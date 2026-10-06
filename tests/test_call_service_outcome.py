"""call_service names what it acted on and flags targets Home Assistant skipped."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from tests.runtime_fixtures import mesa_off_store


async def test_result_lists_targets_their_states_and_unavailable_ones(hass):
    calls = []

    async def turn_off(call):
        calls.append(call.data["entity_id"])
        for entity_id in call.data["entity_id"]:
            if hass.states.get(entity_id).state != "unavailable":
                hass.states.async_set(entity_id, "off", {})

    hass.services.async_register("switch", "turn_off", turn_off)
    registry = er.async_get(hass)
    for object_id, state in (("fan", "on"), ("pump", "unavailable")):
        registry.async_get_or_create("switch", "test", object_id, suggested_object_id=object_id)
        hass.states.async_set(f"switch.{object_id}", state, {})
    key = AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                          permissions=PermissionTree(domains={"switch": PermissionNode(state="GREEN")}),
                          cap_physical_control="allow")
    data = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    result, outcome, _ = await _call_tool("call_service", {
        "service": {"domain": "switch", "name": "turn_off"},
        "targets": [{"kind": "entity", "ids": ["switch.fan", "switch.pump"]}],
    }, key, hass, data)
    assert outcome == "allowed", result
    targets = json.loads(result["content"][0]["text"])["targets"]
    assert targets["count"] == 2 and targets["unavailable"] == ["switch.pump"]
    assert {row["entity_id"]: row["state"] for row in targets["states_after_call"]} == {
        "switch.fan": "off", "switch.pump": "unavailable"}
