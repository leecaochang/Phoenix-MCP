"""Numeric routing preserves HA values through real recognition and dispatch."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli, mcp_view, voice_agent
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.command_operations import numeric_operation, service_arguments
from custom_components.phoenix_mcp.command_routing import async_evaluate_command
from custom_components.phoenix_mcp.const import DOMAIN, COMMAND_ROUTING_PLAN_KEY
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_provider import DecisionProvider
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree


@pytest.fixture(params=[
    ("light", "Desk lamp", "HassLightSet", "brightness_pct", "turn_on", "brightness_pct",
     {"supported_color_modes": ["brightness"]}, "set the desk lamp brightness to {value} percent"),
    ("media_player", "Desk speaker", "HassSetVolume", "volume_pct", "volume_set", "volume_level",
     {"supported_features": MediaPlayerEntityFeature.VOLUME_SET}, "set the desk speaker volume to {value} percent"),
    ("cover", "Desk blind", "HassSetPosition", "position_pct", "set_cover_position", "position",
     {"supported_features": CoverEntityFeature.SET_POSITION, "device_class": "blind"}, "set the desk blind to {value} percent"),
])
async def numeric_home(hass, token_store, request):
    domain, name, intent, field, service, service_field, attributes, sentence = request.param
    token, _ = await token_store.async_create_token("numeric", "test", rate_limit_requests=0)
    token.permissions = PermissionTree(domains={domain: PermissionNode(state="GREEN")})
    token.cap_physical_control = "allow"
    data = PhoenixData(store=token_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    await token_store.async_patch_settings(
        voice_agent_enabled=True, voice_agent_token_id=token.id, voice_agent_provider_id="p",
        voice_agent_model="model", voice_agent_routing_mode="auto", agentcli_routing_mode="auto", mesa_mode="off")
    area = ar.async_get(hass).async_create("Office")
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(domain, "test", "numeric", original_name=name)
    registry.async_update_entity(entry.entity_id, area_id=area.id, aliases={name})
    hass.states.async_set(entry.entity_id, "on" if domain != "cover" else "open", {"friendly_name": name, **attributes})
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    return SimpleNamespace(data=data, token=token, entity_id=entry.entity_id, domain=domain, name=name,
                           intent=intent, field=field, service=service, service_field=service_field,
                           sentence=sentence, area=area.id)


async def run_numeric(hass, home, sentence, ingress="voice"):
    """Leave recognition, planning, authorization and dispatch real."""
    async def stream(*args, **kwargs):
        yield {"type": agentcli.EV_TEXT, "text": "Normal provider reply"}
        yield {"type": agentcli.EV_DONE, "stop_reason": "end_turn",
               "assistant_msg": {"role": "assistant", "content": "Normal provider reply"}}
    provider = MagicMock()
    provider.stream_turn.side_effect = stream
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model="model")
    events = []
    async def emit(name, payload):
        events.append((name, payload))
    with (patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=store)),
          patch.object(agentcli, "build_provider", return_value=provider),
          patch.object(agentcli, "async_run_voice_turn", AsyncMock(return_value="Normal provider reply")) as voice,
          patch.object(DecisionProvider, "classify", new_callable=AsyncMock) as decision):
        if ingress == "voice":
            result = await voice_agent.async_voice_answer(hass, home.data, sentence, language="en")
        else:
            result = await agentcli.async_run_agent_turn(
                hass=hass, data=home.data, token=home.token, provider=provider, session=None,
                messages=[{"role": "user", "content": sentence}], options={}, client_ip="agentcli",
                base_url="http://test", emit=emit, cancel=asyncio.Event(), routing_language="en")
    decision.assert_not_called()
    return result, voice, provider, events


@pytest.mark.parametrize("value", [0, 37, 100])
@pytest.mark.parametrize("ingress", ["voice", "chat"])
async def test_exact_values_reach_existing_service_without_either_model(hass, numeric_home, value, ingress):
    home = numeric_home
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        result, voice, provider, _ = await run_numeric(hass, home, home.sentence.format(value=value), ingress)
    expected_value = value / 100 if home.domain == "media_player" else value
    call.assert_awaited_once_with(home.domain, home.service,
        {"entity_id": [home.entity_id], home.service_field: expected_value}, blocking=True, return_response=False)
    voice.assert_not_called()
    provider.stream_turn.assert_not_called()
    if ingress == "voice":
        assert result == "Command sent."
    else:
        assert result[0] == {"role": "user", "content": home.sentence.format(value=value)}
        assert result[-1] == {"role": "assistant", "content": "Command sent."}
    assert not home.data.agent_recovery.records(home.token.id)


@pytest.mark.parametrize("value", [True, None, "37", -1, 101, float("inf"), float("nan"), 10 ** 400])
def test_shared_validation_never_coerces_clips_or_rounds(value):
    for intent in ("HassLightSet", "HassSetVolume", "HassSetPosition"):
        operation = numeric_operation(intent)
        assert not operation.valid_value(value)
        assert service_arguments({"intent": intent, "entity_ids": [operation.domain + ".test"],
                                  operation.proposal_field: value}) is None


def test_fractional_values_follow_service_precision_without_rounding():
    assert numeric_operation("HassLightSet").valid_value(37.5)
    assert not numeric_operation("HassSetVolume").valid_value(37.5)
    assert not numeric_operation("HassSetPosition").valid_value(37.5)
    assert service_arguments({"intent": "Unknown", "entity_ids": ["light.test"]}) is None
    assert service_arguments({"intent": "HassSetPosition", "entity_ids": ["valve.test"], "position_pct": 37}) is None


@pytest.mark.parametrize("change", ["unsupported", "malformed", "unavailable", "hidden", "unexposed", "scope", "ambiguous"])
async def test_ineligible_numeric_target_never_becomes_a_local_action(hass, numeric_home, change):
    home = numeric_home
    state = hass.states.get(home.entity_id)
    attrs = dict(state.attributes)
    if change in ("unsupported", "malformed"):
        attrs["supported_color_modes"] = ["onoff"] if change == "unsupported" else "brightness"
        attrs["supported_features"] = 0 if change == "unsupported" else True
        hass.states.async_set(home.entity_id, state.state, attrs)
    elif change == "unavailable":
        hass.states.async_set(home.entity_id, "unavailable", attrs)
    elif change == "hidden":
        er.async_get(hass).async_update_entity(home.entity_id, hidden_by=er.RegistryEntryHider.USER)
    elif change == "unexposed":
        from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
        async_expose_entity(hass, "conversation", home.entity_id, False)
    elif change == "scope":
        home.token.permissions = PermissionTree()
    else:
        entry = er.async_get(hass).async_get_or_create(home.domain, "test", "duplicate", original_name=home.name)
        hass.states.async_set(entry.entity_id, state.state, attrs)
    result = await async_evaluate_command(hass, home.data, home.token, home.sentence.format(value=37))
    assert result["proposal"] is None, result


@pytest.mark.parametrize("other_domain", ["remote", "valve"])
async def test_numeric_matching_uses_ha_intent_domains(hass, numeric_home, other_domain):
    home = numeric_home
    home.token.permissions.domains[other_domain] = PermissionNode(state="GREEN")
    other = er.async_get(hass).async_get_or_create(other_domain, "test", "duplicate", original_name=home.name)
    hass.states.async_set(other.entity_id, "open", {"friendly_name": home.name})
    result = await async_evaluate_command(hass, home.data, home.token, home.sentence.format(value=37))
    if home.domain == "cover" and other_domain == "valve":
        assert result["proposal"] is None, result
        assert result["reason"] == "no_target"
    else:
        assert result["proposal"] == {"intent": home.intent, "entity_ids": [home.entity_id], home.field: 37}, result


@pytest.mark.parametrize("variant", ["negated", "quoted", "deferred", "multiple", "fractional", "relative"])
async def test_complete_sentence_required_for_numeric_routing(hass, numeric_home, variant):
    home = numeric_home
    sentence = home.sentence.format(value=37)
    sentence = {"negated": "do not " + sentence, "quoted": 'say "' + sentence + '"',
                "deferred": sentence + " in ten minutes", "multiple": sentence + " and turn on the desk lamp",
                "fractional": home.sentence.format(value="37.5"),
                "relative": sentence.replace("set", "increase", 1).replace(" to ", " by ")}[variant]
    result = await async_evaluate_command(hass, home.data, home.token, sentence)
    assert result["proposal"] is None, result


async def test_explicit_area_requires_every_resolved_target_to_support_operation(hass, numeric_home):
    home = numeric_home
    sentence = {"light": "set the office brightness to 37 percent", "media_player": "set the office volume to 37 percent",
                "cover": "set the blinds in the office to 37 percent"}[home.domain]
    result = await async_evaluate_command(hass, home.data, home.token, sentence)
    assert result["proposal"] == {"intent": home.intent, "entity_ids": [home.entity_id], home.field: 37}, result
    registry = er.async_get(hass)
    other = registry.async_get_or_create(home.domain, "test", "other")
    registry.async_update_entity(other.entity_id, area_id=home.area)
    attrs = {"device_class": "blind"} if home.domain == "cover" else {}
    hass.states.async_set(other.entity_id, "on", attrs)
    result = await async_evaluate_command(hass, home.data, home.token, sentence)
    assert result["proposal"] is None, result


@pytest.mark.parametrize("mode", ["confirm", "prohibited", "read_only"])
async def test_numeric_commands_keep_mesa_enforcement(hass, numeric_home, mode):
    home = numeric_home
    await home.data.store.async_patch_settings(mesa_mode="enforced")
    home.data.mesa = await async_setup_mesa(hass, "enforced")
    home.data.mesa.store.set(home.entity_id, SemanticProfile.from_dict(home.entity_id,
        {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}}, default_origin=MetadataOrigin.USER))
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        result, voice, provider, _ = await run_numeric(hass, home, home.sentence.format(value=37))
    call.assert_not_called()
    voice.assert_not_called()
    provider.stream_turn.assert_not_called()
    assert ("needs approval" if mode == "confirm" else "could not complete") in result


@pytest.mark.parametrize("change", ["none", "features", "value", "invalid_value", "domain", "scope", "exposure"])
async def test_approval_rechecks_exact_numeric_operation(hass, numeric_home, change):
    home = numeric_home
    await home.data.store.async_patch_settings(mesa_mode="enforced")
    home.data.mesa = await async_setup_mesa(hass, "enforced")
    home.data.mesa.store.set(home.entity_id, SemanticProfile.from_dict(home.entity_id,
        {"semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}}, default_origin=MetadataOrigin.USER))
    with patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])):
        result, _, _, _ = await run_numeric(hass, home, home.sentence.format(value=37))
    assert "needs approval" in result
    record = copy.deepcopy(home.data.store.get_pending_approvals()[0])
    args = record["args"]
    if change == "features":
        state = hass.states.get(home.entity_id)
        hass.states.async_set(home.entity_id, state.state, {**state.attributes,
            "supported_features": 0, "supported_color_modes": ["onoff"]})
    elif change == "value":
        args["service_data"][home.service_field] = 55
    elif change == "invalid_value":
        args[COMMAND_ROUTING_PLAN_KEY]["proposal"][home.field] = -1
        args["service_data"][home.service_field] = -0.01 if home.domain == "media_player" else -1
    elif change == "domain":
        args["domain"] = "valve"
    elif change == "scope":
        home.token.permissions = PermissionTree()
    elif change == "exposure":
        from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
        async_expose_entity(hass, "conversation", home.entity_id, False)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], args, home.token, hass, home.data)
    if change == "none":
        assert outcome == "allowed"
        call.assert_awaited_once_with(home.domain, home.service, {"entity_id": [home.entity_id],
            home.service_field: 0.37 if home.domain == "media_player" else 37}, blocking=True, return_response=False)
    else:
        assert outcome == "denied"
        call.assert_not_called()


@pytest.mark.parametrize("language", ["es", "zh-Hans"])
async def test_numeric_values_from_installed_multilingual_templates(hass, numeric_home, language):
    home = numeric_home
    names = {"es": {"light": "Lámpara", "media_player": "Altavoz", "cover": "Persiana"},
             "zh-Hans": {"light": "台灯", "media_player": "音箱", "cover": "窗帘"}}
    name = names[language][home.domain]
    er.async_get(hass).async_update_entity(home.entity_id, aliases={name})
    sentence = ({"light": "pon el brillo de la lámpara al 37 por ciento",
                 "media_player": "en el altavoz pon el volumen al 37 por ciento",
                 "cover": "pon la persiana al 37 por ciento"} if language == "es" else
                {"light": "把台灯的亮度设置为百分之三十七", "media_player": "把音箱的音量设置为百分之三十七",
                 "cover": "把窗帘的开合度设置为百分之三十七"})[home.domain]
    result = await async_evaluate_command(hass, home.data, home.token, sentence, language)
    assert result["proposal"] == {"intent": home.intent, "entity_ids": [home.entity_id], home.field: 37}, result
