"""Execute one MCP request or approved action; the seam every ingress shares."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers import llm as _ha_llm

from .access_key_store import AccessKeyRecord
from .agent_recovery import tool_is_read_only
from .approval_targets import TARGETS_CHANGED, targets_current
from .approvals import PendingApprovalCapacityError, get_approval, STATUS_APPROVED
from .audit import generate_request_id, Outcome
from .const import (
    AI_TASK_CLIENT_IP, ASSIST_CLIENT_IP, BLOCKED_DOMAINS, CAP_CONFIRM, CAP_DENY, DOMAIN,
    HOME_COMPANION_CLIENT_IP, MAX_PENDING_APPROVALS_PER_ACCESS_KEY, MAX_TOOL_NAME_LENGTH,
    MCP_DISCOVER_TTL_MS, MCP_LEGACY_PROTOCOL_VERSION_PREFERRED, MCP_LEGACY_PROTOCOL_VERSIONS,
    MCP_PROTOCOL_VERSIONS, MESA_MODE_OFF, ORIENTATION_GUIDANCE, PHOENIX_VERSION,
    UNKNOWN_ARGUMENT_PROTECTED_FIELDS, VOICE_AGENT_CLIENT_IP, APPROVAL_TARGETS_KEY,
)
from .conversation_log import record_event, recorded_exchange
from .data import PhoenixData
from .helpers import log_internal_failure, log_request as _log
from .capability import access_key_has_write_scope, effective_cap, effective_caps
from .template_sandbox import build_permitted_states as _build_permitted_states
from .redaction import access_key_notes_block, access_key_notes_for_agent, entity_hint_for_agent
from .home_companion import is_companion, wrapper_tool, authorize_call, record_outcome
from .household_authority import household_tool_access
from .mesa_audit import mesa_advisory_ctx
from .mesa_tools import mesa_tool_defs
from .policy_engine import filter_entities_for_access_key, Permission, resolve
from .provider_constants import AGENT_CHAT_CLIENT_IP
from .request_authority import (
    AuthorityChangedError, check_request_authority, dispatch_progress, DispatchProgress,
    policy_version, PolicyChangedError, request_authority, RequestAuthority, dispatch_client_ip,
    dispatch_request_id,
)
from .tool_catalog import (
    _EXECUTOR_REGISTRY, _tool_any_caps, _TOOL_CATALOG_FINGERPRINT, _TOOL_DEFS_BY_NAME,
    _TOOL_HANDLERS, _tool_is_announced, _tool_required_caps, run_companion_turn as run_request,
)
from .tool_common import (
    _CAP_FORBIDDEN_MESSAGE, _resolve_area_id, _tool_error, _tool_refused_before_write, _tool_success,
)
from .request_authority import _approved_exec_ctx
from .tool_contracts import _RETIRED_REPLACEMENTS, adopt_near_misses, unknown_argument_error
from .tool_defs import (
    _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS, _NATIVE_TOOL_PUBLIC_TO_INTERNAL, _NO_HOME_EFFECT_TOOLS, _SYSTEM_TOOL_DEFS,
)
from .tool_input import (
    _argument_notes, decode_container_strings, scalar_arguments, tool_arguments_error,
    with_argument_notes,
)
from .tools.calendar import _tool_calendar_write
from .tools.native import _build_live_context, _UNTRUSTED_DATA_BOUNDARY
from .tools.service import _tool_call_service

_LOGGER = logging.getLogger(__name__)

# What this server can do, answered identically by initialize and
# server/discover: one definition, because both make the same claim to two
# generations of client and must never disagree.
#
# listChanged and subscribe are FALSE on purpose: this transport is stateless
# and holds no connection to push a notification down. A tool list that moves
# under a connected client is reported in-band, on that client's next call, by
# the staleness advisories.
_SERVER_CAPABILITIES: dict[str, dict] = {
    "tools": {"listChanged": False},
    "resources": {"subscribe": False},
    "prompts": {},
}


def _jsonrpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(
    msg_id: Any, code: int, message: str, *, data: dict | None = None, modern: bool = False
) -> dict:
    """A JSON-RPC error. Without a usable id, JSON-RPC 2.0 says `id: null`; 2026-07-28 leaves it out."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    if modern and msg_id is None:
        return {"jsonrpc": "2.0", "error": error}
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


# The same notice when the operator's change left the announced tools as they were:
# what needs approval may differ, but the client's list is still right.
_SETTINGS_CHANGED_ADVISORY = (
    "Notice: this access key's settings were changed by the operator after your "
    "client last fetched the tool list. Your tools are the same, but which actions "
    "need approval may have changed. Call get_capability_summary for the current state."
)

# One-time in-band advisory appended to a tools/call response when this access key's
# settings changed after its last tools/list (the stateless transport cannot
# push notifications/tools/list_changed). Once per staleness epoch.
_STALE_TOOLS_ADVISORY = (
    "Notice: this access key's permissions or settings were changed by the operator "
    "after your client last fetched the tool list, so your tool list and "
    "capability summary may be stale. Call get_capability_summary for the "
    "current state and ask the user to reconnect or refresh this MCP server's "
    "tools."
)

# The deploy-side sibling of the advisory above. Deliberately does NOT mention
# get_capability_summary: that reports permissions, which have not changed here,
# and following it would waste a call and answer the wrong question. What changed
# is the SCHEMAS, and only a reconnect can fix that, because a client enforces
# its own cached schema before the request is ever sent.
_STALE_CATALOG_ADVISORY = (
    "Notice: Phoenix MCP was updated after your client last fetched the tool "
    "list, so some tools may have gained or changed arguments your copy of their "
    "schemas does not have. If a call fails on an argument you believe is valid, "
    "that is the likely cause. Ask the user to reconnect or refresh this MCP "
    "server's tools; re-reading the capability summary will not fix it."
)

# One-time in-band notice carrying changed administrator notes to an external
# client whose instructions predate the change (see _maybe_append_stale_advisory).
_NOTES_CHANGED_NOTICE = (
    "Notice: the operator changed this access key's administrator notes after your "
    "client received its instructions. The notes below replace any earlier ones."
)
_NOTES_REMOVED_NOTICE = (
    "Notice: the operator removed this access key's administrator notes after your "
    "client received its instructions. Disregard the earlier notes."
)


class ExecutorNotRegistered(LookupError):
    """No executor is registered for a tool name.

    Distinct from KeyError because the approve path treats only this as "nothing could have
    run"; a KeyError inside an executor may follow a side effect.
    """


async def async_execute_approved_tool(
    tool_name: str,
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Run the executor for a tool call that was gated and then approved.
    Refuses invalid stored arguments and changed bound targets (TARGETS_CHANGED), then runs
    under confirm-approved MESA semantics. ExecutorNotRegistered is the only proof nothing
    dispatched.
    """
    # The refusals below precede the executor, so nothing can have been written.
    if error := tool_arguments_error(args):
        return _tool_refused_before_write(error), "invalid_request", tool_name
    fn = _EXECUTOR_REGISTRY.get(tool_name)
    if fn is None:
        raise ExecutorNotRegistered(f"No executor registered for tool {tool_name!r}")
    if APPROVAL_TARGETS_KEY in args:
        binding = args[APPROVAL_TARGETS_KEY]
        entities = binding.get("entity_ids") if isinstance(binding, dict) else None
        if (not isinstance(entities, list) or any(not isinstance(entity, str) for entity in entities)
                or not targets_current(args, entities, hass)):
            return _tool_refused_before_write(TARGETS_CHANGED), "denied", tool_name
    # The admin's approval covers the whole action, so the MESA gate inside the
    # executor runs under confirm-approved semantics (see _approved_exec_ctx).
    ctx = _approved_exec_ctx.set(True)
    try:
        return await fn(args, access_key, hass, data)
    finally:
        _approved_exec_ctx.reset(ctx)


def _tool_capability_denied(tool_name: str, access_key: AccessKeyRecord, arguments: dict | None = None) -> bool:
    """Whether a declared capability already refuses this tool (and these arguments) for the access key.

    `caps_when` declares extra capabilities an argument value needs, such as a
    log source that also requires Diagnostics, so they are refused here too.
    """
    tool_def = _TOOL_DEFS_BY_NAME.get(tool_name)
    if tool_def is None:
        return False
    required = list(_tool_required_caps(tool_def))
    for name, by_value in (tool_def.get("caps_when") or {}).items():
        value = (arguments or {}).get(name)
        if isinstance(value, str):
            required.extend(by_value.get(value, ()))
    any_caps = _tool_any_caps(tool_def)
    return any(effective_cap(access_key, cap) == CAP_DENY for cap in required) or bool(
        any_caps and all(effective_cap(access_key, cap) == CAP_DENY for cap in any_caps)
    )


async def _call_tool(
    tool_name: str,
    arguments: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, Outcome, str]:
    """Route a tools/call request to the appropriate tool handler.

    The middle element is the audit outcome, narrowed to audit.Outcome. _TOOL_HANDLERS stores
    handlers as Any, so this does not statically constrain them.
    """
    try:
        check_request_authority()
    except AuthorityChangedError as exc:
        exc.not_dispatched = True
        raise
    if not household_tool_access(tool_name):
        return _tool_error("Forbidden."), "denied", tool_name
    notes: list[str] = []
    notes_context = _argument_notes.set(notes)
    try:
        return with_argument_notes(await _call_tool_checked(
            tool_name, arguments, access_key, hass, data, request_id, client_ip), notes)
    finally:
        _argument_notes.reset(notes_context)


async def _call_tool_checked(
    tool_name: str,
    arguments: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str,
    client_ip: str | None,
) -> tuple[dict, Outcome, str]:
    """_call_tool after its authority checks, with argument notes being collected."""
    # Every refusal below precedes the handler, so nothing can have been written.
    if error := tool_arguments_error(arguments):
        return _tool_refused_before_write(error), "invalid_request", tool_name
    decoded = decode_container_strings(tool_name, arguments)
    if decoded is not arguments:
        if error := tool_arguments_error(decoded):
            return _tool_refused_before_write(error), "invalid_request", tool_name
        arguments = decoded
    if APPROVAL_TARGETS_KEY in arguments:
        return _tool_refused_before_write("Approval target bindings are server-owned."), "invalid_request", tool_name
    if is_companion(access_key):
        refusal = authorize_call(access_key, tool_name, arguments)
        if refusal:
            record_outcome(access_key, tool_name, "denied", arguments=arguments)
            return _tool_error(refusal), "denied", tool_name
        if tool_name == "home_request":
            return await run_request(arguments, access_key, hass, data)
    elif tool_name == "home_request":
        return _tool_error("This tool requires the Home Companion persona."), "denied", tool_name
    internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(tool_name, tool_name)
    entry = _TOOL_HANDLERS.get(internal_name)
    if entry is None:
        return _tool_error(f"Unknown tool: {tool_name}"), "denied", tool_name
    handler, wanted, bound = entry
    # A denied capability learns nothing about its arguments: it is refused here,
    # before any argument check or handler can describe its input.
    if _tool_capability_denied(tool_name, access_key, arguments):
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool_name
    # Curated tools diagnose their arguments inside their handlers, after the
    # capability checks only they can make (call_service's depend on the
    # service). Every other tool is checked here: unknown names first, so a
    # misspelt required argument is named as such. Retired forms keep the
    # handler's migration guidance.
    if tool_name not in UNKNOWN_ARGUMENT_PROTECTED_FIELDS:
        arguments = adopt_near_misses(tool_name, arguments)
        if error := unknown_argument_error(tool_name, arguments):
            return _tool_refused_before_write(error), "invalid_request", tool_name
        if not set(arguments) & set(_RETIRED_REPLACEMENTS.get(tool_name, {})):
            arguments, error = scalar_arguments(tool_name, arguments, read_only=tool_is_read_only(tool_name))
            if error:
                return _tool_refused_before_write(error), "invalid_request", tool_name
    context = {"data": data, "request_id": request_id, "client_ip": client_ip}
    request_context = dispatch_request_id.set(request_id)
    client_context = dispatch_client_ip.set(client_ip)
    progress = DispatchProgress(tracked=handler in (_tool_call_service, _tool_calendar_write))
    parent_progress = dispatch_progress.get()
    if parent_progress is not None:
        parent_progress.tracked = progress.tracked
    progress_context = dispatch_progress.set(progress)
    try:
        result = await handler(
            arguments, access_key, hass, **{k: context[k] for k in wanted}, **bound
        )
    except AuthorityChangedError as exc:
        exc.not_dispatched = progress.tracked and not progress.started
        raise
    finally:
        dispatch_request_id.reset(request_context)
        dispatch_client_ip.reset(client_context)
        dispatch_progress.reset(progress_context)
        if parent_progress is not None and (progress.started or not progress.tracked):
            parent_progress.started = True
    try:
        check_request_authority()
    except AuthorityChangedError as exc:
        exc.not_dispatched = progress.tracked and not progress.started
        if not tool_is_read_only(tool_name):
            exc.receipt = (result[1], result[2])
            if result[1] == "pending_approval" and result[2].startswith("approval:"):
                # The record is server-owned; model text cannot supply a receipt.

                saved = get_approval(data.store, result[2].rsplit(":", 1)[-1])
                exc.not_dispatched = exc.not_dispatched and (
                    saved is not None and saved.access_key_id == access_key.id and saved.request_id == request_id
                    and saved.status in {"pending", "rejected", "cancelled", "expired"}
                    and not saved.execution_started_at
                )
                if (saved is not None and saved.access_key_id == access_key.id
                        and saved.request_id == request_id and saved.status == STATUS_APPROVED
                        and isinstance(saved.result, dict) and saved.result.get("outcome") == "allowed"):
                    exc.receipt = ("allowed", result[2])
        raise
    record_outcome(access_key, tool_name, result[1], result[0], arguments, hass)
    return result


async def _get_ha_assist_api(hass: HomeAssistant) -> Any:
    """Return HA's Assist LLM APIInstance, or raise if unavailable.

    LLMContext's fields vary across HA versions, so kwargs are filtered to the live dataclass;
    an unknown field would otherwise read as no prompts.
    """


    wanted = {
        "platform": DOMAIN,
        "context": None,
        "user_prompt": None,
        "language": "en",
        "assistant": "conversation",
        "device_id": None,
    }
    declared = {f.name for f in dataclasses.fields(_ha_llm.LLMContext)}
    kwargs: dict[str, Any] = {k: v for k, v in wanted.items() if k in declared}
    llm_context = _ha_llm.LLMContext(**kwargs)
    return await _ha_llm.async_get_api(hass, _ha_llm.LLM_API_ASSIST, llm_context)


def _build_server_info(access_key: AccessKeyRecord, hass: HomeAssistant, base_url: str) -> dict:
    """Build the phx://server-info resource payload for the MCP resources/read endpoint."""
    states = hass.states.async_all()
    if access_key.pass_through:
        # Use build_permitted_states to get the same set the access key actually sees,
        # including the Phoenix-platform entity filter (sensor.phoenix_mcp_* telemetry sensors).
        count = len(_build_permitted_states(access_key, hass))
    else:
        filtered = filter_entities_for_access_key(states, access_key, hass)
        count = len(filtered)

    return {
        "name": "Phoenix MCP Scoped Proxy",
        "version": PHOENIX_VERSION,
        "access_key_name": access_key.name,
        "permitted_entity_count": count,
        "capability_flags": effective_caps(access_key),
        "persona": access_key.persona,
        "native_ha_mcp_endpoint": f"{base_url}/api/mcp",
        "phoenix_mcp_context_endpoint": f"{base_url}/api/phoenix-mcp/context",
    }


def _build_context_plain(access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """Build the plain-text context document listing accessible entities and capabilities."""
    if is_companion(access_key):
        return "Use home_request for scoped household requests; installation context is unavailable."

    lines: list[str] = []

    if access_key.pass_through:
        # Use build_permitted_states for an accurate count that respects Phoenix-platform
        # entity filtering and use_assist_exposure (same set the access key actually sees).
        count = len(_build_permitted_states(access_key, hass))
        lines.append("This access key operates in pass-through mode.")
        lines.append(
            f"It has unrestricted access to all {count} accessible Home Assistant entities and services."
        )
        lines.append("")
        lines.append("The phoenix_mcp domain is always blocked regardless of access key type.")
    else:
        states = hass.states.async_all()
        entity_hints = hass.data[DOMAIN].store.get_entity_hints()
        accessible: list[tuple[str, str, str | None]] = []
        for state in states:
            perm = resolve(state.entity_id, access_key, hass)
            if perm == Permission.WRITE:
                accessible.append((state.entity_id, "READ/WRITE", entity_hint_for_agent(access_key, state.entity_id, hass, entity_hints)))
            elif perm == Permission.READ:
                accessible.append((state.entity_id, "READ", entity_hint_for_agent(access_key, state.entity_id, hass, entity_hints)))

        accessible.sort(key=lambda x: x[0])
        lines.append("You have access to the following Home Assistant entities:")
        if accessible:
            for eid, perm_str, hint in accessible:
                hint_part = f' - "{hint}"' if hint else ""
                lines.append(f"- {eid} ({perm_str}){hint_part}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append(
            "You cannot access any other entities. "
            "Do not attempt to call services on entities not listed above."
        )

    notes_block = access_key_notes_block(access_key, hass)
    if notes_block:
        lines.extend(("", notes_block))
    lines.append("")
    caps = effective_caps(access_key)
    lines.append("Capabilities (deny / allow / confirm; confirm requires admin approval for each change, while reads run):")
    label_map = (
        ("cap_config_read", "Config read"),
        ("cap_automation_write", "Automation write"),
        ("cap_script_write", "Script write"),
        ("cap_template_render", "Template render"),
        ("cap_restart", "Restart"),
        ("cap_physical_control", "Physical control (locks/alarms/covers)"),
        ("cap_broadcast", "Broadcast"),
        ("cap_log_read", "Log read"),
        ("cap_service_response", "Service response"),
    )
    for cap_key, label in label_map:
        lines.append(f"- {label}: {caps.get(cap_key, 'deny')}")
    lines.append("")
    if access_key.rate_limit_requests > 0:
        lines.append(
            f"Rate limit: {access_key.rate_limit_requests} requests/min, burst {access_key.rate_limit_burst}/sec"
        )
    else:
        lines.append("Rate limit: none")

    return "\n".join(lines)


def _build_context_json(access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Build the structured JSON context document for the ?format=json context endpoint."""
    if is_companion(access_key):
        return {"access_key_name": access_key.name, "persona": access_key.persona, "entities": [],
                "capability_flags": effective_caps(access_key), "message": "Use home_request for scoped household requests."}
    registry = er.async_get(hass)
    dev_registry = dr.async_get(hass)

    entities: list[dict] = []
    states = hass.states.async_all()
    entity_hints = hass.data[DOMAIN].store.get_entity_hints()

    if access_key.pass_through:
        _expose_check = None
        if access_key.use_assist_exposure:
            # Local: optional component, imported only when this path runs.
            from homeassistant.components.homeassistant.exposed_entities import (
                async_should_expose as _should_expose,
            )
            _expose_check = lambda eid: _should_expose(hass, "conversation", eid)
        for state in states:
            eid = state.entity_id
            if eid.split(".")[0] in BLOCKED_DOMAINS:
                continue
            entry = registry.async_get(eid)
            # Exclude Phoenix MCP telemetry sensors (registered to the phoenix_mcp platform) so
            # pass_through access keys see the same entity set as build_permitted_states().
            if entry is not None and entry.platform == DOMAIN:
                continue
            if _expose_check is not None and not _expose_check(eid):
                continue
            area_id = _resolve_area_id(entry, dev_registry)
            row: dict = {"entity_id": eid, "permission": "READ/WRITE", "area_id": area_id}
            if hint := entity_hint_for_agent(access_key, eid, hass, entity_hints):
                row["hint"] = hint
            entities.append(row)
    else:
        for state in states:
            perm = resolve(state.entity_id, access_key, hass)
            if perm not in (Permission.READ, Permission.WRITE):
                continue
            entry = registry.async_get(state.entity_id)
            area_id = _resolve_area_id(entry, dev_registry)
            perm_str = "READ/WRITE" if perm == Permission.WRITE else "READ"
            e: dict = {"entity_id": state.entity_id, "permission": perm_str, "area_id": area_id}
            hint = entity_hint_for_agent(access_key, state.entity_id, hass, entity_hints)
            if hint:
                e["hint"] = hint
            entities.append(e)

    entities.sort(key=lambda e: e["entity_id"])

    return {
        "access_key_name": access_key.name,
        "pass_through": access_key.pass_through,
        "persona": access_key.persona,
        "notes": access_key_notes_for_agent(access_key, hass),
        "entities": entities,
        "capability_flags": effective_caps(access_key),
        "rate_limit": {
            "requests_per_minute": access_key.rate_limit_requests,
            "burst_per_second": access_key.rate_limit_burst,
        },
    }


def _build_instructions(access_key: AccessKeyRecord, data: PhoenixData, base_url: str, hass: HomeAssistant) -> str:
    """Return the key-aware MCP ``instructions`` primer sent at every session start.

    Holds the etiquette, this key's gated capabilities and notes, and a link to the skill
    guide. A Home Companion key gets its fixed wrapper primer; its notes go to the inner turn.
    """
    if is_companion(access_key):
        return ("This access key uses Phoenix Home Companion. Only home_request is exposed. "
                "Send a complete home request with all restrictions. Each request is independent. "
                "Read the structured status and tool_outcomes; pending approval is not execution. "
                "Do not automatically retry an unknown action outcome.")
    caps = effective_caps(access_key)
    confirm_gated = sorted(c for c, m in caps.items() if m == CAP_CONFIRM)
    lines = [
        "Phoenix MCP is a scoped Home Assistant gateway. This access key sees only operator-granted "
        "entities/tools; some actions are gated.",
        "",
        f"- {ORIENTATION_GUIDANCE} It shows which tools run directly, need approval or are "
        "unavailable. Discover scoped entities with search_entities, or get_overview for the home as a whole.",
        "- Entity/history timestamps may be returned in UTC. If you do not already know Home "
        "Assistant's local offset, call llm__GetDateTime for local presentation. "
        "Present the local date and time directly. Do not mention UTC, offsets, or conversion "
        "unless asked.",
        # Must agree with tool_common._tool_pending, which the agent reads at the
        # moment it matters; a primer that says something else is a second story
        # about the same event. Both say: continue, then collect the outcomes
        # together, because approvals queue and are cleared in one action.
        "- Status \"pending_approval\" is normal: a human must approve; the action has not run. "
        "Do not retry; do not stop after one: further gated calls queue alongside it for one "
        "operator action. For outcomes, call wait_for_approval ONCE with approval_ids listing "
        "all of them, or get_approval_status "
        "for a one-shot check. Otherwise report what awaits approval.",
        "- Preview risky/bulk service calls with dry_run_service; use whatif for triggered automations.",
        "- A tool absent from the list is unavailable: ask the operator to grant its capability; "
        "do not attempt it.",
        "- A permissions-changed notice means a stale tool list: call get_capability_summary "
        "and ask the user to reconnect or refresh this MCP server's tools.",
    ]
    if confirm_gated:
        lines.append(
            "- Admin approval per call as of this connection: "
            + ", ".join(confirm_gated)
            + ". Permissions can change mid-session; check get_capability_summary."
        )
    if data.store.get_settings().mesa_mode != MESA_MODE_OFF:
        lines.append(
            "- MESA per-entity safety is active: nature can require read-only access or confirmation "
            "regardless of capabilities. Read control_mode in describe_entity/find_available_actions."
        )
    if (
        caps.get("cap_automation_write") != CAP_DENY
        and caps.get("cap_search") != CAP_DENY
    ):
        lines.append(
            "- Before authoring an automation, call find_available_actions on the trigger/condition "
            "entity. Prefer its purpose-built triggers/conditions and ready-to-use config examples "
            "over generic state/numeric_state equivalents."
        )
    lines.append("")
    lines.append(
        f"Full Phoenix MCP and Home Assistant usage guide: {base_url}/api/phoenix-mcp/skill (fetch it before "
        "complex automation, scene, or configuration work)."
    )
    # The operator's notes come last: a client that clips server instructions
    # (Claude Code keeps 4,096 characters) then loses only the end of the notes,
    # never the guide link.
    notes_block = access_key_notes_block(access_key, hass)
    if notes_block:
        lines.extend(("", notes_block))
    return "\n".join(lines)


def _announced_tools(access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData) -> list[dict]:
    """Return the client-facing tools that tools/list announces to this key.
    Only tools the key can use: cap-tied, write-scoped and system-surface tools gate; reads
    are always announced. announce_all_tools overrides this as a debugging aid; calls still
    refuse.
    """
    if is_companion(access_key):
        return [wrapper_tool()]
    announce_all = getattr(access_key, "announce_all_tools", False)
    has_write = access_key_has_write_scope(access_key)
    mesa_defs = mesa_tool_defs() if data.mesa is not None else []
    return [
        {k: v for k, v in tool_def.items() if k not in ("cap", "caps", "caps_any", "requires")}
        for tool_def in list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + mesa_defs
        if household_tool_access(tool_def["name"]) and (announce_all or _tool_is_announced(tool_def, access_key, has_write, hass))
    ]


def _maybe_append_stale_advisory(
    tool_result: dict, access_key: AccessKeyRecord, data: PhoenixData, client_ip: str,
    hass: HomeAssistant,
) -> bool:
    """Append the one-time stale-tools, catalog or notes notices to a result, in place.
    Returns whether one was added, for the audit row. Substitutes for tools/list_changed on
    the stateless transport, once per epoch. Skipped for internal surfaces and Home Companion
    notes.
    """
    internal = client_ip in (
        AGENT_CHAT_CLIENT_IP, ASSIST_CLIENT_IP, VOICE_AGENT_CLIENT_IP, AI_TASK_CLIENT_IP, HOME_COMPANION_CLIENT_IP)
    content = tool_result.get("content")
    notices: list[str] = []
    if not internal:
        if access_key.settings_version > access_key.tools_list_version:
            # A change that leaves the announced tools as they were (Allow to
            # Confirm, say) needs no reconnect, as the panel tells the operator;
            # without a record of what was announced, advise the reconnect.
            given = data.announced_tools.get(access_key.id)
            unchanged = given is not None and given == frozenset(t["name"] for t in _announced_tools(access_key, hass, data))
            notices.append(_SETTINGS_CHANGED_ADVISORY if unchanged else _STALE_TOOLS_ADVISORY)
        if (
            access_key.tools_catalog_fingerprint
            and access_key.tools_catalog_fingerprint != _TOOL_CATALOG_FINGERPRINT
        ):
            notices.append(_STALE_CATALOG_ADVISORY)
    epoch = f"{access_key.settings_version}:{access_key.tools_catalog_fingerprint}"
    if notices and data.stale_tools_advised.get(access_key.id) != epoch:
        data.stale_tools_advised[access_key.id] = epoch
    else:
        notices = []
    if (not internal and not is_companion(access_key) and isinstance(content, list)
            and access_key.notes_version > access_key.notes_delivered_version):
        block = access_key_notes_block(access_key, hass)
        notices.append(f"{_NOTES_CHANGED_NOTICE}\n{block}" if block else _NOTES_REMOVED_NOTICE)
        access_key.notes_delivered_version = access_key.notes_version
    if not notices:
        return False
    if isinstance(content, list):
        content.extend({"type": "text", "text": n} for n in notices)
    return True


async def _dispatch_tools_call(
    params: dict,
    msg_id: Any,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str,
    client_ip: str,
) -> tuple[dict, str, str, Outcome]:
    """Run one tools/call and build its JSON-RPC response and audit row.

    This is the single choke point for every tools/call, including each item of a
    JSON-RPC batch, which is why the safety net lives here.
    """
    # Capped before dispatch or logging: this is client-supplied and otherwise
    # unbounded. A malformed client (a local model emitting garbled tool-call
    # syntax) can send garbage as the "name" field; every real tool name is a
    # short identifier, so a generous cap only ever clips pathological input and
    # keeps the audit log (method and resource both derive from it) readable.
    raw_name = params.get("name")
    tool_name = raw_name[:MAX_TOOL_NAME_LENGTH] if isinstance(raw_name, str) else ""
    if tool_name != "home_request" and _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(tool_name, tool_name) not in _TOOL_HANDLERS:
        _log(data, access_key, request_id=request_id, method="tools/call", resource="unknown_tool",
             outcome="invalid_request", client_ip=client_ip)
        return _jsonrpc_error(msg_id, -32602, "Unknown tool."), "tools/call", "unknown_tool", "invalid_request"
    arguments = params.get("arguments")
    if arguments is None:
        # Omitted or null is a no-argument call. Any other non-object, falsy or
        # not, is refused by the argument check rather than read as {}.
        arguments = {}
    mesa_advisory_ctx.set(False)
    try:
        tool_result, outcome, resource = await _call_tool(
            tool_name, arguments, access_key, hass, data,
            request_id=request_id, client_ip=client_ip,
        )
    except AuthorityChangedError as exc:
        if exc.receipt is not None:
            receipt_outcome, receipt_resource = exc.receipt
            _log(data, access_key, request_id=request_id, method=tool_name,
                 resource=receipt_resource, outcome=receipt_outcome, client_ip=client_ip,
                 payload={"name": tool_name, "arguments": arguments, "result_withheld": True})
        raise
    except PendingApprovalCapacityError:
        # Not an internal error: the key's approval queue is full.
        tool_result = _tool_error(
            f"This access key already has {MAX_PENDING_APPROVALS_PER_ACCESS_KEY} actions waiting for approval, "
            "so this one was not queued. Ask the user to approve or reject the waiting requests in "
            "Phoenix MCP's Approvals, then try again."
        )
        outcome = "denied"
        resource = tool_name
    except Exception:
        # _call_tool is a bare dispatcher with no exception handling of its own,
        # and post() has none either: an unhandled exception here would reach
        # aiohttp's default error handling, which the MCP client cannot parse
        # cleanly, and shows up as a multi-minute client HANG rather than a fast
        # error. Log the real exception server-side for diagnosis, never leak it
        # to the client, return something self-correctable.
        _LOGGER.exception(
            "Unhandled exception in tool %s for access key %s rid=%s",
            tool_name, access_key.name, request_id,
        )
        tool_result = _tool_error("Internal error processing this tool call.")
        outcome = "invalid_request"
        resource = tool_name

    stale = _maybe_append_stale_advisory(tool_result, access_key, data, client_ip, hass)
    _log(data, access_key, request_id=request_id, method=tool_name or "tools/call",
         resource=resource, outcome=outcome, client_ip=client_ip,
         payload={"name": tool_name, "arguments": arguments},
         mesa_advisory=mesa_advisory_ctx.get(),
         stale_tools_advisory=stale)
    return _jsonrpc_result(msg_id, tool_result), tool_name or "tools/call", resource, outcome


# Every JSON-RPC method _dispatch_mcp answers, mirroring its branches. The transport reads
# it to choose an unknown method's HTTP status before framing: 404 with -32601 tells a
# client this is an MCP endpoint without that method, and preparing an SSE stream would put
# a 200 on the wire.
_MCP_METHODS: frozenset[str] = frozenset({
    "server/discover",
    "initialize",
    "notifications/initialized",
    "initialized",
    "ping",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/templates/list",
    "resources/read",
    "prompts/list",
    "prompts/get",
})
# 2026-07-28 removed ping together with the session lifecycle.
_MCP_MODERN_METHODS = _MCP_METHODS - {
    "initialize", "notifications/initialized", "initialized", "ping",
}
_MCP_LEGACY_METHODS = _MCP_METHODS - {"server/discover"}


async def _retain_unknown_outcome(data: PhoenixData, access_key_id: str, name: str, request_id: str) -> None:
    """Persist an unknown-outcome claim; the in-memory barrier survives a failed save."""
    try:
        await data.agent_recovery.retain_unknown(access_key_id, name, request_id)
    except Exception as error:
        log_internal_failure(_LOGGER, "retain_unknown", error)


def _may_have_changed_the_home(name: str, progress: DispatchProgress) -> bool:
    """Whether a request that stopped early can have reached the home.

    Only evidence says no: a tracked handler that never reached its boundary, or a tool with
    nothing to change. Every other handler counts as started once entered.
    """
    return name not in _NO_HOME_EFFECT_TOOLS and not (progress.tracked and not progress.started)


def _record_interrupted_write(
    hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord,
    params: dict, request_id: str, client_ip: str, progress: DispatchProgress,
) -> None:
    """Account for a write whose caller went away before it finished.
    Runs in the cancelled task, so the audit row and barrier happen without an await; saving
    the barrier is its own task. A request held at an approval is not unknown, and
    ``home_request`` turns hold their own claims. A request that cannot have reached the home
    gets its row and nothing else. A reload may have replaced the admitting runtime.
    """
    data = hass.data.get(DOMAIN) or data
    name = params["name"]
    payload = {"name": name, "arguments": params.get("arguments"), "interrupted": True}
    try:
        if any(approval.get("request_id") == request_id for approval in data.store.get_pending_approvals()):
            _log(data, access_key, request_id=request_id, method=name, resource=name,
                 outcome="pending_approval", client_ip=client_ip, payload=payload)
            return
        if not _may_have_changed_the_home(name, progress):
            _log(data, access_key, request_id=request_id, method="request/cancelled", resource=name,
                 outcome="cancelled", client_ip=client_ip, payload=payload)
            return
        _log(data, access_key, request_id=request_id, method="request/cancelled", resource=name,
             outcome="unknown", client_ip=client_ip, payload=payload)
        record_event(data, "notice", {"reason": "execution_unknown"}, outcome="unknown")
        if name != "home_request":
            data.agent_recovery.hold_unknown(access_key.id, name, request_id)
            hass.async_create_task(
                _retain_unknown_outcome(data, access_key.id, name, request_id),
                "phoenix_mcp_retain_unknown",
            )
    except Exception as error:  # the cancellation must still propagate
        log_internal_failure(_LOGGER, "interrupted_write", error)


@recorded_exchange
async def _dispatch_mcp(
    method: str,
    msg_id: Any,
    params: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    base_url: str,
    request_id: str | None = None,
) -> tuple[dict | None, str, str, str]:
    """Dispatch one MCP method call.

    Returns (response_msg, log_method, log_resource, outcome).
    response_msg is None for notifications that require no response.
    """
    request_id = request_id or generate_request_id()
    authority = request_authority.get()
    admitted_context = None
    if authority is None:
        authority = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash)
        admitted_context = request_authority.set(authority)
    context = None
    progress = DispatchProgress(tracked=False)
    progress_context = dispatch_progress.set(progress)
    write_entered = False
    dispatch_entered = False
    result = None
    try:
        current = authority.current_access_key()
        # Internal surfaces deliberately disable inline approval waiting. Refresh
        # all authorization fields while preserving only that execution setting.
        access_key = (
            dataclasses.replace(current, confirm_inline_wait_seconds=0)
            if access_key.confirm_inline_wait_seconds == 0
            and current.confirm_inline_wait_seconds != 0
            else current
        )
        context = policy_version.set(access_key.settings_version)
        if method == "tools/call" and isinstance(params.get("name"), str) and not tool_is_read_only(params["name"]):
            await data.agent_recovery.reconcile(data.store, access_key.id)
            if data.agent_recovery.blocks(access_key.id, request_id):
                _log(data, access_key, request_id=request_id, method=params["name"], resource=params["name"],
                     outcome="denied", client_ip=client_ip)
                recovery_error = _tool_error("This new request was not executed because an earlier agent action has an unresolved outcome. Check the earlier action in Home Assistant and resolve it in the Needs Attention window of the Phoenix MCP panel before trying again. Direct read-only tools remain available where this access key exposes them; delegated requests are blocked.")
                return _jsonrpc_result(msg_id, recovery_error), method, params["name"], "denied"
        check_request_authority()
        write_entered = method == "tools/call" and isinstance(params.get("name"), str) and not tool_is_read_only(params["name"])
        dispatch_entered = True
        result = await _dispatch_mcp_authorized(method, msg_id, params, access_key, hass, data, client_ip, base_url, request_id)
        check_request_authority()
        return result
    except AuthorityChangedError as exc:
        if write_entered:
            data = hass.data.get(DOMAIN) or data
            completed = (result is not None and result[3] == "allowed") or (exc.receipt is not None and exc.receipt[0] == "allowed")
            name = params["name"]
            if exc.not_dispatched and not completed:
                _log(data, access_key, request_id=request_id, method="authority/changed", resource=name,
                     outcome="denied", client_ip=client_ip)
                return _jsonrpc_result(msg_id, _tool_error("Request authority changed before dispatch. No action was executed.")), method, name, "denied"
            if name in _NO_HOME_EFFECT_TOOLS and not completed:
                _log(data, access_key, request_id=request_id, method="authority/changed", resource=name,
                     outcome="denied", client_ip=client_ip)
                return _jsonrpc_result(msg_id, _tool_error("Request authority changed while this tool was running. It changes nothing in the home, so there is nothing to check.")), method, name, "denied"
            _log(data, access_key, request_id=request_id, method="authority/changed", resource=name,
                 outcome="invalid_request" if completed else "unknown", client_ip=client_ip)
            if completed:
                receipt = _tool_success("Action dispatch completed, but authority changed before its response could be delivered. Response details were withheld. Do not repeat the action; verify its outcome in Home Assistant.")
                return _jsonrpc_result(msg_id, receipt), method, name, "allowed"
            record_event(data, "notice", {"reason": "execution_unknown"}, outcome="unknown")
            await _retain_unknown_outcome(data, access_key.id, name, request_id)
            return _jsonrpc_error(msg_id, -32000, "Authority changed during dispatch. The action may already have executed. Do not repeat it. Check Home Assistant and review the action in the Needs Attention window of the Phoenix MCP panel before submitting another write."), method, name, "invalid_request"
        _log(data, access_key, request_id=request_id, method=method, resource="/api/phoenix-mcp",
             outcome="denied", client_ip=client_ip)
        if isinstance(exc, PolicyChangedError) and method == "tools/call":
            refusal = _tool_error("Permissions changed while this tool was running. Its result was withheld. The conversation can continue using current permissions." if dispatch_entered else "Permissions changed before dispatch. No action was executed. The conversation can continue using current permissions.")
            _maybe_append_stale_advisory(refusal, access_key, data, client_ip, hass)
            return _jsonrpc_result(msg_id, refusal), method, "/api/phoenix-mcp", "denied"
        return _jsonrpc_error(msg_id, -32000, "Request authority changed. Check earlier actions before resubmitting with current permissions."), method, "/api/phoenix-mcp", "denied"
    except asyncio.CancelledError:
        if write_entered:
            _record_interrupted_write(hass, data, access_key, params, request_id, client_ip, progress)
        raise
    except Exception as exc:
        log_internal_failure(_LOGGER, "mcp_dispatch", exc)
        if write_entered:
            record_event(data, "notice", {"reason": "execution_unknown"}, outcome="unknown")
        _log(data, access_key, request_id=request_id, method=method, resource="/api/phoenix-mcp",
             outcome="invalid_request", client_ip=client_ip)
        return _jsonrpc_error(msg_id, -32603, "Internal error."), method, "/api/phoenix-mcp", "invalid_request"
    finally:
        dispatch_progress.reset(progress_context)
        if context is not None:
            policy_version.reset(context)
        if admitted_context is not None:
            request_authority.reset(admitted_context)


async def _dispatch_mcp_authorized(
    method: str, msg_id: Any, params: dict, access_key: AccessKeyRecord,
    hass: HomeAssistant, data: PhoenixData, client_ip: str,
    base_url: str, request_id: str,
) -> tuple[dict | None, str, str, str]:
    """Dispatch with current request authority established by the outer boundary."""
    if method == "server/discover":
        # The stateless-era replacement for initialize: identity, versions and
        # capabilities in one call, with no handshake to establish. A client
        # speaking a revision that has no initialize would otherwise never receive
        # the access-key-aware primer, which is the only place an agent is told which
        # of its capabilities are approval-gated.
        resp = _jsonrpc_result(msg_id, {
            "resultType": "complete",
            "supportedVersions": list(MCP_PROTOCOL_VERSIONS),
            "capabilities": _SERVER_CAPABILITIES,
            "instructions": _build_instructions(access_key, data, base_url, hass),
            "ttlMs": MCP_DISCOVER_TTL_MS,
            # NEVER "public". The instructions name THIS access key's confirm-gated
            # capabilities, so a shared intermediary caching one access key's answer
            # would serve it to the next access key reaching it through that proxy.
            "cacheScope": "private",
            "_meta": {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "Phoenix MCP", "version": PHOENIX_VERSION,
                },
            },
        })
        # The instructions carry the current notes; in memory, persisted by the
        # periodic flush like tools_list_version.
        access_key.notes_delivered_version = access_key.notes_version
        _log(data, access_key, request_id=request_id, method="server/discover",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "server/discover", "/api/phoenix-mcp", "allowed"

    if method == "initialize":
        # Echo the client's version when it is a supported one, otherwise name
        # the preferred one and let the client decide. Membership on a tuple
        # compares rather than hashes, so a non-string protocolVersion falls to
        # the preferred version instead of raising.
        requested = params.get("protocolVersion")
        version = (
            requested
            if requested in MCP_LEGACY_PROTOCOL_VERSIONS
            else MCP_LEGACY_PROTOCOL_VERSION_PREFERRED
        )
        resp = _jsonrpc_result(msg_id, {
            "protocolVersion": version,
            "capabilities": _SERVER_CAPABILITIES,
            "serverInfo": {"name": "Phoenix MCP", "version": PHOENIX_VERSION},
            "instructions": _build_instructions(access_key, data, base_url, hass),
        })
        access_key.notes_delivered_version = access_key.notes_version
        _log(data, access_key, request_id=request_id, method="initialize",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "initialize", "/api/phoenix-mcp", "allowed"

    if method in ("notifications/initialized", "initialized"):
        _log(data, access_key, request_id=request_id, method=method,
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return None, method, "/api/phoenix-mcp", "allowed"

    if method == "ping":
        resp = _jsonrpc_result(msg_id, {})
        _log(data, access_key, request_id=request_id, method="ping",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "ping", "/api/phoenix-mcp", "allowed"

    if is_companion(access_key) and method in {"resources/list", "resources/templates/list", "prompts/list"}:
        key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates", "prompts/list": "prompts"}[method]
        return _jsonrpc_result(msg_id, {key: []}), method, "/api/phoenix-mcp", "allowed"
    if is_companion(access_key) and method in {"resources/read", "prompts/get"}:
        return _jsonrpc_error(msg_id, -32602, "Unavailable for this persona."), method, "/api/phoenix-mcp", "denied"
    if method == "tools/list":
        tools = _announced_tools(access_key, hass, data)
        resp = _jsonrpc_result(msg_id, {"tools": tools})
        data.announced_tools[access_key.id] = frozenset(tool["name"] for tool in tools)
        # After this response the client's tool list is current: echo the settings
        # version (in memory; persisted by the periodic flush, like last_used_at)
        # and clear the advised marker so a later change advises again.
        access_key.tools_list_version = access_key.settings_version
        access_key.tools_catalog_fingerprint = _TOOL_CATALOG_FINGERPRINT
        data.stale_tools_advised.pop(access_key.id, None)
        _log(data, access_key, request_id=request_id, method="tools/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "tools/list", "/api/phoenix-mcp", "allowed"

    if method == "tools/call":
        return await _dispatch_tools_call(
            params, msg_id, access_key, hass, data, request_id, client_ip,
        )

    if method == "resources/list":
        resp = _jsonrpc_result(msg_id, {
            "resources": [
                {
                    "uri": "homeassistant://assist/context-snapshot",
                    "name": "Assist Context Snapshot",
                    "description": "A snapshot of the current Assist context, matching the existing homeassistant__GetLiveContext tool output",
                    "mimeType": "text/plain",
                },
                {
                    "uri": "phx://server-info",
                    "name": "Phoenix MCP Server Info",
                    "mimeType": "application/json",
                },
            ]
        })
        _log(data, access_key, request_id=request_id, method="resources/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "resources/list", "/api/phoenix-mcp", "allowed"

    if method == "resources/templates/list":
        # Phoenix's two resources have fixed URIs, so there are no parameterized
        # resource templates to advertise. Clients routinely probe this method
        # whenever a server advertises resource support; an empty catalog is the
        # protocol answer, not Method not found.
        resp = _jsonrpc_result(msg_id, {"resourceTemplates": []})
        _log(data, access_key, request_id=request_id, method="resources/templates/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "resources/templates/list", "/api/phoenix-mcp", "allowed"

    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "homeassistant://assist/context-snapshot":
            context_text = _build_live_context(access_key, hass)
            resp = _jsonrpc_result(msg_id, {
                "contents": [{
                    "uri": "homeassistant://assist/context-snapshot",
                    "mimeType": "text/plain",
                    "text": context_text,
                }]
            })
            _log(data, access_key, request_id=request_id, method="resources/read",
                 resource="homeassistant://assist/context-snapshot", outcome="allowed", client_ip=client_ip)
            return resp, "resources/read", "homeassistant://assist/context-snapshot", "allowed"
        if uri != "phx://server-info":
            if msg_id is not None:
                _log(data, access_key, request_id=request_id, method="resources/read",
                     resource=uri or "/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32002, "Unknown resource URI."), "resources/read", uri, "invalid_request"
            return None, "resources/read", uri, "denied"
        server_info = _build_server_info(access_key, hass, base_url)
        resp = _jsonrpc_result(msg_id, {
            "contents": [{
                "uri": "phx://server-info",
                "mimeType": "application/json",
                "text": json.dumps(server_info, default=str),
            }]
        })
        _log(data, access_key, request_id=request_id, method="resources/read",
             resource="phx://server-info", outcome="allowed", client_ip=client_ip)
        return resp, "resources/read", "phx://server-info", "allowed"

    if method == "prompts/list":
        if access_key.pass_through:
            try:
                api_inst = await _get_ha_assist_api(hass)
                prompt_name = f"Default prompt for Home Assistant {api_inst.api.name}"
                prompts = [{"name": prompt_name, "description": f"Default prompt for Home Assistant {api_inst.api.name} API"}]
            except Exception:
                # Never silent: an empty prompt list is a valid answer, so nothing
                # would otherwise distinguish "no prompts" from "the Assist lookup
                # is broken" (as when HA removed LLMContext.user_prompt). Degrading
                # is still right (a missing prompt must not fail the request), but
                # the operator gets a traceback for it.
                _LOGGER.exception("prompts/list: the HA Assist API lookup failed")
                prompts = []
        else:
            prompts = [{
                "name": "Phoenix MCP access context",
                "description": "Describes the Home Assistant entities and capabilities accessible to this access key",
            }]
        resp = _jsonrpc_result(msg_id, {"prompts": prompts})
        _log(data, access_key, request_id=request_id, method="prompts/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "prompts/list", "/api/phoenix-mcp", "allowed"

    if method == "prompts/get":
        name = params.get("name", "")
        if access_key.pass_through:
            try:
                api_inst = await _get_ha_assist_api(hass)
                expected_name = f"Default prompt for Home Assistant {api_inst.api.name}"
                if name != expected_name:
                    _log(data, access_key, request_id=request_id, method="prompts/get",
                         resource="/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                    return _jsonrpc_error(msg_id, -32602, "Unknown prompt."), "prompts/get", "/api/phoenix-mcp", "denied"
                resp = _jsonrpc_result(msg_id, {
                    "description": f"Default prompt for Home Assistant {api_inst.api.name} API",
                    "messages": [{"role": "user", "content": {"type": "text",
                        "text": _UNTRUSTED_DATA_BOUNDARY + "\n\n" + api_inst.api_prompt}}],
                })
            except Exception:
                # See prompts/list above: "Prompt unavailable" reads as a normal
                # absence, so without this the failure that produced it is
                # invisible. The audit outcome stays denied because that is what
                # the caller was told; the traceback is for the operator.
                _LOGGER.exception("prompts/get: the HA Assist API lookup failed")
                _log(data, access_key, request_id=request_id, method="prompts/get",
                     resource="/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32603, "Prompt unavailable."), "prompts/get", "/api/phoenix-mcp", "denied"
        else:
            if name != "Phoenix MCP access context":
                _log(data, access_key, request_id=request_id, method="prompts/get",
                     resource="/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32602, "Unknown prompt."), "prompts/get", "/api/phoenix-mcp", "denied"
            prompt_text = _UNTRUSTED_DATA_BOUNDARY + "\n\n" + _build_context_plain(access_key, hass)
            resp = _jsonrpc_result(msg_id, {
                "description": "Describes the Home Assistant entities and capabilities accessible to this access key",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            })
        _log(data, access_key, request_id=request_id, method="prompts/get",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "prompts/get", "/api/phoenix-mcp", "allowed"

    if msg_id is not None:
        _log(data, access_key, request_id=request_id, method=method or "unknown",
             resource="/api/phoenix-mcp", outcome="not_implemented", client_ip=client_ip)
        return _jsonrpc_error(msg_id, -32601, "Method not found."), method or "unknown", "/api/phoenix-mcp", "not_implemented"

    return None, method or "unknown", "/api/phoenix-mcp", "not_implemented"
