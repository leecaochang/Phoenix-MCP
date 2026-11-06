"""Log, logbook and integration log-level tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import re
from typing import Any

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.loader import IntegrationNotFound
from homeassistant.helpers import entity_registry as er
from homeassistant import loader
from homeassistant.util.ulid import ulid_to_bytes_or_none
from homeassistant.util.dt import utcnow

from ..access_key_store import AccessKeyRecord
from ..const import (
    CAP_DENY, DEBUG_CAPTURE_CAPACITY, DOMAIN, GET_LOGS_LEVEL_ERROR_MESSAGE,
    LOG_DETAIL_STALE_MESSAGE, LOG_LEVEL_ERROR_MESSAGE, LOG_LEVELS, MAX_LOG_ENTRIES,
    MAX_LOGBOOK_HOME_DAYS, MAX_LOGBOOK_NARROWED_DAYS, MAX_LOGBOOK_RESOURCE_IDS,
    MAX_SEARCH_QUERY_LEN,
)
from ..data import PhoenixData
from ..debug_capture import DEBUG_CAPTURE_RUNTIME_KEY, integration_domain
from ..fault_log import async_fault_log_query
from ..helpers import parse_time_param as _parse_time_param
from ..tool_input import str_arg
from ..catalog_text import diff_summary_fields as _summary
from ..capability import effective_cap
from ..log_read import (
    _strict_phoenix_log_text, collect_log_entries as _collect_log_entries, SystemLogDegradedError, SystemLogUnavailableError,
)
from ..logger_control import (
    IntegrationOverride, LoggerControlUnavailable, VALID_LEVELS as LOGGER_CONTROL_LEVELS,
    VALID_PERSISTENCE as LOGGER_CONTROL_PERSISTENCE,
)
from ..policy_engine import (
    filter_service_response, Permission, resolve_config_entry_registry_access,
    resolve_device_registry_access, resolve_read, resolve_registry_access,
)
from ..recorder_queries import iso_utc
from ..request_authority import check_request_authority, request_authority
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _gate, _tool_error, _tool_success, longest_fitting
from ..tool_contracts import normalize_tool_args
from ..ws_dispatch import async_ws_command, WsDispatchError


_LOGGER = logging.getLogger(__name__)


_LOG_INTEGRATION_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_LOG_LOGGER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


async def _tool_log_query(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    tool_name: str,
    phoenix_only: bool,
) -> tuple[dict, str, str]:
    """Run one bounded, source-truthful query over HA's system-log ring."""
    check_request_authority()
    authority = request_authority.get()
    if authority is not None:
        access_key = authority.current_access_key()
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or (
        phoenix_only and effective_cap(access_key, "cap_diagnostics") == CAP_DENY
    ):
        return _tool_error("Forbidden."), "denied", tool_name

    detail_ref = args.get("detail_ref")
    detail_field = args.get("detail_field", "message")
    message_index = args.get("message_index", 0)
    detail_offset = args.get("detail_offset", 0)
    if "detail_ref" in args:
        if not isinstance(detail_ref, str) or re.fullmatch(r"[0-9a-f]{64}", detail_ref) is None:
            return _tool_error("Invalid log detail reference."), "invalid_request", tool_name
        if any(key in args for key in ("level", "integration", "logger", "search", "since", "until", "limit", "cursor")):
            return _tool_error("Log detail retrieval cannot be combined with query filters or pagination."), "invalid_request", tool_name
        if not isinstance(detail_field, str) or detail_field not in ("message", "exception"):
            return _tool_error("detail_field must be message or exception."), "invalid_request", tool_name
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (message_index, detail_offset)):
            return _tool_error("message_index and detail_offset must be nonnegative integers."), "invalid_request", tool_name
        if detail_field == "exception" and "message_index" in args:
            return _tool_error("message_index is supported only for message details."), "invalid_request", tool_name
    elif any(key in args for key in ("detail_field", "message_index", "detail_offset")):
        return _tool_error("Detail parameters require detail_ref."), "invalid_request", tool_name

    # A wrong-SHAPED level degrades to absent (str_arg), but a wrong VALUE is
    # refused rather than coerced: silently answering a WARNING query for a
    # caller that asked for INFO would hide the dropped level. Safe to surface
    # because the cap check above already ran, so a denied access key never reaches
    # a message about its own argument.
    # A non-string filter would otherwise read as no filter and widen the page.
    malformed = [key for key in ("integration", "logger", "search")
                 if args.get(key) is not None and not isinstance(args[key], str)]
    if malformed:
        return _tool_error(f"{', '.join(malformed)} must be a string."), "invalid_request", tool_name
    raw_level = str_arg(args.get("level"), "WARNING").strip().upper() or "WARNING"
    if raw_level not in LOG_LEVELS:
        message = LOG_LEVEL_ERROR_MESSAGE if phoenix_only else GET_LOGS_LEVEL_ERROR_MESSAGE
        return _tool_error(message), "invalid_request", tool_name

    integration = str_arg(args.get("integration")).strip() or None
    logger = str_arg(args.get("logger")).strip() or None
    search = str_arg(args.get("search")).strip() or None
    cursor = str_arg(args.get("cursor")).strip() or None
    if phoenix_only:
        integration = None
    if integration and (
        len(integration) > MAX_SEARCH_QUERY_LEN
        or _LOG_INTEGRATION_RE.fullmatch(integration) is None
    ):
        return _tool_error("Invalid integration domain."), "invalid_request", tool_name
    if logger and (
        len(logger) > MAX_SEARCH_QUERY_LEN
        or _LOG_LOGGER_RE.fullmatch(logger) is None
    ):
        return _tool_error("Invalid logger prefix."), "invalid_request", tool_name
    if search and len(search) > MAX_SEARCH_QUERY_LEN:
        return _tool_error(
            f"search must be at most {MAX_SEARCH_QUERY_LEN} characters."
        ), "invalid_request", tool_name
    if cursor and len(cursor) > 4096:
        return _tool_error("Invalid cursor for these log filters."), "invalid_request", tool_name

    since = None
    until = None
    raw_since = str_arg(args.get("since")).strip()
    raw_until = str_arg(args.get("until")).strip()
    try:
        field = "since"
        if raw_since:
            since = _parse_time_param(raw_since)
        field = "until"
        if raw_until:
            until = _parse_time_param(raw_until)
    except ValueError as err:
        return _tool_error(f"Invalid {field} time format. {err}"), "invalid_request", tool_name
    if since is not None and until is not None and since >= until:
        return _tool_error("since must be earlier than until."), "invalid_request", tool_name

    # Default is the same as Home Assistant's stock system-log ring size.
    limit = 50
    raw_limit = args.get("limit")
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
            if not (1 <= limit <= MAX_LOG_ENTRIES):
                limit = max(1, min(limit, MAX_LOG_ENTRIES))
        except (TypeError, ValueError):
            limit = 50

    try:
        page = _collect_log_entries(
            hass,
            raw_level,
            integration,
            logger,
            search,
            since,
            until,
            raw_since or None,
            raw_until or None,
            limit,
            cursor,
            phoenix_only=phoenix_only,
            access_key=access_key,
            detail_ref=detail_ref,
            detail_field=detail_field,
            message_index=message_index,
            detail_offset=detail_offset,
        )
    except (SystemLogUnavailableError, SystemLogDegradedError) as exc:
        body = {
            "source": {
                "status": exc.status,
                "kind": "home_assistant_system_log",
                "semantics": "deduplicated_buckets",
                "pagination": "best_effort_live_ring",
                "reason": str(exc),
            },
            "filters": {
                "level": raw_level,
                "integration": integration,
                "logger": logger,
                "search": search,
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "time_basis": "latest_occurrence",
            },
            "count": 0,
            "total": 0,
            "matched_buckets": 0,
            "has_more": False,
            "next_cursor": None,
            "entries": [],
        }
        return _tool_error(json.dumps(body)), "invalid_request", tool_name
    except ValueError as exc:
        return _tool_error(str(exc)), "invalid_request", tool_name
    check_request_authority()
    if authority is not None:
        access_key = authority.current_access_key()
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or (
        phoenix_only and effective_cap(access_key, "cap_diagnostics") == CAP_DENY
    ):
        return _tool_error("Forbidden."), "denied", tool_name
    return _tool_success(json.dumps(page.as_dict(), default=str)), "allowed", tool_name


async def _tool_get_logs(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read the system ring or protected fault-dump blocks."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_logs"
    if any(key in args for key in _LOG_DETAIL_KEYS):
        return _tool_error("Fetch a bucket's full text with get_log_detail and its detail_ref."), "invalid_request", "get_logs"
    source = args.get("source", "system_log")
    if source == "fault_log":
        return await async_fault_log_query(args, access_key, hass)
    if source == "integration_debug":
        return _integration_debug_query(args, access_key, hass)
    if source != "system_log":
        return _tool_error("source must be system_log, fault_log or integration_debug."), "invalid_request", "get_logs"
    if "order" in args:
        return _tool_error("order is supported only for fault_log."), "invalid_request", "get_logs"
    return await _tool_log_query(
        args, access_key, hass, tool_name="get_logs", phoenix_only=False
    )


_LOG_DETAIL_KEYS = ("detail_ref", "detail_field", "message_index", "detail_offset")


def _integration_debug_query(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[dict, str, str]:
    """Read captured INFO/DEBUG records of the integrations this access key can see."""

    tool = "get_logs"
    if effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", tool
    unsupported = [key for key in ("level", "logger", "since", "until", "order") if key in args]
    if unsupported:
        return _tool_error("integration_debug accepts integration, search, limit and cursor only."), "invalid_request", tool
    malformed = [key for key in ("integration", "search", "cursor") if args.get(key) is not None and not isinstance(args[key], str)]
    if malformed:
        return _tool_error(f"{', '.join(malformed)} must be a string."), "invalid_request", tool
    limit = args.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LOG_ENTRIES:
        return _tool_error(f"limit must be an integer from 1 to {MAX_LOG_ENTRIES}."), "invalid_request", tool
    cursor = (args.get("cursor") or "").strip()
    if cursor and not cursor.isdigit():
        return _tool_error("Invalid cursor for integration_debug."), "invalid_request", tool
    search = (args.get("search") or "").strip()
    domains = _visible_logger_domains(access_key, hass)
    if integration := (args.get("integration") or "").strip():
        domains = domains & {integration}
    capture = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    source: dict[str, Any] = {"kind": "integration_debug_capture",
                              "levels": ["INFO", "DEBUG"], "capacity": DEBUG_CAPTURE_CAPACITY}
    if capture is None:
        return _tool_success(json.dumps({"source": {**source, "status": "unavailable"}, "count": 0,
                                         "has_more": False, "next_cursor": None, "entries": []})), "allowed", tool
    entries: list[dict[str, Any]] = []
    has_more = False
    for sequence, created, level, logger, message, truncated in capture.newest(domains, int(cursor) if cursor else None):
        safe = _strict_phoenix_log_text(message, access_key, hass)
        # Search sees only scrubbed text, so it can never probe a redacted value.
        if search and search.casefold() not in safe.casefold():
            continue
        if len(entries) == limit:
            has_more = True
            break
        entries.append({"sequence": sequence, "time": datetime.fromtimestamp(created, timezone.utc).isoformat(),
                        "level": level, "integration": integration_domain(logger), "logger": logger,
                        "message": safe, "truncated": truncated})
    body = {
        "source": {**source, "status": "available", "retained": len(capture.records),
                   "capturing_since": capture.attached_at.isoformat(),
                   "note": "Only records below WARNING from integrations whose level was raised appear here; "
                           "WARNING and above are in source system_log."},
        "count": len(entries), "has_more": has_more,
        "next_cursor": str(entries[-1]["sequence"]) if has_more and entries else None,
        "entries": entries,
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", tool


async def _tool_get_log_detail(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: one bounded scrubbed text chunk of a log bucket by its detail_ref."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_log_detail"
    if "detail_ref" not in args:
        return _tool_error("Missing required argument: detail_ref"), "invalid_request", "get_log_detail"
    detail_args = {key: args[key] for key in _LOG_DETAIL_KEYS if key in args}
    # A reference is bound to its namespace, so it can only match where it was
    # minted; Phoenix's own diagnostics are tried only with Diagnostics allowed.
    namespaces = [False] + ([True] if effective_cap(access_key, "cap_diagnostics") != CAP_DENY else [])
    result: tuple[dict, str, str] = _tool_error(LOG_DETAIL_STALE_MESSAGE), "invalid_request", "get_log_detail"
    for phoenix_only in namespaces:
        result = await _tool_log_query(detail_args, access_key, hass, tool_name="get_log_detail", phoenix_only=phoenix_only)
        if result[1] != "invalid_request" or result[0]["content"][0]["text"] != LOG_DETAIL_STALE_MESSAGE:
            return result
    return result


def _logbook_string_list(args: dict, key: str) -> tuple[list[str], str | None]:
    """Validate, trim and deduplicate one id list; returns (values, error or None)."""
    raw = args.get(key)
    if raw is None:
        return [], None
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        return [], f"{key} must be an array of non-empty strings."
    unique = list(dict.fromkeys(item.strip() for item in raw))
    if len(unique) > MAX_LOGBOOK_RESOURCE_IDS:
        return [], f"{key} accepts at most {MAX_LOGBOOK_RESOURCE_IDS} unique values."
    return unique, None


def _logbook_time(value: Any, default: datetime) -> datetime:
    """Parse one logbook bound to UTC; a time without an offset is HA local time."""
    if value in (None, ""):
        return default
    return _parse_time_param(value).astimezone(timezone.utc)


def _logbook_entry_visible(
    entry: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    entity_permissions: dict[str, Permission],
    device_permissions: dict[str, Permission],
) -> bool:
    """Enforce entity-first visibility, with device access for device-only rows."""
    if "entity_id" in entry:
        entity_id = entry.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            return False
        permission = entity_permissions.get(entity_id)
        if permission is None:
            permission = entity_permissions[entity_id] = resolve_read(entity_id, access_key, hass)
        return permission in (Permission.READ, Permission.WRITE)

    device_id = entry.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        return False
    permission = device_permissions.get(device_id)
    if permission is None:
        permission = device_permissions[device_id] = resolve_device_registry_access(
            device_id, access_key, hass
        )
    return permission in (Permission.READ, Permission.WRITE)


def _logbook_context_scoped(entry: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Keep what caused a logbook row only when the access key may see the cause.

    A hidden cause loses all its context fields: a name would disclose it and a redaction marker
    would confirm it. A user ID is never returned.
    """
    cause = entry.get("context_entity_id")
    hidden = cause is not None and resolve_read(cause, access_key, hass) not in (Permission.READ, Permission.WRITE)
    return {
        key: value for key, value in entry.items()
        if key != "context_user_id" and not (hidden and key.startswith("context_"))
    }


async def _tool_get_logbook(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read the human-readable logbook (requires cap_log_read)."""
    args, error = normalize_tool_args("get_logbook", args)
    if error:
        return _tool_error(error), "invalid_request", "get_logbook"
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_logbook"

    entity_ids, entity_error = _logbook_string_list(args, "entity_ids")
    device_ids, device_error = _logbook_string_list(args, "device_ids")
    if entity_error or device_error:
        return (
            _tool_error(entity_error or device_error or "Invalid filters."),
            "invalid_request",
            "get_logbook",
        )
    if ("entity_ids" in args or "device_ids" in args) and not (entity_ids or device_ids):
        # An empty resource selector is a mistake, not a request for the whole home.
        return _tool_error("A resources target needs at least one entity_ids or device_ids value."), "invalid_request", "get_logbook"

    raw_context = args.get("context_id")
    context_id = None
    if raw_context is not None:
        if (
            not isinstance(raw_context, str)
            or not (context_id := raw_context.strip())
            or ulid_to_bytes_or_none(context_id) is None
        ):
            return _tool_error("Invalid context_id."), "invalid_request", "get_logbook"
    if context_id and (entity_ids or device_ids):
        return _tool_error(
            "context_id cannot be combined with entity_ids or device_ids."
        ), "invalid_request", "get_logbook"
    raw_search = args.get("search")
    if raw_search is not None and not isinstance(raw_search, str):
        return _tool_error("search must be a string."), "invalid_request", "get_logbook"
    search = raw_search.strip() if raw_search is not None else None
    search = search or None
    if search and len(search) > MAX_SEARCH_QUERY_LEN:
        return _tool_error(
            f"search must be at most {MAX_SEARCH_QUERY_LEN} characters."
        ), "invalid_request", "get_logbook"

    raw_limit = args.get("limit")
    if raw_limit is None:
        limit = 100
    elif type(raw_limit) is not int or not 1 <= raw_limit <= 1000:
        return _tool_error(
            "limit must be an integer from 1 through 1000."
        ), "invalid_request", "get_logbook"
    else:
        limit = raw_limit

    now = utcnow()
    try:
        field = "start_time"
        start_time = _logbook_time(args.get("start_time"), now - timedelta(hours=24))
        field = "end_time"
        end_time = _logbook_time(args.get("end_time"), now)
    except ValueError as err:
        return _tool_error(f"Invalid {field} format. {err}"), "invalid_request", "get_logbook"
    if start_time >= end_time:
        return _tool_error(
            "start_time must be earlier than end_time. A relative time such as 7d means 7 days "
            "ago, and start_time defaults to 24h before now, so keep the original start_time "
            "when you continue from older_end_time."
        ), "invalid_request", "get_logbook"
    narrowed = bool(entity_ids or device_ids or context_id)
    max_days = MAX_LOGBOOK_NARROWED_DAYS if narrowed else MAX_LOGBOOK_HOME_DAYS
    if end_time - start_time > timedelta(days=max_days):
        return _tool_error(
            f"This logbook query supports at most {max_days} days."
        ), "invalid_request", "get_logbook"

    # Unreadable resources are named, hidden and missing alike, and the rest are
    # read; with none left the query is refused rather than widened home-wide.
    entity_access = {entity_id: resolve_read(entity_id, access_key, hass) if valid_entity_id(entity_id)
                     else Permission.NOT_FOUND for entity_id in entity_ids}
    device_access = {device_id: resolve_device_registry_access(device_id, access_key, hass)
                     for device_id in device_ids}
    not_found = [item for item, permission in (*entity_access.items(), *device_access.items())
                 if permission not in (Permission.READ, Permission.WRITE)]
    entity_ids = [item for item in entity_ids if item not in not_found]
    device_ids = [item for item in device_ids if item not in not_found]
    if not_found and not entity_ids and not device_ids:
        return _tool_error(
            "Requested resource not found: " + ", ".join(json.dumps(item[:80]) for item in not_found) + "."
        ), "not_found", "get_logbook"
    requested_permissions: list[Permission] = [entity_access[item] for item in entity_ids]
    requested_permissions.extend(device_access[item] for item in device_ids)

    payload: dict[str, Any] = {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }
    if entity_ids:
        payload["entity_ids"] = entity_ids
    if device_ids:
        payload["device_ids"] = device_ids
    if context_id:
        payload["context_id"] = context_id

    try:
        result = await async_ws_command(hass, "logbook/get_events", payload)
    except WsDispatchError as exc:
        return _tool_error(f"Failed to read logbook: {exc}"), "invalid_request", "get_logbook"

    if not isinstance(result, list):
        # An unexpected shape must not pass as an empty logbook, which reads as
        # "nothing happened" rather than "the read did not work".
        return _tool_error(
            "Unexpected logbook response from Home Assistant."
        ), "invalid_request", "get_logbook"
    entity_permissions = dict(zip(entity_ids, requested_permissions[:len(entity_ids)]))
    device_permissions = dict(zip(device_ids, requested_permissions[len(entity_ids):]))
    scoped = [
        entry
        for entry in result
        if isinstance(entry, dict)
        and _logbook_entry_visible(
            entry, access_key, hass, entity_permissions, device_permissions
        )
    ]
    scoped = filter_service_response(
        [_logbook_context_scoped(entry, access_key, hass) for entry in scoped], access_key, hass,
    )
    if not isinstance(scoped, list) or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("when"), (int, float))
        or isinstance(entry.get("when"), bool)
        or not math.isfinite(float(entry["when"]))
        for entry in scoped
    ):
        return _tool_error(
            "Unexpected logbook entry shape from Home Assistant."
        ), "invalid_request", "get_logbook"

    if search:
        needle = search.casefold()
        scoped = [
            entry
            for entry in scoped
            if any(
                isinstance(entry.get(field), str)
                and needle in entry[field].casefold()
                for field in ("name", "message", "state")
            )
        ]
    scoped.sort(key=lambda entry: float(entry["when"]))
    total = len(scoped)
    scoped = scoped[-limit:]

    def build(count: int) -> dict[str, Any]:
        # The newest entries are kept; a slice that would not fit one result keeps fewer.
        start = len(scoped) - count
        crowded = False
        if 0 < start < len(scoped) and scoped[start - 1]["when"] == scoped[start]["when"]:
            # A slice never starts inside entries sharing one instant, so older_end_time,
            # an exclusive end, reads that whole instant next; only an instant with more
            # entries than one result holds is kept partly, and said so.
            later = next((index for index in range(start, len(scoped))
                          if scoped[index]["when"] != scoped[start]["when"]), len(scoped))
            crowded = later == len(scoped)
            start = start if crowded else later
        kept = scoped[start:]
        count = len(kept)
        body: dict[str, Any] = {
            "count": count,
            "total": total,
            "has_more": total > count,
            "truncated": total > count,
            "filters": {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "entity_ids": entity_ids,
                "device_ids": device_ids,
                "context_id": context_id,
                "search": search,
                "limit": limit,
            },
            # Home Assistant's own logbook stamps entries in epoch seconds; every
            # other tool's time is ISO 8601, so the same key reads the same way.
            "entries": [{**entry, "when": iso_utc(datetime.fromtimestamp(float(entry["when"]), tz=timezone.utc))}
                        for entry in kept],
        }
        if not_found:
            body["not_found"] = not_found
        if total > count and kept:
            body["older_end_time"] = iso_utc(datetime.fromtimestamp(float(kept[0]["when"]), tz=timezone.utc))
        if crowded:
            body["warnings"] = ["More entries share one instant than one result holds; narrow target or search to read them all."]
        return body

    return _tool_success(json.dumps(longest_fitting(build, len(scoped)), default=str)), "allowed", "get_logbook"


_LOGGER_CONTROL_FINGERPRINT = "_phoenix_logger_control_fingerprint"
_INTEGRATION_DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")


def _visible_logger_domains(access_key: AccessKeyRecord, hass: HomeAssistant) -> set[str]:
    """Integration domains visible through one config entry or registry entity."""
    domains = {
        entry.domain
        for entry in hass.config_entries.async_entries()
        if entry.domain != DOMAIN
        and resolve_config_entry_registry_access(entry.entry_id, access_key, hass)
        in (Permission.READ, Permission.WRITE)
    }
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        platform = getattr(entry, "platform", None)
        if (
            isinstance(platform, str)
            and platform != DOMAIN
            and resolve_registry_access(entry.entity_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
        ):
            domains.add(platform)
    return domains


def _override_dict(value: IntegrationOverride | None) -> dict[str, str] | None:
    return value.to_dict() if value is not None else None


def _logger_context_fingerprint(
    domain: str,
    loggers: set[str],
    override: IntegrationOverride | None,
    visible: bool,
) -> str:
    payload = json.dumps(
        {
            "integration": domain,
            "loggers": sorted(loggers),
            "override": _override_dict(override),
            "visible": visible,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _aggregate_log_level(levels: dict[str, str]) -> str:
    unique = set(levels.values())
    return next(iter(unique)) if len(unique) == 1 else "MIXED"


async def _tool_list_integration_log_levels(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """List scoped integration-aware logger levels."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_integration_log_levels"
    manager = data.logger_control
    if manager is None:
        return _tool_success(json.dumps({
            "source": {"status": "unavailable", "warning": "Logger state is unavailable."},
            "count": 0,
            "integrations": [],
            "read_at": utcnow().isoformat(),
        })), "allowed", "list_integration_log_levels"
    source = manager.adapter.source_status()
    source["timed_restoration"] = (
        "available" if manager.storage_available else "unavailable"
    )
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for domain in sorted(_visible_logger_domains(access_key, hass)):
        try:
            loggers = await manager.adapter.declared_loggers(domain)
            levels = manager.adapter.effective_levels(loggers)
            try:
                override = manager.adapter.get_override(domain)
                override_status = "available"
            except LoggerControlUnavailable as exc:
                override = None
                override_status = "unavailable"
                warnings.append(f"{domain}: {exc}")
            timed = manager.active(domain)
            rows.append({
                "integration": domain,
                "effective_level": _aggregate_log_level(levels),
                "logger_levels": levels,
                "override": {
                    "status": override_status,
                    "setting": _override_dict(override),
                },
                "timed_override": (
                    {
                        "level": timed.applied.level,
                        "expires_at": timed.expires_at.isoformat(),
                    }
                    if timed is not None else None
                ),
            })
        except LoggerControlUnavailable as exc:
            warnings.append(f"{domain}: {exc}")
    if warnings and source.get("status") == "available":
        source = {"status": "degraded", "warnings": warnings}
    elif warnings:
        source = {**source, "warnings": warnings}
    return _tool_success(json.dumps({
        "source": source,
        "count": len(rows),
        "integrations": rows,
        "read_at": utcnow().isoformat(),
    }, default=str)), "allowed", "list_integration_log_levels"


def _log_control_args(
    args: dict[str, Any],
) -> tuple[str, str, str, int | None] | str:
    domain = args.get("integration")
    level = args.get("level")
    persistence = args.get("persistence")
    duration = args.get("duration_minutes")
    if not isinstance(domain, str) or not _INTEGRATION_DOMAIN_RE.fullmatch(domain):
        return "integration must be a valid integration domain."
    if not isinstance(level, str) or level not in LOGGER_CONTROL_LEVELS:
        return "level must be NOTSET, DEBUG, INFO, WARNING, ERROR, or CRITICAL."
    if not isinstance(persistence, str) or persistence not in LOGGER_CONTROL_PERSISTENCE:
        return "persistence must be none, once, or permanent."
    if duration is not None and (
        isinstance(duration, bool) or not isinstance(duration, int) or not 5 <= duration <= 120
    ):
        return "duration_minutes must be an integer from 5 through 120."
    if duration is not None and (persistence != "none" or level == "NOTSET"):
        return "duration_minutes requires persistence=none and a level other than NOTSET."
    if level == "NOTSET" and persistence != "none":
        return "NOTSET requires persistence=none."
    return domain, level, persistence, duration


def _log_control_warnings(level: str) -> list[str]:
    if level not in ("DEBUG", "INFO"):
        return []
    return [
        "Verbose logging can increase disk use.",
        "Third-party integration logs may contain sensitive output outside Phoenix's control.",
    ]


def _build_diff_set_integration_log_level(
    domain: str,
    before: IntegrationOverride | None,
    after: IntegrationOverride | None,
    loggers: set[str],
    duration: int | None,
) -> dict[str, Any]:
    warnings = _log_control_warnings(after.level if after else "NOTSET")
    return {
        "kind": "system_action",
        **_summary("integration.log_level", label=domain),
        "target": {"type": "integration", "id": domain, "label": domain},
        "preview": {
            "before": _override_dict(before),
            "after": _override_dict(after),
            "affected_loggers": sorted(loggers),
            "duration_minutes": duration,
            "persistence": (
                "runtime-only" if after and after.persistence == "none"
                else "one-restart" if after and after.persistence == "once"
                else "permanent" if after else "cleared"
            ),
            "warnings": warnings,
        },
    }


async def _validated_log_control_context(
    args: dict[str, Any], access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[str, str, str, int | None, set[str], IntegrationOverride | None, str] | tuple[dict, str, str]:
    parsed = _log_control_args(args)
    if isinstance(parsed, str):
        return _tool_error(parsed), "invalid_request", "set_integration_log_level"
    domain, level, persistence, duration = parsed
    visible = domain in _visible_logger_domains(access_key, hass)
    if not visible or domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", domain
    try:
        await loader.async_get_integration(hass, domain)
    except IntegrationNotFound:
        return _tool_error("Integration not found."), "not_found", domain
    manager = data.logger_control
    if manager is None:
        return _tool_error("Logger state is unavailable."), "invalid_request", domain
    try:
        loggers = await manager.adapter.declared_loggers(domain)
        before = manager.adapter.get_override(domain)
    except LoggerControlUnavailable:
        return _tool_error("Home Assistant logger state is unavailable or incompatible."), "invalid_request", domain
    fingerprint = _logger_context_fingerprint(domain, loggers, before, visible)
    return domain, level, persistence, duration, loggers, before, fingerprint


async def _tool_set_integration_log_level(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Gate a scoped process-wide integration logger change without MESA."""
    # Capability ordering is intentional: argument errors must not become an
    # oracle for access keys lacking either explicit capability.
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_log_level"
    if effective_cap(access_key, "cap_log_control") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_log_level"
    context = await _validated_log_control_context(args, access_key, hass, data)
    if len(context) == 3 and isinstance(context[0], dict):
        return context
    domain, level, persistence, duration, loggers, before, fingerprint = context
    after = None if level == "NOTSET" else IntegrationOverride(level, persistence)
    manager = data.logger_control
    assert manager is not None
    if duration is None and before == after and manager.active(domain) is None:
        return _tool_success(json.dumps({
            "integration": domain, "changed": False, "setting": _override_dict(before)
        })), "allowed", f"integration:{domain}"
    execute_args = dict(args)
    execute_args[_LOGGER_CONTROL_FINGERPRINT] = fingerprint
    blocked = await _gate(
        "cap_log_control", access_key, hass, data,
        tool_name="set_integration_log_level",
        args=execute_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda: _build_diff_set_integration_log_level(
            domain, before, after, loggers, duration
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_integration_log_level(execute_args, access_key, hass, data)


async def _execute_set_integration_log_level(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or effective_cap(
        access_key, "cap_log_control"
    ) == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_log_level"
    context = await _validated_log_control_context(args, access_key, hass, data)
    if len(context) == 3 and isinstance(context[0], dict):
        return context
    domain, level, persistence, duration, loggers, before, fingerprint = context
    if args.get(_LOGGER_CONTROL_FINGERPRINT) != fingerprint:
        return _tool_error(
            "The integration logger set, override, or visibility changed after approval. Review it again."
        ), "denied", f"integration:{domain}"
    after = None if level == "NOTSET" else IntegrationOverride(level, persistence)
    manager = data.logger_control
    assert manager is not None
    if duration is None and before == after and manager.active(domain) is None:
        return _tool_success(json.dumps({
            "integration": domain, "changed": False, "setting": _override_dict(before)
        })), "allowed", f"integration:{domain}"
    try:
        await manager.async_set(
            domain=domain,
            desired=after,
            prior=before,
            loggers=loggers,
            owner_access_key_id=access_key.id,
            duration_minutes=duration,
        )
        effective = manager.adapter.effective_levels(loggers)
        current = manager.adapter.get_override(domain)
    except (LoggerControlUnavailable, OSError):
        _LOGGER.exception("set_integration_log_level failed for %s", domain)
        return _tool_error("Failed to update integration log level."), "invalid_request", f"integration:{domain}"
    timed = manager.active(domain)
    return _tool_success(json.dumps({
        "integration": domain,
        "changed": True,
        "before": _override_dict(before),
        "after": _override_dict(current),
        "affected_loggers": sorted(loggers),
        "effective_level": _aggregate_log_level(effective),
        "logger_levels": effective,
        "persistence_semantics": (
            "runtime-only" if current and current.persistence == "none"
            else "one-restart" if current and current.persistence == "once"
            else "permanent" if current else "cleared"
        ),
        "timed_override": (
            {"expires_at": timed.expires_at.isoformat()} if timed else None
        ),
        "warnings": _log_control_warnings(level),
    }, default=str)), "allowed", f"integration:{domain}"
