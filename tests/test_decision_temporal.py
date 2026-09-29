"""Typed temporal proposals require independent confidence, revision and category agreement."""
from copy import deepcopy

import pytest

from custom_components.phoenix_mcp.decision_temporal import temporal_proposal


def answer(choice, other):
    return {'choice': choice, 'confidence': 1, 'model': 'jev-1.13.0', 'probabilities': {choice: 1, other: 0}}


@pytest.mark.parametrize('change,reason', [(None, 'target_selected'), ('route_low', 'low_probability'),
    ('target_low', 'low_probability'), ('value_low', 'low_probability'), ('model', 'model_unverified'),
    ('category', 'category_mismatch'), ('missing', 'category_mismatch'), ('kind', 'numeric_unverified')])
def test_temporal_admission_boundaries(change, reason):
    checks = {'route': answer('timer_duration', 'provider'), 'target': answer('t0', 'none'), 'value': answer('value', 'none')}
    targets = {'t0': {'entity_id': 'timer.test', 'name': 'Test timer', 'eligible_categories': ['timer_duration']}}
    source = {'kind': 'duration', 'value': '00:05:00'}
    if change and change.endswith('_low'):
        selected = checks[change[:-4]]
        selected['probabilities'] = {key: 0.89 if key == selected['choice'] else 0.11 for key in selected['probabilities']}
    if change == 'model': checks['value']['model'] = 'different'
    if change == 'category': targets['t0']['eligible_categories'] = ['state_query']
    if change == 'missing': targets = {}
    if change == 'kind': source = {'kind': 'date', 'value': '2026-12-25'}
    original = deepcopy(checks)
    outcome, proposal = temporal_proposal(checks, targets, source, 'UTC', 'Start Test timer for five minutes', 'en')
    assert outcome == reason and checks == original
    if change is None:
        assert proposal['value'] == '00:05:00' and proposal['entity_ids'] == ['timer.test']
    else:
        assert proposal is None
