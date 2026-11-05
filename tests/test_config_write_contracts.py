"""What a configuration write reports must match what Home Assistant and the file now hold.

Each test drives the real component or the real file code, because the failure
modes here live in the gap between what was sent and what Home Assistant stored:
normalized values, dropped entries, resolved secrets, and writes that landed
before a later step failed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import yaml_includes
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.config_recovery import (
    SNAPSHOT_UNSAFE_MESSAGE,
    ConfigurationRecoveryError,
    recovery_error_key,
    recovery_error_params,
    safe_snapshot,
)
from custom_components.phoenix_mcp.const import CONFIG_RECOVERY_LAYOUT_MAX_DEPTH
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.esphome_yaml import redact_esphome_text
from custom_components.phoenix_mcp.tools import authoring, helper, lovelace
from custom_components.phoenix_mcp.tools.energy import (
    _apply_op,
    _energy_recovery_snapshot,
    _OpError,
    validate_energy_recovery_scope,
)
from custom_components.phoenix_mcp.tools.recovery import _tool_restore_configuration
from custom_components.phoenix_mcp.ws_dispatch import WsCommandRefusedError, async_ws_command
from tests.runtime_fixtures import patch_readers


@pytest.fixture
def expected_lingering_timers() -> bool:
    """A schedule helper keeps its next-event timer until Home Assistant stops."""
    return True


def _runtime() -> PhoenixData:
    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())
    data.versions = MagicMock()
    data.versions.record = AsyncMock()
    return data


def _key(**fields) -> AccessKeyRecord:
    return AccessKeyRecord(id="k", name="k", access_key_hash="x", created_at=utcnow(), created_by="test",
                           permissions=fields.pop("permissions", PermissionTree()), **fields)


def _body(result) -> dict:
    return json.loads(result[0]["content"][0]["text"])


def _phases(data: PhoenixData) -> list[tuple[str, str]]:
    return [(row["resource"], row["phase"]) for row in data.config_recovery.records()]


# edit_helper verification


NORMALIZED_EDITS = [
    ("timer", {"name": "Tea", "duration": "0:05:00"}, {"duration": "00:10:00"}, {"duration": "0:10:00"}),
    ("timer", {"name": "Tea", "duration": "0:05:00"}, {"duration": 600}, {"duration": "0:10:00"}),
    ("schedule", {"name": "Work", "monday": [{"from": "07:00:00", "to": "08:00:00"}]},
     {"monday": [{"from": "09:00", "to": "10:00"}]},
     {"monday": [{"from": "09:00:00", "to": "10:00:00"}]}),
    ("counter", {"name": "C", "initial": 1, "step": 1}, {"initial": "5"}, {"initial": 5}),
    ("input_number", {"name": "N", "min": 0, "max": 10, "step": 1, "mode": "slider"}, {"max": "20"}, {"max": 20}),
]


@pytest.mark.parametrize(("domain", "create", "edit", "stored"), NORMALIZED_EDITS)
async def test_an_edit_home_assistant_normalized_is_verified(hass, hass_admin_user, domain, create, edit, stored):
    assert await async_setup_component(hass, domain, {})
    await hass.async_block_till_done()
    item = await async_ws_command(hass, f"{domain}/create", create)
    await hass.async_block_till_done()
    key, data = _key(cap_helper_write="allow", pass_through=True), _runtime()
    with patch_readers("custom_components.phoenix_mcp.tools.helper._enforce_helper_mesa_context", return_value=MagicMock()):
        result = await helper._execute_edit_helper(
            {"helper_type": domain, "helper_id": item["id"], "config": edit}, key, hass, data)
        assert not result[0].get("isError"), result
        assert _body(result)["verified"] is True
        assert _phases(data)[0][1] == "verified"
        data.versions.record.assert_awaited()
        # The resource stays editable: an unresolved record would refuse this.
        again = await helper._execute_edit_helper(
            {"helper_type": domain, "helper_id": item["id"], "config": {"name": "Again"}}, key, hass, data)
    assert not again[0].get("isError"), again
    row = next(r for r in await async_ws_command(hass, f"{domain}/list", {}) if r["id"] == item["id"])
    assert stored.items() <= row.items()


async def test_an_edit_the_row_does_not_show_stays_partial(hass, hass_admin_user):
    """Home Assistant saying it stored a value proves nothing unless the row reads the same."""
    assert await async_setup_component(hass, "timer", {})
    await hass.async_block_till_done()
    item = await async_ws_command(hass, "timer/create", {"name": "Tea", "duration": "0:05:00"})
    await hass.async_block_till_done()
    key, data = _key(cap_helper_write="allow", pass_through=True), _runtime()
    real = helper.async_ws_command

    async def lost_update(hass_, command, payload, **kwargs):
        if command == "timer/update":
            return {**item, "duration": "0:10:00"}
        return await real(hass_, command, payload, **kwargs)

    with (patch_readers("custom_components.phoenix_mcp.tools.helper._enforce_helper_mesa_context", return_value=MagicMock()),
          patch.object(helper, "async_ws_command", lost_update)):
        result = await helper._execute_edit_helper(
            {"helper_type": "timer", "helper_id": item["id"], "config": {"duration": "00:10:00"}}, key, hass, data)
    assert result[0].get("isError")
    assert _body(result)["status"] == "partial"


# Scene writes


@pytest.mark.parametrize(("config", "field"), [
    ({"name": "x", "description": "d", "entities": {"light.a": "on"}}, "config.description is not supported"),
    ({"name": "x", "entities": {"light.a": {"state": 5}}}, "config.entities has an invalid value"),
    ({"name": "x", "entities": {"not an entity": "on"}}, "config.entities has an invalid value"),
])
async def test_a_scene_home_assistant_would_drop_is_refused_before_any_approval(hass, config, field):
    hass.states.async_set("light.a", "on")
    key, data = _key(cap_scene_write="allow", pass_through=True), _runtime()
    refused = authoring._scene_write_precheck({"config": config}, "create_scene", require_id=False)
    assert refused is not None and refused[1] == "invalid_request"
    assert field in refused[0]["content"][0]["text"]
    written = await authoring._scene_write(
        config, key, hass, data, tool_name="create_scene", scene_id="s1", replace=False)
    assert written[1] == "invalid_request"
    assert not Path(hass.config.path("scenes.yaml")).exists()


async def test_scene_refusal_discloses_no_value(hass):
    config = {"name": "x", "entities": {"light.a": {"state": 5}}, "s3cr3t_field": "s3cr3tvalue"}
    refused = authoring._scene_write_precheck({"config": config}, "create_scene", require_id=False)
    assert "s3cr3t" not in refused[0]["content"][0]["text"]


async def test_a_scene_home_assistant_loads_still_writes(hass, immediate_authoring_observation):
    Path(hass.config.path("configuration.yaml")).write_text("scene: !include scenes.yaml\n")
    Path(hass.config.path("scenes.yaml")).write_text("[]\n")
    hass.states.async_set("light.a", "on")
    hass.services.async_register("scene", "reload", lambda call: None)
    key, data = _key(cap_scene_write="allow", pass_through=True), _runtime()
    config = {"id": "ignored", "name": "x", "icon": "mdi:sofa", "metadata": {},
              "entities": {"light.a": {"state": "on", "brightness": 10}}}
    written = await authoring._scene_write(
        config, key, hass, data, tool_name="create_scene", scene_id="s1", replace=False)
    assert not written[0].get("isError"), written
    assert yaml.safe_load(Path(hass.config.path("scenes.yaml")).read_text())[0]["id"] == "s1"


# Energy


@pytest.mark.parametrize("source_type", ["solar", "battery"])
async def test_set_source_refuses_when_several_sources_share_the_type(hass, source_type):
    hass.states.async_set("sensor.new", "1")
    key = _key(cap_energy_write="allow", pass_through=True)
    prefs = {"energy_sources": [
        {"type": source_type, "stat_energy_from": "sensor.east", "stat_energy_to": "sensor.east_in"},
        {"type": source_type, "stat_energy_from": "sensor.west", "stat_energy_to": "sensor.west_in"},
    ], "device_consumption": []}
    with pytest.raises(_OpError, match="ambiguous"):
        _apply_op(prefs, {"op": "set_source", "source_type": source_type, "stat_energy_from": "sensor.new"}, key, hass)


async def test_replace_statistic_reaches_every_field_of_a_source(hass):
    for entity_id in ("sensor.old", "sensor.new"):
        hass.states.async_set(entity_id, "1")
    key = _key(cap_energy_write="allow", pass_through=True)
    prefs = {"energy_sources": [{
        "type": "battery", "stat_energy_from": "sensor.out", "stat_energy_to": "sensor.in",
        "stat_soc": "sensor.old",
        "power_config": {"stat_rate": "sensor.old", "stat_rate_from": "sensor.old"},
    }], "device_consumption": [{"stat_consumption": "sensor.other", "name": "sensor.old"}]}
    new, summary = _apply_op(prefs, {"op": "replace_statistic", "statistic": "sensor.old", "new_statistic": "sensor.new"}, key, hass)
    source = new["energy_sources"][0]
    assert source["stat_soc"] == "sensor.new"
    assert source["power_config"] == {"stat_rate": "sensor.new", "stat_rate_from": "sensor.new"}
    assert new["device_consumption"][0]["name"] == "sensor.old", "a label is not a statistic"
    assert summary["op"] == "replace_statistic"


async def test_a_statistic_used_only_by_stat_soc_can_be_replaced(hass):
    hass.states.async_set("sensor.new", "1")
    key = _key(cap_energy_write="allow", pass_through=True)
    prefs = {"energy_sources": [{"type": "battery", "stat_energy_from": "sensor.out",
                                 "stat_energy_to": "sensor.in", "stat_soc": "sensor.old"}],
             "device_consumption": []}
    new, _ = _apply_op(prefs, {"op": "replace_statistic", "statistic": "sensor.old", "new_statistic": "sensor.new"}, key, hass)
    assert new["energy_sources"][0]["stat_soc"] == "sensor.new"


async def test_energy_recovery_admits_a_dead_statistic_but_not_an_out_of_scope_entity(hass):
    """Undoing a repair names a dead id, which no key is scoped to; a live one stays checked."""
    hass.states.async_set("sensor.hidden", "1")
    hass.states.async_set("sensor.mine", "1")
    scoped = _key(cap_energy_write="allow", permissions=PermissionTree(
        entities={"sensor.mine": PermissionNode(state="GREEN")}))
    dead = _energy_recovery_snapshot({"energy_sources": [], "device_consumption": [
        {"stat_consumption": "sensor.dead"}, {"stat_consumption": "sensor.mine"}]})
    validate_energy_recovery_scope(dead, scoped, hass)
    hidden = _energy_recovery_snapshot({"energy_sources": [], "device_consumption": [
        {"stat_consumption": "sensor.hidden"}]})
    with pytest.raises(ConfigurationRecoveryError, match="outside this access key's current scope"):
        validate_energy_recovery_scope(hidden, scoped, hass)


# YAML-mode dashboards


@pytest.fixture
async def yaml_dashboard(hass, hass_admin_user):
    Path(hass.config.path("secrets.yaml")).write_text("cam_url: https://cam.example/stream/s3cr3tvalue\n")
    Path(hass.config.path("ui-lovelace.yaml")).write_text(
        "title: T\nviews:\n  - title: V\n    cards:\n      - type: picture\n        image: !secret cam_url\n"
    )
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "yaml"}})


async def test_a_yaml_dashboard_write_stores_no_resolved_secret(hass, yaml_dashboard):
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    config = {"views": []}
    result = await lovelace._execute_set_dashboard_config({"config": config}, key, hass, data)
    assert result[1] == "invalid_request" and "YAML-mode" in result[0]["content"][0]["text"]
    assert data.config_recovery.records() == []
    diff = await lovelace._build_diff_set_dashboard_config({"config": config}, key, hass)
    assert "s3cr3tvalue" not in json.dumps(diff, default=str)


async def test_card_and_patch_writes_refuse_a_yaml_dashboard_before_any_snapshot(hass, yaml_dashboard):
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    refused = await lovelace._dashboard_card_current({}, hass, "delete_dashboard_card")
    assert isinstance(refused, tuple) and "YAML-mode" in refused[0]["content"][0]["text"]
    args = {"view_index": 0, "card_index": 0}
    diff = await lovelace._build_diff_dashboard_card(args, "delete", key, hass)
    assert "s3cr3tvalue" not in json.dumps(diff, default=str)
    changes = {"changes": [{"op": "remove", "path": ["views", 0, "cards", 0]}]}
    diff = await lovelace._build_diff_patch_dashboard(changes, key, hass)
    assert "s3cr3tvalue" not in json.dumps(diff, default=str)
    assert data.config_recovery.records() == []


async def test_restore_refuses_a_target_that_became_yaml_mode(hass, yaml_dashboard):
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    snapshot = lovelace._dashboard_layout_snapshot(None, {"views": []})
    rid = await data.config_recovery.begin("dashboard_layout:lovelace", "dashboard_layout_write", key.id, snapshot)
    await data.config_recovery.mark(rid, "verified")
    data.config_recovery.leave(rid)
    result = await _tool_restore_configuration({"recovery_id": rid, "side": "before"}, key, hass, data)
    assert result[0].get("isError")
    assert "s3cr3tvalue" not in json.dumps(result[0])


# Dashboard registry addressing


@pytest.fixture
async def board(hass, hass_admin_user):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    return await async_ws_command(hass, "lovelace/dashboards/create", {"url_path": "audit-board", "title": "Before"})


@pytest.mark.parametrize("address", ["audit-board", "audit_board"])
async def test_edit_dashboard_works_by_either_name(hass, board, address):
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    result = await lovelace._execute_edit_dashboard(
        {"dashboard_id": address, "config": {"title": "After"}}, key, hass, data)
    assert _body(result)["status"] == "verified", result
    assert _phases(data) == [(f"dashboard_registry:{board['id']}", "verified")]
    rows = await async_ws_command(hass, "lovelace/dashboards/list", {})
    assert rows[0]["title"] == "After"


async def test_an_edit_by_url_path_can_be_restored(hass, board):
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    edited = await lovelace._execute_edit_dashboard(
        {"dashboard_id": "audit-board", "config": {"title": "After"}}, key, hass, data)
    args = {"recovery_id": _body(edited)["recovery_id"], "side": "before"}
    receipt = _body(await _tool_restore_configuration(args, key, hass, data))
    args.update({name: receipt[name] for name in ("snapshot_digest", "target_fingerprint")})
    restored = await _tool_restore_configuration(args, key, hass, data)
    assert not restored[0].get("isError"), restored
    rows = await async_ws_command(hass, "lovelace/dashboards/list", {})
    assert rows[0]["title"] == "Before"


async def test_delete_dashboard_works_by_url_path(hass, board):
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    result = await lovelace._execute_delete_dashboard({"dashboard_id": "audit-board"}, key, hass, data)
    assert _body(result)["status"] == "verified", result
    assert await async_ws_command(hass, "lovelace/dashboards/list", {}) == []


@pytest.mark.parametrize("code", ["invalid_format", "not_found"])
def test_core_refusals_that_prove_nothing_changed(code):
    assert lovelace.dashboard_registry_refused(WsCommandRefusedError(code, "refused"))


def test_any_other_core_failure_may_have_landed():
    assert not lovelace.dashboard_registry_refused(WsCommandRefusedError("unknown_error", "failed"))


async def test_a_row_gone_at_dispatch_leaves_no_barrier(hass, board):
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    real = lovelace.async_ws_command

    async def vanished(hass_, command, payload, **kwargs):
        if command == "lovelace/dashboards/update":
            raise WsCommandRefusedError("not_found", "Unable to find dashboard_id")
        return await real(hass_, command, payload, **kwargs)

    with patch.object(lovelace, "async_ws_command", vanished):
        result = await lovelace._execute_edit_dashboard(
            {"dashboard_id": board["id"], "config": {"title": "After"}}, key, hass, data)
    assert result[0].get("isError")
    assert _phases(data)[0][1] == "not_applied"
    again = await lovelace._execute_edit_dashboard(
        {"dashboard_id": board["id"], "config": {"title": "After"}}, key, hass, data)
    assert _body(again)["status"] == "verified", again


# Recovery snapshot admission


def _layout(card) -> dict:
    return {"views": [{"cards": [{"type": "custom:x"}, card]}]}


@pytest.mark.parametrize("card", [
    {"type": "custom:x", "session": "abc"},
    {"type": "custom:x", "community": "public"},
    {"type": "custom:x", "max_tokens": 5},
    {"type": "custom:x", "show_password": True},
    {"type": "custom:x", "token": ""},
    {"type": "markdown", "content": "The <redacted> marker"},
])
def test_ordinary_layout_content_is_admitted(card):
    assert lovelace._dashboard_layout_snapshot("x-y", _layout(card))["config"]["views"][0]["cards"][1] == card


@pytest.mark.parametrize(("card", "location"), [
    ({"type": "custom:x", "password": "hunter2"}, "password"),
    ({"type": "custom:x", "password": 123456}, "password"),
    ({"type": "custom:x", "session_token": "abc"}, "session_token"),
    ({"type": "custom:x", "auth_tokens": ["abc"]}, "auth_tokens"),
    ({"type": "custom:x", "api_key": "abc"}, "api_key"),
    ({"type": "iframe", "url": "https://user:pw@example.com/"}, "url"),
    ({"type": "iframe", "url": "https://example.com/?token=abc"}, "url"),
    ({"type": "custom:x", "session": "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl"}, "session"),
    ({"type": "custom:x", "value": "<redacted>"}, "value"),
    ({"type": "custom:x", "value": "<phoenix-token>"}, "value"),
])
def test_a_layout_with_a_real_secret_is_refused_at_its_location(card, location):
    with pytest.raises(ConfigurationRecoveryError) as error:
        lovelace._dashboard_layout_snapshot("x-y", _layout(card))
    message = str(error.value)
    assert message.startswith(SNAPSHOT_UNSAFE_MESSAGE)
    assert f"config.views[0].cards[1].{location}" in message
    assert recovery_error_key(message) == "recoverySnapshotUnsafeAt"
    assert recovery_error_params(message) == {"location": f"config.views[0].cards[1].{location}"}
    for value in ("hunter2", "123456", "user:pw", "eyJhbGciOi"):
        assert value not in message


def test_a_refusal_does_not_name_a_key_that_could_be_an_entity_id():
    layout = {"views": [{"states": {"person.hidden_guest": {"password": "x"}}}]}
    with pytest.raises(ConfigurationRecoveryError) as error:
        lovelace._dashboard_layout_snapshot("x-y", layout)
    assert "hidden_guest" not in str(error.value)
    assert "config.views[0].states[key].password" in str(error.value)


def test_a_layout_as_deep_as_a_scrubbed_read_is_admitted_and_past_it_is_refused():
    def nested(levels: int) -> dict:
        node: dict = {"type": "tile", "entity": "light.x"}
        for _ in range(levels):
            node = {"type": "vertical-stack", "cards": [node]}
        return {"views": [{"sections": [{"cards": [node]}]}]}

    assert lovelace._dashboard_layout_snapshot("x-y", nested(40))
    with pytest.raises(ConfigurationRecoveryError) as error:
        lovelace._dashboard_layout_snapshot("x-y", nested(CONFIG_RECOVERY_LAYOUT_MAX_DEPTH))
    assert "nested too deeply" in str(error.value)


@pytest.mark.parametrize("value", [
    {"session": "abc"}, {"community": "public"}, {"max_tokens": 5}, {"text": "The <redacted> marker"},
])
def test_other_snapshots_keep_the_plain_key_name_rule(value):
    with pytest.raises(ConfigurationRecoveryError):
        safe_snapshot({"snapshot_type": "storage_helper", "config": value})


def test_other_snapshots_keep_their_depth_limit():
    node: dict = {"leaf": 1}
    for _ in range(30):
        node = {"next": node}
    with pytest.raises(ConfigurationRecoveryError):
        safe_snapshot({"snapshot_type": "storage_helper", "config": node})


# YAML file writes


MULTILINE = "alpha: null\nbeta"


async def test_create_keeps_a_string_with_a_line_ending_in_null(hass):
    config = {"sequence": [{"action": "notify.x", "data": {"message": MULTILINE, "unset": None}}]}
    Path(hass.config.path("configuration.yaml")).write_text("script: !include scripts.yaml\n")
    Path(hass.config.path("scripts.yaml")).write_text("")
    assert yaml_includes.perform_create(hass.config.config_dir, "script", "demo", config).ok
    loaded = yaml.safe_load(Path(hass.config.path("scripts.yaml")).read_text())
    assert loaded["demo"] == config


async def test_edit_keeps_a_string_with_a_line_ending_in_null(hass):
    config = {"sequence": [{"action": "notify.x", "data": {"message": MULTILINE}}]}
    Path(hass.config.path("configuration.yaml")).write_text("script: !include scripts.yaml\n")
    Path(hass.config.path("scripts.yaml")).write_text("demo:\n  sequence: []\n")
    assert yaml_includes.perform_edit(hass.config.config_dir, "script", "demo", config).ok
    assert yaml.safe_load(Path(hass.config.path("scripts.yaml")).read_text())["demo"] == config


@pytest.mark.parametrize("shape", ["list", "named"])
def test_an_append_that_would_not_read_back_is_refused(tmp_path, shape):
    path = tmp_path / "entries.yaml"
    config = {"id": "a", "note": "text"}
    with patch.object(yaml_includes, "dump_tagged", return_value=(
        "- id: a\n  note: other\n" if shape == "list" else "a:\n  id: a\n  note: other\n"
    )):
        result = yaml_includes._create_append(str(path), shape, "a", config)
    assert not result.ok and not path.exists()


def test_dump_writes_a_null_mapping_value_as_an_empty_value():
    text = yaml_includes.dump_tagged({"a": None, "b": "x: null\ny", "c": [None], "d": {"e": None}})
    assert "a:\n" in text and "e:\n" in text
    assert yaml.safe_load(text) == {"a": None, "b": "x: null\ny", "c": [None], "d": {"e": None}}


def test_dump_of_a_bare_null_stays_a_valid_document():
    assert yaml.safe_load(yaml_includes.dump_tagged(None)) is None


# Deletes whose reload fails


@pytest.mark.parametrize(("domain", "file", "text", "execute", "id_field"), [
    ("automation", "automations.yaml", "- id: 'a1'\n  alias: A\n  triggers: []\n  actions: []\n",
     authoring._execute_delete_automation, "automation_id"),
    ("script", "scripts.yaml", "a1:\n  sequence: []\n", authoring._execute_delete_script, "script_id"),
    ("scene", "scenes.yaml", "- id: 'a1'\n  name: A\n  entities:\n    light.a: 'on'\n",
     authoring._execute_delete_scene, "scene_id"),
])
async def test_a_delete_keeps_its_version_when_the_reload_fails(hass, domain, file, text, execute, id_field):
    """The version is the only restore source for a deleted entry, and a retry would find nothing."""
    Path(hass.config.path("configuration.yaml")).write_text(f"{domain}: !include {file}\n")
    Path(hass.config.path(file)).write_text(text)
    hass.states.async_set("light.a", "on")
    key = _key(cap_automation_write="allow", cap_script_write="allow", cap_scene_write="allow", pass_through=True)
    data = _runtime()
    args, _ = await authoring._bind_delete_args({id_field: "a1"}, hass, domain, "a1")
    # No reload service is registered, so the reload raises after the file write.
    result = await execute(args, key, hass, data)
    assert result[0].get("isError") and result[1] == "invalid_request"
    body = _body(result)
    assert body == {**body, "deleted": True, "verified": False, "reload_status": "failed", id_field: "a1"}
    assert "do not repeat the delete" in body["message"]
    data.versions.record.assert_awaited_once()
    assert "a1" not in Path(hass.config.path(file)).read_text()


async def test_a_delete_removes_only_the_home_assistant_scene_registry_entry(hass, immediate_authoring_observation):
    Path(hass.config.path("configuration.yaml")).write_text("scene: !include scenes.yaml\n")
    Path(hass.config.path("scenes.yaml")).write_text("- id: 'a1'\n  name: A\n  entities:\n    light.a: 'on'\n")
    hass.states.async_set("light.a", "on")
    hass.services.async_register("scene", "reload", lambda call: None)
    registry = er.async_get(hass)
    own = registry.async_get_or_create("scene", "homeassistant", "a1")
    other = registry.async_get_or_create("scene", "hue", "a1")
    key, data = _key(cap_scene_write="allow", pass_through=True), _runtime()
    args, _ = await authoring._bind_delete_args({"scene_id": "a1"}, hass, "scene", "a1")
    result = await authoring._execute_delete_scene(args, key, hass, data)
    assert not result[0].get("isError"), result
    assert registry.async_get(own.entity_id) is None
    assert registry.async_get(other.entity_id) is not None


async def test_deleting_a_junk_card_entry_reports_its_save(hass, hass_admin_user):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    key, data = _key(cap_lovelace_write="allow"), _runtime()
    layout = {"views": [{"title": "V", "cards": ["junk", {"type": "tile"}]}]}
    await lovelace._execute_set_dashboard_config({"config": layout}, key, hass, data)
    result = await lovelace._execute_delete_dashboard_card({"view_index": 0, "card_index": 0}, key, hass, data)
    assert not result[0].get("isError"), result


# ESPHome masking


DEVICE_YAML = """
wifi:
  ssid: home
  password: hunter2hunter2
wireguard:
  address: 10.0.0.2
  private_key: PRIVATEKEYVALUE=
  peer_public_key: PEERPUBLICVALUE=
  peer_preshared_key: PRESHAREDVALUE=
on_boot:
  - http_request.post:
      url: https://api.example.com
      headers:
        Authorization: "Bearer REALBEARERTOKEN"
        X-Api-Key: "HEADERAPIKEY"
        X-Custom: "UNLISTEDHEADER"
packet_transport:
  - platform: udp
    encryption:
      key: "PACKETTRANSPORTKEY"
substitutions:
  wifi_pw: SUBSTPASSWORD
  admin_pass: ADMINPASS
  bypass_mode: KEEPTHISBYPASS
  compass: KEEPTHISCOMPASS
"""


@pytest.mark.parametrize("secret", [
    "hunter2hunter2", "PRIVATEKEYVALUE=", "PRESHAREDVALUE=", "REALBEARERTOKEN", "HEADERAPIKEY", "UNLISTEDHEADER",
    "PACKETTRANSPORTKEY", "SUBSTPASSWORD", "ADMINPASS",
])
def test_inline_device_credentials_are_masked(secret):
    masked, _ = redact_esphome_text(DEVICE_YAML)
    assert secret not in masked


@pytest.mark.parametrize("kept", ["PEERPUBLICVALUE=", "KEEPTHISBYPASS", "KEEPTHISCOMPASS", "api.example.com"])
def test_ordinary_device_values_stay_readable(kept):
    masked, _ = redact_esphome_text(DEVICE_YAML)
    assert kept in masked


# Blueprint restore


COMMENTED_BLUEPRINT = (
    "# careful notes\nblueprint:\n  name: B  # inline\n  domain: automation\n  input: {}\ntriggers: []\nactions: []\n"
)


async def test_blueprint_restore_review_shows_what_will_be_stored(hass, hass_admin_user):
    from custom_components.phoenix_mcp.tools.blueprint import _blueprint_recovery_snapshot, _canonical_blueprint_source
    from custom_components.phoenix_mcp.tools.recovery import _BLUEPRINT_RERENDERED_NOTE

    assert await async_setup_component(hass, "automation", {})
    key, data = _key(cap_blueprint_write="allow", cap_automation_write="allow"), _runtime()
    snapshot = _blueprint_recovery_snapshot("automation", "a/b.yaml", COMMENTED_BLUEPRINT)
    rid = await data.config_recovery.begin("blueprint:automation/a/b.yaml", "edit_blueprint", key.id, snapshot)
    await data.config_recovery.mark(rid, "verified")
    data.config_recovery.leave(rid)
    receipt = _body(await _tool_restore_configuration({"recovery_id": rid, "side": "before"}, key, hass, data))
    assert receipt["note"] == _BLUEPRINT_RERENDERED_NOTE
    assert receipt["diff"]["after"] == _canonical_blueprint_source("automation", COMMENTED_BLUEPRINT)
    assert "careful notes" not in receipt["diff"]["after"]


async def test_blueprint_restore_of_rendered_text_carries_no_note(hass, hass_admin_user):
    from custom_components.phoenix_mcp.tools.blueprint import _blueprint_recovery_snapshot, _canonical_blueprint_source

    assert await async_setup_component(hass, "automation", {})
    key, data = _key(cap_blueprint_write="allow", cap_automation_write="allow"), _runtime()
    rendered = _canonical_blueprint_source("automation", COMMENTED_BLUEPRINT)
    snapshot = _blueprint_recovery_snapshot("automation", "a/b.yaml", rendered)
    rid = await data.config_recovery.begin("blueprint:automation/a/b.yaml", "edit_blueprint", key.id, snapshot)
    await data.config_recovery.mark(rid, "verified")
    data.config_recovery.leave(rid)
    receipt = _body(await _tool_restore_configuration({"recovery_id": rid, "side": "before"}, key, hass, data))
    assert "note" not in receipt
