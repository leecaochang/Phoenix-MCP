"""ESPHome tools: device YAML reads, Device Builder reads, firmware jobs and status.

Device YAML reads sit behind the config/esphome path jail and the credential masking in
esphome_yaml.py. Builder lookups cover validation, boards, components, automations, devices, version
history and connectivity; the job tier covers compile, install, memory analysis, logs and cleaning.
Device YAML is the operator's own configuration, so no tool here edits, deletes or restores it.
Compiling rides cap_esphome_yaml; only install needs cap_esphome_flash. This module never imports
the dispatcher.
"""


import asyncio
from collections.abc import Callable
from functools import lru_cache
import ipaddress
import json
import logging
import math
import os
import re
import textwrap
from typing import Any, NamedTuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.config_entries import ConfigEntryState

from ..const import (
    AI_TASK_CLIENT_IP, ASSIST_CLIENT_IP, CAP_DENY, MAX_FILE_BYTES, VOICE_AGENT_CLIENT_IP,
)
from ..esphome_constants import (
    ESPHOME_AUTOMATION_LIST_MAX_CHARS, ESPHOME_AUTOMATION_REF_TYPES,
    ESPHOME_AUTOMATION_YAML_MAX_CHARS, ESPHOME_BUILDER_ANALYZE_OUTPUT_MAX_CHARS,
    ESPHOME_BUILDER_ANALYZE_TIMEOUT_SECONDS, ESPHOME_BUILDER_AUTOMATION_DOCS_MAX_PAGES,
    ESPHOME_BUILDER_HISTORY_AUTHOR, ESPHOME_BUILDER_LOADED_COMPONENTS_MAX,
    ESPHOME_BUILDER_MAX_AUTOMATION_REFS, ESPHOME_BUILDER_TROUBLESHOOT_HEADLESS_SECONDS,
    ESPHOME_BUILDER_TROUBLESHOOT_TIMEOUT_SECONDS, ESPHOME_DIFF_MAX_CHARS,
    ESPHOME_BUILDER_DECODE_TIMEOUT_SECONDS, ESPHOME_BUILDER_JOB_LOG_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS, ESPHOME_BUILDER_JOB_POLL_SECONDS,
    ESPHOME_BUILDER_CANCEL_POLL_SECONDS, ESPHOME_BUILDER_CANCEL_SETTLE_SECONDS,
    ESPHOME_BUILDER_JOB_PROGRESS_MAX_CHARS, ESPHOME_BUILDER_JOB_WAIT_HEADLESS_SECONDS,
    ESPHOME_BUILDER_JOB_WAIT_SECONDS, ESPHOME_BUILDER_LOG_CAPTURE_MAX_SECONDS,
    ESPHOME_BUILDER_LOG_CAPTURE_SECONDS, ESPHOME_BUILDER_MAX_BACKTRACE_LINES,
    ESPHOME_BUILDER_MAX_COMPONENT_IDS, ESPHOME_BUILDER_METADATA_MAX_CHARS,
    ESPHOME_BUILDER_DEVICE_STATES, ESPHOME_BUILDER_OUTPUT_MAX_CHARS, ESPHOME_BUILDER_PAGE_LIMIT,
    ESPHOME_BUILDER_PAGE_LIMIT_MAX, ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_VALIDATE_TIMEOUT_SECONDS, ESPHOME_DECLARED_CACHE_ENTRIES, ESPHOME_YAML_INVENTORY_MAX_ENTRIES,
    ESPHOME_YAML_SCOPE_MAX_BYTES, ESPHOME_YAML_SEARCH_CONTEXT_LINES_MAX, ESPHOME_YAML_SEARCH_MAX_BYTES,
    ESPHOME_YAML_SEARCH_MAX_QUERY_CHARS, ESPHOME_YAML_SEARCH_MAX_MATCHES,
    ESPHOME_YAML_SEARCH_SNIPPET_MAX_CHARS,
)
from ..data import PhoenixData
from ..tool_contracts import normalize_tool_args
from ..mesa import (
    UNRESOLVED_CONTEXT_WARNING,
    async_apply_mesa_to_call,
    entity_control_mode,
    fire_mesa_blocked_event,
    unresolved_context_refused,
)
from ..mesa_audit import note_mesa_advisory
from ..helpers import content_hash
from ..catalog_text import diff_summary_fields as _summary
from ..capability import effective_cap
from ..redaction import (
    redact_entity_ids_outside, redact_inaccessible_entity_ids_in_text, redact_diagnostics, visible_entity_ids,
)
from ..request_authority import check_request_authority
from ..policy_engine import (
    Permission, esphome_entry_for_entity, esphome_entry_writable, physical_gate_applies, resolve,
    resolve_registry_access,
)
from ..access_key_store import AccessKeyRecord
from ..esphome_builder import (
    BuilderAuthRequired,
    BuilderCommandError,
    BuilderError,
    BuilderUnavailable,
    async_builder_capture,
    async_builder_command,
    collect_output_lines,
)
from ..esphome_yaml import (
    DeclaredDevice,
    apply_line_splice,
    declared_components,
    declared_device,
    esphome_rel_path_ok,
    inline_secret_values,
    masked_unified_diff,
    redact_esphome_slice,
    redact_esphome_text,
    scrub_secret_data,
    scrub_secret_values,
    secret_keys_from_text,
    secret_values_from_text,
)
from .. import yaml_includes

from ..tool_common import (
    MESA_DENIED_MESSAGE, _CAP_FORBIDDEN_MESSAGE, _gate, _pending_or_inline, _read_text_capped, _set_progress_status, _tool_error, _tool_success, _usable_path_arg, _with_mesa_advisory,
)
from ..request_authority import _approved_exec_ctx

_LOGGER = logging.getLogger(__name__)


_ESPHOME_DOMAIN = "esphome"


# aioesphomeapi DeviceInfo fields worth surfacing. mac_address and
# bluetooth_mac_address are deliberately ABSENT: HA's own ESPHome diagnostics
# redact both, and Phoenix MCP withholds network identity from agents elsewhere.
_ESPHOME_DEVICE_INFO_FIELDS = (
    "esphome_version",
    "compilation_time",
    "manufacturer",
    "model",
    "project_name",
    "project_version",
    "suggested_area",
    "has_deep_sleep",
    "webserver_port",
    "uses_password",
    "api_encryption_supported",
)


def _esphome_dashboard(hass: HomeAssistant) -> Any:
    """Return the ESPHome Device Builder coordinator, or None when unavailable.

    None means the integration is absent, the dashboard manager is not set up, or no add-on is
    configured.
    """
    # Lazily imported so Phoenix never hard-depends on the esphome integration. Reading HA's
    # in-process coordinator makes no add-on HTTP call, so no credential-resolving endpoint
    # (json-config, get_encryption_key) is reachable through it.
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.esphome.dashboard import (
            async_get_dashboard,
        )
    except ImportError:
        return None
    try:
        return async_get_dashboard(hass)
    except Exception:  # manager may not be set up yet
        return None


class EsphomeAvailability(NamedTuple):
    """What ESPHome surfaces exist on this system.

    integration   - the esphome integration is set up (devices may exist).
    builder       - the Device Builder add-on is configured and known to HA.
    builder_live  - the coordinator's last refresh of it succeeded.
    """

    integration: bool
    builder: bool
    builder_live: bool


def esphome_availability(hass: HomeAssistant) -> EsphomeAvailability:
    """Report which ESPHome surfaces are present, for tool gating and the panel.

    Announcement gating keys on configured state, never `builder_live`, and fails open: an unknown
    host announces everything.
    """
    # An MCP client caches its tool list and this transport cannot push list-changed, so a list
    # tracking a stopped add-on would flap mid-session; an unreachable add-on fails loudly at call
    # time instead. Hiding a tool is a usability affordance, not a security control: the capability
    # gate and call-time checks run regardless.
    if hass is None:
        return EsphomeAvailability(True, True, True)
    try:
        integration = _ESPHOME_DOMAIN in hass.config.components or bool(
            hass.config_entries.async_entries(_ESPHOME_DOMAIN)
        )
    except Exception:  # never let a probe hide the tool surface
        integration = True
    dashboard = _esphome_dashboard(hass)
    builder = dashboard is not None
    return EsphomeAvailability(
        integration=integration,
        builder=builder,
        builder_live=builder and bool(getattr(dashboard, "last_update_success", False)),
    )


def _esphome_actions_for_entity(
    hass: HomeAssistant, access_key: AccessKeyRecord, entity_id: str, writable: bool
) -> list[dict]:
    """The owning ESPHome device's user-defined actions, as find_available_actions rows.

    Availability follows the device's write scope and physical gate, as call_service does. `args` is included
    because these schemas are device-defined.
    """
    entry = esphome_entry_for_entity(hass, entity_id)
    if entry is None:
        return []
    runtime = getattr(entry, "runtime_data", None)
    device_info = getattr(runtime, "device_info", None)
    name = getattr(device_info, "name", None)
    if not name:
        return []

    available = writable or esphome_entry_writable(hass, entry, access_key)
    prefix = name.replace("-", "_")
    gated = physical_gate_applies(_ESPHOME_DOMAIN, "", [
        reg_entry.entity_id for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    ]) and effective_cap(access_key, "cap_physical_control") == CAP_DENY
    rows: list[dict] = []
    for svc in (getattr(runtime, "services", None) or {}).values():
        action = getattr(svc, "name", None)
        if not action:
            continue
        row: dict = {
            "service": f"{_ESPHOME_DOMAIN}.{prefix}_{action}",
            "available": available and not gated,
            "args": [
                {"name": getattr(arg, "name", None), "type": _esphome_arg_type(arg),
                 **_esphome_action_metadata(arg, hass, access_key)}
                for arg in (getattr(svc, "args", None) or [])
            ],
            **_esphome_action_metadata(svc, hass, access_key),
        }
        if not available:
            row["reason"] = "read-only access to this device"
        elif gated:
            row["reason"] = "requires physical control capability"
        rows.append(row)
    rows.sort(key=lambda r: r["service"])
    return rows


def _esphome_action_signature(entry: Any, service: str) -> list[dict]:
    """Declared argument signature for one of an entry's user-defined actions."""
    runtime = getattr(entry, "runtime_data", None)
    device_info = getattr(runtime, "device_info", None)
    name = getattr(device_info, "name", None) or ""
    action = service[len(f"{name.replace('-', '_')}_"):]
    for svc in (getattr(runtime, "services", None) or {}).values():
        if getattr(svc, "name", None) == action:
            return [
                {"name": getattr(arg, "name", None), "type": _esphome_arg_type(arg)}
                for arg in (getattr(svc, "args", None) or [])
            ]
    return []


def _esphome_arg_type(arg: Any) -> str | None:
    """Render a user-service argument type as a plain lowercase string."""
    arg_type = getattr(arg, "type", None)
    if arg_type is None:
        return None
    return str(getattr(arg_type, "name", arg_type)).lower()


def _esphome_metadata_text(value: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """A Device Builder or device string as returned: scrubbed whole, then bounded."""
    # In the other order a credential crossing the cut survives as a prefix.
    return redact_diagnostics(redact_inaccessible_entity_ids_in_text(
        value, access_key, hass))[:ESPHOME_BUILDER_METADATA_MAX_CHARS]


def _esphome_action_metadata(obj: Any, hass: HomeAssistant, access_key: AccessKeyRecord | None) -> dict:
    """Optional 2026.9 action descriptions/examples, bounded and scrubbed."""
    if access_key is None:
        return {}
    metadata = {}
    for key in ("description", "example"):
        value = getattr(obj, key, None)
        if isinstance(value, str) and value:
            metadata[key] = _esphome_metadata_text(value, access_key, hass)
    return metadata


def _esphome_declared_actions(
    hass: HomeAssistant, device_info: Any, runtime: Any, access_key: AccessKeyRecord | None = None
) -> list[dict]:
    """List a device's user-defined API actions and their HA service names.

    Enumerated from the entry's own runtime services, because esphome leaves dynamic services
    registered after disconnect. `registered` surfaces that drift.
    """
    services = getattr(runtime, "services", None) or {}
    prefix = (getattr(device_info, "name", None) or "").replace("-", "_")
    actions: list[dict] = []
    for service in services.values():
        name = getattr(service, "name", None)
        if not name:
            continue
        ha_service = f"{prefix}_{name}"
        actions.append({
            "name": name,
            "ha_service": f"{_ESPHOME_DOMAIN}.{ha_service}",
            "registered": bool(hass.services.has_service(_ESPHOME_DOMAIN, ha_service)),
            "args": [
                {"name": getattr(arg, "name", None), "type": _esphome_arg_type(arg),
                 **_esphome_action_metadata(arg, hass, access_key)}
                for arg in (getattr(service, "args", None) or [])
            ],
            **_esphome_action_metadata(service, hass, access_key),
        })
    actions.sort(key=lambda a: a["name"])
    return actions


def _esphome_dashboard_detail(configured: dict, device_info: Any) -> dict:
    """Project a Device Builder ConfiguredDevice entry plus update status.

    update_available mirrors HA's ESPHome update entity: running version against buildable version.
    `address` is omitted, since LAN topology is withheld from agents everywhere.
    """
    installed = getattr(device_info, "esphome_version", None)
    current = configured.get("current_version")
    detail: dict = {
        "configuration": configured.get("configuration"),
        "current_version": current,
        "deployed_version": configured.get("deployed_version"),
        "target_platform": configured.get("target_platform"),
    }
    if installed and current:
        detail["update_available"] = installed != current
    return detail


def _esphome_runtime_detail(
    hass: HomeAssistant, entry: Any, record: dict, dash_data: dict, access_key: AccessKeyRecord
) -> None:
    """Fill in live runtime fields for a loaded ESPHome config entry.

    Duck-types entry.runtime_data, so aioesphomeapi, an integration requirement, stays out of
    Phoenix's import graph.
    """
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return
    record["available"] = bool(getattr(runtime, "available", False))
    record["expected_disconnect"] = bool(getattr(runtime, "expected_disconnect", False))

    device_info = getattr(runtime, "device_info", None)
    if device_info is None:
        return
    name = getattr(device_info, "name", None)
    record["name"] = name
    record["friendly_name"] = getattr(device_info, "friendly_name", None)
    for field in _ESPHOME_DEVICE_INFO_FIELDS:
        value = getattr(device_info, field, None)
        if value is not None and value != "":
            record[field] = value

    bt = getattr(runtime, "bluetooth_device", None)
    if bt is not None:
        record["bluetooth_proxy"] = {
            "available": bool(getattr(bt, "available", False)),
            "connections_free": getattr(bt, "ble_connections_free", None),
            "connections_limit": getattr(bt, "ble_connections_limit", None),
        }

    actions = _esphome_declared_actions(hass, device_info, runtime, access_key)
    if actions:
        record["actions"] = actions

    if name and name in dash_data:
        record["device_builder"] = _esphome_dashboard_detail(dash_data[name], device_info)


async def _tool_get_esphome_overview(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: status of the ESPHome devices this access key can see."""
    if effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_esphome_overview"

    warnings: list[str] = []
    dashboard = _esphome_dashboard(hass)
    dash_data: dict = {}
    if dashboard is None:
        warnings.append(
            "The ESPHome Device Builder is not available; configuration filenames "
            "and update status are omitted."
        )
    else:
        dash_data = getattr(dashboard, "data", None) or {}
        if not getattr(dashboard, "last_update_success", True):
            warnings.append(
                "The ESPHome Device Builder was unreachable at its last refresh; "
                "version and configuration data may be stale."
            )

    registry = er.async_get(hass)
    devices: list[dict] = []
    for entry in hass.config_entries.async_entries(_ESPHOME_DOMAIN):
        # Scope on the entity REGISTRY rather than live states so a device whose
        # entry failed to load is still counted against the access key's tree; an
        # unloaded entry publishes no states and would otherwise vanish from the
        # fleet view precisely when it is most worth reporting.
        visible = sum(
            1
            for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
            if resolve(reg_entry.entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
        )
        if not visible:
            continue

        loaded = entry.state is ConfigEntryState.LOADED
        record: dict = {
            "title": entry.title,
            "state": entry.state.value,
            "loaded": loaded,
            "accessible_entity_count": visible,
        }
        # runtime_data is DELETED on unload, so reading it on a non-loaded entry
        # raises AttributeError. entry.data and entry.unique_id are never read at
        # all: they carry the API encryption key and the device MAC.
        if loaded:
            _esphome_runtime_detail(hass, entry, record, dash_data, access_key)
        devices.append(record)

    devices.sort(key=lambda d: ((d.get("name") or d.get("title") or "").lower()))
    body: dict = {"count": len(devices), "devices": devices}

    if warnings:
        body["warnings"] = warnings
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_esphome_overview"


_ESPHOME_DIR = "esphome"


_ESPHOME_PATH_REFUSED = (
    "Only jailed .yaml and .yml files inside the ESPHome configuration directory "
    "can be read; files named like secrets, the archive folder, and hidden files are excluded."
)


# Nonexistent and out-of-scope are byte-identical, so the refusal is not an
# existence oracle for devices outside this access key's tree.
_ESPHOME_NOT_FOUND = "ESPHome configuration file not found."


def _resolve_esphome_path(hass: HomeAssistant, file_arg: Any) -> tuple[str, str] | None:
    """Resolve a device-YAML argument to (realpath, esphome-relative path), or None when refused.

    The structural rules run on the argument and the realpath, so a symlink cannot rename past them.
    Rooted at config/esphome, outside FILESYSTEM_ALLOWED_DIRS.
    """
    # Widening cap_filesystem must never reach device YAML, which embeds C++ lambdas and
    # credentials.
    if _usable_path_arg(file_arg) is None:
        return None
    rel_arg = file_arg.strip()
    if os.path.isabs(rel_arg) or not esphome_rel_path_ok(rel_arg):
        return None
    root = os.path.realpath(os.path.join(hass.config.config_dir, _ESPHOME_DIR))
    candidate = os.path.realpath(os.path.join(root, rel_arg))
    if not candidate.startswith(root + os.sep):
        return None
    rel = os.path.relpath(candidate, root)
    if not esphome_rel_path_ok(rel):
        return None
    return candidate, rel


def _locate_esphome_file(hass: HomeAssistant, file_arg: Any) -> tuple[str, str, bool] | None:
    """Resolve a device-YAML argument and probe whether it is a file. Blocking: executor only."""
    resolved = _resolve_esphome_path(hass, file_arg)
    if resolved is None:
        return None
    return resolved[0], resolved[1], os.path.isfile(resolved[0])


def _locate_esphome_files(hass: HomeAssistant, file_args: list[Any]) -> dict[str, tuple[str, str]]:
    """The existing device files among Builder-reported paths, keyed by the reported path.

    Blocking: one executor job for a whole inventory instead of one per row.
    """
    found: dict[str, tuple[str, str]] = {}
    for file_arg in file_args:
        if isinstance(file_arg, str) and file_arg not in found:
            resolved = _resolve_esphome_path(hass, file_arg)
            if resolved is not None and os.path.isfile(resolved[0]):
                found[file_arg] = resolved
    return found


def _read_esphome_secrets(hass: HomeAssistant) -> tuple[set[str], set[str] | None]:
    """Read the ESPHome secrets.yaml internally: (values, key names); no tool returns either.

    Values feed layer-3 redaction and key names feed `defined_secrets`. An unreadable file degrades
    to (empty values, None keys), leaving redaction layers 1 and 2.
    """
    path = os.path.join(hass.config.config_dir, _ESPHOME_DIR, yaml_includes.SECRETS_YAML)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, ValueError):
        return set(), None
    return secret_values_from_text(text), secret_keys_from_text(text)


@lru_cache(maxsize=ESPHOME_DECLARED_CACHE_ENTRIES)
def _declared_device_at(path: str, _mtime_ns: int, _size: int) -> DeclaredDevice | None:
    """Parse one version of a file, as a listing asks for every file on each call."""
    try:
        return declared_device(_read_text_capped(path))
    except (OSError, ValueError, yaml_includes.YamlParseError):
        _LOGGER.debug("ESPHome device details could not be read from %s", path, exc_info=True)
        return None


def _read_declared_device(hass: HomeAssistant, rel: str) -> DeclaredDevice | None:
    """The device a file declares. Blocking: executor only. An unreadable file declares nothing."""
    resolved = _resolve_esphome_path(hass, rel)
    if resolved is None:
        return None
    try:
        status = os.stat(resolved[0])
    except OSError:
        return None
    return _declared_device_at(resolved[0], status.st_mtime_ns, status.st_size)


def _esphome_declared_devices(hass: HomeAssistant, rels: list[str]) -> dict[str, DeclaredDevice | None]:
    """What each file declares. Blocking: executor only.

    Past the byte budget a file declares nothing, which leaves its owner unknown.
    """
    declared: dict[str, DeclaredDevice | None] = {}
    remaining = ESPHOME_YAML_SCOPE_MAX_BYTES
    for rel in dict.fromkeys(rels):
        resolved = _resolve_esphome_path(hass, rel)
        try:
            remaining -= os.path.getsize(resolved[0]) if resolved is not None else 0
        except OSError:
            continue
        if remaining >= 0:
            declared[rel] = _read_declared_device(hass, rel)
    return declared


async def _esphome_declared_for_scope(hass: HomeAssistant, rels: list[str]) -> dict[str, DeclaredDevice | None]:
    """Read what files declare for the scope decision that follows on the event loop."""
    if not hass.config_entries.async_entries(_ESPHOME_DOMAIN):
        return {}
    return await hass.async_add_executor_job(_esphome_declared_devices, hass, rels)


def _names_declared_device(name: str, declared: DeclaredDevice | None) -> bool:
    """Whether a reported device name is the one a file declares, MAC suffix included."""
    if declared is None or declared.name is None:
        return False
    return name == declared.name or (
        declared.mac_suffix and re.fullmatch(rf"{re.escape(declared.name)}-[0-9a-f]{{6}}", name) is not None)


def _esphome_address_key(address: str) -> str:
    """An address as compared with an adopted device's host or name."""
    return address.strip().lower().rstrip(".").removesuffix(".local")


def _esphome_entries_for_file(
    hass: HomeAssistant, rel: str, declared: DeclaredDevice | None = None
) -> list[Any]:
    """Find adopted owners, including entries without live runtime data.

    HA persists device_name through disconnects, so unloading never turns an adopted configuration
    into an orphan governed by the capability alone. ``declared`` adds the name the file itself
    states and the addresses it flashes, for callers that read it.
    """
    dashboard = _esphome_dashboard(hass)
    dash_data = getattr(dashboard, "data", None) or {} if dashboard is not None else {}
    stem = os.path.splitext(os.path.basename(rel))[0]
    targets = {_esphome_address_key(address) for address in declared.addresses} if declared else set()
    matches: list[Any] = []
    for entry in hass.config_entries.async_entries(_ESPHOME_DOMAIN):
        stored = getattr(entry, "data", {})
        info = getattr(getattr(entry, "runtime_data", None), "device_info", None)
        names = {stored.get("device_name"), getattr(info, "name", None)}
        reached = {_esphome_address_key(value) for value in (*names, stored.get("host")) if isinstance(value, str)}
        if targets & reached or any(
            isinstance(name, str) and name and (
                (dash_data.get(name) or {}).get("configuration") == rel or name == stem
                or _names_declared_device(name, declared)
            )
            for name in names
        ):
            matches.append(entry)
    return matches


def _esphome_device_for_file(hass: HomeAssistant, rel: str, declared: DeclaredDevice | None = None) -> Any | None:
    """Return the sole adopted owner for display; ambiguous owners stay unnamed."""
    entries = _esphome_entries_for_file(hass, rel, declared)
    return entries[0] if len(entries) == 1 else None


def _esphome_file_has_no_owner(hass: HomeAssistant, declared: DeclaredDevice | None) -> bool:
    """Whether a file with no matched owner can be taken to have none.

    Nothing adopted is proof, and so is a file stating its own name and no address: a name from
    elsewhere or an address could point it at an adopted device.
    """
    return not hass.config_entries.async_entries(_ESPHOME_DOMAIN) or (
        declared is not None and declared.self_contained)


def _esphome_file_is_orphan(hass: HomeAssistant, rel: str, declared: DeclaredDevice | None) -> bool:
    """Whether a flash of a file with no matched owner goes to a new device.

    Past _esphome_file_has_no_owner the Builder must report the file too, as a device whose
    registration has not reached Home Assistant yet has no name to match.
    """
    if not hass.config_entries.async_entries(_ESPHOME_DOMAIN):
        return True
    if not _esphome_file_has_no_owner(hass, declared):
        return False
    dashboard = _esphome_dashboard(hass)
    dash_data = getattr(dashboard, "data", None) or {} if dashboard is not None else {}
    return any((row or {}).get("configuration") == rel for row in dash_data.values())


def _esphome_file_in_scope(
    hass: HomeAssistant, access_key: AccessKeyRecord, rel: str, *, write: bool,
    declared: DeclaredDevice | None = None,
) -> bool:
    """Whether the access key may read the device YAML at rel, or with write, build from it.

    Every adopted owner must be in scope, even when unloaded. A file with no owner follows the
    capability alone. With the owner unknown it could be any adopted device, so all of them must be
    in scope; ``declared`` None means the file was not read and is that case.
    """
    owners = _esphome_entries_for_file(hass, rel, declared)
    if not owners and not _esphome_file_has_no_owner(hass, declared):
        owners = hass.config_entries.async_entries(_ESPHOME_DOMAIN)
    return _esphome_entries_in_scope(hass, access_key, owners, write=write)


def _esphome_name_in_scope(hass: HomeAssistant, access_key: AccessKeyRecord, name: str) -> bool:
    """Whether every adopted device going by a Builder-reported name is readable by this key."""
    return _esphome_entries_in_scope(
        hass, access_key, _esphome_entries_for_file(hass, f"{name}.yaml"), write=False)


def _esphome_entries_in_scope(
    hass: HomeAssistant, access_key: AccessKeyRecord, entries: list[Any], *, write: bool
) -> bool:
    """Whether the access key reaches each entry through at least one of its entities."""
    registry = er.async_get(hass)
    required = (Permission.WRITE,) if write else (Permission.READ, Permission.WRITE)
    return all(
        any(
            resolve_registry_access(reg_entry.entity_id, access_key, hass) in required
            for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        )
        for entry in entries
    )


def _esphome_files(hass: HomeAssistant) -> list[dict]:
    """Recursively list jailed device YAML, without following directory symlinks.

    UNSCOPED: runs in a worker thread, so the caller filters the rows with
    _esphome_file_in_scope on the event loop (_esphome_scoped_rows).
    """
    root = os.path.join(hass.config.config_dir, _ESPHOME_DIR)
    try:
        names: list[str] = []
        visited = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            visited += len(dirs) + len(files)
            if visited > ESPHOME_YAML_INVENTORY_MAX_ENTRIES:
                raise ValueError("ESPHome configuration inventory exceeds the scan budget.")
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() != "archive")
            names.extend(os.path.relpath(os.path.join(directory, f), root) for f in sorted(files))
    except OSError:
        return []
    rows: list[dict] = []
    for name in names:
        if not esphome_rel_path_ok(name):
            continue
        resolved = _resolve_esphome_path(hass, name)
        if resolved is None:
            continue
        full, name = resolved
        if not os.path.isfile(full):
            continue
        entry = _esphome_device_for_file(hass, name)
        device = getattr(getattr(entry, "runtime_data", None), "device_info", None)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        rows.append({
            "file": name,
            "bytes": size,
            "device": getattr(device, "name", None),
        })
    rows.sort(key=lambda r: r["file"])
    return rows


async def _esphome_scoped_rows(hass: HomeAssistant, access_key: AccessKeyRecord, rows: list[dict]) -> list[dict]:
    """Rows naming a file this access key may read; scope is decided on the loop."""
    declared = await _esphome_declared_for_scope(hass, [row["file"] for row in rows])
    return [row for row in rows if _esphome_file_in_scope(
        hass, access_key, row["file"], write=False, declared=declared.get(row["file"]))]


async def _esphome_listing(hass: HomeAssistant, access_key: AccessKeyRecord) -> list[dict]:
    """The device YAML files this access key may read."""
    return await _esphome_scoped_rows(hass, access_key, await hass.async_add_executor_job(_esphome_files, hass))


class _EntityView(NamedTuple):
    """What a worker thread needs to hide entity IDs, decided on the event loop."""

    visible: frozenset[str]
    domains: frozenset[str]


def _esphome_entity_view(access_key: AccessKeyRecord, hass: HomeAssistant) -> _EntityView:
    """The key's visible entity IDs and every domain in use. Event loop only."""
    known = {*hass.states.async_entity_ids(), *er.async_get(hass).entities.keys()}
    return _EntityView(visible_entity_ids(access_key, hass), frozenset(eid.split(".", 1)[0] for eid in known))


def _esphome_hide_entities(text: str, view: _EntityView, hass: HomeAssistant) -> str:
    """Device YAML with hidden entity IDs replaced. A lambda's it.printf is no entity reference."""
    return redact_entity_ids_outside(text, view.visible, hass, domains=view.domains)


def _esphome_masked_text(
    hass: HomeAssistant, text: str, secret_values: set[str], view: _EntityView
) -> tuple[str, list[str]]:
    """A device file as a key reads it: (masked text, redacted paths). Executor only."""
    masked, paths = redact_esphome_text(text, secret_values)
    return _esphome_hide_entities(masked, view, hass), [_esphome_hide_entities(path, view, hass) for path in paths]


def _esphome_search(
    hass: HomeAssistant, rows: list[dict], view: _EntityView,
    query: str, case_sensitive: bool, context: int,
) -> dict:
    """Search only masked, authorized files within a total byte budget.

    Runs in a worker thread over rows and an entity view the loop decided.
    """
    values, _keys = _read_esphome_secrets(hass)
    needle = query if case_sensitive else query.casefold()
    matches = []
    scanned = 0
    incomplete = False
    for row in rows:
        resolved = _resolve_esphome_path(hass, row["file"])
        if resolved is None:
            continue
        if scanned + row["bytes"] > ESPHOME_YAML_SEARCH_MAX_BYTES:
            incomplete = True
            break
        try:
            raw = _read_text_capped(resolved[0])
            scanned += len(raw.encode("utf-8"))
            masked = _esphome_masked_text(hass, raw, values, view)[0]
        except (OSError, ValueError, yaml_includes.YamlParseError):
            incomplete = True
            continue
        lines = masked.splitlines()
        for index, line in enumerate(lines):
            if needle not in (line if case_sensitive else line.casefold()):
                continue
            start = max(0, index - context)
            snippet = "\n".join(lines[start:index + context + 1])
            matches.append({
                "file": row["file"], "line": index + 1, "context_start_line": start + 1,
                "text": snippet[:ESPHOME_YAML_SEARCH_SNIPPET_MAX_CHARS],
                "text_truncated": len(snippet) > ESPHOME_YAML_SEARCH_SNIPPET_MAX_CHARS,
            })
            if len(matches) >= ESPHOME_YAML_SEARCH_MAX_MATCHES:
                return {"matches": matches, "incomplete": True}
    return {"matches": matches, "incomplete": incomplete}


def _esphome_paged(rows: list[dict], args: dict, key: str) -> dict:
    """Page an already scoped result; counts never describe upstream inventory."""
    offset = args.get("offset", 0)
    offset = offset if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0 else 0
    limit = _esphome_page_limit(args)
    page = []
    size = 0
    for row in rows[offset:offset + limit]:
        size += len(json.dumps(row))
        if size > ESPHOME_BUILDER_OUTPUT_MAX_CHARS:
            break
        page.append(row)
    end = offset + len(page)
    # The shared envelope: count is this page, total every scoped row, and
    # next_offset appears only when there is more.
    body = {key: page, "count": len(page), "total": len(rows), "offset": offset, "has_more": end < len(rows)}
    if end < len(rows):
        body["next_offset"] = end
    return body


async def _tool_search_esphome_yaml(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """Search masked local device configurations under the authoring capability."""
    resource = "search_esphome_yaml"
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", resource
    query = args.get("query")
    sensitive = args.get("case_sensitive", False)
    context = args.get("context_lines", 1)
    if (not isinstance(query, str) or not query.strip()
            or len(query) > ESPHOME_YAML_SEARCH_MAX_QUERY_CHARS
            or not isinstance(sensitive, bool)
            or not isinstance(context, int) or isinstance(context, bool)
            or not 0 <= context <= ESPHOME_YAML_SEARCH_CONTEXT_LINES_MAX):
        return _tool_error("Provide a nonempty query of at most 200 characters, a boolean "
                           "case_sensitive, and context_lines between 0 and 3."), "invalid_request", resource
    try:
        rows = await _esphome_listing(hass, access_key)
        view = _esphome_entity_view(access_key, hass)
        result = await hass.async_add_executor_job(
            _esphome_search, hass, rows, view, query, sensitive, context)
    except (OSError, ValueError):
        return _tool_error("ESPHome configuration search could not complete."), "invalid_request", resource
    rows = await _esphome_scoped_rows(hass, access_key, result["matches"])
    body = _esphome_paged(rows, args, "matches")
    body["incomplete"] = result["incomplete"]
    return _tool_success(json.dumps(body)), "allowed", resource


async def _tool_get_esphome_yaml(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list ESPHome device YAML files, or read one with credentials masked."""
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_esphome_yaml"

    file_arg = args.get("file")
    secret_values, secret_keys = await hass.async_add_executor_job(_read_esphome_secrets, hass)

    if file_arg is None or (isinstance(file_arg, str) and not file_arg.strip()):
        try:
            rows = await _esphome_listing(hass, access_key)
        except ValueError:
            return _tool_error("ESPHome configuration inventory exceeds the scan budget."), "invalid_request", "get_esphome_yaml"
        body: dict = {"count": len(rows), "files": rows}
        if secret_keys is not None:
            body["defined_secrets"] = sorted(secret_keys)
        if _esphome_dashboard(hass) is None:
            body["warnings"] = [
                "The ESPHome Device Builder is not available; files are matched to "
                "devices by name only."
            ]
        return _tool_success(json.dumps(body, default=str)), "allowed", "get_esphome_yaml"

    resolved = await hass.async_add_executor_job(_resolve_esphome_path, hass, file_arg)
    if resolved is None:
        return _tool_error(_ESPHOME_PATH_REFUSED), "invalid_request", "get_esphome_yaml"
    path, rel = resolved
    declared = (await _esphome_declared_for_scope(hass, [rel])).get(rel)
    if not _esphome_file_in_scope(hass, access_key, rel, write=False, declared=declared):
        return _tool_error(_ESPHOME_NOT_FOUND), "denied", f"esphome:{rel}"

    try:
        raw = await hass.async_add_executor_job(_read_text_capped, path)
    except FileNotFoundError:
        return _tool_error(_ESPHOME_NOT_FOUND), "not_found", f"esphome:{rel}"
    except (OSError, ValueError):
        return _tool_error("Failed to read the ESPHome configuration file."), "invalid_request", f"esphome:{rel}"

    try:
        redacted, redacted_paths = await hass.async_add_executor_job(
            _esphome_masked_text, hass, raw, secret_values, _esphome_entity_view(access_key, hass)
        )
    except yaml_includes.YamlParseError as err:
        # Fail closed: a file whose credentials cannot be located must not be
        # returned, since inline credentials are the normal case here.
        return (
            _tool_error(f"The file could not be parsed, so its credentials cannot be masked: {err}"),
            "invalid_request", f"esphome:{rel}",
        )

    entry = _esphome_device_for_file(hass, rel, declared)
    device_info = getattr(getattr(entry, "runtime_data", None), "device_info", None)
    body = {
        "file": rel,
        "content": redacted,
        # Hash of the MASKED text. A hash of the raw bytes beside a text that
        # differs from them only in its credentials would be an offline-guessing
        # oracle for a weak inline password.
        "content_hash": content_hash(redacted),
        "redacted_paths": redacted_paths,
        "device": getattr(device_info, "name", None),
    }
    if secret_keys is not None:
        body["defined_secrets"] = sorted(secret_keys)
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


_ESPHOME_BUILDER_ABSENT = "The ESPHome Device Builder add-on is not available."


_ESPHOME_BUILDER_UNREACHABLE = "The ESPHome Device Builder did not respond."
_ESPHOME_BUILDER_WAIT_LOST = (
    "The ESPHome Device Builder stopped responding, so this job's status is unknown. A build it already "
    "started may still be running: check it with get_esphome_job rather than starting another."
)


_ESPHOME_BUILDER_AUTH = (
    "The ESPHome Device Builder requires authentication, and Phoenix MCP holds no "
    "Device Builder credentials."
)


def _esphome_builder_url(hass: HomeAssistant) -> str | None:
    """The Device Builder's base URL, or None when it is not configured."""
    return getattr(_esphome_dashboard(hass), "url", None)


def _builder_refusal(err: BuilderError, resource: str) -> tuple[dict, str, str]:
    """Map a client failure to a tool error the agent can act on."""
    if isinstance(err, BuilderAuthRequired):
        message = _ESPHOME_BUILDER_AUTH
    elif isinstance(err, BuilderCommandError):
        message = f"The ESPHome Device Builder refused the request: {err.error_code}"
        if err.details:
            message += f" ({err.details})"
    else:
        message = _ESPHOME_BUILDER_UNREACHABLE
    return _tool_error(message), "invalid_request", resource


async def _esphome_file_precheck(
    hass: HomeAssistant, access_key: AccessKeyRecord, args: dict, tool_name: str, *, write: bool = False
) -> tuple[tuple[dict, str, str] | None, str, str]:
    """Resolve and authorize a device file for the file-bound builder tools.

    Existence is checked before any Builder call, so "not found" matches an out-of-scope refusal.
    `write` marks the tools that start a firmware job.
    """
    # Building for a device acts on it, so it must not be reachable from read-only scope. The path
    # is resolved and probed in one executor job; the scope decision stays on the event loop and
    # still runs before the probe result is used.
    located = await hass.async_add_executor_job(_locate_esphome_file, hass, args.get("file"))
    if located is None:
        return (_tool_error(_ESPHOME_PATH_REFUSED), "invalid_request", tool_name), "", ""
    path, rel, is_file = located
    declared = (await _esphome_declared_for_scope(hass, [rel])).get(rel)
    in_scope = _esphome_file_in_scope(hass, access_key, rel, write=write, declared=declared)
    if not in_scope and (is_file or _esphome_entries_for_file(hass, rel)):
        return (_tool_error(_ESPHOME_NOT_FOUND), "denied", f"esphome:{rel}"), path, rel
    if not is_file:
        return (_tool_error(_ESPHOME_NOT_FOUND), "not_found", f"esphome:{rel}"), path, rel
    return None, path, rel


def _esphome_scrub_values(hass: HomeAssistant, path: str) -> set[str]:
    """Every credential value that must not appear in Device Builder output.

    The secrets.yaml values plus whatever this file inlines. An unparseable file still contributes
    the former, since a broken edit is when the scrub matters most.
    """
    secret_values, _keys = _read_esphome_secrets(hass)
    values = set(secret_values or set())
    try:
        values |= inline_secret_values(_read_text_capped(path), values)
    except (OSError, ValueError, yaml_includes.YamlParseError):
        _LOGGER.debug("ESPHome scrub could not read inline credentials", exc_info=True)
    return values


def _esphome_scrubbed_json(body: Any, values: set[str]) -> str:
    """Encode a result after scrubbing it; an encoded value no longer matches itself."""
    return json.dumps(scrub_secret_data(body, values), default=str)


def _esphome_output_scrub(values: set[str]) -> Callable[[str], str]:
    """A scrub for Builder output, applied to each line before the collector bounds it."""
    return lambda line: scrub_secret_values(line, values)


def _esphome_page_limit(args: dict) -> int:
    """Clamp a caller-supplied page size into the configured bounds, else use the default."""
    # A missing, non-integer or non-positive limit means unspecified: clamping limit 0 up to 1 would
    # look like an empty catalog.
    raw = args.get("limit")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        return ESPHOME_BUILDER_PAGE_LIMIT
    return min(raw, ESPHOME_BUILDER_PAGE_LIMIT_MAX)


def _esphome_optional_args(args: dict, names: tuple[str, ...]) -> dict:
    """Pass through only the non-empty string filters the caller actually set."""
    out = {}
    for name in names:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            out[name] = value.strip()
    return out


async def _tool_validate_esphome_yaml(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: run the Device Builder's config validation over a device file.

    Validates what is on disk, never caller content: a security boundary, because a config with
    external_components fetches and executes remote code at validation time.
    """
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "validate_esphome_yaml"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "validate_esphome_yaml"

    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, "validate_esphome_yaml")
    if refusal is not None:
        return refusal

    raw, secret_values, values = await hass.async_add_executor_job(_esphome_read_for_migration, hass, path)
    try:
        res = await async_builder_command(
            hass, url, "devices/validate", {"configuration": rel, "show_secrets": False},
            timeout_seconds=ESPHOME_BUILDER_VALIDATE_TIMEOUT_SECONDS,
            collect_output=True, scrub_output=_esphome_output_scrub(values),
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")

    body: dict[str, Any] = {
        "file": rel,
        "valid": res.success,
        "exit_code": res.exit_code,
        # Validation errors quote the offending lines back, which can include a
        # credential the file carries inline.
        "output": scrub_secret_values(res.output, values),
    }
    if res.output_truncated:
        body["output_truncated"] = True
    # What produced this verdict: the compiler and the Device Builder have
    # separate versions, and a schema change in either can change it.
    if res.esphome_version:
        body["esphome_version"] = res.esphome_version
    if res.server_version:
        body["device_builder_version"] = res.server_version
    if raw is None:
        body["migration_unavailable"] = "The file could not be read and parsed, so it was not checked for migrations."
    else:
        view = _esphome_entity_view(access_key, hass)
        body.update(await _esphome_migration_check(hass, url, rel, raw, secret_values, values, view))
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


def _esphome_read_for_migration(hass: HomeAssistant, path: str) -> tuple[str | None, set[str], set[str]]:
    """(raw text or None, secrets.yaml values, every scrub value) for one file. Executor only.

    Raw text needs the file to read within the size cap and parse, since a file whose credentials
    cannot be located is not sent.
    """
    secret_values, _keys = _read_esphome_secrets(hass)
    secret_values = set(secret_values or set())
    values = set(secret_values)
    try:
        raw = _read_text_capped(path)
    except (OSError, ValueError):
        return None, secret_values, values
    try:
        values |= inline_secret_values(raw, secret_values)
    except yaml_includes.YamlParseError:
        return None, secret_values, values
    return raw, secret_values, values


# Rule names in a migration change: key and action spellings and versions, never values.
_ESPHOME_MIGRATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_ESPHOME_MIGRATION_FIELDS = ("kind", "scope", "old", "new", "since", "removed_in")


def _esphome_migration_changes(raw_changes: Any) -> list[dict]:
    """Project the add-on's migration rules to bounded identifier fields."""
    changes = []
    for change in raw_changes if isinstance(raw_changes, list) else []:
        if not isinstance(change, dict):
            continue
        row: dict[str, Any] = {}
        for key in _ESPHOME_MIGRATION_FIELDS:
            value = change.get(key)
            if isinstance(value, str) and _ESPHOME_MIGRATION_TOKEN_RE.fullmatch(value):
                row[key] = value
        if isinstance(change.get("required"), bool):
            row["required"] = change["required"]
        if row:
            changes.append(row)
    return changes


def _esphome_migration_diff(
    hass: HomeAssistant, raw: str, yaml_diff: dict, secret_values: set[str], rel: str, view: _EntityView
) -> tuple[str, bool] | None:
    """Masked unified diff of the migration, or None when it cannot be shown safely. Executor only.

    The add-on's replacement is applied here, never returned. A migration leaving fewer credentials
    maskable than the original is withheld.
    """
    try:
        migrated = apply_line_splice(
            raw, int(yaml_diff["fromLine"]), int(yaml_diff["toLine"]), str(yaml_diff["replacement"]))
        before, before_paths = _esphome_masked_text(hass, raw, secret_values, view)
        known = secret_values | inline_secret_values(raw, secret_values)
        after, after_paths = _esphome_masked_text(hass, migrated, known, view)
    except (KeyError, TypeError, ValueError, yaml_includes.YamlParseError):
        return None
    if len(after_paths) < len(before_paths):
        return None
    return masked_unified_diff(before, after, rel, f"{rel} (migrated)", ESPHOME_DIFF_MAX_CHARS)


async def _esphome_migration_check(
    hass: HomeAssistant, url: str, rel: str, raw: str, secret_values: set[str], values: set[str],
    view: _EntityView,
) -> dict:
    """The validate reply's migration block: rules that would respell this file, and a masked diff.

    Sends the persisted file Phoenix MCP just read, never caller-supplied text.
    Any failure degrades to migration_unavailable; it never fails the validation.
    """
    try:
        res = await async_builder_command(
            hass, url, "editor/migrate_config", {"content": raw},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError:
        _LOGGER.debug("ESPHome migration check failed", exc_info=True)
        return {"migration_unavailable": "The Device Builder could not check this file for migrations."}
    result = res.result if isinstance(res.result, dict) else {}
    yaml_diff = result.get("yaml_diff")
    changes = _esphome_migration_changes(result.get("changes"))
    if not isinstance(yaml_diff, dict):
        return {"migration": {"needed": False}}
    migration: dict[str, Any] = {"needed": True, "changes": changes}
    shown = await hass.async_add_executor_job(_esphome_migration_diff, hass, raw, yaml_diff, secret_values, rel, view)
    if shown is None:
        migration["diff_withheld"] = "The migrated text could not be masked reliably; review it in the ESPHome dashboard."
    else:
        migration["diff"] = scrub_secret_values(shown[0], values)
        if shown[1]:
            migration["diff_truncated"] = True
    migration["note"] = ("Nothing was changed. Apply the migration in the ESPHome dashboard, "
                         "which is where this file is edited.")
    return {"migration": migration}


async def _tool_get_esphome_board(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: look up an ESPHome board's pins and hardware, or search boards."""
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_esphome_board"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "get_esphome_board"

    board_id = args.get("board_id")
    if isinstance(board_id, str) and board_id.strip():
        command = "boards/get_board"
        payload: dict = {"board_id": board_id.strip()}
        key = "board"
    else:
        command = "boards/get_boards"
        payload = _esphome_optional_args(args, ("query", "platform"))
        offset = args.get("offset")
        payload["offset"] = offset if isinstance(offset, int) and offset > 0 else 0
        payload["limit"] = _esphome_page_limit(args)
        key = "boards"

    try:
        res = await async_builder_command(
            hass, url, command, payload,
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, "get_esphome_board")

    return (
        _tool_success(json.dumps({key: res.result}, default=str)),
        "allowed", "get_esphome_board",
    )


async def _tool_get_esphome_component(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: look up ESPHome component schemas, search them, or list categories."""
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_esphome_component"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "get_esphome_component"

    include_advanced = args.get("include_advanced", False)
    if not isinstance(include_advanced, bool):
        return _tool_error("include_advanced must be a boolean."), "invalid_request", "get_esphome_component"
    ids = args.get("component_ids")
    file_arg = args.get("file")
    if file_arg is not None and not (isinstance(file_arg, str) and not file_arg.strip()):
        if isinstance(ids, list) and ids:
            return (_tool_error("Ask for a file's components and for component_ids in separate calls."),
                    "invalid_request", "get_esphome_component")
        return await _esphome_config_components(hass, access_key, url, args)
    if isinstance(ids, list) and ids:
        wanted = [i.strip() for i in ids if isinstance(i, str) and i.strip()]
        if len(wanted) > ESPHOME_BUILDER_MAX_COMPONENT_IDS:
            return (
                _tool_error(
                    f"Ask for at most {ESPHOME_BUILDER_MAX_COMPONENT_IDS} components at a time."
                ),
                "invalid_request", "get_esphome_component",
            )
        command = "components/get_component_bodies"
        payload: dict = {"component_ids": wanted}
        payload.update(_esphome_optional_args(args, ("platform", "board_id")))
        key = "components"
    elif any(
        isinstance(v, str) and v.strip()
        for v in (args.get("query"), args.get("category"))
    ):
        command = "components/get_components"
        payload = _esphome_optional_args(
            args, ("query", "category", "exclude_category", "platform", "board_id"))
        offset = args.get("offset")
        payload["offset"] = offset if isinstance(offset, int) and offset > 0 else 0
        payload["limit"] = _esphome_page_limit(args)
        key = "components"
    else:
        command = "components/get_categories"
        payload = _esphome_optional_args(args, ("board_id",))
        key = "categories"

    try:
        res = await async_builder_command(
            hass, url, command, payload,
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, "get_esphome_component")

    result = res.result
    if command == "components/get_component_bodies" and not include_advanced:
        result = _esphome_without_advanced(result)
    return (
        _tool_success(json.dumps({key: result}, default=str)),
        "allowed", "get_esphome_component",
    )


def _esphome_without_advanced(value: Any) -> Any:
    """Drop advanced and YAML-only config entries at every depth.

    The Device Builder MCP get_component's filter: an entry with a truthy `advanced` or `hidden`
    flag leaves its `config_entries` list.
    """
    if isinstance(value, list):
        return [_esphome_without_advanced(item) for item in value]
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("config_entries"), list):
        value = value | {"config_entries": [
            entry for entry in value["config_entries"]
            if not (isinstance(entry, dict) and (entry.get("hidden") or entry.get("advanced")))
        ]}
    return {k: _esphome_without_advanced(v) for k, v in value.items()}


# Integration and platform ids as the add-on reports a build's loaded set.
_ESPHOME_LOADED_NAME_RE = re.compile(r"^[a-z0-9_.]{1,64}$")


def _esphome_declared_for_file(path: str) -> dict | None:
    """The components a file declares at its own top level, or None if it will not parse. Executor only."""
    try:
        return declared_components(_read_text_capped(path))
    except (OSError, ValueError, yaml_includes.YamlParseError):
        return None


async def _esphome_config_components(
    hass: HomeAssistant, access_key: AccessKeyRecord, url: str, args: dict
) -> tuple[dict, str, str]:
    """get_esphome_component with a file: the components that configuration uses.

    `declared` comes from the file (names only) and misses packages; `loaded` is what the last build
    compiled, empty until the first compile.
    """
    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, "get_esphome_component")
    if refusal is not None:
        return refusal
    body: dict[str, Any] = {"file": rel}
    notes = []
    declared = await hass.async_add_executor_job(_esphome_declared_for_file, path)
    if declared is None:
        notes.append("The file could not be parsed, so its declared components are not listed.")
    else:
        body["declared"] = declared
        if declared.get("uses_includes"):
            notes.append("Components pulled in through packages or !include appear only under loaded.")
    try:
        res = await async_builder_command(hass, url, "devices/list", {},
                                          timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS)
        configured = res.result.get("configured") if isinstance(res.result, dict) else None
    except BuilderError:
        _LOGGER.debug("ESPHome device list for loaded components failed", exc_info=True)
        configured = None
    if not isinstance(configured, list):
        notes.append("The Device Builder did not report this device's last build.")
    else:
        rows = [d for d in configured if isinstance(d, dict)]
        located = await hass.async_add_executor_job(
            _locate_esphome_files, hass, [d.get("configuration") for d in rows])
        own = next((d for d in rows if isinstance(d.get("configuration"), str)
                    and located.get(d["configuration"], ("", ""))[1] == rel), None)
        if own is not None:
            loaded: dict[str, list[str]] = {}
            for key, field in (("integrations", "loaded_integrations"), ("platforms", "loaded_platforms")):
                names = own.get(field)
                loaded[key] = sorted({name for name in (names if isinstance(names, list) else [])
                                      if isinstance(name, str) and _ESPHOME_LOADED_NAME_RE.fullmatch(name)}
                                     )[:ESPHOME_BUILDER_LOADED_COMPONENTS_MAX]
            body["loaded"] = loaded
            if not body["loaded"]["integrations"]:
                notes.append("loaded is empty until the device has been compiled once.")
    if notes:
        body["notes"] = notes
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


# Fields kept from the Device Builder's automation catalog, per section. An
# ALLOWLIST rather than a drop-list, matching radio.py: a future upstream field
# cannot bloat the response by default. Dropped are the prose (`description`,
# `docs_url`), `form_editable` (a UI-editor concern), and, on the three catalog
# sections, `name`: it is a prettified restatement of the id the caller already
# has ("Cc1101: Set Channel" for cc1101.set_channel), useful in the add-on's
# own dropdowns and redundant to an agent, which writes the id. `devices` KEEPS
# its name, which is the operator's own entity name rather than a derived one.
# The full payload runs to tens of KB, enough to swamp an agent's context. Every
# ROW is kept, since a catalog that silently omits entries is worse than a large one.
_ESPHOME_AUTOMATION_FIELDS: dict[str, tuple[str, ...]] = {
    "triggers": ("id", "applies_to", "is_device_level", "supports_list"),
    "actions": ("id", "domain", "is_control_flow", "has_else_branch",
                "accepts_action_list"),
    "conditions": ("id", "domain", "accepts_condition_list"),
    "devices": ("id", "component_id", "name", "title", "has_explicit_id",
                "parent_id", "is_entity_container"),
}


def _project_esphome_automations(result: Any) -> Any:
    """Trim the automation catalog to the fields that shape how config is written.

    Empty and false values are dropped. `scripts` passes through whole, being the device's own
    declarations.
    """
    if not isinstance(result, dict):
        return result
    out: dict[str, Any] = {}
    for section, rows in result.items():
        keep = _ESPHOME_AUTOMATION_FIELDS.get(section)
        if keep is None or not isinstance(rows, list):
            out[section] = rows
            continue
        out[section] = [
            {k: row[k] for k in keep if row.get(k) not in (None, [], {}, False)}
            if isinstance(row, dict) else row
            for row in rows
        ]
    return out


async def _tool_get_esphome_automations(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: a device's automation catalog, its existing automations, or building-block docs."""
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_esphome_automations"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "get_esphome_automations"

    existing = args.get("existing", False)
    include_advanced = args.get("include_advanced", False)
    if not isinstance(existing, bool) or not isinstance(include_advanced, bool):
        return (_tool_error("existing and include_advanced must be booleans."),
                "invalid_request", "get_esphome_automations")
    if args.get("refs") is not None:
        if args.get("file") is not None or existing:
            return (_tool_error("refs looks up the catalog; ask about a file's automations in a separate call."),
                    "invalid_request", "get_esphome_automations")
        return await _esphome_automation_docs(hass, url, args.get("refs"), include_advanced)
    if args.get("file") is None:
        return _tool_error("file is required unless refs is given."), "invalid_request", "get_esphome_automations"

    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, "get_esphome_automations")
    if refusal is not None:
        return refusal
    if existing:
        return await _esphome_existing_automations(hass, access_key, url, path, rel)

    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)
    try:
        # Exactly the filename. The API also accepts a draft `yaml` body, which
        # would be the same unreviewed config-time evaluation validate refuses.
        res = await async_builder_command(
            hass, url, "automations/get_available", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")

    # Belt-and-braces: the catalog is component metadata rather than config
    # values, but it is derived from this file, so scrub the rendered body too.
    body = {"file": rel, "automations": _project_esphome_automations(res.result)}
    return _tool_success(_esphome_scrubbed_json(body, values)), "allowed", f"esphome:{rel}"


# Catalog ids as the add-on spells them, e.g. light.turn_on or on_press.
_ESPHOME_REF_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


async def _esphome_automation_docs(
    hass: HomeAssistant, url: str, refs: Any, include_advanced: bool
) -> tuple[dict, str, str]:
    """get_esphome_automations with refs: the fields each building block takes.

    Catalog metadata, scoped like the component catalog rather than to a file. The add-on pages
    through `remaining`; a page with no progress ends the loop.
    """
    resource = "get_esphome_automations"
    if not isinstance(refs, list) or not refs:
        return _tool_error("refs must be a nonempty list of {type, id} objects."), "invalid_request", resource
    wanted: list[dict[str, str]] = []
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) - {"type", "id"}
                or ref.get("type") not in ESPHOME_AUTOMATION_REF_TYPES
                or not isinstance(ref.get("id"), str) or not _ESPHOME_REF_ID_RE.fullmatch(ref["id"])):
            return (_tool_error("Each ref needs a type of " + ", ".join(sorted(ESPHOME_AUTOMATION_REF_TYPES))
                                + " and an id from get_esphome_automations."), "invalid_request", resource)
        if {"type": ref["type"], "id": ref["id"]} not in wanted:
            wanted.append({"type": ref["type"], "id": ref["id"]})
    if len(wanted) > ESPHOME_BUILDER_MAX_AUTOMATION_REFS:
        return (_tool_error(f"Ask for at most {ESPHOME_BUILDER_MAX_AUTOMATION_REFS} refs at a time."),
                "invalid_request", resource)

    bodies: dict[str, Any] = {}
    remaining = wanted
    for _page in range(ESPHOME_BUILDER_AUTOMATION_DOCS_MAX_PAGES):
        try:
            res = await async_builder_command(
                hass, url, "automations/get_bodies", {"refs": remaining},
                timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
            )
        except BuilderError as err:
            return _builder_refusal(err, resource)
        result = res.result if isinstance(res.result, dict) else {}
        page = result.get("bodies")
        if isinstance(page, dict):
            bodies.update({k: v for k, v in page.items() if isinstance(k, str)})
        reported = result.get("remaining")
        left = [ref for ref in ({"type": r.get("type"), "id": r.get("id")}
                                for r in (reported if isinstance(reported, list) else []) if isinstance(r, dict))
                if ref in remaining]
        if not left or len(left) >= len(remaining):
            remaining = left
            break
        remaining = left

    out: dict[str, Any] = {}
    not_listed = [f"{r['type']}/{r['id']}" for r in remaining]
    size = 0
    for ref in wanted:
        key = f"{ref['type']}/{ref['id']}"
        if key not in bodies or key in not_listed:
            continue
        doc = bodies[key] if include_advanced else _esphome_without_advanced(bodies[key])
        size += len(json.dumps(doc, default=str))
        if size > ESPHOME_BUILDER_OUTPUT_MAX_CHARS:
            not_listed.append(key)
            continue
        out[key] = doc
    body: dict[str, Any] = {"bodies": out}
    missing = [f"{r['type']}/{r['id']}" for r in wanted
               if f"{r['type']}/{r['id']}" not in bodies and f"{r['type']}/{r['id']}" not in not_listed]
    if missing:
        body["not_found"] = missing
    if not_listed:
        body["not_listed"] = not_listed
        body["note"] = "Ask for the refs under not_listed in another call."
    return _tool_success(json.dumps(body, default=str)), "allowed", resource


def _esphome_read_with_secrets(hass: HomeAssistant, path: str) -> tuple[str, set[str], set[str]]:
    """(raw text, secrets.yaml values, scrub values) for one device file. Executor only."""
    secret_values, _keys = _read_esphome_secrets(hass)
    secret_values = set(secret_values or set())
    raw = _read_text_capped(path)
    values = set(secret_values)
    try:
        values |= inline_secret_values(raw, secret_values)
    except yaml_includes.YamlParseError:
        pass
    return raw, secret_values, values


_ESPHOME_LOCATION_KEYS = ("kind", "component_id", "trigger", "index", "id", "field", "action_name")


def _esphome_same_slice(local: str, reported: str) -> bool:
    """Whether the add-on's raw_yaml holds the same data as the lines Phoenix MCP read.

    The add-on re-dumps the value, so both are compared as data; anything that will not load
    mismatches.
    """
    try:
        return (yaml_includes.load_tagged_lenient(textwrap.dedent(local))
                == yaml_includes.load_tagged_lenient(reported))
    except (yaml_includes.YamlParseError, ValueError, TypeError):
        return False


def _esphome_automation_rows(
    hass: HomeAssistant, raw: str, secret_values: set[str], values: set[str], parsed: Any, view: _EntityView,
) -> tuple[list[dict], list[str]]:
    """Project automations/parse rows, with YAML cut from Phoenix's own masked read. Executor only.

    The add-on's raw_yaml only checks that both read the same file and is never relayed. A row whose
    lines no longer match loses its YAML; an unparseable file withholds all YAML. Label and error
    stay unbounded for the event loop, where the scrub that precedes their bound runs.
    """
    rows: list[dict] = []
    warnings: list[str] = []
    parse_failed = False
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        location = item.get("location")
        if isinstance(location, dict):
            row["location"] = {
                k: (scrub_secret_values(v, values)[:ESPHOME_BUILDER_METADATA_MAX_CHARS] if isinstance(v, str) else v)
                for k, v in location.items()
                if k in _ESPHOME_LOCATION_KEYS and (isinstance(v, str) or (isinstance(v, int) and not isinstance(v, bool)))
            }
        for key in ("label", "error"):
            if isinstance(item.get(key), str) and item[key]:
                row[key] = scrub_secret_values(item[key], values)
        if item.get("unsupported") is True:
            row["unsupported"] = True
        first, last = item.get("from_line"), item.get("to_line")
        if not (isinstance(first, int) and isinstance(last, int)
                and not isinstance(first, bool) and not isinstance(last, bool)):
            rows.append(row)
            continue
        row["from_line"], row["to_line"] = first, last
        reported = item.get("raw_yaml")
        try:
            lines = raw.splitlines(keepends=True)
            local = "".join(lines[first - 1:last]) if 1 <= first <= last <= len(lines) else None
            if local is None or not isinstance(reported, str) or not _esphome_same_slice(local, reported):
                row["yaml_withheld"] = "The file changed while it was being read; ask again."
            elif not parse_failed:
                masked = _esphome_hide_entities(redact_esphome_slice(raw, secret_values, first, last), view, hass)
                if len(masked) > ESPHOME_AUTOMATION_YAML_MAX_CHARS:
                    masked = masked[:ESPHOME_AUTOMATION_YAML_MAX_CHARS]
                    row["yaml_truncated"] = True
                row["yaml"] = masked
        except yaml_includes.YamlParseError:
            parse_failed = True
        except ValueError:
            row["yaml_withheld"] = "The file changed while it was being read; ask again."
        rows.append(row)
    if parse_failed:
        for row in rows:
            row.pop("yaml", None)
            row.pop("yaml_truncated", None)
        warnings.append("The file could not be parsed, so its credentials cannot be masked and no YAML is shown.")
    return rows, warnings


async def _esphome_existing_automations(
    hass: HomeAssistant, access_key: AccessKeyRecord, url: str, path: str, rel: str
) -> tuple[dict, str, str]:
    """get_esphome_automations with existing: the automations the file already defines."""
    try:
        raw, secret_values, values = await hass.async_add_executor_job(_esphome_read_with_secrets, hass, path)
    except (OSError, ValueError):
        return _tool_error("Failed to read the ESPHome configuration file."), "invalid_request", f"esphome:{rel}"
    try:
        # Exactly the filename, never the API's optional draft `yaml` body.
        res = await async_builder_command(
            hass, url, "automations/parse", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    rows, warnings = await hass.async_add_executor_job(
        _esphome_automation_rows, hass, raw, secret_values, values, res.result, _esphome_entity_view(access_key, hass))
    for row in rows:
        for key in ("label", "error"):
            if key in row:
                row[key] = _esphome_metadata_text(row[key], access_key, hass)
    listed: list[dict] = []
    size = 0
    for row in rows:
        size += len(json.dumps(row, default=str))
        if size > ESPHOME_AUTOMATION_LIST_MAX_CHARS:
            break
        listed.append(row)
    body: dict[str, Any] = {"file": rel, "count": len(rows), "automations": listed}
    if len(listed) < len(rows):
        body["not_listed"] = [row.get("label") or row.get("location") for row in rows[len(listed):]]
        warnings.append("The result is full; read the rest of the file with get_esphome_yaml.")
    if warnings:
        body["warnings"] = warnings
    return _tool_success(_esphome_scrubbed_json(body, values)), "allowed", f"esphome:{rel}"


# Phoenix MCP only ever flashes over the air. A caller-supplied serial path would
# be a device-file access primitive, and a device that genuinely needs serial
# needs a person holding a cable anyway.
_ESPHOME_OTA_PORT = "OTA"


# Two sets, deliberately not complements of each other, because an unrecognized
# status must fail in a different direction in each place it is read.
#
# ACTIVE decides whether to refuse starting a new build. An unknown status counts
# as inactive, so a status Phoenix MCP has not seen before can never permanently
# wedge every build behind a job that will never look finished.
_ESPHOME_JOB_ACTIVE = frozenset({"queued", "running"})


# TERMINAL decides whether the log is fetched by replaying the finished job.
# An unknown status counts as NOT terminal, because following a job that is still
# running streams until it finishes, which would hold the request for the whole
# build; reading the live buffer instead merely risks an empty log.
_ESPHOME_JOB_TERMINAL = frozenset({"completed", "failed", "cancelled"})


_ESPHOME_JOB_NOT_FOUND = "No such firmware job."


# Appended ONLY where `file` is actually accepted, i.e. get_esphome_job: naming a
# recovery the caller cannot use (cancel takes an id only) would just send it
# round another loop. Same principle as DOMAIN_SERVICE_HINTS: an agent told only
# "no" retries variations, while one told the fix takes it.
#
# Whichever message applies, the unknown-id and out-of-scope branches return the
# SAME body and differ only in audit outcome, so the refusal is not an existence
# oracle, and the hint names no file the caller did not already supply.
_ESPHOME_JOB_NOT_FOUND_BY_FILE = (
    f"{_ESPHOME_JOB_NOT_FOUND} If you do not have a job_id, for example because "
    "the build was started in an earlier conversation, pass file instead (the "
    "device YAML filename) to get that device's most recent job."
)


# Every tool that starts a job shares this, because an agent does what the note
# NAMES, so it must name wait_for_esphome_job rather than polling.
# wait_for_esphome_job is the only thing that yields a progress line (it holds
# the request open, which is what drives the panel's activity renderer);
# get_esphome_job returns instantly and renders nothing, which would leave the
# operator watching a multi-minute build against a blank window.
_ESPHOME_FOLLOW_NOTE = (
    "To follow it, call wait_for_esphome_job with this job_id: it waits for the "
    "build and reports progress the operator can see, so relay what it says as it "
    "goes. Use get_esphome_job instead only to check back on it later."
)


def _esphome_job_list(result: Any) -> list[dict]:
    """Job records out of a get_jobs reply, whichever way it wraps them."""
    if isinstance(result, list):
        rows = result
    elif isinstance(result, dict):
        rows = result.get("jobs") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


async def _tool_list_esphome_jobs(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """List scoped firmware job metadata, with paging after authorization."""
    resource = "list_esphome_jobs"
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", resource
    status = args.get("status")
    if status is not None and (not isinstance(status, str)
                              or status not in _ESPHOME_JOB_ACTIVE | _ESPHOME_JOB_TERMINAL):
        return _tool_error("Invalid firmware job status."), "invalid_request", resource
    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", resource
    payload = {}
    if "file" in args:
        refusal, _path, rel = await _esphome_file_precheck(hass, access_key, args, resource)
        if refusal is not None:
            return refusal
        payload["configuration"] = rel
    try:
        result = await async_builder_command(hass, url, "firmware/get_jobs", payload,
                                            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS)
    except BuilderError as err:
        return _builder_refusal(err, resource)
    jobs = _esphome_job_list(result.result)
    located = await hass.async_add_executor_job(
        _locate_esphome_files, hass, [job.get("configuration") for job in jobs])
    declared = await _esphome_declared_for_scope(hass, [resolved[1] for resolved in located.values()])
    rows = []
    for job in jobs:
        configuration = job.get("configuration")
        resolved = located.get(configuration) if isinstance(configuration, str) else None
        if resolved is None:
            continue
        rel = resolved[1]
        if (not _esphome_file_in_scope(hass, access_key, rel, write=False, declared=declared.get(rel))
                or (payload and rel != payload["configuration"])
                or (status is not None and status != job.get("status"))):
            continue
        row = {"file": rel}
        # The add-on's own field names: a job record carries job_type and
        # completed_at, never type or finished_at.
        for key in ("job_id", "status", "job_type", "created_at", "started_at", "completed_at"):
            value = job.get(key)
            if isinstance(value, str):
                row[key] = _esphome_metadata_text(value, access_key, hass)
        rows.append(row)
    rows.sort(key=lambda row: (row.get("created_at", ""), row.get("job_id", "")), reverse=True)
    return _tool_success(json.dumps(_esphome_paged(rows, args, "jobs"))), "allowed", resource


# ESPHome version strings as the add-on reports them (2026.10.1, 2026.11.0b2).
_ESPHOME_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+\-]{0,31}$")


def _esphome_device_firmware(device: dict, runtime: Any) -> dict:
    """What a configured row says about its firmware: deployed, update, migration, armed.

    deployed_version lives in nested runtime_state, with the flat field as an older add-on's
    fallback. The update rule is HA's own: deployed against current.
    """
    state = runtime if isinstance(runtime, dict) else {}
    out: dict[str, Any] = {}
    deployed = state.get("deployed_version", device.get("deployed_version"))
    if isinstance(deployed, str) and _ESPHOME_VERSION_RE.fullmatch(deployed):
        out["deployed_version"] = deployed
        current = device.get("current_version")
        if isinstance(current, str) and _ESPHOME_VERSION_RE.fullmatch(current):
            out["update_available"] = deployed != current
    if isinstance(device.get("migration_available"), bool):
        out["migration_available"] = device["migration_available"]
    if state.get("queued_update") is True:
        out["update_armed_for_next_boot"] = True
    return out


async def _tool_list_esphome_devices(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """Project configured and opt-in importable Builder inventory without network identity."""
    resource = "list_esphome_devices"
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", resource
    query = args.get("query", "")
    include = args.get("include_importable", False)
    state = args.get("state")
    if (not isinstance(query, str) or len(query) > ESPHOME_YAML_SEARCH_MAX_QUERY_CHARS
            or not isinstance(include, bool) or (state is not None and (
                not isinstance(state, str) or state not in ESPHOME_BUILDER_DEVICE_STATES))):
        return _tool_error("Invalid device query, state, or include_importable flag."), "invalid_request", resource
    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", resource
    try:
        result = await async_builder_command(hass, url, "devices/list", {},
                                            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS)
    except BuilderError as err:
        return _builder_refusal(err, resource)
    if not isinstance(result.result, dict):
        return _tool_error("The Device Builder returned an unexpected device inventory."), "invalid_request", resource
    configured = result.result.get("configured")
    located = await hass.async_add_executor_job(
        _locate_esphome_files, hass,
        [d.get("configuration") for d in configured if isinstance(d, dict)] if isinstance(configured, list) else [],
    )
    declared = await _esphome_declared_for_scope(hass, [resolved[1] for resolved in located.values()])
    rows = []
    for kind in ("configured", "importable") if include else ("configured",):
        candidates = result.result.get(kind)
        for device in candidates if isinstance(candidates, list) else []:
            if not isinstance(device, dict):
                continue
            name = device.get("name")
            if not isinstance(name, str) or not _ESPHOME_NAME_RE.fullmatch(name):
                continue
            row = {"kind": kind, "name": name}
            if kind == "configured":
                configuration = device.get("configuration")
                resolved = located.get(configuration) if isinstance(configuration, str) else None
                if resolved is None:
                    continue
                rel = resolved[1]
                if not _esphome_file_in_scope(hass, access_key, rel, write=False, declared=declared.get(rel)):
                    continue
                if not _esphome_name_in_scope(hass, access_key, name):
                    continue
                row["file"] = rel
            elif not _esphome_name_in_scope(hass, access_key, name):
                # Discovery does not make an adopted but unloaded device an orphan.
                continue
            runtime = device.get("runtime_state")
            observed = runtime.get("state") if isinstance(runtime, dict) else device.get("state")
            row["state"] = observed.lower() if isinstance(observed, str) else "unknown"
            if state is not None and state != row["state"]:
                continue
            for key in ("friendly_name", "board_id", "target_platform", "current_version",
                        "project_name", "project_version"):
                value = device.get(key)
                if isinstance(value, str):
                    row[key] = _esphome_metadata_text(value, access_key, hass)
            if kind == "configured":
                row.update(_esphome_device_firmware(device, runtime))
            if query.casefold() not in json.dumps(row).casefold():
                continue
            rows.append(row)
    rows.sort(key=lambda row: (row["name"], row["kind"]))
    return _tool_success(json.dumps(_esphome_paged(rows, args, "devices"))), "allowed", resource


async def _esphome_active_job(hass: HomeAssistant, url: str, rel: str) -> dict | None:
    """A queued or running job for this configuration, if there is one.

    Best-effort: a read failure returns None rather than refusing, since the
    command that follows will surface a genuinely broken add-on on its own.
    """
    try:
        res = await async_builder_command(
            hass, url, "firmware/get_jobs", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError:
        _LOGGER.debug("ESPHome in-flight job check failed", exc_info=True)
        return None
    for job in _esphome_job_list(res.result):
        if job.get("status") in _ESPHOME_JOB_ACTIVE and job.get("job_id"):
            return job
    return None


async def _esphome_inflight_refusal(
    hass: HomeAssistant, url: str, rel: str, resource: str
) -> tuple[dict, str, str] | None:
    """Refuse to start a build while one is already in flight for this file.

    A second job does not queue: the add-on cancels the first and reaps its log, so a retry silently
    destroys the build it was retrying.
    """
    active = await _esphome_active_job(hass, url, rel)
    if active is None:
        return None
    return (
        _tool_error(
            f"A firmware job for this file is already {active.get('status')} "
            f"(job_id {active.get('job_id')}). Poll it with get_esphome_job, or stop it with "
            "cancel_esphome_job. Starting another one would cancel it and discard its log."
        ),
        "invalid_request",
        resource,
    )


async def _esphome_job_record(hass: HomeAssistant, url: str, job_id: str) -> dict | None:
    """One job's metadata, or None when the add-on does not know the id."""
    try:
        res = await async_builder_command(
            hass, url, "firmware/get_job", {"job_id": job_id},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderCommandError:
        return None
    return res.result if isinstance(res.result, dict) else None


async def _esphome_latest_job_for_file(hass: HomeAssistant, url: str, rel: str) -> dict | None:
    """The newest job for one configuration, or None if it has never been built."""
    try:
        res = await async_builder_command(
            hass, url, "firmware/get_jobs", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError:
        _LOGGER.debug("ESPHome latest-job lookup failed", exc_info=True)
        return None
    jobs = _esphome_job_list(res.result)
    if not jobs:
        return None
    # Sorted rather than trusting the reply's order, which is undocumented.
    # created_at is an ISO string, so a plain string sort is chronological; a
    # record missing it drops to the bottom instead of winning by accident.
    return max(jobs, key=lambda j: str(j.get("created_at") or ""))


async def _esphome_job_precheck(
    hass: HomeAssistant, access_key: AccessKeyRecord, url: str, args: dict, tool_name: str,
    allow_file: bool = False,
) -> tuple[tuple[dict, str, str] | None, dict]:
    """Resolve a job id and authorize the caller against the file it builds.

    An unknown id and an out-of-scope id return the same body. allow_file lets the caller name the
    configuration instead; cancel does not offer it.
    """
    # A job id is a handle to another device's build log, so it is scoped like the file. Naming the
    # configuration answers "is that build done?" with no remembered state, since a job id exists
    # only in the conversation that started the build. Stopping "whichever job is newest" is not
    # something a caller can have meant to name.
    job_id = args.get("job_id")
    has_file = isinstance(args.get("file"), str) and args["file"].strip()
    if allow_file and has_file and (not isinstance(job_id, str) or not job_id.strip()):
        refusal, _path, rel = await _esphome_file_precheck(hass, access_key, args, tool_name, write=False)
        if refusal is not None:
            return refusal, {}
        job = await _esphome_latest_job_for_file(hass, url, rel)
        if job is None:
            return (
                _tool_error(f"No firmware job has been started for {rel}."),
                "not_found", f"esphome:{rel}",
            ), {}
        return None, job
    if not isinstance(job_id, str) or not job_id.strip():
        message = "job_id or file is required." if allow_file else "job_id is required."
        return (_tool_error(message), "invalid_request", tool_name), {}
    # One message for every refusal below, so the three cases stay byte-identical.
    missing = _ESPHOME_JOB_NOT_FOUND_BY_FILE if allow_file else _ESPHOME_JOB_NOT_FOUND
    try:
        job = await _esphome_job_record(hass, url, job_id.strip())
    except BuilderError as err:
        return _builder_refusal(err, tool_name), {}
    if job is None:
        return (_tool_error(missing), "not_found", tool_name), {}
    # Separate name: `rel` is already bound to a str on the by-file branch above,
    # and the add-on's payload is untyped, so reusing it would widen that binding.
    configuration = job.get("configuration")
    if not isinstance(configuration, str) or not esphome_rel_path_ok(configuration):
        return (_tool_error(missing), "not_found", tool_name), {}
    rel = configuration
    declared = (await _esphome_declared_for_scope(hass, [rel])).get(rel)
    if not _esphome_file_in_scope(hass, access_key, rel, write=False, declared=declared):
        return (_tool_error(missing), "denied", f"esphome:{rel}"), {}
    return None, job


class _EsphomeJobOutput(NamedTuple):
    """A job's log as read back, plus what the read itself revealed."""

    text: str
    truncated: bool
    # False when a finished job's log could not be replayed at all.
    available: bool = True
    # The follow's terminal frame says the update is armed for the next wake.
    armed: bool = False


async def _esphome_job_output(
    hass: HomeAssistant, url: str, job: dict, values: set[str]
) -> _EsphomeJobOutput:
    """The job's log, fetched whichever way this job's state stores it.

    get_job returns a running job's output. A finished job needs a follow, which replays its sidecar
    file and ends on a frame carrying queued_update_armed, kept here.
    """
    if job.get("status") not in _ESPHOME_JOB_TERMINAL:
        # Through the same collector the streaming path uses, never a second
        # line handler: this buffer carries the add-on's literal ANSI escapes and
        # its own terminators, so hand-joining it yields visible colour codes with
        # a blank line between every line.
        text, truncated = collect_output_lines(
            job.get("output"), ESPHOME_BUILDER_JOB_PROGRESS_MAX_CHARS, head_ratio=0.0,
            scrub_output=_esphome_output_scrub(values),
        )
        return _EsphomeJobOutput(scrub_secret_values(text, values), truncated)

    try:
        res = await async_builder_command(
            hass, url, "firmware/follow_job", {"job_id": job.get("job_id")},
            timeout_seconds=ESPHOME_BUILDER_JOB_LOG_TIMEOUT_SECONDS,
            collect_output=True,
            max_chars=ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS,
            scrub_output=_esphome_output_scrub(values),
        )
    except BuilderError:
        # The status is the answer the caller actually needs; a missing log is
        # worth degrading over rather than failing the whole poll.
        _LOGGER.debug("ESPHome job log replay failed", exc_info=True)
        return _EsphomeJobOutput("", False, available=False)
    armed = isinstance(res.result, dict) and res.result.get("queued_update_armed") is True
    return _EsphomeJobOutput(scrub_secret_values(res.output, values), res.output_truncated, armed=armed)


async def _esphome_dependent_job(
    hass: HomeAssistant, url: str, rel: str, job_id: str
) -> dict | None:
    """The job queued behind this one, if any, such as an install's upload.

    Looks under `rel` first and then unfiltered: a chain's jobs do not always share a configuration.
    """
    for args in ({"configuration": rel}, {}):
        try:
            res = await async_builder_command(
                hass, url, "firmware/get_jobs", args,
                timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
            )
        except BuilderError:
            _LOGGER.debug("ESPHome dependent job lookup failed", exc_info=True)
            return None
        for job in _esphome_job_list(res.result):
            if job.get("depends_on") == job_id:
                return job
    return None


async def _tool_compile_esphome_firmware(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: start a firmware build for one device file.

    Never approval-gated: compiling writes no configuration and touches no device, adding build time
    rather than authority over validate, which runs the same evaluation.
    """
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "compile_esphome_firmware"
    if "force_local" in args and not isinstance(args["force_local"], bool):
        return _tool_error("force_local must be a boolean."), "invalid_request", "compile_esphome_firmware"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "compile_esphome_firmware"

    refusal, _path, rel = await _esphome_file_precheck(
        hass, access_key, args, "compile_esphome_firmware", write=True
    )
    if refusal is not None:
        return refusal

    inflight = await _esphome_inflight_refusal(hass, url, rel, f"esphome:{rel}")
    if inflight is not None:
        return inflight

    check_request_authority()
    refusal, _path, rel = await _esphome_file_precheck(
        hass, access_key, args, "compile_esphome_firmware", write=True)
    if refusal is not None:
        return refusal
    payload = {"configuration": rel}
    if "force_local" in args:
        payload["force_local"] = args["force_local"]
    try:
        res = await async_builder_command(
            hass, url, "firmware/compile", payload,
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    return _esphome_job_started(res.result, rel, install=False)


# ESPHome hostnames: lowercase letters, digits and hyphens, not starting or
# ending with one. Builder-reported names outside this shape are not listed.
_ESPHOME_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,29}[a-z0-9])?$")


async def _tool_clean_esphome_build(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: discard one configuration's build artifacts (a stale cache); never approval-gated.

    It also drops the shared PlatformIO cache, slowing every device's next build.
    firmware/reset_build_env stays unreachable: it cancels every in-flight job.
    """
    # Like compile, this writes no configuration file and cannot reach a device.
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "clean_esphome_build"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "clean_esphome_build"

    refusal, _path, rel = await _esphome_file_precheck(
        hass, access_key, args, "clean_esphome_build", write=True
    )
    if refusal is not None:
        return refusal

    # The add-on's clean CANCELS an in-flight build for this configuration before
    # wiping. Refusing instead keeps the rule that a build is never killed as a
    # side effect of asking for something else: cancel_esphome_job is how you
    # stop one, and it says so.
    inflight = await _esphome_inflight_refusal(hass, url, rel, f"esphome:{rel}")
    if inflight is not None:
        return inflight

    try:
        res = await async_builder_command(
            hass, url, "firmware/clean", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    return _esphome_job_started(res.result, rel, install=False, clean=True)


def _esphome_job_started(
    result: Any, rel: str, *, install: bool, clean: bool = False
) -> tuple[dict, str, str]:
    """Report a freshly enqueued job, with the polling instruction attached."""
    job = result if isinstance(result, dict) else {}
    job_id = job.get("job_id")
    if not job_id:
        return (
            _tool_error("The ESPHome Device Builder did not return a job."),
            "invalid_request",
            f"esphome:{rel}",
        )
    if install:
        note = (
            f"Compiling now. {_ESPHOME_FOLLOW_NOTE} The device is only flashed "
            "once the upload that follows the compile has finished."
        )
    elif clean:
        note = (
            f"Discarding this configuration's build artifacts. {_ESPHOME_FOLLOW_NOTE} "
            "Its next build will be a full one, and because the shared PlatformIO "
            "cache is dropped too, the next build of every other device is slower as well."
        )
    else:
        note = f"Building now. {_ESPHOME_FOLLOW_NOTE}"
    body = {
        "file": rel,
        "job_id": job_id,
        "status": job.get("status", "queued"),
        "note": note,
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


async def _install_esphome_precheck(
    hass: HomeAssistant, access_key: AccessKeyRecord, args: dict
) -> tuple[tuple[dict, str, str] | None, str, str]:
    """Everything install checks before it is allowed to enqueue anything.

    Shared by the gate path and the executor, so a change during the approval window is caught at
    apply time too.
    """
    if "force_local" in args and not isinstance(args["force_local"], bool):
        return ((_tool_error("force_local must be a boolean."), "invalid_request",
                 "install_esphome_firmware"), "", "")
    url = _esphome_builder_url(hass)
    if url is None:
        return (
            (_tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "install_esphome_firmware"),
            "", "",
        )
    refusal, _path, rel = await _esphome_file_precheck(
        hass, access_key, args, "install_esphome_firmware", write=True
    )
    if refusal is not None:
        return refusal, url, ""
    inflight = await _esphome_inflight_refusal(hass, url, rel, f"esphome:{rel}")
    if inflight is not None:
        return inflight, url, rel
    check_request_authority()
    refusal, _path, rel = await _esphome_file_precheck(
        hass, access_key, args, "install_esphome_firmware", write=True)
    if refusal is not None:
        return refusal, url, rel
    return None, url, rel


async def _install_esphome_mesa_gate(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    args: dict,
    rel: str,
    diff: Any,
    request_id: str,
    client_ip: str | None,
) -> tuple[dict, str, str] | list[str]:
    """Apply MESA to a firmware install on every adopted owner of a file.

    Returns the refusal or pending approval, else advisory warnings. Every registry entity of every
    owner is evaluated, and one refusal stops the install.
    """
    # Flashing replaces what the whole device runs, so owners are matched on the name and address
    # the file states too, which also binds the key's scope to them. An orphan file has no device
    # in Home Assistant yet, so no profile can cover it and a new device's install proceeds. That
    # needs proof (_esphome_file_is_orphan). Otherwise the owner is unknown, which warns in advisory
    # mode and is refused where policy must be met, a failed runtime included. An approval satisfies
    # Confirm for this exact action only.
    declared = await hass.async_add_executor_job(_read_declared_device, hass, rel)
    owners = _esphome_entries_for_file(hass, rel, declared)
    if not _esphome_file_in_scope(hass, access_key, rel, write=True, declared=declared):
        return _tool_error(_ESPHOME_NOT_FOUND), "denied", f"esphome:{rel}"
    registry = er.async_get(hass)
    entities = sorted({
        reg_entry.entity_id
        for entry in owners
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
    })
    if not entities and data.mesa is not None:
        if not owners and _esphome_file_is_orphan(hass, rel, declared):
            return []
        devices = dr.async_get(hass)
        device_ids = [
            device.id for entry in owners for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
        ]
        if not any(unresolved_context_refused(data, device_id) for device_id in (None, *device_ids)):
            note_mesa_advisory([UNRESOLVED_CONTEXT_WARNING])
            return [UNRESOLVED_CONTEXT_WARNING]
    outcome = await async_apply_mesa_to_call(
        hass,
        data,
        access_key,
        domain="esphome",
        service="install",
        service_data={},
        entities=entities,
        request_id=request_id or "install_esphome_firmware",
        client_ip=client_ip,
        session_id=request_id or "install_esphome_firmware",
        confirm_approved=_approved_exec_ctx.get(),
        approval_tool_name="install_esphome_firmware",
        approval_args=args,
        approval_diff=diff,
        require_all=True,
    )
    if outcome.blocked:
        fire_mesa_blocked_event(hass, access_key, outcome.blocked)
    if outcome.decision == "pending":
        return await _pending_or_inline(hass, data, access_key, outcome.approval)
    if outcome.decision == "deny":
        return _tool_error(MESA_DENIED_MESSAGE), "denied", f"esphome:{rel}"
    return outcome.warnings


async def _tool_install_esphome_firmware(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: build and flash one device's firmware (Confirm-eligible)."""
    # Capability gate first, so a denied access key gets a uniform Forbidden and never
    # learns anything about the file it named.
    # Installing builds from the device's YAML, so it needs that capability too.
    if (effective_cap(access_key, "cap_esphome_flash") == CAP_DENY
            or effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY):
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "install_esphome_firmware"

    pre, _url, rel = await _install_esphome_precheck(hass, access_key, args)
    if pre is not None:
        return pre

    diff = lambda: _build_diff_install_esphome_firmware(args, access_key, hass, data)
    refused = await _install_esphome_mesa_gate(
        hass, data, access_key, args, rel, diff, request_id, client_ip
    )
    if isinstance(refused, tuple):
        return refused
    blocked = await _gate(
        "cap_esphome_flash", access_key, hass, data,
        tool_name="install_esphome_firmware", args=args, request_id=request_id,
        client_ip=client_ip, diff=diff,
    )
    if blocked is not None:
        return blocked
    return await _execute_install_esphome_firmware(
        args, access_key, hass, data, request_id=request_id, client_ip=client_ip
    )


async def _execute_install_esphome_firmware(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Side-effect path for install_esphome_firmware."""
    # A capability approval checks cap_esphome_flash again, but a MESA approval
    # carries no capability of its own, so both are read here.
    if (effective_cap(access_key, "cap_esphome_flash") == CAP_DENY
            or effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY):
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "install_esphome_firmware"
    pre, url, rel = await _install_esphome_precheck(hass, access_key, args)
    if pre is not None:
        return pre
    advisory = await _install_esphome_mesa_gate(
        hass, data, access_key, args, rel,
        lambda: _build_diff_install_esphome_firmware(args, access_key, hass, data),
        request_id, client_ip,
    )
    if isinstance(advisory, tuple):
        return advisory
    return _with_mesa_advisory(await _install_esphome_firmware(hass, args, url, rel), advisory)


async def _install_esphome_firmware(
    hass: HomeAssistant, args: dict, url: str, rel: str
) -> tuple[dict, str, str]:
    """Queue the install after every gate."""
    payload = {"configuration": rel, "port": _ESPHOME_OTA_PORT}
    if "force_local" in args:
        payload["force_local"] = args["force_local"]
    try:
        res = await async_builder_command(
            hass, url, "firmware/install",
            payload,
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    return _esphome_job_started(res.result, rel, install=True)


def _esphome_version_jump(installed: Any, target: Any) -> str | None:
    """Return "old to new" when two ESPHome versions differ beyond the patch, else None."""
    # An over-the-air update rewrites only the app partition, never the bootloader or partition
    # table, so a version change runs the new app on the old bootloader, and the approval card
    # otherwise shows both numbers without saying so. The warning states that verifiable gap and
    # deliberately predicts no unbootable device: a consequence that never arrives teaches the
    # operator to click past it.
    if not isinstance(installed, str) or not isinstance(target, str):
        return None
    left, right = installed.split(".")[:2], target.split(".")[:2]
    if len(left) < 2 or len(right) < 2 or left == right:
        return None
    return f"{installed} to {target}"


_ESPHOME_VERSION_JUMP_RISK = (
    "An over-the-air update rewrites only the app, never the bootloader or the "
    "partition table, so an ESPHome version change can leave a new app running "
    "on the bootloader the device was originally cabled with. Whatever that "
    "bootloader does not support stays unsupported: a device whose log reports "
    "'Bootloader rollback: not supported' cannot self-revert from a bad update. "
    "Flashing over a cable once updates the bootloader."
)


def _build_diff_install_esphome_firmware(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> dict:
    """Diff payload for install_esphome_firmware approvals.

    Best-effort throughout: this runs before the gate and must never raise, so a
    device that cannot be resolved produces a thinner card.
    """
    rel = args.get("file") if isinstance(args.get("file"), str) else ""
    preview: dict[str, Any] = {"file": rel}
    if isinstance(args.get("force_local"), bool):
        preview["force_local"] = args["force_local"]
    label = rel or "an ESPHome device"
    target_id = rel

    entry = _esphome_device_for_file(hass, rel) if rel else None
    if entry is not None:
        runtime = getattr(entry, "runtime_data", None)
        device_info = getattr(runtime, "device_info", None)
        name = getattr(device_info, "name", None)
        if name:
            label = name
            target_id = name
        installed = getattr(device_info, "esphome_version", None)
        if installed:
            preview["installed_version"] = installed
        dashboard = _esphome_dashboard(hass)
        configured = (getattr(dashboard, "data", None) or {}).get(name) or {}
        if configured.get("current_version"):
            preview["version_to_install"] = configured["current_version"]
        online = bool(getattr(runtime, "available", False))
        preview["device_online"] = online
        if not online:
            preview["deferred"] = (
                "The device is offline, so the flash will be armed for its next wake "
                "rather than applied now."
            )
        # Admin-only surface, so the full blast radius is listed rather than only
        # the entities this access key can see.
        entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        preview["entities"] = sorted(e.entity_id for e in entries)
        mesa_modes = {}
        for reg_entry in entries:
            mode = entity_control_mode(data.mesa, access_key, reg_entry.entity_id)
            if mode is not None:
                mesa_modes[reg_entry.entity_id] = mode
        if mesa_modes:
            preview["mesa_control_modes"] = mesa_modes
    else:
        preview["device"] = "No live device maps to this file yet."

    preview["warning"] = (
        "Replaces the firmware the device is running. A bad image can leave it "
        "unreachable until it is reflashed over a cable."
    )

    # The version jump rides in the SUMMARY as well as the preview, because the
    # summary is the bold line and the History-visible marker (the same reasoning
    # as the MESA involvement note), while the preview renders as a YAML dump an
    # approver can skim past.
    jump = _esphome_version_jump(
        preview.get("installed_version"), preview.get("version_to_install")
    )
    if jump:
        preview["version_change"] = jump
        preview["version_change_risk"] = _ESPHOME_VERSION_JUMP_RISK

    fields = (
        _summary("install_esphome_firmware.jump", label=label, jump=jump)
        if jump else _summary("install_esphome_firmware", label=label)
    )
    return {
        "kind": "system_action",
        **fields,
        "target": {"type": "device", "id": target_id, "label": label},
        "preview": preview,
    }


async def _tool_get_esphome_job(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, client_ip: str | None = None
) -> tuple[dict, str, str]:
    """MCP tool: report a firmware job's status and log."""
    args, error = normalize_tool_args("get_esphome_job", args)
    if error:
        return _tool_error(error), "invalid_request", "get_esphome_job"
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_esphome_job"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "get_esphome_job"

    refusal, job = await _esphome_job_precheck(
        hass, access_key, url, args, "get_esphome_job", allow_file=True)
    if refusal is not None:
        return refusal
    # Carries the same stop-here steer as the wait tool on a headless surface:
    # steering only the tool a model was told to stop calling just moves its
    # polling loop here, spending the whole per-turn round budget.
    note = _ESPHOME_HEADLESS_STOP if client_ip in _ESPHOME_HEADLESS_CLIENT_IPS else None
    return await _esphome_job_result(hass, url, job, note)


async def _esphome_job_result(
    hass: HomeAssistant, url: str, job: dict, unfinished_note: str | None = None
) -> tuple[dict, str, str]:
    """Build the reply for one job. Shared so waiting returns the same shape.

    unfinished_note rides only on a job that has NOT finished, so a caller that
    gave up waiting can say why and what to do next.
    """
    rel = job["configuration"]
    status = job.get("status")
    finished = status in _ESPHOME_JOB_TERMINAL
    if job.get("job_type") == "analyze_memory":
        # The add-on keeps no memory analysis once it ends, and its report goes
        # only to the follow attached by the call that started it. Replaying the
        # log here would race the record's removal.
        body_analysis = {"job_id": job.get("job_id"), "file": rel, "status": status,
                         "finished": finished, "note": _ESPHOME_ANALYZE_JOB_NOTE}
        return _tool_success(json.dumps(body_analysis, default=str)), "allowed", f"esphome:{rel}"
    path = os.path.join(hass.config.config_dir, _ESPHOME_DIR, rel)
    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)
    out = await _esphome_job_output(hass, url, job, values)

    body: dict[str, Any] = {
        "job_id": job.get("job_id"),
        "file": rel,
        "status": status,
        "finished": finished,
        "output": out.text,
    }
    if out.truncated:
        body["output_truncated"] = True
    if not out.available:
        body["output_available"] = False
    for key in ("progress", "exit_code"):
        if job.get(key) is not None:
            body[key] = job[key]
    if isinstance(job.get("failure_reason"), str) and _ESPHOME_FAILURE_REASON_RE.fullmatch(job["failure_reason"]):
        body["failure_reason"] = job["failure_reason"]
    # The add-on's own failure text, from the same process that wrote the log,
    # so it gets the same scrub.
    if isinstance(job.get("error"), str) and job["error"]:
        body["error"] = scrub_secret_values(job["error"], values)

    # Only ever claim the device was flashed on the evidence of a COMPLETED
    # upload job. An install returns its compile job alone, so a finished compile
    # says nothing about the device, and an install deferred to the device's next
    # wake produces no upload job at all.
    if finished and status == "completed":
        upload = await _esphome_dependent_job(hass, url, rel, str(job.get("job_id") or ""))
        upload_status = upload.get("status") if upload is not None else None
        if upload is not None:
            body["upload"] = {
                "job_id": upload.get("job_id"),
                "status": upload_status,
                "finished": upload_status in _ESPHOME_JOB_TERMINAL,
            }
            body["flashed"] = upload_status == "completed"
            if upload_status in _ESPHOME_JOB_ACTIVE:
                body["note"] = "Compiled. The device is being flashed now; poll the upload job."
        # The add-on arms the update for the device's next wake in three shapes,
        # and only the first has no upload job: the install found the device
        # offline at the start, the device went offline during the build (the
        # compile is flagged and its held upload CANCELLED), or the upload failed
        # with the device offline (the upload is flagged). The flag is
        # is_deferred_install on whichever job carries it; queued_update_armed
        # only appears on a follow's terminal frame, never on a job record.
        armed = (
            (upload is None or upload_status == "cancelled")
            and bool(job.get("is_deferred_install") or job.get("queued_update_armed") or out.armed)
        ) or (upload is not None and upload_status == "failed"
              and bool(upload.get("is_deferred_install")))
        if armed:
            body["flashed"] = False
            body["armed_for_next_boot"] = True
            body["note"] = (
                "Compiled, but the device was offline, so the flash is armed for its "
                "next wake and has NOT been applied yet."
            )
    if not finished and unfinished_note:
        body["note"] = unfinished_note
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


# Surfaces with no way to show a held request: Assist's tool bridge, the voice
# conversation agent, and AI Task. Agent Chat is deliberately NOT here (it owns
# both ends of its own stream and renders the progress line), and neither is a
# real MCP client, which asked for a long call by calling this tool.
_ESPHOME_HEADLESS_CLIENT_IPS = frozenset({
    ASSIST_CLIENT_IP, VOICE_AGENT_CLIENT_IP, AI_TASK_CLIENT_IP,
})


# Attached to any UNFINISHED job reply on those surfaces. Written imperatively
# and with the reason stated: a softer wording ("ask them to check back, or call
# get_esphome_job on the next turn") reads as permission to poll immediately,
# which spends every round and ends the turn with nothing to say. The turn itself
# must end.
_ESPHOME_HEADLESS_STOP = (
    "This surface cannot hold a request open while a build runs, and it allows "
    "only a limited number of tool calls per turn. THE BUILD HAS NOT FINISHED: "
    "this reply says nothing about whether it succeeded, only that it is still "
    "going. STOP CALLING TOOLS NOW and answer the user: say it is still building "
    "and ask them to check back in a few minutes. Do NOT poll this job again in "
    "this turn: polling spends the remaining calls and the turn then ends with no "
    "reply at all. When the user next asks, call get_esphome_job ONCE with the "
    "file name; you do not need to remember the job id."
)


def _clamp_job_wait(value: Any, client_ip: str | None = None) -> int:
    """Clamp a caller-supplied wait, defaulting to the maximum.

    Headless conversational surfaces get a short wait and are told to ask again.
    """
    # A build finishing inside one call is the point, so the ceiling is the useful default. A voice
    # turn or Assist reply has no stream for progress and a far shorter pipeline timeout, so a
    # full-length hold buys silence and then an error.
    if client_ip in _ESPHOME_HEADLESS_CLIENT_IPS:
        return ESPHOME_BUILDER_JOB_WAIT_HEADLESS_SECONDS
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return ESPHOME_BUILDER_JOB_WAIT_SECONDS
    return min(value, ESPHOME_BUILDER_JOB_WAIT_SECONDS)


def _esphome_progress_message(job: dict, rel: str, phase: str) -> str:
    """The human-readable line a client shows while a build runs, including the percentage."""
    # The percentage never goes in the protocol's numeric progress field, which MCP requires to
    # increase on every notification. The add-on's number resets at the compile-to-upload seam and
    # can read nonzero on a completed job; elapsed seconds carries the monotonic guarantee.
    pct = job.get("progress")
    if isinstance(pct, int) and not isinstance(pct, bool) and 0 <= pct <= 100:
        return f"{phase} {rel}: {pct}%"
    return f"{phase} {rel}"


async def _tool_wait_for_esphome_job(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, client_ip: str | None = None
) -> tuple[dict, str, str]:
    """MCP tool: hold until a firmware job finishes, reporting progress meanwhile.

    Follows the whole chain, picking up an install's dependent upload, and returns only once the
    device has been flashed.
    """
    # Compile and install return immediately by design, so only a held request gives progress an
    # in-flight call to ride on.
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "wait_for_esphome_job"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "wait_for_esphome_job"

    refusal, job = await _esphome_job_precheck(hass, access_key, url, args, "wait_for_esphome_job")
    if refusal is not None:
        return refusal
    if job.get("job_type") == "analyze_memory":
        # Nothing to wait for: the add-on drops the job and its report the moment
        # it ends, so a wait would only ever end on a vanished record.
        return await _esphome_job_result(hass, url, job)

    started = job
    rel = job["configuration"]
    timeout = _clamp_job_wait(args.get("timeout"), client_ip)
    watching, phase = job, "Compiling"
    elapsed = 0.0

    try:
        while elapsed < timeout:
            status = watching.get("status")
            if status in _ESPHOME_JOB_TERMINAL:
                if status != "completed":
                    break
                # A completed compile is not the end of an install: the upload it
                # queued behind itself is where the device actually changes.
                nxt = await _esphome_dependent_job(hass, url, rel, str(watching.get("job_id") or ""))
                if nxt is None or nxt.get("status") in _ESPHOME_JOB_TERMINAL:
                    break
                watching, phase = nxt, "Flashing"
                continue

            _set_progress_status(
                _esphome_progress_message(watching, rel, phase), total=float(timeout),
                key=(
                    "agentchat.progress.esphomeFlashing"
                    if phase == "Flashing"
                    else "agentchat.progress.esphomeCompiling"
                ),
                params={
                    "file": rel,
                    "progress": (
                        f": {watching['progress']}%"
                        if isinstance(watching.get("progress"), int)
                        and not isinstance(watching.get("progress"), bool)
                        and 0 <= watching["progress"] <= 100
                        else ""
                    ),
                },
            )
            await asyncio.sleep(ESPHOME_BUILDER_JOB_POLL_SECONDS)
            elapsed += ESPHOME_BUILDER_JOB_POLL_SECONDS

            try:
                fresh = await _esphome_job_record(hass, url, str(watching.get("job_id") or ""))
            except BuilderUnavailable:
                # A restart or one slow reply says nothing about the build, which
                # runs on inside the add-on, so the wait keeps its budget.
                continue
            except BuilderError as err:
                return _builder_refusal(err, f"esphome:{rel}")
            if fresh is None:
                # The add-on forgot the job mid-wait (a re-submission evicts the
                # previous record). Report what the original job looks like now
                # rather than pretending the wait succeeded.
                break
            watching = fresh
    finally:
        _set_progress_status(None)

    # Always answer about the job the caller named, so the reply carries the
    # upload block and the flashed verdict rather than a bare upload status.
    try:
        current = await _esphome_job_record(hass, url, str(started.get("job_id") or "")) or started
    except BuilderUnavailable:
        return _tool_error(_ESPHOME_BUILDER_WAIT_LOST), "invalid_request", f"esphome:{rel}"
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    # One shared wording with get_esphome_job: two divergent versions of "stop
    # here" leave a gap a model can exploit.
    note = _ESPHOME_HEADLESS_STOP if client_ip in _ESPHOME_HEADLESS_CLIENT_IPS else None
    content, outcome, resource = await _esphome_job_result(hass, url, current, note)
    return content, outcome, resource


async def _tool_cancel_esphome_job(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: stop a queued or running firmware job; never approval-gated.

    The build would finish before a click landed. An interrupted upload leaves the device on its old
    firmware, since ESPHome swaps partitions on completion.
    """
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "cancel_esphome_job"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "cancel_esphome_job"

    refusal, job = await _esphome_job_precheck(hass, access_key, url, args, "cancel_esphome_job")
    if refusal is not None:
        return refusal

    rel = job["configuration"]
    body: dict[str, Any] = {"job_id": job.get("job_id"), "file": rel, "cancelled": True}
    try:
        await async_builder_command(
            hass, url, "firmware/cancel", {"job_id": job.get("job_id")},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderCommandError:
        # The add-on answers not_found for an id it no longer has, which for a
        # job that already finished is the outcome the caller wanted anyway.
        body.update(cancelled=False, message="The job was already finished.")
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    status = await _esphome_settled_status(hass, url, str(job.get("job_id")))
    # Confirmed only by the job reading back as finished; an unreadable job is unknown.
    body["status"] = status
    body["verified"] = None if status is None else status in _ESPHOME_JOB_TERMINAL
    if status is not None and status not in _ESPHOME_JOB_TERMINAL:
        body["message"] = ("The add-on accepted the cancel, but the job still reports " + status
                           + "; check it with get_esphome_job.")
    return _tool_success(json.dumps(body)), "allowed", f"esphome:{rel}"


async def _esphome_settled_status(hass: HomeAssistant, url: str, job_id: str) -> str | None:
    """The job's status once it reads back finished, its last status after a short wait, or None if unreadable."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ESPHOME_BUILDER_CANCEL_SETTLE_SECONDS
    while True:
        try:
            record = await _esphome_job_record(hass, url, job_id)
        except BuilderError:
            return None
        status = record.get("status") if record is not None else None
        if not isinstance(status, str) or status in _ESPHOME_JOB_TERMINAL or loop.time() >= deadline:
            return status if isinstance(status, str) else None
        await asyncio.sleep(ESPHOME_BUILDER_CANCEL_POLL_SECONDS)


async def _tool_get_esphome_device_logs(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: capture a bounded window of a device's live console output.

    Read scope on the device, which is the unit of authorization: the console can be more detailed
    than the entities HA exposes.
    """
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_esphome_device_logs"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "get_esphome_device_logs"

    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, "get_esphome_device_logs")
    if refusal is not None:
        return refusal

    seconds = _esphome_capture_seconds(args)
    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)
    _set_progress_status(
        f"Capturing {rel} device logs", total=float(seconds),
        key="agentchat.progress.esphomeLogs", params={"file": rel},
    )
    try:
        res = await async_builder_capture(
            hass, url, "devices/logs", {"configuration": rel},
            capture_seconds=seconds,
            max_chars=ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS,
            scrub_output=_esphome_output_scrub(values),
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    finally:
        _set_progress_status(None)

    body: dict[str, Any] = {
        "file": rel,
        "captured_seconds": seconds,
        "output": scrub_secret_values(res.output, values),
    }
    if res.output_truncated:
        body["output_truncated"] = True
    if not res.output:
        body["message"] = "The device logged nothing during the capture window."
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


def _esphome_capture_seconds(args: dict) -> int:
    """Clamp a caller-supplied capture window, defaulting nonsense to the default."""
    raw = args.get("seconds")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        return ESPHOME_BUILDER_LOG_CAPTURE_SECONDS
    return min(raw, ESPHOME_BUILDER_LOG_CAPTURE_MAX_SECONDS)


async def _tool_decode_esphome_backtrace(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: symbolize an ESP32 crash backtrace against the device's build."""
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "decode_esphome_backtrace"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "decode_esphome_backtrace"

    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, "decode_esphome_backtrace")
    if refusal is not None:
        return refusal

    raw = args.get("lines")
    lines = [line for line in raw if isinstance(line, str)] if isinstance(raw, list) else []
    if not lines:
        return (
            _tool_error("Pass the backtrace lines from the device log in `lines`."),
            "invalid_request",
            f"esphome:{rel}",
        )
    if len(lines) > ESPHOME_BUILDER_MAX_BACKTRACE_LINES:
        return (
            _tool_error(
                f"Pass at most {ESPHOME_BUILDER_MAX_BACKTRACE_LINES} backtrace lines at a time."
            ),
            "invalid_request",
            f"esphome:{rel}",
        )

    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)
    try:
        res = await async_builder_command(
            hass, url, "devices/decode_backtrace", {"configuration": rel, "lines": lines},
            timeout_seconds=ESPHOME_BUILDER_DECODE_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")

    payload = res.result if isinstance(res.result, dict) else {}
    body = {
        "file": rel,
        "decoded": payload.get("decoded") or [],
        "stale_build": bool(payload.get("stale_build")),
    }
    # The add-on reports "" here on success, so only a real reason is worth
    # putting in front of the agent.
    if payload.get("unavailable_reason"):
        body["unavailable_reason"] = payload["unavailable_reason"]
    return _tool_success(_esphome_scrubbed_json(body, values)), "allowed", f"esphome:{rel}"


# The add-on's own failure classification ("" or "provision" in 1.25.0).
_ESPHOME_FAILURE_REASON_RE = re.compile(r"^[a-z0-9_]{1,64}$")


_ESPHOME_ANALYZE_JOB_NOTE = (
    "This is a memory analysis. The Device Builder does not keep it once it ends, "
    "and its report goes only to the analyze_esphome_memory call that started it."
)


# Git commit ids, short or full.
_ESPHOME_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


async def _esphome_history_precheck(
    hass: HomeAssistant, access_key: AccessKeyRecord, args: dict, tool_name: str
) -> tuple[tuple[dict, str, str] | None, str, str, bool]:
    """Resolve and authorize a device file that may no longer exist.

    The usual jail and name rules apply without existence. A deleted file declares nothing, so past
    an owner found by name it needs every adopted device in scope; a proven orphan follows the
    capability.
    """
    located = await hass.async_add_executor_job(_locate_esphome_file, hass, args.get("file"))
    if located is None:
        return (_tool_error(_ESPHOME_PATH_REFUSED), "invalid_request", tool_name), "", "", False
    path, rel, is_file = located
    declared = (await _esphome_declared_for_scope(hass, [rel])).get(rel)
    if not _esphome_file_in_scope(hass, access_key, rel, write=False, declared=declared):
        return (_tool_error(_ESPHOME_NOT_FOUND), "denied", f"esphome:{rel}"), path, rel, is_file
    return None, path, rel, is_file


def _esphome_history_names(hass: HomeAssistant, names: list[Any]) -> list[str]:
    """Builder-reported deleted names that pass the jail, as esphome-relative paths. Executor only."""
    out = []
    for name in names:
        resolved = _resolve_esphome_path(hass, name)
        if resolved is not None and resolved[1] not in out:
            out.append(resolved[1])
    return out


def _esphome_mask_version(hass: HomeAssistant, text: str, view: _EntityView) -> tuple[str, list[str]]:
    """Mask one historical version whole. Executor only; raises on oversize or unparseable text."""
    # A scrub alone would not do: an old version can carry a credential since rotated out of
    # secrets.yaml and every current file.
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("version too large")
    secret_values, _keys = _read_esphome_secrets(hass)
    return _esphome_masked_text(hass, text, set(secret_values or set()), view)


def _esphome_mask_current(hass: HomeAssistant, path: str, view: _EntityView) -> str:
    """The current file, masked; "" when it no longer exists. Executor only."""
    if not os.path.isfile(path):
        return ""
    return _esphome_mask_version(hass, _read_text_capped(path), view)[0]


async def _esphome_versions(hass: HomeAssistant, url: str, rel: str) -> list[dict] | None:
    """The file's commits, newest first, or None when the Builder cannot be reached."""
    try:
        res = await async_builder_command(
            hass, url, "version_history/list_versions", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderCommandError:
        return []
    except BuilderError:
        return None
    return [c for c in res.result if isinstance(c, dict)] if isinstance(res.result, list) else []


async def _esphome_version_text(hass: HomeAssistant, url: str, rel: str, sha: str) -> str | None:
    """The file's raw text at one commit, or None when the file is absent there."""
    try:
        res = await async_builder_command(
            hass, url, "version_history/get_version", {"configuration": rel, "sha": sha},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderCommandError:
        return None
    content = res.result.get("content") if isinstance(res.result, dict) else None
    return content if isinstance(content, str) else None


async def _tool_get_esphome_history(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: the Device Builder's version history of a device file, masked.

    Lists deleted configurations, a file's versions, or one version as masked content or diff. Uses
    read commands only; every version is masked whole and diffs come from masked text.
    """
    # The add-on's raw diff is unused because its hunks cannot be span-masked, and restore is a
    # write.
    resource = "get_esphome_history"
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", resource
    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", resource

    sha, compare_to, view = args.get("sha"), args.get("compare_to"), args.get("view", "diff")
    if (any(v is not None and (not isinstance(v, str) or not _ESPHOME_SHA_RE.fullmatch(v))
            for v in (sha, compare_to))
            or view not in ("diff", "content")
            or (sha is None and (compare_to is not None or "view" in args))
            or (compare_to is not None and view != "diff")):
        return (_tool_error("sha and compare_to are hex commit ids from this tool's version list; view "
                            "(diff or content) and compare_to need sha, and compare_to needs view diff."),
                "invalid_request", resource)

    file_arg = args.get("file")
    if file_arg is None or (isinstance(file_arg, str) and not file_arg.strip()):
        if sha is not None:
            return _tool_error("sha needs file."), "invalid_request", resource
        return await _esphome_deleted_files(hass, access_key, url, args)

    refusal, path, rel, exists = await _esphome_history_precheck(hass, access_key, args, resource)
    if refusal is not None:
        return refusal
    versions = await _esphome_versions(hass, url, rel)
    if versions is None:
        return _tool_error(_ESPHOME_BUILDER_UNREACHABLE), "invalid_request", f"esphome:{rel}"
    if not versions and not exists:
        # Byte-identical to the out-of-scope refusal.
        return _tool_error(_ESPHOME_NOT_FOUND), "not_found", f"esphome:{rel}"
    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)

    if sha is None:
        rows = []
        for commit in versions:
            full = commit.get("sha")
            if not isinstance(full, str) or not _ESPHOME_SHA_RE.fullmatch(full):
                continue
            row: dict[str, Any] = {"sha": full, "short_sha": full[:7]}
            if isinstance(commit.get("timestamp"), int) and not isinstance(commit.get("timestamp"), bool):
                row["timestamp"] = commit["timestamp"]
            if isinstance(commit.get("message"), str):
                # The file's own name reads like an entity id (porch-light.yaml holds
                # "light.yaml"), so it is kept whole and only the text around it is redacted.
                parts = scrub_secret_values(commit["message"], values).split(rel)
                row["message"] = rel.join(
                    redact_diagnostics(redact_inaccessible_entity_ids_in_text(part, access_key, hass))
                    for part in parts)[:ESPHOME_BUILDER_METADATA_MAX_CHARS]
            # Whether the Builder made it; the author name itself is never returned.
            row["origin"] = "device_builder" if commit.get("author") == ESPHOME_BUILDER_HISTORY_AUTHOR else "other"
            rows.append(row)
        body = _esphome_paged(rows, args, "versions")
        body.update(file=rel, exists=exists)
        if not rows:
            body["note"] = ("No saved versions. The Device Builder's version history may be turned off, "
                            "or the file has not changed since history began.")
        return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"

    old = await _esphome_version_text(hass, url, rel, sha)
    if old is None:
        return (_tool_error("The file does not exist at that version, which may be the one that deleted "
                            "it; read the version before it."), "not_found", f"esphome:{rel}")
    entities = _esphome_entity_view(access_key, hass)
    try:
        masked_old, paths = await hass.async_add_executor_job(_esphome_mask_version, hass, old, entities)
        if view == "content":
            body = {"file": rel, "sha": sha, "content": masked_old, "redacted_paths": paths}
            return _tool_success(_esphome_scrubbed_json(body, values)), "allowed", f"esphome:{rel}"
        if compare_to is not None:
            newer = await _esphome_version_text(hass, url, rel, compare_to)
            if newer is None:
                return (_tool_error("The file does not exist at the compare_to version."),
                        "not_found", f"esphome:{rel}")
            masked_new = (await hass.async_add_executor_job(_esphome_mask_version, hass, newer, entities))[0]
            label = f"{rel}@{compare_to[:7]}"
        else:
            masked_new = await hass.async_add_executor_job(_esphome_mask_current, hass, path, entities)
            label = f"{rel} (current)" if exists else f"{rel} (deleted)"
    except (OSError, ValueError, yaml_includes.YamlParseError):
        return (_tool_error("That version could not be parsed, so its credentials cannot be masked; "
                            "it is not shown."), "invalid_request", f"esphome:{rel}")
    diff, truncated = masked_unified_diff(masked_old, masked_new, f"{rel}@{sha[:7]}", label, ESPHOME_DIFF_MAX_CHARS)
    body = {"file": rel, "sha": sha, "compared_to": compare_to or ("current" if exists else "deleted"),
            "diff": diff}
    if truncated:
        body["diff_truncated"] = True
    if not diff:
        body["note"] = "No differences."
    elif not exists and compare_to is None:
        body["note"] = "The file no longer exists, so the diff shows the whole version as removed."
    return _tool_success(_esphome_scrubbed_json(body, values)), "allowed", f"esphome:{rel}"


async def _esphome_deleted_files(
    hass: HomeAssistant, access_key: AccessKeyRecord, url: str, args: dict
) -> tuple[dict, str, str]:
    """get_esphome_history without a file: deleted configurations this access key may see."""
    resource = "get_esphome_history"
    try:
        res = await async_builder_command(
            hass, url, "version_history/list_deleted", {},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, resource)
    names = [r.get("configuration") for r in res.result if isinstance(r, dict)] if isinstance(res.result, list) else []
    located = await hass.async_add_executor_job(_esphome_history_names, hass, names)
    rows = await _esphome_scoped_rows(hass, access_key, [{"file": rel} for rel in sorted(located)])
    return _tool_success(json.dumps(_esphome_paged(rows, args, "deleted"))), "allowed", resource


_ESPHOME_TROUBLESHOOT_FLAGS = (
    "icmp_available", "zeroconf_running", "dns_resolved", "dns_had_cached_failure",
    "dns_inconclusive", "mdns_inconclusive", "mdns_has_cached_trace",
    "mdns_has_live_anchor_ptr", "ping_attempted",
)
_ESPHOME_PING_SOURCES = frozenset({"", "dns", "mdns", "runtime", "persisted"})


def _esphome_address_kind(address: Any) -> str:
    """What sort of address the device is reached at, without the address itself."""
    if not isinstance(address, str) or not address:
        return "none"
    try:
        ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return "mdns" if address.lower().endswith(".local") else "hostname"
    return "ip"


def _esphome_connectivity_projection(payload: dict) -> dict:
    """Booleans, counts and the reply time from devices/troubleshoot; never an address.

    An allowlist, so a field the add-on adds later stays out until it is reviewed.
    """
    body: dict[str, Any] = {}
    for key in _ESPHOME_TROUBLESHOOT_FLAGS:
        value = payload.get(key)
        if isinstance(value, bool) or (key == "icmp_available" and value is None and key in payload):
            body[key] = value
    for key, field in (("dns_address_count", "dns_addresses"), ("mdns_address_count", "mdns_addresses")):
        if isinstance(payload.get(field), list):
            body[key] = len(payload[field])
    source = payload.get("ping_target_source")
    if source in _ESPHOME_PING_SOURCES:
        body["ping_target_source"] = source or "none"
    raw_rtt = payload.get("ping_rtt_ms")
    rtt = float(raw_rtt) if isinstance(raw_rtt, (int, float)) and not isinstance(raw_rtt, bool) else None
    replied = rtt is not None and math.isfinite(rtt)
    if body.get("ping_attempted"):
        body["ping_replied"] = replied
        if rtt is not None and replied:
            body["ping_rtt_ms"] = round(rtt, 1)
    body["address_kind"] = _esphome_address_kind(payload.get("address"))
    if replied and source != "persisted":
        body["verdict"] = "reachable"
    elif replied:
        body["verdict"] = "answers_only_at_last_known_address"
    elif body.get("dns_resolved") or body.get("mdns_address_count"):
        body["verdict"] = "resolves_but_no_reply" if body.get("ping_attempted") else "resolves"
    else:
        body["verdict"] = "not_found_on_network"
    return body


async def _tool_check_esphome_connectivity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, client_ip: str | None = None
) -> tuple[dict, str, str]:
    """MCP tool: ask the Device Builder whether it can find and reach one device.

    Read scope, though it refreshes the Builder's DNS cache and verdict. The reply is a projection
    that withholds LAN addresses.
    """
    resource = "check_esphome_connectivity"
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", resource
    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", resource
    refusal, _path, rel = await _esphome_file_precheck(hass, access_key, args, resource)
    if refusal is not None:
        return refusal
    timeout = (ESPHOME_BUILDER_TROUBLESHOOT_HEADLESS_SECONDS if client_ip in _ESPHOME_HEADLESS_CLIENT_IPS
               else ESPHOME_BUILDER_TROUBLESHOOT_TIMEOUT_SECONDS)
    check_request_authority()
    try:
        res = await async_builder_command(
            hass, url, "devices/troubleshoot", {"configuration": rel}, timeout_seconds=timeout,
        )
    except BuilderUnavailable:
        return (_tool_error(f"The connectivity check did not finish within {timeout:.0f} s, or the Device "
                            "Builder could not be reached. It does not change the device, so asking "
                            "again is safe."), "invalid_request", f"esphome:{rel}")
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    body = {"file": rel, **_esphome_connectivity_projection(res.result if isinstance(res.result, dict) else {})}
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


async def _esphome_cancel_quietly(hass: HomeAssistant, url: str, job_id: str) -> None:
    """Best-effort cancel of a job whose result can no longer be collected."""
    try:
        await async_builder_command(hass, url, "firmware/cancel", {"job_id": job_id},
                                    timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS)
    except BuilderError:
        _LOGGER.debug("ESPHome analysis cancel failed", exc_info=True)


async def _tool_analyze_esphome_memory(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, client_ip: str | None = None
) -> tuple[dict, str, str]:
    """MCP tool: build one device and report its flash and RAM use per component.

    Synchronous, since the add-on drops the report when the job ends. Write scope, never
    approval-gated. Refuses while a job is in flight, checked last.
    """
    # This supersedes any queued or running job for the device, which could be an install; checking
    # last keeps the gap before the start to one round trip.
    resource = "analyze_esphome_memory"
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", resource
    if client_ip in _ESPHOME_HEADLESS_CLIENT_IPS:
        return (_tool_error("Memory analysis holds the request for a whole build, which this conversation "
                            "surface cannot do. Run it from Agent Chat or an MCP client."),
                "invalid_request", resource)
    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", resource
    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, resource, write=True)
    if refusal is not None:
        return refusal
    check_request_authority()
    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, resource, write=True)
    if refusal is not None:
        return refusal
    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)
    active = await _esphome_active_job(hass, url, rel)
    if active is not None:
        return (_tool_error(f"A firmware job for this file is {active.get('status')} (job_id "
                            f"{active.get('job_id')}). Memory analysis would cancel it; wait for it with "
                            "wait_for_esphome_job first."), "invalid_request", f"esphome:{rel}")
    try:
        start = await async_builder_command(
            hass, url, "firmware/analyze_memory", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    job_id = start.result.get("job_id") if isinstance(start.result, dict) else None
    if not isinstance(job_id, str) or not job_id:
        return _tool_error("The ESPHome Device Builder did not return a job."), "invalid_request", f"esphome:{rel}"

    _set_progress_status(f"Compiling {rel}", total=float(ESPHOME_BUILDER_ANALYZE_TIMEOUT_SECONDS),
                         key="agentchat.progress.esphomeCompiling", params={"file": rel, "progress": ""})
    try:
        res = await async_builder_command(
            hass, url, "firmware/follow_job", {"job_id": job_id},
            timeout_seconds=ESPHOME_BUILDER_ANALYZE_TIMEOUT_SECONDS,
            # The per-component summary prints first and the per-symbol detail runs on
            # for tens of thousands of lines, so the head is kept and the tail trimmed.
            collect_output=True, max_chars=ESPHOME_BUILDER_ANALYZE_OUTPUT_MAX_CHARS, head_ratio=0.75,
            scrub_output=_esphome_output_scrub(values),
        )
    except BuilderCommandError:
        return (_tool_error("The analysis ended before it could be followed, and the Device Builder keeps "
                            "no report once it ends. Run it again."), "invalid_request", f"esphome:{rel}")
    except BuilderUnavailable:
        await _esphome_cancel_quietly(hass, url, job_id)
        return (_tool_error(f"The analysis did not finish within {ESPHOME_BUILDER_ANALYZE_TIMEOUT_SECONDS} s "
                            "and was cancelled, since its report cannot be collected later. Compile the "
                            "device first so the analysis can reuse the build."), "invalid_request", f"esphome:{rel}")
    except asyncio.CancelledError:
        await asyncio.shield(_esphome_cancel_quietly(hass, url, job_id))
        raise
    finally:
        _set_progress_status(None)

    payload = res.result if isinstance(res.result, dict) else {}
    status = payload.get("status")
    body: dict[str, Any] = {"file": rel, "job_id": job_id, "status": status,
                            "exit_code": res.exit_code, "report": scrub_secret_values(res.output, values)}
    if res.output_truncated:
        body["report_truncated"] = True
    if isinstance(payload.get("error"), str) and payload["error"]:
        body["error"] = scrub_secret_values(payload["error"], values)
    if status == "cancelled":
        body["note"] = ("Another build for this device started, and the Device Builder cancelled the "
                        "analysis to run it.")
    elif status == "failed":
        body["note"] = "The analysis failed; the report holds ESPHome's output."
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"
