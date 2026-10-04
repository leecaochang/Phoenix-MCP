"""Publish a small recovery index only after immutable HA snapshot stores exist."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONFIG_RECOVERY_MAX_RECORDS, CONFIG_RECOVERY_STORAGE_KEY, CONFIG_RECOVERY_SNAPSHOT_PREFIX
from .durable_storage import DurableStorageError, DurableStore

_LOGGER = logging.getLogger(__name__)
_FIELDS = ("before", "source", "expected")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _digest(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


class RecoveryStorage:
    """Keep snapshots immutable and publish their references as the write barrier.

    ConfigurationRecovery owns serialization and supplies replacement rows with
    immutable snapshots. Only the current committed rows are cached. All writes
    finish before cancellation releases the owner's lock, including executor I/O.
    Unreferenced blobs are removed only after a successful index commit; a failed
    cleanup leaves a harmless orphan which the next load can discover.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.index = self._store(CONFIG_RECOVERY_STORAGE_KEY)
        self._rows: dict[str, dict] = {}
        self._encoded: dict[str, dict] = {}
        self._known: set[str] = set()

    def _store(self, key: str) -> DurableStore[dict]:
        return DurableStore(self.hass, 1, key, private=True, atomic_writes=True,
                            serialize_in_event_loop=False)

    def _inventory(self) -> tuple[bool, set[str]]:
        path = Path(self.index.path)
        if not path.parent.exists():
            return False, set()
        names = {entry.name for entry in path.parent.iterdir() if entry.is_file()}
        damaged = path.name in names or any(name.startswith(path.name + ".corrupt.") for name in names)
        blobs = {name[len(CONFIG_RECOVERY_SNAPSHOT_PREFIX):] for name in names
                 if name.startswith(CONFIG_RECOVERY_SNAPSHOT_PREFIX)
                 and _DIGEST.fullmatch(name[len(CONFIG_RECOVERY_SNAPSHOT_PREFIX):])}
        return damaged, blobs

    async def async_load(self) -> dict | None:
        existed, known = await self.hass.async_add_executor_job(self._inventory)
        raw = await self.index.async_load()
        if raw is None:
            if existed:
                raise DurableStorageError("Configuration recovery index is unreadable.")
            self._known = known
            return None
        if not isinstance(raw, dict):
            raise DurableStorageError("Configuration recovery index is invalid.")
        if "snapshot_format" not in raw:
            # Released inline snapshots remain authoritative until migration commits.
            self._known = known
            return raw
        if type(raw["snapshot_format"]) is not int or raw["snapshot_format"] != 1 or not isinstance(raw.get("records"), dict) or len(raw["records"]) > CONFIG_RECOVERY_MAX_RECORDS:
            raise DurableStorageError("Configuration recovery snapshot format is unsupported.")
        rows: dict[str, dict] = {}
        snapshots: dict[str, dict] = {}
        for rid, encoded in raw["records"].items():
            if not isinstance(encoded, dict):
                raise DurableStorageError("Configuration recovery record is invalid.")
            row = dict(encoded)
            for field in _FIELDS:
                reference = encoded.get(field)
                if reference is None:
                    row[field] = None
                    continue
                if not isinstance(reference, str) or not _DIGEST.fullmatch(reference):
                    raise DurableStorageError("Configuration recovery snapshot reference is invalid.")
                if reference not in snapshots:
                    snapshot = await self._store(CONFIG_RECOVERY_SNAPSHOT_PREFIX + reference).async_load()
                    if not isinstance(snapshot, dict) or await self.hass.async_add_executor_job(_digest, snapshot) != reference:
                        raise DurableStorageError("Configuration recovery snapshot is unavailable.")
                    snapshots[reference] = snapshot
                row[field] = snapshots[reference]
            rows[rid] = row
        self._rows = dict(rows)
        self._encoded = raw["records"]
        self._known = known | set(snapshots)
        return {"records": rows}

    def _pack(self, rows: dict[str, dict]) -> tuple[dict, dict[str, dict], set[str]]:
        encoded: dict[str, dict] = {}
        blobs: dict[str, dict] = {}
        referenced: set[str] = set()
        for rid, row in rows.items():
            previous = self._rows.get(rid)
            saved = self._encoded.get(rid)
            if previous is row and saved is not None:
                packed = saved
            else:
                packed = dict(row)
                for field in _FIELDS:
                    snapshot = row.get(field)
                    if snapshot is None:
                        packed[field] = None
                    elif saved is not None and previous is not None and previous.get(field) is snapshot:
                        packed[field] = saved[field]
                    else:
                        reference = _digest(snapshot)
                        packed[field] = reference
                        blobs[reference] = snapshot
            encoded[rid] = packed
            referenced.update(packed[field] for field in _FIELDS if packed[field] is not None)
        return encoded, blobs, referenced

    async def _save(self, rows: dict[str, dict]) -> None:
        encoded, blobs, referenced = await self.hass.async_add_executor_job(self._pack, rows)
        committed = {row[field] for row in self._encoded.values() for field in _FIELDS if row[field] is not None}
        for reference, snapshot in blobs.items():
            if reference in committed:
                continue
            # _known includes crash orphans, which were not verified on load.
            # Rewriting newly encountered content also repairs a missing orphan.
            await self._store(CONFIG_RECOVERY_SNAPSHOT_PREFIX + reference).async_save(snapshot)
            self._known.add(reference)
        await self.index.async_save({"snapshot_format": 1, "records": encoded})
        self._rows = dict(rows)
        self._encoded = encoded
        obsolete = self._known - referenced
        self._known |= referenced
        for reference in obsolete:
            try:
                await self._store(CONFIG_RECOVERY_SNAPSHOT_PREFIX + reference).async_remove()
            except OSError:
                _LOGGER.warning("An unreferenced configuration recovery snapshot could not be removed")
            else:
                self._known.discard(reference)

    async def async_save(self, data: dict[str, Any]) -> None:
        transaction = asyncio.create_task(self._save(data["records"]))
        cancelled = False
        while not transaction.done():
            try:
                await asyncio.shield(transaction)
            except asyncio.CancelledError:
                cancelled = True
        transaction.result()
        if cancelled:
            raise asyncio.CancelledError
