"""Warnings for unregistered action services and targets their service, trigger or condition skips.

Built on the installed integrations' own service, trigger and condition descriptions, so a
finding means the running Core would really skip the entity.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import authoring_validation
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.authoring_validation import async_target_findings
from custom_components.phoenix_mcp.const import AUTHORING_TARGET_CHECK_MAX, REFERENCE_WARNING_MAX
from custom_components.phoenix_mcp.tools import discovery

MODE = "input_select.house_mode"
GUEST = "input_boolean.guest"
HIDDEN = "input_boolean.hidden"
WRONG = {"action": "select.select_option", "target": {"entity_id": MODE}, "data": {"option": "Away"}}


@pytest.fixture
async def home(hass: HomeAssistant) -> HomeAssistant:
    """Real input_select and input_boolean entities, and the select and switch integrations."""
    assert await async_setup_component(hass, "input_select", {"input_select": {
        "house_mode": {"options": ["Home", "Away"]}}})
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"guest": {}, "hidden": {}}})
    assert await async_setup_component(hass, "select", {})
    assert await async_setup_component(hass, "switch", {})
    assert await async_setup_component(hass, "script", {})
    await hass.async_block_till_done()
    return hass


def _readable(entity_id: str) -> bool:
    return entity_id != HIDDEN


def _automation(*actions, triggers=(), conditions=()) -> dict:
    return {"triggers": list(triggers) or [{"trigger": "event", "event_type": "probe"}],
            "conditions": list(conditions), "actions": list(actions)}


async def test_wrong_kind_target_names_the_service_that_applies(home):
    [finding] = await async_target_findings(home, _automation(WRONG), _readable)
    assert finding.startswith('config.actions[0].action "select.select_option" does not apply to input_select.house_mode')
    assert 'include ["input_select.select_option"' in finding
    assert await async_target_findings(home, _automation({**WRONG, "action": "input_select.select_option"}), _readable) == []


async def test_legacy_service_key_and_entity_id_in_data_or_step(home):
    for step in ({"service": "select.select_option", "data": {"entity_id": MODE, "option": "Away"}},
                 {"action": "select.select_option", "entity_id": [f"{MODE}, {GUEST}"]}):
        findings = await async_target_findings(home, _automation(step), _readable)
        assert any(MODE in text for text in findings), step
    two = await async_target_findings(home, _automation({"action": "select.select_option", "entity_id": f"{MODE},{GUEST}"}), _readable)
    assert len(two) == 2


async def test_missing_service_is_named(home):
    [finding] = await async_target_findings(home, _automation({"action": "nosuch.service_typo"}), _readable)
    assert finding == 'config.actions[0].action "nosuch.service_typo" is not a service this Home Assistant has registered, so the step fails when it runs.'


async def test_named_script_calls_are_not_an_existence_oracle(home):
    config = _automation({"action": "script.does_not_exist"}, {"action": "script.turn_on", "target": {"entity_id": "script.nope"}})
    assert await async_target_findings(home, config, _readable) == []


async def test_templated_steps_and_targets_are_skipped(home):
    config = _automation(
        {"action": "{{ 'nosuch.service' }}"},
        {"action": "select.select_option", "target": {"entity_id": "{{ states('sensor.x') }}"}},
        {"action": "select.select_option", "target": "{{ target }}"},
        {"action": "select.select_option", "entity_id": ["{% if true %}input_select.house_mode{% endif %}"]},
    )
    assert await async_target_findings(home, config, _readable) == []


async def test_data_that_only_looks_like_an_action_is_not_checked(home):
    config = _automation(
        {"event": "probe", "event_data": {"action": "nosuch.service", "target": {"entity_id": MODE}}},
        {"action": "input_select.select_option", "target": {"entity_id": MODE},
         "data": {"option": "Away", "action": "nosuch.other"}},
        triggers=[{"trigger": "event", "event_type": "probe", "event_data": {"action": "nosuch.service"}}],
    )
    assert await async_target_findings(home, config, _readable) == []


async def test_nested_if_repeat_parallel_and_choose_are_walked(home):
    config = _automation(
        {"if": [{"condition": "state", "entity_id": GUEST, "state": "on"}], "then": [WRONG], "else": [WRONG]},
        {"repeat": {"count": 2, "sequence": [WRONG]}},
        {"parallel": [WRONG, {"sequence": [WRONG]}]},
        {"choose": [{"conditions": [], "sequence": [WRONG]}], "default": [WRONG]},
    )
    paths = [text.split(" ", 1)[0] for text in await async_target_findings(home, config, _readable)]
    assert paths == [
        "config.actions[0].then[0].action", "config.actions[0].else[0].action",
        "config.actions[1].repeat.sequence[0].action",
        "config.actions[2].parallel[0].action", "config.actions[2].parallel[1].sequence[0].action",
        "config.actions[3].default[0].action", "config.actions[3].choose[0].sequence[0].action",
    ]


async def test_script_sequence_is_walked(home):
    [finding] = await async_target_findings(home, {"sequence": [{"delay": 1}, WRONG]}, _readable)
    assert finding.startswith('config.sequence[1].action "select.select_option"')


async def test_trigger_and_condition_targets(home):
    good_trigger = {"trigger": "switch.turned_on", "target": {"entity_id": GUEST}}
    good_condition = {"condition": "switch.is_on", "target": {"entity_id": GUEST}}
    assert await async_target_findings(home, _automation(triggers=[good_trigger], conditions=[good_condition]), _readable) == []
    findings = await async_target_findings(home, _automation(
        triggers=[{**good_trigger, "target": {"entity_id": MODE}}],
        conditions=[{"condition": "and", "conditions": [{**good_condition, "target": {"entity_id": [MODE]}}]}]), _readable)
    assert [text.split(" ", 2)[:2] for text in findings] == [
        ["config.triggers[0].trigger", '"switch.turned_on"'],
        ["config.conditions[0].conditions[0].condition", '"switch.is_on"'],
    ]
    assert "Triggers that apply to it" in findings[0] and "Conditions that apply to it" in findings[1]


async def test_unreadable_unregistered_and_disabled_targets_are_not_named(home):
    home.states.async_set("input_select.state_only", "Home")
    config = _automation(
        {"action": "select.select_option", "target": {"entity_id": [HIDDEN, "input_select.state_only", "input_select.missing"]}},
        {**WRONG, "enabled": False},
    )
    assert await async_target_findings(home, config, _readable) == []


async def test_failed_lookup_says_references_were_not_checked(home):
    with patch.object(authoring_validation, "async_get_all_service_descriptions", side_effect=RuntimeError("secret")):
        findings = await async_target_findings(home, _automation(WRONG), _readable)
    assert findings == [authoring_validation._NOT_CHECKED]
    assert await async_target_findings(home, "not a config", _readable) == []


async def test_findings_and_lookups_are_capped(home):
    many = await async_target_findings(home, _automation(*[{"action": f"nosuch.typo_{i}"} for i in range(REFERENCE_WARNING_MAX + 5)]), _readable)
    assert len(many) == REFERENCE_WARNING_MAX + 1 and many[-1] == "5 more service or target findings not listed."
    looked = await async_target_findings(home, _automation(*[WRONG] * (AUTHORING_TARGET_CHECK_MAX + 1)), _readable)
    assert len(looked) == REFERENCE_WARNING_MAX + 1


def _key(tree: PermissionTree) -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=tree, cap_diagnostics="allow")


@pytest.mark.parametrize(("cfg_type", "config"), [
    ("automation", _automation({"action": "nosuch.service_typo"}, WRONG)),
    ("script", {"sequence": [{"action": "nosuch.service_typo"}, WRONG]}),
])
async def test_validate_tool_reports_the_two_live_cases(home, cfg_type, config):
    key = _key(PermissionTree(domains={"input_select": PermissionNode(state="GREEN")}))
    result, outcome, _ = await discovery._tool_validate_automation_or_script({"type": cfg_type, "config": config}, key, home)
    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed" and body["valid"] is True
    assert len(body["warnings"]) == 2
    assert "nosuch.service_typo" in body["warnings"][0] and MODE in body["warnings"][1]


async def test_validate_tool_does_not_name_an_out_of_scope_target(home):
    key = _key(PermissionTree(domains={"input_boolean": PermissionNode(state="GREEN")}))
    result, _, _ = await discovery._tool_validate_automation_or_script({"type": "automation", "config": _automation(WRONG)}, key, home)
    body = json.loads(result["content"][0]["text"])
    assert "warnings" not in body and body["referenced_entities"] == [{"entity_id": MODE, "exists": False, "accessible": False}]
