"""Entity operations use installed HA service schemas and bounded current values."""
import pytest
from homeassistant.core import State
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.command_operations import service_arguments, supports_command, valid_target_value
from custom_components.phoenix_mcp.const import COMMAND_ROUTING_ENTITY_COMMANDS, DECISION_ENTITY_CATEGORIES
from custom_components.phoenix_mcp.entity_commands import _FEATURES, valid_entity_value, value_choices, literal_text


@pytest.mark.parametrize('category', [key for key in COMMAND_ROUTING_ENTITY_COMMANDS if key != 'attribute_read'])
async def test_fixed_entity_projection_matches_native_schema(hass, category):
    spec = COMMAND_ROUTING_ENTITY_COMMANDS[category]
    domain, service, field, options, feature, _ = spec
    assert await async_setup_component(hass, domain, {domain: [] if domain == 'scene' else {}})
    intent = DECISION_ENTITY_CATEGORIES[category]
    value = 'heat' if category == 'climate_hvac' else 'red' if category == 'light_color' else 'Test option'
    proposal = {'intent': intent, 'entity_ids': [domain + '.test']}
    if field: proposal['value'] = value
    projected = service_arguments(proposal)
    native = hass.services.async_services()[domain][service].schema
    validated = native({'entity_id': projected['entity_id'], **projected['service_data']})
    assert validated['entity_id'] == projected['entity_id']
    if category == 'light_color':
        assert validated['params']['rgb_color'] == (255, 0, 0)
    elif field: assert validated.get('params', validated)[field] == value
    attrs = {options: [value]} if options else {}
    feature_type = _FEATURES.get(domain)
    if domain == 'camera':
        from homeassistant.components.camera import CameraEntityFeature
        feature_type = CameraEntityFeature
        attrs['motion_detection'] = True
    if feature: attrs['supported_features'] = int(getattr(feature_type, feature))
    if service in ('toggle', 'toggle_cover_tilt') and feature_type:
        attrs['supported_features'] = sum(int(flag) for flag in feature_type)
    if category == 'light_color': attrs['supported_color_modes'] = ['rgb']
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


def test_shared_color_choices_are_not_repeated_in_every_target():
    from custom_components.phoenix_mcp.entity_commands import entity_metadata
    intent = DECISION_ENTITY_CATEGORIES['light_color']
    state = State('light.test', 'on', {'supported_color_modes': ['rgb']})
    metadata = entity_metadata(state, intent)
    assert metadata == {'kind': 'color'}
    assert valid_entity_value(state, intent, 'red')
    assert not valid_entity_value(state, intent, 'made-up-color')
    assert not supports_command(State('light.test', 'on', {'supported_color_modes': ['brightness']}), intent)
    values = value_choices('Make the light red', {'t0': {'entity_operations': {'light_color': metadata}}}, 'color')
    assert 'red' in values.values()


@pytest.mark.parametrize('category', ['alarm_disarm', 'alarm_home', 'alarm_trigger'])
def test_code_protected_alarm_never_becomes_a_code_free_candidate(category):
    intent = DECISION_ENTITY_CATEGORIES[category]
    feature = COMMAND_ROUTING_ENTITY_COMMANDS[category][4]
    attrs = {'supported_features': int(getattr(_FEATURES['alarm_control_panel'], feature)) if feature else 0}
    assert supports_command(State('alarm_control_panel.test', 'disarmed', attrs), intent)
    for protection in ({'code_format': 'number'}, {'code_arm_required': True}):
        assert not supports_command(State('alarm_control_panel.test', 'disarmed', {**attrs, **protection}), intent)


@pytest.mark.parametrize('domain,field,value,unit,expected', [
    ('light', 'brightness', 94, None, '36.9%'),
    ('sensor', 'brightness', 94, None, '94'),
    ('media_player', 'volume_level', 0.37, None, '37%'),
    ('cover', 'current_position', 37, None, '37%'),
    ('weather', 'temperature', 72, '°F', '72 °F'),
    ('sensor', 'temperature', 72, None, '72'),
    ('light', 'brightness', 300, None, None),
])
def test_attribute_display_uses_only_known_domain_units(domain, field, value, unit, expected):
    from custom_components.phoenix_mcp.entity_commands import attribute_display
    assert attribute_display(field, value, unit, domain) == expected


def test_colors_attributes_and_device_options_have_independent_complete_choice_budgets():
    targets = {'t0': {'entity_operations': {
        'select_option': {'kind': 'option', 'options': [f'Option {i}' for i in range(250)]},
        'light_color': {'kind': 'color'},
        'attribute_read': {'kind': 'attribute', 'options': ['brightness', 'temperature']},
    }}}
    assert len(value_choices('command', targets)) == 250
    assert set(value_choices('command', targets, 'attribute').values()) == {'brightness', 'temperature'}
    assert 'red' in value_choices('command', targets, 'color').values()


def test_value_selection_cannot_mix_color_and_attribute_decisions():
    from custom_components.phoenix_mcp.entity_commands import chosen_entity_value
    targets = {'t0': {'entity_operations': {'attribute_read': {'kind': 'attribute', 'options': ['brightness']}}}}
    def choice(value):
        return {'choice': value, 'model': 'jev-1.13.0', 'probabilities': {value: 1.0}}
    answer = {'route': choice('attribute_read'), 'attribute': choice('v0'), 'color': choice('none')}
    assert chosen_entity_value(answer, 'request', targets, targets['t0'], 'attribute_read') == 'brightness'
    answer['color'] = choice('v0')
    assert chosen_entity_value(answer, 'request', targets, targets['t0'], 'attribute_read') is None
