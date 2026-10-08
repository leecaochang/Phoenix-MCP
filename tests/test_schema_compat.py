"""Native and legacy schema engines retain validation and authority checks."""

import pytest
import voluptuous as legacy

from homeassistant.helpers import config_validation as cv
from homeassistant.exceptions import HomeAssistantError

from custom_components.phoenix_mcp import ai_task
from custom_components.phoenix_mcp.schema_compat import schema_engine
from custom_components.phoenix_mcp.template_scope import HelperScopeError, validate_template_selectors


@pytest.fixture(params=["legacy", "probatio"])
def engine(request):
    if request.param == "legacy":
        if legacy.Schema.__module__.startswith("probatio."):
            pytest.skip("Original Voluptuous is exercised in the HA floor environment")
        return legacy
    return pytest.importorskip("probatio")


def test_native_engine_comes_from_home_assistant_not_installed_packages(monkeypatch, engine):
    schema = engine.Schema({})
    monkeypatch.setattr(cv, "PLATFORM_SCHEMA", schema)
    assert schema_engine() is engine
    assert schema_engine(schema) is engine


def test_unknown_engine_is_not_silently_replaced(monkeypatch):
    monkeypatch.setattr(cv, "PLATFORM_SCHEMA", object())
    with pytest.raises(TypeError, match="Unsupported"):
        schema_engine()


def test_ai_task_preserves_native_and_legacy_structure_validation(engine):
    from homeassistant.helpers.selector import NumberSelector, TextSelector

    schema = engine.Schema({
        engine.Required("summary"): TextSelector({}),
        engine.Optional("priority"): NumberSelector({"min": 1, "max": 5}),
    }, extra=engine.PREVENT_EXTRA)
    advertised = ai_task._structure_to_json(schema)
    assert advertised["required"] == ["summary"]
    assert advertised["properties"]["priority"]["minimum"] == 1
    assert advertised["additionalProperties"] is False
    assert ai_task._parse_structured('{"summary":"ok","priority":2}', schema) == {
        "summary": "ok", "priority": 2,
    }
    for value in ['{}', '{"summary":7}', '{"summary":"ok","priority":9}',
                  '{"summary":"ok","other":true}']:
        with pytest.raises(HomeAssistantError):
            ai_task._parse_structured(value, schema)


@pytest.mark.parametrize("field", ["press", "device_id"])
@pytest.mark.parametrize("nested", [False, True])
def test_template_selectors_refuse_future_authority_on_both_engines(monkeypatch, engine, field, nested):
    from homeassistant.components.template import config_flow
    from homeassistant.data_entry_flow import section
    from homeassistant.helpers.selector import ActionSelector, DeviceSelector

    selector = ActionSelector({}) if field == "press" else DeviceSelector({})
    inner = engine.Schema({engine.Optional(field): selector})
    outer = engine.Schema({engine.Optional("options"): section(inner)})
    monkeypatch.setattr(config_flow, "config_schema", lambda _: outer)
    validate_template_selectors({"options": {field: []}}, "button")
    value = [{"action": "homeassistant.restart"}] if field == "press" else "device"
    options = {"options": {field: value}} if nested else {field: value}
    with pytest.raises(HelperScopeError, match="future authority"):
        validate_template_selectors(options, "button")
