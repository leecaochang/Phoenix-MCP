"""Authenticate an access key and retire one that has expired."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util.dt import utcnow

from .access_key_store import access_key_name_slug
from .approvals import (
    REASON_ACCESS_KEY_EXPIRED, async_cancel_approvals_for_access_key, audit_closed_approvals,
    dismiss_approval_notification, fire_approval_resolved_event,
)
from .const import (
    ACCESS_KEY_LENGTH, ACCESS_KEY_PREFIX, OAUTH_ACCESS_TOKEN_LENGTH, OAUTH_ACCESS_TOKEN_PREFIX,
    OAUTH_GRANT_REQUEST_KEY, OAUTH_REFRESH_TOKEN_PREFIX, OAUTH_TOKEN_UNTIL_REQUEST_KEY, REFUSED_KEY_LOG_WINDOW_SECONDS,
    REFUSED_KEY_TRACKED_ADDRESSES_MAX,
)
from .helpers import (
    build_error_response, cancel_expiry_timer, fire_rate_limit_events, get_client_ip, log_request,
)
from .mesa import release_access_key_leases
from .oauth_flow import async_end_sign_ins, bearer_challenge
from .oauth_grants import secret_hash
from .voice_agent import async_remove_assist_pipeline

if TYPE_CHECKING:
    from .access_key_store import AccessKeyRecord
    from .data import PhoenixData
    from .rate_limiter import RateLimitResult

_LOGGER = logging.getLogger(__name__)


def unauthorized_response(request_id: str, challenge: str = 'Bearer realm="Phoenix MCP"') -> web.Response:
    """The 401 for a bearer key that is absent, unknown or no longer valid.

    ``challenge`` comes from oauth_flow.bearer_challenge, which leaves the plain realm
    unless a key offers OAuth sign-in.
    """
    response = build_error_response("unauthorized", "Unauthorized.", 401, request_id)
    response.headers["WWW-Authenticate"] = challenge
    return response


# What a Phoenix key or token starts with; one in a web address ends up in proxy and browser logs.
_SECRET_PREFIXES = (ACCESS_KEY_PREFIX, OAUTH_ACCESS_TOKEN_PREFIX, OAUTH_REFRESH_TOKEN_PREFIX)


def _secret_in_query(request: web.Request) -> bool:
    """Whether the query names a token parameter or carries a Phoenix secret under any name."""
    if "token" in request.query or "access_token" in request.query:
        return True
    return any(prefix in part.lower() for name, value in request.query.items()
               for part in (name, value) for prefix in _SECRET_PREFIXES)


async def async_get_authenticated_access_key(
    hass: HomeAssistant,
    request: web.Request,
    data: PhoenixData,
    request_id: str,
    resource: str,
    *,
    burst: bool = True,
) -> tuple[AccessKeyRecord, RateLimitResult] | web.Response:
    """Validate the Phoenix MCP bearer access key and check rate limits.

    ``burst`` False charges the per-minute limit only: for closing a session, a handshake message,
    and for a POST, whose burst is charged once its body shows it isn't one (RateLimiter.charge_burst).

    Returns (access_key, rl_result), or an aiohttp Response on failure. Checks readiness, kill
    switch, query-param leakage, format, hash, revocation, expiry and rate limits, in order.
    An OAuth access token resolves to the key its sign-in is bound to and then takes the same
    path; the sign-in ID is left on the request under OAUTH_GRANT_REQUEST_KEY.
    """
    if not data.ready or data.shutting_down:
        return build_error_response("service_unavailable", "Service unavailable.", 503, request_id)

    if data.store.get_settings().kill_switch:
        # Kill-switch mode should make Phoenix MCP invisible on the network. At
        # startup that means registering no routes; aiohttp cannot unregister routes
        # at runtime, so the routes stay and refuse service with 503.
        return build_error_response("service_unavailable", "Service unavailable.", 503, request_id)

    _401 = unauthorized_response(request_id, bearer_challenge(hass, data, request))

    if _secret_in_query(request):
        return _401

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _401

    presented = auth_header[7:]
    # Non-ASCII includes bytes that are not UTF-8, which aiohttp keeps as surrogates.
    if presented.startswith(OAUTH_ACCESS_TOKEN_PREFIX):
        if len(presented) != OAUTH_ACCESS_TOKEN_LENGTH or not presented.isascii():
            return _401
        access_key = _access_key_for_oauth_token(data, request, presented)
        if access_key is None:
            return unauthorized_response(request_id, bearer_challenge(hass, data, request, invalid_token=True))
    else:
        if not presented.startswith(ACCESS_KEY_PREFIX) or len(presented) != ACCESS_KEY_LENGTH or not presented.isascii():
            return _401

        access_key_hash = hashlib.sha256(presented.encode()).hexdigest()
        access_key = data.store.get_access_key_by_hash(access_key_hash)

        if access_key is None:
            note_refused_key(data, get_client_ip(request))
            return _401

        if not access_key.is_valid():
            if access_key.is_expired():
                await async_archive_expired_access_key(hass, data, access_key)
            note_refused_key(data, get_client_ip(request))
            return _401

    # Update last_used before the rate limit check so last_access reflects every
    # attempted request, not just allowed ones. This keeps last_access consistent
    # with request_count, which also increments on rate-limited requests.
    data.store.update_last_used(access_key.id, utcnow())

    rl_result = data.rate_limiter.check(
        access_key.id,
        access_key.rate_limit_requests,
        access_key.rate_limit_burst,
        burst=burst,
    )

    if not rl_result.allowed:
        fire_rate_limit_events(hass, data, access_key)
        # No request authority exists yet, so the sign-in is named here.
        grant = data.oauth_grants.get(request.get(OAUTH_GRANT_REQUEST_KEY))
        log_request(
            data,
            access_key,
            request_id=request_id,
            method=request.method,
            resource=resource,
            outcome="rate_limited",
            client_ip=get_client_ip(request),
            oauth_app=grant.audit_label() if grant is not None else None,
        )
        resp = build_error_response("rate_limited", "Rate limit exceeded.", 429, request_id)
        resp.headers["Retry-After"] = str(rl_result.retry_after)
        return resp

    return access_key, rl_result


def note_refused_key(data: PhoenixData, address: str | None) -> None:
    """Log a refused, well-formed access key from one address: once per window, later ones counted.

    Nothing of the key is logged. Home Assistant's failed-login handling is not used, as it
    notifies and can ban a household client still holding a rotated key.
    """
    addresses = data.refused_key_addresses
    address = address or "unknown address"
    now = time.monotonic()
    started, counted = addresses.pop(address, (None, 0))
    if started is not None and now - started < REFUSED_KEY_LOG_WINDOW_SECONDS:
        addresses[address] = (started, counted + 1)
        return
    minutes = REFUSED_KEY_LOG_WINDOW_SECONDS // 60
    if counted:
        _LOGGER.warning(
            "Refused an unknown, revoked or expired Phoenix MCP access key from %s, and %d more "
            "since the last report", address, counted)
    else:
        _LOGGER.warning(
            "Refused an unknown, revoked or expired Phoenix MCP access key from %s; further "
            "refusals from this address are counted and reported at most every %d minutes", address, minutes)
    addresses[address] = (now, 0)
    while len(addresses) > REFUSED_KEY_TRACKED_ADDRESSES_MAX:
        del addresses[next(iter(addresses))]


def _access_key_for_oauth_token(data: PhoenixData, request: web.Request, presented: str) -> AccessKeyRecord | None:
    """The access key an OAuth access token presents, or None when it presents nothing.

    The lookup in Phoenix's own sign-in store is the audience check: no other server's
    token can be found there. The sign-in it names, and when the token ends, are left on the
    request for RequestAuthority, which rechecks both at every authority boundary.
    """
    found = data.oauth_grants.find_access_token(secret_hash(presented))
    if found is None:
        return None
    grant, until = found
    access_key = data.store.get_access_key_by_id(grant.access_key_id)
    if access_key is None or not grant.is_live(access_key):
        return None
    data.oauth_grants.touch(grant.id, utcnow())
    request[OAUTH_GRANT_REQUEST_KEY] = grant.id
    request[OAUTH_TOKEN_UNTIL_REQUEST_KEY] = until
    return access_key


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

    Closes its queued approvals, destroys limiter and counter state, fires
    phoenix_mcp_access_key_expired and removes its sensors.
    """

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
            if _s.ai_task_decisions_access_key_id == current.id:
                _patch["ai_task_decisions_access_key_id"] = None
                _patch["ai_task_decisions_enabled"] = False
            if _patch:
                await data.store.async_patch_settings(**_patch)
            changed_approvals = await async_cancel_approvals_for_access_key(
                data.store, current.id, REASON_ACCESS_KEY_EXPIRED,
                skip_ids=data.approvals_in_progress,
            )
    for approval in changed_approvals:
        dismiss_approval_notification(hass, approval.id)
        fire_approval_resolved_event(hass, approval)
    audit_closed_approvals(data.audit, data.store.get_settings(), changed_approvals)
    # Advisory leases the access key still held; in-memory, so outside the store lock.
    release_access_key_leases(data, current.id)
    if data.async_sync_voice_agent is not None:
        data.async_sync_voice_agent()
    if "voice_agent_access_key_id" in _patch:
        # The voice agent lost its access key and is unregistered; remove any
        # Phoenix-created Assist pipeline so a broken assistant is not left behind.
        await async_remove_assist_pipeline(hass, data)
    if data.async_sync_ai_task is not None and any(
            key in _patch for key in ("ai_task_access_key_id", "ai_task_decisions_access_key_id")):
        # A surface lost its access key; remove its entity from HA (and picker).
        data.async_sync_ai_task()
    data.rate_limiter.destroy(current.id)
    data.rate_limit_notified.pop(current.id, None)
    data.access_key_counters.pop(current.id, None)
    data.stale_tools_advised.pop(current.id, None)
    await async_end_sign_ins(data, current.id)
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
