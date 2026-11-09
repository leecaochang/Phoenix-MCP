"""Integration listing, reload, reconfigure, enable and removal tools."""

from __future__ import annotations

import dataclasses
from datetime import datetime
import hashlib
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState, support_entry_unload
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..access_key_store import AccessKeyRecord
from ..config_recovery import ConfigurationRecoveryError
from ..const import (
    CAP_DENY, DOMAIN, INTEGRATION_LIST_DEFAULT_LIMIT, INTEGRATION_LIST_MAX_LIMIT, MESA_MODE_OFF,
)
from ..data import PhoenixData
from ..device_semantics import device_entry_value, device_registry_snapshot
from ..catalog_text import (
    diff_summary_fields as _summary, version_summary_fields as _version_summary,
)
from ..capability import effective_cap
from ..redaction import _scrub_log_text, redact_diagnostics, redact_structure
from ..mesa import (
    evaluate_registry_action, fire_mesa_blocked_event, unresolved_context_refused,
    UNRESOLVED_CONTEXT_WARNING,
)
from ..policy_engine import (
    assist_expose_check, config_entry_registry_context, device_config_entry_ids, Permission,
    resolve_config_entry_registry_access, resolve_config_entry_registry_write,
    resolve_device_registry_access, resolve_registry_access,
)
from ..proxy_diagnostics import discovered_flows, full_pass_through, integration_serial_ports
from ..template_snapshots import template_snapshot, protect_template
from ..tool_common import (
    _add_mesa_advisory, _CAP_FORBIDDEN_MESSAGE, _create_registry_mesa_approval, _gate, _record_version, _restore_ctx, _tool_error, _tool_success, _tool_uncertain, fitted_offset_page, offset_page,
)
from ..request_authority import _approved_exec_ctx, write_boundary
from ..tool_contracts import normalize_tool_args
from .discovery import _registry_relationships_preview
from .integration_reconfigure import (
    async_run_reconfigure_flow, MENU_CHOICE_BUDGET,
    STATUS_APPLY_FAILED as RECONFIGURE_APPLY_FAILED,
)
from .registry import _contains_entity_identity


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Integration registry lifecycle (cap_integration_write)
# ---------------------------------------------------------------------------


_CONFIG_ENTRY_CONTEXT_FINGERPRINT = "_config_entry_context_fingerprint"
_CONFIG_ENTRY_METADATA_SNAPSHOT = "phoenix.config_entry.metadata.v1"
_CONFIG_ENTRY_DELETE_SNAPSHOT = "phoenix.config_entry.delete.v1"
_CONFIG_ENTRY_RECONFIGURE_SNAPSHOT = "phoenix.config_entry.reconfigure.v1"
_CONFIG_ENTRY_PRIVATE_IDENTITY_FINGERPRINT = "_config_entry_private_identity_fingerprint"
_CONFIG_ENTRY_STABLE_IDENTITY_FINGERPRINT = "_config_entry_stable_identity_fingerprint"


@dataclasses.dataclass
class _ConfigEntryActionDecision:
    """Aggregate one config-entry action over its exact entity membership."""

    decision: str
    actions: list[str]
    entities: list[dict[str, Any]]
    warnings: list[str]
    fingerprint: str
    blocked: list[tuple[str, str, str]]


@dataclasses.dataclass(frozen=True)
class _ConfigEntryPrivateIdentity:
    """Private config-entry identity used only for approval/apply binding."""

    binding_fingerprint: str
    stable_fingerprint: str
    entities: list[dict[str, Any]]
    devices: list[dict[str, Any]]
    cross_domain_coowners: list[dict[str, str]]
    same_domain_coowners: list[dict[str, str]]


def _identity_fingerprint(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _config_entry_private_identity(
    entry: Any, hass: HomeAssistant
) -> _ConfigEntryPrivateIdentity | None:
    """Capture private identity, registry membership and shared ownership.

    No value here is returned through MCP or the admin API. The approval stores only hashes; the
    preview gets safe resource ids and co-owner domains separately.
    """
    context = config_entry_registry_context(entry.entry_id, hass)
    if context is None:
        return None
    if not isinstance(getattr(entry, "modified_at", None), datetime):
        return None
    entity_registry = er.async_get(hass)
    entities: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    same_domain: list[dict[str, str]] = []
    cross_domain: list[dict[str, str]] = []
    stable_anchor = bool(getattr(entry, "unique_id", None))

    for entity_id in context.entity_ids:
        registry_entry = entity_registry.async_get(entity_id)
        if registry_entry is None or registry_entry.config_entry_id != entry.entry_id:
            return None
        unique_id = getattr(registry_entry, "unique_id", None)
        stable_anchor = stable_anchor or bool(unique_id)
        entities.append({
            "entity_id": entity_id,
            "unique_id": unique_id,
            "platform": getattr(registry_entry, "platform", None),
            "device_id": getattr(registry_entry, "device_id", None),
        })

    for device_id in context.device_ids:
        device = device_registry_snapshot(hass).get(device_id)
        if device is None or entry.entry_id not in device_config_entry_ids(device):
            return None
        identifiers = sorted(
            (str(domain), str(identifier))
            for domain, identifier in getattr(device, "identifiers", set())
        )
        # A child device has no connections; reading the compatibility shim
        # would report usage that stops working in a later Core release.
        connections = sorted(
            (str(kind), str(value))
            for kind, value in device_entry_value(device, "connections", ()) or ()
        )
        stable_anchor = stable_anchor or bool(identifiers or connections)
        owners: list[dict[str, str]] = []
        for owner_id in sorted(device_config_entry_ids(device)):
            owner = hass.config_entries.async_get_entry(owner_id)
            if owner is None:
                return None
            owner_doc = {"entry_id": owner_id, "domain": owner.domain}
            owners.append(owner_doc)
            if owner_id != entry.entry_id:
                (same_domain if owner.domain == entry.domain else cross_domain).append({
                    "device_id": device_id,
                    **owner_doc,
                })
        devices.append({
            "device_id": device_id,
            "identifiers": identifiers,
            "connections": connections,
            "owners": owners,
        })

    # An entry with no stable private identity cannot be distinguished from a
    # replacement that reused its public id. Reconfiguration therefore fails
    # closed instead of asking the operator to approve an ambiguous target.
    if not stable_anchor:
        return None
    stable_document = {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "unique_id": getattr(entry, "unique_id", None),
        "entities": entities,
        "devices": devices,
    }
    binding_document = {
        **stable_document,
        "modified_at": getattr(entry, "modified_at", None),
    }
    return _ConfigEntryPrivateIdentity(
        binding_fingerprint=_identity_fingerprint(binding_document),
        stable_fingerprint=_identity_fingerprint(stable_document),
        entities=entities,
        devices=devices,
        cross_domain_coowners=cross_domain,
        same_domain_coowners=same_domain,
    )


def _config_entry_value(value: Any) -> str | None:
    """Return a stable public string for a Home Assistant enum-like value."""
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


async def _config_entry_support(
    entry: Any, hass: HomeAssistant
) -> tuple[bool | None, bool | None, bool | None, bool | None]:
    """Probe safe config-entry support metadata without false negatives."""
    supports_unload: bool | None
    try:
        supports_unload = getattr(entry, "supports_unload", None)
        if supports_unload is None:
            supports_unload = await support_entry_unload(hass, entry.domain)
        else:
            supports_unload = bool(supports_unload)
    except Exception:  # integration loaders are third-party boundaries
        supports_unload = None

    def _optional_support(public_name: str, cache_name: str) -> bool | None:
        try:
            value = bool(getattr(entry, public_name))
            cached = getattr(entry, cache_name, None)
            # HA's property returns False when its handler has not been loaded.
            # That is unknown, not evidence that the feature is unsupported.
            return None if cached is None and not value else value
        except Exception:
            return None

    supports_options = _optional_support("supports_options", "_supports_options")
    supports_reconfigure = _optional_support(
        "supports_reconfigure", "_supports_reconfigure"
    )
    try:
        if entry.disabled_by is not None:
            supports_reload: bool | None = False
        elif entry.state is ConfigEntryState.LOADED:
            supports_reload = supports_unload
        elif entry.state in (
            ConfigEntryState.NOT_LOADED,
            ConfigEntryState.SETUP_ERROR,
            ConfigEntryState.SETUP_RETRY,
            ConfigEntryState.FAILED_UNLOAD,
        ):
            supports_reload = True
        else:
            supports_reload = False
    except Exception:
        supports_reload = None
    return supports_reload, supports_unload, supports_options, supports_reconfigure


def _config_entry_metadata_snapshot(entry: Any) -> dict[str, Any]:
    """Safe, discriminated config-entry metadata for version restoration."""
    return {
        "snapshot_type": _CONFIG_ENTRY_METADATA_SNAPSHOT,
        "title": entry.title,
        "pref_disable_new_entities": bool(entry.pref_disable_new_entities),
        "pref_disable_polling": bool(entry.pref_disable_polling),
        "disabled_by": _config_entry_value(entry.disabled_by),
    }


def _config_entry_write_error(
    entry_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    force_registry_only: bool = False,
) -> tuple[dict, str, str] | None:
    """Require WRITE on all entities and devices the entry owns, else not found."""
    permission = resolve_config_entry_registry_write(
        entry_id, access_key, hass, force_registry_only=force_registry_only
    )
    if permission == Permission.WRITE:
        return None
    return _tool_error("Integration not found."), "not_found", entry_id


def _config_entry_action_decision(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    entry: Any,
    *,
    actions: list[str],
    service_data: dict[str, Any],
    session_id: str,
) -> _ConfigEntryActionDecision | tuple[dict, str, str]:
    """Resolve MESA and pin entry state plus exact resource membership."""
    context = config_entry_registry_context(entry.entry_id, hass)
    if context is None:
        return _tool_error("Integration not found."), "not_found", entry.entry_id
    mesa_active = (
        (data.mesa is not None or data.mesa_setup_failed is True)
        and data.store.get_settings().mesa_mode != MESA_MODE_OFF
    )
    entity_ids = sorted(context.entity_ids)
    if mesa_active and actions and not entity_ids and unresolved_context_refused(data):
        reason = (
            "MESA is active and this integration has no registry entity from "
            "which mesa-core can resolve a complete config-entry context."
        )
        blocked = [(entry.entry_id, "mesa:unresolved_config_entry_context", reason)]
        entities: list[dict[str, Any]] = []
        decision_name = "deny"
        warnings: list[str] = []
    else:
        entities = []
        blocked = []
        warnings = [UNRESOLVED_CONTEXT_WARNING] if mesa_active and actions and not entity_ids else []
        has_confirm = False
        try:
            for action in actions:
                for entity_id in entity_ids:
                    decision = evaluate_registry_action(
                        data,
                        access_key,
                        entity_id,
                        action=action,
                        registry_domain="config_entry",
                        service_data=service_data,
                        session_id=session_id,
                    )
                    explanation = None
                    if mesa_active and data.mesa is not None and decision.effective_rule is not None:
                        explanation = data.mesa.resolver.explain(entity_id).to_dict()
                    entities.append({
                        "entity_id": entity_id,
                        "action": action,
                        "decision": decision.decision,
                        "rule": decision.rule,
                        "reason": decision.reason,
                        "warnings": decision.warnings,
                        "effective_rule": decision.effective_rule,
                        "admin_rule": decision.admin_rule,
                        "explanation": explanation,
                    })
                    for warning in decision.warnings:
                        if warning not in warnings:
                            warnings.append(warning)
                    if decision.decision == "deny":
                        blocked.append((entity_id, decision.rule, decision.reason))
                    elif decision.decision == "confirm":
                        has_confirm = True
        except Exception:  # safety resolution must fail closed
            _LOGGER.exception(
                "MESA config-entry evaluation failed for %s", entry.entry_id
            )
            return (
                _tool_error(
                    "MESA safety evaluation failed; no integration change was made."
                ),
                "denied",
                entry.entry_id,
            )
        decision_name = "deny" if blocked else "confirm" if has_confirm else "allow"

    fingerprint_doc = {
        "entry_id": entry.entry_id,
        "entry": _config_entry_metadata_snapshot(entry),
        "state": _config_entry_value(entry.state),
        "entity_ids": entity_ids,
        "device_ids": sorted(context.device_ids),
        "actions": actions,
        "entities": entities,
        "blocked": blocked,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_doc,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return _ConfigEntryActionDecision(
        decision=decision_name,
        actions=actions,
        entities=entities,
        warnings=warnings,
        fingerprint=fingerprint,
        blocked=blocked,
    )


def _config_entry_mesa_preview(
    decision: _ConfigEntryActionDecision, *, admin: bool = False
) -> dict[str, Any]:
    """The decision for an agent's refusal, or with ``admin`` for an approval diff.

    The explanation repeats the stored reasons as written, so only the diff carries it.
    """
    return {
        "decision": decision.decision,
        "actions": decision.actions,
        "entities": [
            (
                {**{key: value for key, value in item.items() if key != "admin_rule"},
                 "effective_rule": item["admin_rule"]}
                if admin else
                {key: value for key, value in item.items() if key not in ("admin_rule", "explanation")}
            )
            for item in decision.entities
        ],
        "warnings": decision.warnings,
        "fingerprint": decision.fingerprint[:16],
    }


def _config_entry_mesa_error(
    entry_id: str, decision: _ConfigEntryActionDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(json.dumps({
            "error": "MESA blocked the integration action.",
            "entry_id": entry_id,
            "mesa": _config_entry_mesa_preview(decision),
            "blocked": [
                {"entity_id": eid, "rule": rule, "reason": reason}
                for eid, rule, reason in decision.blocked
            ],
        }, default=str)),
        "denied",
        entry_id,
    )


def _config_entry_context_changed_error(
    entry_id: str, decision: _ConfigEntryActionDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(json.dumps({
            "error": (
                "The integration's state, resource membership, permissions, or "
                "effective MESA profile changed after approval. Review it again."
            ),
            "entry_id": entry_id,
            "mesa": _config_entry_mesa_preview(decision),
        }, default=str)),
        "denied",
        entry_id,
    )


async def _integration_gate(
    *,
    tool_name: str,
    args: dict[str, Any],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    entry: Any,
    actions: list[str],
    service_data: dict[str, Any],
    request_id: str,
    client_ip: str | None,
    diff: Any,
    cap_name: str = "cap_integration_write",
    approval_bindings: dict[str, str] | None = None,
) -> tuple[dict, str, str] | None:
    """Merge capability and MESA confirmation into one pending approval."""
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=actions,
        service_data=service_data,
        session_id=request_id or tool_name,
    )
    if isinstance(resolved, tuple):
        return resolved
    if resolved.decision == "deny":
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry.entry_id, resolved)
    approval_args = dict(args)
    approval_args[_CONFIG_ENTRY_CONTEXT_FINGERPRINT] = resolved.fingerprint
    if approval_bindings:
        approval_args.update(approval_bindings)
    blocked = await _gate(
        cap_name,
        access_key,
        hass,
        data,
        tool_name=tool_name,
        args=approval_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda: diff(resolved),
    )
    if blocked is not None:
        return blocked
    if resolved.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name=tool_name,
            args=approval_args,
            diff=await diff(resolved),
            request_id=request_id,
            client_ip=client_ip,
            cap_name=cap_name,
        )
    return None


async def _tool_list_integrations(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list only config entries visible through owned resources."""
    if (
        effective_cap(access_key, "cap_integration_write") == CAP_DENY
        and effective_cap(access_key, "cap_integration_reconfigure") == CAP_DENY
    ):
        return _tool_error("Forbidden."), "denied", "list_integrations"
    domain_filter, query = args.get("domain"), args.get("query")
    if any(value is not None and not isinstance(value, str) for value in (domain_filter, query)):
        return _tool_error("domain and query must be strings."), "invalid_request", "list_integrations"
    domain_filter = (domain_filter or "").strip().lower()
    query = (query or "").strip().lower()
    integrations: list[dict[str, Any]] = []
    serial_usage = await integration_serial_ports(hass)
    for entry in hass.config_entries.async_entries():
        if domain_filter and entry.domain != domain_filter:
            continue
        access = resolve_config_entry_registry_access(entry.entry_id, access_key, hass)
        if access not in (Permission.READ, Permission.WRITE):
            continue
        context = config_entry_registry_context(entry.entry_id, hass)
        if context is None:
            continue
        expose = assist_expose_check(access_key, hass)
        accessible_entities = sum(
            (expose is None or expose(entity_id)) and
            resolve_registry_access(entity_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
            for entity_id in context.entity_ids
        )
        accessible_devices = sum(
            resolve_device_registry_access(device_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
            for device_id in context.device_ids
        )
        (
            supports_reload,
            supports_unload,
            supports_options,
            supports_reconfigure,
        ) = await _config_entry_support(entry, hass)
        reason = getattr(entry, "reason", None)
        row = {
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            # Titles are user/integration supplied and commonly contain an IP,
            # hostname URL, or both.  Keep the public title field useful while
            # applying the same network-topology scrub as setup diagnostics.
            "title": redact_diagnostics(entry.title),
            "source": _config_entry_value(entry.source),
            "state": _config_entry_value(entry.state),
            "enabled": entry.disabled_by is None,
            "disabled_by": _config_entry_value(entry.disabled_by),
            "setup_failure_reason": (
                redact_diagnostics(str(reason)) if reason else None
            ),
            "supports_reload": supports_reload,
            "supports_unload": supports_unload,
            "supports_options": supports_options,
            "supports_reconfigure": supports_reconfigure,
            "pref_disable_new_entities": bool(entry.pref_disable_new_entities),
            "pref_disable_polling": bool(entry.pref_disable_polling),
            "accessible_entity_count": accessible_entities,
            "accessible_device_count": accessible_devices,
        }
        if serial_usage and entry.entry_id in serial_usage:
            row["serial_ports"] = serial_usage[entry.entry_id]
        integrations.append(row)
    if query:
        integrations = [row for row in integrations
                        if query in f"{row['domain']} {row['title'] or ''}".lower()]
    integrations.sort(key=lambda e: (e["domain"], e["title"] or ""))
    paged = offset_page(integrations, args, default_limit=INTEGRATION_LIST_DEFAULT_LIMIT, max_limit=INTEGRATION_LIST_MAX_LIMIT)
    if isinstance(paged, str):
        return _tool_error(paged), "invalid_request", "list_integrations"
    page, envelope = paged
    extra: dict[str, Any] = {}
    # HA's discovered-but-not-added list is HA-wide, so only a key that sees what
    # HA sees gets it, once, on the first page.
    if (not envelope.get("offset") and full_pass_through(access_key, hass)
            and effective_cap(access_key, "cap_integration_write") != CAP_DENY):
        discovered, omitted = discovered_flows(hass)
        discovered = [
            row for row in discovered
            if (not domain_filter or row["domain"] == domain_filter)
            and (not query or query in f"{row['domain']} {' '.join(str(v) for v in row['title_placeholders'].values())}".lower())
        ]
        extra = {"discovered": discovered, "discovered_omitted": omitted}
    body = fitted_offset_page(page, envelope, lambda rows, env: {**env, "integrations": rows, **extra})
    return _tool_success(json.dumps(body, default=str)), "allowed", "list_integrations"


def _reconfigure_args_error(args: dict[str, Any]) -> str | None:
    entry_id = args.get("entry_id")
    config = args.get("config")
    menu_choices = args.get("menu_choices", [])
    if not isinstance(entry_id, str) or not entry_id.strip():
        return "entry_id is required."
    if not isinstance(config, dict):
        return "config must be an object."
    if any(not isinstance(key, str) or not key for key in config):
        return "config field names must be non-empty strings."
    if not isinstance(menu_choices, list) or any(
        not isinstance(choice, str) or not choice for choice in menu_choices
    ):
        return "menu_choices must be an array of non-empty strings."
    if len(menu_choices) > MENU_CHOICE_BUDGET:
        return f"menu_choices supports at most {MENU_CHOICE_BUDGET} choices."
    return None


def _reconfigure_identity_error(entry_id: str) -> tuple[dict, str, str]:
    return (
        _tool_error(
            "Phoenix could not establish a stable private identity for this integration; "
            "no reconfigure flow was started. Use Home Assistant's frontend."
        ),
        "denied",
        entry_id,
    )


def _reconfigure_shared_owner_error(
    entry_id: str, identity: _ConfigEntryPrivateIdentity
) -> tuple[dict, str, str]:
    return (
        _tool_error(json.dumps({
            "status": "flow_aborted_before_apply",
            "error": (
                "A device is shared with another config entry from the same integration "
                "domain, so Phoenix cannot prove which entry the flow will reconfigure. "
                "Use Home Assistant's frontend."
            ),
            "same_domain_shared_ownership": identity.same_domain_coowners,
            "retry_safe": True,
        }, default=str)),
        "denied",
        entry_id,
    )


def _reconfigure_version_snapshot(
    entry: Any, args: dict[str, Any], status: str
) -> dict[str, Any]:
    """Redacted audit evidence; deliberately insufficient for restoration."""
    raw_config = args.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    return {
        "snapshot_type": _CONFIG_ENTRY_RECONFIGURE_SNAPSHOT,
        "restorable": False,
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "submitted_fields": sorted(config),
        "submitted_config": redact_structure(config),
        "menu_choice_count": len(args.get("menu_choices") or []),
        "status": status,
        "automatic_rollback": False,
    }


async def _build_diff_reconfigure_integration(
    args: dict[str, Any],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    identity = _config_entry_private_identity(entry, hass) if entry is not None else None
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    raw_config = args.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    preview: dict[str, Any] = {
        "submitted_fields": sorted(config),
        "submitted_config": redact_structure(config),
        "menu_choices": list(args.get("menu_choices") or []),
        "validation_state": "not_yet_validated_by_integration",
        "operator_editable": False,
        "unsupported_steps": ["external", "oauth", "progress"],
        "automatic_rollback": False,
    }
    if entry is not None:
        preview["integration"] = {"domain": entry.domain, "name": label}
    if identity is not None:
        preview["affected_entities"] = [
            {"entity_id": item["entity_id"], "device_id": item["device_id"]}
            for item in identity.entities
        ]
        preview["affected_devices"] = [
            {
                "device_id": item["device_id"],
                "owners": item["owners"],
            }
            for item in identity.devices
        ]
        preview["cross_domain_coowners"] = identity.cross_domain_coowners
        preview["identity_binding"] = "private_identity_and_registry_context_pinned"
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision, admin=True)
    return {
        "kind": "system_action",
        **_summary("integration.reconfigure", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        # There is intentionally no before/after config-entry data. The
        # integration has not validated the proposed values at review time.
        "preview": preview,
    }


async def _tool_reconfigure_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Review agent values, then run only HA's official reconfigure flow."""
    if effective_cap(access_key, "cap_integration_reconfigure") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "reconfigure_integration"
    value_error = _reconfigure_args_error(args)
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", "reconfigure_integration"
    entry_id = str(args["entry_id"]).strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    identity = _config_entry_private_identity(entry, hass)
    if identity is None:
        return _reconfigure_identity_error(entry_id)
    if identity.same_domain_coowners:
        return _reconfigure_shared_owner_error(entry_id, identity)
    _, _, _, supports_reconfigure = await _config_entry_support(entry, hass)
    if supports_reconfigure is False:
        return (
            _tool_error(
                "This integration does not expose Home Assistant's official reconfigure flow. "
                "Use Home Assistant's frontend if it offers another configuration path."
            ),
            "invalid_request",
            entry_id,
        )
    blocked = await _integration_gate(
        tool_name="reconfigure_integration",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=["config_entry.reconfigure"],
        service_data={"submitted_fields": sorted(args["config"])},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_reconfigure_integration(
            args, access_key, hass, decision
        ),
        cap_name="cap_integration_reconfigure",
        approval_bindings={
            _CONFIG_ENTRY_PRIVATE_IDENTITY_FINGERPRINT: identity.binding_fingerprint,
            _CONFIG_ENTRY_STABLE_IDENTITY_FINGERPRINT: identity.stable_fingerprint,
        },
    )
    if blocked is not None:
        return blocked
    return await _execute_reconfigure_integration(args, access_key, hass, data)


@write_boundary
async def _execute_reconfigure_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate approval context, execute, classify, and audit one flow."""
    value_error = _reconfigure_args_error(args)
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", "reconfigure_integration"
    entry_id = str(args["entry_id"]).strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    identity = _config_entry_private_identity(entry, hass)
    if identity is None:
        return _reconfigure_identity_error(entry_id)
    if identity.same_domain_coowners:
        return _reconfigure_shared_owner_error(entry_id, identity)

    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=["config_entry.reconfigure"],
        service_data={"submitted_fields": sorted(args["config"])},
        session_id="reconfigure_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    approved = bool(_approved_exec_ctx.get())
    saved_context = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    saved_binding = args.get(_CONFIG_ENTRY_PRIVATE_IDENTITY_FINGERPRINT)
    saved_stable = args.get(_CONFIG_ENTRY_STABLE_IDENTITY_FINGERPRINT)
    if approved and (
        saved_context != resolved.fingerprint
        or saved_binding != identity.binding_fingerprint
        or saved_stable != identity.stable_fingerprint
    ):
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (resolved.decision == "confirm" and not approved):
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry_id, resolved)

    flow_result = await async_run_reconfigure_flow(
        hass,
        entry,
        args["config"],
        list(args.get("menu_choices") or []),
    )
    status = flow_result.status
    if flow_result.applied:
        current = hass.config_entries.async_get_entry(entry_id)
        post_identity = (
            _config_entry_private_identity(current, hass) if current is not None else None
        )
        if post_identity is None or post_identity.stable_fingerprint != identity.stable_fingerprint:
            status = "applied_identity_mismatch"

    body: dict[str, Any] = {
        "status": status,
        "entry_id": entry_id,
        "domain": entry.domain,
        "applied": flow_result.applied,
        "retry_safe": not flow_result.applied,
        "reason": flow_result.reason,
        "details": redact_structure(flow_result.details),
        "automatic_rollback": False,
    }
    if identity.cross_domain_coowners:
        body["cross_domain_coowners"] = identity.cross_domain_coowners
        body["warning"] = (
            "One or more affected devices also belong to another integration domain."
        )
    if resolved.warnings:
        _add_mesa_advisory(body, resolved.warnings)
    if flow_result.applied:
        body["retry_warning"] = (
            "Do not automatically retry any applied_* result. Inspect Home Assistant "
            "and recover manually in its frontend if needed."
        )
        current = hass.config_entries.async_get_entry(entry_id) or entry
        await _record_version(
            data,
            access_key,
            resource_type="config_entry",
            resource_id=entry_id,
            action="edit",
            before=None,
            after=_reconfigure_version_snapshot(current, args, status),
            alias=redact_diagnostics(entry.title),
            summary=_version_summary(
                "config_entry.reconfigure", subject=redact_diagnostics(entry.title)
            ),
        )
        return (
            _tool_success(json.dumps(body, default=str)),
            "allowed",
            f"integration:{entry_id}",
        )
    return (
        _tool_error(json.dumps(body, default=str)),
        "denied" if status == RECONFIGURE_APPLY_FAILED else "invalid_request",
        entry_id,
    )


async def _tool_set_integration_enabled(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: enable or disable an integration (needs WRITE on all it owns)."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_enabled"
    entry_id = str(args.get("entry_id") or "").strip()
    enabled = args.get("enabled")
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "set_integration_enabled"
    if not isinstance(enabled, bool):
        return _tool_error("enabled must be a boolean."), "invalid_request", "set_integration_enabled"
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(
        entry_id, access_key, hass, force_registry_only=not enabled
    )
    if perm_error is not None:
        return perm_error
    if entry.disabled_by not in (None, ConfigEntryDisabler.USER):
        return (
            _tool_error(
                f"This integration is disabled by {_config_entry_value(entry.disabled_by)}, not by the user."
            ),
            "denied",
            entry_id,
        )
    if (entry.disabled_by is None) == enabled:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "enabled": enabled,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"
    action = "enable" if enabled else "disable"
    blocked = await _integration_gate(
        tool_name="set_integration_enabled",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=[action],
        service_data={"enabled": enabled},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_set_integration_enabled(
            args, access_key, hass, decision
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_integration_enabled(args, access_key, hass, data)


@write_boundary
async def _execute_set_integration_enabled(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    entry_id = str(args.get("entry_id") or "").strip()
    enabled = args.get("enabled")
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "set_integration_enabled"
    if not isinstance(enabled, bool):
        return _tool_error("enabled must be a boolean."), "invalid_request", "set_integration_enabled"
    entry = hass.config_entries.async_get_entry(entry_id)
    # Phoenix MCP's own entry is never a valid target (no self-lockout); treat as not found.
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(
        entry_id, access_key, hass, force_registry_only=not enabled
    )
    if perm_error is not None:
        return perm_error
    if entry.disabled_by not in (None, ConfigEntryDisabler.USER):
        return (
            _tool_error(
                f"This integration is disabled by {_config_entry_value(entry.disabled_by)}, not by the user."
            ),
            "denied",
            entry_id,
        )
    if (entry.disabled_by is None) == enabled:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "enabled": enabled,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"

    action = "enable" if enabled else "disable"
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=[action],
        service_data={"enabled": enabled},
        session_id="set_integration_enabled_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not approved_same
    ):
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry_id, resolved)
    before = _config_entry_metadata_snapshot(entry)
    try:
        reload_succeeded = await hass.config_entries.async_set_disabled_by(
            entry_id, None if enabled else ConfigEntryDisabler.USER
        )
    except Exception as exc:  # OperationNotAllowed etc. -> clean error
        _LOGGER.error("set_integration_enabled failed: %s", _scrub_log_text(str(exc)))
        return _tool_error("Failed to change integration state."), "denied", entry_id
    updated = hass.config_entries.async_get_entry(entry_id)
    if updated is None:
        return _tool_error("Integration not found after state change."), "denied", entry_id
    after = _config_entry_metadata_snapshot(updated)
    changed = before != after
    if changed:
        await _record_version(
            data,
            access_key,
            resource_type="config_entry",
            resource_id=entry_id,
            action="edit",
            before=before,
            after=after,
            alias=redact_diagnostics(updated.title),
        )
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "domain": updated.domain,
        "requested_enabled": enabled,
        "enabled": updated.disabled_by is None,
        "changed": changed,
        "reload_succeeded": bool(reload_succeeded),
        "requires_restart": not bool(reload_succeeded),
    }
    if resolved.warnings:
        _add_mesa_advisory(body, resolved.warnings)
    return _tool_success(json.dumps(body, default=str)), "allowed", f"integration:{entry_id}"


def _set_integration_args_error(args: dict[str, Any]) -> str | None:
    provided = [
        field
        for field in ("title", "pref_disable_new_entities", "pref_disable_polling")
        if field in args
    ]
    if not provided:
        return "Provide at least one integration metadata update."
    if "title" in args and not isinstance(args["title"], str):
        return "title must be a string."
    if isinstance(args.get("title"), str) and not args["title"].strip():
        return "title must not be empty."
    for field in ("pref_disable_new_entities", "pref_disable_polling"):
        if field in args and not isinstance(args[field], bool):
            return f"{field} must be a boolean."
    return None


def _set_integration_changes(entry: Any, args: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "title" in args and args["title"] != entry.title:
        changes["title"] = args["title"]
    for field in ("pref_disable_new_entities", "pref_disable_polling"):
        if field in args and args[field] != bool(getattr(entry, field)):
            changes[field] = args[field]
    return changes


async def _tool_set_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration"
    args, error = normalize_tool_args("set_integration", args)
    if error:
        return _tool_error(error), "invalid_request", "set_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "set_integration"
    value_error = _set_integration_args_error(args)
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", entry_id
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    changes = _set_integration_changes(entry, args)
    if not changes:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"
    actions = ["rename"] if "title" in changes else []
    blocked = await _integration_gate(
        tool_name="set_integration",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=actions,
        service_data={key: value for key, value in changes.items() if key == "title"},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_set_integration(args, access_key, hass, decision),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_integration(args, access_key, hass, data)


@write_boundary
async def _execute_set_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    restoring = _restore_ctx.get() is not None
    if not restoring:
        value_error = _set_integration_args_error(args)
        if value_error is not None:
            return _tool_error(value_error), "invalid_request", entry_id
    changes = _set_integration_changes(entry, args)
    if restoring and "disabled_by" in args:
        stored_disabled_by = args["disabled_by"]
        if stored_disabled_by not in (None, ConfigEntryDisabler.USER.value):
            return _tool_error("Stored disabled state is not user-controlled."), "denied", entry_id
        desired_enabled = stored_disabled_by is None
        if (entry.disabled_by is None) != desired_enabled:
            changes["disabled_by"] = stored_disabled_by
    if not changes:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"

    actions: list[str] = []
    if "title" in changes:
        actions.append("rename")
    if "disabled_by" in changes:
        actions.append("enable" if changes["disabled_by"] is None else "disable")
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=actions,
        service_data={key: value for key, value in changes.items() if key in ("title", "disabled_by")},
        session_id="set_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    # The Changes-tab restore is itself an explicit, authenticated admin
    # confirmation and runs synchronously after this fresh MESA resolution.
    # It may satisfy only `confirm`; deny verdicts (including read_only and
    # enforced prohibited) still fail below.  Normal MCP execution continues to
    # require the exact approval fingerprint captured in its preview.
    mesa_confirmed = approved_same or restoring
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not mesa_confirmed
    ):
        return _config_entry_mesa_error(entry_id, resolved)

    before = _config_entry_metadata_snapshot(entry)
    update_values = {
        key: changes[key]
        for key in ("title", "pref_disable_new_entities", "pref_disable_polling")
        if key in changes
    }
    try:
        if update_values:
            hass.config_entries.async_update_entry(entry, **update_values)
        reload_attempted = False
        reload_succeeded: bool | None = None
        if "disabled_by" in changes:
            reload_attempted = True
            reload_succeeded = await hass.config_entries.async_set_disabled_by(
                entry_id,
                None if changes["disabled_by"] is None else ConfigEntryDisabler.USER,
            )
        elif "pref_disable_polling" in changes and entry.state is ConfigEntryState.LOADED:
            reload_attempted = True
            reload_succeeded = await hass.config_entries.async_reload(entry_id)
    except Exception as exc:
        _LOGGER.error("set_integration failed for %s: %s", entry_id, _scrub_log_text(str(exc)))
        return _tool_error("Failed to update integration."), "denied", entry_id
    updated = hass.config_entries.async_get_entry(entry_id)
    if updated is None:
        return _tool_error("Integration not found after update."), "denied", entry_id
    after = _config_entry_metadata_snapshot(updated)
    await _record_version(
        data,
        access_key,
        resource_type="config_entry",
        resource_id=entry_id,
        action="edit",
        before=before,
        after=after,
        alias=redact_diagnostics(updated.title),
    )
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "changed": before != after,
        "updated": {
            "title": redact_diagnostics(updated.title),
            "pref_disable_new_entities": bool(updated.pref_disable_new_entities),
            "pref_disable_polling": bool(updated.pref_disable_polling),
            "enabled": updated.disabled_by is None,
        },
        "reload_attempted": reload_attempted,
        "reload_succeeded": reload_succeeded,
        "requires_restart": reload_attempted and reload_succeeded is False,
    }
    if resolved.warnings:
        _add_mesa_advisory(body, resolved.warnings)
    return _tool_success(json.dumps(body, default=str)), "allowed", f"integration:{entry_id}"


async def _tool_reload_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "reload_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "reload_integration"
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    support_error = await _reload_integration_support_error(entry, hass)
    if support_error is not None:
        return _tool_error(support_error), "denied", entry_id
    blocked = await _integration_gate(
        tool_name="reload_integration",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=["reload"],
        service_data={},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_reload_integration(args, access_key, hass, decision),
    )
    if blocked is not None:
        return blocked
    return await _execute_reload_integration(args, access_key, hass, data)


async def _reload_integration_support_error(entry: Any, hass: HomeAssistant) -> str | None:
    if entry.disabled_by is not None:
        return "Disabled integrations cannot be reloaded."
    if entry.state in (
        ConfigEntryState.MIGRATION_ERROR,
        ConfigEntryState.SETUP_IN_PROGRESS,
        ConfigEntryState.UNLOAD_IN_PROGRESS,
    ):
        return f"Integration state {_config_entry_value(entry.state)} is not reloadable."
    supports_reload, _unload, _options, _reconfigure = await _config_entry_support(entry, hass)
    if supports_reload is not True:
        return "This integration does not currently support reload."
    return None


@write_boundary
async def _execute_reload_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    support_error = await _reload_integration_support_error(entry, hass)
    if support_error is not None:
        return _tool_error(support_error), "denied", entry_id
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=["reload"],
        service_data={},
        session_id="reload_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not approved_same
    ):
        return _config_entry_mesa_error(entry_id, resolved)
    try:
        reloaded = await hass.config_entries.async_reload(entry_id)
    except Exception as exc:
        _LOGGER.error("reload_integration failed for %s: %s", entry_id, _scrub_log_text(str(exc)))
        return _tool_error("Failed to reload integration."), "denied", entry_id
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "reloaded": bool(reloaded),
        "requires_restart": not bool(reloaded),
        "state": _config_entry_value(
            getattr(hass.config_entries.async_get_entry(entry_id), "state", None)
        ),
    }
    if resolved.warnings:
        _add_mesa_advisory(body, resolved.warnings)
    return _tool_success(json.dumps(body, default=str)), "allowed", f"integration:{entry_id}"


def _config_entry_permission_references(
    data: PhoenixData,
    *,
    entity_ids: list[str],
    device_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Permission nodes which will outlive removal as stale identities."""
    entity_references: list[dict[str, Any]] = []
    device_references: list[dict[str, Any]] = []
    entity_set = set(entity_ids)
    device_set = set(device_ids)
    for record in data.store.list_access_keys():
        if not record.is_valid():
            continue
        for entity_id in sorted(entity_set.intersection(record.permissions.entities)):
            entity_references.append({
                "entity_id": entity_id,
                "kind": "access_key_permission",
                "access_key_id": record.id,
                "access_key_name": record.name,
            })
        for device_id in sorted(device_set.intersection(record.permissions.devices)):
            device_references.append({
                "device_id": device_id,
                "kind": "access_key_permission",
                "access_key_id": record.id,
                "access_key_name": record.name,
            })
        for preset in record.presets:
            for entity_id in sorted(entity_set.intersection(preset.permissions.entities)):
                entity_references.append({
                    "entity_id": entity_id,
                    "kind": "preset_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                    "preset_id": preset.id,
                    "preset_name": preset.name,
                })
            for device_id in sorted(device_set.intersection(preset.permissions.devices)):
                device_references.append({
                    "device_id": device_id,
                    "kind": "preset_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                    "preset_id": preset.id,
                    "preset_name": preset.name,
                })
    return {"entities": entity_references, "devices": device_references}


def _identity_paths(
    value: Any,
    identities: set[str],
    *,
    path: str = "settings",
) -> list[str]:
    """Return paths, never values, for exact identities in Phoenix settings."""
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in identities:
                matches.append(child_path)
            matches.extend(_identity_paths(item, identities, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(
                _identity_paths(item, identities, path=f"{path}[{index}]")
            )
    elif value in identities:
        matches.append(path)
    return matches


def _config_entry_pending_references(
    data: PhoenixData, identities: set[str]
) -> list[dict[str, Any]]:
    """Pending Phoenix actions keyed to the entry or one affected resource."""
    references: list[dict[str, Any]] = []
    for pending in data.store.get_pending_approvals():
        if pending.get("status") != "pending":
            continue
        if pending.get("id") in data.approvals_in_progress:
            continue
        args = pending.get("args", {})
        if any(_contains_entity_identity(args, identity) for identity in identities):
            references.append({
                "approval_id": pending.get("id"),
                "tool_name": pending.get("tool_name"),
            })
    return references


def _config_entry_safe_owner_summary(entry: Any) -> dict[str, Any]:
    """Allowlisted config-entry identity for shared-device reporting."""
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "disabled_by": _config_entry_value(entry.disabled_by),
    }


def _stored_profile_document(profile: Any | None) -> dict[str, Any] | None:
    return profile.to_dict() if profile is not None else None


async def _build_diff_remove_integration(
    args: dict[str, Any],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    """Build the exact removal and surviving-identity preview."""
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    context = config_entry_registry_context(entry_id, hass)
    entity_ids = sorted(context.entity_ids) if context is not None else []
    device_ids = sorted(context.device_ids) if context is not None else []
    affected_devices: list[dict[str, Any]] = []
    shared_devices: list[dict[str, Any]] = []
    for device_id in device_ids:
        device = device_registry_snapshot(hass).get(device_id)
        if device is None:
            continue
        remaining_owners = []
        for owner_id in device_config_entry_ids(device):
            if owner_id == entry_id:
                continue
            owner = hass.config_entries.async_get_entry(owner_id)
            if owner is not None:
                remaining_owners.append(_config_entry_safe_owner_summary(owner))
        device_summary = {
            "device_id": device_id,
            "name": redact_diagnostics(device.name_by_user or device.name or device_id),
            "shared": bool(remaining_owners),
            "remaining_owners": remaining_owners,
        }
        affected_devices.append(device_summary)
        if remaining_owners:
            shared_devices.append(device_summary)

    hints = data.store.get_entity_hints()
    mesa_profiles: dict[str, Any] = {
        "entities": [],
        "devices": [],
        "integration": None,
    }
    if data.mesa is not None:
        for entity_id in entity_ids:
            stored = data.mesa.store.get(entity_id)
            if stored is not None:
                mesa_profiles["entities"].append({
                    "entity_id": entity_id,
                    "profile": _stored_profile_document(stored),
                })
        for device_id in device_ids:
            stored = data.mesa.store.get_device_profile(device_id)
            if stored is not None:
                mesa_profiles["devices"].append({
                    "device_id": device_id,
                    "profile": _stored_profile_document(stored),
                })
        if entry is not None:
            mesa_profiles["integration"] = _stored_profile_document(
                data.mesa.store.get_integration_profile(entry.domain)
            )

    identities = {entry_id, *entity_ids, *device_ids}
    settings = data.store.get_settings().to_dict()
    preview: dict[str, Any] = {
        "entry": (
            _config_entry_safe_owner_summary(entry) if entry is not None else None
        ),
        "affected_entities": entity_ids,
        "affected_devices": affected_devices,
        "relationships": await _registry_relationships_preview(hass, entity_ids),
        "shared_devices": shared_devices,
        "permission_references": _config_entry_permission_references(
            data, entity_ids=entity_ids, device_ids=device_ids
        ),
        "global_hints": [entity_id for entity_id in entity_ids if entity_id in hints],
        "mesa_profiles": mesa_profiles,
        "pending_approvals": _config_entry_pending_references(data, identities),
        "other_phoenix_identity_paths": sorted(set(_identity_paths(settings, identities))),
        "warning": (
            "Home Assistant will remove this config entry through its integration-aware "
            "cleanup. Known consumers and Phoenix identity-keyed configuration are not "
            "rewritten, MESA profiles are not deleted, and this cannot be restored."
        ),
    }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision, admin=True)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    return {
        "kind": "system_action",
        **_summary("integration.remove", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }


def _config_entry_delete_snapshot(entry: Any, context: Any) -> dict[str, Any]:
    """Safe, intentionally non-restorable config-entry removal record."""
    return {
        "snapshot_type": _CONFIG_ENTRY_DELETE_SNAPSHOT,
        "restorable": False,
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "source": _config_entry_value(entry.source),
        "state": _config_entry_value(entry.state),
        "disabled_by": _config_entry_value(entry.disabled_by),
        "pref_disable_new_entities": bool(entry.pref_disable_new_entities),
        "pref_disable_polling": bool(entry.pref_disable_polling),
        "entity_ids": sorted(context.entity_ids),
        "device_ids": sorted(context.device_ids),
    }


def _config_entry_removal_observation(
    entry_id: str,
    before_entity_ids: list[str],
    before_device_ids: list[str],
    hass: HomeAssistant,
) -> dict[str, Any]:
    """Observe cleanup after HA returns or raises; never infer rollback."""
    entity_registry = er.async_get(hass)
    remaining_entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.entities.values()
        if registry_entry.config_entry_id == entry_id
    )
    removed_entity_ids = sorted(set(before_entity_ids) - set(remaining_entity_ids))
    removed_device_ids: list[str] = []
    remaining_devices: list[dict[str, Any]] = []
    ownership_anomalies: list[str] = []
    for device_id in before_device_ids:
        device = device_registry_snapshot(hass).get(device_id)
        if device is None:
            removed_device_ids.append(device_id)
            continue
        owner_ids = device_config_entry_ids(device)
        if entry_id in owner_ids:
            ownership_anomalies.append(device_id)
        owners = []
        for owner_id in owner_ids:
            owner = hass.config_entries.async_get_entry(owner_id)
            if owner is not None:
                owners.append(_config_entry_safe_owner_summary(owner))
        remaining_devices.append({
            "device_id": device_id,
            "remaining_owners": owners,
        })
    return {
        "entry_removed": hass.config_entries.async_get_entry(entry_id) is None,
        "entities_before": before_entity_ids,
        "entities_removed": removed_entity_ids,
        "entities_remaining": remaining_entity_ids,
        "devices_before": before_device_ids,
        "devices_removed": removed_device_ids,
        "devices_remaining": remaining_devices,
        "device_ownership_anomalies": ownership_anomalies,
    }


async def _tool_remove_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Remove one config entry only through Home Assistant's manager."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "remove_integration"
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    blocked = await _integration_gate(
        tool_name="remove_integration",
        args={"entry_id": entry_id},
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=["remove"],
        service_data={},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_remove_integration(
            args, access_key, hass, data, decision
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_remove_integration(args, access_key, hass, data)


@write_boundary
async def _execute_remove_integration(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Use the same protected lifecycle for Template removal on the generic path."""

    entry = hass.config_entries.async_get_entry(str(args.get("entry_id") or ""))
    if entry is None or entry.domain != "template":
        return await _apply_remove_integration(args, access_key, hass, data)
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_integration"
    perm_error = _config_entry_write_error(entry.entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    resolved = _config_entry_action_decision(data, access_key, hass, entry, actions=["remove"],
        service_data={}, session_id="remove_integration_execute")
    if isinstance(resolved, tuple):
        return resolved
    approved = _approved_exec_ctx.get() and args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT) == resolved.fingerprint
    if resolved.decision == "deny" or resolved.decision == "confirm" and not approved:
        return _config_entry_mesa_error(entry.entry_id, resolved)
    rid = None
    try:
        async with protect_template(hass, data, access_key, entry, "remove_integration") as rid:
            result = await _apply_remove_integration(args, access_key, hass, data)
            removed = hass.config_entries.async_get_entry(entry.entry_id) is None
            remaining = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            await data.config_recovery.mark(rid, "verified" if removed and not remaining else "partial" if removed else "unknown", entry_removed=removed, remaining_entities=len(remaining))
            if removed and not result[0].get("isError"):
                body = json.loads(result[0]["content"][0]["text"])
                body["recovery_id"] = rid
                result = (_tool_success(json.dumps(body, default=str)), result[1], result[2])
            return result
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else "Configuration recovery failed; inspect Home Assistant before retrying."
        body = {"error": message, "recovery_id": rid, "status": "unknown" if rid else "not_applied", "retry_safe": False}
        return _tool_uncertain(body) if rid else _tool_error(json.dumps(body)), "invalid_request", "remove_integration"


async def _apply_remove_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate, remove through HA, then report the observed cleanup."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    context = config_entry_registry_context(entry_id, hass)
    if context is None:
        return _tool_error("Integration not found."), "not_found", entry_id
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=["remove"],
        service_data={},
        session_id="remove_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not approved_same
    ):
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry_id, resolved)

    before_entity_ids = sorted(context.entity_ids)
    before_device_ids = sorted(context.device_ids)
    version_before = (template_snapshot(hass, entry) if entry.domain == "template"
                      else _config_entry_delete_snapshot(entry, context))
    remove_result: dict[str, Any] = {}
    remove_raised = False
    try:
        raw_result = await hass.config_entries.async_remove(entry_id)
        if isinstance(raw_result, dict):
            remove_result = raw_result
    except Exception as exc:  # HA/integration removal boundary
        remove_raised = True
        _LOGGER.error("remove_integration failed for %s: %s", entry_id, _scrub_log_text(str(exc)))

    observed = _config_entry_removal_observation(
        entry_id, before_entity_ids, before_device_ids, hass
    )
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "domain": entry.domain,
        "removed": observed["entry_removed"],
        "require_restart": remove_result.get("require_restart"),
        "home_assistant_raised": remove_raised,
        "observed_cleanup": observed,
    }
    if resolved.warnings:
        _add_mesa_advisory(body, resolved.warnings)
    if observed["entry_removed"]:
        await _record_version(
            data,
            access_key,
            resource_type="config_entry",
            resource_id=entry_id,
            action="delete",
            before=version_before,
            after=None,
            alias=redact_diagnostics(entry.title),
        )
        if remove_raised:
            body["warning"] = (
                "Home Assistant raised during removal, but the observed config entry "
                "is gone. Review the reported registry cleanup anomalies."
            )
        return (
            _tool_success(json.dumps(body, default=str)),
            "allowed",
            f"integration:{entry_id}",
        )

    body["error"] = (
        "Home Assistant did not remove the config entry. The observed registry state "
        "is included; Phoenix made no manual registry deletion."
    )
    return _tool_error(json.dumps(body, default=str)), "denied", entry_id


async def _build_diff_set_integration_enabled(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict:
    entry_id = str(args.get("entry_id") or "").strip()
    enabled = bool(args.get("enabled"))
    entry = hass.config_entries.async_get_entry(entry_id)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    preview: dict[str, Any] = {
        "domain": entry.domain if entry is not None else None,
        "enabled": enabled,
        "warning": None if enabled else "Disabling unloads the integration and its entities.",
    }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision, admin=True)
    return {
        "kind": "system_action",
        **_summary("integration.enable" if enabled else "integration.disable", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }


async def _build_diff_set_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    preview: dict[str, Any] = {}
    if entry is not None:
        for field in ("title", "pref_disable_new_entities", "pref_disable_polling"):
            if field in args:
                before = getattr(entry, field)
                preview[field] = {
                    "before": redact_diagnostics(before) if field == "title" else before,
                    "after": redact_diagnostics(args[field]) if field == "title" else args[field],
                }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision, admin=True)
    return {
        "kind": "system_action",
        **_summary("integration.update", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }


async def _build_diff_reload_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    preview: dict[str, Any] = {
        "domain": entry.domain if entry is not None else None,
        "state": _config_entry_value(entry.state) if entry is not None else None,
        "warning": "Reload temporarily unloads and sets up the integration again.",
    }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision, admin=True)
    return {
        "kind": "system_action",
        **_summary("integration.reload", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }
