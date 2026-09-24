"""Validate complete provider transcripts for inert context review without rewriting."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any

from .const import DECISION_CONTEXT_MAX_BYTES, DECISION_CONTEXT_MAX_MESSAGES, DECISION_CONTEXT_MAX_DEPTH
from .policy_engine import is_sensitive_key


def _object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_object)


def history_texts(value: Any, depth: int = 0, *, check_sensitive: bool = False) -> list[str]:
    """Inspect keys and decoded JSON strings too; never send a redacted transcript."""
    if depth > DECISION_CONTEXT_MAX_DEPTH:
        raise ValueError("Context nesting exceeds budget")
    if isinstance(value, str):
        texts = [value]
        if value.lstrip().startswith(("{", "[", '"')):
            try:
                decoded = _json(value)
            except json.JSONDecodeError:
                pass  # Ordinary prose may start with a quote or bracket.
            else:
                texts.extend(history_texts(decoded, depth + 1, check_sensitive=check_sensitive))
        return texts
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Invalid context key")
        if check_sensitive and any(is_sensitive_key(key) for key in value):
            raise ValueError("Sensitive context key")
        return [text for key, item in value.items() for part in (key, item)
                for text in history_texts(part, depth + 1, check_sensitive=check_sensitive)]
    if isinstance(value, list):
        return [text for item in value for text in history_texts(item, depth + 1, check_sensitive=check_sensitive)]
    if value is None or type(value) in (bool, int) or (type(value) is float and math.isfinite(value)):
        return []
    raise ValueError("Invalid context value")


def _text(value: Any, *, empty: bool = False) -> bool:
    if isinstance(value, str):
        return empty or bool(value.strip())
    return bool(isinstance(value, list) and value and all(
        isinstance(block, dict) and set(block) == {"type", "text"}
        and block["type"] == "text" and isinstance(block["text"], str)
        for block in value) and (empty or any(block["text"].strip() for block in value)))


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _assistant(message: dict) -> tuple[str, dict[str, str]] | None:
    """Return call format and IDs, keeping provider-native fields unchanged."""
    if set(message) - {"role", "content", "tool_calls", "reasoning_content"} or "content" not in message:
        return None
    if "reasoning_content" in message and not isinstance(message["reasoning_content"], str):
        return None
    content = message["content"]
    calls: dict[str, str] = {}
    if "tool_calls" in message:
        if not isinstance(message["tool_calls"], list) or not message["tool_calls"] or not _text(content, empty=True):
            return None
        for call in message["tool_calls"]:
            if (not isinstance(call, dict) or set(call) != {"id", "type", "function"}
                    or call["type"] != "function" or not _identifier(call["id"])):
                return None
            function = call["function"]
            if (not isinstance(function, dict) or set(function) != {"name", "arguments"}
                    or not _identifier(function["name"]) or not isinstance(function["arguments"], str)
                    or not isinstance(_json(function["arguments"]), dict) or call["id"] in calls):
                return None
            calls[call["id"]] = function["name"]
        return "openai", calls
    if _text(content):
        return "text", calls
    if not isinstance(content, list) or not content or "reasoning_content" in message:
        return None
    for block in content:
        if not isinstance(block, dict):
            return None
        if block.get("type") == "text" and _text([block], empty=True):
            continue
        if (set(block) != {"type", "id", "name", "input"} or block["type"] != "tool_use"
                or not _identifier(block["id"]) or not _identifier(block["name"])
                or not isinstance(block["input"], dict) or block["id"] in calls):
            return None
        calls[block["id"]] = block["name"]
    return ("anthropic", calls) if calls else None


def _transcript(messages: list[dict]) -> bool:
    expected = "user"
    pending: dict[str, str] = {}
    seen: set[str] = set()
    dialect: str | None = None
    for message in messages:
        if not isinstance(message, dict):
            return False
        if pending:
            if dialect == "openai":
                if (set(message) - {"role", "content", "tool_call_id", "name"}
                        or message.get("role") != "tool" or not _text(message.get("content"), empty=True)):
                    return False
                call_id = message.get("tool_call_id")
                if (not isinstance(call_id, str) or call_id not in pending
                        or ("name" in message and message["name"] != pending[call_id])):
                    return False
                del pending[call_id]
            else:
                content = message.get("content")
                if (set(message) != {"role", "content"} or message["role"] != "user"
                        or not isinstance(content, list) or not content):
                    return False
                for block in content:
                    if (not isinstance(block, dict) or set(block) - {"type", "tool_use_id", "content", "is_error"}
                            or block.get("type") != "tool_result" or not _text(block.get("content"), empty=True)
                            or ("is_error" in block and type(block["is_error"]) is not bool)):
                        return False
                    call_id = block.get("tool_use_id")
                    if not isinstance(call_id, str) or call_id not in pending:
                        return False
                    del pending[call_id]
                if pending:
                    return False
            expected = "assistant"
        elif expected == "user":
            if set(message) != {"role", "content"} or message["role"] != "user" or not _text(message["content"]):
                return False
            expected = "assistant"
        else:
            if message.get("role") != "assistant":
                return False
            parsed = _assistant(message)
            if parsed is None:
                return False
            kind, pending = parsed
            if pending:
                if (dialect is not None and dialect != kind) or seen.intersection(pending):
                    return False
                dialect = kind
                seen.update(pending)
            else:
                expected = "user"
    return expected == "user" and not pending


def structured_context_history(messages: Any) -> list[dict] | None:
    """Accept bounded complete text/tool exchanges, excluding images and opaque blocks."""
    if not isinstance(messages, list) or not messages or len(messages) > DECISION_CONTEXT_MAX_MESSAGES:
        return None
    try:
        encoded = json.dumps(messages, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > DECISION_CONTEXT_MAX_BYTES:
            return None
        history_texts(messages)
        if not _transcript(messages):
            return None
        return deepcopy(messages)
    except (TypeError, ValueError, RecursionError):
        return None
