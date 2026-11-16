"""Area-resolving reads build one device snapshot per call and keep their results.

list_areas, list_floors, describe_area, search_entities, get_overview and the JSON context
resolve every listed entity's area. Each used to rebuild a snapshot of the whole device
registry for every entity, so a large home stalled the event loop for seconds. These tests
pin the snapshot count independent of the entity count, and the results for a scoped and a
pass-through key across direct, device-inherited, overridden and missing areas.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import device_semantics
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.tool_dispatch import _build_context_json, _call_tool


def _key(*, pass_through: bool = False) -> AccessKeyRecord:
    """A key reading the light domain only, or a pass-through key reading everything."""
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="area-cost", access_key_hash="x", created_at=utcnow(), created_by="user1",
        cap_registry_read="allow", cap_search="allow", pass_through=pass_through,
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
    )


def _home(hass: HomeAssistant, extra_lights: int) -> str:
    """Two floors, four areas, and lights whose areas come from each source.

    Kitchen lights inherit Kitchen from the hub, except one the entity registry moves to
    Attic. A light on an area-less device sits in Hall by its own area, one light has no
    area at all, and the Garage sensor is outside the scoped key.
    """
    entry = MockConfigEntry(domain="test_integration", entry_id="area_cost")
    entry.add_to_hass(hass)
    floors, areas, devices, entities = fr.async_get(hass), ar.async_get(hass), dr.async_get(hass), er.async_get(hass)
    ground, upper = floors.async_create("Ground", level=0), floors.async_create("Upper", level=1)
    kitchen = areas.async_create("Kitchen")
    areas.async_update(kitchen.id, floor_id=ground.floor_id)
    hall = areas.async_create("Hall")
    areas.async_update(hall.id, floor_id=ground.floor_id)
    attic = areas.async_create("Attic")
    areas.async_update(attic.id, floor_id=upper.floor_id)
    garage = areas.async_create("Garage")

    hub = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test_integration", "hub")})
    devices.async_update_device(hub.id, area_id=kitchen.id)
    bare = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test_integration", "bare")})
    sensor_hub = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test_integration", "sh")})
    devices.async_update_device(sensor_hub.id, area_id=garage.id)
    # Devices with no entities still make up the registry every snapshot reads.
    for index in range(extra_lights):
        devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test_integration", f"spare{index}")})

    def light(object_id: str, device_id: str | None, area_id: str | None = None) -> None:
        row = entities.async_get_or_create("light", "test_integration", object_id, config_entry=entry,
                                           device_id=device_id, suggested_object_id=object_id)
        if area_id is not None:
            entities.async_update_entity(row.entity_id, name=object_id, area_id=area_id)
        hass.states.async_set(row.entity_id, "on", {"friendly_name": object_id})

    for index in range(2 + extra_lights):
        light(f"kitchen_{index}", hub.id)
    light("moved", hub.id, attic.id)
    light("hall", bare.id, hall.id)
    light("nowhere", None)
    sensor = entities.async_get_or_create("sensor", "test_integration", "garage", config_entry=entry,
                                          device_id=sensor_hub.id, suggested_object_id="garage")
    hass.states.async_set(sensor.entity_id, "5", {})
    return hub.id


async def _tool(hass: HomeAssistant, name: str, args: dict, key: AccessKeyRecord) -> dict:
    data = MagicMock()
    data.mesa = None
    content, outcome, _ = await _call_tool(name, args, key, hass, data)
    assert outcome == "allowed", content
    return json.loads(content["content"][0]["text"])


def _area_names(hass: HomeAssistant) -> dict[str, str]:
    return {area.id: area.name for area in ar.async_get(hass).areas.values()}


async def _outputs(hass: HomeAssistant, key: AccessKeyRecord) -> dict:
    """Every area-resolving read, reduced to the area facts each one reports."""
    names = _area_names(hass)
    areas = await _tool(hass, "list_areas", {}, key)
    floors = await _tool(hass, "list_floors", {}, key)
    kitchen = await _tool(hass, "describe_area", {"area": "Kitchen"}, key)
    found = await _tool(hass, "search_entities", {"area": "Kitchen", "limit": 200}, key)
    overview = await _tool(hass, "get_overview", {}, key)
    hass.data[DOMAIN] = SimpleNamespace(store=SimpleNamespace(get_entity_hints=lambda: {}))
    context = _build_context_json(key, hass)
    return {
        "areas": {row["name"]: (row["floor_id"], row["accessible_entity_count"]) for row in areas["areas"]},
        "floors": {row["name"]: (row["accessible_area_count"], row["accessible_entity_count"],
                                 [area["name"] for area in row["areas"]]) for row in floors["floors"]},
        "kitchen": kitchen,
        "found": sorted(row["entity_id"] for row in found["entities"]),
        "found_areas": {row.get("area") for row in found["entities"]},
        "overview": overview,
        "context": {row["entity_id"]: names.get(row["area_id"]) for row in context["entities"]},
    }


@pytest.mark.parametrize("pass_through", [False, True])
async def test_area_reads_report_each_entitys_area(hass: HomeAssistant, pass_through: bool) -> None:
    _home(hass, extra_lights=0)
    out = await _outputs(hass, _key(pass_through=pass_through))
    garage = {"Garage": (None, 1)} if pass_through else {}
    floor_id = {f.name: f.floor_id for f in fr.async_get(hass).floors.values()}
    assert out["areas"] == {"Kitchen": (floor_id["Ground"], 2), "Hall": (floor_id["Ground"], 1),
                            "Attic": (floor_id["Upper"], 1), **garage}
    assert out["floors"] == {"Ground": (2, 3, ["Hall", "Kitchen"]), "Upper": (1, 1, ["Attic"])}
    assert out["found"] == ["light.kitchen_0", "light.kitchen_1"]
    assert out["found_areas"] == {"Kitchen"}
    assert out["kitchen"]["accessible_entity_count"] == 2
    assert sorted(row["entity_id"] for row in out["kitchen"]["entities_by_domain"]["light"]) == out["found"]
    assert out["context"] == {
        "light.kitchen_0": "Kitchen", "light.kitchen_1": "Kitchen", "light.moved": "Attic",
        "light.hall": "Hall", "light.nowhere": None, **({"sensor.garage": "Garage"} if pass_through else {}),
    }
    by_area = out["overview"]["by_area"]
    assert by_area.get("Kitchen") == 2 and by_area.get("Attic") == 1 and by_area.get("Hall") == 1
    assert by_area.get("Garage") == (1 if pass_through else None)


@pytest.mark.parametrize("tool", ["list_areas", "list_floors", "describe_area", "search_entities", "get_overview",
                                  "context"])
async def test_one_device_snapshot_per_call_whatever_the_entity_count(hass: HomeAssistant, tool: str) -> None:
    """Snapshots per call stay level as entities grow; one per entity grows with them."""
    built = 0
    real = device_semantics.DeviceRegistrySnapshot

    def counted(*args, **kwargs):
        nonlocal built
        built += 1
        return real(*args, **kwargs)

    args = {"describe_area": {"area": "Kitchen"}, "search_entities": {"area": "Kitchen", "limit": 200}}.get(tool, {})
    key = _key()
    hass.data[DOMAIN] = SimpleNamespace(store=SimpleNamespace(get_entity_hints=lambda: {}))

    async def snapshots() -> int:
        nonlocal built
        built = 0
        with patch.object(device_semantics, "DeviceRegistrySnapshot", counted):
            if tool == "context":
                _build_context_json(key, hass)
            else:
                await _tool(hass, tool, args, key)
        return built

    hub_id = _home(hass, extra_lights=0)
    small = await snapshots()
    for index in range(40):
        entity = er.async_get(hass).async_get_or_create(
            "light", "test_integration", f"more_{index}", suggested_object_id=f"more_{index}", device_id=hub_id)
        hass.states.async_set(entity.entity_id, "on", {})
    large = await snapshots()
    assert small >= 1
    assert large == small, f"{tool} built {small} snapshots for 5 lights and {large} for 45"
