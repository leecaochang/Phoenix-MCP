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
import json
import logging
import math
import re
from typing import Any
from uuid import uuid4

from homeassistant.util.dt import utcnow, parse_datetime

from .const import (
    CONVERSATION_LOG_DEFAULTS, CONVERSATION_LOG_LIMITS, CONVERSATION_LOG_RESERVE_BYTES,
    CONVERSATION_LOG_MAX_NODES, CONVERSATION_LOG_MAX_DEPTH,
)
from .helpers import is_sensitive_key, redact_secrets_in_text

_LOGGER = logging.getLogger(__name__)
_REDACTED = "<redacted>"
_SECRET = re.compile(
    r"(?i)(\b(?:bearer|basic)\s+)\S+|"
    r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b|"
    r"((?:[\"']?)(?:api[_ -]?key|password|passwd|secret|access[_ -]?token|refresh[_ -]?token|authorization|private[_ -]?key|pin)(?:[\"']?)\s*(?::|=|\bis\b)\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S)
_DATA_URI = re.compile(r"data:[^\s\"')<>]+", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|(?<!\w)(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,39}(?!\w)", re.I)
_current: ContextVar[tuple[Any, str, int] | None] = ContextVar("phoenix_conversation", default=None)
_transport: ContextVar[str | None] = ContextVar("phoenix_conversation_session", default=None)
_tool: ContextVar[str | None] = ContextVar("phoenix_conversation_tool", default=None)


def sanitized(value: Any, *, max_bytes: int, hide_ip: bool = False) -> Any:
    """Copy supported data through bounded redaction; never stringify unknown objects."""
    remaining = max_bytes
    nodes = 0

    def text(raw: str) -> str:
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
            if match and is_sensitive_key(match.group(1)):
                block_indent = indent
        clean = redact_secrets_in_text("\n".join(lines)) or ""
        clean = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + _REDACTED, clean)
        clean = _DATA_URI.sub("<attachment omitted>", clean)
        return _IP.sub(_REDACTED, clean) if hide_ip else clean

    def visit(obj: Any, depth: int = 0) -> Any:
        nonlocal nodes, remaining
        nodes += 1
        remaining -= 8
        if nodes > CONVERSATION_LOG_MAX_NODES or depth > CONVERSATION_LOG_MAX_DEPTH or remaining < 0:
            raise ValueError("payload budget")
        if isinstance(obj, str):
            return text(obj)
        if obj is None or type(obj) in (bool, int):
            return obj
        if isinstance(obj, float):
            if not math.isfinite(obj):
                raise ValueError("nonfinite")
            return obj
        if isinstance(obj, (bytes, bytearray)):
            return {"attachment": True, "bytes": len(obj), "omitted": "media"}
        if isinstance(obj, (list, tuple, set)):
            return [visit(item, depth + 1) for item in obj]
        if isinstance(obj, dict):
            kind = obj.get("type")
            if isinstance(kind, str) and kind in {"image", "image_url", "input_image", "audio", "input_audio"}:
                return {"attachment": True, "type": kind, "mime_type": text(str(obj.get("mimeType", obj.get("mime_type", ""))))[:128], "omitted": "media"}
            result = {}
            for key, item in obj.items():
                if not isinstance(key, str):
                    raise ValueError("unsupported key")
                safe_key = text(key)
                if key.startswith("_") or is_sensitive_key(key):
                    result[safe_key] = _REDACTED
                elif key in {"blob", "base64"}:
                    result[safe_key] = "<attachment omitted>"
                else:
                    result[safe_key] = visit(item, depth + 1)
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
    results: dict[str, str] = {}
    approvals: dict[str, str] = {}
    decisions: dict[str, str] = {}
    uncertain: set[str] = set()
    for event in entry["events"]:
        if event["kind"] == "tool_call":
            results[event["tool_id"] or "unknown"] = "running" if entry["status"] == "running" else "unknown"
        if event["kind"] == "tool_result":
            results[event["tool_id"] or "unknown"] = event.get("outcome", "unknown")
        if event["kind"] == "notice" and event.get("outcome") == "unknown":
            uncertain.add(event["tool_id"] or "unknown")
        payload = event.get("payload")
        if isinstance(payload, dict) and event["kind"] == "approval_requested":
            approvals[payload.get("approval_id", "")] = event["tool_id"] or "unknown"
        if isinstance(payload, dict) and event["kind"] == "approval_resolved":
            result = payload.get("result") or {}
            resolved = result.get("outcome", payload.get("status")) if isinstance(result, dict) else None
            decisions[payload.get("approval_id", "")] = resolved if isinstance(resolved, str) else "unknown"
    for approval_id, outcome in decisions.items():
        if approval_id in approvals:
            results[approvals[approval_id]] = outcome
    for tool_id in uncertain:
        results[tool_id] = "unknown"
    outcomes = set(results.values())
    if "unknown" in outcomes:
        return "unknown"
    if "pending_approval" in outcomes:
        return "pending_approval"
    if len(outcomes) > 1:
        return "partial"
    return next(iter(outcomes), "running" if entry["tool_count"] else "no_tools")


def setting_limit(key: str, value: Any) -> int:
    """Validate persisted limits without silently enabling unlimited capture."""
    lo, hi = CONVERSATION_LOG_LIMITS[key]
    return value if type(value) is int and lo <= value <= hi else CONVERSATION_LOG_DEFAULTS[key]


class ConversationLog:
    """Own sanitized records, retention and serialized HA Store persistence."""

    def __init__(self, store: Any = None) -> None:
        self._store = store
        self._entries: dict[str, dict] = {}
        self._sizes: dict[str, int] = {}
        self._settings: Any = None
        self._generation = 0
        self._enabled = False
        self._dirty = False
        self._lock = asyncio.Lock()
        self.available = True

    @best_effort
    def configure(self, settings: Any) -> None:
        enabled = getattr(settings, "conversation_log_enabled", False) is True and not settings.disable_all_logging
        if self._enabled and not enabled:
            self._generation += 1
            for entry in self._entries.values():
                entry["capture_closed"] = True
                if entry["status"] == "running":
                    entry["status"] = "interrupted"
                    entry["response_status"] = "recording_stopped"
                    entry["coverage"] = "partial_conversation"
                    entry["execution_status"] = execution_summary(entry)
                self._changed(entry)
            self._dirty = True
        self._enabled = enabled
        self._settings = settings
        self.prune()

    @property
    def budget(self) -> int:
        value = getattr(self._settings, "conversation_log_max_mb", 25)
        return setting_limit("conversation_log_max_mb", value) * 1_000_000

    def _size(self) -> int:
        return 16 + sum(self._sizes.get(key, 0) + 2 for key in self._entries)

    @best_effort
    def prune(self) -> None:
        if self._settings is None:
            return
        cutoff = utcnow() - timedelta(days=setting_limit("conversation_log_max_days", getattr(self._settings, "conversation_log_max_days", None)))
        for key, entry in list(self._entries.items()):
            when = parse_datetime(entry["started_at"])
            if when is None or when.tzinfo is None or when < cutoff:
                del self._entries[key]
                self._sizes.pop(key, None)
                self._dirty = True
        while self._entries and (len(self._entries) > setting_limit("conversation_log_max_entries", getattr(self._settings, "conversation_log_max_entries", None)) or self._size() > self.budget):
            key = next(iter(self._entries))
            del self._entries[key]
            self._sizes.pop(key, None)
            self._dirty = True

    @best_effort
    def begin(self, source: str, *, prompt: Any = None, metadata: Any = None) -> tuple[Any, str, int] | None:
        if not self._enabled:
            return (self, "", -1)
        key = uuid4().hex
        now = utcnow().isoformat()
        self._entries[key] = {
            "id": key, "source": source, "started_at": now, "updated_at": now,
            "coverage": "tool_exchange" if prompt is None and source in {"mcp", "assist", "home_companion"} else "partial_conversation",
            "status": "running", "execution_status": "no_tools", "response_status": "unavailable",
            "prompt": None, "response": None, "events": [], "metadata": {},
            "tool_count": 0, "change_count": 0, "omissions": [], "approval_ids": [],
        }
        handle = (self, key, self._generation)
        self._dirty = True
        if prompt is not None:
            self.assign(handle, "prompt", prompt)
        self.assign(handle, "metadata", metadata or {}, content=False)
        self.prune()
        return handle

    def entry(self, handle: Any) -> dict | None:
        if not handle or handle[0] is not self or handle[2] != self._generation or not self._enabled:
            return None
        return self._entries.get(handle[1])

    def _clean(self, entry: dict, value: Any, content: bool = True) -> Any:
        if content and not self._settings.log_entity_names:
            if "privacy" not in entry["omissions"]:
                entry["omissions"].append("privacy")
            return None
        if "size_limit" in entry["omissions"]:
            return None
        try:
            return sanitized(value, max_bytes=self.budget - CONVERSATION_LOG_RESERVE_BYTES,
                             hide_ip=not self._settings.log_client_ip)
        except ValueError as error:
            reason = "size_limit" if str(error) == "payload budget" else "sanitization"
            if reason not in entry["omissions"]:
                entry["omissions"].append(reason)
            return None
        except Exception:
            if "sanitization" not in entry["omissions"]:
                entry["omissions"].append("sanitization")
            return None

    def _changed(self, entry: dict) -> None:
        if self._entries.get(entry["id"]) is not entry:
            return
        entry["updated_at"] = utcnow().isoformat()
        self._sizes[entry["id"]] = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        self._dirty = True

    def _fits(self, entry: dict) -> bool:
        if len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) > self.budget - CONVERSATION_LOG_RESERVE_BYTES:
            if "size_limit" not in entry["omissions"]:
                entry["omissions"].append("size_limit")
            return False
        return True

    @best_effort
    def assign(self, handle: Any, field: str, value: Any, *, content: bool = True) -> None:
        entry = self.entry(handle)
        if entry is None:
            return
        old = entry.get(field)
        if field == "metadata" and isinstance(value, dict):
            entry[field] = {key: self._clean(entry, item, False) for key, item in value.items()
                            if key in {"request_id", "token_id", "token_name", "client_ip", "conversation_id", "session_ref"}
                            and (key != "client_ip" or self._settings.log_client_ip)}
        else:
            entry[field] = self._clean(entry, value, content)
        if not self._fits(entry):
            entry[field] = old
        self._changed(entry)
        self.prune()

    @best_effort
    def event(self, handle: Any, kind: str, payload: Any = None, **facts: Any) -> None:
        entry = self.entry(handle)
        if entry is None:
            return
        event = {"kind": kind, "timestamp": utcnow().isoformat(), "tool_id": _tool.get(),
                 "payload": self._clean(entry, payload), **facts}
        if kind == "tool_call":
            entry["tool_count"] += 1
        if kind == "change":
            entry["change_count"] += 1
        if kind == "tool_result":
            entry["execution_status"] = facts.get("outcome", "unknown")
        if "size_limit" not in entry["omissions"]:
            entry["events"].append(event)
            if not self._fits(entry):
                entry["events"].pop()
        entry["execution_status"] = execution_summary(entry) if "size_limit" not in entry["omissions"] else entry["execution_status"]
        self._changed(entry)
        self.prune()

    @best_effort
    def finish(self, handle: Any, *, response: Any = None, status: str = "completed", response_status: str | None = None) -> None:
        entry = self.entry(handle)
        if entry is None:
            return
        if response is not None:
            self.assign(handle, "response", response)
        entry["status"] = status
        entry["response_status"] = response_status or ("produced" if response is not None else "unavailable")
        if "size_limit" not in entry["omissions"]:
            entry["execution_status"] = execution_summary(entry)
        if (status == "completed" and entry["response_status"] == "produced"
                and entry["prompt"] is not None and entry["response"] is not None and not entry["omissions"]):
            entry["coverage"] = "full_turn"
        self._changed(entry)
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
        if approval.id not in entry["approval_ids"]:
            entry["approval_ids"].append(approval.id)
        self.event(handle, "approval_resolved" if resolved else "approval_requested", {
            "approval_id": approval.id, "tool_name": approval.tool_name, "status": approval.status,
            "diff": approval.diff, "result": approval.result, "reason": approval.rejected_reason,
        })
        entry["execution_status"] = execution_summary(entry)

    @contextmanager
    def approval_scope(self, approval_id: str):
        entry = next((e for e in self._entries.values() if approval_id in e["approval_ids"] and not e.get("capture_closed")), None)
        ctx = _current.set((self, entry["id"], self._generation) if entry else (self, "", -1))
        try:
            yield
        finally:
            _current.reset(ctx)

    def query(self, *, limit: int = 25, offset: int = 0, search: str = "", source: str = "", outcome: str = "", since: str = "") -> dict:
        self.prune()
        entries = [e for e in reversed(list(self._entries.values())) if
                   (not source or e["source"] == source) and
                   (not outcome or outcome in {e["status"], e["execution_status"], e["response_status"]}) and
                   (not since or e["started_at"] >= since) and
                   (not search or search.casefold() in json.dumps(e, ensure_ascii=False).casefold())]
        summaries = []
        for e in entries[offset:offset + limit]:
            summary = {k: deepcopy(v) for k, v in e.items() if k not in {"events", "metadata", "prompt", "response", "approval_ids"}}
            summary["excerpt"] = e["prompt"][:240] if isinstance(e["prompt"], str) else ""
            summaries.append(summary)
        return {"entries": summaries, "total": len(entries), "enabled": self._enabled,
                "available": self.available, "bytes": self._size()}

    def detail(self, entry_id: str) -> dict | None:
        self.prune()
        return deepcopy(self._entries.get(entry_id))

    async def async_save(self) -> None:
        self.prune()
        if self._store is None or not self._dirty:
            return
        async with self._lock:
            try:
                snapshot = deepcopy(list(self._entries.values()))
                self._dirty = False
                await self._store.async_save({"entries": snapshot})
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
                entry = sanitized({**raw, "metadata": {}}, max_bytes=self.budget)
                text_fields = ("id", "started_at", "updated_at", "status", "source", "coverage", "response_status", "execution_status")
                if (not all(isinstance(entry.get(k), str) for k in text_fields)
                        or not all(isinstance(entry.get(k), list) for k in ("events", "approval_ids", "omissions"))
                        or not all(type(entry.get(k)) is int for k in ("tool_count", "change_count"))
                        or not all(k in entry for k in ("prompt", "response"))):
                    continue
                metadata = raw.get("metadata")
                if isinstance(metadata, dict):
                    entry["metadata"] = {key: self._clean(entry, value, False) for key, value in metadata.items()
                                         if key in {"request_id", "token_id", "token_name", "client_ip", "conversation_id", "session_ref"}
                                         and (key != "client_ip" or self._settings.log_client_ip)}
                if not all(isinstance(event, dict) and isinstance(event.get("kind"), str)
                           and isinstance(event.get("timestamp"), str) and "tool_id" in event for event in entry["events"]):
                    continue
                if entry["status"] == "running":
                    entry.update(status="interrupted", response_status="interrupted", coverage="partial_conversation")
                    entry["execution_status"] = execution_summary(entry)
                if not self._enabled:
                    entry["capture_closed"] = True
                self._entries[entry["id"]] = entry
                self._sizes[entry["id"]] = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
                self.prune()
            except Exception:
                self.available = False
        self._dirty = True

    async def async_clear(self) -> None:
        self._generation += 1
        self._entries.clear()
        self._sizes.clear()
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
            values["request_id"] = uuid4().hex
            if len(args) > 8:
                args = (*args[:8], values["request_id"], *args[9:])
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
        call_ctx = _tool.set(uuid4().hex)
        params = values["params"]
        record_event(data, "tool_call", {"name": params.get("name"), "arguments": params.get("arguments", {}), "request_id": values["request_id"]})
        try:
            result = await run(*args, **kwargs)
            record_event(data, "tool_result", result[0], outcome=result[3])
            if own:
                log.finish(handle, status="completed", response_status="client_unavailable")
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
        handle = log.begin("agentcli", prompt=prompt, metadata={"token_id": kwargs["token"].id})
        ctx = _current.set(handle)
        emit = kwargs["emit"]
        # This transient input to the scrubber is bounded and discarded at turn end.
        # Fragment-by-fragment redaction would leak credentials split across chunks.
        chunks: list[str] = []
        size = 0
        status = "completed"
        response_status = "produced"

        async def observe(name: str, payload: dict) -> None:
            nonlocal size, status, response_status
            if log.entry(handle) is None:
                chunks.clear()
            if log.entry(handle) is not None and name == "assistant_delta" and isinstance(payload.get("text"), str):
                size += len(payload["text"].encode("utf-8"))
                if size <= log.budget - CONVERSATION_LOG_RESERVE_BYTES:
                    chunks.append(payload["text"])
                else:
                    chunks.clear()
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
            log.finish(handle, response="".join(chunks) if chunks else None, status=status, response_status=response_status)
            chunks.clear()
            _current.reset(ctx)
    return wrapped


def record_current(kind: str, payload: Any = None) -> None:
    """Record server-owned routing facts without exposing another module's context."""
    handle = _current.get()
    if handle is not None:
        handle[0].event(handle, kind, payload)


@contextmanager
def transport_session(session_id: Any):
    """Retain only a one-way session reference, never its bearer-like identifier."""
    ref = hashlib.sha256(session_id.encode()).hexdigest() if isinstance(session_id, str) and len(session_id) <= 256 else None
    context = _transport.set(ref)
    try:
        yield
    finally:
        _transport.reset(context)
