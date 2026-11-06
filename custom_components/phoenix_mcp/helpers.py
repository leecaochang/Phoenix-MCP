"""Shared helpers used by multiple Phoenix MCP views."""

from __future__ import annotations

from datetime import datetime
from functools import partial
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util.dt import get_default_time_zone, parse_datetime, utcnow

from .catalog_text import notification_text
from .const import DOMAIN_SERVICE_HINTS, SENSOR_WRITE_DEBOUNCE_SECONDS
from .policy_engine import RelativeTimeError, parse_relative_time
from .request_authority import dispatch_request_id

if TYPE_CHECKING:
    import voluptuous as vol
    from homeassistant.exceptions import ServiceValidationError

    # Type-only: helpers reaches the audit log through data.audit and never
    # imports the module at runtime. Annotations are lazy here, so this cannot
    # introduce a cycle.
    from .audit import Outcome
    from .data import PhoenixData
    from .access_key_store import AccessKeyRecord

_LOGGER = logging.getLogger(__name__)


def build_error_response(
    code: str,
    message: str,
    status: int,
    request_id: str,
    suggestions: list[str] | None = None,
) -> web.Response:
    """Return a JSON error response with an X-Phoenix-Request-ID header."""
    body: dict[str, Any] = {"error": code, "message": message}
    if suggestions:
        body["suggestions"] = suggestions
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body),
        headers={"X-Phoenix-Request-ID": request_id},
    )


def service_not_found_hint(domain: str, service: str) -> tuple[str, list[str]] | None:
    """Return (message, suggestions) for a ServiceNotFound on a known core actuator domain.

    None off DOMAIN_SERVICE_HINTS, keeping the generic "Forbidden.". Safe only in that
    post-authorization catch; the rationale sits at the constant.
    """
    services = DOMAIN_SERVICE_HINTS.get(domain)
    if not services:
        return None
    valid = list(services)
    message = (
        f"No service {domain}.{service}. Valid {domain} services include: "
        f"{', '.join(valid)}."
    )
    return message, valid


def get_client_ip(request: web.Request) -> str:
    """Return the remote IP address, or an empty string if unavailable."""
    return request.remote or ""


def log_request(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    *,
    request_id: str,
    method: str,
    resource: str,
    outcome: Outcome,
    client_ip: str,
    payload: dict | None = None,
    mesa_advisory: bool = False,
    stale_tools_advisory: bool = False,
) -> None:
    """Record an audit entry and update in-memory access key counters."""
    # Attribute the request to the access key's active settings preset, when one is
    # set. getattr-guarded for partial key objects.
    preset_name: str | None = None
    active_preset_id = getattr(access_key, "active_preset_id", None)
    if active_preset_id:
        preset_name = next(
            (p.name for p in getattr(access_key, "presets", []) if p.id == active_preset_id),
            None,
        )
    data.audit.record(
        request_id=request_id,
        access_key_id=access_key.id,
        access_key_name=access_key.name,
        method=method,
        resource=resource,
        outcome=outcome,
        client_ip=client_ip,
        settings=data.store.get_settings(),
        pass_through=access_key.pass_through,
        payload=payload,
        mesa_advisory=mesa_advisory,
        preset=preset_name,
        stale_tools_advisory=stale_tools_advisory,
    )
    update_access_key_counter(data, access_key.id, outcome)


def fire_rate_limit_events(hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord) -> None:
    """Bound bus and notification notices to one per access key per minute."""
    now_mono = time.monotonic()
    last = data.rate_limit_notified.get(access_key.id)
    if last is not None and now_mono - last < 60.0:
        return
    data.rate_limit_notified[access_key.id] = now_mono
    hass.bus.async_fire("phoenix_mcp_rate_limited", {
        "access_key_id": access_key.id,
        "access_key_name": access_key.name,
        "timestamp": utcnow().isoformat(),
    })
    # Notification is throttled.
    settings = data.store.get_settings()
    if settings.notify_on_rate_limit:
        hass.async_create_task(
            hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": notification_text(
                        hass, "rate_limit.message", access_key=access_key.name),
                    "title": notification_text(hass, "rate_limit.title"),
                    "notification_id": f"phoenix_mcp_rate_limit_{access_key.id}",
                },
            )
        )


def content_hash(content: str | bytes | dict | list) -> str:
    """Hash content for compare-and-swap checks on Confirm-gated whole-blob writes.

    JSON-able content hashes over a canonical serialization, text and bytes as raw UTF-8. The
    value is opaque: agents echo it, never recompute it.
    """
    if isinstance(content, (dict, list)):
        raw = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        raw = str(content).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


TIME_INPUT_FORMS = (
    "Use an ISO 8601 time (2026-10-24T08:00:00; without an offset it is Home Assistant "
    "local time), a date, now, or a relative amount ago such as 30min, 24h, 7d, 2w or 1mo "
    "(+7d is 7 days ahead)."
)


def parse_time_param(value: str) -> datetime:
    """Parse a relative time, ISO timestamp or date into an aware datetime.

    A value without an offset is HA local time. Raises ValueError with a client-safe message.
    """
    # HA's parse_datetime regex-matches its argument, so a non-string reaches it
    # as a TypeError rather than the ValueError every caller here handles.
    if not isinstance(value, str):
        raise ValueError(f"Unrecognized time format. {TIME_INPUT_FORMS}")
    try:
        return parse_relative_time(value)
    except RelativeTimeError:
        raise
    except ValueError:
        pass
    dt = parse_datetime(value.strip())
    if dt is None:
        raise ValueError(f"Unrecognized time format: {value!r}. {TIME_INPUT_FORMS}")
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=get_default_time_zone())
    return dt


def cancel_expiry_timer(data: PhoenixData, access_key_id: str) -> None:
    """Cancel and remove the pending expiry timer for an access key, if one exists."""
    cancel = data.expiry_timers.pop(access_key_id, None)
    if cancel is not None:
        cancel()


@callback
def flush_sensor_writes(data: PhoenixData, _now=None) -> None:
    """Write HA state for every access key marked dirty since the last flush.

    Must stay @callback: async_write_ha_state needs the event loop, and HassJob would run a
    plain function in the executor.
    """
    data.sensor_flush_cancel = None
    dirty = data.sensor_write_dirty
    data.sensor_write_dirty = set()
    for access_key_id in dirty:
        for sensor in data.access_key_id_sensors.get(access_key_id, []):
            if sensor.hass is not None:
                sensor.async_write_ha_state()


def update_access_key_counter(data: PhoenixData, access_key_id: str, outcome: str) -> None:
    """Increment the in-memory request, denied and rate-limit counters for a key.

    Sensor writes are debounced by SENSOR_WRITE_DEBOUNCE_SECONDS, so calls do not each emit a
    state_changed event and recorder row.
    """
    if access_key_id not in data.access_key_counters:
        data.access_key_counters[access_key_id] = {
            "request_count": 0,
            "denied_count": 0,
            "rate_limit_hits": 0,
        }
    counters = data.access_key_counters[access_key_id]
    counters["request_count"] += 1
    if outcome in ("denied", "not_found"):
        counters["denied_count"] += 1
    elif outcome == "rate_limited":
        counters["rate_limit_hits"] += 1

    data.sensor_write_dirty.add(access_key_id)
    if data.hass is None:
        # Without a hass there is no loop to schedule on; write immediately.
        flush_sensor_writes(data)
        return
    if data.sensor_flush_cancel is None:
        data.sensor_flush_cancel = async_call_later(
            data.hass,
            SENSOR_WRITE_DEBOUNCE_SECONDS,
            partial(flush_sensor_writes, data),
        )


def validation_error_message(err: ServiceValidationError | vol.Invalid) -> str:
    """Return a non-empty agent-facing message for a service validation error."""
    detail = str(err).strip()
    return f"Invalid request: {detail}" if detail else "Invalid request."


def instance_base_url(hass: HomeAssistant) -> str:
    """Home Assistant's best available URL for links in prompts, or "" if it has none."""
    try:
        # Local: inside a guard for releases that lack it.
        from homeassistant.helpers.network import get_url

        return get_url(hass)
    except Exception:  # NoURLAvailableError and anything else
        return ""


class RepeatedFailureLog:
    """Log a recurring job's failure at WARNING once, then at debug until it recovers.

    The warning names only the exception type, as third-party text can carry hosts or
    credentials; the debug line keeps the traceback.
    """

    def __init__(self, logger: logging.Logger, message: str) -> None:
        self._logger = logger
        self._message = message
        self._failing = False

    def failed(self, exc: BaseException) -> None:
        if self._failing:
            self._logger.debug("%s (%s)", self._message, type(exc).__name__, exc_info=exc)
            return
        self._failing = True
        self._logger.warning("%s (%s)", self._message, type(exc).__name__)

    def recovered(self) -> None:
        self._failing = False


def log_internal_failure(logger: logging.Logger, phase: str, exc: BaseException) -> None:
    """Log fixed failure metadata and Phoenix frame locations, never error text or locals."""
    frames = []
    traceback = exc.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        if "/custom_components/phoenix_mcp/" in code.co_filename:
            frames.append(f"{code.co_filename.rsplit('/', 1)[-1]}:{traceback.tb_lineno}:{code.co_name}")
        traceback = traceback.tb_next
    logger.warning("Phoenix internal failure phase=%s type=%s rid=%s frames=%s",
                   phase, type(exc).__name__, dispatch_request_id.get() or "-", ",".join(frames[-8:]))
