"""Shared primitives for the MCP tool surface.

The pieces every tool handler needs regardless of domain: the MCP content
envelopes (_tool_success/_tool_error/_tool_pending), the capability gate and its
inline-confirm wait, the progress bus the SSE writer drains, configuration
version capture, and the scoped file read/write plus compare-and-swap helpers.

These live here so a per-domain tool module (see tools/) can depend on them
without importing the dispatcher above it.
"""


import asyncio
from collections.abc import Callable
import dataclasses
import json
from contextvars import ContextVar
import logging
import os
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import label_registry as lr

from .approvals import (
    async_create_pending_approval, create_approval_notification, fire_approval_requested_event,
    STATUS_PENDING, list_approvals, STATUS_APPROVED, get_approval,
)
from .const import (
    DOMAIN,
    DOMAIN_IMPORTANT_ATTRIBUTES,
    LEAN_ALWAYS_ATTRS,
    MAX_DIFF_INLINE_BYTES,
    MAX_FILE_BYTES,
    MAX_CONFIRM_INLINE_WAIT_SECONDS,
    REDACTION_SENTINEL,
    REGISTRY_ID_HINT_MAX,
    TOOL_RESULT_MAX_BYTES,
)
from .config_recovery import ConfigurationRecoveryError
from .conversation_log import record_event, json_size
from .mesa_audit import note_mesa_advisory
from .device_semantics import (
    device_registry_snapshot_from_registry,
    effective_entity_area_id,
)
from .mesa import mesa_confirm_preview
from .helpers import content_hash
from .catalog_text import version_summary_fields as _version_summary
from .approvals import DiffSource, _resolve_diff, async_evaluate_capability
from .access_key_store import AccessKeyRecord
from .request_authority import AuthorityChangedError, check_request_authority

if TYPE_CHECKING:
    from .data import PhoenixData

_LOGGER = logging.getLogger(__name__)


def serialize_ha_schema(schema: Any, custom_serializer: Any) -> Any:
    """Serialize an HA form schema with the engine that built it.
    The schema's type picks probatio or voluptuous_serialize, not what is importable. A
    field's ``visible`` condition is left out, as HA's own frontend gets it from a second
    step.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant import data_entry_flow
    if type(schema).__module__.startswith("probatio"):
        # Local: ships with an optional component, not with this integration.
        import probatio

        converted = probatio.to_field_list(schema, custom_serializer=custom_serializer)
        add_conditions = getattr(data_entry_flow, "add_visible_conditions_to_serialized_schema", None)
        if add_conditions is not None and isinstance(converted, list):
            add_conditions(schema, converted)
        return converted
    # Local: ships with an optional component, not with this integration.
    import voluptuous_serialize

    return voluptuous_serialize.convert(schema, custom_serializer=custom_serializer)


def unknown_label_message(hass: HomeAssistant, label_id: str) -> str:
    """Refuse an unknown label and name the label IDs that do exist."""

    known = sorted(lr.async_get(hass).async_list_labels(), key=lambda label: label.label_id)
    listed = ", ".join(f"{label.label_id} ({label.name})" for label in known[:REGISTRY_ID_HINT_MAX])
    more = f" and {len(known) - REGISTRY_ID_HINT_MAX} more" if len(known) > REGISTRY_ID_HINT_MAX else ""
    return f"Unknown label id: {label_id}. " + (
        f"Existing label ids: {listed}{more}." if known else "No labels exist yet; create one in Home Assistant first.")


def unknown_category_message(hass: HomeAssistant, scope: str, category_id: str) -> str:
    """Refuse an unknown category and name the categories of that scope."""

    known = sorted(cr.async_get(hass).async_list_categories(scope=scope), key=lambda item: item.category_id)
    listed = ", ".join(f"{item.category_id} ({item.name})" for item in known[:REGISTRY_ID_HINT_MAX])
    more = f" and {len(known) - REGISTRY_ID_HINT_MAX} more" if len(known) > REGISTRY_ID_HINT_MAX else ""
    return f"Unknown category id {category_id!r} in scope {scope!r}. " + (
        f"Categories in this scope: {listed}{more}." if known else "This scope has no categories yet.")


def stored_result_bytes(value: Any) -> int:
    """Bytes a tool result's JSON text takes as the Conversation Log stores it."""

    return json_size(json.dumps(value, default=str))


def longest_fitting(build: Callable[[int], dict], count: int) -> dict:
    """Return the body built from the most leading rows that fits TOOL_RESULT_MAX_BYTES.

    build(k) makes the body from the first k of count rows and says how the rest continue.
    """
    body = build(count)
    if count == 0 or stored_result_bytes(body) <= TOOL_RESULT_MAX_BYTES:
        return body
    low, high = 0, count - 1
    while low < high:
        middle = (low + high + 1) // 2
        if stored_result_bytes(build(middle)) <= TOOL_RESULT_MAX_BYTES:
            low = middle
        else:
            high = middle - 1
    return build(low)


def fitted_list(rows: list[dict], key: str) -> dict:
    """A whole listing that fits one result, naming by entity_id the rows past it.

    For listings without paging, which would otherwise grow without bound on a large home.
    """
    def build(count: int) -> dict:
        body: dict = {"count": count, key: rows[:count]}
        if count < len(rows):
            body["not_listed"] = [row.get("entity_id") for row in rows[count:]]
            body["warnings"] = ["The result is full: the entities in not_listed were not listed."]
        return body

    return longest_fitting(build, len(rows))


def fitted_offset_page(page: list, envelope: dict, build: Callable[[list, dict], dict]) -> dict:
    """An offset page shrunk to what fits TOOL_RESULT_MAX_BYTES, continuing at the first row left out.

    build(rows, envelope) makes the body; a shorter page sets has_more and the
    next_offset that resumes exactly where it stopped, so nothing is skipped.
    """
    def make(count: int) -> dict:
        trimmed = dict(envelope, count=count)
        if count < len(page):
            trimmed.update(has_more=True, next_offset=envelope["offset"] + count)
        return build(page[:count], trimmed)

    return longest_fitting(make, len(page))


def offset_page(rows: list, args: dict, *, default_limit: int, max_limit: int) -> tuple[list, dict] | str:
    """Slice one offset page; returns (page, envelope) or a caller-facing error.

    The envelope matches search_entities: total before paging, the offset used,
    has_more, and next_offset only when another page exists.
    """
    limit, offset = args.get("limit", default_limit), args.get("offset", 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= max_limit:
        return f"limit must be an integer from 1 to {max_limit}."
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return "offset must be a non-negative integer."
    page = rows[offset:offset + limit]
    envelope: dict[str, Any] = {"count": len(page), "total": len(rows), "offset": offset,
                                "has_more": offset + limit < len(rows)}
    if envelope["has_more"]:
        envelope["next_offset"] = offset + limit
    return page, envelope


def _tool_success(text: str) -> dict:
    """Return an MCP tool result content block with a plain-text payload."""
    return {"content": [{"type": "text", "text": text}]}


def _tool_error(message: str) -> dict:
    """Return an MCP tool result content block indicating an error."""
    return {"content": [{"type": "text", "text": message}], "isError": True}


class UncertainOutcome(dict):
    """A tool error from a write that was dispatched and may have landed.

    It serializes as the ordinary error dict. The approve path closes such a run
    as interrupted, not failed, so nobody reads it as an action that did nothing.
    A copy that drops the type reads as a failure with its text intact.
    """


class ReportedFailure(UncertainOutcome):
    """A dispatched service call Home Assistant itself answered with a failure.

    Agent loops hand it back to the model, as a device being offline must not hold a key at the
    unknown-outcome barrier; a copy that drops the type keeps that barrier. Approval still closes
    the run as interrupted, since a call on several targets may have applied in part.
    """


def _tool_uncertain(body: dict) -> dict:
    """Return a dispatched write whose outcome is unconfirmed, always as an error."""
    return UncertainOutcome(_tool_error(json.dumps(body, default=str)))


def _tool_reported_failure(message: str) -> dict:
    """Return a dispatched call that failed in Home Assistant, always as an error."""
    return ReportedFailure(_tool_uncertain({"status": "unknown", "retry_safe": False, "message": message}))


def _recovery_failure(exc: Exception, sent: str | None, *, lost: str, unsent: str) -> dict:
    """The error of a recovery transaction that raised; `sent` is its record id once dispatched.

    A ConfigurationRecoveryError speaks for itself. After dispatch the write may have landed, so
    the error is uncertain and keeps the record's id.
    """
    message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else None
    if sent is None:
        return _tool_error(message or unsent)
    return _tool_uncertain({"status": "unknown", "recovery_id": sent, "retry_safe": False, "message": message or lost})


async def _check_authority_before_dispatch(recovery: Any, rid: str) -> None:
    """Refuse a change whose authority lapsed, recording that nothing was sent."""
    try:
        check_request_authority()
    except AuthorityChangedError:
        await recovery.mark(rid, "not_applied")
        raise


class RefusedBeforeWrite(dict):
    """A tool error returned before the call could write anything.

    Serializes as the ordinary error dict, and tool text cannot create one. Agent loops hand
    it back to the model instead of holding the unknown-outcome barrier; a copy that drops the
    type keeps that barrier, so losing the marker fails closed.
    """


def _tool_refused_before_write(message: str) -> dict:
    """Return a tool error for a refusal made before any write could start."""
    return RefusedBeforeWrite(_tool_error(message))


def _tool_pending(approval: Any, waited_seconds: int | None = None, *, notified: bool = False) -> dict:
    """Return an MCP tool result for a pending admin approval; isError is False.

    The wording follows the staging model and matches the agent primer in tool_dispatch.
    ``waited_seconds`` states a spent wait; ``notified`` claims only a raised notification.
    """
    notice = (
        "the admin has been notified. "
        if notified
        else "approval notifications are off, so the admin sees it only in the Phoenix MCP panel. "
    )
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
            f"{notice}Do not retry it. Continue with your remaining steps: "
            "further approval-gated calls queue alongside this one and the admin can clear the "
            "whole queue in a single action, so do NOT stop or wait after each one. When you "
            "genuinely need the outcomes, call wait_for_approval ONCE with approval_ids listing "
            "every approval you are waiting on (or get_approval_status for a one-shot check) to "
            "learn which were approved or rejected, and any reason. If nothing you have left to "
            "do depends on them, tell the user what is pending and finish."
        ),
    })
    return {"content": [{"type": "text", "text": body}]}


def _approval_notifies(data: Any) -> bool:
    """Whether a new approval raises a notification, as `create_approval_notification` decides."""
    return bool(data.store.get_settings().notify_on_approval)


def _stored_tool_text(approval: Any) -> str | None:
    """The text of the tool result stored on an approval, if it holds any."""
    result = getattr(approval, "result", None)
    if not isinstance(result, dict):
        return None
    tool_result = result.get("tool_result")
    if not isinstance(tool_result, dict):
        return None
    return "\n".join(
        item.get("text", "") for item in tool_result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip() or None


def execution_failure_text(approval: Any) -> str | None:
    """Return the executor's error for an approved action that failed, else None."""
    if getattr(approval, "closure_reason", None) != "execution_failed":
        return None
    return _stored_tool_text(approval)


def approval_outcome(status: str, approval: Any) -> tuple[str, str]:
    """Return the status and message for an approval that did not apply cleanly.

    The one reading for every reporting surface. An approved action whose executor failed
    reads as a failure to fix, never as a refusal, or the agent retries the same call.
    """
    reason = getattr(approval, "closure_reason", None)
    failure = execution_failure_text(approval)
    if failure is not None:
        return "execution_failed", (
            f"The operator APPROVED this action, but executing it failed: {failure} "
            "Do not retry the same call unchanged; fix the reported cause first "
            "(for example, if it reports a stale expected_hash, re-read the "
            "resource for the current content_hash)."
        )
    if status == "failed":
        if reason == "execution_interrupted":
            return status, (
                "The operator APPROVED this action and execution started, but its "
                "final outcome was interrupted. It may have partly applied. Check "
                "the real-world result before proposing any retry."
                + (f" Reported: {text}" if (text := _stored_tool_text(approval)) else "")
            )
        detail = f" Reason: {reason}." if reason else ""
        return status, (
            f"The operator approved this action, but it could not complete.{detail} "
            "Do not treat this as an operator rejection or retry it unchanged."
        )
    # A rejection with a reason is the operator STEERING, not stopping: iterating
    # on a proposal through several reject-with-reason rounds is the normal way a
    # card gets built. Do not add a strict alternative (forbid variations, escalate
    # to a hard stop on the second rejection): models turn pedantic, give up, or
    # side-step the tools entirely. Only a reasonless rejection means ask first.
    if status == "rejected" and reason:
        return status, (
            f"This action was not applied (it was rejected). Reason: {reason}. "
            "That reason is the operator's direction for what to do next: "
            "address it in your next proposal instead of resubmitting the "
            "same change."
        )
    if status == "rejected":
        return status, (
            "This action was not applied (it was rejected, no reason given). "
            "Do not resubmit the same change; ask the operator how they "
            "would like to proceed."
        )
    detail = f" Reason: {reason}." if reason else ""
    return status, (
        f"This action was not applied (it was {status}).{detail} "
        "Do not retry it; report the outcome to the user."
    )


def _tool_inline_resolved(approval: Any) -> dict:
    """Return the outcome of a confirm resolved inline without applying; isError."""
    status, message = approval_outcome(approval.status, approval)
    body = json.dumps({"status": status, "approval_id": approval.id, "message": message})
    return {"content": [{"type": "text", "text": body}], "isError": True}


def _approval_resource(approval: Any) -> str:
    """Resource string used in audit logs for a pending-approval entry."""
    return f"approval:{approval.tool_name}:{approval.id}"


@dataclasses.dataclass
class _ProgressBus:
    """What an in-flight SSE-framed request reports while it is still running.

    Single-writer: only the response writer touches the stream, and tool code only sets fields
    here. ``progress_token`` is the client's MCP progressToken; None means no notifications
    are emitted, as the MCP spec forbids unsolicited ones.
    """

    progress_token: str | int | None = None
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

# A MESA policy refusal of every target an action had left after Phoenix's own
# scope checks. Unlike a capability denial, neither reconnecting nor
# get_capability_summary changes it, so it says so instead of sending the agent
# there. MESA only ever sees permitted targets, so naming it reveals nothing hidden.
MESA_DENIED_MESSAGE = (
    "Blocked by MESA policy for these targets. This is not a capability: reconnecting or "
    "get_capability_summary will not change it; only the operator's MESA policy can."
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

    Same in-band pattern as tool_dispatch._STALE_TOOLS_ADVISORY. Shallow-copies, so the
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
    review: bool = False,
) -> tuple[dict, str, str] | None:
    """Run capability gating. Returns a response tuple for deny or pending, None for allow.
    Deny gives (error_dict, "denied", tool_name); pending gives (pending_dict,
    "pending_approval", approval_resource). Pass ``diff`` as a zero-arg builder, as only the
    Confirm path reads it. ``review`` sends an Allow to a person too.
    """
    result = await async_evaluate_capability(
        cap_name, access_key, hass, data,
        tool_name=tool_name, args=args, request_id=request_id,
        client_ip=client_ip, diff=diff, target_entities=target_entities, review=review,
    )
    if result.is_deny:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool_name
    if result.is_pending:
        return await _pending_or_inline(hass, data, access_key, result.approval)
    return None


def _noting_reviewed_actions(diff: DiffSource, actions: list[str]) -> DiffSource:
    """A diff that tells the reviewer which actions sent this change to them."""
    if not actions:
        return diff

    async def build() -> dict:
        return {**await _resolve_diff(diff), "reviewed_actions": actions}
    return build


async def _await_inline_confirm(
    hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord, approval: Any,
) -> tuple[dict, str, str]:
    """Optionally hold the response open for a confirm gate to resolve inline.
    With ``confirm_inline_wait_seconds`` set, waits on phoenix_mcp_approval_resolved and
    returns the executed result, a rejection, or the normal pending reply on timeout. The
    request stays audited as "pending_approval" either way.
    """

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
        return (
            _tool_pending(approval, waited_seconds=wait, notified=_approval_notifies(data)),
            "pending_approval", _approval_resource(approval),
        )
    finally:
        unsub()
        _set_progress_status(None)

    latest = get_approval(data.store, approval_id)
    resource = _approval_resource(latest or approval)
    if latest is None or latest.status == STATUS_PENDING:
        # Event fired but the record is not terminal (a rare race): behave as if
        # it timed out, so the agent still gets a usable pending reply.
        return _tool_pending(approval, notified=_approval_notifies(data)), "pending_approval", resource
    if latest.status == STATUS_APPROVED and latest.result:
        tool_result = latest.result.get("tool_result")
        if tool_result is not None:
            return _operator_accepted_result(tool_result), "pending_approval", resource
    # Rejected, cancelled, or approved with no stored result: report the outcome
    # without re-queuing (the original action must never be retried).
    return _tool_inline_resolved(latest), "pending_approval", resource


def _with_mesa_advisory(result: tuple[dict, str, str], warnings: list[str]) -> tuple[dict, str, str]:
    """Add MESA's advisory warnings to a successful JSON tool result."""
    content, outcome, resource = result
    if not warnings or content.get("isError"):
        return result
    try:
        body = json.loads(content["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return result
    if not isinstance(body, dict):
        return result
    _add_mesa_advisory(body, warnings)
    return _tool_success(json.dumps(body, default=str)), outcome, resource


async def _pending_or_inline(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    approval: Any,
) -> tuple[dict, str, str]:
    """Return a pending approval, held inline when the key waits."""
    if access_key.confirm_inline_wait_seconds > 0 and not _access_key_has_other_pending(data, access_key, approval):
        return await _await_inline_confirm(hass, data, access_key, approval)
    return _tool_pending(approval, notified=_approval_notifies(data)), "pending_approval", _approval_resource(approval)


def _access_key_has_other_pending(data: PhoenixData, access_key: AccessKeyRecord, approval: Any) -> bool:
    """Return True when this key has a pending approval other than this one.

    The current one is already stored, so it is excluded. An unreadable store returns False,
    so the default is to wait.
    """

    try:
        pending = list_approvals(data.store, status=STATUS_PENDING, access_key_id=access_key.id)
    except Exception:  # advisory only; never fail a gate over this
        _LOGGER.debug("Could not read pending approvals for %s", access_key.id, exc_info=True)
        return False
    return any(entry.id != approval.id for entry in pending)


# Set by an admin restore (admin.changes) around a re-applied executor call, so the
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
    """Return a one-line description of what a version record changed, or None.

    Covers dashboards, raw yaml and file writes, and entity-registry edits, as a splat of
    summary, summary_key and summary_params. None where the alias tells the story.
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
        # The English sizes are the stored record; the byte counts travel too so
        # the panel can format them for the reader's locale.
        if a is not None and b is not None:
            return (
                _version_summary("size.was", size=_fmt_bytes(a), before=_fmt_bytes(b), size_bytes=a, before_bytes=b)
                if a != b else _version_summary("size", size=_fmt_bytes(a), size_bytes=a)
            )
        if a is not None:
            return _version_summary("size", size=_fmt_bytes(a), size_bytes=a)
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
    """Capture a config change into the version history, best-effort; never raises.
    Called from each _execute_*, so allowed and approved changes record once.
    ``approved_by_user_id`` is set only on the admin restore path. ``summary`` overrides the
    computed one-line description.
    """
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
    except Exception:  # history capture must never break a write
        _LOGGER.exception(
            "Failed to record %s version for %s %s", action, resource_type, resource_id
        )
        return
    # Let the admin panel's Changes tab refresh instantly instead of waiting for
    # its poll. Best-effort: without hass the panel falls back to polling.
    if data.hass is not None:
        data.hass.bus.async_fire(
            "phoenix_mcp_config_changed",
            {"resource_type": resource_type, "resource_id": resource_id, "action": action},
        )


def _version_content_payload(content: str | None, **extra: Any) -> dict | None:
    """Build a before/after payload for a raw-text version, such as a blueprint source.

    Content within MAX_DIFF_INLINE_BYTES is verbatim, larger is a non-restorable marker.
    Returns None on a fresh create.
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
    """Return a caller-supplied path, or None when it cannot name a file, as with a NUL."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    return value


def _read_text_capped(target: str) -> str:
    if os.path.getsize(target) > MAX_FILE_BYTES:
        raise ValueError("file too large")
    with open(target, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _cas_conflict(
    expected: Any, current: str | dict | list | None, tool_name: str
) -> tuple[dict, str, str] | None:
    """Optimistic-concurrency check for a Confirm-gated whole-blob write.
    With ``expected_hash``, refuses when the target changed since the read; runs before gating
    and in the executor, and never echoes the new hash. ``current`` hashes an absent target as
    its read tool reports it: "" for a raw file, None for a dashboard.
    """
    if not expected:
        return None
    current_hash = content_hash(current) if current is not None else None
    if current_hash == expected:
        return None
    # Every caller returns this before its write starts.
    return (
        _tool_refused_before_write(
            "This configuration changed since you last read it (expected_hash no "
            "longer matches). Re-read it and reapply your change."
        ),
        "invalid_request",
        tool_name,
    )


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
    """Walk ``path`` to the container holding its last segment.
    Returns (container, key, None), or (None, None, reason) when the path addresses nothing.
    Segments are dict keys or list indices; negative indices are refused, and nothing is
    created.
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
    """Bound a diff string stored on an approval record to the version store's cap."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... ({len(text) - max_chars} more characters)"


def _add_mesa_advisory(body: dict, warnings: list[str]) -> None:
    """Put MESA's advisory warnings on a tool result and flag its audit row."""
    body["mesa_advisory"] = warnings
    note_mesa_advisory(warnings)


def _mesa_confirm_annotation(
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    groups: list[tuple[str, str, list[str]]],
) -> dict | None:
    """Return the MESA context block for a capability-gate approval diff, or None.

    Approving a gated call also satisfies MESA's confirm, so the record says so, shaped like
    build_mesa_service_diff's block. None when nothing is MESA-confirm or on any failure.
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
    except Exception:  # annotation only; never block the gate diff
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

    async with data.store.async_lock:
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


# State projections shared by get_state and search_entities.


def _normalize_fields(fields: Any) -> list[str]:
    """Coerce the get_state `fields` arg to a list of field names.

    Accepts a list of strings or a single comma-separated string (a common agent
    mistake); anything else yields an empty list, which selects the lean default.
    """
    if isinstance(fields, str):
        return [f for f in (p.strip() for p in fields.split(",")) if f]
    if isinstance(fields, list):
        return [str(f).strip() for f in fields if str(f).strip()]
    return []


def _select_state_fields(scrubbed: dict, fields: list[str]) -> dict:
    """Return only the requested fields from an already-scrubbed state dict.

    Takes top-level keys, ``attr.<name>`` or ``attributes``. entity_id is always included and
    unknown fields are ignored.
    """
    attrs = scrubbed.get("attributes", {})
    out: dict = {"entity_id": scrubbed.get("entity_id")}
    picked_attrs: dict = {}
    for f in fields:
        if f == "attributes":
            picked_attrs.update(attrs)
        elif f.startswith("attr."):
            name = f[len("attr."):]
            if name in attrs:
                picked_attrs[name] = attrs[name]
        elif f in scrubbed and f != "attributes":
            out[f] = scrubbed[f]
    if picked_attrs:
        out["attributes"] = picked_attrs
    return out


def _lean_state(scrubbed: dict) -> dict:
    """Return the domain-aware lean view of an already-scrubbed state dict."""
    eid = scrubbed.get("entity_id", "") or ""
    domain = eid.split(".")[0] if "." in eid else ""
    attrs = scrubbed.get("attributes", {})
    keep = set(LEAN_ALWAYS_ATTRS) | set(DOMAIN_IMPORTANT_ATTRIBUTES.get(domain, ()))
    lean_attrs = {k: v for k, v in attrs.items() if k in keep}
    out: dict = {"entity_id": eid, "state": scrubbed.get("state")}
    if lean_attrs:
        out["attributes"] = lean_attrs
    return out


def _project_state(scrubbed: dict, fields: Any, detailed: bool) -> dict:
    """Apply presentation-only field projection to a scrubbed state dict.

    ``detailed`` gives the full dict, ``fields`` a subset, otherwise the domain-aware lean
    view.
    """
    if detailed:
        return scrubbed
    norm = _normalize_fields(fields)
    if norm:
        return _select_state_fields(scrubbed, norm)
    return _lean_state(scrubbed)
