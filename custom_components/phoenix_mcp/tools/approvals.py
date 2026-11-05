"""Report and wait on approval requests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from homeassistant.core import callback, HomeAssistant

from ..access_key_store import AccessKeyRecord
from ..approvals import STATUS_APPROVED, STATUS_PENDING, get_approval, list_approvals
from ..const import DOMAIN, MAX_APPROVAL_RESULT_CHARS, MAX_BATCH_APPROVALS, MAX_SUBSCRIPTION_SECONDS
from ..data import PhoenixData
from ..tool_common import (
    _approval_resource, _operator_accepted_result, _set_progress_status, _tool_error,
    _tool_success,
)
from ..tool_contracts import normalize_tool_args
from .system import _clamp_timeout


async def _tool_get_approval_status(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: poll one of the access key's own approvals, or list its pending ones.

    With no approval_id it lists this access key's outstanding approvals. A record
    owned by another access key answers exactly like a missing one ("Approval not
    found."), so a refusal is not an existence oracle.
    """

    await data.agent_recovery.reconcile(data.store, access_key.id)
    approval_id = args.get("approval_id")
    if approval_id is None:
        # No id: enumerate this access key's own pending approvals (own data only).
        pending = list_approvals(data.store, status=STATUS_PENDING, access_key_id=access_key.id)
        items = [
            {
                "approval_id": a.id,
                "status": a.status,
                "tool_name": a.tool_name,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            }
            for a in pending
        ]
        body = {"count": len(items), "pending_approvals": items}
        body["agent_recovery"] = data.agent_recovery.records(access_key.id)
        return _tool_success(json.dumps(body, default=str)), "allowed", "get_approval_status"

    if not isinstance(approval_id, str) or not approval_id:
        return _tool_error("Missing approval_id."), "invalid_request", "get_approval_status"
    record = get_approval(data.store, approval_id)
    if record is None or record.access_key_id != access_key.id:
        return _tool_error("Approval not found."), "not_found", "get_approval_status"
    return (_approval_status_result(record, resolved=record.status != STATUS_PENDING),
            "allowed", _approval_resource(record))


def _approval_status_result(record: Any, *, resolved: bool) -> dict:
    """The status body, plus the operator-accepted note when the action ran.

    The note tells an agent that the operator reviewed and accepted this exact
    change, so it stops proposing variations of it. Polls must carry it as well
    as the interactive paths (`_await_inline_confirm` and Agent Chat): an
    approval that outlived the inline window is exactly the case where the
    agent would otherwise keep revising an accepted change.

    Only an APPROVED record with a stored `tool_result` qualifies. An
    approved-but-execution-failed record transitions to failed with the
    `execution_failed` slug, and a plain rejection has no stored result; neither
    is something the operator accepted.

    Never double-appends: the inline path builds its own copy from the stored
    record and never writes the note back, so the record read here is always the
    unannotated one.
    """

    payload = _approval_status_payload(record, resolved=resolved)
    result = _tool_success(json.dumps(payload, default=str))
    if record.status == STATUS_APPROVED and (record.result or {}).get("tool_result") is not None:
        return _operator_accepted_result(result)
    return result


def _approval_status_payload(record: Any, *, resolved: bool) -> dict:
    """Status body shared by get_approval_status and wait_for_approval."""
    return {
        "approval_id": record.id,
        "status": record.status,
        "resolved": resolved,
        "tool_name": record.tool_name,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
        "result": record.result,
        "rejected_reason": record.rejected_reason,
    }


def _approval_batch_payload(record: Any, *, resolved: bool) -> dict:
    """One approval's status in the PLURAL form, with its result summarized.

    Built from `_approval_status_payload` and then narrowed, so the two forms
    share ONE definition of the status fields and cannot drift into answering
    the same question differently depending on how many ids were asked about.

    The `result` a single-id call returns is the executor's whole
    CallToolResult, which for an authoring tool is the config it just wrote.
    That suits one record, but a batch of them can exceed a caller's output
    limit and fail to report its own outcome. What survives is what a caller
    acts on: whether each one errored, and the text if it did, because an agent
    told only "rejected" with the executor's reason buried retries the same
    doomed call.
    """
    payload = _approval_status_payload(record, resolved=resolved)
    payload.pop("result", None)
    payload.update(_approval_result_digest(record))
    return payload


def _approval_result_digest(record: Any) -> dict:
    """Whether this approval's stored result was an error, and its text, bounded."""
    tool_result = (record.result or {}).get("tool_result")
    if not isinstance(tool_result, dict):
        return {}
    text = "\n".join(
        c.get("text", "") for c in tool_result.get("content", [])
        if isinstance(c, dict) and c.get("type") == "text"
    ).strip()
    digest: dict[str, Any] = {"result_is_error": bool(tool_result.get("isError"))}
    if text:
        digest["result_text"] = text[:MAX_APPROVAL_RESULT_CHARS]
        digest["result_truncated"] = len(text) > MAX_APPROVAL_RESULT_CHARS
    return digest


async def _wait_for_many_approvals(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Block until every named approval resolves, or the timeout expires.

    ONE listener for the whole set rather than a wait per id: the ids resolve in
    whatever order the operator works through them (and a batch approve resolves
    several inside one request), so waiting on them in sequence would block on
    #1 while #2 and #3 were already done.

    Returns a per-approval list plus the ids still outstanding, so a timeout is
    partial information rather than a failure: the caller learns exactly which
    ones landed and can wait again for the rest.
    """

    raw = args.get("approval_ids")
    if not isinstance(raw, list) or not raw or not all(isinstance(i, str) and i for i in raw):
        return (
            _tool_error("approval_ids must be a non-empty array of approval id strings."),
            "invalid_request", "wait_for_approval",
        )
    # Order-preserving dedupe: a repeated id would otherwise be reported twice and
    # inflate the outstanding count against itself.
    ids = list(dict.fromkeys(raw))
    if len(ids) > MAX_BATCH_APPROVALS:
        return (
            _tool_error(f"wait_for_approval accepts at most {MAX_BATCH_APPROVALS} approval_ids."),
            "invalid_request", "wait_for_approval",
        )

    records = {}
    unknown = []
    for approval_id in ids:
        record = get_approval(data.store, approval_id)
        # Unknown and belonging-to-another-access-key answer identically, so a refusal
        # is not an existence oracle for another access key's queue. Naming the
        # caller's own ids tells it which to drop.
        if record is None or record.access_key_id != access_key.id:
            unknown.append(approval_id)
        else:
            records[approval_id] = record
    if unknown:
        return _tool_error(
            "Approval not found: " + ", ".join(json.dumps(item[:80]) for item in unknown) + "."
        ), "not_found", "wait_for_approval"

    outstanding = {i for i, r in records.items() if r.status == STATUS_PENDING}
    if outstanding:
        timeout = _clamp_timeout(args.get("timeout", MAX_SUBSCRIPTION_SECONDS))
        future: asyncio.Future = hass.loop.create_future()

        @callback
        def _on_resolved(event: Any) -> None:
            outstanding.discard(event.data.get("approval_id"))
            if not outstanding and not future.done():
                future.set_result(True)

        unsub = hass.bus.async_listen(f"{DOMAIN}_approval_resolved", _on_resolved)
        _set_progress_status(
            f"Waiting for operator approval: {len(outstanding)} pending", total=float(timeout),
            key="agentchat.progress.waitingApprovals",
            params={"count": len(outstanding)},
        )
        try:
            await asyncio.wait_for(future, timeout)
        except TimeoutError:
            pass  # partial result below; the re-read decides what actually landed
        finally:
            unsub()
            _set_progress_status(None)

    # Re-read every record: some may have resolved without an event reaching
    # this listener (the expiry sweep), and a batch approve resolves several at once.
    latest = {i: (get_approval(data.store, i) or records[i]) for i in ids}
    still_pending = [i for i, r in latest.items() if r.status == STATUS_PENDING]
    payload = {
        "approvals": [
            _approval_batch_payload(r, resolved=r.status != STATUS_PENDING)
            for r in latest.values()
        ],
        "resolved": not still_pending,
        "pending": still_pending,
        "note": (
            "Each approval's result is summarized here so a whole batch fits in "
            "one reply. Call get_approval_status with a single approval_id for "
            "that one's full result."
        ),
    }
    result = _tool_success(json.dumps(payload, default=str))
    # The accepted-note applies as soon as ANY of them landed as an operator
    # approval, for the same reason it does on a single one: those changes are
    # final and must not be revised.
    if any(
        r.status == STATUS_APPROVED and (r.result or {}).get("tool_result") is not None
        for r in latest.values()
    ):
        result = _operator_accepted_result(result)
    return result, "allowed", f"approvals:{len(ids)}"


async def _tool_wait_for_approval(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: block until the access key's own approval(s) resolve, or until timeout.

    A bounded server-side wait (not a stream): returns immediately if already
    resolved, else waits on the phoenix_mcp_approval_resolved event filtered to
    the requested approval ids. Own-data only (another access key's id answers like a
    missing one, as in get_approval_status); no capability required.

    Accepts `approval_ids` (a list), including a one-item list for a single
    approval. Confirm-gated calls return pending_approval immediately by default
    (see DEFAULT_CONFIRM_INLINE_WAIT_SECONDS), so an agent making a run of writes
    holds N approval ids and needs ONE place to block until the operator has dealt
    with them, one at a time or in a batch. Waiting on each id in turn would
    serialise it back into the stall the staged model exists to remove.
    """
    args, error = normalize_tool_args("wait_for_approval", args)
    if error:
        return _tool_error(error), "invalid_request", "wait_for_approval"
    if "approval_ids" in args:
        return await _wait_for_many_approvals(args, access_key, hass, data)
    return _tool_error("approval_ids is required."), "invalid_request", "wait_for_approval"
