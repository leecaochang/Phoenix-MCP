"""A local OpenAI-compatible server (llama.cpp, vLLM, Bionic, LocalAI) as a conversation account.

The server sits on the operator's network, so setup admits a private address but never a metadata
one, a key is optional, and every request goes to a stub server speaking the OpenAI wire shape.
"""
import dataclasses
import json

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.phoenix_mcp import agent_chat_views
from custom_components.phoenix_mcp.agent_turn import _headless_call_seconds
from custom_components.phoenix_mcp.catalog_text import LocalizedReason
from custom_components.phoenix_mcp.llm import base as llm_base
from custom_components.phoenix_mcp.llm.accounts import (
    _instance_name, _provider_identity, _with_probed, PROVIDER_DEFINITIONS, ProviderConfig,
)
from custom_components.phoenix_mcp.llm.base import ProbeConfigError
from custom_components.phoenix_mcp.llm.factory import build_provider
from custom_components.phoenix_mcp.llm.probes import async_probe_capabilities
from custom_components.phoenix_mcp.llm.provider_openai import OpenAICompatProvider
from custom_components.phoenix_mcp.provider_constants import (
    AGENT_CHAT_MAX_TOOLS_PER_REQUEST, AGENT_HEADLESS_LOCAL_CALL_SECONDS,
)

# Every request goes over a real socket to the stub server.
pytestmark = pytest.mark.usefixtures("socket_enabled")

# llama.cpp lists a model by its file path unless the server was started with --alias.
_GGUF = "/models/chat/example-7b-instruct-Q4_K_M.gguf"


class _Stub:
    """An OpenAI-compatible server: records each request and answers from a script."""

    def __init__(self, *, models_status: int = 200, chat: list[str] | None = None,
                 chat_status: int = 200, chat_error: dict | None = None,
                 models: list[str] | None = None, native: list[dict] | None = None,
                 thinks=None, props: dict | None = None) -> None:
        self.requests: list[tuple[str, dict, dict]] = []
        self.models_status = models_status
        self.models = models if models is not None else [_GGUF]
        self.chat = chat or []
        self.chat_status = chat_status
        self.chat_error = chat_error
        self.native = native
        # For a non-streamed request: whether the model visibly thinks given the request body.
        self.thinks = thinks or (lambda body: False)
        self.props = props

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/v1/models", self._models)
        app.router.add_post("/v1/chat/completions", self._completions)
        if self.native is not None:
            app.router.add_get("/api/v1/models", self._native_models)
        if self.props is not None:
            app.router.add_get("/props", self._props)
        return app

    async def _props(self, request: web.Request) -> web.Response:
        self.requests.append(("PROPS", dict(request.headers), {}))
        return web.json_response(self.props)

    async def _native_models(self, request: web.Request) -> web.Response:
        return web.json_response({"models": self.native})

    async def _models(self, request: web.Request) -> web.Response:
        self.requests.append(("GET", dict(request.headers), {}))
        if self.models_status != 200:
            return web.json_response({"error": {"message": "no"}}, status=self.models_status)
        return web.json_response({"object": "list", "data": [
            {"id": model, "object": "model", "created": 1700000000 + index, "owned_by": "llamacpp"}
            for index, model in enumerate(self.models)]})

    async def _completions(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(("POST", dict(request.headers), body))
        if self.chat_status != 200:
            return web.json_response(self.chat_error or {}, status=self.chat_status)
        if body.get("stream") is False:
            message = {"role": "assistant", "content": "391"}
            if self.thinks(body):
                message["reasoning_content"] = "17 times 23 is 391."
            return web.json_response({"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for frame in [*self.chat, "[DONE]"]:
            await response.write(f"data: {frame}\n\n".encode())
        await response.write_eof()
        return response


def _cfg(server: TestServer, *, api_key: str = "", model: str = _GGUF) -> ProviderConfig:
    return ProviderConfig(kind="local_openai", model=model, base_url=str(server.make_url("/v1")),
                          api_key=api_key)


def _chunk(delta: dict, finish: str | None = None) -> str:
    return json.dumps({"object": "chat.completion.chunk", "model": _GGUF,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("entered,stored", [
    ("http://127.0.0.1:8080", "http://127.0.0.1:8080/v1"),
    ("http://192.168.1.20:1234/", "http://192.168.1.20:1234/v1"),
    ("http://10.0.0.5:8000/v1/", "http://10.0.0.5:8000/v1"),
    ("https://10.0.0.5/openai/v1", "https://10.0.0.5/openai/v1"),
])
async def test_setup_takes_a_private_address_and_adds_v1_only_to_a_bare_one(entered, stored):
    cfg = await agent_chat_views._probe_config("local_openai", {"base_url": entered})
    assert isinstance(cfg, ProviderConfig)
    assert cfg.base_url == stored
    assert cfg.api_key == ""


@pytest.mark.asyncio
async def test_setup_keeps_an_optional_key():
    cfg = await agent_chat_views._probe_config("local_openai", {"base_url": "http://127.0.0.1:8080", "api_key": " sk-local "})
    assert isinstance(cfg, ProviderConfig) and cfg.api_key == "sk-local"
    assert agent_chat_views._stored_setup_config(cfg) == {"api_key": "sk-local", "base_url": "http://127.0.0.1:8080/v1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("body,key", [
    ({}, "providerServerUrlRequired"),
    ({"base_url": "  "}, "providerServerUrlRequired"),
    ({"base_url": "http://169.254.169.254/v1"}, "providerUrlAddressForbidden"),
    ({"base_url": "http://user:pw@127.0.0.1:8080/v1"}, "providerUrlCredentialsForbidden"),
    ({"base_url": "http://127.0.0.1:8080/v1#x"}, "providerUrlFragmentForbidden"),
    ({"base_url": "ftp://127.0.0.1/v1"}, "providerUrlScheme"),
    ({"base_url": "http://127.0.0.1:8080", "api_key": "bad\nkey"}, "providerApiKeyRequired"),
])
async def test_setup_refusals(body, key):
    error = await agent_chat_views._probe_config("local_openai", body)
    assert isinstance(error, ProbeConfigError)
    assert error.key == key


def test_the_definition_takes_the_shared_tool_cap_and_no_declared_reasoning():
    definition = PROVIDER_DEFINITIONS["local_openai"]
    assert definition.max_tools == AGENT_CHAT_MAX_TOOLS_PER_REQUEST
    assert definition.reasoning == "probed_effort" and definition.ollama is None
    assert [(field.id, field.required) for field in definition.fields] == [("base_url", True), ("api_key", False)]


def test_one_server_is_one_account_whatever_its_key():
    first = _provider_identity("local_openai", {"base_url": "http://Box.lan:8080/v1/", "api_key": "a"})
    second = _provider_identity("local_openai", {"base_url": "http://box.lan:8080/v1", "api_key": "b"})
    other = _provider_identity("local_openai", {"base_url": "http://box.lan:1234/v1", "api_key": "a"})
    assert first == second and first != other


@pytest.mark.parametrize("kind,url,shown", [
    ("local_openai", "http://box.lan:8080/v1", "(box.lan:8080)"),
    ("local_openai", "http://box.lan/v1", "(box.lan)"),
    ("local_openai", "http://[fd00::5]:1235/v1", "([fd00::5]:1235)"),
    ("ollama", "http://box.lan:11434", "(box.lan:11434)"),
])
def test_two_servers_on_one_computer_are_told_apart_by_port(kind, url, shown):
    assert _instance_name({"kind": kind, "base_url": url}, True, "abcdef123456").endswith(shown)


@pytest.mark.parametrize("server,dupes,name", [
    ("Bionic", False, "OpenAI-Compatible (Bionic)"),
    ("llama.cpp", True, "OpenAI-Compatible (llama.cpp, box.lan:8080)"),
    (None, False, "OpenAI-Compatible"),
    ("<b>forged</b>", False, "OpenAI-Compatible"),
])
def test_an_account_is_named_by_its_software(server, dupes, name):
    inst = {"kind": "local_openai", "base_url": "http://box.lan:8080/v1", "catalog": {"server": server}}
    assert _instance_name(inst, dupes, "abcdef123456") == name


def test_a_local_server_gets_the_long_headless_deadline():
    provider = build_provider(ProviderConfig(kind="local_openai", model="m", base_url="http://127.0.0.1:8080/v1"))
    assert _headless_call_seconds(provider) == AGENT_HEADLESS_LOCAL_CALL_SECONDS


# --------------------------------------------------------------------------- #
# Validation and the model list, against a stub server
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_validate_reads_the_model_list_and_sends_no_key_unless_given():
    stub = _Stub()
    async with TestServer(stub.app()) as server, ClientSession() as session:
        assert await OpenAICompatProvider(_cfg(server)).validate(session) == (True, "")
        assert await OpenAICompatProvider(_cfg(server, api_key="sk-local")).validate(session) == (True, "")
    assert "Authorization" not in stub.requests[0][1]
    assert stub.requests[1][1]["Authorization"] == "Bearer sk-local"


@pytest.mark.asyncio
async def test_validate_reports_a_refused_key():
    stub = _Stub(models_status=401)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        ok, reason = await OpenAICompatProvider(_cfg(server, api_key="wrong")).validate(session)
    assert ok is False and isinstance(reason, LocalizedReason) and reason.key == "providerKeyRejected"


@pytest.mark.asyncio
async def test_validate_explains_an_unreachable_server():
    async with TestServer(_Stub().app()) as server:
        url = str(server.make_url("/v1"))
    async with ClientSession() as session:
        ok, reason = await OpenAICompatProvider(
            ProviderConfig(kind="local_openai", model="", base_url=url)).validate(session)
    assert ok is False and isinstance(reason, LocalizedReason)
    assert reason.key == "providerLocalServerUnreachable" and reason.params["url"] == url


@pytest.mark.asyncio
async def test_model_list_judges_a_path_by_its_file_name():
    stub = _Stub(models=[
        "/srv/video-models/example-7b-Q4_K_M.gguf",       # a folder name alone never drops a model
        "/srv/models/nomic-embed-text-v1.5.Q8_0.gguf",     # an embedding model is not a chat model
        "lmstudio-community/example-3b-mlx",
    ])
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server, model=""))
        models = await provider.list_models(session)
    assert models == ["/srv/video-models/example-7b-Q4_K_M.gguf", "lmstudio-community/example-3b-mlx"]
    assert provider.model_created["lmstudio-community/example-3b-mlx"] == 1700000002


_BIONIC_OPENAI_LIST = ["tone-40m-multilingual", "example/chat-27b", "text-embedding-example-v1.5", "example/chat-9b"]


@pytest.mark.asyncio
async def test_a_bionic_list_keeps_only_what_it_types_as_a_language_model():
    # The voice model, whose name does not say so, is missing from Bionic's own list and the embedding model is typed as one.
    stub = _Stub(models=_BIONIC_OPENAI_LIST, native=[
        {"type": "llm", "key": "example/chat-27b", "capabilities": {"trained_for_tool_use": True}},
        {"type": "embedding", "key": "text-embedding-example-v1.5"},
        {"type": "llm", "key": "example/chat-9b"},
    ])
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server, model=""))
        models = await provider.list_models(session)
    assert models == ["example/chat-27b", "example/chat-9b"]
    assert provider.server_name == "Bionic"


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [[], [{"name": "untyped"}], [{"key": "x", "type": 3}]])
async def test_a_native_list_without_types_changes_nothing(native):
    stub = _Stub(models=_BIONIC_OPENAI_LIST, native=native)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server, model=""))
        models = await provider.list_models(session)
    assert models == ["example/chat-27b", "example/chat-9b", "tone-40m-multilingual"]
    # An unusable typed list names nothing; the stub's owned_by, like llama.cpp's, does.
    assert provider.server_name == "llama.cpp"


_BIONIC_DECLARED = [
    {"type": "llm", "key": "example/chat-27b", "capabilities": {
        "vision": True, "trained_for_tool_use": True,
        "reasoning": {"allowed_options": ["off", "low", "medium", "xhigh", "on"], "default": "xhigh"}}},
    {"type": "llm", "key": "example/chat-9b", "capabilities": {
        "vision": False, "trained_for_tool_use": True, "reasoning": {"allowed_options": ["off", "on"], "default": "on"}}},
    {"type": "llm", "key": "example/plain-3b", "capabilities": {"trained_for_tool_use": False}},
    {"type": "llm", "key": "example/bare-1b"},
    {"type": "embedding", "key": "text-embedding-example-v1.5", "capabilities": {"vision": True}},
]


@pytest.mark.asyncio
async def test_bionic_declarations_become_capabilities():
    stub = _Stub(models=[row["key"] for row in _BIONIC_DECLARED], native=_BIONIC_DECLARED)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server, model=""))
        models = await provider.list_models(session)
        caps = await provider.list_model_capabilities(session, models)
    assert caps == {
        # Bionic's off is "none" on the wire and its on (the default) is not a level.
        "example/chat-27b": {"vision": True, "tools": True, "thinking": True,
                             "effort_levels": ["none", "low", "medium", "xhigh"], "thinking_default": "xhigh"},
        "example/chat-9b": {"vision": False, "tools": True, "thinking": True, "effort": False},
        # Untrained for tools is not "cannot call tools"; declared without reasoning means none.
        "example/plain-3b": {"thinking": False},
    }


def _props(vision=None, tools=None, effort=None, levels=()):
    caps = {"supports_parallel_tool_calls": True, "supports_preserve_reasoning": False}
    if tools is not None:
        caps["supports_tool_calls"] = tools
    if effort is not None:
        caps["supports_reasoning_effort"] = effort
        caps["reasoning_effort_levels"] = list(levels)
    out = {"chat_template_caps": caps, "build_info": "b0-example"}
    if vision is not None:
        out["modalities"] = {"vision": vision, "audio": False}
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("props,declared", [
    # A Qwen-style template with a vision projector loaded: no effort levels of its own.
    (_props(vision=True, tools=True, effort=False), {"vision": True, "tools": True}),
    # No projector, and a template without native tool calls: not marked unable, llama.cpp still tries.
    (_props(vision=False, tools=False), {"vision": False}),
    # A template that reads reasoning_effort: its levels in Phoenix's order, unknown ones dropped.
    (_props(effort=True, levels=["max", "low", "turbo", "minimal"]),
     {"thinking": True, "effort_levels": ["minimal", "low", "max"]}),
    (_props(effort=True, levels=[]), {}),
    ({"chat_template_caps": "not a dict", "modalities": ["vision"]}, {}),
])
async def test_llamacpp_props_become_capabilities(props, declared):
    stub = _Stub(props=props)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        caps = await OpenAICompatProvider(_cfg(server)).list_model_capabilities(session, [_GGUF])
    assert caps == ({_GGUF: declared} if declared else {})
    assert [request[0] for request in stub.requests] == ["PROPS"]


@pytest.mark.asyncio
async def test_props_describe_one_model_so_a_longer_list_skips_them():
    stub = _Stub(models=[_GGUF, "other.gguf"], props=_props(vision=True, tools=True))
    async with TestServer(stub.app()) as server, ClientSession() as session:
        assert await OpenAICompatProvider(_cfg(server)).list_model_capabilities(session, [_GGUF, "other.gguf"]) == {}
    assert not [request for request in stub.requests if request[0] == "PROPS"]


@pytest.mark.asyncio
async def test_a_template_with_effort_levels_is_not_probed_for_a_switch():
    stub = _Stub(props=_props(effort=True, levels=["low", "high"]), thinks=_stops_on_effort)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server))
        declared = await provider.list_model_capabilities(session, [_GGUF])
        caps, _calls, _answered = await async_probe_capabilities(session, _cfg(server), declared[_GGUF])
    assert not {"thinking", "effort", "thinking_off"} & set(caps)


@pytest.mark.asyncio
async def test_a_server_without_declarations_declares_nothing():
    async with TestServer(_Stub().app()) as server, ClientSession() as session:
        assert await OpenAICompatProvider(_cfg(server)).list_model_capabilities(session, [_GGUF]) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("options,sent", [
    ({"thinking": False}, "none"),
    ({"thinking": True}, None),
    ({"effort": "none"}, "none"),
    ({"effort": "xhigh"}, "xhigh"),
    ({}, None),
])
async def test_off_on_an_on_off_model_sends_none(options, sent):
    stub = _Stub(chat=[_chunk({"content": "391"}, "stop")])
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server))
        [_ async for _ in provider.stream_turn(session, system_prompt="s", messages=[], tools=[], options=options)]
    assert stub.requests[0][2].get("reasoning_effort") == sent


def _stops_on_effort(body):
    return body.get("reasoning_effort") != "none"


def _stops_on_template(body):
    return (body.get("chat_template_kwargs") or {}).get("enable_thinking") is not False


@pytest.mark.asyncio
@pytest.mark.parametrize("thinks,found", [
    # llama.cpp: reasoning_effort "none" stops it.
    (_stops_on_effort, {"thinking": True, "effort": False, "thinking_off": "reasoning_effort"}),
    # A server that passes enable_thinking to a Qwen-style template.
    (_stops_on_template, {"thinking": True, "effort": False, "thinking_off": "enable_thinking"}),
    # Thinks whatever is sent: it cannot stop.
    (lambda body: True, {"thinking": True, "thinking_off": ""}),
    # Never visibly thinks: nothing is established.
    (lambda body: False, {}),
])
async def test_check_options_finds_how_a_local_model_stops_thinking(thinks, found):
    stub = _Stub(thinks=thinks)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        caps, calls, answered = await async_probe_capabilities(session, _cfg(server))
    assert answered is True
    assert {key: caps[key] for key in ("thinking", "effort", "thinking_off") if key in caps} == found


@pytest.mark.asyncio
async def test_check_options_leaves_declared_thinking_alone():
    stub = _Stub(thinks=_stops_on_effort)
    async with TestServer(stub.app()) as server, ClientSession() as session:
        caps, _calls, _answered = await async_probe_capabilities(
            session, _cfg(server), {"thinking": True, "effort_levels": ["none", "low"]})
    assert not {"thinking", "effort", "thinking_off"} & set(caps)
    assert not any("reasoning_effort" in body or "chat_template_kwargs" in body
                   for _method, _headers, body in stub.requests)


@pytest.mark.parametrize("method,sent", [
    ("reasoning_effort", {"reasoning_effort": "none"}),
    ("enable_thinking", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("", {"reasoning_effort": "none"}),
    (None, {"reasoning_effort": "none"}),
])
@pytest.mark.asyncio
async def test_off_sends_the_stored_off_method(method, sent):
    stub = _Stub(chat=[_chunk({"content": "391"}, "stop")])
    async with TestServer(stub.app()) as server, ClientSession() as session:
        cfg = dataclasses.replace(_cfg(server), thinking_off=method)
        provider = OpenAICompatProvider(cfg)
        [_ async for _ in provider.stream_turn(session, system_prompt="s", messages=[], tools=[],
                                               options={"thinking": False})]
    body = stub.requests[0][2]
    assert {key: body[key] for key in ("reasoning_effort", "chat_template_kwargs") if key in body} == sent


def test_a_found_thinking_switch_outlives_a_catalogue_read():
    probed = {"m": {"thinking": True, "effort": False, "thinking_off": "enable_thinking", "vision": False}}
    # llama.cpp declares nothing, so a catalogue read stores an empty row for the model.
    assert _with_probed({"m": {}}, probed)["m"] == {"thinking": True, "effort": False, "thinking_off": "enable_thinking"}


@pytest.mark.asyncio
@pytest.mark.parametrize("owners,name", [
    (["llamacpp"], "llama.cpp"),
    (["vllm", "vllm"], "vLLM"),
    (["organization_owner"], None),
    (["llamacpp", "vllm"], None),
])
async def test_the_software_is_read_from_owned_by(owners, name):
    stub = _Stub(models=[f"model-{index}" for index in range(len(owners))])
    original = stub._models

    async def owned(request):
        response = await original(request)
        body = json.loads(response.text)
        for row, owner in zip(body["data"], owners):
            row["owned_by"] = owner
        return web.json_response(body)

    stub._models = owned
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server, model=""))
        await provider.list_models(session)
    assert provider.server_name == name


# --------------------------------------------------------------------------- #
# A tool-calling turn
# --------------------------------------------------------------------------- #

_TOOL = {"name": "get_state", "description": "Read one entity.",
         "inputSchema": {"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]}}


@pytest.mark.asyncio
async def test_a_tool_turn_streams_the_call_and_usage():
    stub = _Stub(chat=[
        _chunk({"role": "assistant", "content": None}),
        _chunk({"reasoning_content": "The user wants the light."}),
        _chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                "function": {"name": "get_state", "arguments": "{\"entity_id\":"}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "\"light.kitchen\"}"}}]}),
        _chunk({}, "tool_calls"),
        json.dumps({"object": "chat.completion.chunk", "choices": [],
                    "usage": {"prompt_tokens": 272, "completion_tokens": 85, "total_tokens": 357}}),
    ])
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server))
        events = [event async for event in provider.stream_turn(
            session, system_prompt="s", messages=[{"role": "user", "content": "Is the kitchen light on?"}],
            tools=provider.format_tools([_TOOL]), options={"temperature": 0.2})]
    _, headers, body = stub.requests[0]
    assert "Authorization" not in headers
    assert body["model"] == _GGUF and body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["tools"][0]["function"]["name"] == "get_state" and body["temperature"] == 0.2
    # With no thinking choice, nothing reasoning-shaped is sent and the model keeps its default.
    assert not {"reasoning_effort", "reasoning", "chat_template_kwargs", "thinking"} & set(body)
    tool = next(event for event in events if event["type"] == llm_base.EV_TOOL)
    assert tool["name"] == "get_state" and tool["input"] == {"entity_id": "light.kitchen"}
    usage = [event for event in events if event["type"] == llm_base.EV_USAGE]
    assert usage and usage[-1]["input_tokens"] == 272 and usage[-1]["output_tokens"] == 85
    assert next(event for event in events if event["type"] == llm_base.EV_DONE)["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_a_proven_effort_level_is_sent_as_reasoning_effort():
    stub = _Stub(chat=[_chunk({"content": "Done."}, "stop")])
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server))
        [_ async for _ in provider.stream_turn(session, system_prompt="s", messages=[], tools=[],
                                               options={"effort": "low"})]
    assert stub.requests[0][2]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_a_prompt_over_the_loaded_context_is_a_context_limit_and_not_retried():
    stub = _Stub(chat_status=400, chat_error={"error": {
        "code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 40010, "n_ctx": 32768,
        "message": "request (40010 tokens) exceeds the available context size (32768 tokens), try increasing it"}})
    async with TestServer(stub.app()) as server, ClientSession() as session:
        provider = OpenAICompatProvider(_cfg(server))
        events = [event async for event in provider.stream_turn(
            session, system_prompt="s", messages=[], tools=[], options={})]
    error = next(event for event in events if event["type"] == llm_base.EV_ERROR)
    assert error["code"] == "provider_context_limit" and error["retryable"] is False
    assert len([request for request in stub.requests if request[0] == "POST"]) == 1
