"""Each routing authority check refuses on its own, not only behind another check that masks it."""
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.routing import command_execution, command_routing
from custom_components.phoenix_mcp.routing.command_plan import _capture_plan
from custom_components.phoenix_mcp.routing.command_operations import service_arguments
from custom_components.phoenix_mcp.routing.command_routing import RoutingAuthority
from custom_components.phoenix_mcp.routing.command_target import _digest
from custom_components.phoenix_mcp.routing.plan_currency import (
    _local_plan, bind_service_plan, plan_is_current, service_plan_is_current,
)
from custom_components.phoenix_mcp.routing.routing_tables import COMMAND_ROUTING_PLAN_KEY


@pytest.fixture
async def routed_home(hass: HomeAssistant, access_key_store, immediate_command_observation):
    access_key, _ = await access_key_store.async_create_access_key('pins', 'test', rate_limit_requests=0)
    access_key.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    access_key.cap_physical_control = 'allow'
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    await access_key_store.async_patch_settings(voice_agent_enabled=True, voice_agent_access_key_id=access_key.id,
        voice_agent_provider_id='p', voice_agent_model='m', voice_agent_routing_mode='local',
        agentcli_routing_mode='local', mesa_mode='off')
    assert await async_setup_component(hass, 'homeassistant', {})
    ids = []
    for name in ('desk', 'shelf'):
        entry = er.async_get(hass).async_get_or_create('light', 'test', name, original_name=f'{name} lamp')
        hass.states.async_set(entry.entity_id, 'off', {'friendly_name': f'{name} lamp', 'supported_color_modes': ['onoff']})
        async_expose_entity(hass, 'conversation', entry.entity_id, True)
        ids.append(entry.entity_id)
    return data, access_key, ids


def turn_on(entity_id: str) -> dict:
    return {'intent': 'HassTurnOn', 'entity_ids': [entity_id]}


def planned_args(plan: dict) -> dict:
    return {**service_arguments(plan['proposal']), COMMAND_ROUTING_PLAN_KEY: plan}


async def test_caller_supplied_plan_is_refused_even_when_current(hass, routed_home):
    """M07: only a server-owned turn attaches a plan; an MCP caller sending one is refused."""
    data, access_key, (desk, _) = routed_home
    plan = _capture_plan(hass, data, access_key, turn_on(desk))
    supplied = planned_args(plan)
    assert service_plan_is_current(supplied, hass, data, access_key)
    assert bind_service_plan(supplied, hass, data, access_key) is None
    token = _local_plan.set(plan)
    try:
        args = {key: value for key, value in supplied.items() if key != COMMAND_ROUTING_PLAN_KEY}
        assert bind_service_plan(args, hass, data, access_key) == supplied
    finally:
        _local_plan.reset(token)


async def test_plan_for_another_key_is_not_current(hass, routed_home):
    """M04: a key that differs only by id cannot use the plan."""
    data, access_key, (desk, _) = routed_home
    plan = _capture_plan(hass, data, access_key, turn_on(desk), ingress='chat')
    assert plan_is_current(plan, hass, data, access_key)
    assert not plan_is_current(plan, hass, data, replace(access_key, id='other-key'))


async def test_voice_plan_needs_the_key_still_bound_to_voice(hass, routed_home):
    """M06: rebinding Voice to another key ends a voice plan, even with a matching settings digest."""
    data, access_key, (desk, _) = routed_home
    plan = _capture_plan(hass, data, access_key, turn_on(desk))
    assert plan_is_current(plan, hass, data, access_key)
    await data.store.async_patch_settings(voice_agent_access_key_id='other-key')
    plan['settings'] = _digest(data.store.get_settings().to_dict())
    assert not plan_is_current(plan, hass, data, access_key)


async def test_plan_with_extra_fingerprints_is_not_current(hass, routed_home):
    """M18: the fingerprinted set must equal the proposal's entities."""
    data, access_key, (desk, shelf) = routed_home
    plan = _capture_plan(hass, data, access_key, turn_on(desk))
    plan['fingerprints'][shelf] = _capture_plan(hass, data, access_key, turn_on(shelf))['fingerprints'][shelf]
    assert not plan_is_current(plan, hass, data, access_key)


@pytest.mark.parametrize('selector', ['area_id', 'device_id'])
async def test_planned_call_cannot_widen_to_an_area_or_device(hass, routed_home, selector):
    """M23: a planned call names its entities only."""
    data, access_key, (desk, _) = routed_home
    args = planned_args(_capture_plan(hass, data, access_key, turn_on(desk)))
    assert service_plan_is_current(args, hass, data, access_key, [desk])
    assert not service_plan_is_current({**args, selector: 'anything'}, hass, data, access_key, [desk])


async def test_planned_call_cannot_reach_more_entities_than_proposed(hass, routed_home):
    """M24: the permitted entities equal the proposal's."""
    data, access_key, (desk, shelf) = routed_home
    args = planned_args(_capture_plan(hass, data, access_key, turn_on(desk)))
    assert not service_plan_is_current(args, hass, data, access_key, [desk, shelf])
    assert not service_plan_is_current(args, hass, data, access_key, [])


async def test_stale_plan_is_refused_before_any_dispatch(hass, routed_home):
    """M14: the recheck before dispatch stops a stale plan without reaching the dispatcher."""
    data, access_key, (desk, _) = routed_home
    plan = _capture_plan(hass, data, access_key, turn_on(desk))
    plan['fingerprints'][desk] = 'stale'
    with patch.object(command_execution, 'dispatch_agent_tool', AsyncMock()) as dispatch:
        reply = await command_execution._dispatch_command(hass, data, access_key, plan,
                                                          RoutingAuthority.capture(data, access_key), 'http://test', False)
    assert reply.outcome == 'unauthorized'
    dispatch.assert_not_called()


async def test_kill_switch_stops_recognition_before_it_starts(hass, routed_home):
    """M29: the turn-start authority check runs before any recognition work."""
    data, access_key, _ = routed_home
    await data.store.async_patch_settings(kill_switch=True)
    with patch.object(command_routing, '_recognize_command', AsyncMock()) as recognize:
        result = await command_routing.async_evaluate_command(hass, data, access_key, 'turn on the desk lamp')
    assert result['reason'] == 'authority_changed'
    recognize.assert_not_called()
