"""Fail-closed static dependency checks for helper template authoring."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, TemplateSyntaxError, nodes
import voluptuous as vol

from .const import (
    HELPER_TEMPLATE_MAX_CHARS, HELPER_TEMPLATE_MAX_DEPTH, HELPER_TEMPLATE_MAX_NODES,
    HELPER_TEMPLATE_NODE_TYPES, HELPER_TEMPLATE_PURE_FILTERS,
    HELPER_TEMPLATE_PURE_FUNCTIONS, HELPER_TEMPLATE_PURE_TESTS,
    HELPER_TEMPLATE_STATE_FUNCTIONS, HELPER_TEMPLATE_STATE_PROPERTIES, HELPER_TEMPLATE_TIME_ATTRIBUTES,
    HELPER_TEMPLATE_TIME_FUNCTIONS, HELPER_TEMPLATE_TIME_METHODS,
)
from .policy_engine import _ENTITY_ID_RE, is_sensitive_key


class HelperScopeError(PermissionError):
    """The complete future dependency set cannot be established safely."""


def template_entities(source: str) -> set[str]:
    """Parse a restricted expression language, without evaluating any HA data.

    Only direct literal state lookups and explicit pure operations are accepted.
    Aliases, computed lookups, domain iteration, includes, macros and unknown
    functions/filters cannot silently introduce an unreviewed dependency.
    """
    if not any(marker in source for marker in ("{{", "{%", "{#")):
        return set()
    refusal = (
        "Helper template dependencies cannot be safely established. Use direct "
        "literal entity references and supported pure expressions; computed or "
        "broad queries, aliases and indirect calls are not supported."
    )
    if len(source) > HELPER_TEMPLATE_MAX_CHARS:
        raise HelperScopeError(refusal)
    try:
        tree = Environment().parse(source)
    except (TemplateSyntaxError, RecursionError, ValueError, OverflowError) as exc:
        raise HelperScopeError(refusal) from exc
    found: set[str] = set()
    count = 0

    def time_value(node: nodes.Node) -> bool:
        if not isinstance(node, nodes.Call):
            return False
        if isinstance(node.node, nodes.Name):
            return node.node.name in HELPER_TEMPLATE_TIME_FUNCTIONS
        return (isinstance(node.node, nodes.Getattr)
                and node.node.attr in HELPER_TEMPLATE_TIME_METHODS
                and time_value(node.node.node))

    def entity(node: nodes.Node) -> None:
        if (not isinstance(node, nodes.Const) or not isinstance(node.value, str)
                or not _ENTITY_ID_RE.fullmatch(node.value)):
            raise HelperScopeError(refusal)
        found.add(node.value)

    def attribute(node: nodes.Node | None) -> None:
        if (not isinstance(node, nodes.Const) or not isinstance(node.value, str)
                or is_sensitive_key(node.value)):
            raise HelperScopeError(
                "Helper templates require literal nonsensitive attributes; "
                "sensitive attributes and whole state/attribute objects are not supported."
            )

    def attribute_argument(node: nodes.Call | nodes.Filter | nodes.Test, index: int) -> None:
        value = node.args[index] if len(node.args) > index else next(
            (kw.value for kw in node.kwargs if kw.key == "name"), None)
        attribute(value)

    def state_path(node: nodes.Node, depth: int = 0) -> list[str] | None:
        if depth > HELPER_TEMPLATE_MAX_DEPTH:
            raise HelperScopeError(refusal)
        if isinstance(node, nodes.Name) and node.name == "states":
            return []
        if isinstance(node, (nodes.Getattr, nodes.Getitem)):
            prefix = state_path(node.node, depth + 1)
            if prefix is None:
                return None
            if isinstance(node, nodes.Getattr):
                part = node.attr
            elif isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, str):
                part = node.arg.value
            else:
                raise HelperScopeError(refusal)
            if part.startswith("_"):
                raise HelperScopeError(refusal)
            return [*prefix, part]
        return None

    def visit(node: nodes.Node, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if (count > HELPER_TEMPLATE_MAX_NODES or depth > HELPER_TEMPLATE_MAX_DEPTH
                or type(node).__name__ not in HELPER_TEMPLATE_NODE_TYPES):
            raise HelperScopeError(refusal)
        if isinstance(node, (nodes.Getattr, nodes.Getitem)):
            path = state_path(node)
            if path is not None:
                candidate = path[0] if path and "." in path[0] else ".".join(path[:2])
                if not _ENTITY_ID_RE.fullmatch(candidate):
                    raise HelperScopeError(refusal)
                suffix = path[1:] if "." in path[0] else path[2:]
                if suffix and suffix[0] == "attributes":
                    if len(suffix) < 2:
                        attribute(None)
                    for part in suffix[1:]:
                        attribute(nodes.Const(part))
                elif len(suffix) != 1 or suffix[0] not in HELPER_TEMPLATE_STATE_PROPERTIES:
                    attribute(None)
                found.add(candidate)
                return
            if (isinstance(node, nodes.Getattr)
                    and not (time_value(node.node) and node.attr in HELPER_TEMPLATE_TIME_ATTRIBUTES)):
                raise HelperScopeError(refusal)
        if isinstance(node, nodes.Name):
            raise HelperScopeError(refusal)
        if isinstance(node, nodes.Call):
            if node.dyn_args is not None or node.dyn_kwargs is not None:
                raise HelperScopeError(refusal)
            if isinstance(node.node, nodes.Getattr):
                if node.node.attr not in HELPER_TEMPLATE_TIME_METHODS or not time_value(node.node.node):
                    raise HelperScopeError(refusal)
                visit(node.node.node, depth + 1)
                name = ""
            elif isinstance(node.node, nodes.Name):
                name = node.node.name
            else:
                raise HelperScopeError(refusal)
            if name in HELPER_TEMPLATE_STATE_FUNCTIONS:
                first = node.args[0] if node.args else next(
                    (kw.value for kw in node.kwargs if kw.key == "entity_id"), None)
                if first is None:
                    raise HelperScopeError(refusal)
                entity(first)
                if name in ("state_attr", "is_state_attr"):
                    attribute_argument(node, 1)
            elif name and name not in HELPER_TEMPLATE_PURE_FUNCTIONS:
                raise HelperScopeError(refusal)
            for child in [*node.args, *node.kwargs]:
                visit(child, depth + 1)
            return
        if isinstance(node, (nodes.Filter, nodes.Test)):
            allowed = HELPER_TEMPLATE_PURE_FILTERS if isinstance(node, nodes.Filter) else HELPER_TEMPLATE_PURE_TESTS
            if node.dyn_args is not None or node.dyn_kwargs is not None:
                raise HelperScopeError(refusal)
            if node.name in HELPER_TEMPLATE_STATE_FUNCTIONS:
                if node.node is None:
                    raise HelperScopeError(refusal)
                entity(node.node)
                if node.name in ("state_attr", "is_state_attr"):
                    attribute_argument(node, 0)
            elif node.name not in allowed:
                raise HelperScopeError(refusal)
        for child in node.iter_child_nodes():
            visit(child, depth + 1)

    try:
        visit(tree)
    except RecursionError as exc:
        raise HelperScopeError(refusal) from exc
    return found


def validate_template_selectors(options: Any, template_type: str) -> None:
    """Refuse future HA scripts and unscoped device associations by schema type."""
    from homeassistant.components.template.config_flow import config_schema
    from homeassistant.helpers.selector import ActionSelector, DeviceSelector

    def walk(schema: vol.Schema, values: Any) -> None:
        if not isinstance(values, dict):
            return
        for marker, validator in schema.schema.items():
            name = marker.schema if isinstance(marker, vol.Marker) else marker
            value = values.get(name)
            if isinstance(validator, (ActionSelector, DeviceSelector)) and value:
                raise HelperScopeError(
                    "Nonempty Template actions and device associations are not supported "
                    "because their future authority cannot be established safely."
                )
            nested = getattr(validator, "schema", None)
            if isinstance(nested, vol.Schema):
                walk(nested, value)
                # HA also accepts section fields flattened into stored options.
                walk(nested, values)

    walk(config_schema(template_type), options)
