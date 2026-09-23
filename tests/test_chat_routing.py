"""Agent Chat observes isolated first messages without owning tool execution."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli, chat_routing
from custom_components.phoenix_mcp.admin_view import _validated_settings_patch
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_provider import DecisionProvider
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import GlobalSettings, PermissionNode, PermissionTree


@pytest.fixture
async def chat_home(hass, token_store):
    token, _ = await token_store.async_create_token('chat', 'test', rate_limit_requests=0)
    token.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    data = PhoenixData(store=token_store, audit=AuditLog(), rate_limiter=RateLimiter())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    hass.data['_phoenix_agentcli_store'] = store
    account = await store.add('typesafe', {'api_key': 'decision-secret', 'model': 'test-model'})
    await token_store.async_patch_settings(agentcli_routing_shadow_enabled=True, decision_provider_id=account)
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    entry = er.async_get(hass).async_get_or_create('light', 'test', 'desk', original_name='Desk lamp')
    hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Desk lamp'})
    yield data, token
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


async def chat_turn(hass, home, messages=None, language='en', cancel=None):
    data, token = home
    provider = MagicMock()
    provider.format_tools.side_effect = lambda tools: tools
    provider.append_assistant.side_effect = lambda history, message: history.append(message)
    async def stream(*args, **kwargs):
        yield {'type': agentcli.EV_TEXT, 'text': 'Normal provider reply'}
        yield {'type': agentcli.EV_DONE, 'stop_reason': 'end_turn',
               'assistant_msg': {'role': 'assistant', 'content': 'Normal provider reply'}}
    provider.stream_turn.side_effect = stream
    events = []
    async def emit(name, payload):
        events.append((name, payload))
    result = await agentcli.async_run_agent_turn(
        hass=hass, data=data, token=token, provider=provider, session=None,
        messages=messages if messages is not None else [{'role': 'user', 'content': 'Please illuminate the desk lamp'}],
        options={}, client_ip='agentcli', base_url='http://test', emit=emit,
        cancel=cancel or asyncio.Event(), routing_language=language,
    )
    return provider, events, result


async def test_real_local_match_is_observed_without_dispatch_or_decision(hass, chat_home):
    data, _ = chat_home
    with (patch.object(DecisionProvider, 'classify', new_callable=AsyncMock) as decision,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, events, messages = await chat_turn(hass, chat_home, [{'role': 'user', 'content': 'turn on the desk lamp'}])
    provider.stream_turn.assert_called_once()
    decision.assert_not_called()
    dispatch.assert_not_called()
    assert messages[-1]['content'] == 'Normal provider reply'
    assert events[-1] == ('done', {'stop_reason': 'end_turn'})
    summary = data.command_routing.summary()
    assert summary['observations'] == 0
    assert summary['agent_chat'] == {'observations': 1, 'candidates': 1, 'reasons': {'supported': 1}}
    assert not data.store.get_pending_approvals()


async def test_decision_does_not_delay_or_replace_provider_and_keeps_only_counts(hass, chat_home):
    data, _ = chat_home
    entered, release = asyncio.Event(), asyncio.Event()
    async def classify(self, session, sentence, timeout):
        assert sentence == 'Please illuminate the desk lamp'
        entered.set()
        await release.wait()
        return {'choice': 'light_on'}
    with (patch.object(DecisionProvider, 'classify', classify),
          patch.object(DecisionProvider, 'select_target') as targets,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        async with asyncio.timeout(2):
            provider, _, messages = await chat_turn(hass, chat_home)
            await entered.wait()
        assert not release.is_set()
        pending = data.decision_shadow.task
        release.set()
        await pending
        await asyncio.sleep(0)
    provider.stream_turn.assert_called_once()
    targets.assert_not_called()
    dispatch.assert_not_called()
    assert messages[-1]['content'] == 'Normal provider reply'
    assert data.decision_shadow.summary()['agent_chat'] == {
        'observations': 1, 'reasons': {'observed': 1}, 'choices': {'light_on': 1}}
    assert data.decision_shadow.observations == 0 and not data.decision_shadow.hybrid_reasons
    assert 'desk lamp' not in repr(data.decision_shadow) and 'secret' not in repr(data.decision_shadow)


@pytest.mark.parametrize('case', ['off', 'history', 'non_english', 'missing_language', 'blocks', 'extra_fields'])
async def test_excluded_messages_skip_all_observation(hass, chat_home, case):
    data, _ = chat_home
    messages = [{'role': 'user', 'content': 'command'}]
    language = 'en'
    if case == 'off': await data.store.async_patch_settings(agentcli_routing_shadow_enabled=False)
    if case == 'history': messages = [{'role': 'assistant', 'content': 'context'}, *messages]
    if case == 'non_english': language = 'fr'
    if case == 'missing_language': language = None
    if case == 'blocks': messages[0]['content'] = [{'type': 'text', 'text': 'command'}]
    if case == 'extra_fields': messages[0]['attachments'] = []
    with patch.object(chat_routing, 'async_evaluate_command', new_callable=AsyncMock) as local:
        provider, _, _ = await chat_turn(hass, chat_home, messages, language)
    provider.stream_turn.assert_called_once()
    local.assert_not_called()


@pytest.mark.parametrize('reason', ['timeout', 'busy', 'sentence_trigger', 'unsupported', 'no_target', 'no_match'])
async def test_only_eligible_local_misses_start_background_decision(hass, chat_home, reason):
    data, _ = chat_home
    local = {'route': 'provider', 'reason': reason, 'proposal': None, 'elapsed_ms': 1}
    with (patch.object(chat_routing, 'async_evaluate_command', AsyncMock(return_value=local)),
          patch.object(data.decision_shadow, 'start', return_value=None) as start):
        provider, _, _ = await chat_turn(hass, chat_home)
    provider.stream_turn.assert_called_once()
    assert start.call_count == (1 if reason in ('unsupported', 'no_target', 'no_match') else 0)
    if start.called:
        assert start.call_args.kwargs == {'observe_chat': True}


@pytest.mark.parametrize('change', ['token', 'settings', 'cancel'])
async def test_changes_during_recognition_stop_before_provider(hass, chat_home, change):
    data, token = chat_home
    cancel = asyncio.Event()
    async def local(*args):
        if change == 'token': token.revoked = True
        if change == 'settings': await data.store.async_patch_settings(agentcli_routing_shadow_enabled=False)
        if change == 'cancel': cancel.set()
        return {'route': 'provider', 'reason': 'no_match', 'proposal': None, 'elapsed_ms': 1}
    with patch.object(chat_routing, 'async_evaluate_command', local):
        provider, events, _ = await chat_turn(hass, chat_home, cancel=cancel)
    provider.stream_turn.assert_not_called()
    assert events[0][0] == 'error' and events[-1][1]['stop_reason'] == 'error'
    assert data.command_routing.summary()['agent_chat']['observations'] == 0


async def test_setting_is_opt_in_round_trips_and_rejects_non_boolean(hass, chat_home):
    data, _ = chat_home
    assert not GlobalSettings().agentcli_routing_shadow_enabled
    assert GlobalSettings.from_dict(data.store.get_settings().to_dict()).agentcli_routing_shadow_enabled
    assert not GlobalSettings.from_dict({'agentcli_routing_shadow_enabled': 'true'}).agentcli_routing_shadow_enabled
    changes, error = await _validated_settings_patch({'agentcli_routing_shadow_enabled': True}, data, hass, 'test')
    assert error is None and changes == {'agentcli_routing_shadow_enabled': True}
    _, error = await _validated_settings_patch({'agentcli_routing_shadow_enabled': 'true'}, data, hass, 'test')
    assert error.status == 400
    assert 'decision-secret' not in json.dumps(data.command_routing.summary())


@pytest.mark.parametrize('change', ['token', 'settings', 'account'])
async def test_background_decision_discards_stale_chat_observations(hass, chat_home, change):
    data, token = chat_home
    entered, release = asyncio.Event(), asyncio.Event()
    async def classify(self, *args):
        entered.set()
        await release.wait()
        return {'choice': 'light_on'}
    with patch.object(DecisionProvider, 'classify', classify):
        async with asyncio.timeout(2):
            await chat_turn(hass, chat_home)
            await entered.wait()
        pending = data.decision_shadow.task
        if change == 'token': token.revoked = True
        if change == 'settings': await data.store.async_patch_settings(agentcli_routing_shadow_enabled=False)
        if change == 'account':
            store = await agentcli._get_secret_store(hass)
            await store.set_model(data.store.get_settings().decision_provider_id, 'changed')
        release.set()
        await pending
        await asyncio.sleep(0)
    summary = data.decision_shadow.summary()['agent_chat']
    assert summary['choices'] == {}
    assert summary['observations'] == (1 if change == 'account' else 0)
    if change == 'account': assert summary['reasons'] == {'authority_changed': 1}


async def test_no_account_keeps_local_only(hass, chat_home):
    data, _ = chat_home
    await data.store.async_patch_settings(decision_provider_id=None)
    with patch.object(DecisionProvider, 'classify', new_callable=AsyncMock) as classify:
        provider, _, _ = await chat_turn(hass, chat_home)
    provider.stream_turn.assert_called_once()
    classify.assert_not_called()
    assert data.command_routing.summary()['agent_chat']['observations'] == 1
    assert data.decision_shadow.summary()['agent_chat']['observations'] == 0
