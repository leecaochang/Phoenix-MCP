"""Admin settings views: global settings, routing diagnostics, surface preferences and the wipe."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.core import HomeAssistant

from ..access_key_store import access_key_name_slug, valid_command_routing_languages
from ..ai_task import (
    AiTaskSetupError, ai_evaluate_preferred_status, ai_task_decisions_supported, ai_task_preferred_status,
    ai_task_supported, clear_ai_evaluate_preferred, clear_ai_task_preferred, set_ai_evaluate_preferred,
    set_ai_task_preferred,
)
from ..approvals import (
    REASON_WIPED, collect_pending_approvals_for_wipe, dismiss_approval_notification,
    fire_approval_resolved_event,
)
from ..assist_api import assist_api_supported, async_register_assist_api
from ..catalog_text import LocalizedReason, panel_catalog
from ..const import (
    AUDIT_FLUSH_INTERVALS, AUDIT_LOG_MAXLENS, CONVERSATION_LOG_LIMITS, CONVERSATION_STYLES,
    DETAIL_LEVELS, DOMAIN, GITHUB_URL, HOME_COMPANION_CONTROL_DOMAINS, HOME_COMPANION_READ_DOMAINS,
    HOME_COMPANION_SENSOR_CLASSES, MAX_SEARCH_QUERY_LEN, MESA_MODES, MESA_STORAGE_KEY,
    MESA_STORAGE_VERSION, MIN_HA_VERSION, PHOENIX_VERSION,
)
from ..conversation_log import recorder
from ..data import PhoenixData
from ..durable_storage import DurableStorageError, DurableStore, MesaSaveError
from ..helpers import cancel_expiry_timer
from ..llm.accounts import (
    _get_secret_store, async_wipe_agentcli_secrets, loaded_decision_account, provider_purpose,
)
from ..locales import canonical_language
from ..panel import async_sync_agentchat_inject, async_sync_mesa_inject
from ..provider_constants import (
    AGENT_CHAT_MAX_ITERATIONS_MAX, AGENT_CHAT_MAX_ITERATIONS_MIN, AGENT_CHAT_SCROLLBACK_MAX,
    AGENT_CHAT_SCROLLBACK_MIN, PROVIDER_CATALOG_REFRESH_HOURS_MAX, PROVIDER_CATALOG_REFRESH_HOURS_MIN,
)
from ..routing.command_routing import RoutingAuthority, async_evaluate_command, routing_language
from ..routing.decision_batch import async_preview_batch
from ..routing.decision_context import async_preview_context
from ..routing.decision_history import context_history
from ..routing.decision_reference import async_preview_reference
from ..routing.decision_shadow import async_preview_decision
from ..routing.routing_tables import (
    CHAT_COMMAND_ROUTING_MODES, COMMAND_ROUTING_MODES, COMMAND_ROUTING_TIMEOUT_MAX_MS,
    COMMAND_ROUTING_TIMEOUT_MIN_MS, DECISION_REQUEST_BUDGET_MAX, DECISION_TIMEOUT_MAX_MS,
    DECISION_TIMEOUT_MIN_MS, ROUTING_STATISTICS_SURFACES, TOOL_ROUTING_MODES, TOOL_ROUTING_SETTINGS,
)
from ..routing.tool_routing import qualified_languages
from ..tool_catalog import tool_catalog_counts
from ..tools.esphome import esphome_availability
from ..view_base import PhoenixView, _err
from ..voice_agent import (
    VoicePipelineError, assist_pipeline_preferred, assist_pipeline_supported,
    async_create_assist_pipeline, async_remove_assist_pipeline, async_set_assist_pipeline_preferred,
)
from ..ws_dispatch import async_lovelace_resources
from .common import (
    _audit_admin, _in_menu, _ok, _read_body, require_admin, require_admin_before_ready,
)

_LOGGER = logging.getLogger(__name__)


class PhoenixAdminInfoView(PhoenixView):
    """GET /api/phoenix-mcp/admin/info - integration metadata."""

    url = "/api/phoenix-mcp/admin/info"
    name = "api:phoenix-mcp:admin:info"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        return _ok({
            "version": PHOENIX_VERSION,
            "min_ha_version": MIN_HA_VERSION,
            "github_url": GITHUB_URL,
            "tool_count": tool_catalog_counts(),
            # What a Home Companion key can reach whatever the permission tree says,
            # so the tree and Permissions Summary can show what the resolver allows.
            "home_companion_reach": {
                "control_domains": sorted(HOME_COMPANION_CONTROL_DOMAINS),
                "read_domains": sorted(HOME_COMPANION_READ_DOMAINS),
                "sensor_classes": sorted(HOME_COMPANION_SENSOR_CLASSES),
            },
        }, request_id=request["phoenix_mcp_rid"])


class PhoenixAdminCatalogView(PhoenixView):
    """GET /api/phoenix-mcp/admin/catalog/{language} - the panel's string catalog.

    Served here because hassfest closes translations/ to HA's categories. Admin-only and
    kill-switch immune, so a panel can still render while turning the switch back off.
    """

    url = "/api/phoenix-mcp/admin/catalog/{language}"
    name = "api:phoenix-mcp:admin:catalog"
    requires_auth = True

    @require_admin_before_ready
    async def get(self, request: web.Request, language: str) -> web.Response:
        resources = await self.hass.async_add_executor_job(panel_catalog, language)
        if not resources:
            return _err(
                "not_found", "Catalog not found.", 404,
                request_id=request["phoenix_mcp_rid"],
                key="catalogNotFound",
            )
        return _ok(
            {"language": canonical_language(language), "resources": resources},
            request_id=request["phoenix_mcp_rid"],
        )


def _settings_response(settings, hass: HomeAssistant) -> dict:
    """Return the settings dict plus computed, non-persisted capability flags.

    ``assist_api_supported`` drives the Assist bind toggle. The esphome_* flags report what
    exists on the system; tool announcement ignores ``esphome_builder_live``.
    """


    body = settings.to_dict()
    cfg = loaded_decision_account(hass, settings.decision_provider_id) if hass and settings.decision_provider_id else None
    body["tool_routing_qualified_languages"] = qualified_languages(cfg)
    body["assist_api_supported"] = assist_api_supported()
    body["voice_agent_pipeline_supported"] = assist_pipeline_supported()
    body["voice_agent_pipeline_preferred"] = (
        assist_pipeline_preferred(hass, settings.voice_agent_pipeline_id) if hass else False
    )
    body["ai_task_supported"] = ai_task_supported()
    body["ai_task_decisions_supported"] = ai_task_decisions_supported()
    esphome = esphome_availability(hass)
    body["esphome_integration"] = esphome.integration
    body["esphome_builder"] = esphome.builder
    body["esphome_builder_live"] = esphome.builder_live
    return body


# ---------------------------------------------------------------------------
# Settings PATCH: the writable surface, its validation, and its side effects.
#
# The flow: filter to what is writable, validate/coerce it, apply it under the
# store lock, then run the side effects keyed on which settings actually moved.
# The sets below live at module level, not as handler locals, so they are also
# the readable statement of what this endpoint will accept.
# ---------------------------------------------------------------------------
_BOOL_SETTINGS = frozenset({
    "kill_switch", "disable_all_logging", "log_allowed", "log_denied",
    "log_rate_limited", "log_entity_names", "log_client_ip", "notify_on_rate_limit",
    "notify_on_approval", "mesa_inject_enabled", "access_key_presets_enabled",
    "agentcli_global", "agentcli_home_focused", "voice_agent_enabled",
    "voice_agent_home_focused", "ai_task_enabled", "ai_task_decisions_enabled", "decision_shadow_enabled",
    "agentcli_routing_shadow_enabled", "conversation_log_enabled",
    "provider_catalog_refresh_enabled",
})

_CONVERSATION_STYLE_SETTINGS = frozenset({
    "agentcli_conversation_style", "voice_agent_conversation_style",
    "ai_task_conversation_style",
})

_DETAIL_LEVEL_SETTINGS = frozenset({
    "agentcli_detail_level", "voice_agent_detail_level", "ai_task_detail_level",
})

# Nullable string settings: None/"" clears, anything else must be a string.
# The access key and provider ones are additionally checked against live records
# below, so the panel cannot point a surface at a dead access key or provider.
_NULLABLE_STRING_SETTINGS = (
    "voice_agent_access_key_id", "voice_agent_provider_id", "voice_agent_model",
    "ai_task_access_key_id", "ai_task_provider_id", "ai_task_model", "ai_task_decisions_access_key_id",
    "decision_provider_id", "decision_provider_default_id",
)

# (access key setting, provider setting) pairs validated against live records.
_SURFACE_ACCESS_KEY_PROVIDER_PAIRS = (
    ("voice_agent_access_key_id", "voice_agent_provider_id"),
    ("ai_task_access_key_id", "ai_task_provider_id"),
)

_PATCHABLE_SETTINGS = _BOOL_SETTINGS | set(CONVERSATION_LOG_LIMITS) | {
    *TOOL_ROUTING_SETTINGS.values(),
    "voice_agent_routing_mode", "agentcli_routing_mode", "command_routing_languages", "command_routing_timeout_ms", "decision_timeout_ms", "decision_request_budget",
    "audit_flush_interval", "audit_log_maxlen", "mesa_mode",
    "agentcli_scrollback_lines", "agentcli_max_iterations", "assist_bound_access_key_id",
    "provider_catalog_refresh_hours",
    *_NULLABLE_STRING_SETTINGS,
} | _CONVERSATION_STYLE_SETTINGS | _DETAIL_LEVEL_SETTINGS


def _coerce_int_setting(
    patchable: dict,
    key: str,
    rid: str,
    *,
    allowed: frozenset[int] | None = None,
    bounds: tuple[int, int] | None = None,
) -> web.Response | None:
    """Coerce one integer setting in place. Returns an error response or None.

    The panel posts strings, so int() is deliberate. ``allowed`` rejects values off a fixed
    menu; ``bounds`` clamps limits to the nearest sane value.
    """
    try:
        value = int(patchable[key])
    except (TypeError, ValueError):
        return _err(
            "invalid_request", f"{key} must be an integer.", 400, rid,
            key="integerRequired", params={"field": key},
        )
    if allowed is not None and value not in allowed:
        return _err(
            "invalid_request", f"{key} must be one of: {sorted(allowed)}.",
            400, rid, key="valueAllowed",
            params={"field": key, "values": ", ".join(str(v) for v in sorted(allowed))},
        )
    if bounds is not None:
        value = max(bounds[0], min(bounds[1], value))
    patchable[key] = value
    return None


async def _validated_settings_patch(
    body: dict, data: PhoenixData, hass: HomeAssistant, rid: str
) -> tuple[dict, web.Response | None]:
    """Filter a PATCH body to the writable settings and validate them.

    Returns (patchable, None), or ({}, error_response) on the first rejection. Never writes.
    """
    unknown = sorted(set(body) - _PATCHABLE_SETTINGS)
    if unknown:
        fields = ", ".join(unknown)
        return {}, _err(
            "invalid_request",
            f"The request contains fields Phoenix does not recognize: {fields}.",
            400,
            rid,
            key="unknownFields",
            params={"fields": fields},
        )

    patchable = {k: v for k, v in body.items() if k in _PATCHABLE_SETTINGS}

    for key in _BOOL_SETTINGS & patchable.keys():
        if not isinstance(patchable[key], bool):
            return {}, _err(
                "invalid_request", f"{key!r} must be a boolean (true or false).",
                400, rid, key="booleanRequired", params={"field": key},
            )

    if "mesa_mode" in patchable and not _in_menu(patchable["mesa_mode"], MESA_MODES):
        return {}, _err(
            "invalid_request", f"mesa_mode must be one of: {sorted(MESA_MODES)}.",
            400, rid, key="valueAllowed",
            params={"field": "mesa_mode", "values": ", ".join(sorted(MESA_MODES))},
        )


    for key in _CONVERSATION_STYLE_SETTINGS & patchable.keys():
        if not isinstance(patchable[key], str) or patchable[key] not in CONVERSATION_STYLES:
            return {}, _err(
                "invalid_request", f"{key} must be one of: {sorted(CONVERSATION_STYLES)}.",
                400, rid, key="valueAllowed",
                params={"field": key, "values": ", ".join(sorted(CONVERSATION_STYLES))},
            )
    for key in _DETAIL_LEVEL_SETTINGS & patchable.keys():
        if not isinstance(patchable[key], str) or patchable[key] not in DETAIL_LEVELS:
            return {}, _err(
                "invalid_request", f"{key} must be one of: {sorted(DETAIL_LEVELS)}.",
                400, rid, key="valueAllowed",
                params={"field": key, "values": ", ".join(sorted(DETAIL_LEVELS))},
            )

    for key, modes in (("voice_agent_routing_mode", COMMAND_ROUTING_MODES),
                       ("agentcli_routing_mode", CHAT_COMMAND_ROUTING_MODES),
                       *((key, TOOL_ROUTING_MODES) for key in TOOL_ROUTING_SETTINGS.values())):
        if key in patchable and (not isinstance(patchable[key], str) or patchable[key] not in modes):
            return {}, _err(
                "invalid_request", "Unsupported command routing mode.", 400, rid,
                key="valueAllowed", params={"field": key, "values": ", ".join(sorted(modes))},
            )
    if "command_routing_languages" in patchable and not valid_command_routing_languages(patchable["command_routing_languages"]):
        return {}, _err("invalid_request", "Invalid command routing languages.", 400, rid,
                        key="routingLanguagesInvalid")
    if "command_routing_timeout_ms" in patchable:
        value = patchable["command_routing_timeout_ms"]
        if (type(value) is not int or not
                COMMAND_ROUTING_TIMEOUT_MIN_MS <= value <= COMMAND_ROUTING_TIMEOUT_MAX_MS):
            return {}, _err(
                "invalid_request", "Invalid recognition time budget.", 400, rid,
                key="conversationLimitInvalid", params={"field": "command_routing_timeout_ms",
                "min": COMMAND_ROUTING_TIMEOUT_MIN_MS, "max": COMMAND_ROUTING_TIMEOUT_MAX_MS},
            )

    if "decision_timeout_ms" in patchable:
        value = patchable["decision_timeout_ms"]
        if type(value) is not int or not DECISION_TIMEOUT_MIN_MS <= value <= DECISION_TIMEOUT_MAX_MS:
            return {}, _err("invalid_request", "Invalid decision time budget.", 400, rid, key="conversationLimitInvalid",
                            params={"field": "decision_timeout_ms", "min": DECISION_TIMEOUT_MIN_MS, "max": DECISION_TIMEOUT_MAX_MS})

    if 'decision_request_budget' in patchable:
        value = patchable['decision_request_budget']
        if type(value) is not int or not 0 <= value <= DECISION_REQUEST_BUDGET_MAX:
            return {}, _err('invalid_request', 'Invalid decision request limit.', 400, rid, key='conversationLimitInvalid',
                            params={'field': 'decision_request_budget', 'min': 0, 'max': DECISION_REQUEST_BUDGET_MAX})

    for key, (lo, hi) in CONVERSATION_LOG_LIMITS.items():
        if key in patchable and (type(patchable[key]) is not int or not lo <= patchable[key] <= hi):
            return {}, _err("invalid_request", "Invalid conversation retention limit.", 400, rid,
                            key="conversationLimitInvalid", params={"field": key, "min": lo, "max": hi})

    int_checks = (
        ("audit_flush_interval", {"allowed": AUDIT_FLUSH_INTERVALS}),
        ("audit_log_maxlen", {"allowed": AUDIT_LOG_MAXLENS}),
        ("agentcli_scrollback_lines",
         {"bounds": (AGENT_CHAT_SCROLLBACK_MIN, AGENT_CHAT_SCROLLBACK_MAX)}),
        ("agentcli_max_iterations",
         {"bounds": (AGENT_CHAT_MAX_ITERATIONS_MIN, AGENT_CHAT_MAX_ITERATIONS_MAX)}),
        ("provider_catalog_refresh_hours",
         {"bounds": (PROVIDER_CATALOG_REFRESH_HOURS_MIN, PROVIDER_CATALOG_REFRESH_HOURS_MAX)}),
    )
    for key, kwargs in int_checks:
        if key in patchable:
            err = _coerce_int_setting(patchable, key, rid, **kwargs)
            if err is not None:
                return {}, err

    if "assist_bound_access_key_id" in patchable:
        # None/"" unbinds; a string must reference a currently active access key, so
        # the panel cannot bind Assist to a revoked/expired/ghost id.
        val = patchable["assist_bound_access_key_id"]
        if val in (None, ""):
            patchable["assist_bound_access_key_id"] = None
        elif not isinstance(val, str):
            return {}, _err(
                "invalid_request", "assist_bound_access_key_id must be an access key id string or null.",
                400, rid, key="accessKeyIdOrNull", params={"field": "assist_bound_access_key_id"},
            )
        else:
            bound = data.store.get_access_key_by_id(val)
            if bound is None or not bound.is_valid():
                return {}, _err(
                    "invalid_request", "assist_bound_access_key_id must reference an active access key.",
                    400, rid, key="activeAccessKeyRequired",
                    params={"field": "assist_bound_access_key_id"},
                )

    # Voice-agent and AI-Task string fields: each is None/"" (clear) or must
    # reference a live target, so the panel cannot point either surface at a dead
    # access key or provider. Both runtime surfaces still degrade gracefully if the set
    # is incomplete.
    for vkey in _NULLABLE_STRING_SETTINGS:
        if vkey not in patchable:
            continue
        if patchable[vkey] in (None, ""):
            patchable[vkey] = None
        elif not isinstance(patchable[vkey], str):
            return {}, _err(
                "invalid_request", f"{vkey} must be a string or null.", 400, rid,
                key="stringOrNull", params={"field": vkey},
            )

    provider_store = None
    for tkey, pkey in _SURFACE_ACCESS_KEY_PROVIDER_PAIRS:
        if patchable.get(tkey):
            vtok = data.store.get_access_key_by_id(patchable[tkey])
            if vtok is None or not vtok.is_valid():
                return {}, _err(
                    "invalid_request", f"{tkey} must reference an active access key.",
                    400, rid, key="activeAccessKeyRequired", params={"field": tkey},
                )
        if patchable.get(pkey):
            if provider_store is None:
                provider_store = await _get_secret_store(hass)
            record = provider_store.get(patchable[pkey])
            if not isinstance(record, dict) or provider_purpose(record.get("kind", "")) != "conversation":
                return {}, _err(
                    "invalid_request", f"{pkey} must reference a configured provider account.",
                    400, rid, key="configuredProviderRequired", params={"field": pkey},
                )

    for decision_key in ("decision_provider_id", "decision_provider_default_id"):
        if patchable.get(decision_key):
            provider_store = await _get_secret_store(hass)
            if provider_store.resolve(patchable[decision_key], purpose="decision") is None:
                return {}, _err("invalid_request", "Choose a decision provider account.", 400, rid,
                                key="configuredProviderRequired", params={"field": decision_key})
    # The selected default and the account in use move together: using an account
    # selects it, and re-selecting while one is in use switches the one in use.
    # Turning the provider off (decision_provider_id null) keeps the selection.
    current = data.store.get_settings()
    if patchable.get("decision_provider_id") and "decision_provider_default_id" not in patchable:
        patchable["decision_provider_default_id"] = patchable["decision_provider_id"]
    elif (patchable.get("decision_provider_default_id") and "decision_provider_id" not in patchable
            and current.decision_provider_id):
        patchable["decision_provider_id"] = patchable["decision_provider_default_id"]
    if patchable.get("ai_task_decisions_access_key_id"):
        bound = data.store.get_access_key_by_id(patchable["ai_task_decisions_access_key_id"])
        if bound is None or not bound.is_valid():
            return {}, _err(
                "invalid_request", "ai_task_decisions_access_key_id must reference an active access key.",
                400, rid, key="activeAccessKeyRequired", params={"field": "ai_task_decisions_access_key_id"},
            )
    if (patchable.get("decision_shadow_enabled") or patchable.get("ai_task_decisions_enabled")
            or patchable.get("voice_agent_routing_mode") == "hybrid"
            or patchable.get("agentcli_routing_mode") == "hybrid"
            or any(patchable.get(key) in ("observe", "advise") for key in TOOL_ROUTING_SETTINGS.values())):
        account = patchable.get("decision_provider_id", data.store.get_settings().decision_provider_id)
        provider_store = await _get_secret_store(hass)
        if not account or provider_store.resolve(account, purpose="decision") is None:
            return {}, _err("invalid_request", "Choose a decision provider account.", 400, rid,
                            key="decisionProviderRequired")
    if any(patchable.get(key) == "advise" for key in TOOL_ROUTING_SETTINGS.values()):
        provider_store = await _get_secret_store(hass)
        account = patchable.get("decision_provider_id", data.store.get_settings().decision_provider_id)
        if not qualified_languages(provider_store.resolve(account or "", purpose="decision")):
            return {}, _err("invalid_request", "This decision model has not qualified for tool advice.",
                            400, rid, key="toolRoutingUnqualified")
    if "decision_provider_id" in patchable and patchable["decision_provider_id"] is None:
        patchable["decision_shadow_enabled"] = False
        patchable["ai_task_decisions_enabled"] = False
        patchable.update({key: "off" for key in TOOL_ROUTING_SETTINGS.values()})
        for key in ("voice_agent_routing_mode", "agentcli_routing_mode"):
            if getattr(data.store.get_settings(), key) == "hybrid":
                patchable.setdefault(key, "off")
    if any(patchable.get(key) in ("auto", "off")
           for key in ("voice_agent_routing_mode", "agentcli_routing_mode")):
        patchable.update(decision_shadow_enabled=False, agentcli_routing_shadow_enabled=False)
    return patchable, None


async def _apply_settings_side_effects(
    hass: HomeAssistant,
    data: PhoenixData,
    patchable: dict,
    updated: Any,
    old_kill_switch: bool,
) -> bool:
    """Run the live re-syncs a settings change implies, in order.

    Keyed on what moved. Returns whether a torn-down voice assistant is still in Home
    Assistant.
    """
    voice_assistant_kept = False
    recorder(data)
    await data.conversations.async_save()

    if "audit_log_maxlen" in patchable:
        data.audit.resize(patchable["audit_log_maxlen"])

    if "audit_flush_interval" in patchable and data.reschedule_audit_flush is not None:
        data.reschedule_audit_flush()

    if "mesa_mode" in patchable and data.mesa is not None:
        data.mesa.set_mode(updated.mesa_mode)

    if "kill_switch" in patchable and old_kill_switch and not updated.kill_switch:
        # Kill switch just deactivated: re-register routes if not already registered.
        if not data.routes_registered and data.async_register_routes:
            await data.async_register_routes()
            data.routes_registered = True

    if "kill_switch" in patchable:
        # Keep the selected API available with zero tools while agent work is disabled.
        async_register_assist_api(hass, data)

    # Re-sync the Phoenix MCP voice conversation agent when its config or the kill switch
    # changed; the closure reads current settings and registers/unregisters to match.
    voice_touched = any(k.startswith("voice_agent_") for k in patchable)
    if data.async_sync_voice_agent is not None and ("kill_switch" in patchable or voice_touched):
        data.async_sync_voice_agent()

    # If a voice-agent config change left it not fully configured (disabled, or a
    # access key/provider/model cleared), tear down any Phoenix-created Assist pipeline so a
    # broken assistant is not left pointing at the unregistered agent. The kill
    # switch is intentionally excluded (it keeps the agent registered-but-declining,
    # so its pipeline stays valid). No-op when nothing is tracked. Must run AFTER the
    # re-sync above, which is what unregisters the agent.
    if voice_touched:
        now = data.store.get_settings()
        fully = bool(
            now.voice_agent_enabled and now.voice_agent_access_key_id
            and now.voice_agent_provider_id and now.voice_agent_model
        )
        if not fully and now.voice_agent_pipeline_id is not None:
            voice_assistant_kept = await async_remove_assist_pipeline(hass, data)

    # Add/remove the Phoenix MCP AI Task entity to match its config (it exists only while
    # fully configured), so it appears in HA's AI Task picker only when usable.
    if data.async_sync_ai_task is not None and (
        "kill_switch" in patchable or "decision_provider_id" in patchable
        or any(k.startswith("ai_task_") for k in patchable)
    ):
        data.async_sync_ai_task()

    if "mesa_inject_enabled" in patchable:
        # Add/remove the in-context profile injector module. Takes effect on the
        # next full HA page load (already-open tabs are unaffected).
        await async_sync_mesa_inject(hass)

    if "agentcli_global" in patchable:
        # Add/remove the global Agent Chat window module. Takes effect on the
        # next full HA page load (already-open tabs are unaffected).
        await async_sync_agentchat_inject(hass)

    return voice_assistant_kept


class PhoenixAdminRoutingStatisticsView(PhoenixView):
    """Aggregate Phoenix-owned turn statistics, available only to administrators."""

    url = "/api/phoenix-mcp/admin/command-routing/statistics"
    name = "api:phoenix-mcp:admin:command-routing:statistics"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data = self.hass.data[DOMAIN]
        return _ok(data.routing_statistics.summary(), request_id=request["phoenix_mcp_rid"])

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        if set(body) != {"surface", "confirm"} or not _in_menu(body.get("surface"), ROUTING_STATISTICS_SURFACES) or body.get("confirm") is not True:
            return _err("invalid_request", "Choose Voice, Chat or AI Task and confirm the statistics reset.", 400, rid, key="routingStatisticsResetInvalid")
        data = self.hass.data[DOMAIN]
        await data.routing_statistics.async_reset(body["surface"])
        _audit_admin(self.hass, request, rid, request.path)
        return _ok(data.routing_statistics.summary(), request_id=rid)


class PhoenixAdminCommandPreviewView(PhoenixView):
    """Admin-only inert recognition preview; not an MCP tool."""

    url = "/api/phoenix-mcp/admin/command-routing/preview"
    name = "api:phoenix-mcp:admin:command-routing:preview"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: PhoenixData = self.hass.data[DOMAIN]
        return _ok({**data.command_routing.summary(), "decision_shadow": data.decision_shadow.summary(),
                    'request_budget': data.decision_budget.summary(data.store.get_settings().decision_request_budget)},
                   request_id=request["phoenix_mcp_rid"])

    @require_admin
    async def post(self, request: web.Request) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        sentence = body.get("sentence")
        access_key_id = body.get("access_key_id")
        if (set(body) - {"access_key_id", "sentence", "language", "decision", "targets", "messages", "batch", "numeric", "references"}
                or not isinstance(sentence, str) or not sentence.strip()
                or len(sentence) > MAX_SEARCH_QUERY_LEN
                or not isinstance(access_key_id, str)
                or routing_language(body.get("language", "en"), preview=True) is None
                or type(body.get("decision", False)) is not bool
                or type(body.get("references", False)) is not bool
                or (body.get("references") and "messages" not in body)
                or type(body.get("batch", False)) is not bool
                or type(body.get("numeric", False)) is not bool or (body.get("numeric") and not body.get("batch"))
                or (body.get("batch") and (not body.get("decision") or not body.get("targets") or "messages" in body))
                or type(body.get("targets", False)) is not bool or (body.get("targets") and not body.get("decision"))
                or ("messages" in body and (not body.get("decision") or body.get("targets")
                                            or context_history(body["messages"], structured=True) is None))):
            return _err("invalid_request", "Invalid command preview request.", 400, rid,
                        key="routingPreviewInvalid")
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None or not access_key.is_valid():
            return _err("invalid_request", "Select an active access key.", 400, rid,
                        key="activeAccessKeyRequired", params={"field": "access_key_id"})
        authority = RoutingAuthority.capture(data, access_key)
        language = body.get("language", "en")
        result = await async_evaluate_command(self.hass, data, access_key, sentence, language, preview=True)
        if "messages" in body:
            reviewer = async_preview_context
            if body.get("references"):
                reviewer = async_preview_reference
            result["context_review"] = await reviewer(
                self.hass, data, access_key, sentence, language, body["messages"], result)
            if result["context_review"]["reason"] in ("context_changed", "authority_changed"):
                result.update(route="provider", reason=result["context_review"]["reason"], proposal=None)
        elif body.get("batch"):
            result["batch_review"] = await async_preview_batch(self.hass, data, access_key, sentence, language, result,
                                                             numeric_preview=body.get("numeric", False))
            if result["batch_review"]["reason"] in ("targets_changed", "authority_changed"):
                result.update(route="provider", reason=result["batch_review"]["reason"], proposal=None)
        elif body.get("decision"):
            result["decision_shadow"] = await async_preview_decision(
                self.hass, data, access_key, sentence, language, result, targets=body.get("targets", False), ingress="preview")
        if authority.current(self.hass) is None:
            result = {"route": "provider", "reason": "authority_changed", "proposal": None,
                      "executed": False, "elapsed_ms": result["elapsed_ms"]}
        return _ok({**result, "voice_observations": data.command_routing.summary(),
                    "decision_observations": data.decision_shadow.summary()}, request_id=rid)


class PhoenixAdminSettingsView(PhoenixView):
    """GET/PATCH /api/phoenix-mcp/admin/settings - read or update global integration settings."""

    url = "/api/phoenix-mcp/admin/settings"
    name = "api:phoenix-mcp:admin:settings"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: PhoenixData = self.hass.data[DOMAIN]
        return _ok(_settings_response(data.store.get_settings(), self.hass), request_id=request["phoenix_mcp_rid"])

    @require_admin
    async def patch(self, request: web.Request) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        # Keep persistence and live reconciliation in one request-order critical
        # section. Reference validation belongs here too: access key archival and
        # provider deletion participate in this lock, so a target proved live
        # here cannot disappear before the settings write.
        async with data.settings_update_lock:
            patchable, invalid = await _validated_settings_patch(
                body, data, self.hass, rid
            )
            if invalid is not None:
                return invalid

            async with data.store.async_lock:
                data.decision_shadow.cancel()
                settings_before = data.store.get_settings()
                old_kill_switch = data.store.get_settings().kill_switch
                old_presets_enabled = data.store.get_settings().access_key_presets_enabled
                updated = await data.store.async_patch_settings(**patchable)
                if updated.access_key_presets_enabled and not old_presets_enabled:
                    # Feature just enabled: every access key gets a "Default preset" from
                    # its current state (workspace model). Idempotent on re-enables.
                    seeded = await data.store.async_seed_default_presets()
                    if seeded:
                        _LOGGER.info("Access key presets enabled; seeded a default preset on %d access key(s)", seeded)

            voice_assistant_kept = await _apply_settings_side_effects(
                self.hass, data, patchable, updated, old_kill_switch,
            )

        user = request[KEY_HASS_USER]
        # The change that turns logging off is recorded under the settings it
        # replaced, so the log says when it went quiet, and the row names what
        # changed. Field names only: values can be secret.
        logging_on = not updated.disable_all_logging and updated.log_allowed
        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=updated if logging_on else settings_before,
            payload={"changed": sorted(patchable)},
        )
        body = _settings_response(updated, self.hass)
        if voice_assistant_kept:
            body["voice_agent_pipeline_kept"] = True
        return _ok(body, request_id=rid)


class PhoenixAdminVoiceAgentPipelineView(PhoenixView):
    """POST/DELETE /api/phoenix-mcp/admin/voice_agent/pipeline - one-click Assist setup.

    POST creates an Assist pipeline pointed at Phoenix MCP's conversation agent (optionally
    the preferred assistant) so the operator does not have to wire it up by hand;
    DELETE removes the Phoenix-created pipeline again. Admin session auth only.
    """

    url = "/api/phoenix-mcp/admin/voice_agent/pipeline"
    name = "api:phoenix-mcp:admin:voice_agent:pipeline"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        preferred = body.get("preferred", True)
        if not isinstance(preferred, bool):
            return _err(
                "invalid_request", "preferred must be a boolean.", 400, rid,
                key="booleanRequired", params={"field": "preferred"},
            )

        entries = self.hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return _err(
                "not_found", "Phoenix MCP is not set up.", 404, rid,
                key="setupMissing",
            )

        try:
            async with data.settings_update_lock:
                result = await async_create_assist_pipeline(
                    self.hass, entries[0], data, preferred=preferred
                )
        except VoicePipelineError as err:
            _LOGGER.warning("Assist pipeline setup failed", exc_info=True)
            # Each cause carries its own message key; an unexpected one keeps the general message.
            reason = err.args[0] if err.args and isinstance(err.args[0], LocalizedReason) else LocalizedReason(
                str(err) or "Could not create the Phoenix MCP Assist pipeline.", "assistPipelineFailed")
            return _err("invalid_request", str(reason), 400, rid, key=reason.key)
        _audit_admin(self.hass, request, rid, "voice_agent_pipeline")
        return _ok(result, request_id=rid)

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        async with data.settings_update_lock:
            kept = await async_remove_assist_pipeline(self.hass, data)
        _audit_admin(self.hass, request, rid, "voice_agent_pipeline")
        # kept: Home Assistant still has the pipeline (its only one, or deletion failed).
        return _ok({"ok": True, "kept": kept}, request_id=rid)


class PhoenixAdminVoiceAgentPreferredView(PhoenixView):
    """POST/DELETE /api/phoenix-mcp/admin/voice_agent/pipeline/preferred.

    POST makes the Phoenix-created Assist pipeline preferred; DELETE stops that but keeps it.
    Admin session auth only.
    """

    url = "/api/phoenix-mcp/admin/voice_agent/pipeline/preferred"
    name = "api:phoenix-mcp:admin:voice_agent:pipeline:preferred"
    requires_auth = True

    async def _change(self, request: web.Request, preferred: bool) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        try:
            async with data.settings_update_lock:
                result = await async_set_assist_pipeline_preferred(self.hass, data, preferred=preferred)
        except VoicePipelineError as err:
            _LOGGER.warning("Assist pipeline preference change failed", exc_info=True)
            # Each cause carries its own message key; an unexpected one keeps the general message.
            reason = err.args[0] if err.args and isinstance(err.args[0], LocalizedReason) else LocalizedReason(
                str(err) or "Could not change the preferred assistant.", "assistantPreferredFailed")
            return _err("invalid_request", str(reason), 400, rid, key=reason.key)
        _audit_admin(self.hass, request, rid, "voice_agent_pipeline")
        return _ok(result, request_id=rid)

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        return await self._change(request, True)

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        return await self._change(request, False)


class PhoenixAdminAiTaskPreferredView(PhoenixView):
    """GET/POST/DELETE /api/phoenix-mcp/admin/ai_task/preferred - the default AI Task entity.

    POST makes Phoenix's entity HA's default, overwriting any prior one; DELETE clears it if
    it is Phoenix's; GET reports the state, so the panel can warn first. Admin session auth
    only.
    """

    url = "/api/phoenix-mcp/admin/ai_task/preferred"
    name = "api:phoenix-mcp:admin:ai_task:preferred"
    requires_auth = True

    def _entry(self) -> Any:
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return entries[0] if entries else None

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        entry = self._entry()
        if entry is None:
            return _err(
                "not_found", "Phoenix MCP is not set up.", 404, rid,
                key="setupMissing",
            )
        return _ok(ai_task_preferred_status(self.hass, entry), request_id=rid)

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        entry = self._entry()
        if entry is None:
            return _err(
                "not_found", "Phoenix MCP is not set up.", 404, rid,
                key="setupMissing",
            )
        try:
            result = set_ai_task_preferred(self.hass, entry)
            _audit_admin(self.hass, request, rid, "ai_task_preferred")
            return _ok(result, request_id=rid)
        except AiTaskSetupError as err:
            _LOGGER.warning("AI Task preferred setup failed", exc_info=True)
            # Each cause carries its own message key; an unexpected one keeps the general message.
            reason = err.args[0] if err.args and isinstance(err.args[0], LocalizedReason) else LocalizedReason(
                str(err) or "Could not configure the preferred AI Task entity.", "aiTaskPreferredFailed")
            return _err("invalid_request", str(reason), 400, rid, key=reason.key)

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        entry = self._entry()
        if entry is None:
            return _err(
                "not_found", "Phoenix MCP is not set up.", 404, rid,
                key="setupMissing",
            )
        result = clear_ai_task_preferred(self.hass, entry)
        _audit_admin(self.hass, request, rid, "ai_task_preferred")
        return _ok(result, request_id=rid)


class PhoenixAdminAiEvaluatePreferredView(PhoenixView):
    """GET/POST/DELETE /api/phoenix-mcp/admin/ai_task/evaluate_preferred - the default Decisions entity.

    POST makes Phoenix's entity HA's default, overwriting any prior one; DELETE clears it if
    it is Phoenix's; GET reports the state. Admin session auth only.
    """

    url = "/api/phoenix-mcp/admin/ai_task/evaluate_preferred"
    name = "api:phoenix-mcp:admin:ai_task:evaluate_preferred"
    requires_auth = True

    def _entry(self) -> Any:
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return entries[0] if entries else None

    def _missing(self, rid: str) -> web.Response:
        return _err("not_found", "Phoenix MCP is not set up.", 404, rid, key="setupMissing")

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        entry = self._entry()
        if entry is None:
            return self._missing(rid)
        return _ok(ai_evaluate_preferred_status(self.hass, entry), request_id=rid)

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        entry = self._entry()
        if entry is None:
            return self._missing(rid)
        try:
            result = set_ai_evaluate_preferred(self.hass, entry)
        except AiTaskSetupError as err:
            reason = err.args[0]
            return _err("invalid_request", str(reason), 400, rid, key=reason.key)
        _audit_admin(self.hass, request, rid, "ai_evaluate_preferred")
        return _ok(result, request_id=rid)

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        entry = self._entry()
        if entry is None:
            return self._missing(rid)
        result = clear_ai_evaluate_preferred(self.hass, entry)
        _audit_admin(self.hass, request, rid, "ai_evaluate_preferred")
        return _ok(result, request_id=rid)


def _wipe_flag(value: Any, default: bool) -> bool:
    """Coerce a wipe-scope flag: honor a real bool, else fall back to default."""
    return value if isinstance(value, bool) else default


class PhoenixAdminWipeView(PhoenixView):
    """DELETE /api/phoenix-mcp/admin/wipe - selectively wipe Phoenix MCP data.

    Body flags (all optional): wipe_core (access keys/audit/versions/settings,
    default True), wipe_providers (Agent Chat provider keys, default True),
    wipe_mesa (MESA profiles, default False).
    """

    url = "/api/phoenix-mcp/admin/wipe"
    name = "api:phoenix-mcp:admin:wipe"
    requires_auth = True

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        if body.get("confirm") != "WIPE":
            return _err(
                "invalid_request", 'confirm must be "WIPE".', 400, rid,
                key="wipeConfirmationRequired",
            )

        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        # Selective scope. A non-boolean value (garbage) falls back to the
        # default, which for a destructive op means the safe direction: the two
        # credential-bearing scopes default on, MESA (authored safety policy)
        # defaults off, matching the panel's checkbox defaults.
        wipe_core = _wipe_flag(body.get("wipe_core"), True)
        wipe_providers = _wipe_flag(body.get("wipe_providers"), True)
        wipe_mesa = _wipe_flag(body.get("wipe_mesa"), False)

        if wipe_core:

            # Settings PATCH validates access key references under this lock and then
            # takes the store lock. Core wipe must use the same order so it cannot
            # remove every access key between validation and settings persistence.
            async with data.settings_update_lock:
                async with data.store.async_lock:
                    if data.approvals_in_progress:
                        return _err(
                            "conflict",
                            "An approval is still executing. Wait for it to finish before wiping core data.",
                            409, rid, key="approvalWipeInProgress",
                        )
                    interrupted = any(
                        entry.get("status") == "pending" and entry.get("execution_started_at")
                        for entry in data.store.get_pending_approvals()
                    )
                    if interrupted:
                        return _err(
                            "conflict",
                            "An approval execution may already have applied. Check its result before wiping core data.",
                            409, rid, key="approvalWipeExecutionUncertain",
                        )
                    data.core_wipe_in_progress = True

                try:
                    # Logger restoration records live outside hass.data[DOMAIN]
                    # so a Phoenix reload cannot strand a timed logger restoration.
                    if data.logger_control is not None:
                        await data.logger_control.async_restore_all()

                    # Pipeline creation uses this same lock through HA creation
                    # and id persistence. Remove it before resetting settings.
                    await async_remove_assist_pipeline(hass, data)

                    async with data.store.async_lock:
                        data.rate_limiter.destroy_all()
                        data.rate_limit_notified.clear()
                        data.access_key_counters.clear()
                        data.stale_tools_advised.clear()
                        data.command_routing.clarifications.clear()
                        await data.decision_budget.async_wipe()
                        await data.routing_statistics.async_wipe()
                        await data.audit.async_wipe()
                        await data.conversations.async_clear()
                        await data.versions.async_wipe()
                        await data.agent_recovery.async_wipe(self.hass)
                        # The card catalog is a regenerable cache of what this instance
                        # can render, not user data, but a wipe must not leave a file
                        # behind. The panel re-harvests on its next load.
                        await data.card_catalog.async_clear()

                        for _tid in list(data.expiry_timers):
                            cancel_expiry_timer(data, _tid)

                        # Capture pending approvals BEFORE the wipe clears them, so
                        # their notifications can be dismissed and resolved events
                        # fired after releasing the lock.
                        wiped_approvals = collect_pending_approvals_for_wipe(
                            data.store, REASON_WIPED,
                        )
                        active_slugs = [access_key_name_slug(t.name) for t in data.store.list_access_keys()]
                        settings_before = data.store.get_settings()
                        await data.store.async_wipe()
                finally:
                    data.core_wipe_in_progress = False

            for approval in wiped_approvals:
                dismiss_approval_notification(hass, approval.id)
                fire_approval_resolved_event(hass, approval)

            if not data.routes_registered and data.async_register_routes:
                await data.async_register_routes()
                data.routes_registered = True

            # Sensor removal runs after the lock is released. A concurrent access key creation
            # with the same slug as a just-wiped access key could have its sensors removed here.
            # This race is accepted: wipe is a destructive admin operation and should not
            # be run concurrently with access key creation.
            if data.async_on_access_key_archived:
                await asyncio.gather(
                    *[data.async_on_access_key_archived(slug) for slug in active_slugs],
                    return_exceptions=True,
                )
            # Clear unconditionally: if any sensor removal above failed and left stale
            # entries in access_key_id_sensors, they would cause async_write_ha_state() to
            # be called for access key IDs that no longer exist. Since the wipe removes all
            # access keys, there are no valid sensors left regardless.
            data.access_key_id_sensors.clear()

            # Every setting is back at its default. Apply each one that moved the way a
            # settings change would, so what runs matches what Settings shows: a MESA
            # mode left Off while Settings read Advisory skipped privacy and read-only
            # rules. This also re-syncs the Voice Agent and AI Task (no longer
            # configured), the Assist API after a kill switch, the audit log size and
            # save timer, and the in-context modules.
            settings_after = data.store.get_settings()
            reset = {
                key: value for key, value in settings_after.to_dict().items()
                if getattr(settings_before, key, value) != value
            }
            if reset:
                await _apply_settings_side_effects(hass, data, reset, settings_after, settings_before.kill_switch)

        if wipe_providers:
            # Provider-only wipe is a supported scope, so it must clear the
            # dependent settings just like deleting the referenced account.
            # Serialize the provider removal and settings cleanup against PATCH
            # validation to prevent a freshly validated dead reference.
            async with data.settings_update_lock:
                await async_wipe_agentcli_secrets(hass)
                async with data.store.async_lock:
                    settings = data.store.get_settings()
                    voice_provider_removed = bool(settings.voice_agent_provider_id)
                    ai_task_provider_removed = bool(settings.ai_task_provider_id)
                    provider_patch: dict[str, Any] = {}
                    if voice_provider_removed:
                        provider_patch.update(
                            voice_agent_provider_id=None,
                            voice_agent_model=None,
                        )
                    if ai_task_provider_removed:
                        provider_patch.update(
                            ai_task_provider_id=None,
                            ai_task_model=None,
                        )
                    decision_removed = bool(settings.decision_provider_id)
                    if decision_removed:
                        provider_patch.update(decision_provider_id=None, decision_shadow_enabled=False,
                                              ai_task_decisions_enabled=False,
                                              **{key: "off" for key in TOOL_ROUTING_SETTINGS.values()})
                        for key in ("voice_agent_routing_mode", "agentcli_routing_mode"):
                            if getattr(settings, key) == "hybrid":
                                provider_patch[key] = "off"
                    if settings.decision_provider_default_id:
                        provider_patch["decision_provider_default_id"] = None
                    if provider_patch:
                        await data.store.async_patch_settings(**provider_patch)

                if voice_provider_removed:
                    if data.async_sync_voice_agent is not None:
                        data.async_sync_voice_agent()
                    await async_remove_assist_pipeline(hass, data)
                if (ai_task_provider_removed or decision_removed) and data.async_sync_ai_task is not None:
                    data.async_sync_ai_task()

        if wipe_mesa:
            if data.mesa is not None:
                async with data.mesa.lock:
                    await data.mesa.async_wipe()
            else:
                # MESA runtime unavailable (setup failed or off): clear the
                # on-disk store directly so "delete MESA data" still holds.
                mesa_store: DurableStore[dict] = DurableStore(
                    hass, MESA_STORAGE_VERSION, MESA_STORAGE_KEY, atomic_writes=True
                )
                try:
                    await mesa_store.async_save(
                        {"profiles": {}, "dismissed_suggestions": []}
                    )
                except DurableStorageError as err:
                    raise MesaSaveError("MESA profiles could not be cleared.") from err

        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        return web.Response(status=204, headers={"X-Phoenix-Request-ID": rid})


class PhoenixAdminCardCatalogView(PhoenixView):
    """GET/POST /api/phoenix-mcp/admin/card_catalog - the dashboard card catalog.

    POST is the panel harvester; card_catalog sanitizes the third-party payload and this view
    bounds it. GET reports harvested_at as None when no browser has reported, never "no
    cards".
    """

    url = "/api/phoenix-mcp/admin/card_catalog"
    name = "api:phoenix-mcp:admin:card_catalog"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        catalog = data.card_catalog.catalog
        return _ok({
            "harvested": catalog.harvested,
            **data.card_catalog.as_dict(),
        }, request_id=rid)

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        if not isinstance(body.get("entries"), list):
            return _err(
                "invalid_request", "entries must be a list.", 400, rid,
                key="entriesListRequired",
            )

        resources = await async_lovelace_resources(self.hass)
        catalog = await data.card_catalog.async_replace(
            entries=body.get("entries"),
            resource_count=body.get("resource_count"),
            failed_imports=body.get("failed_imports"),
            resources=resources.items if resources is not None else None,
        )
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({
            "harvested": catalog.harvested,
            "harvested_at": catalog.harvested_at,
            "cards": len(catalog.entries),
            "available": sum(1 for e in catalog.entries if e.available),
            "failed_imports": len(catalog.failed_imports),
        }, request_id=rid)
