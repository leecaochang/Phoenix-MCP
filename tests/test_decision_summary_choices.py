"""Verify complete factored collection choices and conservative scope admission."""
from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.agentcli import ProviderConfig
from custom_components.phoenix_mcp.const import DECISION_SUMMARY_COMPONENTS, DECISION_TYPESAFE_BASE_URL
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider, confident_choice
from custom_components.phoenix_mcp.decision_summary_choices import factor_summary_choices, resolve_summary_choices


def catalog():
    return {f's{domain_index}_{area}': {
        'summary_scope': {'domain': domain, 'device_class': 'motion' if domain == 'binary_sensor' else '',
                          'area_id': '' if area == 0 else f'area_{area}'},
        'area': '' if area == 0 else f'Room {area}',
        'area_aliases': [] if area == 0 else [f'Alias {area}'],
        'states': ['on', 'off'],
    } for domain_index, domain in enumerate(('light', 'switch', 'binary_sensor')) for area in range(100)}


def response(payload, selectors=('light', '', ''), mode='valid'):
    mappings = factor_summary_choices(
        {'questions': {'summary_scope': {}}}, catalog())
    assert mappings
    answers = {}
    selected_values = dict(zip(DECISION_SUMMARY_COMPONENTS, selectors, strict=True))
    for question, spec in payload['questions'].items():
        selected = {'route': 'state_summary', 'target': 'none', 'summary': 'on'}.get(question, 'none')
        if question in mappings:
            selected = next((key for key, value in mappings[question].items()
                             if value == selected_values[question]), 'none')
        answers[question] = {'type': 'choice', 'choice': selected, 'confidence': 1,
                             'probabilities': {key: float(key == selected) for key in spec['criteria']}}
    if mode in ('low', 'tie'):
        probabilities = answers['summary_domain']['probabilities']
        winner = answers['summary_domain']['choice']
        probabilities[winner] = 0.89 if mode == 'low' else 0.5
        probabilities['none'] = 1 - probabilities[winner]
    return {'model': 'jev-1.13.0', 'answers': answers}


@pytest.mark.parametrize('selectors,expected', [
    (('light', '', ''), 's0_0'),
    (('switch', '', 'area_99'), 's1_99'),
    (('binary_sensor', 'motion', ''), 's2_0'),
    (('light', 'motion', ''), 'none'),
    (('light', '', 'missing_area'), 'none'),
])
async def test_factored_selectors_resolve_only_an_existing_exact_scope(selectors, expected):
    scopes = catalog()
    provider = DecisionProvider(ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL), 'systemone')
    async def reply(*args):
        payload = args[4]
        assert all(key in payload['questions'] for key in DECISION_SUMMARY_COMPONENTS)
        assert 'summary_scope' not in payload['questions']
        assert 'summary_scopes' not in payload['state']
        assert 'summary_scope_fields' not in payload['state']
        assert 'summary_areas' not in payload['state']
        assert set(payload['state']['summary_domain_states']) == {'light', 'switch', 'binary_sensor'}
        assert len(payload['questions']['summary_area']['criteria']) == 101
        assert all(len(spec['criteria']) <= 255 for spec in payload['questions'].values())
        assert any('Alias 99' in text for text in payload['questions']['summary_area']['criteria'].values())
        return response(payload, selectors)
    with patch.object(provider, '_request', side_effect=reply) as request:
        answers = await provider.route_and_target(None, 'How many lights are on?', {}, 5000, summary_scopes=scopes)
    request.assert_awaited_once()
    scope = answers['summary_scope']
    assert scope['choice'] == expected
    assert confident_choice(scope)
    assert 'probabilities' not in scope  # No fabricated composite probability.
    assert set(scope['_components']) == set(DECISION_SUMMARY_COMPONENTS)


@pytest.mark.parametrize('mode', ['low', 'tie'])
async def test_every_selector_must_independently_meet_the_existing_threshold(mode):
    provider = DecisionProvider(ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL), 'systemone')
    async def reply(*args):
        return response(args[4], mode=mode)
    with patch.object(provider, '_request', side_effect=reply):
        answers = await provider.route_and_target(None, 'How many lights are on?', {}, 5000, summary_scopes=catalog())
        assert not confident_choice(answers['summary_scope'])


def test_factoring_keeps_existing_grouped_path_when_one_axis_exceeds_choice_limit():
    scopes = catalog()
    for index, item in enumerate(scopes.values()):
        item['summary_scope']['area_id'] = str(index)
    payload = {'questions': {'summary_scope': {'criteria': {'existing': 'unchanged'}}}}
    assert factor_summary_choices(payload, scopes) is None
    assert payload['questions'] == {'summary_scope': {'criteria': {'existing': 'unchanged'}}}


def test_model_mismatch_in_component_cannot_form_a_scope():
    scopes = catalog()
    payload = {'questions': {'summary_scope': {}}}
    mappings = factor_summary_choices(payload, scopes)
    assert mappings
    answers = response({'questions': {
        **payload['questions'],
        'route': {'criteria': {'state_summary': 'summary', 'provider': 'other'}},
    }})['answers']
    for answer in answers.values():
        answer['model'] = 'jev-1.13.0'
    answers['summary_area']['model'] = 'different-model'
    with pytest.raises(DecisionError, match='model_unverified'):
        resolve_summary_choices(answers, mappings, scopes)
