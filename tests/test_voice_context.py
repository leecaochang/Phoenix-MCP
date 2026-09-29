"""Trusted HA satellite locations preserve scoped targets and stale-plan checks."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp import command_routing, voice_agent
from custom_components.phoenix_mcp.command_execution import _capture_plan, plan_is_current
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree
from custom_components.phoenix_mcp.voice_context import capture_voice_origin, voice_origin_current


@pytest.fixture
async def satellite_home(hass, token_store):
    token, _ = await token_store.async_create_token('satellite', 'test')
    token.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    await token_store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_routing_mode='local', command_routing_timeout_ms=5000)
    config = MockConfigEntry(domain='test_integration')
    config.add_to_hass(hass)
    kitchen = ar.async_get(hass).async_create('Kitchen')
    bedroom = ar.async_get(hass).async_create('Bedroom')
    device = dr.async_get(hass).async_get_or_create(config_entry_id=config.entry_id,
        identifiers={('test', 'satellite')}, name='Voice satellite')
    dr.async_get(hass).async_update_device(device.id, area_id=kitchen.id)
    satellite = er.async_get(hass).async_get_or_create('assist_satellite', 'test', 'satellite',
        config_entry=config, device_id=device.id)
    lights = {}
    for area in (kitchen, bedroom):
        light = er.async_get(hass).async_get_or_create('light', 'test', area.id, original_name=area.name+' lamp')
        er.async_get(hass).async_update_entity(light.entity_id, area_id=area.id)
        hass.states.async_set(light.entity_id, 'off', {'friendly_name': area.name+' lamp', 'supported_color_modes': ['brightness']})
        lights[area.name] = light.entity_id
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    return SimpleNamespace(data=data, token=token, satellite=satellite, device=device,
                           kitchen=kitchen, bedroom=bedroom, lights=lights)


@pytest.mark.parametrize('sentence,area', [('turn on the lights', 'Kitchen'),
    ('turn on the lights in the bedroom', 'Bedroom')])
async def test_real_ha_implicit_area_and_explicit_area_precedence(hass, satellite_home, sentence, area):
    h = satellite_home
    origin = capture_voice_origin(hass, h.satellite.entity_id, h.device.id)
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as service:
        result = await command_routing.async_evaluate_command(hass, h.data, h.token, sentence, voice_origin=origin)
    assert result['proposal'] == {'intent': 'HassTurnOn', 'entity_ids': [h.lights[area]], 'voice_origin': origin}, result
    plan = _capture_plan(hass, h.data, h.token, result['proposal'])
    assert plan is not None and plan_is_current(plan, hass, h.data, h.token)
    service.assert_not_called()


@pytest.mark.parametrize('change', ['device_area', 'entity_area', 'name', 'disabled', 'removed'])
async def test_location_changes_invalidate_existing_plans(hass, satellite_home, change):
    h = satellite_home
    origin = capture_voice_origin(hass, h.satellite.entity_id)
    proposal = {'intent': 'HassTurnOn', 'entity_ids': [h.lights['Kitchen']], 'voice_origin': origin}
    plan = _capture_plan(hass, h.data, h.token, proposal)
    assert plan is not None
    if change == 'device_area':
        dr.async_get(hass).async_update_device(h.device.id, area_id=h.bedroom.id)
    elif change == 'entity_area':
        er.async_get(hass).async_update_entity(h.satellite.entity_id, area_id=h.bedroom.id)
    elif change == 'name':
        ar.async_get(hass).async_update(h.kitchen.id, name='Changed Kitchen')
    elif change == 'disabled':
        er.async_get(hass).async_update_entity(h.satellite.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    else:
        er.async_get(hass).async_remove(h.satellite.entity_id)
    assert not voice_origin_current(hass, origin)
    assert not plan_is_current(plan, hass, h.data, h.token)
    assert _capture_plan(hass, h.data, h.token, proposal) is None


@pytest.mark.parametrize('kind', ['missing', 'wrong_domain', 'wrong_device', 'no_satellite', 'no_area'])
async def test_untrusted_or_incomplete_location_is_not_evidence(hass, satellite_home, kind):
    h = satellite_home
    satellite, device = h.satellite.entity_id, h.device.id
    if kind == 'missing': satellite = 'assist_satellite.missing'
    if kind == 'wrong_domain': satellite = h.lights['Kitchen']
    if kind == 'wrong_device': device = 'unrelated'
    if kind == 'no_satellite': satellite = None
    if kind == 'no_area': dr.async_get(hass).async_update_device(h.device.id, area_id=None)
    assert capture_voice_origin(hass, satellite, device) is None


async def test_scope_is_not_widened_by_satellite_area(hass, satellite_home):
    h = satellite_home
    h.token.permissions.domains['light'].state = 'RED'
    result = await command_routing.async_evaluate_command(hass, h.data, h.token, 'turn on the lights',
        voice_origin=capture_voice_origin(hass, h.satellite.entity_id))
    assert result['proposal'] is None


async def test_explicit_collection_read_keeps_origin_revalidation(hass, satellite_home):
    h = satellite_home
    origin = capture_voice_origin(hass, h.satellite.entity_id)
    result = await command_routing.async_evaluate_command(hass, h.data, h.token,
        'how many lights are on in the kitchen', voice_origin=origin)
    assert result['proposal']['intent'] == 'PhoenixStateSummary'
    plan = _capture_plan(hass, h.data, h.token, result['proposal'])
    assert plan is not None and plan_is_current(plan, hass, h.data, h.token)
    dr.async_get(hass).async_update_device(h.device.id, area_id=h.bedroom.id)
    assert not plan_is_current(plan, hass, h.data, h.token)


async def test_area_change_during_recognition_cannot_publish(hass, satellite_home):
    h = satellite_home
    original = command_routing.async_recognize
    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        dr.async_get(hass).async_update_device(h.device.id, area_id=h.bedroom.id)
        return result
    with patch.object(command_routing, 'async_recognize', changed):
        result = await command_routing.async_evaluate_command(hass, h.data, h.token, 'turn on the lights',
            voice_origin=capture_voice_origin(hass, h.satellite.entity_id))
    assert result['reason'] == 'authority_changed' and result['proposal'] is None


async def test_conversation_ingress_forwards_only_ha_origin(hass, satellite_home):
    from homeassistant.components.conversation.models import ConversationInput
    from homeassistant.core import Context
    h = satellite_home
    agent = voice_agent.PhoenixConversationAgent(hass, h.data)
    user_input = ConversationInput(text='turn on the lights', context=Context(), conversation_id=None,
        device_id=h.device.id, satellite_id=h.satellite.entity_id, language='en', agent_id='test')
    with patch.object(voice_agent, 'async_voice_answer', AsyncMock(return_value='Done')) as answer:
        await agent.async_process(user_input)
    assert answer.call_args.kwargs['satellite_id'] == h.satellite.entity_id
    assert answer.call_args.kwargs['device_id'] == h.device.id
    assert answer.call_args.kwargs['include_review_links'] is False
