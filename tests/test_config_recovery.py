"""Durability, secret refusal, cancellation, and real Template recovery tests."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery, ConfigurationRecoveryError
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _restore_token
from custom_components.phoenix_mcp.template_recovery import template_snapshot, restore_template


def data():
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())


async def test_failed_save_keeps_barrier_and_no_active_claim():
    store = MagicMock(async_save=AsyncMock(side_effect=OSError))
    recovery = ConfigurationRecovery(store)
    with pytest.raises(OSError):
        await recovery.begin("r", "delete", "t", {"name": "safe"})
    assert recovery.records()[0]["phase"] == "prepared"
    assert not recovery.records()[0]["active"]
    with pytest.raises(ConfigurationRecoveryError):
        await recovery.begin("r", "delete", "t", {})


async def test_cancellation_and_reload_keep_snapshot():
    store = MagicMock(async_save=AsyncMock())
    recovery = ConfigurationRecovery(store)
    rid = await recovery.begin("r", "delete", "t", {"name": "safe"})
    await recovery.mark(rid, "dispatching")
    recovery.leave(rid)
    store.async_load = AsyncMock(return_value=store.async_save.call_args.args[0])
    fresh = ConfigurationRecovery(store)
    await fresh.async_load()
    assert fresh.get(rid)["before"] == {"name": "safe"}
    with pytest.raises(ConfigurationRecoveryError):
        await fresh.begin("r", "delete", "t", {})
    with pytest.raises(ConfigurationRecoveryError):
        await fresh.reviewed(rid, "admin", delete=True)
    await fresh.reviewed(rid, "admin")
    await fresh.reviewed(rid, "admin", delete=True)
    assert fresh.records() == []


@pytest.mark.parametrize("payload", [{"password": "secret"}, {"x": "https://user:pass@host"}, {"x": "<redacted>"}, {"x": float("nan")}])
async def test_secret_or_unrestorable_snapshot_never_persists(payload):
    store = MagicMock(async_save=AsyncMock())
    with pytest.raises(ConfigurationRecoveryError):
        await ConfigurationRecovery(store).begin("r", "delete", "t", payload)
    store.async_save.assert_not_awaited()


async def test_scrubber_failure_and_retryable_initialization():
    store = MagicMock(async_save=AsyncMock(), async_load=AsyncMock(side_effect=[OSError, None]))
    recovery = ConfigurationRecovery(store)
    with pytest.raises(ConfigurationRecoveryError):
        await recovery.async_load()
    with patch("custom_components.phoenix_mcp.config_recovery.redact_structure", side_effect=RuntimeError):
        with pytest.raises(ConfigurationRecoveryError):
            await recovery.begin("r", "delete", "t", {})
    store.async_save.assert_not_awaited()
    await recovery.begin("r", "delete", "t", {})
    assert store.async_load.await_count == 2


async def test_cancelled_phase_does_not_claim_verified():
    store = MagicMock(async_save=AsyncMock())
    recovery = ConfigurationRecovery(store)
    rid = await recovery.begin("r", "edit", "t", {})
    await recovery.mark(rid, "dispatching")
    store.async_save.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await recovery.mark(rid, "verified")
    assert recovery.get(rid)["phase"] == "dispatching"


async def create_template(hass, kind="sensor"):
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init("template", context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": kind})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Recover me", "state": "{{ 7 }}" if kind == "sensor" else "{{ true }}"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


@pytest.mark.parametrize("kind", ["sensor", "binary_sensor"])
async def test_real_template_recreation_preserves_identity(hass, kind):
    entry = await create_template(hass, kind)
    registry = er.async_get(hass)
    entity = er.async_entries_for_config_entry(registry, entry.entry_id)[0]
    target = f"{kind}.custom_recovered"
    registry.async_update_entity(entity.entity_id, new_entity_id=target, name="My custom name")
    await hass.async_block_till_done()
    snapshot = template_snapshot(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    runtime = data()
    result, outcome, _ = await restore_template(snapshot, _restore_token("admin"), hass, runtime)
    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed", body
    assert body["entry_id"] != entry.entry_id
    assert registry.async_get(target).name == "My custom name"
    assert dict(hass.config_entries.async_get_entry(body["entry_id"]).options) == snapshot["options"]
    assert runtime.config_recovery.records()[0]["phase"] == "verified"


async def test_real_template_collision_never_creates(hass):
    entry = await create_template(hass)
    snapshot = template_snapshot(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(snapshot["entities"][0]["entity_id"], "occupied")
    result, outcome, _ = await restore_template(snapshot, _restore_token("admin"), hass, data())
    assert outcome == "invalid_request"
    assert "occupied" in result["content"][0]["text"]
    assert not hass.config_entries.async_entries("template")


async def test_real_template_settings_and_deleted_restore_from_history(hass):
    from custom_components.phoenix_mcp.mcp_view import _execute_remove_integration, async_restore_version
    from custom_components.phoenix_mcp.tools.helper import _execute_set_config_entry_options
    entry = await create_template(hass)
    runtime = data()
    token = _restore_token("admin")
    result, outcome, _ = await _execute_set_config_entry_options(
        {"entry_id": entry.entry_id, "settings": {"state": "{{ 8 }}"}}, token, hass, runtime)
    assert outcome == "allowed", result
    assert entry.options["state"] == "{{ 8 }}"
    assert runtime.config_recovery.records()[0]["before"]["options"]["state"] == "{{ 7 }}"
    await hass.async_block_till_done()
    result, outcome, _ = await _execute_remove_integration({"entry_id": entry.entry_id}, token, hass, runtime)
    assert outcome == "allowed", result
    await hass.async_block_till_done()
    version = runtime.versions.list_for("config_entry", entry.entry_id)[0]
    assert version.before["snapshot_type"] == "template_helper"
    result, outcome, _ = await async_restore_version(version, "admin", hass, runtime)
    assert outcome == "allowed", result


async def test_template_storage_failure_prevents_removal(hass):
    from custom_components.phoenix_mcp.mcp_view import _execute_remove_integration
    entry = await create_template(hass)
    runtime = data()
    runtime.config_recovery = ConfigurationRecovery(MagicMock(async_save=AsyncMock(side_effect=OSError)))
    result, outcome, _ = await _execute_remove_integration({"entry_id": entry.entry_id}, _restore_token("admin"), hass, runtime)
    assert outcome == "invalid_request"
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry


async def test_template_identity_changes_during_safety_save_refuse(hass):
    from custom_components.phoenix_mcp.mcp_view import _execute_remove_integration
    entry = await create_template(hass)
    runtime = data()
    async def change(_):
        hass.config_entries.async_update_entry(entry, title="Changed elsewhere")
    runtime.config_recovery = ConfigurationRecovery(MagicMock(async_save=AsyncMock(side_effect=change)))
    result, outcome, _ = await _execute_remove_integration({"entry_id": entry.entry_id}, _restore_token("admin"), hass, runtime)
    assert outcome == "invalid_request"
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert runtime.config_recovery.records()[0]["phase"] == "not_applied"


async def test_restart_lost_response_is_not_replayed_and_fresh_runtime_is_distinct(hass):
    from custom_components.phoenix_mcp.mcp_view import _execute_restart_ha, _tool_get_config
    runtime = data()
    token = _restore_token("admin")
    with patch.object(type(hass.services), "async_call", new=AsyncMock(side_effect=asyncio.TimeoutError)) as dispatch:
        result, outcome, _ = await _execute_restart_ha({}, token, hass, runtime)
        assert outcome == "allowed"
        receipt = json.loads(result["content"][0]["text"])
        assert receipt["retry_safe"] is False
        _, outcome, _ = await _execute_restart_ha({}, token, hass, runtime)
        assert outcome == "invalid_request"
        dispatch.assert_awaited_once()
    observed, _, _ = await _tool_get_config({}, token, hass)
    assert json.loads(observed["content"][0]["text"])["runtime_identity"] == receipt["runtime_identity"]
    fresh = MagicMock(data={})
    fresh.config.as_dict.return_value = {}
    observed, _, _ = await _tool_get_config({}, token, fresh)
    assert json.loads(observed["content"][0]["text"])["runtime_identity"] != receipt["runtime_identity"]
    assert runtime.config_recovery.records()[0]["phase"] == "dispatching"


async def test_restore_existing_template_reproduces_optional_fields_and_custom_name(hass):
    from custom_components.phoenix_mcp.tools.helper import _execute_set_config_entry_options
    entry = await create_template(hass)
    snapshot = template_snapshot(hass, entry)
    runtime = data()
    token = _restore_token("admin")
    result, outcome, _ = await _execute_set_config_entry_options({"entry_id": entry.entry_id,
        "settings": {"state": "{{ 9 }}", "unit_of_measurement": "m"}}, token, hass, runtime)
    assert outcome == "allowed", result
    await hass.async_block_till_done()
    entity_id = snapshot["entities"][0]["entity_id"]
    er.async_get(hass).async_update_entity(entity_id, name="Changed")
    result, outcome, _ = await restore_template(snapshot, token, hass, runtime)
    assert outcome == "allowed", result
    assert dict(entry.options) == snapshot["options"]
    assert er.async_get(hass).async_get(entity_id).name is None
    assert runtime.config_recovery.records()[0]["source"] == snapshot


@pytest.mark.parametrize("change", ["legacy", "version", "unknown_field"])
async def test_incompatible_snapshot_refuses_before_creation(hass, change):
    entry = await create_template(hass)
    snapshot = template_snapshot(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    if change == "legacy":
        del snapshot["entities"]
    elif change == "version":
        snapshot["version"] = 999
    else:
        snapshot["options"]["unsupported"] = "x"
    result, outcome, _ = await restore_template(snapshot, _restore_token("admin"), hass, data())
    assert outcome == "invalid_request", result
    assert not hass.config_entries.async_entries("template")
    assert not hass.config_entries.flow.async_progress()


async def test_recreation_partial_keeps_replacement_and_source(hass):
    entry = await create_template(hass)
    snapshot = template_snapshot(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    original = registry.async_update_entity
    def fail_name(entity_id, **kwargs):
        if "new_entity_id" in kwargs:
            raise RuntimeError("injected registry failure")
        return original(entity_id, **kwargs)
    runtime = data()
    with patch.object(registry, "async_update_entity", side_effect=fail_name):
        result, outcome, _ = await restore_template(snapshot, _restore_token("admin"), hass, runtime)
    assert outcome == "invalid_request"
    body = json.loads(result["content"][0]["text"])
    assert body["status"] == "partial"
    assert hass.config_entries.async_get_entry(body["replacement_entry_id"]) is not None
    assert runtime.config_recovery.records()[0]["source"] == snapshot
    assert runtime.config_recovery.records()[0]["phase"] == "partial"


async def test_completed_artifacts_are_not_rotated_by_history_or_capacity(monkeypatch):
    from custom_components.phoenix_mcp import config_recovery
    from custom_components.phoenix_mcp.version_store import VersionStore
    monkeypatch.setattr(config_recovery, "CONFIG_RECOVERY_MAX_RECORDS", 1)
    recovery = ConfigurationRecovery()
    rid = await recovery.begin("helper:x", "delete", "t", {"name": "only copy"})
    await recovery.mark(rid, "verified")
    recovery.leave(rid)
    versions = VersionStore()
    for _ in range(30):
        await versions.record(resource_type="helper", resource_id="x", action="edit", before={}, after={})
    assert recovery.get(rid)["before"] == {"name": "only copy"}
    with pytest.raises(ConfigurationRecoveryError):
        await recovery.begin("helper:y", "delete", "t", {})
    assert recovery.get(rid) is not None


async def test_private_atomic_store_uses_real_filesystem(hass, tmp_path):
    from homeassistant.helpers.storage import Store
    store = Store(hass, 1, "recovery-atomic-test", private=True, atomic_writes=True)
    # Bypass the suite's mocked HA disk-write boundary, not the implementation.
    await hass.async_add_executor_job(store._write_prepared_data, "w", '{"data":{"records":{}}}')
    path = tmp_path / ".storage" / "recovery-atomic-test"
    assert json.loads(path.read_text())["data"]["records"] == {}
    assert path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    old = path.read_bytes()
    with patch("homeassistant.util.file.AtomicWriter.commit", side_effect=OSError):
        with pytest.raises(Exception):
            await hass.async_add_executor_job(store._write_prepared_data, "w", '{"changed":true}')
    assert path.read_bytes() == old


async def test_admin_recovery_requires_ha_admin_and_keeps_active_records(hass):
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    from custom_components.phoenix_mcp.admin_view import PhoenixAdminConfigurationRecoveryView, PhoenixAdminConfigurationRecoveryRecordView
    from custom_components.phoenix_mcp.const import DOMAIN
    runtime = data()
    hass.data[DOMAIN] = runtime
    rid = await runtime.config_recovery.begin("helper:test", "delete", "t", {"name": "safe"})
    user = MagicMock(is_admin=False, id="admin")
    values = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "request"}
    request = MagicMock()
    request.get.side_effect = lambda k, default=None: values.get(k, default)
    request.__getitem__.side_effect = values.__getitem__
    view = PhoenixAdminConfigurationRecoveryView()
    view.hass = hass
    assert (await view.get(request)).status == 403
    user.is_admin = True
    response = await view.get(request)
    assert response.status == 200
    assert json.loads(response.body)["records"][0]["id"] == rid
    action_view = PhoenixAdminConfigurationRecoveryRecordView()
    action_view.hass = hass
    with patch("custom_components.phoenix_mcp.admin_view._read_body", new=AsyncMock(return_value={"action": "reviewed"})):
        assert (await action_view.post(request, recovery_id=rid)).status == 409
    runtime.config_recovery.leave(rid)
    with patch("custom_components.phoenix_mcp.admin_view._read_body", new=AsyncMock(return_value={"action": "reviewed"})):
        assert (await action_view.post(request, recovery_id=rid)).status == 200
    assert runtime.config_recovery.get(rid)["reviewed_by"] == "admin"


async def test_template_change_during_form_preparation_never_submits(hass):
    from custom_components.phoenix_mcp.tools import helper
    entry = await create_template(hass)
    runtime = data()
    original = helper._init_settings_flow

    async def changed(*args):
        form = await original(*args)
        hass.config_entries.async_update_entry(entry, title="Changed externally")
        return form

    with patch.object(helper, "_init_settings_flow", side_effect=changed), patch.object(
        helper, "_configure_settings_flow", new=AsyncMock()
    ) as submit:
        result = await helper._execute_set_config_entry_options(
            {"entry_id": entry.entry_id, "settings": {"state": "{{ 99 }}"}},
            _restore_token("admin"), hass, runtime,
        )
    assert result[0]["isError"]
    submit.assert_not_awaited()
    assert entry.options["state"] == "{{ 7 }}"
    assert runtime.config_recovery.records()[0]["before"]["title"] == "Recover me"
