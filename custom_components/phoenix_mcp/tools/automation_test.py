"""Test an automation trigger or condition against the live home before it is saved.

test_trigger registers trigger definitions with Home Assistant's own trigger helpers for a short,
bounded wait and reports the first firing; test_condition evaluates one condition now. Both read
only what the key may read: every entity a definition names, by ID, registry ID or literal template
lookup, needs Read, proved before anything runs and again before a result is returned. A definition
whose reach cannot be proved is refused whole, and a hidden and a missing entity are refused alike.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.core import Context, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import condition as ha_condition
from homeassistant.helpers import trace as ha_trace
from homeassistant.helpers import trigger as ha_trigger
from homeassistant.helpers.template import Template
from homeassistant.loader import async_get_integration

from ..access_key_store import AccessKeyRecord
from ..capability import effective_cap
from ..const import (
    AUTOMATION_TEST_CONDITIONS, AUTOMATION_TEST_MAX_TRIGGERS, AUTOMATION_TEST_TRIGGERS,
    AUTOMATION_TEST_UNSCOPED_KEYS, CAP_DENY, DOMAIN, HELPER_TEMPLATE_PURE_FUNCTIONS,
    HELPER_TEMPLATE_STATE_FUNCTIONS, MAX_SUBSCRIPTION_SECONDS,
)
from ..policy_engine import (
    _ENTITY_ID_RE, Permission, canonical_entity_id, filter_service_response, is_sensitive_key,
    resolve_read, scrub_sensitive_attributes,
)
from ..redaction import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text
from ..request_authority import check_request_authority
from ..template_scope import HelperScopeError, template_entities
from ..tool_common import _tool_error, _tool_success

_LOGGER = logging.getLogger(__name__)

_TEMPLATE_MARKERS = ("{{", "{%", "{#")
_KIND_KEYS = frozenset({"platform", "trigger", "condition"})
_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_RESERVED_VARIABLES = HELPER_TEMPLATE_STATE_FUNCTIONS | HELPER_TEMPLATE_PURE_FUNCTIONS | {"states", "loop"}
_NOT_FOUND = (
    "Not found: this definition names an entity that does not exist or that this access key "
    "cannot read. Name only entities the key can read; nothing was registered."
)
_UNSCOPED = (
    "Device, area, floor and label references are not supported here, as they reach entities "
    "the definition does not name. Name the entities instead, with entity_id."
)
_MAX_RESULT_DEPTH = 12


class _Refused(Exception):
    """A definition this key may not test; the message is safe to return."""


class _Scope:
    """What a definition reads: entity IDs, and (entity, attribute) pairs it compares."""

    def __init__(self) -> None:
        self.entities: set[str] = set()
        self.attributes: set[tuple[str, str]] = set()


def _entity_strings(value: str, hass: HomeAssistant) -> list[str]:
    """Entity IDs a string names, comma-separated lists and registry IDs included."""
    found = []
    for piece in value.split(","):
        candidate = canonical_entity_id(piece.strip(), hass)
        if _ENTITY_ID_RE.fullmatch(candidate):
            found.append(candidate)
    return found


def _collect(value: Any, hass: HomeAssistant, scope: _Scope, data_names: frozenset[str], depth: int = 0) -> None:
    """Record every reference in a caller's definition, or raise _Refused."""
    if depth > _MAX_RESULT_DEPTH:
        raise _Refused("This definition is nested too deeply to check.")
    if isinstance(value, str):
        if any(marker in value for marker in _TEMPLATE_MARKERS):
            try:
                scope.entities |= template_entities(value, data_names, scope.attributes)
            except HelperScopeError as err:
                raise _Refused(str(err).replace("Helper template", "Template")) from err
        else:
            scope.entities.update(_entity_strings(value, hass))
        return
    if isinstance(value, list):
        for item in value:
            _collect(item, hass, scope, data_names, depth + 1)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key in AUTOMATION_TEST_UNSCOPED_KEYS and item:
            raise _Refused(_UNSCOPED)
        if key == "target" and isinstance(item, dict) and any(
                field != "entity_id" and target for field, target in item.items()):
            raise _Refused(_UNSCOPED)
        if key in _KIND_KEYS and isinstance(item, str):
            continue
        if key == "attribute":
            if not isinstance(item, str) or is_sensitive_key(item):
                raise _Refused("This attribute cannot be compared here: it holds a secret or is not a name.")
            named = value.get("entity_id")
            for part in named if isinstance(named, list) else [named]:
                if isinstance(part, str):
                    scope.attributes.update((entity_id, item) for entity_id in _entity_strings(part, hass))
            continue
        _collect(item, hass, scope, data_names, depth + 1)


def _scope_error(scope: _Scope, access_key: AccessKeyRecord, hass: HomeAssistant) -> str | None:
    """The refusal when a recorded reference is not readable as written, else None."""
    for entity_id in scope.entities | {entity for entity, _ in scope.attributes}:
        if resolve_read(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
            return _NOT_FOUND
    for entity_id, attribute in scope.attributes:
        state = hass.states.get(entity_id)
        if state is None or attribute not in state.attributes:
            continue
        current = state.attributes[attribute]
        # The attribute's value must read the same through the key's scrub, or a comparison
        # would test what get_state hides from this key.
        if filter_service_response(current, access_key, hass, keep_unknown=True) != current:
            return (
                f"The attribute {attribute} cannot be compared here: its value names something "
                "this access key cannot see."
            )
    return None


def _variables(args: dict) -> dict[str, Any]:
    """The caller's plain-data variables, or raise _Refused."""
    variables = args.get("variables")
    if variables is None:
        return {}
    if not isinstance(variables, dict):
        raise _Refused("variables must be an object of plain values.")
    for name in variables:
        if not isinstance(name, str) or not _VARIABLE_NAME_RE.fullmatch(name) or name in _RESERVED_VARIABLES:
            raise _Refused(
                f"The variable name {name!r} is not allowed: use letters, digits and underscores, "
                "starting with a letter, and not a template function's name."
            )
    return variables


def _trigger_kinds(triggers: list) -> list[str]:
    """The kind of each raw trigger, or raise _Refused for one this tool cannot scope."""
    kinds = []
    for item in triggers:
        kind = item.get("trigger", item.get("platform")) if isinstance(item, dict) else None
        if not isinstance(kind, str) or not kind:
            raise _Refused("Each trigger must be an object with a trigger key, such as {\"trigger\": \"state\"}.")
        if "." not in kind and kind not in AUTOMATION_TEST_TRIGGERS:
            raise _Refused(
                f"The {kind} trigger cannot be tested here. Supported: "
                f"{', '.join(sorted(AUTOMATION_TEST_TRIGGERS))}, and entity triggers such as "
                "light.turned_on with an entity_id target."
            )
        kinds.append(kind)
    return kinds


def _condition_kinds(condition: Any, depth: int = 0) -> list[str]:
    """Every condition kind in a raw condition tree, or raise _Refused."""
    if depth > _MAX_RESULT_DEPTH:
        raise _Refused("This condition is nested too deeply to check.")
    if isinstance(condition, str):
        return ["template"]
    if not isinstance(condition, dict):
        raise _Refused("A condition must be an object or a template string.")
    kind = condition.get("condition")
    if kind is None:
        shorthand = [key for key in ("and", "or", "not") if key in condition]
        if len(shorthand) != 1:
            raise _Refused("Each condition must be an object with a condition key, such as {\"condition\": \"state\"}.")
        kind, nested = shorthand[0], condition[shorthand[0]]
    else:
        nested = condition.get("conditions")
    if not isinstance(kind, str) or ("." not in kind and kind not in AUTOMATION_TEST_CONDITIONS):
        raise _Refused(
            f"The {kind} condition cannot be tested here. Supported: "
            f"{', '.join(sorted(AUTOMATION_TEST_CONDITIONS))}, and entity conditions such as "
            "switch.is_on with an entity_id target."
        )
    kinds = [kind]
    if kind in ("and", "or", "not"):
        for item in nested if isinstance(nested, list) else [nested] if nested is not None else []:
            kinds.extend(_condition_kinds(item, depth + 1))
    return kinds


async def _entity_based(hass: HomeAssistant, platform: str, key: str) -> bool:
    """Whether a new-style trigger or condition key is one of Core's entity-target classes."""
    base: type
    try:
        if platform == "trigger":
            # Local: Core's entity trigger and condition bases arrived after the supported floor.
            from homeassistant.helpers.trigger.entity_trigger import EntityTriggerBase
            base, relative = EntityTriggerBase, ha_trigger.get_relative_description_key
        else:
            # Local: as above.
            from homeassistant.helpers.condition.entity_condition import EntityConditionBase
            base, relative = EntityConditionBase, ha_condition.get_relative_description_key
    except ImportError:
        return False
    domain = key.split(".", 1)[0]
    try:
        module = await (await async_get_integration(hass, domain)).async_get_platform(platform)
        getter = module.async_get_triggers if platform == "trigger" else module.async_get_conditions
        found = (await getter(hass)).get(relative(domain, key))
    except Exception:  # an integration without that platform is simply not entity-based
        return False
    return isinstance(found, type) and issubclass(found, base)


async def _require_entity_based(hass: HomeAssistant, platform: str, kinds: list[str]) -> None:
    for kind in kinds:
        if "." in kind and not await _entity_based(hass, platform, kind):
            raise _Refused(
                f"The {kind} {platform} cannot be tested here: only Core's entity {platform}s, "
                "which read just the entities in their entity_id target, are supported."
            )


def _trigger_json(value: Any, access_key: AccessKeyRecord, hass: HomeAssistant, depth: int = 0) -> Any:
    """A trigger's variables as JSON this key may read: states scrubbed, contexts dropped."""
    if depth > _MAX_RESULT_DEPTH:
        return "<redacted>"
    if isinstance(value, State):
        return scrub_sensitive_attributes(value, access_key, hass)
    if isinstance(value, dict):
        return {str(key): _trigger_json(item, access_key, hass, depth + 1)
                for key, item in value.items() if not isinstance(item, Context)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_trigger_json(item, access_key, hass, depth + 1) for item in value]
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, Template):
        return value.template
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _error_text(err: Exception, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    text = redact_secrets_in_text(str(err)[:500]) or "invalid definition"
    return redact_inaccessible_entity_ids_in_text(text, access_key, hass, sentinel="<redacted>")


def _timeout(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = MAX_SUBSCRIPTION_SECONDS
    return max(1, min(seconds, MAX_SUBSCRIPTION_SECONDS))


async def _tool_test_trigger(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: wait a bounded time for trigger definitions to fire, and report the first firing."""
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "test_trigger"
    raw = args.get("trigger")
    triggers = raw if isinstance(raw, list) else [raw]
    if not triggers or len(triggers) > AUTOMATION_TEST_MAX_TRIGGERS:
        return _tool_error(
            f"trigger must be one trigger object or a list of 1 to {AUTOMATION_TEST_MAX_TRIGGERS}."
        ), "invalid_request", "test_trigger"
    scope = _Scope()
    try:
        variables = _variables(args)
        kinds = _trigger_kinds(triggers)
        _collect(triggers, hass, scope, frozenset(variables))
        if (refusal := _scope_error(scope, access_key, hass)) is not None:
            return _tool_error(refusal), "not_found" if refusal is _NOT_FOUND else "denied", "test_trigger"
        await _require_entity_based(hass, "trigger", kinds)
    except _Refused as err:
        return _tool_error(str(err)), "invalid_request", "test_trigger"
    try:
        validated = await ha_trigger.async_validate_trigger_config(hass, cv.TRIGGER_SCHEMA(triggers))
    except (vol.Invalid, HomeAssistantError) as err:
        return _tool_error(f"Home Assistant refused this trigger: {_error_text(err, access_key, hass)}"), \
            "invalid_request", "test_trigger"
    timeout = _timeout(args.get("timeout", MAX_SUBSCRIPTION_SECONDS))
    fired: asyncio.Future = hass.loop.create_future()

    @callback
    def _on_fire(run_variables: dict[str, Any], context: Context | None = None) -> None:
        if not fired.done():
            fired.set_result(run_variables)

    def _log(level: int, message: str, **kwargs: Any) -> None:
        _LOGGER.debug("test_trigger: %s", message)

    check_request_authority()
    remove = await ha_trigger.async_initialize_triggers(
        hass, validated, _on_fire, DOMAIN, "test_trigger", _log, variables=variables or None)
    try:
        result = await asyncio.wait_for(asyncio.shield(fired), timeout)
    except TimeoutError:
        result = None
    finally:
        if remove is not None:
            remove()
    # Authority and scope may have changed during the wait.
    check_request_authority()
    if (refusal := _scope_error(scope, access_key, hass)) is not None:
        return _tool_error(refusal), "not_found" if refusal is _NOT_FOUND else "denied", "test_trigger"
    body: dict[str, Any] = {"fired": result is not None, "timeout_seconds": timeout}
    if result is not None:
        trigger = _trigger_json(result.get("trigger", result), access_key, hass)
        body["trigger"] = filter_service_response(trigger, access_key, hass, keep_unknown=True)
    return _tool_success(json.dumps(body, default=str)), "allowed", "test_trigger"


async def _tool_test_condition(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: evaluate one condition against current states, with optional plain-data variables."""
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "test_condition"
    raw = args.get("condition")
    if raw is None:
        return _tool_error("Missing required argument: condition"), "invalid_request", "test_condition"
    scope = _Scope()
    try:
        variables = _variables(args)
        kinds = _condition_kinds(raw)
        _collect(raw, hass, scope, frozenset(variables))
        if (refusal := _scope_error(scope, access_key, hass)) is not None:
            return _tool_error(refusal), "not_found" if refusal is _NOT_FOUND else "denied", "test_condition"
        await _require_entity_based(hass, "condition", kinds)
    except _Refused as err:
        return _tool_error(str(err)), "invalid_request", "test_condition"
    try:
        validated = await ha_condition.async_validate_condition_config(hass, cv.CONDITION_SCHEMA(raw))
        checker = await ha_condition.async_from_config(hass, validated)
    except (vol.Invalid, HomeAssistantError) as err:
        return _tool_error(f"Home Assistant refused this condition: {_error_text(err, access_key, hass)}"), \
            "invalid_request", "test_condition"
    check_request_authority()
    condition_trace = ha_trace.trace_get()
    try:
        with ha_trace.suppress_template_error_logging():
            passed = checker.async_check(variables=variables or None)
    except HomeAssistantError as err:
        return _tool_error(f"The condition could not be evaluated: {_error_text(err, access_key, hass)}"), \
            "invalid_request", "test_condition"
    finally:
        if unload := getattr(checker, "async_unload", None):
            unload()
    if (refusal := _scope_error(scope, access_key, hass)) is not None:
        return _tool_error(refusal), "not_found" if refusal is _NOT_FOUND else "denied", "test_condition"
    body: dict[str, Any] = {"result": bool(passed)}
    errors = [
        _error_text(Exception(message), access_key, hass)
        for elements in (condition_trace or {}).values()
        for element in elements
        for message in getattr(element, "template_errors", None) or []
    ]
    if errors:
        body["template_errors"] = errors[:10]
    return _tool_success(json.dumps(body, default=str)), "allowed", "test_condition"
