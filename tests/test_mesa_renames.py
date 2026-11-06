"""A renamed entity keeps its MESA profile, whatever was listening when it happened.

Runs through Home Assistant's real entity registry and the stored document, so each case ends
with the same question: does the new id meet the policy the old id had?
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import _listen_for_registry_changes
from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.const import DOMAIN, MESA_STORAGE_KEY
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.durable_storage import DurableStorageError
from custom_components.phoenix_mcp.mesa import async_setup_mesa, evaluate_service_call
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile

_ALWAYS = {"entity": "sun.sun", "operator": "neq", "value": "impossible"}
_FULL_LIMIT = {
    "id": "cap", "predicate": _ALWAYS, "human_reason": "too bright",
    "limit": {"service": "light.turn_on", "parameter": "brightness", "max_value": 50},
}


def _profile(key: str, mode: str | None = "prohibited", enforcement: str | None = "enforced", **boundaries):
    declared = {"control_reason": "fixture", **boundaries}
    if mode is not None:
        declared["control_mode"] = mode
    if enforcement is not None:
        declared["enforcement_mode"] = enforcement
    return SemanticProfile.from_dict(
        key, {"semantic_profile": {"operational_boundaries": declared}}, default_origin=MetadataOrigin.USER,
    )


def _access_key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="renames", access_key_hash="x", created_at=utcnow(), created_by="t",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
    )


async def _data(hass, mode: str = "enforced", *, listen: bool = True) -> tuple[PhoenixData, MockConfigEntry]:
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.async_rename_entity = AsyncMock()
    store.get_settings.return_value = GlobalSettings(mesa_mode=mode)
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=await async_setup_mesa(hass, mode))
    data.versions = MagicMock()
    data.versions.async_rename_resource = AsyncMock()
    hass.data[DOMAIN] = data
    hass.states.async_set("sun.sun", "above_horizon")
    entry = MockConfigEntry(domain=DOMAIN, entry_id="phoenix")
    entry.add_to_hass(hass)
    if listen:
        _listen_for_registry_changes(hass, entry, data)
    return data, entry


def _entity(hass, object_id: str) -> er.RegistryEntry:
    entry = MockConfigEntry(domain="test", entry_id=f"entry-{object_id}")
    entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "light", "test", f"unique-{object_id}", config_entry=entry, suggested_object_id=object_id,
    )


def _verdict(data: PhoenixData, entity_id: str, service_data: dict | None = None):
    return evaluate_service_call(
        data.mesa, data.store.get_settings().mesa_mode, _access_key(), [entity_id],
        domain="light", service="turn_on", service_data=service_data or {}, session_id="s",
    )


def _rename(hass, old: str, new: str) -> None:
    er.async_get(hass).async_update_entity(old, new_entity_id=new)


async def _stored_prohibited(hass, data: PhoenixData, key: str = "light.a") -> None:
    data.mesa.store.set(key, _profile(key))
    async with data.mesa.lock:
        await data.mesa.async_save()


async def _restart(hass, data: PhoenixData, entry: MockConfigEntry) -> None:
    """What a new process does: read the file, then start listening."""
    data.mesa = await async_setup_mesa(hass, "enforced")
    _listen_for_registry_changes(hass, entry, data)
    await hass.async_block_till_done()


@pytest.mark.parametrize(("old", "resident", "kept", "refused"), [
    # Read-only is vetoed in advisory mode, where Prohibited only warns.
    (("read_only", None), ("prohibited", None), "read_only", True),
    (("prohibited", None), ("read_only", None), "read_only", True),
    # Enforced on the profile outranks a stricter name that only warns.
    (("confirm", "enforced"), ("prohibited", "advisory"), "prohibited", True),
    (("prohibited", "enforced"), ("confirm", "advisory"), "prohibited", True),
], ids=["read_only_moves", "read_only_stays", "enforced_confirm_moves", "enforced_prohibited_moves"])
async def test_a_collision_keeps_what_phoenix_would_refuse_in_advisory_mode(hass, old, resident, kept, refused):
    data, _entry = await _data(hass, "advisory")
    _entity(hass, "a")
    data.mesa.store.set("light.a", _profile("light.a", *old))
    data.mesa.store.set("light.b", _profile("light.b", *resident))
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    assert bool(_verdict(data, "light.b").blocked) is refused
    assert data.mesa.store.get("light.b").operational_boundaries.control_mode.value == kept
    assert data.mesa.store.get("light.a") is not None


async def test_a_collision_turns_no_refusal_into_an_approval_when_the_mode_changes(hass):
    """An advisory Prohibited against an enforced Confirm: each is stricter under one mode."""
    data, _entry = await _data(hass, "advisory")
    _entity(hass, "a")
    data.mesa.store.set("light.a", _profile("light.a", "prohibited", "advisory"))
    data.mesa.store.set("light.b", _profile("light.b", "confirm", "enforced"))
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    assert _verdict(data, "light.b").blocked
    data.store.get_settings.return_value = GlobalSettings(mesa_mode="enforced")
    assert _verdict(data, "light.b").blocked


@pytest.mark.parametrize("limit_on", ["old", "resident"])
async def test_a_collision_keeps_a_declared_limit_from_either_side(hass, limit_on):
    data, _entry = await _data(hass, "advisory")
    _entity(hass, "a")
    limited = {"declared_limits": [_FULL_LIMIT]}
    data.mesa.store.set("light.a", _profile("light.a", "autonomous", None, **(limited if limit_on == "old" else {})))
    data.mesa.store.set("light.b", _profile("light.b", "autonomous", None, **(limited if limit_on == "resident" else {})))
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    assert any("exceeds" in warning for warning in _verdict(data, "light.b", {"brightness": 200}).warnings)


async def test_a_collision_keeps_both_limits_under_their_own_ids(hass):
    data, _entry = await _data(hass, "enforced")
    _entity(hass, "a")
    other = {**_FULL_LIMIT, "limit": {**_FULL_LIMIT["limit"], "parameter": "transition", "max_value": 5}}
    data.mesa.store.set("light.a", _profile("light.a", "autonomous", declared_limits=[_FULL_LIMIT]))
    data.mesa.store.set("light.b", _profile("light.b", "autonomous", declared_limits=[other]))
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    limits = data.mesa.store.get("light.b").operational_boundaries.declared_limits
    assert len({limit["id"] for limit in limits}) == 2
    assert _verdict(data, "light.b", {"brightness": 200}).blocked
    assert _verdict(data, "light.b", {"transition": 9}).blocked


async def test_a_collision_with_nothing_stricter_is_a_plain_move(hass):
    data, _entry = await _data(hass)
    _entity(hass, "a")
    data.mesa.store.set("light.a", _profile("light.a"))
    data.mesa.store.set("light.b", _profile("light.b", "autonomous", None))
    moved = data.mesa.backend.read("light.a")
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    assert data.mesa.backend.read("light.b")["semantic_profile"] == moved["semantic_profile"]


@pytest.mark.parametrize("damaged", ["old", "resident"])
async def test_a_collision_leaves_an_unreadable_document_where_it_refuses(hass, damaged):
    data, _entry = await _data(hass)
    _entity(hass, "a")
    bad = {"semantic_profile": {"operational_boundaries": {"control_mode": "bogus"}}}
    data.mesa.store.set("light.a", _profile("light.a", "autonomous"))
    data.mesa.store.set("light.b", _profile("light.b", "autonomous"))
    data.mesa.backend.write("light.a" if damaged == "old" else "light.b", bad)
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    assert _verdict(data, "light.b").blocked[0][1] == "policy_unavailable"


async def test_a_rename_with_the_runtime_failed_is_carried_when_it_returns(hass):
    data, entry = await _data(hass)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    data.mesa = None
    data.mesa_setup_failed = True
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    await _restart(hass, data, entry)
    assert _verdict(data, "light.b").blocked


async def test_a_rename_with_the_entry_unloaded_is_carried_at_setup(hass):
    data, entry = await _data(hass)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    await entry._async_process_on_unload(hass)
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    await _restart(hass, data, entry)
    assert _verdict(data, "light.b").blocked
    assert data.mesa.backend.list_keys() == ["light.b"]


async def test_an_unload_does_not_drop_a_rename_that_is_waiting_for_the_lock(hass, hass_storage):
    data, entry = await _data(hass)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    await data.mesa.lock.acquire()
    _rename(hass, "light.a", "light.b")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await entry._async_process_on_unload(hass)
    data.mesa.lock.release()
    await hass.async_block_till_done()
    data.store.async_rename_entity.assert_awaited_once_with("light.a", "light.b")
    assert list(hass_storage[MESA_STORAGE_KEY]["data"]["profiles"]) == ["light.b"]


async def test_a_persist_lost_with_the_process_is_carried_at_the_next_start(hass, hass_storage):
    data, entry = await _data(hass, listen=False)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    assert list(hass_storage[MESA_STORAGE_KEY]["data"]["profiles"]) == ["light.a"]
    await _restart(hass, data, entry)
    assert _verdict(data, "light.b").blocked
    assert list(hass_storage[MESA_STORAGE_KEY]["data"]["profiles"]) == ["light.b"]


async def test_a_failed_save_holds_then_a_restart_carries_again(hass, hass_storage):
    data, entry = await _data(hass)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    with patch.object(data.mesa.ha_store, "async_save", AsyncMock(side_effect=DurableStorageError("full"))):
        _rename(hass, "light.a", "light.b")
        await hass.async_block_till_done()
    assert data.mesa.storage.rename_save_failed is True
    assert _verdict(data, "light.b").blocked
    await _restart(hass, data, entry)
    assert _verdict(data, "light.b").blocked
    assert data.mesa.storage.rename_save_failed is False


async def test_a_reload_saves_again_what_a_failed_save_left_behind(hass, hass_storage):
    data, entry = await _data(hass)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    with patch.object(data.mesa.ha_store, "async_save", AsyncMock(side_effect=DurableStorageError("full"))):
        _rename(hass, "light.a", "light.b")
        await hass.async_block_till_done()
    _listen_for_registry_changes(hass, entry, data)
    await hass.async_block_till_done()
    assert data.mesa.storage.rename_save_failed is False
    assert list(hass_storage[MESA_STORAGE_KEY]["data"]["profiles"]) == ["light.b"]


async def test_the_registry_map_is_stored_beside_the_profiles_and_needs_no_save_when_current(hass, hass_storage):
    data, entry = await _data(hass, listen=False)
    registered = _entity(hass, "a")
    await _stored_prohibited(hass, data)
    stored = hass_storage[MESA_STORAGE_KEY]["data"]
    assert stored["registry_ids"] == {"light.a": registered.id}
    with patch.object(data.mesa.ha_store, "async_save", AsyncMock()) as save:
        await _restart(hass, data, entry)
    save.assert_not_awaited()


async def test_a_file_from_before_the_map_gains_it_at_setup(hass, hass_storage):
    registered = _entity(hass, "a")
    hass_storage[MESA_STORAGE_KEY] = {
        "version": 1, "minor_version": 1, "key": MESA_STORAGE_KEY,
        "data": {"profiles": {"light.a": _profile("light.a").to_dict()}, "dismissed_suggestions": []},
    }
    await _data(hass)
    await hass.async_block_till_done()
    assert hass_storage[MESA_STORAGE_KEY]["data"]["registry_ids"] == {"light.a": registered.id}


async def test_an_orphan_left_by_a_collision_is_not_carried_again(hass):
    data, entry = await _data(hass)
    _entity(hass, "a")
    data.mesa.store.set("light.a", _profile("light.a"))
    data.mesa.store.set("light.b", _profile("light.b", "autonomous", None))
    async with data.mesa.lock:
        await data.mesa.async_save()
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    orphan = data.mesa.backend.read("light.a")
    at_new_id = data.mesa.backend.read("light.b")
    await _restart(hass, data, entry)
    assert data.mesa.backend.read("light.a") == orphan
    assert data.mesa.backend.read("light.b") == at_new_id


async def test_a_profile_added_at_the_new_id_before_the_carry_does_not_lose_the_old_policy(hass, hass_storage):
    """The missed rename is still found, and meets that profile as a collision."""
    data, entry = await _data(hass, listen=False)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    _rename(hass, "light.a", "light.b")
    await hass.async_block_till_done()
    data.mesa.store.set("light.b", _profile("light.b", "autonomous", None))
    async with data.mesa.lock:
        await data.mesa.async_save()
    await _restart(hass, data, entry)
    assert _verdict(data, "light.b").blocked


async def test_a_rename_during_a_save_survives_its_own_failed_save(hass, hass_storage):
    data, _entry = await _data(hass)
    _entity(hass, "a")
    await _stored_prohibited(hass, data)
    writing, finish = asyncio.Event(), asyncio.Event()
    real_save = data.mesa.ha_store.async_save
    calls = 0

    async def _save(document):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise DurableStorageError("full")
        writing.set()
        await finish.wait()
        await real_save(document)

    async def _operator_save():
        async with data.mesa.lock:
            await data.mesa.async_save()

    with patch.object(data.mesa.ha_store, "async_save", _save):
        saving = hass.async_create_task(_operator_save())
        await writing.wait()
        _rename(hass, "light.a", "light.b")
        await asyncio.sleep(0)
        assert data.mesa.backend.list_keys() == ["light.b"]
        finish.set()
        await saving
        await hass.async_block_till_done()

    assert data.mesa.backend.list_keys() == ["light.b"]
    assert _verdict(data, "light.b").blocked
    assert data.mesa.storage.durable_profiles.keys() == {"light.b"}
