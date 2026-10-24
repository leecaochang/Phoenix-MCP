"""search_entities reads current values with get_state's projection, saving a follow-up read.

The projection is get_state's own (one reader, one renderer), applied to the
returned page only, after scrubbing, so a scrubbed attribute cannot come back.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.const import MESA_MODE_OFF
from custom_components.phoenix_mcp.mcp_view import _call_tool


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(), pass_through=True, cap_search="allow",
    )


def _data():
    store = MagicMock()
    store.get_settings.return_value = SimpleNamespace(mesa_mode=MESA_MODE_OFF)
    return SimpleNamespace(store=store, mesa=None, entity_hints={})


@pytest.fixture
def home(hass):
    hass.states.async_set("sensor.kitchen_temperature", "21.5", {
        "unit_of_measurement": "°C", "device_class": "temperature", "friendly_name": "Kitchen", "access_token": "secret",
        "icon": "mdi:thermometer"})
    hass.states.async_set("climate.hall", "heat", {
        "current_temperature": 19, "temperature": 21, "hvac_action": "heating", "friendly_name": "Hall"})
    return hass


async def _search(hass, args):
    content, outcome, _ = await _call_tool("search_entities", args, _key(), hass, _data())
    assert outcome == "allowed", content
    return {row["entity_id"]: row for row in json.loads(content["content"][0]["text"])["entities"]}


async def test_without_projection_rows_keep_their_shape(home):
    rows = await _search(home, {"domain": ["sensor", "climate"]})
    assert "attributes" not in rows["sensor.kitchen_temperature"]
    assert rows["climate.hall"]["state"] == "heat"


async def test_compact_adds_the_key_attributes(home):
    rows = await _search(home, {"domain": ["sensor", "climate"], "projection": {"kind": "compact"}})
    assert rows["sensor.kitchen_temperature"]["attributes"]["unit_of_measurement"] == "°C"
    assert "icon" not in rows["sensor.kitchen_temperature"]["attributes"]
    assert rows["climate.hall"]["attributes"]["hvac_action"] == "heating"
    assert rows["climate.hall"]["state"] == "heat"


async def test_fields_add_exactly_what_was_asked(home):
    rows = await _search(home, {"domain": "climate", "projection": {"kind": "fields", "fields": ["attr.hvac_action", "last_changed"]}})
    assert rows["climate.hall"]["attributes"] == {"hvac_action": "heating"}
    assert "last_changed" in rows["climate.hall"]


async def test_full_never_brings_back_a_scrubbed_attribute(home):
    rows = await _search(home, {"domain": "sensor", "projection": {"kind": "full"}})
    row = rows["sensor.kitchen_temperature"]
    assert row["attributes"]["icon"] == "mdi:thermometer" and "last_updated" in row
    assert "access_token" not in json.dumps(row)


@pytest.mark.parametrize("projection", [{"kind": "lean"}, "compact", {"fields": ["state"]}])
async def test_a_malformed_projection_is_refused(home, projection):
    content, outcome, _ = await _call_tool("search_entities", {"projection": projection}, _key(), home, _data())
    assert outcome == "invalid_request"
    assert "projection" in content["content"][0]["text"]


@pytest.fixture
def placed(hass):
    """An entity placed directly, one through its device, a child and a via-device dependent of a placed hub, and orphans."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="test", entry_id="placed")
    entry.add_to_hass(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    devices, entities = dr.async_get(hass), er.async_get(hass)
    hub = devices.async_get_or_create(config_entry_id="placed", identifiers={("test", "hub")}, name="Hub")
    devices.async_update_device(hub.id, area_id=kitchen.id)
    part = devices.async_get_or_create_child(config_entry_id="placed", identifiers={("test", "part")}, parent_device_id=hub.id)
    reached = devices.async_get_or_create(config_entry_id="placed", identifiers={("test", "reached")}, name="Reached", via_device_id=hub.id)
    loose = devices.async_get_or_create(config_entry_id="placed", identifiers={("test", "loose")}, name="Loose")
    rows = {}
    for name, device, area in (("direct", None, kitchen.id), ("by_device", hub, None), ("orphan", loose, None)):
        reg = entities.async_get_or_create("sensor", "test", name, suggested_object_id=name, device_id=device.id if device else None)
        if area:
            entities.async_update_entity(reg.entity_id, area_id=area)
        hass.states.async_set(reg.entity_id, "1")
        rows[name] = reg.entity_id
    hass.states.async_set("sensor.yaml_only", "1")
    return SimpleNamespace(hub=hub, part=part, reached=reached, loose=loose, **rows)


async def test_search_has_area_follows_the_area_filter(hass, placed):
    without = await _search(hass, {"domain": "sensor", "has_area": False})
    assert set(without) == {placed.orphan, "sensor.yaml_only"}
    within = await _search(hass, {"domain": "sensor", "has_area": True})
    assert set(within) == {placed.direct, placed.by_device}


async def test_list_devices_has_area_counts_a_main_devices_area(hass, placed):
    key = _key()
    key.cap_registry_read = "allow"

    async def listed(value):
        content, outcome, _ = await _call_tool("list_devices", {"has_area": value}, key, hass, _data())
        assert outcome == "allowed", content
        return {row["device_id"] for row in json.loads(content["content"][0]["text"])["devices"]}

    # A child device is part of its main device and takes its area; a device
    # merely reached through the hub (via_device) is a separate product.
    assert await listed(False) == {placed.loose.id, placed.reached.id}
    assert await listed(True) == {placed.hub.id, placed.part.id}


@pytest.mark.parametrize("tool", ["search_entities", "list_devices"])
async def test_has_area_must_be_a_boolean(hass, tool):
    key = _key()
    key.cap_registry_read = "allow"
    content, outcome, _ = await _call_tool(tool, {"has_area": "no"}, key, hass, _data())
    assert outcome == "invalid_request"
    assert "has_area" in content["content"][0]["text"]
