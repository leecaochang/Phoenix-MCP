"""Shared primitives for the MCP tool surface.

The pieces every tool handler needs regardless of domain: the MCP content
envelopes (_tool_success/_tool_error/_tool_pending), the capability gate and its
inline-confirm wait, the progress bus the SSE writer drains, configuration
version capture, and the scoped file read/write plus compare-and-swap helpers.

These live here rather than in mcp_view so a per-domain tool module (see
tools/) can depend on them without importing the transport that dispatches it,
which would be a cycle. mcp_view imports the names it uses from here.
"""


import asyncio
import dataclasses
import json
from contextvars import ContextVar
import logging
import os
from typing import Any

from homeassistant.util.file import write_utf8_file_atomic as _write_utf8_file_atomic
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    MAX_DIFF_INLINE_BYTES,
    MAX_FILE_BYTES,
    MAX_CONFIRM_INLINE_WAIT_SECONDS,
    REDACTION_SENTINEL,
)
from .data import PhoenixData
from .device_semantics import (
    device_registry_snapshot_from_registry,
    effective_entity_area_id,
)
from .mesa import mesa_confirm_preview
from .helpers import (
    content_hash,
    DiffSource,
    async_evaluate_capability,
    version_summary_fields as _version_summary,
)
from .access_key_store import AccessKeyRecord

_LOGGER = logging.getLogger(__name__)


def serialize_ha_schema(schema: Any, custom_serializer: Any) -> Any:
    """Serialize an HA form schema with probatio when HA ships it, else voluptuous_serialize."""
    try:
        import probatio  # noqa: PLC0415
    except ImportError:
        import voluptuous_serialize  # noqa: PLC0415

        return voluptuous_serialize.convert(
            schema, custom_serializer=custom_serializer
        )

    if type(schema).__module__.startswith("probatio"):
        return probatio.to_field_list(schema, custom_serializer=custom_serializer)

    import voluptuous_serialize  # noqa: PLC0415

    return voluptuous_serialize.convert(schema, custom_serializer=custom_serializer)


def _tool_success(text: str) -> dict:
    """Return an MCP tool result content block with a plain-text payload."""
    return {"content": [{"type": "text", "text": text}]}


def _tool_error(message: str) -> dict:
    """Return an MCP tool result content block indicating an error."""
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _tool_pending(approval: Any, waited_seconds: int | None = None) -> dict:
    """Return an MCP tool result for a pending admin approval.

    isError is False because pending is a valid outcome, not a failure.

    THE WORDING IS LOAD-BEARING AND MUST MATCH THE STAGING MODEL. Approvals queue
    and are cleared in one operator action, so after a gate the agent should KEEP
    GOING and collect the outcomes together at the end. Keep the order: continue
    first, wait ONCE second (wait_for_approval takes a LIST of approval_ids;
    waiting on a single id serialises the agent, since no later approval can be
    created until that wait ends), finish only if nothing depends on the outcome.
    Wording that reads as "tell the user and finish now" makes an early return
    look like success. The agent primer in mcp_view must say the same.

    `waited_seconds` is set only on the inline-wait timeout path and states a FACT
    rather than different advice: that hold already spent its budget, so blocking
    again on the same id would likely spend it twice. The advice is otherwise
    identical on every path that reaches here, because the agent cannot tell
    which it is on (the inline wait is a token setting).
    """
    waited = (
        f"This call already held for {waited_seconds} seconds without resolving, so the operator "
        "is not reviewing in real time right now. "
        if waited_seconds
        else ""
    )
    body = json.dumps({
        "status": "pending_approval",
        "approval_id": approval.id,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "review_url": f"/phoenix-mcp/approvals/{approval.id}",
        "message": (
            f"{waited}This action is queued for admin approval and will be applied once approved; "
            "the admin has been notified. Do not retry it. Continue with your remaining steps: "
            "further approval-gated calls queue alongside this one and the admin can clear the "
            "whole queue in a single action, so do NOT stop or wait after each one. When you "
            "genuinely need the outcomes, call wait_for_approval ONCE with approval_ids listing "
            "every approval you are waiting on (or get_approval_status for a one-shot check) to "
            "learn which were approved or rejected, and any reason. If nothing you have left to "
            "do depends on them, tell the user what is pending and finish."
        ),
    })
    return {"content": [{"type": "text", "text": body}]}


def _tool_inline_resolved(approval: Any) -> dict:
    """Result for an inline-waited confirm that resolved without applying; isError is True.

    Tells the agent the outcome (with any reason) and not to retry, rather than
    handing back a stale pending stub. An APPROVED action whose executor then
    failed ("execution_failed") lands here too and must read as a failure to fix,
    never as a refusal, or the agent treats an approval as a refusal and loops
    retrying the same doomed call.
    """
    reason = getattr(approval, "rejected_reason", None)
    if reason == "execution_failed":
        result = getattr(approval, "result", None)
        tool_result = result.get("tool_result") if isinstance(result, dict) else None
        parts = [
            item.get("text", "")
            for item in (tool_result or {}).get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        failure = " ".join(p for p in parts if p).strip()
        if failure:
            body = json.dumps({
                "status": "execution_failed",
                "approval_id": approval.id,
                "message": (
                    f"The operator APPROVED this action, but executing it failed: {failure} "
                    "Do not retry the same call unchanged; fix the reported cause first "
                    "(for example, if it reports a stale expected_hash, re-read the "
                    "resource for the current content_hash)."
                ),
            })
            return {"content": [{"type": "text", "text": body}], "isError": True}
    if approval.status == "failed" and reason == "execution_interrupted":
        body = json.dumps({
            "status": "failed",
            "approval_id": approval.id,
            "message": (
                "The operator APPROVED this action and execution started, but its "
                "final outcome was interrupted. It may have partly applied. Check "
                "the real-world result before proposing any retry."
            ),
        })
        return {"content": [{"type": "text", "text": body}], "isError": True}
    # A rejection with a reason is the operator steering the next proposal, not a
    # stop signal: iterating through reject-with-reason rounds is the normal
    # card-building workflow, and stricter forbid-variations wording would stall
    # it (agentcli._resolved_result words the same case). A reasonless rejection
    # means ask first.
    if approval.status == "rejected" and reason:
        message = (
            f"This action was not applied (it was rejected). Reason: {reason}. "
            "That reason is the operator's direction for what to do next: "
            "address it in your next proposal instead of resubmitting the "
            "same change."
        )
    elif approval.status == "rejected":
        message = (
            "This action was not applied (it was rejected, no reason given). "
            "Do not resubmit the same change; ask the operator how they "
            "would like to proceed."
        )
    else:
        detail = f" Reason: {reason}." if reason else ""
        message = (
            f"This action was not applied (it was {approval.status}).{detail} "
            "Do not retry it; report the outcome to the user."
        )
    body = json.dumps({
        "status": approval.status,
        "approval_id": approval.id,
        "message": message,
    })
    return {"content": [{"type": "text", "text": body}], "isError": True}


def _approval_resource(approval: Any) -> str:
    """Resource string used in audit logs for a pending-approval entry."""
    return f"approval:{approval.tool_name}:{approval.id}"


@dataclasses.dataclass
class _ProgressBus:
    """What an in-flight SSE-framed request reports while it is still running.

    Single-writer by design: only the response writer touches the stream, and
    handler code deep in a tool only sets fields here, because two coroutines
    writing to one StreamResponse would interleave frames.

    `token` is the client's MCP progressToken. It is None unless the client asked
    for progress on THIS request, and no token means no progress notifications are
    ever emitted (the spec forbids unsolicited ones).
    """

    access_key: str | int | None = None
    total: float | None = None
    status: str | None = None
    status_key: str | None = None
    status_params: dict[str, Any] | None = None


# Set by the SSE writer before the dispatch task is created, so the task's copied
# context carries the same bus object the writer holds. Absent on the plain JSON
# path, where nothing can be sent before the response.
_progress_ctx: ContextVar[_ProgressBus | None] = ContextVar("phoenix_mcp_progress", default=None)


def _set_progress_status(
    status: str | None,
    total: float | None = None,
    *,
    key: str | None = None,
    params: dict[str, Any] | None = None,
) -> None:
    """Describe what the current request is waiting on, for the next SSE tick.

    A no-op unless this request is SSE-framed and the client supplied a
    progressToken, so callers need no conditionals of their own.
    """
    bus = _progress_ctx.get()
    if bus is not None:
        bus.status = status
        bus.total = total
        bus.status_key = key
        bus.status_params = params


# Uniform message for a capability-denied tool call. A denied call must look the
# same regardless of any entity it names, so it can never be used as a scope or
# existence oracle. Shared by _gate and any pre-gate deny short-circuit.
_CAP_FORBIDDEN_MESSAGE = (
    "Forbidden: this capability is not enabled for this access key. It may have changed "
    "since you connected; call get_capability_summary for the current state, refresh "
    "your tool list (reconnect) if it changed, or ask the operator to grant it."
)


# Appended to the executed result when an interactive wait returns an
# operator-APPROVED action. Without it an agent iterating toward a goal reads an
# approval as merely "this step landed" and keeps proposing variations of a change
# the operator already reviewed and settled on. The note forbids corrective
# follow-ups outright and makes a noticed discrepancy a say-it-and-stop, not a
# fix-it, so a model whose own arguments were off does not file corrective re-adds.
_OPERATOR_ACCEPTED_NOTE = (
    "Note: the operator reviewed this exact change and approved it. What was "
    "applied, exactly as it landed, is what the operator accepted: treat it as "
    "final. Do not revise, move, replace, or re-attempt it, and do not file a "
    "corrective follow-up, even if the result differs from what you intended or "
    "seems to fall short of the request. If you see a discrepancy, say so in one "
    "sentence and stop; the operator will tell you if they want it changed. Only "
    "untouched steps the operator explicitly asked for may continue."
)


def _operator_accepted_result(tool_result: dict) -> dict:
    """Return the executed result with the operator-accepted note appended as a text item.

    Same in-band pattern as mcp_view._STALE_TOOLS_ADVISORY. Shallow-copies, so the
    stored approval record's result is never mutated.
    """
    out = dict(tool_result)
    out["content"] = list(out.get("content") or []) + [
        {"type": "text", "text": _OPERATOR_ACCEPTED_NOTE}
    ]
    return out


async def _gate(
    cap_name: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    *,
    tool_name: str,
    args: dict,
    request_id: str,
    client_ip: str | None,
    diff: DiffSource,
    target_entities: list[str] | None = None,
) -> tuple[dict, str, str] | None:
    """Run capability gating. Returns a response tuple for deny/pending, or None for allow.

    For deny, returns (error_dict, "denied", tool_name).
    For pending, returns (pending_dict, "pending_approval", approval_resource).
    For allow, returns None and the caller proceeds with the side effect.

    diff should be passed as a zero-arg builder (`diff=lambda: _build_diff_x(...)`)
    rather than an already-awaited dict. Only the Confirm path reads it, so a
    builder keeps an allowed or denied call from paying for a config read, a
    lovelace load, or an ESPHome add-on round trip whose result is discarded.
    """
    result = await async_evaluate_capability(
        cap_name, access_key, hass, data,
        tool_name=tool_name, args=args, request_id=request_id,
        client_ip=client_ip, diff=diff, target_entities=target_entities,
    )
    if result.is_deny:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool_name
    if result.is_pending:
        return await _pending_or_inline(hass, data, access_key, result.approval)
    return None


async def _await_inline_confirm(
    hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord, approval: Any,
) -> tuple[dict, str, str]:
    """Optionally hold the response open for a confirm gate to resolve inline.

    When the token opts in (confirm_inline_wait_seconds > 0), block up to that
    many seconds (capped at MAX_CONFIRM_INLINE_WAIT_SECONDS) on the same
    phoenix_mcp_approval_resolved event wait_for_approval uses. On approval within the
    window, return the executed tool result the agent would otherwise have to
    poll for; on rejection, tell it the action did not apply; on timeout, fall
    back to the normal immediate pending_approval reply. The original request's
    audit outcome stays "pending_approval" in every case (the resolution is
    audited separately by the approve/reject path), so enabling the wait changes
    only what the agent receives, never how the request is logged.
    """
    from .approvals import (  # noqa: PLC0415
        STATUS_APPROVED,
        STATUS_PENDING,
        get_approval,
    )

    wait = min(access_key.confirm_inline_wait_seconds, MAX_CONFIRM_INLINE_WAIT_SECONDS)
    approval_id = approval.id
    future: asyncio.Future = hass.loop.create_future()

    @callback
    def _on_resolved(event: Any) -> None:
        if event.data.get("approval_id") == approval_id and not future.done():
            future.set_result(True)

    unsub = hass.bus.async_listen(f"{DOMAIN}_approval_resolved", _on_resolved)
    # This is the longest hold in the whole surface. On an SSE-framed request the
    # keepalive ticks carry the reason, so the client shows "waiting for the
    # operator" instead of an unexplained silence.
    _set_progress_status(
        f"Waiting for operator approval: {approval.tool_name}", total=float(wait),
        key="agentchat.progress.waitingApproval",
        params={"name": approval.tool_name},
    )
    try:
        await asyncio.wait_for(future, wait)
    except TimeoutError:
        # The one place the agent is told how long it already held, so it does
        # not immediately block on the same id and spend the budget twice.
        return _tool_pending(approval, waited_seconds=wait), "pending_approval", _approval_resource(approval)
    finally:
        unsub()
        _set_progress_status(None)

    latest = get_approval(data.store, approval_id)
    resource = _approval_resource(latest or approval)
    if latest is None or latest.status == STATUS_PENDING:
        # Event fired but the record is not terminal (a rare race): behave as if
        # it timed out, so the agent still gets a usable pending reply.
        return _tool_pending(approval), "pending_approval", resource
    if latest.status == STATUS_APPROVED and latest.result:
        tool_result = latest.result.get("tool_result")
        if tool_result is not None:
            return _operator_accepted_result(tool_result), "pending_approval", resource
    # Rejected, cancelled, or approved with no stored result: report the outcome
    # without re-queuing (the original action must never be retried).
    return _tool_inline_resolved(latest), "pending_approval", resource


async def _pending_or_inline(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    approval: Any,
) -> tuple[dict, str, str]:
    """Return a pending approval, held inline when the token is configured to wait.

    One definition for every confirm gate (capability, and the MESA gates of
    call_service and the native tools), so they answer identically and all
    deliver _OPERATOR_ACCEPTED_NOTE when an approval resolves inline.

    The wait is SKIPPED when the token already has another approval pending. The
    hold blocks the request and tool calls arrive one at a time, so approval N+1
    cannot be created until approval N finishes waiting; a batch of writes would
    spend one full wait per approval just to fill the queue the operator reviews
    in one go. An operator with an unresolved approval is not approving instantly,
    so blocking buys the agent nothing. The first call still waits, and with
    instant approval the queue is empty again by the next call.

    The skip is inferred, not a per-call flag: a flag would have to land on dozens
    of write tools (changing the catalog fingerprint for every client) or ride in
    `params._meta`, which not every client can set.
    """
    if access_key.confirm_inline_wait_seconds > 0 and not _access_key_has_other_pending(data, access_key, approval):
        return await _await_inline_confirm(hass, data, access_key, approval)
    return _tool_pending(approval), "pending_approval", _approval_resource(approval)


def _access_key_has_other_pending(data: PhoenixData, access_key: AccessKeyRecord, approval: Any) -> bool:
    """True when this token has a pending approval OTHER than this one.

    The current approval is excluded because `async_evaluate_capability` has
    already stored it, so counting it would make the queue never look empty and no
    call would ever wait. Fail-quiet: an unreadable store cannot prove a backlog,
    so the default is to wait.
    """
    from .approvals import STATUS_PENDING, list_approvals  # noqa: PLC0415

    try:
        pending = list_approvals(data.store, status=STATUS_PENDING, access_key_id=access_key.id)
    except Exception:  # noqa: BLE001 - advisory only; never fail a gate over this
        _LOGGER.debug("Could not read pending approvals for %s", access_key.id, exc_info=True)
        return False
    return any(entry.id != approval.id for entry in pending)


# Set by an admin restore (admin_view) around a re-applied executor call, so the
# executor's own capture is stamped as a "rollback" attributed to the admin rather
# than a plain create/edit. asyncio-safe: the value is scoped to the restore task.
_restore_ctx: ContextVar[dict | None] = ContextVar("phx_restore_ctx", default=None)


def _dashboard_card_total(config: dict | None) -> int | None:
    """Total cards across a dashboard layout's views and sections, or None."""
    if not isinstance(config, dict) or not isinstance(config.get("views"), list):
        return None
    total = 0
    for view in config["views"]:
        if not isinstance(view, dict):
            continue
        if isinstance(view.get("cards"), list):
            total += len(view["cards"])
        if isinstance(view.get("sections"), list):
            for section in view["sections"]:
                if isinstance(section, dict) and isinstance(section.get("cards"), list):
                    total += len(section["cards"])
    return total


def _fmt_bytes(size: int) -> str:
    return f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"


def _payload_size(payload: dict | None) -> int | None:
    """Byte size of a raw-content version payload (inline or truncated marker)."""
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    size = payload.get("bytes")
    return size if isinstance(size, int) else None


def _version_change_summary(
    resource_type: str, before: dict | None, after: dict | None
) -> dict[str, Any] | None:
    """A one-line description of what a version record changed.

    Only for resource types whose alias/resource_id says nothing about the
    change itself: dashboards (card-count movement), raw yaml/file writes
    (size movement), and entity-registry edits (which fields changed).
    Automations/scripts/scenes/helpers return None; their alias is the story.

    Returns the summary/summary_key/summary_params triple to splat into the
    version record, or None when this resource type has nothing to say.
    """
    if resource_type == "dashboard":
        b, a = _dashboard_card_total(before), _dashboard_card_total(after)
        if a is not None and b is not None and a != b:
            return _version_summary("cards.was", count=a, before=b)
        if a is not None:
            return _version_summary("cards", count=a)
        return None
    if resource_type in ("yaml_config", "file"):
        b, a = _payload_size(before), _payload_size(after)
        if a is not None and b is not None:
            return (
                _version_summary("size.was", size=_fmt_bytes(a), before=_fmt_bytes(b))
                if a != b else _version_summary("size", size=_fmt_bytes(a))
            )
        if a is not None:
            return _version_summary("size", size=_fmt_bytes(a))
        return None
    if resource_type in ("entity", "device"):
        if after is None:
            return _version_summary(f"{resource_type}.removed")
        if before is None:
            return None
        changed = sorted(
            k for k in set(before) | set(after) if before.get(k) != after.get(k)
        )
        return _version_summary(
            f"{resource_type}.changed", fields=", ".join(changed)
        ) if changed else None
    if resource_type == "config_entry" and after is None and before is not None:
        if before.get("restorable") is False:
            return _version_summary("config_entry.removed")
    return None


async def _record_version(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    *,
    resource_type: str,
    resource_id: str,
    action: str,
    before: dict | None,
    after: dict | None,
    alias: str | None = None,
    summary: dict[str, Any] | None = None,
) -> None:
    """Best-effort capture of a config change into the version history.

    Called from each _execute_* at Phoenix MCP's single execution chokepoint, so
    directly-allowed and Confirm-approved changes are each recorded exactly once.
    Never raises: a version-store failure must not fail the user's write.
    request_id is not threaded (versions correlate to the audit log by token,
    resource and timestamp); approved_by_user_id is set only on the admin restore
    path. `summary` is a one-line what-changed description for the panel's Changes
    list; callers with op context (the card tools) pass their own, and everything
    else gets the computed default (None where the alias already tells the story).
    """
    from .conversation_log import record_event
    record_event(data, "change", {"resource_type": resource_type, "resource_id": resource_id,
                                  "action": action, "before": before, "after": after})
    ctx = _restore_ctx.get()
    approved_by_user_id = None
    if ctx is not None:
        # Admin restore: this reused create/edit is really a rollback by an admin.
        action = "rollback"
        approved_by_user_id = ctx.get("user_id")
    try:
        if summary is None:
            summary = _version_change_summary(resource_type, before, after)
        await data.versions.record(
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            before=before,
            after=after,
            alias=alias,
            access_key_id=access_key.id,
            access_key_name=access_key.name,
            approved_by_user_id=approved_by_user_id,
            **(summary or {}),
        )
    except Exception:  # noqa: BLE001 - history capture must never break a write
        _LOGGER.exception(
            "Failed to record %s version for %s %s", action, resource_type, resource_id
        )
        return
    # Let the admin panel's Changes tab refresh instantly instead of waiting for
    # its poll. Best-effort: a missing hass (tests) just falls back to polling.
    if data.hass is not None:
        data.hass.bus.async_fire(
            "phoenix_mcp_config_changed",
            {"resource_type": resource_type, "resource_id": resource_id, "action": action},
        )


def _version_content_payload(content: str | None, **extra: Any) -> dict | None:
    """Build a before/after payload for raw file / configuration.yaml version capture.

    Content within MAX_DIFF_INLINE_BYTES is stored verbatim (raw, like other
    version records, so an admin restore is byte-faithful); larger content is a
    non-restorable metadata marker, bounding .storage growth. Returns None when
    there was no prior content (a fresh create).
    """
    if content is None:
        return None
    payload: dict = {**extra}
    size = len(content.encode("utf-8"))
    if size > MAX_DIFF_INLINE_BYTES:
        payload.update({"content": None, "truncated": True, "bytes": size})
    else:
        payload["content"] = content
    return payload


def _usable_path_arg(value: Any) -> str | None:
    """A caller-supplied path argument, or None when it can never name a file.

    The NUL check is why this exists rather than a bare isinstance at each jail:
    os.path.realpath RAISES ValueError on an embedded NUL instead of returning
    something the containment check could refuse, so the classic truncation probe
    would escape every path jail as an unhandled error rather than a clean refusal.
    """
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    return value


def _read_text_capped(target: str) -> str:
    if os.path.getsize(target) > MAX_FILE_BYTES:
        raise ValueError("file too large")
    with open(target, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write_text_atomic(target: str, content: str) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    _write_utf8_file_atomic(target, content)


def _cas_conflict(
    expected: Any, current: str | dict | list | None, tool_name: str
) -> tuple[dict, str, str] | None:
    """Optimistic-concurrency check for a Confirm-gated whole-blob write.

    When the caller supplied expected_hash (from the matching read tool), refuse
    the write if the target's content hashes differently now, meaning an admin or
    another agent changed it since the read. It runs both before gating (fail
    fast, so a stale write never becomes a doomed pending approval) and inside the
    executor (to catch drift during the approval window). A whole-blob replace
    must be re-read and merged, so the new hash is deliberately not echoed back.
    None (no expected_hash) skips the check.

    `current` is the target's content normalized so an absent target hashes the
    way its read tool reports it: "" for a missing raw file (get_yaml_config and
    read_file return content ""), None for an absent dashboard
    (get_dashboard_config returns not_found with no hash, so any expected_hash for
    it is a genuine conflict).
    """
    if not expected:
        return None
    current_hash = content_hash(current) if current is not None else None
    if current_hash == expected:
        return None
    return (
        _tool_error(
            "This configuration changed since you last read it (expected_hash no "
            "longer matches). Re-read it and reapply your change."
        ),
        "invalid_request",
        tool_name,
    )


async def _text_file_cas_conflict(
    expected: Any, path: str | None, hass: HomeAssistant, tool_name: str
) -> tuple[dict, str, str] | None:
    """Pre-gate CAS check for a raw-text-file writer (set_yaml_config, write_file).

    Reads the target's current text (an absent or unreadable file counts as "",
    matching what its read tool reports) and refuses if it does not hash to
    expected_hash. Skips the read entirely when expected_hash is absent.
    """
    if not expected:
        return None
    current = ""
    # Two separate executor calls: async_add_executor_job is generic over the
    # callable's return type, so one combined expression would make the type
    # checker bind a single variable to two differently-typed callables.
    if path:
        exists = await hass.async_add_executor_job(os.path.isfile, path)
        if exists:
            try:
                current = await hass.async_add_executor_job(_read_text_capped, path)
            except (OSError, ValueError):
                current = ""
    return _cas_conflict(expected, current, tool_name)


def redaction_sentinel_path(value: Any, _prefix: tuple = ()) -> list | None:
    """Find the first placeholder path, visiting shared containers only once."""
    pending = [(value, _prefix)]
    visited: set[int] = set()
    while pending:
        current, path = pending.pop()
        if isinstance(current, str):
            if REDACTION_SENTINEL in current:
                return list(path)
        elif isinstance(current, (dict, list)):
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)
            if isinstance(current, dict):
                for key in current:
                    if isinstance(key, str) and REDACTION_SENTINEL in key:
                        return [*path, key]
                children = list(current.items())
            else:
                children = list(enumerate(current))
            pending.extend((item, (*path, key)) for key, item in reversed(children))
    return None


def resolve_json_path(root: Any, path: Any) -> tuple[Any, Any, str | None]:
    """Walk `path` to the CONTAINER holding its last segment.

    Returns (container, key, None) so the caller can read, assign or delete the
    leaf itself, or (None, None, reason) when the path addresses nothing.
    Surface-agnostic: the same addressing serves a Lovelace layout, a parsed YAML
    mapping, or an automation body.

    Segments are dict keys (str) or list indices (int). NEGATIVE INDICES ARE
    REFUSED even though Python accepts them: a caller that computed -1 from a
    stale read would silently edit the wrong end of a list. NOTHING IS CREATED: a
    missing parent is a typo, not an instruction to build structure, and inventing
    containers turns a wrong path into a mangled config instead of an error.
    """
    if not isinstance(path, list) or not path:
        return None, None, "path must be a non-empty array of keys and indices."
    node = root
    for depth, segment in enumerate(path[:-1]):
        node, reason = _descend(node, segment, depth)
        if reason is not None:
            return None, None, reason
    leaf = path[-1]
    if isinstance(leaf, bool) or not isinstance(leaf, (str, int)):
        return None, None, f"path segment {len(path) - 1} must be a string key or an integer index."
    if isinstance(node, dict):
        if not isinstance(leaf, str):
            return None, None, f"path segment {len(path) - 1} indexes a mapping, so it must be a string key."
        return node, leaf, None
    if isinstance(node, list):
        if not isinstance(leaf, int):
            return None, None, f"path segment {len(path) - 1} indexes a list, so it must be an integer."
        if not 0 <= leaf < len(node):
            return None, None, f"path segment {len(path) - 1} must be an integer in 0..{len(node) - 1}."
        return node, leaf, None
    return None, None, f"path segment {len(path) - 2} does not address a mapping or a list."


def _descend(node: Any, segment: Any, depth: int) -> tuple[Any, str | None]:
    """One step of resolve_json_path's walk. Returns (child, None) or (None, reason)."""
    if isinstance(segment, bool) or not isinstance(segment, (str, int)):
        return None, f"path segment {depth} must be a string key or an integer index."
    if isinstance(node, dict):
        if not isinstance(segment, str):
            return None, f"path segment {depth} indexes a mapping, so it must be a string key."
        if segment not in node:
            return None, f"path segment {depth} ({segment!r}) does not exist."
        return node[segment], None
    if isinstance(node, list):
        if not isinstance(segment, int):
            return None, f"path segment {depth} indexes a list, so it must be an integer."
        if not 0 <= segment < len(node):
            return None, f"path segment {depth} must be an integer in 0..{len(node) - 1}."
        return node[segment], None
    return None, f"path segment {depth} does not address a mapping or a list."


def _truncate(text: str, max_chars: int = MAX_DIFF_INLINE_BYTES) -> str:
    """Bound a diff string stored on an approval record.

    The cap exists because approval diffs are persisted in .storage alongside the
    pending queue. It matches the version store's inline cap so an admin sees the
    SAME content before approving that the Changes tab shows afterwards; a smaller
    cap would clip a real automation mid-config.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... ({len(text) - max_chars} more characters)"


# Set True by a service-call path when MESA waved the action through under
# advisory mode (warnings emitted, not gated). Read at the tools/call logging
# point to flag the audit entry. A ContextVar is per-async-task and propagates
# within the same request, so it survives the await into _call_tool and back.
_mesa_advisory_ctx: ContextVar[bool] = ContextVar("phoenix_mcp_mesa_advisory", default=False)


# Set while async_execute_approved_tool runs an admin-approved action, so the MESA
# gate treats that approval as the human confirmation it asks for (confirm-approved
# semantics: confirm-mode entities proceed, prohibited/read_only entities are
# still rejected). Without it, an action gated by BOTH a confirm-mode capability
# and a MESA confirm-mode entity would need two sequential approvals: approving
# the capability gate re-runs the call, which would then queue a second, separate
# MESA approval for the action the admin just reviewed.
# asyncio-safe: scoped to the approving request's task.
_approved_exec_ctx: ContextVar[bool] = ContextVar("phoenix_mcp_approved_exec", default=False)


def _mesa_confirm_annotation(
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    groups: list[tuple[str, str, list[str]]],
) -> dict | None:
    """MESA context block for a capability-gate approval diff, or None.

    Approving a gated call also satisfies MESA's confirm for its targets
    (async_execute_approved_tool runs confirm-approved), so the approval record itself
    must say MESA was part of what the admin's click covered: the reviewing
    admin sees it on the card, and History keeps a permanent marker. Shaped like
    build_mesa_service_diff's mesa block so the panel's existing renderer picks
    it up. Best-effort: None when MESA is off/absent, nothing is MESA-confirm,
    or anything fails (the gate diff must always build).
    """
    try:
        data = hass.data.get(DOMAIN)
        if data is None:
            return None
        confirm: list[str] = []
        rest: list[str] = []
        for domain, service, ents in groups:
            if not ents:
                continue
            gated = mesa_confirm_preview(
                data, access_key,
                domain=domain, service=service, service_data={}, entities=list(ents),
            )
            confirm.extend(gated)
            rest.extend(e for e in ents if e not in gated)
        if not confirm:
            return None
        return {
            "confirm_entities": confirm,
            "allowed_entities": rest,
            "warnings": [
                "Approving also satisfies the MESA confirmation for the entities "
                "listed under MESA confirm; no separate MESA approval will be queued."
            ],
        }
    except Exception:  # noqa: BLE001 - annotation only; never block the gate diff
        return None


def _resolve_area_id(entry: Any, device_registry: Any) -> str | None:
    """Return the area_id for an entity registry entry, falling back to the device's area."""
    return effective_entity_area_id(
        entry, device_registry_snapshot_from_registry(device_registry)
    )


async def _create_registry_mesa_approval(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    *,
    tool_name: str,
    args: dict[str, Any],
    diff: dict[str, Any],
    request_id: str,
    client_ip: str | None,
    cap_name: str = "cap_registry_write",
) -> tuple[dict, str, str]:
    """Create the normal registry approval when only MESA requires confirm."""
    from .approvals import (  # noqa: PLC0415
        async_create_pending_approval,
        create_approval_notification,
        fire_approval_requested_event,
    )

    async with data.store.async_lock:
        from .request_authority import check_request_authority
        check_request_authority()
        approval = await async_create_pending_approval(
            data.store,
            access_key_id=access_key.id,
            access_key_name=access_key.name,
            tool_name=tool_name,
            cap_name=cap_name,
            args=args,
            diff=diff,
            request_id=request_id,
            client_ip=client_ip,
        )
    create_approval_notification(hass, approval)
    fire_approval_requested_event(hass, approval)
    return await _pending_or_inline(hass, data, access_key, approval)
