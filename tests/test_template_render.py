"""Resource and lifecycle tests for isolated template rendering."""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import template_render
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import TEMPLATE_WORKER_CONCURRENCY
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import TokenRecord


def _env():
    token = TokenRecord(id='template', name='template', token_hash='hash', created_at=utcnow(), created_by='admin', pass_through=True)
    return token, PhoenixData(store=MagicMock(), rate_limiter=RateLimiter(), audit=AuditLog())


@pytest.mark.parametrize('cancel', [False, True])
async def test_worker_is_killed_and_reaped_on_timeout_or_cancel(hass, cancel):
    token, data = _env()
    started = asyncio.Event()
    process = MagicMock(returncode=None)
    async def communicate(payload):
        started.set()
        await asyncio.Future()
    process.communicate = communicate
    process.wait = AsyncMock()
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)), patch.object(template_render, 'TEMPLATE_WORKER_TIMEOUT_SECONDS', 0.01):
        task = asyncio.create_task(template_render.async_render_template_for_token('ok', token, hass, data))
        await started.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else template_render.TemplateRenderError):
            await task
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
    assert data.template_slots._value == TEMPLATE_WORKER_CONCURRENCY


async def test_worker_receives_scrubbed_snapshot_only(hass):
    token, data = _env()
    hass.states.async_set('sensor.visible', '42', {'access_token': 'never-copy', 'unit_of_measurement': 'C'})
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b'42', b''))
    process.wait = AsyncMock()
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
        assert await template_render.async_render_template_for_token("{{ states('sensor.visible') }}", token, hass, data) == '42'
    payload = json.loads(process.communicate.call_args.args[0])
    assert set(payload) == {'template', 'states', 'time_zone'}
    assert 'never-copy' not in json.dumps(payload)
    assert payload['states'][0]['state'] == '42'
    process.kill.assert_not_called()


async def test_saturated_worker_limit_refuses_before_spawn(hass):
    token, data = _env()
    for _ in range(TEMPLATE_WORKER_CONCURRENCY):
        await data.template_slots.acquire()
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock()) as spawn:
        with pytest.raises(ValueError, match='capacity'):
            await template_render.async_render_template_for_token('ok', token, hass, data)
    spawn.assert_not_called()


@pytest.mark.skipif(sys.platform != 'linux', reason='HA production OS resource limits require Linux')
@pytest.mark.parametrize('template,expected', [
    ("{{ 1 + 1 }}", '2'),
    ("{{ 'x' * 1000000000 }}", None),
    ('{% for a in range(10000) %}{% for b in range(10000) %}{% set n = 1 %}{% endfor %}{% endfor %}ok', None),
])
async def test_real_worker_budgets_leave_event_loop_responsive(hass, template, expected, monkeypatch):
    monkeypatch.setenv("PYTHONMALLOC", "mimalloc")
    token, data = _env()
    task = asyncio.create_task(template_render.async_render_template_for_token(template, token, hass, data))
    heartbeats = 0
    while not task.done():
        await asyncio.sleep(0.01)
        heartbeats += 1
    assert heartbeats > 0
    if expected is None:
        with pytest.raises((ValueError, TimeoutError)):
            await task
    else:
        assert await task == expected


async def test_worker_inherits_runtime_dependency_paths(hass, monkeypatch):
    token, data = _env()
    monkeypatch.setenv('PYTHONMALLOC', 'mimalloc')
    monkeypatch.setattr(sys, 'path', [*sys.path, '/runtime/integration-dependencies'])
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b'42', b''))
    process.wait = AsyncMock()
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
        assert await template_render.async_render_template_for_token('{{ 6 * 7 }}', token, hass, data) == '42'
    assert '/runtime/integration-dependencies' in spawn.call_args.kwargs['env']['PYTHONPATH']
    assert spawn.call_args.kwargs['env']['PYTHONMALLOC'] == 'pymalloc'
    assert template_render.os.environ['PYTHONMALLOC'] == 'mimalloc'


async def test_worker_startup_failure_has_safe_diagnostic(hass, caplog):
    token, data = _env()
    process = MagicMock(returncode=70)
    process.communicate = AsyncMock(return_value=(b'', b'ModuleNotFoundError module=example_dependency\n'))
    process.wait = AsyncMock()
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
        with pytest.raises(template_render.TemplateRenderError, match='could not start'):
            await template_render.async_render_template_for_token('private-template-value', token, hass, data)
    assert 'module=example_dependency' in caplog.text
    assert 'private-template-value' not in caplog.text
    process.wait.assert_awaited_once()


def test_snapshot_preserves_all_template_metadata_without_user_identity(monkeypatch):
    from datetime import datetime, timezone
    from io import BytesIO, StringIO
    from types import SimpleNamespace
    from homeassistant.core import Context, State
    from custom_components.phoenix_mcp import template_worker
    from custom_components.phoenix_mcp.helpers import ScrubbedState, render_template_snapshot

    original = ScrubbedState(State(
        entity_id='sensor.metadata', state='42',
        attributes={'unit_of_measurement': 'C', 'access_token': 'never-copy'},
        last_changed=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
        last_updated=datetime(2026, 1, 2, 2, tzinfo=timezone.utc),
        last_reported=datetime(2026, 1, 2, 3, tzinfo=timezone.utc),
        context=Context(id='context-id', parent_id='parent-id', user_id='never-copy'),
    ))
    encoded = json.dumps(original.as_dict())
    restored = ScrubbedState.from_dict(json.loads(encoded))
    assert 'never-copy' not in encoded
    assert restored.last_changed == original.last_changed
    assert restored.last_updated == original.last_updated
    assert restored.last_reported == original.last_reported
    assert restored.context == original.context
    assert restored.context.user_id is None
    template = (
        '{{ states.sensor.metadata.last_changed.isoformat() }}|'
        '{{ states.sensor.metadata.last_updated.isoformat() }}|'
        '{{ states.sensor.metadata.last_reported.isoformat() }}|'
        '{{ states.sensor.metadata.context.id }}|'
        '{{ states.sensor.metadata.context.parent_id }}|'
        '{{ states.sensor.metadata.context.user_id }}'
    )
    expected = render_template_snapshot(template, {original.entity_id: original})
    assert render_template_snapshot(template, {original.entity_id: restored}) == expected
    payload = json.dumps({'template': template, 'states': [original.as_dict()], 'time_zone': 'UTC'}).encode()
    output = StringIO()
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=BytesIO(payload)))
    monkeypatch.setattr(sys, 'stdout', output)
    # Exercise the actual worker's decode/reconstruction/render path in-process;
    # OS resource enforcement is covered separately by the Linux-only tests.
    with patch.object(template_worker.resource, 'setrlimit'), patch('homeassistant.util.dt.set_default_time_zone'):
        template_worker.main()
    assert output.getvalue() == expected


@pytest.mark.parametrize('returncode,payload,expected', [
    (65, b'{"kind":"division","line":2}', 'Line 2: {{ 1 / 0 }}'),
    (65, b'{"kind":"syntax","message":"unexpected end","line":2}', 'syntax error: unexpected end'),
    (65, b'not-json-secret', 'Template worker failed'),
    (-9, b'secret', 'exited unexpectedly'),
    (1, b'secret', 'exited unexpectedly'),
    (-24, b'', 'CPU budget'),
])
async def test_parent_classifies_worker_failures(hass, returncode, payload, expected):
    import signal
    if returncode == -24:
        returncode = -signal.SIGXCPU
    token, data = _env()
    process = MagicMock(returncode=returncode)
    process.communicate = AsyncMock(return_value=(payload, b'private-stderr'))
    process.wait = AsyncMock()
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
        with pytest.raises(template_render.TemplateRenderError) as failure:
            await template_render.async_render_template_for_token('first\n{{ 1 / 0 }}', token, hass, data)
    assert expected in str(failure.value)
    assert 'secret' not in str(failure.value)
    assert 'private-stderr' not in str(failure.value)
    process.wait.assert_awaited_once()


async def test_spawn_failure_is_safe_and_releases_capacity(hass):
    token, data = _env()
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(side_effect=OSError('private-path'))):
        with pytest.raises(template_render.TemplateRenderError, match='could not start') as failure:
            await template_render.async_render_template_for_token('ok', token, hass, data)
    assert 'private-path' not in str(failure.value)
    assert data.template_slots._value == TEMPLATE_WORKER_CONCURRENCY


@pytest.mark.parametrize('change', ['policy', 'revoke', 'kill', 'unload'])
async def test_diagnostics_recheck_real_authority_after_worker_wait(hass, change):
    from custom_components.phoenix_mcp.const import DOMAIN
    from custom_components.phoenix_mcp.request_authority import (
        AuthorityChangedError, RequestAuthority, policy_version, request_authority,
    )
    token, data = _env()
    data.ready = True
    hass.data[DOMAIN] = data
    data.store.get_token_by_hash.return_value = token
    data.store.get_settings.return_value.kill_switch = False
    process = MagicMock(returncode=65)
    async def communicate(payload):
        if change == 'policy':
            token.settings_version += 1
        elif change == 'revoke':
            data.store.get_token_by_hash.return_value = None
        elif change == 'kill':
            data.store.get_settings.return_value.kill_switch = True
        else:
            data.shutting_down = True
        return b'{"kind":"syntax","message":"never-disclose","line":1}', b''
    process.communicate = communicate
    process.wait = AsyncMock()
    authority_reset = request_authority.set(RequestAuthority(hass, data, token.id, token.token_hash))
    policy_reset = policy_version.set(token.settings_version)
    try:
        with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            from custom_components.phoenix_mcp.mcp_view import _tool_render_template
            with pytest.raises(AuthorityChangedError):
                await _tool_render_template({'template': 'private-input'}, token, hass, data)
    finally:
        request_authority.reset(authority_reset)
        policy_version.reset(policy_reset)
    process.wait.assert_awaited_once()


@pytest.mark.parametrize('template,kind,line', [
    ('first\n{{ 1 + }}', 'syntax', 2),
    ('\n{{ 1 / 0 }}', 'division', 2),
    ('{{ missing.foo }}', 'undefined', 1),
    ('{{ "x" * 300000 }}', 'output', None),
])
def test_worker_serializes_real_failures_without_tracebacks(monkeypatch, caplog, template, kind, line):
    import logging
    from io import BytesIO, StringIO
    from types import SimpleNamespace
    from custom_components.phoenix_mcp import template_worker
    payload = json.dumps({'template': template, 'states': [], 'time_zone': 'UTC'}).encode()
    output = StringIO()
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=BytesIO(payload)))
    monkeypatch.setattr(sys, 'stdout', output)
    previous_logging_disable = logging.root.manager.disable
    with patch.object(template_worker.resource, 'setrlimit'), patch('homeassistant.util.dt.set_default_time_zone'):
        with pytest.raises(SystemExit) as exit_info:
            template_worker.main()
    assert exit_info.value.code == 65
    detail = json.loads(output.getvalue())
    assert detail['kind'] == kind
    assert detail['line'] == line
    assert 'Traceback' not in output.getvalue()
    assert not caplog.records
    assert logging.root.manager.disable == previous_logging_disable


async def test_error_response_uses_only_scoped_snapshot(hass, monkeypatch):
    from io import BytesIO, StringIO
    from types import SimpleNamespace
    from custom_components.phoenix_mcp import template_worker
    from custom_components.phoenix_mcp.mcp_view import _tool_render_template
    from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree

    token, data = _env()
    token.pass_through = False
    token.cap_template_render = 'allow'
    token.permissions = PermissionTree(entities={'sensor.allowed': PermissionNode(state='YELLOW')})
    hass.states.async_set('sensor.allowed', 'private-visible-value', {'access_token': 'private-credential'})
    hass.states.async_set('sensor.hidden', 'private-hidden-value')
    snapshots = []
    async def spawn(*args, **kwargs):
        process = MagicMock(returncode=None)
        process.wait = AsyncMock()
        async def communicate(payload):
            snapshots.append(json.loads(payload))
            output = StringIO()
            with monkeypatch.context() as worker_patch:
                worker_patch.setattr(sys, 'path', list(sys.path))
                worker_patch.setattr(sys, 'stdin', SimpleNamespace(buffer=BytesIO(payload)))
                worker_patch.setattr(sys, 'stdout', output)
                with patch.object(template_worker.resource, 'setrlimit'), patch('homeassistant.util.dt.set_default_time_zone'):
                    try:
                        template_worker.main()
                    except SystemExit as exc:
                        process.returncode = exc.code
                    else:
                        process.returncode = 0
            return output.getvalue().encode(), b''
        process.communicate = communicate
        return process
    with patch.object(template_render.asyncio, 'create_subprocess_exec', spawn):
        arguments = {'template': "{{ states('sensor.hidden') | int }}"}
        hidden, outcome, _ = await _tool_render_template(arguments, token, hass, data)
        hass.states.async_remove('sensor.hidden')
        missing, _, _ = await _tool_render_template(arguments, token, hass, data)
        visible, _, _ = await _tool_render_template({'template': "{{ states('sensor.allowed') | int }}"}, token, hass, data)
    assert hidden == missing
    assert outcome == 'invalid_request' and hidden['isError']
    assert 'Line 1:' in hidden['content'][0]['text']
    assert 'private-' not in json.dumps([hidden, visible])
    assert 'private-hidden-value' not in json.dumps(snapshots)
    assert 'private-credential' not in json.dumps(snapshots)
    assert snapshots[0]['states'][0]['state'] == 'private-visible-value'


async def test_capability_denial_precedes_diagnostics(hass):
    from custom_components.phoenix_mcp.mcp_view import _tool_render_template
    token, data = _env()
    token.pass_through = False
    token.cap_template_render = 'deny'
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock()) as spawn:
        result, outcome, _ = await _tool_render_template({'template': '{{ invalid + }}'}, token, hass, data)
    assert outcome == 'denied'
    assert result['isError'] and result['content'][0]['text'] == 'Forbidden.'
    spawn.assert_not_called()


async def test_authority_change_during_reaping_suppresses_diagnostics(hass):
    from custom_components.phoenix_mcp.request_authority import AuthorityChangedError
    token, data = _env()
    process = MagicMock(returncode=65)
    process.communicate = AsyncMock(return_value=(b'{"kind":"division","line":1}', b''))
    authority_changed = False
    async def wait():
        nonlocal authority_changed
        authority_changed = True
    def check():
        if authority_changed:
            raise AuthorityChangedError('Permissions changed.')
    process.wait = wait
    with patch.object(template_render.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)), patch.object(template_render, 'check_request_authority', check):
        with pytest.raises(AuthorityChangedError):
            await template_render.async_render_template_for_token('{{ 1 / 0 }}', token, hass, data)


@pytest.mark.skipif(sys.platform != 'linux', reason='HA production OS resource limits require Linux')
async def test_real_worker_returns_template_location(hass):
    token, data = _env()
    with pytest.raises(template_render.TemplateRenderError, match='Line 2:'):
        await template_render.async_render_template_for_token('\n{{ 1 / 0 }}', token, hass, data)
