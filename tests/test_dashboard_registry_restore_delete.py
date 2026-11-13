"""Restoring a dashboard to a registry snapshot taken before it existed keeps its layout.

Home Assistant deletes a dashboard's stored layout with its registry row. A restore_configuration
of a dashboard_registry snapshot with exists=false therefore deletes the dashboard, so it first
records the layout as its own recovery copy, as delete_dashboard does, and its receipt and review
say the dashboard and its views will be deleted. The administrator restore path runs the same code.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tools.lovelace import _execute_create_dashboard
from custom_components.phoenix_mcp.tools.recovery import _tool_restore_configuration
from custom_components.phoenix_mcp.tools.restore import async_restore_configuration
from custom_components.phoenix_mcp.ws_dispatch import (
    WsDashboardNotFoundError, async_get_lovelace_config, async_save_lovelace_config, async_ws_command,
)

LAYOUT = {"views": [{"title": "Home", "cards": [{"type": "markdown", "content": "hand built"}]}]}


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=PermissionTree(), cap_lovelace_write="allow")


def _body(result) -> dict:
    return json.loads(result[0]["content"][0]["text"])


@pytest.fixture
async def board(hass, hass_admin_user):
    """A dashboard a key created, on which a person then built a layout in Home Assistant's editor."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    key = _key()
    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())
    created = await _execute_create_dashboard({"config": {"url_path": "kept-board", "title": "Board"}}, key, hass, data)
    assert created[1] == "allowed", created
    await async_save_lovelace_config(hass, "kept-board", LAYOUT)
    return key, data, _body(created)["recovery_id"]


async def _gone(hass) -> bool:
    rows = await async_ws_command(hass, "lovelace/dashboards/list", {})
    try:
        layout = await async_get_lovelace_config(hass, "kept-board", storage_only=True)
    except WsDashboardNotFoundError:
        layout = None
    return not any(row.get("url_path") == "kept-board" for row in rows) and layout is None


def _layout_copies(data) -> list[dict]:
    return [record for record in data.config_recovery.records()
            if record["operation"] == "dashboard_layout_delete" and "hand built" in json.dumps(record)]


async def test_key_restore_to_absent_keeps_a_layout_copy_and_says_it_deletes(hass, board):
    key, data, record_id = board
    args = {"recovery_id": record_id, "side": "before"}
    receipt = _body(await _tool_restore_configuration(args, key, hass, data))
    diff = receipt["diff"]
    assert diff["kind"] == "system_action" and diff["summary_key"] == "diff.dashboard.delete"
    assert "deletes the dashboard with all its views" in diff["preview"]["warning"]
    assert diff["preview"]["views"] == 1
    args.update({k: receipt[k] for k in ("snapshot_digest", "target_fingerprint")})
    restored = await _tool_restore_configuration(args, key, hass, data)
    assert restored[1] == "allowed" and _body(restored)["status"] == "verified", restored
    assert "layout_recovery_id" in _body(restored)
    assert await _gone(hass)
    copies = _layout_copies(data)
    assert len(copies) == 1 and copies[0]["phase"] == "verified"


async def test_admin_restore_to_absent_keeps_a_layout_copy(hass, board, hass_admin_user):
    _, data, record_id = board
    restored = await async_restore_configuration(record_id, "before", hass_admin_user.id, hass, data)
    assert restored[1] == "allowed", restored
    assert await _gone(hass)
    assert len(_layout_copies(data)) == 1


async def test_layout_that_cannot_be_copied_is_not_deleted(hass, board):
    """A layout the recovery store refuses (it holds a secret) leaves the dashboard in place."""
    key, data, record_id = board
    await async_save_lovelace_config(hass, "kept-board", {"views": [{"cards": [
        {"type": "iframe", "url": "https://user:hunter2@example.invalid/"}]}]})
    args = {"recovery_id": record_id, "side": "before"}
    receipt = _body(await _tool_restore_configuration(args, key, hass, data))
    args.update({k: receipt[k] for k in ("snapshot_digest", "target_fingerprint")})
    restored = await _tool_restore_configuration(args, key, hass, data)
    assert restored[1] == "invalid_request"
    assert "nothing was deleted" in restored[0]["content"][0]["text"]
    assert not await _gone(hass)
