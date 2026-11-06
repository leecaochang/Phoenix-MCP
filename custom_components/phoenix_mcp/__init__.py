"""Phoenix MCP custom integration for Home Assistant."""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant, Event, EventType, callback
from homeassistant.helpers import area_registry as ar_mod
from homeassistant.helpers import device_registry as dr_mod
from homeassistant.helpers import entity_registry as er_mod
from homeassistant.helpers import label_registry as lr_mod
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from .audit import AuditLog
from .version_store import VersionStore
from .agent_recovery import AgentRecovery, STORAGE_KEY as AGENT_RECOVERY_STORAGE_KEY
from .card_catalog import CardCatalogStore
from .const import (
    APPROVAL_SWEEP_INTERVAL, AUDIT_STORAGE_KEY, AUDIT_STORAGE_VERSION, CARD_CATALOG_STORAGE_KEY,
    CARD_CATALOG_STORAGE_VERSION, CONFIG_RECOVERY_SWEEP_INTERVAL, CONVERSATION_LOG_FLUSH_SECONDS,
    CONVERSATION_LOG_STORAGE_KEY, DASHBOARD_RESOURCE_OWNERSHIP_STORAGE_KEY, DOMAIN,
    EXPIRY_CHECK_INTERVAL, FLUSH_INTERVAL, MIN_HA_VERSION, RUNTIME_READY_KEY, SENSOR_PUSH_INTERVAL,
    VERSION_STORAGE_KEY, VERSION_STORAGE_VERSION,
)
from .provider_constants import AGENT_CHAT_CATALOG_TICK_INTERVAL
from .routing.routing_tables import (
    DECISION_BUDGET_STORAGE_KEY, ROUTING_STATISTICS_FLUSH_INTERVAL, ROUTING_STATISTICS_STORAGE_KEY,
)
from .data import DurableState, PhoenixData
from .log_scrub import install_traceback_scrub
from .helpers import RepeatedFailureLog, cancel_expiry_timer
from .catalog_text import async_load_catalogs
from .policy_engine import template_blocklist_vars
from .rate_limiter import RateLimiter
from .access_key_store import AccessKeyStore

# HA-added template names (globals, filters, and tests) that are safe: pure
# math/string/data helpers with no entity state or registry access. When the
# runtime audit runs, any name HA adds to the render environment that is not in
# this set and not in the blocklist triggers a warning so new HA template
# functions don't silently bypass Phoenix MCP filtering.
_SAFE_TEMPLATE_NAMES = frozenset({
    "bool", "float", "int", "version", "typeof", "is_number",
    "zip", "apply", "combine", "iif", "as_function", "pack", "unpack",
    "merge_response", "e", "pi", "tau", "sin", "cos", "tan",
    "asin", "acos", "atan", "atan2", "log", "sqrt", "average",
    "median", "statistical_mode", "min", "max", "bitwise_and",
    "bitwise_or", "bitwise_xor", "clamp", "wrap", "remap",
    "slugify", "urlencode", "md5", "sha1", "sha256", "sha512",
    "flatten", "shuffle", "intersect", "difference", "union",
    "symmetric_difference", "set", "tuple", "as_datetime",
    "as_local", "as_timedelta", "as_timestamp", "strptime",
    "timedelta", "now", "utcnow", "relative_time", "time_since",
    "time_until", "today_at",
    "range", "lipsum", "dict", "cycler", "joiner", "namespace", "undefined",
    # Filter-only names.
    "add", "base64_decode", "base64_encode", "contains", "from_hex",
    "from_json", "is_defined", "multiply", "ord", "ordinal",
    "regex_findall", "regex_findall_index", "regex_match", "regex_replace",
    "regex_search", "timestamp_custom", "timestamp_local", "timestamp_utc",
    "to_json", "random", "round",
    # Test-only names.
    "datetime", "list", "match", "search", "string_like",
})

_LOGGER = logging.getLogger(__name__)
install_traceback_scrub(_LOGGER)

PLATFORMS = ["sensor"]

# Every first-party module setup reaches. Home Assistant imports this package in
# its import executor, but an import inside a function first runs where the
# function does, and setup runs on the event loop: reading and compiling these
# there stalls all of Home Assistant, and its loop guard does not see an import
# statement. They stay out of the top of this file so that a Home Assistant too
# old for them still reaches the refusal in async_setup_entry.
_SETUP_MODULES = (
    "access_key_auth", "admin.registry", "agent_chat", "agent_chat_views", "ai_task", "approvals",
    "assist_api", "config_reconciliation", "config_recovery", "conversation_log",
    "dashboard_resource_ownership", "debug_capture", "durable_storage", "llm.accounts",
    "log_read", "logger_control", "mcp_transport", "mesa", "mesa_audit", "mesa_core",
    "mesa_suggestions", "panel", "recovery_storage", "routing.decision_budget",
    "routing.routing_statistics", "sensor", "skill_view", "template_sandbox", "tool_registration",
    "tools.calendar", "voice_agent", "ws_dispatch",
)


def _import_setup_modules() -> None:
    """Load what setup needs. Runs in Home Assistant's import executor."""
    for name in _SETUP_MODULES:
        importlib.import_module(f"{__package__}.{name}")


@callback
def _warn_on_computed_alias_loss(hass: HomeAssistant, event: Event) -> None:
    """Warn when an external registry update removes the computed entity name."""
    event_data = event.data
    if event_data.get("action") != "update":
        return
    changes = event_data.get("changes")
    if not isinstance(changes, dict) or "aliases" not in changes:
        return
    old_aliases = changes["aliases"]
    if not isinstance(old_aliases, list) or er_mod.COMPUTED_NAME not in old_aliases:
        return

    entity_id = event_data.get("entity_id")
    if not isinstance(entity_id, str):
        return
    entry = er_mod.async_get(hass).async_get(entity_id)
    if entry is None or er_mod.COMPUTED_NAME in entry.aliases:
        return

    _LOGGER.warning(
        "Phoenix MCP detected an entity registry update that removed Home "
        "Assistant's computed-name alias from %s. The entity may stop "
        "responding to its current name in Assist. No automatic repair was "
        "applied.",
        entity_id,
    )


def _entry_platforms() -> list[str]:
    """Return all platforms this entry may forward, the unload fallback with no runtime.

    Unload otherwise follows what was forwarded, as some HA releases raise on the rest.
    """
    from .ai_task import ai_task_supported

    platforms = list(PLATFORMS)
    if ai_task_supported():
        platforms.append("ai_task")
    return platforms


def _audit_template_sandbox() -> None:
    """Warn about unrecognized names in Phoenix MCP's template render environment.

    Audits the hass-less environment's globals, filters and tests at setup, so a function HA
    adds cannot silently bypass entity filtering.
    """
    try:
        from .template_sandbox import safe_template_env

        env = safe_template_env()
    except Exception:
        # Construction failing means every render_template call will fail the
        # same way: HA's TemplateEnvironment has changed shape. Surface it once,
        # loudly, at startup instead of only as per-call invalid_request errors.
        _LOGGER.warning(
            "Phoenix MCP: template sandbox environment failed to construct; the "
            "render_template tool will fail per-call on this HA version",
            exc_info=True,
        )
        return
    try:
        import jinja2.sandbox

        blocked = set(template_blocklist_vars().keys())
        overridden = {"states", "state_attr", "is_state", "is_state_attr", "has_value"}
        known = blocked | overridden | _SAFE_TEMPLATE_NAMES
        base = jinja2.sandbox.ImmutableSandboxedEnvironment()
        for kind, names, base_names in (
            ("global", env.globals, base.globals),
            ("filter", env.filters, base.filters),
            ("test", env.tests, base.tests),
        ):
            for name in set(names) - set(base_names):
                if name not in known and not name.startswith("_"):
                    _LOGGER.warning(
                        "Phoenix MCP template sandbox: unrecognized HA template %s '%s' - "
                        "this function is not blocked and may bypass entity filtering",
                        kind,
                        name,
                    )
    except Exception:
        _LOGGER.debug("Phoenix MCP: could not audit template environment", exc_info=True)


# The single DurableState of this Home Assistant process, under its own key
# because unload pops hass.data[DOMAIN] while handlers may still be writing.
_DURABLE_STATE_KEY = "phoenix_mcp_durable_state"

# Names of the view classes already registered on HA's aiohttp router, kept in
# hass.data under its own key so it survives unload: async_unload_entry pops
# hass.data[DOMAIN], but the ROUTES it registered cannot be unregistered and are
# still there. Same pattern as panel._PANEL_REGISTERED_KEY.
_VIEWS_REGISTERED_KEY = "phoenix_mcp_registered_views"


@callback
def _listen_once_until_unload(
    hass: HomeAssistant,
    entry: ConfigEntry,
    event_type: EventType | str,
    listener: Callable,
) -> None:
    """Listen for one event, unregistering on unload only if it never fired.

    async_listen_once removes its own listener on firing, so a plain unload hook removes it
    twice and HA logs an error. Wrapping keeps a genuine double-remove visible.
    """
    fired = False

    def _mark() -> None:
        nonlocal fired
        fired = True

    # inspect, not asyncio: asyncio.iscoroutinefunction is deprecated for 3.16.
    # inspect.iscoroutinefunction reads the function's own coroutine flag and
    # evaluates no annotations.
    #
    # The two wrappers have DISTINCT names rather than one conditional `_once`,
    # because a coroutine and a callback are different signatures and a shared
    # name is a redefinition. The async branch has to stay async: wrapping an
    # async listener in a @callback would leave its coroutine unawaited and
    # silently skip the shutdown flush _on_stop performs.
    if inspect.iscoroutinefunction(listener):
        async def _async_once(event: Event) -> None:
            _mark()
            await listener(event)

        remove = hass.bus.async_listen_once(event_type, _async_once)
    else:
        @callback
        def _sync_once(event: Event) -> None:
            _mark()
            listener(event)

        remove = hass.bus.async_listen_once(event_type, _sync_once)

    @callback
    def _remove_if_still_pending() -> None:
        if not fired:
            remove()

    entry.async_on_unload(_remove_if_still_pending)


@callback
def _register_views(hass: HomeAssistant, view_classes: list) -> None:
    """Register each view class once per Home Assistant process.

    aiohttp cannot remove a route, and a repeat silently shadows the first set. Views hold no
    per-setup state, so existing routes serve the new setup.
    """
    registered: set[str] = hass.data.setdefault(_VIEWS_REGISTERED_KEY, set())
    for view_cls in view_classes:
        if view_cls.__name__ in registered:
            continue
        view = view_cls()
        view.hass = hass
        hass.http.register_view(view)
        registered.add(view_cls.__name__)


async def _async_shutdown_mesa_workers() -> None:
    """Drain policy callbacks while HA's loop and executor are still available."""
    from .mesa_core import ashutdown_policy_workers

    try:
        await ashutdown_policy_workers()
    except Exception:
        _LOGGER.exception("Phoenix MCP: could not shut down MESA policy workers")


async def _rollback_failed_setup(
    hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData | None
) -> None:
    """Remove every surface a partial setup could have published."""
    hass.data[RUNTIME_READY_KEY] = False
    if data is not None:
        data.ready = False
        data.shutting_down = True
        await data.conversations.async_save()
        await data.decision_shadow.async_cancel()
        await _async_shutdown_mesa_workers()
        try:
            from .assist_api import async_unregister_assist_api

            async_unregister_assist_api(data)
        except Exception:  # rollback continues through every surface
            _LOGGER.exception("Phoenix MCP: failed to unregister Assist during setup rollback")
        try:
            from .voice_agent import async_unregister_voice_agent

            async_unregister_voice_agent(hass, entry)
        except Exception:  # rollback continues through every surface
            _LOGGER.exception("Phoenix MCP: failed to unregister voice during setup rollback")
        try:
            from .debug_capture import async_detach_debug_capture

            async_detach_debug_capture(hass)
        except Exception:  # rollback continues through every surface
            _LOGGER.exception("Phoenix MCP: failed to detach debug capture during setup rollback")

    _remove_frontend(hass)
    try:
        if data is not None and data.forwarded_platforms:
            await hass.config_entries.async_unload_platforms(entry, sorted(data.forwarded_platforms))
    except Exception:  # preserve the original setup exception
        _LOGGER.exception("Phoenix MCP: failed to unload platforms during setup rollback")
    if data is not None and hass.data.get(DOMAIN) is data:
        hass.data.pop(DOMAIN, None)


def _ha_version_supported() -> tuple[bool, str]:
    """Whether the running Home Assistant meets MIN_HA_VERSION, and its version; unparsable passes."""
    from awesomeversion import AwesomeVersion
    from homeassistant.const import __version__ as ha_version


    try:
        return AwesomeVersion(ha_version) >= AwesomeVersion(MIN_HA_VERSION), ha_version
    except Exception:  # an unreadable version must not block setup
        return True, ha_version


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Phoenix MCP atomically from a config entry."""
    # HACS checks the version before installing; a manual install does not, so
    # setup refuses clearly here rather than failing somewhere later.
    supported, ha_version = _ha_version_supported()
    if not supported:
        _LOGGER.error(
            "Phoenix MCP needs Home Assistant %s or newer, and this is %s. It was not set up.",
            MIN_HA_VERSION, ha_version,
        )
        return False
    hass.data[RUNTIME_READY_KEY] = False
    try:
        await hass.async_add_import_executor_job(_import_setup_modules)
        result = await _async_setup_entry_impl(hass, entry)
    except BaseException:
        await _rollback_failed_setup(hass, entry, hass.data.get(DOMAIN))
        raise
    if not result:
        await _rollback_failed_setup(hass, entry, hass.data.get(DOMAIN))
    return result


async def _async_durable_state(hass: HomeAssistant) -> DurableState:
    """Return this process's storage owners, loading them on first use only.

    A failed load publishes nothing. A reload reuses the state, so no second owner appears.
    """
    state: DurableState | None = hass.data.get(_DURABLE_STATE_KEY)
    if state is not None:
        # Reloading stays the way to retry a request ledger that could not be read.
        await state.decision_budget.async_retry_load()
        return state

    store = await AccessKeyStore.async_create(hass)
    # Private like the Conversation Log's: tool arguments can name people and places.
    from .durable_storage import DurableStore, ReportingStore
    audit_store: ReportingStore[dict] = ReportingStore(hass, AUDIT_STORAGE_VERSION, AUDIT_STORAGE_KEY, private=True)
    audit = AuditLog(store=audit_store, maxlen=store.get_settings().audit_log_maxlen)
    await audit.async_load()
    from .conversation_log import ConversationLog
    conversations = ConversationLog(ReportingStore(hass, 1, CONVERSATION_LOG_STORAGE_KEY,
                                                    private=True, atomic_writes=True, serialize_in_event_loop=False))
    conversations.configure(store.get_settings())
    await conversations.async_load()

    versions = VersionStore(store=Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY))
    await versions.async_load()

    card_catalog = CardCatalogStore(
        store=Store(hass, CARD_CATALOG_STORAGE_VERSION, CARD_CATALOG_STORAGE_KEY)
    )
    await card_catalog.async_load()

    from .config_recovery import ConfigurationRecovery, ConfigurationRecoveryError
    from .recovery_storage import RecoveryStorage
    config_recovery = ConfigurationRecovery(RecoveryStorage(hass), hass=hass)
    try:
        await config_recovery.async_load()
    except ConfigurationRecoveryError:
        _LOGGER.error("Configuration recovery storage unavailable; protected writes will retry initialization")

    agent_recovery = AgentRecovery(DurableStore(hass, 1, AGENT_RECOVERY_STORAGE_KEY, atomic_writes=True))
    await agent_recovery.async_load()

    from .dashboard_resource_ownership import DashboardResourceOwnership
    dashboard_resources = DashboardResourceOwnership(
        DurableStore(hass, 1, DASHBOARD_RESOURCE_OWNERSHIP_STORAGE_KEY, atomic_writes=True))
    await dashboard_resources.async_load()

    from .routing.routing_statistics import RoutingStatistics
    routing_statistics = RoutingStatistics(ReportingStore(hass, 1, ROUTING_STATISTICS_STORAGE_KEY))
    await routing_statistics.async_load()
    from .routing.decision_budget import DecisionRequestBudget
    decision_budget = DecisionRequestBudget(DurableStore(hass, 1, DECISION_BUDGET_STORAGE_KEY, atomic_writes=True))
    await decision_budget.async_load()

    from .tools.calendar import CalendarWriteJournal
    calendar_writes = CalendarWriteJournal(hass)
    try:
        async with calendar_writes.lock:
            await calendar_writes.load()
    except Exception:
        calendar_writes.unavailable = True
        _LOGGER.error("Calendar recovery storage unavailable; calendar writes will retry initialization")

    state = DurableState(
        store=store,
        audit=audit,
        conversations=conversations,
        versions=versions,
        card_catalog=card_catalog,
        config_recovery=config_recovery,
        agent_recovery=agent_recovery,
        dashboard_resources=dashboard_resources,
        routing_statistics=routing_statistics,
        decision_budget=decision_budget,
        calendar_writes=calendar_writes,
    )
    hass.data[_DURABLE_STATE_KEY] = state
    return state


async def _async_build_runtime(hass: HomeAssistant) -> tuple[PhoenixData, DurableState]:
    """Load durable state and publish the runtime under hass.data."""
    await async_load_catalogs(hass)
    state = await _async_durable_state(hass)
    store, audit, agent_recovery = state.store, state.audit, state.agent_recovery
    rate_limiter = RateLimiter()
    state.conversations.configure(store.get_settings())

    data = PhoenixData(
        decision_budget=state.decision_budget,
        hass=hass,
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
        versions=state.versions,
        agent_recovery=agent_recovery,
        config_recovery=state.config_recovery,
        calendar_writes=state.calendar_writes,
        dashboard_resources=state.dashboard_resources,
        routing_statistics=state.routing_statistics,
        conversations=state.conversations,
        card_catalog=state.card_catalog,
        approvals_in_progress=state.approvals_in_progress,
        ready=False,
    )
    # hass.data is keyed by DOMAIN (not config entry ID). This is intentional: the config
    # flow enforces a single Phoenix MCP instance via async_abort("already_configured"), so there
    # is always at most one entry. Keying by entry ID would add complexity for no benefit.
    hass.data[DOMAIN] = data
    # Migrate presentation only. Durable recovery records remain protected.
    for issue_domain, issue_id in list(ir.async_get(hass).issues):
        if issue_domain == DOMAIN and (issue_id == "mesa_runtime_unavailable"
                                      or issue_id.startswith(("agent_recovery_", "calendar_recovery_"))):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    return data, state


async def _async_attach_diagnostics(hass: HomeAssistant, data: PhoenixData) -> None:
    """Take over logger overrides and start the debug capture."""
    from .logger_control import async_get_logger_override_manager
    data.logger_control = await async_get_logger_override_manager(hass)
    from .debug_capture import async_attach_debug_capture
    async_attach_debug_capture(hass)


async def _async_start_mesa(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> None:
    """Build the MESA runtime and bridge its denials into the audit log."""
    store = data.store
    # Build the MESA runtime unconditionally (even under the kill switch): the
    # admin profile API must work regardless, and the enforcement gate is never
    # reached when no client routes are registered. A failure here must
    # not block Phoenix MCP setup. The integration remains available for reads
    # and recovery, while state-changing MESA gates fail closed unless the
    # operator explicitly selected off mode.
    from .mesa import async_setup_mesa
    try:
        data.mesa = await async_setup_mesa(hass, store.get_settings().mesa_mode)
        data.mesa_setup_failed = False
        ir.async_delete_issue(hass, DOMAIN, "mesa_runtime_unavailable")
    except Exception:  # MESA must never block Phoenix MCP startup
        _LOGGER.exception(
            "Phoenix MCP: MESA runtime setup failed; protected writes will be refused"
        )
        data.mesa = None
        data.mesa_setup_failed = True


    # Record mesa-core's own denials into the audit log. Attached even when MESA
    # is off (the mode is a runtime setting an admin can flip without a restart)
    # and independent of the kill switch, like the rest of the audit plumbing.
    # Detached on unload so a config-entry reload cannot stack handlers, which
    # would duplicate every future row.
    from .mesa_audit import attach_mesa_audit_bridge, detach_mesa_audit_bridge
    _mesa_bridge = attach_mesa_audit_bridge(data)
    entry.async_on_unload(lambda: detach_mesa_audit_bridge(_mesa_bridge))


def _warn_on_incompatible_internals(hass: HomeAssistant) -> None:
    """Name, once at startup, any Home Assistant internal that changed shape."""
    # Surface, once at startup, any HA-internal drift that would break the
    # in-process WS dispatch used by helper CRUD. Per-call failures still
    # degrade to a clean error; this just makes the cause visible early.
    from .ws_dispatch import check_ws_dispatch_compat
    _ws_incompat = check_ws_dispatch_compat(hass)
    if _ws_incompat is not None:
        _LOGGER.warning(
            "Phoenix MCP: in-process WebSocket dispatch may be incompatible with this HA "
            "version (%s); helper CRUD tools may be unavailable.",
            _ws_incompat,
        )
    from .log_read import check_system_log_compat
    _syslog_incompat = check_system_log_compat(hass)
    if _syslog_incompat is not None:
        _LOGGER.warning(
            "Phoenix MCP: system_log integration has changed shape on this HA version "
            "(%s); log diagnostic tools will report degraded source status.",
            _syslog_incompat,
        )


async def _async_register_surfaces(hass: HomeAssistant, data: PhoenixData) -> None:
    """Register the admin views and the panel, and the agent views unless the kill switch is on."""
    store = data.store
    from .admin.registry import ALL_ADMIN_VIEWS
    from .agent_chat_views import ALL_AGENT_CHAT_ADMIN_VIEWS
    _register_views(hass, ALL_ADMIN_VIEWS + ALL_AGENT_CHAT_ADMIN_VIEWS)

    from .panel import (
        async_register_phoenix_panel,
        async_sync_agentchat_inject,
        async_sync_mesa_inject,
    )
    await async_register_phoenix_panel(hass)
    # Optional, experimental, default-off: inject the in-context profile control
    # into HA's native config pages. Independent of the kill switch (admin-only).
    await async_sync_mesa_inject(hass)
    # Optional, default-off: the global Agent Chat window module (floats over the
    # whole HA UI). Independent of the kill switch here; the panel gates showing it.
    await async_sync_agentchat_inject(hass)

    settings = store.get_settings()

    async def _register_routes() -> None:
        """Register agent-facing views. Skipped when kill switch is active."""
        from .mcp_transport import ALL_MCP_VIEWS
        from .skill_view import ALL_SKILL_VIEWS
        from .agent_chat_views import ALL_AGENT_CHAT_CHAT_VIEWS
        _register_views(hass, ALL_MCP_VIEWS + ALL_SKILL_VIEWS + ALL_AGENT_CHAT_CHAT_VIEWS)

    data.async_register_routes = _register_routes
    if not settings.kill_switch:
        await _register_routes()
        data.routes_registered = True


async def _async_register_conversation_agents(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> None:
    """Connect the Assist bridge and the voice agent."""
    # Assist bridge: register the Phoenix MCP scoped llm.API so HA's native Assist/voice
    # conversation agents can drive the bound access key's tools. Registered whenever HA
    # supports it, kill switch or not: each turn's instance carries no tools while the
    # kill switch is on or nothing is bound, so a pipeline pointed at it degrades
    # instead of failing. No-op on HA versions lacking the seam.
    from .assist_api import (
        async_probe_assist_api,
        async_register_assist_api,
        async_unregister_assist_api,
    )
    assist_supported = await async_probe_assist_api(hass)
    if assist_supported:
        async_register_assist_api(hass, data)
    entry.async_on_unload(lambda: async_unregister_assist_api(data))

    # Phoenix MCP voice agent: register Phoenix MCP as HA's own conversation agent when fully
    # configured, so native Assist/voice runs Phoenix MCP's own model loop (voice_agent.py).
    # NOT kill-switch-gated: it stays registered but declines every turn under the
    # kill switch, so an Assist pipeline pointed at Phoenix MCP degrades gracefully instead of
    # hard-erroring. The admin settings PATCH re-syncs it live via this closure.
    from .voice_agent import async_sync_voice_agent, async_unregister_voice_agent

    @callback
    def _sync_voice_agent() -> None:
        async_sync_voice_agent(hass, entry, data)

    data.async_sync_voice_agent = _sync_voice_agent
    _sync_voice_agent()
    entry.async_on_unload(lambda: async_unregister_voice_agent(hass, entry))


async def _async_forward_platforms(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> None:
    """Wire access key lifecycle to its sensors and forward the entity platforms."""
    from .access_key_auth import schedule_expiry_timer

    from .sensor import async_create_access_key_sensors, async_remove_access_key_sensors

    async def _on_access_key_created(access_key) -> None:
        """Create sensor entities when a new access key is minted."""
        await async_create_access_key_sensors(hass, entry, access_key)
        schedule_expiry_timer(hass, data, access_key)

    async def _on_access_key_archived(access_key_slug: str) -> None:
        """Remove sensor entities when an access key is revoked or archived."""
        await async_remove_access_key_sensors(hass, access_key_slug)

    data.async_on_access_key_created = _on_access_key_created
    data.async_on_access_key_archived = _on_access_key_archived

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    data.forwarded_platforms.update(PLATFORMS)

    # The AI Task entity (ai_task.py) is optional; forward it separately and guarded
    # so a failure setting up that platform never aborts Phoenix MCP's core setup.
    from .ai_task import ai_task_supported
    if ai_task_supported():
        try:
            await hass.config_entries.async_forward_entry_setups(entry, ["ai_task"])
            data.forwarded_platforms.add("ai_task")
        except Exception:
            _LOGGER.warning("Could not set up the Phoenix MCP AI Task entity", exc_info=True)


def _start_periodic_work(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> Callable[[], Awaitable[None]]:
    """Start the reconciliation, flush and refresh timers; return the reconciliation."""
    store, audit = data.store, data.audit
    from .config_reconciliation import reconcile_configurations

    reconcile_failures = RepeatedFailureLog(_LOGGER, "Configuration reconciliation failed; will retry")

    async def _reconcile_configuration(_now=None) -> None:
        try:
            await reconcile_configurations(hass, data)
        except Exception as error:
            reconcile_failures.failed(error)
        else:
            reconcile_failures.recovered()

    entry.async_on_unload(async_track_time_interval(hass, _reconcile_configuration, CONFIG_RECOVERY_SWEEP_INTERVAL))

    async def _flush_conversations(_now=None) -> None:
        data.conversations.configure(data.store.get_settings())
        await data.conversations.async_save()

    entry.async_on_unload(async_track_time_interval(
        hass, _flush_conversations, timedelta(seconds=CONVERSATION_LOG_FLUSH_SECONDS)))

    statistics_failures = RepeatedFailureLog(_LOGGER, "Routing statistics flush failed; will retry")

    async def _flush_statistics(_now=None) -> None:
        try:
            await data.routing_statistics.async_save()
        except Exception as error:
            statistics_failures.failed(error)
        else:
            statistics_failures.recovered()

    entry.async_on_unload(async_track_time_interval(hass, _flush_statistics, ROUTING_STATISTICS_FLUSH_INTERVAL))

    from .agent_chat_views import async_refresh_all_catalogs

    async def _refresh_model_catalogs(_now=None) -> None:
        try:
            await async_refresh_all_catalogs(hass)
        except Exception:
            _LOGGER.debug("Provider model catalogue refresh failed; will retry", exc_info=True)

    entry.async_on_unload(async_track_time_interval(hass, _refresh_model_catalogs, AGENT_CHAT_CATALOG_TICK_INTERVAL))
    catalog_task = hass.async_create_background_task(_refresh_model_catalogs(), "phoenix_mcp_model_catalogs")

    def _cancel_catalog_refresh() -> None:
        catalog_task.cancel()

    entry.async_on_unload(_cancel_catalog_refresh)

    async def _flush_last_used(_now=None) -> None:
        await store.async_flush_last_used()

    cancel_flush = async_track_time_interval(hass, _flush_last_used, FLUSH_INTERVAL)
    entry.async_on_unload(cancel_flush)

    async def _push_sensor_updates(_now=None) -> None:
        for sensors in data.access_key_id_sensors.values():
            for sensor in sensors:
                if sensor.hass is not None:
                    sensor.async_write_ha_state()

    cancel_sensor_push = async_track_time_interval(hass, _push_sensor_updates, SENSOR_PUSH_INTERVAL)
    entry.async_on_unload(cancel_sensor_push)

    audit_failures = RepeatedFailureLog(_LOGGER, "Audit flush failed; will retry next interval")

    async def _flush_audit(_now=None) -> None:
        try:
            await audit.async_save()
        except Exception as error:
            audit_failures.failed(error)
        else:
            audit_failures.recovered()

    # Single-slot holder so the unload closure always cancels the LATEST timer,
    # not the one registered first.
    _audit_flush_cancel: list = []

    def _reschedule_audit_flush() -> None:
        """(Re)register the periodic audit flush from the current setting.

        Called at setup and on a settings change; 0 ("Never") removes the timer.
        """
        while _audit_flush_cancel:
            _audit_flush_cancel.pop()()
        interval = data.store.get_settings().audit_flush_interval
        if interval == 0:
            return
        _audit_flush_cancel.append(
            async_track_time_interval(hass, _flush_audit, timedelta(minutes=interval))
        )

    data.reschedule_audit_flush = _reschedule_audit_flush
    _reschedule_audit_flush()
    def _cancel_audit_flush() -> None:
        for cancel in _audit_flush_cancel:
            cancel()

    entry.async_on_unload(_cancel_audit_flush)

    def _cancel_pending_sensor_flush() -> None:
        if data.sensor_flush_cancel is not None:
            data.sensor_flush_cancel()
            data.sensor_flush_cancel = None

    entry.async_on_unload(_cancel_pending_sensor_flush)
    return _reconcile_configuration


async def _async_start_access_key_expiry(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> None:
    """Archive what expired while stopped and keep every remaining key on a timer."""
    from .access_key_auth import async_archive_expired_access_key, schedule_expiry_timer

    store = data.store

    async def _check_expired_access_keys(_now=None) -> None:
        for access_key in list(store.list_access_keys()):
            if access_key.is_expired():
                await async_archive_expired_access_key(hass, data, access_key)

    await _check_expired_access_keys()
    for _access_key in store.list_access_keys():
        schedule_expiry_timer(hass, data, _access_key)
    cancel_expiry = async_track_time_interval(hass, _check_expired_access_keys, EXPIRY_CHECK_INTERVAL)
    entry.async_on_unload(cancel_expiry)
    def _cancel_expiry_timers() -> None:
        for access_key_id in list(data.expiry_timers):
            cancel_expiry_timer(data, access_key_id)

    entry.async_on_unload(_cancel_expiry_timers)


async def _async_recover_approvals(
    hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData, state: DurableState
) -> None:
    """Settle approvals left by an earlier run, then keep sweeping for expired ones."""
    store, audit, agent_recovery = data.store, data.audit, data.agent_recovery

    async def _sweep_expired_approvals(_now=None) -> None:
        from .approvals import (
            dismiss_approval_notification,
            async_expire_overdue_approval_records,
            async_prune_terminal_approvals,
            fire_approval_resolved_event,
        )

        # A pending execution marker with no live claim means the previous
        # executor stopped reporting back. Resolve it before ordinary expiry can
        # present the action as a harmless timeout.
        await _reconcile_interrupted_approvals(
            skip_ids=data.approvals_in_progress,
            at_startup=False,
        )
        async with store.async_lock:
            expired = await async_expire_overdue_approval_records(
                store,
                skip_ids=data.approvals_in_progress,
            )
            await async_prune_terminal_approvals(store, skip_ids=data.approvals_in_progress)
        for approval in expired:
            dismiss_approval_notification(hass, approval.id)
            fire_approval_resolved_event(hass, approval)
            # Recorded as an expiry found at approve time is, so the audit trail
            # shows every request that timed out.
            data.audit.record(
                request_id=approval.request_id or "",
                access_key_id=approval.access_key_id,
                access_key_name=approval.access_key_name,
                method="approval/expired",
                resource=f"approval:{approval.tool_name}:{approval.id}",
                outcome="denied",
                client_ip="",
                settings=store.get_settings(),
            )

    async def _reconcile_interrupted_approvals(
        *,
        skip_ids: set[str] | frozenset[str] | None = None,
        at_startup: bool = True,
    ) -> None:
        """Resolve approvals whose execution claim has no recorded outcome.

        Runs at startup and during expiry sweeps, before ordinary expiry can
        report a clean timeout for an action that may already have applied.
        """
        from .approvals import (
            dismiss_approval_notification,
            async_reconcile_interrupted_approvals,
            fire_approval_resolved_event,
        )

        async with store.async_lock:
            interrupted = await async_reconcile_interrupted_approvals(
                store,
                skip_ids=skip_ids,
            )
        for approval in interrupted:
            dismiss_approval_notification(hass, approval.id)
            fire_approval_resolved_event(hass, approval)
            audit.record(
                request_id=approval.request_id,
                access_key_id="admin",
                access_key_name=f"admin:{approval.approved_by_user_id or 'unknown'}",
                method="approval/failed",
                resource=f"approval:{approval.tool_name}:{approval.id}",
                outcome="denied",
                client_ip="",
                settings=store.get_settings(),
                payload={
                    "reason": approval.closure_reason,
                    "approved_at": (
                        approval.approved_at.isoformat()
                        if approval.approved_at else None
                    ),
                    "reconciled_at_startup": at_startup,
                },
            )

    async def _cancel_queued_approvals_at_startup() -> None:
        """Cancel approvals queued by the previous run and log each one.

        Settled dispatch claims go too, leaving Needs Attention for unrecorded write outcomes.
        """
        from .approvals import (
            async_cancel_queued_approvals_at_startup,
            dismiss_approval_notification,
            fire_approval_resolved_event,
        )

        async with store.async_lock:
            cancelled = await async_cancel_queued_approvals_at_startup(store)
        for approval in cancelled:
            dismiss_approval_notification(hass, approval.id)
            fire_approval_resolved_event(hass, approval)
            audit.record(
                request_id=approval.request_id,
                access_key_id=approval.access_key_id,
                access_key_name=approval.access_key_name,
                method="approval/cancelled",
                resource=f"approval:{approval.tool_name}:{approval.id}",
                outcome="denied",
                client_ip="",
                settings=store.get_settings(),
                payload={"reason": approval.closure_reason, "cancelled_at_startup": True},
            )
        if cancelled:
            _LOGGER.info(
                "Phoenix MCP: cancelled %d approval request(s) still queued from the previous run",
                len(cancelled),
            )
        try:
            settled = await agent_recovery.settle_known_outcomes(store)
        except Exception:  # a stuck journal must not stop setup
            _LOGGER.exception("Phoenix MCP: could not settle recorded dispatch outcomes")
        else:
            if settled:
                _LOGGER.info(
                    "Phoenix MCP: cleared %d agent action(s) whose outcome is on record or "
                    "that never reached Home Assistant", settled,
                )

    # A reload is not a restart. Home Assistant did not stop, so a queued approval
    # keeps its requester and every check that runs when it is approved, and an
    # execution marker with a live claim belongs to a handler that is still running.
    # Only the first setup of a process may treat what it finds as left over.
    if state.startup_recovery_done:
        await _reconcile_interrupted_approvals(
            skip_ids=data.approvals_in_progress, at_startup=False,
        )
    else:
        await _reconcile_interrupted_approvals()
        await _cancel_queued_approvals_at_startup()
        state.startup_recovery_done = True
    await _sweep_expired_approvals()
    cancel_approval_sweep = async_track_time_interval(
        hass,
        _sweep_expired_approvals,
        APPROVAL_SWEEP_INTERVAL,
    )
    entry.async_on_unload(cancel_approval_sweep)


def _listen_for_stop(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> None:
    """Persist state and stop background work when Home Assistant stops."""
    store, audit = data.store, data.audit

    async def _on_stop(event: Event) -> None:
        data.ready = False
        data.shutting_down = True
        hass.data[RUNTIME_READY_KEY] = False
        await data.conversations.async_save()
        await data.decision_shadow.async_cancel()
        await _async_shutdown_mesa_workers()
        try:
            await data.routing_statistics.async_save()
        except Exception:
            _LOGGER.exception("Could not persist routing statistics at shutdown")
        # Both writes are best-effort, for the reason spelled out on
        # async_unload_entry: this runs inside Home Assistant's own stop
        # sequence, so an unwritable store must not raise into it. The first
        # failure must also not skip the second, hence two separate guards.
        try:
            await store.async_flush_last_used()
        except Exception:  # shutdown must not depend on a writable disk
            _LOGGER.exception("Phoenix MCP: could not flush access key usage at shutdown")
        try:
            await audit.async_save()
        except Exception:  # shutdown must not depend on a writable disk
            _LOGGER.exception("Phoenix MCP: could not persist the audit log at shutdown")

    _listen_once_until_unload(hass, entry, EVENT_HOMEASSISTANT_STOP, _on_stop)


def _listen_for_registry_changes(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> None:
    """Keep the entity tree cache and renamed entities current as the registries change."""
    @callback
    def _invalidate_entity_tree(_event=None) -> None:
        data.entity_tree_cache_valid = False

    async def _carry_renamed_entity(
        old_entity_id: str, new_entity_id: str, mesa_carried: bool
    ) -> None:
        if mesa_carried and data.mesa is not None:
            from .mesa import async_persist_renamed_profile
            await async_persist_renamed_profile(hass, data.mesa)
        async with data.store.async_lock:
            await data.store.async_rename_entity(old_entity_id, new_entity_id)
        # Its Changes history follows too, rather than splitting across two ids.
        await data.versions.async_rename_resource("entity", old_entity_id, new_entity_id)

    @callback
    def _on_entity_registry_updated(event: Event) -> None:
        _invalidate_entity_tree(event)
        _warn_on_computed_alias_loss(hass, event)
        # A renamed entity keeps its grants, hints, history and MESA profile. The
        # profile moves here, before this event returns, so no call can address
        # the new id while it is unprotected.
        old_entity_id = event.data.get("old_entity_id")
        if event.data.get("action") == "update" and isinstance(old_entity_id, str):
            new_entity_id = event.data["entity_id"]
            mesa_carried = False
            if data.mesa is not None:
                from .mesa import carry_renamed_profile
                mesa_carried = carry_renamed_profile(data.mesa, old_entity_id, new_entity_id)
            entry.async_create_background_task(
                hass, _carry_renamed_entity(old_entity_id, new_entity_id, mesa_carried),
                "phoenix_mcp_carry_renamed_entity",
            )

    entry.async_on_unload(
        hass.bus.async_listen(
            er_mod.EVENT_ENTITY_REGISTRY_UPDATED, _on_entity_registry_updated  # type: ignore[arg-type]
        )
    )
    # Labels too: the permission tree's Filter menu narrows the tree by label.
    for _registry_event in (
        dr_mod.EVENT_DEVICE_REGISTRY_UPDATED,
        ar_mod.EVENT_AREA_REGISTRY_UPDATED,
        lr_mod.EVENT_LABEL_REGISTRY_UPDATED,
    ):
        entry.async_on_unload(
            hass.bus.async_listen(_registry_event, _invalidate_entity_tree)  # type: ignore[misc]  # loop var erases the event's typed payload
        )


def _start_mesa_background_work(hass: HomeAssistant, entry: ConfigEntry, data: PhoenixData) -> None:
    """Prime the MESA caches and suggestions and keep them current on reload."""
    if data.mesa is not None:
        # Bound once here rather than read as data.mesa inside each closure: the
        # closures run long after this guard, so the attribute read cannot be
        # narrowed at their call sites and the runtime is fixed for this entry's
        # lifetime anyway.
        mesa_runtime = data.mesa
        from .mesa import (
            async_import_sidecar_profiles,
            async_refresh_trigger_issues,
            async_track_lease_expiry,
            refresh_orphans,
        )
        from .mesa_suggestions import refresh_suggestions

        entry.async_on_unload(async_track_lease_expiry(hass, mesa_runtime))

        async def _mesa_startup() -> None:
            """Import sidecar profiles and prime trigger/orphan caches."""
            try:
                count = await async_import_sidecar_profiles(hass, mesa_runtime)
                if count:
                    _LOGGER.info("Phoenix MCP MESA: imported %d developer domain profile(s)", count)
                await async_refresh_trigger_issues(hass, mesa_runtime)
                refresh_orphans(hass, mesa_runtime)
            except Exception:  # background priming must not crash setup
                _LOGGER.warning("Phoenix MCP MESA: startup priming failed", exc_info=True)

        mesa_task = hass.async_create_background_task(_mesa_startup(), "phoenix_mcp_mesa_startup")
        def _cancel_mesa_startup() -> None:
            mesa_task.cancel()

        entry.async_on_unload(_cancel_mesa_startup)

        # Suggestions scan entity STATES, which are still loading while Phoenix MCP sets
        # up (a scan at that point would see a near-empty state machine and cache
        # zero findings). Prime once HA has fully started; on a reload HA is already
        # running with populated states, so prime immediately.
        @callback
        def _prime_suggestions(_event: Event | None = None) -> None:
            try:
                refresh_suggestions(hass, mesa_runtime)
            except Exception:  # priming must not crash startup
                _LOGGER.warning("Phoenix MCP MESA: suggestion priming failed", exc_info=True)

        if hass.state is CoreState.running:
            _prime_suggestions()
        else:
            _listen_once_until_unload(
                hass, entry, EVENT_HOMEASSISTANT_STARTED, _prime_suggestions,
            )

        async def _on_automation_reloaded(_event=None) -> None:
            # Script/scene edits fire no reload event, so their suggestion
            # staleness is bounded by the next explicit refresh (panel Rescan).
            await async_refresh_trigger_issues(hass, mesa_runtime)
            refresh_orphans(hass, mesa_runtime)
            refresh_suggestions(hass, mesa_runtime)

        # "automation_reloaded" is fired by HA's automation component on reload;
        # listen by string to avoid importing that component.
        entry.async_on_unload(
            hass.bus.async_listen("automation_reloaded", _on_automation_reloaded)
        )


async def _async_setup_entry_impl(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Phoenix MCP from a config entry.

    Storage, admin views and the panel always load; MCP views only with the kill switch off.
    """
    data, state = await _async_build_runtime(hass)
    await _async_attach_diagnostics(hass, data)
    await _async_start_mesa(hass, entry, data)
    _warn_on_incompatible_internals(hass)
    await _async_register_surfaces(hass, data)
    await _async_register_conversation_agents(hass, entry, data)
    await _async_forward_platforms(hass, entry, data)
    _reconcile_configuration = _start_periodic_work(hass, entry, data)
    await _async_start_access_key_expiry(hass, entry, data)
    await _async_recover_approvals(hass, entry, data, state)
    _listen_for_stop(hass, entry, data)
    _audit_template_sandbox()
    _listen_for_registry_changes(hass, entry, data)
    _start_mesa_background_work(hass, entry, data)

    # Publish readiness before the bounded recovery readback. Earlier setup
    # failures are caught by async_setup_entry and rolled back.
    data.ready = True
    hass.data[RUNTIME_READY_KEY] = True
    if data.async_sync_ai_task is not None:
        try:
            data.async_sync_ai_task()
        except Exception:
            _LOGGER.exception("Could not refresh the optional AI Task entity")
    await _reconcile_configuration()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down Phoenix MCP: unload the platforms, then remove the panel.

    Every step before the platform unload is best-effort, as ``shutting_down`` already gates
    requests and a raise would leave the entry loaded and answering 503 until restart.
    """
    data: PhoenixData | None = hass.data.get(DOMAIN)
    if data is not None:
        data.ready = False
        data.shutting_down = True
        await data.conversations.async_save()
        await data.decision_shadow.async_cancel()
        try:
            await data.routing_statistics.async_save()
        except Exception:
            _LOGGER.exception("Could not persist routing statistics during unload")
        try:
            await data.audit.async_save()
        except Exception:  # teardown must not depend on a writable disk
            _LOGGER.exception("Phoenix MCP: could not persist the audit log during unload")
    hass.data[RUNTIME_READY_KEY] = False

    # The platform unload runs BEFORE the frontend comes down, and that order is
    # load-bearing: HA keeps a FAILED unload loaded, so tearing the panel down
    # first would leave a still-running integration with no administrative UI,
    # recoverable only by a later successful reload or a restart. Nothing in the
    # teardown depends on the frontend already being gone, so waiting until the
    # outcome is known costs nothing.
    platforms = sorted(data.forwarded_platforms) if data is not None else _entry_platforms()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)

    if unload_ok:
        if data is not None and data.mesa is not None:
            from .mesa import async_end_leases
            await async_end_leases(hass, data.mesa)
        await _async_shutdown_mesa_workers()
        from .debug_capture import async_detach_debug_capture
        async_detach_debug_capture(hass)
        if data is not None:
            for label, save in (("usage", data.store.async_flush_last_used), ("audit", data.audit.async_save)):
                try:
                    await save()
                except Exception:
                    _LOGGER.exception("Phoenix MCP final %s persistence failed", label)
        _remove_frontend(hass)
        hass.data.pop(DOMAIN, None)
    elif data is not None:
        # Still loaded, so it has to stay usable: this flag gates every access key
        # request, and the panel and injectors are deliberately left registered.
        data.shutting_down = False
        data.ready = True
        hass.data[RUNTIME_READY_KEY] = True

    return unload_ok


@dataclass
class _PipelineOwner:
    """The part of PhoenixData pipeline removal needs, after unload discarded it."""

    store: AccessKeyStore


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete credentials while preserving configuration and recovery evidence."""
    from .llm.accounts import async_wipe_agentcli_secrets
    from .ai_task import clear_ai_task_preferred
    from .approvals import (
        REASON_INTEGRATION_REMOVED,
        async_cancel_approvals_for_access_key,
        dismiss_approval_notification,
        fire_approval_resolved_event,
    )

    # An entry that never set up in this process has no state to share.
    state: DurableState | None = hass.data.get(_DURABLE_STATE_KEY)
    store = state.store if state is not None else await AccessKeyStore.async_create(hass)
    # HA removes the registry entries after this returns and leaves its preferences
    # alone, so this is the last point at which Phoenix's entity can be recognized.
    try:
        clear_ai_task_preferred(hass, entry)
    except Exception:  # removal retires credentials regardless
        _LOGGER.exception("Phoenix MCP: could not clear the AI Task default during removal")
    try:
        from .voice_agent import async_remove_assist_pipeline

        await async_remove_assist_pipeline(hass, _PipelineOwner(store))
    except Exception:  # removal retires credentials regardless
        _LOGGER.exception("Phoenix MCP: could not remove the Assist pipeline during removal")
    changed = []
    async with store.async_lock:
        for access_key in store.list_access_keys():
            changed.extend(await async_cancel_approvals_for_access_key(store, access_key.id, reason=REASON_INTEGRATION_REMOVED))
            await store.async_archive_access_key(access_key.id, revoked=True)
        await store.async_patch_settings(assist_bound_access_key_id=None,
            voice_agent_enabled=False, voice_agent_access_key_id=None,
            voice_agent_provider_id=None, voice_agent_model=None, ai_task_enabled=False, ai_task_access_key_id=None,
            ai_task_provider_id=None, ai_task_model=None, decision_provider_id=None,
            decision_provider_default_id=None)
    for record in changed:
        dismiss_approval_notification(hass, record.id)
        fire_approval_resolved_event(hass, record)
    await async_wipe_agentcli_secrets(hass)


@callback
def _remove_frontend(hass: HomeAssistant) -> None:
    """Take down the panel and both injected modules, one failure at a time.

    Guarded individually, so a failure cannot leave modules injected with nothing to serve
    them.
    """
    from .panel import (
        remove_agentchat_inject,
        remove_mesa_inject,
        remove_phoenix_panel,
    )

    for name, remove in (
        ("MESA injector", remove_mesa_inject),
        ("Agent Chat injector", remove_agentchat_inject),
        ("panel", remove_phoenix_panel),
    ):
        try:
            remove(hass)
        except Exception:  # one stuck removal must not skip the rest
            _LOGGER.exception("Phoenix MCP: could not remove the %s during unload", name)
