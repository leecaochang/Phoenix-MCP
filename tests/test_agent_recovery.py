"""Recovery barriers survive interrupted writes without replaying HA actions."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.phoenix_mcp.agent_recovery import (
    AgentRecovery, RecoveryRequired, dispatch_agent_tool, tool_is_read_only,
)


class Disk:
    """Persist by value, including across fresh recovery instances."""

    def __init__(self):
        self.saved = None
        self.fail = False

    async def async_save(self, value):
        if self.fail:
            raise OSError("private filesystem data")
        self.saved = copy.deepcopy(value)

    async def async_load(self):
        return copy.deepcopy(self.saved)


@pytest.mark.asyncio
async def test_claim_survives_restart_and_only_explicit_review_unlocks():
    disk = Disk()
    first = AgentRecovery(disk)
    await first.begin("token", "call_service", "request")
    restored = AgentRecovery(disk)
    await restored.async_load()
    assert restored.records("token")[0]["status"] == "outcome_unknown"
    assert restored.records("other") == []
    with pytest.raises(RecoveryRequired):
        await restored.begin("token", "call_service", "duplicate")
    assert await restored.reviewed("request")
    await restored.begin("token", "call_service", "new")
    assert not await restored.reviewed("new")  # cannot clear an active executor


@pytest.mark.asyncio
async def test_failed_finish_keeps_barrier_in_memory_and_on_disk():
    disk = Disk()
    recovery = AgentRecovery(disk)
    await recovery.begin("token", "call_service", "request")
    disk.fail = True
    with pytest.raises(OSError):
        await recovery.finish("request")
    assert recovery.records("token")[0]["status"] == "outcome_unknown"
    assert "request" in disk.saved["records"]


@pytest.mark.asyncio
async def test_concurrent_claims_admit_one_dispatch():
    recovery = AgentRecovery()
    claims = await asyncio.gather(
        recovery.begin("token", "call_service", "a"),
        recovery.begin("token", "call_service", "b"), return_exceptions=True,
    )
    assert sum(isinstance(r, RecoveryRequired) for r in claims) == 1
    assert len(recovery.records("token")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,marker,cleared", [
    ("approved", False, True), ("pending", False, True), ("pending", True, False),
    ("failed", False, False), ("rejected", False, True),
])
async def test_reconciliation_requires_attributed_approval_receipt(status, marker, cleared):
    recovery = AgentRecovery()
    await recovery.begin("token", "call_service", "request")
    recovery.interrupted("request")
    approval = {"request_id": "request", "token_id": "other", "status": status}
    if marker:
        approval["execution_started_at"] = "started"
    store = SimpleNamespace(get_pending_approvals=lambda: [approval])
    await recovery.reconcile(store, "token")
    assert recovery.records("token")
    approval["token_id"] = "token"
    await recovery.reconcile(store, "token")
    assert recovery.records("token")  # reconciliation reports evidence, never replays
    received = recovery.records("token")[0]["dispatch_result"]
    assert (received != "unknown") is cleared


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "exception", "error", "save"])
async def test_interrupted_dispatch_never_replays_and_never_stores_arguments(hass, failure):
    disk = Disk()
    recovery = AgentRecovery(disk)
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    calls = []

    async def dispatch(*args, **kwargs):
        calls.append(args)
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "exception":
            raise RuntimeError("secret tool arguments")
        if failure == "save":
            disk.fail = True
        result = {"content": [], "isError": failure == "error"}
        return {"result": result}, "call_service", "call_service", "allowed"

    args = dict(hass=hass, data=data, access_key=SimpleNamespace(id="token"), name="call_service",
                arguments={"credential": "never persist this"}, tool_id="t", request_id="r",
                client_ip="agentcli", base_url="http://example")
    with pytest.raises((asyncio.CancelledError, RecoveryRequired)):
        await dispatch_agent_tool(dispatch, **args)
    assert len(calls) == 1
    with pytest.raises(RecoveryRequired):
        await dispatch_agent_tool(dispatch, **{**args, "request_id": "duplicate"})
    assert len(calls) == 1
    assert "never persist" not in str(disk.saved)
    assert recovery.records("token")[0]["status"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_unwritable_journal_prevents_dispatch(hass):
    disk = Disk()
    disk.fail = True
    recovery = AgentRecovery(disk)
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    dispatch = AsyncMock()
    with pytest.raises(RecoveryRequired):
        await dispatch_agent_tool(dispatch, hass=hass, data=data, access_key=SimpleNamespace(id="t"),
                                  name="call_service", arguments={}, tool_id="t", request_id="r",
                                  client_ip="agentcli", base_url="http://example")
    dispatch.assert_not_awaited()


def test_read_classification_uses_canonical_and_legacy_server_catalog():
    assert tool_is_read_only("get_state")
    assert tool_is_read_only("GetLiveContext")
    assert tool_is_read_only("homeassistant__GetLiveContext")
    assert not tool_is_read_only("HassTurnOn")
    assert not tool_is_read_only("intent__HassTurnOn")
    assert not tool_is_read_only("invented_read")


async def test_unknown_tool_does_not_create_recovery_barrier(hass, access_key_store):
    """A real protocol refusal cannot imply an uncertain side effect."""
    from homeassistant.helpers import issue_registry as ir
    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.data import PhoenixData
    from custom_components.phoenix_mcp.mcp_view import _dispatch_mcp
    from custom_components.phoenix_mcp.rate_limiter import RateLimiter

    access_key, _ = await access_key_store.async_create_access_key("unknown tool", "admin")
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data["phoenix_mcp"] = data
    response, _, _, outcome = await dispatch_agent_tool(
        _dispatch_mcp, hass=hass, data=data, access_key=access_key, name="made_up_tool",
        arguments={}, tool_id="tool", request_id="unknown", client_ip="agentcli",
        base_url="http://example",
    )
    assert response["error"]["code"] == -32602
    assert outcome == "invalid_request"
    assert data.agent_recovery.records(access_key.id) == []
    assert not data.agent_recovery.blocks(access_key.id)
    assert ir.async_get(hass).async_get_issue("phoenix_mcp", "agent_recovery_unknown") is None


@pytest.mark.parametrize("surface", ["chat", "voice", "ai_task"])
async def test_provider_loops_continue_after_unknown_tool(hass, access_key_store, surface):
    """The chat, voice and AI Task loops continue past an unknown tool, via the
    real MCP dispatcher.
    """
    from custom_components.phoenix_mcp import agentcli
    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.data import PhoenixData
    from custom_components.phoenix_mcp.rate_limiter import RateLimiter

    access_key, _ = await access_key_store.async_create_access_key("loop", "admin")
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog(), ready=True)
    hass.data["phoenix_mcp"] = data
    provider = agentcli.OpenAICompatProvider(agentcli.ProviderConfig(
        kind="chatgpt", model="test", base_url="https://example.test", api_key="test",
    ))
    rounds = 0

    async def stream(*args, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield {"type": agentcli.EV_TOOL, "name": "made_up_tool", "id": "bad", "input": {}}
        else:
            yield {"type": agentcli.EV_TEXT, "text": "Recovered."}
        yield {"type": agentcli.EV_DONE, "stop_reason": "tool_use" if rounds == 1 else "end_turn",
               "assistant_msg": {"role": "assistant", "content": "Recovered."}}

    events = []

    async def emit(name, payload):
        events.append((name, payload))

    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="Test"):
        if surface == "chat":
            await agentcli.async_run_agent_turn(
                hass=hass, data=data, access_key=access_key, provider=provider, session=MagicMock(),
                messages=[{"role": "user", "content": "test"}], options={}, client_ip="agentcli",
                base_url="http://example", emit=emit, cancel=asyncio.Event(),
            )
            assert not [payload for name, payload in events if name == "error"]
        else:
            run = agentcli.async_run_voice_turn if surface == "voice" else agentcli.async_run_ai_task
            assert await run(hass, data, access_key, provider, MagicMock(), "http://example", "test") == "Recovered."
    assert rounds == 2
    assert data.agent_recovery.records(access_key.id) == []
    assert not data.agent_recovery.blocks(access_key.id)


@pytest.mark.asyncio
async def test_legacy_dispatch_cannot_bypass_barrier_but_status_reads_work(hass, access_key_store):
    import json
    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.data import PhoenixData
    from custom_components.phoenix_mcp.rate_limiter import RateLimiter
    from custom_components.phoenix_mcp.mcp_view import _dispatch_mcp

    access_key, _ = await access_key_store.async_create_access_key("recovery", "admin", pass_through=True)
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data["phoenix_mcp"] = data
    await data.agent_recovery.begin(access_key.id, "call_service", "prior")
    data.agent_recovery.interrupted("prior")
    for name in ("call_service", "HassTurnOn", "intent__HassTurnOn"):
        response, _, _, outcome = await _dispatch_mcp(
            "tools/call", 1, {"name": name, "arguments": {}}, access_key, hass, data, "legacy", "http://example")
        assert outcome == "denied"
        assert response["result"]["isError"] is True
        assert "resultType" not in response["result"]
    response, _, _, outcome = await _dispatch_mcp(
        "tools/call", 2, {"name": "get_approval_status", "arguments": {}}, access_key, hass, data, "legacy", "http://example")
    assert outcome == "allowed"
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["agent_recovery"][0]["request_id"] == "prior"
    other, _ = await access_key_store.async_create_access_key("another", "admin")
    response, _, _, _ = await _dispatch_mcp(
        "tools/call", 3, {"name": "get_approval_status", "arguments": {}}, other, hass, data, "legacy", "http://example")
    assert json.loads(response["result"]["content"][0]["text"])["agent_recovery"] == []


@pytest.mark.asyncio
async def test_received_receipt_stays_protected_until_provider_turn_finishes(hass):
    from custom_components.phoenix_mcp.agent_recovery import recovery_turn

    disk = Disk()
    recovery = AgentRecovery(disk)
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    received = asyncio.Event()
    finish = asyncio.Event()
    dispatch = AsyncMock(return_value=({"result": {"content": []}}, "call_service", "call_service", "allowed"))

    @recovery_turn
    async def run(*, hass, data):
        await dispatch_agent_tool(dispatch, hass=hass, data=data, access_key=SimpleNamespace(id="token"),
            name="call_service", arguments={}, tool_id="t", request_id="r", client_ip="voice", base_url="http://example")
        received.set()
        await finish.wait()
        return SimpleNamespace(outcome="provider_error")

    task = asyncio.create_task(run(hass=hass, data=data))
    await received.wait()
    assert recovery.records("token")[0]["status"] == "in_progress"
    assert not await recovery.reviewed("r")
    finish.set()
    await task
    restored = AgentRecovery(disk)
    await restored.async_load()
    assert restored.blocks("token")
    assert restored.records("token")[0]["dispatch_result"] == "received"
    assert restored.records("token")[0]["status"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_successful_turn_clears_receipts_after_multiple_writes(hass):
    from custom_components.phoenix_mcp.agent_recovery import recovery_turn

    recovery = AgentRecovery(Disk())
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    dispatch = AsyncMock(return_value=({"result": {"content": []}}, "call_service", "call_service", "allowed"))

    @recovery_turn
    async def run(*, hass, data):
        for rid in ("first", "second"):
            await dispatch_agent_tool(dispatch, hass=hass, data=data, access_key=SimpleNamespace(id="token"),
                name="call_service", arguments={}, tool_id="t", request_id=rid, client_ip="voice", base_url="http://example")
        return SimpleNamespace(outcome="ok")

    await run(hass=hass, data=data)
    assert dispatch.await_count == 2
    assert recovery.records("token") == []


@pytest.mark.asyncio
@pytest.mark.parametrize('save_fails', [False, True])
async def test_report_finalizes_only_received_records_from_its_own_finished_turn(hass, save_fails):
    from custom_components.phoenix_mcp.agent_recovery import recovery_turn
    disk = Disk()
    recovery = AgentRecovery(disk)
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    access_key = SimpleNamespace(id='token')
    dispatch = AsyncMock(side_effect=[
        ({'result': {'content': []}}, 'call_service', 'call_service', 'allowed'),
        OSError('Connection interrupted after dispatch'),
    ])
    @recovery_turn
    async def run(*, hass, data):
        for rid in ('received', 'unknown'):
            try:
                await dispatch_agent_tool(dispatch, hass=hass, data=data, access_key=access_key, name='call_service',
                    arguments={}, tool_id=rid, request_id=rid, client_ip='voice', base_url='http://example')
            except RecoveryRequired:
                pass
        return SimpleNamespace(outcome='provider_error', recovery_turn_id='')
    result = await run(hass=hass, data=data)
    await recovery.begin('other', 'call_service', 'unrelated')
    recovery.interrupted('unrelated')
    disk.fail = save_fails
    if save_fails:
        with pytest.raises(OSError):
            await recovery.complete_received_turn(hass, result.recovery_turn_id)
    else:
        await recovery.complete_received_turn(hass, result.recovery_turn_id)
    assert {r['request_id'] for r in recovery.records('token')} == (
        {'received', 'unknown'} if save_fails else {'unknown'})
    assert recovery.blocks('token')
    assert {r['request_id'] for r in recovery.records('other')} == {'unrelated'}
    assert dispatch.await_count == 2


@pytest.mark.parametrize("tool,expected", [("call_service", "call_service"), ("private" * 2000, "unknown_tool")])
async def test_begin_persists_only_bounded_catalog_tool_names(tool, expected):
    disk = Disk()
    recovery = AgentRecovery(disk)
    await recovery.begin("token", tool, "request")
    assert recovery.records("token")[0]["tool"] == expected
    assert disk.saved["records"]["request"]["tool"] == expected
