"""Bounded, unambiguous JSON input shared by storage and ingestion."""

from __future__ import annotations

import json
from typing import Any

from custom_components.phoenix_mcp.mesa_core.exceptions import MesaValidationError

MAX_DEPTH = 100
MAX_NODES = 100_000


def check_structure(value: Any) -> None:
    """Bound traversal and reject cycles before recursive copy/serialization."""
    pending = [(value, 0)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if depth > MAX_DEPTH or visited > MAX_NODES:
            raise MesaValidationError("JSON structure exceeds depth or size limit")
        if isinstance(node, dict):
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MesaValidationError("duplicate JSON object key")
        result[key] = value
    return result


def loads(text: str) -> Any:
    try:
        result = json.loads(text, object_pairs_hook=_unique_object)
        check_structure(result)
        return result
    except (ValueError, RecursionError) as err:
        raise MesaValidationError("invalid or excessively complex JSON input") from err
