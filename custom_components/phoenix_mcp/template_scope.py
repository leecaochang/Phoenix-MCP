"""Fail-closed static dependency checks for helper template authoring."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, TemplateSyntaxError, nodes

from .component_guard import ComponentNotLoaded, imported_module
from .const import (
    HELPER_TEMPLATE_MAX_CHARS, HELPER_TEMPLATE_MAX_DEPTH, HELPER_TEMPLATE_MAX_NODES,
    HELPER_TEMPLATE_NODE_TYPES, HELPER_TEMPLATE_PURE_FILTERS,
    HELPER_TEMPLATE_PURE_FUNCTIONS, HELPER_TEMPLATE_PURE_TESTS,
    HELPER_TEMPLATE_STATE_FUNCTIONS, HELPER_TEMPLATE_STATE_PROPERTIES, HELPER_TEMPLATE_STRING_METHODS,
    HELPER_TEMPLATE_TIME_ATTRIBUTES, HELPER_TEMPLATE_TIME_FUNCTIONS, HELPER_TEMPLATE_TIME_METHODS,
)
from .policy_engine import _ENTITY_ID_RE, is_sensitive_key
from .schema_compat import schema_engine

# Names a template may not assign, so a local can never stand in for a lookup or a checked function.
_RESERVED_NAMES = HELPER_TEMPLATE_STATE_FUNCTIONS | HELPER_TEMPLATE_PURE_FUNCTIONS | {"states", "loop"}


class HelperScopeError(PermissionError):
    """The complete future dependency set cannot be established safely."""


def _names(values: frozenset[str]) -> str:
    return ", ".join(sorted(values))


def scope_refusal(construct: str) -> str:
    """The refusal for one construct, naming it and listing what is allowed, from the enforced sets."""
    return (
        f"Helper template dependencies cannot be safely established: {construct} is not supported. "
        "Reference entities directly with literal IDs, such as states('sensor.x'), is_state('sensor.x', 'on') "
        "or states.sensor.x.state; computed or broad lookups, macros and includes are refused. "
        f"Allowed state functions: {_names(HELPER_TEMPLATE_STATE_FUNCTIONS)}. "
        f"Functions: {_names(HELPER_TEMPLATE_PURE_FUNCTIONS)}. "
        f"Filters: {_names(HELPER_TEMPLATE_PURE_FILTERS)}. "
        f"Tests: {_names(HELPER_TEMPLATE_PURE_TESTS)}. "
        f"String methods, not on a states.* path: {_names(HELPER_TEMPLATE_STRING_METHODS)}. "
        f"Date and time methods: {_names(HELPER_TEMPLATE_TIME_METHODS)}. "
        "Statements: if, set, and for over a checked expression."
    )


def template_entities(source: str) -> set[str]:
    """Return the literal entity IDs a helper template depends on, without evaluating it.

    Accepts only literal state lookups and explicit pure operations; anything else raises
    HelperScopeError naming it. Self-assigned set and for names are allowed.
    """
    if not any(marker in source for marker in ("{{", "{%", "{#")):
        return set()
    if len(source) > HELPER_TEMPLATE_MAX_CHARS:
        raise HelperScopeError(scope_refusal(f"a template longer than {HELPER_TEMPLATE_MAX_CHARS} characters"))
    try:
        tree = Environment().parse(source)
    except TemplateSyntaxError as exc:
        raise HelperScopeError(scope_refusal(f"a template that does not parse ({exc.message})")) from exc
    except (RecursionError, ValueError, OverflowError) as exc:
        raise HelperScopeError(scope_refusal("a template this large or deeply nested")) from exc
    found: set[str] = set()
    assigned: set[str] = set()
    count = 0

    def refuse(construct: str) -> HelperScopeError:
        return HelperScopeError(scope_refusal(construct))

    def time_value(node: nodes.Node) -> bool:
        if not isinstance(node, nodes.Call):
            return False
        if isinstance(node.node, nodes.Name):
            return node.node.name in HELPER_TEMPLATE_TIME_FUNCTIONS
        return (isinstance(node.node, nodes.Getattr)
                and node.node.attr in HELPER_TEMPLATE_TIME_METHODS
                and time_value(node.node.node))

    def entity(node: nodes.Node, function: str) -> None:
        if (not isinstance(node, nodes.Const) or not isinstance(node.value, str)
                or not _ENTITY_ID_RE.fullmatch(node.value)):
            raise refuse(f"'{function}' without a literal entity ID")
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
            raise refuse("a template this deeply nested")
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
                raise refuse("a computed states[...] lookup")
            if part.startswith("_"):
                raise refuse(f"the private attribute '{part}'")
            return [*prefix, part]
        return None

    def targets(node: nodes.Node) -> None:
        """Record the names a set or for assigns; anything but plain names is refused."""
        names = node.items if isinstance(node, nodes.Tuple) else [node]
        for name in names:
            if not isinstance(name, nodes.Name):
                raise refuse("an assignment to anything but a plain name")
            if name.name in _RESERVED_NAMES or name.name.startswith("_"):
                raise refuse(f"assigning to the reserved name '{name.name}'")
            assigned.add(name.name)

    def visit(node: nodes.Node, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > HELPER_TEMPLATE_MAX_NODES or depth > HELPER_TEMPLATE_MAX_DEPTH:
            raise refuse("a template this large or deeply nested")
        if type(node).__name__ not in HELPER_TEMPLATE_NODE_TYPES:
            raise refuse(f"the {type(node).__name__} construct")
        if isinstance(node, (nodes.Assign, nodes.AssignBlock, nodes.For)):
            targets(node.target)
            for child in node.iter_child_nodes(exclude=("target",)):
                visit(child, depth + 1)
            return
        if isinstance(node, (nodes.Getattr, nodes.Getitem)):
            path = state_path(node)
            if path is not None:
                candidate = path[0] if path and "." in path[0] else ".".join(path[:2])
                if not _ENTITY_ID_RE.fullmatch(candidate):
                    raise refuse(f"the broad lookup states.{'.'.join(path)}" if path else "the whole states object")
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
                raise refuse(f"the attribute '{node.attr}'")
        if isinstance(node, nodes.Name):
            if node.ctx == "load" and node.name in assigned:
                return
            raise refuse(f"the name '{node.name}'")
        if isinstance(node, nodes.Call):
            if node.dyn_args is not None or node.dyn_kwargs is not None:
                raise refuse("a call with *args or **kwargs")
            if isinstance(node.node, nodes.Getattr):
                receiver, method = node.node.node, node.node.attr
                if method in HELPER_TEMPLATE_TIME_METHODS and time_value(receiver):
                    pass
                elif method in HELPER_TEMPLATE_STRING_METHODS:
                    # A state object's own methods stay out of reach; its value read with states() does not.
                    if state_path(receiver) is not None:
                        raise refuse(f"the method '{method}' on a states.* path")
                else:
                    raise refuse(f"the method '{method}'")
                visit(receiver, depth + 1)
                name = ""
            elif isinstance(node.node, nodes.Name):
                name = node.node.name
            else:
                raise refuse("an indirect call")
            if name in HELPER_TEMPLATE_STATE_FUNCTIONS:
                first = node.args[0] if node.args else next(
                    (kw.value for kw in node.kwargs if kw.key == "entity_id"), None)
                if first is None:
                    raise refuse(f"'{name}' without a literal entity ID")
                entity(first, name)
                if name in ("state_attr", "is_state_attr"):
                    attribute_argument(node, 1)
            elif name and name not in HELPER_TEMPLATE_PURE_FUNCTIONS:
                raise refuse(f"the function '{name}'")
            for child in [*node.args, *node.kwargs]:
                visit(child, depth + 1)
            return
        if isinstance(node, (nodes.Filter, nodes.Test)):
            kind = "filter" if isinstance(node, nodes.Filter) else "test"
            allowed = HELPER_TEMPLATE_PURE_FILTERS if kind == "filter" else HELPER_TEMPLATE_PURE_TESTS
            if node.dyn_args is not None or node.dyn_kwargs is not None:
                raise refuse(f"a {kind} with *args or **kwargs")
            if node.name in HELPER_TEMPLATE_STATE_FUNCTIONS:
                if node.node is None:
                    raise refuse(f"the {kind} '{node.name}' without a literal entity ID")
                entity(node.node, node.name)
                if node.name in ("state_attr", "is_state_attr"):
                    attribute_argument(node, 0)
            elif node.name not in allowed:
                raise refuse(f"the {kind} '{node.name}'")
        for child in node.iter_child_nodes():
            visit(child, depth + 1)

    try:
        visit(tree)
    except RecursionError as exc:
        raise refuse("a template this large or deeply nested") from exc
    return found


def validate_template_selectors(options: Any, template_type: str) -> None:
    """Refuse nonempty Template actions and device associations, found by schema type.

    Raises HelperScopeError. Their future authority cannot be established.
    """
    # The creation flow imported the module already; an edit has a loaded Template entry.
    try:
        config_schema = imported_module("homeassistant.components.template.config_flow").config_schema
    except ComponentNotLoaded as err:
        raise HelperScopeError("The Template integration is not loaded.") from err
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.selector import ActionSelector
    from homeassistant.helpers.selector import DeviceSelector

    def walk(schema: Any, values: Any) -> None:
        if not isinstance(values, dict):
            return
        engine = schema_engine(schema)
        for marker, validator in schema.schema.items():
            name = marker.schema if isinstance(marker, engine.Marker) else marker
            value = values.get(name)
            if isinstance(validator, (ActionSelector, DeviceSelector)) and value:
                raise HelperScopeError(
                    "Nonempty Template actions and device associations are not supported "
                    "because their future authority cannot be established safely."
                )
            nested = getattr(validator, "schema", None)
            if isinstance(nested, engine.Schema):
                walk(nested, value)
                # HA also accepts section fields flattened into stored options.
                walk(nested, values)

    walk(config_schema(template_type), options)
