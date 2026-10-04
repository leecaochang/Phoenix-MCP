"""Shared request reservations survive restarts, races and uncertain network outcomes."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.phoenix_mcp.admin_view import _validated_settings_patch
from custom_components.phoenix_mcp.agentcli import ProviderConfig
from custom_components.phoenix_mcp.decision_budget import DecisionBudgetError, DecisionRequestBudget
from custom_components.phoenix_mcp.decision_metrics import DecisionMetrics
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider


class MemoryStore:
    def __init__(self): self.value = None
    async def async_load(self): return deepcopy(self.value)
    async def async_save(self, value): self.value = deepcopy(value)
    async def async_remove(self): self.value = None


async def test_core_wipe_clears_durable_allowance_and_failed_removal_stays_closed():
    store = MemoryStore()
    budget = DecisionRequestBudget(store)
    await budget.reserve(1)
    await budget.async_wipe()
    assert store.value is None and budget.summary(1)['used'] == 0
    await budget.reserve(1)
    store.async_remove = AsyncMock(side_effect=OSError('unavailable'))
    with pytest.raises(OSError): await budget.async_wipe()
    with pytest.raises(DecisionBudgetError, match='budget_unavailable'): await budget.reserve(2)


async def test_concurrent_callers_share_exact_allowance_and_restart():
    store = MemoryStore()
    budget = DecisionRequestBudget(store)
    with patch('custom_components.phoenix_mcp.decision_budget.time', return_value=3601):
        outcomes = await asyncio.gather(*(budget.reserve(3) for _ in range(20)), return_exceptions=True)
        assert outcomes.count(None) == 3
        assert all(isinstance(item, DecisionBudgetError) for item in outcomes if item is not None)
        restarted = DecisionRequestBudget(store)
        await restarted.async_load()
        with pytest.raises(DecisionBudgetError, match='request_budget_exhausted'): await restarted.reserve(3)
        assert restarted.summary(3)['remaining'] == 0
        await restarted.reserve(4)
    with patch('custom_components.phoenix_mcp.decision_budget.time', return_value=10):
        with pytest.raises(DecisionBudgetError): await restarted.reserve(3)
    with patch('custom_components.phoenix_mcp.decision_budget.time', return_value=7201):
        await restarted.reserve(3)
        assert restarted.summary(3)['used'] == 1


@pytest.mark.parametrize('raw', [{}, {'window': True, 'used': 0}, {'window': 1, 'used': -1}, 'invalid'])
async def test_corrupt_ledger_refuses_only_budgeted_requests(raw):
    store = MemoryStore(); store.value = raw
    budget = DecisionRequestBudget(store)
    await budget.async_load()
    with pytest.raises(DecisionBudgetError, match='budget_unavailable'): await budget.reserve(2)
    await budget.reserve(0)
    assert budget.summary(2)['available'] is False


async def test_failed_save_blocks_future_reservations():
    store = AsyncMock(); store.async_save.side_effect = OSError('disk unavailable')
    budget = DecisionRequestBudget(store)
    with pytest.raises(DecisionBudgetError, match='budget_unavailable'): await budget.reserve(2)
    with pytest.raises(DecisionBudgetError, match='budget_unavailable'): await budget.reserve(2)
    assert store.async_save.await_count == 1


async def test_cancelled_save_holds_lock_until_durable_and_does_not_refund():
    entered, release = asyncio.Event(), asyncio.Event()
    store = MemoryStore()
    original = store.async_save
    async def save(value):
        entered.set(); await release.wait(); await original(value)
    store.async_save = save
    budget = DecisionRequestBudget(store)
    first = asyncio.create_task(budget.reserve(1))
    await entered.wait()
    first.cancel()
    second = asyncio.create_task(budget.reserve(1))
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await first
    with pytest.raises(DecisionBudgetError, match='request_budget_exhausted'): await second
    assert store.value['used'] == 1


@pytest.mark.parametrize('status', [200, 429, 500])
async def test_transport_shares_preview_voice_chat_and_counts_failures(status, socket_enabled):
    received = []
    async def handler(request):
        received.append(await request.json())
        return web.json_response({}, status=status)
    app = web.Application(); app.router.add_post('/systemone', handler)
    budget, metrics = DecisionRequestBudget(), DecisionMetrics()
    async with TestServer(app) as server, ClientSession() as session:
        cfg = ProviderConfig('typesafe', 'model', str(server.make_url('')).rstrip('/'))
        for index, scope in enumerate(('preview', 'voice', 'chat')):
            provider = DecisionProvider(cfg, 'systemone', request_budget=budget, request_limit=2,
                                        metrics=metrics, metric_scope=scope)
            if index == 2 or status != 200:
                expected = 'request_budget_exhausted' if index == 2 else 'rate_limited' if status == 429 else 'http_error'
                with pytest.raises(DecisionError, match=expected):
                    await provider._request(session, 'POST', 'systemone', 1000, {'questions': {'route': {}}})
            else:
                await provider._request(session, 'POST', 'systemone', 1000, {'questions': {'route': {}}})
    assert len(received) == 2 and budget.summary(2)['used'] == 2
    assert 'chat' not in metrics.summary()['typesafe']


async def test_authority_checked_after_reservation_before_any_network():
    store = AsyncMock()
    current = True
    async def save(value):
        nonlocal current
        current = False
    store.async_save.side_effect = save
    def guard():
        if not current: raise DecisionError('authority_changed')
    provider = DecisionProvider(ProviderConfig('typesafe', 'm', 'http://test'), 'systemone',
        request_budget=DecisionRequestBudget(store), request_limit=1, request_guard=guard)
    session = AsyncMock()
    with pytest.raises(DecisionError, match='authority_changed'):
        await provider._request(session, 'POST', 'systemone', 1000, {})
    session.request.assert_not_called()


@pytest.mark.parametrize('value', [-1, True, '10', 1.5, 100001, None, 0, 25, 100000])
async def test_admin_budget_validates_integer_bounds(hass, access_key_store, value):
    changes, error = await _validated_settings_patch({'decision_request_budget': value}, SimpleNamespace(store=access_key_store), hass, 'rid')
    if type(value) is int and 0 <= value <= 100000:
        assert error is None and changes['decision_request_budget'] == value
    else:
        assert error is not None
