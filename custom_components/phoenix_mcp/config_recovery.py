"""Durable, bounded safety copies for Phoenix configuration mutations."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import timedelta
import json
import re
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.dt import parse_datetime, utcnow

from .access_key_schema import normalize_access_key_fields
from .const import (CONFIG_RECOVERY_LAYOUT_MAX_DEPTH, CONFIG_RECOVERY_MAX_BYTES,
                    CONFIG_RECOVERY_MAX_DEPTH, CONFIG_RECOVERY_MAX_RECORDS,
                    CONFIG_RECOVERY_RETENTION_DAYS, CONFIG_RECOVERY_PER_RESOURCE,
                    CONFIG_RECOVERY_UNRESOLVED, REDACTION_SENTINEL)
from .helpers import is_sensitive_key, redact_secrets_in_text
from .recovery_storage import RecoveryStorage
from .ws_dispatch import ws_commands_unsettled


recovery_source: ContextVar[dict | None] = ContextVar("phoenix_recovery_source", default=None)
recovery_resolution: ContextVar[str | None] = ContextVar("phoenix_recovery_resolution", default=None)


def runtime_identity(hass: HomeAssistant) -> str:
    """Identify this HA runtime across Phoenix reloads and client reconnects."""
    return hass.data.setdefault("phoenix_mcp_runtime_identity", uuid.uuid4().hex)


class ConfigurationRecoveryError(ValueError):
    """A protected mutation cannot proceed safely."""


# Every snapshot refusal starts with this text; a refusal that can name the
# failing location adds it after this sentence.
SNAPSHOT_UNSAFE_MESSAGE = "A complete secret-safe recovery snapshot is unavailable; nothing was dispatched."

# The refusals an administrator can act on, by their exact agent text, with the
# adminError key that says the same in the reader's language. Any other refusal
# is a rare adapter-specific case and gets the general recoveryBlocked text.
_OPERATOR_KEYS = {
    "Configuration recovery storage could not be loaded.": "recoveryStorageUnavailable",
    "A configuration operation is already running for this resource.": "recoveryOperationActive",
    "The configuration operation is still active.": "recoveryOperationActive",
    "A Home Assistant command Phoenix sent is still running; review once it finishes.": "recoveryCommandRunning",
    "Resolve the configuration recovery request in Approvals before retrying.": "recoveryResolveInApprovals",
    "Configuration recovery is full of unresolved operations; resolve them in Approvals.": "recoveryFull",
    "Review this unresolved recovery record before removing it.": "recoveryReviewBeforeRemoving",
    "Review the inactive recovery record before restoring it.": "recoveryReviewBeforeRestoring",
    "The configuration changed after review; review a fresh request.": "recoveryChangedAfterReview",
    "Administrator restoration authority changed.": "recoveryAuthorityChanged",
    "Helper storage could not be read.": "recoveryHelperUnreadable",
    SNAPSHOT_UNSAFE_MESSAGE: "recoverySnapshotUnsafe",
}


_SNAPSHOT_LOCATION_LABEL = " Blocked at "
_SNAPSHOT_LOCATION = re.compile(re.escape(SNAPSHOT_UNSAFE_MESSAGE + _SNAPSHOT_LOCATION_LABEL) + r"(.+): [^:]*\.\Z", re.S)


def recovery_error_key(message: str) -> str:
    """The adminError key for a recovery refusal's agent text."""
    if message.startswith(SNAPSHOT_UNSAFE_MESSAGE):
        return "recoverySnapshotUnsafeAt" if recovery_error_params(message) else "recoverySnapshotUnsafe"
    return _OPERATOR_KEYS.get(message, "recoveryBlocked")


def recovery_error_params(message: str) -> dict[str, str] | None:
    """The refused location of an unsafe snapshot, for the panel's own language."""
    match = _SNAPSHOT_LOCATION.match(message)
    return {"location": match[1]} if match else None


# Key parts that name ordinary things in a dashboard layout (a media session, a
# community card) and so do not by themselves mark a credential there. Removing
# them leaves real markers standing, so session_token still counts.
_LAYOUT_ORDINARY_KEY_PARTS = re.compile(r"session|community")
# Only a key spelled like this reaches a refusal: a dotted key can be an entity ID
# the access key may not see.
_NAMEABLE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
_PATH_MAX_PARTS = 12
_SCRUB_MARKERS = ("<phoenix-access-key>", "<phoenix-token>")


def _credential_key(key: Any, value: Any, layout: bool) -> bool:
    """Whether a key marks its value as a credential a snapshot must not hold.

    A dashboard layout already sits in Home Assistant's storage and has its own
    scrubbed reads, so a layout is refused for what can really be a credential:
    no flag, blank or count is one (a card's max_tokens: 5). Every other snapshot
    keeps the plain key-name rule.
    """
    if not is_sensitive_key(key):
        return False
    if not layout:
        return True
    if value is None or isinstance(value, bool) or value == "":
        return False
    remainder = _LAYOUT_ORDINARY_KEY_PARTS.sub("", key.lower())
    if isinstance(value, (int, float)):
        remainder = remainder.replace("tokens", "")
    return is_sensitive_key(remainder)


def _holds_placeholder(text: str, layout: bool) -> bool:
    """Whether a string stands in for a scrubbed value.

    A layout's text may mention the redaction placeholder, as Markdown can. Only
    a value that is nothing but the placeholder reads as a lost value there.
    """
    if any(marker in text for marker in _SCRUB_MARKERS):
        return True
    return text == REDACTION_SENTINEL if layout else REDACTION_SENTINEL in text


def _unsafe_location(node: Any, path: tuple, layout: bool, depth: int = 0) -> tuple[tuple, str] | None:
    """The first place a snapshot cannot be held losslessly, with the reason."""
    if depth > (CONFIG_RECOVERY_LAYOUT_MAX_DEPTH if layout else CONFIG_RECOVERY_MAX_DEPTH):
        return path, "it is nested too deeply to copy safely"
    if isinstance(node, str):
        if _holds_placeholder(node, layout) or redact_secrets_in_text(node) != node:
            return path, "its text holds a credential or a redaction placeholder"
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            child = (*path, key)
            if _credential_key(key, value, layout) or _holds_placeholder(key, False):
                return child, "its key name marks a credential"
            if found := _unsafe_location(value, child, layout, depth + 1):
                return found
    elif isinstance(node, list):
        for index, item in enumerate(node):
            if found := _unsafe_location(item, (*path, index), layout, depth + 1):
                return found
    return None


def _path_label(path: tuple) -> str:
    """A bounded, value-free label for a location in a snapshot."""
    label = ""
    for part in path[:_PATH_MAX_PARTS]:
        if isinstance(part, int):
            label += f"[{part}]"
        elif isinstance(part, str) and _NAMEABLE_KEY.fullmatch(part):
            label += f".{part}" if label else part
        else:
            label += "[key]"
    return label + ("..." if len(path) > _PATH_MAX_PARTS else "")


def safe_snapshot(value: dict | None) -> dict | None:
    """Admit only bounded JSON that survives scrubbing without losing information.

    Redaction is not a reversible encoding. Refuse rather than persist raw values
    or advertise an incomplete payload as a usable safety copy, and name the
    location so the refusal can be acted on.
    """
    try:
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded.encode()) > CONFIG_RECOVERY_MAX_BYTES:
            raise ConfigurationRecoveryError(f"{SNAPSHOT_UNSAFE_MESSAGE} It is over the recovery size limit.")
        snapshot = json.loads(encoded)
        layout = isinstance(snapshot, dict) and snapshot.get("snapshot_type") == "dashboard_layout"
        if found := _unsafe_location(snapshot, (), layout):
            path, reason = found
            raise ConfigurationRecoveryError(f"{SNAPSHOT_UNSAFE_MESSAGE}{_SNAPSHOT_LOCATION_LABEL}{_path_label(path)}: {reason}.")
    except ConfigurationRecoveryError:
        raise
    except Exception:
        raise ConfigurationRecoveryError(SNAPSHOT_UNSAFE_MESSAGE) from None
    return snapshot


class ConfigurationRecovery:
    """Keep in-flight safety copies independent of optional, rotating history."""

    def __init__(self, store: Store | RecoveryStorage | None = None, *, hass: HomeAssistant | None = None) -> None:
        self._store = store
        self._hass = hass
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._resources: set[str] = set()
        self._active: set[str] = set()
        self._load_failed = False
        self.reconcile_lock = asyncio.Lock()
        self.reconcile_cursor = 0
        self.announced_approvals: set[str] = set()

    async def _snapshot(self, value: dict | None) -> dict | None:
        """Perform bounded serialization and secret scanning outside the event loop."""
        if self._hass is not None:
            return await self._hass.async_add_executor_job(safe_snapshot, value)
        return await asyncio.to_thread(safe_snapshot, value)

    async def async_load(self) -> None:
        """Retry failed initialization without discarding unreadable recovery data."""
        if self._store is None:
            return
        try:
            raw = await self._store.async_load()
            records = {} if raw is None else raw["records"]

            if isinstance(records, dict):
                records = {rid: normalize_access_key_fields(row) if isinstance(row, dict) else row
                           for rid, row in records.items()}
            if not isinstance(records, dict) or len(records) > CONFIG_RECOVERY_MAX_RECORDS:
                raise ValueError
            for key, record in records.items():
                if (not isinstance(key, str) or not isinstance(record, dict)
                        or record.get("id") != key
                        or not all(isinstance(record.get(field), str) for field in (
                            "resource", "operation", "phase", "created_at", "access_key_id"
                        )) or record["phase"] not in (
                            "prepared", "dispatching", "verified", "partial", "unknown", "reviewed", "not_applied"
                        )):
                    raise ValueError
                if any(record.get(field) is not None and not isinstance(record[field], dict) for field in ("before", "source", "expected")):
                    raise ValueError
                await self._snapshot(record.get("before"))
                await self._snapshot(record.get("source"))
                await self._snapshot(record.get("expected"))
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

    def inactive(self, rid: str) -> bool:
        record = self._records.get(rid)
        return record is not None and rid not in self._active and record["resource"] not in self._resources

    def _retained(self, *, reserve: int = 0) -> dict:
        """Bound completed rollback copies without ever dropping unresolved work."""
        cutoff = utcnow() - timedelta(days=CONFIG_RECOVERY_RETENTION_DAYS)
        counts: dict[str, int] = {}
        retained = {}
        for rid, record in reversed(list(self._records.items())):
            if rid in self._active or record["phase"] in CONFIG_RECOVERY_UNRESOLVED:
                retained[rid] = record
                continue
            created = parse_datetime(record["created_at"])
            resource = record["resource"]
            counts[resource] = counts.get(resource, 0) + 1
            if created is not None and created.tzinfo is not None and created >= cutoff and counts[resource] <= CONFIG_RECOVERY_PER_RESOURCE:
                retained[rid] = record
        for rid in reversed(list(retained)):
            if len(retained) <= CONFIG_RECOVERY_MAX_RECORDS - reserve:
                break
            if rid not in self._active and retained[rid]["phase"] not in CONFIG_RECOVERY_UNRESOLVED:
                del retained[rid]
        # Internal rows and snapshots are replaced, never mutated in place.
        return {rid: retained[rid] for rid in self._records if rid in retained}

    async def prune(self) -> None:
        async with self._lock:
            old = self._records
            retained = self._retained()
            if retained == old:
                return
            self._records = retained
            try:
                await self._save()
            except BaseException:
                self._records = old
                raise

    async def update(self, rid: str, **fields: Any) -> None:
        """Persist internal reconciliation bindings without replacing observations."""
        admitted = await self._snapshot(fields)
        assert admitted is not None
        async with self._lock:
            old = self._records[rid]
            self._records[rid] = {**old, **admitted}
            try:
                await self._save()
            except BaseException:
                self._records[rid] = old
                raise

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

    async def begin(self, resource: str, operation: str, access_key_id: str,
                    before: dict | None, *, source: dict | None = None,
                    expected: dict | None = None) -> str:
        """Persist before dispatch; storage failure always prevents the mutation."""
        before = await self._snapshot(before)
        source = await self._snapshot(source if source is not None else recovery_source.get())
        expected = await self._snapshot(expected)
        async with self._lock:
            if self._load_failed:
                await self.async_load()
            if any(r["resource"] == resource and r["phase"] in CONFIG_RECOVERY_UNRESOLVED
                   and r["id"] != recovery_resolution.get() for r in self._records.values()):
                raise ConfigurationRecoveryError("Resolve the configuration recovery request in Approvals before retrying.")
            records = self._retained(reserve=1)
            if len(records) >= CONFIG_RECOVERY_MAX_RECORDS:
                raise ConfigurationRecoveryError("Configuration recovery is full of unresolved operations; resolve them in Approvals.")
            rid = uuid.uuid4().hex
            records[rid] = {
                "id": rid, "resource": resource, "operation": operation,
                "access_key_id": access_key_id, "created_at": utcnow().isoformat(),
                "phase": "prepared", "before": before, "source": source,
                "expected": expected,
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
        admitted = await self._snapshot(observations)
        async with self._lock:
            old = self._records[rid]
            self._records[rid] = {**old, "phase": phase, "observations": admitted}
            try:
                await self._save()
            except BaseException:
                self._records[rid] = old
                raise

    async def set_source(self, rid: str, source: dict) -> None:
        """Attach a post-write snapshot only after the owning adapter reads it back."""
        safe_source = await self._snapshot(source)
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
            # A timed-out command can still land after its caller released the
            # resource; reviewing first would let a newer write be overwritten.
            if ws_commands_unsettled():
                raise ConfigurationRecoveryError(
                    "A Home Assistant command Phoenix sent is still running; review once it finishes."
                )
            if delete and record["phase"] not in ("verified", "not_applied", "reviewed"):
                raise ConfigurationRecoveryError("Review this unresolved recovery record before removing it.")
            previous = dict(self._records)
            if delete:
                del self._records[rid]
            else:
                self._records[rid] = {**record, "phase": "reviewed", "reviewed_by": user_id}
            try:
                await self._save()
            except BaseException:
                self._records = previous
                raise
