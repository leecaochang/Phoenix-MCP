"""Tool-level loading under a provider's tools-array cap, in the loader and in both agent loops.

The loader only declares definitions the access key's catalog already holds,
so the properties that matter are that nothing is dropped silently, that an
undeclared call never reaches the dispatcher, and that eviction never takes a
tool from the round that loaded it.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.phoenix_mcp import agent_chat, agent_turn
from custom_components.phoenix_mcp.llm import base as llm_base
from custom_components.phoenix_mcp.agent_recovery import AgentRecovery
from custom_components.phoenix_mcp.provider_constants import AGENT_CHAT_LOAD_TOOLS_NAME
from custom_components.phoenix_mcp.access_key_store import GlobalSettings
from custom_components.phoenix_mcp.tool_loading import ToolLoader
from tests.runtime_fixtures import patch_readers


def _tool(name: str, required: tuple[str, ...] = ()) -> dict:
    return {"name": name, "description": f"Does {name}. More detail follows.",
            "inputSchema": {"type": "object", "properties": {key: {"type": "string"} for key in required},
                            "required": list(required)}}


def _catalog(starting: int = 10, esphome: int = 4, dashboards: int = 3) -> list[dict]:
    """Generic starting tools plus tools the name fragments place in two deferred groups."""
    return ([_tool(f"plain_{i}") for i in range(starting)]
            + [_tool(f"esphome_tool_{i}", ("file",)) for i in range(esphome)]
            + [_tool(f"dashboard_tool_{i}") for i in range(dashboards)])


def _load(loader: ToolLoader, **arguments) -> tuple[dict, bool]:
    text, is_error = loader.load(arguments)
    return json.loads(text) if text.startswith("{") else {"text": text}, is_error


def test_no_loader_within_the_cap_or_without_deferrable_tools():
    assert ToolLoader.plan(_catalog(), 17) is None
    assert ToolLoader.plan(_catalog(), None) is None
    assert ToolLoader.plan([_tool(f"plain_{i}") for i in range(20)], 10) is None


def test_the_first_round_declares_the_starting_set_and_the_loader():
    loader = ToolLoader.plan(_catalog(), 14)
    names = [tool["name"] for tool in loader.round_tools()]
    assert names[0] == AGENT_CHAT_LOAD_TOOLS_NAME and len(names) == 11 and loader.capacity == 3
    assert loader.groups == {"esphome": [f"esphome_tool_{i}" for i in range(4)],
                             "dashboards": [f"dashboard_tool_{i}" for i in range(3)]}
    directory = loader.directory()
    assert all(f"- {name}: Does {name}." in directory for name in loader.deferred)
    assert "does not apply to the tools listed here" in directory


def test_names_load_what_exists_and_report_the_rest():
    loader = ToolLoader.plan(_catalog(), 14)
    loader.round_tools()
    body, is_error = _load(loader, names=["esphome_tool_0", "plain_1", "made_up"])
    assert not is_error
    assert body["loaded"] == ["esphome_tool_0"]
    assert body["required_arguments"] == {"esphome_tool_0": ["file"]}
    assert body["already_available"] == ["plain_1"] and body["unknown"] == ["made_up"]
    # The tool is callable only from the next round, when it is declared.
    assert loader.blocked("esphome_tool_0") is not None
    assert "esphome_tool_0" in [tool["name"] for tool in loader.round_tools()]
    assert loader.blocked("esphome_tool_0") is None


def test_a_group_that_does_not_fit_is_refused_whole():
    loader = ToolLoader.plan(_catalog(), 14)
    loader.round_tools()
    body, is_error = _load(loader, group="esphome")
    assert is_error and body["group_not_loaded"] == "esphome" and "loaded" not in body
    body, is_error = _load(loader, group="dashboards")
    assert not is_error and body["loaded"] == [f"dashboard_tool_{i}" for i in range(3)]


def test_a_round_never_evicts_what_it_loaded_but_the_next_round_can():
    loader = ToolLoader.plan(_catalog(), 14)
    loader.round_tools()
    _load(loader, names=["esphome_tool_0", "esphome_tool_1", "esphome_tool_2"])
    body, is_error = _load(loader, names=["esphome_tool_3"])
    assert is_error and body["not_loaded"] == ["esphome_tool_3"]
    loader.round_tools()
    loader.use("esphome_tool_0")
    body, _ = _load(loader, names=["esphome_tool_3"])
    # esphome_tool_0 was used last, so the least recently used one goes instead.
    assert body["loaded"] == ["esphome_tool_3"] and body["unloaded"] == ["esphome_tool_1"]


@pytest.mark.parametrize("arguments", [{}, {"names": "esphome_tool_0"}, {"names": [1]}, {"group": "zigbee"},
                                       {"names": ["esphome_tool_0"], "extra": True}, "esphome_tool_0"])
def test_malformed_load_requests_load_nothing(arguments):
    loader = ToolLoader.plan(_catalog(), 14)
    loader.round_tools()
    text, is_error = loader.load(arguments)
    assert is_error and "Nothing was loaded" in text
    assert [tool["name"] for tool in loader.round_tools()][-1] == "plain_9"


class _Scripted:
    """A provider replaying scripted rounds and recording what each request declared."""

    kind = "chatgpt"

    def __init__(self, turns: list[list[dict]]) -> None:
        self.turns, self.i = turns, 0
        self.declared: list[list[str]] = []
        self.system_prompts: list[str] = []

    def format_tools(self, tools):
        return tools

    def append_assistant(self, messages, msg):
        messages.append(msg)

    def append_tool_results(self, messages, results):
        messages.append({"role": "user", "tool_results": results})

    async def stream_turn(self, session, *, system_prompt, messages, tools, options):
        self.declared.append([tool["name"] for tool in tools])
        self.system_prompts.append(system_prompt)
        turn = self.turns[self.i]
        self.i += 1
        for event in turn:
            yield event


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"type": llm_base.EV_TOOL, "id": call_id, "name": name, "input": arguments}


def _done(stop_reason: str) -> dict:
    return {"type": llm_base.EV_DONE, "stop_reason": stop_reason, "assistant_msg": {"role": "assistant", "content": []}}


def _script() -> list[list[dict]]:
    # Round 1 loads a tool and, in the same response, calls a deferred tool it
    # never declared. Round 2 calls the loaded tool. Round 3 answers.
    return [
        [_call("a", AGENT_CHAT_LOAD_TOOLS_NAME, {"names": ["esphome_tool_0"]}),
         _call("b", "esphome_tool_1", {"file": "x.yaml"}), _done("tool_use")],
        [_call("c", "esphome_tool_0", {"file": "x.yaml"}), _done("tool_use")],
        [{"type": llm_base.EV_TEXT, "text": "Done."}, _done("end_turn")],
    ]


def _data() -> MagicMock:
    data = MagicMock()
    data.agent_recovery = AgentRecovery()
    data.shutting_down = False
    data.store.get_settings.return_value = GlobalSettings()
    live = MagicMock(id="tid")
    live.is_valid.return_value = True
    data.store.get_access_key_by_id.return_value = live
    return data


def _assert_loading_turn(provider: _Scripted, dispatch: AsyncMock, results: list[dict]) -> None:
    starting = [f"plain_{i}" for i in range(120)]
    assert provider.declared[0] == [AGENT_CHAT_LOAD_TOOLS_NAME, *starting]
    assert provider.declared[1] == [AGENT_CHAT_LOAD_TOOLS_NAME, *starting, "esphome_tool_0"]
    assert all(len(names) <= 128 for names in provider.declared)
    assert "[esphome]" in provider.system_prompts[0] and "- esphome_tool_1:" in provider.system_prompts[0]
    # Only the declared, loaded tool reached the dispatcher.
    assert [call.args[2]["name"] for call in dispatch.await_args_list] == ["esphome_tool_0"]
    first = results[0]
    assert [r["tool_use_id"] for r in first] == ["a", "b"]
    assert json.loads(first[0]["result_text"])["loaded"] == ["esphome_tool_0"] and not first[0]["is_error"]
    assert first[1]["is_error"] and "is not loaded" in first[1]["result_text"]


@pytest.mark.asyncio
async def test_agent_chat_declares_in_parts_and_never_dispatches_an_undeclared_call(hass):
    provider = _Scripted(_script())
    dispatch = AsyncMock(return_value=({"result": {"content": [{"type": "text", "text": "ok"}]}}, "m", "r", "allowed"))
    events: list = []

    async def emit(name, payload):
        events.append((name, payload))

    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=_catalog(120, 20, 3)), \
            patch_readers("custom_components.phoenix_mcp.tool_dispatch._build_instructions", return_value="sys"), \
            patch_readers("custom_components.phoenix_mcp.tool_dispatch._dispatch_mcp", dispatch):
        messages = await agent_chat.async_run_agent_turn(
            hass=hass, data=_data(), access_key=MagicMock(), provider=provider, session=MagicMock(), messages=[],
            options={}, client_ip="agentcli", base_url="http://h", emit=emit, cancel=asyncio.Event())

    _assert_loading_turn(provider, dispatch, [m["tool_results"] for m in messages if "tool_results" in m])
    shown = [payload["name"] for name, payload in events if name == "tool_result"]
    assert shown == [AGENT_CHAT_LOAD_TOOLS_NAME, "esphome_tool_1", "esphome_tool_0"]
    assert not [payload for name, payload in events if name == "error"]


@pytest.mark.asyncio
async def test_the_headless_loop_loads_the_same_way(hass):
    provider = _Scripted(_script())
    dispatch = AsyncMock(return_value=({"result": {"content": [{"type": "text", "text": "ok"}]}}, "m", "r", "allowed"))
    data = _data()
    access_key = data.store.get_access_key_by_id.return_value
    captured: list[list[dict]] = []
    original = provider.append_tool_results

    def append(messages, results):
        captured.append(results)
        original(messages, results)

    provider.append_tool_results = append
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=_catalog(120, 20, 3)), \
            patch_readers("custom_components.phoenix_mcp.tool_dispatch._dispatch_mcp", dispatch):
        result = await agent_turn._run_headless_turn(hass, data, access_key, provider, None, "http://h",
                                                   system="s", seed="build it", client_ip="voice", max_iterations=4)

    assert result.outcome == agent_turn.TURN_OK and result.text == "Done."
    _assert_loading_turn(provider, dispatch, captured)


@pytest.mark.asyncio
async def test_a_provider_without_a_cap_keeps_the_complete_catalog(hass):
    provider = _Scripted([[{"type": llm_base.EV_TEXT, "text": "Hi."}, _done("end_turn")]])
    provider.kind = "deepseek"
    data = _data()
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=_catalog(120, 20, 3)):
        await agent_turn._run_headless_turn(hass, data, data.store.get_access_key_by_id.return_value, provider, None,
                                          "http://h", system="s", seed="hi", client_ip="voice", max_iterations=2)
    assert len(provider.declared[0]) == 143 and AGENT_CHAT_LOAD_TOOLS_NAME not in provider.declared[0]
    assert "Deferred tools" not in provider.system_prompts[0]
