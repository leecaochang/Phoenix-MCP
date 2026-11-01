"""Circular buffer audit log for Phoenix MCP. Optionally persists entries to HA storage."""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.util.dt import parse_datetime, utcnow

from .const import (
    AGENTCLI_CLIENT_IP,
    AI_TASK_CLIENT_IP,
    ASSIST_CLIENT_IP,
    AUDIT_LOG_MAXLEN,
    AUDIT_STORAGE_VERSION,
    CONVERSATION_LOG_SCAN_BYTES,
    HOME_COMPANION_CLIENT_IP,
    VOICE_AGENT_CLIENT_IP,
)
from .access_key_store import GlobalSettings

if TYPE_CHECKING:
    from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

# Outcome values:
#   allowed        - request completed successfully
#   denied         - blocked by permission rules, blocklist, dual-gate, or RED/NO_ACCESS
#   not_found      - entity absent from hass.states and entity registry (ghost pre-check)
#   rate_limited   - access key exceeded its rate limit
#   not_implemented - MCP method received but not supported (e.g. resources/templates/list)
#   invalid_request - request was structurally invalid (e.g. template render with bad syntax)
#   pending_approval - a cap_* mode of "confirm" queued the action for admin review.
#                       Always recorded regardless of the log_allowed/log_denied toggles
#                       (see record()); the approval trail should not be suppressible.
Outcome = Literal[
    "allowed", "denied", "not_found", "rate_limited",
    "not_implemented", "invalid_request", "pending_approval",
]

_REDACTED = "[redacted]"

# Sources that are Phoenix's own surfaces, not a caller's address: Log client IP
# off hides addresses, and these name no one.
_INTERNAL_SOURCES = frozenset({
    AGENTCLI_CLIENT_IP, ASSIST_CLIENT_IP, VOICE_AGENT_CLIENT_IP, AI_TASK_CLIENT_IP, HOME_COMPANION_CLIENT_IP,
})
# Payload keys that hold entity IDs, hidden with the resource when Log entity IDs is off.
_ENTITY_KEYS = frozenset({"entity_id", "entity_ids", "entity", "entities"})


def _without_entity_ids(value: Any) -> Any:
    """A payload with every entity-ID field replaced. Free text that names an entity stays."""
    if isinstance(value, dict):
        return {k: _REDACTED if k in _ENTITY_KEYS else _without_entity_ids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_entity_ids(v) for v in value]
    return value


def _bounded_field(value: object) -> str:
    """Coerce an audit scalar to a length-capped string (see MAX_AUDIT_FIELD_LEN)."""
    s = value if isinstance(value, str) else str(value)
    return s if len(s) <= MAX_AUDIT_FIELD_LEN else s[:MAX_AUDIT_FIELD_LEN] + "...[truncated]"

MAX_QUERY_LIMIT = 500
MAX_AUDIT_PAYLOAD_BYTES = 2048
# Hard cap for the scalar method/resource fields. Both derive from client-supplied
# JSON-RPC data (method names, resource URIs), so they must be normalized to a
# bounded string before storage: an unbounded or non-string value could bloat the
# ring buffer's persisted snapshot and crash any consumer that string-methods it.
MAX_AUDIT_FIELD_LEN = 256


def _logged_payload(payload: Any) -> str | None:
    """A payload as the audit log keeps it: redacted as the Conversation Log redacts, then capped.

    The Conversation Log's sanitizer also hides device codes (an alarm or lock
    `code`, a PIN, a Wi-Fi key), in keys and in free text, which the general
    secret redaction keeps. A stored payload from an older version may be a
    capped string that is no longer JSON; it is cleaned as text. A payload that
    cannot be sanitized is left out rather than kept raw.
    """
    from .conversation_log import sanitized  # noqa: PLC0415
    from .helpers import log_internal_failure  # noqa: PLC0415

    try:
        if isinstance(payload, str):
            try:
                value: Any = json.loads(payload)
            except ValueError:
                value = None
            clean = sanitized(payload, max_bytes=CONVERSATION_LOG_SCAN_BYTES) if value is None else json.dumps(
                sanitized(value, max_bytes=CONVERSATION_LOG_SCAN_BYTES))
        else:
            # Values that are not JSON (a datetime in a MESA event) keep their text form.
            value = json.loads(json.dumps(payload, default=str))
            clean = json.dumps(sanitized(value, max_bytes=CONVERSATION_LOG_SCAN_BYTES))
    except ValueError:
        return None
    except Exception as error:  # noqa: BLE001 - never keep the raw value
        log_internal_failure(_LOGGER, "audit_payload_sanitize", error)
        return None
    return clean if len(clean) <= MAX_AUDIT_PAYLOAD_BYTES else clean[:MAX_AUDIT_PAYLOAD_BYTES] + "...[truncated]"


def generate_request_id() -> str:
    """Return a new UUID string for use as X-Phoenix-Request-ID."""
    return str(uuid.uuid4())


@dataclass
class AuditEntry:
    request_id: str
    timestamp: datetime
    access_key_id: str
    access_key_name: str
    method: str
    resource: str
    outcome: Outcome
    client_ip: str
    pass_through: bool = False
    payload: str | None = None
    # True when MESA waved this action through under advisory mode (a confirm/
    # read_only-by-nature entity proceeded with a warning instead of being gated).
    mesa_advisory: bool = False
    # Name of the access key's active settings preset at request time.
    preset: str | None = None
    # True when the stale-tool-list notice was injected into this response.
    stale_tools_advisory: bool = False
    ha_user_id: str | None = None
    # Server-owned source identity, never a bearer credential.
    originating_access_key_id: str | None = None

    def to_dict(self) -> dict:
        d = {
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat(),
            "access_key_id": self.access_key_id,
            "access_key_name": self.access_key_name,
            "method": self.method,
            "resource": self.resource,
            "outcome": self.outcome,
            "client_ip": self.client_ip,
            "pass_through": self.pass_through,
        }
        if self.payload is not None:
            d["payload"] = self.payload
        if self.mesa_advisory:
            d["mesa_advisory"] = True
        if self.preset is not None:
            d["preset"] = self.preset
        if self.stale_tools_advisory:
            d["stale_tools_advisory"] = True
        if self.ha_user_id is not None:
            d["ha_user_id"] = self.ha_user_id
        if self.originating_access_key_id is not None:
            d["originating_access_key_id"] = self.originating_access_key_id
        return d


class AuditLog:
    """Circular buffer audit log.

    Logging toggles from GlobalSettings are evaluated at record time, not at
    query time. Sensor counters in sensor.py are updated independently of
    these toggles and always reflect total activity.

    When a Store is provided, the log can be persisted to disk via async_save()
    and restored on startup via async_load(). Without a store it is purely
    in-memory.
    """

    def __init__(self, maxlen: int = AUDIT_LOG_MAXLEN, store: Store | None = None) -> None:
        self._log: deque[AuditEntry] = deque(maxlen=maxlen)
        self._store = store

    def record(
        self,
        *,
        request_id: str,
        access_key_id: str,
        access_key_name: str,
        method: str,
        resource: str,
        outcome: Outcome,
        client_ip: str,
        settings: GlobalSettings,
        pass_through: bool = False,
        payload: dict | None = None,
        mesa_advisory: bool = False,
        preset: str | None = None,
        stale_tools_advisory: bool = False,
        timestamp: datetime | None = None,
        ha_user_id: str | None = None,
        originating_access_key_id: str | None = None,
    ) -> None:
        """Append an audit entry, subject to the current logging settings.

        Returns without writing if disable_all_logging or the relevant per-outcome
        toggle is off. resource and the payload's entity-ID fields are redacted when
        log_entity_names is off, and client_ip when log_client_ip is off unless it
        names one of Phoenix's own surfaces. pending_approval and approval/*
        lifecycle rows ignore the log_allowed/log_denied toggles.
        """
        if settings.disable_all_logging:
            return

        # Normalize the client-derived scalars before inspecting them too. A
        # malformed JSON-RPC request can supply a list here, and lifecycle
        # detection must be just as poison-safe as storage and querying.
        method = _bounded_field(method)
        resource = _bounded_field(resource)

        # Approval lifecycle rows are the authorization trail itself. They are
        # never suppressible by the ordinary allowed/denied filters, just like
        # pending_approval, or an operator can see the request that waited for
        # consent but no durable account of the decision that released it.
        approval_lifecycle = method.startswith("approval/")

        if outcome == "allowed" and not settings.log_allowed and not approval_lifecycle:
            return
        if (
            outcome in ("denied", "not_found", "not_implemented", "invalid_request")
            and not settings.log_denied
            and not approval_lifecycle
        ):
            return
        if outcome == "rate_limited" and not settings.log_rate_limited:
            return

        logged_resource = resource if settings.log_entity_names else _REDACTED
        logged_ip = client_ip if settings.log_client_ip or client_ip in _INTERNAL_SOURCES else _REDACTED

        if payload is not None and not settings.log_entity_names:
            payload = _without_entity_ids(payload)
        logged_payload = None if payload is None else _logged_payload(payload)

        from .household_authority import household_authority

        household = household_authority.get()
        if ha_user_id is None and access_key_id == "admin" and access_key_name.startswith("admin:"):
            ha_user_id = access_key_name.removeprefix("admin:")
        self._log.append(AuditEntry(
            request_id=request_id,
            timestamp=timestamp or utcnow(),
            access_key_id=access_key_id,
            access_key_name=access_key_name,
            method=method,
            resource=logged_resource,
            outcome=outcome,
            client_ip=logged_ip,
            pass_through=pass_through,
            payload=logged_payload,
            mesa_advisory=mesa_advisory,
            preset=preset,
            stale_tools_advisory=stale_tools_advisory,
            ha_user_id=ha_user_id if ha_user_id is not None else household.user_id if household else None,
            originating_access_key_id=originating_access_key_id,
        ))

    _VALID_OUTCOMES = frozenset({"allowed", "denied", "not_found", "rate_limited", "not_implemented", "invalid_request", "pending_approval"})

    def _filtered(
        self,
        *,
        access_key_id: str | None = None,
        outcome: str | None = None,
        client_ip: str | None = None,
        method: str | None = None,
        resource: str | None = None,
        request_id: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditEntry] | None:
        """Shared filter pass behind query() and count(). Newest first.

        Returns None when outcome is not a recognised value, so both callers can
        surface the same 400 rather than one silently returning empty/zero.
        """
        if outcome is not None and outcome not in self._VALID_OUTCOMES:
            return None

        entries = list(self._log)

        if access_key_id is not None:
            entries = [e for e in entries if e.access_key_id == access_key_id]
        if outcome is not None:
            entries = [e for e in entries if e.outcome == outcome]
        if client_ip is not None:
            entries = [e for e in entries if client_ip in e.client_ip]
        # str() guards against a persisted entry whose method/resource is not a
        # string: such a value snapshots to the Store and survives restart, and a
        # filtered query must not crash on .lower(). Ingress is type-checked
        # upstream, but a restored buffer may still hold such entries.
        if method is not None:
            entries = [e for e in entries if method.lower() in str(e.method).lower()]
        if resource is not None:
            entries = [e for e in entries if resource.lower() in str(e.resource).lower()]
        # Exact, not a substring: a request id identifies one request, and Needs
        # Attention links to it to show what became of an uncertain write.
        if request_id is not None:
            entries = [e for e in entries if str(e.request_id) == request_id]
        if since is not None:
            entries = [e for e in entries if e.timestamp >= since]

        entries.reverse()
        return entries

    def query(
        self,
        *,
        access_key_id: str | None = None,
        outcome: str | None = None,
        client_ip: str | None = None,
        method: str | None = None,
        resource: str | None = None,
        request_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry] | None:
        """Return filtered, paginated audit entries in reverse-chronological order.

        Returns None when outcome is not a recognised value, allowing the caller
        to return a 400 error rather than silently returning an empty list.
        limit is capped at MAX_QUERY_LIMIT (500). offset is applied after filtering.
        """
        entries = self._filtered(
            access_key_id=access_key_id, outcome=outcome, client_ip=client_ip,
            method=method, resource=resource, request_id=request_id, since=since,
        )
        if entries is None:
            return None
        limit = min(limit, MAX_QUERY_LIMIT)
        return entries[offset:offset + limit]

    def count(
        self,
        *,
        access_key_id: str | None = None,
        outcome: str | None = None,
        client_ip: str | None = None,
        method: str | None = None,
        resource: str | None = None,
        request_id: str | None = None,
        since: datetime | None = None,
    ) -> int | None:
        """Return the number of entries matching query()'s filters, ignoring limit/offset.

        Returns None for an unrecognised outcome, like query().
        """
        entries = self._filtered(
            access_key_id=access_key_id, outcome=outcome, client_ip=client_ip,
            method=method, resource=resource, request_id=request_id, since=since,
        )
        return None if entries is None else len(entries)

    def clear(self) -> None:
        """Remove all in-memory entries without touching storage."""
        self._log.clear()

    def resize(self, maxlen: int) -> None:
        """Replace the deque with a new one of the given maxlen.

        If maxlen is smaller than the current length, the oldest entries are
        dropped automatically by the deque constructor.
        """
        self._log = deque(self._log, maxlen=maxlen)

    async def async_save(self) -> None:
        """Snapshot the in-memory buffer to HA storage.

        No-op when no store is configured. An audit_flush_interval of 0 ("Never")
        disables only the periodic timer; the shutdown handler still saves.
        """
        if self._store is None:
            return
        await self._store.async_save({
            "version": AUDIT_STORAGE_VERSION,
            "entries": [e.to_dict() for e in self._log],
        })

    async def async_load(self) -> None:
        """Populate the in-memory buffer from HA storage.

        Corrupt or unrecognised entries are skipped with a warning. No-op when
        no store is configured.
        """
        if self._store is None:
            return
        raw = await self._store.async_load()
        if not raw:
            return
        raw_version = raw.get("version")
        if raw_version != AUDIT_STORAGE_VERSION:
            _LOGGER.warning(
                "Audit storage version mismatch (got %s, expected %s); discarding on-disk log",
                raw_version, AUDIT_STORAGE_VERSION,
            )
            return
        from .access_key_schema import normalize_access_key_fields

        # Payloads saved before device codes were redacted are cleaned once here,
        # and the cleaned log is saved at once so the codes leave the disk.
        scrubbed = 0
        for r in raw.get("entries", []):
            try:
                r = normalize_access_key_fields(r)
                ts = parse_datetime(r["timestamp"])
                if ts is None:
                    raise ValueError(f"unparseable timestamp: {r['timestamp']!r}")
                outcome = r["outcome"]
                if outcome not in self._VALID_OUTCOMES:
                    raise ValueError(f"unrecognised outcome: {outcome!r}")
                payload = r.get("payload")
                if payload is not None:
                    clean = _logged_payload(payload)
                    scrubbed += clean != payload
                    payload = clean
                self._log.append(AuditEntry(
                    request_id=r["request_id"],
                    timestamp=ts,
                    access_key_id=r["access_key_id"],
                    access_key_name=r["access_key_name"],
                    method=r["method"],
                    resource=r["resource"],
                    outcome=outcome,
                    client_ip=r["client_ip"],
                    pass_through=r.get("pass_through", False),
                    payload=payload,
                    mesa_advisory=r.get("mesa_advisory", False),
                    preset=r.get("preset"),
                    stale_tools_advisory=r.get("stale_tools_advisory", False),
                    ha_user_id=r.get("ha_user_id"),
                    originating_access_key_id=r.get("originating_access_key_id"),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("Skipping corrupt audit entry: %s", exc)
        if scrubbed:
            await self.async_save()

    async def async_wipe(self) -> None:
        """Clear all entries from memory and write an empty snapshot to disk."""
        self._log.clear()
        if self._store is None:
            return
        await self._store.async_save({
            "version": AUDIT_STORAGE_VERSION,
            "entries": [],
        })

    def __len__(self) -> int:
        return len(self._log)
