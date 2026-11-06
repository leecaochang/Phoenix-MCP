"""One owner per storage file across reload, on a real entry, real HTTP and real stores.

A reload rebuilds registrations, not state. A handler that started before the
reload keeps its PhoenixData and finishes afterwards, so these tests hold one in
flight while the runtime around it is replaced.
"""

import ast
import asyncio
import importlib
import json
import logging
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry
import pytest

import custom_components.phoenix_mcp as phoenix
from custom_components.phoenix_mcp import access_key_store, tool_catalog
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.agent_recovery import STORAGE_KEY as RECOVERY_KEY
from custom_components.phoenix_mcp.approvals import async_create_pending_approval
from custom_components.phoenix_mcp.const import AUDIT_STORAGE_KEY, DOMAIN, MESA_STORAGE_KEY, STORAGE_KEY
from custom_components.phoenix_mcp.durable_storage import DurableStorageError, DurableStore, ReportingStore
from custom_components.phoenix_mcp.tool_dispatch import _dispatch_mcp

APPROVE = "/api/phoenix-mcp/admin/approvals/{}/approve"
KEYS = "/api/phoenix-mcp/admin/access-keys"
EXECUTOR = "custom_components.phoenix_mcp.admin.approvals.async_execute_approved_tool"


async def _setup(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]


async def _approval(data, access_key, request_id):
    async with data.store.async_lock:
        return await async_create_pending_approval(
            data.store, access_key_id=access_key.id, access_key_name=access_key.name,
            tool_name="restart_ha", cap_name="cap_restart", args={},
            diff={"kind": "system_action", "summary": "x"}, request_id=request_id,
        )


async def _runner(data):
    runner, _ = await data.store.async_create_access_key("runner", "admin")
    data.store.get_access_key_by_id(runner.id).cap_restart = "confirm"
    await data.store.async_save()
    return runner


class _Slow:
    """An approved executor held open until the test releases it."""

    def __init__(self) -> None:
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def run(self, *_a, **_k):
        self.entered.set()
        await self.release.wait()
        return ({"content": [{"type": "text", "text": "ok"}]}, "allowed", "restart_ha")


def _disk(hass_storage) -> dict:
    return hass_storage[STORAGE_KEY]["data"]


def _names(rows) -> list[str]:
    return [row["name"] for row in rows]


async def test_a_revocation_stays_durable_when_an_older_handler_finishes(
    hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user,
):
    entry, data = await _setup(hass)
    runner = await _runner(data)
    victim, _ = await data.store.async_create_access_key("victim", "admin")
    record = await _approval(data, runner, "r1")
    client = await hass_client()
    slow = _Slow()
    with patch(EXECUTOR, side_effect=slow.run):
        approve = asyncio.create_task(client.post(APPROVE.format(record.id), json={}))
        await asyncio.wait_for(slow.entered.wait(), 5)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        new = hass.data[DOMAIN]
        assert new is not data and new.store is data.store

        response = await client.delete(f"{KEYS}/{victim.id}")
        assert response.status == 204
        assert _names(_disk(hass_storage)["access_keys"]) == ["runner"]

        slow.release.set()
        answer = await asyncio.wait_for(approve, 5)
        await hass.async_block_till_done()
    assert answer.status == 200
    assert _names(_disk(hass_storage)["access_keys"]) == ["runner"]
    assert _names(_disk(hass_storage)["archived_access_keys"]) == ["victim"]
    restarted = await AccessKeyStore.async_create(hass)
    assert victim.access_key_hash not in {k.access_key_hash for k in restarted.list_access_keys()}


async def test_a_key_created_after_reload_survives_an_older_handler(
    hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user,
):
    entry, data = await _setup(hass)
    record = await _approval(data, await _runner(data), "r1")
    client = await hass_client()
    slow = _Slow()
    with patch(EXECUTOR, side_effect=slow.run):
        approve = asyncio.create_task(client.post(APPROVE.format(record.id), json={}))
        await asyncio.wait_for(slow.entered.wait(), 5)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        await hass.data[DOMAIN].store.async_create_access_key("after-reload", "admin")
        slow.release.set()
        await asyncio.wait_for(approve, 5)
        await hass.async_block_till_done()
    assert sorted(_names(_disk(hass_storage)["access_keys"])) == ["after-reload", "runner"]


async def test_a_running_approval_is_not_declared_interrupted_by_a_reload(
    hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user,
):
    """The claim outlives the reload, and the outcome lands in the live audit log."""
    entry, data = await _setup(hass)
    record = await _approval(data, await _runner(data), "r1")
    client = await hass_client()
    slow = _Slow()
    with patch(EXECUTOR, side_effect=slow.run):
        approve = asyncio.create_task(client.post(APPROVE.format(record.id), json={}))
        await asyncio.wait_for(slow.entered.wait(), 5)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        new = hass.data[DOMAIN]
        assert record.id in new.approvals_in_progress
        running = next(a for a in new.store.get_pending_approvals() if a["id"] == record.id)
        assert running["status"] == "pending" and running["execution_started_at"]

        slow.release.set()
        assert (await asyncio.wait_for(approve, 5)).status == 200
        await hass.async_block_till_done()
    assert record.id not in new.approvals_in_progress
    saved = next(a for a in _disk(hass_storage)["pending_approvals"] if a["id"] == record.id)
    assert saved["status"] == "approved" and not saved.get("closure_reason")
    await new.audit.async_save()
    methods = [row["method"] for row in hass_storage[AUDIT_STORAGE_KEY]["data"]["entries"]]
    assert "approval/executed" in methods and "approval/failed" not in methods


async def test_a_reload_keeps_queued_approvals_and_a_restart_does_not(
    hass, hass_storage, enable_custom_integrations,
):
    entry, data = await _setup(hass)
    record = await _approval(data, await _runner(data), "r1")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    queued = next(a for a in hass.data[DOMAIN].store.get_pending_approvals() if a["id"] == record.id)
    assert queued["status"] == "pending" and not queued.get("closure_reason")

    # A new process has no durable state, whatever the entry did before.
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.pop(phoenix._DURABLE_STATE_KEY)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    closed = next(a for a in hass.data[DOMAIN].store.get_pending_approvals() if a["id"] == record.id)
    assert closed["status"] == "cancelled" and closed["closure_reason"] == "phoenix_mcp_restarted"


async def test_a_marker_with_no_claim_is_still_resolved_on_reload(hass, enable_custom_integrations, caplog):
    """Keeping claims must not turn a dead executor into a permanent pending record."""
    entry, data = await _setup(hass)
    record = await _approval(data, await _runner(data), "r1")
    async with data.store.async_lock:
        data.store.get_pending_approvals()[-1]["execution_started_at"] = "2026-01-01T00:00:00+00:00"
        await data.store.async_save()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    row = next(a for a in hass.data[DOMAIN].store.get_pending_approvals() if a["id"] == record.id)
    assert row["status"] == "failed" and row["closure_reason"] == "execution_interrupted"
    assert "when Home Assistant" not in caplog.text


async def test_setup_reuses_every_storage_owner_across_reload(hass, enable_custom_integrations):
    entry, data = await _setup(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = hass.data[DOMAIN]
    for field in ("store", "audit", "conversations", "versions", "card_catalog", "config_recovery",
                  "agent_recovery", "dashboard_resources", "routing_statistics", "decision_budget",
                  "calendar_writes", "approvals_in_progress"):
        assert getattr(new, field) is getattr(data, field), field
    assert new.mesa is not data.mesa
    for field in ("storage", "backend", "ha_store", "lock"):
        assert getattr(new.mesa, field) is getattr(data.mesa, field), field


async def test_a_retired_mesa_runtime_cannot_drop_a_newer_profile(
    hass, hass_storage, enable_custom_integrations,
):
    entry, data = await _setup(hass)
    retired = data.mesa
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    current = hass.data[DOMAIN].mesa
    async with current.lock:
        current.backend.write("light.new", {"semantic_profile": {}})
        await current.async_save()
    async with retired.lock:
        retired.backend.write("light.old", {"semantic_profile": {}})
        await retired.async_save()
    assert sorted(hass_storage[MESA_STORAGE_KEY]["data"]["profiles"]) == ["light.new", "light.old"]
    assert current.backend.list_keys() == ["light.new", "light.old"]


async def test_a_save_begun_before_a_reload_holds_the_lock_the_new_runtime_waits_on(hass, enable_custom_integrations):
    entry, data = await _setup(hass)
    async with data.mesa.lock:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.data[DOMAIN].mesa.lock.locked()


async def test_a_reload_retries_a_mesa_store_that_failed_to_load(hass, hass_storage, enable_custom_integrations):
    """The corrupt-storage message tells the operator to repair the file, then reload."""
    hass_storage[MESA_STORAGE_KEY] = {"version": 1, "minor_version": 1, "key": MESA_STORAGE_KEY,
                                      "data": {"profiles": "broken"}}
    entry, data = await _setup(hass)
    assert data.mesa is None and data.mesa_setup_failed is True
    assert hass.data[phoenix._DURABLE_STATE_KEY].mesa is None
    hass_storage[MESA_STORAGE_KEY]["data"] = {"profiles": {"light.saved": {"semantic_profile": {}}}}
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    repaired = hass.data[DOMAIN]
    assert repaired.mesa_setup_failed is False
    assert repaired.mesa.backend.list_keys() == ["light.saved"]
    assert repaired.mesa.storage is hass.data[phoenix._DURABLE_STATE_KEY].mesa


async def test_a_new_process_reads_the_mesa_store_again(hass, hass_storage, enable_custom_integrations):
    entry, data = await _setup(hass)
    async with data.mesa.lock:
        data.mesa.backend.write("light.saved", {"semantic_profile": {}})
        await data.mesa.async_save()
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.pop(phoenix._DURABLE_STATE_KEY)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN].mesa.storage is not data.mesa.storage
    assert hass.data[DOMAIN].mesa.backend.list_keys() == ["light.saved"]


async def test_a_saved_log_reports_a_failed_write_and_a_reservation_refuses_one(hass, enable_custom_integrations):
    _, data = await _setup(hass)
    assert type(data.audit._store) is ReportingStore
    assert type(data.decision_budget._store) is DurableStore


async def test_a_reload_retries_an_unreadable_request_budget(hass, hass_storage, enable_custom_integrations):
    from custom_components.phoenix_mcp.routing.routing_tables import DECISION_BUDGET_STORAGE_KEY

    hass_storage[DECISION_BUDGET_STORAGE_KEY] = {"version": 1, "data": {"window": "x", "used": 0}}
    entry, data = await _setup(hass)
    assert data.decision_budget.summary(5)["available"] is False
    repaired = AsyncMock(return_value={"window": 1, "used": 0})
    with patch.object(data.decision_budget._store, "async_load", repaired):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    repaired.assert_awaited_once()
    assert hass.data[DOMAIN].decision_budget.summary(5)["available"] is True


async def test_a_reload_after_the_bad_ledger_was_deleted_clears_the_refusal(
    hass, hass_storage, enable_custom_integrations,
):
    from custom_components.phoenix_mcp.routing.routing_tables import DECISION_BUDGET_STORAGE_KEY

    hass_storage[DECISION_BUDGET_STORAGE_KEY] = {"version": 1, "data": {"window": "x", "used": 0}}
    entry, data = await _setup(hass)
    assert data.decision_budget.summary(5)["available"] is False
    del hass_storage[DECISION_BUDGET_STORAGE_KEY]
    # The storage fixture of older stacks serves the first read from the store's pending data.
    data.decision_budget._store._data = None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN].decision_budget.summary(5)["available"] is True


async def test_removal_uses_the_owned_store_and_a_new_entry_sees_it(
    hass, hass_storage, enable_custom_integrations,
):
    entry, data = await _setup(hass)
    key, _ = await data.store.async_create_access_key("alpha", "admin")
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert data.store.get_access_key_by_id(key.id) is None
    assert _names(_disk(hass_storage)["access_keys"]) == []


@pytest.mark.parametrize("change", ["reload", "unload"])
async def test_a_write_cancelled_after_its_runtime_went_still_holds_the_key_and_keeps_its_row(
    hass, hass_storage, enable_custom_integrations, change,
):
    """Recovery and audit belong to the process, so the barrier reaches whoever comes next."""
    entry, data = await _setup(hass)
    key, _ = await data.store.async_create_access_key("runner", "admin")
    key.cap_backup = "allow"
    started = asyncio.Event()

    async def held(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)

    with patch.dict(tool_catalog._TOOL_HANDLERS, {"create_backup": (held, (), {})}):
        task = asyncio.create_task(_dispatch_mcp(
            "tools/call", 1, {"name": "create_backup", "arguments": {}},
            key, hass, data, "10.0.0.5", "http://x"))
        await asyncio.wait_for(started.wait(), 5)
        if change == "reload":
            assert await hass.config_entries.async_reload(entry.entry_id)
        else:
            assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await hass.async_block_till_done()

    assert [row["access_key_id"] for row in hass_storage[RECOVERY_KEY]["data"]["records"].values()] == [key.id]
    if change == "reload":
        new = hass.data[DOMAIN]
        assert new is not data
        assert new.agent_recovery.blocks(key.id)
        assert [row.outcome for row in new.audit.query() if row.method == "request/cancelled"] == ["unknown"]
        assert new.access_key_counters[key.id]["request_count"] == 1
    else:
        await _stop(hass)
        await _final_write(hass)
        assert "request/cancelled" in _audit_methods(hass_storage)


async def _stop(hass) -> None:
    hass.set_state(CoreState.stopping)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()


async def _final_write(hass) -> None:
    hass.set_state(CoreState.final_write)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)


def _audit_methods(hass_storage) -> list[str]:
    return [row["method"] for row in hass_storage[AUDIT_STORAGE_KEY]["data"]["entries"]]


async def test_a_row_added_after_an_unload_with_no_setup_reaches_the_next_restart(
    hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user,
):
    entry, data = await _setup(hass)
    record = await _approval(data, await _runner(data), "r1")
    client = await hass_client()
    slow = _Slow()
    with patch(EXECUTOR, side_effect=slow.run):
        approve = asyncio.create_task(client.post(APPROVE.format(record.id), json={}))
        await asyncio.wait_for(slow.entered.wait(), 5)
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        slow.release.set()
        answer = await asyncio.wait_for(approve, 5)
    assert answer.status == 200
    assert "approval/executed" not in _audit_methods(hass_storage)
    await _stop(hass)
    await _final_write(hass)
    assert "approval/executed" in _audit_methods(hass_storage)


async def test_only_one_final_audit_flush_survives_any_number_of_reloads(
    hass, enable_custom_integrations,
):
    entry, data = await _setup(hass)
    for _ in range(3):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    with patch.object(data.audit, "async_save", AsyncMock()) as save:
        await _final_write(hass)
    save.assert_awaited_once()


async def test_a_failed_final_audit_flush_is_logged_and_contained(
    hass, enable_custom_integrations, caplog,
):
    _, data = await _setup(hass)
    with patch.object(data.audit, "async_save", AsyncMock(side_effect=OSError("disk"))):
        await _final_write(hass)
    assert "could not persist the audit log at the final write" in caplog.text


@pytest.mark.parametrize("failure", ["stopping", "disk"])
async def test_an_outcome_that_could_not_be_saved_is_reported_and_not_offered_again(
    hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user, failure,
):
    entry, data = await _setup(hass)
    record = await _approval(data, await _runner(data), "r1")
    client = await hass_client()
    slow = _Slow()
    claims: list[bool] = []
    hass.bus.async_listen(f"{DOMAIN}_approval_claimed", lambda event: claims.append(event.data["claimed"]))
    notify = patch("custom_components.phoenix_mcp.admin.approvals.create_approval_notification")
    refuse = AsyncMock(side_effect=DurableStorageError("no write"))
    with patch(EXECUTOR, side_effect=slow.run), notify as offered:
        approve = asyncio.create_task(client.post(APPROVE.format(record.id), json={}))
        await asyncio.wait_for(slow.entered.wait(), 5)
        if failure == "stopping":
            await _stop(hass)
            slow.release.set()
            answer = await asyncio.wait_for(approve, 5)
        else:
            with patch.object(data.store._store, "async_save", refuse):
                slow.release.set()
                answer = await asyncio.wait_for(approve, 5)
    await hass.async_block_till_done()
    assert answer.status == 500
    assert (await answer.json())["message_key"] == "adminError.approvalOutcomeNotSaved"
    offered.assert_not_called()
    assert claims == [True]
    assert record.id not in data.approvals_in_progress

    row = next(row for row in data.audit.query() if row.method == "approval/executed")
    assert row.outcome == "allowed" and json.loads(row.payload)["finalization"] == "not_saved"
    disk = next(a for a in _disk(hass_storage)["pending_approvals"] if a["id"] == record.id)
    assert disk["status"] == "pending" and disk["execution_started_at"]

    if failure == "disk":
        assert (await client.post(APPROVE.format(record.id), json={})).status == 409

    if failure == "stopping":
        await _final_write(hass)
        assert "approval/executed" in _audit_methods(hass_storage)
        assert await hass.config_entries.async_unload(entry.entry_id)
        hass.data.pop(phoenix._DURABLE_STATE_KEY)
        assert await hass.config_entries.async_setup(entry.entry_id)
    else:
        assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    disk = next(a for a in _disk(hass_storage)["pending_approvals"] if a["id"] == record.id)
    assert disk["status"] == "failed" and disk["closure_reason"] == "execution_interrupted"


async def test_the_shutdown_usage_flush_reaches_the_final_write(
    hass, hass_storage, enable_custom_integrations, caplog,
):
    """The stop event always arrives in `stopping`, where the write barrier refuses."""
    _, data = await _setup(hass)
    key, _ = await data.store.async_create_access_key("used", "admin")
    data.store.update_last_used(key.id, utcnow())

    hass.set_state(CoreState.stopping)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    hass.set_state(CoreState.final_write)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()

    assert "could not flush access key usage" not in caplog.text
    saved = next(k for k in _disk(hass_storage)["access_keys"] if k["id"] == key.id)
    assert saved["last_used_at"] is not None
    hass.set_state(CoreState.running)


async def test_the_shutdown_usage_flush_writes_once_and_holds_a_copy(
    hass, hass_storage, enable_custom_integrations,
):
    """Memory that moves on after the flush must not reach the final write through it."""
    _, data = await _setup(hass)
    key, _ = await data.store.async_create_access_key("used", "admin")
    data.store.update_last_used(key.id, utcnow())

    hass.set_state(CoreState.stopping)
    real_save = access_key_store.Store.async_save
    with patch.object(access_key_store.Store, "async_save", autospec=True, side_effect=real_save) as save:
        await data.store.async_flush_last_used()
        await data.store.async_flush_last_used()
    save.assert_awaited_once()
    data.store.get_pending_approvals().append({"id": "after-the-flush"})
    await _final_write(hass)
    assert "after-the-flush" not in [row["id"] for row in _disk(hass_storage)["pending_approvals"]]


async def test_the_shutdown_flush_keeps_the_barrier_for_everything_else(hass, enable_custom_integrations):
    from custom_components.phoenix_mcp.durable_storage import DurableStorageError

    _, data = await _setup(hass)
    hass.set_state(CoreState.stopping)
    with pytest.raises(DurableStorageError):
        await data.store.async_save()
    hass.set_state(CoreState.running)


async def test_a_failed_setup_detaches_debug_capture(hass, enable_custom_integrations):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    root = logging.getLogger("custom_components")
    before = list(root.handlers)
    with patch("custom_components.phoenix_mcp._audit_template_sandbox", side_effect=RuntimeError("boom")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert root.handlers == before
    assert "phoenix_mcp_debug_capture" not in hass.data


async def test_unload_names_only_the_platforms_this_entry_forwarded(hass, enable_custom_integrations):
    entry, data = await _setup(hass)
    data.forwarded_platforms.discard("ai_task")
    with patch.object(hass.config_entries, "async_unload_platforms",
                      wraps=hass.config_entries.async_unload_platforms) as unload:
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert unload.call_args.args[1] == ["sensor"]


async def test_removal_clears_the_ai_task_default(hass, hass_storage, enable_custom_integrations):
    from custom_components.phoenix_mcp import ai_task
    from custom_components.phoenix_mcp.llm.accounts import _get_secret_store

    entry, data = await _setup(hass)
    key, _ = await data.store.async_create_access_key("aitask", "admin")
    secrets = await _get_secret_store(hass)
    account = await secrets.add("chatgpt", {"api_key": "k", "model": "m"})
    await data.store.async_patch_settings(ai_task_enabled=True, ai_task_access_key_id=key.id,
                                           ai_task_provider_id=account, ai_task_model="m")
    data.async_sync_ai_task()
    await hass.async_block_till_done()
    assert ai_task.set_ai_task_preferred(hass, entry)["is_preferred"]
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ai_task._ai_task_preferences(hass).gen_data_entity_id is None


async def test_removal_forgets_the_tracked_assist_pipeline(hass, enable_custom_integrations):
    entry, data = await _setup(hass)
    await data.store.async_patch_settings(voice_agent_pipeline_id="pipeline")
    with patch("custom_components.phoenix_mcp.voice_agent.async_remove_assist_pipeline",
               AsyncMock(return_value=False)) as remove:
        assert await hass.config_entries.async_remove(entry.entry_id)
    assert remove.await_args.args[1].store is data.store


def test_every_first_party_import_in_setup_runs_off_the_event_loop():
    """A local import outside the list first executes on the event loop."""
    tree = ast.parse(Path(phoenix.__file__).read_text(encoding="utf-8"))
    top_level = {node.module for node in tree.body if isinstance(node, ast.ImportFrom) and node.level == 1}
    local = {
        node.module for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.ImportFrom) and node.level == 1
    }
    assert local - top_level - set(phoenix._SETUP_MODULES) == set()
    for name in phoenix._SETUP_MODULES:
        assert importlib.import_module(f"{phoenix.__package__}.{name}")


_FRESH_REGISTRIES = """
import json
import custom_components.phoenix_mcp as phoenix
from custom_components.phoenix_mcp import tool_catalog as catalog
def counts():
    return [len(catalog._TOOL_HANDLERS), len(catalog._EXECUTOR_REGISTRY), len(catalog._COMPANION_TURN)]
before = counts()
phoenix._import_setup_modules()
print(json.dumps({
    "before": before,
    "companion": len(catalog._COMPANION_TURN),
    "handlers": sorted(catalog._TOOL_HANDLERS),
    "executors": sorted(catalog._EXECUTOR_REGISTRY),
    "defined": sorted(catalog._NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(name, name) for name in catalog._TOOL_DEFS_BY_NAME),
}))
"""


def test_setup_imports_alone_fill_the_tool_registries():
    """A clean interpreter, because this suite's conftest imports the registering module for every test.

    Nothing else imports that module, so the setup list is what gives a running
    integration its tools and approval executors.
    """
    result = subprocess.run(
        [sys.executable, "-c", _FRESH_REGISTRIES], cwd=Path(phoenix.__file__).parents[2],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    fresh = json.loads(result.stdout.splitlines()[-1])
    assert fresh["before"] == [0, 0, 0], "importing the package must not register tools by itself"
    assert fresh["companion"] == 1
    assert fresh["handlers"] == fresh["defined"]
    assert fresh["executors"], "no approval executor registered"
    assert set(fresh["executors"]) - set(fresh["handlers"]) == {"call_service_mesa_approved"}


async def test_setup_loads_its_modules_in_the_import_executor(hass, enable_custom_integrations):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    with patch.object(hass, "async_add_import_executor_job",
                      wraps=hass.async_add_import_executor_job) as job:
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert any(call.args[0] is phoenix._import_setup_modules for call in job.call_args_list)


async def test_rotating_a_key_through_the_real_router(
    hass, enable_custom_integrations, hass_client, hass_client_no_auth, hass_admin_user,
):
    """The incident-response path: old bearer refused, new bearer works, once-only key."""
    _, data = await _setup(hass)
    key, raw = await data.store.async_create_access_key("compromised", "admin")
    fired = []
    hass.bus.async_listen("phoenix_mcp_access_key_rotated", fired.append)
    agent = await hass_client_no_auth()
    context = "/api/phoenix-mcp/context"
    assert (await agent.get(context, headers={"Authorization": f"Bearer {raw}"})).status == 200

    admin = await hass_client()
    response = await admin.post(f"{KEYS}/{key.id}/rotate", json={})
    assert response.status == 200
    body = await response.json()
    fresh = body["access_key"]
    assert fresh != raw and fresh.startswith("phx_")
    await hass.async_block_till_done()

    assert (await agent.get(context, headers={"Authorization": f"Bearer {raw}"})).status == 401
    assert (await agent.get(context, headers={"Authorization": f"Bearer {fresh}"})).status == 200
    assert [event.data["access_key_id"] for event in fired] == [key.id]
    assert any(row.resource.endswith(f"/{key.id}/rotate") and row.outcome == "allowed"
               for row in data.audit.query(limit=50))
    listed = await (await admin.get(f"{KEYS}/{key.id}")).json()
    assert fresh not in str(listed)
    assert (await admin.post(f"{KEYS}/missing/rotate", json={})).status == 404


async def test_deleting_an_archived_key_through_the_real_router(
    hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user,
):
    _, data = await _setup(hass)
    key, _ = await data.store.async_create_access_key("old", "admin")
    admin = await hass_client()
    assert (await admin.delete(f"{KEYS}/{key.id}")).status == 204
    assert _names(_disk(hass_storage)["archived_access_keys"]) == ["old"]
    assert (await admin.delete(f"{KEYS}/archived/{key.id}")).status == 204
    assert _disk(hass_storage)["archived_access_keys"] == []
    assert (await admin.delete(f"{KEYS}/archived/{key.id}")).status == 404


async def test_resolving_a_permission_through_the_real_router(
    hass, enable_custom_integrations, hass_client, hass_admin_user,
):
    _, data = await _setup(hass)
    key, _ = await data.store.async_create_access_key("reader", "admin")
    admin = await hass_client()
    response = await admin.get(f"{KEYS}/{key.id}/resolve/light.missing")
    assert response.status == 200
    assert (await response.json())["effective"] == "NOT_FOUND"
    assert (await admin.get(f"{KEYS}/{key.id}/resolve/not-an-entity")).status == 400
    assert (await admin.get(f"{KEYS}/missing/resolve/light.missing")).status == 404
