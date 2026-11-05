"""Recovery barriers survive interrupted writes without replaying HA actions."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.phoenix_mcp.agent_dispatch import dispatch_agent_tool
from custom_components.phoenix_mcp.agent_recovery import (
    AgentRecovery, RecoveryRequired, recovery_turn, tool_is_read_only,
)
from tests.runtime_fixtures import patch_readers


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
    await first.begin("access_key", "call_service", "request")
    restored = AgentRecovery(disk)
    await restored.async_load()
    assert restored.records("access_key")[0]["status"] == "outcome_unknown"
    assert restored.records("other") == []
    with pytest.raises(RecoveryRequired):
        await restored.begin("access_key", "call_service", "duplicate")
    assert await restored.reviewed("request")
    await restored.begin("access_key", "call_service", "new")
    assert not await restored.reviewed("new")  # cannot clear an active executor


@pytest.mark.asyncio
async def test_failed_finish_keeps_barrier_in_memory_and_on_disk():
    disk = Disk()
    recovery = AgentRecovery(disk)
    await recovery.begin("access_key", "call_service", "request")
    disk.fail = True
    with pytest.raises(OSError):
        await recovery.finish("request")
    assert recovery.records("access_key")[0]["status"] == "outcome_unknown"
    assert "request" in disk.saved["records"]


@pytest.mark.asyncio
async def test_concurrent_claims_admit_one_dispatch():
    recovery = AgentRecovery()
    claims = await asyncio.gather(
        recovery.begin("access_key", "call_service", "a"),
        recovery.begin("access_key", "call_service", "b"), return_exceptions=True,
    )
    assert sum(isinstance(r, RecoveryRequired) for r in claims) == 1
    assert len(recovery.records("access_key")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,marker,cleared", [
    ("approved", False, True), ("pending", False, True), ("pending", True, False),
    ("failed", False, False), ("rejected", False, True),
])
async def test_reconciliation_requires_attributed_approval_receipt(status, marker, cleared):
    recovery = AgentRecovery()
    await recovery.begin("access_key", "call_service", "request")
    recovery.interrupted("request")
    approval = {"request_id": "request", "access_key_id": "other", "status": status}
    if marker:
        approval["execution_started_at"] = "started"
    store = SimpleNamespace(get_pending_approvals=lambda: [approval])
    await recovery.reconcile(store, "access_key")
    assert recovery.records("access_key")
    approval["access_key_id"] = "access_key"
    await recovery.reconcile(store, "access_key")
    assert recovery.records("access_key")  # reconciliation reports evidence, never replays
    received = recovery.records("access_key")[0]["dispatch_result"]
    assert (received != "unknown") is cleared


@pytest.mark.asyncio
async def test_startup_settles_recorded_outcomes_and_keeps_the_unknown_one():
    """A restart clears the claims whose outcome is on record.

    What is left is a write that reached Home Assistant with nothing recorded
    anywhere, which still blocks the access key until it is reviewed.
    """
    disk = Disk()
    recovery = AgentRecovery(disk)
    # One claim per access key: a second one is refused while the first is outstanding.
    for key, rid in (("answered_key", "answered"), ("decided_key", "decided"), ("unknown_key", "unknown")):
        await recovery.begin(key, "call_service", rid)
        recovery.interrupted(rid)
    # Home Assistant answered the agent for one of them.
    recovery._records["answered"]["dispatch_result"] = "received"
    store = SimpleNamespace(get_pending_approvals=lambda: [
        {"request_id": "decided", "access_key_id": "decided_key", "status": "cancelled"},
        {"request_id": "unknown", "access_key_id": "unknown_key", "status": "pending"},
    ])

    assert await recovery.settle_known_outcomes(store) == 2
    assert recovery.records("answered_key") == []
    assert recovery.records("decided_key") == []
    assert [r["request_id"] for r in recovery.records("unknown_key")] == ["unknown"]
    # The settled access keys may dispatch again; the unknown one still blocks.
    assert recovery.blocks("answered_key") is False
    assert recovery.blocks("decided_key") is False
    assert recovery.blocks("unknown_key") is True
    restored = AgentRecovery(disk)
    await restored.async_load()
    assert [r["request_id"] for r in restored.records("unknown_key")] == ["unknown"]
    assert await restored.settle_known_outcomes(store) == 0


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

    args = dict(hass=hass, data=data, access_key=SimpleNamespace(id="access_key"), name="call_service",
                arguments={"credential": "never persist this"}, tool_id="t", request_id="r",
                client_ip="agentcli", base_url="http://example")
    with pytest.raises((asyncio.CancelledError, RecoveryRequired)):
        await dispatch_agent_tool(dispatch, **args)
    assert len(calls) == 1
    refused, _method, _resource, outcome = await dispatch_agent_tool(dispatch, **{**args, "request_id": "duplicate"})
    assert outcome == "denied"
    assert "nothing was dispatched" in refused["result"]["content"][0]["text"]
    assert len(calls) == 1
    assert "never persist" not in str(disk.saved)
    assert recovery.records("access_key")[0]["status"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_unwritable_journal_prevents_dispatch(hass):
    disk = Disk()
    disk.fail = True
    recovery = AgentRecovery(disk)
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    dispatch = AsyncMock()
    refused, _method, _resource, outcome = await dispatch_agent_tool(
        dispatch, hass=hass, data=data, access_key=SimpleNamespace(id="t"),
        name="call_service", arguments={}, tool_id="t", request_id="r",
        client_ip="agentcli", base_url="http://example")
    assert outcome == "denied" and refused["result"]["isError"] is True
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_journal_refuses_another_key_as_not_dispatched(hass):
    """A full journal is a refusal before dispatch, never an unknown outcome for the caller."""
    from custom_components.phoenix_mcp.agent_recovery import MAX_RECORDS

    recovery = AgentRecovery(Disk())
    for index in range(MAX_RECORDS):
        await recovery.begin(f"key_{index}", "call_service", f"r{index}")
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    dispatch = AsyncMock()
    refused, _method, _resource, outcome = await dispatch_agent_tool(
        dispatch, hass=hass, data=data, access_key=SimpleNamespace(id="key_b"),
        name="call_service", arguments={}, tool_id="t", request_id="b",
        client_ip="agentcli", base_url="http://example")
    assert outcome == "denied"
    assert "nothing was dispatched" in refused["result"]["content"][0]["text"]
    dispatch.assert_not_awaited()
    assert recovery.records("key_b") == []


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
    from custom_components.phoenix_mcp.tool_dispatch import _dispatch_mcp
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
    from custom_components.phoenix_mcp import agent_chat, agent_turn
    from custom_components.phoenix_mcp.llm import accounts as llm_accounts
    from custom_components.phoenix_mcp.llm import base as llm_base
    from custom_components.phoenix_mcp.llm import provider_openai as llm_provider_openai
    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.data import PhoenixData
    from custom_components.phoenix_mcp.rate_limiter import RateLimiter

    access_key, _ = await access_key_store.async_create_access_key("loop", "admin")
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog(), ready=True)
    hass.data["phoenix_mcp"] = data
    provider = llm_provider_openai.OpenAICompatProvider(llm_accounts.ProviderConfig(
        kind="chatgpt", model="test", base_url="https://example.test", api_key="test",
    ))
    rounds = 0

    async def stream(*args, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield {"type": llm_base.EV_TOOL, "name": "made_up_tool", "id": "bad", "input": {}}
        else:
            yield {"type": llm_base.EV_TEXT, "text": "Recovered."}
        yield {"type": llm_base.EV_DONE, "stop_reason": "tool_use" if rounds == 1 else "end_turn",
               "assistant_msg": {"role": "assistant", "content": "Recovered."}}

    events = []

    async def emit(name, payload):
        events.append((name, payload))

    with patch.object(agent_turn, "_stream_turn_resilient", stream), \
         patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=[]), \
         patch_readers("custom_components.phoenix_mcp.tool_dispatch._build_instructions", return_value="Test"):
        if surface == "chat":
            await agent_chat.async_run_agent_turn(
                hass=hass, data=data, access_key=access_key, provider=provider, session=MagicMock(),
                messages=[{"role": "user", "content": "test"}], options={}, client_ip="agentcli",
                base_url="http://example", emit=emit, cancel=asyncio.Event(),
            )
            assert not [payload for name, payload in events if name == "error"]
        else:
            run = agent_chat.async_run_voice_turn if surface == "voice" else agent_chat.async_run_ai_task
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
    from custom_components.phoenix_mcp.tool_dispatch import _dispatch_mcp

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
        await dispatch_agent_tool(dispatch, hass=hass, data=data, access_key=SimpleNamespace(id="access_key"),
            name="call_service", arguments={}, tool_id="t", request_id="r", client_ip="voice", base_url="http://example")
        received.set()
        await finish.wait()
        return SimpleNamespace(outcome="provider_error")

    task = asyncio.create_task(run(hass=hass, data=data))
    await received.wait()
    assert recovery.records("access_key")[0]["status"] == "in_progress"
    assert not await recovery.reviewed("r")
    finish.set()
    await task
    restored = AgentRecovery(disk)
    await restored.async_load()
    assert restored.blocks("access_key")
    assert restored.records("access_key")[0]["dispatch_result"] == "received"
    assert restored.records("access_key")[0]["status"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_successful_turn_clears_receipts_after_multiple_writes(hass):
    from custom_components.phoenix_mcp.agent_recovery import recovery_turn

    recovery = AgentRecovery(Disk())
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    dispatch = AsyncMock(return_value=({"result": {"content": []}}, "call_service", "call_service", "allowed"))

    @recovery_turn
    async def run(*, hass, data):
        for rid in ("first", "second"):
            await dispatch_agent_tool(dispatch, hass=hass, data=data, access_key=SimpleNamespace(id="access_key"),
                name="call_service", arguments={}, tool_id="t", request_id=rid, client_ip="voice", base_url="http://example")
        return SimpleNamespace(outcome="ok")

    await run(hass=hass, data=data)
    assert dispatch.await_count == 2
    assert recovery.records("access_key") == []


@pytest.mark.asyncio
@pytest.mark.parametrize('save_fails', [False, True])
async def test_report_finalizes_only_received_records_from_its_own_finished_turn(hass, save_fails):
    from custom_components.phoenix_mcp.agent_recovery import recovery_turn
    disk = Disk()
    recovery = AgentRecovery(disk)
    data = SimpleNamespace(agent_recovery=recovery, store=SimpleNamespace(get_pending_approvals=lambda: []))
    access_key = SimpleNamespace(id='access_key')
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
    assert {r['request_id'] for r in recovery.records('access_key')} == (
        {'received', 'unknown'} if save_fails else {'unknown'})
    assert recovery.blocks('access_key')
    assert {r['request_id'] for r in recovery.records('other')} == {'unrelated'}
    assert dispatch.await_count == 2


@pytest.mark.parametrize("tool,expected", [("call_service", "call_service"), ("private" * 2000, "unknown_tool")])
async def test_begin_persists_only_bounded_catalog_tool_names(tool, expected):
    disk = Disk()
    recovery = AgentRecovery(disk)
    await recovery.begin("access_key", tool, "request")
    assert recovery.records("access_key")[0]["tool"] == expected
    assert disk.saved["records"]["request"]["tool"] == expected




def _turn(run):
    return recovery_turn(run)


@pytest.mark.asyncio
async def test_a_finished_write_in_a_live_turn_does_not_stop_another_turn():
    data = SimpleNamespace(agent_recovery=AgentRecovery(Disk()), store=SimpleNamespace(get_pending_approvals=list))
    recovery = data.agent_recovery
    answered, finish = asyncio.Event(), asyncio.Event()

    @_turn
    async def first(_hass, data):
        await recovery.begin("access_key", "call_service", "a")
        await recovery.received("a")
        answered.set()
        await finish.wait()
        return SimpleNamespace(outcome="ok")

    @_turn
    async def second(_hass, data):
        assert not recovery.blocks("access_key") and not recovery.needs_attention("access_key")
        await recovery.begin("access_key", "call_service", "b")
        await recovery.received("b")
        return SimpleNamespace(outcome="ok")

    running = asyncio.create_task(first(None, data))
    await answered.wait()
    await second(None, data)
    finish.set()
    await running
    assert recovery.records("access_key") == []


@pytest.mark.asyncio
async def test_a_failed_turn_still_holds_the_key_afterwards():
    data = SimpleNamespace(agent_recovery=AgentRecovery(Disk()))
    recovery = data.agent_recovery

    @_turn
    async def first(_hass, data):
        await recovery.begin("access_key", "call_service", "a")
        await recovery.received("a")
        return SimpleNamespace(outcome="failed")

    await first(None, data)
    assert recovery.blocks("access_key") and recovery.needs_attention("access_key")
    assert [row["status"] for row in recovery.admin_records()] == ["outcome_unknown"]


@pytest.mark.asyncio
async def test_a_dispatching_write_keeps_writes_out_with_a_truthful_refusal():
    """One write at a time still holds, and a turn that only reads may start."""
    data = SimpleNamespace(agent_recovery=AgentRecovery(Disk()))
    recovery = data.agent_recovery
    dispatching, finish = asyncio.Event(), asyncio.Event()

    @_turn
    async def first(_hass, data):
        await recovery.begin("access_key", "call_service", "a")
        dispatching.set()
        await finish.wait()
        await recovery.received("a")
        return SimpleNamespace(outcome="ok")

    running = asyncio.create_task(first(None, data))
    await dispatching.wait()
    assert recovery.blocks("access_key") and not recovery.needs_attention("access_key")
    with pytest.raises(RecoveryRequired, match="still running"):
        await recovery.begin("access_key", "call_service", "b")
    finish.set()
    await running


@pytest.mark.asyncio
async def test_a_record_with_no_live_turn_still_blocks_with_needs_attention():
    recovery = AgentRecovery(Disk())
    await recovery.begin("access_key", "call_service", "a")
    recovery.interrupted("a")
    assert recovery.needs_attention("access_key")
    with pytest.raises(RecoveryRequired, match="Needs Attention"):
        await recovery.begin("access_key", "call_service", "b")
