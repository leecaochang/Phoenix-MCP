"""Exercise snapshot migration and commit ordering through HA's real file writer."""

import asyncio
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError

from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery, ConfigurationRecoveryError
from custom_components.phoenix_mcp.const import CONFIG_RECOVERY_STORAGE_KEY, CONFIG_RECOVERY_SNAPSHOT_PREFIX
from custom_components.phoenix_mcp.durable_storage import DurableStorageError, DurableStore
from custom_components.phoenix_mcp.recovery_storage import RecoveryStorage

_REAL_WRITE = Store._async_write_data
_REAL_LOAD = Store._async_load
_REAL_REMOVE = Store.async_remove


@pytest.fixture(autouse=True)
def real_storage(hass):
    with patch.object(Store, "_async_write_data", _REAL_WRITE), patch.object(Store, "_async_load", _REAL_LOAD), patch.object(Store, "async_remove", _REAL_REMOVE):
        yield


def blobs(storage):
    return list(Path(storage.index.path).parent.glob(CONFIG_RECOVERY_SNAPSHOT_PREFIX + "*"))


async def test_dedup_and_phase_writes_do_not_rewrite_snapshots(hass):
    storage = RecoveryStorage(hass)
    recovery = ConfigurationRecovery(storage, hass=hass)
    snapshot = {"content": "x" * 480_000}
    rid = await recovery.begin("file:a", "write", "token", {"content": "old"}, source=snapshot, expected=snapshot)
    assert len(blobs(storage)) == 2
    originals = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in blobs(storage)}
    await recovery.mark(rid, "dispatching")
    await recovery.mark(rid, "verified")
    recovery.leave(rid)
    await recovery.begin("file:b", "write", "token", None, expected=snapshot)
    assert len(blobs(storage)) == 2
    assert originals == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in blobs(storage)}
    index = json.loads(Path(storage.index.path).read_text())["data"]
    assert index["snapshot_format"] == 1
    assert index["records"][rid]["source"] == index["records"][rid]["expected"]
    assert Path(storage.index.path).stat().st_size < 2000
    fresh = ConfigurationRecovery(RecoveryStorage(hass), hass=hass)
    await fresh.async_load()
    assert fresh.get(rid) == recovery.get(rid)
    exposed = fresh.get(rid)
    exposed["source"]["content"] = "changed"
    assert fresh.get(rid)["source"] == snapshot


@pytest.mark.parametrize("boundary", ["blob", "index", "success"])
async def test_legacy_migration_commits_snapshots_before_replacing_index(hass, boundary):
    legacy_store = DurableStore(hass, 1, CONFIG_RECOVERY_STORAGE_KEY, atomic_writes=True)
    legacy = ConfigurationRecovery(legacy_store, hass=hass)
    rid = await legacy.begin("file:a", "write", "token", {"content": "before"}, expected={"content": "after"})
    old_bytes = Path(legacy_store.path).read_bytes()
    recovery = ConfigurationRecovery(RecoveryStorage(hass), hass=hass)
    await recovery.async_load()
    real_writer = Store._write_prepared_data

    def write(store, *args):
        if (boundary == "blob" and store.key.startswith(CONFIG_RECOVERY_SNAPSHOT_PREFIX)
                or boundary == "index" and store.key == CONFIG_RECOVERY_STORAGE_KEY):
            raise WriteError("disk unavailable")
        real_writer(store, *args)

    with patch.object(Store, "_write_prepared_data", write):
        if boundary == "success":
            await recovery.mark(rid, "dispatching")
        else:
            with pytest.raises(DurableStorageError):
                await recovery.mark(rid, "dispatching")
            assert Path(legacy_store.path).read_bytes() == old_bytes
    fresh = ConfigurationRecovery(RecoveryStorage(hass), hass=hass)
    await fresh.async_load()
    assert fresh.get(rid)["phase"] == ("dispatching" if boundary == "success" else "prepared")
    assert fresh.get(rid)["before"] == {"content": "before"}
    assert fresh.get(rid)["expected"] == {"content": "after"}
    with pytest.raises(ConfigurationRecoveryError, match="Resolve"):
        await fresh.begin("file:a", "write", "token", {})


@pytest.mark.parametrize("damage", ["missing", "mismatch", "malformed_reference", "unsupported", "corrupt_index"])
async def test_unavailable_snapshots_fail_closed_after_restart(hass, damage):
    storage = RecoveryStorage(hass)
    recovery = ConfigurationRecovery(storage, hass=hass)
    rid = await recovery.begin("file:a", "write", "token", {"content": "before"})
    if damage == "missing":
        blobs(storage)[0].unlink()
    elif damage == "mismatch":
        path = blobs(storage)[0]
        raw = json.loads(path.read_text())
        raw["data"]["content"] = "tampered"
        path.write_text(json.dumps(raw))
    elif damage == "corrupt_index":
        Path(storage.index.path).write_text("{")
    else:
        raw = json.loads(Path(storage.index.path).read_text())
        if damage == "unsupported":
            raw["data"]["snapshot_format"] = 2
        else:
            raw["data"]["records"][rid]["before"] = "../other-store"
        Path(storage.index.path).write_text(json.dumps(raw))
    fresh = ConfigurationRecovery(RecoveryStorage(hass), hass=hass)
    with pytest.raises(ConfigurationRecoveryError):
        await fresh.async_load()
    with pytest.raises(ConfigurationRecoveryError):
        fresh.records()
    with pytest.raises(ConfigurationRecoveryError):
        await fresh.begin("file:other", "write", "token", {})


async def test_cleanup_preserves_shared_snapshots_and_retries_orphans(hass):
    storage = RecoveryStorage(hass)
    recovery = ConfigurationRecovery(storage, hass=hass)
    ids = []
    for resource in ("file:a", "file:b"):
        rid = await recovery.begin(resource, "write", "token", {"content": "shared"})
        await recovery.mark(rid, "verified")
        recovery.leave(rid)
        ids.append(rid)
    assert len(blobs(storage)) == 1
    await recovery.reviewed(ids[0], "admin", delete=True)
    assert len(blobs(storage)) == 1
    with patch.object(Store, "async_remove", side_effect=OSError("disk unavailable")):
        await recovery.reviewed(ids[1], "admin", delete=True)
    assert recovery.records() == []
    assert len(blobs(storage)) == 1
    fresh_storage = RecoveryStorage(hass)
    fresh = ConfigurationRecovery(fresh_storage, hass=hass)
    await fresh.async_load()
    assert fresh.records() == []
    await fresh.begin("file:c", "write", "token", {"content": "new"})
    assert len(blobs(fresh_storage)) == 1
    assert json.loads(blobs(fresh_storage)[0].read_text())["data"] == {"content": "new"}


async def test_cancelled_writer_finishes_before_next_record_can_publish(hass):
    storage = RecoveryStorage(hass)
    recovery = ConfigurationRecovery(storage, hass=hass)
    entered = threading.Event()
    release = threading.Event()
    real_writer = Store._write_prepared_data

    def write(store, *args):
        if store.key == CONFIG_RECOVERY_STORAGE_KEY and not entered.is_set():
            entered.set()
            assert release.wait(10)
        real_writer(store, *args)

    with patch.object(Store, "_write_prepared_data", write):
        first = asyncio.create_task(recovery.begin("file:a", "write", "token", {"content": "a"}))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            second = asyncio.create_task(recovery.begin("file:b", "write", "token", {"content": "b"}))
            await asyncio.sleep(0)
            assert not first.done()
            assert not second.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
    fresh = ConfigurationRecovery(RecoveryStorage(hass), hass=hass)
    await fresh.async_load()
    assert {row["resource"] for row in fresh.records()} == {"file:a", "file:b"}
    assert all(row["phase"] == "prepared" for row in fresh.records())
    assert all(not row["active"] for row in fresh.records())
    with pytest.raises(ConfigurationRecoveryError, match="Resolve"):
        await fresh.begin("file:a", "write", "token", {})


async def test_failed_phase_commit_preserves_previous_index_and_snapshots(hass):
    storage = RecoveryStorage(hass)
    recovery = ConfigurationRecovery(storage, hass=hass)
    rid = await recovery.begin("file:a", "write", "token", {"content": "before"}, expected={"content": "after"})
    old = Path(storage.index.path).read_bytes()
    original_blobs = {p.name: p.read_bytes() for p in blobs(storage)}
    real_writer = Store._write_prepared_data

    def write(store, *args):
        if store.key == CONFIG_RECOVERY_STORAGE_KEY:
            raise WriteError("disk unavailable")
        real_writer(store, *args)

    with patch.object(Store, "_write_prepared_data", write):
        with pytest.raises(DurableStorageError):
            await recovery.set_source(rid, {"content": "observed"})
    assert Path(storage.index.path).read_bytes() == old
    assert recovery.get(rid)["source"] is None
    assert all((Path(storage.index.path).parent / name).read_bytes() == content
               for name, content in original_blobs.items())
    assert len(blobs(storage)) == 3  # Unpublished content must not replace old references.
    fresh_storage = RecoveryStorage(hass)
    fresh = ConfigurationRecovery(fresh_storage, hass=hass)
    await fresh.async_load()
    assert fresh.get(rid)["expected"] == {"content": "after"}
    await fresh.mark(rid, "unknown")
    assert len(blobs(storage)) == 2
    assert fresh.get(rid)["before"] == {"content": "before"}
