"""A group helper is governed by its members: RED and MESA reach through it.

Scenes, scripts and automations stay governed by their primary entity, even though
a scene publishes a member list in the same attribute a group does.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.mesa import async_setup_mesa, evaluate_service_entities
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.policy_engine import group_member_denied, group_member_ids
from custom_components.phoenix_mcp.tools.native import _tool_intent_action


async def _light_group(hass) -> list:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "light", {"light": [{
        "platform": "group", "name": "All", "unique_id": "all_group",
        "entities": ["light.bedroom", "light.kitchen"],
    }]})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for object_id in ("bedroom", "kitchen", "porch"):
        entity_id = registry.async_get_or_create("light", "test", object_id, suggested_object_id=object_id).entity_id
        hass.states.async_set(entity_id, "off", {"supported_color_modes": ["onoff"], "color_mode": "onoff"})
    await hass.async_block_till_done()
    seen: list = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, lambda event: seen.append(event.data))
    return seen


def _data(hass, mesa_mode: str = "off") -> tuple[PhoenixData, list]:
    pending: list = []
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    store.get_settings.return_value = GlobalSettings(mesa_mode=mesa_mode)
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=None)
    hass.data[DOMAIN] = data
    return data, pending


def _key(entities: dict[str, str] | None = None, domains: dict[str, str] | None = None, **fields) -> AccessKeyRecord:
    return AccessKeyRecord(
        id="k", name="K", access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(
            entities={e: PermissionNode(state=s) for e, s in (entities or {}).items()},
            domains={d: PermissionNode(state=s) for d, s in (domains or {}).items()},
        ),
        **fields,
    )


def _turn_on(*entity_ids: str) -> dict:
    return {"service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": "entity", "ids": list(entity_ids)}]}


def _set_profile(data: PhoenixData, entity_id: str, mode: str) -> None:
    data.mesa.store.set(entity_id, SemanticProfile.from_dict(
        entity_id, {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}},
        default_origin=MetadataOrigin.USER,
    ))


async def test_a_group_with_a_red_member_is_refused(hass):
    seen = await _light_group(hass)
    data, _ = _data(hass)
    key = _key({"light.all": "GREEN", "light.bedroom": "RED"})
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert result["content"][0]["text"] == "Forbidden."
    assert seen == []


async def test_a_granted_group_still_reaches_ungranted_members(hass):
    seen = await _light_group(hass)
    data, _ = _data(hass)
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), _key({"light.all": "GREEN"}), hass, data)
    await hass.async_block_till_done()
    assert outcome == "allowed", result
    assert {tuple(row["service_data"]["entity_id"]) for row in seen if row["domain"] == "light"} >= {("light.all",)}


async def test_a_refused_group_drops_from_a_wider_target_list(hass):
    seen = await _light_group(hass)
    data, _ = _data(hass)
    key = _key({"light.bedroom": "RED"}, {"light": "GREEN"})
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all", "light.porch"), key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "allowed", result
    assert seen[0]["service_data"]["entity_id"] == ["light.porch"]


async def test_the_generic_service_does_not_reach_around_the_refusal(hass):
    seen = await _light_group(hass)
    data, _ = _data(hass)
    key = _key({"light.all": "GREEN", "light.bedroom": "RED"})
    result, outcome, _ = await _call_tool("call_service", {
        "service": {"domain": "homeassistant", "name": "turn_on"},
        "targets": [{"kind": "entity", "ids": ["light.all"]}],
    }, key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert seen == []


async def test_native_tools_take_the_same_refusal(hass):
    seen = await _light_group(hass)
    _data(hass)
    key = _key({"light.all": "GREEN", "light.bedroom": "RED"})
    result, outcome, _ = await _tool_intent_action("HassTurnOn", "light", "turn_on", {}, ["light.all"], hass, key)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert seen == []


async def test_dry_run_predicts_the_group_refusal(hass):
    await _light_group(hass)
    data, _ = _data(hass)
    key = _key({"light.all": "GREEN", "light.bedroom": "RED"}, cap_search="allow")
    result, _, _ = await _call_tool("dry_run_service", _turn_on("light.all"), key, hass, data)
    body = json.loads(result["content"][0]["text"])
    assert body["predicted_outcome"] == "denied" and body["resolved_entities"] == []


@pytest.mark.parametrize("pass_through", [False, True])
async def test_a_prohibited_member_blocks_the_group(hass, pass_through):
    seen = await _light_group(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "light.bedroom", "prohibited")
    key = _key({"light.all": "GREEN"}, pass_through=pass_through)
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert "light.bedroom" not in result["content"][0]["text"]
    assert seen == []


async def test_a_member_needing_confirmation_sends_the_group_to_approval(hass):
    seen = await _light_group(hass)
    data, pending = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "light.bedroom", "confirm")
    key = _key({"light.all": "GREEN"})
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), key, hass, data)
    assert outcome == "pending_approval", result
    assert seen == []
    approved, approved_outcome, _ = await async_execute_approved_tool(
        pending[0]["tool_name"], pending[0]["args"], key, hass, data)
    await hass.async_block_till_done()
    assert approved_outcome == "allowed", approved
    assert seen[0]["service_data"]["entity_id"] == ["light.all"]


async def test_preview_names_the_group_and_never_the_member(hass):
    await _light_group(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "light.bedroom", "prohibited")
    key = _key({"light.all": "GREEN"}, cap_search="allow")
    result, _, _ = await _call_tool("dry_run_service", _turn_on("light.all"), key, hass, data)
    text = result["content"][0]["text"]
    body = json.loads(text)
    assert body["predicted_outcome"] == "denied"
    assert [row["entity_id"] for row in body["mesa"]["blocked"]] == ["light.all"]
    assert "light.bedroom" not in text


async def test_advisory_mode_warns_and_acts(hass):
    seen = await _light_group(hass)
    data, _ = _data(hass, "advisory")
    data.mesa = await async_setup_mesa(hass, "advisory")
    _set_profile(data, "light.bedroom", "confirm")
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), _key({"light.all": "GREEN"}), hass, data)
    await hass.async_block_till_done()
    assert outcome == "allowed", result
    text = result["content"][0]["text"]
    assert "light.bedroom" not in text
    assert any("advisory" in warning for warning in json.loads(text)["mesa_advisory"])
    assert seen


async def test_a_registry_decision_about_the_group_does_not_look_through_it(hass):
    """Renaming a group actuates nothing, so a member's policy has no say in it."""
    await _light_group(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "light.bedroom", "prohibited")
    verdict = evaluate_service_entities(
        data.mesa, "enforced", _key({"light.all": "GREEN"}), ["light.all"],
        domain="light", service="turn_on", service_data={}, session_id="s",
    )
    assert verdict.blocked == []


async def test_a_scene_stays_governed_by_its_primary_entity(hass):
    """HA's scene publishes its members in the attribute a group uses."""
    entity_id = er.async_get(hass).async_get_or_create("scene", "homeassistant", "s", suggested_object_id="evening").entity_id
    hass.states.async_set(entity_id, "2026-01-01", {"entity_id": ["light.bedroom"]})
    hass.states.async_set("light.bedroom", "off")
    key = _key({entity_id: "GREEN", "light.bedroom": "RED"})
    assert group_member_ids(entity_id, hass) == frozenset()
    assert group_member_denied(entity_id, key, hass) is False


@pytest.mark.parametrize("domain", ["script", "automation"])
async def test_scripts_and_automations_are_never_groups(hass, domain):
    entity_id = er.async_get(hass).async_get_or_create(domain, domain, "s", suggested_object_id="s").entity_id
    hass.states.async_set(entity_id, "off", {"entity_id": ["light.bedroom"]})
    assert group_member_ids(entity_id, hass) == frozenset()


async def test_a_plain_entity_has_no_members(hass):
    hass.states.async_set("light.solo", "on", {"entity_id": ["light.other"]})
    assert group_member_ids("light.solo", hass) == frozenset()


async def test_nested_groups_are_walked_and_a_cycle_ends(hass):
    registry = er.async_get(hass)
    outer = registry.async_get_or_create("light", "group", "o", suggested_object_id="outer").entity_id
    inner = registry.async_get_or_create("light", "group", "i", suggested_object_id="inner").entity_id
    hass.states.async_set(outer, "on", {"entity_id": [inner]})
    hass.states.async_set(inner, "on", {"entity_id": ["light.leaf", outer]})
    hass.states.async_set("light.leaf", "on")
    assert group_member_ids(outer, hass) == frozenset({inner, "light.leaf"})
    key = _key({outer: "GREEN", "light.leaf": "RED"})
    assert group_member_denied(outer, key, hass) is True


async def test_integration_groups_publish_members_through_the_capability_attribute(hass):
    hass.states.async_set("light.room", "on", {"group_entities": ["light.leaf"]})
    hass.states.async_set("light.leaf", "on")
    assert group_member_ids("light.room", hass) == frozenset({"light.leaf"})


@pytest.mark.parametrize("attributes", [{}, {"entity_id": "light.a"}, {"entity_id": [1]}])
async def test_unreadable_membership_is_refused(hass, attributes):
    entity_id = er.async_get(hass).async_get_or_create("light", "group", "g", suggested_object_id="g").entity_id
    hass.states.async_set(entity_id, "on", attributes)
    assert group_member_ids(entity_id, hass) is None
    assert group_member_denied(entity_id, _key({entity_id: "GREEN"}), hass) is True


async def test_a_group_with_no_state_is_refused(hass):
    entity_id = er.async_get(hass).async_get_or_create("light", "group", "g", suggested_object_id="g").entity_id
    assert group_member_ids(entity_id, hass) is None
