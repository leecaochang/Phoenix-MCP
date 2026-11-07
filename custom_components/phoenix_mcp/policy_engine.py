"""Permission resolution engine for Phoenix MCP. No I/O."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta
from enum import Enum
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State, SupportsResponse, valid_entity_id
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.util.dt import utcnow
from homeassistant.config_entries import ConfigEntryState

from .const import (
    BLOCKED_DOMAINS, DOMAIN, DUAL_GATE_SERVICES, MUSIC_ASSISTANT_DOMAIN,
    MUSIC_ASSISTANT_LIBRARY_SERVICES, NON_ACTUATING_SERVICES, NO_TARGET_SERVICES,
    PHYSICAL_COMMAND_SERVICES, PHYSICAL_GATE_DOMAINS, PRIMARY_ENTITY_DOMAINS, SCRIPT_ENTITY_SERVICES,
    SENSITIVE_ATTRIBUTES, APPROVAL_TARGET_DEPTH, APPROVAL_TARGET_LIMIT, SENSITIVE_KEY_FINAL_WORDS,
    SENSITIVE_KEY_SUBSTRINGS, SENSITIVE_KEY_SUFFIXES, PERSONA_HOME_COMPANION,
    HOME_COMPANION_SENSOR_CLASSES, HOME_COMPANION_CONTROL_DOMAINS, HOME_COMPANION_READ_DOMAINS,
    UPDATE_INSTALL_SERVICE,
)
from .esphome_constants import ESPHOME_DOMAIN
from .device_semantics import (
    DeviceRegistrySnapshot,
    device_authority_chain,
    device_registry_snapshot,
    device_registry_snapshot_from_registry,
    effective_entity_area_id,
)
from .access_key_store import AccessKeyRecord
from .capability import service_capabilities, strictest_capability
from .household_authority import household_admin_access
from .request_authority import household_authority

_LOGGER = logging.getLogger(__name__)
SWITCH_AS_X_PLATFORM = "switch_as_x"


class Permission(str, Enum):
    WRITE = "write"
    READ = "read"
    DENY = "deny"
    NO_ACCESS = "no_access"
    NOT_FOUND = "not_found"


class ConfigEntryRegistryContext(NamedTuple):
    """Exact registry membership owned by one Home Assistant config entry."""

    entry: Any
    entity_ids: tuple[str, ...]
    device_ids: tuple[str, ...]


def device_registry_entries(registry: Any) -> Any:
    """Iterate device entries across HA's mapping and registry-view APIs."""
    return device_registry_snapshot_from_registry(registry).entries


_RELATIVE_TIME_RE = re.compile(r"^([+-]?)\s*(\d+)\s*([a-z]+)(\s+ago)?$")
_RELATIVE_UNITS = {
    **dict.fromkeys(("min", "mins", "minute", "minutes"), "minutes"),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), "hours"),
    **dict.fromkeys(("d", "day", "days"), "days"),
    **dict.fromkeys(("w", "wk", "wks", "week", "weeks"), "weeks"),
    **dict.fromkeys(("mo", "mon", "month", "months"), "months"),
}
_RELATIVE_MAX_DAYS = 366
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def canonical_entity_id(entity_id: str, hass: HomeAssistant) -> str:
    """Resolve an entity_id or registry id to the id resolve() grants on."""
    entity_id = entity_id.lower()
    entry = er.async_get(hass).async_get(entity_id)
    return entry.entity_id if entry else entity_id


def resolve(entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> Permission:
    """Intersect Phoenix entity grants with the admitted household user's grants."""
    permission = _resolve_access_key_access(entity_id, access_key, hass)
    if permission not in (Permission.READ, Permission.WRITE):
        return permission

    household = household_authority.get()
    if household is None:
        return permission
    can_read, can_control = household.entity_access(canonical_entity_id(entity_id, hass))
    if not can_read:
        return Permission.NO_ACCESS
    if permission == Permission.WRITE and not can_control:
        return Permission.READ
    return permission


def _resolve_access_key_access(entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> Permission:
    """Return WRITE, READ, DENY, NO_ACCESS or NOT_FOUND (a ghost) for entity_id and the key.
    Pass-through gets WRITE after the blocklist; Home Companion stays within its domains and
    sensor classes. The chain is entity, device, main parent, domain; bad ancestry is
    NO_ACCESS.
    """
    # A non-string cannot name an entity, and a model does not always follow the
    # tool schema (for example a list where a string was declared). Treated as a
    # ghost so the answer is byte-identical to any other absent entity and fails
    # closed, instead of raising out of the single choke point every permission
    # decision runs through.
    if not isinstance(entity_id, str):
        return Permission.NOT_FOUND

    entity_id = entity_id.lower()
    registry = er.async_get(hass)

    # Resolve to canonical entity_id via entity registry
    entry = registry.async_get(entity_id)
    if entry:
        entity_id = entry.entity_id

    domain = entity_id.split(".")[0]

    # Ghost check (before pass-through short-circuit so ghosts are never accessible).
    # entry is already the registry lookup result; no second call needed.
    if hass.states.get(entity_id) is None and entry is None:
        return Permission.NOT_FOUND

    # Phoenix MCP blocklist - applies even in pass-through mode
    if domain in BLOCKED_DOMAINS:
        return Permission.NO_ACCESS

    # Block Phoenix MCP's own internal sensor entities regardless of pass_through or permission grants.
    # These live under the sensor domain so BLOCKED_DOMAINS does not catch them.
    if entry is not None and entry.platform == DOMAIN:
        return Permission.NO_ACCESS

    companion = access_key.persona == PERSONA_HOME_COMPANION
    if companion:
        state = hass.states.get(entity_id)
        # Restrict device types without changing the shared permission tree.
        if (access_key.pass_through or state is None
                or domain not in HOME_COMPANION_CONTROL_DOMAINS | HOME_COMPANION_READ_DOMAINS
                or (domain == "sensor" and state.attributes.get("device_class") not in HOME_COMPANION_SENSOR_CLASSES)):
            return Permission.NO_ACCESS

    # Pass-through bypasses the permission tree. With the Assist filter on, an
    # unexposed entity is out of reach for every read and every action, as it is
    # for HA's own Assist. The ID is canonical and not a ghost here, so HA's
    # exposure lookup never records a stray one.
    if access_key.pass_through:
        if access_key.use_assist_exposure and not _assist_exposed(hass, entity_id):
            return Permission.NO_ACCESS
        return Permission.WRITE

    permissions = access_key.permissions
    device_chain = device_authority_chain(hass, entry.device_id if entry else None)
    if device_chain is None:
        return Permission.NO_ACCESS

    nodes = (
        permissions.entities.get(entity_id),
        *(permissions.devices.get(device_id) for device_id in device_chain),
        permissions.domains.get(domain),
    )

    # Pass 1: RED check - walk entire ancestor chain before resolving any grant
    for node in nodes:
        if node is not None and node.state == "RED":
            return Permission.DENY

    # Pass 2: most specific non-GREY grant
    for node in nodes:
        if node is None:
            continue
        if node.state == "GREEN":
            return Permission.READ if companion and domain in HOME_COMPANION_READ_DOMAINS else Permission.WRITE
        if node.state == "YELLOW":
            return Permission.READ

    return Permission.NO_ACCESS


def resolve_registry_access(
    entity_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    force_registry_only: bool = False,
) -> Permission:
    """Resolve access to an entity, requiring inherited scope when registry-only.
    A disabled or stateless entry is manageable only through its device or domain; an entity
    grant may restrict that but not create it. Live entities and pass-through keys follow
    resolve(). Assist exposure stays the caller's filter.
    """
    ordinary = resolve(entity_id, access_key, hass)
    if ordinary not in (Permission.READ, Permission.WRITE):
        return ordinary

    entity_id = entity_id.lower()
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    canonical_id = entry.entity_id if entry is not None else entity_id
    if entry is None or (not force_registry_only and (
        entry.disabled_by is None and hass.states.get(canonical_id) is not None
    )):
        return ordinary
    if access_key.pass_through:
        return ordinary

    permissions = access_key.permissions
    domain = canonical_id.split(".", 1)[0]
    # resolve() already refused a malformed chain and any RED ancestor.
    device_chain = device_authority_chain(hass, entry.device_id) or ()

    inherited = Permission.NO_ACCESS
    for node in (
        *(permissions.devices.get(device_id) for device_id in device_chain),
        permissions.domains.get(domain),
    ):
        if node is None:
            continue
        if node.state == "GREEN":
            inherited = Permission.WRITE
            break
        if node.state == "YELLOW":
            inherited = Permission.READ
            break

    if inherited == Permission.NO_ACCESS:
        return Permission.NO_ACCESS
    if ordinary == Permission.WRITE and inherited == Permission.WRITE:
        return Permission.WRITE
    return Permission.READ


def _single_owner(device: Any) -> list[str]:
    """The one owning config-entry id a device reports, if any."""
    config_entry_id = getattr(device, "config_entry_id", None)
    return [config_entry_id] if isinstance(config_entry_id, str) else []


def device_config_entry_ids(device: Any) -> list[str]:
    """Return a device's owning config-entry ids across supported HA versions.

    Reads config_entry_id unless the device is a restored composite, avoiding the deprecation
    warning on config_entries; releases before 2026.9 have config_entries alone.
    """
    if hasattr(device, "config_entry_id") and getattr(device, "is_composite_device", None) is False:
        return _single_owner(device)
    config_entries = getattr(device, "config_entries", None)
    if not isinstance(config_entries, (set, frozenset, list, tuple)):
        return _single_owner(device)
    return sorted(entry_id for entry_id in config_entries if isinstance(entry_id, str))


def device_registry_entity_ids(hass: HomeAssistant, device_id: str) -> list[str]:
    """Return every registry entity currently attached to a device."""
    return sorted(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.device_id == device_id
    )


def device_cascade_entity_ids(
    hass: HomeAssistant,
    device_id: str,
    *,
    snapshot: DeviceRegistrySnapshot | None = None,
) -> list[str]:
    """Return a device's registry entities plus those of its child devices.

    Core cascades deletion, disabling and area from a main device to its children.
    """
    snapshot = snapshot or device_registry_snapshot(hass)
    device_ids = {device_id, *snapshot.part_ids(device_id)}
    return sorted(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.device_id in device_ids
    )


def _phoenix_owned_device(device: Any, hass: HomeAssistant) -> bool:
    """Whether a device belongs to Phoenix MCP and must remain unreachable."""
    for config_entry_id in device_config_entry_ids(device):
        config_entry = hass.config_entries.async_get_entry(config_entry_id)
        if config_entry is not None and config_entry.domain == DOMAIN:
            return True
    registry = er.async_get(hass)
    return any(
        (entry := registry.async_get(entity_id)) is not None
        and entry.platform == DOMAIN
        for entity_id in device_registry_entity_ids(hass, device.id)
    )


def resolve_device_registry_access(
    device_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    snapshot: DeviceRegistrySnapshot | None = None,
) -> Permission:
    """Resolve read access to a device-registry entry.
    A device node, or a child's main-parent node, authorizes it even with no entities.
    Otherwise one attached entity must pass the registry-aware resolver. Pass-through keeps
    its Assist filter.
    """
    if not isinstance(device_id, str):
        return Permission.NOT_FOUND
    snapshot = snapshot or device_registry_snapshot(hass)
    device = snapshot.get(device_id)
    if device is None:
        return Permission.NOT_FOUND
    if _phoenix_owned_device(device, hass):
        return Permission.NO_ACCESS

    entity_ids = device_registry_entity_ids(hass, device_id)

    if not household_admin_access() and not any(
        resolve_registry_access(entity_id, access_key, hass, force_registry_only=device.disabled_by is not None)
        in (Permission.READ, Permission.WRITE) for entity_id in entity_ids
    ):
        return Permission.NO_ACCESS
    if access_key.pass_through:
        expose = assist_expose_check(access_key, hass)
        if expose is not None and not any(
            expose(entity_id)
            and resolve_registry_access(entity_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
            for entity_id in entity_ids
        ):
            return Permission.NO_ACCESS
        return Permission.WRITE

    device_chain = device_authority_chain(hass, device_id)
    if device_chain is None:
        return Permission.NO_ACCESS
    explicit = _device_chain_permission(access_key, device_chain)
    if explicit is not None:
        return explicit

    inherited = [
        resolve_registry_access(
            entity_id,
            access_key,
            hass,
            force_registry_only=device.disabled_by is not None,
        )
        for entity_id in entity_ids
    ]
    if Permission.WRITE in inherited:
        return Permission.WRITE
    if Permission.READ in inherited:
        return Permission.READ
    if Permission.DENY in inherited:
        return Permission.DENY
    return Permission.NO_ACCESS


def device_read_check(access_key: AccessKeyRecord, hass: HomeAssistant) -> Callable[[str], bool]:
    """Return a remembering test of whether this key can read a device, as get_device decides."""
    snapshot: DeviceRegistrySnapshot | None = None
    known: dict[str, bool] = {}

    def readable(device_id: str) -> bool:
        nonlocal snapshot
        if device_id not in known:
            snapshot = snapshot or device_registry_snapshot(hass)
            known[device_id] = resolve_device_registry_access(
                device_id, access_key, hass, snapshot=snapshot) in (Permission.READ, Permission.WRITE)
        return known[device_id]

    return readable


def resolve_device_registry_write(
    device_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    snapshot: DeviceRegistrySnapshot | None = None,
) -> Permission:
    """Resolve the explicit device permission required for whole-device writes.
    Attached entities never authorize this. Scoped callers need GREEN on the device node, or
    on the main parent when the child node is Grey; a RED parent blocks. Pass-through is
    unrestricted.
    """
    if not isinstance(device_id, str):
        return Permission.NOT_FOUND
    snapshot = snapshot or device_registry_snapshot(hass)
    device = snapshot.get(device_id)
    if device is None:
        return Permission.NOT_FOUND
    if _phoenix_owned_device(device, hass):
        return Permission.NO_ACCESS
    if access_key.pass_through:
        return Permission.WRITE
    device_chain = device_authority_chain(hass, device_id)
    if device_chain is None:
        return Permission.NO_ACCESS
    return _device_chain_permission(access_key, device_chain) or Permission.NO_ACCESS


def _device_chain_permission(
    access_key: AccessKeyRecord, device_chain: tuple[str, ...]
) -> Permission | None:
    """Resolve explicit device nodes along a device's authority chain.

    A RED node anywhere on the chain denies; otherwise the most specific
    non-Grey node grants. None means every node on the chain is Grey.
    """
    nodes = [access_key.permissions.devices.get(device_id) for device_id in device_chain]
    if any(node is not None and node.state == "RED" for node in nodes):
        return Permission.DENY
    for node in nodes:
        if node is None:
            continue
        if node.state == "GREEN":
            return Permission.WRITE
        if node.state == "YELLOW":
            return Permission.READ
    return None


def config_entry_registry_context(
    entry_id: str, hass: HomeAssistant
) -> ConfigEntryRegistryContext | None:
    """Collect exact entity and device membership for a config entry.

    Devices come from the registry's owner set, entities from their config_entry_id. Phoenix
    MCP's own entry reads as absent.
    """
    if not isinstance(entry_id, str):
        return None
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return None
    entity_ids = tuple(sorted(
        item.entity_id
        for item in er.async_get(hass).entities.values()
        if item.config_entry_id == entry_id
    ))
    device_ids = tuple(sorted(
        device.id
        for device in device_registry_snapshot(hass).entries
        if entry_id in device_config_entry_ids(device)
        and not _phoenix_owned_device(device, hass)
    ))
    return ConfigEntryRegistryContext(entry, entity_ids, device_ids)


def resolve_config_entry_registry_access(
    entry_id: str, access_key: AccessKeyRecord, hass: HomeAssistant
) -> Permission:
    """Resolve read visibility inherited from an entry's owned resources."""
    context = config_entry_registry_context(entry_id, hass)
    if context is None:
        return Permission.NOT_FOUND
    if access_key.pass_through:
        expose = assist_expose_check(access_key, hass)
        if expose is None or (not context.entity_ids and not context.device_ids):
            return Permission.WRITE
        if any(
            expose(entity_id)
            and resolve_registry_access(entity_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
            for entity_id in context.entity_ids
        ) or any(
            resolve_device_registry_access(device_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
            for device_id in context.device_ids
        ):
            return Permission.WRITE
        return Permission.NO_ACCESS

    expose = assist_expose_check(access_key, hass)
    entity_results = [
        resolve_registry_access(entity_id, access_key, hass)
        for entity_id in context.entity_ids
        if expose is None or expose(entity_id)
    ]
    snapshot = device_registry_snapshot(hass)
    device_results = [
        resolve_device_registry_access(device_id, access_key, hass, snapshot=snapshot)
        for device_id in context.device_ids
    ]
    results = entity_results + device_results
    if Permission.WRITE in results:
        return Permission.WRITE
    if Permission.READ in results:
        return Permission.READ
    if Permission.DENY in results:
        return Permission.DENY
    return Permission.NO_ACCESS


def resolve_config_entry_registry_write(
    entry_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    force_registry_only: bool = False,
) -> Permission:
    """Require complete entity WRITE and explicit device WRITE coverage.

    A scoped key cannot manage a resource-less entry. ``force_registry_only`` proves that a
    disabling caller keeps authority once live states disappear.
    """
    context = config_entry_registry_context(entry_id, hass)
    if context is None:
        return Permission.NOT_FOUND
    if access_key.pass_through:
        return Permission.WRITE
    if not context.entity_ids and not context.device_ids:
        return Permission.NO_ACCESS

    entity_results = [
        resolve_registry_access(
            entity_id,
            access_key,
            hass,
            force_registry_only=force_registry_only,
        )
        for entity_id in context.entity_ids
    ]
    snapshot = device_registry_snapshot(hass)
    device_results = [
        resolve_device_registry_write(device_id, access_key, hass, snapshot=snapshot)
        for device_id in context.device_ids
    ]
    results = entity_results + device_results
    if all(result == Permission.WRITE for result in results):
        return Permission.WRITE
    if Permission.DENY in results:
        return Permission.DENY
    if Permission.READ in results:
        return Permission.READ
    return Permission.NO_ACCESS


def is_sensitive_key(key: Any) -> bool:
    """Return whether a key name marks its value as sensitive, case-insensitively.

    Matches SENSITIVE_ATTRIBUTES, SENSITIVE_KEY_SUBSTRINGS, SENSITIVE_KEY_SUFFIXES or a last
    word in SENSITIVE_KEY_FINAL_WORDS. A dash or a space reads as an underscore, so x-api-key
    and "wifi key" match.
    """
    if not isinstance(key, str):
        return False
    if key in SENSITIVE_ATTRIBUTES:
        return True
    lowered = key.lower().replace("-", "_").replace(" ", "_")
    return (any(marker in lowered for marker in SENSITIVE_KEY_SUBSTRINGS)
            or lowered.endswith(SENSITIVE_KEY_SUFFIXES)
            or re.split(r"[^a-z0-9]+", lowered)[-1] in SENSITIVE_KEY_FINAL_WORDS)


def scrub_attributes(
    attributes: Mapping[str, Any], access_key: AccessKeyRecord, hass: HomeAssistant,
) -> dict[str, Any]:
    """Return state attributes as this access key may read them. Event loop only.

    A sensitive attribute is dropped. Nested values get the service-response filter, except
    that strings merely shaped like an entity ID stay readable.
    """
    return {
        key: filter_service_response(value, access_key, hass, keep_unknown=True)
        for key, value in attributes.items()
        if not is_sensitive_key(key)
        and not (isinstance(key, str) and _hidden_entity_reference(key, access_key, hass, keep_unknown=True))
    }


def scrub_sensitive_attributes(state: State, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict[str, Any]:
    """Return a state dict with its attributes scrubbed for this access key."""
    d = state.as_dict()
    # HA types as_dict()'s values as a union wide enough to include datetime, so
    # the attributes mapping does not narrow on its own. It is always a mapping.
    attributes: Mapping[str, Any] = d.get("attributes") or {}  # type: ignore[assignment]
    return {**d, "attributes": scrub_attributes(attributes, access_key, hass)}


def scrub_state_dict(d: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Return a copy of a raw state dict with its attributes scrubbed for this key."""
    return {**d, "attributes": scrub_attributes(d.get("attributes", {}), access_key, hass)}


_off_loop_reported = False


def _assist_exposed(hass: HomeAssistant, entity_id: str) -> bool:
    """Return HA's conversation exposure for one canonical, existing entity ID.

    HA's lookup writes records on first sight, so off the event loop this fails closed and
    reports the caller once.
    """
    global _off_loop_reported
    if threading.get_ident() != hass.loop_thread_id:
        if not _off_loop_reported:
            _off_loop_reported = True
            _LOGGER.error("Assist exposure was checked off the event loop; the entity was treated as unexposed")
        return False
    # Local: optional component, imported only when this path runs.
    from homeassistant.components.homeassistant.exposed_entities import (
        async_should_expose,
    )

    return bool(async_should_expose(hass, "conversation", entity_id))


def assist_expose_check(access_key: AccessKeyRecord, hass: HomeAssistant) -> Callable[[str], bool] | None:
    """Return the Assist predicate for a pass-through key using exposure, else None."""
    if not (access_key.pass_through and access_key.use_assist_exposure):
        return None
    return lambda entity_id: _assist_exposed(hass, entity_id)


def resolve_read(
    entity_id: Any, access_key: AccessKeyRecord, hass: HomeAssistant, *, registry: bool = False
) -> Permission:
    """Return read permission for one entity, unexposed reads as missing. Event loop only."""
    return (resolve_registry_access if registry else resolve)(entity_id, access_key, hass)


def filter_entities_for_access_key(
    entities: list[State],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> list[dict[str, Any]]:
    """Filter State objects to those accessible by the key, scrubbing sensitive attributes.

    resolve() is the single predicate for scoped and pass-through keys alike; pass-through
    adds only the use_assist_exposure check.
    """
    expose = assist_expose_check(access_key, hass)
    return [
        scrub_sensitive_attributes(e, access_key, hass)
        for e in entities
        if resolve(e.entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
        and (expose is None or expose(e.entity_id))
    ]


def _eligible_for_indirect_expansion(entry: er.RegistryEntry) -> bool:
    """Return whether indirect target expansion may reach this entity."""
    return entry.entity_category is None and entry.hidden_by is None


def _service_area_ids(hass: HomeAssistant, values: list[str]) -> list[str]:
    """Return area IDs for service targets given as an ID, a name or an alias.

    Unknown and hidden areas both expand to nothing, and the reading is never noted, so naming
    an area cannot confirm one the key cannot see.
    """
    registry = ar.async_get(hass)
    resolved: list[str] = []
    for value in values:
        if registry.async_get_area(value) is not None:
            resolved.append(value)
            continue
        wanted = value.strip().casefold()
        matches = [area.id for area in registry.async_list_areas()
                   if wanted in {area.name.casefold(), *(str(alias).casefold() for alias in area.aliases or ())}]
        resolved.extend(matches)
    return resolved


def expand_service_targets(
    *,
    entity_id: str | list[str] | None,
    device_id: str | list[str] | None,
    area_id: str | list[str] | None,
    service_domain: str,
    hass: HomeAssistant,
) -> tuple[set[str], list[str]]:
    """Expand service call targets without permission filtering or registry checks.

    Returns (device_area_candidates, explicit_entity_ids); callers must validate the explicit
    IDs against the registry. resolve_service_targets gives the filtered result.
    """
    entity_registry = er.async_get(hass)
    device_snapshot = device_registry_snapshot(hass)

    candidates: set[str] = set()
    explicit_ids: list[str] = []

    if entity_id is not None:
        if isinstance(entity_id, str):
            ids = [entity_id]
        elif isinstance(entity_id, (list, tuple)):
            # Non-string members are dropped rather than stringified: they
            # cannot name an entity, and inventing one only defers the refusal.
            ids = [e for e in entity_id if isinstance(e, str)]
        else:
            ids = []
        for eid in ids:
            if eid == "all":
                for state in hass.states.async_all():
                    if state.entity_id.split(".")[0] == service_domain:
                        candidates.add(state.entity_id)
            else:
                explicit_ids.append(eid)

    if device_id is not None:
        if isinstance(device_id, str):
            dids = [device_id]
        elif isinstance(device_id, (list, tuple)):
            dids = [d for d in device_id if isinstance(d, str)]
        else:
            dids = []
        # Like HA's own target helper, a parent device reaches its parts too.
        target_device_ids = {
            target_id
            for did in dids
            for target_id in (did, *device_snapshot.part_ids(did))
        }
        for entry in entity_registry.entities.values():
            if (
                entry.device_id in target_device_ids
                and entry.domain == service_domain
                and not entry.disabled_by
                and _eligible_for_indirect_expansion(entry)
            ):
                candidates.add(entry.entity_id)

    if area_id is not None:
        if isinstance(area_id, str):
            aids = [area_id]
        elif isinstance(area_id, (list, tuple)):
            aids = [a for a in area_id if isinstance(a, str)]
        else:
            aids = []
        aids = _service_area_ids(hass, aids)
        # Build indexes once to avoid O(A*E) and O(D*E) nested iteration.
        device_entity_index: dict[str, list[str]] = {}
        area_entity_index: dict[str, list[str]] = {}
        for entry in entity_registry.entities.values():
            if (
                entry.domain == service_domain
                and not entry.disabled_by
                and _eligible_for_indirect_expansion(entry)
            ):
                if entry.device_id:
                    device_entity_index.setdefault(entry.device_id, []).append(entry.entity_id)
                if entry.area_id:
                    area_entity_index.setdefault(entry.area_id, []).append(entry.entity_id)
        for aid in aids:
            for eid in area_entity_index.get(aid, []):
                candidates.add(eid)
            for device in device_snapshot.entries:
                if device_snapshot.effective_area_id(device.id) == aid:
                    for eid in device_entity_index.get(device.id, []):
                        candidates.add(eid)

    # No explicit targets: expand to all domain entities. This mirrors native HA
    # behavior (a service call with no targets affects all entities in the domain).
    # Phoenix MCP still filters through resolve_service_targets before calling HA, so only
    # WRITE-permitted entities are included in the final call.
    if entity_id is None and device_id is None and area_id is None:
        for state in hass.states.async_all():
            if state.entity_id.split(".")[0] == service_domain:
                candidates.add(state.entity_id)

    return candidates, explicit_ids


def resolve_service_targets(
    *,
    entity_id: str | list[str] | None = None,
    device_id: str | list[str] | None = None,
    area_id: str | list[str] | None = None,
    service_domain: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> tuple[list[str], int]:
    """Resolve service call targets to a WRITE-permitted, deduplicated entity_id list.
    Returns (permitted_entities, visible_count). An unregistered explicit ID is skipped and
    counts like a hidden entity. An empty list means the caller must return 403. device_id,
    area_id and 'all' never pass through to HA.
    """
    entity_registry = er.async_get(hass)

    candidates, explicit_ids = expand_service_targets(
        entity_id=entity_id,
        device_id=device_id,
        area_id=area_id,
        service_domain=service_domain,
        hass=hass,
    )

    unregistered: set[str] = set()
    for eid in explicit_ids:
        entry = entity_registry.async_get(eid.lower())
        if entry is None:
            unregistered.add(eid.lower())
        else:
            # HA accepts registry IDs too. Every later gate and dispatch must use
            # the same canonical identity that permission resolution authorizes.
            candidates.add(entry.entity_id)

    # Deduplicate while preserving order, then filter to WRITE-permitted entities
    seen: set[str] = set()
    permitted: list[str] = []
    visible = 0
    for eid in candidates:
        if eid in seen:
            continue
        seen.add(eid)
        permission = resolve(eid, access_key, hass)
        if permission in (Permission.READ, Permission.WRITE):
            visible += 1
        if permission == Permission.WRITE and not group_member_denied(eid, access_key, hass):
            permitted.append(eid)
    # A stateful entity without a registry entry (YAML, no unique ID) that the
    # key can read is counted as dropped; a ghost resolves NOT_FOUND and is not.
    visible += sum(
        resolve(eid, access_key, hass) in (Permission.READ, Permission.WRITE)
        for eid in unregistered - seen
    )

    return permitted, visible


def loaded_group_members(hass: HomeAssistant, entity_id: str) -> list[object]:
    """Read the members a group entity holds; an unavailable state drops them, the entity not."""
    component = hass.data.get(DATA_INSTANCES, {}).get(entity_id.split(".", 1)[0])
    entity = component.get_entity(entity_id) if component is not None else None
    if entity is None or getattr(entity.platform, "platform_name", None) != "group":
        return []
    configured = (entity.extra_state_attributes or {}).get("entity_id")
    return [] if configured is None else [configured]


def _switch_wrapper_source(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    """Return the entity a Switch as X wrapper actuates, or None if its entry cannot say."""
    # Static and readable in the config entry, unlike a template entity's arbitrary target.
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id) if entry.config_entry_id else None
    reference = config_entry.options.get("entity_id") if config_entry is not None else None
    if not isinstance(reference, str):
        return None
    source = er.async_get(hass).async_get(reference)
    if source is not None:
        return source.entity_id
    return reference.lower() if valid_entity_id(reference.lower()) else None


def group_member_ids(entity_id: str, hass: HomeAssistant) -> frozenset[str] | None:
    """Return every member of a group helper, nested groups included.

    A state listing members counts whatever the registry says, as a YAML group has no entry,
    and an unavailable group answers from its loaded entity. A Switch as X wrapper has its
    source as its one member. Empty for anything else; scenes, scripts and automations stay
    governed by their primary entity. None when membership cannot be read, which callers
    treat as a refusal.
    """
    registry = er.async_get(hass)
    members: set[str] = set()
    pending = [(entity_id, 0)]
    seen = {entity_id}
    while pending:
        current, depth = pending.pop()
        if current.split(".", 1)[0] in PRIMARY_ENTITY_DOMAINS:
            continue
        entry = registry.async_get(current)
        if entry is not None and entry.platform == SWITCH_AS_X_PLATFORM:
            source = _switch_wrapper_source(hass, entry)
            if source is None or depth >= APPROVAL_TARGET_DEPTH:
                return None
            members.add(source)
            if source not in seen:
                seen.add(source)
                pending.append((source, depth + 1))
            continue
        state = hass.states.get(current)
        attributes = state.attributes if state is not None else {}
        helper = current.startswith("group.") or (entry is not None and entry.platform == "group")
        listed = [attributes[key] for key in ("group_entities", "entity_id") if key in attributes]
        lists_members = isinstance(attributes.get("entity_id"), (list, tuple, set, frozenset))
        if not (listed or state is None or state.state != STATE_UNAVAILABLE):
            listed = loaded_group_members(hass, current)
            lists_members = bool(listed)
        if not (helper or "group_entities" in attributes or lists_members):
            continue
        if not listed or depth >= APPROVAL_TARGET_DEPTH:
            return None
        for value in listed:
            if not isinstance(value, (list, tuple, set, frozenset)) or len(value) > APPROVAL_TARGET_LIMIT:
                return None
            for member in value:
                if not isinstance(member, str):
                    return None
                members.add(member.lower())
                if member.lower() not in seen:
                    seen.add(member.lower())
                    pending.append((member.lower(), depth + 1))
        if len(members) > APPROVAL_TARGET_LIMIT:
            return None
    members.discard(entity_id)
    return frozenset(members)


def group_member_denied(entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> bool:
    """Return whether a target is a group reaching a member RED for this key.

    Only RED counts: a grant on a group reaches ungranted members on purpose.
    """
    members = group_member_ids(entity_id, hass)
    if members is None:
        return True
    return any(resolve(member, access_key, hass) == Permission.DENY for member in members)


def unreadable_group_targets(
    entity_id: str | list[str] | None, access_key: AccessKeyRecord, hass: HomeAssistant,
) -> list[str]:
    """Return the named targets this key may write that refuse only as unreadable groups.

    Names nothing the key could not already see, so a refusal can say why.
    """
    named = [entity_id] if isinstance(entity_id, str) else entity_id if isinstance(entity_id, list) else []
    return [
        target for target in dict.fromkeys(canonical_entity_id(item, hass) for item in named if isinstance(item, str))
        if resolve(target, access_key, hass) == Permission.WRITE and group_member_ids(target, hass) is None
    ]


def unregistered_targets(
    entity_id: str | list[str] | None, access_key: AccessKeyRecord, hass: HomeAssistant,
) -> list[str]:
    """Return the named targets this key may write that resolve_service_targets skips for lack of a registry entry.

    Names nothing the key could not already see, so a refusal can say why. An unreadable group
    keeps its own refusal.
    """
    registry = er.async_get(hass)
    named = [entity_id] if isinstance(entity_id, str) else entity_id if isinstance(entity_id, list) else []
    return [
        target for target in dict.fromkeys(canonical_entity_id(item, hass) for item in named if isinstance(item, str))
        if registry.async_get(target) is None and resolve(target, access_key, hass) == Permission.WRITE
        and group_member_ids(target, hass) is not None
    ]


def service_target_binding_valid(
    domain: str, service: str, targets: list[str], hass: HomeAssistant,
) -> bool:
    """Bind named script services to the registry entity they actually execute.

    HA's script.<stored_id> handler ignores target selectors, so it must match exactly one
    authorized, enabled script. Reload takes the no-target capability branch.
    """
    if domain != "script" or service in SCRIPT_ENTITY_SERVICES:
        return True
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("script", "script", service)
    entry = registry.async_get(entity_id) if entity_id else None
    return bool(entry is not None and not entry.disabled_by and targets == [entity_id])


def physical_gate_applies(domain: str, service: str, targets: list[str]) -> bool:
    """Return whether the physical gate covers a call over a resolved target list.

    The single rule for enforcement and preview. Every service of a physical actuator domain
    qualifies, as does a raw command service, and any other service with a physical target
    unless it cannot actuate one.
    """
    service_key = f"{domain}/{service}"
    if domain in PHYSICAL_GATE_DOMAINS or service_key in PHYSICAL_COMMAND_SERVICES:
        return True
    if service_key in NON_ACTUATING_SERVICES:
        return False
    return any(entity_id.split(".", 1)[0] in PHYSICAL_GATE_DOMAINS for entity_id in targets)


def firmware_flash_applies(domain: str, service: str, targets: list[str], hass: HomeAssistant) -> bool:
    """Return whether a call installs firmware on an ESPHome device, as the install tool does."""
    if f"{domain}/{service}" != UPDATE_INSTALL_SERVICE:
        return False
    registry = er.async_get(hass)
    return any((entry := registry.async_get(target)) is not None and entry.platform == ESPHOME_DOMAIN
               for target in targets)


def response_required(domain: str, service: str, hass: HomeAssistant) -> bool:
    """Return whether Core refuses a call to this service unless a response is requested."""
    handler = hass.services.async_services_for_domain(domain).get(service)
    return handler is not None and handler.supports_response is SupportsResponse.ONLY


def whole_device_entities(targets: list[str], hass: HomeAssistant) -> list[str]:
    """Return the targets with every entity of their devices and child devices, disabled ones too."""
    registry = er.async_get(hass)
    snapshot = device_registry_snapshot(hass)
    reached = dict.fromkeys(targets)
    for target in targets:
        entry = registry.async_get(target)
        if entry is not None and entry.device_id:
            reached.update(dict.fromkeys(device_cascade_entity_ids(hass, entry.device_id, snapshot=snapshot)))
    return list(reached)


def service_gate_targets(
    *,
    domain: str,
    service: str,
    entity_id: str | list[str] | None = None,
    device_id: str | list[str] | None = None,
    area_id: str | list[str] | None = None,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> list[str]:
    """Return the entities a call reaches, for the gates that follow target domains.

    The resolved targets with the members of any group among them, so a generic toggle of a
    group of covers counts; every entity of the owning device for an ESPHome device action,
    whose firmware can drive any of them; nothing for a service that ignores targets.
    """
    if f"{domain}/{service}" in DUAL_GATE_SERVICES | NO_TARGET_SERVICES | MUSIC_ASSISTANT_LIBRARY_SERVICES:
        return []
    if domain == ESPHOME_DOMAIN and (entry := resolve_esphome_user_service(hass, service)) is not None:
        return [
            reg_entry.entity_id
            for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        ]
    targets, _ = resolve_service_targets(
        entity_id=entity_id, device_id=device_id, area_id=area_id,
        service_domain=domain, access_key=access_key, hass=hass,
    )
    reached = list(targets)
    for target in targets:
        reached.extend(sorted(group_member_ids(target, hass) or ()))
    return reached


def call_needs_physical_gate(
    *,
    domain: str,
    service: str,
    entity_id: str | list[str] | None = None,
    device_id: str | list[str] | None = None,
    area_id: str | list[str] | None = None,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> bool:
    """Return True if a call_service must gate on cap_physical_control.

    Resolves what the call reaches, then applies physical_gate_applies.
    """
    return physical_gate_applies(domain, service, []) or physical_gate_applies(
        domain, service, service_gate_targets(
            domain=domain, service=service, entity_id=entity_id, device_id=device_id,
            area_id=area_id, access_key=access_key, hass=hass,
        ),
    )


def required_service_cap(
    *,
    domain: str,
    service: str,
    service_data: object,
    entity_id: str | list[str] | None = None,
    device_id: str | list[str] | None = None,
    area_id: str | list[str] | None = None,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> str | None:
    """Return the capability that decides a call, shared by admission, execution and preview.

    A call may need several capabilities at once; the one binding this key hardest decides,
    so none is passed over.
    """
    targets = service_gate_targets(
        domain=domain, service=service, entity_id=entity_id, device_id=device_id,
        area_id=area_id, access_key=access_key, hass=hass,
    )
    return strictest_capability(
        access_key,
        "cap_physical_control" if physical_gate_applies(domain, service, targets) else None,
        "cap_esphome_flash" if firmware_flash_applies(domain, service, targets, hass) else None,
        "cap_service_response" if response_required(domain, service, hass) else None,
        *service_capabilities(domain, service, service_data, targets),
    )


def _hidden_entity_reference(
    value: str, access_key: AccessKeyRecord, hass: HomeAssistant, *, keep_unknown: bool,
) -> bool:
    """Whether a string names an entity this access key may not see."""
    if not _ENTITY_ID_RE.match(value):
        return False
    permission = resolve(value, access_key, hass)
    if permission == Permission.NOT_FOUND:
        return not keep_unknown
    return permission in (Permission.NO_ACCESS, Permission.DENY)


def filter_service_response(
    response_data: Any,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    _depth: int = 0,
    *,
    max_depth: int = 10,
    truncated: list[list] | None = None,
    keep_unknown: bool = False,
    _path: tuple = (),
) -> Any:
    """Recursively redact secrets and inaccessible entity IDs from service response data.

    Such IDs and sensitive keys' values become "<redacted>"; entries keyed by a hidden ID
    drop. Past max_depth a container is redacted and recorded in ``truncated``, else emptied.
    """
    if isinstance(response_data, (tuple, set, frozenset)):
        response_data = list(response_data)
    if _depth > max_depth:
        # Depth limit reached. Still redact entity ID strings, but never return
        # a container's raw contents: it could hold entity IDs at deeper levels
        # that would bypass redaction.
        if isinstance(response_data, str):
            hidden = _hidden_entity_reference(response_data, access_key, hass, keep_unknown=keep_unknown)
            return "<redacted>" if hidden else response_data
        if isinstance(response_data, (dict, list)):
            if truncated is not None:
                truncated.append(list(_path))
                return "<redacted>"
            _LOGGER.warning(
                "filter_service_response: depth limit reached, truncating %s to empty",
                type(response_data).__name__,
            )
            return {} if isinstance(response_data, dict) else []
        return response_data
    nested: dict[str, Any] = {"max_depth": max_depth, "truncated": truncated, "keep_unknown": keep_unknown}
    if isinstance(response_data, str):
        hidden = _hidden_entity_reference(response_data, access_key, hass, keep_unknown=keep_unknown)
        return "<redacted>" if hidden else response_data
    if isinstance(response_data, dict):
        out: dict = {}
        for k, v in response_data.items():
            if isinstance(k, str) and _hidden_entity_reference(k, access_key, hass, keep_unknown=keep_unknown):
                continue
            out[k] = (
                "<redacted>" if is_sensitive_key(k)
                else filter_service_response(
                    v, access_key, hass, _depth + 1, **nested,
                    _path=(*_path, k) if truncated is not None else (),
                )
            )
        return out
    if isinstance(response_data, list):
        return [
            filter_service_response(
                item, access_key, hass, _depth + 1, **nested,
                _path=(*_path, index) if truncated is not None else (),
            )
            for index, item in enumerate(response_data)
        ]
    return response_data


def get_effective_hint(
    access_key: AccessKeyRecord,
    entity_id: str,
    hass: HomeAssistant,
    entity_hints: dict[str, str] | None = None,
) -> str | None:
    """Return the most specific hint for an entity.

    Checks the key's entity, device, then domain nodes, then the global ``entity_hints`` map.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    permissions = access_key.permissions

    entity_node = permissions.entities.get(entity_id)
    if entity_node and entity_node.hint:
        return entity_node.hint

    for device_id in device_authority_chain(hass, entry.device_id if entry else None) or ():
        device_node = permissions.devices.get(device_id)
        if device_node and device_node.hint:
            return device_node.hint

    domain = entity_id.split(".")[0]
    domain_node = permissions.domains.get(domain)
    if domain_node and domain_node.hint:
        return domain_node.hint

    if entity_hints:
        return entity_hints.get(entity_id)
    return None


def template_blocklist_vars() -> dict:
    """Return variables that shadow HA template globals to block entity enumeration.
    Pass as ``**template_blocklist_vars()`` to Template.async_render(), since locals shadow
    globals. ``secrets`` and ``hass`` become None, metadata helpers return None, and
    enumeration helpers return empty iterables.
    """
    return {
        # Remove secrets and replace hass with a safe (non-functional) substitute.
        "hass": None,
        "secrets": None,
        # Block config_entry_attr() to prevent integration metadata enumeration.
        "config_entry_attr": lambda *a, **kw: None,
        "integration_entities": lambda *a, **kw: [],
        "area_entities": lambda *a, **kw: [],
        "area_devices": lambda *a, **kw: [],
        "device_entities": lambda *a, **kw: [],
        "expand": lambda *a, **kw: [],
        "label_entities": lambda *a, **kw: [],
        "label_areas": lambda *a, **kw: [],
        "floor_entities": lambda *a, **kw: [],
        "floor_areas": lambda *a, **kw: [],
        "device_attr": lambda *a, **kw: None,
        "device_id": lambda *a, **kw: None,
        "areas": lambda *a, **kw: [],
        "labels": lambda *a, **kw: [],
        "label_id": lambda *a, **kw: None,
        "label_name": lambda *a, **kw: None,
        "floors": lambda *a, **kw: [],
        "floor_id": lambda *a, **kw: None,
        "floor_name": lambda *a, **kw: None,
        "closest": lambda *a, **kw: None,
        "is_device_attr": lambda *a, **kw: False,
        "area_id": lambda *a, **kw: None,
        # State access - can reveal state/attributes of out-of-scope entities
        "state_translated": lambda *a, **kw: None,
        "state_attr_translated": lambda *a, **kw: None,
        "entity_name": lambda *a, **kw: None,
        "distance": lambda *a, **kw: None,
        "is_hidden_entity": lambda *a, **kw: False,
        # Device/area/config metadata enumeration
        "config_entry_id": lambda *a, **kw: None,
        "device_name": lambda *a, **kw: None,
        "area_name": lambda *a, **kw: None,
        "label_description": lambda *a, **kw: None,
        "label_devices": lambda *a, **kw: [],
        # HA issues (could reveal integration problems or installed components)
        "issues": lambda *a, **kw: [],
        "issue": lambda *a, **kw: None,
    }


class RelativeTimeError(ValueError):
    """A recognized relative time Phoenix refuses; the message is safe to return."""


def parse_relative_time(value: str) -> datetime:
    """Parse a relative time string to a UTC datetime.
    Takes an amount and unit (30min, 24h, 7d, 2w, 1mo), meaning that long ago, or 'now'; a
    leading plus means ahead. A bare 'm' is refused as ambiguous. Raises RelativeTimeError for
    a refused time and ValueError for anything else.
    """
    if not isinstance(value, str):
        raise ValueError(f"Unrecognized relative time format: {value!r}")
    text = value.strip().lower()
    if text == "now":
        return utcnow()
    match = _RELATIVE_TIME_RE.match(text)
    if not match:
        raise ValueError(f"Unrecognized relative time format: {value!r}")
    sign, amount, unit_text, ago = match.groups()
    n = int(amount)
    if unit_text == "m":
        raise RelativeTimeError(f"{value.strip()!r} is ambiguous: use {n}min for minutes or {n}mo for months.")
    unit = _RELATIVE_UNITS.get(unit_text)
    if unit is None or (sign == "+" and ago):
        raise ValueError(f"Unrecognized relative time format: {value!r}")
    delta = timedelta(days=30 * n) if unit == "months" else timedelta(**{unit: n})
    if delta > timedelta(days=_RELATIVE_MAX_DAYS):
        raise RelativeTimeError(
            f"Relative time {value.strip()!r} exceeds the maximum allowed range of {_RELATIVE_MAX_DAYS} days."
        )
    return utcnow() + delta if sign == "+" else utcnow() - delta


def _selector_list(value: object) -> list[str] | None:
    """Validate a list-valued intent selector. None means "not provided"."""
    if isinstance(value, list) and value and all(
        isinstance(item, str) and item for item in value
    ):
        return value
    return None


class IntentSelectors(NamedTuple):
    """The five native targeting selectors, coerced to the shapes HA assumes.

    async_match_targets assumes plain strings and iterables. A wrong-shaped value becomes "not
    provided", never stringified, as str(["a"]) would invent an argument.
    """

    name: str | None
    area: str | None
    floor: str | None
    domains: list[str] | None
    device_classes: list[str] | None
    # Selectors the caller supplied in a shape that cannot be used. Any one makes
    # the whole selection unusable: dropping it would let the remaining selectors
    # match more than the caller asked for.
    malformed: tuple[str, ...] = ()

    @property
    def none_usable(self) -> bool:
        """Return True when nothing usable survived coercion, so nothing may match."""
        return not (self.name or self.area or self.floor or self.domains or self.device_classes)


def normalize_intent_selectors(
    *,
    name: object = None,
    area: object = None,
    floor: object = None,
    domains: object = None,
    device_classes: object = None,
) -> IntentSelectors:
    """Coerce the five targeting selectors, for resolver and native tools alike."""
    selectors = IntentSelectors(
        name=name if isinstance(name, str) else None,
        area=area if isinstance(area, str) else None,
        floor=floor if isinstance(floor, str) else None,
        domains=_selector_list(domains),
        device_classes=_selector_list(device_classes),
    )
    supplied = (("name", name, selectors.name), ("area", area, selectors.area),
                ("floor", floor, selectors.floor), ("domain", domains, selectors.domains),
                ("device_class", device_classes, selectors.device_classes))
    malformed = tuple(label for label, raw, usable in supplied
                      if raw is not None and raw != "" and raw != [] and usable is None)
    return selectors._replace(malformed=malformed) if malformed else selectors


def resolve_intent_entities(
    hass: HomeAssistant,
    access_key: AccessKeyRecord,
    *,
    domains: list[str] | None = None,
    device_classes: list[str] | None = None,
    name: str | None = None,
    area: str | None = None,
    floor: str | None = None,
    read_only: bool = False,
    allow_duplicate_names: bool = False,
) -> list[str]:
    """Resolve intent targeting (area/name/floor/domain/device_class) to an entity_id list.

    Matches through HA's async_match_targets. Silently drops entities the key cannot WRITE, or
    read when ``read_only`` serves a preview. Returns an empty list when nothing matches.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.intent import MatchTargetsConstraints
    from homeassistant.helpers.intent import async_match_targets

    # A model does not always follow the tool schema (for example a list for name
    # where a string was declared). HA's async_match_targets assumes name/area/floor
    # are plain strings and calls .strip() on them, so a list raises AttributeError;
    # it likewise iterates the LIST selectors. Any wrong-shaped selector matches
    # nothing: treating it as absent would let the other selectors widen the
    # target set beyond the request. The coercion lives in
    # normalize_intent_selectors so the native tools can explain the refusal
    # without a second copy of these rules.
    selectors = normalize_intent_selectors(
        name=name, area=area, floor=floor,
        domains=domains, device_classes=device_classes,
    )
    if selectors.malformed:
        return []
    name, area, floor = selectors.name, selectors.area, selectors.floor
    domains, device_classes = selectors.domains, selectors.device_classes

    registry = er.async_get(hass)
    expose = assist_expose_check(access_key, hass)

    def _intent_eligible(entity_id: str) -> bool:
        # Exclude config/diagnostic and hidden entities from native Hass* intent
        # matching, matching HA's primary_entities_only default. Phoenix MCP passes
        # assistant=None (bypassing Assist exposure), so without this an area or
        # domain match would sweep in same-domain config entities native HA never
        # targets. Applied to name matches too: an operator who really wants one
        # drives it via call_service with an explicit entity_id, the direct path
        # that stays unfiltered. Unregistered states have no category, so they are
        # kept.
        entry = registry.async_get(entity_id)
        return entry is None or _eligible_for_indirect_expansion(entry)

    permitted: list[State] = [
        s for s in hass.states.async_all()
        if resolve(s.entity_id, access_key, hass) in (
            (Permission.READ, Permission.WRITE) if read_only else (Permission.WRITE,)
        )
        and (expose is None or expose(s.entity_id))
        and _intent_eligible(s.entity_id)
    ]

    # HA interprets an empty states list as all states. Never pass it through.
    if not permitted:
        return []

    if not name and not area and not floor:
        if not domains and not device_classes:
            # No selector at all (no name/area/floor/domain/device_class). A native
            # intent action with zero constraints must match nothing, not fan out to
            # the access key's entire writable scope. An unrecognized argument (e.g. a
            # model passing entity_id, which these tools do not accept) leaves every
            # recognized selector empty; without this guard the call would silently
            # actuate every entity the access key can write. Fail closed.
            return []
        if domains:
            domain_set = set(domains)
            permitted = [s for s in permitted if s.entity_id.split(".")[0] in domain_set]
        if device_classes:
            dc_set = set(device_classes)
            permitted = [s for s in permitted if s.attributes.get("device_class") in dc_set]
        return [s.entity_id for s in permitted]

    constraints = MatchTargetsConstraints(
        name=name,
        area_name=area,
        floor_name=floor,
        domains=domains,
        device_classes=device_classes,
        assistant=None,
        allow_duplicate_names=allow_duplicate_names,
    )
    result = async_match_targets(
        hass,
        constraints,
        states=permitted,
    )
    if not result.is_match:
        return []
    return [s.entity_id for s in result.states]


# ---------------------------------------------------------------------------
# ESPHome user-defined actions
#
# Firmware-declared actions are esphome.<device_name_with_underscores>_<action_name>. Their
# schema holds only declared arguments, so they take no entity target and miss the
# target-flattening path. Authorization is the owning device's write scope: an action can
# act on nothing else.
# ---------------------------------------------------------------------------


def esphome_entry_for_entity(hass: HomeAssistant, entity_id: str) -> Any | None:
    """Return the LOADED ESPHome config entry owning entity_id, or None."""
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or not reg_entry.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
    if entry is None or entry.domain != ESPHOME_DOMAIN:
        return None
    return entry if _esphome_entry_loaded(entry) else None


def _esphome_entry_loaded(entry: Any) -> bool:
    """Return True when the entry is loaded, so reading runtime_data is safe."""

    return entry.state is ConfigEntryState.LOADED


def resolve_esphome_user_service(hass: HomeAssistant, service: str) -> Any | None:
    """Find the loaded ESPHome config entry that owns a user-defined action.

    Keeps the longest device-name prefix that declares the action. Returns None for any
    service no loaded entry claims, including stale registrations HA leaves in hass.services.
    """
    best: Any | None = None
    best_len = -1
    for entry in hass.config_entries.async_entries(ESPHOME_DOMAIN):
        if not _esphome_entry_loaded(entry):
            continue
        runtime = getattr(entry, "runtime_data", None)
        device_info = getattr(runtime, "device_info", None)
        name = getattr(device_info, "name", None)
        if not name:
            continue
        prefix = f"{name.replace('-', '_')}_"
        if not service.startswith(prefix) or len(prefix) <= best_len:
            continue
        declared = {
            getattr(svc, "name", None)
            for svc in (getattr(runtime, "services", None) or {}).values()
        }
        if service[len(prefix):] in declared:
            best, best_len = entry, len(prefix)
    return best


def esphome_entry_writable(hass: HomeAssistant, entry: Any, access_key: AccessKeyRecord) -> bool:
    """True when the access key holds WRITE on at least one of the entry's entities."""
    registry = er.async_get(hass)
    return any(
        resolve(reg_entry.entity_id, access_key, hass) is Permission.WRITE
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    )


def music_assistant_readable_entries(hass: HomeAssistant, access_key: AccessKeyRecord) -> list[str]:
    """Loaded Music Assistant entries on which the access key can read an entity.

    Core's library services look the entry up by id without checking its
    domain, so the domain is checked here before the id reaches them.
    """

    registry = er.async_get(hass)
    return [
        entry.entry_id
        for entry in hass.config_entries.async_entries(MUSIC_ASSISTANT_DOMAIN)
        if entry.state is ConfigEntryState.LOADED and any(
            resolve(reg_entry.entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
            for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        )
    ]


class LocationRegistryContext(NamedTuple):
    """Complete location membership, including disabled and entity-free devices."""

    entry: Any
    area_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    device_ids: tuple[str, ...]


def location_registry_context(
    kind: str, location_id: str, hass: HomeAssistant,
) -> LocationRegistryContext | None:
    """Resolve exact location IDs and complete current affected membership."""
    areas = ar.async_get(hass)
    entry: Any
    area_ids: tuple[str, ...]
    if kind == "area":
        entry = areas.async_get_area(location_id)
        area_ids = (location_id,)
    elif kind == "floor":
        entry = fr.async_get(hass).async_get_floor(location_id)
        area_ids = tuple(sorted(area.id for area in areas.areas.values()
                                if area.floor_id == location_id))
    else:
        return None
    if entry is None:
        return None
    snapshot = device_registry_snapshot(hass)
    if snapshot.conflicting_ids or snapshot.invalid_ancestry_ids:
        return None
    device_ids = tuple(sorted(device.id for device in snapshot.entries
                              if snapshot.effective_area_id(device.id) in area_ids))
    entity_ids = tuple(sorted(entity.entity_id for entity in er.async_get(hass).entities.values()
                              if effective_entity_area_id(entity, snapshot) in area_ids
                              or entity.device_id in device_ids))
    return LocationRegistryContext(entry, area_ids, entity_ids, device_ids)


def resolve_location_registry_write(
    kind: str, location_id: str, access_key: AccessKeyRecord, hass: HomeAssistant,
    *, context: LocationRegistryContext | None = None,
) -> Permission:
    """Require whole-membership authority; empty locations require pass-through.

    Location nodes do not exist in the permission tree. Entity grants alone
    cannot authorize changes to an independently scoped device or empty area.
    """
    context = context or location_registry_context(kind, location_id, hass)
    if context is None:
        return Permission.NO_ACCESS
    if not access_key.pass_through:
        if not context.entity_ids and not context.device_ids:
            return Permission.NO_ACCESS
        for area_id in context.area_ids:
            area_context = location_registry_context("area", area_id, hass)
            if area_context is None or not (area_context.entity_ids or area_context.device_ids):
                return Permission.NO_ACCESS
    if any(resolve_registry_access(entity_id, access_key, hass, force_registry_only=True)
           != Permission.WRITE for entity_id in context.entity_ids):
        return Permission.NO_ACCESS
    if any(resolve_device_registry_write(device_id, access_key, hass) != Permission.WRITE
           for device_id in context.device_ids):
        return Permission.NO_ACCESS
    return Permission.WRITE
