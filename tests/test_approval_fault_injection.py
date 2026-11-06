"""Faults during an approval: cancellation at each stage of the claim, corrupt stored rows.

Runs over HA's real router against a real store. aiohttp does not cancel a handler
when its client leaves, so each fault cancels the handler's own task from the point
it is parked, which is also how a stopping Home Assistant reaches it.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
import pytest

import custom_components.phoenix_mcp as phoenix
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
from homeassistant.util.dt import utcnow

APPROVE = "/api/phoenix-mcp/admin/approvals/{}/approve"
LIST = "/api/phoenix-mcp/admin/approvals"
EXECUTOR = "custom_components.phoenix_mcp.admin.approvals.async_execute_approved_tool"
CLAIM_EVENT = "custom_components.phoenix_mcp.admin.approvals.fire_approval_claim_event"
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
        raise aiohttp.ClientError(f"handler answered {response.status}")


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
