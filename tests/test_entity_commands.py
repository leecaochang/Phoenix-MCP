"""Entity operations use installed HA service schemas and bounded current values."""
import pytest
from homeassistant.core import State
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.command_operations import service_arguments, supports_command, valid_target_value
from custom_components.phoenix_mcp.const import COMMAND_ROUTING_ENTITY_COMMANDS, DECISION_ENTITY_CATEGORIES
from custom_components.phoenix_mcp.entity_commands import _FEATURES, valid_entity_value, value_choices, literal_text


@pytest.mark.parametrize('category', list(COMMAND_ROUTING_ENTITY_COMMANDS))
async def test_fixed_entity_projection_matches_native_schema(hass, category):
    spec = COMMAND_ROUTING_ENTITY_COMMANDS[category]
    domain, service, field, options, feature, _ = spec
    assert await async_setup_component(hass, domain, {domain: [] if domain == 'scene' else {}})
    intent = DECISION_ENTITY_CATEGORIES[category]
    value = 'heat' if category == 'climate_hvac' else 'Test option'
    proposal = {'intent': intent, 'entity_ids': [domain + '.test']}
    if field: proposal['value'] = value
    projected = service_arguments(proposal)
    native = hass.services.async_services()[domain][service].schema
    validated = native({'entity_id': projected['entity_id'], **projected['service_data']})
    assert validated['entity_id'] == projected['entity_id']
    if field: assert validated[field] == value
    attrs = {options: [value]} if options else {}
    if feature: attrs['supported_features'] = int(getattr(_FEATURES[domain], feature))
    state = State(domain + '.test', 'on', attrs)
    assert supports_command(state, intent)
    if field:
        assert valid_entity_value(state, intent, value)
        assert not valid_entity_value(state, intent, {'untrusted': 'value'})
    if feature: assert not supports_command(State(domain + '.test', 'on', {**attrs, 'supported_features': 0}), intent)
    assert service_arguments({**proposal, 'service': 'arbitrary'}) is None
    assert service_arguments({**proposal, 'entity_ids': ['sensor.test']}) is None


@pytest.mark.parametrize('domain', ['text', 'input_text'])
@pytest.mark.parametrize('attrs', [{'mode': 'password'}, {'pattern': '.*'}, {'min': 10, 'max': 4}])
def test_text_metadata_excludes_secret_and_unvalidated_fields(domain, attrs):
    assert not supports_command(State(domain+'.test', 'value', attrs), DECISION_ENTITY_CATEGORIES[domain+'_value'])


def test_choices_are_complete_and_bound_to_known_values():
    from custom_components.phoenix_mcp.decision_provider import DecisionError
    targets = {'t0': {'entity_operations': {'select_option': {'kind': 'option', 'options': ['Away', 'Home']},
                                          'input_text_value': {'kind': 'text'}}}}
    assert set(value_choices('Set the label to "Visitor"', targets).values()) == {'Away', 'Home', 'Visitor'}
    assert literal_text('Set label to “Visitor”') == 'Visitor'
    assert literal_text('Set label to "one" or "two"') is None
    targets['t0']['entity_operations']['select_option']['options'] = [str(x) for x in range(255)]
    with pytest.raises(DecisionError, match='target_metadata_too_large'): value_choices('Set mode', targets)


@pytest.mark.parametrize('value,attrs,valid', [(1000, {}, True), (-3, {}, True), (2.5, {}, False),
    (11, {'maximum': 10}, False), (-1, {'minimum': 0}, False), (4, {'minimum': 0, 'maximum': 10, 'step': 3}, True)])
def test_counter_uses_integer_limits_without_treating_increment_step_as_a_value_grid(value, attrs, valid):
    assert valid_target_value(State('counter.test', '0', attrs), 'PhoenixCounterValue', value) is valid


async def test_timer_duration_projection_matches_native_schema(hass):
    from datetime import timedelta
    assert await async_setup_component(hass, 'timer', {'timer': {}})
    args = service_arguments({'intent': 'PhoenixTimerDuration', 'entity_ids': ['timer.test'], 'value': '00:05:00'})
    native = hass.services.async_services()['timer']['start'].schema
    assert native({'entity_id': ['timer.test'], **args['service_data']})['duration'] == timedelta(minutes=5)


@pytest.mark.parametrize('category,value,state,attrs,matched', [
    ('select_option', 'Away', 'Away', {}, True),
    ('input_text_value', 'Visitors welcome', 'Visitors welcome', {}, True),
    ('climate_hvac', 'heat', 'heat', {}, True),
    ('water_operation', 'eco', 'eco', {}, True),
    ('media_source', 'Radio', 'playing', {'source': 'Radio'}, True),
    ('input_button_press', None, '2026-09-24T00:00:00+00:00', {}, False),
    ('counter_increment', None, '4', {}, False),
])
def test_entity_observation_uses_actual_property_without_claiming_momentary_completion(category, value, state, attrs, matched):
    from custom_components.phoenix_mcp.command_verification import state_matches
    domain = COMMAND_ROUTING_ENTITY_COMMANDS[category][0]
    proposal = {'intent': DECISION_ENTITY_CATEGORIES[category], 'entity_ids': [domain + '.test']}
    if value is not None: proposal['value'] = value
    assert state_matches(proposal, State(domain + '.test', state, attrs)) is matched


@pytest.mark.parametrize('remaining,matched', [(300, True), (100, False)])
def test_timer_duration_observation_does_not_confuse_an_old_countdown_with_a_restart(remaining, matched):
    from datetime import UTC, datetime, timedelta
    from custom_components.phoenix_mcp.command_verification import state_matches
    proposal = {'intent': 'PhoenixTimerDuration', 'entity_ids': ['timer.test'], 'value': '00:05:00'}
    state = State('timer.test', 'active', {'duration': '0:05:00',
        'finishes_at': (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat()})
    assert state_matches(proposal, state) is matched


@pytest.mark.parametrize('category,expected', [
    ('media_mute', {'is_volume_muted': True}), ('media_unmute', {'is_volume_muted': False}),
    ('media_shuffle_off', {'shuffle': False}), ('media_repeat_all', {'repeat': 'all'}),
    ('media_repeat_off', {'repeat': 'off'}), ('fan_oscillate_on', {'oscillating': True}),
    ('fan_direction_forward', {'direction': 'forward'}), ('automation_off', {'stop_actions': True}),
])
async def test_fixed_arguments_match_native_schema_and_reject_injected_values(hass, category, expected):
    domain, service, *_ = COMMAND_ROUTING_ENTITY_COMMANDS[category]
    assert await async_setup_component(hass, domain, {domain: {}})
    proposal = {'intent': DECISION_ENTITY_CATEGORIES[category], 'entity_ids': [domain + '.test']}
    args = service_arguments(proposal)
    assert args['service_data'] == expected
    native = hass.services.async_services()[domain][service].schema
    validated = native({'entity_id': args['entity_id'], **args['service_data']})
    # HA renames mute for the entity method and coerces repeat strings to its native enum.
    for key, value in expected.items():
        actual = validated['mute' if key == 'is_volume_muted' else key]
        assert isinstance(actual, type(value)) and actual == value
    assert service_arguments({**proposal, 'value': 'model-written'}) is None


@pytest.mark.parametrize('category,field,value', [
    ('media_mute', 'is_volume_muted', True), ('media_shuffle_off', 'shuffle', False),
    ('media_repeat_one', 'repeat', 'one'), ('fan_direction_reverse', 'direction', 'reverse'),
    ('fan_oscillate_on', 'oscillating', True),
])
def test_fixed_action_observation_needs_reported_property(category, field, value):
    from custom_components.phoenix_mcp.command_verification import state_matches
    domain = COMMAND_ROUTING_ENTITY_COMMANDS[category][0]
    proposal = {'intent': DECISION_ENTITY_CATEGORIES[category], 'entity_ids': [domain + '.test']}
    assert not state_matches(proposal, State(domain + '.test', 'on'))
    assert state_matches(proposal, State(domain + '.test', 'on', {field: value}))
    if type(value) is bool:
        assert not state_matches(proposal, State(domain + '.test', 'on', {field: str(value)}))
        assert not state_matches(proposal, State(domain + '.test', 'on', {field: int(value)}))
    if field == 'repeat':
        from homeassistant.components.media_player import RepeatMode
        assert state_matches(proposal, State(domain + '.test', 'on', {field: RepeatMode(value)}))
