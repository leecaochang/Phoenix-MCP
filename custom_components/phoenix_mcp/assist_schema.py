"""Lossless Assist validation for Home Assistant's original Voluptuous engine."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
import voluptuous as vol


def legacy_schema(schema: Any) -> Any:
    """Wrap a local JSON Schema without converting away its validation rules."""
    snapshot = deepcopy(schema)
    Draft202012Validator.check_schema(snapshot)
    pending = [snapshot]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        if any(key in node for key in ("$ref", "$dynamicRef", "$recursiveRef")):
            raise ValueError("Assist schemas cannot resolve references")
        for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
            pending.extend(node.get(key, {}).values())
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            pending.extend(node.get(key, []))
        for key in ("items", "additionalProperties", "not", "if", "then", "else", "contains",
                    "unevaluatedProperties", "unevaluatedItems", "propertyNames"):
            if key in node:
                pending.append(node[key])
    validator = Draft202012Validator(snapshot)

    def validate(value: Any) -> Any:
        try:
            validator.validate(value)
        except ValidationError:
            raise vol.Invalid("Tool arguments do not match the advertised schema") from None
        return value

    return vol.Schema(validate)
