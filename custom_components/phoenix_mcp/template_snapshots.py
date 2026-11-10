"""Snapshot a Template helper and guard a change to it."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.importlib import async_import_module
from homeassistant.helpers import entity_registry as er

from .component_guard import ComponentNotLoaded, imported_module
from .config_recovery import ConfigurationRecoveryError, safe_snapshot
from .const import TEMPLATE_RECOVERY_TYPES
from .request_authority import check_request_authority


@dataclass(frozen=True)
class TemplateRecoveryAdapter:
    """One HA Template platform whose config entry can be restored safely."""

    entity_domain: str
    action_capable: bool


# These platforms store actions, which HA runs only when the entity is operated.
# Nonempty actions are refused at authoring and restore (see template_scope); the
# flag is reported in the restore preview.
TEMPLATE_RECOVERY_ADAPTERS = {
    domain: TemplateRecoveryAdapter(domain, domain in {
        "alarm_control_panel", "button", "cover", "fan", "light", "lock",
        "number", "select", "switch", "update", "vacuum",
    })
    for domain in TEMPLATE_RECOVERY_TYPES
}


TEMPLATE_CONFIG_FLOW = "homeassistant.components.template.config_flow"


async def async_import_template_flow(hass: HomeAssistant) -> None:
    """Import the Template config flow in the executor, so a restore can validate a snapshot."""
    await async_import_module(hass, TEMPLATE_CONFIG_FLOW)


def template_snapshot(hass: HomeAssistant, entry) -> dict:
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
    """Raise ConfigurationRecoveryError unless the snapshot is restorable as it stands.

    It must match the installed versions and one enabled ``template`` entity of the adapter's
    domain. Legacy (adapter 0) snapshots qualify only for a sensor or binary_sensor.
    """
    safe_snapshot(snapshot)
    try:
        flow_handler = imported_module(TEMPLATE_CONFIG_FLOW).TemplateConfigFlowHandler
    except ComponentNotLoaded as err:
        raise ConfigurationRecoveryError("The Template integration is not loaded; nothing was dispatched.") from err
    if (snapshot.get("snapshot_type") != "template_helper"
            or type(snapshot.get("schema")) is not int or snapshot.get("schema") != 1
            or snapshot.get("version") != flow_handler.VERSION
            or snapshot.get("minor_version") != flow_handler.MINOR_VERSION
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


@asynccontextmanager
async def protect_template(hass: HomeAssistant, data, access_key, entry, operation: str, *, source=None, expected=None):
    """Persist a safety copy and revalidate before entering a mutation boundary."""
    recovery = data.config_recovery
    key = f"config_entry:{entry.entry_id}"
    async with recovery.resource(key):
        before = template_snapshot(hass, entry)
        if operation == "remove_integration":
            expected = {"snapshot_type": "template_helper", "entry_id": entry.entry_id, "exists": False}
        rid = await recovery.begin(key, operation, access_key.id, before, source=source, expected=expected or source)
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
