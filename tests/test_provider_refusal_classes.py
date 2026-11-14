"""How provider refusals are classified, and the catalogue and request details around them.

Each case is a documented provider answer that used to read as the wrong thing: a context
overflow as a refusal, a credit problem or a bad image as a content block, a server timeout
retried against the provider's instruction, a Gemini project refusal as a rejected key.
"""

from __future__ import annotations

import json

import pytest

from custom_components.phoenix_mcp.llm import base as llm_base
from custom_components.phoenix_mcp.llm import provider_openai
from custom_components.phoenix_mcp.llm.accounts import ProviderConfig
from custom_components.phoenix_mcp.model_selection import best_model, rank_models


class _Content:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def iter_chunked(self, _n: int):
        for i in range(0, len(self._data), 8):
            yield self._data[i:i + 8]


class _FakeResp:
    def __init__(self, status: int = 200, body: bytes = b"", text_data: str = "") -> None:
        self.status = status
        self.content = _Content(body or text_data.encode())
        self._text = text_data
        self.headers: dict = {}

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakeSession:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, headers=None, json=None, timeout=None, allow_redirects=None):
        self.calls.append(("POST", {"url": url, "json": json}))
        return self._resp

    def get(self, url, headers=None, timeout=None, allow_redirects=None, params=None):
        self.calls.append(("GET", {"url": url}))
        return self._resp


def _sse(*frames: str) -> bytes:
    return ("\n\n".join(frames) + "\n\n").encode()


async def _collect(agen):
    return [ev async for ev in agen]


def _failure(kind: str, status: int, error: dict) -> dict:
    return llm_base._http_failure(kind, status, json.dumps({"error": error}), code=llm_base._http_failure_code(status),
                                  retryable=status in llm_base._RETRYABLE_STATUSES)


@pytest.mark.parametrize(("kind", "error"), [
    ("chatgpt", {"code": "context_length_exceeded", "message": "too long"}),
    ("openrouter", {"code": 400, "message": "x", "metadata": {"error_type": "context_length_exceeded"}}),
    ("zai", {"code": "1261", "message": "Prompt too long"}),
    ("tencent", {"code": "400003", "message": "input exceeds the context"}),
])
def test_a_documented_context_overflow_says_so(kind, error):
    event = _failure(kind, 400, error)
    assert (event["code"], event["retryable"]) == ("provider_context_limit", False)


def test_an_ordinary_400_stays_a_refusal():
    assert _failure("chatgpt", 400, {"code": "invalid_value", "message": "x"})["code"] == "provider_rejected"


def test_meta_504_is_not_retried_but_others_are():
    # Meta: the same request usually times out again.
    assert _failure("meta", 504, {"message": "x"})["retryable"] is False
    assert _failure("groq", 504, {"message": "x"})["retryable"] is True


def test_a_gemini_403_names_the_project_not_the_key():
    event = _failure("gemini", 403, {"code": 403, "message": "PERMISSION_DENIED", "status": "PERMISSION_DENIED"})
    assert (event["code"], event["retryable"]) == ("gemini_project_refused", False)
    assert _failure("chatgpt", 403, {"message": "x"})["code"] == "auth"


def test_a_groq_413_over_the_minute_limit_names_the_plan():
    # Groq's answer when one request is larger than the account's tokens-per-minute limit.
    error = {"message": "Request too large for model `openai/gpt-oss-120b` on tokens per minute (TPM): "
                        "Limit 8000, Requested 15367", "type": "tokens", "code": "rate_limit_exceeded"}
    event = _failure("groq", 413, error)
    assert (event["code"], event["retryable"]) == ("request_over_minute_limit", False)
    assert "Limit 8000" in event["provider_detail"]
    # The same code on a 429 is an ordinary rate limit, and another provider's 413 stays a refusal.
    assert _failure("groq", 429, error)["code"] == "rate_limit"
    assert _failure("chatgpt", 413, error)["code"] == "provider_rejected"


@pytest.mark.parametrize(("error", "expected"), [
    ({"code": 402, "message": "x", "metadata": {"error_type": "payment_required"}}, ("quota", False)),
    ({"code": "payment_required", "message": "x"}, ("quota", False)),
    ({"code": 400, "message": "x", "metadata": {"error_type": "token_limit_exceeded"}}, ("provider_context_limit", False)),
    ({"message": "x", "metadata": {"error_type": "unmapped"}}, ("provider_transient", True)),
    ({"code": "data_residency_mismatch", "message": "x"}, ("provider_rejected", False)),
    ({"code": "invalid_image", "message": "x"}, ("provider_rejected", False)),
    ({"code": "content_filter", "message": "x"}, ("provider_blocked", False)),
])
def test_an_error_inside_a_stream_is_classified_by_its_code(error, expected):
    with pytest.raises(llm_base._ProviderResponseError) as raised:
        llm_base._provider_object(json.dumps({"error": error}))
    assert (raised.value.code, raised.value.retryable) == expected


@pytest.mark.asyncio
async def test_a_round_with_no_tools_leaves_the_field_out():
    body = _sse('data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}', "data: [DONE]")
    provider = provider_openai.OpenAICompatProvider(ProviderConfig(kind="deepseek", model="m", base_url="https://h", api_key="k"))
    session = _FakeSession(_FakeResp(200, body))
    await _collect(provider.stream_turn(session, system_prompt="s", messages=[], tools=[], options={}))
    assert "tools" not in session.calls[-1][1]["json"]
    tools = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object"}}}]
    session = _FakeSession(_FakeResp(200, body))
    await _collect(provider.stream_turn(session, system_prompt="s", messages=[], tools=tools, options={}))
    assert session.calls[-1][1]["json"]["tools"] == tools


def test_gemini_catalogue_drops_non_chat_models_and_automatic_mode_skips_moving_aliases():
    listed = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-decision-1",
              "gemini-nano-banana-2.1", "gemini-4-argon"]
    kept = provider_openai._filter_gemini_models(listed)
    assert "gemini-decision-1" not in kept and "gemini-nano-banana-2.1" not in kept
    # The alias gets no thinking control and moves without notice; a named Flash wins.
    assert best_model("gemini", kept) == "gemini-3.8-flash"
    # Elsewhere a provider alias still leads.
    assert best_model("mistral", ["mistral-small-2503", "mistral-small-latest"]) == "mistral-small-latest"


def test_openrouter_automatic_mode_never_prefers_a_free_variant():
    models = ["anthropic/claude-opus-5.5:free", "anthropic/claude-opus-5.5", "openai/gpt-6-luna"]
    assert rank_models("openrouter", models)[0] == "anthropic/claude-opus-5.5"


def test_a_mistral_model_declared_without_reasoning_gets_no_thinking_control():
    rows = [{"id": "m", "capabilities": {"completion_chat": True, "function_calling": True, "reasoning": False}},
            {"id": "r", "capabilities": {"completion_chat": True, "function_calling": True, "reasoning": True}}]
    caps = provider_openai._mistral_capabilities(rows, ["m", "r"])
    assert caps["m"]["thinking"] is False and "thinking" not in caps["r"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "key", "mismatch"), [
    ("token_plan_sgp", "sk-abc", True),
    ("payg", "tp-abc", True),
    ("token_plan", "tp-abc", False),
    ("payg", "sk-abc", False),
    # A key in no documented shape is just rejected.
    ("payg", "other", False),
])
async def test_a_mimo_key_on_the_other_plans_route_says_so(route, key, mismatch):
    provider = provider_openai.OpenAICompatProvider(ProviderConfig(
        kind="mimo", model="m", base_url="https://mimo", api_key=key, endpoint_id=route))
    ok, reason = await provider.validate(_FakeSession(_FakeResp(401, text_data="{}")))
    assert ok is False
    assert (getattr(reason, "key", None) == "providerMimoKeyRouteMismatch") is mismatch
