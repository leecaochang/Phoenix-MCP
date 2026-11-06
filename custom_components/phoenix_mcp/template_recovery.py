"""Qualified Template helper snapshots and guarded configuration recovery."""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any, cast

from homeassistant.helpers import entity_registry as er
from homeassistant.core import HomeAssistant
import json
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.config_entries import SOURCE_USER

from .config_recovery import ConfigurationRecoveryError
from .policy_engine import Permission, resolve_registry_access
from .request_authority import check_request_authority
from .template_snapshots import protect_template, template_snapshot, validate_snapshot
from .tool_common import _record_version, _tool_error, _tool_success, _tool_uncertain
from .tools.helper import (
    _HELPER_MESA_FINGERPRINT, _apply_config_entry_options, _helper_mesa_context, _helper_mesa_error,
    _settings_form_payload, _unwritable_option_entities, _scoped_helper_create_scope_error,
)

_LOGGER = logging.getLogger(__name__)


def validate_restore_scope(snapshot: dict, access_key, hass: HomeAssistant) -> None:
    """Require current WRITE scope over the target entity and its references.

    Covers recreation of a missing helper. Raises PermissionError.
    """

    entity_id = snapshot["entities"][0]["entity_id"]
    if er.async_get(hass).async_get(entity_id) is not None or hass.states.get(entity_id) is not None:
        allowed = resolve_registry_access(entity_id, access_key, hass) == Permission.WRITE
    else:
        # A replacement is first materialized under HA's generated identity.
        allowed = _scoped_helper_create_scope_error(entity_id.split(".")[0], access_key) is None
        node = access_key.permissions.entities.get(entity_id)
        if not access_key.pass_through and node is not None and node.state in ("RED", "YELLOW"):
            allowed = False
    if not allowed or _unwritable_option_entities(snapshot["options"], access_key, hass, template_type=snapshot["options"]["template_type"]):
        raise PermissionError("Forbidden.")


async def restore_template(
    snapshot: dict, access_key, hass: HomeAssistant, data, *, mesa_fingerprint: str | None = None,
) -> tuple[dict, str, str]:
    """Restore one qualified helper through HA flows, retaining partial effects.

    Returns (payload, audit outcome, tool name). Nothing is rolled back on a
    partial failure; the recovery record carries its status for review.
    """

    tool = "restore_template"
    rid = None
    flow_id = None
    entry = None
    recovery = data.config_recovery
    try:
        validate_snapshot(snapshot)
        validate_restore_scope(snapshot, access_key, hass)
        original_id = snapshot["entry_id"]
        expected = snapshot["entities"][0]
        options = deepcopy(snapshot["options"])
        current = hass.config_entries.async_get_entry(original_id)
        if current is not None:
            existing = template_snapshot(hass, current)
            identity_keys = ("entity_id", "unique_id", "platform", "disabled_by")
            if (existing["version"] != snapshot["version"]
                    or existing["minor_version"] != snapshot["minor_version"]
                    or any(existing["entities"][0][key] != expected[key] for key in identity_keys)
                    or current.options.get("name") != options.get("name")):
                raise ConfigurationRecoveryError("The existing helper identity differs from the recovery snapshot.")
            settings = {k: v for k, v in options.items() if k not in ("template_type", "name")}
            settings.update({k: None for k in current.options if k not in options})
            async with protect_template(hass, data, access_key, current, tool, source=snapshot) as rid:
                result = await _apply_config_entry_options(
                    {"entry_id": original_id, "settings": settings, _HELPER_MESA_FINGERPRINT: mesa_fingerprint},
                    access_key, hass, data, record_history=False, recovery_before=existing,
                )
                check_request_authority()
                if result[0].get("isError") or dict(current.options) != options:
                    await recovery.mark(rid, "partial", entry_id=original_id)
                    raise ConfigurationRecoveryError("The saved Template options were not fully reproduced; inspect recovery before retrying.")
                registry = er.async_get(hass)
                entity = registry.async_get(expected["entity_id"])
                if entity is None or entity.config_entry_id != original_id or entity.unique_id != expected["unique_id"]:
                    raise ConfigurationRecoveryError("Template registry identity changed during restoration.")
                registry.async_update_entity(entity.entity_id, name=expected["name"])
                hass.config_entries.async_update_entry(current, title=snapshot["title"])
                await recovery.mark(rid, "verified", entry_id=original_id)
                await _record_version(data, access_key, resource_type="config_entry", resource_id=original_id,
                                      action="edit", before=existing, after=template_snapshot(hass, current), alias=current.title)
                return result
        if _unwritable_option_entities(options, access_key, hass, template_type=options["template_type"]):
            raise ConfigurationRecoveryError("Template references are outside current write authority.")
        mesa = _helper_mesa_context(data, access_key, [expected["entity_id"]], action="create",
                                   service_data={"helper_type": "template"}, session_id=tool)
        if isinstance(mesa, tuple):
            return mesa
        if mesa.decision == "deny":
            return _helper_mesa_error(mesa)
        registry = er.async_get(hass)

        def absent():
            check_request_authority()
            validate_restore_scope(snapshot, access_key, hass)
            if (hass.config_entries.async_get_entry(original_id) is not None
                    or registry.async_get(expected["entity_id"]) is not None
                    or hass.states.get(expected["entity_id"]) is not None):
                raise ConfigurationRecoveryError("The saved helper identity is occupied; nothing was created.")

        async with recovery.resource(f"config_entry:{original_id}"):
            absent()
            flow_result = await hass.config_entries.flow.async_init("template", context={"source": SOURCE_USER})
            flow_id = flow_result.get("flow_id")
            if flow_result.get("type") != FlowResultType.MENU:
                raise ConfigurationRecoveryError("Unsupported Template creation flow.")
            flow_result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": options["template_type"]})
            if flow_result.get("type") != FlowResultType.FORM or flow_result.get("step_id") != options["template_type"]:
                raise ConfigurationRecoveryError("Unsupported Template creation form.")
            supplied = {k: v for k, v in options.items() if k != "template_type"}
            payload = _settings_form_payload(flow_result, supplied)
            # Validate all fields with HA's actual current creation schema.
            payload = cast(Any, flow_result["data_schema"])(payload)
            rid = await recovery.begin(f"config_entry:{original_id}", tool, access_key.id, None, source=snapshot, expected=snapshot)
            await recovery.mark(rid, "dispatching")
            absent()
            fresh_mesa = _helper_mesa_context(data, access_key, [expected["entity_id"]], action="create",
                                             service_data={"helper_type": "template"}, session_id=tool)
            if isinstance(fresh_mesa, tuple) or fresh_mesa != mesa:
                await recovery.mark(rid, "not_applied")
                raise ConfigurationRecoveryError("Template safety policy changed before creation.")
            flow_result = await hass.config_entries.flow.async_configure(flow_id, payload)
            if flow_result.get("type") != FlowResultType.CREATE_ENTRY or flow_result.get("result") is None:
                await recovery.mark(rid, "unknown")
                raise ConfigurationRecoveryError("Template creation did not return a verified entry; inspect Home Assistant before retrying.")
            entry = flow_result["result"]
            await recovery.mark(rid, "partial", replacement_entry_id=entry.entry_id)
            check_request_authority()
            created = er.async_entries_for_config_entry(registry, entry.entry_id)
            if (len(created) != 1 or created[0].platform != "template"
                    or created[0].domain != options["template_type"]
                    or dict(entry.options) != options):
                raise ConfigurationRecoveryError("The replacement helper is incomplete; inspect it before retrying.")
            entity = created[0]
            validate_restore_scope(snapshot, access_key, hass)
            if resolve_registry_access(entity.entity_id, access_key, hass) != Permission.WRITE:
                raise PermissionError("Forbidden.")
            post_mesa = _helper_mesa_context(data, access_key, [entity.entity_id, expected["entity_id"]], action="create",
                                            service_data={"helper_type": "template"}, session_id=tool)
            if isinstance(post_mesa, tuple) or post_mesa.decision == "deny":
                raise ConfigurationRecoveryError("The replacement helper requires manual safety review.")
            occupied = registry.async_get(expected["entity_id"])
            if ((occupied is not None and occupied.id != entity.id)
                    or entity.entity_id != expected["entity_id"] and hass.states.get(expected["entity_id"]) is not None):
                raise ConfigurationRecoveryError("The saved entity ID became occupied; the replacement was retained.")
            registry.async_update_entity(entity.entity_id, new_entity_id=expected["entity_id"], name=expected["name"])
            hass.config_entries.async_update_entry(entry, title=snapshot["title"])
            after = template_snapshot(hass, entry)
            await recovery.mark(rid, "verified", replacement_entry_id=entry.entry_id,
                                entity_id=expected["entity_id"])
            await _record_version(data, access_key, resource_type="config_entry", resource_id=entry.entry_id,
                                  action="create", before=None, after=after, alias=entry.title)
            return _tool_success(json.dumps({"recovery_id": rid, "status": "verified",
                "entry_id": entry.entry_id, "original_entry_id": original_id,
                "entity_id": expected["entity_id"], "retry_safe": False})), "allowed", tool
    except PermissionError:
        return _tool_error("Forbidden."), "denied", tool
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else "Template recovery failed; inspect the recovery record before retrying."
        body = {"error": message, "recovery_id": rid,
                "replacement_entry_id": entry.entry_id if entry is not None else None,
                "status": "partial" if entry is not None else "unknown" if rid else "not_applied",
                "retry_safe": False}
        return (_tool_uncertain(body) if body["status"] != "not_applied" else _tool_error(json.dumps(body)),
                "invalid_request", tool)
    finally:
        if rid:
            recovery.leave(rid)
        if flow_id:
            try:
                hass.config_entries.flow.async_abort(flow_id)
            except Exception:
                _LOGGER.debug("Could not abort the helper flow", exc_info=True)
