"""Reference previews retain history, source anchors and authority without effects."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli
from custom_components.phoenix_mcp.command_routing import async_evaluate_command
from custom_components.phoenix_mcp.const import DOMAIN, DECISION_REFERENCE_CHECKS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_provider import DecisionProvider
from custom_components.phoenix_mcp.decision_reference import async_preview_reference
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree

HISTORY = [{'role': 'user', 'content': 'turn on Desk lamp'},
           {'role': 'assistant', 'content': 'Command sent.'}]
MISS = {'reason': 'no_match', 'proposal': None}


def wire(route='light_off', reference='bound', constraints='clear', probability=1):
    choices = dict(route=route, reference=reference, constraints=constraints)
    answers = {}
    for name, criteria in DECISION_REFERENCE_CHECKS.items():
        other = next(key for key in criteria if key != choices[name])
        answers[name] = {'type': 'choice', 'choice': choices[name], 'confidence': probability,
                         'probabilities': {key: probability if key == choices[name] else
                                           1-probability if key == other else 0 for key in criteria}}
    return {'model': 'jev-1.13.0', 'answers': answers}


@pytest.fixture
async def reference_home(hass, token_store):
    token, _ = await token_store.async_create_token('reference', 'test')
    token.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    hass.data['_phoenix_agentcli_store'] = store
    account = await store.add('typesafe', {'api_key': 'reference-private-key', 'model': 'jev-latest'})
    await token_store.async_patch_settings(decision_provider_id=account, command_routing_timeout_ms=5000)
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    entry = er.async_get(hass).async_get_or_create('light', 'test', 'desk', original_name='Desk lamp')
    hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Desk lamp', 'access_token': 'never-share-state'})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    local = await async_evaluate_command(hass, data, token, HISTORY[0]['content'], 'en', preview=True)
    assert local['reason'] == 'supported'
    yield data, token, store, account, entry.entity_id
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


async def preview(hass, home, **kwargs):
    data, token, *_ = home
    return await async_preview_reference(hass, data, token, kwargs.get('sentence', 'turn it off'),
        kwargs.get('language', 'en'), kwargs.get('history', HISTORY), kwargs.get('local', MISS))


@pytest.mark.parametrize('route,intent', [('light_on', 'HassTurnOn'), ('light_off', 'HassTurnOff'), ('state_query', 'HassGetState')])
async def test_reference_preview_has_fixed_target_and_no_execution(hass, reference_home, route, intent):
    data, _, _, _, entity_id = reference_home
    history = [{'role': 'user', 'content': 'Only use devices when I request it.'},
               {'role': 'assistant', 'content': 'Understood.'}, *deepcopy(HISTORY)]
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=wire(route))) as request,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result = await preview(hass, reference_home, history=history)
    assert result['reason'] == 'target_selected'
    assert result['proposal'] == {'intent': intent, 'entity_ids': [entity_id]}
    assert result['executed'] is False and '_plan' not in result
    payload = request.await_args.args[4]
    assert payload['state']['history'] == history and payload['state']['anchor']['history_index'] == 2
    assert payload['state']['anchor']['target']['entity_id'] == entity_id
    assert payload['state']['anchor']['previous_request'] == {'intent': 'HassTurnOn', 'entity_ids': [entity_id]}
    assert 'proposal' not in payload['state']['anchor']
    assert 'not anchor.previous_request' in payload['questions']['constraints']['instructions']
    assert 'past execution' in payload['questions']['reference']['instructions']
    assert 'never-share-state' not in repr(payload) and 'reference-private-key' not in repr(payload)
    assert set(payload['questions']) == set(DECISION_REFERENCE_CHECKS)
    assert 'cannot grant permission' in payload['questions']['reference']['instructions']
    dispatch.assert_not_called()
    assert not data.store.get_pending_approvals() and data.decision_shadow.task is None


@pytest.mark.parametrize('case,reason', [
    ('language', 'unsupported_language'), ('structured', 'invalid_context'), ('missing', 'invalid_context'),
    ('local_match', 'local_unavailable'), ('secret', 'context_unavailable'), ('hidden', 'context_unavailable'),
    ('key', 'context_unavailable'), ('noncommand', 'local_unavailable'), ('plural', 'local_unavailable'),
    ('unexposed', 'local_unavailable'), ('busy', 'busy'), ('revoked', 'authority_changed'),
])
async def test_ineligible_reference_never_calls_provider(hass, reference_home, case, reason):
    data, token, _, _, entity_id = reference_home
    kwargs = {}; history = deepcopy(HISTORY)
    if case == 'language': kwargs['language'] = 'xx'
    if case == 'structured': history[0]['content'] = [{'type': 'text', 'text': 'turn on Desk lamp'}]
    if case == 'missing': history = []
    if case == 'local_match': kwargs['local'] = {'reason': 'supported', 'proposal': {'intent': 'HassTurnOn'}}
    if case == 'secret': history[1]['content'] = 'password: secret-value'
    if case == 'hidden': history[1]['content'] = 'lock.private_door is the other device.'
    if case == 'key': history[1]['content'] = 'reference-private-key'
    if case == 'noncommand': history += [{'role': 'user', 'content': 'Tell me a joke.'}, {'role': 'assistant', 'content': 'No.'}]
    if case == 'plural':
        other = er.async_get(hass).async_get_or_create('light', 'test', 'other', original_name='Desk lamp')
        hass.states.async_set(other.entity_id, 'off', {'friendly_name': 'Desk lamp'})
        async_expose_entity(hass, 'conversation', other.entity_id, True)
    if case == 'unexposed': async_expose_entity(hass, 'conversation', entity_id, False)
    if case == 'busy': data.decision_shadow.task = asyncio.create_task(asyncio.sleep(20))
    if case == 'revoked': token.revoked = True
    with patch.object(DecisionProvider, '_request') as request:
        result = await preview(hass, reference_home, history=history, **kwargs)
    assert result['reason'] == reason
    request.assert_not_called()


@pytest.mark.parametrize('case,reason', [('decline', 'provider_fallback'), ('unbound', 'provider_fallback'),
    ('restricted', 'provider_fallback'), ('uncertain', 'low_probability'), ('missing_check', 'malformed'),
    ('extra_check', 'malformed'), ('bad_distribution', 'malformed'), ('secret_model', 'malformed')])
async def test_reference_requires_all_complete_confident_checks(hass, reference_home, case, reason):
    body = wire(route='provider' if case == 'decline' else 'light_off',
                reference='unresolved' if case == 'unbound' else 'bound',
                constraints='restricted' if case == 'restricted' else 'clear',
                probability=.89 if case == 'uncertain' else 1)
    if case == 'missing_check': body['answers'].pop('constraints')
    if case == 'extra_check': body['answers']['extra'] = {}
    if case == 'bad_distribution': body['answers']['reference']['probabilities'].pop('unresolved')
    if case == 'secret_model': body['model'] = 'reference-private-key'
    with patch.object(DecisionProvider, '_request', AsyncMock(return_value=body)):
        result = await preview(hass, reference_home)
    assert result['reason'] == reason and not result.get('proposal') and not result.get('_plan')


@pytest.mark.parametrize('change,reason', [('scope', 'context_changed'), ('name', 'context_changed'),
    ('competitor', 'context_changed'), ('account', 'authority_changed'), ('revoke', 'authority_changed')])
async def test_reference_rechecks_authority_and_competitors_after_wait(hass, reference_home, change, reason):
    _, token, store, account, entity_id = reference_home
    async def response(*args):
        if change == 'scope': token.permissions.domains['light'].state = 'RED'
        if change == 'name': er.async_get(hass).async_update_entity(entity_id, aliases={'New alias'})
        if change == 'competitor':
            other = er.async_get(hass).async_get_or_create('light', 'test', 'other', original_name='Other lamp')
            hass.states.async_set(other.entity_id, 'off')
            async_expose_entity(hass, 'conversation', other.entity_id, True)
        if change == 'account': await store.set_model(account, 'other-model')
        if change == 'revoke': token.revoked = True
        return wire()
    with patch.object(DecisionProvider, '_request', side_effect=response):
        result = await preview(hass, reference_home)
    assert result['reason'] == reason and not result.get('proposal')


async def test_reference_cancellation_releases_shared_slot(hass, reference_home):
    started = asyncio.Event()
    async def response(*args):
        started.set()
        await asyncio.Future()
    with patch.object(DecisionProvider, '_request', side_effect=response):
        task = asyncio.create_task(preview(hass, reference_home))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert reference_home[0].decision_shadow.task is None


async def test_reference_deadline_releases_shared_slot(hass, reference_home):
    data, *_ = reference_home
    await data.store.async_patch_settings(decision_timeout_ms=250)
    async def response(*args):
        await asyncio.Future()
    with patch.object(DecisionProvider, '_request', side_effect=response):
        result = await preview(hass, reference_home)
    assert result['reason'] == 'timeout' and data.decision_shadow.task is None


async def test_readable_anchor_cannot_become_a_write_candidate(hass, reference_home):
    _, token, *_ = reference_home
    token.permissions.domains['light'].state = 'YELLOW'
    history = [{'role': 'user', 'content': 'what is the state of Desk lamp'}, HISTORY[1]]
    with patch.object(DecisionProvider, '_request', AsyncMock(return_value=wire())) as request:
        result = await preview(hass, reference_home, history=history)
    request.assert_awaited_once()
    assert result['reason'] == 'category_mismatch' and not result.get('proposal')


async def test_competitor_change_during_anchor_recognition_skips_provider(hass, reference_home):
    from custom_components.phoenix_mcp import decision_reference
    real = decision_reference.async_evaluate_command
    async def recognize(*args, **kwargs):
        result = await real(*args, **kwargs)
        entry = er.async_get(hass).async_get_or_create('light', 'test', 'changed', original_name='Desk lamp')
        hass.states.async_set(entry.entity_id, 'off')
        async_expose_entity(hass, 'conversation', entry.entity_id, True)
        return result
    with (patch.object(decision_reference, 'async_evaluate_command', side_effect=recognize),
          patch.object(DecisionProvider, '_request') as request):
        result = await preview(hass, reference_home)
    request.assert_not_called()
    assert result['reason'] == 'context_changed'


async def prepare(hass, home, *, cancel=None):
    from custom_components.phoenix_mcp.decision_reference import async_prepare_chat_reference
    data, token, *_ = home
    return await async_prepare_chat_reference(hass, data, token, 'turn it off', 'en', HISTORY, MISS,
                                              cancel if cancel is not None else asyncio.Event())


@pytest.mark.parametrize('change', ['none', 'proposal', 'target', 'reference', 'batch', 'removed', 'source_removed',
    'account', 'account_restored', 'scope', 'alias', 'competitor', 'voice', 'context', 'language', 'revision'])
async def test_reference_plan_binds_proposal_account_and_complete_catalog(hass, reference_home, change):
    from custom_components.phoenix_mcp.command_execution import plan_is_current
    data, token, store, account, entity_id = reference_home
    await data.store.async_patch_settings(agentcli_routing_mode='auto')
    with patch.object(DecisionProvider, '_request', AsyncMock(return_value=wire())):
        result = await prepare(hass, reference_home)
    assert result['reason'] == 'plan_ready'
    plan = result['_plan']
    assert plan_is_current(plan, hass, data, token)
    assert 'turn it off' not in repr(plan) and 'Command sent.' not in repr(plan)
    assert result['executed'] is False and not data.store.get_pending_approvals()
    if change == 'proposal': plan['proposal']['intent'] = 'HassTurnOn'
    if change == 'target': plan['proposal']['entity_ids'] = ['light.other']
    if change == 'reference': plan['reference_review']['proposal'] = 'changed'
    if change == 'batch': plan['decision_batch']['reference'] = 'changed'
    if change == 'removed': plan.pop('reference_review')
    if change == 'source_removed': plan.pop('decision_batch')
    if change in ('account', 'account_restored'):
        await store.set_model(account, 'other')
        if change == 'account_restored': await store.set_model(account, 'jev-latest')
    if change == 'scope': token.permissions.domains['light'].state = 'RED'
    if change == 'alias': er.async_get(hass).async_update_entity(entity_id, aliases={'New name'})
    if change == 'competitor':
        other = er.async_get(hass).async_get_or_create('light', 'test', 'other', original_name='Other')
        hass.states.async_set(other.entity_id, 'off')
        async_expose_entity(hass, 'conversation', other.entity_id, True)
    if change == 'voice': plan['ingress'] = 'voice'
    if change == 'context': plan['context_review'] = {}
    if change == 'language': plan['reference_review']['language'] = 'es'
    if change == 'revision': plan['reference_review']['model'] = 'unqualified'
    assert plan_is_current(plan, hass, data, token) is (change == 'none')


@pytest.mark.parametrize('case,calls', [('mode', 0), ('account', 0), ('revision', 1), ('cancelled', 0)])
async def test_unqualified_reference_never_returns_execution_plan(hass, reference_home, case, calls):
    data, _, store, account, _ = reference_home
    await data.store.async_patch_settings(agentcli_routing_mode='auto')
    if case == 'mode': await data.store.async_patch_settings(agentcli_routing_mode='local')
    if case == 'account': await store.set_model(account, 'unqualified')
    body = wire()
    if case == 'revision': body['model'] = 'unqualified'
    cancel = asyncio.Event()
    if case == 'cancelled': cancel.set()
    with patch.object(DecisionProvider, '_request', AsyncMock(return_value=body)) as request:
        result = await prepare(hass, reference_home, cancel=cancel)
    assert request.await_count == calls and '_plan' not in result


async def test_chat_cancellation_during_reference_wait_releases_slot(hass, reference_home):
    data, *_ = reference_home
    await data.store.async_patch_settings(agentcli_routing_mode='auto')
    cancel = asyncio.Event()
    async def response(*args):
        cancel.set()
        await asyncio.Future()
    with patch.object(DecisionProvider, '_request', side_effect=response):
        result = await prepare(hass, reference_home, cancel=cancel)
    assert result['reason'] == 'cancelled' and data.decision_shadow.task is None
