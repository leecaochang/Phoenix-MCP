"""Registry-bound location evidence supplied only by HA's conversation ingress."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.helpers import area_registry as ar, entity_registry as er

from .device_semantics import device_registry_snapshot


def capture_voice_origin(hass: Any, satellite_id: object, device_id: object = None) -> dict | None:
    """Accept a registered satellite with an unambiguous effective area.

    A browser language, entity name or caller-provided area is never location
    evidence. Device-only inputs are excluded: HA's recognition cache keys on
    satellite_id, not device_id. Ordinary explicit commands remain available.
    """
    if not isinstance(satellite_id, str) or not satellite_id.startswith('assist_satellite.'):
        return None
    entry = er.async_get(hass).async_get(satellite_id)
    if entry is None or entry.disabled_by or not entry.device_id:
        return None
    if device_id is not None and device_id != entry.device_id:
        return None
    devices = device_registry_snapshot(hass)
    if devices.get(entry.device_id) is None or entry.device_id in devices.invalid_ancestry_ids:
        return None
    area_id = entry.area_id or devices.effective_area_id(entry.device_id)
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    if area is None:
        return None
    identity = [entry.id, entry.unique_id, entry.platform, entry.config_entry_id,
                entry.device_id, entry.area_id, devices.effective_area_id(entry.device_id),
                devices.parent_id(entry.device_id), area.id, area.name, sorted(area.aliases)]
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {'satellite_id': satellite_id, 'device_id': entry.device_id,
            'area_id': area.id, 'fingerprint': fingerprint}


def voice_origin_current(hass: Any, origin: object) -> bool:
    """Check location again after recognition, dispatch waits and approval."""
    return isinstance(origin, dict) and capture_voice_origin(
        hass, origin.get('satellite_id'), origin.get('device_id')) == origin
