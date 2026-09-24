"""Context previews preserve complete input, privacy, authority and inert behavior."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli
from custom_components.phoenix_mcp.command_routing import async_evaluate_command
from custom_components.phoenix_mcp.const import (
    DOMAIN, DECISION_CONTEXT_CHOICES, DECISION_CONTEXT_MAX_BYTES, DECISION_CONTEXT_MAX_MESSAGES,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_context import async_preview_context, context_history
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider
from custom_components.phoenix_mcp.helpers import redact_inaccessible_entity_ids_in_text
from custom_components.phoenix_mcp.token_store import PermissionTree, PermissionNode

HISTORY = [{'role': 'user', 'content': 'We are discussing how brightness works.'},
           {'role': 'assistant', 'content': 'Brightness is expressed as a percentage.'}]
SENTENCE = 'set Desk lamp to thirty seven percent brightness'


def answer(choice='compatible', probability=1.0):
    return {'choice': choice, 'probabilities': {k: probability if k == choice else 1-probability
            for k in DECISION_CONTEXT_CHOICES}, 'confidence': probability, 'model': 'test-model'}


@pytest.fixture
async def context_home(hass, token_store):
    token, _ = await token_store.async_create_token('context_preview', 'test')
    token.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add('typesafe', {'api_key': 'test-private-key', 'model': 'jev-latest'})
    await token_store.async_patch_settings(decision_provider_id=account)
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    entry = er.async_get(hass).async_get_or_create('light', 'test', 'desk', original_name='Desk lamp')
    hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Desk lamp',
        'supported_color_modes': ['brightness'], 'access_token': 'never-send-state'})
    local = await async_evaluate_command(hass, data, token, SENTENCE, 'en', preview=True)
    assert local['reason'] == 'supported' and local['proposal']['brightness_pct'] == 37
    with patch.object(agentcli, '_get_secret_store', AsyncMock(return_value=store)):
        yield data, token, store, account, entry.entity_id, local
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


async def preview(hass, home, history=None, local=None):
    data, token, _, _, _, recognized = home
    return await async_preview_context(hass, data, token, SENTENCE, 'en',
                                       HISTORY if history is None else history,
                                       recognized if local is None else local)


@pytest.mark.parametrize('bad', [None, {}, [], HISTORY[:1], HISTORY[::-1],
    [{'role': 'system', 'content': 'do it'}, HISTORY[1]],
    [dict(HISTORY[0], extra=True), HISTORY[1]],
    [dict(HISTORY[0], content=['text']), HISTORY[1]],
    [dict(HISTORY[0], content=' '), HISTORY[1]],
    HISTORY * (DECISION_CONTEXT_MAX_MESSAGES // 2 + 1),
    [dict(HISTORY[0], content='x' * DECISION_CONTEXT_MAX_BYTES), HISTORY[1]],
    [dict(HISTORY[0], content='灯' * (DECISION_CONTEXT_MAX_BYTES // 2)), HISTORY[1]],
    [dict(HISTORY[0], content='\ud800'), HISTORY[1]],
])
def test_invalid_history_never_becomes_a_partial_context(bad):
    assert context_history(bad) is None


def test_history_is_copied_without_rewriting():
    history = deepcopy(HISTORY)
    result = context_history(history)
    history[0]['content'] = 'changed'
    assert result == HISTORY


@pytest.mark.parametrize('choice,probability,reason', [('compatible', 1, 'compatible'),
    ('compatible', .89, 'low_probability'), ('compatible', .5, 'low_probability'),
    ('provider', 1, 'provider_fallback')])
async def test_review_is_inert_and_keeps_fixed_numeric_candidate(hass, context_home, choice, probability, reason):
    data, _, _, _, entity, _ = context_home
    before = data.decision_shadow.summary()
    with (patch.object(DecisionProvider, 'review_context', AsyncMock(return_value=answer(choice, probability))) as review,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result = await preview(hass, context_home)
    assert result['reason'] == reason and result['executed'] is False
    assert set(result) == {'reason', 'decision', 'executed', 'elapsed_ms'}
    state = review.await_args.args[1]
    assert state['history'] == HISTORY and state['utterance'] == SENTENCE
    assert state['candidate'] == {'intent': 'HassLightSet', 'entity_ids': [entity], 'brightness_pct': 37}
    assert state['service']['service_data'] == {'brightness_pct': 37}
    assert 'never-send-state' not in json.dumps(state)
    assert data.decision_shadow.summary() == before
    assert not data.store.get_pending_approvals()
    assert HISTORY[0]['content'] not in repr(data.decision_shadow)
    dispatch.assert_not_called()


@pytest.mark.parametrize('reason', ['no_match', 'no_target', 'timeout', 'sentence_trigger', 'unsupported'])
async def test_no_eligible_local_command_skips_provider(hass, context_home, reason):
    with patch.object(DecisionProvider, 'review_context') as review:
        result = await preview(hass, context_home, local={'reason': reason, 'proposal': None})
    assert result['reason'] == 'local_unavailable'
    review.assert_not_called()


@pytest.mark.parametrize('text', ['token=test-private-key', 'Do not operate switch.hidden.',
    'Do not operate switch.hidden!', 'Do not operate switch.hidden right now.',
    'Do not operate switch.hidden...',
    'password: very-private-password', 'x' * DECISION_CONTEXT_MAX_BYTES])
async def test_sensitive_or_oversized_history_is_not_rewritten_and_sent(hass, context_home, text):
    history = [dict(HISTORY[0], content=text), HISTORY[1]]
    with patch.object(DecisionProvider, 'review_context') as review:
        result = await preview(hass, context_home, history)
    assert result['reason'] in ('context_unavailable', 'invalid_context')
    review.assert_not_called()


async def test_entity_privacy_preserves_permitted_ids_and_punctuation(hass, context_home):
    _, token, _, _, entity, _ = context_home
    text = f'{entity}. {entity}! switch.hidden. switch.hidden... host.example.com'
    assert redact_inaccessible_entity_ids_in_text(text, token, hass) == (
        f'{entity}. {entity}! <redacted-entity>. <redacted-entity>... host.example.com')


@pytest.mark.parametrize('change,reason', [('token', 'authority_changed'), ('target', 'context_changed')])
async def test_secret_store_wait_rechecks_before_sending(hass, context_home, change, reason):
    _, token, store, _, entity, _ = context_home
    async def load(*args):
        if change == 'token':
            token.revoked = True
        else:
            er.async_get(hass).async_update_entity(entity, name='Changed target')
        return store
    with (patch.object(agentcli, '_get_secret_store', side_effect=load),
          patch.object(DecisionProvider, 'review_context') as review):
        result = await preview(hass, context_home)
    assert result['reason'] == reason
    review.assert_not_called()


async def test_total_payload_budget_includes_command_and_candidate(hass, context_home):
    history = [dict(HISTORY[0], content='x' * (DECISION_CONTEXT_MAX_BYTES - 250)), HISTORY[1]]
    assert context_history(history) is not None
    with patch.object(DecisionProvider, 'review_context') as review:
        result = await preview(hass, context_home, history)
    assert result['reason'] == 'context_unavailable'
    review.assert_not_called()


@pytest.mark.parametrize('mutation', ['intent', 'extra', 'targets', 'numeric', 'wrong_numeric', 'read'])
async def test_fixed_candidate_projection_validation(hass, context_home, mutation):
    local = deepcopy(context_home[-1])
    proposal = local['proposal']
    if mutation == 'intent': proposal['intent'] = []
    if mutation == 'extra': proposal['service'] = 'turn_off'
    if mutation == 'targets': proposal['entity_ids'].append('light.other')
    if mutation == 'numeric': proposal['brightness_pct'] = float('nan')
    if mutation == 'wrong_numeric': proposal['brightness_pct'] = '37'
    if mutation == 'read': proposal.update(intent='HassGetState')
    with patch.object(DecisionProvider, 'review_context') as review:
        result = await preview(hass, context_home, local=local)
    assert result['reason'] == 'context_unavailable'
    review.assert_not_called()


async def test_primary_state_candidate_does_not_send_state(hass, context_home):
    local = deepcopy(context_home[-1])
    local['proposal'] = {'intent': 'HassGetState', 'entity_ids': [context_home[-2]]}
    with patch.object(DecisionProvider, 'review_context', AsyncMock(return_value=answer())) as review:
        result = await preview(hass, context_home, local=local)
    assert result['reason'] == 'compatible'
    state = review.await_args.args[1]
    assert 'service' not in state and 'never-send-state' not in json.dumps(state)


@pytest.mark.parametrize('change,reason', [('token', 'authority_changed'), ('settings', 'authority_changed'),
    ('account', 'authority_changed'), ('change_back', 'authority_changed'), ('deleted', 'authority_changed'),
    ('runtime', 'authority_changed'), ('target', 'context_changed'), ('scope', 'context_changed')])
async def test_wait_rechecks_relevant_authority_and_context(hass, context_home, change, reason):
    data, token, store, account, entity, _ = context_home
    async def respond(*args):
        if change == 'token': token.revoked = True
        if change == 'settings': await data.store.async_patch_settings(kill_switch=True)
        if change in ('account', 'change_back'):
            await store.set_model(account, 'changed-model')
            if change == 'change_back': await store.set_model(account, 'jev-latest')
        if change == 'deleted': await store.delete(account)
        if change == 'runtime': hass.data[DOMAIN] = None
        if change == 'target': er.async_get(hass).async_update_entity(entity, name='Different lamp')
        if change == 'scope': token.permissions = PermissionTree()
        return answer()
    with patch.object(DecisionProvider, 'review_context', side_effect=respond):
        result = await preview(hass, context_home)
    assert result['reason'] == reason and result['decision'] is None


@pytest.mark.parametrize('failure', ['timeout', 'authentication', 'malformed', 'unexpected'])
async def test_provider_failures_are_generic_and_inert(hass, context_home, failure):
    error = ValueError('private-history') if failure == 'unexpected' else DecisionError(failure)
    with patch.object(DecisionProvider, 'review_context', side_effect=error):
        result = await preview(hass, context_home)
    assert result['reason'] == ('unavailable' if failure == 'unexpected' else failure)
    assert result['decision'] is None and 'private-history' not in json.dumps(result)


@pytest.mark.parametrize('cancel_kind', ['owner', 'caller', 'timeout'])
async def test_context_uses_shared_slot_and_cancellation(hass, context_home, cancel_kind):
    data, token, *_ = context_home
    entered, exited = asyncio.Event(), asyncio.Event()
    if cancel_kind == 'timeout': await data.store.async_patch_settings(decision_timeout_ms=250)
    async def wait(*args):
        entered.set()
        try: await asyncio.Event().wait()
        finally: exited.set()
    with patch.object(DecisionProvider, 'review_context', side_effect=wait):
        pending = asyncio.create_task(preview(hass, context_home))
        await entered.wait()
        assert (await preview(hass, context_home))['reason'] == 'busy'
        assert data.decision_shadow.start(hass, data, token, 'hello', 'en') is None
        if cancel_kind == 'owner': data.decision_shadow.cancel()
        if cancel_kind == 'caller': pending.cancel()
        if cancel_kind == 'caller':
            with pytest.raises(asyncio.CancelledError): await pending
        else:
            assert (await pending)['reason'] == ('timeout' if cancel_kind == 'timeout' else 'cancelled')
    assert exited.is_set() and data.decision_shadow.task is None
    assert not data.store.get_pending_approvals()


@pytest.mark.parametrize('protocol', ['systemone', 'classifier'])
async def test_context_wire_requires_exact_named_question(protocol, socket_enabled):
    seen = []
    async def handler(request):
        wire = await request.json(); seen.append(wire)
        return web.json_response({'model': 'test-model', 'answers': {'context': {
            'type': 'choice', **{k:v for k,v in answer().items() if k != 'model'}}}})
    app = web.Application(); app.router.add_post('/'+protocol, handler)
    state = {'utterance': SENTENCE, 'history': HISTORY, 'candidate': {'brightness_pct': 37}, 'language': 'en'}
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig('typesafe', 'test-model', str(server.make_url('')).rstrip('/'))
        response = await DecisionProvider(cfg, protocol).review_context(session, state, 1000)
    assert response == answer() and len(seen) == 1
    assert seen[0]['state'] == state and set(seen[0]['questions']) == {'context'}
    assert seen[0]['questions']['context']['criteria'] == DECISION_CONTEXT_CHOICES


@pytest.mark.parametrize('mutation', ['extra_question', 'wrong_question', 'bad_choice', 'bad_distribution', 'secret'])
async def test_context_rejects_malformed_or_secret_provider_answers(mutation):
    body = {'model': 'test-model', 'answers': {'context': {'type': 'choice',
        **{k: v for k, v in answer().items() if k != 'model'}}}}
    if mutation == 'extra_question': body['answers']['target'] = {}
    if mutation == 'wrong_question': body['answers']['route'] = body['answers'].pop('context')
    if mutation == 'bad_choice': body['answers']['context']['choice'] = 'execute'
    if mutation == 'bad_distribution': body['answers']['context']['probabilities']['compatible'] = float('nan')
    if mutation == 'secret': body['model'] = 'secret-api-key'
    cfg = agentcli.ProviderConfig('typesafe', 'test-model', 'https://example.test', api_key='secret-api-key')
    provider = DecisionProvider(cfg, 'systemone')
    with patch.object(provider, '_request', AsyncMock(return_value=body)):
        with pytest.raises(DecisionError, match='^malformed$'):
            await provider.review_context(MagicMock(), {}, 1000)
