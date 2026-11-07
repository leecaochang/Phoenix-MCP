"""Infron account behavior: key check, model catalog, refusals, and free-model retirement."""

import json

import pytest

from custom_components.phoenix_mcp.llm import accounts as llm_accounts
from custom_components.phoenix_mcp.llm import factory as llm_factory
from custom_components.phoenix_mcp.llm import probes as llm_probes
from custom_components.phoenix_mcp.llm.accounts import ProviderConfig
from custom_components.phoenix_mcp.llm.base import _http_failure, _infron_capabilities
from custom_components.phoenix_mcp.llm.provider_openai import OpenAICompatProvider, _filter_infron_models
from custom_components.phoenix_mcp.model_selection import best_model, effective_model, successor
from custom_components.phoenix_mcp.provider_constants import (
    AGENT_CHAT_INFRON_BALANCE_URL, AGENT_CHAT_INFRON_BASE_URL, AGENT_CHAT_MAX_TOOLS_PER_REQUEST,
)


class _Content:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, _size):
        yield self.body


class _Response:
    def __init__(self, status: int, payload: object = None, text: str = "") -> None:
        self.status = status
        self.payload = payload
        self.body = text
        self.headers: dict = {}
        self.content = _Content((text or json.dumps(payload)).encode())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self, *_, **__):
        return self.payload

    async def text(self, *_, **__):
        return self.body or json.dumps(self.payload)

    async def read(self):
        return (await self.text()).encode()


class _Session:
    """Answers by URL, the way the two Infron hosts differ: the list ignores the key."""

    def __init__(self, answers: dict[str, _Response]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.answers[url]

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.answers[url]


def _provider(model: str = "a/m") -> OpenAICompatProvider:
    return OpenAICompatProvider(ProviderConfig(
        kind="infron", model=model, base_url=AGENT_CHAT_INFRON_BASE_URL, api_key="test-secret"))


def _row(model_id: str, **overrides) -> dict:
    return {
        "id": model_id, "category_type": "LLM", "supports_function_calling": True, "supports_vision": False,
        "output_modalities": ["text"], "supported_endpoint_types": ["openai"], "deprecated": False,
        "is_display_only": False, **overrides,
    }


def test_infron_registration_and_route():
    definition = llm_accounts.PROVIDER_DEFINITIONS["infron"]
    assert definition.base_url == AGENT_CHAT_INFRON_BASE_URL
    assert definition.max_tools == AGENT_CHAT_MAX_TOOLS_PER_REQUEST
    assert definition.include_usage and definition.reasoning == "probed_effort"
    assert isinstance(llm_factory.build_provider(ProviderConfig(
        kind="infron", model="m", base_url=definition.base_url, api_key="key")), OpenAICompatProvider)
    assert _provider()._chat_url() == "https://llm.onerouter.pro/v1/chat/completions"
    assert _provider()._models_url() == "https://llm.onerouter.pro/v1/models"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "valid"), [(200, True), (401, False)])
async def test_key_check_uses_the_balance_because_the_model_list_takes_any_key(status, valid):
    session = _Session({
        AGENT_CHAT_INFRON_BALANCE_URL: _Response(status, {"credit_balance": 0}),
        f"{AGENT_CHAT_INFRON_BASE_URL}/models": _Response(200, {"data": []}),
    })
    ok, _reason = await _provider().validate(session)
    assert ok is valid
    assert [url for url, _ in session.calls] == [AGENT_CHAT_INFRON_BALANCE_URL]
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer test-secret"
    assert session.calls[0][1]["allow_redirects"] is False


def test_catalog_keeps_tool_capable_chat_models_only():
    data = [
        _row("z/plain"),
        _row("a/free:free"),
        _row("b/no-tools", supports_function_calling=False),
        _row("c/embedding", category_type="Embeddings"),
        _row("d/responses-only", supported_endpoint_types=["openai-response"]),
        _row("e/anthropic-too", supported_endpoint_types=["anthropic", "openai"]),
        _row("f/display", is_display_only=True),
        _row("g/deprecated", deprecated=True),
        _row("h/image-output", output_modalities=["text", "image"]),
        _row("i/no-endpoints", supported_endpoint_types=None),
        "not a row",
    ]
    assert _filter_infron_models(data) == ["a/free:free", "e/anthropic-too", "z/plain"]


def test_catalog_schema_drift_falls_back_to_every_id():
    assert _filter_infron_models([{"id": "b/x"}, {"id": "a/y"}]) == ["a/y", "b/x"]


def test_capabilities_report_only_what_the_catalog_declares():
    assert _infron_capabilities([
        _row("a/m", supports_vision=True), {"id": "b/m", "supports_function_calling": False}, {"id": "c/m"}, "junk",
    ]) == {"a/m": {"tools": True, "vision": True}, "b/m": {"tools": False}}


def test_effort_uses_the_nested_object():
    assert llm_probes._effort_probe_body("infron", "high") == {"reasoning": {"effort": "high"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "message", "code", "retryable"), [
    (402, "Your account or API key has insufficient credits.", "quota", False),
    (429, "Free model requires Team balance greater than $4.499999. (request id: 1)", "quota", False),
    (403, "Your current credits have been used up and we are unable to process further requests.", "quota", False),
    (429, "Free model daily limit exceeded (1000 requests per day)", "rate_limit", False),
    (429, "Request rate increased too quickly", "rate_limit", True),
    (401, "The token status is not available (request id: 1)", "auth", False),
])
async def test_refusals_are_read_from_the_codeless_envelope(status, message, code, retryable):
    body = json.dumps({"error": {"message": message, "type": "infron_ai_error"}})
    session = _Session({f"{AGENT_CHAT_INFRON_BASE_URL}/chat/completions": _Response(status, text=body)})
    events = [event async for event in _provider().stream_turn(
        session, system_prompt="s", messages=[], tools=[], options={})]
    assert (events[0]["code"], events[0]["retryable"]) == (code, retryable)


@pytest.mark.asyncio
async def test_a_billing_code_is_a_refusal_without_its_sentence():
    body = json.dumps({"error": {"message": "Refused.", "type": "infron_ai_error", "code": "insufficient_user_quota"}})
    assert _http_failure("infron", 403, body, code="auth", retryable=False)["code"] == "quota"


def test_a_phrase_binds_only_its_own_provider():
    body = json.dumps({"error": {"message": "Daily limit exceeded"}})
    assert _http_failure("infron", 429, body, code="rate_limit", retryable=True)["retryable"] is False
    assert _http_failure("groq", 429, body, code="rate_limit", retryable=True)["retryable"] is True


CATALOG = ["anthropic/claude-opus-5.5", "openai/gpt-6-luna", "qwen/qwen3.8-27b:free", "apodex/apodex-1.1-mini:free"]


def test_automatic_mode_never_prefers_a_free_variant():
    assert best_model("infron", CATALOG) == "anthropic/claude-opus-5.5"
    assert best_model("infron", ["anthropic/claude-opus-9:free", "openai/gpt-6-luna"]) == "openai/gpt-6-luna"


def test_a_retired_free_model_moves_only_to_a_free_one():
    assert successor("infron", "gone/model:free", CATALOG).endswith(":free")
    assert effective_model("infron", "gone/model:free", CATALOG, ["gone/model:free"])[1] == "gone/model:free"


def test_a_retired_free_model_with_no_free_replacement_is_kept_not_charged():
    paid = ["anthropic/claude-opus-5.5", "openai/gpt-6-luna"]
    assert successor("infron", "gone/model:free", paid) == ""
    assert effective_model("infron", "gone/model:free", paid, ["gone/model:free"]) == ("gone/model:free", "")


def test_a_retired_paid_model_may_still_move_to_the_best_overall():
    assert successor("infron", "gone/model", CATALOG) == "anthropic/claude-opus-5.5"


def _reply(reasoning: str = "", tokens: int = 0) -> _Response:
    return _Response(200, {
        "choices": [{"message": {"content": "391", **({"reasoning": reasoning} if reasoning else {})}}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": tokens}},
    })


class _Probe:
    """Answers each reasoning question from a table of effort to reply."""

    def __init__(self, replies: dict[str, _Response]) -> None:
        self.replies = replies
        self.bodies: list[dict] = []

    def post(self, _url, **kwargs):
        body = kwargs["json"]
        self.bodies.append(body)
        if body["max_tokens"] == 1:
            return _Response(200, {"choices": []})
        return self.replies[body["reasoning"]["effort"]]


LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("low", "none", "levels"), [
    (_reply("thinking"), _reply(), LEVELS),
    (_reply(tokens=40), _reply(), LEVELS),
    (_reply("thinking"), _reply("still thinking"), LEVELS[1:]),
    (_reply(tokens=40), _reply(tokens=12), LEVELS[1:]),
    (_reply("thinking"), _Response(400, text="{}"), LEVELS[1:]),
    (_reply(), _reply(), None),
    (_Response(400, text="{}"), _reply(), None),
])
async def test_effort_levels_follow_what_the_model_visibly_does(low, none, levels):
    session = _Probe({"low": low, "none": none})
    caps, calls, answered = await llm_probes.async_probe_capabilities(session, _provider("a/m").cfg)
    assert answered is True and caps.get("effort_levels") == levels
    assert calls == len(session.bodies) <= 6


@pytest.mark.asyncio
async def test_a_refused_invalid_effort_proves_nothing_here():
    class Refusing(_Probe):
        def post(self, url, **kwargs):
            refused = kwargs["json"].get("reasoning", {}).get("effort") == "phoenix-probe-invalid"
            return _Response(400, text="{}") if refused else super().post(url, **kwargs)

    session = Refusing({"low": _reply()})
    caps, _calls, _answered = await llm_probes.async_probe_capabilities(session, _provider("a/m").cfg)
    assert "effort_levels" not in caps
    assert not any(body.get("reasoning", {}).get("effort") == "phoenix-probe-invalid" for body in session.bodies)


def test_a_catalogue_answer_keeps_probed_facts_it_does_not_mention():
    previous = {"a": {"effort_levels": ["low"], "temperature": False, "tools": True}, "b": {"effort_levels": ["low"]}}
    declared = {"a": {"tools": False, "vision": True}}
    assert llm_accounts._with_probed(declared, previous) == {
        "a": {"tools": False, "vision": True, "effort_levels": ["low"], "temperature": False},
        "b": {"effort_levels": ["low"]},
    }
    assert declared == {"a": {"tools": False, "vision": True}}


def test_a_catalogue_answer_wins_over_a_probed_fact_it_states():
    previous = {"a": {"effort_levels": ["low"], "temperature": False}}
    declared = {"a": {"effort_levels": ["high"], "temperature": True}}
    assert llm_accounts._with_probed(declared, previous) == declared


def test_an_empty_catalogue_answer_keeps_probed_facts():
    assert llm_accounts._with_probed({}, {"a": {"effort_levels": ["low"], "tools": True}}) == {"a": {"effort_levels": ["low"]}}
    assert llm_accounts._with_probed({"a": {}}, None) == {"a": {}}
