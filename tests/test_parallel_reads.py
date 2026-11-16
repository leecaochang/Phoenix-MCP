"""One model response's read calls run at once in Agent Chat and the headless loop.

A response whose every call is read-only by the recovery rule starts all of them before any is
answered; a write, ``home_request`` or an unknown name keeps the whole response in model order.
Answers go back in call order with the model's ids, a cancelled turn cancels the reads still
running, and each call keeps its own audit row.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.runtime_fixtures import patch_readers
from custom_components.phoenix_mcp import agent_turn
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.provider_constants import AGENT_PARALLEL_READ_LIMIT
from custom_components.phoenix_mcp.rate_limiter import RateLimiter

DISPATCH = "custom_components.phoenix_mcp.tool_dispatch._dispatch_mcp"


async def _environment(hass):
    store = AccessKeyStore(hass, AsyncMock())
    store.get_settings().mesa_mode = "off"
    access_key, _raw = await store.async_create_access_key("parallel", "admin", rate_limit_requests=0)
    access_key.pass_through = True
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    return data, access_key


def _read(call_id: str, entity_id: str = "sensor.one") -> dict:
    return {"id": call_id, "name": "get_state", "input": {"entity_id": entity_id}}


def _write(call_id: str) -> dict:
    return {"id": call_id, "name": "call_service", "input": {
        "service": {"domain": "light", "name": "toggle"},
        "targets": [{"kind": "entity", "ids": ["light.one"]}]}}


class _Held:
    """A fake dispatcher whose calls wait until released, recording when each starts and ends."""

    def __init__(self, release_order: list[str] | None = None) -> None:
        self.started: list[str] = []
        self.log: list[str] = []
        self.cancelled: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.running = 0
        self.peak = 0

    async def dispatch(self, method, tool_id, params, *args, **kwargs):
        self.started.append(tool_id)
        self.log.append(f"start:{tool_id}")
        self.running += 1
        self.peak = max(self.peak, self.running)
        gate = self.gates.setdefault(tool_id, asyncio.Event())
        try:
            # Yield once, so a call started beside this one shows in the log before it ends.
            await asyncio.sleep(0)
            await gate.wait()
        except asyncio.CancelledError:
            self.cancelled.append(tool_id)
            raise
        finally:
            self.running -= 1
        self.log.append(f"end:{tool_id}")
        body = {"content": [{"type": "text", "text": f"answer {tool_id}"}]}
        return {"jsonrpc": "2.0", "id": tool_id, "result": body}, method, params["name"], "allowed"

    def release(self, tool_id: str) -> None:
        self.gates.setdefault(tool_id, asyncio.Event()).set()

    async def wait_started(self, count: int) -> None:
        async with asyncio.timeout(2):
            while len(self.started) < count:
                await asyncio.sleep(0)


class _Instant(_Held):
    """A fake dispatcher that answers at once, so only the call order is observed."""

    async def dispatch(self, method, tool_id, params, *args, **kwargs):
        self.release(tool_id)
        return await super().dispatch(method, tool_id, params, *args, **kwargs)


def _chat_batch(hass, data, access_key, calls, emit=None, cancel=None):
    return agent_turn._run_tool_batch(
        calls, hass=hass, data=data, access_key_id=access_key.id, client_ip="agentcli",
        base_url="", emit=emit or AsyncMock(), cancel=cancel or asyncio.Event())


def _headless_run(hass, data, access_key) -> SimpleNamespace:
    return SimpleNamespace(
        hass=hass, data=data, access_key=access_key, access_key_id=access_key.id, still_bound=None,
        client_ip="voice", base_url="", review_urls=[], authority_lost=False, dispatch_started=False,
        write_started=False,
    )


@pytest.mark.parametrize("surface", ["chat", "headless"])
async def test_every_read_starts_before_any_is_answered_and_answers_keep_call_order(hass, surface):
    data, access_key = await _environment(hass)
    held = _Held()
    calls = [_read("a"), _read("b"), _read("c")]
    events: list[tuple[str, dict]] = []

    async def emit(name, payload):
        events.append((name, payload))

    with patch_readers(DISPATCH, held.dispatch):
        if surface == "chat":
            task = asyncio.ensure_future(_chat_batch(hass, data, access_key, calls, emit))
        else:
            results: list[dict] = []
            task = asyncio.ensure_future(agent_turn._dispatch_headless_calls(
                _headless_run(hass, data, access_key), calls, results))
        await held.wait_started(3)
        assert held.started == ["a", "b", "c"] and held.peak == 3
        # Finishing in reverse still answers the model in its own order.
        for tool_id in ("c", "b", "a"):
            held.release(tool_id)
            await asyncio.sleep(0)
        outcome = await asyncio.wait_for(task, 2)
    rows = outcome.results if surface == "chat" else results
    assert [row["tool_use_id"] for row in rows] == ["a", "b", "c"]
    assert [row["result_text"] for row in rows] == ["answer a", "answer b", "answer c"]
    assert held.log[3:] == ["end:c", "end:b", "end:a"]
    if surface == "chat":
        assert not outcome.write_started and not outcome.stop_turn and not outcome.outcome_unknown
        assert [p["id"] for name, p in events if name == "tool_call"] == ["a", "b", "c"]
        assert [p["id"] for name, p in events if name == "tool_result"] == ["a", "b", "c"]
        # Every call is announced before the first answer.
        names = [name for name, _p in events]
        assert names.index("tool_result") > max(i for i, name in enumerate(names) if name == "tool_call")


@pytest.mark.parametrize("surface", ["chat", "headless"])
@pytest.mark.parametrize("odd_one", [
    _write("b"),
    {"id": "b", "name": "no_such_tool", "input": {}},
    {"id": "b", "name": "home_request", "input": {"request": "turn on the light"}},
], ids=["write", "unknown", "home_request"])
async def test_one_call_that_is_not_a_read_keeps_the_whole_response_in_order(hass, surface, odd_one):
    data, access_key = await _environment(hass)
    instant = _Instant()
    calls = [_read("a"), odd_one, _read("c")]
    with patch_readers(DISPATCH, instant.dispatch):
        if surface == "chat":
            rows = (await _chat_batch(hass, data, access_key, calls)).results
        else:
            rows = []
            await agent_turn._dispatch_headless_calls(_headless_run(hass, data, access_key), calls, rows)
    assert instant.log == ["start:a", "end:a", "start:b", "end:b", "start:c", "end:c"]
    assert instant.peak == 1
    assert [row["tool_use_id"] for row in rows] == ["a", "b", "c"]


def test_only_two_or_more_reads_run_at_once():
    assert agent_turn._parallel_reads([_read("a"), _read("b")])
    assert agent_turn._parallel_reads([_read("a"), {"id": "b", "name": "GetLiveContext", "input": {}}])
    assert not agent_turn._parallel_reads([_read("a")])
    assert not agent_turn._parallel_reads([_read("a"), _write("b")])
    assert not agent_turn._parallel_reads([_read("a"), {"id": "b", "input": {}}])
    assert not agent_turn._parallel_reads([_read("a"), {"id": "b", "name": "mesa_request_lease", "input": {}}])


async def test_a_refused_call_keeps_its_place_among_reads_that_ran(hass):
    data, access_key = await _environment(hass)
    instant = _Instant()

    def time_error(name, args, _tz):
        return "Refused time." if args.get("entity_id") == "sensor.bad" else None

    calls = [_read("a"), _read("b", "sensor.bad"), _read("c")]
    with patch_readers(DISPATCH, instant.dispatch), \
            patch.object(agent_turn, "_agentcli_time_arg_error", side_effect=time_error):
        batch = await _chat_batch(hass, data, access_key, calls)
    assert instant.started == ["a", "c"]
    assert [(row["tool_use_id"], row["result_text"]) for row in batch.results] == [
        ("a", "answer a"), ("b", "Refused time."), ("c", "answer c")]


async def test_cancelling_the_chat_turn_cancels_the_reads_still_running(hass):
    data, access_key = await _environment(hass)
    held = _Held()
    cancel = asyncio.Event()
    with patch_readers(DISPATCH, held.dispatch):
        task = asyncio.ensure_future(_chat_batch(hass, data, access_key, [_read("a"), _read("b"), _read("c")],
                                                 cancel=cancel))
        await held.wait_started(3)
        held.release("a")
        async with asyncio.timeout(2):
            while "end:a" not in held.log:
                await asyncio.sleep(0)
        cancel.set()
        batch = await asyncio.wait_for(task, 2)
    assert sorted(held.cancelled) == ["b", "c"]
    assert batch.stop_turn and not batch.outcome_unknown
    assert [(row["tool_use_id"], row["is_error"]) for row in batch.results] == [
        ("a", False), ("b", True), ("c", True)]
    assert "not finished" in batch.results[1]["result_text"]


@pytest.mark.parametrize("surface", ["chat", "headless"])
async def test_a_cancelled_task_leaves_no_read_running(hass, surface):
    data, access_key = await _environment(hass)
    held = _Held()
    calls = [_read("a"), _read("b")]
    with patch_readers(DISPATCH, held.dispatch):
        if surface == "chat":
            task = asyncio.ensure_future(_chat_batch(hass, data, access_key, calls))
        else:
            task = asyncio.ensure_future(agent_turn._dispatch_headless_calls(
                _headless_run(hass, data, access_key), calls, []))
        await held.wait_started(2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert sorted(held.cancelled) == ["a", "b"] and held.running == 0


async def test_reads_beyond_the_limit_wait_their_turn(hass):
    data, access_key = await _environment(hass)
    held = _Held()
    count = AGENT_PARALLEL_READ_LIMIT + 2
    calls = [_read(f"r{index}") for index in range(count)]
    with patch_readers(DISPATCH, held.dispatch):
        task = asyncio.ensure_future(_chat_batch(hass, data, access_key, calls))
        await held.wait_started(AGENT_PARALLEL_READ_LIMIT)
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(held.started) == AGENT_PARALLEL_READ_LIMIT
        for call in calls:
            held.release(call["id"])
        batch = await asyncio.wait_for(task, 2)
    assert held.peak == AGENT_PARALLEL_READ_LIMIT
    assert [row["tool_use_id"] for row in batch.results] == [call["id"] for call in calls]


async def test_parallel_reads_keep_one_audit_row_per_call_and_charge_no_rate_limit(hass):
    data, access_key = await _environment(hass)
    for name in ("one", "two", "three"):
        hass.states.async_set(f"sensor.{name}", "1")
    calls = [_read("a", "sensor.one"), _read("b", "sensor.two"), _read("c", "sensor.three")]

    async def run(parallel: bool) -> tuple[list, list[dict]]:
        data.audit.clear()
        with patch.object(agent_turn, "_parallel_reads", return_value=parallel), \
                patch.object(data.rate_limiter, "check") as check, \
                patch.object(data.rate_limiter, "charge_burst") as burst, \
                patch.object(data.rate_limiter, "charge_batch") as charge:
            batch = await _chat_batch(hass, data, access_key, calls)
        assert not (check.called or burst.called or charge.called)
        return data.audit.query(access_key_id=access_key.id) or [], batch.results

    parallel_rows, parallel_results = await run(True)
    sequential_rows, sequential_results = await run(False)
    assert len(parallel_rows) == len(sequential_rows) == 3
    assert len({row.request_id for row in parallel_rows}) == 3
    assert sorted((row.outcome, row.resource) for row in parallel_rows) == sorted(
        (row.outcome, row.resource) for row in sequential_rows)
    assert {row.outcome for row in parallel_rows} == {"allowed"}
    assert parallel_results == sequential_results
    assert [row["tool_use_id"] for row in parallel_results] == ["a", "b", "c"]


@pytest.mark.parametrize("surface", ["chat", "headless"])
async def test_an_answer_settled_after_authority_is_lost_is_withheld(hass, surface):
    data, access_key = await _environment(hass)
    held = _Held()
    calls = [_read("a"), _read("b")]
    events: list[tuple[str, dict]] = []

    async def emit(name, payload):
        events.append((name, payload))

    run = _headless_run(hass, data, access_key)
    results: list[dict] = []
    with patch_readers(DISPATCH, held.dispatch):
        if surface == "chat":
            task = asyncio.ensure_future(_chat_batch(hass, data, access_key, calls, emit))
        else:
            task = asyncio.ensure_future(agent_turn._dispatch_headless_calls(run, calls, results))
        await held.wait_started(2)
        held.release("a")
        held.release("b")
        async with asyncio.timeout(2):
            while len([entry for entry in held.log if entry.startswith("end:")]) < 2:
                await asyncio.sleep(0)
        # Both reads finished; the key loses authority before either answer is used.
        access_key.revoked = True
        outcome = await asyncio.wait_for(task, 2)
    rows = outcome.results if surface == "chat" else results
    assert [row["tool_use_id"] for row in rows] == ["a", "b"]
    assert all(row["is_error"] and "answer was withheld" in row["result_text"] for row in rows)
    assert not any("answer a" in row["result_text"] or "answer b" in row["result_text"] for row in rows)
    if surface == "chat":
        assert outcome.authority_lost and outcome.stop_turn
        assert [p["id"] for name, p in events if name == "tool_result"] == ["a", "b"]
    else:
        assert run.authority_lost


async def test_template_renders_of_one_response_take_worker_slots_as_calls_in_order_would(hass, monkeypatch):
    """The worker refuses a render when no slot is free, so a batch's renders must not crowd each other out."""
    from custom_components.phoenix_mcp import template_render

    monkeypatch.undo()  # The real slot rules, with only the subprocess faked.
    running = peak = 0

    async def worker(payload, data):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return 0, b"7"

    monkeypatch.setattr(template_render, "_run_in_slot", worker)
    data, access_key = await _environment(hass)
    hass.states.async_set("sensor.one", "1")
    calls = [{"id": f"t{index}", "name": "render_template", "input": {"template": "{{ states | count }}"}}
             for index in range(3)] + [_read("r", "sensor.one")]
    batch = await _chat_batch(hass, data, access_key, calls)
    assert [row["tool_use_id"] for row in batch.results] == ["t0", "t1", "t2", "r"]
    assert [row["is_error"] for row in batch.results] == [False] * 4, batch.results
    assert peak == 1
