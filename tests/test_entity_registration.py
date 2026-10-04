"""Registration deadlines, scoped identity resolution, and committed-write results."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import entity_registration as observation
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool, async_restore_version
from custom_components.phoenix_mcp.request_authority import (
    AuthorityChangedError, RequestAuthority, policy_version, request_authority,
)
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord
from custom_components.phoenix_mcp.version_store import VersionStore


def _access_key():
    return AccessKeyRecord(
        id="registration", name="registration", access_key_hash="test", created_at=utcnow(),
        created_by="admin", cap_script_write="allow", cap_scene_write="allow",
        cap_registry_read="allow", cap_registry_write="allow",
        permissions=PermissionTree(domains={
            name: PermissionNode(state="GREEN") for name in ("script", "scene", "light")
        }),
    )


def _body(result):
    return json.loads(result["content"][0]["text"])


def _entry(hass, domain="script", storage_id="stored"):
    return er.async_get(hass).async_get_or_create(
        domain, "homeassistant" if domain == "scene" else "script", storage_id,
        suggested_object_id="renamed",
    )


@pytest.fixture
def short_budget(monkeypatch):
    monkeypatch.setattr(observation, "AUTHORING_REGISTRATION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(observation, "AUTHORING_REGISTRATION_POLL_SECONDS", 0.001)


@pytest.mark.parametrize("domain", ["scene", "script"])
async def test_waits_for_registry_then_state(hass, short_budget, domain):
    access_key = _access_key()
    task = asyncio.create_task(observation.async_observe_registration(hass, access_key, domain, "stored"))
    try:
        await asyncio.sleep(0)
        assert not task.done()
        entry = _entry(hass, domain)
        await asyncio.sleep(0.005)
        assert not task.done(), "Registry presence alone must not finish the wait"
        hass.states.async_set(entry.entity_id, "unavailable")
        result = await task
        assert result == {"status": "observed", "entity_id": entry.entity_id, "state_present": True}
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_immediate_observation_does_not_sleep(hass):
    entry = _entry(hass)
    hass.states.async_set(entry.entity_id, "off")
    with patch.object(observation.asyncio, "sleep", new=AsyncMock(side_effect=AssertionError("unexpected sleep"))):
        result = await observation.async_observe_registration(hass, _access_key(), "script", "stored")
    assert result["entity_id"] == entry.entity_id


@pytest.mark.parametrize("registered", [False, True])
async def test_timeout_preserves_only_observed_identity(hass, short_budget, registered):
    entry = _entry(hass) if registered else None
    result = await asyncio.wait_for(observation.async_observe_registration(hass, _access_key(), "script", "stored"), 1)
    assert result == {
        "status": "timed_out", "entity_id": entry.entity_id if entry else None,
        "state_present": False if entry else None,
    }


async def test_disabled_entry_completes_without_wait(hass):
    entry = _entry(hass)
    er.async_get(hass).async_update_entity(entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    with patch.object(observation.asyncio, "sleep", new=AsyncMock(side_effect=AssertionError("unexpected sleep"))):
        result = await observation.async_observe_registration(hass, _access_key(), "script", "stored")
    assert result == {"status": "disabled", "entity_id": entry.entity_id, "state_present": False}


@pytest.mark.parametrize("visibility", ["denied", "registry_only", "assist", "wrong_platform"])
async def test_hidden_and_missing_have_identical_results(hass, immediate_authoring_observation, visibility):
    access_key = _access_key()
    missing = await observation.async_observe_registration(hass, access_key, "script", "stored")
    entry = er.async_get(hass).async_get_or_create(
        "script", "other" if visibility == "wrong_platform" else "script", "stored", suggested_object_id="secret",
    )
    if visibility != "registry_only":
        hass.states.async_set(entry.entity_id, "off")
    if visibility == "denied":
        access_key.permissions.entities[entry.entity_id] = PermissionNode(state="RED")
    elif visibility == "registry_only":
        access_key.permissions = PermissionTree(entities={entry.entity_id: PermissionNode(state="GREEN")})
    elif visibility == "assist":
        access_key.pass_through = True
        access_key.use_assist_exposure = True
    with patch.object(observation, "assist_expose_check", return_value=(lambda _: False) if visibility == "assist" else None):
        hidden = await observation.async_observe_registration(hass, access_key, "script", "stored")
    assert hidden == missing


async def test_cancellation_propagates(hass):
    task = asyncio.create_task(observation.async_observe_registration(hass, _access_key(), "script", "stored"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("change", ["policy", "revoke", "kill", "runtime"])
async def test_authority_change_during_wait_blocks_disclosure(hass, short_budget, change):
    access_key = _access_key()
    store = MagicMock()
    store.get_access_key_by_hash.return_value = access_key
    store.get_settings.return_value.kill_switch = False
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    authority = request_authority.set(RequestAuthority(hass, data, access_key.id, access_key.access_key_hash))
    version = policy_version.set(access_key.settings_version)
    task = asyncio.create_task(observation.async_observe_registration(hass, access_key, "script", "stored"))
    try:
        await asyncio.sleep(0)
        if change == "policy":
            access_key.settings_version += 1
        elif change == "revoke":
            store.get_access_key_by_hash.return_value = None
        elif change == "kill":
            store.get_settings.return_value.kill_switch = True
        else:
            hass.data[DOMAIN] = object()
        entry = _entry(hass)
        hass.states.async_set(entry.entity_id, "off")
        with pytest.raises(AuthorityChangedError):
            await task
    finally:
        request_authority.reset(authority)
        policy_version.reset(version)
        hass.data.pop(DOMAIN, None)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
def write_env(hass, tmp_path):
    (tmp_path / "configuration.yaml").write_text("script: !include scripts.yaml\nscene: !include scenes.yaml\n")
    (tmp_path / "scripts.yaml").write_text("{}\n")
    (tmp_path / "scenes.yaml").write_text("[]\n")
    hass.states.async_set("light.member", "off")
    for domain in ("script", "scene"):
        hass.services.async_register(domain, "reload", lambda call: None)
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


@pytest.mark.parametrize("domain", ["script", "scene"])
async def test_write_timeout_edit_and_restore_receipts(hass, write_env, immediate_authoring_observation, domain):
    access_key = _access_key()
    config = {"alias": "Before", "sequence": []} if domain == "script" else {"name": "Before", "entities": {"light.member": "on"}}
    args = {"config": config}
    if domain == "script":
        args["script_id"] = "stored"
    with patch.object(type(hass.services), "async_call", wraps=hass.services.async_call) as reload:
        result, outcome, _ = await _call_tool(f"create_{domain}", args, access_key, hass, write_env)
    reload.assert_awaited_once_with(domain, "reload", blocking=True)
    body = _body(result)
    storage_id = body[f"{domain}_id"]
    assert outcome == "allowed" and not result.get("isError")
    assert body["write_success"] is True and body["reload_status"] == "completed"
    assert body["registration"] == {"status": "timed_out", "entity_id": None, "state_present": None}
    assert "not the write" in body["message"]
    assert len(write_env.versions.list_for(domain, storage_id)) == 1
    original = write_env.versions.list_for(domain, storage_id)[0]
    entry = _entry(hass, domain, storage_id)
    hass.states.async_set(entry.entity_id, "off")
    config = dict(config, **({"alias": "After"} if domain == "script" else {"name": "After"}))
    result, outcome, _ = await _call_tool(f"edit_{domain}", {f"{domain}_id": storage_id, "config": config}, access_key, hass, write_env)
    assert outcome == "allowed"
    assert _body(result)["registration"]["entity_id"] == entry.entity_id
    assert len(write_env.versions.list_for(domain, storage_id)) == 2
    result, outcome, _ = await async_restore_version(original, "admin", hass, write_env, side="after")
    assert outcome == "allowed"
    assert _body(result)["registration"]["entity_id"] == entry.entity_id
    assert write_env.versions.list_for(domain, storage_id)[0].action == "rollback"


async def test_cancelled_wait_keeps_write_and_version(hass, write_env, monkeypatch):
    entered = asyncio.Event()
    from custom_components.phoenix_mcp.tools import authoring
    real_observer = authoring.async_observe_registration

    async def observe(*args):
        entered.set()
        return await real_observer(*args)

    monkeypatch.setattr(authoring, "async_observe_registration", observe)
    task = asyncio.create_task(_call_tool("create_script", {"script_id": "stored", "config": {"sequence": []}}, _access_key(), hass, write_env))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(write_env.versions.list_for("script", "stored")) == 1
    result, outcome, _ = await _call_tool("get_script", {"script_id": "stored"}, _access_key(), hass, write_env)
    assert outcome == "allowed" and _body(result)["config"]["sequence"] == []


@pytest.mark.parametrize("error", [HomeAssistantError("Reload rejected: password=hidden-value"), RuntimeError("private internal detail")])
async def test_reload_failure_preserves_saved_write_and_safe_error(hass, write_env, error, caplog):
    async def reload(_call):
        raise error

    hass.services.async_register("script", "reload", reload)
    result, outcome, _ = await _call_tool("create_script", {"script_id": "stored", "config": {"sequence": []}}, _access_key(), hass, write_env)
    body = _body(result)
    assert result["isError"] and outcome == "invalid_request"
    assert body["write_success"] is True and body["reload_status"] == "failed"
    assert body["registration"]["status"] == "not_checked"
    assert "hidden-value" not in body["error"] and "private internal detail" not in body["error"]
    assert "hidden-value" not in caplog.text
    if isinstance(error, HomeAssistantError):
        assert "Reload rejected" in body["error"]
    assert len(write_env.versions.list_for("script", "stored")) == 1


@pytest.mark.parametrize("domain", ["scene", "script"])
async def test_approved_executor_observes_and_records_once(hass, write_env, immediate_authoring_observation, domain):
    from custom_components.phoenix_mcp.mcp_view import async_execute_approved_tool

    entry = _entry(hass, domain)
    hass.states.async_set(entry.entity_id, "off")
    config = {"sequence": []} if domain == "script" else {"name": "Approved", "entities": {"light.member": "on"}}
    result, outcome, _ = await async_execute_approved_tool(
        f"create_{domain}", {f"{domain}_id": "stored", "config": config}, _access_key(), hass, write_env,
    )
    assert outcome == "allowed"
    assert _body(result)["registration"]["entity_id"] == entry.entity_id
    assert len(write_env.versions.list_for(domain, "stored")) == 1


async def test_observer_failure_does_not_misreport_saved_write(hass, write_env, monkeypatch):
    from custom_components.phoenix_mcp.tools import authoring

    monkeypatch.setattr(authoring, "async_observe_registration", AsyncMock(side_effect=RuntimeError("private detail")))
    result, outcome, _ = await _call_tool("create_script", {"script_id": "stored", "config": {"sequence": []}}, _access_key(), hass, write_env)
    body = _body(result)
    assert outcome == "allowed" and not result.get("isError")
    assert body["write_success"] is True and body["reload_status"] == "completed"
    assert body["registration"]["status"] == "observation_failed"
    assert "private detail" not in str(body)
    assert len(write_env.versions.list_for("script", "stored")) == 1
