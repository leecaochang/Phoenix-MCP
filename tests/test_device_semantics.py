"""Tests for Home Assistant device-registry compatibility semantics."""

from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.device_semantics import (
    device_registry_snapshot,
    device_registry_snapshot_from_registry,
    effective_entity_area_id,
)


def test_legacy_mapping_snapshot_preserves_main_devices() -> None:
    device = {"id": "legacy", "area_id": "kitchen", "via_device_id": None}
    snapshot = device_registry_snapshot_from_registry(
        SimpleNamespace(devices={"legacy": device})
    )

    assert snapshot.entries == (device,)
    assert snapshot.get("legacy") is device
    assert snapshot.effective_area_id("legacy") == "kitchen"


def test_conflicting_identity_and_invalid_ancestry_fail_closed() -> None:
    first = {"id": "conflict", "area_id": "one"}
    second = {"id": "conflict", "area_id": "two", "parent_device_id": "missing"}
    invalid_child = {
        "id": "invalid-child",
        "area_id": None,
        "parent_device_id": "missing",
    }
    snapshot = device_registry_snapshot_from_registry(
        SimpleNamespace(devices=[first], child_devices=[second, invalid_child])
    )

    assert snapshot.get("conflict") is None
    assert snapshot.conflicting_ids == frozenset({"conflict"})
    assert snapshot.effective_area_id("invalid-child") is None
    assert snapshot.invalid_ancestry_ids == frozenset({"invalid-child"})


async def test_real_child_collection_inherits_one_parent_area(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain="test_integration", entry_id="child-semantics")
    entry.add_to_hass(hass)
    parent_area = ar.async_get(hass).async_create("Parent Area")
    child_area = ar.async_get(hass).async_create("Child Area")
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, "parent")},
        name="Parent",
    )
    registry.async_update_device(parent.id, area_id=parent_area.id)
    child = registry.async_get_or_create_child(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, "child")},
        name="Child",
        parent_device_id=parent.id,
    )

    snapshot = device_registry_snapshot(hass)
    assert set(snapshot.by_id) == {parent.id, child.id}
    assert snapshot.effective_area_id(child.id) == parent_area.id
    assert snapshot.parent_id(child.id) == parent.id
    assert snapshot.child_ids(parent.id) == (child.id,)

    registry.async_update_child_device(child.id, area_id=child_area.id)
    snapshot = device_registry_snapshot(hass)
    assert snapshot.effective_area_id(child.id) == child_area.id


def test_entity_direct_area_presence_prevents_device_fallback() -> None:
    device = {"id": "child", "area_id": "device-area"}
    snapshot = device_registry_snapshot_from_registry(
        SimpleNamespace(devices={"child": device})
    )

    assert effective_entity_area_id(
        SimpleNamespace(area_id="", device_id="child"), snapshot
    ) is None
    assert effective_entity_area_id(
        SimpleNamespace(area_id=None, device_id="child"), snapshot
    ) == "device-area"
