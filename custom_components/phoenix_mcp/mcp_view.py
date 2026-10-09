"""MCP Streamable HTTP endpoint for the Phoenix MCP integration."""

from __future__ import annotations

import asyncio
import base64
import functools
import dataclasses
import hashlib
import json
import logging
import math
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

import voluptuous as vol
from aiohttp import web

from .view_base import PhoenixView
from homeassistant import loader
from homeassistant.loader import IntegrationNotFound
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceNotFound,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.config_entries import (
    ConfigEntryDisabler,
    ConfigEntryState,
    support_entry_unload,
)
from homeassistant.core import HomeAssistant, callback, valid_entity_id
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import utcnow
from homeassistant.util.ulid import ulid_to_bytes_or_none

from .audit import generate_request_id
from .const import AGENTCLI_CLIENT_IP, AI_TASK_CLIENT_IP, ASSIST_CLIENT_IP, PHOENIX_VERSION, BLOCKED_DOMAINS, VOICE_AGENT_CLIENT_IP, HOME_COMPANION_CLIENT_IP, CAP_ALLOW, CAP_CONFIRM, CAP_DENY, CAPABILITY_NAMES, DOMAIN, DOMAIN_IMPORTANT_ATTRIBUTES, DUAL_GATE_SERVICES, LEAN_ALWAYS_ATTRS, HIGH_RISK_DOMAINS, MCP_AUTHORITY_CHANGED_ERROR, MCP_DISCOVER_TTL_MS, MCP_LEGACY_PROTOCOL_VERSION_PREFERRED, MCP_LEGACY_PROTOCOL_VERSIONS, MCP_PROTOCOL_VERSIONS, MCP_SSE_KEEPALIVE_SECONDS, MAX_APPROVAL_RESULT_CHARS, MAX_BATCH_APPROVALS, MAX_BATCH_ITEMS, LOG_LEVELS, LOG_LEVEL_ERROR_MESSAGE, MAX_LOG_ENTRIES, MAX_LOGBOOK_HOME_DAYS, MAX_LOGBOOK_NARROWED_DAYS, MAX_LOGBOOK_RESOURCE_IDS, MAX_SEARCH_QUERY_LEN, MAX_SUBSCRIPTION_SECONDS, MAX_ENTITY_ALIASES, MAX_ENTITY_ALIAS_LENGTH, MAX_TOOL_NAME_LENGTH, MESA_APPROVED_EXECUTOR, MESA_MODE_OFF, NO_TARGET_SERVICES, PROXY_TIMEOUT_SECONDS
from .data import PhoenixData
from .tools.esphome import _tool_search_esphome_yaml, _tool_list_esphome_jobs, _tool_list_esphome_devices
from .template_render import TemplateRenderError, async_render_template_for_access_key
from .request_authority import (AuthorityChangedError, PolicyChangedError, RequestAuthority, check_request_authority, policy_version, request_authority)
from .household_authority import household_service_kwargs, household_tool_access
from .request_authority import DispatchProgress, dispatch_progress, mark_dispatch_started
from .tool_common import _create_registry_mesa_approval, offset_page, unknown_category_message, unknown_label_message
from .tools.locations import _tool_set_area_or_floor, _execute_set_area_or_floor, restore_location_version
from .tool_input import decode_container_strings, scalar_arguments, tool_arguments_error
from .device_semantics import (
    device_registry_snapshot,
    entity_follows_device_name,
    entity_name_source,
    update_device_registry_entry,
)
from .service_targets import secondary_target_error, unsupported_secondary_targets
from .tool_contracts import _RETIRED_REPLACEMENTS, normalize_tool_args, unknown_argument_error
from .const import SERVICE_RESULT_STATES_MAX, CALENDAR_EVENTS_DEFAULT_LIMIT, CALENDAR_MAX_EVENTS, DEBUG_CAPTURE_CAPACITY, LOG_DETAIL_STALE_MESSAGE, CALL_SERVICE_NO_TARGET_CAPS, GET_STATE_MAX_IDS, INTEGRATION_LIST_DEFAULT_LIMIT, INTEGRATION_LIST_MAX_LIMIT, UNKNOWN_ARGUMENT_PROTECTED_FIELDS
from .mesa import (
    RegistryMesaDecision,
    async_apply_mesa_to_call,
    evaluate_registry_action,
    fire_mesa_blocked_event,
)
from .ws_dispatch import WsDispatchError, async_ws_command
from .agent_recovery import tool_is_read_only
from .mesa_tools import MESA_TOOL_NAMES, async_call_mesa_tool, mesa_tool_defs
from .helpers import _strict_phoenix_log_text, build_error_response as _error, build_permitted_states as _build_permitted_states, build_safe_config, collect_log_entries as _collect_log_entries, content_hash, diff_summary_fields as _summary, effective_cap, effective_caps, fire_rate_limit_events as _fire_rate_limit_events, async_get_authenticated_access_key as _async_get_authenticated_access_key, get_client_ip as _get_client_ip, log_internal_failure, log_request as _log, parse_time_param as _parse_time_param, redact_diagnostics, redact_structure, sanitize_service_data as _sanitize_service_data, service_not_found_hint as _service_not_found_hint, str_arg, SystemLogDegradedError, SystemLogUnavailableError, access_key_has_write_scope, validation_error_message as _validation_error_message, version_summary_fields as _version_summary
from .recorder_queries import (
    MAX_SIGNIFICANT_STATES_RANGE,
    newest_page,
    STATISTIC_PERIOD_LIMITS,
    recorder_envelope,
    retention_metadata,
    retention_warnings,
    iso_utc,
    state_timestamp,
    statistic_page,
    statistic_timestamp,
    parse_recorder_window,
    read_statistics_page,
)
# Shared tool primitives live in tool_common so per-domain tool modules do not
# import this transport module. Per-domain tools live under tools/; this module
# keeps the transport, the dispatch registry and the executor registry, and
# imports the names it registers or calls. A few domain adapters import shared
# dispatch or recovery helpers from this module locally (inside functions).
from .tools.authoring import _execute_create_automation, _execute_create_scene, _execute_create_script, _execute_delete_automation, _execute_delete_scene, _execute_delete_script, _execute_edit_automation, _execute_edit_scene, _execute_edit_script, _tool_create_automation, _tool_create_scene, _tool_create_script, _tool_delete_automation, _tool_delete_scene, _tool_delete_script, _tool_edit_automation, _tool_edit_scene, _tool_edit_script, _tool_get_automation, _tool_get_automation_traces, _tool_get_scene, _tool_get_script, _tool_list_automations, _tool_list_scenes, _tool_list_scripts
from .tools.config_files import (
    _execute_patch_yaml_config,
    _execute_set_yaml_config,
    _execute_write_file,
    _tool_get_yaml_config,
    _tool_list_files,
    _tool_read_file,
    _tool_patch_yaml_config,
    _tool_set_yaml_config,
    _tool_write_file,
)
from .tools.helper import (
    _HELPER_RESTORE_ID,
    _execute_set_config_entry_options,
    _tool_get_config_entry_options,
    _tool_set_config_entry_options,
    _execute_create_helper,
    _execute_delete_helper,
    _execute_edit_helper,
    _read_helper_config,
    _tool_create_helper,
    _tool_delete_helper,
    _tool_edit_helper,
    _tool_list_helpers,
)
from .tools.recovery import _execute_restore_configuration, _tool_restore_configuration
from .tools.integration_reconfigure import (
    MENU_CHOICE_BUDGET,
    STATUS_APPLY_FAILED as RECONFIGURE_APPLY_FAILED,
    async_run_reconfigure_flow,
)
from .tools.radio import (
    _execute_create_zigbee_group,
    _execute_configure_zigbee_reporting,
    _execute_permit_zigbee_join,
    _execute_reconfigure_zigbee_device,
    _execute_remove_zigbee_device,
    _execute_remove_zigbee_group,
    _execute_scan_zigbee_topology,
    _execute_set_zigbee_device_options,
    _execute_set_zigbee_device_property,
    _execute_set_zigbee_binding,
    _execute_set_zigbee_group_members,
    _tool_configure_zigbee_reporting,
    _tool_get_radio_device,
    _tool_get_radio_network,
    _tool_get_zigbee_groups,
    _tool_permit_zigbee_join,
    _tool_reconfigure_zigbee_device,
    _tool_remove_zigbee_device,
    _tool_scan_zigbee_topology,
    _tool_set_zigbee_device_options,
    _tool_set_zigbee_device_property,
    _tool_set_zigbee_binding,
    _tool_zigbee_group_change,
)
from .tools.energy import (
    _execute_edit_energy_config,
    _tool_edit_energy_config,
    _tool_get_energy_config,
    _tool_get_solar_forecast,
    async_restore_energy_prefs,
)
from .tools.discovery import _registry_relationship_preview, _registry_relationships_preview, _requires_satisfied, _requires_unavailable_reason, _tool_check_config, _tool_compare_entities, _tool_compare_state, _tool_describe_area, _tool_describe_entity, _tool_dry_run_service, _tool_find_available_actions, _tool_get_audit_summary, _tool_get_device, _tool_get_overview, _tool_get_relationships, _tool_get_repairs, _tool_get_system_health, _tool_list_areas, _tool_list_devices, _tool_list_floors, _tool_list_zones, _tool_recent_activity, _tool_recognize_intent, _tool_search_entities, _tool_validate_config, _tool_whatif
from .tools.native import (
    _UNTRUSTED_DATA_BOUNDARY,
    _build_live_context,
    _execute_hass_set_position,
    _execute_hass_set_volume_relative,
    _execute_hass_stop_moving,
    _execute_hass_turn_off,
    _execute_hass_turn_on,
    _tool_get_date_time,
    _tool_get_live_context,
    _tool_hass_broadcast,
    _tool_hass_cancel_all_timers,
    _tool_hass_climate_set_temperature,
    _tool_hass_fan_set_speed,
    _tool_hass_light_set,
    _tool_hass_media_next,
    _tool_hass_media_pause,
    _tool_hass_media_player_mute,
    _tool_hass_media_player_unmute,
    _tool_hass_media_previous,
    _tool_hass_media_search_and_play,
    _tool_hass_media_unpause,
    _tool_hass_set_position,
    _tool_hass_set_volume,
    _tool_hass_set_volume_relative,
    _tool_hass_stop_moving,
    _tool_hass_vacuum_clean_area,
    _tool_hass_vacuum_return_to_base,
    _tool_hass_vacuum_start,
    _tool_hass_turn_off,
    _tool_hass_turn_on,
)
from .tools.calendar import (
    _tool_calendar_write, _tool_get_calendar_write,
    _execute_create_calendar_event, _execute_update_calendar_event, _execute_delete_calendar_event,
)
from .tools.blueprint import (
    _execute_create_blueprint,
    _execute_delete_blueprint,
    _execute_edit_blueprint,
    _read_blueprint_source,
    _resolve_blueprint_path,
    _tool_create_blueprint,
    _tool_delete_blueprint,
    _tool_edit_blueprint,
    _tool_get_blueprint,
    _tool_list_blueprints,
)
from .tools.lovelace import (
    _execute_add_dashboard_card,
    _execute_create_dashboard,
    _execute_delete_dashboard,
    _execute_delete_dashboard_card,
    _execute_patch_dashboard,
    _execute_edit_dashboard,
    _execute_edit_dashboard_card,
    _execute_set_dashboard_config,
    _tool_create_dashboard,
    _tool_dashboard_card,
    _tool_delete_dashboard,
    _tool_edit_dashboard,
    _tool_get_dashboard_config,
    _tool_list_dashboard_cards,
    _tool_patch_dashboard,
    _tool_list_dashboards,
    _tool_set_dashboard_config,
)
from .tools.camera import _tool_get_camera_image
from .tools.esphome import _ESPHOME_DOMAIN, _execute_delete_esphome_yaml, _execute_install_esphome_firmware, _execute_rename_esphome_device, _execute_set_esphome_yaml, _tool_cancel_esphome_job, _tool_clean_esphome_build, _tool_compile_esphome_firmware, _tool_decode_esphome_backtrace, _tool_delete_esphome_yaml, _tool_get_esphome_automations, _tool_get_esphome_board, _tool_get_esphome_component, _tool_get_esphome_device_logs, _tool_get_esphome_job, _tool_get_esphome_overview, _tool_get_esphome_yaml, _tool_install_esphome_firmware, _tool_rename_esphome_device, _tool_set_esphome_yaml, _tool_validate_esphome_yaml, _tool_wait_for_esphome_job
# The published tool catalog (schemas + MCP annotations) is declarative data
# in tool_defs.py; this module reads it to answer tools/list, build the
# per-access-key gate map, and compute the catalog fingerprint. The names are
# re-exported here so mcp_view._SYSTEM_TOOL_DEFS and friends resolve.
from .tool_defs import (
    _ENTITY_TOOL_DEFS,
    _NATIVE_TOOL_DEFS,
    _NATIVE_TOOL_NAMES,
    _NATIVE_TOOL_PUBLIC_TO_INTERNAL,
    _SYSTEM_TOOL_DEFS,
    _TOOL_ANNOTATIONS,
)
from .audit import Outcome
from .tool_common import _CAP_FORBIDDEN_MESSAGE, _resolve_area_id, _ProgressBus, _approved_exec_ctx, _approval_resource, _gate, _mesa_advisory_ctx, _mesa_confirm_annotation, _operator_accepted_result, _pending_or_inline, _progress_ctx, _record_version, _restore_ctx, _set_progress_status, _tool_error, _tool_success
from .policy_engine import (EntityCreationNotPermitted, Permission, assist_expose_check, call_needs_physical_gate, config_entry_registry_context, device_config_entry_ids, device_registry_entity_ids, esphome_entry_writable, filter_entities_for_access_key, filter_service_response, get_effective_hint, resolve, resolve_config_entry_registry_access, resolve_config_entry_registry_write, resolve_device_registry_access, resolve_device_registry_write, resolve_esphome_user_service, resolve_read, resolve_registry_access, resolve_service_targets, service_target_binding_valid, scrub_sensitive_attributes, scrub_state_dict as _scrub_state_dict)
from .rate_limiter import RateLimitResult
from .access_key_store import AccessKeyRecord
from . import yaml_includes
from .logger_control import (
    IntegrationOverride,
    LoggerControlUnavailable,
    VALID_LEVELS as LOGGER_CONTROL_LEVELS,
    VALID_PERSISTENCE as LOGGER_CONTROL_PERSISTENCE,
)

_LOGGER = logging.getLogger(__name__)

# What this server can do, answered identically by initialize and
# server/discover: one definition, because both make the same claim to two
# generations of client and must never disagree.
#
# listChanged and subscribe are FALSE on purpose: this transport is stateless
# and holds no connection to push a notification down. A tool list that moves
# under a connected client is reported in-band, on that client's next call, by
# the staleness advisories.
_SERVER_CAPABILITIES: dict[str, dict] = {
    "tools": {"listChanged": False},
    "resources": {"subscribe": False},
    "prompts": {},
}

# Stamped onto the defs at import: a tool added without an annotation raises
# KeyError here rather than shipping with the spec's unsafe defaults.
for _def in (*_ENTITY_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *_SYSTEM_TOOL_DEFS):
    _def["annotations"] = _TOOL_ANNOTATIONS[_def["name"]]
del _def


def _catalog_fingerprint() -> str:
    """Hash of every tool NAME and inputSchema this build defines.

    The second staleness signal, alongside settings_version: that one tracks the
    operator changing an access key, this one tracks the code changing under a client
    that already fetched its tool list. A client enforces its cached schema
    before sending, so an argument added after that fetch is unusable to it and
    no server-side leniency helps; advising a reconnect is the only remedy.

    Hashes the FULL static catalog, never the per-access-key announced subset, so a
    permission change does not masquerade as a deploy. Descriptions are excluded:
    reworded guidance needs no refetch, and counting prose edits as staleness
    would fire the notice constantly and train operators to ignore it.
    """
    catalog = [
        (d["name"], d.get("inputSchema"))
        for d in (*_ENTITY_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *_SYSTEM_TOOL_DEFS, *mesa_tool_defs())
    ]
    return content_hash(sorted(catalog, key=lambda row: row[0]))[:16]


_TOOL_CATALOG_FINGERPRINT = _catalog_fingerprint()


def tool_catalog_counts() -> dict[str, int]:
    """The tool count, from the registry itself. The ONLY place it is defined.

    Every other statement of the count derives from here: the docs contract
    tests, the admin info endpoint, and scripts/count_tools.py. Native means HA's own
    domain-prefixed MCP tool names, which Phoenix implements 1:1; additional is
    everything Phoenix defines beyond them, mesa_* included.
    """
    names = {
        d["name"]
        for d in (*_ENTITY_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *_SYSTEM_TOOL_DEFS, *mesa_tool_defs())
    }
    native = names & set(_NATIVE_TOOL_NAMES.values())
    return {"total": len(names), "native": len(native), "additional": len(names - native)}


# Tools that perform writes/actions. They are announced in tools/list only when
# the access key has write scope (any GREEN grant or pass_through). Cap-tied tools
# (those with a "cap", "caps" or "caps_any" key) gate on their capability
# instead; all remaining tools (reads, GetDateTime, get_approval_status) are
# always announced. The announce_all_tools access key flag overrides this gating
# entirely.
_WRITE_GATED_TOOLS = frozenset({
    "call_service",
    "HassTurnOn", "HassTurnOff", "HassLightSet", "HassFanSetSpeed",
    "HassClimateSetTemperature", "HassSetPosition", "HassSetVolume",
    "HassSetVolumeRelative", "HassMediaPause", "HassMediaUnpause",
    "HassMediaNext", "HassMediaPrevious", "HassMediaSearchAndPlay",
    "HassMediaPlayerMute", "HassMediaPlayerUnmute", "HassCancelAllTimers",
    "HassStopMoving",
    "HassVacuumStart", "HassVacuumReturnToBase", "HassVacuumCleanArea",
})


def _tool_required_caps(tool_def: dict) -> tuple[str, ...]:
    """Return capabilities which are all required."""
    caps = tool_def.get("caps")
    if isinstance(caps, (list, tuple)):
        return tuple(cap for cap in caps if isinstance(cap, str))
    cap = tool_def.get("cap")
    return (cap,) if isinstance(cap, str) else ()


def _tool_any_caps(tool_def: dict) -> tuple[str, ...]:
    """Return capabilities for which any non-Deny grant makes a tool visible."""
    caps = tool_def.get("caps_any")
    if isinstance(caps, (list, tuple)):
        return tuple(cap for cap in caps if isinstance(cap, str))
    return ()


def _tool_caps(tool_def: dict) -> tuple[str, ...]:
    return _tool_required_caps(tool_def) + _tool_any_caps(tool_def)


def _write_gate_open(internal_name: str, access_key: AccessKeyRecord, has_write: bool) -> bool:
    """Whether a write-gated tool can do anything for this access key."""
    return has_write or (internal_name == "call_service" and any(
        effective_cap(access_key, cap) != CAP_DENY for cap in CALL_SERVICE_NO_TARGET_CAPS
    ))


def _tool_is_announced(
    tool_def: dict, access_key: AccessKeyRecord, has_write: bool, hass: HomeAssistant
) -> bool:
    """Whether a tool should appear in tools/list for this access key.

    cap-tied tools gate on their capability; write/action tools gate on write
    scope; a tool carrying a "requires" key additionally needs that system
    surface to exist; everything else (reads, GetDateTime, get_approval_status)
    is always announced. The caller applies the announce_all_tools override
    separately.

    THREE MIRRORS: this predicate, _tool_gate_map (get_capability_summary), and
    agentcli.build_mcp_tool_list (Agent Chat, Assist, voice, AI Task) each decide
    announcement independently. Changing the rules here means changing all three.
    """
    caps = _tool_required_caps(tool_def)
    any_caps = _tool_any_caps(tool_def)
    if any(effective_cap(access_key, cap) == CAP_DENY for cap in caps):
        return False
    if any_caps and all(effective_cap(access_key, cap) == CAP_DENY for cap in any_caps):
        return False
    internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(
        tool_def["name"], tool_def["name"]
    )
    if not caps and not any_caps and internal_name in _WRITE_GATED_TOOLS and not _write_gate_open(
        internal_name, access_key, has_write
    ):
        return False
    return _requires_satisfied(tool_def, hass)


def _tool_gate_map(
    access_key: AccessKeyRecord, data: PhoenixData, hass: HomeAssistant
) -> dict[str, Any]:
    """Classify every tool for this access key at the tool level (no entity oracle).

    Buckets: usable (announced, executes directly); needs_approval (a cap-tied
    tool whose cap is Confirm and which actually gates, so returns
    pending_approval); unavailable (cap denied, a write/action tool without write
    scope, a household-restricted tool, or a missing system surface).

    The `_EXECUTOR_REGISTRY` check is load-bearing: a cap-tied read checks for
    CAP_DENY and never calls `_gate`, so bucketing by cap mode alone would report
    reads as queued for approval and mis-plan an agent that orients with
    get_capability_summary. A registered executor is the right proxy because the
    Confirm flow applies an approved call through `_EXECUTOR_REGISTRY`; a catalog
    test pins the registry against the handlers that really call `_gate`.

    A tool missing its system surface (no ESPHome, no Device Builder) also gets an
    unavailable_reasons entry: an operator can grant a denied capability but not
    a missing add-on. The view is static, so call_service and the native Hass*
    action tools show as usable with write scope even though a target may still
    hit a physical/dual gate or MESA confirm. Mirrors _tool_is_announced.
    """
    from .home_companion import is_companion
    if is_companion(access_key):
        from .agentcli import build_mcp_tool_list
        return {"usable": [tool["name"] for tool in build_mcp_tool_list(access_key, data)],
                "needs_approval": [], "unavailable": []}
    has_write = access_key_has_write_scope(access_key)
    mesa_defs = mesa_tool_defs() if data.mesa is not None else []
    usable: list[str] = []
    needs_approval: list[str] = []
    unavailable: list[str] = []
    reasons: dict[str, str] = {}
    for tool_def in list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + mesa_defs:
        name = tool_def["name"]
        internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(name, name)
        caps = _tool_required_caps(tool_def)
        any_caps = _tool_any_caps(tool_def)
        if not household_tool_access(name):
            unavailable.append(name)
        elif not _requires_satisfied(tool_def, hass):
            unavailable.append(name)
            reasons[name] = _requires_unavailable_reason(tool_def)
        elif caps or any_caps:
            modes = [effective_cap(access_key, cap) for cap in caps]
            any_modes = [effective_cap(access_key, cap) for cap in any_caps]
            if CAP_DENY in modes or (any_modes and all(mode == CAP_DENY for mode in any_modes)):
                unavailable.append(name)
            elif (
                CAP_CONFIRM in modes
                or (any_modes and all(mode == CAP_CONFIRM for mode in any_modes))
            ) and internal_name in _EXECUTOR_REGISTRY:
                needs_approval.append(name)
            else:
                usable.append(name)
        elif internal_name in _WRITE_GATED_TOOLS:
            (usable if _write_gate_open(internal_name, access_key, has_write) else unavailable).append(name)
        else:
            usable.append(name)
    result: dict[str, Any] = {
        "usable": sorted(usable),
        "needs_approval": sorted(needs_approval),
        "unavailable": sorted(unavailable),
    }
    if reasons:
        result["unavailable_reasons"] = dict(sorted(reasons.items()))
    return result


def _jsonrpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _valid_jsonrpc_id(raw_id: Any) -> bool:
    """Is this an MCP-conforming request id?

    MCP narrows JSON-RPC's general Number shape to strings and integers, and a
    request ID must not be null. `bool` is excluded explicitly because it is a
    subclass of `int` in Python.
    """
    if isinstance(raw_id, bool):
        return False
    return isinstance(raw_id, (str, int))


def _sanitize_jsonrpc_id(raw_id: Any) -> str | int | None:
    """Reduce a JSON-RPC id to something safe to put in a RESPONSE envelope.

    Only for the error paths, where a reply has to carry an id and the request's
    own is unusable: the spec says an Invalid Request answers with `id: null`.
    A conforming request never needs this, since `_classify_jsonrpc_message`
    refuses a bad id before dispatch rather than quietly repairing it.
    """
    return raw_id if _valid_jsonrpc_id(raw_id) else None


def _jsonrpc_error(
    msg_id: Any, code: int, message: str, *, data: dict | None = None
) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


_MCP_MODERN_VERSION = "2026-07-28"
_MCP_PROTOCOL_META = "io.modelcontextprotocol/protocolVersion"
_MCP_CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"
_MCP_CLIENT_CAPABILITIES_META = "io.modelcontextprotocol/clientCapabilities"
_MCP_SERVER_INFO_META = "io.modelcontextprotocol/serverInfo"
_MCP_NAMED_METHOD_FIELDS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
}
_MCP_CACHEABLE_METHODS = frozenset({
    "server/discover", "tools/list", "prompts/list", "resources/list",
    "resources/templates/list", "resources/read",
})
_MCP_LEGACY_SESSION_TTL_SECONDS = 3600.0
_MCP_LEGACY_SESSION_LIMIT = 256
_MCP_LEGACY_SESSION_ACCESS_KEY_LIMIT = 8


def _protocol_error_response(
    msg_id: Any,
    code: int,
    message: str,
    request_id: str,
    *,
    data: dict | None = None,
    status: int = 400,
) -> web.Response:
    """Return a protocol-level JSON-RPC error with its required HTTP status."""
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(_jsonrpc_error(msg_id, code, message, data=data)),
        headers={"X-Phoenix-Request-ID": request_id},
    )


def _audit_mcp_transport(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    request_id: str,
    client_ip: str,
    *,
    body: Any = None,
    outcome: Outcome = "invalid_request",
    resource: str = "/api/phoenix-mcp",
) -> None:
    """Audit an authenticated MCP message that does not reach dispatch."""
    method = body.get("method") if isinstance(body, dict) else None
    _log(
        data,
        access_key,
        request_id=request_id,
        method=method if isinstance(method, str) and method else "POST",
        resource=resource,
        outcome=outcome,
        client_ip=client_ip,
    )


def _request_meta(params: Any) -> dict | None:
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else None


def _is_modern_request(body: dict, request: web.Request) -> bool:
    """Select an MCP era without inferring modern behavior from method names."""
    params = body.get("params")
    meta = _request_meta(params)
    if meta is not None and _MCP_PROTOCOL_META in meta:
        return True
    header_version = request.headers.get("MCP-Protocol-Version")
    if body.get("method") == "initialize":
        return header_version == _MCP_MODERN_VERSION
    return bool(header_version and header_version not in MCP_LEGACY_PROTOCOL_VERSIONS)


def _decode_mcp_header(value: str) -> str | None:
    """Decode the modern MCP Base64 sentinel form, or return a plain value."""
    if not (value.startswith("=?base64?") and value.endswith("?=")):
        return value
    encoded = value[len("=?base64?"):-2]
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _validate_modern_request(
    body: dict, request: web.Request, request_id: str
) -> web.Response | None:
    """Validate 2026-07-28 per-request metadata and mirrored HTTP headers."""
    msg_id = _sanitize_jsonrpc_id(body.get("id"))
    params = body.get("params")
    meta = _request_meta(params)
    header_version = request.headers.get("MCP-Protocol-Version")
    body_version = meta.get(_MCP_PROTOCOL_META) if meta is not None else None

    if body_version is None and isinstance(header_version, str) and header_version not in MCP_PROTOCOL_VERSIONS:
        return _protocol_error_response(
            msg_id, -32022, "Unsupported protocol version", request_id,
            data={"supported": list(MCP_PROTOCOL_VERSIONS), "requested": header_version},
        )

    if not isinstance(header_version, str) or not isinstance(body_version, str):
        return _protocol_error_response(
            msg_id, -32020, "Header mismatch: protocol version metadata is required.",
            request_id,
        )
    if header_version != body_version:
        return _protocol_error_response(
            msg_id, -32020, "Header mismatch: MCP-Protocol-Version does not match the request body.",
            request_id,
        )
    if body_version not in MCP_PROTOCOL_VERSIONS or body_version != _MCP_MODERN_VERSION:
        return _protocol_error_response(
            msg_id,
            -32022,
            "Unsupported protocol version",
            request_id,
            data={"supported": list(MCP_PROTOCOL_VERSIONS), "requested": body_version},
        )

    if meta is None or not isinstance(meta.get(_MCP_CLIENT_CAPABILITIES_META), dict):
        return _protocol_error_response(
            msg_id, -32602, "Invalid params: client capabilities metadata is required.",
            request_id,
        )
    client_info = meta.get(_MCP_CLIENT_INFO_META)
    if client_info is not None and (
        not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not isinstance(client_info.get("version"), str)
    ):
        return _protocol_error_response(
            msg_id, -32602, "Invalid params: clientInfo metadata is malformed.",
            request_id,
        )

    method = body.get("method")
    method_header = request.headers.get("Mcp-Method")
    if not isinstance(method, str) or method_header != method:
        return _protocol_error_response(
            msg_id, -32020, "Header mismatch: Mcp-Method does not match the request body.",
            request_id,
        )

    name_field = _MCP_NAMED_METHOD_FIELDS.get(method)
    if name_field is not None:
        body_name = params.get(name_field) if isinstance(params, dict) else None
        raw_name = request.headers.get("Mcp-Name")
        header_name = _decode_mcp_header(raw_name) if isinstance(raw_name, str) else None
        if not isinstance(body_name, str) or header_name != body_name:
            return _protocol_error_response(
                msg_id, -32020, "Header mismatch: Mcp-Name does not match the request body.",
                request_id,
            )
    return None


def _modernize_response(response_msg: dict | None, method: str) -> dict | None:
    """Add wire-only 2026-07-28 result fields in place; legacy replies never pass here.

    Also remaps the legacy unknown-resource code (-32002) to -32602 and the
    authority-changed code (-32000) to MCP_AUTHORITY_CHANGED_ERROR.
    """
    if response_msg is None:
        return None
    error = response_msg.get("error")
    if isinstance(error, dict):
        if error.get("code") == -32002:
            error["code"] = -32602
        elif error.get("code") == -32000:
            error["code"] = MCP_AUTHORITY_CHANGED_ERROR
    result = response_msg.get("result")
    if not isinstance(result, dict):
        return response_msg
    result["resultType"] = "complete"
    meta = result.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta.setdefault(
            _MCP_SERVER_INFO_META,
            {"name": "Phoenix MCP", "version": PHOENIX_VERSION},
        )
    if method in _MCP_CACHEABLE_METHODS:
        result.setdefault("ttlMs", MCP_DISCOVER_TTL_MS if method == "server/discover" else 0)
        result.setdefault("cacheScope", "private")
    return response_msg


def _classify_jsonrpc_message(body: dict) -> tuple[str, Any]:
    """Validate one JSON-RPC message envelope (jsonrpc == "2.0" already checked).

    Runs before dispatch in BOTH the single-request and batch paths, so the shape
    is validated once. A non-object `params` or non-string `method` would make
    the tools/call dispatcher raise before the per-call exception net and escape
    as an aiohttp 500, and a malformed `id` must never be coerced to None and
    dispatched: the side effect would apply and be answered with an id the
    sender cannot correlate. Returns one of:
      ("dispatch", (method, params)) - a well-formed request for _dispatch_mcp.
      ("accepted", None) - a JSON-RPC response object (no method, carries
          result/error): the legacy transport accepts it without a reply; the
          modern transport rejects it before this classification.
      ("unsupported", (code, message)) - a STRUCTURALLY VALID request that cannot
          be dispatched (positional params): error a real request, STAY SILENT
          for a notification, since the envelope is valid.
      ("error", (code, message)) - a malformed envelope; the caller emits the
          JSON-RPC error with the message's own id.
    """
    method = body.get("method")
    # A response object carries result/error and no method. The legacy transport
    # accepts these without a reply; post() rejects them when a modern protocol
    # signal is present, because modern clients may only POST requests or
    # notifications.
    if method is None and ("result" in body or "error" in body):
        return "accepted", None
    if not isinstance(method, str) or not method:
        return "error", (-32600, "Invalid Request.")
    # A present MCP id must be a String or Integer. A malformed one must not be
    # coerced to None and dispatched: `{"id": {}, "method": "tools/call"}` would
    # run its side effect and answer with an id the caller cannot match to its
    # request, the shape that makes a client retry a non-idempotent call.
    # Refusing costs a conforming client nothing.
    if "id" in body and not _valid_jsonrpc_id(body["id"]):
        return "error", (-32600, "Invalid Request.")
    raw_params = body.get("params")
    # JSON-RPC allows params to be an Array (positional) or Object (by-name). Phoenix MCP
    # only dispatches by-name (its handlers index params as a dict). A list is thus
    # STRUCTURALLY VALID but unsupported ("unsupported"), which matters because a
    # valid positional-params NOTIFICATION must still get no response; a scalar is
    # not a structured value at all, so it is a malformed envelope ("error").
    if raw_params is not None and not isinstance(raw_params, dict):
        if isinstance(raw_params, list):
            return "unsupported", (-32602, "Positional parameters are not supported.")
        return "error", (-32602, "Invalid params.")
    return "dispatch", (method, raw_params or {})



















# One-time in-band advisory appended to a tools/call response when this access key's
# settings changed after its last tools/list (the stateless transport cannot
# push notifications/tools/list_changed). Once per staleness epoch.
_STALE_TOOLS_ADVISORY = (
    "Notice: this access key's permissions or settings were changed by the operator "
    "after your client last fetched the tool list, so your tool list and "
    "capability summary may be stale. Call get_capability_summary for the "
    "current state and ask the user to reconnect or refresh this MCP server's "
    "tools."
)

# The deploy-side sibling of the advisory above. Deliberately does NOT mention
# get_capability_summary: that reports permissions, which have not changed here,
# and following it would waste a call and answer the wrong question. What changed
# is the SCHEMAS, and only a reconnect can fix that, because a client enforces
# its own cached schema before the request is ever sent.
_STALE_CATALOG_ADVISORY = (
    "Notice: Phoenix MCP was updated after your client last fetched the tool "
    "list, so some tools may have gained or changed arguments your copy of their "
    "schemas does not have. If a call fails on an argument you believe is valid, "
    "that is the likely cause. Ask the user to reconnect or refresh this MCP "
    "server's tools; re-reading the capability summary will not fix it."
)









def _normalize_fields(fields: Any) -> list[str]:
    """Coerce the get_state `fields` arg to a list of field names.

    Accepts a list of strings or a single comma-separated string (a common agent
    mistake); anything else yields an empty list, which selects the lean default.
    """
    if isinstance(fields, str):
        return [f for f in (p.strip() for p in fields.split(",")) if f]
    if isinstance(fields, list):
        return [str(f).strip() for f in fields if str(f).strip()]
    return []


def _select_state_fields(scrubbed: dict, fields: list[str]) -> dict:
    """Return only the requested fields from an already-scrubbed state dict.

    Top-level keys (state, last_changed, last_updated, ...) are taken as-is;
    'attr.<name>' selects one attribute; 'attributes' selects all (scrubbed)
    attributes. entity_id is always included. Unknown fields are ignored. Runs
    AFTER scrubbing, so a scrubbed attribute can never be selected back in.
    """
    attrs = scrubbed.get("attributes", {})
    out: dict = {"entity_id": scrubbed.get("entity_id")}
    picked_attrs: dict = {}
    for f in fields:
        if f == "attributes":
            picked_attrs.update(attrs)
        elif f.startswith("attr."):
            name = f[len("attr."):]
            if name in attrs:
                picked_attrs[name] = attrs[name]
        elif f in scrubbed and f != "attributes":
            out[f] = scrubbed[f]
    if picked_attrs:
        out["attributes"] = picked_attrs
    return out


def _lean_state(scrubbed: dict) -> dict:
    """Return the domain-aware lean view of an already-scrubbed state dict."""
    eid = scrubbed.get("entity_id", "") or ""
    domain = eid.split(".")[0] if "." in eid else ""
    attrs = scrubbed.get("attributes", {})
    keep = set(LEAN_ALWAYS_ATTRS) | set(DOMAIN_IMPORTANT_ATTRIBUTES.get(domain, ()))
    lean_attrs = {k: v for k, v in attrs.items() if k in keep}
    out: dict = {"entity_id": eid, "state": scrubbed.get("state")}
    if lean_attrs:
        out["attributes"] = lean_attrs
    return out


def _project_state(scrubbed: dict, fields: Any, detailed: bool) -> dict:
    """Apply presentation-only field projection to a scrubbed state dict.

    `detailed` returns the full dict, explicit fields the requested subset,
    otherwise the domain-aware lean view. Never bypasses scrubbing (the input is
    already scrubbed).
    """
    if detailed:
        return scrubbed
    norm = _normalize_fields(fields)
    if norm:
        return _select_state_fields(scrubbed, norm)
    return _lean_state(scrubbed)


def _readable_state(entity_id: Any, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[Any, Permission]:
    """Return (state, permission) for one requested ID, or (None, ...) when unreadable."""
    perm = resolve_read(entity_id, access_key, hass)
    if perm not in (Permission.READ, Permission.WRITE):
        return None, perm
    return hass.states.get(entity_id), perm


async def _tool_get_state(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: one entity's state, or several in request order."""
    args, error = normalize_tool_args("get_state", args)
    if error:
        return _tool_error(error), "invalid_request", "get_state"
    requested = args.get("entity_id")
    fields, detailed = args.get("fields"), bool(args.get("detailed"))
    if isinstance(requested, list):
        if not 1 <= len(requested) <= GET_STATE_MAX_IDS or not all(isinstance(item, str) for item in requested):
            return _tool_error(f"entity_id must be a string or a list of 1 to {GET_STATE_MAX_IDS} entity ID strings."), "invalid_request", "get_state"
        rows: list[dict] = []
        not_found: list[str] = []
        for entity_id in dict.fromkeys(item.strip() for item in requested):
            state, _ = _readable_state(entity_id, access_key, hass)
            if state is None:
                not_found.append(entity_id)
            else:
                rows.append(_project_state(scrub_sensitive_attributes(state), fields, detailed))
        body = {"count": len(rows), "states": rows, "not_found": not_found}
        return _tool_success(json.dumps(body, default=str)), "allowed", f"entities:{len(rows)}"
    entity_id = str_arg(requested)
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "get_state"
    state, perm = _readable_state(entity_id, access_key, hass)
    if state is None:
        # Audit-only distinction; the reply is identical for missing and hidden.
        outcome = "denied" if perm in (Permission.NO_ACCESS, Permission.DENY) else "not_found"
        return _tool_error("Entity not found."), outcome, entity_id
    result = _project_state(scrub_sensitive_attributes(state), fields, detailed)
    return _tool_success(json.dumps(result, default=str)), "allowed", entity_id


async def _tool_get_history(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: fetch one bounded page of state history."""
    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "get_history"

    perm = resolve_read(entity_id, access_key, hass)
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    if perm in (Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id

    mode = str(args.get("mode") or "state_changes").strip().lower()
    if mode not in ("state_changes", "significant_states"):
        return _tool_error(
            "Invalid mode. Must be one of: state_changes, significant_states."
        ), "invalid_request", entity_id
    order = str(args.get("order") or "oldest_first").strip().lower()
    if order not in ("oldest_first", "newest_first"):
        return _tool_error(
            "Invalid order. Must be one of: oldest_first, newest_first."
        ), "invalid_request", entity_id
    try:
        window = parse_recorder_window(args, default_range=timedelta(hours=24))
    except ValueError as err:
        return _tool_error(str(err)), "invalid_request", entity_id
    if mode == "significant_states" and window.end - window.start > MAX_SIGNIFICANT_STATES_RANGE:
        return _tool_error(
            "significant_states supports at most 7 days per request because Home Assistant cannot bound this query in the database. Use state_changes for longer ranges."
        ), "invalid_request", entity_id
    newest = order == "newest_first"
    # A cursor continues where the previous page stopped: forward it is the new
    # lower bound, backward it is the new exclusive upper bound.
    lower = window.start if newest else window.page_start
    upper = (window.page_start if args.get("cursor") else window.end) if newest else window.end

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder import history as rec_history

        instance = get_instance(hass)
        keep_days = instance.keep_days

        async def fetch(after: datetime, before: datetime, count: int | None) -> list[Any]:
            fn: functools.partial[Any]
            if mode == "state_changes":
                fn = functools.partial(
                    rec_history.state_changes_during_period,
                    hass, after, before, entity_id, True, False, count, False,
                )
            else:
                fn = functools.partial(
                    rec_history.get_significant_states,
                    hass, after, before, [entity_id], None, False, True, False, False,
                )
            result = cast(dict[str, list[Any]], await instance.async_add_executor_job(fn))
            return result.get(entity_id, [])

        if not newest:
            states_list = await fetch(lower, upper, window.limit + 1)
            has_more = len(states_list) > window.limit
            states_list = states_list[:window.limit]
        elif mode == "state_changes":
            states_list, has_more = await newest_page(fetch, lower, upper, window.limit)
        else:
            # Significant states are already bounded to 7 days and read whole.
            states_list = await fetch(lower, upper, None)
            has_more = len(states_list) > window.limit
            states_list = states_list[-window.limit:]
    except Exception:
        _LOGGER.warning("MCP history call failed for entity %s", entity_id, exc_info=True)
        return _tool_error("History call failed."), "denied", entity_id

    page_dicts: list[dict[str, Any]] = [
        dict(s.as_dict()) if hasattr(s, "as_dict") else dict(s) for s in states_list
    ]
    if newest:
        page_dicts.reverse()
    if mode == "significant_states":
        history = [_scrub_state_dict(d) for d in page_dicts]
        for row in history:
            for key in ("last_changed", "last_updated"):
                if isinstance(row.get(key), datetime):
                    row[key] = iso_utc(row[key])
    else:
        history = [
            {
                "state": d.get("state"),
                "when": iso_utc(stamp) if (stamp := state_timestamp(d)) is not None else None,
            }
            for d in page_dicts
        ]
    last_timestamp = state_timestamp(page_dicts[-1]) if page_dicts else None
    if has_more and last_timestamp is None:
        _LOGGER.warning("MCP history row for entity %s has no usable timestamp", entity_id)
        return _tool_error("History call returned an invalid timestamp."), "invalid_request", entity_id
    if newest:
        covered_start = last_timestamp if has_more and last_timestamp is not None else window.start
        covered_end = upper
    else:
        covered_start = window.page_start
        covered_end = last_timestamp if has_more and last_timestamp is not None else window.end
    warnings = retention_warnings("short_term", window.start, keep_days, utcnow())
    body = recorder_envelope(
        entity_id=entity_id,
        window=window,
        covered_start=covered_start,
        covered_end=covered_end,
        rows=history,
        timestamp_getter=(
            (lambda row: state_timestamp({"last_changed": row.get("when")}))
            if mode == "state_changes"
            else state_timestamp
        ),
        effective_limit=window.limit,
        has_more=has_more,
        next_cursor=last_timestamp if has_more else None,
        retention=retention_metadata("short_term", keep_days),
        warnings=warnings,
        result_key="history",
    )
    body["order"] = order
    body["mode"] = mode
    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id


async def _tool_get_statistics(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: fetch one aligned and bounded page of Recorder statistics."""
    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "get_statistics"

    perm = resolve_read(entity_id, access_key, hass)
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    if perm in (Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id

    try:
        window = parse_recorder_window(args, default_range=timedelta(days=30))
    except ValueError as err:
        return _tool_error(str(err)), "invalid_request", entity_id

    period = args.get("period", "hour")
    if period not in STATISTIC_PERIOD_LIMITS:
        return _tool_error(
            "Invalid period. Must be one of: 5minute, hour, day, week, month, year."
        ), "invalid_request", entity_id
    if period == "year":
        from awesomeversion import AwesomeVersion
        from homeassistant.const import __version__ as ha_version

        if AwesomeVersion(ha_version) < AwesomeVersion("2026.3.0"):
            return _tool_error(
                "The year statistics period requires Home Assistant 2026.3 or newer."
            ), "invalid_request", entity_id

    valid_types = {"mean", "min", "max", "sum", "state", "change", "last_reset"}
    raw_types = args.get("statistic_types")
    if raw_types is not None and (
        not isinstance(raw_types, list)
        or not raw_types
        or any(not isinstance(item, str) or item not in valid_types for item in raw_types)
    ):
        return _tool_error(
            "statistic_types must be a non-empty array containing only: mean, min, max, sum, state, change, last_reset."
        ), "invalid_request", entity_id
    type_set = cast(
        set[Literal["mean", "min", "max", "sum", "state", "change", "last_reset"]],
        set(raw_types) if raw_types else valid_types,
    )
    page_start, page_end, effective_limit, has_more = statistic_page(window, period)

    try:
        from homeassistant.components.recorder import get_instance

        instance = get_instance(hass)
        keep_days = instance.keep_days
        query_end = page_end
        if period in ("day", "week", "month", "year"):
            query_end -= timedelta.resolution
        fn = functools.partial(
            read_statistics_page, hass, page_start, query_end, entity_id, period, type_set,
        )
        raw_rows, units, unit_warnings = await instance.async_add_executor_job(fn)
    except Exception:
        _LOGGER.warning("MCP statistics call failed for entity %s", entity_id, exc_info=True)
        return _tool_error("Statistics call failed."), "invalid_request", entity_id

    statistics: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        stamp = statistic_timestamp(raw_row)
        if stamp is None or not page_start <= stamp < page_end:
            continue
        row = dict(raw_row)
        row["start"] = iso_utc(stamp)
        # Core returns bucket ends and resets as epoch seconds; render them like start.
        for key in ("end", "last_reset"):
            value = row.get(key)
            if isinstance(value, datetime):
                row[key] = iso_utc(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                row[key] = iso_utc(datetime.fromtimestamp(value, tz=dt_util.UTC))
        statistics.append(row)
        if len(statistics) == effective_limit:
            break
    retention_kind: Literal["short_term", "long_term"] = (
        "short_term" if period == "5minute" else "long_term"
    )
    warnings = retention_warnings(retention_kind, window.start, keep_days, utcnow()) + unit_warnings
    if not statistics:
        warnings.append(
            "No statistics were returned. The entity may not expose a state_class, may not produce Recorder statistics, or may have no data in this period."
        )
    body = recorder_envelope(
        entity_id=entity_id,
        window=window,
        covered_start=page_start,
        covered_end=page_end,
        rows=statistics,
        timestamp_getter=statistic_timestamp,
        effective_limit=effective_limit,
        has_more=has_more,
        next_cursor=page_end if has_more else None,
        retention=retention_metadata(retention_kind, keep_days),
        warnings=warnings,
        result_key="statistics",
    )
    body["period"] = period
    # Every value field is in units.display_unit_of_measurement; null means Recorder
    # holds no metadata for this entity, not that the values are unitless.
    body["units"] = units
    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id


async def _tool_get_calendar_events(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """Read full integration events including UID and occurrence identity."""
    from .tools.calendar import _calendar_entity, _calendar_events, _safe_calendar_data, _calendar_window
    args, error = normalize_tool_args("get_calendar_events", args)
    if error:
        return _tool_error(error), "invalid_request", "get_calendar_events"
    try:
        entity = _calendar_entity(args.get("entity_id"), access_key, hass)
        # Normalize via the shared parser so relative times ("24h") work.
        for key in ("start_time", "end_time"):
            if key in args:
                if not isinstance(args[key], str):
                    raise ValueError("Calendar times must be strings.")
                args[key] = dt_util.as_local(_parse_time_param(args[key])).isoformat()
        start, end = _calendar_window(args, defaults=True)
        events = await _calendar_events(entity, hass, start, end)
        _calendar_entity(args.get("entity_id"), access_key, hass)
        paged = offset_page(events, args, default_limit=CALENDAR_EVENTS_DEFAULT_LIMIT, max_limit=CALENDAR_MAX_EVENTS)
        if isinstance(paged, str):
            raise ValueError(paged)
        page, envelope = paged
        body = {"calendar_id": args["entity_id"], **envelope, "events": [
            {**event, "content_hash": content_hash(event)} for event in page]}
        return _tool_success(json.dumps(_safe_calendar_data(body, access_key, hass))), "allowed", args["entity_id"]
    except AuthorityChangedError:
        raise
    except PermissionError:
        return _tool_error("Calendar not found."), "denied", "get_calendar_events"
    except ValueError as exc:
        return _tool_error(_safe_calendar_data(str(exc), access_key, hass)), "invalid_request", "get_calendar_events"
    except Exception:
        _LOGGER.exception("Calendar read failed")
        return _tool_error("Failed to read calendar events."), "invalid_request", "get_calendar_events"


async def _tool_call_service(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: call a HA service with entity targets filtered to WRITE-permitted entities."""
    args, error = normalize_tool_args("call_service", args)
    if error:
        # Diagnostics must not override a capability denial. Inspect only the
        # recognized service/selector shape; never repair an unknown selector.
        service_arg = args.get("service")
        domain = str_arg(service_arg.get("domain")) if isinstance(service_arg, dict) else str_arg(args.get("domain"))
        service = str_arg(service_arg.get("name")) if isinstance(service_arg, dict) else str_arg(service_arg)
        service_key = f"{domain}/{service}"
        cap = None
        if domain == "calendar" and service != "get_events":
            cap = "cap_calendar_write"
        elif service_key in DUAL_GATE_SERVICES:
            cap = "cap_restart"
        elif call_needs_physical_gate(
            domain=domain, service=service,
            entity_id=args.get("entity_id"), device_id=args.get("device_id"),
            area_id=args.get("area_id"), access_key=access_key, hass=hass,
        ):
            cap = "cap_physical_control"
        elif service_key in NO_TARGET_SERVICES:
            cap = "cap_yaml_edit"
        if cap is not None and effective_cap(access_key, cap) == CAP_DENY:
            return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "call_service"
        return _tool_error(error), "invalid_request", "call_service"
    # Coerced before anything gates on them: service_key and the physical-gate
    # domain check are set-membership tests, so a list-valued domain raises
    # instead of being refused.
    domain = str_arg(args.get("domain"))
    service = str_arg(args.get("service"))
    if not domain or not service:
        return _tool_error("Missing required arguments: domain and service"), "invalid_request", "call_service"

    try:
        household_service_kwargs(hass, domain, service)
    except Unauthorized:
        return _tool_error("Forbidden."), "denied", "call_service"

    if domain == "calendar" and service != "get_events":
        if effective_cap(access_key, "cap_calendar_write") == CAP_DENY:
            return _tool_error("Forbidden."), "denied", "call_service"
        return _tool_error("Use the dedicated calendar event authoring tools for calendar writes."), "invalid_request", "call_service"

    secondary_targets = await unsupported_secondary_targets(
        hass, args.get("service_data"), domain=domain, service=service
    )
    if secondary_targets:
        return (
            _tool_error(secondary_target_error(secondary_targets)),
            "invalid_request",
            "call_service",
        )

    service_key = f"{domain}/{service}"

    from .command_execution import bind_service_plan

    if (service_key not in DUAL_GATE_SERVICES | NO_TARGET_SERVICES
            and domain != _ESPHOME_DOMAIN
            and not any((args.get("entity_id"), args.get("device_id"), args.get("area_id")))):
        if (call_needs_physical_gate(domain=domain, service=service, access_key=access_key, hass=hass)
                and effective_cap(access_key, "cap_physical_control") == CAP_DENY):
            return _tool_error("Forbidden."), "denied", "call_service"
        return (
            _tool_error("An explicit entity_id, device_id, or area_id target is required for an entity service. Targetless services outside the supported allowlist are unavailable through call_service."),
            "invalid_request", "call_service",
        )

    bound_args = bind_service_plan(args, hass, data, access_key)
    if bound_args is None:
        return _tool_error("Command targets or configuration changed."), "denied", "call_service"
    args = bound_args

    if service_key in DUAL_GATE_SERVICES:
        blocked = await _gate(
            "cap_restart", access_key, hass, data,
            tool_name="call_service", args=args, request_id=request_id,
            client_ip=client_ip, diff=lambda: _build_diff_call_service(args, access_key, hass),
        )
        if blocked is not None:
            return blocked
    elif call_needs_physical_gate(
        domain=domain, service=service,
        entity_id=args.get("entity_id"), device_id=args.get("device_id"),
        area_id=args.get("area_id"), access_key=access_key, hass=hass,
    ):
        if effective_cap(access_key, "cap_physical_control") != CAP_DENY:
            try:
                targets, _ = resolve_service_targets(
                    entity_id=args.get("entity_id"), device_id=args.get("device_id"),
                    area_id=args.get("area_id"), service_domain=domain, access_key=access_key, hass=hass,
                )
            except EntityCreationNotPermitted:
                targets = []
            if not targets:
                return _tool_error("Forbidden."), "denied", "call_service"
        blocked = await _gate(
            "cap_physical_control", access_key, hass, data,
            tool_name="call_service", args=args, request_id=request_id,
            client_ip=client_ip, diff=lambda: _build_diff_call_service(args, access_key, hass),
            target_entities=targets if effective_cap(access_key, "cap_physical_control") != CAP_DENY else None,
        )
        if blocked is not None:
            return blocked
    elif service_key in NO_TARGET_SERVICES:
        blocked = await _gate(
            "cap_yaml_edit", access_key, hass, data,
            tool_name="call_service", args=args, request_id=request_id,
            client_ip=client_ip, diff=lambda: _build_diff_call_service(args, access_key, hass),
        )
        if blocked is not None:
            return blocked
    return await _execute_call_service(
        args, access_key, hass, data, request_id=request_id, client_ip=client_ip,
    )


async def _dispatch_no_target_tool_call(
    hass: HomeAssistant,
    access_key: AccessKeyRecord,
    *,
    domain: str,
    service: str,
    service_data: dict,
    resource: str,
    timeout_noun: str,
    surface_validation_errors: bool,
) -> tuple[dict, Outcome, str]:
    """Call a service that takes NO entity target, and map the outcome.

    Covers three families: the dual-gate services (no entities in hass.states),
    the config-reload family (whose schemas reject an entity_id), and ESPHome
    user-defined actions (schema built from only the arguments the device
    declared).

    Authorization has already happened by the time this runs; it decides nothing.

    The caller chooses whether validation detail is safe to surface. For
    NO_TARGET_SERVICES it is safe because the message describes the caller's
    own reloadable config after the cap_yaml_edit authorization gate.
    """
    if domain in HIGH_RISK_DOMAINS:
        _LOGGER.info(
            "High-risk service call %s/%s by access key %s",
            domain, service, access_key.name,
        )
    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            service_context = household_service_kwargs(hass, domain, service)
            mark_dispatch_started()
            await hass.services.async_call(
                domain, service, service_data, blocking=True, return_response=False,
                **service_context,
            )
    except asyncio.TimeoutError:
        subject = "the device" if timeout_noun == "Action" else "HA"
        return (
            _tool_success(json.dumps({
                "success": True,
                "partial": True,
                "message": f"{timeout_noun} dispatched but {subject} did not respond within the timeout window.",
            })),
            "allowed",
            resource,
        )
    except ServiceNotFound:
        # Generic: never confirm or deny service existence.
        return _tool_error("Forbidden."), "denied", resource
    except (ServiceValidationError, vol.Invalid) as err:
        if not surface_validation_errors:
            return _tool_error("Forbidden."), "denied", resource
        return _tool_error(_validation_error_message(err)), "invalid_request", resource
    except HomeAssistantError:
        return _tool_error("Forbidden."), "denied", resource
    return _tool_success(json.dumps({"success": True})), "allowed", resource


async def _execute_call_service(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    *,
    request_id: str = "",
    client_ip: str | None = None,
    mesa_approved: bool = False,
) -> tuple[dict, str, str]:
    # Coerced before anything gates on them: service_key and the physical-gate
    # domain check are set-membership tests, so a list-valued domain raises
    # instead of being refused.
    domain = str_arg(args.get("domain"))
    service = str_arg(args.get("service"))
    if not domain or not service:
        return _tool_error("Missing required arguments: domain and service"), "invalid_request", "call_service"

    if domain == "calendar" and service != "get_events":
        if effective_cap(access_key, "cap_calendar_write") == CAP_DENY:
            return _tool_error("Forbidden."), "denied", "call_service"
        return _tool_error("Use the dedicated calendar event authoring tools for calendar writes."), "invalid_request", "call_service"

    from .approval_targets import TARGETS_CHANGED, targets_current

    resource = f"service:{domain}/{service}"
    service_key = f"{domain}/{service}"

    if service_key in DUAL_GATE_SERVICES:
        required_cap = "cap_restart"
    elif call_needs_physical_gate(
        domain=domain, service=service,
        entity_id=args.get("entity_id"), device_id=args.get("device_id"),
        area_id=args.get("area_id"), access_key=access_key, hass=hass,
    ):
        required_cap = "cap_physical_control"
    elif service_key in NO_TARGET_SERVICES:
        required_cap = "cap_yaml_edit"
    else:
        required_cap = None
    if required_cap is not None and effective_cap(access_key, required_cap) == CAP_DENY:
        return _tool_error("Forbidden."), "denied", resource

    # Re-run at the executor choke point. Confirm stores caller arguments and a
    # service can register or change its description while approval is pending,
    # so the request-time precheck alone is not an authorization boundary.
    secondary_targets = await unsupported_secondary_targets(
        hass, args.get("service_data"), domain=domain, service=service
    )
    if secondary_targets:
        return (
            _tool_error(secondary_target_error(secondary_targets)),
            "invalid_request",
            resource,
        )

    check_request_authority()
    entity_id = args.get("entity_id")
    device_id = args.get("device_id")
    area_id = args.get("area_id")
    # Selector-bearing service data has already refused at the shared boundary.
    service_data = _sanitize_service_data(args.get("service_data"))
    from .command_execution import service_plan_is_current
    from .const import COMMAND_ROUTING_PLAN_KEY

    local_plan = COMMAND_ROUTING_PLAN_KEY in args
    if not service_plan_is_current(args, hass, data, access_key):
        return _tool_error("Command targets or configuration changed."), "denied", resource

    # DUAL_GATE_SERVICES have no entities in hass.states; routing them through
    # resolve_service_targets always produces an empty list and a spurious 403.
    # The cap_restart gate above is the only permission check required.
    if service_key == "homeassistant/restart" and not service_data:
        # One restart path, so every restart leaves the durable restart receipt.
        return await _execute_restart_ha({}, access_key, hass, data)
    if service_key in DUAL_GATE_SERVICES:
        return await _dispatch_no_target_tool_call(
            hass, access_key, domain=domain, service=service, service_data=service_data,
            resource=resource, timeout_noun="Service",
            surface_validation_errors=False,
        )

    # NO_TARGET_SERVICES are domain-wide config reloads that take no entity target.
    # The normal path below always attaches an explicit entity_id list, which
    # these services' schemas reject, so call them with bare service_data.
    # The cap_yaml_edit gate above is the only permission check required; any
    # entity/device/area the caller passed is ignored (these services have no
    # target semantics). MESA does not apply (no entities to govern).
    if service_key in NO_TARGET_SERVICES:
        # surface_validation_errors=True: post-authorization (cap_yaml_edit is
        # already granted) and the message describes the caller's own reloadable
        # config, not hidden state.
        return await _dispatch_no_target_tool_call(
            hass, access_key, domain=domain, service=service, service_data=service_data,
            resource=resource, timeout_noun="Service",
            surface_validation_errors=True,
        )

    # ESPHome user-defined actions (esphome.<device>_<action>) take NO entity
    # target: HA builds their schema from only the arguments the device declared,
    # so attaching the flattened entity_id list makes voluptuous reject the call
    # outright, and routing them through resolve_service_targets yields
    # an empty list and a spurious 403. Authorization is the owning DEVICE's write
    # scope rather than a capability: the action is defined by that device's own
    # firmware and can only act on it, so an access key that may already actuate the
    # device's entities is exactly the access key that may invoke its actions. MESA
    # does not apply (no entity target to govern). A service no LOADED entry
    # claims falls through to the normal path and refuses uniformly, so a stale
    # registration left behind by an unload reveals nothing.
    if domain == _ESPHOME_DOMAIN:
        esphome_entry = resolve_esphome_user_service(hass, service)
        if esphome_entry is not None:
            if not esphome_entry_writable(hass, esphome_entry, access_key):
                return _tool_error("Forbidden."), "denied", resource
            # A device-defined schema makes a wrong argument NAME the likeliest
            # failure, and the caller can fix it, so validation errors surface.
            return await _dispatch_no_target_tool_call(
                hass, access_key, domain=domain, service=service, service_data=service_data,
                resource=resource, timeout_noun="Action",
                surface_validation_errors=True,
            )

    try:
        if not any((entity_id, device_id, area_id)):
            if domain == "esphome":
                return _tool_error("Forbidden."), "denied", resource
            return (
                _tool_error("An explicit entity_id, device_id, or area_id target is required for an entity service. Targetless services outside the supported allowlist are unavailable through call_service."),
                "invalid_request", resource,
            )
        permitted_entities, _requested_count = resolve_service_targets(
            entity_id=entity_id,
            device_id=device_id,
            area_id=area_id,
            service_domain=domain,
            access_key=access_key,
            hass=hass,
        )
    except EntityCreationNotPermitted:
        return _tool_error("Forbidden."), "denied", resource

    if not permitted_entities or not service_target_binding_valid(domain, service, permitted_entities, hass):
        return _tool_error("Forbidden."), "denied", resource

    if not service_plan_is_current(args, hass, data, access_key, permitted_entities):
        return _tool_error("Command targets or configuration changed."), "denied", resource

    if not targets_current(args, permitted_entities, hass):
        return _tool_error(TARGETS_CHANGED), "denied", resource

    # MESA enforcement runs last, on the flattened entity list Phoenix MCP already
    # permitted (targets are always flattened to explicit entity_ids; device_id,
    # area_id and "all" never reach HA). MESA never sees entities Phoenix MCP
    # denied; it can drop entities, gate the whole call for confirmation, or
    # block outright.
    from .const import APPROVAL_TARGETS_KEY
    exact_approval = APPROVAL_TARGETS_KEY in args or _approved_exec_ctx.get()
    local_constraints: dict[str, Any] = {"require_all": True} if local_plan or exact_approval else {}
    if local_plan:
        local_constraints["approval_args"] = args
    mesa_outcome = await async_apply_mesa_to_call(
        hass, data, access_key,
        domain=domain, service=service, service_data=service_data,
        entities=permitted_entities,
        request_id=request_id, client_ip=client_ip, session_id=request_id,
        confirm_approved=mesa_approved or _approved_exec_ctx.get(),
        **local_constraints,
    )
    if mesa_outcome.blocked:
        fire_mesa_blocked_event(hass, access_key, mesa_outcome.blocked)
    if mesa_outcome.decision == "pending":
        # Same holding behaviour as a capability confirm (tool_common._gate):
        # one definition, so the two kinds of gate cannot answer differently.
        return await _pending_or_inline(hass, data, access_key, mesa_outcome.approval)
    if mesa_outcome.decision == "deny":
        return _tool_error("Forbidden."), "denied", resource
    check_request_authority()
    permitted_entities = mesa_outcome.entities
    if not service_target_binding_valid(domain, service, permitted_entities, hass):
        return _tool_error("Forbidden."), "denied", resource
    if not service_plan_is_current(args, hass, data, access_key, permitted_entities):
        return _tool_error("Command targets or configuration changed."), "denied", resource

    if not targets_current(args, permitted_entities, hass):
        return _tool_error(TARGETS_CHANGED), "denied", resource

    if domain in HIGH_RISK_DOMAINS:
        _LOGGER.info(
            "High-risk service call %s/%s by access key %s",
            domain, service, access_key.name,
        )

    call_data = dict(service_data)
    call_data["entity_id"] = permitted_entities

    use_return_response = False
    if effective_cap(access_key, "cap_service_response") != CAP_DENY:
        try:
            from homeassistant.core import SupportsResponse as _SR
            handler = hass.services.async_services().get(domain, {}).get(service)
            use_return_response = (
                handler is not None and
                getattr(handler, "supports_response", None) not in (None, _SR.NONE)
            )
        except Exception:
            _LOGGER.debug(
                "supports_response probe failed for %s/%s; calling without return_response",
                domain, service, exc_info=True,
            )

    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            service_context = household_service_kwargs(hass, domain, service)
            mark_dispatch_started()
            svc_response = await hass.services.async_call(
                domain,
                service,
                call_data,
                blocking=True,
                return_response=use_return_response,
                **service_context,
            )
    except asyncio.TimeoutError:
        return (
            _tool_success(json.dumps({
                "success": True,
                "partial": True,
                "message": "Service dispatched but HA did not respond within the timeout window.",
            })),
            "allowed",
            resource,
        )
    except ServiceNotFound:
        # For most domains, return a generic error: never confirm or deny
        # service existence (no enumeration oracle). But for a curated set of
        # core actuator domains (DOMAIN_SERVICE_HINTS), name the valid core service
        # verbs so an agent that guessed the wrong one (e.g. valve.open instead of
        # valve.open_valve) can self-correct. This is leak-safe: the catch is
        # post-authorization (the access key already proved WRITE on an entity in this
        # domain) and the verbs come from a hardcoded public HA-core list, not a
        # live hass.services lookup, so nothing hidden is revealed.
        hint = _service_not_found_hint(domain, service)
        if hint is not None:
            return _tool_error(hint[0]), "invalid_request", resource
        return _tool_error("Forbidden."), "denied", resource
    except ServiceValidationError as err:
        # The access key already holds WRITE on this entity and invoked a real service;
        # a validation error is about the caller's own argument (e.g. an
        # out-of-range setpoint), not about hidden entities or services, so it is
        # safe to surface and lets the agent self-correct instead of reading it as
        # a permission denial. (ServiceNotFound subclasses ServiceValidationError
        # but is caught above and stays generic, preserving the no-oracle rule.)
        return _tool_error(_validation_error_message(err)), "invalid_request", resource
    except vol.Invalid as err:
        # hass.services.async_call validates service_data against the target
        # service's own schema with plain voluptuous, and re-raises vol.Invalid/
        # MultipleInvalid unwrapped rather than as a HomeAssistantError (e.g. a
        # service whose schema does not accept entity_id at all, called with a
        # resolved entity target still attached). The ServiceValidationError/
        # HomeAssistantError handlers above do not catch it, since vol.Invalid
        # subclasses neither; unhandled it would reach the dispatch-level safety
        # net as a bare "Internal error" and lose the actionable detail
        # voluptuous provides. Safe to surface for the same reason as
        # ServiceValidationError: it describes the caller's own argument shape,
        # not hidden state.
        return _tool_error(_validation_error_message(err)), "invalid_request", resource
    except HomeAssistantError:
        return _tool_error("Forbidden."), "denied", resource

    body: dict[str, Any] = {"success": True}
    if local_plan:
        from .command_verification import async_verify_command_state

        body["state_verification"] = await async_verify_command_state(
            hass, data, access_key, args[COMMAND_ROUTING_PLAN_KEY],
        )
        check_request_authority()
        access_key = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash).current_access_key()
    filtered_response = filter_service_response(svc_response, access_key, hass) if svc_response is not None else None
    if filtered_response is not None:
        body["service_response"] = filtered_response
    try:
        body["targets"] = _service_call_targets(permitted_entities, access_key, hass)
    except Exception:  # noqa: BLE001 - the effect already happened; the report is optional
        _LOGGER.debug("Could not describe call_service targets", exc_info=True)
    if mesa_outcome.warnings:
        body["mesa_advisory"] = mesa_outcome.warnings
        _mesa_advisory_ctx.set(True)

    return _tool_success(json.dumps(body, default=str)), "allowed", resource


def _service_call_targets(entity_ids: list[str], access_key: AccessKeyRecord, hass: HomeAssistant) -> dict[str, Any]:
    """Name what the call acted on and how each target reads right after it.

    These are the entities the access key was already permitted to write, so
    nothing hidden is revealed. Home Assistant skips an unavailable target
    without failing the call, which is why those are listed separately.
    """
    states: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for entity_id in entity_ids:
        if resolve(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
            continue
        state = hass.states.get(entity_id)
        value = state.state if state is not None else None
        if value in (None, "unavailable"):
            unavailable.append(entity_id)
        if len(states) < SERVICE_RESULT_STATES_MAX:
            states.append({"entity_id": entity_id, "state": value})
    result: dict[str, Any] = {"count": len(entity_ids), "states_after_call": states}
    if len(entity_ids) > len(states):
        result["states_omitted"] = len(entity_ids) - len(states)
    if unavailable:
        result["unavailable"] = unavailable[:SERVICE_RESULT_STATES_MAX]
    return result


async def _execute_call_service_mesa_approved(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Re-run a MESA-gated service call after admin approval.

    Registered under MESA_APPROVED_EXECUTOR but never dispatchable from the tool
    router, so an access key cannot reach the confirm-approved path itself. Re-runs
    Phoenix MCP scope resolution and MESA evaluation; only control_mode:confirm blocks
    are treated as satisfied, so an entity that became prohibited or read_only
    since the request is still rejected.
    """
    return await _execute_call_service(args, access_key, hass, data, mesa_approved=True)


async def _tool_get_config(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: return HA config (requires cap_config_read)."""
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_config"
    from .config_recovery import runtime_identity
    body = {**build_safe_config(hass), "runtime_identity": runtime_identity(hass)}
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_config"




_LOG_INTEGRATION_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_LOG_LOGGER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


async def _tool_log_query(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    tool_name: str,
    phoenix_only: bool,
) -> tuple[dict, str, str]:
    """Run one bounded, source-truthful query over HA's system-log ring."""
    check_request_authority()
    authority = request_authority.get()
    if authority is not None:
        access_key = authority.current_access_key()
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or (
        phoenix_only and effective_cap(access_key, "cap_diagnostics") == CAP_DENY
    ):
        return _tool_error("Forbidden."), "denied", tool_name

    detail_ref = args.get("detail_ref")
    detail_field = args.get("detail_field", "message")
    message_index = args.get("message_index", 0)
    detail_offset = args.get("detail_offset", 0)
    if "detail_ref" in args:
        if not isinstance(detail_ref, str) or re.fullmatch(r"[0-9a-f]{64}", detail_ref) is None:
            return _tool_error("Invalid log detail reference."), "invalid_request", tool_name
        if any(key in args for key in ("level", "integration", "logger", "search", "since", "until", "limit", "cursor")):
            return _tool_error("Log detail retrieval cannot be combined with query filters or pagination."), "invalid_request", tool_name
        if not isinstance(detail_field, str) or detail_field not in ("message", "exception"):
            return _tool_error("detail_field must be message or exception."), "invalid_request", tool_name
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (message_index, detail_offset)):
            return _tool_error("message_index and detail_offset must be nonnegative integers."), "invalid_request", tool_name
        if detail_field == "exception" and "message_index" in args:
            return _tool_error("message_index is supported only for message details."), "invalid_request", tool_name
    elif any(key in args for key in ("detail_field", "message_index", "detail_offset")):
        return _tool_error("Detail parameters require detail_ref."), "invalid_request", tool_name

    # A wrong-SHAPED level degrades to absent (str_arg), but a wrong VALUE is
    # refused rather than coerced: silently answering a WARNING query for a
    # caller that asked for INFO would hide the dropped level. Safe to surface
    # because the cap check above already ran, so a denied access key never reaches
    # a message about its own argument.
    # A non-string filter would otherwise read as no filter and widen the page.
    malformed = [key for key in ("integration", "logger", "search")
                 if args.get(key) is not None and not isinstance(args[key], str)]
    if malformed:
        return _tool_error(f"{', '.join(malformed)} must be a string."), "invalid_request", tool_name
    raw_level = str_arg(args.get("level"), "WARNING").strip().upper() or "WARNING"
    if raw_level not in LOG_LEVELS:
        return _tool_error(LOG_LEVEL_ERROR_MESSAGE), "invalid_request", tool_name

    integration = str_arg(args.get("integration")).strip() or None
    logger = str_arg(args.get("logger")).strip() or None
    search = str_arg(args.get("search")).strip() or None
    cursor = str_arg(args.get("cursor")).strip() or None
    if phoenix_only:
        integration = None
    if integration and (
        len(integration) > MAX_SEARCH_QUERY_LEN
        or _LOG_INTEGRATION_RE.fullmatch(integration) is None
    ):
        return _tool_error("Invalid integration domain."), "invalid_request", tool_name
    if logger and (
        len(logger) > MAX_SEARCH_QUERY_LEN
        or _LOG_LOGGER_RE.fullmatch(logger) is None
    ):
        return _tool_error("Invalid logger prefix."), "invalid_request", tool_name
    if search and len(search) > MAX_SEARCH_QUERY_LEN:
        return _tool_error(
            f"search must be at most {MAX_SEARCH_QUERY_LEN} characters."
        ), "invalid_request", tool_name
    if cursor and len(cursor) > 4096:
        return _tool_error("Invalid cursor for these log filters."), "invalid_request", tool_name

    since = None
    until = None
    raw_since = str_arg(args.get("since")).strip()
    raw_until = str_arg(args.get("until")).strip()
    try:
        if raw_since:
            since = _parse_time_param(raw_since)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        if raw_until:
            until = _parse_time_param(raw_until)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        return _tool_error("Invalid since or until time format."), "invalid_request", tool_name
    if since is not None and until is not None and since >= until:
        return _tool_error("since must be earlier than until."), "invalid_request", tool_name

    # Default is the same as Home Assistant's stock system-log ring size.
    limit = 50
    raw_limit = args.get("limit")
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
            if not (1 <= limit <= MAX_LOG_ENTRIES):
                limit = max(1, min(limit, MAX_LOG_ENTRIES))
        except (TypeError, ValueError):
            limit = 50

    try:
        page = _collect_log_entries(
            hass,
            raw_level,
            integration,
            logger,
            search,
            since,
            until,
            raw_since or None,
            raw_until or None,
            limit,
            cursor,
            phoenix_only=phoenix_only,
            access_key=access_key,
            detail_ref=detail_ref,
            detail_field=detail_field,
            message_index=message_index,
            detail_offset=detail_offset,
        )
    except (SystemLogUnavailableError, SystemLogDegradedError) as exc:
        body = {
            "source": {
                "status": exc.status,
                "kind": "home_assistant_system_log",
                "semantics": "deduplicated_buckets",
                "pagination": "best_effort_live_ring",
                "reason": str(exc),
            },
            "filters": {
                "level": raw_level,
                "integration": integration,
                "logger": logger,
                "search": search,
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "time_basis": "latest_occurrence",
            },
            "count": 0,
            "matched_buckets": 0,
            "has_more": False,
            "next_cursor": None,
            "entries": [],
        }
        return _tool_error(json.dumps(body)), "invalid_request", tool_name
    except ValueError as exc:
        return _tool_error(str(exc)), "invalid_request", tool_name
    check_request_authority()
    if authority is not None:
        access_key = authority.current_access_key()
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or (
        phoenix_only and effective_cap(access_key, "cap_diagnostics") == CAP_DENY
    ):
        return _tool_error("Forbidden."), "denied", tool_name
    return _tool_success(json.dumps(page.as_dict(), default=str)), "allowed", tool_name


async def _tool_get_logs(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read the system ring or protected fault-dump blocks."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_logs"
    if any(key in args for key in _LOG_DETAIL_KEYS):
        return _tool_error("Fetch a bucket's full text with get_log_detail and its detail_ref."), "invalid_request", "get_logs"
    source = args.get("source", "system_log")
    if source == "fault_log":
        from .fault_log import async_fault_log_query
        return await async_fault_log_query(args, access_key, hass)
    if source == "integration_debug":
        return _integration_debug_query(args, access_key, hass)
    if source != "system_log":
        return _tool_error("source must be system_log, fault_log or integration_debug."), "invalid_request", "get_logs"
    if "order" in args:
        return _tool_error("order is supported only for fault_log."), "invalid_request", "get_logs"
    return await _tool_log_query(
        args, access_key, hass, tool_name="get_logs", phoenix_only=False
    )


_LOG_DETAIL_KEYS = ("detail_ref", "detail_field", "message_index", "detail_offset")


def _integration_debug_query(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[dict, str, str]:
    """Read captured INFO/DEBUG records of the integrations this access key can see."""
    from .debug_capture import DEBUG_CAPTURE_RUNTIME_KEY, integration_domain  # noqa: PLC0415

    tool = "get_logs"
    if effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", tool
    unsupported = [key for key in ("level", "logger", "since", "until", "order") if key in args]
    if unsupported:
        return _tool_error("integration_debug accepts integration, search, limit and cursor only."), "invalid_request", tool
    malformed = [key for key in ("integration", "search", "cursor") if args.get(key) is not None and not isinstance(args[key], str)]
    if malformed:
        return _tool_error(f"{', '.join(malformed)} must be a string."), "invalid_request", tool
    limit = args.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LOG_ENTRIES:
        return _tool_error(f"limit must be an integer from 1 to {MAX_LOG_ENTRIES}."), "invalid_request", tool
    cursor = (args.get("cursor") or "").strip()
    if cursor and not cursor.isdigit():
        return _tool_error("Invalid cursor for integration_debug."), "invalid_request", tool
    search = (args.get("search") or "").strip()
    domains = _visible_logger_domains(access_key, hass)
    if integration := (args.get("integration") or "").strip():
        domains = domains & {integration}
    capture = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    source: dict[str, Any] = {"kind": "integration_debug_capture",
                              "levels": ["INFO", "DEBUG"], "capacity": DEBUG_CAPTURE_CAPACITY}
    if capture is None:
        return _tool_success(json.dumps({"source": {**source, "status": "unavailable"}, "count": 0,
                                         "has_more": False, "next_cursor": None, "entries": []})), "allowed", tool
    entries: list[dict[str, Any]] = []
    has_more = False
    for sequence, created, level, logger, message, truncated in capture.newest(domains, int(cursor) if cursor else None):
        safe = _strict_phoenix_log_text(message, access_key, hass)
        # Search sees only scrubbed text, so it can never probe a redacted value.
        if search and search.casefold() not in safe.casefold():
            continue
        if len(entries) == limit:
            has_more = True
            break
        entries.append({"sequence": sequence, "time": datetime.fromtimestamp(created, timezone.utc).isoformat(),
                        "level": level, "integration": integration_domain(logger), "logger": logger,
                        "message": safe, "truncated": truncated})
    body = {
        "source": {**source, "status": "available", "retained": len(capture.records),
                   "capturing_since": capture.attached_at.isoformat(),
                   "note": "Only records below WARNING from integrations whose level was raised appear here; "
                           "WARNING and above are in source system_log."},
        "count": len(entries), "has_more": has_more,
        "next_cursor": str(entries[-1]["sequence"]) if has_more and entries else None,
        "entries": entries,
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", tool


async def _tool_get_log_detail(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: one bounded scrubbed text chunk of a log bucket by its detail_ref."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_log_detail"
    if "detail_ref" not in args:
        return _tool_error("Missing required argument: detail_ref"), "invalid_request", "get_log_detail"
    detail_args = {key: args[key] for key in _LOG_DETAIL_KEYS if key in args}
    # A reference is bound to its namespace, so it can only match where it was
    # minted; Phoenix's own diagnostics are tried only with Diagnostics allowed.
    namespaces = [False] + ([True] if effective_cap(access_key, "cap_diagnostics") != CAP_DENY else [])
    result: tuple[dict, str, str] = _tool_error(LOG_DETAIL_STALE_MESSAGE), "invalid_request", "get_log_detail"
    for phoenix_only in namespaces:
        result = await _tool_log_query(detail_args, access_key, hass, tool_name="get_log_detail", phoenix_only=phoenix_only)
        if result[1] != "invalid_request" or result[0]["content"][0]["text"] != LOG_DETAIL_STALE_MESSAGE:
            return result
    return result


async def _tool_get_phoenix_diagnostics(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read aggressively scrubbed Phoenix warning/error diagnostics."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_phoenix_diagnostics"
    if any(key in args for key in _LOG_DETAIL_KEYS):
        return _tool_error("Fetch a bucket's full text with get_log_detail and its detail_ref."), "invalid_request", "get_phoenix_diagnostics"
    return await _tool_log_query(
        args,
        access_key,
        hass,
        tool_name="get_phoenix_diagnostics",
        phoenix_only=True,
    )


def _logbook_string_list(args: dict, key: str) -> tuple[list[str], str | None]:
    """Validate, trim and deduplicate one id list; returns (values, error or None)."""
    raw = args.get(key)
    if raw is None:
        return [], None
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        return [], f"{key} must be an array of non-empty strings."
    unique = list(dict.fromkeys(item.strip() for item in raw))
    if len(unique) > MAX_LOGBOOK_RESOURCE_IDS:
        return [], f"{key} accepts at most {MAX_LOGBOOK_RESOURCE_IDS} unique values."
    return unique, None


def _logbook_time(value: Any, default: datetime) -> datetime:
    """Parse one logbook bound and normalize timezone-less ISO input to UTC."""
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        raise ValueError
    parsed = _parse_time_param(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _logbook_entry_visible(
    entry: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    entity_permissions: dict[str, Permission],
    device_permissions: dict[str, Permission],
) -> bool:
    """Enforce entity-first visibility, with device access for device-only rows."""
    if "entity_id" in entry:
        entity_id = entry.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            return False
        permission = entity_permissions.get(entity_id)
        if permission is None:
            permission = entity_permissions[entity_id] = resolve_read(entity_id, access_key, hass)
        return permission in (Permission.READ, Permission.WRITE)

    device_id = entry.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        return False
    permission = device_permissions.get(device_id)
    if permission is None:
        permission = device_permissions[device_id] = resolve_device_registry_access(
            device_id, access_key, hass
        )
    return permission in (Permission.READ, Permission.WRITE)


async def _tool_get_logbook(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read the human-readable logbook (requires cap_log_read)."""
    args, error = normalize_tool_args("get_logbook", args)
    if error:
        return _tool_error(error), "invalid_request", "get_logbook"
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_logbook"

    entity_ids, entity_error = _logbook_string_list(args, "entity_ids")
    device_ids, device_error = _logbook_string_list(args, "device_ids")
    if entity_error or device_error:
        return (
            _tool_error(entity_error or device_error or "Invalid filters."),
            "invalid_request",
            "get_logbook",
        )
    if ("entity_ids" in args or "device_ids" in args) and not (entity_ids or device_ids):
        # An empty resource selector is a mistake, not a request for the whole home.
        return _tool_error("A resources target needs at least one entity_ids or device_ids value."), "invalid_request", "get_logbook"

    raw_context = args.get("context_id")
    context_id = None
    if raw_context is not None:
        if (
            not isinstance(raw_context, str)
            or not (context_id := raw_context.strip())
            or ulid_to_bytes_or_none(context_id) is None
        ):
            return _tool_error("Invalid context_id."), "invalid_request", "get_logbook"
    if context_id and (entity_ids or device_ids):
        return _tool_error(
            "context_id cannot be combined with entity_ids or device_ids."
        ), "invalid_request", "get_logbook"
    if any(not valid_entity_id(entity_id) for entity_id in entity_ids):
        return _tool_error("Requested resource not found."), "not_found", "get_logbook"

    raw_search = args.get("search")
    if raw_search is not None and not isinstance(raw_search, str):
        return _tool_error("search must be a string."), "invalid_request", "get_logbook"
    search = raw_search.strip() if raw_search is not None else None
    search = search or None
    if search and len(search) > MAX_SEARCH_QUERY_LEN:
        return _tool_error(
            f"search must be at most {MAX_SEARCH_QUERY_LEN} characters."
        ), "invalid_request", "get_logbook"

    raw_limit = args.get("limit")
    if raw_limit is None:
        limit = 100
    elif type(raw_limit) is not int or not 1 <= raw_limit <= 1000:
        return _tool_error(
            "limit must be an integer from 1 through 1000."
        ), "invalid_request", "get_logbook"
    else:
        limit = raw_limit

    now = utcnow()
    try:
        start_time = _logbook_time(args.get("start_time"), now - timedelta(hours=24))
        end_time = _logbook_time(args.get("end_time"), now)
    except ValueError:
        return _tool_error(
            "Invalid start_time or end_time format."
        ), "invalid_request", "get_logbook"
    if start_time >= end_time:
        return _tool_error(
            "start_time must be earlier than end_time."
        ), "invalid_request", "get_logbook"
    narrowed = bool(entity_ids or device_ids or context_id)
    max_days = MAX_LOGBOOK_NARROWED_DAYS if narrowed else MAX_LOGBOOK_HOME_DAYS
    if end_time - start_time > timedelta(days=max_days):
        return _tool_error(
            f"This logbook query supports at most {max_days} days."
        ), "invalid_request", "get_logbook"

    requested_permissions: list[Permission] = [
        resolve_read(entity_id, access_key, hass) for entity_id in entity_ids
    ]
    requested_permissions.extend(
        resolve_device_registry_access(device_id, access_key, hass)
        for device_id in device_ids
    )
    if any(
        permission not in (Permission.READ, Permission.WRITE)
        for permission in requested_permissions
    ):
        return (
            _tool_error("Requested resource not found."),
            "not_found",
            "get_logbook",
        )

    payload: dict[str, Any] = {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }
    if entity_ids:
        payload["entity_ids"] = entity_ids
    if device_ids:
        payload["device_ids"] = device_ids
    if context_id:
        payload["context_id"] = context_id

    try:
        result = await async_ws_command(hass, "logbook/get_events", payload)
    except WsDispatchError as exc:
        return _tool_error(f"Failed to read logbook: {exc}"), "invalid_request", "get_logbook"

    if not isinstance(result, list):
        # An unexpected shape must not pass as an empty logbook, which reads as
        # "nothing happened" rather than "the read did not work".
        return _tool_error(
            "Unexpected logbook response from Home Assistant."
        ), "invalid_request", "get_logbook"
    entity_permissions = dict(zip(entity_ids, requested_permissions[:len(entity_ids)]))
    device_permissions = dict(zip(device_ids, requested_permissions[len(entity_ids):]))
    scoped = [
        entry
        for entry in result
        if isinstance(entry, dict)
        and _logbook_entry_visible(
            entry, access_key, hass, entity_permissions, device_permissions
        )
    ]
    scoped = filter_service_response(scoped, access_key, hass)
    if not isinstance(scoped, list) or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("when"), (int, float))
        or isinstance(entry.get("when"), bool)
        or not math.isfinite(float(entry["when"]))
        for entry in scoped
    ):
        return _tool_error(
            "Unexpected logbook entry shape from Home Assistant."
        ), "invalid_request", "get_logbook"

    if search:
        needle = search.casefold()
        scoped = [
            entry
            for entry in scoped
            if any(
                isinstance(entry.get(field), str)
                and needle in entry[field].casefold()
                for field in ("name", "message", "state")
            )
        ]
    scoped.sort(key=lambda entry: float(entry["when"]))
    total = len(scoped)
    scoped = scoped[-limit:]
    body = {
        "count": len(scoped),
        "total": total,
        "truncated": total > len(scoped),
        "filters": {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "entity_ids": entity_ids,
            "device_ids": device_ids,
            "context_id": context_id,
            "search": search,
            "limit": limit,
        },
        "entries": scoped,
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_logbook"


_LOGGER_CONTROL_FINGERPRINT = "_phoenix_logger_control_fingerprint"
_INTEGRATION_DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")


def _visible_logger_domains(access_key: AccessKeyRecord, hass: HomeAssistant) -> set[str]:
    """Integration domains visible through one config entry or registry entity."""
    domains = {
        entry.domain
        for entry in hass.config_entries.async_entries()
        if entry.domain != DOMAIN
        and resolve_config_entry_registry_access(entry.entry_id, access_key, hass)
        in (Permission.READ, Permission.WRITE)
    }
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        platform = getattr(entry, "platform", None)
        if (
            isinstance(platform, str)
            and platform != DOMAIN
            and resolve_registry_access(entry.entity_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
        ):
            domains.add(platform)
    return domains


def _override_dict(value: IntegrationOverride | None) -> dict[str, str] | None:
    return value.to_dict() if value is not None else None


def _logger_context_fingerprint(
    domain: str,
    loggers: set[str],
    override: IntegrationOverride | None,
    visible: bool,
) -> str:
    payload = json.dumps(
        {
            "integration": domain,
            "loggers": sorted(loggers),
            "override": _override_dict(override),
            "visible": visible,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _aggregate_log_level(levels: dict[str, str]) -> str:
    unique = set(levels.values())
    return next(iter(unique)) if len(unique) == 1 else "MIXED"


async def _tool_list_integration_log_levels(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """List scoped integration-aware logger levels."""
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_integration_log_levels"
    manager = data.logger_control
    if manager is None:
        return _tool_success(json.dumps({
            "source": {"status": "unavailable", "warning": "Logger state is unavailable."},
            "count": 0,
            "integrations": [],
            "read_at": utcnow().isoformat(),
        })), "allowed", "list_integration_log_levels"
    source = manager.adapter.source_status()
    source["timed_restoration"] = (
        "available" if manager.storage_available else "unavailable"
    )
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for domain in sorted(_visible_logger_domains(access_key, hass)):
        try:
            loggers = await manager.adapter.declared_loggers(domain)
            levels = manager.adapter.effective_levels(loggers)
            try:
                override = manager.adapter.get_override(domain)
                override_status = "available"
            except LoggerControlUnavailable as exc:
                override = None
                override_status = "unavailable"
                warnings.append(f"{domain}: {exc}")
            timed = manager.active(domain)
            rows.append({
                "integration": domain,
                "effective_level": _aggregate_log_level(levels),
                "logger_levels": levels,
                "override": {
                    "status": override_status,
                    "setting": _override_dict(override),
                },
                "timed_override": (
                    {
                        "level": timed.applied.level,
                        "expires_at": timed.expires_at.isoformat(),
                    }
                    if timed is not None else None
                ),
            })
        except LoggerControlUnavailable as exc:
            warnings.append(f"{domain}: {exc}")
    if warnings and source.get("status") == "available":
        source = {"status": "degraded", "warnings": warnings}
    elif warnings:
        source = {**source, "warnings": warnings}
    return _tool_success(json.dumps({
        "source": source,
        "count": len(rows),
        "integrations": rows,
        "read_at": utcnow().isoformat(),
    }, default=str)), "allowed", "list_integration_log_levels"


def _log_control_args(
    args: dict[str, Any],
) -> tuple[str, str, str, int | None] | str:
    domain = args.get("integration")
    level = args.get("level")
    persistence = args.get("persistence")
    duration = args.get("duration_minutes")
    if not isinstance(domain, str) or not _INTEGRATION_DOMAIN_RE.fullmatch(domain):
        return "integration must be a valid integration domain."
    if not isinstance(level, str) or level not in LOGGER_CONTROL_LEVELS:
        return "level must be NOTSET, DEBUG, INFO, WARNING, ERROR, or CRITICAL."
    if not isinstance(persistence, str) or persistence not in LOGGER_CONTROL_PERSISTENCE:
        return "persistence must be none, once, or permanent."
    if duration is not None and (
        isinstance(duration, bool) or not isinstance(duration, int) or not 5 <= duration <= 120
    ):
        return "duration_minutes must be an integer from 5 through 120."
    if duration is not None and (persistence != "none" or level == "NOTSET"):
        return "duration_minutes requires persistence=none and a level other than NOTSET."
    if level == "NOTSET" and persistence != "none":
        return "NOTSET requires persistence=none."
    return domain, level, persistence, duration


def _log_control_warnings(level: str) -> list[str]:
    if level not in ("DEBUG", "INFO"):
        return []
    return [
        "Verbose logging can increase disk use.",
        "Third-party integration logs may contain sensitive output outside Phoenix's control.",
    ]


def _build_diff_set_integration_log_level(
    domain: str,
    before: IntegrationOverride | None,
    after: IntegrationOverride | None,
    loggers: set[str],
    duration: int | None,
) -> dict[str, Any]:
    warnings = _log_control_warnings(after.level if after else "NOTSET")
    return {
        "kind": "system_action",
        **_summary("integration.log_level", label=domain),
        "target": {"type": "integration", "id": domain, "label": domain},
        "preview": {
            "before": _override_dict(before),
            "after": _override_dict(after),
            "affected_loggers": sorted(loggers),
            "duration_minutes": duration,
            "persistence": (
                "runtime-only" if after and after.persistence == "none"
                else "one-restart" if after and after.persistence == "once"
                else "permanent" if after else "cleared"
            ),
            "warnings": warnings,
        },
    }


async def _validated_log_control_context(
    args: dict[str, Any], access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[str, str, str, int | None, set[str], IntegrationOverride | None, str] | tuple[dict, str, str]:
    parsed = _log_control_args(args)
    if isinstance(parsed, str):
        return _tool_error(parsed), "invalid_request", "set_integration_log_level"
    domain, level, persistence, duration = parsed
    visible = domain in _visible_logger_domains(access_key, hass)
    if not visible or domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", domain
    try:
        await loader.async_get_integration(hass, domain)
    except IntegrationNotFound:
        return _tool_error("Integration not found."), "not_found", domain
    manager = data.logger_control
    if manager is None:
        return _tool_error("Logger state is unavailable."), "invalid_request", domain
    try:
        loggers = await manager.adapter.declared_loggers(domain)
        before = manager.adapter.get_override(domain)
    except LoggerControlUnavailable:
        return _tool_error("Home Assistant logger state is unavailable or incompatible."), "invalid_request", domain
    fingerprint = _logger_context_fingerprint(domain, loggers, before, visible)
    return domain, level, persistence, duration, loggers, before, fingerprint


async def _tool_set_integration_log_level(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Gate a scoped process-wide integration logger change without MESA."""
    # Capability ordering is intentional: argument errors must not become an
    # oracle for access keys lacking either explicit capability.
    if effective_cap(access_key, "cap_log_read") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_log_level"
    if effective_cap(access_key, "cap_log_control") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_log_level"
    context = await _validated_log_control_context(args, access_key, hass, data)
    if len(context) == 3 and isinstance(context[0], dict):
        return context
    domain, level, persistence, duration, loggers, before, fingerprint = context
    after = None if level == "NOTSET" else IntegrationOverride(level, persistence)
    manager = data.logger_control
    assert manager is not None
    if duration is None and before == after and manager.active(domain) is None:
        return _tool_success(json.dumps({
            "integration": domain, "changed": False, "setting": _override_dict(before)
        })), "allowed", f"integration:{domain}"
    execute_args = dict(args)
    execute_args[_LOGGER_CONTROL_FINGERPRINT] = fingerprint
    blocked = await _gate(
        "cap_log_control", access_key, hass, data,
        tool_name="set_integration_log_level",
        args=execute_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda: _build_diff_set_integration_log_level(
            domain, before, after, loggers, duration
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_integration_log_level(execute_args, access_key, hass, data)


async def _execute_set_integration_log_level(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_log_read") == CAP_DENY or effective_cap(
        access_key, "cap_log_control"
    ) == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_log_level"
    context = await _validated_log_control_context(args, access_key, hass, data)
    if len(context) == 3 and isinstance(context[0], dict):
        return context
    domain, level, persistence, duration, loggers, before, fingerprint = context
    if args.get(_LOGGER_CONTROL_FINGERPRINT) != fingerprint:
        return _tool_error(
            "The integration logger set, override, or visibility changed after approval. Review it again."
        ), "denied", f"integration:{domain}"
    after = None if level == "NOTSET" else IntegrationOverride(level, persistence)
    manager = data.logger_control
    assert manager is not None
    if duration is None and before == after and manager.active(domain) is None:
        return _tool_success(json.dumps({
            "integration": domain, "changed": False, "setting": _override_dict(before)
        })), "allowed", f"integration:{domain}"
    try:
        await manager.async_set(
            domain=domain,
            desired=after,
            prior=before,
            loggers=loggers,
            owner_access_key_id=access_key.id,
            duration_minutes=duration,
        )
        effective = manager.adapter.effective_levels(loggers)
        current = manager.adapter.get_override(domain)
    except (LoggerControlUnavailable, OSError):
        _LOGGER.exception("set_integration_log_level failed for %s", domain)
        return _tool_error("Failed to update integration log level."), "invalid_request", f"integration:{domain}"
    timed = manager.active(domain)
    return _tool_success(json.dumps({
        "integration": domain,
        "changed": True,
        "before": _override_dict(before),
        "after": _override_dict(current),
        "affected_loggers": sorted(loggers),
        "effective_level": _aggregate_log_level(effective),
        "logger_levels": effective,
        "persistence_semantics": (
            "runtime-only" if current and current.persistence == "none"
            else "one-restart" if current and current.persistence == "once"
            else "permanent" if current else "cleared"
        ),
        "timed_override": (
            {"expires_at": timed.expires_at.isoformat()} if timed else None
        ),
        "warnings": _log_control_warnings(level),
    }, default=str)), "allowed", f"integration:{domain}"


def _entity_meta_snapshot(entry: Any) -> dict:
    """The registry metadata fields set_entity can change (for version capture).

    `aliases` is stored in Home Assistant's OWN wire form, an ordered list whose
    `None` member is the COMPUTED_NAME sentinel (see `_alias_wire`). Raw rather
    than resolved, because this payload is what a restore replays and the
    sentinel has to survive it: resolving it to the entity's current name would
    freeze that name, so a later rename would silently stop matching by voice.
    """
    return {
        "entity_id": entry.entity_id,
        "name": entry.name,
        "icon": entry.icon,
        "area_id": entry.area_id,
        "device_class": entry.device_class,
        "disabled_by": getattr(entry.disabled_by, "value", entry.disabled_by),
        "hidden_by": getattr(entry.hidden_by, "value", entry.hidden_by),
        "labels": sorted(entry.labels),
        "categories": dict(sorted(entry.categories.items())),
        "aliases": _alias_wire(entry.aliases),
    }


def _alias_wire(aliases: Any) -> list[str | None]:
    """An entity's alias list as JSON, mirroring HA's own `aliases_v2` encoding.

    HA serializes the COMPUTED_NAME sentinel as null and Phoenix does the same,
    so a version snapshot round-trips exactly and nothing has to invent a
    representation HA would not recognise.
    """
    return [None if alias is er.COMPUTED_NAME else alias for alias in (aliases or ())]


def _alias_unwire(aliases: Any) -> list:
    """The inverse of _alias_wire, for the restore path."""
    return [er.COMPUTED_NAME if alias is None else alias for alias in (aliases or ())]


def _alias_edit(value: Any, field: str) -> list[str] | str:
    """Normalize an add_aliases / remove_aliases argument, or refuse it.

    Wrong shapes are REFUSED rather than degraded to absent. These arguments
    change what Home Assistant will answer to by voice, so a value that quietly
    coerced to an empty list would report a change nobody made; and a list with
    a non-string member is refused rather than having that member dropped,
    because a dropped member is an alias the caller meant to act on.
    """
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        bad = next((v for v in value if not isinstance(v, str)), None)
        if bad is not None:
            return f"{field} must be a list of strings; {bad!r} is not one."
        candidates = list(value)
    else:
        return f"{field} must be a string or a list of strings."
    cleaned: list[str] = []
    for candidate in candidates:
        alias = candidate.strip()
        if not alias:
            continue
        if len(alias) > MAX_ENTITY_ALIAS_LENGTH:
            return f"alias {alias[:40]!r}... is longer than {MAX_ENTITY_ALIAS_LENGTH} characters."
        if not any(alias.casefold() == seen.casefold() for seen in cleaned):
            cleaned.append(alias)
    return cleaned


def _apply_alias_edits(
    current: list, add: list[str], remove: list[str]
) -> tuple[list, list[str], list[str]] | str:
    """The new alias list, plus what actually changed, or a refusal message.

    ADD APPENDS and REMOVE FILTERS STRINGS, which is the whole reason this tool
    has no absolute set-the-aliases form. Home Assistant seeds every entity with
    a COMPUTED_NAME sentinel meaning "match my own current name", and builds the
    voice matching trie from this list alone, so an absolute write that dropped
    the sentinel would stop the entity answering to its own name, and an empty
    list would remove it from voice control entirely. Neither is reachable here:
    the sentinel is not a string, so nothing a caller names can match it, and
    order is preserved because Home Assistant preserves it deliberately.

    Matching is case-insensitive on both sides, since that is how the value is
    ultimately used: hassil matches spoken text without regard to case, so two
    aliases differing only in case are the same alias.
    """
    folded_remove = {alias.casefold() for alias in remove}
    kept = [
        alias for alias in current
        if not (isinstance(alias, str) and alias.casefold() in folded_remove)
    ]
    removed = [
        alias for alias in current
        if isinstance(alias, str) and alias.casefold() in folded_remove
    ]
    present = {alias.casefold() for alias in kept if isinstance(alias, str)}
    added: list[str] = []
    for alias in add:
        if alias.casefold() in present:
            continue
        present.add(alias.casefold())
        added.append(alias)
    result = [*kept, *added]
    if not result:
        return (
            "That would leave the entity with no names at all, so Home Assistant "
            "would stop responding to it by voice. Keep at least one alias, or "
            "delete the entity if that is what you meant."
        )
    strings = sum(1 for alias in result if isinstance(alias, str))
    if strings > MAX_ENTITY_ALIASES:
        return f"an entity may have at most {MAX_ENTITY_ALIASES} aliases; this would leave {strings}."
    return result, added, removed


def _registry_write_perm_error(perm: Any, entity_id: str) -> tuple[dict, str, str] | None:
    """Shared scope check for registry writes; None means WRITE-accessible.

    READ-only access returns a specific forbidden (the access key can already see the
    entity, so no existence oracle); anything else returns the not_found body.
    """
    if perm == Permission.WRITE:
        return None
    if perm == Permission.READ:
        return _tool_error("Read-only access to this entity; registry edits need write access."), "denied", entity_id
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    return _tool_error("Entity not found."), "denied", entity_id


def _registry_area_id_error(
    args: dict, hass: HomeAssistant, entity_id: str
) -> tuple[dict, str, str] | None:
    """Validate set_entity's optional area reference; None means valid."""
    if "area_id" not in args:
        return None
    area_id = args["area_id"]
    if area_id is not None and not isinstance(area_id, str):
        return _tool_error("area_id must be a string or null."), "invalid_request", entity_id
    if area_id and ar.async_get(hass).async_get_area(area_id) is None:
        return _tool_error("Unknown area_id."), "invalid_request", entity_id
    return None


_SET_ENTITY_UPDATE_ARGUMENTS = frozenset(
    {
        "name", "use_device_name", "icon", "area_id", "device_class", "enabled",
        "hidden", "add_aliases", "remove_aliases", "add_labels", "remove_labels",
        "categories", "new_entity_id",
    }
)

_SET_ENTITY_RESTORE_ARGUMENTS = frozenset(
    {"aliases", "labels", "disabled_by", "hidden_by"}
)


def _registry_id_edit(value: Any, field: str) -> list[str] | str:
    """Normalize a label-id add/remove argument, or return a refusal message."""
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        return f"{field} must be a string or a list of strings."
    cleaned: list[str] = []
    for value_item in values:
        item = value_item.strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _apply_registry_id_edits(
    current: set[str], add: list[str], remove: list[str]
) -> tuple[set[str], list[str], list[str]]:
    """Apply set-style registry ID edits; additions win an add/remove tie."""
    result = (current - set(remove)) | set(add)
    return result, sorted(result - current), sorted(current - result)


def _registry_name_error(value: Any) -> str | None:
    """Validate a name override without confusing literal quotes with clearing."""
    if value is not None and not isinstance(value, str):
        return "name must be a string or null."
    if value and all(char.isspace() or char in "\"'" for char in value):
        return (
            "name must contain more than quotes and whitespace. "
            "Use changes.name: null to clear the name override."
        )
    return None


def _set_entity_value_error(
    args: dict,
    hass: HomeAssistant,
    entry: er.RegistryEntry,
    *,
    restoring: bool,
) -> str | None:
    """Validate set_entity values and references without changing the registry."""
    name_error = _registry_name_error(args.get("name"))
    if name_error is not None:
        return name_error
    if "use_device_name" in args:
        if not isinstance(args["use_device_name"], bool):
            return "use_device_name must be true or false."
        if "name" in args:
            return "Send either name or use_device_name, not both."
        if args["use_device_name"] and not entity_follows_device_name(hass, entry):
            return (
                "use_device_name needs an entity on a named device and Home Assistant "
                "2026.10 or later, which shows such an entity under its device's name."
            )
    for field in ("icon", "device_class"):
        if field in args and args[field] is not None and not isinstance(args[field], str):
            return f"{field} must be a string or null."
    for field in ("enabled", "hidden"):
        if field in args and not isinstance(args[field], bool):
            return f"{field} must be true or false."

    if "new_entity_id" in args:
        new_entity_id = args["new_entity_id"]
        if not isinstance(new_entity_id, str) or not valid_entity_id(new_entity_id):
            return "new_entity_id must be a valid entity ID."
        if new_entity_id == entry.entity_id:
            return "new_entity_id is already this entity's ID."
        old_domain = entry.entity_id.split(".", 1)[0]
        if new_entity_id.split(".", 1)[0] != old_domain:
            return f"new_entity_id must stay in the {old_domain} domain."
        registry = er.async_get(hass)
        if registry.async_get(new_entity_id) is not None or hass.states.get(
            new_entity_id
        ) is not None:
            return "new_entity_id is already occupied."

    for field in ("add_labels", "remove_labels"):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return parsed
        if field == "add_labels":
            missing = [label_id for label_id in parsed if lr.async_get(hass).async_get_label(label_id) is None]
            if missing:
                return unknown_label_message(hass, missing[0])
    if restoring and "labels" in args:
        labels = args["labels"]
        if not isinstance(labels, list) or not all(
            isinstance(label_id, str) for label_id in labels
        ):
            return "labels must be a list of label ids."
        missing = [
            label_id
            for label_id in labels
            if lr.async_get(hass).async_get_label(label_id) is None
        ]
        if missing:
            return unknown_label_message(hass, missing[0])

    if "categories" in args:
        categories = args["categories"]
        if not isinstance(categories, dict):
            return "categories must be an object mapping scope to category id or null."
        category_registry = cr.async_get(hass)
        for scope, category_id in categories.items():
            if not isinstance(scope, str) or not scope:
                return "category scopes must be non-empty strings."
            if category_id is not None and not isinstance(category_id, str):
                return f"category {scope!r} must be a string id or null."
            if category_id is not None and category_registry.async_get_category(
                scope=scope, category_id=category_id
            ) is None:
                return unknown_category_message(hass, scope, category_id)

    if restoring:
        enum_fields = (
            ("disabled_by", er.RegistryEntryDisabler),
            ("hidden_by", er.RegistryEntryHider),
        )
        for field, enum_type in enum_fields:
            if field not in args or args[field] is None:
                continue
            try:
                enum_type(args[field])
            except (TypeError, ValueError):
                return f"Invalid stored {field} value."

    if not restoring and "enabled" in args and entry.disabled_by not in (
        None, er.RegistryEntryDisabler.USER
    ):
        return f"This entity is disabled by {entry.disabled_by.value}, not by the user."
    if not restoring and "hidden" in args and entry.hidden_by not in (
        None, er.RegistryEntryHider.USER
    ):
        return f"This entity is hidden by {entry.hidden_by.value}, not by the user."
    if args.get("enabled") is True and entry.device_id:
        device = device_registry_snapshot(hass).get(entry.device_id)
        if device is not None and device.disabled:
            return "The entity cannot be enabled while its device is disabled."
    return None


def _set_entity_args_error(
    args: dict,
    hass: HomeAssistant,
    entity_id: str,
    *,
    allow_restore_fields: bool = False,
) -> tuple[dict, str, str] | None:
    """Validate supported fields and reject requests with no registry update."""
    area_error = _registry_area_id_error(args, hass, entity_id)
    if area_error is not None:
        return area_error
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return _tool_error("Entity has no registry entry to edit."), "invalid_request", entity_id
    update_arguments = _SET_ENTITY_UPDATE_ARGUMENTS | (
        _SET_ENTITY_RESTORE_ARGUMENTS if allow_restore_fields else set()
    )
    if not any(field in args for field in update_arguments):
        return (
            _tool_error(
                "Provide at least one of the supported entity registry updates."
            ),
            "invalid_request",
            entity_id,
        )
    value_error = _set_entity_value_error(
        args, hass, entry, restoring=allow_restore_fields
    )
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", entity_id
    return None


_REGISTRY_MESA_FINGERPRINT = "_registry_mesa_fingerprint"


def _contains_entity_identity(value: Any, entity_id: str) -> bool:
    """Whether a Phoenix configuration subtree keys or values the exact ID."""
    if isinstance(value, dict):
        return any(
            key == entity_id or _contains_entity_identity(item, entity_id)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_entity_identity(item, entity_id) for item in value)
    return value == entity_id


def _entity_identity_references(
    data: PhoenixData, entity_id: str
) -> list[dict[str, Any]]:
    """Active Phoenix configuration whose identity would not follow a rename."""
    references: list[dict[str, Any]] = []
    for record in data.store.list_access_keys():
        if not record.is_valid():
            continue
        if entity_id in record.permissions.entities:
            references.append(
                {
                    "kind": "access_key_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                }
            )
        for preset in record.presets:
            if entity_id in preset.permissions.entities:
                references.append(
                    {
                        "kind": "preset_permission",
                        "access_key_id": record.id,
                        "access_key_name": record.name,
                        "preset_id": preset.id,
                        "preset_name": preset.name,
                    }
                )
    if entity_id in data.store.get_entity_hints():
        references.append({"kind": "global_hint", "entity_id": entity_id})
    if data.mesa is not None and data.mesa.store.get(entity_id) is not None:
        references.append({"kind": "mesa_entity_profile", "entity_id": entity_id})
    for pending in data.store.get_pending_approvals():
        if pending.get("status") != "pending":
            continue
        if pending.get("id") in data.approvals_in_progress:
            # The approval currently executing necessarily contains this entity
            # ID in its saved args; it is the operation, not a competing config
            # consumer. Other pending approvals remain blockers.
            continue
        if _contains_entity_identity(pending.get("args", {}), entity_id):
            references.append(
                {
                    "kind": "pending_approval",
                    "approval_id": pending.get("id"),
                    "tool_name": pending.get("tool_name"),
                }
            )
    return references


def _rename_blocker_error(
    data: PhoenixData, entity_id: str
) -> tuple[dict, str, str] | None:
    blockers = _entity_identity_references(data, entity_id)
    if not blockers:
        return None
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "Entity ID rename is blocked by Phoenix configuration keyed "
                        "to the old ID. Move or remove every blocker first."
                    ),
                    "entity_id": entity_id,
                    "blockers": blockers,
                },
                default=str,
            )
        ),
        "invalid_request",
        entity_id,
    )


def _registry_mesa_error(
    entity_id: str, action: str, decision: RegistryMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": f"MESA blocked entity registry {action}.",
                    "entity_id": entity_id,
                    "mesa": {
                        "rule": decision.rule,
                        "reason": decision.reason,
                        "effective_rule": decision.effective_rule,
                        "warnings": decision.warnings,
                    },
                },
                default=str,
            )
        ),
        "denied",
        entity_id,
    )


def _registry_mesa_decision(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    entity_id: str,
    *,
    action: str,
    service_data: dict[str, Any] | None = None,
    session_id: str,
) -> RegistryMesaDecision | tuple[dict, str, str]:
    """Resolve MESA fail-closed for a registry identity action."""
    try:
        return evaluate_registry_action(
            data,
            access_key,
            entity_id,
            action=action,
            service_data=service_data,
            session_id=session_id,
        )
    except Exception:  # noqa: BLE001 - a safety resolver failure must block
        _LOGGER.exception("MESA registry %s evaluation failed for %s", action, entity_id)
        return (
            _tool_error("MESA safety evaluation failed; no registry change was made."),
            "denied",
            entity_id,
        )


def _mesa_preview(decision: RegistryMesaDecision) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "rule": decision.rule,
        "reason": decision.reason,
        "effective_rule": decision.effective_rule,
        "warnings": decision.warnings,
    }



def _registry_write_precheck(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    tool_name: str,
    data: PhoenixData | None = None,
) -> tuple[dict, str, str] | None:
    """Pre-gate validation for registry writes; None means OK to proceed.

    Checks entity_id presence, write scope, and registry references so a doomed
    request is rejected before approval. The executor re-validates at apply time.
    """
    entity_id = str(args.get("entity_id") or "").strip()
    if not entity_id:
        return _tool_error("entity_id is required."), "invalid_request", tool_name
    permission = resolve_registry_access(entity_id, access_key, hass)
    perm_error = _registry_write_perm_error(permission, entity_id)
    if perm_error is not None:
        return perm_error
    registry_entry = er.async_get(hass).async_get(entity_id)
    canonical_id = registry_entry.entity_id if registry_entry is not None else entity_id
    if tool_name == "set_entity":
        args_error = _set_entity_args_error(args, hass, entity_id)
        if args_error is not None:
            return args_error
        if args.get("enabled") is False and resolve_registry_access(
            entity_id, access_key, hass, force_registry_only=True
        ) != Permission.WRITE:
            return (
                _tool_error(
                    "Disabling this entity would remove the access key's only write grant. "
                    "Grant WRITE to its device or domain first."
                ),
                "denied",
                entity_id,
            )
        if "new_entity_id" in args and data is not None:
            blocker_error = _rename_blocker_error(data, canonical_id)
            if blocker_error is not None:
                return blocker_error
    return None


_SET_DEVICE_UPDATE_ARGUMENTS = frozenset(
    {"name", "area_id", "enabled", "add_labels", "remove_labels"}
)
_SET_DEVICE_RESTORE_ARGUMENTS = frozenset({"labels", "disabled_by"})
_DEVICE_MESA_FINGERPRINT = "_device_mesa_fingerprint"


@dataclasses.dataclass
class _DeviceMesaDecision:
    """Aggregate MESA verdict for every entity and action on one device."""

    decision: str
    actions: list[str]
    entities: list[dict[str, Any]]
    warnings: list[str]
    profile_fingerprint: str
    blocked: list[tuple[str, str, str]]


def _device_meta_snapshot(device: Any) -> dict[str, Any]:
    """Restorable user metadata for one device registry entry."""
    return {
        "device_id": device.id,
        "name": device.name_by_user,
        "area_id": device.area_id,
        "disabled_by": getattr(device.disabled_by, "value", device.disabled_by),
        "labels": sorted(device.labels),
    }


def _device_public_meta(device: Any) -> dict[str, Any]:
    """Safe update response extending the restorable device snapshot."""
    return {
        **_device_meta_snapshot(device),
        "name": device.name_by_user or device.name,
        "original_name": device.name,
        "name_by_user": device.name_by_user,
    }


def _device_write_perm_error(
    permission: Permission,
    device_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> tuple[dict, str, str] | None:
    """Require explicit whole-device WRITE without hiding an already-visible row."""
    if permission == Permission.WRITE:
        return None
    if permission == Permission.NOT_FOUND:
        return _tool_error("Device not found."), "not_found", device_id
    readable = resolve_device_registry_access(device_id, access_key, hass) in (
        Permission.READ,
        Permission.WRITE,
    )
    if readable:
        return (
            _tool_error(
                "Whole-device registry edits require an explicit WRITE grant on "
                "this device; attached entities and domain grants authorize reads only."
            ),
            "denied",
            device_id,
        )
    return _tool_error("Device not found."), "denied", device_id


def _set_device_value_error(
    args: dict,
    hass: HomeAssistant,
    device: Any,
    *,
    restoring: bool,
) -> str | None:
    """Validate set_device values and registry references without mutation."""
    name_error = _registry_name_error(args.get("name"))
    if name_error is not None:
        return name_error
    if "area_id" in args:
        area_id = args["area_id"]
        if area_id is not None and not isinstance(area_id, str):
            return "area_id must be a string or null."
        if area_id and ar.async_get(hass).async_get_area(area_id) is None:
            return "Unknown area_id."
    if "enabled" in args and not isinstance(args["enabled"], bool):
        return "enabled must be true or false."

    for field in ("add_labels", "remove_labels"):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return parsed
        if field == "add_labels":
            missing = [
                label_id
                for label_id in parsed
                if lr.async_get(hass).async_get_label(label_id) is None
            ]
            if missing:
                return unknown_label_message(hass, missing[0])
    if restoring and "labels" in args:
        labels = args["labels"]
        if not isinstance(labels, list) or not all(
            isinstance(label_id, str) for label_id in labels
        ):
            return "labels must be a list of label ids."
        missing = [
            label_id
            for label_id in labels
            if lr.async_get(hass).async_get_label(label_id) is None
        ]
        if missing:
            return unknown_label_message(hass, missing[0])
    if restoring and "disabled_by" in args and args["disabled_by"] is not None:
        try:
            dr.DeviceEntryDisabler(args["disabled_by"])
        except (TypeError, ValueError):
            return "Invalid stored disabled_by value."

    will_enable = args.get("enabled") is True or (
        restoring and "disabled_by" in args and args["disabled_by"] is None
    )
    if will_enable:
        disabled_owners = []
        for entry_id in device_config_entry_ids(device):
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None and entry.disabled_by is not None:
                disabled_owners.append(entry_id)
        if disabled_owners:
            return (
                "The device cannot be enabled while an owning config entry is "
                f"disabled: {disabled_owners[0]}."
            )
    if not restoring and "enabled" in args and device.disabled_by not in (
        None,
        dr.DeviceEntryDisabler.USER,
    ):
        return f"This device is disabled by {device.disabled_by.value}, not by the user."
    return None


def _set_device_args_error(
    args: dict,
    hass: HomeAssistant,
    device_id: str,
    *,
    allow_restore_fields: bool = False,
) -> tuple[dict, str, str] | None:
    """Validate supported device fields and reject no-op-shaped requests."""
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    supported = _SET_DEVICE_UPDATE_ARGUMENTS | (
        _SET_DEVICE_RESTORE_ARGUMENTS if allow_restore_fields else set()
    )
    if not any(field in args for field in supported):
        return (
            _tool_error("Provide at least one supported device registry update."),
            "invalid_request",
            device_id,
        )
    value_error = _set_device_value_error(
        args, hass, device, restoring=allow_restore_fields
    )
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", device_id
    return None


def _device_child_write_error(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    device: Any,
    *,
    restoring: bool,
) -> tuple[dict, str, str] | None:
    """Block authorization-affecting edits when any child is not writable."""
    affects_children = any(field in args for field in ("name", "area_id", "enabled"))
    if restoring:
        affects_children = affects_children or "disabled_by" in args
    if not affects_children:
        return None
    blocked = [
        entity_id
        for entity_id in device_registry_entity_ids(hass, device.id)
        if resolve_registry_access(
            entity_id, access_key, hass, force_registry_only=True
        )
        != Permission.WRITE
    ]
    if not blocked:
        return None
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "The device update would affect registry entities that are "
                        "not writable by this access key."
                    ),
                    "device_id": device.id,
                    "blocked_entities": blocked,
                }
            )
        ),
        "denied",
        device.id,
    )


def _device_mesa_actions(args: dict, *, restoring: bool) -> list[str]:
    actions: list[str] = []
    if "name" in args:
        actions.append("rename")
    if "enabled" in args:
        actions.append("enable" if args["enabled"] else "disable")
    elif restoring and "disabled_by" in args:
        actions.append("enable" if args["disabled_by"] is None else "disable")
    return actions


def _device_mesa_service_data(args: dict) -> dict[str, Any]:
    """Metadata parameters for MESA after Phoenix expands the exact targets.

    device_id and area_id are Home Assistant target selectors. Passing either
    while asking mesa-core to evaluate one already-resolved entity is correctly
    rejected as contradictory: the selector could expand to other entities.
    Area/label changes are not MESA actions, so only the fields that describe
    rename or enable/disable belong in the synthetic call.
    """
    return {
        field: args[field]
        for field in ("name", "enabled", "disabled_by")
        if field in args
    }


def _device_mesa_decision(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    device: Any,
    *,
    actions: list[str],
    service_data: dict[str, Any],
    session_id: str,
    entity_ids: list[str] | None = None,
) -> _DeviceMesaDecision | tuple[dict, str, str]:
    """Resolve all device MESA actions across the exact attached membership."""
    if entity_ids is None:
        entity_ids = device_registry_entity_ids(hass, device.id)
    else:
        entity_ids = sorted(set(entity_ids))
    mesa_active = (
        (data.mesa is not None or data.mesa_setup_failed is True)
        and data.store.get_settings().mesa_mode != MESA_MODE_OFF
    )
    if mesa_active and not entity_ids:
        reason = (
            "MESA is active and this device has no registry entity from which "
            "mesa-core can resolve its inherited device context."
        )
        decision_doc = {
            "decision": "deny",
            "actions": actions,
            "entities": [],
            "warnings": [],
            "blocked": [{
                "entity_id": None,
                "rule": "mesa:unresolved_device_context",
                "reason": reason,
            }],
        }
        fingerprint = hashlib.sha256(
            json.dumps(decision_doc, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return _DeviceMesaDecision(
            decision="deny",
            actions=actions,
            entities=[],
            warnings=[],
            profile_fingerprint=fingerprint,
            blocked=[(
                device.id,
                "mesa:unresolved_device_context",
                reason,
            )],
        )

    resolved_entities: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocked: list[tuple[str, str, str]] = []
    has_confirm = False
    try:
        for action in actions:
            for entity_id in entity_ids:
                decision = evaluate_registry_action(
                    data,
                    access_key,
                    entity_id,
                    action=action,
                    registry_domain="device_registry",
                    service_data=service_data,
                    session_id=session_id,
                )
                resolved_entities.append({
                    "entity_id": entity_id,
                    "action": action,
                    "decision": decision.decision,
                    "rule": decision.rule,
                    "reason": decision.reason,
                    "warnings": decision.warnings,
                    "effective_rule": decision.effective_rule,
                    "profile_fingerprint": decision.profile_fingerprint,
                })
                for warning in decision.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
                if decision.decision == "deny":
                    blocked.append((entity_id, decision.rule, decision.reason))
                elif decision.decision == "confirm":
                    has_confirm = True
    except Exception:  # noqa: BLE001 - MESA resolution must fail closed
        _LOGGER.exception("MESA device registry evaluation failed for %s", device.id)
        return (
            _tool_error("MESA safety evaluation failed; no device change was made."),
            "denied",
            device.id,
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "device_id": device.id,
                "actions": actions,
                "entities": resolved_entities,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return _DeviceMesaDecision(
        decision="deny" if blocked else "confirm" if has_confirm else "allow",
        actions=actions,
        entities=resolved_entities,
        warnings=warnings,
        profile_fingerprint=fingerprint,
        blocked=blocked,
    )


def _device_mesa_preview(decision: _DeviceMesaDecision) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "actions": decision.actions,
        "entities": [
            {key: value for key, value in item.items() if key != "profile_fingerprint"}
            for item in decision.entities
        ],
        "warnings": decision.warnings,
        "profile_fingerprint": decision.profile_fingerprint[:16],
    }


def _device_mesa_error(
    device: Any, decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": "MESA blocked the device registry update.",
                    "device_id": device.id,
                    "mesa": _device_mesa_preview(decision),
                    "blocked": [
                        {"entity_id": eid, "rule": rule, "reason": reason}
                        for eid, rule, reason in decision.blocked
                    ],
                },
                default=str,
            )
        ),
        "denied",
        device.id,
    )


def _device_mesa_context_changed_error(
    device: Any, decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "The device's entity membership or effective MESA profile "
                        "changed after approval. Review the update again."
                    ),
                    "device_id": device.id,
                    "mesa": _device_mesa_preview(decision),
                },
                default=str,
            )
        ),
        "denied",
        device.id,
    )


def _set_device_precheck(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> tuple[dict, str, str] | None:
    device_id = str(args.get("device_id") or "").strip()
    if not device_id:
        return _tool_error("device_id is required."), "invalid_request", "set_device"
    perm_error = _device_write_perm_error(
        resolve_device_registry_write(device_id, access_key, hass),
        device_id,
        access_key,
        hass,
    )
    if perm_error is not None:
        return perm_error
    args_error = _set_device_args_error(args, hass, device_id)
    if args_error is not None:
        return args_error
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    return _device_child_write_error(
        args, access_key, hass, device, restoring=False
    )


async def _build_diff_set_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _DeviceMesaDecision | None = None,
) -> dict[str, Any]:
    device_id = str(args.get("device_id") or "")
    device = device_registry_snapshot(hass).get(device_id)
    before = _device_meta_snapshot(device) if device is not None else {}
    fields: list[str] = []
    preview: dict[str, Any] = {}
    for field in ("name", "area_id"):
        if field in args:
            fields.append(field)
            preview[field] = {"before": before.get(field), "after": args[field]}
    if "enabled" in args:
        fields.append("enabled")
        preview["enabled"] = {
            "before": before.get("disabled_by") is None,
            "after": args["enabled"],
        }
    if device is not None and (
        "add_labels" in args or "remove_labels" in args
    ):
        fields.append("labels")
        add = _registry_id_edit(args.get("add_labels", []), "add_labels")
        remove = _registry_id_edit(args.get("remove_labels", []), "remove_labels")
        after_labels = None
        if not isinstance(add, str) and not isinstance(remove, str):
            after_labels = sorted(
                _apply_registry_id_edits(set(device.labels), add, remove)[0]
            )
        preview["labels"] = {
            "before": sorted(device.labels),
            "after": after_labels,
        }
    if device is not None and args.get("enabled") is False:
        affected = device_registry_entity_ids(hass, device.id)
        preview["affected_entities"] = affected
        preview["relationships"] = await _registry_relationships_preview(
            hass, affected
        )
        preview["warning"] = (
            "Home Assistant will stop publishing this device's entities while "
            "the device is disabled. Consumers are not rewritten."
        )
    if device is not None and args.get("enabled") is True:
        preview["activation"] = {
            "reload_or_restart_may_be_needed": True,
            "note": (
                "The registry entry will be enabled. Its owning integration may "
                "need to reload, or Home Assistant may need to restart, before "
                "live entities return."
            ),
        }
    if mesa_decision is not None:
        preview["mesa"] = _device_mesa_preview(mesa_decision)
    label = (
        device.name_by_user or device.name or device_id
        if device is not None
        else device_id
    )
    return {
        "kind": "system_action",
        **_summary(
            "set_device",
            fields=", ".join(fields) or "nothing",
            device_id=device_id,
        ),
        "target": {"type": "device", "id": device_id, "label": label},
        "preview": preview,
    }


async def _tool_set_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: edit user-controlled device registry metadata."""
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_device"
    args, error = normalize_tool_args("set_device", args)
    if error:
        return _tool_error(error), "invalid_request", "set_device"
    pre = _set_device_precheck(args, access_key, hass)
    if pre is not None:
        return pre
    device_id = str(args.get("device_id") or "").strip()
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    actions = _device_mesa_actions(args, restoring=False)
    mesa_decision: _DeviceMesaDecision | None = None
    approval_args = dict(args)
    if actions:
        resolved = _device_mesa_decision(
            data,
            access_key,
            hass,
            device,
            actions=actions,
            service_data=_device_mesa_service_data(args),
            session_id=request_id or "set_device",
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        if mesa_decision.decision == "deny":
            if mesa_decision.blocked:
                fire_mesa_blocked_event(hass, access_key, mesa_decision.blocked)
            return _device_mesa_error(device, mesa_decision)
        # The normal capability gate may create the approval even when MESA's
        # own verdict is allow. Pin the complete membership/profile context in
        # either case so a device cannot gain a child or change profile in the
        # approval window and still execute under the old preview.
        approval_args[_DEVICE_MESA_FINGERPRINT] = (
            mesa_decision.profile_fingerprint
        )
    diff_builder = lambda: _build_diff_set_device(  # noqa: E731
        args, access_key, hass, mesa_decision
    )
    blocked = await _gate(
        "cap_registry_write",
        access_key,
        hass,
        data,
        tool_name="set_device",
        args=approval_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa_decision is not None and mesa_decision.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="set_device",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_set_device(args, access_key, hass, data)


@dataclasses.dataclass(frozen=True)
class _RegistryCommit:
    """A completed synchronous mutation, ready for best-effort history capture."""

    result: tuple[dict, str, str]
    resource_type: str
    resource_id: str
    action: str
    before: dict | None
    after: dict | None
    alias: str | None


async def _finish_registry_commit(
    commit: _RegistryCommit | tuple[dict, str, str],
    access_key: AccessKeyRecord,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Persist already captured history without reopening the mutation boundary."""
    if isinstance(commit, tuple):
        return commit
    await _record_version(
        data, access_key, resource_type=commit.resource_type,
        resource_id=commit.resource_id, action=commit.action,
        before=commit.before, after=commit.after, alias=commit.alias,
    )
    check_request_authority()
    return commit.result


async def _execute_set_device(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Commit registry state before awaiting history persistence."""
    return await _finish_registry_commit(
        _commit_set_device(args, access_key, hass, data), access_key, data
    )


def _commit_set_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> _RegistryCommit | tuple[dict, str, str]:
    """Validate, mutate, and capture one device result without yielding."""
    check_request_authority()
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_device"
    device_id = str(args.get("device_id") or "").strip()
    if not device_id:
        return _tool_error("device_id is required."), "invalid_request", "set_device"
    perm_error = _device_write_perm_error(
        resolve_device_registry_write(device_id, access_key, hass),
        device_id,
        access_key,
        hass,
    )
    if perm_error is not None:
        return perm_error
    registry = dr.async_get(hass)
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    restoring = _restore_ctx.get() is not None
    args_error = _set_device_args_error(
        args, hass, device_id, allow_restore_fields=restoring
    )
    if args_error is not None:
        return args_error
    child_error = _device_child_write_error(
        args, access_key, hass, device, restoring=restoring
    )
    if child_error is not None:
        return child_error

    actions = _device_mesa_actions(args, restoring=restoring)
    mesa_decision: _DeviceMesaDecision | None = None
    if actions:
        resolved = _device_mesa_decision(
            data,
            access_key,
            hass,
            device,
            actions=actions,
            service_data=_device_mesa_service_data(args),
            session_id="set_device_execute",
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        saved_fingerprint = args.get(_DEVICE_MESA_FINGERPRINT)
        if (
            _approved_exec_ctx.get()
            and isinstance(saved_fingerprint, str)
            and saved_fingerprint != mesa_decision.profile_fingerprint
        ):
            return _device_mesa_context_changed_error(device, mesa_decision)
        mesa_confirmed = (
            _approved_exec_ctx.get()
            and isinstance(saved_fingerprint, str)
            and saved_fingerprint == mesa_decision.profile_fingerprint
        )
        if mesa_decision.decision == "deny" or (
            mesa_decision.decision == "confirm" and not mesa_confirmed
        ):
            if mesa_decision.blocked:
                fire_mesa_blocked_event(hass, access_key, mesa_decision.blocked)
            return _device_mesa_error(device, mesa_decision)

    updates: dict[str, Any] = {}
    if "name" in args:
        updates["name_by_user"] = args["name"] or None
    if "area_id" in args:
        updates["area_id"] = args["area_id"] or None
    if "enabled" in args:
        updates["disabled_by"] = (
            None if args["enabled"] else dr.DeviceEntryDisabler.USER
        )
    label_add: list[str] = []
    label_remove: list[str] = []
    for field, sink in (("add_labels", label_add), ("remove_labels", label_remove)):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return _tool_error(parsed), "invalid_request", device_id
        sink.extend(parsed)
    labels_added: list[str] = []
    labels_removed: list[str] = []
    if label_add or label_remove:
        new_labels, labels_added, labels_removed = _apply_registry_id_edits(
            set(device.labels), label_add, label_remove
        )
        updates["labels"] = new_labels
    if restoring:
        if "labels" in args:
            updates["labels"] = set(args["labels"])
        if "disabled_by" in args:
            updates["disabled_by"] = (
                dr.DeviceEntryDisabler(args["disabled_by"])
                if args["disabled_by"] is not None
                else None
            )
    if not updates:
        return (
            _tool_error("Provide at least one supported device registry update."),
            "invalid_request",
            device_id,
        )

    before = _device_meta_snapshot(device)
    try:
        updated = update_device_registry_entry(registry, device_id, updates)
    except Exception as exc:  # noqa: BLE001 - surface a clean tool error
        _LOGGER.error("set_device failed for %s: %s", device_id, exc)
        return _tool_error("Failed to update device."), "denied", device_id
    if updated is None:
        return _tool_error("Failed to read updated device."), "denied", device_id
    after = _device_meta_snapshot(updated)

    body: dict[str, Any] = {
        "device_id": device_id,
        "updated": _device_public_meta(updated),
    }
    if label_add or label_remove:
        body["labels_added"] = labels_added
        body["labels_removed"] = labels_removed
    if "enabled" in args and args["enabled"]:
        entity_ids = device_registry_entity_ids(hass, device_id)
        body["activation"] = {
            "reload_or_restart_may_be_needed": any(
                hass.states.get(entity_id) is None for entity_id in entity_ids
            ),
            "note": (
                "The registry entry is enabled. Its owning integration may need "
                "to reload, or Home Assistant may need to restart, before live "
                "entities return."
            ),
        }
    if mesa_decision is not None and mesa_decision.warnings:
        body["mesa_advisory"] = mesa_decision.warnings
        _mesa_advisory_ctx.set(True)
    return _RegistryCommit(
        result=(_tool_success(json.dumps(body, default=str)), "allowed", device_id),
        resource_type="device", resource_id=device_id, action="edit",
        before=before, after=after, alias=updated.name_by_user or updated.name or device_id,
    )


_REMOVE_DEVICE_CONTEXT_FINGERPRINT = "_remove_device_context_fingerprint"
_REMOVE_DEVICE_MESA_FINGERPRINT = "_remove_device_mesa_fingerprint"


@dataclasses.dataclass
class _DeviceRemovalContext:
    """One integration owner's exact, revalidatable device-removal context."""

    device: Any
    config_entry: Any
    hook: Any
    owner_ids: list[str]
    affected_entity_ids: list[str]
    child_device_ids: list[str]
    fingerprint: str


async def _async_device_removal_hook(hass: HomeAssistant, entry: Any) -> Any | None:
    """Load an integration and return its device-removal hook, if supported.

    Home Assistant's config-entry support cache can be uninitialized. Loading
    the component is the authoritative check and is repeated in the executor.
    The async component getter is newer than Phoenix's minimum HA version, so
    retain the synchronous getter fallback used by Home Assistant 2025.2.
    """
    try:
        integration = await loader.async_get_integration(hass, entry.domain)
        async_get_component = getattr(integration, "async_get_component", None)
        if callable(async_get_component):
            component = await async_get_component()
        else:
            component = integration.get_component()
    except (ImportError, loader.IntegrationNotFound):
        return None
    except Exception:  # noqa: BLE001 - a broken integration must fail closed
        _LOGGER.exception(
            "Could not load %s to resolve device removal support", entry.domain
        )
        return None
    hook = getattr(component, "async_remove_config_entry_device", None)
    return hook if callable(hook) else None


def _device_owner_summary(entry: Any, *, supports_removal: bool | None = None) -> dict:
    """Safe config-entry owner projection for removal previews and results."""
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": entry.title,
        "disabled_by": getattr(entry.disabled_by, "value", entry.disabled_by),
        "supports_remove_device": (
            supports_removal
            if supports_removal is not None
            else getattr(entry, "supports_remove_device", None)
        ),
    }


def _device_permission_references(
    data: PhoenixData, device_id: str
) -> list[dict[str, Any]]:
    """Active-access-key and preset permission nodes keyed to one device."""
    references: list[dict[str, Any]] = []
    for record in data.store.list_access_keys():
        if not record.is_valid():
            continue
        if device_id in record.permissions.devices:
            references.append({
                "kind": "access_key_permission",
                "access_key_id": record.id,
                "access_key_name": record.name,
            })
        for preset in record.presets:
            if device_id in preset.permissions.devices:
                references.append({
                    "kind": "preset_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                    "preset_id": preset.id,
                    "preset_name": preset.name,
                })
    return references


def _device_removal_context_fingerprint(
    device_id: str,
    entry: Any,
    owner_ids: list[str],
    affected_entity_ids: list[str],
    child_device_ids: list[str],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "device_id": device_id,
                "config_entry_id": entry.entry_id,
                "integration": entry.domain,
                "owner_ids": owner_ids,
                "affected_entity_ids": affected_entity_ids,
                "child_device_ids": child_device_ids,
                "supports_removal": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _resolve_device_removal_context(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> _DeviceRemovalContext | tuple[dict, str, str]:
    """Load support, then resolve fresh state for the same selected owner."""
    context = _device_removal_context(args, access_key, hass)
    if isinstance(context, tuple):
        return context
    owner_id: str = context.config_entry.entry_id
    hook = await _async_device_removal_hook(hass, context.config_entry)
    check_request_authority()
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_device"
    if hass.config_entries.async_get_entry(owner_id) is not context.config_entry:
        return _tool_error("The owning config entry changed. Review the removal again."), "denied", context.device.id
    fresh = _device_removal_context(
        {**args, "config_entry_id": owner_id}, access_key, hass, hook
    )
    if isinstance(fresh, tuple):
        return fresh
    if hook is None:
        return (
            _tool_error("This integration does not support device removal."),
            "invalid_request", context.device.id,
        )
    return fresh


def _device_removal_context(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    hook: Any = None,
) -> _DeviceRemovalContext | tuple[dict, str, str]:
    """Read current ownership and membership in one event-loop segment."""
    check_request_authority()
    device_id = str(args.get("device_id") or "").strip()
    if not device_id:
        return _tool_error("device_id is required."), "invalid_request", "remove_device"
    perm_error = _device_write_perm_error(
        resolve_device_registry_write(device_id, access_key, hass),
        device_id,
        access_key,
        hass,
    )
    if perm_error is not None:
        return perm_error
    device_snapshot = device_registry_snapshot(hass)
    device = device_snapshot.get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id

    owner_ids = device_config_entry_ids(device)
    selected = args.get("config_entry_id")
    if selected is not None and (not isinstance(selected, str) or not selected.strip()):
        return (
            _tool_error("config_entry_id must be a non-empty string."),
            "invalid_request",
            device_id,
        )
    if selected is None:
        if len(owner_ids) != 1:
            message = (
                "config_entry_id is required when a device has multiple owners."
                if owner_ids
                else "This device has no owning config entry to remove."
            )
            return _tool_error(message), "invalid_request", device_id
        selected = owner_ids[0]
    else:
        selected = selected.strip()
    if selected not in owner_ids:
        return (
            _tool_error("The selected config entry does not own this device."),
            "invalid_request",
            device_id,
        )
    entry = hass.config_entries.async_get_entry(selected)
    if entry is None:
        return _tool_error("The owning config entry no longer exists."), "invalid_request", device_id
    if entry.domain == DOMAIN:
        return _tool_error("Phoenix MCP devices cannot be removed."), "denied", device_id
    affected_entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in er.async_get(hass).entities.values()
        if registry_entry.device_id == device_id
        and registry_entry.config_entry_id == selected
    )
    child_device_ids = list(device_snapshot.child_ids(device_id))
    fingerprint = _device_removal_context_fingerprint(
        device_id,
        entry,
        owner_ids,
        affected_entity_ids,
        child_device_ids,
    )
    return _DeviceRemovalContext(
        device=device,
        config_entry=entry,
        hook=hook,
        owner_ids=owner_ids,
        affected_entity_ids=affected_entity_ids,
        child_device_ids=child_device_ids,
        fingerprint=fingerprint,
    )


def _device_removal_mesa_error(
    context: _DeviceRemovalContext, decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": "MESA blocked device removal.",
                    "device_id": context.device.id,
                    "config_entry_id": context.config_entry.entry_id,
                    "mesa": _device_mesa_preview(decision),
                    "blocked": [
                        {"entity_id": eid, "rule": rule, "reason": reason}
                        for eid, rule, reason in decision.blocked
                    ],
                },
                default=str,
            )
        ),
        "denied",
        context.device.id,
    )


def _device_removal_context_changed_error(
    context: _DeviceRemovalContext, mesa_decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "The device's ownership, affected entities, integration "
                        "support, child relationships, or effective MESA profile "
                        "changed after approval. Review the removal again."
                    ),
                    "device_id": context.device.id,
                    "config_entry_id": context.config_entry.entry_id,
                    "mesa": _device_mesa_preview(mesa_decision),
                },
                default=str,
            )
        ),
        "denied",
        context.device.id,
    )


async def _build_diff_remove_device(
    context: _DeviceRemovalContext,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    mesa_decision: _DeviceMesaDecision,
) -> dict[str, Any]:
    remaining_owner_ids = [
        owner_id
        for owner_id in context.owner_ids
        if owner_id != context.config_entry.entry_id
    ]
    remaining_owners = []
    for owner_id in remaining_owner_ids:
        owner = hass.config_entries.async_get_entry(owner_id)
        if owner is not None:
            remaining_owners.append(_device_owner_summary(owner))
    visible_children = [
        child_id
        for child_id in context.child_device_ids
        if resolve_device_registry_access(child_id, access_key, hass)
        in (Permission.READ, Permission.WRITE)
    ]
    stored_device_profile = (
        data.mesa.store.get_device_profile(context.device.id)
        if data.mesa is not None
        else None
    )
    preview = {
        "selected_owner": _device_owner_summary(
            context.config_entry, supports_removal=True
        ),
        "affected_entities": context.affected_entity_ids,
        "relationships": await _registry_relationships_preview(
            hass, context.affected_entity_ids
        ),
        "child_devices_losing_parent_if_device_disappears": visible_children,
        "remaining_owners": remaining_owners,
        "device_permission_references": _device_permission_references(
            data, context.device.id
        ),
        "device_mesa_profile": (
            stored_device_profile.to_dict()
            if stored_device_profile is not None
            else None
        ),
        "mesa": _device_mesa_preview(mesa_decision),
        "warning": (
            "The owning integration will decide whether removal is accepted. "
            "Known consumers are not rewritten, and this operation cannot be restored."
        ),
    }
    label = context.device.name_by_user or context.device.name or context.device.id
    return {
        "kind": "system_action",
        **_summary(
            "remove_device",
            device_id=context.device.id,
            config_entry_id=context.config_entry.entry_id,
        ),
        "target": {"type": "device", "id": context.device.id, "label": label},
        "preview": preview,
    }


async def _tool_remove_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: remove one integration's ownership through its HA hook."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_device"
    error = unknown_argument_error("remove_device", args)
    if error:
        return _tool_error(error), "invalid_request", "remove_device"
    resolved = await _resolve_device_removal_context(args, access_key, hass)
    if isinstance(resolved, tuple):
        return resolved
    context = resolved
    mesa = _device_mesa_decision(
        data,
        access_key,
        hass,
        context.device,
        actions=["remove"],
        # config_entry_id is also a Home Assistant target selector. Phoenix has
        # already expanded the selected owner to exact entity IDs, so passing
        # the selector into mesa-core would correctly be rejected as
        # contradictory (it could name entities outside this decision).
        service_data={},
        session_id=request_id or "remove_device",
        entity_ids=context.affected_entity_ids,
    )
    if isinstance(mesa, tuple):
        return mesa
    if mesa.decision == "deny":
        if mesa.blocked:
            fire_mesa_blocked_event(hass, access_key, mesa.blocked)
        return _device_removal_mesa_error(context, mesa)

    approval_args = {
        **args,
        "device_id": context.device.id,
        "config_entry_id": context.config_entry.entry_id,
        _REMOVE_DEVICE_CONTEXT_FINGERPRINT: context.fingerprint,
        _REMOVE_DEVICE_MESA_FINGERPRINT: mesa.profile_fingerprint,
    }
    diff_builder = lambda: _build_diff_remove_device(  # noqa: E731
        context, access_key, hass, data, mesa
    )
    blocked = await _gate(
        "cap_integration_write",
        access_key,
        hass,
        data,
        tool_name="remove_device",
        args=approval_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="remove_device",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_remove_device(approval_args, access_key, hass, data)


def _device_removal_cleanup_error(device_id: str) -> tuple[dict, str, str]:
    """Report partial or uncertain effects without exposing changed context."""
    return (
        _tool_error(
            "The integration accepted removal, but authority or device "
            "context changed before registry cleanup. Phoenix skipped "
            "cleanup; integration effects may already have occurred. "
            "Inspect the device before retrying."
        ),
        "denied", device_id,
    )


async def _execute_remove_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate and invoke an integration-aware device removal."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_device"
    resolved = await _resolve_device_removal_context(args, access_key, hass)
    if isinstance(resolved, tuple):
        return resolved
    context = resolved
    mesa = _device_mesa_decision(
        data,
        access_key,
        hass,
        context.device,
        actions=["remove"],
        service_data={},
        session_id="remove_device_execute",
        entity_ids=context.affected_entity_ids,
    )
    if isinstance(mesa, tuple):
        return mesa
    approved = _approved_exec_ctx.get()
    saved_context = args.get(_REMOVE_DEVICE_CONTEXT_FINGERPRINT)
    saved_mesa = args.get(_REMOVE_DEVICE_MESA_FINGERPRINT)
    if approved and (
        saved_context != context.fingerprint
        or saved_mesa != mesa.profile_fingerprint
    ):
        return _device_removal_context_changed_error(context, mesa)
    mesa_confirmed = (
        approved
        and isinstance(saved_mesa, str)
        and saved_mesa == mesa.profile_fingerprint
    )
    if mesa.decision == "deny" or (
        mesa.decision == "confirm" and not mesa_confirmed
    ):
        if mesa.blocked:
            fire_mesa_blocked_event(hass, access_key, mesa.blocked)
        return _device_removal_mesa_error(context, mesa)

    device_id = context.device.id
    selected_owner = context.config_entry.entry_id
    label = context.device.name_by_user or context.device.name or device_id
    try:
        accepted = await context.hook(hass, context.config_entry, context.device)
    except Exception as exc:  # noqa: BLE001 - integration hook is third-party code
        _LOGGER.error(
            "remove_device hook failed for %s via %s: %s",
            device_id,
            context.config_entry.domain,
            exc,
        )
        return (
            _tool_error("The integration failed while removing this device; Phoenix made no registry change."),
            "denied",
            device_id,
        )
    if not accepted:
        return (
            _tool_error("The integration rejected device removal; the registry was not changed by Phoenix."),
            "denied",
            device_id,
        )

    # The integration may already have acted. Refusal here cannot undo its work
    # and must never replay the hook or claim an untouched device.
    try:
        check_request_authority()
    except AuthorityChangedError:
        return _device_removal_cleanup_error(device_id)
    registry = dr.async_get(hass)
    current = registry.async_get(device_id)
    if current is not None and selected_owner in device_config_entry_ids(current):
        fresh = _device_removal_context(
            {"device_id": device_id, "config_entry_id": selected_owner},
            access_key, hass, context.hook,
        )
        changed = (
            effective_cap(access_key, "cap_integration_write") == CAP_DENY
            or isinstance(fresh, tuple)
        )
        if isinstance(fresh, _DeviceRemovalContext):
            changed = changed or (
                fresh.config_entry is not context.config_entry
                or fresh.fingerprint != context.fingerprint
            )
            current_mesa = _device_mesa_decision(
                data, access_key, hass, fresh.device, actions=["remove"],
                service_data={}, session_id="remove_device_cleanup",
                entity_ids=fresh.affected_entity_ids,
            )
            changed = changed or (
                isinstance(current_mesa, tuple)
                or current_mesa.profile_fingerprint != mesa.profile_fingerprint
            )
        if changed:
            return _device_removal_cleanup_error(device_id)
        try:
            owners = device_config_entry_ids(current)
            if len(owners) > 1:
                # HA releases with composite/multi-owner devices still need
                # the legacy selective removal path.
                registry.async_update_device(
                    device_id, remove_config_entry_id=selected_owner
                )
            else:
                # HA 2026.9 models a device as belonging to one config entry;
                # removing that owner removes the device itself.
                registry.async_remove_device(device_id)
        except Exception as exc:  # noqa: BLE001 - HA registry boundary
            _LOGGER.error(
                "remove_device registry cleanup failed for %s owner %s: %s",
                device_id,
                selected_owner,
                exc,
            )
            return (
                _tool_error(
                    "The integration accepted removal, but Home Assistant could "
                    "not remove its registry ownership. Inspect the device before retrying."
                ),
                "denied",
                device_id,
            )
        current = registry.async_get(device_id)
    if current is not None and selected_owner in device_config_entry_ids(current):
        return (
            _tool_error(
                "The integration accepted removal, but its device ownership remains."
            ),
            "denied",
            device_id,
        )
    disappeared = current is None
    remaining_owner_ids = device_config_entry_ids(current) if current is not None else []
    remaining_owners = []
    for owner_id in remaining_owner_ids:
        owner = hass.config_entries.async_get_entry(owner_id)
        if owner is not None:
            remaining_owners.append(_device_owner_summary(owner))

    version_before = {
        "restorable": False,
        "operation": "remove_config_entry_device",
        "device": _device_meta_snapshot(context.device),
        "selected_owner": _device_owner_summary(
            context.config_entry, supports_removal=True
        ),
        "owners_before": context.owner_ids,
        "affected_entities": context.affected_entity_ids,
        "device_disappeared": disappeared,
        "remaining_owner_ids": remaining_owner_ids,
    }
    await _record_version(
        data,
        access_key,
        resource_type="device",
        resource_id=device_id,
        action="delete",
        before=version_before,
        after=None,
        alias=label,
    )
    check_request_authority()
    body: dict[str, Any] = {
        "device_id": device_id,
        "config_entry_id": selected_owner,
        "removed": True,
        "device_disappeared": disappeared,
        "remaining_owners": remaining_owners,
    }
    if mesa.warnings:
        body["mesa_advisory"] = mesa.warnings
        _mesa_advisory_ctx.set(True)
    return _tool_success(json.dumps(body, default=str)), "allowed", device_id


async def _build_diff_set_entity(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: RegistryMesaDecision | None = None,
) -> dict:
    entity_id = str(args.get("entity_id") or "")
    entry = er.async_get(hass).async_get(entity_id)
    before = _entity_meta_snapshot(entry) if entry else {}
    fields = [f for f in ("name", "icon", "area_id", "device_class") if f in args]
    preview: dict[str, Any] = {
        f: {"before": before.get(f), "after": args.get(f)} for f in fields
    }
    if entry is not None and "use_device_name" in args:
        fields.append("use_device_name")
        preview["use_device_name"] = {
            "before": entity_name_source(hass, entry) == "device",
            "after": args["use_device_name"],
        }
    if "enabled" in args:
        fields.append("enabled")
        preview["enabled"] = {
            "before": before.get("disabled_by") is None,
            "after": args["enabled"],
        }
    if "hidden" in args:
        fields.append("hidden")
        preview["hidden"] = {
            "before": before.get("hidden_by") is not None,
            "after": args["hidden"],
        }
    # Aliases preview as the RESOLVED spoken names on both sides, which is what
    # the operator is actually approving a change to; the sentinel is an
    # implementation detail and naming it here would ask them to review a
    # concept the tool does not expose. Best-effort like every diff builder.
    if entry is not None and ("add_aliases" in args or "remove_aliases" in args):
        fields.append("aliases")
        add = _alias_edit(args.get("add_aliases", []), "add_aliases")
        remove = _alias_edit(args.get("remove_aliases", []), "remove_aliases")
        applied = (
            _apply_alias_edits(list(entry.aliases or ()), add, remove)
            if not isinstance(add, str) and not isinstance(remove, str)
            else "unavailable"
        )
        after_names = None
        if not isinstance(applied, str):
            # Resolve the same way async_get_entity_aliases does, rather than
            # building a throwaway registry entry to hand it: one less HA
            # internal to construct, and the .strip() matches its own.
            full = er.async_get_full_entity_name(hass, entry)
            after_names = [
                full if alias is er.COMPUTED_NAME else alias.strip()
                for alias in applied[0]
            ]
        preview["aliases"] = {
            "before": er.async_get_entity_aliases(hass, entry),
            "after": after_names,
        }
    if entry is not None and ("add_labels" in args or "remove_labels" in args):
        fields.append("labels")
        add = _registry_id_edit(args.get("add_labels", []), "add_labels")
        remove = _registry_id_edit(args.get("remove_labels", []), "remove_labels")
        after_labels = None
        if not isinstance(add, str) and not isinstance(remove, str):
            after_labels = sorted(
                _apply_registry_id_edits(set(entry.labels), add, remove)[0]
            )
        preview["labels"] = {"before": sorted(entry.labels), "after": after_labels}
    if entry is not None and "categories" in args:
        fields.append("categories")
        after_categories = dict(entry.categories)
        if isinstance(args["categories"], dict):
            for scope, category_id in args["categories"].items():
                if category_id is None:
                    after_categories.pop(scope, None)
                else:
                    after_categories[scope] = category_id
        preview["categories"] = {
            "before": dict(sorted(entry.categories.items())),
            "after": dict(sorted(after_categories.items())),
        }
    if entry is not None and "new_entity_id" in args:
        fields.append("entity_id")
        preview["entity_id"] = {
            "before": entry.entity_id,
            "after": args["new_entity_id"],
        }
        preview["relationships"] = await _registry_relationship_preview(
            hass, entry.entity_id
        )
        data = hass.data.get(DOMAIN)
        if data is not None:
            preview["phoenix_configuration"] = _entity_identity_references(
                data, entry.entity_id
            )
        preview["warning"] = (
            "Home Assistant does not rewrite consumers of the old entity ID. "
            "Update every relationship listed here separately."
        )
    if mesa_decision is not None:
        preview["mesa"] = _mesa_preview(mesa_decision)
    target_label = before.get("name")
    if not target_label and entry is not None:
        target_label = er.async_get_full_entity_name(hass, entry)
    return {
        "kind": "system_action",
        **_summary("set_entity", fields=", ".join(fields) or "nothing", entity_id=entity_id),
        "target": {"type": "entity", "id": entity_id, "label": target_label or entity_id},
        "preview": preview,
    }


async def _build_diff_delete_entity(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: RegistryMesaDecision | None = None,
) -> dict:
    entity_id = str(args.get("entity_id") or "")
    entry = er.async_get(hass).async_get(entity_id)
    snap = _entity_meta_snapshot(entry) if entry else {}
    relationships = (
        await _registry_relationship_preview(hass, entity_id)
        if entry is not None
        else {"consumers": [], "consumer_count": 0, "searched": []}
    )
    data = hass.data.get(DOMAIN)
    preview: dict[str, Any] = {
        "name": snap.get("name"),
        "area_id": snap.get("area_id"),
        "warning": "Removes the entity's registry entry. A live entity's integration may re-create it; an orphan stays gone. Not re-creatable through Phoenix MCP.",
        "relationships": relationships,
        "phoenix_configuration": (
            _entity_identity_references(data, entity_id) if data is not None else []
        ),
    }
    if mesa_decision is not None:
        preview["mesa"] = _mesa_preview(mesa_decision)
    return {
        "kind": "system_action",
        **_summary("delete_entity", entity_id=entity_id),
        "target": {"type": "entity", "id": entity_id, "label": snap.get("name") or entity_id},
        "preview": preview,
    }


async def _tool_set_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: edit an entity's registry metadata (Confirm-eligible)."""
    # Capability gate first: a denied access key gets a uniform Forbidden with no entity
    # work, so the tool can never be a scope/existence oracle when the cap is off.
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_entity"
    args, error = normalize_tool_args("set_entity", args)
    if error:
        return _tool_error(error), "invalid_request", "set_entity"
    # Then validate entity scope and registry references before gating so a doomed
    # request cannot create a pending approval. The executor re-validates at apply time.
    pre = _registry_write_precheck(args, access_key, hass, "set_entity", data)
    if pre is not None:
        return pre
    mesa_decision: RegistryMesaDecision | None = None
    approval_args = dict(args)
    if "new_entity_id" in args:
        entity_id = str(args.get("entity_id") or "").strip()
        registry_entry = er.async_get(hass).async_get(entity_id)
        if registry_entry is not None:
            entity_id = registry_entry.entity_id
            approval_args["entity_id"] = entity_id
        resolved = _registry_mesa_decision(
            data,
            access_key,
            entity_id,
            action="rename",
            service_data={"new_entity_id": args["new_entity_id"]},
            session_id=request_id or "set_entity",
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        if mesa_decision.decision == "deny":
            fire_mesa_blocked_event(
                hass,
                access_key,
                [(entity_id, mesa_decision.rule, mesa_decision.reason)],
            )
            return _registry_mesa_error(entity_id, "rename", mesa_decision)
        if mesa_decision.decision == "confirm":
            approval_args[_REGISTRY_MESA_FINGERPRINT] = (
                mesa_decision.profile_fingerprint
            )
    diff_builder = lambda: _build_diff_set_entity(  # noqa: E731
        args, access_key, hass, mesa_decision
    )
    blocked = await _gate(
        "cap_registry_write", access_key, hass, data,
        tool_name="set_entity", args=approval_args, request_id=request_id,
        client_ip=client_ip, diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa_decision is not None and mesa_decision.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="set_entity",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_set_entity(args, access_key, hass, data)


async def _execute_set_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Commit registry state before awaiting history persistence."""
    return await _finish_registry_commit(
        _commit_set_entity(args, access_key, hass, data), access_key, data
    )


def _commit_set_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> _RegistryCommit | tuple[dict, str, str]:
    """Validate, mutate, and capture one entity result without yielding."""
    check_request_authority()
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_entity"
    entity_id = str(args.get("entity_id") or "").strip()
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is not None:
        entity_id = registry_entry.entity_id
    if not entity_id:
        return _tool_error("entity_id is required."), "invalid_request", "set_entity"
    err = _registry_write_perm_error(
        resolve_registry_access(entity_id, access_key, hass), entity_id
    )
    if err is not None:
        return err
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None:
        return _tool_error("Entity has no registry entry to edit."), "invalid_request", entity_id
    restoring = _restore_ctx.get() is not None
    args_error = _set_entity_args_error(
        args, hass, entity_id, allow_restore_fields=restoring
    )
    if args_error is not None:
        return args_error
    new_entity_id = args.get("new_entity_id")
    mesa_decision: RegistryMesaDecision | None = None
    if isinstance(new_entity_id, str):
        blocker_error = _rename_blocker_error(data, entry.entity_id)
        if blocker_error is not None:
            return blocker_error
        resolved = _registry_mesa_decision(
            data,
            access_key,
            entry.entity_id,
            action="rename",
            service_data={"new_entity_id": new_entity_id},
            session_id="set_entity_execute",
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        mesa_confirmed = (
            _approved_exec_ctx.get()
            and isinstance(args.get(_REGISTRY_MESA_FINGERPRINT), str)
            and args[_REGISTRY_MESA_FINGERPRINT]
            == mesa_decision.profile_fingerprint
        )
        if mesa_decision.decision == "deny" or (
            mesa_decision.decision == "confirm" and not mesa_confirmed
        ):
            fire_mesa_blocked_event(
                hass,
                access_key,
                [(entry.entity_id, mesa_decision.rule, mesa_decision.reason)],
            )
            return _registry_mesa_error(
                entry.entity_id, "rename", mesa_decision
            )
    will_disable = args.get("enabled") is False or (
        restoring and args.get("disabled_by") is not None
    )
    if will_disable and resolve_registry_access(
        entity_id, access_key, hass, force_registry_only=True
    ) != Permission.WRITE:
        return (
            _tool_error(
                "Disabling this entity would remove the access key's only write grant. "
                "Grant WRITE to its device or domain first."
            ),
            "denied",
            entity_id,
        )

    updates: dict = {}
    if "name" in args:
        # A restore replays the captured override exactly: an empty name is Home
        # Assistant's "Use device name", not a cleared override.
        updates["name"] = args["name"] if restoring else args["name"] or None
    if "use_device_name" in args:
        updates["name"] = "" if args["use_device_name"] else None
    if "icon" in args:
        updates["icon"] = args["icon"] or None
    if "area_id" in args:
        area_id = args["area_id"]
        updates["area_id"] = area_id or None
    if "device_class" in args:
        updates["device_class"] = args["device_class"] or None
    if "enabled" in args:
        updates["disabled_by"] = (
            None if args["enabled"] else er.RegistryEntryDisabler.USER
        )
    if "hidden" in args:
        updates["hidden_by"] = (
            er.RegistryEntryHider.USER if args["hidden"] else None
        )
    if isinstance(new_entity_id, str):
        updates["new_entity_id"] = new_entity_id
    alias_add: list[str] = []
    alias_remove: list[str] = []
    for field, sink in (("add_aliases", alias_add), ("remove_aliases", alias_remove)):
        if field not in args:
            continue
        parsed = _alias_edit(args[field], field)
        if isinstance(parsed, str):
            return _tool_error(parsed), "invalid_request", entity_id
        sink.extend(parsed)
    added: list[str] = []
    removed: list[str] = []
    if alias_add or alias_remove:
        applied = _apply_alias_edits(list(entry.aliases or ()), alias_add, alias_remove)
        if isinstance(applied, str):
            return _tool_error(applied), "invalid_request", entity_id
        new_aliases, added, removed = applied
        updates["aliases"] = new_aliases
    # A restore re-applies the snapshot's alias list ABSOLUTELY, which the tool
    # itself deliberately cannot do. Reproducing the admin's chosen snapshot is
    # the whole operation (a YAML restore is likewise exempt from the dropped-key
    # guard), and the raw wire form carries the sentinel and the order back
    # unchanged. _restore_ctx is read here in the event loop, not passed by the
    # caller, so no agent-reachable path can set it.
    if "aliases" in args and restoring:
        updates["aliases"] = _alias_unwire(args["aliases"])
    label_add: list[str] = []
    label_remove: list[str] = []
    for field, sink in (("add_labels", label_add), ("remove_labels", label_remove)):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return _tool_error(parsed), "invalid_request", entity_id
        sink.extend(parsed)
    labels_added: list[str] = []
    labels_removed: list[str] = []
    if label_add or label_remove:
        new_labels, labels_added, labels_removed = _apply_registry_id_edits(
            set(entry.labels), label_add, label_remove
        )
        updates["labels"] = new_labels
    if "categories" in args and not restoring:
        categories = dict(entry.categories)
        for scope, category_id in args["categories"].items():
            if category_id is None:
                categories.pop(scope, None)
            else:
                categories[scope] = category_id
        updates["categories"] = categories
    if restoring:
        if "labels" in args:
            updates["labels"] = set(args["labels"])
        if "categories" in args:
            updates["categories"] = dict(args["categories"])
        if "disabled_by" in args:
            updates["disabled_by"] = (
                er.RegistryEntryDisabler(args["disabled_by"])
                if args["disabled_by"] is not None else None
            )
        if "hidden_by" in args:
            updates["hidden_by"] = (
                er.RegistryEntryHider(args["hidden_by"])
                if args["hidden_by"] is not None else None
            )
    if not updates:
        return (
            _tool_error("Provide at least one of the supported entity registry updates."),
            "invalid_request", entity_id,
        )

    before = _entity_meta_snapshot(reg.async_get(entity_id))
    try:
        reg.async_update_entity(entity_id, **updates)
    except Exception as exc:  # noqa: BLE001 - surface a clean tool error
        _LOGGER.error("set_entity failed for %s: %s", entity_id, exc)
        return _tool_error("Failed to update entity."), "denied", entity_id
    updated_id = new_entity_id if isinstance(new_entity_id, str) else entity_id
    updated_entry = reg.async_get(updated_id)
    if updated_entry is None:
        _LOGGER.error(
            "set_entity updated %s but no registry entry exists at %s",
            entity_id,
            updated_id,
        )
        return _tool_error("Failed to read updated entity."), "denied", entity_id
    after = _entity_meta_snapshot(updated_entry)

    public_after = {
        **after,
        "name_source": entity_name_source(hass, updated_entry),
        "aliases": er.async_get_entity_aliases(hass, updated_entry),
    }
    body: dict = {"entity_id": updated_id, "updated": public_after}
    if updated_id != entity_id:
        body["previous_entity_id"] = entity_id
    if alias_add or alias_remove:
        # What CHANGED, not what was asked for: an alias already present or one
        # that was not there is a no-op, and a caller that cannot see that reads
        # a typo as a successful edit.
        body["aliases_added"] = added
        body["aliases_removed"] = removed
        updated = reg.async_get(updated_id)
        if updated is not None:
            body["aliases"] = er.async_get_entity_aliases(hass, updated)
    if label_add or label_remove:
        body["labels_added"] = labels_added
        body["labels_removed"] = labels_removed
    if "enabled" in args and args["enabled"]:
        if updated_entry is not None:
            config_entry = (
                hass.config_entries.async_get_entry(updated_entry.config_entry_id)
                if updated_entry.config_entry_id else None
            )
            body["activation"] = {
                "requires_restart": config_entry is None or not config_entry.supports_unload,
                "note": (
                    "The registry entry is enabled. Its integration may need to be "
                    "reloaded or Home Assistant restarted before a live state appears."
                ),
            }
    if mesa_decision is not None and mesa_decision.warnings:
        body["mesa_advisory"] = mesa_decision.warnings
        _mesa_advisory_ctx.set(True)
    return _RegistryCommit(
        result=(_tool_success(json.dumps(body, default=str)), "allowed", updated_id),
        resource_type="entity", resource_id=updated_id, action="edit",
        before=before, after=after, alias=after.get("name") or updated_id,
    )


async def _tool_delete_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: delete an entity's registry entry (Confirm-eligible)."""
    # Capability gate first: a denied access key gets a uniform Forbidden with no entity
    # work, so the tool can never be a scope/existence oracle when the cap is off.
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_entity"
    error = unknown_argument_error("delete_entity", args)
    if error:
        return _tool_error(error), "invalid_request", "delete_entity"
    pre = _registry_write_precheck(args, access_key, hass, "delete_entity", data)
    if pre is not None:
        return pre
    entity_id = str(args.get("entity_id") or "").strip()
    resolved = _registry_mesa_decision(
        data,
        access_key,
        entity_id,
        action="delete",
        session_id=request_id or "delete_entity",
    )
    if isinstance(resolved, tuple):
        return resolved
    mesa_decision = resolved
    if mesa_decision.decision == "deny":
        fire_mesa_blocked_event(
            hass,
            access_key,
            [(entity_id, mesa_decision.rule, mesa_decision.reason)],
        )
        return _registry_mesa_error(entity_id, "delete", mesa_decision)
    approval_args = {**args, "entity_id": entity_id}
    if mesa_decision.decision == "confirm":
        approval_args[_REGISTRY_MESA_FINGERPRINT] = (
            mesa_decision.profile_fingerprint
        )
    diff_builder = lambda: _build_diff_delete_entity(  # noqa: E731
        args, access_key, hass, mesa_decision
    )
    blocked = await _gate(
        "cap_registry_write", access_key, hass, data,
        tool_name="delete_entity", args=approval_args, request_id=request_id,
        client_ip=client_ip, diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa_decision.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="delete_entity",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_delete_entity(args, access_key, hass, data)


async def _execute_delete_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Commit registry state before awaiting history persistence."""
    return await _finish_registry_commit(
        _commit_delete_entity(args, access_key, hass, data), access_key, data
    )


def _commit_delete_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> _RegistryCommit | tuple[dict, str, str]:
    """Validate, mutate, and capture one entity result without yielding."""
    check_request_authority()
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_entity"
    entity_id = str(args.get("entity_id") or "").strip()
    if not entity_id:
        return _tool_error("entity_id is required."), "invalid_request", "delete_entity"
    err = _registry_write_perm_error(
        resolve_registry_access(entity_id, access_key, hass), entity_id
    )
    if err is not None:
        return err
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None:
        return _tool_error("Entity has no registry entry to delete."), "invalid_request", entity_id
    resolved = _registry_mesa_decision(
        data,
        access_key,
        entry.entity_id,
        action="delete",
        session_id="delete_entity_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    mesa_decision = resolved
    mesa_confirmed = (
        _approved_exec_ctx.get()
        and isinstance(args.get(_REGISTRY_MESA_FINGERPRINT), str)
        and args[_REGISTRY_MESA_FINGERPRINT]
        == mesa_decision.profile_fingerprint
    )
    if mesa_decision.decision == "deny" or (
        mesa_decision.decision == "confirm" and not mesa_confirmed
    ):
        fire_mesa_blocked_event(
            hass,
            access_key,
            [(entry.entity_id, mesa_decision.rule, mesa_decision.reason)],
        )
        return _registry_mesa_error(entry.entity_id, "delete", mesa_decision)
    before = _entity_meta_snapshot(entry)
    try:
        reg.async_remove(entity_id)
    except Exception as exc:  # noqa: BLE001 - surface a clean tool error
        _LOGGER.error("delete_entity failed for %s: %s", entity_id, exc)
        return _tool_error("Failed to delete entity."), "denied", entity_id

    body: dict[str, Any] = {"entity_id": entity_id, "deleted": True}
    if mesa_decision.warnings:
        body["mesa_advisory"] = mesa_decision.warnings
        _mesa_advisory_ctx.set(True)
    return _RegistryCommit(
        result=(_tool_success(json.dumps(body)), "allowed", entity_id),
        resource_type="entity", resource_id=entity_id, action="delete",
        before=before, after=None, alias=before.get("name") or entity_id,
    )


async def _tool_render_template(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: render a Jinja2 template against permitted entity state."""
    if effective_cap(access_key, "cap_template_render") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "render_template"

    template_str = args.get("template", "")
    if not template_str:
        return _tool_error("Missing required argument: template"), "invalid_request", "render_template"

    try:
        rendered = await async_render_template_for_access_key(template_str, access_key, hass, data)
    except AuthorityChangedError:
        raise
    except TemplateRenderError as exc:
        return _tool_error(str(exc)), "invalid_request", "render_template"
    except Exception:
        return _tool_error("Template rendering failed. Check syntax, input size, and resource limits."), "invalid_request", "render_template"

    return _tool_success(rendered), "allowed", "render_template"


async def _tool_restart_ha(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: restart HA (gated by cap_restart, supports Confirm)."""
    blocked = await _gate(
        "cap_restart", access_key, hass, data,
        tool_name="restart_ha", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_restart_ha(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_restart_ha(args, access_key, hass, data)


async def _execute_restart_ha(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Journal restart once; a response cannot prove process replacement."""
    from .config_recovery import ConfigurationRecoveryError, runtime_identity

    if effective_cap(access_key, "cap_restart") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "restart_ha"
    recovery = data.config_recovery
    rid = None
    try:
        async with recovery.resource("homeassistant:restart"):
            identity = runtime_identity(hass)
            rid = await recovery.begin("homeassistant:restart", "restart_ha", access_key.id,
                                       {"snapshot_type": "restart_receipt", "runtime_identity": identity})
            await recovery.mark(rid, "dispatching")
            check_request_authority()
            try:
                async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
                    await hass.services.async_call("homeassistant", "restart", {}, blocking=True)
            except asyncio.TimeoutError:
                pass
            except ServiceNotFound:
                await recovery.mark(rid, "not_applied")
                return _tool_error("Restart service is unavailable; nothing was dispatched."), "invalid_request", "restart_ha"
            return _tool_success(json.dumps({
                "success": True, "partial": True, "status": "dispatched", "recovery_id": rid,
                "runtime_identity": identity, "retry_safe": False,
                "message": "Reconnect with a fresh client and compare get_config runtime_identity. Do not repeat the restart request.",
            })), "allowed", "restart_ha"
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else "Restart outcome is uncertain. Reconnect and inspect current Home Assistant state before retrying."
        return _tool_error(json.dumps({"error": message, "recovery_id": rid, "retry_safe": False})), "invalid_request", "restart_ha"
    finally:
        if rid is not None:
            recovery.leave(rid)


async def _tool_get_approval_status(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: poll one of the access key's own approvals, or list its pending ones.

    With no approval_id it lists this access key's outstanding approvals. A record
    owned by another access key answers exactly like a missing one ("Approval not
    found."), so a refusal is not an existence oracle.
    """
    from .approvals import STATUS_PENDING, get_approval, list_approvals  # noqa: PLC0415

    await data.agent_recovery.reconcile(data.store, access_key.id)
    approval_id = args.get("approval_id")
    if approval_id is None:
        # No id: enumerate this access key's own pending approvals (own data only).
        pending = list_approvals(data.store, status=STATUS_PENDING, access_key_id=access_key.id)
        items = [
            {
                "approval_id": a.id,
                "status": a.status,
                "tool_name": a.tool_name,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            }
            for a in pending
        ]
        body = {"count": len(items), "pending_approvals": items}
        body["agent_recovery"] = data.agent_recovery.records(access_key.id)
        return _tool_success(json.dumps(body, default=str)), "allowed", "get_approval_status"

    if not isinstance(approval_id, str) or not approval_id:
        return _tool_error("Missing approval_id."), "invalid_request", "get_approval_status"
    record = get_approval(data.store, approval_id)
    if record is None or record.access_key_id != access_key.id:
        return _tool_error("Approval not found."), "not_found", "get_approval_status"
    payload = {
        "approval_id": record.id,
        "status": record.status,
        "tool_name": record.tool_name,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
        "result": record.result,
        "rejected_reason": record.rejected_reason,
    }
    return _tool_success(json.dumps(payload, default=str)), "allowed", _approval_resource(record)


def _approval_status_result(record: Any, *, resolved: bool) -> dict:
    """The status body, plus the operator-accepted note when the action ran.

    The note tells an agent that the operator reviewed and accepted this exact
    change, so it stops proposing variations of it. Polls must carry it as well
    as the interactive paths (`_await_inline_confirm` and Agent Chat): an
    approval that outlived the inline window is exactly the case where the
    agent would otherwise keep revising an accepted change.

    Only an APPROVED record with a stored `tool_result` qualifies. An
    approved-but-execution-failed record transitions to failed with the
    `execution_failed` slug, and a plain rejection has no stored result; neither
    is something the operator accepted.

    Never double-appends: the inline path builds its own copy from the stored
    record and never writes the note back, so the record read here is always the
    unannotated one.
    """
    from .approvals import STATUS_APPROVED  # noqa: PLC0415

    payload = _approval_status_payload(record, resolved=resolved)
    result = _tool_success(json.dumps(payload, default=str))
    if record.status == STATUS_APPROVED and (record.result or {}).get("tool_result") is not None:
        return _operator_accepted_result(result)
    return result


def _approval_status_payload(record: Any, *, resolved: bool) -> dict:
    """Status body shared by wait_for_approval (same fields as get_approval_status)."""
    return {
        "approval_id": record.id,
        "status": record.status,
        "resolved": resolved,
        "tool_name": record.tool_name,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
        "result": record.result,
        "rejected_reason": record.rejected_reason,
    }


def _approval_batch_payload(record: Any, *, resolved: bool) -> dict:
    """One approval's status in the PLURAL form, with its result summarized.

    Built from `_approval_status_payload` and then narrowed, so the two forms
    share ONE definition of the status fields and cannot drift into answering
    the same question differently depending on how many ids were asked about.

    The `result` a single-id call returns is the executor's whole
    CallToolResult, which for an authoring tool is the config it just wrote.
    That suits one record, but a batch of them can exceed a caller's output
    limit and fail to report its own outcome. What survives is what a caller
    acts on: whether each one errored, and the text if it did, because an agent
    told only "rejected" with the executor's reason buried retries the same
    doomed call.
    """
    payload = _approval_status_payload(record, resolved=resolved)
    payload.pop("result", None)
    payload.update(_approval_result_digest(record))
    return payload


def _approval_result_digest(record: Any) -> dict:
    """Whether this approval's stored result was an error, and its text, bounded."""
    tool_result = (record.result or {}).get("tool_result")
    if not isinstance(tool_result, dict):
        return {}
    text = "\n".join(
        c.get("text", "") for c in tool_result.get("content", [])
        if isinstance(c, dict) and c.get("type") == "text"
    ).strip()
    digest: dict[str, Any] = {"result_is_error": bool(tool_result.get("isError"))}
    if text:
        digest["result_text"] = text[:MAX_APPROVAL_RESULT_CHARS]
        digest["result_truncated"] = len(text) > MAX_APPROVAL_RESULT_CHARS
    return digest


async def _wait_for_many_approvals(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Block until every named approval resolves, or the timeout expires.

    ONE listener for the whole set rather than a wait per id: the ids resolve in
    whatever order the operator works through them (and a batch approve resolves
    several inside one request), so waiting on them in sequence would block on
    #1 while #2 and #3 were already done.

    Returns a per-approval list plus the ids still outstanding, so a timeout is
    partial information rather than a failure: the caller learns exactly which
    ones landed and can wait again for the rest.
    """
    from .approvals import STATUS_APPROVED, STATUS_PENDING, get_approval  # noqa: PLC0415

    raw = args.get("approval_ids")
    if not isinstance(raw, list) or not raw or not all(isinstance(i, str) and i for i in raw):
        return (
            _tool_error("approval_ids must be a non-empty array of approval id strings."),
            "invalid_request", "wait_for_approval",
        )
    # Order-preserving dedupe: a repeated id would otherwise be reported twice and
    # inflate the outstanding count against itself.
    ids = list(dict.fromkeys(raw))
    if len(ids) > MAX_BATCH_APPROVALS:
        return (
            _tool_error(f"wait_for_approval accepts at most {MAX_BATCH_APPROVALS} approval_ids."),
            "invalid_request", "wait_for_approval",
        )

    records = {}
    for approval_id in ids:
        record = get_approval(data.store, approval_id)
        # Unknown and belonging-to-another-access-key answer identically, so a refusal
        # is not an existence oracle for another access key's queue.
        if record is None or record.access_key_id != access_key.id:
            return _tool_error("Approval not found."), "not_found", "wait_for_approval"
        records[approval_id] = record

    outstanding = {i for i, r in records.items() if r.status == STATUS_PENDING}
    if outstanding:
        timeout = _clamp_timeout(args.get("timeout", MAX_SUBSCRIPTION_SECONDS))
        future: asyncio.Future = hass.loop.create_future()

        @callback
        def _on_resolved(event: Any) -> None:
            outstanding.discard(event.data.get("approval_id"))
            if not outstanding and not future.done():
                future.set_result(True)

        unsub = hass.bus.async_listen(f"{DOMAIN}_approval_resolved", _on_resolved)
        _set_progress_status(
            f"Waiting for operator approval: {len(outstanding)} pending", total=float(timeout),
            key="agentchat.progress.waitingApprovals",
            params={"count": len(outstanding)},
        )
        try:
            await asyncio.wait_for(future, timeout)
        except TimeoutError:
            pass  # partial result below; the re-read decides what actually landed
        finally:
            unsub()
            _set_progress_status(None)

    # Re-read every record: some may have resolved without an event reaching
    # this listener (the expiry sweep), and a batch approve resolves several at once.
    latest = {i: (get_approval(data.store, i) or records[i]) for i in ids}
    still_pending = [i for i, r in latest.items() if r.status == STATUS_PENDING]
    payload = {
        "approvals": [
            _approval_batch_payload(r, resolved=r.status != STATUS_PENDING)
            for r in latest.values()
        ],
        "resolved": not still_pending,
        "pending": still_pending,
        "note": (
            "Each approval's result is summarized here so a whole batch fits in "
            "one reply. Call get_approval_status with a single approval_id for "
            "that one's full result."
        ),
    }
    result = _tool_success(json.dumps(payload, default=str))
    # The accepted-note applies as soon as ANY of them landed as an operator
    # approval, for the same reason it does on a single one: those changes are
    # final and must not be revised.
    if any(
        r.status == STATUS_APPROVED and (r.result or {}).get("tool_result") is not None
        for r in latest.values()
    ):
        result = _operator_accepted_result(result)
    return result, "allowed", f"approvals:{len(ids)}"


async def _tool_wait_for_approval(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: block until the access key's own approval(s) resolve, or until timeout.

    A bounded server-side wait (not a stream): returns immediately if already
    resolved, else waits on the phoenix_mcp_approval_resolved event filtered to
    the requested approval ids. Own-data only (another access key's id answers like a
    missing one, as in get_approval_status); no capability required.

    Accepts `approval_ids` (a list), including a one-item list for a single
    approval. Confirm-gated calls return pending_approval immediately by default
    (see DEFAULT_CONFIRM_INLINE_WAIT_SECONDS), so an agent making a run of writes
    holds N approval ids and needs ONE place to block until the operator has dealt
    with them, one at a time or in a batch. Waiting on each id in turn would
    serialise it back into the stall the staged model exists to remove.
    """
    args, error = normalize_tool_args("wait_for_approval", args)
    if error:
        return _tool_error(error), "invalid_request", "wait_for_approval"
    if "approval_ids" in args:
        return await _wait_for_many_approvals(args, access_key, hass, data)
    return _tool_error("approval_ids is required."), "invalid_request", "wait_for_approval"


# Executor registry for the admin-approval gate. When an admin approves a pending
# request, the approve handler looks up the saved tool_name here and invokes the
# corresponding _execute_X function with the saved args.
_EXECUTOR_REGISTRY: dict[str, Any] = {}


def _register_executor(tool_name: str, fn: Any) -> None:
    """Record an executor function for a tool. Called once at module import."""
    _EXECUTOR_REGISTRY[tool_name] = fn


# Tool dispatch registry: name -> (handler, context params it accepts, bound kwargs).
# Populated by the _register_tool block at the foot of this module, once every
# handler is defined.
_TOOL_HANDLERS: dict[str, tuple[Any, tuple[str, ...], dict[str, Any]]] = {}

# Request context a handler opts into by naming the parameter. Reads take
# (args, access_key, hass); gated writes add all three of these.
_TOOL_CONTEXT_PARAMS = ("data", "request_id", "client_ip")


def _register_tool(name: str, handler: Any, /, **bound: Any) -> None:
    """Bind a tool name to its handler. Called once per tool at module import.

    The handler's parameter names are read from its code object (not
    inspect.signature, which can evaluate annotations eagerly on Python 3.14) so
    the dispatcher passes just the context each one takes, instead of every
    handler accepting a uniform signature it mostly ignores. bound supplies
    per-registration constants, which is how the dashboard card ops and the
    mesa_* tools share one handler each.

    name and handler are positional-only so that no bound kwarg (the card ops
    bind their own tool_name) can collide with this function's own parameters.
    """
    if name in _TOOL_HANDLERS:
        raise ValueError(f"duplicate tool registration: {name}")
    code = handler.__code__
    accepted = set(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])
    wanted = tuple(p for p in _TOOL_CONTEXT_PARAMS if p in accepted)
    _TOOL_HANDLERS[name] = (handler, wanted, bound)


def _restore_access_key(user_id: str) -> AccessKeyRecord:
    """Synthetic pass-through access key for re-applying a config under admin authority.

    pass_through makes policy_engine.resolve() return WRITE for every entity, so the
    per-entity scope checks inside the scene/helper executors pass for an admin who
    has full authority. Every capability is set to allow explicitly: pass_through
    only promotes deny for non-exempt caps, and the blueprint executors check the
    pass-through-EXEMPT domain caps (cap_automation_write / cap_script_write)
    inside the executor, which would refuse an admin restore on the defaults.
    It is never persisted and never authenticates a request.
    """
    return AccessKeyRecord(
        id=f"__restore__:{user_id}",
        name="(admin restore)",
        access_key_hash="",
        created_at=utcnow(),
        created_by=user_id,
        pass_through=True,
        **{cap: CAP_ALLOW for cap in CAPABILITY_NAMES},  # type: ignore[arg-type]  # every CAPABILITY_NAMES entry is a cap_* str field
    )


async def _resource_exists(hass: HomeAssistant, resource_type: str, resource_id: str) -> bool:
    """Whether a versioned resource currently exists (picks restore edit vs recreate)."""
    try:
        if resource_type in ("automation", "script", "scene"):
            current = await hass.async_add_executor_job(
                yaml_includes.read_entry, hass.config.config_dir, resource_type, resource_id,
            )
            return current is not None
        if resource_type == "helper":
            from .tools.helper import _storage_items
            ht, _, hid = resource_id.partition(":")
            rows = _storage_items(ht, await async_ws_command(hass, f"{ht}/list", {}))
            if rows is None:
                raise ValueError("Unreadable helper storage")
            return any(row.get("id") == hid for row in rows)
    except Exception:
        from .config_recovery import ConfigurationRecoveryError
        raise ConfigurationRecoveryError("Current configuration could not be read; restore was not dispatched.") from None
    return False


async def async_restore_configuration(
    recovery_id: str, side: str, admin_user_id: str, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Restore a recovery snapshot following an authenticated administrator review."""
    access_key = _restore_access_key(admin_user_id)
    ctx = _restore_ctx.set({"user_id": admin_user_id})
    try:
        args = {"recovery_id": recovery_id, "side": side}
        receipt = await _tool_restore_configuration(args, access_key, hass, data)
        if receipt[1] != "allowed" or receipt[0].get("isError"):
            return receipt
        body = json.loads(receipt[0]["content"][0]["text"])
        args.update({key: body[key] for key in ("snapshot_digest", "target_fingerprint")})
        return await _execute_restore_configuration(args, access_key, hass, data)
    finally:
        _restore_ctx.reset(ctx)


async def async_restore_version(
    record: Any, admin_user_id: str, hass: HomeAssistant, data: PhoenixData, side: str | None = None
) -> tuple[dict, str, str]:
    """Re-apply a stored config version under admin authority."""
    if side == "before":
        target = record.before
    elif side == "after":
        target = record.after
    else:
        target = record.after if record.after is not None else record.before
    if not isinstance(target, dict):
        return _tool_error("This version has no configuration to restore on that side."), "invalid_request", "async_restore_version"
    # The executors manage ids themselves; a stored config may carry one (e.g. a
    # deleted helper's full item), so drop it before re-applying.
    target = {k: v for k, v in target.items() if k != "id"}

    resource_type = record.resource_type
    resource_id = record.resource_id
    if resource_type in ("automation", "script", "scene") and yaml_includes.contains_tag_strings(target):
        # The stored snapshot flattened YAML tags to display strings ("!secret x");
        # writing them back as quoted strings would silently break the config.
        # Fail closed, like the oversized-snapshot marker below.
        return _tool_error(
            "This version contains YAML tags such as !secret or !include and "
            "cannot be restored automatically. Re-create it manually."
        ), "invalid_request", "async_restore_version"
    access_key = _restore_access_key(admin_user_id)
    from .config_recovery import ConfigurationRecoveryError
    try:
        exists = await _resource_exists(hass, resource_type, resource_id)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", "async_restore_version"

    from .config_recovery import recovery_source
    source_ctx = recovery_source.set(target)
    ctx = _restore_ctx.set({"user_id": admin_user_id})
    try:
        if resource_type == "automation":
            if exists:
                current = await hass.async_add_executor_job(
                    yaml_includes.read_entry, hass.config.config_dir, "automation", resource_id,
                )
                return await _execute_edit_automation({
                    "automation_id": resource_id, "config": target,
                    "expected_hash": content_hash(current),
                }, access_key, hass, data)
            # Recreate in place under the original id so the rollback lands on the
            # same timeline and a second restore just edits it.
            return await _execute_create_automation({"config": target, "automation_id": resource_id}, access_key, hass, data)
        if resource_type == "script":
            if exists:
                return await _execute_edit_script({"script_id": resource_id, "config": target}, access_key, hass, data)
            return await _execute_create_script({"script_id": resource_id, "config": target}, access_key, hass, data)
        if resource_type == "scene":
            if exists:
                return await _execute_edit_scene({"scene_id": resource_id, "config": target}, access_key, hass, data)
            return await _execute_create_scene({"config": target, "scene_id": resource_id}, access_key, hass, data)
        if resource_type == "helper":
            ht, _, hid = resource_id.partition(":")
            if ht == "tag":
                # Scan timestamps and scanner device ids are operational evidence,
                # not authorable configuration. Never rewind them from version
                # history. A deleted tag is recreated under its original stable
                # physical identifier; an existing tag edit must not receive the
                # create-only tag_id field.
                target = {
                    key: target[key]
                    for key in ("name", "description")
                    if key in target
                }
                if not exists:
                    target["tag_id"] = hid
            elif ht == "person":
                # Person versions deliberately store only privacy-safe booleans
                # for HA user/picture bindings. Existing-person rollback preserves
                # those private fields by omission; deleted-person recreation is
                # intentionally unlinked and pictureless rather than persisting a
                # raw HA user id or private path in Changes history.
                target = {
                    key: target[key]
                    for key in ("name", "device_trackers")
                    if key in target
                }
            recovery_source.set({"snapshot_type": "storage_helper", "helper_type": ht, "helper_id": hid, "config": target})
            if exists:
                return await _execute_edit_helper({"helper_type": ht, "helper_id": hid, "config": target}, access_key, hass, data)
            from .tools.helper import _helper_create_mesa_entity, _enforce_helper_mesa_context
            create_args = {"helper_type": ht, "config": target, _HELPER_RESTORE_ID: hid}
            mesa_precheck = _enforce_helper_mesa_context(create_args, access_key, hass, data,
                [_helper_create_mesa_entity(ht, create_args, hass)], action="create",
                service_data={"helper_type": ht, "config": target, "flow_steps": None},
                session_id="create_helper_execute")
            if isinstance(mesa_precheck, tuple):
                return mesa_precheck
            rid = None
            try:
                async with data.config_recovery.resource(f"helper:{resource_id}"):
                    rid = await data.config_recovery.begin(f"helper:{resource_id}", "restore_helper", access_key.id, None,
                        source={"snapshot_type": "storage_helper", "helper_type": ht, "helper_id": hid, "config": target})
                    await data.config_recovery.mark(rid, "dispatching")
                    if await _resource_exists(hass, "helper", resource_id):
                        await data.config_recovery.mark(rid, "not_applied")
                        return _tool_error("Helper identity became occupied before restoration."), "invalid_request", "async_restore_version"
                    restored = await _execute_create_helper(
                        {"helper_type": ht, "config": target, _HELPER_RESTORE_ID: hid}, access_key, hass, data,
                    )
                    if restored[0].get("isError"):
                        await data.config_recovery.mark(rid, "unknown")
                    else:
                        body = json.loads(restored[0]["content"][0]["text"])
                        new_id = body["helper"]["id"]
                        observed = await _read_helper_config(hass, ht, new_id)
                        await data.config_recovery.mark(rid, "verified" if observed is not None else "partial", replacement_helper_id=new_id)
                        if observed is None:
                            return _tool_error(json.dumps({"status": "partial", "recovery_id": rid,
                                "replacement_helper_id": new_id, "retry_safe": False})), "invalid_request", "async_restore_version"
                        body.update(recovery_id=rid, status="verified", retry_safe=False)
                        restored = (_tool_success(json.dumps(body)), restored[1], restored[2])
                    return restored
            except Exception:
                return _tool_error("Helper restoration was not verified. Inspect configuration recovery before retrying."), "invalid_request", "async_restore_version"
            finally:
                if rid is not None:
                    data.config_recovery.leave(rid)
        if resource_type == "dashboard":
            # Dashboards are edit-only: re-apply the layout to the existing dashboard
            # (resource_id "lovelace" is the default dashboard, url_path None).
            return await _execute_set_dashboard_config(
                {"url_path": None if resource_id == "lovelace" else resource_id, "config": target},
                access_key, hass, data,
            )
        if resource_type == "config_entry":
            if target.get("snapshot_type") == "template_helper":
                from .template_recovery import restore_template
                return await restore_template(target, access_key, hass, data)
            if target.get("restorable") is False:
                return _tool_error(
                    "This integration version is a non-restorable audit record and cannot be restored. Phoenix "
                    "does not store raw integration data or provide automatic rollback."
                ), "invalid_request", "async_restore_version"
            if target.get("snapshot_type") == _CONFIG_ENTRY_METADATA_SNAPSHOT:
                fields = {
                    key: target[key]
                    for key in (
                        "title",
                        "pref_disable_new_entities",
                        "pref_disable_polling",
                        "disabled_by",
                    )
                    if key in target
                }
                return await _execute_set_integration(
                    {"entry_id": resource_id, **fields}, access_key, hass, data
                )
            # Re-runs the helper's own options flow with the snapshot, so HA
            # validates the restored settings exactly as it validated the write.
            # No expected_hash: a restore deliberately overwrites whatever is
            # there now, which is the whole point of choosing a snapshot.
            return await _execute_set_config_entry_options(
                {"entry_id": resource_id, "settings": target}, access_key, hass, data,
            )
        if resource_type == "energy":
            # Energy is restore-only and deliberately WHOLESALE: the snapshot IS
            # the operator's chosen state, so reproducing it is the operation. The
            # addressed-write rule edit_energy_config enforces does not apply here
            # (a YAML restore is likewise exempt from the dropped-key check).
            return await async_restore_energy_prefs(target, access_key, hass, data)
        if resource_type in ("yaml_config", "file", "esphome_yaml", "blueprint"):
            restorable = target.get("content")
            if not isinstance(restorable, str):
                # Oversized snapshots are stored as a non-restorable marker.
                return _tool_error("This version is too large to restore inline."), "invalid_request", "async_restore_version"
            if resource_type == "blueprint":
                # resource_id is "<domain>/<config-relative path>". Route through
                # the executors so the path jail, schema validation, and consumer
                # reload all apply; edit when the file exists, recreate otherwise.
                bp_domain, _, bp_rel = resource_id.partition("/")
                resolved = _resolve_blueprint_path(hass, bp_domain, bp_rel)
                bp_exists = (
                    resolved is not None
                    and await _read_blueprint_source(hass, resolved[0]) is not None
                )
                bp_args = {"domain": bp_domain, "path": bp_rel, "content": restorable}
                if bp_exists:
                    return await _execute_edit_blueprint(bp_args, access_key, hass, data)
                return await _execute_create_blueprint(bp_args, access_key, hass, data)
            if resource_type == "yaml_config":
                # resource_id is the config-relative path. Older records, written
                # before set_yaml_config took a file argument, all carry
                # "configuration.yaml", which resolves the same way, so they
                # restore unchanged.
                return await _execute_set_yaml_config(
                    {"file": resource_id, "content": restorable}, access_key, hass, data)
            if resource_type == "esphome_yaml":
                # Snapshots hold the RAW file, so a restore reproduces it exactly.
                # It routes through the executor, whose splice is a no-op on raw
                # content (no placeholders). The executor reads _restore_ctx (set
                # by the caller below) and waives the three LITERAL credential
                # refusals for this path only: raw text re-writes the file's inline
                # credentials verbatim, which those rules read as an agent changing
                # a frozen value, so without the waiver every restore of a file
                # with an inline credential would fail. See splice_esphome_text.
                return await _execute_set_esphome_yaml(
                    {"file": resource_id, "content": restorable}, access_key, hass, data)
            return await _execute_write_file({"path": resource_id, "content": restorable}, access_key, hass, data)
        if resource_type in {"area", "floor"}:
            return await restore_location_version(resource_type, resource_id, target, access_key, hass, data)
        if resource_type == "entity":
            # Re-apply the registry metadata. Restoring a deleted entry's snapshot
            # lands here too and fails cleanly in set_entity, because a deleted
            # registry entry cannot be recreated.
            fields = {
                key: target[key]
                for key in (
                    "name",
                    "icon",
                    "area_id",
                    "device_class",
                    "aliases",
                    "labels",
                    "categories",
                    "disabled_by",
                    "hidden_by",
                )
                if key in target
            }
            desired_entity_id = target.get("entity_id")
            candidate_ids: list[str] = []
            for snapshot in (record.before, record.after):
                if isinstance(snapshot, dict) and isinstance(
                    snapshot.get("entity_id"), str
                ):
                    candidate_ids.append(snapshot["entity_id"])
            candidate_ids.append(resource_id)
            current_entity_id = next(
                (
                    candidate
                    for candidate in candidate_ids
                    if er.async_get(hass).async_get(candidate) is not None
                ),
                resource_id,
            )
            if (
                isinstance(desired_entity_id, str)
                and desired_entity_id != current_entity_id
            ):
                fields["new_entity_id"] = desired_entity_id
            if not fields:
                return _tool_error("This version has no entity metadata to restore."), "invalid_request", "async_restore_version"
            return await _execute_set_entity(
                {"entity_id": current_entity_id, **fields}, access_key, hass, data
            )
        if resource_type == "device":
            if target.get("restorable") is False:
                return _tool_error(
                    "This device version is an audit record and cannot be restored automatically."
                ), "invalid_request", "async_restore_version"
            fields = {
                key: target[key]
                for key in ("name", "area_id", "labels", "disabled_by")
                if key in target
            }
            if not fields:
                return _tool_error(
                    "This version has no device metadata to restore."
                ), "invalid_request", "async_restore_version"
            return await _execute_set_device(
                {"device_id": resource_id, **fields}, access_key, hass, data
            )
        return _tool_error(f"Cannot restore resource type '{resource_type}'."), "invalid_request", "async_restore_version"
    finally:
        _restore_ctx.reset(ctx)
        recovery_source.reset(source_ctx)


class ExecutorNotRegistered(LookupError):
    """No executor is registered for a tool name.

    Distinct from a bare KeyError because the approve path treats "nothing could
    have run" as retryable and everything else as possibly applied. A KeyError
    raised INSIDE an executor (a dict lookup on a service response, a missing
    config key) would be indistinguishable from the lookup failing, and the
    caller would clear the durable marker and re-offer an approval whose side
    effect had already begun.
    """


async def async_execute_approved_tool(
    tool_name: str,
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Run the executor for a tool call that was gated and then approved.

    Refuses stored arguments that fail validation and an approval whose bound
    entity targets changed (TARGETS_CHANGED), then runs under confirm-approved
    MESA semantics. Raises ExecutorNotRegistered if no executor is registered
    for the tool_name, the ONLY failure here that proves nothing was dispatched.
    """
    if error := tool_arguments_error(args):
        return _tool_error(error), "invalid_request", tool_name
    fn = _EXECUTOR_REGISTRY.get(tool_name)
    if fn is None:
        raise ExecutorNotRegistered(f"No executor registered for tool {tool_name!r}")
    from .approval_targets import TARGETS_CHANGED, targets_current
    from .const import APPROVAL_TARGETS_KEY
    if APPROVAL_TARGETS_KEY in args:
        binding = args[APPROVAL_TARGETS_KEY]
        entities = binding.get("entity_ids") if isinstance(binding, dict) else None
        if (not isinstance(entities, list) or any(not isinstance(entity, str) for entity in entities)
                or not targets_current(args, entities, hass)):
            return _tool_error(TARGETS_CHANGED), "denied", tool_name
    # The admin's approval covers the whole action, so the MESA gate inside the
    # executor runs under confirm-approved semantics (see _approved_exec_ctx).
    ctx = _approved_exec_ctx.set(True)
    try:
        return await fn(args, access_key, hass, data)
    finally:
        _approved_exec_ctx.reset(ctx)


def _build_diff_restart_ha(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    return {
        "kind": "system_action",
        **_summary("restart_ha"),
        "target": {"type": "system", "id": "homeassistant", "label": "Home Assistant"},
        "preview": {
            "warning": "Home Assistant will restart and be briefly unavailable.",
        },
    }


# ---------------------------------------------------------------------------
# Capability summary (no capability required)
# ---------------------------------------------------------------------------


async def _tool_get_capability_summary(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: the access key introspecting its own caps/persona/limits. No cap required."""
    caps = effective_caps(access_key)
    body = {
        "access_key_name": access_key.name,
        "persona": access_key.persona,
        "pass_through": access_key.pass_through,
        "write_scope": access_key_has_write_scope(access_key),
        "capabilities": caps,
        "allowed": sorted(c for c, m in caps.items() if m == CAP_ALLOW),
        "confirm_gated": sorted(c for c, m in caps.items() if m == CAP_CONFIRM),
        "denied": sorted(c for c, m in caps.items() if m == CAP_DENY),
        "tools": _tool_gate_map(access_key, data, hass),
        "rate_limit": (
            {"requests_per_min": access_key.rate_limit_requests, "burst_per_sec": access_key.rate_limit_burst}
            if access_key.rate_limit_requests > 0 else "none"
        ),
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_capability_summary"


# ---------------------------------------------------------------------------
# Bounded subscription (cap_config_read): watch_entity
# ---------------------------------------------------------------------------


def _clamp_timeout(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = MAX_SUBSCRIPTION_SECONDS
    return max(1, min(seconds, MAX_SUBSCRIPTION_SECONDS))


_WATCH_TIMED_OUT = object()


async def _tool_watch_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: block until an accessible entity changes state, or until timeout."""
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "watch_entity"

    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "watch_entity"
    if resolve_read(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
        return _tool_error("Entity not found."), "not_found", entity_id

    until_state = args.get("until_state")
    if until_state is not None and not isinstance(until_state, str):
        return _tool_error("until_state must be a string."), "invalid_request", entity_id
    timeout = _clamp_timeout(args.get("timeout", MAX_SUBSCRIPTION_SECONDS))
    future: asyncio.Future = hass.loop.create_future()

    @callback
    def _on_change(event: Any) -> None:
        new_state = event.data.get("new_state")
        if until_state is not None and new_state is not None and new_state.state != until_state:
            return
        if not future.done():
            future.set_result(new_state)

    unsub = async_track_state_change_event(hass, [entity_id], _on_change)
    current = hass.states.get(entity_id)
    if until_state is not None and current is not None and current.state == until_state and not future.done():
        # The awaited state may already hold: an action verified after it landed.
        future.set_result(current)
    try:
        new_state = await asyncio.wait_for(future, timeout)
    except TimeoutError:
        new_state = _WATCH_TIMED_OUT
    finally:
        unsub()
    # Authority, scope and Assist exposure may have changed during the wait.
    check_request_authority()
    if resolve_read(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
        return _tool_error("Entity not found."), "not_found", entity_id
    if new_state is _WATCH_TIMED_OUT:
        timed_out: dict[str, Any] = {"entity_id": entity_id, "changed": False, "timeout_seconds": timeout}
        if until_state is not None:
            timed_out["reached"] = False
            timed_out["state"] = current_state.state if (current_state := hass.states.get(entity_id)) else None
        return _tool_success(json.dumps(timed_out)), "allowed", entity_id

    if new_state is None:
        body = {"entity_id": entity_id, "changed": True, "removed": True}
    else:
        scrubbed = scrub_sensitive_attributes(new_state)
        body = {
            "entity_id": entity_id,
            "changed": new_state is not current,
            "state": scrubbed.get("state"),
            "attributes": scrubbed.get("attributes"),
            "when": getattr(new_state, "last_changed", None),
        }
        if until_state is not None:
            body["reached"] = True
    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id













































































































































# ---------------------------------------------------------------------------
# Integration registry lifecycle (cap_integration_write)
# ---------------------------------------------------------------------------


_CONFIG_ENTRY_CONTEXT_FINGERPRINT = "_config_entry_context_fingerprint"
_CONFIG_ENTRY_METADATA_SNAPSHOT = "phoenix.config_entry.metadata.v1"
_CONFIG_ENTRY_DELETE_SNAPSHOT = "phoenix.config_entry.delete.v1"
_CONFIG_ENTRY_RECONFIGURE_SNAPSHOT = "phoenix.config_entry.reconfigure.v1"
_CONFIG_ENTRY_PRIVATE_IDENTITY_FINGERPRINT = "_config_entry_private_identity_fingerprint"
_CONFIG_ENTRY_STABLE_IDENTITY_FINGERPRINT = "_config_entry_stable_identity_fingerprint"


@dataclasses.dataclass
class _ConfigEntryActionDecision:
    """Aggregate one config-entry action over its exact entity membership."""

    decision: str
    actions: list[str]
    entities: list[dict[str, Any]]
    warnings: list[str]
    fingerprint: str
    blocked: list[tuple[str, str, str]]


@dataclasses.dataclass(frozen=True)
class _ConfigEntryPrivateIdentity:
    """Private config-entry identity used only for approval/apply binding."""

    binding_fingerprint: str
    stable_fingerprint: str
    entities: list[dict[str, Any]]
    devices: list[dict[str, Any]]
    cross_domain_coowners: list[dict[str, str]]
    same_domain_coowners: list[dict[str, str]]


def _identity_fingerprint(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _config_entry_private_identity(
    entry: Any, hass: HomeAssistant
) -> _ConfigEntryPrivateIdentity | None:
    """Capture private identity, registry membership and shared ownership.

    No value from this document is returned through MCP or the admin API. The
    approval stores only hashes; the human preview gets safe resource ids and
    co-owner domains separately.
    """
    context = config_entry_registry_context(entry.entry_id, hass)
    if context is None:
        return None
    if not isinstance(getattr(entry, "modified_at", None), datetime):
        return None
    entity_registry = er.async_get(hass)
    entities: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    same_domain: list[dict[str, str]] = []
    cross_domain: list[dict[str, str]] = []
    stable_anchor = bool(getattr(entry, "unique_id", None))

    for entity_id in context.entity_ids:
        registry_entry = entity_registry.async_get(entity_id)
        if registry_entry is None or registry_entry.config_entry_id != entry.entry_id:
            return None
        unique_id = getattr(registry_entry, "unique_id", None)
        stable_anchor = stable_anchor or bool(unique_id)
        entities.append({
            "entity_id": entity_id,
            "unique_id": unique_id,
            "platform": getattr(registry_entry, "platform", None),
            "device_id": getattr(registry_entry, "device_id", None),
        })

    for device_id in context.device_ids:
        device = device_registry_snapshot(hass).get(device_id)
        if device is None or entry.entry_id not in device_config_entry_ids(device):
            return None
        identifiers = sorted(
            (str(domain), str(identifier))
            for domain, identifier in getattr(device, "identifiers", set())
        )
        connections = sorted(
            (str(kind), str(value))
            for kind, value in getattr(device, "connections", set())
        )
        stable_anchor = stable_anchor or bool(identifiers or connections)
        owners: list[dict[str, str]] = []
        for owner_id in sorted(device_config_entry_ids(device)):
            owner = hass.config_entries.async_get_entry(owner_id)
            if owner is None:
                return None
            owner_doc = {"entry_id": owner_id, "domain": owner.domain}
            owners.append(owner_doc)
            if owner_id != entry.entry_id:
                (same_domain if owner.domain == entry.domain else cross_domain).append({
                    "device_id": device_id,
                    **owner_doc,
                })
        devices.append({
            "device_id": device_id,
            "identifiers": identifiers,
            "connections": connections,
            "owners": owners,
        })

    # An entry with no stable private identity cannot be distinguished from a
    # replacement that reused its public id. Reconfiguration therefore fails
    # closed instead of asking the operator to approve an ambiguous target.
    if not stable_anchor:
        return None
    stable_document = {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "unique_id": getattr(entry, "unique_id", None),
        "entities": entities,
        "devices": devices,
    }
    binding_document = {
        **stable_document,
        "modified_at": getattr(entry, "modified_at", None),
    }
    return _ConfigEntryPrivateIdentity(
        binding_fingerprint=_identity_fingerprint(binding_document),
        stable_fingerprint=_identity_fingerprint(stable_document),
        entities=entities,
        devices=devices,
        cross_domain_coowners=cross_domain,
        same_domain_coowners=same_domain,
    )


def _config_entry_value(value: Any) -> str | None:
    """Return a stable public string for a Home Assistant enum-like value."""
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


async def _config_entry_support(
    entry: Any, hass: HomeAssistant
) -> tuple[bool | None, bool | None, bool | None, bool | None]:
    """Probe safe config-entry support metadata without false negatives."""
    supports_unload: bool | None
    try:
        supports_unload = getattr(entry, "supports_unload", None)
        if supports_unload is None:
            supports_unload = await support_entry_unload(hass, entry.domain)
        else:
            supports_unload = bool(supports_unload)
    except Exception:  # integration loaders are third-party boundaries
        supports_unload = None

    def _optional_support(public_name: str, cache_name: str) -> bool | None:
        try:
            value = bool(getattr(entry, public_name))
            cached = getattr(entry, cache_name, None)
            # HA's property returns False when its handler has not been loaded.
            # That is unknown, not evidence that the feature is unsupported.
            return None if cached is None and not value else value
        except Exception:
            return None

    supports_options = _optional_support("supports_options", "_supports_options")
    supports_reconfigure = _optional_support(
        "supports_reconfigure", "_supports_reconfigure"
    )
    try:
        if entry.disabled_by is not None:
            supports_reload: bool | None = False
        elif entry.state is ConfigEntryState.LOADED:
            supports_reload = supports_unload
        elif entry.state in (
            ConfigEntryState.NOT_LOADED,
            ConfigEntryState.SETUP_ERROR,
            ConfigEntryState.SETUP_RETRY,
            ConfigEntryState.FAILED_UNLOAD,
        ):
            supports_reload = True
        else:
            supports_reload = False
    except Exception:
        supports_reload = None
    return supports_reload, supports_unload, supports_options, supports_reconfigure


def _config_entry_metadata_snapshot(entry: Any) -> dict[str, Any]:
    """Safe, discriminated config-entry metadata for version restoration."""
    return {
        "snapshot_type": _CONFIG_ENTRY_METADATA_SNAPSHOT,
        "title": entry.title,
        "pref_disable_new_entities": bool(entry.pref_disable_new_entities),
        "pref_disable_polling": bool(entry.pref_disable_polling),
        "disabled_by": _config_entry_value(entry.disabled_by),
    }


def _config_entry_write_error(
    entry_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    force_registry_only: bool = False,
) -> tuple[dict, str, str] | None:
    """Require WRITE on all entities and devices the entry owns, else not found."""
    permission = resolve_config_entry_registry_write(
        entry_id, access_key, hass, force_registry_only=force_registry_only
    )
    if permission == Permission.WRITE:
        return None
    return _tool_error("Integration not found."), "not_found", entry_id


def _config_entry_action_decision(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    entry: Any,
    *,
    actions: list[str],
    service_data: dict[str, Any],
    session_id: str,
) -> _ConfigEntryActionDecision | tuple[dict, str, str]:
    """Resolve MESA and pin entry state plus exact resource membership."""
    context = config_entry_registry_context(entry.entry_id, hass)
    if context is None:
        return _tool_error("Integration not found."), "not_found", entry.entry_id
    mesa_active = (
        (data.mesa is not None or data.mesa_setup_failed is True)
        and data.store.get_settings().mesa_mode != MESA_MODE_OFF
    )
    entity_ids = sorted(context.entity_ids)
    if mesa_active and actions and not entity_ids:
        reason = (
            "MESA is active and this integration has no registry entity from "
            "which mesa-core can resolve a complete config-entry context."
        )
        blocked = [(entry.entry_id, "mesa:unresolved_config_entry_context", reason)]
        entities: list[dict[str, Any]] = []
        decision_name = "deny"
        warnings: list[str] = []
    else:
        entities = []
        blocked = []
        warnings = []
        has_confirm = False
        try:
            for action in actions:
                for entity_id in entity_ids:
                    decision = evaluate_registry_action(
                        data,
                        access_key,
                        entity_id,
                        action=action,
                        registry_domain="config_entry",
                        service_data=service_data,
                        session_id=session_id,
                    )
                    explanation = None
                    if mesa_active and data.mesa is not None:
                        explanation = data.mesa.resolver.explain(entity_id).to_dict()
                    entities.append({
                        "entity_id": entity_id,
                        "action": action,
                        "decision": decision.decision,
                        "rule": decision.rule,
                        "reason": decision.reason,
                        "warnings": decision.warnings,
                        "effective_rule": decision.effective_rule,
                        "explanation": explanation,
                    })
                    for warning in decision.warnings:
                        if warning not in warnings:
                            warnings.append(warning)
                    if decision.decision == "deny":
                        blocked.append((entity_id, decision.rule, decision.reason))
                    elif decision.decision == "confirm":
                        has_confirm = True
        except Exception:  # noqa: BLE001 - safety resolution must fail closed
            _LOGGER.exception(
                "MESA config-entry evaluation failed for %s", entry.entry_id
            )
            return (
                _tool_error(
                    "MESA safety evaluation failed; no integration change was made."
                ),
                "denied",
                entry.entry_id,
            )
        decision_name = "deny" if blocked else "confirm" if has_confirm else "allow"

    fingerprint_doc = {
        "entry_id": entry.entry_id,
        "entry": _config_entry_metadata_snapshot(entry),
        "state": _config_entry_value(entry.state),
        "entity_ids": entity_ids,
        "device_ids": sorted(context.device_ids),
        "actions": actions,
        "entities": entities,
        "blocked": blocked,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_doc,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return _ConfigEntryActionDecision(
        decision=decision_name,
        actions=actions,
        entities=entities,
        warnings=warnings,
        fingerprint=fingerprint,
        blocked=blocked,
    )


def _config_entry_mesa_preview(
    decision: _ConfigEntryActionDecision,
) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "actions": decision.actions,
        "entities": decision.entities,
        "warnings": decision.warnings,
        "fingerprint": decision.fingerprint[:16],
    }


def _config_entry_mesa_error(
    entry_id: str, decision: _ConfigEntryActionDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(json.dumps({
            "error": "MESA blocked the integration action.",
            "entry_id": entry_id,
            "mesa": _config_entry_mesa_preview(decision),
            "blocked": [
                {"entity_id": eid, "rule": rule, "reason": reason}
                for eid, rule, reason in decision.blocked
            ],
        }, default=str)),
        "denied",
        entry_id,
    )


def _config_entry_context_changed_error(
    entry_id: str, decision: _ConfigEntryActionDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(json.dumps({
            "error": (
                "The integration's state, resource membership, permissions, or "
                "effective MESA profile changed after approval. Review it again."
            ),
            "entry_id": entry_id,
            "mesa": _config_entry_mesa_preview(decision),
        }, default=str)),
        "denied",
        entry_id,
    )


async def _integration_gate(
    *,
    tool_name: str,
    args: dict[str, Any],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    entry: Any,
    actions: list[str],
    service_data: dict[str, Any],
    request_id: str,
    client_ip: str | None,
    diff: Any,
    cap_name: str = "cap_integration_write",
    approval_bindings: dict[str, str] | None = None,
) -> tuple[dict, str, str] | None:
    """Merge capability and MESA confirmation into one pending approval."""
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=actions,
        service_data=service_data,
        session_id=request_id or tool_name,
    )
    if isinstance(resolved, tuple):
        return resolved
    if resolved.decision == "deny":
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry.entry_id, resolved)
    approval_args = dict(args)
    approval_args[_CONFIG_ENTRY_CONTEXT_FINGERPRINT] = resolved.fingerprint
    if approval_bindings:
        approval_args.update(approval_bindings)
    blocked = await _gate(
        cap_name,
        access_key,
        hass,
        data,
        tool_name=tool_name,
        args=approval_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda: diff(resolved),
    )
    if blocked is not None:
        return blocked
    if resolved.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name=tool_name,
            args=approval_args,
            diff=await diff(resolved),
            request_id=request_id,
            client_ip=client_ip,
            cap_name=cap_name,
        )
    return None


async def _tool_list_integrations(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list only config entries visible through owned resources."""
    if (
        effective_cap(access_key, "cap_integration_write") == CAP_DENY
        and effective_cap(access_key, "cap_integration_reconfigure") == CAP_DENY
    ):
        return _tool_error("Forbidden."), "denied", "list_integrations"
    domain_filter, query = args.get("domain"), args.get("query")
    if any(value is not None and not isinstance(value, str) for value in (domain_filter, query)):
        return _tool_error("domain and query must be strings."), "invalid_request", "list_integrations"
    domain_filter = (domain_filter or "").strip().lower()
    query = (query or "").strip().lower()
    integrations: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries():
        if domain_filter and entry.domain != domain_filter:
            continue
        access = resolve_config_entry_registry_access(entry.entry_id, access_key, hass)
        if access not in (Permission.READ, Permission.WRITE):
            continue
        context = config_entry_registry_context(entry.entry_id, hass)
        if context is None:
            continue
        expose = assist_expose_check(access_key, hass)
        accessible_entities = sum(
            (expose is None or expose(entity_id)) and
            resolve_registry_access(entity_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
            for entity_id in context.entity_ids
        )
        accessible_devices = sum(
            resolve_device_registry_access(device_id, access_key, hass)
            in (Permission.READ, Permission.WRITE)
            for device_id in context.device_ids
        )
        (
            supports_reload,
            supports_unload,
            supports_options,
            supports_reconfigure,
        ) = await _config_entry_support(entry, hass)
        reason = getattr(entry, "reason", None)
        integrations.append({
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            # Titles are user/integration supplied and commonly contain an IP,
            # hostname URL, or both.  Keep the public title field useful while
            # applying the same network-topology scrub as setup diagnostics.
            "title": redact_diagnostics(entry.title),
            "source": _config_entry_value(entry.source),
            "state": _config_entry_value(entry.state),
            "enabled": entry.disabled_by is None,
            "disabled_by": _config_entry_value(entry.disabled_by),
            "setup_failure_reason": (
                redact_diagnostics(str(reason)) if reason else None
            ),
            "supports_reload": supports_reload,
            "supports_unload": supports_unload,
            "supports_options": supports_options,
            "supports_reconfigure": supports_reconfigure,
            "pref_disable_new_entities": bool(entry.pref_disable_new_entities),
            "pref_disable_polling": bool(entry.pref_disable_polling),
            "accessible_entity_count": accessible_entities,
            "accessible_device_count": accessible_devices,
        })
    if query:
        integrations = [row for row in integrations
                        if query in f"{row['domain']} {row['title'] or ''}".lower()]
    integrations.sort(key=lambda e: (e["domain"], e["title"] or ""))
    paged = offset_page(integrations, args, default_limit=INTEGRATION_LIST_DEFAULT_LIMIT, max_limit=INTEGRATION_LIST_MAX_LIMIT)
    if isinstance(paged, str):
        return _tool_error(paged), "invalid_request", "list_integrations"
    page, envelope = paged
    return _tool_success(json.dumps({**envelope, "integrations": page}, default=str)), "allowed", "list_integrations"


def _reconfigure_args_error(args: dict[str, Any]) -> str | None:
    entry_id = args.get("entry_id")
    config = args.get("config")
    menu_choices = args.get("menu_choices", [])
    if not isinstance(entry_id, str) or not entry_id.strip():
        return "entry_id is required."
    if not isinstance(config, dict):
        return "config must be an object."
    if any(not isinstance(key, str) or not key for key in config):
        return "config field names must be non-empty strings."
    if not isinstance(menu_choices, list) or any(
        not isinstance(choice, str) or not choice for choice in menu_choices
    ):
        return "menu_choices must be an array of non-empty strings."
    if len(menu_choices) > MENU_CHOICE_BUDGET:
        return f"menu_choices supports at most {MENU_CHOICE_BUDGET} choices."
    return None


def _reconfigure_identity_error(entry_id: str) -> tuple[dict, str, str]:
    return (
        _tool_error(
            "Phoenix could not establish a stable private identity for this integration; "
            "no reconfigure flow was started. Use Home Assistant's frontend."
        ),
        "denied",
        entry_id,
    )


def _reconfigure_shared_owner_error(
    entry_id: str, identity: _ConfigEntryPrivateIdentity
) -> tuple[dict, str, str]:
    return (
        _tool_error(json.dumps({
            "status": "flow_aborted_before_apply",
            "error": (
                "A device is shared with another config entry from the same integration "
                "domain, so Phoenix cannot prove which entry the flow will reconfigure. "
                "Use Home Assistant's frontend."
            ),
            "same_domain_shared_ownership": identity.same_domain_coowners,
            "retry_safe": True,
        }, default=str)),
        "denied",
        entry_id,
    )


def _reconfigure_version_snapshot(
    entry: Any, args: dict[str, Any], status: str
) -> dict[str, Any]:
    """Redacted audit evidence; deliberately insufficient for restoration."""
    raw_config = args.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    return {
        "snapshot_type": _CONFIG_ENTRY_RECONFIGURE_SNAPSHOT,
        "restorable": False,
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "submitted_fields": sorted(config),
        "submitted_config": redact_structure(config),
        "menu_choice_count": len(args.get("menu_choices") or []),
        "status": status,
        "automatic_rollback": False,
    }


async def _build_diff_reconfigure_integration(
    args: dict[str, Any],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    identity = _config_entry_private_identity(entry, hass) if entry is not None else None
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    raw_config = args.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    preview: dict[str, Any] = {
        "submitted_fields": sorted(config),
        "submitted_config": redact_structure(config),
        "menu_choices": list(args.get("menu_choices") or []),
        "validation_state": "not_yet_validated_by_integration",
        "operator_editable": False,
        "unsupported_steps": ["external", "oauth", "progress"],
        "automatic_rollback": False,
    }
    if entry is not None:
        preview["integration"] = {"domain": entry.domain, "name": label}
    if identity is not None:
        preview["affected_entities"] = [
            {"entity_id": item["entity_id"], "device_id": item["device_id"]}
            for item in identity.entities
        ]
        preview["affected_devices"] = [
            {
                "device_id": item["device_id"],
                "owners": item["owners"],
            }
            for item in identity.devices
        ]
        preview["cross_domain_coowners"] = identity.cross_domain_coowners
        preview["identity_binding"] = "private_identity_and_registry_context_pinned"
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision)
    return {
        "kind": "system_action",
        **_summary("integration.reconfigure", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        # There is intentionally no before/after config-entry data. The
        # integration has not validated the proposed values at review time.
        "preview": preview,
    }


async def _tool_reconfigure_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Review agent values, then run only HA's official reconfigure flow."""
    if effective_cap(access_key, "cap_integration_reconfigure") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "reconfigure_integration"
    value_error = _reconfigure_args_error(args)
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", "reconfigure_integration"
    entry_id = str(args["entry_id"]).strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    identity = _config_entry_private_identity(entry, hass)
    if identity is None:
        return _reconfigure_identity_error(entry_id)
    if identity.same_domain_coowners:
        return _reconfigure_shared_owner_error(entry_id, identity)
    _, _, _, supports_reconfigure = await _config_entry_support(entry, hass)
    if supports_reconfigure is False:
        return (
            _tool_error(
                "This integration does not expose Home Assistant's official reconfigure flow. "
                "Use Home Assistant's frontend if it offers another configuration path."
            ),
            "invalid_request",
            entry_id,
        )
    blocked = await _integration_gate(
        tool_name="reconfigure_integration",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=["config_entry.reconfigure"],
        service_data={"submitted_fields": sorted(args["config"])},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_reconfigure_integration(
            args, access_key, hass, decision
        ),
        cap_name="cap_integration_reconfigure",
        approval_bindings={
            _CONFIG_ENTRY_PRIVATE_IDENTITY_FINGERPRINT: identity.binding_fingerprint,
            _CONFIG_ENTRY_STABLE_IDENTITY_FINGERPRINT: identity.stable_fingerprint,
        },
    )
    if blocked is not None:
        return blocked
    return await _execute_reconfigure_integration(args, access_key, hass, data)


async def _execute_reconfigure_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate approval context, execute, classify, and audit one flow."""
    value_error = _reconfigure_args_error(args)
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", "reconfigure_integration"
    entry_id = str(args["entry_id"]).strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    identity = _config_entry_private_identity(entry, hass)
    if identity is None:
        return _reconfigure_identity_error(entry_id)
    if identity.same_domain_coowners:
        return _reconfigure_shared_owner_error(entry_id, identity)

    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=["config_entry.reconfigure"],
        service_data={"submitted_fields": sorted(args["config"])},
        session_id="reconfigure_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    approved = bool(_approved_exec_ctx.get())
    saved_context = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    saved_binding = args.get(_CONFIG_ENTRY_PRIVATE_IDENTITY_FINGERPRINT)
    saved_stable = args.get(_CONFIG_ENTRY_STABLE_IDENTITY_FINGERPRINT)
    if approved and (
        saved_context != resolved.fingerprint
        or saved_binding != identity.binding_fingerprint
        or saved_stable != identity.stable_fingerprint
    ):
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (resolved.decision == "confirm" and not approved):
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry_id, resolved)

    flow_result = await async_run_reconfigure_flow(
        hass,
        entry,
        args["config"],
        list(args.get("menu_choices") or []),
    )
    status = flow_result.status
    if flow_result.applied:
        current = hass.config_entries.async_get_entry(entry_id)
        post_identity = (
            _config_entry_private_identity(current, hass) if current is not None else None
        )
        if post_identity is None or post_identity.stable_fingerprint != identity.stable_fingerprint:
            status = "applied_identity_mismatch"

    body: dict[str, Any] = {
        "status": status,
        "entry_id": entry_id,
        "domain": entry.domain,
        "applied": flow_result.applied,
        "retry_safe": not flow_result.applied,
        "reason": flow_result.reason,
        "details": redact_structure(flow_result.details),
        "automatic_rollback": False,
    }
    if identity.cross_domain_coowners:
        body["cross_domain_coowners"] = identity.cross_domain_coowners
        body["warning"] = (
            "One or more affected devices also belong to another integration domain."
        )
    if resolved.warnings:
        body["mesa_advisory"] = resolved.warnings
        _mesa_advisory_ctx.set(True)
    if flow_result.applied:
        body["retry_warning"] = (
            "Do not automatically retry any applied_* result. Inspect Home Assistant "
            "and recover manually in its frontend if needed."
        )
        current = hass.config_entries.async_get_entry(entry_id) or entry
        await _record_version(
            data,
            access_key,
            resource_type="config_entry",
            resource_id=entry_id,
            action="edit",
            before=None,
            after=_reconfigure_version_snapshot(current, args, status),
            alias=redact_diagnostics(entry.title),
            summary=_version_summary(
                "config_entry.reconfigure", subject=redact_diagnostics(entry.title)
            ),
        )
        return (
            _tool_success(json.dumps(body, default=str)),
            "allowed",
            f"integration:{entry_id}",
        )
    return (
        _tool_error(json.dumps(body, default=str)),
        "denied" if status == RECONFIGURE_APPLY_FAILED else "invalid_request",
        entry_id,
    )


async def _tool_set_integration_enabled(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: enable or disable an integration (needs WRITE on all it owns)."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration_enabled"
    entry_id = str(args.get("entry_id") or "").strip()
    enabled = args.get("enabled")
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "set_integration_enabled"
    if not isinstance(enabled, bool):
        return _tool_error("enabled must be a boolean."), "invalid_request", "set_integration_enabled"
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(
        entry_id, access_key, hass, force_registry_only=not enabled
    )
    if perm_error is not None:
        return perm_error
    if entry.disabled_by not in (None, ConfigEntryDisabler.USER):
        return (
            _tool_error(
                f"This integration is disabled by {_config_entry_value(entry.disabled_by)}, not by the user."
            ),
            "denied",
            entry_id,
        )
    if (entry.disabled_by is None) == enabled:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "enabled": enabled,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"
    action = "enable" if enabled else "disable"
    blocked = await _integration_gate(
        tool_name="set_integration_enabled",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=[action],
        service_data={"enabled": enabled},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_set_integration_enabled(
            args, access_key, hass, decision
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_integration_enabled(args, access_key, hass, data)


async def _execute_set_integration_enabled(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    entry_id = str(args.get("entry_id") or "").strip()
    enabled = args.get("enabled")
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "set_integration_enabled"
    if not isinstance(enabled, bool):
        return _tool_error("enabled must be a boolean."), "invalid_request", "set_integration_enabled"
    entry = hass.config_entries.async_get_entry(entry_id)
    # Phoenix MCP's own entry is never a valid target (no self-lockout); treat as not found.
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(
        entry_id, access_key, hass, force_registry_only=not enabled
    )
    if perm_error is not None:
        return perm_error
    if entry.disabled_by not in (None, ConfigEntryDisabler.USER):
        return (
            _tool_error(
                f"This integration is disabled by {_config_entry_value(entry.disabled_by)}, not by the user."
            ),
            "denied",
            entry_id,
        )
    if (entry.disabled_by is None) == enabled:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "enabled": enabled,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"

    action = "enable" if enabled else "disable"
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=[action],
        service_data={"enabled": enabled},
        session_id="set_integration_enabled_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not approved_same
    ):
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry_id, resolved)
    before = _config_entry_metadata_snapshot(entry)
    try:
        reload_succeeded = await hass.config_entries.async_set_disabled_by(
            entry_id, None if enabled else ConfigEntryDisabler.USER
        )
    except Exception as exc:  # noqa: BLE001 - OperationNotAllowed etc. -> clean error
        _LOGGER.error("set_integration_enabled failed: %s", exc)
        return _tool_error("Failed to change integration state."), "denied", entry_id
    updated = hass.config_entries.async_get_entry(entry_id)
    if updated is None:
        return _tool_error("Integration not found after state change."), "denied", entry_id
    after = _config_entry_metadata_snapshot(updated)
    changed = before != after
    if changed:
        await _record_version(
            data,
            access_key,
            resource_type="config_entry",
            resource_id=entry_id,
            action="edit",
            before=before,
            after=after,
            alias=redact_diagnostics(updated.title),
        )
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "domain": updated.domain,
        "requested_enabled": enabled,
        "enabled": updated.disabled_by is None,
        "changed": changed,
        "reload_succeeded": bool(reload_succeeded),
        "requires_restart": not bool(reload_succeeded),
    }
    if resolved.warnings:
        body["mesa_advisory"] = resolved.warnings
        _mesa_advisory_ctx.set(True)
    return _tool_success(json.dumps(body, default=str)), "allowed", f"integration:{entry_id}"


def _set_integration_args_error(args: dict[str, Any]) -> str | None:
    provided = [
        field
        for field in ("title", "pref_disable_new_entities", "pref_disable_polling")
        if field in args
    ]
    if not provided:
        return "Provide at least one integration metadata update."
    if "title" in args and not isinstance(args["title"], str):
        return "title must be a string."
    if isinstance(args.get("title"), str) and not args["title"].strip():
        return "title must not be empty."
    for field in ("pref_disable_new_entities", "pref_disable_polling"):
        if field in args and not isinstance(args[field], bool):
            return f"{field} must be a boolean."
    return None


def _set_integration_changes(entry: Any, args: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "title" in args and args["title"] != entry.title:
        changes["title"] = args["title"]
    for field in ("pref_disable_new_entities", "pref_disable_polling"):
        if field in args and args[field] != bool(getattr(entry, field)):
            changes[field] = args[field]
    return changes


async def _tool_set_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_integration"
    args, error = normalize_tool_args("set_integration", args)
    if error:
        return _tool_error(error), "invalid_request", "set_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "set_integration"
    value_error = _set_integration_args_error(args)
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", entry_id
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    changes = _set_integration_changes(entry, args)
    if not changes:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"
    actions = ["rename"] if "title" in changes else []
    blocked = await _integration_gate(
        tool_name="set_integration",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=actions,
        service_data={key: value for key, value in changes.items() if key == "title"},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_set_integration(args, access_key, hass, decision),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_integration(args, access_key, hass, data)


async def _execute_set_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    restoring = _restore_ctx.get() is not None
    if not restoring:
        value_error = _set_integration_args_error(args)
        if value_error is not None:
            return _tool_error(value_error), "invalid_request", entry_id
    changes = _set_integration_changes(entry, args)
    if restoring and "disabled_by" in args:
        stored_disabled_by = args["disabled_by"]
        if stored_disabled_by not in (None, ConfigEntryDisabler.USER.value):
            return _tool_error("Stored disabled state is not user-controlled."), "denied", entry_id
        desired_enabled = stored_disabled_by is None
        if (entry.disabled_by is None) != desired_enabled:
            changes["disabled_by"] = stored_disabled_by
    if not changes:
        return _tool_success(json.dumps({
            "entry_id": entry_id,
            "changed": False,
        })), "allowed", f"integration:{entry_id}"

    actions: list[str] = []
    if "title" in changes:
        actions.append("rename")
    if "disabled_by" in changes:
        actions.append("enable" if changes["disabled_by"] is None else "disable")
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=actions,
        service_data={key: value for key, value in changes.items() if key in ("title", "disabled_by")},
        session_id="set_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    # The Changes-tab restore is itself an explicit, authenticated admin
    # confirmation and runs synchronously after this fresh MESA resolution.
    # It may satisfy only `confirm`; deny verdicts (including read_only and
    # enforced prohibited) still fail below.  Normal MCP execution continues to
    # require the exact approval fingerprint captured in its preview.
    mesa_confirmed = approved_same or restoring
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not mesa_confirmed
    ):
        return _config_entry_mesa_error(entry_id, resolved)

    before = _config_entry_metadata_snapshot(entry)
    update_values = {
        key: changes[key]
        for key in ("title", "pref_disable_new_entities", "pref_disable_polling")
        if key in changes
    }
    try:
        if update_values:
            hass.config_entries.async_update_entry(entry, **update_values)
        reload_attempted = False
        reload_succeeded: bool | None = None
        if "disabled_by" in changes:
            reload_attempted = True
            reload_succeeded = await hass.config_entries.async_set_disabled_by(
                entry_id,
                None if changes["disabled_by"] is None else ConfigEntryDisabler.USER,
            )
        elif "pref_disable_polling" in changes and entry.state is ConfigEntryState.LOADED:
            reload_attempted = True
            reload_succeeded = await hass.config_entries.async_reload(entry_id)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("set_integration failed for %s: %s", entry_id, exc)
        return _tool_error("Failed to update integration."), "denied", entry_id
    updated = hass.config_entries.async_get_entry(entry_id)
    if updated is None:
        return _tool_error("Integration not found after update."), "denied", entry_id
    after = _config_entry_metadata_snapshot(updated)
    await _record_version(
        data,
        access_key,
        resource_type="config_entry",
        resource_id=entry_id,
        action="edit",
        before=before,
        after=after,
        alias=redact_diagnostics(updated.title),
    )
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "changed": before != after,
        "updated": {
            "title": redact_diagnostics(updated.title),
            "pref_disable_new_entities": bool(updated.pref_disable_new_entities),
            "pref_disable_polling": bool(updated.pref_disable_polling),
            "enabled": updated.disabled_by is None,
        },
        "reload_attempted": reload_attempted,
        "reload_succeeded": reload_succeeded,
        "requires_restart": reload_attempted and reload_succeeded is False,
    }
    if resolved.warnings:
        body["mesa_advisory"] = resolved.warnings
        _mesa_advisory_ctx.set(True)
    return _tool_success(json.dumps(body, default=str)), "allowed", f"integration:{entry_id}"


async def _tool_reload_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "reload_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "reload_integration"
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    support_error = await _reload_integration_support_error(entry, hass)
    if support_error is not None:
        return _tool_error(support_error), "denied", entry_id
    blocked = await _integration_gate(
        tool_name="reload_integration",
        args=args,
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=["reload"],
        service_data={},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_reload_integration(args, access_key, hass, decision),
    )
    if blocked is not None:
        return blocked
    return await _execute_reload_integration(args, access_key, hass, data)


async def _reload_integration_support_error(entry: Any, hass: HomeAssistant) -> str | None:
    if entry.disabled_by is not None:
        return "Disabled integrations cannot be reloaded."
    if entry.state in (
        ConfigEntryState.MIGRATION_ERROR,
        ConfigEntryState.SETUP_IN_PROGRESS,
        ConfigEntryState.UNLOAD_IN_PROGRESS,
    ):
        return f"Integration state {_config_entry_value(entry.state)} is not reloadable."
    supports_reload, _unload, _options, _reconfigure = await _config_entry_support(entry, hass)
    if supports_reload is not True:
        return "This integration does not currently support reload."
    return None


async def _execute_reload_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    support_error = await _reload_integration_support_error(entry, hass)
    if support_error is not None:
        return _tool_error(support_error), "denied", entry_id
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=["reload"],
        service_data={},
        session_id="reload_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not approved_same
    ):
        return _config_entry_mesa_error(entry_id, resolved)
    try:
        reloaded = await hass.config_entries.async_reload(entry_id)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("reload_integration failed for %s: %s", entry_id, exc)
        return _tool_error("Failed to reload integration."), "denied", entry_id
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "reloaded": bool(reloaded),
        "requires_restart": not bool(reloaded),
        "state": _config_entry_value(
            getattr(hass.config_entries.async_get_entry(entry_id), "state", None)
        ),
    }
    if resolved.warnings:
        body["mesa_advisory"] = resolved.warnings
        _mesa_advisory_ctx.set(True)
    return _tool_success(json.dumps(body, default=str)), "allowed", f"integration:{entry_id}"


def _config_entry_permission_references(
    data: PhoenixData,
    *,
    entity_ids: list[str],
    device_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Permission nodes which will outlive removal as stale identities."""
    entity_references: list[dict[str, Any]] = []
    device_references: list[dict[str, Any]] = []
    entity_set = set(entity_ids)
    device_set = set(device_ids)
    for record in data.store.list_access_keys():
        if not record.is_valid():
            continue
        for entity_id in sorted(entity_set.intersection(record.permissions.entities)):
            entity_references.append({
                "entity_id": entity_id,
                "kind": "access_key_permission",
                "access_key_id": record.id,
                "access_key_name": record.name,
            })
        for device_id in sorted(device_set.intersection(record.permissions.devices)):
            device_references.append({
                "device_id": device_id,
                "kind": "access_key_permission",
                "access_key_id": record.id,
                "access_key_name": record.name,
            })
        for preset in record.presets:
            for entity_id in sorted(entity_set.intersection(preset.permissions.entities)):
                entity_references.append({
                    "entity_id": entity_id,
                    "kind": "preset_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                    "preset_id": preset.id,
                    "preset_name": preset.name,
                })
            for device_id in sorted(device_set.intersection(preset.permissions.devices)):
                device_references.append({
                    "device_id": device_id,
                    "kind": "preset_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                    "preset_id": preset.id,
                    "preset_name": preset.name,
                })
    return {"entities": entity_references, "devices": device_references}


def _identity_paths(
    value: Any,
    identities: set[str],
    *,
    path: str = "settings",
) -> list[str]:
    """Return paths, never values, for exact identities in Phoenix settings."""
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in identities:
                matches.append(child_path)
            matches.extend(_identity_paths(item, identities, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(
                _identity_paths(item, identities, path=f"{path}[{index}]")
            )
    elif value in identities:
        matches.append(path)
    return matches


def _config_entry_pending_references(
    data: PhoenixData, identities: set[str]
) -> list[dict[str, Any]]:
    """Pending Phoenix actions keyed to the entry or one affected resource."""
    references: list[dict[str, Any]] = []
    for pending in data.store.get_pending_approvals():
        if pending.get("status") != "pending":
            continue
        if pending.get("id") in data.approvals_in_progress:
            continue
        args = pending.get("args", {})
        if any(_contains_entity_identity(args, identity) for identity in identities):
            references.append({
                "approval_id": pending.get("id"),
                "tool_name": pending.get("tool_name"),
            })
    return references


def _config_entry_safe_owner_summary(entry: Any) -> dict[str, Any]:
    """Allowlisted config-entry identity for shared-device reporting."""
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "disabled_by": _config_entry_value(entry.disabled_by),
    }


def _stored_profile_document(profile: Any | None) -> dict[str, Any] | None:
    return profile.to_dict() if profile is not None else None


async def _build_diff_remove_integration(
    args: dict[str, Any],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    """Build the exact removal and surviving-identity preview."""
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    context = config_entry_registry_context(entry_id, hass)
    entity_ids = sorted(context.entity_ids) if context is not None else []
    device_ids = sorted(context.device_ids) if context is not None else []
    affected_devices: list[dict[str, Any]] = []
    shared_devices: list[dict[str, Any]] = []
    for device_id in device_ids:
        device = device_registry_snapshot(hass).get(device_id)
        if device is None:
            continue
        remaining_owners = []
        for owner_id in device_config_entry_ids(device):
            if owner_id == entry_id:
                continue
            owner = hass.config_entries.async_get_entry(owner_id)
            if owner is not None:
                remaining_owners.append(_config_entry_safe_owner_summary(owner))
        device_summary = {
            "device_id": device_id,
            "name": redact_diagnostics(device.name_by_user or device.name or device_id),
            "shared": bool(remaining_owners),
            "remaining_owners": remaining_owners,
        }
        affected_devices.append(device_summary)
        if remaining_owners:
            shared_devices.append(device_summary)

    hints = data.store.get_entity_hints()
    mesa_profiles: dict[str, Any] = {
        "entities": [],
        "devices": [],
        "integration": None,
    }
    if data.mesa is not None:
        for entity_id in entity_ids:
            stored = data.mesa.store.get(entity_id)
            if stored is not None:
                mesa_profiles["entities"].append({
                    "entity_id": entity_id,
                    "profile": _stored_profile_document(stored),
                })
        for device_id in device_ids:
            stored = data.mesa.store.get_device_profile(device_id)
            if stored is not None:
                mesa_profiles["devices"].append({
                    "device_id": device_id,
                    "profile": _stored_profile_document(stored),
                })
        if entry is not None:
            mesa_profiles["integration"] = _stored_profile_document(
                data.mesa.store.get_integration_profile(entry.domain)
            )

    identities = {entry_id, *entity_ids, *device_ids}
    settings = data.store.get_settings().to_dict()
    preview: dict[str, Any] = {
        "entry": (
            _config_entry_safe_owner_summary(entry) if entry is not None else None
        ),
        "affected_entities": entity_ids,
        "affected_devices": affected_devices,
        "relationships": await _registry_relationships_preview(hass, entity_ids),
        "shared_devices": shared_devices,
        "permission_references": _config_entry_permission_references(
            data, entity_ids=entity_ids, device_ids=device_ids
        ),
        "global_hints": [entity_id for entity_id in entity_ids if entity_id in hints],
        "mesa_profiles": mesa_profiles,
        "pending_approvals": _config_entry_pending_references(data, identities),
        "other_phoenix_identity_paths": sorted(set(_identity_paths(settings, identities))),
        "warning": (
            "Home Assistant will remove this config entry through its integration-aware "
            "cleanup. Known consumers and Phoenix identity-keyed configuration are not "
            "rewritten, MESA profiles are not deleted, and this cannot be restored."
        ),
    }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    return {
        "kind": "system_action",
        **_summary("integration.remove", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }


def _config_entry_delete_snapshot(entry: Any, context: Any) -> dict[str, Any]:
    """Safe, intentionally non-restorable config-entry removal record."""
    return {
        "snapshot_type": _CONFIG_ENTRY_DELETE_SNAPSHOT,
        "restorable": False,
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "source": _config_entry_value(entry.source),
        "state": _config_entry_value(entry.state),
        "disabled_by": _config_entry_value(entry.disabled_by),
        "pref_disable_new_entities": bool(entry.pref_disable_new_entities),
        "pref_disable_polling": bool(entry.pref_disable_polling),
        "entity_ids": sorted(context.entity_ids),
        "device_ids": sorted(context.device_ids),
    }


def _config_entry_removal_observation(
    entry_id: str,
    before_entity_ids: list[str],
    before_device_ids: list[str],
    hass: HomeAssistant,
) -> dict[str, Any]:
    """Observe cleanup after HA returns or raises; never infer rollback."""
    entity_registry = er.async_get(hass)
    remaining_entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.entities.values()
        if registry_entry.config_entry_id == entry_id
    )
    removed_entity_ids = sorted(set(before_entity_ids) - set(remaining_entity_ids))
    removed_device_ids: list[str] = []
    remaining_devices: list[dict[str, Any]] = []
    ownership_anomalies: list[str] = []
    for device_id in before_device_ids:
        device = device_registry_snapshot(hass).get(device_id)
        if device is None:
            removed_device_ids.append(device_id)
            continue
        owner_ids = device_config_entry_ids(device)
        if entry_id in owner_ids:
            ownership_anomalies.append(device_id)
        owners = []
        for owner_id in owner_ids:
            owner = hass.config_entries.async_get_entry(owner_id)
            if owner is not None:
                owners.append(_config_entry_safe_owner_summary(owner))
        remaining_devices.append({
            "device_id": device_id,
            "remaining_owners": owners,
        })
    return {
        "entry_removed": hass.config_entries.async_get_entry(entry_id) is None,
        "entities_before": before_entity_ids,
        "entities_removed": removed_entity_ids,
        "entities_remaining": remaining_entity_ids,
        "devices_before": before_device_ids,
        "devices_removed": removed_device_ids,
        "devices_remaining": remaining_devices,
        "device_ownership_anomalies": ownership_anomalies,
    }


async def _tool_remove_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Remove one config entry only through Home Assistant's manager."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    if not entry_id:
        return _tool_error("entry_id is required."), "invalid_request", "remove_integration"
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    blocked = await _integration_gate(
        tool_name="remove_integration",
        args={"entry_id": entry_id},
        access_key=access_key,
        hass=hass,
        data=data,
        entry=entry,
        actions=["remove"],
        service_data={},
        request_id=request_id,
        client_ip=client_ip,
        diff=lambda decision: _build_diff_remove_integration(
            args, access_key, hass, data, decision
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_remove_integration(args, access_key, hass, data)


async def _execute_remove_integration(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Use the same protected lifecycle for Template removal on the generic path."""
    from .config_recovery import ConfigurationRecoveryError
    from .template_recovery import protect_template

    entry = hass.config_entries.async_get_entry(str(args.get("entry_id") or ""))
    if entry is None or entry.domain != "template":
        return await _apply_remove_integration(args, access_key, hass, data)
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_integration"
    perm_error = _config_entry_write_error(entry.entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    resolved = _config_entry_action_decision(data, access_key, hass, entry, actions=["remove"],
        service_data={}, session_id="remove_integration_execute")
    if isinstance(resolved, tuple):
        return resolved
    approved = _approved_exec_ctx.get() and args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT) == resolved.fingerprint
    if resolved.decision == "deny" or resolved.decision == "confirm" and not approved:
        return _config_entry_mesa_error(entry.entry_id, resolved)
    rid = None
    try:
        async with protect_template(hass, data, access_key, entry, "remove_integration") as rid:
            result = await _apply_remove_integration(args, access_key, hass, data)
            removed = hass.config_entries.async_get_entry(entry.entry_id) is None
            remaining = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            await data.config_recovery.mark(rid, "verified" if removed and not remaining else "partial" if removed else "unknown", entry_removed=removed, remaining_entities=len(remaining))
            if removed and not result[0].get("isError"):
                body = json.loads(result[0]["content"][0]["text"])
                body["recovery_id"] = rid
                result = (_tool_success(json.dumps(body, default=str)), result[1], result[2])
            return result
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else "Configuration recovery failed; inspect Home Assistant before retrying."
        return _tool_error(json.dumps({"error": message, "recovery_id": rid,
            "status": "unknown" if rid else "not_applied", "retry_safe": False})), "invalid_request", "remove_integration"


async def _apply_remove_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate, remove through HA, then report the observed cleanup."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_integration"
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain == DOMAIN:
        return _tool_error("Integration not found."), "not_found", entry_id
    perm_error = _config_entry_write_error(entry_id, access_key, hass)
    if perm_error is not None:
        return perm_error
    context = config_entry_registry_context(entry_id, hass)
    if context is None:
        return _tool_error("Integration not found."), "not_found", entry_id
    resolved = _config_entry_action_decision(
        data,
        access_key,
        hass,
        entry,
        actions=["remove"],
        service_data={},
        session_id="remove_integration_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    saved_fingerprint = args.get(_CONFIG_ENTRY_CONTEXT_FINGERPRINT)
    approved_same = (
        _approved_exec_ctx.get()
        and isinstance(saved_fingerprint, str)
        and saved_fingerprint == resolved.fingerprint
    )
    if _approved_exec_ctx.get() and isinstance(saved_fingerprint, str) and not approved_same:
        return _config_entry_context_changed_error(entry_id, resolved)
    if resolved.decision == "deny" or (
        resolved.decision == "confirm" and not approved_same
    ):
        if resolved.blocked:
            fire_mesa_blocked_event(hass, access_key, resolved.blocked)
        return _config_entry_mesa_error(entry_id, resolved)

    before_entity_ids = sorted(context.entity_ids)
    before_device_ids = sorted(context.device_ids)
    from .template_recovery import template_snapshot
    version_before = (template_snapshot(hass, entry) if entry.domain == "template"
                      else _config_entry_delete_snapshot(entry, context))
    remove_result: dict[str, Any] = {}
    remove_raised = False
    try:
        raw_result = await hass.config_entries.async_remove(entry_id)
        if isinstance(raw_result, dict):
            remove_result = raw_result
    except Exception as exc:  # noqa: BLE001 - HA/integration removal boundary
        remove_raised = True
        _LOGGER.error("remove_integration failed for %s: %s", entry_id, exc)

    observed = _config_entry_removal_observation(
        entry_id, before_entity_ids, before_device_ids, hass
    )
    body: dict[str, Any] = {
        "entry_id": entry_id,
        "domain": entry.domain,
        "removed": observed["entry_removed"],
        "require_restart": remove_result.get("require_restart"),
        "home_assistant_raised": remove_raised,
        "observed_cleanup": observed,
    }
    if resolved.warnings:
        body["mesa_advisory"] = resolved.warnings
        _mesa_advisory_ctx.set(True)
    if observed["entry_removed"]:
        await _record_version(
            data,
            access_key,
            resource_type="config_entry",
            resource_id=entry_id,
            action="delete",
            before=version_before,
            after=None,
            alias=redact_diagnostics(entry.title),
        )
        if remove_raised:
            body["warning"] = (
                "Home Assistant raised during removal, but the observed config entry "
                "is gone. Review the reported registry cleanup anomalies."
            )
        return (
            _tool_success(json.dumps(body, default=str)),
            "allowed",
            f"integration:{entry_id}",
        )

    body["error"] = (
        "Home Assistant did not remove the config entry. The observed registry state "
        "is included; Phoenix made no manual registry deletion."
    )
    return _tool_error(json.dumps(body, default=str)), "denied", entry_id


# ---------------------------------------------------------------------------
# Backup (cap_backup) - create + list only; restore is intentionally unsupported
# ---------------------------------------------------------------------------


async def _backup_agent_ids(hass: HomeAssistant) -> list[str]:
    """Available backup agent ids (e.g. hassio.local on HAOS, backup.local on Core).

    Raises WsDispatchError rather than degrading to []: an empty list means "this
    install has no backup agents", which callers act on (create_backup refuses,
    list_backups reports none), so a transport error must not masquerade as it.
    """
    info = await async_ws_command(hass, "backup/agents/info", {})
    agents = info.get("agents") if isinstance(info, dict) else None
    if not isinstance(agents, list):
        return []
    return [str(a["agent_id"]) for a in agents if isinstance(a, dict) and a.get("agent_id")]


def _backup_to_summary(b: Any) -> dict:
    """Project one backup (a ManagerBackup dataclass or dict) to compact JSON.

    The raw backup/info result holds dataclass instances; serializing them with
    json default=str produces unparseable repr strings, so flatten to fields.
    """
    if isinstance(b, dict):
        d = b
    elif dataclasses.is_dataclass(b) and not isinstance(b, type):
        try:
            d = dataclasses.asdict(b)
        except Exception:  # noqa: BLE001 - fall back to attribute access
            d = {}
    else:
        d = {}
    fields = ("backup_id", "name", "date", "database_included", "homeassistant_version")
    out: dict = {f: (d.get(f) if d else getattr(b, f, None)) for f in fields}
    agents = d.get("agents") if d else getattr(b, "agents", None)
    size = None
    agent_ids: list = []
    if isinstance(agents, dict):
        agent_ids = list(agents.keys())
        for a in agents.values():
            sz = a.get("size") if isinstance(a, dict) else getattr(a, "size", None)
            if sz is not None:
                size = sz
                break
    out["size"] = size
    out["agents"] = agent_ids
    return out


async def _tool_list_backups(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list existing backups (compact, newest first) and available agents."""
    if effective_cap(access_key, "cap_backup") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_backups"
    try:
        result = await async_ws_command(hass, "backup/info", {})
    except WsDispatchError as exc:
        return _tool_error(f"Failed to list backups: {exc}"), "invalid_request", "list_backups"
    raw = result.get("backups") if isinstance(result, dict) else None
    backups = raw if isinstance(raw, list) else []
    summaries = [_backup_to_summary(b) for b in backups]
    summaries.sort(key=lambda s: s.get("date") or "", reverse=True)
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 200))
    warnings: list[str] = []
    try:
        available_agents = await _backup_agent_ids(hass)
    except WsDispatchError as exc:
        available_agents = []
        warnings.append(f"Failed to list backup agents: {exc}")
    body = {
        "total": len(summaries),
        "returned": min(len(summaries), limit),
        # Explicit, even though total vs returned already implies it: every other
        # paginating read states truncation as a boolean, and a reader that has to
        # compare two numbers to notice will sometimes not compare them.
        "truncated": len(summaries) > limit,
        "backups": summaries[:limit],
        "available_agents": available_agents,
    }
    if warnings:
        body["warnings"] = warnings
    return _tool_success(json.dumps(body, default=str)), "allowed", "list_backups"


async def _tool_create_backup(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a backup (Confirm-gated)."""
    if effective_cap(access_key, "cap_backup") != CAP_DENY and (refusal := _backup_agent_refusal(args)):
        return refusal
    blocked = await _gate(
        "cap_backup", access_key, hass, data,
        tool_name="create_backup", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_create_backup(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_backup(args, access_key, hass, data)


def _backup_agent_refusal(args: dict) -> tuple[dict, str, str] | None:
    """Refuse a malformed agent_ids instead of silently choosing a local agent."""
    agent_ids = args.get("agent_ids")
    if agent_ids is None or (isinstance(agent_ids, list) and agent_ids
                             and all(isinstance(item, str) and item for item in agent_ids)):
        return None
    return _tool_error("agent_ids must be a non-empty array of backup agent IDs; omit it to use a local agent."), "invalid_request", "create_backup"


async def _execute_create_backup(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    if refusal := _backup_agent_refusal(args):
        return refusal
    agent_ids = args.get("agent_ids")
    if agent_ids is None:
        # Auto-detect: the default agent is install-type dependent (hassio.local on
        # HAOS/supervised, backup.local on Core). Prefer a local one.
        try:
            available = await _backup_agent_ids(hass)
        except WsDispatchError as exc:
            return _tool_error(
                f"Failed to determine available backup agents: {exc}. Pass agent_ids explicitly."
            ), "invalid_request", "create_backup"
        agent_ids = next(([a] for a in ("hassio.local", "backup.local") if a in available), available[:1])
        if not agent_ids:
            return _tool_error("No backup agents are available; pass agent_ids explicitly."), "invalid_request", "create_backup"
    payload: dict = {"agent_ids": agent_ids}
    name = args.get("name")
    if isinstance(name, str) and name.strip():
        payload["name"] = name
    try:
        result = await async_ws_command(hass, "backup/generate", payload, timeout=60)
    except WsDispatchError as exc:
        return _tool_error(f"Failed to create backup: {exc}"), "invalid_request", "create_backup"
    job_id = getattr(result, "backup_job_id", None)
    if job_id is None and isinstance(result, dict):
        job_id = result.get("backup_job_id")
    body = {"created": True, "backup_job_id": job_id, "agent_ids": agent_ids}
    return _tool_success(json.dumps(body, default=str)), "allowed", "create_backup"


async def _tool_mesa(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
    *,
    mesa_tool_name: str,
) -> tuple[dict, str, str]:
    """Adapter for the mesa_* tools, whose entry point takes the name first."""
    from .mesa_audit import request_context

    with request_context(access_key, request_id, client_ip):
        return await async_call_mesa_tool(mesa_tool_name, args, access_key, hass, data, request_id)


_TOOL_DEFS_BY_NAME: dict[str, dict] = {
    definition["name"]: definition
    for definition in (*_ENTITY_TOOL_DEFS, *_SYSTEM_TOOL_DEFS, *_NATIVE_TOOL_DEFS)
}


def _tool_capability_denied(tool_name: str, access_key: AccessKeyRecord, arguments: dict | None = None) -> bool:
    """Whether a declared capability already refuses this tool (and these arguments) for the access key.

    `caps_when` declares extra capabilities an argument value needs, such as a
    log source that also requires Diagnostics, so they are refused here too.
    """
    tool_def = _TOOL_DEFS_BY_NAME.get(tool_name)
    if tool_def is None:
        return False
    required = list(_tool_required_caps(tool_def))
    for name, by_value in (tool_def.get("caps_when") or {}).items():
        value = (arguments or {}).get(name)
        if isinstance(value, str):
            required.extend(by_value.get(value, ()))
    any_caps = _tool_any_caps(tool_def)
    return any(effective_cap(access_key, cap) == CAP_DENY for cap in required) or bool(
        any_caps and all(effective_cap(access_key, cap) == CAP_DENY for cap in any_caps)
    )


async def _call_tool(
    tool_name: str,
    arguments: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, Outcome, str]:
    """Route a tools/call request to the appropriate tool handler.

    The middle element is the audit outcome, narrowed to audit.Outcome so the
    _dispatch_mcp call site that feeds it straight into log_request type-checks.
    This does NOT statically constrain the handlers (_TOOL_HANDLERS stores them
    as Any, erasing their own returns); a sweep test over the whole tool surface
    enforces a valid outcome per tool.
    """
    try:
        check_request_authority()
    except AuthorityChangedError as exc:
        exc.not_dispatched = True
        raise
    if not household_tool_access(tool_name):
        return _tool_error("Forbidden."), "denied", tool_name
    if error := tool_arguments_error(arguments):
        return _tool_error(error), "invalid_request", tool_name
    decoded = decode_container_strings(tool_name, arguments)
    if decoded is not arguments:
        if error := tool_arguments_error(decoded):
            return _tool_error(error), "invalid_request", tool_name
        arguments = decoded
    from .const import APPROVAL_TARGETS_KEY
    if APPROVAL_TARGETS_KEY in arguments:
        return _tool_error("Approval target bindings are server-owned."), "invalid_request", tool_name
    from .home_companion import is_companion, authorize_call, run_request, record_outcome
    if is_companion(access_key):
        refusal = authorize_call(access_key, tool_name, arguments)
        if refusal:
            record_outcome(access_key, tool_name, "denied", arguments=arguments)
            return _tool_error(refusal), "denied", tool_name
        if tool_name == "home_request":
            return await run_request(arguments, access_key, hass, data)
    elif tool_name == "home_request":
        return _tool_error("This tool requires the Home Companion persona."), "denied", tool_name
    internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(tool_name, tool_name)
    entry = _TOOL_HANDLERS.get(internal_name)
    if entry is None:
        return _tool_error(f"Unknown tool: {tool_name}"), "denied", tool_name
    handler, wanted, bound = entry
    # A denied capability learns nothing about its arguments: it is refused here,
    # before any argument check or handler can describe its input.
    if _tool_capability_denied(tool_name, access_key, arguments):
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool_name
    # Curated tools diagnose their arguments inside their handlers, after the
    # capability checks only they can make (call_service's depend on the
    # service). Every other tool is checked here: unknown names first, so a
    # misspelt required argument is named as such. Retired forms keep the
    # handler's migration guidance.
    if tool_name not in UNKNOWN_ARGUMENT_PROTECTED_FIELDS:
        if error := unknown_argument_error(tool_name, arguments):
            return _tool_error(error), "invalid_request", tool_name
        if not set(arguments) & set(_RETIRED_REPLACEMENTS.get(tool_name, {})):
            arguments, error = scalar_arguments(tool_name, arguments)
            if error:
                return _tool_error(error), "invalid_request", tool_name
    context = {"data": data, "request_id": request_id, "client_ip": client_ip}
    from .request_authority import dispatch_request_id
    request_context = dispatch_request_id.set(request_id)
    from .tools.calendar import _tool_calendar_write
    progress = DispatchProgress(tracked=handler in (_tool_call_service, _tool_calendar_write))
    parent_progress = dispatch_progress.get()
    progress_context = dispatch_progress.set(progress)
    try:
        result = await handler(
            arguments, access_key, hass, **{k: context[k] for k in wanted}, **bound
        )
    except AuthorityChangedError as exc:
        exc.not_dispatched = progress.tracked and not progress.started
        raise
    finally:
        dispatch_request_id.reset(request_context)
        dispatch_progress.reset(progress_context)
        if parent_progress is not None and (progress.started or not progress.tracked):
            parent_progress.started = True
    try:
        check_request_authority()
    except AuthorityChangedError as exc:
        exc.not_dispatched = progress.tracked and not progress.started
        if not tool_is_read_only(tool_name):
            exc.receipt = (result[1], result[2])
            if result[1] == "pending_approval" and result[2].startswith("approval:"):
                # The record is server-owned; model text cannot supply a receipt.
                from .approvals import get_approval, STATUS_APPROVED

                saved = get_approval(data.store, result[2].rsplit(":", 1)[-1])
                exc.not_dispatched = exc.not_dispatched and (
                    saved is not None and saved.access_key_id == access_key.id and saved.request_id == request_id
                    and saved.status in {"pending", "rejected", "cancelled", "expired"}
                    and not saved.execution_started_at
                )
                if (saved is not None and saved.access_key_id == access_key.id
                        and saved.request_id == request_id and saved.status == STATUS_APPROVED
                        and isinstance(saved.result, dict) and saved.result.get("outcome") == "allowed"):
                    exc.receipt = ("allowed", result[2])
        raise
    record_outcome(access_key, tool_name, result[1], result[0], arguments, hass)
    return result


async def _get_ha_assist_api(hass: HomeAssistant) -> Any:
    """Return HA's Assist LLM APIInstance, or raise if unavailable.

    HA-COUPLING POINT. LLMContext's fields differ across HA versions (older
    ones declare `user_prompt`), and a TypeError from an unknown field would be
    swallowed by BOTH callers (prompts/list and prompts/get, pass-through access keys
    only) and silently reported as no prompts. So the kwargs are filtered to the
    fields the live dataclass declares, the same "supply the intersection with the
    live signature" approach ws_dispatch uses for ActiveConnection: a future
    field removal degrades to omitting it rather than a silent empty result.
    """
    import dataclasses  # noqa: PLC0415

    from homeassistant.helpers import llm as _ha_llm  # noqa: PLC0415

    wanted = {
        "platform": DOMAIN,
        "context": None,
        "user_prompt": None,
        "language": "en",
        "assistant": "conversation",
        "device_id": None,
    }
    declared = {f.name for f in dataclasses.fields(_ha_llm.LLMContext)}
    kwargs: dict[str, Any] = {k: v for k, v in wanted.items() if k in declared}
    llm_context = _ha_llm.LLMContext(**kwargs)
    return await _ha_llm.async_get_api(hass, _ha_llm.LLM_API_ASSIST, llm_context)


def _build_server_info(access_key: AccessKeyRecord, hass: HomeAssistant, base_url: str) -> dict:
    """Build the phx://server-info resource payload for the MCP resources/read endpoint."""
    states = hass.states.async_all()
    if access_key.pass_through:
        # Use build_permitted_states to get the same set the access key actually sees,
        # including the Phoenix-platform entity filter (sensor.phoenix_mcp_* telemetry sensors).
        count = len(_build_permitted_states(access_key, hass))
    else:
        filtered = filter_entities_for_access_key(states, access_key, hass)
        count = len(filtered)

    return {
        "name": "Phoenix MCP Scoped Proxy",
        "version": PHOENIX_VERSION,
        "access_key_name": access_key.name,
        "permitted_entity_count": count,
        "capability_flags": effective_caps(access_key),
        "persona": access_key.persona,
        "native_ha_mcp_endpoint": f"{base_url}/api/mcp",
        "phoenix_mcp_context_endpoint": f"{base_url}/api/phoenix-mcp/context",
    }


def _build_context_plain(access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """Build the plain-text context document listing accessible entities and capabilities."""
    from .home_companion import is_companion
    if is_companion(access_key):
        return "Use home_request for scoped household requests; installation context is unavailable."

    lines: list[str] = []

    if access_key.pass_through:
        # Use build_permitted_states for an accurate count that respects Phoenix-platform
        # entity filtering and use_assist_exposure (same set the access key actually sees).
        count = len(_build_permitted_states(access_key, hass))
        lines.append("This access key operates in pass-through mode.")
        lines.append(
            f"It has unrestricted access to all {count} accessible Home Assistant entities and services."
        )
        lines.append("")
        lines.append("The phoenix_mcp domain is always blocked regardless of access key type.")
    else:
        states = hass.states.async_all()
        entity_hints = hass.data[DOMAIN].store.get_entity_hints()
        accessible: list[tuple[str, str, str | None]] = []
        for state in states:
            perm = resolve(state.entity_id, access_key, hass)
            if perm == Permission.WRITE:
                accessible.append((state.entity_id, "READ/WRITE", get_effective_hint(access_key, state.entity_id, hass, entity_hints)))
            elif perm == Permission.READ:
                accessible.append((state.entity_id, "READ", get_effective_hint(access_key, state.entity_id, hass, entity_hints)))

        accessible.sort(key=lambda x: x[0])
        lines.append("You have access to the following Home Assistant entities:")
        if accessible:
            for eid, perm_str, hint in accessible:
                hint_part = f' - "{hint}"' if hint else ""
                lines.append(f"- {eid} ({perm_str}){hint_part}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append(
            "You cannot access any other entities. "
            "Do not attempt to call services on entities not listed above."
        )

    lines.append("")
    caps = effective_caps(access_key)
    lines.append("Capabilities (deny / allow / confirm; confirm requires admin approval per request):")
    label_map = (
        ("cap_config_read", "Config read"),
        ("cap_automation_write", "Automation write"),
        ("cap_script_write", "Script write"),
        ("cap_template_render", "Template render"),
        ("cap_restart", "Restart"),
        ("cap_physical_control", "Physical control (locks/alarms/covers)"),
        ("cap_broadcast", "Broadcast"),
        ("cap_log_read", "Log read"),
        ("cap_service_response", "Service response"),
    )
    for cap_key, label in label_map:
        lines.append(f"- {label}: {caps.get(cap_key, 'deny')}")
    lines.append("")
    if access_key.rate_limit_requests > 0:
        lines.append(
            f"Rate limit: {access_key.rate_limit_requests} requests/min, burst {access_key.rate_limit_burst}/sec"
        )
    else:
        lines.append("Rate limit: none")

    return "\n".join(lines)


def _build_context_json(access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Build the structured JSON context document for the ?format=json context endpoint."""
    from .home_companion import is_companion
    if is_companion(access_key):
        return {"access_key_name": access_key.name, "persona": access_key.persona, "entities": [],
                "capability_flags": effective_caps(access_key), "message": "Use home_request for scoped household requests."}
    registry = er.async_get(hass)
    dev_registry = dr.async_get(hass)

    entities: list[dict] = []
    states = hass.states.async_all()

    if access_key.pass_through:
        _expose_check = None
        if access_key.use_assist_exposure:
            from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
                async_should_expose as _should_expose,
            )
            _expose_check = lambda eid: _should_expose(hass, "conversation", eid)
        for state in states:
            eid = state.entity_id
            if eid.split(".")[0] in BLOCKED_DOMAINS:
                continue
            entry = registry.async_get(eid)
            # Exclude Phoenix MCP telemetry sensors (registered to the phoenix_mcp platform) so
            # pass_through access keys see the same entity set as build_permitted_states().
            if entry is not None and entry.platform == DOMAIN:
                continue
            if _expose_check is not None and not _expose_check(eid):
                continue
            area_id = _resolve_area_id(entry, dev_registry)
            entities.append({
                "entity_id": eid,
                "permission": "READ/WRITE",
                "area_id": area_id,
            })
    else:
        entity_hints = hass.data[DOMAIN].store.get_entity_hints()
        for state in states:
            perm = resolve(state.entity_id, access_key, hass)
            if perm not in (Permission.READ, Permission.WRITE):
                continue
            entry = registry.async_get(state.entity_id)
            area_id = _resolve_area_id(entry, dev_registry)
            perm_str = "READ/WRITE" if perm == Permission.WRITE else "READ"
            e: dict = {"entity_id": state.entity_id, "permission": perm_str, "area_id": area_id}
            hint = get_effective_hint(access_key, state.entity_id, hass, entity_hints)
            if hint:
                e["hint"] = hint
            entities.append(e)

    entities.sort(key=lambda e: e["entity_id"])

    return {
        "access_key_name": access_key.name,
        "pass_through": access_key.pass_through,
        "persona": access_key.persona,
        "entities": entities,
        "capability_flags": effective_caps(access_key),
        "rate_limit": {
            "requests_per_minute": access_key.rate_limit_requests,
            "burst_per_second": access_key.rate_limit_burst,
        },
    }


def _build_instructions(access_key: AccessKeyRecord, data: PhoenixData, base_url: str) -> str:
    """Access-key-aware MCP `instructions` primer, sent at every session start.

    Short: the etiquette that prevents the common failure modes (treating
    pending_approval as an error, retrying, ignoring the per-entity safety layer)
    plus this access key's gated capabilities and a link to the full guide at
    /api/phoenix-mcp/skill.
    """
    from .home_companion import is_companion
    if is_companion(access_key):
        return ("This access key uses Phoenix Home Companion. Only home_request is exposed. "
                "Send a complete home request with all restrictions. Each request is independent. "
                "Read the structured status and tool_outcomes; pending approval is not execution. "
                "Do not automatically retry an unknown action outcome.")
    caps = effective_caps(access_key)
    confirm_gated = sorted(c for c, m in caps.items() if m == CAP_CONFIRM)
    lines = [
        "Phoenix MCP is a scoped Home Assistant gateway. This access key sees only operator-granted "
        "entities/tools; some actions are gated.",
        "",
        "- Call get_capability_summary first for read/control permissions and approval gates. "
        "Discover scoped entities with get_overview or search_entities.",
        "- Entity/history timestamps may be returned in UTC. If you do not already know Home "
        "Assistant's local offset, call llm__GetDateTime for local presentation. "
        "Present the local date and time directly. Do not mention UTC, offsets, or conversion "
        "unless asked.",
        # Must agree with tool_common._tool_pending, which the agent reads at the
        # moment it matters; a primer that says something else is a second story
        # about the same event. Both say: continue, then collect the outcomes
        # together, because approvals queue and are cleared in one action.
        "- Status \"pending_approval\" is normal: a human must approve; the action has not run. "
        "Do not retry; do not stop after one: further gated calls queue alongside it for one "
        "operator action. For outcomes, call wait_for_approval ONCE with approval_ids listing "
        "all of them, or get_approval_status "
        "for a one-shot check. Otherwise report what awaits approval.",
        "- Preview risky/bulk service calls with dry_run_service; use whatif for triggered automations.",
        "- A tool absent from the list is unavailable: ask the operator to grant its capability; "
        "do not attempt it.",
        "- A permissions-changed notice means a stale tool list: call get_capability_summary "
        "and ask the user to reconnect or refresh this MCP server's tools.",
    ]
    if confirm_gated:
        lines.append(
            "- Admin approval per call as of this connection: "
            + ", ".join(confirm_gated)
            + ". Permissions can change mid-session; check get_capability_summary."
        )
    if data.store.get_settings().mesa_mode != MESA_MODE_OFF:
        lines.append(
            "- MESA per-entity safety is active: nature can require read-only access or confirmation "
            "regardless of capabilities. Read control_mode in describe_entity/find_available_actions."
        )
    if (
        caps.get("cap_automation_write") != CAP_DENY
        and caps.get("cap_search") != CAP_DENY
    ):
        lines.append(
            "- Before authoring an automation, call find_available_actions on the trigger/condition "
            "entity. Prefer its purpose-built triggers/conditions and ready-to-use config examples "
            "over generic state/numeric_state equivalents."
        )
    lines.append("")
    lines.append(
        f"Full Phoenix MCP and Home Assistant usage guide: {base_url}/api/phoenix-mcp/skill (fetch it before "
        "complex automation, scene, or configuration work)."
    )
    return "\n".join(lines)


def _maybe_append_stale_advisory(
    tool_result: dict, access_key: AccessKeyRecord, data: PhoenixData, client_ip: str
) -> bool:
    """Append the one-time stale-tools/catalog notices to a result, in place.

    Returns whether an advisory was added, which the caller records on the audit
    row. This is the stateless transport's substitute for tools/list_changed: the
    notice rides in-band on the next tools/call, once per staleness epoch (a fresh
    tools/list resets it). The epoch key spans two independent axes, so resolving
    one does not re-fire the other: the OPERATOR changed this access key
    (settings_version moved past the version echoed at the last tools/list), and
    the BUILD changed under a client that already fetched its schemas (catalog
    fingerprint differs; an empty stored fingerprint means never recorded, so
    never stale).

    Skipped for Phoenix's own internal surfaces (the sentinel client IPs): they
    rebuild their tool list every turn from the live access key and have no external
    MCP server to reconnect. The epoch is deliberately NOT marked for them, so a
    real external client sharing the access key still gets advised.
    """
    internal = client_ip in (
        AGENTCLI_CLIENT_IP, ASSIST_CLIENT_IP, VOICE_AGENT_CLIENT_IP, AI_TASK_CLIENT_IP, HOME_COMPANION_CLIENT_IP)
    notices: list[str] = []
    if not internal:
        if access_key.settings_version > access_key.tools_list_version:
            notices.append(_STALE_TOOLS_ADVISORY)
        if (
            access_key.tools_catalog_fingerprint
            and access_key.tools_catalog_fingerprint != _TOOL_CATALOG_FINGERPRINT
        ):
            notices.append(_STALE_CATALOG_ADVISORY)
    epoch = f"{access_key.settings_version}:{access_key.tools_catalog_fingerprint}"
    if not notices or data.stale_tools_advised.get(access_key.id) == epoch:
        return False
    data.stale_tools_advised[access_key.id] = epoch
    content = tool_result.get("content")
    if isinstance(content, list):
        content.extend({"type": "text", "text": n} for n in notices)
    return True


async def _dispatch_tools_call(
    params: dict,
    msg_id: Any,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str,
    client_ip: str,
) -> tuple[dict, str, str, Outcome]:
    """Run one tools/call and build its JSON-RPC response and audit row.

    This is the single choke point for every tools/call, including each item of a
    JSON-RPC batch, which is why the safety net lives here.
    """
    # Capped before dispatch or logging: this is client-supplied and otherwise
    # unbounded. A malformed client (a local model emitting garbled tool-call
    # syntax) can send garbage as the "name" field; every real tool name is a
    # short identifier, so a generous cap only ever clips pathological input and
    # keeps the audit log (method and resource both derive from it) readable.
    raw_name = params.get("name")
    tool_name = raw_name[:MAX_TOOL_NAME_LENGTH] if isinstance(raw_name, str) else ""
    if tool_name != "home_request" and _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(tool_name, tool_name) not in _TOOL_HANDLERS:
        _log(data, access_key, request_id=request_id, method="tools/call", resource="unknown_tool",
             outcome="invalid_request", client_ip=client_ip)
        return _jsonrpc_error(msg_id, -32602, "Unknown tool."), "tools/call", "unknown_tool", "invalid_request"
    arguments = params.get("arguments")
    if arguments is None:
        # Omitted or null is a no-argument call. Any other non-object, falsy or
        # not, is refused by the argument check rather than read as {}.
        arguments = {}
    _mesa_advisory_ctx.set(False)
    try:
        tool_result, outcome, resource = await _call_tool(
            tool_name, arguments, access_key, hass, data,
            request_id=request_id, client_ip=client_ip,
        )
    except AuthorityChangedError as exc:
        if exc.receipt is not None:
            receipt_outcome, receipt_resource = exc.receipt
            _log(data, access_key, request_id=request_id, method=tool_name,
                 resource=receipt_resource, outcome=receipt_outcome, client_ip=client_ip,
                 payload={"name": tool_name, "arguments": arguments, "result_withheld": True})
        raise
    except Exception:
        # _call_tool is a bare dispatcher with no exception handling of its own,
        # and post() has none either: an unhandled exception here would reach
        # aiohttp's default error handling, which the MCP client cannot parse
        # cleanly, and shows up as a multi-minute client HANG rather than a fast
        # error. Log the real exception server-side for diagnosis, never leak it
        # to the client, return something self-correctable.
        _LOGGER.exception(
            "Unhandled exception in tool %s for access key %s rid=%s",
            tool_name, access_key.name, request_id,
        )
        tool_result = _tool_error("Internal error processing this tool call.")
        outcome = "invalid_request"
        resource = tool_name

    stale = _maybe_append_stale_advisory(tool_result, access_key, data, client_ip)
    _log(data, access_key, request_id=request_id, method=tool_name or "tools/call",
         resource=resource, outcome=outcome, client_ip=client_ip,
         payload={"name": tool_name, "arguments": arguments},
         mesa_advisory=_mesa_advisory_ctx.get(),
         stale_tools_advisory=stale)
    return _jsonrpc_result(msg_id, tool_result), tool_name or "tools/call", resource, outcome


# Every JSON-RPC method _dispatch_mcp answers. The transport reads it to choose
# an unknown method's HTTP STATUS, which the spec makes meaningful: 404 with a
# -32601 body says "this IS an MCP endpoint and it does not implement that
# method", which is how a client distinguishes it from a 404 by a server that
# hosts no MCP endpoint at all and decides whether to fall back. The status has
# to be decided BEFORE the response framing is picked, because preparing an SSE
# stream puts a 200 on the wire; an unknown method has nothing to stream anyway.
#
# This mirrors the dispatcher's own branches: a protocol test reads them back off
# the AST and fails on any drift. A method added to one and not the other is the
# failure that matters: answering a real method with 404, or streaming an
# unknown one.
_MCP_METHODS: frozenset[str] = frozenset({
    "server/discover",
    "initialize",
    "notifications/initialized",
    "initialized",
    "ping",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/templates/list",
    "resources/read",
    "prompts/list",
    "prompts/get",
})
_MCP_MODERN_METHODS = _MCP_METHODS - {
    "initialize", "notifications/initialized", "initialized",
}
_MCP_LEGACY_METHODS = _MCP_METHODS - {"server/discover"}


from .conversation_log import recorded_exchange, transport_session, record_event


@recorded_exchange
async def _dispatch_mcp(
    method: str,
    msg_id: Any,
    params: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    base_url: str,
    request_id: str | None = None,
) -> tuple[dict | None, str, str, str]:
    """Dispatch one MCP method call.

    Returns (response_msg, log_method, log_resource, outcome).
    response_msg is None for notifications that require no response.
    """
    request_id = request_id or generate_request_id()
    authority = request_authority.get()
    admitted_context = None
    if authority is None:
        authority = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash)
        admitted_context = request_authority.set(authority)
    context = None
    write_entered = False
    dispatch_entered = False
    result = None
    try:
        current = authority.current_access_key()
        # Internal surfaces deliberately disable inline approval waiting. Refresh
        # all authorization fields while preserving only that execution setting.
        access_key = (
            dataclasses.replace(current, confirm_inline_wait_seconds=0)
            if access_key.confirm_inline_wait_seconds == 0
            and current.confirm_inline_wait_seconds != 0
            else current
        )
        context = policy_version.set(access_key.settings_version)
        if method == "tools/call" and isinstance(params.get("name"), str) and not tool_is_read_only(params["name"]):
            await data.agent_recovery.reconcile(data.store, access_key.id)
            if data.agent_recovery.blocks(access_key.id, request_id):
                _log(data, access_key, request_id=request_id, method=params["name"], resource=params["name"],
                     outcome="denied", client_ip=client_ip)
                recovery_error = _tool_error("This new request was not executed because an earlier agent action has an unresolved outcome. Check the earlier action in Home Assistant and resolve it in Phoenix MCP > Needs attention before trying again. Direct read-only tools remain available where this access key exposes them; delegated requests are blocked.")
                return _jsonrpc_result(msg_id, recovery_error), method, params["name"], "denied"
        check_request_authority()
        write_entered = method == "tools/call" and isinstance(params.get("name"), str) and not tool_is_read_only(params["name"])
        dispatch_entered = True
        result = await _dispatch_mcp_authorized(method, msg_id, params, access_key, hass, data, client_ip, base_url, request_id)
        check_request_authority()
        return result
    except AuthorityChangedError as exc:
        if write_entered:
            completed = (result is not None and result[3] == "allowed") or (exc.receipt is not None and exc.receipt[0] == "allowed")
            name = params["name"]
            if exc.not_dispatched and not completed:
                _log(data, access_key, request_id=request_id, method="authority/changed", resource=name,
                     outcome="denied", client_ip=client_ip)
                return _jsonrpc_result(msg_id, _tool_error("Request authority changed before dispatch. No action was executed.")), method, name, "denied"
            _log(data, access_key, request_id=request_id, method="authority/changed", resource=name,
                 outcome="invalid_request", client_ip=client_ip)
            if completed:
                receipt = _tool_success("Action dispatch completed, but authority changed before its response could be delivered. Response details were withheld. Do not repeat the action; verify its outcome in Home Assistant.")
                return _jsonrpc_result(msg_id, receipt), method, name, "allowed"
            record_event(data, "notice", {"reason": "execution_unknown"}, outcome="unknown")
            try:
                await data.agent_recovery.retain_unknown(access_key.id, name, request_id)
            except Exception as recovery_error:
                log_internal_failure(_LOGGER, "retain_unknown", recovery_error)
            return _jsonrpc_error(msg_id, -32000, "Authority changed during dispatch. The action may already have executed. Do not repeat it. Check Home Assistant and review the action in Phoenix MCP > Needs attention before submitting another write."), method, name, "invalid_request"
        _log(data, access_key, request_id=request_id, method=method, resource="/api/phoenix-mcp",
             outcome="denied", client_ip=client_ip)
        if isinstance(exc, PolicyChangedError) and method == "tools/call":
            refusal = _tool_error("Permissions changed while this tool was running. Its result was withheld. The conversation can continue using current permissions." if dispatch_entered else "Permissions changed before dispatch. No action was executed. The conversation can continue using current permissions.")
            _maybe_append_stale_advisory(refusal, access_key, data, client_ip)
            return _jsonrpc_result(msg_id, refusal), method, "/api/phoenix-mcp", "denied"
        return _jsonrpc_error(msg_id, -32000, "Request authority changed. Check earlier actions before resubmitting with current permissions."), method, "/api/phoenix-mcp", "denied"
    except Exception as exc:
        log_internal_failure(_LOGGER, "mcp_dispatch", exc)
        if write_entered:
            record_event(data, "notice", {"reason": "execution_unknown"}, outcome="unknown")
        _log(data, access_key, request_id=request_id, method=method, resource="/api/phoenix-mcp",
             outcome="invalid_request", client_ip=client_ip)
        return _jsonrpc_error(msg_id, -32603, "Internal error."), method, "/api/phoenix-mcp", "invalid_request"
    finally:
        if context is not None:
            policy_version.reset(context)
        if admitted_context is not None:
            request_authority.reset(admitted_context)


async def _dispatch_mcp_authorized(
    method: str, msg_id: Any, params: dict, access_key: AccessKeyRecord,
    hass: HomeAssistant, data: PhoenixData, client_ip: str,
    base_url: str, request_id: str,
) -> tuple[dict | None, str, str, str]:
    """Dispatch with current request authority established by the outer boundary."""
    if method == "server/discover":
        # The stateless-era replacement for initialize: identity, versions and
        # capabilities in one call, with no handshake to establish. A client
        # speaking a revision that has no initialize would otherwise never receive
        # the access-key-aware primer, which is the only place an agent is told which
        # of its capabilities are approval-gated.
        resp = _jsonrpc_result(msg_id, {
            "resultType": "complete",
            "supportedVersions": list(MCP_PROTOCOL_VERSIONS),
            "capabilities": _SERVER_CAPABILITIES,
            "instructions": _build_instructions(access_key, data, base_url),
            "ttlMs": MCP_DISCOVER_TTL_MS,
            # NEVER "public". The instructions name THIS access key's confirm-gated
            # capabilities, so a shared intermediary caching one access key's answer
            # would serve it to the next access key reaching it through that proxy.
            "cacheScope": "private",
            "_meta": {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "Phoenix MCP", "version": PHOENIX_VERSION,
                },
            },
        })
        _log(data, access_key, request_id=request_id, method="server/discover",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "server/discover", "/api/phoenix-mcp", "allowed"

    if method == "initialize":
        # Echo the client's version when it is a supported one, otherwise name
        # the preferred one and let the client decide. Membership on a tuple
        # compares rather than hashes, so a non-string protocolVersion falls to
        # the preferred version instead of raising.
        requested = params.get("protocolVersion")
        version = (
            requested
            if requested in MCP_LEGACY_PROTOCOL_VERSIONS
            else MCP_LEGACY_PROTOCOL_VERSION_PREFERRED
        )
        resp = _jsonrpc_result(msg_id, {
            "protocolVersion": version,
            "capabilities": _SERVER_CAPABILITIES,
            "serverInfo": {"name": "Phoenix MCP", "version": PHOENIX_VERSION},
            "instructions": _build_instructions(access_key, data, base_url),
        })
        _log(data, access_key, request_id=request_id, method="initialize",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "initialize", "/api/phoenix-mcp", "allowed"

    if method in ("notifications/initialized", "initialized"):
        _log(data, access_key, request_id=request_id, method=method,
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return None, method, "/api/phoenix-mcp", "allowed"

    if method == "ping":
        resp = _jsonrpc_result(msg_id, {})
        _log(data, access_key, request_id=request_id, method="ping",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "ping", "/api/phoenix-mcp", "allowed"

    from .home_companion import is_companion
    if is_companion(access_key) and method in {"resources/list", "resources/templates/list", "prompts/list"}:
        key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates", "prompts/list": "prompts"}[method]
        return _jsonrpc_result(msg_id, {key: []}), method, "/api/phoenix-mcp", "allowed"
    if is_companion(access_key) and method in {"resources/read", "prompts/get"}:
        return _jsonrpc_error(msg_id, -32602, "Unavailable for this persona."), method, "/api/phoenix-mcp", "denied"
    if method == "tools/list":
        # Announce only the tools this access key can use, unless announce_all_tools
        # is set. Cap-tied tools gate on their cap; write/action tools gate on
        # write scope; tools needing a system surface (ESPHome, the Device
        # Builder) gate on it being present; reads are always announced.
        # announce_all_tools overrides all of it, including "requires": it is a
        # debugging affordance, and the call path still refuses.
        announce_all = getattr(access_key, "announce_all_tools", False)
        has_write = access_key_has_write_scope(access_key)
        mesa_defs = mesa_tool_defs() if data.mesa is not None else []
        tools = []
        for tool_def in list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + mesa_defs:
            if household_tool_access(tool_def["name"]) and (announce_all or _tool_is_announced(tool_def, access_key, has_write, hass)):
                tools.append({k: v for k, v in tool_def.items() if k not in ("cap", "caps", "caps_any", "requires")})
        if is_companion(access_key):
            from .home_companion import wrapper_tool
            tools = [wrapper_tool()]
        resp = _jsonrpc_result(msg_id, {"tools": tools})
        # After this response the client's tool list is current: echo the settings
        # version (in memory; persisted by the periodic flush, like last_used_at)
        # and clear the advised marker so a later change advises again.
        access_key.tools_list_version = access_key.settings_version
        access_key.tools_catalog_fingerprint = _TOOL_CATALOG_FINGERPRINT
        data.stale_tools_advised.pop(access_key.id, None)
        _log(data, access_key, request_id=request_id, method="tools/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "tools/list", "/api/phoenix-mcp", "allowed"

    if method == "tools/call":
        return await _dispatch_tools_call(
            params, msg_id, access_key, hass, data, request_id, client_ip,
        )

    if method == "resources/list":
        resp = _jsonrpc_result(msg_id, {
            "resources": [
                {
                    "uri": "homeassistant://assist/context-snapshot",
                    "name": "Assist Context Snapshot",
                    "description": "A snapshot of the current Assist context, matching the existing homeassistant__GetLiveContext tool output",
                    "mimeType": "text/plain",
                },
                {
                    "uri": "phx://server-info",
                    "name": "Phoenix MCP Server Info",
                    "mimeType": "application/json",
                },
            ]
        })
        _log(data, access_key, request_id=request_id, method="resources/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "resources/list", "/api/phoenix-mcp", "allowed"

    if method == "resources/templates/list":
        # Phoenix's two resources have fixed URIs, so there are no parameterized
        # resource templates to advertise. Clients routinely probe this method
        # whenever a server advertises resource support; an empty catalog is the
        # protocol answer, not Method not found.
        resp = _jsonrpc_result(msg_id, {"resourceTemplates": []})
        _log(data, access_key, request_id=request_id, method="resources/templates/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "resources/templates/list", "/api/phoenix-mcp", "allowed"

    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "homeassistant://assist/context-snapshot":
            context_text = _build_live_context(access_key, hass)
            resp = _jsonrpc_result(msg_id, {
                "contents": [{
                    "uri": "homeassistant://assist/context-snapshot",
                    "mimeType": "text/plain",
                    "text": context_text,
                }]
            })
            _log(data, access_key, request_id=request_id, method="resources/read",
                 resource="homeassistant://assist/context-snapshot", outcome="allowed", client_ip=client_ip)
            return resp, "resources/read", "homeassistant://assist/context-snapshot", "allowed"
        if uri != "phx://server-info":
            if msg_id is not None:
                _log(data, access_key, request_id=request_id, method="resources/read",
                     resource=uri or "/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32002, "Unknown resource URI."), "resources/read", uri, "invalid_request"
            return None, "resources/read", uri, "denied"
        server_info = _build_server_info(access_key, hass, base_url)
        resp = _jsonrpc_result(msg_id, {
            "contents": [{
                "uri": "phx://server-info",
                "mimeType": "application/json",
                "text": json.dumps(server_info, default=str),
            }]
        })
        _log(data, access_key, request_id=request_id, method="resources/read",
             resource="phx://server-info", outcome="allowed", client_ip=client_ip)
        return resp, "resources/read", "phx://server-info", "allowed"

    if method == "prompts/list":
        if access_key.pass_through:
            try:
                api_inst = await _get_ha_assist_api(hass)
                prompt_name = f"Default prompt for Home Assistant {api_inst.api.name}"
                prompts = [{"name": prompt_name, "description": f"Default prompt for Home Assistant {api_inst.api.name} API"}]
            except Exception:
                # Never silent: an empty prompt list is a valid answer, so nothing
                # would otherwise distinguish "no prompts" from "the Assist lookup
                # is broken" (as when HA removed LLMContext.user_prompt). Degrading
                # is still right (a missing prompt must not fail the request), but
                # the operator gets a traceback for it.
                _LOGGER.exception("prompts/list: the HA Assist API lookup failed")
                prompts = []
        else:
            prompts = [{
                "name": "Phoenix MCP access context",
                "description": "Describes the Home Assistant entities and capabilities accessible to this access key",
            }]
        resp = _jsonrpc_result(msg_id, {"prompts": prompts})
        _log(data, access_key, request_id=request_id, method="prompts/list",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "prompts/list", "/api/phoenix-mcp", "allowed"

    if method == "prompts/get":
        name = params.get("name", "")
        if access_key.pass_through:
            try:
                api_inst = await _get_ha_assist_api(hass)
                expected_name = f"Default prompt for Home Assistant {api_inst.api.name}"
                if name != expected_name:
                    _log(data, access_key, request_id=request_id, method="prompts/get",
                         resource="/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                    return _jsonrpc_error(msg_id, -32602, "Unknown prompt."), "prompts/get", "/api/phoenix-mcp", "denied"
                resp = _jsonrpc_result(msg_id, {
                    "description": f"Default prompt for Home Assistant {api_inst.api.name} API",
                    "messages": [{"role": "user", "content": {"type": "text",
                        "text": _UNTRUSTED_DATA_BOUNDARY + "\n\n" + api_inst.api_prompt}}],
                })
            except Exception:
                # See prompts/list above: "Prompt unavailable" reads as a normal
                # absence, so without this the failure that produced it is
                # invisible. The audit outcome stays denied because that is what
                # the caller was told; the traceback is for the operator.
                _LOGGER.exception("prompts/get: the HA Assist API lookup failed")
                _log(data, access_key, request_id=request_id, method="prompts/get",
                     resource="/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32603, "Prompt unavailable."), "prompts/get", "/api/phoenix-mcp", "denied"
        else:
            if name != "Phoenix MCP access context":
                _log(data, access_key, request_id=request_id, method="prompts/get",
                     resource="/api/phoenix-mcp", outcome="denied", client_ip=client_ip)
                return _jsonrpc_error(msg_id, -32602, "Unknown prompt."), "prompts/get", "/api/phoenix-mcp", "denied"
            prompt_text = _UNTRUSTED_DATA_BOUNDARY + "\n\n" + _build_context_plain(access_key, hass)
            resp = _jsonrpc_result(msg_id, {
                "description": "Describes the Home Assistant entities and capabilities accessible to this access key",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            })
        _log(data, access_key, request_id=request_id, method="prompts/get",
             resource="/api/phoenix-mcp", outcome="allowed", client_ip=client_ip)
        return resp, "prompts/get", "/api/phoenix-mcp", "allowed"

    if msg_id is not None:
        _log(data, access_key, request_id=request_id, method=method or "unknown",
             resource="/api/phoenix-mcp", outcome="not_implemented", client_ip=client_ip)
        return _jsonrpc_error(msg_id, -32601, "Method not found."), method or "unknown", "/api/phoenix-mcp", "not_implemented"

    return None, method or "unknown", "/api/phoenix-mcp", "not_implemented"


async def _handle_streamable_batch(
    items: list,
    access_key: AccessKeyRecord,
    rl_result: RateLimitResult,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str,
    client_ip: str,
    base_url: str,
) -> web.Response:
    """Dispatch a JSON-RPC batch array per MCP 2025-03-26.

    Each item is dispatched independently. Failed items produce per-item error objects
    rather than failing the whole batch. Notifications (no id) produce no response entry.
    Returns 202 when all items are notifications; 200 with a results array otherwise.
    """
    if not items:
        _audit_mcp_transport(
            data, access_key, request_id, client_ip, body=items, resource="mcp:batch"
        )
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(_jsonrpc_error(None, -32600, "Empty batch.")),
            headers={"X-Phoenix-Request-ID": request_id},
        )

    # Rate limiting for a batch is charged PER ITEM and preflighted against both
    # the minute and burst ceilings by RateLimiter.charge_batch, in post() before
    # anything here runs; charging one unit per batch would let an access key spend
    # MAX_BATCH_ITEMS times its configured ceiling.
    # Items are dispatched sequentially (not via asyncio.gather) so a batch can never
    # run up to MAX_BATCH_ITEMS side-effecting tools concurrently and interleave
    # writes; order is preserved and one item's failure stays isolated.
    if len(items) > MAX_BATCH_ITEMS:
        _audit_mcp_transport(
            data, access_key, request_id, client_ip, body=items, resource="mcp:batch"
        )
        return web.Response(
            status=400,
            content_type="application/json",
            text=json.dumps(_jsonrpc_error(None, -32600, f"Batch too large. Maximum {MAX_BATCH_ITEMS} items.")),
            headers={"X-Phoenix-Request-ID": request_id},
        )

    responses = await _dispatch_streamable_batch(
        items, access_key, hass, data, client_ip, base_url=base_url,
        request_id=request_id,
    )

    if not responses:
        return web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id})

    resp = web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(responses, default=str),
        headers={"X-Phoenix-Request-ID": request_id},
    )
    resp.headers.update(_rate_limit_headers(access_key, rl_result))
    return resp


def _batch_expects_response(items: list) -> bool:
    """Whether a batch produces any reply entry, without dispatching anything.

    Mirrors the per-item suppression rules in _dispatch_streamable_batch so the
    SSE path can decide between a stream and a bare 202 BEFORE preparing a
    response. Any change to those rules must change this too; a test holds the
    two in agreement.
    """
    for item in items:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            return True  # malformed: gets an Invalid Request entry
        kind, _payload = _classify_jsonrpc_message(item)
        if kind == "accepted":
            continue  # a response/notification object never gets a reply
        if kind == "unsupported" and "id" not in item:
            continue  # valid positional-params notification: no entry
        if kind == "error" or "id" in item:
            return True
    return False


async def _dispatch_streamable_batch(
    items: list,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    *,
    base_url: str,
    request_id: str | None = None,
) -> list[dict]:
    """Dispatch each batch item in order, returning only the entries that reply.

    Items are dispatched sequentially (not via asyncio.gather) so a batch can
    never run up to MAX_BATCH_ITEMS side-effecting tools concurrently and
    interleave writes; order is preserved and one item's failure stays isolated.
    """
    bus = _progress_ctx.get()
    batch_policy_version = access_key.settings_version

    write_receipts: dict[int, str] = {}

    async def _dispatch_one(item: Any) -> dict | None:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, resource="mcp:batch",
            )
            msg_id = _sanitize_jsonrpc_id(item.get("id")) if isinstance(item, dict) else None
            return _jsonrpc_error(msg_id, -32600, "Invalid Request.")
        msg_id = _sanitize_jsonrpc_id(item.get("id"))
        kind, payload = _classify_jsonrpc_message(item)
        is_notification = "id" not in item
        if kind == "accepted":
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, outcome="allowed", resource="mcp:response",
            )
            return None  # response/notification object: no reply entry
        if kind in ("error", "unsupported"):
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, resource="mcp:batch",
            )
            # A structurally valid but undispatchable item (positional params) with
            # no id is a valid notification: no entry. A malformed item is an
            # Invalid Request (id null) and still gets an error entry.
            if kind == "unsupported" and is_notification:
                return None
            return _jsonrpc_error(msg_id, *payload)
        method, params = payload
        if is_notification and method in _MCP_METHODS and method not in {"notifications/initialized", "initialized"}:
            _audit_mcp_transport(data, access_key, request_id or generate_request_id(), client_ip,
                                 body=item, resource="mcp:notification")
            return None

        if data.enforce_mcp_lifecycle and method not in _MCP_LEGACY_METHODS:
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, outcome="not_implemented", resource="mcp:batch",
            )
            return None if is_notification else _jsonrpc_error(msg_id, -32601, "Method not found.")
        if bus is not None:
            # Progress is per-request in MCP, and each batch item carries its own
            # token; items run one at a time, so one field is enough.
            bus.progress_token = _progress_token(method, params)
        try:
            response_msg, _, _, outcome = await _dispatch_mcp(
                method, msg_id, params, access_key, hass, data, client_ip,
                base_url=base_url,
                request_id=request_id,
            )
        finally:
            if bus is not None:
                bus.progress_token = None
                bus.status = None
        if (not is_notification and response_msg is not None and method == "tools/call"
                and isinstance(params.get("name"), str) and not tool_is_read_only(params["name"])):
            write_receipts[id(response_msg)] = outcome
        return None if is_notification else response_msg

    responses: list[dict] = []
    for item in items:
        try:
            r = await _dispatch_one(item)
        except Exception:  # noqa: BLE001 - isolate one item's failure from the batch
            msg_id = _sanitize_jsonrpc_id(item.get("id")) if isinstance(item, dict) else None
            responses.append(_jsonrpc_error(msg_id, -32603, "Internal error."))
            continue
        if r is not None:
            responses.append(r)
    # Earlier replies remain buffered while later items await. Revalidate their
    # common authority before disclosing any buffered result in the final batch.
    authority = request_authority.get()
    if authority is not None:
        try:
            current = authority.current_access_key()
            if not current.accepts_policy_version(batch_policy_version):
                raise AuthorityChangedError("Permissions changed.")
        except AuthorityChangedError:
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                outcome="invalid_request", resource="mcp:batch",
            )
            withheld = []
            for row in responses:
                if write_receipts.get(id(row)) == "allowed":
                    withheld.append(_jsonrpc_result(row.get("id"), _tool_success(
                        "Action dispatch completed. Result withheld because authority changed. Do not repeat the action; verify its outcome in Home Assistant."
                    )))
                elif id(row) in write_receipts:
                    withheld.append(_jsonrpc_error(row.get("id"), -32000,
                        "Authority changed. Check earlier actions before resubmitting; do not repeat any dispatched action."))
                else:
                    withheld.append(_jsonrpc_error(row.get("id"), -32000,
                        "Request authority changed. Resubmit reads with current permissions."))
            return withheld
    return responses


async def _dispatch_mcp_result(
    method: str,
    msg_id: Any,
    params: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    *,
    base_url: str,
    request_id: str | None = None,
) -> dict | None:
    """_dispatch_mcp reduced to just the response message, for the SSE writer."""
    response_msg, _method, _resource, _outcome = await _dispatch_mcp(
        method, msg_id, params, access_key, hass, data, client_ip,
        base_url=base_url,
        request_id=request_id,
    )
    return response_msg


async def _dispatch_modern_mcp_result(
    method: str,
    msg_id: Any,
    params: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    *,
    base_url: str,
    request_id: str | None = None,
) -> dict | None:
    """Dispatch and apply the 2026-07-28 wire shape for an SSE response."""
    response_msg = await _dispatch_mcp_result(
        method, msg_id, params, access_key, hass, data, client_ip,
        base_url=base_url, request_id=request_id,
    )
    return _modernize_response(response_msg, method)


def _rate_limit_headers(access_key: AccessKeyRecord, rl_result: Any) -> dict[str, str]:
    """The X-RateLimit-* trio, or nothing when rate limiting is off for this access key."""
    if access_key.rate_limit_requests <= 0:
        return {}
    return {
        "X-RateLimit-Limit": str(access_key.rate_limit_requests),
        "X-RateLimit-Remaining": str(rl_result.remaining),
        "X-RateLimit-Reset": str(rl_result.reset),
    }


def _progress_token(method: str, params: dict) -> str | int | None:
    """The client's progressToken for this request, if it asked for progress.

    Per the MCP spec a token is a string or integer in `params._meta`, and a
    server may only send progress notifications for a request that carried one.
    Anything else (absent, wrong type) means no progress: never an error, since a
    malformed _meta must not fail an otherwise valid call.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    tok = meta.get("progressToken")
    if isinstance(tok, str) and tok:
        return tok
    if isinstance(tok, int) and not isinstance(tok, bool):
        return tok
    return None


def _sse_frame(payload: dict) -> bytes:
    """One SSE `message` event carrying a JSON-RPC object."""
    return f"event: message\ndata: {json.dumps(payload, default=str)}\n\n".encode()


def _progress_notification(bus: _ProgressBus, elapsed: float) -> dict:
    """A JSON-RPC notifications/progress message for the current wait.

    `progress` is elapsed seconds, which satisfies the spec's requirement that the
    value increase with every notification for a given token.
    """
    params: dict[str, Any] = {
        "progressToken": bus.progress_token,
        "progress": round(elapsed, 1),
    }
    if bus.total is not None:
        params["total"] = bus.total
    if bus.status:
        params["message"] = bus.status
    return {"jsonrpc": "2.0", "method": "notifications/progress", "params": params}


async def _mcp_sse_response(
    request: web.Request,
    hass: HomeAssistant,
    request_id: str,
    extra_headers: dict[str, str],
    bus: _ProgressBus,
    dispatch: Any,
    *,
    cancel_on_disconnect: bool,
    msg_id: Any = None,
) -> web.StreamResponse:
    """Run `dispatch` and deliver its JSON-RPC result as a single SSE message.

    Same response, different framing: the client gets exactly the payload the
    plain JSON path would have returned, but the connection carries a frame every
    MCP_SSE_KEEPALIVE_SECONDS while the work is in flight. That matters because a
    confirm gate can hold a tools/call for minutes with nothing on the wire, which
    is what an intermediary drops.

    Only this function writes to the stream. Handler code deep in a tool sets
    bus.status instead, so two coroutines can never interleave frames.
    """
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer the stream
            "X-Phoenix-Request-ID": request_id,
            **extra_headers,
        },
    )
    await resp.prepare(request)

    task = hass.loop.create_task(dispatch)
    task.add_done_callback(_log_dispatch_task_error)
    started = time.monotonic()
    writable = True
    try:
        while True:
            done, _pending = await asyncio.wait(
                {task}, timeout=MCP_SSE_KEEPALIVE_SECONDS
            )
            if done:
                break
            if not writable:
                continue
            elapsed = time.monotonic() - started
            frame = (
                _sse_frame(_progress_notification(bus, elapsed))
                if bus.progress_token is not None
                else b": keepalive\n\n"
            )
            try:
                await resp.write(frame)
            except Exception:  # noqa: BLE001 - any write failure closes this response
                writable = False
                if cancel_on_disconnect:
                    # Stream closure is cancellation in the 2026-07-28 transport.
                    # Cancellation is cooperative: an awaited operation that already
                    # committed is not rolled back, but no later awaitable work should
                    # continue after the response channel disappears. The legacy
                    # 2025-03-26 transport explicitly requires the opposite behavior.
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    break

        try:
            response_msg = None if task.cancelled() else task.result()
        except Exception:
            # Defense in depth: _dispatch_mcp already nets tools/call exceptions, and
            # the batch path isolates per item. Never surface the text to the client.
            _LOGGER.exception("Unhandled exception on the MCP SSE path rid=%s", request_id)
            response_msg = _jsonrpc_error(msg_id, -32603, "Internal error.")

        if writable and response_msg is not None:
            try:
                await resp.write(_sse_frame(response_msg))
            except Exception:  # noqa: BLE001 - client disconnected mid-flight
                pass
        try:
            await resp.write_eof()
        except Exception:  # noqa: BLE001
            pass
        return resp
    except asyncio.CancelledError:
        # Home Assistant enables aiohttp handler cancellation. A connection loss
        # can therefore cancel this request handler while it is waiting for the
        # child dispatch task, before any keepalive write gets a chance to fail.
        # Propagate that transport cancellation into modern dispatch, but leave
        # legacy dispatch detached as required by the 2025-03-26 revision.
        if cancel_on_disconnect and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        raise


def _log_dispatch_task_error(task: asyncio.Task) -> None:
    """Never let a detached dispatch task's exception vanish silently."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _LOGGER.error("MCP dispatch task failed: %s", exc, exc_info=exc)


def _reject_non_finite(_value: str) -> None:
    """json.loads parse_constant hook: reject NaN / Infinity / -Infinity.

    Python's json.loads accepts these by default, and they defeat range checks
    (every comparison with NaN is False, so NaN passes _validate_number_range's
    min/max) and can flow through to HA services and device integrations. Raising
    here rejects them at ingestion; the caller's ValueError handler turns it into
    a clean JSON-RPC parse error.
    """
    raise ValueError("Non-finite JSON numbers are not allowed.")


def _parse_finite_float(value: str) -> float:
    """Reject exponent overflow as well as literal non-finite constants."""
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON numbers are not allowed.")
    return result


def _origin_rejected(hass: HomeAssistant, request: web.Request, request_id: str) -> web.Response | None:
    """Refuse a browser cross-origin (DNS-rebinding) request per the MCP transport spec.

    The MCP Streamable HTTP transport says a server MUST validate the Origin
    header to prevent DNS-rebinding: a malicious web page resolving a hostname to
    127.0.0.1 could otherwise drive a locally-bound MCP server from the victim's
    browser. Only browsers send Origin, so an ABSENT Origin (every real,
    non-browser MCP client) is allowed; a PRESENT Origin is accepted only when it
    matches one of Home Assistant's own configured URLs. It is validated against
    HA's configured URLs via is_hass_url, never the request's own Host header,
    which a rebinding attacker also forges. This is defense in depth: Phoenix MCP
    already requires its access key sent as a bearer credential, which such a page cannot know, so a rebound
    request still gets the uniform 401; the Origin check refuses it one step
    earlier.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return None
    from homeassistant.helpers.network import is_hass_url  # noqa: PLC0415
    try:
        allowed = is_hass_url(hass, origin)
    except ValueError:
        # A malformed Origin (e.g. "http://[::1") makes HA's URL parser raise;
        # treat any unparseable Origin as not allowed rather than letting it
        # escape as an unauthenticated 500.
        allowed = False
    if allowed:
        return None
    return _error("forbidden", "Origin not allowed.", 403, request_id)


def _prune_legacy_sessions(data: PhoenixData) -> None:
    """Expire idle legacy sessions and bound worst-case in-memory growth."""
    now = time.monotonic()
    expired = [
        session_id
        for session_id, state in data.mcp_sessions.items()
        if now - float(state.get("last_seen", 0.0)) > _MCP_LEGACY_SESSION_TTL_SECONDS
    ]
    for session_id in expired:
        data.mcp_sessions.pop(session_id, None)


def _validate_legacy_initialize(params: dict) -> tuple[int, str] | None:
    """Validate the required 2025-03-26 initialize parameters."""
    if not isinstance(params.get("protocolVersion"), str):
        return -32602, "Invalid params: protocolVersion is required."
    if not isinstance(params.get("capabilities"), dict):
        return -32602, "Invalid params: capabilities are required."
    client_info = params.get("clientInfo")
    if (
        not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not isinstance(client_info.get("version"), str)
    ):
        return -32602, "Invalid params: clientInfo is required."
    return None


def _open_legacy_session(data: PhoenixData, access_key: AccessKeyRecord) -> str | None:
    """Allocate within both quotas without evicting another active session."""
    _prune_legacy_sessions(data)
    owned = sum(state.get("access_key_id") == access_key.id for state in data.mcp_sessions.values())
    if owned >= _MCP_LEGACY_SESSION_ACCESS_KEY_LIMIT or len(data.mcp_sessions) >= _MCP_LEGACY_SESSION_LIMIT:
        return None
    session_id = secrets.token_urlsafe(32)
    data.mcp_sessions[session_id] = {
        "access_key_id": access_key.id,
        "initialized": False,
        "last_seen": time.monotonic(),
        "protocol_version": MCP_LEGACY_PROTOCOL_VERSION_PREFERRED,
    }
    return session_id


def _legacy_session(
    data: PhoenixData, request: web.Request, access_key: AccessKeyRecord
) -> dict | None:
    _prune_legacy_sessions(data)
    session_id = request.headers.get("Mcp-Session-Id")
    state = data.mcp_sessions.get(session_id) if isinstance(session_id, str) else None
    if state is None or state.get("access_key_id") != access_key.id:
        return None
    state["last_seen"] = time.monotonic()
    return state


def _legacy_session_error_response(
    request: web.Request, msg_id: Any, request_id: str
) -> web.Response:
    """Distinguish a missing required session from a supplied unknown one."""
    supplied = bool(request.headers.get("Mcp-Session-Id"))
    return _protocol_error_response(
        msg_id,
        -32000,
        "Legacy MCP session required.",
        request_id,
        status=404 if supplied else 400,
    )


class PhoenixMcpView(PhoenixView):
    """POST/DELETE /api/phoenix-mcp - MCP Streamable HTTP transport.

    The revisions implemented are const.MCP_PROTOCOL_VERSIONS; read the comment
    there before adding one, since the list is a compliance claim.

    Served from the integration's base path so the URL an operator pastes into
    an MCP client config is as short as possible. The trailing-slash form is
    registered too: aiohttp treats it as a distinct resource and does not
    redirect, so without it a stray slash in a client config would 404.
    """

    url = "/api/phoenix-mcp"
    extra_urls = ["/api/phoenix-mcp/"]
    name = "api:phoenix-mcp:mcp"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """Handle one request with task-local credential identity."""
        context = request_authority.set(None)
        try:
            with transport_session(request.headers.get("Mcp-Session-Id")):
                return await self._post(request)
        finally:
            request_authority.reset(context)

    async def delete(self, request: web.Request) -> web.Response:
        """Terminate an authenticated legacy Streamable HTTP session."""
        context = request_authority.set(None)
        try:
            return await self._delete(request)
        finally:
            request_authority.reset(context)

    async def _delete(self, request: web.Request) -> web.Response:
        """Release a session created by ``initialize`` for this access key."""
        hass = self.hass
        request_id = generate_request_id()
        data = hass.data.get(DOMAIN)
        if data is None or not data.ready or data.shutting_down:
            return _error(
                "service_unavailable", "Service unavailable.", 503, request_id
            )
        client_ip = _get_client_ip(request)

        rejected = _origin_rejected(hass, request, request_id)
        if rejected is not None:
            return rejected

        result = await _async_get_authenticated_access_key(
            hass, request, data, request_id, "/api/phoenix-mcp"
        )
        if isinstance(result, web.Response):
            return result
        access_key, _rl_result = result
        authority = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash)
        request_authority.set(authority)
        try:
            access_key = authority.current_access_key()
        except AuthorityChangedError:
            unavailable = (
                not data.ready
                or data.shutting_down
                or data.store.get_settings().kill_switch
                or hass.data.get(DOMAIN) is not data
            )
            return _error(
                "service_unavailable" if unavailable else "unauthorized",
                "Service unavailable." if unavailable else "Unauthorized.",
                503 if unavailable else 401,
                request_id,
            )

        session_id = request.headers.get("Mcp-Session-Id")
        if not session_id:
            _log(
                data,
                access_key,
                request_id=request_id,
                method="DELETE",
                resource="mcp:session",
                outcome="invalid_request",
                client_ip=client_ip,
            )
            return _error(
                "invalid_request",
                "Mcp-Session-Id header is required.",
                400,
                request_id,
            )

        _prune_legacy_sessions(data)
        session = data.mcp_sessions.get(session_id)
        if session is None or session.get("access_key_id") != access_key.id:
            _log(
                data,
                access_key,
                request_id=request_id,
                method="DELETE",
                resource="mcp:session",
                outcome="not_found",
                client_ip=client_ip,
            )
            return _error("not_found", "MCP session not found.", 404, request_id)

        data.mcp_sessions.pop(session_id, None)
        _log(
            data,
            access_key,
            request_id=request_id,
            method="DELETE",
            resource="mcp:session",
            outcome="allowed",
            client_ip=client_ip,
        )
        return web.Response(status=204, headers={"X-Phoenix-Request-ID": request_id})

    async def _post(self, request: web.Request) -> web.Response:
        """Authenticate, read, and dispatch one Streamable HTTP request."""
        hass = self.hass
        request_id = generate_request_id()
        data = hass.data.get(DOMAIN)
        if data is None or not data.ready or data.shutting_down:
            return _error(
                "service_unavailable", "Service unavailable.", 503, request_id
            )
        client_ip = _get_client_ip(request)

        rejected = _origin_rejected(hass, request, request_id)
        if rejected is not None:
            return rejected

        result = await _async_get_authenticated_access_key(
            hass, request, data, request_id, "/api/phoenix-mcp"
        )
        if isinstance(result, web.Response):
            return result
        access_key, rl_result = result
        authority = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash)
        request_authority.set(authority)

        def audited_transport_response(
            response: web.Response,
            *,
            body: Any = None,
            outcome: Outcome = "invalid_request",
            resource: str = "/api/phoenix-mcp",
        ) -> web.Response:
            _audit_mcp_transport(
                data, access_key, request_id, client_ip,
                body=body, outcome=outcome, resource=resource,
            )
            return response

        from .const import MAX_REQUEST_BODY_BYTES as _MAX_BODY
        if request.content_length is not None and request.content_length > _MAX_BODY:
            return audited_transport_response(
                _error("request_too_large", "Request body too large.", 413, request_id)
            )
        # aiohttp's StreamReader.read(n) is a SHORT read: it returns as soon as
        # the buffer has ANY data, up to n bytes, not once n bytes have
        # arrived. A single read(_MAX_BODY + 1) call could therefore silently
        # truncate a larger tool-call body arriving across multiple
        # TCP/chunked segments. Loop until EOF instead, bailing as soon as the
        # cap is exceeded.
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = await request.content.read(_MAX_BODY - total + 1)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_BODY:
                    return audited_transport_response(
                        _error("request_too_large", "Request body too large.", 413, request_id)
                    )
                at_eof = getattr(request.content, "at_eof", None)
                if callable(at_eof) and at_eof():
                    break
        except Exception:
            return audited_transport_response(
                _error("invalid_request", "Failed to read request body.", 400, request_id)
            )
        try:
            access_key = authority.current_access_key()
        except AuthorityChangedError:
            unavailable = not data.ready or data.shutting_down or data.store.get_settings().kill_switch or hass.data.get(DOMAIN) is not data
            return _error(
                "service_unavailable" if unavailable else "unauthorized",
                "Service unavailable." if unavailable else "Unauthorized.",
                503 if unavailable else 401, request_id,
            )
        body_bytes = b"".join(chunks)
        if not body_bytes:
            return audited_transport_response(web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32700, "Parse error.")),
                headers={"X-Phoenix-Request-ID": request_id},
            ))
        try:
            parsed = json.loads(body_bytes, parse_constant=_reject_non_finite, parse_float=_parse_finite_float)
        except (ValueError, RecursionError):  # Invalid JSON, numeric overflow, or excessive nesting.
            _LOGGER.warning(
                "MCP POST: invalid JSON body (%d bytes read, Content-Length %s) rid=%s",
                len(body_bytes), request.content_length, request_id,
            )
            return audited_transport_response(web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32700, "Parse error.")),
                headers={"X-Phoenix-Request-ID": request_id},
            ))

        # Response framing, not a session: an SSE-capable client gets the SAME
        # single JSON-RPC response, delivered as one `message` event, with
        # keepalive frames while the work runs. Per the Streamable HTTP spec
        # conforming clients for the supported revisions advertise both types,
        # so this is their normal path; a custom JSON-only caller keeps
        # byte-identical behavior.
        wants_sse = "text/event-stream" in request.headers.get("Accept", "")
        base_url = str(request.url.origin())
        rl_headers = _rate_limit_headers(access_key, rl_result)

        if isinstance(parsed, list):
            if data.enforce_mcp_lifecycle:
                header_version = request.headers.get("MCP-Protocol-Version")
                carries_modern_meta = any(
                    isinstance(item, dict)
                    and _MCP_PROTOCOL_META in (_request_meta(item.get("params")) or {})
                    for item in parsed
                )
                if (
                    header_version == _MCP_MODERN_VERSION
                    or carries_modern_meta
                ):
                    return audited_transport_response(_protocol_error_response(
                        None,
                        -32600,
                        "Invalid Request: JSON-RPC batches are not supported by this protocol version.",
                        request_id,
                    ), body=parsed, resource="mcp:batch")
                if header_version and header_version not in MCP_PROTOCOL_VERSIONS:
                    return audited_transport_response(_protocol_error_response(
                        None, -32022, "Unsupported protocol version", request_id,
                        data={"supported": list(MCP_PROTOCOL_VERSIONS), "requested": header_version},
                    ), body=parsed, resource="mcp:batch")
                if any(
                    isinstance(item, dict) and item.get("method") == "initialize"
                    for item in parsed
                ):
                    return audited_transport_response(_protocol_error_response(
                        None,
                        -32600,
                        "Invalid Request: initialize must not be sent in a batch.",
                        request_id,
                    ), body=parsed, resource="mcp:batch")
                session = _legacy_session(data, request, access_key)
                if session is None:
                    session_outcome: Outcome = (
                        "not_found"
                        if request.headers.get("Mcp-Session-Id")
                        else "invalid_request"
                    )
                    return audited_transport_response(
                        _legacy_session_error_response(request, None, request_id),
                        body=parsed,
                        outcome=session_outcome,
                        resource="mcp:batch",
                    )
                if not session.get("initialized") and parsed:
                    first = parsed[0]
                    if (isinstance(first, dict) and first.get("jsonrpc") == "2.0" and "id" not in first
                            and first.get("method") in {"notifications/initialized", "initialized"}
                            and _classify_jsonrpc_message(first)[0] == "dispatch"):
                        session["initialized"] = True
                if not session.get("initialized"):
                    return audited_transport_response(_protocol_error_response(
                        None, -32000, "Legacy MCP initialization is not complete.", request_id
                    ), body=parsed, resource="mcp:batch")
            if len(parsed) > MAX_BATCH_ITEMS or (access_key.rate_limit_requests > 0 and (
                    (access_key.rate_limit_burst > 0 and len(parsed) > access_key.rate_limit_burst)
                    or len(parsed) > access_key.rate_limit_requests)):
                return audited_transport_response(_protocol_error_response(None, -32600,
                    "Batch exceeds the configured request capacity; split it into smaller requests.", request_id),
                    body=parsed, resource="mcp:batch")
            # A batch is ONE HTTP request carrying up to MAX_BATCH_ITEMS calls,
            # and the auth check above charged it as one, so batching would
            # multiply an access key's effective rate by MAX_BATCH_ITEMS. The items are
            # the work, so the other N-1 are charged here, once the count is
            # known, and the whole batch is REFUSED if the budget will not cover
            # it. Preflighting is what makes the ceiling real: charging without
            # refusing would let an access key run every item and only then be
            # throttled. Nothing has been dispatched yet, so there is no
            # half-applied batch to worry about.
            batch_rl = data.rate_limiter.charge_batch(
                access_key.id, access_key.rate_limit_requests, access_key.rate_limit_burst,
                min(len(parsed), MAX_BATCH_ITEMS) - 1,
            )
            if not batch_rl.allowed:
                _fire_rate_limit_events(hass, data, access_key)
                _log(
                    data, access_key, request_id=request_id, method="POST",
                    resource="mcp:batch", outcome="rate_limited", client_ip=client_ip,
                )
                resp = _error("rate_limited", "Rate limit exceeded.", 429, request_id)
                resp.headers["Retry-After"] = str(batch_rl.retry_after)
                return resp
            # From here on the batch's own result is what the client is told, on
            # BOTH paths: the single-request result above was computed before the
            # other N-1 items were charged, so it would report a budget the access key
            # no longer has. This covers the SSE framing; the JSON path takes
            # batch_rl directly.
            if batch_rl.rate_limiting_enabled:
                rl_headers = _rate_limit_headers(access_key, batch_rl)
            if wants_sse and _batch_expects_response(parsed) and 0 < len(parsed) <= MAX_BATCH_ITEMS:
                bus = _ProgressBus()
                _progress_ctx.set(bus)
                return cast(web.Response, await _mcp_sse_response(
                    request, hass, request_id, rl_headers, bus,
                    _dispatch_streamable_batch(
                        parsed, access_key, hass, data, client_ip, base_url=base_url,
                        request_id=request_id,
                    ),
                    cancel_on_disconnect=False,
                ))
            batch_dispatch = _handle_streamable_batch(parsed, access_key, batch_rl, hass, data, request_id, client_ip, base_url=base_url)
            if data.enforce_mcp_lifecycle:
                batch_task = asyncio.create_task(batch_dispatch)
                batch_task.add_done_callback(_log_dispatch_task_error)
                return await asyncio.shield(batch_task)
            return await batch_dispatch

        if not isinstance(parsed, dict):
            return audited_transport_response(web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32600, "Invalid Request.")),
                headers={"X-Phoenix-Request-ID": request_id},
            ), body=parsed)

        body = parsed
        if body.get("jsonrpc") != "2.0":
            return audited_transport_response(web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(_sanitize_jsonrpc_id(body.get("id")), -32600, "Invalid Request.")),
                headers={"X-Phoenix-Request-ID": request_id},
            ), body=body)

        msg_id = _sanitize_jsonrpc_id(body.get("id"))
        # A JSON-RPC notification is a VALID request that omits `id`; it must
        # never receive a response. But `id`-absence alone is not enough: a
        # malformed envelope (bad method, non-object params) is an Invalid
        # Request and MUST get an error with `id: null`, not be silently
        # swallowed. So classify FIRST and only suppress the dispatch path.
        # (`id: null` is a request, not a notification, hence membership, not value.)
        kind, payload = _classify_jsonrpc_message(body)
        is_notification = "id" not in body
        if kind == "accepted":
            if data.enforce_mcp_lifecycle and _is_modern_request(body, request):
                return audited_transport_response(_protocol_error_response(
                    msg_id,
                    -32600,
                    "Invalid Request: clients must not send JSON-RPC responses for this protocol version.",
                    request_id,
                ), body=body)
            return audited_transport_response(
                web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id}),
                body=body,
                outcome="allowed",
                resource="mcp:response",
            )
        if kind in ("error", "unsupported"):
            # A structurally valid but undispatchable request (positional params)
            # is still a valid notification when it has no id, so it gets no reply;
            # a malformed envelope always gets an error (id null for a no-id body).
            if kind == "unsupported" and is_notification:
                return audited_transport_response(
                    web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id}),
                    body=body,
                )
            code, message = payload
            return audited_transport_response(web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(msg_id, code, message)),
                headers={"X-Phoenix-Request-ID": request_id},
            ), body=body)
        method, params = payload

        if is_notification and method in _MCP_METHODS and method not in {"notifications/initialized", "initialized"}:
            return audited_transport_response(web.Response(status=400,
                headers={"X-Phoenix-Request-ID": request_id}), body=body)
        modern = False
        legacy_session_id: str | None = None
        if data.enforce_mcp_lifecycle:
            modern = _is_modern_request(body, request)
            if modern:
                validation_error = _validate_modern_request(body, request, request_id)
                if validation_error is not None:
                    return audited_transport_response(validation_error, body=body)
                known_methods = _MCP_MODERN_METHODS
            else:
                known_methods = _MCP_LEGACY_METHODS
                if method == "initialize":
                    if request.headers.get("Mcp-Session-Id"):
                        return audited_transport_response(_protocol_error_response(
                            msg_id,
                            -32600,
                            "Invalid Request: initialize must start a new session.",
                            request_id,
                        ), body=body)
                    invalid_initialize = _validate_legacy_initialize(params)
                    if invalid_initialize is not None:
                        return audited_transport_response(_protocol_error_response(
                            msg_id, *invalid_initialize, request_id
                        ), body=body)
                    legacy_session_id = _open_legacy_session(data, access_key)
                    if legacy_session_id is None:
                        response = _protocol_error_response(
                            msg_id, -32000, "Legacy MCP session capacity reached. Close unused sessions or retry after they expire.", request_id,
                            status=429,
                        )
                        response.headers["Retry-After"] = str(math.ceil(_MCP_LEGACY_SESSION_TTL_SECONDS))
                        return audited_transport_response(response, body=body)
                else:
                    session = _legacy_session(data, request, access_key)
                    if session is None:
                        message_session_outcome: Outcome = (
                            "not_found"
                            if request.headers.get("Mcp-Session-Id")
                            else "invalid_request"
                        )
                        return audited_transport_response(
                            _legacy_session_error_response(request, msg_id, request_id),
                            body=body,
                            outcome=message_session_outcome,
                        )
                    if method in ("notifications/initialized", "initialized"):
                        session["initialized"] = True
                    elif method != "ping" and not session.get("initialized"):
                        return audited_transport_response(_protocol_error_response(
                            msg_id,
                            -32000,
                            "Legacy MCP initialization is not complete.",
                            request_id,
                        ), body=body)
        else:
            known_methods = _MCP_METHODS

        response_msg: dict | None
        if method not in known_methods:
            # Still routed through the dispatcher rather than answered here, so
            # the error body and the audit row keep one definition; only the
            # HTTP status is decided at this layer. A notification for a method
            # that is not implemented is accepted and dropped (202), not refused:
            # the envelope is valid and the sender is not owed a reply.
            if method in _MCP_METHODS:
                _log(
                    data,
                    access_key,
                    request_id=request_id,
                    method=method,
                    resource="/api/phoenix-mcp",
                    outcome="not_implemented",
                    client_ip=client_ip,
                )
                response_msg = _jsonrpc_error(msg_id, -32601, "Method not found.")
            else:
                response_msg, _m, _r, _o = await _dispatch_mcp(
                    method, msg_id, params, access_key, hass, data, client_ip,
                    base_url=base_url,
                    request_id=request_id,
                )
            if modern:
                response_msg = _modernize_response(response_msg, method)
            if is_notification or response_msg is None:
                return web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id})
            return web.Response(
                status=404 if modern else 200,
                content_type="application/json",
                text=json.dumps(response_msg),
                headers={"X-Phoenix-Request-ID": request_id},
            )

        if legacy_session_id is not None:
            rl_headers = {**rl_headers, "Mcp-Session-Id": legacy_session_id}

        if wants_sse and not is_notification:
            bus = _ProgressBus(progress_token=_progress_token(method, params))
            _progress_ctx.set(bus)
            dispatch = (
                _dispatch_modern_mcp_result(
                    method, msg_id, params, access_key, hass, data, client_ip,
                    base_url=base_url,
                    request_id=request_id,
                )
                if modern
                else _dispatch_mcp_result(
                    method, msg_id, params, access_key, hass, data, client_ip,
                    base_url=base_url,
                    request_id=request_id,
                )
            )
            return cast(web.Response, await _mcp_sse_response(
                request, hass, request_id, rl_headers, bus,
                dispatch,
                cancel_on_disconnect=modern,
                msg_id=msg_id,
            ))

        inline_dispatch = _dispatch_mcp(method, msg_id, params, access_key, hass, data, client_ip,
                                 base_url=base_url, request_id=request_id)
        if not modern:
            task = asyncio.create_task(inline_dispatch)
            task.add_done_callback(_log_dispatch_task_error)
            response_msg, _log_method, _log_resource, _outcome = await asyncio.shield(task)
        else:
            response_msg, _log_method, _log_resource, _outcome = await inline_dispatch
        if modern:
            response_msg = _modernize_response(response_msg, method)

        if is_notification or response_msg is None:
            return web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id})

        resp = web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(response_msg, default=str),
            headers={"X-Phoenix-Request-ID": request_id},
        )
        resp.headers.update(rl_headers)
        return resp


class PhoenixMcpContextView(PhoenixView):
    """GET /api/phoenix-mcp/context - context document listing accessible entities and capability flags.

    Returns plain text by default; pass ?format=json for a structured JSON response.
    """

    url = "/api/phoenix-mcp/context"
    name = "api:phoenix-mcp:context"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = self.hass
        request_id = generate_request_id()
        data = hass.data.get(DOMAIN)
        if data is None or not data.ready or data.shutting_down:
            return _error(
                "service_unavailable", "Service unavailable.", 503, request_id
            )
        client_ip = _get_client_ip(request)

        result = await _async_get_authenticated_access_key(
            hass, request, data, request_id, "/api/phoenix-mcp/context"
        )
        if isinstance(result, web.Response):
            return result
        access_key, _rl = result

        _log(data, access_key, request_id=request_id, method="GET", resource="/api/phoenix-mcp/context",
             outcome="allowed", client_ip=client_ip)

        fmt = request.query.get("format", "")
        if fmt == "json":
            body = _build_context_json(access_key, hass)
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(body, default=str),
                headers={"X-Phoenix-Request-ID": request_id},
            )

        text = _build_context_plain(access_key, hass)
        return web.Response(
            status=200,
            content_type="text/plain",
            text=text,
            headers={"X-Phoenix-Request-ID": request_id},
        )


ALL_MCP_VIEWS: list[type[PhoenixView]] = [
    PhoenixMcpView,
    PhoenixMcpContextView,
]


# --- Diff builders for Confirm-eligible tools ---------------------------------
# Each builder produces the structured payload shown in the admin Approvals UI.
# Diffs are best-effort: anything missing or non-fatal renders an empty preview
# rather than blocking creation of the pending approval.




def _build_diff_call_service(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Diff for call_service. Resolves entity targets read-only and lists service params."""
    domain = args.get("domain", "")
    service = args.get("service", "")
    # Sanitised like the executor's, so the approver reviews the call that will
    # run. Showing a selector the executor strips would describe a call reaching
    # a whole floor and invite a rejection of something that never had that reach.
    service_data = _sanitize_service_data(args.get("service_data"))
    entity_arg = args.get("entity_id")
    if isinstance(entity_arg, str):
        entity_ids: list[str] = [entity_arg]
    elif isinstance(entity_arg, list):
        entity_ids = [e for e in entity_arg if isinstance(e, str)]
    else:
        entity_ids = []
    preview = {
        "domain": domain,
        "service": service,
        "service_data": service_data,
        "requested_entity_ids": entity_ids,
        "device_id": args.get("device_id"),
        "area_id": args.get("area_id"),
    }
    # Read-only target resolution for the MESA note; the executor re-resolves
    # at approve time. Dual-gate and no-target reloads have no targets and
    # resolve to nothing, so they never carry the note.
    try:
        resolved, _count = resolve_service_targets(
            entity_id=args.get("entity_id"), device_id=args.get("device_id"),
            area_id=args.get("area_id"), service_domain=domain,
            access_key=access_key, hass=hass,
        )
    except EntityCreationNotPermitted:
        resolved = []
    mesa_note = _mesa_confirm_annotation(access_key, hass, [(domain, service, resolved)])
    if mesa_note:
        preview["mesa"] = mesa_note
    return {
        "kind": "service_preview",
        **_summary("call_service.mesa" if mesa_note else "call_service",
                   domain=domain, service=service),
        "target": {"type": "service", "id": f"{domain}/{service}", "label": f"{domain}/{service}"},
        "preview": preview,
    }


async def _build_diff_set_integration_enabled(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict:
    entry_id = str(args.get("entry_id") or "").strip()
    enabled = bool(args.get("enabled"))
    entry = hass.config_entries.async_get_entry(entry_id)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    preview: dict[str, Any] = {
        "domain": entry.domain if entry is not None else None,
        "enabled": enabled,
        "warning": None if enabled else "Disabling unloads the integration and its entities.",
    }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision)
    return {
        "kind": "system_action",
        **_summary("integration.enable" if enabled else "integration.disable", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }


async def _build_diff_set_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    preview: dict[str, Any] = {}
    if entry is not None:
        for field in ("title", "pref_disable_new_entities", "pref_disable_polling"):
            if field in args:
                before = getattr(entry, field)
                preview[field] = {
                    "before": redact_diagnostics(before) if field == "title" else before,
                    "after": redact_diagnostics(args[field]) if field == "title" else args[field],
                }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision)
    return {
        "kind": "system_action",
        **_summary("integration.update", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }


async def _build_diff_reload_integration(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _ConfigEntryActionDecision | None = None,
) -> dict[str, Any]:
    entry_id = str(args.get("entry_id") or "").strip()
    entry = hass.config_entries.async_get_entry(entry_id)
    label = (
        f"{entry.domain} ({redact_diagnostics(entry.title)})"
        if entry is not None
        else entry_id
    )
    preview: dict[str, Any] = {
        "domain": entry.domain if entry is not None else None,
        "state": _config_entry_value(entry.state) if entry is not None else None,
        "warning": "Reload temporarily unloads and sets up the integration again.",
    }
    if mesa_decision is not None:
        preview["mesa"] = _config_entry_mesa_preview(mesa_decision)
    return {
        "kind": "system_action",
        **_summary("integration.reload", label=label),
        "target": {"type": "integration", "id": entry_id, "label": label},
        "preview": preview,
    }


def _build_diff_create_backup(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    name = args.get("name") if isinstance(args.get("name"), str) else None
    agent_ids = args.get("agent_ids") if isinstance(args.get("agent_ids"), list) else None
    return {
        "kind": "system_action",
        **(_summary("create_backup.named", name=name) if name else _summary("create_backup")),
        "target": {"type": "backup", "id": None, "label": name},
        "preview": {"name": name, "agent_ids": agent_ids or "(auto-detected local agent)",
                    "note": "Creates a backup; Phoenix MCP cannot restore backups."},
    }


# Register executors for tools that support the admin-approval gate.
# Each entry maps an MCP tool name to its side-effect-only _execute_X function.
# When an admin approves a pending request, async_execute_approved_tool() invokes the
# matching executor with the saved args.
_register_executor("restart_ha", _execute_restart_ha)
_register_executor("call_service", _execute_call_service)
_register_executor("create_automation", _execute_create_automation)
_register_executor("edit_automation", _execute_edit_automation)
_register_executor("delete_automation", _execute_delete_automation)
_register_executor("create_script", _execute_create_script)
_register_executor("edit_script", _execute_edit_script)
_register_executor("delete_script", _execute_delete_script)
_register_executor("create_scene", _execute_create_scene)
_register_executor("edit_scene", _execute_edit_scene)
_register_executor("delete_scene", _execute_delete_scene)
_register_executor("create_helper", _execute_create_helper)
_register_executor("edit_helper", _execute_edit_helper)
_register_executor("delete_helper", _execute_delete_helper)
_register_executor("restore_configuration", _execute_restore_configuration)
_register_executor("write_file", _execute_write_file)
_register_executor("create_blueprint", _execute_create_blueprint)
_register_executor("edit_blueprint", _execute_edit_blueprint)
_register_executor("delete_blueprint", _execute_delete_blueprint)
_register_executor("set_yaml_config", _execute_set_yaml_config)
_register_executor("patch_yaml_config", _execute_patch_yaml_config)
_register_executor("set_helper_settings", _execute_set_config_entry_options)
_register_executor("set_esphome_yaml", _execute_set_esphome_yaml)
_register_executor("delete_esphome_yaml", _execute_delete_esphome_yaml)
_register_executor("rename_esphome_device", _execute_rename_esphome_device)
_register_executor("install_esphome_firmware", _execute_install_esphome_firmware)
_register_executor("set_integration_enabled", _execute_set_integration_enabled)
_register_executor("set_integration", _execute_set_integration)
_register_executor("reconfigure_integration", _execute_reconfigure_integration)
_register_executor("set_integration_log_level", _execute_set_integration_log_level)
_register_executor("reload_integration", _execute_reload_integration)
_register_executor("remove_integration", _execute_remove_integration)
_register_executor("create_backup", _execute_create_backup)
_register_executor("create_dashboard", _execute_create_dashboard)
_register_executor("edit_dashboard", _execute_edit_dashboard)
_register_executor("delete_dashboard", _execute_delete_dashboard)
_register_executor("set_dashboard_config", _execute_set_dashboard_config)
_register_executor("add_dashboard_card", _execute_add_dashboard_card)
_register_executor("edit_dashboard_card", _execute_edit_dashboard_card)
_register_executor("delete_dashboard_card", _execute_delete_dashboard_card)
_register_executor("patch_dashboard", _execute_patch_dashboard)
_register_executor("edit_energy_config", _execute_edit_energy_config)
_register_executor("set_area_or_floor", _execute_set_area_or_floor)
_register_executor("set_entity", _execute_set_entity)
_register_executor("set_device", _execute_set_device)
_register_executor("remove_device", _execute_remove_device)
_register_executor("delete_entity", _execute_delete_entity)
_register_executor("permit_zigbee_join", _execute_permit_zigbee_join)
_register_executor("scan_zigbee_topology", _execute_scan_zigbee_topology)
_register_executor("reconfigure_zigbee_device", _execute_reconfigure_zigbee_device)
_register_executor("remove_zigbee_device", _execute_remove_zigbee_device)
_register_executor("set_zigbee_device_options", _execute_set_zigbee_device_options)
_register_executor("set_zigbee_device_property", _execute_set_zigbee_device_property)
_register_executor("set_zigbee_binding", _execute_set_zigbee_binding)
_register_executor("configure_zigbee_reporting", _execute_configure_zigbee_reporting)
_register_executor("create_zigbee_group", _execute_create_zigbee_group)
_register_executor("set_zigbee_group_members", _execute_set_zigbee_group_members)
_register_executor("remove_zigbee_group", _execute_remove_zigbee_group)
_register_executor("HassSetPosition", _execute_hass_set_position)
_register_executor("HassSetVolumeRelative", _execute_hass_set_volume_relative)
_register_executor("HassStopMoving", _execute_hass_stop_moving)
_register_executor("HassTurnOn", _execute_hass_turn_on)
_register_executor("HassTurnOff", _execute_hass_turn_off)
# MESA control_mode:confirm re-execution. Registered but intentionally NOT
# dispatchable from _call_tool, so only the admin approve path can reach it.
_register_executor(MESA_APPROVED_EXECUTOR, _execute_call_service_mesa_approved)
# ---------------------------------------------------------------------------
# Tool dispatch registry.
#
# Maps each published tool name to its handler; a catalog test checks the
# registry against the published tool catalog. Handlers differ in which pieces of
# request context they need, so _register_tool reads each one's parameter names
# once at import (from the code object, not inspect.signature) and _call_tool
# passes exactly those by keyword.
# ---------------------------------------------------------------------------

_register_tool("get_state", _tool_get_state)
_register_tool("get_camera_image", _tool_get_camera_image)
_register_tool("get_history", _tool_get_history)
_register_tool("get_statistics", _tool_get_statistics)
_register_tool("get_calendar_events", _tool_get_calendar_events)
_register_tool("call_service", _tool_call_service)
_register_tool("get_config", _tool_get_config)
_register_tool("render_template", _tool_render_template)
_register_tool("list_automations", _tool_list_automations)
_register_tool("get_automation", _tool_get_automation)
_register_tool("list_scripts", _tool_list_scripts)
_register_tool("get_script", _tool_get_script)
_register_tool("create_automation", _tool_create_automation)
_register_tool("edit_automation", _tool_edit_automation)
_register_tool("delete_automation", _tool_delete_automation)
_register_tool("restart_ha", _tool_restart_ha)
_register_tool("get_approval_status", _tool_get_approval_status)
_register_tool("wait_for_approval", _tool_wait_for_approval)
_register_tool("get_capability_summary", _tool_get_capability_summary)
_register_tool("get_audit_summary", _tool_get_audit_summary)
_register_tool("GetLiveContext", _tool_get_live_context)
_register_tool("GetDateTime", _tool_get_date_time)
_register_tool("HassTurnOn", _tool_hass_turn_on)
_register_tool("HassTurnOff", _tool_hass_turn_off)
_register_tool("HassLightSet", _tool_hass_light_set)
_register_tool("HassFanSetSpeed", _tool_hass_fan_set_speed)
_register_tool("HassClimateSetTemperature", _tool_hass_climate_set_temperature)
_register_tool("HassSetPosition", _tool_hass_set_position)
_register_tool("HassSetVolume", _tool_hass_set_volume)
_register_tool("HassSetVolumeRelative", _tool_hass_set_volume_relative)
_register_tool("HassMediaPause", _tool_hass_media_pause)
_register_tool("HassMediaUnpause", _tool_hass_media_unpause)
_register_tool("HassMediaNext", _tool_hass_media_next)
_register_tool("HassMediaPrevious", _tool_hass_media_previous)
_register_tool("HassMediaSearchAndPlay", _tool_hass_media_search_and_play)
_register_tool("HassMediaPlayerMute", _tool_hass_media_player_mute)
_register_tool("HassMediaPlayerUnmute", _tool_hass_media_player_unmute)
_register_tool("HassCancelAllTimers", _tool_hass_cancel_all_timers)
_register_tool("HassStopMoving", _tool_hass_stop_moving)
_register_tool("HassVacuumStart", _tool_hass_vacuum_start)
_register_tool("HassVacuumReturnToBase", _tool_hass_vacuum_return_to_base)
_register_tool("HassVacuumCleanArea", _tool_hass_vacuum_clean_area)
_register_tool("HassBroadcast", _tool_hass_broadcast)
_register_tool("get_logs", _tool_get_logs)
_register_tool("get_log_detail", _tool_get_log_detail)
_register_tool("get_phoenix_diagnostics", _tool_get_phoenix_diagnostics)
_register_tool("get_logbook", _tool_get_logbook)
_register_tool("list_integration_log_levels", _tool_list_integration_log_levels)
_register_tool("set_integration_log_level", _tool_set_integration_log_level)
_register_tool("list_blueprints", _tool_list_blueprints)
_register_tool("get_blueprint", _tool_get_blueprint)
_register_tool("create_blueprint", _tool_create_blueprint)
_register_tool("edit_blueprint", _tool_edit_blueprint)
_register_tool("delete_blueprint", _tool_delete_blueprint)
_register_tool("set_area_or_floor", _tool_set_area_or_floor)
_register_tool("set_entity", _tool_set_entity)
_register_tool("set_device", _tool_set_device)
_register_tool("remove_device", _tool_remove_device)
_register_tool("delete_entity", _tool_delete_entity)
_register_tool("get_radio_network", _tool_get_radio_network)
_register_tool("get_radio_device", _tool_get_radio_device)
_register_tool("get_zigbee_groups", _tool_get_zigbee_groups)
_register_tool("scan_zigbee_topology", _tool_scan_zigbee_topology)
_register_tool("permit_zigbee_join", _tool_permit_zigbee_join)
_register_tool("reconfigure_zigbee_device", _tool_reconfigure_zigbee_device)
_register_tool("remove_zigbee_device", _tool_remove_zigbee_device)
_register_tool("set_zigbee_device_options", _tool_set_zigbee_device_options)
_register_tool("set_zigbee_device_property", _tool_set_zigbee_device_property)
_register_tool("set_zigbee_binding", _tool_set_zigbee_binding)
_register_tool("configure_zigbee_reporting", _tool_configure_zigbee_reporting)
_register_tool(
    "create_zigbee_group", _tool_zigbee_group_change,
    tool_name="create_zigbee_group",
)
_register_tool(
    "set_zigbee_group_members", _tool_zigbee_group_change,
    tool_name="set_zigbee_group_members",
)
_register_tool(
    "remove_zigbee_group", _tool_zigbee_group_change,
    tool_name="remove_zigbee_group",
)
_register_tool("create_script", _tool_create_script)
_register_tool("edit_script", _tool_edit_script)
_register_tool("delete_script", _tool_delete_script)
_register_tool("list_areas", _tool_list_areas)
_register_tool("list_floors", _tool_list_floors)
_register_tool("list_zones", _tool_list_zones)
_register_tool("list_devices", _tool_list_devices)
_register_tool("get_device", _tool_get_device)
_register_tool("search_entities", _tool_search_entities)
_register_tool("recognize_intent", _tool_recognize_intent)
_register_tool("get_overview", _tool_get_overview)
_register_tool("describe_area", _tool_describe_area)
_register_tool("find_available_actions", _tool_find_available_actions)
_register_tool("get_automation_traces", _tool_get_automation_traces)
_register_tool("get_system_health", _tool_get_system_health)
_register_tool("get_repairs", _tool_get_repairs)
# ESPHome firmware jobs: a build takes minutes and the admin Approve POST runs its
# executor inline, so compile and install ENQUEUE a job in the add-on and return
# its id, and get_esphome_job polls it. The jobs are the add-on's, durable and
# connection-independent, so Phoenix MCP keeps no job state of its own.
_register_tool("get_esphome_overview", _tool_get_esphome_overview)
_register_tool("get_esphome_yaml", _tool_get_esphome_yaml)
_register_tool("search_esphome_yaml", _tool_search_esphome_yaml)
_register_tool("list_esphome_jobs", _tool_list_esphome_jobs)
_register_tool("list_esphome_devices", _tool_list_esphome_devices)
_register_tool("set_esphome_yaml", _tool_set_esphome_yaml)
_register_tool("delete_esphome_yaml", _tool_delete_esphome_yaml)
_register_tool("validate_esphome_yaml", _tool_validate_esphome_yaml)
_register_tool("get_esphome_board", _tool_get_esphome_board)
_register_tool("get_esphome_component", _tool_get_esphome_component)
_register_tool("get_esphome_automations", _tool_get_esphome_automations)
_register_tool("clean_esphome_build", _tool_clean_esphome_build)
_register_tool("rename_esphome_device", _tool_rename_esphome_device)
_register_tool("compile_esphome_firmware", _tool_compile_esphome_firmware)
_register_tool("install_esphome_firmware", _tool_install_esphome_firmware)
_register_tool("get_esphome_job", _tool_get_esphome_job)
_register_tool("wait_for_esphome_job", _tool_wait_for_esphome_job)
_register_tool("cancel_esphome_job", _tool_cancel_esphome_job)
_register_tool("get_esphome_device_logs", _tool_get_esphome_device_logs)
_register_tool("decode_esphome_backtrace", _tool_decode_esphome_backtrace)
_register_tool("check_ha_config", _tool_check_config)
_register_tool("get_relationships", _tool_get_relationships)
_register_tool("describe_entity", _tool_describe_entity)
_register_tool("whatif", _tool_whatif)
_register_tool("compare_states", _tool_compare_state)
_register_tool("compare_entities", _tool_compare_entities)
_register_tool("recent_activity", _tool_recent_activity)
_register_tool("dry_run_service", _tool_dry_run_service)
_register_tool("validate_automation_or_script", _tool_validate_config)
_register_tool("list_scenes", _tool_list_scenes)
_register_tool("get_scene", _tool_get_scene)
_register_tool("create_scene", _tool_create_scene)
_register_tool("edit_scene", _tool_edit_scene)
_register_tool("delete_scene", _tool_delete_scene)
_register_tool("list_helpers", _tool_list_helpers)
_register_tool("create_helper", _tool_create_helper)
_register_tool("edit_helper", _tool_edit_helper)
_register_tool("delete_helper", _tool_delete_helper)
_register_tool("restore_configuration", _tool_restore_configuration)
_register_tool("watch_entity", _tool_watch_entity)
_register_tool("list_files", _tool_list_files)
_register_tool("read_file", _tool_read_file)
_register_tool("write_file", _tool_write_file)
_register_tool("get_energy_config", _tool_get_energy_config)
_register_tool("get_solar_forecast", _tool_get_solar_forecast)
_register_tool("edit_energy_config", _tool_edit_energy_config)
_register_tool("get_yaml_config", _tool_get_yaml_config)
_register_tool("set_yaml_config", _tool_set_yaml_config)
_register_tool("patch_yaml_config", _tool_patch_yaml_config)
_register_tool("get_helper_settings", _tool_get_config_entry_options)
_register_tool("set_helper_settings", _tool_set_config_entry_options)
_register_tool("list_integrations", _tool_list_integrations)
_register_tool("set_integration_enabled", _tool_set_integration_enabled)
_register_tool("set_integration", _tool_set_integration)
_register_tool("reconfigure_integration", _tool_reconfigure_integration)
_register_tool("reload_integration", _tool_reload_integration)
_register_tool("remove_integration", _tool_remove_integration)
_register_tool("list_backups", _tool_list_backups)
_register_tool("create_backup", _tool_create_backup)
_register_tool("list_dashboards", _tool_list_dashboards)
_register_tool("list_dashboard_cards", _tool_list_dashboard_cards)
_register_tool("create_dashboard", _tool_create_dashboard)
_register_tool("edit_dashboard", _tool_edit_dashboard)
_register_tool("delete_dashboard", _tool_delete_dashboard)
_register_tool("get_dashboard_config", _tool_get_dashboard_config)
_register_tool("set_dashboard_config", _tool_set_dashboard_config)
_register_tool("add_dashboard_card", _tool_dashboard_card, op="add", tool_name="add_dashboard_card")
_register_tool("edit_dashboard_card", _tool_dashboard_card, op="edit", tool_name="edit_dashboard_card")
_register_tool("delete_dashboard_card", _tool_dashboard_card, op="delete", tool_name="delete_dashboard_card")
_register_tool("patch_dashboard", _tool_patch_dashboard)

# The mesa_* tools take (tool_name, args, ...) and are scoped by mesa_tools.
for _mesa_tool_name in sorted(MESA_TOOL_NAMES):
    _register_tool(_mesa_tool_name, _tool_mesa, mesa_tool_name=_mesa_tool_name)

_register_tool("create_calendar_event", _tool_calendar_write, operation="create", tool_name="create_calendar_event")
_register_tool("update_calendar_event", _tool_calendar_write, operation="update", tool_name="update_calendar_event")
_register_tool("delete_calendar_event", _tool_calendar_write, operation="delete", tool_name="delete_calendar_event")
_register_executor("create_calendar_event", _execute_create_calendar_event)
_register_executor("update_calendar_event", _execute_update_calendar_event)
_register_executor("delete_calendar_event", _execute_delete_calendar_event)
_register_tool("get_calendar_write", _tool_get_calendar_write)
