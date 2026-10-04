"""Shared helpers used by multiple Phoenix MCP views."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import re
import string
from collections.abc import Awaitable, Callable, Iterator
from functools import lru_cache
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util.dt import parse_datetime, utcnow

from .const import (
    CATALOGS_DIR,
    DIFF_SUMMARY_TEMPLATES,
    NOTIFICATION_TEMPLATES,
    VERSION_SUMMARY_TEMPLATES,
    VOICE_TEMPLATES,
    CAP_ALLOW,
    CAP_CONFIRM,
    CAP_DENY,
    CAPABILITY_NAMES,
    DOMAIN,
    DOMAIN_SERVICE_HINTS,
    PASS_THROUGH_EXEMPT_CAPS,
    SENSOR_WRITE_DEBOUNCE_SECONDS,
    SYSTEM_LOG_MAX_RESPONSE_BYTES,
    TARGET_SELECTOR_KEYS,
    ACCESS_KEY_LENGTH,
    ACCESS_KEY_PREFIX,
)
from .policy_engine import (
    Permission,
    assist_expose_check,
    is_sensitive_key,
    parse_relative_time,
    resolve,
    resolve_registry_access,
    template_blocklist_vars,
)
from .locales import canonical_language
from .request_authority import check_request_authority
from .system_log_payload import (
    compact_log_entry,
    log_detail_chunk,
    log_detail_ref,
    log_payload_budget,
    log_result_size,
)
from .access_key_store import access_key_name_slug

if TYPE_CHECKING:
    import voluptuous as vol
    from homeassistant.exceptions import ServiceValidationError

    # Type-only: helpers reaches the audit log through data.audit and never
    # imports the module at runtime. Annotations are lazy here, so this cannot
    # introduce a cycle.
    from .audit import Outcome
    from .data import PhoenixData
    from .rate_limiter import RateLimitResult
    from .access_key_store import AccessKeyRecord

_LOGGER = logging.getLogger(__name__)


def build_permitted_states(access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Return {entity_id: ScrubbedState} for the entities an access key may read.

    resolve() is the single filter predicate, plus the shared assist_expose_check
    for pass_through access keys with use_assist_exposure. Every template handler must
    build its sandbox from this function so the filtering never diverges.
    """
    expose = assist_expose_check(access_key, hass)
    return {
        s.entity_id: ScrubbedState(s)
        for s in hass.states.async_all()
        if resolve(s.entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
        and (expose is None or expose(s.entity_id))
    }


def build_permitted_entity_ids(access_key: AccessKeyRecord, hass: HomeAssistant) -> set:
    """Return the entity IDs accessible to an access key, including registry-only entities.

    Unlike build_permitted_states, this unions live states with the entity registry
    so history and statistics can query recorder data for entities that are offline
    or disabled. Also applies use_assist_exposure filtering for pass_through access keys.
    """
    from homeassistant.helpers import entity_registry as er_mod

    registry = er_mod.async_get(hass)
    candidate_ids: set[str] = {s.entity_id for s in hass.states.async_all()}
    candidate_ids.update(entry.entity_id for entry in registry.entities.values())

    expose = assist_expose_check(access_key, hass)
    return {
        eid for eid in candidate_ids
        if resolve_registry_access(eid, access_key, hass) in (Permission.READ, Permission.WRITE)
        and (expose is None or expose(eid))
    }


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
    """Actionable hint for a ServiceNotFound on a known core actuator domain.

    Returns (message, suggestions) naming the domain's valid core service verbs,
    or None if the domain is not in DOMAIN_SERVICE_HINTS (caller keeps the generic
    no-oracle "Forbidden."). Safe only in the ServiceNotFound catch, which is
    post-authorization: the access key has already proven WRITE on an entity in this
    domain, so naming public HA-core verbs for it reveals nothing hidden. See
    DOMAIN_SERVICE_HINTS in const.py for the full leak-safety rationale.
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
    # set. getattr-guarded so bare test doubles keep working.
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
    """Hash content for compare-and-swap checks on Confirm-gated whole-blob config writes.

    A read tool returns this hash; the matching write tool takes it as
    expected_hash and refuses if the target changed since the read (an edit during
    the read/approve/apply window). JSON-able content is hashed over a canonical
    (sorted-key, compact) serialization so key order never causes a false
    conflict; text and bytes are hashed as raw UTF-8. The value is opaque: agents
    echo it back verbatim, never recompute it.
    """
    if isinstance(content, (dict, list)):
        raw = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        raw = str(content).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_time_param(value: str) -> datetime:
    """Parse a relative time string or ISO timestamp. Raises ValueError for unknown formats."""
    # HA's parse_datetime regex-matches its argument, so a non-string reaches it
    # as a TypeError rather than the ValueError every caller here handles.
    if not isinstance(value, str):
        raise ValueError(f"Unrecognized time format: {value!r}")
    try:
        return parse_relative_time(value)
    except ValueError:
        pass
    dt = parse_datetime(value)
    if dt is None:
        raise ValueError(f"Unrecognized time format: {value!r}")
    return dt


async def async_get_authenticated_access_key(
    hass: HomeAssistant,
    request: web.Request,
    data: PhoenixData,
    request_id: str,
    resource: str,
) -> tuple[AccessKeyRecord, RateLimitResult] | web.Response:
    """Validate the Phoenix MCP bearer access key and check rate limits.

    Returns (access_key, rl_result) on success, or an aiohttp Response on failure.
    Checks readiness, kill switch, query-param access key leakage, format
    pre-validation, hash lookup, revocation, expiry, and rate limits in that order.
    """
    if not data.ready or data.shutting_down:
        return build_error_response("service_unavailable", "Service unavailable.", 503, request_id)

    if data.store.get_settings().kill_switch:
        # Kill-switch mode should make Phoenix MCP invisible on the network. At
        # startup that means registering no routes; aiohttp cannot unregister routes
        # at runtime, so the routes stay and refuse service with 503.
        return build_error_response("service_unavailable", "Service unavailable.", 503, request_id)

    _401 = build_error_response("unauthorized", "Unauthorized.", 401, request_id)
    _401.headers["WWW-Authenticate"] = 'Bearer realm="Phoenix MCP"'

    for key in ("token", "access_token"):
        if key in request.query:
            return _401

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _401

    presented = auth_header[7:]
    if not presented.startswith(ACCESS_KEY_PREFIX) or len(presented) != ACCESS_KEY_LENGTH:
        return _401

    access_key_hash = hashlib.sha256(presented.encode()).hexdigest()
    access_key = data.store.get_access_key_by_hash(access_key_hash)

    if access_key is None:
        return _401

    if not access_key.is_valid():
        if access_key.is_expired():
            await async_archive_expired_access_key(hass, data, access_key)
        return _401

    # Update last_used before the rate limit check so last_access reflects every
    # attempted request, not just allowed ones. This keeps last_access consistent
    # with request_count, which also increments on rate-limited requests.
    data.store.update_last_used(access_key.id, utcnow())

    rl_result = data.rate_limiter.check(
        access_key.id,
        access_key.rate_limit_requests,
        access_key.rate_limit_burst,
    )

    if not rl_result.allowed:
        fire_rate_limit_events(hass, data, access_key)
        log_request(
            data,
            access_key,
            request_id=request_id,
            method=request.method,
            resource=resource,
            outcome="rate_limited",
            client_ip=get_client_ip(request),
        )
        resp = build_error_response("rate_limited", "Rate limit exceeded.", 429, request_id)
        resp.headers["Retry-After"] = str(rl_result.retry_after)
        return resp

    return access_key, rl_result


def cancel_expiry_timer(data: PhoenixData, access_key_id: str) -> None:
    """Cancel and remove the pending expiry timer for an access key, if one exists."""
    cancel = data.expiry_timers.pop(access_key_id, None)
    if cancel is not None:
        cancel()


def schedule_expiry_timer(hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord) -> None:
    """Schedule a timer to archive an access key at its expiry time.

    An access key without an expiry is left untouched. Otherwise any earlier timer for
    it is cancelled first, and none is scheduled when it has already expired.
    """
    if access_key.expires_at is None:
        return
    cancel_expiry_timer(data, access_key.id)
    delay = (access_key.expires_at - utcnow()).total_seconds()
    if delay <= 0:
        return

    @callback
    def _on_expiry(_now=None) -> None:
        data.expiry_timers.pop(access_key.id, None)
        hass.async_create_background_task(
            async_archive_expired_access_key(hass, data, access_key),
            f"phoenix_mcp_expire_{access_key.id}",
        )

    data.expiry_timers[access_key.id] = async_call_later(hass, delay, _on_expiry)


async def async_archive_expired_access_key(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
) -> None:
    """Move an expired access key to the archive and perform full cleanup.

    Archives the record to storage, closes its queued approvals, destroys rate
    limiter and counter state, fires the phoenix_mcp_access_key_expired bus event, and
    removes sensor entities.
    """
    from .approvals import (  # noqa: PLC0415 - avoid import cycle at module load
        REASON_ACCESS_KEY_EXPIRED,
        async_cancel_approvals_for_access_key,
        dismiss_approval_notification,
        fire_approval_resolved_event,
    )

    cancel_expiry_timer(data, access_key.id)
    # Expiry removes the same live access key references as explicit revocation, so it
    # follows the same settings-lock then access-key-store-lock order.
    async with data.settings_update_lock:
        async with data.store.async_lock:
            # The timer, request-path expiry check, and periodic sweep may all arrive
            # for the same access key. Re-read under the mutation lock so only the first
            # expired transition archives it, and so PATCH/revoke cannot interleave
            # with the stored snapshot and dependent cleanup below.
            current = data.store.get_access_key_by_id(access_key.id)
            now = utcnow()
            if (
                current is None
                or current.expires_at is None
                or current.expires_at > now
            ):
                return
            slug = access_key_name_slug(current.name)
            archived = await data.store.async_archive_access_key(
                current.id, revoked=False, revoked_at=now
            )
            if archived is None:
                return
            # Same queue hygiene as revoke: an expired access key's approvals cannot be
            # legitimately approved anymore (re-validation would auto-reject them).
            # Clear bindings in the same transaction as access key archival.
            _s = data.store.get_settings()
            _patch: dict[str, Any] = {}
            if _s.assist_bound_access_key_id == current.id:
                _patch["assist_bound_access_key_id"] = None
            if _s.voice_agent_access_key_id == current.id:
                _patch["voice_agent_access_key_id"] = None
            if _s.ai_task_access_key_id == current.id:
                _patch["ai_task_access_key_id"] = None
            if _patch:
                await data.store.async_patch_settings(**_patch)
            changed_approvals = await async_cancel_approvals_for_access_key(
                data.store, current.id, REASON_ACCESS_KEY_EXPIRED,
                skip_ids=data.approvals_in_progress,
            )
    for approval in changed_approvals:
        dismiss_approval_notification(hass, approval.id)
        fire_approval_resolved_event(hass, approval)
    # Advisory leases the access key still held; in-memory, so outside the store lock.
    from .mesa import release_access_key_leases  # noqa: PLC0415
    release_access_key_leases(data, current.id)
    if data.async_sync_voice_agent is not None:
        data.async_sync_voice_agent()
    if "voice_agent_access_key_id" in _patch:
        # The voice agent lost its access key and is unregistered; remove any
        # Phoenix-created Assist pipeline so a broken assistant is not left behind.
        from .voice_agent import async_remove_assist_pipeline  # noqa: PLC0415
        await async_remove_assist_pipeline(hass, data)
    if "ai_task_access_key_id" in _patch and data.async_sync_ai_task is not None:
        # The AI Task lost its access key; remove the AI Task entity from HA (and picker).
        data.async_sync_ai_task()
    data.rate_limiter.destroy(current.id)
    data.rate_limit_notified.pop(current.id, None)
    data.access_key_counters.pop(current.id, None)
    data.stale_tools_advised.pop(current.id, None)
    hass.bus.async_fire("phoenix_mcp_access_key_expired", {
        "access_key_id": current.id,
        "access_key_name": current.name,
        "timestamp": now.isoformat(),
    })
    if data.async_on_access_key_archived:
        try:
            await data.async_on_access_key_archived(slug)
        except Exception:
            _LOGGER.warning(
                "Sensor cleanup failed for expired access key %s", current.id, exc_info=True,
            )


class _ContextProxy(dict):
    """Dict subclass that also supports attribute access.

    Used by ScrubbedState.context so templates can use both context.id and
    context | tojson without TypeError. Behaves as a plain dict for json.dumps().
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class ScrubbedState:
    """Lightweight State wrapper that strips sensitive attributes for use in template sandboxes."""

    def __init__(self, raw: Any) -> None:
        self.entity_id = raw.entity_id
        self.state = raw.state
        self.attributes = {k: v for k, v in raw.attributes.items() if not is_sensitive_key(k)}
        self.last_updated = getattr(raw, "last_updated", None)
        self.last_changed = getattr(raw, "last_changed", None)
        self.last_reported = getattr(raw, "last_reported", None)
        # Strip user_id from context to prevent HA user ID enumeration via templates.
        ctx = getattr(raw, "context", None)
        self.context: _ContextProxy | None
        if ctx is not None:
            self.context = _ContextProxy({
                "id": getattr(ctx, "id", None),
                "parent_id": getattr(ctx, "parent_id", None),
                "user_id": None,
            })
        else:
            self.context = None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".")[0]

    @property
    def object_id(self) -> str:
        return self.entity_id.split(".", 1)[1] if "." in self.entity_id else self.entity_id

    @property
    def name(self) -> str:
        friendly = self.attributes.get("friendly_name")
        if friendly:
            return str(friendly)
        return self.object_id.replace("_", " ").title()

    @classmethod
    def from_dict(cls, row: dict) -> ScrubbedState:
        """Restore the template facade without HA constructor timestamp defaults."""
        from types import SimpleNamespace

        context = row.get("context")
        return cls(SimpleNamespace(
            entity_id=row["entity_id"], state=row["state"],
            attributes=row["attributes"],
            last_changed=parse_datetime(row["last_changed"]) if row.get("last_changed") else None,
            last_updated=parse_datetime(row["last_updated"]) if row.get("last_updated") else None,
            last_reported=parse_datetime(row["last_reported"]) if row.get("last_reported") else None,
            context=SimpleNamespace(**context) if context is not None else None,
        ))

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": self.attributes,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "last_reported": self.last_reported.isoformat() if self.last_reported else None,
            "last_changed": self.last_changed.isoformat() if self.last_changed else None,
            "context": {
                "id": getattr(self.context, "id", None),
                "parent_id": getattr(self.context, "parent_id", None),
                "user_id": None,
            } if self.context is not None else None,
        }


class _DomainFilteredStates:
    """Iterable wrapper for a single domain's entities inside FilteredStates.

    Supports both iteration ({% for state in states.light %}) yielding
    ScrubbedState objects, and attribute access (states.light.living_room)
    returning individual entities by object_id.
    """

    def __init__(self, entities: dict) -> None:
        self._entities = entities

    def __iter__(self) -> Iterator[Any]:
        return iter(self._entities.values())

    def __len__(self) -> int:
        return len(self._entities)

    def __getattr__(self, object_id: str) -> Any:
        if object_id.startswith("_"):
            raise AttributeError(object_id)
        return self._entities.get(object_id)


class FilteredStates:
    """Callable proxy over a permitted-entity dict mimicking the HA template 'states' global.

    HA templates use 'states' as both a callable (states('sensor.foo')) and a
    domain-keyed accessor (states.light). A plain dict breaks the callable form,
    so this proxy implements both protocols while restricting access to permitted entities.
    """

    def __init__(self, permitted: dict) -> None:
        self._permitted = permitted

    def __call__(self, entity_id: str, default: str = "unknown") -> str:
        s = self._permitted.get(entity_id)
        return s.state if s is not None else default

    def __getitem__(self, entity_id: str) -> Any:
        return self._permitted.get(entity_id)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._permitted.values())

    def __len__(self) -> int:
        return len(self._permitted)

    def __getattr__(self, domain: str) -> Any:
        if domain.startswith("_"):
            raise AttributeError(domain)
        entities = {
            eid.split(".", 1)[1]: s
            for eid, s in self._permitted.items()
            if eid.split(".")[0] == domain
        }
        return _DomainFilteredStates(entities)


_SAFE_TEMPLATE_ENV = None


def safe_template_env() -> Any:
    """Return the cached hass-less TemplateEnvironment used for access key template renders.

    A TemplateEnvironment constructed without hass never registers the hass-aware
    helpers (states, expand, area_entities, integration_entities, ...) as globals,
    filters, or tests, so entity access exists only through the permission-filtered
    variables Phoenix MCP injects. Rendering in the full hass environment and shadowing
    globals with render variables is NOT safe: Jinja2 variables never shadow
    filters or tests, so {{ 'sensor.x' | states }} would bypass the sandbox.
    """
    global _SAFE_TEMPLATE_ENV
    if _SAFE_TEMPLATE_ENV is None:
        from homeassistant.helpers.template import TemplateEnvironment  # noqa: PLC0415
        _SAFE_TEMPLATE_ENV = TemplateEnvironment(None)
    return _SAFE_TEMPLATE_ENV


def render_template_for_access_key(template_str: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """Render a Jinja2 template against the access key's permitted entity state.

    Renders in safe_template_env() with permission-restricted replacements for the
    HA state helpers and pure dt_util shims for the time helpers the hass-less
    environment lacks. Raises (TemplateError, ValueError, jinja2 errors) on any
    failure; callers map exceptions to an invalid_request response.
    """
    return render_template_snapshot(template_str, build_permitted_states(access_key, hass))


def render_template_snapshot(template_str: str, permitted: dict) -> str:
    """Render a detached state snapshot; production calls run in the worker."""
    from homeassistant.helpers.template import MAX_TEMPLATE_OUTPUT
    from homeassistant.util import dt as dt_util
    from .template_diagnostics import TemplateOutputLimitError

    filtered_states = FilteredStates(permitted)

    # Permission-restricted versions of the HA template state helpers.
    def _state_attr(entity_id: str, attr: str) -> Any:
        s = permitted.get(entity_id)
        return s.attributes.get(attr) if s is not None else None

    def _is_state(entity_id: str, value: str) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.state == value

    def _is_state_attr(entity_id: str, attr: str, value) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.attributes.get(attr) == value

    def _has_value(entity_id: str) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.state not in ("unknown", "unavailable")

    # Time helpers absent from the hass-less environment. These mirror HA's
    # DateTimeExtension implementations; they touch only dt_util, never entities.
    def _today_at(time_str: str = "") -> datetime:
        today = dt_util.start_of_local_day()
        if not time_str:
            return today
        parsed = dt_util.parse_time(time_str)
        if parsed is None:
            raise ValueError(f"could not convert str to datetime: '{time_str}'")
        return datetime.combine(today, parsed, today.tzinfo)

    def _localize(value: datetime) -> datetime:
        return value if value.tzinfo else dt_util.as_local(value)

    def _relative_time(value: Any) -> Any:
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() < value else dt_util.get_age(value)

    def _time_since(value: Any, precision: int = 1) -> Any:
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() < value else dt_util.get_age(value, precision)

    def _time_until(value: Any, precision: int = 1) -> Any:
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() > value else dt_util.get_time_remaining(value, precision)

    variables = {
        "states": filtered_states,
        "state_attr": _state_attr,
        "is_state": _is_state,
        "is_state_attr": _is_state_attr,
        "has_value": _has_value,
        "now": dt_util.now,
        "utcnow": dt_util.utcnow,
        "today_at": _today_at,
        "relative_time": _relative_time,
        "time_since": _time_since,
        "time_until": _time_until,
        # Defense in depth: these names do not exist in the hass-less environment,
        # but the enumeration helpers must return empty values rather than raise,
        # so keep the stubs as render variables.
        **template_blocklist_vars(),
    }

    compiled = safe_template_env().from_string(template_str)
    chunks = []
    size = 0
    for chunk in compiled.generate(variables):
        size += len(chunk)
        if size > MAX_TEMPLATE_OUTPUT:
            raise TemplateOutputLimitError("Template output exceeded maximum size.")
        chunks.append(chunk)
    return "".join(chunks).strip()


# Ranks the level of a RECORD. DEBUG and INFO stay although HA's system_log
# handler is attached at WARNING and stores neither: this map classifies whatever
# HA supplies, and dropping an entry would silently narrow what an operator can
# read. The narrower set a CALLER may ask for is const.LOG_LEVELS; the two differ
# deliberately.
_LOG_LEVEL_RANK: dict[str, int] = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 3}
_PHOENIX_ACCESS_KEY_SCRUB_RE = re.compile(r"phx_[0-9a-f]{64}", re.IGNORECASE)
# Home Assistant long-lived access tokens (and other JWTs) are three base64url
# segments whose header always begins "eyJ". Redact so a leaked LLAT in a log
# line is not handed back to an access key holding cap_log_read.
_JWT_SCRUB_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")
# Credentials embedded in URLs/log text: query params (token=..., api_password=...)
# and userinfo (https://user:pass@host). Over-redaction in logs is acceptable.
_URL_CRED_QUERY_RE = re.compile(
    r"(?i)(access_key|accesskey|access_token|refresh_token|api_password|password|api_key|apikey|client_secret|secret|token|auth)=[^\s&\"';]+"
)
_URL_CRED_USERINFO_RE = re.compile(r"://[^/\s:@]+:[^/\s:@]+@")
_PHOENIX_LOGGER_PREFIXES = ("homeassistant.components.phoenix_mcp", "custom_components.phoenix_mcp")


def _scrub_log_text(text: str) -> str:
    """Redact Phoenix MCP access keys, JWTs/LLATs, and URL-embedded credentials from a log line."""
    text = _PHOENIX_ACCESS_KEY_SCRUB_RE.sub("<phoenix-access-key>", text)
    text = _JWT_SCRUB_RE.sub("<token>", text)
    text = _URL_CRED_QUERY_RE.sub(r"\1=<redacted>", text)
    text = _URL_CRED_USERINFO_RE.sub("://<redacted>@", text)
    return text


# A "key: value" or "key = value" line in a YAML/config diff, capturing the
# leading key so its value can be redacted when the key name looks sensitive.
_CONFIG_SECRET_LINE_RE = re.compile(r"^(\s*)([\w.\-]+)(\s*[:=]\s*)(\S.*)$")
_SECRET_REFERENCE_RE = re.compile(
    r"!(?:secret|env_var)\s+[A-Za-z_][A-Za-z0-9_.\-/]*\s*(?:#.*)?"
)
_CODE_INITIALIZER_RE = re.compile(
    r"\s*(?:(?:const|let|var)\s+)?"
    r"(?P<key>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*=\s*"
    r"(?P<value>null|undefined|true|false|\{\s*\}|\[\s*\]|\"\"|''|0)\s*;?\s*"
)


def _non_secret_code_initializer(line: str) -> bool:
    """Recognize complete empty/boolean code initializers, never credential literals.

    The member/declaration syntax keeps YAML/config scalars on the
    conservative path. Zero is admitted only for session bookkeeping and named
    counters, not passwords, PINs or numeric access tokens. No trailing source,
    comments, nonempty strings or containers can take this exception.
    """
    match = _CODE_INITIALIZER_RE.fullmatch(line)
    if match is None:
        return False
    key = match.group("key")
    if _scrub_log_text(key) != key:
        return False
    if "." not in key and not line.lstrip().startswith(("const ", "let ", "var ")):
        return False
    if match.group("value") != "0":
        return True
    field = key.rsplit(".", 1)[-1].lower().lstrip("_$")
    return field == "session" or field.endswith((
        "count", "counter", "generation", "revision", "epoch", "index", "length", "size",
    ))


def redact_secrets_in_text(text: str | None) -> str | None:
    """Redact secret-valued config lines and embedded credentials from diff text.

    Applied to approval diffs (file writes, configuration.yaml edits) before they
    persist to .storage so secrets are not copied to disk verbatim. A line whose
    key name is sensitive (is_sensitive_key) has its value replaced; JWTs and
    URL-embedded credentials anywhere in the text are scrubbed too. The structure
    of the change stays visible to the reviewing admin.
    """
    if not text:
        return text
    out: list[str] = []
    for line in text.split("\n"):
        if _non_secret_code_initializer(line):
            out.append(line)
            continue
        m = _CONFIG_SECRET_LINE_RE.match(line)
        if (m is not None and is_sensitive_key(m.group(2))
                and not _SECRET_REFERENCE_RE.fullmatch(m.group(4))):
            out.append(_scrub_log_text(f"{m.group(1)}{m.group(2)}{m.group(3)}<redacted>"))
        else:
            out.append(_scrub_log_text(line))
    return "\n".join(out)


def redact_structure(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets from a JSON-able structure.

    A dict value whose key name is sensitive (is_sensitive_key) becomes
    "<redacted>"; string values are scrubbed for secret-valued config lines and
    embedded credentials (redact_secrets_in_text). Other scalars pass through
    unchanged. Used for audit payloads and the admin-facing copy of approval
    args so secrets are never serialised verbatim. Recursion is depth-bounded so
    a pathologically nested payload cannot raise RecursionError on the logging
    path; subtrees past the limit collapse to "<redacted>".
    """
    if _depth > 25:
        return "<redacted>"
    if isinstance(obj, dict):
        return {
            k: "<redacted>" if is_sensitive_key(k) else redact_structure(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_structure(item, _depth + 1) for item in obj]
    if isinstance(obj, str):
        return redact_secrets_in_text(obj)
    return obj


# Network-topology scrubbing for integration-defined diagnostics (get_system_health).
# build_safe_config already withholds internal_url/external_url/config_dir/paths
# from get_config, but system_health values are free-form strings that can carry
# the same infrastructure detail (LAN IPs, hostnames inside URLs, filesystem
# paths), which redact_structure (secret-keyed values and embedded credentials
# only) does not catch. The patterns are deliberately conservative. The IPv4 set
# covers only PRIVATE/loopback/link-local ranges, so a public-IP-shaped version
# string like "4.8.0.1" is NOT matched while the LAN topology is still scrubbed.
_PRIVATE_IPV4_RE = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"                  # 10.0.0.0/8
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"               # loopback 127.0.0.0/8
    r"|169\.254\.\d{1,3}\.\d{1,3}"                   # link-local 169.254.0.0/16
    r"|192\.168\.\d{1,3}\.\d{1,3}"                   # 192.168.0.0/16
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"  # 172.16.0.0/12
    r")\b"
)
# Link-local (fe80::/10) and unique-local (fc00::/7) IPv6.
_PRIVATE_IPV6_RE = re.compile(
    r"\b(?:fe80|f[cd][0-9a-f]{2})(?::[0-9a-f]{0,4}){2,7}\b", re.IGNORECASE
)
# Bare http(s) URL (host + optional port/path). URL-embedded credentials are already
# scrubbed upstream; this removes the host/topology itself.
_BARE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
# Absolute filesystem paths: unix /a/b... (>=2 segments, so a lone "/" or a URL path
# already handled above is not matched) and Windows drive paths C:\a\b\... .
_UNIX_PATH_RE = re.compile(r"(?<![\w/])/(?:[\w.\-]+/)+[\w.\-]+")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[\w.\-]+\\?){2,}")


def _scrub_network_topology(text: str) -> str:
    """Replace LAN IPs, bare URLs, and absolute filesystem paths with placeholders."""
    text = _BARE_URL_RE.sub("<redacted-url>", text)
    text = _PRIVATE_IPV4_RE.sub("<redacted-ip>", text)
    text = _PRIVATE_IPV6_RE.sub("<redacted-ip>", text)
    text = _WIN_PATH_RE.sub("<redacted-path>", text)
    text = _UNIX_PATH_RE.sub("<redacted-path>", text)
    return text


def _scrub_diagnostic_str(text: str) -> str:
    """Full diagnostic-string scrub: embedded credentials/JWTs then network topology."""
    return _scrub_network_topology(redact_secrets_in_text(text) or "")


def redact_diagnostics(obj: Any, _depth: int = 0) -> Any:
    """redact_structure plus a conservative network-topology scrub for diagnostics.

    Each string is also scrubbed for private/loopback/link-local IPs, bare URLs
    and absolute paths. Integration-defined payloads are free-form, so dict KEYS
    get the same string scrub as values (an integration may key its health data by
    a URL, LAN IP or path); a sensitive-named key still redacts its value.
    cap_diagnostics is an elevated read, so some over-redaction is acceptable; the
    diagnostic shape is preserved.
    """
    if _depth > 25:
        return "<redacted>"
    if isinstance(obj, dict):
        return {
            (_scrub_diagnostic_str(k) if isinstance(k, str) else k):
                "<redacted>" if is_sensitive_key(k) else redact_diagnostics(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_diagnostics(item, _depth + 1) for item in obj]
    if isinstance(obj, str):
        return _scrub_diagnostic_str(obj)
    return obj


# Config keys safe to disclose to a cap_config_read access key. Allowlist, not denylist,
# so a new HA config key defaults to excluded. Deliberately omits precise location
# (latitude/longitude/elevation/radius), internal_url/external_url, and filesystem
# paths (config_dir, allowlist_external_dirs/urls, media_dirs) which reveal home
# coordinates, network topology, and host layout beyond what an agent needs.
_SAFE_CONFIG_KEYS = frozenset({
    "location_name", "time_zone", "unit_system", "currency", "country",
    "language", "version", "config_source", "state", "safe_mode",
    "recovery_mode", "components",
})


def build_safe_config(hass: HomeAssistant) -> dict:
    """Return the cap_config_read-safe subset of hass.config.as_dict().

    Agent-useful context (HA version, time zone, units, location name, loaded
    components for capability detection) with the sensitive fields removed. Phoenix
    MCP's own components are stripped so an access key cannot enumerate its routes.
    """
    raw = hass.config.as_dict()
    safe = {k: raw[k] for k in _SAFE_CONFIG_KEYS if k in raw}
    if "components" in safe:
        safe["components"] = sorted(
            c for c in safe["components"]
            if c != DOMAIN and not c.startswith(DOMAIN + ".")
        )
    return safe


class SystemLogUnavailableError(Exception):
    """Raised when the system-log source is absent."""

    status = "unavailable"


class SystemLogDegradedError(Exception):
    """Raised when the system-log source exists but cannot be read safely."""

    status = "degraded"


def check_system_log_compat(hass: HomeAssistant) -> str | None:
    """Best-effort startup probe that hass.data["system_log"] has the shape get_logs reads.

    Runtime reads validate the same shape and fail loudly; this is an early
    operator warning. Returns None when compatible OR when system_log is absent,
    and a short reason string on shape drift.
    """
    try:
        syslog = hass.data.get("system_log")
        if syslog is None:
            return None
        records = getattr(syslog, "records", None)
        if records is None:
            return "system_log data has no 'records' attribute"
        if not callable(getattr(records, "values", None)):
            return "system_log records is no longer a dict-like mapping"
        return None
    except Exception as err:  # noqa: BLE001 - advisory probe must never abort setup
        return f"system_log probe could not run: {err}"


@dataclass(frozen=True)
class LogEntryPage:
    """One stateless page over Home Assistant's live deduplicated log ring."""

    entries: list[dict]
    matched_buckets: int
    has_more: bool
    next_cursor: str | None
    source: dict[str, Any]
    filters: dict[str, Any]
    warnings: list[str]
    byte_limited: bool = False
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the response envelope, the same one measured against the byte budget."""
        body: dict[str, Any]
        if self.detail is not None:
            body = {"source": self.source, "detail": self.detail, "payload": log_payload_budget()}
            if self.warnings:
                body["warnings"] = self.warnings
            return body
        body = {
            "source": self.source,
            "filters": self.filters,
            "count": len(self.entries),
            "matched_buckets": self.matched_buckets,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
            "entries": self.entries,
            "payload": log_payload_budget(byte_limited=self.byte_limited),
        }
        if self.warnings:
            body["warnings"] = self.warnings
        return body


_LOG_CURSOR_VERSION = 1
_LOG_REDACTED_PATH = "<redacted-path>"
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ULID_RE = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", re.IGNORECASE)
_LONG_HEX_RE = re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE)
_ENTITY_ID_TEXT_RE = re.compile(r"(?<![\w.])([a-z0-9_]+\.[a-z0-9_]+)(?!\w|\.\w)")


def redact_inaccessible_entity_ids_in_text(
    text: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    sentinel: str = "<redacted-entity>",
) -> str:
    """Redact inaccessible entity IDs embedded in otherwise free-form text."""
    def _replacement(match: re.Match[str]) -> str:
        try:
            entity_id = match.group(1)
            # Decimal literals are not entity references. Keep actual or registered
            # numeric-looking IDs under the normal scope check, including disabled ones.
            if (all(part.isdecimal() for part in entity_id.split("."))
                    and hass.states.get(entity_id) is None
                    and er.async_get(hass).async_get(entity_id) is None):
                return entity_id
            permission = resolve(entity_id, access_key, hass)
        except Exception:
            return sentinel
        if permission in (Permission.READ, Permission.WRITE):
            return match.group(1)
        return sentinel

    return _ENTITY_ID_TEXT_RE.sub(_replacement, text)


def _logger_prefix_matches(logger_name: str, prefix: str) -> bool:
    """Match one complete logger namespace, never an adjacent lookalike."""
    return logger_name == prefix or logger_name.startswith(prefix + ".")


def _log_iso(timestamp: float) -> str:
    """Return a stable UTC ISO timestamp for one system-log epoch value."""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _log_timestamp(value: Any) -> float | None:
    """Coerce one system-log timestamp without accepting booleans or infinities."""
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        value = value.timestamp()
    if not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _safe_log_path(path: Any, hass: HomeAssistant) -> str:
    """Return only a normalized HA package or custom-component relative path."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return _LOG_REDACTED_PATH
    normalized = path.replace("\\", "/")
    config_dir = str(getattr(hass.config, "config_dir", "") or "").replace("\\", "/").rstrip("/")
    if config_dir and normalized.startswith(config_dir + "/"):
        normalized = normalized[len(config_dir) + 1 :]
    elif "/custom_components/" in normalized:
        normalized = "custom_components/" + normalized.split("/custom_components/", 1)[1]
    elif "/homeassistant/" in normalized:
        normalized = "homeassistant/" + normalized.split("/homeassistant/", 1)[1]

    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        return _LOG_REDACTED_PATH
    safe_roots = ("homeassistant", "components", "custom_components")
    if not pure.parts or pure.parts[0] not in safe_roots:
        return _LOG_REDACTED_PATH
    return pure.as_posix()


def _safe_log_location(value: Any, hass: HomeAssistant, *, root_cause: bool) -> dict | None:
    """Normalize a system-log source or root-cause tuple without host paths."""
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        return None
    line = value[1]
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        line = None
    result: dict[str, Any] = {"file": _safe_log_path(value[0], hass), "line": line}
    if root_cause and len(value) >= 3:
        function = value[2]
        result["function"] = function if isinstance(function, str) and function.isidentifier() else None
    return result


def _strict_diagnostic_text(text: str) -> str:
    """Scrub diagnostic credentials, topology and opaque identifiers."""
    scrubbed = _scrub_diagnostic_str(_scrub_log_text(text))
    scrubbed = _UUID_RE.sub("<redacted-id>", scrubbed)
    scrubbed = _ULID_RE.sub("<redacted-id>", scrubbed)
    scrubbed = _LONG_HEX_RE.sub("<redacted-id>", scrubbed)

    return scrubbed


def _strict_phoenix_log_text(text: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """Apply the stronger Phoenix self-diagnostics text boundary."""
    return redact_inaccessible_entity_ids_in_text(_strict_diagnostic_text(text), access_key, hass)


def _log_filter_fingerprint(filters: dict[str, Any], phoenix_only: bool) -> str:
    """Hash the effective filters so a cursor cannot silently change queries."""
    payload = {**filters, "phoenix_only": phoenix_only}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _encode_log_cursor(
    *,
    snapshot_at: float,
    timestamp: float,
    bucket_id: str,
    filter_hash: str,
    since: float | None,
    until: float | None,
) -> str:
    payload = {
        "v": _LOG_CURSOR_VERSION,
        "snapshot": snapshot_at,
        "timestamp": timestamp,
        "bucket": bucket_id,
        "filters": filter_hash,
        "since": since,
        "until": until,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_log_cursor(
    cursor: str, filter_hash: str
) -> tuple[float, tuple[float, str], float | None, float | None]:
    """Decode and bind one opaque log cursor to the current effective filters."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(payload, dict) or payload.get("v") != _LOG_CURSOR_VERSION:
            raise ValueError
        snapshot = _log_timestamp(payload.get("snapshot"))
        timestamp = _log_timestamp(payload.get("timestamp"))
        since = _log_timestamp(payload.get("since")) if payload.get("since") is not None else None
        until = _log_timestamp(payload.get("until")) if payload.get("until") is not None else None
        bucket = payload.get("bucket")
        if (
            snapshot is None
            or timestamp is None
            or not isinstance(bucket, str)
            or len(bucket) != 64
            or any(character not in "0123456789abcdef" for character in bucket)
            or payload.get("filters") != filter_hash
        ):
            raise ValueError
    except Exception as err:
        raise ValueError("Invalid cursor for these log filters.") from err
    return snapshot, (timestamp, bucket), since, until


def _log_bucket_id(record: Any) -> str:
    """Return a non-reversible deterministic tie-breaker for one deduplicated bucket."""
    identity = getattr(record, "key", None)
    if identity is None:
        identity = (
            getattr(record, "name", None),
            getattr(record, "source", None),
            getattr(record, "root_cause", None),
        )
    return hashlib.sha256(repr(identity).encode(errors="replace")).hexdigest()


def _log_messages(value: Any) -> list[str] | None:
    """Copy Home Assistant's retained message variants without accepting mappings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return None
    try:
        messages = list(value)
    except Exception:
        return None
    if not messages or not all(isinstance(item, str) for item in messages):
        return None
    return messages


def _log_exception(value: Any) -> str | None:
    """Normalize Home Assistant's current string and older iterable exception shapes."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return "".join(value) or None
    return None


def collect_log_entries(
    hass: HomeAssistant,
    level: str,
    integration: str | None,
    logger: str | None,
    search: str | None,
    since: datetime | None,
    until: datetime | None,
    since_query: str | None,
    until_query: str | None,
    limit: int,
    cursor: str | None,
    *,
    phoenix_only: bool = False,
    access_key: AccessKeyRecord | None = None,
    detail_ref: str | None = None,
    detail_field: str = "message",
    message_index: int = 0,
    detail_offset: int = 0,
) -> LogEntryPage:
    """Read, validate, scrub, filter, and paginate the live system-log ring."""
    min_rank = _LOG_LEVEL_RANK.get(level.upper(), _LOG_LEVEL_RANK["WARNING"])
    syslog = hass.data.get("system_log")
    if syslog is None:
        raise SystemLogUnavailableError("the system_log integration is not loaded")
    records = getattr(syslog, "records", None)
    values = getattr(records, "values", None)
    if records is None or not callable(values):
        raise SystemLogDegradedError("system_log records is no longer a mapping")

    capacity = getattr(records, "maxlen", None)
    capacity_valid = isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0
    warnings: list[str] = []
    if not capacity_valid:
        capacity = None
        warnings.append("The system-log ring capacity is unavailable on this Home Assistant version.")

    cursor_filters = {
        "level": level,
        "integration": integration,
        "logger": logger,
        "search": search,
        "since": since_query,
        "until": until_query,
        "time_basis": "latest_occurrence",
    }
    filter_hash = _log_filter_fingerprint(cursor_filters, phoenix_only)
    if cursor:
        snapshot_at, cursor_key, cursor_since, cursor_until = _decode_log_cursor(cursor, filter_hash)
        since_ts = cursor_since
        until_ts = cursor_until
    else:
        snapshot_at, cursor_key = utcnow().timestamp(), None
        since_ts = since.timestamp() if since else None
        until_ts = until.timestamp() if until else None
    effective_filters = {
        **cursor_filters,
        "since": _log_iso(since_ts) if since_ts is not None else None,
        "until": _log_iso(until_ts) if until_ts is not None else None,
    }
    entries_with_keys: list[tuple[tuple[float, str], dict]] = []
    retained_first: list[float] = []
    retained_latest: list[float] = []
    skipped = 0
    try:
        raw_records = list(values())
    except Exception as err:
        raise SystemLogDegradedError(
            "system_log records could not be read"
        ) from err
    for record in raw_records:
        record_level = getattr(record, "level", "")
        logger_name = getattr(record, "name", "")
        latest = _log_timestamp(getattr(record, "timestamp", None))
        first = _log_timestamp(getattr(record, "first_occurred", None))
        messages = _log_messages(getattr(record, "message", None))
        count = getattr(record, "count", 1)
        if (
            not isinstance(record_level, str)
            or record_level not in _LOG_LEVEL_RANK
            or not isinstance(logger_name, str)
            or not logger_name
            or latest is None
            or first is None
            or messages is None
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            skipped += 1
            continue
        retained_first.append(first)
        retained_latest.append(latest)
        is_phoenix = any(
            _logger_prefix_matches(logger_name, prefix)
            for prefix in _PHOENIX_LOGGER_PREFIXES
        )
        if phoenix_only != is_phoenix:
            continue
        if _LOG_LEVEL_RANK[record_level] < min_rank:
            continue
        if integration:
            if not (
                _logger_prefix_matches(
                    logger_name, f"homeassistant.components.{integration}"
                )
                or _logger_prefix_matches(
                    logger_name, f"custom_components.{integration}"
                )
            ):
                continue
        if logger and not _logger_prefix_matches(logger_name, logger):
            continue
        if since_ts is not None and latest < since_ts:
            continue
        if until_ts is not None and latest > until_ts:
            continue
        if latest > snapshot_at:
            continue

        scrub: Callable[[str], str] = _scrub_log_text
        if phoenix_only:
            if access_key is None:
                raise SystemLogDegradedError("Phoenix diagnostics access key context is unavailable")
            scrub = lambda value: _strict_phoenix_log_text(value, access_key, hass)
        safe_messages = [scrub(message) for message in messages]
        exception = _log_exception(getattr(record, "exception", None))
        safe_exception = scrub(exception) if exception else None
        if search:
            needle = search.casefold()
            searchable = [*safe_messages, safe_exception or ""]
            if not any(needle in value.casefold() for value in searchable):
                continue

        bucket_id = _log_bucket_id(record)
        sort_key = (latest, bucket_id)
        entries_with_keys.append((sort_key, {
            "first_occurred": _log_iso(first),
            "latest_occurred": _log_iso(latest),
            "level": record_level,
            "logger": logger_name,
            "messages": safe_messages,
            "exception": safe_exception,
            "occurrences": count,
            "source": _safe_log_location(getattr(record, "source", None), hass, root_cause=False),
            "root_cause": _safe_log_location(getattr(record, "root_cause", None), hass, root_cause=True),
        }))

    entries_with_keys.sort(key=lambda item: item[0], reverse=True)
    matched_buckets = len(entries_with_keys)
    if cursor_key is not None:
        entries_with_keys = [item for item in entries_with_keys if item[0] < cursor_key]
    if skipped:
        warnings.append(f"Skipped {skipped} malformed system-log bucket(s).")
    if cursor:
        warnings.append(
            "Pagination is best-effort over a live deduplicated ring; buckets updated after the first page may move."
        )
    source = {
        "status": "degraded" if skipped or not capacity_valid else "available",
        "kind": "home_assistant_system_log",
        "semantics": "deduplicated_buckets",
        "pagination": "best_effort_live_ring",
        "capacity": capacity,
        "retained_buckets": len(raw_records),
        "skipped_buckets": skipped,
        "earliest_first_occurred": _log_iso(min(retained_first)) if retained_first else None,
        "latest_occurred": _log_iso(max(retained_latest)) if retained_latest else None,
        "read_at": _log_iso(snapshot_at),
        "recorded_levels": ["WARNING", "ERROR", "CRITICAL"],
    }
    def reference(key: tuple[float, str], entry: dict) -> str | None:
        return log_detail_ref(entry, access_key, phoenix_only=phoenix_only, bucket_id=key[1]) if access_key else None

    if detail_ref:
        for key, entry in entries_with_keys:
            current_ref = reference(key, entry)
            if current_ref is not None and hmac.compare_digest(detail_ref, current_ref):
                detail = log_detail_chunk(entry, detail_ref, detail_field, message_index, detail_offset)
                detail_page = LogEntryPage([], 0, False, None, source, effective_filters, warnings, detail=detail)
                if log_result_size(detail_page.as_dict()) > SYSTEM_LOG_MAX_RESPONSE_BYTES:
                    raise SystemLogDegradedError("system_log metadata exceeds the response budget")
                return detail_page
        raise ValueError("Invalid or stale log detail reference. Restart the query.")

    def page(items: list[tuple[tuple[float, str], dict]], *, byte_limited: bool = False) -> LogEntryPage:
        has_more = len(entries_with_keys) > len(items)
        next_cursor = None
        if has_more and items:
            last_key = items[-1][0]
            next_cursor = _encode_log_cursor(
                snapshot_at=snapshot_at, timestamp=last_key[0], bucket_id=last_key[1],
                filter_hash=filter_hash, since=since_ts, until=until_ts,
            )
        return LogEntryPage(
            entries=[entry for _, entry in items], matched_buckets=matched_buckets,
            has_more=has_more, next_cursor=next_cursor,
            source=source, filters=effective_filters, warnings=warnings, byte_limited=byte_limited,
        )

    page_items: list[tuple[tuple[float, str], dict]] = []
    for key, entry in entries_with_keys[:limit]:
        candidate = [*page_items, (key, compact_log_entry(entry, reference(key, entry)))]
        if log_result_size(page(candidate).as_dict()) > SYSTEM_LOG_MAX_RESPONSE_BYTES:
            if not page_items:
                raise SystemLogDegradedError("system_log metadata exceeds the response budget")
            return page(page_items, byte_limited=True)
        page_items = candidate
    return page(page_items)


@callback
def flush_sensor_writes(data: PhoenixData, _now=None) -> None:
    """Write HA state for every access key marked dirty since the last flush.

    Runs at most once per debounce window (scheduled by update_access_key_counter).
    Access keys revoked or archived in the window drop out: their access_key_id_sensors
    entry is already gone.

    Must be @callback: async_write_ha_state() requires the event loop thread, and
    without the decorator HassJob would treat this plain sync function, passed to
    async_call_later, as an executor job.
    """
    data.sensor_flush_cancel = None
    dirty = data.sensor_write_dirty
    data.sensor_write_dirty = set()
    for access_key_id in dirty:
        for sensor in data.access_key_id_sensors.get(access_key_id, []):
            if sensor.hass is not None:
                sensor.async_write_ha_state()


def update_access_key_counter(data: PhoenixData, access_key_id: str, outcome: str) -> None:
    """Increment the in-memory request/denied/rate-limit counters for an access key.

    Counters are initialised on first use and read by sensor.py and, directly, by
    the admin stats view. Sensor state writes are debounced: request_count and
    last_access change on EVERY request, and an immediate async_write_ha_state
    would emit one state_changed event and recorder row per agent call. The first
    request after a flush schedules one write SENSOR_WRITE_DEBOUNCE_SECONDS out;
    requests inside the window coalesce into it.
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


# Tool argument coercion
def dict_arg(value: object) -> dict:
    """A tool argument declared as an object, coerced to a dict.

    The dict sibling of str_arg, with the same degrade-to-absent rule: a list or
    bare string where a config object was declared yields {}, not a coerced
    interpretation. Preferred over `args.get(k) if isinstance(args.get(k), dict)
    else {}`, which looks the key up twice and leaves the result unnarrowed for a
    type checker. Always returns a dict, so a caller that must tell "absent" from
    "empty" (the card diff builders) cannot use it.
    """
    return value if isinstance(value, dict) else {}


def sanitize_service_data(value: object) -> dict[str, Any]:
    """Caller-supplied service data with every HA target selector removed.

    Phoenix MCP flattens targets itself and calls the service with an explicit
    entity list, so a selector inside service data is never honoured: HA unions it
    with that list, reaching entities the permission tree, the capability gates
    and MESA never evaluated. Public service-call ingress already refuses such
    selectors before admission; this defensively strips them for internal callers
    and does not replace that validation. The keys are const.TARGET_SELECTOR_KEYS,
    which a guard test pins against HA's own definition.

    Returns a NEW dict and never mutates the caller's, which also gives each call
    site a snapshot: evaluation suspends at an await, and a caller still holding
    the original could otherwise swap the target between the check and the call.
    """
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k not in TARGET_SELECTOR_KEYS}


def str_arg(value: object, default: str = "") -> str:
    """A tool argument declared as a string, coerced to one.

    A model does not always follow the tool schema: a list where a string was
    declared would reach HA's matcher and raise. A wrong-shaped value degrades to
    "absent" rather than being interpreted, so the call fails closed on its own
    terms instead of crashing into the dispatcher's catch-all. Deliberately NOT
    str(value): stringifying True or ["a"] invents an argument the caller never
    sent, which would be refused further down with a message about a value nobody
    typed.
    """
    return value if isinstance(value, str) else default


def str_list_arg(value: object) -> list[str]:
    """A tool argument declared as a string or a list of strings, coerced.

    Non-string members are dropped rather than stringified, for the reason in
    str_arg; a comma-joined string is NOT split here, since only some tools
    accept that form and each does its own splitting.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


# Capability evaluation
#
# async_evaluate_capability returns a CapabilityResult:
#   mode "allow"                -> proceed to the side effect.
#   mode "deny"                 -> return forbidden to the caller.
#   mode "confirm" + approval   -> a pending approval was created; return the pending response.
#
# effective_cap collapses pass_through interaction into a single value used by
# self-summary endpoints. It is NOT a substitute for async_evaluate_capability when
# enforcing a check, because it does not go through the approval queue.


def effective_cap(access_key: AccessKeyRecord, cap_name: str) -> str:
    """Return the cap mode after applying pass_through interaction rules.

    Exempt caps are unaffected by pass_through. For non-exempt caps under
    pass_through, "deny" becomes "allow" but "confirm" is preserved (the admin's
    intent to gate is honored). On household ingress a non-administrator gets
    "deny" for every cap outside HOUSEHOLD_CAPABILITIES.
    """
    from .const import HOUSEHOLD_CAPABILITIES
    from .household_authority import household_authority

    household = household_authority.get()
    if household is not None and not household.is_admin() and cap_name not in HOUSEHOLD_CAPABILITIES:
        return CAP_DENY
    raw = getattr(access_key, cap_name, CAP_DENY)
    if cap_name in PASS_THROUGH_EXEMPT_CAPS:
        return raw
    if access_key.pass_through:
        if raw == CAP_CONFIRM:
            return CAP_CONFIRM
        return CAP_ALLOW
    return raw


def access_key_has_write_scope(access_key: AccessKeyRecord) -> bool:
    """True if the access key can write to at least one entity.

    Decides whether tools/list announces the control tools (call_service, the
    native Hass* action tools). Pass-through always has write scope; otherwise any
    GREEN grant in the permission tree counts. This is an advisory
    over-approximation (a GREEN under a RED ancestor still counts); the per-call
    permission check is the real gate.
    """
    if access_key.pass_through:
        return True
    tree = access_key.permissions
    for nodes in (tree.domains, tree.devices, tree.entities):
        for node in nodes.values():
            if node.state == "GREEN":
                return True
    return False


def effective_caps(access_key: AccessKeyRecord) -> dict[str, str]:
    """Return {cap name: effective mode} for every capability of an access key."""
    return {name: effective_cap(access_key, name) for name in CAPABILITY_NAMES}


@dataclass
class CapabilityResult:
    """Outcome of an async_evaluate_capability call.

    mode is one of "allow" / "deny" / "confirm". When mode is "confirm",
    approval is the freshly created PendingApproval record and the caller
    must return a pending response without executing.
    """

    mode: str
    approval: Any | None = None

    @property
    def is_allow(self) -> bool:
        return self.mode == CAP_ALLOW

    @property
    def is_deny(self) -> bool:
        return self.mode == CAP_DENY

    @property
    def is_pending(self) -> bool:
        return self.mode == CAP_CONFIRM


# An approval diff, either already built or deferred behind a zero-arg builder.
# Most builders read config off disk (or call the ESPHome add-on) while the diff
# is read only when an approval is created, so deferring spares an allow or a deny
# a result nobody looks at.
DiffSource = dict | Callable[[], "dict | Awaitable[dict]"] | None


async def _resolve_diff(diff: DiffSource) -> dict:
    """Resolve a diff given either as a built dict or as a deferred builder.

    Expensive builders (config off disk, a lovelace config, the ESPHome add-on)
    are passed as a zero-arg callable and invoked here, on the confirm path alone;
    cheap builders may pass a plain dict. Awaitability is tested with
    collections.abc.Awaitable rather than inspect, so sync and async builders can
    be mixed at the call sites.
    """
    if diff is None:
        return {}
    if not callable(diff):
        return diff or {}
    built = diff()
    if isinstance(built, Awaitable):
        built = await built
    return built or {}


async def async_evaluate_capability(
    cap_name: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    *,
    tool_name: str,
    args: dict,
    request_id: str,
    diff: DiffSource = None,
    client_ip: str | None = None,
    target_entities: list[str] | None = None,
) -> CapabilityResult:
    """Resolve a capability check into Allow / Deny / Pending(approval).

    Reads the effective cap mode (after pass-through interaction) and either
    permits, denies, or creates a pending approval and returns a Confirm result.
    Diff is supplied by the caller; it appears in the admin review UI. It may be
    a dict or a zero-arg builder, and a builder is invoked only on the Confirm
    path, so an allowed or denied call never pays for a diff nobody reads.
    """
    from .approvals import (  # noqa: PLC0415
        create_approval_notification,
        async_create_pending_approval,
        fire_approval_requested_event,
    )

    mode = effective_cap(access_key, cap_name)
    if mode == CAP_ALLOW:
        return CapabilityResult(CAP_ALLOW)
    if mode == CAP_DENY:
        return CapabilityResult(CAP_DENY)
    # The diff is needed only on the confirm path, so a deferred builder runs here
    # and nowhere else. Both early returns above leave it untouched.
    if target_entities is not None:
        from .approval_targets import bind_targets, bound_preview
        args = bind_targets(args, target_entities, hass)
    resolved_diff = await _resolve_diff(diff)
    if target_entities is not None:
        resolved_diff = bound_preview(resolved_diff, args)
    async with data.store.async_lock:
        check_request_authority()
        approval = await async_create_pending_approval(
            data.store,
            access_key_id=access_key.id,
            access_key_name=access_key.name,
            tool_name=tool_name,
            cap_name=cap_name,
            args=args,
            diff=resolved_diff,
            request_id=request_id,
            client_ip=client_ip,
        )
    create_approval_notification(hass, approval)
    fire_approval_requested_event(hass, approval)
    return CapabilityResult(CAP_CONFIRM, approval=approval)


def validation_error_message(err: ServiceValidationError | vol.Invalid) -> str:
    """Human-readable message for a service validation error shown to the agent.

    Covers ServiceValidationError and vol.Invalid/MultipleInvalid, which
    hass.services.async_call re-raises unwrapped rather than as a
    HomeAssistantError. A ServiceValidationError carrying only a translation key
    can render as an empty string (translations are not loaded in-process), so an
    empty detail falls back to a generic message.
    """
    detail = str(err).strip()
    return f"Invalid request: {detail}" if detail else "Invalid request."


def diff_summary_fields(key: str, **params: object) -> dict[str, object]:
    """Build a diff's three summary fields from one template.

    Returns the English `summary` plus the `summary_key` and `summary_params` the
    panel needs to render the same sentence in the operator's language; splat it
    into a diff dict. One template per key in const.DIFF_SUMMARY_TEMPLATES keeps
    the stored English and the translated form from drifting. An unknown key
    raises KeyError on purpose: a blank summary on the surface an admin approves
    from would be worse.
    """
    return _summary_fields("diff", DIFF_SUMMARY_TEMPLATES, key, params)


def version_summary_fields(key: str, **params: object) -> dict[str, object]:
    """The same three fields for a version record's one-line change summary.

    A separate namespace from the diff templates: a diff describes a proposed
    change, a version record one already applied.
    """
    return _summary_fields("version", VERSION_SUMMARY_TEMPLATES, key, params)


def _summary_fields(
    namespace: str, templates: dict[str, str], key: str, params: dict[str, object]
) -> dict[str, object]:
    return {
        "summary": templates[key].format(**params),
        "summary_key": f"{namespace}.{key}",
        "summary_params": params,
    }


def notification_text(hass: object, key: str, **params: object) -> str:
    """One persistent-notification string in the instance's language.

    A notification is shown to every admin, so unlike the panel there is no
    per-viewer language: this resolves against hass.config.language. Reads the
    same catalogs/<lang>.json as the panel and falls back to the English template
    when the catalog cannot be read or lacks the key.
    """
    language = getattr(getattr(hass, "config", None), "language", None)
    return _localized(NOTIFICATION_TEMPLATES, "notification", key, language, params)


def voice_text(language: object, key: str, **params: object) -> str:
    """One spoken voice-agent sentence, in the CONVERSATION's language.

    The opposite resolution from notification_text: a spoken reply goes to
    whoever is talking, so it follows the language HA passes to async_process.
    Falls back to English for an unknown language like the notification path.
    """
    return _localized(VOICE_TEMPLATES, "voice", key, language, params)


def _localized(
    templates: dict[str, str], section: str, key: str, language: object, params: dict
) -> str:
    """Resolve one backend-rendered string, falling back to its English template."""
    template = templates[key]
    resolved = canonical_language(language)
    if resolved != "en":
        translated = _catalog_section(resolved, section).get(key)
        if translated:
            template = translated
    return template.format(**params)


@lru_cache(maxsize=16)
def _catalog_section(language: str, section: str) -> dict[str, str]:
    """One top-level section of one language file, flattened to dotted keys.

    Reads catalogs/, NOT translations/: hassfest validates translations/ against a
    CLOSED set of Home Assistant categories and rejects the file on an unknown
    one, so Phoenix's own operator-facing strings (panel, notification, voice)
    live in catalogs/ (see CATALOGS_DIR). Only HA's own sections (config, entity)
    stay in translations/. Cached per (language, section) because the file
    changes only with the integration; any failure returns an empty mapping so
    the caller keeps the English.
    """
    path = CATALOGS_DIR / f"{language}.json"
    try:
        section_data = json.loads(path.read_text(encoding="utf-8")).get(section, {})
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}

    def walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else key)
        elif isinstance(node, str):
            out[prefix] = node

    walk(section_data, "")
    return out


def _placeholders(value: str) -> frozenset[str]:
    """The {placeholder} names in one catalog string, ignoring literal text."""
    return frozenset(
        field for _, field, _, _ in string.Formatter().parse(value) if field
    )


@lru_cache(maxsize=8)
def panel_catalog(language: str) -> dict[str, str]:
    """The panel's strings for one language, English-backed, flattened.

    English is the base and the requested language is overlaid, so an untranslated
    key resolves to English rather than rendering as a raw key. A translated
    string whose {placeholder} set differs from English is DROPPED in favour of
    the English one: the panel interpolates by name, so a renamed, dropped or
    invented placeholder would render a broken sentence or throw. A guard test
    already refuses to let a mismatched locale ship, so this is the runtime
    backstop for a hand-edited install.
    """
    resolved = canonical_language(language)
    english = _catalog_section("en", "panel")
    if resolved == "en":
        return english
    merged = dict(english)
    for key, value in _catalog_section(resolved, "panel").items():
        base = english.get(key)
        if base is not None and _placeholders(base) != _placeholders(value):
            continue
        merged[key] = value
    return merged


def log_internal_failure(logger: logging.Logger, phase: str, exc: Exception) -> None:
    """Log fixed failure metadata and Phoenix frame locations, never error text or locals."""
    from .request_authority import dispatch_request_id
    frames = []
    traceback = exc.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        if "/custom_components/phoenix_mcp/" in code.co_filename:
            frames.append(f"{code.co_filename.rsplit('/', 1)[-1]}:{traceback.tb_lineno}:{code.co_name}")
        traceback = traceback.tb_next
    logger.warning("Phoenix internal failure phase=%s type=%s rid=%s frames=%s",
                   phase, type(exc).__name__, dispatch_request_id.get() or "-", ",".join(frames[-8:]))
