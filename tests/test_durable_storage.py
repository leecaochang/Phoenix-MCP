"""Real HA writer faults cannot acknowledge an unwritten execution barrier."""

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

from custom_components.phoenix_mcp.agent_recovery import AgentRecovery, RecoveryRequired
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery
from custom_components.phoenix_mcp.durable_storage import DurableStorageError, DurableStore
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.tools.calendar import CalendarWriteJournal


# Capture before the HA fixture installs its in-memory storage replacement.
_REAL_WRITE = Store._async_write_data
_REAL_LOAD = Store._async_load


@pytest.fixture(autouse=True)
def real_storage(hass):
    """Qualify the installed HA writer, not the harness's simulated persistence."""
    with patch.object(Store, "_async_write_data", _REAL_WRITE), patch.object(Store, "_async_load", _REAL_LOAD):
        yield


@pytest.mark.parametrize("failure", [WriteError("disk unavailable"), SerializationError("unsupported value")])
@pytest.mark.parametrize("owner", ["configuration", "agent", "calendar", "access_key"])
async def test_critical_storage_raises_when_real_ha_writer_fails(hass, failure, owner):
    """Patch only HA's final writer, preserving the swallowing handler under test."""
    store = DurableStore(hass, 1, "critical-test", atomic_writes=True)
    with patch.object(Store, "_write_prepared_data", side_effect=failure):
        # Positive reproduction: ordinary HA storage logs this fault and returns.
        ordinary = Store(hass, 1, "ordinary-control")
        await ordinary.async_save({"claim": "not-written"})
        assert not Path(ordinary.path).exists()
        if owner == "configuration":
            recovery = ConfigurationRecovery(store)
            with pytest.raises(DurableStorageError):
                await recovery.begin("resource", "delete", "access_key", {})
            assert recovery.records()[0]["phase"] == "prepared"
            assert not recovery.records()[0]["active"]
        elif owner == "agent":
            recovery = AgentRecovery(store)
            with pytest.raises(RecoveryRequired, match="nothing was dispatched"):
                await recovery.begin("access_key", "call_service", "request")
            assert recovery.blocks("access_key")
        elif owner == "calendar":
            journal = CalendarWriteJournal(hass)
            journal.records = [{"receipt_id": "pending", "status": "outcome_unknown"}]
            with pytest.raises(DurableStorageError):
                await journal.save()
        else:
            access_keys = await AccessKeyStore.async_create(hass)
            with pytest.raises(DurableStorageError):
                await access_keys.async_create_access_key("test", "admin")
        assert not Path(store.path).exists()


async def test_critical_store_writes_reloadable_record_and_rejects_deferred_writes(hass):
    store = DurableStore(hass, 1, "durable-success", atomic_writes=True)
    await store.async_save({"claim": "before-dispatch"})
    assert Path(store.path).is_file()
    fresh = DurableStore(hass, 1, "durable-success")
    assert await fresh.async_load() == {"claim": "before-dispatch"}
    readonly = DurableStore(hass, 1, "readonly", read_only=True)
    with pytest.raises(DurableStorageError):
        await readonly.async_save({"claim": "no"})
    hass.set_state(CoreState.stopping)
    with pytest.raises(DurableStorageError):
        await store.async_save({"claim": "deferred"})
    assert await fresh.async_load() == {"claim": "before-dispatch"}


async def test_a_failed_access_key_save_leaves_the_durable_policy_in_force(hass):
    """Mutators change memory before saving; a refused write must not keep the change.

    A broader permission tree, a new access key, a revocation and a settings
    change are each refused by the real writer, and the live state must match
    what storage still holds.
    """
    from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree

    store = await AccessKeyStore.async_create(hass)
    key, _ = await store.async_create_access_key("scoped", "admin")
    version = key.settings_version
    grant = PermissionTree(entities={"light.kitchen": PermissionNode(state="GREEN")})
    with patch.object(Store, "_write_prepared_data", side_effect=WriteError("disk full")):
        with pytest.raises(DurableStorageError):
            await store.async_set_permissions(key.id, grant)
        with pytest.raises(DurableStorageError):
            await store.async_create_access_key("second", "admin")
        with pytest.raises(DurableStorageError):
            await store.async_patch_settings(kill_switch=True)
        with pytest.raises(DurableStorageError):
            await store.async_archive_access_key(key.id, revoked=True)

    live = store.get_access_key_by_id(key.id)
    assert live is key, "a surviving record keeps its identity for in-flight holders"
    assert key.permissions.entities == {}
    assert key.settings_version == version
    assert [k.name for k in store.list_access_keys()] == ["scoped"]
    assert store.get_settings().kill_switch is False

    reloaded = await AccessKeyStore.async_create(hass)
    assert reloaded.get_access_key_by_id(key.id).permissions.entities == {}
    assert [k.name for k in reloaded.list_access_keys()] == ["scoped"]

    # The next good write still lands, and later failures restore to it.
    await store.async_set_permissions(key.id, grant)
    with patch.object(Store, "_write_prepared_data", side_effect=WriteError("disk full")):
        with pytest.raises(DurableStorageError):
            await store.async_set_permissions(key.id, PermissionTree())
    assert set(key.permissions.entities) == {"light.kitchen"}


class _HeldWriter:
    """HA's final writer, held on its executor thread until the test releases it."""

    def __init__(self, loop, *, fail=False):
        import asyncio
        import threading

        self.loop, self.fail = loop, fail
        self.entered = asyncio.Event()
        self.release = threading.Event()
        self.calls = 0
        self.real = Store._write_prepared_data

    def __call__(self, owner, mode, serialized):
        self.calls += 1
        if self.calls > 1:
            return self.real(owner, mode, serialized)
        self.loop.call_soon_threadsafe(self.entered.set)
        assert self.release.wait(5), "test did not release the writer"
        if self.fail:
            raise WriteError("held writer failed")
        return self.real(owner, mode, serialized)


async def _set_permissions(store, key, tree):
    async with store.async_lock:
        return await store.async_set_permissions(key.id, tree)


@pytest.mark.parametrize("first_write_fails", [False, True])
async def test_a_cancelled_save_still_settles_before_a_later_failure(hass, first_write_fails):
    """Cancelling the caller does not stop HA's executor write; the store must
    still learn its outcome, or a later failed grant has nothing to restore to."""
    import asyncio

    from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree

    store = await AccessKeyStore.async_create(hass)
    key, _ = await store.async_create_access_key("scoped", "admin")
    writer = _HeldWriter(asyncio.get_running_loop(), fail=first_write_fails)
    with patch.object(Store, "_write_prepared_data", lambda owner, mode, data: writer(owner, mode, data)):
        caller = asyncio.create_task(store.async_patch_settings(agentcli_scrollback_lines=321))
        await asyncio.wait_for(writer.entered.wait(), 2)
        caller.cancel()
        await asyncio.sleep(0)
        assert not caller.done(), "a cancelled caller keeps waiting for its write"
        writer.release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
    grant = PermissionTree(entities={"light.kitchen": PermissionNode(state="GREEN")})
    with patch.object(Store, "_write_prepared_data", side_effect=WriteError("disk full")):
        with pytest.raises(DurableStorageError):
            await _set_permissions(store, key, grant)
    assert key.permissions.entities == {}
    reloaded = await AccessKeyStore.async_create(hass)
    assert reloaded.get_access_key_by_id(key.id).permissions.entities == {}
    assert store.get_settings().agentcli_scrollback_lines == reloaded.get_settings().agentcli_scrollback_lines


async def test_an_older_write_cannot_land_after_a_newer_one(hass):
    """A cancelled grant still being written must not overwrite a later removal."""
    import asyncio

    from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree

    store = await AccessKeyStore.async_create(hass)
    key, _ = await store.async_create_access_key("scoped", "admin")
    grant = PermissionTree(entities={"light.kitchen": PermissionNode(state="GREEN")})
    writer = _HeldWriter(asyncio.get_running_loop())
    with patch.object(Store, "_write_prepared_data", lambda owner, mode, data: writer(owner, mode, data)):
        old = asyncio.create_task(_set_permissions(store, key, grant))
        await asyncio.wait_for(writer.entered.wait(), 2)
        old.cancel()
        # Not under the access-key lock, like a settings change: only the
        # store's own write order can keep the removal last.
        newer = asyncio.create_task(store.async_set_permissions(key.id, PermissionTree()))
        await asyncio.sleep(0)
        writer.release.set()
        with pytest.raises(asyncio.CancelledError):
            await old
        await newer
    assert key.permissions.entities == {}
    reloaded = await AccessKeyStore.async_create(hass)
    assert reloaded.get_access_key_by_id(key.id).permissions.entities == {}
