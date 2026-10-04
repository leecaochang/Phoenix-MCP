"""Pinned native sentences retain scoped service and stale-plan semantics."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli, mcp_view, voice_agent
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.command_routing import async_evaluate_command
from custom_components.phoenix_mcp.command_execution import _capture_plan, plan_is_current
from custom_components.phoenix_mcp.command_operations import service_arguments
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree

CORPUS = json.loads((Path(__file__).parent/'fixtures/ohf_native_actions/cases.json').read_text())
EXPECTED = {
    'HassMediaPause': ('media_pause', {}), 'HassMediaUnpause': ('media_play', {}),
    'HassMediaNext': ('media_next_track', {}), 'HassMediaPrevious': ('media_previous_track', {}),
    'HassMediaPlayerMute': ('volume_mute', {'is_volume_muted': True}),
    'HassMediaPlayerUnmute': ('volume_mute', {'is_volume_muted': False}),
    'HassVacuumStart': ('start', {}), 'HassVacuumReturnToBase': ('return_to_base', {}),
}


@pytest.fixture
async def native_home(hass, access_key_store, immediate_command_observation):
    access_key, _ = await access_key_store.async_create_access_key('native', 'test', rate_limit_requests=0)
    access_key.permissions = PermissionTree(domains={domain: PermissionNode(state='GREEN') for domain in ('media_player', 'vacuum')})
    access_key.cap_physical_control = 'allow'
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    await access_key_store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=access_key.id,
                                          voice_agent_routing_mode='local', command_routing_timeout_ms=5000,
                                          voice_agent_provider_id='p', voice_agent_model='model',
                                          agentcli_routing_mode='local', mesa_mode='off')
    entities = {}
    for domain, name, state, features in (
        ('media_player', 'TV', 'playing', int(MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.PLAY |
            MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK | MediaPlayerEntityFeature.VOLUME_MUTE)),
        ('vacuum', 'Rover', 'idle', int(VacuumEntityFeature.START | VacuumEntityFeature.RETURN_HOME)),
    ):
        entry = er.async_get(hass).async_get_or_create(domain, 'test', domain, original_name=name)
        hass.states.async_set(entry.entity_id, state, {'friendly_name': name, 'supported_features': features})
        entities[domain] = entry.entity_id
    for intent, (service, _) in EXPECTED.items():
        domain = 'vacuum' if intent.startswith('HassVacuum') else 'media_player'
        hass.services.async_register(domain, service, AsyncMock())
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    return SimpleNamespace(data=data, access_key=access_key, entities=entities)


@pytest.mark.parametrize('case', CORPUS['cases'], ids=lambda c: c['sentence'])
async def test_pinned_ohf_sentences_map_to_exact_guarded_services(hass, native_home, case):
    home = native_home
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        result = await async_evaluate_command(hass, home.data, home.access_key, case['sentence'])
    assert result['reason'] == 'supported', result
    proposal = result['proposal']
    assert proposal == {'intent': case['intent'], 'entity_ids': [home.entities[case['domain']]]}
    service, values = EXPECTED[case['intent']]
    assert service_arguments(proposal) == {'domain': case['domain'], 'service': service,
                                         'entity_id': proposal['entity_ids'], 'service_data': values}
    plan = _capture_plan(hass, home.data, home.access_key, proposal)
    assert plan is not None and plan_is_current(plan, hass, home.data, home.access_key)
    call.assert_not_called()
    assert result['executed'] is False and home.data.store.get_pending_approvals() == []


@pytest.mark.parametrize('sentence', ['pause', 'resume', 'mute', 'start cleaning', 'pause all media players',
    'do not pause TV', 'pause TV in ten minutes', 'pause TV and start rover', 'explain how to pause TV',
    'pause missing TV', 'clean the kitchen'])
async def test_native_actions_require_one_explicit_supported_request(hass, native_home, sentence):
    home = native_home
    result = await async_evaluate_command(hass, home.data, home.access_key, sentence)
    assert result['proposal'] is None, result


@pytest.mark.parametrize('change', ['read_only', 'unavailable', 'feature', 'state', 'duplicate', 'custom'])
async def test_native_action_refusals_preserve_competitors_and_precedence(hass, native_home, change):
    home = native_home
    entity_id = home.entities['media_player']
    state = hass.states.get(entity_id)
    if change == 'read_only':
        home.access_key.permissions.domains['media_player'].state = 'YELLOW'
    elif change in ('unavailable', 'feature', 'state'):
        hass.states.async_set(entity_id, 'unavailable' if change == 'unavailable' else 'paused' if change == 'state' else state.state,
                              {**state.attributes, **({'supported_features': 0} if change == 'feature' else {})})
    elif change == 'duplicate':
        other = er.async_get(hass).async_get_or_create('media_player', 'test', 'duplicate', original_name='TV')
        hass.states.async_set(other.entity_id, 'paused', dict(state.attributes))
    from custom_components.phoenix_mcp import command_routing
    original = command_routing.async_recognize
    async def recognize(*args, **kwargs):
        result = await original(*args, **kwargs)
        if change == 'custom':
            assert result is not None
            result.intent_metadata = {'hass_custom_sentence': True}
        return result
    with patch.object(command_routing, 'async_recognize', side_effect=recognize):
        result = await async_evaluate_command(hass, home.data, home.access_key, 'pause TV')
    assert result['proposal'] is None, result


@pytest.mark.parametrize('intent,sentence', [('HassMediaPause', 'pause TV'), ('HassMediaNext', 'next item on TV'),
                                          ('HassMediaPrevious', 'previous track on the TV')])
async def test_native_media_plan_rechecks_playing_before_effects(hass, native_home, intent, sentence):
    home = native_home
    result = await async_evaluate_command(hass, home.data, home.access_key, sentence)
    assert result['proposal'] is not None, result
    plan = _capture_plan(hass, home.data, home.access_key, result['proposal'])
    assert plan_is_current(plan, hass, home.data, home.access_key)
    state = hass.states.get(home.entities['media_player'])
    hass.states.async_set(state.entity_id, 'paused', dict(state.attributes))
    assert not plan_is_current(plan, hass, home.data, home.access_key)


async def test_native_duplicate_target_choice_retains_fixed_service_projection(hass, native_home):
    from custom_components.phoenix_mcp.command_clarification import async_clarification_reply
    home = native_home
    state = hass.states.get(home.entities['media_player'])
    other = er.async_get(hass).async_get_or_create('media_player', 'test', 'other-tv', original_name='TV')
    hass.states.async_set(other.entity_id, 'playing', dict(state.attributes))
    observed = await async_evaluate_command(hass, home.data, home.access_key, 'pause TV', clarify=True)
    selected, reply = await async_clarification_reply(hass, home.data, home.access_key, observed['_clarification'], '1', 'en', ingress='voice')
    assert reply is None and selected is not None
    assert service_arguments(selected)['service'] == 'media_pause'
    plan = _capture_plan(hass, home.data, home.access_key, selected)
    assert plan is not None and plan_is_current(plan, hass, home.data, home.access_key)


async def run_native(hass, home, sentence, ingress):
    """Leave routing, authorization, approval and recovery paths intact."""
    async def stream(*args, **kwargs):
        yield {'type': agentcli.EV_TEXT, 'text': 'Provider fallback'}
        yield {'type': agentcli.EV_DONE, 'stop_reason': 'end_turn',
               'assistant_msg': {'role': 'assistant', 'content': 'Provider fallback'}}
    provider = MagicMock()
    provider.stream_turn.side_effect = stream
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model='model')
    with (patch.object(agentcli, '_get_secret_store', AsyncMock(return_value=store)),
          patch.object(agentcli, 'build_provider', return_value=provider),
          patch.object(agentcli, 'async_run_voice_turn', AsyncMock(return_value='Provider fallback')) as voice):
        if ingress == 'voice':
            result = await voice_agent.async_voice_answer(hass, home.data, sentence, language='en')
        else:
            result = await agentcli.async_run_agent_turn(hass=hass, data=home.data, access_key=home.access_key,
                provider=provider, session=None, messages=[{'role': 'user', 'content': sentence}], options={},
                client_ip='agentcli', base_url='http://test', emit=AsyncMock(), cancel=asyncio.Event(), routing_language='en')
    voice.assert_not_called()
    provider.stream_turn.assert_not_called()
    return result


@pytest.mark.parametrize('case', list({c['intent']: c for c in CORPUS['cases']}.values()), ids=lambda c: c['intent'])
@pytest.mark.parametrize('ingress', ['voice', 'chat'])
async def test_native_actions_execute_once_through_existing_ingress(hass, native_home, case, ingress):
    home = native_home
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        await run_native(hass, home, case['sentence'], ingress)
    service, values = EXPECTED[case['intent']]
    call.assert_awaited_once_with(case['domain'], service,
        {'entity_id': [home.entities[case['domain']]], **values}, blocking=True, return_response=False)
    assert home.data.agent_recovery.records(home.access_key.id) == []


@pytest.mark.parametrize('change', ['unchanged', 'state', 'feature', 'scope'])
async def test_native_approval_keeps_exact_action_and_current_guards(hass, native_home, change):
    home = native_home
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    await home.data.store.async_patch_settings(mesa_mode='enforced')
    home.data.mesa = await async_setup_mesa(hass, 'enforced')
    entity_id = home.entities['media_player']
    home.data.mesa.store.set(entity_id, SemanticProfile.from_dict(entity_id,
        {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}}, default_origin=MetadataOrigin.USER))
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        answer = await run_native(hass, home, 'pause TV', 'voice')
        assert 'needs approval' in answer
        call.assert_not_called()
        record, = home.data.store.get_pending_approvals()
        state = hass.states.get(home.entities['media_player'])
        if change in ('state', 'feature'):
            hass.states.async_set(state.entity_id, 'paused' if change == 'state' else state.state,
                                  {**state.attributes, **({'supported_features': 0} if change == 'feature' else {})})
        elif change == 'scope':
            home.access_key.permissions = PermissionTree()
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], home.access_key, hass, home.data)
        if change == 'unchanged':
            assert outcome == 'allowed'
            call.assert_awaited_once_with('media_player', 'media_pause', {'entity_id': [state.entity_id]}, blocking=True, return_response=False)
        else:
            assert outcome == 'denied'
            call.assert_not_called()
