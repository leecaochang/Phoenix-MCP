"""Authorization boundary for service calls: which targets a service may take, selectors and templates in its data."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import partial
from inspect import unwrap
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import config_validation as cv, entity_registry as er, service as ha_service
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.template import is_template_string

from .access_key_store import AccessKeyRecord
from .const import (
    CROSS_DOMAIN_SERVICES,
    DEVICE_ACTUATOR_DOMAINS,
    DOMAIN,
    MUSIC_ASSISTANT_LIBRARY_SERVICES,
    NO_TARGET_SERVICES,
    PHYSICAL_COMMAND_SERVICES,
    REACH_WIDENING_FLAGS,
    SCRIPT_ENTITY_SERVICES,
    SCRIPT_INPUT_FIELD,
    SCRIPT_INPUT_SERVICES,
    SECONDARY_TARGET_SELECTOR_KEYS,
    TARGET_SELECTOR_KEYS,
    TTS_SPEAK_ENGINE_DOMAIN,
    TTS_SPEAK_PLAYER_DOMAIN,
    TTS_SPEAK_PLAYER_FIELD,
    TTS_SPEAK_SERVICE,
    WHOLE_DEVICE_SERVICES,
)
from .policy_engine import Permission, resolve, resolve_service_targets, whole_device_entities

_LOGGER = logging.getLogger(__name__)

_TARGET_SELECTOR_TYPES = frozenset({"area", "device", "entity", "floor", "label", "target", "config_entry"})
# Also ordinary keys: a select option has a string label, and an object selector may have
# a field of that name, which carries its own selector.
_AMBIGUOUS_SELECTOR_TYPES = frozenset({"floor", "label"})
_DESCRIPTIONS_FAILED = f"{DOMAIN}_service_descriptions_failed"
# A variable of these names selects a registry target, which only resolution can judge.
_INPUT_SELECTOR_KEYS = TARGET_SELECTOR_KEYS - {"entity_id", "exclude"}
_REGISTRY_ID_CHARS = 32
_DEVICE_REACH_ADVISORY = "An entity of this device has an advisory policy restriction."


async def _service_descriptions(hass: HomeAssistant) -> Mapping[str, Any] | None:
    """Return HA's service descriptions, or None when they cannot be read, logged once."""
    try:
        descriptions: Any = await async_get_all_descriptions(hass)
    except Exception as err:  # each caller has a safe degradation
        if not hass.data.get(_DESCRIPTIONS_FAILED):
            hass.data[_DESCRIPTIONS_FAILED] = True
            _LOGGER.warning(
                "Home Assistant's service descriptions could not be read (%s); call_service falls "
                "back to own-domain targets and the built-in list of target fields.",
                type(err).__name__,
            )
        return None
    return descriptions if isinstance(descriptions, Mapping) else None


def dedicated_tool_refusal(domain: str, service: str) -> tuple[str | None, str] | None:
    """Return (capability, message) for a service only a dedicated tool may run.

    The dedicated tool applies authority call_service cannot: calendar writes
    bind to the event, and a config entry reload needs the entry's own scope.
    """
    if domain == "calendar" and service != "get_events":
        return "cap_calendar_write", "Use the dedicated calendar event authoring tools for calendar writes."
    if domain == "homeassistant" and service == "reload_config_entry":
        return "cap_integration_write", "Use reload_integration to reload an integration."
    if domain == "todo" and service == "remove_completed_items":
        return None, "Use set_todo_item with action clear_completed, which removes the items it read."
    return None


TODO_REMOVAL_WARNING = "Removing to-do items is permanent. Phoenix keeps no copy, so they cannot be restored."


def todo_removal_review(
    hass: HomeAssistant, domain: str, service: str, service_data: Mapping[str, Any], entity_ids: list[str],
) -> dict[str, Any]:
    """Return the preview fields of a todo.remove_item call: its warning, and the items it names."""
    if domain != "todo" or service != "remove_item":
        return {}
    items = todo_items_in_review(hass, domain, service, service_data, entity_ids)
    return {"warning": TODO_REMOVAL_WARNING, **({"todo_items": items} if items else {})}


def todo_items_in_review(
    hass: HomeAssistant, domain: str, service: str, service_data: Mapping[str, Any], entity_ids: list[str],
) -> list[dict[str, Any]]:
    """Return the items a todo.remove_item call names, as an approver should read them.

    Read synchronously here, for the diff, only for already-resolved targets; best effort.
    """
    if domain != "todo" or service != "remove_item":
        return []
    # Local: optional component, imported only when this path runs.
    from homeassistant.components.todo.const import DATA_COMPONENT

    named = service_data.get("item")
    wanted = {named} if isinstance(named, str) else set(named) if isinstance(named, list) else set()
    component = hass.data.get(DATA_COMPONENT)
    if component is None or not wanted:
        return []
    return [
        {"list": entity_id, "uid": item.uid, "summary": item.summary,
         "status": getattr(item.status, "value", item.status)}
        for entity_id in entity_ids
        if (entity := component.get_entity(entity_id)) is not None
        for item in getattr(entity, "todo_items", None) or ()
        if item.uid in wanted or item.summary in wanted
    ]


def _flat_fields(description: object) -> Mapping[str, Any]:
    """A description's fields with sections opened, as HA flattens them into service data."""
    fields = description.get("fields") if isinstance(description, Mapping) else None
    if not isinstance(fields, Mapping):
        return {}
    flat: dict[str, Any] = {}
    for name, field in fields.items():
        nested = field.get("fields") if isinstance(field, Mapping) else None
        if isinstance(nested, Mapping):
            flat.update(nested)
        else:
            flat[name] = field
    return flat


def _as_filters(value: object) -> list[Mapping]:
    """A selector filter, or a list of them, as a list of mappings."""
    return [item for item in (value if isinstance(value, list) else [value]) if isinstance(item, Mapping)]


def _declared_target_filters(description: object) -> list[Mapping] | None:
    """Return a description's entity filters: [] for any entity, None for no target.

    Read from ``target`` or an ``entity_id`` field. Only domain and integration decide
    binding.
    """
    if not isinstance(description, Mapping):
        return None
    if "target" in description:
        target = description["target"]
        if not isinstance(target, Mapping):
            return []
        return _as_filters(target["entity"] if target.get("entity") is not None else target.get("device"))
    field = _flat_fields(description).get("entity_id")
    if field is None:
        return None
    selector = field.get("selector") if isinstance(field, Mapping) else None
    entity = selector.get("entity") if isinstance(selector, Mapping) else None
    if not isinstance(entity, Mapping):
        return []
    return _as_filters(entity["filter"] if "filter" in entity else entity)


def _is_entity_service(hass: HomeAssistant, domain: str, service: str) -> bool:
    """Whether HA's own entity dispatcher runs this service, which only acts on its entities.

    An integration without a services.yaml has no description to read. If HA
    renames the dispatcher this answers False, which refuses instead of admitting.
    """
    registered = hass.services.async_services().get(domain, {}).get(service)
    if registered is None:
        return False
    try:
        handler = unwrap(registered.job.target)
        while isinstance(handler, partial):
            handler = unwrap(handler.func)
    except (AttributeError, ValueError):
        return False
    return handler in (
        getattr(ha_service, "entity_service_call", None),
        getattr(ha_service, "batched_entity_service_call", None),
    )


def _schema_names_entity_id(schema: object, depth: int = 4) -> bool:
    """Return whether a registered schema declares entity_id as a field of its own.

    Tolerated extra keys do not count; an unreadable schema answers False, which refuses.
    """
    fields = schema if isinstance(schema, Mapping) else getattr(schema, "schema", None)
    if isinstance(fields, Mapping):
        return any(str(key) == "entity_id" for key in fields)
    if depth == 0:
        return False
    nested = getattr(schema, "validators", ())
    return (fields is not None and _schema_names_entity_id(fields, depth - 1)) or (
        isinstance(nested, (list, tuple)) and any(_schema_names_entity_id(item, depth - 1) for item in nested)
    )


def entity_id_argument(
    hass: HomeAssistant, domain: str, service: str, targets: list[str], service_data: Mapping[str, Any],
) -> str | list[str]:
    """Return the shape a service's schema takes for its one target.

    A list, or the bare string when the schema refuses the list and takes it. No side effect.
    """
    registered = hass.services.async_services().get(domain, {}).get(service)
    if len(targets) != 1 or registered is None or registered.schema is None:
        return targets
    try:
        registered.schema({**service_data, "entity_id": targets})
        return targets
    except vol.Invalid:
        pass
    try:
        registered.schema({**service_data, "entity_id": targets[0]})
    except vol.Invalid:
        return targets
    return targets[0]


def _fits_filters(hass: HomeAssistant, entity_id: str, filters: list[Mapping]) -> bool:
    """Whether an entity satisfies any filter; no filters admit every entity."""
    if not filters:
        return True
    entity_domain = entity_id.split(".", 1)[0]
    for entity_filter in filters:
        domains = entity_filter.get("domain")
        if domains and entity_domain not in ([domains] if isinstance(domains, str) else domains):
            continue
        integration = entity_filter.get("integration")
        if integration:
            entry = er.async_get(hass).async_get(entity_id)
            if entry is None or entry.platform != integration:
                continue
        return True
    return False


async def unbound_service_targets(
    hass: HomeAssistant, targets: list[str], *, domain: str, service: str
) -> tuple[str, ...]:
    """Return the resolved targets this service does not act on.
    WRITE and MESA mean something only for an entity the service acts on, so a service with no
    declared target takes none. A script's named service, an unregistered name and unreadable
    descriptions keep the own-domain rule. tts.speak's named players are its own to judge.
    """
    if f"{domain}/{service}" in CROSS_DOMAIN_SERVICES:
        return ()
    if f"{domain}/{service}" == TTS_SPEAK_SERVICE:
        targets = speech_engines(targets)
    descriptions = await _service_descriptions(hass)
    named_script = domain == "script" and service not in SCRIPT_ENTITY_SERVICES
    services = descriptions.get(domain) if descriptions is not None else None
    # A script's fields are its inputs: an entity_id among them declares no target.
    filters = None if named_script else _declared_target_filters(
        services.get(service) if isinstance(services, Mapping) else None
    )
    registered = hass.services.async_services().get(domain, {}).get(service)
    if filters is None and not named_script and registered is not None and (
        _is_entity_service(hass, domain, service) or _schema_names_entity_id(registered.schema)
    ):
        filters = []
    own_domain = domain == "script" or registered is None or descriptions is None
    return tuple(
        entity_id for entity_id in targets
        if not (
            own_domain and entity_id.split(".", 1)[0] == domain if filters is None
            else _fits_filters(hass, entity_id, filters)
        )
    )


async def named_script_refusal(hass: HomeAssistant, domain: str, service: str) -> str | None:
    """Refuse a script run by name whose input is called entity_id, explaining the way that works.

    Its own ID fills that field to identify the call, and a caller value cannot be told from a
    target selector, so the script would act on itself.
    """
    if domain != "script" or service in SCRIPT_ENTITY_SERVICES:
        return None
    descriptions = await _service_descriptions(hass)
    services = descriptions.get(domain) if descriptions is not None else None
    description = services.get(service) if isinstance(services, Mapping) else None
    if "entity_id" not in _flat_fields(description):
        return None
    return (
        f"script.{service} takes an input named entity_id, which cannot be filled when it is run "
        "by name. Call script.turn_on with this script as the target and pass the input in "
        "variables; an entity named there needs the same access as a target. No action was "
        "dispatched."
    )


def script_inputs(domain: str, service: str, service_data: object) -> Mapping | None:
    """Return the values a call hands a script or automation, or None for any other call."""
    if not isinstance(service_data, Mapping):
        return None
    service_key = f"{domain}/{service}"
    if service_key in SCRIPT_INPUT_SERVICES:
        variables = service_data.get(SCRIPT_INPUT_FIELD)
        return variables if isinstance(variables, Mapping) else None
    if domain == "script" and service not in SCRIPT_ENTITY_SERVICES and service_key not in NO_TARGET_SERVICES:
        return service_data
    return None


def _input_tokens(value: object) -> Iterator[str]:
    """Yield each string of a nested input as HA reads a target: comma-separated, lowercased."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            yield from (token.strip().lower() for token in item.split(","))
        elif isinstance(item, Mapping):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


def _names_entity(token: str, hass: HomeAssistant) -> bool:
    """Whether a token can name an entity: an ID of a domain in use, or a registry ID.

    Decided without asking if that entity exists, so a hidden one and a missing one read alike.
    A device ID has the registry ID's shape and is refused with them.
    """
    if len(token) == _REGISTRY_ID_CHARS and all(char in "0123456789abcdef" for char in token):
        return True
    if not valid_entity_id(token):
        return False
    domain = token.split(".", 1)[0]
    return domain in hass.data.get(DATA_INSTANCES, {}) or hass.states.async_entity_ids_count(domain) > 0


def script_input_entities(domain: str, service: str, service_data: object, hass: HomeAssistant) -> list[str]:
    """Return the entities a call's script or automation inputs name, in canonical form.

    A script can read any variable, whether or not a field declares it, and act on its value,
    so the value decides, not the field. Non-entity values are left alone.
    """
    inputs = script_inputs(domain, service, service_data)
    if not inputs:
        return []
    registry = er.async_get(hass)
    named: dict[str, None] = {}
    for token in _input_tokens(inputs):
        if _names_entity(token, hass):
            entry = registry.async_get(token)
            named[entry.entity_id if entry is not None else token] = None
    return list(named)


def gate_entity_ids(
    domain: str, service: str, service_data: object, entity_id: str | list[str] | None, hass: HomeAssistant,
) -> str | list[str] | None:
    """Return a call's entity targets with the entities its inputs name, for the capability gates."""
    named = script_input_entities(domain, service, service_data, hass)
    if not named:
        return entity_id
    return [*([entity_id] if isinstance(entity_id, str) else entity_id if isinstance(entity_id, list) else []), *named]


def _scripts_run(domain: str, service: str, entity_id: object) -> list[str]:
    """Return the object IDs of the scripts a call runs, as far as its entity targets say."""
    if domain != "script":
        return []
    if service not in SCRIPT_ENTITY_SERVICES:
        return [service]
    named = [entity_id] if isinstance(entity_id, str) else entity_id if isinstance(entity_id, list) else []
    return [item.split(".", 1)[1] for item in named if isinstance(item, str) and item.startswith("script.")]


async def script_input_selectors(
    hass: HomeAssistant, domain: str, service: str, service_data: object, entity_id: object,
) -> tuple[str, ...]:
    """Return the inputs that select targets no entity ID names: devices, areas, labels, "all".

    Read by key and by the fields of the scripts this call runs. An entity selector stays out,
    as its value is judged like any other. "all" in a field declared with no target selector
    is an ordinary word.
    """
    inputs = script_inputs(domain, service, service_data)
    if not inputs:
        return ()
    scripts = _scripts_run(domain, service, entity_id)
    descriptions = await _service_descriptions(hass) if scripts else None
    services = descriptions.get("script") if descriptions is not None else None
    fields: dict[str, Any] = {}
    for script in scripts:
        fields.update(_flat_fields(services.get(script)) if isinstance(services, Mapping) else {})

    def selects(name: str, value: object) -> bool:
        field = fields.get(name)
        selector = field.get("selector") if isinstance(field, Mapping) else None
        declared_plain = isinstance(selector, Mapping) and not _contains_target_selector(selector)
        entity_only = isinstance(selector, Mapping) and set(selector) == {"entity"}
        if selector is not None and not declared_plain and not entity_only and value not in (None, "", [], {}):
            return True
        if not declared_plain and "all" in _input_tokens(value):
            return True
        pending: list[object] = [{name: value}]
        while pending:
            item = pending.pop()
            if isinstance(item, Mapping):
                if any(key in _INPUT_SELECTOR_KEYS and nested for key, nested in item.items()):
                    return True
                pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
        return False

    return tuple(sorted(str(name) for name, value in inputs.items() if selects(str(name), value)))


def script_input_selector_error(names: tuple[str, ...]) -> str:
    """Explain a refusal using only input names the caller supplied."""
    return (
        f"These inputs select targets that cannot be checked: {', '.join(names)}. Name each "
        "entity by its entity ID instead. No action was dispatched."
    )


@dataclass(frozen=True)
class CallReach:
    """What a call reaches beyond the targets it dispatches to.

    ``entities`` is what WRITE, the approval binding and MESA judge, the targets among them.
    ``every_target`` means no permitted subset: all of it passes, or nothing runs.
    """

    entities: list[str]
    every_target: bool = False
    refusal: str | None = None


def call_reach(
    domain: str, service: str, service_data: object, targets: list[str],
    access_key: AccessKeyRecord, hass: HomeAssistant,
) -> CallReach:
    """Return what a call over resolved targets reaches, and its refusal if a key may not.

    An entity named in a script's inputs is authorized as a target, and the refusal reads alike
    for a hidden one and a missing one. A whole-device service reaches the targets' devices,
    and a raw command needs Write on each thing it can operate there.
    """
    reached = list(targets)
    every_target = False
    refusal = None
    if named := script_input_entities(domain, service, service_data, hass):
        every_target = True
        permitted, _ = resolve_service_targets(
            entity_id=named, service_domain=domain, access_key=access_key, hass=hass,
        )
        if set(named) - set(permitted):
            refusal = (
                f"An input of {domain}.{service} names an entity. Every entity named in an input "
                "needs the same access as a target. No action was dispatched."
            )
        reached = list(dict.fromkeys([*reached, *permitted]))
    service_key = f"{domain}/{service}"
    if service_key in WHOLE_DEVICE_SERVICES:
        every_target = True
        reached = whole_device_entities(reached, hass)
        if service_key in PHYSICAL_COMMAND_SERVICES and any(
            entity_id.split(".", 1)[0] in DEVICE_ACTUATOR_DOMAINS
            and resolve(entity_id, access_key, hass) != Permission.WRITE
            for entity_id in reached
        ):
            refusal = (
                f"{domain}.{service} reaches the whole device, so it needs write access to "
                "everything that device can operate. No action was dispatched."
            )
    return CallReach(reached, every_target, refusal)


def visible_reach(entities: list[str], access_key: AccessKeyRecord, hass: HomeAssistant) -> list[str]:
    """Return the entities of a reach this key may see; a device can hold others."""
    return [
        entity_id for entity_id in entities
        if resolve(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
    ]


def visible_reach_warnings(
    warnings: list[str], reach: CallReach, access_key: AccessKeyRecord, hass: HomeAssistant,
) -> list[str]:
    """Return advisory warnings for the key; with a hidden entity in reach, only its own stay.

    A warning can carry an operator's reason without naming its entity, so a warning that
    does not open with an entity the key may see turns generic.
    """
    shown = visible_reach(reach.entities, access_key, hass)
    if len(shown) == len(reach.entities):
        return warnings
    return list(dict.fromkeys(
        warning if warning.startswith(tuple(f"{entity_id}:" for entity_id in shown)) else _DEVICE_REACH_ADVISORY
        for warning in warnings
    ))


def unbound_target_error(domain: str, service: str, targets: tuple[str, ...]) -> str:
    """Explain a refusal using only targets the caller may already write."""
    named = ", ".join(targets[:3]) + (f" and {len(targets) - 3} more" if len(targets) > 3 else "")
    return (
        f"{domain}.{service} does not act on {named}. A service can only target the entities it declares, "
        "and a service that declares no target is unavailable through call_service. No action was dispatched."
    )


def unregistered_target_error(targets: list[str]) -> str:
    """Explain a refusal using only targets the caller may already write."""
    named = ", ".join(targets[:3]) + (f" and {len(targets) - 3} more" if len(targets) > 3 else "")
    return (
        f"Home Assistant has no registry entry for {named}, usually because of a missing unique ID, "
        "and call_service only targets registered entities. No action was dispatched."
    )


def unreadable_group_error(groups: list[str]) -> str:
    """Explain a refusal using only groups the caller may write."""
    return (
        f"The members of {', '.join(groups[:3])} cannot be read right now, and a group is only "
        "controlled once its members can be checked. No action was dispatched."
    )


def _contains_target_selector(value: object) -> bool:
    """Whether a selector document can name HA registry targets."""
    if isinstance(value, Mapping):
        if any(
            str(key) in _TARGET_SELECTOR_TYPES and (
                str(key) not in _AMBIGUOUS_SELECTOR_TYPES
                or item is None or (isinstance(item, Mapping) and "selector" not in item)
            )
            for key, item in value.items()
        ):
            return True
        return any(_contains_target_selector(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_target_selector(item) for item in value)
    return False


def secondary_target_fields(descriptions: object) -> frozenset[str]:
    """Collect non-primary target field names from HA service descriptions.

    Scans every service, as a conservative fallback for a missing or late description.
    """
    found = set(SECONDARY_TARGET_SELECTOR_KEYS)
    if not isinstance(descriptions, Mapping):
        return frozenset(found)
    for services in descriptions.values():
        if not isinstance(services, Mapping):
            continue
        for description in services.values():
            for name, field in _flat_fields(description).items():
                if (
                    isinstance(field, Mapping)
                    and _contains_target_selector(field.get("selector"))
                    and str(name) not in TARGET_SELECTOR_KEYS
                ):
                    found.add(str(name))
    return frozenset(found)


def _reach_widening_flags(domain: str, service: str, service_data: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the data flags set here that send a call past its targets, read as HA reads them."""

    def is_set(value: object) -> bool:
        try:
            return cv.boolean(value)
        except vol.Invalid:
            return True

    return tuple(
        flag for flag in REACH_WIDENING_FLAGS.get(f"{domain}/{service}", ())
        if flag in service_data and is_set(service_data[flag])
    )


async def unsupported_secondary_targets(
    hass: HomeAssistant, service_data: object, *, domain: str, service: str
) -> tuple[str, ...]:
    """Return caller fields that could select targets Phoenix did not resolve.

    If HA's cached description load fails, the static fallback still blocks known target
    fields.
    """
    if f"{domain}/{service}" in MUSIC_ASSISTANT_LIBRARY_SERVICES and isinstance(service_data, dict):
        # Its config entry is authorized by the Music Assistant library check.
        service_data = {key: value for key, value in service_data.items() if key != "config_entry_id"}
    if not isinstance(service_data, dict) or not service_data:
        return ()
    primary =tuple(sorted(str(key) for key in service_data if key in TARGET_SELECTOR_KEYS))
    widened = _reach_widening_flags(domain, service, service_data)
    # A target mapping is a selector block outside the call's own target, which
    # selects nothing at this boundary. A scalar target stays a data field.
    if isinstance(service_data.get("target"), Mapping):
        primary = tuple(sorted((*primary, "target")))
    if primary or widened:
        return tuple(sorted((*primary, *widened)))
    descriptions = await _service_descriptions(hass)
    unsafe = set(secondary_target_fields(descriptions))
    # Only an explicit text selector on this service can disambiguate a name
    # discovered on another service. Keep build-time refusals and missing or
    # malformed metadata conservative, and never admit structured text values.
    services = descriptions.get(domain) if descriptions is not None else None
    description = services.get(service) if isinstance(services, Mapping) else None
    for name, field in _flat_fields(description).items():
        selector = field.get("selector") if isinstance(field, Mapping) else None
        if (
            name not in SECONDARY_TARGET_SELECTOR_KEYS
            and isinstance(service_data.get(name), str)
            and isinstance(selector, Mapping)
            and set(selector) == {"text"}
            and (selector["text"] is None or isinstance(selector["text"], Mapping))
            and not _contains_target_selector(selector)
        ):
            unsafe.discard(name)
    return tuple(sorted(str(key) for key in service_data if str(key) in unsafe))


async def required_secondary_targets(hass: HomeAssistant, domain: str, service: str) -> tuple[str, ...]:
    """Return the required fields of a service that the secondary-target guard always refuses.

    Such a service cannot run through call_service. tts.speak's field has its own adapter.
    """
    descriptions = await _service_descriptions(hass)
    services = descriptions.get(domain) if descriptions is not None else None
    description = services.get(service) if isinstance(services, Mapping) else None
    required = {
        str(name): "" for name, field in _flat_fields(description).items()
        if isinstance(field, Mapping) and field.get("required") and name not in TARGET_SELECTOR_KEYS
    }
    if f"{domain}/{service}" == TTS_SPEAK_SERVICE:
        required.pop(TTS_SPEAK_PLAYER_FIELD, None)
    return await unsupported_secondary_targets(hass, required, domain=domain, service=service) if required else ()


def speech_engines(targets: list[str]) -> list[str]:
    """Return a tts.speak target list without its players: what HA's entity target takes."""
    return [entity_id for entity_id in targets if entity_id.split(".", 1)[0] != TTS_SPEAK_PLAYER_DOMAIN]


def speech_players(targets: list[str]) -> list[str]:
    """Return the players in a tts.speak target list: what its data field takes."""
    return [entity_id for entity_id in targets if entity_id.split(".", 1)[0] == TTS_SPEAK_PLAYER_DOMAIN]


def merge_speech_players(args: dict, domain: str, service: str) -> tuple[dict, str | None]:
    """Move tts.speak's named players into the call's targets, where every gate judges them.

    Returns the arguments and an error for a value that does not name players one by one;
    "all" and registry IDs have no reviewed meaning in this field. Any other call is unchanged.
    """
    service_data = args.get("service_data")
    if (f"{domain}/{service}" != TTS_SPEAK_SERVICE or not isinstance(service_data, dict)
            or TTS_SPEAK_PLAYER_FIELD not in service_data):
        return args, None
    players = service_data[TTS_SPEAK_PLAYER_FIELD]
    players = [players] if isinstance(players, str) else players
    if not isinstance(players, list) or not players or not all(
        isinstance(player, str) and valid_entity_id(player) and speech_players([player]) for player in players
    ):
        return args, f"{TTS_SPEAK_PLAYER_FIELD} must list media player entity IDs. No action was dispatched."
    primary = args.get("entity_id")
    primary = [primary] if isinstance(primary, str) else primary if isinstance(primary, list) else []
    return {
        **args,
        "entity_id": [*primary, *players],
        "service_data": {key: value for key, value in service_data.items() if key != TTS_SPEAK_PLAYER_FIELD},
    }, None


def speech_target_refusal(domain: str, service: str, entity_id: object, targets: list[str]) -> str | None:
    """Refuse tts.speak unless an engine and every named player survived resolution.

    Reads the same for a hidden player and a missing one. A smaller audience is not the
    announcement that was asked for, so this call never runs on a permitted subset.
    """
    if f"{domain}/{service}" != TTS_SPEAK_SERVICE:
        return None
    named = speech_players([item for item in entity_id if isinstance(item, str)]) if isinstance(entity_id, list) else []
    if named and set(named) <= set(targets) and any(
        target.split(".", 1)[0] == TTS_SPEAK_ENGINE_DOMAIN for target in targets
    ):
        return None
    return (
        "tts.speak needs a tts entity target and write access to every media player in "
        f"{TTS_SPEAK_PLAYER_FIELD}. No action was dispatched."
    )


def secondary_target_error(fields: tuple[str, ...]) -> str:
    """Explain a refusal using only field names the caller supplied."""
    joined = ", ".join(fields)
    if TARGET_SELECTOR_KEYS.intersection(fields):
        return (
            f"Target selectors are not supported in service data: {joined}. "
            "Put entity_id, device_id, or area_id in targets; floor_id and label_id "
            "are not supported. No action was dispatched."
        )
    return (
        f"Unsupported secondary target selector in service_data: {joined}. "
        "Phoenix MCP can authorize only the primary entity_id, device_id, or area_id "
        "target for a generic service call."
    )


def template_fields(service_data: object) -> tuple[str, ...]:
    """Return caller fields whose value carries Home Assistant template syntax.

    A service may render any field over every entity's state, outside the key's scope, and
    descriptions do not say which, so the syntax is refused everywhere, keys included.
    """
    if not isinstance(service_data, Mapping):
        return ()

    def _holds_template(value: object) -> bool:
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                if is_template_string(item):
                    return True
            elif isinstance(item, Mapping):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
        return False

    return tuple(sorted(str(key) for key, value in service_data.items() if _holds_template((key, value))))


def template_fields_error(fields: tuple[str, ...]) -> str:
    """Explain a template refusal using only field names the caller supplied."""
    return (
        f"Template syntax is not supported in service_data: {', '.join(fields)}. "
        "Home Assistant would render it with access to every entity, outside this "
        "access key's scope. Read the values you need first and send them as plain "
        "text. No action was dispatched."
    )
