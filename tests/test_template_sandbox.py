"""The token template sandbox exposes only permission-filtered entity state.

render_template_for_token() must not leak state through helper functions, Jinja2
filters or enumeration helpers.
"""

from __future__ import annotations

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.helpers import render_template_for_token
from custom_components.phoenix_mcp.token_store import (
    PermissionNode,
    PermissionTree,
    TokenRecord,
)


def _scoped_token() -> TokenRecord:
    """A token that can READ sensor.allowed but has no grant for sensor.secret."""
    return TokenRecord(
        id="t1",
        name="scoped",
        token_hash="x",
        created_at=utcnow(),
        created_by="admin",
        cap_template_render="allow",
        permissions=PermissionTree(
            domains={},
            devices={},
            entities={"sensor.allowed": PermissionNode(state="YELLOW")},
        ),
    )


@pytest.fixture
def two_states(hass):
    hass.states.async_set("sensor.allowed", "42")
    hass.states.async_set("sensor.secret", "topsecret")
    return hass


def test_permitted_entity_renders(two_states):
    token = _scoped_token()
    assert render_template_for_token("{{ states('sensor.allowed') }}", token, two_states) == "42"


def test_out_of_scope_state_function_returns_unknown(two_states):
    token = _scoped_token()
    assert render_template_for_token("{{ states('sensor.secret') }}", token, two_states) == "unknown"


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{{ state_attr('sensor.secret', 'friendly_name') }}", "None"),
        ("{{ is_state('sensor.secret', 'topsecret') }}", "False"),
        ("{{ has_value('sensor.secret') }}", "False"),
    ],
)
def test_state_helpers_as_functions_do_not_leak(two_states, template, expected):
    """The permission-restricted helper functions answer as if the entity is absent."""
    token = _scoped_token()
    out = render_template_for_token(template, token, two_states)
    assert out == expected
    assert "topsecret" not in out


@pytest.mark.parametrize(
    "template",
    [
        "{{ 'sensor.secret' | states }}",
        "{{ 'sensor.secret' | state_attr('friendly_name') }}",
        "{{ 'sensor.secret' | has_value }}",
        "{{ ['sensor.secret'] | expand | map(attribute='entity_id') | list }}",
    ],
)
def test_state_helpers_as_filters_do_not_leak(two_states, template):
    """State helpers used as filters must not expose real state."""
    token = _scoped_token()
    with pytest.raises(Exception) as exc:
        render_template_for_token(template, token, two_states)
    assert "topsecret" not in str(exc.value)


@pytest.mark.parametrize(
    "template",
    [
        "{{ area_entities('Kitchen') }}",
        "{{ integration_entities('hue') }}",
        "{{ states.sensor | map(attribute='entity_id') | list }}",
    ],
)
def test_enumeration_helpers_blocked(two_states, template):
    """Enumeration helpers return empty (blocklist stubs) or raise; none reveals sensor.secret."""
    token = _scoped_token()
    # Control render first: shows the sandbox renderer works in this code path, so
    # a wholesale rendering failure fails loudly instead of passing as "did not leak".
    assert render_template_for_token("{{ states('sensor.allowed') }}", token, two_states) == "42"
    try:
        out = render_template_for_token(template, token, two_states)
    except Exception as exc:
        # A controlled block (raise) is acceptable, but the exception text must not
        # leak either.
        assert "sensor.secret" not in str(exc) and "topsecret" not in str(exc)
        return
    assert "sensor.secret" not in out
    assert "topsecret" not in out


def test_time_helpers_available(two_states):
    """now()/utcnow()/today_at() work although the hass-less environment omits them by default."""
    token = _scoped_token()
    out = render_template_for_token("{{ now().year }}", token, two_states)
    assert int(out) >= 2024
    out = render_template_for_token("{{ utcnow().year }}", token, two_states)
    assert int(out) >= 2024
    # today_at returns a datetime; just confirm it renders without error.
    render_template_for_token("{{ today_at('00:00') }}", token, two_states)


def test_safe_math_filters_available(two_states):
    token = _scoped_token()
    assert render_template_for_token("{{ [3,1,2] | sort | first }}", token, two_states) == "1"
    assert render_template_for_token("{{ 10 | int + 5 }}", token, two_states) == "15"


def test_sandbox_audit_reports_no_unrecognized_names(caplog):
    """The runtime audit emits no warning for the installed HA version.

    Every name in the render environment is blocked, overridden, or known-safe.
    """
    import logging

    from custom_components.phoenix_mcp import _audit_template_sandbox

    with caplog.at_level(logging.WARNING, logger="custom_components.phoenix_mcp"):
        _audit_template_sandbox()
    sandbox_warnings = [
        r for r in caplog.records if "template sandbox" in r.getMessage()
    ]
    assert not sandbox_warnings, [r.getMessage() for r in sandbox_warnings]
