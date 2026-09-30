"""Agent Chat routing preserves conversation context, dispatch and recovery."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
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
async def chat_home(hass, token_store, immediate_command_observation):
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


@pytest.mark.parametrize('category,domain,attributes,service,field,value', [
    ('light_brightness', 'light', {'supported_color_modes': ['brightness']}, 'turn_on', 'brightness_pct', 37),
    ('media_volume', 'media_player', {'supported_features': MediaPlayerEntityFeature.VOLUME_SET}, 'volume_set', 'volume_level', 0.37),
    ('cover_position', 'cover', {'supported_features': CoverEntityFeature.SET_POSITION}, 'set_cover_position', 'position', 37),
])
async def test_chat_executes_qualified_source_checked_percentages(hass, chat_home, category, domain, attributes, service, field, value):
    from custom_components.phoenix_mcp import mcp_view
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    data, token = chat_home
    store = hass.data['_phoenix_agentcli_store']
    await store.set_model(data.store.get_settings().decision_provider_id, 'jev-latest')
    await data.store.async_patch_settings(agentcli_routing_mode='hybrid', mesa_mode='off')
    token.cap_physical_control = 'allow'
    token.permissions.domains[domain] = PermissionNode(state='GREEN')
    entry = er.async_get(hass).async_get_or_create(domain, 'test', 'numeric_device', original_name='Test device')
    hass.states.async_set(entry.entity_id, 'off', attributes)
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    async def response(session, method, path, timeout, payload):
        assert set(payload['questions']) == {'route', 'target', 'percentage', 'summary', 'summary_scope'}
        target = next(key for key, item in payload['state']['targets'].items() if item['entity_id'] == entry.entity_id)
        selected = {'route': category, 'target': target, 'percentage': '37'}
        return {'model': 'jev-1.13.0', 'answers': {
            name: {'type': 'choice', 'choice': selected.get(name, 'none'), 'confidence': 1,
                   'probabilities': {key: float(key == selected.get(name, 'none')) for key in question['criteria']}}
            for name, question in payload['questions'].items()}}
    with (patch.object(DecisionProvider, '_request', side_effect=response) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, _ = await chat_turn(hass, chat_home, [
            {'role': 'user', 'content': 'I would like Test device at thirty-seven percent now.'}])
    remote.assert_awaited_once()
    provider.stream_turn.assert_not_called()
    call.assert_awaited_once_with(domain, service, {'entity_id': [entry.entity_id], field: value},
                                blocking=True, return_response=False)


async def test_decision_does_not_delay_or_replace_provider_and_keeps_only_counts(hass, chat_home):
    data, _ = chat_home
    entered, release = asyncio.Event(), asyncio.Event()
    async def classify(self, session, sentence, timeout, language):
        assert language == "en"
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


@pytest.mark.parametrize('case', ['off', 'history', 'unsupported_language', 'missing_language', 'blocks', 'extra_fields'])
async def test_excluded_messages_skip_all_observation(hass, chat_home, case):
    data, _ = chat_home
    messages = [{'role': 'user', 'content': 'command'}]
    language = 'en'
    if case == 'off': await data.store.async_patch_settings(agentcli_routing_shadow_enabled=False)
    if case == 'history': messages = [{'role': 'assistant', 'content': 'context'}, *messages]
    if case == 'unsupported_language': language = 'zz'
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
        assert start.call_args.kwargs == {'observe_chat': True, 'ingress': 'chat'}


@pytest.mark.parametrize('change', ['token', 'settings', 'cancel'])
@pytest.mark.parametrize('execute', [False, True])
async def test_changes_during_recognition_stop_before_provider(hass, chat_home, change, execute):
    data, token = chat_home
    cancel = asyncio.Event()
    if execute: await data.store.async_patch_settings(agentcli_routing_mode='local')
    async def local(*args, **kwargs):
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
@pytest.mark.parametrize('routing_mode', ['local', 'auto'])
async def test_chat_local_dispatches_once_without_either_model(hass, executing_chat, sentence, service, extra, routing_mode):
    await executing_chat[0].store.async_patch_settings(agentcli_routing_mode=routing_mode)
    from custom_components.phoenix_mcp import mcp_view
    data, token = executing_chat
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call,
          patch.object(DecisionProvider, 'classify', new_callable=AsyncMock) as decision):
        provider, events, messages = await chat_turn(hass, executing_chat, [{'role': 'user', 'content': sentence}])
    call.assert_awaited_once_with('light', service, {'entity_id': ['light.test_desk'], **extra}, blocking=True, return_response=False)
    provider.stream_turn.assert_not_called()
    decision.assert_not_called()
    assert messages[-1] == {'role': 'assistant', 'content': (
        'Home Assistant reports the requested state.' if service == 'turn_off' else
        'Command sent. Home Assistant has not yet confirmed the requested state.')}
    assert events[-1] == ('done', {'stop_reason': 'end_turn'})
    assert data.agent_recovery.records(token.id) == []
    assert data.store.get_settings().voice_agent_routing_mode == 'off'
    assert not data.store.get_settings().voice_agent_enabled
    assert data.command_routing.summary()['agent_chat']['candidates'] == 1
    assert data.audit.query(token_id=token.id)[0].client_ip == 'agentcli'


async def test_chat_read_then_explicit_command_uses_local_routing(hass, executing_chat):
    from custom_components.phoenix_mcp import mcp_view
    with patch.object(DecisionProvider, 'classify', new_callable=AsyncMock) as decision:
        provider, _, messages = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': 'what is the state of the desk lamp'}])
        provider.stream_turn.assert_not_called()
        assert messages[-1]['content'] == 'The state is Off.'
        with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
              patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
            followup, _, result = await chat_turn(hass, executing_chat,
                [*messages, {'role': 'user', 'content': 'turn on the desk lamp'}])
    followup.stream_turn.assert_not_called()
    call.assert_awaited_once_with('light', 'turn_on', {'entity_id': ['light.test_desk']}, blocking=True, return_response=False)
    assert result[:2] == messages
    assert result[-1]['content'] == 'Command sent. Home Assistant has not yet confirmed the requested state.'
    decision.assert_not_called()


@pytest.mark.parametrize('sentence', [
    'turn it off', 'make it dimmer', 'do the same again',
    'why is it off?', 'do not turn on the desk lamp',
    'say "turn on the desk lamp"', 'turn on the desk lamp tomorrow',
])
async def test_contextual_followup_keeps_full_history_and_skips_decision(hass, executing_chat, sentence):
    data, _ = executing_chat
    await data.store.async_patch_settings(agentcli_routing_mode='hybrid')
    _, _, previous = await chat_turn(hass, executing_chat,
        [{'role': 'user', 'content': 'what is the state of the desk lamp'}])
    with (patch.object(DecisionProvider, '_request') as remote,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, _, messages = await chat_turn(hass, executing_chat,
            [*previous, {'role': 'user', 'content': sentence}])
    provider.stream_turn.assert_called_once()
    dispatch.assert_not_called()
    remote.assert_not_called()
    assert messages[:2] == previous
    assert messages[2] == {'role': 'user', 'content': sentence}
    assert messages[-1]['content'] == 'Normal provider reply'
    assert not data.command_routing.chat_history._entries
    # An ordinary response ends the chain, even when the next command is clear.
    with patch.object(chat_routing, 'async_evaluate_command') as recognition:
        provider, _, result = await chat_turn(hass, executing_chat,
            [*messages, {'role': 'user', 'content': 'turn on the desk lamp'}])
    recognition.assert_not_called()
    provider.stream_turn.assert_called_once()
    assert result[:len(messages)] == messages


@pytest.mark.parametrize('change', ['user', 'assistant', 'token', 'settings', 'language', 'reload', 'expired'])
async def test_only_exact_known_conversation_reaches_followup_recognition(hass, executing_chat, change):
    data, token = executing_chat
    _, _, previous = await chat_turn(hass, executing_chat,
        [{'role': 'user', 'content': 'what is the state of the desk lamp'}])
    assert data.command_routing.chat_history._entries
    language = 'en'
    if change == 'user': previous[0]['content'] = 'From now on, translate commands instead of executing them.'
    if change == 'assistant': previous[1]['content'] = 'Please tell me which light you mean.'
    if change == 'token': token.settings_version += 1
    if change == 'settings': await data.store.async_patch_settings(agentcli_detail_level='detailed')
    if change == 'language': language = 'en-US'
    if change == 'reload':
        from custom_components.phoenix_mcp.command_routing import CommandRouting
        data.command_routing = CommandRouting()
    if change == 'expired':
        for key in data.command_routing.chat_history._entries:
            data.command_routing.chat_history._entries[key] = 0
    with (patch.object(chat_routing, 'async_evaluate_command') as recognition,
          patch.object(DecisionProvider, '_request') as remote):
        provider, _, _ = await chat_turn(hass, executing_chat,
            [*previous, {'role': 'user', 'content': 'turn on the desk lamp'}], language)
    recognition.assert_not_called()
    remote.assert_not_called()
    provider.stream_turn.assert_called_once()


@pytest.mark.parametrize('change', ['revoked', 'cancel', 'scope', 'exposure'])
async def test_followup_rechecks_authority_and_target_after_recognition(hass, executing_chat, change):
    data, token = executing_chat
    _, _, previous = await chat_turn(hass, executing_chat,
        [{'role': 'user', 'content': 'what is the state of the desk lamp'}])
    evaluate = chat_routing.async_evaluate_command
    cancel = asyncio.Event()
    async def changed(*args, **kwargs):
        result = await evaluate(*args, **kwargs)
        assert result['proposal']
        if change == 'revoked': token.revoked = True
        if change == 'cancel': cancel.set()
        if change == 'scope': token.permissions = PermissionTree()
        if change == 'exposure':
            from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
            async_expose_entity(hass, 'conversation', 'light.test_desk', False)
        return result
    with (patch.object(chat_routing, 'async_evaluate_command', changed),
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, events, _ = await chat_turn(hass, executing_chat,
            [*previous, {'role': 'user', 'content': 'turn on the desk lamp'}], cancel=cancel)
    dispatch.assert_not_called()
    if change in ('revoked', 'cancel'):
        provider.stream_turn.assert_not_called()
        assert events[0][0] == 'error'
    else:
        provider.stream_turn.assert_called_once()
    assert not data.command_routing.chat_history._entries


@pytest.mark.parametrize('case', ['history', 'unsupported_language', 'missing_language', 'blocks', 'extra_fields'])
@pytest.mark.parametrize('mode', ['local', 'hybrid', 'auto'])
async def test_execution_exclusions_preserve_provider(hass, executing_chat, case, mode):
    await executing_chat[0].store.async_patch_settings(agentcli_routing_mode=mode)
    messages = [{'role': 'user', 'content': 'turn on the desk lamp'}]
    language = 'en'
    if case == 'history': messages.insert(0, {'role': 'assistant', 'content': 'Earlier context'})
    if case == 'unsupported_language': language = 'zz'
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
    assert not data.command_routing.chat_history._entries


@pytest.mark.parametrize('change', ['none', 'mode', 'identity', 'scope', 'brightness', 'exposure', 'token'])
@pytest.mark.parametrize('followup', [False, True])
async def test_chat_approval_binds_ingress_and_revalidates_before_execution(hass, executing_chat, change, followup):
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
    history = []
    if followup:
        _, _, history = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': 'what is the state of the desk lamp'}])
        assert data.command_routing.chat_history._entries
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        provider, events, messages = await chat_turn(hass, executing_chat,
            [*history, {'role': 'user', 'content': 'set the desk lamp brightness to 50 percent'}])
    provider.stream_turn.assert_not_called()
    assert not data.command_routing.chat_history._entries
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


@pytest.mark.parametrize('failure', ['timeout', 'delivery', 'cancel_delivery', 'done_delivery', 'done_cancel'])
async def test_chat_command_retains_recovery_through_response_delivery(hass, executing_chat, failure):
    from custom_components.phoenix_mcp import mcp_view
    data, token = executing_chat
    cancel = asyncio.Event()
    async def emit(name, payload):
        if name == 'assistant_delta':
            if failure == 'delivery': raise ConnectionResetError()
            if failure == 'cancel_delivery': cancel.set()
        if name == 'done':
            if failure == 'done_delivery': raise ConnectionResetError()
            if failure == 'done_cancel': cancel.set()
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        if failure == 'timeout': call.side_effect = TimeoutError()
        provider = MagicMock()
        kwargs = dict(hass=hass, data=data, token=token, provider=provider, session=None,
            messages=[{'role': 'user', 'content': 'turn on the desk lamp'}], options={},
            client_ip='agentcli', base_url='http://test', emit=emit, cancel=cancel, routing_language='en')
        if failure in ('delivery', 'done_delivery'):
            with pytest.raises(ConnectionResetError): await agentcli.async_run_agent_turn(**kwargs)
        else:
            await agentcli.async_run_agent_turn(**kwargs)
    call.assert_awaited_once()
    provider.stream_turn.assert_not_called()
    assert data.agent_recovery.blocks(token.id)
    assert len(data.agent_recovery.records(token.id)) == 1
    assert not data.command_routing.chat_history._entries


async def test_execution_mode_is_closed_and_default_off(hass, chat_home):
    data, _ = chat_home
    assert GlobalSettings().agentcli_routing_mode == 'off'
    assert GlobalSettings.from_dict({'agentcli_routing_mode': 'local'}).agentcli_routing_mode == 'off'
    for invalid in ('unknown', 'local_shadow', True, [], None):
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


@pytest.fixture
async def hybrid_chat(executing_chat):
    data, _ = executing_chat
    await data.store.async_patch_settings(agentcli_routing_mode='hybrid')
    return executing_chat


def chat_decision_wire(payload, category='light_on', target='t0', percent=50):
    choices = {'route': category, 'target': target,
               'brightness_tens': str(percent // 10), 'brightness_units': str(percent % 10)}
    return {'model': 'test-model', 'answers': {
        question: {'type': 'choice', 'choice': choices.get(question, 'none'), 'confidence': 0.1,
                   'probabilities': {key: float(key == choices.get(question, 'none')) for key in spec['criteria']}}
        for question, spec in payload['questions'].items()}}


async def decision_chat_turn(hass, home, *, category='light_on', percent=50, request=None,
                             sentence='Please illuminate the desk lamp', cancel=None, language='en'):
    async def respond(session, method, path, timeout, payload):
        return chat_decision_wire(payload, category, percent=percent)
    with patch.object(DecisionProvider, '_request', AsyncMock(side_effect=request or respond)) as remote:
        provider, events, messages = await chat_turn(hass, home,
            [{'role': 'user', 'content': sentence}], cancel=cancel, language=language)
    return provider, events, messages, remote


@pytest.mark.parametrize('category,percent,service', [
    ('light_on', 50, 'turn_on'), ('light_off', 50, 'turn_off'),
    ('state_query', 50, None),
])
@pytest.mark.parametrize('routing_mode', ['hybrid', 'auto'])
async def test_chat_hybrid_uses_real_decision_validation_and_one_dispatch(hass, hybrid_chat, category, percent, service, routing_mode):
    await hybrid_chat[0].store.async_patch_settings(agentcli_routing_mode=routing_mode)
    from custom_components.phoenix_mcp import mcp_view
    data, token = hybrid_chat
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, messages, remote = await decision_chat_turn(hass, hybrid_chat, category=category, percent=percent)
    assert remote.await_count == 2
    provider.stream_turn.assert_not_called()
    if service:
        extra = {'brightness_pct': percent} if category == 'light_brightness' else {}
        call.assert_awaited_once_with('light', service, {'entity_id': ['light.test_desk'], **extra}, blocking=True, return_response=False)
    else:
        call.assert_not_called()
    assert messages[-1]['content'] == (
        'Home Assistant reports the requested state.' if service == 'turn_off' else
        'Command sent. Home Assistant has not yet confirmed the requested state.' if service else 'The state is Off.')
    assert events[-1] == ('done', {'stop_reason': 'end_turn'})
    assert data.agent_recovery.records(token.id) == []
    assert data.decision_shadow.summary()['chat_hybrid'] == {'evaluations': 1, 'reasons': {'plan_ready': 1}}
    assert data.decision_shadow.summary()['hybrid']['evaluations'] == 0
    assert data.decision_shadow.summary()['agent_chat']['observations'] == 0
    assert not data.store.get_settings().voice_agent_enabled
    # Model-selected commands cannot establish a strict local history chain.
    assert not data.command_routing.chat_history._entries


async def test_chat_hybrid_local_match_spends_no_decision_call(hass, hybrid_chat):
    provider, _, messages, remote = await decision_chat_turn(hass, hybrid_chat, sentence='what is the state of the desk lamp')
    assert messages[-1]['content'] == 'The state is Off.'
    provider.stream_turn.assert_not_called()
    remote.assert_not_called()
    assert hybrid_chat[0].decision_shadow.summary()['chat_hybrid']['evaluations'] == 0


@pytest.mark.parametrize('case,calls,reason', [
    ('provider', 1, 'provider_fallback'), ('decline', 2, 'target_declined'),
    ('low_category', 1, 'category_low_probability'), ('low_target', 2, 'target_low_probability'),
    ('brightness_declined', 1, 'numeric_unverified'), ('malformed', 1, 'malformed'),
    ('timeout', 1, 'timeout'), ('unavailable', 1, 'unavailable'), ('no_scope', 1, 'no_target'),
    ('local_timeout', 0, 'local_unavailable'), ('trigger', 0, 'local_unavailable'), ('busy', 0, 'busy'),
])
async def test_chat_hybrid_falls_back_only_before_dispatch(hass, hybrid_chat, case, calls, reason):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.decision_provider import DecisionError
    data, token = hybrid_chat
    async def respond(session, method, path, timeout, payload):
        if case in ('timeout', 'unavailable'): raise DecisionError(case)
        if case == 'malformed': return {'model': 'test-model', 'answers': {}}
        answer = chat_decision_wire(payload, 'provider' if case == 'provider' else
            'light_brightness' if case == 'brightness_declined' else 'light_on',
            'none' if case == 'decline' else 't0')
        question = {'low_category': 'route', 'low_target': 'target', 'brightness_declined': 'brightness_tens'}.get(case)
        if question in answer['answers']:
            value = answer['answers'][question]
            value['probabilities'] = {k: 0.0 for k in value['probabilities']}
            value['probabilities'][value['choice']] = 0.8
            value['probabilities']['provider' if question == 'route' else 'none'] = 0.2
        return answer
    if case == 'no_scope': token.permissions = PermissionTree()
    busy = None
    if case == 'busy':
        busy = asyncio.create_task(asyncio.Event().wait())
        data.decision_shadow.task = busy
    local = {'route': 'provider', 'proposal': None, 'elapsed_ms': 1,
             'reason': 'timeout' if case == 'local_timeout' else 'sentence_trigger' if case == 'trigger' else 'no_match'}
    with (patch.object(chat_routing, 'async_evaluate_command', AsyncMock(return_value=local)),
          patch.object(mcp_view, '_dispatch_mcp') as dispatch):
        provider, _, _, remote = await decision_chat_turn(hass, hybrid_chat, request=respond)
    provider.stream_turn.assert_called_once()
    dispatch.assert_not_called()
    assert remote.await_count == calls
    assert data.decision_shadow.summary()['chat_hybrid'] == {'evaluations': 1, 'reasons': {reason: 1}}
    assert not data.store.get_pending_approvals()
    if busy: assert not busy.done() and data.decision_shadow.task is busy


@pytest.mark.parametrize('change', ['account', 'token', 'mode', 'catalog'])
async def test_chat_hybrid_rechecks_after_network_wait(hass, hybrid_chat, change):
    from custom_components.phoenix_mcp import mcp_view
    data, token = hybrid_chat
    async def respond(session, method, path, timeout, payload):
        if change == 'account':
            store = await agentcli._get_secret_store(hass)
            await store.set_model(data.store.get_settings().decision_provider_id, 'changed')
        if change == 'token': token.revoked = True
        if change == 'mode': await data.store.async_patch_settings(agentcli_routing_mode='off')
        if change == 'catalog' and 'target' in payload['questions']:
            er.async_get(hass).async_update_entity('light.test_desk', name='Changed')
        return chat_decision_wire(payload)
    with patch.object(mcp_view, '_dispatch_mcp') as dispatch:
        provider, events, _, _ = await decision_chat_turn(hass, hybrid_chat, request=respond)
    dispatch.assert_not_called()
    if change == 'catalog':
        provider.stream_turn.assert_called_once()
        assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'targets_changed': 1}
    else:
        provider.stream_turn.assert_not_called()
        assert events[0][0] == 'error'
        assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'authority_changed': 1}


@pytest.mark.parametrize('kind', ['event', 'task', 'owner'])
async def test_chat_hybrid_cancellation_closes_owned_job_and_never_dispatches(hass, hybrid_chat, kind):
    from custom_components.phoenix_mcp import mcp_view
    data, _ = hybrid_chat
    entered, closed, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def request(*args):
        entered.set()
        try: await asyncio.Event().wait()
        finally: closed.set()
    with patch.object(mcp_view, '_dispatch_mcp') as dispatch:
        task = asyncio.create_task(decision_chat_turn(hass, hybrid_chat, request=request, cancel=cancel))
        await entered.wait()
        if kind == 'event': cancel.set()
        if kind == 'task': task.cancel()
        if kind == 'owner': data.decision_shadow.cancel()
        if kind == 'task':
            with pytest.raises(asyncio.CancelledError): await task
        else:
            async with asyncio.timeout(1): provider, events, _, _ = await task
            provider.stream_turn.assert_not_called()
            assert events[0][0] == 'error'
            assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'cancelled': 1}
    dispatch.assert_not_called()
    assert closed.is_set() and data.decision_shadow.task is None


@pytest.mark.parametrize('change', ['none', 'account', 'deleted', 'mode', 'new_candidate', 'alias'])
@pytest.mark.parametrize('routing_mode', ['hybrid', 'auto'])
async def test_chat_hybrid_saved_approval_revalidates_decision_constraints(hass, hybrid_chat, change, routing_mode):
    await hybrid_chat[0].store.async_patch_settings(agentcli_routing_mode=routing_mode)
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.const import COMMAND_ROUTING_PLAN_KEY
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    data, token = hybrid_chat
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}}, default_origin=MetadataOrigin.USER))
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        provider, events, _, _ = await decision_chat_turn(hass, hybrid_chat, category='light_off')
    provider.stream_turn.assert_not_called()
    assert any(name == 'approval_required' for name, _ in events)
    record = data.store.get_pending_approvals()[0]
    plan = record['args'][COMMAND_ROUTING_PLAN_KEY]
    assert plan['ingress'] == 'chat' and plan['decision_category'] == 'light_off'
    assert record['args']['service_data'] == {}
    assert 'secret' not in json.dumps(plan)
    store = await agentcli._get_secret_store(hass)
    account = data.store.get_settings().decision_provider_id
    if change == 'account': await store.set_model(account, 'changed')
    if change == 'deleted': await store.delete(account)
    if change == 'mode': await data.store.async_patch_settings(agentcli_routing_mode='local')
    if change == 'alias': er.async_get(hass).async_update_entity('light.test_desk', aliases={'New alias'})
    if change == 'new_candidate':
        entry = er.async_get(hass).async_get_or_create('light', 'test', 'another', original_name='Other lamp')
        hass.states.async_set(entry.entity_id, 'off', {'supported_color_modes': ['brightness']})
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


async def test_chat_hybrid_requires_decision_account_and_clear_disables_mode(hass, hybrid_chat):
    data, _ = hybrid_chat
    changes, error = await _validated_settings_patch({'agentcli_routing_mode': 'hybrid'}, data, hass, 'test')
    assert error is None and changes == {'agentcli_routing_mode': 'hybrid'}
    _, error = await _validated_settings_patch({'agentcli_routing_mode': 'hybrid', 'decision_provider_id': None}, data, hass, 'test')
    assert error.status == 400
    changes, error = await _validated_settings_patch({'decision_provider_id': None}, data, hass, 'test')
    assert error is None and changes['agentcli_routing_mode'] == 'off'


@pytest.mark.parametrize('failure', ['timeout', 'delivery', 'cancel_delivery'])
async def test_chat_hybrid_recovery_covers_effect_and_response_delivery(hass, hybrid_chat, failure):
    from custom_components.phoenix_mcp import mcp_view
    data, token = hybrid_chat
    cancel = asyncio.Event()
    provider = MagicMock()
    async def emit(name, payload):
        if name == 'assistant_delta':
            if failure == 'delivery': raise ConnectionResetError()
            if failure == 'cancel_delivery': cancel.set()
    async def respond(session, method, path, timeout, payload):
        return chat_decision_wire(payload)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(DecisionProvider, '_request', AsyncMock(side_effect=respond)),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        if failure == 'timeout': call.side_effect = TimeoutError()
        kwargs = dict(hass=hass, data=data, token=token, provider=provider, session=None,
            messages=[{'role': 'user', 'content': 'Please illuminate the desk lamp'}], options={},
            client_ip='agentcli', base_url='http://test', emit=emit, cancel=cancel, routing_language='en')
        if failure == 'delivery':
            with pytest.raises(ConnectionResetError): await agentcli.async_run_agent_turn(**kwargs)
        else:
            await agentcli.async_run_agent_turn(**kwargs)
        assert data.agent_recovery.blocks(token.id)
        await agentcli.async_run_agent_turn(**{**kwargs, 'cancel': asyncio.Event()})
    call.assert_awaited_once()
    provider.stream_turn.assert_not_called()
    assert len(data.agent_recovery.records(token.id)) == 1


@pytest.mark.parametrize('language,alias,sentence,reply', [
    ('zh-Hans', '台灯', '打开台灯', '命令已发送。Home Assistant 尚未确认请求的状态。'),
    ('es', 'Lámpara', 'enciende la lámpara', 'Comando enviado. Home Assistant aún no ha confirmado el estado solicitado.'),
])
@pytest.mark.parametrize('routing_mode', ['local', 'auto'])
async def test_selected_language_local_chat_executes_without_a_model(hass, executing_chat, language, alias, sentence, reply, routing_mode):
    await executing_chat[0].store.async_patch_settings(agentcli_routing_mode=routing_mode)
    from custom_components.phoenix_mcp import mcp_view
    data, _ = executing_chat
    await data.store.async_patch_settings(command_routing_languages=[] if routing_mode == "auto" else [language])
    er.async_get(hass).async_update_entity('light.test_desk', aliases={alias})
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call,
          patch.object(DecisionProvider, '_request') as remote):
        provider, _, messages = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': sentence}], language=language)
    assert messages[-1]['content'] == reply
    call.assert_awaited_once_with('light', 'turn_on', {'entity_id': ['light.test_desk']}, blocking=True, return_response=False)
    provider.stream_turn.assert_not_called()
    remote.assert_not_called()


@pytest.mark.parametrize('language,sentence,pending_text', [
    ('zh-Hans', '请打开台灯', '此指令需要在 Phoenix MCP 面板中审批。'),
    ('es', 'enciende la lámpara', 'Esta orden requiere aprobación en el panel de Phoenix MCP.'),
])
@pytest.mark.parametrize('remove_language', [False, True])
async def test_multilingual_hybrid_chat_approval_and_language_revalidation(hass, hybrid_chat, language, sentence, pending_text, remove_language):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    data, token = hybrid_chat
    er.async_get(hass).async_update_entity('light.test_desk', aliases={'台灯', 'lámpara'})
    await data.store.async_patch_settings(command_routing_languages=[language], mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}}, default_origin=MetadataOrigin.USER))
    local = {'reason': 'no_match', 'proposal': None, 'elapsed_ms': 1, 'route': 'provider'}
    with (patch.object(chat_routing, 'async_evaluate_command', AsyncMock(return_value=local)),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, messages, remote = await decision_chat_turn(hass, hybrid_chat,
            category='light_on', sentence=sentence, language=language)
        assert pending_text in messages[-1]['content']
        assert any(name == 'approval_required' for name, _ in events)
        assert remote.await_count == 2
        assert all(args.args[4]['state']['language'] == ('zh-CN' if language == 'zh-Hans' else 'es') for args in remote.await_args_list)
        call.assert_not_called()
        provider.stream_turn.assert_not_called()
        record = data.store.get_pending_approvals()[0]
        if remove_language:
            await data.store.async_patch_settings(command_routing_languages=[])
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('denied' if remove_language else 'allowed')
    assert call.await_count == (0 if remove_language else 1)


@pytest.mark.parametrize('language', ['zh-Hans', 'es', 'fr', 'unknown'])
async def test_unselected_chat_language_skips_recognition_and_decision(hass, hybrid_chat, language):
    data, _ = hybrid_chat
    await data.store.async_patch_settings(command_routing_languages=[])
    with (patch.object(chat_routing, 'async_evaluate_command') as local,
          patch.object(DecisionProvider, '_request') as remote):
        provider, _, _ = await chat_turn(hass, hybrid_chat, language=language)
    local.assert_not_called()
    remote.assert_not_called()
    provider.stream_turn.assert_called_once()


async def test_auto_account_clear_keeps_local_routing_and_stops_remote_calls(hass, executing_chat):
    data, _ = executing_chat
    changes, error = await _validated_settings_patch({'agentcli_routing_mode': 'auto'}, data, hass, 'test')
    assert error is None
    await data.store.async_patch_settings(**changes)
    assert not data.store.get_settings().agentcli_routing_shadow_enabled
    changes, error = await _validated_settings_patch({'decision_provider_id': None}, data, hass, 'test')
    assert error is None
    await data.store.async_patch_settings(**changes)
    assert data.store.get_settings().agentcli_routing_mode == 'auto'
    with patch.object(DecisionProvider, '_request') as remote:
        provider, _, _ = await chat_turn(hass, executing_chat)
    provider.stream_turn.assert_called_once()
    remote.assert_not_called()
    assert data.command_routing.summary()['agent_chat']['observations'] == 1
    changes, error = await _validated_settings_patch({'agentcli_routing_mode': 'off'}, data, hass, 'test')
    assert error is None
    await data.store.async_patch_settings(**changes)
    with patch.object(chat_routing, 'async_evaluate_command') as local:
        provider, _, _ = await chat_turn(hass, executing_chat)
    provider.stream_turn.assert_called_once()
    local.assert_not_called()


@pytest.mark.parametrize('language', ['en', 'zh-Hans', 'es'])
@pytest.mark.parametrize('routing_mode', ['hybrid', 'auto'])
async def test_numeric_chat_automatically_falls_back_before_target_request(hass, hybrid_chat, language, routing_mode):
    data, _ = hybrid_chat
    await data.store.async_patch_settings(agentcli_routing_mode=routing_mode, command_routing_languages=[language])
    local = {'reason': 'no_match', 'proposal': None, 'elapsed_ms': 1, 'route': 'provider'}
    with (patch.object(chat_routing, 'async_evaluate_command', AsyncMock(return_value=local)),
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, events, messages, remote = await decision_chat_turn(
            hass, hybrid_chat, category='light_brightness', percent=55,
            sentence='set the desk lamp to 50 percent', language=language)
    provider.stream_turn.assert_called_once()
    assert remote.await_count == 1
    dispatch.assert_not_called()
    assert not data.store.get_pending_approvals()
    assert not any(name == 'approval_required' for name, _ in events)
    assert messages[-1]['content'] == 'Normal provider reply'
    assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'numeric_unverified': 1}


CONTEXT_HISTORY = [
    {'role': 'user', 'content': 'How does brightness work?'},
    {'role': 'assistant', 'content': 'Brightness is expressed as a percentage.'},
]


def context_response(*, model='jev-1.13.0', decline=False, probability=1):
    def choice(selected, other):
        return {'type': 'choice', 'choice': selected, 'confidence': probability,
                'probabilities': {selected: probability, other: 1-probability}}
    return {'model': model, 'answers': {
        'request': choice('immediate', 'contextual'),
        'constraints': choice('restricted', 'clear') if decline else choice('clear', 'restricted'),
    }}


@pytest.fixture
async def context_chat(hass, executing_chat):
    data, _ = executing_chat
    # These exercise execution gates, not executor scheduling under a short budget.
    await data.store.async_patch_settings(agentcli_routing_mode='auto', command_routing_timeout_ms=5000)
    store = await agentcli._get_secret_store(hass)
    await store.set_model(data.store.get_settings().decision_provider_id, 'jev-latest')
    return executing_chat


def context_messages(sentence='set the desk lamp brightness to 37 percent'):
    return [dict(message) for message in CONTEXT_HISTORY] + [{'role': 'user', 'content': sentence}]


async def test_mixed_chat_routes_fixed_numeric_command_and_preserves_history(hass, context_chat):
    from custom_components.phoenix_mcp import mcp_view
    data, token = context_chat
    messages = context_messages()
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response())) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, result = await chat_turn(hass, context_chat, messages)
    provider.stream_turn.assert_not_called()
    remote.assert_awaited_once()
    state = remote.await_args.args[4]['state']
    assert state['history'] == CONTEXT_HISTORY
    assert state['candidate']['brightness_pct'] == 37
    call.assert_awaited_once_with('light', 'turn_on', {'entity_id': ['light.test_desk'], 'brightness_pct': 37},
                                 blocking=True, return_response=False)
    assert result[:2] == CONTEXT_HISTORY
    assert result[-1]['content'] == 'Command sent. Home Assistant has not yet confirmed the requested state.'
    assert events[-1] == ('done', {'stop_reason': 'end_turn'})
    assert not data.command_routing.chat_history._entries
    assert not data.agent_recovery.records(token.id)
    assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'plan_ready': 1}


async def test_mixed_history_requires_review_again_after_success(hass, context_chat):
    data, _ = context_chat
    with patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response())) as remote:
        provider, _, history = await chat_turn(hass, context_chat, context_messages('what is the state of the desk lamp'))
        provider.stream_turn.assert_not_called()
        assert not data.command_routing.chat_history._entries
        provider, _, history = await chat_turn(hass, context_chat,
            [*history, {'role': 'user', 'content': 'what is the state of the desk lamp'}])
    provider.stream_turn.assert_not_called()
    assert remote.await_count == 2
    assert remote.await_args.args[4]['state']['history'] == history[:-2]
    assert history[:2] == CONTEXT_HISTORY and not data.command_routing.chat_history._entries


async def test_verified_local_chain_still_skips_context_provider(hass, context_chat):
    with patch.object(DecisionProvider, '_request') as remote:
        _, _, history = await chat_turn(hass, context_chat,
            [{'role': 'user', 'content': 'what is the state of the desk lamp'}])
        provider, _, _ = await chat_turn(hass, context_chat,
            [*history, {'role': 'user', 'content': 'what is the state of the desk lamp'}])
    provider.stream_turn.assert_not_called()
    remote.assert_not_called()


@pytest.mark.parametrize('case', ['decline', 'uncertain', 'unknown_revision', 'malformed', 'timeout',
    'local_miss', 'secret', 'tool_history', 'oversized', 'language', 'account', 'endpoint', 'configured_revision', 'local_mode'])
async def test_context_fallback_keeps_the_full_conversation(hass, context_chat, case):
    from custom_components.phoenix_mcp.decision_provider import DecisionError
    data, _ = context_chat
    messages = context_messages()
    response = context_response()
    language = 'en'
    if case == 'decline': response = context_response(decline=True)
    if case == 'uncertain': response = context_response(probability=.89)
    if case == 'unknown_revision': response = context_response(model='jev-unreviewed')
    if case == 'malformed': del response['answers']['constraints']
    if case == 'local_miss': messages[-1]['content'] = 'make it dimmer'
    if case == 'secret': messages[0]['content'] = 'password: private-test-value'
    if case == 'tool_history': messages[1]['tool_calls'] = []
    if case == 'oversized': messages[0]['content'] = 'x' * 17000
    if case == 'language': language = 'zz'
    if case == 'account':
        store = await agentcli._get_secret_store(hass)
        account = await store.add('typesafe', {'base_url': 'http://example.test/v1', 'model': 'unqualified-model'})
        await data.store.async_patch_settings(decision_provider_id=account)
    if case == 'endpoint':
        store = await agentcli._get_secret_store(hass)
        account = await store.add('typesafe', {'base_url': 'https://unreviewed.example/v1',
                                              'api_key': 'other-secret', 'model': 'jev-latest'})
        await data.store.async_patch_settings(decision_provider_id=account)
    if case == 'configured_revision':
        store = await agentcli._get_secret_store(hass)
        await store.set_model(data.store.get_settings().decision_provider_id, 'unreviewed-model')
    if case == 'local_mode': await data.store.async_patch_settings(agentcli_routing_mode='local')
    original = json.loads(json.dumps(messages))
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=response,
                side_effect=DecisionError('timeout') if case == 'timeout' else None)) as remote,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, _, history = await chat_turn(hass, context_chat, messages, language)
    provider.stream_turn.assert_called_once()
    dispatch.assert_not_called()
    assert history[:-1] == original and history[-1]['content'] == 'Normal provider reply'
    if case in ('secret', 'tool_history', 'oversized', 'language', 'account', 'endpoint', 'configured_revision', 'local_mode'):
        remote.assert_not_called()
    else:
        remote.assert_awaited_once()
    assert not data.command_routing.chat_history._entries


@pytest.mark.parametrize('change', ['token', 'settings', 'account', 'account_back', 'target', 'history', 'scope'])
async def test_context_wait_changes_stop_without_dispatch_or_provider_replay(hass, context_chat, change):
    data, token = context_chat
    messages = context_messages()
    async def response(*args):
        if change == 'token': token.revoked = True
        if change == 'settings': await data.store.async_patch_settings(kill_switch=True)
        if change in ('account', 'account_back'):
            store = await agentcli._get_secret_store(hass)
            account = data.store.get_settings().decision_provider_id
            await store.set_model(account, 'changed')
            if change == 'account_back': await store.set_model(account, 'jev-latest')
        if change == 'target': er.async_get(hass).async_update_entity('light.test_desk', name='Replacement')
        if change == 'history': messages[0]['content'] = 'Do not operate devices.'
        if change == 'scope': token.permissions = PermissionTree()
        return context_response()
    with (patch.object(DecisionProvider, '_request', side_effect=response),
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, events, _ = await chat_turn(hass, context_chat, messages)
    dispatch.assert_not_called()
    provider.stream_turn.assert_not_called()
    assert any(name == 'error' for name, _ in events)


@pytest.mark.parametrize('kind', ['event', 'task', 'owner'])
async def test_context_chat_cancellation_closes_its_request(hass, context_chat, kind):
    data, _ = context_chat
    entered, closed, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def wait(*args):
        entered.set()
        try: await asyncio.Event().wait()
        finally: closed.set()
    with (patch.object(DecisionProvider, '_request', side_effect=wait),
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        pending = asyncio.create_task(chat_turn(hass, context_chat, context_messages(), cancel=cancel))
        await entered.wait()
        if kind == 'event': cancel.set()
        if kind == 'task': pending.cancel()
        if kind == 'owner': data.decision_shadow.cancel()
        if kind == 'task':
            with pytest.raises(asyncio.CancelledError): await pending
        else:
            async with asyncio.timeout(1): provider, events, _ = await pending
            provider.stream_turn.assert_not_called()
            assert any(name == 'error' for name, _ in events)
    assert closed.is_set() and data.decision_shadow.task is None
    dispatch.assert_not_called()


@pytest.mark.parametrize('barrier', ['prohibited', 'read_only', 'confirm'])
@pytest.mark.parametrize('pass_through', [False, True])
async def test_context_chat_keeps_mesa_and_pass_through_gates(hass, context_chat, barrier, pass_through):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    data, token = context_chat
    token.pass_through = pass_through
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': barrier}}}, default_origin=MetadataOrigin.USER))
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response())),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, _ = await chat_turn(hass, context_chat, context_messages())
    provider.stream_turn.assert_not_called()
    call.assert_not_called()
    assert any(name == 'approval_required' for name, _ in events) == (barrier == 'confirm')
    assert not data.command_routing.chat_history._entries


@pytest.mark.parametrize('change', ['none', 'account', 'account_back', 'deleted', 'mode', 'target', 'scope',
    'model_proof', 'kind_proof', 'language_proof', 'ingress', 'value'])
async def test_context_saved_approval_rechecks_provider_and_exact_action(hass, context_chat, change, provider_context_history):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.const import COMMAND_ROUTING_PLAN_KEY
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    data, token = context_chat
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}}, default_origin=MetadataOrigin.USER))
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response())),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[]))):
        provider, events, _ = await chat_turn(hass, context_chat, [*deepcopy(provider_context_history), {'role': 'user', 'content': 'set the desk lamp brightness to 37 percent'}])
    provider.stream_turn.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    plan = record['args'][COMMAND_ROUTING_PLAN_KEY]
    assert plan['context_review']['model'] == 'jev-1.13.0'
    assert plan['proposal']['brightness_pct'] == 37
    assert 'decision_category' not in plan
    assert 'secret' not in json.dumps(plan) and provider_context_history[0]['content'] not in json.dumps(plan)
    assert COMMAND_ROUTING_PLAN_KEY not in json.dumps(events)
    store = await agentcli._get_secret_store(hass)
    account = data.store.get_settings().decision_provider_id
    if change in ('account', 'account_back'):
        await store.set_model(account, 'changed')
        if change == 'account_back': await store.set_model(account, 'jev-latest')
    if change == 'deleted': await store.delete(account)
    if change == 'mode': await data.store.async_patch_settings(agentcli_routing_mode='local')
    if change == 'target': er.async_get(hass).async_update_entity('light.test_desk', name='Replacement')
    if change == 'scope': token.permissions = PermissionTree()
    if change == 'model_proof': plan['context_review']['model'] = 'unreviewed-model'
    if change == 'kind_proof': plan['context_review']['kind'] = 'unknown'
    if change == 'language_proof': plan['context_review']['language'] = 'zh-Hant'
    if change == 'ingress': plan['ingress'] = 'voice'
    if change == 'value': record['args']['service_data']['brightness_pct'] = 99
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


@pytest.mark.parametrize('capability', ['deny', 'confirm'])
async def test_context_keeps_physical_capability_gate(hass, context_chat, capability):
    from custom_components.phoenix_mcp import mcp_view
    data, token = context_chat
    token.cap_physical_control = capability
    token.permissions = PermissionTree(domains={'cover': PermissionNode(state='GREEN')})
    entry = er.async_get(hass).async_get_or_create('cover', 'test', 'blind', original_name='Blind')
    hass.states.async_set(entry.entity_id, 'open', {'friendly_name': 'Blind',
        'supported_features': CoverEntityFeature.SET_POSITION})
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response())),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, _ = await chat_turn(hass, context_chat,
            context_messages(f'set {entry.entity_id} to 37 percent'))
    call.assert_not_called()
    provider.stream_turn.assert_not_called()
    assert any(name == 'approval_required' for name, _ in events) == (capability == 'confirm')
    assert bool(data.store.get_pending_approvals()) == (capability == 'confirm')


@pytest.mark.parametrize('domain,name,features,sentence,service,field,value', [
    ('media_player', 'Speaker', MediaPlayerEntityFeature.VOLUME_SET, 'set media_player.test_speaker volume to 37 percent', 'volume_set', 'volume_level', .37),
    ('cover', 'Blind', CoverEntityFeature.SET_POSITION, 'set cover.test_blind to 37 percent', 'set_cover_position', 'position', 37),
])
async def test_context_keeps_local_numeric_projection_for_other_devices(
        hass, context_chat, domain, name, features, sentence, service, field, value):
    from custom_components.phoenix_mcp import mcp_view
    data, token = context_chat
    token.permissions = PermissionTree(domains={domain: PermissionNode(state='GREEN')})
    entry = er.async_get(hass).async_get_or_create(domain, 'test', name.lower(), original_name=name)
    assert entry.entity_id in sentence
    hass.states.async_set(entry.entity_id, 'on' if domain == 'media_player' else 'open',
                          {'friendly_name': name, 'supported_features': features})
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response())) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, _ = await chat_turn(hass, context_chat, context_messages(sentence))
    provider.stream_turn.assert_not_called()
    assert remote.await_args.args[4]['state']['service']['service_data'] == {field: value}
    call.assert_awaited_once_with(domain, service, {'entity_id': [entry.entity_id], field: value},
                                 blocking=True, return_response=False)
    assert not data.command_routing.chat_history._entries


async def test_context_busy_slot_preserves_other_ingress_job(hass, context_chat):
    data, _ = context_chat
    other = asyncio.create_task(asyncio.Event().wait())
    data.decision_shadow.task = other
    try:
        with (patch.object(DecisionProvider, '_request') as remote,
              patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
            provider, _, history = await chat_turn(hass, context_chat, context_messages())
        provider.stream_turn.assert_called_once()
        assert history[:2] == CONTEXT_HISTORY
        assert data.decision_shadow.task is other and not other.done()
        remote.assert_not_called()
        dispatch.assert_not_called()
    finally:
        other.cancel()
        await asyncio.gather(other, return_exceptions=True)
        data.decision_shadow.task = None


async def test_context_interrupted_delivery_retains_write_recovery(hass, context_chat):
    from custom_components.phoenix_mcp import mcp_view
    data, token = context_chat
    async def emit(name, payload):
        if name == 'assistant_delta':
            raise ConnectionResetError('delivery interrupted')
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response())),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        with pytest.raises(ConnectionResetError):
            await agentcli.async_run_agent_turn(
                hass=hass, data=data, token=token, provider=MagicMock(), session=None,
                messages=context_messages(), options={}, client_ip='agentcli', base_url='http://test',
                emit=emit, cancel=asyncio.Event(), routing_language='en')
    call.assert_awaited_once()
    assert data.agent_recovery.blocks(token.id)
    assert not data.command_routing.chat_history._entries


async def test_context_account_change_during_local_recognition_stops_before_history_sharing(hass, context_chat):
    data, _ = context_chat
    evaluate = chat_routing.async_evaluate_command
    async def changed(*args, **kwargs):
        result = await evaluate(*args, **kwargs)
        assert result['proposal']
        store = await agentcli._get_secret_store(hass)
        await store.set_model(data.store.get_settings().decision_provider_id, 'jev-1.13.0')
        return result
    with (patch.object(chat_routing, 'async_evaluate_command', side_effect=changed),
          patch.object(DecisionProvider, '_request') as remote,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, events, _ = await chat_turn(hass, context_chat, context_messages())
    remote.assert_not_called()
    dispatch.assert_not_called()
    provider.stream_turn.assert_not_called()
    assert any(name == 'error' for name, _ in events)


@pytest.mark.parametrize('decline', [False, True])
async def test_rich_history_routes_only_after_review_and_preserves_full_fallback(hass, context_chat, provider_context_history, decline):
    from custom_components.phoenix_mcp import mcp_view
    messages = [*deepcopy(provider_context_history), {'role': 'user', 'content': 'set the desk lamp brightness to 37 percent'}]
    original = deepcopy(messages)
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=context_response(decline=decline))) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, history = await chat_turn(hass, context_chat, messages)
    remote.assert_awaited_once()
    state = remote.await_args.args[4]['state']
    assert state['history'] == original[:-1] and state['history_format'] == 'provider_messages'
    assert state['candidate']['brightness_pct'] == 37
    assert provider.stream_turn.called == decline and call.called != decline
    assert history[:len(original)] == original
    if not decline:
        call.assert_awaited_once_with('light', 'turn_on', {'entity_id': ['light.test_desk'], 'brightness_pct': 37},
                                     blocking=True, return_response=False)
    assert not context_chat[0].command_routing.chat_history._entries


@pytest.mark.parametrize('case', ['unqualified', 'unknown_revision', 'secret', 'changed_history', 'changed_account', 'incomplete'])
async def test_rich_history_refusals_never_dispatch(hass, context_chat, provider_context_history, case):
    data, _ = context_chat
    messages = [*deepcopy(provider_context_history), {'role': 'user', 'content': 'set the desk lamp brightness to 37 percent'}]
    store = await agentcli._get_secret_store(hass)
    if case == 'unqualified':
        await store.set_model(data.store.get_settings().decision_provider_id, 'unqualified')
    if case == 'secret': messages[0]['content'] = 'password: private-context-secret'
    if case == 'incomplete': messages.pop(-2)
    original = deepcopy(messages)
    async def reply(*args):
        if case == 'changed_history': messages[0]['content'] = 'Do not change any devices.'
        if case == 'changed_account': await store.set_model(data.store.get_settings().decision_provider_id, 'other')
        return context_response(model='unknown-revision' if case == 'unknown_revision' else 'jev-1.13.0')
    with (patch.object(DecisionProvider, '_request', side_effect=reply) as remote,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, events, history = await chat_turn(hass, context_chat, messages)
    dispatch.assert_not_called()
    assert not data.store.get_pending_approvals()
    if case in ('changed_history', 'changed_account'):
        provider.stream_turn.assert_not_called()
        assert any(name == 'error' for name, _ in events)
    else:
        provider.stream_turn.assert_called_once()
        assert history[:len(original)] == original
    if case in ('unqualified', 'secret', 'incomplete'): remote.assert_not_called()


@pytest.mark.parametrize('report_matches', [False, True])
async def test_only_observed_write_result_can_extend_local_history(hass, executing_chat, report_matches):
    from custom_components.phoenix_mcp import mcp_view
    eid = 'light.test_desk'
    async def dispatched(*args, **kwargs):
        if report_matches:
            hass.states.async_set(eid, 'on', dict(hass.states.get(eid).attributes))
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', AsyncMock(side_effect=dispatched)) as call):
        provider, _, messages = await chat_turn(hass, executing_chat,
            [{'role': 'user', 'content': 'turn on the desk lamp'}])
        provider.stream_turn.assert_not_called()
        call.assert_awaited_once()
        followup, _, _ = await chat_turn(hass, executing_chat,
            [*messages, {'role': 'user', 'content': 'what is the state of the desk lamp'}])
    assert followup.stream_turn.called is not report_matches
    assert call.await_count == 1


@pytest.mark.parametrize('category,service', [('light_on', 'turn_on'), ('state_query', None)])
async def test_qualified_jev_chat_uses_one_joint_request(hass, hybrid_chat, category, service):
    from custom_components.phoenix_mcp import mcp_view
    data, _ = hybrid_chat
    store = await agentcli._get_secret_store(hass)
    await store.set_model(data.store.get_settings().decision_provider_id, 'jev-latest')
    async def response(session, method, path, timeout, payload):
        assert set(payload['questions']) == {'route', 'target', 'summary', 'summary_scope'}
        return {**chat_decision_wire(payload, category), 'model': 'jev-1.13.0'}
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, messages, remote = await decision_chat_turn(hass, hybrid_chat, request=response)
    remote.assert_awaited_once()
    provider.stream_turn.assert_not_called()
    if service:
        call.assert_awaited_once_with('light', service, {'entity_id': ['light.test_desk']}, blocking=True, return_response=False)
    else:
        call.assert_not_called()
        assert messages[-1]['content'] == 'The state is Off.'
    assert events[-1] == ('done', {'stop_reason': 'end_turn'})
    assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'plan_ready': 1}


def followup_response(payload, *, value='37', decline=False):
    """Return independent protocol answers for candidate selection and history review."""
    if 'route' not in payload['questions']:
        return context_response(decline=decline)
    target = next(key for key, item in payload['state']['targets'].items()
                  if item['entity_id'] == 'light.test_desk')
    selected = {'route': 'light_brightness', 'target': target, 'percentage': value}
    return {'model': 'jev-1.13.0', 'answers': {
        name: {'type': 'choice', 'choice': selected.get(name, 'none'), 'confidence': 1,
               'probabilities': {key: float(key == selected.get(name, 'none')) for key in question['criteria']}}
        for name, question in payload['questions'].items()}}


@pytest.mark.parametrize('local_chain', [False, True])
async def test_decision_followup_preserves_history_and_both_execution_proofs(hass, context_chat, local_chain):
    from custom_components.phoenix_mcp import mcp_view
    data, _ = context_chat
    messages = context_messages('I would like Desk lamp at thirty-seven percent now.')
    captured = []
    async def response(*args):
        payload = args[4]; captured.append(payload)
        return followup_response(payload)
    with (patch.object(DecisionProvider, '_request', side_effect=response) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        if local_chain:
            _, _, messages = await chat_turn(hass, context_chat, [{'role': 'user', 'content': 'turn on the desk lamp'}])
            messages.append({'role': 'user', 'content': 'I would like Desk lamp at thirty-seven percent now.'})
            call.reset_mock()
        original = json.loads(json.dumps(messages))
        provider, _, history = await chat_turn(hass, context_chat, messages)
    assert remote.await_count == 2
    assert set(captured[0]['questions']) == {'route', 'target', 'percentage', 'summary', 'summary_scope'}
    assert 'history' not in captured[0]['state']
    assert captured[1]['state']['history'] == original[:-1]
    assert captured[1]['state']['candidate']['brightness_pct'] == 37
    assert captured[1]['state']['candidate_source'] == 'decision'
    assert 'model-selected hypothesis' in captured[1]['questions']['request']['instructions']
    provider.stream_turn.assert_not_called()
    call.assert_awaited_once_with('light', 'turn_on', {'entity_id': ['light.test_desk'], 'brightness_pct': 37},
                                blocking=True, return_response=False)
    assert history[:-1] == original
    assert not data.command_routing.chat_history._entries


@pytest.mark.parametrize('case,calls', [('value_mismatch', 1), ('restriction', 2), ('uncertain', 2),
    ('unknown_source_revision', 1), ('unknown_context_revision', 2), ('secret', 0), ('hidden_id', 0),
    ('catalog', 0)])
async def test_decision_followup_declines_without_effects(hass, context_chat, case, calls):
    from custom_components.phoenix_mcp.const import DECISION_GROUPED_MAX_TARGETS
    data, _ = context_chat
    messages = context_messages('I would like Desk lamp at 37 percent now.')
    if case == 'secret': messages[0]['content'] = 'password: never-share-this'
    if case == 'hidden_id': messages[0]['content'] = 'Do not change lock.private_door'
    if case == 'catalog':
        # The existing desk target puts this one past the complete grouped limit.
        for index in range(DECISION_GROUPED_MAX_TARGETS):
            entry = er.async_get(hass).async_get_or_create('light', 'test', f'extra_{index}')
            hass.states.async_set(entry.entity_id, 'off')
    async def response(*args):
        payload = args[4]
        body = followup_response(payload, value='38' if case == 'value_mismatch' else '37', decline=case == 'restriction')
        if case == 'unknown_source_revision' and 'route' in payload['questions']: body['model'] = 'unknown'
        if 'route' not in payload['questions']:
            if case == 'unknown_context_revision': body['model'] = 'unknown'
            if case == 'uncertain': body = context_response(probability=.89)
        return body
    original = json.loads(json.dumps(messages))
    with (patch.object(DecisionProvider, '_request', side_effect=response) as remote,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, _, history = await chat_turn(hass, context_chat, messages)
    assert remote.await_count == calls
    dispatch.assert_not_called()
    provider.stream_turn.assert_called_once()
    assert history[:-1] == original
    assert data.decision_shadow.task is None


@pytest.mark.parametrize('change', ['none', 'account', 'competitor', 'value', 'context_proof', 'source_proof', 'context_removed'])
async def test_decision_followup_approval_revalidates_combined_plan(hass, context_chat, change):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.const import COMMAND_ROUTING_PLAN_KEY
    data, token = context_chat
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}}, default_origin=MetadataOrigin.USER))
    async def response(*args): return followup_response(args[4])
    with (patch.object(DecisionProvider, '_request', side_effect=response),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[]))):
        provider, _, _ = await chat_turn(hass, context_chat, context_messages('I would like Desk lamp at 37 percent now.'))
    provider.stream_turn.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    plan = record['args'][COMMAND_ROUTING_PLAN_KEY]
    assert 'context_review' in plan and 'decision_batch' in plan
    assert plan['context_review']['proposal'] and plan['decision_batch']['percentage'] == 37
    if change == 'account':
        store = await agentcli._get_secret_store(hass)
        await store.set_model(data.store.get_settings().decision_provider_id, 'changed')
    if change == 'competitor':
        entry = er.async_get(hass).async_get_or_create('light', 'test', 'competitor', original_name='Another lamp')
        hass.states.async_set(entry.entity_id, 'off')
    if change == 'value': plan['proposal']['brightness_pct'] = 88
    if change == 'context_proof': del plan['context_review']['proposal']
    if change == 'source_proof': del plan['decision_batch']
    if change == 'context_removed': del plan['context_review']
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


async def test_decision_followup_uses_one_deadline_for_both_requests(hass, context_chat):
    data, _ = context_chat
    await data.store.async_patch_settings(decision_timeout_ms=250)
    async def response(*args):
        await asyncio.sleep(.18)
        return followup_response(args[4])
    with (patch.object(DecisionProvider, '_request', side_effect=response) as remote,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, _, _ = await chat_turn(hass, context_chat, context_messages('I would like Desk lamp at 37 percent now.'))
    assert remote.await_count == 2
    dispatch.assert_not_called()
    provider.stream_turn.assert_called_once()
    assert data.decision_shadow.task is None


@pytest.mark.parametrize('change', ['cancel', 'history', 'account', 'competitor'])
async def test_decision_followup_wait_preserves_current_authority(hass, context_chat, change):
    data, _ = context_chat
    messages = context_messages('I would like Desk lamp at 37 percent now.')
    entered, release, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def response(*args):
        if 'route' not in args[4]['questions']:
            entered.set()
            await release.wait()
        return followup_response(args[4])
    with (patch.object(DecisionProvider, '_request', side_effect=response),
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        task = asyncio.create_task(chat_turn(hass, context_chat, messages, cancel=cancel))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if change == 'cancel': cancel.set()
            if change == 'history': messages[0]['content'] = 'Never change the lamp.'
            if change == 'account':
                store = await agentcli._get_secret_store(hass)
                await store.set_model(data.store.get_settings().decision_provider_id, 'changed')
            if change == 'competitor':
                entry = er.async_get(hass).async_get_or_create('light', 'test', 'new_competitor')
                hass.states.async_set(entry.entity_id, 'off')
            release.set()
            provider, _, _ = await asyncio.wait_for(task, 5)
        finally:
            release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    dispatch.assert_not_called()
    provider.stream_turn.assert_not_called()
    assert data.decision_shadow.task is None


@pytest.mark.parametrize('language', ['es', 'es-MX', 'zh-Hans', 'zh-CN'])
@pytest.mark.parametrize('decline', [False, True])
async def test_multilingual_joint_followup_uses_whole_history_and_numeric_evidence(hass, context_chat, language, decline):
    from custom_components.phoenix_mcp import mcp_view
    messages = [
        {'role': 'user', 'content': 'Solo explica comandos, no operes dispositivos.' if decline else '¿Qué es el brillo?'},
        {'role': 'assistant', 'content': 'Entendido.'},
        {'role': 'user', 'content': 'Quiero Desk lamp al treinta y siete por ciento ahora.'},
    ]
    if language.startswith('zh'):
        messages = [
            {'role': 'user', 'content': '不要操作设备，只解释命令。' if decline else '什么是亮度？'},
            {'role': 'assistant', 'content': '好的。'},
            {'role': 'user', 'content': '我希望 Desk lamp 现在的亮度是百分之三十七。'},
        ]
    async def response(*args):
        payload = args[4]
        assert payload['state']['language'] == ('zh-CN' if language.startswith('zh') else 'es')
        if 'route' not in payload['questions']:
            assert payload['state']['history'] == messages[:2]
            assert payload['state']['candidate']['brightness_pct'] == 37
        return followup_response(payload, decline=decline)
    with (patch.object(DecisionProvider, '_request', side_effect=response) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, history = await chat_turn(hass, context_chat, messages, language=language)
    assert remote.await_count == 2
    assert call.await_count == (0 if decline else 1)
    assert provider.stream_turn.called is decline
    assert history[:3] == messages[:3]
    if not decline:
        call.assert_awaited_once_with('light', 'turn_on', {'entity_id': ['light.test_desk'], 'brightness_pct': 37},
                                     blocking=True, return_response=False)


async def test_traditional_chinese_followup_checks_history_and_source_value(hass, context_chat):
    from custom_components.phoenix_mcp import mcp_view
    messages = context_messages('請把 Desk lamp 的亮度設為百分之三十七。')
    async def respond(*args):
        payload = args[4]
        assert payload['state']['language'] == 'zh-TW'
        if 'route' not in payload['questions']:
            assert payload['state']['history'] == messages[:-1]
            assert payload['state']['candidate']['brightness_pct'] == 37
        return followup_response(payload)
    with (patch.object(DecisionProvider, '_request', side_effect=respond) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, _ = await chat_turn(hass, context_chat, messages, language='zh-Hant')
    assert remote.await_count == 2
    provider.stream_turn.assert_not_called()
    call.assert_awaited_once_with('light', 'turn_on', {'entity_id': ['light.test_desk'], 'brightness_pct': 37},
                                 blocking=True, return_response=False)


async def test_categorical_followup_routes_with_more_than_common_catalog_limit(hass, context_chat):
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    data, token = context_chat
    token.permissions.domains['sensor'] = PermissionNode(state='YELLOW')
    for index in range(60):
        entry = er.async_get(hass).async_get_or_create('sensor', 'test', f'catalog_{index}')
        hass.states.async_set(entry.entity_id, '20')
        async_expose_entity(hass, 'conversation', entry.entity_id, True)
    seen = []
    async def response(*args):
        payload = args[4]; seen.append(payload)
        if 'route' not in payload['questions']:
            return context_response()
        return {**chat_decision_wire(payload, 'state_query'), 'model': 'jev-1.13.0'}
    local = {'reason': 'no_match', 'proposal': None, 'elapsed_ms': 1, 'route': 'provider'}
    with (patch.object(chat_routing, 'async_evaluate_command', AsyncMock(return_value=local)),
          patch.object(DecisionProvider, '_request', side_effect=response) as remote):
        provider, _, history = await chat_turn(hass, context_chat,
            context_messages('Please report the current status of Desk lamp.'))
    assert len(seen[0]['state']['targets']) == 61
    assert set(seen[0]['questions']) == {'route', 'target', 'summary', 'summary_scope'}
    assert remote.await_count == 2, data.decision_shadow.summary()
    assert not provider.stream_turn.called, data.decision_shadow.summary()
    assert seen[1]['state']['history'] == CONTEXT_HISTORY
    assert history[:2] == CONTEXT_HISTORY
    assert history[-1]['content'] == 'The state is Off.'
    assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'plan_ready': 1}


def reference_chat_wire(payload, category='light_off', *, restrict=False):
    selected = ({'route': category, 'reference': 'bound', 'constraints': 'restricted' if restrict else 'clear'}
                if 'reference' in payload['questions'] else {'route': 'provider', 'target': 'none'})
    return {'model': 'jev-1.13.0', 'answers': {
        name: {'type': 'choice', 'choice': selected.get(name, 'none'), 'confidence': 1,
               'probabilities': {key: float(key == selected.get(name, 'none')) for key in spec['criteria']}}
        for name, spec in payload['questions'].items()}}


@pytest.mark.parametrize('category,sentence,service', [('light_on', 'turn it on', 'turn_on'),
    ('light_off', 'turn it off', 'turn_off'), ('state_query', 'is it on?', None)])
@pytest.mark.parametrize('local_chain', [False, True])
async def test_chat_reference_uses_full_history_and_guarded_dispatch(hass, context_chat, category, sentence, service, local_chain):
    from custom_components.phoenix_mcp import mcp_view
    data, _ = context_chat
    history = [{'role': 'user', 'content': 'what is the state of Desk lamp'},
               {'role': 'assistant', 'content': 'The state is Off.'}]
    if local_chain:
        _, _, history = await chat_turn(hass, context_chat, history[:1])
        assert data.command_routing.chat_history._entries
    original = deepcopy(history)
    seen = []
    async def response(*args):
        seen.append(args[4])
        return reference_chat_wire(args[4], category)
    with (patch.object(DecisionProvider, '_request', side_effect=response) as request,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, returned = await chat_turn(hass, context_chat, [*history, {'role': 'user', 'content': sentence}])
    assert not provider.stream_turn.called, data.decision_shadow.summary()
    assert request.await_count == 2
    assert seen[1]['state']['history'] == original
    assert returned[:len(original)] == original and events[-1] == ('done', {'stop_reason': 'end_turn'})
    assert not data.command_routing.chat_history._entries
    if service: call.assert_awaited_once_with('light', service, {'entity_id': ['light.test_desk']}, blocking=True, return_response=False)
    else: call.assert_not_called()


@pytest.mark.parametrize('failure', ['restriction', 'malformed', 'uncertain', 'timeout'])
async def test_reference_not_attempted_after_errors_and_never_overrides_restrictions(hass, context_chat, failure):
    from custom_components.phoenix_mcp.decision_provider import DecisionError
    data, _ = context_chat
    history = [{'role': 'user', 'content': 'what is the state of Desk lamp'}, {'role': 'assistant', 'content': 'The state is Off.'}]
    async def response(*args):
        payload = args[4]
        if failure in ('malformed', 'timeout'): raise DecisionError(failure)
        body = reference_chat_wire(payload, restrict=True)
        if failure == 'uncertain':
            check = body['answers']['route']; check['choice'] = 'light_off'
            check['probabilities'] = {key: .85 if key == 'light_off' else .15 if key == 'provider' else 0
                                      for key in check['probabilities']}
        return body
    with (patch.object(DecisionProvider, '_request', side_effect=response) as request,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, _, returned = await chat_turn(hass, context_chat, [*history, {'role': 'user', 'content': 'turn it off'}])
    assert request.await_count == (2 if failure == 'restriction' else 1)
    provider.stream_turn.assert_called_once()
    dispatch.assert_not_called()
    assert returned[:2] == history and not data.store.get_pending_approvals()


@pytest.mark.parametrize('change', ['none', 'alias', 'reference_proof', 'proposal'])
async def test_reference_approval_rechecks_bound_plan(hass, context_chat, change):
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.const import COMMAND_ROUTING_PLAN_KEY
    from custom_components.phoenix_mcp.command_execution import plan_is_current
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    data, token = context_chat
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set('light.test_desk', SemanticProfile.from_dict('light.test_desk',
        {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}}, default_origin=MetadataOrigin.USER))
    messages = [{'role': 'user', 'content': 'what is the state of Desk lamp'}, {'role': 'assistant', 'content': 'The state is Off.'},
                {'role': 'user', 'content': 'turn it on'}]
    async def response(*args): return reference_chat_wire(args[4], 'light_on')
    with (patch.object(DecisionProvider, '_request', side_effect=response),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, events, _ = await chat_turn(hass, context_chat, messages)
    assert not provider.stream_turn.called, data.decision_shadow.summary()
    call.assert_not_called()
    assert any(name == 'approval_required' for name, _ in events)
    record = data.store.get_pending_approvals()[0]
    plan = record['args'][COMMAND_ROUTING_PLAN_KEY]
    assert plan_is_current(plan, hass, data, token)
    if change == 'alias': er.async_get(hass).async_update_entity('light.test_desk', aliases={'new alias'})
    if change == 'reference_proof': plan['reference_review']['proposal'] = 'changed'
    if change == 'proposal': plan['proposal']['intent'] = 'HassTurnOff'
    assert plan_is_current(plan, hass, data, token) is (change == 'none')
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


async def test_reference_and_explicit_selection_share_total_deadline(hass, context_chat):
    data, _ = context_chat
    await data.store.async_patch_settings(decision_timeout_ms=250)
    messages = [{'role': 'user', 'content': 'what is the state of Desk lamp'}, {'role': 'assistant', 'content': 'The state is Off.'},
                {'role': 'user', 'content': 'turn it off'}]
    async def response(*args):
        await asyncio.sleep(.15)
        return reference_chat_wire(args[4])
    with (patch.object(DecisionProvider, '_request', side_effect=response) as request,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        provider, _, _ = await chat_turn(hass, context_chat, messages)
    assert request.await_count == 2
    provider.stream_turn.assert_called_once()
    dispatch.assert_not_called()
    assert data.decision_shadow.summary()['chat_hybrid']['reasons'] == {'timeout': 1}
    assert data.decision_shadow.task is None


async def test_repeated_references_use_exact_successful_subject_and_fresh_history_review(hass, context_chat):
    data, _ = context_chat
    _, _, history = await chat_turn(hass, context_chat, [{'role': 'user', 'content': 'what is the state of Desk lamp'}])
    seen = []
    async def response(*args):
        seen.append(args[4])
        return reference_chat_wire(args[4], 'state_query')
    with patch.object(DecisionProvider, '_request', side_effect=response) as remote:
        for text in ('is it on?', 'is it off?', 'is it on?'):
            previous = deepcopy(history)
            provider, _, history = await chat_turn(hass, context_chat, [*history, {'role': 'user', 'content': text}])
            assert not provider.stream_turn.called, data.decision_shadow.summary()
            assert history[:len(previous)] == previous
            assert seen[-1]['state']['history'] == previous
            assert seen[-1]['state']['anchor']['source'] == 'verified_reply'
            assert history[-1]['content'] == 'The state is Off.'
    assert remote.await_count == 6
    assert not data.command_routing.chat_history._entries


@pytest.mark.parametrize('change', ['history', 'account', 'scope', 'failed_reply'])
async def test_repeated_reference_cannot_reuse_stale_or_unconfirmed_subject(hass, context_chat, change):
    data, token = context_chat
    _, _, history = await chat_turn(hass, context_chat, [{'role': 'user', 'content': 'what is the state of Desk lamp'}])
    async def response(*args): return reference_chat_wire(args[4], 'state_query')
    with patch.object(DecisionProvider, '_request', side_effect=response):
        _, _, history = await chat_turn(hass, context_chat, [*history, {'role': 'user', 'content': 'is it on?'}])
    if change == 'history': history[-1]['content'] = 'Another lamp is the subject now.'
    elif change == 'account':
        await hass.data['_phoenix_agentcli_store'].set_model(data.store.get_settings().decision_provider_id, 'changed')
    elif change == 'scope': token.revoked = True
    else:
        history[-1]['content'] = 'The action failed.'
    with patch.object(DecisionProvider, '_request', side_effect=response) as remote:
        provider, _, _ = await chat_turn(hass, context_chat, [*history, {'role': 'user', 'content': 'is it off?'}])
    assert all('reference' not in call.args[4]['questions'] for call in remote.await_args_list)


@pytest.mark.parametrize('restricted', [False, True])
async def test_temporal_chat_command_preserves_full_history_review(hass, context_chat, restricted):
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    from custom_components.phoenix_mcp import mcp_view
    data, token = context_chat
    token.permissions.domains['time'] = PermissionNode(state='GREEN')
    entry = er.async_get(hass).async_get_or_create('time', 'test', 'clock', original_name='Test clock')
    hass.states.async_set(entry.entity_id, '12:00:00', {'friendly_name': 'Test clock'})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    seen = []
    async def respond(*args):
        payload = args[4]; seen.append(payload)
        if 'request' in payload['questions']:
            return context_response(decline=restricted)
        target = next(key for key, item in payload['state']['targets'].items() if item['entity_id'] == entry.entity_id)
        choices = {'route': 'time_value', 'target': target, 'value': 'value'}
        return {'model': 'jev-1.13.0', 'answers': {
            question: {'type': 'choice', 'choice': choices.get(question, 'none'), 'confidence': 1,
                       'probabilities': {key: float(key == choices.get(question, 'none')) for key in spec['criteria']}}
            for question, spec in payload['questions'].items()}}
    with (patch.object(DecisionProvider, '_request', side_effect=respond) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, returned = await chat_turn(hass, context_chat, context_messages('Set Test clock to 18:30'))
    assert remote.await_count == 2, data.decision_shadow.summary()
    assert seen[-1]['state']['history'] == CONTEXT_HISTORY
    assert seen[-1]['state']['candidate']['value'] == '18:30:00'
    assert returned[:len(CONTEXT_HISTORY)] == CONTEXT_HISTORY
    assert provider.stream_turn.called is restricted
    if restricted: call.assert_not_called()
    else:
        call.assert_awaited_once_with('time', 'set_value', {'entity_id': [entry.entity_id], 'time': '18:30:00'},
                                     blocking=True, return_response=False)


@pytest.mark.parametrize('restricted', [False, True])
async def test_option_command_in_chat_rechecks_complete_history(hass, context_chat, restricted):
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    from custom_components.phoenix_mcp import mcp_view
    data, token = context_chat
    token.permissions.domains['select'] = PermissionNode(state='GREEN')
    hass.services.async_register('select', 'select_option', AsyncMock())
    entry = er.async_get(hass).async_get_or_create('select', 'test', 'mode', original_name='Test mode')
    hass.states.async_set(entry.entity_id, 'Home', {'friendly_name': 'Test mode', 'options': ['Home', 'Away']})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    seen = []
    async def respond(*args):
        payload = args[4]; seen.append(payload)
        if 'request' in payload['questions']: return context_response(decline=restricted)
        target = next(key for key, item in payload['state']['targets'].items() if item['entity_id'] == entry.entity_id)
        value = next(key for key, item in payload['state']['values'].items() if item == 'Away')
        choices = {'route': 'select_option', 'target': target, 'value': value,
                   'attribute': 'none', 'color': 'none'}
        return {'model': 'jev-1.13.0', 'answers': {question: {
            'type': 'choice', 'choice': choices.get(question, 'none'), 'confidence': 1,
            'probabilities': {key: float(key == choices.get(question, 'none')) for key in spec['criteria']}}
            for question, spec in payload['questions'].items()}}
    with (patch.object(DecisionProvider, '_request', side_effect=respond) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, returned = await chat_turn(hass, context_chat, context_messages('Set Test mode to Away'))
    assert remote.await_count == 2, data.decision_shadow.summary()
    assert 'attribute' in seen[0]['questions']
    assert seen[-1]['state']['history'] == CONTEXT_HISTORY
    assert seen[-1]['state']['candidate']['value'] == 'Away'
    assert returned[:len(CONTEXT_HISTORY)] == CONTEXT_HISTORY
    assert provider.stream_turn.called is restricted
    if restricted:
        call.assert_not_called()
    else:
        call.assert_awaited_once_with('select', 'select_option',
                                     {'entity_id': [entry.entity_id], 'option': 'Away'},
                                     blocking=True, return_response=False)


@pytest.mark.parametrize('mode,contextual', [('hybrid', False), ('auto', False), ('off', False), ('auto', True)])
async def test_custom_sentence_keeps_precedence_through_chat(
        hass, context_chat, custom_sentence_recognition, mode, contextual):
    data, _ = context_chat
    await data.store.async_patch_settings(agentcli_routing_mode=mode, agentcli_routing_shadow_enabled=True)
    sentence = 'turn on the desk lamp'
    messages = context_messages(sentence) if contextual else [{'role': 'user', 'content': sentence}]
    async def respond(*args):
        payload = args[4]
        if 'route' not in payload['questions']:
            return context_response()
        return {**chat_decision_wire(payload, 'light_on'), 'model': 'jev-1.13.0'}
    from custom_components.phoenix_mcp import mcp_view
    with (patch.object(DecisionProvider, '_request', AsyncMock(side_effect=respond)) as remote,
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(data.decision_shadow, 'start', wraps=data.decision_shadow.start) as start,
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        provider, _, result = await chat_turn(hass, context_chat, messages)
    provider.stream_turn.assert_called_once()
    assert result[-1]['content'] == 'Normal provider reply'
    remote.assert_not_called()
    start.assert_not_called()
    call.assert_not_called()
    assert data.command_routing.chat_reasons == {'custom_sentence': 1}
    assert data.store.get_pending_approvals() == []
