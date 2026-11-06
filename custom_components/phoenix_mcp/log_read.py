"""Read Home Assistant's system log through scoped, redacted, cursor-paged queries."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from .const import LOG_DETAIL_STALE_MESSAGE, SYSTEM_LOG_MAX_RESPONSE_BYTES
from .policy_engine import Permission, resolve
from .redaction import (
    _redact_entity_ids, _scrub_diagnostic_str, _scrub_log_text,
    redact_inaccessible_entity_ids_in_text,
)
from .system_log_payload import (
    compact_log_entry, log_detail_chunk, log_detail_ref, log_payload_budget, log_result_size,
)

if TYPE_CHECKING:
    from .access_key_store import AccessKeyRecord


# Ranks the level of a RECORD. DEBUG and INFO stay although HA's system_log
# handler is attached at WARNING and stores neither: this map classifies whatever
# HA supplies, and dropping an entry would silently narrow what an operator can
# read. The narrower set a CALLER may ask for is const.LOG_LEVELS; the two differ
# deliberately.
_LOG_LEVEL_RANK: dict[str, int] = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 3}

_PHOENIX_LOGGER_PREFIXES = ("homeassistant.components.phoenix_mcp", "custom_components.phoenix_mcp")


class SystemLogUnavailableError(Exception):
    """Raised when the system-log source is absent."""

    status = "unavailable"


class SystemLogDegradedError(Exception):
    """Raised when the system-log source exists but cannot be read safely."""

    status = "degraded"


def check_system_log_compat(hass: HomeAssistant) -> str | None:
    """Probe that hass.data["system_log"] has the shape get_logs reads, best-effort.

    Returns None when compatible or absent, else a short reason.
    """
    try:
        syslog = hass.data.get("system_log")
        if syslog is None:
            return None
        records = getattr(syslog, "records", None)
        if records is None:
            return "system_log data has no 'records' attribute"
        if not callable(getattr(records, "values", None)):
            return "system_log records is no longer a dict-like mapping"
        return None
    except Exception as err:  # advisory probe must never abort setup
        return f"system_log probe could not run: {err}"


@dataclass(frozen=True)
class LogEntryPage:
    """One stateless page over Home Assistant's live deduplicated log ring."""

    entries: list[dict]
    matched_buckets: int
    has_more: bool
    next_cursor: str | None
    source: dict[str, Any]
    filters: dict[str, Any]
    warnings: list[str]
    byte_limited: bool = False
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the response envelope, the same one measured against the byte budget."""
        body: dict[str, Any]
        if self.detail is not None:
            body = {"source": self.source, "detail": self.detail, "payload": log_payload_budget()}
            if self.warnings:
                body["warnings"] = self.warnings
            return body
        body = {
            "source": self.source,
            "filters": self.filters,
            "count": len(self.entries),
            "total": self.matched_buckets,
            "matched_buckets": self.matched_buckets,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
            "entries": self.entries,
            "payload": log_payload_budget(byte_limited=self.byte_limited),
        }
        if self.warnings:
            body["warnings"] = self.warnings
        return body


_LOG_CURSOR_VERSION = 1

_LOG_REDACTED_PATH = "<redacted-path>"

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

_ULID_RE = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", re.IGNORECASE)

_LONG_HEX_RE = re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE)


def _logger_prefix_matches(logger_name: str, prefix: str) -> bool:
    """Match one complete logger namespace, never an adjacent lookalike."""
    return logger_name == prefix or logger_name.startswith(prefix + ".")


def _log_iso(timestamp: float) -> str:
    """Return a stable UTC ISO timestamp for one system-log epoch value."""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _log_timestamp(value: Any) -> float | None:
    """Coerce one system-log timestamp without accepting booleans or infinities."""
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        value = value.timestamp()
    if not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _safe_log_path(path: Any, hass: HomeAssistant) -> str:
    """Return only a normalized HA package or custom-component relative path."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return _LOG_REDACTED_PATH
    normalized = path.replace("\\", "/")
    config_dir = str(getattr(hass.config, "config_dir", "") or "").replace("\\", "/").rstrip("/")
    if config_dir and normalized.startswith(config_dir + "/"):
        normalized = normalized[len(config_dir) + 1 :]
    elif "/custom_components/" in normalized:
        normalized = "custom_components/" + normalized.split("/custom_components/", 1)[1]
    elif "/homeassistant/" in normalized:
        normalized = "homeassistant/" + normalized.split("/homeassistant/", 1)[1]

    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        return _LOG_REDACTED_PATH
    safe_roots = ("homeassistant", "components", "custom_components")
    if not pure.parts or pure.parts[0] not in safe_roots:
        return _LOG_REDACTED_PATH
    return pure.as_posix()


def _safe_log_location(value: Any, hass: HomeAssistant, *, root_cause: bool) -> dict | None:
    """Normalize a system-log source or root-cause tuple without host paths."""
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        return None
    line = value[1]
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        line = None
    result: dict[str, Any] = {"file": _safe_log_path(value[0], hass), "line": line}
    if root_cause and len(value) >= 3:
        function = value[2]
        result["function"] = function if isinstance(function, str) and function.isidentifier() else None
    return result


def _strict_diagnostic_text(text: str) -> str:
    """Scrub diagnostic credentials, topology and opaque identifiers."""
    scrubbed = _scrub_diagnostic_str(_scrub_log_text(text))
    scrubbed = _UUID_RE.sub("<redacted-id>", scrubbed)
    scrubbed = _ULID_RE.sub("<redacted-id>", scrubbed)
    scrubbed = _LONG_HEX_RE.sub("<redacted-id>", scrubbed)

    return scrubbed


def _strict_phoenix_log_text(text: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """Apply the stronger Phoenix self-diagnostics text boundary."""
    return redact_inaccessible_entity_ids_in_text(_strict_diagnostic_text(text), access_key, hass)


def _scoped_log_text(text: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """Home Assistant log text without the entities this access key may not see.

    A log is full of dotted names that are no entity at all, such as modules and
    file names, so only a name that resolves to something hidden is replaced.
    """
    return _redact_entity_ids(
        _scrub_log_text(text), hass,
        lambda entity_id: resolve(entity_id, access_key, hass) not in (Permission.NO_ACCESS, Permission.DENY),
        "<redacted-entity>",
    )


def _log_filter_fingerprint(filters: dict[str, Any], phoenix_only: bool) -> str:
    """Hash the effective filters so a cursor cannot silently change queries."""
    payload = {**filters, "phoenix_only": phoenix_only}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _encode_log_cursor(
    *,
    snapshot_at: float,
    timestamp: float,
    bucket_id: str,
    filter_hash: str,
    since: float | None,
    until: float | None,
) -> str:
    payload = {
        "v": _LOG_CURSOR_VERSION,
        "snapshot": snapshot_at,
        "timestamp": timestamp,
        "bucket": bucket_id,
        "filters": filter_hash,
        "since": since,
        "until": until,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_log_cursor(
    cursor: str, filter_hash: str
) -> tuple[float, tuple[float, str], float | None, float | None]:
    """Decode and bind one opaque log cursor to the current effective filters."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(payload, dict) or payload.get("v") != _LOG_CURSOR_VERSION:
            raise ValueError
        snapshot = _log_timestamp(payload.get("snapshot"))
        timestamp = _log_timestamp(payload.get("timestamp"))
        since = _log_timestamp(payload.get("since")) if payload.get("since") is not None else None
        until = _log_timestamp(payload.get("until")) if payload.get("until") is not None else None
        bucket = payload.get("bucket")
        if (
            snapshot is None
            or timestamp is None
            or not isinstance(bucket, str)
            or len(bucket) != 64
            or any(character not in "0123456789abcdef" for character in bucket)
            or payload.get("filters") != filter_hash
        ):
            raise ValueError
    except Exception as err:
        raise ValueError("Invalid cursor for these log filters.") from err
    return snapshot, (timestamp, bucket), since, until


def _log_bucket_id(record: Any) -> str:
    """Return a non-reversible deterministic tie-breaker for one deduplicated bucket."""
    identity = getattr(record, "key", None)
    if identity is None:
        identity = (
            getattr(record, "name", None),
            getattr(record, "source", None),
            getattr(record, "root_cause", None),
        )
    return hashlib.sha256(repr(identity).encode(errors="replace")).hexdigest()


def _log_messages(value: Any) -> list[str] | None:
    """Copy Home Assistant's retained message variants without accepting mappings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return None
    try:
        messages = list(value)
    except Exception:
        return None
    if not messages or not all(isinstance(item, str) for item in messages):
        return None
    return messages


def _log_exception(value: Any) -> str | None:
    """Normalize Home Assistant's current string and older iterable exception shapes."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return "".join(value) or None
    return None


def collect_log_entries(
    hass: HomeAssistant,
    level: str,
    integration: str | None,
    logger: str | None,
    search: str | None,
    since: datetime | None,
    until: datetime | None,
    since_query: str | None,
    until_query: str | None,
    limit: int,
    cursor: str | None,
    *,
    phoenix_only: bool = False,
    access_key: AccessKeyRecord | None = None,
    detail_ref: str | None = None,
    detail_field: str = "message",
    message_index: int = 0,
    detail_offset: int = 0,
) -> LogEntryPage:
    """Read, validate, scrub, filter, and paginate the live system-log ring."""
    min_rank = _LOG_LEVEL_RANK.get(level.upper(), _LOG_LEVEL_RANK["WARNING"])
    syslog = hass.data.get("system_log")
    if syslog is None:
        raise SystemLogUnavailableError("the system_log integration is not loaded")
    records = getattr(syslog, "records", None)
    values = getattr(records, "values", None)
    if records is None or not callable(values):
        raise SystemLogDegradedError("system_log records is no longer a mapping")

    capacity = getattr(records, "maxlen", None)
    capacity_valid = isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0
    warnings: list[str] = []
    if not capacity_valid:
        capacity = None
        warnings.append("The system-log ring capacity is unavailable on this Home Assistant version.")

    cursor_filters = {
        "level": level,
        "integration": integration,
        "logger": logger,
        "search": search,
        "since": since_query,
        "until": until_query,
        "time_basis": "latest_occurrence",
    }
    filter_hash = _log_filter_fingerprint(cursor_filters, phoenix_only)
    if cursor:
        snapshot_at, cursor_key, cursor_since, cursor_until = _decode_log_cursor(cursor, filter_hash)
        since_ts = cursor_since
        until_ts = cursor_until
    else:
        snapshot_at, cursor_key = utcnow().timestamp(), None
        since_ts = since.timestamp() if since else None
        until_ts = until.timestamp() if until else None
    effective_filters = {
        **cursor_filters,
        "since": _log_iso(since_ts) if since_ts is not None else None,
        "until": _log_iso(until_ts) if until_ts is not None else None,
    }
    entries_with_keys: list[tuple[tuple[float, str], dict]] = []
    retained_first: list[float] = []
    retained_latest: list[float] = []
    skipped = 0
    try:
        raw_records = list(values())
    except Exception as err:
        raise SystemLogDegradedError(
            "system_log records could not be read"
        ) from err
    for record in raw_records:
        record_level = getattr(record, "level", "")
        logger_name = getattr(record, "name", "")
        latest = _log_timestamp(getattr(record, "timestamp", None))
        first = _log_timestamp(getattr(record, "first_occurred", None))
        messages = _log_messages(getattr(record, "message", None))
        count = getattr(record, "count", 1)
        if (
            not isinstance(record_level, str)
            or record_level not in _LOG_LEVEL_RANK
            or not isinstance(logger_name, str)
            or not logger_name
            or latest is None
            or first is None
            or messages is None
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            skipped += 1
            continue
        retained_first.append(first)
        retained_latest.append(latest)
        is_phoenix = any(
            _logger_prefix_matches(logger_name, prefix)
            for prefix in _PHOENIX_LOGGER_PREFIXES
        )
        if phoenix_only != is_phoenix:
            continue
        if _LOG_LEVEL_RANK[record_level] < min_rank:
            continue
        if integration:
            if not (
                _logger_prefix_matches(
                    logger_name, f"homeassistant.components.{integration}"
                )
                or _logger_prefix_matches(
                    logger_name, f"custom_components.{integration}"
                )
            ):
                continue
        if logger and not _logger_prefix_matches(logger_name, logger):
            continue
        if since_ts is not None and latest < since_ts:
            continue
        if until_ts is not None and latest > until_ts:
            continue
        if latest > snapshot_at:
            continue

        scrub: Callable[[str], str] = _scrub_log_text
        if phoenix_only:
            if access_key is None:
                raise SystemLogDegradedError("Phoenix diagnostics access key context is unavailable")
            scrub = lambda value: _strict_phoenix_log_text(value, access_key, hass)
        elif access_key is not None:
            scrub = lambda value: _scoped_log_text(value, access_key, hass)
        safe_messages = [scrub(message) for message in messages]
        exception = _log_exception(getattr(record, "exception", None))
        safe_exception = scrub(exception) if exception else None
        if search:
            needle = search.casefold()
            searchable = [*safe_messages, safe_exception or ""]
            if not any(needle in value.casefold() for value in searchable):
                continue

        bucket_id = _log_bucket_id(record)
        sort_key = (latest, bucket_id)
        entries_with_keys.append((sort_key, {
            "first_occurred": _log_iso(first),
            "latest_occurred": _log_iso(latest),
            "level": record_level,
            "logger": logger_name,
            "messages": safe_messages,
            "exception": safe_exception,
            "occurrences": count,
            "source": _safe_log_location(getattr(record, "source", None), hass, root_cause=False),
            "root_cause": _safe_log_location(getattr(record, "root_cause", None), hass, root_cause=True),
        }))

    entries_with_keys.sort(key=lambda item: item[0], reverse=True)
    matched_buckets = len(entries_with_keys)
    if cursor_key is not None:
        entries_with_keys = [item for item in entries_with_keys if item[0] < cursor_key]
    if skipped:
        warnings.append(f"Skipped {skipped} malformed system-log bucket(s).")
    if cursor:
        warnings.append(
            "Pagination is best-effort over a live deduplicated ring; buckets updated after the first page may move."
        )
    source = {
        "status": "degraded" if skipped or not capacity_valid else "available",
        "kind": "home_assistant_system_log",
        "semantics": "deduplicated_buckets",
        "pagination": "best_effort_live_ring",
        "capacity": capacity,
        "retained_buckets": len(raw_records),
        "skipped_buckets": skipped,
        "earliest_first_occurred": _log_iso(min(retained_first)) if retained_first else None,
        "latest_occurred": _log_iso(max(retained_latest)) if retained_latest else None,
        "read_at": _log_iso(snapshot_at),
        "recorded_levels": ["WARNING", "ERROR", "CRITICAL"],
    }
    def reference(key: tuple[float, str], entry: dict) -> str | None:
        return log_detail_ref(entry, access_key, phoenix_only=phoenix_only, bucket_id=key[1]) if access_key else None

    if detail_ref:
        for key, entry in entries_with_keys:
            current_ref = reference(key, entry)
            if current_ref is not None and hmac.compare_digest(detail_ref, current_ref):
                detail = log_detail_chunk(entry, detail_ref, detail_field, message_index, detail_offset)
                detail_page = LogEntryPage([], 0, False, None, source, effective_filters, warnings, detail=detail)
                if log_result_size(detail_page.as_dict()) > SYSTEM_LOG_MAX_RESPONSE_BYTES:
                    raise SystemLogDegradedError("system_log metadata exceeds the response budget")
                return detail_page
        raise ValueError(LOG_DETAIL_STALE_MESSAGE)

    def page(items: list[tuple[tuple[float, str], dict]], *, byte_limited: bool = False) -> LogEntryPage:
        has_more = len(entries_with_keys) > len(items)
        next_cursor = None
        if has_more and items:
            last_key = items[-1][0]
            next_cursor = _encode_log_cursor(
                snapshot_at=snapshot_at, timestamp=last_key[0], bucket_id=last_key[1],
                filter_hash=filter_hash, since=since_ts, until=until_ts,
            )
        return LogEntryPage(
            entries=[entry for _, entry in items], matched_buckets=matched_buckets,
            has_more=has_more, next_cursor=next_cursor,
            source=source, filters=effective_filters, warnings=warnings, byte_limited=byte_limited,
        )

    page_items: list[tuple[tuple[float, str], dict]] = []
    for key, entry in entries_with_keys[:limit]:
        candidate = [*page_items, (key, compact_log_entry(entry, reference(key, entry)))]
        if log_result_size(page(candidate).as_dict()) > SYSTEM_LOG_MAX_RESPONSE_BYTES:
            if not page_items:
                raise SystemLogDegradedError("system_log metadata exceeds the response budget")
            return page(page_items, byte_limited=True)
        page_items = candidate
    return page(page_items)
