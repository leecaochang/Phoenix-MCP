"""Date/time evidence uses calendar validation, native helper types and DST checks."""
from datetime import date, datetime, time

import pytest
from homeassistant.core import State
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.command_operations import service_arguments, supports_command
from custom_components.phoenix_mcp.temporal_values import read_temporal_value, valid_temporal_value, temporal_state_matches


@pytest.mark.parametrize('text,expected', [
    ('Set date to 2028-02-29.', {'kind': 'date', 'value': '2028-02-29'}),
    ('Set time to 7:30 pm', {'kind': 'time', 'value': '19:30:00'}),
    ('Set value to 2026-12-25 7:30 pm', {'kind': 'datetime', 'value': '2026-12-25 19:30:00'}),
    ('Set time to 00:05', {'kind': 'time', 'value': '00:05:00'}),
    ('Set value to 2026-12-25T18:30', {'kind': 'datetime', 'value': '2026-12-25 18:30:00'}),
    ('Set value to 2026-12-25 at 6:30 pm', {'kind': 'datetime', 'value': '2026-12-25 18:30:00'}),
    ('设置input_datetime.helper2为2026-12-25', {'kind': 'date', 'value': '2026-12-25'}),
    ('set input_datetime.helper2 to 12:30', {'kind': 'time', 'value': '12:30:00'}),
    ('Set date to 2027-02-29', None), ('Set date to 03/04/2026', None),
    ('Set time to 25:00', None), ('Set time to 12:00 or 13:00', None),
    ('Set value to 2026-12-25T18:30Z', None), ('Set value to tomorrow', None),
    ('Set value to 2026-12-25T18:30+02:00', None),
])
def test_temporal_literal_evidence(text, expected):
    assert read_temporal_value(text) == expected


@pytest.mark.parametrize('value,valid', [
    ('2026-03-08 02:30:00', False), ('2026-11-01 01:30:00', False),
    ('2026-03-08 03:30:00', True), ('2026-11-01 03:30:00', True),
])
def test_dst_gaps_and_overlaps_are_not_guessed(value, valid):
    assert valid_temporal_value('datetime', value, 'America/New_York') is valid


@pytest.mark.parametrize('intent,domain,field,value,parsed', [
    ('PhoenixDateValue', 'date', 'date', '2026-12-25', date(2026, 12, 25)),
    ('PhoenixTimeValue', 'time', 'time', '18:30:00', time(18, 30)),
    ('PhoenixDateTimeValue', 'datetime', 'datetime', '2026-12-25 18:30:00', datetime(2026, 12, 25, 18, 30)),
    ('PhoenixInputDate', 'input_datetime', 'date', '2026-12-25', date(2026, 12, 25)),
    ('PhoenixInputTime', 'input_datetime', 'time', '18:30:00', time(18, 30)),
    ('PhoenixInputDateTime', 'input_datetime', 'datetime', '2026-12-25 18:30:00', datetime(2026, 12, 25, 18, 30)),
])
async def test_projection_matches_installed_ha_service_schema(hass, intent, domain, field, value, parsed):
    assert await async_setup_component(hass, domain, {domain: {}})
    proposal = {'intent': intent, 'entity_ids': [domain + '.test'], 'value': value}
    args = service_arguments(proposal)
    schema = hass.services.async_services()[domain][args['service']].schema
    validated = schema({**args['service_data'], 'entity_id': args['entity_id']})
    assert validated[field] == parsed
    assert supports_command(State(domain + '.test', value, {'has_date': True, 'has_time': True}), intent)
    if domain == 'input_datetime':
        assert not supports_command(State(domain + '.test', value, {'has_date': False, 'has_time': False}), intent)
    assert service_arguments({**proposal, 'value': 'not a date or time'}) is None
    assert service_arguments({**proposal, 'timestamp': 123}) is None


def test_partial_helper_state_observation_does_not_compare_unrequested_field():
    state = State('input_datetime.test', '2026-12-25 18:30:00', {'year': 2026, 'month': 12, 'day': 25,
                                                             'hour': 18, 'minute': 30, 'second': 0})
    assert temporal_state_matches({'intent': 'PhoenixInputDate', 'entity_ids': [state.entity_id], 'value': '2026-12-25'}, state, 'UTC')
    assert temporal_state_matches({'intent': 'PhoenixInputTime', 'entity_ids': [state.entity_id], 'value': '18:30:00'}, state, 'UTC')


@pytest.mark.parametrize('intent,value,state_value,valid', [
    ('PhoenixInputDate', '2026-03-08', '2026-03-07 02:30:00', False),
    ('PhoenixInputTime', '01:30:00', '2026-11-01 03:30:00', False),
    ('PhoenixInputTime', '03:30:00', '2026-11-01 04:30:00', True),
])
def test_partial_helper_update_checks_combined_dst_value(intent, value, state_value, valid):
    from custom_components.phoenix_mcp.temporal_values import temporal_value_current
    state = State('input_datetime.test', state_value, {'has_date': True, 'has_time': True})
    proposal = {'intent': intent, 'entity_ids': [state.entity_id], 'value': value}
    assert temporal_value_current(proposal, state, 'America/New_York') is valid
