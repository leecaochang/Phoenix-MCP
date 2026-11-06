"""How a headless turn ends on the paths its surface tests do not reach."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.phoenix_mcp import agent_turn, home_companion
from custom_components.phoenix_mcp.access_key_store import GlobalSettings
from custom_components.phoenix_mcp.agent_recovery import AgentRecovery, RecoveryRequired
from custom_components.phoenix_mcp.const import HOME_COMPANION_FORMAT_ERROR_CODES, HOME_COMPANION_MAX_CONTEXT_BYTES
from custom_components.phoenix_mcp.home_companion import CompanionTurn
from custom_components.phoenix_mcp.llm import base as llm_base
from tests.runtime_fixtures import patch_readers



class _Provider:
    """A provider whose calls replay scripted normalized events."""

    def __init__(self, *calls: list[dict]) -> None:
        self.calls = list(calls)
        self.requests = 0
        self.sent: list[dict] = []
        self.systems: list[str] = []
        self.declared: list[list[dict]] = []

    def format_tools(self, tools: list[dict]) -> list[dict]:
        return tools

    def append_assistant(self, messages: list[dict], msg: dict) -> None:
        self.sent.append(msg)
        messages.append(msg)

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:
        self.sent.append({"tool_results": results})
        messages.append({"role": "user", "tool_results": results})

    async def stream_turn(self, session, *, system_prompt, messages, tools, options):
        self.requests += 1
        self.systems.append(system_prompt)
        self.declared.append(tools)
        for event in self.calls.pop(0):
            yield event


def _done(stop_reason: str = "end_turn", content: object = "") -> dict:
    return {"type": llm_base.EV_DONE, "stop_reason": stop_reason,
            "assistant_msg": {"role": "assistant", "content": content}}


def _text(text: str) -> dict:
    return {"type": llm_base.EV_TEXT, "text": text}


def _tool(name: str, call_id: str = "call", **arguments: object) -> dict:
    return {"type": llm_base.EV_TOOL, "id": call_id, "name": name, "input": arguments}


def _invalid() -> dict:
    return {"type": llm_base.EV_INVALID_TOOLS, "assistant_msg": {"role": "assistant", "content": ""},
            "results": [{"tool_use_id": "bad", "tool_name": "get_state", "result_text": "bad", "is_error": True}]}


def _format_error() -> dict:
    return {"type": llm_base.EV_ERROR, "code": "elsewhere", "message": "bad", "retryable": False,
            "diagnostic": {"category": sorted(HOME_COMPANION_FORMAT_ERROR_CODES)[0]}}


def _answered(text: str = "on", outcome: str = "allowed") -> AsyncMock:
    return AsyncMock(return_value=(
        {"result": {"content": [{"type": "text", "text": text}]}}, "tools/call", "tool", outcome))


def _data() -> MagicMock:
    data = MagicMock()
    data.agent_recovery = AgentRecovery()
    data.shutting_down = False
    data.store.get_settings.return_value = GlobalSettings()
    live = MagicMock(id="tid")
    live.is_valid.return_value = True
    data.store.get_access_key_by_id.return_value = live
    return data


async def _run(hass: HomeAssistant, data: MagicMock, provider: _Provider, **kwargs: object):
    arguments = {"system": "s", "seed": "hi", "client_ip": "voice", "max_iterations": 3, **kwargs}
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=[]):
        return await agent_turn._run_headless_turn(
            hass, data, data.store.get_access_key_by_id.return_value, provider, None, "http://h", **arguments)


@pytest.fixture
def companion_turn():
    """Admit the data's key as the running Home Companion request."""
    token = home_companion._turn.set(CompanionTurn(access_key_id="tid"))
    yield home_companion._turn.get()
    home_companion._turn.reset(token)


async def test_a_companion_context_over_its_ceiling_never_reaches_the_provider(hass: HomeAssistant, companion_turn):
    provider = _Provider([_text("unused"), _done()])
    result = await _run(hass, _data(), provider, system="x" * (HOME_COMPANION_MAX_CONTEXT_BYTES + 1))
    assert result.outcome == agent_turn.TURN_OUTPUT_LIMIT and result.text == ""
    assert provider.requests == 0


async def test_usage_without_a_collector_changes_nothing(hass: HomeAssistant):
    usage = {"type": llm_base.EV_USAGE, "input_tokens": 3, "output_tokens": 4}
    assert agent_turn.headless_usage.get() is None
    result = await _run(hass, _data(), _Provider([usage, _text("Hi."), _done()]))
    assert result.outcome == agent_turn.TURN_OK and result.text == "Hi."


async def test_usage_reaches_a_collector_once_per_call(hass: HomeAssistant):
    collector = MagicMock()
    token = agent_turn.headless_usage.set(collector)
    try:
        await _run(hass, _data(), _Provider([{"type": llm_base.EV_USAGE, "input_tokens": 3}, _text("Hi."), _done()]))
    finally:
        agent_turn.headless_usage.reset(token)
    collector.start_call.assert_called_once_with()
    collector.report.assert_called_once()


async def test_a_handed_back_invalid_round_is_held_to_the_output_ceiling(hass: HomeAssistant):
    invalid = {"type": llm_base.EV_INVALID_TOOLS, "assistant_msg": {"role": "assistant", "content": "x" * 64},
               "results": [{"tool_use_id": "bad", "tool_name": "get_state", "result_text": "bad", "is_error": True}]}
    provider = _Provider([invalid], [_text("unused"), _done()])
    with patch.object(agent_turn, "AGENT_CHAT_MAX_TURN_OUTPUT_BYTES", 16):
        result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_OUTPUT_LIMIT
    assert provider.requests == 1 and provider.sent == []


async def test_a_reply_at_the_output_ceiling_is_not_retained(hass: HomeAssistant):
    provider = _Provider([_text("partial"), _done(content="x" * 64)])
    with patch.object(agent_turn, "AGENT_CHAT_MAX_TURN_OUTPUT_BYTES", 16):
        result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_OUTPUT_LIMIT and result.text == "partial"
    assert provider.sent == []


@pytest.mark.parametrize(("text", "expected", "declined"), [
    ("PHXFOCUS: Not a home request.", "Not a home request.", True),
    ("It is on.", "It is on.", False),
])
async def test_a_stream_without_a_done_event_still_answers(hass: HomeAssistant, text, expected, declined):
    provider = _Provider([_text(text)])
    result = await _run(hass, _data(), provider, detect_focus_refusal=True)
    assert (result.outcome, result.text, result.focus_declined) == (agent_turn.TURN_OK, expected, declined)
    assert provider.sent == []


async def test_a_stream_without_a_done_event_retains_nothing(hass: HomeAssistant):
    provider = _Provider([_text("It is on.")])
    result = await _run(hass, _data(), provider)
    assert (result.outcome, result.text) == (agent_turn.TURN_OK, "It is on.")
    assert provider.requests == 1 and provider.sent == []


async def test_the_focus_marker_is_stripped_from_the_retained_message(hass: HomeAssistant):
    provider = _Provider([_text("PHXFOCUS: No."), _done(content="PHXFOCUS: No.")])
    result = await _run(hass, _data(), provider, detect_focus_refusal=True)
    assert result.focus_declined and result.text == "No."
    assert provider.sent == [{"role": "assistant", "content": "No."}]


async def test_a_marker_only_in_the_message_still_declines(hass: HomeAssistant):
    provider = _Provider([_text("No."), _done(content="PHXFOCUS: No.")])
    result = await _run(hass, _data(), provider, detect_focus_refusal=True)
    assert result.focus_declined and result.text == "No."


async def test_a_review_stop_answers_the_calls_and_ends_the_turn_undispatched(hass: HomeAssistant):
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_text("unused"), _done()])
    review = SimpleNamespace(action="stop", tool=None)
    dispatch = AsyncMock()
    with patch.object(agent_turn, "create_review_state", return_value=object()), \
            patch.object(agent_turn, "async_review_tools", AsyncMock(return_value=review)) as reviewing, \
            patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        result = await _run(hass, _data(), provider, max_iterations=4, routing_language="en")
    assert (result.outcome, result.text) == (agent_turn.TURN_AUTHORITY_LOST, "")
    assert provider.requests == 1
    dispatch.assert_not_awaited()
    assert reviewing.await_args.kwargs["remaining_rounds"] == 3
    assert reviewing.await_args.kwargs["language"] == "en"
    answered = provider.sent[-1]["tool_results"]
    assert [row["tool_use_id"] for row in answered] == ["call"] and answered[0]["is_error"]


@pytest.mark.parametrize(("text", "expected"), [
    ("three lights", 'three lights\nread_answer: {"kind": "count", "source": 0}'),
    ("[1, 2]", '[1, 2]\nread_answer: {"kind": "count", "source": 0}'),
    ('{"count": 2}', '{"count": 2, "read_answer": {"kind": "count", "source": 0}}'),
])
async def test_a_count_reference_joins_every_shape_of_search_result(hass: HomeAssistant, companion_turn, text, expected):
    companion_turn.evidence.calls = 1
    companion_turn.evidence.search_totals = {0: 2}
    answer = json.dumps({"status": "completed", "message": "Two."})
    provider = _Provider([_tool("search_entities"), _done("tool_use")], [_text(answer), _done()])
    dispatch = AsyncMock(return_value=(
        {"result": {"content": [{"type": "text", "text": text}]}}, "tools/call", "search_entities", "allowed"))
    with patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_OK
    results = next(row["tool_results"] for row in provider.sent if "tool_results" in row)
    assert results[0]["result_text"] == expected


@pytest.mark.parametrize("seam", ["model", "review", "dispatch"])
async def test_a_cancelled_turn_raises_and_keeps_its_recovery_receipts(hass: HomeAssistant, seam):
    data = _data()
    complete = AsyncMock()
    data.agent_recovery.complete_turn = complete

    class _Cancelled(_Provider):
        async def stream_turn(self, session, *, system_prompt, messages, tools, options):
            if seam == "model":
                raise asyncio.CancelledError
            async for event in super().stream_turn(session, system_prompt=system_prompt, messages=messages,
                                                   tools=tools, options=options):
                yield event

    review = AsyncMock(side_effect=asyncio.CancelledError) if seam == "review" else AsyncMock(
        return_value=SimpleNamespace(action="continue"))
    dispatch = AsyncMock(side_effect=asyncio.CancelledError)
    provider = _Cancelled([_tool("call_service"), _done("tool_use")])
    with patch.object(agent_turn, "create_review_state", return_value=object()), \
            patch.object(agent_turn, "async_review_tools", review), \
            patch.object(agent_turn, "dispatch_agent_tool", dispatch), pytest.raises(asyncio.CancelledError):
        await _run(hass, data, provider)
    complete.assert_not_awaited()
    assert dispatch.await_count == (1 if seam == "dispatch" else 0)
    assert not data.agent_recovery._live_turns


async def test_an_unknown_dispatch_outcome_ends_the_turn_without_a_partial_answer(hass: HomeAssistant):
    data = _data()
    complete = AsyncMock()
    data.agent_recovery.complete_turn = complete
    provider = _Provider([_text("Working on it."), _tool("call_service"), _done("tool_use")])
    with patch.object(agent_turn, "dispatch_agent_tool", AsyncMock(side_effect=RecoveryRequired("unknown"))):
        result = await _run(hass, data, provider)
    assert (result.outcome, result.text) == (agent_turn.TURN_OUTCOME_UNKNOWN, "")
    complete.assert_not_awaited()


async def test_no_rounds_at_all_is_exhausted(hass: HomeAssistant):
    provider = _Provider()
    result = await _run(hass, _data(), provider, max_iterations=0)
    assert (result.outcome, result.text) == (agent_turn.TURN_EXHAUSTED, "")
    assert provider.requests == 0


async def test_recovery_is_reconciled_before_the_key_is_judged(hass: HomeAssistant):
    data = _data()
    order: list[str] = []
    data.agent_recovery.reconcile = AsyncMock(side_effect=lambda *_: order.append("reconcile"))
    data.agent_recovery.needs_attention = MagicMock(side_effect=lambda _: order.append("attention") or False)
    await _run(hass, data, _Provider([_text("Hi."), _done()]))
    assert order == ["reconcile", "attention"]
    data.agent_recovery.reconcile.assert_awaited_once_with(data.store, "tid")


async def test_the_reserved_last_round_is_told_so_and_offered_no_tools(hass: HomeAssistant):
    catalog = [{"name": "get_state", "description": "d", "inputSchema": {"type": "object", "properties": {}}}]
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_text("Done."), _done()])
    dispatch = AsyncMock(return_value=(
        {"result": {"content": [{"type": "text", "text": "on"}]}}, "tools/call", "get_state", "allowed"))
    with patch.object(agent_turn, "dispatch_agent_tool", dispatch), \
            patch.object(agent_turn, "build_agent_tool_list", return_value=catalog):
        result = await _run(hass, _data(), provider, max_iterations=2, reserve_final_round=True)
    assert result.text == "Done."
    assert provider.systems == ["s", "s" + agent_turn._FINAL_ROUND_SYSTEM]
    assert [bool(tools) for tools in provider.declared] == [True, False]


async def test_a_final_round_does_not_advance_the_tool_loader(hass: HomeAssistant):
    loader = MagicMock()
    loader.directory.return_value = ""
    loader.round_tools.return_value = []
    with patch.object(agent_turn, "_tool_loader", return_value=loader):
        await _run(hass, _data(), _Provider([_text("Hi."), _done()]), response_only=True)
    loader.round_tools.assert_called_once_with()


async def test_a_handed_back_round_counts_toward_the_output_ceiling(hass: HomeAssistant):
    invalid = {"type": llm_base.EV_INVALID_TOOLS, "assistant_msg": {"role": "assistant", "content": "x" * 64},
               "results": [{"tool_use_id": "bad", "tool_name": "get_state", "result_text": "bad", "is_error": True}]}
    answer = _done(content="y" * 64)
    ceiling = len(json.dumps(answer["assistant_msg"])) + 8
    assert ceiling > len(json.dumps(invalid["assistant_msg"]))
    provider = _Provider([invalid], [_text("Done."), answer])
    with patch.object(agent_turn, "AGENT_CHAT_MAX_TURN_OUTPUT_BYTES", ceiling):
        result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_OUTPUT_LIMIT and provider.requests == 2


async def test_retained_replies_add_up_toward_the_output_ceiling(hass: HomeAssistant):
    first = _done("tool_use", content="x" * 64)
    answer = _done(content="y" * 64)
    ceiling = len(json.dumps(answer["assistant_msg"])) + 8
    provider = _Provider([_tool("get_state"), first], [_text("Done."), answer])
    dispatch = AsyncMock(return_value=(
        {"result": {"content": [{"type": "text", "text": "on"}]}}, "tools/call", "get_state", "allowed"))
    with patch.object(agent_turn, "AGENT_CHAT_MAX_TURN_OUTPUT_BYTES", ceiling), \
            patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_OUTPUT_LIMIT and provider.requests == 2


async def test_malformed_tool_calls_stop_being_handed_back_at_the_budget(hass: HomeAssistant):
    provider = _Provider([_invalid()], [_invalid()], [_text("unused"), _done()])
    with patch.object(agent_turn, "AGENT_CHAT_MAX_TOOL_CALL_CORRECTIONS", 1):
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert result.outcome == agent_turn.TURN_PROVIDER_ERROR and result.detail_code == "bad_tool_call"
    assert provider.requests == 2


async def test_a_final_round_never_hands_a_malformed_call_back(hass: HomeAssistant):
    provider = _Provider([_invalid()], [_text("unused"), _done()])
    result = await _run(hass, _data(), provider, response_only=True, max_iterations=3)
    assert result.outcome == agent_turn.TURN_PROVIDER_ERROR and provider.requests == 1


async def test_the_focus_marker_is_ordinary_text_without_detection(hass: HomeAssistant):
    provider = _Provider([_text("PHXFOCUS: No."), _done(content="PHXFOCUS: No.")])
    result = await _run(hass, _data(), provider)
    assert result.text == "PHXFOCUS: No." and not result.focus_declined
    assert provider.sent == [{"role": "assistant", "content": "PHXFOCUS: No."}]


async def test_tools_in_a_final_round_are_recorded_as_rejected(hass: HomeAssistant):
    provider = _Provider([_text("partial"), _tool("get_state", a=1), _done("tool_use")])
    with patch.object(agent_turn, "record_rejected_tool") as rejected:
        result = await _run(hass, _data(), provider, response_only=True)
    assert (result.outcome, result.text) == (agent_turn.TURN_EXHAUSTED, "")
    rejected.assert_called_once_with("call", "get_state", {"a": 1},
                                     reason="No tools are permitted in the final response round.", stage="round_limit")


async def test_lost_authority_stops_a_repeat_request_before_its_preflight(hass: HomeAssistant):
    data = _data()
    data.store.get_access_key_by_id.return_value.is_valid.return_value = False
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_tool("get_state"), _done("tool_use")],
                         [_text("unused"), _done()])
    with patch.object(agent_turn, "preflight_control_calls", return_value=None) as preflight:
        result = await _run(hass, data, provider, max_iterations=5)
    assert result.outcome == agent_turn.TURN_AUTHORITY_LOST
    assert provider.requests == 2 and preflight.call_count == 1


async def test_a_companion_never_hands_a_malformed_call_back_after_a_write(hass: HomeAssistant, companion_turn):
    provider = _Provider([_tool("call_service"), _done("tool_use")], [_invalid()], [_text("unused"), _done()])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()):
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert result.outcome == agent_turn.TURN_OUTCOME_UNKNOWN and provider.requests == 2


async def test_a_companion_has_its_own_budget_for_malformed_calls(hass: HomeAssistant, companion_turn):
    provider = _Provider([_invalid()], [_invalid()], [_text("unused"), _done()])
    with patch.object(agent_turn, "AGENT_CHAT_MAX_TOOL_CALL_CORRECTIONS", 9), \
            patch.object(agent_turn, "HOME_COMPANION_MAX_FORMAT_CORRECTIONS", 1):
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert result.outcome == agent_turn.TURN_PROVIDER_ERROR and provider.requests == 2


async def test_a_format_error_is_corrected_only_inside_a_companion_turn(hass: HomeAssistant):
    provider = _Provider([_format_error()], [_text("unused"), _done()])
    result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_PROVIDER_ERROR and provider.requests == 1


async def test_a_format_error_is_corrected_inside_a_companion_turn(hass: HomeAssistant, companion_turn):
    answer = json.dumps({"status": "completed", "message": "Done."})
    provider = _Provider([_format_error()], [_text(answer), _done()])
    result = await _run(hass, _data(), provider)
    assert (result.outcome, result.text) == (agent_turn.TURN_OK, answer) and provider.requests == 2


async def test_a_format_error_after_a_write_is_not_corrected(hass: HomeAssistant, companion_turn):
    provider = _Provider([_tool("call_service"), _done("tool_use")], [_format_error()], [_text("unused"), _done()])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()):
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert result.outcome == agent_turn.TURN_OUTCOME_UNKNOWN and provider.requests == 2


async def test_a_format_error_beside_a_tool_call_is_not_corrected(hass: HomeAssistant, companion_turn):
    provider = _Provider([_tool("get_state"), _format_error()], [_text("unused"), _done()])
    result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_PROVIDER_ERROR and provider.requests == 1


async def test_a_format_error_with_no_round_left_is_not_corrected(hass: HomeAssistant, companion_turn):
    provider = _Provider([_format_error()], [_text("unused"), _done()])
    result = await _run(hass, _data(), provider, max_iterations=1)
    assert result.outcome == agent_turn.TURN_PROVIDER_ERROR and provider.requests == 1


async def test_a_provider_failure_after_a_write_is_an_unknown_outcome(hass: HomeAssistant):
    failure = {"type": llm_base.EV_ERROR, "code": "rate_limit", "message": "busy", "retryable": False}
    provider = _Provider([_tool("call_service"), _done("tool_use")], [failure])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()):
        result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_OUTCOME_UNKNOWN
    assert result.detail_code == "provider_after_dispatch" and "may have run" in result.detail


async def test_a_later_read_does_not_clear_a_started_write(hass: HomeAssistant):
    failure = {"type": llm_base.EV_ERROR, "code": "rate_limit", "message": "busy", "retryable": False}
    provider = _Provider([_tool("call_service", "w"), _tool("get_state", "r"), _done("tool_use")], [failure])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()):
        result = await _run(hass, _data(), provider)
    assert result.outcome == agent_turn.TURN_OUTCOME_UNKNOWN


async def test_an_unsupported_model_is_reported_in_the_surface_language(hass: HomeAssistant):
    failure = {"type": llm_base.EV_ERROR, "code": "model_api_unsupported", "message": "english", "retryable": False}
    result = await _run(hass, _data(), _Provider([failure]), routing_language="en")
    assert result.detail == agent_turn.voice_text("en", "model_api_unsupported") != "english"
    assert result.detail_code == "model_api_unsupported"


async def test_a_call_that_ends_without_a_stop_reason_is_not_taken_for_the_last_ones(hass: HomeAssistant):
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_text("Done.")], [_text("unused"), _done()])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()):
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert (result.outcome, result.text) == (agent_turn.TURN_OK, "Done.") and provider.requests == 2


async def test_a_read_report_is_not_corrected_after_a_write(hass: HomeAssistant, companion_turn):
    provider = _Provider([_tool("call_service"), _done("tool_use")], [_text("Done."), _done()], [_text("unused"), _done()])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()), \
            patch.object(agent_turn, "invalid_read_report", return_value=True):
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert result.text == "Done." and provider.requests == 2


async def test_a_read_report_with_no_round_left_stands(hass: HomeAssistant, companion_turn):
    provider = _Provider([_text("Done."), _done()], [_text("unused"), _done()])
    with patch.object(agent_turn, "invalid_read_report", return_value=True):
        result = await _run(hass, _data(), provider, max_iterations=1)
    assert result.text == "Done." and provider.requests == 1


async def test_a_refused_proposal_is_recorded_and_its_text_dropped(hass: HomeAssistant):
    provider = _Provider([_text("Let me try."), _tool("light_turn_on", a=1), _done("tool_use")], [_text("Done."), _done()])
    refusals = [{"tool_use_id": "call", "tool_name": "light_turn_on", "result_text": "bad", "is_error": True}]
    with patch.object(agent_turn, "preflight_control_calls", side_effect=[refusals]), \
            patch.object(agent_turn, "record_rejected_tool") as rejected:
        result = await _run(hass, _data(), provider)
    assert result.text == "Done." and provider.sent[1] == {"tool_results": refusals}
    rejected.assert_called_once_with("call", "light_turn_on", {"a": 1}, reason="bad", stage="companion_preflight")


async def test_a_refused_proposal_needs_a_round_for_its_correction(hass: HomeAssistant):
    provider = _Provider([_tool("light_turn_on"), _done("tool_use")], [_text("unused"), _done()])
    refusals = [{"tool_use_id": "call", "tool_name": "light_turn_on", "result_text": "bad", "is_error": True}]
    with patch.object(agent_turn, "preflight_control_calls", return_value=refusals):
        result = await _run(hass, _data(), provider, max_iterations=2)
    assert (result.outcome, result.text) == (agent_turn.TURN_PROVIDER_ERROR, "") and provider.requests == 1


async def test_a_refused_proposal_after_a_write_ends_the_turn_as_incomplete(hass: HomeAssistant):
    provider = _Provider([_tool("call_service"), _done("tool_use")], [_tool("light_turn_on"), _done("tool_use")],
                         [_text("unused"), _done()])
    refusals = [{"tool_use_id": "call", "tool_name": "light_turn_on", "result_text": "bad", "is_error": True}]
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()), \
            patch.object(agent_turn, "preflight_control_calls", side_effect=[None, refusals]), \
            patch.object(agent_turn, "incomplete_proposal") as incomplete:
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert json.loads(result.text)["status"] == "refused" and result.outcome == agent_turn.TURN_OK
    assert provider.requests == 2
    incomplete.assert_called_once()


async def test_a_call_after_authority_was_lost_is_recorded_as_refused(hass: HomeAssistant):
    data = _data()
    data.store.get_access_key_by_id.return_value.is_valid.return_value = False
    provider = _Provider([_tool("get_state", a=1), _done("tool_use")], [_text("No longer allowed."), _done()])
    dispatch = _answered()
    with patch.object(agent_turn, "record_rejected_tool") as rejected, patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        result = await _run(hass, data, provider)
    assert (result.outcome, result.text) == (agent_turn.TURN_AUTHORITY_LOST, "No longer allowed.")
    dispatch.assert_not_awaited()
    rejected.assert_called_once_with("call", "get_state", {"a": 1}, stage="authority_guard",
                                     reason="This access key is no longer authorized; the tool was not run.")


async def test_a_dispatch_runs_under_the_freshly_resolved_key(hass: HomeAssistant):
    fresh = MagicMock(id="tid")
    dispatch = _answered()
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_text("Done."), _done()])
    with patch.object(agent_turn, "_current_dispatch_access_key", return_value=fresh) as resolve, \
            patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        await _run(hass, _data(), provider)
    assert dispatch.await_args.kwargs["access_key"] is fresh
    resolve.assert_called_once()


@pytest.mark.parametrize("outcome", ["denied", "invalid_request"])
async def test_authority_refused_during_dispatch_stops_a_repeat_request(hass: HomeAssistant, outcome):
    refused = ({"error": {"code": -32000, "message": "gone"}}, "tools/call", "tool", outcome)
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_tool("get_state"), _done("tool_use")],
                         [_text("unused"), _done()])
    dispatch = AsyncMock(return_value=refused)
    with patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        result = await _run(hass, _data(), provider, max_iterations=5)
    assert result.outcome == agent_turn.TURN_AUTHORITY_LOST
    assert provider.requests == 2 and dispatch.await_count == 1


async def test_a_queued_approval_answers_its_call_once_and_the_turn_goes_on(hass: HomeAssistant):
    provider = _Provider([_tool("call_service"), _done("tool_use")], [_text("Queued."), _done()])
    pending = {"approval_id": "a", "review_url": "/phoenix-mcp/approvals/a", "record": None}
    with patch.object(agent_turn, "dispatch_agent_tool", _answered("{}", "pending_approval")), \
            patch.object(agent_turn, "_parse_pending", return_value=pending):
        result = await _run(hass, _data(), provider)
    assert (result.text, result.review_urls) == ("Queued.", ["/phoenix-mcp/approvals/a"])
    rows = next(row["tool_results"] for row in provider.sent if "tool_results" in row)
    assert len(rows) == 1 and rows[0]["is_error"] is False
    assert json.loads(rows[0]["result_text"])["status"] == "queued_for_approval"


async def test_a_queued_approval_ends_a_companion_turn(hass: HomeAssistant, companion_turn):
    provider = _Provider([_tool("call_service"), _done("tool_use")], [_text("unused"), _done()])
    pending = {"approval_id": "a", "review_url": "/phoenix-mcp/approvals/a", "record": None}
    with patch.object(agent_turn, "dispatch_agent_tool", _answered("{}", "pending_approval")), \
            patch.object(agent_turn, "_parse_pending", return_value=pending):
        result = await _run(hass, _data(), provider)
    assert (result.outcome, result.text) == (agent_turn.TURN_OK, "") and provider.requests == 1
    assert result.review_urls == ["/phoenix-mcp/approvals/a"]


async def test_tool_images_reach_the_model_beside_the_text(hass: HomeAssistant):
    image = {"type": "image", "data": "aGk=", "mimeType": "image/png"}
    response = ({"result": {"content": [{"type": "text", "text": "seen"}, image]}}, "tools/call", "tool", "allowed")
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_text("Done."), _done()])
    with patch.object(agent_turn, "dispatch_agent_tool", AsyncMock(return_value=response)):
        await _run(hass, _data(), provider)
    rows = next(row["tool_results"] for row in provider.sent if "tool_results" in row)
    assert rows[0]["images"] == [{"data": "aGk=", "mime_type": "image/png"}] and rows[0]["result_text"] == "seen"


async def test_the_answer_is_the_text_after_the_tools(hass: HomeAssistant):
    provider = _Provider([_text("Let me check."), _tool("get_state"), _done("tool_use")], [_text("It is on."), _done()])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()):
        result = await _run(hass, _data(), provider)
    assert result.text == "It is on."


async def test_results_are_fitted_against_the_longer_final_prompt(hass: HomeAssistant):
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_text("Done."), _done()])
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()), \
            patch.object(agent_turn, "append_fitting_results") as fitting:
        await _run(hass, _data(), provider)
    assert fitting.call_args.args[1] == "s" + agent_turn._FINAL_ROUND_SYSTEM


async def test_lost_authority_is_reported_ahead_of_exhaustion(hass: HomeAssistant):
    data = _data()
    data.store.get_access_key_by_id.return_value.is_valid.return_value = False
    provider = _Provider([_tool("get_state"), _done("tool_use")])
    result = await _run(hass, data, provider, max_iterations=1)
    assert result.outcome == agent_turn.TURN_AUTHORITY_LOST


async def test_every_ending_keeps_the_declined_focus(hass: HomeAssistant):
    marked = [_text("PHXFOCUS: No.")]
    failure = {"type": llm_base.EV_ERROR, "code": "rate_limit", "message": "busy", "retryable": False}
    run = {"detect_focus_refusal": True}
    dispatch = _answered()
    with patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        exhausted = await _run(hass, _data(), _Provider([*marked, _tool("get_state"), _done("tool_use")]),
                               max_iterations=1, **run)
        truncated = await _run(hass, _data(), _Provider([*marked, _done("max_tokens")]), **run)
        failed = await _run(hass, _data(), _Provider([*marked, _tool("get_state"), _done("tool_use")], [failure]), **run)
        lost_data = _data()
        lost_data.store.get_access_key_by_id.return_value.is_valid.return_value = False
        lost = await _run(hass, lost_data, _Provider([*marked, _tool("get_state"), _done("tool_use")],
                                                     [_text("Gone."), _done()]), **run)
        with patch.object(agent_turn, "AGENT_CHAT_MAX_TURN_OUTPUT_BYTES", 8):
            limited = await _run(hass, _data(), _Provider([*marked, _done(content="x" * 64)]), **run)
    assert (exhausted.outcome, truncated.outcome, failed.outcome, lost.outcome, limited.outcome) == (
        agent_turn.TURN_EXHAUSTED, agent_turn.TURN_TRUNCATED, agent_turn.TURN_PROVIDER_ERROR,
        agent_turn.TURN_AUTHORITY_LOST, agent_turn.TURN_OUTPUT_LIMIT)
    assert all(turn.focus_declined for turn in (exhausted, truncated, failed, lost, limited))


async def test_a_queued_approval_keeps_the_next_calls_row_in_place(hass: HomeAssistant):
    provider = _Provider([_tool("call_service", "queued"), _tool("get_state", "read"), _done("tool_use")],
                         [_text("Done."), _done()])
    pending = {"approval_id": "a", "review_url": "/phoenix-mcp/approvals/a", "record": None}
    with patch.object(agent_turn, "dispatch_agent_tool", _answered()), \
            patch.object(agent_turn, "_parse_pending", side_effect=[pending, None]):
        await _run(hass, _data(), provider)
    rows = next(row["tool_results"] for row in provider.sent if "tool_results" in row)
    assert [row["tool_use_id"] for row in rows] == ["queued", "read"]
    assert rows[1]["result_text"] == "on"


async def test_a_call_is_vetted_then_claimed_then_dispatched(hass: HomeAssistant):
    order: list[str] = []
    provider = _Provider([_tool("get_state"), _done("tool_use")], [_text("Done."), _done()])
    async def dispatch(*_, **__):
        order.append("dispatch")
        return {"result": {"content": []}}, "tools/call", "tool", "allowed"
    with patch.object(agent_turn, "ambiguous_control", side_effect=lambda *_: order.append("vetted") or False), \
            patch.object(agent_turn, "tool_is_read_only", side_effect=lambda _: order.append("claimed") or True), \
            patch.object(agent_turn, "dispatch_agent_tool", dispatch):
        await _run(hass, _data(), provider)
    assert order == ["vetted", "claimed", "dispatch"]
