"""Fix a Home Assistant repair issue through its integration's own fix flow, after approval.

A fix flow runs the integration's code, so what it changes cannot be known in advance. The key
needs cap_integration_reconfigure and write scope over every config entry of the issue's
integration and of the integration it concerns; an issue from an integration with no config
entry, such as Core's own, is refused. Every fix waits for an administrator whatever the
capability's mode, and the approval is bound to the issue and each entry's registry context.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from ..access_key_store import AccessKeyRecord
from ..capability import effective_cap
from ..catalog_text import diff_summary_fields as _summary
from ..const import CAP_DENY, DOMAIN
from ..data import PhoenixData
from ..mesa import fire_mesa_blocked_event
from ..redaction import redact_structure
from ..request_authority import _approved_exec_ctx, write_boundary
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _gate, _tool_error, _tool_success
from .discovery import _repair_ref, _safe_repair_text
from .flow_driver import async_drive_flow
from .integration_reconfigure import MENU_CHOICE_BUDGET, _result_type
from .integrations import (
    _ConfigEntryActionDecision, _config_entry_action_decision, _config_entry_mesa_error,
    _config_entry_mesa_preview, _config_entry_write_error,
)

_LOGGER = logging.getLogger(__name__)

_TOOL = "fix_repair"
_CAP = "cap_integration_reconfigure"
_BINDING = "_repair_fix_binding"
_NOT_FOUND = "Repair not found."
_DESCRIPTION_MAX = 600


def _args_error(args: dict[str, Any]) -> str | None:
    ref = args.get("repair_ref")
    config = args.get("config", {})
    menu_choices = args.get("menu_choices", [])
    if not isinstance(ref, str) or not ref.startswith("repair_"):
        return "repair_ref is required, as get_repairs returns it."
    if not isinstance(config, dict) or any(not isinstance(key, str) or not key for key in config):
        return "config must be an object of form field names to values."
    if not isinstance(menu_choices, list) or any(not isinstance(item, str) or not item for item in menu_choices):
        return "menu_choices must be an array of non-empty strings."
    if len(menu_choices) > MENU_CHOICE_BUDGET:
        return f"menu_choices supports at most {MENU_CHOICE_BUDGET} choices."
    return None


def _issue_for(hass: HomeAssistant, access_key: AccessKeyRecord, ref: str) -> ir.IssueEntry | None:
    """The active, shown issue this key's reference names."""
    registry = ir.async_get(hass)
    hidden = getattr(registry, "async_is_hidden", lambda _issue: False)
    return next((
        issue for issue in registry.issues.values()
        if issue.active and not hidden(issue) and _repair_ref(access_key, issue.domain, issue.issue_id) == ref
    ), None)


def _resolve(
    hass: HomeAssistant, access_key: AccessKeyRecord, data: PhoenixData, ref: str, session_id: str,
) -> tuple[ir.IssueEntry, list[Any], list[_ConfigEntryActionDecision], str] | tuple[dict, str, str]:
    """The issue, its entries and their decisions, and the approval binding; or a refusal."""
    issue = _issue_for(hass, access_key, ref)
    if issue is None:
        return _tool_error(_NOT_FOUND), "not_found", _TOOL
    if not issue.is_fixable:
        return _tool_error(
            "This repair has no fix flow. Follow its instructions in Home Assistant's Repairs."
        ), "invalid_request", _TOOL
    domains = sorted({issue.domain} | ({issue.issue_domain} if isinstance(issue.issue_domain, str) else set()))
    entries = []
    for domain in domains:
        found = hass.config_entries.async_entries(domain)
        if domain == DOMAIN or not found:
            return _tool_error(
                "This repair belongs to Home Assistant itself or to an integration set up without a "
                "config entry, which no access key's scope covers. Fix it in Home Assistant's Repairs."
            ), "denied", _TOOL
        entries.extend(found)
    decisions = []
    for entry in entries:
        if (refusal := _config_entry_write_error(entry.entry_id, access_key, hass)) is not None:
            return _tool_error(
                "This repair's integration is outside this access key's write scope."
            ), refusal[1], _TOOL
        decision = _config_entry_action_decision(
            data, access_key, hass, entry, actions=["repair.fix"],
            service_data={"repair_ref": ref}, session_id=session_id)
        if isinstance(decision, tuple):
            return decision
        decisions.append(decision)
    identity = {
        "issue": [issue.domain, issue.issue_id, issue.issue_domain, issue.translation_key,
                  issue.created.isoformat(), json.dumps(issue.data or {}, sort_keys=True, default=str)],
        "entries": [[entry.entry_id, decision.fingerprint] for entry, decision in zip(entries, decisions)],
    }
    binding = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return issue, entries, decisions, binding


async def _issue_text(hass: HomeAssistant, issue: ir.IssueEntry, access_key: AccessKeyRecord) -> tuple[str, str]:
    """The issue's English title and description from Core's translations, scrubbed for this key."""
    try:
        strings = await async_get_translations(hass, "en", "issues", {issue.domain})
    except Exception:  # text is documentation, never required
        strings = {}
    prefix = f"component.{issue.domain}.issues.{issue.translation_key}."
    placeholders = issue.translation_placeholders or {}

    def render(text: str | None) -> str:
        if not text:
            return ""
        for name, value in placeholders.items():
            text = text.replace("{" + name + "}", str(value))
        return str(_safe_repair_text(text, access_key, hass))[:_DESCRIPTION_MAX]

    title = render(strings.get(prefix + "title")) or f"{issue.domain}: {issue.translation_key}"
    return title, render(strings.get(prefix + "description"))


async def _build_diff(
    args: dict[str, Any], issue: ir.IssueEntry, entries: list[Any],
    decisions: list[_ConfigEntryActionDecision], access_key: AccessKeyRecord, hass: HomeAssistant,
) -> dict[str, Any]:
    title, description = await _issue_text(hass, issue, access_key)
    config = args.get("config") or {}
    return {
        "kind": "system_action",
        **_summary("repair.fix", label=title),
        "target": {"type": "repair", "id": args["repair_ref"], "label": title},
        "preview": {
            "issue": {
                "domain": issue.domain, "title": title, "description": description,
                "severity": str(issue.severity) if issue.severity is not None else None,
            },
            "integrations": [
                {"domain": entry.domain, "name": _safe_repair_text(entry.title, access_key, hass)} for entry in entries
            ],
            "submitted_fields": sorted(config),
            "submitted_config": redact_structure(config),
            "menu_choices": list(args.get("menu_choices") or []),
            "fix_effect": "decided_by_the_integration",
            "automatic_rollback": False,
            "mesa": [_config_entry_mesa_preview(decision, admin=True) for decision in decisions],
        },
    }


async def _tool_fix_repair(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: queue a repair issue's own fix flow, with the agent's answers, for approval."""
    if effective_cap(access_key, _CAP) == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", _TOOL
    if (error := _args_error(args)) is not None:
        return _tool_error(error), "invalid_request", _TOOL
    resolved = _resolve(hass, access_key, data, args["repair_ref"], request_id or _TOOL)
    if len(resolved) == 3:
        return resolved
    issue, entries, decisions, binding = resolved
    for entry, decision in zip(entries, decisions):
        if decision.decision == "deny":
            if decision.blocked:
                fire_mesa_blocked_event(hass, access_key, decision.blocked)
            return _config_entry_mesa_error(entry.entry_id, decision)
    approval_args = {**args, _BINDING: binding}
    blocked = await _gate(
        _CAP, access_key, hass, data, tool_name=_TOOL, args=approval_args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff(args, issue, entries, decisions, access_key, hass),
        review=True,
    )
    if blocked is not None:
        return blocked
    return await _execute_fix_repair(approval_args, access_key, hass, data)


@write_boundary
async def _execute_fix_repair(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Run an approved fix once, after checking that nothing it was bound to has changed."""
    if not _approved_exec_ctx.get():
        return _tool_error("A repair fix runs only after an administrator approves it."), "denied", _TOOL
    if (error := _args_error(args)) is not None:
        return _tool_error(error), "invalid_request", _TOOL
    resolved = _resolve(hass, access_key, data, args["repair_ref"], "fix_repair_execute")
    if len(resolved) == 3:
        return resolved
    issue, entries, decisions, binding = resolved
    if binding != args.get(_BINDING):
        return _tool_error(
            "The repair, its integration, or this access key's access changed after approval. "
            "Nothing was run; ask for a new review."
        ), "denied", _TOOL
    if any(decision.decision == "deny" for decision in decisions):
        return _tool_error("MESA now blocks this repair's integration. Nothing was run."), "denied", _TOOL
    manager = repairs_flow_manager(hass)
    if manager is None:
        return _tool_error("Home Assistant's Repairs integration is not loaded."), "invalid_request", _TOOL
    outcome = await async_drive_flow(
        manager,
        # Current releases read the issue from the context; earlier ones from the data.
        lambda: manager.async_init(issue.domain, context={"issue_id": issue.issue_id},
                                   data={"issue_id": issue.issue_id}),
        args.get("config") or {}, list(args.get("menu_choices") or []),
        lambda result: _result_type(result) is FlowResultType.CREATE_ENTRY,
    )
    remaining = ir.async_get(hass).async_get_issue(issue.domain, issue.issue_id) is not None
    status = ("fixed" if outcome.finished and not remaining
              else "finished_issue_remains" if outcome.finished
              else "not_finished_may_have_changed" if outcome.submitted else "not_started")
    body = {
        "status": status,
        "repair_ref": args["repair_ref"],
        "domain": issue.domain,
        "issue_resolved": not remaining,
        "retry_safe": not outcome.submitted,
        "reason": outcome.reason,
        "details": redact_structure(_safe_details(outcome.details, access_key, hass)),
        "automatic_rollback": False,
    }
    ok = outcome.finished
    return (_tool_success if ok else _tool_error)(json.dumps(body, default=str)), \
        "allowed" if ok else "invalid_request", _TOOL


def _safe_details(value: Any, access_key: AccessKeyRecord, hass: HomeAssistant, depth: int = 0) -> Any:
    """A flow's details with every string scrubbed for this key."""
    if depth > 8:
        return "<redacted>"
    if isinstance(value, str):
        return _safe_repair_text(value, access_key, hass)
    if isinstance(value, dict):
        return {str(key): _safe_details(item, access_key, hass, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_details(item, access_key, hass, depth + 1) for item in value]
    return value
