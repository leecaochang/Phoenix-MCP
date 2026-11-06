"""MESA semantic-safety runtime for Phoenix MCP.

Wires the vendored mesa-core into the integration. Access keys decide who may touch what; MESA
decides what is safe to touch. Targets are flattened first, then MESA evaluates the explicit
entity list, and it binds pass-through keys too.

Enforcement runs synchronously on the loop over an in-memory profile store persisted through
HA's Store. The enforcer is non-interactive, so a Confirm entity is blocked with rule
``control_mode:confirm_no_channel`` and Phoenix maps that to a warning or its approval gate.
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
from .catalog_text import diff_summary_fields
from .redaction import redact_inaccessible_entity_ids_in_text
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
from .service_targets import todo_items_in_review

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
        """Persist the profile set; callers hold ``self.lock``.

        A failed write restores the last durable set in memory, then raises MesaSaveError.
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
        """Delete every profile and reset all Phoenix-side MESA caches, then persist.

        Callers hold ``self.lock``. Covers all profile levels and dismissed suggestions.
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
        """Update the enforcer's mode; ``off`` maps to advisory as it never consults it."""
        self.enforcer.mode = "enforced" if mesa_mode == MESA_MODE_ENFORCED else "advisory"


_T = TypeVar("_T")


def _on_event_loop(hass: HomeAssistant, callback: Callable[..., _T]) -> Callable[..., _T]:
    """Run a registry-walking host callback on the event loop.

    Callable from mesa-core's worker threads; registries change only on the loop, and on the
    loop itself the callback runs directly.
    """

    def _call(*args: Any) -> _T:
        if threading.get_ident() == hass.loop_thread_id:
            return callback(*args)
        return run_callback_threadsafe(hass.loop, callback, *args).result(MESA_HOST_CALLBACK_TIMEOUT_SECONDS)

    return _call


def _build_get_entity_area(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping entity_id to its effective area_id.

    The entity's own area, else its device's effective area. Runs per entity, on the loop.
    """

    def _get_entity_area(entity_id: str) -> str | None:
        entry = er_mod.async_get(hass).async_get(entity_id)
        if entry is None:
            return None
        return entry.area_id or device_effective_area_id(hass, entry.device_id)

    return _on_event_loop(hass, _get_entity_area)


def _build_get_entity_device(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping entity_id to the device that owns it.

    Backs the MESA device level. The lookup is unconditional, and no device is ever guessed.
    """

    def _get_entity_device(entity_id: str) -> str | None:
        entry = er_mod.async_get(hass).async_get(entity_id)
        return entry.device_id if entry is not None else None

    return _get_entity_device


def _build_get_device_parent(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping a device to the main device it is part of.

    Backs the MESA parent_device level. Only parent_device_id counts, not a via_device hub
    link. Malformed ancestry reports no parent.
    """

    def _get_device_parent(device_id: str) -> str | None:
        chain = device_authority_chain(hass, device_id)
        return chain[1] if chain is not None and len(chain) == 2 else None

    return _get_device_parent


def _build_get_entity_integration(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping entity_id to the integration that created it.

    Backs the MESA integration level: the registry ``platform`` is the sidecar's key.
    """

    def _get_entity_integration(entity_id: str) -> str | None:
        entry = er_mod.async_get(hass).async_get(entity_id)
        return entry.platform if entry is not None else None

    return _get_entity_integration


def build_expand_target(hass: HomeAssistant) -> Callable[[str, str], list[str]]:
    """Return the host callback resolving indirect automation targets to entity ids.
    Expands device, area, floor and label selectors for mesa-core's trigger walk. Inclusive,
    unlike service-target expansion: only disabled entries are left out. Any failure returns
    [].
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

    Backs the calendar_entity condition. A state other than on or off raises, so mesa-core
    treats it as unevaluable and fails closed.
    """

    def _get_calendar_events(calendar_id: str) -> list[str]:
        state = hass.states.get(calendar_id)
        if state is None or state.state not in ("on", "off"):
            raise LookupError(f"{calendar_id} reports no usable state")
        return [str(state.attributes.get("message") or calendar_id)] if state.state == "on" else []

    return _get_calendar_events


def _build_get_solar_elevation(hass: HomeAssistant) -> Callable[[datetime], float | None]:
    """Return a sync callback computing the sun's elevation (degrees) at a time.

    Backs solar_angle conditions, exact at any instant. Any failure returns None, which
    mesa-core treats as unevaluable and fails closed.
    """

    def _get_solar_elevation(at: datetime) -> float | None:
        try:
            location, elevation = sun_mod.get_astral_location(hass)
            return float(location.solar_elevation(at, elevation))
        except Exception:  # fail-closed per the mesa-core contract
            return None

    return _get_solar_elevation


def _build_on_lease_event(hass: HomeAssistant) -> Callable[[dict[str, Any]], None]:
    """Bridge lease events onto the bus as phoenix_mcp_mesa_lease_expired; thread-safe."""

    def _on_lease_event(payload: dict[str, Any]) -> None:
        hass.add_job(hass.bus.async_fire, f"{DOMAIN}_mesa_lease_expired", dict(payload))

    return _on_lease_event


def async_track_lease_expiry(hass: HomeAssistant, runtime: MesaRuntime) -> Callable[[], None]:
    """Sweep run-out leases every MESA_LEASE_SWEEP_INTERVAL. Returns the cancel.

    mesa-core owns no event loop, so expiry events need this periodic expire() call. The sweep
    runs in the executor, one at a time; the entry cancels it at unload.
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

    Runs in the executor, like the sweep. Fails quiet: a failure must not hold up the unload.
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
    """Serialize a numeric_threshold selector into its valid config value.

    Returns {type: above|below, value: {number, unit_of_measurement}}. The number is a
    representative in-range placeholder.
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
    """Return a minimal valid config for a semantic moment, or None.

    None means the selector's serialization is unknown: an example is omitted, never guessed.
    Only required parameterized fields are serialized.
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
    """Return the purpose-specific trigger and condition vocabulary for one entity, or None.
    Backs ``mesa_get_profile(include_semantic_moments=True)``; enforcement never reads it. Any
    failure, including a moved HA module, degrades to None. Each moment carries an ``example``
    where the serialization is known, else its form ``schema``; those lookups are best-effort.
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

    The key's persona is its only role-like attribute; caller_id is the rename-stable key id.
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
    Built even with the kill switch on, for the admin profile API. Raises
    MesaStorageCorruptError rather than starting without the stored policy; the gates then
    fail closed through the setup-failure path.
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
    """Import developer mesa_profile.json sidecars; return how many domain profiles landed.

    Domains with an operator-authored (source: user) profile are skipped.
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

    Names live entities plus every entity with a stored profile, as ``entity_ids`` replaces
    the store's key set and an inherited ``none`` is otherwise uncheckable.
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

    Covers entity, device, area and integration profiles, in bare separate lists. Never
    deletes a profile; it only surfaces them for manual cleanup.
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
    Synchronous, so it wins over any later call; on True the caller persists and refreshes
    orphans. A profile already at the new id is kept: the stricter of the two governs and the
    other stays under the old id as an orphan.
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
    """Return the entities an area change would take out of a profiled area.

    ``moves`` maps each affected entity to its effective area after the change. Only an exit
    can lift a restriction. With the runtime unavailable every changed entity is returned.
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

    ``allowed`` may proceed; ``confirm`` needs admin confirmation; ``blocked`` holds (entity,
    rule, reason) for outright refusals; ``warnings`` are collected advisories.
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
    """Evaluate rename or delete through the normal inheritance resolver and enforcer.
    The service name defaults to ``entity_registry.<action>``. The returned fingerprint covers
    the whole resolved explanation, so an approval satisfies only the state it previewed.
    Refusals reach the MESA audit trail, under a fresh request id on an approval resume.
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

    A blocked or Confirm member governs the group. The verdict names the group, never the
    member, which the key may not be able to see.
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
    Adds both brightness and brightness_pct, leaving the caller's parameters as sent. An
    uncomputable value is added as None, which mesa-core refuses for a limit on it. A call
    naming no brightness measures the default light profile, if any.
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
    """Return the calls a service call amounts to, which a declared limit also measures.

    Covers generic homeassistant turn services, a toggle with data, and light, fan and
    thermostat aliases. A dataless toggle on a light that is on measures nothing.
    """
    own_domain = domain
    if domain == "homeassistant" and service in _GENERIC_TURN_SERVICES:
        own_domain = entity_id.partition(".")[0]
    services = [f"{own_domain}.{service}"]
    measured = params
    turns_on = False
    if service == "toggle":
        state = runtime.hass.states.get(entity_id)
        turns_on = state is None or state.state != "on"
    if own_domain == "light" and (service == "turn_on" or (service == "toggle" and (params or turns_on))):
        measured = _light_limit_params(runtime.hass, entity_id, params)
    # A toggle that turns the entity on is that turn_on, so a limit refusing an
    # absent parameter must see it even when the toggle carries no data.
    if service == "toggle" and (measured or turns_on):
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
    Phoenix vetoes read-only writes. Confirm enters the admin gate, or warns in advisory mode.
    Approval satisfies only confirmation. Text naming an entity the key cannot read is
    redacted here, for every gate and preview.
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
        # the outgoing call too. A non-mapping target is a data field such as a
        # notification recipient, so it stays in for declared limits to measure.
        # The resolved entity goes last so no caller value can displace it.
        service_params = dict(entity_service_data)
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
    """Return the effective MESA control_mode for one entity, for advisory display.
    Returns autonomous, confirm, read_only, prohibited, or None when undeterminable or when
    the runtime is None. Reads the mode the gate would act on, after temporal and privacy
    tightening. Callers pass only entities the key can access.
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

    Read-only annotation for capability-approval diffs, whose approval also satisfies MESA's
    confirm. Returns [] when MESA is off or absent and on any failure.
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
    todo_items: list[dict[str, Any]] | None = None,
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
    if todo_items:
        preview["todo_items"] = todo_items
    return {
        "kind": "service_preview",
        **diff_summary_fields("mesa_service", domain=domain, service=service),
        "target": {"type": "service", "id": f"{domain}.{service}", "label": f"{domain}.{service}"},
        "preview": preview,
    }


def _mesa_call_args(
    domain: str, service: str, service_data: dict[str, Any], entities: list[str]
) -> dict[str, Any]:
    """Return the saved args for a MESA approval, re-runnable by the executor.

    Saves the explicit flattened entity list, so re-execution fires on what was reviewed.
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
    """Return whether a target with no registry entity must be refused, not warned about.
    Enforced mode and a failed runtime refuse; advisory refuses only a device with its own
    profile.
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
    - allow: proceed with ``outcome.entities``, a subset of the input.
    - deny: every entity was blocked.
    - pending: at least one entity needs confirmation; an approval was created.
    ``off`` allows all; a setup failure fails closed while a mode is configured.
    ``require_all`` makes it all-or-nothing, ``service_data_by_entity`` carries per-target
    parameters, and ``approval_diff`` may be a zero-arg builder, read only when an approval is
    created.
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
            todo_items=todo_items_in_review(
                hass, domain, service, service_data, verdict.confirm + verdict.allowed),
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
    """Create a PendingApproval for a MESA confirm.

    Carries the MESA sentinel cap. The default executor re-runs a service call under
    confirm-approved semantics; bounded tools may supply their own exact-action executor.
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
    """Drop any advisory leases a revoked or expired key holds. Returns how many.

    Bookkeeping only; authentication is what stops the key. Fails quiet, so a failure never
    interrupts a revocation.
    """
    runtime = getattr(data, "mesa", None)
    if runtime is None:
        return 0
    try:
        return runtime.lease_manager.release_session(access_key_id)
    except Exception:  # advisory cleanup never blocks a revocation
        _LOGGER.debug("Phoenix MCP: releasing leases for access key %s failed", access_key_id, exc_info=True)
        return 0
