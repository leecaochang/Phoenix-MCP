"""Real Jinja failures and adversarial worker diagnostic protocol coverage."""

from __future__ import annotations

import json

import pytest
from jinja2 import TemplateSyntaxError

from custom_components.phoenix_mcp.const import (
    TEMPLATE_DIAGNOSTIC_BYTES,
    TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS,
    TEMPLATE_DIAGNOSTIC_SOURCE_CHARS,
    TEMPLATE_FAILURE_MESSAGES,
)
from custom_components.phoenix_mcp.helpers import render_template_snapshot
from custom_components.phoenix_mcp.template_diagnostics import (
    TemplateOutputLimitError,
    describe_template_failure,
    format_template_failure,
)


def _diagnose(template, permitted=None):
    with pytest.raises(Exception) as failure:
        render_template_snapshot(template, permitted or {})
    detail = describe_template_failure(failure.value)
    return detail, format_template_failure(json.dumps(detail).encode(), template)


@pytest.mark.parametrize('template,kind,line', [
    ('first\n{{ 1 + }}', 'syntax', 2),
    ('\n\n{{ 1 / 0 }}', 'division', 3),
    ('first\n{{ missing.foo }}', 'undefined', 2),
    ("first\n{{ 'bad' | int }}", 'value', 2),
    ("first\n{{ 1 + 'bad' }}", 'type', 2),
    ('\n{% macro fail() %}\n{{ 1 / 0 }}\n{% endmacro %}\n{{ fail() }}', 'division', 3),
    ('first\r\n{{ 1 / 0 }}', 'division', 2),
    ('first\r{{ 1 / 0 }}', 'division', 2),
    ('雪\u2028text\n{{ 1 / 0 }}', 'division', 2),
    ('first\n{{ missing | nonexistent_filter }}', 'syntax', 2),
    ('first\n{{ "x".__class__.__mro__ }}', 'security', 2),
])
def test_real_template_failures_have_original_source_locations(template, kind, line):
    detail, text = _diagnose(template)
    assert detail['kind'] == kind
    assert detail['line'] == line
    assert f'Line {line}: ' in text
    assert 'Traceback' not in text
    assert 'site-packages' not in text


def test_runtime_diagnostic_does_not_echo_state_values():
    from homeassistant.core import State
    from custom_components.phoenix_mcp.helpers import ScrubbedState

    state = ScrubbedState(State('sensor.allowed', 'private-value-never-return'))
    detail, text = _diagnose("{{ states('sensor.allowed') | int }}", {state.entity_id: state})
    assert detail['kind'] == 'value'
    assert 'private-value-never-return' not in json.dumps(detail) + text


@pytest.mark.parametrize('exc,kind', [
    (TemplateOutputLimitError('secret'), 'output'),
    (MemoryError('secret'), 'memory'),
    (RecursionError('secret'), 'recursion'),
    (RuntimeError('secret'), 'worker'),
    (ValueError('secret'), 'worker'),
])
def test_non_template_and_budget_failures_never_expose_messages(exc, kind):
    detail = describe_template_failure(exc)
    assert detail == {'kind': kind, 'line': None}
    assert 'secret' not in format_template_failure(json.dumps(detail).encode(), 'source')


@pytest.mark.parametrize('payload', [
    b'invalid json', b'\xff', b'[]', b'null', b'{"kind":[]}',
    b'{"kind":"new-category"}', b'{"kind":"syntax","message":42}',
    b'{"kind":"value","message":"secret"}',
    b'{"kind":"value","source_line":"secret"}',
    b'{"kind":"syntax","message":"' + b'x' * (TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS + 4) + b'"}',
    b'x' * (TEMPLATE_DIAGNOSTIC_BYTES + 1),
    b'[' * 1500 + b']' * 1500,
])
def test_invalid_worker_protocol_fails_closed(payload):
    assert format_template_failure(payload, 'source') == TEMPLATE_FAILURE_MESSAGES['worker']


@pytest.mark.parametrize('line', [None, True, False, -1, 0, 3, '1', 1.5, [], {}])
def test_invalid_locations_are_omitted(line):
    payload = json.dumps({'kind': 'division', 'line': line}).encode()
    assert format_template_failure(payload, 'first\nsecond') == TEMPLATE_FAILURE_MESSAGES['division']


def test_diagnostic_and_source_are_bounded_and_printable():
    exc = TemplateSyntaxError('雪' * 2000 + '\x1b\n', 1)
    detail = describe_template_failure(exc)
    payload = json.dumps(detail, ensure_ascii=False).encode()
    assert len(payload) <= TEMPLATE_DIAGNOSTIC_BYTES
    text = format_template_failure(payload, '\x1b' + '雪' * 2000)
    assert '\x1b' not in text
    assert len(text.splitlines()[1]) <= TEMPLATE_DIAGNOSTIC_SOURCE_CHARS + len('Line 1: ') + 3
    assert text.endswith('...')


def test_output_limit_is_classified_without_partial_output(monkeypatch):
    monkeypatch.setattr('homeassistant.helpers.template.MAX_TEMPLATE_OUTPUT', 8)
    detail, text = _diagnose('partial-output')
    assert detail['kind'] == 'output'
    assert 'partial-output' not in text


def test_successful_warning_semantics_unchanged():
    assert render_template_snapshot('prefix{{ missing }}suffix', {}) == 'prefixsuffix'
