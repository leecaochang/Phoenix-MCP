"""Independent disclosure, lifecycle and resource-budget regression boundaries."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from hassil.models import MatchEntity, UnmatchedTextEntity
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import panel
from custom_components.phoenix_mcp.decision_shadow import DecisionShadow
from custom_components.phoenix_mcp.esphome_builder import _OutputCollector, _TRUNCATION_MARKER
from custom_components.phoenix_mcp.mcp_view import _build_context_json, _build_context_plain
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord
from custom_components.phoenix_mcp.tools import discovery


def access_key(**changes):
    return AccessKeyRecord(id="audit-access-key", name="audit-access-key", access_key_hash="hash", created_at=utcnow(), created_by="u",
                       permissions=PermissionTree(entities={"light.visible": PermissionNode(state="GREEN")}), **changes)


def body(result):
    return json.loads(result[0]["content"][0]["text"])


async def test_config_validation_never_echoes_values_or_paths(hass):
    secret = "arbitrary-inline-value-with-no-secret-shaped-key"
    error = SimpleNamespace(message=f"bad value '{secret}' at /private/config/hidden.yaml light.hidden", domain=secret)
    result = SimpleNamespace(errors=[error] * 80, warnings=[error])
    with patch("homeassistant.helpers.check_config.async_check_ha_config_file", AsyncMock(return_value=result)):
        response = body(await discovery._tool_check_config({}, access_key(cap_diagnostics="allow"), hass))
    assert response["valid"] is False and response["truncated"] is True
    assert len(response["errors"]) == 50
    assert secret not in json.dumps(response) and "hidden" not in json.dumps(response)


@pytest.mark.parametrize("selector", ["name", "area", "floor"])
async def test_hidden_recognition_and_missing_recognition_have_identical_bodies(hass, selector):
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("light.visible", "on")
    recognized = SimpleNamespace(intent=SimpleNamespace(name="HassTurnOn"),
                                 entities={selector: MatchEntity(selector, "hidden", "hidden")},
                                 unmatched_entities={})
    missed = SimpleNamespace(intent=SimpleNamespace(name="HassTurnOn"), entities={},
                             unmatched_entities={selector: UnmatchedTextEntity(selector, "hidden")})
    with patch("custom_components.phoenix_mcp.intent_recognition.async_recognize", AsyncMock(side_effect=[recognized, missed])):
        args = {"sentence": "turn on hidden"}
        first = body(await discovery._tool_recognize_intent(args, access_key(cap_search="allow"), hass))
        second = body(await discovery._tool_recognize_intent(args, access_key(cap_search="allow"), hass))
    assert first == second == {"match": False, "near_misses": []}


async def test_reverse_consumers_require_their_own_identity_scope(hass):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("automation", "test", "owned-id", suggested_object_id="secret")
    hass.states.async_set(entry.entity_id, "on")
    holder = {"kind": "automation", "id": "owned-id", "name": "Secret"}
    caller = access_key(cap_search="allow")
    assert not discovery._consumer_visible(holder, caller, hass)
    caller.permissions.entities[entry.entity_id] = PermissionNode(state="YELLOW")
    assert discovery._consumer_visible(holder, caller, hass)
    registry.async_remove(entry.entity_id)
    hass.states.async_remove(entry.entity_id)
    assert not discovery._consumer_visible(holder, caller, hass)


async def test_hidden_reverse_scan_drops_identity_and_global_dangling(hass):
    hass.states.async_set("light.visible", "on")
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("automation", "test", "hidden-auto", suggested_object_id="hidden")
    hass.states.async_set(entry.entity_id, "on")
    with open(hass.config.path("automations.yaml"), "w") as file:
        file.write("- id: hidden-auto\n  alias: hidden-name\n  triggers:\n  - trigger: state\n    entity_id: light.visible\n  actions:\n  - action: lock.lock\n    target:\n      entity_id: lock.gone\n")
    caller = access_key(cap_search="allow")
    response = body(await discovery._tool_get_relationships({"scope": {"kind": "entity", "id": "light.visible"}}, caller, hass))
    assert response["consumers"] == [] and response["dangling_references"] == []
    assert "hidden-name" not in json.dumps(response) and "lock.gone" not in json.dumps(response)
    caller.permissions.entities[entry.entity_id] = PermissionNode(state="YELLOW")
    response = body(await discovery._tool_get_relationships({"scope": {"kind": "entity", "id": "light.visible"}}, caller, hass))
    assert response["consumer_count"] == 1
    assert response["dangling_references"][0]["entity_id"] == "lock.gone"


async def test_home_companion_legacy_context_is_restricted(hass):
    caller = access_key(persona="home_companion")
    hass.states.async_set("light.visible", "on")
    assert _build_context_json(caller, hass)["entities"] == []
    assert "light.visible" not in _build_context_plain(caller, hass)
    from custom_components.phoenix_mcp.home_companion import wrapper_tool
    tool_name = wrapper_tool()["name"]
    assert tool_name in _build_context_plain(caller, hass)
    assert tool_name in _build_context_json(caller, hass)["message"]


async def test_url_sweeps_use_real_ha_url_manager(hass):
    urls = [panel._INJECT_JS_URL + "?v=old", panel._AGENTCHAT_JS_URL + "?v=old", "/other.js"]
    manager = UrlManager(lambda *args: None, urls)
    hass.data[DATA_EXTRA_MODULE_URL] = manager
    panel._remove_all_inject_urls(hass)
    panel._remove_all_agentchat_urls(hass)
    assert manager.urls == frozenset({"/other.js"})


async def test_decision_shutdown_awaits_cancellation_cleanup(hass):
    finished = asyncio.Event()
    async def work():
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            finished.set()
    shadow = DecisionShadow()
    shadow.task = asyncio.create_task(work())
    await asyncio.sleep(0)
    await shadow.async_cancel()
    assert finished.is_set() and shadow.task.done()


@pytest.mark.parametrize("ratio", [0, 0.75, 1])
def test_output_collector_bounds_retention_during_streaming(ratio):
    collector = _OutputCollector(64, ratio)
    chunks = ["a" * 10000, "b" * 10000, "end"]
    for chunk in chunks:
        collector.add(chunk)
        assert len(collector._head) + len(collector._tail) <= 128
    value, truncated = collector.finish()
    complete = "".join(chunk + "\n" for chunk in chunks)
    head = int(64 * ratio)
    tail = 64 - head
    assert truncated
    assert value == complete[:head] + _TRUNCATION_MARKER + (complete[-tail:] if tail else "")


async def test_nonselector_ai_task_schema_uses_active_converter():
    import voluptuous as vol
    from custom_components.phoenix_mcp.ai_task import _structure_to_json
    schema = _structure_to_json(vol.Schema({vol.Required("count"): int}))
    assert schema["properties"]["count"]["type"] == "integer"
    assert "count" in schema["required"]


async def test_rate_limit_event_bus_is_throttled_independently_of_notifications(hass, access_key_store):
    from custom_components.phoenix_mcp.helpers import fire_rate_limit_events
    caller, _ = await access_key_store.async_create_access_key("events", "admin")
    other, _ = await access_key_store.async_create_access_key("other-events", "admin")
    access_key_store.get_settings().notify_on_rate_limit = False
    events = []
    hass.bus.async_listen("phoenix_mcp_rate_limited", events.append)
    data = SimpleNamespace(store=access_key_store, rate_limit_notified={})
    with patch("custom_components.phoenix_mcp.helpers.time.monotonic", return_value=0):
        for _ in range(100):
            fire_rate_limit_events(hass, data, caller)
    await hass.async_block_till_done()
    assert len(events) == 1
    with patch("custom_components.phoenix_mcp.helpers.time.monotonic", return_value=0):
        fire_rate_limit_events(hass, data, other)
        fire_rate_limit_events(hass, data, other)
    await hass.async_block_till_done()
    assert [event.data["access_key_id"] for event in events] == [caller.id, other.id]
    with patch("custom_components.phoenix_mcp.helpers.time.monotonic", return_value=61):
        fire_rate_limit_events(hass, data, caller)
    await hass.async_block_till_done()
    assert [event.data["access_key_id"] for event in events] == [caller.id, other.id, caller.id]


def test_internal_failure_records_location_without_exception_values(caplog):
    import logging
    from custom_components.phoenix_mcp.helpers import log_internal_failure
    from custom_components.phoenix_mcp.request_authority import dispatch_request_id
    context = dispatch_request_id.set("request-example")
    try:
        try:
            raise RuntimeError("private-provider-response")
        except RuntimeError as exc:
            log_internal_failure(logging.getLogger("phoenix-regression"), "bounded-phase", exc)
    finally:
        dispatch_request_id.reset(context)
    assert "request-example" in caplog.text and "RuntimeError" in caplog.text
    assert "private-provider-response" not in caplog.text


@pytest.mark.parametrize("domain", ["entity_registry", "device_registry", "area_registry", "helper"])
@pytest.mark.parametrize("failed", [False, True])
async def test_failed_mesa_registry_runtime_refuses_identity_mutation(hass, access_key_store, domain, failed):
    from custom_components.phoenix_mcp.data import PhoenixData
    from custom_components.phoenix_mcp.mesa import evaluate_registry_action
    from custom_components.phoenix_mcp.rate_limiter import RateLimiter
    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.tools.helper import _helper_mesa_context
    caller, _ = await access_key_store.async_create_access_key("mesa-failure", "admin")
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog(), mesa_setup_failed=failed)
    access_key_store.get_settings().mesa_mode = "advisory"
    decision = evaluate_registry_action(data, caller, "light.visible", action="update", registry_domain=domain)
    assert decision.decision == "deny" and decision.rule == "mesa:runtime_unavailable"
    if domain == "helper":
        result = _helper_mesa_context(data, caller, ["light.visible"], action="update", session_id="fixture")
        assert result.decisions[0][1].decision == "deny"
        assert result.decisions[0][1].rule == "mesa:runtime_unavailable"
    access_key_store.get_settings().mesa_mode = "off"
    assert evaluate_registry_action(data, caller, "light.visible", action="update", registry_domain=domain).decision == "allow"


@pytest.mark.parametrize("mode", ["deny", "confirm", "allow"])
def test_dashboard_consumer_requires_its_own_capability(hass, mode):
    caller = access_key()
    caller.cap_lovelace_write = mode
    assert discovery._consumer_visible({"kind": "dashboard", "id": "private-dashboard"}, caller, hass) is (mode != "deny")


async def test_calendar_review_refuses_active_writer_without_waiting_for_its_lock(hass):
    from custom_components.phoenix_mcp.tools.calendar import CalendarWriteJournal

    journal = CalendarWriteJournal(hass)
    journal.active.add("in-flight")
    async with journal.lock:
        async with asyncio.timeout(0.2):
            assert await journal.reviewed("in-flight") is False


@pytest.mark.parametrize("calendar", [False, True])
async def test_repair_confirmation_uses_the_owning_recovery_journal(hass, calendar):
    from homeassistant.data_entry_flow import FlowResultType
    from custom_components.phoenix_mcp.const import DOMAIN
    from custom_components.phoenix_mcp.repairs import AgentRecoveryFlow

    journals = SimpleNamespace(calendar_writes=SimpleNamespace(reviewed=AsyncMock(return_value=True)),
                               agent_recovery=SimpleNamespace(reviewed=AsyncMock(return_value=True)))
    hass.data[DOMAIN] = journals
    flow = AgentRecoveryFlow()
    flow.hass = hass
    flow.issue_id = "recovery-fixture"
    flow.data = {"calendar_receipt_id": "receipt"} if calendar else {"request_id": "request"}
    refused = await flow.async_step_confirm({"checked_home_assistant": False})
    assert refused["errors"] == {"base": "check_required"}
    journals.calendar_writes.reviewed.assert_not_awaited()
    journals.agent_recovery.reviewed.assert_not_awaited()
    result = await flow.async_step_confirm({"checked_home_assistant": True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    owner = journals.calendar_writes if calendar else journals.agent_recovery
    other = journals.agent_recovery if calendar else journals.calendar_writes
    owner.reviewed.assert_awaited_once_with("receipt" if calendar else "request")
    other.reviewed.assert_not_awaited()


@pytest.mark.parametrize("tool", ["describe", "whatif"])
async def test_analysis_omits_hidden_consumer_identity_but_reaches_visible_matches(hass, tool):
    hass.states.async_set("light.visible", "on")
    entry = er.async_get(hass).async_get_or_create("automation", "test", "hidden-auto", suggested_object_id="hidden")
    hass.states.async_set(entry.entity_id, "on")
    with open(hass.config.path("automations.yaml"), "w") as file:
        file.write("- id: hidden-auto\n  alias: hidden-name\n  triggers:\n  - trigger: state\n    entity_id: light.visible\n    to: 'off'\n  actions: []\n")
    caller = access_key(cap_search="allow")
    data = SimpleNamespace(store=SimpleNamespace(get_settings=lambda: SimpleNamespace(mesa_mode="off"), get_entity_hints=dict), mesa=None)

    async def invoke():
        if tool == "describe":
            return body(await discovery._tool_describe_entity({"entity_id": "light.visible"}, caller, hass, data))["referenced_by"]
        return body(await discovery._tool_whatif({"entity_id": "light.visible", "hypothetical_state": "off"}, caller, hass))["candidates"]

    assert await invoke() == []
    caller.permissions.entities[entry.entity_id] = PermissionNode(state="YELLOW")
    matches = await invoke()
    assert len(matches) == 1
    assert matches[0].get("id", matches[0].get("automation_id")) == "hidden-auto"
    assert matches[0]["name"] == "hidden-name"


async def test_config_check_locates_errors_only_for_a_key_that_can_open_the_file(hass):
    hass.config.components.add("sensor")
    text = ("Invalid config for 'sensor' at packages/climate.yaml, line 14: required key 'platform' not provided, "
            "please check the docs at https://example.invalid")
    extra = "Invalid config for 'sensor' at configuration.yaml, line 3: 'p4ssw0rd-opt' is an invalid option for 'sensor'"
    hidden = "Invalid config for 'sensor' at ../outside.yaml, line 2: expected int for 'port', got 'secret-value'"
    result = SimpleNamespace(errors=[SimpleNamespace(message=m, domain="sensor") for m in (text, extra, hidden)], warnings=[])
    with patch("homeassistant.helpers.check_config.async_check_ha_config_file", AsyncMock(return_value=result)):
        editor = body(await discovery._tool_check_config({}, access_key(cap_diagnostics="allow", cap_yaml_edit="allow"), hass))
        reader = body(await discovery._tool_check_config({}, access_key(cap_diagnostics="allow"), hass))
    first, second, third = editor["errors"]
    assert (first["file"], first["line"], first["reason"]) == ("packages/climate.yaml", 14, "required key 'platform' is missing")
    assert second["reason"] == "contains an option this integration does not support" and "p4ssw0rd" not in json.dumps(editor)
    assert "file" not in third and "secret-value" not in json.dumps(editor)
    assert all("file" not in row for row in reader["errors"])
