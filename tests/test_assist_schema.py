"""Legacy Assist validation preserves bounds, composition and local-only schemas."""

from copy import deepcopy

import pytest
import voluptuous as vol

from custom_components.phoenix_mcp.assist_schema import legacy_schema


@pytest.mark.parametrize("value", [True, False, 0, 4, 1.5, "2", None])
def test_integer_bounds_refuse_without_coercing(value):
    validator = legacy_schema({"type": "integer", "minimum": 1, "maximum": 3})
    with pytest.raises(vol.Invalid):
        validator(value)


def test_composition_and_nested_values_preserve_schema_and_input():
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string"}, "area": {"type": "string"},
            "value": {"type": ["integer", "null"], "minimum": 0},
        },
        "allOf": [{"anyOf": [{"required": ["name"]}, {"required": ["area"]}]},
                  {"required": ["value"]}],
    }
    original = deepcopy(schema)
    validator = legacy_schema(schema)
    schema["allOf"].clear()
    for value in ({"name": "Lamp", "value": 0}, {"area": "Room", "value": None}):
        expected = deepcopy(value)
        assert validator(value) == expected
        assert value == expected
    for value in ({"name": "Lamp"}, {"value": 1}, {"area": "Room", "value": True},
                  {"name": "Lamp", "value": 1, "extra": 2}):
        with pytest.raises(vol.Invalid):
            validator(value)
    assert original["allOf"]


def test_exact_one_union_is_not_any_union():
    validator = legacy_schema({"oneOf": [{"type": "integer"}, {"type": "number"}]})
    assert validator(1.5) == 1.5
    with pytest.raises(vol.Invalid):
        validator(1)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
@pytest.mark.parametrize("schema_path", ["properties", "items", "allOf", "$defs"])
def test_nested_references_refuse_without_resolving_resources(keyword, schema_path):
    reference = {keyword: "https://untrusted.invalid/schema"}
    nested = ([reference] if schema_path == "allOf" else
              {"nested": reference} if schema_path in ("properties", "$defs") else reference)
    with pytest.raises(ValueError, match="cannot resolve references"):
        legacy_schema({schema_path: nested})


def test_reference_like_property_names_and_extensions_remain_plain_data():
    validator = legacy_schema({
        "type": "object", "properties": {"$ref": {"type": "string"}},
        "x-provider": {"$ref": "annotation"},
    })
    assert validator({"$ref": "ordinary value"}) == {"$ref": "ordinary value"}


def test_validation_error_does_not_echo_the_bad_argument():
    validator = legacy_schema({"type": "integer"})
    with pytest.raises(vol.Invalid) as error:
        validator("private caller value")
    assert "private caller value" not in str(error.value)


@pytest.mark.parametrize("nested", [False, True])
def test_unsupported_dialect_cannot_switch_reference_or_validation_semantics(nested):
    schema = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}
    if nested:
        schema = {"type": "object", "properties": {"value": schema}}
    with pytest.raises(ValueError, match="require JSON Schema 2020-12"):
        legacy_schema(schema)
