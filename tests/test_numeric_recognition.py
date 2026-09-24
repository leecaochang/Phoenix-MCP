"""Literal ID numeric recognition uses whole built-in sentences and source spans."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.numeric_recognition import NumericIdMatch, _literal_span, recognize_numeric_ids


@pytest.mark.parametrize('entity,pattern,intent', [
    ('light.test_lamp', 'set {entity} to {value} percent brightness', 'HassLightSet'),
    ('media_player.test_speaker', 'set {entity} volume to {value} percent', 'HassSetVolume'),
    ('cover.test_blind', 'set {entity} to {value} percent', 'HassSetPosition'),
])
@pytest.mark.parametrize('text,value', [('0', 0), ('37', 37), ('100', 100), ('thirty seven', 37)])
def test_real_templates_retain_exact_values(entity, pattern, intent, text, value):
    assert recognize_numeric_ids(pattern.format(entity=entity, value=text), 'en', (entity,)) == NumericIdMatch(intent, entity, value)


@pytest.mark.parametrize('sentence', [
    'do not set light.test_lamp to 37 percent brightness',
    'set light.test_lamp tomorrow to 37 percent brightness',
    'set light.test_lamp to 37 percent brightness tomorrow',
    'say "set light.test_lamp to 37 percent brightness"',
    '"set light.test_lamp to 37 percent brightness"',
    'set light.test_lamp to 37 or 55 percent brightness',
    'set light.test_lamp to 137 percent brightness',
    'set light.test_lamp to -1 percent brightness',
    'set light.test_lamp to 37.5 percent brightness',
    'set light.test_lamp brightness up by 37 percent',
    'set light.test_lamp to 37 percent and turn off light.test_lamp',
    'set light.test lamp to 37 percent brightness',
    'set light.test_lamp_fake to 37 percent brightness',
    'if I get home set light.test_lamp to 37 percent brightness',
    'set it to 37 percent brightness',
    'set light.hidden to 37 percent brightness',
])
def test_full_sentence_refusal_without_fuzzy_names(sentence):
    assert recognize_numeric_ids(sentence, 'en', ('light.test_lamp',)) is None


@pytest.mark.parametrize('span', [None, (), (0,), [0, 3], (True, 3), (-1, 3), (3, 0), (0, 20), ('0', 3)])
def test_missing_or_invalid_original_span_fails_closed(span):
    assert not _literal_span('abc', SimpleNamespace(text='abc', text_span=span), 'abc')


def test_original_span_must_equal_the_literal_target():
    entity = SimpleNamespace(text='light.test lamp', text_span=(4, 19))
    assert not _literal_span('set light.test lamp to 37 percent', entity, 'light.test_lamp')


@pytest.mark.parametrize('change', ['missing_span', 'wrong_span', 'wrong_number', 'wildcard', 'extra_slot', 'conflict', 'too_many'])
def test_parser_anomalies_never_become_numeric_evidence(change):
    import hassil
    original = hassil.recognize_all
    def changed(*args, **kwargs):
        result = list(original(*args, **kwargs))[0]
        if change == 'missing_span': result.entities['name'].text_span = None
        if change == 'wrong_span': result.entities['name'].text_span = (0, 15)
        if change == 'wrong_number': result.entities['brightness'].value = float('nan')
        if change == 'wildcard': result.entities['name'].is_wildcard = True
        if change == 'extra_slot': result.entities['relative'] = result.entities['name']
        yield result
        if change == 'conflict':
            other = deepcopy(result)
            other.entities['brightness'].value = 55
            yield other
        if change == 'too_many':
            from custom_components.phoenix_mcp.const import COMMAND_ROUTING_NUMERIC_MATCH_LIMIT
            for _ in range(COMMAND_ROUTING_NUMERIC_MATCH_LIMIT):
                yield result
    with patch.object(hassil, 'recognize_all', changed):
        assert recognize_numeric_ids('set light.test_lamp to 37 percent brightness', 'en', ('light.test_lamp',)) is None
