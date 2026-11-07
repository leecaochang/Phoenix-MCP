"""Template helper authoring is scoped to what the access key may read.

Static dependency analysis, plus ingress and executor checks against real HA helpers.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.template import Template
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from homeassistant.components.template import config_flow as template_flow

_TEMPLATE_OPTIONS_SECTION = getattr(template_flow, "CONF_ADDITIONAL_OPTIONS", "advanced_options")

from tests.runtime_fixtures import mesa_off_store

from custom_components.phoenix_mcp.const import HELPER_TEMPLATE_MAX_CHARS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.template_recovery import restore_template
from custom_components.phoenix_mcp.template_snapshots import template_snapshot
from custom_components.phoenix_mcp.template_scope import HelperScopeError, template_entities
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord
from custom_components.phoenix_mcp.tools import helper


def agent(**caps):
    return AccessKeyRecord(
        id=uuid.uuid4().hex, name="scope-test", access_key_hash="x", created_at=utcnow(), created_by="test",
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN"),
                                           "button": PermissionNode(state="GREEN")}),
        cap_helper_write=caps.get("cap_helper_write", "allow"),
    )


def runtime():
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())


def steps(expression, **extra):
    return [{"step_id": "user", "data": {"next_step_id": "sensor"}},
            {"step_id": "sensor", "data": {"name": "Scope helper", "state": expression, **extra}}]


@pytest.mark.parametrize("expression,entities", [
    ("{{ 7 }}", set()), ("{{ true }}", set()), ("{{ ['one', 'two'] }}", set()),
    ("{{ '1.0' }}", set()), ("{{ today_at() }}", set()),
    ("{{ now().replace(hour=0, minute=0).hour }}", set()),
    ("{{ states('sensor.source') | float(0) + 2 }}", {"sensor.source"}),
    ("{{ states(entity_id='sensor.source') }}", {"sensor.source"}),
    ("{{ state_attr('sensor.source', 'unit_of_measurement') }}", {"sensor.source"}),
    ("{{ is_state('sensor.source', '7') and has_value('sensor.other') }}", {"sensor.source", "sensor.other"}),
    ("{{ 'sensor.source' | states }}", {"sensor.source"}),
    ("{{ 'sensor.source' is is_state('7') }}", {"sensor.source"}),
    ("{{ states.sensor.source.state }}", {"sensor.source"}),
    ("{{ states['sensor.source'].attributes['unit_of_measurement'] }}", {"sensor.source"}),
    ("{{ states['sensor']['source'].state }}", {"sensor.source"}),
    ("{% if is_state('sensor.source', '7') %}yes{% else %}no{% endif %}", {"sensor.source"}),
    ("{{ state_attr('sensor.source', 'list')[0] }}", {"sensor.source"}),
])
async def test_supported_templates_match_real_ha_syntax(hass, expression, entities):
    """Positive controls use the installed HA renderer, not a fake grammar."""
    hass.states.async_set("sensor.source", "7", {"unit_of_measurement": "W", "list": [1, 2]})
    hass.states.async_set("sensor.other", "2")
    assert template_entities(expression) == entities
    Template(expression, hass).async_render()


UNRESOLVED = [
    "{{ states }}", "{{ states.sensor }}", "{{ states() }}",
    "{{ states('sensor.' ~ 'source') }}", "{{ states(state_attr('sensor.source', 'link')) }}",
    "{{ states['sensor'][state_attr('sensor.source', 'link')] }}",
    "{% set lookup = states %}{{ lookup('sensor.source') }}",
    "{% set x = 'sensor.source' %}{{ states(x) }}",
    "{{ (states)('sensor.' + 'source') }}",
    "{{ states | map(attribute='state') | list }}",
    "{{ 'sensor.source' | expand | list }}", "{{ expand('group.source') }}",
    "{{ area_entities('area') }}", "{{ device_attr('device', 'name') }}",
    "{{ integration_entities('entry') }}", "{{ label_entities('label') }}",
    "{{ 'sensor.source' | state_translated }}",
    "{{ states | attr('sensor') }}", "{{ states.sensor.source._hass }}",
    "{{ states.sensor.source.state.upper() }}",
    "{% for state in states %}{{ state.state }}{% endfor %}",
    "{% macro lookup(x) %}{{ states(x) }}{% endmacro %}{{ lookup('sensor.source') }}",
    "{% include 'sensor.source' %}", "{{ this.state }}", "{{ config }}",
    "{{ states.get('sensor.source') }}",
    "{{ states(*['sensor.source']) }}", "{{ states(**{'entity_id': 'sensor.source'}) }}",
    "{{ 'sensor.source' | states(*[]) }}", "{{ unknown('sensor.source') }}",
    "{{ states('sensor.source') ",
]


@pytest.mark.parametrize("expression", UNRESOLVED)
def test_unresolved_queries_are_not_treated_as_dependency_free(expression):
    with pytest.raises(HelperScopeError):
        template_entities(expression)


@pytest.mark.parametrize("expression", [
    "{{ '" + "x" * HELPER_TEMPLATE_MAX_CHARS + "' }}",
    "{{ " + "+".join(["1"] * 600) + " }}",
    "{{ " + "[" * 80 + "1" + "]" * 80 + " }}",
    "{{ " + "9" * 5000 + " }}",
    "{{ states.sensor.source" + ".state" * 50 + " }}",
])
def test_analysis_budgets_refuse_cleanly(expression):
    with pytest.raises(HelperScopeError):
        template_entities(expression)


@pytest.mark.parametrize("permission", ["RED", "YELLOW"])
@pytest.mark.parametrize("expression", [
    "{{ states('sensor.source') }}", "{{ states.sensor.source.state }}",
    "{{ state_attr('sensor.source', 'fixture') }}", "{{ 'sensor.source' | states }}",
])
async def test_hidden_or_read_only_source_refused_before_approval(hass, permission, expression):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("sensor.source", "314159", {"secret": "fixture"})
    access_key = agent(cap_helper_write="confirm")
    access_key.permissions.entities["sensor.source"] = PermissionNode(state=permission)
    data = runtime()
    data.approvals = MagicMock(async_create=AsyncMock())
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps(expression)}, access_key, hass, data)
    assert outcome == "denied", result
    assert "outside this access key's write scope" in result["content"][0]["text"]
    data.approvals.async_create.assert_not_awaited()
    assert not hass.config_entries.async_entries("template")
    assert not hass.config_entries.flow.async_progress_by_handler("template")


@pytest.mark.parametrize("expression", UNRESOLVED[:14])
async def test_unresolved_template_refused_through_creation(hass, expression):
    assert await async_setup_component(hass, "homeassistant", {})
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps(expression)}, agent(), hass, runtime())
    assert outcome == "denied", result
    assert "dependencies cannot be safely established" in result["content"][0]["text"]
    assert not hass.config_entries.async_entries("template")


async def create_entry(hass, state="{{ 7 }}"):
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init("template", context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "sensor"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Existing helper", "state": state})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


async def test_literal_authorized_source_reaches_real_creation(hass):
    hass.states.async_set("sensor.source", "314159")
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps("{{ states('sensor.source') }}")}, agent(), hass, runtime())
    assert outcome == "allowed", result
    await hass.async_block_till_done()
    body = json.loads(result["content"][0]["text"])
    assert hass.states.get(body["entity_ids"][0]).state == "314159"


@pytest.mark.parametrize("settings", [
    {"state": "{{ states('sensor.source') }}"},
    {_TEMPLATE_OPTIONS_SECTION: {"availability": "{{ has_value('sensor.source') }}"}},
    {"state": "{{ states | list }}"},
])
async def test_edit_and_executor_refuse_before_mutation(hass, settings):
    entry = await create_entry(hass)
    access_key = agent()
    access_key.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    original = dict(entry.options)
    data = runtime()
    for operation in (
        lambda: _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": settings}, access_key, hass, data),
        lambda: helper._execute_set_config_entry_options({"entry_id": entry.entry_id, "settings": settings}, access_key, hass, data),
    ):
        result, outcome, _ = await operation()
        assert outcome == "denied", result
        assert dict(entry.options) == original
        assert data.config_recovery.records() == []


async def test_partial_edit_checks_retained_reference_and_allows_removing_it(hass):
    hass.states.async_set("sensor.source", "7")
    entry = await create_entry(hass, "{{ states('sensor.source') }}")
    access_key = agent()
    access_key.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    data = runtime()
    result, outcome, _ = await _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": {_TEMPLATE_OPTIONS_SECTION: {"availability": "{{ true }}"}}}, access_key, hass, data)
    assert outcome == "denied", result
    assert data.config_recovery.records() == []
    result, outcome, _ = await _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": {"state": "{{ 9 }}"}}, access_key, hass, data)
    assert outcome == "allowed", result
    assert entry.options["state"] == "{{ 9 }}"


async def test_a_real_template_body_with_a_hidden_source_is_withheld(hass):
    """Through HA's own form: the stored body and the form's suggested value both go."""
    hass.states.async_set("sensor.source", "7")
    entry = await create_entry(hass, "{{ states('sensor.source') }}")
    access_key = agent()
    access_key.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    result, outcome, _ = await _call_tool("get_helper_settings", {"entry_id": entry.entry_id}, access_key, hass, runtime())
    assert outcome == "allowed", result
    assert "sensor.source" not in result["content"][0]["text"]
    body = json.loads(result["content"][0]["text"])
    assert body["settings"]["state"] == "<redacted>"
    assert body["editable_settings"]["state"] == "<redacted>"


async def test_a_real_template_body_in_scope_stays_readable(hass):
    hass.states.async_set("sensor.source", "7")
    entry = await create_entry(hass, "{{ states('sensor.source') }}")
    result, outcome, _ = await _call_tool("get_helper_settings", {"entry_id": entry.entry_id}, agent(), hass, runtime())
    assert outcome == "allowed", result
    assert json.loads(result["content"][0]["text"])["settings"]["state"] == "{{ states('sensor.source') }}"


async def test_settings_revalidate_source_after_form_wait(hass):
    hass.states.async_set("sensor.source", "7")
    entry = await create_entry(hass)
    access_key = agent()
    original = helper._init_settings_flow
    calls = 0

    async def revoke_after_wait(*args):
        nonlocal calls
        form = await original(*args)
        calls += 1
        if calls == 2:  # executor's actual submission form, after safe preflight
            access_key.permissions.entities["sensor.source"] = PermissionNode(state="RED")
        return form

    data = runtime()
    with patch.object(helper, "_init_settings_flow", side_effect=revoke_after_wait):
        result, outcome, _ = await helper._execute_set_config_entry_options(
            {"entry_id": entry.entry_id, "settings": {"state": "{{ states('sensor.source') }}"}}, access_key, hass, data)
    assert result.get("isError"), (result, outcome)
    assert outcome == "denied"
    assert data.config_recovery.records()[0]["phase"] == "not_applied"
    assert calls == 2
    assert entry.options["state"] == "{{ 7 }}"
    assert not hass.config_entries.options.async_progress()


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize("expression", ["{{ states('sensor.source') }}", "{{ states | list }}"])
async def test_restore_receipt_and_executor_enforce_expression_scope(hass, deleted, expression):
    entry = await create_entry(hass)
    snapshot = template_snapshot(hass, entry)
    snapshot["options"]["state"] = expression
    access_key = agent()
    access_key.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    data = runtime()
    rid = await data.config_recovery.begin(f"config_entry:{entry.entry_id}", "test", access_key.id, snapshot)
    await data.config_recovery.mark(rid, "verified")
    data.config_recovery.leave(rid)
    if deleted:
        await hass.config_entries.async_remove(entry.entry_id)
    result, outcome, _ = await _call_tool("restore_configuration", {"recovery_id": rid, "side": "before"}, access_key, hass, data)
    assert outcome == "denied", result
    result, outcome, _ = await restore_template(snapshot, access_key, hass, data)
    assert outcome == "denied", result
    assert len(data.config_recovery.records()) == 1
    if deleted:
        assert not hass.config_entries.async_entries("template")
    else:
        assert entry.options["state"] == "{{ 7 }}"


@pytest.mark.parametrize("action", [
    [{"action": "light.turn_on", "target": {"entity_id": "all"}}],
    [{"action": "homeassistant.restart"}],
    [{"action": "light.turn_on", "target": {"device_id": "opaque-device"}}],
    [{"if": "{{ true }}", "then": [{"action": "homeassistant.restart"}]}],
])
async def test_nonempty_actions_have_no_unchecked_later_authority(hass, action):
    assert await async_setup_component(hass, "homeassistant", {})
    values = [{"step_id": "user", "data": {"next_step_id": "button"}},
              {"step_id": "button", "data": {"name": "Action helper", "press": action}}]
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": values}, agent(), hass, runtime())
    assert outcome == "denied", result
    assert "Nonempty Template actions" in result["content"][0]["text"]
    assert not hass.config_entries.async_entries("template")


async def test_capability_denial_precedes_expression_analysis(hass):
    with patch.object(helper, "template_entities", side_effect=AssertionError("must not analyze")):
        result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps("{{ states }}")}, agent(cap_helper_write="deny"), hass, runtime())
    assert outcome == "denied"
    assert "dependencies" not in result["content"][0]["text"]


async def test_pass_through_does_not_authorize_unknown_dependencies(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    access_key = agent()
    access_key.pass_through = True
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps("{{ states | list }}")}, access_key, hass, runtime())
    assert outcome == "denied", result
    assert not hass.config_entries.async_entries("template")


async def test_template_device_association_refuses_before_approval(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps("{{ 7 }}", device_id="opaque")}, agent(), hass, runtime())
    assert outcome == "denied", result
    assert "device associations" in result["content"][0]["text"]


async def test_retained_actions_and_restore_actions_refuse(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    flow = hass.config_entries.flow
    result = await flow.async_init("template", context={"source": SOURCE_USER})
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "button"})
    result = await flow.async_configure(result["flow_id"], {"name": "Native action helper", "press": [{"stop": "inert"}]})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    access_key = agent()
    data = runtime()
    result, outcome, _ = await _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": {_TEMPLATE_OPTIONS_SECTION: {"availability": "{{ true }}"}}}, access_key, hass, data)
    assert outcome == "denied", result
    snapshot = template_snapshot(hass, entry)
    result, outcome, _ = await restore_template(snapshot, access_key, hass, data)
    assert outcome == "denied", result
    await hass.config_entries.async_remove(entry.entry_id)
    result, outcome, _ = await restore_template(snapshot, access_key, hass, data)
    assert outcome == "denied", result
    assert not hass.config_entries.async_entries("template")
    assert data.config_recovery.records() == []


@pytest.mark.parametrize("expression", [
    "{{ state_attr('sensor.source', 'access_token') }}",
    "{{ state_attr(entity_id='sensor.source', name='api_key') }}",
    "{{ is_state_attr('sensor.source', 'password', 'guess') }}",
    "{{ 'sensor.source' | state_attr('api_key') }}",
    "{{ 'sensor.source' is is_state_attr('password', 'guess') }}",
    "{{ state_attr('sensor.source', 'access_' ~ 'token') }}",
    "{{ states.sensor.source }}", "{{ states['sensor.source'] | to_json }}",
    "{{ states.sensor.source.attributes }}",
    "{{ states.sensor.source.attributes['access_token'] }}",
    "{{ states['sensor']['source']['attributes']['PASSWORD'] }}",
])
async def test_authorized_source_cannot_launder_scrubbed_attributes(hass, expression):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("sensor.source", "7", {"access_token": "safe-fixture-secret", "password": "fixture"})
    # The fixture source is writable; refusal must come from attribute policy.
    with pytest.raises(HelperScopeError):
        template_entities(expression)
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps(expression)}, agent(), hass, runtime())
    assert outcome == "denied", result
    assert "attributes" in result["content"][0]["text"]
    assert not hass.config_entries.async_entries("template")


@pytest.mark.parametrize("expression,entities", [
    ("{{ iif(is_state('sensor.source', '7'), 'on', 'off') }}", {"sensor.source"}),
    ("{{ is_state('sensor.source', '7') | iif('yes', 'no') }}", {"sensor.source"}),
    ("{{ is_number(states('sensor.source')) }}", {"sensor.source"}),
    ("{{ states('sensor.source') | is_number }}", {"sensor.source"}),
    ("{{ states('sensor.source') is is_number }}", {"sensor.source"}),
    ("{{ states('sensor.text').strip() }}", {"sensor.text"}),
    ("{{ states('sensor.text').rstrip('.') }}", {"sensor.text"}),
    ("{{ states('sensor.text').split(',')[0] }}", {"sensor.text"}),
    ("{{ state_attr('sensor.source', 'label').lstrip().removeprefix('x').removesuffix('.') }}", {"sensor.source"}),
    ("{{ 'a.b'.startswith('a') and 'a.b'.endswith('b') }}", set()),
    ("{{ 'Ab'.lower() ~ 'ab'.upper() ~ 'ab'.replace('a', 'c') }}", set()),
    ("{{ now().strftime('%H').strip() }}", set()),
    ("{{ now().replace(hour=0).hour }}", set()),
    ("{{ states('sensor.text') | regex_replace(',', ';') }}", {"sensor.text"}),
    ("{{ states('sensor.text') | regex_match('^a') or states('sensor.text') | regex_search('b') }}", {"sensor.text"}),
    ("{{ states('sensor.text') | regex_findall('[a-z]') | length }}", {"sensor.text"}),
    ("{{ states('sensor.text') | regex_findall_index('[a-z]', 0) }}", {"sensor.text"}),
    ("{{ states('sensor.text') is match('a') and states('sensor.text') is search('b') }}", {"sensor.text"}),
    ("{{ '%.1f' | format(states('sensor.source') | float(0)) }}", {"sensor.source"}),
    ("{% set x = states('sensor.source') | float(0) %}{{ x * 2 }}", {"sensor.source"}),
    ("{% set a, b = states('sensor.source'), states('sensor.other') %}{{ a ~ b }}", {"sensor.source", "sensor.other"}),
    ("{% set label %}{{ states('sensor.source') }}{% endset %}{{ label | trim }}", {"sensor.source"}),
    ("{% for part in states('sensor.text').split(',') if part %}{{ part.strip() }}{% else %}none{% endfor %}",
     {"sensor.text"}),
    ("{% for n in [1, 2] %}{% set y = n * states('sensor.other') | int(0) %}{{ y }}{% endfor %}", {"sensor.other"}),
])
async def test_newly_allowed_forms_keep_their_exact_dependencies(hass, expression, entities):
    """Each newly allowed form renders in installed HA and reports exactly the entities it reads."""
    hass.states.async_set("sensor.source", "7", {"label": " x7."})
    hass.states.async_set("sensor.other", "2")
    hass.states.async_set("sensor.text", "a, b.")
    assert template_entities(expression) == entities
    Template(expression, hass).async_render()


@pytest.mark.parametrize("expression", [
    "{{ states.sensor.source.state.strip() }}",
    "{{ states['sensor.source'].state.split(',') }}",
    "{{ states.sensor.source.attributes.label.rstrip('.') }}",
    "{{ states.strip() }}",
    "{{ states('sensor.source').format(1) }}",
    "{{ states('sensor.source').__class__ }}",
    "{{ 'x'.title() }}",
    "{% set x = states %}{{ x.sensor.source.state }}",
    "{% set x = states.sensor.source %}{{ x }}",
    "{% set x = 'sensor.source' %}{{ x | states }}",
    "{% set x = 'sensor.source' %}{{ is_state(x, '7') }}",
    "{% set x = 'source' %}{{ states.sensor[x].state }}",
    "{% set states = 'x' %}{{ states }}",
    "{% set float = 1 %}{{ float }}",
    "{% set _x = 1 %}{{ _x }}",
    "{% set ns = namespace(x=1) %}{{ ns.x }}",
    "{% set ns.x = 1 %}",
    "{{ y }}{% set y = 1 %}",
    "{% for x in [1] %}{{ loop.index }}{% endfor %}",
    "{% for s in states.sensor %}{{ s }}{% endfor %}",
    "{% for s in states %}{{ s }}{% endfor %}",
    "{% for e in state_attr('group.all', 'entity_id') %}{{ states(e) }}{% endfor %}",
    "{% for a, b in [[1, 2]] %}{{ a.b }}{% endfor %}",
    "{{ 'sensor.source' | regex_replace('x', 'y') | states }}",
])
def test_names_methods_and_loops_never_reach_unchecked_state(expression):
    with pytest.raises(HelperScopeError):
        template_entities(expression)


@pytest.mark.parametrize("expression,construct", [
    ("{{ unknown('x') }}", "the function 'unknown'"),
    ("{{ 'x' | slugify }}", "the filter 'slugify'"),
    ("{{ 1 is sameas 1 }}", "the test 'sameas'"),
    ("{{ 'x'.title() }}", "the method 'title'"),
    ("{{ states.sensor.source.state.upper() }}", "the method 'upper' on a states.* path"),
    ("{% macro m() %}{% endmacro %}", "the Macro construct"),
    ("{{ lookup }}", "the name 'lookup'"),
    ("{% set states = 1 %}", "the reserved name 'states'"),
    ("{{ states('sensor.' ~ 'x') }}", "'states' without a literal entity ID"),
    ("{{ states[x] }}", "a computed states[...] lookup"),
    ("{{ states.sensor }}", "the broad lookup states.sensor"),
    ("{{ states('sensor.x') ", "a template that does not parse"),
])
def test_a_refusal_names_the_construct_and_lists_what_is_allowed(expression, construct):
    with pytest.raises(HelperScopeError) as caught:
        template_entities(expression)
    message = str(caught.value)
    assert f"{construct}" in message and "dependencies cannot be safely established" in message
    assert "Functions: " in message and "Filters: " in message and "iif" in message and "rstrip" in message


def test_the_listed_sets_are_the_enforced_ones():
    """The refusal lists the allow-lists from the constants the checker enforces, so neither can drift."""
    from custom_components.phoenix_mcp import const
    from custom_components.phoenix_mcp.template_scope import scope_refusal
    message = scope_refusal("x")
    listed = {}
    for label, values in (("Allowed state functions", const.HELPER_TEMPLATE_STATE_FUNCTIONS),
                          ("Functions", const.HELPER_TEMPLATE_PURE_FUNCTIONS),
                          ("Filters", const.HELPER_TEMPLATE_PURE_FILTERS),
                          ("Tests", const.HELPER_TEMPLATE_PURE_TESTS),
                          ("String methods, not on a states.* path", const.HELPER_TEMPLATE_STRING_METHODS),
                          ("Date and time methods", const.HELPER_TEMPLATE_TIME_METHODS)):
        section = message.split(f"{label}: ", 1)[1].split(". ", 1)[0]
        listed[label] = set(section.split(", "))
        assert listed[label] == set(values), label
    # Every name each set allows really passes the checker.
    for name in const.HELPER_TEMPLATE_PURE_FUNCTIONS - const.HELPER_TEMPLATE_TIME_FUNCTIONS:
        assert template_entities(f"{{{{ {name}(1) }}}}") == set()
    for name in const.HELPER_TEMPLATE_PURE_FILTERS:
        assert template_entities(f"{{{{ 1 | {name} }}}}") == set()
    for name in const.HELPER_TEMPLATE_PURE_TESTS:
        assert template_entities(f"{{{{ 1 is {name} }}}}") == set()
    for name in const.HELPER_TEMPLATE_STRING_METHODS:
        assert template_entities(f"{{{{ 'a'.{name}() }}}}") == set()


async def test_every_allowed_name_exists_in_installed_ha(hass):
    """Each allowed function, filter and test is one HA's template environment provides, so none passes and then fails."""
    from homeassistant.helpers.template import TemplateEnvironment
    from custom_components.phoenix_mcp import const
    env = TemplateEnvironment(hass)
    assert const.HELPER_TEMPLATE_PURE_FUNCTIONS <= set(env.globals)
    assert const.HELPER_TEMPLATE_PURE_FILTERS <= set(env.filters)
    assert const.HELPER_TEMPLATE_PURE_TESTS <= set(env.tests)


@pytest.mark.parametrize("expression", ["{{ round(1.5) }}", "{{ abs(-1) }}", "{{ str(1) }}"])
async def test_a_function_ha_lacks_is_refused_and_its_filter_is_not(hass, expression):
    name = expression.split("(")[0].removeprefix("{{ ")
    with pytest.raises(HelperScopeError, match=f"the function '{name}'"):
        template_entities(expression)
    with pytest.raises(Exception):
        Template(expression, hass).async_render()
    if name != "str":
        assert template_entities(f"{{{{ -1.5 | {name} }}}}") == set()


@pytest.mark.parametrize("expression", [
    "{% set x = states('sensor.source') %}{{ x }}",
    "{{ iif(true, states('sensor.source'), 'off') }}",
    "{% for part in states('sensor.source').split(',') %}{{ part }}{% endfor %}",
    "{{ states('sensor.source').rstrip('.') }}",
])
async def test_newly_allowed_forms_still_require_write_on_their_source(hass, expression):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("sensor.source", "314159")
    access_key = agent(cap_helper_write="confirm")
    access_key.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    data = runtime()
    data.approvals = MagicMock(async_create=AsyncMock())
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps(expression)},
                                          access_key, hass, data)
    assert outcome == "denied" and "outside this access key's write scope" in result["content"][0]["text"]
    data.approvals.async_create.assert_not_awaited()
    assert not hass.config_entries.async_entries("template")


async def test_a_refused_template_names_the_construct_through_creation(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    result, outcome, _ = await _call_tool(
        "create_helper", {"helper_type": "template", "flow_steps": steps("{{ states('sensor.source') | slugify }}")},
        agent(), hass, runtime())
    text = result["content"][0]["text"]
    assert outcome == "denied" and "the filter 'slugify'" in text and "regex_replace" in text
    assert not hass.config_entries.async_entries("template")
