"""Storage helpers reject request fields and bind writes to one exact identity."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord, GlobalSettings, PermissionNode, PermissionTree,
)
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError
from custom_components.phoenix_mcp.const import STORAGE_HELPER_TYPES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import (
    _call_tool, async_execute_approved_tool, async_restore_version,
)
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.tools import helper
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import async_ws_command
from tests.runtime_fixtures import mesa_off_store


def _key(cap="allow", *, pass_through=False):
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="binding", access_key_hash="x",
        created_at=utcnow(), created_by="test", cap_helper_write=cap,
        cap_registry_read="allow", pass_through=pass_through,
        permissions=PermissionTree(domains={
            domain: PermissionNode(state="GREEN") for domain in STORAGE_HELPER_TYPES
        }),
    )


def _data():
    return PhoenixData(
        store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(),
        versions=VersionStore(),
    )


@pytest.fixture
async def pair(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    rows = [await async_ws_command(hass, "input_boolean/create", {"name": name})
            for name in ("Binding A", "Binding B")]
    await hass.async_block_till_done()
    return rows


@pytest.mark.parametrize("helper_type,operation,field", [
    (kind, operation, field)
    for kind in sorted(STORAGE_HELPER_TYPES)
    for operation in ("create", "edit")
    for field in ("id", "type", "target")
    if (kind, operation, field) != ("tag", "create", "target")
])
async def test_reserved_fields_refuse_before_approval(hass, helper_type, operation, field):
    field = f"{helper_type}_id" if field == "target" else field
    value = 99 if field == "id" else (
        f"{helper_type}/{'create' if operation == 'create' else 'update'}"
        if field == "type" else "redirected"
    )
    args = {"helper_type": helper_type, "helper_id": "selected",
            "config": {"name": "Unchanged", field: value}}
    gate, dispatch = AsyncMock(), AsyncMock()
    data = _data()
    with patch.object(helper, "_gate", gate), patch.object(helper, "async_ws_command", dispatch):
        content, outcome, _ = await _call_tool(
            f"{operation}_helper", args, _key("confirm"), hass, data,
        )
    assert outcome == "invalid_request", content
    gate.assert_not_awaited()
    dispatch.assert_not_awaited()
    assert data.config_recovery.records() == []


@pytest.mark.parametrize("operation", ("create", "edit"))
@pytest.mark.parametrize("field", ("id", "type", "input_boolean_id", "tag_id"))
async def test_reserved_fields_refuse_in_approved_executor(hass, pair, operation, field):
    data = _data()
    value = 99 if field == "id" else (
        f"input_boolean/{'create' if operation == 'create' else 'update'}"
        if field == "type" else pair[1]["id"]
    )
    args = {"helper_type": "input_boolean", "helper_id": pair[0]["id"],
            "config": {"name": "Unchanged", field: value}}
    before = await async_ws_command(hass, "input_boolean/list", {})
    content, outcome, _ = await async_execute_approved_tool(
        f"{operation}_helper", args, _key("confirm"), hass, data,
    )
    assert outcome == "invalid_request", content
    assert await async_ws_command(hass, "input_boolean/list", {}) == before
    assert data.config_recovery.records() == []


@pytest.mark.parametrize("cap,pass_through", (("allow", False), ("confirm", False), ("confirm", True)))
async def test_cross_target_refusal_writes_nothing(hass, pair, cap, pass_through):
    data = _data()
    before = await async_ws_command(hass, "input_boolean/list", {})
    content, outcome, _ = await _call_tool(
        "edit_helper", {"helper_type": "input_boolean", "helper_id": pair[0]["id"],
                        "config": {"name": "Redirected", "input_boolean_id": pair[1]["id"]}},
        _key(cap, pass_through=pass_through), hass, data,
    )
    assert outcome == "invalid_request", content
    assert await async_ws_command(hass, "input_boolean/list", {}) == before
    assert data.config_recovery.records() == []


async def test_capability_denial_precedes_reserved_field_diagnostics(hass):
    content, outcome, _ = await _call_tool("create_helper", {
        "helper_type": "input_boolean", "config": {"name": "Refused", "id": 1},
    }, _key("deny"), hass, _data())
    assert outcome == "denied"
    assert "request fields" not in content["content"][0]["text"]


async def test_normalized_helper_id_matches_recovery_and_readback(hass, pair):
    data = _data()
    content, outcome, _ = await _call_tool("edit_helper", {
        "helper_type": "input_boolean", "helper_id": f" {pair[0]['id']} ",
        "config": {"name": "Normalized A"},
    }, _key(), hass, data)
    assert outcome == "allowed", content
    record = data.config_recovery.records()[0]
    assert record["phase"] == "verified"
    assert record["before"]["helper_id"] == pair[0]["id"]
    assert record["expected"]["config"]["id"] == pair[0]["id"]


async def test_confirm_edit_delete_and_recovery_bind_same_identity(hass, pair):
    data, key = _data(), _key("confirm")
    for operation in ("edit", "delete"):
        args = {"helper_type": "input_boolean", "helper_id": pair[0]["id"]}
        if operation == "edit":
            args["config"] = {"name": "Reviewed A"}
        gate = AsyncMock(return_value=({}, "pending_approval", "approval"))
        with patch.object(helper, "_gate", gate):
            _, outcome, _ = await _call_tool(f"{operation}_helper", args, key, hass, data)
        assert outcome == "pending_approval"
        saved = deepcopy(gate.await_args.kwargs["args"])
        content, outcome, _ = await async_execute_approved_tool(
            f"{operation}_helper", saved, key, hass, data,
        )
        assert outcome == "allowed", content
        record = data.config_recovery.records()[0]
        assert record["phase"] == "verified"
        assert record["resource"] == f"helper:input_boolean:{pair[0]['id']}"
        for image in (record["before"], record["expected"]):
            assert image["helper_id"] == pair[0]["id"]
            if image["config"] is not None:
                assert image["config"]["id"] == pair[0]["id"]
        rows = await async_ws_command(hass, "input_boolean/list", {})
        assert next(r for r in rows if r["id"] == pair[1]["id"]) == pair[1]
        if operation == "edit":
            assert next(r for r in rows if r["id"] == pair[0]["id"])["name"] == "Reviewed A"
        else:
            assert not any(r["id"] == pair[0]["id"] for r in rows)


@pytest.mark.parametrize("approved", (False, True))
async def test_override_cannot_reach_mesa_protected_target(hass, pair, approved):
    data, key = _data(), _key("confirm")
    data.store.get_settings.return_value = GlobalSettings(mesa_mode="enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    target = er.async_get(hass).async_get_entity_id("input_boolean", "input_boolean", pair[1]["id"])
    data.mesa.store.set(target, SemanticProfile.from_dict(target, {
        "semantic_profile": {"operational_boundaries": {
            "control_mode": "read_only", "control_reason": "Exact target binding",
        }},
    }, default_origin=MetadataOrigin.USER))
    args = {"helper_type": "input_boolean", "helper_id": pair[0]["id"],
            "config": {"name": "Reviewed A"}}
    gate = AsyncMock(return_value=({}, "pending_approval", "approval"))
    if approved:
        with patch.object(helper, "_gate", gate):
            _, outcome, _ = await _call_tool("edit_helper", args, key, hass, data)
        assert outcome == "pending_approval"
        args = deepcopy(gate.await_args.kwargs["args"])
    args["config"]["input_boolean_id"] = pair[1]["id"]
    before = await async_ws_command(hass, "input_boolean/list", {})
    invoke = async_execute_approved_tool if approved else _call_tool
    content, outcome, _ = await invoke("edit_helper", args, key, hass, data)
    assert outcome == "invalid_request", content
    assert await async_ws_command(hass, "input_boolean/list", {}) == before
    assert data.config_recovery.records() == []
    # The real target's policy still refuses a normally addressed edit.
    _, outcome, _ = await _call_tool("edit_helper", {
        "helper_type": "input_boolean", "helper_id": pair[1]["id"],
        "config": {"name": "Protected"},
    }, key, hass, data)
    assert outcome == "denied"


@pytest.mark.parametrize("fault", ("target", "snapshot", "type", "id", "foreign_target", "delete_fields"))
async def test_protected_boundary_refuses_mismatched_envelopes(hass, pair, fault):
    data = _data()
    args = {"helper_type": "input_boolean", "helper_id": pair[0]["id"], "config": {"name": "Changed"}}
    before = pair[1] if fault == "snapshot" else pair[0]
    payload = {"input_boolean_id": pair[1]["id"] if fault == "target" else pair[0]["id"], "name": "Changed"}
    if fault in ("type", "id"):
        payload[fault] = "redirected"
    if fault == "foreign_target":
        payload["tag_id"] = "redirected"
    dispatch = AsyncMock()
    with patch.object(helper, "async_ws_command", dispatch), pytest.raises(ConfigurationRecoveryError):
        await helper._protected_storage_helper_write(
            args, _key(), hass, data, before, payload, deleting=fault == "delete_fields",
        )
    dispatch.assert_not_awaited()
    assert data.config_recovery.records() == []


async def test_restore_snapshot_cannot_redirect_existing_helper(hass, pair, hass_admin_user):
    data = _data()
    record = SimpleNamespace(resource_type="helper", resource_id=f"input_boolean:{pair[0]['id']}",
                             before=None, after={"id": pair[0]["id"], "input_boolean_id": pair[1]["id"], "name": "Redirected"})
    before = await async_ws_command(hass, "input_boolean/list", {})
    content, outcome, _ = await async_restore_version(record, hass_admin_user.id, hass, data)
    assert outcome == "invalid_request", content
    assert await async_ws_command(hass, "input_boolean/list", {}) == before
    assert data.config_recovery.records() == []


async def test_tag_creation_keeps_native_stable_id(hass, hass_admin_user):
    assert await async_setup_component(hass, "tag", {"tag": {}})
    data = _data()
    content, outcome, _ = await _call_tool("create_helper", {
        "helper_type": "tag", "config": {"tag_id": "binding-tag", "name": "Binding tag"},
    }, _key(), hass, data)
    assert outcome == "allowed", content
    rows = await async_ws_command(hass, "tag/list", {})
    assert next(r for r in rows if r["id"] == "binding-tag")["name"] == "Binding tag"
