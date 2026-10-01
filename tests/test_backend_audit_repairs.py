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
from homeassistant.util.file import WriteError

from custom_components.phoenix_mcp import panel
from custom_components.phoenix_mcp.decision_shadow import DecisionShadow
from custom_components.phoenix_mcp.esphome_builder import _OutputCollector, _TRUNCATION_MARKER
from custom_components.phoenix_mcp.mcp_view import _build_context_json, _build_context_plain
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.phoenix_mcp.tools import discovery
from custom_components.phoenix_mcp.tools.config_files import _recoverable_file_write


def token(**changes):
    return TokenRecord(id="audit-token", name="audit-token", token_hash="hash", created_at=utcnow(), created_by="u",
                       permissions=PermissionTree(entities={"light.visible": PermissionNode(state="GREEN")}), **changes)


def body(result):
    return json.loads(result[0]["content"][0]["text"])


async def test_config_validation_never_echoes_values_or_paths(hass):
    secret = "arbitrary-inline-value-with-no-secret-shaped-key"
    error = SimpleNamespace(message=f"bad value '{secret}' at /private/config/hidden.yaml light.hidden", domain=secret)
    result = SimpleNamespace(errors=[error] * 80, warnings=[error])
    with patch("homeassistant.helpers.check_config.async_check_ha_config_file", AsyncMock(return_value=result)):
        response = body(await discovery._tool_check_config({}, token(cap_diagnostics="allow"), hass))
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
        first = body(await discovery._tool_recognize_intent(args, token(cap_search="allow"), hass))
        second = body(await discovery._tool_recognize_intent(args, token(cap_search="allow"), hass))
    assert first == second == {"match": False, "near_misses": []}


async def test_reverse_consumers_require_their_own_identity_scope(hass):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("automation", "test", "owned-id", suggested_object_id="secret")
    hass.states.async_set(entry.entity_id, "on")
    holder = {"kind": "automation", "id": "owned-id", "name": "Secret"}
    caller = token(cap_search="allow")
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
    caller = token(cap_search="allow")
    response = body(await discovery._tool_get_relationships({"scope": {"kind": "entity", "id": "light.visible"}}, caller, hass))
    assert response["consumers"] == [] and response["dangling_references"] == []
    assert "hidden-name" not in json.dumps(response) and "lock.gone" not in json.dumps(response)
    caller.permissions.entities[entry.entity_id] = PermissionNode(state="YELLOW")
    response = body(await discovery._tool_get_relationships({"scope": {"kind": "entity", "id": "light.visible"}}, caller, hass))
    assert response["consumer_count"] == 1
    assert response["dangling_references"][0]["entity_id"] == "lock.gone"


async def test_home_companion_legacy_context_is_restricted(hass):
    caller = token(persona="home_companion")
    hass.states.async_set("light.visible", "on")
    assert _build_context_json(caller, hass)["entities"] == []
    assert "light.visible" not in _build_context_plain(caller, hass)


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


@pytest.mark.parametrize("changed", [False, True])
async def test_atomic_write_failure_only_releases_barrier_after_unchanged_readback(hass, changed):
    from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery
    path = hass.config.path("file.txt")
    with open(path, "w") as file:
        file.write("before")
    recovery = ConfigurationRecovery()
    data = SimpleNamespace(config_recovery=recovery)
    def fail(target, content):
        if changed:
            with open(target, "w") as file:
                file.write(content)
        raise WriteError("failure")
    with patch("custom_components.phoenix_mcp.tools.config_files._write_text_atomic", fail):
        response = await _recoverable_file_write(path=path, rel="file.txt", before_exists=True, before_content="before",
            content="after", token=token(), data=data, operation="write_file", display_path="file.txt", hass=hass)
    assert response[1] == "invalid_request"
    assert recovery.records()[0]["phase"] == ("unknown" if changed else "not_applied")


async def test_nonselector_ai_task_schema_uses_active_converter():
    import voluptuous as vol
    from custom_components.phoenix_mcp.ai_task import _structure_to_json
    schema = _structure_to_json(vol.Schema({vol.Required("count"): int}))
    assert schema["properties"]["count"]["type"] == "integer"
    assert "count" in schema["required"]


async def test_rate_limit_event_bus_is_throttled_independently_of_notifications(hass, token_store):
    from custom_components.phoenix_mcp.helpers import fire_rate_limit_events
    caller, _ = await token_store.async_create_token("events", "admin")
    token_store.get_settings().notify_on_rate_limit = False
    events = []
    hass.bus.async_listen("phoenix_mcp_rate_limited", events.append)
    data = SimpleNamespace(store=token_store, rate_limit_notified={})
    with patch("custom_components.phoenix_mcp.helpers.time.monotonic", return_value=0):
        for _ in range(100):
            fire_rate_limit_events(hass, data, caller)
    await hass.async_block_till_done()
    assert len(events) == 1
    with patch("custom_components.phoenix_mcp.helpers.time.monotonic", return_value=61):
        fire_rate_limit_events(hass, data, caller)
    await hass.async_block_till_done()
    assert len(events) == 2


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
async def test_failed_mesa_registry_runtime_refuses_identity_mutation(hass, token_store, domain):
    from custom_components.phoenix_mcp.data import PhoenixData
    from custom_components.phoenix_mcp.mesa import evaluate_registry_action
    from custom_components.phoenix_mcp.rate_limiter import RateLimiter
    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.tools.helper import _helper_mesa_context
    caller, _ = await token_store.async_create_token("mesa-failure", "admin")
    data = PhoenixData(store=token_store, rate_limiter=RateLimiter(), audit=AuditLog(), mesa_setup_failed=True)
    token_store.get_settings().mesa_mode = "advisory"
    decision = evaluate_registry_action(data, caller, "light.visible", action="update", registry_domain=domain)
    assert decision.decision == "deny" and decision.rule == "mesa:runtime_unavailable"
    if domain == "helper":
        result = _helper_mesa_context(data, caller, ["light.visible"], action="update", session_id="fixture")
        assert result.decisions[0][1].decision == "deny"
        assert result.decisions[0][1].rule == "mesa:runtime_unavailable"
    token_store.get_settings().mesa_mode = "off"
    assert evaluate_registry_action(data, caller, "light.visible", action="update", registry_domain=domain).decision == "allow"
