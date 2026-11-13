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

from custom_components.phoenix_mcp import template_render
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


async def _core_entity_class(home, platform: str, key: str) -> bool:
    """Whether the installed Core registers ``key`` as an entity trigger or condition.

    Judged by the class's own hierarchy names, not by where Core keeps the base class, which
    moved between releases.
    """
    from homeassistant.helpers import condition as ha_condition, trigger as ha_trigger
    from homeassistant.loader import async_get_integration

    helpers = ha_trigger if platform == "trigger" else ha_condition
    domain = key.split(".", 1)[0]
    try:
        module = await (await async_get_integration(home, domain)).async_get_platform(platform)
        getter = module.async_get_triggers if platform == "trigger" else module.async_get_conditions
        found = (await getter(home)).get(helpers.get_relative_description_key(domain, key))
    except Exception:
        return False
    base = "EntityTriggerBase" if platform == "trigger" else "EntityConditionBase"
    return isinstance(found, type) and any(cls.__name__ == base for cls in found.__mro__)


async def _entity_conditions(home) -> bool:
    assert await async_setup_component(home, "light", {})
    return await _core_entity_class(home, "condition", "light.is_on")


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
    assert await async_setup_component(home, "light", {})
    if not await _core_entity_class(home, "trigger", "light.turned_on"):
        pytest.skip("this Home Assistant release has no light.turned_on entity trigger")
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


def _body_or_text(content: dict) -> str:
    text = _text(content)
    return "" if text.startswith("Not found") else text


async def test_an_entity_trigger_with_an_entity_target_runs(home):
    """On a release whose light integration has no such entity trigger it is refused, not guessed at."""
    assert await async_setup_component(home, "light", {})
    trigger = {"trigger": "light.turned_on", "target": {"entity_id": LAMP}}
    if not await _core_entity_class(home, "trigger", "light.turned_on"):
        content, outcome = await _call("test_trigger", {"trigger": trigger, "timeout": 1}, _key(), home)
        assert outcome == "invalid_request" and "only Core's entity" in _text(content), content
        return
    content, outcome = await _fire_after_registration(
        home, {"trigger": trigger, "timeout": 5}, _key(),
        lambda: home.states.async_set(LAMP, "on", {"brightness": 10}))
    assert outcome == "allowed", content
    assert _body(content)["fired"] is True


@pytest.mark.parametrize("trigger", [
    {"trigger": "template", "value_template": "{{ is_state('light.lamp', 'on') }}"},
    {"trigger": "numeric_state", "entity_id": SENSOR, "above": 1, "value_template": "{{ 5 }}"},
    {"trigger": "state", "entity_id": LAMP, "for": "{{ '00:00:01' }}"},
])
async def test_a_trigger_template_is_refused_before_home_assistant_sees_it(home, trigger):
    with patch.object(automation_test.ha_trigger, "async_validate_trigger_config") as validate:
        content, outcome = await _call("test_trigger", {"trigger": trigger, "timeout": 1}, _key(), home)
    assert outcome == "invalid_request", content
    assert "Templates cannot be tested with test_trigger" in _text(content)
    assert "test_condition" in _text(content)
    validate.assert_not_called()


async def test_a_key_that_may_write_automations_tests_a_template_trigger(home):
    key = _key(cap_automation_write="confirm")
    content, outcome = await _fire_after_registration(
        home, {"trigger": {"trigger": "template", "value_template": "{{ is_state('light.lamp', 'on') }}"}, "timeout": 5},
        key, lambda: home.states.async_set(LAMP, "on", {"brightness": 10, "access_token": "abc"}))
    assert outcome == "allowed", content
    body = _body(content)
    assert body["fired"] is True
    assert body["trigger"]["entity_id"] == LAMP
    assert "access_token" not in json.dumps(body)


async def test_a_template_trigger_still_needs_read_on_every_entity_it_looks_up(home):
    key = _key(cap_automation_write="allow")
    for target in (HIDDEN, MISSING):
        with patch.object(automation_test.ha_trigger, "async_validate_trigger_config") as validate:
            content, outcome = await _call("test_trigger", {"trigger": {
                "trigger": "template", "value_template": f"{{{{ is_state('{target}', 'on') }}}}"}, "timeout": 1}, key, home)
        assert outcome == "not_found", content
        validate.assert_not_called()
    content, outcome = await _call("test_trigger", {"trigger": {
        "trigger": "template", "value_template": "{{ states('light.' ~ 'vault') == 'on' }}"}, "timeout": 1}, key, home)
    assert outcome == "invalid_request" and "computed" in _text(content), content


@pytest.mark.parametrize("worker,trigger,expected", [
    (None, {"trigger": "template", "value_template": "{{ as_timestamp('\\ud800', 0) }}"}, "lone surrogate"),
    ((1, b""), {"trigger": "template", "value_template": "{{ is_state('light.lamp', 'on') }}"}, "exited unexpectedly"),
    ((0, b'{"compile_seconds": 9}'), {"trigger": "state", "entity_id": LAMP, "for": "{{ '00:00:01' }}"}, "too long to compile"),
])
async def test_a_trigger_template_the_worker_refuses_never_reaches_home_assistant(home, worker, trigger, expected):
    async def failing(payload, hass, data):
        return worker

    with patch.object(automation_test.ha_trigger, "async_validate_trigger_config") as validate, \
            patch.object(template_render, "_run_worker", failing if worker else template_render._run_worker):
        content, outcome = await _call("test_trigger", {"trigger": trigger, "timeout": 1},
                                       _key(cap_automation_write="allow"), home)
    assert outcome == "invalid_request", content
    assert "Templates refused before Home Assistant validated them" in _text(content)
    assert expected in _text(content), content
    validate.assert_not_called()


async def _validated_conditions(home, args: dict, key: AccessKeyRecord) -> tuple[dict, str, list]:
    """Run test_condition and record every config handed to Home Assistant's validator."""
    seen: list = []
    real = automation_test.ha_condition.async_validate_condition_config

    async def validate(hass, config):
        seen.append(json.dumps(config, default=str))
        return await real(hass, config)

    with patch.object(automation_test.ha_condition, "async_validate_condition_config", validate):
        content, outcome = await _call("test_condition", args, key, home)
    return content, outcome, seen


@pytest.mark.parametrize(("definition", "expected"), [
    ("{{ (states('sensor.temperature') | float) * 2 > 40 }}", True),
    ({"and": [{"condition": "state", "entity_id": DESK, "state": "on"}, "{{ is_state('light.lamp', 'on') }}"]}, False),
    ({"condition": "state", "entity_id": DESK, "state": "on", "for": "{{ '00:00:00' }}"}, True),
])
async def test_home_assistant_only_sees_references_to_worker_results(home, definition, expected):
    content, outcome, seen = await _validated_conditions(home, {"condition": definition}, _key())
    assert outcome == "allowed", content
    assert _body(content)["result"] is expected
    assert seen and all("states(" not in config and "is_state" not in config and "'00:00:00'" not in config
                        for config in seen), seen
    assert "_phoenix_template_0" in seen[0]


async def test_a_failed_template_inside_or_still_lets_another_branch_pass(home):
    definition = {"or": ["{{ (states('sensor.temperature') | float) / 0 > 1 }}",
                         {"condition": "state", "entity_id": DESK, "state": "on"}]}
    content, outcome = await _call("test_condition", {"condition": definition}, _key(), home)
    assert outcome == "allowed", content
    assert _body(content)["result"] is True
    alone = await _call("test_condition", {"condition": definition["or"][0]}, _key(), home)
    assert alone[1] == "invalid_request", alone
    assert "could not be evaluated" in _text(alone[0])


async def test_a_numeric_state_value_template_reads_the_scrubbed_entity_as_state(home):
    """Core binds state to the entity; a caller variable of that name never reaches its secrets."""
    leak = {"condition": "numeric_state", "entity_id": LAMP, "above": 0.5,
            "value_template": "{{ 1 if state.attributes.access_token is defined else 0 }}"}
    content, outcome = await _call("test_condition", {"condition": leak, "variables": {"state": {}}}, _key(), home)
    assert outcome == "allowed", content
    assert _body(content)["result"] is False
    doubled = {"condition": "numeric_state", "entity_id": SENSOR, "above": 40,
               "value_template": "{{ state.state | float * 2 }}"}
    content, outcome = await _call("test_condition", {"condition": doubled, "variables": {"state": {}}}, _key(), home)
    assert outcome == "allowed", content
    assert _body(content)["result"] is True


async def test_undefined_names_are_reported_as_template_errors(home):
    content, outcome = await _call("test_condition", {"condition": "{{ (trigger.to_state ~ '') == '' }}",
                                                      "variables": {"trigger": {}}}, _key(), home)
    assert outcome == "allowed", content
    assert any("to_state" in error for error in _body(content)["template_errors"]), content


async def test_a_lone_surrogate_escape_is_refused_without_the_worker(home):
    from custom_components.phoenix_mcp import template_render

    with patch.object(template_render, "_run_worker", side_effect=AssertionError("spawned")):
        content, outcome = await _call(
            "test_condition", {"condition": '{{ as_timestamp("\\ud800", 0) > 0 }}'}, _key(), home)
    assert outcome == "invalid_request", content
    assert "lone surrogate" in _text(content)


async def test_a_worker_failure_refuses_before_home_assistant_validates(home):
    from custom_components.phoenix_mcp import template_render

    failure = template_render.TemplateRenderError("Template rendering exceeded its CPU budget. Reduce the template workload.")
    with patch.object(template_render, "_run_worker", side_effect=failure), \
            patch.object(automation_test.ha_condition, "async_validate_condition_config") as validate:
        content, outcome = await _call("test_condition", {"condition": "{{ 2 ** 3 > 1 }}"}, _key(), home)
    assert outcome == "invalid_request", content
    assert "CPU budget" in _text(content)
    validate.assert_not_called()


async def test_scope_is_checked_again_after_the_condition_is_evaluated(home):
    key = _key()
    real = automation_test.ha_condition.async_from_config

    async def narrowing(hass, config):
        checker = await real(hass, config)
        check = checker.async_check

        def async_check(*args, **kwargs):
            key.permissions = _key({LAMP: "RED", DESK: "GREEN"}).permissions
            return check(*args, **kwargs)

        checker.async_check = async_check
        return checker

    with patch.object(automation_test.ha_condition, "async_from_config", narrowing):
        content, outcome = await _call("test_condition", {"condition": {
            "condition": "state", "entity_id": LAMP, "state": "off"}}, key, home)
    assert outcome == "not_found", content
    assert "result" not in _text(content)


@pytest.mark.parametrize("field", ["area_id", "label_id", "floor_id"])
async def test_an_empty_selector_answers_like_a_missing_one(home, rooms, field):
    if not await _entity_conditions(home):
        pytest.skip("this Home Assistant release has no entity conditions")
    from homeassistant.helpers import area_registry as ar, floor_registry as fr, label_registry as lr

    empty = {
        "area_id": lambda: ar.async_get(home).async_create("Outside").id,
        "label_id": lambda: lr.async_get(home).async_create("Unused").label_id,
        "floor_id": lambda: fr.async_get(home).async_create("Attic").floor_id,
    }[field]()
    found = await _call("test_condition", _targeted(field, empty), _key(), home)
    missing = await _call("test_condition", _targeted(field, "no-such-id"), _key(), home)
    assert found[1] == missing[1] == "not_found", (found, missing)
    assert _text(found[0]) == _text(missing[0])
    beside = await _call("test_condition", {"condition": {"condition": "light.is_on", "target": {
        field: empty, "entity_id": LAMP}}}, _key(), home)
    assert beside[1] == "not_found", beside
