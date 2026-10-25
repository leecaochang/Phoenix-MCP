"""ESPHome tools: device YAML reads, Device Builder reads, firmware jobs and status.

The whole ESPHome surface: get/search device YAML behind the config/esphome path
jail and the credential masking in esphome_yaml.py, the Device Builder lookups
(validate, boards, components, automations, devices), the firmware job tier
(compile, install, list/poll/wait/cancel, device logs, backtrace), cleaning a
build and the fleet status read. Device YAML is the operator's own configuration,
so no tool here edits or deletes it. Compiling rides cap_esphome_yaml; only
putting an image on hardware (install) needs cap_esphome_flash.

mcp_view registers these tools and this module never imports it. Shared
primitives come from ..tool_common.
"""


import asyncio
import json
import logging
import os
import re
from typing import Any, NamedTuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.config_entries import ConfigEntryState

from ..const import (
    AI_TASK_CLIENT_IP,
    ASSIST_CLIENT_IP,
    CAP_DENY,
    ESPHOME_BUILDER_DECODE_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_JOB_LOG_TIMEOUT_SECONDS,
    ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS,
    ESPHOME_BUILDER_JOB_POLL_SECONDS,
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
    async_builder_capture,
    async_builder_command,
    collect_output_lines,
)
from ..esphome_yaml import (
    esphome_rel_path_ok,
    inline_secret_values,
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
    return {key: page, "count": len(rows), "offset": offset,
            "next_offset": end if end < len(rows) else None}


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
        # Hash of the RAW bytes on disk, so it changes whenever the file does even
        # though the caller only ever sees the masked text.
        "content_hash": content_hash(raw),
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

    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)
    try:
        res = await async_builder_command(
            hass, url, "devices/validate", {"configuration": rel},
            timeout_seconds=ESPHOME_BUILDER_VALIDATE_TIMEOUT_SECONDS,
            collect_output=True,
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")

    body = {
        "file": rel,
        "valid": res.success,
        "exit_code": res.exit_code,
        # Validation errors quote the offending lines back, which can include a
        # credential the file carries inline.
        "output": scrub_secret_values(res.output, values),
    }
    if res.output_truncated:
        body["output_truncated"] = True
    return _tool_success(json.dumps(body, default=str)), "allowed", f"esphome:{rel}"


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

    ids = args.get("component_ids")
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

    return (
        _tool_success(json.dumps({key: res.result}, default=str)),
        "allowed", "get_esphome_component",
    )


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
    """MCP tool: the trigger/action/condition catalog a device's own config supports."""
    if effective_cap(access_key, "cap_esphome_yaml") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_esphome_automations"

    url = _esphome_builder_url(hass)
    if url is None:
        return _tool_error(_ESPHOME_BUILDER_ABSENT), "invalid_request", "get_esphome_automations"

    refusal, path, rel = await _esphome_file_precheck(hass, access_key, args, "get_esphome_automations")
    if refusal is not None:
        return refusal

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
        for key in ("job_id", "status", "type", "created_at", "started_at", "finished_at"):
            value = job.get(key)
            if isinstance(value, str):
                row[key] = redact_diagnostics(
                    redact_inaccessible_entity_ids_in_text(value[:ESPHOME_BUILDER_METADATA_MAX_CHARS], access_key, hass))
        rows.append(row)
    rows.sort(key=lambda row: (row.get("created_at", ""), row.get("job_id", "")), reverse=True)
    return _tool_success(json.dumps(_esphome_paged(rows, args, "jobs"))), "allowed", resource


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


async def _esphome_job_output(
    hass: HomeAssistant, url: str, job: dict, values: set[str]
) -> tuple[str, bool]:
    """The job's log, fetched whichever way this job's state stores it.

    A running job holds its output in memory and get_job returns it. A finished
    job does NOT: the add-on writes the log to a sidecar file and empties the
    record's own output list, so the only way back to it is to follow the
    already-finished job, which replays the sidecar and closes immediately.
    """
    if job.get("status") not in _ESPHOME_JOB_TERMINAL:
        # Through the same collector the streaming path uses, never a second
        # line handler: this buffer carries the add-on's literal ANSI escapes and
        # its own terminators, so hand-joining it yields visible colour codes with
        # a blank line between every line.
        text, truncated = collect_output_lines(
            job.get("output"), ESPHOME_BUILDER_JOB_PROGRESS_MAX_CHARS, head_ratio=0.0
        )
        return scrub_secret_values(text, values), truncated

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
        return "", False
    return scrub_secret_values(res.output, values), res.output_truncated


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

    Never approval-gated even when cap_esphome_yaml is set to confirm. Compiling
    writes no file and touches no device: it is validate_esphome_yaml with a
    longer runtime, and validate already runs the same config-time evaluation. So
    it adds build time rather than authority, and gating it would put an admin
    click in the middle of every iteration of an authoring loop.
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

    Never approval-gated, and cap-only for the same reason as compile: it writes
    no configuration file and cannot reach a device. Wider than the add-on's
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
    path = os.path.join(hass.config.config_dir, _ESPHOME_DIR, rel)
    values = await hass.async_add_executor_job(_esphome_scrub_values, hass, path)
    output, truncated = await _esphome_job_output(hass, url, job, values)

    status = job.get("status")
    finished = status in _ESPHOME_JOB_TERMINAL
    body: dict[str, Any] = {
        "job_id": job.get("job_id"),
        "file": rel,
        "status": status,
        "finished": finished,
        "output": output,
    }
    if truncated:
        body["output_truncated"] = True
    for key in ("progress", "exit_code", "error"):
        if job.get(key) is not None:
            body[key] = job[key]

    # Only ever claim the device was flashed on the evidence of a COMPLETED
    # upload job. An install returns its compile job alone, so a finished compile
    # says nothing about the device, and an install deferred to the device's next
    # wake produces no upload job at all.
    if finished and status == "completed":
        upload = await _esphome_dependent_job(hass, url, rel, str(job.get("job_id") or ""))
        if upload is not None:
            upload_status = upload.get("status")
            body["upload"] = {
                "job_id": upload.get("job_id"),
                "status": upload_status,
                "finished": upload_status in _ESPHOME_JOB_TERMINAL,
            }
            body["flashed"] = upload_status == "completed"
            if upload_status in _ESPHOME_JOB_ACTIVE:
                body["note"] = "Compiled. The device is being flashed now; poll the upload job."
        elif job.get("is_deferred_install") or job.get("queued_update_armed"):
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
    try:
        await async_builder_command(
            hass, url, "firmware/cancel", {"job_id": job.get("job_id")},
            timeout_seconds=ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS,
        )
    except BuilderCommandError:
        # The add-on answers not_found for an id it no longer has, which for a
        # job that already finished is the outcome the caller wanted anyway.
        return (
            _tool_success(json.dumps({"job_id": job.get("job_id"), "file": rel, "cancelled": False,
                                      "message": "The job was already finished."})),
            "allowed",
            f"esphome:{rel}",
        )
    except BuilderError as err:
        return _builder_refusal(err, f"esphome:{rel}")
    return (
        _tool_success(json.dumps({"job_id": job.get("job_id"), "file": rel, "cancelled": True})),
        "allowed",
        f"esphome:{rel}",
    )


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
