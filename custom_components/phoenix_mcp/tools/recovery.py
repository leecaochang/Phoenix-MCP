"""Access-key-scoped MCP restoration for qualified configuration records."""

from __future__ import annotations

import json
import os
from typing import Any

from homeassistant.helpers import entity_registry as er

from ..config_recovery import ConfigurationRecoveryError, recovery_resolution
from ..helpers import content_hash, effective_cap
from ..request_authority import check_request_authority
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _gate, _restore_ctx, _tool_error, _tool_success
from .helper import (
    _HELPER_MESA_FINGERPRINT,
    _enforce_helper_mesa_context,
    _helper_mesa_approval_gate,
    _helper_mesa_context,
    _unwritable_option_entities,
)


def _may_restore_record(record: dict | None, access_key) -> bool:
    """Agent records are owner-only; HA admin restoration uses a bound context."""
    if record is None:
        return False
    context = _restore_ctx.get()
    return record.get("access_key_id") == access_key.id or (
        context is not None and access_key.id == f"__restore__:{context['user_id']}"
    )


def _template_record_target(record: dict[str, Any], side: str, hass) -> dict[str, Any]:
    from ..template_recovery import template_snapshot

    source = record.get(side)
    if not isinstance(source, dict) or source.get("snapshot_type") != "template_helper":
        raise ConfigurationRecoveryError("This recovery side is not an available Template snapshot.")
    entry_id = source.get("entry_id")
    entity = source.get("entities", [{}])[0]
    entity_id = entity.get("entity_id") if isinstance(entity, dict) else None
    if not isinstance(entry_id, str) or not isinstance(entity_id, str):
        raise ConfigurationRecoveryError("The Template recovery target is incomplete.")
    entry = hass.config_entries.async_get_entry(entry_id)
    current = None
    if entry is not None:
        if entry.domain != "template":
            raise ConfigurationRecoveryError("The saved Template target is occupied by another integration.")
        current = template_snapshot(hass, entry)
    registry_entry = er.async_get(hass).async_get(entity_id)
    registry = None
    if registry_entry is not None:
        registry = {
            "entity_id": registry_entry.entity_id,
            "unique_id": registry_entry.unique_id,
            "platform": registry_entry.platform,
            "config_entry_id": registry_entry.config_entry_id,
        }
    return {
        "entry": current,
        "registry": registry,
        "state_present": hass.states.get(entity_id) is not None,
    }


def _selected_template_snapshot(record: dict[str, Any], side: str) -> dict[str, Any]:
    selected = record.get(side)
    if not isinstance(selected, dict) or selected.get("snapshot_type") != "template_helper":
        raise ConfigurationRecoveryError("The selected recovery side is unavailable for Template restoration.")
    return selected


def _restore_diff(record: dict[str, Any], side: str, current: dict[str, Any]) -> dict[str, Any]:
    selected = _selected_template_snapshot(record, side)
    before = current["entry"]
    return {
        "kind": "config_diff",
        "summary": "Restore Template helper configuration",
        "target": {
            "type": "config_entry",
            "id": selected["entry_id"],
            "label": selected["title"],
        },
        "before": json.dumps(before, indent=2, sort_keys=True, default=str)
        if isinstance(before, dict) else None,
        "after": json.dumps(selected, indent=2, sort_keys=True, default=str),
        "preview": {"helper_type": selected["options"]["template_type"], "side": side},
    }


def _dashboard_registry_lookup_key(record: dict[str, Any], side: str, snapshot: dict[str, Any]) -> str:
    """Prefer stable url_path over HA's generated registry ID when available."""
    candidates = [snapshot, record.get("source" if side == "before" else "before")]
    for candidate in candidates:
        row = candidate.get("dashboard") if isinstance(candidate, dict) else None
        if isinstance(row, dict) and isinstance(row.get("url_path"), str) and row["url_path"]:
            return row["url_path"]
    return str(snapshot["resource_id"])


async def _tool_restore_configuration(
    args: dict,
    access_key,
    hass,
    data,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Restore a recovery record in two steps, dispatching on its snapshot type.

    Without `snapshot_digest` and `target_fingerprint` it returns a receipt (both
    values plus a diff). A call echoing both is gated through Phoenix approval; the
    execute step then revalidates both against the live target before writing.
    """
    tool = "restore_configuration"
    if all(effective_cap(access_key, cap) == "deny" for cap in (
        "cap_helper_write", "cap_energy_write", "cap_yaml_edit", "cap_filesystem",
        "cap_blueprint_write", "cap_lovelace_write",
    )):
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    rid = args.get("recovery_id")
    side = args.get("side")
    if not isinstance(rid, str) or not rid or side not in ("before", "source"):
        return _tool_error("recovery_id and side ('before' or 'source') are required."), "invalid_request", tool
    record = data.config_recovery.get(rid)
    if not _may_restore_record(record, access_key):
        return _tool_error("Configuration recovery record not found."), "not_found", tool
    assert record is not None
    if not data.config_recovery.inactive(rid) or (record.get("phase") in ("prepared", "dispatching", "partial", "unknown")
            and recovery_resolution.get() != rid):
        return _tool_error("Resolve the recovery request in Approvals before restoring this record."), "invalid_request", tool
    selected = record.get(side)
    if isinstance(selected, dict) and selected.get("snapshot_type") == "energy_preferences":
        return await _tool_restore_energy_configuration(
            args, record, side, access_key, hass, data, request_id, client_ip,
        )
    if isinstance(selected, dict) and selected.get("snapshot_type") in ("authored_file", "yaml_file"):
        return await _tool_restore_file_configuration(
            args, record, side, access_key, hass, data, request_id, client_ip,
        )
    if isinstance(selected, dict) and selected.get("snapshot_type") == "blueprint_source":
        return await _tool_restore_blueprint_configuration(
            args, record, side, access_key, hass, data, request_id, client_ip,
        )
    if isinstance(selected, dict) and selected.get("snapshot_type") == "dashboard_layout":
        return await _tool_restore_dashboard_layout(
            args, record, side, access_key, hass, data, request_id, client_ip,
        )
    if isinstance(selected, dict) and selected.get("snapshot_type") == "dashboard_registry":
        return await _tool_restore_dashboard_registry(
            args, record, side, access_key, hass, data, request_id, client_ip,
        )
    if effective_cap(access_key, "cap_helper_write") == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    try:
        from ..template_recovery import validate_restore_scope, validate_snapshot

        selected = _selected_template_snapshot(record, side)
        validate_snapshot(selected)
        validate_restore_scope(selected, access_key, hass)
        digest = content_hash(selected)
        target = _template_record_target(record, side, hass)
        fingerprint = content_hash(target)
    except PermissionError:
        return _tool_error("Forbidden."), "denied", tool
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - malformed persisted values must not disclose internals
        return _tool_error("Configuration recovery record is not usable."), "invalid_request", tool
    from ..template_recovery import TEMPLATE_RECOVERY_ADAPTERS
    options = selected["options"]
    entity_id = selected["entities"][0]["entity_id"]
    current_entry = hass.config_entries.async_get_entry(selected["entry_id"])
    action = "settings" if current_entry is not None else "create"
    if _unwritable_option_entities(options, access_key, hass, template_type=options["template_type"]):
        return _tool_error("The Template snapshot references entities outside this access key's current write scope."), "denied", tool
    context = _helper_mesa_context(
        data, access_key, [entity_id], action=action,
        service_data={"helper_type": options["template_type"], "config": options},
        session_id=tool,
    )
    if isinstance(context, tuple):
        return context
    supplied_digest = args.get("snapshot_digest")
    supplied_fingerprint = args.get("target_fingerprint")
    if supplied_digest is None and supplied_fingerprint is None:
        return _tool_success(json.dumps({
            "status": "needs_confirmation",
            "recovery_id": rid,
            "side": side,
            "snapshot_digest": digest,
            "target_fingerprint": fingerprint,
            "diff": _restore_diff(record, side, target),
            "adapter": {
                "type": options["template_type"],
                "action_capable": TEMPLATE_RECOVERY_ADAPTERS[options["template_type"]].action_capable,
            },
        }, default=str)), "allowed", tool
    if supplied_digest != digest or supplied_fingerprint != fingerprint:
        return _tool_error("The selected snapshot or current Template target changed; request a fresh restore receipt."), "invalid_request", tool
    bound = {
        "recovery_id": rid,
        "side": side,
        "snapshot_digest": digest,
        "target_fingerprint": fingerprint,
    }
    approval_args, blocked = await _helper_mesa_approval_gate(
        bound, access_key, hass, data, context=context,
        tool_name=tool, request_id=request_id, client_ip=client_ip,
        diff_builder=lambda: _restore_diff(record, side, target),
    )
    if blocked is not None:
        return blocked
    return await _execute_restore_configuration(approval_args, access_key, hass, data)


async def _tool_restore_file_configuration(
    args: dict, record: dict[str, Any], side: str, access_key, hass, data,
    request_id: str, client_ip: str | None,
) -> tuple[dict, str, str]:
    """Preflight a jailed file restore and bind its live content fingerprint."""
    from .config_files import _authored_file_snapshot

    tool = "restore_configuration"
    snapshot = record.get(side)
    if not isinstance(snapshot, dict):
        return _tool_error("The selected file recovery snapshot is unavailable."), "invalid_request", tool
    family = snapshot.get("snapshot_type")
    if family not in ("authored_file", "yaml_file"):
        return _tool_error("Unsupported file recovery adapter."), "invalid_request", tool
    want_exists = snapshot.get("exists") is True
    if (snapshot.get("exists") not in (True, False)
            or (want_exists and not isinstance(snapshot.get("content"), str))
            or (not want_exists and snapshot.get("content") is not None)):
        return _tool_error("The selected file snapshot is malformed."), "invalid_request", tool
    if family == "yaml_file" and not want_exists:
        return _tool_error("Restoring an absent YAML include would delete configuration; this inverse is refused."), "invalid_request", tool
    cap = "cap_yaml_edit" if family == "yaml_file" else "cap_filesystem"
    if effective_cap(access_key, cap) == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    rel, content = snapshot.get("resource_id"), snapshot.get("content")
    if not isinstance(rel, str) or (want_exists and not isinstance(content, str)):
        return _tool_error("The selected file snapshot is incomplete."), "invalid_request", tool
    try:
        if isinstance(content, str) and len(content.encode("utf-8")) > 1_000_000:
            raise ConfigurationRecoveryError("File snapshot exceeds the restore size limit.")
        if family == "yaml_file":
            from .config_files import _yaml_write_target
            target = _yaml_write_target(hass, {"file": rel})
            if target is None or target[1] != rel:
                raise ConfigurationRecoveryError("The YAML target is outside its current write jail.")
            path = target[0]
        else:
            from .config_files import _resolve_fs_path
            fs_path = _resolve_fs_path(hass, rel)
            if fs_path is None:
                raise ConfigurationRecoveryError("The file target is outside its current write jail.")
            path = fs_path
        current_exists = await hass.async_add_executor_job(os.path.isfile, path)
        current_size = await hass.async_add_executor_job(os.path.getsize, path) if current_exists else 0
        if current_exists and current_size > 1_000_000:
            raise ConfigurationRecoveryError("The current file exceeds the restore size limit.")
        if family == "yaml_file" and rel != "configuration.yaml":
            from .config_files import _yaml_mount_refusal
            refusal = await hass.async_add_executor_job(
                _yaml_mount_refusal, hass.config.config_dir, path, rel, tool,
            )
            if refusal is not None:
                raise ConfigurationRecoveryError("The YAML include target is no longer writable.")
        if current_exists:
            from ..tool_common import _read_text_capped
            current_text = await hass.async_add_executor_job(_read_text_capped, path)
        else:
            current_text = None
        if family == "yaml_file":
            from .config_files import (
                _yaml_protected_check, _yaml_removal_check, _yaml_write_precheck,
            )
            refusal = _yaml_write_precheck({"content": content}, tool)
            if refusal is not None:
                return refusal
            refusal = await hass.async_add_executor_job(
                _yaml_protected_check, current_text, content, tool,
            )
            if refusal is not None:
                return refusal
            if _restore_ctx.get() is None:
                refusal = await hass.async_add_executor_job(
                    _yaml_removal_check, current_text, content, {"remove_keys": []}, tool,
                )
                if refusal is not None:
                    return refusal
        target_snapshot = await hass.async_add_executor_job(_authored_file_snapshot, rel, current_exists, current_text, family)
        digest, fingerprint = content_hash(snapshot), content_hash(target_snapshot)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - path and persisted-state failures fail closed
        return _tool_error("The selected file target is not usable."), "invalid_request", tool
    if args.get("snapshot_digest") is None and args.get("target_fingerprint") is None:
        diff = {
            "kind": "config_diff", "summary": "Restore authored file content",
            "target": {"type": family, "id": rel, "label": rel},
            "before": current_text, "after": content if want_exists else None,
        }
        return _tool_success(json.dumps({
            "status": "needs_confirmation", "recovery_id": record["id"], "side": side,
            "snapshot_digest": digest, "target_fingerprint": fingerprint, "diff": diff,
        })), "allowed", tool
    if args.get("snapshot_digest") != digest or args.get("target_fingerprint") != fingerprint:
        return _tool_error("The selected file snapshot or target changed; request a fresh restore receipt."), "invalid_request", tool
    bound = {"recovery_id": record["id"], "side": side,
             "snapshot_digest": digest, "target_fingerprint": fingerprint}
    blocked = await _gate(
        cap, access_key, hass, data, tool_name=tool, args=bound,
        request_id=request_id, client_ip=client_ip,
        diff=lambda: {"kind": "config_diff", "summary": "Restore authored file content",
                      "target": {"type": family, "id": rel, "label": rel},
                      "before": current_text, "after": content if want_exists else None},
    )
    if blocked is not None:
        return blocked
    return await _execute_restore_configuration(bound, access_key, hass, data)


def _blueprint_target_snapshot(domain: str, path: str, content: str | None) -> dict:
    from .blueprint import _blueprint_recovery_snapshot
    return _blueprint_recovery_snapshot(domain, path, content)


async def _tool_restore_blueprint_configuration(
    args: dict, record: dict[str, Any], side: str, access_key, hass, data,
    request_id: str, client_ip: str | None,
) -> tuple[dict, str, str]:
    """Preflight one blueprint source restore under its dual gate.

    The gate is cap_blueprint_write plus the blueprint domain's own write cap.
    """
    from .blueprint import _blueprint_denied, _read_blueprint_source, _resolve_blueprint_path

    tool = "restore_configuration"
    snapshot = record.get(side)
    if not isinstance(snapshot, dict):
        return _tool_error("The selected blueprint snapshot is unavailable."), "invalid_request", tool
    domain, path = snapshot.get("domain"), snapshot.get("path")
    if (snapshot.get("schema") != 1 or domain not in ("automation", "script")
            or not isinstance(path, str) or not path
            or type(snapshot.get("exists")) is not bool
            or snapshot.get("exists") != isinstance(snapshot.get("content"), str)):
        return _tool_error("The selected blueprint snapshot is incomplete."), "invalid_request", tool
    denied = _blueprint_denied(access_key, {"domain": domain}, tool)
    if denied is not None:
        return denied
    resolved = _resolve_blueprint_path(hass, domain, path)
    if resolved is None:
        return _tool_error("The blueprint target is outside its current write jail."), "invalid_request", tool
    target_path, canonical = resolved
    if canonical != path:
        return _tool_error("The blueprint path is no longer canonical."), "invalid_request", tool
    try:
        current = await _read_blueprint_source(hass, target_path)
        target_snapshot = _blueprint_target_snapshot(domain, path, current)
        digest, fingerprint = content_hash(snapshot), content_hash(target_snapshot)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    if args.get("snapshot_digest") is None and args.get("target_fingerprint") is None:
        diff = {
            "kind": "yaml_diff", "summary": "Restore blueprint source",
            "target": {"type": "blueprint", "id": f"{domain}/{path}", "label": path},
            "before": current, "after": snapshot.get("content"),
        }
        return _tool_success(json.dumps({
            "status": "needs_confirmation", "recovery_id": record["id"], "side": side,
            "snapshot_digest": digest, "target_fingerprint": fingerprint, "diff": diff,
        })), "allowed", tool
    if args.get("snapshot_digest") != digest or args.get("target_fingerprint") != fingerprint:
        return _tool_error("The selected blueprint snapshot or target changed; request a fresh restore receipt."), "invalid_request", tool
    bound = {"recovery_id": record["id"], "side": side,
             "snapshot_digest": digest, "target_fingerprint": fingerprint}
    blocked = await _gate(
        "cap_blueprint_write", access_key, hass, data, tool_name=tool, args=bound,
        request_id=request_id, client_ip=client_ip,
        diff=lambda: {"kind": "yaml_diff", "summary": "Restore blueprint source",
                      "target": {"type": "blueprint", "id": f"{domain}/{path}", "label": path},
                      "before": current, "after": snapshot.get("content")},
    )
    if blocked is not None:
        return blocked
    return await _execute_restore_configuration(bound, access_key, hass, data)


async def _tool_restore_dashboard_layout(
    args: dict, record: dict[str, Any], side: str, access_key, hass, data,
    request_id: str, client_ip: str | None,
) -> tuple[dict, str, str]:
    """Preflight a dashboard layout restore under cap_lovelace_write."""
    from .lovelace import _dashboard_layout_snapshot
    from ..ws_dispatch import WsDashboardNotFoundError, async_get_lovelace_config
    from ..policy_engine import filter_service_response

    tool = "restore_configuration"
    snapshot = record.get(side)
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != 1
            or snapshot.get("exists") is not True or not isinstance(snapshot.get("config"), dict)):
        return _tool_error("This dashboard layout snapshot has no restorable layout."), "invalid_request", tool
    if effective_cap(access_key, "cap_lovelace_write") == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    url_path = snapshot.get("url_path")
    if url_path is not None and not isinstance(url_path, str):
        return _tool_error("The dashboard layout target is invalid."), "invalid_request", tool
    try:
        current = await async_get_lovelace_config(hass, url_path)
    except WsDashboardNotFoundError:
        current = None
    except Exception:  # noqa: BLE001 - unreadable target is not absence
        return _tool_error("Could not read the current dashboard layout."), "invalid_request", tool
    try:
        target = _dashboard_layout_snapshot(url_path, current)
        digest, fingerprint = content_hash(snapshot), content_hash(target)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    if args.get("snapshot_digest") is None and args.get("target_fingerprint") is None:
        return _tool_success(json.dumps({
            "status": "needs_confirmation", "recovery_id": record["id"], "side": side,
            "snapshot_digest": digest, "target_fingerprint": fingerprint,
            "diff": {
                "kind": "yaml_diff", "summary": "Restore dashboard layout",
                "target": {"type": "dashboard", "id": snapshot["resource_id"], "label": url_path or "Default"},
                "before": json.dumps(filter_service_response(current, access_key, hass), default=str) if current else None,
                "after": json.dumps(filter_service_response(snapshot["config"], access_key, hass), default=str),
            },
        }, default=str)), "allowed", tool
    if args.get("snapshot_digest") != digest or args.get("target_fingerprint") != fingerprint:
        return _tool_error("The selected dashboard snapshot or target changed; request a fresh restore receipt."), "invalid_request", tool
    bound = {"recovery_id": record["id"], "side": side,
             "snapshot_digest": digest, "target_fingerprint": fingerprint}
    blocked = await _gate(
        "cap_lovelace_write", access_key, hass, data, tool_name=tool, args=bound,
        request_id=request_id, client_ip=client_ip,
        diff=lambda: {"kind": "yaml_diff", "summary": "Restore dashboard layout",
                      "target": {"type": "dashboard", "id": snapshot["resource_id"], "label": url_path or "Default"},
                      "before": json.dumps(filter_service_response(current, access_key, hass), default=str) if current else None,
                      "after": json.dumps(filter_service_response(snapshot["config"], access_key, hass), default=str)},
    )
    if blocked is not None:
        return blocked
    return await _execute_restore_configuration(bound, access_key, hass, data)


async def _tool_restore_dashboard_registry(
    args: dict, record: dict[str, Any], side: str, access_key, hass, data,
    request_id: str, client_ip: str | None,
) -> tuple[dict, str, str]:
    """Preflight dashboard registry metadata restoration under cap_lovelace_write."""
    from .lovelace import _dashboard_registry_snapshot

    tool = "restore_configuration"
    snapshot = record.get(side)
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != 1
            or not isinstance(snapshot.get("resource_id"), str)
            or not isinstance(snapshot.get("exists"), bool)):
        return _tool_error("This dashboard registry snapshot is invalid."), "invalid_request", tool
    if effective_cap(access_key, "cap_lovelace_write") == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    try:
        lookup_key = _dashboard_registry_lookup_key(record, side, snapshot)
        target = await _dashboard_registry_snapshot(hass, lookup_key)
        digest, fingerprint = content_hash(snapshot), content_hash(target)
    except Exception:  # noqa: BLE001 - incomplete registry read is not absence
        return _tool_error("Could not read the current dashboard registry."), "invalid_request", tool
    if args.get("snapshot_digest") is None and args.get("target_fingerprint") is None:
        return _tool_success(json.dumps({
            "status": "needs_confirmation", "recovery_id": record["id"], "side": side,
            "snapshot_digest": digest, "target_fingerprint": fingerprint,
            "diff": {
                "kind": "config_diff", "summary": "Restore dashboard registry entry",
                "target": {"type": "dashboard", "id": snapshot["resource_id"]},
                "before": json.dumps(target, default=str),
                "after": json.dumps(snapshot, default=str),
            },
        }, default=str)), "allowed", tool
    if args.get("snapshot_digest") != digest or args.get("target_fingerprint") != fingerprint:
        return _tool_error("The selected dashboard registry snapshot or target changed; request a fresh restore receipt."), "invalid_request", tool
    bound = {"recovery_id": record["id"], "side": side,
             "snapshot_digest": digest, "target_fingerprint": fingerprint}
    blocked = await _gate(
        "cap_lovelace_write", access_key, hass, data, tool_name=tool, args=bound,
        request_id=request_id, client_ip=client_ip,
        diff=lambda: {"kind": "config_diff", "summary": "Restore dashboard registry entry",
                      "target": {"type": "dashboard", "id": snapshot["resource_id"]},
                      "before": json.dumps(target, default=str),
                      "after": json.dumps(snapshot, default=str)},
    )
    if blocked is not None:
        return blocked
    return await _execute_restore_configuration(bound, access_key, hass, data)


async def _execute_restore_dashboard_registry(args, record, side, snapshot, access_key, hass, data):
    """Revalidate and restore one dashboard registry row, recording the inverse."""
    from .lovelace import _dashboard_registry_snapshot

    tool = "restore_configuration"
    if effective_cap(access_key, "cap_lovelace_write") == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    if (snapshot.get("schema") != 1 or not isinstance(snapshot.get("resource_id"), str)
            or not isinstance(snapshot.get("exists"), bool)):
        return _tool_error("This dashboard registry snapshot is invalid."), "invalid_request", tool
    key = _dashboard_registry_lookup_key(record, side, snapshot)
    if content_hash(snapshot) != args.get("snapshot_digest"):
        return _tool_error("The selected dashboard registry snapshot changed after approval."), "invalid_request", tool
    try:
        target = await _dashboard_registry_snapshot(hass, key)
        if content_hash(target) != args.get("target_fingerprint"):
            return _tool_error("The dashboard registry changed after approval."), "invalid_request", tool
        check_request_authority()
        if target == snapshot:
            return _tool_success(json.dumps({"status": "verified", "already_applied": True})), "allowed", tool
        recovery = data.config_recovery
        resource = f"dashboard_registry:{key}"
        async with recovery.resource(resource):
            rid = await recovery.begin(resource, "dashboard_registry_restore", access_key.id, target, expected=snapshot)
            try:
                await recovery.mark(rid, "dispatching")
                current = await _dashboard_registry_snapshot(hass, key)
                if current != target:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("The dashboard registry changed before restore dispatch."), "invalid_request", tool
                check_request_authority()
                from ..ws_dispatch import async_ws_command
                if snapshot["exists"]:
                    dashboard = snapshot.get("dashboard")
                    if not isinstance(dashboard, dict):
                        await recovery.mark(rid, "not_applied")
                        return _tool_error("The dashboard snapshot has no registry row."), "invalid_request", tool
                    current_row = target.get("dashboard")
                    metadata = {k: v for k, v in dashboard.items() if k != "id"}
                    if current_row is None:
                        result = await async_ws_command(hass, "lovelace/dashboards/create", metadata)
                    else:
                        from homeassistant.components.lovelace.const import STORAGE_DASHBOARD_UPDATE_FIELDS
                        fields = {getattr(field, "schema", field) for field in STORAGE_DASHBOARD_UPDATE_FIELDS}
                        result = await async_ws_command(
                            hass, "lovelace/dashboards/update",
                            {"dashboard_id": current_row["id"], **{k: v for k, v in metadata.items() if k in fields}},
                        )
                elif target.get("exists"):
                    row = target.get("dashboard")
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                        await recovery.mark(rid, "not_applied")
                        return _tool_error("The current dashboard identity is incomplete; it was not deleted."), "invalid_request", tool
                    result = await async_ws_command(hass, "lovelace/dashboards/delete", {"dashboard_id": row["id"]})
                else:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("No dashboard registry change is needed."), "invalid_request", tool
                observed = await _dashboard_registry_snapshot(hass, key)
                same = (observed.get("exists") == snapshot["exists"] and
                        (not snapshot["exists"] or all(
                            observed.get("dashboard", {}).get(field) == snapshot["dashboard"].get(field)
                            for field in ("url_path", "title", "icon", "show_in_sidebar", "require_admin")
                            if field in snapshot["dashboard"]
                        )))
                if not same:
                    await recovery.mark(rid, "partial", observed=observed)
                    return _tool_success(json.dumps({
                        "status": "partial", "recovery_id": rid,
                        "message": "Dashboard registry readback differs; do not retry automatically.",
                    }, default=str)), "allowed", tool
                await recovery.mark(rid, "verified", observed_hash=content_hash(observed))
                await recovery.set_source(rid, observed)
                return _tool_success(json.dumps({
                    "status": "verified", "recovery_id": rid, "dashboard": result,
                }, default=str)), "allowed", tool
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - keep uncertain outcome in the recovery store
        return _tool_error("Dashboard registry restore outcome is uncertain; inspect configuration recovery before retrying."), "invalid_request", tool


async def _tool_restore_energy_configuration(
    args: dict, record: dict[str, Any], side: str, access_key, hass, data,
    request_id: str, client_ip: str | None,
) -> tuple[dict, str, str]:
    """Preflight an Energy restore and bind its live target into Phoenix approval."""
    from .energy import (
        energy_recovery_diff, energy_recovery_target,
        validate_energy_recovery_scope, validate_energy_recovery_snapshot,
    )

    tool = "restore_configuration"
    if effective_cap(access_key, "cap_energy_write") == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    selected = record.get(side)
    if not isinstance(selected, dict):
        return _tool_error("The selected Energy recovery side is unavailable."), "invalid_request", tool
    try:
        validate_energy_recovery_snapshot(selected)
        validate_energy_recovery_scope(selected, access_key, hass)
        target = await energy_recovery_target(hass)
        digest = content_hash(selected)
        fingerprint = content_hash(target)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - malformed persisted snapshots fail closed
        return _tool_error("Energy recovery target is not usable."), "invalid_request", tool
    if args.get("snapshot_digest") is None and args.get("target_fingerprint") is None:
        return _tool_success(json.dumps({
            "status": "needs_confirmation", "recovery_id": record["id"], "side": side,
            "snapshot_digest": digest, "target_fingerprint": fingerprint,
            "diff": energy_recovery_diff(
                target, selected, side, access_key, hass,
            ),
        }, default=str)), "allowed", tool
    if args.get("snapshot_digest") != digest or args.get("target_fingerprint") != fingerprint:
        return _tool_error("The selected Energy snapshot or target changed; request a fresh restore receipt."), "invalid_request", tool
    bound = {
        "recovery_id": record["id"], "side": side,
        "snapshot_digest": digest, "target_fingerprint": fingerprint,
    }
    blocked = await _gate(
        "cap_energy_write", access_key, hass, data,
        tool_name=tool, args=bound, request_id=request_id, client_ip=client_ip,
        diff=lambda: energy_recovery_diff(
            target, selected, side, access_key, hass,
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_restore_configuration(bound, access_key, hass, data)


async def _execute_restore_configuration(
    args: dict, access_key, hass, data
) -> tuple[dict, str, str]:
    """Revalidate owner, snapshot digest and target fingerprint after approval.

    Then run the adapter for the snapshot type (Energy, file, blueprint, dashboard
    layout or registry, Template).
    """
    tool = "restore_configuration"
    record = data.config_recovery.get(str(args.get("recovery_id") or ""))
    if not _may_restore_record(record, access_key):
        return _tool_error("Configuration recovery record not found."), "not_found", tool
    assert record is not None
    if record.get("phase") in ("prepared", "dispatching", "partial", "unknown") and recovery_resolution.get() != record["id"]:
        return _tool_error("Resolve the recovery request in Approvals before restoring this record."), "invalid_request", tool
    side = args.get("side")
    if side not in ("before", "source"):
        return _tool_error("side must be 'before' or 'source'."), "invalid_request", tool
    selected = record.get(side)
    if isinstance(selected, dict) and selected.get("snapshot_type") == "energy_preferences":
        from .energy import (
            energy_recovery_target, restore_energy_configuration,
            validate_energy_recovery_scope, validate_energy_recovery_snapshot,
        )

        if effective_cap(access_key, "cap_energy_write") == "deny":
            return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
        try:
            validate_energy_recovery_snapshot(selected)
            validate_energy_recovery_scope(selected, access_key, hass)
            if content_hash(selected) != args.get("snapshot_digest"):
                return _tool_error("The selected Energy recovery snapshot changed after approval."), "invalid_request", tool
            if content_hash(await energy_recovery_target(hass)) != args.get("target_fingerprint"):
                return _tool_error("The Energy target changed after approval."), "invalid_request", tool
            check_request_authority()
            return await restore_energy_configuration(selected, access_key, hass, data)
        except ConfigurationRecoveryError as exc:
            return _tool_error(str(exc)), "invalid_request", tool
        except Exception:  # noqa: BLE001 - family adapter owns the recovery outcome
            return _tool_error("Energy restoration failed; inspect Home Assistant and configuration recovery before retrying."), "invalid_request", tool
    if isinstance(selected, dict) and selected.get("snapshot_type") in ("authored_file", "yaml_file"):
        return await _execute_restore_file_configuration(args, record, side, selected, access_key, hass, data)
    if isinstance(selected, dict) and selected.get("snapshot_type") == "blueprint_source":
        return await _execute_restore_blueprint_configuration(args, record, side, selected, access_key, hass, data)
    if isinstance(selected, dict) and selected.get("snapshot_type") == "dashboard_layout":
        return await _execute_restore_dashboard_layout(args, record, side, selected, access_key, hass, data)
    if isinstance(selected, dict) and selected.get("snapshot_type") == "dashboard_registry":
        return await _execute_restore_dashboard_registry(args, record, side, selected, access_key, hass, data)
    if effective_cap(access_key, "cap_helper_write") == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    try:
        from ..template_recovery import restore_template, validate_restore_scope, validate_snapshot

        selected = _selected_template_snapshot(record, side)
        validate_snapshot(selected)
        validate_restore_scope(selected, access_key, hass)
        if content_hash(selected) != args.get("snapshot_digest"):
            return _tool_error("The selected recovery snapshot changed after approval."), "invalid_request", tool
        if content_hash(_template_record_target(record, side, hass)) != args.get("target_fingerprint"):
            return _tool_error("The Template target changed after approval."), "invalid_request", tool
        current_entry = hass.config_entries.async_get_entry(selected["entry_id"])
        action = "settings" if current_entry is not None else "create"
        context = _enforce_helper_mesa_context(
            args, access_key, hass, data, [selected["entities"][0]["entity_id"]],
            action=action,
            service_data={"helper_type": selected["options"]["template_type"], "config": selected["options"]},
            session_id=tool,
        )
        if isinstance(context, tuple):
            return context
        check_request_authority()
        return await restore_template(
            selected, access_key, hass, data, mesa_fingerprint=args.get(_HELPER_MESA_FINGERPRINT),
        )
    except PermissionError:
        return _tool_error("Forbidden."), "denied", tool
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - restoration adapter owns the detailed recovery receipt
        return _tool_error("Template restoration failed; inspect Home Assistant and configuration recovery before retrying."), "invalid_request", tool


async def _execute_restore_file_configuration(args, record, side, snapshot, access_key, hass, data):
    """Revalidate a file adapter, then use its own jailed write transaction."""
    from .config_files import (
        _authored_file_snapshot, _get_yaml_file_lock, _recoverable_file_delete, _recoverable_file_write,
        _resolve_fs_path, _yaml_mount_refusal, _yaml_protected_check,
        _yaml_removal_check, _yaml_write_target,
    )
    from ..tool_common import _read_text_capped

    tool = "restore_configuration"
    family = snapshot.get("snapshot_type")
    cap = "cap_yaml_edit" if family == "yaml_file" else "cap_filesystem"
    if effective_cap(access_key, cap) == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    want_exists = snapshot.get("exists") is True
    if (snapshot.get("exists") not in (True, False)
            or (want_exists and not isinstance(snapshot.get("content"), str))
            or (not want_exists and snapshot.get("content") is not None)):
        return _tool_error("The selected file snapshot is malformed."), "invalid_request", tool
    if family == "yaml_file" and not want_exists:
        return _tool_error("Restoring an absent YAML include would delete configuration; this inverse is refused."), "invalid_request", tool
    rel, content = snapshot.get("resource_id"), snapshot["content"]
    if family == "yaml_file":
        target = _yaml_write_target(hass, {"file": rel})
        if target is None or target[1] != rel:
            return _tool_error("The YAML target is outside its current write jail."), "invalid_request", tool
        path = target[0]
    else:
        path = _resolve_fs_path(hass, rel)
        if path is None:
            return _tool_error("The file target is outside its current write jail."), "invalid_request", tool
    try:
        async with _get_yaml_file_lock(hass, rel):
            exists = await hass.async_add_executor_job(os.path.isfile, path)
            current = await hass.async_add_executor_job(_read_text_capped, path) if exists else None
            target_snapshot = await hass.async_add_executor_job(_authored_file_snapshot, rel, exists, current, family)
            if content_hash(snapshot) != args.get("snapshot_digest"):
                return _tool_error("The file recovery snapshot changed after approval."), "invalid_request", tool
            if content_hash(target_snapshot) != args.get("target_fingerprint"):
                return _tool_error("The file target changed after approval."), "invalid_request", tool
            if not want_exists:
                if not exists:
                    return _tool_success(json.dumps({"status": "verified", "already_applied": True})), "allowed", tool
                return await _recoverable_file_delete(
                    path=path, rel=rel, before_content=current, access_key=access_key,
                    data=data, operation=tool, hass=hass,
                )
            if family == "yaml_file":
                if rel != "configuration.yaml":
                    refusal = await hass.async_add_executor_job(
                        _yaml_mount_refusal, hass.config.config_dir, path, rel, tool,
                    )
                    if refusal is not None:
                        return _tool_error("The YAML include target is no longer writable."), "invalid_request", tool
                refusal = await hass.async_add_executor_job(_yaml_protected_check, current, content, tool)
                if refusal is not None:
                    return refusal
                if _restore_ctx.get() is None:
                    refusal = await hass.async_add_executor_job(
                        _yaml_removal_check, current, content, {"remove_keys": []}, tool,
                    )
                    if refusal is not None:
                        return refusal
            return await _recoverable_file_write(
                path=path, rel=rel, before_exists=exists, before_content=current,
                content=content, access_key=access_key, data=data, operation=tool,
                display_path=rel, hass=hass, snapshot_type=family, return_result=True,
            ) or (_tool_error("File restoration was not applied."), "invalid_request", tool)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - adapter retains uncertain writes for inspection
        return _tool_error("File restoration failed; inspect configuration recovery before retrying."), "invalid_request", tool


async def _execute_restore_blueprint_configuration(args, record, side, snapshot, access_key, hass, data):
    """Restore blueprint source only through HA's validated blueprint commands."""
    from .blueprint import (
        _blueprint_denied, _blueprint_recovery_snapshot, _canonical_blueprint_source, _read_blueprint_source,
        _resolve_blueprint_path,
    )
    from ..tool_common import _record_version, _version_content_payload
    from ..request_authority import check_request_authority
    from ..ws_dispatch import WsDispatchError, async_ws_command

    tool = "restore_configuration"
    domain, path = snapshot.get("domain"), snapshot.get("path")
    denied = _blueprint_denied(access_key, {"domain": domain}, tool)
    if denied is not None:
        return denied
    if (snapshot.get("schema") != 1 or domain not in ("automation", "script")
            or not isinstance(path, str) or type(snapshot.get("exists")) is not bool
            or snapshot.get("exists") != isinstance(snapshot.get("content"), str)):
        return _tool_error("The selected blueprint snapshot is incomplete."), "invalid_request", tool
    resolved = _resolve_blueprint_path(hass, domain, path)
    if resolved is None or resolved[1] != path:
        return _tool_error("The blueprint target is outside its current write jail."), "invalid_request", tool
    target_path = resolved[0]
    recovery = data.config_recovery
    resource = f"blueprint:{domain}/{path}"
    try:
        if content_hash(snapshot) != args.get("snapshot_digest"):
            return _tool_error("The selected blueprint snapshot changed after approval."), "invalid_request", tool
        expected_source = await hass.async_add_executor_job(_canonical_blueprint_source, domain, snapshot["content"]) if snapshot["exists"] else None
        async with recovery.resource(resource):
            before_text = await _read_blueprint_source(hass, target_path)
            before = _blueprint_recovery_snapshot(domain, path, before_text)
            if content_hash(before) != args.get("target_fingerprint"):
                return _tool_error("The blueprint target changed after approval."), "invalid_request", tool
            rid = await recovery.begin(resource, tool, access_key.id, before, source=snapshot, expected=snapshot)
            try:
                await recovery.mark(rid, "dispatching")
                check_request_authority()
                if snapshot["exists"]:
                    await async_ws_command(hass, "blueprint/save", {
                        "domain": domain, "path": path, "yaml": snapshot["content"],
                        "allow_override": before_text is not None,
                    })
                elif before_text is not None:
                    await async_ws_command(hass, "blueprint/delete", {"domain": domain, "path": path})
                observed = await _read_blueprint_source(hass, target_path)
                if observed != expected_source:
                    await recovery.mark(rid, "partial", observed_hash=content_hash(observed or ""))
                    return _tool_success(json.dumps({
                        "status": "partial", "recovery_id": rid,
                        "message": "Blueprint restore was submitted but readback differs; do not retry automatically.",
                    })), "allowed", tool
                await recovery.mark(rid, "verified", observed_hash=content_hash(observed or ""))
                await recovery.set_source(rid, _blueprint_recovery_snapshot(domain, path, observed))
                await _record_version(
                    data, access_key, resource_type="blueprint", resource_id=f"{domain}/{path}",
                    action="edit" if observed is not None else "delete",
                    before=_version_content_payload(before_text) if before_text is not None else None,
                    after=_version_content_payload(observed) if observed is not None else None,
                    alias=path,
                )
                return _tool_success(json.dumps({"status": "verified", "recovery_id": rid})), "allowed", tool
            except WsDispatchError:
                await recovery.mark(rid, "unknown", outcome="blueprint_ws_failed")
                return _tool_success(json.dumps({
                    "status": "unknown", "recovery_id": rid,
                    "message": "Blueprint restore outcome is unknown; inspect before any retry.",
                })), "allowed", tool
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - records remain unresolved if verification fails
        return _tool_error("Blueprint restoration failed; inspect configuration recovery before retrying."), "invalid_request", tool


async def _execute_restore_dashboard_layout(args, record, side, snapshot, access_key, hass, data):
    """Recheck the live dashboard and delegate to its locked layout adapter."""
    from .lovelace import _dashboard_layout_snapshot, _recoverable_dashboard_save
    from ..ws_dispatch import WsDashboardNotFoundError, async_get_lovelace_config
    from ..tool_common import _record_version

    tool = "restore_configuration"
    if effective_cap(access_key, "cap_lovelace_write") == "deny":
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    if (snapshot.get("schema") != 1 or snapshot.get("exists") is not True
            or not isinstance(snapshot.get("config"), dict)):
        return _tool_error("This dashboard layout snapshot has no restorable layout."), "invalid_request", tool
    url_path = snapshot.get("url_path")
    if url_path is not None and not isinstance(url_path, str):
        return _tool_error("The dashboard layout target is invalid."), "invalid_request", tool
    try:
        if content_hash(snapshot) != args.get("snapshot_digest"):
            return _tool_error("The selected dashboard snapshot changed after approval."), "invalid_request", tool
        try:
            before = await async_get_lovelace_config(hass, url_path)
        except WsDashboardNotFoundError:
            before = None
        target = _dashboard_layout_snapshot(url_path, before)
        if content_hash(target) != args.get("target_fingerprint"):
            return _tool_error("The dashboard layout changed after approval."), "invalid_request", tool
        check_request_authority()
        result = await _recoverable_dashboard_save(
            url_path=url_path, before=before, after=snapshot["config"],
            access_key=access_key, hass=hass, data=data, return_result=True,
        )
        if result is None or result[1] != "allowed":
            return result or (_tool_error("Dashboard layout restore was not applied."), "invalid_request", tool)
        if json.loads(result[0]["content"][0]["text"]).get("status") != "verified":
            return result
        await _record_version(
            data, access_key, resource_type="dashboard", resource_id=snapshot["resource_id"],
            action="edit", before=before, after=snapshot["config"],
            alias=url_path or "(default)",
        )
        return result
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - owning adapter keeps uncertain status in recovery
        return _tool_error("Dashboard layout restore failed; inspect configuration recovery before retrying."), "invalid_request", tool
