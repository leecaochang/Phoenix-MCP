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


def test_hybrid_counters_keep_only_allowlisted_reasons_and_reset_with_runtime():
    shadow = DecisionShadow()
    private = {'entity_id': 'light.private', 'utterance': 'private command', 'api_key': 'private-key'}
    for index in range(100):
        shadow.observe_hybrid({'reason': f'private-key-{index}', 'decision': private})
    shadow.observe_hybrid({'reason': 'observed', '_plan': private})
    shadow.observe_hybrid({'reason': 'observed', '_plan': private}, authority_changed=True)
    summary = shadow.summary()
    assert summary['hybrid'] == {'evaluations': 102, 'reasons': {
        'unavailable': 100, 'plan_ready': 1, 'authority_changed': 1,
    }}
    assert 'private' not in repr(shadow)
    summary['hybrid']['reasons']['injected'] = 1
    assert 'injected' not in shadow.summary()['hybrid']['reasons']
    assert DecisionShadow().summary()['hybrid'] == {'evaluations': 0, 'reasons': {}}


def response():
    return {'model':'jev-1.13.0', 'answers':{'route':{'type':'choice','choice':'light_on',
        'confidence':0.8,'probabilities':{k:0.92 if k=='light_on' else 0.02 for k in DECISION_ROUTE_CHOICES}}}}


@pytest.fixture
async def decision_home(hass, token_store):
    token, _ = await token_store.async_create_token('decision_test','test')
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add('typesafe', {'api_key':'test-secret','model':'jev-latest'})
    await token_store.async_patch_settings(decision_provider_id=account)
    with patch.object(agentcli, '_get_secret_store', AsyncMock(return_value=store)):
        yield data, token, store, account
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


@pytest.mark.parametrize('protocol', ['systemone','classifier'])
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
    assert payload['state']=={'utterance':'Switch the lamp on'}
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
def test_malformed_answers_have_no_decision(change):
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
    with pytest.raises(DecisionError):parse_choice(body,DECISION_ROUTE_CHOICES)


async def test_decision_accounts_are_secret_free_and_cannot_resolve_for_conversation(decision_home):
    data, token, store, account=decision_home
    assert store.resolve(account) is None
    cfg=store.resolve(account,purpose='decision')
    assert cfg.kind=='typesafe'
    with pytest.raises(ValueError):agentcli.build_provider(cfg)
    assert 'test-secret' not in json.dumps(store.list_instances())
    assert not any(i['kind']=='typesafe' for i in agentcli._provider_catalog())
    assert {i['kind'] for i in agentcli._provider_catalog('decision')}=={'typesafe','systemone','classifier'}


@pytest.mark.parametrize('field',['voice_agent_provider_id','ai_task_provider_id'])
async def test_backend_rejects_decision_account_surface_binding(hass,decision_home,field):
    data,token,store,account=decision_home
    _,error=await _validated_settings_patch({field:account},data,hass,'rid')
    assert error is not None and error.status==400


async def test_only_decision_accounts_can_be_selected(hass,decision_home):
    data,token,store,account=decision_home
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
    data,token,*_=decision_home
    with (patch.object(DecisionProvider,'classify',AsyncMock(return_value=parse_choice(response(),DECISION_ROUTE_CHOICES))) as classify,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result=await async_preview_decision(hass,data,token,'Switch the lamp on','en',{'reason':'no_match','proposal':None})
    assert result['reason']=='observed' and result['executed'] is False
    classify.assert_awaited_once();dispatch.assert_not_called()
    assert data.store.get_pending_approvals()==[]
    assert data.decision_shadow.summary()['observations']==0
    assert data.decision_shadow.summary()['hybrid']=={'evaluations':0,'reasons':{}}


@pytest.mark.parametrize('reason,proposal',[('supported',{'intent':'HassTurnOn'}),('sentence_trigger',None),
                                           ('timeout',None),('busy',None),('authority_changed',None)])
async def test_local_matches_and_unsafe_recognition_failures_skip_remote(hass,decision_home,reason,proposal):
    data,token,*_=decision_home
    with patch.object(DecisionProvider,'classify',AsyncMock()) as classify:
        result=await async_preview_decision(hass,data,token,'command','en',{'reason':reason,'proposal':proposal})
    assert result['decision'] is None
    classify.assert_not_called()


@pytest.mark.parametrize('change',['token','settings','account_model','account_deleted','runtime'])
async def test_changes_during_network_wait_discard_decision(hass,decision_home,change):
    data,token,store,account=decision_home
    entered=asyncio.Event();release=asyncio.Event()
    async def classify(*args):
        entered.set();await release.wait();return parse_choice(response(),DECISION_ROUTE_CHOICES)
    with patch.object(DecisionProvider,'classify',classify):
        task=data.decision_shadow.start(hass,data,token,'command','en',observe=True)
        await entered.wait()
        if change=='token':token.revoked=True
        if change=='settings':await data.store.async_patch_settings(decision_timeout_ms=1234)
        if change=='account_model':await store.set_model(account,'changed')
        if change=='account_deleted':await store.delete(account)
        if change=='runtime':data.shutting_down=True
        release.set();result=await task
    assert result['reason']=='authority_changed' and result['decision'] is None
    assert not data.decision_shadow.choices


async def test_single_flight_cancellation_releases_slot_without_retaining_text(hass,decision_home):
    data,token,*_=decision_home
    entered=asyncio.Event();closed=asyncio.Event()
    async def classify(*args):
        entered.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    with patch.object(DecisionProvider,'classify',classify):
        task=data.decision_shadow.start(hass,data,token,'private utterance','en',observe=True)
        await entered.wait()
        assert data.decision_shadow.start(hass,data,token,'second','en') is None
        data.decision_shadow.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    await closed.wait()
    assert data.decision_shadow.task is None
    assert 'private utterance' not in repr(data.decision_shadow)


async def test_non_english_never_sends(hass,decision_home):
    data,token,*_=decision_home
    with patch.object(DecisionProvider,'classify',AsyncMock()) as classify:
        result=await data.decision_shadow.start(hass,data,token,'allume','fr')
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


async def test_keyless_private_endpoint_identity_and_setup_validation():
    for kind in ('systemone','classifier'):
        cfg=await agentcli._probe_config(kind,{'base_url':'http://127.0.0.1:8000/v1','model':'local-model'})
        assert isinstance(cfg,agentcli.ProviderConfig)
        assert not cfg.api_key
        store=agentcli.AgentCliStore(AsyncMock())
        await store.add(kind,agentcli._stored_setup_config(cfg,'local-model'))
        assert store.has_duplicate(kind,{'base_url':'http://127.0.0.1:8000/v1/'})
        assert not store.has_duplicate(kind,{'base_url':'http://127.0.0.1:9000/v1'})
        for url in ('http://169.254.169.254/v1','http://user:password@127.0.0.1/v1','http://127.0.0.1/v1?key=secret'):
            assert isinstance(await agentcli._probe_config(kind,{'base_url':url,'model':'m'}),agentcli.ProbeConfigError)


@pytest.mark.parametrize('local_reason,expected', [('supported',0),('no_match',1),('timeout',0),('sentence_trigger',0)])
async def test_voice_shadow_never_waits_for_decision_or_replaces_provider(hass,decision_home,local_reason,expected):
    from custom_components.phoenix_mcp import voice_agent
    data,token,store,account=decision_home
    chat=await store.add('deepseek',{'api_key':'chat','model':'model'})
    await data.store.async_patch_settings(voice_agent_enabled=True,voice_agent_token_id=token.id,
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
