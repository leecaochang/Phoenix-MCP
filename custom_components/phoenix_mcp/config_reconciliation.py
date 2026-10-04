"""Observe uncertain configuration writes and route decisions through approvals."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging

from homeassistant.helpers import entity_registry as er

from .config_recovery import ConfigurationRecoveryError, recovery_resolution, runtime_identity, safe_snapshot
from .const import (CONFIG_RECOVERY_APPROVAL_CAP, CONFIG_RECOVERY_APPROVAL_ACCESS_KEY,
                    CONFIG_RECOVERY_UNRESOLVED, CONFIG_RECOVERY_OBSERVATION_TIMEOUT,
                    CONFIG_RECOVERY_SWEEP_BATCH, DOMAIN)
from .helpers import content_hash

_LOGGER = logging.getLogger(__name__)


async def _read_file(hass, path: str) -> str | None:
    """Only a missing path is absence; permission and read failures stay unknown."""
    from .tool_common import _read_text_capped
    try:
        return await hass.async_add_executor_job(_read_text_capped, path)
    except FileNotFoundError:
        return None


async def observe_configuration(hass, record: dict) -> dict:
    """Read current configuration through its owning adapter without any effects."""
    snapshot = record.get("expected") or record.get("source") or record.get("before")
    if not isinstance(snapshot, dict):
        raise ConfigurationRecoveryError("No supported recovery snapshot is available.")
    kind = snapshot.get("snapshot_type")
    if kind == "restart_receipt":
        return {"snapshot_type": kind, "runtime_identity": runtime_identity(hass)}
    if kind == "storage_helper":
        from .tools.helper import _storage_items, _public_helper_config
        from .ws_dispatch import async_ws_command
        helper_type = snapshot["helper_type"]
        helper_id = record.get("observations", {}).get("replacement_helper_id") or snapshot["helper_id"]
        rows = _storage_items(helper_type, await async_ws_command(hass, f"{helper_type}/list", {}))
        if rows is None:
            raise ConfigurationRecoveryError("Helper storage could not be read.")
        current = next((row for row in rows if row.get("id") == helper_id), None)
        return safe_snapshot({**snapshot, "helper_id": helper_id,
                              "config": _public_helper_config(helper_type, current) if current is not None else None}) or {}
    if kind == "template_helper":
        from .template_recovery import template_snapshot
        entry_id = record.get("observations", {}).get("replacement_entry_id") or snapshot["entry_id"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None:
            return template_snapshot(hass, entry)
        registry = er.async_get(hass)
        if er.async_entries_for_config_entry(registry, entry_id) or any(
            registry.async_get(entity["entity_id"]) is not None or hass.states.get(entity["entity_id"]) is not None
            for entity in (record.get("before") or record.get("source") or {}).get("entities", [])
        ):
            raise ConfigurationRecoveryError("The helper is absent but its entity identity is still occupied.")
        return {"snapshot_type": kind, "entry_id": entry_id, "exists": False}
    if kind in ("authored_file", "yaml_file"):
        from .tools.config_files import _authored_file_snapshot, _resolve_fs_path, _yaml_write_target
        rel = snapshot["resource_id"]
        if kind == "yaml_file":
            target = _yaml_write_target(hass, {"file": rel})
            path = target[0] if target is not None and target[1] == rel else None
        else:
            path = _resolve_fs_path(hass, rel)
        if path is None:
            raise ConfigurationRecoveryError("The recovery target is outside its current file jail.")
        text = await _read_file(hass, path)
        return await hass.async_add_executor_job(_authored_file_snapshot, rel, text is not None, text, kind)
    if kind == "blueprint_source":
        from .tools.blueprint import _resolve_blueprint_path, _blueprint_recovery_snapshot
        domain, rel = snapshot["domain"], snapshot["path"]
        if domain not in ("automation", "script"):
            raise ConfigurationRecoveryError("Unsupported blueprint domain.")
        target = _resolve_blueprint_path(hass, domain, rel)
        if target is None or target[1] != rel:
            raise ConfigurationRecoveryError("The blueprint target is outside its current file jail.")
        return _blueprint_recovery_snapshot(domain, rel, await _read_file(hass, target[0]))
    if kind == "energy_preferences":
        from .tools.energy import energy_recovery_target
        return await energy_recovery_target(hass)
    if kind == "dashboard_layout":
        from .tools.lovelace import _dashboard_layout_snapshot
        from .ws_dispatch import async_get_lovelace_config, WsDashboardNotFoundError
        try:
            config = await async_get_lovelace_config(hass, snapshot.get("url_path"))
        except WsDashboardNotFoundError:
            config = None
        return _dashboard_layout_snapshot(snapshot.get("url_path"), config)
    if kind == "dashboard_registry":
        from .tools.lovelace import _dashboard_registry_snapshot
        return await _dashboard_registry_snapshot(hass, snapshot["resource_id"])
    raise ConfigurationRecoveryError("This configuration has no observation adapter.")


def _matches(record: dict, observed: dict) -> bool:
    expected = deepcopy(record.get("expected"))
    before = record.get("before") or {}
    if before.get("snapshot_type") == "restart_receipt":
        return observed.get("runtime_identity") != before.get("runtime_identity")
    if not isinstance(expected, dict):
        return False
    if expected.get("snapshot_type") == "dashboard_registry":
        # HA allocates IDs and fills defaults on create. Every submitted or
        # already-present field must still match, not just entry existence.
        row = observed.get("dashboard") or {}
        return observed.get("exists") == expected.get("exists") and all(
            row.get(key) == value for key, value in (expected.get("dashboard") or {}).items()
        )
    if expected.get("snapshot_type") == "template_helper" and record.get("observations", {}).get("replacement_entry_id"):
        # The config flow allocates a new entry and unique ID. Keep the remaining
        # configured identity, title, options and compatibility fields exact.
        expected["entry_id"] = observed.get("entry_id")
        for entity, current in zip(expected.get("entities", []), observed.get("entities", [])):
            entity["unique_id"] = current.get("unique_id")
    return expected == observed


def is_recovery_approval(approval, data) -> bool:
    """Only system-created records with a durable back-reference get admin authority."""
    if approval.token_id != CONFIG_RECOVERY_APPROVAL_ACCESS_KEY or approval.cap_name != CONFIG_RECOVERY_APPROVAL_CAP:
        return False
    record = data.config_recovery.get(approval.args.get("recovery_id"))
    return record is not None and record.get("approval_id") == approval.id


async def _restore_receipt(hass, data, record: dict, side: str) -> tuple[dict, dict]:
    from .mcp_view import _restore_access_key
    from .tools.recovery import _tool_restore_configuration
    from .tool_common import _restore_ctx
    ctx = _restore_ctx.set({"user_id": "recovery_preview"})
    resolving = recovery_resolution.set(record["id"])
    try:
        args = {"recovery_id": record["id"], "side": side}
        result, outcome, _ = await _tool_restore_configuration(args, _restore_access_key("recovery_preview"), hass, data)
        if outcome != "allowed" or result.get("isError"):
            raise ConfigurationRecoveryError("This snapshot cannot currently be restored.")
        receipt = json.loads(result["content"][0]["text"])
        args.update({key: receipt[key] for key in ("snapshot_digest", "target_fingerprint")})
        return args, receipt["diff"]
    finally:
        recovery_resolution.reset(resolving)
        _restore_ctx.reset(ctx)


async def _queue_recovery(hass, data, record: dict, observed: dict | None) -> None:
    from .approvals import (async_create_pending_approval, create_approval_notification,
                           fire_approval_requested_event, PendingApprovalCapacityError, PendingApproval,
                           async_update_approval_status, dismiss_approval_notification, fire_approval_resolved_event)
    from .helpers import diff_summary_fields as _summary
    side = "before" if isinstance(record.get("before"), dict) else "source"
    snapshot = record.get(side)
    args: dict = {"recovery_id": record["id"], "side": side, "_resolution": "acknowledge",
            "_observed_hash": content_hash(observed) if observed is not None else None}
    diff: dict = {"kind": "system_action", "target": {"type": "configuration", "id": record["resource"]},
            "preview": {"saved": snapshot, "current": observed, "intended": record.get("expected")}}
    key = "configuration_recovery.acknowledge"
    if observed is not None and isinstance(snapshot, dict) and snapshot.get("snapshot_type") != "restart_receipt":
        try:
            bound, diff = await _restore_receipt(hass, data, record, side)
            args.update(bound, _resolution="restore")
            key = "configuration_recovery.restore"
        except Exception:
            # A refused restore is never advertised as an executable inverse.
            args["_resolution"] = "acknowledge"
    diff.update(_summary("configuration_recovery.restore" if key == "configuration_recovery.restore"
                         else "configuration_recovery.acknowledge", resource=record["resource"]))
    diff["preview"] = {**diff.get("preview", {}), "current": observed, "intended": record.get("expected")}
    replaced = None
    newly_created = False
    async with data.store.async_lock:
        current = data.config_recovery.get(record["id"])
        if current is None or current["phase"] not in CONFIG_RECOVERY_UNRESOLVED or not data.config_recovery.inactive(record["id"]):
            return
        existing = next((a for a in data.store.get_pending_approvals()
                         if a.get("token_id") == CONFIG_RECOVERY_APPROVAL_ACCESS_KEY
                         and a.get("status") == "pending" and a.get("args", {}).get("recovery_id") == record["id"]), None)
        if existing is not None:
            if existing["id"] in data.approvals_in_progress or existing.get("execution_started_at"):
                return
            if existing.get("args") != args:
                replaced = await async_update_approval_status(data.store, existing["id"], status="cancelled",
                    rejected_reason="Configuration changed; replaced by a fresh review.")
                existing = None
        if existing is not None:
            approval = PendingApproval.from_dict(existing)
        else:
            try:
                approval = await async_create_pending_approval(data.store,
                    token_id=CONFIG_RECOVERY_APPROVAL_ACCESS_KEY, token_name="Phoenix MCP",
                    tool_name="restore_configuration", cap_name=CONFIG_RECOVERY_APPROVAL_CAP,
                    args=args, diff=diff, request_id=record["id"])
                newly_created = True
            except PendingApprovalCapacityError:
                return
        await data.config_recovery.update(record["id"], approval_id=approval.id)
    if replaced is not None:
        dismiss_approval_notification(hass, replaced.id)
        fire_approval_resolved_event(hass, replaced)
    if approval.id not in data.config_recovery.announced_approvals:
        create_approval_notification(hass, approval)
        data.config_recovery.announced_approvals.add(approval.id)
    if newly_created:
        fire_approval_requested_event(hass, approval)


async def _close_resolved_approval(hass, data, record: dict) -> None:
    from .approvals import (get_approval, async_update_approval_status,
                           dismiss_approval_notification, fire_approval_resolved_event)
    async with data.store.async_lock:
        approval = get_approval(data.store, record.get("approval_id", ""))
        if approval is None or approval.is_terminal() or approval.id in data.approvals_in_progress or approval.execution_started_at:
            return
        updated = await async_update_approval_status(data.store, approval.id, status="cancelled",
            result={"configuration_reconciled": True, "execution_attribution": "unproven"})
    if updated is not None:
        dismiss_approval_notification(hass, updated.id)
        fire_approval_resolved_event(hass, updated)


async def reconcile_configurations(hass, data) -> None:
    """Lifecycle-owned readback sweep; never replay a write or infer attribution."""
    recovery = data.config_recovery
    if recovery.reconcile_lock.locked() or not data.ready or data.shutting_down:
        return
    async with recovery.reconcile_lock:
        records = recovery.records()
        for completed in records:
            if completed["phase"] not in CONFIG_RECOVERY_UNRESOLVED:
                await _close_resolved_approval(hass, data, completed)
        records = [r for r in records if r["phase"] in CONFIG_RECOVERY_UNRESOLVED]
        offset = recovery.reconcile_cursor % max(1, len(records))
        selected = (records[offset:] + records[:offset])[:CONFIG_RECOVERY_SWEEP_BATCH]
        recovery.reconcile_cursor = offset + len(selected)
        for record in selected:
            try:
                if not recovery.inactive(record["id"]):
                    continue
                if record["phase"] not in CONFIG_RECOVERY_UNRESOLVED:
                    await _close_resolved_approval(hass, data, record)
                    continue
                if record["phase"] == "prepared":
                    await recovery.mark(record["id"], "not_applied", verification="dispatch_not_started")
                    await _close_resolved_approval(hass, data, record)
                    continue
                observed = None
                async with recovery.resource(record["resource"]):
                    try:
                        observed = await asyncio.wait_for(observe_configuration(hass, record), CONFIG_RECOVERY_OBSERVATION_TIMEOUT)
                        if _matches(record, observed):
                            await recovery.set_source(record["id"], observed)
                            await recovery.mark(record["id"], "verified", verification="current_state_matches",
                                                execution_attribution="unproven")
                    except Exception:
                        # Unsupported, secret-bearing or temporarily unavailable
                        # reads are unknown, never absence or a successful write.
                        _LOGGER.debug("Configuration observation unavailable for %s", record["id"], exc_info=True)
                if recovery.get(record["id"])["phase"] not in CONFIG_RECOVERY_UNRESOLVED:
                    await _close_resolved_approval(hass, data, record)
                else:
                    await asyncio.wait_for(_queue_recovery(hass, data, record, observed), CONFIG_RECOVERY_OBSERVATION_TIMEOUT)
            except Exception:
                _LOGGER.warning("Configuration reconciliation failed for %s; will retry", record["id"], exc_info=True)
        await recovery.prune()


async def acknowledge_recovery(approval, user_id: str, hass, data, *, rejected: bool = False) -> None:
    """Accept the reviewed current state without modifying Home Assistant."""
    record = data.config_recovery.get(approval.args["recovery_id"])
    if record is None or record["phase"] not in CONFIG_RECOVERY_UNRESOLVED:
        return
    if not data.config_recovery.inactive(record["id"]):
        raise ConfigurationRecoveryError("The configuration operation is still active.")
    if rejected and approval.args.get("_resolution") == "acknowledge":
        return
    expected_hash = approval.args.get("_observed_hash")
    async with data.config_recovery.resource(record["resource"]):
        if expected_hash is not None:
            observed = await asyncio.wait_for(observe_configuration(hass, record), CONFIG_RECOVERY_OBSERVATION_TIMEOUT)
            if content_hash(observed) != expected_hash:
                raise ConfigurationRecoveryError("The configuration changed after review; review a fresh request.")
        await data.config_recovery.update(record["id"], phase="reviewed", reviewed_by=user_id,
                                         decision="keep_current" if rejected else "acknowledged")


async def execute_recovery_approval(approval, user, hass, data) -> tuple[dict, str, str]:
    """Apply only the system-created, reviewed restore, under current HA admin authority."""
    from .mcp_view import _restore_access_key
    from .request_authority import execution_guard, check_request_authority
    from .tool_common import _restore_ctx, _approved_exec_ctx, _tool_success, _tool_error
    from .tools.recovery import _execute_restore_configuration
    record = data.config_recovery.get(approval.args["recovery_id"])
    if record is None:
        return _tool_error("The recovery copy is no longer available."), "invalid_request", "restore_configuration"
    def guard():
        if hass.data.get(DOMAIN) is not data or not data.ready or data.shutting_down or not user.is_admin:
            raise ConfigurationRecoveryError("Administrator restoration authority changed.")
    ctx = _restore_ctx.set({"user_id": user.id})
    resolving = recovery_resolution.set(record["id"])
    guarded = execution_guard.set(guard)
    approved = _approved_exec_ctx.set(True)
    try:
        check_request_authority()
        if not data.config_recovery.inactive(record["id"]):
            raise ConfigurationRecoveryError("The configuration operation is still active.")
        if record["phase"] not in CONFIG_RECOVERY_UNRESOLVED:
            return _tool_success("The configuration has already been reconciled."), "allowed", record["resource"]
        mode = approval.args["_resolution"]
        if mode == "acknowledge":
            await acknowledge_recovery(approval, user.id, hass, data)
            return _tool_success("Current configuration acknowledged. No action was replayed."), "allowed", record["resource"]
        if content_hash(await observe_configuration(hass, record)) != approval.args["_observed_hash"]:
            raise ConfigurationRecoveryError("The configuration changed after review; review a fresh request.")
        restored = await _execute_restore_configuration(approval.args, _restore_access_key(user.id), hass, data)
        check_request_authority()
        if restored[0].get("isError"):
            return restored
        try:
            receipt = json.loads(restored[0]["content"][0]["text"])
        except (KeyError, IndexError, TypeError, ValueError):
            receipt = {}
        if isinstance(receipt, dict) and receipt.get("status") in ("partial", "unknown"):
            return _tool_error("The restore is incomplete; inspect its retained safety copy."), "invalid_request", record["resource"]
        unresolved = [r for r in data.config_recovery.records() if r["id"] != record["id"]
                      and r["resource"] == record["resource"] and r["phase"] in CONFIG_RECOVERY_UNRESOLVED]
        if unresolved:
            return _tool_error("The restore is incomplete; its safety copy has been retained."), "invalid_request", record["resource"]
        await data.config_recovery.reviewed(record["id"], user.id)
        return restored
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", "restore_configuration"
    finally:
        _approved_exec_ctx.reset(approved)
        execution_guard.reset(guarded)
        recovery_resolution.reset(resolving)
        _restore_ctx.reset(ctx)
