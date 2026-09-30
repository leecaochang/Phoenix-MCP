"""Tool advice preserves complete candidates, protocol pairing and dispatch authority."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.phoenix_mcp import agentcli, tool_routing
from custom_components.phoenix_mcp.const import DOMAIN, TOOL_ROUTING_SETTINGS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_history import pending_context_history
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider
from custom_components.phoenix_mcp.token_store import GlobalSettings

CATALOG = [{"name": name, "description": description, "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": name != "call_service"}}
           for name, description in [("get_state", "Read current state"), ("get_history", "Read history"),
                                     ("call_service", "Change device state")]]
CALLS = [{"id": "first", "name": "get_state", "input": {}},
         {"id": "second", "name": "call_service", "input": {}}]


def history(calls=None, dialect="claude"):
    calls = calls or CALLS
    pending = {"role": "assistant", "content": [{"type": "tool_use", **c} for c in calls]}
    if dialect == "openai":
        pending = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["input"])}}
            for c in calls]}
    return [{"role": "user", "content": "Investigate overnight readings. Do not change devices."}, pending]


@pytest.fixture
async def home(hass, token_store):
    token, _ = await token_store.async_create_token("router", "test")
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add("typesafe", {"api_key": "private-test-key", "model": "jev-latest"})
    hass.data['_phoenix_agentcli_store'] = store
    await token_store.async_patch_settings(decision_provider_id=account, agentcli_tool_routing_mode="observe")
    with patch.object(agentcli, 'build_mcp_tool_list', return_value=deepcopy(CATALOG)):
        yield data, token, store, account


async def review(hass, home, **overrides):
    data, token, _, _ = home
    turn = overrides.pop('turn', tool_routing.create_review_state(data, token, 'chat'))
    args = dict(surface="chat", language="en", messages=history(), calls=deepcopy(CALLS),
                system="Respect the user restrictions.", remaining_rounds=2)
    args.update(overrides)
    return await tool_routing.async_review_tools(hass, data, turn, **args)


def qualify(monkeypatch):
    monkeypatch.setattr(tool_routing, 'TOOL_ROUTING_QUALIFIED_MODELS', {
        ('typesafe', 'https://api.typesafe.ai/v1', 'jev-latest', 'en'): ('test-model',)})


@pytest.mark.parametrize('mode,tool,reason,action', [
    ('off', 'get_history', 'off', 'continue'), ('observe', 'get_state', 'agreement', 'continue'),
    ('observe', 'get_history', 'alternative', 'continue'), ('observe', None, 'declined', 'continue'),
    ('advise', 'get_history', 'replan', 'replan'), ('advise', 'call_service', 'replan', 'replan')])
async def test_modes(hass, home, monkeypatch, mode, tool, reason, action):
    qualify(monkeypatch)
    await home[0].store.async_patch_settings(agentcli_tool_routing_mode=mode)
    with patch.object(DecisionProvider, 'select_tool', AsyncMock(return_value={'tool': tool, 'model': 'test-model'})) as choose:
        result = await review(hass, home)
    assert (result.reason, result.action) == (reason, action)
    assert choose.await_count == (mode != 'off')
    if mode != 'off':
        assert len(choose.call_args.args[1]['catalog']) == 3
    assert home[0].decision_shadow.task is None


@pytest.mark.parametrize('dialect', ['claude', 'openai'])
def test_pending_exchange_exact_args_and_pairing(dialect):
    messages = history(dialect=dialect)
    assert pending_context_history(messages, CALLS) == messages
    changed = deepcopy(CALLS)
    changed[0]['input'] = {'new': 1}
    assert pending_context_history(messages, changed) is None
    assert pending_context_history(messages, CALLS[:1]) is None
    assert pending_context_history(messages, CALLS[::-1]) is None
    assert pending_context_history(messages, CALLS * 2) is None


async def test_unqualified_advice_sends_nothing(hass, home):
    await home[0].store.async_patch_settings(agentcli_tool_routing_mode='advise')
    with patch.object(DecisionProvider, 'select_tool', AsyncMock()) as choose:
        assert (await review(hass, home)).reason == 'unqualified'
    choose.assert_not_called()


@pytest.mark.parametrize('change', ['revoke', 'scope', 'kill', 'account', 'catalog', 'context'])
async def test_wait_rechecks(hass, home, change):
    data, token, store, account = home
    messages = history()
    async def choose(*args):
        if change == 'revoke':
            await data.store.async_archive_token(token.id, revoked=True)
        elif change == 'scope':
            token.settings_version += 1
        elif change == 'kill':
            await data.store.async_patch_settings(kill_switch=True)
        elif change == 'account':
            await store.delete(account)
        elif change == 'catalog':
            agentcli.build_mcp_tool_list.return_value.pop()
        else:
            messages[0]['content'] = 'Changed'
        return {'tool': 'get_history', 'model': 'test-model'}
    with patch.object(DecisionProvider, 'select_tool', choose):
        assert (await review(hass, home, messages=messages)).action == 'stop'


@pytest.mark.parametrize('bad', [
    'api_key=private-test-token', '{"api_key":"hidden"}', 'light.hidden', 'private-test-key', 'x' * 17000])
async def test_private_or_oversized_context_is_not_shortened(hass, home, bad):
    messages = history()
    messages[0]['content'] = bad
    with patch.object(DecisionProvider, 'select_tool', AsyncMock()) as choose:
        result = await review(hass, home, messages=messages)
    assert result.reason == 'context_unavailable'
    choose.assert_not_called()


@pytest.mark.parametrize('error', ['malformed', 'low_probability', 'request_budget_exhausted', 'timeout'])
async def test_advisory_failure_preserves_planner(hass, home, error):
    with patch.object(DecisionProvider, 'select_tool', AsyncMock(side_effect=DecisionError(error))):
        assert (await review(hass, home)).action == 'continue'


async def test_turn_bounds_and_cancel(hass, home, monkeypatch):
    qualify(monkeypatch)
    data, token, _, _ = home
    turn = tool_routing.create_review_state(data, token, 'chat')
    with patch.object(DecisionProvider, 'select_tool', AsyncMock(return_value={'tool': 'get_state', 'model': 'test-model'})) as choose:
        for _ in range(3):
            await review(hass, home, turn=turn)
        assert choose.await_count == 2
        assert (await review(hass, home, remaining_rounds=0)).reason == 'last_round'
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def slow(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    cancel = asyncio.Event()
    with patch.object(DecisionProvider, 'select_tool', slow):
        task = asyncio.create_task(review(hass, home, cancel=cancel))
        await entered.wait()
        assert (await review(hass, home)).reason == 'busy'
        cancel.set()
        assert (await task).action == 'stop'
    assert cancelled.is_set() and data.decision_shadow.task is None


@pytest.mark.parametrize('capacity', [2, 3, 255])
async def test_provider_groups_keep_complete_catalog_and_declines(capacity):
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'm', 'https://api.typesafe.ai/v1'), 'systemone')
    state = {'catalog': {f't{i}': t for i, t in enumerate(CATALOG)}}
    async def request(session, method, path, timeout, payload):
        assert payload['state'] == state
        answers = {}
        for q, spec in payload['questions'].items():
            assert len(spec['criteria']) <= capacity
            choice = 't1' if 't1' in spec['criteria'] else 'none'
            answers[q] = {'type': 'choice', 'choice': choice, 'confidence': 1,
                          'probabilities': {k: int(k == choice) for k in spec['criteria']}}
        return {'model': 'm', 'answers': answers}
    with patch.object(provider, 'target_choice_limit', AsyncMock(return_value=capacity)), patch.object(provider, '_request', request):
        assert (await provider.select_tool(None, state, 1000))['tool'] == 'get_history'


def test_settings_roundtrip_and_untrusted_values():
    raw = {key: 'observe' for key in TOOL_ROUTING_SETTINGS.values()}
    settings = GlobalSettings.from_dict(raw)
    assert all(settings.to_dict()[key] == 'observe' for key in raw)
    assert all(getattr(GlobalSettings.from_dict({key: []}), key) == 'off' for key in raw)


@pytest.mark.parametrize('surface', ['chat', 'voice', 'ai_task'])
@pytest.mark.parametrize('dialect', ['claude', 'openai'])
@pytest.mark.parametrize('next_choice', ['fresh', 'original', 'provider_error'])
async def test_loops_withhold_whole_batch_then_dispatch_fresh_arguments(hass, home, monkeypatch, surface, dialect, next_choice):
    qualify(monkeypatch)
    data, token, _, _ = home
    await data.store.async_patch_settings(**{TOOL_ROUTING_SETTINGS[surface]: 'advise'})
    provider = (agentcli.ClaudeProvider if dialect == 'claude' else agentcli.OpenAICompatProvider)(
        agentcli.ProviderConfig('claude' if dialect == 'claude' else 'chatgpt', 'model', 'https://unused'))
    rounds = []
    new_calls = [{'id': 'fresh', 'name': 'get_state' if next_choice == 'original' else 'get_history', 'input': {'hours': 12}}]
    async def stream(*args, **kwargs):
        index = len(rounds)
        rounds.append(deepcopy(kwargs['messages']))
        if index == 1:
            assert not kwargs['dispatch_started']
            if next_choice == 'provider_error':
                yield {'type': agentcli.EV_ERROR, 'message': 'Provider unavailable', 'code': 'provider_error'}
                return
        if index < 2:
            calls = CALLS if index == 0 else new_calls
            for call in calls:
                yield {'type': agentcli.EV_TOOL, **call}
            yield {'type': agentcli.EV_DONE, 'stop_reason': 'tool_use', 'assistant_msg': history(calls, dialect)[-1]}
        else:
            yield {'type': agentcli.EV_TEXT, 'text': '{"ok":true}'}
            yield {'type': agentcli.EV_DONE, 'stop_reason': 'end_turn', 'assistant_msg': {'role': 'assistant', 'content': '{"ok":true}'}}
    dispatch = AsyncMock(return_value=({'result': {'content': [{'type': 'text', 'text': 'read'}]}}, 'tools/call', '', 'allowed'))
    with patch.object(DecisionProvider, 'select_tool', AsyncMock(return_value={'tool': 'get_history', 'model': 'test-model'})) as choose, \
            patch.object(agentcli, '_stream_turn_resilient', stream), patch.object(agentcli, 'dispatch_agent_tool', dispatch), \
            patch.object(agentcli, '_agentcli_time_context', return_value=''), \
            patch('custom_components.phoenix_mcp.mcp_view._build_instructions', return_value='Respect restrictions.'):
        if surface == 'chat':
            await agentcli.async_run_agent_turn(hass=hass, data=data, token=token, provider=provider, session=None,
                messages=history()[:1], options={}, client_ip='test', base_url='', emit=AsyncMock(), cancel=asyncio.Event(),
                routing_language='en', max_iterations=3)
        else:
            result = await agentcli._run_headless_turn(hass, data, token, provider, None, '', system='Respect restrictions.',
                seed=history()[0]['content'], client_ip='test', max_iterations=3, routing_surface=surface, routing_language='en')
            if next_choice == 'provider_error':
                assert result.outcome == agentcli.TURN_PROVIDER_ERROR
            else:
                assert result.text == '{"ok":true}'
    assert choose.await_count == 1
    assert dispatch.await_count == (0 if next_choice == 'provider_error' else 1)
    if next_choice != 'provider_error':
        assert dispatch.call_args.kwargs['name'] == new_calls[0]['name']
        assert dispatch.call_args.kwargs['arguments'] == {'hours': 12}
    assert 'Not executed' in json.dumps(rounds[1])
    assert 'first' in json.dumps(rounds[1]) and 'second' in json.dumps(rounds[1])
    assert not data.agent_recovery.blocks(token.id)


async def test_review_uses_exact_surface_catalog_and_rejects_new_announcements(hass, home):
    data, token, _, _ = home
    agentcli.build_mcp_tool_list.return_value.append({
        'name': 'llm__GetDateTime', 'description': 'Current local time', 'inputSchema': {'type': 'object'}})
    announced = agentcli.build_agent_tool_list(token, data, 'chat')
    assert len(announced) == 3
    assert len(agentcli.build_agent_tool_list(token, data, 'voice')) == 4
    with patch.object(DecisionProvider, 'select_tool', AsyncMock(return_value={'tool': 'get_state', 'model': 'test-model'})) as choose:
        assert (await review(hass, home, announced_tools=announced)).reason == 'agreement'
        assert list(choose.call_args.args[1]['catalog'].values()) == announced
        agentcli.build_mcp_tool_list.return_value.append({'name': 'new_tool'})
        assert (await review(hass, home, announced_tools=announced)).action == 'stop'
        assert choose.await_count == 1


@pytest.mark.parametrize('content', [
    [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'opaque'}}],
    [{'type': 'image_url', 'image_url': {'url': 'https://invalid/image.png'}}],
])
async def test_multimodal_context_falls_back_without_sending(hass, home, content):
    messages = history()
    messages[0]['content'] = content
    with patch.object(DecisionProvider, 'select_tool', AsyncMock()) as choose:
        assert (await review(hass, home, messages=messages)).reason == 'context_unavailable'
    choose.assert_not_called()


@pytest.mark.parametrize('case,reason', [('tie', 'low_probability'), ('low_decline', 'low_probability'),
    ('multiple', 'category_mismatch'), ('unknown', 'malformed'), ('missing', 'malformed'),
    ('total', 'malformed'), ('overflow', 'catalog_unavailable')])
async def test_provider_refuses_incomplete_or_unsound_choices(case, reason):
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'm', 'https://api.typesafe.ai/v1'), 'systemone')
    state = {'catalog': {f't{i}': t for i, t in enumerate(CATALOG)}}
    if case == 'overflow':
        state['extra'] = 'x' * 262145
    async def request(session, method, path, timeout, payload):
        answers = {}
        for i, (q, spec) in enumerate(payload['questions'].items()):
            choice = f't{i}' if i == 0 or case == 'multiple' else 'none'
            probabilities = {k: int(k == choice) for k in spec['criteria']}
            if case in ('tie', 'low_decline') and i == 1:
                probability = .5 if case == 'tie' else .89
                probabilities = {f't{i}': 1-probability, 'none': probability}
            if case == 'total':
                probabilities = {k: 1 for k in probabilities}
            answers[q] = {'type': 'choice', 'choice': 'unknown' if case == 'unknown' else choice,
                          'confidence': 1, 'probabilities': probabilities}
        if case == 'missing':
            answers.pop('tool_1')
        return {'model': 'm', 'answers': answers}
    with patch.object(provider, 'target_choice_limit', AsyncMock(return_value=2)), patch.object(provider, '_request', request):
        with pytest.raises(DecisionError, match=f'^{reason}$'):
            await provider.select_tool(None, state, 1000)


async def test_post_reservation_guard_stops_http(hass, home):
    data, token, _, _ = home
    async def reserve(limit):
        token.settings_version += 1
    session = MagicMock()
    with patch.object(data.decision_budget, 'reserve', reserve), \
            patch('custom_components.phoenix_mcp.tool_routing.async_get_clientsession', return_value=session):
        assert (await review(hass, home)).action == 'stop'
    session.request.assert_not_called()


async def test_one_correction_even_if_planner_disagrees(hass, home, monkeypatch):
    qualify(monkeypatch)
    data, token, _, _ = home
    await data.store.async_patch_settings(agentcli_tool_routing_mode='advise')
    turn = tool_routing.create_review_state(data, token, 'chat')
    with patch.object(DecisionProvider, 'select_tool', AsyncMock(return_value={'tool': 'get_history', 'model': 'test-model'})) as choose:
        assert (await review(hass, home, turn=turn)).action == 'replan'
        assert (await review(hass, home, turn=turn)).reason == 'limit'
    assert choose.await_count == 1


async def test_returned_model_must_also_be_qualified(hass, home, monkeypatch):
    qualify(monkeypatch)
    await home[0].store.async_patch_settings(agentcli_tool_routing_mode='advise')
    with patch.object(DecisionProvider, 'select_tool', AsyncMock(return_value={'tool': 'get_history', 'model': 'new-model'})):
        assert (await review(hass, home)).reason == 'unqualified'


@pytest.mark.parametrize('surface', list(TOOL_ROUTING_SETTINGS))
async def test_settings_validation_and_qualification(hass, home, surface):
    from custom_components.phoenix_mcp.admin_view import _validated_settings_patch
    key = TOOL_ROUTING_SETTINGS[surface]
    valid, error = await _validated_settings_patch({key: 'observe'}, home[0], hass, 'test')
    assert error is None and valid[key] == 'observe'
    _, error = await _validated_settings_patch({key: 'advise'}, home[0], hass, 'test')
    assert error is not None and error.status == 400
    _, error = await _validated_settings_patch({key: []}, home[0], hass, 'test')
    assert error is not None and error.status == 400


def test_telemetry_cannot_retain_private_values():
    from custom_components.phoenix_mcp.decision_shadow import DecisionShadow
    runtime = DecisionShadow()
    runtime.observe_tool_review('chat', 'private-key', 1)
    runtime.observe_tool_review('private-surface', 'agreement', 1)
    assert runtime.summary()['tool_routing'] == {'chat': {'reviews': 1, 'reasons': {'unavailable': 1}, 'elapsed_ms': 1.0}}
    result = runtime.summary()
    result['tool_routing']['chat']['reasons']['private'] = 1
    assert 'private' not in repr(runtime.summary())


@pytest.mark.parametrize('surface', ['chat', 'voice', 'ai_task'])
def test_companion_cannot_admit_decision_review(home, surface):
    data, token, _, _ = home
    token.persona = 'home_companion'
    with patch.object(tool_routing.RoutingAuthority, 'capture', side_effect=AssertionError('Must not capture review authority')):
        state = tool_routing.create_review_state(data, token, surface)
    assert state.authority is None
