"""Admin approval views: review, approval, rejection and execution of held actions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from ..approvals import (
    REASON_ACCESS_KEY_INACTIVE, REASON_ADMIN_CANCELLED, REASON_CAPABILITY_DENIED,
    REASON_EXECUTION_INTERRUPTED, REASON_KILL_SWITCH, STATUS_APPROVED, STATUS_CANCELLED,
    STATUS_EXPIRED, STATUS_FAILED, async_clear_execution_marker, async_mark_execution_started,
    async_update_approval_status, create_approval_notification, dismiss_approval_notification,
    fire_approval_claim_event, fire_approval_resolved_event, get_approval, list_approvals,
)
from ..audit import Outcome
from ..capability import effective_cap
from ..config_reconciliation import (
    acknowledge_recovery, execute_recovery_approval, is_recovery_approval,
)
from ..config_recovery import ConfigurationRecoveryError, recovery_error_key, recovery_error_params
from ..const import DOMAIN, MAX_BATCH_APPROVALS, MESA_CONFIRM_CAP
from ..data import PhoenixData
from ..household_authority import household_scope
from ..request_authority import (
    AuthorityChangedError, RequestAuthority, check_request_authority, policy_version,
    request_authority,
)
from ..tool_common import RefusedBeforeWrite, UncertainOutcome, _tool_refused_before_write
from ..tool_dispatch import ExecutorNotRegistered, async_execute_approved_tool
from ..tools.restore import _restore_access_key
from ..view_base import PhoenixView, _err
from .common import _ok, _parse_pagination, _read_body, require_admin

_LOGGER = logging.getLogger(__name__)


class PhoenixAdminApprovalsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/approvals - list approvals, optionally filtered by status/access key."""

    url = "/api/phoenix-mcp/admin/approvals"
    name = "api:phoenix-mcp:admin:approvals"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        status = request.query.get("status")
        access_key_id = request.query.get("access_key_id")
        paged = _parse_pagination(request, rid, default_limit=50, max_limit=500)
        if isinstance(paged, web.Response):
            return paged
        limit, offset = paged
        records = list_approvals(data.store, status=status, access_key_id=access_key_id)
        total = len(records)
        records = records[offset:offset + limit]
        # in_progress rides the row rather than only the claim EVENT, so a panel
        # that was loaded (or reloaded) while an approve was already executing
        # still renders it as non-actionable. The event gives the instant update;
        # this is what makes a fresh page correct.
        return _ok({
            "approvals": [
                {**r.to_dict(), "in_progress": r.id in data.approvals_in_progress}
                for r in records
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }, request_id=rid)


class PhoenixAdminApprovalView(PhoenixView):
    """GET / DELETE /api/phoenix-mcp/admin/approvals/{approval_id}.

    DELETE is an alias for reject with reason 'admin_cancelled'.
    """

    url = "/api/phoenix-mcp/admin/approvals/{approval_id}"
    name = "api:phoenix-mcp:admin:approval"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, approval_id: str) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        record = get_approval(data.store, approval_id)
        if record is None:
            return _err("not_found", "Approval not found.", 404, rid, key="approvalNotFound")
        return _ok(record.to_dict(), request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, approval_id: str) -> web.Response:

        return await _resolve_approval(
            self.hass, request, approval_id,
            terminal_status="cancelled",
            auto_reason=REASON_ADMIN_CANCELLED,
        )


class PhoenixAdminApprovalApproveView(PhoenixView):
    """POST /api/phoenix-mcp/admin/approvals/{approval_id}/approve."""

    url = "/api/phoenix-mcp/admin/approvals/{approval_id}/approve"
    name = "api:phoenix-mcp:admin:approval_approve"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, approval_id: str) -> web.Response:
        return await _approve_approval(self.hass, request, approval_id)


class PhoenixAdminApprovalBatchApproveView(PhoenixView):
    """POST /api/phoenix-mcp/admin/approvals/batch/approve.

    Each id goes through ``_approve_approval``, equivalent to clicking Approve for each. Stops
    at the first failure, as HA writes are not transactional and likely failures are
    systematic; the rest stay pending and individually approvable.
    """

    url = "/api/phoenix-mcp/admin/approvals/batch/approve"
    name = "api:phoenix-mcp:admin:approval_batch_approve"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        ids = body.get("approval_ids") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            return _err(
                "invalid_request", "approval_ids must be a non-empty array of strings.", 400, rid,
                key="approvalIdsNonEmpty",
            )
        if len(ids) != len(set(ids)):
            # The second attempt at a duplicate would hit the in-progress claim
            # or the already-terminal branch and be reported as a failure,
            # halting the batch on the caller's own typo.
            return _err(
                "invalid_request", "approval_ids must not repeat an id.", 400, rid,
                key="approvalIdsUnique",
            )
        if len(ids) > MAX_BATCH_APPROVALS:
            return _err(
                "invalid_request",
                f"A batch may hold at most {MAX_BATCH_APPROVALS} approvals.",
                400, rid,
                key="approvalBatchMaximum", params={"max": MAX_BATCH_APPROVALS},
            )

        applied: list[dict] = []
        failure: dict | None = None
        for index, approval_id in enumerate(ids):
            response = await _approve_approval(self.hass, request, approval_id)
            try:
                parsed = json.loads(response.text or "{}")
            except ValueError:  # pragma: no cover - _ok/_err always emit JSON
                parsed = {}
            # A 200 is not sufficient: an executor that returned isError finalizes
            # the record as failed/execution_failed and still answers 200, and
            # treating that as applied would report a failed write as a success.
            if response.status == 200 and parsed.get("status") == "approved":
                applied.append({"approval_id": approval_id, "tool_name": parsed.get("tool_name")})
                continue
            failure = {
                "approval_id": approval_id,
                "tool_name": parsed.get("tool_name"),
                "status": response.status,
                "error": parsed.get("error") or parsed.get("closure_reason") or "failed",
                "message": parsed.get("message"),
            }
            if parsed.get("message_key"):
                failure["message_key"] = parsed["message_key"]
                if parsed.get("message_params"):
                    failure["message_params"] = parsed["message_params"]
            # Everything from here on is untouched and still pending.
            remaining = ids[index + 1:]
            break
        else:
            remaining = []

        data: PhoenixData = self.hass.data[DOMAIN]
        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method="approval/batch_approve",
            resource=f"approvals:{len(ids)}",
            outcome="allowed" if failure is None else "denied",
            client_ip="",
            settings=data.store.get_settings(),
            payload={
                "requested": len(ids),
                "applied": len(applied),
                "stopped_at": failure["approval_id"] if failure else None,
            },
        )
        return _ok(
            {"applied": applied, "failed": failure, "remaining": remaining},
            request_id=rid,
        )


class PhoenixAdminApprovalRejectView(PhoenixView):
    """POST /api/phoenix-mcp/admin/approvals/{approval_id}/reject."""

    url = "/api/phoenix-mcp/admin/approvals/{approval_id}/reject"
    name = "api:phoenix-mcp:admin:approval_reject"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, approval_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        reason = body.get("reason") if isinstance(body, dict) else None
        if reason is not None and not isinstance(reason, str):
            return _err(
                "invalid_request", "reason must be a string.", 400, rid,
                key="approvalReasonString",
            )
        return await _resolve_approval(
            self.hass, request, approval_id,
            terminal_status="rejected",
            auto_reason=reason,
        )


async def _resolve_approval(
    hass: HomeAssistant,
    request: web.Request,
    approval_id: str,
    *,
    terminal_status: str,
    auto_reason: str | None,
) -> web.Response:
    """Reject or cancel a pending approval. Idempotent on already-resolved records."""

    rid = request["phoenix_mcp_rid"]
    data: PhoenixData = hass.data[DOMAIN]
    user = request[KEY_HASS_USER]
    async with data.store.async_lock:
        record = get_approval(data.store, approval_id)
        if record is None:
            return _err("not_found", "Approval not found.", 404, rid, key="approvalNotFound")
        if record.is_terminal():
            return _ok(record.to_dict(), request_id=rid)
        if approval_id in data.approvals_in_progress:
            return _err(
                "conflict", "Approval is already being processed.", 409, rid,
                key="approvalProcessing",
            )
        if record.execution_started_at is not None:
            return _err(
                "conflict",
                "A previous attempt may already have applied. Check the result "
                "before changing or retrying this approval.",
                409, rid,
                key="approvalExecutionInterrupted",
            )
        # An overdue record has already timed out, as Approve and the sweep
        # treat it, so it closes as expired rather than as the operator's choice.
        if record.expires_at <= utcnow():
            terminal_status = STATUS_EXPIRED
            auto_reason = None
        if terminal_status == "rejected" and is_recovery_approval(record, data):
            try:
                await acknowledge_recovery(record, user.id, hass, data, rejected=True)
            except ConfigurationRecoveryError as exc:
                return _err("conflict", str(exc), 409, rid, key=recovery_error_key(str(exc)),
                            params=recovery_error_params(str(exc)))
        updated = await async_update_approval_status(
            data.store,
            approval_id,
            status=terminal_status,
            approved_by_user_id=None if terminal_status == STATUS_EXPIRED else user.id,
            closure_reason=auto_reason,
        )
    if updated is None:
        return _err("not_found", "Approval not found.", 404, rid, key="approvalNotFound")
    dismiss_approval_notification(hass, approval_id)
    fire_approval_resolved_event(hass, updated)
    data.audit.record(
        request_id=rid,
        access_key_id="admin",
        access_key_name=f"admin:{user.id}",
        method=f"approval/{terminal_status}",
        resource=f"approval:{updated.tool_name}:{approval_id}",
        outcome="denied",
        client_ip="",
        settings=data.store.get_settings(),
    )
    return _ok(updated.to_dict(), request_id=rid)


async def _approve_approval(hass: HomeAssistant, request: web.Request, approval_id: str) -> web.Response:
    """Claim, execute and finalize one pending approval.

    Validation and the durable execution marker run under ``store.async_lock``; the executor
    runs outside it and its outcome is written back under the lock.
    """

    rid = request["phoenix_mcp_rid"]
    data: PhoenixData = hass.data[DOMAIN]
    user = request[KEY_HASS_USER]
    closed_record = None
    closed_method: str | None = None
    closed_reason: str | None = None
    closed_response: web.Response | None = None
    resolved = False
    async with data.store.async_lock:
        if data.core_wipe_in_progress:
            return _err(
                "conflict", "Approval execution is paused while core data is being wiped.",
                409, rid, key="approvalWipeInProgress",
            )
        record = get_approval(data.store, approval_id)
        if record is None:
            return _err("not_found", "Approval not found.", 404, rid, key="approvalNotFound")
        settings = data.store.get_settings()
        recovery_request = is_recovery_approval(record, data)

        def _record_admin_lifecycle(
            method: str,
            *,
            outcome: Outcome,
            reason: str | None = None,
        ) -> None:
            data.audit.record(
                request_id=rid,
                access_key_id="admin",
                access_key_name=f"admin:{user.id}",
                method=method,
                resource=f"approval:{record.tool_name}:{approval_id}",
                outcome=outcome,
                client_ip="",
                settings=settings,
                payload={"reason": reason} if reason else None,
                ha_user_id=user.id,
            )

        if record.is_terminal():
            return _ok(record.to_dict(), request_id=rid)

        if approval_id in data.approvals_in_progress:
            return _err(
                "conflict", "Approval is already being processed.", 409, rid,
                key="approvalProcessing",
            )

        # Persist the claim under the lock before dispatch, and add the in-memory claim only
        # after it lands, so a stop or crash leaves a trace and an unwritable store fails
        # closed.
        #
        # A marker already on the record means an earlier attempt never reported back.
        # Refuse rather than overwrite that evidence; only recovery resolves these.
        if record.execution_started_at is not None:
            return _err(
                "conflict",
                "A previous attempt may already have applied. Check the result "
                "before changing or retrying this approval.",
                409, rid,
                key="approvalExecutionInterrupted",
            )

        if record.expires_at <= utcnow():
            closed_record = await async_update_approval_status(
                data.store,
                approval_id,
                status=STATUS_EXPIRED,
            )
            closed_method = "approval/expired"
            closed_response = _err(
                "conflict", "This request expired before it was approved, so nothing was run.", 409, rid,
                key="approvalExpired",
            )
        else:
            access_key = _restore_access_key(user.id) if recovery_request else data.store.get_access_key_by_id(record.access_key_id)
            if access_key is None or not access_key.is_valid():
                closed_record = await async_update_approval_status(
                    data.store, approval_id,
                    status=STATUS_CANCELLED,
                    approved_by_user_id=user.id,
                    closure_reason=REASON_ACCESS_KEY_INACTIVE,
                )
                closed_method = "approval/cancelled"
                closed_reason = REASON_ACCESS_KEY_INACTIVE
                closed_response = _err(
                    "not_found", "Access key no longer active.", 404, rid,
                    key="approvalAccessKeyInactive",
                )
            elif not recovery_request and record.cap_name != MESA_CONFIRM_CAP and effective_cap(access_key, record.cap_name) == "deny":
                # The MESA sentinel cap is re-evaluated by its executor instead.
                closed_record = await async_update_approval_status(
                    data.store, approval_id,
                    status=STATUS_FAILED,
                    approved_by_user_id=user.id,
                    closure_reason=REASON_CAPABILITY_DENIED,
                )
                closed_method = "approval/failed"
                closed_reason = REASON_CAPABILITY_DENIED
                closed_response = _err(
                    "forbidden", "Capability is now denied for this access key.", 403, rid,
                    key="approvalCapabilityDenied",
                )
            elif settings.kill_switch and not recovery_request:
                closed_record = await async_update_approval_status(
                    data.store, approval_id,
                    status=STATUS_CANCELLED,
                    approved_by_user_id=user.id,
                    closure_reason=REASON_KILL_SWITCH,
                )
                closed_method = "approval/cancelled"
                closed_reason = REASON_KILL_SWITCH
                closed_response = _err(
                    "service_unavailable", "Kill switch engaged.", 503, rid,
                    key="killSwitchEngaged",
                )
            elif not await async_mark_execution_started(
                data.store, approval_id, approved_by_user_id=user.id,
            ):
                return _err(
                    "service_unavailable",
                    "Could not record this approval before running it. Nothing was run; try again.",
                    503, rid,
                    key="approvalRecordFailed",
                )
            else:
                # Add the in-memory claim only after durable persistence succeeds.
                # Cancellation during the save leaves the marker as the backstop,
                # without stranding a process-local claim.
                data.approvals_in_progress.add(approval_id)

    if closed_response is not None:
        if closed_record is None:
            return _err("not_found", "Approval not found.", 404, rid, key="approvalNotFound")
        dismiss_approval_notification(hass, approval_id)
        fire_approval_resolved_event(hass, closed_record)
        _record_admin_lifecycle(
            closed_method or "approval/failed",
            outcome="denied",
            reason=closed_reason,
        )
        return closed_response
    if access_key is None:
        return _err(
            "not_found", "Access key no longer active.", 404, rid,
            key="approvalAccessKeyInactive",
        )

    # Persisting the execution marker above also persisted approved_at and the
    # approving admin. Mirror that durable decision into the audit ring BEFORE
    # dispatch, so the audit never says consent happened after the side effect.
    try:
        _record_admin_lifecycle("approval/approved", outcome="allowed")

    # Tell every surface the claim landed, BEFORE the execution rather than after
    # it: the resolved event cannot fire until the tool finishes, and until then
    # the panel, Agent Chat and the notification would all still offer Approve and
    # Reject on an approval that is already being acted on. Dismissing the
    # notification here is the same signal for the surface that has no UI state.
        fire_approval_claim_event(hass, approval_id, claimed=True)
        dismiss_approval_notification(hass, approval_id)
    except BaseException:
        # The durable marker still blocks a retry. Release only the local guard
        # because dispatch has not started.
        data.approvals_in_progress.discard(approval_id)
        raise

    # Execute outside the lock so the tool can use it freely. The claim is
    # released in the finally below; whether the approval is OFFERED AGAIN after a
    # failure depends on whether the executor could have applied anything, which
    # is the distinction the two excepts below draw.
    try:
        try:
            # Approved executors must retain the admitted access key and policy epoch
            # across their own waits, just like ordinary MCP dispatch.
            authority_ctx = request_authority.set(
                None if recovery_request else RequestAuthority(hass, data, access_key.id, access_key.access_key_hash)
            )
            policy_ctx = policy_version.set(access_key.settings_version)
            try:
                with data.conversations.approval_scope(approval_id):
                    if recovery_request:
                        tool_result, outcome, _resource = await execute_recovery_approval(record, user, hass, data)
                    else:
                        if record.household_request:

                            async with household_scope(hass, user_id=record.ha_user_id):
                                try:
                                    check_request_authority()
                                except AuthorityChangedError:
                                    # This check precedes the executor. A removed or
                                    # inactive requester cannot have caused effects.
                                    tool_result = _tool_refused_before_write(
                                        "Requester authority changed. No action was dispatched.")
                                    outcome, _resource = "denied", record.tool_name
                                else:
                                    tool_result, outcome, _resource = await async_execute_approved_tool(
                                        record.tool_name, record.args, access_key, hass, data,
                                    )
                        else:
                            tool_result, outcome, _resource = await async_execute_approved_tool(
                                record.tool_name, record.args, access_key, hass, data,
                            )
            finally:
                policy_version.reset(policy_ctx)
                request_authority.reset(authority_ctx)
        except ExecutorNotRegistered:
            # The registry lookup raises before any executor is called, so this
            # also proves nothing was dispatched. It has its own exception type
            # because catching bare KeyError would be ambiguous: an executor that
            # raises KeyError internally (a dict lookup on a service response, a
            # missing config key) is indistinguishable from the lookup failing,
            # and a side effect that HAD begun would be re-offered as retryable.
            await async_clear_execution_marker(data.store, approval_id)
            _record_admin_lifecycle(
                "approval/failed",
                outcome="denied",
                reason="executor_missing",
            )
            return _err(
                "invalid_request", "No executor registered for this tool.", 400, rid,
                key="approvalExecutorMissing",
            )
        except BaseException as exc:
            # BaseException, because a CancelledError would otherwise skip this handler and
            # re-offer a dispatched approval. Anything here may have raised after the side
            # effect applied, so the record resolves as recovery resolves an interrupted
            # one. If persisting that fails, the durable marker blocks a retry until the
            # sweep closes it.
            cancelled = isinstance(exc, asyncio.CancelledError)
            if cancelled:
                _LOGGER.warning(
                    "Approval %s was cancelled mid-execution; it may have applied",
                    approval_id,
                )
            else:
                _LOGGER.exception("Approval execution failed for %s", approval_id)
            # Best-effort during a cancellation: awaiting inside a cancelled task
            # can be interrupted again, and the marker left on disk is the
            # backstop either way (recovery reconciles it, and the marker check
            # above refuses any retry before then). So a failure to resolve here
            # degrades to "resolved at the next recovery sweep", never to "offered
            # again".
            try:
                async with data.store.async_lock:
                    interrupted = await async_update_approval_status(
                        data.store, approval_id,
                        status=STATUS_FAILED,
                        approved_by_user_id=user.id,
                        closure_reason=REASON_EXECUTION_INTERRUPTED,
                    )
                resolved = True
                if interrupted is not None:
                    fire_approval_resolved_event(hass, interrupted)
                _record_admin_lifecycle(
                    "approval/failed",
                    outcome="denied",
                    reason=REASON_EXECUTION_INTERRUPTED,
                )
            except BaseException:  # the marker is the backstop
                _LOGGER.exception(
                    "Could not resolve approval %s after a failed execution; the "
                    "recovery will close it as interrupted", approval_id,
                )
                # An ordinary failure counts as resolved, so the finally does not
                # re-offer it. Only for cancellation is `resolved` left False: the
                # finally then re-offers the still-pending record, but the marker
                # check refuses the retry, and re-raising skips the response anyway.
                resolved = not cancelled
            if cancelled:
                # Never swallow a cancellation: the event loop owns it, and
                # returning a response here would report a completed request for
                # a task Home Assistant is tearing down.
                raise
            return _err(
                "internal_error",
                "Execution failed and may have partly applied. This approval was "
                "closed rather than offered again; check the result before retrying.",
                500, rid,
                key="approvalExecutionUncertain",
            )

        is_error = bool(tool_result.get("isError"))
        saved_result: dict[str, Any] = {"tool_result": tool_result, "outcome": outcome}
        if isinstance(tool_result, RefusedBeforeWrite):
            # Persisted because the type does not survive storage. Server-owned:
            # executor text cannot set a key at this level.
            saved_result["refused_before_write"] = True
        final_status = STATUS_FAILED if is_error else STATUS_APPROVED
        auto_reason = "execution_failed" if is_error else None
        if isinstance(tool_result, UncertainOutcome):
            auto_reason = REASON_EXECUTION_INTERRUPTED
        audit_outcome: Outcome = "allowed" if final_status == STATUS_APPROVED else "denied"

        def _record_execution_audit(payload: dict | None = None) -> None:
            data.audit.record(
                request_id=rid,
                access_key_id=record.access_key_id,
                access_key_name=record.access_key_name,
                method="approval/failed" if is_error else "approval/executed",
                resource=f"approval:{record.tool_name}:{approval_id}",
                outcome=audit_outcome,
                client_ip="",
                settings=settings,
                payload=payload,
                ha_user_id=record.ha_user_id,
            )

        async with data.store.async_lock:
            updated = await async_update_approval_status(
                data.store, approval_id,
                status=final_status,
                approved_by_user_id=user.id,
                closure_reason=auto_reason,
                result=saved_result,
            )
        if updated is None:
            _record_execution_audit({
                "finalization": "missing_after_execution",
                "executor_outcome": outcome,
            })
            return _err("not_found", "Approval not found.", 404, rid, key="approvalNotFound")
        if updated.status != final_status:
            _record_execution_audit({
                "finalization": "conflict",
                "stored_status": updated.status,
                "executor_outcome": outcome,
            })
            return _err(
                "conflict", "Approval was already resolved.", 409, rid,
                key="approvalAlreadyResolved",
            )
        dismiss_approval_notification(hass, approval_id)
        resolved = True
        fire_approval_resolved_event(hass, updated)
        _record_execution_audit()
        return _ok(updated.to_dict(), request_id=rid)
    finally:
        data.approvals_in_progress.discard(approval_id)
        if not resolved:
            # A pre-dispatch failure can leave the record pending and genuinely
            # retryable. The surfaces were told to stop offering it, so they have
            # to be told it is back; leaving them quiet would hide a live approval
            # until the next reload. The notification is recreated for the same
            # reason.
            fire_approval_claim_event(hass, approval_id, claimed=False)
            still_pending = get_approval(data.store, approval_id)
            if still_pending is not None and not still_pending.is_terminal():
                create_approval_notification(hass, still_pending)
