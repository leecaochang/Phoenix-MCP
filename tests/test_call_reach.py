"""A gate judges what a call reaches, not only what it names.

Covers the capabilities of a call, entities a caller chooses through a script's inputs, whole
device services under MESA, scene members on the physical gate, and approvals for targets that
cannot be bound. Scripts, scenes, cover groups and the registries are Home Assistant's own; the
services a script calls are recorded stubs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_set_service_schema
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.approval_targets import target_binding
from custom_components.phoenix_mcp.approvals import async_evaluate_capability as evaluate_capability
from custom_components.phoenix_mcp.capability import service_capabilities, strictest_capability
from custom_components.phoenix_mcp.const import (
    APPROVAL_TARGETS_KEY,
    DOMAIN,
    DUAL_GATE_SERVICES,
    NO_TARGET_SERVICES,
    PHYSICAL_COMMAND_SERVICES,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.service_targets import (
    named_script_refusal,
    script_input_entities,
    script_input_selectors,
)
from custom_components.phoenix_mcp.policy_engine import required_service_cap
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.tools.service import _execute_call_service


def _profile(key: str, mode: str) -> SemanticProfile:
    return SemanticProfile.from_dict(
        key,
        {"semantic_profile": {"operational_boundaries": {"control_mode": mode, "control_reason": "fixture"}}},
        default_origin=MetadataOrigin.USER,
    )


async def _data(hass, mode: str = "off") -> tuple[PhoenixData, list]:
    pending: list = []
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    store.get_settings.return_value = GlobalSettings(mesa_mode=mode)
    store.get_entity_hints.return_value = {}
    mesa = await async_setup_mesa(hass, mode) if mode != "off" else None
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=mesa)
    hass.data[DOMAIN] = data
    return data, pending


def _key(entities: dict[str, str], devices: dict[str, str] | None = None, **fields) -> AccessKeyRecord:
    return AccessKeyRecord(
        id="k", name="K", access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(
            entities={e: PermissionNode(state=s) for e, s in entities.items()},
            devices={d: PermissionNode(state=s) for d, s in (devices or {}).items()},
        ),
        **{"cap_search": "allow", **fields},
    )


def _entity(hass, domain: str, platform: str, object_id: str, state: str = "off", **registry) -> str:
    entity_id = er.async_get(hass).async_get_or_create(
        domain, platform, f"{platform}_{object_id}", suggested_object_id=object_id, **registry).entity_id
    hass.states.async_set(entity_id, state)
    return entity_id


def _record(hass, *names: tuple[str, str]) -> list:
    ran: list = []

    async def handler(call) -> None:
        ran.append((call.domain, call.service, dict(call.data)))

    for domain, service in names:
        hass.services.async_register(domain, service, handler)
    return ran


def _call(domain: str, service: str, entity_id: str | list[str], data: dict | None = None) -> dict:
    ids = [entity_id] if isinstance(entity_id, str) else entity_id
    return {
        "service": {"domain": domain, "name": service, **({"data": data} if data else {})},
        "targets": [{"kind": "entity", "ids": ids}],
    }


def _body(result: tuple) -> dict:
    return json.loads(result[0]["content"][0]["text"])


def _text(result: tuple) -> str:
    return result[0]["content"][0]["text"]


# Capabilities of a call


@pytest.mark.parametrize(("first", "second"), [("deny", "confirm"), ("confirm", "deny")])
def test_deny_binds_harder_than_confirm_in_either_order(first, second):
    key = _key({}, cap_physical_control=first, cap_notify=second)
    chosen = strictest_capability(key, "cap_physical_control", "cap_notify")
    assert getattr(key, chosen) == "deny"


def test_confirm_binds_harder_than_allow_and_no_name_means_none():
    key = _key({}, cap_physical_control="allow", cap_notify="confirm")
    assert strictest_capability(key, "cap_physical_control", "cap_notify") == "cap_notify"
    assert strictest_capability(key, None, None) is None


def test_every_capability_of_a_call_counts_and_a_notify_target_replaces_none():
    assert service_capabilities("recorder", "purge_entities", {}, ["notify.chat"]) == ("cap_registry_write",)
    assert set(service_capabilities("homeassistant", "toggle", {}, ["notify.chat"])) == {"cap_notify"}
    assert set(service_capabilities("scene", "delete", {}, ["notify.chat"])) == {"cap_notify", "cap_scene_write"}
    assert set(service_capabilities("media_player", "play_media", {"announce": True}, ["notify.chat"])) == {
        "cap_notify", "cap_broadcast",
    }


@pytest.mark.parametrize("service_key", sorted(DUAL_GATE_SERVICES | NO_TARGET_SERVICES | {
    "scene/delete", "recorder/purge_entities", "tts/speak", "notify/send_message",
}))
def test_a_notify_target_never_takes_a_capability_away(service_key):
    domain, service = service_key.split("/")
    alone = set(service_capabilities(domain, service, {}, ["sensor.x"]))
    assert alone <= set(service_capabilities(domain, service, {}, ["sensor.x", "notify.x"]))


async def test_the_one_way_to_ask_weighs_every_capability_of_the_call(hass):
    data, _ = await _data(hass)
    scene = _entity(hass, "scene", "x", "s")
    chat = _entity(hass, "notify", "x", "chat")
    key = _key({scene: "GREEN", chat: "GREEN"}, cap_notify="allow", cap_scene_write="deny")
    assert data is not None
    assert required_service_cap(
        domain="scene", service="delete", service_data={}, entity_id=[scene, chat], access_key=key, hass=hass,
    ) == "cap_scene_write"


@pytest.mark.parametrize("mode", ["deny", "confirm"])
async def test_a_notify_entity_in_the_list_does_not_replace_the_registry_capability(hass, mode):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("recorder", "purge_entities"))
    data, pending = await _data(hass)
    history = _entity(hass, "sensor", "x", "hist")
    chat = _entity(hass, "notify", "x", "chat")
    key = _key({history: "GREEN", chat: "GREEN"}, cap_registry_write=mode, cap_notify="allow")
    result = await _call_tool("call_service", _call("recorder", "purge_entities", [history, chat]), key, hass, data)
    assert result[1] == ("denied" if mode == "deny" else "pending_approval"), result
    assert ran == []
    assert len(pending) == (0 if mode == "deny" else 1)


@pytest.mark.parametrize(("physical", "notify"), [("deny", "confirm"), ("confirm", "deny")])
async def test_a_call_needing_two_capabilities_is_refused_when_either_is_denied(hass, physical, notify):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("nuki", "lock_n_go"))
    data, pending = await _data(hass)
    lock = _entity(hass, "lock", "nuki", "front")
    chat = _entity(hass, "notify", "nuki", "chat")
    key = _key({lock: "GREEN", chat: "GREEN"}, cap_physical_control=physical, cap_notify=notify)
    result = await _call_tool("call_service", _call("nuki", "lock_n_go", [lock, chat]), key, hass, data)
    assert result[1] == "denied", result
    assert ran == []
    assert pending == []


async def test_a_purge_of_a_lock_needs_the_registry_capability_and_not_physical(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("recorder", "purge_entities"))
    data, _ = await _data(hass)
    lock = _entity(hass, "lock", "x", "front")
    args = _call("recorder", "purge_entities", lock)
    refused = _key({lock: "GREEN"}, cap_physical_control="allow", cap_registry_write="deny")
    assert (await _call_tool("call_service", args, refused, hass, data))[1] == "denied"
    allowed = _key({lock: "GREEN"}, cap_physical_control="deny", cap_registry_write="allow")
    result = await _call_tool("call_service", args, allowed, hass, data)
    assert result[1] == "allowed", result
    assert len(ran) == 1


async def test_resetting_a_homekit_accessory_needs_no_physical_control(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("homekit", "reset_accessory"))
    data, _ = await _data(hass)
    cover = _entity(hass, "cover", "x", "blind")
    key = _key({cover: "GREEN"}, cap_physical_control="deny")
    result = await _call_tool("call_service", _call("homekit", "reset_accessory", cover), key, hass, data)
    assert result[1] == "allowed", result
    assert len(ran) == 1


# Script inputs


_SCRIPT = {"script": {
    "door": {
        "fields": {"which_lock": {"selector": {"entity": {"domain": "lock"}}}},
        "sequence": [{"action": "lock.unlock", "target": {"entity_id": "{{ which_lock }}"}}],
    },
    "undeclared": {"sequence": [{"action": "lock.unlock", "target": {"entity_id": "{{ door }}"}}]},
    "room": {
        "fields": {"where": {"selector": {"area": {}}}, "tone": {"selector": {"select": {"options": ["all", "low"]}}}},
        "sequence": [{"action": "lock.unlock", "target": {"area_id": "{{ where }}"}}],
    },
}}


async def _scripts(hass) -> tuple[PhoenixData, list, list, str, str]:
    assert await async_setup_component(hass, "homeassistant", {})
    lock_calls = _record(hass, ("lock", "unlock"))
    locked = _entity(hass, "lock", "x", "red", "locked")
    allowed = _entity(hass, "lock", "x", "green", "locked")
    assert await async_setup_component(hass, "script", _SCRIPT)
    await hass.async_block_till_done()
    data, pending = await _data(hass)
    return data, pending, lock_calls, locked, allowed


def _script_key(**fields) -> AccessKeyRecord:
    return _key(
        {"script.door": "GREEN", "script.undeclared": "GREEN", "script.room": "GREEN",
         "lock.green": "GREEN", "lock.red": "RED"},
        cap_physical_control="allow", **fields,
    )


@pytest.mark.parametrize(("script", "variables"), [
    ("script.door", {"which_lock": "lock.red"}),
    ("script.undeclared", {"door": "lock.red"}),
    ("script.undeclared", {"door": ["lock.green", "lock.red"]}),
    ("script.undeclared", {"door": "lock.green, lock.red"}),
    ("script.undeclared", {"door": "LOCK.RED"}),
    ("script.undeclared", {"nested": {"deep": [{"door": "lock.red"}]}}),
    ("script.undeclared", {"lock.red": "unlocked"}),
])
async def test_a_variable_that_names_a_forbidden_entity_runs_nothing(hass, script, variables):
    data, pending, lock_calls, _, _ = await _scripts(hass)
    key = _script_key()
    args = _call("script", "turn_on", script, {"variables": variables})
    result = await _call_tool("call_service", args, key, hass, data)
    assert result[1] == "denied", result
    await hass.async_block_till_done()
    assert lock_calls == []
    assert pending == []
    preview = await _call_tool("dry_run_service", args, key, hass, data)
    assert _body(preview)["predicted_outcome"] == "denied"


async def test_a_registry_id_in_a_variable_is_read_as_the_entity_it_names(hass):
    data, _, lock_calls, locked, _ = await _scripts(hass)
    registry_id = er.async_get(hass).async_get(locked).id
    result = await _call_tool(
        "call_service", _call("script", "turn_on", "script.undeclared", {"variables": {"door": registry_id}}),
        _script_key(), hass, data,
    )
    assert result[1] == "denied", result
    assert lock_calls == []


async def test_a_refusal_reads_the_same_for_a_hidden_entity_and_a_missing_one(hass):
    data, _, lock_calls, _, _ = await _scripts(hass)
    _entity(hass, "lock", "x", "hidden", "locked")
    key = _script_key()
    answers = {}
    for name in ("lock.hidden", "lock.absent"):
        args = _call("script", "turn_on", "script.undeclared", {"variables": {"door": name}})
        result = await _call_tool("call_service", args, key, hass, data)
        assert result[1] == "denied"
        answers[name] = _text(result)
        preview = _body(await _call_tool("dry_run_service", args, key, hass, data))
        assert preview["predicted_outcome"] == "denied"
        answers[name + " preview"] = (preview["refusal_reason"], preview["resolved_entities"])
    assert answers["lock.hidden"] == answers["lock.absent"]
    assert answers["lock.hidden preview"] == answers["lock.absent preview"]
    assert "lock.hidden" not in answers["lock.hidden"]
    assert lock_calls == []


async def test_a_permitted_entity_in_a_variable_runs_the_script_on_it_alone(hass):
    data, _, lock_calls, _, allowed = await _scripts(hass)
    args = _call("script", "turn_on", "script.door", {"variables": {"which_lock": allowed}})
    preview = await _call_tool("dry_run_service", args, _script_key(), hass, data)
    assert _body(preview)["predicted_outcome"] == "allowed"
    result = await _call_tool("call_service", args, _script_key(), hass, data)
    assert result[1] == "allowed", result
    await hass.async_block_till_done()
    assert [call[2]["entity_id"] for call in lock_calls] in ([allowed], [[allowed]])


async def test_the_targets_of_a_script_with_an_entity_input_stay_the_scripts(hass):
    data, _, _, _, allowed = await _scripts(hass)
    ran = _record(hass, ("script", "turn_on"))
    args = _call("script", "turn_on", "script.door", {"variables": {"which_lock": allowed, "next": "script.undeclared"}})
    result = await _call_tool("call_service", args, _script_key(), hass, data)
    assert result[1] == "allowed", result
    assert ran[0][2]["entity_id"] in (["script.door"], "script.door")


async def test_a_physical_entity_in_a_variable_meets_the_physical_gate(hass):
    data, pending, lock_calls, _, allowed = await _scripts(hass)
    args = _call("script", "turn_on", "script.door", {"variables": {"which_lock": allowed}})
    denied = _script_key()
    denied.cap_physical_control = "deny"
    assert (await _call_tool("call_service", args, denied, hass, data))[1] == "denied"
    assert _body(await _call_tool("dry_run_service", args, denied, hass, data))["predicted_outcome"] == "denied"
    confirm = _script_key()
    confirm.cap_physical_control = "confirm"
    assert _body(await _call_tool("dry_run_service", args, confirm, hass, data))["predicted_outcome"] == "pending_approval"
    result = await _call_tool("call_service", args, confirm, hass, data)
    assert result[1] == "pending_approval", result
    assert lock_calls == []
    preview = pending[0]["diff"]["preview"]
    assert preview["also_reaches"] == [allowed]
    assert allowed in preview["resolved_entity_ids"]
    done = await async_execute_approved_tool("call_service", pending[0]["args"], confirm, hass, data)
    assert done[1] == "allowed", done
    await hass.async_block_till_done()
    assert len(lock_calls) == 1


async def test_an_approved_run_is_refused_when_the_named_entity_was_taken_away(hass):
    data, pending, lock_calls, _, allowed = await _scripts(hass)
    key = _script_key()
    key.cap_physical_control = "confirm"
    await _call_tool(
        "call_service", _call("script", "turn_on", "script.door", {"variables": {"which_lock": allowed}}), key, hass, data,
    )
    key.permissions.entities[allowed] = PermissionNode(state="RED")
    result = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert result[1] == "denied", result
    await hass.async_block_till_done()
    assert lock_calls == []


async def test_a_writable_input_is_no_target_for_a_script_the_key_cannot_run(hass):
    data, _, lock_calls, _, allowed = await _scripts(hass)
    key = _key({allowed: "GREEN"}, cap_physical_control="allow")
    args = _call("script", "turn_on", "script.door", {"variables": {"which_lock": allowed}})
    assert _body(await _call_tool("dry_run_service", args, key, hass, data))["predicted_outcome"] == "denied"
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "denied"
    await hass.async_block_till_done()
    assert lock_calls == []


@pytest.mark.parametrize(("variables", "outcome"), [
    ({"door": "lock.red"}, "denied"),
    ({"door": "all"}, "invalid_request"),
])
async def test_the_executor_judges_saved_inputs_itself(hass, variables, outcome):
    data, _, lock_calls, _, _ = await _scripts(hass)
    args = {
        "domain": "script", "service": "turn_on", "entity_id": ["script.undeclared"],
        "service_data": {"variables": variables},
    }
    result = await _execute_call_service(args, _script_key(), hass, data)
    assert result[1] == outcome, result
    await hass.async_block_till_done()
    assert lock_calls == []


async def test_a_notify_entity_in_a_variable_needs_the_notify_capability(hass):
    data, _, _, _, _ = await _scripts(hass)
    chat = _entity(hass, "notify", "x", "chat")
    key = _key({"script.undeclared": "GREEN", chat: "GREEN"}, cap_notify="deny")
    args = _call("script", "turn_on", "script.undeclared", {"variables": {"who": chat}})
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "denied"


async def test_mesa_judges_an_entity_named_in_a_variable_and_a_blocked_one_stops_the_call(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    lock_calls = _record(hass, ("lock", "unlock"))
    lock = _entity(hass, "lock", "x", "front", "locked")
    assert await async_setup_component(hass, "script", _SCRIPT)
    await hass.async_block_till_done()
    data, pending = await _data(hass, "enforced")
    data.mesa.store.set("script.door", _profile("script.door", "autonomous"))
    data.mesa.store.set(lock, _profile(lock, "prohibited"))
    key = _key({"script.door": "GREEN", lock: "GREEN"}, cap_physical_control="allow")
    args = _call("script", "turn_on", "script.door", {"variables": {"which_lock": lock}})
    assert _body(await _call_tool("dry_run_service", args, key, hass, data))["predicted_outcome"] == "denied"
    result = await _call_tool("call_service", args, key, hass, data)
    assert result[1] == "denied", result
    await hass.async_block_till_done()
    assert lock_calls == []
    data.mesa.store.set(lock, _profile(lock, "confirm"))
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "pending_approval"
    assert pending[0]["tool_name"] == "call_service_mesa_approved"
    assert pending[0]["args"]["entity_id"] in ("script.door", ["script.door"])
    done = await async_execute_approved_tool(pending[0]["tool_name"], pending[0]["args"], key, hass, data)
    assert done[1] == "allowed", done
    await hass.async_block_till_done()
    assert len(lock_calls) == 1


async def test_a_script_run_by_name_is_judged_by_value_as_well(hass):
    data, _, lock_calls, locked, allowed = await _scripts(hass)
    key = _script_key()
    refused = await _call_tool(
        "call_service", _call("script", "undeclared", "script.undeclared", {"door": locked}), key, hass, data,
    )
    assert refused[1] == "denied", refused
    await hass.async_block_till_done()
    assert lock_calls == []
    assert (await _call_tool(
        "call_service", _call("script", "door", "script.door", {"which_lock": allowed}), key, hass, data,
    ))[1] == "invalid_request"
    result = await _call_tool("call_service", _call("script", "undeclared", "script.undeclared", {"door": allowed}), key, hass, data)
    assert result[1] == "allowed", result


async def test_automation_trigger_variables_are_judged_too(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    lock_calls = _record(hass, ("lock", "unlock"))
    locked = _entity(hass, "lock", "x", "red", "locked")
    assert await async_setup_component(hass, "automation", {"automation": {
        "id": "open", "alias": "open", "triggers": [{"trigger": "event", "event_type": "never"}],
        "actions": [{"action": "lock.unlock", "target": {"entity_id": "{{ door }}"}}],
    }})
    await hass.async_block_till_done()
    data, _ = await _data(hass)
    automation = er.async_get(hass).async_get_entity_id("automation", "automation", "open")
    key = _key({automation: "GREEN", locked: "RED"}, cap_physical_control="allow")
    args = _call("automation", "trigger", automation, {"variables": {"door": locked}})
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "denied"
    await hass.async_block_till_done()
    assert lock_calls == []
    key.permissions.entities[locked] = PermissionNode(state="GREEN")
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "allowed"
    await hass.async_block_till_done()
    assert len(lock_calls) == 1


async def test_ordinary_variables_are_left_alone(hass):
    data, _, _, _, _ = await _scripts(hass)
    ran = _record(hass, ("script", "turn_on"))
    variables = {
        "brightness": 40, "message": "the lock is fine, thanks", "file": "report.pdf", "site": "example.com",
        "ratio": "0.5", "flag": True, "items": ["a", "b"], "nested": {"mode": "evening"},
    }
    args = _call("script", "turn_on", "script.undeclared", {"variables": variables})
    result = await _call_tool("call_service", args, _script_key(), hass, data)
    assert result[1] == "allowed", result
    assert ran[0][2]["variables"] == variables


async def test_inputs_that_select_devices_or_areas_are_refused_by_name(hass):
    data, _, lock_calls, _, _ = await _scripts(hass)
    key = _script_key()
    for script, variables, named in (
        ("script.room", {"where": "kitchen"}, "where"),
        ("script.undeclared", {"target": {"area_id": "kitchen"}}, "target"),
        ("script.undeclared", {"area_id": "kitchen"}, "area_id"),
        ("script.undeclared", {"door": "all"}, "door"),
    ):
        args = _call("script", "turn_on", script, {"variables": variables})
        result = await _call_tool("call_service", args, key, hass, data)
        assert result[1] == "invalid_request", result
        assert named in _text(result)
        assert (await _call_tool("dry_run_service", args, key, hass, data))[1] == "invalid_request"
    await hass.async_block_till_done()
    assert lock_calls == []


async def test_all_in_a_field_with_a_plain_selector_is_an_ordinary_word(hass):
    data, _, _, _, _ = await _scripts(hass)
    assert await script_input_selectors(hass, "script", "turn_on", {"variables": {"tone": "all"}}, "script.room") == ()
    assert await script_input_selectors(hass, "script", "turn_on", {"variables": {"tone": "all"}}, "script.door") == ("tone",)


async def test_only_the_input_field_of_a_script_call_is_read(hass):
    assert script_input_entities("script", "turn_off", {"x": "lock.front"}, hass) == []
    assert script_input_entities("light", "turn_on", {"variables": {"x": "lock.front"}}, hass) == []
    assert await named_script_refusal(hass, "light", "turn_on") is None


async def test_a_named_script_with_an_entity_id_input_says_the_entity_needs_access(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "script", {"script": {"s": {
        "fields": {"entity_id": {"selector": {"entity": {}}}},
        "sequence": [{"event": "x"}],
    }}})
    await hass.async_block_till_done()
    refusal = await named_script_refusal(hass, "script", "s")
    assert "script.turn_on" in refusal
    assert "same access as a target" in refusal


# Whole-device services


def _zwave(hass) -> tuple[str, str, str, str, str]:
    entry = MockConfigEntry(domain="zwave_js", entry_id="zw")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("zwave_js", "1-5")}, name="Door")
    lock = _entity(hass, "lock", "zwave_js", "front", "locked", config_entry=entry, device_id=device.id)
    battery = _entity(hass, "sensor", "zwave_js", "front_battery", "90", config_entry=entry, device_id=device.id)
    return entry.entry_id, device.id, lock, battery, "zwave"


_SET_VALUE = {"command_class": 98, "property": "targetMode", "value": 0}


async def _raw_service(hass, service: str = "set_value") -> list:
    ran = _record(hass, ("zwave_js", service))
    async_set_service_schema(hass, "zwave_js", service, {
        "name": service, "description": "x", "target": {"entity": {"integration": "zwave_js"}},
        "fields": {"broadcast": {"selector": {"boolean": {}}}},
    })
    return ran


async def test_a_raw_command_on_a_sensor_needs_write_on_the_lock_of_its_device(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass)
    data, pending = await _data(hass)
    _, device, lock, battery, _ = _zwave(hass)
    sensor_only = _key({battery: "GREEN"}, cap_physical_control="allow")
    args = _call("zwave_js", "set_value", battery, _SET_VALUE)
    result = await _call_tool("call_service", args, sensor_only, hass, data)
    assert result[1] == "denied", result
    assert ran == []
    preview = _body(await _call_tool("dry_run_service", args, sensor_only, hass, data))
    assert preview["predicted_outcome"] == "denied"
    assert lock not in json.dumps(preview)
    confirm = _key({battery: "GREEN"}, cap_physical_control="confirm")
    assert _body(await _call_tool("dry_run_service", args, confirm, hass, data))["predicted_outcome"] == "denied"
    assert (await _call_tool("call_service", args, confirm, hass, data))[1] == "denied"
    assert pending == []
    saved = {"domain": "zwave_js", "service": "set_value", "entity_id": [battery], "service_data": _SET_VALUE}
    assert (await _execute_call_service(saved, sensor_only, hass, data))[1] == "denied"
    assert ran == []
    both = _key({battery: "GREEN", lock: "GREEN"}, cap_physical_control="allow")
    assert (await _call_tool("call_service", args, both, hass, data))[1] == "allowed"
    device_key = _key({}, devices={device: "GREEN"}, cap_physical_control="allow")
    assert (await _call_tool("call_service", args, device_key, hass, data))[1] == "allowed"
    assert len(ran) == 2


@pytest.mark.parametrize(("sensor_mode", "outcome"), [
    ("autonomous", "denied"),
    (None, "denied"),
])
async def test_mesa_judges_every_entity_of_the_device_on_a_raw_command(hass, sensor_mode, outcome):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass)
    data, _ = await _data(hass, "enforced")
    _, _, lock, battery, _ = _zwave(hass)
    data.mesa.store.set(lock, _profile(lock, "prohibited"))
    if sensor_mode:
        data.mesa.store.set(battery, _profile(battery, sensor_mode))
    key = _key({battery: "GREEN", lock: "GREEN"}, cap_physical_control="allow")
    args = _call("zwave_js", "set_value", battery, _SET_VALUE)
    assert _body(await _call_tool("dry_run_service", args, key, hass, data))["predicted_outcome"] == outcome
    result = await _call_tool("call_service", args, key, hass, data, request_id="rid")
    assert result[1] == outcome, result
    assert ran == []


async def test_a_blocked_target_is_not_dropped_for_the_rest_of_the_same_device(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass)
    data, _ = await _data(hass, "enforced")
    _, _, lock, battery, _ = _zwave(hass)
    data.mesa.store.set(lock, _profile(lock, "prohibited"))
    data.mesa.store.set(battery, _profile(battery, "autonomous"))
    key = _key({battery: "GREEN", lock: "GREEN"}, cap_physical_control="allow")
    result = await _call_tool("call_service", _call("zwave_js", "set_value", [lock, battery], _SET_VALUE), key, hass, data, request_id="rid")
    assert result[1] == "denied", result
    assert ran == []


async def test_a_raw_command_under_confirm_names_the_device_and_runs_only_on_its_target(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass)
    data, pending = await _data(hass, "enforced")
    _, _, lock, battery, _ = _zwave(hass)
    data.mesa.store.set(lock, _profile(lock, "confirm"))
    data.mesa.store.set(battery, _profile(battery, "autonomous"))
    key = _key({battery: "GREEN", lock: "GREEN"}, cap_physical_control="allow")
    args = _call("zwave_js", "set_value", battery, _SET_VALUE)
    result = await _call_tool("call_service", args, key, hass, data, request_id="rid")
    assert result[1] == "pending_approval", result
    preview = pending[0]["diff"]["preview"]
    assert preview["also_reaches"] == [lock]
    assert set(preview["resolved_entity_ids"]) == {lock, battery}
    assert preview["mesa"]["confirm_entities"] == [lock]
    assert pending[0]["args"][APPROVAL_TARGETS_KEY]["entity_ids"] == sorted([lock, battery])
    done = await async_execute_approved_tool(pending[0]["tool_name"], pending[0]["args"], key, hass, data)
    assert done[1] == "allowed", done
    assert ran[0][2]["entity_id"] in ([battery], battery)


async def test_a_capability_approval_of_a_raw_command_binds_the_whole_device(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass)
    data, pending = await _data(hass)
    entry, device, lock, battery, _ = _zwave(hass)
    key = _key({battery: "GREEN", lock: "GREEN"}, cap_physical_control="confirm")
    args = _call("zwave_js", "set_value", battery, _SET_VALUE)
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "pending_approval"
    assert pending[0]["diff"]["preview"]["also_reaches"] == [lock]
    assert set(pending[0]["diff"]["preview"]["resolved_entity_ids"]) == {lock, battery}
    # A new entity on the node changes what the approval would reach.
    _entity(hass, "switch", "zwave_js", "front_extra", device_id=device, config_entry=hass.config_entries.async_get_entry(entry))
    key.permissions.entities["switch.front_extra"] = PermissionNode(state="GREEN")
    stale = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert stale[1] == "denied", stale
    assert ran == []


async def test_a_hidden_entity_of_the_device_stays_out_of_what_the_key_reads(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    await _raw_service(hass)
    data, _ = await _data(hass, "advisory")
    entry, device, lock, battery, _ = _zwave(hass)
    hidden = _entity(hass, "sensor", "zwave_js", "front_hidden", device_id=device, config_entry=hass.config_entries.async_get_entry(entry))
    second = _entity(hass, "sensor", "zwave_js", "front_second", device_id=device, config_entry=hass.config_entries.async_get_entry(entry))
    data.mesa.store.set(hidden, _profile(hidden, "prohibited"))
    data.mesa.store.set(second, _profile(second, "confirm"))
    key = _key({battery: "GREEN", lock: "GREEN"}, cap_physical_control="allow")
    args = _call("zwave_js", "set_value", battery, _SET_VALUE)
    preview = await _call_tool("dry_run_service", args, key, hass, data)
    result = await _call_tool("call_service", args, key, hass, data, request_id="rid")
    assert result[1] == "allowed", result
    for text in (_text(preview), _text(result)):
        assert hidden not in text
        assert second not in text
        assert "fixture" not in text


@pytest.mark.parametrize("flag", [True, "yes", "on", 1, "true"])
async def test_a_broadcast_flag_is_refused_however_it_is_spelled(hass, flag):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass, "multicast_set_value")
    data, pending = await _data(hass)
    _, device, _, battery, _ = _zwave(hass)
    key = _key({}, devices={device: "GREEN"}, cap_physical_control="allow")
    args = _call("zwave_js", "multicast_set_value", battery, {**_SET_VALUE, "broadcast": flag})
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "invalid_request"
    assert (await _call_tool("dry_run_service", args, key, hass, data))[1] == "invalid_request"
    assert ran == []
    assert pending == []


async def test_a_broadcast_flag_saved_in_an_approval_is_refused_at_execution(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass, "multicast_set_value")
    data, pending = await _data(hass)
    _, device, _, battery, _ = _zwave(hass)
    key = _key({}, devices={device: "GREEN"}, cap_physical_control="confirm")
    args = _call("zwave_js", "multicast_set_value", battery, _SET_VALUE)
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "pending_approval"
    saved = {**pending[0]["args"], "service_data": {**pending[0]["args"]["service_data"], "broadcast": True}}
    result = await async_execute_approved_tool("call_service", saved, key, hass, data)
    assert result[1] == "invalid_request", result
    assert ran == []


async def test_a_cleared_broadcast_flag_runs(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = await _raw_service(hass, "multicast_set_value")
    data, _ = await _data(hass)
    _, device, _, battery, _ = _zwave(hass)
    key = _key({}, devices={device: "GREEN"}, cap_physical_control="allow")
    args = _call("zwave_js", "multicast_set_value", battery, {**_SET_VALUE, "broadcast": False})
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "allowed"
    assert len(ran) == 1


def _firmware(hass, platform: str) -> tuple[str, str, str]:
    entry = MockConfigEntry(domain=platform, entry_id="fw")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(platform, "n")}, name="Node",
    )
    light = _entity(hass, "light", platform, "vault", "on", config_entry=entry, device_id=device.id)
    update = _entity(hass, "update", platform, "vault_firmware", "on", config_entry=entry, device_id=device.id)
    return device.id, light, update


@pytest.mark.parametrize("platform", ["esphome", "zha", "zwave_js"])
async def test_update_install_judges_every_entity_of_the_device(hass, platform):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("update", "install"))
    data, pending = await _data(hass, "enforced")
    _, light, update = _firmware(hass, platform)
    data.mesa.store.set(light, _profile(light, "prohibited"))
    data.mesa.store.set(update, _profile(update, "autonomous"))
    key = _key({update: "GREEN"}, cap_esphome_flash="allow", cap_restart="allow")
    args = _call("update", "install", update)
    result = await _call_tool("call_service", args, key, hass, data, request_id="rid")
    assert result[1] == "pending_approval", result
    approved = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert approved[1] == "denied", approved
    assert ran == []


async def test_update_install_runs_once_the_whole_device_passes(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("update", "install"))
    data, pending = await _data(hass, "enforced")
    _, light, update = _firmware(hass, "zha")
    data.mesa.store.set(light, _profile(light, "autonomous"))
    data.mesa.store.set(update, _profile(update, "autonomous"))
    key = _key({update: "GREEN"}, cap_restart="allow")
    assert (await _call_tool("call_service", _call("update", "install", update), key, hass, data, request_id="rid"))[1] == "pending_approval"
    result = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert result[1] == "allowed", result
    assert ran[0][2]["entity_id"] in ([update], update)


async def test_an_esphome_update_needs_the_flash_capability(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("update", "install"))
    data, pending = await _data(hass)
    _, _, update = _firmware(hass, "esphome")
    args = _call("update", "install", update)
    denied = _key({update: "GREEN"}, cap_esphome_flash="deny", cap_search="allow")
    assert (await _call_tool("call_service", args, denied, hass, data))[1] == "denied"
    assert _body(await _call_tool("dry_run_service", args, denied, hass, data))["predicted_outcome"] == "denied"
    actions = _body(await _call_tool("find_available_actions", {"entity_id": update}, denied, hass, data))
    row = next(row for row in actions["actions"] if row["service"] == "update.install")
    assert (row["available"], row["reason"]) == (False, "requires ESPHome flash capability")
    confirm = _key({update: "GREEN"}, cap_esphome_flash="confirm")
    assert (await _call_tool("call_service", args, confirm, hass, data))[1] == "pending_approval"
    assert ran == []
    assert (await async_execute_approved_tool("call_service", pending[0]["args"], confirm, hass, data))[1] == "allowed"
    assert len(ran) == 1


@pytest.mark.parametrize("pass_through", [False, True])
async def test_any_other_update_needs_its_capability_and_a_review(hass, pass_through):
    """Core, an add-on or a HACS card: new code runs, so Allow does not skip the person."""
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("update", "install"))
    data, pending = await _data(hass)
    _, _, update = _firmware(hass, "zha")
    args = _call("update", "install", update)
    denied = _key({update: "GREEN"}, cap_esphome_flash="deny", cap_restart="deny", pass_through=pass_through)
    assert (await _call_tool("call_service", args, denied, hass, data))[1] == "denied"
    key = _key({update: "GREEN"}, cap_esphome_flash="deny", cap_restart="allow", pass_through=pass_through)
    assert _body(await _call_tool("dry_run_service", args, key, hass, data))["predicted_outcome"] == "pending_approval"
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "pending_approval"
    assert ran == []
    assert (await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data))[1] == "allowed"
    assert len(ran) == 1


@pytest.mark.parametrize("service_key", sorted(PHYSICAL_COMMAND_SERVICES))
async def test_every_raw_command_needs_write_on_what_its_device_can_operate(hass, service_key):
    assert await async_setup_component(hass, "homeassistant", {})
    domain, service = service_key.split("/")
    ran = _record(hass, (domain, service))
    async_set_service_schema(hass, domain, service, {
        "name": service, "description": "x", "target": {"entity": {"integration": domain}},
    })
    data, _ = await _data(hass)
    entry = MockConfigEntry(domain=domain, entry_id="raw")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(domain, "n")}, name="N")
    _entity(hass, "switch", domain, "relay", config_entry=entry, device_id=device.id)
    sensor = _entity(hass, "sensor", domain, "power", "1", config_entry=entry, device_id=device.id)
    key = _key({sensor: "GREEN"}, cap_physical_control="allow")
    assert (await _call_tool("call_service", _call(domain, service, sensor), key, hass, data))[1] == "denied"
    assert ran == []
    assert (await _call_tool(
        "call_service", _call(domain, service, sensor), _key({}, devices={device.id: "GREEN"}, cap_physical_control="allow"),
        hass, data,
    ))[1] == "allowed"
    assert len(ran) == 1


# Scenes


async def _scene_home(hass) -> tuple[PhoenixData, list, str, str]:
    assert await async_setup_component(hass, "homeassistant", {})
    lock = _entity(hass, "lock", "x", "front", "locked")
    light = _entity(hass, "light", "x", "desk", "off")
    assert await async_setup_component(hass, "scene", {"scene": [{"name": "Seed", "entities": {light: "on"}}]})
    await hass.async_block_till_done()
    data, pending = await _data(hass)
    return data, pending, lock, light


def _scene_key(lock: str, light: str, **caps) -> AccessKeyRecord:
    return _key({lock: "GREEN", light: "GREEN"}, cap_scene_write="allow", **caps)


async def test_a_scene_with_a_physical_member_is_refused_on_deny(hass):
    data, pending, lock, light = await _scene_home(hass)
    key = _scene_key(lock, light, cap_physical_control="deny")
    args = {"config": {"name": "Door", "entities": {lock: "unlocked", light: "on"}}}
    result = await _call_tool("create_scene", args, key, hass, data)
    assert result[1] == "denied", result
    assert pending == []
    assert not Path(hass.config.path("scenes.yaml")).exists()


async def test_a_scene_with_a_physical_member_needs_approval_on_confirm_and_the_preview_says_so(hass):
    data, pending, lock, light = await _scene_home(hass)
    key = _scene_key(lock, light, cap_physical_control="confirm")
    args = {"config": {"name": "Door", "entities": {lock: "unlocked", light: "on"}}}
    result = await _call_tool("create_scene", args, key, hass, data)
    assert result[1] == "pending_approval", result
    assert pending[0]["cap_name"] == "cap_physical_control"
    assert pending[0]["diff"]["preview"]["physical_entities"] == [lock]


async def test_an_approved_scene_is_refused_once_physical_control_is_denied(hass):
    data, pending, lock, light = await _scene_home(hass)
    key = _scene_key(lock, light, cap_physical_control="confirm")
    await _call_tool("create_scene", {"config": {"name": "Door", "entities": {lock: "unlocked"}}}, key, hass, data)
    key.cap_physical_control = "deny"
    result = await async_execute_approved_tool("create_scene", pending[0]["args"], key, hass, data)
    assert result[1] == "denied", result


async def test_scene_edit_meets_the_same_gate(hass):
    data, pending, lock, light = await _scene_home(hass)
    key = _scene_key(lock, light, cap_physical_control="deny")
    args = {"scene_id": "s1", "config": {"name": "Door", "entities": {lock: "unlocked"}}}
    assert (await _call_tool("edit_scene", args, key, hass, data))[1] == "denied"
    key.cap_physical_control = "confirm"
    result = await _call_tool("edit_scene", args, key, hass, data)
    assert result[1] == "pending_approval", result
    assert pending[0]["diff"]["preview"]["physical_entities"] == [lock]


async def test_a_scene_of_ordinary_members_needs_no_physical_control(hass):
    data, pending, lock, light = await _scene_home(hass)
    key = _scene_key(lock, light, cap_physical_control="deny")
    args = {"config": {"name": "Evening", "entities": {light: "on"}}}
    result = await _call_tool("create_scene", args, key, hass, data)
    assert _body(result)["write_success"] is True
    assert light in Path(hass.config.path("scenes.yaml")).read_text()
    assert pending == []


async def test_a_group_of_covers_in_a_scene_counts_as_physical(hass):
    data, _, lock, light = await _scene_home(hass)
    member = _entity(hass, "cover", "x", "blind")
    group = _entity(hass, "group", "group", "blinds")
    hass.states.async_set(group, "open", {"entity_id": [member]})
    key = _key({group: "GREEN", member: "GREEN", light: "GREEN"}, cap_scene_write="allow", cap_physical_control="deny")
    result = await _call_tool("create_scene", {"config": {"name": "B", "entities": {group: "on"}}}, key, hass, data)
    assert result[1] == "denied", result


async def test_a_group_reaching_a_red_member_is_not_a_writable_scene_member(hass):
    data, _, lock, light = await _scene_home(hass)
    member = _entity(hass, "light", "x", "forbidden")
    group = _entity(hass, "light", "group", "lights")
    hass.states.async_set(group, "on", {"entity_id": [member]})
    key = _key({group: "GREEN", member: "RED"}, cap_scene_write="allow")
    result = await _call_tool("create_scene", {"config": {"name": "G", "entities": {group: "on"}}}, key, hass, data)
    assert result[1] == "denied", result


@pytest.mark.parametrize(("service", "service_data"), [
    ("apply", {"entities": {"lock.front": "unlocked"}}),
    ("create", {"scene_id": "made", "entities": {"lock.front": "unlocked"}}),
    ("create", {"scene_id": "made", "snapshot_entities": ["lock.front"]}),
])
@pytest.mark.parametrize("physical", ["deny", "allow"])
async def test_inline_scene_services_cannot_run_through_call_service(hass, service, service_data, physical):
    data, pending, lock, light = await _scene_home(hass)
    ran = _record(hass, ("lock", "unlock"))
    assert hass.services.has_service("scene", service)
    target = _entity(hass, "scene", "x", "here", "2026-01-01T00:00:00+00:00")
    key = _key({lock: "GREEN", target: "GREEN"}, cap_scene_write="allow", cap_physical_control=physical)
    result = await _call_tool("call_service", _call("scene", service, target, service_data), key, hass, data)
    assert result[1] in ("denied", "invalid_request"), result
    await hass.async_block_till_done()
    assert ran == []
    assert pending == []


async def test_activating_an_existing_scene_stays_judged_on_the_scene(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("scene", "turn_on"))
    data, _ = await _data(hass)
    scene = _entity(hass, "scene", "x", "evening")
    key = _key({scene: "GREEN"}, cap_physical_control="deny")
    assert (await _call_tool("call_service", _call("scene", "turn_on", scene), key, hass, data))[1] == "allowed"
    assert len(ran) == 1


# Approvals that could never run


async def _cover_group(hass) -> str:
    assert await async_setup_component(hass, "homeassistant", {})
    _entity(hass, "cover", "x", "left", "open")
    _entity(hass, "cover", "x", "right", "open")
    assert await async_setup_component(hass, "cover", {"cover": [{
        "platform": "group", "name": "Blinds", "unique_id": "blinds", "entities": ["cover.left", "cover.right"],
    }]})
    await hass.async_block_till_done()
    return "cover.blinds"


async def test_an_unavailable_group_binds_the_members_it_holds_and_its_approval_runs(hass):
    group = await _cover_group(hass)
    ran = _record(hass, ("cover", "open_cover"))
    hass.states.async_set(group, "unavailable", {})
    assert target_binding([group], hass)["fingerprint"] is not None
    data, pending = await _data(hass)
    key = _key({group: "GREEN", "cover.left": "GREEN", "cover.right": "GREEN"}, cap_physical_control="confirm")
    assert (await _call_tool("call_service", _call("cover", "open_cover", group), key, hass, data))[1] == "pending_approval"
    done = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert done[1] == "allowed", done
    assert len(ran) == 1


async def test_a_group_of_unregistered_members_queues_no_approval_that_cannot_run(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("cover", "open_cover"))
    data, pending = await _data(hass)
    group = _entity(hass, "cover", "group", "yaml_blinds", "open")
    hass.states.async_set("cover.yaml_member", "open")
    hass.states.async_set(group, "open", {"entity_id": ["cover.yaml_member"]})
    key = _key({group: "GREEN", "cover.yaml_member": "GREEN"}, cap_physical_control="confirm")
    args = _call("cover", "open_cover", group)
    assert _body(await _call_tool("dry_run_service", args, key, hass, data))["predicted_outcome"] == "denied"
    result = await _call_tool("call_service", args, key, hass, data)
    assert result[1] == "denied", result
    assert group in _text(result)
    assert pending == []
    assert ran == []


async def test_a_native_tool_queues_no_approval_that_cannot_run(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    data, pending = await _data(hass)
    hass.states.async_set("cover.yaml_only", "open")
    key = _key({"cover.yaml_only": "GREEN"}, cap_physical_control="confirm")
    result = await evaluate_capability(
        "cap_physical_control", key, hass, data, tool_name="HassTurnOn", args={}, request_id="r",
        target_entities=["cover.yaml_only"],
    )
    assert result.is_deny
    assert pending == []


async def test_mesa_confirm_queues_no_approval_that_cannot_run(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _record(hass, ("cover", "open_cover"))
    data, pending = await _data(hass, "enforced")
    group = _entity(hass, "cover", "group", "yaml_blinds", "open")
    hass.states.async_set("cover.yaml_member", "open")
    hass.states.async_set(group, "open", {"entity_id": ["cover.yaml_member"]})
    data.mesa.store.set(group, _profile(group, "confirm"))
    key = _key({group: "GREEN", "cover.yaml_member": "GREEN"}, cap_physical_control="allow")
    result = await _call_tool("call_service", _call("cover", "open_cover", group), key, hass, data, request_id="rid")
    assert result[1] == "denied", result
    assert pending == []
    assert ran == []
