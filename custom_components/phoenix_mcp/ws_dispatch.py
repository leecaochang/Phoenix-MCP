"""In-process dispatch of Home Assistant WebSocket API commands.

Some capabilities, notably helper CRUD, exist only as WS commands. This looks up the
registered handler in ``hass.data["websocket_api"]``, validates against its schema, and runs
it on a synthetic ``ActiveConnection`` that captures the result, with no socket or token.

It is the one place leaning on those HA internals, guarded by a startup probe. Handlers
needing admin run under a real admin user from ``hass.auth``; Phoenix's capability gate
decides whether a call runs at all.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from .request_authority import check_request_authority
from .const import STORAGE_HELPER_TYPES, WS_REQUEST_CONTROL_FIELDS
from .component_guard import require_component

import voluptuous as vol
from homeassistant.components.websocket_api import const as ws_const
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0

# Results of commands whose caller stopped waiting (timeout or cancellation)
# while HA's handler task, which Phoenix cannot cancel, was still running.
_UNSETTLED: set[asyncio.Future] = set()


def ws_commands_unsettled() -> bool:
    """Whether a dispatched command may still change Home Assistant."""
    return bool(_UNSETTLED)


def _track_unsettled(future: asyncio.Future) -> None:
    def settled(done: asyncio.Future) -> None:
        _UNSETTLED.discard(done)
        if not done.cancelled():
            done.exception()  # retrieved, so a late failure is not logged as unhandled

    _UNSETTLED.add(future)
    future.add_done_callback(settled)

# The only WS commands Phoenix MCP may dispatch in-process, so a caller cannot turn input
# into an arbitrary privileged command run as admin. Keep in step with the in-tree callers.
# restore_backup is absent as too destructive.
#
# Most ZHA write commands are absent: their handlers enable process-wide debug logging or
# park callbacks in connection.subscriptions, which a synthetic connection never releases,
# and reconfigure and topology never call send_result. topology goes through zigpy in
# radio.py, permit and remove through the zha services, reconfigure through
# async_zha_reconfigure_device. zha/devices/bind, unbind and the group commands are safe
# exceptions, and Phoenix resolves every IEEE and group id from scoped registry entities.
_HELPER_DOMAINS = tuple(sorted(STORAGE_HELPER_TYPES))
ALLOWED_WS_COMMANDS: frozenset[str] = frozenset(
    [f"{domain}/{op}" for domain in _HELPER_DOMAINS for op in ("create", "update", "delete", "list")]
    + [
        "backup/agents/info", "backup/info", "backup/generate",
        "lovelace/dashboards/list", "lovelace/dashboards/create",
        "lovelace/dashboards/update", "lovelace/dashboards/delete",
        # Storage-mode dashboard resources. HA's collection handlers validate the
        # type and URL; tools/dashboard_resources.py is the only caller and gates
        # them on cap_dashboard_resources. They are not registered in YAML mode.
        "lovelace/resources/create", "lovelace/resources/update", "lovelace/resources/delete",
        "logbook/get_events",
        # Read only; tools/notify.py is the only caller, gated on cap_log_read, and
        # it drops Phoenix's own operator notifications before anything is shown.
        "persistent_notification/get",
        # Loaded-config reverse references. This complements Phoenix's direct
        # YAML walk with packages, blueprint-expanded configs, people, and
        # integration-managed consumers. The caller supplies only entity ids
        # already resolved through the Phoenix permission tree.
        "search/related",
        # Integration-aware logger control. Unlike logger/set_level, this resolves
        # the integration's declared logger set and preserves HA's none/once/
        # permanent semantics. Phoenix adds capability, scope, approval, and
        # timed-restoration gates before reaching this command.
        "logger/integration_log_level",
        "zha/device", "zha/network/settings", "zha/groups",
        "zha/devices/groupable", "zha/group/add", "zha/group/remove",
        "zha/group/members/add", "zha/group/members/remove",
        "zha/devices/bind", "zha/devices/unbind",
        # Blueprint authoring. HA's own handlers do the whole job: blueprint/save
        # parses the YAML, builds a Blueprint against the domain schema, refuses an
        # existing path unless allow_override, and reloads every consumer on an
        # override; blueprint/delete raises BlueprintInUse when an automation still
        # references it. Both send_result cleanly. blueprint/import is deliberately
        # ABSENT: it fetches an operator-unseen URL from inside HA, which is an
        # SSRF primitive with LAN and supervisor reach. Callers pass literal YAML.
        "blueprint/save", "blueprint/delete",
        # Read-only: renders a blueprint with caller-supplied inputs into the
        # standalone config it would produce, without saving anything.
        "blueprint/substitute",
        # Energy preferences. energy/get_prefs is the only route to the statistic-to-role
        # mapping in .storage/energy, which a caller needs to see before orphaning a source.
        #
        # energy/save_prefs is admin-only and each top-level key is a full replace, so
        # nothing composes a payload from agent input: tools/energy.py re-reads unredacted,
        # applies one addressed mutation, verifies the difference and sends only the changed
        # keys. An admin version restore is the one exception.
        #
        # energy/validate checks what is persisted, so it cannot pre-flight a write;
        # validate_ws_payload does that with save_prefs's schema. energy/solar_forecast
        # returns {} when no solar source names an entry.
        "energy/get_prefs", "energy/save_prefs", "energy/validate", "energy/solar_forecast",
    ]
)


class WsDispatchError(Exception):
    """Raised when an in-process WS command cannot be dispatched or fails."""


class WsCommandNotDispatchedError(WsDispatchError):
    """The command was refused before its handler could execute."""


class WsCommandRefusedError(WsDispatchError):
    """The handler ran and answered with an error result carrying Home Assistant's `code`.

    Whether that proves nothing changed depends on the handler, so callers decide
    per command; Core's collection create and update answer `invalid_format`
    only from validation that runs before anything is stored.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class WsDashboardNotFoundError(WsCommandNotDispatchedError):
    """A named Lovelace dashboard does not exist.

    A legitimate absence, which CAS paths treat as before=None, unlike "lovelace could not be
    read".
    """


class WsYamlDashboardError(WsCommandNotDispatchedError):
    """A YAML-mode dashboard, whose loaded layout carries resolved !secret values.

    `filename` is its YAML file relative to the configuration directory, or None
    when that cannot be established.
    """

    def __init__(self, message: str, filename: str | None) -> None:
        super().__init__(message)
        self.filename = filename


class _CapturingConnection(ActiveConnection):
    """An ActiveConnection that captures the command result in a Future.

    Overrides send_result, send_error and async_handle_exception, the sinks handlers use.
    """

    def __init__(self, hass: HomeAssistant, user: Any) -> None:
        # Pass only the constructor kwargs the live ActiveConnection accepts, so
        # this works across supported HA versions (parameters are added over
        # time). Read names from the code object, never
        # inspect.signature, which on Python 3.14 evaluates ActiveConnection's
        # TYPE_CHECKING-only annotations and raises NameError.
        available: dict[str, Any] = {
            "logger": _LOGGER,
            "hass": hass,
            "send_message": self._capture_send,
            "user": user,
            "refresh_token": None,
            "remote": None,
        }
        code = ActiveConnection.__init__.__code__
        accepted = set(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])
        super().__init__(**{k: v for k, v in available.items() if k in accepted})
        self.result_future: asyncio.Future = hass.loop.create_future()

    @callback
    def _capture_send(self, message: Any) -> None:
        """Capture a pre-built result message that bypassed send_result.

        Parses it and resolves the future on a success result; events and pings are ignored.
        """
        if self.result_future.done():
            return
        data: Any = message
        if isinstance(data, (bytes, bytearray, str)):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                return
        if not isinstance(data, dict) or data.get("type") != "result":
            return
        if data.get("success"):
            self.result_future.set_result(data.get("result"))
        else:
            err = data.get("error") or {}
            self.result_future.set_exception(
                WsDispatchError(f"{err.get('code')}: {err.get('message')}")
            )

    @callback
    def send_result(self, msg_id: int, result: Any | None = None) -> None:
        if not self.result_future.done():
            self.result_future.set_result(result)

    @callback
    def send_error(self, msg_id: int, code: str, message: str, *args: Any, **kwargs: Any) -> None:
        if not self.result_future.done():
            self.result_future.set_exception(WsCommandRefusedError(code, message))

    @callback
    def async_handle_exception(self, msg: dict, err: Exception) -> None:
        if not self.result_future.done():
            self.result_future.set_exception(err)


async def _resolve_admin_user(hass: HomeAssistant) -> Any:
    """Return a real active admin user (owner preferred) for require_admin commands."""
    users = await hass.auth.async_get_users()
    active_admins = [u for u in users if u.is_active and u.is_admin and not u.system_generated]
    if not active_admins:
        raise WsDispatchError("No active admin user is available to run this command.")
    return next((u for u in active_admins if u.is_owner), active_admins[0])


def _validated_message(hass: HomeAssistant, command: str, payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Return an allowlisted command's handler and its schema-validated message."""
    if command not in ALLOWED_WS_COMMANDS:
        raise WsCommandNotDispatchedError(f"WebSocket command not allowed: {command}")
    if set(payload) & WS_REQUEST_CONTROL_FIELDS:
        raise WsCommandNotDispatchedError("Payload must not contain WebSocket request fields.")

    handlers = hass.data.get(ws_const.DOMAIN)
    if not handlers or command not in handlers:
        raise WsCommandNotDispatchedError(f"WebSocket command not available: {command}")
    handler, schema = handlers[command]

    msg: dict[str, Any] = {**payload, "id": 1, "type": command}
    if schema not in (None, False):
        try:
            msg = schema(msg)
        except vol.Invalid as err:
            raise WsCommandNotDispatchedError(f"Invalid arguments for {command}: {err}") from err
    return handler, msg


def validate_ws_payload(hass: HomeAssistant, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a payload with the command's schema; return it normalized, without running."""
    _, msg = _validated_message(hass, command, copy.deepcopy(payload))
    return {key: value for key, value in msg.items() if key not in ("id", "type")}


async def async_ws_command(
    hass: HomeAssistant,
    command: str,
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Invoke a registered HA WebSocket command in-process and return its result.

    Raises WsDispatchError off the ALLOWED_WS_COMMANDS allowlist, when unregistered, on a
    schema failure, with no admin user, or on a handler error or timeout.
    """
    handler, msg = _validated_message(hass, command, payload)
    user = await _resolve_admin_user(hass)
    check_request_authority()

    # Everything from here leans on HA internals: ActiveConnection construction,
    # the require_admin/async_response decorators, and the handler delivering its
    # outcome through the connection. Any unexpected failure (a changed
    # ActiveConnection signature, a handler-side error surfaced via the result
    # future) is wrapped as WsDispatchError so callers get a clean tool error
    # instead of a raw HA exception.
    try:
        connection = _CapturingConnection(hass, user)
        # require_admin runs synchronously (raises if not admin); async_response
        # schedules a background task that resolves result_future.
        handler(hass, connection, msg)
        future = connection.result_future
        # asyncio.wait, unlike wait_for, leaves the future alone on timeout or
        # cancellation, so Phoenix still learns when HA's handler task settles.
        try:
            await asyncio.wait({future}, timeout=timeout)
        except asyncio.CancelledError:
            if not future.done():
                _track_unsettled(future)
            elif not future.cancelled():
                future.exception()  # a receipt nobody will read; retrieve it so it is not logged
            raise
        if not future.done():
            _track_unsettled(future)
            raise WsDispatchError(
                f"WebSocket command {command} did not finish within {timeout:g} seconds. "
                "Home Assistant may still complete it, so check the result before retrying."
            )
        return future.result()
    except WsDispatchError:
        raise
    except Exception as err:  # degrade any HA-internal breakage to a clean error
        raise WsDispatchError(f"WebSocket command {command} failed: {err}") from err


# The ActiveConnection.__init__ params _CapturingConnection knows how to supply.
# Must stay in sync with the `available` dict in _CapturingConnection.__init__;
# construction supplies the intersection with the live signature.
_SUPPLIED_CONNECTION_PARAMS = frozenset(
    {"logger", "hass", "send_message", "user", "refresh_token", "remote"}
)
_REQUIRED_CONNECTION_METHODS = ("send_result", "send_error", "async_handle_exception")
# async_ws_command asserts two things about the registry beyond "it is a dict":
# each entry unpacks as (handler, schema), and the handler takes exactly
# (hass, connection, msg). Both are HA's shape, not ours, and neither is visible
# to the constructor/method checks below.
_REGISTRY_ENTRY_LEN = 2
_HANDLER_ARGCOUNT = 3


def _handler_contract_error(registry: dict) -> str | None:
    """Check one registered allowlisted command against the call in async_ws_command.

    Returns None when compatible or when nothing is registered yet; samples, as all entries
    share HA's machinery.
    """
    sample = next((c for c in sorted(ALLOWED_WS_COMMANDS) if c in registry), None)
    if sample is None:
        return None
    entry = registry[sample]
    if not isinstance(entry, tuple) or len(entry) != _REGISTRY_ENTRY_LEN:
        return (
            f"websocket_api registry entry for {sample} is no longer a "
            f"(handler, schema) pair"
        )
    handler = entry[0]
    if not callable(handler):
        return f"websocket_api handler for {sample} is not callable"
    # Read arity from the code object, never inspect.signature (see the comment
    # in check_ws_dispatch_compat). A handler taking *args is a decorator wrapper
    # that forwards whatever it is given, so it tells us nothing and is skipped.
    code = getattr(handler, "__code__", None)
    if code is None:
        return None
    if code.co_flags & 0x04:  # CO_VARARGS
        return None
    positional = code.co_argcount - len(getattr(handler, "__defaults__", None) or ())
    if positional != _HANDLER_ARGCOUNT:
        return (
            f"websocket_api handler for {sample} takes {positional} positional "
            f"args, expected {_HANDLER_ARGCOUNT} (hass, connection, msg)"
        )
    return None


def check_ws_dispatch_compat(hass: HomeAssistant) -> str | None:
    """Best-effort check that the HA internals this module relies on are intact.
    Returns None when compatible, else a short reason, surfaced once at startup. Detects a new
    required ActiveConnection parameter, a missing result sink, a non-dict registry, and a
    changed handler contract.
    """
    try:
        registry = hass.data.get(ws_const.DOMAIN)
        if registry is not None and not isinstance(registry, dict):
            return "websocket_api command registry is not a dict"
        # Read parameter names straight from the code object. Do NOT use
        # inspect.signature here: on Python 3.14 it eagerly evaluates the
        # target's annotations, and ActiveConnection.__init__ annotates a
        # TYPE_CHECKING-only name (WebSocketAdapter) that is undefined at
        # runtime, so signature() raises NameError and would abort setup.
        code = getattr(ActiveConnection.__init__, "__code__", None)
        if code is not None:
            posargs = code.co_varnames[1 : code.co_argcount]  # skip self
            defaults = getattr(ActiveConnection.__init__, "__defaults__", None) or ()
            required = list(posargs[: len(posargs) - len(defaults)] if defaults else posargs)
            # Keyword-only params are required too unless they have a default in
            # __kwdefaults__; construction would fail per-call on one Phoenix MCP cannot
            # supply, so the probe must flag it the same as a positional param.
            kwonly = code.co_varnames[code.co_argcount : code.co_argcount + code.co_kwonlyargcount]
            kwdefaults = getattr(ActiveConnection.__init__, "__kwdefaults__", None) or {}
            required += [p for p in kwonly if p not in kwdefaults]
            unsupported = [p for p in required if p not in _SUPPLIED_CONNECTION_PARAMS]
            if unsupported:
                return (
                    "ActiveConnection.__init__ has required params Phoenix MCP cannot "
                    f"supply: {', '.join(unsupported)}"
                )
        missing_methods = [m for m in _REQUIRED_CONNECTION_METHODS if not hasattr(ActiveConnection, m)]
        if missing_methods:
            return f"ActiveConnection is missing methods: {', '.join(missing_methods)}"
        if isinstance(registry, dict):
            handler_err = _handler_contract_error(registry)
            if handler_err is not None:
                return handler_err
        return None
    except Exception as err:  # advisory probe must never abort setup
        return f"compatibility probe could not run: {err}"


# ---------------------------------------------------------------------------
# Lovelace dashboard view/card config (read + write).
#
# The lovelace/config WS command returns an opaque orjson.Fragment that cannot be
# read back in-process, so these go directly through the lovelace integration's
# LovelaceConfig objects (the same internal state HA's own handler uses). Kept in
# this module, the designated home for HA-internals access. Only storage-mode
# dashboards are writable; YAML-mode dashboards are file-backed.
# ---------------------------------------------------------------------------


def _lovelace_dashboard(hass: HomeAssistant, url_path: str | None) -> Any:
    """Return the LovelaceConfig for a url_path (None = default dashboard), or None.
    Mirrors HA's lovelace/config handler. None means only that no dashboard is registered
    there; lovelace being absent raises, as writes read a missing dashboard as nothing to
    preserve.
    """
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.lovelace.const import (
            DOMAIN as _LL_DOMAIN,
            LOVELACE_DATA,
        )
    except Exception as exc:  # lovelace not loaded
        raise WsDispatchError(f"lovelace is not loaded: {exc}") from exc
    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        raise WsDispatchError("lovelace is not loaded")
    dashboards = data.dashboards
    if url_path is None:
        return dashboards.get(_LL_DOMAIN) or dashboards.get(None)
    return dashboards.get(url_path)


@dataclass(frozen=True)
class LovelaceResources:
    """The registered dashboard resources and where Home Assistant keeps them.

    ``mode`` is "storage" or "yaml" (with !secret values resolved). Each item is HA's own
    dictionary: url and type, plus id in storage mode.
    """

    mode: str
    items: list[dict[str, Any]]


async def async_lovelace_resources(hass: HomeAssistant) -> LovelaceResources | None:
    """Return the registered dashboard resources, or None if they cannot be read.

    Mirrors HA's lovelace/resources handler. None covers an unavailable lovelace, safe mode
    and a failed read: an unknown answer must not look like no resources.
    """
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        data = hass.data.get(LOVELACE_DATA)
        if data is None or hass.config.safe_mode:
            return None
        resources = data.resources
        await resources.async_get_info()
        items = resources.async_items() or []
        mode = data.resource_mode
    except Exception:  # callers report an unreadable list, never a failed tool
        _LOGGER.debug("Could not read the dashboard resource list", exc_info=True)
        return None
    if mode not in ("storage", "yaml"):
        return None
    return LovelaceResources(mode, [
        dict(item) for item in items if isinstance(item, dict) and isinstance(item.get("url"), str)
    ])


async def async_get_lovelace_config(
    hass: HomeAssistant, url_path: str | None, *, storage_only: bool = False,
) -> dict[str, Any] | None:
    """Return a dashboard's stored view and card config, or None if auto-generated.
    Raises WsDashboardNotFoundError for an unknown dashboard and WsDispatchError when lovelace
    fails. With ``storage_only`` a YAML dashboard raises WsYamlDashboardError unloaded, as
    HA's loader resolves !secret.
    """
    config = _lovelace_dashboard(hass, url_path)
    if config is None:
        raise WsDashboardNotFoundError(f"config_not_found: unknown dashboard {url_path!r}")
    if storage_only and getattr(config, "mode", None) != "storage":
        path = getattr(config, "path", None)
        filename = None
        if isinstance(path, str):
            relative = os.path.relpath(path, hass.config.config_dir)
            filename = None if relative.startswith("..") or os.path.isabs(relative) else relative
        raise WsYamlDashboardError(f"dashboard {url_path!r} is YAML-mode", filename)
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.lovelace.const import ConfigNotFound
    except Exception as exc:
        raise WsDispatchError(f"lovelace unavailable: {exc}") from exc
    try:
        return await config.async_load(False)
    except ConfigNotFound:
        return None
    except Exception as exc:
        raise WsDispatchError(f"failed to read dashboard config: {exc}") from exc


async def async_save_lovelace_config(
    hass: HomeAssistant, url_path: str | None, config_data: dict[str, Any]
) -> None:
    """Save a dashboard's view and card config. Storage-mode dashboards only.

    Raises WsDashboardNotFoundError for an unknown dashboard and WsDispatchError when refused,
    with WsCommandNotDispatchedError for YAML mode. Authority failures propagate first.
    """
    config = _lovelace_dashboard(hass, url_path)
    if config is None:
        raise WsDashboardNotFoundError(f"config_not_found: unknown dashboard {url_path!r}")
    # mode property returns "storage" (MODE_STORAGE) or "yaml" (MODE_YAML).
    if getattr(config, "mode", None) != "storage":
        raise WsCommandNotDispatchedError(
            "only storage-mode dashboards can be written; this dashboard is YAML-mode"
        )
    check_request_authority()
    try:
        await config.async_save(config_data)
    except Exception as exc:
        raise WsDispatchError(f"failed to save dashboard config: {exc}") from exc


# ---------------------------------------------------------------------------
# ZHA device reconfigure (re-interview), outside async_ws_command: the WS handler parks a
# callback in connection.subscriptions and never calls send_result, so dispatch would leak a
# listener and time out. This mirrors the handler body minus the subscription.
# ---------------------------------------------------------------------------


async def async_zha_reconfigure_device(hass: HomeAssistant, ieee: str) -> None:
    """Start a ZHA device reinterview, fire-and-forget, like HA's own handler.

    There is no completion signal. Raises WsDispatchError when ZHA is unavailable or the
    device is absent; ZHA internals moving degrades to the same per-call error.
    """
    try:
        require_component(hass, "zha")
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.zha.helpers import get_zha_gateway
    except Exception as exc:  # zha missing or restructured
        raise WsDispatchError(f"ZHA is not available: {exc}") from exc
    try:
        gateway = get_zha_gateway(hass)
        device = gateway.get_device(_zha_eui64(ieee))
    except WsDispatchError:
        raise
    except Exception as exc:  # gateway not running / API drift
        raise WsDispatchError(f"ZHA gateway is not available: {exc}") from exc
    if device is None:
        raise WsDispatchError("not_found: ZHA device not found")
    try:
        hass.async_create_task(gateway.async_reinterview_device(device.ieee))
    except Exception as exc:
        raise WsDispatchError(f"failed to start reconfigure: {exc}") from exc


def _zha_eui64(ieee: str) -> Any:
    """Convert an ieee string to zigpy's EUI64 type (what the gateway keys on)."""
    try:
        # Local: ships with an optional component, not with this integration.
        from zigpy.types import EUI64
    except Exception as exc:
        raise WsDispatchError(f"ZHA is not available: {exc}") from exc
    try:
        return EUI64.convert(ieee)
    except Exception as exc:
        raise WsDispatchError(f"invalid ieee address: {ieee}") from exc
