"""Execute command proposals through existing dispatch, approval and recovery."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from .agent_recovery import RecoveryRequired, dispatch_agent_tool, recovery_turn
from .audit import generate_request_id
from .command_operations import service_arguments, supports_command
from .command_routing import RoutingAuthority, effective_routing_mode
from .const import COMMAND_ROUTING_PLAN_KEY, VOICE_AGENT_CLIENT_IP, DECISION_TARGET_INTENTS, DECISION_EXECUTION_CATEGORIES
from .device_semantics import device_registry_snapshot
from .helpers import voice_text
from .policy_engine import Permission, resolve

# Only a server-owned command turn can attach a plan to initial service dispatch.
_local_plan: ContextVar[dict | None] = ContextVar("phoenix_local_command_plan", default=None)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _target_fingerprint(hass: Any, token: Any, entity_id: str, intent: str) -> str | None:
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose
    from homeassistant.helpers import entity_registry as er

    state = hass.states.get(entity_id)
    entry = er.async_get(hass).async_get(entity_id)
    readable = intent == "HassGetState"
    permissions = (Permission.READ, Permission.WRITE) if readable else (Permission.WRITE,)
    if (state is None or state.state in ("unknown", "unavailable") or entry is None
            or entry.disabled_by or entry.hidden_by or entry.platform == "group"
            or "entity_id" in state.attributes or resolve(entity_id, token, hass) not in permissions
            or not async_should_expose(hass, "conversation", entity_id)):
        return None
    if not supports_command(state, intent):
        return None
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
        "attributes": {key: state.attributes.get(key) for key in (
            "friendly_name", "supported_color_modes", "supported_features", "device_class",
        )},
    })


def _capture_plan(hass: Any, data: Any, token: Any, proposal: dict, *, ingress: str = "voice") -> dict | None:
    if proposal["intent"] != "HassGetState" and service_arguments(proposal) is None:
        return None
    fingerprints = {entity_id: _target_fingerprint(hass, token, entity_id, proposal["intent"])
                    for entity_id in proposal["entity_ids"]}
    if not fingerprints or any(value is None for value in fingerprints.values()):
        return None
    return {"proposal": proposal, "fingerprints": fingerprints, "token_id": token.id, "ingress": ingress,
            "token_version": token.settings_version, "settings": _digest(data.store.get_settings().to_dict())}


def _decision_plan_current(plan: dict, hass: Any, data: Any, token: Any) -> bool:
    if "decision_category" not in plan:
        return True
    from .agentcli import decision_account_fingerprint
    from .decision_targets import capture_targets

    settings = data.store.get_settings()
    category = plan["decision_category"]
    mode = effective_routing_mode(settings, plan.get("ingress", "voice"))
    return bool(category in DECISION_EXECUTION_CATEGORIES
                and mode == "hybrid" and settings.decision_provider_id
                and DECISION_TARGET_INTENTS.get(category) == plan["proposal"]["intent"]
                and plan.get("decision_account") is not None
                and decision_account_fingerprint(hass, settings.decision_provider_id) == plan["decision_account"]
                and capture_targets(hass, token, category).digest() == plan.get("decision_catalog"))


def plan_is_current(plan: dict, hass: Any, data: Any, token: Any) -> bool:
    """Recheck identity, scope, availability, capabilities and configuration."""
    try:
        settings = data.store.get_settings()
        proposal = plan["proposal"]
        if proposal["intent"] != "HassGetState" and service_arguments(proposal) is None:
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
    """Recovery completes only when the dispatched result is certain."""

    text: str
    outcome: str = "ok"
    pending: dict | None = None
    allows_local_followup: bool = False


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
        return LocalCommandReply(voice_text(language, "token_unavailable"))
    token = replace(current, confirm_inline_wait_seconds=0)
    proposal = plan["proposal"]
    read = proposal["intent"] == "HassGetState"
    if not read and data.agent_recovery.blocks(token.id):
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    if read:
        name = "get_state"
        arguments = {"entity_id": proposal["entity_ids"][0], "projection": {"kind": "fields", "fields": ["state"]}}
    else:
        name = "call_service"
        service_args = service_arguments(proposal)
        if service_args is None:
            return LocalCommandReply(voice_text(language, "token_unavailable"))
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
        return LocalCommandReply(voice_text(language, "local_failed"))
    payload = None
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                payload = json.loads(item["text"])
                break
            except (ValueError, TypeError):
                continue
    if not isinstance(payload, dict):
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    if read:
        if not plan_is_current(plan, hass, data, token):
            return LocalCommandReply(voice_text(language, "token_unavailable"))
        state = payload.get("state")
        if isinstance(state, str) and len(state) <= 255:
            return LocalCommandReply(voice_text(language, "local_state", state=state), allows_local_followup=True)
        return LocalCommandReply(voice_text(language, "local_read_failed"))
    if payload.get("partial") or payload.get("success") is not True:
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    warnings = payload.get("mesa_advisory", [])
    advisory = " ".join(warning for warning in warnings if isinstance(warning, str)) if isinstance(warnings, list) else ""
    return LocalCommandReply(voice_text(language, "local_sent") + (f" {advisory}" if advisory else ""),
                             allows_local_followup=not warnings)


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
    except Exception:
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
    except Exception:
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")


async def async_execute_local_command(hass: Any, data: Any, token: Any, proposal: dict,
                                      authority: RoutingAuthority, base_url: str,
                                      include_review_links: bool = False, *, language: object = "en") -> str | None:
    """None allows provider fallback only before any local dispatch is attempted."""
    try:
        plan = _capture_plan(hass, data, token, proposal)
    except Exception:
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
        return reply.text
    except Exception:
        # A failed dispatch or receipt save must never cause provider replay.
        return voice_text(language, "outcome_unknown")
