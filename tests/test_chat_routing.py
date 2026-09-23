"""Agent Chat first-message routing preserves dispatch, approvals and recovery."""
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
@pytest.mark.parametrize('execute', [False, True])
async def test_changes_during_recognition_stop_before_provider(hass, chat_home, change, execute):
    data, token = chat_home
    cancel = asyncio.Event()
    if execute: await data.store.async_patch_settings(agentcli_routing_mode='local')
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


@pytest.fixture
async def executing_chat(chat_home, hass):
    data, token = chat_home
    token.cap_physical_control = 'allow'
    await data.store.async_patch_settings(agentcli_routing_mode='local', mesa_mode='off')
    hass.states.async_set('light.test_desk', 'off', {
        'friendly_name': 'Desk lamp', 'supported_color_modes': ['brightness']})
    return data, token


@pytest.mark.parametrize('sentence,service,extra', [
    ('turn on the desk lamp', 'turn_on', {}),
    ('turn off the desk lamp', 'turn_off', {}),
    ('set the desk lamp brightness to 50 percent', 'turn_on', {'brightness_pct': 50.0}),
])
async def test_chat_local_dispatches_once_without_either_model(hass, executing_chat, sentence, service, extra):
    from custom_components.phoenix_mcp import mcp_view
    data, token = executing_chat
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call,
          patch.object(DecisionProvider, 'classify', new_callable=AsyncMock) as decision):
        provider, events, messages = await chat_turn(hass, executing_chat, [{'role': 'user', 'content': sentence}])
    call.assert_awaited_once_with('light', service, {'entity_id': ['light.test_desk'], **extra}, blocking=True, return_response=False)
    provider.stream_turn.assert_not_called()
    decision.assert_not_called()
    assert messages[-1] == {'role': 'assistant', 'content': 'Command sent.'}
    assert events[-1] == ('done', {'stop_reason': 'end_turn'})
    assert data.agent_recovery.records(token.id) == []
    assert data.store.get_settings().voice_agent_routing_mode == 'off'
    assert not data.store.get_settings().voice_agent_enabled
    assert data.command_routing.summary()['agent_chat']['candidates'] == 1
    assert data.audit.query(token_id=token.id)[0].client_ip == 'agentcli'


async def test_chat_read_uses_dispatch_and_followup_uses_provider(hass, executing_chat):
    with patch.object(DecisionProvider, 'classify', new_callable=AsyncMock) as decision:
        provider, _, messages = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': 'what is the state of the desk lamp'}])
        provider.stream_turn.assert_not_called()
        assert messages[-1]['content'] == 'The state is off.'
        followup, _, _ = await chat_turn(hass, executing_chat,
            [*messages, {'role': 'user', 'content': 'turn on the desk lamp'}])
    followup.stream_turn.assert_called_once()
    decision.assert_not_called()


@pytest.mark.parametrize('case', ['history', 'non_english', 'missing_language', 'blocks', 'extra_fields'])
async def test_execution_exclusions_preserve_provider(hass, executing_chat, case):
    messages = [{'role': 'user', 'content': 'turn on the desk lamp'}]
    language = 'en'
    if case == 'history': messages.insert(0, {'role': 'assistant', 'content': 'Earlier context'})
    if case == 'non_english': language = 'fr'
    if case == 'missing_language': language = None
    if case == 'blocks': messages[0]['content'] = [{'type': 'text', 'text': 'turn on the desk lamp'}]
    if case == 'extra_fields': messages[0]['attachments'] = []
    with patch.object(chat_routing, 'async_evaluate_command', new_callable=AsyncMock) as local:
        provider, _, _ = await chat_turn(hass, executing_chat, messages, language)
    provider.stream_turn.assert_called_once()
    local.assert_not_called()


@pytest.mark.parametrize('reason', ['no_match', 'unsupported', 'no_target', 'busy', 'timeout', 'sentence_trigger'])
async def test_execution_misses_do_not_spend_background_decisions(hass, executing_chat, reason):
    data, _ = executing_chat
    with (patch.object(chat_routing, 'async_evaluate_command', AsyncMock(return_value={
              'route': 'provider', 'proposal': None, 'reason': reason, 'elapsed_ms': 1})),
          patch.object(data.decision_shadow, 'start') as start):
        provider, _, _ = await chat_turn(hass, executing_chat)
    provider.stream_turn.assert_called_once()
    start.assert_not_called()
    assert data.store.get_settings().agentcli_routing_shadow_enabled


@pytest.mark.parametrize('barrier', ['prohibited', 'read_only', 'confirm'])
@pytest.mark.parametrize('pass_through', [False, True])
async def test_execution_policy_refusal_never_replays(hass, executing_chat, barrier, pass_through):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    data, token = executing_chat
    token.pass_through = pass_through
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': barrier}}},
        default_origin=MetadataOrigin.USER))
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, _ = await chat_turn(hass, executing_chat, [{'role': 'user', 'content': 'turn on the desk lamp'}])
    provider.stream_turn.assert_not_called()
    call.assert_not_called()
    if barrier == 'confirm':
        assert any(name == 'approval_required' for name, _ in events)
    else:
        assert not data.store.get_pending_approvals()
        assert 'could not complete' in next(p['text'] for n, p in events if n == 'assistant_delta')


@pytest.mark.parametrize('change', ['none', 'mode', 'identity', 'scope', 'brightness', 'exposure', 'token'])
async def test_chat_approval_binds_ingress_and_revalidates_before_execution(hass, executing_chat, change):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.const import COMMAND_ROUTING_PLAN_KEY
    from custom_components.phoenix_mcp.command_execution import plan_is_current
    data, token = executing_chat
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}},
        default_origin=MetadataOrigin.USER))
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        provider, events, messages = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': 'set the desk lamp brightness to 50 percent'}])
    provider.stream_turn.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    plan = record['args'][COMMAND_ROUTING_PLAN_KEY]
    assert plan['ingress'] == 'chat'
    pending = next(payload for name, payload in events if name == 'approval_required')
    assert pending['approval_id'] == record['id']
    assert COMMAND_ROUTING_PLAN_KEY not in json.dumps(pending)
    assert 'needs approval' in messages[-1]['content']
    assert not plan_is_current({**plan, 'ingress': 'voice'}, hass, data, token)
    assert not plan_is_current({**plan, 'ingress': 'invalid'}, hass, data, token)
    if change == 'mode': await data.store.async_patch_settings(agentcli_routing_mode='off')
    if change == 'identity': er.async_get(hass).async_update_entity('light.test_desk', name='Replacement')
    if change == 'scope': token.permissions = PermissionTree()
    if change == 'brightness': hass.states.async_set('light.test_desk', 'off', {'supported_color_modes': ['onoff']})
    if change == 'exposure':
        from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
        async_expose_entity(hass, 'conversation', 'light.test_desk', False)
    if change == 'token': token.revoked = True
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


@pytest.mark.parametrize('failure', ['timeout', 'delivery', 'cancel_delivery'])
async def test_chat_command_retains_recovery_through_response_delivery(hass, executing_chat, failure):
    from custom_components.phoenix_mcp import mcp_view
    data, token = executing_chat
    cancel = asyncio.Event()
    async def emit(name, payload):
        if name == 'assistant_delta':
            if failure == 'delivery': raise ConnectionResetError()
            if failure == 'cancel_delivery': cancel.set()
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        if failure == 'timeout': call.side_effect = TimeoutError()
        provider = MagicMock()
        kwargs = dict(hass=hass, data=data, token=token, provider=provider, session=None,
            messages=[{'role': 'user', 'content': 'turn on the desk lamp'}], options={},
            client_ip='agentcli', base_url='http://test', emit=emit, cancel=cancel, routing_language='en')
        if failure == 'delivery':
            with pytest.raises(ConnectionResetError): await agentcli.async_run_agent_turn(**kwargs)
        else:
            await agentcli.async_run_agent_turn(**kwargs)
    call.assert_awaited_once()
    provider.stream_turn.assert_not_called()
    assert data.agent_recovery.blocks(token.id)
    assert len(data.agent_recovery.records(token.id)) == 1


async def test_execution_mode_is_closed_and_default_off(hass, chat_home):
    data, _ = chat_home
    assert GlobalSettings().agentcli_routing_mode == 'off'
    assert GlobalSettings.from_dict({'agentcli_routing_mode': 'local'}).agentcli_routing_mode == 'local'
    for invalid in ('hybrid', 'local_shadow', True, [], None):
        assert GlobalSettings.from_dict({'agentcli_routing_mode': invalid}).agentcli_routing_mode == 'off'
        _, error = await _validated_settings_patch({'agentcli_routing_mode': invalid}, data, hass, 'test')
        assert error.status == 400
    change, error = await _validated_settings_patch({'agentcli_routing_mode': 'local'}, data, hass, 'test')
    assert error is None and change == {'agentcli_routing_mode': 'local'}


@pytest.mark.parametrize('change', ['hidden', 'unregistered', 'unavailable'])
async def test_ineligible_local_target_falls_back_before_dispatch(hass, executing_chat, change):
    from custom_components.phoenix_mcp import mcp_view
    data, _ = executing_chat
    registry = er.async_get(hass)
    if change == 'hidden': registry.async_update_entity('light.test_desk', hidden_by=er.RegistryEntryHider.USER)
    if change == 'unregistered': registry.async_remove('light.test_desk')
    if change == 'unavailable': hass.states.async_set('light.test_desk', 'unavailable')
    with (patch.object(mcp_view, '_dispatch_mcp') as dispatch,
          patch.object(data.decision_shadow, 'start') as decision):
        provider, _, _ = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': 'turn on the desk lamp'}])
    provider.stream_turn.assert_called_once()
    dispatch.assert_not_called()
    decision.assert_not_called()


async def test_cancellation_during_command_reconcile_prevents_dispatch(hass, executing_chat):
    from custom_components.phoenix_mcp import mcp_view
    data, _ = executing_chat
    cancel = asyncio.Event()
    original = data.agent_recovery.reconcile
    calls = 0
    async def reconcile(*args):
        nonlocal calls
        calls += 1
        await original(*args)
        if calls == 2: cancel.set()
    with (patch.object(data.agent_recovery, 'reconcile', reconcile),
          patch.object(mcp_view, '_dispatch_mcp') as dispatch):
        provider, events, _ = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': 'turn on the desk lamp'}], cancel=cancel)
    provider.stream_turn.assert_not_called()
    dispatch.assert_not_called()
    assert events[0][1]['code'] == 'cancelled'
