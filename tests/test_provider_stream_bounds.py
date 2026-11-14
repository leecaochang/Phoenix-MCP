"""Provider streams stay linear, bounded in time and in argument size, whatever the provider sends."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

from aiohttp import SocketTimeoutError
import pytest

from custom_components.phoenix_mcp import agent_turn, provider_constants
from custom_components.phoenix_mcp.llm import base as llm_base
from custom_components.phoenix_mcp.llm.accounts import AgentCliStore, ProviderConfig
from custom_components.phoenix_mcp.llm.provider_claude import ClaudeProvider
from custom_components.phoenix_mcp.llm.provider_openai import OpenAICompatProvider, _ChatStream, _merge_tool_fragment
from custom_components.phoenix_mcp.provider_constants import AGENT_CHAT_MAX_TOOL_ARGUMENT_CHARS

# Linear parsing of these streams takes about a second; the old quadratic copying took fifteen
# of seconds for the same input, so this bound is generous without being meaningless.
_CPU_BOUND_SECONDS = 6.0


class _Content:
    """A response body handed over in chunks without ever suspending, as aiohttp does when buffered."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.chunks = 0

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._data), size):
            self.chunks += 1
            yield self._data[start:start + size]


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.content = _Content(body)


def _openai_tool_stream(fragments: int, piece: str) -> bytes:
    def frame(tool: dict | None = None, finish: str | None = None) -> str:
        delta = {"tool_calls": [tool]} if tool else {}
        return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}, separators=(",", ":"))

    frames = [frame({"index": 0, "id": "c1", "function": {"name": "get_state", "arguments": '{"a":"'}})]
    frames += [frame({"index": 0, "function": {"arguments": piece}})] * fragments
    frames += [frame({"index": 0, "function": {"arguments": '"}'}}), frame(finish="tool_calls")]
    return ("\n\n".join(frames) + "\n\n").encode()


def _claude_stream(fragments: int, piece: str, kind: str) -> bytes:
    start = ({"type": "tool_use", "id": "c1", "name": "get_state", "input": {}} if kind == "json"
             else {"type": "text", "text": ""})
    delta = ({"type": "input_json_delta", "partial_json": piece} if kind == "json"
             else {"type": "text_delta", "text": piece})
    events = [{"type": "message_start", "message": {"usage": {}}},
              {"type": "content_block_start", "index": 0, "content_block": start}]
    if kind == "json":
        events.append({"type": "content_block_delta", "index": 0,
                       "delta": {"type": "input_json_delta", "partial_json": '{"a":"'}})
    events += [{"type": "content_block_delta", "index": 0, "delta": delta}] * fragments
    if kind == "json":
        events.append({"type": "content_block_delta", "index": 0,
                       "delta": {"type": "input_json_delta", "partial_json": '"}'}})
    events += [{"type": "content_block_stop", "index": 0},
               {"type": "message_delta", "delta": {"stop_reason": "tool_use" if kind == "json" else "end_turn"}},
               {"type": "message_stop"}]
    return ("\n\n".join("data: " + json.dumps(e, separators=(",", ":")) for e in events) + "\n\n").encode()


async def _timed(events) -> tuple[list[dict], float]:
    started = time.process_time()
    out = [event async for event in events]
    return out, time.process_time() - started


async def test_many_small_tool_argument_fragments_parse_in_linear_time():
    provider = OpenAICompatProvider(ProviderConfig(kind="ollama", model="m", base_url="http://h:11434"))
    events, spent = await _timed(provider._parse(_Resp(_openai_tool_stream(250_000, "x" * 12)), False))
    tool = next(e for e in events if e["type"] == llm_base.EV_TOOL)
    assert len(tool["input"]["a"]) == 3_000_000
    assert spent < _CPU_BOUND_SECONDS


@pytest.mark.parametrize("kind", ["json", "text"])
async def test_many_small_claude_fragments_parse_in_linear_time(kind):
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="m", base_url="https://h", api_key="k"))
    events, spent = await _timed(provider._parse(_Resp(_claude_stream(250_000, "x" * 12, kind))))
    done = events[-1]
    assert done["type"] == llm_base.EV_DONE
    block = done["assistant_msg"]["content"][0]
    assert len(block["input"]["a"] if kind == "json" else block["text"]) == 3_000_000
    assert spent < _CPU_BOUND_SECONDS


def test_gemini_resends_still_merge_and_a_hostile_mix_stays_bounded():
    stream = _ChatStream(True)
    for arguments in ("{}", '{"entity_id": "light.a"}', "{}"):
        _merge_tool_fragment({"id": "c1", "function": {"name": "t", "arguments": arguments}}, stream)
    assert json.loads(stream.calls[0]["args"].text()) == {"entity_id": "light.a"}
    # Appended text that is not one object, followed by many whole objects, used to re-parse
    # the whole buffer on every fragment.
    hostile = _ChatStream(True)
    _merge_tool_fragment({"id": "c2", "function": {"name": "t", "arguments": '{"a":"' + "x" * 200_000}}, hostile)
    started = time.process_time()
    for _ in range(5_000):
        _merge_tool_fragment({"id": "c2", "function": {"arguments": "}"}}, hostile)
        _merge_tool_fragment({"id": "c2", "function": {"arguments": "{}"}}, hostile)
    assert time.process_time() - started < _CPU_BOUND_SECONDS
    assert hostile.parse_budget.chars < AGENT_CHAT_MAX_TOOL_ARGUMENT_CHARS


async def test_an_oversized_tool_argument_fails_at_once_and_is_not_retried():
    provider = OpenAICompatProvider(ProviderConfig(kind="ollama", model="m", base_url="http://h:11434"))
    piece = "x" * 1000
    fragments = AGENT_CHAT_MAX_TOOL_ARGUMENT_CHARS // len(piece) + 1000
    resp = _Resp(_openai_tool_stream(fragments, piece))
    events = [event async for event in provider._parse(resp, False)]
    assert [(e["type"], e["code"], e["retryable"]) for e in events] == [(llm_base.EV_ERROR, "bad_tool_call", False)]
    assert not any(e["type"] == llm_base.EV_TOOL for e in events)
    # It stopped reading at the cap, well before the end of the response.
    total = -(-len(resp.content._data) // 2048)
    assert resp.content.chunks < total * 0.9


async def test_a_buffered_stream_gives_the_event_loop_turns():
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    before = ticks
    lines = [line async for line in llm_base._sse_lines(_Resp(b"data: {}\n" * 5_000))]
    task.cancel()
    assert len(lines) == 5_000
    assert ticks - before >= 5_000 // llm_base._SSE_YIELD_EVERY_LINES - 1


class _Dripping:
    """A response that sends one data line, then only keep-alive comments, forever."""

    def __init__(self, first: bytes) -> None:
        self._first = first

    async def iter_chunked(self, _size: int):
        yield self._first
        while True:
            await asyncio.sleep(0.01)
            yield b": PROCESSING\n\n"


async def test_keep_alive_comments_alone_do_not_hold_a_stream_open():
    resp = _Resp(b"")
    resp.content = _Dripping(b"data: {}\n\n")
    with patch.object(llm_base, "AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS", 0.1):
        with pytest.raises(SocketTimeoutError):
            async with asyncio.timeout(5):
                async for _line in llm_base._sse_lines(resp):
                    pass


class _ThinkingGateway:
    """A gateway that sends only keep-alive comments while the model thinks, then the answer."""

    async def iter_chunked(self, _size: int):
        for _ in range(30):
            await asyncio.sleep(0.01)
            yield b": PROCESSING\n\n"
        yield b'data: {"answer": 1}\n\n'


async def test_keep_alive_comments_hold_the_wait_for_the_first_data_open():
    resp = _Resp(b"")
    resp.content = _ThinkingGateway()
    with patch.object(llm_base, "AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS", 0.1):
        async with asyncio.timeout(5):
            lines = [line async for line in llm_base._sse_lines(resp)]
    assert 'data: {"answer": 1}' in lines


@pytest.mark.parametrize(("kind", "seconds"), [
    ("ollama", provider_constants.AGENT_HEADLESS_LOCAL_CALL_SECONDS),
    ("ollama_cloud", provider_constants.AGENT_HEADLESS_CALL_SECONDS),
    ("openrouter", provider_constants.AGENT_HEADLESS_CALL_SECONDS),
])
def test_a_local_ollama_call_gets_the_longer_headless_deadline(kind, seconds):
    provider = OpenAICompatProvider(ProviderConfig(kind=kind, model="m", base_url="https://h/v1", api_key="k"))
    assert agent_turn._headless_call_seconds(provider) == seconds
    assert provider_constants.AGENT_HEADLESS_LOCAL_CALL_SECONDS > provider_constants.AGENT_HEADLESS_CALL_SECONDS


class _Session:
    def __init__(self, content) -> None:
        self._content = content

    def post(self, *_args, **_kwargs):
        session = self

        class _Response:
            status = 200
            headers: dict = {}
            content = session._content

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False
        return _Response()

    def get(self, *_args, **_kwargs):
        raise AssertionError("no other request is expected")


async def test_a_provider_dripping_comments_after_its_first_delta_ends_as_silent():
    provider = OpenAICompatProvider(ProviderConfig(kind="openrouter", model="m", base_url="https://h/v1", api_key="k"))
    first = ("data: " + json.dumps({"choices": [{"delta": {"content": "Hello there, friend."}, "finish_reason": None}]}) + "\n\n").encode()
    with patch.object(llm_base, "AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS", 0.1):
        async with asyncio.timeout(5):
            events = [e async for e in provider.stream_turn(_Session(_Dripping(first)), system_prompt="s",
                                                            messages=[], tools=[], options={})]
    assert [e["type"] for e in events] == [llm_base.EV_TEXT, llm_base.EV_ERROR]
    assert events[-1]["code"] == "provider_silent"


def test_an_endless_rate_limit_header_is_dropped_not_raised():
    fields = llm_base._RETRY_AFTER_HEADER_FIELDS
    assert llm_base._rate_limit_headers({"Retry-After": "9" * 4301}, fields) == {}
    assert llm_base._rate_limit_headers({"Retry-After": "9" * 8000}, fields) == {}
    assert llm_base._rate_limit_headers({"Retry-After": "30"}, fields) == {"retry_after_seconds": 30}


class _Trickling:
    """A provider whose events never stop arriving."""

    kind = "test"

    async def stream_turn(self, session, **_kwargs):
        while True:
            yield {"type": llm_base.EV_TEXT, "text": "."}
            await asyncio.sleep(0.01)


async def test_a_call_that_never_finishes_ends_at_its_deadline_as_slow():
    async with asyncio.timeout(5):
        events = [ev async for ev in agent_turn._stream_turn_resilient(
            _Trickling(), None, system_prompt="s", messages=[], tools=[], options={},
            cancel=asyncio.Event(), call_seconds=0.2, dispatch_started=True, write_started=True)]
    last = events[-1]
    assert last["code"] == "provider_after_dispatch" and last["outcome"] == "outcome_unknown"
    assert all(ev["type"] == llm_base.EV_TEXT for ev in events[:-1])
    plain = [ev async for ev in agent_turn._stream_turn_resilient(
        _Trickling(), None, system_prompt="s", messages=[], tools=[], options={},
        cancel=asyncio.Event(), call_seconds=0.2)]
    assert (plain[-1]["code"], plain[-1]["retryable"]) == ("provider_slow", False)


async def test_capability_strings_are_bounded_before_they_are_stored():
    class _Memory:
        async def async_save(self, data):
            self.data = data

    store = AgentCliStore(_Memory())
    store._data = {"acct": {"kind": "openrouter"}}
    assert await store.set_capabilities("acct", {
        "good/model": {"tools": True, "effort_levels": ["low", "high"]},
        "bad\ud800model": {"tools": True},
        "x" * 500: {"tools": True},
        "odd/model": {"tools": True, "replacement": "bad\ud800", "effort_levels": ["ok", "\ud800"],
                      "\ud800": True, "nested": {"a": 1}},
    }, "2026-11-13T00:00:00+00:00")
    stored = store._data["acct"]["capabilities"]
    assert stored == {"good/model": {"tools": True, "effort_levels": ["low", "high"]},
                      "odd/model": {"tools": True}}
    # What Home Assistant's Store writes must still encode.
    json.dumps(store._data, ensure_ascii=False).encode("utf-8")


async def test_an_empty_thinking_off_survives_storage():
    """An empty thinking_off means the model cannot stop thinking, so Agent Chat offers no Off."""
    class _Memory:
        async def async_save(self, data):
            self.data = data

    store = AgentCliStore(_Memory())
    store._data = {"acct": {"kind": "ollama"}}
    await store.set_capabilities("acct", {
        "qwq": {"tools": True, "thinking": True, "thinking_off": ""},
        "gpt-oss:20b": {"thinking": True, "thinking_off": "", "thinking_default": "medium"},
    }, "2026-11-13T00:00:00+00:00")
    stored = store._data["acct"]["capabilities"]
    assert stored["qwq"]["thinking_off"] == "" and stored["gpt-oss:20b"]["thinking_off"] == ""


BASE ="http://ollama.lan:11434"
_LOCAL = [{"name": "llama3.1:8b", "model": "llama3.1:8b", "modified_at": "2026-09-01T10:00:00Z"},
          {"name": "qwen3:14b", "model": "qwen3:14b", "modified_at": "2026-10-01T10:00:00Z"}]
_CLOUD = {"name": "gpt-oss:120b-cloud", "model": "gpt-oss:120b-cloud", "modified_at": "2026-11-01T10:00:00Z",
          "remote_model": "gpt-oss:120b", "remote_host": "https://ollama.com:443"}
_ALIASED = {"name": "house-model", "model": "house-model", "modified_at": "2026-11-02T10:00:00Z",
            "remote_model": "glm-4.6", "remote_host": "https://ollama.com:443"}


class _Ollama:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def _reply(self, body: dict):
        class _Response:
            status = 200
            content = _Content(json.dumps(body).encode())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False
        return _Response()

    def get(self, url, **_kwargs):
        assert url == f"{BASE}/api/tags"
        return self._reply({"models": self.rows})

    def post(self, url, **_kwargs):
        assert url == f"{BASE}/api/show"
        return self._reply({"capabilities": ["completion", "tools"]})


async def _refresh(store: AgentCliStore, rows: list[dict]) -> None:
    """The same steps as the catalogue refresh: list, capabilities, catalogue."""
    provider = OpenAICompatProvider(store.resolve("acct"))
    models = await provider.list_models(_Ollama(rows))
    caps = await provider.list_model_capabilities(_Ollama(rows), models)
    await store.set_capabilities("acct", caps, "2026-11-13T00:00:00+00:00")
    await store.set_catalog("acct", models, provider.model_created, "2026-11-13T00:00:00+00:00",
                            [m for m in models if (caps.get(m) or {}).get("tools") is not False],
                            provider.model_remote)


@pytest.mark.parametrize("stored", ["", "qwen3:14b"])
async def test_a_local_ollama_account_stays_local_after_cloud_models_are_pulled(stored):
    class _Memory:
        async def async_save(self, data):
            self.data = data

    store = AgentCliStore(_Memory())
    store._data = {"acct": {"kind": "ollama", "base_url": BASE, "model": stored}}
    await _refresh(store, _LOCAL)
    # Cloud models appear, newer than every local one; the chosen local model is then removed.
    await _refresh(store, [_LOCAL[0], _CLOUD, _ALIASED])
    model = store.resolve("acct").model
    assert model not in {"gpt-oss:120b-cloud", "house-model"}
    assert model == ("llama3.1:8b" if not stored else "qwen3:14b")
