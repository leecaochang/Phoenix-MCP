"""Explicit administrator previews of conversation context, without execution plans."""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_execution import _target_fingerprint
from .command_operations import numeric_operation, service_arguments
from .command_routing import RoutingAuthority, routing_language
from .const import (
    COMMAND_ROUTING_INTENTS, DECISION_CONTEXT_MAX_BYTES, DECISION_CONTEXT_MAX_MESSAGES,
    DECISION_CONTEXT_REQUIRED_CHOICES, MAX_SEARCH_QUERY_LEN,
)
from .decision_provider import DecisionError, DecisionProvider, confident_choice
from .helpers import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text


def context_history(messages: Any) -> list[dict[str, str]] | None:
    """Accept complete plain-text turn pairs; never truncate or summarize a history."""
    if (not isinstance(messages, list) or not messages or len(messages) % 2
            or len(messages) > DECISION_CONTEXT_MAX_MESSAGES):
        return None
    copied = []
    size = 0
    for index, message in enumerate(messages):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message.get("role") != ("assistant" if index % 2 else "user")
                or not isinstance(message.get("content"), str) or not message["content"].strip()):
            return None
        size += len(message["content"])
        if size > DECISION_CONTEXT_MAX_BYTES:
            return None
        copied.append(dict(message))
    try:
        if len(json.dumps(copied, ensure_ascii=False).encode()) > DECISION_CONTEXT_MAX_BYTES:
            return None
    except UnicodeError:
        return None
    return copied


def _state(hass: Any, token: Any, sentence: str, language: str,
           history: list[dict[str, str]], proposal: Any) -> tuple[dict, str] | None:
    """Keep exact context or decline; redaction must not erase a prior restriction."""
    if (not isinstance(proposal, dict) or not isinstance(proposal.get("intent"), str)
            or proposal["intent"] not in COMMAND_ROUTING_INTENTS):
        return None
    intent = proposal["intent"]
    operation = numeric_operation(intent)
    fields = {"intent", "entity_ids"} | ({operation.proposal_field} if operation else set())
    ids = proposal.get("entity_ids")
    if (set(proposal) != fields or not isinstance(ids, list) or len(ids) != 1
            or not isinstance(ids[0], str)):
        return None
    service = None if intent == "HassGetState" else service_arguments(proposal)
    if intent != "HassGetState" and service is None:
        return None
    fingerprint = _target_fingerprint(hass, token, ids[0], intent)
    if fingerprint is None:
        return None
    for text in [sentence, *[message["content"] for message in history]]:
        if redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(text) or "", token, hass) != text:
            return None
    candidate = {"intent": intent, "entity_ids": list(ids)}
    if operation:
        candidate[operation.proposal_field] = proposal[operation.proposal_field]
    state = {"utterance": sentence, "language": language, "history": history, "candidate": candidate}
    if service is not None:
        state["service"] = service
    if len(json.dumps(state, ensure_ascii=False).encode()) > DECISION_CONTEXT_MAX_BYTES:
        return None
    return state, fingerprint


async def async_preview_context(hass: Any, data: Any, token: Any, sentence: str,
                                language: str, messages: Any, local: dict) -> dict:
    """Use the shared decision slot/budget, without admitting any automatic route."""
    started = monotonic()
    def result(reason: str, decision: dict | None = None) -> dict:
        return {"reason": reason, "decision": decision, "executed": False,
                "elapsed_ms": round((monotonic() - started) * 1000, 2)}

    history = context_history(messages)
    lang = routing_language(language, preview=True)
    if (history is None or lang is None or not isinstance(sentence, str)
            or not sentence.strip() or len(sentence) > MAX_SEARCH_QUERY_LEN):
        return result("invalid_context")
    if local.get("reason") != "supported" or not local.get("proposal"):
        return result("local_unavailable")
    authority = RoutingAuthority.capture(data, token)
    if authority.current(hass) is None:
        return result("authority_changed")
    runtime = data.decision_shadow
    if runtime.task is not None:
        return result("busy")
    # Capture independent primitives before scheduling; caller-owned objects are mutable.
    proposal = json.loads(json.dumps(local["proposal"]))

    async def evaluate() -> dict:
        from .agentcli import _get_secret_store, PROVIDER_DEFINITIONS
        try:
            current = authority.current(hass)
            if current is None:
                return result("authority_changed")
            snapshot = _state(hass, current, sentence, lang, history, proposal)
            if snapshot is None:
                return result("context_unavailable")
            state, fingerprint = snapshot
            store = await _get_secret_store(hass)
            account = authority.settings.decision_provider_id
            if not account:
                return result("not_configured")
            cfg = store.resolve(account, purpose="decision")
            if cfg is None or not cfg.model:
                return result("not_configured")
            current = authority.current(hass)
            if current is None:
                return result("authority_changed")
            if _state(hass, current, sentence, lang, history, proposal) != snapshot:
                return result("context_changed")
            # Exact configured credentials are never sent back inside conversation text.
            if cfg.api_key and cfg.api_key in json.dumps(state, ensure_ascii=False):
                return result("context_unavailable")
            provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter)
            async with asyncio.timeout(authority.settings.decision_timeout_ms / 1000):
                answer = await provider.review_context(async_get_clientsession(hass), state,
                                                       authority.settings.decision_timeout_ms)
            current = authority.current(hass)
            if current is None or store.resolve(account, purpose="decision") != cfg:
                return result("authority_changed")
            latest = _state(hass, current, sentence, lang, history, proposal)
            if latest is None or latest[1] != fingerprint or latest != snapshot:
                return result("context_changed")
            checks = answer["checks"]
            reason = "provider_fallback" if any(
                checks[question]["choice"] != required
                for question, required in DECISION_CONTEXT_REQUIRED_CHOICES.items()
            ) else ("compatible" if all(confident_choice(check) for check in checks.values()) else "low_probability")
            return result(reason, answer)
        except DecisionError as error:
            return result(str(error))
        except TimeoutError:
            return result("timeout")
        except Exception:
            return result("unavailable")

    task = hass.async_create_background_task(evaluate(), "phoenix_context_preview")
    runtime.task = task
    def finished(completed: asyncio.Task) -> None:
        if runtime.task is completed:
            runtime.task = None
        if not completed.cancelled():
            completed.exception()
    task.add_done_callback(finished)
    try:
        return await task
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise
        return result("cancelled")
    finally:
        if task.done():
            finished(task)
