"""Protected bounded faulthandler snapshots and whole-dump pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass

from .const import (
    CAP_DENY, FAULT_LOG_DEFAULT_LIMIT, FAULT_LOG_MAX_CURSOR_LEN, MAX_LOG_ENTRIES,
    MAX_SEARCH_QUERY_LEN,
    FAULT_LOG_FILENAME, FAULT_LOG_MAX_BYTES, FAULT_LOG_MAX_LINES,
    FAULT_LOG_MAX_BLOCK_BYTES, FAULT_LOG_MAX_PAGE_BYTES,
)
from .helpers import _safe_log_path, _strict_diagnostic_text, _strict_phoenix_log_text
from .helpers import effective_cap
from .request_authority import check_request_authority, request_authority
from .tool_common import _tool_error, _tool_success

# A thread header is part of its enclosing dump, never a new crash boundary.
_HEADER = re.compile(r"^(?:Fatal Python error: [^\n]+|Timeout \([^\n]+\)!)$", re.MULTILINE)
_FRAME_PATH = re.compile(r'(?m)^(\s*File )"([^"\n]+)"(, line \d+)')
_THREAD_ID = re.compile(r"(?m)^((?:Current thread|Thread) )0x[0-9a-fA-F]+")
_URL = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_ABSOLUTE_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|/)[^\s\"'<>:,;()]+")


@dataclass(frozen=True)
class FaultSnapshot:
    """A bounded byte window with explicit unavailable and coverage states."""

    status: str
    text: str = ""
    clipped: bool = False


def read_fault_snapshot(config_dir: str) -> FaultSnapshot:
    """Open only the fixed regular file, without following a final symlink.

    The directory descriptor pins the config root. Nonblocking open prevents a
    special file from hanging the executor; the descriptor is checked before
    reading. Hard links are refused because they can alias another config file.
    """
    directory = None
    descriptor = None
    try:
        directory = os.open(config_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            descriptor = os.open(
                FAULT_LOG_FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return FaultSnapshot("absent")
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            return FaultSnapshot("unavailable")
        if not before.st_size:
            return FaultSnapshot("empty")
        start = max(0, before.st_size - FAULT_LOG_MAX_BYTES)
        os.lseek(descriptor, start, os.SEEK_SET)
        chunks = []
        remaining = before.st_size - start
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                return FaultSnapshot("unavailable")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns
                or after.st_nlink != 1):
            return FaultSnapshot("unavailable")
        raw = b"".join(chunks)
        # A byte window can start inside a line or UTF-8 character. Discard that
        # line and its enclosing dump instead of inventing a truncated traceback.
        if start:
            raw = raw.partition(b"\n")[2]
        text = raw.decode("utf-8", errors="strict")
        lines = text.splitlines(keepends=True)
        clipped = bool(start) or len(lines) > FAULT_LOG_MAX_LINES
        if len(lines) > FAULT_LOG_MAX_LINES:
            text = "".join(lines[-FAULT_LOG_MAX_LINES:])
        return FaultSnapshot("available", text, clipped)
    except (OSError, UnicodeError, ValueError):
        return FaultSnapshot("unavailable")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _scrub_block(text, access_key, hass):
    """Keep frame locations useful without disclosing host paths or thread IDs."""
    text = _URL.sub("<redacted-url>", text)
    lines = []
    for line in text.splitlines(keepends=True):
        match = _FRAME_PATH.match(line)
        if match:
            # These are structurally identified source paths, not entity IDs.
            # Scrubbing them as prose would erase filenames such as sensor.py.
            path = _strict_diagnostic_text(_safe_log_path(match[2], hass))
            line = (match[1] + '"' + path + '"' + match[3]
                    + _strict_phoenix_log_text(line[match.end():], access_key, hass))
        else:
            line = _strict_phoenix_log_text(line, access_key, hass)
        lines.append(_ABSOLUTE_PATH.sub("<redacted-path>", _THREAD_ID.sub(r"\1<thread>", line)))
    return "".join(lines)


def _decode_cursor(cursor, access_key):
    """Authenticate and validate cursor shape before any file I/O."""
    try:
        payload, signature = cursor.split(".")
        expected = hmac.new(access_key.access_key_hash.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        offset = value["offset"]
        if (not isinstance(value["view"], str) or re.fullmatch(r"[0-9a-f]{64}", value["view"]) is None
                or isinstance(offset, bool)
                or not isinstance(offset, int) or offset < 1):
            raise ValueError
        return value
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("Invalid or stale fault-log cursor. Restart the query.") from exc


def _cursor_offset(cursor, fingerprint, access_key):
    """Bind a validated cursor to the current scrubbed view and filters."""
    value = _decode_cursor(cursor, access_key)
    if value["view"] != fingerprint:
        raise ValueError("Invalid or stale fault-log cursor. Restart the query.")
    return value["offset"]


def fault_log_page(snapshot, access_key, hass, *, search, order, limit, cursor):
    """Scrub before matching and paginate complete retained blocks only."""
    source = {
        "kind": "home_assistant_fault_log", "status": snapshot.status,
        "semantics": "faulthandler_blocks", "pagination": "bounded_snapshot",
        "coverage": "bounded_tail", "clipped": snapshot.clipped,
    }
    body = {
        "source": source, "filters": {"search": search, "order": order},
        "crash_recorded": None, "count": 0, "matched_blocks": 0,
        "has_more": False, "next_cursor": None, "entries": [],
    }
    if snapshot.status == "unavailable":
        source["reason"] = "The protected fault-log source could not be read reliably."
        return body
    headers = list(_HEADER.finditer(snapshot.text))
    if headers:
        body["crash_recorded"] = True
    elif not snapshot.text.strip() and not snapshot.clipped:
        body["crash_recorded"] = False
    omitted = snapshot.clipped
    if headers and snapshot.text[:headers[0].start()].strip():
        omitted = True
    if snapshot.text.strip() and not headers:
        omitted = True
    blocks = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(snapshot.text)
        block = snapshot.text[header.start():end]
        # The final unterminated line is a partial write, not a complete dump.
        if (not block.endswith("\n") or not re.search(
                r"(?m)^(?:Current thread|Thread) 0x[0-9a-fA-F]+", block
        ) or len(block.encode("utf-8")) > FAULT_LOG_MAX_BLOCK_BYTES):
            omitted = True
            continue
        safe = _scrub_block(block, access_key, hass).strip()
        if len(json.dumps({"text": safe}).encode("utf-8")) > FAULT_LOG_MAX_BLOCK_BYTES:
            omitted = True
            continue
        blocks.append(safe)
    if omitted:
        source["status"] = "degraded"
        body["warnings"] = ["Older, partial, unrecognized or oversized dump content was omitted. This is not complete crash history."]
    if order == "newest_first":
        blocks.reverse()
    # Bind to the entire safe snapshot, so changed coverage also invalidates a
    # cursor. Hidden text is never hashed into access key-visible cursor metadata.
    fingerprint = hashlib.sha256(json.dumps(
        [source, body["filters"], blocks], sort_keys=True
    ).encode()).hexdigest()
    offset = _cursor_offset(cursor, fingerprint, access_key) if cursor else 0
    matched = [block for block in blocks if not search or search.casefold() in block.casefold()]
    if cursor and offset >= len(matched):
        raise ValueError("Invalid or stale fault-log cursor. Restart the query.")
    entries = []
    # Include envelope/filter/warning bytes and reserve the largest permitted
    # cursor. The reserve also covers count growth and entry separators.
    size = len(json.dumps(body).encode("utf-8")) + FAULT_LOG_MAX_CURSOR_LEN
    for block in matched[offset:offset + limit]:
        entry = {"text": block}
        entry_size = len(json.dumps(entry).encode("utf-8"))
        if size + entry_size > FAULT_LOG_MAX_PAGE_BYTES:
            break
        entries.append(entry)
        size += entry_size
    next_offset = offset + len(entries)
    body.update(entries=entries, count=len(entries), matched_blocks=len(matched),
                has_more=next_offset < len(matched))
    if body["has_more"]:
        payload = base64.urlsafe_b64encode(json.dumps(
            {"offset": next_offset, "view": fingerprint}, separators=(",", ":")
        ).encode()).decode().rstrip("=")
        signature = hmac.new(access_key.access_key_hash.encode(), payload.encode(), hashlib.sha256).hexdigest()
        body["next_cursor"] = payload + "." + signature
    if body["crash_recorded"] is False:
        body["message"] = "No fault dump is recorded in this source. This does not prove Home Assistant has never crashed."
    return body


async def async_fault_log_query(args, access_key, hass):
    """Apply capability and current-authority gates around the protected read."""
    resource = "get_logs"
    if any(effective_cap(access_key, cap) == CAP_DENY for cap in ("cap_log_read", "cap_diagnostics")):
        return _tool_error("Forbidden."), "denied", resource
    if any(key in args for key in ("level", "integration", "logger", "since", "until", "path",
                                   "detail_ref", "detail_field", "message_index", "detail_offset")):
        return _tool_error("fault_log supports search, order, limit and cursor only."), "invalid_request", resource
    search = args.get("search")
    cursor = args.get("cursor")
    order = args.get("order", "newest_first")
    limit = args.get("limit", FAULT_LOG_DEFAULT_LIMIT)
    if search is not None and (not isinstance(search, str) or len(search) > MAX_SEARCH_QUERY_LEN):
        return _tool_error("Invalid fault-log search."), "invalid_request", resource
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > FAULT_LOG_MAX_CURSOR_LEN):
        return _tool_error("Invalid fault-log cursor."), "invalid_request", resource
    if not isinstance(order, str) or order not in ("newest_first", "oldest_first"):
        return _tool_error("Invalid fault-log order."), "invalid_request", resource
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LOG_ENTRIES:
        return _tool_error("limit must be an integer from 1 to 100."), "invalid_request", resource
    if cursor:
        try:
            _decode_cursor(cursor, access_key)
        except ValueError as exc:
            return _tool_error(str(exc)), "invalid_request", resource
    check_request_authority()
    snapshot = await hass.async_add_executor_job(read_fault_snapshot, hass.config.config_dir)
    check_request_authority()
    authority = request_authority.get()
    if authority is not None:
        access_key = authority.current_access_key()
    if any(effective_cap(access_key, cap) == CAP_DENY for cap in ("cap_log_read", "cap_diagnostics")):
        return _tool_error("Forbidden."), "denied", resource
    try:
        body = fault_log_page(snapshot, access_key, hass, search=search or None,
                              order=order, limit=limit, cursor=cursor or None)
    except ValueError as exc:
        return _tool_error(str(exc)), "invalid_request", resource
    unavailable = body["source"]["status"] == "unavailable"
    result = _tool_error if unavailable else _tool_success
    return result(json.dumps(body)), "invalid_request" if unavailable else "allowed", resource
