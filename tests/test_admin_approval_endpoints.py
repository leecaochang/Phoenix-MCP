"""The admin approval endpoints, through HA's real router against a real store and audit log.

Records are laid into the running store directly, so each test states its starting
queue; everything after that is an HTTP request. Only the executor is replaced.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import callback
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.approvals import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from custom_components.phoenix_mcp.const import DOMAIN, ACCESS_KEY_PREFIX
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord


# ---- helpers -----------------------------------------------------------------


def _make_access_key(access_key_id: str = "tok-1", name: str = "alice", **caps) -> AccessKeyRecord:
    raw = ACCESS_KEY_PREFIX + secrets.token_hex(32)
    return AccessKeyRecord(
        id=access_key_id,
        name=name,
        access_key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_at=utcnow(),
        created_by="admin",
        **caps,
    )


def _make_pending(approval_id: str, access_key_id: str = "tok-1", **kwargs) -> dict:
    now = utcnow()
    base = {
        "id": approval_id,
        "access_key_id": access_key_id,
        "access_key_name": "alice",
        "tool_name": "restart_ha",
        "cap_name": "cap_restart",
        "args": {},
        "diff": {"kind": "system_action", "summary": "Restart"},
        "status": STATUS_PENDING,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=3600)).isoformat(),
        "resolved_at": None,
        "approved_by_user_id": None,
        "rejected_reason": None,
        "result": None,
        "request_id": "rid-1",
        "client_ip": None,
    }
    base.update(kwargs)
    return base


APPROVALS = "/api/phoenix-mcp/admin/approvals"
APPROVAL = APPROVALS + "/{approval_id}"
APPROVE = APPROVAL + "/approve"
REJECT = APPROVAL + "/reject"
BATCH_APPROVE = APPROVALS + "/batch/approve"
RUNTIME = SimpleNamespace()


@pytest.fixture(autouse=True)
async def runtime(hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user):
    """A set-up entry, its live store and an authenticated admin client."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    claims: list[tuple[str, bool]] = []

    @callback
    def _claimed(event) -> None:
        claims.append((event.data["approval_id"], event.data["claimed"]))

    hass.bus.async_listen(f"{DOMAIN}_approval_claimed", _claimed)
    RUNTIME.__dict__.update(hass=hass, data=hass.data[DOMAIN], client=await hass_client(),
                            admin_id=hass_admin_user.id, claims=claims)
    store = hass.data[DOMAIN].store
    store.get_settings().mesa_mode = "off"
    audit = hass.data[DOMAIN].audit
    audit.record = MagicMock(wraps=audit.record)
    yield
    store.__dict__.pop("async_save", None)


def _make_store(pending: list[dict] | None = None, access_keys: list[AccessKeyRecord] | None = None,
                kill_switch: bool = False):
    """Lay the starting queue and keys into the live store."""
    store = RUNTIME.data.store
    store.set_pending_approvals(list(pending or []))
    for access_key in access_keys or []:
        store._access_keys[access_key.id] = access_key
    store.get_settings().kill_switch = kill_switch
    return store


def _make_data(_store):
    return RUNTIME.data


def _make_admin_request(body: bytes = b"", query: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(body=body, query=query or {})


class _Route:
    """One admin endpoint; a call is a real authenticated HTTP request."""

    def __init__(self, path: str) -> None:
        self.path = path

    async def _send(self, method: str, request: SimpleNamespace, ids: dict) -> SimpleNamespace:
        response = await RUNTIME.client.request(
            method, self.path.format(**ids), data=request.body or None, params=request.query,
            headers={"Content-Type": "application/json"})
        return SimpleNamespace(status=response.status, text=await response.text())

    async def get(self, request: SimpleNamespace, **ids: str) -> SimpleNamespace:
        return await self._send("GET", request, ids)

    async def post(self, request: SimpleNamespace, **ids: str) -> SimpleNamespace:
        return await self._send("POST", request, ids)

    async def delete(self, request: SimpleNamespace, **ids: str) -> SimpleNamespace:
        return await self._send("DELETE", request, ids)


# ---- list view ---------------------------------------------------------------


class TestApprovalsList:
    @pytest.mark.asyncio
    async def test_returns_all_when_no_filter(self):
        _make_store(pending=[
            _make_pending("appr_a"),
            _make_pending("appr_b", status=STATUS_APPROVED),
        ])
        view = _Route(APPROVALS)
        request = _make_admin_request()

        resp = await view.get(request)

        body = json.loads(resp.text)
        assert body["total"] == 2

    @pytest.mark.asyncio
    async def test_filters_by_status(self):
        _make_store(pending=[
            _make_pending("appr_a"),
            _make_pending("appr_b", status=STATUS_APPROVED),
        ])
        view = _Route(APPROVALS)
        request = _make_admin_request(query={"status": STATUS_PENDING})

        resp = await view.get(request)

        body = json.loads(resp.text)
        assert body["total"] == 1
        assert body["approvals"][0]["id"] == "appr_a"

    @pytest.mark.asyncio
    async def test_filters_by_access_key(self):
        _make_store(pending=[
            _make_pending("appr_a", access_key_id="tok-1"),
            _make_pending("appr_b", access_key_id="tok-2"),
        ])
        view = _Route(APPROVALS)
        request = _make_admin_request(query={"access_key_id": "tok-1"})

        resp = await view.get(request)

        body = json.loads(resp.text)
        assert body["total"] == 1
        assert body["approvals"][0]["id"] == "appr_a"


# ---- detail view -------------------------------------------------------------


class TestApprovalDetail:
    @pytest.mark.asyncio
    async def test_returns_record(self):
        _make_store(pending=[_make_pending("appr_a")])
        view = _Route(APPROVAL)
        request = _make_admin_request()

        resp = await view.get(request, approval_id="appr_a")

        body = json.loads(resp.text)
        assert body["id"] == "appr_a"
        assert body["tool_name"] == "restart_ha"

    @pytest.mark.asyncio
    async def test_returns_404_for_missing(self):
        _make_store(pending=[])
        view = _Route(APPROVAL)
        request = _make_admin_request()

        resp = await view.get(request, approval_id="appr_missing")

        assert resp.status == 404


# ---- delete (cancel alias) ---------------------------------------------------


class TestApprovalDelete:
    @pytest.mark.asyncio
    async def test_delete_cancels_pending(self):
        _make_store(pending=[_make_pending("appr_a")])
        view = _Route(APPROVAL)
        request = _make_admin_request()

        with \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.delete(request, approval_id="appr_a")

        body = json.loads(resp.text)
        assert body["status"] == STATUS_CANCELLED
        assert body["rejected_reason"] == "admin_cancelled"

    @pytest.mark.asyncio
    async def test_delete_idempotent_on_terminal(self):
        _make_store(pending=[_make_pending("appr_a", status=STATUS_APPROVED)])
        view = _Route(APPROVAL)
        request = _make_admin_request()

        resp = await view.delete(request, approval_id="appr_a")

        body = json.loads(resp.text)
        # Idempotent: already-terminal record returns its current state, not cancelled.
        assert body["status"] == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_delete_rejected_when_already_in_progress(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        data.approvals_in_progress.add("appr_a")
        view = _Route(APPROVAL)
        request = _make_admin_request()

        resp = await view.delete(request, approval_id="appr_a")

        assert resp.status == 409
        assert next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")["status"] == STATUS_PENDING


# ---- reject view -------------------------------------------------------------


class TestApprovalReject:
    @pytest.mark.asyncio
    async def test_reject_with_reason(self):
        _make_store(pending=[_make_pending("appr_a")])
        view = _Route(REJECT)
        body = json.dumps({"reason": "not_safe"}).encode()
        request = _make_admin_request(body=body)

        with \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_REJECTED
        assert out["rejected_reason"] == "not_safe"

    @pytest.mark.asyncio
    async def test_reject_without_body(self):
        _make_store(pending=[_make_pending("appr_a")])
        view = _Route(REJECT)
        request = _make_admin_request(body=b"{}")

        with \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_REJECTED
        assert out["rejected_reason"] is None

    @pytest.mark.asyncio
    async def test_reject_rejected_when_already_in_progress(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        data.approvals_in_progress.add("appr_a")
        view = _Route(REJECT)
        request = _make_admin_request(body=b"{}")

        resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        assert next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")["status"] == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_reject_preserves_a_stale_execution_marker(self):
        pending = _make_pending("appr_a")
        pending["execution_started_at"] = utcnow().isoformat()
        store = _make_store(pending=[pending])
        view = _Route(REJECT)

        response = await view.post(
            _make_admin_request(body=b"{}"), approval_id="appr_a",
        )

        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert response.status == 409
        assert stored["status"] == STATUS_PENDING
        assert stored["execution_started_at"]


# ---- approve view ------------------------------------------------------------


class TestApprovalApprove:
    @pytest.mark.asyncio
    async def test_approve_runs_executor_and_marks_approved(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_APPROVED
        assert out["result"]["outcome"] == "allowed"
        assert out["approved_by_user_id"] == RUNTIME.admin_id

    @pytest.mark.asyncio
    async def test_approve_marks_execution_on_disk_before_running_the_executor(self):
        """The execution marker is on disk before the executor runs.

        approvals_in_progress only guards a double-click within one process. The
        executor runs outside the store lock and its outcome is written afterwards, so
        a restart in that window must find a marker, not an untouched pending record
        whose action may already have applied. Observed from inside the executor.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")
        seen: dict = {}

        async def _observing_executor(name, args, tok, hass, data):
            entry = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
            seen["marker"] = entry.get("execution_started_at")
            seen["approved_at"] = entry.get("approved_at")
            seen["approved_by_user_id"] = entry.get("approved_by_user_id")
            seen["status"] = entry["status"]
            seen["audit_methods"] = [
                call.kwargs["method"] for call in data.audit.record.call_args_list
            ]
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_observing_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 200
        assert seen["marker"] is not None, (
            "execution_started_at was not persisted before the executor ran"
        )
        assert seen["approved_at"] == seen["marker"]
        assert seen["approved_by_user_id"] == RUNTIME.admin_id
        assert seen["audit_methods"] == ["approval/approved"]
        assert seen["status"] == STATUS_PENDING
        # Cleared once the outcome is recorded, so the history carries no
        # half-finished marker and a later startup has nothing to reconcile.
        assert "execution_started_at" not in next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")

    @pytest.mark.asyncio
    async def test_approve_refuses_when_the_marker_cannot_be_persisted(self):
        """Fail closed: no durable marker, no execution.

        The same store records the terminal status afterwards, so a disk that cannot
        take the marker cannot record the outcome either. Running anyway would leave a
        pending record whose action has run with nothing saying so.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        store.async_save = AsyncMock(side_effect=OSError("disk full"))
        data = _make_data(store)
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        executor = AsyncMock()
        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 503
        executor.assert_not_called()
        # Still pending, and the claim released so a retry is possible once the
        # store is writable again.
        assert next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")["status"] == STATUS_PENDING
        assert "appr_a" not in data.approvals_in_progress

    @pytest.mark.asyncio
    async def test_approve_rejected_when_already_in_progress(self):
        # Double-run race guard: an approve whose id is already claimed by a
        # concurrent in-flight approve returns 409 and never runs the executor.
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        data = _make_data(store)
        data.approvals_in_progress.add("appr_a")
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        executor = AsyncMock()
        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        executor.assert_not_called()
        # Untouched: still pending for the in-flight request to finalize.
        assert next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")["status"] == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_approve_cannot_claim_during_core_wipe(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        data = _make_data(store)
        data.core_wipe_in_progress = True
        view = _Route(APPROVE)
        executor = AsyncMock()

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor):
            response = await view.post(
                _make_admin_request(body=b"{}"), approval_id="appr_a",
            )

        assert response.status == 409
        assert json.loads(response.text)["message_key"] == "adminError.approvalWipeInProgress"
        executor.assert_not_awaited()
        assert next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")["status"] == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_two_admin_clients_cannot_execute_the_same_approval_twice(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def _blocked_executor(name, args, tok, hass, data):
            started.set()
            await finish.wait()
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        executor = AsyncMock(side_effect=_blocked_executor)
        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            first_task = asyncio.create_task(
                view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")
            )
            await started.wait()
            second_response = await view.post(
                _make_admin_request(body=b"{}"), approval_id="appr_a",
            )
            finish.set()
            first_response = await first_task

        assert first_response.status == 200
        assert second_response.status == 409
        assert json.loads(first_response.text)["status"] == STATUS_APPROVED
        assert executor.await_count == 1

    @pytest.mark.asyncio
    async def test_approve_at_the_exact_deadline_expires_without_running(self):
        """The deadline instant itself counts as overdue, as the sweep treats it."""
        deadline = utcnow() + timedelta(hours=1)
        pending = _make_pending("appr_a", access_key_id="tok-1", expires_at=deadline.isoformat())
        store = _make_store(pending=[pending], access_keys=[_make_access_key("tok-1", cap_restart="confirm")])
        view = _Route(APPROVE)
        executor = AsyncMock()

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("custom_components.phoenix_mcp.admin_view.utcnow", return_value=deadline), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("custom_components.phoenix_mcp.admin_view.fire_approval_resolved_event"):
            response = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")

        assert response.status == 409
        assert next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")["status"] == STATUS_EXPIRED
        executor.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_approve_expires_an_overdue_record_before_claiming_it(self):
        """An overdue request answers approvalExpired on Approve and closes as expired without running."""
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        pending = _make_pending(
            "appr_a",
            access_key_id="tok-1",
            expires_at=(utcnow() - timedelta(seconds=1)).isoformat(),
        )
        store = _make_store(pending=[pending], access_keys=[access_key])
        data = _make_data(store)
        view = _Route(APPROVE)
        executor = AsyncMock()

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("custom_components.phoenix_mcp.admin_view.fire_approval_resolved_event") as resolved:
            response = await view.post(
                _make_admin_request(body=b"{}"), approval_id="appr_a",
            )

        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert response.status == 409
        assert json.loads(response.text)["message_key"] == "adminError.approvalExpired"
        assert stored["status"] == STATUS_EXPIRED
        executor.assert_not_awaited()
        resolved.assert_called_once()
        assert resolved.call_args.args[1].status == STATUS_EXPIRED
        assert data.audit.record.call_args.kwargs["method"] == "approval/expired"

    @pytest.mark.asyncio
    async def test_reject_at_the_exact_deadline_closes_as_expired(self):
        """The deadline instant itself counts as overdue, as the sweep treats it."""
        deadline = utcnow() + timedelta(hours=1)
        pending = _make_pending("appr_a", access_key_id="tok-1", expires_at=deadline.isoformat())
        store = _make_store(pending=[pending], access_keys=[_make_access_key("tok-1", cap_restart="confirm")])
        view = _Route(REJECT)

        with (patch("custom_components.phoenix_mcp.admin_view.utcnow", return_value=deadline),
              patch("homeassistant.components.persistent_notification.async_dismiss")):
            response = await view.post(
                _make_admin_request(body=b'{"reason": "not now"}'), approval_id="appr_a",
            )

        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert response.status == 200
        assert stored["status"] == STATUS_EXPIRED

    @pytest.mark.asyncio
    async def test_reject_closes_an_overdue_record_as_expired(self):
        """An overdue request times out on Reject too, not only on Approve."""
        pending = _make_pending(
            "appr_a",
            access_key_id="tok-1",
            expires_at=(utcnow() - timedelta(seconds=1)).isoformat(),
        )
        store = _make_store(pending=[pending], access_keys=[_make_access_key("tok-1", cap_restart="confirm")])
        data = _make_data(store)
        view = _Route(REJECT)

        with patch("homeassistant.components.persistent_notification.async_dismiss"):
            response = await view.post(
                _make_admin_request(body=b'{"reason": "not now"}'), approval_id="appr_a",
            )

        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert response.status == 200
        assert stored["status"] == STATUS_EXPIRED
        assert stored.get("rejected_reason") is None
        assert stored.get("approved_by_user_id") is None
        assert data.audit.record.call_args.kwargs["method"] == "approval/expired"

    @pytest.mark.asyncio
    async def test_approve_reads_settings_after_waiting_for_the_store_lock(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        executor = AsyncMock()
        queued = asyncio.Event()

        async def _patch_kill_switch():
            queued.set()
            async with store.async_lock:
                store.get_settings().kill_switch = True

        await store.async_lock.acquire()
        update_task = asyncio.create_task(_patch_kill_switch())
        await queued.wait()
        approve_task = asyncio.create_task(
            view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")
        )
        await asyncio.sleep(0)
        store.async_lock.release()

        await update_task
        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            response = await approve_task

        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert response.status == 503
        assert stored["status"] == STATUS_CANCELLED
        assert stored["rejected_reason"] == "kill_switch"
        executor.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_approve_releases_in_progress_claim(self):
        # The claim is released after execution so the id is not stuck.
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        data = _make_data(store)
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert json.loads(resp.text)["status"] == STATUS_APPROVED
        assert "appr_a" not in data.approvals_in_progress

    @pytest.mark.asyncio
    async def test_approve_conflicts_if_record_resolved_during_execution(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        data = _make_data(store)
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        async def _executor_that_resolves_elsewhere(name, args, tok, hass, data):
            store.get_pending_approvals()[0]["status"] = STATUS_REJECTED
            store.get_pending_approvals()[0]["rejected_reason"] = "admin_cancelled"
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_executor_that_resolves_elsewhere), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        assert store.get_pending_approvals()[0]["status"] == STATUS_REJECTED
        assert [call.kwargs["method"] for call in data.audit.record.call_args_list] == [
            "approval/approved",
            "approval/executed",
        ]
        audit_kwargs = data.audit.record.call_args_list[-1].kwargs
        assert audit_kwargs["outcome"] == "allowed"
        assert audit_kwargs["payload"] == {
            "finalization": "conflict",
            "stored_status": STATUS_REJECTED,
            "executor_outcome": "allowed",
        }
        assert "appr_a" not in data.approvals_in_progress

    @pytest.mark.asyncio
    async def test_approve_rejected_when_executor_returns_error(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        async def _failing_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": "Restart failed."}], "isError": True}, "denied", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_failing_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_FAILED
        assert out["rejected_reason"] == "execution_failed"

    @pytest.mark.asyncio
    async def test_an_unconfirmed_write_closes_as_interrupted_not_failed(self):
        from custom_components.phoenix_mcp.tool_common import (
            _tool_uncertain,
            approval_outcome,
            execution_failure_text,
        )

        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)

        async def _executor(name, args, tok, hass, data):
            return _tool_uncertain({"status": "unknown", "note": "may have landed"}), "invalid_request", "x"

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_FAILED
        assert out["rejected_reason"] == "execution_interrupted"
        record = SimpleNamespace(rejected_reason=out["rejected_reason"], result=out["result"])
        assert execution_failure_text(record) is None
        status, message = approval_outcome(STATUS_FAILED, record)
        assert status == STATUS_FAILED
        assert "may have partly applied" in message and "may have landed" in message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("refused", [True, False])
    async def test_approve_records_an_executor_refusal_made_before_writing(self, refused):
        # Only a RefusedBeforeWrite result is persisted as known-unapplied; the
        # type cannot survive storage, so the record carries a server-owned flag.
        from custom_components.phoenix_mcp.tool_common import _tool_error, _tool_refused_before_write

        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")
        text = "This configuration changed since you last read it (expected_hash no longer matches)."

        async def _executor(name, args, tok, hass, data):
            result = _tool_refused_before_write(text) if refused else _tool_error(text)
            return result, "invalid_request", "restart_ha"

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_FAILED and out["rejected_reason"] == "execution_failed"
        assert out["result"].get("refused_before_write") is (True if refused else None)

    @pytest.mark.asyncio
    async def test_approve_cancels_when_token_revoked(self):
        # No access key in store, so get_access_key_by_id returns None.
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        with \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 404
        assert json.loads(resp.text)["error"] == "not_found"
        # Storage shows the approval cancelled.
        cancelled = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert cancelled["status"] == STATUS_CANCELLED
        assert cancelled["rejected_reason"] == "access_key_inactive"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("batch", [False, True])
    @pytest.mark.parametrize("fields", [
        {"expires_at": utcnow() - timedelta(seconds=1)}, {"revoked": True},
    ], ids=["expired", "revoked"])
    async def test_approve_closes_for_a_key_still_listed_but_no_longer_valid(self, fields, batch):
        """A key leaves the active set lazily, so the listing alone does not prove it may act."""
        access_key = _make_access_key("tok-1", cap_restart="confirm", **fields)
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(BATCH_APPROVE) if batch else _Route(APPROVE)
        body = json.dumps({"approval_ids": ["appr_a"]}).encode() if batch else b"{}"
        executor = AsyncMock()

        with patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await (view.post(_make_admin_request(body=body)) if batch
                          else view.post(_make_admin_request(body=body), approval_id="appr_a"))

        out = json.loads(resp.text)
        if batch:
            assert out["applied"] == [] and out["failed"]["approval_id"] == "appr_a"
        else:
            assert resp.status == 404 and out["error"] == "not_found"
        executor.assert_not_awaited()
        cancelled = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert cancelled["status"] == STATUS_CANCELLED
        assert cancelled["rejected_reason"] == "access_key_inactive"

    @pytest.mark.asyncio
    async def test_approve_rejected_when_cap_now_deny(self):
        access_key = _make_access_key("tok-1", cap_restart="deny")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        with \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 403
        assert json.loads(resp.text)["error"] == "forbidden"
        rejected = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert rejected["status"] == STATUS_FAILED
        assert rejected["rejected_reason"] == "capability_denied"

    @pytest.mark.asyncio
    async def test_approve_mesa_sentinel_cap_skips_effective_cap_recheck(self):
        from custom_components.phoenix_mcp.const import MESA_APPROVED_EXECUTOR, MESA_CONFIRM_CAP

        # The MESA sentinel cap is not a real access key capability, so effective_cap
        # would auto-deny it. The approve path must skip that recheck and execute
        # (MESA re-validation lives inside the executor instead).
        access_key = _make_access_key("tok-1")  # no mesa cap; effective_cap would be deny
        pending = _make_pending(
            "appr_m",
            access_key_id="tok-1",
            cap_name=MESA_CONFIRM_CAP,
            tool_name=MESA_APPROVED_EXECUTOR,
            args={"domain": "light", "service": "turn_on", "entity_id": ["light.a"]},
        )
        _make_store(pending=[pending], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            assert name == MESA_APPROVED_EXECUTOR
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "svc")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_m")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_approve_cancels_when_kill_switch_engaged(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(
            pending=[_make_pending("appr_a", access_key_id="tok-1")],
            access_keys=[access_key],
            kill_switch=True,
        )
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        with \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 503
        cancelled = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert cancelled["status"] == STATUS_CANCELLED
        assert cancelled["rejected_reason"] == "kill_switch"

    @pytest.mark.asyncio
    async def test_approve_idempotent_on_terminal(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(
            pending=[_make_pending("appr_a", access_key_id="tok-1", status=STATUS_APPROVED)],
            access_keys=[access_key],
        )
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_APPROVED  # already terminal; returned as-is

    @pytest.mark.asyncio
    async def test_approve_404_for_missing(self):
        _make_store(pending=[])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        resp = await view.post(request, approval_id="appr_missing")

        assert resp.status == 404


# ---- audit logging on resolution -------------------------------------------


class TestApprovalClaimSignal:
    """An executing approve stops every surface still offering the action.

    Approve runs its saved tool inline in the admin's request, so the resolved
    event cannot fire until that finishes. Meanwhile the Approvals tab, the Agent
    Chat bubble and the persistent notification must stop offering Approve and
    Reject, which the claim/release signal tested here does.
    """

    @staticmethod
    def _claim_events() -> list[tuple[str, bool]]:
        return RUNTIME.claims

    @pytest.mark.asyncio
    async def test_claim_event_fires_before_the_executor_runs(self):
        """The claim event fires before the executor runs.

        Firing it afterwards would coincide with the resolved event and change nothing.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        seen_during_execution: list[tuple[str, bool]] = []

        async def _fake_executor(name, args, tok, hass_, data_):
            seen_during_execution.extend(self_ref._claim_events())
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        self_ref = self
        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert json.loads(resp.text)["status"] == STATUS_APPROVED
        assert seen_during_execution == [("appr_a", True)]

    @pytest.mark.asyncio
    async def test_notification_is_dismissed_before_the_executor_runs(self):
        """The notification is dismissed before the executor runs.

        It has no UI state of its own, so dismissal is its way of going non-actionable.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        dismissed_during_execution: list[int] = []

        async def _fake_executor(name, args, tok, hass_, data_):
            dismissed_during_execution.append(dismiss.call_count)
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss") as dismiss:
            await view.post(request, approval_id="appr_a")

        assert dismissed_during_execution == [1]

    @pytest.mark.asyncio
    async def test_successful_approve_never_releases(self):
        """A resolved approval is never released.

        The resolved event already removed it everywhere; a release would re-offer a
        dead action.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass_, data_):
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            await view.post(request, approval_id="appr_a")

        assert self._claim_events() == [("appr_a", True)]

    @pytest.mark.asyncio
    async def test_a_raising_executor_closes_the_approval_instead_of_re_offering_it(self):
        """A raising executor closes the approval instead of re-offering it.

        A call that succeeded and then raised in result handling is indistinguishable
        from one that never ran, so re-offering would invite the admin to apply it
        twice. The record is resolved as recovery reconciliation resolves an interrupted
        one (failed, execution_interrupted) and the surfaces learn from the resolved
        event rather than from the approval reappearing.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        data = _make_data(store)
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool",
                   side_effect=RuntimeError("boom")), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("homeassistant.components.persistent_notification.async_create") as create:
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 500
        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert stored["status"] == STATUS_FAILED
        assert stored["rejected_reason"] == "execution_interrupted"
        # Not released back to pending, so no claimed=False and no notification
        # inviting a second attempt.
        assert self._claim_events() == [("appr_a", True)]
        assert create.call_count == 0
        assert "appr_a" not in data.approvals_in_progress

    @pytest.mark.asyncio
    async def test_a_second_approve_after_a_raising_executor_cannot_run_it_again(self):
        # The two-POST shape: the whole point is that the admin looking at the
        # 500 cannot click Approve again and apply the side effect twice.
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)

        executor = AsyncMock(side_effect=[
            RuntimeError("raised after the side effect landed"),
            ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha"),
        ])
        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("homeassistant.components.persistent_notification.async_create"):
            first = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")
            second = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")

        assert first.status == 500
        # The executor ran exactly ONCE: that is the guarantee. The second POST
        # answers 200 because approving an already-terminal record is idempotent,
        # so it reports the stored outcome rather than running anything.
        assert executor.await_count == 1
        assert json.loads(second.text)["status"] == STATUS_FAILED
        assert json.loads(second.text)["rejected_reason"] == "execution_interrupted"

    @pytest.mark.asyncio
    async def test_an_unregistered_tool_stays_retryable(self):
        # The other side of the line: the registry lookup is the first thing
        # async_execute_approved_tool does, so ExecutorNotRegistered means nothing ran
        # and nothing can have applied. That one stays retryable, and the marker is
        # cleared so a later startup does not resolve a healthy pending approval as
        # interrupted.
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        view = _Route(APPROVE)

        from custom_components.phoenix_mcp.tool_dispatch import ExecutorNotRegistered

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool",
                   side_effect=ExecutorNotRegistered("no such tool")), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("homeassistant.components.persistent_notification.async_create") as create:
            resp = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")

        assert resp.status == 400
        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert stored["status"] == STATUS_PENDING
        assert "execution_started_at" not in stored
        assert self._claim_events() == [("appr_a", True), ("appr_a", False)]
        assert create.call_count == 1

    @pytest.mark.asyncio
    async def test_a_real_executor_raising_KeyError_is_not_treated_as_a_missing_one(self):
        """A KeyError raised inside an executor does not read as "nothing ran".

        Drives the real registry and dispatcher: mocking async_execute_approved_tool
        cannot tell a lookup failure from an executor failure. Treating the KeyError as
        retryable would clear the durable marker and re-offer an applied action.
        """
        from custom_components.phoenix_mcp import tool_catalog

        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(
            pending=[_make_pending("appr_a", access_key_id="tok-1", tool_name="restart_ha")],
            access_keys=[access_key])
        view = _Route(APPROVE)
        applied: list[str] = []

        async def _executor(args, tok, hass_, data_):
            applied.append("side effect landed")
            raise KeyError("a lookup inside the executor, long after the write")

        with \
             patch.dict(tool_catalog._EXECUTOR_REGISTRY, {"restart_ha": _executor}), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("homeassistant.components.persistent_notification.async_create"):
            first = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")
            second = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")

        assert first.status == 500
        # Ran once, and the record is closed rather than offered back.
        assert applied == ["side effect landed"]
        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert stored["status"] == STATUS_FAILED
        assert stored["rejected_reason"] == "execution_interrupted"
        assert json.loads(second.text)["rejected_reason"] == "execution_interrupted"

    @pytest.mark.asyncio
    async def test_an_approval_carrying_a_stale_marker_refuses_to_run_again(self):
        """A pending record carrying an execution marker refuses to run again.

        The marker means an earlier attempt reached dispatch and never reported back,
        so the action may already have applied. Overwriting it and running again is the
        retry that must not happen; recovery reconciliation resolves these.
        """
        from custom_components.phoenix_mcp import tool_catalog

        pending = _make_pending("appr_a", access_key_id="tok-1", tool_name="restart_ha")
        pending["execution_started_at"] = utcnow().isoformat()
        store = _make_store(pending=[pending], access_keys=[])
        data = _make_data(store)
        view = _Route(APPROVE)
        applied: list[str] = []

        async def _executor(args, tok, hass_, data_):
            applied.append("should never run")
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        with \
             patch.dict(tool_catalog._EXECUTOR_REGISTRY, {"restart_ha": _executor}), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("homeassistant.components.persistent_notification.async_create"):
            resp = await view.post(_make_admin_request(body=b"{}"), approval_id="appr_a")

        assert resp.status == 409
        assert applied == []
        assert "appr_a" not in data.approvals_in_progress
        stored = next(r for r in store.get_pending_approvals() if r["id"] == "appr_a")
        assert stored["status"] == STATUS_PENDING
        assert stored["execution_started_at"]

    @pytest.mark.asyncio
    async def test_list_reports_in_progress_so_a_reloaded_panel_agrees(self):
        """The list reports in-progress approvals so a reloaded panel agrees.

        The claim event only reaches a panel that was already open; a page loaded
        mid-execution learns it from the row.
        """
        store = _make_store(pending=[_make_pending("appr_a"), _make_pending("appr_b")])
        data = _make_data(store)
        data.approvals_in_progress.add("appr_a")
        view = _Route(APPROVALS)

        resp = await view.get(_make_admin_request())

        rows = {r["id"]: r for r in json.loads(resp.text)["approvals"]}
        assert rows["appr_a"]["in_progress"] is True
        assert rows["appr_b"]["in_progress"] is False


class TestApprovalAuditLogging:
    @pytest.mark.asyncio
    async def test_reject_writes_queryable_audit_entry(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        view = _Route(REJECT)
        request = _make_admin_request(body=json.dumps({"reason": "not_safe"}).encode())

        with \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 200
        # The entry exists and is filterable by a canonical outcome (would be
        # None if the recorded outcome were outside _VALID_OUTCOMES).
        denied = data.audit.query(outcome="denied")
        assert denied is not None
        assert any("appr_a" in e.resource for e in denied)

    @pytest.mark.asyncio
    async def test_approve_writes_allowed_audit_entry(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", access_key_id="tok-1")], access_keys=[access_key])
        data = _make_data(store)
        view = _Route(APPROVE)
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 200
        allowed = data.audit.query(outcome="allowed")
        assert allowed is not None
        assert any("appr_a" in e.resource for e in allowed)


# ---- batch approve -----------------------------------------------------------


class TestBatchApprove:
    """POST /admin/approvals/batch/approve.

    Every id runs through the same _approve_approval, so these tests cover the
    batch's own contract (order, stop-at-failure, what is reported, what is left
    pending), not the per-approval lifecycle.
    """

    @staticmethod
    def _view_and_request(pending, access_keys, ids, kill_switch=False):
        store = _make_store(pending=pending, access_keys=access_keys, kill_switch=kill_switch)
        data = _make_data(store)
        view = _Route(BATCH_APPROVE)
        request = _make_admin_request(body=json.dumps({"approval_ids": ids}).encode())
        return view, request, store, data

    @pytest.mark.asyncio
    async def test_approves_every_id_in_order(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        view, request, store, _data = self._view_and_request(
            [_make_pending("appr_a"), _make_pending("appr_b"), _make_pending("appr_c")],
            [access_key], ["appr_a", "appr_b", "appr_c"],
        )
        seen: list[str] = []

        async def _fake_executor(name, args, tok, hass, data):
            seen.append(name)
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request)

        out = json.loads(resp.text)
        assert resp.status == 200
        assert [a["approval_id"] for a in out["applied"]] == ["appr_a", "appr_b", "appr_c"]
        assert out["failed"] is None and out["remaining"] == []
        assert len(seen) == 3
        assert all(r["status"] == STATUS_APPROVED for r in store.get_pending_approvals())

    @pytest.mark.asyncio
    async def test_stops_at_first_failure_and_leaves_the_rest_pending(self):
        """Stop at the first failure and leave the rest pending.

        The second approval fails, so the third never reaches its executor and stays
        individually approvable.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        view, request, store, _data = self._view_and_request(
            [_make_pending("appr_a"), _make_pending("appr_b"), _make_pending("appr_c")],
            [access_key], ["appr_a", "appr_b", "appr_c"],
        )
        seen: list[str] = []

        async def _fake_executor(name, args, tok, hass, data):
            seen.append(tok.id)
            if len(seen) == 2:
                return ({"content": [{"type": "text", "text": "boom"}], "isError": True}, "invalid_request", "x")
            return ({"content": [{"type": "text", "text": "{}"}]}, "allowed", "restart_ha")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("homeassistant.components.persistent_notification.async_create"):
            resp = await view.post(request)

        out = json.loads(resp.text)
        assert [a["approval_id"] for a in out["applied"]] == ["appr_a"]
        assert out["failed"]["approval_id"] == "appr_b"
        assert out["remaining"] == ["appr_c"]
        # The third executor never ran.
        assert len(seen) == 2
        by_id = {r["id"]: r for r in store.get_pending_approvals()}
        assert by_id["appr_a"]["status"] == STATUS_APPROVED
        assert by_id["appr_c"]["status"] == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_execution_failure_is_not_reported_as_applied(self):
        """A failed execution answered with 200 is not reported as applied.

        An executor returning isError finalizes the record as rejected with
        execution_failed while _approve_approval still answers 200; counting that as
        applied would report a failed write as a success.
        """
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        view, request, _store, _data = self._view_and_request(
            [_make_pending("appr_a")], [access_key], ["appr_a"],
        )

        async def _fake_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": "nope"}], "isError": True}, "invalid_request", "x")

        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"), \
             patch("homeassistant.components.persistent_notification.async_create"):
            resp = await view.post(request)

        out = json.loads(resp.text)
        assert out["applied"] == []
        assert out["failed"]["approval_id"] == "appr_a"

    @pytest.mark.asyncio
    async def test_kill_switch_stops_the_batch_at_the_first_item(self):
        access_key = _make_access_key("tok-1", cap_restart="confirm")
        view, request, store, _data = self._view_and_request(
            [_make_pending("appr_a"), _make_pending("appr_b")],
            [access_key], ["appr_a", "appr_b"], kill_switch=True,
        )
        executor = AsyncMock()
        with \
             patch("custom_components.phoenix_mcp.admin_view.async_execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request)

        out = json.loads(resp.text)
        executor.assert_not_called()
        assert out["applied"] == [] and out["failed"]["approval_id"] == "appr_a"
        assert out["remaining"] == ["appr_b"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [
        {}, {"approval_ids": []}, {"approval_ids": "appr_a"},
        {"approval_ids": ["appr_a", ""]}, {"approval_ids": ["appr_a", 3]},
    ])
    async def test_rejects_a_malformed_id_list(self, body):
        view, request, _s, _d = self._view_and_request([], [], ["x"])
        request.body = json.dumps(body).encode()
        resp = await view.post(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_rejects_a_repeated_id(self):
        """A repeated id is refused up front.

        A duplicate would hit the already-terminal branch and halt the batch on the
        caller's own typo.
        """
        view, request, _s, _d = self._view_and_request([], [], ["appr_a", "appr_a"])
        resp = await view.post(request)
        assert resp.status == 400
        assert "repeat" in json.loads(resp.text)["message"]

    @pytest.mark.asyncio
    async def test_rejects_more_than_the_request_bound(self):
        from custom_components.phoenix_mcp.const import MAX_BATCH_APPROVALS
        ids = [f"appr_{i}" for i in range(MAX_BATCH_APPROVALS + 1)]
        view, request, _s, _d = self._view_and_request([], [], ids)
        resp = await view.post(request)
        assert resp.status == 400


@pytest.mark.parametrize("change", ["revocation", "policy", "runtime", "kill_switch"])
async def test_approved_executor_carries_current_authority_across_wait(change):
    """Real approval dispatch binds authority for executor revalidation."""
    from custom_components.phoenix_mcp import tool_catalog
    from custom_components.phoenix_mcp.request_authority import (
        check_request_authority, policy_version, request_authority,
    )

    access_key = _make_access_key("tok-1", cap_registry_write="confirm")
    store = _make_store(access_keys=[access_key], pending=[_make_pending(
        "appr_registry", tool_name="set_entity", cap_name="cap_registry_write",
    )])
    data = _make_data(store)
    view = _Route(APPROVE)
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def executor(args, tok, hass_, data_):
        check_request_authority()
        assert request_authority.get().access_key_id == access_key.id
        assert policy_version.get() == access_key.settings_version
        entered.set()
        await release.wait()
        check_request_authority()
        effects.append("mutation")
        return {"content": []}, "allowed", "test"

    with (
        patch.dict(tool_catalog._EXECUTOR_REGISTRY, {"set_entity": executor}),
        patch("homeassistant.components.persistent_notification.async_dismiss"),
        patch("homeassistant.components.persistent_notification.async_create"),
    ):
        task = asyncio.create_task(view.post(
            _make_admin_request(body=b"{}"), approval_id="appr_registry"
        ))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if change == "revocation":
                access_key.revoked = True
            elif change == "policy":
                access_key.settings_version += 1
            elif change == "runtime":
                data.shutting_down = True
            else:
                store.get_settings().kill_switch = True
        finally:
            release.set()
        response = await task
    assert response.status == 500
    assert effects == []
    assert store.get_pending_approvals()[0]["status"] == STATUS_FAILED
    assert store.get_pending_approvals()[0]["rejected_reason"] == "execution_interrupted"
    assert not data.approvals_in_progress
    assert request_authority.get() is None
