"""Exercise decision wire contracts and fail-closed account isolation."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.phoenix_mcp import agentcli
from custom_components.phoenix_mcp.admin_view import _validated_settings_patch
from custom_components.phoenix_mcp.const import DECISION_ROUTE_CHOICES, DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider, parse_choice
from custom_components.phoenix_mcp.decision_shadow import DecisionShadow, async_preview_decision


@pytest.mark.parametrize("chat", [False, True])
def test_hybrid_counters_keep_only_allowlisted_reasons_and_reset_with_runtime(chat):
    key = "chat_hybrid" if chat else "hybrid"
    shadow = DecisionShadow()
    private = {'entity_id': 'light.private', 'utterance': 'private command', 'api_key': 'private-key'}
    for index in range(100):
        shadow.observe_hybrid({'reason': f'private-key-{index}', 'decision': private}, chat=chat)
    shadow.observe_hybrid({'reason': 'observed', '_plan': private}, chat=chat)
    shadow.observe_hybrid({'reason': 'observed', '_plan': private}, authority_changed=True, chat=chat)
    summary = shadow.summary()
    assert summary[key] == {'evaluations': 102, 'reasons': {
        'unavailable': 100, 'plan_ready': 1, 'authority_changed': 1,
    }}
    assert 'private' not in repr(shadow)
    summary[key]['reasons']['injected'] = 1
    assert 'injected' not in shadow.summary()[key]['reasons']
    assert DecisionShadow().summary()[key] == {'evaluations': 0, 'reasons': {}}


def response():
    return {'model':'jev-1.13.0', 'answers':{'route':{'type':'choice','choice':'light_on',
        'confidence':0.8,'probabilities':{k:0.92 if k=='light_on' else 0.02 for k in DECISION_ROUTE_CHOICES}}}}


@pytest.fixture
async def decision_home(hass, access_key_store):
    access_key, _ = await access_key_store.async_create_access_key('decision_test','test')
    data = PhoenixData(store=access_key_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add('typesafe', {'api_key':'test-secret','model':'jev-latest'})
    await access_key_store.async_patch_settings(decision_provider_id=account)
    with patch.object(agentcli, '_get_secret_store', AsyncMock(return_value=store)):
        yield data, access_key, store, account
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


@pytest.mark.parametrize('protocol', ['systemone'])
async def test_real_http_contract(protocol, socket_enabled):
    requests=[]
    async def handler(request):
        requests.append((request.path, request.headers.get('Authorization'),await request.json()))
        return web.json_response(response())
    app=web.Application()
    app.router.add_post('/v1/'+protocol, handler)
    async def models(request):
        return web.json_response({'models':[{'name':'jev-latest'}]})
    app.router.add_get('/v1/models', models)
    async with TestServer(app) as server, ClientSession() as session:
        cfg=agentcli.ProviderConfig('typesafe','jev-latest',str(server.make_url('/v1')),'test-secret')
        provider=DecisionProvider(cfg,protocol)
        assert await provider.validate(session)==(True,'')
        assert await provider.list_models(session)==['jev-latest']
        answer=await provider.classify(session,'Switch the lamp on',1000)
    assert answer['choice']=='light_on'
    assert requests[0][0]=='/v1/'+protocol
    assert requests[0][1]=='Bearer test-secret'
    payload=requests[0][2]
    assert payload['state']=={'utterance':'Switch the lamp on','language':'en'}
    assert set(payload)=={'model','state','questions'}
    assert set(payload['questions']['route']['criteria'])==set(DECISION_ROUTE_CHOICES)


@pytest.mark.parametrize('status,reason', [(302,'http_error'),(401,'authentication'),(403,'authentication'),
                                        (429,'rate_limited'),(529,'rate_limited'),(500,'http_error')])
async def test_http_failures_never_follow_redirects_retry_or_echo_bodies(status,reason,socket_enabled):
    count=0
    async def handler(request):
        nonlocal count
        count+=1
        return web.Response(status=status,text='private prompt and test-secret',headers={'Location':'/must-not-follow'})
    app=web.Application(); app.router.add_post('/systemone',handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg=agentcli.ProviderConfig('typesafe','m',str(server.make_url('')).rstrip('/'))
        with pytest.raises(DecisionError,match='^'+reason+'$'):
            await DecisionProvider(cfg,'systemone').classify(session,'command',1000)
    assert count==1


@pytest.mark.parametrize('body,reason', [
    ('{"answers":{},"answers":{}}','malformed'), ('[]','malformed'),
    ('{"answers":'+ '['*2000,'malformed'), ('x'*65537,'response_too_large'),
], ids=['duplicate_keys','array','deep_json','oversized'])
async def test_body_bounds_and_duplicate_keys(body,reason,socket_enabled):
    async def handler(request):
        return web.Response(text=body)
    app=web.Application(); app.router.add_post('/systemone',handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg=agentcli.ProviderConfig('typesafe','m',str(server.make_url('')).rstrip('/'))
        with pytest.raises(DecisionError,match='^'+reason+'$'):
            await DecisionProvider(cfg,'systemone').classify(session,'command',1000)


@pytest.mark.parametrize('change', ['missing','unknown','wrong_type','nan','bool','negative','sum','not_max','confidence','extra','model'])
@pytest.mark.parametrize('typesafe', [False, True])
def test_malformed_answers_have_no_decision(change, typesafe):
    body=response();a=body['answers']['route']
    if change=='missing':del a['probabilities']['provider']
    if change=='unknown':a['choice']='call_service'
    if change=='wrong_type':a['type']='noul'
    if change=='nan':a['probabilities']['provider']=float('nan')
    if change=='bool':a['confidence']=True
    if change=='negative':a['probabilities']['provider']=-1
    if change=='sum':a['probabilities']['provider']=0.9
    if change=='not_max':a['choice']='provider'
    if change=='confidence':a['confidence']=2
    if change=='extra':body['answers']['extra']=copy.deepcopy(a)
    if change=='model':body['model']='private output\n'
    from custom_components.phoenix_mcp.const import DECISION_TYPESAFE_BASE_URL
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL),
                                'systemone')
    parser = provider._parse_choice if typesafe else parse_choice
    with pytest.raises(DecisionError):
        parser(body, DECISION_ROUTE_CHOICES)


async def test_decision_accounts_are_secret_free_and_cannot_resolve_for_conversation(decision_home):
    data, access_key, store, account=decision_home
    assert store.resolve(account) is None
    cfg=store.resolve(account,purpose='decision')
    assert cfg.kind=='typesafe'
    with pytest.raises(ValueError):agentcli.build_provider(cfg)
    assert 'test-secret' not in json.dumps(store.list_instances())
    assert not any(i['kind']=='typesafe' for i in agentcli._provider_catalog())
    assert {i['kind'] for i in agentcli._provider_catalog('decision')}=={'typesafe'}


@pytest.mark.parametrize('field',['voice_agent_provider_id','ai_task_provider_id'])
async def test_backend_rejects_decision_account_surface_binding(hass,decision_home,field):
    data,access_key,store,account=decision_home
    _,error=await _validated_settings_patch({field:account},data,hass,'rid')
    assert error is not None and error.status==400


async def test_only_decision_accounts_can_be_selected(hass,decision_home):
    data,access_key,store,account=decision_home
    chat=await store.add('deepseek',{'api_key':'chat','model':'m'})
    _,error=await _validated_settings_patch({'decision_provider_id':chat},data,hass,'rid')
    assert error is not None and error.status==400
    changes,error=await _validated_settings_patch({'decision_provider_id':account,'decision_shadow_enabled':True},data,hass,'rid')
    assert error is None and changes['decision_shadow_enabled']
    changes,error=await _validated_settings_patch({'decision_provider_id':None},data,hass,'rid')
    assert error is None and changes['decision_shadow_enabled'] is False


@pytest.mark.parametrize('budget',[True,0,249,30001,'1000',None])
async def test_decision_budget_is_separate_and_strict(hass,decision_home,budget):
    data,*_=decision_home
    _,error=await _validated_settings_patch({'decision_timeout_ms':budget},data,hass,'rid')
    assert error is not None


async def test_manual_observation_cannot_execute_or_create_approval(hass,decision_home):
    data,access_key,*_=decision_home
    with (patch.object(DecisionProvider,'classify',AsyncMock(return_value=parse_choice(response(),DECISION_ROUTE_CHOICES))) as classify,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result=await async_preview_decision(hass,data,access_key,'Switch the lamp on','en',{'reason':'no_match','proposal':None})
    assert result['reason']=='observed' and result['executed'] is False
    classify.assert_awaited_once();dispatch.assert_not_called()
    assert data.store.get_pending_approvals()==[]
    assert data.decision_shadow.summary()['observations']==0
    assert data.decision_shadow.summary()['hybrid']=={'evaluations':0,'reasons':{}}


@pytest.mark.parametrize('reason,proposal',[('supported',{'intent':'HassTurnOn'}),('sentence_trigger',None),
                                           ('timeout',None),('busy',None),('authority_changed',None)])
async def test_local_matches_and_unsafe_recognition_failures_skip_remote(hass,decision_home,reason,proposal):
    data,access_key,*_=decision_home
    with patch.object(DecisionProvider,'classify',AsyncMock()) as classify:
        result=await async_preview_decision(hass,data,access_key,'command','en',{'reason':reason,'proposal':proposal})
    assert result['decision'] is None
    classify.assert_not_called()


@pytest.mark.parametrize('change',['token','settings','account_model','account_deleted','runtime'])
async def test_changes_during_network_wait_discard_decision(hass,decision_home,change):
    data,access_key,store,account=decision_home
    entered=asyncio.Event();release=asyncio.Event()
    async def classify(*args):
        entered.set();await release.wait();return parse_choice(response(),DECISION_ROUTE_CHOICES)
    with patch.object(DecisionProvider,'classify',classify):
        task=data.decision_shadow.start(hass,data,access_key,'command','en',observe=True)
        await entered.wait()
        if change=='token':access_key.revoked=True
        if change=='settings':await data.store.async_patch_settings(decision_timeout_ms=1234)
        if change=='account_model':await store.set_model(account,'changed')
        if change=='account_deleted':await store.delete(account)
        if change=='runtime':data.shutting_down=True
        release.set();result=await task
    assert result['reason']=='authority_changed' and result['decision'] is None
    assert not data.decision_shadow.choices


async def test_single_flight_cancellation_releases_slot_without_retaining_text(hass,decision_home):
    data,access_key,*_=decision_home
    entered=asyncio.Event();closed=asyncio.Event()
    async def classify(*args):
        entered.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    with patch.object(DecisionProvider,'classify',classify):
        task=data.decision_shadow.start(hass,data,access_key,'private utterance','en',observe=True)
        await entered.wait()
        assert data.decision_shadow.start(hass,data,access_key,'second','en') is None
        data.decision_shadow.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    await closed.wait()
    assert data.decision_shadow.task is None
    assert 'private utterance' not in repr(data.decision_shadow)


@pytest.mark.parametrize("language", ["fr", "zh-Hans", "es"])
async def test_non_english_never_sends(hass,decision_home,language):
    data,access_key,*_=decision_home
    with patch.object(DecisionProvider,'classify',AsyncMock()) as classify:
        result=await data.decision_shadow.start(hass,data,access_key,'allume',language)
    assert result['reason']=='unsupported_language'
    classify.assert_not_called()


async def test_real_http_timeout_is_bounded_and_not_retried(socket_enabled):
    entered=asyncio.Event();release=asyncio.Event();calls=0
    async def handler(request):
        nonlocal calls
        calls+=1;entered.set();await release.wait()
        return web.json_response(response())
    app=web.Application();app.router.add_post('/systemone',handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg=agentcli.ProviderConfig('typesafe','m',str(server.make_url('')).rstrip('/'))
        try:
            with pytest.raises((TimeoutError,DecisionError)):
                await DecisionProvider(cfg,'systemone').classify(session,'command',20)
        finally:release.set()
    assert entered.is_set() and calls==1


@pytest.mark.parametrize('local_reason,expected', [('supported',0),('no_match',1),('timeout',0),('sentence_trigger',0)])
async def test_voice_shadow_never_waits_for_decision_or_replaces_provider(hass,decision_home,local_reason,expected):
    from custom_components.phoenix_mcp import voice_agent
    data,access_key,store,account=decision_home
    chat=await store.add('deepseek',{'api_key':'chat','model':'model'})
    await data.store.async_patch_settings(voice_agent_enabled=True,voice_agent_access_key_id=access_key.id,
        voice_agent_provider_id=chat,voice_agent_model='model',voice_agent_routing_mode='local_shadow',
        decision_shadow_enabled=True)
    entered=asyncio.Event();release=asyncio.Event()
    async def classify(*args):
        entered.set();await release.wait();return parse_choice(response(),DECISION_ROUTE_CHOICES)
    local={'reason':local_reason,'proposal':{'intent':'HassTurnOn'} if local_reason=='supported' else None,
           'route':'local_candidate' if local_reason=='supported' else 'provider','elapsed_ms':1,'executed':False}
    ordinary=AsyncMock(return_value='ordinary answer')
    with (patch.object(DecisionProvider,'classify',classify),
          patch('custom_components.phoenix_mcp.command_routing.async_evaluate_command',AsyncMock(return_value=local)),
          patch.object(agentcli,'build_provider',return_value=MagicMock()),
          patch.object(agentcli,'async_run_voice_turn',ordinary),
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        async with asyncio.timeout(1):
            reply=await voice_agent.async_voice_answer(hass,data,'private utterance',language='en')
        assert reply=='ordinary answer' and not release.is_set()
        ordinary.assert_awaited_once()
        if expected:
            await entered.wait()
            pending=data.decision_shadow.task
            release.set();await pending
        else:assert data.decision_shadow.task is None
        dispatch.assert_not_called()
    assert data.decision_shadow.observations==expected
    assert 'private utterance' not in json.dumps(data.decision_shadow.summary())


@pytest.mark.parametrize('language,expected', [('zh-Hans', 'zh-CN'), ('es', 'es')])
async def test_multilingual_decision_preview_sends_language_without_execution(hass, decision_home, language, expected):
    data, access_key, *_ = decision_home
    with (patch.object(DecisionProvider, '_request', AsyncMock(return_value=response())) as request,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result = await async_preview_decision(hass, data, access_key, '打开台灯', language,
                                              {'reason': 'no_match', 'proposal': None}, ingress='preview')
    assert result['reason'] == 'observed' and result['executed'] is False
    assert '_plan' not in result
    payload = request.call_args.args[4]
    assert payload['state'] == {'utterance': '打开台灯', 'language': expected}
    assert 'English' not in payload['questions']['route']['instructions']
    dispatch.assert_not_called()
    assert data.store.get_pending_approvals() == []
    assert data.decision_shadow.summary()['observations'] == 0


async def test_preview_ingress_cannot_prepare_plan_even_with_hybrid_enabled(hass, decision_home):
    data, access_key, *_ = decision_home
    await data.store.async_patch_settings(agentcli_routing_mode='hybrid')
    with patch.object(DecisionProvider, '_request', AsyncMock()) as request:
        result = await async_preview_decision(hass, data, access_key, '打开台灯', 'zh-Hans',
                                              {'reason': 'no_match', 'proposal': None},
                                              targets=True, prepare=True, ingress='preview')
    assert result['reason'] == 'not_enabled' and '_plan' not in result
    request.assert_not_called()


@pytest.mark.parametrize('change,check', [
    ('selected', 'selected_choice'), ('keys', 'probability_keys'), ('values', 'probability_values'),
    ('confidence', 'confidence'), ('total', 'probability_total'), ('highest', 'selected_not_highest'),
    ('model', 'model_identifier'),
])
def test_validation_diagnostics_preserve_strict_checks_without_response_data(change, check):
    from custom_components.phoenix_mcp.decision_provider import DecisionValidationError
    body = response()
    answer = body['answers']['route']
    if change == 'selected': answer['choice'] = 'private-provider-text'
    elif change == 'keys': answer['probabilities']['private-provider-text'] = 0
    elif change == 'values': answer['probabilities']['light_on'] = 'private-provider-text'
    elif change == 'confidence': answer['confidence'] = 'private-provider-text'
    elif change == 'total': answer['probabilities']['light_on'] = 0.6
    elif change == 'highest': answer['choice'] = 'provider'
    else: body['model'] = 'private provider text'
    with pytest.raises(DecisionValidationError, match='^malformed$') as error:
        parse_choice(body, DECISION_ROUTE_CHOICES)
    assert error.value.check == check
    assert 'private' not in str(error.value) + repr(vars(error.value))


@pytest.mark.parametrize('view_class,method', [
    (agentcli.PhoenixAgentCliModelsView, 'get'), (agentcli.PhoenixAgentCliRefreshView, 'post'),
])
@pytest.mark.parametrize('reason', ['unavailable', 'timeout', 'authentication'])
async def test_offline_decision_catalog_is_a_handled_error_without_account_changes(hass, decision_home, view_class, method, reason):
    _, _, store, account = decision_home
    before = copy.deepcopy(store.get(account))
    view = MagicMock(hass=hass)
    with patch.object(DecisionProvider, 'list_models', side_effect=DecisionError(reason)):
        response = await getattr(view_class, method).__wrapped__(view, {'phoenix_mcp_rid': 'test'}, account)
    assert response.status == 400
    body = json.loads(response.text)
    assert body['message_key'].endswith('providerConnectionFailedNoDetail')
    assert 'test-secret' not in response.text
    assert store.get(account) == before


@pytest.mark.parametrize('question', ['route', 'target', 'percentage', 'summary_scope', 'private-question'])
def test_probability_total_diagnostic_contains_only_allowlisted_aggregates(question):
    from custom_components.phoenix_mcp.decision_provider import DecisionValidationError
    body = {'model': 'jev-1.13.0', 'answers': {question: {
        'type': 'choice', 'choice': 'private-choice', 'confidence': 0.95,
        'probabilities': {'private-choice': 0.95, 'none': 0.1},
    }}}
    with pytest.raises(DecisionValidationError) as error:
        parse_choice(body, {'private-choice': 'private-description', 'none': 'Decline'}, question)
    assert error.value.check == 'probability_total'
    expected = {} if question == 'private-question' else {
        'question': question, 'choice_count': 2, 'probability_sum': 1.05,
    }
    assert error.value.diagnostics == expected
    assert 'private' not in repr(vars(error.value))


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, 2, 'private-value'])
def test_invalid_probability_values_never_expose_aggregate_diagnostics(bad):
    from custom_components.phoenix_mcp.decision_provider import DecisionValidationError
    body = response()
    body['answers']['route']['probabilities']['light_on'] = bad
    with pytest.raises(DecisionValidationError) as error:
        parse_choice(body, DECISION_ROUTE_CHOICES)
    assert error.value.check == 'probability_values'
    assert error.value.diagnostics == {}


@pytest.mark.parametrize('kind,base_url,protocol,allowed', [
    ('typesafe', 'https://api.typesafe.ai/v1', 'systemone', True),
    ('typesafe', 'https://other.example/v1', 'systemone', False),
])
async def test_rounding_is_applied_only_to_known_typesafe_endpoint(kind, base_url, protocol, allowed):
    provider = DecisionProvider(agentcli.ProviderConfig(kind, 'jev-latest', base_url), protocol)
    body = response()
    probabilities = {key: 0.95 if key == 'light_on' else 0.01 for key in DECISION_ROUTE_CHOICES}
    body['answers']['route']['probabilities'] = probabilities
    with patch.object(provider, '_request', AsyncMock(return_value=body)):
        if not allowed:
            with pytest.raises(DecisionError, match='malformed'):
                await provider.classify(MagicMock(), 'Turn on a light', 1000)
        else:
            result = await provider.classify(MagicMock(), 'Turn on a light', 1000)
            assert result['probabilities'] == probabilities
            assert sum(result['probabilities'].values()) == pytest.approx(0.99)


@pytest.mark.parametrize('values,valid,confident', [
    ([0.95, 0.04, 0], True, True),
    ([0.95, 0.06, 0], True, True),
    ([0.5, 0.49], True, False),
    ([0.89, 0.1, 0], True, False),
    ([0.5, 0.5, 0], True, False),
    ([0.51, 0.51, 0], False, False),
    ([0.95, 0.01, 0], False, False),
    ([0.95] + [0.01] * 26, False, False),
])
def test_typesafe_rounding_bounds_preserve_raw_probabilities_and_threshold(values, valid, confident):
    from custom_components.phoenix_mcp.const import DECISION_TYPESAFE_BASE_URL
    from custom_components.phoenix_mcp.decision_provider import confident_choice
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL),
                                'systemone')
    probabilities = {str(index): value for index, value in enumerate(values)}
    choices = {key: key for key in probabilities}
    body = {'model': 'jev-1.13.0', 'answers': {'route': {
        'type': 'choice', 'choice': '0', 'confidence': 1, 'probabilities': probabilities,
    }}}
    if not valid:
        with pytest.raises(DecisionError, match='malformed'):
            provider._parse_choice(body, choices)
    else:
        result = provider._parse_choice(body, choices)
        assert result['probabilities'] == probabilities
        assert confident_choice(result) is confident


@pytest.mark.parametrize('selection', ['target', 'summary_scope'])
@pytest.mark.parametrize('case', ['last_group', 'none', 'conflict', 'uncertain_decline', 'missing', 'unknown'])
async def test_grouped_choices_require_complete_unambiguous_global_selection(selection, case):
    from custom_components.phoenix_mcp.const import DECISION_TYPESAFE_BASE_URL
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL),
                                'systemone')
    targets = {f't{i}': {'entity_id': f'light.test_{i}', 'name': 'Duplicate lamp' if i in (0, 254) else f'Lamp {i}',
                        'eligible_categories': ['light_on']} for i in range(255)} if selection == 'target' else {}
    scopes = {f's{i}': {'summary_scope': {'domain': 'light', 'device_class': '', 'area_id': str(i)},
                       'area': f'Room {i}', 'area_aliases': [], 'states': ['on', 'off']}
              for i in range(255)} if selection == 'summary_scope' else None
    async def reply(*args):
        payload = args[4]
        first, second = payload['questions'][selection], payload['questions'][selection + '_2']
        expected = set(targets if selection == 'target' else scopes)
        assert set(first['criteria']) & set(second['criteria']) == {'none'}
        assert (set(first['criteria']) | set(second['criteria'])) - {'none'} == expected
        assert len(first['criteria']) == 255 and len(second['criteria']) == 2
        assert len(payload['state']['targets'] if selection == 'target' else payload['state']['summary_scopes']) == 255
        chosen = ('t' if selection == 'target' else 's') + '254'
        answers = {}
        for question, spec in payload['questions'].items():
            key = 'none'
            if question == 'route':
                key = 'light_on' if selection == 'target' else 'state_summary'
            if question == 'summary' and selection == 'summary_scope':
                key = 'total'
            if question == selection + '_2' and case != 'none':
                key = chosen
            if question == selection and case == 'conflict':
                key = next(key for key in spec['criteria'] if key != 'none')
            answers[question] = {'type': 'choice', 'choice': key, 'confidence': 1,
                                 'probabilities': {k: float(k == key) for k in spec['criteria']}}
        if case == 'uncertain_decline':
            p = answers[selection]['probabilities']
            p['none'] = 0.89
            p[next(k for k in p if k != 'none')] = 0.11
        if case == 'missing':
            del answers[selection + '_2']
        if case == 'unknown':
            answers[selection + '_2']['choice'] = 'not_in_catalog'
        return {'model': 'jev-1.13.0', 'answers': answers}
    with patch.object(provider, '_request', side_effect=reply) as request:
        if case in ('last_group', 'none'):
            result = await provider.route_and_target(None, 'Test request', targets, 5000, summary_scopes=scopes)
            assert result[selection]['choice'] == ('none' if case == 'none' else ('t' if selection == 'target' else 's') + '254')
            assert not any(key.endswith('_2') for key in result)
        else:
            with pytest.raises(DecisionError):
                await provider.route_and_target(None, 'Test request', targets, 5000, summary_scopes=scopes)
        request.assert_awaited_once()


async def test_grouped_choice_limit_refuses_before_provider_request():
    from custom_components.phoenix_mcp.const import DECISION_TYPESAFE_BASE_URL, DECISION_GROUPED_MAX_TARGETS
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL),
                                'systemone')
    targets = {f't{i}': {'entity_id': f'light.test_{i}', 'eligible_categories': ['light_on']}
               for i in range(DECISION_GROUPED_MAX_TARGETS + 1)}
    with patch.object(provider, '_request') as request:
        with pytest.raises(DecisionError, match='too_many_targets'):
            await provider.route_and_target(None, 'Turn on a lamp', targets, 5000)
    request.assert_not_called()


@pytest.mark.parametrize('kind', ['entity', 'collection'])
@pytest.mark.parametrize('failure', [None, 'authority', 'uncertain', 'declined', 'model_changed'])
async def test_oversized_catalog_kind_selection_keeps_complete_relevant_catalog(kind, failure):
    from custom_components.phoenix_mcp.const import DECISION_TYPESAFE_BASE_URL
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL),
                                'systemone')
    targets = {f't{i}': {'entity_id': f'light.lamp_{i}', 'eligible_categories': ['light_on']} for i in range(2)}
    scopes = {f's{i}': {'summary_scope': {'domain': 'light', 'device_class': '', 'area_id': str(i)},
                       'area': f'Room {i}', 'area_aliases': [], 'states': ['on', 'off']} for i in range(2)}
    revalidate = MagicMock(side_effect=DecisionError('authority_changed') if failure == 'authority' else None)
    async def reply(*args):
        payload = args[4]
        first = 'catalog' in payload['questions']
        if not first:
            assert payload['state']['targets'] == (targets if kind == 'entity' else {})
            assert payload['state'].get('summary_scopes') == (scopes if kind == 'collection' else None)
        answers = {}
        for question, spec in payload['questions'].items():
            selected = {'catalog': kind, 'route': 'light_on' if kind == 'entity' else 'state_summary',
                        'target': 't1' if kind == 'entity' else 'none',
                        'summary_scope': 's1', 'summary': 'on'}.get(question, 'none')
            if first and failure == 'declined':
                selected = 'provider'
            answers[question] = {'type': 'choice', 'choice': selected, 'confidence': 1,
                                 'probabilities': {key: float(key == selected) for key in spec['criteria']}}
        if first and failure == 'uncertain':
            answers['catalog']['probabilities'] = {kind: 0.89, 'provider': 0.11,
                                                   'collection' if kind == 'entity' else 'entity': 0}
        return {'model': 'different-model' if not first and failure == 'model_changed' else 'jev-1.13.0',
                'answers': answers}
    with (patch('custom_components.phoenix_mcp.decision_provider.DECISION_BATCH_MAX_TARGET_BYTES', 1),
          patch.object(provider, '_request', side_effect=reply) as request):
        if failure:
            with pytest.raises(DecisionError):
                await provider.route_and_target(None, 'A request', targets, 5000, summary_scopes=scopes,
                                                revalidate=revalidate)
        else:
            result = await provider.route_and_target(None, 'A request', targets, 5000, summary_scopes=scopes,
                                                     revalidate=revalidate)
            assert result['route']['choice'] == ('light_on' if kind == 'entity' else 'state_summary')
        assert request.await_count == (1 if failure in ('authority', 'uncertain', 'declined') else 2)
        assert revalidate.call_count == (0 if failure in ('uncertain', 'declined') else 1)


@pytest.mark.parametrize('body,expected', [
    ('{"detail":{"error_type":"max_tokens_exceeded"}}', 'provider_context_limit'),
    ('{"detail":{"error_type":"max_tokens_exceeded","message":"private prompt test-secret"},'
     '"usage":{"input_tokens":999,"output_tokens":99}}', 'provider_context_limit'),
    ('{"detail":{"error_type":"private prompt test-secret"}}', 'http_error'),
    ('{"detail":{"error_type":{}}}', 'http_error'),
    ('{"detail":[]}', 'http_error'),
    ('[]', 'http_error'),
    ('{"detail":{"error_type":"max_tokens_exceeded","error_type":"max_tokens_exceeded"}}', 'http_error'),
    ('{"detail":{"error_type":"max_tokens_exceeded"},"padding":"' + 'x' * 4096 + '"}', 'http_error'),
    ('{"detail":' + '[' * 2000, 'http_error'),
    ('private prompt test-secret', 'http_error'),
    (b'\xff', 'http_error'),
], ids=['capacity', 'private_extras', 'unknown_code', 'object_code', 'array_detail', 'array_body',
        'duplicate', 'oversized', 'deep_json', 'non_json', 'invalid_utf8'])
async def test_typesafe_capacity_error_is_bounded_and_secret_free(body, expected, socket_enabled, caplog):
    from custom_components.phoenix_mcp.decision_metrics import DecisionMetrics

    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        assert request.headers['Authorization'] == 'Bearer test-secret'
        return web.Response(status=400, body=body.encode() if isinstance(body, str) else body,
                            headers={'Location': '/must-not-follow'})
    app = web.Application()
    app.router.add_post('/systemone', handler)
    metrics = DecisionMetrics()
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig('typesafe', 'jev-latest', str(server.make_url('')).rstrip('/'), 'test-secret')
        provider = DecisionProvider(cfg, 'systemone', metrics=metrics)
        with pytest.raises(DecisionError, match='^' + expected + '$'):
            await provider.classify(session, 'private prompt', 1000)
    assert calls == 1
    summary = metrics.summary()['typesafe']['preview']['classify']
    assert summary['requests'] == 1 and summary['responses'] == 0
    assert summary['usage_unknown'] == 1 and summary['reported_input_tokens'] is None
    assert 'private prompt' not in repr(metrics) + caplog.text
    assert 'test-secret' not in repr(metrics) + caplog.text


@pytest.mark.parametrize('kind,protocol,status,expected', [
    ('typesafe', 'systemone', 401, 'authentication'),
    ('typesafe', 'systemone', 403, 'authentication'),
    ('typesafe', 'systemone', 429, 'rate_limited'),
    ('typesafe', 'systemone', 529, 'rate_limited'),
    ('typesafe', 'systemone', 302, 'http_error'),
    ('typesafe', 'systemone', 500, 'http_error'),
    ('typesafe', 'systemone', 422, 'http_error'),
])
async def test_capacity_code_does_not_override_status_or_other_adapters(kind, protocol, status, expected, socket_enabled):
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        return web.json_response({'detail': {'error_type': 'max_tokens_exceeded'}}, status=status,
                                 headers={'Location': '/must-not-follow'})
    app = web.Application()
    app.router.add_post('/' + protocol, handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig(kind, 'model', str(server.make_url('')).rstrip('/'))
        with pytest.raises(DecisionError, match='^' + expected + '$'):
            await DecisionProvider(cfg, protocol).classify(session, 'command', 1000)
    assert calls == 1


async def test_capacity_refusal_reaches_preview_and_fixed_hybrid_counters(hass, decision_home, socket_enabled):
    data, access_key, store, _ = decision_home
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        return web.json_response({'detail': {'error_type': 'max_tokens_exceeded', 'message': 'private prompt'}}, status=400)
    app = web.Application()
    app.router.add_post('/systemone', handler)
    async with TestServer(app) as server:
        cfg = agentcli.ProviderConfig('typesafe', 'jev-latest', str(server.make_url('')).rstrip('/'), 'test-secret')
        with (patch.object(store, 'resolve', return_value=cfg),
              patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
            result = await async_preview_decision(hass, data, access_key, 'command', 'en', {'reason': 'no_match', 'proposal': None})
    assert calls == 1
    assert result['reason'] == 'provider_context_limit'
    assert result['decision'] is None and result['executed'] is False and '_plan' not in result
    assert data.store.get_pending_approvals() == []
    dispatch.assert_not_called()
    for chat in (False, True):
        data.decision_shadow.observe_hybrid(result, chat=chat)
        key = 'chat_hybrid' if chat else 'hybrid'
        assert data.decision_shadow.summary()[key]['reasons'] == {'provider_context_limit': 1}
    assert 'private prompt' not in json.dumps(result) + repr(data.decision_shadow)


@pytest.mark.parametrize('cancel', [False, True])
async def test_capacity_error_body_obeys_deadline_and_cancellation(cancel, socket_enabled):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        reply = web.StreamResponse(status=400)
        await reply.prepare(request)
        await reply.write(b'{"detail":')
        await release.wait()
        return reply
    app = web.Application()
    app.router.add_post('/systemone', handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig('typesafe', 'jev-latest', str(server.make_url('')).rstrip('/'))
        provider = DecisionProvider(cfg, 'systemone')
        original = provider._http_error_reason
        async def observe(reply):
            entered.set()
            return await original(reply)
        with patch.object(provider, '_http_error_reason', side_effect=observe):
            task = asyncio.create_task(provider._request(session, 'POST', 'systemone', 1000 if cancel else 50,
                                                         {'state': {'utterance': 'command'}, 'questions': {}}))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                if cancel:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(DecisionError, match='^http_error$'):
                        await asyncio.wait_for(task, 1)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    assert calls == 1


@pytest.mark.parametrize('kind', ['systemone', 'classifier', 'ollama_decision', 'unknown'])
async def test_unsupported_accounts_cannot_enter_any_provider_surface(hass, decision_home, kind):
    data, _, store, _ = decision_home
    legacy = await store.add(kind, {'api_key': 'private-key', 'base_url': 'http://retired.test/v1', 'model': 'retired-model'})
    assert agentcli.provider_purpose(kind) == 'unsupported'
    assert legacy not in {row['id'] for row in store.list_instances()}
    assert store.resolve(legacy) is None
    assert store.resolve(legacy, purpose='decision') is None
    assert store.resolve(legacy, purpose='unsupported') is None
    hass.data['_phoenix_agentcli_store'] = store
    assert agentcli.decision_account_fingerprint(hass, legacy) is None
    _, error = await _validated_settings_patch({'decision_provider_id': legacy}, data, hass, 'rid')
    assert error is not None and error.status == 400
    config = await agentcli._probe_config(kind, {'api_key': 'private-key', 'base_url': 'http://retired.test/v1'})
    assert isinstance(config, agentcli.ProbeConfigError) and config.key == 'providerUnknown'
    with pytest.raises(ValueError):
        DecisionProvider(agentcli.ProviderConfig(kind, 'retired-model', 'http://retired.test/v1'), 'systemone')
    # Stored credentials remain recoverable only through the existing admin delete/wipe owner.
    await store.delete(legacy)
    assert store.get(legacy) is None


@pytest.mark.parametrize('status', [200, 500])
async def test_decision_request_records_identity_and_outcome_without_request_envelope(status, socket_enabled):
    from custom_components.phoenix_mcp import conversation_log as history
    from custom_components.phoenix_mcp.access_key_store import GlobalSettings
    log = history.ConversationLog()
    log.configure(GlobalSettings(conversation_log_enabled=True))
    handle = log.begin('agentcli', prompt='Safe visible prompt')
    ctx = history._current.set(handle)
    async def handler(request):
        return web.json_response(response(), status=status)
    app = web.Application()
    app.router.add_post('/systemone', handler)
    try:
        async with TestServer(app) as server, ClientSession() as session:
            cfg = agentcli.ProviderConfig('typesafe', 'jev-latest', str(server.make_url('')).rstrip('/'), 'canary-api-secret')
            provider = DecisionProvider(cfg, 'systemone', metric_scope='chat_shadow')
            if status == 200:
                await provider.classify(session, 'private request envelope', 1000)
            else:
                with pytest.raises(DecisionError):
                    await provider.classify(session, 'private request envelope', 1000)
        events = log.detail(handle[1])['events']
        assert len(events) == 1
        assert events[0]['payload']['result'] == ('completed' if status == 200 else 'failed')
        assert events[0]['payload']['observation_only'] is True
        assert events[0]['payload']['provider'] == 'typesafe'
        assert events[0]['payload']['model'] == 'jev-latest'
        assert events[0]['payload']['input']['text'] == 'private request envelope'
        assert 'questions' not in json.dumps(events) and 'instructions' not in json.dumps(events)
        assert 'canary-api-secret' not in json.dumps(events)
    finally:
        history._current.reset(ctx)
