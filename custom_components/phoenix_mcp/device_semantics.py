"""Compatibility-aware Home Assistant device-registry read semantics."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeviceRegistrySnapshot:
    """One deterministic view of main and child device collections."""

    entries: tuple[Any, ...]
    by_id: dict[str, Any]
    effective_area_by_id: dict[str, str | None]
    parent_by_id: dict[str, str]
    child_parent_by_id: dict[str, str]
    children_by_parent: dict[str, tuple[str, ...]]
    conflicting_ids: frozenset[str]
    invalid_ancestry_ids: frozenset[str]

    def get(self, device_id: str | None) -> Any | None:
        """Return an unambiguous registry entry by id."""
        return self.by_id.get(device_id) if isinstance(device_id, str) else None

    def effective_area_id(self, device_id: str | None) -> str | None:
        """Return Core's effective direct-or-parent area for one device."""
        if not isinstance(device_id, str):
            return None
        return self.effective_area_by_id.get(device_id)

    def parent_id(self, device_id: str | None) -> str | None:
        """Return the direct child parent or ordinary via-device parent."""
        if not isinstance(device_id, str):
            return None
        return self.parent_by_id.get(device_id)

    def child_ids(self, device_id: str) -> tuple[str, ...]:
        """Return direct child and ordinary via-device ids."""
        return self.children_by_parent.get(device_id, ())

    def child_parent_id(self, device_id: str | None) -> str | None:
        """Return a 2026.9 child device's direct main parent id."""
        if not isinstance(device_id, str):
            return None
        return self.child_parent_by_id.get(device_id)


def _collection_entries(collection: object) -> Collection[Any]:
    """Normalize legacy mappings and HA 2026.9 collection views."""
    if isinstance(collection, Mapping):
        return tuple(collection.values())
    if isinstance(collection, Collection):
        return collection
    return ()


def _entry_document(entry: Any) -> dict[str, Any]:
    """Return the public registry representation without compatibility attrs."""
    document = getattr(entry, "dict_repr", None)
    if isinstance(document, Mapping):
        return dict(document)
    if isinstance(entry, Mapping):
        return dict(entry)
    return {
        "id": getattr(entry, "id", None),
        "area_id": getattr(entry, "area_id", None),
        "parent_device_id": getattr(entry, "parent_device_id", None),
        "via_device_id": getattr(entry, "via_device_id", None),
    }


def device_entry_value(entry: Any, key: str, default: Any = None) -> Any:
    """Read a public device field without child compatibility shims."""
    return _entry_document(entry).get(key, default)


def _valid_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _effective_area(
    document: Mapping[str, Any], documents_by_id: Mapping[str, Mapping[str, Any]]
) -> tuple[str | None, bool]:
    """Return effective area and whether malformed ancestry was encountered."""
    direct_area = document.get("area_id")
    if direct_area is not None:
        return (_valid_id(direct_area), _valid_id(direct_area) is None)

    parent_value = document.get("parent_device_id")
    if parent_value is None:
        return None, False
    parent_id = _valid_id(parent_value)
    if parent_id is None:
        return None, True
    parent = documents_by_id.get(parent_id)
    if parent is None or parent.get("parent_device_id") is not None:
        return None, True
    parent_area = parent.get("area_id")
    if parent_area is None:
        return None, False
    area_id = _valid_id(parent_area)
    return area_id, area_id is None


def device_registry_snapshot_from_registry(registry: Any) -> DeviceRegistrySnapshot:
    """Build one snapshot across legacy, main, and child collections."""
    collections = [getattr(registry, "devices", ())]
    if hasattr(registry, "child_devices"):
        collections.append(getattr(registry, "child_devices"))

    order: list[str] = []
    by_id: dict[str, Any] = {}
    documents: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    for collection in collections:
        for entry in _collection_entries(collection):
            document = _entry_document(entry)
            device_id = _valid_id(document.get("id"))
            if device_id is None or device_id in conflicts:
                continue
            if device_id not in by_id:
                order.append(device_id)
                by_id[device_id] = entry
                documents[device_id] = document
                continue
            if documents[device_id] != document:
                conflicts.add(device_id)
                del by_id[device_id]
                del documents[device_id]

    if conflicts:
        _LOGGER.warning(
            "Device registry contained conflicting identity rows; excluding %d "
            "ambiguous device id(s)",
            len(conflicts),
        )

    effective_areas: dict[str, str | None] = {}
    invalid_ancestry: set[str] = set()
    parent_by_id: dict[str, str] = {}
    child_parent_by_id: dict[str, str] = {}
    children: dict[str, list[str]] = {}
    for device_id, document in documents.items():
        effective_areas[device_id], invalid = _effective_area(document, documents)
        if invalid:
            invalid_ancestry.add(device_id)
        child_parent = document.get("parent_device_id")
        relation_parent = _valid_id(
            child_parent if child_parent is not None else document.get("via_device_id")
        )
        if relation_parent is not None and relation_parent in documents:
            parent_by_id[device_id] = relation_parent
            if child_parent is not None:
                child_parent_by_id[device_id] = relation_parent
            children.setdefault(relation_parent, []).append(device_id)

    entries = tuple(by_id[device_id] for device_id in order if device_id in by_id)
    return DeviceRegistrySnapshot(
        entries=entries,
        by_id=by_id,
        effective_area_by_id=effective_areas,
        parent_by_id=parent_by_id,
        child_parent_by_id=child_parent_by_id,
        children_by_parent={
            parent_id: tuple(sorted(child_ids))
            for parent_id, child_ids in children.items()
        },
        conflicting_ids=frozenset(conflicts),
        invalid_ancestry_ids=frozenset(invalid_ancestry),
    )


def device_registry_snapshot(hass: HomeAssistant) -> DeviceRegistrySnapshot:
    """Build the current compatibility-aware device snapshot."""
    return device_registry_snapshot_from_registry(dr.async_get(hass))


def update_device_registry_entry(
    registry: Any, device_id: str, updates: dict[str, Any]
) -> Any | None:
    """Update a main or child device across Home Assistant registry eras."""
    snapshot = device_registry_snapshot_from_registry(registry)
    device = snapshot.get(device_id)
    if device is None:
        return None
    document = _entry_document(device)
    if (
        document.get("parent_device_id") is not None
        and hasattr(registry, "async_update_child_device")
    ):
        return registry.async_update_child_device(device_id, **updates)
    return registry.async_update_device(device_id, **updates)


def effective_entity_area_id(entry: Any, snapshot: DeviceRegistrySnapshot) -> str | None:
    """Return an entity's direct area or its device's effective area."""
    if entry is None:
        return None
    direct_area = getattr(entry, "area_id", None)
    if direct_area is not None:
        return _valid_id(direct_area)
    return snapshot.effective_area_id(getattr(entry, "device_id", None))
