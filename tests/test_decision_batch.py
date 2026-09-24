"""Joint previews never authorize effects or weaken full-catalog target admission."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp import agentcli, device_semantics
from custom_components.phoenix_mcp.const import DOMAIN, DECISION_EXECUTION_CATEGORIES, DECISION_MAX_TARGETS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_batch import async_preview_batch, capture_batch_targets
from custom_components.phoenix_mcp.decision_provider import DecisionProvider, DecisionError
from custom_components.phoenix_mcp.decision_targets import capture_targets
from custom_components.phoenix_mcp.token_store import PermissionTree, PermissionNode


@pytest.fixture
async def batch_home(hass, token_store):
    token, _ = await token_store.async_create_token('batch', 'test')
    token.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add('typesafe', {'api_key': 'test-secret', 'model': 'jev-latest'})
    hass.data['_phoenix_agentcli_store'] = store
    await token_store.async_patch_settings(decision_provider_id=account)
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    entry = er.async_get(hass).async_get_or_create('light', 'test', 'desk', original_name='Desk lamp')
    hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Desk lamp', 'private': 'private-state'})
    yield data, token, store, account, entry.entity_id
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


def wire_response(payload, category='light_on', target='t0'):
    return {'model': 'jev-1.13.0', 'answers': {
        question: {'type': 'choice', 'choice': selected, 'confidence': 1,
                   'probabilities': {key: float(key == selected) for key in payload['questions'][question]['criteria']}}
        for question, selected in (('route', category), ('target', target))}}


async def preview(hass, home, local=None):
    data, token, *_ = home
    return await async_preview_batch(hass, data, token, 'Switch on the desk lamp', 'en',
                                      local or {'reason': 'no_match', 'proposal': None})


@pytest.mark.parametrize('protocol', ['systemone', 'classifier'])
async def test_one_real_request_with_two_independent_closed_choices(protocol, socket_enabled):
    seen = []
    async def handler(request):
        payload = await request.json(); seen.append(payload)
        return web.json_response(wire_response(payload))
    app = web.Application(); app.router.add_post('/' + protocol, handler)
    async with TestServer(app) as server, ClientSession() as session:
        provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'm', str(server.make_url('')).rstrip('/')), protocol)
        result = await provider.route_and_target(session, 'switch it on', {'t0': {'eligible_categories': ['light_on']}}, 1000)
    assert len(seen) == 1 and set(seen[0]['questions']) == {'route', 'target'}
    assert 'light_brightness' not in seen[0]['questions']['route']['criteria']
    assert result['route']['choice'] == 'light_on' and result['target']['choice'] == 't0'


@pytest.mark.parametrize('damage', ['missing', 'extra', 'invented', 'nonfinite', 'secret_model', 'wrong_distribution'])
async def test_malformed_joint_answers_are_rejected(damage):
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'm', 'https://example.test', 'test-secret'), 'systemone')
    async def response(*args):
        body = wire_response(args[4])
        if damage == 'missing': del body['answers']['target']
        if damage == 'extra': body['answers']['execute'] = {}
        if damage == 'invented': body['answers']['target']['choice'] = 'light.hidden'
        if damage == 'nonfinite': body['answers']['route']['confidence'] = float('nan')
        if damage == 'secret_model': body['model'] = 'test-secret'
        if damage == 'wrong_distribution': body['answers']['target']['probabilities']['none'] = 1
        return body
    with patch.object(provider, '_request', side_effect=response), pytest.raises(DecisionError, match='malformed'):
        await provider.route_and_target(MagicMock(), 'command', {'t0': {}}, 1000)


@pytest.mark.parametrize('matched', [False, True])
async def test_joint_preview_compares_local_match_without_execution(hass, batch_home, matched):
    data, _, _, _, entity_id = batch_home
    local = {'reason': 'supported' if matched else 'no_match',
             'proposal': {'intent': 'HassTurnOn', 'entity_ids': [entity_id]} if matched else None}
    async def response(*args):
        payload = args[4]
        assert 'private-state' not in json.dumps(payload)
        assert payload['state']['targets']['t0']['eligible_categories'] == ['light_off', 'light_on', 'state_query']
        return wire_response(payload)
    with (patch.object(DecisionProvider, '_request', side_effect=response) as request,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result = await preview(hass, batch_home, local)
    request.assert_awaited_once(); dispatch.assert_not_called()
    assert result['reason'] == 'target_selected' and result['executed'] is False and '_plan' not in result
    assert result['local_agreement'] is (True if matched else None)
    assert not data.store.get_pending_approvals() and data.decision_shadow.observations == 0
    assert 'Desk lamp' not in repr(data.decision_shadow)
    assert data.decision_shadow.task is None


@pytest.mark.parametrize('case', ['denied', 'unexposed', 'unavailable', 'oversized', 'trigger', 'timeout'])
async def test_no_request_when_catalog_or_local_recognition_is_ineligible(hass, batch_home, case):
    _, token, _, _, entity_id = batch_home
    local = None
    if case == 'denied': token.permissions = PermissionTree()
    if case == 'unexposed': async_expose_entity(hass, 'conversation', entity_id, False)
    if case == 'unavailable': hass.states.async_set(entity_id, 'unavailable')
    if case == 'oversized':
        for index in range(DECISION_MAX_TARGETS):
            entry = er.async_get(hass).async_get_or_create('light', 'test', str(index))
            hass.states.async_set(entry.entity_id, 'off')
    if case in ('trigger', 'timeout'): local = {'reason': 'sentence_trigger' if case == 'trigger' else 'timeout', 'proposal': None}
    with patch.object(DecisionProvider, '_request') as request:
        result = await preview(hass, batch_home, local)
    request.assert_not_called()
    assert result['reason'] in ('no_target', 'too_many_targets', 'local_unavailable')


async def test_category_must_match_target_authority_even_with_high_probability(hass, batch_home):
    _, token, *_ = batch_home
    token.permissions = PermissionTree(domains={'light': PermissionNode(state='YELLOW')})
    async def response(*args): return wire_response(args[4])
    with patch.object(DecisionProvider, '_request', side_effect=response):
        result = await preview(hass, batch_home)
    assert result['reason'] == 'category_mismatch' and 'proposal' not in result


@pytest.mark.parametrize('change', ['scope', 'competing_entity', 'unexposed', 'account', 'settings'])
async def test_wait_rechecks_full_catalog_and_authority(hass, batch_home, change):
    data, token, store, account, entity_id = batch_home
    async def response(*args):
        if change == 'scope': token.permissions = PermissionTree()
        if change == 'competing_entity':
            entry = er.async_get(hass).async_get_or_create('light', 'test', 'other', original_name='Desk lamp')
            hass.states.async_set(entry.entity_id, 'off')
        if change == 'unexposed': async_expose_entity(hass, 'conversation', entity_id, False)
        if change == 'account': await store.set_model(account, 'changed')
        if change == 'settings': await data.store.async_patch_settings(kill_switch=True)
        return wire_response(args[4])
    with patch.object(DecisionProvider, '_request', side_effect=response):
        result = await preview(hass, batch_home)
    assert result['reason'] in ('targets_changed', 'authority_changed') and 'decision' not in result


async def test_batch_shares_one_graph_without_changing_complete_category_catalogs(hass, batch_home):
    _, token, *_ = batch_home
    for index in range(3):
        entry = er.async_get(hass).async_get_or_create('light', 'test', f'extra_{index}')
        hass.states.async_set(entry.entity_id, 'off')
    expected = {category: capture_targets(hass, token, category) for category in DECISION_EXECUTION_CATEGORIES}
    with patch.object(device_semantics, 'device_registry_snapshot_from_registry',
                      wraps=device_semantics.device_registry_snapshot_from_registry) as build:
        targets, snapshots = capture_batch_targets(hass, token)
    build.assert_called_once()
    assert len(targets) == 4 and snapshots == expected


@pytest.mark.parametrize('change', ['unchanged', 'area'])
async def test_batch_recaptures_device_graph_after_model_wait(hass, batch_home, change):
    _, _, _, _, entity_id = batch_home
    config = MockConfigEntry(domain='test')
    config.add_to_hass(hass)
    registry = dr.async_get(hass)
    area = ar.async_get(hass).async_create('Office')
    parent = registry.async_get_or_create(config_entry_id=config.entry_id,
                                         identifiers={('test', 'parent')})
    registry.async_update_device(parent.id, area_id=area.id)
    child = registry.async_get_or_create_child(config_entry_id=config.entry_id,
                                               identifiers={('test', 'child')},
                                               parent_device_id=parent.id)
    er.async_get(hass).async_update_entity(entity_id, device_id=child.id)

    async def response(*args):
        assert args[4]['state']['targets']['t0']['area'] == 'Office'
        if change == 'area':
            updated_area = ar.async_get(hass).async_create('Study')
            registry.async_update_device(parent.id, area_id=updated_area.id)
        return wire_response(args[4])

    with (patch.object(DecisionProvider, '_request', side_effect=response) as request,
          patch.object(device_semantics, 'device_registry_snapshot_from_registry',
                       wraps=device_semantics.device_registry_snapshot_from_registry) as build):
        result = await preview(hass, batch_home)
    request.assert_awaited_once()
    assert build.call_count == 2
    assert result['reason'] == ('target_selected' if change == 'unchanged' else 'targets_changed')
    if change != 'unchanged':
        assert 'proposal' not in result and 'decision' not in result


async def test_batch_cancellation_releases_only_owned_slot(hass, batch_home):
    data, *_ = batch_home
    entered, closed = asyncio.Event(), asyncio.Event()
    async def response(*args):
        entered.set()
        try: await asyncio.Event().wait()
        finally: closed.set()
    with patch.object(DecisionProvider, '_request', side_effect=response) as request:
        pending = asyncio.create_task(preview(hass, batch_home))
        await entered.wait()
        assert (await preview(hass, batch_home))['reason'] == 'busy'
        data.decision_shadow.cancel()
        assert (await pending)['reason'] == 'cancelled'
    assert closed.is_set() and data.decision_shadow.task is None and request.await_count == 1


@pytest.mark.parametrize('text', ['password: private-password', 'test-secret', 'switch.hidden'])
async def test_sensitive_utterance_not_shared(hass, batch_home, text):
    data, token, *_ = batch_home
    with patch.object(DecisionProvider, '_request') as request:
        result = await async_preview_batch(hass, data, token, text, 'en', {'reason': 'no_match'})
    request.assert_not_called()
    assert result['reason'] == 'context_unavailable'


async def test_union_catalog_retains_read_only_entities_without_write_eligibility(hass, batch_home):
    _, token, *_ = batch_home
    token.permissions.domains['sensor'] = PermissionNode(state='YELLOW')
    entry = er.async_get(hass).async_get_or_create('sensor', 'test', 'temperature')
    hass.states.async_set(entry.entity_id, '20')
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    targets, _ = capture_batch_targets(hass, token)
    assert len(targets) == 2
    assert targets['t1']['eligible_categories'] == ['state_query']
