"""A run's trace detail tells a key no more than its scope and capabilities allow.

Step results name no entity, so a step that evaluated a hidden entity loses its values; the
stored configuration and a webhook-triggered run's variables need the authoring capability.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import PERSONA_READ_ONLY
from custom_components.phoenix_mcp.personas import PERSONA_DEFINITIONS
from custom_components.phoenix_mcp.tool_dispatch import _call_tool

LOADED_COMPONENTS = ("recorder", "system_health")
WEBHOOK_ID = "trace-scope-hook-7f3a9c"


def _key(domains: dict[str, str], **caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={domain: PermissionNode(state=state) for domain, state in domains.items()}),
        **{"cap_traces": "allow", **caps})


async def _run_detail(hass: HomeAssistant, key: AccessKeyRecord, automation: str) -> tuple[str, dict]:
    listed, _outcome, _ = await _call_tool("get_automation_traces", {"automation_id": automation}, key, hass, MagicMock())
    run_id = json.loads(listed["content"][0]["text"])["traces"][0]["run_id"]
    content, outcome, _ = await _call_tool(
        "get_automation_traces", {"automation_id": automation, "run_id": run_id}, key, hass, MagicMock())
    assert outcome == "allowed"
    text = content["content"][0]["text"]
    body = json.loads(text)
    return text, body.get("extended_dict", body)


async def test_steps_that_evaluated_a_hidden_entity_keep_no_values(hass):
    hass.states.async_set("lock.smart_lock", "locked", {"friendly_name": "Front door"})
    hass.states.async_set("sensor.hidden_temp", "23.7", {})
    hass.states.async_set("alarm_control_panel.home", "disarmed", {})
    hass.states.async_set("binary_sensor.porch", "off", {})
    hass.states.async_set("binary_sensor.window", "off", {})
    assert await async_setup_component(hass, "automation", {"automation": [{
        "id": "door_check", "alias": "Door check",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.porch"}],
        "conditions": [
            {"condition": "state", "entity_id": "binary_sensor.window", "state": "off"},
            {"condition": "or", "conditions": [
                {"condition": "state", "entity_id": "lock.smart_lock", "state": "unlocked"},
                {"condition": "template", "value_template": "{{ states.sensor.hidden_temp.state | float > 30 }}"},
            ]},
        ],
        "actions": [],
    }, {
        "id": "vars", "alias": "Vars",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.porch"}],
        "actions": [{"variables": {"alarm": "{{ states('alarm_control_panel.home') }}"}},
                    {"variables": {"porch": "{{ states('binary_sensor.porch') }}"}}],
    }]})
    await hass.async_block_till_done()
    key = _key({"automation": "GREEN", "binary_sensor": "GREEN"})
    hass.states.async_set("binary_sensor.porch", "on", {})
    await hass.async_block_till_done()

    door_text, door = await _run_detail(hass, key, "automation.door_check")
    for leaked in ('"locked"', "23.7", "lock.smart_lock", "hidden_temp"):
        assert leaked not in door_text
    assert door["trace"]["condition/1"][0]["result"] == "<redacted>"
    # A step over readable entities keeps its result, so the redaction is per step.
    assert door["trace"]["condition/0"][0]["result"]["result"] is True

    vars_text, variables = await _run_detail(hass, key, "automation.vars")
    assert "disarmed" not in vars_text
    assert variables["trace"]["action/0"][0]["changed_variables"] == "<redacted>"
    assert variables["trace"]["action/1"][0]["changed_variables"]["porch"] == "on"


async def test_configuration_and_webhook_request_need_the_authoring_capability(hass, hass_client_no_auth):
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "webhook", {})
    assert await async_setup_component(hass, "automation", {"automation": [{
        "id": "hook_auto", "alias": "Hook auto", "description": "private description",
        "triggers": [{"trigger": "webhook", "webhook_id": WEBHOOK_ID, "allowed_methods": ["POST"]}],
        "actions": [],
    }]})
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    assert (await client.post(f"/api/webhook/{WEBHOOK_ID}?q=query-marker", json={"note": "body-marker"})).status == 200
    await hass.async_block_till_done()

    caps = dict(PERSONA_DEFINITIONS[PERSONA_READ_ONLY])
    assert caps["cap_traces"] == "allow" and caps["cap_automation_write"] == "deny"
    reader = _key({"automation": "YELLOW"}, **caps)
    text, detail = await _run_detail(hass, reader, "automation.hook_auto")
    for leaked in (WEBHOOK_ID, "private description", "body-marker", "query-marker"):
        assert leaked not in text
    assert "config" not in detail and "blueprint_inputs" not in detail
    assert detail["withheld"]

    author = _key({"automation": "YELLOW"}, **{**caps, "cap_automation_write": "confirm"})
    text, detail = await _run_detail(hass, author, "automation.hook_auto")
    assert detail["config"]["description"] == "private description"
    assert "body-marker" in text
    # A webhook ID is a credential to Home Assistant itself, so even an author reads it elsewhere.
    assert WEBHOOK_ID not in text


async def test_variables_computed_from_a_webhook_request_need_the_authoring_capability(hass, hass_client_no_auth):
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "webhook", {})
    assert await async_setup_component(hass, "automation", {"automation": [{
        "id": "hook_vars", "alias": "Hook vars",
        "triggers": [{"trigger": "webhook", "webhook_id": WEBHOOK_ID, "allowed_methods": ["POST"]}],
        "variables": {"top_note": "{{ trigger.json.note | upper }}"},
        "actions": [{"variables": {"step_note": "{{ trigger.json.note ~ '-step' }}"}}],
    }]})
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    assert (await client.post(f"/api/webhook/{WEBHOOK_ID}", json={"note": "derived-marker"})).status == 200
    await hass.async_block_till_done()

    caps = dict(PERSONA_DEFINITIONS[PERSONA_READ_ONLY])
    reader = _key({"automation": "YELLOW"}, **caps)
    text, detail = await _run_detail(hass, reader, "automation.hook_vars")
    for leaked in ("derived-marker", "DERIVED-MARKER", "derived-marker-step"):
        assert leaked not in text
    steps = [step for steps in detail["trace"].values() for step in steps]
    assert steps and all("changed_variables" not in step for step in steps)
    assert detail["withheld"]

    author = _key({"automation": "YELLOW"}, **{**caps, "cap_automation_write": "confirm"})
    text, detail = await _run_detail(hass, author, "automation.hook_vars")
    assert "DERIVED-MARKER" in text and "derived-marker-step" in text
