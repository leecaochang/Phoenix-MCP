"""A malformed filter or target is refused; it never silently becomes a broader one."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool, _tool_is_announced
from tests.runtime_fixtures import mesa_off_store


def _key(**fields) -> AccessKeyRecord:
    fields.setdefault("permissions", PermissionTree(domains={"light": PermissionNode(state="GREEN")}))
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x",
                           created_at=utcnow(), created_by="u", **fields)


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())


@pytest.mark.parametrize("tool,args,cap", [
    ("get_dashboard_config", {"url_path": False}, "cap_lovelace_write"),
    ("set_dashboard_config", {"url_path": 0, "config": {"views": []}}, "cap_lovelace_write"),
    ("add_dashboard_card", {"url_path": [], "view_index": 0, "card": {"type": "markdown"}}, "cap_lovelace_write"),
    ("create_backup", {"agent_ids": "hassio.local"}, "cap_backup"),
    ("create_backup", {"agent_ids": []}, "cap_backup"),
    ("search_entities", {"domain": {"item": "light"}}, "cap_search"),
    ("get_logs", {"integration": ["hue"]}, "cap_log_read"),
    ("get_logs", {"search": 3}, "cap_log_read"),
    ("get_logbook", {"target": {"kind": "resources", "entity_ids": []}}, "cap_log_read"),
])
async def test_malformed_selector_is_refused_before_any_effect(hass, tool, args, cap):
    with patch("custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config", AsyncMock()) as read:
        result, outcome, _ = await _call_tool(tool, args, _key(**{cap: "allow"}), hass, _data())
    assert outcome == "invalid_request", result
    read.assert_not_called()


def test_call_service_is_announced_for_capability_only_services():
    hass = MagicMock()
    from custom_components.phoenix_mcp.tool_defs import _ENTITY_TOOL_DEFS
    definition = next(d for d in _ENTITY_TOOL_DEFS if d["name"] == "call_service")
    no_scope = PermissionTree()
    assert not _tool_is_announced(definition, _key(permissions=no_scope), False, hass)
    assert _tool_is_announced(definition, _key(permissions=no_scope, cap_restart="allow"), False, hass)
    assert _tool_is_announced(definition, _key(permissions=no_scope, cap_yaml_edit="confirm"), False, hass)


async def test_restart_through_call_service_leaves_the_restart_receipt(hass):
    data = _data()
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        result, outcome, _ = await _call_tool(
            "call_service", {"service": {"domain": "homeassistant", "name": "restart"}},
            _key(permissions=PermissionTree(), cap_restart="allow"), hass, data)
    assert outcome == "allowed", result
    body = json.loads(result["content"][0]["text"])
    assert body["status"] == "dispatched" and body["retry_safe"] is False
    assert data.config_recovery.get(body["recovery_id"])["before"]["snapshot_type"] == "restart_receipt"
    call.assert_awaited_once()


async def test_a_failed_recovery_snapshot_reports_that_nothing_was_written(hass):
    from custom_components.phoenix_mcp.tools.config_files import _execute_write_file
    data = _data()
    with patch.object(data.config_recovery, "begin", AsyncMock(side_effect=OSError("disk full"))):
        result = await _execute_write_file({"path": "www/new.txt", "content": "x"},
                                           _key(cap_filesystem="allow"), hass, data)
    assert result[1] == "invalid_request"
    assert "nothing was written" in result[0]["content"][0]["text"]
