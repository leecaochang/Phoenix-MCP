"""OAuth sign-in flow state that lives in memory: windows, pending requests and codes.

Nothing here is persisted. A restart or reload closes every sign-in window and drops every
request and unredeemed code, and turning the kill switch on does the same through clear().
Also the protocol helpers shared by the OAuth views and the access key check: the public
origin, the 401 challenge, parameter checks and PKCE.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from .const import (
    OAUTH_ADDRESS_REQUESTS_PER_MINUTE, OAUTH_CODE_PREFIX, OAUTH_CODE_TTL_SECONDS, OAUTH_ISSUER_PATH,
    OAUTH_LOOPBACK_HOSTS, OAUTH_PENDING_MAX, OAUTH_PENDING_PER_ADDRESS_MAX, OAUTH_REQUEST_TTL_SECONDS,
    OAUTH_RESOURCE_PATH, OAUTH_TRACKED_ADDRESSES_MAX, OAUTH_URL_MAX_LENGTH, OAUTH_WINDOW_SECONDS,
)
from .oauth_grants import mint_secret, secret_hash

if TYPE_CHECKING:
    from .access_key_store import AccessKeyStore
    from .data import PhoenixData

_LOGGER = logging.getLogger(__name__)

_PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9\-_]{43}$")


@dataclass
class ClientVerdict:
    """What fetching a client's metadata document established."""

    ok: bool
    name: str = ""
    # Catalog key under adminError naming why the client could not be verified.
    problem: str = ""


@dataclass
class PendingRequest:
    """A sign-in started by an app and waiting for an administrator."""

    id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    state: str | None
    resource: str
    issuer: str
    address: str
    created_at: datetime
    expires_at: datetime
    verdict: ClientVerdict | None = None
    opened: bool = False
    # The one metadata fetch for this request, shared by concurrent panel loads.
    verdict_task: asyncio.Task | None = None


@dataclass
class IssuedCode:
    """An authorization code, known only by its hash."""

    access_key_id: str
    generation: int
    client_id: str
    client_name: str
    redirect_uri: str
    code_challenge: str
    resource: str
    created_by: str
    expires_at: datetime


@dataclass
class OAuthRuntime:
    """Per-process sign-in state. Bounded, and cleared by the kill switch."""

    # access key ID to the moment its sign-in window closes.
    windows: dict[str, datetime] = field(default_factory=dict)
    pending: OrderedDict[str, PendingRequest] = field(default_factory=OrderedDict)
    codes: dict[str, IssuedCode] = field(default_factory=dict)
    # A redeemed code's hash, to the sign-in it produced, until the code would have expired.
    redeemed: dict[str, tuple[str, datetime]] = field(default_factory=dict)
    # Address to (window start, count) for the unauthenticated endpoints.
    _address_hits: OrderedDict[str, tuple[float, int]] = field(default_factory=OrderedDict)

    def clear(self) -> None:
        """Forget every window, request and code (kill switch on)."""
        self.windows.clear()
        self.pending.clear()
        self.codes.clear()
        self.redeemed.clear()

    def forget_key(self, access_key_id: str) -> None:
        """Close a key's window and drop its unredeemed codes."""
        self.windows.pop(access_key_id, None)
        for code_hash in [h for h, c in self.codes.items() if c.access_key_id == access_key_id]:
            del self.codes[code_hash]

    # Windows

    def open_window(self, access_key_id: str) -> datetime:
        until = utcnow() + timedelta(seconds=OAUTH_WINDOW_SECONDS)
        self.windows[access_key_id] = until
        return until

    def window_until(self, access_key_id: str) -> datetime | None:
        until = self.windows.get(access_key_id)
        if until is not None and utcnow() >= until:
            del self.windows[access_key_id]
            return None
        return until

    def close_window(self, access_key_id: str) -> None:
        self.windows.pop(access_key_id, None)

    def any_window_open(self, store: AccessKeyStore) -> bool:
        return any(
            self.window_until(key.id) is not None and key.oauth_enabled and key.is_valid()
            for key in store.list_access_keys()
        )

    # Pending requests

    def _expire(self) -> None:
        now = utcnow()
        for request_id in [r.id for r in self.pending.values() if now >= r.expires_at]:
            del self.pending[request_id]
        for code_hash in [h for h, c in self.codes.items() if now >= c.expires_at]:
            del self.codes[code_hash]
        for code_hash in [h for h, (_g, until) in self.redeemed.items() if now >= until]:
            del self.redeemed[code_hash]

    def add_pending(self, **values) -> PendingRequest | None:
        """Store a new request, or None when this address already has too many.

        The global cap evicts the oldest request nobody has opened yet, then the oldest.
        """
        self._expire()
        address = values["address"]
        if sum(1 for r in self.pending.values() if r.address == address) >= OAUTH_PENDING_PER_ADDRESS_MAX:
            return None
        while len(self.pending) >= OAUTH_PENDING_MAX:
            victim = next((r.id for r in self.pending.values() if not r.opened), next(iter(self.pending)))
            del self.pending[victim]
        now = utcnow()
        request = PendingRequest(
            id=secrets.token_urlsafe(32), created_at=now,
            expires_at=now + timedelta(seconds=OAUTH_REQUEST_TTL_SECONDS), **values,
        )
        self.pending[request.id] = request
        return request

    def get_pending(self, request_id: str) -> PendingRequest | None:
        self._expire()
        return self.pending.get(request_id)

    def pop_pending(self, request_id: str) -> PendingRequest | None:
        self._expire()
        return self.pending.pop(request_id, None)

    # Codes

    def issue_code(self, **values) -> str:
        raw, code_hash = mint_secret(OAUTH_CODE_PREFIX)
        self.codes[code_hash] = IssuedCode(
            expires_at=utcnow() + timedelta(seconds=OAUTH_CODE_TTL_SECONDS), **values,
        )
        return raw

    def redeem_code(self, raw: str) -> tuple[IssuedCode | None, str | None]:
        """Take a code for exchange: (code, None), or (None, grant ID it already produced)."""
        self._expire()
        code_hash = secret_hash(raw)
        code = self.codes.pop(code_hash, None)
        if code is not None:
            return code, None
        reused = self.redeemed.get(code_hash)
        return None, reused[0] if reused else None

    def mark_redeemed(self, raw: str, grant_id: str) -> None:
        self.redeemed[secret_hash(raw)] = (grant_id, utcnow() + timedelta(seconds=OAUTH_CODE_TTL_SECONDS))

    # Unauthenticated request budget

    def address_allowed(self, address: str) -> bool:
        """Count one request from ``address``; False once it passes the per-minute budget."""
        now = time.monotonic()
        start, count = self._address_hits.pop(address, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        count += 1
        self._address_hits[address] = (start, count)
        while len(self._address_hits) > OAUTH_TRACKED_ADDRESSES_MAX:
            self._address_hits.popitem(last=False)
        return count <= OAUTH_ADDRESS_REQUESTS_PER_MINUTE


async def async_end_sign_ins(data: PhoenixData, access_key_id: str) -> None:
    """Clean up after a key's sign-ins ended (revoke, expiry, rotation, OAuth off).

    The key's own save already made them unusable; this closes its window, drops its codes
    and deletes the dead rows.
    """
    # Best effort: the rows are already unusable, so a failure here must never stop the
    # revocation, expiry or rotation that called it.
    try:
        data.oauth.forget_key(access_key_id)
        await data.oauth_grants.async_prune(lambda grant: data.store.get_access_key_by_id(grant.access_key_id))
    except Exception:
        _LOGGER.warning("Could not clean up the OAuth sign-ins of an ended access key", exc_info=True)


def public_origin(hass: HomeAssistant, request: web.Request) -> str | None:
    """The HTTPS origin this request reached Home Assistant by, or None.

    Only an origin Home Assistant itself is configured with counts, so a forged Host header
    cannot choose the issuer. None means OAuth sign-in stays dark for this request.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.network import NoURLAvailableError, get_url, is_hass_url
    try:
        url = get_url(hass, require_current_request=True, require_ssl=True)
    except NoURLAvailableError:
        url = None
    except Exception:  # an unexpected helper failure keeps sign-in dark, never open
        url = None
    if url is None and request.url.scheme == "https":
        # A proxy that sends only X-Forwarded-Host: accept the forwarded origin when it is
        # one Home Assistant is configured with.
        origin = str(request.url.origin())
        try:
            url = origin if is_hass_url(hass, origin) else None
        except ValueError:
            url = None
    if not url:
        return None
    return url.rstrip("/")


def configured_public_url(hass: HomeAssistant) -> str | None:
    """The instance's configured HTTPS address for the panel to show, or None."""
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.network import NoURLAvailableError, get_url
    try:
        return get_url(hass, require_ssl=True, allow_internal=False).rstrip("/")
    except NoURLAvailableError:
        pass
    except Exception:
        return None
    try:
        return get_url(hass, require_ssl=True).rstrip("/")
    except Exception:
        return None


def resource_url(origin: str) -> str:
    return origin + OAUTH_RESOURCE_PATH


def issuer_url(origin: str) -> str:
    return origin + OAUTH_ISSUER_PATH


def protected_resource_metadata_url(origin: str) -> str:
    return f"{origin}/.well-known/oauth-protected-resource{OAUTH_RESOURCE_PATH}"


def oauth_offered(data: PhoenixData) -> bool:
    """Whether any usable access key has OAuth sign-in switched on."""
    return any(key.oauth_enabled and key.is_valid() for key in data.store.list_access_keys())


def bearer_challenge(hass: HomeAssistant, data: PhoenixData, request: web.Request | None,
                     *, invalid_token: bool = False) -> str:
    """The WWW-Authenticate value for a 401 from Phoenix's MCP endpoint.

    Unchanged from the plain realm until a key offers OAuth sign-in and the request arrived
    over HTTPS; then it points the app at Phoenix's own protected-resource document.
    """
    challenge = 'Bearer realm="Phoenix MCP"'
    if request is None or not oauth_offered(data):
        return challenge
    origin = public_origin(hass, request)
    if origin is None:
        return challenge
    challenge += f', resource_metadata="{protected_resource_metadata_url(origin)}"'
    if invalid_token:
        challenge += ', error="invalid_token"'
    return challenge


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host in OAUTH_LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def valid_client_id(value: str | None) -> bool:
    """An https address with a path and nothing that could hide where it points."""
    if not value or len(value) > OAUTH_URL_MAX_LENGTH or not value.isascii():
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (parts.scheme == "https" and bool(parts.hostname) and parts.path not in ("", "/")
            and not parts.fragment and not parts.username and not parts.password
            and not _is_loopback_host(parts.hostname))


def valid_redirect_uri(value: str | None) -> bool:
    """An https address, or plain http to this machine's loopback, without a fragment."""
    if not value or len(value) > OAUTH_URL_MAX_LENGTH or not value.isascii():
        return False
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises on a malformed port
    except ValueError:
        return False
    if parts.fragment or parts.username or parts.password or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and _is_loopback_host(parts.hostname)


def redirect_matches(presented: str, registered: str) -> bool:
    """Exact match, except that a loopback address may use any port."""
    if presented == registered:
        return True
    try:
        a, b = urlsplit(presented), urlsplit(registered)
    except ValueError:
        return False
    if not (_is_loopback_host(a.hostname) and _is_loopback_host(b.hostname)):
        return False
    return (a.scheme, a.hostname, a.path, a.query) == (b.scheme, b.hostname, b.path, b.query)


def is_loopback_redirect(uri: str) -> bool:
    try:
        return _is_loopback_host(urlsplit(uri).hostname)
    except ValueError:
        return False


def redirect_host(uri: str) -> str:
    """The host an app returns to, in the ASCII form a person can compare."""
    try:
        host = urlsplit(uri).hostname or ""
    except ValueError:
        return ""
    return host


def valid_code_challenge(value: str | None) -> bool:
    return bool(value) and _PKCE_CHALLENGE_RE.fullmatch(value or "") is not None


def pkce_matches(verifier: str | None, challenge: str) -> bool:
    """S256 only: base64url(SHA-256(verifier)) without padding, compared in constant time."""
    if not verifier or not _PKCE_VERIFIER_RE.fullmatch(verifier):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge)


def resource_matches(presented: str | None, origin: str) -> bool:
    """Absent, or Phoenix's MCP address with or without its trailing slash."""
    if presented is None:
        return True
    return presented.rstrip("/") == resource_url(origin)
