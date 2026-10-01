"""Runtime action discovery and policy checks against real HA handlers."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree
from custom_components.phoenix_mcp.version_store import VersionStore


def _body(result):
    return json.loads(result["content"][0]["text"])


def _args(domain, service, entity_id, **data):
    return {"service": {"domain": domain, "name": service, "data": data},
            "targets": [{"kind": "entity", "ids": [entity_id]}]}


@pytest.fixture
async def runtime(hass, token_store):
    token, _ = await token_store.async_create_token("runtime-actions", "admin")
    token.cap_search = "allow"
    token.cap_service_response = "allow"
    token.confirm_inline_wait_seconds = 0
    token.permissions = PermissionTree(domains={
        domain: PermissionNode(state="GREEN") for domain in ("script", "automation", "scene", "input_boolean")
    }, entities={"script.hidden": PermissionNode(state="RED")})
    token_store.get_settings().mesa_mode = "off"
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"runtime_marker": {}}})
    assert await async_setup_component(hass, "script", {"script": {
        "visible": {"sequence": [{"event": "runtime_script", "event_data": {"value": "{{ value | default('unset') }}"}}]},
        "hidden": {"sequence": [{"event": "runtime_hidden"}]},
        "waiting": {"sequence": [{"event": "runtime_started"}, {"wait_for_trigger": [{"platform": "event", "event_type": "runtime_release"}]}]},
    }})
    assert await async_setup_component(hass, "automation", {"automation": [{
        "id": "runtime_automation", "alias": "Runtime automation", "trigger": [],
        "condition": [{"condition": "template", "value_template": "{{ false }}"}],
        "action": [{"event": "runtime_automation"}],
    }]})
    assert await async_setup_component(hass, "scene", {"scene": [{
        "id": "runtime_scene", "name": "Runtime scene", "entities": {"input_boolean.runtime_marker": "on"},
    }]})
    await hass.async_block_till_done()
    return token, data


@pytest.mark.parametrize("pass_through", [False, True])
async def test_named_script_cannot_substitute_an_unrelated_target(hass, runtime, pass_through):
    token, data = runtime
    token.pass_through = pass_through
    seen = []
    hass.bus.async_listen("runtime_hidden", lambda event: seen.append(event))
    args = _args("script", "hidden", "script.visible")
    result, outcome, _ = await _call_tool("call_service", args, token, hass, data)
    assert outcome == "denied" and result["isError"]
    result, outcome, _ = await _call_tool("dry_run_service", args, token, hass, data)
    assert outcome == "allowed" and _body(result)["predicted_outcome"] == "denied"
    assert _body(result)["resolved_entities"] == []
    await hass.async_block_till_done()
    assert seen == []


async def test_named_script_matches_storage_id_after_entity_rename(hass, runtime):
    token, data = runtime
    registry = er.async_get(hass)
    registry.async_update_entity("script.visible", new_entity_id="script.renamed")
    await hass.async_block_till_done()
    seen = []
    hass.bus.async_listen("runtime_script", lambda event: seen.append(event.data["value"]))
    args = _args("script", "visible", "script.renamed", value="bound")
    result, outcome, _ = await _call_tool("dry_run_service", args, token, hass, data)
    assert outcome == "allowed" and _body(result)["predicted_outcome"] == "allowed"
    result, outcome, _ = await _call_tool("call_service", args, token, hass, data)
    assert outcome == "allowed" and not result.get("isError"), result
    await hass.async_block_till_done()
    assert seen == ["bound"]
    result, _, _ = await _call_tool("find_available_actions", {"entity_id": "script.renamed"}, token, hass, data)
    actions = {row["service"]: row for row in _body(result)["actions"]}
    assert "script.visible" in actions and "script.hidden" not in actions
    assert "script.waiting" not in actions
    assert "Stop all running" in actions["script.turn_off"]["description"]
    assert actions["script.reload"]["available"] is False


@pytest.mark.parametrize("target", ["script.hidden", "script.visible"])
async def test_unknown_script_service_is_opaque(hass, runtime, target):
    token, data = runtime
    result, outcome, _ = await _call_tool("call_service", _args("script", "missing", target), token, hass, data)
    assert outcome == "denied" and result["content"][0]["text"] == "Forbidden."


async def test_script_start_and_stop_are_runtime_controls(hass, runtime):
    token, data = runtime
    started = asyncio.Event()
    hass.bus.async_listen("runtime_started", lambda event: started.set())
    result, outcome, _ = await _call_tool("call_service", _args("script", "turn_on", "script.waiting"), token, hass, data)
    assert outcome == "allowed" and not result.get("isError"), result
    await asyncio.wait_for(started.wait(), 1)
    assert hass.states.get("script.waiting").state == "on"
    result, outcome, _ = await _call_tool("call_service", _args("script", "turn_off", "script.waiting"), token, hass, data)
    assert outcome == "allowed" and not result.get("isError"), result
    assert hass.states.get("script.waiting").state == "off"
    assert er.async_get(hass).async_get("script.waiting").disabled_by is None


async def test_automation_enable_disable_and_trigger_are_distinct(hass, runtime):
    token, data = runtime
    seen = []
    hass.bus.async_listen("runtime_automation", lambda event: seen.append(event))
    entity_id = "automation.runtime_automation"
    for service, state in (("turn_off", "off"), ("turn_on", "on")):
        result, outcome, _ = await _call_tool("call_service", _args("automation", service, entity_id), token, hass, data)
        assert outcome == "allowed" and not result.get("isError"), result
        assert hass.states.get(entity_id).state == state
    assert seen == []
    result, outcome, _ = await _call_tool("call_service", _args("automation", "trigger", entity_id, skip_condition=False), token, hass, data)
    assert outcome == "allowed" and not result.get("isError"), result
    await hass.async_block_till_done()
    assert seen == []
    result, outcome, _ = await _call_tool("call_service", _args("automation", "trigger", entity_id), token, hass, data)
    assert outcome == "allowed" and not result.get("isError"), result
    await hass.async_block_till_done()
    assert len(seen) == 1


async def test_scene_activation_and_read_only_refusal(hass, runtime):
    token, data = runtime
    args = _args("scene", "turn_on", "scene.runtime_scene")
    token.permissions.entities["scene.runtime_scene"] = PermissionNode(state="YELLOW")
    result, outcome, _ = await _call_tool("call_service", args, token, hass, data)
    assert outcome == "denied" and result["isError"]
    assert hass.states.get("input_boolean.runtime_marker").state == "off"
    token.permissions.entities.pop("scene.runtime_scene")
    result, outcome, _ = await _call_tool("call_service", args, token, hass, data)
    assert outcome == "allowed" and not result.get("isError"), result
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.runtime_marker").state == "on"


def _profile(data, entity_id, mode):
    data.mesa.store.set(entity_id, SemanticProfile.from_dict(
        entity_id, {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}},
        default_origin=MetadataOrigin.USER,
    ))


@pytest.mark.parametrize("domain,service,entity_id", [
    ("script", "visible", "script.visible"),
    ("script", "turn_on", "script.visible"),
    ("automation", "trigger", "automation.runtime_automation"),
    ("scene", "turn_on", "scene.runtime_scene"),
])
@pytest.mark.parametrize("mode", ["read_only", "prohibited", "confirm"])
async def test_runtime_actions_preserve_mesa_and_approval_gates(hass, runtime, domain, service, entity_id, mode):
    token, data = runtime
    data.store.get_settings().mesa_mode = "enforced"
    data.mesa = await async_setup_mesa(hass, "enforced")
    _profile(data, entity_id, mode)
    args = _args(domain, service, entity_id)
    result, outcome, _ = await _call_tool("dry_run_service", args, token, hass, data)
    expected = "pending_approval" if mode == "confirm" else "denied"
    assert outcome == "allowed" and _body(result)["predicted_outcome"] == expected
    result, outcome, _ = await _call_tool("call_service", args, token, hass, data)
    assert outcome == expected, result
    if mode == "confirm":
        approval = data.store.get_pending_approvals()[-1]
        _profile(data, entity_id, "prohibited")
        result, outcome, _ = await async_execute_approved_tool(approval["tool_name"], approval["args"], token, hass, data)
        assert outcome == "denied" and result["isError"]
        _profile(data, entity_id, "confirm")
        result, outcome, _ = await async_execute_approved_tool(approval["tool_name"], approval["args"], token, hass, data)
        assert outcome == "allowed" and not result.get("isError"), result
        token.permissions.entities[entity_id] = PermissionNode(state="RED")
        result, outcome, _ = await async_execute_approved_tool(approval["tool_name"], approval["args"], token, hass, data)
        assert outcome == "denied" and result["isError"]


async def test_named_script_rechecks_binding_after_mesa_wait(hass, runtime, monkeypatch):
    from custom_components.phoenix_mcp import mcp_view

    token, data = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    apply_mesa = mcp_view.async_apply_mesa_to_call

    async def paused_mesa(*args, **kwargs):
        entered.set()
        await release.wait()
        return await apply_mesa(*args, **kwargs)

    monkeypatch.setattr(mcp_view, "async_apply_mesa_to_call", paused_mesa)
    task = asyncio.create_task(_call_tool("call_service", _args("script", "visible", "script.visible"), token, hass, data))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        er.async_get(hass).async_update_entity("script.visible", new_unique_id="changed_storage_id")
        release.set()
        result, outcome, _ = await task
        assert outcome == "denied" and result["isError"]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_named_script_approval_rechecks_registry_binding(hass, runtime):
    token, data = runtime
    data.store.get_settings().mesa_mode = "enforced"
    data.mesa = await async_setup_mesa(hass, "enforced")
    _profile(data, "script.visible", "confirm")
    seen = []
    hass.bus.async_listen("runtime_script", lambda event: seen.append(event))
    args = _args("script", "visible", "script.visible")
    _, outcome, _ = await _call_tool("call_service", args, token, hass, data)
    assert outcome == "pending_approval"
    approval = data.store.get_pending_approvals()[-1]
    er.async_get(hass).async_update_entity("script.visible", new_unique_id="changed_storage_id")
    result, outcome, _ = await async_execute_approved_tool(approval["tool_name"], approval["args"], token, hass, data)
    assert outcome == "denied" and result["isError"]
    await hass.async_block_till_done()
    assert seen == []


@pytest.mark.parametrize("domain", ["automation", "script", "scene"])
@pytest.mark.parametrize("operation", ["create", "edit"])
async def test_authoring_waits_for_reload_before_runtime_use(hass, runtime, tmp_path, monkeypatch, domain, operation):
    token, data = runtime
    setattr(token, f"cap_{domain}_write", "allow")
    data.versions = VersionStore()
    configs = {
        "automation": {"id": "stored", "alias": "Stored", "trigger": [], "action": [{"event": "runtime_authored"}]},
        "script": {"alias": "Stored", "sequence": [{"event": "runtime_authored"}]},
        "scene": {"id": "stored", "name": "Stored", "entities": {"input_boolean.runtime_marker": "on"}},
    }
    import yaml

    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\nscript: !include scripts.yaml\nscene: !include scenes.yaml\n")
    for family, filename in (("automation", "automations.yaml"), ("script", "scripts.yaml"), ("scene", "scenes.yaml")):
        stored = ({"stored": configs[family]} if family == "script" else [configs[family]]) if operation == "edit" and family == domain else ({} if family == "script" else [])
        (tmp_path / filename).write_text(yaml.safe_dump(stored))
    entered, release = asyncio.Event(), asyncio.Event()
    real_call = type(hass.services).async_call

    async def delayed_reload(self, service_domain, service_name, *args, **kwargs):
        if (service_domain, service_name) == (domain, "reload"):
            entered.set()
            await release.wait()
        return await real_call(self, service_domain, service_name, *args, **kwargs)

    monkeypatch.setattr(type(hass.services), "async_call", delayed_reload)
    config = {key: value for key, value in configs[domain].items() if key != "id"}
    args = {"config": config}
    if domain in ("automation", "script") or operation == "edit":
        args[f"{domain}_id"] = "stored"
    task = asyncio.create_task(_call_tool(f"{operation}_{domain}", args, token, hass, data))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert not task.done()
        release.set()
        result, outcome, _ = await asyncio.wait_for(task, 2)
        assert outcome == "allowed" and not result.get("isError"), result
        await hass.async_block_till_done()
        entry_id = "stored" if domain != "scene" else _body(result)["scene_id"]
        entity_id = er.async_get(hass).async_get_entity_id(domain, "homeassistant" if domain == "scene" else domain, entry_id)
        assert entity_id is not None and hass.states.get(entity_id) is not None
        if domain != "automation":
            assert _body(result)["reload_status"] == "completed"
        result, outcome, _ = await _call_tool("call_service", _args(domain, "trigger" if domain == "automation" else "turn_on", entity_id), token, hass, data)
        assert outcome == "allowed" and not result.get("isError"), result
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
