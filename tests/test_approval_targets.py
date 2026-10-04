"""Approval target drift checks using real Home Assistant registries and services."""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar, entity_registry as er

from custom_components.phoenix_mcp.approvals import get_approval
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree


@pytest.fixture
async def target_runtime(hass, access_key_store):
    access_key, _ = await access_key_store.async_create_access_key('target-review', 'admin')
    access_key.confirm_inline_wait_seconds = 0
    access_key.cap_physical_control = 'confirm'
    access_key.permissions = PermissionTree(domains={'cover': PermissionNode(state='GREEN')})
    access_key_store.get_settings().mesa_mode = 'off'
    data = PhoenixData(store=access_key_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    area = ar.async_get(hass).async_create('Review room')
    registry = er.async_get(hass)
    first = registry.async_get_or_create('cover', 'test', 'review-first', suggested_object_id='review_first')
    second = registry.async_get_or_create('cover', 'test', 'review-second', suggested_object_id='review_second')
    first = registry.async_update_entity(first.entity_id, area_id=area.id)
    for entry in (first, second):
        hass.states.async_set(entry.entity_id, 'closed', {'friendly_name': entry.entity_id, 'supported_features': 15})
    calls = []

    @callback
    def capture(call):
        calls.append(call.data)

    for service in ('open_cover', 'stop_cover', 'set_cover_position'):
        hass.services.async_register('cover', service, capture)
    return access_key, data, area, first, second, calls


async def queue(hass, runtime, tool, args):
    access_key, data, *_ = runtime
    result, outcome, _ = await _call_tool(tool, args, access_key, hass, data, request_id='target-review')
    assert outcome == 'pending_approval', result
    return get_approval(data.store, data.store.get_pending_approvals()[-1]['id'])


@pytest.mark.parametrize('tool', ['call_service', 'HassTurnOn', 'HassSetPosition', 'HassStopMoving'])
@pytest.mark.parametrize('change', ['added', 'removed', 'permission'])
async def test_capability_approval_refuses_changed_area_targets(hass, target_runtime, tool, change):
    access_key, data, area, first, second, calls = target_runtime
    args = ({'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'area', 'ids': [area.id]}]} if tool == 'call_service'
            else {'area': area.name, 'domain': ['cover'], **({'position': 30} if tool == 'HassSetPosition' else {})})
    approval = await queue(hass, target_runtime, tool, args)
    registry = er.async_get(hass)
    if change == 'added':
        registry.async_update_entity(second.entity_id, area_id=area.id)
    elif change == 'removed':
        registry.async_update_entity(first.entity_id, area_id=None)
        registry.async_update_entity(second.entity_id, area_id=area.id)
    else:
        registry.async_update_entity(second.entity_id, area_id=area.id)
        access_key.permissions.entities[first.entity_id] = PermissionNode(state='RED')
    result, outcome, _ = await async_execute_approved_tool(tool, approval.args, access_key, hass, data)
    assert outcome == 'denied', result
    assert calls == []


@pytest.mark.parametrize('tool', ['call_service', 'HassTurnOn', 'HassSetPosition', 'HassStopMoving'])
async def test_unchanged_capability_approval_reaches_only_reviewed_entity(hass, target_runtime, tool):
    access_key, data, area, first, _second, calls = target_runtime
    args = ({'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'area', 'ids': [area.id]}]} if tool == 'call_service'
            else {'area': area.name, 'domain': ['cover'], **({'position': 30} if tool == 'HassSetPosition' else {})})
    approval = await queue(hass, target_runtime, tool, args)
    result, outcome, _ = await async_execute_approved_tool(tool, approval.args, access_key, hass, data)
    assert outcome == 'allowed', result
    assert len(calls) == 1
    assert calls[0]['entity_id'] == [first.entity_id]


@pytest.mark.parametrize('selector', ['device', 'floor', 'domain'])
async def test_added_structural_member_requires_new_review(hass, target_runtime, selector):
    from homeassistant.helpers import device_registry as dr, floor_registry as fr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    access_key, data, area, first, second, calls = target_runtime
    registry = er.async_get(hass)
    if selector == 'device':
        config_entry = MockConfigEntry(domain='test')
        config_entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={('test', 'review')})
        registry.async_update_entity(first.entity_id, device_id=device.id)
        tool = 'call_service'
        args = {'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'device', 'ids': [device.id]}]}
    elif selector == 'floor':
        floor = fr.async_get(hass).async_create('Review floor')
        ar.async_get(hass).async_update(area.id, floor_id=floor.floor_id)
        tool, args = 'HassTurnOn', {'floor': floor.name, 'domain': ['cover']}
    else:
        tool, args = 'HassTurnOn', {'domain': ['cover']}
    approval = await queue(hass, target_runtime, tool, args)
    if selector == 'device':
        registry.async_update_entity(second.entity_id, device_id=device.id)
    elif selector == 'floor':
        registry.async_update_entity(second.entity_id, area_id=area.id)
    else:
        third = registry.async_get_or_create('cover', 'test', 'review-third', suggested_object_id='review_third')
        hass.states.async_set(third.entity_id, 'closed')
    result, outcome, _ = await async_execute_approved_tool(tool, approval.args, access_key, hass, data)
    assert outcome == 'denied', result
    assert calls == []


async def test_group_membership_and_member_identity_are_bound(hass, target_runtime):
    access_key, data, _area, first, second, calls = target_runtime
    registry = er.async_get(hass)
    group = registry.async_get_or_create('cover', 'group', 'review-group', suggested_object_id='review_group')
    hass.states.async_set(group.entity_id, 'closed', {'entity_id': [first.entity_id]})
    args = {'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'entity', 'ids': [group.entity_id]}]}
    approval = await queue(hass, target_runtime, 'call_service', args)
    result, outcome, _ = await async_execute_approved_tool('call_service', approval.args, access_key, hass, data)
    assert outcome == 'allowed', result
    assert calls.pop()['entity_id'] == [group.entity_id]
    hass.states.async_set(group.entity_id, 'closed', {'entity_id': [first.entity_id, second.entity_id]})
    result, outcome, _ = await async_execute_approved_tool('call_service', approval.args, access_key, hass, data)
    assert outcome == 'denied', result
    assert calls == []
    hass.states.async_set(group.entity_id, 'closed', {'entity_id': [first.entity_id]})
    registry.async_remove(first.entity_id)
    hass.states.async_remove(first.entity_id)
    replacement = registry.async_get_or_create('cover', 'test', 'replacement', suggested_object_id='review_first')
    assert replacement.entity_id == first.entity_id and replacement.id != first.id
    result, outcome, _ = await async_execute_approved_tool('call_service', approval.args, access_key, hass, data)
    assert outcome == 'denied', result
    assert calls == []


async def test_mesa_approval_freezes_primary_selection_and_rechecks_scope(hass, target_runtime):
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile

    access_key, data, area, first, second, calls = target_runtime
    access_key.cap_physical_control = 'allow'
    data.store.get_settings().mesa_mode = 'enforced'
    data.mesa = await async_setup_mesa(hass, 'enforced')
    data.mesa.store.set(first.entity_id, SemanticProfile.from_dict(
        first.entity_id, {'semantic_profile': {'operational_boundaries': {'control_mode': 'confirm'}}},
        default_origin=MetadataOrigin.USER,
    ))
    args = {'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'area', 'ids': [area.id]}]}
    approval = await queue(hass, target_runtime, 'call_service', args)
    assert approval.diff['preview']['target_semantics'] == 'exact'
    assert approval.args['entity_id'] == [first.entity_id]
    er.async_get(hass).async_update_entity(second.entity_id, area_id=area.id)
    result, outcome, _ = await async_execute_approved_tool(approval.tool_name, approval.args, access_key, hass, data)
    assert outcome == 'allowed', result
    assert calls.pop()['entity_id'] == [first.entity_id]
    access_key.permissions.entities[first.entity_id] = PermissionNode(state='RED')
    result, outcome, _ = await async_execute_approved_tool(approval.tool_name, approval.args, access_key, hass, data)
    assert outcome == 'denied', result
    assert calls == []


@pytest.mark.parametrize('change', ['scope', 'mesa'])
async def test_exact_approval_never_dispatches_a_remaining_subset(hass, target_runtime, change):
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile

    access_key, data, area, first, second, calls = target_runtime
    er.async_get(hass).async_update_entity(second.entity_id, area_id=area.id)
    args = {'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'area', 'ids': [area.id]}]}
    approval = await queue(hass, target_runtime, 'call_service', args)
    if change == 'scope':
        access_key.permissions.entities[first.entity_id] = PermissionNode(state='RED')
    else:
        data.store.get_settings().mesa_mode = 'enforced'
        data.mesa = await async_setup_mesa(hass, 'enforced')
        data.mesa.store.set(first.entity_id, SemanticProfile.from_dict(
            first.entity_id, {'semantic_profile': {'operational_boundaries': {'control_mode': 'prohibited'}}},
            default_origin=MetadataOrigin.USER,
        ))
    result, outcome, _ = await async_execute_approved_tool('call_service', approval.args, access_key, hass, data)
    assert outcome == 'denied', result
    assert calls == []


async def test_private_binding_cannot_be_supplied_and_legacy_approval_refuses(hass, target_runtime):
    from custom_components.phoenix_mcp.const import APPROVAL_TARGETS_KEY

    access_key, data, area, first, _second, calls = target_runtime
    args = {'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'area', 'ids': [area.id]}]}
    result, outcome, _ = await _call_tool('call_service', {**args, APPROVAL_TARGETS_KEY: {}}, access_key, hass, data)
    assert outcome == 'invalid_request', result
    assert not data.store.get_pending_approvals()
    result, outcome, _ = await async_execute_approved_tool('call_service', {
        'domain': 'cover', 'service': 'open_cover', 'entity_id': [first.entity_id],
    }, access_key, hass, data)
    assert outcome == 'denied', result
    assert 'new request' in result['content'][0]['text']
    assert calls == []


async def test_identical_concurrent_requests_keep_independent_bound_records(hass, target_runtime):
    import asyncio
    from custom_components.phoenix_mcp.approvals import PendingApproval

    access_key, data, area, first, _second, calls = target_runtime
    args = {'area': area.name, 'domain': ['cover']}
    await asyncio.gather(*(_call_tool('HassTurnOn', dict(args), access_key, hass, data, request_id=str(i)) for i in range(2)))
    records = [PendingApproval.from_dict(row) for row in data.store.get_pending_approvals()]
    assert len(records) == 2 and records[0].id != records[1].id
    for approval in records:
        assert approval.diff['preview']['resolved_entity_ids'] == [first.entity_id]
        result, outcome, _ = await async_execute_approved_tool(approval.tool_name, approval.args, access_key, hass, data)
        assert outcome == 'allowed', result
    assert len(calls) == 2 and all(call['entity_id'] == [first.entity_id] for call in calls)


async def test_drift_during_service_description_wait_refuses_dispatch(hass, target_runtime, monkeypatch):
    import asyncio
    from custom_components.phoenix_mcp import mcp_view

    access_key, data, area, _first, second, calls = target_runtime
    args = {'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'area', 'ids': [area.id]}]}
    approval = await queue(hass, target_runtime, 'call_service', args)
    started, release = asyncio.Event(), asyncio.Event()

    async def suspended(*_args, **_kwargs):
        started.set()
        await release.wait()
        return ()

    monkeypatch.setattr(mcp_view, 'unsupported_secondary_targets', suspended)
    task = asyncio.create_task(async_execute_approved_tool('call_service', approval.args, access_key, hass, data))
    await started.wait()
    er.async_get(hass).async_update_entity(second.entity_id, area_id=area.id)
    release.set()
    result, outcome, _ = await task
    assert outcome == 'denied', result
    assert calls == []


async def test_native_subgroups_stop_on_drift_and_preserve_prior_effect(hass, target_runtime):
    import json

    access_key, data, area, first, _second, calls = target_runtime
    access_key.permissions.domains['valve'] = PermissionNode(state='GREEN')
    registry = er.async_get(hass)
    valve = registry.async_get_or_create('valve', 'test', 'review-valve', suggested_object_id='review_valve')
    valve = registry.async_update_entity(valve.entity_id, area_id=area.id)
    hass.states.async_set(valve.entity_id, 'closed')

    @callback
    def moved(call):
        calls.append(call.data)
        registry.async_update_entity(valve.entity_id, area_id=None)

    @callback
    def valve_call(call):
        calls.append(call.data)

    hass.services.async_register('cover', 'open_cover', moved)
    hass.services.async_register('valve', 'open_valve', valve_call)
    approval = await queue(hass, target_runtime, 'HassTurnOn', {'area': area.name, 'domain': ['cover', 'valve']})
    result, outcome, _ = await async_execute_approved_tool(approval.tool_name, approval.args, access_key, hass, data)
    assert outcome == 'denied' and result['isError'], result
    payload = json.loads(result['content'][0]['text'])
    assert payload['partial'] is True
    assert any(entry['id'] == first.entity_id for entry in payload['data']['success'])
    assert len(calls) == 1 and calls[0]['entity_id'] == [first.entity_id]


@pytest.mark.parametrize('bad_members', [['cover.review_group'], ['cover.missing'], [True], 'cover.review_first'])
async def test_unverifiable_group_binding_never_executes(hass, target_runtime, bad_members):
    from custom_components.phoenix_mcp.approval_targets import bind_targets, targets_current

    access_key, data, _area, _first, _second, calls = target_runtime
    group = er.async_get(hass).async_get_or_create('cover', 'group', 'review-group', suggested_object_id='review_group')
    hass.states.async_set(group.entity_id, 'closed', {'entity_id': bad_members})
    args = bind_targets({'domain': 'cover', 'service': 'open_cover', 'entity_id': [group.entity_id]}, [group.entity_id], hass)
    assert not targets_current(args, [group.entity_id], hass)
    result, outcome, _ = await async_execute_approved_tool('call_service', args, access_key, hass, data)
    assert outcome == 'denied', result
    assert calls == []


async def test_binding_bounds_and_private_projection(hass, target_runtime):
    import json
    from custom_components.phoenix_mcp.approval_targets import bind_targets, target_binding, targets_current
    from custom_components.phoenix_mcp.const import APPROVAL_TARGET_LIMIT, APPROVAL_TARGETS_KEY

    _access_key, _data, _area, first, _second, _calls = target_runtime
    assert target_binding([f'cover.member_{i}' for i in range(APPROVAL_TARGET_LIMIT + 1)], hass)['fingerprint'] is None
    args = bind_targets({}, [first.entity_id], hass)
    assert targets_current(args, [first.entity_id], hass)
    args[APPROVAL_TARGETS_KEY]['version'] = True
    assert not targets_current(args, [first.entity_id], hass)
    approval = await queue(hass, target_runtime, 'HassTurnOn', {'domain': ['cover']})
    public = approval.to_dict()
    assert public['args'][APPROVAL_TARGETS_KEY] == '<redacted>'
    assert 'fingerprint' not in json.dumps(public['diff'])


async def test_persisted_authoring_review_declares_future_native_selectors(hass, target_runtime):
    from custom_components.phoenix_mcp.tools.authoring import _build_diff_create_automation, _build_diff_create_script

    access_key, _data, area, _first, _second, _calls = target_runtime
    config = {'alias': 'Future room action', 'actions': [{'action': 'cover.open_cover', 'target': {'area_id': area.id}}]}
    for builder in (_build_diff_create_automation, _build_diff_create_script):
        diff = builder({'config': config, 'script_id': 'future_room'}, access_key, hass)
        assert diff['preview']['target_semantics'] == 'future_native_execution'
        assert diff['preview']['target_membership'] == 'may_change'
        assert diff['preview']['runtime_authority'] == 'Home Assistant'


async def test_position_approval_retains_unsupported_selected_domain_without_actuating_it(hass, target_runtime):
    access_key, data, area, first, _second, calls = target_runtime
    light = er.async_get(hass).async_get_or_create('light', 'test', 'review-light', suggested_object_id='review_light')
    er.async_get(hass).async_update_entity(light.entity_id, area_id=area.id)
    hass.states.async_set(light.entity_id, 'on')
    access_key.permissions.domains['light'] = PermissionNode(state='GREEN')
    approval = await queue(hass, target_runtime, 'HassSetPosition', {
        'area': area.name, 'domain': ['cover', 'light'], 'position': 30,
    })
    result, outcome, _ = await async_execute_approved_tool('HassSetPosition', approval.args, access_key, hass, data)
    assert outcome == 'allowed', result
    assert len(calls) == 1 and calls[0]['entity_id'] == [first.entity_id]
