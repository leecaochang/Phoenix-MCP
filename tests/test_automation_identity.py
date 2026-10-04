"""Automation identity guards at preflight and the serialized commit boundary."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.mcp_view import _call_tool, async_execute_approved_tool, async_restore_version
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord
from custom_components.phoenix_mcp.tools import authoring
from custom_components.phoenix_mcp.version_store import VersionStore


def config(alias="Original"):
    """Return a harmless HA-valid automation without shared mutable input."""
    return {"alias": alias, "triggers": [], "actions": [{"delay": {"seconds": 1}}]}


@pytest.fixture
async def identity_env(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    reload = MagicMock()
    hass.services.async_register("automation", "reload", reload)
    access_key = AccessKeyRecord(id="identity", name="identity", token_hash="x", created_at=utcnow(), created_by="u", cap_automation_write="allow")
    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())
    return access_key, data, reload


def seed(path, alias="Original"):
    """Save a known current automation and return its tag-encoded hash."""
    current = dict(config(alias), id="chosen")
    path.write_text(yi.dump_tagged([current]))
    return content_hash(current)


@pytest.mark.parametrize("layout", ["fallback", "plain", "merge_list", "dir_list", "nested"])
@pytest.mark.parametrize("alias", ["Original", "Different"])
def test_duplicate_create_preserves_every_file(tmp_path, layout, alias):
    if layout == "fallback":
        target = tmp_path / "automations.yaml"
    elif layout == "plain":
        (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
        target = tmp_path / "automations.yaml"
    elif layout == "nested":
        (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
        (tmp_path / "automations.yaml").write_text("- !include actual.yaml\n")
        target = tmp_path / "actual.yaml"
    else:
        tag = "!include_dir_merge_list" if layout == "merge_list" else "!include_dir_list"
        (tmp_path / "configuration.yaml").write_text(f"automation: {tag} automations\n")
        (tmp_path / "automations").mkdir()
        target = tmp_path / "automations" / "unrelated_filename.yaml"
    old = dict(config(), id="chosen")
    target.write_text(yi.dump_tagged(old if layout in ("dir_list", "nested") else [old]))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
    result = yi.automation_write(str(tmp_path), "chosen", config(alias), True)
    assert result.error_kind == "already_exists"
    assert 'Existing alias: "Original"' in result.error_text
    assert f'Proposed alias: "{alias}"' in result.error_text
    assert "get_automation" in result.error_text and "edit_automation" in result.error_text
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")} == before


@pytest.mark.parametrize("fallback", [True, False])
@pytest.mark.parametrize("alias", ["Original", "Changed", None])
@pytest.mark.parametrize("hash_mode", ["missing", "stale", "current"])
def test_alias_and_hash_matrix(tmp_path, fallback, alias, hash_mode):
    if not fallback:
        (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    target = tmp_path / "automations.yaml"
    current_hash = seed(target)
    before = target.read_bytes()
    proposed = config(alias)
    if alias is None:
        proposed.pop("alias")
    expected = {"missing": None, "stale": "stale", "current": current_hash}[hash_mode]
    result = yi.automation_write(str(tmp_path), "chosen", dict(proposed, id="chosen"), False, expected)
    success = hash_mode == "current" or (hash_mode == "missing" and alias == "Original")
    assert result.ok == success
    if not success:
        assert target.read_bytes() == before
    else:
        assert yi.read_entry(str(tmp_path), "automation", "chosen") == dict(proposed, id="chosen")


@pytest.mark.parametrize("fallback", [True, False])
def test_preexisting_duplicates_refuse_edit_and_create(tmp_path, fallback):
    if not fallback:
        (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    target = tmp_path / "automations.yaml"
    current = dict(config(), id="chosen")
    target.write_text(yi.dump_tagged([current, dict(current, alias="Duplicate")]))
    before = target.read_bytes()
    for create in (True, False):
        result = yi.automation_write(str(tmp_path), "chosen", config(), create, content_hash(current))
        assert result.error_kind == "duplicate"
        assert target.read_bytes() == before


@pytest.mark.parametrize("extra", ["automation manual: [{id: inline}]\n", "homeassistant:\n  packages: {}\n"])
def test_custom_id_requires_proven_absence(tmp_path, extra):
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n" + extra)
    result = yi.automation_write(str(tmp_path), "new", config(), True)
    assert result.error_kind == "ambiguous"
    assert not (tmp_path / "automations.yaml").exists()


@pytest.mark.parametrize("tool,args", [
    ("create_automation", {"automation_id": 123}),
    ("create_automation", {"automation_id": "chosen", "config": dict(config(), id="other")}),
    ("edit_automation", {"automation_id": "chosen", "expected_hash": ""}),
    ("edit_automation", {"automation_id": "chosen", "expected_hash": None}),
    ("edit_automation", {"automation_id": "chosen", "expected_hash": False}),
])
async def test_invalid_identity_never_reaches_gate(hass, identity_env, tmp_path, tool, args):
    access_key, data, reload = identity_env
    path = tmp_path / "automations.yaml"
    seed(path)
    before = path.read_bytes()
    payload = {"config": config(), **args}
    with patch.object(authoring, "_gate", new_callable=AsyncMock) as gate:
        result = await _call_tool(tool, payload, access_key, hass, data)
    assert result[1] == "invalid_request"
    gate.assert_not_called()
    reload.assert_not_called()
    assert path.read_bytes() == before


@pytest.mark.parametrize("tool", ["create_automation", "edit_automation"])
async def test_refused_identity_has_no_effect_or_approval(hass, identity_env, tmp_path, tool):
    access_key, data, reload = identity_env
    access_key.cap_automation_write = "confirm"
    path = tmp_path / "automations.yaml"
    seed(path)
    before = path.read_bytes()
    with patch.object(authoring, "_gate", new_callable=AsyncMock) as gate, patch.object(authoring, "_record_version", new_callable=AsyncMock) as record:
        result = await _call_tool(tool, {"automation_id": "chosen", "config": config("Changed")}, access_key, hass, data)
    assert result[1] != "allowed"
    gate.assert_not_called()
    record.assert_not_called()
    reload.assert_not_called()
    assert path.read_bytes() == before


async def test_two_creates_of_same_id_have_one_winner(hass, identity_env, tmp_path):
    access_key, data, reload = identity_env
    results = await asyncio.gather(*[
        authoring._execute_create_automation({"automation_id": "chosen", "config": config(alias)}, access_key, hass, data)
        for alias in ("First", "Second")
    ])
    assert sorted(r[1] for r in results) == ["allowed", "denied"]
    assert len(yi.load_tagged(str(tmp_path / "automations.yaml"))) == 1
    assert reload.call_count == 1


@pytest.mark.parametrize("fallback", [True, False])
async def test_hash_rechecked_after_waiting_for_lock(hass, identity_env, tmp_path, fallback):
    access_key, data, reload = identity_env
    if fallback:
        (tmp_path / "configuration.yaml").unlink()
    path = tmp_path / "automations.yaml"
    expected = seed(path)
    lock = authoring._get_automation_lock(hass)
    reached_validation = asyncio.Event()
    real_validate = authoring._config_validation_error

    async def validate(*args, **kwargs):
        result = await real_validate(*args, **kwargs)
        reached_validation.set()
        return result

    with patch.object(authoring, "_config_validation_error", side_effect=validate):
        async with lock:
            task = asyncio.create_task(authoring._execute_edit_automation(
                {"automation_id": "chosen", "config": config("Proposed"), "expected_hash": expected}, access_key, hass, data,
            ))
            await reached_validation.wait()
            seed(path, "Concurrent")
            before = path.read_bytes()
        result = await task
    assert result[1] == "invalid_request"
    assert path.read_bytes() == before
    reload.assert_not_called()


async def test_approval_replay_rechecks_create_and_rename(hass, identity_env, tmp_path):
    access_key, data, reload = identity_env
    path = tmp_path / "automations.yaml"
    expected = seed(path)
    seed(path, "Intervening")
    before = path.read_bytes()
    for tool in ("create_automation", "edit_automation"):
        result = await async_execute_approved_tool(tool, {"automation_id": "chosen", "config": config("Proposed"), "expected_hash": expected}, access_key, hass, data)
        assert result[1] != "allowed"
    assert path.read_bytes() == before
    reload.assert_not_called()


async def test_entity_id_is_not_a_create_on_lookup_failure(hass, identity_env, tmp_path):
    access_key, data, reload = identity_env
    path = tmp_path / "automations.yaml"
    seed(path)
    hass.states.async_set("automation.example", "off", {"id": "chosen"})
    before = path.read_bytes()
    result = await _call_tool("edit_automation", {"automation_id": "automation.example", "config": config("Proposed")}, access_key, hass, data)
    assert result[1] != "allowed"
    assert path.read_bytes() == before
    reload.assert_not_called()


async def test_restore_rename_then_recreate_and_restore_again(hass, identity_env, tmp_path):
    _, data, reload = identity_env
    path = tmp_path / "automations.yaml"
    seed(path)
    record = MagicMock(resource_type="automation", resource_id="chosen", before=None, after=config("Restored"))
    for remove in (False, True, False):
        if remove:
            path.write_text("[]\n")
        result = await async_restore_version(record, "admin", hass, data)
        assert result[1] == "allowed"
        assert yi.read_entry(str(tmp_path), "automation", "chosen")["alias"] == "Restored"
    assert reload.call_count == 3


async def test_reload_failure_retry_cannot_duplicate(hass, identity_env, tmp_path):
    access_key, data, _ = identity_env
    async def fail_reload(call):
        raise RuntimeError("reload failed")
    hass.services.async_register("automation", "reload", fail_reload)
    args = {"automation_id": "chosen", "config": config()}
    first = await _call_tool("create_automation", deepcopy(args), access_key, hass, data)
    assert first[1] == "invalid_request"
    before = (tmp_path / "automations.yaml").read_bytes()
    second = await _call_tool("create_automation", deepcopy(args), access_key, hass, data)
    assert second[1] == "denied"
    assert (tmp_path / "automations.yaml").read_bytes() == before


@pytest.mark.parametrize("contents", ["[broken", "not a list", "id: chosen\n", "- invalid\n"])
@pytest.mark.parametrize("fallback", [True, False])
async def test_unreadable_identity_is_never_absence(hass, identity_env, tmp_path, contents, fallback):
    access_key, data, reload = identity_env
    if fallback:
        (tmp_path / "configuration.yaml").unlink()
    path = tmp_path / "automations.yaml"
    path.write_text(contents)
    for tool in ("create_automation", "edit_automation"):
        result = await _call_tool(tool, {"automation_id": "chosen", "config": config("Proposed")}, access_key, hass, data)
        assert result[1] != "allowed"
    assert path.read_text() == contents
    reload.assert_not_called()


async def test_generated_id_collision_is_refused(hass, identity_env, tmp_path):
    access_key, data, reload = identity_env
    path = tmp_path / "automations.yaml"
    existing = dict(config(), id="phoenix_mcp_0123456789abcdef")
    path.write_text(yi.dump_tagged([existing]))
    before = path.read_bytes()
    with patch.object(authoring.uuid, "uuid4", return_value=MagicMock(hex="0123456789abcdef")):
        result = await authoring._execute_create_automation({"config": config("Other")}, access_key, hass, data)
    assert result[1] == "denied"
    assert path.read_bytes() == before
    reload.assert_not_called()


async def test_restore_rechecks_snapshot_after_validation(hass, identity_env, tmp_path):
    _, data, reload = identity_env
    path = tmp_path / "automations.yaml"
    seed(path)
    validate = authoring._config_validation_error

    async def change_during_validation(*args, **kwargs):
        result = await validate(*args, **kwargs)
        seed(path, "Concurrent")
        return result

    record = MagicMock(resource_type="automation", resource_id="chosen", before=None, after=config("Restored"))
    with patch.object(authoring, "_config_validation_error", side_effect=change_during_validation):
        result = await async_restore_version(record, "admin", hass, data)
    assert result[1] == "invalid_request"
    assert yi.read_entry(str(tmp_path), "automation", "chosen")["alias"] == "Concurrent"
    reload.assert_not_called()


def test_alias_addition_requires_hash(tmp_path):
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    old = dict(config(), id="chosen")
    old.pop("alias")
    path = tmp_path / "automations.yaml"
    path.write_text(yi.dump_tagged([old]))
    before = path.read_bytes()
    refused = yi.automation_write(str(tmp_path), "chosen", config(), False)
    assert refused.error_kind == "conflict"
    assert path.read_bytes() == before
    allowed = yi.automation_write(str(tmp_path), "chosen", dict(config(), id="chosen"), False, content_hash(old))
    assert allowed.ok


async def test_executor_rechecks_authority(hass, identity_env, tmp_path):
    access_key, data, reload = identity_env
    path = tmp_path / "automations.yaml"
    expected = seed(path)
    before = path.read_bytes()
    with patch.object(authoring, "check_request_authority", side_effect=authoring.AuthorityChangedError("changed")):
        with pytest.raises(authoring.AuthorityChangedError):
            await authoring._execute_edit_automation({"automation_id": "chosen", "config": config("Other"), "expected_hash": expected}, access_key, hass, data)
    assert path.read_bytes() == before
    reload.assert_not_called()
