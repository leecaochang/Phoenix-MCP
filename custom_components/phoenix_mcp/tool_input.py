"""Bounded JSON admission and validation of Phoenix-owned nested objects."""

from __future__ import annotations

import math
import json
from typing import Any

from .const import MAX_REQUEST_BODY_BYTES, TOOL_INPUT_MAX_DEPTH, TOOL_INPUT_MAX_NODES
from .tool_defs import _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS, _SYSTEM_TOOL_DEFS


_SCHEMAS = {definition["name"]: definition["inputSchema"]
            for definition in (*_ENTITY_TOOL_DEFS, *_SYSTEM_TOOL_DEFS)}
_PUBLIC_SCHEMAS = {**_SCHEMAS, **{definition["name"]: definition["inputSchema"]
                                  for definition in _NATIVE_TOOL_DEFS}}
_CONTAINER_TYPES = ("object", "array")


def decode_container_strings(tool: str, arguments: dict) -> dict:
    """Accept an object or array argument that a client sent as JSON text.

    Some MCP clients serialize every argument as a string. The published schema
    stays strictly object/array; a string is decoded only for a top-level
    parameter that cannot itself be a string, and only when it parses to a
    declared container type, so text meant as text is never reinterpreted.
    The caller re-applies the admission bounds to the decoded arguments.
    """
    properties = _PUBLIC_SCHEMAS.get(tool, {}).get("properties", {})
    decoded_arguments: dict | None = None
    for name, value in arguments.items():
        schema = properties.get(name)
        if not isinstance(value, str) or not isinstance(schema, dict):
            continue
        kinds = schema.get("type")
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not isinstance(kinds, list) or "string" in kinds:
            continue
        wanted = [kind for kind in kinds if kind in _CONTAINER_TYPES]
        text = value.strip()
        if not wanted or not text.startswith(("{", "[")):
            continue
        try:
            decoded = json.loads(text)
        except (ValueError, RecursionError):
            continue
        if any(_matches_type(decoded, kind) for kind in wanted):
            decoded_arguments = decoded_arguments if decoded_arguments is not None else dict(arguments)
            decoded_arguments[name] = decoded
    return arguments if decoded_arguments is None else decoded_arguments


def tool_arguments_error(arguments: object) -> str | None:
    """Bound every ingress, including internal calls and persisted executors.

    Depth bounds also terminate cycles. Shared references remain valid when
    their expanded JSON representation fits the same traversal budget.
    """
    if not isinstance(arguments, dict):
        return "Tool arguments must be an object."
    pending: list[tuple[object, int]] = [(arguments, 0)]
    nodes, payload_bytes = 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > TOOL_INPUT_MAX_DEPTH or nodes > TOOL_INPUT_MAX_NODES:
            return "Tool arguments exceed the nesting or item limit."
        if isinstance(item, (dict, list)):
            if len(item) + len(pending) + nodes > TOOL_INPUT_MAX_NODES:
                return "Tool arguments exceed the nesting or item limit."
            payload_bytes += 2 + max(0, len(item) - 1)
            if isinstance(item, dict):
                payload_bytes += len(item)
                if any(not isinstance(key, str) for key in item):
                    return "Tool argument object keys must be strings."
                for key in item:
                    if len(key) > MAX_REQUEST_BODY_BYTES:
                        return "Tool arguments exceed the size limit."
                    payload_bytes += len(json.dumps(key, ensure_ascii=False).encode("utf-8", errors="surrogatepass"))
                    if payload_bytes > MAX_REQUEST_BODY_BYTES:
                        return "Tool arguments exceed the size limit."
                pending.extend((value, depth + 1) for value in item.values())
            else:
                pending.extend((value, depth + 1) for value in item)
        elif isinstance(item, str):
            if len(item) > MAX_REQUEST_BODY_BYTES:
                return "Tool arguments exceed the size limit."
            payload_bytes += len(json.dumps(item, ensure_ascii=False).encode("utf-8", errors="surrogatepass"))
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            try:
                if not math.isfinite(item):
                    return "Non-finite numbers are not allowed."
            except OverflowError:
                return "Tool argument numbers exceed the supported range."
            payload_bytes += len(str(item))
        elif item is not None and not isinstance(item, bool):
            return "Tool arguments must contain JSON values."
        else:
            payload_bytes += 4 if item is None or item is True else 5
        if payload_bytes > MAX_REQUEST_BODY_BYTES:
            return "Tool arguments exceed the size limit."
    return None


def _matches_type(value: Any, kind: str) -> bool:
    return {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(kind, False)


def _schema_error(value: Any, schema: dict, path: str) -> str | None:
    """Validate only the small schema vocabulary used by owned control objects."""
    choices = schema.get("oneOf")
    if choices and not any(_schema_error(value, choice, path) is None for choice in choices):
        return f"{path} has an invalid type or value."
    kinds = schema.get("type")
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds and not any(_matches_type(value, kind) for kind in kinds):
        return f"{path} must be {' or '.join(kinds)}."
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of the supported values."
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        if extra is False and any(key not in properties for key in value):
            return f"{path} contains unsupported fields."
        if any(key not in value for key in schema.get("required", [])):
            return f"{path} is missing a required field."
        if len(value) < schema.get("minProperties", 0):
            return f"{path} must be a non-empty object."
        for key, item in value.items():
            child = properties.get(key, extra if isinstance(extra, dict) else {})
            if child:
                error = _schema_error(item, child, f"{path}.{key}" if key in properties else path)
                if error:
                    return error
    elif isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", TOOL_INPUT_MAX_NODES):
            return f"{path} has an invalid number of items."
        if schema.get("uniqueItems") and any(item in value[:index] for index, item in enumerate(value)):
            return f"{path} must not contain duplicate items."
        for index, item in enumerate(value):
            error = _schema_error(item, schema.get("items", {}), f"{path}[{index}]")
            if error:
                return error
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_REQUEST_BODY_BYTES):
            return f"{path} has an invalid length."
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            return f"{path} is outside its supported range."
    return None


def nested_argument_error(tool: str, arguments: dict) -> str | None:
    """Reject closed nested shape mistakes without rejecting outer extra keys.

    Open HA configuration and service-data dictionaries keep their native
    validators. Call after the owning capability's denial check.
    """
    for name, schema in _SCHEMAS.get(tool, {}).get("properties", {}).items():
        if name not in arguments or schema.get("type") not in ("object", "array"):
            continue
        error = _schema_error(arguments[name], schema, name)
        if error:
            return error
    for name in ("projection", "scope", "lookup", "target", "address", "change"):
        if name not in _SCHEMAS.get(tool, {}).get("properties", {}):
            continue
        value = arguments.get(name)
        if not isinstance(value, dict):
            continue
        kind = value.get("kind")
        allowed: set[str] | None = None
        if name == "projection":
            allowed = {"kind", "fields"} if kind == "fields" else {"kind"}
        elif name == "target" and tool == "get_logbook":
            allowed = {"kind", "id"} if kind == "context" else {"kind", "entity_ids", "device_ids"}
        elif name == "target" and tool == "edit_energy_config":
            allowed = {"kind", {"statistic": "id", "device_name": "name", "source": "source_type"}[str(kind)]}
        elif name == "change" and kind == "remove":
            allowed = {"kind"}
        if allowed is not None and set(value) - allowed:
            return f"{name} contains fields incompatible with its kind."
    if tool in {"call_service", "dry_run_service"}:
        for target in arguments.get("targets", []):
            if target["kind"] == "all" and "ids" in target:
                return "targets with kind all must omit ids."
    if tool == "edit_energy_config" and isinstance(arguments.get("changes"), dict):
        operation = arguments.get("operation")
        changes = arguments["changes"]
        if operation in ("remove_device", "remove_source") and changes:
            return "changes must be empty for a removal."
        allowed_changes: set[str] | None = None
        if operation in ("add_device", "rename_device"):
            allowed_changes = {"name"}
        elif operation == "replace_statistic":
            allowed_changes = {"new_statistic"}
        elif operation == "set_source":
            allowed_changes = set(_SCHEMAS[tool]["properties"]["changes"]["properties"]) - {"name", "new_statistic"}
        if allowed_changes is not None and set(changes) - allowed_changes:
            return "changes contains fields incompatible with the operation."
    return None
