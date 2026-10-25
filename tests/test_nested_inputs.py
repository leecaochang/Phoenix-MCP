"""Closed nested control objects must refuse before flattening or effects."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.access_key_store import GlobalSettings, AccessKeyRecord
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
    ("patch_dashboard", {"changes": [{"path": ["views", True], "kind": "remove"}]}),
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
    access_key = AccessKeyRecord(id="nested", name="nested", access_key_hash="x", created_at=utcnow(), created_by="admin", **{cap: mode})
    store = MagicMock()
    store.get_settings.return_value = GlobalSettings()
    return access_key, PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock())


@pytest.mark.parametrize("tool,args", [
    ("create_dashboard", {"config": {"url_path": "reviewed-dashboard", "title": "Reviewed", "id": "other"}}),
    ("edit_dashboard", {"dashboard_id": "reviewed-dashboard", "config": {"title": "Reviewed", "dashboard_id": "other-dashboard"}}),
    ("edit_dashboard", {"dashboard_id": "reviewed-dashboard", "config": {"show_in_sidebar": "false"}}),
])
async def test_dashboard_metadata_refuses_before_gate(hass, tool, args):
    access_key, data = _environment("cap_lovelace_write", "confirm")
    with patch.object(lovelace, "_gate", AsyncMock(return_value=({}, "pending_approval", tool))) as gate:
        result, outcome, _ = await mcp_view._call_tool(tool, args, access_key, hass, data)
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
    access_key, data = _environment(cap, "deny")
    result, outcome, _ = await mcp_view._call_tool(tool, args, access_key, hass, data)
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
    ("set_entity", {"entity_id": "sensor.reviewed", "changes": {"name": None, "categories": {"automation": None}}}),
    ("edit_energy_config", {"operation": "set_source", "target": {"kind": "source", "source_type": "grid"}, "changes": {"number_energy_price": None, "stat_energy_to": None}}),
    ("patch_dashboard", {"changes": [{"path": ["views", 0], "kind": "set", "value": None}]}),
])
def test_documented_absence_null_and_open_data_remain_valid(tool, args):
    _, error = normalize_tool_args(tool, args)
    assert error is None


def test_outer_extra_fields_are_refused_by_name():
    for extra in ({"outer_extra": {"anything": 1}}, {"projection": {"unrelated": True}}):
        _, error = normalize_tool_args("set_entity", {"entity_id": "sensor.reviewed", "changes": {"name": "Updated"}, **extra})
        assert error and f'Unknown argument "{next(iter(extra))}"' in error


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
    access_key, data = _environment("cap_lovelace_write", "allow")
    access_key.pass_through = True
    args = {"config": {"nested": value}}
    handler = AsyncMock()
    with patch.dict(mcp_view._TOOL_HANDLERS, {"edit_dashboard": (handler, (), {})}), patch.dict(
        mcp_view._EXECUTOR_REGISTRY, {"edit_dashboard": handler}
    ):
        if approved:
            result, outcome, _ = await mcp_view.async_execute_approved_tool("edit_dashboard", args, access_key, hass, data)
        else:
            result, outcome, _ = await mcp_view._call_tool("edit_dashboard", args, access_key, hass, data)
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
    access_key, _ = _environment("cap_energy_write", "allow")
    with pytest.raises(energy._OpError):
        energy._source_fields_arg({"number_energy_price": value}, "grid", access_key, hass)


async def test_dashboard_executor_rechecks_metadata_shape(hass):
    access_key, data = _environment("cap_lovelace_write", "allow")
    with patch.object(lovelace, "_dashboard_registry_snapshot", AsyncMock()) as snapshot:
        result, outcome, _ = await mcp_view.async_execute_approved_tool(
            "edit_dashboard", {"dashboard_id": "reviewed", "config": {"dashboard_id": "other"}}, access_key, hass, data,
        )
    assert outcome == "invalid_request"
    assert result["isError"]
    snapshot.assert_not_called()


class TestJsonEncodedContainers:
    """Weak clients send containers as JSON text; decode only where text is impossible."""

    def test_object_and_array_text_decode_to_their_declared_types(self):
        from custom_components.phoenix_mcp.tool_input import decode_container_strings
        decoded = decode_container_strings("call_service", {
            "service": ' {"domain": "light", "name": "turn_on"}',
            "targets": '[{"kind": "entity", "ids": ["light.kitchen"]}]',
        })
        assert decoded == {
            "service": {"domain": "light", "name": "turn_on"},
            "targets": [{"kind": "entity", "ids": ["light.kitchen"]}],
        }
        _, error = normalize_tool_args("call_service", decoded)
        assert error is None

    @pytest.mark.parametrize("tool,arguments", [
        ("search_entities", {"query": '{"looks": "like json"}'}),
        ("get_state", {"entity_id": "light.kitchen", "projection": '{"kind": '}),
        ("get_state", {"entity_id": "light.kitchen", "projection": '["compact"]'}),
        ("call_service", {"targets": '"[1]"'}),
        ("unknown_tool", {"projection": '{"kind": "full"}'}),
    ])
    def test_text_parameters_and_wrong_shapes_are_left_untouched(self, tool, arguments):
        from custom_components.phoenix_mcp.tool_input import decode_container_strings
        assert decode_container_strings(tool, arguments) is arguments

    def test_native_tool_arrays_decode(self):
        from custom_components.phoenix_mcp.tool_defs import _NATIVE_TOOL_DEFS
        from custom_components.phoenix_mcp.tool_input import decode_container_strings
        name, field = next((d["name"], key) for d in _NATIVE_TOOL_DEFS
                           for key, schema in d["inputSchema"].get("properties", {}).items()
                           if schema.get("type") == "array")
        assert decode_container_strings(name, {field: '["light"]'}) == {field: ["light"]}

    async def test_dispatch_accepts_a_stringified_projection(self, hass):
        from homeassistant.util.dt import utcnow as now
        from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree
        key = AccessKeyRecord(id="weak", name="weak", access_key_hash="x", created_at=now(), created_by="admin",
                              permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}))
        hass.states.async_set("light.kitchen", "on", {"brightness": 128})
        result, outcome, _ = await mcp_view._call_tool(
            "get_state", {"entity_id": "light.kitchen", "projection": '{"kind": "full"}'}, key, hass, MagicMock())
        assert outcome == "allowed", result
        assert '"brightness": 128' in result["content"][0]["text"]

    async def test_decoded_arguments_are_bounded_again(self, hass):
        deep = "[" * (TOOL_INPUT_MAX_DEPTH + 5) + "]" * (TOOL_INPUT_MAX_DEPTH + 5)
        result, outcome, _ = await mcp_view._call_tool(
            "call_service", {"service": {"domain": "light", "name": "turn_on"}, "targets": deep},
            AccessKeyRecord(id="weak", name="weak", access_key_hash="x", created_at=utcnow(), created_by="admin"),
            hass, MagicMock())
        assert outcome == "invalid_request"
        assert "nesting" in result["content"][0]["text"]


@pytest.mark.parametrize("arguments,reaches_handler", [
    pytest.param(..., True, id="omitted"),
    (None, True),
    ({}, True),
    ([], False),
    (False, False),
    (0, False),
    ("", False),
    ([1], False),
])
async def test_only_an_omitted_or_null_arguments_member_means_no_arguments(hass, access_key_store, arguments, reaches_handler):
    """A falsy non-object is malformed, not a no-argument call; it must not queue an approval."""
    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.const import DOMAIN
    from custom_components.phoenix_mcp.rate_limiter import RateLimiter

    access_key, _raw = await access_key_store.async_create_access_key("argument-shape", "admin")
    await access_key_store.async_patch_access_key(access_key.id, cap_restart="confirm")
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog(), hass=hass)
    hass.data[DOMAIN] = data
    handler = AsyncMock(return_value=({"content": [{"type": "text", "text": "ok"}]}, "allowed", "restart_ha"))
    params: dict = {"name": "restart_ha"}
    if arguments is not ...:
        params["arguments"] = arguments
    try:
        with patch.dict(mcp_view._TOOL_HANDLERS, {"restart_ha": (handler, (), {})}):
            _msg, _method, _resource, outcome = await mcp_view._dispatch_mcp(
                "tools/call", 1, params, access_key, hass, data, "127.0.0.1", "")
    finally:
        if data.sensor_flush_cancel is not None:
            data.sensor_flush_cancel()
    assert (outcome != "invalid_request") is reaches_handler
    assert handler.called is reaches_handler
