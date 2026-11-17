"""Running Agent Chat, Voice and AI Task turns answer to current authority before every model call."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.phoenix_mcp import agent_chat, agent_turn, ai_task, tool_dispatch, voice_agent
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.catalog_text import voice_text
from custom_components.phoenix_mcp.const import CAPABILITY_NAMES, DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.llm import base as llm_base
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from tests.runtime_fixtures import patch_readers


class _Provider:
    """Scripted provider that records the system prompt and messages of each request."""

    kind = "test"

    def __init__(self, *calls: list[dict]) -> None:
        self.calls = list(calls)
        self.requests = 0
        self.systems: list[str] = []
        self.seen: list[str] = []

    def format_tools(self, tools):
        return tools

    def append_assistant(self, messages, msg):
        messages.append(msg)

    def append_tool_results(self, messages, results):
        messages.append({"role": "user", "tool_results": results})

    async def stream_turn(self, session, *, system_prompt, messages, tools, options):
        self.requests += 1
        self.systems.append(system_prompt)
        self.seen.append(json.dumps(messages, default=str))
        for event in self.calls.pop(0):
            yield event


def _read(call_id: str) -> list[dict]:
    return [{"type": llm_base.EV_TOOL, "id": call_id, "name": "get_state", "input": {"entity_id": "light.lamp"}},
            {"type": llm_base.EV_DONE, "stop_reason": "tool_use", "assistant_msg": {"role": "assistant", "content": ""}}]


def _answer(text: str) -> list[dict]:
    return [{"type": llm_base.EV_TEXT, "text": text},
            {"type": llm_base.EV_DONE, "stop_reason": "end_turn", "assistant_msg": {"role": "assistant", "content": text}}]


async def _environment(hass: HomeAssistant):
    store = AccessKeyStore(hass, AsyncMock())
    key, _ = await store.async_create_access_key("turn-authority", "admin", rate_limit_requests=0)
    other, _ = await store.async_create_access_key("turn-authority-other", "admin", rate_limit_requests=0)
    key.pass_through = True
    for cap in CAPABILITY_NAMES:
        setattr(key, cap, "allow")
    await store.async_patch_settings(mesa_mode="off", voice_agent_enabled=True, voice_agent_access_key_id=key.id,
                                     ai_task_enabled=True, ai_task_access_key_id=key.id)
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    data.ready = True
    hass.data[DOMAIN] = data
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Hall lamp"})
    return data, key, other


def _allowed_reads(data: PhoenixData) -> int:
    return sum(1 for row in data.audit.query() if row.method == "get_state" and row.outcome == "allowed")


def _voice_binding(data: PhoenixData, key_id: str):
    def still_bound() -> bool:
        current = data.store.get_settings()
        return bool(current.voice_agent_enabled and current.voice_agent_access_key_id == key_id)
    return still_bound


@pytest.mark.parametrize("change", [
    {"voice_agent_enabled": False},
    {"voice_agent_access_key_id": "other"},
])
async def test_a_voice_turn_stops_using_a_key_unbound_mid_turn(hass: HomeAssistant, change):
    data, key, other = await _environment(hass)
    real = tool_dispatch._dispatch_mcp
    rebind = {name: (other.id if value == "other" else value) for name, value in change.items()}

    async def dispatch_then_unbind(*args, **kwargs):
        result = await real(*args, **kwargs)
        await data.store.async_patch_settings(**rebind)
        return result

    provider = _Provider(_read("c1"), _read("c2"), _answer("done"))
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list",
                       return_value=[tool_dispatch._TOOL_DEFS_BY_NAME["get_state"]]), \
            patch.object(agent_turn, "_dispatch_mcp", dispatch_then_unbind):
        answer = await agent_chat.async_run_voice_turn(
            hass, data, agent_turn._zero_inline_wait(key), provider, None, "http://h", "hi", max_iterations=4,
            still_bound=_voice_binding(data, key.id))
    assert _allowed_reads(data) == 1
    assert provider.requests == 1
    assert answer == voice_text(None, "access_key_unavailable")


async def test_an_unbound_key_is_refused_at_dispatch_even_inside_a_round(hass: HomeAssistant):
    """A read then a write in one round: the binding changes after the read, so the write never runs."""
    data, key, other = await _environment(hass)
    real = tool_dispatch._dispatch_mcp
    turned_on = AsyncMock()
    hass.services.async_register("light", "turn_on", turned_on)

    async def dispatch_then_unbind(*args, **kwargs):
        result = await real(*args, **kwargs)
        await data.store.async_patch_settings(ai_task_access_key_id=other.id)
        return result

    write = {"type": llm_base.EV_TOOL, "id": "c2", "name": "call_service", "input": {
        "service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": "entity", "ids": ["light.lamp"]}]}}
    provider = _Provider([*_read("c1")[:1], write, *_read("c2")[1:]], _answer("unused"))

    def still_bound() -> bool:
        current = data.store.get_settings()
        return bool(current.ai_task_enabled and current.ai_task_access_key_id == key.id)

    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list",
                       return_value=[tool_dispatch._TOOL_DEFS_BY_NAME["get_state"],
                                     tool_dispatch._TOOL_DEFS_BY_NAME["call_service"]]), \
            patch.object(agent_turn, "_dispatch_mcp", dispatch_then_unbind):
        with pytest.raises(agent_chat.AiTaskError):
            await agent_chat.async_run_ai_task(hass, data, agent_turn._zero_inline_wait(key), provider, None,
                                               "http://h", "task", max_iterations=4, still_bound=still_bound)
    assert _allowed_reads(data) == 1
    turned_on.assert_not_awaited()
    assert provider.requests == 1


async def test_reads_that_ran_together_withhold_an_answer_once_the_binding_is_gone(hass: HomeAssistant):
    """Two reads in one round start together; the binding changes during them, so no answer after it is used."""
    data, key, other = await _environment(hass)
    real = tool_dispatch._dispatch_mcp

    async def dispatch_then_unbind(*args, **kwargs):
        result = await real(*args, **kwargs)
        await data.store.async_patch_settings(ai_task_access_key_id=other.id)
        return result

    both = [*_read("c1")[:1], *_read("c2")]
    provider = _Provider(both, _answer("unused"))

    def still_bound() -> bool:
        current = data.store.get_settings()
        return bool(current.ai_task_enabled and current.ai_task_access_key_id == key.id)

    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list",
                       return_value=[tool_dispatch._TOOL_DEFS_BY_NAME["get_state"]]), \
            patch.object(agent_turn, "_dispatch_mcp", dispatch_then_unbind):
        with pytest.raises(agent_chat.AiTaskError):
            await agent_chat.async_run_ai_task(hass, data, agent_turn._zero_inline_wait(key), provider, None,
                                               "http://h", "task", max_iterations=4, still_bound=still_bound)
    # Both reads ran before the binding changed and keep their audit rows; neither answer reached a model.
    assert _allowed_reads(data) == 2
    assert provider.requests == 1


async def test_no_provider_round_follows_the_kill_switch_in_voice(hass: HomeAssistant):
    data, key, _ = await _environment(hass)
    real = tool_dispatch._dispatch_mcp

    async def dispatch_then_kill(*args, **kwargs):
        result = await real(*args, **kwargs)
        await data.store.async_patch_settings(kill_switch=True)
        return result

    provider = _Provider(_read("c1"), _answer("The lamp is on."))
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list",
                       return_value=[tool_dispatch._TOOL_DEFS_BY_NAME["get_state"]]), \
            patch.object(agent_turn, "_dispatch_mcp", dispatch_then_kill):
        answer = await agent_chat.async_run_voice_turn(
            hass, data, agent_turn._zero_inline_wait(key), provider, None, "http://h", "hi", max_iterations=4)
    # The state read before the switch never travels to the provider.
    assert provider.requests == 1
    assert answer == voice_text(None, "unavailable")


async def test_no_provider_round_follows_the_kill_switch_in_agent_chat(hass: HomeAssistant):
    data, key, _ = await _environment(hass)
    real = tool_dispatch._dispatch_mcp
    events: list[tuple[str, dict]] = []

    async def emit(name, payload):
        events.append((name, payload))

    async def dispatch_then_kill(*args, **kwargs):
        result = await real(*args, **kwargs)
        await data.store.async_patch_settings(kill_switch=True)
        return result

    provider = _Provider(_read("c1"), _answer("The lamp is on."))
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list",
                       return_value=[tool_dispatch._TOOL_DEFS_BY_NAME["get_state"]]), \
            patch.object(agent_turn, "_dispatch_mcp", dispatch_then_kill):
        await agent_chat.async_run_agent_turn(
            hass=hass, data=data, access_key=key, provider=provider, session=None,
            messages=[{"role": "user", "content": "hi"}], options={}, client_ip="agentcli",
            base_url="http://h", emit=emit, cancel=asyncio.Event(), max_iterations=4)
    assert provider.requests == 1
    assert not [p for n, p in events if n == "assistant_delta"]
    assert [p["code"] for n, p in events if n == "error"] == ["kill_switch"]


async def test_a_revoked_key_gets_no_further_agent_chat_round(hass: HomeAssistant):
    data, key, _ = await _environment(hass)
    real = tool_dispatch._dispatch_mcp
    events: list[tuple[str, dict]] = []

    async def emit(name, payload):
        events.append((name, payload))

    async def dispatch_then_revoke(*args, **kwargs):
        result = await real(*args, **kwargs)
        data.store.get_access_key_by_id(key.id).revoked = True
        return result

    provider = _Provider(_read("c1"), _answer("The lamp is on."))
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list",
                       return_value=[tool_dispatch._TOOL_DEFS_BY_NAME["get_state"]]), \
            patch.object(agent_turn, "_dispatch_mcp", dispatch_then_revoke):
        await agent_chat.async_run_agent_turn(
            hass=hass, data=data, access_key=key, provider=provider, session=None,
            messages=[{"role": "user", "content": "hi"}], options={}, client_ip="agentcli",
            base_url="http://h", emit=emit, cancel=asyncio.Event(), max_iterations=4)
    assert provider.requests == 1
    assert [p["code"] for n, p in events if n == "error"] == ["unauthorized"]


async def test_a_stuck_voice_call_ends_at_its_deadline(hass: HomeAssistant):
    data, key, _ = await _environment(hass)

    class _Stuck(_Provider):
        async def stream_turn(self, session, *, system_prompt, messages, tools, options):
            self.requests += 1
            while True:
                yield {"type": llm_base.EV_TEXT, "text": "."}
                await asyncio.sleep(0.01)

    with patch.object(agent_turn, "AGENT_HEADLESS_CALL_SECONDS", 0.2), \
            patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=[]):
        async with asyncio.timeout(5):
            answer = await agent_chat.async_run_voice_turn(
                hass, data, agent_turn._zero_inline_wait(key), _Stuck(), None, "http://h", "hi")
    assert answer == voice_text(None, "provider_slow")


async def test_every_surface_prompt_marks_tool_results_untrusted(hass: HomeAssistant):
    data, key, _ = await _environment(hass)
    chat, voice, task = _Provider(_answer("a")), _Provider(_answer("b")), _Provider(_answer("c"))
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=[]):
        await agent_chat.async_run_agent_turn(
            hass=hass, data=data, access_key=key, provider=chat, session=None,
            messages=[{"role": "user", "content": "hi"}], options={}, client_ip="agentcli",
            base_url="http://h", emit=AsyncMock(), cancel=asyncio.Event())
        await agent_chat.async_run_voice_turn(hass, data, agent_turn._zero_inline_wait(key), voice, None,
                                              "http://h", "hi")
        for structure in (None, {"type": "object"}):
            await agent_chat.async_run_ai_task(hass, data, agent_turn._zero_inline_wait(key), task, None,
                                               "http://h", "task", structure_json=structure)
            task.calls.append(_answer("{}"))
    for system in (*chat.systems, *voice.systems, *task.systems):
        assert agent_turn.UNTRUSTED_TOOL_DATA in system


async def test_the_wrappers_bind_the_turn_to_their_surface(hass: HomeAssistant):
    """Voice and AI Task pass a live binding check that follows their settings."""
    data, key, other = await _environment(hass)
    seen = {}

    async def capture_voice(*args, still_bound=None, **kwargs):
        seen["voice"] = still_bound
        return "ok"

    async def capture_task(*args, still_bound=None, **kwargs):
        seen["ai_task"] = still_bound
        return "ok"

    config = type("Cfg", (), {"model": "m"})()
    store = type("Store", (), {"resolve": lambda self, *a, **k: config})()
    await data.store.async_patch_settings(voice_agent_provider_id="p", ai_task_provider_id="p", ai_task_model="m")
    with patch_readers("custom_components.phoenix_mcp.llm.accounts._get_secret_store", AsyncMock(return_value=store)), \
            patch_readers("custom_components.phoenix_mcp.llm.factory.build_provider", return_value=_Provider()), \
            patch.object(voice_agent, "async_run_voice_turn", capture_voice), \
            patch.object(ai_task, "async_run_ai_task", capture_task):
        await voice_agent.async_voice_answer(hass, data, "hi")
        await ai_task.async_generate_ai_task_data(hass, data, "task", None)
    assert seen["voice"]() and seen["ai_task"]()
    await data.store.async_patch_settings(voice_agent_access_key_id=other.id, ai_task_enabled=False)
    assert not seen["voice"]() and not seen["ai_task"]()


async def test_an_agent_chat_turn_names_its_provider_and_model_on_its_audit_rows(hass: HomeAssistant):
    from types import SimpleNamespace
    from custom_components.phoenix_mcp.request_authority import acting_model

    data, key, _ = await _environment(hass)
    provider = _Provider(_read("c1"), _answer("The lamp is on."))
    provider.cfg = SimpleNamespace(kind="claude", model="claude-sonnet-5-5")
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list",
                       return_value=[tool_dispatch._TOOL_DEFS_BY_NAME["get_state"]]):
        await agent_chat.async_run_agent_turn(
            hass=hass, data=data, access_key=key, provider=provider, session=None,
            messages=[{"role": "user", "content": "hi"}], options={}, client_ip="agentcli",
            base_url="http://h", emit=AsyncMock(), cancel=asyncio.Event(), max_iterations=4)
    reads = [row for row in data.audit.query() if row.method == "get_state"]
    assert reads and all((row.provider, row.model) == ("claude", "claude-sonnet-5-5") for row in reads)
    assert acting_model.get() is None
