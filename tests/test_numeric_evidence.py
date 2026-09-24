"""Source percentage evidence cannot infer values or authorize an action."""
from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.const import MAX_SEARCH_QUERY_LEN
from custom_components.phoenix_mcp.numeric_evidence import read_explicit_percentage, compare_percentage


@pytest.mark.parametrize('value', range(101))
def test_exact_ascii_percentage_range(value):
    assert read_explicit_percentage(f'Please adjust the device to {value}%.', 'en') == value


@pytest.mark.parametrize('spelling,value', [
    ('zero', 0), ('one', 1), ('eleven', 11), ('twenty', 20), ('thirty-seven', 37),
    ('THIRTY SEVEN', 37), ('forty-two', 42), ('fifty five', 55), ('ninety-nine', 99), ('one hundred', 100),
])
@pytest.mark.parametrize('unit', ['percent', 'per cent', '%'])
def test_ha_number_spellings_and_explicit_units(spelling, value, unit):
    assert read_explicit_percentage(f'I would like the device at {spelling} {unit} now.', 'en') == value


@pytest.mark.parametrize('number', [
    '-37', '- 37', '+37', '+ 37', '\u221237', '.37', '37.5', '37,5', '3/7', '37/100', '37-55',
    '1e2', '137', '101', '037', 'thirty-seven point five', 'negative thirty seven',
    'minus thirty-seven', 'one hundred thirty-seven', 'one hundred and thirty seven',
    'one thousand thirty seven', 'two hundred', 'thirty seven and a half', 'half', 'a quarter',
    'thirty-seven-', 'thirty--seven', '37-0', 'thirty 7', '３７', '³⁷',
])
def test_no_suffix_extraction_rounding_sign_removal_or_conversion(number):
    assert read_explicit_percentage(f'Set the device to {number} percent.', 'en') is None


@pytest.mark.parametrize('sentence', [
    'Set the device to 37 percent instead of 73 percent.',
    'Set the device to 73 percent instead of 37 percent.',
    'Set the device to 37 percent, not 73.',
    'Set the device to thirty-seven percent, not seventy-three.',
    'Set the device to 37 or 55 percent.',
    'Set the device to 37 percent and another device to 55 percent.',
    'Set device 2 to 37 percent.',
    'Set Bedroom One lamp to 37 percent.',
    'At 8:30, set the device to 37 percent.',
    'Set light.office_2 to 37 percent.',
    'Set the device to 37 decibels.',
    'Set the device to 37.',
    'Set the device to half.',
    'Set the device to 37 percentages.',
    'Set the device to 37 percentish.',
])
def test_multiple_numbers_and_unsupported_notation_stay_unverified(sentence):
    assert read_explicit_percentage(sentence, 'en') is None


@pytest.mark.parametrize('sentence', [
    'Do not set the device to 37 percent.',
    'Tomorrow set the device to 37 percent.',
    'Translate "set the device to 37 percent".',
    'Increase the device by 37 percent.',
    'Can the device support 37 percent?',
])
def test_value_evidence_is_explicitly_not_command_or_target_validation(sentence):
    assert read_explicit_percentage(sentence, 'en') == 37


@pytest.mark.parametrize('language', ['zh', 'zh-Hans', 'es', 'en-US', '', None])
def test_no_unevaluated_language_assumption(language):
    assert read_explicit_percentage('Set the device to 37 percent.', language) is None


def test_input_budget_and_missing_number_dependency_fail_closed():
    assert read_explicit_percentage('x' * MAX_SEARCH_QUERY_LEN + '37%', 'en') is None
    with patch('custom_components.phoenix_mcp.numeric_evidence._number_words', side_effect=ImportError):
        assert read_explicit_percentage('Set the device to 37 percent.', 'en') is None


def test_diagnostic_distinguishes_disagreement_from_missing_evidence():
    assert compare_percentage(37, 37) == {'status': 'match', 'source_value': 37}
    assert compare_percentage(37, 5) == {'status': 'mismatch', 'source_value': 37}
    assert compare_percentage(None, 37) == {'status': 'unverified'}
