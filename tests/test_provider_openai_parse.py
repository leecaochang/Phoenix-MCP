"""The OpenAI-compatible stream parser: framing, refusals, per-provider deltas and how a stream ends."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.llm import provider_openai
from custom_components.phoenix_mcp.llm.accounts import ProviderConfig
from custom_components.phoenix_mcp.llm.base import (
    EV_DONE, EV_ERROR, EV_INVALID_TOOLS, EV_TEXT, EV_THINKING, EV_TOOL, EV_USAGE,
    _ProviderResponseError,
)
from custom_components.phoenix_mcp.llm.provider_openai import (
    OpenAICompatProvider,
    _chunk_choice,
    _ChatStream,
    _merge_tool_fragment,
)

_MALFORMED = ("provider_malformed", True)
_BLOCKED = ("provider_blocked", False)
_INCOMPLETE = ("provider_incomplete", True)


class _Content:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._data), size):
            yield self._data[start:start + size]


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.content = _Content(body)


def _provider(kind: str = "chatgpt", model: str = "m") -> OpenAICompatProvider:
    return OpenAICompatProvider(ProviderConfig(kind=kind, model=model, base_url="https://h/v1", api_key="k"))


def _frame(delta=None, finish=None, **chunk) -> str:
    choice = {"delta": {} if delta is None else delta, "finish_reason": finish}
    return "data: " + json.dumps({"choices": [choice], **chunk})


def _tool(index=0, call_id="c1", name="t", arguments=None, **extra) -> dict:
    function: dict = {"name": name}
    if arguments is not None:
        function["arguments"] = arguments
    return {"index": index, "id": call_id, "function": function, **extra}


async def _events(provider: OpenAICompatProvider, *frames: str, show_thinking: bool = False) -> list[dict]:
    body = ("\n\n".join(frames) + "\n\n").encode()
    return [event async for event in provider._parse(_Resp(body), show_thinking)]


def _types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def _failure(events: list[dict]) -> tuple[str, bool]:
    last = events[-1]
    assert last["type"] == EV_ERROR
    return last["code"], last["retryable"]


@pytest.fixture
def rejected():
    with patch.object(provider_openai, "record_rejected_tool") as record:
        yield record


# ---------------------------------------------------------------- framing


@pytest.mark.parametrize("line, expected", [
    ('{"choices": []}', _MALFORMED),
    ('   {"choices": []}', _MALFORMED),
    ("[1, 2]", _MALFORMED),
    ('{"error": {"code": "content_filter"}}', _BLOCKED),
    ('{"error": {"code": "rate_limit_exceeded"}}', ("provider_transient", True)),
    ("{broken", _MALFORMED),
])
@pytest.mark.asyncio
async def test_bare_json_outside_sse_framing_is_refused(line, expected):
    events = await _events(_provider(), _frame({"content": "before  "}), line)
    assert events[0]["type"] == EV_TEXT
    assert _failure(events) == expected and len(events) == 2


@pytest.mark.parametrize("line", [
    "data:", "data:    ", "event: message", ": keep-alive", "id: 7", "plain words", "   ", "retry: 10",
])
@pytest.mark.asyncio
async def test_lines_without_a_payload_are_skipped(line):
    events = await _events(_provider(), _frame({"content": "hello!!!"}), line, _frame(finish="stop"))
    assert _types(events) == [EV_TEXT, EV_TEXT, EV_DONE]
    assert events[-1]["assistant_msg"]["content"] == "hello!!!"


@pytest.mark.asyncio
async def test_nothing_after_the_done_sentinel_is_read():
    events = await _events(_provider(), _frame({"content": "hi"}, finish="stop"), "data: [DONE]", "data: {broken")
    assert _types(events)[-1] == EV_DONE


@pytest.mark.parametrize("line", ["data: {broken", "data: [1]", "data: 3", 'data: {"error": "stop"}'])
@pytest.mark.asyncio
async def test_an_unusable_data_payload_ends_the_stream_with_a_refusal(line):
    events = await _events(_provider(), line, _frame(finish="stop"))
    assert len(events) == 1 and events[0]["type"] == EV_ERROR


@pytest.mark.asyncio
async def test_the_done_sentinel_without_a_finish_reason_is_incomplete():
    events = await _events(_provider(), _frame({"content": "partial"}), "data: [DONE]")
    assert _types(events)[0] == EV_TEXT and _failure(events) == _INCOMPLETE


# ---------------------------------------------------------------- usage


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": 0, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 5}},
    {"prompt_tokens": "9", "completion_tokens": None},
    {"prompt_cache_hit_tokens": 4},
    {},
])
@pytest.mark.asyncio
async def test_a_usage_row_without_tokens_yields_no_event(usage):
    events = await _events(_provider(), 'data: ' + json.dumps({"choices": [], "usage": usage}), _frame(finish="stop"))
    assert _types(events) == [EV_DONE]


@pytest.mark.parametrize("usage, cached", [
    ({"prompt_tokens": 7, "completion_tokens": 2}, 0),
    ({"prompt_tokens": 7, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 3}}, 3),
    ({"prompt_tokens": 7, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 0},
      "prompt_cache_hit_tokens": 4}, 4),
    ({"prompt_tokens": 7, "completion_tokens": 2, "prompt_tokens_details": "x", "prompt_cache_hit_tokens": 4}, 4),
])
@pytest.mark.asyncio
async def test_usage_reads_the_cached_count_from_either_field(usage, cached):
    events = await _events(_provider(), 'data: ' + json.dumps({"choices": [], "usage": usage}), _frame(finish="stop"))
    assert events[0] == {"type": EV_USAGE, "input_tokens": 7, "output_tokens": 2, "cache_read_tokens": cached}


@pytest.mark.parametrize("usage", [{"prompt_tokens": 7}, {"completion_tokens": 7}])
@pytest.mark.asyncio
async def test_one_token_count_is_enough_for_a_usage_event(usage):
    events = await _events(_provider(), 'data: ' + json.dumps({"choices": [], "usage": usage}), _frame(finish="stop"))
    assert _types(events) == [EV_USAGE, EV_DONE]


@pytest.mark.asyncio
async def test_usage_is_reported_before_the_chunk_is_judged():
    usage = {"prompt_tokens": 1, "completion_tokens": 1}
    events = await _events(_provider(), 'data: ' + json.dumps({"usage": usage, "choices": "no"}))
    assert _types(events) == [EV_USAGE, EV_ERROR] and _failure(events) == _MALFORMED


@pytest.mark.asyncio
async def test_a_usage_row_on_a_content_chunk_comes_before_its_text():
    usage = {"prompt_tokens": 1, "completion_tokens": 1}
    events = await _events(_provider(), _frame({"content": "hello!!!"}, finish="stop", usage=usage))
    assert _types(events) == [EV_USAGE, EV_TEXT, EV_TEXT, EV_DONE]


@pytest.mark.asyncio
async def test_a_non_mapping_usage_is_ignored():
    events = await _events(_provider(), 'data: ' + json.dumps({"choices": [], "usage": [1]}), _frame(finish="stop"))
    assert _types(events) == [EV_DONE]


# ---------------------------------------------------------------- choices and deltas


@pytest.mark.parametrize("chunk", [
    {}, {"choices": None}, {"choices": {}}, {"choices": "x"}, {"choices": [1]}, {"choices": ["x"]},
    {"choices": [{"delta": 1}]}, {"choices": [{"delta": "text"}]}, {"choices": [{"delta": [1]}]},
])
@pytest.mark.asyncio
async def test_a_misshapen_chunk_is_malformed(chunk):
    events = await _events(_provider(), _frame({"content": "before  "}), "data: " + json.dumps(chunk))
    assert _types(events) == [EV_TEXT, EV_ERROR] and _failure(events) == _MALFORMED


@pytest.mark.parametrize("delta", [None, 0, "", [], False])
@pytest.mark.asyncio
async def test_an_empty_delta_is_treated_as_no_delta(delta):
    chunk = {"choices": [{"delta": delta, "finish_reason": "stop"}]}
    events = await _events(_provider(), "data: " + json.dumps(chunk))
    assert _types(events) == [EV_DONE]


@pytest.mark.parametrize("frame", [
    _frame({"refusal": "no"}), _frame({"content": "x"}, finish="content_filter"),
    _frame({"refusal": "no", "content": "x", "reasoning": "r"}),
])
@pytest.mark.asyncio
async def test_a_refusal_is_blocked_before_anything_on_its_chunk_is_used(frame):
    events = await _events(_provider(), _frame({"content": "before  "}), frame, _frame(finish="stop"))
    assert _types(events) == [EV_TEXT, EV_ERROR] and _failure(events) == _BLOCKED


@pytest.mark.parametrize("delta", [{"refusal": ""}, {"refusal": None}])
@pytest.mark.asyncio
async def test_an_empty_refusal_field_is_not_a_refusal(delta):
    events = await _events(_provider(), _frame(delta, finish="stop"))
    assert _types(events) == [EV_DONE]


# ---------------------------------------------------------------- reasoning


@pytest.mark.parametrize("delta", [{"reasoning": 1}, {"reasoning_content": ["x"]}, {"reasoning": {"a": 1}}])
@pytest.mark.asyncio
async def test_non_text_reasoning_is_malformed(delta):
    events = await _events(_provider("deepseek"), _frame(delta), _frame(finish="stop"))
    assert _failure(events) == _MALFORMED and len(events) == 1


@pytest.mark.asyncio
async def test_reasoning_is_replayed_only_by_providers_that_require_it():
    frames = (_frame({"reasoning_content": "a"}), _frame({"reasoning": "b"}), _frame(finish="stop"))
    replayed = await _events(_provider("deepseek"), *frames)
    assert _types(replayed) == [EV_DONE] and replayed[-1]["assistant_msg"]["reasoning_content"] == "ab"
    shown = await _events(_provider("chatgpt"), *frames, show_thinking=True)
    assert [e.get("text") for e in shown[:2]] == ["a", "b"] and _types(shown) == [EV_THINKING, EV_THINKING, EV_DONE]
    assert "reasoning_content" not in shown[-1]["assistant_msg"]


@pytest.mark.asyncio
async def test_reasoning_content_wins_over_reasoning_on_one_delta():
    events = await _events(_provider("deepseek"), _frame({"reasoning_content": "a", "reasoning": "b"}, finish="stop"),
                           show_thinking=True)
    assert events[0] == {"type": EV_THINKING, "text": "a"}
    assert events[-1]["assistant_msg"]["reasoning_content"] == "a"


@pytest.mark.asyncio
async def test_reasoning_precedes_the_text_of_its_chunk():
    events = await _events(_provider(), _frame({"reasoning": "why", "content": "answer  "}, finish="stop"),
                           show_thinking=True)
    assert _types(events)[:2] == [EV_THINKING, EV_TEXT]


@pytest.mark.parametrize("kind, kept", [("openrouter", True), ("chatgpt", False)])
@pytest.mark.asyncio
async def test_reasoning_details_are_retained_for_openrouter_alone(kind, kept):
    details = [{"type": "reasoning.encrypted", "data": "x"}, "dropped", {"type": "reasoning.text", "text": "y"}]
    events = await _events(_provider(kind), _frame({"reasoning_details": details}),
                           _frame({"reasoning_details": "not a list"}),
                           _frame({"reasoning_details": [{"n": 3}]}, finish="stop"))
    message = events[-1]["assistant_msg"]
    if kept:
        assert message["reasoning_details"] == [details[0], details[2], {"n": 3}]
    else:
        assert "reasoning_details" not in message


# ---------------------------------------------------------------- content


@pytest.mark.parametrize("content", [1, True, {"a": 1}, ["x"], [{"type": EV_TEXT, "text": "x"}]])
@pytest.mark.asyncio
async def test_non_text_content_is_malformed_outside_mistral(content):
    events = await _events(_provider(), _frame({"content": content}), _frame(finish="stop"))
    assert _failure(events) == _MALFORMED and len(events) == 1


@pytest.mark.parametrize("content", [1, {"a": 1}, True])
@pytest.mark.asyncio
async def test_a_scalar_content_is_malformed_for_mistral_too(content):
    events = await _events(_provider("mistral"), _frame({"content": content}), _frame(finish="stop"))
    assert _failure(events) == _MALFORMED and len(events) == 1


@pytest.mark.asyncio
async def test_mistral_keeps_blocks_in_order_and_skips_the_unusable():
    thinking = {"type": "thinking", "thinking": [{"type": "text", "text": "deep"}, {"type": "ref"}, "x"]}
    blocks = [
        "not a block", 7, None, thinking, {"type": "text", "text": "ans"}, {"type": "text", "text": ""},
        {"type": "image", "url": "u"}, {"type": "text"},
    ]
    events = await _events(_provider("mistral"), _frame({"content": blocks}, finish="stop"), show_thinking=True)
    assert events[:2] == [{"type": EV_THINKING, "text": "deep"}, {"type": EV_TEXT, "text": "ans"}]
    assert _types(events) == [EV_THINKING, EV_TEXT, EV_DONE]
    assert events[-1]["assistant_msg"]["content"] == [
        thinking, {"type": "text", "text": "ans"}, {"type": "text", "text": ""},
        {"type": "image", "url": "u"}, {"type": "text"},
    ]


@pytest.mark.parametrize("show", [True, False])
@pytest.mark.asyncio
async def test_mistral_thinking_block_without_text_shows_nothing_but_is_retained(show):
    empty = {"type": "thinking", "thinking": [{"type": "text", "text": ""}, {"type": "other", "text": "x"}]}
    bare = {"type": "thinking"}
    events = await _events(_provider("mistral"), _frame({"content": [empty, bare]}, finish="stop"), show_thinking=show)
    assert _types(events) == [EV_DONE] and events[-1]["assistant_msg"]["content"] == [empty, bare]


@pytest.mark.asyncio
async def test_mistral_thinking_is_retained_when_its_display_is_hidden():
    block = {"type": "thinking", "thinking": [{"type": "text", "text": "deep"}]}
    events = await _events(_provider("mistral"), _frame({"content": [block]}, finish="stop"))
    assert _types(events) == [EV_DONE] and events[-1]["assistant_msg"]["content"] == [block]


@pytest.mark.asyncio
async def test_mistral_string_deltas_after_blocks_become_a_text_block():
    block = {"type": "thinking", "thinking": [{"type": "text", "text": "deep"}]}
    events = await _events(_provider("mistral"), _frame({"content": [block]}), _frame({"content": "one"}),
                           _frame({"content": " two"}), _frame({"content": ""}), _frame(finish="stop"))
    assert [e["text"] for e in events[:-1]] == ["one", " two"]
    assert events[-1]["assistant_msg"]["content"] == [block, {"type": "text", "text": "one two"}]


@pytest.mark.asyncio
async def test_mistral_plain_strings_with_no_blocks_stay_a_plain_reply():
    events = await _events(_provider("mistral"), _frame({"content": "hello there"}), _frame(finish="stop"))
    assert events[-1]["assistant_msg"]["content"] == "hello there"


@pytest.mark.asyncio
async def test_mistral_block_failure_keeps_the_events_before_it():
    good = {"type": "text", "text": "first"}
    bad = {"type": "thinking", "thinking": 5}
    events = await _events(_provider("mistral"), _frame({"content": [good, bad]}, finish="stop"))
    assert events[0] == {"type": EV_TEXT, "text": "first"} and _failure(events) == _MALFORMED


@pytest.mark.asyncio
async def test_opencode_mistral_family_uses_typed_content():
    block = {"type": "text", "text": "ans"}
    events = await _events(_provider("opencode", "mistral-large"), _frame({"content": [block]}, finish="stop"))
    assert events[-1]["assistant_msg"]["content"] == [block]


@pytest.mark.parametrize("show, expected", [
    (True, [(EV_THINKING, "plan"), (EV_TEXT, "answer")]),
    (False, [(EV_TEXT, "answer")]),
])
@pytest.mark.asyncio
async def test_inline_think_tags_split_across_chunks(show, expected):
    events = await _events(_provider("ollama"), _frame({"content": "<th"}), _frame({"content": "ink>plan</thi"}),
                           _frame({"content": "nk>answer"}), _frame(finish="stop"), show_thinking=show)
    shown = [(e["type"], e["text"]) for e in events if e["type"] in (EV_TEXT, EV_THINKING)]
    merged: list[tuple[str, str]] = []
    for kind, text in shown:
        if merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + text)
        else:
            merged.append((kind, text))
    assert merged == expected and events[-1]["assistant_msg"]["content"] == "answer"


@pytest.mark.parametrize("show", [True, False])
@pytest.mark.asyncio
async def test_an_unclosed_think_tail_is_flushed_as_thinking(show):
    events = await _events(_provider("ollama"), _frame({"content": "<think>plan"}), _frame(finish="stop"),
                           show_thinking=show)
    assert (EV_THINKING in _types(events)) is show
    assert events[-1]["assistant_msg"]["content"] == ""
    if show:
        assert "".join(e["text"] for e in events if e["type"] == EV_THINKING) == "plan"


@pytest.mark.asyncio
async def test_a_short_reply_held_back_as_a_possible_tag_is_flushed_as_text():
    events = await _events(_provider(), _frame({"content": "hi"}), _frame(finish="stop"))
    assert events[0] == {"type": EV_TEXT, "text": "hi"} and events[-1]["assistant_msg"]["content"] == "hi"


@pytest.mark.asyncio
async def test_flushed_text_precedes_the_refusal_of_the_tool_calls():
    tool = _tool(arguments="{}")
    events = await _events(_provider(), _frame({"content": "hi", "tool_calls": [tool]}, finish="stop"))
    assert events[0] == {"type": EV_TEXT, "text": "hi"} and _failure(events) == _INCOMPLETE


# ---------------------------------------------------------------- tool call fragments


@pytest.mark.parametrize("fragments", ["x", 1, {"index": 0}, True, {}, "", 0, None])
@pytest.mark.asyncio
async def test_tool_calls_that_are_not_a_list_are_malformed(fragments):
    events = await _events(_provider(), _frame({"tool_calls": fragments}), _frame(finish="tool_calls"))
    assert _failure(events) == _MALFORMED and len(events) == 1


@pytest.mark.parametrize("fragment", ["x", 1, None, ["a"]])
@pytest.mark.asyncio
async def test_a_tool_fragment_that_is_not_a_mapping_is_malformed(fragment):
    events = await _events(_provider(), _frame({"tool_calls": [fragment]}), _frame(finish="tool_calls"))
    assert _failure(events) == _MALFORMED and len(events) == 1


@pytest.mark.parametrize("index", [-1, True, False, "0", 1.0, None, [0]])
@pytest.mark.asyncio
async def test_a_tool_index_must_be_a_non_negative_integer(index):
    events = await _events(_provider(), _frame({"tool_calls": [_tool(index=index, arguments="{}")]}),
                           _frame(finish="tool_calls"))
    assert _failure(events) == _MALFORMED and len(events) == 1


@pytest.mark.parametrize("function", ["x", 1, ["name"], None, True])
@pytest.mark.asyncio
async def test_a_tool_function_that_is_not_a_mapping_is_malformed(function):
    fragment = {"index": 0, "id": "c1", "function": function}
    events = await _events(_provider(), _frame({"tool_calls": [fragment]}), _frame(finish="tool_calls"))
    assert _failure(events) == _MALFORMED and len(events) == 1


@pytest.mark.parametrize("arguments", [{"a": 1}, 1, ["x"], None, True])
@pytest.mark.asyncio
async def test_tool_arguments_must_be_json_text(rejected, arguments):
    fragment = {"index": 0, "id": "c1", "function": {"name": "t", "arguments": arguments}}
    events = await _events(_provider(), _frame({"tool_calls": [fragment]}), _frame(finish="tool_calls"))
    assert _failure(events) == _MALFORMED and len(events) == 1
    rejected.assert_called_once_with("c1", "t", arguments, reason="The provider must send tool arguments as JSON text.")


@pytest.mark.asyncio
async def test_a_fragment_without_arguments_keeps_what_the_call_has():
    events = await _events(_provider(), _frame({"tool_calls": [_tool(arguments='{"a"')]}),
                           _frame({"tool_calls": [{"index": 0, "function": {}}]}),
                           _frame({"tool_calls": [{"index": 0}]}),
                           _frame({"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}),
                           _frame(finish="tool_calls"))
    assert events[0] == {"type": EV_TOOL, "id": "c1", "name": "t", "input": {"a": 1}}
    assert events[-1]["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_a_fragment_with_only_a_name_declares_a_call_with_empty_arguments():
    events = await _events(_provider(), _frame({"tool_calls": [_tool()]}, finish="tool_calls"))
    assert events[0]["input"] == {}
    call = events[-1]["assistant_msg"]["tool_calls"][0]
    assert call == {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}


@pytest.mark.asyncio
async def test_a_fragment_without_an_index_continues_slot_zero():
    fragment = {"id": "c1", "function": {"name": "t", "arguments": "{}"}}
    events = await _events(_provider(), _frame({"tool_calls": [fragment]}, finish="tool_calls"))
    assert events[0]["id"] == "c1"


@pytest.mark.asyncio
async def test_calls_are_dispatched_in_index_order_whatever_their_arrival():
    events = await _events(_provider(), _frame({"tool_calls": [_tool(3, "c3", arguments="{}")]}),
                           _frame({"tool_calls": [_tool(1, "c1", arguments="{}")]}),
                           _frame(finish="tool_calls"))
    assert [e["id"] for e in events[:2]] == ["c1", "c3"]
    assert [c["id"] for c in events[-1]["assistant_msg"]["tool_calls"]] == ["c1", "c3"]


@pytest.mark.asyncio
async def test_later_fragments_replace_id_and_name_but_empty_ones_do_not():
    events = await _events(_provider(), _frame({"tool_calls": [_tool(call_id="a", name="first", arguments="{}")]}),
                           _frame({"tool_calls": [_tool(call_id="", name="")]}),
                           _frame({"tool_calls": [_tool(call_id="b", name="second")]}),
                           _frame(finish="tool_calls"))
    assert (events[0]["id"], events[0]["name"]) == ("b", "second")


@pytest.mark.asyncio
async def test_plain_providers_concatenate_argument_fragments_gemini_merges_whole_ones(rejected):
    fragments = (_frame({"tool_calls": [_tool(arguments="{}")]}),
                 _frame({"tool_calls": [{"index": 0, "function": {"arguments": '{"a": 1}'}}]}))
    plain = await _events(_provider(), *fragments, _frame(finish="tool_calls"))
    assert _types(plain) == [EV_INVALID_TOOLS] and plain[0]["assistant_msg"]["tool_calls"][0]["function"]["arguments"] == "{}"
    gemini = await _events(_provider("gemini"), *fragments, _frame(finish="stop"))
    assert gemini[0]["input"] == {"a": 1}


@pytest.mark.asyncio
async def test_gemini_ids_pick_the_slot_and_a_stop_with_calls_means_tool_calls():
    first = {"id": "a", "function": {"name": "t", "arguments": '{"n": 1}'}}
    second = {"id": "b", "function": {"name": "u", "arguments": '{"n": 2}'}}
    events = await _events(_provider("gemini"), _frame({"tool_calls": [first]}), _frame({"tool_calls": [second]}),
                           _frame(finish="stop"))
    assert [e["id"] for e in events[:2]] == ["a", "b"] and events[-1]["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_a_gemini_stop_without_calls_stays_a_stop():
    events = await _events(_provider("gemini"), _frame({"content": "hello!!!"}, finish="stop"))
    assert events[-1]["stop_reason"] == "end_turn"


@pytest.mark.parametrize("kind, kept", [("gemini", True), ("chatgpt", False)])
@pytest.mark.asyncio
async def test_a_thought_signature_on_the_call_returns_with_it(kind, kept):
    signed = _tool(arguments="{}", extra_content={"google": {"thought_signature": "s"}})
    events = await _events(_provider(kind), _frame({"tool_calls": [signed]}, finish="tool_calls"))
    call = events[-1]["assistant_msg"]["tool_calls"][0]
    assert ("extra_content" in call) is kept


@pytest.mark.parametrize("kind, kept", [("gemini", True), ("chatgpt", False)])
@pytest.mark.asyncio
async def test_a_thought_signature_on_the_delta_reaches_the_next_call_only(kind, kept):
    extra = {"google": {"thought_signature": "s"}}
    events = await _events(
        _provider(kind), _frame({"extra_content": extra, "tool_calls": [_tool(0, "a", arguments="{}")]}),
        _frame({"tool_calls": [_tool(1, "b", arguments="{}")]}), _frame(finish="tool_calls"))
    first, second = events[-1]["assistant_msg"]["tool_calls"]
    assert first.get("extra_content") == (extra if kept else None) and "extra_content" not in second


@pytest.mark.asyncio
async def test_a_pending_signature_never_replaces_one_the_call_already_has():
    own, pending = {"google": {"thought_signature": "own"}}, {"google": {"thought_signature": "pending"}}
    events = await _events(
        _provider("gemini"), _frame({"tool_calls": [_tool(call_id="a", arguments="{}", extra_content=own)]}),
        _frame({"extra_content": pending, "tool_calls": [{"id": "a", "function": {}}]}),
        _frame(finish="tool_calls"))
    assert events[-1]["assistant_msg"]["tool_calls"][0]["extra_content"] == own


@pytest.mark.asyncio
async def test_a_non_mapping_gemini_extra_is_ignored():
    fragment = _tool(arguments="{}", extra_content="x")
    events = await _events(_provider("gemini"), _frame({"extra_content": 5, "tool_calls": [fragment]},
                                                       finish="tool_calls"))
    assert "extra_content" not in events[-1]["assistant_msg"]["tool_calls"][0]


@pytest.mark.parametrize("fragments", [{}, "", 0, False])
@pytest.mark.asyncio
async def test_an_empty_tool_calls_field_of_another_shape_is_malformed_not_absent(fragments):
    events = await _events(_provider(), _frame({"tool_calls": fragments}, finish="stop"))
    assert _types(events) == [EV_ERROR] and _failure(events) == _MALFORMED


def test_misshapen_rows_are_classified_rather_than_left_to_the_outer_guard():
    for chunk in ({"choices": [1]}, {"choices": [{"delta": 1}]}):
        with pytest.raises(_ProviderResponseError):
            _chunk_choice(chunk)
    for fragment in (1, {"function": 1}):
        with pytest.raises(_ProviderResponseError):
            _merge_tool_fragment(fragment, _ChatStream(False))


@pytest.mark.asyncio
async def test_a_bad_fragment_keeps_the_text_of_its_own_chunk():
    events = await _events(_provider(), _frame({"content": "before  ", "tool_calls": "x"}))
    assert _types(events) == [EV_TEXT, EV_ERROR] and _failure(events) == _MALFORMED


@pytest.mark.asyncio
async def test_reasoning_details_of_another_shape_are_ignored_not_iterated():
    events = await _events(_provider("openrouter"), _frame({"content": "hello!!!", "reasoning_details": 5}, finish="stop"))
    assert _types(events)[-1] == EV_DONE and "reasoning_details" not in events[-1]["assistant_msg"]


@pytest.mark.asyncio
async def test_a_fragment_without_an_index_extends_the_call_in_slot_zero():
    events = await _events(_provider(), _frame({"tool_calls": [_tool(arguments='{"a"')]}),
                           _frame({"tool_calls": [{"function": {"arguments": ": 1}"}}]}),
                           _frame(finish="tool_calls"))
    assert events[0]["input"] == {"a": 1}


@pytest.mark.asyncio
async def test_an_empty_id_or_name_leaves_the_call_as_it_was():
    events = await _events(_provider(), _frame({"tool_calls": [_tool(call_id="a", name="first", arguments="{}")]}),
                           _frame({"tool_calls": [_tool(call_id="", name="")]}),
                           _frame(finish="tool_calls"))
    assert (events[0]["id"], events[0]["name"]) == ("a", "first")


@pytest.mark.asyncio
async def test_null_gemini_arguments_are_refused_rather_than_read_as_empty(rejected):
    fragment = {"id": "c1", "function": {"name": "t", "arguments": None}}
    events = await _events(_provider("gemini"), _frame({"tool_calls": [fragment]}, finish="tool_calls"))
    assert _types(events) == [EV_ERROR] and _failure(events) == _MALFORMED
    rejected.assert_called_once()


# ---------------------------------------------------------------- how a stream ends


@pytest.mark.parametrize("finish, code, retryable", [
    ("sensitive", "provider_blocked", False),
    ("model_context_window_exceeded", "provider_context_limit", False),
    ("model_length", "provider_context_limit", False),
    ("network_error", "provider_transient", True),
    ("aborted", "provider_transient", True),
    ("error", "provider_transient", True),
    ("insufficient_system_resource", "provider_transient", True),
])
@pytest.mark.asyncio
async def test_a_failing_finish_reason_outranks_a_complete_tool_call(rejected, finish, code, retryable):
    events = await _events(_provider(), _frame({"content": "a tail", "tool_calls": [_tool(arguments="{}")]}, finish=finish))
    assert _failure(events) == (code, retryable)
    assert EV_TOOL not in _types(events) and EV_DONE not in _types(events)
    rejected.assert_not_called()


@pytest.mark.asyncio
async def test_a_failing_finish_reason_drops_the_held_back_tail():
    events = await _events(_provider(), _frame({"content": "hi"}, finish="error"))
    assert _types(events) == [EV_ERROR]


@pytest.mark.parametrize("finish", ["weird", "end_turn", "tool_use", "max_tokens"])
@pytest.mark.asyncio
async def test_an_unknown_finish_reason_is_malformed(finish):
    events = await _events(_provider(), _frame({"content": "hello!!!"}, finish=finish))
    assert _failure(events) == _MALFORMED and EV_DONE not in _types(events)


@pytest.mark.asyncio
async def test_repetition_truncation_counts_as_the_length_limit():
    events = await _events(_provider("mimo"), _frame({"content": "hello!!!"}, finish="repetition_truncation"))
    assert events[-1]["type"] == EV_DONE and events[-1]["stop_reason"] == "max_tokens"


@pytest.mark.asyncio
async def test_a_tool_calls_finish_with_no_call_is_malformed():
    events = await _events(_provider(), _frame({"content": "hello!!!"}, finish="tool_calls"))
    assert _failure(events) == _MALFORMED and EV_DONE not in _types(events)


@pytest.mark.parametrize("finish, expected", [
    ("stop", ("provider_incomplete", True)), ("length", ("provider_incomplete", False)),
])
@pytest.mark.asyncio
async def test_complete_calls_without_a_tool_calls_finish_do_not_run(finish, expected):
    events = await _events(_provider(), _frame({"tool_calls": [_tool(arguments="{}")]}, finish=finish))
    assert _failure(events) == expected and _types(events) == [EV_ERROR]


@pytest.mark.asyncio
async def test_duplicate_call_ids_run_nothing_and_are_recorded(rejected):
    fragments = [_tool(0, "dup", "t", '{"n": 1}'), _tool(1, "dup", "u", '{"n": 2}')]
    events = await _events(_provider(), _frame({"tool_calls": fragments}, finish="tool_calls"))
    assert _types(events) == [EV_ERROR] and _failure(events) == _MALFORMED
    assert [call.args for call in rejected.call_args_list] == [("dup", "t", {"n": 1}), ("dup", "u", {"n": 2})]
    for call in rejected.call_args_list:
        assert call.kwargs == {"reason": "The response contains duplicate tool call IDs.", "finish_reason": "tool_calls"}


@pytest.mark.asyncio
async def test_distinct_call_ids_are_all_dispatched_before_done(rejected):
    fragments = [_tool(0, "a", "t", "{}"), _tool(1, "b", "u", "{}")]
    events = await _events(_provider(), _frame({"tool_calls": fragments}, finish="tool_calls"))
    assert _types(events) == [EV_TOOL, EV_TOOL, EV_DONE]
    rejected.assert_not_called()


@pytest.mark.asyncio
async def test_a_stream_that_stops_with_calls_records_each_and_is_incomplete(rejected):
    fragments = [_tool(0, "a", "t", "{}"), _tool(1, "b", "u", "{}")]
    events = await _events(_provider(), _frame({"tool_calls": fragments}))
    assert _types(events) == [EV_ERROR] and _failure(events) == _INCOMPLETE
    reason = "The provider response ended before the tool call was complete."
    assert [(c.args, c.kwargs) for c in rejected.call_args_list] == [
        (("a", "t", "{}"), {"reason": reason}), (("b", "u", "{}"), {"reason": reason}),
    ]


@pytest.mark.parametrize("call_id, name, problem", [
    (None, "t", "The tool call has no usable ID."), ("c1", None, "The tool call has no usable name."),
    ("c1", "bad name", "The tool call has no usable name."),
])
@pytest.mark.parametrize("finish, retryable", [("tool_calls", True), ("length", False)])
@pytest.mark.asyncio
async def test_a_call_with_an_unusable_identity_is_refused_and_recorded(rejected, call_id, name, problem, finish, retryable):
    fragment = {"index": 0, "function": {"arguments": "{}"}}
    if call_id:
        fragment["id"] = call_id
    if name:
        fragment["function"]["name"] = name
    events = await _events(_provider(), _frame({"tool_calls": [fragment]}, finish=finish))
    assert _failure(events) == ("provider_malformed", retryable) and len(events) == 1
    rejected.assert_called_once()
    assert rejected.call_args.kwargs["finish_reason"] == finish


@pytest.mark.parametrize("arguments", ["[1]", "1", "null", '"text"', "true"])
@pytest.mark.asyncio
async def test_arguments_that_are_valid_json_but_no_object_are_refused(rejected, arguments):
    events = await _events(_provider(), _frame({"tool_calls": [_tool(arguments=arguments)]}))
    assert _failure(events) == _MALFORMED
    assert rejected.call_args.kwargs["finish_reason"] is None


@pytest.mark.asyncio
async def test_truncated_arguments_at_the_length_limit_name_the_size(rejected):
    fragment = _tool(arguments='{"a": ')
    events = await _events(_provider(), _frame({"content": "hi", "tool_calls": [fragment]}, finish="length"))
    assert _types(events) == [EV_TEXT, EV_ERROR]
    last = events[-1]
    assert last["code"] == "tool_call_truncated" and last["retryable"] is False and last["status"] == 0
    assert last["diagnostic"] == {"category": "tool_call_truncated"}
    assert rejected.call_args.kwargs["reason"] == "The output length limit interrupted the tool arguments."
    assert rejected.call_args.kwargs["finish_reason"] == "length"
    assert isinstance(rejected.call_args.kwargs["error"], json.JSONDecodeError)


@pytest.mark.asyncio
async def test_arguments_cut_by_a_dropped_stream_ask_for_a_resend(rejected):
    events = await _events(_provider(), _frame({"tool_calls": [_tool(arguments='{"a": ')]}))
    last = events[-1]
    assert last["code"] == "tool_call_cut" and last["retryable"] is True and last["status"] == 0
    assert last["diagnostic"] == {"category": "tool_call_cut"}
    assert rejected.call_args.kwargs["reason"] == "The stream ended before the tool arguments were complete."
    assert rejected.call_args.kwargs["finish_reason"] is None


@pytest.mark.asyncio
async def test_a_good_sibling_of_bad_arguments_never_runs_and_history_carries_empty_arguments(rejected):
    fragments = [_tool(0, "a", "t", '{"n": 1}'), _tool(1, "b", "u", '{"n": ')]
    events = await _events(_provider(), _frame({"content": "note", "tool_calls": fragments}, finish="tool_calls"))
    assert _types(events)[-1] == EV_INVALID_TOOLS and EV_TOOL not in _types(events)
    event = events[-1]
    assert event["code"] == "bad_tool_call"
    first, second = event["assistant_msg"]["tool_calls"]
    assert first["function"]["arguments"] == '{"n": 1}' and second["function"]["arguments"] == "{}"
    assert event["assistant_msg"]["content"] == "note"
    assert [row["tool_use_id"] for row in event["results"]] == ["a", "b"]
    assert "not valid JSON" in event["results"][0]["result_text"] and event["results"][1]["is_error"]
    assert rejected.call_args.kwargs["reason"] == "Tool arguments are not valid JSON."


@pytest.mark.parametrize("fragments", [
    [_tool(0, "dup", "t", "{bad"), _tool(1, "dup", "t", "{}")],
])
@pytest.mark.asyncio
async def test_bad_arguments_that_cannot_pair_with_results_end_the_turn(rejected, fragments):
    events = await _events(_provider(), _frame({"tool_calls": fragments}, finish="tool_calls"))
    assert _types(events) == [EV_ERROR]
    assert events[0]["code"] == "bad_tool_call" and events[0]["retryable"] is True and events[0]["status"] == 0


@pytest.mark.parametrize("finish", ["stop", "weird"])
@pytest.mark.asyncio
async def test_bad_arguments_on_another_finish_end_the_turn(rejected, finish):
    events = await _events(_provider(), _frame({"tool_calls": [_tool(arguments="{bad")]}, finish=finish))
    assert _types(events) == [EV_ERROR] and events[0]["code"] == "bad_tool_call"


@pytest.mark.asyncio
async def test_the_tool_only_reply_replays_empty_text_not_null():
    events = await _events(_provider(), _frame({"tool_calls": [_tool(arguments="{}")]}, finish="tool_calls"))
    message = events[-1]["assistant_msg"]
    assert message["content"] == "" and message["role"] == "assistant"


@pytest.mark.asyncio
async def test_a_plain_reply_carries_no_optional_replay_fields():
    events = await _events(_provider(), _frame({"content": "hello!!!"}, finish="stop"))
    assert events[-1]["assistant_msg"] == {"role": "assistant", "content": "hello!!!"}


@pytest.mark.asyncio
async def test_finish_reason_read_from_a_chunk_with_a_delta_and_the_last_reason_wins():
    events = await _events(_provider(), _frame({"content": "hello!!!"}, finish="length"), _frame(finish="stop"))
    assert events[-1]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_a_later_empty_finish_reason_leaves_the_earlier_one():
    events = await _events(_provider(), _frame(finish="length"), _frame({"content": "hello!!!"}))
    assert events[-1]["stop_reason"] == "max_tokens"
