"""Interleaving regressions for admitted requests and resource boundaries."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow
from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree, TokenStore
from tests.test_mcp_sse import _make_request, _view


async def _environment(hass):
    store = TokenStore(hass, AsyncMock())
    token, raw = await store.async_create_token('audit-boundary', 'admin', rate_limit_requests=0)
    token.pass_through = True
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    return data, token, raw


@pytest.mark.parametrize('change,status', [('revoke', 401), ('rotate', 401), ('expire', 401), ('kill', 503), ('unload', 503)])
async def test_authority_rechecked_after_body_wait(hass, change, status):
    data, token, raw = await _environment(hass)
    req = _make_request({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, 'application/json', raw)
    read = req.content.read
    async def changed_read(*args):
        if change == 'revoke':
            await data.store.async_archive_token(token.id, revoked=True)
        elif change == 'rotate':
            await data.store.async_rotate_token(token.id)
        elif change == 'expire':
            token.expires_at = utcnow() - timedelta(seconds=1)
        elif change == 'kill':
            data.store.get_settings().kill_switch = True
        else:
            data.shutting_down = True
        return await read(*args)
    req.content.read = changed_read
    response = await _view(hass).post(req)
    assert response.status == status
    assert 'tools' not in json.loads(response.text)


async def test_revocation_between_batch_items_blocks_next_dispatch(hass):
    data, token, raw = await _environment(hass)
    body = [{'jsonrpc': '2.0', 'id': i, 'method': 'ping'} for i in (1, 2)]
    original = mcp_view._dispatch_mcp_authorized
    calls = []
    async def dispatch(*args, **kwargs):
        calls.append(args[1])
        result = await original(*args, **kwargs)
        await data.store.async_archive_token(token.id, revoked=True)
        return result
    with patch.object(mcp_view, '_dispatch_mcp_authorized', dispatch):
        response = await _view(hass).post(_make_request(body, 'application/json', raw))
    assert calls == [1]
    assert all('error' in row for row in json.loads(response.text))


@pytest.mark.parametrize('change', ['revoke', 'policy', 'kill'])
async def test_watch_wait_does_not_disclose_after_authority_change(hass, change):
    data, token, raw = await _environment(hass)
    hass.states.async_set('sensor.audit', 'old')
    started = asyncio.Event()
    callbacks = []
    def subscribe(_hass, _ids, callback):
        callbacks.append(callback)
        started.set()
        return lambda: None
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'watch_entity', 'arguments': {'entity_id': 'sensor.audit', 'timeout': 1},
    }}
    with patch.object(mcp_view, 'async_track_state_change_event', subscribe):
        task = asyncio.create_task(_view(hass).post(_make_request(body, 'application/json', raw)))
        await asyncio.wait_for(started.wait(), 2)
        if change == 'revoke':
            await data.store.async_archive_token(token.id, revoked=True)
        elif change == 'policy':
            token.settings_version += 1
            token.pass_through = False
        else:
            data.store.get_settings().kill_switch = True
        callbacks[0](MagicMock(data={'new_state': MagicMock(state='never-disclose')}))
        response = await task
    assert 'never-disclose' not in response.text
    assert 'error' in json.loads(response.text)


@pytest.mark.parametrize('number', ['1e999', '-1e999', 'NaN', 'Infinity'])
async def test_nonfinite_wire_never_dispatches(hass, number):
    _, _, raw = await _environment(hass)
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'call_service', 'arguments': {'service': {'domain': 'climate', 'name': 'set_temperature'},
        'service_data': {'temperature': 7}}}}
    req = _make_request(body, 'application/json', raw)
    payload = json.dumps(body).replace('"temperature": 7', '"temperature": '+number).encode()
    req.content.read = AsyncMock(return_value=payload)
    req.content.at_eof = lambda: True
    with patch.object(mcp_view, '_call_tool', AsyncMock()) as dispatch:
        response = await _view(hass).post(req)
    assert json.loads(response.text)['error']['code'] == -32700
    dispatch.assert_not_called()


async def test_nonfinite_inprocess_never_dispatches(hass):
    data, token, _ = await _environment(hass)
    result, outcome, _ = await mcp_view._call_tool('call_service', {'service_data': {'nested': [float('inf')]}}, token, hass, data)
    assert outcome == 'invalid_request'
    assert result['isError']


@pytest.mark.parametrize('target', ['ghost', 'denied', 'valid'])
async def test_physical_target_validated_before_approval(hass, target):
    data, token, _ = await _environment(hass)
    token.pass_through = False
    token.cap_physical_control = 'confirm'
    token.confirm_inline_wait_seconds = 0
    token.permissions = PermissionTree(domains={'cover': PermissionNode(state='GREEN')})
    if target != 'ghost':
        er.async_get(hass).async_get_or_create('cover', 'test', 'audit', suggested_object_id='audit')
        hass.states.async_set('cover.audit', 'closed')
    if target == 'denied':
        token.permissions = PermissionTree(domains={'cover': PermissionNode(state='RED')})
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        result, outcome, _ = await mcp_view._tool_call_service(
            {'service': {'domain': 'cover', 'name': 'open_cover'}, 'targets': [{'kind': 'entity', 'ids': ['cover.audit']}]},
            token, hass, data,
        )
    if target == 'valid':
        assert outcome == 'pending_approval', result
        assert len(data.store.get_pending_approvals()) == 1
    else:
        assert outcome == 'denied'
        assert result['isError']
        assert data.store.get_pending_approvals() == []


@pytest.mark.parametrize('change', ['revoke', 'policy', 'kill'])
async def test_service_preparation_wait_rechecks_before_side_effect(hass, change):
    data, token, raw = await _environment(hass)
    hass.states.async_set('light.audit', 'off')
    sink = AsyncMock()
    hass.services.async_register('light', 'turn_on', sink)
    prepares = 0
    async def prepare(*args):
        nonlocal prepares
        prepares += 1
        if prepares == 2:
            if change == 'revoke':
                await data.store.async_archive_token(token.id, revoked=True)
            elif change == 'policy':
                token.settings_version += 1
                token.pass_through = False
            else:
                data.store.get_settings().kill_switch = True
        return []
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'call_service', 'arguments': {'service': {'domain': 'light', 'name': 'turn_on'},
        'targets': [{'kind': 'entity', 'ids': ['light.audit']} ]}}}
    with patch.object(mcp_view, 'unsupported_secondary_targets', prepare):
        response = await _view(hass).post(_make_request(body, 'application/json', raw))
    assert prepares == 2
    assert 'error' in json.loads(response.text)
    sink.assert_not_called()


async def test_repeated_targets_reach_exact_service_union(hass):
    data, token, _ = await _environment(hass)
    for name in ('audit_a', 'audit_b'):
        er.async_get(hass).async_get_or_create('light', 'test', name, suggested_object_id=name)
        hass.states.async_set(f'light.{name}', 'off')
    calls = []
    async def service(call):
        calls.append(dict(call.data))
    hass.services.async_register('light', 'turn_on', service)
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        _, outcome, _ = await mcp_view._call_tool('call_service', {
            'service': {'domain': 'light', 'name': 'turn_on'},
            'targets': [
                {'kind': 'entity', 'ids': ['light.audit_a']},
                {'kind': 'entity', 'ids': ['light.audit_a', 'light.audit_b']},
            ],
        }, token, hass, data)
    assert outcome == 'allowed'
    assert len(calls) == 1
    assert set(calls[0]['entity_id']) == {'light.audit_a', 'light.audit_b'}
    assert len(calls[0]['entity_id']) == 2


@pytest.mark.parametrize('change', ['revoke', 'policy'])
async def test_later_batch_wait_cannot_disclose_earlier_buffered_read(hass, change):
    data, token, raw = await _environment(hass)
    hass.states.async_set('sensor.audit', 'buffered-secret')
    original = mcp_view._dispatch_mcp_authorized
    async def dispatch(*args, **kwargs):
        if args[1] == 2:
            if change == 'revoke':
                await data.store.async_archive_token(token.id, revoked=True)
            else:
                token.settings_version += 1
                token.pass_through = False
        return await original(*args, **kwargs)
    body = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'get_state', 'arguments': {'entity_id': 'sensor.audit'}}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'},
    ]
    with patch.object(mcp_view, '_dispatch_mcp_authorized', dispatch):
        response = await _view(hass).post(_make_request(body, 'application/json', raw))
    assert 'buffered-secret' not in response.text
    assert all('error' in row for row in json.loads(response.text))


async def test_native_service_rechecks_authority_after_mesa_wait(hass):
    from custom_components.phoenix_mcp.tools import native
    from types import SimpleNamespace

    data, token, raw = await _environment(hass)
    er.async_get(hass).async_get_or_create('light', 'test', 'audit', suggested_object_id='audit')
    hass.states.async_set('light.audit', 'off')
    sink = AsyncMock()
    hass.services.async_register('light', 'turn_on', sink)
    async def gate(*args, **kwargs):
        await data.store.async_archive_token(token.id, revoked=True)
        return SimpleNamespace(response=None, entities=['light.audit'], warnings=[])
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'HassTurnOn', 'arguments': {'domain': ['light']}}}
    with patch.object(native, '_mesa_gate_native', AsyncMock(side_effect=gate)) as mesa:
        response = await _view(hass).post(_make_request(body, 'application/json', raw))
    mesa.assert_awaited_once()
    assert 'error' in json.loads(response.text)
    sink.assert_not_called()
