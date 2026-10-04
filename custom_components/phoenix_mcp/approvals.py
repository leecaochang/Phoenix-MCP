"""Pending-approval records and lifecycle helpers for confirm-gated actions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util.dt import parse_datetime, utcnow

from .helpers import notification_text
from .const import (
    APPROVAL_DEFAULT_TTL_SECONDS,
    DOMAIN,
    MAX_PENDING_APPROVALS_PER_ACCESS_KEY, APPROVAL_HISTORY_LIMIT, APPROVAL_HISTORY_DAYS, APPROVAL_PAYLOAD_RETENTION_SECONDS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .access_key_store import AccessKeyStore

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
    rejected_reason: str | None = None
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

    def to_dict(self, redact_args: bool = True) -> dict:
        """Serialise the approval.

        Args are redacted by default so admin-API responses never echo secret
        bearing content (write_file / set_yaml_config bodies, credentials); the
        review surface is the already-redacted diff. The persistence path passes
        redact_args=False to keep the raw args the approved-action executor
        re-runs from. See helpers.redact_structure.
        """
        from .helpers import redact_structure  # noqa: PLC0415

        public_args = redact_structure(self.args)
        if isinstance(public_args, dict):
            # Leading-underscore arguments are executor-only approval bindings
            # (MESA/context/private identity hashes), not operator input. Keep
            # them durably for replay but never project them through the admin
            # API or Details view.
            public_args = {
                key: "<redacted>" if isinstance(key, str) and key.startswith("_") else value
                for key, value in public_args.items()
            }
        return {
            "id": self.id,
            "access_key_id": self.access_key_id,
            "access_key_name": self.access_key_name,
            "tool_name": self.tool_name,
            "cap_name": self.cap_name,
            "args": self.args if not redact_args else public_args,
            "diff": self.diff,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "approved_by_user_id": self.approved_by_user_id,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejected_reason": self.rejected_reason,
            "result": self.result,
            "request_id": self.request_id,
            "client_ip": self.client_ip,
            "household_request": self.household_request,
            "ha_user_id": self.ha_user_id,
            "execution_started_at": (
                self.execution_started_at.isoformat() if self.execution_started_at else None
            ),
        }

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
            rejected_reason=data.get("rejected_reason"),
            result=data.get("result"),
            request_id=data.get("request_id", ""),
            client_ip=data.get("client_ip"),
            household_request=data.get("household_request", data.get("client_ip") in {"assist", "voice", "voice_agent", "ai_task"}),
            ha_user_id=data.get("ha_user_id"),
            execution_started_at=(
                parse_datetime(data["execution_started_at"])
                if data.get("execution_started_at") else None
            ),
        )

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


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
) -> PendingApproval:
    """Add a new pending approval to storage and return the record.

    Caller must hold store.async_lock if creating from a multi-step path.
    Raises PendingApprovalCapacityError if the access key is at the per-access-key cap.
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
    from .household_authority import household_authority

    household = household_authority.get()
    now = utcnow()
    approval = PendingApproval(
        id=_new_approval_id(),
        access_key_id=access_key_id,
        access_key_name=access_key_name,
        tool_name=tool_name,
        cap_name=cap_name,
        args=args,
        diff=diff,
        status=STATUS_PENDING,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        request_id=request_id,
        client_ip=client_ip,
        household_request=household is not None,
        ha_user_id=household.user_id if household else None,
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
    rejected_reason: str | None = None,
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
        if rejected_reason is not None:
            entry["rejected_reason"] = rejected_reason
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
    """Record on DISK that the admin approved and execution is about to run.

    The in-memory claim in data.approvals_in_progress stops a double-click but dies
    with the process. The executor runs outside the store lock and its outcome is
    written afterwards, so a stop or crash in that window would leave an untouched
    pending record whose side effect may already have applied and could be approved
    again. async_reconcile_interrupted_approvals reads this marker at startup and
    during periodic recovery.

    Returns True when the marker is durable. A False return MUST stop the caller
    from executing: this FAILS CLOSED. Executing anyway on an unwritable store buys
    nothing, because the same store must persist the terminal status, so a disk that
    cannot take the marker cannot record the outcome either. The action would run
    with no trace and stay re-approvable, the exact replay this marker prevents,
    made permanent. Refusing costs a retry.

    Caller must hold store.async_lock.
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
        except Exception:  # noqa: BLE001 - see the docstring: this must fail closed
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

    Only for failures that happen BEFORE the executor could apply anything (an
    unregistered tool name, which the registry lookup raises on before doing any
    work). Leaving the marker would make recovery resolve a good pending approval
    as interrupted.

    Never call this after a failure that might have applied something: that is the
    case the marker exists for, and the caller resolves the record instead.

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
            except Exception:  # noqa: BLE001 - the record is still pending either way
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
    """Resolve approvals whose execution claim has no recorded outcome.

    Called at startup and by the periodic approval sweep. A record still PENDING
    while carrying execution_started_at has no live in-process executor, so
    whether its side effect landed is unknown. Active executions are skipped by ID.

    It resolves to FAILED rather than staying pending: a pending record offers the
    admin a button that may apply a service call, restart or configuration write a
    SECOND time, whereas failing it costs at worst a re-request and re-approval,
    with the reason naming what is uncertain. Silently duplicating an unknown side
    effect is the one outcome with no recovery.

    Returns the changed records so the caller can dismiss their notifications and
    fire the resolved event. Caller must hold store.async_lock.
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
        entry["rejected_reason"] = REASON_EXECUTION_INTERRUPTED
        entry.pop("execution_started_at", None)
        try:
            changed.append(PendingApproval.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    if changed:
        store.set_pending_approvals(raw)
        await store.async_save()
        _LOGGER.warning(
            "Phoenix MCP: %d approval(s) were being executed when Home Assistant "
            "stopped; whether they applied is unknown, so they were resolved as "
            "failed rather than left approvable again", len(changed),
        )
    return changed


async def async_cancel_approvals_for_access_key(
    store: AccessKeyStore,
    access_key_id: str,
    reason: str,
    *,
    skip_ids: set[str] | frozenset[str] | None = None,
) -> list[PendingApproval]:
    """Close pending approvals for an access key and return the changed records.

    Called when an access key is revoked or expires, so its queued approvals do not
    linger as approvable-looking entries until the expiry sweep. skip_ids
    protects an approval whose saved action is mid-execution
    (data.approvals_in_progress). A stale execution marker resolves as failed
    and interrupted so access key cleanup cannot erase evidence of an uncertain side
    effect. Caller must hold async_lock, and must dismiss each returned record's
    notification and fire phoenix_mcp_approval_resolved outside the lock.
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
        interrupted = bool(entry.get("execution_started_at"))
        entry["status"] = STATUS_FAILED if interrupted else STATUS_CANCELLED
        entry["resolved_at"] = now_iso
        entry["rejected_reason"] = REASON_EXECUTION_INTERRUPTED if interrupted else reason
        entry.pop("execution_started_at", None)
        changed.append(PendingApproval.from_dict(entry))
    if changed:
        store.set_pending_approvals(raw)
        await store.async_save()
    return changed


def collect_pending_approvals_for_wipe(
    store: AccessKeyStore, reason: str,
) -> list[PendingApproval]:
    """Return the currently-pending approvals as cancelled records, read-only.

    Used by the admin wipe: AccessKeyStore.async_wipe clears the whole approval queue,
    so this does NOT mutate or persist the store. It builds the terminal records
    the caller needs to dismiss notifications and fire phoenix_mcp_approval_resolved
    after the wipe. There is no skip_ids: the wipe deletes every record, so an
    in-progress approval cannot be spared. Caller holds async_lock and must call
    this before async_wipe.
    """
    now_iso = utcnow().isoformat()
    out: list[PendingApproval] = []
    for entry in store.get_pending_approvals():
        if entry.get("status") != STATUS_PENDING:
            continue
        record = dict(entry)
        record["status"] = STATUS_CANCELLED
        record["resolved_at"] = now_iso
        record["rejected_reason"] = reason
        out.append(PendingApproval.from_dict(record))
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
        expired.append(PendingApproval.from_dict(entry))
    if expired:
        store.set_pending_approvals(raw)
        await store.async_save()
    return expired


def fire_approval_requested_event(hass: HomeAssistant, approval: PendingApproval) -> None:
    """Fire an HA event when a new approval is queued."""
    from .conversation_log import approval_event
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
    from .conversation_log import approval_event
    approval_event(hass, approval, resolved=True)
    hass.bus.async_fire(
        f"{DOMAIN}_approval_resolved",
        {
            "approval_id": approval.id,
            "access_key_id": approval.access_key_id,
            "access_key_name": approval.access_key_name,
            "tool_name": approval.tool_name,
            "status": approval.status,
            "rejected_reason": approval.rejected_reason,
            "approved_by_user_id": approval.approved_by_user_id,
            "timestamp": utcnow().isoformat(),
        },
    )


def fire_approval_claim_event(hass: HomeAssistant, approval_id: str, *, claimed: bool) -> None:
    """Fire an HA event when an approval is claimed for execution, or released.

    Approve runs the saved action INLINE in the admin's own request, so the
    resolved event cannot fire until the tool has finished, which can take seconds
    for a real write. Until then every surface showing the approval would stay
    actionable (Approve in Agent Chat, Reject in the panel), although the
    store-lock claim refuses the second action with a 409.

    This fires the moment the claim is taken so every surface can go
    non-actionable immediately. It fires again with claimed=False when execution
    failed and left the approval pending and retryable: a released approval is
    actionable again and must come back, which is why this is a claim/release pair
    and not a second "resolved".

    Reject needs no counterpart: it resolves inside the store lock with nothing to
    execute, so its resolved event already fires promptly.
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

    from homeassistant.components import persistent_notification  # noqa: PLC0415

    persistent_notification.async_create(
        hass,
        message=notification_text(
            hass, "approval.message",
            access_key=approval.access_key_name,
            url=f"/phoenix-mcp/approvals/{approval.id}",
        ),
        title=notification_text(hass, "approval.title"),
        notification_id=notification_id_for_approval(approval.id),
    )


def dismiss_approval_notification(hass: HomeAssistant, approval_id: str) -> None:
    from homeassistant.components import persistent_notification  # noqa: PLC0415

    persistent_notification.async_dismiss(
        hass,
        notification_id=notification_id_for_approval(approval_id),
    )


async def async_prune_terminal_approvals(store: AccessKeyStore, *, skip_ids: set[str] | None = None) -> None:
    """Bound resolved history and retire raw payloads; caller holds the store lock."""
    now = utcnow()
    protected, resolved = [], []
    original = store.get_pending_approvals()
    skipped = skip_ids or set()
    for row in original:
        if (row.get("status") not in TERMINAL_STATUSES or row.get("execution_started_at")
                or row.get("rejected_reason") == REASON_EXECUTION_INTERRUPTED
                or row.get("id") in skipped):
            protected.append(row)
            continue
        try:
            at = parse_datetime(row.get("resolved_at") or row.get("created_at") or "")
        except (TypeError, ValueError):
            at = None
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
