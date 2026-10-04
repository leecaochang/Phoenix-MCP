"""Execute command proposals through existing dispatch, approval and recovery."""

from __future__ import annotations

import logging
from .helpers import log_internal_failure

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from .agent_recovery import RecoveryRequired, dispatch_agent_tool, recovery_turn
from .audit import generate_request_id
from .command_operations import service_arguments, supports_command, numeric_operation, valid_target_value, finite_number
from .command_routing import RoutingAuthority, effective_routing_mode
from .const import (
    COMMAND_ROUTING_READ_INTENTS, COMMAND_ROUTING_PLAN_KEY, COMMAND_ROUTING_UNKNOWN_ACTION_DOMAINS, COMMAND_ROUTING_ENTITY_INTENTS, COMMAND_ROUTING_VALUE_INTENTS, COMMAND_ROUTING_TEMPORAL_OPERATIONS, DECISION_TEMPORAL_CATEGORIES, VOICE_AGENT_CLIENT_IP, DECISION_TARGET_INTENTS, DECISION_EXECUTION_CATEGORIES,
    DECISION_CONTEXT_EXECUTION_MODELS, DECISION_CONTEXT_EXECUTION_LANGUAGES, DECISION_FOLLOWUP_EXECUTION_MODELS,
    DECISION_BATCH_EXECUTION_MODELS,
    DECISION_NUMERIC_CATEGORIES, DECISION_PERCENTAGE_EXECUTION_MODELS,
    DECISION_REFERENCE_EXECUTION_MODELS, DECISION_REFERENCE_LANGUAGES, DECISION_REFERENCE_INTENTS,
)
from .device_semantics import DeviceRegistrySnapshot, device_registry_snapshot
from .helpers import voice_text
from .policy_engine import Permission, resolve

_LOGGER = logging.getLogger(__name__)

# Only a server-owned command turn can attach a plan to initial service dispatch.
_local_plan: ContextVar[dict | None] = ContextVar("phoenix_local_command_plan", default=None)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _target_fingerprint(hass: Any, token: Any, entity_id: str, intent: str, *,
                        devices: DeviceRegistrySnapshot | None = None) -> str | None:
    """Return a digest of the target's identity and command state, or None if ineligible.

    Device metadata may be shared via `devices` only within one synchronous capture.
    """
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose
    from homeassistant.helpers import entity_registry as er

    state = hass.states.get(entity_id)
    entry = er.async_get(hass).async_get(entity_id)
    readable = intent in COMMAND_ROUTING_READ_INTENTS
    permissions = (Permission.READ, Permission.WRITE) if readable else (Permission.WRITE,)
    unknown_action = bool(state and state.domain in COMMAND_ROUTING_UNKNOWN_ACTION_DOMAINS
                          and intent in COMMAND_ROUTING_ENTITY_INTENTS)
    if (state is None or (intent != "PhoenixStateSummary" and (state.state == "unavailable" or (state.state == "unknown" and not unknown_action))) or entry is None
            or entry.disabled_by or entry.hidden_by or entry.platform == "group"
            or (intent == "PhoenixStateSummary" and entry.entity_category is not None)
            or "entity_id" in state.attributes or resolve(entity_id, token, hass) not in permissions
            or not async_should_expose(hass, "conversation", entity_id)):
        return None
    entity_command = COMMAND_ROUTING_ENTITY_INTENTS.get(intent)
    if entity_command and not readable and not hass.services.has_service(entity_command[0], entity_command[1]):
        return None
    if not supports_command(state, intent):
        return None
    if devices is None:
        devices = device_registry_snapshot(hass)
    if entry.device_id and (devices.get(entry.device_id) is None
                            or entry.device_id in devices.invalid_ancestry_ids):
        return None
    return _digest({
        "registry_id": entry.id, "unique_id": entry.unique_id, "platform": entry.platform,
        "config_entry_id": entry.config_entry_id, "device_id": entry.device_id,
        "area_id": entry.area_id, "aliases": sorted(entry.aliases, key=str),
        "resolved_aliases": er.async_get_entity_aliases(hass, entry),
        "name": entry.name, "original_name": entry.original_name,
        "device_area": devices.effective_area_id(entry.device_id),
        "device_parent": devices.parent_id(entry.device_id),
        "temperature_unit": hass.config.units.temperature_unit,
        "timezone": hass.config.time_zone,
        "entity_command_attributes": {key: state.attributes.get(key) for key in
            ({spec[3] for spec in COMMAND_ROUTING_ENTITY_INTENTS.values() if spec[3]} | {"mode", "pattern", "code_format", "code_arm_required", "motion_detection"})},
        "attributes": {key: state.attributes.get(key) for key in (
            "friendly_name", "supported_color_modes", "supported_features", "device_class",
            "min", "max", "minimum", "maximum", "initial", "step", "unit_of_measurement", "min_temp", "max_temp", "target_temp_step",
            "min_humidity", "max_humidity", "min_color_temp_kelvin", "max_color_temp_kelvin", "has_date", "has_time",
        )},
    })


def _temporal_current(hass: Any, proposal: dict) -> bool:
    if 'voice_origin' in proposal:
        from .voice_context import voice_origin_current
        if not voice_origin_current(hass, proposal['voice_origin']):
            return False
    if proposal.get("intent") in COMMAND_ROUTING_VALUE_INTENTS:
        from .entity_commands import valid_entity_value
        return all(valid_entity_value(hass.states.get(eid), proposal["intent"], proposal.get("value"))
                   for eid in proposal.get("entity_ids", []))
    spec = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(proposal.get("intent", ""))
    if spec is None:
        return True
    from .temporal_values import temporal_value_current
    return all(temporal_value_current(proposal, hass.states.get(eid), hass.config.time_zone)
               for eid in proposal.get("entity_ids", []))


def _capture_plan(hass: Any, data: Any, token: Any, proposal: dict, *, ingress: str = "voice") -> dict | None:
    if 'clarification' in proposal:
        from .command_clarification import clarification_current
        if not clarification_current(hass, data, token, proposal):
            return None
    if proposal["intent"] not in COMMAND_ROUTING_READ_INTENTS and service_arguments(proposal) is None:
        return None
    operation = numeric_operation(proposal["intent"])
    if operation and any(not valid_target_value(hass.states.get(eid), proposal["intent"],
                                               proposal.get(operation.proposal_field)) for eid in proposal["entity_ids"]):
        return None
    if not _temporal_current(hass, proposal):
        return None
    if proposal['intent'] == 'PhoenixStateSummary':
        from .command_summary import summary_current
        if not summary_current(hass, token, proposal):
            return None
    fingerprints = {entity_id: _target_fingerprint(hass, token, entity_id, proposal["intent"])
                    for entity_id in proposal["entity_ids"]}
    if not fingerprints or any(value is None for value in fingerprints.values()):
        return None
    return {"proposal": proposal, "fingerprints": fingerprints, "token_id": token.id, "ingress": ingress,
            "token_version": token.settings_version, "settings": _digest(data.store.get_settings().to_dict())}


def _decision_plan_current(plan: dict, hass: Any, data: Any, token: Any) -> bool:
    if "decision_category" not in plan:
        return "decision_batch" not in plan
    from .agentcli import decision_account_fingerprint, loaded_decision_account
    from .decision_batch import batch_catalog_digest, batch_execution_eligible, capture_batch_targets, percentage_execution_eligible
    from .decision_targets import capture_sequential_targets

    settings = data.store.get_settings()
    category = plan["decision_category"]
    if (plan["proposal"]["intent"] not in COMMAND_ROUTING_READ_INTENTS and "reference_review" not in plan
            and plan.get("decision_target_evidence") != _digest(plan["proposal"])):
        return False
    if category == 'state_summary' and 'decision_batch' not in plan:
        return False
    numeric = category in DECISION_NUMERIC_CATEGORIES
    temporal = category in DECISION_TEMPORAL_CATEGORIES
    typed = plan["proposal"].get("intent") in COMMAND_ROUTING_VALUE_INTENTS
    if (numeric or temporal or plan["proposal"].get("intent") in COMMAND_ROUTING_ENTITY_INTENTS) and "decision_batch" not in plan:
        return False
    catalog_digest = None
    mode = effective_routing_mode(settings, plan.get("ingress", "voice"))
    if "decision_batch" in plan:
        review = plan["decision_batch"]
        if not isinstance(review, dict):
            return False
        account = settings.decision_provider_id
        cfg = loaded_decision_account(hass, account) if account else None
        keys = {"model", "language", "catalog"} | ({"percentage", "proposal"} if numeric else {"value", "proposal"} if temporal or typed else set())
        if category == 'state_summary':
            keys.add('proposal')
            if review.get('proposal') != _digest(plan['proposal']):
                return False
        if "catalog_numeric" in review:
            if numeric or temporal or review["catalog_numeric"] is not True:
                return False
            keys.add("catalog_numeric")
        if "context_review" in plan:
            keys.add("context")
        if "reference_review" in plan:
            keys.add("reference")
        qualified = DECISION_PERCENTAGE_EXECUTION_MODELS if numeric else DECISION_BATCH_EXECUTION_MODELS
        if (not isinstance(review, dict) or set(review) != keys
                or not all(isinstance(review[key], str) for key in keys - {"percentage", "catalog_numeric"})
                or cfg is None or not batch_execution_eligible(cfg, review["language"])
                or review["model"] not in qualified.get((cfg.kind, cfg.base_url, cfg.model), ())):
            return False
        if "context_review" in plan and review["context"] != _digest(plan["context_review"]):
            return False
        if "reference_review" in plan and (numeric or temporal or typed or "context_review" in plan
                or review["reference"] != _digest(plan["reference_review"])):
            return False
        if numeric:
            operation = numeric_operation(plan["proposal"]["intent"])
            if (not percentage_execution_eligible(cfg, review["language"]) or operation is None
                    or not finite_number(review["percentage"])
                    or not operation.valid_value(plan["proposal"].get(operation.proposal_field))
                    or plan["proposal"][operation.proposal_field] != review["percentage"]
                    or _digest(plan["proposal"]) != review["proposal"]):
                return False
        if (temporal or typed) and (review["value"] != plan["proposal"].get("value")
                         or review["proposal"] != _digest(plan["proposal"]) or not _temporal_current(hass, plan["proposal"])):
            return False
        snapshot = capture_batch_targets(hass, token, numeric_preview=numeric or review.get("catalog_numeric", False), extended=True, temporal=temporal)
        if batch_catalog_digest(snapshot) != review["catalog"] or category not in snapshot[1]:
            return False
        catalog_digest = snapshot[1][category].digest()
    return bool((category in DECISION_EXECUTION_CATEGORIES or category == "state_summary" or numeric or temporal)
                and mode == "hybrid" and settings.decision_provider_id
                and DECISION_TARGET_INTENTS.get(category) == plan["proposal"]["intent"]
                and plan.get("decision_account") is not None
                and decision_account_fingerprint(hass, settings.decision_provider_id) == plan["decision_account"]
                and (catalog_digest or capture_sequential_targets(hass, token, category).digest()) == plan.get("decision_catalog"))


def _context_plan_current(plan: dict, hass: Any, data: Any) -> bool:
    if "context_review" not in plan:
        return True
    from .agentcli import loaded_decision_account, decision_account_fingerprint
    review = plan["context_review"]
    account = data.store.get_settings().decision_provider_id
    cfg = loaded_decision_account(hass, account) if account else None
    decision = "decision_category" in plan
    keys = {"account", "kind", "model", "language"} | ({"proposal"} if decision else set())
    qualified = DECISION_FOLLOWUP_EXECUTION_MODELS if decision else DECISION_CONTEXT_EXECUTION_MODELS
    return bool(
        plan.get("ingress") == "chat"
        and effective_routing_mode(data.store.get_settings(), "chat") == "hybrid"
        and isinstance(review, dict) and set(review) == keys
        and all(isinstance(value, str) for value in review.values())
        and review["language"] in DECISION_CONTEXT_EXECUTION_LANGUAGES
        and cfg and account and cfg.kind == review["kind"]
        and review["model"] in qualified.get((cfg.kind, cfg.base_url, cfg.model), ())
        and (not decision or (isinstance(plan.get("decision_batch"), dict) and plan["decision_batch"].get("model") == review["model"]
                              and review["proposal"] == _digest(plan["proposal"])))
        and review["account"] == decision_account_fingerprint(hass, account)
    )


def _reference_plan_current(plan: dict, hass: Any, data: Any) -> bool:
    if "reference_review" not in plan:
        return True
    from .agentcli import loaded_decision_account, decision_account_fingerprint
    review = plan["reference_review"]
    account = data.store.get_settings().decision_provider_id
    cfg = loaded_decision_account(hass, account) if account else None
    proposal = plan["proposal"]
    ids = proposal.get("entity_ids")
    batch = plan.get("decision_batch")
    return bool(
        plan.get("ingress") == "chat" and "context_review" not in plan
        and effective_routing_mode(data.store.get_settings(), "chat") == "hybrid"
        and isinstance(review, dict) and set(review) == {"account", "model", "language", "proposal"}
        and all(isinstance(value, str) for value in review.values())
        and review["language"] in DECISION_REFERENCE_LANGUAGES
        and cfg and account and review["model"] in
        DECISION_REFERENCE_EXECUTION_MODELS.get((cfg.kind, cfg.base_url, cfg.model), ())
        and isinstance(batch, dict) and batch.get("model") == review["model"]
        and batch.get("language") == review["language"]
        and review["account"] == decision_account_fingerprint(hass, account)
        and set(proposal) == {"intent", "entity_ids"}
        and proposal["intent"] in DECISION_REFERENCE_INTENTS
        and isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str) and ids[0].startswith("light.")
        and review["proposal"] == _digest(proposal)
    )


def plan_is_current(plan: dict, hass: Any, data: Any, token: Any) -> bool:
    """Recheck identity, scope, availability, capabilities and configuration."""
    try:
        settings = data.store.get_settings()
        proposal = plan["proposal"]
        if 'clarification' in proposal:
            from .command_clarification import clarification_current
            if not clarification_current(hass, data, token, proposal):
                return False
        if proposal["intent"] not in COMMAND_ROUTING_READ_INTENTS and service_arguments(proposal) is None:
            return False
        operation = numeric_operation(proposal["intent"])
        if operation and any(not valid_target_value(hass.states.get(eid), proposal["intent"],
                                                   proposal.get(operation.proposal_field)) for eid in proposal["entity_ids"]):
            return False
        if proposal['intent'] == 'PhoenixStateSummary':
            from .command_summary import summary_current
            if not summary_current(hass, token, proposal):
                return False
        if not _temporal_current(hass, proposal):
            return False
        ingress = plan.get("ingress", "voice")
        if ingress == "chat":
            enabled = effective_routing_mode(settings, "chat") in ("local", "hybrid")
        elif ingress == "voice":
            enabled = (settings.voice_agent_enabled and effective_routing_mode(settings) in ("local", "hybrid")
                       and settings.voice_agent_token_id == token.id)
        else:
            return False
        return bool(
            enabled and token.is_valid() and token.id == plan["token_id"]
            and token.settings_version == plan["token_version"]
            and _digest(settings.to_dict()) == plan["settings"]
            and _decision_plan_current(plan, hass, data, token)
            and _context_plan_current(plan, hass, data)
            and _reference_plan_current(plan, hass, data)
            and proposal["entity_ids"] and set(proposal["entity_ids"]) == set(plan["fingerprints"])
            and all(plan["fingerprints"][eid] is not None and
                    _target_fingerprint(hass, token, eid, proposal["intent"]) == plan["fingerprints"][eid]
                    for eid in proposal["entity_ids"])
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def bind_service_plan(args: dict, hass: Any, data: Any, token: Any) -> dict | None:
    """Attach constraints from trusted turn context; refuse caller-supplied plans."""
    if COMMAND_ROUTING_PLAN_KEY in args:
        return None
    plan = _local_plan.get()
    if plan is None:
        return args
    bound = {**args, COMMAND_ROUTING_PLAN_KEY: plan}
    return bound if service_plan_is_current(bound, hass, data, token) else None


def service_plan_is_current(args: dict, hass: Any, data: Any, token: Any,
                            entities: list[str] | None = None) -> bool:
    """Saved approval constraints only restrict the ordinary service executor."""
    if COMMAND_ROUTING_PLAN_KEY not in args:
        return True
    plan = args[COMMAND_ROUTING_PLAN_KEY]
    if not isinstance(plan, dict) or not plan_is_current(plan, hass, data, token):
        return False
    proposal = plan["proposal"]
    expected = service_arguments(proposal)
    if expected is None:
        return False
    return (all(args.get(key, {} if key == "service_data" else None) == value for key, value in expected.items())
            and not args.get("area_id") and not args.get("device_id")
            and (entities is None or sorted(entities) == sorted(proposal["entity_ids"])))


@dataclass
class LocalCommandReply:
    """Result of one local command turn: reply text, outcome, and any pending approval."""

    text: str
    outcome: str = "ok"
    pending: dict | None = None
    allows_local_followup: bool = False
    statistics_completed: bool = False


async def _dispatch_command(hass: Any, data: Any, token: Any, plan: dict,
                            authority: RoutingAuthority, base_url: str,
                            include_review_links: bool, *, client_ip: str = VOICE_AGENT_CLIENT_IP,
                            cancel: asyncio.Event | None = None, language: object = "en") -> LocalCommandReply:
    """Run inside the owning ingress's recovery turn, including response delivery."""
    from .agentcli import dispatch_tool_result, _parse_pending
    from .mcp_view import _dispatch_mcp

    await data.agent_recovery.reconcile(data.store, token.id)
    current = authority.current(hass)
    if cancel is not None and cancel.is_set():
        return LocalCommandReply(voice_text(language, "local_cancelled"), "cancelled")
    if current is None or not plan_is_current(plan, hass, data, current):
        return LocalCommandReply(voice_text(language, "token_unavailable"), "unauthorized")
    token = replace(current, confirm_inline_wait_seconds=0)
    proposal = plan["proposal"]
    read = proposal["intent"] in COMMAND_ROUTING_READ_INTENTS
    if not read and data.agent_recovery.blocks(token.id):
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    arguments: dict[str, Any]
    if proposal['intent'] == 'PhoenixStateSummary':
        name = 'get_states'
        arguments = {'projection': {'kind': 'fields', 'fields': ['entity_id', 'state']}}
    elif read:
        name = "get_state"
        field = "state" if proposal["intent"] == "HassGetState" else "attr." + proposal["value"]
        arguments = {"entity_id": proposal["entity_ids"][0], "projection": {
            "kind": "fields", "fields": [field, "attr.unit_of_measurement", "attr.temperature_unit", "attr.device_class"]}}
    else:
        name = "call_service"
        service_args = service_arguments(proposal)
        if service_args is None:
            return LocalCommandReply(voice_text(language, "token_unavailable"), "unauthorized")
        arguments = {"service": {"domain": service_args["domain"], "name": service_args["service"], "data": service_args["service_data"]},
                     "targets": [{"kind": "entity", "ids": proposal["entity_ids"]}]}
    request_id = generate_request_id()
    context = _local_plan.set(plan)
    try:
        response, _method, _resource, outcome = await dispatch_agent_tool(
            _dispatch_mcp, hass=hass, data=data, token=token, name=name, arguments=arguments,
            tool_id=request_id, request_id=request_id, client_ip=client_ip, base_url=base_url,
        )
    except RecoveryRequired:
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    finally:
        _local_plan.reset(context)
    if authority.current(hass) is None:
        return LocalCommandReply(voice_text(language, "outcome_unknown" if not read else "token_unavailable"), "outcome_unknown")
    result = dispatch_tool_result(response)
    pending = _parse_pending(result, outcome=outcome, data=data, token_id=token.id,
                             tool_name=name, request_id=request_id)
    if pending:
        text = voice_text(language, "local_pending")
        if include_review_links:
            text += f"\n\n[{voice_text(language, 'local_review')}]({base_url}{pending['review_url']})"
        return LocalCommandReply(text, pending={
            "approval_id": pending["approval_id"], "tool_name": name,
            "review_url": pending.get("review_url"), "expires_at": pending.get("expires_at"),
            "diff": pending["record"].diff,
        })
    if result.get("isError") or outcome != "allowed":
        return LocalCommandReply(voice_text(language, "local_failed"), "failed")
    payload = None
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                payload = json.loads(item["text"])
                break
            except (ValueError, TypeError):
                continue
    if proposal['intent'] == 'PhoenixStateSummary':
        from .command_summary import summary_text
        summary_reply = summary_text(proposal, payload, language) if plan_is_current(plan, hass, data, token) else None
        return LocalCommandReply(summary_reply or voice_text(language, 'local_read_failed'), outcome='ok' if summary_reply is not None else 'failed', allows_local_followup=summary_reply is not None, statistics_completed=summary_reply is not None)
    if not isinstance(payload, dict):
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    if read:
        if not plan_is_current(plan, hass, data, token):
            return LocalCommandReply(voice_text(language, "token_unavailable"), "unauthorized")
        attributes = payload.get("attributes", {})
        if not isinstance(attributes, dict):
            return LocalCommandReply(voice_text(language, "local_read_failed"), "failed")
        state = payload.get("state") if proposal["intent"] == "HassGetState" else attributes.get(proposal["value"])
        if proposal["intent"] != "HassGetState":
            if state is None:
                return LocalCommandReply(voice_text(language, "local_attribute_unavailable"), "failed")
        from .command_display import async_display_state
        domain = proposal["entity_ids"][0].split(".", 1)[0]
        temperature_unit = (hass.config.units.temperature_unit if domain in ("climate", "water_heater")
                            else attributes.get("temperature_unit") if domain == "weather" else None)
        state = await async_display_state(hass, proposal["entity_ids"][0], payload, language,
            field=proposal.get("value") if proposal["intent"] != "HassGetState" else None,
            temperature_unit=temperature_unit if isinstance(temperature_unit, str) else None)
        current = authority.current(hass)
        if current is None or not plan_is_current(plan, hass, data, current):
            return LocalCommandReply(voice_text(language, "token_unavailable"), "unauthorized")
        if isinstance(state, str) and len(state) <= 1024:
            return LocalCommandReply(voice_text(language, "local_state", state=state), allows_local_followup=True, statistics_completed=True)
        return LocalCommandReply(voice_text(language, "local_read_failed"), "failed")
    if payload.get("partial") or payload.get("success") is not True:
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    warnings = payload.get("mesa_advisory", [])
    advisory = " ".join(warning for warning in warnings if isinstance(warning, str)) if isinstance(warnings, list) else ""
    matched = payload.get("state_verification") == "matched"
    text = voice_text(language, "local_state_matched" if matched else "local_state_unconfirmed")
    return LocalCommandReply(text + (f" {advisory}" if advisory else ""),
                             allows_local_followup=matched and not warnings, statistics_completed=True)


@recovery_turn
async def _run_local_command(hass: Any, data: Any, token: Any, plan: dict,
                             authority: RoutingAuthority, base_url: str,
                             include_review_links: bool, language: object = "en") -> LocalCommandReply:
    """Voice owns a complete non-streaming command recovery turn."""
    return await _dispatch_command(hass, data, token, plan, authority, base_url, include_review_links, language=language)


async def async_execute_chat_command(hass: Any, data: Any, token: Any, proposal: dict,
                                     authority: RoutingAuthority, base_url: str,
                                     client_ip: str, cancel: asyncio.Event, *, language: object = "en") -> LocalCommandReply | None:
    """Reuse the outer Chat recovery turn; None permits pre-dispatch fallback."""
    try:
        plan = _capture_plan(hass, data, token, proposal, ingress="chat")
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        return None
    if plan is None:
        return None
    return await async_execute_chat_plan(hass, data, token, plan, authority, base_url, client_ip, cancel, language=language)


async def async_execute_chat_plan(hass: Any, data: Any, token: Any, plan: dict,
                                  authority: RoutingAuthority, base_url: str,
                                  client_ip: str, cancel: asyncio.Event, *, language: object = "en") -> LocalCommandReply:
    """Consume the frozen Chat plan within its outer recovery turn, without replay."""
    if plan.get("ingress") != "chat":
        return LocalCommandReply(voice_text(language, "token_unavailable"), "unauthorized")
    try:
        return await _dispatch_command(hass, data, token, plan, authority, base_url, True,
                                       client_ip=client_ip, cancel=cancel, language=language)
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")


async def async_execute_local_command(hass: Any, data: Any, token: Any, proposal: dict,
                                      authority: RoutingAuthority, base_url: str,
                                      include_review_links: bool = False, *, language: object = "en") -> str | None:
    """None allows provider fallback only before any local dispatch is attempted."""
    try:
        plan = _capture_plan(hass, data, token, proposal)
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        return None
    if plan is None:
        return None
    return await async_execute_command_plan(hass, data, token, plan, authority, base_url, include_review_links, language=language)


async def async_execute_command_plan(hass: Any, data: Any, token: Any, plan: dict,
                                      authority: RoutingAuthority, base_url: str,
                                      include_review_links: bool = False, *, language: object = "en") -> str:
    """Consume the exact frozen plan; never replan or fall back after this boundary."""
    try:
        reply = await _run_local_command(hass, data, token, plan, authority, base_url, include_review_links, language=language)
        from .routing_statistics import local_result
        local_result(reply, "decision" if plan.get("decision_category") else "local")
        return reply.text
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        # A failed dispatch or receipt save must never cause provider replay.
        return voice_text(language, "outcome_unknown")
