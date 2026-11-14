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
    ("openai_decision", "https://api.openai.com/v1", "gpt-6-luna"),
    ("cloudflare_decision", DECISION_CLOUDFLARE_BASE_URL.format(account_id=ACCOUNT), "clef-flash"),
    ("cloudflare_decision", DECISION_CLOUDFLARE_BASE_URL.format(account_id=ACCOUNT), "clef"),
])
def test_qualified_hosted_models_act_only_as_themselves_and_clef_only_on_single_requests(kind, base_url, model):
    cfg = llm_accounts.ProviderConfig(kind, model, base_url)
    for registry in (DECISION_SEQUENTIAL_EXECUTION_MODELS, DECISION_BATCH_EXECUTION_MODELS,
                     DECISION_PERCENTAGE_EXECUTION_MODELS):
        assert revision_admitted(registry, cfg, model)
        # No followed alias here: another revision, even a JEV one, is not admitted.
        assert not revision_admitted(registry, cfg, "jev-1.14.0")
    assert sequential_revision_admitted(cfg, model, model)
    # Follow-ups were measured on every hosted model except Clef.
    followups = kind != "cloudflare_decision"
    for registry in (DECISION_CONTEXT_EXECUTION_MODELS, DECISION_REFERENCE_EXECUTION_MODELS,
                     DECISION_FOLLOWUP_EXECUTION_MODELS):
        assert (decision_key(cfg) in registry) is followups
        assert revision_admitted(registry, cfg, model) is followups
        assert not revision_admitted(registry, cfg, "jev-1.14.0")


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


def _openai_answer(choice="light_on", name="route", **extra):
    return {"model": "gpt-6-luna", "answers": [{
        "type": "choice", "name": name, "choice": choice, "confidence": 0.95,
        "probabilities": [{"value": key, "probability": 0.95 if key == choice else 0.0125}
                          for key in DECISION_ROUTE_CHOICES], **extra}],
        "usage": {"input_tokens": 50, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                  "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 50}}


async def test_openai_sends_ordered_named_questions_and_reads_ordered_answers(socket_enabled):
    app, seen = await _serve("/v1/decisions", lambda _: _openai_answer())
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("openai_decision", "gpt-6-luna", str(server.make_url("/v1")), "sk-test")
        result = await DecisionProvider(cfg, "openai_decisions").classify(session, "Turn on the lamp", 1000)
    sent = seen[0]
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    message = sent["body"]["input"][0]
    assert message["role"] == "user" and "state" not in sent["body"]
    assert message["content"][1]["type"] == "input_json" and message["content"][1]["data"]["utterance"] == "Turn on the lamp"
    question = sent["body"]["questions"][0]
    assert question["type"] == "choice" and question["name"] == "route"
    assert [choice["value"] for choice in question["choices"]] == list(DECISION_ROUTE_CHOICES)
    assert all(choice["description"] for choice in question["choices"])
    assert result["choice"] == "light_on" and result["model"] == "gpt-6-luna"


@pytest.mark.parametrize("body,reason", [
    (_openai_answer(name="other"), "malformed"),
    ({"model": "gpt-6-luna", "answers": []}, "malformed"),
    ({"model": "gpt-6-luna", "answers": [{"type": "refusal", "name": "route"}]}, "low_probability"),
    ({**_openai_answer(), "answers": [{**_openai_answer()["answers"][0], "probabilities": [
        {"value": "light_on", "probability": 0.5}, {"value": "light_on", "probability": 0.5}]}]}, "malformed"),
    ({**_openai_answer(), "answers": [{**_openai_answer()["answers"][0], "choice": True}]}, "malformed"),
])
async def test_openai_answers_must_match_each_question(body, reason, socket_enabled):
    app, _ = await _serve("/v1/decisions", lambda _: body)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("openai_decision", "gpt-6-luna", str(server.make_url("/v1")), "k")
        with pytest.raises(DecisionError, match="^" + reason + "$"):
            await DecisionProvider(cfg, "openai_decisions").classify(session, "Turn on the lamp", 1000)


def test_openai_request_converts_types_and_folds_yes_no_meaning_into_instructions():
    from custom_components.phoenix_mcp.routing.decision_provider import openai_request
    body = openai_request("gpt-6-luna", {"x": 1}, {
        "pick": {"type": "choice", "instructions": "Pick.", "criteria": {"light.a": None, "40": "Forty."}},
        "yes": {"type": "noul", "instructions": "Is it?", "criteria": {"true": "Lit.", "false": "Dark."}},
        "level": {"type": "score", "instructions": "Rate.", "criteria": ["low", "high"]},
    })
    pick, yes, level = body["questions"]
    assert pick["choices"] == [{"value": "light.a"}, {"value": "40", "description": "Forty."}]
    assert yes == {"type": "predicate", "name": "yes", "instructions": "Is it? True means: Lit. False means: Dark."}
    assert level["levels"] == [{"label": "low"}, {"label": "high"}]


def test_openai_score_and_predicate_answers_take_system_one_shape():
    from custom_components.phoenix_mcp.routing.decision_provider import normalized_response
    body = normalized_response("openai_decision", "gpt-6-luna", {"model": "gpt-6-luna", "answers": [
        {"type": "predicate", "name": "yes", "probability": 0.8},
        {"type": "score", "name": "level", "score": 0.9, "confidence": 0.9, "probabilities": [
            {"value": 0, "label": "low", "probability": 0.1}, {"value": 1, "label": "high", "probability": 0.9}]},
    ]}, ["yes", "level"])
    assert body["answers"]["yes"] == {"type": "noul", "noul": 0.8}
    assert body["answers"]["level"] == {"type": "score", "confidence": 0.9, "score": 0.9,
                                        "probabilities": {"0": 0.1, "1": 0.9}, "legend": {"0": "low", "1": "high"}}


@pytest.mark.parametrize("code,expected", [("context_length_exceeded", "provider_context_limit"), ("other", "http_error")])
async def test_openai_context_limit_is_recognized(code, expected, socket_enabled):
    async def handler(request):
        return web.json_response({"error": {"message": "too long", "type": "invalid_request_error", "code": code}}, status=400)
    app = web.Application()
    app.router.add_post("/v1/decisions", handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("openai_decision", "gpt-6-luna", str(server.make_url("/v1")), "k")
        with pytest.raises(DecisionError, match="^" + expected + "$"):
            await DecisionProvider(cfg, "openai_decisions").classify(session, "command", 1000)


def test_openai_target_choices_carry_the_catalog_name_and_large_state_falls_back_to_text():
    from custom_components.phoenix_mcp.routing.decision_provider import openai_request
    spec = {"target": {"type": "choice", "instructions": "Pick.",
                       "criteria": {"light.bed": None, "light.den": None, "none": "No match."}}}
    plain = {"targets": {"light.bed": {"name": "Bed Light", "aliases": ["Nightlight"], "area": "Bedroom"},
                         "light.den": {"name": "", "aliases": [], "area": ""}}}
    columns = {"target_fields": ["aliases", "area", "name"],
               "targets": {"light.bed": [["Nightlight"], "Bedroom", "Bed Light"], "light.den": [[], "", ""]}}
    for state in (plain, columns):
        body = openai_request("gpt-6-luna", state, spec)
        assert body["questions"][0]["choices"] == [
            {"value": "light.bed", "description": "Bed Light; also called Nightlight; in Bedroom"},
            {"value": "light.den"}, {"value": "none", "description": "No match."}]
        assert body["input"][0]["content"][1] == {"type": "input_json", "data": state}
    # Beyond the documented input_json limits the state goes as text, never truncated.
    many = {"targets": {f"light.l{index}": {"name": f"L{index}"} for index in range(6000)}}
    deep: dict = {"x": 1}
    for _ in range(40):
        deep = {"d": deep}
    for state in (many, deep):
        body = openai_request("gpt-6-luna", state, spec)
        assert isinstance(body["input"], str) and body["input"].startswith("state = {")


@pytest.mark.parametrize("bar,top,confident", [
    (None, 0.85, False), (None, 0.90, True),
    (0.80, 0.85, True), (0.80, 0.79, False),
    (0.70, 0.70, True), (1.00, 0.99, False), (1.00, 1.0, True),
    # Out of bounds or the wrong type falls back to the default, never to a lower bar.
    (0.50, 0.85, False), (1.5, 0.95, True), ("0.7", 0.85, False), (True, 0.95, True),
])
def test_an_answer_meets_its_own_accounts_bar(bar, top, confident):
    from custom_components.phoenix_mcp.routing.decision_types import confident_choice
    answer = {"choice": "a", "probabilities": {"a": top, "b": round(1 - top, 4)}, "model": "m"}
    if bar is not None:
        answer["min_probability"] = bar
    assert confident_choice(answer) is confident


async def test_the_account_threshold_reaches_every_parsed_answer(socket_enabled):
    app, _ = await _serve("/v1/systemone", lambda _: {**_answer("jev-1.13"), "answers": {"route": {
        "type": "choice", "choice": "light_on", "confidence": 0.85,
        "probabilities": {key: 0.85 if key == "light_on" else round(0.15 / (len(DECISION_ROUTE_CHOICES) - 1), 6)
                          for key in DECISION_ROUTE_CHOICES}}}})
    async with TestServer(app) as server, ClientSession() as session:
        for threshold, confident in ((0.90, False), (0.80, True)):
            cfg = llm_accounts.ProviderConfig("opencode_decision", "jev-1.13", str(server.make_url("/v1")), "k",
                                              decision_threshold=threshold)
            result = await DecisionProvider(cfg, "systemone").classify(session, "Turn on the lamp", 1000)
            from custom_components.phoenix_mcp.routing.decision_types import confident_choice
            assert result["min_probability"] == threshold and confident_choice(result) is confident


async def test_store_keeps_a_decision_threshold_only_for_decision_accounts(hass):
    from homeassistant.helpers.storage import Store
    store = llm_accounts.AgentCliStore(Store(hass, 1, "phx_test_decision_threshold"))
    decision = await store.add("typesafe", {"api_key": "k", "model": "jev-latest"})
    chat = await store.add("deepseek", {"api_key": "k2", "model": "m", "base_url": "https://d"})
    assert store.resolve(decision, purpose="decision").decision_threshold == 0.90
    assert await store.set_decision_threshold(decision, 0.8)
    assert store.resolve(decision, purpose="decision").decision_threshold == 0.8
    assert not await store.set_decision_threshold(chat, 0.8)
    rows = {row["id"]: row for row in store.list_instances()}
    assert rows[decision]["decision_threshold"] == 0.8 and "decision_threshold" not in rows[chat]
    # A stored value outside the bounds reads as the default.
    store._data[decision]["decision_threshold"] = 0.4
    assert store.resolve(decision, purpose="decision").decision_threshold == 0.90


@pytest.mark.parametrize("body,status,saved", [
    ({"decision_threshold": 0.8}, 200, 0.8),
    ({"decision_threshold": 1}, 200, 1.0),
    ({"decision_threshold": 0.69}, 400, None),
    ({"decision_threshold": 1.01}, 400, None),
    ({"decision_threshold": 0.805}, 400, None),
    ({"decision_threshold": "0.8"}, 400, None),
    ({"decision_threshold": True}, 400, None),
    ({"decision_threshold": 0.8, "model": "x"}, 400, None),
])
async def test_patch_validates_the_threshold(hass, body, status, saved):
    import json as _json
    from unittest.mock import AsyncMock, MagicMock
    from tests.runtime_fixtures import patch_readers
    secret_store = MagicMock()
    secret_store.get = MagicMock(return_value={"kind": "typesafe"})
    secret_store.set_decision_threshold = AsyncMock(return_value=True)
    view = agent_chat_views.PhoenixAgentCliProviderView()
    view.hass = hass
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    user = MagicMock(); user.is_admin = True; user.id = "admin"
    ctx = {"phoenix_mcp_rid": "rid", KEY_AUTHENTICATED: True, KEY_HASS_USER: user}
    request = MagicMock()
    request.get = MagicMock(side_effect=lambda k, d=None: ctx.get(k, d))
    request.__getitem__ = MagicMock(side_effect=lambda k: ctx[k])
    raw = _json.dumps(body).encode()
    request.content.read = AsyncMock(side_effect=[raw, b""])
    request.headers = {}
    request.content_length = len(raw)
    with patch_readers("custom_components.phoenix_mcp.llm.accounts._get_secret_store",
                       AsyncMock(return_value=secret_store)):
        resp = await view.patch(request, instance_id="pid")
    assert resp.status == status
    if saved is None:
        secret_store.set_decision_threshold.assert_not_awaited()
    else:
        secret_store.set_decision_threshold.assert_awaited_once_with("pid", saved)


@pytest.mark.parametrize("details,expected", [
    ([{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID", "domain": "googleapis.com"}],
     "authentication"),
    ([{"@type": "type.googleapis.com/google.rpc.BadRequest", "fieldViolations": []}], "http_error"),
])
async def test_google_reports_a_bad_key_as_400_and_phoenix_reads_it_as_a_rejected_key(details, expected, socket_enabled):
    async def handler(request):
        return web.json_response({"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                             "status": "INVALID_ARGUMENT", "details": details}}, status=400)
    app = web.Application()
    app.router.add_post("/v1beta/decisions", handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = llm_accounts.ProviderConfig("gemini_decision", "gemini-decision-1", str(server.make_url("/v1beta")), "bad")
        with pytest.raises(DecisionError, match="^" + expected + "$"):
            await DecisionProvider(cfg, "gemini_decisions").classify(session, "command", 1000)
