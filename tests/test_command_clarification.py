"""Clarification is one fresh, scoped target choice, never an approval or replay."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli, command_clarification as cc, mcp_view, voice_agent
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.command_execution import _capture_plan, plan_is_current
from custom_components.phoenix_mcp.command_routing import async_evaluate_command
from custom_components.phoenix_mcp.const import DOMAIN, COMMAND_CLARIFICATION_LIMIT
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree


@pytest.fixture
async def ambiguous_home(hass, access_key_store, immediate_command_observation):
    access_key, _ = await access_key_store.async_create_access_key('clarification', 'test', rate_limit_requests=0)
    access_key.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    access_key.cap_physical_control = 'allow'
    data = PhoenixData(store=access_key_store, audit=AuditLog(), rate_limiter=RateLimiter())
    hass.data[DOMAIN] = data
    await access_key_store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=access_key.id,
        voice_agent_provider_id='p', voice_agent_model='m', voice_agent_routing_mode='local',
        agentcli_routing_mode='local', command_routing_timeout_ms=5000, mesa_mode='off')
    ids = []
    for index in range(2):
        entry = er.async_get(hass).async_get_or_create('light', 'test', str(index), original_name=f'Lamp {index}')
        er.async_get(hass).async_update_entity(entry.entity_id, aliases={'Desk lamp'})
        hass.states.async_set(entry.entity_id, 'off', {'friendly_name': f'Lamp {index}', 'supported_color_modes': ['brightness']})
        ids.append(entry.entity_id)
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    return SimpleNamespace(data=data, access_key=access_key, ids=sorted(ids))


def test_memory_is_one_use_bounded_expiring_and_binding_specific():
    memory = cc.CommandClarifications()
    request = {'sentence': 'private utterance', 'choices': {}}
    with patch.object(cc, 'monotonic', return_value=1):
        memory.remember('c', 'authority', request)
        request['sentence'] = 'changed'
        assert memory.consume('other', 'authority') is None
        assert memory.consume('c', 'authority')['sentence'] == 'private utterance'
        assert memory.consume('c', 'authority') is None
        memory.remember('c', 'old', request)
        assert memory.consume('c', 'new') == {'_expired': True}
        for index in range(COMMAND_CLARIFICATION_LIMIT + 1):
            memory.remember(index, 'binding', request)
        assert len(memory._entries) == COMMAND_CLARIFICATION_LIMIT
        assert memory.consume(0, 'binding') is None
    with patch.object(cc, 'monotonic', return_value=1000):
        assert memory.consume(1, 'binding') == {'_expired': True}
    assert 'private utterance' not in repr(memory)


async def test_no_clarification_details_in_default_preview(hass, ambiguous_home):
    h = ambiguous_home
    result = await async_evaluate_command(hass, h.data, h.access_key, 'turn on the desk lamp')
    assert result['reason'] == 'no_target' and '_clarification' not in result


@pytest.mark.parametrize('answer', ['1', 'light.test_0', '2', 'one', 'two'])
async def test_number_and_exact_id_recheck_original_request(hass, ambiguous_home, answer):
    h = ambiguous_home
    result = await async_evaluate_command(hass, h.data, h.access_key, 'turn on the desk lamp', clarify=True)
    assert '_clarification' in result, result
    request = result['_clarification']
    selected, reply = await cc.async_clarification_reply(hass, h.data, h.access_key, request, answer, 'en', ingress='voice')
    assert reply is None and selected is not None
    assert selected['entity_ids'] == [h.ids[1 if answer in ('2', 'two') else 0]]
    plan = _capture_plan(hass, h.data, h.access_key, selected)
    assert plan_is_current(plan, hass, h.data, h.access_key)


@pytest.mark.parametrize('change', ['alias', 'scope', 'new_competitor', 'unavailable', 'custom'])
async def test_changed_choices_never_select_from_old_list(hass, ambiguous_home, change):
    h = ambiguous_home
    request = (await async_evaluate_command(hass, h.data, h.access_key, 'turn on the desk lamp', clarify=True))['_clarification']
    if change == 'alias': er.async_get(hass).async_update_entity(h.ids[1], aliases={'Other'})
    if change == 'scope': h.access_key.permissions.entities[h.ids[1]] = PermissionNode(state='YELLOW')
    if change == 'new_competitor':
        entry = er.async_get(hass).async_get_or_create('light', 'test', 'third', original_name='Desk lamp')
        hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Desk lamp'})
    if change == 'unavailable': hass.states.async_set(h.ids[1], 'unavailable')
    if change == 'custom':
        from custom_components.phoenix_mcp.intent_recognition import async_recognize
        recognized = await async_recognize(hass, request['sentence'], 'en', strict=True)
        recognized.intent_metadata = {'hass_custom_sentence': True}
        context = patch('custom_components.phoenix_mcp.command_routing.async_recognize', AsyncMock(return_value=recognized))
    else:
        context = nullcontext()
    with context:
        selected, reply = await cc.async_clarification_reply(hass, h.data, h.access_key, request, '1', 'en', ingress='voice')
    assert selected is None and 'repeat the full command' in reply


@pytest.mark.parametrize('answer,expected', [('cancel', 'Cancelled.'), ('取消', 'Cancelled.'),
    ('1 and turn off everything', None), ('maybe the first', None), ('turn off the lamp 0', None)])
async def test_cancel_and_unrecognized_reply_consume_no_action(hass, ambiguous_home, answer, expected):
    h = ambiguous_home
    request = (await async_evaluate_command(hass, h.data, h.access_key, 'turn on the desk lamp', clarify=True))['_clarification']
    proposal, reply = await cc.async_clarification_reply(hass, h.data, h.access_key, request, answer, 'en', ingress='voice')
    assert proposal is None and reply == expected


@pytest.mark.parametrize('change', ['new_competitor', 'alias', 'scope'])
async def test_competitor_changes_invalidate_captured_choice_plan(hass, ambiguous_home, change):
    h = ambiguous_home
    request = (await async_evaluate_command(hass, h.data, h.access_key, 'turn on the desk lamp', clarify=True))['_clarification']
    proposal, _ = await cc.async_clarification_reply(hass, h.data, h.access_key, request, '1', 'en', ingress='voice')
    plan = _capture_plan(hass, h.data, h.access_key, proposal)
    if change == 'new_competitor':
        entry = er.async_get(hass).async_get_or_create('light', 'test', 'third', original_name='Desk lamp')
        hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Desk lamp'})
    elif change == 'alias': er.async_get(hass).async_update_entity(h.ids[1], aliases={'Other'})
    else: h.access_key.permissions.entities[h.ids[1]] = PermissionNode(state='YELLOW')
    assert not plan_is_current(plan, hass, h.data, h.access_key)


@pytest.mark.parametrize('ingress', ['voice', 'chat'])
@pytest.mark.parametrize('confirm', [False, True, 'stale'])
async def test_two_turn_choice_runs_existing_dispatch_and_approval(hass, ambiguous_home, ingress, confirm):
    h = ambiguous_home
    if confirm:
        from custom_components.phoenix_mcp.mesa import async_setup_mesa
        from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
        await h.data.store.async_patch_settings(mesa_mode='enforced')
        h.data.mesa = await async_setup_mesa(hass, 'enforced')
        h.data.mesa.store.set(h.ids[0], SemanticProfile.from_dict(h.ids[0],
            {'control_mode': 'confirm', 'origin': MetadataOrigin.USER.value}))
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model='m')
    provider = MagicMock()
    messages = []
    async def turn(sentence):
        if ingress == 'voice':
            return await voice_agent.async_voice_answer(hass, h.data, sentence, language='en', conversation_id='c')
        messages.append({'role': 'user', 'content': sentence})
        await agentcli.async_run_agent_turn(hass=hass, data=h.data, access_key=h.access_key, provider=provider,
            session=None, messages=messages, options={}, client_ip='agentcli', base_url='http://test',
            emit=AsyncMock(), cancel=asyncio.Event(), routing_language='en')
        return messages[-1]['content']
    with (patch.object(agentcli, '_get_secret_store', AsyncMock(return_value=store)),
          patch.object(agentcli, 'build_provider', return_value=provider) as build,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        prompt = await turn('turn on the desk lamp')
        assert 'Which target' in prompt and all(eid in prompt for eid in h.ids)
        call.assert_not_called()
        reply = await turn('1')
        if confirm:
            assert 'needs approval' in reply
            call.assert_not_called()
            record, = h.data.store.get_pending_approvals()
            if confirm == 'stale':
                er.async_get(hass).async_update_entity(h.ids[1], aliases={'Changed competitor'})
            _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], h.access_key, hass, h.data)
            assert outcome == ('denied' if confirm == 'stale' else 'allowed')
        if confirm == 'stale':
            call.assert_not_called()
        else:
            call.assert_awaited_once_with('light', 'turn_on', {'entity_id': [h.ids[0]]}, blocking=True, return_response=False)
        build.assert_not_called()
        provider.stream_turn.assert_not_called()
    assert not h.data.command_routing.clarifications._entries
    stats = h.data.routing_statistics.summary()[ingress]
    assert stats['outcomes']['decision'] == 0
    assert stats['outcomes']['local'] == (0 if confirm else 1)
    assert stats['provider_turns'] == 0


@pytest.mark.parametrize('ingress', ['voice', 'chat'])
async def test_cancelled_choice_is_counted_without_a_model_or_action(hass, ambiguous_home, ingress):
    h = ambiguous_home
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model='m')
    provider = MagicMock()
    messages = []
    with (patch.object(agentcli, '_get_secret_store', AsyncMock(return_value=store)),
          patch.object(agentcli, 'build_provider', return_value=provider) as build,
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        for sentence in ('turn on the desk lamp', 'cancel'):
            if ingress == 'voice':
                await voice_agent.async_voice_answer(hass, h.data, sentence, language='en', conversation_id='cancel-test')
            else:
                messages.append({'role': 'user', 'content': sentence})
                await agentcli.async_run_agent_turn(hass=hass, data=h.data, access_key=h.access_key, provider=provider,
                    session=None, messages=messages, options={}, client_ip='agentcli', base_url='http://test',
                    emit=AsyncMock(), cancel=asyncio.Event(), routing_language='en')
        call.assert_not_called()
        build.assert_not_called()
        provider.stream_turn.assert_not_called()
    stats = h.data.routing_statistics.summary()[ingress]
    assert stats['outcomes']['cancelled'] == 1 and stats['outcomes']['failed'] == 1
    assert stats['outcomes']['local'] == stats['outcomes']['decision'] == stats['provider_turns'] == 0
