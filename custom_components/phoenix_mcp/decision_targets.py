"""Build bounded, scoped target snapshots for inert decision-provider previews."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from homeassistant.helpers import area_registry as ar, entity_registry as er

from .command_execution import _target_fingerprint
from .const import (
    DECISION_MAX_TARGETS, DECISION_MAX_TARGET_BYTES, DECISION_MAX_TARGET_TEXT,
    DECISION_MAX_TARGET_ALIASES, DECISION_TARGET_INTENTS,
)
from .decision_provider import DecisionError
from .device_semantics import device_registry_snapshot
from .helpers import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text


@dataclass(frozen=True)
class TargetSnapshot:
    """Opaque keys stay bound to the complete original catalog, never model-written IDs."""

    targets: dict[str, dict]
    fingerprints: dict[str, str]


def capture_targets(hass: Any, token: Any, category: str) -> TargetSnapshot:
    """Reuse local execution eligibility; send identity metadata only, never states."""
    intent = DECISION_TARGET_INTENTS.get(category)
    if intent is None:
        raise DecisionError("unsupported_category")
    registry = er.async_get(hass)
    areas = ar.async_get(hass)
    devices = device_registry_snapshot(hass)
    targets: dict[str, dict] = {}
    fingerprints: dict[str, str] = {}

    def text(value: Any) -> str:
        if not isinstance(value, str) or len(value) > DECISION_MAX_TARGET_TEXT:
            raise DecisionError("target_metadata_too_large")
        return redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(value) or "", token, hass)

    for state in sorted(hass.states.async_all(), key=lambda item: item.entity_id):
        entity_id = state.entity_id
        fingerprint = _target_fingerprint(hass, token, entity_id, intent)
        if fingerprint is None:
            continue
        if len(targets) >= DECISION_MAX_TARGETS:
            # Never hide competing candidates by taking an arbitrary first page.
            raise DecisionError("too_many_targets")
        entry = registry.async_get(entity_id)
        assert entry is not None
        if len(entry.aliases) > DECISION_MAX_TARGET_ALIASES:
            raise DecisionError("target_metadata_too_large")
        area_id = entry.area_id or devices.effective_area_id(entry.device_id)
        area = areas.async_get_area(area_id) if area_id else None
        name = next(value for value in (state.attributes.get("friendly_name"), entry.name,
                                        entry.original_name, entity_id) if isinstance(value, str) and value)
        descriptor = {
            "entity_id": entity_id,
            "name": text(name),
            "aliases": sorted(text(alias) for alias in er.async_get_entity_aliases(hass, entry)),
            "area": text(area.name) if area else "",
        }
        targets[f"t{len(targets)}"] = descriptor
        fingerprints[entity_id] = fingerprint
    if len(json.dumps(targets, ensure_ascii=False).encode()) > DECISION_MAX_TARGET_BYTES:
        raise DecisionError("target_metadata_too_large")
    return TargetSnapshot(targets, fingerprints)
