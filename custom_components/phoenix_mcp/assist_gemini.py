"""Gemini-safe advertised tool schemas for the Assist bridge.

HA's Gemini formatter (google_generative_ai_conversation) represents one type per
node plus nullable, enum, item counts, properties, required and items. It raises
on a type list, drops anyOf/oneOf, keeps one allOf branch, and turns an object
without properties into an unexplained {"json": string}. Phoenix keeps validating
every call against the full original schema and only ADVERTISES a lowered copy:

- a nullable type list becomes its one type with nullable;
- a union keeps one branch, the array form when there is one;
- a combinator of required-groups only is dropped, since validation still
  enforces it and names the missing field;
- an open object is shown as an object holding its JSON text in "json", which
  unwrap_json_fields turns back into the object before dispatch.

A field that accepts any JSON type has no honest single-type form, so its tool
stays unavailable to Gemini rather than being advertised as something it is not.
"""

from __future__ import annotations

import copy
import json
from typing import Any

JSON_FIELD = "json"
_ITEMS = "[]"
# Keys a combinator branch may hold and still be a pure presence constraint.
_CONSTRAINT_ONLY = frozenset({"required", "anyOf", "allOf", "oneOf", "not", "description",
                              "minProperties", "maxProperties"})


class GeminiUnrepresentable(ValueError):
    """The schema has a field Gemini cannot be shown without misrepresenting it."""


def gemini_schema(schema: dict[str, Any]) -> tuple[dict[str, Any], tuple[tuple[str, ...], ...]]:
    """Return the advertised Gemini schema and the paths of open objects it re-shaped."""
    open_paths: list[tuple[str, ...]] = []

    def lower(node: Any, path: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(node, dict):
            raise GeminiUnrepresentable(f"{'.'.join(path) or 'root'} is not a schema object")
        node = dict(node)
        for key in ("anyOf", "oneOf", "allOf"):
            if key not in node:
                continue
            branches = node.pop(key)
            if all(isinstance(b, dict) and set(b) <= _CONSTRAINT_ONLY for b in branches):
                continue
            typed = [b for b in branches if isinstance(b, dict) and "type" in b]
            if key == "allOf" or len(typed) != len(branches):
                raise GeminiUnrepresentable(f"{'.'.join(path) or 'root'} combines shapes")
            chosen = next((b for b in typed if b.get("type") == "array"), typed[0])
            node = {**node, **chosen, **({"description": node["description"]} if "description" in node else {})}
        kinds = node.get("type")
        if isinstance(kinds, list):
            concrete = [kind for kind in kinds if kind != "null"]
            if not concrete or len(concrete) > 2:
                raise GeminiUnrepresentable(f"{'.'.join(path) or 'root'} accepts any JSON type")
            if "null" in kinds:
                node["nullable"] = True
            node["type"] = "array" if "array" in concrete else concrete[0]
            if node["type"] != "array":
                node.pop("items", None)
        if node.get("type") == "object":
            properties = node.get("properties")
            if properties:
                node["properties"] = {key: lower(value, (*path, key)) for key, value in properties.items()}
            elif path:
                open_paths.append(path)
                description = str(node.get("description") or "").strip()
                node = {
                    "type": "object",
                    "description": f"{description} Give the whole object as JSON text in {JSON_FIELD}.".strip(),
                    "properties": {JSON_FIELD: {"type": "string", "description": "The object as JSON text."}},
                    "required": [JSON_FIELD],
                }
        elif node.get("type") == "array" and isinstance(node.get("items"), dict):
            node["items"] = lower(node["items"], (*path, _ITEMS))
        return node

    return lower(copy.deepcopy(schema), ()), tuple(open_paths)


def unwrap_json_fields(arguments: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> dict[str, Any]:
    """Turn each re-shaped open object back from {"json": text} into the object it encodes.

    Anything else at those paths passes through unchanged for normal validation.
    """
    if not paths:
        return arguments
    result = copy.deepcopy(arguments)
    for path in paths:
        _unwrap(result, path)
    return result


def _unwrap(container: Any, path: tuple[str, ...]) -> None:
    key, rest = path[0], path[1:]
    if key == _ITEMS:
        if isinstance(container, list):
            for index, item in enumerate(container):
                if rest:
                    _unwrap(item, rest)
                else:
                    container[index] = _decoded(item)
        return
    if not isinstance(container, dict) or key not in container:
        return
    if rest:
        _unwrap(container[key], rest)
    else:
        container[key] = _decoded(container[key])


def _decoded(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {JSON_FIELD} and isinstance(value[JSON_FIELD], str):
        try:
            decoded = json.loads(value[JSON_FIELD])
        except (ValueError, RecursionError):
            return value
        if isinstance(decoded, dict):
            return decoded
    return value
