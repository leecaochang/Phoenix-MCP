"""Faults during an approval: cancellation at each stage of the claim, corrupt stored rows.

Runs over HA's real router against a real store. aiohttp does not cancel a handler
when its client leaves, so each fault cancels the handler's own task from the point
it is parked, which is also how a stopping Home Assistant reaches it.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp.test_utils import make_mocked_request
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
import pytest

import custom_components.phoenix_mcp as phoenix
from custom_components.phoenix_mcp.admin.approvals import _approve_approval
from custom_components.phoenix_mcp.approvals import (
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING,
    async_cancel_approvals_for_access_key,
    async_cancel_queued_approvals_at_startup,
    async_create_pending_approval,
    async_expire_overdue_approval_records,
    async_reconcile_interrupted_approvals,
    collect_pending_approvals_for_wipe,
    get_approval,
    list_approvals,
)
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.durable_storage import DurableStorageError
from custom_components.phoenix_mcp.household_authority import household_authority
from custom_components.phoenix_mcp.request_authority import policy_version, request_authority
from custom_components.phoenix_mcp.tool_dispatch import ExecutorNotRegistered
from homeassistant.util.dt import utcnow

APPROVE = "/api/phoenix-mcp/admin/approvals/{}/approve"
APPROVALS = "/api/phoenix-mcp/admin/approvals"
REJECT = "/api/phoenix-mcp/admin/approvals/{}/reject"
LIST = "/api/phoenix-mcp/admin/approvals"
EXECUTOR = "custom_components.phoenix_mcp.admin.approvals.async_execute_approved_tool"
CLAIM_EVENT = "custom_components.phoenix_mcp.admin.approvals.fire_approval_claim_event"
RESOLVED_EVENT = "custom_components.phoenix_mcp.admin.approvals.fire_approval_resolved_event"
DISMISS = "custom_components.phoenix_mcp.admin.approvals.dismiss_approval_notification"
OFFER = "custom_components.phoenix_mcp.admin.approvals.create_approval_notification"
UPDATE = "custom_components.phoenix_mcp.admin.approvals.async_update_approval_status"
OK = ({"content": [{"type": "text", "text": "ok"}]}, "allowed", "restart_ha")


@pytest.fixture
async def home(hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = hass.data[DOMAIN]
    runner, _ = await data.store.async_create_access_key("runner", "admin")
    data.store.get_access_key_by_id(runner.id).cap_restart = "confirm"
    await data.store.async_save()
    return data, runner, await hass_client(), entry


async def _approval(data, access_key, request_id="r1"):
    async with data.store.async_lock:
        return await async_create_pending_approval(
            data.store, access_key_id=access_key.id, access_key_name=access_key.name,
            tool_name="restart_ha", cap_name="cap_restart", args={},
            diff={"kind": "system_action", "summary": "x"}, request_id=request_id,
        )


async def _post_expecting_cancel(client, approval_id) -> None:
    """The server drops the connection when its handler is cancelled mid-request."""
    with pytest.raises((aiohttp.ClientError, asyncio.TimeoutError)):
        response = await client.post(APPROVE.format(approval_id), json={})
        pytest.fail(f"handler answered {response.status}")


async def test_cancel_during_the_durable_claim_takes_no_memory_claim(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    real_save = data.store.async_save

    async def cancelled_save():
        data.store.async_save = real_save
        asyncio.current_task().cancel()
        await asyncio.sleep(0)

    data.store.async_save = cancelled_save
    with patch(EXECUTOR, AsyncMock(return_value=OK)) as executor:
        await _post_expecting_cancel(client, record.id)
    executor.assert_not_awaited()
    assert record.id not in data.approvals_in_progress
    assert get_approval(data.store, record.id).execution_started_at is not None


async def test_cancel_after_the_claim_releases_it_and_the_marker_refuses_a_retry(hass, home):
    """The claim event precedes dispatch, so nothing ran, yet only recovery may settle it."""
    data, runner, client, _ = home
    record = await _approval(data, runner)
    with (patch(CLAIM_EVENT, side_effect=asyncio.CancelledError),
          patch(EXECUTOR, AsyncMock(return_value=OK)) as executor):
        await _post_expecting_cancel(client, record.id)
    executor.assert_not_awaited()
    assert record.id not in data.approvals_in_progress
    assert get_approval(data.store, record.id).execution_started_at is not None

    with patch(EXECUTOR, AsyncMock(return_value=OK)) as executor:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 409
    assert (await response.json())["message_key"] == "adminError.approvalExecutionInterrupted"
    executor.assert_not_awaited()


async def test_cancel_during_dispatch_resolves_failed_and_is_never_offered_again(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    resolved = []
    hass.bus.async_listen(f"{DOMAIN}_approval_resolved", resolved.append)

    async def cancelled(*_a, **_k):
        asyncio.current_task().cancel()
        await asyncio.sleep(0)

    with patch(EXECUTOR, side_effect=cancelled):
        await _post_expecting_cancel(client, record.id)
    await hass.async_block_till_done()
    assert record.id not in data.approvals_in_progress
    closed = get_approval(data.store, record.id)
    assert closed.status == STATUS_FAILED and closed.closure_reason == "execution_interrupted"
    assert [event.data["approval_id"] for event in resolved] == [record.id]

    with patch(EXECUTOR, AsyncMock(return_value=OK)) as executor:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 200
    assert (await response.json())["status"] == STATUS_FAILED
    executor.assert_not_awaited()


@contextmanager
def _traced(data, record_id, execute):
    """Log every side effect of an approve in order; ``execute`` is the fake executor."""
    log: list[tuple] = []
    real_save, real_audit = data.store.async_save, data.audit.record

    async def save():
        row = next((r for r in data.store.get_pending_approvals() if r["id"] == record_id), None)
        log.append(("save", row and row["status"], bool(row and row.get("execution_started_at"))))
        await real_save()

    def audit(**row):
        log.append(("audit", row["method"], (row["payload"] or {}).get("finalization")))
        real_audit(**row)

    async def executor(*args):
        log.append(("execute",))
        return await execute(*args)

    data.store.async_save = save
    with (patch.object(data.audit, "record", audit), patch(EXECUTOR, executor),
          patch(CLAIM_EVENT, side_effect=lambda _h, _id, *, claimed: log.append(("claim", claimed))),
          patch(DISMISS, side_effect=lambda *_: log.append(("dismiss",))),
          patch(RESOLVED_EVENT, side_effect=lambda _h, row: log.append(("resolved", row.status))),
          patch(OFFER, side_effect=lambda _h, row: log.append(("offer", row.status)))):
        yield log
    data.store.async_save = real_save


def _refuse_saves(data):
    """Fail below the store, so its real rollback returns memory to the last durable state."""
    async def refused(_payload):
        raise DurableStorageError("no room")

    data.store._store.async_save = refused


def _forget(data, record_id):
    data.store.set_pending_approvals(
        [row for row in data.store.get_pending_approvals() if row["id"] != record_id])


CLAIMED = [("save", STATUS_PENDING, True), ("audit", "approval/approved", None), ("claim", True),
           ("dismiss",), ("execute",)]


async def test_a_successful_approve_acts_only_after_its_claim_is_durable_and_announced(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    with _traced(data, record.id, AsyncMock(return_value=OK)) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 200
    assert log == [*CLAIMED, ("save", "approved", False), ("dismiss",), ("resolved", "approved"),
                   ("audit", "approval/executed", None)]


async def test_an_error_result_closes_as_failed_in_the_same_order(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    error = ({"isError": True, "content": []}, "invalid_request", "restart_ha")
    with _traced(data, record.id, AsyncMock(return_value=error)) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 200
    assert log == [*CLAIMED, ("save", STATUS_FAILED, False), ("dismiss",), ("resolved", STATUS_FAILED),
                   ("audit", "approval/failed", None)]


async def test_a_missing_executor_clears_the_marker_and_only_then_offers_again(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    with _traced(data, record.id, AsyncMock(side_effect=ExecutorNotRegistered("none"))) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 400
    assert log == [*CLAIMED, ("save", STATUS_PENDING, False), ("audit", "approval/failed", None),
                   ("claim", False), ("offer", STATUS_PENDING)]
    assert get_approval(data.store, record.id).execution_started_at is None


async def test_a_raising_executor_closes_before_its_audit_and_is_not_offered_again(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    with _traced(data, record.id, AsyncMock(side_effect=RuntimeError("boom"))) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 500
    body = await response.json()
    assert (body["error"], body["message_key"]) == ("internal_error", "adminError.approvalExecutionUncertain")
    assert log == [*CLAIMED, ("save", STATUS_FAILED, False), ("resolved", STATUS_FAILED),
                   ("audit", "approval/failed", None)]


async def test_a_closed_request_saves_before_it_announces_and_never_claims(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    data.store.get_settings().kill_switch = True
    with _traced(data, record.id, AsyncMock(return_value=OK)) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 503
    assert log == [("save", STATUS_CANCELLED, False), ("dismiss",), ("resolved", STATUS_CANCELLED),
                   ("audit", "approval/cancelled", None)]


async def test_an_expired_request_saves_before_it_announces_and_never_claims(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    next(row for row in data.store.get_pending_approvals() if row["id"] == record.id)["expires_at"] = (
        (utcnow() - timedelta(seconds=1)).isoformat())
    with _traced(data, record.id, AsyncMock(return_value=OK)) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 409
    assert log == [("save", STATUS_EXPIRED, False), ("dismiss",), ("resolved", STATUS_EXPIRED),
                   ("audit", "approval/expired", None)]


async def test_an_outcome_that_cannot_be_saved_keeps_the_claim_on_every_surface(hass, home):
    """The surfaces stay claimed: no dismissal, resolution, release or new offer."""
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        _refuse_saves(data)
        return OK

    with _traced(data, record.id, execute) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    del data.store._store.async_save
    assert response.status == 500
    assert (await response.json())["message_key"] == "adminError.approvalOutcomeNotSaved"
    assert log == [*CLAIMED, ("save", "approved", False), ("audit", "approval/executed", "not_saved")]
    assert record.id not in data.approvals_in_progress
    kept = get_approval(data.store, record.id)
    assert kept.status == STATUS_PENDING and kept.execution_started_at is not None


async def test_a_closure_whose_record_is_gone_answers_not_found_and_announces_nothing(hass, home):
    """The store lock keeps the row in place, so only a replaced writer reports it missing."""
    data, runner, client, _ = home
    record = await _approval(data, runner)
    data.store.get_settings().kill_switch = True
    with _traced(data, record.id, AsyncMock(return_value=OK)) as log, patch(UPDATE, AsyncMock(return_value=None)):
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 404
    assert (await response.json())["message_key"] == "adminError.approvalNotFound"
    assert log == []


async def test_a_record_removed_during_a_raising_executor_still_audits_the_failure(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        _forget(data, record.id)
        raise RuntimeError("boom")

    with _traced(data, record.id, execute) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 500
    assert log == [*CLAIMED, ("audit", "approval/failed", None)]
    assert record.id not in data.approvals_in_progress


async def test_a_record_removed_during_execution_is_audited_and_answers_not_found(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        _forget(data, record.id)
        return OK

    with _traced(data, record.id, execute) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 404
    assert (await response.json())["message_key"] == "adminError.approvalNotFound"
    assert log == [*CLAIMED, ("audit", "approval/executed", "missing_after_execution"), ("claim", False)]
    assert record.id not in data.approvals_in_progress


async def test_a_record_resolved_during_execution_releases_the_claim_without_an_offer(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        async with data.store.async_lock:
            await async_cancel_approvals_for_access_key(data.store, runner.id, reason="key_revoked")
        return OK

    with _traced(data, record.id, execute) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 409
    assert (await response.json())["message_key"] == "adminError.approvalAlreadyResolved"
    assert log[-2:] == [("audit", "approval/executed", "conflict"), ("claim", False)]


async def test_a_failed_execution_whose_closure_cannot_be_saved_is_not_offered_again(hass, home, caplog):
    """The marker stays for recovery and an ordinary failure counts as settled."""
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        _refuse_saves(data)
        raise RuntimeError("boom")

    with _traced(data, record.id, execute) as log:
        response = await client.post(APPROVE.format(record.id), json={})
    del data.store._store.async_save
    assert response.status == 500
    assert log == [*CLAIMED, ("save", STATUS_FAILED, False)]
    assert "Could not resolve approval" in caplog.text
    assert record.id not in data.approvals_in_progress
    assert get_approval(data.store, record.id).execution_started_at is not None

    with _traced(data, record.id, AsyncMock(return_value=OK)) as retry:
        again = await client.post(APPROVE.format(record.id), json={})
    assert again.status == 409
    assert (await again.json())["message_key"] == "adminError.approvalExecutionInterrupted"
    assert retry == []


async def test_a_cancelled_execution_whose_closure_cannot_be_saved_stays_marked(hass, home, caplog):
    """The marker stays, so the surfaces keep their claim, a fresh list agrees and a retry is refused."""
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        _refuse_saves(data)
        raise asyncio.CancelledError

    with _traced(data, record.id, execute) as log:
        await _post_expecting_cancel(client, record.id)
        await hass.async_block_till_done()
    del data.store._store.async_save
    assert log == [*CLAIMED, ("save", STATUS_FAILED, False)]
    assert "Could not resolve approval" in caplog.text
    assert record.id not in data.approvals_in_progress
    assert get_approval(data.store, record.id).execution_started_at is not None
    listed = await (await client.get(APPROVALS)).json()
    assert [(row["id"], row["in_progress"]) for row in listed["approvals"]] == [(record.id, True)]

    with _traced(data, record.id, AsyncMock(return_value=OK)) as retry:
        again = await client.post(APPROVE.format(record.id), json={})
    assert again.status == 409
    assert retry == []


def _expire(data, record):
    next(row for row in data.store.get_pending_approvals() if row["id"] == record.id)["expires_at"] = (
        (utcnow() - timedelta(seconds=1)).isoformat())


CONDITIONS = {
    "expired": lambda data, runner, record: _expire(data, record),
    "revoked": lambda data, runner, record: setattr(runner, "revoked", True),
    "denied": lambda data, runner, record: setattr(runner, "cap_restart", "deny"),
    "kill": lambda data, runner, record: setattr(data.store.get_settings(), "kill_switch", True),
}
CLOSURES = {
    "expired": (409, "approvalExpired", STATUS_EXPIRED, None, "approval/expired"),
    "revoked": (404, "approvalAccessKeyInactive", STATUS_CANCELLED, "access_key_inactive", "approval/cancelled"),
    "denied": (403, "approvalCapabilityDenied", STATUS_FAILED, "capability_denied", "approval/failed"),
    "kill": (503, "killSwitchEngaged", STATUS_CANCELLED, "kill_switch", "approval/cancelled"),
}


@pytest.mark.parametrize("conditions", [
    ("expired",), ("revoked",), ("denied",), ("kill",),
    ("expired", "revoked"), ("expired", "denied"), ("expired", "kill"),
    ("revoked", "denied"), ("revoked", "kill"), ("denied", "kill"),
], ids="+".join)
async def test_the_first_failed_check_closes_the_request(hass, home, hass_admin_user, conditions):
    """Each listed condition holds, and the leftmost decides status, answer, stamp and audit."""
    data, runner, client, _ = home
    record = await _approval(data, runner)
    for name in conditions:
        CONDITIONS[name](data, runner, record)
    status, key, stored, reason, method = CLOSURES[conditions[0]]
    with (patch.object(data.audit, "record", wraps=data.audit.record) as audit,
          patch(EXECUTOR, AsyncMock(return_value=OK)) as executor):
        response = await client.post(APPROVE.format(record.id), json={})
    executor.assert_not_awaited()
    assert response.status == status
    assert (await response.json())["message_key"] == f"adminError.{key}"
    closed = get_approval(data.store, record.id)
    assert (closed.status, closed.closure_reason) == (stored, reason)
    assert closed.approved_by_user_id == (None if conditions[0] == "expired" else hass_admin_user.id)
    row = audit.call_args.kwargs
    assert audit.call_count == 1
    assert (row["method"], row["outcome"]) == (method, "denied")
    assert row["payload"] == ({"reason": reason} if reason else None)
    assert row["resource"] == f"approval:restart_ha:{record.id}"
    assert (row["access_key_id"], row["access_key_name"], row["ha_user_id"]) == (
        "admin", f"admin:{hass_admin_user.id}", hass_admin_user.id)


async def test_a_wipe_refuses_before_any_record_is_read(hass, home):
    data, _, client, _ = home
    data.core_wipe_in_progress = True
    response = await client.post(APPROVE.format("appr_missing"), json={})
    data.core_wipe_in_progress = False
    assert response.status == 409
    assert (await response.json())["message_key"] == "adminError.approvalWipeInProgress"


async def test_a_resolved_record_answers_with_itself_even_while_its_claim_is_held(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    next(row for row in data.store.get_pending_approvals() if row["id"] == record.id)["status"] = "rejected"
    data.approvals_in_progress.add(record.id)
    response = await client.post(APPROVE.format(record.id), json={})
    data.approvals_in_progress.discard(record.id)
    assert response.status == 200
    assert (await response.json())["status"] == "rejected"


async def test_a_closure_is_announced_after_the_lock_is_released(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    data.store.get_settings().kill_switch = True
    locked = []
    held = lambda *_a, **_k: locked.append(data.store.async_lock.locked())
    with (patch(DISMISS, side_effect=held), patch(RESOLVED_EVENT, side_effect=held),
          patch.object(data.audit, "record", side_effect=held)):
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 503
    assert locked == [False, False, False]


async def test_a_queued_reject_cannot_enter_between_the_checks_and_the_marker(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    lock = data.store.async_lock

    async def queued(count):
        while len(lock._waiters or ()) < count:
            await asyncio.sleep(0)

    with patch(EXECUTOR, AsyncMock(return_value=OK)) as executor:
        await lock.acquire()
        approve = asyncio.create_task(client.post(APPROVE.format(record.id), json={}))
        await queued(1)
        reject = asyncio.create_task(client.post(REJECT.format(record.id), json={}))
        await queued(2)
        lock.release()
        approved, rejected = await approve, await reject
    assert (approved.status, rejected.status) == (200, 409)
    executor.assert_awaited_once()
    assert get_approval(data.store, record.id).status == "approved"


async def test_the_lifecycle_audit_names_the_admin_and_the_execution_names_the_key(hass, home, hass_admin_user):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    next(row for row in data.store.get_pending_approvals() if row["id"] == record.id)["ha_user_id"] = "requester"
    with (patch.object(data.audit, "record", wraps=data.audit.record) as audit,
          patch(EXECUTOR, AsyncMock(return_value=OK))):
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 200
    decision, execution = (call.kwargs for call in audit.call_args_list)
    assert (decision["method"], decision["outcome"], decision["payload"]) == ("approval/approved", "allowed", None)
    assert (decision["access_key_id"], decision["ha_user_id"]) == ("admin", hass_admin_user.id)
    assert (execution["method"], execution["outcome"], execution["payload"]) == ("approval/executed", "allowed", None)
    assert (execution["access_key_id"], execution["access_key_name"]) == (runner.id, runner.name)
    assert execution["ha_user_id"] == "requester"
    assert decision["settings"] is execution["settings"] is data.store.get_settings()


async def test_a_failed_result_is_audited_as_denied(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    error = ({"isError": True, "content": []}, "invalid_request", "restart_ha")
    with (patch.object(data.audit, "record", wraps=data.audit.record) as audit,
          patch(EXECUTOR, AsyncMock(return_value=error))):
        await client.post(APPROVE.format(record.id), json={})
    row = audit.call_args.kwargs
    assert (row["method"], row["outcome"]) == ("approval/failed", "denied")


@pytest.mark.parametrize("outcome", ["returned", "raised", "unregistered"])
async def test_the_audit_after_dispatch_keeps_the_settings_read_under_the_lock(hass, home, outcome):
    """Logging settings replaced while an action runs do not rewrite how its decision is recorded."""
    data, runner, client, _ = home
    record = await _approval(data, runner)
    admitted = data.store.get_settings()

    async def execute(*_):
        data.store._settings = replace(admitted)
        if outcome == "raised":
            raise RuntimeError("boom")
        if outcome == "unregistered":
            raise ExecutorNotRegistered("none")
        return OK

    with (patch.object(data.audit, "record", wraps=data.audit.record) as audit, patch(EXECUTOR, execute)):
        await client.post(APPROVE.format(record.id), json={})
    data.store._settings = admitted
    assert [call.kwargs["settings"] is admitted for call in audit.call_args_list] == [True, True]


@pytest.mark.parametrize("fault", [None, RuntimeError("boom")], ids=["returned", "raised"])
async def test_the_outcome_is_saved_under_the_store_lock(hass, home, fault):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    real_save = data.store.async_save
    locked = []

    async def save():
        locked.append(data.store.async_lock.locked())
        await real_save()

    data.store.async_save = save
    with patch(EXECUTOR, AsyncMock(side_effect=fault, return_value=OK)):
        await client.post(APPROVE.format(record.id), json={})
    data.store.async_save = real_save
    assert locked == [True, True]


async def test_a_plain_request_runs_without_a_household_identity(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    seen = []

    async def execute(*_):
        seen.append(household_authority.get())
        return OK

    with patch(EXECUTOR, execute):
        await client.post(APPROVE.format(record.id), json={})
    assert seen == [None]


async def test_a_cancelled_dispatch_stays_cancelled_for_the_caller(hass, home, hass_admin_user):
    data, runner, _, _ = home
    record = await _approval(data, runner)
    request = make_mocked_request("POST", "/approve")
    request[KEY_HASS_USER] = hass_admin_user
    request["phoenix_mcp_rid"] = "rid"
    with patch(EXECUTOR, AsyncMock(side_effect=asyncio.CancelledError)), pytest.raises(asyncio.CancelledError):
        await _approve_approval(hass, request, record.id)
    assert get_approval(data.store, record.id).status == STATUS_FAILED


async def test_a_save_failure_that_is_not_a_storage_refusal_is_not_reported_as_one(hass, home):
    """Only the storage layer's refusal proves the store went back to its marked record."""
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        async def broken():
            data.store.async_save = real_save
            raise RuntimeError("boom")

        data.store.async_save = broken
        return OK

    with _traced(data, record.id, execute) as log:
        real_save = data.store.async_save
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 500
    assert log[-1] == ("claim", False)
    assert not any(row[0] == "audit" and row[2] for row in log)


async def test_an_unsaved_outcome_records_what_the_executor_reported(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)

    async def execute(*_):
        _refuse_saves(data)
        return OK

    with (patch.object(data.audit, "record", wraps=data.audit.record) as audit, patch(EXECUTOR, execute)):
        await client.post(APPROVE.format(record.id), json={})
    del data.store._store.async_save
    assert audit.call_args.kwargs["payload"] == {"finalization": "not_saved", "executor_outcome": "allowed"}


async def test_an_audit_failure_on_an_unsaved_outcome_still_keeps_the_claim(hass, home):
    """The marker stands whether or not the row landed, so nothing offers the action again."""
    data, runner, client, _ = home
    record = await _approval(data, runner)
    real_audit = data.audit.record

    def audit(**row):
        if (row["payload"] or {}).get("finalization"):
            raise RuntimeError("boom")
        real_audit(**row)

    async def execute(*_):
        _refuse_saves(data)
        return OK

    with _traced(data, record.id, execute) as log, patch.object(data.audit, "record", audit):
        response = await client.post(APPROVE.format(record.id), json={})
    del data.store._store.async_save
    assert response.status == 500
    assert not {"claim", "offer"} & {entry[0] for entry in log[len(CLAIMED):]}
    assert get_approval(data.store, record.id).execution_started_at is not None


async def test_a_notification_that_cannot_be_dismissed_decides_nothing(hass, home, caplog):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    resolved = []
    hass.bus.async_listen(f"{DOMAIN}_approval_resolved", resolved.append)
    execute = AsyncMock(return_value=OK)

    with (patch("homeassistant.components.persistent_notification.async_dismiss", side_effect=RuntimeError("boom")),
          patch(EXECUTOR, execute)):
        response = await client.post(APPROVE.format(record.id), json={})
        await hass.async_block_till_done()
    assert response.status == 200
    assert execute.await_count == 1
    assert get_approval(data.store, record.id).status == "approved"
    assert [event.data["approval_id"] for event in resolved] == [record.id]
    assert "Could not dismiss the notification" in caplog.text


async def test_a_failed_final_dismissal_still_releases_the_claim(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    dismissals = []

    def dismiss(*_):
        dismissals.append(1)
        if len(dismissals) == 2:
            raise RuntimeError("boom")

    with (patch(DISMISS, side_effect=dismiss), patch(CLAIM_EVENT) as claim,
          patch(EXECUTOR, AsyncMock(return_value=OK))):
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 500
    assert [call.kwargs["claimed"] for call in claim.call_args_list] == [True, False]
    assert get_approval(data.store, record.id).status == "approved"


async def test_a_failed_final_event_needs_no_release_because_dismissal_settled_it(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    with (patch(RESOLVED_EVENT, side_effect=RuntimeError("boom")), patch(CLAIM_EVENT) as claim,
          patch(EXECUTOR, AsyncMock(return_value=OK))):
        response = await client.post(APPROVE.format(record.id), json={})
    assert response.status == 500
    assert [call.kwargs["claimed"] for call in claim.call_args_list] == [True]


@pytest.mark.parametrize("execute", [AsyncMock(return_value=OK), AsyncMock(side_effect=RuntimeError("boom"))],
                         ids=["returned", "raised"])
async def test_dispatch_leaves_no_authority_in_the_calling_context(hass, home, hass_admin_user, execute):
    data, runner, _, _ = home
    record = await _approval(data, runner)
    request = make_mocked_request("POST", "/approve")
    request[KEY_HASS_USER] = hass_admin_user
    request["phoenix_mcp_rid"] = "rid"
    with patch(EXECUTOR, execute):
        await _approve_approval(hass, request, record.id)
    assert request_authority.get() is None
    assert policy_version.get() is None


async def test_dispatch_runs_in_the_conversation_scope_of_its_own_approval(hass, home):
    data, runner, client, _ = home
    record = await _approval(data, runner)
    with (patch.object(data.conversations, "approval_scope", wraps=data.conversations.approval_scope) as scope,
          patch(EXECUTOR, AsyncMock(return_value=OK))):
        await client.post(APPROVE.format(record.id), json={})
    scope.assert_called_once_with(record.id)


def _corrupt(approval_id: str, **fields) -> dict:
    return {"id": approval_id, "status": STATUS_PENDING, **fields}


async def test_a_corrupt_row_hides_itself_and_no_sibling(hass, home):
    data, runner, client, _ = home
    good = await _approval(data, runner)
    data.store.get_pending_approvals().append(_corrupt("appr_bad", created_at="not a time"))

    assert get_approval(data.store, "appr_bad") is None
    assert [row.id for row in list_approvals(data.store)] == [good.id]
    response = await client.get(LIST)
    assert response.status == 200
    body = await response.json()
    assert [row["id"] for row in body["approvals"]] == [good.id]

    missing = await client.post(APPROVE.format("appr_bad"), json={})
    assert missing.status == 404
    assert (await missing.json())["message_key"] == "adminError.approvalNotFound"


async def test_a_corrupt_overdue_row_does_not_stop_the_expiry_sweep(hass, home):
    """One bad row must not leave every later overdue approval open for good."""
    data, runner, _, _ = home
    data.store.get_pending_approvals().append(_corrupt("appr_bad", expires_at="2000-01-01T00:00:00+00:00"))
    late = await _approval(data, runner)
    next(row for row in data.store.get_pending_approvals() if row["id"] == late.id)["expires_at"] = (
        "2000-01-01T00:00:00+00:00")
    async with data.store.async_lock:
        expired = await async_expire_overdue_approval_records(data.store)
    assert [row.id for row in expired] == [late.id]
    assert get_approval(data.store, late.id).status == STATUS_EXPIRED


async def test_a_corrupt_interrupted_row_does_not_stop_recovery(hass, home):
    data, runner, _, _ = home
    data.store.get_pending_approvals().append(_corrupt("appr_bad", execution_started_at=utcnow().isoformat()))
    interrupted = await _approval(data, runner)
    next(row for row in data.store.get_pending_approvals() if row["id"] == interrupted.id)[
        "execution_started_at"] = utcnow().isoformat()
    async with data.store.async_lock:
        closed = await async_reconcile_interrupted_approvals(data.store)
    assert [row.id for row in closed] == [interrupted.id]
    assert get_approval(data.store, interrupted.id).status == STATUS_FAILED


async def test_a_corrupt_row_does_not_stop_a_revocation_cancelling_the_key_queue(hass, home):
    data, runner, _, _ = home
    data.store.get_pending_approvals().append(_corrupt("appr_bad", access_key_id=runner.id))
    good = await _approval(data, runner)
    async with data.store.async_lock:
        closed = await async_cancel_approvals_for_access_key(data.store, runner.id, reason="key_revoked")
    assert [row.id for row in closed] == [good.id]
    assert get_approval(data.store, good.id).status == STATUS_CANCELLED


async def test_a_corrupt_row_does_not_stop_the_restart_or_wipe_cancellations(hass, home):
    data, runner, _, _ = home
    data.store.get_pending_approvals().append(_corrupt("appr_bad"))
    good = await _approval(data, runner)
    assert [row.id for row in collect_pending_approvals_for_wipe(data.store, "wiped")] == [good.id]
    async with data.store.async_lock:
        closed = await async_cancel_queued_approvals_at_startup(data.store)
    assert [row.id for row in closed] == [good.id]


@pytest.mark.parametrize("surface", [
    "custom_components.phoenix_mcp.assist_api.async_unregister_assist_api",
    "custom_components.phoenix_mcp.voice_agent.async_unregister_voice_agent",
    "custom_components.phoenix_mcp.debug_capture.async_detach_debug_capture",
])
async def test_setup_rollback_continues_past_a_surface_that_raises(hass, home, surface):
    data, _, _, entry = home
    data.forwarded_platforms = {"sensor"}
    with (patch(surface, side_effect=RuntimeError("boom")),
          patch.object(phoenix, "_remove_frontend") as frontend,
          patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)) as unload):
        await phoenix._rollback_failed_setup(hass, entry, data)
    frontend.assert_called_once_with(hass)
    unload.assert_awaited_once_with(entry, ["sensor"])
    assert data.shutting_down and not data.ready
    assert DOMAIN not in hass.data


async def test_setup_rollback_keeps_the_original_failure_when_unloading_platforms_fails(hass, home):
    data, _, _, entry = home
    data.forwarded_platforms = {"sensor"}
    with (patch.object(phoenix, "_remove_frontend"),
          patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(side_effect=RuntimeError("boom")))):
        await phoenix._rollback_failed_setup(hass, entry, data)
    assert DOMAIN not in hass.data
