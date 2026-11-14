"""Test an automation trigger or condition against the live home before it is saved.

test_trigger registers trigger definitions with Home Assistant's own trigger helpers for a short,
bounded wait and reports the first firing; test_condition evaluates one condition now. test_trigger
admits templates only for a key whose cap_automation_write is Allow, after the template worker has
compiled and trial-rendered them, since such a key could save the same template in an automation
without review; Home Assistant then renders them itself. test_condition renders each one in the disposable template worker over
the key's scrubbed states and hands Home Assistant only a reference to the result. Both read
only what the key may read: every entity a definition names, by ID, registry ID or literal template
lookup, every entity a target expands to and every entity of a device trigger's device needs Read,
proved before anything runs and again before a result is returned. A definition whose reach cannot
be proved is refused whole, and a hidden and a missing reference are refused alike.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import Context, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import condition as ha_condition
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import target as ha_target
from homeassistant.helpers import trace as ha_trace
from homeassistant.helpers import trigger as ha_trigger
from homeassistant.helpers.template import Template
from homeassistant.loader import async_get_integration

from ..access_key_store import AccessKeyRecord
from ..authoring_validation import async_template_refusal
from ..capability import effective_cap
from ..const import (
    AUTOMATION_TEST_CONDITIONS, AUTOMATION_TEST_MAX_TRIGGERS, AUTOMATION_TEST_TRIGGERS,
    AUTOMATION_TEST_UNSCOPED_KEYS, CAP_ALLOW, CAP_DENY, DOMAIN, HELPER_TEMPLATE_PURE_FUNCTIONS,
    HELPER_TEMPLATE_STATE_FUNCTIONS, MAX_SUBSCRIPTION_SECONDS,
)
from ..policy_engine import (
    _ENTITY_ID_RE, Permission, canonical_entity_id, filter_service_response, is_sensitive_key,
    resolve_read, scrub_sensitive_attributes,
)
from ..redaction import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text
from ..request_authority import check_request_authority
from ..template_render import (
    TemplateRenderError, TemplateWorkerBusyError, async_evaluate_templates_for_access_key,
)
from ..template_scope import HelperScopeError, caller_template_strings, is_template, template_entities
from ..tool_common import _tool_error, _tool_success

if TYPE_CHECKING:
    from ..data import PhoenixData

_LOGGER = logging.getLogger(__name__)

_KIND_KEYS = frozenset({"platform", "trigger", "condition"})
_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_RESERVED_VARIABLES = HELPER_TEMPLATE_STATE_FUNCTIONS | HELPER_TEMPLATE_PURE_FUNCTIONS | {"states", "loop"}
_NOT_FOUND = (
    "Not found: this definition names an entity that does not exist or that this access key "
    "cannot read. Name only entities the key can read; nothing was registered."
)
_UNSCOPED = (
    "Device, area, floor and label references are supported only in a target block, or as the "
    "device of a device trigger or condition. Name entities with entity_id elsewhere."
)
_MAX_RESULT_DEPTH = 12
_TRIGGER_TEMPLATES = (
    "Templates cannot be tested with test_trigger by this access key: it may not write automations "
    "without review, and Home Assistant would run them on every state change. Check a template's current value "
    "with test_condition, as a template condition."
)
# Caller variable names start with a letter, so these never collide with one.
_RESULT_VARIABLE = "_phoenix_template_{}"
_SELECTOR_FIELDS = ("device_id", "area_id", "floor_id", "label_id")


class _Refused(Exception):
    """A definition this key may not test; the message is safe to return."""


class _Scope:
    """What a definition reads: entity IDs, targets and devices to expand, and compared attributes."""

    def __init__(self) -> None:
        self.entities: set[str] = set()
        self.attributes: set[tuple[str, str]] = set()
        # Each target with the trigger or condition kind that reads it.
        self.targets: list[tuple[str, dict[str, Any]]] = []
        self.devices: set[str] = set()
        # Whether each kind's class expands targets to primary entities only, read from Core.
        self.primary_only: dict[str, bool] = {}


def _ids(value: Any) -> list[str] | None:
    """A string or list of strings as a list, else None."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


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
        if is_template(value):
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
    device_kind = any(value.get(kind) == "device" for kind in _KIND_KEYS)
    for key, item in value.items():
        if key == "target" and isinstance(item, dict):
            # Expanded the way Core expands it; an exclude block only narrows, so it is ignored.
            selection = {field: _ids(ids) for field, ids in item.items() if field != "exclude" and ids}
            if any(ids is None for ids in selection.values()):
                raise _Refused("A target lists IDs as a string or a list of strings.")
            kind = next((value[k] for k in _KIND_KEYS if isinstance(value.get(k), str)), "")
            scope.targets.append((kind, selection))
            continue
        if key == "device_id" and device_kind and (devices := _ids(item)) is not None:
            scope.devices.update(devices)
            continue
        if key in AUTOMATION_TEST_UNSCOPED_KEYS and item:
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


def _reached(scope: _Scope, hass: HomeAssistant) -> set[str] | None:
    """Every entity the targets and devices reach now, or None when one names nothing that exists.

    Targets use Core's own expansion with groups expanded, and with configuration and diagnostic
    entities left out only where the kind's own class leaves them out. Each device, area, floor
    and label selector must reach an entity, as in describe_area, so an empty one looks missing.
    A device trigger's device must have an entity.
    """
    reached: set[str] = set()
    for kind, target in scope.targets:
        primary = scope.primary_only.get(kind, False)
        selected = ha_target.async_extract_referenced_entity_ids(
            hass, ha_target.TargetSelection(target), True, primary_entities_only=primary)
        if (selected.missing_devices or selected.missing_areas or selected.missing_floors
                or selected.missing_labels):
            return None
        for field in _SELECTOR_FIELDS:
            for selector in target.get(field) or []:
                alone = ha_target.async_extract_referenced_entity_ids(
                    hass, ha_target.TargetSelection({field: [selector]}), True, primary_entities_only=primary)
                if not alone.referenced | alone.indirectly_referenced:
                    return None
        reached |= selected.referenced | selected.indirectly_referenced
    registry = er.async_get(hass)
    for device_id in scope.devices:
        entities = er.async_entries_for_device(registry, device_id, include_disabled_entities=True)
        if not entities:
            return None
        reached.update(entry.entity_id for entry in entities)
    return reached


def _scope_error(scope: _Scope, access_key: AccessKeyRecord, hass: HomeAssistant) -> str | None:
    """The refusal when a recorded reference is not readable as written, else None."""
    reached = _reached(scope, hass)
    if reached is None:
        return _NOT_FOUND
    for entity_id in scope.entities | reached | {entity for entity, _ in scope.attributes}:
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


async def _entity_class(hass: HomeAssistant, platform: str, key: str) -> type | None:
    """The class of a new-style trigger or condition key when it is one of Core's entity classes."""
    base: Any
    # Both bases exist at the 2026.7 floor in the flat helper modules; the trigger base moved to
    # a package in 2026.10 and the condition base in 2026.11.
    if platform == "trigger":
        try:
            # Local: the package path exists only from 2026.10.
            from homeassistant.helpers.trigger.entity_trigger import EntityTriggerBase
            base = EntityTriggerBase
        except ImportError:
            base = getattr(ha_trigger, "EntityTriggerBase", None)
        relative = ha_trigger.get_relative_description_key
    else:
        try:
            # Local: the package path exists only from 2026.11.
            from homeassistant.helpers.condition.entity_condition import EntityConditionBase
            base = EntityConditionBase
        except ImportError:
            base = getattr(ha_condition, "EntityConditionBase", None)
        relative = ha_condition.get_relative_description_key
    if not isinstance(base, type):
        return None
    domain = key.split(".", 1)[0]
    try:
        module = await (await async_get_integration(hass, domain)).async_get_platform(platform)
        getter = module.async_get_triggers if platform == "trigger" else module.async_get_conditions
        found = (await getter(hass)).get(relative(domain, key))
    except Exception:  # an integration without that platform is simply not entity-based
        return None
    return found if isinstance(found, type) and issubclass(found, base) else None


async def _require_entity_based(hass: HomeAssistant, platform: str, kinds: list[str], scope: _Scope) -> None:
    """Refuse a new-style kind that is not entity-based; record how each expands its targets."""
    for kind in kinds:
        if "." not in kind:
            continue
        found = await _entity_class(hass, platform, kind)
        if found is not None:
            # Core's class decides whether configuration and diagnostic entities are tracked;
            # without the flag, the widest expansion is assumed.
            primary = getattr(found, "_primary_entities_only", False)
            scope.primary_only[kind] = primary if isinstance(primary, bool) else False
        else:
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
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: wait a bounded time for trigger definitions to fire, and report the first firing.

    Templates need cap_automation_write Allow and pass the template worker check before Home
    Assistant validates them.
    """
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "test_trigger"
    raw = args.get("trigger")
    triggers = raw if isinstance(raw, list) else [raw]
    if not triggers or len(triggers) > AUTOMATION_TEST_MAX_TRIGGERS:
        return _tool_error(
            f"trigger must be one trigger object or a list of 1 to {AUTOMATION_TEST_MAX_TRIGGERS}."
        ), "invalid_request", "test_trigger"
    scope = _Scope()
    templated = bool(caller_template_strings(triggers))
    try:
        if templated and effective_cap(access_key, "cap_automation_write") != CAP_ALLOW:
            raise _Refused(_TRIGGER_TEMPLATES)
        variables = _variables(args)
        kinds = _trigger_kinds(triggers)
        _collect(triggers, hass, scope, frozenset(variables))
        await _require_entity_based(hass, "trigger", kinds, scope)
        if (refusal := _scope_error(scope, access_key, hass)) is not None:
            return _tool_error(refusal), "not_found" if refusal is _NOT_FOUND else "denied", "test_trigger"
    except _Refused as err:
        return _tool_error(str(err)), "invalid_request", "test_trigger"
    if templated and (refusal := await async_template_refusal(triggers, hass, data)):
        return _tool_error(refusal), "invalid_request", "test_trigger"
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


class _FailedTemplate:
    """A template the worker could not render: Home Assistant's render of its reference raises."""

    def __init__(self, message: str) -> None:
        self._message = message

    def __str__(self) -> str:
        raise TemplateError(self._message)


def _with_template_references(raw: Any, hass: HomeAssistant) -> tuple[Any, list[dict], dict[str, Any]]:
    """A copy of a raw condition with each template replaced by a reference to its worker result.

    Returns the copy, the renders to run and, per reference variable, the render index, or for
    a numeric_state value_template one index per entity, rendered with that entity as state.
    """
    renders: list[dict] = []
    references: dict[str, Any] = {}

    def walk(value: Any, holder: dict | None, key: Any) -> Any:
        if is_template(value):
            name = _RESULT_VARIABLE.format(len(references))
            if holder is not None and key == "value_template" and holder.get("condition") == "numeric_state":
                named = holder.get("entity_id")
                per_entity = {}
                for part in named if isinstance(named, list) else [named]:
                    for entity_id in _entity_strings(part, hass) if isinstance(part, str) else []:
                        per_entity[entity_id] = len(renders)
                        renders.append({"template": value, "state": entity_id})
                references[name] = per_entity
                return "{{ " + name + "[state.entity_id] }}"
            references[name] = len(renders)
            renders.append({"template": value, "state": None})
            return "{{ " + name + " }}"
        if isinstance(value, dict):
            return {item_key: walk(item, value, item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, None, None) for item in value]
        return value

    return walk(raw, None, None), renders, references


def _reference_values(references: dict[str, Any], answers: list[dict]) -> dict[str, Any]:
    """The variables a rewritten condition reads: each worker output, or a value whose render raises."""
    def value(index: int) -> Any:
        answer = answers[index]
        return answer["output"] if "output" in answer else _FailedTemplate(answer["failure"])

    return {name: {entity_id: value(i) for entity_id, i in index.items()} if isinstance(index, dict) else value(index)
            for name, index in references.items()}


async def _tool_test_condition(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: evaluate one condition against current states, with optional plain-data variables.

    Templates are rendered in the template worker; Home Assistant evaluates the rest.
    """
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
        await _require_entity_based(hass, "condition", kinds, scope)
        if (refusal := _scope_error(scope, access_key, hass)) is not None:
            return _tool_error(refusal), "not_found" if refusal is _NOT_FOUND else "denied", "test_condition"
    except _Refused as err:
        return _tool_error(str(err)), "invalid_request", "test_condition"
    rewritten, renders, references = _with_template_references(raw, hass)
    answers: list[dict] = []
    if renders:
        try:
            answers = await async_evaluate_templates_for_access_key(
                renders, variables, scope.entities, access_key, hass, data)
        except TemplateWorkerBusyError as err:
            return _tool_error(str(err)), "invalid_request", "test_condition"
        except TemplateRenderError as err:
            return _tool_error(f"The condition's templates could not be evaluated: {err}"), \
                "invalid_request", "test_condition"
    try:
        validated = await ha_condition.async_validate_condition_config(hass, cv.CONDITION_SCHEMA(rewritten))
        checker = await ha_condition.async_from_config(hass, validated)
    except (vol.Invalid, HomeAssistantError) as err:
        return _tool_error(f"Home Assistant refused this condition: {_error_text(err, access_key, hass)}"), \
            "invalid_request", "test_condition"
    check_request_authority()
    condition_trace = ha_trace.trace_get()
    try:
        with ha_trace.suppress_template_error_logging():
            passed = checker.async_check(
                variables={**variables, **_reference_values(references, answers)} or None)
    except HomeAssistantError as err:
        return _tool_error(f"The condition could not be evaluated: {_error_text(err, access_key, hass)}"), \
            "invalid_request", "test_condition"
    finally:
        if unload := getattr(checker, "async_unload", None):
            unload()
    if (refusal := _scope_error(scope, access_key, hass)) is not None:
        return _tool_error(refusal), "not_found" if refusal is _NOT_FOUND else "denied", "test_condition"
    body: dict[str, Any] = {"result": bool(passed)}
    messages = [message for answer in answers for message in answer.get("warnings", [])]
    messages += [
        message
        for elements in (condition_trace or {}).values()
        for element in elements
        for message in getattr(element, "template_errors", None) or []
    ]
    errors = [_error_text(Exception(message), access_key, hass) for message in messages]
    if errors:
        body["template_errors"] = errors[:10]
    return _tool_success(json.dumps(body, default=str)), "allowed", "test_condition"
