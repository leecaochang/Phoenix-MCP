"""Qualified Template helper snapshots and guarded configuration recovery."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.helpers import entity_registry as er

from .config_recovery import ConfigurationRecoveryError, safe_snapshot
from .const import TEMPLATE_RECOVERY_TYPES
from .request_authority import check_request_authority


@dataclass(frozen=True)
class TemplateRecoveryAdapter:
    """One HA Template platform whose config entry can be restored safely."""

    entity_domain: str
    action_capable: bool
    snapshot_fields: tuple[str, ...] = ("title", "version", "minor_version", "options", "entities")
    reference_extraction: str = "recursive_entity_id"
    identity_fields: tuple[str, ...] = ("entity_id", "unique_id", "platform", "disabled_by", "name")
    restore_procedure: str = "installed_template_config_and_options_flows"
    unsupported_states: tuple[str, ...] = ("non_json_or_secret_options", "multiple_or_unmapped_entities")


# HA executes these stored actions only after an entity is operated. Restoration
# only replays the config flow; authoring continues to validate their targets.
TEMPLATE_RECOVERY_ADAPTERS = {
    domain: TemplateRecoveryAdapter(domain, domain in {
        "alarm_control_panel", "button", "cover", "fan", "light", "lock",
        "number", "select", "switch", "update", "vacuum",
    })
    for domain in TEMPLATE_RECOVERY_TYPES
}


def template_snapshot(hass, entry) -> dict:
    """Capture supported configuration and exact entity identity without secrets."""
    adapter = TEMPLATE_RECOVERY_ADAPTERS.get(entry.options.get("template_type"))
    if (entry.domain != "template" or adapter is None
            or entry.data or getattr(entry, "subentries", {})):
        raise ConfigurationRecoveryError("This Template helper has no qualified recovery adapter; nothing was dispatched.")
    entities: list[dict[str, Any]] = [
        {"entity_id": e.entity_id, "unique_id": e.unique_id, "platform": e.platform,
         "name": e.name, "disabled_by": e.disabled_by.value if e.disabled_by else None}
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    ]
    if (len(entities) != 1 or entities[0]["platform"] != "template"
            or entities[0]["entity_id"].split(".")[0] != adapter.entity_domain):
        raise ConfigurationRecoveryError("Template helper entity mapping is incomplete; nothing was dispatched.")
    result = safe_snapshot({
        "snapshot_type": "template_helper", "schema": 1, "adapter": 1,
        "ha_version": HA_VERSION,
        "entry_id": entry.entry_id, "domain": entry.domain, "title": entry.title,
        "version": entry.version, "minor_version": entry.minor_version,
        "options": dict(entry.options), "entities": entities,
    })
    assert result is not None
    return result


def validate_snapshot(snapshot: dict) -> None:
    """Legacy and incompatible snapshots never silently lose identity fields."""
    safe_snapshot(snapshot)
    from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
    if (snapshot.get("snapshot_type") != "template_helper"
            or type(snapshot.get("schema")) is not int or snapshot.get("schema") != 1
            or snapshot.get("version") != TemplateConfigFlowHandler.VERSION
            or snapshot.get("minor_version") != TemplateConfigFlowHandler.MINOR_VERSION
            or snapshot.get("domain") != "template"
            or snapshot.get("adapter", 0) not in (0, 1)
            or snapshot.get("ha_version") != HA_VERSION
            or not isinstance(snapshot.get("entry_id"), str)
            or not isinstance(snapshot.get("title"), str)
            or not isinstance(snapshot.get("options"), dict)
            or snapshot["options"].get("template_type") not in TEMPLATE_RECOVERY_ADAPTERS
            or not isinstance(snapshot.get("entities"), list)
            or len(snapshot["entities"]) != 1):
        raise ConfigurationRecoveryError("Unsupported or incompatible Template recovery snapshot.")
    if snapshot.get("adapter", 0) == 0 and snapshot["options"].get("template_type") not in {
        "sensor", "binary_sensor",
    }:
        raise ConfigurationRecoveryError("Legacy Template recovery snapshot has no qualified type adapter.")
    entity = snapshot["entities"][0]
    if (not isinstance(entity, dict) or not isinstance(entity.get("entity_id"), str)
            or entity["entity_id"].split(".")[0] != TEMPLATE_RECOVERY_ADAPTERS[
                snapshot["options"]["template_type"]
            ].entity_domain
            or entity.get("platform") != "template"
            or not isinstance(entity.get("unique_id"), str)
            or entity.get("name") is not None and not isinstance(entity["name"], str)
            or entity.get("disabled_by") is not None):
        raise ConfigurationRecoveryError("Template entity mapping cannot be restored safely.")


def validate_restore_scope(snapshot: dict, token, hass) -> None:
    """Require current target and reference WRITE scope, including recreation."""
    from .policy_engine import Permission, resolve_registry_access
    from .tools.helper import _scoped_helper_create_scope_error, _unwritable_option_entities

    entity_id = snapshot["entities"][0]["entity_id"]
    if er.async_get(hass).async_get(entity_id) is not None or hass.states.get(entity_id) is not None:
        allowed = resolve_registry_access(entity_id, token, hass) == Permission.WRITE
    else:
        # A replacement is first materialized under HA's generated identity.
        allowed = _scoped_helper_create_scope_error(entity_id.split(".")[0], token) is None
        node = token.permissions.entities.get(entity_id)
        if not token.pass_through and node is not None and node.state in ("RED", "YELLOW"):
            allowed = False
    if not allowed or _unwritable_option_entities(snapshot["options"], token, hass):
        raise PermissionError("Forbidden.")


@asynccontextmanager
async def protect_template(hass, data, token, entry, operation: str, *, source=None, expected=None):
    """Persist a safety copy and revalidate before entering a mutation boundary."""
    recovery = data.config_recovery
    key = f"config_entry:{entry.entry_id}"
    async with recovery.resource(key):
        before = template_snapshot(hass, entry)
        if operation == "remove_integration":
            expected = {"snapshot_type": "template_helper", "entry_id": entry.entry_id, "exists": False}
        rid = await recovery.begin(key, operation, token.id, before, source=source, expected=expected or source)
        try:
            await recovery.mark(rid, "dispatching")
            check_request_authority()
            current = hass.config_entries.async_get_entry(entry.entry_id)
            if current is not entry or template_snapshot(hass, current) != before:
                await recovery.mark(rid, "not_applied")
                raise ConfigurationRecoveryError("The helper changed while saving its safety copy; nothing was dispatched.")
            yield rid
        finally:
            recovery.leave(rid)


async def restore_template(
    snapshot: dict, token, hass, data, *, mesa_fingerprint: str | None = None,
) -> tuple[dict, str, str]:
    """Restore one qualified helper through HA flows, retaining partial effects."""
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType
    from .tools.helper import (
        _HELPER_MESA_FINGERPRINT, _apply_config_entry_options, _helper_mesa_context,
        _helper_mesa_error, _settings_form_payload, _unwritable_option_entities,
    )
    from .tool_common import _record_version, _tool_error, _tool_success
    import json

    tool = "restore_template"
    rid = None
    flow_id = None
    entry = None
    recovery = data.config_recovery
    try:
        validate_snapshot(snapshot)
        validate_restore_scope(snapshot, token, hass)
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
            async with protect_template(hass, data, token, current, tool, source=snapshot) as rid:
                result = await _apply_config_entry_options(
                    {"entry_id": original_id, "settings": settings, _HELPER_MESA_FINGERPRINT: mesa_fingerprint},
                    token, hass, data, record_history=False, recovery_before=existing,
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
                await _record_version(data, token, resource_type="config_entry", resource_id=original_id,
                                      action="edit", before=existing, after=template_snapshot(hass, current), alias=current.title)
                return result
        if _unwritable_option_entities(options, token, hass):
            raise ConfigurationRecoveryError("Template references are outside current write authority.")
        mesa = _helper_mesa_context(data, token, [expected["entity_id"]], action="create",
                                   service_data={"helper_type": "template"}, session_id=tool)
        if isinstance(mesa, tuple):
            return mesa
        if mesa.decision == "deny":
            return _helper_mesa_error(mesa)
        registry = er.async_get(hass)

        def absent():
            check_request_authority()
            validate_restore_scope(snapshot, token, hass)
            if (hass.config_entries.async_get_entry(original_id) is not None
                    or registry.async_get(expected["entity_id"]) is not None
                    or hass.states.get(expected["entity_id"]) is not None):
                raise ConfigurationRecoveryError("The saved helper identity is occupied; nothing was created.")

        async with recovery.resource(f"config_entry:{original_id}"):
            absent()
            result = await hass.config_entries.flow.async_init("template", context={"source": SOURCE_USER})
            flow_id = result.get("flow_id")
            if result.get("type") != FlowResultType.MENU:
                raise ConfigurationRecoveryError("Unsupported Template creation flow.")
            result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": options["template_type"]})
            if result.get("type") != FlowResultType.FORM or result.get("step_id") != options["template_type"]:
                raise ConfigurationRecoveryError("Unsupported Template creation form.")
            supplied = {k: v for k, v in options.items() if k != "template_type"}
            payload = _settings_form_payload(result, supplied)
            # Validate all fields with HA's actual current creation schema.
            payload = result["data_schema"](payload)
            rid = await recovery.begin(f"config_entry:{original_id}", tool, token.id, None, source=snapshot, expected=snapshot)
            await recovery.mark(rid, "dispatching")
            absent()
            fresh_mesa = _helper_mesa_context(data, token, [expected["entity_id"]], action="create",
                                             service_data={"helper_type": "template"}, session_id=tool)
            if isinstance(fresh_mesa, tuple) or fresh_mesa != mesa:
                await recovery.mark(rid, "not_applied")
                raise ConfigurationRecoveryError("Template safety policy changed before creation.")
            result = await hass.config_entries.flow.async_configure(flow_id, payload)
            if result.get("type") != FlowResultType.CREATE_ENTRY or result.get("result") is None:
                await recovery.mark(rid, "unknown")
                raise ConfigurationRecoveryError("Template creation did not return a verified entry; inspect Home Assistant before retrying.")
            entry = result["result"]
            await recovery.mark(rid, "partial", replacement_entry_id=entry.entry_id)
            check_request_authority()
            created = er.async_entries_for_config_entry(registry, entry.entry_id)
            if (len(created) != 1 or created[0].platform != "template"
                    or created[0].domain != options["template_type"]
                    or dict(entry.options) != options):
                raise ConfigurationRecoveryError("The replacement helper is incomplete; inspect it before retrying.")
            entity = created[0]
            from .policy_engine import Permission, resolve_registry_access
            validate_restore_scope(snapshot, token, hass)
            if resolve_registry_access(entity.entity_id, token, hass) != Permission.WRITE:
                raise PermissionError("Forbidden.")
            post_mesa = _helper_mesa_context(data, token, [entity.entity_id, expected["entity_id"]], action="create",
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
            await _record_version(data, token, resource_type="config_entry", resource_id=entry.entry_id,
                                  action="create", before=None, after=after, alias=entry.title)
            return _tool_success(json.dumps({"recovery_id": rid, "status": "verified",
                "entry_id": entry.entry_id, "original_entry_id": original_id,
                "entity_id": expected["entity_id"], "retry_safe": False})), "allowed", tool
    except PermissionError:
        return _tool_error("Forbidden."), "denied", tool
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else "Template recovery failed; inspect the recovery record before retrying."
        return _tool_error(json.dumps({"error": message, "recovery_id": rid,
            "replacement_entry_id": entry.entry_id if entry is not None else None,
            "status": "partial" if entry is not None else "unknown" if rid else "not_applied",
            "retry_safe": False})), "invalid_request", tool
    finally:
        if rid:
            recovery.leave(rid)
        if flow_id:
            try:
                hass.config_entries.flow.async_abort(flow_id)
            except Exception:
                pass
