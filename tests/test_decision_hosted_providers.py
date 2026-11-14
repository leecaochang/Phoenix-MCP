"""Wire contracts and qualification boundaries of the hosted decision providers beside TypeSafe."""
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.phoenix_mcp import agent_chat_views
from custom_components.phoenix_mcp.llm import accounts as llm_accounts
from custom_components.phoenix_mcp.llm.base import ProbeConfigError
from custom_components.phoenix_mcp.routing.decision_provider import (
    DecisionProvider, decision_endpoint, decision_key, gemini_request, revision_admitted,
    sequential_revision_admitted,
)
from custom_components.phoenix_mcp.routing.decision_types import DecisionError
from custom_components.phoenix_mcp.routing.routing_tables import (
    DECISION_CLOUDFLARE_BASE_URL, DECISION_GEMINI_BASE_URL, DECISION_OPENCODE_BASE_URL,
    DECISION_ROUTE_CHOICES, DECISION_SEQUENTIAL_EXECUTION_MODELS, DECISION_TYPESAFE_BASE_URL,
    DECISION_BATCH_EXECUTION_MODELS, DECISION_PERCENTAGE_EXECUTION_MODELS, DECISION_CONTEXT_EXECUTION_MODELS,
    DECISION_REFERENCE_EXECUTION_MODELS, DECISION_FOLLOWUP_EXECUTION_MODELS,
)

ACCOUNT = "0123456789abcdef0123456789abcdef"


def _answer(model: str | None = "jev-1.13.0") -> dict:
    body = {"answers": {"route": {"type": "choice", "choice": "light_on", "confidence": 0.95,
                                  "probabilities": {key: 0.95 if key == "light_on" else 0.0125
                                                    for key in DECISION_ROUTE_CHOICES}}},
            "usage": {"input_tokens": 10, "output_tokens": 0}}
    if model is not None:
        body["model"] = model
    return body


async def _serve(path: str, reply):
    """A local endpoint at ``path`` that records each request and answers with ``reply(request)``."""
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append({"path": request.path, "headers": dict(request.headers), "body": await request.json()})
        return web.json_response(reply(request))

    app = web.Application()
    app.router.add_post(path, handler)
    return app, seen


async def test_opencode_posts_system_one_with_bearer_and_lists_fixed_models(socket_enabled):
    app, seen = await _serve("/v1/systemone", lambda _: _answer("jev-1.13.0"))
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("opencode_decision", "jev-1.13", str(server.make_url("/v1")), "secret")
        provider = DecisionProvider(cfg, "systemone")
        # No network for the model list: these endpoints list no decision models.
        assert await provider.list_models(session) == ["gemini-decision-1", "gpt-6-luna-decision", "jev-1.13"]
        assert seen == []
        result = await provider.classify(session, "Turn on the lamp", 1000)
    assert result["choice"] == "light_on" and result["model"] == "jev-1.13.0"
    assert seen[0]["headers"]["Authorization"] == "Bearer secret"
    assert seen[0]["body"]["model"] == "jev-1.13" and "state" in seen[0]["body"]


async def test_fixed_model_validation_is_one_bounded_decision(socket_enabled):
    def reply(request):
        return {"model": "gemini-decision-1", "answers": {"check": {
            "type": "choice", "choice": "ready", "confidence": 0.99,
            "probabilities": {"ready": 0.99, "other": 0.01}}}}
    app, seen = await _serve("/v1/systemone", reply)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("opencode_decision", "gemini-decision-1", str(server.make_url("/v1")), "k")
        assert await DecisionProvider(cfg, "systemone").validate(session) == (True, "")
    assert len(seen) == 1 and set(seen[0]["body"]["questions"]) == {"check"}


async def test_cloudflare_runs_the_model_address_and_unwraps_its_envelope(socket_enabled):
    app, seen = await _serve("/run/clef-flash", lambda _: {
        "result": _answer("clef-flash"), "success": True, "errors": [], "messages": []})
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("cloudflare_decision", "clef-flash", str(server.make_url("/run")), "token")
        result = await DecisionProvider(cfg, "systemone").classify(session, "Turn on the lamp", 1000)
    assert result["model"] == "clef-flash"
    assert seen[0]["path"] == "/run/clef-flash" and seen[0]["headers"]["Authorization"] == "Bearer token"
    assert seen[0]["body"]["model"] == "clef-flash"


@pytest.mark.parametrize("envelope", [
    {"result": None, "success": True},
    {"result": {"answers": {}}, "success": False},
])
async def test_cloudflare_failed_envelope_is_unreadable(envelope, socket_enabled):
    app, _ = await _serve("/run/clef", lambda _: envelope)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("cloudflare_decision", "clef", str(server.make_url("/run")), "token")
        with pytest.raises(DecisionError, match="^malformed$"):
            await DecisionProvider(cfg, "systemone").classify(session, "Turn on the lamp", 1000)


async def test_gemini_sends_text_input_with_its_own_key_header(socket_enabled):
    app, seen = await _serve("/v1beta/decisions", lambda _: {**_answer(None), "usage": {
        "total_input_tokens": 12, "total_output_tokens": 0, "total_tokens": 12}})
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("gemini_decision", "gemini-decision-1",
                                          str(server.make_url("/v1beta")), "google-key")
        result = await DecisionProvider(cfg, "gemini_decisions").classify(session, "Turn on the lamp", 1000)
    sent = seen[0]
    assert sent["headers"]["x-goog-api-key"] == "google-key" and "Authorization" not in sent["headers"]
    assert "state" not in sent["body"] and sent["body"]["input"].startswith("state = {")
    assert "Turn on the lamp" in sent["body"]["input"]
    assert result["model"] == "gemini-decision-1"


async def test_gemini_answer_naming_a_model_is_refused(socket_enabled):
    app, _ = await _serve("/v1beta/decisions", lambda _: _answer("other-model"))
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("gemini_decision", "gemini-decision-1", str(server.make_url("/v1beta")), "k")
        with pytest.raises(DecisionError, match="^malformed$"):
            await DecisionProvider(cfg, "gemini_decisions").classify(session, "Turn on the lamp", 1000)


def test_gemini_request_converts_criteria_to_text_and_its_key_names():
    body = gemini_request("gemini-decision-1", {"utterance": "x"}, {
        "target": {"type": "choice", "instructions": "Pick.", "criteria": {"light.a": None, "none": "No match."}},
        "yes": {"type": "noul", "instructions": ["Is it?"], "criteria": {"true": "Yes.", "false": "No."}},
        "level": {"type": "score", "instructions": "Rate.", "criteria": ["low", {"label": "high"}]},
    })
    questions = body["questions"]
    assert questions["target"]["criteria"] == {"light.a": "", "none": "No match."}
    assert questions["yes"] == {"type": "noul", "instructions": '["Is it?"]',
                                "criteria": {"true_description": "Yes.", "false_description": "No."}}
    assert questions["level"]["criteria"] == ["low", '{"label":"high"}']


@pytest.mark.parametrize("kind,protocol", [
    ("opencode_decision", "gemini_decisions"),
    ("gemini_decision", "systemone"),
    ("claude", "systemone"),
])
def test_unknown_kind_and_protocol_pairs_are_refused(kind, protocol):
    with pytest.raises(ValueError):
        DecisionProvider(llm_accounts.ProviderConfig(kind, "m", "https://example.invalid"), protocol)


def test_every_cloudflare_account_shares_the_registry_endpoint():
    real = llm_accounts.ProviderConfig("cloudflare_decision", "clef-flash",
                                       DECISION_CLOUDFLARE_BASE_URL.format(account_id=ACCOUNT))
    assert decision_endpoint(real) == DECISION_CLOUDFLARE_BASE_URL
    assert decision_key(real) == ("cloudflare_decision", DECISION_CLOUDFLARE_BASE_URL, "clef-flash")
    for forged in ("https://evil.example/client/v4/accounts/" + ACCOUNT + "/ai/run/@cf/cloudflare",
                   DECISION_CLOUDFLARE_BASE_URL.format(account_id="../" + ACCOUNT[3:]),
                   DECISION_CLOUDFLARE_BASE_URL.format(account_id=ACCOUNT + "/x")):
        cfg = llm_accounts.ProviderConfig("cloudflare_decision", "clef-flash", forged)
        assert decision_endpoint(cfg) == forged
    # The same address under another kind is not Cloudflare's.
    other = llm_accounts.ProviderConfig("opencode_decision", "clef-flash", real.base_url)
    assert decision_endpoint(other) == real.base_url


@pytest.mark.parametrize("cfg", [
    llm_accounts.ProviderConfig("cloudflare_decision", "clef-flash",
                                "https://api.cloudflare.com/client/v4/accounts/not-an-id/ai/run/@cf/cloudflare"),
    llm_accounts.ProviderConfig("opencode_decision", "jev-1.13", "https://self-hosted.example/v1"),
    llm_accounts.ProviderConfig("gemini_decision", "gemini-decision-1", "https://self-hosted.example/v1beta"),
])
def test_hosted_providers_cannot_act_sequentially_without_a_registry_entry(cfg):
    """Only a custom TypeSafe address is the operator's own choice; every other endpoint needs qualification."""
    assert decision_key(cfg) not in DECISION_SEQUENTIAL_EXECUTION_MODELS
    returned = cfg.model
    assert not sequential_revision_admitted(cfg, returned, returned)
    assert not revision_admitted(DECISION_SEQUENTIAL_EXECUTION_MODELS, cfg, returned)


def test_custom_typesafe_address_keeps_operator_choice():
    custom = llm_accounts.ProviderConfig("typesafe", "jev-latest", "https://jev.self-hosted.example/v1")
    assert sequential_revision_admitted(custom, "jev-x", "jev-x")
    assert not sequential_revision_admitted(custom, "jev-x", "jev-y")
    hosted = llm_accounts.ProviderConfig("typesafe", "jev-latest", DECISION_TYPESAFE_BASE_URL)
    assert not sequential_revision_admitted(hosted, "jev-preview", "jev-preview")


@pytest.mark.parametrize("account_id,ok", [
    (ACCOUNT, True),
    ("  " + ACCOUNT.upper() + " ", True),
    (ACCOUNT[:-1], False),
    (ACCOUNT + "0", False),
    ("../" + ACCOUNT[3:], False),
    ("", False),
    (None, False),
])
async def test_cloudflare_setup_takes_only_an_account_id(account_id, ok):
    probe = await agent_chat_views._probe_config("cloudflare_decision", {
        "account_id": account_id, "api_key": "token", "base_url": "https://evil.example", "model": "clef"})
    if not ok:
        assert isinstance(probe, ProbeConfigError) and probe.key == "providerAccountIdInvalid"
        return
    assert probe.base_url == DECISION_CLOUDFLARE_BASE_URL.format(account_id=ACCOUNT)
    stored = agent_chat_views._stored_setup_config(probe, "clef")
    assert stored == {"api_key": "token", "base_url": probe.base_url, "model": "clef"}


async def test_fixed_decision_endpoints_ignore_a_supplied_address():
    for kind, base_url in (("opencode_decision", DECISION_OPENCODE_BASE_URL),
                           ("gemini_decision", DECISION_GEMINI_BASE_URL)):
        probe = await agent_chat_views._probe_config(kind, {"api_key": "k", "base_url": "https://evil.example"})
        assert probe.base_url == base_url
        assert "base_url" not in agent_chat_views._stored_setup_config(probe, probe.model)


@pytest.mark.parametrize("kind,base_url,model", [
    ("opencode_decision", DECISION_OPENCODE_BASE_URL, "jev-1.13"),
    ("opencode_decision", DECISION_OPENCODE_BASE_URL, "gemini-decision-1"),
    ("opencode_decision", DECISION_OPENCODE_BASE_URL, "gpt-6-luna-decision"),
    ("gemini_decision", DECISION_GEMINI_BASE_URL, "gemini-decision-1"),
    ("cloudflare_decision", DECISION_CLOUDFLARE_BASE_URL.format(account_id=ACCOUNT), "clef-flash"),
    ("cloudflare_decision", DECISION_CLOUDFLARE_BASE_URL.format(account_id=ACCOUNT), "clef"),
])
def test_qualified_hosted_models_act_on_single_requests_only_as_themselves(kind, base_url, model):
    cfg = llm_accounts.ProviderConfig(kind, model, base_url)
    for registry in (DECISION_SEQUENTIAL_EXECUTION_MODELS, DECISION_BATCH_EXECUTION_MODELS,
                     DECISION_PERCENTAGE_EXECUTION_MODELS):
        assert revision_admitted(registry, cfg, model)
        # No followed alias here: another revision, even a JEV one, is not admitted.
        assert not revision_admitted(registry, cfg, "jev-1.14.0")
    assert sequential_revision_admitted(cfg, model, model)
    for registry in (DECISION_CONTEXT_EXECUTION_MODELS, DECISION_REFERENCE_EXECUTION_MODELS,
                     DECISION_FOLLOWUP_EXECUTION_MODELS):
        assert decision_key(cfg) not in registry


@pytest.mark.parametrize("kind,expected", [
    ("opencode_decision", "provider_context_limit"),
    ("cloudflare_decision", "http_error"),
])
async def test_relayed_jev_capacity_refusal_is_recognized_only_where_jev_answers(kind, expected, socket_enabled):
    async def handler(request):
        return web.json_response({"detail": {"error_type": "max_tokens_exceeded"}}, status=400)
    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    app.router.add_post("/v1/clef", handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig(kind, "clef" if kind == "cloudflare_decision" else "jev-1.13",
                                          str(server.make_url("/v1")), "k")
        with pytest.raises(DecisionError, match="^" + expected + "$"):
            await DecisionProvider(cfg, "systemone").classify(session, "command", 1000)
