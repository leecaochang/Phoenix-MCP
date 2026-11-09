"""Approved restore replays: drift after approval and uncertain outcomes.

An approval binds a snapshot digest and a target fingerprint; the executor must
refuse when either moved during the wait and report an unknown outcome, not a
failure, once Home Assistant may have been reached.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tools.recovery import _tool_restore_configuration
from custom_components.phoenix_mcp.ws_dispatch import WsDispatchError


def runtime():
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())


def access_key(**caps):
    return AccessKeyRecord(id="audit", name="audit", access_key_hash="x", created_at=utcnow(), created_by="test", permissions=PermissionTree(), **caps)


def body(result):
    return json.loads(result[0]["content"][0]["text"])



def _text(result):
    return result[0]["content"][0]["text"]


async def _approved_layout_restore(hass):
    """A dashboard edit and the bound arguments an approval of its rollback would carry."""
    from custom_components.phoenix_mcp.tools import lovelace
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    t, d = access_key(cap_lovelace_write="allow"), runtime()
    before, after = {"views": [{"title": "Before"}]}, {"views": [{"title": "After"}]}
    await lovelace._execute_set_dashboard_config({"config": before}, t, hass, d)
    edited = await lovelace._execute_set_dashboard_config({"config": after}, t, hass, d)
    args = {"recovery_id": body(edited)["recovery_id"], "side": "before"}
    receipt = body(await _tool_restore_configuration(args, t, hass, d))
    args.update({key: receipt[key] for key in ("snapshot_digest", "target_fingerprint")})
    return t, d, args, after


async def test_approved_layout_restore_refuses_a_dashboard_changed_after_approval(hass):
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration
    from custom_components.phoenix_mcp.ws_dispatch import async_get_lovelace_config, async_save_lovelace_config
    t, d, args, _ = await _approved_layout_restore(hass)
    later = {"views": [{"title": "Edited while the approval waited"}]}
    await async_save_lovelace_config(hass, None, later)
    records = len(d.config_recovery.records())
    result = await _execute_restore_configuration(args, t, hass, d)
    assert result[1] == "invalid_request" and "changed after approval" in _text(result)
    assert await async_get_lovelace_config(hass, None) == later
    assert len(d.config_recovery.records()) == records


async def test_approved_layout_restore_refuses_a_different_snapshot(hass):
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration
    from custom_components.phoenix_mcp.ws_dispatch import async_get_lovelace_config
    t, d, args, after = await _approved_layout_restore(hass)
    result = await _execute_restore_configuration({**args, "snapshot_digest": "0" * 64}, t, hass, d)
    assert result[1] == "invalid_request" and "snapshot changed after approval" in _text(result)
    assert await async_get_lovelace_config(hass, None) == after


@pytest.mark.parametrize("started", [False, True])
async def test_a_layout_restore_that_raises_is_uncertain_only_once_started(hass, started):
    from custom_components.phoenix_mcp.tool_common import UncertainOutcome
    from custom_components.phoenix_mcp.tools import recovery
    t, d, args, _ = await _approved_layout_restore(hass)
    seam = "_recoverable_dashboard_save" if started else "_dashboard_layout_snapshot"
    with patch.object(recovery, seam, side_effect=RuntimeError("adapter failed")):
        result = await recovery._execute_restore_configuration(args, t, hass, d)
    assert result[1] == "invalid_request"
    assert isinstance(result[0], UncertainOutcome) is started
    if started:
        assert body(result)["status"] == "unknown" and body(result)["retry_safe"] is False
    else:
        assert _text(result).startswith("Dashboard layout restore failed")


_BLUEPRINT_BEFORE = "blueprint:\n  name: Before\n  domain: automation\ntriggers: []\nactions: []\n"


async def _approved_blueprint_restore(hass, *, saved: str | None, current: str):
    """A recorded blueprint edit, its file on disk, and the arguments an approval would bind."""
    from custom_components.phoenix_mcp.helpers import content_hash
    from custom_components.phoenix_mcp.tools.blueprint import _blueprint_recovery_snapshot
    t, d = access_key(cap_blueprint_write="allow", cap_automation_write="allow"), runtime()
    rel = "audit/replay.yaml"
    path = Path(hass.config.path("blueprints", "automation", "audit", "replay.yaml"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current, encoding="utf-8")
    snapshot = _blueprint_recovery_snapshot("automation", rel, saved)
    rid = await d.config_recovery.begin(f"blueprint:automation/{rel}", "edit_blueprint", t.id, snapshot)
    await d.config_recovery.mark(rid, "verified")
    d.config_recovery.leave(rid)
    args = {
        "recovery_id": rid, "side": "before", "snapshot_digest": content_hash(snapshot),
        "target_fingerprint": content_hash(_blueprint_recovery_snapshot("automation", rel, current)),
    }
    return t, d, args, path


async def test_approved_blueprint_restore_refuses_a_file_changed_after_approval(hass):
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration
    t, d, args, path = await _approved_blueprint_restore(hass, saved=_BLUEPRINT_BEFORE, current="blueprint: current\n")
    path.write_text("blueprint: edited while the approval waited\n", encoding="utf-8")
    ws = AsyncMock()
    with patch("custom_components.phoenix_mcp.tools.recovery.async_ws_command", ws):
        result = await _execute_restore_configuration(args, t, hass, d)
    assert result[1] == "invalid_request" and "changed after approval" in _text(result)
    ws.assert_not_awaited()
    assert path.read_text(encoding="utf-8") == "blueprint: edited while the approval waited\n"


async def test_approved_blueprint_restore_refuses_a_different_snapshot(hass):
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration
    t, d, args, _ = await _approved_blueprint_restore(hass, saved=_BLUEPRINT_BEFORE, current="blueprint: current\n")
    ws = AsyncMock()
    with patch("custom_components.phoenix_mcp.tools.recovery.async_ws_command", ws):
        result = await _execute_restore_configuration({**args, "snapshot_digest": "0" * 64}, t, hass, d)
    assert result[1] == "invalid_request" and "snapshot changed after approval" in _text(result)
    ws.assert_not_awaited()


async def test_a_blueprint_command_failure_leaves_the_restore_unknown(hass):
    from custom_components.phoenix_mcp.tool_common import UncertainOutcome
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration
    t, d, args, path = await _approved_blueprint_restore(hass, saved=_BLUEPRINT_BEFORE, current="blueprint: current\n")
    with patch("custom_components.phoenix_mcp.tools.recovery.async_ws_command",
               AsyncMock(side_effect=WsDispatchError("timed out after dispatch"))):
        result = await _execute_restore_configuration(args, t, hass, d)
    assert result[1] == "invalid_request" and isinstance(result[0], UncertainOutcome)
    rid = body(result)["recovery_id"]
    assert body(result)["status"] == "unknown"
    assert d.config_recovery.get(rid)["phase"] == "unknown"
    assert path.read_text(encoding="utf-8") == "blueprint: current\n"


async def test_restoring_a_blueprint_that_did_not_exist_deletes_it_through_home_assistant(hass):
    from custom_components.phoenix_mcp.tools.recovery import _execute_restore_configuration
    t, d, args, path = await _approved_blueprint_restore(hass, saved=None, current=_BLUEPRINT_BEFORE)
    sent = []

    async def ws(_hass, command, payload):
        sent.append((command, payload))
        path.unlink()

    with patch("custom_components.phoenix_mcp.tools.recovery.async_ws_command", AsyncMock(side_effect=ws)):
        result = await _execute_restore_configuration(args, t, hass, d)
    assert result[1] == "allowed", result
    assert sent == [("blueprint/delete", {"domain": "automation", "path": "audit/replay.yaml"})]
    assert body(result)["status"] == "verified" and not path.exists()
    assert d.config_recovery.get(body(result)["recovery_id"])["phase"] == "verified"
