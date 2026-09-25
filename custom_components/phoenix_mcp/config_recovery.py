"""Durable, bounded safety copies for Phoenix configuration mutations."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from typing import Any
import uuid

from homeassistant.helpers.storage import Store
from homeassistant.util.dt import utcnow

from .const import CONFIG_RECOVERY_MAX_BYTES, CONFIG_RECOVERY_MAX_RECORDS
from .helpers import redact_structure


recovery_source: ContextVar[dict | None] = ContextVar("phoenix_recovery_source", default=None)


def runtime_identity(hass) -> str:
    """Identify this HA runtime across Phoenix reloads and client reconnects."""
    return hass.data.setdefault("phoenix_mcp_runtime_identity", uuid.uuid4().hex)


class ConfigurationRecoveryError(ValueError):
    """A protected mutation cannot proceed safely."""


def safe_snapshot(value: dict | None) -> dict | None:
    """Admit only bounded JSON that survives scrubbing without losing information.

    Redaction is not a reversible encoding. Refuse rather than persist raw values
    or advertise an incomplete payload as a usable safety copy.
    """
    try:
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded.encode()) > CONFIG_RECOVERY_MAX_BYTES:
            raise ValueError
        snapshot = json.loads(encoded)
        if redact_structure(snapshot) != snapshot:
            raise ValueError
        if "<redacted>" in encoded or "<phoenix-token>" in encoded:
            raise ValueError
    except Exception:
        raise ConfigurationRecoveryError(
            "A complete secret-safe recovery snapshot is unavailable; nothing was dispatched."
        ) from None
    return snapshot


class ConfigurationRecovery:
    """Keep in-flight safety copies independent of optional, rotating history."""

    def __init__(self, store: Store | None = None) -> None:
        self._store = store
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._resources: set[str] = set()
        self._active: set[str] = set()
        self._load_failed = False

    async def async_load(self) -> None:
        """Retry failed initialization without discarding unreadable recovery data."""
        if self._store is None:
            return
        try:
            raw = await self._store.async_load()
            records = {} if raw is None else raw["records"]
            if not isinstance(records, dict) or len(records) > CONFIG_RECOVERY_MAX_RECORDS:
                raise ValueError
            for key, record in records.items():
                if (not isinstance(key, str) or not isinstance(record, dict)
                        or record.get("id") != key
                        or not all(isinstance(record.get(field), str) for field in (
                            "resource", "operation", "phase", "created_at", "token_id"
                        )) or record["phase"] not in (
                            "prepared", "dispatching", "verified", "partial", "unknown", "reviewed", "not_applied"
                        )):
                    raise ValueError
                if any(record.get(field) is not None and not isinstance(record[field], dict) for field in ("before", "source")):
                    raise ValueError
                safe_snapshot(record.get("before"))
                safe_snapshot(record.get("source"))
            self._records = records
            self._load_failed = False
        except Exception:
            self._load_failed = True
            raise ConfigurationRecoveryError("Configuration recovery storage could not be loaded.") from None

    async def _save(self) -> None:
        if self._store is not None:
            await self._store.async_save({"records": self._records})

    def records(self) -> list[dict]:
        """Admin-only immutable copies; interrupted dispatch remains uncertain."""
        if self._load_failed:
            raise ConfigurationRecoveryError("Configuration recovery storage could not be loaded.")
        return [dict(deepcopy(r), active=r["id"] in self._active)
                for r in reversed(list(self._records.values()))]

    def get(self, rid: str) -> dict | None:
        record = self._records.get(rid)
        return deepcopy(record) if record is not None else None

    @asynccontextmanager
    async def resource(self, key: str):
        """Refuse concurrent Phoenix writes, including unresolved prior effects."""
        if key in self._resources:
            raise ConfigurationRecoveryError("A configuration operation is already running for this resource.")
        self._resources.add(key)
        try:
            yield
        finally:
            self._resources.discard(key)

    async def begin(self, resource: str, operation: str, token_id: str,
                    before: dict | None, *, source: dict | None = None) -> str:
        """Persist before dispatch; storage failure always prevents the mutation."""
        before = safe_snapshot(before)
        source = safe_snapshot(source if source is not None else recovery_source.get())
        async with self._lock:
            if self._load_failed:
                await self.async_load()
            if any(r["resource"] == resource and r["phase"] in (
                "prepared", "dispatching", "partial", "unknown"
            ) for r in self._records.values()):
                raise ConfigurationRecoveryError("Review the previous configuration recovery record before retrying.")
            records = deepcopy(self._records)
            if len(records) >= CONFIG_RECOVERY_MAX_RECORDS:
                # Completed copies also require explicit admin removal. Never
                # rotate away the only surviving deleted-resource snapshot.
                raise ConfigurationRecoveryError("Configuration recovery storage is full; review and remove completed records.")
            rid = uuid.uuid4().hex
            records[rid] = {
                "id": rid, "resource": resource, "operation": operation,
                "token_id": token_id, "created_at": utcnow().isoformat(),
                "phase": "prepared", "before": before, "source": source,
            }
            self._records = records
            try:
                await self._save()
            except BaseException:
                # A failed save may have reached disk. Keep the in-memory barrier.
                raise
            self._active.add(rid)
            return rid

    async def mark(self, rid: str, phase: str, **observations: Any) -> None:
        """Persist a phase without removing its safety payload."""
        if phase not in ("dispatching", "verified", "partial", "unknown", "not_applied"):
            raise ValueError("Invalid recovery phase")
        safe_snapshot(observations)
        async with self._lock:
            old = self._records[rid]
            self._records[rid] = {**old, "phase": phase, "observations": observations}
            try:
                await self._save()
            except BaseException:
                self._records[rid] = old
                raise

    async def set_source(self, rid: str, source: dict) -> None:
        """Attach a post-write snapshot only after the owning adapter reads it back."""
        safe_source = safe_snapshot(source)
        assert safe_source is not None
        async with self._lock:
            old = self._records[rid]
            updated = {**old, "source": safe_source}
            self._records[rid] = updated
            try:
                await self._save()
            except BaseException:
                self._records[rid] = old
                raise

    def leave(self, rid: str) -> None:
        """Cancellation and process loss never imply that an operation failed."""
        self._active.discard(rid)

    async def reviewed(self, rid: str, user_id: str, *, delete: bool = False) -> None:
        """Explicit admin reconciliation is separate from observed desired state."""
        async with self._lock:
            record = self._records[rid]
            if rid in self._active or record["resource"] in self._resources:
                raise ConfigurationRecoveryError("The configuration operation is still active.")
            if delete and record["phase"] not in ("verified", "not_applied", "reviewed"):
                raise ConfigurationRecoveryError("Review this unresolved recovery record before removing it.")
            previous = deepcopy(self._records)
            if delete:
                del self._records[rid]
            else:
                self._records[rid] = {**record, "phase": "reviewed", "reviewed_by": user_id}
            try:
                await self._save()
            except BaseException:
                self._records = previous
                raise
