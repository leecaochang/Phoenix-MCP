"""Joint-decision previews and qualified plans through the existing command executor."""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_routing import RoutingAuthority, routing_language
from .command_execution import _capture_plan, _digest
from .command_operations import numeric_operation
from .const import (
    DECISION_BATCH_EXECUTION_LANGUAGES, DECISION_BATCH_EXECUTION_MODELS,
    DECISION_EXECUTION_CATEGORIES, DECISION_MAX_TARGETS, DECISION_MAX_TARGET_BYTES,
    DECISION_TARGET_INTENTS, DECISION_NUMERIC_CATEGORIES, DECISION_PERCENTAGE_PREVIEW_MAX_TARGETS, MAX_SEARCH_QUERY_LEN,
)
from .decision_provider import DecisionError, DecisionProvider, confident_choice
from .decision_targets import capture_targets, CatalogLimitError, TargetSnapshot
from .device_semantics import device_registry_snapshot
from .helpers import redact_secrets_in_text, redact_inaccessible_entity_ids_in_text


def capture_batch_targets(hass: Any, token: Any, *, numeric_preview: bool = False
                          ) -> tuple[dict[str, dict], dict[str, TargetSnapshot]]:
    """Union complete scoped catalogs; keep eligibility per operation and all competitors."""
    devices = device_registry_snapshot(hass)
    categories = DECISION_EXECUTION_CATEGORIES | DECISION_NUMERIC_CATEGORIES if numeric_preview else DECISION_EXECUTION_CATEGORIES
    max_targets = DECISION_PERCENTAGE_PREVIEW_MAX_TARGETS if numeric_preview else DECISION_MAX_TARGETS
    snapshots = {category: capture_targets(hass, token, category, devices=devices, max_targets=max_targets,
                                          compact=numeric_preview)
                 for category in sorted(categories)}
    entities: dict[str, dict] = {}
    for category, snapshot in snapshots.items():
        for descriptor in snapshot.targets.values():
            item = entities.setdefault(descriptor["entity_id"], {**descriptor, "eligible_categories": []})
            item["eligible_categories"].append(category)
    if len(entities) > max_targets:
        raise CatalogLimitError("combined", "target_count", len(entities), max_targets)
    targets = {f"t{index}": entities[entity_id] for index, entity_id in enumerate(sorted(entities))}
    size = len(json.dumps(targets, ensure_ascii=False).encode())
    if size > DECISION_MAX_TARGET_BYTES:
        raise CatalogLimitError("combined", "metadata_bytes", size, DECISION_MAX_TARGET_BYTES)
    return targets, snapshots


def batch_execution_eligible(cfg: Any, language: str) -> bool:
    """Qualify each endpoint, configured model and language independently."""
    return bool(language in DECISION_BATCH_EXECUTION_LANGUAGES
                and (cfg.kind, cfg.base_url, cfg.model) in DECISION_BATCH_EXECUTION_MODELS)


def batch_catalog_digest(snapshot: tuple[dict[str, dict], dict[str, TargetSnapshot]]) -> str:
    """Bind all categories and competitors without retaining their metadata."""
    targets, catalogs = snapshot
    return _digest({"targets": targets, "catalogs": {category: catalog.digest()
                                                    for category, catalog in catalogs.items()}})


async def async_prepare_batch(hass: Any, authority: RoutingAuthority, provider: DecisionProvider,
                              session: Any, sentence: str, language: str, ingress: str) -> tuple[dict | None, dict] | None:
    """Use the caller's slot and deadline; None permits sequential routing before any request."""
    current = authority.current(hass)
    if current is None:
        raise DecisionError("authority_changed")
    if not batch_execution_eligible(provider.cfg, language):
        return None
    if (redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(sentence) or "", current, hass) != sentence
            or (provider.cfg.api_key and provider.cfg.api_key in sentence)):
        raise DecisionError("context_unavailable")
    try:
        snapshot = capture_batch_targets(hass, current)
    except DecisionError as error:
        if str(error) in ("too_many_targets", "target_metadata_too_large"):
            return None
        raise
    targets, catalogs = snapshot
    if not targets:
        raise DecisionError("no_target")
    if provider.cfg.api_key and provider.cfg.api_key in json.dumps(targets, ensure_ascii=False):
        raise DecisionError("context_unavailable")
    answer = await provider.route_and_target(session, sentence, targets,
                                            authority.settings.decision_timeout_ms, language)
    current = authority.current(hass)
    if current is None:
        raise DecisionError("authority_changed")
    try:
        changed = capture_batch_targets(hass, current) != snapshot
    except DecisionError:
        changed = True
    if changed:
        raise DecisionError("targets_changed")
    category, target = answer["route"], answer["target"]
    models = DECISION_BATCH_EXECUTION_MODELS[(provider.cfg.kind, provider.cfg.base_url, provider.cfg.model)]
    if category["model"] not in models or target["model"] != category["model"]:
        raise DecisionError("model_unverified")
    selection: dict = {"reason": "provider_fallback", "decision": None, "executed": False}
    if category["choice"] == "provider":
        return category, selection
    if not confident_choice(category):
        return category, {**selection, "reason": "low_probability"}
    selection["decision"] = target
    if target["choice"] == "none":
        return category, {**selection, "reason": "target_declined"}
    if not confident_choice(target):
        return category, {**selection, "reason": "low_probability"}
    descriptor = targets[target["choice"]]
    if category["choice"] not in descriptor["eligible_categories"]:
        raise DecisionError("category_mismatch")
    proposal = {"intent": DECISION_TARGET_INTENTS[category["choice"]], "entity_ids": [descriptor["entity_id"]]}
    plan = _capture_plan(hass, authority.data, current, proposal, ingress=ingress)
    if plan is None:
        raise DecisionError("targets_changed")
    plan.update(decision_category=category["choice"], decision_catalog=catalogs[category["choice"]].digest(),
                decision_batch={"model": category["model"], "language": language,
                                "catalog": batch_catalog_digest(snapshot)})
    return category, {**selection, "reason": "target_selected", "entity_id": descriptor["entity_id"],
                      "proposal": proposal, "_plan": plan}


async def async_preview_batch(hass: Any, data: Any, token: Any, sentence: str,
                              language: str, local: dict, *, numeric_preview: bool = False) -> dict:
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
            provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter,
                                        metrics=runtime.metrics, metric_scope="preview")
            if numeric_preview and not provider.supports_exact_percentages():
                return result("unsupported_category")
            snapshot = capture_batch_targets(hass, current, numeric_preview=numeric_preview)
            targets, _ = snapshot
            if not targets:
                return result("no_target")
            if cfg.api_key and cfg.api_key in json.dumps(targets, ensure_ascii=False):
                return result("context_unavailable")
            options = {"numeric_preview": True} if numeric_preview else {}
            async with asyncio.timeout(authority.settings.decision_timeout_ms / 1000):
                answer = await provider.route_and_target(async_get_clientsession(hass), sentence, targets,
                                                        authority.settings.decision_timeout_ms, lang, **options)
            current = authority.current(hass)
            if current is None or store.resolve(account, purpose="decision") != cfg:
                return result("authority_changed")
            try:
                if not safe_text(current) or capture_batch_targets(hass, current, numeric_preview=numeric_preview) != snapshot:
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
            proposal: dict[str, Any] = {"intent": DECISION_TARGET_INTENTS[category["choice"]], "entity_ids": [descriptor["entity_id"]]}
            if numeric_preview:
                value = answer["percentage"]
                if not confident_choice(value):
                    return result("low_probability", decision=answer)
                operation = numeric_operation(proposal["intent"])
                if operation is not None:
                    if value["choice"] == "none":
                        return result("percentage_declined", decision=answer)
                    proposal[operation.proposal_field] = int(value["choice"])
                elif value["choice"] != "none":
                    return result("percentage_mismatch", decision=answer)
            return result("target_selected", decision=answer, proposal=proposal,
                          local_agreement=proposal == local.get("proposal") if local.get("proposal") else None)
        except CatalogLimitError as error:
            return result(str(error), catalog_limit=error.details)
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
