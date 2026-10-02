"""Real HTTP regressions for abandoned reviews and authority loss after writes."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer
import pytest
from homeassistant.setup import async_setup_component
from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp import agentcli, mcp_view
from custom_components.phoenix_mcp.admin_view import PhoenixAdminApprovalApproveView
from custom_components.phoenix_mcp.approvals import async_create_pending_approval, get_approval
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.request_authority import check_request_authority
from custom_components.phoenix_mcp.token_store import TokenStore


async def _environment(hass):
    store = TokenStore(hass, AsyncMock())
    store.get_settings().mesa_mode = "off"
    token, raw = await store.async_create_token("lifecycle", "admin", rate_limit_requests=0)
    token.pass_through = True
    token.cap_restart = "confirm"
    token.cap_physical_control = "allow"
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    return data, token, raw


@pytest.mark.parametrize("claimed", [False, True])
async def test_actual_task_cancellation_resolves_only_unclaimed_review(hass, claimed):
    data, token, _ = await _environment(hass)
    record = await async_create_pending_approval(data.store, token_id=token.id, token_name=token.name,
        tool_name="restart_ha", cap_name="cap_restart", args={}, diff={}, request_id="turn")
    if claimed:
        # A durable claim must survive even if no live claim remains in memory.
        pending = data.store.get_pending_approvals()
        pending[0]["execution_started_at"] = record.created_at.isoformat()
        data.store.set_pending_approvals(pending)
    task = asyncio.create_task(agentcli._await_agent_approval(hass, data, record.id, asyncio.Event(), 60))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert get_approval(data.store, record.id).status == ("pending" if claimed else "cancelled")


@pytest.mark.parametrize("handler_cancellation", [True, False])
async def test_real_chat_disconnect_cancels_approval(hass, hass_access_token, socket_enabled, handler_cancellation):
    assert await async_setup_component(hass, "http", {})
    data, token, _ = await _environment(hass)
    view = agentcli.PhoenixAgentCliChatView()
    view.hass = hass
    hass.http.register_view(view)
    cfg = agentcli.ProviderConfig("claude", "fixture-model", "http://example.invalid", "fixture-key")
    store = MagicMock()
    store.resolve.return_value = cfg
    provider = agentcli.ClaudeProvider(cfg)
    async def stream(*args, **kwargs):
        yield {"type": agentcli.EV_TOOL, "id": "call", "name": "restart_ha", "input": {}}
        yield {"type": agentcli.EV_DONE, "stop_reason": "tool_use", "assistant_msg": {
            "role": "assistant", "content": [{"type": "tool_use", "id": "call", "name": "restart_ha", "input": {}}]}}
    provider.stream_turn = stream
    server = TestServer(hass.http.app, handler_cancellation=handler_cancellation)
    client = TestClient(server, headers={"Authorization": f"Bearer {hass_access_token}"})
    await client.start_server()
    with patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=store)), \
         patch.object(agentcli, "build_provider", return_value=provider), \
         patch.object(agentcli, "AGENTCLI_HEARTBEAT_SECONDS", 0.02):
        try:
            response = await client.post(view.url, json={"token_id": token.id, "instance_id": "fixture", "user": "restart"})
            assert response.status == 200
            async with asyncio.timeout(3):
                buffer = ""
                async for chunk in response.content.iter_any():
                    buffer += chunk.decode()
                    if "event: approval_required" in buffer:
                        break
            assert "event: approval_required" in buffer
            record = data.store.get_pending_approvals()[0]
            response.close()
            async with asyncio.timeout(3):
                while get_approval(data.store, record["id"]).status == "pending":
                    await asyncio.sleep(0.01)
            assert get_approval(data.store, record["id"]).status == "cancelled"
        finally:
            await client.close()


@pytest.fixture
async def lifecycle_http(hass, hass_client_no_auth):
    assert await async_setup_component(hass, "http", {})
    data, token, raw = await _environment(hass)
    er.async_get(hass).async_get_or_create("light", "test", "lifecycle", suggested_object_id="lifecycle")
    hass.states.async_set("light.lifecycle", "off")
    for cls in (*mcp_view.ALL_MCP_VIEWS, PhoenixAdminApprovalApproveView):
        view = cls()
        view.hass = hass
        hass.http.register_view(view)
    client = await hass_client_no_auth()
    headers = {"Authorization": f"Bearer {raw}"}
    response = await client.post("/api/phoenix-mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}}})
    assert response.status == 200
    session = response.headers["Mcp-Session-Id"]
    headers["Mcp-Session-Id"] = session
    response = await client.post("/api/phoenix-mcp", headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status == 202
    client.session.headers["Mcp-Session-Id"] = session
    return client, data, token, raw


@pytest.mark.parametrize("era,batch", [("legacy", False), ("legacy", True), ("modern", False)])
async def test_real_json_disconnect_preserves_only_legacy_dispatch(hass, socket_enabled, era, batch):
    """Closing the real HTTP connection detaches legacy work but cancels modern work."""
    assert await async_setup_component(hass, "http", {})
    data, _, raw = await _environment(hass)
    assert data.enforce_mcp_lifecycle
    view = mcp_view.PhoenixMcpView()
    view.hass = hass
    hass.http.register_view(view)
    client = TestClient(TestServer(hass.http.app, handler_cancellation=True))
    await client.start_server()
    headers = {"Authorization": f"Bearer {raw}", "Accept": "application/json"}
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    effects = []

    async def dispatch(method, msg_id, *args, **kwargs):
        entered.set()
        try:
            await release.wait()
            effects.append(method)
            return mcp_view._jsonrpc_result(msg_id, {}), method, "fixture", "allowed"
        finally:
            finished.set()

    try:
        if era == "legacy":
            response = await client.post(view.url, headers=headers, json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "disconnect-test", "version": "1"}}})
            assert response.status == 200
            headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
            await response.read()
            response = await client.post(view.url, headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized"})
            assert response.status == 202
            await response.read()
        body = {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}}
        if era == "modern":
            headers.update({"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "ping"})
            body["params"] = {"_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }}
        with patch.object(mcp_view, "_dispatch_mcp_authorized", side_effect=dispatch):
            pending = asyncio.create_task(client.post(view.url, headers=headers, json=[body] if batch else body))
            async with asyncio.timeout(3):
                await entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            if era == "modern":
                async with asyncio.timeout(3):
                    await finished.wait()
                assert effects == []
            else:
                # Let aiohttp observe the socket closure before permitting completion.
                await asyncio.sleep(0.05)
                assert not finished.is_set()
                release.set()
                async with asyncio.timeout(3):
                    await finished.wait()
                assert effects == ["ping"]
    finally:
        release.set()
        await client.close()


def _write(msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": {
        "name": "call_service", "arguments": {"service": {"domain": "light", "name": "toggle"},
        "targets": [{"kind": "entity", "ids": ["light.lifecycle"]}]}}}


async def test_executed_write_receipt_survives_permission_change(lifecycle_http, hass):
    client, data, token, raw = lifecycle_http
    effects = []
    async def service(call):
        effects.append(call.data)
        token.settings_version += 1
    hass.services.async_register("light", "toggle", service)
    response = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=_write())
    body = await response.json()
    assert len(effects) == 1, body
    assert "Do not repeat" in json.dumps(body)
    assert "Retry with" not in json.dumps(body)
    assert "result" in body
    rows = data.audit.query()
    receipt = next(row for row in rows if row.method == "call_service")
    assert receipt.outcome == "allowed"
    assert json.loads(receipt.payload)["result_withheld"] is True
    assert not any(row.method == "tools/call" and row.outcome == "denied" for row in rows)


async def test_unknown_write_retains_barrier_and_refuses_repeat(lifecycle_http, hass):
    client, data, token, raw = lifecycle_http
    effects = []
    # The executor may lose authority before it can return its receipt.
    async def handler(*args, **kwargs):
        effects.append("effect")
        token.settings_version += 1
        check_request_authority()
    with patch.dict(mcp_view._TOOL_HANDLERS, {"call_service": (handler, (), {})}):
        first = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=_write())
        body = await first.json()
        assert "may already" in json.dumps(body)
        assert "Do not repeat" in json.dumps(body)
        second = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=_write(2))
        assert "earlier agent action" in json.dumps(await second.json())
    assert effects == ["effect"]
    assert data.agent_recovery.blocks(token.id)
    assert any(row.method == "authority/changed" and row.outcome == "invalid_request" for row in data.audit.query())


@pytest.mark.parametrize("service_error", ["authority", "integration"])
async def test_real_service_uncertain_effect_retains_barrier(lifecycle_http, hass, service_error):
    from homeassistant.exceptions import HomeAssistantError

    client, data, token, raw = lifecycle_http
    effects = []

    async def service(call):
        effects.append(call.data)
        token.settings_version += 1
        if service_error == "authority":
            check_request_authority()
        raise HomeAssistantError("private-integration-failure")

    hass.services.async_register("light", "toggle", service)
    first = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=_write())
    body = await first.json()
    assert "may already" in json.dumps(body)
    assert "No action was executed" not in json.dumps(body)
    assert "private-integration-failure" not in json.dumps(body)
    assert data.agent_recovery.blocks(token.id)
    second = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=_write(2))
    assert "earlier agent action" in json.dumps(await second.json())
    assert len(effects) == 1


async def test_buffered_batch_write_keeps_receipt_when_later_read_loses_authority(lifecycle_http, hass):
    client, data, token, raw = lifecycle_http
    calls = AsyncMock()
    hass.services.async_register("light", "toggle", calls)
    async def delayed_read(*args, **kwargs):
        token.settings_version += 1
        return {"content": [{"type": "text", "text": "private-stale-result"}]}, "allowed", "sensor.lifecycle"
    batch = [_write(), {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_state", "arguments": {"entity_id": "light.lifecycle"}}}]
    with patch.dict(mcp_view._TOOL_HANDLERS, {"get_state": (delayed_read, (), {})}):
        response = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=batch)
    result = await response.json()
    calls.assert_awaited_once()
    assert "Do not repeat" in json.dumps(result[0])
    assert "result" in result[0] and "error" in result[1]
    assert "private-stale-result" not in json.dumps(result)


async def test_inline_approved_effect_keeps_receipt_after_policy_edit(lifecycle_http, hass, hass_access_token):
    client, data, token, raw = lifecycle_http
    token.cap_physical_control = "confirm"
    token.confirm_inline_wait_seconds = 3
    er.async_get(hass).async_get_or_create("cover", "test", "lifecycle", suggested_object_id="lifecycle")
    hass.states.async_set("cover.lifecycle", "closed")
    sink = AsyncMock()
    hass.services.async_register("cover", "open_cover", sink)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "call_service", "arguments": {"service": {"domain": "cover", "name": "open_cover"},
        "targets": [{"kind": "entity", "ids": ["cover.lifecycle"]}]}}}
    request = asyncio.create_task(client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=body))
    async with asyncio.timeout(2):
        while not data.store.get_pending_approvals():
            await asyncio.sleep(0.01)
    approval_id = data.store.get_pending_approvals()[0]["id"]
    token.cap_physical_control = "allow"
    token.settings_version += 1
    accepted = await client.post(f"/api/phoenix-mcp/admin/approvals/{approval_id}/approve",
        headers={"Authorization": f"Bearer {hass_access_token}"}, json={})
    assert accepted.status == 200, await accepted.text()
    response = await request
    result = await response.json()
    sink.assert_awaited_once()
    assert "Do not repeat" in json.dumps(result)
    assert "result" in result
    assert not data.agent_recovery.blocks(token.id)


async def test_cancel_before_approval_emission_cleans_saved_review(hass):
    data, token, _ = await _environment(hass)
    pending_created = asyncio.Event()
    async def dispatch(*args, request_id, **kwargs):
        await async_create_pending_approval(data.store, token_id=token.id, token_name=token.name,
            tool_name="restart_ha", cap_name="cap_restart", args={}, diff={}, request_id=request_id)
        pending_created.set()
        await asyncio.Event().wait()
    with patch.object(mcp_view, "_dispatch_mcp", side_effect=dispatch):
        task = asyncio.create_task(agentcli._run_tool_batch(
            [{"id": "call", "name": "restart_ha", "input": {}}], hass=hass, data=data,
            token_id=token.id, client_ip="agentcli", base_url="", emit=AsyncMock(), cancel=asyncio.Event()))
        await asyncio.wait_for(pending_created.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert data.store.get_pending_approvals()[0]["status"] == "cancelled"


async def test_unknown_receipt_never_persists_caller_tool_text(hass):
    data, token, _ = await _environment(hass)
    await data.agent_recovery.retain_unknown(token.id, "private-caller-label" * 10000, "unknown")
    await data.agent_recovery.retain_unknown(token.id, "call_service", "known")
    rows = {row["request_id"]: row for row in data.agent_recovery.records(token.id)}
    assert rows["unknown"]["tool"] == "unknown_tool"
    assert rows["known"]["tool"] == "call_service"


@pytest.mark.parametrize("surface", ["http", "agent"])
async def test_service_authority_loss_before_dispatch_has_no_unknown_barrier(lifecycle_http, hass, monkeypatch, surface):
    from custom_components.phoenix_mcp.agent_recovery import dispatch_agent_tool
    client, data, token, raw = lifecycle_http
    calls = AsyncMock()
    hass.services.async_register("light", "toggle", calls)
    original = mcp_view.unsupported_secondary_targets
    checks = 0

    async def change(*args, **kwargs):
        nonlocal checks
        checks += 1
        if checks == 2:
            token.settings_version += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(mcp_view, "unsupported_secondary_targets", change)
    if surface == "http":
        response = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=_write())
        assert response.status == 200
        body = await response.json()
    else:
        body, _, _, _ = await dispatch_agent_tool(mcp_view._dispatch_mcp, hass=hass, data=data, token=token,
            name="call_service", arguments=_write()["params"]["arguments"], tool_id=1, request_id="prepare",
            client_ip="agentcli", base_url="")
    assert checks == 2
    assert "No action was executed" in json.dumps(body)
    calls.assert_not_awaited()
    assert not data.agent_recovery.blocks(token.id)


@pytest.mark.parametrize("status", ["rejected", "cancelled"])
async def test_inline_refusal_after_authority_change_does_not_claim_dispatch(lifecycle_http, hass, status):
    client, data, token, raw = lifecycle_http
    token.cap_physical_control = "confirm"
    token.confirm_inline_wait_seconds = 3
    er.async_get(hass).async_get_or_create("cover", "test", "held", suggested_object_id="held")
    hass.states.async_set("cover.held", "closed")
    calls = AsyncMock()
    hass.services.async_register("cover", "open_cover", calls)
    body = _write()
    body["params"]["arguments"] = {"service": {"domain": "cover", "name": "open_cover"}, "targets": [{"kind": "entity", "ids": ["cover.held"]}]}
    request = asyncio.create_task(client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=body))
    async with asyncio.timeout(2):
        while not data.store.get_pending_approvals():
            await asyncio.sleep(0.01)
    records = data.store.get_pending_approvals()
    records[0]["status"] = status
    data.store.set_pending_approvals(records)
    token.settings_version += 1
    if status == "cancelled":
        data.store.get_settings().kill_switch = True
    hass.bus.async_fire(f"{DOMAIN}_approval_resolved", {"approval_id": records[0]["id"]})
    response = await request
    assert "No action was executed" in json.dumps(await response.json())
    calls.assert_not_awaited()
    assert not data.agent_recovery.blocks(token.id)


@pytest.mark.parametrize("failure", ["save", "issues"])
async def test_authority_recovery_double_fault_keeps_json_response_and_barrier(lifecycle_http, hass, monkeypatch, failure):
    client, data, token, raw = lifecycle_http
    effects = []
    async def handler(*args, **kwargs):
        effects.append("started")
        token.settings_version += 1
        check_request_authority()
    if failure == "save":
        data.agent_recovery._store = AsyncMock()
        data.agent_recovery._store.async_save.side_effect = OSError("private-path-disk-full")
    else:
        monkeypatch.setattr(data.agent_recovery, "publish_issues", MagicMock(side_effect=ValueError("private-path-issue")))
    with patch.dict(mcp_view._TOOL_HANDLERS, {"call_service": (handler, (), {})}):
        response = await client.post("/api/phoenix-mcp", headers={"Authorization": f"Bearer {raw}"}, json=_write(72))
    assert response.status == 200
    body = await response.json()
    assert body["id"] == 72 and body["error"]["code"] == -32000
    assert "may already" in body["error"]["message"]
    assert "private-path" not in json.dumps(body) and "Traceback" not in json.dumps(body)
    assert effects == ["started"] and data.agent_recovery.blocks(token.id)


async def test_emit_failure_after_pending_review_still_closes_it(hass):
    data, token, _ = await _environment(hass)

    async def emit(event, payload):
        if event == "approval_required":
            raise ConnectionResetError("client gone")

    with pytest.raises(ConnectionResetError):
        await agentcli._run_tool_batch(
            [{"id": "call", "name": "restart_ha", "input": {}}], hass=hass, data=data,
            token_id=token.id, client_ip="agentcli", base_url="", emit=emit, cancel=asyncio.Event())
    pending = data.store.get_pending_approvals()
    assert pending
    assert get_approval(data.store, pending[0]["id"]).status == "cancelled"
