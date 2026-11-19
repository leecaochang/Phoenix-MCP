"""A System One server the operator runs, such as Ollama or llama.cpp, as a decision account.

The server sits on the operator's network, so setup admits a private address, a key is optional, and
one decision question proves the chosen model decides. Ollama names no choice capacity, so setup
learns it. Whatever model the operator picks may act on single sequential commands; every other
execution registry stays keyed to fixed hosted endpoints.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.phoenix_mcp import agent_chat_views
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.llm import accounts as llm_accounts
from custom_components.phoenix_mcp.llm.base import ProbeConfigError
from custom_components.phoenix_mcp.routing.decision_provider import (
    DecisionProvider, registry_covers, revision_admitted, sequential_revision_admitted,
)
from custom_components.phoenix_mcp.routing.decision_types import DecisionError
from custom_components.phoenix_mcp.routing.routing_tables import (
    DECISION_BATCH_EXECUTION_MODELS, DECISION_CONTEXT_EXECUTION_MODELS, DECISION_FOLLOWUP_EXECUTION_MODELS,
    DECISION_LOCAL_PROBE_CHOICES, DECISION_MAX_TARGETS, DECISION_PERCENTAGE_EXECUTION_MODELS,
    DECISION_REFERENCE_EXECUTION_MODELS, DECISION_SEQUENTIAL_EXECUTION_MODELS,
)
from tests.runtime_fixtures import patch_readers

# Every request goes over a real socket to the stub server.
pytestmark = pytest.mark.usefixtures("socket_enabled")

_SECRET_STORE = "custom_components.phoenix_mcp.llm.accounts._get_secret_store"
_SESSION = "custom_components.phoenix_mcp.agent_chat_views.async_get_clientsession"


class _Server:
    """A System One server: Ollama's lists and capabilities, and a decision endpoint with a choice cap."""

    def __init__(self, *, models=("clef-flash:latest", "qwen3.8:27b"), show: dict | bool = True, cap: int = 255,
                 decide: bool = True, rows: dict | None = None) -> None:
        self.models = list(models)
        # Model name to Ollama capabilities; False answers /api/show with 404, as a non-Ollama server.
        self.show = show if show is not True else {"clef-flash:latest": ["decision", "vision"],
                                                   "qwen3.8:27b": ["completion", "tools"]}
        self.cap = cap
        self.decide = decide
        self.rows = rows or {}
        self.decisions: list[dict] = []
        self.headers: list[dict] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/v1/models", self._models)
        app.router.add_post("/v1/systemone", self._decide)
        app.router.add_post("/api/show", self._show)
        return app

    async def _models(self, request: web.Request) -> web.Response:
        self.headers.append(dict(request.headers))
        return web.json_response({"object": "list", "data": [
            {"id": model, "object": "model", "owned_by": "library", **self.rows.get(model, {})}
            for model in self.models]})

    async def _show(self, request: web.Request) -> web.Response:
        if self.show is False:
            return web.Response(status=404)
        model = (await request.json())["model"]
        return web.json_response({"capabilities": self.show.get(model, ["completion"])})

    async def _decide(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.decisions.append(body)
        self.headers.append(dict(request.headers))
        if not self.decide:
            return web.json_response({"error": "model does not support decisions"}, status=400)
        for name, question in body["questions"].items():
            if len(question["criteria"]) > self.cap:
                return web.json_response({"error": f"questions.{name}: too many options "
                                                   f"({len(question['criteria'])}), this model supports at most "
                                                   f"{self.cap}"}, status=400)
        answers = {}
        for name, question in body["questions"].items():
            keys = list(question["criteria"])
            choice = "none" if "none" in keys else keys[0]
            rest = (1 - 0.97) / max(len(keys) - 1, 1)
            answers[name] = {"type": "choice", "choice": choice, "confidence": 0.97,
                             "probabilities": {key: 0.97 if key == choice else rest for key in keys}}
        return web.json_response({"model": body["model"], "answers": answers,
                                  "usage": {"input_tokens": 10, "output_tokens": 0}})


def _admin_request(body: dict):
    """An authenticated admin request whose body is ``body``."""
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

    user = MagicMock(is_admin=True, id="admin")
    context = {"phoenix_mcp_rid": "rid", KEY_AUTHENTICATED: True, KEY_HASS_USER: user}
    request = MagicMock()
    request.get = MagicMock(side_effect=lambda key, default=None: context.get(key, default))
    request.__getitem__ = MagicMock(side_effect=lambda key: context[key])
    raw = json.dumps(body).encode()
    request.content.read = AsyncMock(side_effect=[raw, b""])
    request.headers = {}
    request.content_length = len(raw)
    return request


def _cfg(server: TestServer, model: str = "clef-flash:latest", **extra) -> llm_accounts.ProviderConfig:
    return llm_accounts.ProviderConfig(kind="local_decision", model=model, base_url=str(server.make_url("/v1")),
                                       **extra)


async def _create(hass, server: TestServer, session: ClientSession, store, body: dict | None = None):
    view = agent_chat_views.PhoenixAgentCliProvidersView()
    view.hass = hass
    request = {"kind": "local_decision", "base_url": str(server.make_url("")).rstrip("/"),
               "model": "clef-flash:latest", **(body or {})}
    with patch_readers(_SECRET_STORE, AsyncMock(return_value=store)), patch(_SESSION, return_value=session):
        return await view.post(_admin_request(request))


# --------------------------------------------------------------------------- #
# The account and its setup
# --------------------------------------------------------------------------- #

def test_the_kind_is_a_decision_account_with_an_address_and_an_optional_key():
    definition = llm_accounts.PROVIDER_DEFINITIONS["local_decision"]
    assert definition.label == "Local / System One"
    assert definition.purpose == "decision" and definition.adapter == "systemone"
    fields = {field.id: field for field in definition.fields}
    assert set(fields) == {"base_url", "api_key"}
    assert fields["api_key"].required is False
    # The decision kinds that take an address from the operator; Azure's must be a Foundry resource host.
    assert [kind for kind, item in llm_accounts.PROVIDER_DEFINITIONS.items()
            if item.purpose == "decision" and any(field.id == "base_url" for field in item.fields)] == [
                "local_decision", "azure_decision"]


@pytest.mark.parametrize("entered,stored", [
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434/v1"),
    ("http://192.168.1.20:11434/", "http://192.168.1.20:11434/v1"),
    ("http://10.0.0.5:8080/v1/", "http://10.0.0.5:8080/v1"),
])
async def test_setup_takes_a_private_address_and_adds_v1_only_to_a_bare_one(entered, stored):
    cfg = await agent_chat_views._probe_config("local_decision", {"base_url": entered, "model": "clef"})
    assert isinstance(cfg, llm_accounts.ProviderConfig)
    assert cfg.base_url == stored and cfg.api_key == ""


@pytest.mark.parametrize("entered,key", [
    ("", "providerServerUrlRequired"),
    ("http://169.254.169.254/latest", "providerUrlAddressForbidden"),
    ("http://127.0.0.1:11434/v1?x=1", "providerUrlInvalid"),
])
async def test_setup_refuses_a_missing_metadata_or_query_address(entered, key):
    cfg = await agent_chat_views._probe_config("local_decision", {"base_url": entered, "model": "clef"})
    assert isinstance(cfg, ProbeConfigError)
    assert cfg.key == key


async def test_a_hosted_decision_kind_still_takes_no_address():
    cfg = await agent_chat_views._probe_config("typesafe", {"api_key": "k", "base_url": "http://192.168.1.20/v1",
                                                            "model": "jev-latest"})
    assert isinstance(cfg, llm_accounts.ProviderConfig)
    assert "192.168" not in cfg.base_url


async def test_ollama_offers_only_its_decision_models():
    stub = _Server()
    async with TestServer(stub.app()) as server, ClientSession() as session:
        assert await DecisionProvider(_cfg(server), "systemone").list_models(session) == ["clef-flash:latest"]


async def test_a_server_without_ollamas_capabilities_keeps_its_whole_list():
    stub = _Server(models=["clef-flash", "tev1"], show=False)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        assert await DecisionProvider(_cfg(server, "tev1"), "systemone").list_models(session) == ["clef-flash", "tev1"]


async def test_setup_stores_the_account_and_the_choice_limit_it_found(hass):
    stub = _Server(cap=255)
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        response = await _create(hass, server, session, store)
        assert response.status == 201, response.body
        instance = json.loads(response.body)["instance"]
        cfg = store.resolve(instance["id"], purpose="decision")
    assert cfg.kind == "local_decision" and cfg.model == "clef-flash:latest"
    assert cfg.decision_choice_limit == DECISION_LOCAL_PROBE_CHOICES
    # One capacity question proved the model decides, and its answer was reused for the stored limit.
    assert [len(item["questions"]["check"]["criteria"]) for item in stub.decisions] == [DECISION_LOCAL_PROBE_CHOICES]
    assert instance["name"] == "Local"


async def test_a_server_that_refuses_the_large_format_is_set_to_the_fallback(hass):
    stub = _Server(cap=DECISION_MAX_TARGETS + 1)
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        response = await _create(hass, server, session, store)
        assert response.status == 201
        instance_id = json.loads(response.body)["instance"]["id"]
    assert store.resolve(instance_id, purpose="decision").decision_choice_limit == DECISION_MAX_TARGETS + 1
    assert [len(item["questions"]["check"]["criteria"]) for item in stub.decisions] == [
        DECISION_LOCAL_PROBE_CHOICES, DECISION_MAX_TARGETS + 1]


async def test_a_model_that_does_not_decide_is_not_saved(hass):
    stub = _Server(decide=False)
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        response = await _create(hass, server, session, store)
    assert response.status == 400
    assert store.list_instances() == []


async def test_a_chat_model_on_the_same_server_is_not_offered(hass):
    stub = _Server()
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        response = await _create(hass, server, session, store, {"model": "qwen3.8:27b"})
    assert response.status == 400
    assert json.loads(response.body)["message_key"] == "adminError.providerModelNotListed"
    assert stub.decisions == [] and store.list_instances() == []


async def test_the_optional_key_is_sent_and_never_listed(hass):
    stub = _Server()
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        response = await _create(hass, server, session, store, {"api_key": "local-secret-key"})
        assert response.status == 201
    assert all(item.get("Authorization") == "Bearer local-secret-key" for item in stub.headers)
    assert "local-secret-key" not in json.dumps(store.list_instances())


async def test_changing_the_model_learns_its_limit(hass):
    stub = _Server(models=["clef-flash:latest", "clef:latest"],
                   show={"clef-flash:latest": ["decision"], "clef:latest": ["decision"]})
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        instance_id = json.loads((await _create(hass, server, session, store)).body)["instance"]["id"]
        stub.cap = DECISION_MAX_TARGETS + 1
        hass.data[DOMAIN] = MagicMock(settings_update_lock=asyncio.Lock())
        view = agent_chat_views.PhoenixAgentCliProviderView()
        view.hass = hass
        with patch_readers(_SECRET_STORE, AsyncMock(return_value=store)), patch(_SESSION, return_value=session):
            response = await view.patch(_admin_request({"model": "clef:latest"}), instance_id=instance_id)
        assert response.status == 200, response.body
    cfg = store.resolve(instance_id, purpose="decision")
    assert cfg.model == "clef:latest" and cfg.decision_choice_limit == DECISION_MAX_TARGETS + 1


async def test_two_local_accounts_are_told_apart_by_address():
    store = llm_accounts.AgentCliStore(AsyncMock())
    await store.add("local_decision", {"base_url": "http://192.168.1.103:11434/v1", "model": "clef"})
    await store.add("local_decision", {"base_url": "http://192.168.1.104:8080/v1", "model": "clef"})
    assert sorted(item["name"] for item in store.list_instances()) == [
        "Local (192.168.1.103:11434)", "Local (192.168.1.104:8080)"]


async def test_one_server_holds_one_account_per_model():
    store = llm_accounts.AgentCliStore(AsyncMock())
    await store.add("local_decision", {"base_url": "http://192.168.1.103:11434/v1", "model": "clef"})
    # Another model on the same server is another account; the same model again is not.
    assert not store.has_duplicate("local_decision", {"base_url": "http://192.168.1.103:11434/v1/", "model": "d3:9b"})
    assert store.has_duplicate("local_decision", {"base_url": "http://192.168.1.103:11434/v1/", "model": "clef"})
    assert store.has_duplicate("local_decision", {"base_url": "http://192.168.1.103:11434/v1", "model": "clef:latest"})
    assert not store.has_duplicate("local_decision", {"base_url": "http://192.168.1.104:11434/v1", "model": "clef"})


async def test_two_accounts_on_one_server_are_told_apart_by_id():
    store = llm_accounts.AgentCliStore(AsyncMock())
    first = await store.add("local_decision", {"base_url": "http://192.168.1.103:11434/v1", "model": "clef"})
    second = await store.add("local_decision", {"base_url": "http://192.168.1.103:11434/v1", "model": "d3:9b"})
    other = await store.add("local_decision", {"base_url": "http://192.168.1.104:8080/v1", "model": "clef"})
    names = {item["id"]: item["name"] for item in store.list_instances()}
    assert names == {first: f"Local ({first[:8]})", second: f"Local ({second[:8]})", other: "Local (192.168.1.104:8080)"}


async def test_adding_a_second_model_on_one_server_succeeds_and_the_same_model_is_refused(hass):
    stub = _Server(models=["clef-flash:latest", "clef:latest"],
                   show={"clef-flash:latest": ["decision"], "clef:latest": ["decision"]})
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        assert (await _create(hass, server, session, store)).status == 201
        assert (await _create(hass, server, session, store, {"model": "clef:latest"})).status == 201
        again = await _create(hass, server, session, store, {"model": "clef:latest"})
    assert again.status == 409 and json.loads(again.body)["message_key"] == "adminError.providerAlreadyConfigured"
    assert len(store.list_instances()) == 2


async def test_a_model_change_never_makes_two_accounts_of_one_model(hass):
    stub = _Server(models=["clef-flash:latest", "clef:latest"],
                   show={"clef-flash:latest": ["decision"], "clef:latest": ["decision"]})
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        first = json.loads((await _create(hass, server, session, store)).body)["instance"]["id"]
        await _create(hass, server, session, store, {"model": "clef:latest"})
        hass.data[DOMAIN] = MagicMock(settings_update_lock=asyncio.Lock())
        view = agent_chat_views.PhoenixAgentCliProviderView()
        view.hass = hass
        with patch_readers(_SECRET_STORE, AsyncMock(return_value=store)), patch(_SESSION, return_value=session):
            response = await view.patch(_admin_request({"model": "clef:latest"}), instance_id=first)
    assert response.status == 409 and json.loads(response.body)["error"] == "already_exists"
    assert store.resolve(first, purpose="decision").model == "clef-flash:latest"


# --------------------------------------------------------------------------- #
# The choice limit in use
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stored,expected", [
    (255, 255), (50, 50), (None, DECISION_MAX_TARGETS + 1), (256, DECISION_MAX_TARGETS + 1),
    (1, DECISION_MAX_TARGETS + 1), (True, DECISION_MAX_TARGETS + 1), ("255", DECISION_MAX_TARGETS + 1),
])
async def test_the_learned_limit_sizes_target_questions(stored, expected):
    stub = _Server()
    store = llm_accounts.AgentCliStore(AsyncMock())
    async with TestServer(stub.app()) as server, ClientSession() as session:
        instance_id = await store.add("local_decision", {"base_url": str(server.make_url("/v1")),
                                                         "model": "clef-flash:latest"})
        if stored is not None:
            store._data[instance_id]["capabilities"] = {"clef-flash:latest": {"choice_limit": stored}}
        cfg = store.resolve(instance_id, purpose="decision")
        assert await DecisionProvider(cfg, "systemone").target_choice_limit(session, 5000) == expected


async def test_a_capacity_the_server_names_wins_over_the_learned_one():
    stub = _Server(rows={"clef-flash:latest": {"x_max_choice_options": 100}})
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = DecisionProvider(_cfg(server, decision_choice_limit=255), "systemone")
        assert await provider.target_choice_limit(session, 5000) == 100


async def test_the_limit_survives_a_capability_refresh_that_does_not_mention_it():
    store = llm_accounts.AgentCliStore(AsyncMock())
    instance_id = await store.add("local_decision", {"base_url": "http://192.168.1.103:11434/v1",
                                                     "model": "clef-flash:latest"})
    await store.set_capabilities(instance_id, {"clef-flash:latest": {"choice_limit": 255}}, "2026-11-15T00:00:00+00:00")
    await store.set_capabilities(instance_id, {}, "2026-11-15T01:00:00+00:00")
    assert store.resolve(instance_id, purpose="decision").decision_choice_limit == 255


async def test_a_probe_answer_must_be_a_decision():
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"model": "clef-flash:latest", "answers": {}})
    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    async with TestServer(app) as server, ClientSession() as session:
        with pytest.raises(DecisionError, match="malformed"):
            await DecisionProvider(_cfg(server), "systemone").probe_choice_limit(session)


# --------------------------------------------------------------------------- #
# What it may do
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("model", ["clef-flash:latest", "clef:27b-q8_0", "laya:latest", "anything"])
def test_any_model_may_act_on_a_single_sequential_command(model):
    cfg = llm_accounts.ProviderConfig("local_decision", model, "http://192.168.1.103:11434/v1")
    assert sequential_revision_admitted(cfg, model, model)
    # Both answers must still come from one revision.
    assert not sequential_revision_admitted(cfg, model, model + "-other")
    assert not sequential_revision_admitted(cfg, None, None)


_NOT_FOLLOW_UPS = [DECISION_SEQUENTIAL_EXECUTION_MODELS, DECISION_BATCH_EXECUTION_MODELS, DECISION_PERCENTAGE_EXECUTION_MODELS]
_FOLLOW_UPS = [DECISION_CONTEXT_EXECUTION_MODELS, DECISION_REFERENCE_EXECUTION_MODELS, DECISION_FOLLOWUP_EXECUTION_MODELS]


@pytest.mark.parametrize("registry", _NOT_FOLLOW_UPS + _FOLLOW_UPS)
@pytest.mark.parametrize("model", ["clef-flash", "clef-flash:latest", "laya:latest", "jev-1.13.0", "jev-latest", "clefx:latest"])
def test_no_registry_admits_a_local_server_outside_the_qualified_family(registry, model):
    """Joint and percentage decisions stay preview-only, and follow-ups too unless the model is clef."""
    cfg = llm_accounts.ProviderConfig("local_decision", model, "http://192.168.1.103:11434/v1")
    assert not revision_admitted(registry, cfg, model)
    assert not registry_covers(registry, cfg)
    assert all(kind != "local_decision" for kind, _, _ in registry)


@pytest.mark.parametrize("model", ["clef:latest", "clef:27b-q8_0", "clef", "library/clef:27b"])
def test_local_clef_handles_follow_ups_answering_as_itself(model):
    cfg = llm_accounts.ProviderConfig("local_decision", model, "http://192.168.1.103:11434/v1")
    for registry in _FOLLOW_UPS:
        assert registry_covers(registry, cfg)
        assert revision_admitted(registry, cfg, model)
        assert not revision_admitted(registry, cfg, "clef-flash:latest")
        assert not revision_admitted(registry, cfg, None)
    for registry in _NOT_FOLLOW_UPS:
        assert not registry_covers(registry, cfg)
        assert not revision_admitted(registry, cfg, model)


@pytest.mark.parametrize("kind", ["typesafe", "cloudflare_decision", "opencode_decision"])
def test_the_family_rule_is_for_local_servers_only(kind):
    cfg = llm_accounts.ProviderConfig(kind, "clef:latest", "https://self-hosted.example/v1")
    for registry in _FOLLOW_UPS:
        assert not registry_covers(registry, cfg)
        assert not revision_admitted(registry, cfg, "clef:latest")


def test_a_local_server_gets_no_exact_percentages_or_rounding():
    provider = DecisionProvider(
        llm_accounts.ProviderConfig("local_decision", "clef", "http://192.168.1.103:11434/v1"), "systemone")
    assert not provider.supports_exact_percentages()
    assert provider.rounding_error == 0


@pytest.mark.parametrize("body,expected", [
    # Ollama, when the prompt outgrows the context the model runs with (num_ctx).
    ({"error": "the prompt is longer than the context length currently available to the model; shorten the "
               "prompt, adjust the context length in settings, or use a model with a longer context length"},
     "provider_context_limit"),
    # llama.cpp's server.
    ({"error": {"code": 400, "message": "the request exceeds the available context size", "type": "exceed_context_size_error"}},
     "provider_context_limit"),
    ({"error": "model \'d3:9b\' not found"}, "http_error"),
    ({"detail": {"error_type": "max_tokens_exceeded"}}, "http_error"),
])
async def test_a_local_context_refusal_is_named_as_one(body, expected):
    async def handler(request):
        return web.json_response(body, status=400)
    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("local_decision", "d3:9b", str(server.make_url("/v1")))
        with pytest.raises(DecisionError, match="^" + expected + "$"):
            await DecisionProvider(cfg, "systemone").classify(session, "command", 1000)
