"""Fingerprint a command target: identity, scope, availability and command state."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..device_semantics import DeviceRegistrySnapshot, device_registry_snapshot
from ..policy_engine import Permission, resolve
from .command_operations import supports_command
from .routing_tables import (
    COMMAND_ROUTING_ENTITY_INTENTS, COMMAND_ROUTING_READ_INTENTS,
    COMMAND_ROUTING_UNKNOWN_ACTION_DOMAINS,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _target_fingerprint(hass: HomeAssistant, access_key: Any, entity_id: str, intent: str, *,
                        devices: DeviceRegistrySnapshot | None = None) -> str | None:
    """Return a digest of the target's identity and command state, or None if ineligible.

    Device metadata may be shared via `devices` only within one synchronous capture.
    """
    # Local: optional component, imported only when this path runs.
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    state = hass.states.get(entity_id)
    entry = er.async_get(hass).async_get(entity_id)
    readable = intent in COMMAND_ROUTING_READ_INTENTS
    permissions = (Permission.READ, Permission.WRITE) if readable else (Permission.WRITE,)
    unknown_action = bool(state and state.domain in COMMAND_ROUTING_UNKNOWN_ACTION_DOMAINS
                          and intent in COMMAND_ROUTING_ENTITY_INTENTS)
    if (state is None or (intent != "PhoenixStateSummary" and (state.state == "unavailable" or (state.state == "unknown" and not unknown_action))) or entry is None
            or entry.disabled_by or entry.hidden_by or entry.platform == "group"
            or (intent == "PhoenixStateSummary" and entry.entity_category is not None)
            or "entity_id" in state.attributes or resolve(entity_id, access_key, hass) not in permissions
            or not async_should_expose(hass, "conversation", entity_id)):
        return None
    entity_command = COMMAND_ROUTING_ENTITY_INTENTS.get(intent)
    if entity_command and not readable and not hass.services.has_service(entity_command[0], entity_command[1]):
        return None
    if not supports_command(state, intent):
        return None
    if devices is None:
        devices = device_registry_snapshot(hass)
    if entry.device_id and (devices.get(entry.device_id) is None
                            or entry.device_id in devices.invalid_ancestry_ids):
        return None
    return _digest({
        "registry_id": entry.id, "unique_id": entry.unique_id, "platform": entry.platform,
        "config_entry_id": entry.config_entry_id, "device_id": entry.device_id,
        "area_id": entry.area_id, "aliases": sorted(entry.aliases, key=str),
        "resolved_aliases": er.async_get_entity_aliases(hass, entry),
        "name": entry.name, "original_name": entry.original_name,
        "device_area": devices.effective_area_id(entry.device_id),
        "device_parent": devices.parent_id(entry.device_id),
        "temperature_unit": hass.config.units.temperature_unit,
        "timezone": hass.config.time_zone,
        "entity_command_attributes": {key: state.attributes.get(key) for key in
            ({spec[3] for spec in COMMAND_ROUTING_ENTITY_INTENTS.values() if spec[3]} | {"mode", "pattern", "code_format", "code_arm_required", "motion_detection"})},
        "attributes": {key: state.attributes.get(key) for key in (
            "friendly_name", "supported_color_modes", "supported_features", "device_class",
            "min", "max", "minimum", "maximum", "initial", "step", "unit_of_measurement", "min_temp", "max_temp", "target_temp_step",
            "min_humidity", "max_humidity", "min_color_temp_kelvin", "max_color_temp_kelvin", "has_date", "has_time",
        )},
    })
