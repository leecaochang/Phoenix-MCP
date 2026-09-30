"""Reports distinguish dispatch receipts, later observations and replay boundaries."""
import json

import pytest

from custom_components.phoenix_mcp.home_companion_response import ResponseEvidence, build_report
from custom_components.phoenix_mcp.const import HOME_COMPANION_MAX_REPORT_ENTITIES


def result(body, *, error=False):
    return {'content':[{'type':'text','text':json.dumps(body)}], 'isError':error}


def receipt(ids=('light.one',), failed=()):
    return result({'response_type':'action_done', 'data':{
        'success':[{'type':'entity','id':eid,'name':'Visible '+eid} for eid in ids], 'failed':list(failed)}})


def state(evidence, **attrs):
    evidence.observe('get_state','allowed',result({'entity_id':'light.one','state':'on','attributes':attrs}))


@pytest.mark.parametrize('name,args,attrs,confirmed', [
    ('intent__HassTurnOn',{}, {},True),
    ('intent__HassTurnOff',{}, {},False),
    ('light__HassLightSet',{'brightness':40},{'brightness':102},True),
    ('light__HassLightSet',{'brightness':40},{'brightness':160},False),
    ('light__HassLightSet',{'brightness':40},{},False),
    ('light__HassLightSet',{'color':'blue'},{'rgb_color':[0,0,255]},True),
    ('light__HassLightSet',{'color':'blue'},{'rgb_color':[255,0,0]},False),
    ('light__HassLightSet',{'temperature':3000},{'color_temp_kelvin':3000},True),
    ('light__HassLightSet',{'brightness':40,'color':'blue'},{'brightness':102},False),
    ('fan__HassFanSetSpeed',{'percentage':50},{'percentage':50},True),
    ('fan__HassFanSetSpeed',{'percentage':50},{'percentage':40},False),
])
def test_attribute_evidence_is_required_for_settings(name,args,attrs,confirmed):
    evidence=ResponseEvidence()
    evidence.observe(name,'allowed',receipt(),args)
    state(evidence,**attrs)
    report=build_report('completed','Everything physically worked!',evidence,'en')
    assert report['execution']=='accepted'
    assert (report['verification']=='confirmed') is confirmed
    assert report['retry_policy']=='no_replay'
    assert report['next_step']==('none' if confirmed else 'check_state')
    assert 'physically' not in report['message']


def test_reads_before_or_between_controls_do_not_verify_later_effects():
    evidence=ResponseEvidence()
    state(evidence)
    evidence.observe('intent__HassTurnOn','allowed',receipt(),{})
    assert not evidence.verified
    state(evidence)
    assert evidence.verified
    evidence.observe('intent__HassTurnOff','allowed',receipt(),{})
    assert not evidence.verified


@pytest.mark.parametrize('status',['invalid_output','provider_error','output_limit','exhausted','truncated'])
def test_received_receipt_survives_narrative_failure_without_claiming_verification(status):
    evidence=ResponseEvidence()
    evidence.observe('intent__HassTurnOn','allowed',receipt(),{})
    report=build_report(status,'Resend the command.',evidence,'en')
    assert report['status']=='completed'
    assert report['execution']=='accepted' and report['verification']=='not_verified'
    assert report['next_step']=='check_state' and report['retry_policy']=='no_replay'
    assert 'Resend the command.' not in report['message']
    unknown=build_report(status,'',evidence,'en',recovery_required=True)
    assert unknown['status']=='outcome_unknown' and unknown['next_step']=='resolve_recovery'


@pytest.mark.parametrize('failed', [[{'type':'entity','id':'light.two'}], []])
def test_partial_receipts_and_pending_after_success_never_claim_whole_task_completion(failed):
    evidence=ResponseEvidence()
    evidence.observe('intent__HassTurnOn','allowed',receipt(failed=failed),{})
    if not failed:
        evidence.observe('intent__HassTurnOn','pending_approval',result({}),{})
    report=build_report('completed','All done.',evidence,'en')
    assert report['execution']=='partial'
    assert report['status']==('partial' if failed else 'pending_approval')
    assert report['next_step']==('check_state' if failed else 'review_approval')
    assert 'All done' not in report['message']


def test_malformed_receipt_is_unknown_instead_of_accepted():
    evidence=ResponseEvidence()
    evidence.observe('intent__HassTurnOn','allowed',result({'data':'bad'}),{})
    report=build_report('completed','Success!',evidence,'en')
    assert report['execution']=='uncertain' and report['status']=='outcome_unknown'
    assert report['next_step']=='check_state'


def test_authority_loss_omits_old_labels_and_warnings():
    evidence=ResponseEvidence()
    evidence.observe('intent__HassTurnOn','allowed',receipt(),{})
    evidence.warnings=['Private warning']
    report=build_report('authority_lost','Visible light.one',evidence,'en')
    assert 'light.one' not in report['message'] and 'Private' not in report['message']
    assert report['execution']=='uncertain' and report['retry_policy']=='no_replay'


def test_evidence_overflow_never_silently_becomes_verified():
    evidence=ResponseEvidence()
    ids=tuple('light.e'+str(i) for i in range(HOME_COMPANION_MAX_REPORT_ENTITIES+1))
    evidence.observe('intent__HassTurnOn','allowed',receipt(ids),{})
    evidence.observe('get_states','allowed',result([{'entity_id':eid,'state':'on'} for eid in ids]))
    assert len(evidence.targets)==HOME_COMPANION_MAX_REPORT_ENTITIES
    assert not evidence.verified
    assert build_report('completed','',evidence,'en')['status']=='partial'


def test_code_owned_clarification_and_read_summary_reject_invented_success():
    evidence=ResponseEvidence()
    report=build_report('completed','I turned everything on.',evidence,'en')
    assert report['status']=='clarification_needed' and report['next_step']=='full_request'
    assert 'action, target' not in report['message'] or 'complete' in report['message']
    state(evidence,unit_of_measurement='W')
    report=build_report('completed','I changed another device.',evidence,'en')
    assert report['execution']=='not_attempted' and 'changed' not in report['message']
    assert 'light.one: on W' in report['message']


@pytest.mark.parametrize('detail', ['concise','balanced','detailed'])
def test_styles_change_prose_without_changing_execution_facts(detail):
    evidence = ResponseEvidence()
    evidence.observe('intent__HassTurnOn','allowed',receipt(),{})
    state(evidence)
    reports = [build_report('completed','invented',evidence,'en',style=style,detail=detail)
               for style in ['direct','warm','calm_guide','lively','technical']]
    assert len({r['message'] for r in reports})==(1 if detail=='concise' else 5)
    facts = [{k:v for k,v in r.items() if k!='message'} for r in reports]
    assert all(f==facts[0] for f in facts)
    assert all('light.one' in r['message'] for r in reports)
    assert all(('Turned on' if detail=='concise' else 'accepted') in r['message'] for r in reports)


def test_zero_count_is_valid_and_does_not_claim_home_wide_visibility():
    evidence = ResponseEvidence()
    evidence.observe('search_entities','allowed',result({'entities':[], 'count':0, 'total':0, 'truncated':False}))
    assert evidence.select_read_answer({'kind':'count','source':0})
    report = build_report('completed','Nothing in the entire house',evidence,'en')
    assert report['status']=='completed' and '0' in report['message']
    assert 'permitted scope' in report['message'] and 'entire house' not in report['message']


@pytest.mark.parametrize('style', ['direct', 'warm', 'calm_guide', 'lively', 'technical'])
def test_count_detail_describes_scope_instead_of_one_arbitrary_search_row(style):
    evidence = ResponseEvidence()
    evidence.observe('search_entities', 'allowed', result({
        'entities': [{'entity_id': 'light.tall', 'friendly_name': 'Tall Lamp'}],
        'count': 1, 'total': 44, 'truncated': True,
    }), {'domain': 'light', 'limit': 1})
    assert evidence.select_read_answer({'kind': 'count', 'source': 0})
    reports = [build_report('completed', 'There are 999 bulbs.', evidence, 'en',
                            style=style, detail=detail)
               for detail in ['concise', 'balanced', 'detailed']]
    assert all('44 matching light entities' in report['message'] for report in reports)
    assert all('Tall Lamp' not in report['message'] and '999' not in report['message'] for report in reports)
    assert 'groups' not in reports[0]['message']
    assert 'groups' in reports[1]['message']
    assert 'physical devices' in reports[2]['message']
    assert len(reports[0]['message']) < len(reports[1]['message']) < len(reports[2]['message'])


@pytest.mark.parametrize('domain,kind', [('light','light entities'), (['fan'],'fan entities'),
                                       (['light','fan'],'entities'), (None,'entities')])
def test_count_kind_comes_from_the_selected_admitted_search(domain,kind):
    evidence = ResponseEvidence()
    for selected_domain in ['sensor', domain]:
        evidence.observe('search_entities','allowed',result({'entities':[], 'total':7}),
                         {'domain':selected_domain})
    assert evidence.select_read_answer({'kind':'count','source':1})
    report = build_report('completed','7 sensors',evidence,'en',detail='concise')
    assert f'7 matching {kind}' in report['message']


def test_concise_success_omits_advisory_without_omitting_required_next_steps():
    evidence = ResponseEvidence()
    evidence.observe('intent__HassTurnOn','allowed',receipt(),{})
    evidence.warnings = ['MESA advisory: this would need approval in enforced mode.']
    concise = build_report('completed','',evidence,'en',detail='concise')
    assert 'MESA advisory' not in concise['message']
    assert concise['next_step']=='check_state'
    assert 'accepted' in concise['message']
    for detail in ['balanced','detailed']:
        assert 'MESA advisory' in build_report('completed','',evidence,'en',detail=detail)['message']
    evidence.pending = 1
    pending = build_report('pending_approval','',evidence,'en',detail='concise')
    assert pending['next_step']=='review_approval' and 'approval' in pending['message']


@pytest.mark.parametrize('detail', ['concise', 'balanced', 'detailed'])
def test_room_readback_distinguishes_confirmed_lights_from_unavailable_exception(detail):
    evidence = ResponseEvidence()
    ids = tuple('light.room_'+str(index) for index in range(7))
    evidence.observe('intent__HassTurnOff', 'allowed', receipt(ids), {})
    evidence.targets[ids[-1]] = 'Garage Switch Qwerty Lights'
    evidence.areas = dict.fromkeys(ids, 'Steve Office')
    evidence.observe('get_states', 'allowed', result([
        {'entity_id': eid, 'state': 'unavailable' if eid == ids[-1] else 'off'} for eid in ids]))
    report = build_report('completed', 'Everything worked.', evidence, 'en', style='warm', detail=detail)
    assert report['execution'] == 'accepted' and report['verification'] == 'not_verified'
    assert report['next_step'] == 'check_state' and report['retry_policy'] == 'no_replay'
    assert 'Unavailable (1): Garage Switch Qwerty Lights.' in report['message']
    assert 'do not resend' in report['message'] and 'Repairs' not in report['message']
    assert 'final device states were not verified' not in report['message']
    if detail == 'concise':
        assert report['message'].startswith('Turned off 6 lights in Steve Office.')
        assert 'light.room_' not in report['message']
    else:
        assert 'requested settings for 6 of 7 targets' in report['message']


def test_room_summary_requires_a_shared_canonical_area_for_every_summarized_target():
    evidence = ResponseEvidence()
    ids = ('light.one', 'light.two')
    evidence.observe('intent__HassTurnOn', 'allowed', receipt(ids), {})
    evidence.observe('get_states', 'allowed', result([{'entity_id': eid, 'state': 'on'} for eid in ids]))
    for areas in [{}, {ids[0]: 'Office'}, {ids[0]: 'Office', ids[1]: 'Hallway'}]:
        evidence.areas = areas
        message = build_report('completed', '', evidence, 'en', detail='concise')['message']
        assert 'Visible light.one, Visible light.two' in message
        assert 'in Office' not in message
    evidence.areas = dict.fromkeys(ids, 'Office')
    assert build_report('completed', '', evidence, 'en', detail='concise')['message'] == 'Turned on 2 lights in Office.'


def test_mismatch_missing_read_and_bounded_exception_names_do_not_disappear():
    evidence = ResponseEvidence()
    ids = tuple('light.room_'+str(index) for index in range(11))
    evidence.observe('intent__HassTurnOn', 'allowed', receipt(ids), {})
    evidence.observe('get_state', 'allowed', result({'entity_id': ids[0], 'state': 'off'}))
    report = build_report('completed', '', evidence, 'en', detail='concise')
    assert 'Not verified (11):' in report['message']
    assert 'Turned on' not in report['message'] and report['verification'] == 'not_verified'


@pytest.mark.parametrize('tool,eid,args,device_state,attrs', [
    ('climate__HassClimateSetTemperature', 'climate.one', {'temperature': 21}, 'heat', {'temperature': 20}),
    ('intent__HassTurnOn', 'climate.one', {}, 'heat', {}),
    ('intent__HassSetPosition', 'cover.one', {'position': 40}, 'open', {'current_position': 100}),
    ('media_player__HassMediaNext', 'media_player.one', {}, 'playing', {}),
    ('media_player__HassSetVolumeRelative', 'media_player.one', {'volume_step': 10}, 'playing', {'volume_level': .5}),
])
def test_household_state_alone_does_not_prove_requested_settings(tool, eid, args, device_state, attrs):
    evidence = ResponseEvidence()
    evidence.observe(tool, 'allowed', receipt((eid,)), args)
    evidence.observe('get_state', 'allowed', result({'entity_id': eid, 'state': device_state, 'attributes': attrs}))
    report = build_report('completed', 'All settings verified.', evidence, 'en')
    assert report['execution'] == 'accepted' and report['verification'] == 'not_verified'
    assert report['retry_policy'] == 'no_replay' and report['next_step'] == 'check_state'
    assert 'All settings verified.' not in report['message']


@pytest.mark.parametrize('count', [0, 2])
def test_timer_receipt_cannot_verify_other_controls_or_accept_invented_counts(count):
    evidence = ResponseEvidence()
    evidence.observe('intent__HassCancelAllTimers', 'allowed', result({'response_type': 'action_done',
        'data': {'success': [], 'failed': []}, 'speech_slots': {'canceled': count}}))
    evidence.observe('intent__HassTurnOn', 'allowed', receipt(), {})
    state(evidence)
    report = build_report('completed', 'Canceled 999 timers.', evidence, 'en')
    assert report['execution'] == 'accepted' and report['verification'] == 'not_verified'
    assert '999' not in report['message'] and report['next_step'] == 'check_state'


@pytest.mark.parametrize('tool,eid,args,attrs', [
    ('climate__HassClimateSetTemperature', 'climate.one', {'temperature': 0}, {'temperature': False}),
    ('media_player__HassSetVolume', 'media_player.one', {'volume_level': 0}, {'volume_level': False}),
    ('media_player__HassMediaPlayerMute', 'media_player.one', {}, {'is_volume_muted': 1}),
])
def test_wrong_shaped_household_attributes_cannot_verify_settings(tool, eid, args, attrs):
    evidence = ResponseEvidence()
    evidence.observe(tool, 'allowed', receipt((eid,)), args)
    evidence.observe('get_state', 'allowed', result({'entity_id': eid, 'state': 'on', 'attributes': attrs}))
    assert not evidence.verified


@pytest.mark.parametrize('slots', [None, 'bad', [], {'canceled': True}])
def test_malformed_timer_receipt_never_claims_acceptance(slots):
    evidence = ResponseEvidence()
    evidence.observe('intent__HassCancelAllTimers', 'allowed', result({'response_type': 'action_done',
        'data': {'success': [], 'failed': []}, 'speech_slots': slots}))
    report = build_report('completed', 'Done.', evidence, 'en')
    assert report['execution'] == 'uncertain' and report['verification'] == 'not_verified'
