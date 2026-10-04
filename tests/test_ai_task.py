"""Tests for the Phoenix MCP AI Task entity (ai_task.py + agentcli.async_run_ai_task).

The installed HA provides the real AITaskEntity base. The missing-platform case
blocks its import in an isolated Phoenix module, so the feature-detection guard
is tested independently of what is installed.
"""
from __future__ import annotations

import builtins
import importlib.util
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.phoenix_mcp.agent_recovery import AgentRecovery
from custom_components.phoenix_mcp import ai_task, agentcli
from custom_components.phoenix_mcp.agentcli import AiTaskError, async_run_ai_task
from custom_components.phoenix_mcp.const import AGENTCLI_MAX_TURN_OUTPUT_BYTES, AI_TASK_CLIENT_IP, DOMAIN
from custom_components.phoenix_mcp.access_key_store import GlobalSettings
from homeassistant.exceptions import HomeAssistantError


class _MockProvider:
    def __init__(self):
        self.tool_result_batches: list[list[dict]] = []

    def format_tools(self, tools):
        return tools

    def append_assistant(self, messages, msg):
        messages.append(msg)

    def append_tool_results(self, messages, results):
        self.tool_result_batches.append(results)
        messages.append({"role": "user", "content": results})


def _staged_stream(*batches, capture=None):
    state = {"i": 0}

    async def gen(provider, session, **kwargs):
        if capture is not None:
            capture["system_prompt"] = kwargs.get("system_prompt")
        i = state["i"]
        state["i"] += 1
        batch = batches[i] if i < len(batches) else batches[-1]
        for ev in batch:
            yield ev

    return gen


def _tool_ev(name, tid, args=None):
    return {"type": agentcli.EV_TOOL, "name": name, "id": tid, "input": args or {}}


def _done(stop_reason):
    return {"type": agentcli.EV_DONE, "stop_reason": stop_reason,
            "assistant_msg": {"role": "assistant", "content": []}}


def _make_access_key():
    return SimpleNamespace(id="tok-1", announce_all_tools=True, confirm_inline_wait_seconds=0)


def _make_data(**settings_over):
    settings = GlobalSettings(**settings_over)
    store = MagicMock()
    store.get_settings.return_value = settings
    return SimpleNamespace(store=store, mesa=None, ready=True, shutting_down=False, agent_recovery=AgentRecovery())


_FULL = dict(
    ai_task_enabled=True, ai_task_access_key_id="tok-1",
    ai_task_provider_id="i1", ai_task_model="m",
)


# --------------------------------------------------------------------------- #
# module-level helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def unsupported_ai_task():
    """Exercise the real import fallback without reloading the shared module."""
    original_import = builtins.__import__
    blocked = []

    def without_ai_task(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "homeassistant.components" and "ai_task" in fromlist:
            blocked.append(name)
            raise ImportError("AI Task platform intentionally unavailable")
        return original_import(name, globals, locals, fromlist, level)

    spec = importlib.util.spec_from_file_location(
        "custom_components.phoenix_mcp._ai_task_missing_platform_test", ai_task.__file__,
    )
    module = importlib.util.module_from_spec(spec)
    with patch("builtins.__import__", side_effect=without_ai_task):
        spec.loader.exec_module(module)
    assert blocked == ["homeassistant.components"]
    return module


def test_supported_with_installed_ha_platform():
    from homeassistant.components.ai_task import AITaskEntity

    assert ai_task.ai_task_supported() is True
    assert issubclass(ai_task.PhoenixAITaskEntity, AITaskEntity)


def test_supported_is_false_when_platform_import_fails(unsupported_ai_task):
    assert unsupported_ai_task.ai_task_supported() is False
    assert unsupported_ai_task.PhoenixAITaskEntity is None
    assert callable(unsupported_ai_task.async_generate_ai_task_data)


# --------------------------------------------------------------------------- #
# async_sync_ai_task (dynamic entity lifecycle)
# --------------------------------------------------------------------------- #

def test_sync_noop_when_unsupported(unsupported_ai_task):
    data = _make_data(**_FULL)
    data.ai_task_entity = None
    add = MagicMock()
    unsupported_ai_task.async_sync_ai_task(MagicMock(), SimpleNamespace(entry_id="e"), data, add)
    add.assert_not_called()
    assert data.ai_task_entity is None


@pytest.mark.asyncio
async def test_setup_noop_when_platform_import_fails(unsupported_ai_task):
    hass = MagicMock()
    add = MagicMock()
    await unsupported_ai_task.async_setup_entry(hass, SimpleNamespace(entry_id="e"), add)
    add.assert_not_called()
    hass.data.get.assert_not_called()


def test_sync_creates_real_ha_entity_with_runtime_availability(hass):
    from homeassistant.components.ai_task import AITaskEntity, AITaskEntityFeature

    data = _make_data(**_FULL)
    data.ready = False
    data.ai_task_entity = None
    hass.data[DOMAIN] = data
    add = MagicMock()
    ai_task.async_sync_ai_task(hass, SimpleNamespace(entry_id="e"), data, add)
    entity = data.ai_task_entity
    assert isinstance(entity, AITaskEntity)
    assert entity.unique_id == "e_ai_task"
    assert entity.supported_features == AITaskEntityFeature.GENERATE_DATA
    add.assert_called_once_with([entity])
    assert entity.available is False
    data.ready = True
    assert entity.available is True
    data.store.get_settings.return_value = GlobalSettings(**_FULL, kill_switch=True)
    assert entity.available is False
    ai_task.async_sync_ai_task(hass, SimpleNamespace(entry_id="e"), data, add)
    assert data.ai_task_entity is entity
    add.assert_called_once()


def test_sync_adds_entity_when_fully_configured(hass):
    data = _make_data(**_FULL)
    data.ai_task_entity = None
    add = MagicMock()
    dummy = SimpleNamespace(entity_id="ai_task.phoenix_mcp_ai_task")
    with patch.object(ai_task, "_AI_TASK_AVAILABLE", True), \
         patch.object(ai_task, "PhoenixAITaskEntity", MagicMock(return_value=dummy)):
        ai_task.async_sync_ai_task(hass, SimpleNamespace(entry_id="e"), data, add)
    assert data.ai_task_entity is dummy
    add.assert_called_once_with([dummy])


def test_sync_adds_configured_entity_before_runtime_ready_on_restart(hass):
    """Platform setup precedes Phoenix's final ready publication on every restart."""
    data = _make_data(**_FULL)
    data.ready = False
    data.ai_task_entity = None
    add = MagicMock()
    dummy = SimpleNamespace(entity_id="ai_task.phoenix_mcp_ai_task")
    with patch.object(ai_task, "_AI_TASK_AVAILABLE", True), \
         patch.object(ai_task, "PhoenixAITaskEntity", MagicMock(return_value=dummy)):
        ai_task.async_sync_ai_task(hass, SimpleNamespace(entry_id="e"), data, add)
    assert data.ai_task_entity is dummy
    add.assert_called_once_with([dummy])


@pytest.mark.asyncio
async def test_sync_removes_registry_entry_when_not_configured(hass):
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    reg.async_get_or_create("ai_task", "phoenix_mcp", "e1_ai_task")
    assert reg.async_get_entity_id("ai_task", "phoenix_mcp", "e1_ai_task") is not None

    data = _make_data(ai_task_enabled=False)  # not fully configured, so it should not exist
    data.ai_task_entity = None
    with patch.object(ai_task, "_AI_TASK_AVAILABLE", True):
        ai_task.async_sync_ai_task(hass, SimpleNamespace(entry_id="e1"), data, MagicMock())
    # The registry entry (and thus the live entity + picker option) is gone.
    assert reg.async_get_entity_id("ai_task", "phoenix_mcp", "e1_ai_task") is None
    assert data.ai_task_entity is None


# --------------------------------------------------------------------------- #
# AI Task "default data-gen entity" preference (one-click)
# --------------------------------------------------------------------------- #

class _FakePrefs:
    """Stand-in for HA's AITaskPreferences."""

    def __init__(self, initial=None):
        self.gen_data_entity_id = initial

    def async_set_preferences(self, *, gen_data_entity_id):
        self.gen_data_entity_id = gen_data_entity_id


def test_preferred_status_without_preferences(hass):
    # The platform imports, but HA's preferences store has not been set up.
    status = ai_task.ai_task_preferred_status(hass, SimpleNamespace(entry_id="e"))
    assert status["supported"] is False
    assert status["is_preferred"] is False


def test_set_preferred_requires_entity(hass):
    # No Phoenix MCP AI Task entity registered: setup error before touching prefs.
    with pytest.raises(ai_task.AiTaskSetupError):
        ai_task.set_ai_task_preferred(hass, SimpleNamespace(entry_id="nope"))


async def test_set_preferred_updates_prefs_and_status(hass, phoenix_ai_task_platform):
    platform, entry = phoenix_ai_task_platform
    data = _make_data(**_FULL)
    data.ai_task_entity = None
    hass.data[DOMAIN] = data
    await ai_task.async_setup_entry(hass, entry, platform._async_schedule_add_entities_for_entry)
    await hass.async_block_till_done()
    ent = data.ai_task_entity
    fake = _FakePrefs()
    with patch.object(ai_task, "_AI_TASK_AVAILABLE", True), \
         patch.object(ai_task, "_ai_task_preferences", return_value=fake):
        status = ai_task.set_ai_task_preferred(hass, entry)
    assert fake.gen_data_entity_id == ent.entity_id
    assert status["is_preferred"] is True
    assert status["entity_id"] == ent.entity_id


def test_preferred_rejects_registry_only_entity(hass, phoenix_ai_task_platform):
    """A restored registry record is not a loaded AI Task picker option."""
    from homeassistant.helpers import entity_registry as er

    ent = er.async_get(hass).async_get_or_create("ai_task", DOMAIN, "e1_ai_task")
    hass.states.async_set(ent.entity_id, "unavailable", {"restored": True})
    fake = _FakePrefs(ent.entity_id)
    entry = SimpleNamespace(entry_id="e1")
    with patch.object(ai_task, "_ai_task_preferences", return_value=fake):
        status = ai_task.ai_task_preferred_status(hass, entry)
        assert status["entity_id"] is None
        assert status["is_preferred"] is False
        with pytest.raises(ai_task.AiTaskSetupError):
            ai_task.set_ai_task_preferred(hass, entry)
        assert fake.gen_data_entity_id == ent.entity_id
        ai_task.clear_ai_task_preferred(hass, entry)
    assert fake.gen_data_entity_id is None


async def test_sync_publishes_runtime_availability(hass, phoenix_ai_task_platform):
    """Non-polling entities need a state write after readiness and kill-switch changes."""
    platform, entry = phoenix_ai_task_platform
    data = _make_data(**_FULL)
    data.ready = False
    data.ai_task_entity = None
    hass.data[DOMAIN] = data
    await ai_task.async_setup_entry(hass, entry, platform._async_schedule_add_entities_for_entry)
    await hass.async_block_till_done()
    entity = data.ai_task_entity
    assert hass.states.get(entity.entity_id).state == "unavailable"
    for ready, kill_switch, expected in [
        (True, False, "unknown"), (True, True, "unavailable"), (True, False, "unknown"),
    ]:
        data.ready = ready
        data.store.get_settings.return_value = GlobalSettings(**_FULL, kill_switch=kill_switch)
        data.async_sync_ai_task()
        assert data.ai_task_entity is entity
        assert hass.states.get(entity.entity_id).state == expected


async def test_live_disabled_ai_task_is_not_refreshed_or_readded(hass, phoenix_ai_task_platform, caplog):
    from homeassistant.helpers import entity_registry as er

    platform, entry = phoenix_ai_task_platform
    data = _make_data(**_FULL)
    data.ai_task_entity = None
    hass.data[DOMAIN] = data
    await ai_task.async_setup_entry(hass, entry, platform._async_schedule_add_entities_for_entry)
    await hass.async_block_till_done()
    entity_id = data.ai_task_entity.entity_id
    er.async_get(hass).async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    caplog.clear()
    data.async_sync_ai_task()
    data.async_sync_ai_task()
    assert data.ai_task_entity is None
    assert hass.states.get(entity_id) is None
    assert "incorrectly being triggered" not in caplog.text


def test_sync_drops_entity_after_ha_aborts_registration(hass):
    data = _make_data(**_FULL)
    hass.data[DOMAIN] = data
    entry = SimpleNamespace(entry_id="aborted")
    entity = ai_task.PhoenixAITaskEntity(hass, entry)
    entity.entity_id = "ai_task.aborted"
    entity.add_to_platform_abort()
    data.ai_task_entity = entity
    add = MagicMock()
    ai_task.async_sync_ai_task(hass, entry, data, add)
    assert data.ai_task_entity is None
    add.assert_not_called()


def test_clear_preferred_resets_when_ours(hass):
    from homeassistant.helpers import entity_registry as er

    ent = er.async_get(hass).async_get_or_create("ai_task", "phoenix_mcp", "e1_ai_task")
    fake = _FakePrefs(ent.entity_id)
    with patch.object(ai_task, "_AI_TASK_AVAILABLE", True), \
         patch.object(ai_task, "_ai_task_preferences", return_value=fake):
        status = ai_task.clear_ai_task_preferred(hass, SimpleNamespace(entry_id="e1"))
    assert fake.gen_data_entity_id is None
    assert status["is_preferred"] is False


def test_sync_clears_preference_when_removing_preferred_entity(hass):
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    ent = reg.async_get_or_create("ai_task", "phoenix_mcp", "e1_ai_task")
    fake = _FakePrefs(ent.entity_id)  # Phoenix MCP is HA's default data-gen entity
    data = _make_data(ai_task_enabled=False)  # not configured, so the entity is removed
    data.ai_task_entity = None
    with patch.object(ai_task, "_AI_TASK_AVAILABLE", True), \
         patch.object(ai_task, "_ai_task_preferences", return_value=fake):
        ai_task.async_sync_ai_task(hass, SimpleNamespace(entry_id="e1"), data, MagicMock())
    assert reg.async_get_entity_id("ai_task", "phoenix_mcp", "e1_ai_task") is None
    assert fake.gen_data_entity_id is None  # dangling default cleared on teardown


def test_is_fully_configured():
    assert ai_task._is_fully_configured(GlobalSettings(**_FULL)) is True
    assert ai_task._is_fully_configured(GlobalSettings(ai_task_enabled=True)) is False
    assert ai_task._is_fully_configured(GlobalSettings(**{**_FULL, "ai_task_enabled": False})) is False


def test_structure_to_json_roundtrips_and_none():
    assert ai_task._structure_to_json(None) is None
    schema = vol.Schema({vol.Required("summary"): str, vol.Optional("count"): int})
    out = ai_task._structure_to_json(schema)
    assert out["type"] == "object"
    assert "summary" in out["properties"] and out["required"] == ["summary"]


def test_structure_to_json_supports_home_assistant_selector_schema():
    from homeassistant.helpers import selector

    schema = vol.Schema(
        {
            vol.Required("scene_name", description="Name of the example scene"): (
                selector.TextSelector({})
            ),
            vol.Required("purpose", description="One-sentence purpose of the scene"): (
                selector.TextSelector({})
            ),
            vol.Optional("priority"): selector.NumberSelector({"min": 1, "max": 5}),
        },
        extra=vol.PREVENT_EXTRA,
    )

    assert ai_task._structure_to_json(schema) == {
        "type": "object",
        "properties": {
            "scene_name": {
                "type": "string",
                "description": "Name of the example scene",
            },
            "purpose": {
                "type": "string",
                "description": "One-sentence purpose of the scene",
            },
            "priority": {
                "type": "number",
                "minimum": 1.0,
                "maximum": 5.0,
                "multipleOf": 1,
            },
        },
        "additionalProperties": False,
        "required": ["scene_name", "purpose"],
    }


def test_parse_structured_plain_and_fenced_and_bad():
    assert ai_task._parse_structured('{"a": 1}') == {"a": 1}
    assert ai_task._parse_structured('```json\n{"a": 2}\n```') == {"a": 2}
    assert ai_task._parse_structured('```\n{"a": 3}\n```') == {"a": 3}
    with pytest.raises(HomeAssistantError):
        ai_task._parse_structured("not json at all")


# --------------------------------------------------------------------------- #
# async_generate_ai_task_data
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ai_task_raises_when_the_provider_blows_the_output_budget():
    """The output ceiling is a failure here, not a degraded answer.

    Voice degrades to a spoken sentence because someone is listening; an automation
    asked for data, so the only honest outcome is an error the caller turns into a
    HomeAssistantError. Shares AGENTCLI_MAX_TURN_OUTPUT_BYTES with the interactive
    loop.
    """
    access_key = _make_access_key()
    data = _make_data()
    provider = _MockProvider()
    huge = {"role": "assistant", "content": "x" * (AGENTCLI_MAX_TURN_OUTPUT_BYTES + 1)}
    stream = _staged_stream([{"type": agentcli.EV_DONE, "stop_reason": "tool_use",
                              "assistant_msg": huge}])
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"), \
         pytest.raises(AiTaskError, match="too much output"):
        await async_run_ai_task(
            MagicMock(), data, access_key, provider, MagicMock(), "http://h", "summarize")

    # Rejected before the append, so it never entered the re-sent transcript.
    assert provider.tool_result_batches == []


@pytest.mark.asyncio
async def test_ai_task_raises_when_it_runs_out_of_steps():
    """Running out of steps raises instead of masquerading as a formatting problem.

    The round that exhausts the budget is a tool round, which leaves final_text
    empty. With a structure that must not be reported as "could not parse structured
    output" (blaming the model for JSON it never got to write and pointing the
    operator at the prompt instead of Steps before check-in); without one it must not
    return an empty string to the automation.
    """
    access_key = _make_access_key()
    data = _make_data()
    provider = _MockProvider()
    stream = _staged_stream([_tool_ev("get_state", "t1"), _done("tool_use")])
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch.object(agentcli, "_current_dispatch_access_key", return_value=access_key), \
         patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=_dispatch_ok("on")), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"), \
         pytest.raises(AiTaskError, match="ran out of steps"):
        await async_run_ai_task(
            MagicMock(), data, access_key, provider, MagicMock(), "http://h", "summarize",
            max_iterations=3)


@pytest.mark.asyncio
async def test_ai_task_raises_on_a_truncated_result():
    """A truncated result raises instead of being returned.

    An automation would store half a result as a complete one. With a structure it
    must not surface as a JSON parse error (the wrong cause), and without one it
    must not return a half-written answer that looks complete.
    """
    access_key = _make_access_key()
    data = _make_data()
    provider = _MockProvider()
    stream = _staged_stream([
        {"type": agentcli.EV_TEXT, "text": '{"summary": "the kitchen'},
        {"type": agentcli.EV_DONE, "stop_reason": "max_tokens",
         "assistant_msg": {"role": "assistant", "content": []}},
    ])
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"), \
         pytest.raises(AiTaskError, match="output length limit"):
        await async_run_ai_task(
            MagicMock(), data, access_key, provider, MagicMock(), "http://h", "summarize")


@pytest.mark.asyncio
async def test_ai_task_stops_after_one_round_when_authority_is_lost():
    """A dead access key buys one closing round here too, not max_iterations of them."""
    access_key = _make_access_key()
    data = _make_data()
    provider = _MockProvider()
    dispatch = _dispatch_ok("should not run")
    stream = _staged_stream([_tool_ev("call_service", "t1"), _done("tool_use")])
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch.object(agentcli, "_current_dispatch_access_key", return_value=None), \
         patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=dispatch), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"):
        with pytest.raises(AiTaskError):
            await async_run_ai_task(
                MagicMock(), data, access_key, provider, MagicMock(), "http://h", "x",
                max_iterations=20)

    assert dispatch.calls == []
    assert len(provider.tool_result_batches) == 1


@pytest.mark.asyncio
async def test_generate_free_text_happy_path():
    data = _make_data(**_FULL)
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model="m")
    with patch.object(agentcli, "_current_dispatch_access_key", return_value=_make_access_key()), \
         patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=store)), \
         patch.object(agentcli, "build_provider", return_value=_MockProvider()), \
         patch.object(agentcli, "async_run_ai_task", AsyncMock(return_value="A plain summary.")):
        result = await ai_task.async_generate_ai_task_data(MagicMock(), data, "summarize", None)
    assert result == "A plain summary."


@pytest.mark.asyncio
async def test_generate_structured_parses_json():
    data = _make_data(**_FULL)
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model="m")
    schema = vol.Schema({vol.Required("summary"): str})
    with patch.object(agentcli, "_current_dispatch_access_key", return_value=_make_access_key()), \
         patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=store)), \
         patch.object(agentcli, "build_provider", return_value=_MockProvider()), \
         patch.object(agentcli, "async_run_ai_task", AsyncMock(return_value='{"summary": "ok"}')) as rt:
        result = await ai_task.async_generate_ai_task_data(MagicMock(), data, "do it", schema)
    assert result == {"summary": "ok"}
    # structure_json was passed to the loop (so the model was told to emit JSON).
    assert rt.call_args.kwargs["structure_json"]["type"] == "object"


@pytest.mark.asyncio
async def test_generate_disabled_raises():
    data = _make_data(ai_task_enabled=False)
    with pytest.raises(HomeAssistantError, match="not fully configured"):
        await ai_task.async_generate_ai_task_data(MagicMock(), data, "x", None)


@pytest.mark.asyncio
async def test_generate_kill_switch_raises_unavailable():
    data = _make_data(kill_switch=True, **_FULL)
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await ai_task.async_generate_ai_task_data(MagicMock(), data, "x", None)


@pytest.mark.asyncio
async def test_generate_access_key_gone_raises():
    data = _make_data(**_FULL)
    with patch.object(agentcli, "_current_dispatch_access_key", return_value=None):
        with pytest.raises(HomeAssistantError, match="access key"):
            await ai_task.async_generate_ai_task_data(MagicMock(), data, "x", None)


@pytest.mark.asyncio
async def test_generate_wraps_loop_error():
    data = _make_data(**_FULL)
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model="m")
    with patch.object(agentcli, "_current_dispatch_access_key", return_value=_make_access_key()), \
         patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=store)), \
         patch.object(agentcli, "build_provider", return_value=_MockProvider()), \
         patch.object(agentcli, "async_run_ai_task", AsyncMock(side_effect=AiTaskError("model down"))):
        with pytest.raises(HomeAssistantError, match="model down"):
            await ai_task.async_generate_ai_task_data(MagicMock(), data, "x", None)


# --------------------------------------------------------------------------- #
# async_run_ai_task (the gated loop)
# --------------------------------------------------------------------------- #

def _dispatch_ok(text):
    async def fake(method, msg_id, params, tok, hass, data, client_ip, base_url, *a, **k):
        fake.calls.append({"client_ip": client_ip, "name": params["name"]})
        return ({"result": {"content": [{"type": "text", "text": text}]}}, "n", "r", "allowed")
    fake.calls = []
    return fake


@pytest.mark.asyncio
async def test_run_ai_task_tool_then_answer_uses_ai_task_sentinel():
    access_key = _make_access_key()
    data = _make_data()
    provider = _MockProvider()
    dispatch = _dispatch_ok('{"state": "on"}')
    stream = _staged_stream(
        [_tool_ev("get_state", "t1", {"entity_id": "light.x"}), _done("tool_use")],
        [{"type": agentcli.EV_TEXT, "text": "final answer"}, _done("end_turn")],
    )
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch.object(agentcli, "_current_dispatch_access_key", return_value=access_key), \
         patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=dispatch), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"):
        text = await async_run_ai_task(MagicMock(), data, access_key, provider, MagicMock(), "http://h", "instr")
    assert text == "final answer"
    assert dispatch.calls == [{"client_ip": AI_TASK_CLIENT_IP, "name": "get_state"}]


@pytest.mark.asyncio
async def test_run_ai_task_includes_structure_directive_in_system_prompt():
    access_key = _make_access_key()
    data = _make_data()
    capture: dict = {}
    stream = _staged_stream(
        [{"type": agentcli.EV_TEXT, "text": '{"a": 1}'}, _done("end_turn")],
        capture=capture,
    )
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"):
        text = await async_run_ai_task(
            MagicMock(), data, access_key, _MockProvider(), MagicMock(), "http://h", "instr",
            structure_json={"type": "object", "properties": {"a": {"type": "integer"}}},
        )
    assert text == '{"a": 1}'
    assert "JSON schema" in capture["system_prompt"]
    assert '"type": "object"' in capture["system_prompt"]
    assert "Conversation presentation policy" not in capture["system_prompt"]
    assert "matter-of-fact" not in capture["system_prompt"]


@pytest.mark.asyncio
async def test_run_ai_task_free_text_uses_selected_style_and_detail():
    access_key = _make_access_key()
    data = _make_data(
        ai_task_conversation_style="technical", ai_task_detail_level="detailed",
    )
    capture: dict = {}
    stream = _staged_stream(
        [{"type": agentcli.EV_TEXT, "text": "result"}, _done("end_turn")],
        capture=capture,
    )
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"):
        text = await async_run_ai_task(
            MagicMock(), data, access_key, _MockProvider(), MagicMock(), "http://h", "instr",
        )
    assert text == "result"
    assert "precise terminology" in capture["system_prompt"]
    assert "thorough reasoning" in capture["system_prompt"]
    assert "AI Task free-text output contract" in capture["system_prompt"]
    assert "Do not mention tools, available entities" in capture["system_prompt"]
    assert agentcli._HOME_FOCUS_INSTRUCTION not in capture["system_prompt"]


@pytest.mark.asyncio
async def test_run_ai_task_pending_becomes_queued_without_blocking():
    access_key = _make_access_key()
    data = _make_data()
    provider = _MockProvider()
    record = SimpleNamespace(
        id="ap1", status="pending", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        access_key_id=access_key.id, tool_name="call_service", request_id="",
    )

    async def dispatch_pending(method, msg_id, params, *a, **k):
        record.request_id = k["request_id"]
        return ({"result": {"content": [{"type": "text", "text": '{"status":"pending_approval","approval_id":"ap1"}'}]}}, "n", "r", "pending_approval")

    stream = _staged_stream(
        [_tool_ev("call_service", "t1"), _done("tool_use")],
        [{"type": agentcli.EV_TEXT, "text": "queued"}, _done("end_turn")],
    )
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch.object(agentcli, "_current_dispatch_access_key", return_value=access_key), \
         patch.object(agentcli, "get_approval", return_value=record), \
         patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=dispatch_pending), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"):
        text = await async_run_ai_task(MagicMock(), data, access_key, provider, MagicMock(), "http://h", "unlock")
    assert text == "queued"
    fed_back = provider.tool_result_batches[0][0]["result_text"]
    assert "queued_for_approval" in fed_back


@pytest.mark.asyncio
async def test_run_ai_task_error_raises_aitaskerror():
    access_key = _make_access_key()
    data = _make_data()
    stream = _staged_stream([{"type": agentcli.EV_ERROR, "message": "boom"}])
    with patch.object(agentcli, "_stream_turn_resilient", stream), \
         patch.object(agentcli, "build_mcp_tool_list", return_value=[]), \
         patch("custom_components.phoenix_mcp.mcp_view._build_instructions", return_value="SYS"):
        with pytest.raises(AiTaskError, match="boom"):
            await async_run_ai_task(MagicMock(), data, access_key, _MockProvider(), MagicMock(), "http://h", "hi")


def test_probatio_structure_serialization_does_not_import_legacy_converter(monkeypatch):
    import sys

    if not vol.Schema.__module__.startswith("probatio."):
        pytest.skip("The installed HA uses the original Voluptuous engine")
    monkeypatch.setitem(sys.modules, "voluptuous_openapi", None)
    schema = vol.Schema({vol.Required("count"): int})
    result = ai_task._structure_to_json(schema)
    assert result["properties"]["count"]["type"] == "integer"
    assert result["required"] == ["count"]
