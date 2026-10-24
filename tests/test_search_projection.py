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
