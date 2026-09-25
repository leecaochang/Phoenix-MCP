"""Bounded context reviews; only the internal Chat path can prepare execution plans."""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_execution import _target_fingerprint, plan_is_current, _digest
from .command_operations import numeric_operation, service_arguments
from .command_routing import RoutingAuthority, effective_routing_mode, routing_language
from .const import (
    COMMAND_ROUTING_READ_INTENTS, COMMAND_ROUTING_INTENTS, COMMAND_ROUTING_VALUE_INTENTS, COMMAND_ROUTING_TEMPORAL_OPERATIONS, COMMAND_ROUTING_LANGUAGES, DECISION_CONTEXT_MAX_BYTES, DECISION_CONTEXT_MAX_MESSAGES,
    DECISION_CONTEXT_REQUIRED_CHOICES, DECISION_CONTEXT_EXECUTION_MODELS,
    DECISION_CONTEXT_EXECUTION_LANGUAGES, DECISION_FOLLOWUP_EXECUTION_MODELS, MAX_SEARCH_QUERY_LEN,
)
from .decision_history import history_texts, structured_context_history
from .decision_provider import DecisionError, DecisionProvider, confident_choice
from .helpers import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text


def context_history(messages: Any, *, structured: bool = False) -> list[dict] | None:
    """Validate plain pairs or complete provider exchanges without shortening history."""
    if structured:
        return structured_context_history(messages)
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
           history: list[dict], proposal: Any) -> tuple[dict, str] | None:
    """Keep exact context or decline; redaction must not erase a prior restriction."""
    if (not isinstance(proposal, dict) or not isinstance(proposal.get("intent"), str)
            or proposal["intent"] not in COMMAND_ROUTING_INTENTS | COMMAND_ROUTING_TEMPORAL_OPERATIONS.keys()):
        return None
    intent = proposal["intent"]
    operation = numeric_operation(intent)
    value_field = operation.proposal_field if operation else "value" if intent in COMMAND_ROUTING_TEMPORAL_OPERATIONS or intent in COMMAND_ROUTING_VALUE_INTENTS else None
    summary = intent == 'PhoenixStateSummary'
    fields = {"intent", "entity_ids"} | ({'summary_scope', 'summary_mode'} if summary else {value_field} if value_field else set())
    ids = proposal.get("entity_ids")
    if (set(proposal) != fields or not isinstance(ids, list) or not ids or (not summary and len(ids) != 1)
            or any(not isinstance(eid, str) for eid in ids)):
        return None
    service = None if intent in COMMAND_ROUTING_READ_INTENTS else service_arguments(proposal)
    if intent not in COMMAND_ROUTING_READ_INTENTS and service is None:
        return None
    fingerprint: str | None
    if summary:
        from .command_summary import summary_current
        if not summary_current(hass, token, proposal):
            return None
        fingerprint = _digest({eid: _target_fingerprint(hass, token, eid, intent) for eid in ids})
    else:
        fingerprint = _target_fingerprint(hass, token, ids[0], intent)
    if fingerprint is None:
        return None
    try:
        texts = history_texts([sentence, history], check_sensitive=True)
    except (ValueError, RecursionError):
        return None
    for text in texts:
        if redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(text) or "", token, hass) != text:
            return None
    candidate = {"intent": intent, "entity_ids": list(ids)}
    if summary:
        candidate.update(summary_scope=proposal['summary_scope'], summary_mode=proposal['summary_mode'])
    if value_field:
        candidate[value_field] = proposal[value_field]
    state = {"utterance": sentence, "language": language, "history": history, "candidate": candidate}
    if context_history(history) is None:
        state["history_format"] = "provider_messages"
    if service is not None:
        state["service"] = service
    if len(json.dumps(state, ensure_ascii=False).encode()) > DECISION_CONTEXT_MAX_BYTES:
        return None
    return state, fingerprint


def context_execution_eligible(hass: Any, data: Any, language: object) -> bool:
    """Skip history sharing for providers or languages outside the evaluated rollout."""
    from .agentcli import loaded_decision_account
    settings = data.store.get_settings()
    account = settings.decision_provider_id
    cfg = loaded_decision_account(hass, account) if account else None
    return bool(effective_routing_mode(settings, "chat") == "hybrid"
                and routing_language(language, enabled=tuple(COMMAND_ROUTING_LANGUAGES)) in DECISION_CONTEXT_EXECUTION_LANGUAGES
                and cfg and (cfg.kind, cfg.base_url, cfg.model) in DECISION_CONTEXT_EXECUTION_MODELS)


async def async_preview_context(hass: Any, data: Any, token: Any, sentence: str,
                                language: str, messages: Any, local: dict) -> dict:
    """Administrator previews cannot request execution plans."""
    if not local.get("proposal"):
        return await _async_review_decision_context(hass, data, token, sentence, language, messages, local)
    return await _async_review_context(hass, data, token, sentence, language, messages, local)


async def async_prepare_chat_context(hass: Any, data: Any, token: Any, sentence: str,
                                     language: str, messages: Any, local: dict,
                                     cancel: asyncio.Event) -> dict:
    """Internal Chat admission preserves the shared execution and approval boundaries."""
    if not local.get("proposal"):
        return await _async_review_decision_context(hass, data, token, sentence, language, messages, local,
                                                    prepare=True, cancel=cancel)
    return await _async_review_context(hass, data, token, sentence, language, messages, local,
                                       prepare=True, cancel=cancel)


async def _async_review_context(hass: Any, data: Any, token: Any, sentence: str,
                                language: str, messages: Any, local: dict, *, prepare: bool = False,
                                cancel: asyncio.Event | None = None, source_plan: dict | None = None,
                                decision_candidate: bool = False) -> dict:
    """Own one cancellable request under the shared decision slot and time budget."""
    started = monotonic()
    def result(reason: str, decision: dict | None = None) -> dict:
        return {"reason": reason, "decision": decision, "executed": False,
                "elapsed_ms": round((monotonic() - started) * 1000, 2)}

    history = context_history(messages, structured=True)
    lang = routing_language(language, enabled=tuple(COMMAND_ROUTING_LANGUAGES))
    if (history is None or lang is None or not isinstance(sentence, str)
            or not sentence.strip() or len(sentence) > MAX_SEARCH_QUERY_LEN):
        return result("invalid_context")
    if local.get("reason") != "supported" or not local.get("proposal"):
        return result("local_unavailable")
    if cancel is not None and cancel.is_set():
        return result("cancelled")
    if prepare and not context_execution_eligible(hass, data, lang):
        return result("context_unverified")
    authority = RoutingAuthority.capture(data, token)
    if authority.current(hass) is None:
        return result("authority_changed")
    runtime = data.decision_shadow
    if runtime.task is not None:
        return result("busy")
    # Capture independent primitives before scheduling; caller-owned objects are mutable.
    proposal = json.loads(json.dumps(local["proposal"]))
    source_plan = json.loads(json.dumps(source_plan)) if source_plan is not None else None

    def source_current(current: Any) -> bool:
        return source_plan is None or (
            source_plan.get("proposal") == proposal and "decision_batch" in source_plan
            and "context_review" not in source_plan and source_plan.get("ingress") == "chat"
            and plan_is_current(source_plan, hass, data, current))

    async def evaluate() -> dict:
        from .agentcli import _get_secret_store, PROVIDER_DEFINITIONS
        try:
            current = authority.current(hass)
            if current is None:
                return result("authority_changed")
            if not source_current(current):
                return result("context_changed")
            snapshot = _state(hass, current, sentence, lang, history, proposal)
            if snapshot is None:
                return result("context_unavailable")
            state, fingerprint = snapshot
            if decision_candidate:
                state["candidate_source"] = "decision"
                if len(json.dumps(state, ensure_ascii=False).encode()) > DECISION_CONTEXT_MAX_BYTES:
                    return result("context_unavailable")
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
            latest = _state(hass, current, sentence, lang, history, proposal)
            if decision_candidate and latest is not None:
                latest[0]["candidate_source"] = "decision"
            if latest != snapshot or not source_current(current):
                return result("context_changed")
            # Exact configured credentials are never sent back inside conversation text.
            if cfg.api_key and any(cfg.api_key in text for text in history_texts(state)):
                return result("context_unavailable")
            provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter,
                                        metrics=runtime.metrics, metric_scope="chat" if prepare else "preview")
            async with asyncio.timeout(authority.settings.decision_timeout_ms / 1000):
                answer = await provider.review_context(async_get_clientsession(hass), state,
                                                       authority.settings.decision_timeout_ms)
            current = authority.current(hass)
            if current is None or store.resolve(account, purpose="decision") != cfg:
                return result("authority_changed")
            latest = _state(hass, current, sentence, lang, history, proposal)
            if decision_candidate and latest is not None:
                latest[0]["candidate_source"] = "decision"
            if latest is None or latest[1] != fingerprint or latest != snapshot or not source_current(current):
                return result("context_changed")
            checks = answer["checks"]
            reason = "provider_fallback" if any(
                checks[question]["choice"] != required
                for question, required in DECISION_CONTEXT_REQUIRED_CHOICES.items()
            ) else ("compatible" if all(confident_choice(check) for check in checks.values()) else "low_probability")
            reviewed = result(reason, answer)
            if prepare and reason == "compatible":
                from .agentcli import decision_account_fingerprint
                from .command_execution import _capture_plan
                if (not context_execution_eligible(hass, data, lang)
                        or answer["model"] not in DECISION_CONTEXT_EXECUTION_MODELS.get((cfg.kind, cfg.base_url, cfg.model), ())):
                    return result("context_unverified", answer)
                if decision_candidate and (source_plan is None or source_plan["decision_batch"]["model"] != answer["model"] or answer["model"] not in
                        DECISION_FOLLOWUP_EXECUTION_MODELS.get((cfg.kind, cfg.base_url, cfg.model), ())):
                    return result("context_unverified", answer)
                plan = source_plan if source_plan is not None else _capture_plan(hass, data, current, proposal, ingress="chat")
                account_digest = decision_account_fingerprint(hass, account)
                if plan is None or account_digest is None:
                    return result("context_changed")
                plan["context_review"] = {"account": account_digest, "kind": cfg.kind,
                                          "model": answer["model"], "language": lang}
                if decision_candidate:
                    plan["context_review"]["proposal"] = _digest(proposal)
                    plan["decision_batch"]["context"] = _digest(plan["context_review"])
                reviewed.update(reason="plan_ready", _plan=plan)
            return reviewed
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
    waiter = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if waiter is not None:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if cancel is not None and cancel.is_set():
                return result("cancelled")
        return await task
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise
        return result("cancelled")
    finally:
        if not task.done():
            task.cancel()
        if waiter is not None:
            waiter.cancel()
        await asyncio.gather(task, *([waiter] if waiter is not None else []), return_exceptions=True)
        finished(task)


async def _async_review_decision_context(hass: Any, data: Any, token: Any, sentence: str,
                                         language: str, messages: Any, local: dict, *, prepare: bool = False,
                                         cancel: asyncio.Event | None = None) -> dict:
    """Resolve an explicit latest request, then review it against intact history before effects."""
    from .agentcli import _get_secret_store
    from .decision_batch import async_preview_batch
    from .decision_shadow import async_preview_decision
    from .numeric_evidence import has_percentage_unit

    started = monotonic()
    def result(reason: str) -> dict:
        return {"reason": reason, "decision": None, "executed": False,
                "elapsed_ms": round((monotonic() - started) * 1000, 2)}

    history = context_history(messages, structured=True)
    lang = routing_language(language, enabled=tuple(COMMAND_ROUTING_LANGUAGES))
    if (history is None or lang is None or not isinstance(sentence, str) or not sentence.strip()
            or len(sentence) > MAX_SEARCH_QUERY_LEN):
        return result("invalid_context")
    if local.get("proposal") or local.get("reason") not in ("no_match", "no_target", "unsupported"):
        return result("local_unavailable")
    authority = RoutingAuthority.capture(data, token)
    current = authority.current(hass)
    if current is None:
        return result("authority_changed")
    if cancel is not None and cancel.is_set():
        return result("cancelled")
    if data.decision_shadow.task is not None:
        return result("busy")
    if prepare and not context_execution_eligible(hass, data, lang):
        return result("context_unverified")
    try:
        async with asyncio.timeout(authority.settings.decision_timeout_ms / 1000):
            store = await _get_secret_store(hass)
            account = authority.settings.decision_provider_id
            cfg = store.resolve(account, purpose="decision") if account else None
            if cfg is None or account is None:
                return result("not_configured")
            if prepare and (cfg.kind, cfg.base_url, cfg.model) not in DECISION_FOLLOWUP_EXECUTION_MODELS:
                return result("context_unverified")
            current = authority.current(hass)
            if current is None:
                return result("authority_changed")
            # Check the complete transcript before sending even a history-free candidate request.
            texts = history_texts([sentence, history], check_sensitive=True)
            if any(redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(text) or "", current, hass) != text
                   or (cfg.api_key and cfg.api_key in text) for text in texts):
                return result("context_unavailable")
            if prepare:
                candidate = await async_preview_decision(hass, data, current, sentence, lang, local,
                                                         targets=True, prepare=True, ingress="chat", cancel=cancel, joint_only=True)
                source_plan = candidate.get("_plan")
                proposal = source_plan.get("proposal") if source_plan else None
                if source_plan and "decision_batch" not in source_plan:
                    return result("context_unverified")
            else:
                candidate = await async_preview_batch(hass, data, current, sentence,
                                                      "zh-Hans" if lang == "zh-CN" else lang, local,
                                                      numeric_preview=has_percentage_unit(sentence))
                source_plan = None
                proposal = candidate.get("proposal")
                if proposal and numeric_operation(proposal["intent"]) and candidate.get("value_check", {}).get("status") != "match":
                    return result("numeric_unverified")
            current = authority.current(hass)
            if current is None or store.resolve(account, purpose="decision") != cfg:
                return result("authority_changed")
            if cancel is not None and cancel.is_set():
                return result("cancelled")
            if not proposal:
                reason = candidate.get("target_selection", {}).get("reason", candidate["reason"])
                if prepare and reason in ("provider_fallback", "target_declined"):
                    from .decision_reference import async_prepare_chat_reference, reference_execution_eligible
                    if reference_execution_eligible(hass, data, lang):
                        assert cancel is not None
                        # A separate history-based reference check, never a retry after an error or low probability.
                        review = await async_prepare_chat_reference(hass, data, current, sentence, lang, history, local, cancel)
                        if review["reason"] in ("local_unavailable", "invalid_context", "unsupported_language"):
                            return result(reason)
                        review["elapsed_ms"] = round((monotonic() - started) * 1000, 2)
                        return review
                return result(reason)
            review = await _async_review_context(hass, data, current, sentence, lang, history,
                {"reason": "supported", "proposal": proposal}, prepare=prepare, cancel=cancel,
                source_plan=source_plan, decision_candidate=True)
            if not prepare:
                review["candidate_review"] = candidate
            review["elapsed_ms"] = round((monotonic() - started) * 1000, 2)
            return review
    except TimeoutError:
        return result("timeout")
    except (ValueError, RecursionError):
        return result("context_unavailable")
    except Exception:
        return result("unavailable")
