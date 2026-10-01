"""Closed nested control objects must refuse before flattening or effects."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.token_store import GlobalSettings, TokenRecord
from custom_components.phoenix_mcp.tool_contracts import normalize_tool_args
from custom_components.phoenix_mcp.tools import energy, helper, lovelace, radio
from custom_components.phoenix_mcp.const import (
    MAX_REQUEST_BODY_BYTES, SERVICE_TARGET_MAX_GROUPS, SERVICE_TARGET_MAX_IDS,
    TOOL_INPUT_MAX_DEPTH, TOOL_INPUT_MAX_NODES,
)
from custom_components.phoenix_mcp.tool_input import tool_arguments_error


@pytest.mark.parametrize("tool,arguments", [
    ("set_entity", {"entity_id": "sensor.reviewed", "changes": {"name": "Updated", "entity_id": "sensor.other"}}),
    ("set_device", {"device_id": "reviewed", "changes": {"name": "Updated", "device_id": "other"}}),
    ("set_integration", {"entry_id": "reviewed", "changes": {"title": "Updated", "entry_id": "other"}}),
    ("edit_energy_config", {"operation": "remove_device", "target": {"kind": "statistic", "id": "sensor.reviewed"}, "changes": {"op": "set_source"}}),
    ("patch_dashboard", {"target": {"path": ["views", True]}, "change": {"kind": "remove"}}),
    ("patch_yaml_config", {"address": {"kind": "path", "value": ["a", True]}, "change": {"kind": "remove"}}),
    ("get_logbook", {"target": None}),
    ("get_logbook", {"target": {"kind": "context", "id": "context", "entity_ids": ["sensor.other"]}}),
    ("get_state", {"entity_id": "sensor.reviewed", "projection": {"kind": "compact", "fields": ["state"]}}),
    ("get_relationships", {"scope": {"kind": [], "id": "reviewed"}}),
    ("get_esphome_job", {"lookup": {"kind": [], "id": "reviewed"}}),
    ("call_service", {"service": {"domain": "light", "name": "turn_on", "data": None}}),
    ("call_service", {"service": {"domain": "light", "name": "turn_on"}, "targets": None}),
    ("call_service", {"service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": []}]}),
    ("call_service", {"service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": "all", "ids": ["light.other"]}]}),
    ("call_service", {"service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": "entity", "ids": ["light.reviewed", False]}]}),
])
def test_nested_control_refuses_invalid_input(tool, arguments):
    _, error = normalize_tool_args(tool, arguments)
    assert error is not None


@pytest.mark.parametrize("step", [
    {"step_id": "user", "data": {}, "ignored": True},
    {"step_id": "user", "data": {}, 1: "mixed"},
])
def test_helper_step_envelope_is_closed(step):
    assert helper._helper_flow_steps_error([step]) is not None


def test_group_members_are_validated_as_a_whole():
    assert radio._group_member_args({"members": [
        {"device_id": "reviewed", "endpoint": 1},
        {"device_id": "other", "endpoint": 1, "ignored": True},
    ]}) is None


def _environment(cap, mode):
    token = TokenRecord(id="nested", name="nested", token_hash="x", created_at=utcnow(), created_by="admin", **{cap: mode})
    store = MagicMock()
    store.get_settings.return_value = GlobalSettings()
    return token, PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock())


@pytest.mark.parametrize("tool,args", [
    ("create_dashboard", {"config": {"url_path": "reviewed-dashboard", "title": "Reviewed", "id": "other"}}),
    ("edit_dashboard", {"dashboard_id": "reviewed-dashboard", "config": {"title": "Reviewed", "dashboard_id": "other-dashboard"}}),
    ("edit_dashboard", {"dashboard_id": "reviewed-dashboard", "config": {"show_in_sidebar": "false"}}),
])
async def test_dashboard_metadata_refuses_before_gate(hass, tool, args):
    token, data = _environment("cap_lovelace_write", "confirm")
    with patch.object(lovelace, "_gate", AsyncMock(return_value=({}, "pending_approval", tool))) as gate:
        result, outcome, _ = await mcp_view._call_tool(tool, args, token, hass, data)
    assert outcome == "invalid_request"
    assert result["isError"]
    gate.assert_not_called()


@pytest.mark.parametrize("cap,tool,args", [
    ("cap_registry_write", "set_entity", {"entity_id": "sensor.reviewed", "changes": {"entity_id": "sensor.other"}}),
    ("cap_registry_write", "set_device", {"device_id": "reviewed", "changes": {"device_id": "other"}}),
    ("cap_integration_write", "set_integration", {"entry_id": "reviewed", "changes": {"entry_id": "other"}}),
    ("cap_energy_write", "edit_energy_config", {"operation": "remove_device", "target": {"kind": "statistic", "id": "sensor.reviewed"}, "changes": {"op": "set_source"}}),
    ("cap_lovelace_write", "edit_dashboard", {"dashboard_id": "reviewed", "config": {"dashboard_id": "other"}}),
    ("cap_physical_control", "call_service", {"service": {"domain": "cover", "name": "open_cover", "ignored": True}, "targets": [{"kind": "entity", "ids": ["cover.reviewed"]}]}),
    ("cap_restart", "call_service", {"service": {"domain": "homeassistant", "name": "restart", "ignored": True}}),
    ("cap_helper_write", "create_helper", {"helper_type": "template", "flow_steps": [{"step_id": "user", "data": {}, "ignored": True}]}),
    ("cap_radio_write", "create_zigbee_group", {"name": "Review", "members": [{"device_id": "reviewed", "endpoint": True}]}),
])
async def test_denial_precedes_nested_diagnostics(hass, cap, tool, args):
    token, data = _environment(cap, "deny")
    result, outcome, _ = await mcp_view._call_tool(tool, args, token, hass, data)
    assert outcome == "denied"
    assert "unsupported" not in result["content"][0]["text"].lower()


@pytest.mark.parametrize("tool", ["call_service", "dry_run_service"])
@pytest.mark.parametrize("field", ["groups", "ids"])
def test_service_batch_limits(tool, field):
    targets = [{"kind": "entity", "ids": ["light.reviewed"]}]
    if field == "groups":
        targets *= SERVICE_TARGET_MAX_GROUPS + 1
    else:
        targets[0]["ids"] *= SERVICE_TARGET_MAX_IDS + 1
    _, error = normalize_tool_args(tool, {"service": {"domain": "light", "name": "turn_on"}, "targets": targets})
    assert error


@pytest.mark.parametrize("tool,args", [
    ("get_logbook", {}),
    ("call_service", {"service": {"domain": "light", "name": "turn_on", "data": {"custom": {"nested": [1, None]}}}}),
    ("set_entity", {"entity_id": "sensor.reviewed", "changes": {"name": None, "categories": {"automation": None}}, "outer_extra": {"anything": 1}}),
    ("set_entity", {"entity_id": "sensor.reviewed", "changes": {"name": "Updated"}, "projection": {"unrelated": True}, "change": {"kind": "remove", "unused": True}}),
    ("edit_energy_config", {"operation": "set_source", "target": {"kind": "source", "source_type": "grid"}, "changes": {"number_energy_price": None, "stat_energy_to": None}}),
    ("patch_dashboard", {"target": {"path": ["views", 0]}, "change": {"kind": "set", "value": None}}),
])
def test_documented_absence_null_and_open_data_remain_valid(tool, args):
    _, error = normalize_tool_args(tool, args)
    assert error is None


def _deep_value():
    value: object = "leaf"
    for _ in range(TOOL_INPUT_MAX_DEPTH + 1):
        value = [value]
    return value


@pytest.mark.parametrize("value", [
    _deep_value(), [0] * TOOL_INPUT_MAX_NODES, {1: "mixed"},
    float("nan"), float("inf"), 10**1000, {"not_json"}, (1, 2),
    "x" * (MAX_REQUEST_BODY_BYTES + 1),
    "\n" * (MAX_REQUEST_BODY_BYTES // 2),
    [10**300] * 4000,
])
@pytest.mark.parametrize("approved", [False, True])
async def test_malformed_json_never_reaches_handler_or_executor(hass, value, approved):
    token, data = _environment("cap_lovelace_write", "allow")
    token.pass_through = True
    args = {"config": {"nested": value}}
    handler = AsyncMock()
    with patch.dict(mcp_view._TOOL_HANDLERS, {"edit_dashboard": (handler, (), {})}), patch.dict(
        mcp_view._EXECUTOR_REGISTRY, {"edit_dashboard": handler}
    ):
        if approved:
            result, outcome, _ = await mcp_view.async_execute_approved_tool("edit_dashboard", args, token, hass, data)
        else:
            result, outcome, _ = await mcp_view._call_tool("edit_dashboard", args, token, hass, data)
    assert outcome == "invalid_request"
    assert result["isError"]
    handler.assert_not_called()


def test_cycles_are_bounded_and_shared_references_are_permitted():
    cycle = {}
    cycle["self"] = cycle
    assert tool_arguments_error(cycle)
    shared = {"value": [1, 2, 3]}
    assert tool_arguments_error({"first": shared, "second": shared}) is None


@pytest.mark.parametrize("value", [True, "1.2", float("inf"), 10**1000])
def test_energy_executor_refuses_invalid_numeric_values(hass, value):
    token, _ = _environment("cap_energy_write", "allow")
    with pytest.raises(energy._OpError):
        energy._source_fields_arg({"number_energy_price": value}, "grid", token, hass)


async def test_dashboard_executor_rechecks_metadata_shape(hass):
    token, data = _environment("cap_lovelace_write", "allow")
    with patch.object(lovelace, "_dashboard_registry_snapshot", AsyncMock()) as snapshot:
        result, outcome, _ = await mcp_view.async_execute_approved_tool(
            "edit_dashboard", {"dashboard_id": "reviewed", "config": {"dashboard_id": "other"}}, token, hass, data,
        )
    assert outcome == "invalid_request"
    assert result["isError"]
    snapshot.assert_not_called()
