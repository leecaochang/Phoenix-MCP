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
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

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
from custom_components.phoenix_mcp.mesa import async_setup_mesa, evaluate_service_call, evaluate_service_entities
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


async def _wrapped_switch(hass, reference: str = "registry", target_domain: str = "light") -> tuple[list, str]:
    """A real Switch as X entry over switch.vault; the call log and the wrapper's entity id."""
    assert await async_setup_component(hass, "homeassistant", {})
    switch_calls = async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    source = er.async_get(hass).async_get_or_create("switch", "test", "vault", suggested_object_id="vault")
    hass.states.async_set(source.entity_id, "off")
    entry = MockConfigEntry(domain="switch_as_x", title="Wrapper", options={
        "entity_id": source.id if reference == "registry" else source.entity_id,
        "invert": False, "target_domain": target_domain,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    wrapper = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
    assert wrapper.startswith(f"{target_domain}.")
    return switch_calls, wrapper


@pytest.mark.parametrize("reference", ["registry", "entity_id"])
@pytest.mark.parametrize("target_domain", ["light", "fan", "cover", "lock", "siren", "valve"])
async def test_a_switch_wrapper_has_its_source_as_its_member(hass, reference, target_domain):
    _, wrapper = await _wrapped_switch(hass, reference, target_domain)
    assert group_member_ids(wrapper, hass) == frozenset({"switch.vault"})


async def test_a_red_source_refuses_its_wrapper(hass):
    switch_calls, wrapper = await _wrapped_switch(hass)
    data, _ = _data(hass)
    key = _key({"switch.vault": "RED"}, {"light": "GREEN"})
    assert group_member_denied(wrapper, key, hass) is True
    _, outcome, _ = await _call_tool("call_service", _turn_on(wrapper), key, hass, data)
    await hass.async_block_till_done()
    assert outcome != "allowed"
    assert switch_calls == []


async def test_a_wrapper_reaches_an_ungranted_source_as_a_group_reaches_a_member(hass):
    switch_calls, wrapper = await _wrapped_switch(hass)
    data, _ = _data(hass)
    result, outcome, _ = await _call_tool("call_service", _turn_on(wrapper), _key(domains={"light": "GREEN"}), hass, data)
    await hass.async_block_till_done()
    assert outcome == "allowed", result
    assert [call.data["entity_id"] for call in switch_calls] == ["switch.vault"]


@pytest.mark.parametrize("pass_through", [False, True])
async def test_a_prohibited_source_blocks_its_wrapper(hass, pass_through):
    switch_calls, wrapper = await _wrapped_switch(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "switch.vault", "prohibited")
    key = _key(domains={"light": "GREEN"}, pass_through=pass_through)
    result, outcome, _ = await _call_tool("call_service", _turn_on(wrapper), key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert "switch.vault" not in result["content"][0]["text"]
    assert switch_calls == []


async def test_a_source_needing_confirmation_sends_its_wrapper_to_approval(hass):
    switch_calls, wrapper = await _wrapped_switch(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "switch.vault", "confirm")
    verdict = evaluate_service_call(
        data.mesa, "enforced", _key(domains={"light": "GREEN"}), [wrapper],
        domain="light", service="turn_on", service_data={}, session_id="s",
    )
    assert verdict.confirm == [wrapper]
    assert verdict.allowed == [] and switch_calls == []


async def test_a_group_of_wrappers_reaches_the_source(hass):
    _, wrapper = await _wrapped_switch(hass)
    hass.states.async_set("light.room", "on", {"group_entities": [wrapper]})
    assert group_member_ids("light.room", hass) == frozenset({wrapper, "switch.vault"})


@pytest.mark.parametrize("options", [{}, {"entity_id": "not an id"}, {"entity_id": 3}])
async def test_a_wrapper_whose_source_cannot_be_read_is_refused(hass, options):
    entry = MockConfigEntry(domain="switch_as_x", options=options)
    entry.add_to_hass(hass)
    wrapper = er.async_get(hass).async_get_or_create(
        "light", "switch_as_x", "w", config_entry=entry, suggested_object_id="w").entity_id
    assert group_member_ids(wrapper, hass) is None
    assert group_member_denied(wrapper, _key({wrapper: "GREEN"}), hass) is True


async def test_another_platforms_entity_id_option_is_not_a_source(hass):
    entry = MockConfigEntry(domain="threshold", options={"entity_id": "switch.vault"})
    entry.add_to_hass(hass)
    entity_id = er.async_get(hass).async_get_or_create(
        "light", "threshold", "t", config_entry=entry, suggested_object_id="t").entity_id
    hass.states.async_set(entity_id, "on")
    assert group_member_ids(entity_id, hass) == frozenset()


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


@pytest.mark.parametrize("attributes", [{}, {"entity_id": "light.other"}])
async def test_a_plain_entity_has_no_members(hass, attributes):
    hass.states.async_set("light.solo", "on", attributes)
    assert group_member_ids("light.solo", hass) == frozenset()


async def test_a_state_listing_members_is_a_group_whatever_the_registry_says(hass):
    hass.states.async_set("light.yaml_group", "on", {"entity_id": ["light.leaf"]})
    hass.states.async_set("light.leaf", "on")
    assert group_member_ids("light.yaml_group", hass) == frozenset({"light.leaf"})


async def _yaml_group(hass) -> list:
    """A real YAML group with no unique_id: members in the state, nothing in the registry."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "light", {"light": [{
        "platform": "group", "name": "All lamps", "entities": ["light.vault", "light.desk"],
    }]})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for object_id in ("vault", "desk"):
        entity_id = registry.async_get_or_create("light", "test", object_id, suggested_object_id=object_id).entity_id
        hass.states.async_set(entity_id, "on", {"supported_color_modes": ["onoff"], "color_mode": "onoff"})
    await hass.async_block_till_done()
    assert registry.async_get("light.all_lamps") is None
    seen: list = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, lambda event: seen.append(event.data))
    return seen


async def test_a_yaml_group_is_refused_to_native_tools_on_a_red_member(hass):
    seen = await _yaml_group(hass)
    _data(hass)
    key = _key({"light.all_lamps": "GREEN", "light.vault": "RED", "light.desk": "GREEN"})
    result, outcome, _ = await _tool_intent_action("HassTurnOff", "light", "turn_off", {}, ["light.all_lamps"], hass, key)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert seen == []


async def test_a_yaml_group_is_refused_to_native_tools_on_a_prohibited_member(hass):
    seen = await _yaml_group(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "light.vault", "prohibited")
    key = _key(domains={"light": "GREEN"})
    result, outcome, _ = await _tool_intent_action("HassLightSet", "light", "turn_on", {"brightness": 10}, ["light.all_lamps"], hass, key)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert seen == []


@pytest.mark.parametrize("tool,arguments", [
    pytest.param("HassTurnOff", {"name": "All lamps"}, id="by name"),
    pytest.param("HassTurnOff", {"domain": ["light"]}, id="by domain"),
    pytest.param("HassLightSet", {"name": "All lamps", "brightness": 10}, id="by name with data"),
])
@pytest.mark.parametrize("member", ["RED", "prohibited"])
async def test_a_yaml_group_is_refused_through_the_named_native_tools(hass, tool, arguments, member):
    """Resolution finds the group by its state, so the refusal must not need a registry entry."""
    await _yaml_group(hass)
    turn_off = async_mock_service(hass, "light", "turn_off")
    turn_on = async_mock_service(hass, "light", "turn_on")
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    if member == "prohibited":
        _set_profile(data, "light.vault", member)
        key = _key(domains={"light": "GREEN"})
    else:
        key = _key({"light.vault": "RED"}, domains={"light": "GREEN"})
    await _call_tool(tool, arguments, key, hass, data)
    await hass.async_block_till_done()
    reached = [call.data.get("entity_id") for call in [*turn_off, *turn_on]]
    assert not any("light.all_lamps" in (ids if isinstance(ids, list) else [ids]) for ids in reached)
    assert not any("light.vault" in (ids if isinstance(ids, list) else [ids]) for ids in reached)


async def test_a_yaml_group_stays_out_of_call_service(hass):
    """An unregistered ID is skipped there, so its members never come into it."""
    seen = await _yaml_group(hass)
    data, _ = _data(hass)
    key = _key(domains={"light": "GREEN"})
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all_lamps"), key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert seen == []


async def _unavailable_group(hass) -> list:
    """The registered group of real members, every one of them gone unavailable."""
    seen = await _light_group(hass)
    for entity_id in ("light.bedroom", "light.kitchen"):
        hass.states.async_set(entity_id, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("light.all").state == "unavailable"
    assert "entity_id" not in hass.states.get("light.all").attributes
    return seen


async def test_an_unavailable_group_still_answers_with_its_members(hass):
    await _unavailable_group(hass)
    assert group_member_ids("light.all", hass) == frozenset({"light.bedroom", "light.kitchen"})


async def test_an_unavailable_yaml_group_still_answers_with_its_members(hass):
    await _yaml_group(hass)
    for entity_id in ("light.vault", "light.desk"):
        hass.states.async_set(entity_id, "unavailable")
    await hass.async_block_till_done()
    assert group_member_ids("light.all_lamps", hass) == frozenset({"light.vault", "light.desk"})


async def test_an_unavailable_group_runs_for_a_key_that_may_control_it(hass):
    seen = await _unavailable_group(hass)
    data, _ = _data(hass)
    key = _key(domains={"light": "GREEN"}, cap_search="allow")
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "allowed", result
    assert json.loads(result["content"][0]["text"])["targets"]["unavailable"] == ["light.all"]
    assert [event["domain"] for event in seen if event["service_data"].get("entity_id") == ["light.all"]] == ["light"]
    preview, _, _ = await _call_tool("dry_run_service", _turn_on("light.all"), key, hass, data)
    assert json.loads(preview["content"][0]["text"])["predicted_outcome"] == "allowed"


async def test_a_refresh_of_an_unavailable_group_runs(hass):
    await _unavailable_group(hass)
    data, _ = _data(hass)
    result, outcome, _ = await _call_tool(
        "call_service",
        {"service": {"domain": "homeassistant", "name": "update_entity"},
         "targets": [{"kind": "entity", "ids": ["light.all"]}]},
        _key(domains={"light": "GREEN"}), hass, data,
    )
    assert outcome == "allowed", result


async def test_an_unavailable_group_keeps_its_red_member_rule(hass):
    seen = await _unavailable_group(hass)
    data, _ = _data(hass)
    key = _key({"light.all": "GREEN", "light.bedroom": "RED"})
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), key, hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert seen == []


async def test_an_unavailable_group_keeps_its_mesa_member_rule(hass):
    seen = await _unavailable_group(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "light.bedroom", "prohibited")
    result, outcome, _ = await _call_tool("call_service", _turn_on("light.all"), _key(domains={"light": "GREEN"}), hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert seen == []


async def test_members_truly_unknown_refuse_and_say_so_on_every_surface(hass):
    """Nothing to read anywhere: the rule stays a refusal, and it names the group."""
    assert await async_setup_component(hass, "homeassistant", {})
    entity_id = er.async_get(hass).async_get_or_create("light", "group", "g", suggested_object_id="ghost").entity_id
    hass.states.async_set(entity_id, "unavailable")
    data, _ = _data(hass)
    key = _key({entity_id: "GREEN"}, cap_search="allow")
    result, outcome, _ = await _call_tool("call_service", _turn_on(entity_id), key, hass, data)
    assert outcome == "denied"
    assert entity_id in result["content"][0]["text"] and result["content"][0]["text"] != "Forbidden."
    preview, _, _ = await _call_tool("dry_run_service", _turn_on(entity_id), key, hass, data)
    body = json.loads(preview["content"][0]["text"])
    assert body["predicted_outcome"] == "denied" and entity_id in body["refusal_reason"]
    assert await async_setup_component(hass, "light", {})
    listing, _, _ = await _call_tool("find_available_actions", {"entity_id": entity_id}, key, hass, data)
    row = next(item for item in json.loads(listing["content"][0]["text"])["actions"]
               if item["service"] == "light.turn_on")
    assert row["available"] is False and "cannot be read" in row["reason"]


async def test_mesa_refuses_members_truly_unknown_and_says_so(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    entity_id = er.async_get(hass).async_get_or_create("light", "group", "g", suggested_object_id="ghost").entity_id
    hass.states.async_set(entity_id, "unavailable")
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    verdict = evaluate_service_call(
        data.mesa, "enforced", _key({entity_id: "GREEN"}), [entity_id],
        domain="light", service="turn_on", service_data={}, session_id="s",
    )
    assert [row[0] for row in verdict.blocked] == [entity_id]
    assert "cannot be read" in verdict.blocked[0][2]


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
