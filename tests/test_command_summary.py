"""Scoped aggregate reads use real recognition, dispatch and current membership."""
import asyncio
from unittest.mock import patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.command_execution import _capture_plan, plan_is_current, async_execute_chat_command
from custom_components.phoenix_mcp.command_routing import RoutingAuthority, async_evaluate_command
from custom_components.phoenix_mcp.command_summary import summary_catalog, summary_proposal, summary_text
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.access_key_store import PermissionTree, PermissionNode


@pytest.fixture
async def summary_home(hass, access_key_store, immediate_command_observation):
    access_key, _ = await access_key_store.async_create_access_key('summary', 'test', rate_limit_requests=0)
    access_key.permissions = PermissionTree(domains={'light': PermissionNode(state='GREEN')})
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    await access_key_store.async_patch_settings(agentcli_routing_mode='local', mesa_mode='off')
    assert await async_setup_component(hass, 'homeassistant', {})
    assert await async_setup_component(hass, 'conversation', {})
    area = ar.async_get(hass).async_create('Office')
    registry = er.async_get(hass)
    ids=[]
    for name, state in [('one','on'),('two','off'),('three','unavailable')]:
        entry=registry.async_get_or_create('light','test',name,original_name=name)
        registry.async_update_entity(entry.entity_id,area_id=area.id)
        hass.states.async_set(entry.entity_id,state,{'friendly_name':name})
        async_expose_entity(hass,'conversation',entry.entity_id,True)
        ids.append(entry.entity_id)
    return data,access_key,ids


def proposal(hass, access_key, mode='on'):
    descriptor=next(d for d in summary_catalog(hass,access_key).targets.values() if not d['summary_scope']['area_id'])
    return summary_proposal(hass,access_key,descriptor,mode)


@pytest.mark.parametrize('limit', ['count', 'bytes'])
async def test_summary_catalog_overflow_refuses_without_truncation(hass, summary_home, limit):
    from custom_components.phoenix_mcp.decision_targets import CatalogLimitError
    _, access_key, _ = summary_home
    full = summary_catalog(hass, access_key)
    assert len(full.targets) > 1
    with pytest.raises(CatalogLimitError):
        summary_catalog(hass, access_key, **({'max_targets': 1} if limit == 'count' else {'max_bytes': 1}))


@pytest.mark.parametrize('private', ['light.hidden_private', 'phx_' + 'a'*64])
async def test_summary_area_metadata_cannot_disclose_hidden_ids_or_secrets(hass, summary_home, private):
    from custom_components.phoenix_mcp.decision_provider import DecisionError
    _, access_key, ids = summary_home
    entry = er.async_get(hass).async_get(ids[0])
    ar.async_get(hass).async_update(entry.area_id, aliases={private})
    with pytest.raises(DecisionError, match='context_unavailable'):
        summary_catalog(hass, access_key)


def test_summary_duplicate_rows_and_unbounded_breakdown_are_not_partial_answers():
    from custom_components.phoenix_mcp.const import COMMAND_SUMMARY_MAX_STATES
    ids = [f'sensor.fixture_{index}' for index in range(COMMAND_SUMMARY_MAX_STATES+1)]
    query = {'entity_ids': ids, 'summary_mode': 'breakdown'}
    rows = [{'entity_id': entity, 'state': str(index)} for index, entity in enumerate(ids)]
    assert summary_text(query, rows, 'en') is None
    assert summary_text({'entity_ids': ids[:1], 'summary_mode': 'total'}, rows[:1]*2, 'en') is None


async def test_real_local_count_and_dispatch(hass, summary_home):
    data,access_key,ids=summary_home
    result=await async_evaluate_command(hass,data,access_key,'how many lights are on','en',preview=True)
    assert result['proposal']==proposal(hass,access_key)
    with patch('custom_components.phoenix_mcp.agentcli.build_provider') as provider:
        reply=await async_execute_chat_command(hass,data,access_key,result['proposal'],RoutingAuthority.capture(data,access_key),'http://test','test',asyncio.Event())
    assert reply.allows_local_followup
    assert '1 of 3' in reply.text and '1 have unknown' in reply.text
    provider.assert_not_called()
    assert [hass.states.get(eid).state for eid in ids]==['on','off','unavailable']


@pytest.mark.parametrize('sentence', ['how many lights are on in the office', 'how many office lights are on'])
async def test_real_area_count(hass,summary_home,sentence):
    data,access_key,ids=summary_home
    result=await async_evaluate_command(hass,data,access_key,sentence,'en',preview=True)
    assert result['proposal']['entity_ids']==sorted(ids)
    assert result['proposal']['summary_scope']['area_id']=='office'


@pytest.mark.parametrize('sentence', ['turn on all lights', 'how many lights were on yesterday', 'explain how many lights are on', 'how many lights are brighter than 20 percent'])
async def test_no_aggregate_for_other_requests(hass,summary_home,sentence):
    data,access_key,_=summary_home
    result=await async_evaluate_command(hass,data,access_key,sentence,'en',preview=True)
    assert not result['proposal'] or result['proposal']['intent']!='PhoenixStateSummary'


async def test_scope_changes_invalidate_and_hidden_members_never_count(hass,summary_home):
    data,access_key,ids=summary_home
    p=proposal(hass,access_key)
    plan=_capture_plan(hass,data,access_key,p,ingress='chat')
    assert plan_is_current(plan,hass,data,access_key)
    entry=er.async_get(hass).async_get_or_create('light','test','four')
    hass.states.async_set(entry.entity_id,'on')
    assert not plan_is_current(plan,hass,data,access_key)
    async_expose_entity(hass,'conversation',entry.entity_id,False)
    assert plan_is_current(plan,hass,data,access_key)
    async_expose_entity(hass,'conversation',ids[0],False)
    assert not plan_is_current(plan,hass,data,access_key)
    assert ids[0] not in proposal(hass,access_key)['entity_ids']


async def test_zero_and_incomplete_results(hass,summary_home):
    _,access_key,ids=summary_home
    p=proposal(hass,access_key)
    rows=[{'entity_id':eid,'state':'off'} for eid in ids]
    assert '0 of 3' in summary_text(p,rows,'en')
    assert summary_text(p,rows[:-1],'en') is None
    assert summary_text(p,{'entities':rows},'en') is None
    p['summary_mode']='breakdown'
    assert 'off: 3' in summary_text(p,rows,'en')
    p['summary_mode']='total'
    assert '3 entities' in summary_text(p,rows,'en')


async def test_scope_denial_does_not_disclose_count(hass,summary_home):
    data,access_key,_=summary_home
    p = proposal(hass,access_key)
    access_key.permissions = PermissionTree()
    reply=await async_execute_chat_command(hass,data,access_key,p,RoutingAuthority.capture(data,access_key),'http://test','test',asyncio.Event())
    assert reply is None


@pytest.mark.parametrize('mode', ['on', 'off', 'total', 'breakdown'])
async def test_jev_selects_query_phoenix_counts_via_real_dispatch(hass,summary_home,mode):
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp import agentcli
    from custom_components.phoenix_mcp.decision_provider import DecisionProvider
    from custom_components.phoenix_mcp.decision_shadow import async_preview_decision
    from custom_components.phoenix_mcp.command_execution import async_execute_chat_plan
    data,access_key,_=summary_home
    store=agentcli.AgentCliStore(AsyncMock())
    account=await store.add('typesafe',{'api_key':'test-secret','model':'jev-latest'})
    hass.data['_phoenix_agentcli_store']=store
    await data.store.async_patch_settings(agentcli_routing_mode='hybrid',decision_provider_id=account)
    async def response(*args):
        payload=args[4]
        scopes=payload['state']['summary_scopes']
        key=next(key for key,item in scopes.items() if not item['summary_scope']['area_id'])
        selected={'route':'state_summary','target':'none','summary_scope':key,'summary':mode}
        return {'model':'jev-1.13.0','answers':{
            name:{'type':'choice','choice':selected.get(name,'none'),'confidence':1,
                  'probabilities':{key:float(key==selected.get(name,'none')) for key in spec['criteria']}}
            for name,spec in payload['questions'].items()}}
    with patch.object(DecisionProvider,'_request',side_effect=response) as request:
        result=await async_preview_decision(hass,data,access_key,'Give me a current light count','en',
                                            {'reason':'no_match','proposal':None},targets=True,prepare=True,ingress='chat')
    request.assert_awaited_once()
    assert '_plan' in result, result
    assert plan_is_current(result['_plan'],hass,data,access_key)
    reply=await async_execute_chat_plan(hass,data,access_key,result['_plan'],RoutingAuthority.capture(data,access_key),'http://test','test',asyncio.Event())
    assert reply.allows_local_followup, reply
    assert '3' in reply.text
    result['_plan']['proposal']['summary_mode'] = 'total' if mode != 'total' else 'on'
    assert not plan_is_current(result['_plan'],hass,data,access_key)


async def test_duplicate_area_names_refuse_local_query(hass,summary_home):
    from types import SimpleNamespace
    from custom_components.phoenix_mcp.command_summary import local_summary
    _,access_key,ids=summary_home
    other=ar.async_get(hass).async_create('Other room', aliases={'Office'})
    er.async_get(hass).async_update_entity(ids[0],area_id=other.id)
    assert local_summary(hass,access_key,SimpleNamespace(response='how_many'),{'domain':'light','area':'office','state':'on'}) is None


async def test_unknown_members_stay_in_denominator_and_state_changes_remain_fresh(hass,summary_home):
    data,access_key,ids=summary_home
    p=proposal(hass,access_key)
    plan=_capture_plan(hass,data,access_key,p,ingress='chat')
    hass.states.async_set(ids[0],'unknown',{'friendly_name':'one'})
    assert plan_is_current(plan,hass,data,access_key)
    reply=await async_execute_chat_command(hass,data,access_key,p,RoutingAuthority.capture(data,access_key),'http://test','test',asyncio.Event())
    assert '0 of 3' in reply.text and '2 have unknown' in reply.text


async def test_context_review_contains_complete_summary_candidate(hass,summary_home):
    from custom_components.phoenix_mcp.decision_context import _state
    _,access_key,_=summary_home
    p=proposal(hass,access_key)
    state=_state(hass,access_key,'how many lights are on','en',
                 [{'role':'user','content':'Only read states.'},{'role':'assistant','content':'OK.'}],p)
    assert state is not None and state[0]['candidate']==p
    assert 'service' not in state[0]


async def test_device_class_scope_and_nonprimary_exclusions(hass,summary_home):
    from homeassistant.helpers.entity_registry import RegistryEntryHider
    from homeassistant.helpers.entity import EntityCategory
    data,access_key,ids=summary_home
    access_key.permissions.domains['binary_sensor']=PermissionNode(state='YELLOW')
    registry=er.async_get(hass)
    entry=registry.async_get_or_create('binary_sensor','test','door')
    hass.states.async_set(entry.entity_id,'on',{'device_class':'door'})
    async_expose_entity(hass,'conversation',entry.entity_id,True)
    descriptor=next(d for d in summary_catalog(hass,access_key).targets.values() if d['summary_scope']['device_class']=='door')
    assert summary_proposal(hass,access_key,descriptor,'on')['entity_ids']==[entry.entity_id]
    assert summary_proposal(hass,access_key,descriptor,'playing') is None
    registry.async_update_entity(ids[0],entity_category=EntityCategory.DIAGNOSTIC)
    registry.async_update_entity(ids[1],hidden_by=RegistryEntryHider.USER)
    assert summary_members_for_light(hass,access_key)==[ids[2]]


def summary_members_for_light(hass,access_key):
    from custom_components.phoenix_mcp.command_summary import summary_members
    return summary_members(hass,access_key,{'domain':'light','device_class':'','area_id':''})


@pytest.mark.parametrize('language,sentence', [('es','cuántas luces están encendidas'), ('de','wie viele Lichter sind an')])
async def test_installed_non_english_count_grammar(hass,summary_home,language,sentence):
    data,access_key,_=summary_home
    result=await async_evaluate_command(hass,data,access_key,sentence,language,preview=True)
    assert result['proposal']==proposal(hass,access_key)


@pytest.mark.parametrize('damage', ['single_target', 'wrong_filter', 'unrequested_filter', 'scope_changed', 'low_probability'])
async def test_jev_summary_refusals(hass,summary_home,damage):
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp import agentcli
    from custom_components.phoenix_mcp.decision_provider import DecisionProvider
    from custom_components.phoenix_mcp.decision_batch import async_preview_batch
    data,access_key,ids=summary_home
    store=agentcli.AgentCliStore(AsyncMock())
    account=await store.add('typesafe',{'api_key':'test-secret','model':'jev-latest'})
    hass.data['_phoenix_agentcli_store']=store
    await data.store.async_patch_settings(decision_provider_id=account)
    async def response(*args):
        payload=args[4]
        key=next(key for key,item in payload['state']['summary_scopes'].items() if not item['summary_scope']['area_id'])
        selected={'route':'state_summary','target':'none','summary_scope':key,'summary':'on'}
        if damage=='single_target': selected['target']=next(iter(payload['state']['targets']))
        if damage=='wrong_filter': selected['summary']='playing'
        if damage=='unrequested_filter': selected.update(route='light_on',target=next(iter(payload['state']['targets'])),summary_scope='none')
        if damage=='scope_changed': async_expose_entity(hass,'conversation',ids[0],False)
        answers={name:{'type':'choice','choice':selected.get(name,'none'),'confidence':1,
                      'probabilities':{key:float(key==selected.get(name,'none')) for key in spec['criteria']}}
                 for name,spec in payload['questions'].items()}
        if damage=='low_probability':
            answers['summary']['probabilities'].update(on=0.6,off=0.4)
        return {'model':'jev-1.13.0','answers':answers}
    with patch.object(DecisionProvider,'_request',side_effect=response):
        result=await async_preview_batch(hass,data,access_key,'how many lights are on','en',{'reason':'no_match'})
    assert 'proposal' not in result
    assert result['reason'] in ('category_mismatch','targets_changed','low_probability'),result


async def test_jev_summary_with_history_binds_context_and_query(hass,summary_home):
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp import agentcli
    from custom_components.phoenix_mcp.decision_provider import DecisionProvider
    from custom_components.phoenix_mcp.decision_context import async_prepare_chat_context
    from custom_components.phoenix_mcp.const import DECISION_CONTEXT_REQUIRED_CHOICES
    data,access_key,_=summary_home
    store=agentcli.AgentCliStore(AsyncMock())
    account=await store.add('typesafe',{'api_key':'test-secret','model':'jev-latest'})
    hass.data['_phoenix_agentcli_store']=store
    await data.store.async_patch_settings(agentcli_routing_mode='hybrid',decision_provider_id=account)
    history=[{'role':'user','content':'Only read my home states.'},{'role':'assistant','content':'OK.'}]
    async def response(*args):
        payload=args[4]
        if 'route' in payload['questions']:
            key=next(key for key,item in payload['state']['summary_scopes'].items() if not item['summary_scope']['area_id'])
            selected={'route':'state_summary','target':'none','summary_scope':key,'summary':'breakdown'}
        else:
            assert payload['state']['history']==history
            assert payload['state']['candidate']['summary_mode']=='breakdown'
            selected=DECISION_CONTEXT_REQUIRED_CHOICES
        return {'model':'jev-1.13.0','answers':{
            name:{'type':'choice','choice':selected.get(name,'none'),'confidence':1,
                  'probabilities':{key:float(key==selected.get(name,'none')) for key in spec['criteria']}}
            for name,spec in payload['questions'].items()}}
    with patch.object(DecisionProvider,'_request',side_effect=response) as request:
        result=await async_prepare_chat_context(hass,data,access_key,'Summarize the current lights','en',history,
                                                {'reason':'no_match','proposal':None},asyncio.Event())
    assert result['reason']=='plan_ready',result
    assert request.await_count==2
    assert plan_is_current(result['_plan'],hass,data,access_key)


async def test_summary_area_metadata_cannot_disclose_configured_secret(hass,summary_home):
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp import agentcli
    from custom_components.phoenix_mcp.decision_batch import async_preview_batch
    from custom_components.phoenix_mcp.decision_provider import DecisionProvider
    data,access_key,_=summary_home
    store=agentcli.AgentCliStore(AsyncMock())
    account=await store.add('typesafe',{'api_key':'private-opaque-credential','model':'jev-latest'})
    hass.data['_phoenix_agentcli_store']=store
    await data.store.async_patch_settings(decision_provider_id=account)
    ar.async_get(hass).async_update('office',aliases={'private-opaque-credential'})
    with patch.object(DecisionProvider,'_request',new_callable=AsyncMock) as request:
        result=await async_preview_batch(hass,data,access_key,'Count the lights','en',{'reason':'no_match'})
    assert result['reason']=='context_unavailable'
    request.assert_not_called()


async def test_local_count_does_not_build_unrelated_provider_catalog(hass,summary_home):
    data,access_key,_=summary_home
    with patch('custom_components.phoenix_mcp.command_summary.summary_catalog',side_effect=AssertionError('Unrelated catalog must not be built')):
        result=await async_evaluate_command(hass,data,access_key,'how many lights are on','en',preview=True)
        assert result['proposal']['summary_mode']=='on'
        assert _capture_plan(hass,data,access_key,result['proposal'],ingress='chat') is not None


@pytest.mark.parametrize('repeats', [1, 4])
def test_large_summary_wire_preserves_every_scope_and_state_choice(repeats):
    import json
    from custom_components.phoenix_mcp.command_summary import summary_wire
    targets={f's{index}':{'summary_scope':{'domain':'light','device_class':f'class_{index % repeats}','area_id':f'room_{index // repeats}'},
                         'area':f'Room {index // repeats}','area_aliases':[f'Alias {index // repeats}'],
                         'states':['on','off','unknown','unavailable']} for index in range(150)}
    wire=summary_wire(targets)
    assert len(json.dumps(wire).encode()) < 16384
    assert set(wire['summary_scopes'])==set(targets)
    if repeats > 1:
        assert 'summary_areas' in wire
    for key,row in wire['summary_scopes'].items():
        decoded=dict(zip(wire['summary_scope_fields'],row,strict=True))
        if 'area_ref' in decoded:
            decoded.update(wire['summary_areas'][decoded.pop('area_ref')])
        assert {name:decoded[name] for name in ('domain','device_class','area_id')}==targets[key]['summary_scope']
        assert decoded['area_aliases']==targets[key]['area_aliases']
        assert wire['summary_domain_states'][decoded['domain']]==targets[key]['states']


async def test_summary_question_instructions_are_independent_and_consistent():
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp.agentcli import ProviderConfig
    from custom_components.phoenix_mcp.decision_provider import DecisionProvider
    provider=DecisionProvider(ProviderConfig('typesafe','jev-latest','https://example.test'),'systemone')
    scopes={'s0':{'summary_scope':{'domain':'light','device_class':'','area_id':''},
                  'area':'','area_aliases':[],'states':['on','off']}}
    async def response(*args):
        payload=args[4]
        questions=payload['questions']
        assert 'counts and general conversation' not in questions['route']['criteria']['provider']
        assert 'counts, comparisons' not in questions['route']['instructions']
        assert 'across the whole accessible home' in questions['summary_scope']['criteria']['s0'].lower()
        assert 'Independently select the requested count' in questions['summary']['instructions']
        selected={'route':'state_summary','target':'none','summary_scope':'s0','summary':'on'}
        return {'model':'jev-1.13.0','answers':{
            name:{'type':'choice','choice':selected[name],'confidence':1,
                  'probabilities':{key:float(key==selected[name]) for key in question['criteria']}}
            for name,question in questions.items()}}
    with patch.object(provider,'_request',side_effect=response):
        result=await provider.route_and_target(AsyncMock(),'Count the lights that are on',{},1000,summary_scopes=scopes)
    assert result['summary']['choice']=='on'
