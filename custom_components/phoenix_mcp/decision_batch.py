"""Inert joint-decision comparisons, including locally recognized standalone commands."""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_routing import RoutingAuthority, routing_language
from .const import DECISION_EXECUTION_CATEGORIES, DECISION_MAX_TARGETS, DECISION_MAX_TARGET_BYTES, DECISION_TARGET_INTENTS, MAX_SEARCH_QUERY_LEN
from .decision_provider import DecisionError, DecisionProvider, confident_choice
from .decision_targets import capture_targets, TargetSnapshot
from .helpers import redact_secrets_in_text, redact_inaccessible_entity_ids_in_text


def capture_batch_targets(hass: Any, token: Any) -> tuple[dict[str, dict], dict[str, TargetSnapshot]]:
    """Union complete scoped catalogs; keep eligibility per operation and all competitors."""
    snapshots = {category: capture_targets(hass, token, category) for category in sorted(DECISION_EXECUTION_CATEGORIES)}
    entities: dict[str, dict] = {}
    for category, snapshot in snapshots.items():
        for descriptor in snapshot.targets.values():
            item = entities.setdefault(descriptor["entity_id"], {**descriptor, "eligible_categories": []})
            item["eligible_categories"].append(category)
    if len(entities) > DECISION_MAX_TARGETS:
        raise DecisionError("too_many_targets")
    targets = {f"t{index}": entities[entity_id] for index, entity_id in enumerate(sorted(entities))}
    if len(json.dumps(targets, ensure_ascii=False).encode()) > DECISION_MAX_TARGET_BYTES:
        raise DecisionError("target_metadata_too_large")
    return targets, snapshots


async def async_preview_batch(hass: Any, data: Any, token: Any, sentence: str,
                              language: str, local: dict) -> dict:
    """Use the shared slot without preparing plans, dispatching or changing local results."""
    started = monotonic()
    def result(reason: str, **extra: Any) -> dict:
        return {"reason": reason, "executed": False, "elapsed_ms": round((monotonic() - started) * 1000, 2), **extra}

    lang = routing_language(language, preview=True)
    if lang is None or not isinstance(sentence, str) or not sentence.strip() or len(sentence) > MAX_SEARCH_QUERY_LEN:
        return result("invalid_input")
    if local.get("reason") not in ("supported", "no_match", "unsupported", "no_target"):
        return result("local_unavailable")
    authority = RoutingAuthority.capture(data, token)
    if authority.current(hass) is None:
        return result("authority_changed")
    runtime = data.decision_shadow
    if runtime.task is not None:
        return result("busy")

    def safe_text(current: Any) -> bool:
        return redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(sentence) or "", current, hass) == sentence

    async def evaluate() -> dict:
        from .agentcli import _get_secret_store, PROVIDER_DEFINITIONS
        try:
            store = await _get_secret_store(hass)
            account = authority.settings.decision_provider_id
            cfg = store.resolve(account, purpose="decision") if account else None
            current = authority.current(hass)
            if current is None:
                return result("authority_changed")
            if not account or cfg is None or not cfg.model:
                return result("not_configured")
            if not safe_text(current) or (cfg.api_key and cfg.api_key in sentence):
                return result("context_unavailable")
            snapshot = capture_batch_targets(hass, current)
            targets, _ = snapshot
            if not targets:
                return result("no_target")
            if cfg.api_key and cfg.api_key in json.dumps(targets, ensure_ascii=False):
                return result("context_unavailable")
            provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter,
                                        metrics=runtime.metrics, metric_scope="preview")
            async with asyncio.timeout(authority.settings.decision_timeout_ms / 1000):
                answer = await provider.route_and_target(async_get_clientsession(hass), sentence, targets,
                                                        authority.settings.decision_timeout_ms, lang)
            current = authority.current(hass)
            if current is None or store.resolve(account, purpose="decision") != cfg:
                return result("authority_changed")
            try:
                if not safe_text(current) or capture_batch_targets(hass, current) != snapshot:
                    return result("targets_changed")
            except DecisionError:
                return result("targets_changed")
            category, target = answer["route"], answer["target"]
            if category["choice"] == "provider" or target["choice"] == "none":
                return result("provider_fallback", decision=answer)
            if not confident_choice(category) or not confident_choice(target):
                return result("low_probability", decision=answer)
            descriptor = targets[target["choice"]]
            if category["choice"] not in descriptor["eligible_categories"]:
                return result("category_mismatch", decision=answer)
            proposal = {"intent": DECISION_TARGET_INTENTS[category["choice"]], "entity_ids": [descriptor["entity_id"]]}
            return result("target_selected", decision=answer, proposal=proposal,
                          local_agreement=proposal == local.get("proposal") if local.get("proposal") else None)
        except DecisionError as error:
            return result(str(error))
        except TimeoutError:
            return result("timeout")
        except Exception:
            return result("unavailable")

    task = hass.async_create_background_task(evaluate(), "phoenix_batch_preview")
    runtime.task = task
    try:
        return await task
    except asyncio.CancelledError:
        caller = asyncio.current_task()
        if caller is not None and caller.cancelling():
            raise
        return result("cancelled")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if runtime.task is task:
            runtime.task = None
