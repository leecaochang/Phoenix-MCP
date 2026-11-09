"""Home Assistant configuration, templates, restart, watches and capability reports."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from homeassistant.core import callback, HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.event import async_track_state_change_event

from ..access_key_store import AccessKeyRecord
from ..tool_catalog import build_mcp_tool_list
from ..config_recovery import ConfigurationRecoveryError, runtime_identity
from ..const import (
    CAP_ALLOW, CAP_CONFIRM, CAP_DENY, MAX_SUBSCRIPTION_SECONDS, PROXY_TIMEOUT_SECONDS,
)
from ..data import PhoenixData
from ..tool_input import str_arg
from ..catalog_text import diff_summary_fields as _summary
from ..capability import access_key_has_write_scope, effective_cap, effective_caps
from ..redaction import build_safe_config
from ..home_companion import is_companion
from ..household_authority import household_tool_access
from ..mesa_audit import request_context
from ..mesa_tools import async_call_mesa_tool, mesa_tool_defs
from ..policy_engine import Permission, resolve_read, scrub_sensitive_attributes
from ..request_authority import AuthorityChangedError, check_request_authority, mark_dispatch_started, write_boundary
from ..template_render import async_render_template_for_access_key, TemplateRenderError
from ..tool_catalog import (
    _EXECUTOR_REGISTRY, _tool_any_caps, _tool_required_caps, _write_gate_open,
    _WRITE_GATED_TOOLS,
)
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _gate, _tool_error, _tool_success, _tool_uncertain
from ..tool_defs import (
    _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS, _NATIVE_TOOL_PUBLIC_TO_INTERNAL, _SYSTEM_TOOL_DEFS,
)
from .discovery import _requires_satisfied, _requires_unavailable_reason
from .logs import _LOG_DETAIL_KEYS, _tool_log_query


def _tool_gate_map(
    access_key: AccessKeyRecord, data: PhoenixData, hass: HomeAssistant
) -> dict[str, Any]:
    """Classify every tool for this access key at the tool level (no entity oracle).

    Buckets: usable, needs_approval (a cap at Confirm on a tool that actually gates), unavailable. A
    static view, mirroring _tool_is_announced.
    """
    # `_EXECUTOR_REGISTRY` is load-bearing: a cap-tied read checks CAP_DENY and never calls `_gate`,
    # so bucketing by cap mode alone would report reads as queued and mis-plan an agent. A
    # registered executor is the right proxy because the Confirm flow applies an approved call
    # through it. Unavailable covers a denied cap, a write tool without write scope, a
    # household-restricted tool, or a missing system surface; the last also gets an
    # unavailable_reasons entry, since an operator can grant a capability but not a missing add-on.
    # call_service and Hass* tools show usable with write scope though a target may still hit a
    # physical or dual gate or MESA confirm.
    if is_companion(access_key):
        return {"usable": [tool["name"] for tool in build_mcp_tool_list(access_key, data)],
                "needs_approval": [], "unavailable": []}
    has_write = access_key_has_write_scope(access_key)
    mesa_defs = mesa_tool_defs() if data.mesa is not None else []
    usable: list[str] = []
    needs_approval: list[str] = []
    unavailable: list[str] = []
    reasons: dict[str, str] = {}
    for tool_def in list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + mesa_defs:
        name = tool_def["name"]
        internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(name, name)
        caps = _tool_required_caps(tool_def)
        any_caps = _tool_any_caps(tool_def)
        if not household_tool_access(name):
            unavailable.append(name)
        elif not _requires_satisfied(tool_def, hass):
            unavailable.append(name)
            reasons[name] = _requires_unavailable_reason(tool_def)
        elif caps or any_caps:
            modes = [effective_cap(access_key, cap) for cap in caps]
            any_modes = [effective_cap(access_key, cap) for cap in any_caps]
            if CAP_DENY in modes or (any_modes and all(mode == CAP_DENY for mode in any_modes)):
                unavailable.append(name)
            elif internal_name in _WRITE_GATED_TOOLS and not _write_gate_open(internal_name, access_key, has_write):
                unavailable.append(name)
            elif (
                CAP_CONFIRM in modes
                or (any_modes and all(mode == CAP_CONFIRM for mode in any_modes))
            ) and internal_name in _EXECUTOR_REGISTRY:
                needs_approval.append(name)
            else:
                usable.append(name)
        elif internal_name in _WRITE_GATED_TOOLS:
            (usable if _write_gate_open(internal_name, access_key, has_write) else unavailable).append(name)
        else:
            usable.append(name)
    result: dict[str, Any] = {
        "usable": sorted(usable),
        "needs_approval": sorted(needs_approval),
        "unavailable": sorted(unavailable),
    }
    if reasons:
        result["unavailable_reasons"] = dict(sorted(reasons.items()))
    return result


async def _tool_get_config(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: return HA config (requires cap_config_read)."""
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_config"
    body = {**build_safe_config(hass), "runtime_identity": runtime_identity(hass)}
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_config"


async def _tool_get_phoenix_diagnostics(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read aggressively scrubbed Phoenix warning/error diagnostics."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_phoenix_diagnostics"
    if any(key in args for key in _LOG_DETAIL_KEYS):
        return _tool_error("Fetch a bucket's full text with get_log_detail and its detail_ref."), "invalid_request", "get_phoenix_diagnostics"
    return await _tool_log_query(
        args,
        access_key,
        hass,
        tool_name="get_phoenix_diagnostics",
        phoenix_only=True,
    )


async def _tool_render_template(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: render a Jinja2 template against permitted entity state."""
    if effective_cap(access_key, "cap_template_render") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "render_template"

    template_str = args.get("template", "")
    if not template_str:
        return _tool_error("Missing required argument: template"), "invalid_request", "render_template"

    try:
        rendered = await async_render_template_for_access_key(template_str, access_key, hass, data)
    except AuthorityChangedError:
        raise
    except TemplateRenderError as exc:
        return _tool_error(str(exc)), "invalid_request", "render_template"
    except Exception:
        return _tool_error("Template rendering failed. Check syntax, input size, and resource limits."), "invalid_request", "render_template"

    return _tool_success(rendered), "allowed", "render_template"


async def _tool_restart_ha(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: restart HA (gated by cap_restart, supports Confirm)."""
    blocked = await _gate(
        "cap_restart", access_key, hass, data,
        tool_name="restart_ha", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_restart_ha(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_restart_ha(args, access_key, hass, data)


@write_boundary
async def _execute_restart_ha(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Journal restart once; a response cannot prove process replacement."""

    if effective_cap(access_key, "cap_restart") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "restart_ha"
    recovery = data.config_recovery
    rid = None
    try:
        async with recovery.resource("homeassistant:restart"):
            identity = runtime_identity(hass)
            rid = await recovery.begin("homeassistant:restart", "restart_ha", access_key.id,
                                       {"snapshot_type": "restart_receipt", "runtime_identity": identity})
            await recovery.mark(rid, "dispatching")
            check_request_authority()
            try:
                async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
                    mark_dispatch_started()
                    await hass.services.async_call("homeassistant", "restart", {}, blocking=True)
            except asyncio.TimeoutError:
                pass
            except ServiceNotFound:
                await recovery.mark(rid, "not_applied")
                return _tool_error("Restart service is unavailable; nothing was dispatched."), "invalid_request", "restart_ha"
            return _tool_success(json.dumps({
                "success": True, "partial": True, "status": "dispatched", "recovery_id": rid,
                "runtime_identity": identity, "retry_safe": False,
                "message": "Reconnect with a fresh client and compare get_config runtime_identity. Do not repeat the restart request.",
            })), "allowed", "restart_ha"
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else "Restart outcome is uncertain. Reconnect and inspect current Home Assistant state before retrying."
        body = {"error": message, "recovery_id": rid, "status": "unknown" if rid else "not_applied", "retry_safe": False}
        return _tool_uncertain(body) if rid else _tool_error(json.dumps(body)), "invalid_request", "restart_ha"
    finally:
        if rid is not None:
            recovery.leave(rid)


def _build_diff_restart_ha(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    return {
        "kind": "system_action",
        **_summary("restart_ha"),
        "target": {"type": "system", "id": "homeassistant", "label": "Home Assistant"},
        "preview": {
            "warning": "Home Assistant will restart and be briefly unavailable.",
        },
    }


# ---------------------------------------------------------------------------
# Capability summary (no capability required)
# ---------------------------------------------------------------------------


async def _tool_get_capability_summary(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: the access key introspecting its own caps/persona/limits. No cap required."""
    caps = effective_caps(access_key)
    body = {
        "access_key_name": access_key.name,
        "persona": access_key.persona,
        "pass_through": access_key.pass_through,
        "write_scope": access_key_has_write_scope(access_key),
        "capabilities": caps,
        "allowed": sorted(c for c, m in caps.items() if m == CAP_ALLOW),
        "confirm_gated": sorted(c for c, m in caps.items() if m == CAP_CONFIRM),
        "denied": sorted(c for c, m in caps.items() if m == CAP_DENY),
        "tools": _tool_gate_map(access_key, data, hass),
        "rate_limit": (
            {"requests_per_min": access_key.rate_limit_requests, "burst_per_sec": access_key.rate_limit_burst}
            if access_key.rate_limit_requests > 0 else "none"
        ),
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_capability_summary"


# ---------------------------------------------------------------------------
# Bounded subscription (cap_config_read): watch_entity
# ---------------------------------------------------------------------------


def _clamp_timeout(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = MAX_SUBSCRIPTION_SECONDS
    return max(1, min(seconds, MAX_SUBSCRIPTION_SECONDS))


_WATCH_TIMED_OUT = object()


async def _tool_watch_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: block until an accessible entity changes state, or until timeout."""
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "watch_entity"

    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "watch_entity"
    if resolve_read(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
        return _tool_error("Entity not found."), "not_found", entity_id

    until_state = args.get("until_state")
    if until_state is not None and not isinstance(until_state, str):
        return _tool_error("until_state must be a string."), "invalid_request", entity_id
    timeout = _clamp_timeout(args.get("timeout", MAX_SUBSCRIPTION_SECONDS))
    future: asyncio.Future = hass.loop.create_future()

    @callback
    def _on_change(event: Any) -> None:
        new_state = event.data.get("new_state")
        if until_state is not None and new_state is not None and new_state.state != until_state:
            return
        if not future.done():
            future.set_result(new_state)

    unsub = async_track_state_change_event(hass, [entity_id], _on_change)
    current = hass.states.get(entity_id)
    if until_state is not None and current is not None and current.state == until_state and not future.done():
        # The awaited state may already hold: an action verified after it landed.
        future.set_result(current)
    try:
        new_state = await asyncio.wait_for(future, timeout)
    except TimeoutError:
        new_state = _WATCH_TIMED_OUT
    finally:
        unsub()
    # Authority, scope and Assist exposure may have changed during the wait.
    check_request_authority()
    if resolve_read(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
        return _tool_error("Entity not found."), "not_found", entity_id
    if new_state is _WATCH_TIMED_OUT:
        timed_out: dict[str, Any] = {"entity_id": entity_id, "changed": False, "timeout_seconds": timeout}
        if until_state is not None:
            timed_out["reached"] = False
            timed_out["state"] = current_state.state if (current_state := hass.states.get(entity_id)) else None
        return _tool_success(json.dumps(timed_out)), "allowed", entity_id

    if new_state is None:
        body = {"entity_id": entity_id, "changed": True, "removed": True}
    else:
        scrubbed = scrub_sensitive_attributes(new_state, access_key, hass)
        # Any state_changed event ends the wait, including an attribute-only
        # update, so say which it was and time it by this update, not last_changed.
        body = {
            "entity_id": entity_id,
            "changed": new_state is not current,
            "state_changed": current is None or new_state.state != current.state,
            "state": scrubbed.get("state"),
            "attributes": scrubbed.get("attributes"),
            "when": new_state.last_updated.isoformat(),
            "last_changed": new_state.last_changed.isoformat(),
        }
        if until_state is not None:
            body["reached"] = True
    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id


async def _tool_mesa(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
    *,
    mesa_tool_name: str,
) -> tuple[dict, str, str]:
    """Adapter for the mesa_* tools, whose entry point takes the name first."""

    with request_context(access_key, request_id, client_ip):
        return await async_call_mesa_tool(mesa_tool_name, args, access_key, hass, data, request_id)
