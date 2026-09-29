"""Literal target evidence is independent of a model's selected probability."""
import pytest

from custom_components.phoenix_mcp.decision_identity import explicit_write_target
from custom_components.phoenix_mcp.const import DECISION_ENTITY_CATEGORIES


def check(sentence, *, name='Audit Lamp', aliases=(), language='en', other=None, intent='PhoenixEntityLightColor', value='blue'):
    targets = {'t0': {'entity_id': 'light.audit_lamp', 'name': name, 'aliases': list(aliases)}}
    if other is not None:
        targets['t1'] = {'entity_id': 'light.other', 'name': other}
    return explicit_write_target(sentence, targets, {
        'intent': intent, 'entity_ids': ['light.audit_lamp'], 'value': value}, language)


@pytest.mark.parametrize('sentence,expected', [
    ('Set Audit Lamp to blue.', True),
    ('Make AUDIT   LAMP blue.', True),
    ('Set "Audit Lamp" to blue.', True),
    ('Set light.audit_lamp to blue.', True),
    ('Set Audit Missing Lamp to blue.', False),
    ('Set Audit Lamps to blue.', False),
    ('Set preAudit Lamp to blue.', False),
    ('Set light.audit_lamp_2 to blue.', False),
    ('Set other.light.audit_lamp to blue.', False),
    ('Set it to blue.', False),
    ('Set the lamp in the audit room to blue.', False),
])
def test_exact_names_and_ids_without_fuzzy_substitution(sentence, expected):
    assert check(sentence) is expected


@pytest.mark.parametrize('sentence,name,language', [
    ('Mets la Lampe du bureau en bleu.', 'Lampe du bureau', 'fr'),
    ('Stelle die Bürolampe auf Blau.', 'Bürolampe', 'de'),
    ('Pon la lámpara de la oficina en azul.', 'lámpara de la oficina', 'es'),
    ('把办公室灯设为蓝色', '办公室灯', 'zh-CN'),
    ('書斎の照明を青にして', '書斎の照明', 'ja'),
    ('Set the Cafe\u0301 Lamp blue.', 'Café Lamp', 'en'),
])
def test_registered_localized_names_preserve_script_and_canonical_accents(sentence, name, language):
    assert check(sentence, name=name, language=language)


def test_aliases_are_literal_and_not_regular_expressions_or_translations():
    assert check('Make Study glow blue.', aliases=['Study glow'])
    assert not check('Make Study glow blue.')
    assert check('Set Lamp (A)+ to blue.', aliases=['Lamp (A)+'])
    assert not check('Set Lamp A to blue.', aliases=['Lamp (A)+'])
    assert not check('Set Lampe du bureau to blue.')


def test_longer_competitor_and_multiple_subjects_cannot_authorize_shorter_match():
    assert not check('Set Audit Lamp 2 to blue.', other='Audit Lamp 2')
    assert not check('Set Audit Lamp and Other Lamp to blue.', other='Other Lamp')
    assert not check('Set Audit Lamp to blue.', other='Audit Lamp')
    assert check('Set Audit Lamp to blue.', other='Audit')


def test_copied_text_value_is_not_target_identity_evidence():
    intent = DECISION_ENTITY_CATEGORIES['input_text_value']
    assert not check('Set missing helper to "Audit Lamp".', intent=intent, value='Audit Lamp')
    assert check('Set Audit Lamp to "Visitors welcome".', intent=intent, value='Visitors welcome')


def test_identity_read_and_resource_bounds_are_explicit():
    assert check('What is it doing?', intent='HassGetState')
    assert not check('Audit Lamp ' * 257)
    assert not explicit_write_target('Audit Lamp', {}, {'intent': 'HassTurnOn', 'entity_ids': []}, 'en')


def test_redacted_catalog_text_is_not_identity_evidence():
    assert not check('Set <redacted> to blue.', name='<redacted>')
    assert not check('Set <redacted-entity> to blue.', aliases=['<redacted-entity>'])
    assert check('Set light.audit_lamp to blue.', name='<redacted>')
