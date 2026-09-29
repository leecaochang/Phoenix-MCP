"""Fixed entity commands with current feature, option and literal-value validation."""
from __future__ import annotations

import json
import re
from typing import Any

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.components.light import LightEntityFeature, color_supported
from homeassistant.util.color import COLORS

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.humidifier import HumidifierEntityFeature
from homeassistant.components.lawn_mower import LawnMowerEntityFeature
from homeassistant.components.lock import LockEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.components.siren import SirenEntityFeature
from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.components.valve import ValveEntityFeature
from homeassistant.components.water_heater import WaterHeaterEntityFeature

from .const import COMMAND_ROUTING_ENTITY_INTENTS, COMMAND_ROUTING_ENTITY_VALUE_LIMIT, COMMAND_ROUTING_FIXED_INTENT_DATA, COMMAND_ROUTING_READ_INTENTS, COMMAND_ROUTING_READ_ATTRIBUTES, COMMAND_ROUTING_READ_PERCENT_SCALES, DECISION_ENTITY_VALUE_QUESTIONS, COMMAND_ROUTING_REQUIRED_STATES

_FEATURES = {'alarm_control_panel': AlarmControlPanelEntityFeature, 'light': LightEntityFeature, 'climate': ClimateEntityFeature, 'cover': CoverEntityFeature, 'fan': FanEntityFeature,
             'humidifier': HumidifierEntityFeature, 'lawn_mower': LawnMowerEntityFeature, 'lock': LockEntityFeature,
             'media_player': MediaPlayerEntityFeature, 'siren': SirenEntityFeature, 'vacuum': VacuumEntityFeature,
             'valve': ValveEntityFeature, 'water_heater': WaterHeaterEntityFeature}


def command_options(state: Any, intent: str) -> list[str] | None:
    """Return the complete bounded option list; never silently remove competitors."""
    spec = COMMAND_ROUTING_ENTITY_INTENTS[intent]
    if not spec[3]:
        return None
    values = (sorted(COMMAND_ROUTING_READ_ATTRIBUTES & state.attributes.keys()) if intent in COMMAND_ROUTING_READ_INTENTS else
              list(COLORS) if spec[3] == '__color_names__' else state.attributes.get(spec[3]))
    if (not isinstance(values, (list, tuple)) or not values or len(values) > COMMAND_ROUTING_ENTITY_VALUE_LIMIT
            or any(not isinstance(value, str) or not value or len(value) > 255 for value in values)):
        return None
    return list(dict.fromkeys(values))


def supports_entity_command(state: Any, intent: str) -> bool:
    spec = COMMAND_ROUTING_ENTITY_INTENTS[intent]
    if intent in COMMAND_ROUTING_REQUIRED_STATES and state.state not in COMMAND_ROUTING_REQUIRED_STATES[intent]:
        return False
    if intent in COMMAND_ROUTING_READ_INTENTS:
        return command_options(state, intent) is not None
    if state.domain != spec[0]:
        return False
    if spec[4]:
        feature_type = _FEATURES.get(state.domain)
        if state.domain == 'camera':
            # Camera has optional dependencies; importing it must not be a Phoenix setup requirement.
            from homeassistant.components.camera import CameraEntityFeature
            feature_type = CameraEntityFeature
        feature = getattr(feature_type, spec[4], None)
        supported = state.attributes.get('supported_features', 0)
        if feature is None or not isinstance(supported, int) or isinstance(supported, bool) or not supported & feature:
            return False
    if spec[3] == '__color_names__':
        modes = state.attributes.get('supported_color_modes')
        if not isinstance(modes, (list, tuple, set, frozenset)) or not all(isinstance(mode, str) for mode in modes) or not color_supported(modes):
            return False
    if state.domain == 'camera' and 'motion_detection' in spec[1] and type(state.attributes.get('motion_detection')) is not bool:
        return False
    if spec[1] in ('toggle', 'toggle_cover_tilt'):
        pairs = {'cover': ('OPEN_TILT', 'CLOSE_TILT') if spec[1] == 'toggle_cover_tilt' else ('OPEN', 'CLOSE'),
                 'valve': ('OPEN', 'CLOSE'), 'climate': ('TURN_ON', 'TURN_OFF'),
                 'fan': ('TURN_ON', 'TURN_OFF'), 'media_player': ('TURN_ON', 'TURN_OFF'),
                 'siren': ('TURN_ON', 'TURN_OFF')}
        if state.domain in pairs:
            supported = state.attributes.get('supported_features', 0)
            if not isinstance(supported, int) or isinstance(supported, bool) or any(not supported & getattr(_FEATURES[state.domain], flag) for flag in pairs[state.domain]):
                return False
    if state.domain in ('lock', 'alarm_control_panel') and state.attributes.get('code_format'):
        return False
    if spec[3] and command_options(state, intent) is None:
        return False
    if state.domain == 'alarm_control_panel' and state.attributes.get('code_arm_required'):
        return False
    if state.domain in ('text', 'input_text'):
        if state.attributes.get('mode', 'text') != 'text':
            return False
        low, high = state.attributes.get('min', 0), state.attributes.get('max', 255)
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 255:
            return False
        # HA text patterns can use different regex dialects. Leave patterned fields to HA's normal agent path.
        if state.attributes.get('pattern'):
            return False
    return True


def valid_entity_value(state: Any, intent: str, value: Any) -> bool:
    if state is None or not supports_entity_command(state, intent) or not isinstance(value, str):
        return False
    spec = COMMAND_ROUTING_ENTITY_INTENTS[intent]
    if spec[3]:
        return value in (command_options(state, intent) or [])
    return (state.domain in ('text', 'input_text')
            and state.attributes.get('min', 0) <= len(value) <= state.attributes.get('max', 255))


def entity_arguments(proposal: dict) -> dict | None:
    from .const import COMMAND_ROUTING_PROOF_FIELDS
    spec = COMMAND_ROUTING_ENTITY_INTENTS.get(proposal.get('intent', ''))
    if spec is None or proposal.get("intent") in COMMAND_ROUTING_READ_INTENTS:
        return None
    ids = proposal.get('entity_ids')
    fields = {'intent', 'entity_ids'} | ({'value'} if spec[2] else set())
    if (set(proposal) - COMMAND_ROUTING_PROOF_FIELDS != fields or not isinstance(ids, list) or len(ids) != 1
            or not isinstance(ids[0], str) or not ids[0].startswith(spec[0] + '.')
            or (spec[2] and (not isinstance(proposal['value'], str) or len(proposal['value']) > 255))):
        return None
    return {'domain': spec[0], 'service': spec[1], 'entity_id': list(ids),
            'service_data': {spec[2]: proposal['value']} if spec[2] else dict(COMMAND_ROUTING_FIXED_INTENT_DATA.get(proposal['intent'], {}))}


def literal_text(sentence: str) -> str | None:
    """Read one explicitly quoted value without interpreting natural-language commands."""
    matches = list(re.finditer(r'"([^"\n]*)"|“([^”\n]*)”|「([^」\n]*)」', sentence))
    if len(matches) != 1:
        return None
    return next(value for value in matches[0].groups() if value is not None)


def value_choices(sentence: str, targets: dict, question: str = "value") -> dict[str, str]:
    """Opaque choices contain only known options or one independently copied text literal."""
    from .decision_provider import DecisionError
    values = set()
    for descriptor in targets.values():
        for metadata in descriptor.get('entity_operations', {}).values():
            kind = metadata.get('kind')
            if (question == 'attribute' and kind == 'attribute') or (question == 'value' and kind == 'option'):
                values.update(metadata.get('options', []))
    if question == 'color' and any(metadata.get('kind') == 'color' for target in targets.values()
           for metadata in target.get('entity_operations', {}).values()):
        values.update(COLORS)
    literal = literal_text(sentence)
    if question == 'value' and literal is not None and len(literal) <= 255 and any(
            metadata.get('kind') == 'text' for target in targets.values()
            for metadata in target.get('entity_operations', {}).values()):
        values.add(literal)
    if len(values) > COMMAND_ROUTING_ENTITY_VALUE_LIMIT:
        raise DecisionError('target_metadata_too_large')
    return {f'v{index}': value for index, value in enumerate(sorted(values))}


def chosen_entity_value(answer: dict, sentence: str, targets: dict, descriptor: dict, category: str) -> str | None:
    """Bind a choice to this operation and target, not another entity's option list."""
    from .decision_provider import confident_choice
    metadata = descriptor.get('entity_operations', {}).get(category, {})
    question = metadata.get('kind') if metadata.get('kind') in ('attribute', 'color') else 'value'
    if any(answer.get(other, {}).get('choice', 'none') != 'none' for other in DECISION_ENTITY_VALUE_QUESTIONS if other != question):
        return None
    check = answer.get(question)
    if not check or not confident_choice(check) or check['model'] != answer['route']['model']:
        return None
    value = value_choices(sentence, targets, question).get(check['choice'])
    metadata = descriptor.get('entity_operations', {}).get(category, {})
    if value is None or (metadata.get('kind') == 'text' and value != literal_text(sentence)):
        return None
    if metadata.get('kind') == 'color' and value not in COLORS:
        return None
    if metadata.get('kind') in ('option', 'attribute') and value not in metadata.get('options', []):
        return None
    return value


def entity_metadata(state: Any, intent: str) -> dict:
    spec = COMMAND_ROUTING_ENTITY_INTENTS[intent]
    if not spec[2]:
        return {}
    if spec[3] == '__color_names__':
        return {'kind': 'color'}
    if spec[3]:
        return {'kind': 'attribute' if intent in COMMAND_ROUTING_READ_INTENTS else 'option', 'options': command_options(state, intent)}
    return {'kind': 'text', 'minimum_length': state.attributes.get('min', 0), 'maximum_length': state.attributes.get('max', 255)}


def attribute_display(field: str, value: Any, temperature_unit: str | None, domain: str) -> str | None:
    """Format only the already-authorized, scrubbed tool value, never read raw state."""
    from .command_operations import finite_number
    conversion = COMMAND_ROUTING_READ_PERCENT_SCALES.get(field)
    if conversion is not None and domain in conversion[0]:
        scale = conversion[1]
        if not finite_number(value) or not 0 <= value <= scale:
            return None
        return f"{round(value * 100 / scale, 1):g}%"
    if field in ('temperature', 'current_temperature', 'target_temp_high', 'target_temp_low') and finite_number(value) and temperature_unit:
        return f"{value:g} {temperature_unit}"
    if field == 'color_temp_kelvin' and domain == 'light' and finite_number(value):
        return f"{value:g} K"
    if isinstance(value, str):
        return value if len(value) <= 1024 else None
    if isinstance(value, (bool, int, float, list, dict)):
        try:
            result = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return None
        return result if len(result) <= 1024 else None
    return None
