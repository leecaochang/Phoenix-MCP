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
    DECISION_ENTITY_VALUE_QUESTIONS, DECISION_BATCH_EXECUTION_LANGUAGES, DECISION_BATCH_EXECUTION_MODELS,
    DECISION_EXECUTION_CATEGORIES, DECISION_MAX_TARGETS, DECISION_MAX_TARGET_BYTES,
    DECISION_BATCH_MAX_TARGET_BYTES,
    DECISION_PERCENTAGE_EXECUTION_MODELS, DECISION_TEMPORAL_CATEGORIES,
    COMMAND_ROUTING_VALUE_INTENTS, DECISION_TARGET_INTENTS, DECISION_NUMERIC_CATEGORIES, DECISION_BATCH_MAX_TARGETS, MAX_SEARCH_QUERY_LEN,
)
from .decision_provider import DecisionError, DecisionValidationError, DecisionProvider, confident_choice
from .decision_targets import catalog_state, capture_targets, CatalogLimitError, TargetSnapshot
from .device_semantics import device_registry_snapshot
from .helpers import redact_secrets_in_text, redact_inaccessible_entity_ids_in_text
from .numeric_evidence import read_explicit_percentage, compare_percentage, has_percentage_unit, read_explicit_scalar, explicit_unit_matches


def capture_batch_targets(hass: Any, token: Any, *, numeric_preview: bool = False, extended: bool = False, temporal: bool = False
                          ) -> tuple[dict[str, dict], dict[str, TargetSnapshot]]:
    """Union complete scoped catalogs; keep eligibility per operation and all competitors."""
    devices = device_registry_snapshot(hass)
    categories = DECISION_EXECUTION_CATEGORIES | DECISION_NUMERIC_CATEGORIES if numeric_preview else DECISION_EXECUTION_CATEGORIES
    if temporal:
        categories = DECISION_EXECUTION_CATEGORIES | DECISION_TEMPORAL_CATEGORIES.keys()
    extended = (numeric_preview and not temporal) or extended
    max_targets = DECISION_BATCH_MAX_TARGETS if extended else DECISION_MAX_TARGETS
    snapshots = {category: capture_targets(hass, token, category, devices=devices, max_targets=max_targets,
                                          compact=extended)
                 for category in sorted(categories)}
    if extended and not temporal:
        from .command_summary import summary_catalog
        try:
            snapshots['state_summary'] = summary_catalog(hass, token, devices=devices)
        except CatalogLimitError:
            # Disable this complete query catalog rather than truncating it or other routes.
            pass
    entities: dict[str, dict] = {}
    for category, snapshot in snapshots.items():
        if category == 'state_summary':
            continue
        for descriptor in snapshot.targets.values():
            identity = {key: value for key, value in descriptor.items() if key not in ("numeric", "value_kind", "entity_command")}
            item = entities.setdefault(descriptor["entity_id"], {**identity, "eligible_categories": []})
            item["eligible_categories"].append(category)
            if "entity_command" in descriptor:
                item.setdefault("entity_operations", {})[category] = descriptor["entity_command"]
            if "value_kind" in descriptor:
                item.setdefault("temporal_operations", {})[category] = descriptor["value_kind"]
            if "numeric" in descriptor:
                item.setdefault("numeric_operations", {})[category] = descriptor["numeric"]
    if len(entities) > max_targets:
        raise CatalogLimitError("combined", "target_count", len(entities), max_targets)
    targets = {f"t{index}": entities[entity_id] for index, entity_id in enumerate(sorted(entities))}
    size = len(json.dumps(catalog_state(targets) if extended else targets, ensure_ascii=False).encode())
    max_bytes = DECISION_BATCH_MAX_TARGET_BYTES if extended else DECISION_MAX_TARGET_BYTES
    if size > max_bytes:
        raise CatalogLimitError("combined", "metadata_bytes", size, max_bytes)
    # Collection scopes have their own complete 16 KiB/254-choice budget.
    # Sharing the entity catalog budget silently disables summaries in larger homes.
    return targets, snapshots


def batch_execution_eligible(cfg: Any, language: str) -> bool:
    """Qualify each endpoint, configured model and language independently."""
    return bool(language in DECISION_BATCH_EXECUTION_LANGUAGES
                and (cfg.kind, cfg.base_url, cfg.model) in DECISION_BATCH_EXECUTION_MODELS)


def percentage_execution_eligible(cfg: Any, language: str) -> bool:
    """Keep numeric qualification independent from future categorical model additions."""
    return bool(batch_execution_eligible(cfg, language)
                and (cfg.kind, cfg.base_url, cfg.model) in DECISION_PERCENTAGE_EXECUTION_MODELS)


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
    from .temporal_values import async_read_temporal_value
    source = await async_read_temporal_value(hass, sentence, language)
    from .agentcli import loaded_decision_account
    current = authority.current(hass)
    account = authority.settings.decision_provider_id
    if current is None or not account or loaded_decision_account(hass, account) != provider.cfg:
        raise DecisionError("authority_changed")
    if redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(sentence) or "", current, hass) != sentence:
        raise DecisionError("context_unavailable")
    if source is not None:
        from .decision_temporal import async_prepare_temporal
        return await async_prepare_temporal(hass, authority, provider, session, sentence, language, ingress, source)
    numeric = False
    scalar = False
    source_value = None
    from .entity_commands import literal_text
    if percentage_execution_eligible(provider.cfg, language) and literal_text(sentence) is None:
        source_value = await hass.async_add_executor_job(read_explicit_percentage, sentence, language)
        if source_value is None:
            source_value = await hass.async_add_executor_job(read_explicit_scalar, sentence, language)
            scalar = source_value is not None
        numeric = source_value is not None
        current = authority.current(hass)
        account = authority.settings.decision_provider_id
        if (current is None or not account or loaded_decision_account(hass, account) != provider.cfg):
            raise DecisionError("authority_changed")
        if redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(sentence) or "", current, hass) != sentence:
            raise DecisionError("context_unavailable")
        if not numeric and has_percentage_unit(sentence):
            return None, {"reason": "numeric_unverified", "decision": None, "executed": False}
    try:
        snapshot = capture_batch_targets(hass, current, numeric_preview=numeric, extended=True)
    except DecisionError as error:
        if str(error) in ("too_many_targets", "target_metadata_too_large"):
            return None
        raise
    targets, catalogs = snapshot
    summaries = catalogs.get('state_summary')
    if not targets and not (summaries and summaries.targets):
        raise DecisionError("no_target")
    if provider.cfg.api_key and provider.cfg.api_key in json.dumps({"targets": targets, "summaries": summaries.targets if summaries else {}}, ensure_ascii=False):
        raise DecisionError("context_unavailable")
    options: dict[str, Any] = {"numeric_preview": True} if numeric else {}
    if summaries and summaries.targets:
        options['summary_scopes'] = summaries.targets
    if scalar:
        options["scalar_value"] = source_value
    answer = await provider.route_and_target(session, sentence, targets,
                                            authority.settings.decision_timeout_ms, language, **options)
    current = authority.current(hass)
    if current is None:
        raise DecisionError("authority_changed")
    try:
        changed = capture_batch_targets(hass, current, numeric_preview=numeric, extended=True) != snapshot
    except DecisionError:
        changed = True
    if changed:
        raise DecisionError("targets_changed")
    category, target = answer["route"], answer["target"]
    qualified = DECISION_PERCENTAGE_EXECUTION_MODELS if numeric else DECISION_BATCH_EXECUTION_MODELS
    models = qualified[(provider.cfg.kind, provider.cfg.base_url, provider.cfg.model)]
    if category["model"] not in models or target["model"] != category["model"]:
        raise DecisionError("model_unverified")
    selection: dict = {"reason": "provider_fallback", "decision": None, "executed": False}
    if category["choice"] == "provider":
        return category, selection
    if not confident_choice(category):
        return category, {**selection, "reason": "low_probability"}
    selection["decision"] = target
    is_summary = category['choice'] == 'state_summary'
    if is_summary:
        if target['choice'] != 'none' or not confident_choice(target):
            return category, {**selection, 'reason': 'category_mismatch'}
        if summaries is None:
            raise DecisionError('category_mismatch')
        target = answer['summary_scope']
    elif answer.get('summary_scope', {}).get('choice', 'none') != 'none':
        return category, {**selection, 'reason': 'category_mismatch'}
    if target["choice"] == "none":
        return category, {**selection, "reason": "target_declined"}
    if not confident_choice(target):
        return category, {**selection, "reason": "low_probability"}
    descriptor = summaries.targets[target['choice']] if is_summary and summaries is not None else targets[target['choice']]
    if not is_summary and category["choice"] not in descriptor["eligible_categories"]:
        raise DecisionError("category_mismatch")
    proposal: dict[str, Any] = {"intent": DECISION_TARGET_INTENTS[category["choice"]], "entity_ids": [descriptor["entity_id"]] if not is_summary else []}
    selected_numeric = category["choice"] in DECISION_NUMERIC_CATEGORIES
    if numeric and selected_numeric:
        operation = numeric_operation(proposal["intent"])
        percentage = answer["percentage"]
        if (operation is None or percentage["model"] != category["model"]
                or not confident_choice(percentage) or percentage["choice"] != ("value" if scalar else str(source_value))):
            return category, {**selection, "reason": "numeric_unverified"}
        unit = descriptor.get("numeric_operations", {}).get(category["choice"], {}).get("unit")
        if category["choice"] in ("light_brightness", "media_volume", "cover_position"):
            unit = "%"
        if (not scalar and unit != "%") or not explicit_unit_matches(sentence, unit):
            return category, {**selection, "reason": "numeric_unverified"}
        proposal[operation.proposal_field] = source_value
    if numeric and not selected_numeric and (answer["percentage"]["choice"] != "none"
            or answer["percentage"]["model"] != category["model"] or not confident_choice(answer["percentage"])):
        return category, {**selection, "reason": "numeric_unverified"}
    if category['choice'] == 'state_summary':
        from .command_summary import summary_proposal
        choice = answer.get('summary', {})
        if not confident_choice(choice) or choice.get('model') != category['model']:
            return category, {**selection, 'reason': 'low_probability'}
        candidate = summary_proposal(hass, current, descriptor, choice['choice'])
        if candidate is None:
            return category, {**selection, 'reason': 'category_mismatch'}
        proposal = candidate
    elif answer.get('summary', {}).get('choice', 'none') != 'none':
        return category, {**selection, 'reason': 'category_mismatch'}
    typed = proposal["intent"] in COMMAND_ROUTING_VALUE_INTENTS
    if typed:
        from .entity_commands import chosen_entity_value
        value = chosen_entity_value(answer, sentence, targets, descriptor, category["choice"])
        if value is None:
            return category, {**selection, "reason": "numeric_unverified"}
        proposal["value"] = value
    elif any(answer.get(question, {}).get("choice", "none") != "none" for question in DECISION_ENTITY_VALUE_QUESTIONS):
        return category, {**selection, "reason": "category_mismatch"}
    plan = _capture_plan(hass, authority.data, current, proposal, ingress=ingress)
    if plan is None:
        raise DecisionError("targets_changed")
    plan.update(decision_category=category["choice"], decision_catalog=catalogs[category["choice"]].digest(),
                decision_batch={"model": category["model"], "language": language,
                                "catalog": batch_catalog_digest(snapshot)})
    if is_summary:
        plan['decision_batch']['proposal'] = _digest(proposal)
    if typed:
        plan["decision_batch"].update(value=proposal["value"], proposal=_digest(proposal))
    if numeric and selected_numeric:
        plan["decision_batch"].update(percentage=source_value, proposal=_digest(proposal))
    elif numeric:
        plan["decision_batch"]["catalog_numeric"] = True
    return category, {**selection, "reason": "target_selected", **({"entity_id": descriptor["entity_id"]} if not is_summary else {}),
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
        deadline = asyncio.get_running_loop().time() + authority.settings.decision_timeout_ms / 1000
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
            from .temporal_values import async_read_temporal_value
            async with asyncio.timeout_at(deadline):
                temporal = await async_read_temporal_value(hass, sentence, lang)
            current = authority.current(hass)
            if current is None or store.resolve(account, purpose="decision") != cfg:
                return result("authority_changed")
            if not safe_text(current):
                return result("context_unavailable")
            timezone = hass.config.time_zone
            if numeric_preview and temporal is None and not provider.supports_exact_percentages():
                return result("unsupported_category")
            extended = batch_execution_eligible(cfg, lang)
            snapshot = capture_batch_targets(hass, current, numeric_preview=numeric_preview, extended=extended, temporal=temporal is not None)
            targets, catalogs = snapshot
            summaries = catalogs.get('state_summary')
            if not targets and not (summaries and summaries.targets):
                return result("no_target")
            if cfg.api_key and cfg.api_key in json.dumps({"targets": targets, "summaries": summaries.targets if summaries else {}}, ensure_ascii=False):
                return result("context_unavailable")
            options: dict[str, Any] = {"numeric_preview": True} if numeric_preview else {}
            if summaries and summaries.targets:
                options['summary_scopes'] = summaries.targets
            source_value = None
            scalar = False
            async with asyncio.timeout_at(deadline):
                if temporal is not None:
                    answer = await provider.route_temporal(async_get_clientsession(hass), sentence, lang, targets,
                                                           temporal, timezone, authority.settings.decision_timeout_ms)
                elif numeric_preview:
                    source_value = await hass.async_add_executor_job(read_explicit_percentage, sentence, lang)
                    if source_value is None:
                        source_value = await hass.async_add_executor_job(read_explicit_scalar, sentence, lang)
                        scalar = source_value is not None
                        if scalar:
                            options["scalar_value"] = source_value
                if temporal is None:
                    answer = await provider.route_and_target(async_get_clientsession(hass), sentence, targets,
                                                            authority.settings.decision_timeout_ms, lang, **options)
            current = authority.current(hass)
            if current is None or store.resolve(account, purpose="decision") != cfg:
                return result("authority_changed")
            try:
                if not safe_text(current) or capture_batch_targets(hass, current, numeric_preview=numeric_preview, extended=extended, temporal=temporal is not None) != snapshot or hass.config.time_zone != timezone:
                    return result("targets_changed")
            except DecisionError:
                return result("targets_changed")
            if temporal is not None:
                from .decision_temporal import temporal_proposal
                reason, temporal_candidate = temporal_proposal(answer, targets, temporal, timezone, sentence)
                return result(reason, decision=answer, proposal=temporal_candidate)
            category, target = answer["route"], answer["target"]
            is_summary = category['choice'] == 'state_summary'
            if is_summary:
                if target['choice'] != 'none' or not confident_choice(target):
                    return result('category_mismatch', decision=answer)
                target = answer['summary_scope']
            elif answer.get('summary_scope', {}).get('choice', 'none') != 'none':
                return result('category_mismatch', decision=answer)
            if category["choice"] == "provider" or target["choice"] == "none":
                return result("provider_fallback", decision=answer)
            if not confident_choice(category) or not confident_choice(target):
                return result("low_probability", decision=answer)
            descriptor = summaries.targets[target['choice']] if is_summary and summaries is not None else targets[target['choice']]
            if not is_summary and category["choice"] not in descriptor["eligible_categories"]:
                return result("category_mismatch", decision=answer)
            proposal: dict[str, Any] = {"intent": DECISION_TARGET_INTENTS[category["choice"]], "entity_ids": [descriptor["entity_id"]] if not is_summary else []}
            if category['choice'] == 'state_summary':
                from .command_summary import summary_proposal
                choice = answer.get('summary', {})
                if not confident_choice(choice):
                    return result('low_probability', decision=answer)
                candidate = summary_proposal(hass, current, descriptor, choice['choice'])
                if candidate is None:
                    return result('category_mismatch', decision=answer)
                proposal = candidate
            elif answer.get('summary', {}).get('choice', 'none') != 'none':
                return result('category_mismatch', decision=answer)
            if proposal["intent"] in COMMAND_ROUTING_VALUE_INTENTS:
                from .entity_commands import chosen_entity_value
                chosen_value = chosen_entity_value(answer, sentence, targets, descriptor, category["choice"])
                if chosen_value is None:
                    return result("numeric_unverified", decision=answer)
                proposal["value"] = chosen_value
            elif any(answer.get(question, {}).get("choice", "none") != "none" for question in DECISION_ENTITY_VALUE_QUESTIONS):
                return result("category_mismatch", decision=answer)
            evidence = {}
            if numeric_preview:
                value = answer["percentage"]
                if not confident_choice(value):
                    return result("low_probability", decision=answer)
                operation = numeric_operation(proposal["intent"])
                if operation is not None:
                    if value["choice"] == "none":
                        return result("percentage_declined", decision=answer)
                    proposal[operation.proposal_field] = source_value if scalar else int(value["choice"])
                    evidence["value_check"] = compare_percentage(source_value, proposal[operation.proposal_field])
                elif value["choice"] != "none":
                    return result("percentage_mismatch", decision=answer)
            return result("target_selected", decision=answer, proposal=proposal,
                          **evidence,
                          local_agreement=proposal == local.get("proposal") if local.get("proposal") else None)
        except CatalogLimitError as error:
            return result(str(error), catalog_limit=error.details)
        except DecisionValidationError as error:
            return result("malformed", validation_check=error.check,
                          **({"validation_details": error.diagnostics} if error.diagnostics else {}))
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
