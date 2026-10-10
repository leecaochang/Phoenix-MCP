"""Helper edit/delete ownership and storage diagnostics.

A Template helper id routes to config-entry settings or removal only when the
entity's platform and its owning config entry are both Template, so another
integration's entity can never select its own entry. Storage helper writes
refuse before approval when their safety copy cannot be read, name YAML
helpers to a key that can see them, and report a refused dispatch as
unchanged only when settled readback proves it.
"""

from copy import deepcopy
import json
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.request_authority import AuthorityChangedError
from custom_components.phoenix_mcp.tools import helper
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import WsDispatchError, async_ws_command
from tests.runtime_fixtures import mesa_off_store


def _key(cap="allow", domains=("input_boolean", "input_number", "sensor")):
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="ownership", access_key_hash="x", created_at=utcnow(),
        created_by="test", cap_helper_write=cap, cap_integration_write=cap, cap_registry_read="allow",
        permissions=PermissionTree(domains={d: PermissionNode(state="GREEN") for d in domains}),
    )


def _data():
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


def _text(content):
    return content["content"][0]["text"]


def _registered_sensor(hass, platform, entry_domain, unique_id="shared-id"):
    options = {"template_type": "sensor", "name": "Shared", "state": "{{ 1 }}"} if entry_domain == "template" else {}
    entry = MockConfigEntry(domain=entry_domain, options=options)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    entity = registry.async_get_or_create("sensor", platform, unique_id, config_entry=entry)
    return entry, entity.entity_id


@pytest.mark.parametrize("platform,entry_domain", [
    ("foreign_lights", "foreign_lights"),
    ("template", "foreign_lights"),
    ("foreign_lights", "template"),
])
@pytest.mark.parametrize("tool", ("delete_helper", "edit_helper"))
async def test_foreign_owner_never_reaches_config_entry_removal_or_settings(hass, platform, entry_domain, tool):
    entry, _ = _registered_sensor(hass, platform, entry_domain)
    args = {"helper_type": "sensor", "helper_id": "shared-id"}
    if tool == "edit_helper":
        args["config"] = {"state": "{{ 1 }}"}
    settings = AsyncMock()
    with patch.object(hass.config_entries, "async_remove", AsyncMock()) as remove, \
            patch.object(helper, "_tool_set_config_entry_options", settings):
        content, outcome, _ = await _call_tool(tool, args, _key(), hass, _data())
    assert outcome == "not_found", content
    remove.assert_not_awaited()
    settings.assert_not_awaited()
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry


async def test_template_owner_routes_past_a_foreign_entity_with_the_same_unique_id(hass):
    """The foreign entity registers first, which a domain-only scan would match."""
    foreign, foreign_entity = _registered_sensor(hass, "foreign_lights", "foreign_lights")
    template, template_entity = _registered_sensor(hass, "template", "template")
    assert helper._resolve_helper_entity_id(hass, "sensor", "shared-id", platform="template") == template_entity
    assert helper._resolve_helper_entity_id(hass, "sensor", "shared-id", platform="foreign_lights") == foreign_entity
    content, outcome, _ = await _call_tool(
        "delete_helper", {"helper_type": "sensor", "helper_id": "shared-id"}, _key(), hass, _data(),
    )
    assert outcome == "allowed", content
    assert hass.config_entries.async_get_entry(template.entry_id) is None
    assert hass.config_entries.async_get_entry(foreign.entry_id) is foreign
    assert er.async_get(hass).async_get(foreign_entity) is not None


async def test_storage_type_resolves_only_its_own_platform(hass):
    er.async_get(hass).async_get_or_create("input_boolean", "foreign_lights", "lookalike")
    assert helper._resolve_helper_entity_id(hass, "input_boolean", "lookalike") is None
    content, outcome, _ = await _call_tool(
        "delete_helper", {"helper_type": "input_boolean", "helper_id": "lookalike"}, _key(), hass, _data(),
    )
    assert outcome == "not_found", content


@pytest.fixture
async def booleans(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"yaml_one": {"name": "Yaml one"}}})
    stored = await async_ws_command(hass, "input_boolean/create", {"name": "Stored one"})
    await hass.async_block_till_done()
    return stored


def _call_args(tool, helper_id):
    args = {"helper_type": "input_boolean", "helper_id": helper_id}
    if tool == "edit_helper":
        args["config"] = {"name": "Changed"}
    return args


@pytest.mark.parametrize("tool", ("delete_helper", "edit_helper"))
async def test_yaml_helper_refuses_before_approval(hass, booleans, tool):
    data, gate = _data(), AsyncMock()
    with patch.object(helper, "_gate", gate):
        content, outcome, _ = await _call_tool(tool, _call_args(tool, "yaml_one"), _key("confirm"), hass, data)
    assert outcome == "invalid_request", content
    assert "defined in YAML" in _text(content) and "nothing changed" in _text(content)
    gate.assert_not_awaited()
    assert data.config_recovery.records() == []
    assert hass.states.get("input_boolean.yaml_one") is not None


async def test_yaml_helper_is_plain_absence_to_a_key_that_cannot_see_it(hass, booleans):
    content, outcome, _ = await _call_tool(
        "delete_helper", _call_args("delete_helper", "yaml_one"), _key(domains=()), hass, _data(),
    )
    assert outcome == "not_found", content
    assert "YAML" not in _text(content)


async def test_registered_entity_missing_from_storage_is_not_found(hass, booleans):
    er.async_get(hass).async_get_or_create("input_boolean", "input_boolean", "orphan")
    content, outcome, _ = await _call_tool(
        "delete_helper", _call_args("delete_helper", "orphan"), _key(), hass, _data(),
    )
    assert outcome == "not_found", content


@pytest.mark.parametrize("result", (WsDispatchError("unavailable"), {"not": "a list"}))
@pytest.mark.parametrize("tool", ("delete_helper", "edit_helper"))
async def test_unreadable_storage_refuses_before_approval(hass, booleans, result, tool):
    real = helper.async_ws_command

    async def dispatch(hass_, command, payload, **kwargs):
        if command == "input_boolean/list":
            if isinstance(result, Exception):
                raise result
            return result
        return await real(hass_, command, payload, **kwargs)

    data, gate = _data(), AsyncMock()
    with patch.object(helper, "_gate", gate), patch.object(helper, "async_ws_command", dispatch):
        content, outcome, _ = await _call_tool(tool, _call_args(tool, booleans["id"]), _key("confirm"), hass, data)
    assert outcome == "invalid_request", content
    assert "storage is unavailable; nothing changed" in _text(content)
    gate.assert_not_awaited()
    assert data.config_recovery.records() == []


@pytest.mark.parametrize("tool", ("delete_helper", "edit_helper"))
async def test_storage_lost_after_approval_changes_nothing(hass, booleans, tool):
    data, key = _data(), _key("confirm")
    gate = AsyncMock(return_value=({}, "pending_approval", "approval"))
    with patch.object(helper, "_gate", gate):
        _, outcome, _ = await _call_tool(tool, _call_args(tool, booleans["id"]), key, hass, data)
    assert outcome == "pending_approval"
    saved = deepcopy(gate.await_args.kwargs["args"])
    with patch.object(helper, "async_ws_command", AsyncMock(side_effect=WsDispatchError("gone"))) as dispatch:
        content, outcome, _ = await async_execute_approved_tool(tool, saved, key, hass, data)
    assert outcome == "invalid_request", content
    assert "storage is unavailable; nothing changed" in _text(content)
    assert {call.args[1] for call in dispatch.await_args_list} == {"input_boolean/list"}
    assert data.config_recovery.records() == []


async def test_storage_lost_inside_the_boundary_is_not_applied(hass, booleans):
    """The tool and executor reads admit the request; the boundary's own read fails."""
    real = helper.async_ws_command
    reads = 0

    async def dispatch(hass_, command, payload, **kwargs):
        nonlocal reads
        if command == "input_boolean/list":
            reads += 1
            if reads > 2:
                raise WsDispatchError("gone")
        if command == "input_boolean/delete":
            raise AssertionError("dispatched")
        return await real(hass_, command, payload, **kwargs)

    data = _data()
    with patch.object(helper, "async_ws_command", dispatch):
        content, outcome, _ = await _call_tool(
            "delete_helper", _call_args("delete_helper", booleans["id"]), _key(), hass, data,
        )
    assert outcome == "invalid_request", content
    assert "nothing was dispatched" in _text(content)
    assert [r["phase"] for r in data.config_recovery.records()] == ["not_applied"]
    content, outcome, _ = await _call_tool(
        "delete_helper", _call_args("delete_helper", booleans["id"]), _key(), hass, data,
    )
    assert outcome == "allowed", content


async def test_settled_home_assistant_refusal_reports_nothing_changed(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_number", {"input_number": {}})
    item = await async_ws_command(hass, "input_number/create", {"name": "Level", "min": 0, "max": 10})
    await hass.async_block_till_done()
    data = _data()
    args = {"helper_type": "input_number", "helper_id": item["id"], "config": {"min": 20}}
    content, outcome, _ = await _call_tool("edit_helper", args, _key(), hass, data)
    assert outcome == "invalid_request", content
    assert "Home Assistant refused the helper change" in _text(content) and "nothing changed" in _text(content)
    assert [r["phase"] for r in data.config_recovery.records()] == ["not_applied"]
    stored = await async_ws_command(hass, "input_number/list", {})
    assert next(r for r in stored if r["id"] == item["id"]) == item
    args["config"] = {"min": 5}
    content, outcome, _ = await _call_tool("edit_helper", args, _key(), hass, data)
    assert outcome == "allowed", content


async def test_refusal_while_a_command_is_unsettled_stays_uncertain(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_number", {"input_number": {}})
    item = await async_ws_command(hass, "input_number/create", {"name": "Level", "min": 0, "max": 10})
    await hass.async_block_till_done()
    data = _data()
    with patch.object(helper, "ws_commands_unsettled", return_value=True):
        content, outcome, _ = await _call_tool("edit_helper", {
            "helper_type": "input_number", "helper_id": item["id"], "config": {"min": 20},
        }, _key(), hass, data)
    assert outcome == "invalid_request", content
    body = json.loads(_text(content))
    assert body["status"] == "unknown" and body["retry_safe"] is False
    assert [r["phase"] for r in data.config_recovery.records()] == ["unknown"]


@pytest.mark.parametrize("fault,status", (("raise_after_landing", "unknown"), ("no_effect", "partial")))
async def test_dispatched_delete_without_verified_result_is_uncertain(hass, booleans, fault, status):
    real = helper.async_ws_command

    async def dispatch(hass_, command, payload, **kwargs):
        if command == "input_boolean/delete":
            if fault == "no_effect":
                return None
            await real(hass_, command, payload, **kwargs)
            raise WsDispatchError("connection lost")
        return await real(hass_, command, payload, **kwargs)

    data = _data()
    with patch.object(helper, "async_ws_command", dispatch):
        content, outcome, _ = await _call_tool(
            "delete_helper", _call_args("delete_helper", booleans["id"]), _key(), hass, data,
        )
    assert outcome == "invalid_request", content
    body = json.loads(_text(content))
    record = data.config_recovery.records()[0]
    assert body == {**body, "status": status, "recovery_id": record["id"], "retry_safe": False}
    assert record["phase"] == status


async def test_authority_change_before_dispatch_propagates_as_not_applied(hass, booleans):
    data = _data()
    with patch("custom_components.phoenix_mcp.request_authority.check_request_authority",
               side_effect=AuthorityChangedError("Unauthorized.")), \
            pytest.raises(AuthorityChangedError):
        await _call_tool("delete_helper", _call_args("delete_helper", booleans["id"]), _key(), hass, data)
    assert [r["phase"] for r in data.config_recovery.records()] == ["not_applied"]
    rows = await async_ws_command(hass, "input_boolean/list", {})
    assert any(r["id"] == booleans["id"] for r in rows)
