"""Recovery regressions exercised through real HA APIs and uncertain effects."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.tools.recovery import _tool_restore_configuration
from custom_components.phoenix_mcp.tools.lovelace import _execute_set_dashboard_config, _recoverable_dashboard_save
from custom_components.phoenix_mcp.tools.config_files import _authored_file_snapshot
from custom_components.phoenix_mcp.tools.energy import _execute_edit_energy_config
from custom_components.phoenix_mcp.ws_dispatch import async_ws_command, WsDispatchError


def runtime():
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())


def access_key(**caps):
    return AccessKeyRecord(id="audit", name="audit", access_key_hash="x", created_at=utcnow(), created_by="test", permissions=PermissionTree(), **caps)


def body(result):
    return json.loads(result[0]["content"][0]["text"])


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
async def test_file_recovery_preserves_line_endings_through_write_read_and_restore(hass, newline):
    from custom_components.phoenix_mcp.tools.config_files import _execute_write_file, _tool_read_file
    from custom_components.phoenix_mcp.helpers import content_hash

    t, d = access_key(cap_filesystem="allow"), runtime()
    path = Path(hass.config.path("www", "endings.txt"))
    path.parent.mkdir(parents=True, exist_ok=True)
    before = newline.join(["original", "text", ""])
    wanted = newline.join(["changed", "text", ""])
    path.write_bytes(before.encode())
    result = await _execute_write_file({"path": "www/endings.txt", "content": wanted,
                                        "expected_hash": content_hash(before)}, t, hass, d)
    assert result[1] == "allowed", result
    saved = d.config_recovery.get(body(result)["recovery_id"])
    assert saved["phase"] == "verified"
    assert saved["before"]["content"] == before
    assert saved["source"]["content"] == wanted
    assert path.read_bytes() == wanted.encode()
    read = await _tool_read_file({"path": "www/endings.txt"}, t, hass)
    assert body(read)["content"] == wanted
    restored = await _execute_write_file({"path": "www/endings.txt", "content": saved["before"]["content"],
                                          "expected_hash": content_hash(wanted)}, t, hass, d)
    assert restored[1] == "allowed", restored
    assert d.config_recovery.get(body(restored)["recovery_id"])["phase"] == "verified"
    assert path.read_bytes() == before.encode()


async def test_real_dashboard_registry_restore_updates_only_supported_fields(hass, hass_admin_user):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    from custom_components.phoenix_mcp.tools.lovelace import _execute_create_dashboard, _execute_edit_dashboard
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    created = await _execute_create_dashboard({"config": {"url_path": "audit-board", "title": "Before"}}, t, hass, d)
    assert created[1] == "allowed", created
    row = body(created)["dashboard"]
    edited = await _execute_edit_dashboard({"dashboard_id": row["id"], "config": {"title": "After"}}, t, hass, d)
    assert edited[1] == "allowed", edited
    args = {"recovery_id": body(edited)["recovery_id"], "side": "before"}
    receipt = await _tool_restore_configuration(args, t, hass, d)
    info = body(receipt)
    args.update({k: info[k] for k in ("snapshot_digest", "target_fingerprint")})
    restored = await _tool_restore_configuration(args, t, hass, d)
    assert restored[1] == "allowed", restored
    assert body(restored)["status"] == "verified"
    assert d.config_recovery.records()[0]["phase"] == "verified"
    rows = await async_ws_command(hass, "lovelace/dashboards/list", {})
    assert next(r for r in rows if r["id"] == row["id"])["title"] == "Before"


@pytest.mark.parametrize("operation", ["create", "edit"])
@pytest.mark.parametrize("field,previous,wanted", [
    ("title", "Before", "After"),
    ("icon", "mdi:home", "mdi:chart-box"),
    ("show_in_sidebar", False, True),
    ("require_admin", False, True),
])
async def test_dashboard_registry_stale_metadata_retains_barrier(
    hass, hass_admin_user, operation, field, previous, wanted,
):
    from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
    from custom_components.phoenix_mcp.tools import lovelace
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    config = {"url_path": "readback-board", "title": "Before", field: previous}
    if operation == "edit":
        created = await lovelace._execute_create_dashboard({"config": config}, t, hass, d)
        row_id = body(created)["dashboard"]["id"]
    submissions = []
    original_snapshot = lovelace._dashboard_registry_snapshot

    async def dispatch(host, command, payload):
        result = await async_ws_command(host, command, payload)
        if command.endswith(("/create", "/update")):
            submissions.append(command)
        return result

    async def stale_readback(host, key):
        snapshot = await original_snapshot(host, key)
        if submissions and snapshot.get("dashboard"):
            snapshot["dashboard"][field] = previous
        return snapshot

    with patch.object(lovelace, "async_ws_command", side_effect=dispatch), patch.object(
        lovelace, "_dashboard_registry_snapshot", side_effect=stale_readback,
    ):
        if operation == "create":
            result = await lovelace._execute_create_dashboard(
                {"config": {**config, field: wanted}}, t, hass, d,
            )
        else:
            result = await lovelace._execute_edit_dashboard(
                {"dashboard_id": row_id, "config": {field: wanted}}, t, hass, d,
            )
    assert len(submissions) == 1
    assert body(result)["status"] == "partial"
    recovery = d.config_recovery.get(body(result)["recovery_id"])
    assert recovery["phase"] == "partial" and recovery.get("source") is None
    actual = await original_snapshot(hass, "readback-board")
    assert actual["dashboard"][field] == wanted
    assert recovery["expected"]["dashboard"][field] == wanted
    with pytest.raises(ConfigurationRecoveryError):
        await d.config_recovery.begin(recovery["resource"], "retry", t.id, {})


async def test_dashboard_registry_missing_submitted_field_is_incomplete(hass, hass_admin_user):
    from custom_components.phoenix_mcp.tools import lovelace
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    created = await lovelace._execute_create_dashboard(
        {"config": {"url_path": "nullable-board", "title": "Before", "icon": "mdi:home"}}, t, hass, d,
    )
    row_id = body(created)["dashboard"]["id"]
    original_snapshot = lovelace._dashboard_registry_snapshot
    applied = False

    async def dispatch(host, command, payload):
        nonlocal applied
        result = await async_ws_command(host, command, payload)
        if command.endswith("/update"):
            applied = True
        return result

    async def incomplete_readback(host, key):
        snapshot = await original_snapshot(host, key)
        if applied:
            snapshot["dashboard"].pop("icon")
        return snapshot

    with patch.object(lovelace, "async_ws_command", side_effect=dispatch), patch.object(
        lovelace, "_dashboard_registry_snapshot", side_effect=incomplete_readback,
    ):
        result = await lovelace._execute_edit_dashboard(
            {"dashboard_id": row_id, "config": {"icon": "mdi:chart-box"}}, t, hass, d,
        )
    assert applied and body(result)["status"] == "partial"
    assert (await original_snapshot(hass, row_id))["dashboard"]["icon"] == "mdi:chart-box"
    assert d.config_recovery.get(body(result)["recovery_id"])["phase"] == "partial"


async def test_dashboard_registry_native_null_clear_is_verified(hass, hass_admin_user):
    from custom_components.phoenix_mcp.tools import lovelace
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    created = await lovelace._execute_create_dashboard(
        {"config": {"url_path": "clear-board", "title": "Before", "icon": "mdi:home"}}, t, hass, d,
    )
    row_id = body(created)["dashboard"]["id"]
    result = await lovelace._execute_edit_dashboard(
        {"dashboard_id": row_id, "config": {"icon": None}}, t, hass, d,
    )
    assert body(result)["status"] == "verified"
    observed = await lovelace._dashboard_registry_snapshot(hass, row_id)
    assert "icon" not in observed["dashboard"]
    assert d.config_recovery.get(body(result)["recovery_id"])["source"] == observed


async def test_real_dashboard_layout_write_returns_recovery_id(hass):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    d = runtime()
    result = await _execute_set_dashboard_config({"config": {"views": []}}, access_key(cap_lovelace_write="allow"), hass, d)
    assert result[1] == "allowed", result
    assert d.config_recovery.records()[0]["phase"] == "verified"
    assert body(result)["recovery_id"] == d.config_recovery.records()[0]["id"]


@pytest.mark.parametrize("stale_readback", [True, False])
async def test_dashboard_layout_restore_records_only_verified_history(hass, stale_readback):
    from custom_components.phoenix_mcp.tools import lovelace
    from custom_components.phoenix_mcp.ws_dispatch import async_get_lovelace_config, async_save_lovelace_config
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    before, after = {"views": [{"title": "Before"}]}, {"views": [{"title": "After"}]}
    await lovelace._execute_set_dashboard_config({"config": before}, t, hass, d)
    edited = await lovelace._execute_set_dashboard_config({"config": after}, t, hass, d)
    args = {"recovery_id": body(edited)["recovery_id"], "side": "before"}
    receipt = body(await _tool_restore_configuration(args, t, hass, d))
    args.update({key: receipt[key] for key in ("snapshot_digest", "target_fingerprint")})
    previous_versions = {version.id for version in d.versions.list_recent()}
    submissions = []

    async def save(host, url_path, config):
        await async_save_lovelace_config(host, url_path, config)
        submissions.append(config)

    async def read(host, url_path):
        if submissions and stale_readback:
            return after
        return await async_get_lovelace_config(host, url_path)

    with patch.object(lovelace, "async_save_lovelace_config", side_effect=save), patch.object(
        lovelace, "async_get_lovelace_config", side_effect=read,
    ):
        result = await _tool_restore_configuration(args, t, hass, d)
    assert submissions == [before]
    assert await async_get_lovelace_config(hass, None) == before
    response = body(result)
    assert response["status"] == ("partial" if stale_readback else "verified")
    assert d.config_recovery.get(response["recovery_id"])["phase"] == response["status"]
    current_versions = {version.id for version in d.versions.list_recent()}
    assert len(current_versions - previous_versions) == (0 if stale_readback else 1)


async def test_file_restore_preview_compares_live_content(hass):
    d, t = runtime(), access_key(cap_filesystem="allow")
    rel = "www/audit.txt"
    p = Path(hass.config.path(rel))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("After")
    snapshot = _authored_file_snapshot(rel, True, "Before")
    rid = await d.config_recovery.begin(f"authored_file:{rel}", "write_file", t.id, snapshot)
    await d.config_recovery.mark(rid, "verified")
    d.config_recovery.leave(rid)
    result = await _tool_restore_configuration({"recovery_id": rid, "side": "before"}, t, hass, d)
    diff = body(result)["diff"]
    assert diff["before"] == "After"
    assert diff["after"] == "Before"
    assert p.read_text() == "After"


async def test_timeout_after_dashboard_effect_keeps_barrier(hass):
    d, t = runtime(), access_key(cap_lovelace_write="allow")
    before, after = {"views": []}, {"views": [{"title": "Changed"}]}
    actual = dict(before)
    async def save(*args):
        actual.clear()
        actual.update(after)
        raise WsDispatchError("WebSocket command timed out.")
    with patch("custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.lovelace.async_save_lovelace_config", AsyncMock(side_effect=save)):
        result = await _recoverable_dashboard_save(url_path=None, before=before, after=after, access_key=t, hass=hass, data=d)
    assert actual == after
    assert result[1] == "invalid_request"
    assert d.config_recovery.records()[0]["phase"] == "unknown"
    from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
    with pytest.raises(ConfigurationRecoveryError):
        await d.config_recovery.begin("dashboard_layout:lovelace", "retry", t.id, {})


async def test_energy_partial_readback_returns_observed_state(hass):
    d, t = runtime(), access_key(cap_energy_write="allow")
    before = {"energy_sources": [], "device_consumption": [{"stat_consumption": "sensor.old", "name": "Old"}], "device_consumption_water": []}
    with patch("custom_components.phoenix_mcp.tools.energy._read_prefs_raw", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.energy.async_ws_command", AsyncMock(return_value=None)):
        result = await _execute_edit_energy_config({"op": "remove_device", "device_name": "Old"}, t, hass, d)
    assert d.config_recovery.records()[0]["phase"] == "partial"
    assert result[1] == "allowed"
    assert body(result)["saved"] is False
    assert body(result)["status"] == "partial"
    assert body(result)["device_consumption"][0]["name"] == "Old"
    assert not d.versions.list_recent()


async def test_admin_restore_button_restores_another_access_keys_file(hass, hass_admin_user):
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    from custom_components.phoenix_mcp.admin_view import PhoenixAdminConfigurationRecoveryRecordView
    from custom_components.phoenix_mcp.const import DOMAIN
    d = runtime()
    hass.data[DOMAIN] = d
    path = Path(hass.config.path("www/audit.txt"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("After")
    snapshot = _authored_file_snapshot("www/audit.txt", True, "Before")
    rid = await d.config_recovery.begin("authored_file:www/audit.txt", "write_file", "audit", snapshot)
    await d.config_recovery.mark(rid, "verified")
    d.config_recovery.leave(rid)
    values = {KEY_HASS_USER: hass_admin_user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "audit"}
    request = MagicMock()
    request.get.side_effect = lambda k, default=None: values.get(k, default)
    request.__getitem__.side_effect = values.__getitem__
    view = PhoenixAdminConfigurationRecoveryRecordView()
    view.hass = hass
    with patch("custom_components.phoenix_mcp.admin_view._read_body", AsyncMock(return_value={"action": "restore", "side": "before"})):
        result = await view.post(request, recovery_id=rid)
    assert result.status == 200
    assert json.loads(result.text)["outcome"] == "allowed"
    assert path.read_text() == "Before"


async def test_real_blueprint_formatting_is_verified(hass, hass_admin_user):
    from custom_components.phoenix_mcp.tools.blueprint import _execute_create_blueprint
    assert await async_setup_component(hass, "automation", {})
    t = access_key(cap_blueprint_write="allow", cap_automation_write="allow")
    d = runtime()
    content = '# A preserved comment\nblueprint:\n  name: "Audit blueprint"\n  domain: automation\n  input: {}\ntriggers: []\nactions: []\n'
    result = await _execute_create_blueprint({"domain": "automation", "path": "audit/example.yaml", "content": content}, t, hass, d)
    assert result[1] == "allowed", result
    assert body(result)["saved"] is True, result
    actual = Path(hass.config.path("blueprints", "automation", "audit", "example.yaml")).read_text()
    assert "Audit blueprint" in actual and actual != content
    assert d.config_recovery.records()[0]["phase"] == "verified"
    assert d.config_recovery.records()[0]["source"]["content"] == actual


@pytest.mark.parametrize("scope", ["empty", "domain_read", "domain_deny", "entity_read", "entity_deny"])
@pytest.mark.parametrize("existing", [False, True])
async def test_template_recreation_refuses_revoked_entity_scope(hass, scope, existing):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp.template_recovery import template_snapshot
    from custom_components.phoenix_mcp.mcp_view import _call_tool
    from custom_components.phoenix_mcp.access_key_store import PermissionNode
    assert await async_setup_component(hass, "homeassistant", {})
    flow = hass.config_entries.flow
    result = await flow.async_init("template", context={"source": SOURCE_USER})
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "sensor"})
    result = await flow.async_configure(result["flow_id"], {"name": "Audit restore", "state": "{{ 7 }}"})
    await hass.async_block_till_done()
    entry = result["result"]
    saved = template_snapshot(hass, entry)
    t, d = access_key(cap_helper_write="allow"), runtime()
    t.permissions = PermissionTree(domains={"sensor": PermissionNode(state="GREEN")})
    rid = await d.config_recovery.begin(f"config_entry:{entry.entry_id}", "remove_integration", t.id, saved)
    if not existing:
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    await d.config_recovery.mark(rid, "verified")
    d.config_recovery.leave(rid)
    args = {"recovery_id": rid, "side": "before"}
    receipt = await _call_tool("restore_configuration", args, t, hass, d)
    assert receipt[1] == "allowed", receipt
    info = body(receipt)
    bound = {**args, **{k: info[k] for k in ("snapshot_digest", "target_fingerprint")}}
    eid = saved["entities"][0]["entity_id"]
    if scope == "empty":
        t.permissions = PermissionTree()
    elif scope.startswith("domain"):
        t.permissions = PermissionTree(domains={"sensor": PermissionNode(state="YELLOW" if scope.endswith("read") else "RED")})
    else:
        t.permissions.entities[eid] = PermissionNode(state="YELLOW" if scope.endswith("read") else "RED")
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration
    for result in (
        await _call_tool("restore_configuration", args, t, hass, d),
        await _execute_restore_configuration(bound, t, hass, d),
    ):
        assert result[1] == "denied", result
        assert result[0]["content"][0]["text"] == "Forbidden."
    eid = saved["entities"][0]["entity_id"]
    assert (er.async_get(hass).async_get(eid) is not None) == existing
    assert len(d.config_recovery.records()) == 1


@pytest.mark.parametrize("family", ["yaml_file", "blueprint_source", "energy_preferences", "dashboard_layout", "dashboard_registry"])
async def test_admin_recovery_restores_each_new_family(hass, hass_admin_user, family):
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    from custom_components.phoenix_mcp.admin_view import PhoenixAdminConfigurationRecoveryRecordView
    from custom_components.phoenix_mcp.const import DOMAIN
    from custom_components.phoenix_mcp.tools.lovelace import _dashboard_layout_snapshot, _dashboard_registry_snapshot
    from custom_components.phoenix_mcp.tools.blueprint import _blueprint_recovery_snapshot
    from custom_components.phoenix_mcp.tools.energy import _energy_recovery_snapshot
    from custom_components.phoenix_mcp.ws_dispatch import async_save_lovelace_config, async_get_lovelace_config
    d = runtime()
    hass.data[DOMAIN] = d
    current_prefs = {"energy_sources": [], "device_consumption": [], "device_consumption_water": []}
    original_ws = async_ws_command

    async def energy_ws(host, command, payload):
        if command == "energy/get_prefs":
            return dict(current_prefs)
        if command == "energy/save_prefs":
            current_prefs.update(payload)
            return dict(current_prefs)
        return await original_ws(host, command, payload)

    if family == "yaml_file":
        path = Path(hass.config.path("configuration.yaml"))
        path.write_text("default_config:\ninput_boolean: {}\n")
        snapshot = _authored_file_snapshot("configuration.yaml", True, "default_config:\n", "yaml_file")
    elif family == "blueprint_source":
        assert await async_setup_component(hass, "automation", {})
        path = Path(hass.config.path("blueprints", "automation", "test", "admin.yaml"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("blueprint:\n  name: Current\n  domain: automation\ntriggers: []\nactions: []\n")
        snapshot = _blueprint_recovery_snapshot("automation", "test/admin.yaml", "blueprint:\n  name: Restored\n  domain: automation\ntriggers: []\nactions: []\n")
    elif family == "energy_preferences":
        snapshot = _energy_recovery_snapshot(current_prefs)
        current_prefs["device_consumption"] = [{"stat_consumption": "external:meter"}]
    else:
        assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
        if family == "dashboard_layout":
            snapshot = _dashboard_layout_snapshot(None, {"views": [{"title": "Restored"}]})
            await async_save_lovelace_config(hass, None, {"views": []})
        else:
            row = await async_ws_command(hass, "lovelace/dashboards/create", {"url_path": "admin-restore", "title": "Restored"})
            snapshot = await _dashboard_registry_snapshot(hass, row["id"])
            await async_ws_command(hass, "lovelace/dashboards/update", {"dashboard_id": row["id"], "title": "Current"})
    rid = await d.config_recovery.begin("fixture", "write", "another-access-key", snapshot)
    await d.config_recovery.mark(rid, "verified")
    d.config_recovery.leave(rid)
    values = {KEY_HASS_USER: hass_admin_user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "test"}
    request = MagicMock()
    request.get.side_effect = lambda k, default=None: values.get(k, default)
    request.__getitem__.side_effect = values.__getitem__
    view = PhoenixAdminConfigurationRecoveryRecordView()
    view.hass = hass
    with patch("custom_components.phoenix_mcp.admin_view._read_body", AsyncMock(return_value={"action": "restore", "side": "before"})), patch("custom_components.phoenix_mcp.tools.energy.async_ws_command", side_effect=energy_ws):
        response = await view.post(request, recovery_id=rid)
    assert response.status == 200, response.text
    assert json.loads(response.text)["outcome"] == "allowed", response.text
    assert d.config_recovery.records()[0]["phase"] == "verified"
    if family == "yaml_file":
        assert path.read_text() == "default_config:\n"
    elif family == "blueprint_source":
        assert "name: Restored" in path.read_text()
    elif family == "energy_preferences":
        assert current_prefs["device_consumption"] == []
    elif family == "dashboard_layout":
        assert await async_get_lovelace_config(hass, None) == {"views": [{"title": "Restored"}]}
    else:
        assert (await _dashboard_registry_snapshot(hass, row["id"]))["dashboard"]["title"] == "Restored"


@pytest.mark.parametrize("family", ["energy", "blueprint", "dashboard_registry"])
async def test_ambiguous_ws_failure_preserves_family_barrier(hass, family):
    from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
    from custom_components.phoenix_mcp.tools.blueprint import _execute_create_blueprint
    from custom_components.phoenix_mcp.tools.lovelace import _recoverable_dashboard_registry_write
    d = runtime()
    effects = []

    async def uncertain(*args):
        effects.append(args)
        raise WsDispatchError("Command timed out after dispatch")

    if family == "energy":
        t = access_key(cap_energy_write="allow")
        prefs = {"energy_sources": [], "device_consumption": [{"name": "Old", "stat_consumption": "external:old"}], "device_consumption_water": []}
        resource = "energy:preferences"
        with patch("custom_components.phoenix_mcp.tools.energy._read_prefs_raw", AsyncMock(return_value=prefs)), patch("custom_components.phoenix_mcp.tools.energy.async_ws_command", AsyncMock(side_effect=uncertain)):
            result = await _execute_edit_energy_config({"op": "remove_device", "device_name": "Old"}, t, hass, d)
    elif family == "blueprint":
        t = access_key(cap_blueprint_write="allow", cap_automation_write="allow")
        resource = "blueprint:automation/test/error.yaml"
        with patch("custom_components.phoenix_mcp.tools.blueprint.async_ws_command", AsyncMock(side_effect=uncertain)):
            result = await _execute_create_blueprint({"domain": "automation", "path": "test/error.yaml", "content": "blueprint:\n  name: Test\n  domain: automation\ntriggers: []\nactions: []\n"}, t, hass, d)
    else:
        t = access_key(cap_lovelace_write="allow")
        resource = "dashboard_registry:test-board"
        before = {"snapshot_type": "dashboard_registry", "schema": 1, "resource_id": "test-board", "exists": False, "dashboard": None}
        with patch("custom_components.phoenix_mcp.tools.lovelace._dashboard_registry_snapshot", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.lovelace.async_ws_command", AsyncMock(side_effect=uncertain)):
            result = await _recoverable_dashboard_registry_write(dashboard_id="test-board", before=before, operation="create", command="lovelace/dashboards/create", payload={}, access_key=t, hass=hass, data=d)
    assert result[1] == "invalid_request"
    assert len(effects) == 1
    assert d.config_recovery.records()[0]["phase"] == "unknown"
    with pytest.raises(ConfigurationRecoveryError):
        await d.config_recovery.begin(resource, "retry", t.id, {})


async def test_template_preview_compares_live_options(hass):
    from homeassistant.config_entries import SOURCE_USER
    from custom_components.phoenix_mcp.template_recovery import template_snapshot
    from custom_components.phoenix_mcp.access_key_store import PermissionNode
    assert await async_setup_component(hass, "homeassistant", {})
    flow = hass.config_entries.flow
    result = await flow.async_init("template", context={"source": SOURCE_USER})
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "sensor"})
    result = await flow.async_configure(result["flow_id"], {"name": "Preview", "state": "{{ 7 }}"})
    await hass.async_block_till_done()
    entry = result["result"]
    snapshot = template_snapshot(hass, entry)
    t, d = access_key(cap_helper_write="allow"), runtime()
    t.permissions = PermissionTree(domains={"sensor": PermissionNode(state="GREEN")})
    rid = await d.config_recovery.begin(f"config_entry:{entry.entry_id}", "edit", t.id, snapshot)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "state": "{{ 8 }}"})
    await d.config_recovery.mark(rid, "verified")
    d.config_recovery.leave(rid)
    result = await _tool_restore_configuration({"recovery_id": rid, "side": "before"}, t, hass, d)
    assert result[1] == "allowed", result
    diff = body(result)["diff"]
    assert json.loads(diff["before"])["options"]["state"] == "{{ 8 }}"
    assert json.loads(diff["after"])["options"]["state"] == "{{ 7 }}"


@pytest.mark.parametrize("op", ["card", "patch"])
async def test_structural_dashboard_writes_return_recovery_id(hass, op):
    from custom_components.phoenix_mcp.tools.lovelace import _execute_add_dashboard_card, _execute_patch_dashboard
    from custom_components.phoenix_mcp.ws_dispatch import async_save_lovelace_config
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    await async_save_lovelace_config(hass, None, {"views": [{"title": "Start", "cards": []}]})
    d, t = runtime(), access_key(cap_lovelace_write="allow")
    if op == "card":
        result = await _execute_add_dashboard_card({"view_index": 0, "card": {"type": "markdown", "content": "Test"}}, t, hass, d)
    else:
        result = await _execute_patch_dashboard({"op": "set", "path": ["views", 0, "title"], "value": "Changed"}, t, hass, d)
    assert result[1] == "allowed", result
    assert body(result)["recovery_id"] == d.config_recovery.records()[0]["id"]
    assert d.config_recovery.records()[0]["phase"] == "verified"


async def test_real_blueprint_restore_accepts_ha_serialization(hass, hass_admin_user):
    from custom_components.phoenix_mcp.tools.blueprint import _blueprint_recovery_snapshot
    assert await async_setup_component(hass, "automation", {})
    d, t = runtime(), access_key(cap_blueprint_write="allow", cap_automation_write="allow")
    content = '# Original formatting\nblueprint:\n  name: "Restored"\n  domain: automation\n  input:\n    entity:\n      name: Target\ntriggers: []\nactions:\n  - action: light.turn_on\n    target:\n      entity_id: !input entity\n'
    path = Path(hass.config.path("blueprints", "automation", "test", "restore.yaml"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace('"Restored"', '"Current"'))
    snapshot = _blueprint_recovery_snapshot("automation", "test/restore.yaml", content)
    rid = await d.config_recovery.begin("blueprint:automation/test/restore.yaml", "write", t.id, snapshot)
    await d.config_recovery.mark(rid, "verified")
    d.config_recovery.leave(rid)
    args = {"recovery_id": rid, "side": "before"}
    receipt = body(await _tool_restore_configuration(args, t, hass, d))
    result = await _tool_restore_configuration({**args, **{key: receipt[key] for key in ("snapshot_digest", "target_fingerprint")}}, t, hass, d)
    assert result[1] == "allowed", result
    assert body(result)["status"] == "verified"
    assert "!input" in path.read_text()
    assert "name: Restored" in path.read_text()
    assert d.config_recovery.records()[0]["source"]["content"] == path.read_text()


async def test_real_ws_timeout_is_never_a_pre_dispatch_refusal(hass, hass_admin_user, monkeypatch):
    from homeassistant.components import websocket_api
    from custom_components.phoenix_mcp import ws_dispatch
    effects = []

    @websocket_api.websocket_command({"type": "test/uncertain"})
    def handler(host, connection, msg):
        effects.append(msg)
        # No reply after the effect, as with a stalled HA command.

    websocket_api.async_register_command(hass, handler)
    monkeypatch.setattr(ws_dispatch, "ALLOWED_WS_COMMANDS", ws_dispatch.ALLOWED_WS_COMMANDS | {"test/uncertain"})
    with pytest.raises(WsDispatchError) as error:
        await async_ws_command(hass, "test/uncertain", {}, timeout=0.001)
    assert len(effects) == 1
    assert not isinstance(error.value, ws_dispatch.WsCommandNotDispatchedError)


async def test_pre_dispatch_refusal_does_not_leave_uncertain_barrier(hass):
    from custom_components.phoenix_mcp.ws_dispatch import WsCommandNotDispatchedError
    d, t = runtime(), access_key(cap_lovelace_write="allow")
    before = {"views": []}
    with patch("custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.lovelace.async_save_lovelace_config", AsyncMock(side_effect=WsCommandNotDispatchedError("Invalid schema"))):
        result = await _recoverable_dashboard_save(url_path=None, before=before, after={"views": [{"title": "Changed"}]}, access_key=t, hass=hass, data=d)
    assert result[1] == "invalid_request"
    assert d.config_recovery.records()[0]["phase"] == "not_applied"
    await d.config_recovery.begin("dashboard_layout:lovelace", "next", t.id, {})


async def test_deleting_a_dashboard_keeps_a_restorable_layout(hass, hass_admin_user):
    """HA deletes the stored layout with the registry row; its cards must not be lost."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    from custom_components.phoenix_mcp.tools.lovelace import _execute_create_dashboard, _execute_delete_dashboard
    from custom_components.phoenix_mcp.ws_dispatch import async_get_lovelace_config
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    created = await _execute_create_dashboard({"config": {"url_path": "audit-cards", "title": "Cards"}}, t, hass, d)
    row = body(created)["dashboard"]
    layout = {"views": [{"title": "Home", "cards": [{"type": "markdown", "content": "keep me"}]}]}
    saved = await _execute_set_dashboard_config({"url_path": "audit-cards", "config": layout}, t, hass, d)
    assert saved[1] == "allowed", saved
    deleted = await _execute_delete_dashboard({"dashboard_id": row["id"]}, t, hass, d)
    assert deleted[1] == "allowed", deleted
    record = d.config_recovery.get(body(deleted)["layout_recovery_id"])
    assert record["phase"] == "verified" and record["before"]["config"] == layout
    recreated = await _execute_create_dashboard({"config": {"url_path": "audit-cards", "title": "Cards"}}, t, hass, d)
    assert recreated[1] == "allowed", recreated
    args = {"recovery_id": body(deleted)["layout_recovery_id"], "side": "before"}
    receipt = await _tool_restore_configuration(args, t, hass, d)
    args.update({k: body(receipt)[k] for k in ("snapshot_digest", "target_fingerprint")})
    restored = await _tool_restore_configuration(args, t, hass, d)
    assert restored[1] == "allowed", restored
    assert await async_get_lovelace_config(hass, "audit-cards") == layout


async def test_dashboard_delete_is_refused_when_its_layout_cannot_be_retained(hass, hass_admin_user):
    from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    from custom_components.phoenix_mcp.tools.lovelace import _execute_create_dashboard, _execute_delete_dashboard
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    created = await _execute_create_dashboard({"config": {"url_path": "audit-kept", "title": "Kept"}}, t, hass, d)
    row = body(created)["dashboard"]
    await _execute_set_dashboard_config({"url_path": "audit-kept", "config": {"views": [{"cards": []}]}}, t, hass, d)
    with patch.object(d.config_recovery, "begin", AsyncMock(side_effect=ConfigurationRecoveryError("store unavailable"))):
        refused = await _execute_delete_dashboard({"dashboard_id": row["id"]}, t, hass, d)
    assert refused[1] == "invalid_request" and "nothing was deleted" in refused[0]["content"][0]["text"]
    rows = await async_ws_command(hass, "lovelace/dashboards/list", {})
    assert any(r["id"] == row["id"] for r in rows)


async def test_admin_energy_version_restore_keeps_a_durable_pre_image(hass):
    from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
    from custom_components.phoenix_mcp.tools.energy import async_restore_energy_prefs
    t, d = access_key(cap_energy_write="allow"), runtime()
    original = {"energy_sources": [], "device_consumption": [{"stat_consumption": "sensor.kettle_energy"}]}
    prefs = json.loads(json.dumps(original))

    async def dispatch(host, command, payload, **kw):
        if command == "energy/get_prefs":
            return json.loads(json.dumps(prefs))
        prefs.update(json.loads(json.dumps(payload)))
        return json.loads(json.dumps(prefs))

    rollback = {"device_consumption": [{"stat_consumption": "sensor.oven_energy"}]}
    with patch("custom_components.phoenix_mcp.tools.energy.async_ws_command", AsyncMock(side_effect=dispatch)):
        result = await async_restore_energy_prefs(rollback, t, hass, d)
        assert result[1] == "allowed", result
        record = d.config_recovery.records()[0]
        assert record["phase"] == "verified"
        assert record["before"]["preferences"]["device_consumption"] == original["device_consumption"]
        saved = dict(prefs)
        with patch.object(d.config_recovery, "begin", AsyncMock(side_effect=ConfigurationRecoveryError("store unavailable"))):
            refused = await async_restore_energy_prefs({"device_consumption": []}, t, hass, d)
    assert refused[1] == "invalid_request" and prefs == saved
