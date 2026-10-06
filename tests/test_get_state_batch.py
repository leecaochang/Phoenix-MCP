"""get_state reads one entity or an ordered list, with one refusal for missing and hidden IDs."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import GET_STATE_MAX_IDS
from custom_components.phoenix_mcp.mcp_view import _call_tool


def _key(**fields) -> AccessKeyRecord:
    fields.setdefault("permissions", PermissionTree(
        domains={"light": PermissionNode(state="GREEN")},
        entities={"light.vault": PermissionNode(state="RED")},
    ))
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x",
                           created_at=utcnow(), created_by="u", **fields)


async def _read(hass, key, entity_id, kind="compact"):
    result, outcome, resource = await _call_tool(
        "get_state", {"entity_id": entity_id, "projection": {"kind": kind}}, key, hass, MagicMock())
    return result, outcome, resource


async def test_list_returns_states_in_request_order_and_one_not_found_list(hass):
    for eid in ("light.kitchen", "light.porch", "light.vault"):
        hass.states.async_set(eid, "on", {"brightness": 10, "access_token": "secret"})
    result, outcome, resource = await _read(
        hass, _key(), ["light.porch", "light.ghost", "light.kitchen", "light.vault", "light.porch"], "full")
    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed" and resource == "entities:2"
    assert [row["entity_id"] for row in body["states"]] == ["light.porch", "light.kitchen"]
    assert body["not_found"] == ["light.ghost", "light.vault"]
    assert "secret" not in result["content"][0]["text"]


async def test_single_id_shape_is_unchanged(hass):
    hass.states.async_set("light.kitchen", "on", {"brightness": 10})
    result, outcome, _ = await _read(hass, _key(), "light.kitchen")
    assert outcome == "allowed"
    assert json.loads(result["content"][0]["text"])["entity_id"] == "light.kitchen"
    missing, _, _ = await _read(hass, _key(), "light.ghost")
    hidden, _, _ = await _read(hass, _key(), "light.vault")
    assert missing["content"][0]["text"] == hidden["content"][0]["text"] == "Entity not found."


async def test_list_bounds_and_item_types_are_enforced(hass):
    for bad in ([], ["light.a"] * (GET_STATE_MAX_IDS + 1), ["light.a", 3]):
        _, outcome, _ = await _read(hass, _key(), bad)
        assert outcome == "invalid_request", bad


async def test_assist_exposure_hides_unexposed_entities_from_both_forms(hass):
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("light.kitchen", "on", {})
    hass.states.async_set("light.porch", "on", {})
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    async_expose_entity(hass, "conversation", "light.porch", False)
    key = _key(permissions=PermissionTree(), pass_through=True, use_assist_exposure=True)
    result, _, _ = await _read(hass, key, ["light.kitchen", "light.porch"])
    body = json.loads(result["content"][0]["text"])
    assert [row["entity_id"] for row in body["states"]] == ["light.kitchen"]
    assert body["not_found"] == ["light.porch"]
    single, _, _ = await _read(hass, key, "light.porch")
    assert single["content"][0]["text"] == "Entity not found."
