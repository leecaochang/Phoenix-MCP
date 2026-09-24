"""Transport measurements preserve missing usage, bounded timings and private content."""
import asyncio
import json

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.phoenix_mcp.agentcli import ProviderConfig
from custom_components.phoenix_mcp.const import DECISION_METRIC_SAMPLES
from custom_components.phoenix_mcp.decision_metrics import DecisionMetrics
from custom_components.phoenix_mcp.decision_provider import DecisionProvider, DecisionError


def test_metrics_have_fixed_keys_and_bounded_samples():
    metrics = DecisionMetrics()
    for index in range(DECISION_METRIC_SAMPLES + 10):
        metrics.record('preview', 'batch', 20, index, {'private': 'secret', 'usage': {'input_tokens': 7, 'output_tokens': 2}}, provider_kind='typesafe')
        metrics.record('secret prompt', 'batch', 20, index, {}, provider_kind='typesafe')
        metrics.record('chat', 'private model', 20, index, {}, provider_kind='typesafe')
    report = metrics.summary()['typesafe']['preview']['batch']
    assert report['requests'] == DECISION_METRIC_SAMPLES + 10
    assert report['timing_samples'] == DECISION_METRIC_SAMPLES
    assert report['reported_input_tokens'] == 7 * report['requests']
    assert report['usage_unknown'] == 0
    assert report['p95_elapsed_ms'] > report['p50_elapsed_ms']
    assert 'secret' not in repr(metrics) and 'private' not in repr(metrics)
    report['requests'] = 0
    assert metrics.summary()['typesafe']['preview']['batch']['requests'] > 0
    assert DecisionMetrics().summary() == {}


@pytest.mark.parametrize('usage', [None, {}, {'input_tokens': True, 'output_tokens': 0},
    {'input_tokens': -1, 'output_tokens': 0}, {'input_tokens': 3},
    {'input_tokens': float('nan'), 'output_tokens': 0}, {'input_tokens': 10**100, 'output_tokens': 0}])
def test_unknown_usage_is_not_zero_or_a_cost_estimate(usage):
    metrics = DecisionMetrics()
    metrics.record('chat', 'context', 10, 10, {'usage': usage}, provider_kind='typesafe')
    metrics.record('chat', 'context', 10, 30, None, provider_kind='typesafe')
    result = metrics.summary()['typesafe']['chat']['context']
    assert result['responses'] == 1 and result['requests'] == 2
    assert result['usage_reports'] == 0 and result['usage_unknown'] == 2
    assert result['reported_input_tokens'] is None and result['reported_output_tokens'] is None
    assert result['mean_elapsed_ms'] == 20


@pytest.mark.parametrize('status', [200, 429])
async def test_real_request_records_usage_and_errors_without_bodies(status, socket_enabled):
    payload = {'state': {'utterance': 'private utterance'}, 'questions': {'route': {}, 'target': {}}}
    metrics = DecisionMetrics()
    async def handler(request):
        assert await request.json() == payload
        return web.json_response({'usage': {'input_tokens': 123, 'output_tokens': 4}, 'private': 'private result'}, status=status)
    app = web.Application(); app.router.add_post('/systemone', handler)
    async with TestServer(app) as server, ClientSession() as session:
        provider = DecisionProvider(ProviderConfig('typesafe', 'model', str(server.make_url('')).rstrip('/')),
                                    'systemone', metrics=metrics)
        if status == 200:
            await provider._request(session, 'POST', 'systemone', 1000, payload)
        else:
            with pytest.raises(DecisionError, match='rate_limited'):
                await provider._request(session, 'POST', 'systemone', 1000, payload)
    result = metrics.summary()['typesafe']['preview']['batch']
    assert result['requests'] == 1 and result['request_bytes'] == len(json.dumps(payload).encode())
    assert result['reported_input_tokens'] == (123 if status == 200 else None)
    assert 'private' not in repr(metrics)


async def test_cancelled_request_is_counted_with_unknown_usage(socket_enabled):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        await release.wait()
        return web.json_response({})
    metrics = DecisionMetrics()
    app = web.Application(); app.router.add_post('/systemone', handler)
    async with TestServer(app) as server, ClientSession() as session:
        provider = DecisionProvider(ProviderConfig('typesafe', 'm', str(server.make_url('')).rstrip('/')),
                                    'systemone', metrics=metrics, metric_scope='voice')
        task = asyncio.create_task(provider._request(session, 'POST', 'systemone', 1000, {'questions': {'route': {}}}))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        release.set()
    assert metrics.summary()['typesafe']['voice']['classify']['usage_unknown'] == 1
