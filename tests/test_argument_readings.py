"""Arguments in an older or Home Assistant shape that name one request are read, never widened.

The call runs as its current shape would, through every scope, capability and
MESA check, and the result's arguments_adjusted says how it was read. A shape
that is ambiguous, or that would let a guess act on a write, is still refused.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import MESA_MODE_OFF
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool


def _key(**caps) -> AccessKeyRecord:
    tree = PermissionTree(entities={"light.allowed": PermissionNode(state="GREEN"), "lock.door": PermissionNode(state="GREEN")})
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=tree, cap_search="allow", **caps)


def _data() -> PhoenixData:
    store = MagicMock()
    store.get_settings.return_value = SimpleNamespace(mesa_mode=MESA_MODE_OFF)
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=None)


@pytest.fixture
async def home(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    registry = er.async_get(hass)
    for domain, name in (("light", "allowed"), ("light", "hidden"), ("lock", "door")):
        entry = registry.async_get_or_create(domain, "test", name, suggested_object_id=name)
        hass.states.async_set(entry.entity_id, "off")
    calls = AsyncMock()
    hass.services.async_register("light", "turn_on", calls)
    hass.services.async_register("lock", "unlock", calls)
    return SimpleNamespace(hass=hass, calls=calls)


def _body(result) -> dict:
    return json.loads(result[0]["content"][0]["text"])


@pytest.mark.parametrize("args", [
    {"service": "light.turn_on", "target": {"entity_id": "light.allowed"}, "data": {"brightness": 50}},
    {"domain": "light", "service": "turn_on", "entity_id": "light.allowed", "service_data": {"brightness": 50}},
    {"service": {"domain": "light", "name": "turn_on", "data": {"brightness": 50, "entity_id": "light.allowed"}}},
    {"service": {"domain": "light", "service": "turn_on", "data": {"brightness": 50}},
     "targets": {"kind": "entity_id", "ids": "light.allowed"}},
])
async def test_home_assistant_and_retired_call_shapes_run_as_the_current_one(home, args):
    result = await _call_tool("call_service", args, _key(), home.hass, _data())
    assert result[1] == "allowed", result
    call = home.calls.await_args.args[0]
    assert call.data["entity_id"] == ["light.allowed"] and call.data["brightness"] == 50
    assert _body(result)["arguments_adjusted"][0].startswith("Read ")


@pytest.mark.parametrize("args", [
    {"service": "light.turn_on", "target": {"entity_id": "light.hidden"}},
    {"service": {"domain": "light", "name": "turn_on", "data": {"entity_id": "light.hidden"}}},
    {"domain": "light", "service": "turn_on", "entity_id": "all"},
])
async def test_a_read_shape_never_reaches_what_the_current_one_cannot(home, args):
    current = await _call_tool("call_service", {"service": {"domain": "light", "name": "turn_on"},
                                                "targets": [{"kind": "entity", "ids": ["light.hidden"]}]},
                               _key(), home.hass, _data())
    result = await _call_tool("call_service", args, _key(), home.hass, _data())
    # A whole-domain read acts only on what the key may write; a hidden target is refused like the current shape.
    assert result[1] == ("allowed" if args.get("entity_id") == "all" else current[1])
    called = [c.args[0].data["entity_id"] for c in home.calls.await_args_list]
    assert all("light.hidden" not in ids for ids in called)


@pytest.mark.parametrize("target", [{"entity_id": "lock.door"}, {"floor_id": "upstairs"}])
async def test_physical_control_denial_still_answers_first(home, target):
    # Even a shape it then refuses: a denied capability learns nothing about its arguments.
    result = await _call_tool("call_service", {"service": "lock.unlock", "target": target},
                              _key(cap_physical_control="deny"), home.hass, _data())
    assert result[1] == "denied" and home.calls.await_count == 0
    assert result[0]["content"][0]["text"].startswith("Forbidden")


@pytest.mark.parametrize("args", [
    {"service": {"domain": "light", "name": "turn_on"}, "domain": "light", "targets": [{"kind": "entity", "ids": ["light.allowed"]}]},
    {"service": "light.turn_on", "target": {"entity_id": "light.allowed"}, "targets": [{"kind": "entity", "ids": ["light.allowed"]}]},
    {"service": "light.turn_on", "target": {"floor_id": "upstairs"}},
    # Reading only the supported key would quietly answer a narrower request.
    {"service": "light.turn_on", "target": {"entity_id": "light.allowed", "floor_id": "upstairs"}},
])
async def test_an_ambiguous_or_unsupported_shape_is_refused_before_dispatch(home, args):
    result = await _call_tool("call_service", args, _key(), home.hass, _data())
    assert result[1] == "invalid_request" and home.calls.await_count == 0


async def test_a_retired_read_shape_and_a_missing_projection_are_read(home):
    result = await _call_tool("get_state", {"entity_id": "light.allowed"}, _key(), home.hass, _data())
    assert result[1] == "allowed" and _body(result)["arguments_adjusted"][0].startswith("No projection was sent")
    result = await _call_tool("get_state", {"entity_id": "light.allowed", "detailed": True}, _key(), home.hass, _data())
    body = _body(result)
    assert "last_changed" in body and "projection" in body["arguments_adjusted"][0]


async def test_entity_id_text_for_a_list_is_read_as_the_list(home):
    for text in ('["light.allowed", "lock.door"]', "light.allowed, lock.door"):
        result = await _call_tool("get_state", {"entity_id": text, "projection": {"kind": "compact"}},
                                  _key(), home.hass, _data())
        body = _body(result)
        assert [row["entity_id"] for row in body["states"]] == ["light.allowed", "lock.door"]
        assert "a list of 2" in body["arguments_adjusted"][0]


async def test_an_adjusted_call_that_fails_still_says_how_it_was_read(home):
    result = await _call_tool("get_state", {"entity_id": "light.nowhere"}, _key(), home.hass, _data())
    assert result[1] in ("not_found", "denied")
    texts = [item["text"] for item in result[0]["content"]]
    assert texts[0] == "Entity not found." and texts[-1].startswith("Arguments adjusted: No projection was sent")


def test_only_entity_ids_split_on_commas_while_json_lists_read_anywhere():
    from custom_components.phoenix_mcp.tool_input import decode_container_strings

    assert decode_container_strings("get_history", {"entity_id": "a.b, c.d"})["entity_id"] == ["a.b", "c.d"]
    # A state can hold a comma; JSON array text is unambiguous for any list field.
    assert decode_container_strings("get_history", {"in_state": "1,234"})["in_state"] == "1,234"
    assert decode_container_strings("get_history", {"in_state": '["on", "off"]'})["in_state"] == ["on", "off"]


@pytest.mark.parametrize(("field", "text", "read"), [
    ("device_class", "[door, window]", ["door", "window"]),
    ("domain", "[binary_sensor]", ["binary_sensor"]),
    ("entity_id", "[light.allowed, lock.door]", ["light.allowed", "lock.door"]),
    # Not bare identifiers, or a state, which is free text: left as sent.
    ("device_class", "[door, ]", None),
    ("integration", "[Living Room]", None),
    ("in_state", "[on, off]", None),
])
def test_bracketed_identifiers_read_as_the_list_they_spell(field, text, read):
    from custom_components.phoenix_mcp.tool_input import decode_container_strings

    tool = "get_history" if field == "in_state" else "search_entities" if field != "entity_id" else "get_state"
    assert decode_container_strings(tool, {field: text})[field] == (read if read is not None else text)


async def test_an_unquoted_device_class_list_finds_what_the_list_finds(home):
    hass = home.hass
    hass.states.async_set("binary_sensor.front", "off", {"device_class": "door"})
    hass.states.async_set("binary_sensor.hall", "off", {"device_class": "window"})
    key = _key()
    key.pass_through = True
    found = {}
    for value in (["door", "window"], "[door, window]"):
        result = await _call_tool("search_entities", {"device_class": value}, key, hass, _data())
        body = _body(result)
        found[str(value)] = sorted(row["entity_id"] for row in body["entities"])
    assert found["['door', 'window']"] == found["[door, window]"] == ["binary_sensor.front", "binary_sensor.hall"]
    assert "a list of 2" in body["arguments_adjusted"][0]


@pytest.mark.parametrize(("tool", "field", "sent", "read"), [
    ("get_statistics", "period", "Day", "day"),
    ("get_history", "order", "NEWEST_FIRST", "newest_first"),
    ("search_entities", "registry_state", "All", "all"),
])
def test_an_enum_in_another_case_is_read_on_reads_only(tool, field, sent, read):
    from custom_components.phoenix_mcp.tool_input import scalar_arguments

    arguments, error = scalar_arguments(tool, {field: sent}, read_only=True)
    assert error is None and arguments[field] == read
    # A write takes its enum exactly as declared; the handler refuses another spelling.
    assert scalar_arguments(tool, {field: sent}, read_only=False)[0][field] == sent
