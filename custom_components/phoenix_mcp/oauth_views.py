"""Phoenix's own OAuth authorization server, for apps that cannot send an access key.

Five unauthenticated routes: the protected-resource and authorization-server documents,
authorize, token and revoke. Home Assistant core owns the root well-known documents and
/auth/*, so Phoenix uses the path-suffixed documents and an issuer under its own path.

Every route checks, in order: readiness, the kill switch (503), credentials in the query
string, the per-address budget, and then whether sign-in is offered at all. Until an access
key switches OAuth sign-in on, and for any request that did not arrive over HTTPS, the
routes answer 404 as if they did not exist.

Authorize never decides anything: it parks the request and sends the browser to the Phoenix
panel, where an administrator approves it for one access key. Tokens are bound to that key
and presented through the same check as the key itself.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs

from aiohttp import web

from .audit import generate_request_id
from .const import (
    DOMAIN, OAUTH_CODE_LENGTH, OAUTH_CODE_PREFIX, OAUTH_FORM_MAX_BYTES, OAUTH_ISSUER_PATH,
    OAUTH_PANEL_PATH, OAUTH_QUERY_SECRETS, OAUTH_REFRESH_TOKEN_LENGTH, OAUTH_REFRESH_TOKEN_PREFIX,
    OAUTH_RESOURCE_PATH, OAUTH_STATE_MAX_LENGTH, OAUTH_ACCESS_TOKEN_PREFIX, OAUTH_ACCESS_TOKEN_LENGTH,
)
from .data import PhoenixData
from .durable_storage import DurableStorageError
from .helpers import get_client_ip
from .oauth_flow import (
    issuer_url, oauth_offered, pkce_matches, public_origin, redirect_host, resource_matches, resource_url,
    valid_client_id, valid_code_challenge, valid_redirect_uri,
)
from .oauth_grants import GrantLimitError, IssuedTokens, secret_hash
from .view_base import PhoenixView

_LOGGER = logging.getLogger(__name__)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _json(body: dict, status: int = 200, rid: str = "") -> web.Response:
    return web.Response(
        status=status, content_type="application/json", text=json.dumps(body),
        headers={**_NO_STORE, **({"X-Phoenix-Request-ID": rid} if rid else {})},
    )


def _oauth_error(error: str, status: int = 400, rid: str = "") -> web.Response:
    """An RFC 6749 error body; the code names the class of failure and nothing more."""
    return _json({"error": error}, status, rid)


def _gate(view: PhoenixView, request: web.Request, rid: str) -> tuple[PhoenixData, str] | web.Response:
    """The checks every OAuth route makes first; returns (data, public origin) or a refusal."""
    data: PhoenixData | None = view.hass.data.get(DOMAIN)
    if data is None or not data.ready or data.shutting_down:
        return _oauth_error("temporarily_unavailable", 503, rid)
    if data.store.get_settings().kill_switch:
        return _oauth_error("temporarily_unavailable", 503, rid)
    if any(name in request.query for name in OAUTH_QUERY_SECRETS):
        return _oauth_error("invalid_request", 400, rid)
    if not data.oauth.address_allowed(get_client_ip(request)):
        response = _oauth_error("slow_down", 429, rid)
        response.headers["Retry-After"] = "60"
        return response
    origin = public_origin(view.hass, request)
    if origin is None or not oauth_offered(data):
        return web.Response(status=404, headers={"X-Phoenix-Request-ID": rid})
    return data, origin


def _single_values(pairs: dict[str, list[str]]) -> dict[str, str] | None:
    """One value per parameter, or None when any repeats (RFC 6749 section 3.1)."""
    if any(len(values) != 1 for values in pairs.values()):
        return None
    return {name: values[0] for name, values in pairs.items()}


async def _read_form(request: web.Request) -> dict[str, str] | None:
    """A bounded form-encoded body, or None when it is missing, too large or malformed."""
    if request.content_type != "application/x-www-form-urlencoded":
        return None
    if request.content_length is not None and request.content_length > OAUTH_FORM_MAX_BYTES:
        return None
    raw = await request.content.read(OAUTH_FORM_MAX_BYTES + 1)
    if len(raw) > OAUTH_FORM_MAX_BYTES:
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return _single_values(parse_qs(text, keep_blank_values=True, strict_parsing=False))


def _audit(
    data: PhoenixData, request: web.Request, rid: str, access_key: Any, outcome: str,
    oauth_app: dict[str, str],
) -> None:
    """Record a token-endpoint event against the key and app it concerns; never any token."""
    data.audit.record(
        request_id=rid,
        access_key_id=access_key.id,
        access_key_name=access_key.name,
        method=request.method,
        resource="oauth:token",
        outcome=outcome,  # type: ignore[arg-type]
        client_ip=get_client_ip(request),
        settings=data.store.get_settings(),
        oauth_app=oauth_app,
    )


def _code_app(code: Any) -> dict[str, str]:
    """The app an authorization code was approved for, as an audit row names it."""
    return {"name": code.client_name, "host": redirect_host(code.redirect_uri)}


def _tokens_body(tokens: IssuedTokens) -> dict:
    return {
        "access_token": tokens.access_token,
        "token_type": "Bearer",
        "expires_in": tokens.expires_in,
        "refresh_token": tokens.refresh_token,
    }


class PhoenixOAuthProtectedResourceView(PhoenixView):
    """GET /.well-known/oauth-protected-resource/api/phoenix-mcp - RFC 9728 document."""

    url = "/.well-known/oauth-protected-resource" + OAUTH_RESOURCE_PATH
    name = "api:phoenix-mcp:oauth:protected-resource"
    requires_auth = False
    cors_allowed = True

    async def get(self, request: web.Request) -> web.Response:
        rid = generate_request_id()
        gate = _gate(self, request, rid)
        if isinstance(gate, web.Response):
            return gate
        _data, origin = gate
        return _json({
            "resource": resource_url(origin),
            "authorization_servers": [issuer_url(origin)],
            "bearer_methods_supported": ["header"],
            "resource_name": "Phoenix MCP",
        }, rid=rid)


class PhoenixOAuthServerMetadataView(PhoenixView):
    """GET the RFC 8414 document for issuer <origin>/api/phoenix-mcp/oauth.

    Served at the path-inserted RFC 8414 location and both OpenID discovery forms, the
    order MCP clients try them in.
    """

    url = "/.well-known/oauth-authorization-server" + OAUTH_ISSUER_PATH
    extra_urls = [
        "/.well-known/openid-configuration" + OAUTH_ISSUER_PATH,
        OAUTH_ISSUER_PATH + "/.well-known/openid-configuration",
    ]
    name = "api:phoenix-mcp:oauth:server-metadata"
    requires_auth = False
    cors_allowed = True

    async def get(self, request: web.Request) -> web.Response:
        rid = generate_request_id()
        gate = _gate(self, request, rid)
        if isinstance(gate, web.Response):
            return gate
        _data, origin = gate
        issuer = issuer_url(origin)
        return _json({
            "issuer": issuer,
            "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token",
            "revocation_endpoint": issuer + "/revoke",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }, rid=rid)


class PhoenixOAuthAuthorizeView(PhoenixView):
    """GET /api/phoenix-mcp/oauth/authorize - park a sign-in and hand it to the panel.

    Checks only the request's shape and never redirects to the app: an administrator
    decides in the panel, which also fetches the app's metadata document. A malformed
    request, a closed sign-in window or a full queue each land on a panel page that says so.
    """

    url = OAUTH_ISSUER_PATH + "/authorize"
    name = "api:phoenix-mcp:oauth:authorize"
    requires_auth = False

    @staticmethod
    def _to_panel(page: str) -> web.Response:
        return web.Response(status=302, headers={
            "Location": f"{OAUTH_PANEL_PATH}/{page}", "Referrer-Policy": "no-referrer", **_NO_STORE,
        })

    async def get(self, request: web.Request) -> web.Response:
        rid = generate_request_id()
        gate = _gate(self, request, rid)
        if isinstance(gate, web.Response):
            return gate
        data, origin = gate
        params = _single_values({name: request.query.getall(name) for name in request.query})
        if params is None:
            return self._to_panel("invalid")
        client_id = params.get("client_id")
        redirect_uri = params.get("redirect_uri")
        state = params.get("state")
        resource = params.get("resource")
        if (params.get("response_type") != "code"
                or params.get("code_challenge_method") != "S256"
                or not valid_code_challenge(params.get("code_challenge"))
                or not valid_client_id(client_id)
                or not valid_redirect_uri(redirect_uri)
                or (state is not None and len(state) > OAUTH_STATE_MAX_LENGTH)
                or not resource_matches(resource, origin)):
            return self._to_panel("invalid")
        if not data.oauth.any_window_open(data.store):
            return self._to_panel("closed")
        assert client_id is not None and redirect_uri is not None
        pending = data.oauth.add_pending(
            client_id=client_id, redirect_uri=redirect_uri,
            code_challenge=params["code_challenge"], state=state,
            resource=resource_url(origin), issuer=issuer_url(origin),
            address=get_client_ip(request),
        )
        if pending is None:
            return self._to_panel("busy")
        return self._to_panel(pending.id)


class PhoenixOAuthTokenView(PhoenixView):
    """POST /api/phoenix-mcp/oauth/token - exchange a code, or refresh, for tokens."""

    url = OAUTH_ISSUER_PATH + "/token"
    name = "api:phoenix-mcp:oauth:token"
    requires_auth = False
    cors_allowed = True

    async def post(self, request: web.Request) -> web.Response:
        rid = generate_request_id()
        gate = _gate(self, request, rid)
        if isinstance(gate, web.Response):
            return gate
        data, origin = gate
        form = await _read_form(request)
        if form is None:
            return _oauth_error("invalid_request", rid=rid)
        grant_type = form.get("grant_type")
        try:
            if grant_type == "authorization_code":
                return await self._exchange_code(request, data, origin, form, rid)
            if grant_type == "refresh_token":
                return await self._refresh(request, data, form, rid)
        except DurableStorageError:
            # Nothing was issued: the old code or refresh token still stands.
            return _oauth_error("temporarily_unavailable", 503, rid)
        return _oauth_error("unsupported_grant_type", rid=rid)

    async def _exchange_code(
        self, request: web.Request, data: PhoenixData, origin: str, form: dict[str, str], rid: str,
    ) -> web.Response:
        raw = form.get("code", "")
        if not raw.startswith(OAUTH_CODE_PREFIX) or len(raw) != OAUTH_CODE_LENGTH or not raw.isascii():
            return _oauth_error("invalid_grant", rid=rid)
        code, reused_grant_id = data.oauth.redeem_code(raw)
        if code is None:
            if reused_grant_id is not None:
                # A code used twice was intercepted: end the sign-in it produced.
                removed = await data.oauth_grants.async_remove(reused_grant_id)
                access_key = data.store.get_access_key_by_id(removed.access_key_id) if removed else None
                if access_key is not None and removed is not None:
                    _audit(data, request, rid, access_key, "denied", removed.audit_label())
            return _oauth_error("invalid_grant", rid=rid)
        access_key = data.store.get_access_key_by_id(code.access_key_id)
        presented_resource = form.get("resource")
        if (form.get("client_id") != code.client_id
                or form.get("redirect_uri") != code.redirect_uri
                or (presented_resource is not None and presented_resource.rstrip("/") != code.resource)
                or not resource_matches(presented_resource, origin)):
            return _oauth_error("invalid_grant", rid=rid)
        if not pkce_matches(form.get("code_verifier"), code.code_challenge):
            if access_key is not None:
                _audit(data, request, rid, access_key, "denied", _code_app(code))
            return _oauth_error("invalid_grant", rid=rid)
        if (access_key is None or not access_key.is_valid() or not access_key.oauth_enabled
                or access_key.oauth_generation != code.generation):
            return _oauth_error("invalid_grant", rid=rid)
        try:
            tokens = await data.oauth_grants.async_create(
                access_key=access_key, client_id=code.client_id, client_name=code.client_name,
                redirect_host=redirect_host(code.redirect_uri), created_by=code.created_by,
            )
        except GrantLimitError:
            return _oauth_error("access_denied", rid=rid)
        data.oauth.mark_redeemed(raw, tokens.grant_id)
        _audit(data, request, rid, access_key, "allowed", _code_app(code))
        return _json(_tokens_body(tokens), rid=rid)

    async def _refresh(self, request: web.Request, data: PhoenixData, form: dict[str, str], rid: str) -> web.Response:
        raw = form.get("refresh_token", "")
        if (not raw.startswith(OAUTH_REFRESH_TOKEN_PREFIX) or len(raw) != OAUTH_REFRESH_TOKEN_LENGTH
                or not raw.isascii()):
            return _oauth_error("invalid_grant", rid=rid)
        outcome, tokens, grant = await data.oauth_grants.async_refresh(
            secret_hash(raw), form.get("client_id"),
            lambda g: data.store.get_access_key_by_id(g.access_key_id),
        )
        if outcome == "issued" and tokens is not None:
            return _json(_tokens_body(tokens), rid=rid)
        if outcome == "reused" and grant is not None:
            access_key = data.store.get_access_key_by_id(grant.access_key_id)
            if access_key is not None:
                _audit(data, request, rid, access_key, "denied", grant.audit_label())
        return _oauth_error("invalid_grant", rid=rid)


class PhoenixOAuthRevokeView(PhoenixView):
    """POST /api/phoenix-mcp/oauth/revoke - an app ends its own sign-in (RFC 7009).

    Either of its tokens ends the whole sign-in. The answer is 200 whether or not the
    token was known, so it reveals nothing.
    """

    url = OAUTH_ISSUER_PATH + "/revoke"
    name = "api:phoenix-mcp:oauth:revoke"
    requires_auth = False
    cors_allowed = True

    async def post(self, request: web.Request) -> web.Response:
        rid = generate_request_id()
        gate = _gate(self, request, rid)
        if isinstance(gate, web.Response):
            return gate
        data, _origin = gate
        form = await _read_form(request)
        if form is None:
            return _oauth_error("invalid_request", rid=rid)
        raw = form.get("token", "")
        grant = None
        if raw.isascii() and raw.startswith(OAUTH_ACCESS_TOKEN_PREFIX) and len(raw) == OAUTH_ACCESS_TOKEN_LENGTH:
            grant = data.oauth_grants.find_by_access_hash(secret_hash(raw))
        elif raw.isascii() and raw.startswith(OAUTH_REFRESH_TOKEN_PREFIX) and len(raw) == OAUTH_REFRESH_TOKEN_LENGTH:
            grant = data.oauth_grants.grant_for_refresh(secret_hash(raw))
        client_id = form.get("client_id")
        if grant is not None and (client_id is None or client_id == grant.client_id):
            try:
                await data.oauth_grants.async_remove(grant.id)
            except DurableStorageError:
                return _oauth_error("temporarily_unavailable", 503, rid)
        return _json({}, rid=rid)


ALL_OAUTH_VIEWS = [
    PhoenixOAuthProtectedResourceView,
    PhoenixOAuthServerMetadataView,
    PhoenixOAuthAuthorizeView,
    PhoenixOAuthTokenView,
    PhoenixOAuthRevokeView,
]
