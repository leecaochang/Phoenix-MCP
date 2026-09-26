"""Recovery regressions exercised through real HA APIs and uncertain effects."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.token_store import TokenRecord, PermissionTree
from custom_components.phoenix_mcp.tools.recovery import _tool_restore_configuration
from custom_components.phoenix_mcp.tools.lovelace import _execute_set_dashboard_config, _recoverable_dashboard_save
from custom_components.phoenix_mcp.tools.config_files import _authored_file_snapshot
from custom_components.phoenix_mcp.tools.energy import _execute_edit_energy_config
from custom_components.phoenix_mcp.ws_dispatch import async_ws_command, WsDispatchError


def runtime():
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())


def token(**caps):
    return TokenRecord(id="audit", name="audit", token_hash="x", created_at=utcnow(), created_by="test", permissions=PermissionTree(), **caps)


def body(result):
    return json.loads(result[0]["content"][0]["text"])


async def test_real_dashboard_registry_restore_updates_only_supported_fields(hass, hass_admin_user):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    from custom_components.phoenix_mcp.tools.lovelace import _execute_create_dashboard, _execute_edit_dashboard
    t, d = token(cap_lovelace_write="allow"), runtime()
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


async def test_real_dashboard_layout_write_returns_recovery_id(hass):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    d = runtime()
    result = await _execute_set_dashboard_config({"config": {"views": []}}, token(cap_lovelace_write="allow"), hass, d)
    assert result[1] == "allowed", result
    assert d.config_recovery.records()[0]["phase"] == "verified"
    assert body(result)["recovery_id"] == d.config_recovery.records()[0]["id"]


async def test_file_restore_preview_compares_live_content(hass):
    d, t = runtime(), token(cap_filesystem="allow")
    rel = "www/audit.txt"
    p = Path(hass.config.path(rel))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("After")
    snapshot = _authored_file_snapshot(rel, True, "Before")
    rid = await d.config_recovery.begin(f"authored_file:{rel}", "write_file", t.id, snapshot)
    await d.config_recovery.mark(rid, "verified")
    result = await _tool_restore_configuration({"recovery_id": rid, "side": "before"}, t, hass, d)
    diff = body(result)["diff"]
    assert diff["before"] == "After"
    assert diff["after"] == "Before"
    assert p.read_text() == "After"


async def test_timeout_after_dashboard_effect_keeps_barrier(hass):
    d, t = runtime(), token(cap_lovelace_write="allow")
    before, after = {"views": []}, {"views": [{"title": "Changed"}]}
    actual = dict(before)
    async def save(*args):
        actual.clear()
        actual.update(after)
        raise WsDispatchError("WebSocket command timed out.")
    with patch("custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.lovelace.async_save_lovelace_config", AsyncMock(side_effect=save)):
        result = await _recoverable_dashboard_save(url_path=None, before=before, after=after, token=t, hass=hass, data=d)
    assert actual == after
    assert result[1] == "invalid_request"
    assert d.config_recovery.records()[0]["phase"] == "unknown"
    from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
    with pytest.raises(ConfigurationRecoveryError):
        await d.config_recovery.begin("dashboard_layout:lovelace", "retry", t.id, {})


async def test_energy_partial_readback_returns_observed_state(hass):
    d, t = runtime(), token(cap_energy_write="allow")
    before = {"energy_sources": [], "device_consumption": [{"stat_consumption": "sensor.old", "name": "Old"}], "device_consumption_water": []}
    with patch("custom_components.phoenix_mcp.tools.energy._read_prefs_raw", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.energy.async_ws_command", AsyncMock(return_value=None)):
        result = await _execute_edit_energy_config({"op": "remove_device", "device_name": "Old"}, t, hass, d)
    assert d.config_recovery.records()[0]["phase"] == "partial"
    assert result[1] == "allowed"
    assert body(result)["saved"] is False
    assert body(result)["status"] == "partial"
    assert body(result)["device_consumption"][0]["name"] == "Old"
    assert not d.versions.list_recent()


async def test_admin_restore_button_restores_another_tokens_file(hass, hass_admin_user):
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
    t = token(cap_blueprint_write="allow", cap_automation_write="allow")
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
    from custom_components.phoenix_mcp.token_store import PermissionNode
    assert await async_setup_component(hass, "homeassistant", {})
    flow = hass.config_entries.flow
    result = await flow.async_init("template", context={"source": SOURCE_USER})
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "sensor"})
    result = await flow.async_configure(result["flow_id"], {"name": "Audit restore", "state": "{{ 7 }}"})
    await hass.async_block_till_done()
    entry = result["result"]
    saved = template_snapshot(hass, entry)
    t, d = token(cap_helper_write="allow"), runtime()
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
    rid = await d.config_recovery.begin("fixture", "write", "another-token", snapshot)
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
        t = token(cap_energy_write="allow")
        prefs = {"energy_sources": [], "device_consumption": [{"name": "Old", "stat_consumption": "external:old"}], "device_consumption_water": []}
        resource = "energy:preferences"
        with patch("custom_components.phoenix_mcp.tools.energy._read_prefs_raw", AsyncMock(return_value=prefs)), patch("custom_components.phoenix_mcp.tools.energy.async_ws_command", AsyncMock(side_effect=uncertain)):
            result = await _execute_edit_energy_config({"op": "remove_device", "device_name": "Old"}, t, hass, d)
    elif family == "blueprint":
        t = token(cap_blueprint_write="allow", cap_automation_write="allow")
        resource = "blueprint:automation/test/error.yaml"
        with patch("custom_components.phoenix_mcp.tools.blueprint.async_ws_command", AsyncMock(side_effect=uncertain)):
            result = await _execute_create_blueprint({"domain": "automation", "path": "test/error.yaml", "content": "blueprint:\n  name: Test\n  domain: automation\ntriggers: []\nactions: []\n"}, t, hass, d)
    else:
        t = token(cap_lovelace_write="allow")
        resource = "dashboard_registry:test-board"
        before = {"snapshot_type": "dashboard_registry", "schema": 1, "resource_id": "test-board", "exists": False, "dashboard": None}
        with patch("custom_components.phoenix_mcp.tools.lovelace._dashboard_registry_snapshot", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.lovelace.async_ws_command", AsyncMock(side_effect=uncertain)):
            result = await _recoverable_dashboard_registry_write(dashboard_id="test-board", before=before, operation="create", command="lovelace/dashboards/create", payload={}, token=t, hass=hass, data=d)
    assert result[1] == "invalid_request"
    assert len(effects) == 1
    assert d.config_recovery.records()[0]["phase"] == "unknown"
    with pytest.raises(ConfigurationRecoveryError):
        await d.config_recovery.begin(resource, "retry", t.id, {})


async def test_template_preview_compares_live_options(hass):
    from homeassistant.config_entries import SOURCE_USER
    from custom_components.phoenix_mcp.template_recovery import template_snapshot
    from custom_components.phoenix_mcp.token_store import PermissionNode
    assert await async_setup_component(hass, "homeassistant", {})
    flow = hass.config_entries.flow
    result = await flow.async_init("template", context={"source": SOURCE_USER})
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "sensor"})
    result = await flow.async_configure(result["flow_id"], {"name": "Preview", "state": "{{ 7 }}"})
    await hass.async_block_till_done()
    entry = result["result"]
    snapshot = template_snapshot(hass, entry)
    t, d = token(cap_helper_write="allow"), runtime()
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
    d, t = runtime(), token(cap_lovelace_write="allow")
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
    d, t = runtime(), token(cap_blueprint_write="allow", cap_automation_write="allow")
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
    d, t = runtime(), token(cap_lovelace_write="allow")
    before = {"views": []}
    with patch("custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config", AsyncMock(return_value=before)), patch("custom_components.phoenix_mcp.tools.lovelace.async_save_lovelace_config", AsyncMock(side_effect=WsCommandNotDispatchedError("Invalid schema"))):
        result = await _recoverable_dashboard_save(url_path=None, before=before, after={"views": [{"title": "Changed"}]}, token=t, hass=hass, data=d)
    assert result[1] == "invalid_request"
    assert d.config_recovery.records()[0]["phase"] == "not_applied"
    await d.config_recovery.begin("dashboard_layout:lovelace", "next", t.id, {})
