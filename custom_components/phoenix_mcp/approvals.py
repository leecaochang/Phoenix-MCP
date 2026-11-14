"""Pending-approval records and lifecycle helpers for confirm-gated actions."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util.dt import utcnow

from .approval_targets import bind_targets, bound_preview, targets_bound
from .conversation_log import redact_device_codes, approval_event
from .catalog_text import notification_text
from .capability import effective_cap
from .redaction import redact_structure
from .const import (
    APPROVAL_DEFAULT_TTL_SECONDS, CONFIG_RECOVERY_APPROVAL_ACCESS_KEY, DOMAIN,
    MAX_PENDING_APPROVALS_PER_ACCESS_KEY, APPROVAL_HISTORY_LIMIT, APPROVAL_HISTORY_DAYS,
    APPROVAL_PAYLOAD_RETENTION_SECONDS, CAP_ALLOW, CAP_CONFIRM, CAP_DENY,
)
from .request_authority import household_authority, check_request_authority, request_authority
from .time_text import parse_datetime
from collections.abc import Awaitable, Callable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .access_key_store import AccessKeyRecord, AccessKeyStore
    from .audit import AuditLog
    from .data import PhoenixData

_LOGGER = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_FAILED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
})

REASON_ACCESS_KEY_INACTIVE = "access_key_inactive"
REASON_CAPABILITY_DENIED = "capability_denied"
REASON_KILL_SWITCH = "kill_switch"
REASON_ADMIN_CANCELLED = "admin_cancelled"
REASON_REVOKED = "access_key_revoked"
REASON_ACCESS_KEY_EXPIRED = "access_key_expired"
REASON_WIPED = "phoenix_mcp_data_wiped"
# The execution claim was persisted but Home Assistant stopped before its
# outcome was saved, so whether the side effect landed is UNKNOWN.
# See async_reconcile_interrupted_approvals for why that resolves as failed.
REASON_EXECUTION_INTERRUPTED = "execution_interrupted"
REASON_AGENT_CHAT_ENDED = "agent_chat_ended"
# Agent Chat waited AGENT_CHAT_APPROVAL_WAIT_SECONDS with the chat still open and
# nobody decided, so it withdrew the request and told the model.
REASON_AGENT_CHAT_WAIT_TIMED_OUT = "agent_chat_wait_timed_out"
# The integration was removed; every key was archived with its queue.
REASON_INTEGRATION_REMOVED = "integration_removed"
# A configuration recovery review whose arguments changed was replaced by a new one.
REASON_RECOVERY_REVIEW_REPLACED = "recovery_review_replaced"
# Home Assistant restarted while the request was still queued. Nothing ran, and
# the conversation that asked for it ended with the previous run.
REASON_RESTARTED = "phoenix_mcp_restarted"
# The key was rotated while the request waited: whoever held the old secret asked for it.
REASON_ACCESS_KEY_ROTATED = "access_key_rotated"
# The OAuth sign-in that queued the request was removed by an administrator.
REASON_OAUTH_SIGN_IN_REMOVED = "oauth_sign_in_removed"
# OAuth was switched off for the key, which ends every sign-in that queued a request.
REASON_OAUTH_TURNED_OFF = "oauth_turned_off"
# The queuing sign-in ended some other way (refresh-token reuse, for one), found at approve time.
REASON_OAUTH_SIGN_IN_ENDED = "oauth_sign_in_ended"
# There is deliberately no constant for target_out_of_scope / target_missing /
# rate_limited_at_execution. The approve path re-validates only the three things
# it owns (access key active, capability permits, kill switch off); target scope and
# existence are re-checked inside each _execute_X, whose failure surfaces as
# execution_failed with the executor's own message, and an admin approving is
# not rate limited. The panel keeps label-map entries for those three slugs so an
# older stored record still renders, which is why the translation catalogs list
# more reasons than this module emits.


class PendingApprovalCapacityError(Exception):
    """Raised when an access key already holds MAX_PENDING_APPROVALS_PER_ACCESS_KEY entries."""


@dataclass
class PendingApproval:
    """One queued approval request awaiting admin decision."""

    id: str
    access_key_id: str
    access_key_name: str
    tool_name: str
    cap_name: str
    args: dict
    diff: dict
    status: str
    created_at: datetime
    expires_at: datetime
    request_id: str
    client_ip: str | None = None
    resolved_at: datetime | None = None
    approved_by_user_id: str | None = None
    approved_at: datetime | None = None
    closure_reason: str | None = None
    result: Any = None
    # Set on disk immediately BEFORE the executor runs and cleared when its
    # outcome is persisted. It is the only durable trace that a side effect may
    # already have been applied: the in-memory claim in data.approvals_in_progress
    # covers concurrent requests in this process but dies with it, so without
    # this marker a crash mid-execution would leave a pending record an admin
    # could approve again and apply twice.
    execution_started_at: datetime | None = None
    household_request: bool = False
    ha_user_id: str | None = None
    # The OAuth sign-in that queued the request and the app label its audit rows carry.
    # None for a raw key, a household surface, or a record saved before these existed.
    sign_in_id: str | None = None
    oauth_app: dict[str, str] | None = None
    # credential_generation() of the key when queued; None matches any generation.
    credential_generation: str | None = None

    def to_dict(self, redact_args: bool = True) -> dict:
        """Serialize the approval, redacting args unless ``redact_args`` is False.

        The persistence path alone keeps raw args, the sign-in id and the credential
        generation, for the approved-action executor.
        """
        stored = {} if redact_args else {
            "sign_in_id": self.sign_in_id,
            "credential_generation": self.credential_generation,
        }
        return {
            **stored,
            "oauth_app": dict(self.oauth_app) if self.oauth_app else None,
            "id": self.id,
            "access_key_id": self.access_key_id,
            "access_key_name": self.access_key_name,
            "tool_name": self.tool_name,
            "cap_name": self.cap_name,
            "args": self._public_args() if redact_args else self.args,
            "diff": self.diff,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "approved_by_user_id": self.approved_by_user_id,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "closure_reason": self.closure_reason,
            "result": self.result,
            "request_id": self.request_id,
            "client_ip": self.client_ip,
            "household_request": self.household_request,
            "ha_user_id": self.ha_user_id,
            "execution_started_at": (
                self.execution_started_at.isoformat() if self.execution_started_at else None
            ),
        }

    def _public_args(self) -> Any:
        """The arguments as an admin may see them."""
        # Device codes too (a disarm code, a PIN), which the general redaction keeps.
        public_args = redact_device_codes(redact_structure(self.args))
        if isinstance(public_args, dict):
            # Leading-underscore arguments are executor-only approval bindings
            # (MESA/context/private identity hashes), not operator input. Keep
            # them durably for replay but never project them through the admin
            # API or Details view.
            public_args = {
                key: "<redacted>" if isinstance(key, str) and key.startswith("_") else value
                for key, value in public_args.items()
            }
        return public_args

    @classmethod
    def from_dict(cls, data: dict) -> PendingApproval:
        return cls(
            id=data["id"],
            access_key_id=data["access_key_id"],
            access_key_name=data.get("access_key_name", ""),
            tool_name=data["tool_name"],
            cap_name=data.get("cap_name", ""),
            args=data.get("args", {}),
            diff=data.get("diff", {}),
            status=data.get("status", STATUS_PENDING),
            created_at=parse_datetime(data["created_at"]) or utcnow(),
            expires_at=parse_datetime(data["expires_at"]) or utcnow(),
            resolved_at=parse_datetime(data["resolved_at"]) if data.get("resolved_at") else None,
            approved_by_user_id=data.get("approved_by_user_id"),
            approved_at=(
                parse_datetime(data["approved_at"])
                if data.get("approved_at") else None
            ),
            closure_reason=data.get("closure_reason"),
            result=data.get("result"),
            request_id=data.get("request_id", ""),
            client_ip=data.get("client_ip"),
            household_request=data.get("household_request", data.get("client_ip") in {"assist", "voice", "voice_agent", "ai_task"}),
            ha_user_id=data.get("ha_user_id"),
            execution_started_at=(
                parse_datetime(data["execution_started_at"])
                if data.get("execution_started_at") else None
            ),
            sign_in_id=data.get("sign_in_id") if isinstance(data.get("sign_in_id"), str) else None,
            oauth_app=_stored_oauth_app(data.get("oauth_app")),
            credential_generation=(
                data.get("credential_generation")
                if isinstance(data.get("credential_generation"), str) else None
            ),
        )

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def _stored_oauth_app(value: Any) -> dict[str, str] | None:
    """A stored app label as {"name", "host"} strings, else None."""
    if not isinstance(value, dict):
        return None
    name, host = value.get("name"), value.get("host")
    if not isinstance(name, str) or not isinstance(host, str) or not host:
        return None
    return {"name": name, "host": host}


def credential_generation(access_key: AccessKeyRecord) -> str:
    """A short fingerprint of the key's current secret; only rotation changes it."""
    return hashlib.sha256(f"phoenix-approval:{access_key.access_key_hash}".encode()).hexdigest()[:16]


def _queuing_sign_in(access_key_id: str) -> tuple[str | None, dict[str, str] | None]:
    """The OAuth sign-in this request was admitted with, and its app label, when it is this key's."""
    authority = request_authority.get()
    if authority is None or authority.oauth_grant_id is None or authority.access_key_id != access_key_id:
        return None, None
    grant = authority.data.oauth_grants.get(authority.oauth_grant_id)
    return authority.oauth_grant_id, (None if grant is None else grant.audit_label())


def _new_approval_id() -> str:
    return f"appr_{uuid.uuid4().hex[:16]}"


async def async_create_pending_approval(
    store: AccessKeyStore,
    *,
    access_key_id: str,
    access_key_name: str,
    tool_name: str,
    cap_name: str,
    args: dict,
    diff: dict,
    request_id: str,
    client_ip: str | None = None,
    ttl_seconds: int = APPROVAL_DEFAULT_TTL_SECONDS,
    access_key: AccessKeyRecord | None = None,
) -> PendingApproval:
    """Add a new pending approval to storage and return the record.

    A key's request passes ``access_key``, whose secret the approval is bound to; the OAuth
    sign-in comes from the request's authority. Caller must hold store.async_lock if creating
    from a multi-step path. Raises PendingApprovalCapacityError at the per-access-key cap.
    """
    raw = store.get_pending_approvals()
    pending_for_access_key = sum(
        1 for entry in raw
        if entry.get("access_key_id") == access_key_id and entry.get("status") == STATUS_PENDING
    )
    if pending_for_access_key >= MAX_PENDING_APPROVALS_PER_ACCESS_KEY:
        raise PendingApprovalCapacityError(
            f"access key {access_key_id} already has {pending_for_access_key} pending approvals"
        )

    household = household_authority.get()
    sign_in_id, oauth_app = _queuing_sign_in(access_key_id)
    now = utcnow()
    approval = PendingApproval(
        id=_new_approval_id(),
        access_key_id=access_key_id,
        access_key_name=access_key_name,
        tool_name=tool_name,
        cap_name=cap_name,
        args=args,
        # The diff is for review only (the executor re-runs args), so device codes
        # never need to be kept in it, shown, or written to disk.
        diff=redact_device_codes(diff),
        status=STATUS_PENDING,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        request_id=request_id,
        client_ip=client_ip,
        household_request=household is not None,
        ha_user_id=household.user_id if household else None,
        sign_in_id=sign_in_id,
        oauth_app=oauth_app,
        # A household request was made by a Home Assistant user, not with the key's secret.
        credential_generation=None if access_key is None or household is not None else credential_generation(access_key),
    )
    raw.append(approval.to_dict(redact_args=False))
    store.set_pending_approvals(raw)
    await store.async_save()
    return approval


def get_approval(store: AccessKeyStore, approval_id: str) -> PendingApproval | None:
    """Return the approval, or None if it is missing or its stored record is corrupt."""
    for entry in store.get_pending_approvals():
        if entry.get("id") == approval_id:
            try:
                return PendingApproval.from_dict(entry)
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("Skipping corrupt approval record %r: %s", approval_id, exc)
                return None
    return None


def list_approvals(
    store: AccessKeyStore,
    *,
    status: str | None = None,
    access_key_id: str | None = None,
) -> list[PendingApproval]:
    """Return approvals matching optional filters, newest first."""
    out: list[PendingApproval] = []
    for entry in store.get_pending_approvals():
        if status is not None and entry.get("status") != status:
            continue
        if access_key_id is not None and entry.get("access_key_id") != access_key_id:
            continue
        try:
            out.append(PendingApproval.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda a: a.created_at, reverse=True)
    return out


async def async_update_approval_status(
    store: AccessKeyStore,
    approval_id: str,
    *,
    status: str,
    approved_by_user_id: str | None = None,
    closure_reason: str | None = None,
    result: Any = None,
) -> PendingApproval | None:
    """Transition a pending approval to a terminal state and persist.

    Returns the updated record, the unchanged record if it is no longer pending, or
    None if the approval is missing. Caller must hold store.async_lock.
    """
    raw = store.get_pending_approvals()
    for entry in raw:
        if entry.get("id") != approval_id:
            continue
        if entry.get("status") != STATUS_PENDING:
            return PendingApproval.from_dict(entry)
        entry["status"] = status
        entry["resolved_at"] = utcnow().isoformat()
        if approved_by_user_id is not None:
            entry["approved_by_user_id"] = approved_by_user_id
        if closure_reason is not None:
            entry["closure_reason"] = closure_reason
        if result is not None:
            entry["result"] = result
        # The record is terminal, so the "may be mid-execution" marker has done
        # its job and must not survive into the stored history.
        entry.pop("execution_started_at", None)
        store.set_pending_approvals(raw)
        await store.async_save()
        return PendingApproval.from_dict(entry)
    return None


async def async_mark_execution_started(
    store: AccessKeyStore,
    approval_id: str,
    *,
    approved_by_user_id: str,
) -> bool:
    """Record on disk that the admin approved and execution is about to run.
    Returns True when durable; False must stop the caller, as a store that cannot take this
    cannot record the outcome either. Recovery reads the marker, so a crash cannot leave a
    side effect re-approvable. Caller holds store.async_lock.
    """
    raw = store.get_pending_approvals()
    for entry in raw:
        if entry.get("id") != approval_id:
            continue
        if entry.get("status") != STATUS_PENDING:
            return False
        approved_at = utcnow().isoformat()
        entry["approved_by_user_id"] = approved_by_user_id
        entry["approved_at"] = approved_at
        entry["execution_started_at"] = approved_at
        store.set_pending_approvals(raw)
        try:
            await store.async_save()
        except Exception:  # see the docstring: this must fail closed
            _LOGGER.exception(
                "Could not record the start of execution for approval %s; refusing to "
                "run it, because a store that cannot take the marker cannot record the "
                "outcome either and the action would be re-approvable after it ran",
                approval_id,
            )
            entry.pop("execution_started_at", None)
            entry.pop("approved_at", None)
            entry.pop("approved_by_user_id", None)
            store.set_pending_approvals(raw)
            return False
        return True
    return False


async def async_clear_execution_marker(
    store: AccessKeyStore,
    approval_id: str,
) -> None:
    """Undo async_mark_execution_started for an attempt that provably never ran.

    Only for failures before the executor could apply anything, such as an unregistered tool.
    Takes the store lock itself, so callers must not hold it.
    """
    async with store.async_lock:
        raw = store.get_pending_approvals()
        for entry in raw:
            if entry.get("id") != approval_id:
                continue
            if not entry.pop("execution_started_at", None):
                return
            entry.pop("approved_at", None)
            entry.pop("approved_by_user_id", None)
            store.set_pending_approvals(raw)
            try:
                await store.async_save()
            except Exception:  # the record is still pending either way
                _LOGGER.exception(
                    "Could not clear the execution marker for approval %s; recovery "
                    "will resolve it as interrupted", approval_id,
                )
            return


async def async_reconcile_interrupted_approvals(
    store: AccessKeyStore,
    *,
    skip_ids: set[str] | frozenset[str] | None = None,
) -> list[PendingApproval]:
    """Resolve to FAILED approvals whose execution claim has no recorded outcome.

    Skips active executions by ID. Staying pending would offer a second apply. Returns the
    changed records for the caller to announce. Caller holds store.async_lock.
    """
    skip_ids = skip_ids or frozenset()
    raw = store.get_pending_approvals()
    changed: list[PendingApproval] = []
    for entry in raw:
        if entry.get("status") != STATUS_PENDING:
            continue
        if entry.get("id") in skip_ids:
            continue
        if not entry.get("execution_started_at"):
            continue
        entry["status"] = STATUS_FAILED
        entry["resolved_at"] = utcnow().isoformat()
        entry["closure_reason"] = REASON_EXECUTION_INTERRUPTED
        entry.pop("execution_started_at", None)
        try:
            changed.append(PendingApproval.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    if changed:
        store.set_pending_approvals(raw)
        await store.async_save()
        _LOGGER.warning(
            "Phoenix MCP: %d approval(s) started running and never reported an "
            "outcome; whether they applied is unknown, so they were resolved as "
            "failed rather than left approvable again", len(changed),
        )
    return changed


async def async_cancel_approvals_for_access_key(
    store: AccessKeyStore,
    access_key_id: str,
    reason: str,
    *,
    skip_ids: set[str] | frozenset[str] | None = None,
    where: Callable[[dict], bool] | None = None,
) -> list[PendingApproval]:
    """Close a key's pending approvals (those ``where`` accepts); return the changed records.

    ``skip_ids`` protects a mid-execution approval; a stale marker resolves as failed. Caller
    holds async_lock, then dismisses, fires phoenix_mcp_approval_resolved and audits outside it.
    """
    skip_ids = skip_ids or frozenset()
    raw = store.get_pending_approvals()
    changed: list[PendingApproval] = []
    now_iso = utcnow().isoformat()
    for entry in raw:
        if entry.get("access_key_id") != access_key_id:
            continue
        if entry.get("status") != STATUS_PENDING:
            continue
        if entry.get("id") in skip_ids:
            continue
        if where is not None and not where(entry):
            continue
        interrupted = bool(entry.get("execution_started_at"))
        entry["status"] = STATUS_FAILED if interrupted else STATUS_CANCELLED
        entry["resolved_at"] = now_iso
        entry["closure_reason"] = REASON_EXECUTION_INTERRUPTED if interrupted else reason
        entry.pop("execution_started_at", None)
        try:
            changed.append(PendingApproval.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    if changed:
        store.set_pending_approvals(raw)
        await store.async_save()
    return changed


async def async_cancel_sign_in_approvals(
    hass: HomeAssistant | None, data: PhoenixData, access_key_id: str, reason: str,
    *, sign_in_id: str | None = None,
) -> list[PendingApproval]:
    """Cancel the key's pending approvals queued by ``sign_in_id``, or by any sign-in no longer live.

    Raw-key and household requests stay. Takes the store lock, so callers must not hold it,
    then dismisses, announces and audits each closed request.
    """
    def queued_by_ended_sign_in(entry: dict) -> bool:
        queued_by = entry.get("sign_in_id")
        if not isinstance(queued_by, str):
            return False
        if sign_in_id is not None:
            return queued_by == sign_in_id
        return not data.oauth_grants.is_live(queued_by, data.store.get_access_key_by_id(access_key_id))

    async with data.store.async_lock:
        changed = await async_cancel_approvals_for_access_key(
            data.store, access_key_id, reason,
            skip_ids=data.approvals_in_progress, where=queued_by_ended_sign_in,
        )
    if hass is not None:
        for approval in changed:
            dismiss_approval_notification(hass, approval.id)
            fire_approval_resolved_event(hass, approval)
    audit_closed_approvals(data.audit, data.store.get_settings(), changed)
    return changed


async def async_cancel_queued_approvals_at_startup(
    store: AccessKeyStore,
) -> list[PendingApproval]:
    """Cancel every approval still queued when Home Assistant starts.
    Runs after interrupted records are failed, so these never ran, and their requester is
    gone. Records stay with a reason. Caller holds store.async_lock, then dismisses and
    announces outside it.
    """
    raw = store.get_pending_approvals()
    changed: list[PendingApproval] = []
    now_iso = utcnow().isoformat()
    for entry in raw:
        if entry.get("status") != STATUS_PENDING:
            continue
        entry["status"] = STATUS_CANCELLED
        entry["resolved_at"] = now_iso
        entry["closure_reason"] = REASON_RESTARTED
        entry.pop("execution_started_at", None)
        try:
            changed.append(PendingApproval.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    if changed:
        store.set_pending_approvals(raw)
        await store.async_save()
    return changed


def collect_pending_approvals_for_wipe(
    store: AccessKeyStore, reason: str,
) -> list[PendingApproval]:
    """Return the pending approvals as cancelled records without touching the store.

    The wipe clears the queue, and these let the caller dismiss and announce afterward. Caller
    holds async_lock and calls this before async_wipe.
    """
    now_iso = utcnow().isoformat()
    out: list[PendingApproval] = []
    for entry in store.get_pending_approvals():
        if entry.get("status") != STATUS_PENDING:
            continue
        record = dict(entry)
        record["status"] = STATUS_CANCELLED
        record["resolved_at"] = now_iso
        record["closure_reason"] = reason
        try:
            out.append(PendingApproval.from_dict(record))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def async_expire_overdue_approval_records(
    store: AccessKeyStore,
    *,
    skip_ids: set[str] | frozenset[str] | None = None,
) -> list[PendingApproval]:
    """Move overdue pending approvals to status=expired and return changed records.

    Caller must hold async_lock.
    """
    skip_ids = skip_ids or frozenset()
    raw = store.get_pending_approvals()
    now = utcnow()
    now_iso = now.isoformat()
    expired: list[PendingApproval] = []
    for entry in raw:
        if entry.get("status") != STATUS_PENDING:
            continue
        if entry.get("id") in skip_ids:
            continue
        try:
            expires = parse_datetime(entry.get("expires_at", ""))
        except (TypeError, ValueError):
            expires = None
        if expires is None or expires > now:
            continue
        entry["status"] = STATUS_EXPIRED
        entry["resolved_at"] = now_iso
        try:
            expired.append(PendingApproval.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    if expired:
        store.set_pending_approvals(raw)
        await store.async_save()
    return expired


def audit_closed_approvals(
    audit: AuditLog | None, settings: Any, approvals: list[PendingApproval],
) -> None:
    """Write the approval/<status> row of each request closed without an administrator's decision.

    Each row carries the queuing request's ID, key, household user and app label, with the
    approval ID and reason in the payload, so it links to the request with entity names off.
    """
    if audit is None:
        return
    for approval in approvals:
        audit.record(
            request_id=approval.request_id or "",
            access_key_id=approval.access_key_id,
            access_key_name=approval.access_key_name,
            method=f"approval/{approval.status}",
            resource=f"approval:{approval.tool_name}:{approval.id}",
            outcome="denied",
            client_ip="",
            settings=settings,
            payload={"approval_id": approval.id, "reason": approval.closure_reason},
            ha_user_id=approval.ha_user_id,
            oauth_app=approval.oauth_app,
        )


def fire_approval_requested_event(hass: HomeAssistant, approval: PendingApproval) -> None:
    """Fire an HA event when a new approval is queued."""
    approval_event(hass, approval)
    hass.bus.async_fire(
        f"{DOMAIN}_approval_requested",
        {
            "approval_id": approval.id,
            "access_key_id": approval.access_key_id,
            "access_key_name": approval.access_key_name,
            "tool_name": approval.tool_name,
            "cap_name": approval.cap_name,
            "review_url": f"/phoenix-mcp/approvals/{approval.id}",
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            "timestamp": utcnow().isoformat(),
        },
    )


def fire_approval_resolved_event(hass: HomeAssistant, approval: PendingApproval) -> None:
    """Fire an HA event when an approval reaches a terminal state."""
    approval_event(hass, approval, resolved=True)
    hass.bus.async_fire(
        f"{DOMAIN}_approval_resolved",
        {
            "approval_id": approval.id,
            "access_key_id": approval.access_key_id,
            "access_key_name": approval.access_key_name,
            "tool_name": approval.tool_name,
            "status": approval.status,
            "closure_reason": approval.closure_reason,
            "approved_by_user_id": approval.approved_by_user_id,
            "timestamp": utcnow().isoformat(),
        },
    )


def fire_approval_claim_event(hass: HomeAssistant, approval_id: str, *, claimed: bool) -> None:
    """Fire an HA event when an approval is claimed for execution, or released.

    Lets surfaces go non-actionable before the inline run finishes.
    """
    hass.bus.async_fire(
        f"{DOMAIN}_approval_claimed",
        {
            "approval_id": approval_id,
            "claimed": claimed,
            "timestamp": utcnow().isoformat(),
        },
    )


def notification_id_for_approval(approval_id: str) -> str:
    return f"{DOMAIN}_approval_{approval_id}"


def create_approval_notification(hass: HomeAssistant, approval: PendingApproval) -> None:
    """Fire an HA persistent notification for a new pending approval.

    Suppressed when the admin has turned off approval notifications
    (settings.notify_on_approval). The in-panel Approvals badge still updates.
    """
    data = hass.data.get(DOMAIN)
    if data is not None and not data.store.get_settings().notify_on_approval:
        return

    # Local: optional component, imported only when this path runs.
    from homeassistant.components import persistent_notification

    # Configuration recovery is Phoenix's own review, not an access key's request.
    recovery = approval.access_key_id == CONFIG_RECOVERY_APPROVAL_ACCESS_KEY
    persistent_notification.async_create(
        hass,
        message=notification_text(
            hass, "approval.recovery_message" if recovery else "approval.message",
            access_key=approval.access_key_name,
            url=f"/phoenix-mcp/approvals/{approval.id}",
        ),
        title=notification_text(hass, "approval.title"),
        notification_id=notification_id_for_approval(approval.id),
    )


def dismiss_approval_notification(hass: HomeAssistant, approval_id: str) -> None:
    """Dismiss an approval's notification. A failure is logged, as a notice decides no approval."""
    # Local: optional component, imported only when this path runs.
    from homeassistant.components import persistent_notification

    try:
        persistent_notification.async_dismiss(
            hass,
            notification_id=notification_id_for_approval(approval_id),
        )
    except Exception:
        _LOGGER.warning("Could not dismiss the notification of approval %s", approval_id, exc_info=True)


async def async_prune_terminal_approvals(store: AccessKeyStore, *, skip_ids: set[str] | None = None) -> None:
    """Bound resolved history and retire raw payloads; caller holds the store lock."""
    now = utcnow()
    protected, resolved = [], []
    original = store.get_pending_approvals()
    skipped = skip_ids or set()
    for row in original:
        if (row.get("status") not in TERMINAL_STATUSES or row.get("execution_started_at")
                or row.get("id") in skipped):
            protected.append(row)
            continue
        try:
            at = parse_datetime(row.get("resolved_at") or row.get("created_at") or "")
        except (TypeError, ValueError):
            at = None
        if row.get("closure_reason") == REASON_EXECUTION_INTERRUPTED:
            # Kept as the record of an action whose outcome nobody saw, but its raw
            # arguments only for the history window: the operator has that long to
            # check the result, and nothing reads them afterwards.
            if (at is not None and at.tzinfo is not None and now - at > timedelta(days=APPROVAL_HISTORY_DAYS)
                    and (row.get("args") or row.get("diff") or row.get("result") is not None)):
                row = {**row, "args": {}, "diff": {}, "result": None}
            protected.append(row)
            continue
        if at is None or at.tzinfo is None:
            protected.append(row)
            continue
        if now - at > timedelta(days=APPROVAL_HISTORY_DAYS):
            continue
        clean = dict(row)
        if (now - at).total_seconds() >= APPROVAL_PAYLOAD_RETENTION_SECONDS:
            clean.update(args={}, diff={}, result=None)
        resolved.append((at, clean))
    resolved.sort(key=lambda pair: pair[0], reverse=True)
    retained = protected + [row for _, row in resolved[:APPROVAL_HISTORY_LIMIT]]
    if retained != original:
        store.set_pending_approvals(retained)
        try:
            await store.async_save()
        except BaseException:
            store.set_pending_approvals(original)
            raise


@dataclass
class CapabilityResult:
    """Outcome of an async_evaluate_capability call.

    mode is one of "allow" / "deny" / "confirm". When mode is "confirm",
    approval is the freshly created PendingApproval record and the caller
    must return a pending response without executing.
    """

    mode: str
    approval: Any | None = None

    @property
    def is_deny(self) -> bool:
        return self.mode == CAP_DENY

    @property
    def is_pending(self) -> bool:
        return self.mode == CAP_CONFIRM


# An approval diff, either already built or deferred behind a zero-arg builder.
# Most builders read config off disk (or call the ESPHome add-on) while the diff
# is read only when an approval is created, so deferring spares an allow or a deny
# a result nobody looks at.
DiffSource = dict | Callable[[], "dict | Awaitable[dict]"] | None


async def _resolve_diff(diff: DiffSource) -> dict:
    """Resolve a diff given as a built dict or a deferred sync or async builder.

    Builders run here, on the confirm path alone.
    """
    if diff is None:
        return {}
    if not callable(diff):
        return diff or {}
    built = diff()
    if isinstance(built, Awaitable):
        built = await built
    return built or {}


async def async_evaluate_capability(
    cap_name: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    *,
    tool_name: str,
    args: dict,
    request_id: str,
    diff: DiffSource = None,
    client_ip: str | None = None,
    target_entities: list[str] | None = None,
    review: bool = False,
) -> CapabilityResult:
    """Resolve a capability check into Allow, Deny or Pending(approval).

    Reads the effective cap mode after pass-through interaction. ``diff`` is a dict or a
    zero-arg builder, invoked only on the Confirm path, for the admin review UI.
    """

    mode = effective_cap(access_key, cap_name)
    if mode == CAP_ALLOW and not review:
        return CapabilityResult(CAP_ALLOW)
    if mode == CAP_DENY:
        return CapabilityResult(CAP_DENY)
    # The diff is needed only on the confirm path, so a deferred builder runs here
    # and nowhere else. Both early returns above leave it untouched.
    if target_entities is not None:
        args = bind_targets(args, target_entities, hass)
        if not targets_bound(args):
            return CapabilityResult(CAP_DENY)
    resolved_diff = await _resolve_diff(diff)
    if target_entities is not None:
        resolved_diff = bound_preview(resolved_diff, args)
    async with data.store.async_lock:
        check_request_authority()
        approval = await async_create_pending_approval(
            data.store,
            access_key_id=access_key.id,
            access_key_name=access_key.name,
            tool_name=tool_name,
            cap_name=cap_name,
            args=args,
            diff=resolved_diff,
            request_id=request_id,
            client_ip=client_ip,
            access_key=access_key,
        )
    create_approval_notification(hass, approval)
    fire_approval_requested_event(hass, approval)
    return CapabilityResult(CAP_CONFIRM, approval=approval)
