"""Build bounded, scoped decision catalogs for previews and guarded Voice plans."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from homeassistant.helpers import area_registry as ar, entity_registry as er

from .command_operations import numeric_metadata
from .command_execution import _target_fingerprint, _digest
from .const import (
    DECISION_MAX_TARGETS, DECISION_MAX_TARGET_BYTES, DECISION_MAX_TARGET_TEXT,
    DECISION_MAX_TARGET_ALIASES, DECISION_TARGET_INTENTS, COMMAND_ROUTING_TEMPORAL_OPERATIONS,
)
from .decision_provider import DecisionError
from .device_semantics import DeviceRegistrySnapshot, device_registry_snapshot
from .helpers import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text


class CatalogLimitError(DecisionError):
    """Expose scoped numeric bounds in inert previews without catalog text or IDs."""

    def __init__(self, category: str, bound: str, observed: int, limit: int) -> None:
        super().__init__("too_many_targets" if bound == "target_count" else "target_metadata_too_large")
        self.details = {"category": category, "bound": bound, "observed": observed, "limit": limit}


@dataclass(frozen=True)
class TargetSnapshot:
    """Opaque keys stay bound to the complete original catalog, never model-written IDs."""

    targets: dict[str, dict]
    fingerprints: dict[str, str]

    def digest(self) -> str:
        """Only this irreversible catalog fingerprint may survive in an approval."""
        return _digest({"targets": self.targets, "fingerprints": self.fingerprints})


def capture_targets(hass: Any, token: Any, category: str, *,
                    devices: DeviceRegistrySnapshot | None = None,
                    max_targets: int = DECISION_MAX_TARGETS, compact: bool = False) -> TargetSnapshot:
    """Reuse local execution eligibility; send identity metadata only, never states."""
    intent = DECISION_TARGET_INTENTS.get(category)
    if intent is None:
        raise DecisionError("unsupported_category")
    registry = er.async_get(hass)
    areas = ar.async_get(hass)
    # A batch may share this synchronous view; callers must recapture after any wait.
    if devices is None:
        devices = device_registry_snapshot(hass)
    targets: dict[str, dict] = {}
    fingerprints: dict[str, str] = {}

    def text(value: Any) -> str:
        if not isinstance(value, str):
            raise DecisionError("target_metadata_too_large")
        if len(value) > DECISION_MAX_TARGET_TEXT:
            raise CatalogLimitError(category, "text_length", len(value), DECISION_MAX_TARGET_TEXT)
        return redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(value) or "", token, hass)

    for state in sorted(hass.states.async_all(), key=lambda item: item.entity_id):
        entity_id = state.entity_id
        fingerprint = _target_fingerprint(hass, token, entity_id, intent, devices=devices)
        if fingerprint is None:
            continue
        if len(targets) >= max_targets:
            # Never hide competing candidates by taking an arbitrary first page.
            raise CatalogLimitError(category, "target_count", len(targets) + 1, max_targets)
        entry = registry.async_get(entity_id)
        assert entry is not None
        if len(entry.aliases) > DECISION_MAX_TARGET_ALIASES:
            raise CatalogLimitError(category, "alias_count", len(entry.aliases), DECISION_MAX_TARGET_ALIASES)
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
        temporal = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(intent)
        if temporal:
            descriptor["value_kind"] = temporal[3]
        metadata = numeric_metadata(state, intent)
        if metadata and intent not in ("HassLightSet", "HassSetVolume", "HassSetPosition"):
            if intent in ("HassClimateSetTemperature", "PhoenixWaterTemperature"):
                metadata["unit"] = hass.config.units.temperature_unit
            metadata["unit"] = text(metadata["unit"] or "")
            descriptor["numeric"] = metadata
        if compact:
            # Remove only identical identity text and empty fields; keep every candidate.
            descriptor["aliases"] = [alias for alias in descriptor["aliases"]
                                     if alias not in (descriptor["name"], entity_id)]
            if not descriptor["aliases"]:
                del descriptor["aliases"]
            if not descriptor["area"]:
                del descriptor["area"]
        targets[f"t{len(targets)}"] = descriptor
        fingerprints[entity_id] = fingerprint
    size = len(json.dumps(targets, ensure_ascii=False).encode())
    if size > DECISION_MAX_TARGET_BYTES:
        raise CatalogLimitError(category, "metadata_bytes", size, DECISION_MAX_TARGET_BYTES)
    return TargetSnapshot(targets, fingerprints)
