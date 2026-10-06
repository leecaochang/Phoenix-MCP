"""Gemini sees a representable schema; validation and dispatch keep the full contract."""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.phoenix_mcp.assist_api import PhoenixAssistAPI, PhoenixAssistTool
from custom_components.phoenix_mcp.assist_gemini import GeminiUnrepresentable, gemini_schema, unwrap_json_fields
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.mcp_view import _TOOL_DEFS_BY_NAME
from tests.test_assist_api import _ctx, _make_access_key, _make_data, _tool_input


def _lowered(tool: str) -> dict:
    return gemini_schema(copy.deepcopy(_TOOL_DEFS_BY_NAME[tool]["inputSchema"]))[0]


def test_lowering_keeps_one_honest_type_per_field():
    changes = _lowered("set_entity")["properties"]["changes"]["properties"]
    assert changes["name"]["type"] == "string" and changes["name"]["nullable"] is True
    assert changes["add_aliases"]["type"] == "array" and changes["add_aliases"]["items"] == {"type": "string"}
    assert _lowered("get_state")["properties"]["entity_id"]["type"] == "array"
    assert "anyOf" not in _lowered("intent__HassTurnOn")
    assert "allOf" not in _lowered("light__HassLightSet")


@pytest.mark.parametrize("tool", ["patch_dashboard", "set_zigbee_device_property"])
def test_any_json_value_stays_unavailable(tool):
    with pytest.raises(GeminiUnrepresentable):
        gemini_schema(copy.deepcopy(_TOOL_DEFS_BY_NAME[tool]["inputSchema"]))


def test_open_object_is_shown_as_json_text_and_unwrapped():
    lowered, paths = gemini_schema(copy.deepcopy(_TOOL_DEFS_BY_NAME["create_helper"]["inputSchema"]))
    assert paths == (("config",), ("flow_steps", "[]", "data"))
    assert lowered["properties"]["config"]["required"] == ["json"]
    args = {"config": {"json": '{"name": "Guest"}'},
            "flow_steps": [{"step_id": "user", "data": {"json": '{"x": 1}'}}, {"step_id": "s", "data": {"y": 2}}]}
    assert unwrap_json_fields(args, paths) == {
        "config": {"name": "Guest"},
        "flow_steps": [{"step_id": "user", "data": {"x": 1}}, {"step_id": "s", "data": {"y": 2}}],
    }
    for left_alone in ({"json": "not json"}, {"json": "[1, 2]"}, {"json": '{"a": 1}', "other": 1}):
        assert unwrap_json_fields({"config": left_alone}, paths) == {"config": left_alone}


async def _gemini_instance(hass):
    access_key = _make_access_key(announce_all=True)
    data = _make_data(access_key, bound_id=access_key.id)
    data.mesa = MagicMock()
    hass.data[DOMAIN] = data
    context = _ctx()
    context.platform = "google_generative_ai_conversation"
    instance = await PhoenixAssistAPI(hass, data).async_get_api_instance(context)
    return {tool.name: tool for tool in instance.tools}


async def test_validation_still_uses_the_full_schema(hass):
    tools = await _gemini_instance(hass)
    with pytest.raises(vol.Invalid):
        tools["intent__HassTurnOn"].parameters({})
    assert tools["set_entity"].parameters({"entity_id": "light.a", "changes": {"name": None}})


async def test_open_object_reaches_dispatch_as_the_object(hass):
    tools = await _gemini_instance(hass)
    tool: PhoenixAssistTool = tools["call_service"]
    args = tool.parameters({"service": {"domain": "light", "name": "turn_on", "data": {"json": '{"brightness": 10}'}},
                            "targets": [{"kind": "entity", "ids": ["light.a"]}]})
    captured = {}

    async def fake_dispatch(method, msg_id, params, *a, **k):
        captured.update(params)
        return ({"result": {"content": [{"type": "text", "text": "{}"}]}}, "call_service", "call_service", "allowed")

    with patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=fake_dispatch):
        await tool.async_call(hass, _tool_input(args), _ctx())
    assert captured["arguments"]["service"]["data"] == {"brightness": 10}
