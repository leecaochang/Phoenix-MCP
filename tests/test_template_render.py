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
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
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
