"""Calendar identity, snapshots, approval replay and uncertain-effect contracts."""

import asyncio
from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent, DATA_COMPONENT, EVENT_START, EVENT_END
from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import TokenStore
from custom_components.phoenix_mcp.tools import calendar as tools


class WritableCalendar(CalendarEntity):
    """Real HA event types with observable integration dispatch boundaries."""

    _attr_supported_features = CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.UPDATE_EVENT | CalendarEntityFeature.DELETE_EVENT

    def __init__(self):
        self.events = []
        self.calls = []
        self.entity_id = 'calendar.test'

    @property
    def event(self):
        return self.events[0] if self.events else None

    async def async_get_events(self, hass, start_date, end_date):
        return [event for event in self.events if event.start_datetime_local < end_date and event.end_datetime_local > start_date]

    async def async_create_event(self, **kwargs):
        self.calls.append(('create', kwargs))
        self.events.append(CalendarEvent(**{("start" if key == EVENT_START else "end" if key == EVENT_END else key): value for key, value in kwargs.items()}, uid=f'uid-{len(self.calls)}'))

    async def async_update_event(self, uid, event, **selectors):
        self.calls.append(('update', uid, selectors, event))
        old = next(item for item in self.events if item.uid == uid)
        self.events[self.events.index(old)] = CalendarEvent(**{("start" if key == EVENT_START else "end" if key == EVENT_END else key): value for key, value in event.items()}, uid=uid, recurrence_id=selectors.get('recurrence_id'))

    async def async_delete_event(self, uid, **selectors):
        self.calls.append(('delete', uid, selectors))
        self.events = [event for event in self.events if event.uid != uid]


@pytest.fixture
async def calendar_env(hass):
    store = TokenStore(hass, AsyncMock())
    token, _ = await store.async_create_token('calendar-tests', 'admin', rate_limit_requests=0)
    token.pass_through = True
    token.cap_calendar_write = 'allow'
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    entity = WritableCalendar()
    registry = er.async_get(hass)
    registry.async_get_or_create('calendar', 'local_calendar', 'calendar-test', suggested_object_id='test')
    hass.states.async_set('calendar.test', 'off')
    hass.data[DATA_COMPONENT] = SimpleNamespace(get_entity=lambda eid: entity if eid == entity.entity_id else None)
    return hass, data, token, entity


def payload(result):
    return json.loads(result[0]['content'][0]['text'])


async def call(env, name, args):
    hass, data, token, _ = env
    return await mcp_view._call_tool(name, args, token, hass, data)


CREATE = {'entity_id': 'calendar.test', 'event': {'summary': 'Appointment', 'start': '2026-10-02', 'end': '2026-10-03', 'description': 'Original', 'location': 'Office'}}


async def selection(env):
    body = payload(await call(env, 'get_calendar_events', {'entity_id': 'calendar.test', 'start_time': '2026-10-01', 'end_time': '2026-10-04'}))
    row = body['events'][0]
    return {'entity_id': 'calendar.test', 'uid': row['uid'], 'expected_hash': row['content_hash'], 'start_time': '2026-10-01', 'end_time': '2026-10-04'}


async def test_create_uid_readback_and_durable_receipt(calendar_env):
    result = await call(calendar_env, 'create_calendar_event', CREATE)
    assert result[1] == 'allowed'
    body = payload(result)
    assert body['status'] == 'applied' and body['after']['uid']
    assert body['before'] is None
    receipt = await call(calendar_env, 'get_calendar_write', {'entity_id': 'calendar.test', 'receipt_id': body['receipt_id']})
    assert payload(receipt)['after']['uid'] == body['after']['uid']
    journal = tools.CalendarWriteJournal(calendar_env[0])
    records = await journal.load()
    assert records[0]['status'] == 'applied'


async def test_patch_preserves_omissions_and_null_clears(calendar_env):
    await call(calendar_env, 'create_calendar_event', CREATE)
    args = await selection(calendar_env)
    result = await call(calendar_env, 'update_calendar_event', {**args, 'mode': 'patch', 'event': {'summary': 'Changed', 'location': None}})
    assert result[1] == 'allowed'
    body = payload(result)
    assert body['before']['description'] == 'Original'
    assert body['after']['description'] == 'Original'
    assert body['after'].get('location', '') == ''
    assert body['after']['uid'] == args['uid']


async def test_replace_clears_optional_fields_and_delete_keeps_uid(calendar_env):
    await call(calendar_env, 'create_calendar_event', CREATE)
    result = await call(calendar_env, 'update_calendar_event', {**await selection(calendar_env), 'mode': 'replace', 'event': {'summary': 'Replaced', 'start': '2026-10-02', 'end': '2026-10-03'}})
    assert result[1] == 'allowed'
    assert payload(result)['after'].get('description', '') == ''
    result = await call(calendar_env, 'delete_calendar_event', await selection(calendar_env))
    assert result[1] == 'allowed'
    assert payload(result)['before']['uid'] and payload(result)['after'] is None
    assert calendar_env[3].events == []


@pytest.mark.parametrize('event', [None, [], {'summary': True, 'start': '2026-10-02', 'end': '2026-10-03'}, {'summary': 'x', 'start': '2026-10-02T12:00:00', 'end': '2026-10-02T13:00:00'}, {'summary': 'x', 'start': '2026-10-02', 'end': '2026-10-02'}, {'summary': 'x', 'start': '2026-10-02', 'end': '2026-10-03', 'rrule': 'FREQ=HOURLY'}])
async def test_invalid_event_refuses_before_approval(calendar_env, event):
    calendar_env[2].cap_calendar_write = 'confirm'
    result = await call(calendar_env, 'create_calendar_event', {**CREATE, 'event': event})
    assert result[1] == 'invalid_request'
    assert not calendar_env[1].store.get_pending_approvals()
    assert not calendar_env[3].calls


async def test_capability_deny_survives_passthrough(calendar_env):
    calendar_env[2].cap_calendar_write = 'deny'
    result = await call(calendar_env, 'create_calendar_event', {'event': []})
    assert result[1] == 'denied'
    assert result[0]['content'][0]['text'] == 'Forbidden.'
    result = await call(calendar_env, 'call_service', {'service': {'domain': 'calendar', 'name': 'create_event'}, 'targets': [{'kind': 'entity', 'ids': ['calendar.test']}]})
    assert result[1] == 'denied'
    assert not calendar_env[3].calls


async def test_generic_calendar_write_cannot_bypass_receipt(calendar_env):
    result = await call(calendar_env, 'call_service', {'service': {'domain': 'calendar', 'name': 'create_event'}, 'targets': [{'kind': 'entity', 'ids': ['calendar.test']}]})
    assert result[1] == 'invalid_request'
    assert 'dedicated calendar' in result[0]['content'][0]['text']


async def test_integration_feature_check(calendar_env):
    calendar_env[3]._attr_supported_features = CalendarEntityFeature.DELETE_EVENT
    assert (await call(calendar_env, 'create_calendar_event', CREATE))[1] == 'invalid_request'
    assert not calendar_env[3].calls


async def test_stale_approval_and_identity_refuse(calendar_env):
    await call(calendar_env, 'create_calendar_event', CREATE)
    args = {**await selection(calendar_env), 'mode': 'patch', 'event': {'summary': 'Changed'}}
    calendar_env[2].cap_calendar_write = 'confirm'
    assert (await call(calendar_env, 'update_calendar_event', args))[1] == 'pending_approval'
    approval = calendar_env[1].store.get_pending_approvals()[0]
    calendar_env[3].events[0].summary = 'External edit'
    result = await mcp_view.async_execute_approved_tool('update_calendar_event', approval["args"], calendar_env[2], calendar_env[0], calendar_env[1])
    assert result[1] == 'invalid_request'
    assert len(calendar_env[3].calls) == 1
    er.async_get(calendar_env[0]).async_update_entity('calendar.test', new_unique_id='replacement')
    result = await mcp_view.async_execute_approved_tool('update_calendar_event', approval["args"], calendar_env[2], calendar_env[0], calendar_env[1])
    assert result[1] == 'denied'


async def test_confirm_executes_once_with_frozen_payload(calendar_env):
    calendar_env[2].cap_calendar_write = 'confirm'
    assert (await call(calendar_env, 'create_calendar_event', CREATE))[1] == 'pending_approval'
    approval = calendar_env[1].store.get_pending_approvals()[0]
    assert not calendar_env[3].calls
    result = await mcp_view.async_execute_approved_tool('create_calendar_event', approval["args"], calendar_env[2], calendar_env[0], calendar_env[1])
    assert result[1] == 'allowed'
    assert len(calendar_env[3].calls) == 1


async def test_recurring_selector_spelling_and_forwarding(calendar_env):
    entity = calendar_env[3]
    entity.events = [CalendarEvent(start=date(2026, 10, 2), end=date(2026, 10, 3), summary='Series', uid='series', recurrence_id='20261002', rrule='FREQ=DAILY')]
    args = await selection(calendar_env)
    result = await call(calendar_env, 'delete_calendar_event', args)
    assert result[1] == 'invalid_request'
    result = await call(calendar_env, 'delete_calendar_event', {**args, 'recurrence_id': '20261002', 'recurrence_range': 'THIS_AND_FUTURE'})
    assert result[1] == 'invalid_request'
    result = await call(calendar_env, 'delete_calendar_event', {**args, 'recurrence_id': '20261002', 'recurrence_range': 'THISANDFUTURE'})
    assert result[1] == 'allowed'
    assert entity.calls[-1] == ('delete', 'series', {'recurrence_id': '20261002', 'recurrence_range': 'THISANDFUTURE'})


async def test_mesa_denial_is_before_receipt_or_effect(calendar_env, monkeypatch):
    gate = AsyncMock(return_value=SimpleNamespace(decision='deny'))
    monkeypatch.setattr(tools, 'async_apply_mesa_to_call', gate)
    assert (await call(calendar_env, 'create_calendar_event', CREATE))[1] == 'denied'
    assert not calendar_env[3].calls
    assert gate.call_args.kwargs['require_all'] is True
    assert gate.call_args.kwargs['approval_tool_name'] == 'create_calendar_event'
    assert gate.call_args.kwargs['domain'] == 'calendar'
    assert gate.call_args.kwargs['service'] == 'create_event'


async def test_cancelled_dispatch_keeps_unknown_receipt(calendar_env, monkeypatch):
    async def interrupted(**kwargs):
        records = await calendar_env[1].calendar_writes.load()
        assert records[-1]['status'] == 'outcome_unknown'
        raise asyncio.CancelledError
    monkeypatch.setattr(calendar_env[3], 'async_create_event', interrupted)
    with pytest.raises(asyncio.CancelledError):
        await call(calendar_env, 'create_calendar_event', CREATE)
    rows = payload(await call(calendar_env, 'get_calendar_write', {'entity_id': 'calendar.test'}))['writes']
    assert len(rows) == 1 and rows[0]['status'] == 'outcome_unknown'


async def test_receipt_is_token_and_identity_scoped(calendar_env):
    body = payload(await call(calendar_env, 'create_calendar_event', CREATE))
    token, _ = await calendar_env[1].store.async_create_token('other-calendar', 'admin', rate_limit_requests=0)
    token.pass_through = True
    result = await mcp_view._call_tool('get_calendar_write', {'entity_id': 'calendar.test', 'receipt_id': body['receipt_id']}, token, calendar_env[0], calendar_env[1])
    assert result[1] == 'not_found'
    er.async_get(calendar_env[0]).async_update_entity('calendar.test', new_unique_id='replaced')
    assert (await call(calendar_env, 'get_calendar_write', {'entity_id': 'calendar.test', 'receipt_id': body['receipt_id']}))[1] == 'not_found'


async def test_event_readback_scrubs_hidden_references(calendar_env):
    calendar_env[3].events = [CalendarEvent(start=date(2026, 10, 2), end=date(2026, 10, 3), summary='sensor.hidden appointment', uid='identity')]
    row = payload(await call(calendar_env, 'get_calendar_events', {'entity_id': 'calendar.test', 'start_time': '2026-10-01', 'end_time': '2026-10-04'}))['events'][0]
    assert 'sensor.hidden' not in row['summary']
    assert row['uid'] == 'identity'
    assert row['content_hash'] == content_hash(calendar_env[3].events[0].as_dict())


@pytest.mark.parametrize('tool_name', ['get_calendar_events', 'create_calendar_event'])
async def test_integration_validation_error_scrubs_secrets_and_hidden_entities(calendar_env, monkeypatch, tool_name):
    error = ValueError('Failed for sensor.hidden at https://username:private-secret@example.com/')
    read = AsyncMock(side_effect=error)
    monkeypatch.setattr(calendar_env[3], 'async_get_events', read)
    args = CREATE if tool_name == 'create_calendar_event' else {'entity_id': 'calendar.test'}
    result = await call(calendar_env, tool_name, args)
    assert result[1] == 'invalid_request'
    text = result[0]['content'][0]['text']
    assert text.startswith('Failed for')
    assert 'sensor.hidden' not in text and 'private-secret' not in text
    read.assert_awaited_once()
    assert not calendar_env[3].calls


async def test_authority_changed_during_event_read_blocks_write(calendar_env, monkeypatch):
    from custom_components.phoenix_mcp.request_authority import RequestAuthority, AuthorityChangedError, request_authority, policy_version
    hass, data, token, entity = calendar_env
    data.ready = True
    original = entity.async_get_events
    async def change_after_read(*args):
        rows = await original(*args)
        token.settings_version += 1
        return rows
    monkeypatch.setattr(entity, 'async_get_events', change_after_read)
    authority = request_authority.set(RequestAuthority(hass, data, token.id, token.token_hash))
    epoch = policy_version.set(token.settings_version)
    try:
        with pytest.raises(AuthorityChangedError):
            await call(calendar_env, 'create_calendar_event', CREATE)
    finally:
        request_authority.reset(authority)
        policy_version.reset(epoch)
    assert not entity.calls


async def test_failed_intent_save_never_dispatches(calendar_env, monkeypatch):
    journal = tools._calendar_journal(calendar_env[1], calendar_env[0])
    monkeypatch.setattr(journal.store, 'async_save', AsyncMock(side_effect=OSError('private write failure')))
    result = await call(calendar_env, 'create_calendar_event', CREATE)
    assert result[1] == 'invalid_request'
    assert 'No write was dispatched' in result[0]['content'][0]['text']
    assert not calendar_env[3].calls


async def test_late_mesa_change_stops_dispatch(calendar_env, monkeypatch):
    gate = AsyncMock(side_effect=[SimpleNamespace(decision='allow'), SimpleNamespace(decision='deny')])
    monkeypatch.setattr(tools, 'async_apply_mesa_to_call', gate)
    assert (await call(calendar_env, 'create_calendar_event', CREATE))[1] == 'denied'
    assert not calendar_env[3].calls


async def test_readback_failure_does_not_repeat_successful_dispatch(calendar_env, monkeypatch):
    original = calendar_env[3].async_get_events
    async def read(*args):
        if calendar_env[3].calls:
            raise OSError('private read failure')
        return await original(*args)
    monkeypatch.setattr(calendar_env[3], 'async_get_events', read)
    result = await call(calendar_env, 'create_calendar_event', CREATE)
    assert result[1] == 'invalid_request'
    assert payload(result)['status'] == 'applied'
    assert len(calendar_env[3].calls) == 1
    assert payload(await call(calendar_env, 'get_calendar_write', {'entity_id': 'calendar.test', 'receipt_id': payload(result)['receipt_id']}))['status'] == 'applied'


async def test_moved_event_readback_uses_new_window(calendar_env):
    await call(calendar_env, 'create_calendar_event', CREATE)
    result = await call(calendar_env, 'update_calendar_event', {**await selection(calendar_env), 'mode': 'patch', 'event': {'start': '2026-11-02', 'end': '2026-11-03'}})
    assert result[1] == 'allowed'
    assert payload(result)['after']['start'] == '2026-11-02'


async def test_bounds_are_pre_effect(calendar_env):
    assert (await call(calendar_env, 'get_calendar_events', {'entity_id': 'calendar.test', 'start_time': '2026-10-01', 'end_time': '2026-12-01'}))[1] == 'invalid_request'
    result = await call(calendar_env, 'create_calendar_event', {**CREATE, 'event': {**CREATE['event'], 'description': 'x' * (tools.CALENDAR_EVENT_MAX_BYTES + 1)}})
    assert result[1] == 'invalid_request'
    assert not calendar_env[3].calls


async def test_generic_calendar_discovery_and_preview_agree(calendar_env):
    async def no_op(call):
        raise AssertionError('Preview must not dispatch')
    calendar_env[0].services.async_register('calendar', 'create_event', no_op)
    body = payload(await call(calendar_env, 'find_available_actions', {'entity_id': 'calendar.test'}))
    action = next(action for action in body['actions'] if action['service'] == 'calendar.create_event')
    assert action['available'] is False and 'dedicated calendar' in action['reason']
    result = await call(calendar_env, 'dry_run_service', {'service': {'domain': 'calendar', 'name': 'create_event'}, 'targets': [{'kind': 'entity', 'ids': ['calendar.test']}]})
    assert result[1] == 'invalid_request'


@pytest.fixture
async def real_calendar_env(calendar_env):
    from homeassistant.components.local_calendar.calendar import LocalCalendarEntity
    from ical.calendar import Calendar
    hass, data, token, _ = calendar_env
    entity = LocalCalendarEntity(AsyncMock(), Calendar(), 'Test', 'local-test')
    entity.hass = hass
    entity.entity_id = 'calendar.test'
    entity.async_update_ha_state = AsyncMock()
    hass.data[DATA_COMPONENT] = SimpleNamespace(get_entity=lambda eid: entity if eid == entity.entity_id else None)
    return hass, data, token, entity


async def test_real_local_calendar_replacement_clears_optional_strings(real_calendar_env):
    calendar_env = real_calendar_env
    assert (await call(calendar_env, 'create_calendar_event', CREATE))[1] == 'allowed'
    result = await call(calendar_env, 'update_calendar_event', {**await selection(calendar_env), 'mode': 'replace', 'event': {'summary': 'Replaced', 'start': '2026-10-02', 'end': '2026-10-03'}})
    assert result[1] == 'allowed'
    after = payload(result)['after']
    assert after.get('description', '') == '' and after.get('location', '') == ''


@pytest.mark.parametrize('operation', ['update', 'delete'])
async def test_real_recurring_range_and_edited_exception_refusal(real_calendar_env, operation):
    env = real_calendar_env
    assert (await call(env, 'create_calendar_event', {**CREATE, 'event': {**CREATE['event'], 'rrule': 'FREQ=DAILY;COUNT=3'}}))[1] == 'allowed'

    async def read():
        result = await call(env, 'get_calendar_events', {'entity_id': 'calendar.test', 'start_time': '2026-10-01', 'end_time': '2026-10-07'})
        return sorted(payload(result)['events'], key=lambda row: row['start'])

    def select(row):
        return {'entity_id': 'calendar.test', 'uid': row['uid'], 'recurrence_id': row['recurrence_id'], 'expected_hash': row['content_hash'], 'start_time': '2026-10-01', 'end_time': '2026-10-07'}

    rows = await read()
    assert len(rows) == 3
    result = await call(env, 'update_calendar_event', {**select(rows[1]), 'mode': 'patch', 'recurrence_range': 'THISANDFUTURE', 'event': {'summary': 'Future'}})
    assert result[1] == 'allowed'
    rows = await read()
    assert [row['summary'] for row in rows] == ['Appointment', 'Future', 'Future']
    result = await call(env, 'update_calendar_event', {**select(rows[1]), 'mode': 'patch', 'event': {'summary': 'Exception'}})
    assert result[1] == 'allowed'
    rows = await read()
    assert [row['summary'] for row in rows] == ['Appointment', 'Exception', 'Future']
    assert not rows[1].get('rrule')
    args = {**select(rows[1]), 'recurrence_range': 'THISANDFUTURE'}
    if operation == 'update':
        args.update(mode='patch', event={'summary': 'Refused'})
    result = await call(env, f'{operation}_calendar_event', args)
    assert result[1] == 'invalid_request'
    assert 'edited exception' in result[0]['content'][0]['text']
    assert await read() == rows


async def test_recurrence_rule_removal_is_refused(calendar_env):
    calendar_env[3].events = [CalendarEvent(start=date(2026, 10, 2), end=date(2026, 10, 3), summary='Series', uid='series', recurrence_id='20261002', rrule='FREQ=DAILY')]
    args = {**await selection(calendar_env), 'recurrence_id': '20261002'}
    for mode, event in [('patch', {'rrule': None}), ('replace', {'summary': 'X', 'start': '2026-10-02', 'end': '2026-10-03'})]:
        result = await call(calendar_env, 'update_calendar_event', {**args, 'mode': mode, 'event': event})
        assert result[1] == 'invalid_request'
        assert 'no validated recurrence-rule clear value' in result[0]['content'][0]['text']
    assert not calendar_env[3].calls


async def test_unknown_receipt_capacity_requires_explicit_review_without_replay(calendar_env):
    from custom_components.phoenix_mcp.const import CALENDAR_RECEIPT_LIMIT
    hass, data, _token, entity = calendar_env
    journal = tools.CalendarWriteJournal(hass)
    data.calendar_writes = journal
    journal.records = [{"receipt_id": f"r-{i}", "entity_id": entity.entity_id,
                        "status": "outcome_unknown", "operation": "create"}
                       for i in range(CALENDAR_RECEIPT_LIMIT)]
    await journal.save()
    journal.publish_issues()
    blocked = await call(calendar_env, 'create_calendar_event', CREATE)
    assert blocked[1] == "invalid_request" and entity.calls == []
    assert len(journal.records) == CALENDAR_RECEIPT_LIMIT
    journal.active.add("r-0")
    assert await journal.reviewed("r-0") is False
    journal.active.clear()
    assert await journal.reviewed("r-0") is True
    assert entity.calls == []
    persisted = tools.CalendarWriteJournal(hass)
    assert (await persisted.load())[0]["status"] == "reviewed"
    assert await journal.reviewed("r-0") is False
    result = await call(calendar_env, 'create_calendar_event', CREATE)
    assert result[1] == "allowed" and len(entity.calls) == 1
    assert not any(row["receipt_id"] == "r-0" for row in journal.records)
    assert all(any(row["receipt_id"] == f"r-{i}" for row in journal.records) for i in range(1, CALENDAR_RECEIPT_LIMIT))


async def test_calendar_review_persistence_failure_keeps_unknown_receipt(hass):
    journal = tools.CalendarWriteJournal(hass)
    journal.records = [{"receipt_id": "r", "status": "outcome_unknown"}]
    journal.save = AsyncMock(side_effect=OSError("unwritable"))
    with pytest.raises(OSError):
        await journal.reviewed("r")
    assert journal.records == [{"receipt_id": "r", "status": "outcome_unknown"}]
