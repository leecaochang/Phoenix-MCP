"""Routed outcome observation is bounded, scoped and never a retry mechanism."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import command_verification as verification
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.command_execution import _capture_plan
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.request_authority import AuthorityChangedError
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree


@pytest.mark.parametrize('intent,value,domain,state,attributes,expected', [
    ('HassTurnOn', None, 'light', 'on', {}, True),
    ('HassTurnOn', None, 'light', 'off', {}, False),
    ('HassTurnOff', None, 'light', 'off', {}, True),
    ('HassTurnOn', None, 'light', 'on', {'assumed_state': True}, False),
    ('HassLightSet', 37, 'light', 'on', {'brightness': 94}, True),
    ('HassLightSet', 37, 'light', 'on', {'brightness': 95}, True),
    ('HassLightSet', 37, 'light', 'on', {'brightness': 96}, False),
    ('HassLightSet', 37, 'light', 'off', {'brightness': 94}, False),
    ('HassLightSet', 0, 'light', 'off', {}, True),
    ('HassLightSet', 0, 'light', 'on', {'brightness': 0}, False),
    ('HassLightSet', 100, 'light', 'on', {'brightness': 255}, True),
    ('HassLightSet', 100, 'light', 'on', {'brightness': 256}, False),
    ('HassLightSet', 37, 'light', 'unavailable', {'brightness': 94}, False),
    ('HassLightSet', 37, 'light', 'on', {'brightness': True}, False),
    ('HassLightSet', 37, 'light', 'on', {'brightness': float('nan')}, False),
    ('HassLightSet', 37, 'light', 'on', {'brightness': 10 ** 400}, False),
    ('HassLightSet', 37, 'light', 'on', {'brightness': '94'}, False),
    ('HassSetVolume', 37, 'media_player', 'playing', {'volume_level': .37}, True),
    ('HassSetVolume', 37, 'media_player', 'paused', {'volume_level': .374}, True),
    ('HassSetVolume', 37, 'media_player', 'playing', {'volume_level': .38}, False),
    ('HassSetVolume', 37, 'media_player', 'off', {'volume_level': .37}, False),
    ('HassSetVolume', 0, 'media_player', 'idle', {'volume_level': 0}, True),
    ('HassSetVolume', 100, 'media_player', 'playing', {'volume_level': 1}, True),
    ('HassSetVolume', 37, 'media_player', 'playing', {}, False),
    ('HassSetPosition', 37, 'cover', 'open', {'current_position': 37}, True),
    ('HassSetPosition', 37, 'cover', 'opening', {'current_position': 37}, False),
    ('HassSetPosition', 37, 'cover', 'closing', {'current_position': 37}, False),
    ('HassSetPosition', 37, 'cover', 'closed', {'current_position': 37}, False),
    ('HassSetPosition', 37, 'cover', 'open', {'current_position': 38}, False),
    ('HassSetPosition', 0, 'cover', 'closed', {'current_position': 0}, True),
    ('HassSetPosition', 100, 'cover', 'open', {'current_position': 100}, True),
])
def test_operation_specific_state_comparison(intent, value, domain, state, attributes, expected):
    proposal = {'intent': intent}
    operation = verification.numeric_operation(intent)
    if operation:
        proposal[operation.proposal_field] = value
    assert verification.state_matches(proposal, State(domain + '.test', state, attributes)) is expected


@pytest.fixture
async def observation_home(hass, access_key_store, monkeypatch):
    access_key, _ = await access_key_store.async_create_access_key('observe', 'test', rate_limit_requests=0)
    access_key.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    data = PhoenixData(store=access_key_store, audit=AuditLog(), rate_limiter=RateLimiter())
    hass.data[DOMAIN] = data
    await access_key_store.async_patch_settings(agentcli_routing_mode='local')
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    entry = er.async_get(hass).async_get_or_create('light', 'test', 'observation', original_name='Observed lamp')
    hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Observed lamp'})
    proposal = {'intent': 'HassTurnOn', 'entity_ids': [entry.entity_id]}
    plan = _capture_plan(hass, data, access_key, proposal, ingress='chat')
    assert plan is not None
    monkeypatch.setattr(verification, 'COMMAND_VERIFICATION_WAIT_SECONDS', .04)
    monkeypatch.setattr(verification, 'COMMAND_VERIFICATION_POLL_SECONDS', .005)
    return data, access_key, plan, entry.entity_id


async def test_delayed_state_report_is_observed_without_service_calls(hass, observation_home):
    data, access_key, plan, eid = observation_home
    loop = asyncio.get_running_loop()
    handle = loop.call_later(.01, lambda: hass.states.async_set(eid, 'on', dict(hass.states.get(eid).attributes)))
    try:
        with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as service:
            assert await verification.async_verify_command_state(hass, data, access_key, plan) == 'matched'
        service.assert_not_called()
    finally:
        handle.cancel()


async def test_deadline_does_not_retry_or_create_recovery(hass, observation_home):
    data, access_key, plan, _ = observation_home
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as service:
        async with asyncio.timeout(.5):
            assert await verification.async_verify_command_state(hass, data, access_key, plan) == 'unconfirmed'
    service.assert_not_called()
    assert data.agent_recovery.records(access_key.id) == []


@pytest.mark.parametrize('change', ['scope', 'exposure', 'identity', 'removed', 'version'])
async def test_wait_rechecks_read_scope_and_identity_before_disclosing_match(hass, observation_home, change):
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    data, access_key, plan, eid = observation_home
    def changed():
        hass.states.async_set(eid, 'on', dict(hass.states.get(eid).attributes))
        if change == 'scope': access_key.permissions = PermissionTree()
        elif change == 'exposure': async_expose_entity(hass, 'conversation', eid, False)
        elif change == 'identity': er.async_get(hass).async_update_entity(eid, name='Replacement')
        elif change == 'removed': hass.states.async_remove(eid)
        else: access_key.settings_version += 1
    handle = asyncio.get_running_loop().call_later(.01, changed)
    try:
        assert await verification.async_verify_command_state(hass, data, access_key, plan) == 'unconfirmed'
    finally:
        handle.cancel()


@pytest.mark.parametrize('change', ['revoked', 'runtime', 'shutdown', 'kill_switch'])
async def test_authority_loss_during_observation_is_not_hidden(hass, observation_home, change):
    data, access_key, plan, _ = observation_home
    def changed():
        if change == 'revoked': access_key.revoked = True
        elif change == 'runtime': hass.data[DOMAIN] = object()
        elif change == 'shutdown': data.shutting_down = True
        else: data.store.get_settings().kill_switch = True
    handle = asyncio.get_running_loop().call_later(.01, changed)
    try:
        with pytest.raises(AuthorityChangedError):
            await verification.async_verify_command_state(hass, data, access_key, plan)
    finally:
        handle.cancel()


async def test_cancellation_leaves_no_background_observer(hass, observation_home):
    data, access_key, plan, _ = observation_home
    task = asyncio.create_task(verification.async_verify_command_state(hass, data, access_key, plan))
    await asyncio.sleep(.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()


async def test_all_targets_must_match_in_the_same_observation(hass, observation_home):
    data, access_key, _, eid = observation_home
    other = er.async_get(hass).async_get_or_create('light', 'test', 'other', original_name='Other lamp')
    hass.states.async_set(other.entity_id, 'off', {'friendly_name': 'Other lamp'})
    hass.states.async_set(eid, 'on', dict(hass.states.get(eid).attributes))
    plan = _capture_plan(hass, data, access_key, {'intent': 'HassTurnOn', 'entity_ids': [eid, other.entity_id]}, ingress='chat')
    assert await verification.async_verify_command_state(hass, data, access_key, plan) == 'unconfirmed'
    hass.states.async_set(other.entity_id, 'on', dict(hass.states.get(other.entity_id).attributes))
    assert await verification.async_verify_command_state(hass, data, access_key, plan) == 'matched'


@pytest.mark.parametrize('intent,domain,value,attribute,state,matched', [
    ('PhoenixLightTemperature', 'light', 3500, 'color_temp_kelvin', 'on', True),
    ('PhoenixLightTemperature', 'light', 3500, 'color_temp_kelvin', 'off', False),
    ('HassFanSetSpeed', 'fan', 37, 'percentage', 'on', True),
    ('HassFanSetSpeed', 'fan', 37, 'percentage', 'off', False),
    ('PhoenixCoverTilt', 'cover', 37, 'current_tilt_position', 'opening', False),
    ('PhoenixCoverTilt', 'cover', 37, 'current_tilt_position', 'open', True),
    ('PhoenixNumberValue', 'number', -2.5, None, '-2.5', True),
])
def test_native_numeric_verification_checks_requested_property_and_power(intent, domain, value, attribute, state, matched):
    from custom_components.phoenix_mcp.command_operations import numeric_operation
    operation = numeric_operation(intent)
    proposal = {'intent': intent, 'entity_ids': [domain + '.test'], operation.proposal_field: value}
    assert verification.state_matches(proposal, State(domain + '.test', state, {attribute: value} if attribute else {})) is matched
