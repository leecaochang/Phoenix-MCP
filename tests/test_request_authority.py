"""Authority is rechecked wherever an admitted request waits; wire input stays bounded.

Revocation, rotation, expiry, policy edits and the kill switch take effect between
admission and dispatch (body read, batch items, watch and MESA waits). Non-finite
numbers and excessive nesting never reach a handler.
"""

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
from custom_components.phoenix_mcp.token_store import TokenRecord, PermissionNode, PermissionTree, TokenStore
from tests.test_mcp_sse import _make_request as _transport_request, _view


def _make_request(*args, **kwargs):
    kwargs["extra_headers"] = {"Mcp-Session-Id": "authority-session", **kwargs.get("extra_headers", {})}
    return _transport_request(*args, **kwargs)


async def _environment(hass):
    store = TokenStore(hass, AsyncMock())
    store.get_settings().mesa_mode = "off"
    token, raw = await store.async_create_token('audit-boundary', 'admin', rate_limit_requests=0)
    token.pass_through = True
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    data.mcp_sessions["authority-session"] = {"token_id": token.id, "initialized": True,
        "last_seen": mcp_view.time.monotonic(), "protocol_version": "2025-03-26"}
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
    body = json.loads(response.text)
    assert body['result']['isError'] if change == 'policy' else 'error' in body


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


async def test_excessive_wire_nesting_never_reaches_handler(hass):
    _, _, raw = await _environment(hass)
    req = _make_request({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, 'application/json', raw)
    payload = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_state","arguments":{"deep":'
               + '[' * 2000 + '0' + ']' * 2000 + '}}}')
    req.content.read = AsyncMock(return_value=payload.encode())
    req.content.at_eof = lambda: True
    handler = AsyncMock()
    with patch.dict(mcp_view._TOOL_HANDLERS, {'get_state': (handler, (), {})}):
        response = await _view(hass).post(req)
    assert json.loads(response.text)['result']['isError']
    handler.assert_not_called()


async def test_json_decoder_recursion_error_is_contained(hass):
    _, _, raw = await _environment(hass)
    req = _make_request({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, 'application/json', raw)
    with patch.object(mcp_view.json, 'loads', side_effect=RecursionError):
        response = await _view(hass).post(req)
    assert json.loads(response.text)['error']['code'] == -32700


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
    async def prepare(*args, domain, service):
        assert (domain, service) == ('light', 'turn_on')
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
    body = json.loads(response.text)
    assert body['result']['isError'] is True
    assert 'No action was executed' in json.dumps(body)
    assert not data.agent_recovery.blocks(token.id)
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


async def _internal_ingress(kind, hass, data, token, name, arguments):
    from types import SimpleNamespace
    from custom_components.phoenix_mcp import agentcli
    from custom_components.phoenix_mcp.assist_api import PhoenixAssistTool

    if kind == 'assist':
        tool = PhoenixAssistTool(name, '', MagicMock(), token.id)
        from homeassistant.auth.const import GROUP_ID_ADMIN
        from homeassistant.core import Context
        user = await hass.auth.async_create_user('authority-test-admin', group_ids=[GROUP_ID_ADMIN])
        return await tool.async_call(hass, SimpleNamespace(id='call', tool_args=arguments), SimpleNamespace(context=Context(user_id=user.id)))
    call = {'id': 'call', 'name': name, 'input': arguments}
    if kind == 'interactive':
        result = await agentcli._run_tool_batch(
            [call], hass=hass, data=data, token_id=token.id, client_ip='agentcli',
            base_url='http://example.invalid', emit=AsyncMock(), cancel=asyncio.Event(),
        )
        return result.results
    captured = []
    provider = MagicMock()
    provider.format_tools.return_value = []
    provider.append_tool_results.side_effect = lambda messages, results: captured.extend(results)
    async def stream(*args, **kwargs):
        yield {'type': agentcli.EV_TOOL, **call}
        yield {'type': agentcli.EV_DONE, 'stop_reason': 'tool_use', 'assistant_msg': {'role': 'assistant', 'content': []}}
    with patch.object(agentcli, '_stream_turn_resilient', stream):
        await agentcli._run_headless_turn(
            hass, data, token, provider, MagicMock(), 'http://example.invalid',
            system='', seed='', client_ip='voice_agent', max_iterations=1,
        )
    return captured


async def _change_authority(data, token, change):
    if change == 'none':
        return
    if change == 'grant':
        await data.store.async_patch_token(token.id, cap_search='allow')
        return
    if change == 'revoke':
        await data.store.async_archive_token(token.id, revoked=True)
    elif change == 'rotate':
        await data.store.async_rotate_token(token.id)
    elif change == 'expire':
        token.expires_at = utcnow() - timedelta(seconds=1)
    elif change == 'policy':
        token.pass_through = False
        token.settings_version += 1
    else:
        data.store.get_settings().kill_switch = True


@pytest.mark.parametrize('ingress', ['assist', 'interactive', 'headless'])
@pytest.mark.parametrize('change', ['revoke', 'rotate', 'expire', 'policy', 'kill', 'grant', 'none'])
async def test_internal_watch_suppresses_state_after_authority_changes(hass, ingress, change):
    from custom_components.phoenix_mcp.request_authority import request_authority, policy_version

    data, token, _ = await _environment(hass)
    hass.states.async_set('sensor.audit', 'old')
    subscribed = asyncio.Event()
    callbacks = []
    def subscribe(_hass, _ids, callback):
        callbacks.append(callback)
        subscribed.set()
        return lambda: None
    with patch.object(mcp_view, 'async_track_state_change_event', subscribe):
        task = asyncio.create_task(_internal_ingress(
            ingress, hass, data, token, 'watch_entity', {'entity_id': 'sensor.audit', 'timeout': 1},
        ))
        await asyncio.wait_for(subscribed.wait(), 2)
        await _change_authority(data, token, change)
        hass.states.async_set('sensor.audit', 'protected-after-wait')
        callbacks[0](MagicMock(data={'new_state': hass.states.get('sensor.audit')}))
        result = await task
    if change in ('none', 'grant'):
        assert 'protected-after-wait' in json.dumps(result)
    elif ingress == 'assist':
        assert 'error' in result
    else:
        assert result[0]['is_error'] is True
    if change not in ('none', 'grant'):
        assert 'protected-after-wait' not in json.dumps(result)
    assert request_authority.get() is None
    assert policy_version.get() is None


@pytest.mark.parametrize('ingress', ['assist', 'interactive', 'headless'])
@pytest.mark.parametrize('change', ['revoke', 'rotate', 'expire', 'policy', 'kill', 'grant', 'none'])
async def test_internal_service_rechecks_before_side_effect(hass, ingress, change):
    data, token, _ = await _environment(hass)
    er.async_get(hass).async_get_or_create('light', 'test', 'audit', suggested_object_id='audit')
    hass.states.async_set('light.audit', 'off')
    sink = AsyncMock()
    hass.services.async_register('light', 'turn_on', sink)
    calls = 0
    async def prepare(*args, domain, service):
        assert (domain, service) == ('light', 'turn_on')
        nonlocal calls
        calls += 1
        if calls == 2:
            await _change_authority(data, token, change)
        return []
    with patch.object(mcp_view, 'unsupported_secondary_targets', prepare):
        result = await _internal_ingress(ingress, hass, data, token, 'call_service', {
            'service': {'domain': 'light', 'name': 'turn_on'},
            'targets': [{'kind': 'entity', 'ids': ['light.audit']}],
        })
    assert calls == 2
    if change in ('none', 'grant'):
        sink.assert_awaited_once()
    else:
        sink.assert_not_called()
        assert 'error' in result if ingress == 'assist' else result[0]['is_error']


async def test_internal_dispatch_preserves_zero_inline_wait_and_cleans_context(hass):
    from custom_components.phoenix_mcp.agentcli import _current_dispatch_token
    from custom_components.phoenix_mcp.request_authority import request_authority, policy_version

    data, token, _ = await _environment(hass)
    token.confirm_inline_wait_seconds = 60
    captured = []
    async def execute(method, msg_id, params, admitted, *args):
        captured.append(admitted.confirm_inline_wait_seconds)
        assert request_authority.get() is not None
        raise RuntimeError('failure before return')
    with patch.object(mcp_view, '_dispatch_mcp_authorized', execute):
        result = await mcp_view._dispatch_mcp('ping', 1, {}, _current_dispatch_token(data, token.id), hass, data, 'assist', '')
    assert result[0]['id'] == 1 and result[0]['error']['code'] == -32603
    assert result[3] == 'invalid_request'
    assert captured == [0]
    assert token.confirm_inline_wait_seconds == 60
    assert request_authority.get() is None
    assert policy_version.get() is None


async def test_grants_cannot_erase_an_intervening_restriction(hass):
    data, token, _ = await _environment(hass)
    token.pass_through = False
    admitted = token.settings_version
    await data.store.async_patch_token(token.id, cap_script_write="confirm")
    await data.store.async_patch_token(token.id, cap_script_write="allow")
    assert token.accepts_policy_version(admitted)
    await data.store.async_patch_token(token.id, cap_script_write="deny")
    await data.store.async_patch_token(token.id, cap_search="allow")
    assert not token.accepts_policy_version(admitted)
    assert token.accepts_policy_version(token.settings_version - 1)
    restored = TokenRecord.from_dict(token.to_storage_dict())
    assert not restored.accepts_policy_version(token.settings_version - 1)


async def test_pass_through_confirm_is_a_restriction_and_mixed_edits_stay_strict(hass):
    data, token, _ = await _environment(hass)
    admitted = token.settings_version
    await data.store.async_patch_token(token.id, cap_script_write="confirm")
    assert not token.accepts_policy_version(admitted)
    admitted = token.settings_version
    await data.store.async_patch_token(token.id, cap_script_write="allow", pass_through=False)
    assert not token.accepts_policy_version(admitted)


@pytest.mark.parametrize("change", ["grant", "restriction"])
async def test_chat_continues_after_capability_edit_during_tool_wait(hass, change):
    from custom_components.phoenix_mcp import agentcli

    data, token, _ = await _environment(hass)
    token.pass_through = False
    token.cap_config_read = 'allow'
    token.permissions = PermissionTree(entities={'sensor.audit': PermissionNode(state='YELLOW')})
    data.store.get_settings().conversation_log_enabled = True
    hass.states.async_set('sensor.audit', 'old')
    subscribed = asyncio.Event()
    callbacks = []
    def subscribe(_hass, _ids, callback):
        callbacks.append(callback)
        subscribed.set()
        return lambda: None
    rounds = 0
    async def stream(*args, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield {'type': agentcli.EV_TOOL, 'id': 'read', 'name': 'watch_entity',
                   'input': {'entity_id': 'sensor.audit', 'timeout': 1}}
            yield {'type': agentcli.EV_DONE, 'stop_reason': 'tool_use', 'assistant_msg': {'role': 'assistant', 'content': []}}
        else:
            yield {'type': agentcli.EV_TEXT, 'text': 'Conversation continued.'}
            yield {'type': agentcli.EV_DONE, 'stop_reason': 'end_turn', 'assistant_msg': {'role': 'assistant', 'content': 'Conversation continued.'}}
    provider = agentcli.OpenAICompatProvider(agentcli.ProviderConfig(kind='chatgpt', model='selected-test-model', base_url='https://example.invalid'))
    emit = AsyncMock()
    with patch.object(mcp_view, 'async_track_state_change_event', subscribe), patch.object(agentcli, '_stream_turn_resilient', stream):
        task = asyncio.create_task(agentcli.async_run_agent_turn(
            hass=hass, data=data, token=token, provider=provider, session=MagicMock(),
            messages=[{'role': 'user', 'content': 'Watch the test sensor.'}], options={},
            client_ip='agentcli', base_url='http://example.invalid', emit=emit, cancel=asyncio.Event(),
        ))
        await asyncio.wait_for(subscribed.wait(), 2)
        await data.store.async_patch_token(token.id, **({'cap_search': 'allow'} if change == 'grant' else {'cap_config_read': 'deny'}))
        hass.states.async_set('sensor.audit', 'new')
        callbacks[0](MagicMock(data={'new_state': hass.states.get('sensor.audit')}))
        await task
    assert rounds == 2
    assert not any(call.args[0] == 'error' for call in emit.call_args_list)
    row = data.conversations.query()['entries'][0]
    entry = data.conversations.detail(row['id'])
    assert entry['status'] == 'completed'
    assert entry['execution_status'] == ('allowed' if change == 'grant' else 'denied')
    if change == 'restriction':
        result = next(event['payload'] for event in entry['events'] if event['kind'] == 'tool_result')
        assert result['result']['isError'] and 'Permissions changed' in json.dumps(result)
        assert '"state": "new"' not in json.dumps(result)
    assert entry['response'] == 'Conversation continued.'
    assert entry['metadata']['model'] == 'selected-test-model'


async def test_policy_change_during_reconciliation_reports_no_dispatch(hass):
    data, token, _ = await _environment(hass)

    async def tighten(*_args):
        token.settings_version += 1
        token.pass_through = False

    with patch.object(data.agent_recovery, "reconcile", side_effect=tighten), \
            patch.object(mcp_view, "_dispatch_mcp_authorized", AsyncMock()) as dispatch:
        response, _, _, outcome = await mcp_view._dispatch_mcp(
            "tools/call", 1, {"name": "call_service", "arguments": {}},
            token, hass, data, "agentcli", "",
        )
    dispatch.assert_not_called()
    assert outcome == "denied"
    assert "No action was executed" in json.dumps(response)
    assert "result was withheld" not in json.dumps(response)
