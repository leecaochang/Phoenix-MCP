"""Bound scrubbed system-log previews and token-bound detail chunks."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .const import (
    SYSTEM_LOG_MAX_DETAIL_BYTES,
    SYSTEM_LOG_MAX_ENTRY_BYTES,
    SYSTEM_LOG_MAX_EXCEPTION_BYTES,
    SYSTEM_LOG_MAX_MESSAGE_BYTES,
    SYSTEM_LOG_MAX_MESSAGE_VARIANTS,
    SYSTEM_LOG_MAX_METADATA_BYTES,
    SYSTEM_LOG_MAX_RESPONSE_BYTES,
)


def log_result_size(body: dict) -> int:
    """Measure the serialized text ToolResult, including nested JSON escaping."""
    result = {"content": [{"type": "text", "text": json.dumps(body, default=str)}]}
    return len(json.dumps(result).encode("utf-8"))


def log_payload_budget(*, byte_limited: bool = False) -> dict:
    """Describe the response size budget, separate from how much log history is retained."""
    return {
        "max_response_bytes": SYSTEM_LOG_MAX_RESPONSE_BYTES,
        "size_basis": "serialized_tool_result_json",
        "byte_limited": byte_limited,
    }


def compact_log_text(text: str, budget: int, *, traceback: bool = False) -> tuple[str, dict]:
    """Keep both ends within a JSON string budget, favoring traceback tails."""
    original_chars = len(text)
    metadata = {"original_chars": original_chars, "omitted_chars": 0}
    if len(json.dumps(text).encode("utf-8")) <= budget:
        return text, metadata

    def preview(kept: int) -> tuple[str, int, int]:
        head = kept // (4 if traceback else 2)
        tail = kept - head
        marker = f"\n[... {original_chars - kept} characters omitted ...]\n"
        return text[:head] + marker + (text[-tail:] if tail else ""), head, tail

    low, high = 0, min(original_chars - 1, budget)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(preview(middle)[0]).encode("utf-8")) <= budget:
            low = middle
        else:
            high = middle - 1
    value, head, tail = preview(low)
    return value, {
        "original_chars": original_chars,
        "omitted_chars": original_chars - low,
        "head_chars": head,
        "tail_chars": tail,
    }


def log_detail_ref(entry: dict, token: Any, *, phoenix_only: bool, bucket_id: str) -> str:
    """Bind a reference to this token, namespace and complete scrubbed bucket.

    An HMAC keyed by the token's hash, so a reference is not usable by another
    token or after the bucket content changes.
    """
    payload = json.dumps([phoenix_only, bucket_id, entry], sort_keys=True).encode("utf-8")
    return hmac.new(token.token_hash.encode(), payload, hashlib.sha256).hexdigest()


def compact_log_entry(entry: dict, detail_ref: str | None, *, text_divisor: int = 1) -> dict:
    """Preview retained variants and metadata without losing omission accounting.

    Retries with a halved text budget (up to an eighth) until the entry fits
    SYSTEM_LOG_MAX_ENTRY_BYTES; raises ValueError if even that is too large.
    """
    result = dict(entry)
    messages = entry["messages"]
    previews = [compact_log_text(text, SYSTEM_LOG_MAX_MESSAGE_BYTES // text_divisor)
                for text in messages[:SYSTEM_LOG_MAX_MESSAGE_VARIANTS]]
    result["messages"] = [text for text, _metadata in previews]
    exception, exception_meta = compact_log_text(
        entry["exception"] or "", SYSTEM_LOG_MAX_EXCEPTION_BYTES // text_divisor, traceback=True
    )
    result["exception"] = exception if entry["exception"] is not None else None
    metadata = {}
    result["logger"], metadata["logger"] = compact_log_text(
        entry["logger"], SYSTEM_LOG_MAX_METADATA_BYTES
    )
    for key in ("source", "root_cause"):
        if entry[key] is None:
            continue
        result[key] = dict(entry[key])
        for field, value in entry[key].items():
            if isinstance(value, str):
                result[key][field], metadata[f"{key}.{field}"] = compact_log_text(
                    value, SYSTEM_LOG_MAX_METADATA_BYTES
                )
    result["detail_ref"] = detail_ref
    result["truncation"] = {
        "messages": [item for _text, item in previews],
        "omitted_message_variants": max(0, len(messages) - len(previews)),
        "exception": exception_meta,
        "metadata": {key: value for key, value in metadata.items() if value["omitted_chars"]},
    }
    if log_result_size({"entries": [result]}) > SYSTEM_LOG_MAX_ENTRY_BYTES:
        if text_divisor >= 8:
            raise ValueError("System-log bucket metadata exceeds the response budget.")
        return compact_log_entry(entry, detail_ref, text_divisor=text_divisor * 2)
    return result


def log_detail_chunk(entry: dict, reference: str, field: str, index: int, offset: int) -> dict:
    """Return a bounded contiguous slice of current scrubbed text by character offset."""
    if field == "message":
        if index >= len(entry["messages"]):
            raise ValueError("Invalid detail message index.")
        text = entry["messages"][index]
    else:
        text = entry["exception"] or ""
    if offset > len(text):
        raise ValueError("Invalid detail offset.")
    low, high = 0, min(len(text) - offset, SYSTEM_LOG_MAX_DETAIL_BYTES)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(text[offset:offset + middle]).encode("utf-8")) <= SYSTEM_LOG_MAX_DETAIL_BYTES:
            low = middle
        else:
            high = middle - 1
    end = offset + low
    return {
        "detail_ref": reference,
        "field": field,
        "message_index": index if field == "message" else None,
        "offset": offset,
        "end_offset": end,
        "total_chars": len(text),
        "has_more": end < len(text),
        "next_offset": end if end < len(text) else None,
        "text": text[offset:end],
    }
