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


@pytest.mark.parametrize('language', ['es', 'zh-CN'])
@pytest.mark.parametrize('value', range(101))
def test_new_languages_verify_digits_and_ha_spelled_values(language, value):
    from unicode_rbnf import RbnfEngine
    engine = RbnfEngine.for_language('es' if language == 'es' else 'zh')
    spelling = engine.format_number(value).text_by_ruleset[
        'spellout-numbering' if language == 'es' else 'spellout-cardinal']
    for number in (str(value), spelling):
        sentence = f'Pon el brillo al {number} por ciento.' if language == 'es' else f'把亮度设为百分之{number}。'
        assert read_explicit_percentage(sentence, language) == value


@pytest.mark.parametrize('language,sentence,value', [
    ('es', 'Quiero la lámpara al treinta y siete por ciento ahora.', 37),
    ('es', 'Pon el brillo al diecise\u0301is por ciento.', 16),
    ('es', 'Pon el brillo al 37%.', 37),
    ('zh-CN', '请设为三十七%。', 37),
    ('zh-CN', '请设为百分之37。', 37),
    ('zh-CN', '请设为百分之三十七.', 37),
    ('zh-CN', '请设为37％。', 37),
    ('es', 'No pongas la lámpara al 37 por ciento.', 37),
    ('zh-CN', '不要把亮度设为百分之三十七。', 37),
])
def test_multilingual_value_evidence_does_not_decide_intent(language, sentence, value):
    from custom_components.phoenix_mcp.numeric_evidence import has_percentage_unit
    assert has_percentage_unit(sentence)
    assert read_explicit_percentage(sentence, language) == value


@pytest.mark.parametrize('number', ['-37', '+37', '37,5', '37.5', '37/100', '137', '037',
    'menos treinta y siete', 'treinta y siete y medio', 'ciento treinta y siete', 'mil treinta y siete',
    'treinta-siete', 'treinta 7', '３７'])
def test_spanish_rejects_signs_fractions_mixed_and_suffix_numbers(number):
    assert read_explicit_percentage(f'Pon el brillo al {number} por ciento.', 'es') is None


@pytest.mark.parametrize('number', ['负三十七', '正三十七', '-37', '+37', '37.5', '37,5',
    '三十七点五', '三十七多', '三十七成', '一百三十七', '千三十七', '三十七分之一', '三十7',
    '三十七或五十五', '３７', '037', '半', '三十七万'])
def test_chinese_rejects_signs_fractions_mixed_and_suffix_numbers(number):
    assert read_explicit_percentage(f'把亮度设为百分之{number}。', 'zh-CN') is None


@pytest.mark.parametrize('language,sentence', [
    ('es', 'Pon lámpara 2 al 37 por ciento.'),
    ('es', 'Pon el brillo al 37 por ciento, no 73.'),
    ('es', 'Pon el brillo al 37 por ciento y medio.'),
    ('es', 'Pon el brillo al 37 o 55 por ciento.'),
    ('zh-CN', '把二号灯设为百分之三十七。'),
    ('zh-CN', '把亮度设为百分之三十七而不是七十三。'),
    ('zh-CN', '把亮度设为百分之三十七或百分之五十五。'),
    ('zh-CN', '把亮度设为37%多一点。'),
    ('zh-CN', '八点把亮度设为百分之三十七。'),
    ('zh-CN', '把亮度设为负百分之三十七。'),
])
def test_other_numeric_evidence_is_never_discarded_in_new_languages(language, sentence):
    assert read_explicit_percentage(sentence, language) is None


@pytest.mark.parametrize('sentence,language,expected', [
    ('set value to -2.5 dB', 'en', -2.5), ('set value to 21.5', 'en', 21.5),
    ('Wert auf 21,5 setzen', 'de', 21.5), ('设置为37', 'zh-CN', 37),
    ('set it to 1,000', 'en', None), ('set it to 1.000', 'de', None),
    ('turn on lamp2', 'en', None), ('set helper2 to 37', 'en', None),
    ('set time to 12:30', 'en', None), ('set date to 2026-10-01', 'en', None),
    ('set value to 1e3', 'en', None), ('set value to 1/2', 'en', None),
    ('set value to 2 or 3', 'en', None), ('set value to １２', 'en', None),
])
def test_scalar_evidence_preserves_values_and_rejects_dates_and_ambiguity(sentence, language, expected):
    from custom_components.phoenix_mcp.numeric_evidence import read_explicit_scalar
    assert read_explicit_scalar(sentence, language) == expected


@pytest.mark.parametrize('sentence,unit,expected', [
    ('set temperature to 21.5 °F', '°C', False), ('set temperature to 21.5 °C', '°C', True),
    ('set it to 21.5 percent', '°C', True),  # Words still require the model's semantic review.
    ('set it to 21.5%', '°C', False), ('set it to 3500K', 'K', True),
    ('set it to 3500 kelvin', '°C', False), ('set it to 55 percent', '%', True),
    ('设为21.5℃', '°C', True), ('设为21.5℉', '°C', False),
])
def test_explicit_unit_markers_never_convert_values(sentence, unit, expected):
    from custom_components.phoenix_mcp.numeric_evidence import explicit_unit_matches
    assert explicit_unit_matches(sentence, unit) is expected
