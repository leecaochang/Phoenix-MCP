"""Tests for the approvals module (PendingApproval CRUD, lifecycle, expiry)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from html import unescape
import json
from pathlib import Path
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant.core import callback
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.approvals import (
    PendingApproval,
    PendingApprovalCapacityError,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REJECTED,
    async_cancel_approvals_for_access_key,
    collect_pending_approvals_for_wipe,
    async_create_pending_approval,
    async_expire_overdue_approval_records,
    get_approval,
    fire_approval_requested_event,
    list_approvals,
    REASON_EXECUTION_INTERRUPTED,
    async_mark_execution_started,
    async_reconcile_interrupted_approvals,
    async_update_approval_status,
)
from custom_components.phoenix_mcp.approvals import create_approval_notification
from custom_components.phoenix_mcp.const import DOMAIN, MAX_PENDING_APPROVALS_PER_ACCESS_KEY
from custom_components.phoenix_mcp.access_key_store import GlobalSettings


class _FakeStore:
    """Minimal store-like object for tests."""

    def __init__(self) -> None:
        self._pending: list[dict] = []
        self.async_save = AsyncMock()
        self.async_lock = asyncio.Lock()
        self._settings = GlobalSettings()

    def get_pending_approvals(self) -> list[dict]:
        return self._pending

    def set_pending_approvals(self, approvals: list[dict]) -> None:
        self._pending = approvals

    def get_settings(self) -> GlobalSettings:
        return self._settings

    async def async_patch_settings(self, **kwargs) -> GlobalSettings:
        self._settings = GlobalSettings.from_dict({**self._settings.to_dict(), **kwargs})
        return self._settings


@pytest.fixture
def store() -> _FakeStore:
    return _FakeStore()


# --- async_create_pending_approval --------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    async def test_returns_record_with_pending_status(self, store):
        record = await async_create_pending_approval(
            store,
            token_id="t1",
            token_name="alice",
            tool_name="restart_ha",
            cap_name="cap_restart",
            args={},
            diff={"kind": "system_action", "summary": "Restart"},
            request_id="rid-1",
        )
        assert record.status == STATUS_PENDING
        assert record.token_id == "t1"
        assert record.tool_name == "restart_ha"
        assert record.id.startswith("appr_")

    @pytest.mark.asyncio
    async def test_persists_to_storage(self, store):
        await async_create_pending_approval(
            store,
            token_id="t1", token_name="alice", tool_name="restart_ha",
            cap_name="cap_restart", args={}, diff={}, request_id="rid",
        )
        assert len(store.get_pending_approvals()) == 1
        store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_expires_at_in_future(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        assert record.expires_at > utcnow()
        delta = record.expires_at - record.created_at
        assert 55 <= delta.total_seconds() <= 65

    @pytest.mark.asyncio
    async def test_per_token_capacity_enforced(self, store):
        for i in range(MAX_PENDING_APPROVALS_PER_ACCESS_KEY):
            await async_create_pending_approval(
                store, token_id="t1", token_name="a", tool_name="x",
                cap_name="cap_restart", args={"i": i}, diff={}, request_id=f"r{i}",
            )
        with pytest.raises(PendingApprovalCapacityError):
            await async_create_pending_approval(
                store, token_id="t1", token_name="a", tool_name="x",
                cap_name="cap_restart", args={}, diff={}, request_id="overflow",
            )

    @pytest.mark.asyncio
    async def test_capacity_is_per_token(self, store):
        # Fill t1 to capacity, then verify t2 can still create.
        for i in range(MAX_PENDING_APPROVALS_PER_ACCESS_KEY):
            await async_create_pending_approval(
                store, token_id="t1", token_name="a", tool_name="x",
                cap_name="cap_restart", args={}, diff={}, request_id=f"r{i}",
            )
        record = await async_create_pending_approval(
            store, token_id="t2", token_name="b", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r-other",
        )
        assert record.token_id == "t2"


# --- get / list ---------------------------------------------------------------


class TestGetAndList:
    @pytest.mark.asyncio
    async def test_get_returns_record(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
        )
        fetched = get_approval(store, record.id)
        assert fetched is not None
        assert fetched.id == record.id

    def test_get_returns_none_for_missing(self, store):
        assert get_approval(store, "appr_does_not_exist") is None

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, store):
        a = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        b = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        async with store.async_lock:
            await async_update_approval_status(store, b.id, status=STATUS_APPROVED)
        pending = list_approvals(store, status=STATUS_PENDING)
        approved = list_approvals(store, status=STATUS_APPROVED)
        assert [r.id for r in pending] == [a.id]
        assert [r.id for r in approved] == [b.id]

    @pytest.mark.asyncio
    async def test_list_filters_by_token(self, store):
        a = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        await async_create_pending_approval(
            store, token_id="t2", token_name="b", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        result = list_approvals(store, token_id="t1")
        assert [r.id for r in result] == [a.id]


# --- async_update_approval_status ---------------------------------------------------


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_approves_pending(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
        )
        async with store.async_lock:
            updated = await async_update_approval_status(
                store, record.id,
                status=STATUS_APPROVED,
                approved_by_user_id="user1",
                result={"ok": True},
            )
        assert updated.status == STATUS_APPROVED
        assert updated.approved_by_user_id == "user1"
        assert updated.result == {"ok": True}
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_idempotent_on_terminal(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
        )
        async with store.async_lock:
            await async_update_approval_status(store, record.id, status=STATUS_APPROVED)
            second = await async_update_approval_status(
                store, record.id, status=STATUS_REJECTED,
                rejected_reason="too_late",
            )
        # Second call observes the existing terminal state without overwriting.
        assert second.status == STATUS_APPROVED
        assert second.rejected_reason is None

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, store):
        async with store.async_lock:
            result = await async_update_approval_status(store, "appr_missing", status=STATUS_REJECTED)
        assert result is None


# --- async_cancel_approvals_for_access_key ----------------------------------------------


class TestCancelForToken:
    @pytest.mark.asyncio
    async def test_cancels_only_pending(self, store):
        a = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        b = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        async with store.async_lock:
            await async_update_approval_status(store, a.id, status=STATUS_APPROVED)
        cancelled = await async_cancel_approvals_for_access_key(store, "t1", "token_revoked")
        assert [c.id for c in cancelled] == [b.id]
        assert cancelled[0].status == STATUS_CANCELLED
        assert get_approval(store, b.id).status == STATUS_CANCELLED
        assert get_approval(store, a.id).status == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_does_not_touch_other_tokens(self, store):
        await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        b = await async_create_pending_approval(
            store, token_id="t2", token_name="b", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        await async_cancel_approvals_for_access_key(store, "t1", "token_revoked")
        assert get_approval(store, b.id).status == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_skips_in_progress_ids(self, store):
        # An approval whose saved action is mid-execution must not be flipped
        # out from under the executor, matching the expiry sweep's protection.
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        cancelled = await async_cancel_approvals_for_access_key(
            store, "t1", "token_revoked", skip_ids={record.id},
        )
        assert cancelled == []
        assert get_approval(store, record.id).status == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_token_cleanup_surfaces_a_stale_execution_as_interrupted(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        async with store.async_lock:
            assert await async_mark_execution_started(
                store, record.id, approved_by_user_id="admin",
            )

        async with store.async_lock:
            changed = await async_cancel_approvals_for_access_key(
                store, "t1", "token_revoked",
            )

        assert [item.id for item in changed] == [record.id]
        assert changed[0].status == STATUS_FAILED
        assert changed[0].rejected_reason == REASON_EXECUTION_INTERRUPTED
        assert "execution_started_at" not in store.get_pending_approvals()[0]


# --- collect_pending_approvals_for_wipe ---------------------------------------


class TestCollectForWipe:
    @pytest.mark.asyncio
    async def test_returns_cancelled_records_for_every_pending(self, store):
        a = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        b = await async_create_pending_approval(
            store, token_id="t2", token_name="b", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        async with store.async_lock:
            await async_update_approval_status(store, b.id, status=STATUS_APPROVED)

        collected = collect_pending_approvals_for_wipe(store, "phoenix_mcp_data_wiped")

        # Only the still-pending record; spans all tokens (no per-token filter).
        assert [c.id for c in collected] == [a.id]
        assert collected[0].status == STATUS_CANCELLED
        assert collected[0].rejected_reason == "phoenix_mcp_data_wiped"

    def test_read_only_does_not_mutate_store(self, store):
        # It must not persist or flip statuses: async_wipe clears the queue.
        store.set_pending_approvals([
            {"id": "ap1", "token_id": "gone", "tool_name": "x",
             "status": STATUS_PENDING, "created_at": "2026-07-10T00:00:00+00:00",
             "expires_at": "2026-07-10T01:00:00+00:00"},
        ])
        collect_pending_approvals_for_wipe(store, "phoenix_mcp_data_wiped")
        assert store.get_pending_approvals()[0]["status"] == STATUS_PENDING
        store.async_save.assert_not_called()


# --- async_expire_overdue_approval_records ------------------------------------------


class TestExpire:
    @pytest.mark.asyncio
    async def test_expires_overdue(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        # Backdate the record so it appears overdue.
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)
        expired = await async_expire_overdue_approval_records(store)
        assert len(expired) == 1
        assert get_approval(store, record.id).status == STATUS_EXPIRED

    @pytest.mark.asyncio
    async def test_does_not_expire_in_window(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        expired = await async_expire_overdue_approval_records(store)
        assert expired == []
        assert get_approval(store, record.id).status == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_does_not_re_expire_terminal(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        async with store.async_lock:
            await async_update_approval_status(store, record.id, status=STATUS_APPROVED)
        # Backdate again; this must not flip approved to expired.
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)
        expired = await async_expire_overdue_approval_records(store)
        assert expired == []
        assert get_approval(store, record.id).status == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_skips_in_progress_ids(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)

        expired = await async_expire_overdue_approval_records(store, skip_ids={record.id})

        assert expired == []
        assert get_approval(store, record.id).status == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_returns_expired_records(self, store):
        record = await async_create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)

        expired = await async_expire_overdue_approval_records(store)

        assert [approval.id for approval in expired] == [record.id]
        assert expired[0].status == STATUS_EXPIRED


# --- expiry archival cancels queued approvals ---------------------------------


class TestExpiredTokenCancelsApprovals:
    @pytest.mark.asyncio
    async def test_archive_expired_token_rechecks_current_expiry_under_lock(self, store):
        """A concurrent expiry extension wins over an older timer snapshot."""
        from custom_components.phoenix_mcp.helpers import async_archive_expired_access_key

        stale = MagicMock(id="tok-exp", name="alice")
        stale.expires_at = utcnow() - timedelta(seconds=1)
        current = MagicMock(id="tok-exp", name="alice")
        current.expires_at = utcnow() + timedelta(hours=1)
        store.get_access_key_by_id = MagicMock(return_value=current)
        store.async_archive_access_key = AsyncMock()
        data = MagicMock(
            store=store,
            approvals_in_progress=set(),
            expiry_timers={},
        )
        data.settings_update_lock = asyncio.Lock()
        hass = MagicMock()

        await async_archive_expired_access_key(hass, data, stale)

        store.async_archive_access_key.assert_not_awaited()
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_archive_expired_token_cancels_and_notifies(self, store):
        """Token expiry runs the same approval-queue hygiene as revoke: pending
        approvals flip to cancelled (reason token_expired), notifications are
        dismissed, and phoenix_mcp_approval_resolved fires for each."""
        from unittest.mock import MagicMock, patch

        from custom_components.phoenix_mcp.helpers import async_archive_expired_access_key

        record = await async_create_pending_approval(
            store, token_id="tok-exp", token_name="a", tool_name="restart_ha",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
        )
        store.async_archive_access_key = AsyncMock(return_value=MagicMock())

        data = MagicMock()
        data.store = store
        data.approvals_in_progress = set()
        data.expiry_timers = {}
        data.settings_update_lock = asyncio.Lock()
        data.async_on_access_key_archived = None
        hass = MagicMock()
        access_key = MagicMock()
        access_key.id = "tok-exp"
        access_key.name = "alice"
        access_key.expires_at = utcnow() - timedelta(seconds=1)
        store.get_access_key_by_id = MagicMock(return_value=access_key)

        with patch(
            "custom_components.phoenix_mcp.approvals.dismiss_approval_notification"
        ) as dismiss:
            await async_archive_expired_access_key(hass, data, access_key)

        assert get_approval(store, record.id).status == STATUS_CANCELLED
        assert get_approval(store, record.id).rejected_reason == "token_expired"
        dismiss.assert_called_once_with(hass, record.id)
        resolved = [
            c.args[1] for c in hass.bus.async_fire.call_args_list
            if c.args[0] == "phoenix_mcp_approval_resolved"
        ]
        assert [p["approval_id"] for p in resolved] == [record.id]

    async def test_archive_expired_token_clears_assist_binding(self, store):
        """Expiry clears the Assist binding + voice-agent token if they pointed at
        the expiring token, and re-syncs the voice agent."""
        from unittest.mock import MagicMock, patch

        from custom_components.phoenix_mcp.helpers import async_archive_expired_access_key

        await store.async_patch_settings(assist_bound_token_id="tok-exp", voice_agent_token_id="tok-exp")
        store.async_archive_access_key = AsyncMock(return_value=MagicMock())

        data = MagicMock()
        data.store = store
        data.approvals_in_progress = set()
        data.expiry_timers = {}
        data.settings_update_lock = asyncio.Lock()
        data.async_on_access_key_archived = None
        data.async_sync_voice_agent = MagicMock()
        hass = MagicMock()
        access_key = MagicMock()
        access_key.id = "tok-exp"
        access_key.name = "alice"
        access_key.expires_at = utcnow() - timedelta(seconds=1)
        store.get_access_key_by_id = MagicMock(return_value=access_key)

        with patch("custom_components.phoenix_mcp.approvals.dismiss_approval_notification"):
            await async_archive_expired_access_key(hass, data, access_key)

        assert store.get_settings().assist_bound_token_id is None
        assert store.get_settings().voice_agent_token_id is None
        data.async_sync_voice_agent.assert_called_once()


# --- to_dict / from_dict round trip -----------------------------------------


class TestRecordSerialization:
    def test_round_trip(self):
        original = PendingApproval(
            id="appr_test",
            token_id="t1",
            token_name="alice",
            tool_name="restart_ha",
            cap_name="cap_restart",
            args={"k": "v"},
            diff={"kind": "system_action", "summary": "Restart"},
            status=STATUS_PENDING,
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(seconds=60),
            request_id="rid",
        )
        roundtrip = PendingApproval.from_dict(original.to_dict())
        assert roundtrip.id == original.id
        assert roundtrip.tool_name == original.tool_name
        assert roundtrip.args == original.args
        assert roundtrip.diff == original.diff

    def test_is_terminal(self):
        for status in (
            STATUS_APPROVED,
            STATUS_REJECTED,
            STATUS_FAILED,
            STATUS_EXPIRED,
            STATUS_CANCELLED,
        ):
            r = PendingApproval(
                id="x", token_id="t", token_name="n", tool_name="x",
                cap_name="c", args={}, diff={}, status=status,
                created_at=utcnow(), expires_at=utcnow(), request_id="r",
            )
            assert r.is_terminal() is True
        r = PendingApproval(
            id="x", token_id="t", token_name="n", tool_name="x",
            cap_name="c", args={}, diff={}, status=STATUS_PENDING,
            created_at=utcnow(), expires_at=utcnow(), request_id="r",
        )
        assert r.is_terminal() is False


class TestNotificationGating:
    """create_approval_notification honours the notify_on_approval setting."""

    def _approval(self) -> PendingApproval:
        return PendingApproval(
            id="appr_x", token_id="t", token_name="codex", tool_name="call_service",
            cap_name="cap_physical_control", args={}, diff={}, status=STATUS_PENDING,
            created_at=utcnow(), expires_at=utcnow(), request_id="r",
        )

    def _data(self, *, notify: bool) -> MagicMock:
        data = MagicMock()
        data.store.get_settings = MagicMock(return_value=GlobalSettings(notify_on_approval=notify))
        return data

    def test_suppressed_when_disabled(self, hass):
        hass.data[DOMAIN] = self._data(notify=False)
        with patch("homeassistant.components.persistent_notification.async_create") as m:
            create_approval_notification(hass, self._approval())
            m.assert_not_called()

    def test_fired_when_enabled(self, hass):
        hass.data[DOMAIN] = self._data(notify=True)
        with patch("homeassistant.components.persistent_notification.async_create") as m:
            create_approval_notification(hass, self._approval())
            m.assert_called_once()
            assert "/phoenix-mcp/approvals/appr_x" in m.call_args.kwargs["message"]


@pytest.mark.parametrize("notify", [False, True])
async def test_requested_event_has_bound_review_link_without_configuration(hass, store, notify):
    data = MagicMock()
    data.store.get_settings.return_value = GlobalSettings(notify_on_approval=notify)
    hass.data[DOMAIN] = data
    secret = "sensitive-configuration-" + "x" * 100_000
    approval = await async_create_pending_approval(
        store, token_id="t1", token_name="Notification test", tool_name="write_file",
        cap_name="cap_filesystem",
        args={"content": secret, "review_url": "https://untrusted.example/approve"},
        diff={"before": secret, "after": secret, "targets": ["sensor.hidden"]},
        request_id="private-request", client_ip="192.0.2.1",
    )
    events = []

    @callback
    def capture(event):
        events.append(event.data)

    unsubscribe = hass.bus.async_listen(f"{DOMAIN}_approval_requested", capture)
    try:
        with patch("homeassistant.components.persistent_notification.async_create") as notification:
            create_approval_notification(hass, approval)
            fire_approval_requested_event(hass, approval)
            await hass.async_block_till_done()
        assert notification.call_count == int(notify)
        assert len(events) == 1
        event = events[0]
        assert event["approval_id"] == approval.id
        assert event["review_url"] == f"/phoenix-mcp/approvals/{approval.id}"
        assert set(event) == {
            "approval_id", "token_id", "token_name", "tool_name", "cap_name",
            "review_url", "expires_at", "timestamp",
        }
        encoded = json.dumps(event)
        assert len(encoded) < 1_024
        assert "sensitive-configuration" not in encoded
        assert "sensor.hidden" not in encoded and "untrusted.example" not in encoded
        assert get_approval(store, approval.id).status == STATUS_PENDING
    finally:
        unsubscribe()


async def test_requested_events_keep_identical_requests_separate(hass, store):
    approvals = [await async_create_pending_approval(
        store, token_id="t1", token_name="Notification test", tool_name="restart_ha",
        cap_name="cap_restart", args={}, diff={}, request_id=f"request-{number}",
    ) for number in range(2)]
    events = []

    @callback
    def capture(event):
        events.append(event.data)

    unsubscribe = hass.bus.async_listen(f"{DOMAIN}_approval_requested", capture)
    try:
        for approval in approvals:
            fire_approval_requested_event(hass, approval)
        await hass.async_block_till_done()
        assert [event["approval_id"] for event in events] == [approval.id for approval in approvals]
        assert len({event["review_url"] for event in events}) == 2
        assert all(event["review_url"].endswith(event["approval_id"]) for event in events)
        assert all(get_approval(store, approval.id).status == STATUS_PENDING for approval in approvals)
    finally:
        unsubscribe()


async def test_documented_notification_automation_only_opens_bound_review(hass, store):
    page = (Path(__file__).resolve().parents[1] / "docs/panel.html").read_text()
    example = re.search(r'<code id="approval-notification-example">(.*?)</code>', page, re.DOTALL)
    assert example is not None
    config = yaml.safe_load(unescape(example.group(1)))
    notifications = []

    @callback
    def notify(call):
        notifications.append(call.data)

    hass.services.async_register("notify", "mobile_app_your_device", notify)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    approvals = [await async_create_pending_approval(
        store, token_id="t1", token_name="Notification test", tool_name="restart_ha",
        cap_name="cap_restart", args={}, diff={}, request_id=f"request-{number}",
    ) for number in range(2)]
    for approval in approvals:
        fire_approval_requested_event(hass, approval)
    await hass.async_block_till_done()
    assert len(notifications) == 2
    for notification, approval in zip(notifications, approvals, strict=True):
        data = notification["data"]
        assert data["url"] == data["clickAction"] == f"/phoenix-mcp/approvals/{approval.id}"
        assert data["tag"] == f"phoenix_approval_{approval.id}"
        assert get_approval(store, approval.id).status == STATUS_PENDING
    for wrong_url in ("https://untrusted.example/approve", f"/phoenix-mcp/approvals/{approvals[1].id}"):
        hass.bus.async_fire(f"{DOMAIN}_approval_requested", {
            "approval_id": approvals[0].id, "review_url": wrong_url,
        })
    await hass.async_block_till_done()
    assert len(notifications) == 2
    assert all(get_approval(store, approval.id).status == STATUS_PENDING for approval in approvals)


class TestArgsRedaction:
    """Approval args are redacted in the admin-facing serialisation but kept raw
    on the persistence path so the approved-action executor can re-run them."""

    def _approval(self) -> PendingApproval:
        now = utcnow()
        return PendingApproval(
            id="a1", token_id="t", token_name="n", tool_name="write_file",
            cap_name="cap_filesystem",
            args={"path": "/config/secrets.yaml", "content": "api_key: abc123secret"},
            diff={}, status=STATUS_PENDING,
            created_at=now, expires_at=now, request_id="r",
        )

    def test_default_to_dict_redacts_args(self):
        d = self._approval().to_dict()
        assert "abc123secret" not in d["args"]["content"]
        assert d["args"]["path"] == "/config/secrets.yaml"

    def test_persistence_to_dict_keeps_raw_args(self):
        d = self._approval().to_dict(redact_args=False)
        assert d["args"]["content"] == "api_key: abc123secret"

    def test_executor_only_approval_bindings_are_never_projected(self):
        approval = self._approval()
        approval.args["_config_entry_private_identity_fingerprint"] = "private-hash"
        assert approval.to_dict()["args"][
            "_config_entry_private_identity_fingerprint"
        ] == "<redacted>"
        assert approval.to_dict(redact_args=False)["args"][
            "_config_entry_private_identity_fingerprint"
        ] == "private-hash"

    @pytest.mark.asyncio
    async def test_created_approval_persists_raw_args(self, store):
        await async_create_pending_approval(
            store, token_id="t", token_name="n", tool_name="write_file",
            cap_name="cap_filesystem",
            args={"content": "api_key: abc123secret"}, diff={}, request_id="r",
        )
        stored = store.get_pending_approvals()[0]
        assert stored["args"]["content"] == "api_key: abc123secret"


# --- interrupted execution ----------------------------------------------------


class TestInterruptedExecution:
    """A crash between the side effect and its recorded outcome.

    The in-memory claim in data.approvals_in_progress guards a double-click but dies
    with the process. The executor runs outside the store lock and the terminal
    status is written afterwards, so a stop in that window must leave a durable
    marker rather than an untouched pending record whose action may already have
    applied (approving it again would apply it twice).
    """

    async def _pending(self, store) -> str:
        approval = await async_create_pending_approval(
            store, token_id="t1", token_name="tok", tool_name="call_service",
            cap_name="cap_physical_control", args={}, diff={}, request_id="r",
        )
        return approval.id

    @pytest.mark.asyncio
    async def test_marker_is_written_before_the_executor_runs(self, store):
        approval_id = await self._pending(store)

        await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        )

        entry = store.get_pending_approvals()[0]
        # Still pending: the marker records that it MIGHT have applied, and the
        # record only becomes terminal once the executor actually reports.
        assert entry["status"] == STATUS_PENDING
        assert entry["approved_by_user_id"] == "admin"
        assert entry["approved_at"] == entry["execution_started_at"]
        assert entry["execution_started_at"] is not None

    @pytest.mark.asyncio
    async def test_a_restart_mid_execution_resolves_rather_than_re_offering(self, store):
        approval_id = await self._pending(store)
        await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        )

        # A new process reads the store back and reconciles.
        changed = await async_reconcile_interrupted_approvals(store)

        assert [a.id for a in changed] == [approval_id]
        entry = store.get_pending_approvals()[0]
        assert entry["status"] == STATUS_FAILED
        assert entry["rejected_reason"] == REASON_EXECUTION_INTERRUPTED
        # Approving it again is the one unrecoverable outcome, so the record must
        # not be pending any more.
        assert get_approval(store, approval_id).status != STATUS_PENDING

    @pytest.mark.asyncio
    async def test_reconciliation_skips_a_still_live_execution_claim(self, store):
        approval_id = await self._pending(store)
        await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        )

        changed = await async_reconcile_interrupted_approvals(
            store,
            skip_ids={approval_id},
        )

        assert changed == []
        entry = store.get_pending_approvals()[0]
        assert entry["status"] == STATUS_PENDING
        assert entry["execution_started_at"]

    @pytest.mark.asyncio
    async def test_an_untouched_pending_approval_is_left_alone(self, store):
        # The whole point is telling "nobody acted on this" apart from "someone started
        # acting and the answer was lost". Resolving both would throw away every
        # approval waiting in the queue at a restart.
        approval_id = await self._pending(store)

        changed = await async_reconcile_interrupted_approvals(store)

        assert changed == []
        assert get_approval(store, approval_id).status == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_a_completed_execution_clears_the_marker(self, store):
        approval_id = await self._pending(store)
        await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        )

        await async_update_approval_status(
            store, approval_id, status=STATUS_APPROVED, approved_by_user_id="admin",
        )

        entry = store.get_pending_approvals()[0]
        assert "execution_started_at" not in entry
        # And a later startup has nothing to reconcile.
        assert await async_reconcile_interrupted_approvals(store) == []

    @pytest.mark.asyncio
    async def test_an_unwritable_store_refuses_rather_than_running_unmarked(self, store):
        """Fail closed: an unwritable store refuses the approval instead of running unmarked.

        The same store persists the terminal status afterwards, so a disk that cannot
        take the marker cannot record the outcome either. Executing anyway leaves a
        pending record whose action has run with no trace saying so, which is the
        replay the marker exists to prevent.
        """
        approval_id = await self._pending(store)
        store.async_save = AsyncMock(side_effect=OSError("disk full"))

        assert await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        ) is False
        # Still pending and still approvable once the disk is writable, with no
        # half-written marker left to be reconciled away at the next startup.
        assert get_approval(store, approval_id).status == STATUS_PENDING
        assert "execution_started_at" not in store.get_pending_approvals()[0]

    @pytest.mark.asyncio
    async def test_a_durable_marker_reports_success(self, store):
        approval_id = await self._pending(store)
        assert await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        ) is True

    @pytest.mark.asyncio
    async def test_an_already_resolved_approval_reports_failure(self, store):
        # Nothing to mark, so nothing may run: the caller treats False as "do not
        # execute", and a terminal record must never be executed again.
        approval_id = await self._pending(store)
        await async_update_approval_status(
            store, approval_id, status=STATUS_APPROVED, approved_by_user_id="admin")

        assert await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        ) is False

    @pytest.mark.asyncio
    async def test_the_marker_survives_a_store_round_trip(self, store):
        # It is only worth anything if it is still there in the NEXT process.
        approval_id = await self._pending(store)
        await async_mark_execution_started(
            store, approval_id, approved_by_user_id="admin",
        )

        reloaded = PendingApproval.from_dict(store.get_pending_approvals()[0])

        assert reloaded.execution_started_at is not None
        assert PendingApproval.from_dict(
            reloaded.to_dict(redact_args=False)
        ).execution_started_at == reloaded.execution_started_at


async def test_terminal_retention_bounds_history_but_preserves_uncertain_and_pending(store):
    from custom_components.phoenix_mcp.approvals import async_prune_terminal_approvals
    from custom_components.phoenix_mcp.const import APPROVAL_HISTORY_LIMIT
    now = utcnow()
    old = (now - timedelta(hours=2)).isoformat()
    rows = [{"id": str(i), "status": STATUS_APPROVED, "resolved_at": old,
             "args": {"content": "private"}, "diff": {"before": "private"}, "result": "private"}
            for i in range(APPROVAL_HISTORY_LIMIT + 30)]
    protected = [
        {"id": "pending", "status": STATUS_PENDING, "args": {"content": "retain"}},
        {"id": "interrupted", "status": STATUS_FAILED, "rejected_reason": REASON_EXECUTION_INTERRUPTED, "args": {"content": "retain"}},
        {"id": "marker", "status": STATUS_APPROVED, "execution_started_at": old},
        {"id": "claimed", "status": STATUS_APPROVED, "resolved_at": old},
        {"id": "corrupt-time", "status": STATUS_APPROVED, "resolved_at": 123},
    ]
    rows += protected + [{"id": "too-old", "status": STATUS_REJECTED,
                          "resolved_at": (now - timedelta(days=8)).isoformat()}]
    store.set_pending_approvals(rows)
    await async_prune_terminal_approvals(store, skip_ids={"claimed"})
    retained = store.get_pending_approvals()
    assert len(retained) == APPROVAL_HISTORY_LIMIT + len(protected)
    assert retained[:len(protected)] == protected
    assert all(row["args"] == {} and row["diff"] == {} and row["result"] is None for row in retained[len(protected):])
    assert not any(row["id"] == "too-old" for row in retained)
    store.async_save.reset_mock()
    await async_prune_terminal_approvals(store, skip_ids={"claimed"})
    store.async_save.assert_not_awaited()


async def test_failed_retention_persistence_restores_original_payloads(store):
    from custom_components.phoenix_mcp.approvals import async_prune_terminal_approvals
    original = [{"id": "old", "status": STATUS_APPROVED, "args": {"content": "retain"},
                 "resolved_at": (utcnow() - timedelta(hours=2)).isoformat()}]
    store.set_pending_approvals(original)
    store.async_save.side_effect = OSError("unwritable")
    with pytest.raises(OSError):
        await async_prune_terminal_approvals(store)
    assert store.get_pending_approvals() == original
