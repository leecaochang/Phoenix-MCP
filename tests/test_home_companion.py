"""Single-tool delegation enforces explicit scope and preserves real dispatch boundaries."""
import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import ServiceCall, ServiceRegistry

from custom_components.phoenix_mcp import agentcli, home_companion as home, mcp_view
from custom_components.phoenix_mcp.const import DOMAIN, HOME_COMPANION_TOOLS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.personas import get_persona_caps, detect_persona
from custom_components.phoenix_mcp.policy_engine import resolve, Permission
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord


@pytest.fixture
async def companion(hass, access_key_store):
    access_key, _ = await access_key_store.async_create_access_key('companion', 'test')
    await access_key_store.async_patch_access_key(access_key.id, persona='home_companion', **get_persona_caps('home_companion'))
    access_key.permissions = PermissionTree(entities={
        'light.selected': PermissionNode('GREEN'), 'fan.selected': PermissionNode('GREEN'),
        'sensor.temperature': PermissionNode('YELLOW'),
    })
    for entity, state, attrs in [
        ('light.selected','off', {'friendly_name':'Selected lamp'}),
        ('light.other','off', {'friendly_name':'Other lamp'}),
        ('fan.selected','off', {'friendly_name':'Selected fan','supported_features':1}),
        ('lock.door','locked', {'friendly_name':'Front door'}),
        ('sensor.temperature','23', {'device_class':'temperature'}),
        ('sensor.location','home', {'device_class':'enum'}),
    ]:
        hass.states.async_set(entity,state,attrs)
    data = PhoenixData(store=access_key_store,rate_limiter=MagicMock(),audit=MagicMock(),hass=hass)
    hass.data[DOMAIN] = data
    await access_key_store.async_patch_settings(mesa_mode='off')
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add('chatgpt',{'api_key':'synthetic-key','model':'test-model'})
    hass.data['_phoenix_agentcli_store'] = store
    await access_key_store.async_patch_settings(voice_agent_provider_id=account,voice_agent_model='test-model')
    yield data, access_key, store, account
    if data.sensor_flush_cancel:
        data.sensor_flush_cancel()


async def rpc(hass, companion, name='home_request', args=None, method='tools/call'):
    data, access_key, _, _ = companion
    return await mcp_view._dispatch_mcp(method,'id',{'name':name,'arguments':args or {'request':'Read the temperature.'}},access_key,hass,data,'test','http://test')


def payload(response):
    return json.loads(response[0]['result']['content'][0]['text'])


def scripted(monkeypatch, rounds, *, before=None, transcripts=None):
    provider = agentcli.ClaudeProvider(agentcli.ProviderConfig('claude','test','http://test','key'))
    catalogs = []
    async def stream(session, *, system_prompt, messages, tools, options):
        catalogs.append(tools)
        if transcripts is not None:
            transcripts.append(deepcopy(messages))
        if before:
            await before(len(catalogs))
        current = rounds.pop(0)
        if isinstance(current,str):
            yield {'type':agentcli.EV_TEXT,'text':current}
            yield {'type':agentcli.EV_DONE,'stop_reason':'end_turn','assistant_msg':{'role':'assistant','content':[{'type':'text','text':current}]}}
        elif isinstance(current,dict):
            yield current
        else:
            calls = [{'type':'tool_use','id':f't{len(catalogs)}_{i}','name':name,'input':args} for i,(name,args) in enumerate(current)]
            for call in calls:
                yield {'type':agentcli.EV_TOOL,'id':call['id'],'name':call['name'],'input':call['input']}
            yield {'type':agentcli.EV_DONE,'stop_reason':'tool_use','assistant_msg':{'role':'assistant','content':calls}}
    monkeypatch.setattr(provider,'stream_turn',stream)
    monkeypatch.setattr(agentcli,'build_provider',lambda cfg:provider)
    return catalogs


async def test_persona_is_persisted_and_never_inferred(companion):
    data,access_key,_,_=companion
    await data.store.async_patch_access_key(access_key.id,cap_log_read='allow',announce_all_tools=True,pass_through=True)
    assert access_key.persona == 'home_companion'
    assert not access_key.pass_through and not access_key.announce_all_tools
    assert AccessKeyRecord.from_dict(access_key.to_storage_dict()).persona == 'home_companion'
    assert detect_persona(get_persona_caps('home_companion')) != 'home_companion'


async def test_single_external_catalog_and_no_resource_backdoor(hass,companion):
    result = await rpc(hass,companion,method='tools/list')
    assert [t['name'] for t in result[0]['result']['tools']] == ['home_request']
    assert [t['name'] for t in agentcli.build_agent_tool_list(companion[1],companion[0],'chat')] == ['home_request']
    for method in ['resources/list','prompts/list','resources/templates/list']:
        assert list((await rpc(hass,companion,method=method))[0]['result'].values()) == [[]]
    for method in ['resources/read','prompts/get']:
        assert 'error' in (await rpc(hass,companion,method=method))[0]


@pytest.mark.parametrize('name',['phoenix_request','get_state','read_file','call_service','HassTurnOn','intent__HassTurnOn'])
async def test_guessed_external_calls_never_dispatch(hass,companion,name):
    with patch.object(ServiceRegistry,'async_call',AsyncMock()) as service:
        result = await rpc(hass,companion,name,{'entity_id':'light.selected'})
    internal = mcp_view._NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(name, name)
    if internal not in mcp_view._TOOL_HANDLERS:
        assert result[3] == 'invalid_request'
        assert result[0]['error']['code'] == -32602
    else:
        assert result[3] == 'denied'
        assert result[0]['result']['isError']
    service.assert_not_awaited()
    assert not companion[0].store.get_pending_approvals()


async def test_scope_honors_inherited_grants_and_excludes_sensitive_classes(hass,companion):
    _,access_key,_,_=companion
    access_key.permissions.domains = {d:PermissionNode('GREEN') for d in ('light','sensor','lock')}
    access_key.permissions.entities['lock.door'] = PermissionNode('GREEN')
    access_key.permissions.entities['sensor.location'] = PermissionNode('GREEN')
    assert resolve('light.selected',access_key,hass) == Permission.WRITE
    assert resolve('light.other',access_key,hass) == Permission.WRITE
    assert resolve('sensor.temperature',access_key,hass) == Permission.READ
    for entity in ('lock.door','sensor.location'):
        assert resolve(entity,access_key,hass) == Permission.NO_ACCESS
    access_key.permissions.domains['light'] = PermissionNode('RED')
    assert resolve('light.selected',access_key,hass) == Permission.DENY


@pytest.mark.parametrize('grant', ['YELLOW', 'GREEN'])
async def test_domain_grants_discover_lights_and_gate_native_writes(hass,companion,monkeypatch,grant):
    _, access_key, _, _ = companion
    access_key.permissions = PermissionTree(
        domains={'light':PermissionNode(grant)},
        entities={'light.other':PermissionNode('RED')},
    )
    calls = []
    async def turn_on(call):
        calls.append(call.data)
    hass.services.async_register('light','turn_on',turn_on)
    transcripts = []
    scripted(monkeypatch, [
        [('search_entities', {'domain':'light'})],
        [('light__HassLightSet', {'domain':['light'],'brightness':40})],
        json.dumps({'status':'completed' if grant == 'GREEN' else 'refused','message':'Finished.'}),
    ], transcripts=transcripts)
    result = payload(await rpc(hass,companion))
    discovered = json.dumps(transcripts[1][-1])
    assert 'light.selected' in discovered and 'light.other' not in discovered
    if grant == 'GREEN':
        assert result['status'] == 'completed'
        assert len(calls) == 1 and calls[0]['entity_id'] == ['light.selected']
    else:
        assert result['status'] == 'refused'
        assert not calls


async def test_shared_loop_reads_with_complete_catalog(hass,companion,monkeypatch):
    catalogs=scripted(monkeypatch,[[('get_state',{'entity_id':'sensor.temperature','projection':{'kind':'compact'}})],json.dumps({'status':'completed','message':'23 degrees.'})])
    result=payload(await rpc(hass,companion))
    assert result['status']=='completed'
    assert result['tool_outcomes']==[{'tool':'get_state','outcome':'allowed'}]
    assert {t['name'] for t in catalogs[0]}==HOME_COMPANION_TOOLS
    assert not companion[0].home_companion_active and home._turn.get() is None


@pytest.mark.parametrize('user_request', [
    'Read sensor.temperature. Do not change any device.',
    "Read the temperature in Alex's office. Do not change any device.",
    'Read only a sensor named exactly "Office temperature". If none matches, do not substitute another sensor.',
])
async def test_separate_requests_start_fresh_but_keep_their_own_tool_results(hass,companion,monkeypatch,user_request):
    transcripts = []
    scripted(monkeypatch, [
        json.dumps({'status':'clarification_needed','message':'Rephrase and resend the complete request with targets and restrictions.'}),
        [('get_state', {'entity_id':'sensor.temperature','projection':{'kind':'compact'}})],
        json.dumps({'status':'completed','message':'23 degrees.'}),
    ], transcripts=transcripts)
    first = payload(await rpc(hass,companion,args={'request':'Read that sensor again.'}))
    assert first['status'] == 'clarification_needed'
    second = payload(await rpc(hass,companion,args={'request':user_request}))
    assert second['status'] == 'completed'
    assert transcripts[1] == [{'role':'user','content':user_request}]
    assert len(transcripts[2]) == 3
    assert transcripts[2][-1]['content'][0]['type'] == 'tool_result'
    assert '23' in json.dumps(transcripts[2][-1])


async def test_real_native_write_resolves_only_selected_light(hass,companion,monkeypatch):
    calls=[]
    async def turn_on(call: ServiceCall):
        calls.append(call.data)
    hass.services.async_register('light','turn_on',turn_on)
    scripted(monkeypatch,[[('light__HassLightSet',{'domain':['light'],'brightness':40})],json.dumps({'status':'completed','message':'Requested brightness applied.'})])
    result=payload(await rpc(hass,companion,args={'request':'Set selected lights to 40%.'}))
    assert result['status']=='completed'
    assert len(calls)==1 and calls[0]['entity_id']==['light.selected']
    assert calls[0]['brightness_pct']==40
    assert not companion[0].agent_recovery.blocks(companion[1].id)


@pytest.mark.parametrize('miss', ['get_state', 'describe_area'])
async def test_discovery_miss_then_grounded_room_control(hass,companion,monkeypatch,miss):
    from homeassistant.helpers import area_registry as ar, entity_registry as er
    area = ar.async_get(hass).async_create('Steve Office')
    registry = er.async_get(hass)
    for part in ('doorway', 'desk'):
        entity = registry.async_get_or_create('light','test',part,suggested_object_id='office_'+part)
        registry.async_update_entity(entity.entity_id,area_id=area.id)
        hass.states.async_set(entity.entity_id,'on',{'friendly_name':'Steve Office Lights '+part.title()})
        companion[1].permissions.entities[entity.entity_id] = PermissionNode('GREEN')
    calls = []
    async def turn_off(call):
        calls.append(call.data)
    hass.services.async_register('homeassistant','turn_off',turn_off)
    failed = {'entity_id':'light.absent'} if miss == 'get_state' else {'area':"Steve's office"}
    scripted(monkeypatch, [[(miss,failed)], [('search_entities',{'area':'Steve Office','domain':'light'})],
        [('intent__HassTurnOff',{'area':'Steve Office','domain':['light']})],
        json.dumps({'status':'completed','message':'Done.'})])
    result = payload(await rpc(hass,companion,args={'request':"tuen off all of steve's office lights"}))
    assert len(calls) == 1, result
    assert set(calls[0]['entity_id']) == {'light.office_doorway','light.office_desk'}
    assert result['status'] == 'completed'


async def test_failed_control_still_blocks_replacement_after_discovery(hass,companion,monkeypatch):
    scripted(monkeypatch, [[('light__HassLightSet',{'name':'light.other','brightness':40})],
        [('search_entities',{'domain':'light'})],
        [('light__HassLightSet',{'name':'light.selected','brightness':40})],
        json.dumps({'status':'completed','message':'Done.'})])
    with patch.object(ServiceRegistry,'async_call',AsyncMock()) as service:
        result = payload(await rpc(hass,companion))
    assert result['status'] == 'refused'
    service.assert_not_awaited()


@pytest.mark.parametrize('name',['read_file','home_request','call_service'])
async def test_internal_hallucinations_and_recursion_refused(hass,companion,monkeypatch,name):
    scripted(monkeypatch,[[(name,{'request':'again'})],json.dumps({'status':'refused','message':'Unavailable.'})])
    with patch.object(ServiceRegistry,'async_call',AsyncMock()) as service:
        result=payload(await rpc(hass,companion))
    assert result['status']=='refused'
    service.assert_not_awaited()
    assert not companion[0].store.get_pending_approvals()


@pytest.mark.parametrize('change',['scope','revoke','settings','account'])
async def test_drift_during_provider_wait_stops_before_write(hass,companion,monkeypatch,change):
    data,access_key,store,account=companion
    async def mutate(_):
        if change=='scope':
            await data.store.async_set_permissions(access_key.id,PermissionTree())
        elif change=='revoke':
            await data.store.async_archive_access_key(access_key.id,revoked=True)
        elif change=='settings':
            await data.store.async_patch_settings(kill_switch=True)
        else:
            await store.delete(account)
    scripted(monkeypatch,[[('light__HassLightSet',{'name':'light.selected','brightness':40})]],before=mutate)
    with patch.object(ServiceRegistry,'async_call',AsyncMock()) as service:
        result=await rpc(hass,companion)
    assert ('error' in result[0]) or payload(result)['status']=='authority_lost'
    service.assert_not_awaited()
    assert not data.home_companion_active


async def test_cancellation_clears_admission(hass,companion,monkeypatch):
    entered=asyncio.Event()
    async def wait(_):
        entered.set()
        await asyncio.Event().wait()
    scripted(monkeypatch,[],before=wait)
    task=asyncio.create_task(rpc(hass,companion))
    await entered.wait()
    assert payload(await rpc(hass,companion))['status']=='busy'
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not companion[0].home_companion_active


@pytest.mark.parametrize('args',[{'request':''},{'request':'x'*4097},{'request':2},{'request':'read','language':[]},{'request':'read','tools':['read_file']}])
async def test_invalid_input_sends_no_provider_request(hass,companion,monkeypatch,args):
    catalogs=scripted(monkeypatch,[])
    assert payload(await rpc(hass,companion,args=args))['status']=='invalid_request'
    assert not catalogs


@pytest.mark.parametrize('mode',['confirm','read_only'])
async def test_mesa_gate_remains_effective_and_approval_ends_batch(hass,companion,monkeypatch,mode):
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    data,access_key,_,_=companion
    await data.store.async_patch_settings(mesa_mode='enforced')
    data.mesa = await async_setup_mesa(hass,'enforced')
    data.mesa.store.set('light.selected',SemanticProfile.from_dict('light.selected',
        {'semantic_profile':{'operational_boundaries':{'control_mode':mode}}},default_origin=MetadataOrigin.USER))
    rounds=[[('light__HassLightSet',{'name':'light.selected','brightness':40}),
             ('fan__HassFanSetSpeed',{'name':'fan.selected','percentage':50})]]
    if mode == 'read_only':
        rounds.append(json.dumps({'status':'completed','message':'Incorrect completion claim.'}))
    scripted(monkeypatch,rounds)
    with patch.object(ServiceRegistry,'async_call',AsyncMock()) as service:
        result=payload(await rpc(hass,companion))
    service.assert_not_awaited()
    if mode=='confirm':
        assert result['status']=='pending_approval' and len(result['review_urls'])==1
        assert len(data.store.get_pending_approvals())==1
        assert len(result['tool_outcomes'])==1
    else:
        assert result['status']=='refused'
        assert not data.store.get_pending_approvals()


async def test_uncertain_prior_write_blocks_new_provider_turn(hass,companion,monkeypatch):
    data,access_key,_,_=companion
    await data.agent_recovery.begin(access_key.id,'light__HassLightSet','old')
    data.agent_recovery.interrupted('old')
    catalogs=scripted(monkeypatch,[])
    result=await rpc(hass,companion)
    assert result[0]['result']['isError']
    assert 'This new request was not executed' in result[0]['result']['content'][0]['text']
    assert not catalogs


async def test_repeated_write_never_reexecutes(hass,companion,monkeypatch):
    calls=[]
    async def turn_on(call):
        calls.append(call.data)
    hass.services.async_register('light','turn_on',turn_on)
    action=('light__HassLightSet',{'name':'light.selected','brightness':40})
    scripted(monkeypatch,[[action],[action],json.dumps({'status':'completed','message':'Done.'})])
    result=payload(await rpc(hass,companion))
    assert len(calls)==1
    assert result['status']=='partial'
    assert result['execution']=='partial'


@pytest.mark.parametrize('answer,expected', [
    ('```json\n{"status":"completed","message":"23 degrees."}\n```', 'completed'),
    ('The value is 23 degrees.\n\n{"status":"completed","message":"23 degrees."}', 'completed'),
    ('{"status":"completed","message":"ok"}\nActually the task failed.', 'invalid_output'),
    ('{"status":"completed","message":"ok"}{"status":"refused","message":"no"}', 'invalid_output'),
    ('{"status":"completed","message":"ok","extra":true}', 'invalid_output'),
    ('{"status":"success","message":"ok"}', 'invalid_output'),
    ('not JSON', 'invalid_output'),
])
async def test_final_envelope_accepts_fences_but_rejects_wrong_shapes(hass,companion,monkeypatch,answer,expected):
    scripted(monkeypatch,[[('get_state',{'entity_id':'sensor.temperature','projection':{'kind':'compact'}})],answer])
    assert payload(await rpc(hass,companion))['status']==expected


async def test_invalid_final_reply_after_control_reports_possible_effects_without_retry(hass,companion,monkeypatch):
    calls = []
    async def turn_on(call):
        calls.append(call.data)
        hass.states.async_set('light.selected','on',{'friendly_name':'Selected lamp'})
    hass.services.async_register('light','turn_on',turn_on)
    scripted(monkeypatch, [[('light__HassLightSet',{'name':'light.selected','brightness':40})],
                          'Malformed final reply.'])
    result = payload(await rpc(hass,companion,args={'request':'Set the selected lamp to 40%.'}))
    assert result['status'] == 'completed'
    assert result['execution'] == 'accepted' and result['verification'] == 'not_verified'
    assert len(calls) == 1 and hass.states.get('light.selected').state == 'on'
    assert result['tool_outcomes'] == [{'tool':'light__HassLightSet','outcome':'allowed'}, {'tool':'get_states','outcome':'allowed'}]
    assert 'accepted' in result['message']
    assert 'Check in Home Assistant' in result['message']
    assert 'do not resend' in result['message']
    assert not companion[0].agent_recovery.blocks(companion[1].id)


async def test_provider_failure_after_write_does_not_replay(hass,companion,monkeypatch):
    calls=[]
    async def turn_on(call):
        calls.append(call.data)
    hass.services.async_register('light','turn_on',turn_on)
    async def fail_after_write(round_number):
        if round_number==2:
            raise RuntimeError('Synthetic provider failure')
    scripted(monkeypatch,[[('light__HassLightSet',{'name':'light.selected','brightness':40})]],before=fail_after_write)
    result=payload(await rpc(hass,companion))
    assert result['status']=='outcome_unknown'
    assert len(calls)==1
    assert not companion[0].home_companion_active


@pytest.mark.parametrize('entity', ['light.other', 'light.nonexistent'])
async def test_failed_reads_explain_name_or_access_without_revealing_hidden_entities(hass,companion,monkeypatch,entity):
    scripted(monkeypatch, [[('get_state', {'entity_id':entity,'projection':{'kind':'compact'}})],
                          json.dumps({'status':'completed','message':'Unsupported claim.'})])
    result = payload(await rpc(hass,companion))
    assert result['status'] == 'refused'
    assert 'name, filters or permissions' in result['message']
    assert 'try again with the complete request' in result['message']
    assert entity not in result['message']


async def test_last_round_reports_completed_write_without_another_tool_batch(hass,companion,monkeypatch):
    calls = []
    async def turn_on(call):
        calls.append(call.data)
    hass.services.async_register('light','turn_on',turn_on)
    read = [('get_state', {'entity_id':'light.selected','projection':{'kind':'compact'}})]
    catalogs = scripted(monkeypatch, [read, read,
        [('light__HassLightSet', {'name':'light.selected','brightness':40})],
        json.dumps({'status':'completed','message':'The brightness command was accepted.'}),
    ])
    result = payload(await rpc(hass,companion))
    assert result['status'] == 'completed'
    assert len(calls) == 1 and len(catalogs) == 4
    assert catalogs[-1] == [] and all(catalogs[:-1])
    assert not companion[0].agent_recovery.blocks(companion[1].id)


@pytest.mark.parametrize('write_first', [False, True])
async def test_final_round_ignoring_no_tools_cannot_act_and_reports_prior_effects(hass,companion,monkeypatch,write_first):
    calls = []
    async def turn_on(call):
        calls.append(call.data)
    hass.services.async_register('light','turn_on',turn_on)
    read = [('get_state', {'entity_id':'light.selected','projection':{'kind':'compact'}})]
    write = [('light__HassLightSet', {'name':'light.selected','brightness':40})]
    catalogs = scripted(monkeypatch, [write if write_first else read, read, read,
        [('light__HassLightSet', {'name':'light.selected','brightness':70})],
    ])
    result = payload(await rpc(hass,companion))
    assert result['status'] == ('completed' if write_first else 'exhausted')
    assert len(catalogs) == 4 and catalogs[-1] == []
    assert len(result['tool_outcomes']) == (4 if write_first else 3)
    assert len(calls) == int(write_first)
    assert not companion[0].agent_recovery.blocks(companion[1].id)
    if write_first:
        assert result['next_step'] == 'check_state'
        assert result['retry_policy'] == 'no_replay'
    else:
        assert 'step limit' in result['message']
        assert 'same complete request' in result['message']
    assert not companion[0].store.get_pending_approvals()


@pytest.mark.parametrize('write_first', [False, True])
async def test_provider_error_after_reads_preserves_only_real_write_uncertainty(hass,companion,monkeypatch,write_first):
    calls = []
    async def turn_on(call):
        calls.append(call.data)
    hass.services.async_register('light','turn_on',turn_on)
    rounds = [[('get_state', {'entity_id':'sensor.temperature','projection':{'kind':'compact'}})],
              {'type':agentcli.EV_ERROR,'code':'network','retryable':True}]
    if write_first:
        rounds.insert(0, [('light__HassLightSet', {'name':'light.selected','brightness':40})])
    catalogs = scripted(monkeypatch, rounds)
    result = payload(await rpc(hass,companion))
    assert result['status'] == ('completed' if write_first else 'provider_error')
    if not write_first:
        assert 'No device command was attempted' in result['message']
    assert result['tool_outcomes'][-1] == {'tool':'get_states' if write_first else 'get_state','outcome':'allowed'}
    assert len(catalogs) == (3 if write_first else 2)
    assert len(calls) == int(write_first)
    assert not companion[0].agent_recovery.blocks(companion[1].id)
    assert not companion[0].store.get_pending_approvals()
    assert not companion[0].home_companion_active


@pytest.mark.parametrize('surface',['voice','chat','ai_text','ai_structured'])
async def test_owned_ingresses_delegate_original_request_once_without_report_reinterpretation(hass,companion,monkeypatch,surface):
    data,access_key,_,_=companion
    catalogs=scripted(monkeypatch,[
        [('get_state',{'entity_id':'sensor.temperature','projection':{'kind':'full'}})],
        json.dumps({'status':'completed','message':'Invented success: lights changed.'}),
    ] + ([json.dumps({'temperature':23})] if surface=='ai_structured' else []))
    provider=agentcli.build_provider(None)
    request="Read the temperature in Alex's office. Do not change devices."
    if surface=='voice':
        answer=await agentcli.async_run_voice_turn(hass,data,access_key,provider,None,'http://test',request)
    elif surface=='chat':
        events=[]
        async def emit(name,value):events.append((name,value))
        messages=await agentcli.async_run_agent_turn(hass=hass,data=data,access_key=access_key,provider=provider,
            session=None,messages=[{'role':'user','content':request}],options={},client_ip='test',base_url='',
            emit=emit,cancel=asyncio.Event())
        answer=messages[-1]['content']
        assert sum(name=='assistant_delta' for name,_ in events)==1
    else:
        answer=await agentcli.async_run_ai_task(hass,data,access_key,provider,None,'',request,
            structure_json={'type':'object','properties':{'temperature':{'type':'number'}}} if surface=='ai_structured' else None)
    if surface=='ai_structured':
        assert json.loads(answer)=={'temperature':23}
        assert catalogs[-1]==[] and len(catalogs)==3
    else:
        assert '23' in answer and 'Invented success' not in answer
        assert len(catalogs)==2
    assert all(len(catalog)!=1 for catalog in catalogs)


async def test_code_readback_confirms_receipt_even_when_final_model_json_is_bad(hass,companion,monkeypatch):
    async def turn_on(call):
        hass.states.async_set('light.selected','on',{'brightness':102,'friendly_name':'Selected lamp'})
    hass.services.async_register('light','turn_on',turn_on)
    scripted(monkeypatch,[[('light__HassLightSet',{'name':'light.selected','brightness':40})],'Bad JSON'])
    report=payload(await rpc(hass,companion))
    assert report['status']=='completed' and report['verification']=='confirmed'
    assert report['next_step']=='none' and report['retry_policy']=='no_replay'
    assert not companion[0].agent_recovery.blocks(companion[1].id)


async def test_chat_cancellation_stops_inner_planner(hass,companion,monkeypatch):
    data,access_key,_,_=companion
    cancel=asyncio.Event()
    entered=asyncio.Event()
    async def wait(_):
        entered.set()
        await asyncio.Event().wait()
    catalogs=scripted(monkeypatch,[],before=wait)
    events=[]
    async def emit(name,value):events.append((name,value))
    task=asyncio.create_task(agentcli.async_run_agent_turn(hass=hass,data=data,access_key=access_key,
        provider=agentcli.build_provider(None),session=None,messages=[{'role':'user','content':'Read temperature'}],
        options={},client_ip='test',base_url='',emit=emit,cancel=cancel))
    await entered.wait()
    cancel.set()
    await task
    assert not data.home_companion_active and len(catalogs)==1
    assert any(name=='error' and value['code']=='cancelled' for name,value in events)


@pytest.mark.parametrize('surface',['voice','chat'])
async def test_companion_uses_wrapper_under_auto_routing_without_announcing_guessed_tools(hass,companion,monkeypatch,surface):
    from custom_components.phoenix_mcp.voice_agent import async_voice_answer
    data,access_key,_,_=companion
    await data.store.async_patch_settings(voice_agent_enabled=True,voice_agent_token_id=access_key.id,
        voice_agent_routing_mode='auto',agentcli_routing_mode='auto')
    monkeypatch.setattr('custom_components.phoenix_mcp.command_routing.async_evaluate_command',
                        AsyncMock(side_effect=AssertionError('Wrapper persona must not use direct dispatch')))
    monkeypatch.setattr('custom_components.phoenix_mcp.chat_routing.async_evaluate_command',
                        AsyncMock(side_effect=AssertionError('Wrapper persona must not use direct dispatch')))
    catalogs=scripted(monkeypatch,[[('get_state',{'entity_id':'sensor.temperature','projection':{'kind':'compact'}})],
        json.dumps({'status':'completed','message':'23 degrees.'})])
    if surface=='voice':
        answer=await async_voice_answer(hass,data,'Read temperature.',language='en')
        assert '23' in answer
    else:
        events=[]
        async def emit(name,value):events.append((name,value))
        messages=await agentcli.async_run_agent_turn(hass=hass,data=data,access_key=access_key,provider=agentcli.build_provider(None),
            session=None,messages=[{'role':'user','content':'Read temperature.'}],options={},client_ip='test',base_url='',
            emit=emit,cancel=asyncio.Event(),routing_language='en')
        assert '23' in messages[-1]['content']
    assert len(catalogs)==2


async def test_house_count_uses_scoped_total_without_enumerating_states(hass, companion, monkeypatch):
    _, access_key, _, _ = companion
    access_key.permissions.domains['light'] = PermissionNode('YELLOW')
    for i in range(150):
        hass.states.async_set(f'light.count_{i}', 'off')
    access_key.permissions.entities['light.other'] = PermissionNode('RED')
    transcripts = []
    scripted(monkeypatch, [
        [('search_entities', {'domain':'light', 'limit':1})],
        json.dumps({'status':'completed','message':'999 lights!', 'read_answer':{'kind':'count','source':0}}),
    ], transcripts=transcripts)
    report = payload(await rpc(hass, companion, args={'request':'how many lights in my house?'}))
    assert report['status']=='completed' and report['execution']=='not_attempted'
    assert '151' in report['message'] and '999' not in report['message']
    assert 'permitted scope' in report['message']
    body, _ = json.JSONDecoder().raw_decode(transcripts[1][-1]['content'][0]['content'][0]['text'])
    assert len(body['entities'])==1 and body['total']==151 and body['truncated']
    assert body['read_answer'] == {'kind': 'count', 'source': 0}
    assert report['tool_outcomes']==[{'tool':'search_entities','outcome':'allowed'}]


@pytest.mark.parametrize('source', [-1, 4, True, '0'])
async def test_count_source_must_reference_received_evidence(hass, companion, monkeypatch, source):
    scripted(monkeypatch, [
        [('search_entities', {'domain':'light', 'limit':1})],
        json.dumps({'status':'completed','message':'999 lights!', 'read_answer':{'kind':'count','source':source}}),
        json.dumps({'status':'completed','message':'999 lights!', 'read_answer':{'kind':'count','source':source}}),
    ])
    report = payload(await rpc(hass, companion))
    assert report['status']=='invalid_output' and '999' not in report['message']


async def test_voice_report_uses_current_style_and_detail(hass, companion, monkeypatch):
    from custom_components.phoenix_mcp.voice_agent import async_voice_answer
    data, access_key, _, account = companion
    await data.store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=access_key.id,
        voice_agent_provider_id=account, voice_agent_routing_mode='off')
    async def turn_on(call):
        hass.states.async_set('light.selected','on', {'friendly_name':'Selected lamp'})
    hass.services.async_register('homeassistant','turn_on',turn_on)
    answers = []
    for style, detail in [('direct','concise'), ('warm','balanced'), ('technical','detailed')]:
        await data.store.async_patch_settings(voice_agent_conversation_style=style, voice_agent_detail_level=detail)
        scripted(monkeypatch, [[('intent__HassTurnOn', {'name':'light.selected','domain':['light']})],
            json.dumps({'status':'completed','message':'invented'})])
        answers.append(await async_voice_answer(hass,data,'Turn on Selected lamp.',language='en'))
    assert len(set(answers))==3
    assert all('Selected lamp' in a for a in answers)
    assert answers[0] == 'Turned on Selected lamp.'
    assert all('accepted' in a for a in answers[1:])
    assert len(answers[0]) < len(answers[1]) < len(answers[2])
    assert 'physical proof' in answers[2]


def test_internal_companion_does_not_receive_external_catalog_reconnect_advice(companion):
    data, access_key, _, _ = companion
    assert access_key.settings_version > access_key.tools_list_version
    response = {'content':[{'type':'text','text':'Area not found.'}]}
    assert not mcp_view._maybe_append_stale_advisory(response,access_key,data,'home_companion')
    assert response['content']==[{'type':'text','text':'Area not found.'}]
    assert mcp_view._maybe_append_stale_advisory(response,access_key,data,'external-client')
    assert any('reconnect' in row['text'] for row in response['content'])


async def test_chat_lookup_failure_is_displayed_once_and_does_not_create_repairs(hass, companion, monkeypatch):
    data, access_key, _, _ = companion
    scripted(monkeypatch, [{'type': agentcli.EV_ERROR, 'code': 'network', 'retryable': True}])
    events = []
    async def emit(name, value):
        events.append((name, value))
    await agentcli.async_run_agent_turn(
        hass=hass, data=data, access_key=access_key, provider=agentcli.build_provider(None), session=None,
        messages=[{'role': 'user', 'content': 'turn on steve lights in the the office doorway'}],
        options={}, client_ip='test', base_url='', emit=emit, cancel=asyncio.Event())
    errors = [value for name, value in events if name == 'error']
    assert len(errors) == 1 and errors[0]['code'] == 'provider_error'
    assert 'No device command was attempted' in errors[0]['message']
    assert not any(name == 'assistant_delta' for name, _ in events)
    assert data.agent_recovery.records(access_key.id) == []


async def test_actual_dispatch_failure_retains_repairs_without_replaying(hass, companion, monkeypatch):
    calls = []
    async def failed_after_effect(call):
        calls.append(call.data)
        hass.states.async_set('light.selected', 'on')
        raise RuntimeError('Uncertain service outcome')
    hass.services.async_register('light', 'turn_on', failed_after_effect)
    scripted(monkeypatch, [[('light__HassLightSet', {'name': 'light.selected', 'brightness': 40})]])
    result = payload(await rpc(hass, companion))
    assert result['status'] == 'outcome_unknown'
    assert len(calls) == 1 and hass.states.get('light.selected').state == 'on'
    assert companion[0].agent_recovery.blocks(companion[1].id)
    assert result['next_step'] == 'resolve_recovery'


@pytest.mark.parametrize('after_read', [False, True])
@pytest.mark.parametrize('code', ['provider_malformed', 'bad_tool_call', 'provider_retry_exhausted'])
async def test_malformed_planner_correction_preserves_reads_without_replaying(hass, companion, monkeypatch, after_read, code):
    rounds = [[('get_state', {'entity_id': 'sensor.temperature', 'projection': {'kind': 'full'}})]] if after_read else []
    rounds += [{'type': agentcli.EV_ERROR, 'code': code, 'retryable': False, 'diagnostic': {'category': 'bad_tool_call'}}]
    if not after_read:
        rounds += [[('get_state', {'entity_id': 'sensor.temperature', 'projection': {'kind': 'full'}})]]
    rounds += [json.dumps({'status': 'completed', 'message': '23 degrees.'})]
    transcripts = []
    catalogs = scripted(monkeypatch, rounds, transcripts=transcripts)
    report = payload(await rpc(hass, companion))
    assert report['status'] == 'completed'
    assert len(catalogs) == 3
    corrected = transcripts[2 if after_read else 1]
    assert 'malformed' in corrected[-1]['content']
    assert corrected[0]['content'] == 'Read the temperature.'
    assert report['tool_outcomes'] == [{'tool': 'get_state', 'outcome': 'allowed'}]
    assert companion[0].agent_recovery.records(companion[1].id) == []


async def test_repeated_malformed_planner_does_not_loop(hass, companion, monkeypatch):
    failure = {'type': agentcli.EV_ERROR, 'code': 'provider_malformed', 'retryable': False}
    catalogs = scripted(monkeypatch, [failure, failure])
    report = payload(await rpc(hass, companion))
    assert report['status'] == 'provider_error' and len(catalogs) == 2
    assert report['execution'] == 'not_attempted'


@pytest.mark.parametrize('invalid', [
    ('intent__HassTurnOff', {'entity_id': 'light.selected'}),
    ('light__HassLightSet', {'name': 'light.selected', 'brightness': 101}),
    ('fan__HassFanSetSpeed', {'name': 'fan.selected', 'percentage': 'fast'}),
])
async def test_invalid_native_proposal_is_corrected_before_claiming_any_write(hass, companion, monkeypatch, invalid):
    data, access_key, _, _ = companion
    calls, transcripts = [], []
    async def turn_on(call):
        calls.append(call.data)
        hass.states.async_set('light.selected', 'on', {'friendly_name': 'Selected lamp'})
    hass.services.async_register('homeassistant', 'turn_on', turn_on)
    original = 'Turn on Selected lamp.'
    scripted(monkeypatch, [
        [invalid, ('intent__HassTurnOn', {'name': 'light.selected'})],
        [('intent__HassTurnOn', {'name': 'light.selected'})],
        json.dumps({'status': 'completed', 'message': 'Finished.'}),
    ], transcripts=transcripts)
    with patch.object(data.agent_recovery, 'begin', wraps=data.agent_recovery.begin) as claim:
        report = payload(await rpc(hass, companion, args={'request': original}))
    assert report['status'] == 'completed' and report['verification'] == 'confirmed'
    assert len(calls) == 1 and claim.call_count == 1
    assert transcripts[1][0]['content'] == original
    errors = transcripts[1][-1]['content']
    assert len(errors) == 2 and all(row['is_error'] for row in errors)
    assert all('None of the tools in this batch ran' in json.dumps(row['content']) for row in errors)
    assert data.agent_recovery.records(access_key.id) == []


async def test_repeated_invalid_native_proposals_never_claim_or_attempt_a_write(hass, companion, monkeypatch):
    catalogs = scripted(monkeypatch, [[('intent__HassTurnOff', {'entity_id': 'light.selected'})]] * 2)
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion))
    assert len(catalogs) == 2 and report['execution'] == 'not_attempted'
    assert report['status'] == 'provider_error'
    service.assert_not_awaited()
    assert companion[0].agent_recovery.records(companion[1].id) == []


async def test_invalid_proposal_after_received_control_keeps_receipt_without_repairs(hass, companion, monkeypatch):
    calls = []
    async def turn_on(call):
        calls.append(call.data)
        hass.states.async_set('light.selected', 'on', {'friendly_name': 'Selected lamp'})
    hass.services.async_register('homeassistant', 'turn_on', turn_on)
    catalogs = scripted(monkeypatch, [
        [('intent__HassTurnOn', {'name': 'light.selected'})],
        [('intent__HassTurnOff', {'entity_id': 'light.selected'})],
    ])
    report = payload(await rpc(hass, companion))
    assert len(catalogs) == 2 and len(calls) == 1
    assert report['execution'] == 'partial' and report['status'] == 'partial'
    assert companion[0].agent_recovery.records(companion[1].id) == []


@pytest.mark.parametrize('language,utterance', [
    ('en', 'Turn those lights off.'), ('en', 'Switch it off.'),
    ('de', 'Schalte diese Lampen aus.'), ('es', 'Apaga esas luces.'),
    ('fr', 'Éteins ces lumières.'), ('it', 'Spegni quelle luci.'),
    ('nl', 'Zet die lichten uit.'), ('pl', 'Wyłącz te światła.'),
    ('ru', 'Выключи эти светильники.'), ('zh-Hans', '关掉那些灯。'),
    ('zh-Hant', '關掉那些燈。'), ('ja', 'それらのライトを消して。'), ('ko', '그것들을 꺼 주세요.'),
])
async def test_context_only_request_cannot_actuate_even_an_already_off_device(hass, companion, monkeypatch, language, utterance):
    catalogs = scripted(monkeypatch, [[('intent__HassTurnOff', {'domain': ['light']})]])
    data, access_key, _, _ = companion
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion, args={'request': utterance, 'language': language}))
    assert len(catalogs) == 1
    assert report['status'] == 'clarification_needed' and report['execution'] == 'not_attempted'
    service.assert_not_awaited()
    assert not data.store.get_pending_approvals() and not data.agent_recovery.records(access_key.id)


@pytest.mark.parametrize('domain,noun', [
    ('switch', 'switch'), ('climate', 'climate'), ('climate', 'thermostat'),
    ('cover', 'cover'), ('cover', 'blind'), ('valve', 'valve'),
    ('media_player', 'media player'), ('vacuum', 'vacuum'), ('humidifier', 'humidifier'),
    ('input_boolean', 'input boolean'),
])
async def test_household_device_nouns_cannot_supply_missing_context(hass, companion, monkeypatch, domain, noun):
    data, access_key, _, _ = companion
    eid = domain + '.household'
    hass.states.async_set(eid, 'off', {'friendly_name': 'Household ' + noun})
    access_key.permissions.entities[eid] = PermissionNode('GREEN')
    access_key.cap_physical_control = 'allow'
    scripted(monkeypatch, [[('intent__HassTurnOff', {'name': eid})]])
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion, args={'request': 'Turn that ' + noun + ' off.'}))
    assert report['status'] == 'clarification_needed' and report['execution'] == 'not_attempted'
    service.assert_not_awaited()
    assert not data.store.get_pending_approvals() and not data.agent_recovery.records(access_key.id)


@pytest.mark.parametrize('utterance,expected', [
    ('Open the office cover.', True), ('Open the office covers.', False),
    ('Open those covers.', True),
])
async def test_plural_household_targets_do_not_invent_context_or_a_single_selection(hass, companion, utterance, expected):
    _, access_key, _, _ = companion
    access_key.cap_physical_control = 'allow'
    for part in ('left', 'right'):
        eid = 'cover.office_' + part
        hass.states.async_set(eid, 'closed', {'friendly_name': 'Office cover ' + part})
        access_key.permissions.entities[eid] = PermissionNode('GREEN')
    context = home._turn.set(home.CompanionTurn(access_key.id, request=utterance))
    try:
        assert home.ambiguous_control(access_key, hass, 'intent__HassSetPosition') is expected
    finally:
        home._turn.reset(context)


@pytest.mark.parametrize('utterance,alias', [('Turn this Selected lamp on.', None), ('Turn those Sunbeam lights on.', 'Sunbeam')])
async def test_context_reference_with_explicit_scoped_name_or_alias_can_execute(hass, companion, monkeypatch, utterance, alias):
    from homeassistant.helpers import entity_registry as er
    if alias:
        hass.states.async_remove('light.selected')
        entry = er.async_get(hass).async_get_or_create('light', 'test', 'selected', suggested_object_id='selected')
        assert entry.entity_id == 'light.selected'
        hass.states.async_set('light.selected', 'off', {'friendly_name': 'Selected lamp'})
        er.async_get(hass).async_update_entity(entry.entity_id, aliases=[alias])
    async def turn_on(call):
        hass.states.async_set('light.selected', 'on', {'friendly_name': 'Selected lamp'})
    hass.services.async_register('homeassistant', 'turn_on', turn_on)
    scripted(monkeypatch, [[('intent__HassTurnOn', {'name': 'light.selected'})], json.dumps({'status': 'completed', 'message': 'Finished.'})])
    assert payload(await rpc(hass, companion, args={'request': utterance}))['status'] == 'completed'


@pytest.mark.parametrize('code', ['bad_tool_call', 'provider_malformed'])
async def test_malformed_planning_after_control_never_requests_correction(hass, companion, monkeypatch, code):
    calls = []
    async def turn_on(call):
        calls.append(call.data)
        hass.states.async_set('light.selected', 'on', {'brightness': 102, 'friendly_name': 'Selected lamp'})
    hass.services.async_register('light', 'turn_on', turn_on)
    catalogs = scripted(monkeypatch, [
        [('light__HassLightSet', {'name': 'light.selected', 'brightness': 40})],
        {'type': agentcli.EV_ERROR, 'code': code, 'retryable': False},
    ])
    report = payload(await rpc(hass, companion))
    assert len(catalogs) == 2 and len(calls) == 1
    assert report['status'] == 'completed' and report['verification'] == 'confirmed'
    assert report['retry_policy'] == 'no_replay'
    assert companion[0].agent_recovery.records(companion[1].id) == []


@pytest.mark.parametrize('utterance,expected', [
    ("tuen on steve's office light", 'clarification_needed'),
    ('turn on steve lights in the the office doorway', 'completed'),
    ('turn on steve office doowway light', 'completed'),
    ("tuen on all of steve's office lights", 'completed'),
])
async def test_tied_natural_target_evidence_cannot_be_replaced_with_one_candidate(hass, companion, monkeypatch, utterance, expected):
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er
    data, access_key, _, _ = companion
    area = ar.async_get(hass).async_create('Steve Office')
    targets = []
    for part in ('doorway', 'desk'):
        entry = er.async_get(hass).async_get_or_create('light', 'test', part, suggested_object_id='steve_office_lights_'+part)
        er.async_get(hass).async_update_entity(entry.entity_id, area_id=area.id)
        hass.states.async_set(entry.entity_id, 'off', {'friendly_name': 'Steve Office Lights '+part.title()})
        access_key.permissions.entities[entry.entity_id] = PermissionNode('GREEN')
        targets.append(entry.entity_id)
    calls = []
    async def turn_on(call):
        calls.append(call.data)
        for eid in call.data['entity_id']:
            hass.states.async_set(eid, 'on', hass.states.get(eid).attributes)
    hass.services.async_register('homeassistant', 'turn_on', turn_on)
    group = 'all of' in utterance
    proposed = {'area': 'Steve Office', 'domain': ['light']} if group else {'name': targets[0]}
    scripted(monkeypatch, [
        [('search_entities', {'domain': 'light', 'query': 'steve office'})],
        [('intent__HassTurnOn', proposed)],
        json.dumps({'status': 'completed', 'message': 'Accepted.'}),
    ])
    report = payload(await rpc(hass, companion, args={'request': utterance}))
    assert report['status'] == expected
    assert len(calls) == int(expected == 'completed')
    if calls:
        assert set(calls[0]['entity_id']) == set(targets if group else targets[:1])
        assert all(hass.states.get(eid).state == 'on' for eid in calls[0]['entity_id'])
    if expected == 'clarification_needed':
        assert report['execution'] == 'not_attempted' and 'complete request' in report['message']
        assert not data.store.get_pending_approvals()
    assert not data.agent_recovery.records(access_key.id)


async def test_room_targets_follow_registry_area_even_when_names_suggest_other_rooms(hass, companion, monkeypatch):
    from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    config = MockConfigEntry(domain='test')
    config.add_to_hass(hass)
    area = ar.async_get(hass).async_create('Steve Office')
    device = dr.async_get(hass).async_get_or_create(config_entry_id=config.entry_id, identifiers={('test', 'office')})
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    targets = []
    for key, label, in_room in [('desk', 'Steve Office Lights Desk', True),
                                ('stairs', 'Stairs Hallway', True),
                                ('garage', 'Garage Switch Qwerty Lights', True),
                                ('unassigned', 'Steve Office LED 1', False)]:
        entry = er.async_get(hass).async_get_or_create('light', 'test', key, suggested_object_id=key,
                                                       device_id=device.id if in_room else None)
        hass.states.async_set(entry.entity_id, 'unavailable' if key == 'garage' else 'on', {'friendly_name': label})
        companion[1].permissions.entities[entry.entity_id] = PermissionNode('GREEN')
        if in_room:
            targets.append(entry.entity_id)
    calls = []
    async def turn_off(call):
        calls.append(call.data)
        for eid in call.data['entity_id']:
            previous = hass.states.get(eid)
            if previous.state != 'unavailable':
                hass.states.async_set(eid, 'off', previous.attributes)
    hass.services.async_register('homeassistant', 'turn_off', turn_off)
    await companion[0].store.async_patch_settings(voice_agent_detail_level='concise')
    scripted(monkeypatch, [
        [('intent__HassTurnOff', {'area': area.name, 'domain': ['light']})],
        [('get_states', {'entity_ids': targets})],
        json.dumps({'status': 'completed', 'message': 'All done.'}),
    ])
    report = payload(await rpc(hass, companion, args={'request': 'turn off lights in steve office'}))
    assert len(calls) == 1 and set(calls[0]['entity_id']) == set(targets)
    assert report['message'].startswith('Turned off 2 lights in Steve Office.')
    assert 'Unavailable (1): Garage Switch Qwerty Lights.' in report['message']
    assert report['verification'] == 'not_verified'
    assert not companion[0].agent_recovery.records(companion[1].id)


@pytest.mark.parametrize('malformed', ['not JSON', '{"status":"completed","message":"","read_answer":{"kind":"count","source":0}}',
                                       '{"status":"completed","message":"Two lights","read_answer":{"kind":"count","source":999}}'])
async def test_invalid_count_report_gets_one_tools_free_correction(hass, companion, monkeypatch, malformed):
    transcripts = []
    catalogs = scripted(monkeypatch, [[('search_entities', {'domain': 'light', 'limit': 1})], malformed,
                        json.dumps({'status': 'completed', 'message': 'Invented 999.', 'read_answer': {'kind': 'count', 'source': 0}})],
                        transcripts=transcripts)
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion, args={'request': 'How many lights in my house?'}))
    assert report['status'] == 'completed' and '1 matching light entities' in report['message']
    assert '999' not in report['message'] and report['execution'] == 'not_attempted'
    assert len(catalogs) == 3 and catalogs[-1] == []
    assert 'did not pass validation' in json.dumps(transcripts[-1])
    service.assert_not_awaited()
    assert not companion[0].agent_recovery.records(companion[1].id)


@pytest.mark.parametrize('second', ['malformed again', [('intent__HassTurnOn', {'name': 'light.selected'})]])
async def test_count_report_correction_cannot_dispatch_or_repeat(hass, companion, monkeypatch, second):
    catalogs = scripted(monkeypatch, [[('search_entities', {'domain': 'light', 'limit': 1})], 'malformed', second])
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion, args={'request': 'How many lights in my house?'}))
    assert report['status'] in {'invalid_output', 'exhausted'}
    assert report['execution'] == 'not_attempted'
    assert len(catalogs) == 3 and catalogs[-1] == []
    service.assert_not_awaited()
    assert not companion[0].agent_recovery.records(companion[1].id)


@pytest.mark.parametrize('mode', ['off', 'observe', 'advise'])
@pytest.mark.parametrize('discovery', ['search_entities', 'get_capability_summary'])
async def test_companion_never_creates_or_calls_decision_review(hass, companion, monkeypatch, discovery, mode):
    from custom_components.phoenix_mcp.decision_provider import DecisionProvider

    data, access_key, store, _ = companion
    account = await store.add('typesafe', {'api_key': 'decision-test-key', 'model': 'jev-latest'})
    await data.store.async_patch_settings(decision_provider_id=account, voice_agent_tool_routing_mode=mode)
    applied = []

    async def turn_on(call):
        applied.append(call.data)
        hass.states.async_set('light.selected', 'on', {'friendly_name': 'Selected lamp', 'brightness': 102})

    hass.services.async_register('light', 'turn_on', turn_on)
    discovery_args = {'domain': 'light'} if discovery == 'search_entities' else {}
    scripted(monkeypatch, [[(discovery, discovery_args)],
        [('light__HassLightSet', {'domain': ['light'], 'brightness': 40})],
        json.dumps({'status': 'completed', 'message': 'Done.'})])
    choose = AsyncMock(return_value={'tool': 'get_history', 'model': 'synthetic-review-model'})
    with patch.object(DecisionProvider, 'select_tool', choose), \
            patch('custom_components.phoenix_mcp.tool_routing.create_review_state', side_effect=AssertionError('Companion must skip review state')), \
            patch('custom_components.phoenix_mcp.tool_routing.async_review_tools', side_effect=AssertionError('Companion must skip review')):
        result = payload(await rpc(hass, companion, args={'request': 'Set selected lights to 40%.'}))

    assert result['status'] == 'completed'
    assert len(applied) == 1 and applied[0]['entity_id'] == ['light.selected']
    assert applied[0]['brightness_pct'] == 40
    assert not data.store.get_pending_approvals() and not data.agent_recovery.blocks(access_key.id)
    choose.assert_not_awaited()
    assert data.decision_shadow.summary().get('tool_routing', {}) == {}


@pytest.mark.parametrize('domain', ['switch', 'climate', 'cover', 'valve', 'media_player', 'vacuum', 'timer'])
async def test_household_domains_follow_read_write_and_red_scope(hass, companion, domain):
    _, access_key, _, _ = companion
    eid = domain + '.household'
    hass.states.async_set(eid, 'off')
    access_key.permissions.domains[domain] = PermissionNode('YELLOW')
    assert resolve(eid, access_key, hass) == Permission.READ
    access_key.permissions.domains[domain] = PermissionNode('GREEN')
    assert resolve(eid, access_key, hass) == Permission.WRITE
    access_key.permissions.entities[eid] = PermissionNode('GREEN')
    access_key.permissions.domains[domain] = PermissionNode('RED')
    assert resolve(eid, access_key, hass) == Permission.DENY


@pytest.mark.parametrize('cap', ['cap_registry_read', 'cap_search'])
def test_household_catalog_is_capability_filtered_but_cannot_add_authoring(companion, cap):
    data, access_key, _, _ = companion
    context = home._turn.set(home.CompanionTurn(access_key.id))
    try:
        enabled = {tool['name'] for tool in agentcli.build_agent_tool_list(access_key, data, 'voice')}
        setattr(access_key, cap, 'deny')
        narrowed = {tool['name'] for tool in agentcli.build_agent_tool_list(access_key, data, 'voice')}
        assert narrowed < enabled
        assert enabled == HOME_COMPANION_TOOLS
        access_key.cap_filesystem = access_key.cap_automation_write = access_key.cap_restart = 'allow'
        assert {tool['name'] for tool in agentcli.build_agent_tool_list(access_key, data, 'voice')} == narrowed
    finally:
        home._turn.reset(context)


HOUSEHOLD_ACTIONS = [
    ('switch', 'intent__HassTurnOn', {'name': 'switch.household'}, 'homeassistant', 'turn_on', 'on', {}),
    ('climate', 'climate__HassClimateSetTemperature', {'name': 'climate.household', 'temperature': 21}, 'climate', 'set_temperature', 'heat', {'temperature': 21}),
    ('cover', 'intent__HassSetPosition', {'name': 'cover.household', 'position': 40}, 'cover', 'set_cover_position', 'open', {'current_position': 40}),
    ('media_player', 'media_player__HassSetVolume', {'name': 'media_player.household', 'volume_level': 30}, 'media_player', 'volume_set', 'playing', {'volume_level': .3}),
    ('vacuum', 'vacuum__HassVacuumStart', {'name': 'vacuum.household'}, 'vacuum', 'start', 'cleaning', {'supported_features': 8192}),
]


@pytest.mark.parametrize('domain,tool,args,service_domain,service_name,final_state,attrs', HOUSEHOLD_ACTIONS)
@pytest.mark.parametrize('narration_fails', [False, True])
async def test_household_receipts_readback_and_provider_failure_do_not_replay(hass, companion, monkeypatch,
        domain, tool, args, service_domain, service_name, final_state, attrs, narration_fails):
    data, access_key, _, _ = companion
    await data.store.async_patch_access_key(access_key.id, cap_physical_control='allow')
    eid = domain + '.household'
    access_key.permissions.entities[eid] = PermissionNode('GREEN')
    hass.states.async_set(eid, 'off', {'friendly_name': 'Household device', **attrs})
    applied = []

    async def operate(call):
        applied.append(call.data)
        hass.states.async_set(eid, final_state, {'friendly_name': 'Household device', **attrs})

    hass.services.async_register(service_domain, service_name, operate)
    ending = {'type': agentcli.EV_ERROR, 'code': 'provider_error', 'message': 'Failed.'} if narration_fails else json.dumps({'status': 'completed', 'message': 'Everything physically worked.'})
    scripted(monkeypatch, [[(tool, args)], ending])
    report = payload(await rpc(hass, companion, args={'request': 'Operate ' + eid + ' with the requested settings.'}))
    assert len(applied) == 1 and applied[0]['entity_id'] == [eid]
    assert report['execution'] == 'accepted' and report['retry_policy'] == 'no_replay'
    assert report['verification'] == ('not_verified' if domain == 'vacuum' else 'confirmed')
    assert 'physically' not in report['message']
    assert not data.agent_recovery.blocks(access_key.id)


@pytest.mark.parametrize('mode,expected', [('allow', 'completed'), ('confirm', 'pending_approval'), ('deny', 'refused')])
async def test_companion_cover_capability_gate_is_preserved(hass, companion, monkeypatch, mode, expected):
    data, access_key, _, _ = companion
    await data.store.async_patch_access_key(access_key.id, cap_physical_control=mode)
    access_key.permissions.entities['cover.household'] = PermissionNode('GREEN')
    hass.states.async_set('cover.household', 'closed', {'friendly_name': 'Household cover', 'current_position': 0})
    args = {'name': 'cover.household', 'position': 40}
    scripted(monkeypatch, [[('intent__HassSetPosition', args)], json.dumps({'status': 'completed', 'message': 'Done.'})])
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion, args={'request': 'Set cover.household to 40 percent.'}))
    assert report['status'] == expected
    assert service.await_count == (mode == 'allow')
    assert bool(data.store.get_pending_approvals()) == (mode == 'confirm')
    assert not data.agent_recovery.blocks(access_key.id)


@pytest.mark.parametrize('tool,args', [
    ('climate__HassClimateSetTemperature', {'name': 'climate.household', 'temperature': True}),
    ('intent__HassSetPosition', {'name': 'cover.household', 'position': 101}),
    ('media_player__HassSetVolume', {'name': 'media_player.household', 'volume_level': -1}),
    ('media_player__HassSetVolumeRelative', {'name': 'media_player.household', 'volume_step': True}),
    ('media_player__HassMediaPause', {'name': 'media_player.household', 'domain': ['light']}),
    ('media_player__HassMediaSearchAndPlay', {'name': 'media_player.household'}),
    ('vacuum__HassVacuumCleanArea', {}),
    ('intent__HassCancelAllTimers', {'area': []}),
    ('intent__HassStopMoving', {'domain': ['valve']}),
])
async def test_invalid_household_proposals_are_withheld_before_write_journaling(hass, companion, monkeypatch, tool, args):
    data, access_key, _, _ = companion
    scripted(monkeypatch, [[(tool, args)], [(tool, args)]])
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion))
    assert report['execution'] == 'not_attempted'
    service.assert_not_awaited()
    assert not data.store.get_pending_approvals() and not data.agent_recovery.records(access_key.id)


@pytest.mark.parametrize('utterance,proposed,extra,expected', [
    ('turn on steve office doowway light', 'doorway', None, False),
    ('turn on steve office doowway light', 'desk', None, True),
    ('turn on steve office light', 'doorway', None, True),
    ('turn on exactly steve office doowway light', 'doorway', None, True),
    ('turn on steve office doowway light', 'doorway', 'dootway', True),
])
async def test_single_typo_corroborates_only_unique_explicit_target(hass, companion, utterance, proposed, extra, expected):
    _, access_key, _, _ = companion
    for part in ['doorway', 'desk'] + ([extra] if extra else []):
        eid = 'light.steve_office_' + part
        hass.states.async_set(eid, 'off', {'friendly_name': 'Steve Office Lights ' + part.title()})
        access_key.permissions.entities[eid] = PermissionNode('GREEN')
    context = home._turn.set(home.CompanionTurn(access_key.id, request=utterance))
    try:
        assert home.ambiguous_control(access_key, hass, 'intent__HassTurnOn', {'name': 'light.steve_office_' + proposed}) is expected
    finally:
        home._turn.reset(context)


async def test_ambiguity_guard_records_withheld_proposal_and_selected_provider(hass, companion, monkeypatch):
    from custom_components.phoenix_mcp.conversation_log import recorder
    data, access_key, _, _ = companion
    await data.store.async_patch_settings(conversation_log_enabled=True)
    for part in ('doorway', 'desk'):
        eid = 'light.office_' + part
        hass.states.async_set(eid, 'off', {'friendly_name': 'Office light ' + part})
        access_key.permissions.entities[eid] = PermissionNode('GREEN')
    scripted(monkeypatch, [[('intent__HassTurnOn', {'name': 'light.office_doorway'})]])
    with patch.object(ServiceRegistry, 'async_call', AsyncMock()) as service:
        report = payload(await rpc(hass, companion, args={'request': 'Turn on the office light'}))
    assert report['status'] == 'clarification_needed'
    service.assert_not_awaited()
    log = recorder(data)
    entry = log.detail(log.query()['entries'][0]['id'])
    error = next(event['payload'] for event in entry['events'] if event['kind'] == 'error')
    assert error['stage'] == 'target_guard' and error['executed'] is False
    assert error['arguments'] == {'name': 'light.office_doorway'}
    route = next(event['payload'] for event in entry['events'] if event['kind'] == 'routing')
    assert route['route'] == 'provider' and route['provider'] == 'claude' and route['model'] == 'test'
    assert route['result'] == 'completed'
