"""Local routing observations use real HA recognition and never perform effects."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hassil.models import MatchEntity
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.command_routing import async_evaluate_command
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree


@pytest.fixture
async def routing(hass, token_store):
    token, _ = await token_store.async_create_token("routing", "test")
    token.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    return data, token


def recognition(intent="HassTurnOn", **slots):
    return SimpleNamespace(intent=SimpleNamespace(name=intent),
                           entities={k: MatchEntity(k, v, str(v)) for k, v in slots.items()},
                           unmatched_entities={}, intent_metadata={}, response="one")


@pytest.fixture
async def english_home(hass, routing):
    area = ar.async_get(hass).async_create("Kitchen")
    entry = er.async_get(hass).async_get_or_create("light", "test", "kitchen-lamp", original_name="Kitchen lamp")
    er.async_get(hass).async_update_entity(entry.entity_id, area_id=area.id, aliases={"Kitchen lamp", "Desk lamp"})
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Kitchen lamp", "supported_color_modes": ["brightness"]})
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    return entry.entity_id


@pytest.mark.parametrize("sentence,intent", [
    ("turn on the kitchen lamp", "HassTurnOn"),
    ("turn off the desk lamp", "HassTurnOff"),
    ("turn on the lights in the kitchen", "HassTurnOn"),
    ("set the kitchen lamp brightness to 50 percent", "HassLightSet"),
    ("what is the state of the kitchen lamp", "HassGetState"),
])
async def test_real_ha_candidates_have_no_effect(hass, routing, english_home, sentence, intent):
    data, token = routing
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        result = await async_evaluate_command(hass, data, token, sentence)
    assert result["route"] == "local_candidate", result
    assert result["proposal"]["intent"] == intent
    assert result["proposal"]["entity_ids"] == [english_home]
    assert result["executed"] is False
    call.assert_not_called()
    assert data.store.get_pending_approvals() == []


@pytest.mark.parametrize("sentence", [
    "do not turn on the kitchen lamp", 'say "turn on the kitchen lamp"',
    "turn on the kitchen lamp in ten minutes", "turn on the kitchen lamp and turn off the desk lamp",
    "why is the kitchen lamp off", "turn on the missing lamp", "turn on everything",
])
async def test_real_ha_unsupported_sentences_fall_back(hass, routing, english_home, sentence):
    data, token = routing
    result = await async_evaluate_command(hass, data, token, sentence)
    assert result["route"] == "provider", result
    assert result["proposal"] is None


@pytest.mark.parametrize("modes,expected", [
    (["onoff"], "unsupported"), (None, "unsupported"), ([], "unsupported"),
    ("brightness", "unsupported"), ({"brightness": True}, "unsupported"),
    ([None], "unsupported"), (["future_mode"], "unsupported"),
    (["brightness"], "supported"), (["color_temp"], "supported"),
    (["hs"], "supported"), (["rgbw"], "supported"),
])
async def test_brightness_candidate_requires_device_support(hass, routing, english_home, modes, expected):
    data, token = routing
    state = hass.states.get(english_home)
    attributes = dict(state.attributes)
    if modes is None:
        attributes.pop("supported_color_modes")
    else:
        attributes["supported_color_modes"] = modes
    hass.states.async_set(english_home, "off", attributes)
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        result = await async_evaluate_command(hass, data, token, "set the kitchen lamp brightness to 50 percent")
    assert result["reason"] == expected, result
    assert bool(result["proposal"]) == (expected == "supported")
    call.assert_not_called()
    # A non-dimmable light can still be a valid on/off target.
    assert (await async_evaluate_command(hass, data, token, "turn on the kitchen lamp"))["reason"] == "supported"


async def test_area_brightness_requires_every_target_to_support_it(hass, routing, english_home):
    data, token = routing
    registry = er.async_get(hass)
    other = registry.async_get_or_create("light", "test", "nondimmable")
    registry.async_update_entity(other.entity_id, area_id=registry.async_get(english_home).area_id)
    hass.states.async_set(other.entity_id, "off", {"supported_color_modes": ["onoff"]})
    result = await async_evaluate_command(hass, data, token, "set the brightness in the kitchen to 50 percent")
    assert result["reason"] == "unsupported", result
    assert result["proposal"] is None
    hass.states.async_set(other.entity_id, "off", {"supported_color_modes": ["brightness"]})
    result = await async_evaluate_command(hass, data, token, "set the brightness in the kitchen to 50 percent")
    assert result["reason"] == "supported", result
    assert result["proposal"]["entity_ids"] == sorted([english_home, other.entity_id])


async def test_hidden_and_missing_targets_are_indistinguishable(hass, routing, english_home):
    data, token = routing
    token.permissions = PermissionTree()
    for name in ("Kitchen lamp", "Missing"):
        with patch("custom_components.phoenix_mcp.command_routing.async_recognize", AsyncMock(return_value=recognition(name=name))):
            result = await async_evaluate_command(hass, data, token, "turn on the lamp")
        assert result["reason"] == "no_target"
        assert result["proposal"] is None


@pytest.mark.parametrize("change", ["scope", "rotate", "revoke", "kill", "settings", "runtime"])
async def test_authority_is_checked_after_recognition(hass, routing, english_home, change):
    data, token = routing
    async def recognize(*args, **kwargs):
        if change == "scope":
            token.settings_version += 1
        elif change == "rotate":
            token.token_hash = "replacement"
        elif change == "revoke":
            token.revoked = True
        elif change == "kill":
            data.store.get_settings().kill_switch = True
        elif change == "settings":
            await data.store.async_patch_settings(command_routing_timeout_ms=1000)
        else:
            hass.data[DOMAIN] = object()
        return recognition(name="Kitchen lamp")
    with patch("custom_components.phoenix_mcp.command_routing.async_recognize", recognize):
        result = await async_evaluate_command(hass, data, token, "turn on the kitchen lamp")
    assert result["reason"] == "authority_changed"
    assert result["proposal"] is None


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_keep_worker_capacity(hass, routing, cancel):
    data, token = routing
    await data.store.async_patch_settings(command_routing_timeout_ms=50)
    started, finish = asyncio.Event(), asyncio.Event()
    async def recognize(*args, **kwargs):
        started.set()
        await finish.wait()
        return None
    with patch("custom_components.phoenix_mcp.command_routing.async_recognize", recognize):
        caller = asyncio.create_task(async_evaluate_command(hass, data, token, "turn on the lamp"))
        await started.wait()
        if cancel:
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        else:
            assert (await caller)["reason"] == "timeout"
        assert not data.command_routing.task.done()
        assert (await async_evaluate_command(hass, data, token, "turn on the lamp"))["reason"] == "busy"
        finish.set()
        await data.command_routing.task
        assert (await async_evaluate_command(hass, data, token, "turn on the lamp"))["reason"] == "no_match"


@pytest.mark.parametrize("language", ["fr", "zh", "", None, []])
async def test_non_english_never_calls_recognizer(hass, routing, language):
    data, token = routing
    with patch("custom_components.phoenix_mcp.command_routing.async_recognize") as recognize:
        assert (await async_evaluate_command(hass, data, token, "hello", language))["reason"] == "unsupported_language"
    recognize.assert_not_called()

@pytest.mark.parametrize("intent,slots", [
    ("HassLightSet", {"name": "Desk lamp", "brightness": "50"}),
    ("HassLightSet", {"name": "Desk lamp", "brightness": float("nan")}),
    ("HassLightSet", {"name": "Desk lamp", "brightness": True}),
    ("HassLightSet", {"name": "Desk lamp", "brightness": 101}),
    ("HassLightSet", {"name": "Desk lamp", "color": "red"}),
    ("HassGetState", {"name": "Desk lamp", "state": "on"}),
    ("HassTurnOn", {"domain": "light"}),
    ("HassTurnOn", {"name": "all", "domain": "light"}),
    ("HassTurnOn", {"name": "Desk lamp", "timer": 5}),
])
async def test_unsupported_slots_are_never_ignored(hass, routing, english_home, intent, slots):
    data, token = routing
    with patch("custom_components.phoenix_mcp.command_routing.async_recognize", AsyncMock(return_value=recognition(intent, **slots))):
        result = await async_evaluate_command(hass, data, token, "test")
    assert result["proposal"] is None


@pytest.mark.parametrize("change", ["read_only", "unavailable", "hidden", "custom", "duplicate"])
async def test_uncertain_targets_do_not_become_candidates(hass, routing, english_home, change):
    data, token = routing
    result = recognition(name="Desk lamp")
    if change == "read_only":
        token.permissions.domains["light"].state = "YELLOW"
    elif change == "unavailable":
        hass.states.async_set(english_home, "unavailable")
    elif change == "hidden":
        from homeassistant.helpers.entity_registry import RegistryEntryHider
        er.async_get(hass).async_update_entity(english_home, hidden_by=RegistryEntryHider.USER)
    elif change == "duplicate":
        other = er.async_get(hass).async_get_or_create("light", "test", "other")
        er.async_get(hass).async_update_entity(other.entity_id, aliases={"Desk lamp"})
        hass.states.async_set(other.entity_id, "off")
    else:
        result.intent_metadata = {"hass_custom_sentence": True}
    with patch("custom_components.phoenix_mcp.command_routing.async_recognize", AsyncMock(return_value=result)):
        assert (await async_evaluate_command(hass, data, token, "test"))["proposal"] is None


async def test_voice_shadow_preserves_normal_provider_turn(hass, routing, english_home):
    from custom_components.phoenix_mcp import agentcli, voice_agent
    data, token = routing
    await data.store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id="p", voice_agent_model="model", voice_agent_routing_mode="local_shadow")
    provider_store = MagicMock()
    provider_store.resolve.return_value = SimpleNamespace(model="model")
    normal_turn = AsyncMock(return_value="Normal provider answer")
    with (patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=provider_store)),
          patch.object(agentcli, "build_provider", return_value=MagicMock()),
          patch.object(agentcli, "async_run_voice_turn", normal_turn)):
        text = await voice_agent.async_voice_answer(hass, data, "turn on the desk lamp", language="en")
    assert text == "Normal provider answer"
    normal_turn.assert_awaited_once()
    assert normal_turn.await_args.args[6] == "turn on the desk lamp"
    assert normal_turn.await_args.args[2].confirm_inline_wait_seconds == 0
    assert data.command_routing.observations == 1
    assert data.command_routing.candidates == 1
    assert data.command_routing.task is None
    assert data.store.get_pending_approvals() == []


async def test_voice_off_skips_recognition(hass, routing):
    from custom_components.phoenix_mcp import agentcli, voice_agent
    data, token = routing
    await data.store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id="p", voice_agent_model="model")
    provider_store = MagicMock()
    provider_store.resolve.return_value = SimpleNamespace(model="model")
    with (patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=provider_store)),
          patch.object(agentcli, "build_provider", return_value=MagicMock()),
          patch.object(agentcli, "async_run_voice_turn", AsyncMock(return_value="answer")),
          patch("custom_components.phoenix_mcp.command_routing.async_recognize") as recognize):
        assert await voice_agent.async_voice_answer(hass, data, "hello") == "answer"
    recognize.assert_not_called()

async def test_voice_does_not_contact_provider_after_authority_changes(hass, routing):
    from custom_components.phoenix_mcp import agentcli, voice_agent
    data, token = routing
    await data.store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id="p", voice_agent_model="model", voice_agent_routing_mode="local_shadow")
    provider_store = MagicMock()
    provider_store.resolve.return_value = SimpleNamespace(model="model")
    async def recognize(*args, **kwargs):
        token.revoked = True
        return recognition(name="Desk lamp")
    with (patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=provider_store)),
          patch.object(agentcli, "build_provider") as build,
          patch("custom_components.phoenix_mcp.command_routing.async_recognize", recognize)):
        await voice_agent.async_voice_answer(hass, data, "turn on the desk lamp", language="en")
    build.assert_not_called()
    assert data.command_routing.observations == 0


async def test_voice_rechecks_settings_after_loading_provider_config(hass, routing):
    from custom_components.phoenix_mcp import agentcli, voice_agent
    data, token = routing
    await data.store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id="p", voice_agent_model="model", voice_agent_routing_mode="local_shadow")
    async def provider_store(_hass):
        await data.store.async_patch_settings(voice_agent_routing_mode="off")
        store = MagicMock()
        store.resolve.return_value = SimpleNamespace(model="model")
        return store
    with (patch.object(agentcli, "_get_secret_store", provider_store),
          patch.object(agentcli, "build_provider") as build,
          patch("custom_components.phoenix_mcp.command_routing.async_recognize") as recognize):
        await voice_agent.async_voice_answer(hass, data, "hello", language="en")
    build.assert_not_called()
    recognize.assert_not_called()


async def test_routing_settings_persist_and_active_modes_fail_closed(token_store):
    from custom_components.phoenix_mcp.token_store import GlobalSettings
    await token_store.async_patch_settings(voice_agent_routing_mode="local_shadow", command_routing_timeout_ms=1700)
    restored = GlobalSettings.from_dict(token_store.get_settings().to_dict())
    assert restored.voice_agent_routing_mode == "local_shadow"
    assert restored.command_routing_timeout_ms == 1700
    assert GlobalSettings.from_dict({}).voice_agent_routing_mode == "off"
    assert GlobalSettings.from_dict({"voice_agent_routing_mode": "hybrid"}).voice_agent_routing_mode == "off"


@pytest.mark.parametrize("exposed,sentence,expected", [
    (True, "turn on the desk lamp", "supported"),
    (False, "turn on the desk lamp", "no_match"),
    (True, "turn on light.test_kitchen_lamp", "no_match"),
    (True, "turn on the lamp desk", "no_match"),
])
async def test_strict_recognition_uses_exposed_exact_aliases(
    hass, routing, english_home, exposed, sentence, expected,
):
    """Separate recognition vocabulary from the token's unchanged write scope."""
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    data, token = routing
    async_expose_entity(hass, "conversation", english_home, exposed)
    await hass.async_block_till_done()
    result = await async_evaluate_command(hass, data, token, sentence)
    assert result["reason"] == expected, result
    if expected == "supported":
        assert result["proposal"]["entity_ids"] == [english_home]
    else:
        assert result["proposal"] is None
