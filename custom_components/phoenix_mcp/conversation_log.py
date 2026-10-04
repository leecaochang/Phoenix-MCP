"""Bounded, sanitized conversation evidence, independent of execution journals."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import timedelta
import functools
import hashlib
import ipaddress
import time
import json
import logging
import math
import re
from typing import Any
from uuid import UUID, uuid4

from homeassistant.util.dt import utcnow, parse_datetime

from .const import (
    CONVERSATION_LOG_DEFAULTS, CONVERSATION_LOG_LIMITS, CONVERSATION_LOG_RESERVE_BYTES,
    CONVERSATION_LOG_MAX_NODES, CONVERSATION_LOG_MAX_DEPTH, CONVERSATION_LOG_METADATA,
    CONVERSATION_LOG_ENTRY_BYTES, CONVERSATION_LOG_PAYLOAD_BYTES, CONVERSATION_LOG_SCAN_BYTES,
    CONVERSATION_LOG_MAX_EVENTS, CONVERSATION_LOG_MAX_OPERATIONS, CONVERSATION_LOG_MAX_APPROVALS,
    CONVERSATION_LOG_OUTCOMES, CONVERSATION_LOG_FLUSH_SECONDS, CONVERSATION_LOG_STATUS_PRIORITY,
)
from .helpers import is_sensitive_key, redact_secrets_in_text

_LOGGER = logging.getLogger(__name__)
_REDACTED = "<redacted>"
_SECRET = re.compile(
    r"(?i)(\b(?:bearer|basic)\s+)\S+|"
    r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b|"
    r"((?<![\w])(?:[\"']?)(?:api[_ -]?key|password|passwd|secret|access[_ -]?token|refresh[_ -]?token|authorization|private[_ -]?key|pin|psk|encryption[_ -]?key)(?:[\"']?)\s*(?::|=|\bis\b)\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S)
_DATA_URI = re.compile(r"data:[^\s\"')<>]+", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|(?<!\w)(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,39}(?!\w)", re.I)
_CODE = re.compile(r'(?i)(\bcode["\']?\s*(?::|=|\bis\b)\s*["\']?)([0-9][0-9 -]{2,})')
_SERVICE_CODE = re.compile(r"(?i)(\bcode[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:pin|psk|encryption_key|code)=)[^&#\s\"']+")


def sensitive_key(key: str, service: bool = False) -> bool:
    """Service codes are credentials; protocol error codes remain readable."""
    return is_sensitive_key(key) or key.casefold() in {"pin", "psk", "encryption_key"} or (service and key.casefold() == "code")


def hide_address(match: re.Match) -> str:
    try:
        ipaddress.ip_address(match[0])
    except ValueError:
        return match[0]
    return _REDACTED


_current: ContextVar[tuple[Any, str, int] | None] = ContextVar("phoenix_conversation", default=None)
_transport: ContextVar[str | None] = ContextVar("phoenix_conversation_session", default=None)
_assistant_flush: ContextVar[Callable | None] = ContextVar("phoenix_assistant_flush", default=None)
_tool: ContextVar[str | None] = ContextVar("phoenix_conversation_tool", default=None)


def sanitized(value: Any, *, max_bytes: int, hide_ip: bool = False) -> Any:
    """Copy supported data through bounded redaction; never stringify unknown objects."""
    remaining = max_bytes
    nodes = 0

    def text(raw: str, service: bool = False) -> str:
        nonlocal remaining
        remaining -= len(raw.encode("utf-8"))
        if remaining < 0:
            raise ValueError("payload budget")
        clean = _PRIVATE_KEY.sub(_REDACTED, raw)
        clean = re.sub(r"(?i)\b(bearer|basic)\s+[^\s\"'<>]+", r"\1 <redacted>", clean)
        lines = clean.split("\n")
        block_indent = None
        for index, line in enumerate(lines):
            indent = len(line) - len(line.lstrip())
            if block_indent is not None:
                if not line.strip() or indent > block_indent:
                    lines[index] = " " * indent + _REDACTED
                    continue
                block_indent = None
            match = re.match(r"^\s*(?:-\s*)?([\w.-]+)\s*:\s*([|>][-+0-9]*|)\s*$", line)
            if match and sensitive_key(match.group(1), service or "service" in raw or "alarm_control_panel" in raw):
                block_indent = indent
        clean = redact_secrets_in_text("\n".join(lines)) or ""
        clean = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + _REDACTED, clean)
        clean = _CODE.sub(lambda m: m[1] + _REDACTED, clean)
        if service or "service_data" in raw or "alarm_control_panel" in raw or "lock.unlock" in raw:
            clean = _SERVICE_CODE.sub(lambda m: m[1] + _REDACTED, clean)
        clean = _QUERY_SECRET.sub(lambda m: m[1] + _REDACTED, clean)
        clean = _DATA_URI.sub("<attachment omitted>", clean)
        return _IP.sub(hide_address, clean) if hide_ip else clean

    def visit(obj: Any, depth: int = 0, service: bool = False) -> Any:
        nonlocal nodes, remaining
        nodes += 1
        remaining -= 8
        if nodes > CONVERSATION_LOG_MAX_NODES or depth > CONVERSATION_LOG_MAX_DEPTH or remaining < 0:
            raise ValueError("payload budget")
        if isinstance(obj, str):
            return text(obj, service)
        if obj is None or type(obj) in (bool, int):
            return obj
        if isinstance(obj, float):
            if not math.isfinite(obj):
                raise ValueError("nonfinite")
            return obj
        if isinstance(obj, (bytes, bytearray)):
            return {"attachment": True, "bytes": len(obj), "omitted": "media"}
        if isinstance(obj, (list, tuple, set)):
            return [visit(item, depth + 1, service) for item in obj]
        if isinstance(obj, dict):
            service = service or isinstance(obj.get("service"), (str, dict)) or (
                isinstance(obj.get("action"), str) and obj["action"].startswith(("alarm_control_panel.", "lock.")))
            kind = obj.get("type")
            if isinstance(kind, str) and kind in {"image", "image_url", "input_image", "audio", "input_audio"}:
                return {"attachment": True, "type": kind, "mime_type": text(str(obj.get("mimeType", obj.get("mime_type", ""))))[:128], "omitted": "media"}
            result = {}
            for key, item in obj.items():
                if not isinstance(key, str):
                    raise ValueError("unsupported key")
                safe_key = text(key)
                if key.startswith("_") or sensitive_key(key, service):
                    result[safe_key] = _REDACTED
                elif key in {"blob", "base64"}:
                    result[safe_key] = "<attachment omitted>"
                else:
                    result[safe_key] = visit(item, depth + 1, service or key in {"service", "service_data"} or obj.get("domain") in {"alarm_control_panel", "lock"})
            return result
        raise ValueError("unsupported data")

    result = visit(value)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > max_bytes:
        raise ValueError("payload budget")
    return result


def best_effort(method: Callable) -> Callable:
    """A failed optional recorder must never become an execution failure."""
    @functools.wraps(method)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except Exception:
            self.available = False
            _LOGGER.warning("Conversation recording unavailable")
            return None
    return guarded


def execution_summary(entry: dict) -> str:
    """Aggregate receipts without allowing a later read to hide an earlier failure."""
    if "receipts" in entry:
        results = dict(entry["receipts"])
        for state in entry["approval_states"].values():
            results[state["tool_id"]] = state["outcome"]
        for tool_id in entry["uncertain_tools"]:
            results[tool_id] = "unknown"
        outcomes = set(results.values()) | set(entry["overflow_outcomes"])
        if entry["overflow_running"]:
            outcomes.add("running")
        if entry["status"] != "running" and "running" in outcomes:
            outcomes.discard("running")
            outcomes.add("unknown")
        if "unknown" in outcomes:
            return "unknown"
        if "pending_approval" in outcomes:
            return "pending_approval"
        if len(outcomes) > 1:
            return "partial"
        return next(iter(outcomes), "no_tools")
    return "unknown"


def setting_limit(key: str, value: Any) -> int:
    """Validate persisted limits without silently enabling unlimited capture."""
    lo, hi = CONVERSATION_LOG_LIMITS[key]
    return value if type(value) is int and lo <= value <= hi else CONVERSATION_LOG_DEFAULTS[key]


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def clipped(value: Any, limit: int) -> Any:
    """Shorten only already sanitized text; never retain a raw truncated prefix."""
    if isinstance(value, str):
        marker = "\n<text shortened>"
        low, high = 0, min(len(value), max(0, limit))
        while low < high:
            middle = (low + high + 1) // 2
            if json_size(value[:middle] + marker) <= limit:
                low = middle
            else:
                high = middle - 1
        return value[:low] + marker
    if isinstance(value, dict):
        result = {}
        remaining = limit - 32
        for key, item in value.items():
            cost = json_size(key) + 4
            if remaining < cost + 64:
                break
            safe = item if json_size(item) <= remaining - cost else clipped(item, remaining - cost)
            result[key] = safe
            remaining -= cost + json_size(safe)
        return result
    if isinstance(value, list):
        result_list = []
        remaining = limit - 32
        for item in value:
            if remaining < 64:
                break
            safe = item if json_size(item) <= remaining else clipped(item, remaining)
            result_list.append(safe)
            remaining -= json_size(safe) + 2
        return result_list
    return value


def text_values(value: Any):
    """Search content values rather than JSON syntax or envelope field names."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from text_values(item)
    elif value is not None:
        yield str(value)


def prompt_excerpt(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt[:240]
    if isinstance(prompt, list):
        return " ".join(block["text"] for block in prompt if isinstance(block, dict)
                        and block.get("type") in {"text", "input_text"} and isinstance(block.get("text"), str))[:240]
    return ""


class ConversationLog:
    """Own sanitized evidence and bounded control records independently of payloads."""

    def __init__(self, store: Any = None) -> None:
        self._store = store
        self._entries: dict[str, dict] = {}
        self._sizes: dict[str, int] = {}
        self._field_sizes: dict[str, dict[str, int]] = {}
        self._search: dict[str, str] = {}
        self._settings: Any = None
        self._configuration: tuple = ()
        self._generation = 0
        self._enabled = False
        self._dirty = False
        self._last_save = 0.0
        self._lock = asyncio.Lock()
        self.available = True

    @best_effort
    def configure(self, settings: Any) -> None:
        configuration = tuple(getattr(settings, k, None) for k in (
            "conversation_log_enabled", "disable_all_logging", "log_entity_names", "log_client_ip",
            *CONVERSATION_LOG_DEFAULTS))
        self._settings = settings
        if configuration == self._configuration:
            return
        self._configuration = configuration
        enabled = settings.conversation_log_enabled is True and not settings.disable_all_logging
        if self._enabled and not enabled:
            self._generation += 1
            for entry in self._entries.values():
                entry["capture_closed"] = True
                if entry["status"] == "running":
                    entry.update(status="interrupted", response_status="recording_stopped", coverage="partial_conversation")
                    entry["execution_status"] = execution_summary(entry)
                self._changed(entry, "capture_closed", "status", "response_status", "coverage", "execution_status")
        self._enabled = enabled
        self.prune()

    @property
    def budget(self) -> int:
        return setting_limit("conversation_log_max_mb", getattr(self._settings, "conversation_log_max_mb", None)) * 1_000_000

    @property
    def entry_budget(self) -> int:
        return min(CONVERSATION_LOG_ENTRY_BYTES, self.budget // 4)

    def _size(self) -> int:
        return 16 + sum(size + 2 for size in self._sizes.values())

    def _remove(self, key: str) -> None:
        self._entries.pop(key, None)
        self._sizes.pop(key, None)
        self._field_sizes.pop(key, None)
        self._search.pop(key, None)
        self._dirty = True

    @best_effort
    def prune(self) -> None:
        if self._settings is None:
            return
        cutoff = (utcnow() - timedelta(days=setting_limit("conversation_log_max_days", getattr(self._settings, "conversation_log_max_days", None)))).isoformat()
        for key, entry in list(self._entries.items()):
            if entry["started_at"] < cutoff:
                self._remove(key)
            elif self._sizes[key] > self.entry_budget:
                self._bound(entry)
        while self._entries and (len(self._entries) > setting_limit("conversation_log_max_entries", getattr(self._settings, "conversation_log_max_entries", None)) or self._size() > self.budget):
            self._remove(next(iter(self._entries)))

    @best_effort
    def begin(self, source: str, *, prompt: Any = None, metadata: Any = None) -> tuple[Any, str, int] | None:
        if not self._enabled:
            return (self, "", -1)
        key = uuid4().hex
        now = utcnow().isoformat()
        entry: dict[str, Any] = {
            "id": key, "source": source, "started_at": now, "updated_at": now,
            "coverage": "tool_exchange" if prompt is None and source in {"mcp", "assist", "home_companion"} else "partial_conversation",
            "status": "running", "execution_status": "no_tools", "response_status": "unavailable",
            "prompt": None, "response": None, "events": [], "metadata": {},
            "tool_count": 0, "change_count": 0, "omissions": [], "approval_ids": [],
            "receipts": {}, "approval_states": {}, "uncertain_tools": [], "observed_outcomes": [],
            "overflow_outcomes": [], "overflow_running": 0, "omitted_events": 0,
            "field_omissions": {}, "first_tool": "",
        }
        self._entries[key] = entry
        self._changed(entry)
        handle = (self, key, self._generation)
        if prompt is not None:
            self.assign(handle, "prompt", prompt)
        self.assign(handle, "metadata", metadata or {}, content=False)
        self.prune()
        return handle

    def entry(self, handle: Any) -> dict | None:
        if not handle or handle[0] is not self or handle[2] != self._generation or not self._enabled:
            return None
        return self._entries.get(handle[1])

    def _clean(self, entry: dict, value: Any, content: bool = True, *, limit: int = CONVERSATION_LOG_PAYLOAD_BYTES) -> tuple[Any, str | None]:
        reason = None
        if content and not self._settings.log_entity_names:
            result, reason = None, "privacy"
        else:
            try:
                result = sanitized(value, max_bytes=CONVERSATION_LOG_SCAN_BYTES, hide_ip=not self._settings.log_client_ip)
                if json_size(result) > limit:
                    result = clipped(result, limit)
                    reason = "size_limit"
            except ValueError as error:
                result, reason = None, "size_limit" if str(error) == "payload budget" else "sanitization"
            except Exception:
                result, reason = None, "sanitization"
        if reason and reason not in entry["omissions"]:
            entry["omissions"].append(reason)
        return result, reason

    def _changed(self, entry: dict, *fields: str) -> None:
        if self._entries.get(entry["id"]) is not entry:
            return
        entry["updated_at"] = utcnow().isoformat()
        sizes = self._field_sizes.setdefault(entry["id"], {})
        for key in set(fields or entry.keys()) | {"updated_at", "omissions"}:
            sizes[key] = json_size(key) + 2 + json_size(entry[key])
        self._sizes[entry["id"]] = 2 + sum(sizes.values()) + max(0, len(sizes) - 1) * 2
        self._search.pop(entry["id"], None)
        self._dirty = True

    def _bound(self, entry: dict) -> None:
        # Compact evidence before dropping event headers. Outcome bookkeeping is
        # independent, so neither operation can erase a refusal or late approval.
        for event in entry["events"]:
            if self._sizes[entry["id"]] <= self.entry_budget - CONVERSATION_LOG_RESERVE_BYTES:
                break
            if event.get("payload") is not None:
                event.update(payload=None, omission="size_limit")
                self._changed(entry, "events")
        while entry["events"] and self._sizes[entry["id"]] > self.entry_budget - CONVERSATION_LOG_RESERVE_BYTES:
            entry["events"].pop(0)
            entry["omitted_events"] += 1
            self._changed(entry, "events", "omitted_events")
        if entry["omitted_events"] or any(e.get("omission") == "size_limit" for e in entry["events"]):
            if "size_limit" not in entry["omissions"]:
                entry["omissions"].append("size_limit")
            self._changed(entry, "omissions")

    @best_effort
    def assign(self, handle: Any, field: str, value: Any, *, content: bool = True) -> None:
        entry = self.entry(handle)
        if entry is None:
            return
        if field == "metadata" and isinstance(value, dict):
            entry[field] = {key: self._clean(entry, item, False, limit=512)[0] for key, item in value.items()
                            if key in CONVERSATION_LOG_METADATA and (key != "client_ip" or self._settings.log_client_ip)}
        else:
            entry[field], reason = self._clean(entry, value, content)
            if reason:
                entry["field_omissions"][field] = reason
            else:
                entry["field_omissions"].pop(field, None)
        self._changed(entry, field, "field_omissions")
        self._bound(entry)

    def _account(self, entry: dict, event: dict) -> None:
        kind = event["kind"]
        tool_id = event.get("tool_id") or "unknown"
        receipts = entry["receipts"]
        outcome = event.get("outcome", "unknown")
        if outcome not in CONVERSATION_LOG_OUTCOMES:
            outcome = "unknown"
        if kind in {"tool_call", "tool_result"}:
            result = "running" if kind == "tool_call" else outcome
            if tool_id in receipts or len(receipts) < CONVERSATION_LOG_MAX_OPERATIONS:
                receipts[tool_id] = result
            else:
                entry["overflow_running"] = max(0, entry["overflow_running"] + (1 if kind == "tool_call" else -1))
                if kind == "tool_result" and outcome not in entry["overflow_outcomes"]:
                    entry["overflow_outcomes"].append(outcome)
            if kind == "tool_result" and outcome not in entry["observed_outcomes"]:
                entry["observed_outcomes"].append(outcome)
        if kind == "notice" and outcome == "unknown":
            if tool_id not in entry["uncertain_tools"]:
                if len(entry["uncertain_tools"]) < CONVERSATION_LOG_MAX_OPERATIONS:
                    entry["uncertain_tools"].append(tool_id)
                elif "unknown" not in entry["overflow_outcomes"]:
                    entry["overflow_outcomes"].append("unknown")
        if kind in {"approval_requested", "approval_resolved"}:
            approval_id = event.get("approval_id")
            if approval_id and (approval_id in entry["approval_states"] or len(entry["approval_states"]) < CONVERSATION_LOG_MAX_APPROVALS):
                state = entry["approval_states"].setdefault(approval_id, {"tool_id": tool_id, "outcome": "pending_approval"})
                if kind == "approval_resolved":
                    state["outcome"] = outcome
                    if outcome not in entry["observed_outcomes"]:
                        entry["observed_outcomes"].append(outcome)
                if approval_id not in entry["approval_ids"]:
                    entry["approval_ids"].append(approval_id)
            else:
                if "unknown" not in entry["overflow_outcomes"]:
                    entry["overflow_outcomes"].append("unknown")

    @best_effort
    def event(self, handle: Any, kind: str, payload: Any = None, **facts: Any) -> dict | None:
        entry = self.entry(handle)
        if entry is None:
            return None
        if kind != "assistant_text":
            flush_assistant()
        clean, reason = self._clean(entry, payload)
        event = {"kind": kind, "timestamp": utcnow().isoformat(), "tool_id": _tool.get(), "payload": clean}
        if kind == "tool_call":
            event["parent_tool_id"] = facts.get("parent_tool_id")
        if reason:
            event["omission"] = reason
        if facts.get("outcome") in CONVERSATION_LOG_OUTCOMES:
            event["outcome"] = facts["outcome"]
        # Only server-owned identifiers and enum outcomes bypass content privacy.
        if kind in {"approval_requested", "approval_resolved"} and isinstance(payload, dict):
            event["approval_id"] = self._clean(entry, payload.get("approval_id"), False, limit=128)[0]
            result = payload.get("result")
            outcome = result.get("outcome") if isinstance(result, dict) else None
            outcome = outcome or {"pending": "pending_approval"}.get(str(payload.get("status")), payload.get("status"))
            event["outcome"] = outcome if outcome in CONVERSATION_LOG_OUTCOMES else "unknown"
        if kind == "tool_call":
            entry["tool_count"] += 1
            if isinstance(clean, dict) and isinstance(clean.get("name"), str):
                entry["first_tool"] = entry["first_tool"] or clean["name"][:128]
        if kind == "change":
            entry["change_count"] += 1
        self._account(entry, event)
        if len(entry["events"]) < CONVERSATION_LOG_MAX_EVENTS:
            self._field_sizes[entry["id"]]["events"] += json_size(event) + (2 if entry["events"] else 0)
            entry["events"].append(event)
        else:
            entry["omitted_events"] += 1
            if "size_limit" not in entry["omissions"]:
                entry["omissions"].append("size_limit")
        entry["execution_status"] = execution_summary(entry)
        self._changed(entry, "receipts", "approval_states", "approval_ids", "uncertain_tools",
                      "observed_outcomes", "overflow_outcomes", "overflow_running", "omitted_events",
                      "execution_status", "tool_count", "change_count", "first_tool")
        self._bound(entry)
        self.prune()
        return event if any(item is event for item in entry["events"]) else None

    @best_effort
    def finish(self, handle: Any, *, response: Any = None, status: str = "completed", response_status: str | None = None) -> None:
        entry = self.entry(handle)
        if entry is None:
            return
        if response is not None:
            self.assign(handle, "response", response)
        entry["status"] = status
        entry["response_status"] = response_status or ("produced" if response is not None else "unavailable")
        entry["execution_status"] = execution_summary(entry)
        if status == "completed" and entry["response_status"] == "produced" and entry["prompt"] is not None and entry["response"] is not None and not entry["omissions"]:
            entry["coverage"] = "full_turn"
        entry["finished_at"] = utcnow().isoformat()
        self._changed(entry, "status", "response_status", "execution_status", "coverage", "finished_at")
        self._bound(entry)
        self.prune()

    @best_effort
    def approval(self, approval: Any, *, resolved: bool = False) -> None:
        handle = None if resolved else _current.get()
        entry = self.entry(handle)
        if entry is None:
            entry = next((e for e in self._entries.values() if approval.id in e["approval_ids"]), None)
            handle = (self, entry["id"], self._generation) if entry else None
        if entry is None or entry.get("capture_closed") or self.entry(handle) is None:
            return
        self.event(handle, "approval_resolved" if resolved else "approval_requested", {
            "approval_id": approval.id, "tool_name": approval.tool_name, "status": approval.status,
            "diff": approval.diff, "result": approval.result, "reason": approval.rejected_reason,
        })

    @contextmanager
    def approval_scope(self, approval_id: str):
        entry = next((e for e in self._entries.values() if approval_id in e["approval_ids"] and not e.get("capture_closed")), None)
        ctx = _current.set((self, entry["id"], self._generation) if entry else (self, "", -1))
        try:
            yield
        finally:
            _current.reset(ctx)

    @best_effort
    def bind_chat(self, handle: Any, client_id: Any, token: Any, *, has_history: bool) -> None:
        """Correlate explicit chat identity only, never shared credentials or timing."""
        entry = self.entry(handle)
        if entry is None or getattr(token, "persona", None) == "home_companion":
            return
        try:
            identifier = UUID(client_id) if isinstance(client_id, str) and len(client_id) == 36 else None
        except ValueError:
            return
        if identifier is None:
            return
        key = hashlib.sha256(f"{token.id}:{identifier}".encode()).hexdigest()
        previous = next((e for e in reversed(list(self._entries.values()))
                         if e.get("chat_key") == key and e is not entry), None) if has_history else None
        entry.update(chat_key=key, thread_id=previous["thread_id"] if previous else entry["id"],
                     history_missing=bool(has_history and previous is None))
        self._changed(entry, "chat_key", "thread_id", "history_missing")

    def query(self, *, limit: int = 25, offset: int = 0, search: str = "", source: str = "", outcome: str = "", since: str = "", grouped: bool = False) -> dict:
        self.prune()
        entries = []
        for entry in reversed(list(self._entries.values())):
            outcomes = {entry["status"], entry["execution_status"], entry["response_status"], *(o for o in entry.get("observed_outcomes", []) if o != "pending_approval")}
            if outcomes & {"invalid_request", "not_found", "not_implemented"}:
                outcomes.add("failed")
            if "rejected" in outcomes:
                outcomes.add("denied")
            if (source and entry["source"] != source) or (outcome and outcome not in outcomes) or (since and entry["started_at"] < since):
                continue
            if search:
                if entry["id"] not in self._search:
                    self._search[entry["id"]] = "\n".join(text_values([entry["prompt"], entry["response"], entry["first_tool"], entry["metadata"], [e.get("payload") for e in entry["events"]]])).casefold()
                if search.casefold() not in self._search[entry["id"]]:
                    continue
            entries.append(entry)
        keys = {"id", "source", "started_at", "updated_at", "finished_at", "coverage", "status", "execution_status", "response_status", "tool_count", "change_count", "omissions", "omitted_events", "first_tool"}
        def summary(e: dict) -> dict:
            return {**{k: deepcopy(v) for k, v in e.items() if k in keys}, "excerpt": prompt_excerpt(e["prompt"])}

        if grouped:
            selected = {e.get("thread_id", e["id"]) for e in entries}
            groups: dict[str, list[dict]] = {}
            for e in self._entries.values():
                thread = e.get("thread_id", e["id"])
                if thread in selected:
                    groups.setdefault(thread, []).append(e)
            rows = []
            for thread, turns in groups.items():
                turns.sort(key=lambda e: e["started_at"])
                row = summary(turns[0])
                row.update(id=thread, updated_at=max(e["updated_at"] for e in turns),
                           latest_at=turns[-1]["started_at"])
                if any(e.get("thread_id") for e in turns):
                    row.update(turns=[summary(e) for e in turns],
                               history_missing=thread not in self._entries or bool(turns[0].get("history_missing")))
                for field in ("tool_count", "change_count"):
                    row[field] = sum(e[field] for e in turns)
                for field in ("status", "execution_status"):
                    row[field] = next((value for value in CONVERSATION_LOG_STATUS_PRIORITY if any(e[field] == value for e in turns)), turns[-1][field])
                rows.append(row)
            rows.sort(key=lambda e: e["latest_at"], reverse=True)
            summaries, total = rows[offset:offset + limit], len(rows)
        else:
            summaries, total = [summary(e) for e in entries[offset:offset + limit]], len(entries)
        return {"entries": summaries, "total": total, "enabled": self._enabled,
                "available": self.available, "bytes": self._size(), "max_bytes": self.budget}

    def detail(self, entry_id: str) -> dict | None:
        self.prune()
        return deepcopy(self._entries.get(entry_id))

    async def async_save(self, *, coalesced: bool = False) -> None:
        self.prune()
        if self._store is None or not self._dirty:
            return
        async with self._lock:
            if not self._dirty or (coalesced and time.monotonic() - self._last_save < CONVERSATION_LOG_FLUSH_SECONDS):
                return
            try:
                snapshot = deepcopy(list(self._entries.values()))
                self._dirty = False
                await self._store.async_save({"entries": snapshot})
                self._last_save = time.monotonic()
                self.available = True
            except Exception:
                self._dirty = True
                self.available = False
                _LOGGER.warning("Conversation history could not be saved; will retry")

    async def async_load(self) -> None:
        if self._store is None:
            return
        try:
            saved = await self._store.async_load()
        except Exception:
            self.available = False
            _LOGGER.warning("Conversation history could not be loaded")
            return
        rows = saved.get("entries", []) if isinstance(saved, dict) else []
        if not isinstance(rows, list):
            self.available = False
            return
        for raw in rows:
            try:
                if not isinstance(raw, dict):
                    continue
                entry = sanitized({**raw, "metadata": {}}, max_bytes=max(self.budget, CONVERSATION_LOG_SCAN_BYTES), hide_ip=False)
                fields = ("id", "started_at", "updated_at", "status", "source", "coverage", "response_status", "execution_status")
                if (not all(isinstance(entry.get(k), str) for k in fields)
                        or not all(isinstance(entry.get(k), list) for k in ("events", "approval_ids", "omissions"))
                        or not all(type(entry.get(k)) is int for k in ("tool_count", "change_count"))
                        or not all(k in entry for k in ("prompt", "response"))):
                    continue
                stamp = parse_datetime(entry["started_at"])
                if stamp is None or stamp.tzinfo is None:
                    continue
                entry["started_at"] = stamp.astimezone(utcnow().tzinfo).isoformat()
                if not all(isinstance(event, dict) and isinstance(event.get("kind"), str)
                           and isinstance(event.get("timestamp"), str) and "tool_id" in event for event in entry["events"]):
                    continue
                entry.setdefault("receipts", {})
                entry.setdefault("approval_states", {})
                for key in ("uncertain_tools", "observed_outcomes", "overflow_outcomes"):
                    entry.setdefault(key, [])
                entry.setdefault("overflow_running", 0)
                entry.setdefault("omitted_events", 0)
                entry.setdefault("field_omissions", {})
                entry.setdefault("first_tool", "")
                # Re-sanitize payloads with current privacy controls, not envelope timestamps.
                for field in ("prompt", "response"):
                    if entry[field] is not None:
                        entry[field], reason = self._clean(entry, entry[field])
                        if reason:
                            entry["field_omissions"][field] = reason
                metadata = raw.get("metadata")
                if isinstance(metadata, dict):
                    entry["metadata"] = {key: self._clean(entry, value, False, limit=512)[0] for key, value in metadata.items()
                                         if key in CONVERSATION_LOG_METADATA and (key != "client_ip" or self._settings.log_client_ip)}
                for event in entry["events"]:
                    payload = event.get("payload")
                    if event["kind"] == "routing" and isinstance(payload, dict) and payload.get("result") == "running":
                        payload["result"] = "interrupted"
                    if "receipts" not in raw:
                        if event["kind"].startswith("approval_") and isinstance(payload, dict):
                            event["approval_id"] = payload.get("approval_id")
                            result = payload.get("result")
                            event["outcome"] = (result.get("outcome") if isinstance(result, dict) else None) or {"pending": "pending_approval"}.get(str(payload.get("status")), payload.get("status"))
                        self._account(entry, event)
                    event["payload"], reason = self._clean(entry, payload)
                    if reason:
                        event["omission"] = reason
                if len(entry["events"]) > CONVERSATION_LOG_MAX_EVENTS:
                    entry["omitted_events"] += len(entry["events"]) - CONVERSATION_LOG_MAX_EVENTS
                    entry["events"] = entry["events"][:CONVERSATION_LOG_MAX_EVENTS]
                if entry["status"] == "running":
                    entry.update(status="interrupted", response_status="interrupted", coverage="partial_conversation")
                entry["execution_status"] = execution_summary(entry)
                if not self._enabled:
                    entry["capture_closed"] = True
                self._entries[entry["id"]] = entry
                updated = entry["updated_at"]
                self._changed(entry)
                entry["updated_at"] = updated
                self._bound(entry)
            except Exception:
                self.available = False
        self.prune()
        self._dirty = True

    async def async_clear(self) -> None:
        self._generation += 1
        for key in list(self._entries):
            self._remove(key)
        self._dirty = True
        await self.async_save()


def recorder(data: Any) -> ConversationLog:
    log = getattr(data, "conversations", None)
    if not isinstance(log, ConversationLog):
        log = ConversationLog()
        data.conversations = log
    log.configure(data.store.get_settings())
    return log


def record_event(data: Any, kind: str, payload: Any = None, **facts: Any) -> None:
    """Optional observability must never alter an execution outcome."""
    try:
        recorder(data).event(_current.get(), kind, payload, **facts)
    except Exception:
        _LOGGER.warning("Conversation event omitted")


def approval_event(hass: Any, approval: Any, *, resolved: bool = False) -> None:
    from .const import DOMAIN
    try:
        data = hass.data.get(DOMAIN)
        if data is not None:
            recorder(data).approval(approval, resolved=resolved)
    except Exception:
        _LOGGER.warning("Conversation approval evidence omitted")


def _arguments(run: Callable, args: tuple, kwargs: dict) -> dict:
    return dict(zip(run.__code__.co_varnames[:run.__code__.co_argcount], args)) | kwargs


def recorded_exchange(run: Callable) -> Callable:
    """Record final tool envelopes after the dispatcher's authority checks."""
    @functools.wraps(run)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        values = _arguments(run, args, kwargs)
        if values["method"] != "tools/call":
            return await run(*args, **kwargs)
        data = values["data"]
        if values.get("request_id") is None:
            values["request_id"] = str(uuid4())
            position = run.__code__.co_varnames[:run.__code__.co_argcount].index("request_id")
            if len(args) > position:
                args = (*args[:position], values["request_id"], *args[position + 1:])
            else:
                kwargs["request_id"] = values["request_id"]
        log = recorder(data)
        parent = _current.get()
        own = parent is None
        token = values["token"]
        source = values["client_ip"] if values["client_ip"] in {"agentcli", "assist", "voice", "ai_task", "home_companion"} else "mcp"
        handle = log.begin(source, metadata={"request_id": values.get("request_id"), "token_id": token.id,
                                            "token_name": token.name, "client_ip": values["client_ip"], "session_ref": _transport.get()}) if own else parent
        ctx = _current.set(handle)
        parent_tool_id = _tool.get()
        call_ctx = _tool.set(uuid4().hex)
        params = values["params"]
        record_event(data, "tool_call", {"name": params.get("name"), "arguments": params.get("arguments", {}), "request_id": values["request_id"]}, parent_tool_id=parent_tool_id)
        try:
            result = await run(*args, **kwargs)
            record_event(data, "tool_result", result[0], outcome=result[3])
            if own:
                log.finish(handle, status="completed", response_status="client_unavailable")
                await log.async_save(coalesced=True)
            return result
        except BaseException:
            record_event(data, "tool_result", None, outcome="unknown")
            if own:
                log.finish(handle, status="interrupted", response_status="client_unavailable")
            raise
        finally:
            _tool.reset(call_ctx)
            _current.reset(ctx)
    return wrapped


def recorded_turn(source: str, prompt_key: str, *, transform: Callable | None = None) -> Callable:
    """Capture owned ingress turns, reusing an outer scope for delegation."""
    def decorate(run: Callable) -> Callable:
        @functools.wraps(run)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            if _current.get() is not None:
                return await run(*args, **kwargs)
            values = _arguments(run, args, kwargs)
            data = values["data"]
            log = recorder(data)
            handle = log.begin(source, prompt=values.get(prompt_key), metadata={"conversation_id": values.get("conversation_id")})
            ctx = _current.set(handle)
            try:
                response = await run(*args, **kwargs)
                entry = log.entry(handle)
                outcomes = [e["payload"].get("outcome") for e in entry["events"]
                            if e["kind"] in {"routing", "turn_outcome"} and isinstance(e["payload"], dict)
                            and "outcome" in e["payload"]] if entry else []
                status = "failed" if outcomes and outcomes[-1] not in {"ok", "completed"} else "completed"
                log.finish(handle, response=transform(response) if transform else response, status=status)
                await log.async_save(coalesced=True)
                return response
            except asyncio.CancelledError:
                log.finish(handle, status="cancelled", response_status="cancelled")
                raise
            except Exception:
                log.finish(handle, status="failed", response_status="failed")
                raise
            finally:
                _current.reset(ctx)
        return wrapped
    return decorate


def recorded_chat(run: Callable) -> Callable:
    """Sanitize assembled visible output, never store individual streaming fragments."""
    @functools.wraps(run)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        chat_id = kwargs.pop("log_conversation_id", None)
        if _current.get() is not None:
            return await run(*args, **kwargs)
        data = kwargs["data"]
        log = recorder(data)
        messages = kwargs["messages"]
        prompt = messages[-1].get("content") if messages and messages[-1].get("role") == "user" else None
        # Provider transcripts can use a user-role tool-result block on continuation.
        # That is not a new human prompt and must not become a copied envelope.
        if isinstance(prompt, list) and not all(isinstance(block, dict) and block.get("type") in {
                "text", "input_text", "image", "image_url", "input_image"} for block in prompt):
            prompt = None
        handle = log.begin("agentcli", prompt=prompt, metadata={"token_id": kwargs["token"].id, "token_name": kwargs["token"].name})
        log.bind_chat(handle, chat_id, kwargs["token"], has_history=len(messages) > 1)
        ctx = _current.set(handle)
        record_identity(token=kwargs["token"], config=getattr(kwargs.get("provider"), "cfg", None))
        emit = kwargs["emit"]
        # This transient input to the scrubber is bounded and discarded at turn end.
        # Fragment-by-fragment redaction would leak credentials split across chunks.
        chunks: list[str] = []
        size = 0
        last_text = None
        overflow = False
        status = "completed"
        response_status = "produced"

        def flush() -> None:
            nonlocal last_text, size, overflow
            if overflow:
                last_text = None
                entry = log.entry(handle)
                if entry is not None:
                    entry["field_omissions"]["response"] = "size_limit"
                    if "size_limit" not in entry["omissions"]:
                        entry["omissions"].append("size_limit")
                    log._changed(entry, "field_omissions")
                overflow = False
            if chunks:
                text = "".join(chunks)
                chunks.clear()
                entry = log.entry(handle)
                if entry is not None:
                    last_text, _reason = log._clean(entry, text)
                    log.event(handle, "assistant_text", text)
            size = 0

        flush_context = _assistant_flush.set(flush)

        async def observe(name: str, payload: dict) -> None:
            nonlocal size, status, response_status, overflow
            if log.entry(handle) is None:
                chunks.clear()
            if log.entry(handle) is not None and name == "assistant_delta" and isinstance(payload.get("text"), str):
                size += len(payload["text"].encode("utf-8"))
                if size <= CONVERSATION_LOG_SCAN_BYTES:
                    chunks.append(payload["text"])
                else:
                    chunks.clear()
                    overflow = True
                    response_status = "size_limit"
            elif name in {"error", "notice", "continue_required"}:
                record_event(data, name, payload)
                if name == "error":
                    status = "cancelled" if payload.get("code") == "cancelled" else "failed"
                    response_status = status
                elif name == "continue_required":
                    status = "paused"
            await emit(name, payload)

        kwargs["emit"] = observe
        try:
            return await run(*args, **kwargs)
        except asyncio.CancelledError:
            status = response_status = "cancelled"
            raise
        except Exception:
            status = response_status = "failed"
            raise
        finally:
            if kwargs.get("cancel") is not None and kwargs["cancel"].is_set():
                status = response_status = "cancelled"
            flush_assistant()
            log.finish(handle, response=last_text, status=status, response_status=response_status)
            chunks.clear()
            _assistant_flush.reset(flush_context)
            _current.reset(ctx)
            await log.async_save(coalesced=True)
    return wrapped


def record_identity(*, token: Any = None, config: Any = None) -> None:
    """Copy only safe selected identities, never credentials or provider envelopes."""
    try:
        handle = _current.get()
        if handle is None:
            return
        log = handle[0]
        entry = log.entry(handle)
        if entry is None:
            return
        metadata = dict(entry["metadata"])
        values = {"token_id": getattr(token, "id", None), "token_name": getattr(token, "name", None),
                  "provider": getattr(config, "kind", None), "model": getattr(config, "model", None)}
        # Delegated work cannot replace the originating turn's selected identity.
        for key, value in values.items():
            if key not in metadata and isinstance(value, str):
                metadata[key] = value
        log.assign(handle, "metadata", metadata, content=False)
    except Exception:
        _LOGGER.warning("Conversation identity omitted")


def record_current(kind: str, payload: Any = None) -> None:
    """Record server-owned routing facts without exposing another module's context."""
    handle = _current.get()
    if handle is not None:
        handle[0].event(handle, kind, payload)


@contextmanager
def routing_span(route: str, **facts: Any):
    """Keep one bounded routing receipt, including cancellation and late results."""
    handle = _current.get()
    event = None
    payload = {"route": route, "result": "running", **facts}
    try:
        if handle is not None:
            log = handle[0]
            entry = log.entry(handle)
            if entry is not None:
                event = log.event(handle, "routing", payload)
    except Exception:
        _LOGGER.warning("Conversation routing start omitted")
    try:
        yield payload
    except asyncio.CancelledError:
        payload["result"] = "cancelled"
        raise
    except GeneratorExit:
        if payload["result"] == "running":
            payload["result"] = "interrupted"
        raise
    except BaseException:
        payload["result"] = "failed"
        raise
    else:
        if payload["result"] == "running":
            payload["result"] = "completed"
    finally:
        if handle is not None and event is not None:
            try:
                log = handle[0]
                entry = log.entry(handle)
                if entry is not None and any(item is event for item in entry["events"]):
                    clean, reason = log._clean(entry, payload)
                    event["payload"] = clean
                    if reason:
                        event["omission"] = reason
                    event["finished_at"] = utcnow().isoformat()
                    log._changed(entry, "events")
                    log._bound(entry)
                    log.prune()
            except Exception:
                _LOGGER.warning("Conversation routing evidence omitted")


def _scrub_argument_text(raw: str, *, hide_ip: bool) -> str:
    """Scrub JSON string tokens even when the surrounding arguments are invalid."""
    if len(raw.encode("utf-8")) > CONVERSATION_LOG_SCAN_BYTES:
        raise ValueError("payload budget")
    decoder = json.JSONDecoder()
    pieces: list[str] = []
    cursor = 0
    for count, match in enumerate(re.finditer(r'"(?:\\.|[^"\\])*(?:"|\Z)', raw)):
        if count >= CONVERSATION_LOG_MAX_NODES:
            raise ValueError("payload budget")
        if match.start() < cursor:
            continue
        token = match[0]
        # A broken escape cannot be decoded safely. Omit the argument payload.
        decoded = json.loads(token if token.endswith('"') and len(token) > 1 else token + '"')
        end = match.end()
        colon = end
        while colon < len(raw) and raw[colon].isspace():
            colon += 1
        if colon < len(raw) and raw[colon] == ":" and (sensitive_key(decoded, service=True)
                                                       or decoded.startswith("_") or decoded in {"blob", "base64"}):
            start = colon + 1
            while start < len(raw) and raw[start].isspace():
                start += 1
            try:
                _, end = decoder.raw_decode(raw, start)
            except ValueError:
                # Incomplete secret values can contain spaces or punctuation.
                end = len(raw)
            pieces.extend((raw[cursor:match.start()], token, raw[match.end():start], '"<redacted>"'))
        else:
            clean = sanitized(decoded, max_bytes=CONVERSATION_LOG_SCAN_BYTES, hide_ip=hide_ip)
            pieces.extend((raw[cursor:match.start()], token if clean == decoded else json.dumps(clean, ensure_ascii=False)))
        cursor = end
    pieces.append(raw[cursor:])
    return sanitized("".join(pieces), max_bytes=CONVERSATION_LOG_SCAN_BYTES, hide_ip=hide_ip)


def record_rejected_tool(call_id: Any, name: Any, arguments: Any, *, reason: str,
                         finish_reason: Any = None, stage: str = "provider_validation", error: json.JSONDecodeError | None = None) -> None:
    """Keep rejected proposals in admin history, never in provider/SSE diagnostics."""
    try:
        handle = _current.get()
        if handle is None or handle[0].entry(handle) is None:
            return
        log = handle[0]
        payload = {
            "code": "tool_call_rejected", "message": "Tool call rejected before execution. " + reason,
            "name": name, "call_id": call_id, "executed": False,
            "stage": stage, "finish_reason": finish_reason,
        }
        if error is not None:
            # Positions refer to received text; redaction can change its length.
            payload["validation"] = {"reason": error.msg, "line": error.lineno,
                                     "column": error.colno, "character": error.pos}
        if log._settings.log_entity_names:
            try:
                if isinstance(arguments, str):
                    arguments = _scrub_argument_text(arguments, hide_ip=not log._settings.log_client_ip)
                else:
                    arguments = sanitized({"service_data": arguments}, max_bytes=CONVERSATION_LOG_SCAN_BYTES,
                                          hide_ip=not log._settings.log_client_ip)["service_data"]
            except Exception:
                arguments = None
                payload["arguments_omission"] = "Arguments could not be safely sanitized within recording limits."
        payload["arguments"] = arguments
        log.event(handle, "error", payload)
    except Exception:
        _LOGGER.warning("Rejected tool evidence omitted")


@contextmanager
def transport_session(session_id: Any):
    """Retain only a one-way session reference, never its bearer-like identifier."""
    ref = hashlib.sha256(session_id.encode()).hexdigest() if isinstance(session_id, str) and len(session_id) <= 256 else None
    context = _transport.set(ref)
    try:
        yield
    finally:
        _transport.reset(context)


def flush_assistant() -> None:
    """Close visible narration at a real model/tool boundary, never per fragment."""
    flush = _assistant_flush.get()
    if flush is not None:
        try:
            flush()
        except Exception:
            _LOGGER.warning("Conversation narration omitted")
