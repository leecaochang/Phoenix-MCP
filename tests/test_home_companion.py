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
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree, TokenRecord


@pytest.fixture
async def companion(hass, token_store):
    token, _ = await token_store.async_create_token('companion', 'test')
    await token_store.async_patch_token(token.id, persona='home_companion', **get_persona_caps('home_companion'))
    token.permissions = PermissionTree(entities={
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
    data = PhoenixData(store=token_store,rate_limiter=MagicMock(),audit=MagicMock(),hass=hass)
    hass.data[DOMAIN] = data
    await token_store.async_patch_settings(mesa_mode='off')
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add('chatgpt',{'api_key':'synthetic-key','model':'test-model'})
    hass.data['_phoenix_agentcli_store'] = store
    await token_store.async_patch_settings(voice_agent_provider_id=account,voice_agent_model='test-model')
    yield data, token, store, account
    if data.sensor_flush_cancel:
        data.sensor_flush_cancel()


async def rpc(hass, companion, name='phoenix_request', args=None, method='tools/call'):
    data, token, _, _ = companion
    return await mcp_view._dispatch_mcp(method,'id',{'name':name,'arguments':args or {'request':'Read the temperature.'}},token,hass,data,'test','http://test')


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
    data,token,_,_=companion
    await data.store.async_patch_token(token.id,cap_log_read='allow',announce_all_tools=True,pass_through=True)
    assert token.persona == 'home_companion'
    assert not token.pass_through and not token.announce_all_tools
    assert TokenRecord.from_dict(token.to_storage_dict()).persona == 'home_companion'
    assert detect_persona(get_persona_caps('home_companion')) != 'home_companion'


async def test_single_external_catalog_and_no_resource_backdoor(hass,companion):
    result = await rpc(hass,companion,method='tools/list')
    assert [t['name'] for t in result[0]['result']['tools']] == ['phoenix_request']
    assert [t['name'] for t in agentcli.build_agent_tool_list(companion[1],companion[0],'chat')] == ['phoenix_request']
    for method in ['resources/list','prompts/list','resources/templates/list']:
        assert list((await rpc(hass,companion,method=method))[0]['result'].values()) == [[]]
    for method in ['resources/read','prompts/get']:
        assert 'error' in (await rpc(hass,companion,method=method))[0]


@pytest.mark.parametrize('name',['get_state','read_file','call_service','HassTurnOn','intent__HassTurnOn'])
async def test_guessed_external_calls_never_dispatch(hass,companion,name):
    with patch.object(ServiceRegistry,'async_call',AsyncMock()) as service:
        result = await rpc(hass,companion,name,{'entity_id':'light.selected'})
    assert result[3] == 'denied'
    assert result[0]['result']['isError']
    service.assert_not_awaited()
    assert not companion[0].store.get_pending_approvals()


async def test_scope_honors_inherited_grants_and_excludes_sensitive_classes(hass,companion):
    _,token,_,_=companion
    token.permissions.domains = {d:PermissionNode('GREEN') for d in ('light','sensor','lock')}
    token.permissions.entities['lock.door'] = PermissionNode('GREEN')
    token.permissions.entities['sensor.location'] = PermissionNode('GREEN')
    assert resolve('light.selected',token,hass) == Permission.WRITE
    assert resolve('light.other',token,hass) == Permission.WRITE
    assert resolve('sensor.temperature',token,hass) == Permission.READ
    for entity in ('lock.door','sensor.location'):
        assert resolve(entity,token,hass) == Permission.NO_ACCESS
    token.permissions.domains['light'] = PermissionNode('RED')
    assert resolve('light.selected',token,hass) == Permission.DENY


@pytest.mark.parametrize('grant', ['YELLOW', 'GREEN'])
async def test_domain_grants_discover_lights_and_gate_native_writes(hass,companion,monkeypatch,grant):
    _, token, _, _ = companion
    token.permissions = PermissionTree(
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


@pytest.mark.parametrize('name',['read_file','phoenix_request','call_service'])
async def test_internal_hallucinations_and_recursion_refused(hass,companion,monkeypatch,name):
    scripted(monkeypatch,[[(name,{'request':'again'})],json.dumps({'status':'refused','message':'Unavailable.'})])
    with patch.object(ServiceRegistry,'async_call',AsyncMock()) as service:
        result=payload(await rpc(hass,companion))
    assert result['status']=='refused'
    service.assert_not_awaited()
    assert not companion[0].store.get_pending_approvals()


@pytest.mark.parametrize('change',['scope','revoke','settings','account'])
async def test_drift_during_provider_wait_stops_before_write(hass,companion,monkeypatch,change):
    data,token,store,account=companion
    async def mutate(_):
        if change=='scope':
            await data.store.async_set_permissions(token.id,PermissionTree())
        elif change=='revoke':
            await data.store.async_archive_token(token.id,revoked=True)
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
    data,token,_,_=companion
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
    data,token,_,_=companion
    await data.agent_recovery.begin(token.id,'light__HassLightSet','old')
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
    assert result['status']=='refused'


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
    scripted(monkeypatch,[answer])
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
    assert result['status'] == 'invalid_output'
    assert len(calls) == 1 and hass.states.get('light.selected').state == 'on'
    assert result['tool_outcomes'] == [{'tool':'light__HassLightSet','outcome':'allowed'}]
    assert 'may already have changed' in result['message']
    assert 'Check its current state in Home Assistant' in result['message']
    assert 'Do not resend' in result['message']
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
    assert 'name or search filters' in result['message']
    assert 'may not have access' in result['message']
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
    assert result['status'] == 'exhausted'
    assert len(catalogs) == 4 and catalogs[-1] == []
    assert len(result['tool_outcomes']) == 3
    assert len(calls) == int(write_first)
    assert companion[0].agent_recovery.blocks(companion[1].id) is write_first
    if write_first:
        assert 'may already have changed' in result['message']
        assert 'Do not repeat' in result['message']
    else:
        assert 'too many steps' in result['message']
        assert 'try again with one complete request' in result['message']
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
    assert result['status'] == ('outcome_unknown' if write_first else 'provider_error')
    if not write_first:
        assert 'No device action started' in result['message']
    assert result['tool_outcomes'][-1] == {'tool':'get_state','outcome':'allowed'}
    assert len(catalogs) == (3 if write_first else 2)
    assert len(calls) == int(write_first)
    assert companion[0].agent_recovery.blocks(companion[1].id) is write_first
    assert not companion[0].store.get_pending_approvals()
    assert not companion[0].home_companion_active
