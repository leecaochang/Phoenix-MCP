"""Location lifecycle, whole-scope authority, drift refusal and fresh label merges."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp import mcp_view as view
from custom_components.phoenix_mcp.const import DOMAIN, LOCATION_REGISTRY_BINDING
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.request_authority import (
    AuthorityChangedError, RequestAuthority, policy_version, request_authority,
)
from custom_components.phoenix_mcp.token_store import (
    GlobalSettings, PermissionNode, PermissionTree, TokenRecord, TokenStore,
)
from custom_components.phoenix_mcp.tools import locations


@pytest.fixture
def location_env(hass):
    owner = MockConfigEntry(domain="test_integration", entry_id="location-owner")
    owner.add_to_hass(hass)
    floor = fr.async_get(hass).async_create("Location floor", level=1)
    area = ar.async_get(hass).async_create("Location area", floor_id=floor.floor_id)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={(owner.domain, "location-device")},
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    entity = er.async_get(hass).async_get_or_create(
        "light", owner.domain, "location-light", config_entry=owner, device_id=device.id,
    )
    hass.states.async_set(entity.entity_id, "off")
    labels = [lr.async_get(hass).async_create(name).label_id for name in ("first", "second")]
    token = TokenRecord(
        id="location-token", name="Location test", token_hash="hash", created_at=utcnow(),
        created_by="test", cap_registry_write="allow", cap_registry_read="allow",
        permissions=PermissionTree(devices={device.id: PermissionNode(state="GREEN")}),
    )
    store = MagicMock(spec=TokenStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    store.get_pending_approvals.return_value = []
    store.list_tokens.return_value = []
    store.get_token_by_hash.return_value = token
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), hass=hass)
    hass.data[DOMAIN] = data
    yield token, data, floor.floor_id, area.id, device.id, entity.entity_id, labels
    if data.sensor_flush_cancel is not None:
        data.sensor_flush_cancel()


async def _call(hass, env, *, kind="area", operation="update", changes=None, location_id=None):
    token, data, floor_id, area_id, *_ = env
    args = {"kind": kind, "operation": operation}
    if operation != "create":
        args["location_id"] = location_id or (area_id if kind == "area" else floor_id)
    if changes is not None:
        args["changes"] = changes
    return await view._call_tool("set_area_or_floor", args, token, hass, data)


def _body(result):
    assert result[1] == "allowed", result
    return json.loads(result[0]["content"][0]["text"])


async def test_area_labels_patch_replace_clear_and_restore(hass, location_env):
    token, data, _, area_id, _, _, labels = location_env
    _body(await _call(hass, location_env, changes={"add_labels": [labels[0]]}))
    original = data.versions.list_for("area", area_id)[0]
    _body(await _call(hass, location_env, changes={"add_labels": [labels[1]]}))
    assert ar.async_get(hass).async_get_area(area_id).labels == set(labels)
    _body(await _call(hass, location_env, changes={"remove_labels": [labels[0]]}))
    assert ar.async_get(hass).async_get_area(area_id).labels == {labels[1]}
    _body(await _call(hass, location_env, changes={"labels": []}))
    assert not ar.async_get(hass).async_get_area(area_id).labels
    _body(await view.async_restore_version(original, "admin", hass, data, side="after"))
    assert ar.async_get(hass).async_get_area(area_id).labels == {labels[0]}
    assert data.versions.list_for("area", area_id)[0].action == "rollback"
    # Replacement is distinct from additive assignment.
    _body(await _call(hass, location_env, changes={"labels": [labels[1]]}))
    assert ar.async_get(hass).async_get_area(area_id).labels == {labels[1]}


@pytest.mark.parametrize("changes", [
    {}, {"name": None}, {"name": 1}, {"name": ""}, {"name": "x" * 256},
    {"aliases": "room"}, {"aliases": [None]}, {"labels": "first"},
    {"labels": [None]}, {"labels": ["missing"]}, {"labels": ["missing"] * 101},
    {"labels": [], "add_labels": []}, {"add_labels": ["first"], "remove_labels": ["first"]},
    {"level": 2}, {"floor_id": "missing"}, {"floor_id": False}, {"icon": []},
    {"picture": "unknown"}, {"location_id": "hidden"},
])
async def test_malformed_area_changes_have_no_effect(hass, location_env, changes):
    _, data, _, area_id, *_ = location_env
    before = ar.async_get(hass).async_get_area(area_id)
    result = await _call(hass, location_env, changes=changes)
    assert result[1] in {"invalid_request", "denied"}, result
    assert ar.async_get(hass).async_get_area(area_id) == before
    assert not data.versions.list_for("area", area_id)
    assert not data.store.get_pending_approvals()


@pytest.mark.parametrize("changes", [
    {"labels": []}, {"add_labels": []}, {"remove_labels": []}, {"floor_id": None},
    {"level": True}, {"level": 1.5}, {"level": 2**40},
])
async def test_floor_rejects_labels_area_fields_and_noninteger_level(hass, location_env, changes):
    assert (await _call(hass, location_env, kind="floor", changes=changes))[1] == "invalid_request"


async def test_floor_metadata_and_area_floor_assignment(hass, location_env):
    _body(await _call(hass, location_env, kind="floor", changes={
        "name": "Renamed floor", "icon": "mdi:home", "aliases": ["Upper"], "level": None,
    }))
    _body(await _call(hass, location_env, changes={"floor_id": None, "aliases": ["Office"], "icon": None}))
    token, _, floor_id, area_id, *_ = location_env
    assert ar.async_get(hass).async_get_area(area_id).floor_id is None
    # An empty destination floor has no scoped grant.
    assert (await _call(hass, location_env, changes={"floor_id": floor_id}))[1] == "denied"
    token.pass_through = True
    _body(await _call(hass, location_env, changes={"floor_id": floor_id}))


@pytest.mark.parametrize("kind", ["area", "floor"])
async def test_create_empty_discovery_duplicate_delete_and_no_recreation(hass, location_env, kind):
    token, data, *_ = location_env
    assert (await _call(hass, location_env, kind=kind, operation="create", changes={"name": "New"}))[1] == "denied"
    token.pass_through = True
    created = _body(await _call(hass, location_env, kind=kind, operation="create", changes={"name": "New"}))
    location_id = created["location_id"]
    listed = _body(await view._call_tool(f"list_{kind}s", {}, token, hass, data))
    assert any(row[f"{kind}_id"] == location_id for row in listed[f"{kind}s"])
    assert (await _call(hass, location_env, kind=kind, operation="create", changes={"name": " new "}))[1] == "invalid_request"
    _body(await _call(hass, location_env, kind=kind, operation="delete", location_id=location_id))
    record = data.versions.list_for(kind, location_id)[0]
    assert record.before["restorable"] is False
    assert (await view.async_restore_version(record, "admin", hass, data, side="before"))[1] == "invalid_request"


@pytest.mark.parametrize("kind", ["area", "floor"])
@pytest.mark.parametrize("obstruction", ["entity_red", "device_red", "entity_only", "empty_device", "disabled", "empty_area"])
async def test_whole_membership_authority(hass, location_env, kind, obstruction):
    token, _, floor_id, area_id, device_id, entity_id, _ = location_env
    if obstruction == "entity_red":
        token.permissions.entities[entity_id] = PermissionNode(state="RED")
    elif obstruction == "device_red":
        token.permissions.devices[device_id] = PermissionNode(state="RED")
    elif obstruction == "entity_only":
        token.permissions.devices.clear()
        token.permissions.entities[entity_id] = PermissionNode(state="GREEN")
    elif obstruction == "empty_device":
        owner = MockConfigEntry(domain="test_integration", entry_id="ungranted-owner")
        owner.add_to_hass(hass)
        ungranted = dr.async_get(hass).async_get_or_create(
            config_entry_id=owner.entry_id, identifiers={(owner.domain, "ungranted")},
        )
        dr.async_get(hass).async_update_device(ungranted.id, area_id=area_id)
    elif obstruction == "disabled":
        token.permissions.devices.clear()
        token.permissions.entities[entity_id] = PermissionNode(state="GREEN")
        er.async_get(hass).async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
        hass.states.async_remove(entity_id)
    elif obstruction == "empty_area":
        if kind == "area":
            dr.async_get(hass).async_update_device(device_id, area_id=None)
        else:
            ar.async_get(hass).async_create("Empty child", floor_id=floor_id)
    result = await _call(hass, location_env, kind=kind, changes={"icon": "mdi:home"})
    assert result[1] == "denied", result
    hidden = await _call(hass, location_env, kind=kind, location_id="missing", changes={"icon": "mdi:home"})
    assert hidden[0] == result[0]


async def test_capability_denial_before_validation_and_private_binding_rejected(hass, location_env):
    token, data, *_ = location_env
    token.cap_registry_write = "deny"
    assert (await view._call_tool("set_area_or_floor", {"kind": []}, token, hass, data))[1] == "denied"
    token.cap_registry_write = "allow"
    result = await view._call_tool("set_area_or_floor", {
        "kind": "area", "operation": "update", "location_id": location_env[3],
        "changes": {"icon": "mdi:home"}, LOCATION_REGISTRY_BINDING: "forged",
    }, token, hass, data)
    assert result[1] == "invalid_request"


@pytest.mark.parametrize("drift", [None, "membership", "metadata", "permission", "recreated", "label_removed"])
async def test_confirm_binds_identity_membership_metadata_and_references(hass, location_env, drift):
    token, data, _, area_id, _, entity_id, labels = location_env
    token.cap_registry_write = "confirm"
    pending = await _call(hass, location_env, changes={"add_labels": [labels[0]]})
    assert pending[1] == "pending_approval", pending
    saved = data.store.get_pending_approvals()[0]["args"]
    assert LOCATION_REGISTRY_BINDING in saved
    if drift == "membership":
        member = er.async_get(hass).async_get_or_create("light", "test_integration", "new")
        er.async_get(hass).async_update_entity(member.entity_id, area_id=area_id)
    elif drift == "metadata":
        ar.async_get(hass).async_update(area_id, icon="mdi:office-building")
    elif drift == "permission":
        token.permissions.entities[entity_id] = PermissionNode(state="RED")
    elif drift == "recreated":
        ar.async_get(hass).async_delete(area_id)
        new = ar.async_get(hass).async_create("Location area")
        assert new.id == area_id
    elif drift == "label_removed":
        lr.async_get(hass).async_delete(labels[0])
    result = await view.async_execute_approved_tool("set_area_or_floor", saved, token, hass, data)
    assert result[1] == ("allowed" if drift is None else ("invalid_request" if drift == "label_removed" else "denied")), result
    if drift:
        assert not data.versions.list_for("area", area_id)


async def test_concurrent_labels_commit_and_capture_before_history_wait(hass, location_env):
    token, data, _, area_id, _, _, labels = location_env
    entered, release = asyncio.Event(), asyncio.Event()
    captures = []

    async def record(*args, **kwargs):
        captures.append(kwargs)
        if len(captures) == 1:
            entered.set()
            await release.wait()

    with patch.object(locations, "_record_version", side_effect=record):
        first = asyncio.create_task(_call(hass, location_env, changes={"add_labels": [labels[0]]}))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = await _call(hass, location_env, changes={"add_labels": [labels[1]]})
            assert _body(second)["updated"]["labels"] == sorted(labels)
        finally:
            release.set()
            first_result = await first
    assert _body(first_result)["updated"]["labels"] == [labels[0]]
    assert captures[0]["after"]["labels"] == [labels[0]]
    assert captures[1]["before"]["labels"] == [labels[0]]
    assert ar.async_get(hass).async_get_area(area_id).labels == set(labels)


@pytest.mark.parametrize("kind", ["area", "floor"])
async def test_delete_clears_reviewed_placement(hass, location_env, kind):
    _, data, floor_id, area_id, device_id, entity_id, _ = location_env
    er.async_get(hass).async_update_entity(entity_id, area_id=area_id)
    receipt = _body(await _call(hass, location_env, kind=kind, operation="delete"))
    assert receipt["restorable"] is False
    if kind == "area":
        assert dr.async_get(hass).async_get(device_id).area_id is None
        assert er.async_get(hass).async_get(entity_id).area_id is None
    else:
        assert ar.async_get(hass).async_get_area(area_id).floor_id is None
        assert fr.async_get(hass).async_get_floor(floor_id) is None
    assert len(data.versions.list_for(kind, area_id if kind == "area" else floor_id)) == 1


async def test_dispatch_readback_failure_is_uncertain_without_retry(hass, location_env):
    token, data, _, area_id, *_ = location_env
    registry = ar.async_get(hass)
    original = registry.async_update
    effects = []

    def failing(*args, **kwargs):
        effects.append(args)
        original(*args, **kwargs)
        raise RuntimeError("readback unavailable")

    with patch.object(registry, "async_update", side_effect=failing):
        result = await _call(hass, location_env, changes={"icon": "mdi:home"})
    assert result[1] == "invalid_request"
    assert json.loads(result[0]["content"][0]["text"])["status"] == "unknown"
    assert registry.async_get_area(area_id).icon == "mdi:home"
    assert len(effects) == 1
    assert not data.versions.list_for("area", area_id)


async def test_authority_loss_during_history_does_not_undo_commit(hass, location_env):
    token, data, _, area_id, *_ = location_env
    authority = RequestAuthority(hass=hass, data=data, token_id=token.id,
                                 token_hash=token.token_hash)
    ctx = request_authority.set(authority)
    policy_ctx = policy_version.set(token.settings_version)

    async def revoke(*args, **kwargs):
        data.store.get_token_by_hash.return_value = None

    try:
        with patch.object(locations, "_record_version", side_effect=revoke):
            with pytest.raises(AuthorityChangedError):
                await locations._execute_set_area_or_floor({
                    "kind": "area", "operation": "update", "location_id": area_id,
                    "changes": {"icon": "mdi:home"},
                }, token, hass, data)
    finally:
        request_authority.reset(ctx)
        policy_version.reset(policy_ctx)
    assert ar.async_get(hass).async_get_area(area_id).icon == "mdi:home"


@pytest.mark.parametrize("mode", ["prohibited", "read_only", "confirm"])
async def test_inherited_area_mesa_identity_actions(hass, location_env, mode):
    token, data, _, area_id, *_ = location_env
    data.store.get_settings.return_value.mesa_mode = "enforced"
    data.mesa = await async_setup_mesa(hass, "enforced")
    profile = SemanticProfile.from_dict(area_id, {"semantic_profile": {
        "operational_boundaries": {"control_mode": mode, "control_reason": "Location boundary"},
    }}, default_origin=MetadataOrigin.USER)
    data.mesa.store.set_area_profile(area_id, profile)
    result = await _call(hass, location_env, changes={"name": "Renamed area"})
    assert result[1] == ("pending_approval" if mode == "confirm" else "denied"), result
    if mode == "confirm":
        saved = data.store.get_pending_approvals()[0]["args"]
        _body(await view.async_execute_approved_tool("set_area_or_floor", saved, token, hass, data))
    assert ar.async_get(hass).async_get_area(area_id).name == ("Renamed area" if mode == "confirm" else "Location area")
