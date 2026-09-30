"""Durability, secret refusal, cancellation, and real Template recovery tests."""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery, ConfigurationRecoveryError
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _restore_token, _call_tool
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.phoenix_mcp.template_recovery import template_snapshot, restore_template
from homeassistant.util.dt import utcnow


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


def _template_agent(domain: str, *, token_id: str | None = None) -> TokenRecord:
    return TokenRecord(
        id=token_id or uuid.uuid4().hex, name="template-agent", token_hash="x",
        created_at=utcnow(), created_by="test",
        permissions=PermissionTree(domains={domain: PermissionNode(state="GREEN")}),
        cap_helper_write="allow", cap_registry_read="allow",
    )


async def test_agent_template_restore_is_owner_scoped_and_rechecks_target(hass):
    from custom_components.phoenix_mcp.tools.helper import _execute_set_config_entry_options

    entry = await create_template(hass)
    domain = entry.options["template_type"]
    token = _template_agent(domain)
    runtime = data()
    source = template_snapshot(hass, entry)
    await _execute_set_config_entry_options(
        {"entry_id": entry.entry_id, "settings": {"state": "{{ 8 }}"}},
        token, hass, runtime,
    )
    recovery_id = await runtime.config_recovery.begin(
        f"config_entry:{entry.entry_id}", "test_agent_restore", token.id, source,
    )
    await runtime.config_recovery.mark(recovery_id, "verified")
    runtime.config_recovery.leave(recovery_id)

    receipt, outcome, _ = await _call_tool(
        "restore_configuration", {"recovery_id": recovery_id, "side": "before"},
        token, hass, runtime,
    )
    assert outcome == "allowed"
    body = json.loads(receipt["content"][0]["text"])
    assert body["status"] == "needs_confirmation"
    assert body["adapter"]["type"] == "sensor"

    stranger = _template_agent(domain)
    result, outcome, _ = await _call_tool(
        "restore_configuration", {"recovery_id": recovery_id, "side": "before"},
        stranger, hass, runtime,
    )
    assert outcome == "not_found"

    stale = dict(body)
    stale["target_fingerprint"] = "stale"
    result, outcome, _ = await _call_tool(
        "restore_configuration", {
            "recovery_id": recovery_id, "side": "before",
            "snapshot_digest": stale["snapshot_digest"],
            "target_fingerprint": stale["target_fingerprint"],
        }, token, hass, runtime,
    )
    assert outcome == "invalid_request"
    assert "changed" in result["content"][0]["text"]

    result, outcome, _ = await _call_tool(
        "restore_configuration", {
            "recovery_id": recovery_id, "side": "before",
            "snapshot_digest": body["snapshot_digest"],
            "target_fingerprint": body["target_fingerprint"],
        }, token, hass, runtime,
    )
    assert outcome == "allowed", result
    assert entry.options["state"] == source["options"]["state"]


async def test_agent_authored_file_restore_rechecks_digest_and_target(hass):
    from custom_components.phoenix_mcp.helpers import content_hash
    from custom_components.phoenix_mcp.tools.config_files import _authored_file_snapshot
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration

    token = TokenRecord(
        id=uuid.uuid4().hex, name="file-agent", token_hash="x",
        created_at=utcnow(), created_by="test", permissions=PermissionTree(),
        cap_filesystem="allow",
    )
    path = hass.config.path("www", "recovery-smoke.txt")
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("current", encoding="utf-8")
    snapshot = _authored_file_snapshot("www/recovery-smoke.txt", True, "before")
    runtime = data()
    rid = await runtime.config_recovery.begin(
        "authored_file:www/recovery-smoke.txt", "write_file", token.id, snapshot,
    )
    await runtime.config_recovery.mark(rid, "verified")
    target = _authored_file_snapshot("www/recovery-smoke.txt", True, "current")
    bound = {
        "recovery_id": rid, "side": "before",
        "snapshot_digest": content_hash(snapshot),
        "target_fingerprint": content_hash(target),
    }

    result, outcome, _ = await _execute_restore_configuration(bound, token, hass, runtime)

    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed", body
    assert body["status"] == "verified"
    assert Path(path).read_text(encoding="utf-8") == "before"


async def test_agent_authored_file_restore_removes_created_file(hass):
    from pathlib import Path
    from custom_components.phoenix_mcp.helpers import content_hash
    from custom_components.phoenix_mcp.tools.config_files import _authored_file_snapshot
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration

    token = TokenRecord(
        id=uuid.uuid4().hex, name="file-agent", token_hash="x",
        created_at=utcnow(), created_by="test", permissions=PermissionTree(),
        cap_filesystem="allow",
    )
    rel = "www/recovery-created.txt"
    path = Path(hass.config.path(rel))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("created", encoding="utf-8")
    absent = _authored_file_snapshot(rel, False, None)
    current = _authored_file_snapshot(rel, True, "created")
    runtime = data()
    rid = await runtime.config_recovery.begin(
        f"authored_file:{rel}", "write_file", token.id, absent, source=current,
    )
    await runtime.config_recovery.mark(rid, "verified")
    result, outcome, _ = await _execute_restore_configuration({
        "recovery_id": rid, "side": "before",
        "snapshot_digest": content_hash(absent),
        "target_fingerprint": content_hash(current),
    }, token, hass, runtime)

    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed", body
    assert body["status"] == "verified"
    assert not path.exists()
    assert runtime.config_recovery.records()[0]["source"] == absent


async def test_agent_blueprint_restore_uses_ha_blueprint_command(hass):
    from pathlib import Path
    from custom_components.phoenix_mcp.helpers import content_hash
    from custom_components.phoenix_mcp.tools.blueprint import _blueprint_recovery_snapshot
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration

    token = TokenRecord(
        id=uuid.uuid4().hex, name="blueprint-agent", token_hash="x",
        created_at=utcnow(), created_by="test", permissions=PermissionTree(),
        cap_blueprint_write="allow", cap_automation_write="allow",
    )
    path = Path(hass.config.path("blueprints", "automation", "author", "restore.yaml"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("blueprint: current\n", encoding="utf-8")
    snapshot = _blueprint_recovery_snapshot("automation", "author/restore.yaml", "blueprint:\n  name: Before\n  domain: automation\ntriggers: []\nactions: []\n")
    target = _blueprint_recovery_snapshot("automation", "author/restore.yaml", "blueprint: current\n")
    runtime = data()
    rid = await runtime.config_recovery.begin(
        "blueprint:automation/author/restore.yaml", "edit_blueprint", token.id, snapshot,
    )
    await runtime.config_recovery.mark(rid, "verified")

    async def ws(_hass, command, payload):
        assert command == "blueprint/save"
        from homeassistant.components.blueprint.models import Blueprint
        from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
        from homeassistant.util import yaml as yaml_util
        path.write_text(Blueprint(yaml_util.parse_yaml(payload["yaml"]), expected_domain="automation", schema=BLUEPRINT_SCHEMA).yaml(), encoding="utf-8")
        return {"overrides_existing": True}

    with patch("custom_components.phoenix_mcp.ws_dispatch.async_ws_command", AsyncMock(side_effect=ws)):
        result, outcome, _ = await _execute_restore_configuration({
            "recovery_id": rid, "side": "before",
            "snapshot_digest": content_hash(snapshot),
            "target_fingerprint": content_hash(target),
        }, token, hass, runtime)

    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed", body
    assert body["status"] == "verified"
    assert "name: Before" in path.read_text(encoding="utf-8")
    assert runtime.config_recovery.records()[0]["source"]["content"] == path.read_text(encoding="utf-8")


async def test_agent_dashboard_registry_restore_uses_dashboard_command(hass):
    from custom_components.phoenix_mcp.helpers import content_hash
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration

    token = TokenRecord(
        id=uuid.uuid4().hex, name="dashboard-agent", token_hash="x",
        created_at=utcnow(), created_by="test", permissions=PermissionTree(),
        cap_lovelace_write="allow",
    )
    row = {"id": "dash-1", "url_path": "dashboard-one", "title": "Current"}
    rows = [dict(row)]

    async def ws(_hass, command, payload=None):
        if command == "lovelace/dashboards/list":
            return [dict(item) for item in rows]
        if command == "lovelace/dashboards/update":
            current = next(item for item in rows if item["id"] == payload["dashboard_id"])
            current.update({k: v for k, v in payload.items() if k != "dashboard_id"})
            return dict(current)
        raise AssertionError(command)

    snapshot = {
        "snapshot_type": "dashboard_registry", "schema": 1,
        "resource_id": "dashboard-one", "exists": True,
        "dashboard": {"id": "dash-1", "url_path": "dashboard-one", "title": "Before"},
    }
    runtime = data()
    rid = await runtime.config_recovery.begin(
        "dashboard_registry:dashboard-one", "edit_dashboard", token.id, snapshot,
    )
    await runtime.config_recovery.mark(rid, "verified")
    target = {
        "snapshot_type": "dashboard_registry", "schema": 1,
        "resource_id": "dashboard-one", "exists": True, "dashboard": row,
    }
    with patch("custom_components.phoenix_mcp.tools.lovelace.async_ws_command", AsyncMock(side_effect=ws)), patch(
        "custom_components.phoenix_mcp.ws_dispatch.async_ws_command", AsyncMock(side_effect=ws),
    ):
        result, outcome, _ = await _execute_restore_configuration({
            "recovery_id": rid, "side": "before",
            "snapshot_digest": content_hash(snapshot),
            "target_fingerprint": content_hash(target),
        }, token, hass, runtime)

    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed", body
    assert body["status"] == "verified"
    assert rows[0]["title"] == "Before"
    assert runtime.config_recovery.records()[0]["operation"] == "dashboard_registry_restore"


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


async def test_invalid_template_settings_do_not_create_recovery_barrier(hass):
    """A field rejected by the real options schema cannot block the next edit."""
    from custom_components.phoenix_mcp.tools.helper import _execute_set_config_entry_options

    entry = await create_template(hass)
    runtime = data()
    token = _restore_token("admin")
    result, outcome, _ = await _execute_set_config_entry_options(
        {"entry_id": entry.entry_id, "settings": {"name": "Rejected rename"}},
        token, hass, runtime,
    )
    assert outcome == "invalid_request"
    assert "rejected" in result["content"][0]["text"]
    assert runtime.config_recovery.records() == []
    assert entry.options["state"] == "{{ 7 }}"
    result, outcome, _ = await _execute_set_config_entry_options(
        {"entry_id": entry.entry_id, "settings": {"state": "{{ 8 }}"}},
        token, hass, runtime,
    )
    assert outcome == "allowed", result
    assert entry.options["state"] == "{{ 8 }}"


def test_numeric_version_is_not_a_helper_entity_reference():
    """Update version strings coexist with recursively scoped entity targets."""
    from custom_components.phoenix_mcp.tools.helper import _entities_in_options

    assert _entities_in_options({
        "installed_version": "1.0", "latest_version": "2.0",
        "install": [{"target": {"entity_id": "update.firmware"}}],
    }) == {"update.firmware"}


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


async def test_completed_artifacts_rotate_automatically_at_capacity(monkeypatch):
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
    next_id = await recovery.begin("helper:y", "delete", "t", {})
    assert recovery.get(rid) is None
    with pytest.raises(ConfigurationRecoveryError):
        await recovery.begin("helper:z", "delete", "t", {})
    assert recovery.get(next_id) is not None


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


@pytest.mark.parametrize("change_on_call", [1, 2])
async def test_template_change_during_form_preparation_never_submits(hass, change_on_call):
    from custom_components.phoenix_mcp.tools import helper
    entry = await create_template(hass)
    runtime = data()
    original = helper._init_settings_flow
    calls = 0

    async def changed(*args):
        nonlocal calls
        calls += 1
        form = await original(*args)
        if calls == change_on_call:
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
    if change_on_call == 1:
        assert runtime.config_recovery.records() == []
    else:
        assert runtime.config_recovery.records()[0]["before"]["title"] == "Recover me"
