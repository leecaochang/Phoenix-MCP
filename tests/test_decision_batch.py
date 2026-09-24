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
from custom_components.phoenix_mcp.const import (
    DOMAIN, DECISION_EXECUTION_CATEGORIES, DECISION_MAX_TARGETS, DECISION_PERCENTAGE_PREVIEW_MAX_TARGETS,
)
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


async def preview(hass, home, local=None, *, numeric_preview=False):
    data, token, *_ = home
    return await async_preview_batch(hass, data, token, 'Switch on the desk lamp', 'en',
                                      local or {'reason': 'no_match', 'proposal': None}, numeric_preview=numeric_preview)


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
    assert not {'light_brightness', 'media_volume', 'cover_position'} & set(seen[0]['questions']['route']['criteria'])
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
@pytest.mark.parametrize('numeric', [False, True])
async def test_no_request_when_catalog_or_local_recognition_is_ineligible(hass, batch_home, case, numeric):
    _, token, _, _, entity_id = batch_home
    local = None
    if case == 'denied': token.permissions = PermissionTree()
    if case == 'unexposed': async_expose_entity(hass, 'conversation', entity_id, False)
    if case == 'unavailable': hass.states.async_set(entity_id, 'unavailable')
    if case == 'oversized':
        for index in range(DECISION_PERCENTAGE_PREVIEW_MAX_TARGETS if numeric else DECISION_MAX_TARGETS):
            entry = er.async_get(hass).async_get_or_create('light', 'test', str(index))
            hass.states.async_set(entry.entity_id, 'off')
    if case in ('trigger', 'timeout'): local = {'reason': 'sentence_trigger' if case == 'trigger' else 'timeout', 'proposal': None}
    with patch.object(DecisionProvider, '_request') as request:
        result = await preview(hass, batch_home, local, numeric_preview=numeric)
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


@pytest.mark.parametrize('numeric', [False, True])
async def test_batch_cancellation_releases_only_owned_slot(hass, batch_home, numeric):
    data, *_ = batch_home
    entered, closed = asyncio.Event(), asyncio.Event()
    async def response(*args):
        entered.set()
        try: await asyncio.Event().wait()
        finally: closed.set()
    with patch.object(DecisionProvider, '_request', side_effect=response) as request:
        pending = asyncio.create_task(preview(hass, batch_home, numeric_preview=numeric))
        await entered.wait()
        assert (await preview(hass, batch_home, numeric_preview=numeric))['reason'] == 'busy'
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


@pytest.fixture
async def percentage_home(hass, batch_home):
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.components.media_player import MediaPlayerEntityFeature
    _, token, _, _, light = batch_home
    token.permissions.domains.update({domain: PermissionNode(state='GREEN') for domain in ('media_player', 'cover')})
    hass.states.async_set(light, 'off', {'supported_color_modes': ['brightness']})
    entities = {'light_brightness': light}
    for category, domain, feature in (
        ('media_volume', 'media_player', MediaPlayerEntityFeature.VOLUME_SET),
        ('cover_position', 'cover', CoverEntityFeature.SET_POSITION),
    ):
        entry = er.async_get(hass).async_get_or_create(domain, 'test', category)
        hass.states.async_set(entry.entity_id, 'idle' if domain == 'media_player' else 'open',
                              {'supported_features': feature, 'private': 'private-state'})
        async_expose_entity(hass, 'conversation', entry.entity_id, True)
        entities[category] = entry.entity_id
    return batch_home, entities


def percentage_response(payload, category, entity_id, percentage):
    target = next(key for key, value in payload['state']['targets'].items() if value['entity_id'] == entity_id)
    body = wire_response(payload, category, target)
    body['answers']['percentage'] = {
        'type': 'choice', 'choice': percentage, 'confidence': 1,
        'probabilities': {key: float(key == percentage) for key in payload['questions']['percentage']['criteria']},
    }
    return body


async def percentage_preview(hass, home):
    data, token, *_ = home
    return await async_preview_batch(hass, data, token, 'Set a device to thirty-seven percent', 'en',
                                      {'reason': 'no_match', 'proposal': None}, numeric_preview=True)


@pytest.mark.parametrize('category,intent,field', [
    ('light_brightness', 'HassLightSet', 'brightness_pct'),
    ('media_volume', 'HassSetVolume', 'volume_pct'),
    ('cover_position', 'HassSetPosition', 'position_pct'),
])
@pytest.mark.parametrize('percent', [0, 1, 10, 37, 55, 99, 100])
async def test_joint_percentage_preview_uses_one_request_and_never_prepares_execution(
    hass, percentage_home, category, intent, field, percent,
):
    home, entities = percentage_home
    entity_id = entities[category]
    async def response(*args):
        payload = args[4]
        assert set(payload['questions']) == {'route', 'target', 'percentage'}
        assert len(payload['questions']['percentage']['criteria']) == 102
        assert len(payload['state']['targets']) == 3
        assert 'private-state' not in json.dumps(payload)
        for descriptor in payload['state']['targets'].values():
            expected = next(key for key, entity in entities.items() if descriptor['entity_id'] == entity)
            assert expected in descriptor['eligible_categories']
        return percentage_response(payload, category, entity_id, str(percent))
    with (patch.object(DecisionProvider, '_request', side_effect=response) as request,
          patch('custom_components.phoenix_mcp.decision_batch._capture_plan') as plan,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result = await percentage_preview(hass, home)
    request.assert_awaited_once(); plan.assert_not_called(); dispatch.assert_not_called()
    assert result['proposal'] == {'intent': intent, 'entity_ids': [entity_id], field: percent}
    assert result['executed'] is False and '_plan' not in result and not home[0].store.get_pending_approvals()


@pytest.mark.parametrize('damage,reason', [
    ('missing', 'malformed'), ('extra', 'malformed'), ('fractional', 'malformed'), ('outside', 'malformed'),
    ('bool', 'malformed'), ('incomplete', 'malformed'), ('nonfinite', 'malformed'),
    ('low', 'low_probability'), ('none', 'percentage_declined'), ('wrong_operation', 'category_mismatch'),
    ('unrequested_number', 'percentage_mismatch'), ('nonnumeric', 'target_selected'), ('fallback', 'provider_fallback'),
])
async def test_joint_percentage_requires_complete_consistent_high_probability_answers(hass, percentage_home, damage, reason):
    home, entities = percentage_home
    async def response(*args):
        category = {'wrong_operation': 'media_volume', 'unrequested_number': 'light_on',
                    'nonnumeric': 'light_on', 'fallback': 'provider'}.get(damage, 'light_brightness')
        percent = {'fractional': '37.5', 'outside': '101', 'bool': True,
                   'none': 'none', 'nonnumeric': 'none'}.get(damage, '37')
        body = percentage_response(args[4], category, entities['light_brightness'], percent)
        value = body['answers']['percentage']
        if damage == 'missing': del body['answers']['percentage']
        if damage == 'extra': body['answers']['execute'] = {}
        if damage == 'incomplete': del value['probabilities']['0']
        if damage == 'nonfinite': value['confidence'] = float('nan')
        if damage == 'low': value['probabilities'].update({'37': 0.8, 'none': 0.2})
        return body
    with patch.object(DecisionProvider, '_request', side_effect=response) as request:
        result = await percentage_preview(hass, home)
    request.assert_awaited_once()
    assert result['reason'] == reason and result['executed'] is False
    if damage != 'nonnumeric': assert 'proposal' not in result
    else: assert result['proposal'] == {'intent': 'HassTurnOn', 'entity_ids': [entities['light_brightness']]}


@pytest.mark.parametrize('change', ['scope', 'feature', 'unexposed', 'competing_entity', 'account', 'settings'])
async def test_joint_percentage_discards_changed_catalog_or_authority(hass, percentage_home, change):
    from homeassistant.components.cover import CoverEntityFeature
    home, entities = percentage_home
    data, token, store, account, _ = home
    async def response(*args):
        entity_id = entities['media_volume']
        if change == 'scope': token.permissions.domains['media_player'].state = 'YELLOW'
        if change == 'feature': hass.states.async_set(entity_id, 'idle', {'supported_features': 0})
        if change == 'unexposed': async_expose_entity(hass, 'conversation', entity_id, False)
        if change == 'competing_entity':
            entry = er.async_get(hass).async_get_or_create('cover', 'test', 'competitor')
            hass.states.async_set(entry.entity_id, 'open', {'supported_features': CoverEntityFeature.SET_POSITION})
            async_expose_entity(hass, 'conversation', entry.entity_id, True)
        if change == 'account': await store.set_model(account, 'changed')
        if change == 'settings': await data.store.async_patch_settings(kill_switch=True)
        return percentage_response(args[4], 'media_volume', entity_id, '37')
    with patch.object(DecisionProvider, '_request', side_effect=response):
        result = await percentage_preview(hass, home)
    assert result['reason'] in ('targets_changed', 'authority_changed')
    assert 'decision' not in result and 'proposal' not in result


@pytest.mark.parametrize('kind,protocol', [('typesafe', 'systemone'), ('systemone', 'systemone'), ('classifier', 'classifier')])
async def test_unqualified_endpoint_cannot_receive_extended_numeric_choices(kind, protocol):
    provider = DecisionProvider(agentcli.ProviderConfig(kind, 'm', 'http://local.test/v1'), protocol)
    with patch.object(provider, '_request') as request, pytest.raises(DecisionError, match='unsupported_category'):
        await provider.route_and_target(None, 'request', {'t0': {}}, 1000, numeric_preview=True)
    request.assert_not_called()


@pytest.mark.parametrize('change', [False, True])
async def test_extended_preview_keeps_all_competitors_above_common_limit(hass, batch_home, change):
    _, token, _, _, original = batch_home
    hass.states.async_set(original, 'off', {'supported_color_modes': ['brightness']})
    for index in range(DECISION_MAX_TARGETS):
        entry = er.async_get(hass).async_get_or_create('light', 'test', f'extra{index}')
        hass.states.async_set(entry.entity_id, 'off', {'supported_color_modes': ['brightness']})
    with pytest.raises(DecisionError, match='too_many_targets'):
        capture_batch_targets(hass, token)
    async def response(*args):
        payload = args[4]
        assert len(payload['state']['targets']) == DECISION_MAX_TARGETS + 1
        assert len(payload['questions']['target']['criteria']) == DECISION_MAX_TARGETS + 2
        assert all('light_brightness' in item['eligible_categories'] for item in payload['state']['targets'].values())
        body = percentage_response(payload, 'light_brightness', original, '37')
        if change:
            er.async_get(hass).async_update_entity(entry.entity_id, aliases={'Desk lamp'})
        return body
    with patch.object(DecisionProvider, '_request', side_effect=response) as request:
        result = await percentage_preview(hass, batch_home)
    request.assert_awaited_once()
    assert result['reason'] == ('targets_changed' if change else 'target_selected')
    if change: assert 'decision' not in result and 'proposal' not in result
    else: assert result['proposal']['brightness_pct'] == 37


async def test_extended_preview_preserves_metadata_byte_budget(hass, batch_home):
    registry = er.async_get(hass)
    for index in range(20):
        entry = registry.async_get_or_create('light', 'test', f'extra{index}')
        registry.async_update_entity(entry.entity_id, aliases={str(i) * 200 for i in range(8)})
        hass.states.async_set(entry.entity_id, 'off')
    with patch.object(DecisionProvider, '_request') as request:
        result = await percentage_preview(hass, batch_home)
    assert result['reason'] == 'target_metadata_too_large'
    request.assert_not_called()


async def test_compact_batch_fits_duplicate_heavy_catalog_without_dropping_candidates(hass, batch_home):
    from custom_components.phoenix_mcp.decision_targets import CatalogLimitError
    _, token, _, _, original = batch_home
    expected_entities = {original}
    registry = er.async_get(hass)
    for index in range(60):
        name = f'Lamp {index} ' + 'x' * 100
        entry = registry.async_get_or_create('light', 'test', f'compact_{index}', original_name=name)
        registry.async_update_entity(entry.entity_id, aliases={er.COMPUTED_NAME})
        hass.states.async_set(entry.entity_id, 'off', {'friendly_name': name, 'supported_color_modes': ['brightness']})
        expected_entities.add(entry.entity_id)
    with pytest.raises(CatalogLimitError) as caught:
        capture_targets(hass, token, 'state_query', max_targets=DECISION_PERCENTAGE_PREVIEW_MAX_TARGETS)
    assert caught.value.details['bound'] == 'metadata_bytes'
    targets, _ = capture_batch_targets(hass, token, numeric_preview=True)
    assert {item['entity_id'] for item in targets.values()} == expected_entities
    assert all('state_query' in item['eligible_categories'] for item in targets.values())


@pytest.mark.parametrize('failure,bound', [('name', 'text_length'), ('aliases', 'alias_count'), ('bytes', 'metadata_bytes')])
async def test_preview_limit_diagnostic_contains_only_scoped_counts(hass, batch_home, failure, bound):
    _, _, _, _, entity_id = batch_home
    registry = er.async_get(hass)
    if failure == 'name': hass.states.async_set(entity_id, 'off', {'friendly_name': 'x' * 257})
    if failure == 'aliases': registry.async_update_entity(entity_id, aliases={f'private alias {i}' for i in range(9)})
    if failure == 'bytes':
        for index in range(20):
            entry = registry.async_get_or_create('light', 'test', f'extra{index}')
            registry.async_update_entity(entry.entity_id, aliases={str(i) * 200 for i in range(8)})
            hass.states.async_set(entry.entity_id, 'off')
    with patch.object(DecisionProvider, '_request') as request:
        result = await percentage_preview(hass, batch_home)
    request.assert_not_called()
    assert result['reason'] == 'target_metadata_too_large'
    diagnostic = result['catalog_limit']
    assert set(diagnostic) == {'category', 'bound', 'observed', 'limit'}
    assert diagnostic['bound'] == bound and diagnostic['observed'] > diagnostic['limit']
    assert entity_id not in json.dumps(result) and 'private alias' not in json.dumps(result)
    assert result['executed'] is False and 'proposal' not in result
