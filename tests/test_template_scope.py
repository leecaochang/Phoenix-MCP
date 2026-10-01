"""Static dependency and real HA helper ingress/executor scope regressions."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.template import Template
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.const import HELPER_TEMPLATE_MAX_CHARS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.template_recovery import restore_template, template_snapshot
from custom_components.phoenix_mcp.template_scope import HelperScopeError, template_entities
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.phoenix_mcp.tools import helper


def agent(**caps):
    return TokenRecord(
        id=uuid.uuid4().hex, name="scope-test", token_hash="x", created_at=utcnow(), created_by="test",
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN"),
                                           "button": PermissionNode(state="GREEN")}),
        cap_helper_write=caps.get("cap_helper_write", "allow"),
    )


def runtime():
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())


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
    token = agent(cap_helper_write="confirm")
    token.permissions.entities["sensor.source"] = PermissionNode(state=permission)
    data = runtime()
    data.approvals = MagicMock(async_create=AsyncMock())
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps(expression)}, token, hass, data)
    assert outcome == "denied", result
    assert "outside this token's write scope" in result["content"][0]["text"]
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
    {"additional_options": {"availability": "{{ has_value('sensor.source') }}"}},
    {"state": "{{ states | list }}"},
])
async def test_edit_and_executor_refuse_before_mutation(hass, settings):
    entry = await create_entry(hass)
    token = agent()
    token.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    original = dict(entry.options)
    data = runtime()
    for operation in (
        lambda: _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": settings}, token, hass, data),
        lambda: helper._execute_set_config_entry_options({"entry_id": entry.entry_id, "settings": settings}, token, hass, data),
    ):
        result, outcome, _ = await operation()
        assert outcome == "denied", result
        assert dict(entry.options) == original
        assert data.config_recovery.records() == []


async def test_partial_edit_checks_retained_reference_and_allows_removing_it(hass):
    hass.states.async_set("sensor.source", "7")
    entry = await create_entry(hass, "{{ states('sensor.source') }}")
    token = agent()
    token.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    data = runtime()
    result, outcome, _ = await _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": {"additional_options": {"availability": "{{ true }}"}}}, token, hass, data)
    assert outcome == "denied", result
    assert data.config_recovery.records() == []
    result, outcome, _ = await _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": {"state": "{{ 9 }}"}}, token, hass, data)
    assert outcome == "allowed", result
    assert entry.options["state"] == "{{ 9 }}"


async def test_settings_revalidate_source_after_form_wait(hass):
    hass.states.async_set("sensor.source", "7")
    entry = await create_entry(hass)
    token = agent()
    original = helper._init_settings_flow
    calls = 0

    async def revoke_after_wait(*args):
        nonlocal calls
        form = await original(*args)
        calls += 1
        if calls == 2:  # executor's actual submission form, after safe preflight
            token.permissions.entities["sensor.source"] = PermissionNode(state="RED")
        return form

    data = runtime()
    with patch.object(helper, "_init_settings_flow", side_effect=revoke_after_wait):
        result, outcome, _ = await helper._execute_set_config_entry_options(
            {"entry_id": entry.entry_id, "settings": {"state": "{{ states('sensor.source') }}"}}, token, hass, data)
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
    token = agent()
    token.permissions.entities["sensor.source"] = PermissionNode(state="RED")
    data = runtime()
    rid = await data.config_recovery.begin(f"config_entry:{entry.entry_id}", "test", token.id, snapshot)
    await data.config_recovery.mark(rid, "verified")
    data.config_recovery.leave(rid)
    if deleted:
        await hass.config_entries.async_remove(entry.entry_id)
    result, outcome, _ = await _call_tool("restore_configuration", {"recovery_id": rid, "side": "before"}, token, hass, data)
    assert outcome == "denied", result
    result, outcome, _ = await restore_template(snapshot, token, hass, data)
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
    token = agent()
    token.pass_through = True
    result, outcome, _ = await _call_tool("create_helper", {"helper_type": "template", "flow_steps": steps("{{ states | list }}")}, token, hass, runtime())
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
    token = agent()
    data = runtime()
    result, outcome, _ = await _call_tool("set_helper_settings", {"entry_id": entry.entry_id, "settings": {"additional_options": {"availability": "{{ true }}"}}}, token, hass, data)
    assert outcome == "denied", result
    snapshot = template_snapshot(hass, entry)
    result, outcome, _ = await restore_template(snapshot, token, hass, data)
    assert outcome == "denied", result
    await hass.config_entries.async_remove(entry.entry_id)
    result, outcome, _ = await restore_template(snapshot, token, hass, data)
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
