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
from custom_components.phoenix_mcp.tool_dispatch import _call_tool


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
    from pytest_homeassistant.common import MockConfigEntry

    entry = MockConfigEntry(domain="test", entry_id="placed")
    entry.add_to_hass(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    devices, entities = dr.async_get(hass), er.async_get(hass)
    hub = devices.async_get_or_create(config_entry_id="placed", identifiers={("test", "hub")}, name="Hub")
    devices.async_update_device(hub.id, area_id=kitchen.id)
    # Child devices arrived in Home Assistant 2026.9; earlier releases have no part.
    part = (devices.async_get_or_create_child(config_entry_id="placed", identifiers={("test", "part")}, parent_device_id=hub.id)
            if hasattr(devices, "async_get_or_create_child") else None)
    # via_device_id replaced the identifier tuple in 2026.9. Only parameters
    # count: 2026.7 also has a local variable of that name.
    code = dr.DeviceRegistry.async_get_or_create.__code__
    parameters = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]
    via = {"via_device_id": hub.id} if "via_device_id" in parameters else {"via_device": ("test", "hub")}
    reached = devices.async_get_or_create(config_entry_id="placed", identifiers={("test", "reached")}, name="Reached", **via)
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
    assert await listed(True) == {placed.hub.id} | ({placed.part.id} if placed.part else set())


@pytest.mark.parametrize("tool", ["search_entities", "list_devices"])
async def test_has_area_must_be_a_boolean(hass, tool):
    key = _key()
    key.cap_registry_read = "allow"
    content, outcome, _ = await _call_tool(tool, {"has_area": "no"}, key, hass, _data())
    assert outcome == "invalid_request"
    assert "has_area" in content["content"][0]["text"]


async def test_device_rows_name_their_area(hass, placed):
    # "Which devices are in the kitchen" is asked by name; the row's area_id alone is an opaque ID.
    key = _key()
    key.cap_registry_read = "allow"
    content, outcome, _ = await _call_tool("list_devices", {}, key, hass, _data())
    assert outcome == "allowed", content
    rows = {row["device_id"]: row for row in json.loads(content["content"][0]["text"])["devices"]}
    assert rows[placed.hub.id]["area"] == "Kitchen" and rows[placed.hub.id]["area_id"]
    if placed.part:
        assert rows[placed.part.id]["area"] == "Kitchen"  # a child device takes its main device's area
    assert rows[placed.loose.id]["area"] is None and rows[placed.loose.id]["area_id"] is None
    content, outcome, _ = await _call_tool("get_device", {"device_id": placed.hub.id}, key, hass, _data())
    assert outcome == "allowed" and json.loads(content["content"][0]["text"])["area"] == "Kitchen"


async def test_by_domain_counts_every_match_not_just_the_page(hass, placed):
    hass.states.async_set("light.orphan_lamp", "on")
    content, outcome, _ = await _call_tool("search_entities", {"has_area": False, "limit": 1}, _key(), hass, _data())
    assert outcome == "allowed", content
    body = json.loads(content["content"][0]["text"])
    assert body["count"] == 1 and body["by_domain"] == {"sensor": 2, "light": 1}
    assert sum(body["by_domain"].values()) == body["total"]
    # One domain needs no breakdown beside total.
    content, _, _ = await _call_tool("search_entities", {"domain": "sensor", "has_area": False}, _key(), hass, _data())
    assert "by_domain" not in json.loads(content["content"][0]["text"])


@pytest.fixture
def context_home(hass, placed):
    """The placed home plus a floor, a label and a manufacturer, and a name-only Kitchen entity."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import floor_registry as fr
    from homeassistant.helpers import label_registry as lr

    upstairs = fr.async_get(hass).async_create("Upstairs")
    kitchen = next(a for a in ar.async_get(hass).async_list_areas() if a.name == "Kitchen")
    ar.async_get(hass).async_update(kitchen.id, floor_id=upstairs.floor_id)
    dr.async_get(hass).async_update_device(placed.loose.id, manufacturer="Acme Corp", model="Widget 9")
    label = lr.async_get(hass).async_create("Holiday Lights")
    er.async_get(hass).async_update_entity(placed.orphan, labels={label.label_id})
    hass.states.async_set("sensor.kitchen_scale", "1", {"friendly_name": "Kitchen Scale"})
    return placed


async def _ranked(hass, query):
    content, outcome, _ = await _call_tool("search_entities", {"query": query}, _key(), hass, _data())
    assert outcome == "allowed", content
    return json.loads(content["content"][0]["text"])["entities"]


@pytest.mark.parametrize(("query", "field"), [
    ("upstairs", "floor"), ("holiday", "label"), ("acme", "manufacturer"), ("widget", "model"),
])
async def test_a_query_finds_entities_by_their_floor_label_and_device(hass, context_home, query, field):
    rows = await _ranked(hass, query)
    assert rows and all(field in row["matched"] for row in rows)
    expected = {"upstairs": {context_home.direct, context_home.by_device}, "holiday": {context_home.orphan},
                "acme": {context_home.orphan}, "widget": {context_home.orphan}}[query]
    assert {row["entity_id"] for row in rows} == expected


async def test_a_device_word_in_the_composed_name_is_a_name_match(hass, context_home):
    rows = await _ranked(hass, "hub")
    assert [row["entity_id"] for row in rows] == [context_home.by_device]
    assert "hub" in rows[0]["composed_name"].lower() and "matched" not in rows[0]


async def test_a_name_match_leads_and_an_area_match_says_so(hass, context_home):
    rows = await _ranked(hass, "kitchen")
    # The entity named Kitchen leads; those only in the Kitchen area follow, one of them through its device.
    assert rows[0]["entity_id"] == "sensor.kitchen_scale" and "matched" not in rows[0]
    assert {row["entity_id"] for row in rows[1:]} == {context_home.direct, context_home.by_device}
    assert all(row["matched"] == ["area"] for row in rows[1:])


async def test_every_word_must_be_found_somewhere(hass, context_home):
    content, _, _ = await _call_tool("search_entities", {"query": "kitchen orphan"}, _key(), hass, _data())
    # No entity has both words anywhere, so only the typo-tolerant name fallback answers.
    assert json.loads(content["content"][0]["text"]).get("fuzzy_fallback") is True
    rows = await _ranked(hass, "holiday orphan")
    assert [row["entity_id"] for row in rows] == [context_home.orphan] and rows[0]["matched"] == ["label"]
