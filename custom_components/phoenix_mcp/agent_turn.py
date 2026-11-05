"""The headless agent turn: stream a model, run its tools, repeat."""

from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import aclosing
from contextvars import ContextVar
import dataclasses
from datetime import datetime, tzinfo
import json
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable, Callable

from aiohttp import ClientSession, web
from homeassistant.core import callback, HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import utcnow

from .access_key_store import AccessKeyRecord
from .view_base import _err
from .agent_recovery import recovery_turn, RecoveryRequired, tool_is_read_only
from .agent_dispatch import dispatch_agent_tool
from .approvals import (
    async_update_approval_status, dismiss_approval_notification, fire_approval_resolved_event,
    get_approval, REASON_AGENT_CHAT_ENDED, REASON_AGENT_CHAT_WAIT_TIMED_OUT, STATUS_APPROVED,
    STATUS_CANCELLED, STATUS_FAILED, STATUS_PENDING, STATUS_REJECTED,
)
from .audit import generate_request_id
from .const import (
    DOMAIN, HOME_COMPANION_FORMAT_ERROR_CODES, HOME_COMPANION_MAX_FORMAT_CORRECTIONS,
    MAX_TOOL_NAME_LENGTH, MESA_APPROVED_EXECUTOR,
)
from .conversation_log import record_rejected_tool
from .data import PhoenixData
from .helpers import voice_text
from .home_companion import (
    internal_turn, preflight_control_calls, incomplete_proposal, invalid_read_report,
    append_fitting_results, check_turn_context, ambiguous_control, count_source, is_companion,
)
from .llm.accounts import PROVIDER_DEFINITIONS
from .llm.base import (
    _CONNECT_RETRY_BACKOFF_SECONDS, _CONNECT_RETRY_MAX_ATTEMPTS, _CONNECT_RETRY_WINDOW_SECONDS,
    _invalid_tools_error, _local_tool_results, _merged_results, _tool_limit_problem,
    _tool_loader, _usage_int, EV_DONE, EV_ERROR, EV_INVALID_TOOLS, EV_RETRYING, EV_TEXT,
    EV_THINKING, EV_TOOL, EV_USAGE,
)
from .llm.provider_claude import ClaudeProvider
from .llm.provider_openai import OpenAICompatProvider
from .provider_constants import (
    AGENTCLI_APPROVAL_WAIT_SECONDS, AGENTCLI_ATTACHMENT_MAX_BYTES,
    AGENTCLI_ATTACHMENT_MAX_COUNT, AGENTCLI_ATTACHMENT_MAX_EDGE_PX,
    AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES, AGENTCLI_MAX_TOOL_CALL_CORRECTIONS,
    AGENTCLI_MAX_TURN_OUTPUT_BYTES, AGENTCLI_PROGRESS_INTERVAL_SECONDS,
    AGENTCLI_RETRY_FIRST_REPLY_SECONDS, AGENTCLI_STATUS_INTERVAL_SECONDS,
    AGENTCLI_TOOL_RESULT_MAX_CHARS,
)
from .routing.routing_statistics import provider_events
from .routing.tool_routing import create_review_state, async_review_tools, withheld_results
from .tool_catalog import build_agent_tool_list
from .tool_common import (
    _operator_accepted_result, approval_outcome, execution_failure_text, _ProgressBus,
    _progress_ctx,
)
from .tool_defs import _NATIVE_TOOL_PUBLIC_TO_INTERNAL
from .tool_dispatch import _dispatch_mcp


def _clip_display(text: str) -> str:
    """Cap tool-result text for the verbose panel view; the model still gets the
    full result via the message history."""
    if len(text) <= AGENTCLI_TOOL_RESULT_MAX_CHARS:
        return text
    return text[:AGENTCLI_TOOL_RESULT_MAX_CHARS] + f"\n… (truncated, {len(text)} chars total)"


def _extract_image_blocks(content: Any) -> list[dict[str, str]]:
    """Validate and normalize MCP image blocks for internal consumers."""
    images: list[dict[str, str]] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        data = item.get("data")
        mime_type = str(item.get("mimeType") or "").lower().split(";", 1)[0].strip()
        if not isinstance(data, str) or mime_type not in {"image/jpeg", "image/png", "image/gif"}:
            continue
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            continue
        if not decoded or len(decoded) > 4 * 1024 * 1024:
            continue
        images.append({"data": data, "mime_type": mime_type})
    return images


# JPEG start-of-frame markers carry the dimensions; C4, C8 and CC share the
# range but are table and extension markers.
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _image_kind_and_size(data: bytes) -> tuple[str, int, int] | None:
    """The MIME type and pixel size an image's own header declares, or None.

    Read from the bytes rather than trusted from the request, so a renamed or
    truncated file is refused before it reaches a provider.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return "image/gif", int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if not data.startswith(b"\xff\xd8\xff"):
        return None
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:
            index += 2
            continue
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            if index + 9 > len(data):
                return None
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return "image/jpeg", width, height
        index += 2 + length
    return None


_MIB = 1024 * 1024


def _attachments_unreadable(rid: str) -> web.Response:
    return _err("invalid_request", "The attached images could not be read.", 400, rid, key="attachmentsInvalid")


def _attachment_too_large(rid: str) -> web.Response:
    return _err("invalid_request", f"An attached image is larger than {AGENTCLI_ATTACHMENT_MAX_BYTES // _MIB} MB.",
                400, rid, key="attachmentTooLarge", params={"max": AGENTCLI_ATTACHMENT_MAX_BYTES // _MIB})


# Base64 characters every image in one message may use, with room for each
# image part's data-URI prefix.
_ATTACHMENT_BUDGET_CHARS = -(-AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES * 4 // 3) + 1024


_UNSEEN_IMAGE_NOTE = "[An image was attached here, but this model cannot see images.]"


def _without_history_images(messages: list) -> None:
    """Replace every image part in a request's history with a short text note.

    For a model known not to see images: a refused image would otherwise be
    resent with the history and refused again on every later message. Covers
    attachments in user messages and camera images inside tool results. Edits
    the request's own parsed body in place, iteratively, so a deeply nested
    history cannot exhaust the stack.
    """
    stack: list[Any] = [messages]
    while stack:
        item = stack.pop()
        slots = enumerate(item) if isinstance(item, list) else item.items() if isinstance(item, dict) else ()
        for slot, child in list(slots):
            if isinstance(child, dict) and child.get("type") in ("image", "image_url"):
                item[slot] = {"type": "text", "text": _UNSEEN_IMAGE_NOTE}
            elif isinstance(child, (list, dict)):
                stack.append(child)


def _image_payload_chars(value: Any) -> int:
    """Base64 characters of the image parts in a chat history.

    The browser resends the newest attached images (and camera results) with
    the history, so those ride in the attachment budget instead of the ordinary
    body cap. Iterative, so a deeply nested history cannot exhaust the stack.
    """
    total = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            source = item.get("source")
            url = item.get("image_url")
            if item.get("type") == "image" and isinstance(source, dict) and isinstance(source.get("data"), str):
                total += len(source["data"])
            elif item.get("type") == "image_url" and isinstance(url, (dict, str)):
                url = url.get("url") if isinstance(url, dict) else url
                total += len(url) if isinstance(url, str) else 0
            else:
                stack.extend(item.values())
    return total


def _chat_attachments(value: Any, rid: str) -> list[dict[str, str]] | web.Response:
    """Validate the images an administrator attached to one chat message.

    Returns the normalized images, or the refusal to send. Every image must
    decode, match the type its header declares and fit the count, size and
    dimension bounds; one bad image refuses the message rather than sending the
    rest.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return _attachments_unreadable(rid)
    if len(value) > AGENTCLI_ATTACHMENT_MAX_COUNT:
        return _err("invalid_request", f"Attach at most {AGENTCLI_ATTACHMENT_MAX_COUNT} images to one message.",
                    400, rid, key="attachmentsTooMany", params={"max": AGENTCLI_ATTACHMENT_MAX_COUNT})
    images: list[dict[str, str]] = []
    total = 0
    for item in value:
        data = item.get("data") if isinstance(item, dict) else None
        declared = item.get("mime_type") if isinstance(item, dict) else None
        if not isinstance(data, str) or not isinstance(declared, str):
            return _attachments_unreadable(rid)
        if len(data) > -(-AGENTCLI_ATTACHMENT_MAX_BYTES * 4 // 3):
            return _attachment_too_large(rid)
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            return _attachments_unreadable(rid)
        if len(decoded) > AGENTCLI_ATTACHMENT_MAX_BYTES:
            return _attachment_too_large(rid)
        header = _image_kind_and_size(decoded)
        if header is None or header[0] != declared.lower().strip():
            return _err("invalid_request", "Attach a JPEG, PNG or GIF image.", 400, rid, key="attachmentUnsupported")
        kind, width, height = header
        if not (0 < width <= AGENTCLI_ATTACHMENT_MAX_EDGE_PX and 0 < height <= AGENTCLI_ATTACHMENT_MAX_EDGE_PX):
            return _err("invalid_request",
                        f"An attached image is larger than {AGENTCLI_ATTACHMENT_MAX_EDGE_PX} pixels on a side.",
                        400, rid, key="attachmentDimensions", params={"max": AGENTCLI_ATTACHMENT_MAX_EDGE_PX})
        total += len(decoded)
        if total > AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES:
            return _err("invalid_request",
                        f"The attached images are larger than {AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES // _MIB} MB together.",
                        400, rid, key="attachmentsTooLarge", params={"max": AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES // _MIB})
        images.append({"data": data, "mime_type": kind})
    return images


def _flatten_text(content: list[dict]) -> str:
    return "\n".join(
        c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
    )


_AGENTCLI_LOCAL_TIME_FIELDS: dict[str, frozenset[str]] = {
    "get_state": frozenset({"last_changed", "last_updated", "last_reported"}),
    "get_history": frozenset({
        "start", "end", "when", "last_changed", "last_updated", "last_reported",
        "first_transition", "last_transition", "first_entered", "last_entered", "recorded_from",
    }),
    "get_statistics": frozenset({"start", "end", "last_reset"}),
    "get_logbook": frozenset({"start_time", "end_time", "when"}),
    "recent_activity": frozenset({"when"}),
    "get_automation_traces": frozenset({"start", "finish"}),
    "list_automations": frozenset({"last_triggered"}),
    "get_todo_items": frozenset({"due"}),
    "set_todo_item": frozenset({"due"}),
    "get_energy_config": frozenset({"start", "end"}),
    "get_logs": frozenset({
        "since", "until", "first_occurred", "latest_occurred", "earliest_first_occurred", "read_at",
    }),
    "get_phoenix_diagnostics": frozenset({
        "since", "until", "first_occurred", "latest_occurred", "earliest_first_occurred", "read_at",
    }),
}

# Result times a read asks the model to send back as a query bound. Agent Chat
# states them in Home Assistant's zone (the same instant), so a value echoed
# unchanged meets the wall-time rule for query arguments.
_AGENTCLI_LOCAL_RESUME_FIELDS: dict[str, frozenset[str]] = {
    "get_logbook": frozenset({"older_end_time"}),
}


def _agentcli_local_time(value: Any, local_tz: tzinfo) -> str | None:
    """Return a local ISO companion for one recognized wire timestamp."""
    parsed: datetime | None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=dt_util.UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
    else:
        return None
    return parsed.astimezone(local_tz).isoformat()


def _agentcli_add_local_time_fields(
    tool_name: str, text: str, local_tz: tzinfo,
) -> str:
    """Add local companions without changing canonical tool-result timestamps.

    This is an Agent Chat-only presentation layer. Cursor values deliberately get
    no companion because they are opaque protocol inputs for a subsequent call;
    resume times are restated in the local zone instead.
    """
    fields = _AGENTCLI_LOCAL_TIME_FIELDS.get(tool_name)
    resume = _AGENTCLI_LOCAL_RESUME_FIELDS.get(tool_name, frozenset())
    if fields is None and not resume:
        return text
    fields = fields or frozenset()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text

    def add_companions(value: Any) -> Any:
        if isinstance(value, list):
            return [add_companions(item) for item in value]
        if not isinstance(value, dict):
            return value
        augmented: dict[str, Any] = {}
        for key, item in value.items():
            if key in resume:
                augmented[key] = _agentcli_local_time(item, local_tz) or item
                continue
            augmented[key] = add_companions(item)
            companion = _agentcli_local_time(item, local_tz) if key in fields else None
            companion_key = f"{key}_local"
            if companion is not None and companion_key not in value:
                augmented[companion_key] = companion
        return augmented

    return json.dumps(add_companions(payload), separators=(",", ":"), ensure_ascii=False)


_AGENTCLI_RESOURCE_INVENTORY_TOOLS = frozenset({
    "list_automations", "list_scripts", "list_scenes", "list_helpers", "list_devices",
})


def _agentcli_add_resource_match_guidance(
    tool_name: str, args: dict[str, Any], text: str,
) -> str:
    """Repeat Agent Chat's premise guard beside discovery evidence for weaker models.

    This provider-only suffix is not emitted in the visible tool summary and never changes the
    external MCP result. Inventory lists always carry the conditional reminder. A filtered entity
    search carries it only on a structurally proven zero match.
    """
    guidance: str | None = None
    if tool_name in _AGENTCLI_RESOURCE_INVENTORY_TOOLS:
        guidance = (
            "If you called this inventory to identify a resource named by the operator and no "
            "plausible match appears, stop now. State the mismatch and ask one focused clarifying "
            "question. Do not substitute a related entry or inspect logs, history, or traces for "
            "a guessed target."
        )
    elif tool_name == "search_entities" and str(args.get("query") or "").strip():
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("count") == 0 \
                and payload.get("entities") == []:
            guidance = (
                "This filtered discovery found no matching entity. If the current request depends "
                "on that named resource, stop now, state the mismatch, and ask one focused "
                "clarifying question. Do not substitute a related entity or search logs, history, "
                "or traces for a guessed target."
            )
    if guidance is None:
        return text
    return f"{text}\n\nPhoenix Agent Chat factual guard: {guidance}"


# Every absolute time argument a read takes, so the wall-time rule the prompt
# states holds for each tool it names. Every tool reads a time without an offset
# as Home Assistant local time; calendar reads also keep accepting the offsets
# events carry, so they are not policed here.
_AGENTCLI_LOCAL_QUERY_FIELDS: dict[str, frozenset[str]] = {
    "get_history": frozenset({"start_time", "end_time"}),
    "get_statistics": frozenset({"start_time", "end_time"}),
    "get_logbook": frozenset({"start_time", "end_time"}),
    "compare_states": frozenset({"t1", "t2"}),
    "get_logs": frozenset({"since", "until"}),
    "get_phoenix_diagnostics": frozenset({"since", "until"}),
    "get_energy_config": frozenset({"start_time", "end_time"}),
}


def _agentcli_time_arg_error(
    tool_name: str, args: dict[str, Any], local_tz: tzinfo,
) -> str | None:
    """Refuse a query time the model shifted into another zone itself.

    A wall time without an offset passes unchanged, since the tool reads it as
    HA local time. A value already carrying HA's own offset for that instant,
    such as a _local result value sent back, is exact and passes too; any other
    offset is refused as a shift the model made itself.
    """
    for field in _AGENTCLI_LOCAL_QUERY_FIELDS.get(tool_name, ()):
        value = args.get(field)
        parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
            continue
        if parsed.astimezone(local_tz).utcoffset() != parsed.utcoffset():
            return (
                f"Agent Chat {field} must be a Home Assistant local wall time without "
                "a timezone suffix, a relative time, or a _local value copied unchanged "
                "from a result. Pass the operator's intended clock time directly; "
                "Phoenix applies the configured time zone."
            )
    return None


def dispatch_tool_result(response: dict | None) -> dict:
    """Keep dispatcher refusals visible to internal callers as tool failures."""
    if response and "error" in response:
        return {"isError": True, "content": [{
            "type": "text", "text": "The tool request was refused by Phoenix MCP.",
        }]}
    return (response or {}).get("result", {}) or {}


def _parse_pending(
    result: dict,
    *,
    outcome: str,
    data: PhoenixData,
    access_key_id: str,
    tool_name: str,
    request_id: str,
) -> dict | None:
    """Return trusted control data for this exact pending approval dispatch."""
    if result.get("isError") or outcome != "pending_approval":
        return None
    approval_id = None
    for item in result.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            parsed = json.loads(item.get("text", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(parsed, dict)
            and parsed.get("status") == "pending_approval"
            and isinstance(parsed.get("approval_id"), str)
            and parsed["approval_id"]
        ):
            approval_id = parsed["approval_id"]
            break
    if approval_id is None:
        return None

    record = get_approval(data.store, approval_id)
    if record is None:
        return None

    internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(tool_name, tool_name)
    if (
        record.status != STATUS_PENDING
        or record.expires_at <= utcnow()
        or record.access_key_id != access_key_id
        or record.tool_name not in (internal_name, MESA_APPROVED_EXECUTOR)
        or record.request_id != request_id
    ):
        return None
    return {
        "approval_id": record.id,
        "review_url": f"/phoenix-mcp/approvals/{record.id}",
        "expires_at": record.expires_at.isoformat(),
        "record": record,
    }


def refused_before_write(approval: Any) -> bool:
    """Whether an approved action's executor refused before it could write anything.

    The approve path persists the flag only for a tool_common.RefusedBeforeWrite
    result, so the failure has no outcome to reconcile. Anything else, including
    a record without the flag, keeps the unknown-outcome handling.
    """
    result = getattr(approval, "result", None)
    return (getattr(approval, "status", None) == STATUS_FAILED
            and getattr(approval, "rejected_reason", None) == "execution_failed"
            and isinstance(result, dict) and result.get("refused_before_write") is True)


def _resolved_result(status: str, applied_result: dict | None, approval: Any = None) -> dict:
    """A CallToolResult to feed back after an interactive approval resolves.

    Anything that did not apply reads as tool_common.approval_outcome words it,
    so a rejection with a reason is actually reported instead of a generic notice
    and an approved-but-failed record never reads as a rejection. An approved
    result carries the operator-accepted note
    (tool_common._operator_accepted_result) so the agent treats the approved
    change as settled instead of iterating on it.
    """
    if status == STATUS_APPROVED and applied_result is not None:
        return _operator_accepted_result(applied_result)
    if status in (STATUS_FAILED, STATUS_REJECTED, STATUS_CANCELLED):
        response_status, message = approval_outcome(status, approval)
        return {"content": [{"type": "text", "text": json.dumps({
            "status": response_status,
            "message": message,
        })}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps({
        "status": "pending_approval",
        "message": "Still awaiting approval; it was not applied in this session. Do not retry.",
    })}]}


async def _dispatch_with_progress(emit: Any, tool_id: Any, coro: Any) -> Any:
    """Run one tool call, relaying whatever it reports while it is still running.

    Tools that hold the request open (a firmware build, a confirm gate) describe
    what they are waiting on through the progress bus in tool_common, which
    exists so an SSE-framed MCP request can emit notifications/progress. Agent Chat is the
    other consumer of the same signal: it owns both ends of its own stream, so it
    needs no progressToken negotiation and can always show the line.

    The bus is set BEFORE the task is created so the task's copied context holds
    the same object, matching how the MCP transport's SSE writer does it. Only changed
    statuses are emitted, so a slow build does not spam the transcript.
    """

    bus = _ProgressBus()
    _progress_ctx.set(bus)
    task = asyncio.ensure_future(coro)
    last: str | None = None
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=AGENTCLI_PROGRESS_INTERVAL_SECONDS)
            if done:
                return await task
            if bus.status and bus.status != last:
                last = bus.status
                await emit("tool_progress", {
                    "id": tool_id,
                    "message": bus.status,
                    **({"message_key": bus.status_key} if bus.status_key else {}),
                    **({"message_params": bus.status_params} if bus.status_params else {}),
                })
    finally:
        _progress_ctx.set(None)
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def _review_stop_error(reason: str, data: PhoenixData) -> dict:
    """The chat error for a tool review that stopped the turn, naming what stopped it."""
    if reason == "cancelled":
        return {"code": "cancelled", "message": "Cancelled."}
    if reason in {"catalog_changed", "context_changed"}:
        return {"code": "review_changed", "message": (
            "The access key's tools or the conversation changed while the tool call was reviewed, "
            "so nothing was run. Send the request again.")}
    if _kill_switch_on(data):
        return {"code": "kill_switch", "message": "The kill switch is on, so the agent stopped. Nothing more was run."}
    return {"code": "unauthorized", "message": "This access key is no longer authorized."}


async def _cancel_agent_approval(
    hass: HomeAssistant, data: PhoenixData, approval_id: str, reason: str = REASON_AGENT_CHAT_ENDED,
) -> Any:
    """Cancel only abandoned pending work whose executor has never been claimed."""
    async with data.store.async_lock:
        record = get_approval(data.store, approval_id)
        if (record is None or record.status != STATUS_PENDING
                or record.execution_started_at is not None
                or approval_id in data.approvals_in_progress):
            return None
        resolved = await async_update_approval_status(
            data.store, approval_id, status=STATUS_CANCELLED,
            rejected_reason=reason,
        )
    if resolved is not None and resolved.status == STATUS_CANCELLED:
        dismiss_approval_notification(hass, approval_id)
        fire_approval_resolved_event(hass, resolved)
    return resolved


async def _await_agent_approval(
    hass: HomeAssistant, data: PhoenixData, approval_id: str, cancel: asyncio.Event, timeout: float,
) -> dict:
    """Block until an approval resolves (or cancel/timeout), then return a result.

    Modeled on tool_common._await_inline_confirm but tied to the live SSE
    connection (long timeout) and cancel-aware. The bus event carries only IDs, so the
    executed result is re-read from the persisted approval record.
    """
    future: asyncio.Future = hass.loop.create_future()

    @callback
    def _on_resolved(event: Any) -> None:
        # @callback so HA runs this in the event loop (not the executor thread);
        # otherwise future.set_result would never wake the awaiting loop.
        if event.data.get("approval_id") == approval_id and not future.done():
            future.set_result(True)

    unsub = hass.bus.async_listen(f"{DOMAIN}_approval_resolved", _on_resolved)
    cancel_task = hass.loop.create_task(cancel.wait())
    try:
        await asyncio.wait({future, cancel_task}, timeout=timeout,
                           return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        await _cancel_agent_approval(hass, data, approval_id)
        raise
    finally:
        unsub()
        if not cancel_task.done():
            cancel_task.cancel()

    latest = get_approval(data.store, approval_id)
    if latest is None:
        return _resolved_result(STATUS_PENDING, None)
    if latest.status == STATUS_PENDING:
        # cancel_task is always done here (the finally cancels it), so the event
        # decides. A chat that ended outranks a wait that ran out at the same time.
        reason = REASON_AGENT_CHAT_ENDED if cancel.is_set() else REASON_AGENT_CHAT_WAIT_TIMED_OUT
        resolved = await _cancel_agent_approval(hass, data, approval_id, reason)
        if resolved is not None and resolved.status == STATUS_CANCELLED:
            return _resolved_result(STATUS_CANCELLED, None, resolved)
        # In-progress, or an admin resolved it in the race: re-read the true outcome.
        latest = get_approval(data.store, approval_id) or latest
    applied = None
    if latest.status == STATUS_APPROVED and latest.result:
        applied = latest.result.get("tool_result")
    return _resolved_result(latest.status, applied, latest)


_STYLE_INSTRUCTIONS = {
    "direct": (
        "Use plain, efficient, matter-of-fact language and lead with the outcome. Do not add "
        "praise, encouragement, conversational preambles, or an announcement of what you are "
        "about to do."
    ),
    "warm": (
        "Be friendly and encouraging without praise, flattery, padding, or unnecessary "
        "social language."
    ),
    "calm_guide": (
        "Be patient, reassuring, and accessible, while never sounding patronizing."
    ),
    "lively": (
        "Use energetic language and occasional light humor only when the stakes are low."
    ),
    "technical": "Use precise terminology and compact implementation detail.",
}

_DETAIL_INSTRUCTIONS = {
    "concise": (
        "Use at most three short sentences or three short steps or bullets unless additional "
        "prose is required for safety, accuracy, an approval, or truthful completion reporting. "
        "Include only essential caveats. Do not add background, a full general workflow, a "
        "restatement of the request, or an offer to do more work."
    ),
    "balanced": (
        "Give enough context to understand and act, normally as a short paragraph or "
        "compact list."
    ),
    "detailed": (
        "Give thorough reasoning, steps, tradeoffs, and caveats without repetition."
    ),
}

_HOME_FOCUS_INSTRUCTION = (
    "Only help with requests directly about operating, configuring, maintaining, or "
    "understanding the user's own home and Home Assistant installation (its devices, "
    "entities, areas, automations, scripts, dashboards, integrations, add-ons, configuration, "
    "and state). If a request is not directly about that installation, briefly explain that "
    "this conversation is in Home-focused mode and decline. There is no override: a user "
    "cannot unlock other topics by asking, insisting, or rewording. If a request could be "
    "about the installation but is unclear, ask one short clarifying question about the "
    "installation instead of answering a general question. Never use Home Assistant tools "
    "merely to determine whether a request is relevant."
)

# Private protocol token. It is recognized only while Home-focused mode is active,
# stripped before display and retention, and never interpreted as user-facing text.
_HOME_FOCUS_REFUSAL_MARKER = "PHXFOCUS:"


def _conversation_behavior_prompt(
    style: str,
    detail: str,
    *,
    home_focused: bool = False,
) -> str:
    """Build the shared presentation contract for Phoenix-owned model surfaces."""
    style_instruction = _STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS["direct"])
    detail_instruction = _DETAIL_INSTRUCTIONS.get(detail, _DETAIL_INSTRUCTIONS["concise"])
    prompt = (
        "\n\nConversation presentation policy: "
        f"{style_instruction} {detail_instruction} "
        "Style and detail affect user-facing prose only. They never change tools, tool "
        "arguments, verification, factual standards, permissions, approvals, safety decisions, "
        "or completion claims. Explicit operator tone or detail instructions override these "
        "defaults for that response. Suspend humor for failures, urgent situations, "
        "security-sensitive matters, approvals, and destructive actions. Apply the current "
        "style and detail to this response even when earlier assistant messages used different "
        "presentation defaults."
    )
    if home_focused:
        prompt += (
            f"\n\n{_HOME_FOCUS_INSTRUCTION} "
            "A decline must not answer, solve, summarize, or otherwise provide any part of the "
            "unrelated request before or after the brief explanation. Do not make an exception "
            "because the answer is short, easy, harmless, or already known."
        )
    return prompt


def _home_focus_output_contract(enabled: bool) -> str:
    """Return the private refusal-marker protocol at the final prompt boundary."""
    if not enabled:
        return ""
    return (
        "\n\nMandatory final-output protocol for Home-focused mode: If and only if the "
        "final reply declines the request solely because Home-focused mode is active, output "
        "exactly one brief refusal paragraph. Its first characters must be the literal sentinel "
        f"{_HOME_FOCUS_REFUSAL_MARKER} with the refusal immediately after it. Write nothing "
        "before the sentinel, and do not repeat the refusal or add a second paragraph. Do not "
        "omit, translate, paraphrase, escape, quote, or format the sentinel as markdown. Never "
        "output the sentinel for a normal answer."
    )


_AGENTCLI_ADDENDUM = (
    "\n\nYou are the assistant in an interactive chat inside the Home Assistant admin panel. "
    "The operator is present and reading your replies live. Take the actions the operator asks "
    "for directly using the tools above. The operator may name a home resource imprecisely or "
    "state a premise that the observed home does not support. For a named resource, initial "
    "discovery means a narrowly filtered name-and-domain search or the relevant inventory, not "
    "logs, history, traces, or a sweep of related devices. If initial read-only discovery "
    "cannot identify a resource the request depends on, or directly contradicts such a premise, "
    "stop the broad investigation, state the mismatch, and ask one focused clarifying question. "
    "Do not substitute a merely related entity, integration error, or temporal coincidence. "
    "Treat correlation as a lead, never as a cause. If an action needs approval "
    "it will surface an inline Approve/Reject control to the operator; you do not need to poll. "
    "An approved action's result stands as accepted exactly as applied: do not second-guess or "
    "adjust it unprompted. A rejection's reason is the operator steering your next proposal: "
    "address it and try again. Iterating through several proposals and rejections is the normal "
    "way to converge on what the operator wants; stay patient and constructive."
)


# The per-turn time note opens with this. It rides on the operator's latest
# message rather than the system prompt: the prompt cache is a prefix, so a
# changing line ahead of the history would re-bill the whole history every turn.
_TIME_CONTEXT_LEAD = "\n\n[Phoenix note: Home Assistant's local time zone is "


def _agentcli_time_context(hass: HomeAssistant) -> str:
    """State Home Assistant's zone and the current local time for one chat turn."""
    now = dt_util.now()
    offset = now.strftime("%z")
    offset_label = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else "UTC"
    return (
        _TIME_CONTEXT_LEAD
        + f"{hass.config.time_zone} ({offset_label}). The current local date and time at the "
        f"start of this turn is {now.strftime('%Y-%m-%d %H:%M:%S')}.]"
    )


def _with_time_context(messages: list[dict], index: int | None, note: str) -> list[dict]:
    """Copy messages with the turn's time note appended to the operator message at index.

    The stored transcript stays clean; only the request copy carries the note.
    """
    if index is None:
        return messages
    message = messages[index]
    content = message.get("content")
    if isinstance(content, str):
        stamped = {**message, "content": content + note}
    elif isinstance(content, list):
        stamped = {**message, "content": [*content, {"type": "text", "text": note.lstrip("\n")}]}
    else:
        return messages
    return [*messages[:index], stamped, *messages[index + 1:]]


def _agentcli_time_guidance() -> str:
    """Tell Agent Chat how to present Home Assistant timestamps locally.

    Unchanged between turns, so it stays in the cached system prompt; the clock
    itself is the note _agentcli_time_context adds to the operator's message.
    """
    *leading, last = sorted(_AGENTCLI_LOCAL_QUERY_FIELDS)
    tools = f"{', '.join(leading)} and {last}"
    return (
        "\n\nThe operator's latest message ends with a Phoenix note giving Home Assistant's "
        "local time zone and the current local date and time at the start of the turn. Phoenix "
        "adds that note; the operator did not write it, so do not answer or quote it. Common temporal tool "
        "results preserve their canonical timestamps and add authoritative companion fields "
        "whose names end in _local. Use the _local value when reasoning or reporting; never "
        f"calculate a local value from its canonical counterpart. For {tools} absolute "
        "time arguments, send the intended Home Assistant wall time without a timezone "
        "suffix, for example 2026-08-14T05:00:00. "
        "Phoenix applies the configured zone; do not shift the window yourself. State dates "
        "and times matter-of-factly. Do not mention local time, UTC, offsets, or conversion "
        "unless the operator explicitly asks."
    )


# --------------------------------------------------------------------------- #
# The agent loop
# --------------------------------------------------------------------------- #

async def _cancelable_provider_events(events: Any, cancel: asyncio.Event) -> AsyncGenerator[dict, None]:
    """Close the HTTP generator even when cancellation arrives during a quiet stream."""
    stopped = asyncio.create_task(cancel.wait())
    next_event: asyncio.Task | None = None
    try:
        while not cancel.is_set():
            next_event = asyncio.create_task(anext(events))
            done, _ = await asyncio.wait((next_event, stopped), return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                return
            try:
                event = next_event.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        if next_event is not None:
            next_event.cancel()
        stopped.cancel()
        await asyncio.gather(*([next_event] if next_event is not None else []),
                             stopped, return_exceptions=True)
        await events.aclose()


def _after_dispatch_failure(ev: dict, write_started: bool) -> dict:
    """A provider failure after this turn dispatched a tool: never retried, uncertain after a write.

    Reads still stop automatic retries but cannot create an uncertain device
    action, so only a write is reported as outcome unknown. Classified from
    server annotations, never from the model.
    """
    ev = {**ev, "retryable": False, "dispatch_started": True}
    if write_started:
        ev = {**ev, "code": "provider_after_dispatch", "outcome": "outcome_unknown",
              "message": "The provider failed after tool dispatch. Earlier actions may have run. Reconcile Home Assistant and approval results before retrying."}
    return ev


async def _stream_turn_resilient(
    provider: ClaudeProvider | OpenAICompatProvider, session: ClientSession, *,
    system_prompt: str, messages: list[dict], tools: list[dict], options: dict,
    cancel: asyncio.Event,
    dispatch_started: bool = False,
    write_started: bool = False,
    first_reply_seconds: float | None = None,
) -> AsyncIterator[dict]:
    """provider.stream_turn wrapped with a bounded retry for a transient connection failure.

    Covers a failure (e.g. a DNS/connect timeout) that occurs BEFORE any content
    is produced: retries with a short backoff until _CONNECT_RETRY_WINDOW_SECONDS
    elapses, then surfaces the error (annotated that it retried). Never retries
    once any content/tool/done event has been seen (that could duplicate
    already-streamed output) or once a tool dispatch has started; a mid-stream or
    non-retryable error passes straight through. Every other event is yielded
    unchanged.

    first_reply_seconds bounds the wait for the first attempt's first event too;
    past it the call ends as provider_slow, not retried. A surface with someone
    waiting on a reply (Voice) sets it; the others keep only the silent-stream bound.
    """
    loop_time = asyncio.get_running_loop().time
    started = loop_time()
    deadline = started + _CONNECT_RETRY_WINDOW_SECONDS
    attempt = 0
    # The provider's own account of the failure that started the retries, which
    # the final error keeps: the retry's own failure is often a bare timeout.
    first_detail = ""
    backoff = _CONNECT_RETRY_BACKOFF_SECONDS
    while not cancel.is_set():
        attempt += 1
        produced = False
        retry = False
        events = _cancelable_provider_events(provider_events(provider.stream_turn(
            session, system_prompt=system_prompt, messages=messages, tools=tools, options=options,
        ), config=getattr(provider, "cfg", None), messages=messages), cancel)
        # The deadline only covers the wait for a retry's (or a bounded surface's)
        # first event, during which this generator is awaiting the provider, never
        # suspended at a yield.
        first_reply = asyncio.timeout(None)
        limit = AGENTCLI_RETRY_FIRST_REPLY_SECONDS if attempt > 1 else first_reply_seconds
        if attempt > 1 and first_reply_seconds is not None:
            limit = min(AGENTCLI_RETRY_FIRST_REPLY_SECONDS, first_reply_seconds)
        try:
            async with first_reply, aclosing(events):
                if limit is not None:
                    first_reply.reschedule(loop_time() + limit)
                async for ev in events:
                    first_reply.reschedule(None)
                    etype = ev.get("type")
                    # A provider that names its own wait (Retry-After, Google's
                    # RetryInfo) is retried only after it, and only when that fits
                    # the window; an earlier retry is refused again.
                    wait = _CONNECT_RETRY_BACKOFF_SECONDS
                    named = (ev.get("rate_limit") or {}).get("retry_after_seconds") if etype == EV_ERROR else None
                    if isinstance(named, int) and named > wait:
                        wait = float(named)
                    if (etype == EV_ERROR and not produced and not dispatch_started and ev.get("retryable")
                            and attempt < _CONNECT_RETRY_MAX_ATTEMPTS and not cancel.is_set()
                            and loop_time() + wait < deadline):
                        retry = True
                        backoff = wait
                        first_detail = first_detail or _failure_detail(ev)
                        yield {"type": EV_RETRYING, "attempt": attempt + 1, "status": ev.get("status"),
                               "detail": _failure_detail(ev), "detail_code": _failure_detail_code(ev)}
                        break
                    if etype == EV_ERROR and dispatch_started:
                        ev = _after_dispatch_failure(ev, write_started)
                    if etype == EV_ERROR and attempt > 1:
                        ev = _retry_exhausted(ev, int(loop_time() - started), first_detail)
                    if etype in (EV_TEXT, EV_THINKING, EV_TOOL, EV_DONE, EV_INVALID_TOOLS):
                        produced = True
                    yield ev
        except TimeoutError:
            if not first_reply.expired():
                raise
            if attempt == 1:
                slow = {"type": EV_ERROR, "status": 0, "code": "provider_slow", "retryable": False,
                        "message": f"The provider sent no reply within {limit:g}s.",
                        "message_params": {"seconds": f"{limit:g}"}}
                yield _after_dispatch_failure(slow, write_started) if dispatch_started else slow
                return
            stalled = {"type": EV_ERROR, "status": 0, "code": "network", "retryable": True,
                       "message": f"The provider sent no reply within {limit:g}s."}
            yield _retry_exhausted(stalled, int(loop_time() - started), first_detail)
            return
        if not retry or cancel.is_set():
            return
        try:
            await asyncio.wait_for(cancel.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass


def _failure_detail(ev: dict) -> str:
    """The provider's own sentence about a failure, or Phoenix's description of it."""
    return str(ev.get("provider_detail") or ev.get("message") or "").strip()


def _failure_detail_code(ev: dict) -> str:
    """The code behind _failure_detail when that detail is Phoenix's own English.

    The panel translates the detail through this code; a provider's own sentence
    has no code here, so it shows verbatim.
    """
    return "" if str(ev.get("provider_detail") or "").strip() else str(ev.get("code") or "")


def _retry_exhausted(ev: dict, seconds: int, first_detail: str) -> dict:
    """The final error after retrying: the real time spent and, kept, the failure that started it."""
    detail = str(ev.get("message") or "").strip()
    if ev.get("status") == 429 and ev.get("code") == "rate_limit":
        code = "provider_rate_limit_exhausted"
        message = f"The provider's rate limit remained exceeded after retrying for about {seconds}s. Provider detail: {detail}"
    else:
        code = "provider_retry_exhausted"
        message = f"The provider did not answer after retrying for about {seconds}s. {detail}"
    extra = {"provider_detail": first_detail} if first_detail and not ev.get("provider_detail") else {}
    return {**ev, **extra, "code": code, "message": message.strip(),
            "message_params": {"seconds": seconds, "detail": detail, "detail_code": str(ev.get("code") or "")}}


class _CallStatus:
    """Agent Chat's status line for one model call, in place of a bare spinner.

    Built only from facts the loop observes: the provider, the turn's step and
    tools run so far, how long nothing has been shown, a model that is thinking,
    and a retry with the provider's own sentence. While nothing is visible it is
    refreshed every AGENTCLI_STATUS_INTERVAL_SECONDS.
    """

    def __init__(self, emit: Callable[[str, dict], Awaitable[None]], provider: Any, *, step: int, tools: int) -> None:
        definition = PROVIDER_DEFINITIONS.get(str(getattr(provider, "kind", "")))
        self._emit = emit
        self._fields: dict[str, Any] = {
            "provider": definition.label if definition is not None else str(getattr(provider, "kind", "")),
            "step": step, "tools": tools,
        }
        self._time = asyncio.get_running_loop().time
        self._since = self._time()
        self._phase = "waiting"
        self._shown = False
        self._ticker: asyncio.Task | None = None

    async def start(self) -> None:
        await self._send()
        self._ticker = asyncio.create_task(self._tick())

    def stop(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()

    async def observe(self, ev: dict) -> None:
        etype = ev.get("type")
        if etype in (EV_TEXT, EV_TOOL, EV_DONE):
            # The reply or a tool line now speaks for itself.
            self._shown = True
        elif etype == EV_THINKING and self._phase != "thinking":
            self._phase = "thinking"
            await self._send()
        elif etype == EV_RETRYING:
            self._since, self._phase = self._time(), "waiting"
            self._fields.update(attempt=ev.get("attempt"), detail=ev.get("detail") or "",
                                detail_code=ev.get("detail_code") or "")
            await self._send(retrying=True)

    async def _send(self, retrying: bool = False) -> None:
        await self._emit("status", {**self._fields, "phase": "retrying" if retrying else self._phase,
                                    "seconds": int(self._time() - self._since)})

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(AGENTCLI_STATUS_INTERVAL_SECONDS)
            if not self._shown:
                await self._send()


@dataclasses.dataclass
class _ModelCallResult:
    """Everything one model call produced, drained from its event stream."""

    tool_calls: list[dict]
    assistant_msg: dict | None
    stop_reason: str
    errored: bool
    saw_text: bool
    usage_input: int
    usage_output: int
    context_tokens: int
    focus_declined: bool
    # (cache read, cache write) prompt tokens, included in usage_input.
    usage_cache: tuple[int, int] = (0, 0)
    # The EV_INVALID_TOOLS event when the response's tool arguments did not parse.
    invalid_tools: dict | None = None


def _strip_focus_marker_text(text: str) -> tuple[str, bool]:
    """Strip a leading private focus marker while preserving ordinary model text."""
    stripped = text.lstrip()
    if not stripped.startswith(_HOME_FOCUS_REFUSAL_MARKER):
        return text, False
    return stripped[len(_HOME_FOCUS_REFUSAL_MARKER):].lstrip(), True


def _strip_focus_marker_message(message: dict) -> tuple[dict, bool]:
    """Strip the marker from Anthropic, OpenAI, or Mistral assistant content."""
    content = message.get("content")
    if isinstance(content, str):
        cleaned, found = _strip_focus_marker_text(content)
        return ({**message, "content": cleaned} if found else message), found
    if not isinstance(content, list):
        return message, False
    for index, block in enumerate(content):
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        if not block["text"].strip():
            continue
        cleaned, found = _strip_focus_marker_text(block["text"])
        if not found:
            return message, False
        blocks = list(content)
        blocks[index] = {**block, "text": cleaned}
        return {**message, "content": blocks}, True
    return message, False


@dataclasses.dataclass
class _FocusPrefixBuffer:
    """Hold only enough leading streamed text to recognize the private marker."""

    enabled: bool
    pending: str = ""
    decided: bool = False
    declined: bool = False

    def feed(self, text: str) -> str:
        if not self.enabled or self.decided:
            return text
        self.pending += text
        candidate = self.pending.lstrip()
        # The marker check comes first: some providers stream whole sentences,
        # so the marker can arrive inside a chunk longer than the hold limit.
        bounded = len(self.pending) <= len(_HOME_FOCUS_REFUSAL_MARKER) + 64
        if candidate.startswith(_HOME_FOCUS_REFUSAL_MARKER):
            if bounded and not candidate[len(_HOME_FOCUS_REFUSAL_MARKER):].strip():
                # Wait for the refusal itself so its leading space is stripped too.
                return ""
            self.decided = True
            self.declined = True
            visible, _ = _strip_focus_marker_text(self.pending)
            self.pending = ""
            return visible
        # Hold only text that could still become the marker, and only a bounded
        # run of leading whitespace before it.
        if bounded and _HOME_FOCUS_REFUSAL_MARKER.startswith(candidate):
            return ""
        self.decided = True
        visible = self.pending
        self.pending = ""
        return visible

    def finish(self) -> str:
        if not self.pending:
            return ""
        visible, declined = _strip_focus_marker_text(self.pending)
        self.pending = ""
        self.decided = True
        self.declined = self.declined or declined
        return visible


async def _consume_model_stream(
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    *,
    system: str,
    messages: list[dict],
    tools: Any,
    options: dict,
    cancel: asyncio.Event,
    emit: Callable[[str, dict], Awaitable[None]],
    usage_done_input: int,
    usage_done_output: int,
    context_tokens: int,
    usage_done_cache: tuple[int, int] = (0, 0),
    detect_focus_refusal: bool = False,
    dispatch_started: bool = False,
    write_started: bool = False,
    status: dict | None = None,
) -> _ModelCallResult:
    """Drain one model call's normalized event stream into a result.

    Pure translation of provider events to SSE frames plus accumulation; it
    makes no policy decisions and runs no tools. The usage totals are passed in
    because EV_USAGE is cumulative-REPLACE per call, not additive, so the frame
    the browser receives must add this call's latest report to the finished
    calls' running sum.
    """
    tool_calls: list[dict] = []
    assistant_msg: dict | None = None
    invalid_tools: dict | None = None
    stop_reason = "end_turn"
    errored = False
    saw_text = False
    usage_cur_input = usage_cur_output = 0
    usage_cur_cache = (0, 0)
    focus = _FocusPrefixBuffer(enabled=detect_focus_refusal)
    focus_event_emitted = False
    line = _CallStatus(emit, provider, **status) if status is not None else None
    if line is not None:
        await line.start()
    try:
        async for ev in _stream_turn_resilient(
            provider, session, system_prompt=system, messages=messages,
            tools=tools, options=options, cancel=cancel, dispatch_started=dispatch_started,
            write_started=write_started,
        ):
            if line is not None:
                await line.observe(ev)
            etype = ev["type"]
            if etype == EV_TEXT:
                visible = focus.feed(ev["text"])
                if focus.declined and not focus_event_emitted:
                    await emit("focus_declined", {})
                    focus_event_emitted = True
                if visible:
                    saw_text = True
                    await emit("assistant_delta", {"text": visible})
            elif etype == EV_THINKING:
                await emit("thinking_delta", {"text": ev["text"]})
            elif etype == EV_TOOL:
                tool_calls.append(ev)
            elif etype == EV_INVALID_TOOLS:
                invalid_tools = ev
            elif etype == EV_USAGE:
                usage_cur_input = _usage_int(ev.get("input_tokens"))
                usage_cur_output = _usage_int(ev.get("output_tokens"))
                usage_cur_cache = (_usage_int(ev.get("cache_read_tokens")), _usage_int(ev.get("cache_write_tokens")))
                if usage_cur_input:
                    context_tokens = usage_cur_input
                await emit("usage", {
                    "input_tokens": usage_done_input + usage_cur_input,
                    "output_tokens": usage_done_output + usage_cur_output,
                    "context_tokens": context_tokens,
                    # Parts of input_tokens: read from and written to the provider's prompt cache.
                    "cache_read_tokens": usage_done_cache[0] + usage_cur_cache[0],
                    "cache_write_tokens": usage_done_cache[1] + usage_cur_cache[1],
                })
            elif etype == EV_DONE:
                stop_reason = ev["stop_reason"]
                assistant_msg = ev["assistant_msg"]
                if detect_focus_refusal and assistant_msg is not None:
                    assistant_msg, message_declined = _strip_focus_marker_message(assistant_msg)
                    focus.declined = focus.declined or message_declined
                    if message_declined and not focus.decided:
                        focus.pending = ""
                        focus.decided = True
                    if focus.declined and not focus_event_emitted:
                        await emit("focus_declined", {})
                        focus_event_emitted = True
            elif etype == EV_ERROR:
                await emit("error", {"code": ev.get("code"), "message": ev.get("message"),
                                     "retryable": ev.get("retryable", False),
                                     **({"outcome": "outcome_unknown"} if ev.get("outcome") == "outcome_unknown" else {}),
                                     **({"diagnostic": ev["diagnostic"]} if ev.get("diagnostic") else {}),
                                     **({"message_params": ev["message_params"]}
                                        if ev.get("message_params") else {}),
                                     # The provider's own error sentence, already
                                     # clipped and scrubbed by the adapter. The
                                     # admin panel and conversation log show it.
                                     **({"provider_detail": ev["provider_detail"]}
                                        if ev.get("provider_detail") else {}),
                                     **({"provider_code": ev["provider_code"]}
                                        if ev.get("provider_code") else {}),
                                     # The adapter has already reduced response headers
                                     # to a strict allowlist of safe numeric quota
                                     # fields. Preserve that structured snapshot
                                     # through the provider-to-SSE translation.
                                     **({"rate_limit": ev["rate_limit"]}
                                        if ev.get("rate_limit") else {}),
                                     # Which of Phoenix's own request keys the provider
                                     # refused, when it named one. The layer holding the
                                     # store records it; nothing down here can.
                                     **({"refused": ev["refused"]} if ev.get("refused") else {})})
                errored = True
    finally:
        if line is not None:
            line.stop()

    trailing = focus.finish()
    if focus.declined and not focus_event_emitted:
        await emit("focus_declined", {})
        focus_event_emitted = True
    if trailing:
        saw_text = True
        await emit("assistant_delta", {"text": trailing})

    return _ModelCallResult(
        tool_calls=tool_calls, assistant_msg=assistant_msg, stop_reason=stop_reason,
        errored=errored, saw_text=saw_text,
        usage_input=usage_cur_input, usage_output=usage_cur_output,
        context_tokens=context_tokens,
        focus_declined=focus.declined,
        usage_cache=usage_cur_cache,
        invalid_tools=invalid_tools,
    )


@dataclasses.dataclass
class _ToolBatchResult:
    """Outcome of running one round's tool calls."""

    results: list[dict]
    human_resolved: bool
    stop_turn: bool
    authority_lost: bool
    write_started: bool = False
    # A call's outcome could not be established; the turn must end with a
    # recovery barrier instead of another model round.
    outcome_unknown: bool = False


# The answers a round records when one call's outcome is unknown. The turn ends
# there, so the model reads them only on a later turn, after Needs Attention
# was reviewed.
_UNKNOWN_OUTCOME_RESULT = (
    "Outcome unknown: this action may have been applied in full, in part, or not at all, and Phoenix "
    "MCP stopped the turn. Do not repeat it. Check its effect in Home Assistant first."
)
_NOT_RUN_AFTER_UNKNOWN = (
    "Not run: an earlier call in this response has an unknown outcome, so the turn stopped before this "
    "call. Send it again only if it is still needed after that outcome is checked."
)


async def _run_tool_batch(
    tool_calls: list[dict],
    *,
    hass: HomeAssistant,
    data: PhoenixData,
    access_key_id: str,
    client_ip: str,
    base_url: str,
    emit: Callable[[str, dict], Awaitable[None]],
    cancel: asyncio.Event,
) -> _ToolBatchResult:
    """Dispatch one round's tool calls, honoring cancel and mid-turn authority loss.

    EVERY tool_use id gets a result even when nothing was dispatched: Anthropic
    requires each one answered in the next message, so a blocked call still
    appends a synthetic error rather than being skipped. That is why this drains
    the whole batch instead of returning early. An unknown outcome stops the
    batch but still answers the uncertain call and every call after it, because
    the returned conversation is what the browser sends on the next turn.

    The access key is re-resolved per call (_current_dispatch_access_key), so a mid-turn
    revoke, expiry, kill switch or cap narrowing is honored BEFORE any side
    effect, not just at the start of the turn.
    """

    results: list[dict] = []
    human_resolved = False
    stop_turn = False
    authority_lost = False
    write_started = False
    outcome_unknown = False
    # The approved executor's own error, when that is what made the outcome unknown.
    unknown_reason: str | None = None

    for tc in tool_calls:
        name = str(tc.get("name") or "")[:MAX_TOOL_NAME_LENGTH]
        dispatch_access_key = None if (cancel.is_set() or stop_turn) \
            else _current_dispatch_access_key(data, access_key_id)
        if dispatch_access_key is None:
            stop_turn = True
            if cancel.is_set():
                reason: str | None = "Cancelled by the operator; not run."
            else:
                authority_lost = True
                reason = ("Phoenix MCP was stopped by its kill switch; not run." if _kill_switch_on(data)
                          else "This access key is no longer authorized; not run.")
            results.append({"tool_use_id": tc.get("id"), "tool_name": name,
                            "result_text": reason, "is_error": True})
            continue
        args = tc.get("input") or {}
        await emit("tool_call", {"id": tc.get("id"), "name": name, "arguments": args})
        # emit() can set cancel if the client vanished mid-batch; re-check
        # AFTER the emit and before the side effect closes that gap.
        if cancel.is_set():
            stop_turn = True
            results.append({"tool_use_id": tc.get("id"), "tool_name": name,
                            "result_text": "Cancelled by the operator; not run.",
                            "is_error": True})
            continue
        time_error = _agentcli_time_arg_error(
            name, args, dt_util.now().tzinfo or dt_util.UTC,
        ) if isinstance(args, dict) else None
        if time_error is not None:
            await emit("tool_result", {
                "id": tc.get("id"), "name": name,
                "is_error": True, "summary": time_error,
            })
            results.append({
                "tool_use_id": tc.get("id"), "tool_name": name,
                "result_text": time_error, "is_error": True,
            })
            continue
        dispatch_request_id = generate_request_id()
        write_started = write_started or not tool_is_read_only(name)
        try:
            resp_msg, _m, _r, outcome = await _dispatch_with_progress(
                emit, tc.get("id"),
                dispatch_agent_tool(
                    _dispatch_mcp, hass=hass, data=data, access_key=dispatch_access_key,
                    name=name, arguments=args, tool_id=tc.get("id"),
                    client_ip=client_ip, base_url=base_url, request_id=dispatch_request_id,
                ),
            )
        except BaseException as exc:
            # Creation may have saved a queue entry before dispatch was interrupted.
            for saved in data.store.get_pending_approvals():
                if saved.get("access_key_id") == access_key_id and saved.get("request_id") == dispatch_request_id:
                    await _cancel_agent_approval(hass, data, saved["id"])
            if not isinstance(exc, RecoveryRequired):
                raise
            outcome_unknown = True
            break
        result = dispatch_tool_result(resp_msg)
        if outcome in ("denied", "invalid_request") and (resp_msg or {}).get("error", {}).get("code") == -32000:
            authority_lost = stop_turn = True
        pending = _parse_pending(
            result,
            outcome=outcome,
            data=data,
            access_key_id=access_key_id,
            tool_name=name,
            request_id=dispatch_request_id,
        )
        if pending:
            record = pending["record"]
            try:
                await emit("approval_required", {
                    "approval_id": pending["approval_id"],
                    "tool_name": name,
                    "review_url": pending.get("review_url"),
                    "expires_at": pending.get("expires_at"),
                    "diff": record.diff,
                })
                result = await _await_agent_approval(
                    hass, data, pending["approval_id"], cancel, AGENTCLI_APPROVAL_WAIT_SECONDS,
                )
            finally:
                await _cancel_agent_approval(hass, data, pending["approval_id"])
            status = _status_of(result)
            # The transcript shows WHY, not just the status: the admin's
            # typed rejection reason, or the executor's error when an
            # approval executed but failed (status "execution_failed").
            resolved_record = get_approval(data.store, pending["approval_id"])
            reason = failure = None
            if resolved_record is not None:
                failure = execution_failure_text(resolved_record)
                reason = failure or getattr(resolved_record, "rejected_reason", None)
            await emit("approval_resolved", {
                "approval_id": pending["approval_id"],
                "status": status,
                "reason": _clip_display(reason) if reason else None,
                # The executor's own text is English for the agent; the panel
                # shows its localized form, as the Approvals window does.
                **({"reason_kind": "execution"} if failure else {}),
            })
            refused = refused_before_write(resolved_record)
            if status in (STATUS_FAILED, "execution_failed") and not refused:
                # The executor may have applied part of the approved action.
                # Do not let the next model round turn its error into a replay.
                unknown_reason = execution_failure_text(resolved_record) if resolved_record else None
                outcome_unknown = True
                break
            # A refusal before writing reaches the model with the executor's
            # reason (see _resolved_result), so it can re-read and propose again.
            if status in (STATUS_APPROVED, STATUS_REJECTED) or refused:
                human_resolved = True
        is_error = bool(result.get("isError"))
        content = result.get("content", [])
        text = _agentcli_add_local_time_fields(
            name,
            _flatten_text(content),
            dt_util.now().tzinfo or dt_util.UTC,
        )
        images = _extract_image_blocks(content)
        for index, image in enumerate(images):
            await emit("tool_image", {
                "id": tc.get("id"),
                "name": name,
                "index": index,
                "mime_type": image["mime_type"],
                "data": image["data"],
            })
        await emit("tool_result", {"id": tc.get("id"), "name": name,
                                   "is_error": is_error, "summary": _clip_display(text)})
        model_text = _agentcli_add_resource_match_guidance(name, args, text)
        results.append({"tool_use_id": tc.get("id"), "tool_name": name,
                        "result_text": model_text, "images": images, "is_error": is_error})

    if outcome_unknown:
        # Every earlier call appended its result, so the uncertain call is the
        # first one left unanswered and the rest never started.
        uncertain = _UNKNOWN_OUTCOME_RESULT + (
            f" The executor reported: {_clip_display(unknown_reason)}" if unknown_reason else "")
        for index, tc in enumerate(tool_calls[len(results):]):
            results.append({"tool_use_id": tc.get("id"),
                            "tool_name": str(tc.get("name") or "")[:MAX_TOOL_NAME_LENGTH],
                            "result_text": _NOT_RUN_AFTER_UNKNOWN if index else uncertain,
                            "is_error": True})

    return _ToolBatchResult(
        results=results, human_resolved=human_resolved,
        stop_turn=stop_turn, authority_lost=authority_lost,
        write_started=write_started, outcome_unknown=outcome_unknown,
    )


_HEADLESS_PROVIDER_ERROR = "the language model could not be reached"
_FINAL_ROUND_SYSTEM = (
    "\nThis is the final response round. No further tools are available. "
    "Return the required final answer using only the results already received. "
    "Distinguish an accepted device command from a verified physical state. "
    "If the results are insufficient, explain what is known and what is missing. "
    "Do not claim that no action occurred when a control tool already ran."
)

# How a headless turn ended. A CODE, not a sentence, because the two surfaces
# render it very differently: voice speaks it, in the CONVERSATION's language via
# catalog_templates.VOICE_TEMPLATES, while AI Task raises it as an English operator-facing
# error. Prose from the loop would force an English literal into a spoken reply.
TURN_OK = "ok"
TURN_PROVIDER_ERROR = "provider_error"
TURN_OUTCOME_UNKNOWN = "outcome_unknown"
TURN_OUTPUT_LIMIT = "output_limit"
TURN_EXHAUSTED = "exhausted"
TURN_AUTHORITY_LOST = "authority_lost"
TURN_TRUNCATED = "truncated"


@dataclasses.dataclass
class HeadlessUsage:
    """Provider-reported tokens over a headless turn's model calls.

    A provider's usage report is cumulative within one call, so each call keeps
    only its latest report and the totals add the calls together. Context is the
    newest call's input, as in Agent Chat's own footer.
    """

    calls: list[tuple[int, int, int, int]] = dataclasses.field(default_factory=list)

    def start_call(self) -> None:
        self.calls.append((0, 0, 0, 0))

    def report(self, ev: dict) -> None:
        if not self.calls:
            self.start_call()
        self.calls[-1] = (_usage_int(ev.get("input_tokens")), _usage_int(ev.get("output_tokens")),
                          _usage_int(ev.get("cache_read_tokens")), _usage_int(ev.get("cache_write_tokens")))

    def frame(self) -> dict | None:
        """The Agent Chat usage frame, or None when the provider reported nothing."""
        if not any(any(call) for call in self.calls):
            return None
        return {
            "input_tokens": sum(call[0] for call in self.calls),
            "output_tokens": sum(call[1] for call in self.calls),
            "context_tokens": next((call[0] for call in reversed(self.calls) if call[0]), 0),
            "cache_read_tokens": sum(call[2] for call in self.calls),
            "cache_write_tokens": sum(call[3] for call in self.calls),
        }


# Set by a caller that shows a headless turn's usage (Home Companion in Agent Chat).
headless_usage: ContextVar[HeadlessUsage | None] = ContextVar("phoenix_headless_usage", default=None)


@dataclasses.dataclass
class _HeadlessTurnResult:
    """What one headless turn produced, and how it ended.

    `text` is the model's LAST text (reset at each tool round), so a partial
    answer survives a failure and the caller can still say something useful.

    `outcome` is one of the TURN_* codes, so a non-OK ending is never mistaken
    for a normal one: running out of rounds would otherwise look like an empty
    reply (AI Task would blame the model for JSON it never got to write), and
    hitting the model's own output limit would look like a complete answer.

    `detail` carries the provider's own error text for TURN_PROVIDER_ERROR only.
    """

    text: str
    review_urls: list[str]
    outcome: str = TURN_OK
    detail: str = ""
    # The provider error's code, so a spoken reply can choose its own sentence
    # instead of reading out the English detail.
    detail_code: str = ""
    focus_declined: bool = False
    blocked_by_prior_action: bool = False
    recovery_turn_id: str = ""


@recovery_turn
async def _run_headless_turn(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    base_url: str,
    *,
    system: str,
    seed: str,
    client_ip: str,
    max_iterations: int,
    detect_focus_refusal: bool = False,
    routing_surface: str = "voice",
    routing_language: object = None,
    reserve_final_round: bool = False,
    response_only: bool = False,
    first_reply_seconds: float | None = None,
) -> _HeadlessTurnResult:
    """Run one headless model/tool turn to completion.

    The shared engine behind async_run_voice_turn and async_run_ai_task: what a
    surface decides (error policy, audit sentinel, review URLs) lives in its
    wrapper; what both must do identically lives here, so a fix to the
    tool-dispatch path cannot land on one and miss the other.

    HEADLESS means two things, both deliberate. There is no event emission, so
    none of the interactive loop's SSE machinery applies. And a confirm gate is
    never waited on: neither an Assist pipeline nor an ai_task.generate_data call
    can hold a request open for an admin, so a pending approval is fed back as a
    "queued_for_approval" tool result and the turn continues. That rules out
    sharing _run_tool_batch, which blocks up to AGENTCLI_APPROVAL_WAIT_SECONDS on
    a human.

    The access key is re-resolved before every dispatch (_current_dispatch_access_key), so a
    mid-turn revoke, expiry, kill switch or cap narrowing is honored before any
    side effect.
    """

    await data.agent_recovery.reconcile(data.store, access_key.id)
    if data.agent_recovery.needs_attention(access_key.id):
        return _HeadlessTurnResult("", [], outcome=TURN_OUTCOME_UNKNOWN, blocked_by_prior_action=True)

    review_state = None if is_companion(access_key) else create_review_state(data, access_key, routing_surface)
    agent_tools = build_agent_tool_list(access_key, data, routing_surface)
    loader = _tool_loader(provider, agent_tools)
    if loader is not None:
        system += loader.directory()
    declared = loader.round_tools() if loader is not None else agent_tools
    tools = provider.format_tools(declared)
    tools_key = tuple(tool["name"] for tool in declared)
    if (too_many := _tool_limit_problem(provider, tools)) is not None:
        return _HeadlessTurnResult("", [], outcome=TURN_PROVIDER_ERROR, detail=too_many["message"])
    messages: list[dict] = [{"role": "user", "content": seed}]
    access_key_id = access_key.id
    cancel = asyncio.Event()  # never fired; the stream primitive expects one
    final_text = ""
    review_urls: list[str] = []
    # Bound the TOTAL provider output retained across the turn, matching
    # async_run_agent_turn: assistant and tool messages are retained across every
    # round and the growing transcript is re-sent on each one, so without a
    # ceiling a faulty or hostile provider could inflate it. Checked BEFORE the
    # append, so the offending message is never retained.
    turn_output_bytes = 0
    # Set when a dispatch was refused because the access key lost authority mid-turn.
    # The round that discovers it still finishes and still feeds the refusals
    # back, so the model gets ONE turn to tell the user; asking for tools again
    # after that is stopped rather than repeated (see below).
    authority_lost = False
    # Only a break clears this. Falling out of the for is the runaway case: the
    # round that exhausts the budget is by definition a tool round, which has
    # already reset final_text, so the caller would otherwise receive "".
    exhausted = True
    focus_declined = False
    dispatch_started = False
    write_started = False

    format_corrections = 0
    report_only = False
    for iteration in range(max_iterations):
        final_round = response_only or report_only or (reserve_final_round and iteration == max_iterations - 1)
        round_system = system
        if final_round:
            round_system += _FINAL_ROUND_SYSTEM
        if not check_turn_context(access_key, round_system, messages):
            return _HeadlessTurnResult("", review_urls, outcome=TURN_OUTPUT_LIMIT)
        if (usage := headless_usage.get()) is not None:
            usage.start_call()
        if loader is not None and not final_round:
            definitions = loader.round_tools()
            if (key := tuple(tool["name"] for tool in definitions)) != tools_key:
                tools, tools_key = provider.format_tools(definitions), key
        tool_calls: list[dict] = []
        assistant_msg: dict | None = None
        invalid: dict | None = None
        stop_reason = "end_turn"
        call_text = ""
        correct_format = False
        async for ev in _stream_turn_resilient(
            provider, session, system_prompt=round_system, messages=messages,
            tools=[] if final_round else tools, options={}, cancel=cancel, dispatch_started=dispatch_started,
            write_started=write_started, first_reply_seconds=first_reply_seconds,
        ):
            etype = ev["type"]
            if etype == EV_INVALID_TOOLS:
                # Hand malformed tool-call JSON back as tool errors while a round
                # remains for the corrected call. Home Companion keeps its own
                # stricter budget and never corrects after a write.
                companion = internal_turn(access_key)
                limit = HOME_COMPANION_MAX_FORMAT_CORRECTIONS if companion else AGENTCLI_MAX_TOOL_CALL_CORRECTIONS
                if (not final_round and format_corrections < limit and iteration < max_iterations - 1
                        and not (companion and write_started)):
                    invalid = ev
                    continue
                ev = _invalid_tools_error()
                etype = EV_ERROR
            if etype == EV_TEXT:
                call_text += ev["text"]
            elif etype == EV_TOOL:
                tool_calls.append(ev)
            elif etype == EV_USAGE:
                if usage is not None:
                    usage.report(ev)
            elif etype == EV_DONE:
                stop_reason = ev["stop_reason"]
                assistant_msg = ev["assistant_msg"]
            elif etype == EV_ERROR:
                if (internal_turn(access_key) and not write_started and not tool_calls
                        and (ev.get('code') in HOME_COMPANION_FORMAT_ERROR_CODES
                             or (ev.get('diagnostic') or {}).get('category') in HOME_COMPANION_FORMAT_ERROR_CODES)
                        and format_corrections < HOME_COMPANION_MAX_FORMAT_CORRECTIONS
                        and iteration < max_iterations - 1):
                    format_corrections += 1
                    messages.append({'role': 'user', 'content': (
                        'Your previous response was malformed and none of the tools in that response ran. '
                        'Use valid JSON objects for tool arguments, including quoted string values. '
                        'Keep the original task and restrictions unchanged.')})
                    correct_format = True
                    break
                return _HeadlessTurnResult(
                    final_text, review_urls,
                    outcome=TURN_OUTCOME_UNKNOWN if write_started else TURN_PROVIDER_ERROR,
                    detail=(voice_text(routing_language, "model_api_unsupported")
                            if ev.get("code") == "model_api_unsupported"
                            else str(ev.get("message") or _HEADLESS_PROVIDER_ERROR)),
                    detail_code=str(ev.get("code") or ""),
                    focus_declined=focus_declined,
                )
        if correct_format:
            continue
        if invalid is not None:
            format_corrections += 1
            prospective = turn_output_bytes + len(json.dumps(invalid["assistant_msg"], default=str))
            if prospective > AGENTCLI_MAX_TURN_OUTPUT_BYTES:
                return _HeadlessTurnResult(
                    final_text, review_urls, outcome=TURN_OUTPUT_LIMIT, focus_declined=focus_declined,
                )
            provider.append_assistant(messages, invalid["assistant_msg"])
            turn_output_bytes = prospective
            provider.append_tool_results(messages, invalid["results"])
            continue
        if detect_focus_refusal:
            call_text, text_declined = _strip_focus_marker_text(call_text)
            focus_declined = focus_declined or text_declined
            if assistant_msg is not None:
                assistant_msg, message_declined = _strip_focus_marker_message(assistant_msg)
                focus_declined = focus_declined or message_declined
        final_text += call_text
        if assistant_msg is not None:
            prospective = turn_output_bytes + len(json.dumps(assistant_msg, default=str))
            if prospective > AGENTCLI_MAX_TURN_OUTPUT_BYTES:
                return _HeadlessTurnResult(
                    final_text, review_urls, outcome=TURN_OUTPUT_LIMIT,
                    focus_declined=focus_declined,
                )
            provider.append_assistant(messages, assistant_msg)
            turn_output_bytes = prospective
        if final_round and tool_calls:
            for proposed in tool_calls:
                record_rejected_tool(proposed.get("id"), proposed.get("name"), proposed.get("input"),
                                     reason="No tools are permitted in the final response round.", stage="round_limit")
            # A provider ignoring the final-round instruction cannot dispatch
            # another batch and leave no round for reporting its outcome.
            return _HeadlessTurnResult("", review_urls, outcome=TURN_EXHAUSTED)
        if stop_reason != "tool_use":
            if (not write_started and invalid_read_report(access_key, final_text)
                    and format_corrections < HOME_COMPANION_MAX_FORMAT_CORRECTIONS
                    and iteration < max_iterations - 1):
                format_corrections += 1
                report_only = True
                messages.append({'role': 'user', 'content': (
                    'Your final report did not pass validation. Return exactly one JSON object '
                    'with status and a nonempty message; an answer already written belongs in message. For a count, copy the unchanged server-provided read_answer reference '
                    'from the received search result. Do not invent a count or source index. '
                    'Do not repeat discovery or run a control. Keep the original task and restrictions unchanged.')})
                final_text = ''
                continue
            exhausted = False
            break
        if authority_lost:
            # The previous round fed back "no longer authorized" for every call
            # and this round asked for tools again rather than answering. Every
            # one would be refused identically, so stop instead of spending the
            # remaining rounds re-reporting the same refusal to the same dead
            # access key. The interactive loop ends the turn outright here; headless
            # allows the one intervening round so the model can say what happened.
            exhausted = False
            break

        if validation_results := preflight_control_calls(access_key, tool_calls):
            for proposed, validation in zip(tool_calls, validation_results):
                record_rejected_tool(proposed.get("id"), proposed.get("name"), proposed.get("input"),
                                     reason=validation["result_text"], stage="companion_preflight")
            if write_started:
                incomplete_proposal(access_key)
                return _HeadlessTurnResult(json.dumps({'status': 'refused', 'message': 'A later control proposal was invalid and was not run.'}), review_urls)
            if (format_corrections >= HOME_COMPANION_MAX_FORMAT_CORRECTIONS
                    or iteration >= max_iterations - 2):
                return _HeadlessTurnResult('', review_urls, outcome=TURN_PROVIDER_ERROR)
            format_corrections += 1
            provider.append_tool_results(messages, validation_results)
            final_text = ''
            continue

        # The answer is the model's LAST text (after tools resolve), not the
        # intermediate "let me check..." text before a tool round; reset it here.
        final_text = ""
        slots, tool_calls = _local_tool_results(loader, tool_calls, data)
        if review_state is not None and tool_calls:
            review = await async_review_tools(hass, data, review_state, surface=routing_surface,
                language=routing_language, messages=messages, calls=tool_calls, system=system,
                remaining_rounds=max_iterations - iteration - 1, announced_tools=agent_tools)
            if review.action != "continue":
                provider.append_tool_results(messages, _merged_results(slots, withheld_results(tool_calls, review)))
                if review.action == "stop":
                    return _HeadlessTurnResult("", review_urls, outcome=TURN_AUTHORITY_LOST)
                continue
        results: list[dict] = []
        for tc in tool_calls:
            name = str(tc.get("name") or "")[:MAX_TOOL_NAME_LENGTH]
            dispatch_access_key = _current_dispatch_access_key(data, access_key_id)
            if dispatch_access_key is None:
                record_rejected_tool(tc.get("id"), name, tc.get("input"),
                                     reason="This access key is no longer authorized; the tool was not run.", stage="authority_guard")
                authority_lost = True
                results.append({
                    "tool_use_id": tc.get("id"), "tool_name": name,
                    "result_text": "This access key is no longer authorized; not run.",
                    "is_error": True,
                })
                continue
            dispatch_request_id = generate_request_id()
            if ambiguous_control(dispatch_access_key, hass, name, tc.get("input")):
                record_rejected_tool(tc.get("id"), name, tc.get("input"),
                                     reason="The target is ambiguous; no action was executed.", stage="target_guard")
                return _HeadlessTurnResult(json.dumps({'status': 'clarification_needed', 'message': 'Ambiguous target.'}), review_urls)
            dispatch_started = True
            write_started = write_started or not tool_is_read_only(name)
            try:
                resp_msg, _m, _r, outcome = await dispatch_agent_tool(
                    _dispatch_mcp, hass=hass, data=data, access_key=dispatch_access_key,
                    name=name, arguments=tc.get("input") or {}, tool_id=tc.get("id"),
                    client_ip=client_ip, base_url=base_url, request_id=dispatch_request_id,
                )
            except RecoveryRequired:
                return _HeadlessTurnResult("", review_urls, outcome=TURN_OUTCOME_UNKNOWN)
            result = dispatch_tool_result(resp_msg)
            if outcome in ("denied", "invalid_request") and (resp_msg or {}).get("error", {}).get("code") == -32000:
                authority_lost = True
            pending = _parse_pending(
                result,
                outcome=outcome,
                data=data,
                access_key_id=access_key_id,
                tool_name=name,
                request_id=dispatch_request_id,
            )
            if pending:
                if pending.get("review_url"):
                    review_urls.append(pending["review_url"])
                results.append({
                    "tool_use_id": tc.get("id"), "tool_name": name,
                    "result_text": json.dumps({
                        "status": "queued_for_approval",
                        "message": "Queued for admin approval in the Phoenix MCP panel; not applied yet.",
                    }),
                    "is_error": False,
                })
                if internal_turn(dispatch_access_key):
                    return _HeadlessTurnResult("", review_urls)
                continue
            content = result.get("content", [])
            result_text = _flatten_text(content)
            reference = count_source(dispatch_access_key, name)
            if reference is not None:
                try:
                    body = json.loads(result_text)
                except (ValueError, TypeError):
                    body = None
                if isinstance(body, dict):
                    result_text = json.dumps({**body, 'read_answer': reference}, ensure_ascii=False)
                else:
                    result_text += '\nread_answer: ' + json.dumps(reference)
            results.append({
                "tool_use_id": tc.get("id"), "tool_name": name,
                "result_text": result_text,
                "images": _extract_image_blocks(content),
                "is_error": bool(result.get("isError")),
            })
        # Measured against the longer final-round prompt, which the next round may use.
        append_fitting_results(access_key, system + _FINAL_ROUND_SYSTEM, messages, _merged_results(slots, results),
                               provider.append_tool_results)

    if authority_lost:
        # Reported ahead of exhaustion: a turn can hit both (authority lost in the
        # final allowed round), and the access key dying is the actionable cause. It
        # also cannot be inferred from the outside: the closing round often ends
        # normally, so without this the turn would look like an ordinary success
        # that produced nothing.
        return _HeadlessTurnResult(
            final_text, review_urls, outcome=TURN_AUTHORITY_LOST,
            focus_declined=focus_declined,
        )
    if exhausted:
        return _HeadlessTurnResult(
            final_text, review_urls, outcome=TURN_EXHAUSTED,
            focus_declined=focus_declined,
        )
    if stop_reason == "max_tokens":
        # The model stopped at its OWN output limit, so `text` is a sentence that
        # was cut off mid-thought. Reported rather than returned bare: a spoken
        # half-answer gets acted on, and a half-written JSON document fails to
        # parse and is then blamed on the model's formatting.
        return _HeadlessTurnResult(
            final_text, review_urls, outcome=TURN_TRUNCATED,
            focus_declined=focus_declined,
        )
    return _HeadlessTurnResult(
        final_text, review_urls, focus_declined=focus_declined,
    )


def _status_of(result: dict) -> str:
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                parsed = json.loads(item.get("text", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("status"):
                return parsed["status"]
    return STATUS_APPROVED if not result.get("isError") else STATUS_FAILED


def _zero_inline_wait(access_key: AccessKeyRecord) -> AccessKeyRecord:
    """Return a copy of the access key with confirm_inline_wait_seconds zeroed.

    Falls back to the original access key if it is not a dataclass.
    """
    try:
        return dataclasses.replace(access_key, confirm_inline_wait_seconds=0)
    except (TypeError, ValueError):
        return access_key


def _kill_switch_on(data: PhoenixData) -> bool:
    """Whether the runtime kill switch is what stopped a turn."""
    return bool(data.store.get_settings().kill_switch)


def _current_dispatch_access_key(data: PhoenixData, access_key_id: str) -> AccessKeyRecord | None:
    """Re-resolve the access key for one tool dispatch, or None if it may no longer act.

    A single Agent Chat turn can stay open a long time (a confirm gate blocks on
    an approval wait), so authority must be re-checked before EVERY side effect,
    not just at stream open, mirroring async_get_authenticated_access_key on the MCP path.
    Returns None when Phoenix MCP is shutting down, the runtime kill switch is on, or the
    access key has been revoked/expired or narrowed away since the turn started; the
    fresh record is returned otherwise so cap changes take effect immediately.
    """
    if (
        getattr(data, "ready", True) is False
        or getattr(data, "shutting_down", False) is True
        or data.store.get_settings().kill_switch
    ):
        return None
    access_key = data.store.get_access_key_by_id(access_key_id)
    if access_key is None or not access_key.is_valid():
        return None
    return _zero_inline_wait(access_key)
