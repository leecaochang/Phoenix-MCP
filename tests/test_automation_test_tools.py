"""test_trigger and test_condition read only what the key may read, through Core's own helpers.

Every entity a definition names, by ID, registry ID or literal template lookup, needs Read before
anything is registered and again before a result is returned; a hidden and a missing entity are
refused alike. Kinds whose reach no entity scope bounds are refused whole.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools import automation_test

LAMP, DESK, HIDDEN, MISSING = "light.lamp", "light.desk", "light.vault", "light.nowhere"
SENSOR, GROUP = "sensor.temperature", "group.lights"


def _key(entities: dict[str, str] | None = None, **fields) -> AccessKeyRecord:
    fields.setdefault("cap_config_read", "allow")
    grants = entities if entities is not None else {LAMP: "YELLOW", DESK: "GREEN", SENSOR: "YELLOW", GROUP: "YELLOW", HIDDEN: "RED"}
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(entities={e: PermissionNode(state=s) for e, s in grants.items()}), **fields,
    )


async def _call(name: str, args: dict, key: AccessKeyRecord, hass) -> tuple[dict, str]:
    content, outcome, _ = await _call_tool(name, args, key, hass, MagicMock())
    return content, outcome


def _body(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


def _text(content: dict) -> str:
    return content["content"][0]["text"]


@pytest.fixture
async def home(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    registry = er.async_get(hass)
    for entity_id in (LAMP, DESK, HIDDEN, SENSOR):
        domain, object_id = entity_id.split(".")
        registry.async_get_or_create(domain, "test", object_id, suggested_object_id=object_id)
    hass.states.async_set(LAMP, "off", {"brightness": 0, "access_token": "abc"})
    hass.states.async_set(DESK, "on")
    hass.states.async_set(HIDDEN, "on")
    hass.states.async_set(SENSOR, "21.5")
    hass.states.async_set(GROUP, "on", {"entity_id": [LAMP, HIDDEN]})
    return hass


async def _fire_after_registration(hass, args: dict, key: AccessKeyRecord, change) -> tuple[dict, str]:
    """Run test_trigger and make a change only once its triggers are registered."""
    ready = asyncio.Event()
    real = automation_test.ha_trigger.async_initialize_triggers

    async def registered(*a, **kw):
        remove = await real(*a, **kw)
        ready.set()
        return remove

    with patch.object(automation_test.ha_trigger, "async_initialize_triggers", registered):
        task = asyncio.create_task(_call("test_trigger", args, key, hass))
        await asyncio.wait_for(ready.wait(), timeout=2)
        change()
        await hass.async_block_till_done()
        return await task


async def test_a_state_trigger_reports_its_firing_with_the_state_scrubbed(home):
    content, outcome = await _fire_after_registration(
        home, {"trigger": {"trigger": "state", "entity_id": LAMP, "to": "on"}, "timeout": 5}, _key(),
        lambda: home.states.async_set(LAMP, "on", {"brightness": 200, "access_token": "abc"}))
    assert outcome == "allowed", content
    body = _body(content)
    assert body["fired"] is True
    assert body["trigger"]["entity_id"] == LAMP
    assert body["trigger"]["to_state"]["state"] == "on"
    assert "access_token" not in body["trigger"]["to_state"]["attributes"]
    assert "abc" not in _text(content)


async def test_a_trigger_that_does_not_fire_times_out(home):
    content, outcome = await _call("test_trigger", {"trigger": {"trigger": "state", "entity_id": LAMP, "to": "on"}, "timeout": 1}, _key(), home)
    assert outcome == "allowed"
    assert _body(content) == {"fired": False, "timeout_seconds": 1}


async def test_the_trigger_is_removed_after_the_test(home):
    before = home.bus.async_listeners().get("state_changed", 0)
    await _call("test_trigger", {"trigger": {"trigger": "state", "entity_id": LAMP}, "timeout": 1}, _key(), home)
    assert home.bus.async_listeners().get("state_changed", 0) == before


@pytest.mark.parametrize("tool,definition", [
    ("test_trigger", {"trigger": {"trigger": "state", "entity_id": "{target}"}}),
    ("test_trigger", {"trigger": {"trigger": "state", "entity_id": f"{LAMP}, {{target}}"}}),
    ("test_trigger", {"trigger": {"trigger": "numeric_state", "entity_id": SENSOR, "above": "{target}"}}),
    ("test_trigger", {"trigger": {"trigger": "template", "value_template": "{{{{ is_state('{target}', 'on') }}}}"}}),
    ("test_condition", {"condition": {"condition": "state", "entity_id": "{target}", "state": "on"}}),
    ("test_condition", {"condition": "{{{{ states.{target}.state == 'on' }}}}"}),
    ("test_condition", {"condition": {"or": [{"condition": "state", "entity_id": LAMP, "state": "on"},
                                              {"not": {"condition": "state", "entity_id": "{target}", "state": "on"}}]}}),
])
async def test_a_hidden_and_a_missing_entity_are_refused_alike(home, tool, definition):
    def with_target(target: str) -> dict:
        return json.loads(json.dumps(definition).replace("{target}", target).replace("{{", "{").replace("}}", "}")
                          if tool == "test_condition" and isinstance(definition["condition"], str)
                          else json.dumps(definition).replace("{target}", target).replace("{{{{", "{{").replace("}}}}", "}}"))
    hidden = await _call(tool, with_target(HIDDEN), _key(), home)
    missing = await _call(tool, with_target(MISSING), _key(), home)
    assert hidden[1] == missing[1] == "not_found", (hidden, missing)
    assert _text(hidden[0]) == _text(missing[0])


async def test_a_registry_id_naming_a_hidden_entity_is_refused(home):
    entry = er.async_get(home).async_get(HIDDEN)
    content, outcome = await _call("test_trigger", {"trigger": {"trigger": "state", "entity_id": entry.id}}, _key(), home)
    assert outcome == "not_found", content


@pytest.mark.parametrize("trigger", [
    {"trigger": "event", "event_type": "state_changed"},
    {"trigger": "mqtt", "topic": "home/#"},
    {"trigger": "webhook", "webhook_id": "probe"},
    {"trigger": "conversation", "command": "open sesame"},
    {"trigger": "tag", "tag_id": "x"},
])
async def test_kinds_without_an_entity_scope_are_refused(home, trigger):
    content, outcome = await _call("test_trigger", {"trigger": trigger, "timeout": 1}, _key(), home)
    assert outcome == "invalid_request", content
    assert "cannot be tested here" in _text(content) or "Device, area" in _text(content)


@pytest.fixture
async def rooms(home):
    """LAMP and DESK on one device in the lounge; HIDDEN on another in the vault; a label on each."""
    from homeassistant.helpers import area_registry as ar, device_registry as dr, floor_registry as fr
    from homeassistant.helpers import label_registry as lr
    from pytest_homeassistant.common import MockConfigEntry

    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(home)
    floor = fr.async_get(home).async_create("Ground")
    lounge = ar.async_get(home).async_create("Lounge", floor_id=floor.floor_id)
    vault = ar.async_get(home).async_create("Vault")
    shared = lr.async_get(home).async_create("Shared")
    lights = lr.async_get(home).async_create("Readable")
    devices, entities = dr.async_get(home), er.async_get(home)
    lamp_device = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "lamps")})
    vault_device = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "vault")})
    devices.async_update_device(lamp_device.id, area_id=lounge.id)
    devices.async_update_device(vault_device.id, area_id=vault.id)
    for entity_id, device, labels in ((LAMP, lamp_device, {shared.label_id, lights.label_id}),
                                      (DESK, lamp_device, {lights.label_id}),
                                      (HIDDEN, vault_device, {shared.label_id})):
        entities.async_update_entity(entity_id, device_id=device.id, labels=labels)
    return {"lounge": lounge.id, "vault": vault.id, "floor": floor.floor_id, "shared": shared.label_id,
            "readable": lights.label_id, "lamp_device": lamp_device.id, "vault_device": vault_device.id,
            "ground": floor.floor_id}


def _targeted(field: str, value: str) -> dict:
    return {"condition": {"condition": "light.is_on", "target": {field: value}}}


async def _entity_conditions(home) -> bool:
    assert await async_setup_component(home, "light", {})
    return _has_entity_triggers()


@pytest.mark.parametrize(("field", "readable", "hidden"), [
    ("area_id", "lounge", "vault"),
    ("device_id", "lamp_device", "vault_device"),
    ("label_id", "readable", "shared"),
])
async def test_a_target_is_admitted_only_when_everything_it_expands_to_is_readable(home, rooms, field, readable, hidden):
    if not await _entity_conditions(home):
        pytest.skip("this Home Assistant release has no entity conditions")
    allowed = await _call("test_condition", _targeted(field, rooms[readable]), _key(), home)
    assert allowed[1] == "allowed", allowed
    refused = await _call("test_condition", _targeted(field, rooms[hidden]), _key(), home)
    unknown = await _call("test_condition", _targeted(field, "no-such-id"), _key(), home)
    assert refused[1] == unknown[1] == "not_found", (refused, unknown)
    assert _text(refused[0]) == _text(unknown[0])


async def test_a_diagnostic_entity_counts_only_where_core_tracks_it(home, rooms):
    """light.is_on skips configuration and diagnostic entities, as Core's own class says."""
    if not await _entity_conditions(home):
        pytest.skip("this Home Assistant release has no entity conditions")
    from homeassistant.const import EntityCategory
    signal = er.async_get(home).async_get_or_create(
        "sensor", "test", "lamp_signal", suggested_object_id="lamp_signal",
        device_id=rooms["lamp_device"], entity_category=EntityCategory.DIAGNOSTIC)
    home.states.async_set(signal.entity_id, "-60")
    allowed = await _call("test_condition", _targeted("area_id", rooms["lounge"]), _key(), home)
    assert allowed[1] == "allowed", allowed
    refused = await _call("test_condition", {"condition": {"condition": "device", "device_id": rooms["lamp_device"],
                                                           "domain": "light", "type": "is_on", "entity_id": LAMP}}, _key(), home)
    assert refused[1] == "not_found", refused


async def test_a_floor_target_reaches_its_areas(home, rooms):
    if not await _entity_conditions(home):
        pytest.skip("this Home Assistant release has no entity conditions")
    assert (await _call("test_condition", _targeted("floor_id", rooms["ground"]), _key(), home))[1] == "allowed"
    from homeassistant.helpers import area_registry as ar
    ar.async_get(home).async_update(rooms["vault"], floor_id=rooms["ground"])
    assert (await _call("test_condition", _targeted("floor_id", rooms["ground"]), _key(), home))[1] == "not_found"


async def test_an_exclude_block_does_not_narrow_what_must_be_readable(home, rooms):
    if not await _entity_conditions(home):
        pytest.skip("this Home Assistant release has no entity conditions")
    content, outcome = await _call("test_condition", {"condition": {"condition": "light.is_on", "target": {
        "label_id": rooms["shared"], "exclude": {"entity_id": HIDDEN}}}}, _key(), home)
    assert outcome == "not_found", content


async def test_a_device_trigger_needs_read_on_every_entity_of_its_device(home, rooms):
    assert await async_setup_component(home, "light", {})
    trigger = {"trigger": "device", "domain": "light", "type": "turned_on", "entity_id": DESK}
    refused = await _call("test_trigger", {"trigger": {**trigger, "device_id": rooms["vault_device"]}, "timeout": 1}, _key(), home)
    assert refused[1] == "not_found", refused
    content, outcome = await _call("test_trigger", {"trigger": {**trigger, "device_id": rooms["lamp_device"]}, "timeout": 1}, _key(), home)
    assert outcome in ("allowed", "invalid_request"), content
    assert "Not found" not in _text(content)


async def test_an_area_is_expanded_again_after_the_wait(home, rooms):
    if not _has_entity_triggers():
        pytest.skip("this Home Assistant release has no entity triggers")
    assert await async_setup_component(home, "light", {})
    ready = asyncio.Event()
    real = automation_test.ha_trigger.async_initialize_triggers

    async def registered(*a, **kw):
        remove = await real(*a, **kw)
        ready.set()
        return remove

    with patch.object(automation_test.ha_trigger, "async_initialize_triggers", registered):
        task = asyncio.create_task(_call("test_trigger", {"trigger": {
            "trigger": "light.turned_on", "target": {"area_id": rooms["lounge"]}}, "timeout": 2}, _key(), home))
        await asyncio.wait_for(ready.wait(), timeout=2)
        from homeassistant.helpers import device_registry as dr
        dr.async_get(home).async_update_device(rooms["vault_device"], area_id=rooms["lounge"])
        content, outcome = await task
    assert outcome == "not_found", content


@pytest.mark.parametrize("field", ["device_id", "area_id"])
async def test_an_unscoped_reference_outside_a_target_is_refused(home, field):
    content, outcome = await _call("test_condition", {"condition": {
        "condition": "state", "entity_id": LAMP, "state": "on", "options": {field: "anything"}}}, _key(), home)
    assert outcome == "invalid_request", content
    assert "Device, area, floor and label" in _text(content)


async def test_a_computed_template_lookup_is_refused(home):
    content, outcome = await _call("test_condition", {"condition": "{{ states('light.' ~ 'vault') == 'on' }}"}, _key(), home)
    assert outcome == "invalid_request", content
    assert "literal entity ID" in _text(content)


@pytest.mark.parametrize("definition", [
    {"condition": "state", "entity_id": LAMP, "attribute": "access_token", "state": "abc"},
    {"condition": "state", "entity_id": GROUP, "attribute": "entity_id", "state": [LAMP]},
    "{{ state_attr('light.lamp', 'access_token') == 'abc' }}",
])
async def test_an_attribute_the_key_cannot_read_as_is_cannot_be_compared(home, definition):
    content, outcome = await _call("test_condition", {"condition": definition}, _key(), home)
    assert outcome in ("invalid_request", "denied"), content
    assert "abc" not in _text(content) and HIDDEN not in _text(content)


async def test_the_capability_is_required(home):
    content, outcome = await _call("test_condition", {"condition": {"condition": "state", "entity_id": LAMP, "state": "off"}},
                                   _key(cap_config_read="deny"), home)
    assert outcome == "denied"


@pytest.mark.parametrize(("definition", "expected"), [
    ({"condition": "state", "entity_id": LAMP, "state": "off"}, True),
    ({"condition": "state", "entity_id": LAMP, "state": "on"}, False),
    ({"condition": "numeric_state", "entity_id": SENSOR, "above": 20}, True),
    ({"and": [{"condition": "state", "entity_id": DESK, "state": "on"}, "{{ states('sensor.temperature') | float > 30 }}"]}, False),
    ("{{ is_state('light.desk', 'on') }}", True),
])
async def test_a_condition_reports_its_result(home, definition, expected):
    content, outcome = await _call("test_condition", {"condition": definition}, _key(), home)
    assert outcome == "allowed", content
    assert _body(content)["result"] is expected


async def test_variables_are_plain_data_for_templates(home):
    condition = "{{ trigger.to_state.state == 'on' and is_state('light.desk', 'on') }}"
    variables = {"trigger": {"to_state": {"state": "on"}}}
    content, outcome = await _call("test_condition", {"condition": condition, "variables": variables}, _key(), home)
    assert outcome == "allowed", content
    assert _body(content)["result"] is True
    # Plain data still cannot pick the entity a lookup reads.
    computed = "{{ is_state(trigger.entity_id, 'on') }}"
    refused = await _call("test_condition", {"condition": computed, "variables": {"trigger": {"entity_id": HIDDEN}}}, _key(), home)
    assert refused[1] == "invalid_request", refused


@pytest.mark.parametrize("name", ["states", "is_state", "_private", "9lives", "has space"])
async def test_a_variable_cannot_shadow_a_template_function(home, name):
    content, outcome = await _call("test_condition", {"condition": "{{ true }}", "variables": {name: 1}}, _key(), home)
    assert outcome == "invalid_request", content


async def test_a_template_error_is_reported_scrubbed(home):
    content, outcome = await _call("test_condition", {"condition": "{{ undefined_thing.attr == 1 }}",
                                                      "variables": {"undefined_thing": None}}, _key(), home)
    assert outcome in ("allowed", "invalid_request"), content


async def test_scope_is_checked_again_after_the_wait(home):
    key = _key()
    narrowed = _key({**{LAMP: "RED"}, DESK: "GREEN"})
    ready = asyncio.Event()
    real = automation_test.ha_trigger.async_initialize_triggers

    async def registered(*a, **kw):
        remove = await real(*a, **kw)
        ready.set()
        return remove

    with patch.object(automation_test.ha_trigger, "async_initialize_triggers", registered):
        task = asyncio.create_task(_call("test_trigger", {"trigger": {"trigger": "state", "entity_id": LAMP}, "timeout": 5}, key, home))
        await asyncio.wait_for(ready.wait(), timeout=2)
        key.permissions = narrowed.permissions
        home.states.async_set(LAMP, "on")
        content, outcome = await task
    assert outcome == "not_found", content
    assert "on" not in _body_or_text(content)


def _has_entity_triggers() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("homeassistant.helpers.trigger.entity_trigger") is not None
    except ModuleNotFoundError:
        return False


def _body_or_text(content: dict) -> str:
    text = _text(content)
    return "" if text.startswith("Not found") else text


async def test_an_entity_trigger_with_an_entity_target_runs(home):
    """On a release without Core's entity triggers the same definition is refused, not guessed at."""
    assert await async_setup_component(home, "light", {})
    trigger = {"trigger": "light.turned_on", "target": {"entity_id": LAMP}}
    if not _has_entity_triggers():
        content, outcome = await _call("test_trigger", {"trigger": trigger, "timeout": 1}, _key(), home)
        assert outcome == "invalid_request" and "only Core's entity" in _text(content), content
        return
    content, outcome = await _fire_after_registration(
        home, {"trigger": trigger, "timeout": 5}, _key(),
        lambda: home.states.async_set(LAMP, "on", {"brightness": 10}))
    assert outcome == "allowed", content
    assert _body(content)["fired"] is True
