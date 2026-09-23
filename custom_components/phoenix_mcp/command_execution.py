"""Execute local Voice proposals through existing dispatch, approval and recovery."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from .agent_recovery import RecoveryRequired, dispatch_agent_tool, recovery_turn
from .audit import generate_request_id
from .command_routing import RoutingAuthority
from .const import COMMAND_ROUTING_PLAN_KEY, VOICE_AGENT_CLIENT_IP
from .device_semantics import device_registry_snapshot
from .helpers import voice_text
from .policy_engine import Permission, resolve

# Only a server-owned Voice turn can attach a plan to initial service dispatch.
_local_plan: ContextVar[dict | None] = ContextVar("phoenix_local_command_plan", default=None)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _target_fingerprint(hass: Any, token: Any, entity_id: str, intent: str) -> str | None:
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose
    from homeassistant.components.light import ATTR_SUPPORTED_COLOR_MODES, brightness_supported
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
    if not readable and not entity_id.startswith("light."):
        return None
    modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES)
    if intent == "HassLightSet" and (
        not isinstance(modes, (list, tuple, set, frozenset))
        or not all(isinstance(mode, str) for mode in modes) or not brightness_supported(modes)
    ):
        return None
    devices = device_registry_snapshot(hass)
    if entry.device_id and (devices.get(entry.device_id) is None
                            or entry.device_id in devices.invalid_ancestry_ids):
        return None
    return _digest({
        "registry_id": entry.id, "unique_id": entry.unique_id, "platform": entry.platform,
        "config_entry_id": entry.config_entry_id, "device_id": entry.device_id,
        "area_id": entry.area_id, "aliases": sorted(entry.aliases, key=str),
        "name": entry.name, "original_name": entry.original_name,
        "device_area": devices.effective_area_id(entry.device_id),
        "device_parent": devices.parent_id(entry.device_id),
        "attributes": {key: state.attributes.get(key) for key in (
            "friendly_name", "supported_color_modes", "supported_features", "device_class",
        )},
    })


def _service_args(proposal: dict) -> dict:
    intent = proposal["intent"]
    return {
        "domain": "light", "service": "turn_off" if intent == "HassTurnOff" else "turn_on",
        "entity_id": proposal["entity_ids"],
        "service_data": {"brightness_pct": proposal["brightness_pct"]} if intent == "HassLightSet" else {},
    }


def _capture_plan(hass: Any, data: Any, token: Any, proposal: dict) -> dict | None:
    fingerprints = {entity_id: _target_fingerprint(hass, token, entity_id, proposal["intent"])
                    for entity_id in proposal["entity_ids"]}
    if not fingerprints or any(value is None for value in fingerprints.values()):
        return None
    return {"proposal": proposal, "fingerprints": fingerprints, "token_id": token.id,
            "token_version": token.settings_version, "settings": _digest(data.store.get_settings().to_dict())}


def plan_is_current(plan: dict, hass: Any, data: Any, token: Any) -> bool:
    """Recheck identity, scope, availability, capabilities and configuration."""
    try:
        settings = data.store.get_settings()
        proposal = plan["proposal"]
        return bool(
            settings.voice_agent_enabled and settings.voice_agent_routing_mode == "local"
            and settings.voice_agent_token_id == token.id
            and token.is_valid() and token.id == plan["token_id"]
            and token.settings_version == plan["token_version"]
            and _digest(settings.to_dict()) == plan["settings"]
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
    if proposal["intent"] not in ("HassTurnOn", "HassTurnOff", "HassLightSet"):
        return False
    expected = _service_args(proposal)
    return (all(args.get(key, {} if key == "service_data" else None) == value for key, value in expected.items())
            and not args.get("area_id") and not args.get("device_id")
            and (entities is None or sorted(entities) == sorted(proposal["entity_ids"])))


@dataclass
class LocalCommandReply:
    """Recovery completes only when the dispatched result is certain."""

    text: str
    outcome: str = "ok"


@recovery_turn
async def _run_local_command(hass: Any, data: Any, token: Any, plan: dict,
                             authority: RoutingAuthority, base_url: str,
                             include_review_links: bool) -> LocalCommandReply:
    from .agentcli import dispatch_tool_result, _parse_pending
    from .mcp_view import _dispatch_mcp

    await data.agent_recovery.reconcile(data.store, token.id)
    current = authority.current(hass)
    if current is None or not plan_is_current(plan, hass, data, current):
        return LocalCommandReply(voice_text("en", "token_unavailable"))
    token = replace(current, confirm_inline_wait_seconds=0)
    proposal = plan["proposal"]
    read = proposal["intent"] == "HassGetState"
    if not read and data.agent_recovery.blocks(token.id):
        return LocalCommandReply(voice_text("en", "outcome_unknown"), "outcome_unknown")
    if read:
        name = "get_state"
        arguments = {"entity_id": proposal["entity_ids"][0], "projection": {"kind": "fields", "fields": ["state"]}}
    else:
        name = "call_service"
        service_args = _service_args(proposal)
        arguments = {"service": {"domain": "light", "name": service_args["service"], "data": service_args["service_data"]},
                     "targets": [{"kind": "entity", "ids": proposal["entity_ids"]}]}
    request_id = generate_request_id()
    context = _local_plan.set(plan)
    try:
        response, _method, _resource, outcome = await dispatch_agent_tool(
            _dispatch_mcp, hass=hass, data=data, token=token, name=name, arguments=arguments,
            tool_id=request_id, request_id=request_id, client_ip=VOICE_AGENT_CLIENT_IP, base_url=base_url,
        )
    except RecoveryRequired:
        return LocalCommandReply(voice_text("en", "outcome_unknown"), "outcome_unknown")
    finally:
        _local_plan.reset(context)
    if authority.current(hass) is None:
        return LocalCommandReply(voice_text("en", "outcome_unknown" if not read else "token_unavailable"), "outcome_unknown")
    result = dispatch_tool_result(response)
    pending = _parse_pending(result, outcome=outcome, data=data, token_id=token.id,
                             tool_name=name, request_id=request_id)
    if pending:
        text = voice_text("en", "local_pending")
        if include_review_links:
            text += f"\n\n[{voice_text('en', 'local_review')}]({base_url}{pending['review_url']})"
        return LocalCommandReply(text)
    if result.get("isError") or outcome != "allowed":
        return LocalCommandReply(voice_text("en", "local_failed"))
    payload = None
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                payload = json.loads(item["text"])
                break
            except (ValueError, TypeError):
                continue
    if not isinstance(payload, dict):
        return LocalCommandReply(voice_text("en", "outcome_unknown"), "outcome_unknown")
    if read:
        if not plan_is_current(plan, hass, data, token):
            return LocalCommandReply(voice_text("en", "token_unavailable"))
        state = payload.get("state")
        return LocalCommandReply(voice_text("en", "local_state", state=state) if isinstance(state, str) and len(state) <= 255
                                 else voice_text("en", "local_read_failed"))
    if payload.get("partial") or payload.get("success") is not True:
        return LocalCommandReply(voice_text("en", "outcome_unknown"), "outcome_unknown")
    warnings = payload.get("mesa_advisory", [])
    advisory = " ".join(warning for warning in warnings if isinstance(warning, str)) if isinstance(warnings, list) else ""
    return LocalCommandReply(voice_text("en", "local_sent") + (f" {advisory}" if advisory else ""))


async def async_execute_local_command(hass: Any, data: Any, token: Any, proposal: dict,
                                      authority: RoutingAuthority, base_url: str,
                                      include_review_links: bool = False) -> str | None:
    """None allows provider fallback only before any local dispatch is attempted."""
    try:
        plan = _capture_plan(hass, data, token, proposal)
    except Exception:
        return None
    if plan is None:
        return None
    try:
        reply = await _run_local_command(hass, data, token, plan, authority, base_url, include_review_links)
        return reply.text
    except Exception:
        # A failed dispatch or receipt save must never cause provider replay.
        return voice_text("en", "outcome_unknown")
