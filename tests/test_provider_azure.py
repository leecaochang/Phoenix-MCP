"""Microsoft Azure account behavior: resource address, key header, deployment list and effort probing."""

import json

import pytest

from custom_components.phoenix_mcp import agent_chat_views
from custom_components.phoenix_mcp.agent_chat_views import _azure_api_url, ProbeConfigError
from custom_components.phoenix_mcp.llm import accounts as llm_accounts
from custom_components.phoenix_mcp.llm import factory as llm_factory
from custom_components.phoenix_mcp.llm import probes as llm_probes
from custom_components.phoenix_mcp.llm.accounts import ProviderConfig
from custom_components.phoenix_mcp.llm.base import _REPLAY_REASONING_KINDS
from custom_components.phoenix_mcp.llm.provider_openai import OpenAICompatProvider, _azure_deployments
from custom_components.phoenix_mcp.provider_constants import (
    AGENT_CHAT_AZURE_DEPLOYMENTS_PATH, AGENT_CHAT_MAX_TOOLS_PER_REQUEST, AGENT_CHAT_PROVIDERS,
)

_HOST = "https://res.services.ai.azure.com"
_BASE = f"{_HOST}/openai/v1"
_DEPLOYMENTS = f"{_HOST}{AGENT_CHAT_AZURE_DEPLOYMENTS_PATH}"
_FUTURE = 4102444800
_PAST = 1000000000


class _Content:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, _size):
        yield self.body


class _Response:
    def __init__(self, status: int, payload: object = None) -> None:
        self.status = status
        self.payload = payload
        self.headers: dict = {}
        self.content = _Content(json.dumps(payload).encode())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self, *_, **__):
        return self.payload

    async def text(self, *_, **__):
        return json.dumps(self.payload)

    async def read(self):
        return (await self.text()).encode()


class _Session:
    """Answers by URL: the deployment list and the model catalog are separate requests."""

    def __init__(self, answers: dict[str, _Response]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.answers[url]


def _provider(model: str = "") -> OpenAICompatProvider:
    return OpenAICompatProvider(ProviderConfig(kind="azure", model=model, base_url=_BASE, api_key="test-secret"))


def _deployment(name: str, model: str, status: str = "succeeded", created: int = 1700000000) -> dict:
    return {"id": name, "model": model, "status": status, "created_at": created, "object": "deployment"}


def _model(model_id: str, chat: bool = True, retires: int = _FUTURE, created: int = 1790000000) -> dict:
    return {"id": model_id, "capabilities": {"chat_completion": chat, "inference": True},
            "deprecation": {"inference": retires}, "created_at": created, "object": "model"}


def test_azure_registration_and_route():
    definition = llm_accounts.PROVIDER_DEFINITIONS["azure"]
    assert "azure" in AGENT_CHAT_PROVIDERS and "azure" in _REPLAY_REASONING_KINDS
    assert definition.max_tools == AGENT_CHAT_MAX_TOOLS_PER_REQUEST
    assert definition.include_usage and definition.reasoning == "probed_effort"
    assert [field.id for field in definition.fields] == ["base_url", "api_key"]
    assert isinstance(llm_factory.build_provider(ProviderConfig(
        kind="azure", model="m", base_url=_BASE, api_key="key")), OpenAICompatProvider)
    assert _provider()._chat_url() == f"{_BASE}/chat/completions"
    assert _provider()._models_url() == _DEPLOYMENTS


def test_the_key_rides_the_api_key_header_never_a_bearer_token():
    headers = _provider()._headers()
    assert headers["api-key"] == "test-secret"
    assert "Authorization" not in headers


@pytest.mark.parametrize(("url", "expected"), [
    (f"{_HOST}/api/projects/home", _BASE),
    (f"{_HOST}/openai/v1/", _BASE),
    ("https://RES.openai.azure.com", "https://res.openai.azure.com/openai/v1"),
    ("https://res.cognitiveservices.azure.com:443/", "https://res.cognitiveservices.azure.com/openai/v1"),
    ("http://res.openai.azure.com", ""),
    ("https://res.openai.azure.com:8443", ""),
    ("https://user:pw@res.openai.azure.com", ""),
    ("https://res.openai.azure.com.example.com", ""),
    ("https://example.com/res.openai.azure.com", ""),
    ("https://openai.azure.com", ""),
    ("https://a.b.openai.azure.com", ""),
    ("https://-res.openai.azure.com", ""),
    ("not a url", ""),
])
def test_only_an_azure_resource_host_becomes_the_api_address(url, expected):
    assert _azure_api_url(url) == expected


@pytest.mark.asyncio
async def test_setup_keeps_the_resource_host_whatever_address_is_pasted(monkeypatch):
    async def _no_problem(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_chat_views, "_validate_base_url", _no_problem)
    cfg = await agent_chat_views._probe_config("azure", {"base_url": f"{_HOST}/api/projects/home", "api_key": "k"})
    assert isinstance(cfg, ProviderConfig) and cfg.base_url == _BASE
    refused = await agent_chat_views._probe_config("azure", {"base_url": "https://example.com/v1", "api_key": "k"})
    assert isinstance(refused, ProbeConfigError) and refused.key == "providerAzureEndpointInvalid"


def test_an_account_is_its_resource_and_key_whichever_azure_host_names_it():
    one = llm_accounts._provider_identity("azure", {"base_url": _BASE, "api_key": "k"})
    other = llm_accounts._provider_identity("azure", {"base_url": "https://two.openai.azure.com/openai/v1", "api_key": "k"})
    assert one != other
    assert one == llm_accounts._provider_identity("azure", {"base_url": f"{_BASE}/", "api_key": " k "})
    for alias in ("https://res.openai.azure.com/openai/v1", "https://res.cognitiveservices.azure.com/openai/v1"):
        assert llm_accounts._provider_identity("azure", {"base_url": alias, "api_key": "k"}) == one


def test_deployments_keep_chat_models_and_take_the_model_release_time():
    deployments = [
        _deployment("chat-a", "Model-A", created=1),
        _deployment("embedder", "Embed-X"),
        _deployment("old", "Model-Old"),
        _deployment("pending", "Model-A", status="creating"),
        _deployment("custom-name", "Unlisted-Model", created=1700000005),
        _deployment("my-tts", "Unlisted-tts"),
    ]
    catalog = [_model("Model-A", created=1790000000), _model("Embed-X", chat=False),
               _model("Model-Old", retires=_PAST)]
    names, created = _azure_deployments(deployments, catalog)
    assert names == ["chat-a", "custom-name"]
    assert created == {"chat-a": 1790000000.0, "custom-name": 1700000005.0}


def test_deployments_without_a_catalog_are_judged_by_name():
    names, _created = _azure_deployments([_deployment("gpt", "gpt-x"), _deployment("tts", "gpt-x-tts")], None)
    assert names == ["gpt"]


@pytest.mark.asyncio
async def test_the_model_list_names_deployments_not_the_azure_catalog():
    session = _Session({
        _DEPLOYMENTS: _Response(200, {"data": [_deployment("kimi", "Kimi-X"), _deployment("embed", "Embed-X")],
                                      "object": "list"}),
        f"{_BASE}/models": _Response(200, {"data": [_model("Kimi-X"), _model("Embed-X", chat=False),
                                                    _model("Never-Deployed")]}),
    })
    provider = _provider()
    assert await provider.list_models(session) == ["kimi"]
    assert set(provider.model_created) == {"kimi"}
    assert all(kwargs["headers"]["api-key"] == "test-secret" and kwargs["allow_redirects"] is False
               for _url, kwargs in session.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "valid"), [(200, True), (401, False)])
async def test_the_key_is_checked_against_the_deployment_list(status, valid):
    session = _Session({_DEPLOYMENTS: _Response(status, {"data": []})})
    ok, _reason = await _provider().validate(session)
    assert ok is valid
    assert [url for url, _ in session.calls] == [_DEPLOYMENTS]


def test_effort_is_probed_on_the_standard_field():
    assert llm_probes._effort_probe_body("azure", "high") == {"reasoning_effort": "high"}
