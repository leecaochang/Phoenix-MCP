"""Add or reconfigure a config subentry, such as an AI integration's agent, after approval.

Core's own subentry flow validates and applies the agent's values. A subentry can create an
agent and choose which Home Assistant tools it gets (an AI agent's llm_hass_api) or what its
prompt says, so every write waits for an administrator whatever the capability's mode. The key
needs cap_integration_reconfigure and write scope over the parent entry, the same as
reconfigure_integration, and the approval is bound to the entry's identity and, for a
reconfigure, to the subentry's reviewed settings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.loader import async_get_integration

from ..access_key_store import AccessKeyRecord
from ..capability import effective_cap
from ..catalog_text import diff_summary_fields as _summary
from ..catalog_text import version_summary_fields as _version_summary
from ..const import CAP_DENY, DOMAIN
from ..data import PhoenixData
from ..mesa import fire_mesa_blocked_event
from ..redaction import redact_diagnostics, redact_structure
from ..request_authority import _approved_exec_ctx, write_boundary
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _gate, _record_version, _tool_error, _tool_success
from .flow_driver import async_drive_flow
from .integration_reconfigure import MENU_CHOICE_BUDGET, RECONFIGURE_SUCCESS_REASON, _result_type
from .integrations import (
    _ConfigEntryActionDecision, _config_entry_action_decision, _config_entry_mesa_error,
    _config_entry_mesa_preview, _config_entry_private_identity, _config_entry_write_error,
    _reconfigure_identity_error, _reconfigure_shared_owner_error,
)

_TOOL = "set_integration_subentry"
_CAP = "cap_integration_reconfigure"
_BINDING = "_subentry_binding"
_NOT_FOUND = "Integration not found."


def _args_error(args: dict[str, Any]) -> str | None:
    entry_id, kind, subentry_id = args.get("entry_id"), args.get("subentry_type"), args.get("subentry_id")
    config, menu_choices = args.get("config", {}), args.get("menu_choices", [])
    if not isinstance(entry_id, str) or not entry_id.strip():
        return "entry_id is required."
    if (kind is None) == (subentry_id is None):
        return "Pass subentry_type to add a subentry, or subentry_id to reconfigure one, not both."
    if not isinstance(kind if kind is not None else subentry_id, str) or not (kind or subentry_id):
        return "subentry_type and subentry_id must be non-empty strings."
    if not isinstance(config, dict) or any(not isinstance(key, str) or not key for key in config):
        return "config must be an object of form field names to values."
    if not isinstance(menu_choices, list) or any(not isinstance(item, str) or not item for item in menu_choices):
        return "menu_choices must be an array of non-empty strings."
    if len(menu_choices) > MENU_CHOICE_BUDGET:
        return f"menu_choices supports at most {MENU_CHOICE_BUDGET} choices."
    return None


async def _supported_types(hass: HomeAssistant, entry: Any) -> dict[str, dict[str, bool]]:
    """The subentry types the entry's handler offers; the handler is loaded first."""
    try:
        await (await async_get_integration(hass, entry.domain)).async_get_platform("config_flow")
        return dict(entry.supported_subentry_types)
    except Exception:  # an integration without a config flow offers no subentries
        return {}


async def _resolve(
    hass: HomeAssistant, access_key: AccessKeyRecord, data: PhoenixData, args: dict[str, Any], session_id: str,
) -> tuple[Any, Any, str, _ConfigEntryActionDecision, str] | tuple[dict, str, str]:
    """The entry, the subentry being changed (or None), its type, the decision and the binding."""
    entry_id = str(args["entry_id"]).strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error(_NOT_FOUND), "not_found", entry_id
    if (refusal := _config_entry_write_error(entry_id, access_key, hass)) is not None:
        return refusal
    identity = _config_entry_private_identity(entry, hass)
    if identity is None:
        return _reconfigure_identity_error(entry_id)
    if identity.same_domain_coowners:
        return _reconfigure_shared_owner_error(entry_id, identity)
    supported = await _supported_types(hass, entry)
    subentry = None
    if args.get("subentry_id") is not None:
        subentry = entry.subentries.get(args["subentry_id"])
        if subentry is None:
            return _tool_error("Subentry not found."), "not_found", entry_id
        kind = subentry.subentry_type
        if not supported.get(kind, {}).get("supports_reconfigure"):
            return _tool_error(f"This integration cannot reconfigure a {kind} subentry."), "invalid_request", entry_id
    else:
        kind = args["subentry_type"]
        if kind not in supported:
            offered = ", ".join(sorted(supported)) or "none"
            return _tool_error(f"This integration does not offer a {kind} subentry. Offered: {offered}."), \
                "invalid_request", entry_id
    action = "config_subentry.reconfigure" if subentry is not None else "config_subentry.create"
    decision = _config_entry_action_decision(
        data, access_key, hass, entry, actions=[action],
        service_data={"subentry_type": kind, "submitted_fields": sorted(args.get("config") or {})},
        session_id=session_id)
    if isinstance(decision, tuple):
        return decision
    pinned = {
        "entry": [entry.entry_id, identity.binding_fingerprint, identity.stable_fingerprint, decision.fingerprint],
        "subentry": None if subentry is None else [
            subentry.subentry_id, json.dumps(dict(subentry.data), sort_keys=True, default=str)],
    }
    binding = hashlib.sha256(json.dumps(pinned, sort_keys=True).encode()).hexdigest()
    return entry, subentry, kind, decision, binding


async def _build_diff(
    args: dict[str, Any], entry: Any, subentry: Any, kind: str, decision: _ConfigEntryActionDecision,
) -> dict[str, Any]:
    label = f"{entry.domain} ({redact_diagnostics(entry.title)})"
    config = args.get("config") or {}
    preview: dict[str, Any] = {
        "integration": {"domain": entry.domain, "name": label},
        "subentry_type": kind,
        "submitted_fields": sorted(config),
        "submitted_config": redact_structure(config),
        "menu_choices": list(args.get("menu_choices") or []),
        "validation_state": "not_yet_validated_by_integration",
        "automatic_rollback": False,
        "mesa": _config_entry_mesa_preview(decision, admin=True),
    }
    if "llm_hass_api" in config:
        preview["agent_tools"] = config["llm_hass_api"]
    if subentry is not None:
        preview["subentry"] = {"subentry_id": subentry.subentry_id, "title": redact_diagnostics(subentry.title)}
        preview["current_settings"] = redact_structure(dict(subentry.data))
    summary = (_summary("integration.subentry.reconfigure", label=label, subject=redact_diagnostics(subentry.title))
               if subentry is not None else _summary("integration.subentry.create", label=label, subject=kind))
    return {
        "kind": "system_action",
        **summary,
        "target": {"type": "integration", "id": entry.entry_id, "label": label},
        "preview": preview,
    }


async def _tool_set_integration_subentry(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: queue adding or reconfiguring a config subentry through Core's flow, for approval."""
    if effective_cap(access_key, _CAP) == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", _TOOL
    if (error := _args_error(args)) is not None:
        return _tool_error(error), "invalid_request", _TOOL
    resolved = await _resolve(hass, access_key, data, args, request_id or _TOOL)
    if len(resolved) == 3:
        return resolved
    entry, subentry, kind, decision, binding = resolved
    if decision.decision == "deny":
        if decision.blocked:
            fire_mesa_blocked_event(hass, access_key, decision.blocked)
        return _config_entry_mesa_error(entry.entry_id, decision)
    approval_args = {**args, _BINDING: binding}
    blocked = await _gate(
        _CAP, access_key, hass, data, tool_name=_TOOL, args=approval_args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff(args, entry, subentry, kind, decision), review=True,
    )
    if blocked is not None:
        return blocked
    return await _execute_set_integration_subentry(approval_args, access_key, hass, data)


@write_boundary
async def _execute_set_integration_subentry(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Run an approved subentry flow once, after checking that nothing it was bound to changed."""
    if not _approved_exec_ctx.get():
        return _tool_error("A subentry change runs only after an administrator approves it."), "denied", _TOOL
    if (error := _args_error(args)) is not None:
        return _tool_error(error), "invalid_request", _TOOL
    resolved = await _resolve(hass, access_key, data, args, "set_integration_subentry_execute")
    if len(resolved) == 3:
        return resolved
    entry, subentry, kind, decision, binding = resolved
    if binding != args.get(_BINDING):
        return _tool_error(
            "The integration, the subentry, or this access key's access changed after approval. "
            "Nothing was run; ask for a new review."
        ), "denied", _TOOL
    if decision.decision == "deny":
        return _tool_error("MESA now blocks this integration. Nothing was run."), "denied", _TOOL
    manager = hass.config_entries.subentries
    before = set(entry.subentries)
    if subentry is None:
        def start() -> Any:
            return manager.async_init((entry.entry_id, kind), context={"source": SOURCE_USER})

        def success(result: Any) -> bool:
            return _result_type(result) is FlowResultType.CREATE_ENTRY
    else:
        def start() -> Any:
            return manager.async_init(
                (entry.entry_id, kind), context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id})

        def success(result: Any) -> bool:
            return _result_type(result) is FlowResultType.ABORT and result.get("reason") == RECONFIGURE_SUCCESS_REASON
    outcome = await async_drive_flow(
        manager, start, args.get("config") or {}, list(args.get("menu_choices") or []), success)
    current = hass.config_entries.async_get_entry(entry.entry_id)
    added = sorted(set(current.subentries) - before) if current is not None else []
    body: dict[str, Any] = {
        "status": "applied" if outcome.finished else "not_finished_may_have_changed" if outcome.submitted else "not_started",
        "entry_id": entry.entry_id,
        "subentry_type": kind,
        "applied": outcome.finished,
        "retry_safe": not outcome.submitted,
        "reason": outcome.reason,
        "details": redact_structure(dict(outcome.details)),
        "automatic_rollback": False,
    }
    if subentry is not None:
        body["subentry_id"] = subentry.subentry_id
    elif added:
        body["subentry_id"] = added[0]
    if outcome.finished:
        title = redact_diagnostics(subentry.title) if subentry is not None else kind
        await _record_version(
            data, access_key, resource_type="config_entry", resource_id=entry.entry_id, action="edit",
            before=None,
            after={"snapshot_type": "phoenix.config_subentry.v1", "restorable": False, "entry_id": entry.entry_id,
                   "domain": entry.domain, "subentry_type": kind, "subentry_id": body.get("subentry_id"),
                   "submitted_fields": sorted(args.get("config") or {}),
                   "submitted_config": redact_structure(args.get("config") or {})},
            alias=redact_diagnostics(entry.title),
            summary=_version_summary(
                "config_subentry.reconfigure" if subentry is not None else "config_subentry.create", subject=title),
        )
        return _tool_success(json.dumps(body, default=str)), "allowed", f"integration:{entry.entry_id}"
    return _tool_error(json.dumps(body, default=str)), "invalid_request", entry.entry_id
