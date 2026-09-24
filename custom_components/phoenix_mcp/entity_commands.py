"""Fixed entity commands with current feature, option and literal-value validation."""
from __future__ import annotations

import re
from typing import Any

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

from .const import COMMAND_ROUTING_ENTITY_INTENTS, COMMAND_ROUTING_ENTITY_VALUE_LIMIT

_FEATURES = {'climate': ClimateEntityFeature, 'cover': CoverEntityFeature, 'fan': FanEntityFeature,
             'humidifier': HumidifierEntityFeature, 'lawn_mower': LawnMowerEntityFeature, 'lock': LockEntityFeature,
             'media_player': MediaPlayerEntityFeature, 'siren': SirenEntityFeature, 'vacuum': VacuumEntityFeature,
             'valve': ValveEntityFeature, 'water_heater': WaterHeaterEntityFeature}


def command_options(state: Any, intent: str) -> list[str] | None:
    """Return the complete bounded option list; never silently remove competitors."""
    spec = COMMAND_ROUTING_ENTITY_INTENTS[intent]
    if not spec[3]:
        return None
    values = state.attributes.get(spec[3])
    if (not isinstance(values, (list, tuple)) or not values or len(values) > COMMAND_ROUTING_ENTITY_VALUE_LIMIT
            or any(not isinstance(value, str) or not value or len(value) > 255 for value in values)):
        return None
    return list(dict.fromkeys(values))


def supports_entity_command(state: Any, intent: str) -> bool:
    spec = COMMAND_ROUTING_ENTITY_INTENTS[intent]
    if state.domain != spec[0]:
        return False
    if spec[4]:
        feature = getattr(_FEATURES.get(state.domain), spec[4], None)
        supported = state.attributes.get('supported_features', 0)
        if feature is None or not isinstance(supported, int) or not supported & feature:
            return False
    if state.domain == 'lock' and state.attributes.get('code_format'):
        return False
    if spec[3] and command_options(state, intent) is None:
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
    spec = COMMAND_ROUTING_ENTITY_INTENTS.get(proposal.get('intent', ''))
    if spec is None:
        return None
    ids = proposal.get('entity_ids')
    fields = {'intent', 'entity_ids'} | ({'value'} if spec[2] else set())
    if (set(proposal) != fields or not isinstance(ids, list) or len(ids) != 1
            or not isinstance(ids[0], str) or not ids[0].startswith(spec[0] + '.')
            or (spec[2] and (not isinstance(proposal['value'], str) or len(proposal['value']) > 255))):
        return None
    return {'domain': spec[0], 'service': spec[1], 'entity_id': list(ids),
            'service_data': {spec[2]: proposal['value']} if spec[2] else {}}


def literal_text(sentence: str) -> str | None:
    """Read one explicitly quoted value without interpreting natural-language commands."""
    matches = list(re.finditer(r'"([^"\n]*)"|“([^”\n]*)”|「([^」\n]*)」', sentence))
    if len(matches) != 1:
        return None
    return next(value for value in matches[0].groups() if value is not None)


def value_choices(sentence: str, targets: dict) -> dict[str, str]:
    """Opaque choices contain only known options or one independently copied text literal."""
    from .decision_provider import DecisionError
    values = set()
    for descriptor in targets.values():
        for metadata in descriptor.get('entity_operations', {}).values():
            values.update(metadata.get('options', []))
    literal = literal_text(sentence)
    if literal is not None and len(literal) <= 255 and any(
            metadata.get('kind') == 'text' for target in targets.values()
            for metadata in target.get('entity_operations', {}).values()):
        values.add(literal)
    if len(values) > COMMAND_ROUTING_ENTITY_VALUE_LIMIT:
        raise DecisionError('target_metadata_too_large')
    return {f'v{index}': value for index, value in enumerate(sorted(values))}


def chosen_entity_value(answer: dict, sentence: str, targets: dict, descriptor: dict, category: str) -> str | None:
    """Bind a choice to this operation and target, not another entity's option list."""
    from .decision_provider import confident_choice
    check = answer.get('value')
    if not check or not confident_choice(check) or check['model'] != answer['route']['model']:
        return None
    value = value_choices(sentence, targets).get(check['choice'])
    metadata = descriptor.get('entity_operations', {}).get(category, {})
    if value is None or (metadata.get('kind') == 'text' and value != literal_text(sentence)):
        return None
    if metadata.get('kind') == 'option' and value not in metadata.get('options', []):
        return None
    return value


def entity_metadata(state: Any, intent: str) -> dict:
    spec = COMMAND_ROUTING_ENTITY_INTENTS[intent]
    if not spec[2]:
        return {}
    if spec[3]:
        return {'kind': 'option', 'options': command_options(state, intent)}
    return {'kind': 'text', 'minimum_length': state.attributes.get('min', 0), 'maximum_length': state.attributes.get('max', 255)}
