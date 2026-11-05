"""Authorization boundary for service calls: which targets a service may take, selectors and templates in its data."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from inspect import unwrap
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, service as ha_service
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.template import is_template_string

from .const import (
    CROSS_DOMAIN_SERVICES,
    MUSIC_ASSISTANT_LIBRARY_SERVICES,
    SECONDARY_TARGET_SELECTOR_KEYS,
    TARGET_SELECTOR_KEYS,
)

_TARGET_SELECTOR_TYPES = frozenset({"area", "device", "entity", "target", "config_entry"})


def dedicated_tool_refusal(domain: str, service: str) -> tuple[str, str] | None:
    """Return (capability, message) for a service only a dedicated tool may run.

    The dedicated tool applies authority call_service cannot: calendar writes
    bind to the event, and a config entry reload needs the entry's own scope.
    """
    if domain == "calendar" and service != "get_events":
        return "cap_calendar_write", "Use the dedicated calendar event authoring tools for calendar writes."
    if domain == "homeassistant" and service == "reload_config_entry":
        return "cap_integration_write", "Use reload_integration to reload an integration."
    return None


def _as_filters(value: object) -> list[Mapping]:
    """A selector filter, or a list of them, as a list of mappings."""
    return [item for item in (value if isinstance(value, list) else [value]) if isinstance(item, Mapping)]


def _declared_target_filters(description: object) -> list[Mapping] | None:
    """Entity filters a service description declares, [] for any entity, None for no target.

    A target comes from the description's `target`, or from an `entity_id` field,
    the older way to declare one. Only the domain and integration of each filter
    decide binding; HA itself applies device classes and supported features.
    """
    if not isinstance(description, Mapping):
        return None
    if "target" in description:
        target = description["target"]
        if not isinstance(target, Mapping):
            return []
        return _as_filters(target["entity"] if target.get("entity") is not None else target.get("device"))
    fields = description.get("fields")
    field = fields.get("entity_id") if isinstance(fields, Mapping) else None
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
    """Whether a registered schema declares entity_id as a field of its own.

    Extra keys a schema merely tolerates do not count. Reads the attributes
    voluptuous and probatio share through a few levels of wrapping, and anything
    else answers False, which refuses.
    """
    fields = getattr(schema, "schema", None)
    if isinstance(fields, Mapping):
        return any(str(key) == "entity_id" for key in fields)
    if depth == 0:
        return False
    nested = getattr(schema, "validators", ())
    return (fields is not None and _schema_names_entity_id(fields, depth - 1)) or (
        isinstance(nested, (list, tuple)) and any(_schema_names_entity_id(item, depth - 1) for item in nested)
    )


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

    WRITE on an entity authorizes acting on that entity, and MESA governs that
    entity, so both mean something only when the service acts on it. A service
    with no declared target ignores whatever entity is named, which would turn any
    write grant into a decoy for the service, so it takes no target. A registered
    schema naming entity_id declares one, like the field in a description. Three
    cases keep the own-domain rule: a script's named service, which
    service_target_binding_valid ties to its exact entity; a name no one registered,
    which Home Assistant refuses with a usable hint; and unreadable descriptions,
    where a declared target cannot be told from none.
    """
    if f"{domain}/{service}" in CROSS_DOMAIN_SERVICES:
        return ()
    readable = True
    try:
        descriptions: Any = await async_get_all_descriptions(hass)
    except Exception:  # the own-domain rule is the safe degradation
        descriptions = {}
        readable = False
    services = descriptions.get(domain) if isinstance(descriptions, Mapping) else None
    filters = _declared_target_filters(services.get(service) if isinstance(services, Mapping) else None)
    registered = hass.services.async_services().get(domain, {}).get(service)
    if filters is None and registered is not None and (
        _is_entity_service(hass, domain, service) or _schema_names_entity_id(registered.schema)
    ):
        filters = []
    own_domain = domain == "script" or registered is None or not readable
    return tuple(
        entity_id for entity_id in targets
        if not (
            own_domain and entity_id.split(".", 1)[0] == domain if filters is None
            else _fits_filters(hass, entity_id, filters)
        )
    )


def unbound_target_error(domain: str, service: str, targets: tuple[str, ...]) -> str:
    """Explain a refusal using only targets the caller may already write."""
    named = ", ".join(targets[:3]) + (f" and {len(targets) - 3} more" if len(targets) > 3 else "")
    return (
        f"{domain}.{service} does not act on {named}. A service can only target the entities it declares, "
        "and a service that declares no target is unavailable through call_service. No action was dispatched."
    )


def _contains_target_selector(value: object) -> bool:
    """Whether a selector document can name HA registry targets."""
    if isinstance(value, Mapping):
        if _TARGET_SELECTOR_TYPES.intersection(str(key) for key in value):
            return True
        return any(_contains_target_selector(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_target_selector(item) for item in value)
    return False


def secondary_target_fields(descriptions: object) -> frozenset[str]:
    """Collect non-primary target field names from HA service descriptions.

    Scan every registered service to retain a conservative fallback when the
    requested description is missing or late-loaded. The caller may disambiguate
    a discovered name only with explicit text metadata on the requested service.
    """
    found = set(SECONDARY_TARGET_SELECTOR_KEYS)
    if not isinstance(descriptions, Mapping):
        return frozenset(found)
    for services in descriptions.values():
        if not isinstance(services, Mapping):
            continue
        for description in services.values():
            if not isinstance(description, Mapping):
                continue
            fields = description.get("fields")
            if not isinstance(fields, Mapping):
                continue
            for name, field in fields.items():
                if (
                    isinstance(field, Mapping)
                    and _contains_target_selector(field.get("selector"))
                    and str(name) not in TARGET_SELECTOR_KEYS
                ):
                    found.add(str(name))
    return frozenset(found)


async def unsupported_secondary_targets(
    hass: HomeAssistant, service_data: object, *, domain: str, service: str
) -> tuple[str, ...]:
    """Return caller fields that could select targets Phoenix did not resolve.

    HA caches the description load and refreshes it when registered services
    change. If that compatibility seam fails, the static fallback still blocks
    every target field known at build time rather than disabling the guard.
    """
    if f"{domain}/{service}" in MUSIC_ASSISTANT_LIBRARY_SERVICES and isinstance(service_data, dict):
        # Its config entry is authorized by the Music Assistant library check.
        service_data = {key: value for key, value in service_data.items() if key != "config_entry_id"}
    if not isinstance(service_data, dict) or not service_data:
        return ()
    primary =tuple(sorted(str(key) for key in service_data if key in TARGET_SELECTOR_KEYS))
    # A target mapping is a selector block outside the call's own target, which
    # selects nothing at this boundary. A scalar target stays a data field.
    if isinstance(service_data.get("target"), Mapping):
        primary = tuple(sorted((*primary, "target")))
    if primary:
        return primary
    try:
        descriptions: Any = await async_get_all_descriptions(hass)
    except Exception:  # the static fallback is the safe degradation
        descriptions = {}
    unsafe = set(secondary_target_fields(descriptions))
    # Only an explicit text selector on this service can disambiguate a name
    # discovered on another service. Keep build-time refusals and missing or
    # malformed metadata conservative, and never admit structured text values.
    services = descriptions.get(domain) if isinstance(descriptions, Mapping) else None
    description = services.get(service) if isinstance(services, Mapping) else None
    fields = description.get("fields") if isinstance(description, Mapping) else None
    if isinstance(fields, Mapping):
        for name, field in fields.items():
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

    A service may render a field as a template (camera.snapshot's filename, a
    custom integration's own fields) over every entity's state, outside this
    access key's scope, so the rendered value or the call's success can disclose
    what the key cannot read. Service descriptions do not say which fields are
    rendered, so template syntax is refused in every field, keys included.
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
