"""ESPHome tools: device YAML reads, Device Builder reads, firmware jobs and status.

The whole ESPHome surface: get/search device YAML behind the config/esphome path
jail and the credential masking in esphome_yaml.py, the Device Builder lookups
(validate with its migration check, boards, components, automations, devices,
version history, connectivity), the firmware job tier (compile, install, memory
analysis, list/poll/wait/cancel, device logs, backtrace), cleaning a build and the
fleet status read. Device YAML is the operator's own configuration, so no tool here
edits, deletes or restores it. Compiling rides cap_esphome_yaml; only putting an
image on hardware (install) needs cap_esphome_flash.

mcp_view registers these tools and this module never imports it. Shared
primitives come from ..tool_common.
"""


import asyncio
import ipaddress
import json
import logging
import math
import os
import re
import textwrap
from typing import Any, NamedTuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.config_entries import ConfigEntryState

from ..const import (
    AI_TASK_CLIENT_IP,
    ASSIST_CLIENT_IP,
    CAP_DENY,
    ESPHOME_AUTOMATION_LIST_MAX_CHARS,
    ESPHOME_AUTOMATION_REF_TYPES,
    ESPHOME_AUTOMATION_YAML_MAX_CHARS,
    ESPHOME_BUILDER_ANALYZE_OUTPUT_MAX_CHARS,
    ESPHOME_BUILDER_ANALYZE_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_AUTOMATION_DOCS_MAX_PAGES,
    ESPHOME_BUILDER_HISTORY_AUTHOR,
    ESPHOME_BUILDER_LOADED_COMPONENTS_MAX,
    ESPHOME_BUILDER_MAX_AUTOMATION_REFS,
    ESPHOME_BUILDER_TROUBLESHOOT_HEADLESS_SECONDS,
    ESPHOME_BUILDER_TROUBLESHOOT_TIMEOUT_SECONDS,
    ESPHOME_DIFF_MAX_CHARS,
    ESPHOME_BUILDER_DECODE_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_JOB_LOG_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS,
    ESPHOME_BUILDER_JOB_POLL_SECONDS,
    ESPHOME_BUILDER_CANCEL_POLL_SECONDS,
    ESPHOME_BUILDER_CANCEL_SETTLE_SECONDS,
    ESPHOME_BUILDER_JOB_PROGRESS_MAX_CHARS,
    ESPHOME_BUILDER_JOB_WAIT_HEADLESS_SECONDS,
    ESPHOME_BUILDER_JOB_WAIT_SECONDS,
    ESPHOME_BUILDER_LOG_CAPTURE_MAX_SECONDS,
    ESPHOME_BUILDER_LOG_CAPTURE_SECONDS,
    ESPHOME_BUILDER_MAX_BACKTRACE_LINES,
    ESPHOME_BUILDER_MAX_COMPONENT_IDS,
    ESPHOME_BUILDER_METADATA_MAX_CHARS,
    ESPHOME_BUILDER_DEVICE_STATES,
    ESPHOME_BUILDER_OUTPUT_MAX_CHARS,
    ESPHOME_BUILDER_PAGE_LIMIT,
    ESPHOME_BUILDER_PAGE_LIMIT_MAX,
    ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_VALIDATE_TIMEOUT_SECONDS,
    ESPHOME_YAML_INVENTORY_MAX_ENTRIES,
    ESPHOME_YAML_SEARCH_CONTEXT_LINES_MAX,
    ESPHOME_YAML_SEARCH_MAX_BYTES,
    ESPHOME_YAML_SEARCH_MAX_QUERY_CHARS,
    ESPHOME_YAML_SEARCH_MAX_MATCHES,
    ESPHOME_YAML_SEARCH_SNIPPET_MAX_CHARS,
    MAX_FILE_BYTES,
    VOICE_AGENT_CLIENT_IP,
)
from ..data import PhoenixData
from ..tool_contracts import normalize_tool_args
from ..mesa import entity_control_mode
from ..helpers import (
    diff_summary_fields as _summary, content_hash, effective_cap,
    redact_entity_ids_outside, redact_inaccessible_entity_ids_in_text, redact_diagnostics, visible_entity_ids,
)
from ..request_authority import check_request_authority
from ..policy_engine import (
    Permission, esphome_entry_for_entity, esphome_entry_writable, resolve,
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
    apply_line_splice,
    declared_components,
    esphome_rel_path_ok,
    inline_secret_values,
    masked_unified_diff,
    redact_esphome_slice,
    redact_esphome_text,
    scrub_secret_values,
    secret_keys_from_text,
    secret_values_from_text,
)
from .. import yaml_includes

from ..tool_common import (
    _CAP_FORBIDDEN_MESSAGE,
    _gate,
    _read_text_capped,
    _set_progress_status,
    _tool_error,
    _tool_success,
    _usable_path_arg,
)

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

    Lazy-imported so Phoenix MCP never hard-depends on the esphome integration
    being installed. Returns None when the integration is absent, when the
    dashboard manager has not been set up yet, or when no Device Builder add-on
    is configured. Reading HA's own in-process coordinator is deliberate: no
    add-on HTTP call is made, so no credential-resolving endpoint (json-config,
    get_encryption_key) is reachable through it.
    """
    try:
        from homeassistant.components.esphome.dashboard import (  # noqa: PLC0415
            async_get_dashboard,
        )
    except ImportError:
        return None
    try:
        return async_get_dashboard(hass)
    except Exception:  # noqa: BLE001 - manager may not be set up yet
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

    Announcement gating keys on CONFIGURED state, never on `builder_live`: an MCP
    client caches its tool list and this transport cannot push a list-changed
    notification, so a tool list tracking a stopped add-on would flap
    mid-session. A configured-but-unreachable add-on keeps its tools announced and
    fails loudly at call time; `builder_live` is for the admin panel.

    Fails OPEN. Hiding a tool is a usability affordance, not a security control:
    the capability gate and call-time checks run regardless, so an unknown host
    announces everything rather than silently losing a tool surface.
    """
    if hass is None:
        return EsphomeAvailability(True, True, True)
    try:
        integration = _ESPHOME_DOMAIN in hass.config.components or bool(
            hass.config_entries.async_entries(_ESPHOME_DOMAIN)
        )
    except Exception:  # noqa: BLE001 - never let a probe hide the tool surface
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

    Availability follows the device's write scope, matching how
    _execute_call_service authorizes them. `args` is included because these
    schemas are device-defined: without the signature an agent has no way to
    learn what a device's action expects.
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
    rows: list[dict] = []
    for svc in (getattr(runtime, "services", None) or {}).values():
        action = getattr(svc, "name", None)
        if not action:
            continue
        row: dict = {
            "service": f"{_ESPHOME_DOMAIN}.{prefix}_{action}",
            "available": available,
            "args": [
                {"name": getattr(arg, "name", None), "type": _esphome_arg_type(arg),
                 **_esphome_action_metadata(arg, hass, access_key)}
                for arg in (getattr(svc, "args", None) or [])
            ],
            **_esphome_action_metadata(svc, hass, access_key),
        }
        if not available:
            row["reason"] = "read-only access to this device"
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


def _esphome_action_metadata(obj: Any, hass: HomeAssistant, access_key: AccessKeyRecord | None) -> dict:
    """Optional 2026.9 action descriptions/examples, bounded and scrubbed."""
    if access_key is None:
        return {}
    metadata = {}
    for key in ("description", "example"):
        value = getattr(obj, key, None)
        if isinstance(value, str) and value:
            metadata[key] = redact_diagnostics(redact_inaccessible_entity_ids_in_text(
                value[:ESPHOME_BUILDER_METADATA_MAX_CHARS], access_key, hass))
    return metadata


def _esphome_declared_actions(
    hass: HomeAssistant, device_info: Any, runtime: Any, access_key: AccessKeyRecord | None = None
) -> list[dict]:
    """List a device's user-defined API actions and their HA service names.

    Enumerated from the entry's OWN runtime services, never by scanning
    hass.services: the esphome integration does not remove its dynamically
    registered services on disconnect or unload, so a registry scan would report
    actions belonging to devices that are gone. `registered` surfaces that drift
    instead of hiding it.
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

    update_available mirrors how HA's own ESPHome update entity decides: the
    version running on the device against the version the Device Builder would
    build now. The device's `address` is deliberately omitted; it is LAN topology,
    which Phoenix MCP withholds from agents on every other surface.
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
    """Fill in live runtime fields for a LOADED ESPHome config entry.

    Reads entry.runtime_data by duck typing rather than importing the esphome
    integration's RuntimeEntryData, so Phoenix MCP never pulls aioesphomeapi into
    its own import graph (it is an integration requirement, not a Phoenix one).
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
    "can be read; secrets.yaml, the archive folder, and hidden files are excluded."
)


# Nonexistent and out-of-scope are byte-identical, so the refusal is not an
# existence oracle for devices outside this access key's tree.
_ESPHOME_NOT_FOUND = "ESPHome configuration file not found."


def _resolve_esphome_path(hass: HomeAssistant, file_arg: Any) -> tuple[str, str] | None:
    """Resolve a device-YAML argument to (realpath, esphome-relative path).

    Returns None when refused. The structural rules run on BOTH the caller's
    argument and the realpath-resolved result, so a symlink cannot rename its way
    past the secrets.yaml or archive rule. Rooted at config/esphome and
    deliberately NOT part of FILESYSTEM_ALLOWED_DIRS: widening cap_filesystem
    must never reach device YAML, which embeds C++ lambdas and credentials.
    """
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
    """Read the ESPHome secrets.yaml INTERNALLY: (values, key names).

    Never returned through any tool in any form; values feed the layer-3
    redaction cross-check and key names feed `defined_secrets`. An unreadable
    file degrades to (empty values, None keys) so redaction falls back to layers
    1 and 2, rather than failing every read.
    """
    path = os.path.join(hass.config.config_dir, _ESPHOME_DIR, yaml_includes.SECRETS_YAML)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return set(), None
    return secret_values_from_text(text), secret_keys_from_text(text)


def _esphome_entries_for_file(hass: HomeAssistant, rel: str) -> list[Any]:
    """Find adopted owners, including entries without live runtime data.

    HA persists device_name independently of the connection state. Use that
    identity as well as runtime metadata so unloading never turns an adopted
    configuration into an orphan governed by the capability alone.
    """
    dashboard = _esphome_dashboard(hass)
    dash_data = getattr(dashboard, "data", None) or {} if dashboard is not None else {}
    stem = os.path.splitext(os.path.basename(rel))[0]
    matches: list[Any] = []
    for entry in hass.config_entries.async_entries(_ESPHOME_DOMAIN):
        persisted = getattr(entry, "data", {}).get("device_name")
        info = getattr(getattr(entry, "runtime_data", None), "device_info", None)
        names = {persisted, getattr(info, "name", None)}
        if any(
            isinstance(name, str) and name and (
                (dash_data.get(name) or {}).get("configuration") == rel or name == stem
            )
            for name in names
        ):
            matches.append(entry)
    return matches


def _esphome_device_for_file(hass: HomeAssistant, rel: str) -> Any | None:
    """Return the sole adopted owner for display; ambiguous owners stay unnamed."""
    entries = _esphome_entries_for_file(hass, rel)
    return entries[0] if len(entries) == 1 else None


def _esphome_file_in_scope(hass: HomeAssistant, access_key: AccessKeyRecord, rel: str, *, write: bool) -> bool:
    """Whether the access key may read the device YAML at rel, or with write, build from it.

    Every adopted owner must be in scope, even when unloaded. Orphan files
    follow the capability alone.
    """
    entries = _esphome_entries_for_file(hass, rel)
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
    _esphome_file_in_scope on the event loop (_esphome_listing).
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


async def _esphome_listing(hass: HomeAssistant, access_key: AccessKeyRecord) -> list[dict]:
    """The device YAML files this access key may read; scope is decided on the loop."""
    rows = await hass.async_add_executor_job(_esphome_files, hass)
    return [row for row in rows if _esphome_file_in_scope(hass, access_key, row["file"], write=False)]


def _esphome_search(
    hass: HomeAssistant, rows: list[dict], visible: frozenset[str],
    query: str, case_sensitive: bool, context: int,
) -> dict:
    """Search only masked, authorized files within a total byte budget.

    Runs in a worker thread over rows and visible entity IDs the loop decided.
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
            masked, _paths = redact_esphome_text(raw, values)
            masked = redact_entity_ids_outside(masked, visible, hass)
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
        visible = visible_entity_ids(access_key, hass)
        result = await hass.async_add_executor_job(
            _esphome_search, hass, rows, visible, query, sensitive, context)
    except (OSError, ValueError):
        return _tool_error("ESPHome configuration search could not complete."), "invalid_request", resource
    rows = [row for row in result["matches"]
            if _esphome_file_in_scope(hass, access_key, row["file"], write=False)]
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
    if not _esphome_file_in_scope(hass, access_key, rel, write=False):
        return _tool_error(_ESPHOME_NOT_FOUND), "denied", f"esphome:{rel}"

    try:
        raw = await hass.async_add_executor_job(_read_text_capped, path)
    except FileNotFoundError:
        return _tool_error(_ESPHOME_NOT_FOUND), "not_found", f"esphome:{rel}"
    except (OSError, ValueError):
        return _tool_error("Failed to read the ESPHome configuration file."), "invalid_request", f"esphome:{rel}"

    try:
        redacted, redacted_paths = await hass.async_add_executor_job(
            redact_esphome_text, raw, secret_values
        )
    except yaml_includes.YamlParseError as err:
        # Fail closed: a file whose credentials cannot be located must not be
        # returned, since inline credentials are the normal case here.
        return (
            _tool_error(f"The file could not be parsed, so its credentials cannot be masked: {err}"),
            "invalid_request", f"esphome:{rel}",
        )

    entry = _esphome_device_for_file(hass, rel)
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

    Existence is checked HERE, before any Device Builder call, so the add-on's
    own "not found" can never produce a different body than an out-of-scope
    refusal: both must be indistinguishable.

    `write` is set by the tools that start a firmware job. Building for a device
    acts on it rather than describing it, so it must not be reachable from
    read-only scope; the lookups and diagnostics stay on read.
    The path is resolved and probed in one executor job; the scope decision stays
    on the event loop, and still runs before the probe result is used.
    """
    located = await hass.async_add_executor_job(_locate_esphome_file, hass, args.get("file"))
    if located is None:
        return (_tool_error(_ESPHOME_PATH_REFUSED), "invalid_request", tool_name), "", ""
    path, rel, is_file = located
    if not _esphome_file_in_scope(hass, access_key, rel, write=write):
        return (_tool_error(_ESPHOME_NOT_FOUND), "denied", f"esphome:{rel}"), path, rel
    if not is_file:
        return (_tool_error(_ESPHOME_NOT_FOUND), "not_found", f"esphome:{rel}"), path, rel
    return None, path, rel


def _esphome_scrub_values(hass: HomeAssistant, path: str) -> set[str]:
    """Every credential value that must not appear in Device Builder output.

    The secrets.yaml values plus whatever this file inlines. A file that does not
    parse still contributes the secrets.yaml values: it is often unparseable
    precisely because someone is validating a broken edit, and losing the scrub
    there would be the worst moment for it.
    """
    secret_values, _keys = _read_esphome_secrets(hass)
    values = set(secret_values or set())
    try:
        with open(path, encoding="utf-8") as handle:
            values |= inline_secret_values(handle.read(), values)
    except (OSError, ValueError, yaml_includes.YamlParseError):
        _LOGGER.debug("ESPHome scrub could not read inline credentials", exc_info=True)
    return values


def _esphome_page_limit(args: dict) -> int:
    """Clamp a caller-supplied page size into the configured bounds.

    A missing, non-integer, or non-positive limit falls back to the default
    rather than clamping up to 1: a model sending limit 0 means "unspecified",
    and answering with a single row would look like an empty catalog.
    """
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

    Validates what is ON DISK, never caller-supplied content, and that is a
    security boundary rather than a convenience. ESPHome evaluates configuration
    at validation time, and a config carrying external_components fetches and
    executes remote code to do it. Validating an arbitrary payload would
    therefore be an unreviewed code-execution primitive, while a file on disk is
    one the operator put there.
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
            collect_output=True,
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
        body.update(await _esphome_migration_check(hass, url, rel, raw, secret_values, values))
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


def _esphome_read_for_migration(hass: HomeAssistant, path: str) -> tuple[str | None, set[str], set[str]]:
    """(raw text or None, secrets.yaml values, every scrub value) for one file. Executor only.

    The raw text is returned only when the file reads within the size cap AND
    parses, because the migration check sends exactly that text and masks both
    sides of its diff; a file whose credentials cannot be located is not sent.
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
    raw: str, yaml_diff: dict, secret_values: set[str], rel: str
) -> tuple[str, bool] | None:
    """Masked unified diff of the migration, or None when it cannot be shown safely. Executor only.

    The add-on's own replacement is never returned: it is applied here and both
    whole texts are masked. A migration that leaves fewer credentials maskable than
    the original (a credential key respelled to a name the mask does not know) is
    withheld rather than risk showing its value.
    """
    try:
        migrated = apply_line_splice(
            raw, int(yaml_diff["fromLine"]), int(yaml_diff["toLine"]), str(yaml_diff["replacement"]))
        before, before_paths = redact_esphome_text(raw, secret_values)
        known = secret_values | inline_secret_values(raw, secret_values)
        after, after_paths = redact_esphome_text(migrated, known)
    except (KeyError, TypeError, ValueError, yaml_includes.YamlParseError):
        return None
    if len(after_paths) < len(before_paths):
        return None
    return masked_unified_diff(before, after, rel, f"{rel} (migrated)", ESPHOME_DIFF_MAX_CHARS)


async def _esphome_migration_check(
    hass: HomeAssistant, url: str, rel: str, raw: str, secret_values: set[str], values: set[str]
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
    shown = await hass.async_add_executor_job(_esphome_migration_diff, raw, yaml_diff, secret_values, rel)
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

    The same filter as the Device Builder's own MCP get_component: an entry with
    a truthy `advanced` or `hidden` flag leaves its `config_entries` list. Most
    configs never set them, and they are a large share of a component body.
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

    Two views, because neither alone is the whole answer: `declared` is read from
    the file itself (names only, never values) and misses what packages bring in;
    `loaded` is what the device's last build actually compiled, and is empty
    until the first compile.
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

    Empty and false values are dropped too: a flag that is false on most rows
    carries no information and costs a line each. `scripts` passes through whole,
    being the device's own declarations rather than catalog metadata.
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
    rendered = json.dumps(
        {"file": rel, "automations": _project_esphome_automations(res.result)},
        default=str,
    )
    return _tool_success(scrub_secret_values(rendered, values)), "allowed", f"esphome:{rel}"


# Catalog ids as the add-on spells them, e.g. light.turn_on or on_press.
_ESPHOME_REF_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


async def _esphome_automation_docs(
    hass: HomeAssistant, url: str, refs: Any, include_advanced: bool
) -> tuple[dict, str, str]:
    """get_esphome_automations with refs: the fields each building block takes.

    Catalog metadata, the same for every device, so it is scoped like the
    component catalog rather than to a file. The add-on answers a large request in
    pages and lists what is left in `remaining`; a page that makes no progress
    ends the loop rather than spinning on it.
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

    raw_yaml is not a verbatim slice: the add-on re-dumps the parsed value, so its
    quoting and indentation differ from the file. Both are loaded and compared as
    data; anything that will not load counts as a mismatch.
    """
    try:
        return (yaml_includes.load_tagged_lenient(textwrap.dedent(local))
                == yaml_includes.load_tagged_lenient(reported))
    except (yaml_includes.YamlParseError, ValueError, TypeError):
        return False


def _esphome_automation_rows(raw: str, secret_values: set[str], parsed: Any) -> tuple[list[dict], list[str]]:
    """Project automations/parse rows, with YAML cut from Phoenix MCP's own masked read. Executor only.

    The add-on's raw_yaml is used only to check that both read the same file; it
    is never relayed. A row whose lines no longer match is listed without its
    YAML, and a file that will not parse withholds every row's YAML (fail closed).
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
                k: (v[:ESPHOME_BUILDER_METADATA_MAX_CHARS] if isinstance(v, str) else v)
                for k, v in location.items()
                if k in _ESPHOME_LOCATION_KEYS and (isinstance(v, str) or (isinstance(v, int) and not isinstance(v, bool)))
            }
        for key in ("label", "error"):
            if isinstance(item.get(key), str) and item[key]:
                row[key] = item[key][:ESPHOME_BUILDER_METADATA_MAX_CHARS]
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
                masked = redact_esphome_slice(raw, secret_values, first, last)
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
    rows, warnings = await hass.async_add_executor_job(_esphome_automation_rows, raw, secret_values, res.result)
    for row in rows:
        for key in ("label", "error"):
            if key in row:
                row[key] = redact_diagnostics(redact_inaccessible_entity_ids_in_text(row[key], access_key, hass))
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
    rendered = json.dumps(body, default=str)
    return _tool_success(scrub_secret_values(rendered, values)), "allowed", f"esphome:{rel}"


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
    rows = []
    for job in jobs:
        configuration = job.get("configuration")
        resolved = located.get(configuration) if isinstance(configuration, str) else None
        if resolved is None:
            continue
        rel = resolved[1]
        if (not _esphome_file_in_scope(hass, access_key, rel, write=False)
                or (payload and rel != payload["configuration"])
                or (status is not None and status != job.get("status"))):
            continue
        row = {"file": rel}
        # The add-on's own field names: a job record carries job_type and
        # completed_at, never type or finished_at.
        for key in ("job_id", "status", "job_type", "created_at", "started_at", "completed_at"):
            value = job.get(key)
            if isinstance(value, str):
                row[key] = redact_diagnostics(
                    redact_inaccessible_entity_ids_in_text(value[:ESPHOME_BUILDER_METADATA_MAX_CHARS], access_key, hass))
        rows.append(row)
    rows.sort(key=lambda row: (row.get("created_at", ""), row.get("job_id", "")), reverse=True)
    return _tool_success(json.dumps(_esphome_paged(rows, args, "jobs"))), "allowed", resource


# ESPHome version strings as the add-on reports them (2026.10.1, 2026.11.0b2).
_ESPHOME_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+\-]{0,31}$")


def _esphome_device_firmware(device: dict, runtime: Any) -> dict:
    """What a configured row says about its firmware: deployed, update, migration, armed.

    deployed_version lives in the nested runtime_state on the WebSocket wire; the
    flat field is read only as a fallback for an older add-on. The update rule is
    the one HA's own ESPHome update entity applies: deployed against current.
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
                if not _esphome_file_in_scope(hass, access_key, rel, write=False):
                    continue
                if not _esphome_file_in_scope(hass, access_key, name + ".yaml", write=False):
                    continue
                row["file"] = rel
            elif not _esphome_file_in_scope(hass, access_key, name + ".yaml", write=False):
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
                    row[key] = redact_diagnostics(
                        redact_inaccessible_entity_ids_in_text(value[:ESPHOME_BUILDER_METADATA_MAX_CHARS], access_key, hass))
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

    Submitting a second job for the same configuration does not queue behind the
    first: the add-on cancels the first and reaps its log. So a retry issued
    while a build is running silently destroys the build it was retrying, and an
    agent that cannot see that just retries again.
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

    A job id is a handle to another device's build log, so it is scoped like the
    file: the job names its configuration, which must be in the access key's tree. An
    id the add-on does not know and an id for a device this access key cannot see
    return the same body, so polling ids is not a way to learn which exist.

    allow_file lets the caller name the CONFIGURATION instead, answering "is that
    build done?" with no remembered state (a job id only exists in the
    conversation that started the build, and voice or Assist conversations do not
    survive). Not offered for cancel: stopping "whichever job is newest" is not
    something a caller can have meant to name.
    """
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
    job = await _esphome_job_record(hass, url, job_id.strip())
    if job is None:
        return (_tool_error(missing), "not_found", tool_name), {}
    # Separate name: `rel` is already bound to a str on the by-file branch above,
    # and the add-on's payload is untyped, so reusing it would widen that binding.
    configuration = job.get("configuration")
    if not isinstance(configuration, str) or not esphome_rel_path_ok(configuration):
        return (_tool_error(missing), "not_found", tool_name), {}
    rel = configuration
    if not _esphome_file_in_scope(hass, access_key, rel, write=False):
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

    A running job holds its output in memory and get_job returns it. A finished
    job does NOT: the add-on writes the log to a sidecar file and empties the
    record's own output list, so the only way back to it is to follow the
    already-finished job, which replays the sidecar and closes immediately. That
    replay ends on a frame carrying queued_update_armed, kept here.
    """
    if job.get("status") not in _ESPHOME_JOB_TERMINAL:
        # Through the same collector the streaming path uses, never a second
        # line handler: this buffer carries the add-on's literal ANSI escapes and
        # its own terminators, so hand-joining it yields visible colour codes with
        # a blank line between every line.
        text, truncated = collect_output_lines(
            job.get("output"), ESPHOME_BUILDER_JOB_PROGRESS_MAX_CHARS, head_ratio=0.0
        )
        return _EsphomeJobOutput(scrub_secret_values(text, values), truncated)

    try:
        res = await async_builder_command(
            hass, url, "firmware/follow_job", {"job_id": job.get("job_id")},
            timeout_seconds=ESPHOME_BUILDER_JOB_LOG_TIMEOUT_SECONDS,
            collect_output=True,
            max_chars=ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS,
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

    Looks under `rel` first and then unfiltered, because the two jobs of a chain
    do NOT always share a configuration. The filtered call stays first so the
    common case keeps its cheap, well-scoped query.
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

    Never approval-gated: cap_esphome_yaml offers only allow or deny. Compiling
    writes no configuration file and touches no device: it is validate_esphome_yaml
    with a longer runtime, and validate already runs the same config-time
    evaluation. So it adds build time rather than authority, and gating it would put
    an admin click in the middle of every iteration of a build loop.
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
    """MCP tool: discard one configuration's build artifacts (a stale cache).

    Never approval-gated (cap_esphome_yaml offers only allow or deny), for the same
    reason as compile: it writes no configuration file and cannot reach a device. Wider than the add-on's
    docs suggest: it also drops the shared PlatformIO cache, so every device's
    next build is slower (the tool description says so). firmware/reset_build_env
    stays unreachable: it takes no configuration and CANCELS EVERY in-flight job.
    """
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

    Shared by the gate path and the executor, so a change appearing during the
    approval window (the file going away, the access key losing the device, another
    build starting meanwhile) is caught at apply time too.
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
    if effective_cap(access_key, "cap_esphome_flash") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "install_esphome_firmware"

    pre, _url, _rel = await _install_esphome_precheck(hass, access_key, args)
    if pre is not None:
        return pre

    blocked = await _gate(
        "cap_esphome_flash", access_key, hass, data,
        tool_name="install_esphome_firmware", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_install_esphome_firmware(args, access_key, hass, data),
    )
    if blocked is not None:
        return blocked
    return await _execute_install_esphome_firmware(args, access_key, hass, data)


async def _execute_install_esphome_firmware(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Side-effect path for install_esphome_firmware."""
    pre, url, rel = await _install_esphome_precheck(hass, access_key, args)
    if pre is not None:
        return pre

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
    """Return "old to new" when two ESPHome versions differ beyond the patch, else None.

    A patch bump is routine. A version change is where the limit of an over-the-air
    update matters: it rewrites only the app partition, never the bootloader or
    the partition table, so the new app runs on the old bootloader. The approval
    card otherwise shows both numbers without saying they are a version apart.

    The warning wording deliberately does NOT predict an unbootable device, which
    does not happen in practice; a warning naming a consequence that never arrives
    teaches the operator to click past it. It states the verifiable bootloader gap.
    """
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

    Unlike a page size, the useful default here IS the ceiling: a build that
    finishes inside one call is the whole point.

    EXCEPT on a headless conversational surface, where the ceiling is a hang. A
    voice turn or an Assist reply has no stream to write progress to and its own
    pipeline timeout is far under this tool's, so a full-length hold buys only
    silence followed by an error. Those callers get a short wait and are told to
    ask again.
    """
    if client_ip in _ESPHOME_HEADLESS_CLIENT_IPS:
        return ESPHOME_BUILDER_JOB_WAIT_HEADLESS_SECONDS
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return ESPHOME_BUILDER_JOB_WAIT_SECONDS
    return min(value, ESPHOME_BUILDER_JOB_WAIT_SECONDS)


def _esphome_progress_message(job: dict, rel: str, phase: str) -> str:
    """The human-readable line a client shows while a build runs.

    The percentage lives HERE and never in the protocol's numeric progress field,
    which MCP requires to increase on every notification for a token. The add-on's
    number does not qualify: it resets at the compile-to-upload seam and can read
    nonzero on an already-completed job. Elapsed seconds carries the monotonic
    guarantee; this string carries the meaning.
    """
    pct = job.get("progress")
    if isinstance(pct, int) and not isinstance(pct, bool) and 0 <= pct <= 100:
        return f"{phase} {rel}: {pct}%"
    return f"{phase} {rel}"


async def _tool_wait_for_esphome_job(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, client_ip: str | None = None
) -> tuple[dict, str, str]:
    """MCP tool: hold until a firmware job finishes, reporting progress meanwhile.

    The polling sibling of get_esphome_job, and the only place a build reports
    anything while it runs: compile and install return immediately by design, so
    without a held request there is no in-flight call for progress to ride on.

    Follows the whole chain rather than one job. Waiting on an install's compile
    picks up the dependent upload when the compile finishes, so a single call
    reports "Compiling x: 70%" and then "Flashing x: 40%" and only returns once
    the device has actually been flashed.
    """
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

            fresh = await _esphome_job_record(hass, url, str(watching.get("job_id") or ""))
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
    current = await _esphome_job_record(hass, url, str(started.get("job_id") or "")) or started
    # One shared wording with get_esphome_job: two divergent versions of "stop
    # here" leave a gap a model can exploit.
    note = _ESPHOME_HEADLESS_STOP if client_ip in _ESPHOME_HEADLESS_CLIENT_IPS else None
    content, outcome, resource = await _esphome_job_result(hass, url, current, note)
    return content, outcome, resource


async def _tool_cancel_esphome_job(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: stop a queued or running firmware job.

    Never approval-gated. Waiting for an admin to approve a stop request is
    backwards, and the build would finish before the click landed. It is also not
    more dangerous than starting one: ESPHome writes an update to the inactive
    partition and swaps on completion, so an interrupted upload leaves the device
    running exactly the firmware it already had.
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

    Read scope on the device, not write: this observes rather than changes. The
    console prints whatever the firmware logs, which can be more detailed than
    the entities Home Assistant exposes, so the device itself is the unit of
    authorization here.
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
    rendered = json.dumps(body, default=str)
    return _tool_success(scrub_secret_values(rendered, values)), "allowed", f"esphome:{rel}"


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

    The jail and name rules are the ones every ESPHome read uses; only existence
    is not required, since history is how a deleted file is found again. Scope
    follows device ownership by name, so a deleted file whose device is still
    adopted keeps that device's scope, and a true orphan follows the capability.
    """
    located = await hass.async_add_executor_job(_locate_esphome_file, hass, args.get("file"))
    if located is None:
        return (_tool_error(_ESPHOME_PATH_REFUSED), "invalid_request", tool_name), "", "", False
    path, rel, is_file = located
    if not _esphome_file_in_scope(hass, access_key, rel, write=False):
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


def _esphome_mask_version(hass: HomeAssistant, text: str) -> tuple[str, list[str]]:
    """Mask one historical version whole. Executor only; raises on oversize or unparseable text.

    A scrub alone would not do: an old version can carry a credential that has
    since been rotated out of secrets.yaml and every current file.
    """
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("version too large")
    secret_values, _keys = _read_esphome_secrets(hass)
    return redact_esphome_text(text, set(secret_values or set()))


def _esphome_mask_current(hass: HomeAssistant, path: str) -> str:
    """The current file, masked; "" when it no longer exists. Executor only."""
    if not os.path.isfile(path):
        return ""
    return _esphome_mask_version(hass, _read_text_capped(path))[0]


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

    Lists deleted configurations, a file's versions, or one version as masked
    content or a masked diff. Only read commands are used: no raw diff from the
    add-on (its hunks cannot be span-masked) and no restore (a write). Every
    version is masked whole and diffs are computed here from masked text.
    """
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
                parts = scrub_secret_values(commit["message"][:ESPHOME_BUILDER_METADATA_MAX_CHARS], values).split(rel)
                row["message"] = rel.join(
                    redact_diagnostics(redact_inaccessible_entity_ids_in_text(part, access_key, hass)) for part in parts)
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
    try:
        masked_old, paths = await hass.async_add_executor_job(_esphome_mask_version, hass, old)
        if view == "content":
            body = {"file": rel, "sha": sha, "content": masked_old, "redacted_paths": paths}
            return _tool_success(scrub_secret_values(json.dumps(body, default=str), values)), "allowed", f"esphome:{rel}"
        if compare_to is not None:
            newer = await _esphome_version_text(hass, url, rel, compare_to)
            if newer is None:
                return (_tool_error("The file does not exist at the compare_to version."),
                        "not_found", f"esphome:{rel}")
            masked_new = (await hass.async_add_executor_job(_esphome_mask_version, hass, newer))[0]
            label = f"{rel}@{compare_to[:7]}"
        else:
            masked_new = await hass.async_add_executor_job(_esphome_mask_current, hass, path)
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
    return _tool_success(scrub_secret_values(json.dumps(body, default=str), values)), "allowed", f"esphome:{rel}"


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
    rows = [{"file": rel} for rel in sorted(located)
            if _esphome_file_in_scope(hass, access_key, rel, write=False)]
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

    Read scope, like device logs: it observes the device rather than acting on it.
    It does refresh the Builder's own DNS cache and reachability verdict, which the
    description says. The reply is a projection: the add-on's answer is full of LAN
    addresses, which Phoenix MCP withholds from agents on every surface.
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

    Synchronous, unlike compile: the add-on drops the job and its report the
    moment it ends, so the report is collected by a follow attached here and
    nowhere else. Write scope like compile (it builds for the device), never
    approval-gated. It supersedes any queued or running job for the device, which
    could be an install, so it refuses while one is in flight, and that check runs
    last so the gap before the start is one round trip.
    """
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
