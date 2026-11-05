"""MESA semantic-safety runtime for Phoenix MCP.

Wires the vendored mesa-core (custom_components.phoenix_mcp.mesa_core) into the
integration. mesa-core is per-ENTITY policy (what is safe to touch); access keys are
per-CALLER policy (who may touch what). Phoenix MCP flattens targets first, then
MESA evaluates the explicit entity list. MESA is orthogonal to the permission tree
and binds pass-through access keys too.

- The profile store is an in-memory dict (PhoenixMesaBackend) persisted through
  HA's Store. It never blocks, so enforcement runs mesa-core's synchronous APIs on
  the event loop; the retrieval tools (mesa_tools.py) use mesa-core's worker pool.
- The MesaEnforcer is built with ``interactive=False``: a ``control_mode: confirm``
  entity is blocked with rule ``control_mode:confirm_no_channel`` BEFORE any
  challenge is issued, so mesa-core's ConfirmationManager stays empty. Phoenix MCP
  interprets that block itself: a warning in advisory mode, a route to its own
  admin approval gate in enforced mode.
- Enforced-ness is recomputed host-side per entity from public settings and profile data.

mesa-core is read-only: never modify it or reach into its private state.
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import inspect
import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from homeassistant.components.light.const import DATA_PROFILES as LIGHT_DATA_PROFILES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar_mod
from homeassistant.helpers import device_registry as dr_mod
from homeassistant.helpers import entity_registry as er_mod
from homeassistant.helpers import sun as sun_mod
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util.async_ import run_callback_threadsafe
from homeassistant.util.percentage import percentage_to_ranged_value, ranged_value_to_percentage
from homeassistant.util.yaml import load_yaml as _load_yaml

from homeassistant.util import dt as dt_util

from . import mesa_audit
from .approval_targets import bind_targets, bound_preview
from .approvals import (
    create_approval_notification, async_create_pending_approval, fire_approval_requested_event,
)
from .audit import generate_request_id
from .device_semantics import device_authority_chain, device_effective_area_id, device_registry_snapshot
from .durable_storage import DurableStorageError, DurableStore, MesaSaveError
from .helpers import diff_summary_fields, redact_inaccessible_entity_ids_in_text
from .policy_engine import group_member_ids
from .const import (
    MESA_APPROVED_EXECUTOR, MESA_CONFIRM_CAP, MESA_HOST_CALLBACK_TIMEOUT_SECONDS,
    MESA_LEASE_SWEEP_INTERVAL, MESA_MODE_ENFORCED, MESA_MODE_ADVISORY, MESA_MODE_OFF,
    MESA_STORAGE_KEY, MESA_STORAGE_VERSION, DOMAIN, APPROVAL_TARGETS_KEY,
)
from .mesa_core import (
    CallerContext,
    ControlMode,
    InheritanceResolver,
    LeaseManager,
    MesaEnforcer,
    ProfileStore,
    TriggerValidator,
    import_from_integration,
)
from .mesa_core.backends import StorageBackend
from .mesa_core.exceptions import MesaError
from .mesa_core.profile import CONTROL_MODE_RANK
from .request_authority import dispatch_client_ip, dispatch_request_id, check_request_authority

if TYPE_CHECKING:
    from .approvals import PendingApproval
    from .data import PhoenixData
    from .mesa_core import SemanticProfile, ValidationIssue
    from .access_key_store import AccessKeyRecord

_LOGGER = logging.getLogger(__name__)

# MESA's rule name for a call whose parameters name a target other than the
# entity the decision was made for. A caller can still reach it with a field
# that is a target selector to MESA but plain data to Home Assistant, such as
# config_entry_id, so it describes the request and never a Phoenix fault.
_RULE_CONTRADICTORY_TARGET = "contradictory_target"

# Home Assistant's generic turn services forward their data unchanged to the
# same service on each target's own domain.
_GENERIC_TURN_SERVICES = frozenset({"turn_on", "turn_off", "toggle"})

_LIGHT_FULL_BRIGHTNESS = 255
_FAN_PERCENTAGE_SERVICES = ("turn_on", "set_percentage")
_FAN_STEP_SERVICES = frozenset({"increase_speed", "decrease_speed"})
_CLIMATE_RANGE_ENDS = ("target_temp_high", "target_temp_low")

_AUTOMATION_YAML = "automations.yaml"


class MesaStorageCorruptError(MesaError):
    """The stored profile set cannot be trusted, so MESA must not start from it."""


def _exchange_documents(documents: dict[str, dict[str, Any]], first: str, second: str) -> None:
    moved_first, moved_second = documents.pop(first, None), documents.pop(second, None)
    if moved_first is not None:
        documents[second] = moved_first
    if moved_second is not None:
        documents[first] = moved_second


class PhoenixMesaBackend(StorageBackend):
    """In-memory dict storage backend persisted via HA's Store.

    All reads/writes/deletes operate on an in-process dict, so mesa-core's
    synchronous API stays non-blocking on the event loop. Durability is the
    caller's responsibility: mutate, then call MesaRuntime.async_save().
    """

    def __init__(self, initial: dict[str, dict[str, Any]] | None = None) -> None:
        self._data: dict[str, dict[str, Any]] = dict(initial or {})

    def read(self, key: str) -> dict[str, Any] | None:
        value = self._data.get(key)
        return dict(value) if value is not None else None

    def write(self, key: str, data: dict[str, Any]) -> None:
        self._data[key] = dict(data)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def list_keys(self, prefix: str | None = None) -> list[str]:
        keys = sorted(self._data)
        if prefix is not None:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Deep-enough copy for persistence (profile docs are plain JSON)."""
        return {k: dict(v) for k, v in self._data.items()}

    def restore(self, documents: dict[str, dict[str, Any]]) -> None:
        """Replace the whole in-memory set with a saved snapshot."""
        self._data = {k: dict(v) for k, v in documents.items()}

    def exchange(self, first: str, second: str) -> None:
        """Swap the documents under two keys, either of which may be empty."""
        _exchange_documents(self._data, first, second)


@dataclass
class MesaRuntime:
    """Holds the constructed mesa-core objects and Phoenix-side caches."""

    hass: HomeAssistant
    backend: PhoenixMesaBackend
    store: ProfileStore
    resolver: InheritanceResolver
    enforcer: MesaEnforcer
    validator: TriggerValidator
    ha_store: Store
    # Advisory coordination leases. In-memory only by design: they cap at 30
    # seconds and never survive a restart. Exposed to agents through the
    # mesa_request_lease/mesa_release_lease tools (mesa_tools.py); there is no
    # admin UI or HA sensor.
    lease_manager: LeaseManager
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    trigger_issues: list[ValidationIssue] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    orphan_areas: list[str] = field(default_factory=list)
    orphan_integrations: list[str] = field(default_factory=list)
    orphan_devices: list[str] = field(default_factory=list)
    # Profile suggestions (mesa_suggestions.py): cached like the orphan lists.
    # dismissed_suggestions persists in the phoenix_mcp_mesa store (Phoenix-owned sibling
    # key next to "profiles"; mesa-core never sees it).
    suggestions: list = field(default_factory=list)
    dismissed_suggestions: set[str] = field(default_factory=set)
    # What storage holds, which a failed save returns memory to.
    durable_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    durable_dismissed: list[str] = field(default_factory=list)

    async def async_save(self) -> None:
        """Persist the current profile set to HA storage.

        Callers already hold ``self.lock`` for the mutate-then-save sequence. A
        failed write returns memory to the last durable set before raising
        MesaSaveError, so a change reported as failed is not left in force while
        storage holds the old policy.
        """
        profiles = self.backend.snapshot()
        dismissed = sorted(self.dismissed_suggestions)
        try:
            await self.ha_store.async_save({"profiles": profiles, "dismissed_suggestions": dismissed})
        except DurableStorageError as err:
            self.backend.restore(self.durable_profiles)
            self.dismissed_suggestions = set(self.durable_dismissed)
            raise MesaSaveError("MESA profiles could not be saved.") from err
        self.durable_profiles = profiles
        self.durable_dismissed = dismissed

    async def async_wipe(self) -> None:
        """Delete every MESA profile and reset all Phoenix-side MESA caches.

        Callers already hold ``self.lock``. Clears the profile backend (all
        levels: entity/device/domain/integration/area/deployment), the dismissed-
        suggestion set, and the cached orphan/suggestion/trigger lists, then
        persists the emptied store.
        """
        for key in self.backend.list_keys():
            self.backend.delete(key)
        self.dismissed_suggestions.clear()
        self.trigger_issues = []
        self.orphans = []
        self.orphan_areas = []
        self.orphan_integrations = []
        self.orphan_devices = []
        self.suggestions = []
        await self.async_save()

    def set_mode(self, mesa_mode: str) -> None:
        """Update the enforcer's global mode after a settings change.

        ``off`` never calls the enforcer (the verdict helper short-circuits), so
        it maps to advisory here for safety if the enforcer is ever consulted.
        """
        self.enforcer.mode = "enforced" if mesa_mode == MESA_MODE_ENFORCED else "advisory"


_T = TypeVar("_T")


def _on_event_loop(hass: HomeAssistant, callback: Callable[..., _T]) -> Callable[..., _T]:
    """Run a host callback that walks Home Assistant's registries on the event loop.

    mesa-core resolves in its own worker threads (trigger validation and the
    retrieval tools), but Home Assistant changes its registries on the loop only,
    and walking one from another thread while it changes fails with "dictionary
    changed size during iteration". Called on the loop, as enforcement does, the
    callback runs directly.
    """

    def _call(*args: Any) -> _T:
        if threading.get_ident() == hass.loop_thread_id:
            return callback(*args)
        return run_callback_threadsafe(hass.loop, callback, *args).result(MESA_HOST_CALLBACK_TIMEOUT_SECONDS)

    return _call


def _build_get_entity_area(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping entity_id to its effective area_id.

    The entity's own area, else its device's effective area. Runs once per
    resolved entity, so it reads the device and its parent directly instead of
    building a whole-registry snapshot, and on the event loop, where the
    registries change.
    """

    def _get_entity_area(entity_id: str) -> str | None:
        entry = er_mod.async_get(hass).async_get(entity_id)
        if entry is None:
            return None
        return entry.area_id or device_effective_area_id(hass, entry.device_id)

    return _on_event_loop(hass, _get_entity_area)


def _build_get_entity_device(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping entity_id to the device that owns it.

    This is the MESA "device" inheritance level, between entity and area: one
    profile against a device registry id governs every entity that device owns,
    including entities a firmware or integration update adds later, which
    per-entity profiles can never cover.

    The lookup is UNCONDITIONAL, unlike the device read inside
    _build_get_entity_area, which consults the device only when the entity has no
    area of its own. There is no fallback by design: without this callback the
    whole layer is inert, because guessing a device would silently apply policy
    nobody wrote.
    """

    def _get_entity_device(entity_id: str) -> str | None:
        entry = er_mod.async_get(hass).async_get(entity_id)
        return entry.device_id if entry is not None else None

    return _get_entity_device


def _build_get_device_parent(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping a device to the main device it is part of.

    This is the MESA "parent_device" level, between device and area: since HA
    2026.9 a multi-part product is a main device plus one child device per part,
    and the main device's profile governs every part. Only a child device's
    parent_device_id counts; a via_device hub link is connectivity, not
    composition. Malformed ancestry, which Core forbids, reports no parent: the
    permission tree already refuses access to such entities.
    """

    def _get_device_parent(device_id: str) -> str | None:
        chain = device_authority_chain(hass, device_id)
        return chain[1] if chain is not None and len(chain) == 2 else None

    return _get_device_parent


def _build_get_entity_integration(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping entity_id to the integration that created it.

    This is the MESA "integration" inheritance level (between area and domain): the
    entity registry's ``platform`` is the component that created the entity (e.g.
    ``hue``), which is exactly the key a vendor sidecar is stored under. It lets a
    device/hub integration's sidecar govern the entities it created regardless of
    their entity domain (lock.*, sensor.*, ...).
    """

    def _get_entity_integration(entity_id: str) -> str | None:
        entry = er_mod.async_get(hass).async_get(entity_id)
        return entry.platform if entry is not None else None

    return _get_entity_integration


def build_expand_target(hass: HomeAssistant) -> Callable[[str, str], list[str]]:
    """Return the host callback resolving indirect automation targets to entity ids.

    mesa-core's trigger walk (entities_by_role / TriggerValidator) meets target
    selectors it cannot resolve itself: device triggers and the target blocks of
    purpose-specific triggers reference a device_id/area_id/floor_id/label_id, and
    only the host can expand those against the HA registries. Without this
    callback, an automation triggering on a device is invisible to the
    triggers_automations staleness check and to Phoenix MCP's reverse references.

    Deliberately INCLUSIVE, unlike service-target expansion: hidden, config and
    diagnostic entities still fire triggers, so there is no primary-entities-only
    filtering. Disabled registry entries are excluded (they produce no state or
    events). An entity with its own area does not count toward its device's area,
    matching HA's area semantics. Any failure returns [] (a resolution error must
    never break validation or a reverse-reference read). The expansion walks the
    registries, so it runs on the event loop.
    """

    def _device_entities(ent_reg: Any, device_id: str) -> list[str]:
        # A targeted parent device reaches its child devices' entities too.
        return [
            e.entity_id
            for target_id in (device_id, *device_registry_snapshot(hass).part_ids(device_id))
            for e in er_mod.async_entries_for_device(ent_reg, target_id)
            if e.disabled_by is None
        ]

    def _area_entities(ent_reg: Any, area_id: str) -> list[str]:
        out = [
            e.entity_id
            for e in er_mod.async_entries_for_area(ent_reg, area_id)
            if e.disabled_by is None
        ]
        dev_reg = dr_mod.async_get(hass)
        for device in dr_mod.async_entries_for_area(dev_reg, area_id):
            out.extend(
                e.entity_id
                for e in er_mod.async_entries_for_device(ent_reg, device.id)
                if e.disabled_by is None and e.area_id is None
            )
        return out

    def _expand_target(kind: str, ref: str) -> list[str]:
        try:
            ent_reg = er_mod.async_get(hass)
            if kind == "device_id":
                return _device_entities(ent_reg, ref)
            if kind == "area_id":
                return _area_entities(ent_reg, ref)
            if kind == "floor_id":
                out: list[str] = []
                for area in ar_mod.async_entries_for_floor(ar_mod.async_get(hass), ref):
                    out.extend(_area_entities(ent_reg, area.id))
                return out
            if kind == "label_id":
                out = [
                    e.entity_id
                    for e in er_mod.async_entries_for_label(ent_reg, ref)
                    if e.disabled_by is None
                ]
                dev_reg = dr_mod.async_get(hass)
                for device in dr_mod.async_entries_for_label(dev_reg, ref):
                    out.extend(_device_entities(ent_reg, device.id))
                return out
        except Exception:  # fail-quiet per the docstring
            return []
        return []

    on_loop = _on_event_loop(hass, _expand_target)

    def _expand_target_from_any_thread(kind: str, ref: str) -> list[str]:
        try:
            return on_loop(kind, ref)
        except Exception:  # fail-quiet per the docstring, including a loop that is stopping
            return []

    return _expand_target_from_any_thread


def _build_get_state(hass: HomeAssistant) -> Callable[[str], str | None]:
    def _get_state(entity_id: str) -> str | None:
        state = hass.states.get(entity_id)
        return state.state if state is not None else None

    return _get_state


def _build_get_calendar_events(hass: HomeAssistant) -> Callable[[str], list[str]]:
    """Return a sync callback naming a calendar entity's current event, if any.

    Backs mesa-core's calendar_entity temporal condition, which holds while the
    list is non-empty. A calendar entity is "on" exactly while one of its events
    is running, so its cached state answers without a fetch. Anything but on or
    off raises, which mesa-core treats as unevaluable (fail-closed).
    """

    def _get_calendar_events(calendar_id: str) -> list[str]:
        state = hass.states.get(calendar_id)
        if state is None or state.state not in ("on", "off"):
            raise LookupError(f"{calendar_id} reports no usable state")
        return [str(state.attributes.get("message") or calendar_id)] if state.state == "on" else []

    return _get_calendar_events


def _build_get_solar_elevation(hass: HomeAssistant) -> Callable[[datetime], float | None]:
    """Return a sync callback computing the sun's elevation (degrees) at a time.

    Backs mesa-core's solar_angle temporal conditions. HA's cached astral Location
    (helpers.sun.get_astral_location) computes elevation at ANY instant, so
    solar_offset_minutes sampling in the past is exact, not approximated from the
    current sun.sun state. Any failure returns None, which mesa-core treats as
    unevaluable (fail-closed).
    """

    def _get_solar_elevation(at: datetime) -> float | None:
        try:
            location, elevation = sun_mod.get_astral_location(hass)
            return float(location.solar_elevation(at, elevation))
        except Exception:  # fail-closed per the mesa-core contract
            return None

    return _get_solar_elevation


def _build_on_lease_event(hass: HomeAssistant) -> Callable[[dict[str, Any]], None]:
    """Bridge mesa-core lease events onto the HA event bus as phoenix_mcp_mesa_lease_expired.

    mesa-core's tool handlers run LeaseManager methods on a worker thread, so the
    callback fires there; hass.add_job hops back to the event loop before touching
    the bus (async_fire is a @callback, loop-only).
    """

    def _on_lease_event(payload: dict[str, Any]) -> None:
        hass.add_job(hass.bus.async_fire, f"{DOMAIN}_mesa_lease_expired", dict(payload))

    return _on_lease_event


def async_track_lease_expiry(hass: HomeAssistant, runtime: MesaRuntime) -> Callable[[], None]:
    """Sweep run-out leases every MESA_LEASE_SWEEP_INTERVAL. Returns the cancel.

    mesa-core owns no event loop: a lease that runs out is removed, and its
    natural_expiry event fired, only when a lease operation sweeps, so without
    this an automation on phoenix_mcp_mesa_lease_expired would fire only when
    some key next requests or releases a lease. mesa-core asks hosts to call
    expire() periodically for exactly this. The sweep runs in the executor
    because a worker can hold the lease manager's lock while it waits for a
    registry lookup the event loop must answer. One sweep at a time; the entry
    owns the cancel and calls it at unload.
    """
    sweeping = False

    async def _sweep(_now: datetime) -> None:
        nonlocal sweeping
        if sweeping:
            return
        sweeping = True
        try:
            await hass.async_add_executor_job(runtime.lease_manager.expire)
        except Exception:  # leases are advisory; the next sweep retries
            _LOGGER.debug("Phoenix MCP MESA: lease expiry sweep failed", exc_info=True)
        finally:
            sweeping = False

    return async_track_time_interval(hass, _sweep, MESA_LEASE_SWEEP_INTERVAL)


async def async_end_leases(hass: HomeAssistant, runtime: MesaRuntime) -> None:
    """End every lease while unloading, so each still fires its ended event.

    Leases live only in this runtime, which a reload replaces, and an automation
    waiting on phoenix_mcp_mesa_lease_expired must not be left waiting. Client
    disconnects are no release point, since a lease belongs to its access key. Runs
    in the executor for the same reason as the sweep. Fails quiet: leases are
    advisory, and a failure here must not hold up the unload.
    """

    def _end_all() -> None:
        manager = runtime.lease_manager
        manager.expire()
        for session_id in {lease.session_id for lease in manager.active_leases()}:
            manager.release_session(session_id)

    try:
        await hass.async_add_executor_job(_end_all)
    except Exception:  # advisory cleanup never blocks an unload
        _LOGGER.debug("Phoenix MCP MESA: ending leases at unload failed", exc_info=True)


def _numeric_threshold_example(nt_spec: dict[str, Any]) -> dict[str, Any]:
    """Serialize a numeric_threshold selector into its VALID config value.

    A numeric_threshold selector (used by *_crossed_threshold triggers) does not
    serialize the way its selector shape suggests: the config value is
    {type: above|below, value: {number, unit_of_measurement}}, not a bare number.
    The unit lives either at the selector level as a list (temperature) or inside
    the number spec as a string (percentage); both are handled. type must be above
    or below (there is no bidirectional form). The number is a representative
    in-range placeholder for the caller to adapt.
    """
    number = nt_spec.get("number") or {}
    lo, hi = number.get("min"), number.get("max")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        example_number: float = round((lo + hi) / 2)
    elif isinstance(lo, (int, float)):
        example_number = round(lo)
    else:
        example_number = 20
    value: dict[str, Any] = {"number": example_number}
    unit_list = nt_spec.get("unit_of_measurement")
    if isinstance(unit_list, list) and unit_list:
        value["unit_of_measurement"] = unit_list[0]
    elif isinstance(number.get("unit_of_measurement"), str):
        value["unit_of_measurement"] = number["unit_of_measurement"]
    return {"type": "above", "value": value}


def _moment_example_config(
    moment_id: str, kind: str, entity_id: str, schema: dict[str, Any]
) -> dict[str, Any] | None:
    """A minimal, copy-adaptable VALID config for a semantic moment, or None.

    The moment's ``schema`` is the frontend FORM descriptor (selectors), not the
    config-YAML serialization, so a model handed only that cannot reliably author
    the trigger/condition. This emits a concrete example for the selector types
    whose serialization is known, and returns None (example omitted, never
    guessed) for anything else so a wrong example is never shipped. Fields with
    defaults (behavior, for) are omitted; only required parameterized fields are
    serialized. Triggers and conditions share the numeric_threshold serialization
    and differ only in the leading key.
    """
    options: dict[str, Any] = {}
    for field_name, field_spec in (schema.get("fields") or {}).items():
        selector = field_spec.get("selector") or {}
        if "numeric_threshold" in selector:
            options[field_name] = _numeric_threshold_example(selector["numeric_threshold"])
    if not options:
        return None
    key = "condition" if kind == "condition" else "trigger"
    return {key: moment_id, "target": {"entity_id": entity_id}, "options": options}


async def async_semantic_moments(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]] | None:
    """Live purpose-specific trigger/condition vocabulary for one entity, or None.

    Backs mesa-core's ``mesa_get_profile(include_semantic_moments=True)``.
    ``async_get_triggers_for_target``/``async_get_conditions_for_target`` resolve
    which apply to an entity from each integration's triggers.yaml/conditions.yaml.
    An integration that declares none yields a smaller or empty set, still a valid answer.

    HA-COUPLING POINT alongside ws_dispatch, the injector and mesa_suggestions:
    imports sit inside the function and ANY failure (including a moved or missing
    module) degrades to None, which mesa-core renders as the field being
    omitted. Re-verify on HA upgrades. Consumed live, never consulted by enforcement.

    Each moment carries an ``example`` or its ``schema``. The schema (HA's
    ``async_get_all_descriptions``) is the frontend FORM descriptor, which does NOT
    map to the config YAML an agent must author, so where the serialization is
    known (numeric_threshold) a valid example from ``_moment_example_config``
    replaces it. Those lookups are separate and best-effort: if the ids resolve but
    they fail, moments are returned without those keys.
    """
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.websocket_api.automation import (
            async_get_conditions_for_target,
            async_get_triggers_for_target,
        )

        target = {"entity_id": entity_id}
        triggers = await async_get_triggers_for_target(hass, target, False)
        conditions = await async_get_conditions_for_target(hass, target, False)
    except Exception:  # degrade to field-omitted, never break the tool
        _LOGGER.debug("semantic moments lookup failed for %s", entity_id, exc_info=True)
        return None

    trigger_schemas: dict[str, Any] = {}
    try:
        # Local: inside a guard for releases that lack it.
        from homeassistant.helpers.trigger import (
            async_get_all_descriptions as async_get_all_trigger_descriptions,
        )

        trigger_schemas = await async_get_all_trigger_descriptions(hass)
    except Exception:  # schema is best-effort; ids above still stand
        _LOGGER.debug("semantic moments trigger schema lookup failed for %s", entity_id, exc_info=True)

    condition_schemas: dict[str, Any] = {}
    try:
        # Local: inside a guard for releases that lack it.
        from homeassistant.helpers.condition import (
            async_get_all_descriptions as async_get_all_condition_descriptions,
        )

        condition_schemas = await async_get_all_condition_descriptions(hass)
    except Exception:  # schema is best-effort; ids above still stand
        _LOGGER.debug("semantic moments condition schema lookup failed for %s", entity_id, exc_info=True)

    moments: list[dict[str, Any]] = []
    for key in sorted(triggers):
        entry: dict[str, Any] = {"id": key, "kind": "trigger"}
        if schema := trigger_schemas.get(key):
            # A worked example supersedes the selector schema: the schema is the
            # frontend FORM descriptor and is not directly authorable, so when a
            # valid example can be emitted it replaces the schema, shrinking the
            # payload and avoiding two representations of each field. The schema
            # stays as the fallback when no example is available.
            if example := _moment_example_config(key, "trigger", entity_id, schema):
                entry["example"] = example
            else:
                entry["schema"] = schema
        moments.append(entry)
    for key in sorted(conditions):
        entry = {"id": key, "kind": "condition"}
        if schema := condition_schemas.get(key):
            if example := _moment_example_config(key, "condition", entity_id, schema):
                entry["example"] = example
            else:
                entry["schema"] = schema
        moments.append(entry)
    return moments


def read_automation_configs(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Read automations.yaml as a list of HA automation config dicts.

    Performs file I/O; run via hass.async_add_executor_job. Returns [] when the
    file is missing or malformed (the TriggerValidator tolerates an empty set).
    """
    path = hass.config.path(_AUTOMATION_YAML)
    if not os.path.isfile(path):
        return []
    try:
        data = _load_yaml(path)
    except Exception:  # a broken YAML file must not crash setup
        _LOGGER.warning("MESA: could not parse %s for trigger validation", path, exc_info=True)
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def build_caller_context(access_key: AccessKeyRecord, session_id: str) -> CallerContext:
    """Map a Phoenix MCP access key to a MESA CallerContext.

    Phoenix MCP has no per-user roles; the access key's persona is its only role-like
    attribute, so MESA access_roles rules are persona-granular. caller_id uses
    the access key id (stable across renames); the name is surfaced as display_name.
    """
    return CallerContext(
        caller_id=access_key.id,
        roles=[access_key.persona] if access_key.persona else [],
        is_authenticated=True,
        session_id=session_id,
        display_name=access_key.name,
    )


def _corrupt_copies(path: str) -> list[str]:
    """Copies Home Assistant set aside when a store could not be parsed. Touches the disk."""
    return glob.glob(f"{glob.escape(path)}.corrupt.*")


async def async_setup_mesa(hass: HomeAssistant, mesa_mode: str) -> MesaRuntime:
    """Construct the MESA runtime, loading any persisted profiles.

    Built even when the kill switch is on: the admin profile API must work
    regardless, and the enforcement gate is never reached when no client routes
    are registered.

    Raises MesaStorageCorruptError rather than starting without the stored
    policy, so the gates fail closed through the setup-failure path. Home
    Assistant moves an undecodable file aside and reports an empty store, so a
    corrupt copy with no file in its place keeps refusing across reloads until
    the operator restores the copy or deletes it.
    """
    ha_store: Store[dict] = DurableStore(hass, MESA_STORAGE_VERSION, MESA_STORAGE_KEY, atomic_writes=True)
    raw = await ha_store.async_load()
    if raw is None:
        corrupt = await hass.async_add_executor_job(_corrupt_copies, ha_store.path)
        if corrupt:
            raise MesaStorageCorruptError(
                f"MESA profile storage is corrupt. Copy {sorted(corrupt)[-1]} back to {ha_store.path}, "
                "or delete it to start with no profiles, then reload Phoenix MCP."
            )
        raw = {}
    profiles = raw.get("profiles", {}) if isinstance(raw, dict) else None
    if not isinstance(profiles, dict) or not all(isinstance(doc, dict) for doc in profiles.values()):
        raise MesaStorageCorruptError(
            f"MESA profile storage at {ha_store.path} has an invalid shape. "
            "Restore it from a backup, then reload Phoenix MCP."
        )
    backend = PhoenixMesaBackend(profiles)
    dismissed = {k for k in (raw.get("dismissed_suggestions") or []) if isinstance(k, str)}

    get_entity_area = _build_get_entity_area(hass)
    get_entity_integration = _build_get_entity_integration(hass)
    get_entity_device = _build_get_entity_device(hass)
    get_device_parent = _build_get_device_parent(hass)
    store = ProfileStore(
        backend=backend,
        get_entity_area=get_entity_area,
        get_entity_integration=get_entity_integration,
        get_entity_device=get_entity_device,
        get_device_parent=get_device_parent,
    )
    resolver = InheritanceResolver(
        store=store,
        get_entity_area=get_entity_area,
        get_entity_integration=get_entity_integration,
        get_entity_device=get_entity_device,
        get_device_parent=get_device_parent,
    )
    store.attach_resolver(resolver)
    # The enforcer resolves through whatever store it is handed, so it takes the
    # UNFILTERED one. Backing it with a per-access-key scoped view would turn reduced
    # visibility into reduced policy: every inheritance layer only ever tightens,
    # so a hidden layer yields a MORE permissive effective profile than the
    # operator authored, and nothing in a resolution marks a layer as missing.
    # Scoping belongs to the retrieval tools, which filter entities, never layers.
    enforcer = MesaEnforcer(
        store=store,
        resolver=resolver,
        mode="enforced" if mesa_mode == MESA_MODE_ENFORCED else "advisory",
        interactive=False,
        get_state=_build_get_state(hass),
        get_solar_elevation=_build_get_solar_elevation(hass),
        get_calendar_events=_build_get_calendar_events(hass),
    )
    validator = TriggerValidator(store=store, expand_target=build_expand_target(hass))
    lease_manager = LeaseManager(
        store=store,
        get_state=_build_get_state(hass),
        on_lease_event=_build_on_lease_event(hass),
    )

    return MesaRuntime(
        hass=hass,
        backend=backend,
        store=store,
        resolver=resolver,
        enforcer=enforcer,
        validator=validator,
        ha_store=ha_store,
        lease_manager=lease_manager,
        dismissed_suggestions=dismissed,
        durable_profiles=backend.snapshot(),
        durable_dismissed=sorted(dismissed),
    )


async def async_import_sidecar_profiles(hass: HomeAssistant, runtime: MesaRuntime) -> int:
    """Import developer mesa_profile.json sidecars from installed integrations.

    Domains that already carry an operator-authored (source: user) domain
    profile are skipped so restarts never clobber manual edits. Returns the
    number of domain profiles imported.
    """
    components_dir = hass.config.path("custom_components")

    def _scan() -> list[SemanticProfile]:
        found: list[SemanticProfile] = []
        if not os.path.isdir(components_dir):
            return found
        for name in sorted(os.listdir(components_dir)):
            path = os.path.join(components_dir, name)
            if not os.path.isdir(path):
                continue
            try:
                profile = import_from_integration(path)
            except MesaError:
                _LOGGER.warning("MESA: malformed sidecar in %s; skipping", name, exc_info=True)
                continue
            if profile is not None:
                found.append(profile)
        return found

    profiles = await hass.async_add_executor_job(_scan)
    imported = 0
    async with runtime.lock:
        for profile in profiles:
            existing = runtime.store.get_integration_profile(profile.entity_id)
            if existing is not None and existing.metadata.source.value == "user":
                continue
            runtime.store.set_integration_profile(profile.entity_id, profile)
            imported += 1
        if imported:
            await runtime.async_save()
    return imported


async def async_refresh_trigger_issues(hass: HomeAssistant, runtime: MesaRuntime) -> None:
    """Re-run the TriggerValidator and cache the results on the runtime.

    The validator checks the EFFECTIVE triggers_automations, so an entity can
    read as ``none`` by inheriting it from a domain, integration, or area
    profile while carrying no profile of its own. Such an entity is not in the
    store's key set, so it is only checkable when the host names it.

    ``entity_ids`` REPLACES the store's key set rather than extending it, so the
    candidates are the union: the live entities (registry plus states, mirroring
    refresh_orphans) and every entity carrying its own stored profile. Passing
    the live set alone would stop checking a profile whose entity is currently
    absent, which is exactly the stale declaration the check exists to catch.
    """
    configs = await hass.async_add_executor_job(read_automation_configs, hass)
    entity_ids = (
        set(er_mod.async_get(hass).entities)
        | set(hass.states.async_entity_ids())
        | set(runtime.store.entity_keys())
    )
    runtime.trigger_issues = await hass.async_add_executor_job(
        lambda: runtime.validator.validate(lambda: configs, entity_ids=entity_ids)
    )


def refresh_orphans(hass: HomeAssistant, runtime: MesaRuntime) -> None:
    """Recompute stored profiles whose target is gone.

    Four independent kinds: entity profiles whose entity left the registry (via the
    store's ``find_orphans``), device profiles whose device was removed, area
    profiles whose area was deleted, and integration profiles whose integration is
    neither producing entities nor loaded as a component. MESA never auto-deletes
    a profile (a temporary removal must not lose authored intent); this only
    surfaces them for manual cleanup. A registry rename does not leave an orphan:
    carry_renamed_profile moves the profile with the entity.

    The scoped kinds are computed here because ``find_orphans`` returns scoped
    orphans as their full reserved key (``__device__:abc``), which would change
    the shape every consumer reads. The lists stay bare and separate.
    """
    er = er_mod.async_get(hass)
    known = set(er.entities) | set(hass.states.async_entity_ids())
    runtime.orphans = runtime.store.find_orphans(known)

    known_devices = set(device_registry_snapshot(hass).by_id)
    runtime.orphan_devices = [d for d in runtime.store.device_keys() if d not in known_devices]

    known_areas = set(ar_mod.async_get(hass).areas)
    runtime.orphan_areas = [a for a in runtime.store.area_keys() if a not in known_areas]

    known_integrations = {e.platform for e in er.entities.values() if e.platform} | set(
        hass.config.components
    )
    runtime.orphan_integrations = [
        i for i in runtime.store.integration_keys() if i not in known_integrations
    ]


def _stored_control_rank(runtime: MesaRuntime, entity_id: str) -> int | None:
    """Restrictiveness of a stored entity profile's own mode, or None if unreadable."""
    try:
        profile = runtime.store.get(entity_id)
    except (MesaError, ValueError):
        return None
    mode = profile.operational_boundaries.control_mode if profile is not None else None
    return CONTROL_MODE_RANK[mode] if isinstance(mode, ControlMode) else -1


def carry_renamed_profile(runtime: MesaRuntime, old_entity_id: str, new_entity_id: str) -> bool:
    """Move an entity profile to the entity's new id. Returns whether it had one.

    Synchronous so the registry event moves it before any later call can address
    the new id; when this returns True the caller persists the result and refreshes
    the orphan list. Profiles are keyed by entity id, so without this the renamed
    entity loses its protection while the permission grant follows it.

    Home Assistant frees the new id before a rename, so a profile already stored
    there describes an entity that is gone or one that does not exist yet. Neither
    is deleted, and the stricter of the two governs the renamed entity: this
    exchange never loosens policy. The other stays under the old id, where it shows
    as an orphan for the operator to resolve. A mode that cannot be read counts as
    strictest, since its document may hold a restriction that cannot be compared.
    """
    if runtime.backend.read(old_entity_id) is None:
        return False
    if runtime.backend.read(new_entity_id) is not None:
        ranks = [_stored_control_rank(runtime, eid) for eid in (old_entity_id, new_entity_id)]
        _LOGGER.warning(
            "MESA: %s was renamed to %s, which already has a profile. Both are kept; "
            "the stricter applies to %s and the other is now an orphan to review.",
            old_entity_id, new_entity_id, new_entity_id,
        )
        if ranks[1] is None or (ranks[0] is not None and ranks[0] <= ranks[1]):
            return True
    runtime.backend.exchange(old_entity_id, new_entity_id)
    _exchange_documents(runtime.durable_profiles, old_entity_id, new_entity_id)
    return True


async def async_persist_renamed_profile(hass: HomeAssistant, runtime: MesaRuntime) -> None:
    """Save a carried profile. A failed write leaves the carried set in force."""
    async with runtime.lock:
        try:
            await runtime.async_save()
        except MesaSaveError:
            _LOGGER.error("MESA: a renamed entity's profile could not be saved; it holds until the next restart")
        refresh_orphans(hass, runtime)


def area_profile_exits(
    data: PhoenixData, moves: Mapping[str, str | None]
) -> list[str]:
    """The entities an area change would take out of a profiled area.

    ``moves`` maps each affected entity to its effective area after the change.
    Area is an input to policy, so a move out of a profiled area can lift a
    restriction, while a move into one only tightens: inheritance layers never
    loosen. Only a move that leaves a profile is therefore a MESA action, so an
    ordinary room change for an entity with a restrictive domain default stays
    unaffected. With the runtime unavailable every changed entity is returned, for
    the gate to refuse.
    """
    runtime = data.mesa
    if data.store.get_settings().mesa_mode == MESA_MODE_OFF:
        return []
    if runtime is None:
        return sorted(moves)
    get_area = _build_get_entity_area(runtime.hass)
    return sorted(
        entity_id
        for entity_id, after in moves.items()
        if (before := get_area(entity_id)) is not None
        and before != after
        and runtime.store.get_area_profile(before) is not None
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


@dataclass
class MesaVerdict:
    """Per-entity MESA outcome for one flattened service call.

    allowed: entities that may proceed. confirm: entities whose profile
    requires admin confirmation (enforced mode). blocked: (entity, rule, reason)
    for entities MESA refuses outright (read_only, prohibited, declared limit,
    privacy). warnings: advisory messages collected across all entities.
    """

    allowed: list[str] = field(default_factory=list)
    confirm: list[str] = field(default_factory=list)
    blocked: list[tuple[str, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RegistryMesaDecision:
    """MESA's fully resolved verdict for one entity-registry identity action."""

    decision: str  # "allow" | "confirm" | "deny"
    rule: str
    reason: str
    warnings: list[str] = field(default_factory=list)
    effective_rule: dict[str, Any] | None = None
    profile_fingerprint: str | None = None


def evaluate_registry_action(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    entity_id: str,
    *,
    action: str,
    registry_domain: str = "entity_registry",
    service_data: dict[str, Any] | None = None,
    session_id: str = "entity_registry",
) -> RegistryMesaDecision:
    """Evaluate rename/delete through the normal inheritance resolver and enforcer.

    The synthetic service name defaults to ``entity_registry.<action>`` and may
    be switched to another registry domain such as ``device_registry``. The
    enforcer still resolves the entity's exact
    entity/device/area/integration/domain layers, so a permissive entity profile
    cannot bypass a restrictive ancestor. The returned fingerprint covers the
    whole resolved explanation, not only the final control-mode string; an
    approval can therefore satisfy only the MESA state it actually previewed.

    Every caller is a real gate, so a refusal here reaches the MESA audit trail
    under the dispatching request. An executor resuming an approval has none and
    gets a request id of its own.
    """
    runtime = data.mesa
    settings = data.store.get_settings()
    if settings.mesa_mode == MESA_MODE_OFF:
        return RegistryMesaDecision(
            decision="allow",
            rule="mesa:off",
            reason="MESA is off.",
        )
    if runtime is None:
        return RegistryMesaDecision(
            decision="deny",
            rule="mesa:runtime_unavailable",
            reason="MESA is configured but unavailable. Reload Phoenix MCP after checking Needs Attention in the Phoenix panel.",
        )

    explanation = runtime.resolver.explain(entity_id)
    explanation_doc = explanation.to_dict()
    fingerprint = hashlib.sha256(
        json.dumps(
            explanation_doc,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    control = next(
        (
            item
            for item in explanation.explanation
            if item.field_path == "operational_boundaries.control_mode"
        ),
        None,
    )
    boundaries = explanation.effective_profile.operational_boundaries
    control_mode = getattr(boundaries.control_mode, "value", boundaries.control_mode)
    effective_rule = {
        "action": f"{registry_domain}.{action}",
        "control_mode": control_mode,
        "control_reason": boundaries.control_reason,
        "enforcement_mode": (
            MESA_MODE_ENFORCED
            if settings.mesa_mode == MESA_MODE_ENFORCED
            else boundaries.enforcement_mode
        ),
        "provided_by_level": control.provided_by_level if control is not None else None,
        "provided_by_origin": control.provided_by_origin if control is not None else None,
        "profile_fingerprint": fingerprint[:16],
    }
    with mesa_audit.request_context(
        access_key, dispatch_request_id.get() or generate_request_id(), dispatch_client_ip.get()
    ):
        verdict = evaluate_service_entities(
            runtime,
            settings.mesa_mode,
            access_key,
            [entity_id],
            domain=registry_domain,
            service=action,
            service_data=dict(service_data or {}),
            session_id=session_id,
        )
    reason = boundaries.control_reason or entity_id
    if verdict.confirm:
        return RegistryMesaDecision(
            decision="confirm",
            rule="control_mode:confirm",
            reason=f"{registry_domain} {action} requires confirmation: {reason}",
            warnings=list(verdict.warnings),
            effective_rule=effective_rule,
            profile_fingerprint=fingerprint,
        )
    if verdict.blocked:
        _blocked_entity, rule, blocked_reason = verdict.blocked[0]
        return RegistryMesaDecision(
            decision="deny",
            rule=rule,
            reason=blocked_reason,
            warnings=list(verdict.warnings),
            effective_rule=effective_rule,
            profile_fingerprint=fingerprint,
        )
    return RegistryMesaDecision(
        decision="allow",
        rule=f"control_mode:{control_mode}",
        reason=f"{registry_domain} {action} is permitted: {reason}",
        warnings=list(verdict.warnings),
        effective_rule=effective_rule,
        profile_fingerprint=fingerprint,
    )


def _host_enforced(settings_mode: str, result: Any) -> bool:
    """Recompute MesaEnforcer._is_enforced host-side from public result data."""
    if settings_mode == MESA_MODE_ENFORCED:
        return True
    boundaries = result.effective_profile.operational_boundaries
    return getattr(boundaries, "enforcement_mode", "advisory") == "enforced"


def evaluate_service_call(
    runtime: MesaRuntime,
    settings_mode: str,
    access_key: AccessKeyRecord,
    entities: list[str],
    *,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    session_id: str,
    confirm_approved: bool = False,
    service_data_by_entity: dict[str, dict[str, Any]] | None = None,
) -> MesaVerdict:
    """Evaluate a service call, looking through group helpers to the members it actuates.

    A group reaches its members, so a policy on a member governs the group: a
    blocked member blocks the group and a member needing confirmation sends the
    group to confirmation. The verdict names the group, never the member, which
    the access key may not be able to see.
    """
    verdict = evaluate_service_entities(
        runtime, settings_mode, access_key, entities, domain=domain, service=service,
        service_data=service_data, session_id=session_id, confirm_approved=confirm_approved,
        service_data_by_entity=service_data_by_entity,
    )
    for group in [*verdict.allowed, *verdict.confirm]:
        members = group_member_ids(group, runtime.hass)
        if members == frozenset():
            continue
        group_data = service_data_by_entity[group] if service_data_by_entity is not None else service_data
        member_verdict = None if members is None else evaluate_service_entities(
            runtime, settings_mode, access_key, sorted(members), domain=domain, service=service,
            service_data=group_data, session_id=session_id, confirm_approved=confirm_approved,
        )
        if member_verdict is None or member_verdict.blocked:
            rule = member_verdict.blocked[0][1] if member_verdict is not None else "control_mode:blocked"
            verdict.allowed = [entity_id for entity_id in verdict.allowed if entity_id != group]
            verdict.confirm = [entity_id for entity_id in verdict.confirm if entity_id != group]
            verdict.blocked.append((group, rule, f"A member of {group} is blocked by policy."))
        elif member_verdict.confirm and group in verdict.allowed:
            verdict.allowed.remove(group)
            verdict.confirm.append(group)
        if member_verdict is not None and member_verdict.warnings:
            verdict.warnings.append(f"{group}: a member of this group has an advisory policy restriction.")
    return verdict


def _finite_number(value: Any) -> float | None:
    """A numeric value as Home Assistant's own coercion reads it, else None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _light_default_brightness(hass: HomeAssistant, entity_id: str, params: dict[str, Any]) -> float | None:
    """The brightness Home Assistant's default light profile gives this call, if any.

    The profile store is asked the way the light service asks it, on a copy, so the
    rule for when a default applies stays Home Assistant's.
    """
    profiles = hass.data.get(LIGHT_DATA_PROFILES)
    if profiles is None:
        return None
    state = hass.states.get(entity_id)
    applied = dict(params)
    profiles.apply_default(entity_id, state is not None and state.state == "on", applied)
    return _finite_number(applied.get("brightness"))


def _light_limit_params(hass: HomeAssistant, entity_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Add the absolute brightness a light.turn_on call amounts to.

    mesa-core matches a declared limit on the exact parameter name, while Home
    Assistant accepts one brightness as brightness, brightness_pct or a step from
    the current level. Both absolute forms are added, so a limit on either sees
    the same value and the caller's own parameters stay as sent. A brightness that
    cannot be worked out, including one a named light profile supplies, is added
    as None: mesa-core refuses a value it cannot compare, and only for a limit on
    that parameter. A call naming no brightness measures the default light profile
    when one applies. Otherwise the light keeps or restores a level no state shows,
    and nothing is added.
    """
    scale = _LIGHT_FULL_BRIGHTNESS / 100
    if "brightness_step" in params or "brightness_step_pct" in params:
        state = hass.states.get(entity_id)
        current = _finite_number(state.attributes.get("brightness")) if state and state.state == "on" else None
        step = (
            _finite_number(params["brightness_step"]) if "brightness_step" in params
            else (pct * scale if (pct := _finite_number(params["brightness_step_pct"])) is not None else None)
        )
        brightness = None if step is None else min(max((current or 0) + step, 0), _LIGHT_FULL_BRIGHTNESS)
    elif "brightness_pct" in params:
        pct = _finite_number(params["brightness_pct"])
        brightness = None if pct is None else pct * scale
    elif "brightness" in params:
        brightness = _finite_number(params["brightness"])
    elif "profile" in params:
        brightness = None
    elif (brightness := _light_default_brightness(hass, entity_id, params)) is None:
        return params
    return {
        "brightness": brightness,
        "brightness_pct": None if brightness is None else brightness / scale,
        **params,
    }


def _fan_stepped_percentage(hass: HomeAssistant, entity_id: str, service: str, params: dict[str, Any]) -> float | None:
    """The percentage a fan speed step lands on, or None when it cannot be worked out.

    Follows FanEntity: an explicit step moves from the current percentage, and the
    default step moves one speed through the fan's speed range.
    """
    state = hass.states.get(entity_id)
    attributes = state.attributes if state is not None else {}
    current = _finite_number(attributes.get("percentage")) or 0
    modifier = 1 if service == "increase_speed" else -1
    if "percentage_step" in params:
        step = _finite_number(params["percentage_step"])
        return None if step is None else max(0, min(100, current + step * modifier))
    step = _finite_number(attributes.get("percentage_step"))
    if not step or step <= 0:
        return None
    speed_range = (1, round(100 / step))
    index = math.ceil(percentage_to_ranged_value(speed_range, current))
    return max(0, min(100, ranged_value_to_percentage(speed_range, index + modifier)))


def _fan_limit_forms(
    hass: HomeAssistant, entity_id: str, service: str, params: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """The percentage services a fan call amounts to.

    fan.turn_on and fan.set_percentage set one percentage, and a speed step ends
    on one, so a limit on either service measures all of them.
    """
    if service in _FAN_STEP_SERVICES:
        measured = {**params, "percentage": _fan_stepped_percentage(hass, entity_id, service, params)}
    elif service in _FAN_PERCENTAGE_SERVICES and "percentage" in params:
        measured = params
    else:
        return []
    return [(f"fan.{name}", measured) for name in _FAN_PERCENTAGE_SERVICES if name != service]


def _climate_limit_forms(params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The setpoints of a climate.set_temperature call under each other's names.

    A call sets one temperature or a low and high range. Each end of a range meets
    a limit on temperature, and a single temperature meets a limit on either end.
    """
    service = "climate.set_temperature"
    forms = [
        (service, {**params, "temperature": params[end]})
        for end in _CLIMATE_RANGE_ENDS if end in params
    ]
    if "temperature" in params:
        forms.append((service, {**dict.fromkeys(_CLIMATE_RANGE_ENDS, params["temperature"]), **params}))
    return forms


def _limit_equivalents(
    runtime: MesaRuntime, entity_id: str, domain: str, service: str, params: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """The calls a service call amounts to, which a declared limit also measures.

    mesa-core compares a limit with the exact service and parameter named. A
    generic homeassistant turn service runs as the same service on the entity's
    own domain with its data forwarded, a toggle with data turns an off entity on
    with that data, and a light, a fan and a thermostat each take one value under
    several names or services. Each form must meet the same limit. A toggle with
    no data on a light that is on only turns it off, so it measures nothing.
    """
    own_domain = domain
    if domain == "homeassistant" and service in _GENERIC_TURN_SERVICES:
        own_domain = entity_id.partition(".")[0]
    services = [f"{own_domain}.{service}"]
    measured = params
    if own_domain == "light" and service in ("turn_on", "toggle"):
        state = runtime.hass.states.get(entity_id)
        if service == "turn_on" or params or state is None or state.state != "on":
            measured = _light_limit_params(runtime.hass, entity_id, params)
    if service == "toggle" and measured:
        services.append(f"{own_domain}.turn_on")
    exact = f"{domain}.{service}"
    forms = [
        (name, measured) for name in services
        if name != exact or measured is not params
    ]
    if own_domain == "fan":
        forms.extend(_fan_limit_forms(runtime.hass, entity_id, "turn_on" if service == "toggle" else service, params))
    elif own_domain == "climate" and service == "set_temperature":
        forms.extend(_climate_limit_forms(params))
    return forms


def _carries_limits(result: Any) -> bool:
    """Whether a resolved profile holds a limit, directly or in a temporal constraint."""
    boundaries = result.effective_profile.operational_boundaries
    return bool(boundaries.declared_limits or boundaries.temporal_constraints)


def evaluate_service_entities(
    runtime: MesaRuntime,
    settings_mode: str,
    access_key: AccessKeyRecord,
    entities: list[str],
    *,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    session_id: str,
    confirm_approved: bool = False,
    service_data_by_entity: dict[str, dict[str, Any]] | None = None,
) -> MesaVerdict:
    """Evaluate every flattened entity through the MesaEnforcer.

    The public evaluated mode includes temporal and privacy adjustments from
    this call. Phoenix always vetoes read-only writes. Non-interactive Confirm
    blocks enter the admin gate; advisory Confirm allows with a host warning.
    Approval satisfies only confirmation, never another restriction. Warnings
    and reasons can name an entity other than the target, such as a limit's
    predicate entity, so what the key cannot read is redacted here, for every
    gate and preview alike.
    """
    verdict = MesaVerdict()
    caller = build_caller_context(access_key, session_id)
    service_str = f"{domain}.{service}"
    now = dt_util.now()
    for entity_id in entities:
        entity_service_data = (
            service_data_by_entity[entity_id]
            if service_data_by_entity is not None
            else service_data
        )
        # The parameters this call will execute, not a filtered view of them: a
        # decision made about a different payload than the one sent is the
        # divergence the target check exists to catch, and the audit record would
        # describe a call that never happened. service_data has already had its
        # target selectors CONSUMED by target resolution, so they are absent from
        # the outgoing call too. The one exception is a non-mapping target, which
        # is a data field such as a notification recipient, selects nothing in
        # hass.services.async_call, and has no entity for mesa-core to compare.
        # The resolved entity goes last so no caller value can displace it.
        service_params = {
            key: value for key, value in entity_service_data.items()
            if key != "target" or isinstance(value, Mapping)
        }
        result = runtime.enforcer.evaluate(
            entity_id=entity_id,
            service=service_str,
            service_params={**service_params, "entity_id": entity_id},
            caller_context=caller,
            current_time=now,
        )
        # Equivalent forms decide nothing but limits, so a profile without one
        # is judged by the call alone.
        for equivalent_service, equivalent_params in _limit_equivalents(
            runtime, entity_id, domain, service, service_params
        ):
            if not result.allowed or not _carries_limits(result):
                break
            equivalent = runtime.enforcer.evaluate(
                entity_id=entity_id,
                service=equivalent_service,
                service_params={**equivalent_params, "entity_id": entity_id},
                caller_context=caller,
                current_time=now,
            )
            if not equivalent.allowed and (equivalent.rule_applied or "").startswith("declared_limit:"):
                result = equivalent
            else:
                verdict.warnings.extend(w for w in equivalent.warnings if w not in result.warnings)
        mode = result.evaluated_control_mode
        rule = result.rule_applied or "control_mode:blocked"
        confirmation = mode is ControlMode.CONFIRM and (
            result.allowed or rule == "control_mode:confirm_no_channel"
        )
        # Phoenix supplies the human approval channel. Replace only the core's
        # no-channel explanation, retaining independent policy/limit warnings.
        verdict.warnings.extend(
            warning for warning in result.warnings
            if not (confirmation and warning == "no interaction channel: confirm is treated as prohibited")
        )
        if mode is ControlMode.READ_ONLY and result.allowed:
            reason = f"Entity is read-only by nature: {entity_id}"
            mesa_audit.record_read_only_denial(entity_id, service_str, caller, result)
            verdict.blocked.append((entity_id, "control_mode:read_only", reason))
            continue
        if result.allowed and mode is None:
            verdict.blocked.append((entity_id, "policy_unavailable", "MESA returned no evaluated control mode."))
            continue
        if result.rule_applied == "policy_unavailable":
            mesa_audit.record_policy_failure(entity_id, service_str, caller, result)
        if confirmation:
            if confirm_approved or not _host_enforced(settings_mode, result):
                verdict.allowed.append(entity_id)
                if not confirm_approved:
                    verdict.warnings.append(
                        f"{entity_id}: this action is marked 'confirm' by MESA; "
                        "proceeding because MESA is in advisory mode (it would require admin "
                        "approval under enforced mode)."
                    )
            else:
                verdict.confirm.append(entity_id)
            continue
        if result.allowed:
            verdict.allowed.append(entity_id)
            continue
        if rule == _RULE_CONTRADICTORY_TARGET:
            # The caller sees the uniform refusal and the MESA audit row carries
            # the conflict. A caller chooses these parameters, so this stays below
            # the levels an operator watches.
            _LOGGER.debug("MESA refused %s on %s: %s", service_str, entity_id, result.reason)
        verdict.blocked.append((entity_id, rule, result.reason))
    if verdict.warnings or verdict.blocked:
        hass = runtime.hass
        verdict.warnings = list(dict.fromkeys(
            redact_inaccessible_entity_ids_in_text(warning, access_key, hass) for warning in verdict.warnings
        ))
        verdict.blocked = [
            (entity_id, rule, redact_inaccessible_entity_ids_in_text(reason, access_key, hass))
            for entity_id, rule, reason in verdict.blocked
        ]
    return verdict


def entity_control_mode(
    runtime: MesaRuntime | None, access_key: AccessKeyRecord, entity_id: str, session_id: str = "phoenix_mcp"
) -> str | None:
    """Best-effort effective MESA control_mode for one entity (advisory display).

    control_mode is an entity-level property, so the representative service used
    to drive the evaluation does not change it. The mode is the one this
    evaluation would act on, after an active temporal window and the restricted
    privacy level have tightened it, as the gate reads it: the inherited mode
    alone can read Autonomous for an entity a write would route to Confirm. The
    inherited mode is the fallback only when mesa-core reports no evaluated
    mode. Returns the control_mode string
    (autonomous, confirm, read_only, prohibited) or None if it cannot be
    determined. Callers only pass entities the access key can already access, so this
    is not an enumeration oracle.

    runtime is None when MESA never started (callers pass data.mesa), which
    returns None rather than pushing the check to every caller.
    """
    if runtime is None:
        return None
    try:
        result = runtime.enforcer.evaluate(
            entity_id=entity_id,
            service=f"{entity_id.split('.')[0]}.turn_on",
            service_params={"entity_id": entity_id},
            caller_context=build_caller_context(access_key, session_id),
            current_time=dt_util.now(),
        )
        cm = result.evaluated_control_mode
        if cm is None:
            cm = result.effective_profile.operational_boundaries.control_mode
        return getattr(cm, "value", cm) if cm is not None else None
    except Exception:  # advisory display must never break the caller
        return None


def mesa_confirm_preview(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    *,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    entities: list[str],
) -> list[str]:
    """Return the entities MESA would route to admin confirmation.

    Read-only companion to async_apply_mesa_to_call for the capability-gate
    approval diff builders: the admin's approval of a gated call also satisfies
    MESA's confirm (async_execute_approved_tool runs confirm-approved), so the
    approval record names the entities that confirmation covers. Best-effort by
    contract: returns [] when MESA is off or absent, and on ANY failure, because
    an approval diff must never fail to build over an annotation.
    """
    try:
        runtime = data.mesa
        settings = data.store.get_settings()
        if runtime is None or settings.mesa_mode == MESA_MODE_OFF or not entities:
            return []
        verdict = evaluate_service_call(
            runtime, settings.mesa_mode, access_key, list(entities),
            domain=domain, service=service, service_data=service_data,
            session_id="approval_diff",
        )
        return list(verdict.confirm)
    except Exception:  # advisory annotation only
        return []


def build_mesa_service_diff(
    domain: str,
    service: str,
    service_data: dict[str, Any],
    verdict: MesaVerdict,
    service_data_by_entity: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a service_preview diff for a MESA-gated approval (admin review UI)."""
    preview: dict[str, Any] = {
        "domain": domain,
        "service": service,
        "resolved_entity_ids": list(verdict.confirm + verdict.allowed),
        "service_data": dict(service_data),
        "mesa": {
            "confirm_entities": list(verdict.confirm),
            "allowed_entities": list(verdict.allowed),
            "blocked": [
                {"entity_id": e, "rule": r, "reason": reason}
                for e, r, reason in verdict.blocked
            ],
            "warnings": list(verdict.warnings),
        },
    }
    if service_data_by_entity is not None:
        preview["service_data_by_entity"] = {
            entity_id: dict(service_data_by_entity[entity_id])
            for entity_id in verdict.confirm + verdict.allowed
        }
    return {
        "kind": "service_preview",
        **diff_summary_fields("mesa_service", domain=domain, service=service),
        "target": {"type": "service", "id": f"{domain}.{service}", "label": f"{domain}.{service}"},
        "preview": preview,
    }


def _mesa_call_args(
    domain: str, service: str, service_data: dict[str, Any], entities: list[str]
) -> dict[str, Any]:
    """Saved-args payload for a MESA approval, re-runnable by the executor.

    Saves the explicit flattened entity list (the confirm + already-allowed
    entities) rather than the original area/name targets, so re-execution fires
    on exactly what was reviewed. The executor re-resolves scope per entity and
    re-runs MESA under confirm-approved semantics; entities that became
    prohibited or read_only since the request are still rejected.
    """
    return {
        "domain": domain,
        "service": service,
        "service_data": dict(service_data),
        "entity_id": list(entities),
    }


@dataclass
class MesaGateOutcome:
    """Result of applying the MESA gate to one service call."""

    decision: str  # "allow" | "deny" | "pending"
    entities: list[str] = field(default_factory=list)
    approval: PendingApproval | None = None
    blocked: list[tuple[str, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


UNRESOLVED_CONTEXT_WARNING = (
    "MESA could not check this because it has no registry entity to resolve a policy through; "
    "proceeding because MESA is in advisory mode."
)


def unresolved_context_refused(data: PhoenixData, device_id: str | None = None) -> bool:
    """Whether a target with no registry entity must be refused rather than warned about.

    mesa-core resolves policy through an entity, so such a target cannot be judged.
    Enforced mode and a failed runtime refuse what they cannot judge. Advisory warns,
    as it does for everything else, unless the operator stored a profile for this
    very device: that is a known policy, and it cannot be evaluated here.
    """
    runtime = data.mesa
    if runtime is None or data.store.get_settings().mesa_mode != MESA_MODE_ADVISORY:
        return True
    return device_id is not None and device_id in runtime.store.device_keys()


async def async_apply_mesa_to_call(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    *,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    entities: list[str],
    request_id: str,
    client_ip: str | None,
    session_id: str,
    confirm_approved: bool = False,
    approval_tool_name: str = MESA_APPROVED_EXECUTOR,
    approval_args: dict[str, Any] | None = None,
    approval_diff: dict[str, Any] | Callable[[], Any] | None = None,
    require_all: bool = False,
    service_data_by_entity: dict[str, dict[str, Any]] | None = None,
) -> MesaGateOutcome:
    """Apply MESA enforcement to an already-flattened, Phoenix-permitted entity list.

    Returns an outcome the caller maps to its own response shape:
    - allow: proceed with ``outcome.entities`` (a subset of the input).
    - deny: every entity was blocked; caller returns tool_common.MESA_DENIED_MESSAGE or its own MESA refusal.
    - pending: at least one entity needs confirmation; an approval was created.

    ``mesa_mode == off`` short-circuits to allow-all. A production setup failure
    fails closed while advisory or enforced mode remains configured. The
    optional approval fields let another exact-action executor reuse this gate;
    ``require_all`` turns the normal safe subset behavior into all-or-nothing.
    ``service_data_by_entity`` describes one logical operation whose exact
    service parameters differ per target. ``approval_diff`` may be a zero-arg
    builder, sync or async, read only when an approval is created.
    """
    runtime = data.mesa
    settings = data.store.get_settings()
    if settings.mesa_mode == MESA_MODE_OFF:
        return MesaGateOutcome(decision="allow", entities=list(entities))
    if runtime is None:
        reason = "MESA is configured but unavailable. Reload Phoenix MCP after checking Needs Attention in the Phoenix panel."
        return MesaGateOutcome(
            decision="deny",
            blocked=[(entity_id, "mesa:runtime_unavailable", reason) for entity_id in entities],
        )

    # Only this path sets the audit context: the same enforcer runs from
    # dry_run_service / find_available_actions to PREVIEW a verdict, and a
    # preview is not a denial worth a row in the operator's log.
    with mesa_audit.request_context(access_key, request_id, client_ip):
        verdict = evaluate_service_call(
            runtime,
            settings.mesa_mode,
            access_key,
            entities,
            domain=domain,
            service=service,
            service_data=service_data,
            session_id=session_id,
            confirm_approved=confirm_approved,
            service_data_by_entity=service_data_by_entity,
        )

    if require_all and verdict.blocked:
        return MesaGateOutcome(
            decision="deny", blocked=verdict.blocked, warnings=verdict.warnings
        )

    if verdict.confirm and not confirm_approved:
        mesa_diff = build_mesa_service_diff(
            domain,
            service,
            service_data,
            verdict,
            service_data_by_entity=service_data_by_entity,
        )
        supplied_diff: Any = approval_diff() if callable(approval_diff) else approval_diff
        if inspect.isawaitable(supplied_diff):
            supplied_diff = await supplied_diff
        if supplied_diff is None:
            diff = mesa_diff
        else:
            diff = dict(supplied_diff)
            preview = dict(diff.get("preview") or {})
            preview["mesa"] = mesa_diff["preview"]["mesa"]
            diff["preview"] = preview
        saved_args = (dict(approval_args) if approval_args is not None else _mesa_call_args(
            domain, service, service_data, verdict.confirm + verdict.allowed))
        if approval_tool_name in (MESA_APPROVED_EXECUTOR, "HassSetVolumeRelative"):
            if APPROVAL_TARGETS_KEY not in saved_args:
                saved_args = bind_targets(saved_args, verdict.confirm + verdict.allowed, hass)
            diff = bound_preview(diff, saved_args)
        approval = await async_create_mesa_approval(
            hass,
            data,
            access_key,
            args=saved_args,
            diff=diff,
            request_id=request_id,
            client_ip=client_ip,
            tool_name=approval_tool_name,
        )
        return MesaGateOutcome(
            decision="pending",
            approval=approval,
            blocked=verdict.blocked,
            warnings=verdict.warnings,
        )

    if not verdict.allowed:
        return MesaGateOutcome(
            decision="deny", blocked=verdict.blocked, warnings=verdict.warnings
        )

    # Known gap (low severity): warnings flow to the access key via mesa_advisory /
    # native speech regardless of cap_config_read, so an advisory warning reveals an
    # in-scope entity's control_mode even to an access key that lacks profile-read access.
    # Not an enumeration oracle (the access key targeted an entity it already has WRITE on).
    # Hiding it would mean gating warnings on cap_config_read at the surfacing sites.
    mesa_audit.note_mesa_advisory(verdict.warnings)
    return MesaGateOutcome(
        decision="allow",
        entities=verdict.allowed,
        blocked=verdict.blocked,
        warnings=verdict.warnings,
    )


async def async_create_mesa_approval(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    *,
    args: dict[str, Any],
    diff: dict[str, Any],
    request_id: str,
    client_ip: str | None,
    tool_name: str = MESA_APPROVED_EXECUTOR,
) -> PendingApproval:
    """Create a PendingApproval for a MESA confirm, mirroring async_evaluate_capability.

    The record carries the MESA sentinel cap (skips the effective_cap recheck on
    approve). The default non-dispatchable executor re-runs a service call under
    MESA confirm-approved semantics; bounded domain tools may supply their own
    registered exact-action executor.
    """


    async with data.store.async_lock:
        # As the capability and registry paths do: a request whose key, runtime or
        # policy changed while it waited queues nothing.
        check_request_authority()
        approval = await async_create_pending_approval(
            data.store,
            access_key_id=access_key.id,
            access_key_name=access_key.name,
            tool_name=tool_name,
            cap_name=MESA_CONFIRM_CAP,
            args=args,
            diff=diff,
            request_id=request_id,
            client_ip=client_ip,
        )
    create_approval_notification(hass, approval)
    fire_approval_requested_event(hass, approval)
    return approval


def fire_mesa_blocked_event(
    hass: HomeAssistant, access_key: AccessKeyRecord, blocked: list[tuple[str, str, str]]
) -> None:
    """Fire phoenix_mcp_mesa_blocked for each entity MESA refused (automation hooks)."""

    for entity_id, rule, _reason in blocked:
        hass.bus.async_fire(
            f"{DOMAIN}_mesa_blocked",
            {
                "access_key_id": access_key.id,
                "access_key_name": access_key.name,
                "entity_id": entity_id,
                "rule_applied": rule,
                "timestamp": dt_util.utcnow().isoformat(),
            },
        )


def release_access_key_leases(data: Any, access_key_id: str) -> int:
    """Drop any advisory leases an access key still holds. Returns how many.

    Called when an access key is revoked or expires. Bookkeeping, not a safety
    mechanism: what stops a dead access key is authentication refusing its next call. A
    lease it left behind blocks other sessions for at most
    MAX_LEASE_DURATION_SECONDS, after which mesa-core drops it during the next
    request's own sweep, so this only shortens that window.

    Leases are keyed by session and Phoenix runs the lease tools with the access key id
    as the session, so releasing the access key's session releases exactly its own.
    Fails quiet: leases are in-memory and advisory, so a failure here must never
    interrupt a revocation.
    """
    runtime = getattr(data, "mesa", None)
    if runtime is None:
        return 0
    try:
        return runtime.lease_manager.release_session(access_key_id)
    except Exception:  # advisory cleanup never blocks a revocation
        _LOGGER.debug("Phoenix MCP: releasing leases for access key %s failed", access_key_id, exc_info=True)
        return 0
