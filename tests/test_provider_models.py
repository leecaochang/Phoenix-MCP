"""Provider model catalogues: automatic mode, retirement, listing, and per-provider request rules."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import agentcli
from custom_components.phoenix_mcp.agentcli import (
    ClaudeProvider,
    OpenAICompatProvider,
    ProviderConfig,
    _filter_chatgpt_models,
    _filter_gemini_models,
    _filter_meta_models,
    _filter_mistral_models,
    _filter_nvidia_models,
    _filter_opencode_models,
    _filter_openrouter_models,
)


class _Content:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def iter_chunked(self, _n: int):
        yield self._data


class _Resp:
    def __init__(self, status=200, body=b"", json_data=None, text=""):
        self.status, self.content, self._json, self._text = status, _Content(body), json_data, text
        self.headers: dict = {}

    async def text(self):
        return self._text

    async def json(self, content_type=None):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Session:
    """Answers each request from a queue and records what was sent."""

    def __init__(self, *responses: _Resp) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def _next(self, **call):
        self.calls.append(call)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def get(self, url, headers=None, timeout=None, allow_redirects=None, params=None):
        return self._next(method="GET", url=url, params=params, headers=headers, allow_redirects=allow_redirects)

    def post(self, url, headers=None, json=None, timeout=None, allow_redirects=None):  # noqa: A002
        # A copy: the provider edits its body in place when it retries.
        return self._next(method="POST", url=url, json=copy.deepcopy(json), headers=headers,
                          allow_redirects=allow_redirects)


def _sse(*frames: str) -> bytes:
    return ("\n\n".join(frames) + "\n\n").encode()


async def _collect(agen):
    return [ev async for ev in agen]


# ---------------------------------------------------------------- store: automatic mode

@pytest.mark.asyncio
async def test_new_account_is_automatic_and_follows_the_catalogue():
    store = agentcli.AgentCliStore(AsyncMock())
    iid = await store.add("claude", {"api_key": "k"})
    assert store.resolve(iid).model == ""
    listed = store.list_instances()[0]
    assert listed["model_mode"] == "auto" and listed["model"] == ""

    await store.set_catalog(iid, ["claude-haiku-4-5", "claude-opus-4-8"], {}, "t1")
    assert store.resolve(iid).model == "claude-opus-4-8"

    # The provider ships a newer model; the account follows it with no edit.
    await store.set_catalog(iid, ["claude-opus-5-5", "claude-opus-4-8", "claude-haiku-4-5"], {}, "t2")
    assert store.resolve(iid).model == "claude-opus-5-5"
    assert store.list_instances()[0]["model"] == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_a_retired_explicit_model_is_replaced_but_a_custom_one_is_not():
    store = agentcli.AgentCliStore(AsyncMock())
    retired = await store.add("deepseek", {"api_key": "a", "model": "deepseek-v4-flash"})
    custom = await store.add("deepseek", {"api_key": "b", "model": "my-finetune"})
    for iid in (retired, custom):
        await store.set_catalog(iid, ["deepseek-v4-flash", "deepseek-v4-pro"], {}, "t1")
    assert store.resolve(retired).model == "deepseek-v4-flash"

    for iid in (retired, custom):
        await store.set_catalog(iid, ["deepseek-flash", "deepseek-v4-pro"], {}, "t2")
    assert store.resolve(retired).model == "deepseek-flash"
    assert store.list_instances()[0]["model_replaced_from"] in (None, "deepseek-v4-flash")
    assert store.resolve(custom).model == "my-finetune"
    # A per-surface override (Voice, AI Task, the chat dropdown) gets the same rule:
    # an id the provider never listed is honoured, a retired one is replaced.
    assert store.resolve(retired, "x-y").model == "x-y"
    assert store.resolve(retired, "deepseek-v4-flash").model == "deepseek-flash"


@pytest.mark.asyncio
async def test_an_empty_listing_never_replaces_the_catalogue():
    store = agentcli.AgentCliStore(AsyncMock())
    iid = await store.add("claude", {"api_key": "k"})
    await store.set_catalog(iid, ["claude-opus-4-8"], {}, "t1")
    assert await store.set_catalog(iid, [], {}, "t2") is False
    assert store.resolve(iid).model == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_automatic_mode_skips_a_model_that_cannot_call_tools():
    store = agentcli.AgentCliStore(AsyncMock())
    iid = await store.add("chatgpt", {"api_key": "k"})
    await store.set_catalog(iid, ["gpt-6-sol", "gpt-5-sol"], {}, "t1")
    assert store.resolve(iid).model == "gpt-6-sol"
    await store.record_refusal(iid, "gpt-6-sol", "tools", utcnow().isoformat())
    assert store.resolve(iid).model == "gpt-5-sol"
    # The learned refusal expires like every other learned refusal.
    stale = (utcnow() - timedelta(days=3650)).isoformat()
    store._data[iid]["learned"] = {"gpt-6-sol": {"tools": stale}}
    assert store.resolve(iid).model == "gpt-6-sol"


@pytest.mark.asyncio
async def test_decision_accounts_never_follow_the_catalogue():
    store = agentcli.AgentCliStore(AsyncMock())
    iid = await store.add("typesafe", {"api_key": "k", "model": "jev-1.13.0"})
    await store.set_catalog(iid, ["jev-1.13.0", "jev-2.0.0"], {}, "t1")
    assert store.resolve(iid, purpose="decision").model == "jev-1.13.0"
    assert store.list_instances()[0]["model_mode"] == "fixed"


# ---------------------------------------------------------------- Anthropic listing

@pytest.mark.asyncio
async def test_claude_list_models_follows_pagination_and_reads_capabilities():
    page1 = {"data": [{"id": "claude-opus-5-5", "created_at": "2026-07-24T00:00:00Z",
                       "capabilities": {"image_input": {"supported": True},
                                        "thinking": {"supported": True},
                                        "effort": {"supported": True, "low": {"supported": True},
                                                   "high": {"supported": True}, "max": {"supported": False}}}}],
             "has_more": True, "last_id": "claude-opus-5-5"}
    page2 = {"data": [{"id": "claude-haiku-4-5", "created_at": "1970-01-01T00:00:00Z",
                       "capabilities": {"image_input": {"supported": False}}}], "has_more": False}
    session = _Session(_Resp(json_data=page1), _Resp(json_data=page2))
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="", base_url="https://a", api_key="k"))
    models = await provider.list_models(session)
    assert models == ["claude-opus-5-5", "claude-haiku-4-5"]
    assert session.calls[0]["params"]["limit"] == "1000"
    assert session.calls[1]["params"]["after_id"] == "claude-opus-5-5"
    assert provider.model_created == {"claude-opus-5-5": pytest.approx(1784851200.0)}
    caps = await provider.list_model_capabilities(session, models)
    assert caps["claude-opus-5-5"] == {"vision": True, "thinking": True, "effort_levels": ["low", "high"]}
    assert caps["claude-haiku-4-5"] == {"vision": False}


@pytest.mark.asyncio
async def test_claude_list_models_without_a_configured_model_has_no_fallback():
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="", base_url="https://a", api_key="k"))
    assert await provider.list_models(_Session(_Resp(status=500))) == []


@pytest.mark.asyncio
async def test_claude_adaptive_thinking_rejection_retries_with_an_enabled_budget():
    agentcli._CLAUDE_NO_ADAPTIVE.discard("claude-haiku-x")
    err = _Resp(status=400, text=json.dumps({"error": {"type": "invalid_request_error",
                "message": "adaptive thinking is not supported on this model"}}))
    ok = _Resp(body=_sse(
        'data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}',
        'data: {"type":"message_stop"}'))
    session = _Session(err, ok)
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="claude-haiku-x", base_url="https://a", api_key="k"))
    events = await _collect(provider.stream_turn(
        session, system_prompt="s", messages=[], tools=[], options={"max_tokens": 8000}))
    assert [e for e in events if e["type"] == "message_done"]
    first, second = (c["json"] for c in session.calls)
    assert first["thinking"]["type"] == "adaptive" and "output_config" in first
    assert second["thinking"] == {"type": "enabled", "budget_tokens": 4000}
    assert "output_config" not in second
    assert "claude-haiku-x" in agentcli._CLAUDE_NO_ADAPTIVE
    agentcli._CLAUDE_NO_ADAPTIVE.discard("claude-haiku-x")


@pytest.mark.asyncio
async def test_claude_overloaded_stream_error_is_retryable_and_504_is_transient():
    overloaded = _Resp(body=_sse('data: {"type":"error","error":{"type":"overloaded_error","message":"x"}}'))
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="m", base_url="https://a", api_key="k"))
    events = await _collect(provider.stream_turn(
        _Session(overloaded), system_prompt="s", messages=[], tools=[], options={}))
    assert events[-1]["type"] == "provider_error" and events[-1]["retryable"] is True

    gateway = _Resp(status=504, text=json.dumps({"error": {"type": "timeout_error", "message": "x"}}))
    events = await _collect(provider.stream_turn(
        _Session(gateway), system_prompt="s", messages=[], tools=[], options={}))
    assert events[-1]["retryable"] is True

    billing = _Resp(status=402, text=json.dumps({"error": {"type": "billing_error", "message": "x"}}))
    events = await _collect(provider.stream_turn(
        _Session(billing), system_prompt="s", messages=[], tools=[], options={}))
    assert events[-1]["retryable"] is False and events[-1]["code"] == "quota"


# ---------------------------------------------------------------- OpenAI-compatible request rules

def _openai(kind="chatgpt", model="gpt-x"):
    return OpenAICompatProvider(ProviderConfig(kind=kind, model=model, base_url="https://h/v1", api_key="k"))


_DONE_BODY = _sse(
    'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}', "data: [DONE]")
_TOOL = [{"type": "function", "function": {"name": "t", "parameters": {}}}]


@pytest.mark.asyncio
async def test_openai_tool_turns_send_reasoning_effort_none_even_when_a_level_is_chosen():
    agentcli._OPENAI_NO_EFFORT_NONE.discard("gpt-x")
    session = _Session(_Resp(body=_DONE_BODY))
    await _collect(_openai().stream_turn(
        session, system_prompt="s", messages=[], tools=_TOOL, options={"effort": "high"}))
    assert session.calls[0]["json"]["reasoning_effort"] == "none"
    # Without tools the operator's level is honoured.
    session = _Session(_Resp(body=_DONE_BODY))
    await _collect(_openai().stream_turn(
        session, system_prompt="s", messages=[], tools=[], options={"effort": "high"}))
    assert session.calls[0]["json"]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_openai_model_that_rejects_effort_none_is_retried_once_and_remembered():
    agentcli._OPENAI_NO_EFFORT_NONE.discard("gpt-x")
    reject = _Resp(status=400, text=json.dumps({"error": {
        "message": "Unrecognized request argument supplied: reasoning_effort", "type": "invalid_request_error"}}))
    session = _Session(reject, _Resp(body=_DONE_BODY))
    events = await _collect(_openai().stream_turn(
        session, system_prompt="s", messages=[], tools=_TOOL, options={}))
    assert any(e["type"] == "message_done" for e in events)
    assert "reasoning_effort" in session.calls[0]["json"]
    assert "reasoning_effort" not in session.calls[1]["json"]
    # Remembered: the next tool turn does not send the field, so no failing request.
    session = _Session(_Resp(body=_DONE_BODY))
    await _collect(_openai().stream_turn(
        session, system_prompt="s", messages=[], tools=_TOOL, options={}))
    assert len(session.calls) == 1 and "reasoning_effort" not in session.calls[0]["json"]
    agentcli._OPENAI_NO_EFFORT_NONE.discard("gpt-x")


@pytest.mark.asyncio
async def test_openai_responses_only_refusal_is_learned_as_no_tools():
    agentcli._OPENAI_NO_EFFORT_NONE.discard("gpt-x")
    reject = _Resp(status=400, text=json.dumps({"error": {
        "message": "Function calling is not supported with this model on Chat Completions; use the Responses API.",
        "type": "invalid_request_error"}}))
    events = await _collect(_openai().stream_turn(
        _Session(reject), system_prompt="s", messages=[], tools=_TOOL, options={}))
    assert events[-1]["refused"] == "tools"
    # An unrelated 400 that merely contains the word "tools" teaches nothing.
    other = _Resp(status=400, text=json.dumps({"error": {"message": "Invalid schema for tools[0]", "type": "x"}}))
    events = await _collect(_openai().stream_turn(
        _Session(other), system_prompt="s", messages=[], tools=_TOOL, options={}))
    assert events[-1]["refused"] is None


@pytest.mark.asyncio
async def test_probe_uses_max_completion_tokens_for_openai():
    session = _Session(_Resp(status=200))
    await _openai().probe_option(session, {})
    assert "max_completion_tokens" in session.calls[0]["json"] and "max_tokens" not in session.calls[0]["json"]
    session = _Session(_Resp(status=200))
    await _openai("deepseek", "m").probe_option(session, {})
    assert "max_tokens" in session.calls[0]["json"]


def test_groq_tool_results_carry_no_name_but_others_keep_it():
    result = [{"tool_use_id": "c1", "tool_name": "t", "content": "x"}]
    for kind, expect in (("groq", False), ("deepseek", True)):
        messages: list[dict] = []
        _openai(kind, "m").append_tool_results(messages, result)
        assert ("name" in messages[0]) is expect


@pytest.mark.asyncio
async def test_kimi_and_openrouter_replay_reasoning_with_tool_calls():
    tool_frame = ('data: {"choices":[{"delta":{"reasoning_content":"think","reasoning_details":[{"type":"x","id":1}],'
                  '"tool_calls":[{"index":0,"id":"c1","function":{"name":"t","arguments":"{}"}}]},"finish_reason":null}]}')
    body = _sse(tool_frame, 'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}', "data: [DONE]")
    for kind in ("kimi", "openrouter"):
        events = await _collect(_openai(kind, "m").stream_turn(
            _Session(_Resp(body=body)), system_prompt="s", messages=[], tools=_TOOL, options={}))
        message = next(e for e in events if e["type"] == "message_done")["assistant_msg"]
        # OpenRouter replays its structured details; the others replay the text.
        assert ("reasoning_content" in message) is (kind == "kimi")
        assert ("reasoning_details" in message) is (kind == "openrouter")
    events = await _collect(_openai("groq", "m").stream_turn(
        _Session(_Resp(body=body)), system_prompt="s", messages=[], tools=_TOOL, options={}))
    assert "reasoning_content" not in next(e for e in events if e["type"] == "message_done")["assistant_msg"]


# ---------------------------------------------------------------- catalogue filters

def test_chatgpt_filter_drops_responses_only_and_special_purpose_models():
    ids = ["gpt-6-sol", "gpt-6-sol-pro", "o3-deep-research", "gpt-live-1", "gpt-5.6-cyber",
           "gpt-5.3-codex", "chat-latest", "gpt-audio", "o4-mini"]
    assert _filter_chatgpt_models(ids) == ["gpt-6-sol", "o4-mini"]


def test_gemini_filter_drops_media_and_live_models():
    ids = ["models/gemini-3.8-flash", "gemini-3.8-live", "gemini-3.8-flash-tts", "gemini-3.1-flash-image",
           "gemini-2.5-flash-native-audio-preview", "gemini-embedding-001", "gemini-3.5-flash-lite"]
    assert _filter_gemini_models(ids) == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_opencode_legacy_allows_qualified_qwen_models_without_opening_the_family():
    ids = ["gpt-5.5", "claude-sonnet-5-5", "gemini-3.8-flash", "jev-1.13", "grok-4.7", "muse-spark-1.3",
           "qwen3.8-max", "qwen3.5-plus", "qwen3.8-flash", "qwen-next", "deepseek-flash", "kimi-k3", "glm-5.3",
           "big-pickle", "minimax-m3"]
    assert _filter_opencode_models(ids) == [
        "big-pickle", "deepseek-flash", "glm-5.3", "kimi-k3", "minimax-m3", "qwen3.5-plus", "qwen3.8-max",
    ]


@pytest.mark.parametrize("verified_only", [False, True])
def test_opencode_never_restores_an_unsupported_catalog(verified_only):
    assert _filter_opencode_models(
        ["gpt-6.1-sol", "claude-sonnet-5-5", "qwen3.8-flash", "jev-1.13"],
        verified_only=verified_only,
    ) == []


def test_opencode_console_requires_per_model_endpoint_evidence():
    ids = ["qwen3.8-max", "qwen3.5-plus", "qwen3.8-flash", "kimi-k3", "new-vendor-model", "test"]
    assert _filter_opencode_models(ids, verified_only=True) == ["kimi-k3", "qwen3.5-plus", "qwen3.8-max"]
    assert _filter_opencode_models(ids) == ["kimi-k3", "new-vendor-model", "qwen3.5-plus", "qwen3.8-max", "test"]


@pytest.mark.asyncio
async def test_opencode_setup_routes_are_explicit_and_credentials_keep_their_identity(hass):
    from custom_components.phoenix_mcp.const import (
        AGENTCLI_OPENCODE_BASE_URL, AGENTCLI_OPENCODE_CONSOLE_BASE_URL,
    )

    legacy = await agentcli._probe_config("opencode", {"api_key": "secret"})
    console = await agentcli._probe_config("opencode", {
        "api_key": "secret", "endpoint_id": "console", "base_url": "https://untrusted.example",
    })
    assert isinstance(legacy, ProviderConfig) and isinstance(console, ProviderConfig)
    assert legacy.base_url == AGENTCLI_OPENCODE_BASE_URL
    assert console.base_url == AGENTCLI_OPENCODE_CONSOLE_BASE_URL
    assert "base_url" not in agentcli._stored_setup_config(console)
    assert agentcli._stored_setup_config(console)["endpoint_id"] == "console"
    assert isinstance(await agentcli._probe_config("opencode", {
        "api_key": "secret", "endpoint_id": "https://untrusted.example",
    }), agentcli.ProbeConfigError)
    old = {"api_key": "secret", "model": "kimi-k3"}
    store = agentcli.AgentCliStore(AsyncMock())
    iid = await store.add("opencode", old)
    snapshot = copy.deepcopy(store.get(iid))
    assert store.resolve(iid).base_url == AGENTCLI_OPENCODE_BASE_URL
    assert store.resolve(iid).model == "kimi-k3"
    assert store.has_duplicate("opencode", agentcli._stored_setup_config(console))
    assert store.get(iid) == snapshot
    public = next(p for p in agentcli._provider_catalog() if p["kind"] == "opencode")
    assert "Bearer secret" not in json.dumps(public)
    assert "https://" not in json.dumps(public)
    assert [c["value"] for c in public["fields"][0]["choices"]] == ["zen", "console"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["zen", "console"])
@pytest.mark.parametrize("model", ["qwen3.8-max", "qwen3.5-plus"])
async def test_opencode_catalog_and_all_inference_probes_use_their_own_urls(route, model):
    cfg = await agentcli._probe_config("opencode", {
        "api_key": "secret", "endpoint_id": route, "model": model,
    })
    provider = OpenAICompatProvider(cfg)
    base = "https://opencode.ai/zen/v1" if route == "zen" else "https://opencode.ai/inference/openai/v1"
    catalog = f"{base}/models" if route == "zen" else "https://opencode.ai/inference/v1/models"
    session = _Session(_Resp(json_data={"data": [{"id": "kimi-k3"}, {"id": "qwen3.8-max"}, {"id": "qwen3.5-plus"}, {"id": "qwen3.8-flash"}]}))
    assert await provider.validate(session) == (True, "")
    assert await provider.list_models(session) == ["kimi-k3", "qwen3.5-plus", "qwen3.8-max"]
    assert await provider.probe_option(session, {}) == 200
    await provider.probe_image(session, "image", "question")
    assert [c["url"] for c in session.calls] == [catalog, catalog, f"{base}/chat/completions", f"{base}/chat/completions"]
    assert all(c["allow_redirects"] is False for c in session.calls)
    assert all(c["headers"]["Authorization"] == "Bearer secret" for c in session.calls)


def test_opencode_does_not_redirect_credentials_from_a_saved_custom_url():
    provider = OpenAICompatProvider(ProviderConfig(
        kind="opencode", model="custom", base_url="https://operator.example/v1", endpoint_id="console",
    ))
    assert provider._models_url() == "https://operator.example/v1/models"
    assert provider._chat_url() == "https://operator.example/v1/chat/completions"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, {}, [None, "bad", {"id": 12}, {"id": ""}, {"id": "qwen3.8-flash"}]])
async def test_opencode_console_catalog_refuses_bad_rows_without_restoring_the_saved_model(data):
    provider = OpenAICompatProvider(ProviderConfig(
        kind="opencode", model="qwen3.8-max", base_url=agentcli.AGENTCLI_OPENCODE_CONSOLE_BASE_URL,
    ))
    assert await provider.list_models(_Session(_Resp(json_data={"data": data}))) == []


@pytest.mark.asyncio
async def test_opencode_console_refuses_other_api_families_before_any_network_call():
    provider = OpenAICompatProvider(ProviderConfig(
        kind="opencode", model="qwen3.8-flash", base_url=agentcli.AGENTCLI_OPENCODE_CONSOLE_BASE_URL,
    ))
    session = _Session()
    events = await _collect(provider.stream_turn(session, system_prompt="", messages=[], tools=[], options={}))
    assert events[0]["code"] == "model_api_unsupported" and not events[0]["retryable"]
    assert await provider.probe_option(session, {}) == 400
    assert await provider.probe_image(session, "image", "question") == (400, "")
    assert await provider.list_models(_Session(_Resp(status=503))) == []
    assert session.calls == []


@pytest.mark.asyncio
async def test_opencode_replays_reasoning_and_complete_tool_calls_without_usage_flags():
    provider = OpenAICompatProvider(ProviderConfig(
        kind="opencode", model="qwen3.8-max", base_url=agentcli.AGENTCLI_OPENCODE_CONSOLE_BASE_URL,
    ))
    frame = {"choices": [{"delta": {"reasoning_content": "retained reasoning", "tool_calls": [{
        "index": 0, "id": "call_1", "function": {"name": "read", "arguments": '{"x":1}'},
    }]}, "finish_reason": "tool_calls"}]}
    session = _Session(_Resp(body=_sse("data: " + json.dumps(frame))))
    tools = provider.format_tools([{"name": "read", "description": "Read", "inputSchema": {
        "type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"],
    }}])
    assert tools[0]["function"]["parameters"]["properties"]["x"] == {"type": "integer"}
    messages = [{"role": "user", "content": "Read."}]
    events = await _collect(provider.stream_turn(session, system_prompt="", messages=messages, tools=tools, options={}))
    assistant = events[-1]["assistant_msg"]
    assert assistant["reasoning_content"] == "retained reasoning"
    assert not any(e["type"] == "thinking_delta" for e in events)
    provider.append_assistant(messages, assistant)
    provider.append_tool_results(messages, [{"tool_use_id": "call_1", "tool_name": "read", "content": "1"}])
    await _collect(provider.stream_turn(session, system_prompt="", messages=messages, tools=tools, options={}))
    body = session.calls[-1]["json"]
    assert body["messages"][-2] == assistant
    assert body["messages"][-1]["tool_call_id"] == "call_1"
    assert body["tools"] == tools
    assert "stream_options" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "error", "code", "retryable"), [
    (401, {"type": "CreditsError", "message": "Insufficient balance."}, "quota", False),
    (402, {"message": "No credit."}, "quota", False),
    (429, {"type": "RateLimitError", "message": "Slow down."}, "rate_limit", True),
    (403, {"type": "FreeTierError", "message": "Free tier requires OpenCode."}, "auth", False),
])
async def test_opencode_console_keeps_billing_and_rate_limit_classifications(status, error, code, retryable):
    provider = OpenAICompatProvider(ProviderConfig(
        kind="opencode", model="qwen3.8-max", base_url=agentcli.AGENTCLI_OPENCODE_CONSOLE_BASE_URL,
    ))
    session = _Session(_Resp(status=status, text=json.dumps({"error": error})))
    events = await _collect(provider.stream_turn(session, system_prompt="", messages=[], tools=[], options={}))
    assert events[0]["code"] == code and events[0]["retryable"] is retryable
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_opencode_stream_cancellation_closes_the_response_without_retrying():
    entered, closed = asyncio.Event(), asyncio.Event()

    class WaitingContent:
        async def iter_chunked(self, _n):
            entered.set()
            await asyncio.Event().wait()
            yield b""

    class WaitingResponse(_Resp):
        async def __aexit__(self, *_a):
            closed.set()
            return False

    response = WaitingResponse()
    response.content = WaitingContent()
    session = _Session(response)
    provider = OpenAICompatProvider(ProviderConfig(
        kind="opencode", model="kimi-k3", base_url=agentcli.AGENTCLI_OPENCODE_CONSOLE_BASE_URL,
    ))
    task = asyncio.create_task(_collect(provider.stream_turn(
        session, system_prompt="", messages=[], tools=[], options={},
    )))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and len(session.calls) == 1


def test_meta_filter_excludes_non_chat_and_data_sharing_models():
    ids = ["muse-spark-1.3", "muse-spark-1.3-contributor", "muse-image-1.0", "sam-3.1"]
    assert _filter_meta_models(ids) == ["muse-spark-1.3"]


def test_mistral_filter_drops_deprecated_labs_and_magistral_models():
    caps = {"completion_chat": True, "function_calling": True}
    rows = [{"id": "mistral-medium-latest", "capabilities": caps},
            {"id": "mistral-old", "capabilities": caps, "deprecation": "2026-12-01T00:00:00Z"},
            {"id": "labs-thing", "capabilities": caps}, {"id": "magistral-medium-latest", "capabilities": caps},
            {"id": "mistral-embed", "capabilities": {"completion_chat": False, "function_calling": False}}]
    assert _filter_mistral_models(rows) == ["mistral-medium-latest"]


def test_nvidia_and_openrouter_filters_drop_non_chat_entries():
    assert _filter_nvidia_models(["meta/llama-3.3-70b", "nvidia/nvclip", "meta/llama-guard-4-12b",
                                  "nvidia/nemotron-4-340b-reward", "nvidia/embed-qa-4"]) == ["meta/llama-3.3-70b"]
    rows = [{"id": "a/chat", "supported_parameters": ["tools"], "architecture": {"output_modalities": ["text"]}},
            {"id": "a/chat:batch", "supported_parameters": ["tools"]},
            {"id": "a/img", "supported_parameters": ["tools"], "architecture": {"output_modalities": ["image", "text"]}},
            {"id": "a/notools", "supported_parameters": []}]
    assert _filter_openrouter_models(rows) == ["a/chat"]


@pytest.mark.asyncio
async def test_models_past_their_shutdown_date_are_not_listed():
    past = (utcnow() - timedelta(days=2)).date().isoformat()
    future = (utcnow() + timedelta(days=200)).date().isoformat()
    body = {"data": [{"id": "gpt-old", "created": 1, "shutdown_date": past},
                     {"id": "gpt-new", "created": 2, "shutdown_date": future},
                     {"id": "gpt-keep", "created": 3, "shutdown_date": None}]}
    provider = _openai()
    models = await provider.list_models(_Session(_Resp(json_data=body)))
    assert sorted(models) == ["gpt-keep", "gpt-new"]
    assert provider.model_created["gpt-keep"] == 3
