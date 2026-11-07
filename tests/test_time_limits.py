"""A time a model or provider sends that names no moment is its own mistake, never a failed turn.

Each value parses or nearly parses, then raises in the parser or in a zone conversion.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.phoenix_mcp import agent_turn, helpers
from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord, AccessKeyStore, PermissionNode, PermissionTree,
)
from custom_components.phoenix_mcp.agent_chat import async_run_agent_turn
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.llm import base as llm_base
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.tool_dispatch import _call_tool

IMPOSSIBLE = ["2026-11-31T23:59:59", "2026-02-30T00:00:00", "2026-11-06T25:00:00"]
AT_THE_EDGES = ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00", "9999-12-31T23:59:59",
                "0001-01-01", "9999-12-31"]
FIXED_TIME = "2026-01-01T00:00:00"


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Start the test Recorder before Home Assistant, as its fixture requires."""


def _read_key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="reader", access_key_hash="x", created_at=dt_util.utcnow(), created_by="u",
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}), pass_through=True)


async def _batch_environment(hass):
    store = AccessKeyStore(hass, AsyncMock())
    store.get_settings().mesa_mode = "off"
    access_key, _ = await store.async_create_access_key("turn", "admin", rate_limit_requests=0)
    entry = er.async_get(hass).async_get_or_create("switch", "test", "lamp", suggested_object_id="lamp")
    hass.states.async_set(entry.entity_id, "off")
    access_key.permissions = PermissionTree(entities={entry.entity_id: PermissionNode(state="GREEN")})
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    return data, access_key, entry.entity_id


def _switch_on(entity_id: str, call_id: str = "write") -> dict:
    return {"id": call_id, "name": "call_service", "input": {
        "service": {"domain": "switch", "name": "turn_on", "data": {}},
        "targets": [{"kind": "entity", "ids": [entity_id]}]}}


def _history(entity_id: str, end_time: str, call_id: str = "read") -> dict:
    return {"id": call_id, "name": "get_history", "input": {
        "entity_id": entity_id, "start_time": FIXED_TIME, "end_time": end_time}}


async def _run_batch(hass, data, access_key, calls: list[dict]):
    events: list[tuple[str, dict]] = []

    async def emit(name, payload):
        events.append((name, payload))

    batch = await agent_turn._run_tool_batch(
        calls, hass=hass, data=data, access_key_id=access_key.id, client_ip="agentcli",
        base_url="http://test", emit=emit, cancel=asyncio.Event())
    return batch, events


@pytest.mark.parametrize("value", [*IMPOSSIBLE, *AT_THE_EDGES])
def test_a_time_with_no_moment_passes_to_the_tool_that_answers_it(value):
    assert agent_turn._agentcli_time_arg_error("get_history", {"end_time": value}, dt_util.UTC) is None


@pytest.mark.parametrize("value", [*IMPOSSIBLE, "0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"])
def test_a_result_time_with_no_moment_gets_no_companion_and_stays_as_sent(value):
    assert agent_turn._agentcli_local_time(value, dt_util.UTC) is None
    text = json.dumps({"events": [{"due": value}]})
    with patch.dict(agent_turn._AGENT_CHAT_LOCAL_TIME_FIELDS, {"tool": frozenset({"due"})}):
        assert json.loads(agent_turn._agentcli_add_local_time_fields("tool", text, dt_util.UTC)) == json.loads(text)


@pytest.mark.parametrize("value", [*IMPOSSIBLE, *AT_THE_EDGES, "99999999999999999999d"])
def test_the_shared_parser_refuses_with_the_error_its_callers_catch(value):
    with pytest.raises(ValueError):
        helpers.parse_time_param(value)


@pytest.mark.parametrize("value", [*IMPOSSIBLE, *AT_THE_EDGES])
async def test_the_refusal_reaches_the_model_and_the_turn_goes_on(hass, value):
    data, access_key, entity_id = await _batch_environment(hass)
    batch, events = await _run_batch(hass, data, access_key, [_history(entity_id, value)])
    assert not batch.outcome_unknown and not batch.stop_turn
    assert [row["tool_use_id"] for row in batch.results] == ["read"]
    assert [name for name, _payload in events] == ["tool_call", "tool_result"]


@pytest.mark.parametrize("field", ["end_time", "start_time"])
@pytest.mark.parametrize("value", [*IMPOSSIBLE, *AT_THE_EDGES])
async def test_history_answers_a_time_with_no_moment_as_invalid_input(recorder_mock, hass, field, value):
    hass.states.async_set("sensor.a", "1")
    args = {"entity_id": "sensor.a", "start_time": FIXED_TIME, field: value}
    result, outcome, _resource = await _call_tool("get_history", args, _read_key(), hass, MagicMock(), "r", None)
    assert outcome == "invalid_request" and result["isError"]


@pytest.mark.parametrize(("tool", "args"), [
    ("get_statistics", {"statistic_ids": ["sensor.a"], "start_time": None}),
    ("get_logbook", {"start_time": None, "target": {"kind": "entities", "entity_ids": ["sensor.a"]}}),
    ("compare_states", {"entity_ids": ["sensor.a"], "t1": None, "t2": FIXED_TIME}),
    ("get_logs", {"since": None}),
    ("get_energy_config", {"start_time": None}),
    ("recent_activity", {"since": None}),
    ("get_calendar_events", {"entity_id": ["calendar.a"], "start_time": None}),
])
@pytest.mark.parametrize("value", [*IMPOSSIBLE, *AT_THE_EDGES])
async def test_every_time_reading_tool_raises_nothing_at_the_edges(recorder_mock, hass, tool, args, value):
    hass.states.async_set("sensor.a", "1")
    filled = {key: value if item is None else item for key, item in args.items()}
    reader = _read_key()
    reader.cap_log_read = "allow"
    _result, outcome, _resource = await _call_tool(tool, filled, reader, hass, MagicMock(), "r", None)
    assert outcome in ("invalid_request", "allowed")


async def test_an_exception_after_a_write_keeps_its_receipt_and_stops_the_round(hass):
    data, access_key, entity_id = await _batch_environment(hass)
    ran = []

    async def turn_on(_call):
        ran.append("ran")

    hass.services.async_register("switch", "turn_on", turn_on)
    real = agent_turn._flatten_text
    results = iter([real, MagicMock(side_effect=RuntimeError("boom"))])

    def flatten(content):
        return next(results)(content)

    calls = [_switch_on(entity_id), _switch_on(entity_id, "second"), _switch_on(entity_id, "third")]
    with patch.object(agent_turn, "_flatten_text", flatten):
        batch, _events = await _run_batch(hass, data, access_key, calls)
    assert ran == ["ran", "ran"]
    assert batch.outcome_unknown and batch.write_started
    assert [row["tool_use_id"] for row in batch.results] == ["write", "second", "third"]
    assert not batch.results[0]["is_error"]
    assert batch.results[1]["result_text"] == agent_turn._UNKNOWN_OUTCOME_RESULT
    assert batch.results[2]["result_text"] == agent_turn._NOT_RUN_AFTER_UNKNOWN


async def test_an_exception_after_a_rejected_review_stays_an_exception(hass):
    """A rejection ran nothing, whatever follows it."""
    data, access_key, entity_id = await _batch_environment(hass)
    pending = {"record": MagicMock(diff={}), "approval_id": "appr"}
    with (patch.object(agent_turn, "_parse_pending", return_value=pending),
          patch.object(agent_turn, "_await_agent_approval", AsyncMock(return_value={"status": "rejected"})),
          patch.object(agent_turn, "_status_of", return_value=agent_turn.STATUS_REJECTED),
          patch.object(agent_turn, "_cancel_agent_approval", AsyncMock()),
          patch.object(agent_turn, "_flatten_text", side_effect=RuntimeError("boom")),
          pytest.raises(RuntimeError)):
        await _run_batch(hass, data, access_key, [_switch_on(entity_id)])


async def test_an_exception_before_any_write_stays_an_exception(hass):
    """Nothing can be repeated, so an unknown outcome would be a false alarm."""
    data, access_key, entity_id = await _batch_environment(hass)
    with (patch.object(agent_turn, "_flatten_text", side_effect=RuntimeError("boom")),
          pytest.raises(RuntimeError)):
        await _run_batch(hass, data, access_key, [_history(entity_id, FIXED_TIME)])


async def test_a_turn_broken_after_a_write_hands_back_a_valid_conversation_and_a_barrier(hass):
    data, access_key, entity_id = await _batch_environment(hass)
    hass.services.async_register("switch", "turn_on", AsyncMock())
    calls = [_switch_on(entity_id), _history(entity_id, FIXED_TIME)]
    blocks = [{"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"]} for call in calls]
    provider = MagicMock()

    async def stream(*_args, **_kwargs):
        for call in calls:
            yield {"type": llm_base.EV_TOOL, **{key: call[key] for key in ("id", "name", "input")}}
        yield {"type": llm_base.EV_DONE, "stop_reason": "tool_use", "assistant_msg": {"role": "assistant", "content": blocks}}

    provider.stream_turn = stream
    provider.format_tools = lambda tools: tools
    provider.append_tool_results = lambda messages, results: messages.append(
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": row["tool_use_id"]} for row in results]})
    events: list[tuple[str, dict]] = []

    async def emit(name, payload):
        events.append((name, payload))

    real = agent_turn._flatten_text
    results = iter([real, MagicMock(side_effect=RuntimeError("boom"))])
    with (patch.object(agent_turn, "_flatten_text", lambda content: next(results)(content)),
          patch("custom_components.phoenix_mcp.agent_chat.async_route_chat_command", AsyncMock(return_value=True))):
        await async_run_agent_turn(
            hass=hass, data=data, access_key=access_key, provider=provider, session=MagicMock(),
            messages=[{"role": "user", "content": "go"}], options={}, client_ip="agentcli",
            base_url="http://test", emit=emit, cancel=asyncio.Event())
    assert [payload["code"] for name, payload in events if name == "error"] == ["outcome_unknown"]
    returned = next(payload["messages"] for name, payload in events if name == "messages")
    assert [row["tool_use_id"] for row in returned[-1]["content"]] == ["write", "read"]
    assert data.agent_recovery.blocks(access_key.id)


@pytest.mark.parametrize("value", [10 ** 400, "2026-11-31T23:59:59", "0001-01-01T00:00:00"])
def test_a_catalogue_time_with_no_moment_reads_as_absent(value):
    assert llm_base._epoch(value) is None
    assert llm_base._created_map([{"id": "model", "created": value}]) == {}


@pytest.mark.parametrize("value", ["2026-11-31T23:59:59", "2026-02-30"])
def test_a_shutdown_date_with_no_moment_is_not_a_shutdown(value):
    assert llm_base._is_expired({"id": "model", "shutdown_date": value}) is False
