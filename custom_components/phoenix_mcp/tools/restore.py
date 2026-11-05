"""Restore a recorded configuration version."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from .. import yaml_includes
from ..access_key_store import AccessKeyRecord
from ..config_recovery import recovery_source, ConfigurationRecoveryError
from ..const import CAP_ALLOW, CAPABILITY_NAMES
from ..data import PhoenixData
from ..helpers import content_hash
from ..template_recovery import restore_template
from ..template_snapshots import validate_snapshot
from ..tool_common import _restore_ctx, _tool_error, _tool_success
from ..ws_dispatch import async_ws_command
from .authoring import (
    _execute_create_automation, _execute_create_scene, _execute_create_script,
    _execute_edit_automation, _execute_edit_scene, _execute_edit_script,
)
from .blueprint import (
    _execute_create_blueprint, _execute_edit_blueprint, _read_blueprint_source,
    _resolve_blueprint_path,
)
from .energy import async_restore_energy_prefs
from .helper import (
    _execute_create_helper, _execute_edit_helper, _execute_set_config_entry_options,
    _HELPER_RESTORE_ID, _read_helper_config, _helper_create_mesa_entity,
    _enforce_helper_mesa_context, _HELPER_RESTORE_REPLACE, _storage_items,
)
from .integrations import _CONFIG_ENTRY_METADATA_SNAPSHOT, _execute_set_integration
from .labels import restore_label_version
from .locations import restore_location_version
from .lovelace import _execute_set_dashboard_config
from .recovery import _execute_restore_configuration, _tool_restore_configuration
from .registry import _execute_set_device, _execute_set_entity


def _restore_access_key(user_id: str) -> AccessKeyRecord:
    """Synthetic pass-through access key for re-applying a config under admin authority.

    pass_through makes policy_engine.resolve() return WRITE for every entity, so the
    per-entity scope checks inside the scene/helper executors pass for an admin who
    has full authority. Every capability is set to allow explicitly: pass_through
    only promotes deny for non-exempt caps, and the blueprint executors check the
    pass-through-EXEMPT domain caps (cap_automation_write / cap_script_write)
    inside the executor, which would refuse an admin restore on the defaults.
    It is never persisted and never authenticates a request.
    """
    return AccessKeyRecord(
        id=f"__restore__:{user_id}",
        name="(admin restore)",
        access_key_hash="",
        created_at=utcnow(),
        created_by=user_id,
        pass_through=True,
        **{cap: CAP_ALLOW for cap in CAPABILITY_NAMES},  # type: ignore[arg-type]  # every CAPABILITY_NAMES entry is a cap_* str field
    )


async def _resource_exists(hass: HomeAssistant, resource_type: str, resource_id: str) -> bool:
    """Whether a versioned resource currently exists (picks restore edit vs recreate)."""
    try:
        if resource_type in ("automation", "script", "scene"):
            current = await hass.async_add_executor_job(
                yaml_includes.read_entry, hass.config.config_dir, resource_type, resource_id,
            )
            return current is not None
        if resource_type == "helper":
            ht, _, hid = resource_id.partition(":")
            rows = _storage_items(ht, await async_ws_command(hass, f"{ht}/list", {}))
            if rows is None:
                raise ValueError("Unreadable helper storage")
            return any(row.get("id") == hid for row in rows)
    except Exception:
        raise ConfigurationRecoveryError("Current configuration could not be read; restore was not dispatched.") from None
    return False


async def async_restore_configuration(
    recovery_id: str, side: str, admin_user_id: str, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Restore a recovery snapshot following an authenticated administrator review."""
    access_key = _restore_access_key(admin_user_id)
    ctx = _restore_ctx.set({"user_id": admin_user_id})
    try:
        args = {"recovery_id": recovery_id, "side": side}
        receipt = await _tool_restore_configuration(args, access_key, hass, data)
        if receipt[1] != "allowed" or receipt[0].get("isError"):
            return receipt
        body = json.loads(receipt[0]["content"][0]["text"])
        args.update({key: body[key] for key in ("snapshot_digest", "target_fingerprint")})
        return await _execute_restore_configuration(args, access_key, hass, data)
    finally:
        _restore_ctx.reset(ctx)


def restore_refusal(record, side: str, hass: HomeAssistant) -> str | None:
    """Why async_restore_version would refuse this side whatever the item holds now, or None.

    Returns a reason slug: yaml_tags, audit_only, template_incompatible or
    entity_missing. These are the refusals that do not depend on the item's
    current content, so Changes can say why before Restore is chosen. A missing
    side returns None; the panel explains that itself.
    """
    target = record.before if side == "before" else record.after
    if not isinstance(target, dict):
        return None
    resource_type = record.resource_type
    if resource_type in ("automation", "script", "scene") and yaml_includes.contains_tag_strings(target):
        return "yaml_tags"
    if resource_type in ("config_entry", "device") and target.get("restorable") is False:
        return "audit_only"
    if resource_type == "config_entry" and target.get("snapshot_type") == "template_helper":
        try:
            validate_snapshot(target)
        except ValueError:  # ConfigurationRecoveryError is a ValueError
            return "template_incompatible"
    if resource_type == "entity":
        # The same ids the restore follows a rename through; a deleted registry
        # entry cannot be recreated.
        candidates = [
            snapshot["entity_id"] for snapshot in (record.before, record.after)
            if isinstance(snapshot, dict) and isinstance(snapshot.get("entity_id"), str)
        ] + [record.resource_id]
        registry = er.async_get(hass)
        if all(registry.async_get(candidate) is None for candidate in candidates):
            return "entity_missing"
    return None


async def async_restore_version(
    record: Any, admin_user_id: str, hass: HomeAssistant, data: PhoenixData, side: str | None = None
) -> tuple[dict, str, str]:
    """Re-apply a stored config version under admin authority."""
    if side not in ("before", "after"):
        side = "after" if record.after is not None else "before"
    target = record.before if side == "before" else record.after
    if not isinstance(target, dict):
        return _tool_error("This version has no configuration to restore on that side."), "invalid_request", "async_restore_version"
    # The executors manage ids themselves; a stored config may carry one (e.g. a
    # deleted helper's full item), so drop it before re-applying.
    target = {k: v for k, v in target.items() if k != "id"}

    resource_type = record.resource_type
    resource_id = record.resource_id
    if resource_type in ("automation", "script", "scene") and yaml_includes.contains_tag_strings(target):
        # The stored snapshot flattened YAML tags to display strings ("!secret x");
        # writing them back as quoted strings would silently break the config.
        # Fail closed, like the oversized-snapshot marker below.
        return _tool_error(
            "This version contains YAML tags such as !secret or !include and "
            "cannot be restored automatically. Re-create it manually."
        ), "invalid_request", "async_restore_version"
    access_key = _restore_access_key(admin_user_id)
    try:
        exists = await _resource_exists(hass, resource_type, resource_id)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", "async_restore_version"

    source_ctx = recovery_source.set(target)
    ctx = _restore_ctx.set({"user_id": admin_user_id})
    try:
        if resource_type == "automation":
            if exists:
                current = await hass.async_add_executor_job(
                    yaml_includes.read_entry, hass.config.config_dir, "automation", resource_id,
                )
                return await _execute_edit_automation({
                    "automation_id": resource_id, "config": target,
                    "expected_hash": content_hash(current),
                }, access_key, hass, data)
            # Recreate in place under the original id so the rollback lands on the
            # same timeline and a second restore just edits it.
            return await _execute_create_automation({"config": target, "automation_id": resource_id}, access_key, hass, data)
        if resource_type == "script":
            if exists:
                return await _execute_edit_script({"script_id": resource_id, "config": target}, access_key, hass, data)
            return await _execute_create_script({"script_id": resource_id, "config": target}, access_key, hass, data)
        if resource_type == "scene":
            if exists:
                return await _execute_edit_scene({"scene_id": resource_id, "config": target}, access_key, hass, data)
            return await _execute_create_scene({"config": target, "scene_id": resource_id}, access_key, hass, data)
        if resource_type == "helper":
            ht, _, hid = resource_id.partition(":")
            if ht == "tag":
                # Scan timestamps and scanner device ids are operational evidence,
                # not authorable configuration. Never rewind them from version
                # history. A deleted tag is recreated under its original stable
                # physical identifier; an existing tag edit must not receive the
                # create-only tag_id field.
                target = {
                    key: target[key]
                    for key in ("name", "description")
                    if key in target
                }
                if not exists:
                    target["tag_id"] = hid
            elif ht == "person":
                # Person versions deliberately store only privacy-safe booleans
                # for HA user/picture bindings. Existing-person rollback preserves
                # those private fields by omission; deleted-person recreation is
                # intentionally unlinked and pictureless rather than persisting a
                # raw HA user id or private path in Changes history.
                target = {
                    key: target[key]
                    for key in ("name", "device_trackers")
                    if key in target
                }
            recovery_source.set({"snapshot_type": "storage_helper", "helper_type": ht, "helper_id": hid, "config": target})
            if exists:
                # A before side is the whole stored item. A zone or tag edit's
                # after side holds only the fields that edit changed, so it is
                # applied as that edit again rather than as a replacement.
                complete = side == "before" or getattr(record, "action", None) != "edit" or ht not in ("zone", "tag")
                return await _execute_edit_helper({"helper_type": ht, "helper_id": hid, "config": target,
                                                   _HELPER_RESTORE_REPLACE: complete}, access_key, hass, data)
            create_args = {"helper_type": ht, "config": target, _HELPER_RESTORE_ID: hid}
            mesa_precheck = _enforce_helper_mesa_context(create_args, access_key, hass, data,
                [_helper_create_mesa_entity(ht, create_args, hass)], action="create",
                service_data={"helper_type": ht, "config": target, "flow_steps": None},
                session_id="create_helper_execute")
            if isinstance(mesa_precheck, tuple):
                return mesa_precheck
            rid = None
            try:
                async with data.config_recovery.resource(f"helper:{resource_id}"):
                    rid = await data.config_recovery.begin(f"helper:{resource_id}", "restore_helper", access_key.id, None,
                        source={"snapshot_type": "storage_helper", "helper_type": ht, "helper_id": hid, "config": target})
                    await data.config_recovery.mark(rid, "dispatching")
                    if await _resource_exists(hass, "helper", resource_id):
                        await data.config_recovery.mark(rid, "not_applied")
                        return _tool_error("Helper identity became occupied before restoration."), "invalid_request", "async_restore_version"
                    restored = await _execute_create_helper(
                        {"helper_type": ht, "config": target, _HELPER_RESTORE_ID: hid}, access_key, hass, data,
                    )
                    if restored[0].get("isError"):
                        await data.config_recovery.mark(rid, "unknown")
                    else:
                        body = json.loads(restored[0]["content"][0]["text"])
                        new_id = body["helper"]["id"]
                        observed = await _read_helper_config(hass, ht, new_id)
                        # Each restored field must read back as saved; tag_id is
                        # the tag's identity, stored as its id.
                        verified = isinstance(observed, dict) and all(
                            observed.get(key) == value for key, value in target.items() if key != "tag_id"
                        )
                        await data.config_recovery.mark(rid, "verified" if verified else "partial", replacement_helper_id=new_id)
                        if not verified:
                            return _tool_error(json.dumps({"status": "partial", "recovery_id": rid,
                                "replacement_helper_id": new_id, "retry_safe": False})), "invalid_request", "async_restore_version"
                        body.update(recovery_id=rid, status="verified", retry_safe=False)
                        restored = (_tool_success(json.dumps(body)), restored[1], restored[2])
                    return restored
            except Exception:
                return _tool_error("Helper restoration was not verified. Inspect configuration recovery before retrying."), "invalid_request", "async_restore_version"
            finally:
                if rid is not None:
                    data.config_recovery.leave(rid)
        if resource_type == "dashboard":
            # Dashboards are edit-only: re-apply the layout to the existing dashboard
            # (resource_id "lovelace" is the default dashboard, url_path None).
            return await _execute_set_dashboard_config(
                {"url_path": None if resource_id == "lovelace" else resource_id, "config": target},
                access_key, hass, data,
            )
        if resource_type == "config_entry":
            if target.get("snapshot_type") == "template_helper":
                return await restore_template(target, access_key, hass, data)
            if target.get("restorable") is False:
                return _tool_error(
                    "This integration version is a non-restorable audit record and cannot be restored. Phoenix "
                    "does not store raw integration data or provide automatic rollback."
                ), "invalid_request", "async_restore_version"
            if target.get("snapshot_type") == _CONFIG_ENTRY_METADATA_SNAPSHOT:
                fields = {
                    key: target[key]
                    for key in (
                        "title",
                        "pref_disable_new_entities",
                        "pref_disable_polling",
                        "disabled_by",
                    )
                    if key in target
                }
                return await _execute_set_integration(
                    {"entry_id": resource_id, **fields}, access_key, hass, data
                )
            # Re-runs the helper's own options flow with the snapshot, so HA
            # validates the restored settings exactly as it validated the write.
            # No expected_hash: a restore deliberately overwrites whatever is
            # there now, which is the whole point of choosing a snapshot.
            return await _execute_set_config_entry_options(
                {"entry_id": resource_id, "settings": target}, access_key, hass, data,
            )
        if resource_type == "energy":
            # Energy is restore-only and deliberately WHOLESALE: the snapshot IS
            # the operator's chosen state, so reproducing it is the operation. The
            # addressed-write rule edit_energy_config enforces does not apply here
            # (a YAML restore is likewise exempt from the dropped-key check).
            return await async_restore_energy_prefs(target, access_key, hass, data)
        if resource_type in ("yaml_config", "file", "esphome_yaml"):
            # Configuration files belong to the operator. Their history stays
            # readable, but a version is never written back to disk.
            return _tool_error(
                "This version holds configuration file text, which Phoenix does not write "
                "back. Copy the content from this version and apply it by hand."
            ), "invalid_request", "async_restore_version"
        if resource_type == "blueprint":
            restorable = target.get("content")
            if not isinstance(restorable, str):
                # Oversized snapshots are stored as a non-restorable marker.
                return _tool_error("This version is too large to restore inline."), "invalid_request", "async_restore_version"
            # resource_id is "<domain>/<config-relative path>". Route through
            # the executors so the path jail, schema validation, and consumer
            # reload all apply; edit when the file exists, recreate otherwise.
            bp_domain, _, bp_rel = resource_id.partition("/")
            resolved = await hass.async_add_executor_job(_resolve_blueprint_path, hass, bp_domain, bp_rel)
            bp_exists = (
                resolved is not None
                and await _read_blueprint_source(hass, resolved[0]) is not None
            )
            bp_args = {"domain": bp_domain, "path": bp_rel, "content": restorable}
            if bp_exists:
                return await _execute_edit_blueprint(bp_args, access_key, hass, data)
            return await _execute_create_blueprint(bp_args, access_key, hass, data)
        if resource_type in {"area", "floor"}:
            return await restore_location_version(resource_type, resource_id, target, access_key, hass, data)
        if resource_type in {"label", "category"}:
            return await restore_label_version(resource_type, resource_id, target, access_key, hass, data)
        if resource_type == "entity":
            # Re-apply the registry metadata. Restoring a deleted entry's snapshot
            # lands here too and fails cleanly in set_entity, because a deleted
            # registry entry cannot be recreated.
            fields = {
                key: target[key]
                for key in (
                    "name",
                    "icon",
                    "area_id",
                    "device_class",
                    "aliases",
                    "labels",
                    "categories",
                    "disabled_by",
                    "hidden_by",
                )
                if key in target
            }
            desired_entity_id = target.get("entity_id")
            candidate_ids: list[str] = []
            for snapshot in (record.before, record.after):
                if isinstance(snapshot, dict) and isinstance(
                    snapshot.get("entity_id"), str
                ):
                    candidate_ids.append(snapshot["entity_id"])
            candidate_ids.append(resource_id)
            current_entity_id = next(
                (
                    candidate
                    for candidate in candidate_ids
                    if er.async_get(hass).async_get(candidate) is not None
                ),
                resource_id,
            )
            if (
                isinstance(desired_entity_id, str)
                and desired_entity_id != current_entity_id
            ):
                fields["new_entity_id"] = desired_entity_id
            if not fields:
                return _tool_error("This version has no entity metadata to restore."), "invalid_request", "async_restore_version"
            return await _execute_set_entity(
                {"entity_id": current_entity_id, **fields}, access_key, hass, data
            )
        if resource_type == "device":
            if target.get("restorable") is False:
                return _tool_error(
                    "This device version is an audit record and cannot be restored automatically."
                ), "invalid_request", "async_restore_version"
            fields = {
                key: target[key]
                for key in ("name", "area_id", "labels", "disabled_by")
                if key in target
            }
            if not fields:
                return _tool_error(
                    "This version has no device metadata to restore."
                ), "invalid_request", "async_restore_version"
            return await _execute_set_device(
                {"device_id": resource_id, **fields}, access_key, hass, data
            )
        return _tool_error(f"Cannot restore resource type '{resource_type}'."), "invalid_request", "async_restore_version"
    finally:
        _restore_ctx.reset(ctx)
        recovery_source.reset(source_ctx)
