"""OAuth sign-in for access keys, end to end through Home Assistant's real HTTP router.

The app side is played by an unauthenticated client reaching Home Assistant through a
trusted proxy that terminated HTTPS for the instance's configured address, as a tunnel or
reverse proxy does; the administrator side by Home Assistant's authenticated admin client.
Only the metadata-document fetch is replaced, at the seam where the approval view calls it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import secrets
from datetime import timedelta
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.admin.access_keys import (
    PhoenixAdminAccessKeyRotateView, PhoenixAdminAccessKeyView, PhoenixAdminAccessKeysView,
)
from custom_components.phoenix_mcp.admin.approvals import PhoenixAdminApprovalApproveView, PhoenixAdminApprovalsView
from custom_components.phoenix_mcp.admin.logs import PhoenixAdminAuditView
from custom_components.phoenix_mcp.admin.oauth import (
    PhoenixAdminAccessKeyOAuthSignInView, PhoenixAdminAccessKeyOAuthView,
    PhoenixAdminAccessKeyOAuthWindowView, PhoenixAdminOAuthRequestApproveView,
    PhoenixAdminOAuthRequestDenyView, PhoenixAdminOAuthRequestView, PhoenixAdminOAuthSummaryView,
)
from custom_components.phoenix_mcp.approvals import async_create_pending_approval, credential_generation, get_approval
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp import mcp_transport
from custom_components.phoenix_mcp.const import (
    DOMAIN, OAUTH_ACCESS_TOKEN_TTL_SECONDS, OAUTH_ADDRESS_REQUESTS_PER_MINUTE, OAUTH_CODE_TTL_SECONDS,
    OAUTH_GRANTS_STORAGE_KEY, OAUTH_GRANTS_STORAGE_VERSION, OAUTH_MAX_GRANTS_PER_ACCESS_KEY,
    OAUTH_PENDING_PER_ADDRESS_MAX, OAUTH_REFRESH_GRACE_SECONDS, OAUTH_REFRESH_SIBLINGS_MAX, OAUTH_REFRESH_USES_MAX,
    OAUTH_RETIRED_TOKENS_MAX,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.durable_storage import DurableStorageError, DurableStore
from custom_components.phoenix_mcp.mcp_transport import ALL_MCP_VIEWS
from custom_components.phoenix_mcp.mesa import async_create_mesa_approval
from custom_components.phoenix_mcp.oauth_client_metadata import check_document
from custom_components.phoenix_mcp.oauth_flow import ClientVerdict, pkce_matches, redirect_matches
from custom_components.phoenix_mcp.oauth_grants import GrantLimitError, OAuthGrantStore, _HashUntil, secret_hash
from custom_components.phoenix_mcp.oauth_views import ALL_OAUTH_VIEWS
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.household_authority import HouseholdAuthority
from custom_components.phoenix_mcp.request_authority import (
    AuthorityChangedError, RequestAuthority, check_request_authority, household_authority, request_authority,
    write_boundary,
)
from custom_components.phoenix_mcp.tool_common import _create_registry_mesa_approval
from custom_components.phoenix_mcp.tools import system as system_tools

ORIGIN = "https://phx.example"
# Home Assistant's forwarded middleware turns these into the request's https scheme.
HTTPS = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}
HOST = {"Host": "phx.example:443", **HTTPS}
CLIENT_ID = "https://app.example/oauth/client.json"
REDIRECT = "https://app.example/callback"
_ADMIN_VIEWS = (
    PhoenixAdminAccessKeysView, PhoenixAdminAccessKeyView,
    PhoenixAdminOAuthRequestView, PhoenixAdminOAuthRequestApproveView, PhoenixAdminOAuthRequestDenyView,
    PhoenixAdminAccessKeyOAuthView, PhoenixAdminAccessKeyOAuthWindowView, PhoenixAdminAccessKeyOAuthSignInView,
    PhoenixAdminOAuthSummaryView, PhoenixAdminAuditView,
)


async def _behind_trusted_proxy(hass: HomeAssistant) -> None:
    """Set up Home Assistant's HTTP server behind a trusted local proxy."""
    assert await async_setup_component(
        hass, "http", {"http": {"use_x_forwarded_for": True, "trusted_proxies": ["127.0.0.1", "::1"]}})
    try:
        from homeassistant.components.http.config import async_get_and_load_store
    except ImportError:  # before 2026.11 the YAML settings apply directly
        return
    # Since 2026.11 they are a trial that reverts by restarting unless confirmed.
    await (await async_get_and_load_store(hass)).async_promote_pending()


@pytest.fixture
async def oauth(hass, hass_client, hass_client_no_auth):
    await _behind_trusted_proxy(hass)
    await hass.config.async_update(external_url=ORIGIN)
    store = await AccessKeyStore.async_create(hass)
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    # Every view before the clients start: older Home Assistant freezes the router then.
    for view_cls in (*ALL_MCP_VIEWS, *ALL_OAUTH_VIEWS, *_ADMIN_VIEWS, PhoenixAdminAccessKeyRotateView,
                     PhoenixAdminApprovalApproveView, PhoenixAdminApprovalsView):
        view = view_cls()
        view.hass = hass
        hass.http.register_view(view)
    with patch("custom_components.phoenix_mcp.admin.oauth.async_fetch_client_verdict",
               return_value=ClientVerdict(True, name="Test App")) as fetch:
        yield await hass_client_no_auth(), await hass_client(), data, fetch


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _initialize():
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
    }}


async def _key(data, name="phone", oauth=True):
    access_key, raw = await data.store.async_create_access_key(name, "admin", oauth_enabled=oauth)
    if oauth:
        data.oauth.open_window(access_key.id)
    return access_key, raw


async def _authorize(app, challenge, **overrides):
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
        "resource": ORIGIN + "/api/phoenix-mcp", **overrides,
    }
    params = {k: v for k, v in params.items() if v is not None}
    return await app.get("/api/phoenix-mcp/oauth/authorize?" + urlencode(params), headers=HOST,
                         allow_redirects=False)


async def _approve(app, admin, challenge, access_key_id) -> str:
    response = await _authorize(app, challenge)
    assert response.status == 302
    request_id = response.headers["Location"].rsplit("/", 1)[1]
    view = await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}")
    assert view.status == 200
    approved = await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve",
                                json={"access_key_id": access_key_id})
    assert approved.status == 200
    query = parse_qs(urlsplit((await approved.json())["redirect_to"]).query)
    assert query["state"] == ["xyz"] and query["iss"] == [ORIGIN + "/api/phoenix-mcp/oauth"]
    return query["code"][0]


async def _token(app, **form):
    return await app.post("/api/phoenix-mcp/oauth/token", data=form, headers=HOST)


async def _sign_in(app, admin, access_key_id) -> dict:
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key_id)
    response = await _token(app, grant_type="authorization_code", code=code, code_verifier=verifier,
                            client_id=CLIENT_ID, redirect_uri=REDIRECT, resource=ORIGIN + "/api/phoenix-mcp")
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    return await response.json()


async def _mcp(app, token):
    return await app.post("/api/phoenix-mcp", json=_initialize(),
                          headers={**HOST, "Authorization": f"Bearer {token}", "Accept": "application/json"})


# Discovery and darkness


async def test_nothing_is_advertised_until_a_key_offers_sign_in(oauth):
    app, _admin, data, _fetch = oauth
    _key_record, raw = await _key(data, oauth=False)
    for path in ("/.well-known/oauth-protected-resource/api/phoenix-mcp",
                 "/.well-known/oauth-authorization-server/api/phoenix-mcp/oauth"):
        assert (await app.get(path, headers=HOST)).status == 404
    response = await _mcp(app, "phx_" + "0" * 64)
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="Phoenix MCP"'


async def test_metadata_describes_phoenix_own_server_over_https_only(oauth):
    app, _admin, data, _fetch = oauth
    await _key(data)
    resource = await (await app.get("/.well-known/oauth-protected-resource/api/phoenix-mcp", headers=HOST)).json()
    assert resource["resource"] == ORIGIN + "/api/phoenix-mcp"
    assert resource["authorization_servers"] == [ORIGIN + "/api/phoenix-mcp/oauth"]
    for path in ("/.well-known/oauth-authorization-server/api/phoenix-mcp/oauth",
                 "/.well-known/openid-configuration/api/phoenix-mcp/oauth",
                 "/api/phoenix-mcp/oauth/.well-known/openid-configuration"):
        server = await (await app.get(path, headers=HOST)).json()
        assert server["issuer"] == ORIGIN + "/api/phoenix-mcp/oauth"
        assert server["code_challenge_methods_supported"] == ["S256"]
        assert server["token_endpoint_auth_methods_supported"] == ["none"]
        assert server["client_id_metadata_document_supported"] is True
        assert "registration_endpoint" not in server
    # A request that did not reach Home Assistant through its HTTPS address sees nothing.
    assert (await app.get("/.well-known/oauth-protected-resource/api/phoenix-mcp")).status == 404


class _CorePathMetadataView(HomeAssistantView):
    """Stand-in for Core's per-path RFC 9728 route (core PR #192728).

    Core registers it before any integration and answers 404 for a path whose view does not
    require Home Assistant authentication, which Phoenix's MCP view does not.
    """

    url = "/.well-known/oauth-protected-resource/{resource_path:.+}"
    name = "test:core:oauth-protected-resource:path"
    requires_auth = False

    async def get(self, request, resource_path: str):
        return self.json_message("core catch-all", HTTPStatus.NOT_FOUND)


async def test_phoenix_metadata_outranks_a_core_catch_all_registered_first(hass, hass_client_no_auth):
    await _behind_trusted_proxy(hass)
    await hass.config.async_update(external_url=ORIGIN)
    data = PhoenixData(store=await AccessKeyStore.async_create(hass), rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    hass.http.register_view(_CorePathMetadataView())
    for view_cls in (*ALL_MCP_VIEWS, *ALL_OAUTH_VIEWS):
        view = view_cls()
        view.hass = hass
        hass.http.register_view(view)
    app = await hass_client_no_auth()
    await _key(data)
    # Phoenix's exact path wins whatever the registration order, so OAuth clients still find it.
    response = await app.get("/.well-known/oauth-protected-resource/api/phoenix-mcp", headers=HOST)
    assert response.status == 200
    assert (await response.json())["resource"] == ORIGIN + "/api/phoenix-mcp"
    # The catch-all is live for every other path.
    other = await app.get("/.well-known/oauth-protected-resource/api/other", headers=HOST)
    assert (other.status, (await other.json())["message"]) == (404, "core catch-all")
    challenge = (await _mcp(app, "phx_" + "0" * 64)).headers["WWW-Authenticate"]
    assert 'resource_metadata="https://phx.example/.well-known/oauth-protected-resource/api/phoenix-mcp"' in challenge


async def test_kill_switch_refuses_every_step_and_drops_flows_in_progress(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    _verifier, challenge = _pkce()
    request_id = (await _authorize(app, challenge)).headers["Location"].rsplit("/", 1)[1]
    await data.store.async_patch_settings(kill_switch=True)
    data.oauth.clear()  # what the settings PATCH does when the kill switch turns on
    for response in (
        await app.get("/.well-known/oauth-protected-resource/api/phoenix-mcp", headers=HOST),
        await _authorize(app, challenge),
        await _token(app, grant_type="refresh_token", refresh_token="phxr_" + "0" * 64),
    ):
        assert response.status == 503
    assert (await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}")).status == 503
    assert (await admin.post(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/oauth/window")).status == 503
    await data.store.async_patch_settings(kill_switch=False)
    assert (await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}")).status == 404


# The full sign-in


async def test_sign_in_presents_the_bound_key_and_refreshes(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    assert tokens["token_type"] == "Bearer" and tokens["access_token"].startswith("phxo_")
    assert (await _mcp(app, tokens["access_token"])).status == 200
    assert data.oauth.window_until(access_key.id) is None  # approval closed the window

    refreshed = await (await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"],
                                    client_id=CLIENT_ID)).json()
    assert refreshed["refresh_token"] != tokens["refresh_token"]
    assert (await _mcp(app, refreshed["access_token"])).status == 200
    # A retried refresh inside the grace adds a sibling instead of ending the sign-in.
    retried = await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
    assert retried.status == 200
    assert (await _mcp(app, (await retried.json())["access_token"])).status == 200

    status = await (await admin.get(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/oauth")).json()
    assert [s["client_name"] for s in status["sign_ins"]] == ["Test App"]
    assert "hash" not in str(status)


async def test_stale_refresh_after_grace_ends_the_sign_in(oauth, freezer: FrozenDateTimeFactory):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    refreshed = await (await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"])).json()
    freezer.tick(timedelta(seconds=OAUTH_REFRESH_GRACE_SECONDS + 1))
    reused = await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
    assert reused.status == 400 and (await reused.json())["error"] == "invalid_grant"
    assert (await _mcp(app, refreshed["access_token"])).status == 401
    assert data.oauth_grants.list_for_key(access_key.id) == []


# The backlog's refusals


async def test_wrong_pkce_verifier_is_refused(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    _verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    response = await _token(app, grant_type="authorization_code", code=code, code_verifier=_pkce()[0],
                            client_id=CLIENT_ID, redirect_uri=REDIRECT)
    assert (await response.json())["error"] == "invalid_grant"
    assert data.oauth_grants.list_for_key(access_key.id) == []


async def test_reused_code_ends_the_sign_in_it_produced(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_id=CLIENT_ID, redirect_uri=REDIRECT)
    first = await (await _token(app, **form)).json()
    second = await _token(app, **form)
    assert (await second.json())["error"] == "invalid_grant"
    assert (await _mcp(app, first["access_token"])).status == 401


@pytest.mark.parametrize("field,value", [("client_id", "https://other.example/client.json"),
                                         ("redirect_uri", "https://app.example/elsewhere")])
async def test_code_for_another_client_or_redirect_is_refused(oauth, field, value):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_id=CLIENT_ID, redirect_uri=REDIRECT)
    form[field] = value
    assert (await (await _token(app, **form)).json())["error"] == "invalid_grant"


async def test_unverified_client_cannot_be_approved_and_deny_does_not_redirect(oauth):
    app, admin, data, fetch = oauth
    access_key, _raw = await _key(data)
    fetch.return_value = ClientVerdict(False, problem="oauthRedirectNotListed")
    request_id = (await _authorize(app, _pkce()[1])).headers["Location"].rsplit("/", 1)[1]
    detail = await (await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}")).json()
    assert detail["verified"] is False and detail["problem"] == "oauthRedirectNotListed"
    approve = await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve",
                               json={"access_key_id": access_key.id})
    assert approve.status == 400
    deny = await (await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/deny")).json()
    assert deny["redirect_to"] is None


@pytest.mark.parametrize("event", ["revoke", "rotate", "switch_off", "remove"])
async def test_ending_the_key_or_sign_in_ends_its_tokens(oauth, event):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    await _key(data, "other-key")  # keeps sign-in offered, so refusals are not just darkness
    tokens = await _sign_in(app, admin, access_key.id)
    if event == "revoke":
        await data.store.async_archive_access_key(access_key.id, revoked=True)
    elif event == "rotate":
        await data.store.async_rotate_access_key(access_key.id)
    elif event == "switch_off":
        await data.store.async_patch_access_key(access_key.id, oauth_enabled=False)
    else:
        sign_in = data.oauth_grants.list_for_key(access_key.id)[0]
        removed = await admin.delete(
            f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/oauth/sign-ins/{sign_in.id}")
        assert removed.status == 204
    response = await _mcp(app, tokens["access_token"])
    assert response.status == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]
    refresh = await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
    assert (await refresh.json())["error"] == "invalid_grant"


async def test_last_key_switching_off_takes_every_route_dark(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    await data.store.async_patch_access_key(access_key.id, oauth_enabled=False)
    refresh = await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
    assert refresh.status == 404
    response = await _mcp(app, tokens["access_token"])
    assert response.status == 401 and response.headers["WWW-Authenticate"] == 'Bearer realm="Phoenix MCP"'


async def test_switching_oauth_back_on_does_not_revive_old_sign_ins(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    await data.store.async_patch_access_key(access_key.id, oauth_enabled=False)
    await data.store.async_patch_access_key(access_key.id, oauth_enabled=True)
    assert (await _mcp(app, tokens["access_token"])).status == 401


async def test_removing_a_sign_in_ends_a_request_already_admitted(hass, oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    await _sign_in(app, admin, access_key.id)
    sign_in = data.oauth_grants.list_for_key(access_key.id)[0]
    authority = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash, sign_in.id)
    authority.current_access_key()
    await data.oauth_grants.async_remove(sign_in.id)
    with pytest.raises(AuthorityChangedError):
        authority.current_access_key()


async def test_home_assistant_token_and_other_shapes_are_not_phoenix_credentials(oauth, hass_access_token):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    assert (await _mcp(app, hass_access_token)).status == 401
    assert (await _mcp(app, tokens["refresh_token"])).status == 401
    leaked = await app.post("/api/phoenix-mcp?access_token=" + tokens["access_token"], json=_initialize(),
                            headers={**HOST, "Accept": "application/json"})
    assert leaked.status == 401
    in_query = await app.post("/api/phoenix-mcp/oauth/token?refresh_token=" + tokens["refresh_token"],
                              data={"grant_type": "refresh_token"}, headers=HOST)
    assert in_query.status == 400


async def test_authorize_checks_shape_and_window_without_redirecting_to_the_app(oauth):
    app, _admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    _verifier, challenge = _pkce()
    for overrides in ({"code_challenge_method": "plain"}, {"code_challenge": None},
                      {"client_id": "http://app.example/client.json"}, {"redirect_uri": "http://app.example/cb"},
                      {"resource": "https://elsewhere.example/api/phoenix-mcp"}, {"response_type": "token"}):
        response = await _authorize(app, challenge, **overrides)
        assert response.headers["Location"] == "/phoenix-mcp/oauth/invalid", overrides
    data.oauth.close_window(access_key.id)
    closed = (await _authorize(app, challenge)).headers["Location"]
    # Shaped like a parked request, so a caller cannot tell whether a window is open.
    assert closed.startswith("/phoenix-mcp/oauth/") and len(closed.rsplit("/", 1)[1]) == 43
    assert not data.oauth.pending


async def test_approval_needs_an_open_window_on_the_chosen_key(oauth):
    app, admin, data, _fetch = oauth
    open_key, _ = await _key(data, "open-key")
    closed_key, _ = await _key(data, "closed-key")
    data.oauth.close_window(closed_key.id)
    request_id = (await _authorize(app, _pkce()[1])).headers["Location"].rsplit("/", 1)[1]
    detail = await (await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}")).json()
    assert [k["id"] for k in detail["keys"]] == [open_key.id]
    refused = await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve",
                               json={"access_key_id": closed_key.id})
    assert refused.status == 400
    approved = await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve",
                                json={"access_key_id": open_key.id})
    assert approved.status == 200
    again = await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve",
                             json={"access_key_id": open_key.id})
    assert again.status == 404


# Pieces


def test_pkce_is_s256_only():
    verifier, challenge = _pkce()
    assert pkce_matches(verifier, challenge)
    assert not pkce_matches(verifier, verifier[:43])
    assert not pkce_matches("short", challenge)


def test_loopback_redirects_may_change_port_and_nothing_else():
    assert redirect_matches("http://127.0.0.1:53111/callback", "http://127.0.0.1/callback")
    assert redirect_matches("http://localhost:9/callback", "http://localhost/callback")
    assert not redirect_matches("http://127.0.0.1:53111/other", "http://127.0.0.1/callback")
    assert not redirect_matches("https://app.example:8443/callback", "https://app.example/callback")


@pytest.mark.parametrize("document,problem", [
    ([], "oauthClientDocumentInvalid"),
    ({"client_id": "https://evil.example/c.json", "redirect_uris": [REDIRECT]}, "oauthClientIdMismatch"),
    ({"client_id": CLIENT_ID, "redirect_uris": ["https://app.example/other"]}, "oauthRedirectNotListed"),
])
def test_metadata_document_must_name_itself_and_list_the_redirect(document, problem):
    assert check_document(document, CLIENT_ID, REDIRECT).problem == problem


def test_metadata_document_name_is_cleaned():
    verdict = check_document({"client_id": CLIENT_ID, "redirect_uris": [REDIRECT],
                              "client_name": "Good‮App\u0000 " + "x" * 200}, CLIENT_ID, REDIRECT)
    assert verdict.ok and "‮" not in verdict.name and len(verdict.name) <= 80


async def test_metadata_fetch_refuses_private_addresses():
    from custom_components.phoenix_mcp.oauth_client_metadata import async_fetch_client_verdict
    for client_id in ("https://127.0.0.1/c.json", "https://10.0.0.5/c.json", "https://[::1]/c.json"):
        verdict = await async_fetch_client_verdict(client_id, REDIRECT)
        assert verdict.problem == "oauthClientAddressRefused", client_id


def test_every_client_problem_has_panel_text():
    """The approval dialog looks up oauth.problem.<key> for whatever the fetch reports."""
    import json
    import pathlib
    import re

    from custom_components.phoenix_mcp import oauth_client_metadata

    source = pathlib.Path(oauth_client_metadata.__file__).read_text()
    problems = set(re.findall(r'problem="(\w+)"', source))
    catalog = json.loads((pathlib.Path(oauth_client_metadata.__file__).parent / "catalogs" / "en.json").read_text())
    assert problems and problems == set(catalog["panel"]["oauth"]["problem"])


async def test_summary_counts_live_sign_ins_and_open_windows(oauth):
    app, admin, data, _fetch = oauth
    signed, _ = await _key(data, "signed")
    plain, _ = await data.store.async_create_access_key("plain", "admin")
    await _sign_in(app, admin, signed.id)
    summary = await (await admin.get("/api/phoenix-mcp/admin/oauth/summary")).json()
    assert summary["keys"] == {signed.id: {"sign_ins": 1, "window_open_until": None}}
    assert plain.id not in summary["keys"] and summary["kill_switch"] is False


async def test_sign_in_cannot_be_turned_on_without_an_https_address(hass, oauth):
    _app, admin, data, _fetch = oauth
    await hass.config.async_update(external_url=None)
    created = await admin.post("/api/phoenix-mcp/admin/access-keys", json={"name": "no-https", "oauth_enabled": True})
    assert created.status == 400 and (await created.json())["message_key"] == "adminError.oauthNoHttps"
    plain = await admin.post("/api/phoenix-mcp/admin/access-keys", json={"name": "plain-key"})
    key_id = (await plain.json())["id"]
    patched = await admin.patch(f"/api/phoenix-mcp/admin/access-keys/{key_id}", json={"oauth_enabled": True})
    assert patched.status == 400 and (await patched.json())["message_key"] == "adminError.oauthNoHttps"
    assert data.store.get_access_key_by_id(key_id).oauth_enabled is False
    status = await (await admin.get(f"/api/phoenix-mcp/admin/access-keys/{key_id}/oauth")).json()
    assert status["public_url"] is None


async def test_no_window_opens_without_an_https_address(hass, oauth):
    _app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    await hass.config.async_update(external_url=None)
    response = await admin.post(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/oauth/window")
    assert response.status == 400 and (await response.json())["message_key"] == "adminError.oauthNoHttps"


async def test_stopping_a_window_refuses_the_approval_and_works_under_the_kill_switch(oauth):
    """Stop Sign-In closes the window at once; a request already waiting can no longer be approved for that key."""
    app, admin, data, _fetch = oauth
    access_key, _ = await _key(data, "stopped-key")
    await _key(data, "other-key")
    request_id = (await _authorize(app, _pkce()[1])).headers["Location"].rsplit("/", 1)[1]
    stopped = await admin.delete(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/oauth/window")
    assert stopped.status == 200 and (await stopped.json())["window_open_until"] is None
    assert data.oauth.window_until(access_key.id) is None
    refused = await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve",
                               json={"access_key_id": access_key.id})
    assert refused.status == 400
    data.oauth.open_window(access_key.id)
    await data.store.async_patch_settings(kill_switch=True)
    data.oauth.open_window(access_key.id)
    assert (await admin.delete(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/oauth/window")).status == 200
    assert data.oauth.window_until(access_key.id) is None
    await data.store.async_patch_settings(kill_switch=False)
    missing = await admin.delete("/api/phoenix-mcp/admin/access-keys/no-such-key/oauth/window")
    assert missing.status == 404


# Audit label


APP_LABEL = {"name": "Test App", "host": "app.example"}


async def test_audit_rows_name_the_signed_in_app_and_key_rows_do_not(oauth):
    app, admin, data, _fetch = oauth
    access_key, raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    assert (await _mcp(app, tokens["access_token"])).status == 200
    signed_in = [e for e in data.audit.query(access_key_id=access_key.id) or [] if e.oauth_app]
    assert signed_in and all(e.oauth_app == APP_LABEL for e in signed_in)

    before = {e.request_id for e in data.audit.query(access_key_id=access_key.id) or []}
    assert (await _mcp(app, raw)).status == 200
    by_key = [e for e in data.audit.query(access_key_id=access_key.id) or [] if e.request_id not in before]
    assert by_key and all(e.oauth_app is None for e in by_key)

    listed = await (await admin.get("/api/phoenix-mcp/admin/audit")).json()
    rows = listed.get("data", listed)["entries"]
    assert any(row.get("oauth_app") == APP_LABEL for row in rows)
    assert any("oauth_app" not in row for row in rows if row["access_key_id"] == access_key.id)


async def test_a_rate_limited_sign_in_is_labeled_too(oauth):
    from types import SimpleNamespace

    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    with patch.object(data.rate_limiter, "check", return_value=SimpleNamespace(allowed=False, retry_after=1)):
        assert (await _mcp(app, tokens["access_token"])).status == 429
    limited = [e for e in data.audit.query(access_key_id=access_key.id, outcome="rate_limited") or []]
    assert limited and limited[0].oauth_app == APP_LABEL


async def test_the_label_outlives_the_sign_in_and_survives_storage(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    assert (await _mcp(app, tokens["access_token"])).status == 200
    for grant in data.oauth_grants.list_for_key(access_key.id):
        await data.oauth_grants.async_remove(grant.id)
    labeled = [e for e in data.audit.query(access_key_id=access_key.id) or [] if e.oauth_app]
    assert labeled
    reloaded, _ = AuditLog()._parse_rows([e.to_dict() for e in labeled])
    assert [e.oauth_app for e in reloaded] == [APP_LABEL] * len(labeled)


@pytest.mark.parametrize("stored", [
    "Test App", {"name": "Test App"}, {"name": 3, "host": "app.example"}, {"name": "x", "host": ""},
])
def test_a_malformed_stored_label_is_dropped(stored):
    row = {"request_id": "r", "timestamp": "2026-11-11T00:00:00+00:00", "access_key_id": "k",
           "access_key_name": "K", "method": "m", "resource": "x", "outcome": "allowed", "client_ip": "1.2.3.4",
           "oauth_app": stored}
    entries, _ = AuditLog()._parse_rows([row])
    assert len(entries) == 1 and entries[0].oauth_app is None


def test_only_the_signed_in_keys_rows_are_labeled_in_its_request(hass):
    from types import SimpleNamespace
    from custom_components.phoenix_mcp.access_key_store import GlobalSettings
    from custom_components.phoenix_mcp.request_authority import request_authority

    grant = SimpleNamespace(audit_label=lambda: dict(APP_LABEL))
    data = SimpleNamespace(oauth_grants=SimpleNamespace(get=lambda grant_id: grant if grant_id == "g" else None))
    audit = AuditLog()
    token = request_authority.set(RequestAuthority(hass, data, "k", "h", "g"))  # type: ignore[arg-type]
    try:
        for key_id in ("k", "other"):
            audit.record(request_id="r", access_key_id=key_id, access_key_name=key_id, method="m",
                         resource="x", outcome="allowed", client_ip="1.2.3.4", settings=GlobalSettings())
    finally:
        request_authority.reset(token)
    assert [(e.access_key_id, e.oauth_app) for e in audit.query() or []] == [("other", None), ("k", APP_LABEL)]


def _token_rows(data, access_key_id):
    return [e for e in data.audit.query(access_key_id=access_key_id) or [] if e.resource == "oauth:token"]


async def test_every_token_endpoint_row_names_the_app(oauth, freezer: FrozenDateTimeFactory):
    app, admin, data, _fetch = oauth
    # A wrong verifier, before any sign-in exists.
    access_key, _raw = await _key(data)
    _verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    await _token(app, grant_type="authorization_code", code=code, code_verifier=_pkce()[0],
                 client_id=CLIENT_ID, redirect_uri=REDIRECT)
    # The exchange, then the same code again, which ends the sign-in.
    data.oauth.open_window(access_key.id)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_id=CLIENT_ID, redirect_uri=REDIRECT)
    await _token(app, **form)
    await _token(app, **form)
    # A refresh token used again after its grace.
    data.oauth.open_window(access_key.id)
    tokens = await _sign_in(app, admin, access_key.id)
    await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
    freezer.tick(timedelta(seconds=OAUTH_REFRESH_GRACE_SECONDS + 1))
    await _token(app, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
    rows = _token_rows(data, access_key.id)
    assert sorted(e.outcome for e in rows) == ["allowed", "allowed", "denied", "denied", "denied"]
    assert all(e.oauth_app == APP_LABEL for e in rows)


# Approvals queued through a sign-in


_EXECUTOR = "custom_components.phoenix_mcp.admin.approvals.async_execute_approved_tool"
_OK = ({"content": [{"type": "text", "text": "ok"}]}, "allowed", "restart_ha")


def _sse_data(text: str) -> dict:
    lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return json.loads(lines[-1]) if lines else json.loads(text)


async def _call_restart(app, token, accept: str = "application/json, text/event-stream") -> dict:
    """tools/call restart_ha through the real MCP route, as the signed-in app."""
    headers = {**HOST, "Authorization": f"Bearer {token}", "Accept": accept}
    init = await app.post("/api/phoenix-mcp", headers=headers, json=_initialize())
    assert init.status == 200, await init.text()
    headers["MCP-Protocol-Version"] = "2025-03-26"
    if session := init.headers.get("Mcp-Session-Id"):
        headers["Mcp-Session-Id"] = session
    await app.post("/api/phoenix-mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    call = await app.post("/api/phoenix-mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "restart_ha", "arguments": {}}})
    return _sse_data(await call.text())


@pytest.fixture
async def queued(hass, oauth):
    """A key with restart on Confirm, signed in once."""
    app, admin, data, _fetch = oauth
    data.store.get_settings().mesa_mode = "off"
    access_key, _raw = await _key(data, "household-claude")
    await _key(data, "other-key")  # keeps sign-in offered after a switch-off
    await data.store.async_patch_access_key(access_key.id, cap_restart="confirm", rate_limit_requests=0)
    tokens = await _sign_in(app, admin, access_key.id)
    sign_in = data.oauth_grants.list_for_key(access_key.id)[0]
    return app, admin, data, access_key, tokens, sign_in


def _pending(data) -> list[dict]:
    return [row for row in data.store.get_pending_approvals() if row["status"] == "pending"]


async def _end(admin, access_key, sign_in, event) -> None:
    if event == "remove":
        response = await admin.delete(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/oauth/sign-ins/{sign_in.id}")
    elif event == "switch_off":
        response = await admin.patch(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}", json={"oauth_enabled": False})
    elif event == "rotate":
        response = await admin.post(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}/rotate", json={})
    else:
        response = await admin.delete(f"/api/phoenix-mcp/admin/access-keys/{access_key.id}")
    assert response.status in (200, 204), await response.text()


async def test_an_approval_records_the_sign_in_that_queued_it(queued):
    app, admin, data, access_key, tokens, sign_in = queued
    await _call_restart(app, tokens["access_token"])
    (stored,) = _pending(data)
    assert stored["sign_in_id"] == sign_in.id and stored["oauth_app"] == APP_LABEL
    assert stored["credential_generation"] == credential_generation(access_key)
    listed = (await (await admin.get("/api/phoenix-mcp/admin/approvals")).json())["approvals"][0]
    # The panel sees the app, never the sign-in id or the credential fingerprint.
    assert listed["oauth_app"] == APP_LABEL
    assert "sign_in_id" not in listed and "credential_generation" not in listed


async def test_a_mesa_confirm_records_the_sign_in_and_the_secret_that_queued_it(hass, queued):
    """The MESA confirm path binds its approval to the credential, as the capability path does."""
    _app, _admin, data, access_key, _tokens, sign_in = queued
    token = request_authority.set(
        RequestAuthority(hass, data, access_key.id, access_key.access_key_hash, sign_in.id))
    try:
        approval = await async_create_mesa_approval(
            hass, data, access_key, args={"domain": "light", "service": "turn_on"}, diff={},
            request_id="mesa", client_ip=None)
    finally:
        request_authority.reset(token)
    stored = next(row for row in data.store.get_pending_approvals() if row["id"] == approval.id)
    assert stored["sign_in_id"] == sign_in.id and stored["oauth_app"] == APP_LABEL
    assert stored["credential_generation"] == credential_generation(access_key)


async def test_a_registry_mesa_confirm_records_the_sign_in_and_the_secret_that_queued_it(hass, queued):
    """A registry write held only by MESA binds its approval to the credential too."""
    _app, admin, data, access_key, _tokens, sign_in = queued
    token = request_authority.set(
        RequestAuthority(hass, data, access_key.id, access_key.access_key_hash, sign_in.id))
    try:
        _content, outcome, _resource = await _create_registry_mesa_approval(
            hass, data, access_key, tool_name="set_entity", args={"entity_id": "light.kitchen"}, diff={},
            request_id="registry-mesa", client_ip=None)
    finally:
        request_authority.reset(token)
    assert outcome == "pending_approval"
    (stored,) = _pending(data)
    assert stored["sign_in_id"] == sign_in.id and stored["oauth_app"] == APP_LABEL
    assert stored["credential_generation"] == credential_generation(access_key)
    await data.store.async_rotate_access_key(access_key.id)
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor:
        approved = await admin.post(f"/api/phoenix-mcp/admin/approvals/{stored['id']}/approve")
    assert (await approved.json())["message_key"] == "adminError.approvalCredentialEnded"
    executor.assert_not_awaited()


async def test_rotation_keeps_household_requests_the_secret_never_made(queued):
    """Assist, Voice and AI Task requests come from Home Assistant users, so a new secret leaves them."""
    _app, admin, data, access_key, _tokens, _sign_in_record = queued
    household = household_authority.set(HouseholdAuthority(user_id=None, user=None))
    try:
        spoken = await async_create_pending_approval(
            data.store, access_key_id=access_key.id, access_key_name=access_key.name, tool_name="restart_ha",
            cap_name="cap_restart", args={}, diff={}, request_id="assist", access_key=access_key)
    finally:
        household_authority.reset(household)
    keyed = await async_create_pending_approval(
        data.store, access_key_id=access_key.id, access_key_name=access_key.name, tool_name="restart_ha",
        cap_name="cap_restart", args={}, diff={}, request_id="raw", access_key=access_key)
    await _end(admin, access_key, None, "rotate")
    assert get_approval(data.store, keyed.id).closure_reason == "access_key_rotated"
    assert get_approval(data.store, spoken.id).status == "pending"
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor:
        approved = await admin.post(f"/api/phoenix-mcp/admin/approvals/{spoken.id}/approve")
    assert approved.status == 200, await approved.text()
    executor.assert_awaited_once()


@pytest.mark.parametrize(("event", "reason"), [
    ("remove", "oauth_sign_in_removed"), ("switch_off", "oauth_turned_off"),
    ("rotate", "access_key_rotated"), ("revoke", "access_key_revoked"),
])
async def test_ending_the_sign_in_cancels_what_it_queued(queued, event, reason):
    app, admin, data, access_key, tokens, sign_in = queued
    await _call_restart(app, tokens["access_token"])
    (stored,) = _pending(data)
    queued_row = next(e for e in data.audit.query(access_key_id=access_key.id) or [] if e.outcome == "pending_approval")
    await _end(admin, access_key, sign_in, event)
    closed = get_approval(data.store, stored["id"])
    assert (closed.status, closed.closure_reason) == ("cancelled", reason)
    rows = [e for e in data.audit.query(access_key_id=access_key.id) or [] if e.method == "approval/cancelled"]
    assert len(rows) == 1
    assert rows[0].request_id == queued_row.request_id and rows[0].oauth_app == APP_LABEL
    assert json.loads(rows[0].payload) == {"approval_id": stored["id"], "reason": reason}
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor:
        approved = await admin.post(f"/api/phoenix-mcp/admin/approvals/{stored['id']}/approve")
    assert (await approved.json())["status"] == "cancelled"
    executor.assert_not_awaited()


async def test_rotation_cancels_a_raw_key_request_and_oauth_off_keeps_it(queued):
    _app, admin, data, access_key, _tokens, _sign_in_record = queued
    raw_request = await async_create_pending_approval(
        data.store, access_key_id=access_key.id, access_key_name=access_key.name, tool_name="restart_ha",
        cap_name="cap_restart", args={}, diff={}, request_id="raw")
    await _end(admin, access_key, None, "switch_off")
    assert get_approval(data.store, raw_request.id).status == "pending"
    await _end(admin, access_key, None, "rotate")
    closed = get_approval(data.store, raw_request.id)
    assert (closed.status, closed.closure_reason) == ("cancelled", "access_key_rotated")


@pytest.mark.parametrize(("end", "reason"), [
    ("sign_in", "oauth_sign_in_ended"), ("secret", "access_key_rotated"),
])
async def test_approve_rechecks_the_credential_that_queued_the_request(queued, end, reason):
    """An ending that bypassed the cancellation (a failed cancel, say) is caught at approve."""
    app, admin, data, access_key, tokens, sign_in = queued
    await _call_restart(app, tokens["access_token"])
    (stored,) = _pending(data)
    if end == "sign_in":
        await data.oauth_grants.async_remove(sign_in.id)
    else:
        await data.store.async_rotate_access_key(access_key.id)
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor:
        approved = await admin.post(f"/api/phoenix-mcp/admin/approvals/{stored['id']}/approve")
    assert approved.status == 404
    assert (await approved.json())["message_key"] == "adminError.approvalCredentialEnded"
    executor.assert_not_awaited()
    closed = get_approval(data.store, stored["id"])
    assert (closed.status, closed.closure_reason) == ("cancelled", reason)


async def test_the_approved_run_carries_the_sign_in(queued):
    app, admin, data, access_key, tokens, sign_in = queued
    await _call_restart(app, tokens["access_token"])
    (stored,) = _pending(data)
    seen: dict = {}

    async def execute(tool_name, args, key, hass_, data_):
        seen["sign_in"] = request_authority.get().oauth_grant_id
        await data.oauth_grants.async_remove(sign_in.id)
        try:
            check_request_authority()
        except AuthorityChangedError:
            seen["stopped"] = True
            raise
        return _OK

    with patch(_EXECUTOR, execute):
        await admin.post(f"/api/phoenix-mcp/admin/approvals/{stored['id']}/approve")
    assert seen == {"sign_in": sign_in.id, "stopped": True}


async def test_executed_rows_join_the_queuing_request_and_name_the_app(queued):
    app, admin, data, access_key, tokens, sign_in = queued
    await _call_restart(app, tokens["access_token"])
    (stored,) = _pending(data)
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)):
        approved = await admin.post(f"/api/phoenix-mcp/admin/approvals/{stored['id']}/approve")
    assert approved.status == 200
    (row,) = [e for e in data.audit.query(access_key_id=access_key.id) or [] if e.method == "approval/executed"]
    assert row.request_id == stored["request_id"] and row.oauth_app == APP_LABEL
    assert json.loads(row.payload)["approval_id"] == stored["id"]


async def test_a_record_saved_before_sign_ins_were_recorded_still_approves(queued):
    """Older records have no sign-in or generation, so they count as the key's own."""
    _app, admin, data, access_key, _tokens, _sign_in_record = queued
    record = await async_create_pending_approval(
        data.store, access_key_id=access_key.id, access_key_name=access_key.name, tool_name="restart_ha",
        cap_name="cap_restart", args={}, diff={}, request_id="old")
    stored = next(row for row in data.store.get_pending_approvals() if row["id"] == record.id)
    for name in ("sign_in_id", "oauth_app", "credential_generation"):
        stored.pop(name)
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor:
        approved = await admin.post(f"/api/phoenix-mcp/admin/approvals/{record.id}/approve")
    assert approved.status == 200
    executor.assert_awaited_once()


async def test_removing_the_sign_in_during_an_inline_wait_cancels_the_action(hass, queued):
    app, admin, data, access_key, tokens, sign_in = queued
    await data.store.async_patch_access_key(access_key.id, confirm_inline_wait_seconds=30)
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor:
        call = hass.async_create_task(_call_restart(app, tokens["access_token"]))
        async with asyncio.timeout(10):
            while not _pending(data):
                await asyncio.sleep(0.01)
        (stored,) = _pending(data)
        await _end(admin, access_key, sign_in, "remove")
        async with asyncio.timeout(10):
            answer = await call
        approved = await admin.post(f"/api/phoenix-mcp/admin/approvals/{stored['id']}/approve")
    executor.assert_not_awaited()
    assert (await approved.json())["status"] == "cancelled"
    assert get_approval(data.store, stored["id"]).closure_reason == "oauth_sign_in_removed"
    assert "applied" not in json.dumps(answer)


# The access token's hour ends a request at admission and before dispatch, never during one.

_MODERN = "2026-07-28"


def _past_the_hour():
    """Move only the authority clock past the token's hour, so the client's own timeouts are unaffected."""
    later = utcnow() + timedelta(seconds=OAUTH_ACCESS_TOKEN_TTL_SECONDS + 1)
    return patch("custom_components.phoenix_mcp.request_authority.utcnow", return_value=later)


async def test_a_write_running_when_the_token_hour_ends_finishes_and_answers(queued):
    """On the modern stream the keepalive tick no longer cancels the write or holds the key."""
    app, _admin, data, access_key, tokens, _sign_in_record = queued
    await data.store.async_patch_access_key(access_key.id, cap_restart="allow")
    clock = _past_the_hour()

    @write_boundary
    async def slow_restart(args, access_key, hass, data):
        clock.start()
        await asyncio.sleep(1)
        return {"content": [{"type": "text", "text": "restarted"}]}, "allowed", "restart_ha"

    headers = {**HOST, "Authorization": f"Bearer {tokens['access_token']}",
               "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": _MODERN, "Mcp-Method": "tools/call", "Mcp-Name": "restart_ha"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "restart_ha", "arguments": {}, "_meta": {
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "client", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {}}}}
    try:
        with patch.object(system_tools, "_execute_restart_ha", slow_restart), \
                patch.object(mcp_transport, "MCP_SSE_KEEPALIVE_SECONDS", 0.2):
            response = await app.post("/api/phoenix-mcp", headers=headers, json=body)
            answer = _sse_data(await response.text())
    finally:
        clock.stop()
    assert answer["result"]["content"][0]["text"] == "restarted"
    assert not data.agent_recovery.blocks(access_key.id, "next")
    rows = [(e.method, e.outcome) for e in data.audit.query(access_key_id=access_key.id) or []]
    assert ("restart_ha", "allowed") in rows and not any(method == "request/cancelled" for method, _ in rows)


@pytest.mark.parametrize("accept", ["application/json, text/event-stream", "application/json"])
async def test_the_token_hour_ending_during_an_inline_wait_still_answers_pending(hass, queued, accept):
    """The client keeps the approval id; nothing claims the action ended or may have run."""
    app, _admin, data, access_key, tokens, _sign_in_record = queued
    await data.store.async_patch_access_key(access_key.id, confirm_inline_wait_seconds=30)
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor, \
            patch("custom_components.phoenix_mcp.tool_common.MAX_CONFIRM_INLINE_WAIT_SECONDS", 1), \
            patch.object(mcp_transport, "MCP_SSE_KEEPALIVE_SECONDS", 0.2):
        call = hass.async_create_task(_call_restart(app, tokens["access_token"], accept=accept))
        async with asyncio.timeout(10):
            while not _pending(data):
                await asyncio.sleep(0.01)
        (stored,) = _pending(data)
        with _past_the_hour():
            async with asyncio.timeout(10):
                answer = await call
    executor.assert_not_awaited()
    text = answer["result"]["content"][0]["text"]
    assert stored["id"] in text and "may have run" not in text and "No action was executed" not in text
    assert not data.agent_recovery.blocks(access_key.id, "next")


async def test_a_batch_item_after_the_token_hour_is_not_dispatched(queued):
    """Each dispatch checks the hour first, so a batch that outlives its token stops there."""
    app, _admin, data, access_key, tokens, _sign_in_record = queued
    await data.store.async_patch_access_key(access_key.id, cap_restart="allow")
    clock = _past_the_hour()
    runs: list[str] = []

    @write_boundary
    async def restart(args, access_key, hass, data):
        runs.append("restart")
        clock.start()
        return {"content": [{"type": "text", "text": "restarted"}]}, "allowed", "restart_ha"

    headers = {**HOST, "Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"}
    init = await app.post("/api/phoenix-mcp", headers=headers, json=_initialize())
    headers["MCP-Protocol-Version"] = "2025-03-26"
    if session := init.headers.get("Mcp-Session-Id"):
        headers["Mcp-Session-Id"] = session
    await app.post("/api/phoenix-mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    batch = [{"jsonrpc": "2.0", "id": index, "method": "tools/call",
              "params": {"name": "restart_ha", "arguments": {}}} for index in (1, 2)]
    try:
        with patch.object(system_tools, "_execute_restart_ha", restart):
            answer = await (await app.post("/api/phoenix-mcp", headers=headers, json=batch)).json()
    finally:
        clock.stop()
    assert runs == ["restart"]
    assert [reply["id"] for reply in answer if "error" in reply] == [2]


async def _reuse_ends_sign_in(app, admin, data, access_key, how) -> tuple[str, object]:
    """Sign in afresh and return its access token plus an ending: reuse of a secret, or the app revoking it."""
    data.oauth.open_window(access_key.id)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_id=CLIENT_ID, redirect_uri=REDIRECT)
    tokens = await (await _token(app, **form)).json()

    async def end() -> None:
        if how == "code":
            assert (await _token(app, **form)).status == 400
            return
        if how == "revoke":
            revoked = await app.post("/api/phoenix-mcp/oauth/revoke",
                                     data={"token": tokens["refresh_token"], "client_id": CLIENT_ID}, headers=HOST)
            assert revoked.status == 200
            return
        refresh = tokens["refresh_token"]
        for _ in range(OAUTH_REFRESH_USES_MAX):                        # the rotation and its retries
            await _refresh(app, refresh)
        assert (await _refresh(app, refresh))[0] == 400                # reuse ends the sign-in

    return tokens["access_token"], end


@pytest.mark.parametrize("how", ["refresh", "code", "revoke"])
async def test_reuse_cancels_what_the_sign_in_queued_at_once(queued, how):
    app, admin, data, access_key, _tokens, _sign_in_record = queued
    access_token, end = await _reuse_ends_sign_in(app, admin, data, access_key, how)
    await _call_restart(app, access_token)
    (stored,) = _pending(data)
    await end()
    closed = get_approval(data.store, stored["id"])
    assert (closed.status, closed.closure_reason) == ("cancelled", "oauth_sign_in_ended")
    (row,) = [e for e in data.audit.query(access_key_id=access_key.id) or [] if e.method == "approval/cancelled"]
    assert json.loads(row.payload) == {"approval_id": stored["id"], "reason": "oauth_sign_in_ended"}
    # The fixture's own sign-in is still live, and nothing it queued would have been touched.
    assert len(data.oauth_grants.list_for_key(access_key.id)) == 1


async def test_reuse_during_an_inline_wait_cancels_the_action(hass, queued):
    app, admin, data, access_key, _tokens, _sign_in_record = queued
    await data.store.async_patch_access_key(access_key.id, confirm_inline_wait_seconds=30)
    access_token, end = await _reuse_ends_sign_in(app, admin, data, access_key, "refresh")
    with patch(_EXECUTOR, AsyncMock(return_value=_OK)) as executor:
        call = hass.async_create_task(_call_restart(app, access_token))
        async with asyncio.timeout(10):
            while not _pending(data):
                await asyncio.sleep(0.01)
        (stored,) = _pending(data)
        await end()
        async with asyncio.timeout(10):
            answer = await call
    executor.assert_not_awaited()
    assert get_approval(data.store, stored["id"]).closure_reason == "oauth_sign_in_ended"
    assert "applied" not in json.dumps(answer)


# HTTPS only, darkness and oracles


async def test_plain_http_with_the_public_host_stays_dark(oauth):
    """The Host header alone never lights sign-in up: the request itself must be HTTPS."""
    app, _admin, data, _fetch = oauth
    await _key(data)
    cleartext = {"Host": "phx.example:443"}
    assert (await app.get("/.well-known/oauth-protected-resource/api/phoenix-mcp", headers=cleartext)).status == 404
    assert (await app.post("/api/phoenix-mcp/oauth/token", data={"grant_type": "refresh_token"},
                           headers=cleartext)).status == 404
    response = await app.post("/api/phoenix-mcp", json=_initialize(),
                              headers={**cleartext, "Authorization": "Bearer phx_" + "0" * 64})
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="Phoenix MCP"'
    # A proxy saying http is no better than no proxy.
    assert (await app.get("/.well-known/oauth-protected-resource/api/phoenix-mcp",
                          headers={**HOST, "X-Forwarded-Proto": "http"})).status == 404


async def test_host_header_cannot_choose_the_issuer_when_home_assistant_serves_tls(hass, oauth):
    """get_url's localhost fallback is not a configured address, so it names no issuer."""
    app, _admin, data, _fetch = oauth
    await _key(data)
    hass.config.api.use_ssl = True
    try:
        response = await app.get("/.well-known/oauth-protected-resource/api/phoenix-mcp",
                                 headers={**HTTPS, "Host": "localhost"})
    finally:
        hass.config.api.use_ssl = False
    assert response.status == 404


async def test_a_forwarded_host_cannot_choose_the_issuer(oauth):
    app, _admin, data, _fetch = oauth
    await _key(data)
    path = "/.well-known/oauth-authorization-server/api/phoenix-mcp/oauth"
    good = await app.get(path, headers={**HTTPS, "X-Forwarded-Host": "phx.example"})
    assert good.status == 200 and (await good.json())["issuer"] == ORIGIN + "/api/phoenix-mcp/oauth"
    assert (await app.get(path, headers={**HTTPS, "X-Forwarded-Host": "evil.example"})).status == 404


async def test_dark_routes_answer_404_under_the_kill_switch_and_over_budget(oauth):
    app, _admin, data, _fetch = oauth
    access_key, _raw = await _key(data, oauth=False)
    path = "/.well-known/oauth-protected-resource/api/phoenix-mcp"
    await data.store.async_patch_settings(kill_switch=True)
    assert (await app.get(path, headers=HOST)).status == 404
    assert (await _token(app, grant_type="refresh_token", refresh_token="phxr_" + "0" * 64)).status == 404
    await data.store.async_patch_settings(kill_switch=False)
    for _ in range(OAUTH_ADDRESS_REQUESTS_PER_MINUTE + 5):
        assert (await app.get(path, headers=HOST)).status == 404
    # Lit, the same address is over its budget: the darkness above counted nothing.
    await data.store.async_patch_access_key(access_key.id, oauth_enabled=True)
    for _ in range(OAUTH_ADDRESS_REQUESTS_PER_MINUTE):
        assert (await app.get(path, headers=HOST)).status == 200
    limited = await app.get(path, headers=HOST)
    assert limited.status == 429 and limited.headers["Retry-After"] == "60"


async def test_query_secrets_are_refused_even_with_a_valid_form(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    response = await app.post("/api/phoenix-mcp/oauth/token?refresh_token=" + tokens["refresh_token"],
                              data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
                              headers=HOST)
    assert response.status == 400 and (await response.json())["error"] == "invalid_request"


@pytest.mark.parametrize("body,content_type", [
    ('{"grant_type": "refresh_token"}', "application/json"),
    ("grant_type=refresh_token&grant_type=authorization_code", "application/x-www-form-urlencoded"),
    ("grant_type=refresh_token&x=" + "y" * 17000, "application/x-www-form-urlencoded"),
])
async def test_token_endpoint_reads_one_bounded_form(oauth, body, content_type):
    app, _admin, data, _fetch = oauth
    await _key(data)
    response = await app.post("/api/phoenix-mcp/oauth/token", data=body,
                              headers={**HOST, "Content-Type": content_type})
    assert response.status == 400 and (await response.json())["error"] == "invalid_request"


async def test_a_form_sent_in_pieces_is_read_whole(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    body = urlencode(dict(grant_type="authorization_code", code=code, client_id=CLIENT_ID,
                          redirect_uri=REDIRECT, code_verifier=verifier)).encode()

    async def pieces():
        yield body[:-20]
        await asyncio.sleep(0.05)
        yield body[-20:]

    response = await app.post("/api/phoenix-mcp/oauth/token", data=pieces(),
                              headers={**HOST, "Content-Type": "application/x-www-form-urlencoded"})
    assert response.status == 200 and (await response.json())["access_token"].startswith("phxo_")


async def test_a_form_that_stops_arriving_ends_its_own_request(oauth):
    app, _admin, data, _fetch = oauth
    await _key(data)

    async def stalls():
        yield b"grant_type=refresh_token"
        await asyncio.sleep(5)
        yield b"&refresh_token=x"

    with patch("custom_components.phoenix_mcp.oauth_views.OAUTH_FORM_READ_TIMEOUT_SECONDS", 0.1):
        response = await asyncio.wait_for(app.post(
            "/api/phoenix-mcp/oauth/token", data=stalls(),
            headers={**HOST, "Content-Type": "application/x-www-form-urlencoded"}), 3)
    assert response.status == 400 and (await response.json())["error"] == "invalid_request"


async def test_a_full_queue_looks_parked_to_the_app_and_busy_to_the_administrator(oauth):
    """The app sees the same redirect either way; only the admin view says the queue is full."""
    app, admin, data, _fetch = oauth
    await _key(data)
    _verifier, challenge = _pkce()
    parked = [await _authorize(app, challenge) for _ in range(OAUTH_PENDING_PER_ADDRESS_MAX)]
    refused = await _authorize(app, challenge)
    assert refused.status == parked[0].status == 302
    assert set(refused.headers) == set(parked[0].headers)
    busy_id = refused.headers["Location"].rsplit("/", 1)[1]
    assert len(busy_id) == len(parked[0].headers["Location"].rsplit("/", 1)[1])
    assert busy_id not in data.oauth.pending
    detail = await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{busy_id}")
    body = await detail.json()
    assert detail.status == 404 and body["error"] == "oauth_busy" and body["message_key"] == "adminError.oauthBusy"
    # A closed window and an unknown ID still read as ended.
    unknown = await (await admin.get("/api/phoenix-mcp/admin/oauth/requests/" + "x" * 43)).json()
    assert unknown["error"] == "not_found"
    data.oauth.clear()
    assert (await (await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{busy_id}")).json())["error"] == "not_found"


async def test_pending_caps_count_an_ipv6_64_as_one_address(oauth):
    app, _admin, data, _fetch = oauth
    await _key(data)
    _verifier, challenge = _pkce()
    legit = (await _authorize(app, challenge)).headers["Location"].rsplit("/", 1)[1]
    counter = itertools.count(1)
    with patch("custom_components.phoenix_mcp.oauth_views.get_client_ip",
               side_effect=lambda _r: f"2001:db8:1:2::{next(counter):x}"):
        for _ in range(OAUTH_PENDING_PER_ADDRESS_MAX + 4):
            assert (await _authorize(app, challenge)).status == 302
    assert data.oauth.get_pending(legit) is not None
    assert len(data.oauth.pending) == 1 + OAUTH_PENDING_PER_ADDRESS_MAX


def test_address_buckets_and_budget():
    from custom_components.phoenix_mcp.oauth_flow import OAuthRuntime, address_bucket

    assert address_bucket("2001:db8:1:2::5") == address_bucket("2001:db8:1:2:ffff::1") == "2001:db8:1:2::/64"
    assert address_bucket("2001:db8:1:3::5") != address_bucket("2001:db8:1:2::5")
    assert address_bucket("::ffff:192.0.2.4") == "192.0.2.4" == address_bucket("192.0.2.4")
    assert address_bucket("not an address") == "not an address"
    runtime = OAuthRuntime()
    assert all(runtime.address_allowed(f"2001:db8::{n:x}") for n in range(OAUTH_ADDRESS_REQUESTS_PER_MINUTE))
    assert not runtime.address_allowed("2001:db8::ffff")
    assert runtime.address_allowed("192.0.2.4")


@pytest.mark.parametrize("field,value", [
    ("state", "s" * 513),
    ("client_id", "https://localhost/c.json"),
    ("client_id", "https://app.example"),
    ("client_id", "https://app.example/./c.json"),
    ("client_id", "https://app%2eexample/c.json"),
    ("redirect_uri", "https://good.example\\@evil.example/cb"),
    ("redirect_uri", "https://evil.example\\.good.example/cb"),
    ("redirect_uri", "https://evil%2eexample/cb"),
    ("redirect_uri", "https://good.example/c b"),
    ("redirect_uri", "http://app.example/cb"),
])
async def test_authorize_refuses_addresses_that_could_hide_where_they_point(oauth, field, value):
    app, _admin, data, _fetch = oauth
    await _key(data)
    response = await _authorize(app, _pkce()[1], **{field: value})
    assert response.headers["Location"] == "/phoenix-mcp/oauth/invalid"
    assert not data.oauth.pending


def test_client_id_dot_segments_are_refused():
    """Home Assistant's own filter already refuses ``../`` in a query; the check stands alone."""
    from custom_components.phoenix_mcp.oauth_flow import valid_client_id

    assert valid_client_id("https://app.example/a/c.json")
    for value in ("https://app.example/a/../c.json", "https://app.example/./c.json", "https://app.example/.."):
        assert not valid_client_id(value), value


def test_loopback_redirect_keeps_its_host():
    assert not redirect_matches("http://127.0.0.1:5/callback", "http://localhost/callback")


# Codes


async def test_a_mismatched_exchange_burns_the_code_and_is_audited(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    burned = await _token(app, grant_type="authorization_code", code=code, code_verifier=verifier,
                          client_id="https://evil.example/c.json", redirect_uri=REDIRECT)
    assert (await burned.json())["error"] == "invalid_grant"
    assert [(e.outcome, e.oauth_app) for e in _token_rows(data, access_key.id)] == [("denied", APP_LABEL)]
    again = await _token(app, grant_type="authorization_code", code=code, code_verifier=verifier,
                         client_id=CLIENT_ID, redirect_uri=REDIRECT)
    assert (await again.json())["error"] == "invalid_grant"
    assert data.oauth_grants.list_for_key(access_key.id) == []


async def test_a_code_used_while_its_exchange_saves_ends_the_sign_in(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_id=CLIENT_ID, redirect_uri=REDIRECT)
    original = data.oauth_grants._async_commit
    gate = asyncio.Event()

    async def slow_commit(grants):
        await gate.wait()
        await original(grants)

    with patch.object(data.oauth_grants, "_async_commit", side_effect=slow_commit):
        first = asyncio.ensure_future(_token(app, **form))
        await asyncio.sleep(0.05)
        second = await _token(app, **form)
        gate.set()
        first_response = await first
    assert (await second.json())["error"] == "invalid_grant"
    assert (await first_response.json())["error"] == "invalid_grant"
    assert data.oauth_grants.list_for_key(access_key.id) == []
    assert [e.outcome for e in _token_rows(data, access_key.id)] == ["denied"]


async def test_a_failed_save_puts_the_code_back(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_id=CLIENT_ID, redirect_uri=REDIRECT)
    with patch.object(data.oauth_grants, "async_create", side_effect=DurableStorageError("disk")):
        first = await _token(app, **form)
    assert first.status == 503 and (await first.json())["error"] == "temporarily_unavailable"
    retry = await _token(app, **form)
    assert retry.status == 200 and (await _mcp(app, (await retry.json())["access_token"])).status == 200


async def test_a_full_key_burns_the_code_and_is_audited(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_id=CLIENT_ID, redirect_uri=REDIRECT)
    with patch.object(data.oauth_grants, "async_create", side_effect=GrantLimitError("per_key")):
        refused = await _token(app, **form)
    assert (await refused.json())["error"] == "access_denied"
    assert (await (await _token(app, **form)).json())["error"] == "invalid_grant"
    assert [e.outcome for e in _token_rows(data, access_key.id)] == ["denied"]


async def test_a_code_expires_after_its_minute(oauth, freezer: FrozenDateTimeFactory):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    freezer.tick(timedelta(seconds=OAUTH_CODE_TTL_SECONDS + 1))
    late = await _token(app, grant_type="authorization_code", code=code, code_verifier=verifier,
                        client_id=CLIENT_ID, redirect_uri=REDIRECT)
    assert (await late.json())["error"] == "invalid_grant"


@pytest.mark.parametrize("event", ["rotate", "switch_off", "revoke", "expire"])
async def test_the_token_endpoint_rechecks_the_key_a_code_was_approved_for(oauth, event):
    """Codes outlive the key's change here because the panel's cleanup is not run."""
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    await _key(data, "other-key")
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    if event == "rotate":
        await data.store.async_rotate_access_key(access_key.id)
    elif event == "switch_off":
        await data.store.async_patch_access_key(access_key.id, oauth_enabled=False)
    elif event == "revoke":
        await data.store.async_archive_access_key(access_key.id, revoked=True)
    else:
        access_key.expires_at = utcnow() - timedelta(seconds=1)
    response = await _token(app, grant_type="authorization_code", code=code, code_verifier=verifier,
                            client_id=CLIENT_ID, redirect_uri=REDIRECT)
    assert (await response.json())["error"] == "invalid_grant"
    assert not data.oauth_grants.list_for_key(access_key.id)


async def test_a_code_stays_bound_to_the_resource_it_was_approved_for(hass, oauth):
    """Approved through the external address, the code cannot be spent for the internal one."""
    app, admin, data, _fetch = oauth
    await hass.config.async_update(internal_url="https://phx.internal")
    access_key, _raw = await _key(data)
    verifier, challenge = _pkce()
    code = await _approve(app, admin, challenge, access_key.id)
    response = await app.post("/api/phoenix-mcp/oauth/token", headers={**HTTPS, "Host": "phx.internal"}, data=dict(
        grant_type="authorization_code", code=code, code_verifier=verifier, client_id=CLIENT_ID,
        redirect_uri=REDIRECT, resource="https://phx.internal/api/phoenix-mcp"))
    assert (await response.json())["error"] == "invalid_grant"


# Refresh grace


async def _refresh(app, token, **extra):
    response = await _token(app, grant_type="refresh_token", refresh_token=token, **extra)
    return response.status, await response.json()


async def test_a_refresh_token_serves_three_uses_in_its_grace_then_ends_the_sign_in(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    r0 = (await _sign_in(app, admin, access_key.id))["refresh_token"]
    _status, app_tokens = await _refresh(app, r0)                     # the app rotates R0
    for _ in range(OAUTH_REFRESH_USES_MAX - 1):                       # two retries inside the grace
        status, sibling = await _refresh(app, r0)
        assert status == 200 and (await _mcp(app, sibling["access_token"])).status == 200
    status, body = await _refresh(app, r0)                            # a fourth use is reuse
    assert status == 400 and body["error"] == "invalid_grant"
    assert data.oauth_grants.list_for_key(access_key.id) == []
    assert (await _mcp(app, app_tokens["access_token"])).status == 401
    assert _token_rows(data, access_key.id)[0].outcome == "denied"


async def test_three_concurrent_refreshes_of_one_token_all_succeed(oauth):
    """An app without a refresh lock may send three at once; each gets working tokens."""
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    r0 = (await _sign_in(app, admin, access_key.id))["refresh_token"]
    answers = await asyncio.gather(*(_refresh(app, r0) for _ in range(3)))
    assert [status for status, _body in answers] == [200, 200, 200]
    for _status, body in answers:
        assert (await _mcp(app, body["access_token"])).status == 200
    assert len(data.oauth_grants.list_for_key(access_key.id)) == 1


async def test_two_lost_refresh_answers_then_a_retry_keep_the_sign_in(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    r0 = (await _sign_in(app, admin, access_key.id))["refresh_token"]
    await _refresh(app, r0)                                           # its answer never arrived
    await _refresh(app, r0)                                           # nor this one
    status, body = await _refresh(app, r0)
    assert status == 200 and (await _mcp(app, body["access_token"])).status == 200
    assert (await _refresh(app, body["refresh_token"]))[0] == 200


async def test_a_refresh_token_saved_as_spent_has_one_use_left():
    """A record from before use counts: a token that gave its one sibling had two uses."""
    loaded = _HashUntil.from_dict({"hash": "h", "until": utcnow().isoformat(), "spent": True})
    assert loaded is not None and loaded.uses == OAUTH_REFRESH_USES_MAX - 1
    assert _HashUntil.from_dict(loaded.to_dict()).uses == loaded.uses


async def test_a_sibling_leaves_the_apps_access_token_its_hour(oauth, freezer: FrozenDateTimeFactory):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    r0 = (await _sign_in(app, admin, access_key.id))["refresh_token"]
    _status, app_tokens = await _refresh(app, r0)
    await _refresh(app, r0)
    freezer.tick(timedelta(seconds=OAUTH_REFRESH_GRACE_SECONDS * 5))
    assert (await _mcp(app, app_tokens["access_token"])).status == 200
    # The app's own refresh token was not pushed out either: rotating it still works.
    status, _body = await _refresh(app, app_tokens["refresh_token"])
    assert status == 200


async def test_siblings_stop_at_the_cap_and_further_use_is_reuse(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    r0 = (await _sign_in(app, admin, access_key.id))["refresh_token"]
    _s, r1 = await _refresh(app, r0)
    _s, s1 = await _refresh(app, r0)
    _s, r2 = await _refresh(app, r1["refresh_token"])                 # retires R1 and S1
    _s, a = await _refresh(app, r1["refresh_token"])
    _s, b = await _refresh(app, s1["refresh_token"])
    assert len(data.oauth_grants.list_for_key(access_key.id)[0].refresh_hashes) == OAUTH_REFRESH_SIBLINGS_MAX
    _s, _r3 = await _refresh(app, r2["refresh_token"])                # retires R2, A and B
    assert (await _refresh(app, r2["refresh_token"]))[0] == 200
    assert (await _refresh(app, a["refresh_token"]))[0] == 200
    # Three refresh tokens are current: one more sibling would pass the cap, so it is reuse.
    status, _body = await _refresh(app, b["refresh_token"])
    assert status == 400 and data.oauth_grants.list_for_key(access_key.id) == []


async def test_a_stolen_token_washed_through_many_rotations_is_still_reuse(oauth, freezer: FrozenDateTimeFactory):
    """The retired list is bounded; the family tag remembers every token the sign-in issued."""
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    r1 = (await _sign_in(app, admin, access_key.id))["refresh_token"]
    token = r1
    for _ in range(OAUTH_RETIRED_TOKENS_MAX + 2):
        status, body = await _refresh(app, token)
        assert status == 200
        token = body["refresh_token"]
        freezer.tick(timedelta(minutes=2))
    status, _body = await _refresh(app, r1)
    assert status == 400
    assert data.oauth_grants.list_for_key(access_key.id) == []
    assert (await _refresh(app, token))[0] == 400
    assert _token_rows(data, access_key.id)[0].outcome == "denied"


async def test_a_refresh_for_another_client_is_refused_and_audited(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    status, body = await _refresh(app, tokens["refresh_token"], client_id="https://evil.example/c.json")
    assert status == 400 and body["error"] == "invalid_grant"
    newest = _token_rows(data, access_key.id)[0]
    assert (newest.outcome, newest.oauth_app) == ("denied", APP_LABEL)
    assert (await _refresh(app, tokens["refresh_token"], client_id=CLIENT_ID))[0] == 200


async def test_a_sign_in_stored_before_family_tags_gets_one(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    r0 = (await _sign_in(app, admin, access_key.id))["refresh_token"]
    grant = data.oauth_grants.list_for_key(access_key.id)[0]
    grant.family_hash = ""
    _status, body = await _refresh(app, r0)
    assert body["refresh_token"].startswith("phxr_") and grant.family_hash == ""
    renewed = data.oauth_grants.list_for_key(access_key.id)[0]
    assert renewed.family_hash and renewed.family_hash != secret_hash(r0[5:21])
    assert (await _refresh(app, body["refresh_token"]))[0] == 200


async def test_sign_ins_persist_and_removal_deletes_their_file(hass, hass_storage):
    def store():
        return OAuthGrantStore(DurableStore(
            hass, OAUTH_GRANTS_STORAGE_VERSION, OAUTH_GRANTS_STORAGE_KEY, private=True, atomic_writes=True))

    keys = await AccessKeyStore.async_create(hass)
    key, _raw = await keys.async_create_access_key("phone", "admin", oauth_enabled=True)
    grants = store()
    await grants.async_load()
    issued = await grants.async_create(access_key=key, client_id=CLIENT_ID, client_name="App",
                                       redirect_host="app.example", created_by="admin")
    outcome, rotated, _grant = await grants.async_refresh(issued.refresh_token, CLIENT_ID, lambda _g: key)
    assert outcome == "issued" and rotated is not None
    assert (await grants.async_refresh(issued.refresh_token, CLIENT_ID, lambda _g: key))[0] == "issued"
    assert (await grants.async_refresh(issued.refresh_token, CLIENT_ID, lambda _g: key))[0] == "issued"
    text = repr(hass_storage[OAUTH_GRANTS_STORAGE_KEY]["data"])
    for raw in (issued.access_token, issued.refresh_token, rotated.refresh_token, issued.refresh_token[5:21]):
        assert raw not in text
    reloaded = store()
    await reloaded.async_load()
    grant = reloaded.get(issued.grant_id)
    assert grant is not None and grant.is_live(key) and grant.family_hash
    assert reloaded.find_by_access_hash(secret_hash(rotated.access_token)) is not None
    # The use count and the family tag came back from storage: a fourth use is reuse.
    assert (await reloaded.async_refresh(issued.refresh_token, CLIENT_ID, lambda _g: key))[0] == "reused"
    await reloaded.async_clear()
    assert OAUTH_GRANTS_STORAGE_KEY not in hass_storage and reloaded.get(issued.grant_id) is None


async def test_grant_caps_hold_at_create(hass):
    keys = await AccessKeyStore.async_create(hass)
    key, _raw = await keys.async_create_access_key("phone", "admin", oauth_enabled=True)
    grants = OAuthGrantStore()
    for _ in range(OAUTH_MAX_GRANTS_PER_ACCESS_KEY):
        await grants.async_create(access_key=key, client_id=CLIENT_ID, client_name="App",
                                  redirect_host="app.example", created_by="admin")
    with pytest.raises(GrantLimitError):
        await grants.async_create(access_key=key, client_id=CLIENT_ID, client_name="App",
                                  redirect_host="app.example", created_by="admin")


async def test_approval_refuses_a_key_at_its_sign_in_cap(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    for _ in range(OAUTH_MAX_GRANTS_PER_ACCESS_KEY):
        await data.oauth_grants.async_create(access_key=access_key, client_id=CLIENT_ID, client_name="App",
                                             redirect_host="app.example", created_by="admin")
    request_id = (await _authorize(app, _pkce()[1])).headers["Location"].rsplit("/", 1)[1]
    await admin.get(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}")
    refused = await admin.post(f"/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve",
                               json={"access_key_id": access_key.id})
    assert refused.status == 409


# Access tokens and admitted requests


async def test_the_access_token_lasts_one_hour(oauth, freezer: FrozenDateTimeFactory):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    assert tokens["expires_in"] == OAUTH_ACCESS_TOKEN_TTL_SECONDS
    freezer.tick(timedelta(seconds=OAUTH_ACCESS_TOKEN_TTL_SECONDS - 5))
    assert (await _mcp(app, tokens["access_token"])).status == 200
    assert (await _context(app, tokens["access_token"])).status == 200
    freezer.tick(timedelta(seconds=10))
    response = await _mcp(app, tokens["access_token"])
    assert response.status == 401 and 'error="invalid_token"' in response.headers["WWW-Authenticate"]
    assert (await _context(app, tokens["access_token"])).status == 401
    assert data.oauth_grants.list_for_key(access_key.id)


async def test_a_request_admitted_late_in_the_hour_ends_with_its_token(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    original = mcp_transport._read_post_body
    later = utcnow() + timedelta(seconds=OAUTH_ACCESS_TOKEN_TTL_SECONDS + 1)

    async def read_past_the_hour(post):
        chunks = await original(post)
        # Only the recheck's clock moves, so the client's own timeouts are unaffected.
        patcher.start()
        return chunks

    patcher = patch("custom_components.phoenix_mcp.request_authority.utcnow", return_value=later)
    try:
        with patch.object(mcp_transport, "_read_post_body", read_past_the_hour):
            assert (await _mcp(app, tokens["access_token"])).status == 401
    finally:
        patcher.stop()
    # A key is not bound by any token's hour.
    authority = RequestAuthority(MagicMock(), data, access_key.id, access_key.access_key_hash)
    assert authority.oauth_token_until is None


@pytest.mark.parametrize("remove_mid_request", [False, True])
async def test_removing_a_sign_in_ends_a_request_admitted_through_the_router(oauth, remove_mid_request):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    sign_in = data.oauth_grants.list_for_key(access_key.id)[0]
    original = mcp_transport._read_post_body

    async def read_then_remove(post):
        chunks = await original(post)
        if remove_mid_request:
            await data.oauth_grants.async_remove(sign_in.id)
        return chunks

    with patch.object(mcp_transport, "_read_post_body", read_then_remove):
        response = await _mcp(app, tokens["access_token"])
    assert response.status == (401 if remove_mid_request else 200)


async def test_context_rows_name_the_signed_in_app(oauth):
    app, admin, data, _fetch = oauth
    access_key, raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    assert (await _context(app, tokens["access_token"], "?format=json")).status == 200
    assert (await _context(app, raw)).status == 200
    rows = [e for e in data.audit.query(access_key_id=access_key.id) or [] if e.resource == "/api/phoenix-mcp/context"]
    assert sorted(str(e.oauth_app) for e in rows) == sorted([str(APP_LABEL), "None"])


async def test_context_refuses_tokens_of_an_expired_or_switched_off_key(oauth, freezer: FrozenDateTimeFactory):
    """OAuthGrant.is_live alone guards /context: its validity and OAuth terms both count."""
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    access_key.oauth_enabled = False  # as if the generation had not advanced
    assert (await _context(app, tokens["access_token"])).status == 401
    access_key.oauth_enabled = True
    assert (await _context(app, tokens["access_token"])).status == 200
    access_key.expires_at = utcnow() + timedelta(seconds=5)
    freezer.tick(timedelta(seconds=10))  # expired, not yet archived by its timer
    assert (await _context(app, tokens["access_token"])).status == 401


async def test_context_charges_the_burst(oauth):
    app, _admin, data, _fetch = oauth
    _key_record, raw = await data.store.async_create_access_key("bursty", "admin", rate_limit_burst=1)
    assert (await _context(app, raw)).status == 200
    assert (await _context(app, raw)).status == 429


@pytest.mark.parametrize("header", [
    "Basic {key}", "Token {key}", "{key}", "bearer {key}", "Bearer  {key}",
])
async def test_only_a_bearer_header_presents_a_key(oauth, header):
    app, _admin, data, _fetch = oauth
    _key_record, raw = await data.store.async_create_access_key("plain", "admin")
    response = await app.get("/api/phoenix-mcp/context", headers={**HOST, "Authorization": header.format(key=raw)})
    assert response.status == 401


async def test_a_key_needs_its_prefix_and_a_token_its_length(oauth):
    app, admin, data, _fetch = oauth
    access_key, raw = await _key(data)
    # A stored hash whose secret lacks the prefix is never looked up.
    unprefixed = "xhx_" + raw[4:]
    access_key.access_key_hash = secret_hash(unprefixed)
    assert (await _context(app, unprefixed)).status == 401
    access_key.access_key_hash = secret_hash(raw)
    assert (await _context(app, raw)).status == 200
    tokens = await _sign_in(app, admin, access_key.id)
    response = await _mcp(app, tokens["access_token"] + "0")
    assert response.status == 401 and "invalid_token" not in response.headers["WWW-Authenticate"]


async def test_expiry_closes_the_keys_window_and_drops_its_codes(hass, oauth, freezer: FrozenDateTimeFactory):
    from custom_components.phoenix_mcp.access_key_auth import async_archive_expired_access_key

    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    await _approve(app, admin, _pkce()[1], access_key.id)
    data.oauth.open_window(access_key.id)
    assert data.oauth.codes
    access_key.expires_at = utcnow() + timedelta(seconds=5)
    freezer.tick(timedelta(seconds=10))
    await async_archive_expired_access_key(hass, data, data.store.get_access_key_by_id(access_key.id))
    assert data.oauth.window_until(access_key.id) is None and not data.oauth.codes


# Revocation


async def test_revoking_either_token_ends_the_sign_in(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    for kind in ("access_token", "refresh_token"):
        data.oauth.open_window(access_key.id)
        tokens = await _sign_in(app, admin, access_key.id)
        revoked = await app.post("/api/phoenix-mcp/oauth/revoke", data={"token": tokens[kind], "client_id": CLIENT_ID},
                                 headers=HOST)
        assert revoked.status == 200 and await revoked.json() == {}
        assert (await _mcp(app, tokens["access_token"])).status == 401
        assert data.oauth_grants.list_for_key(access_key.id) == []


async def test_revoke_answers_alike_for_unknown_tokens_and_other_clients(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    for form in ({"token": "phxo_" + "0" * 64}, {"token": "nonsense"},
                 {"token": tokens["refresh_token"], "client_id": "https://evil.example/c.json"}):
        response = await app.post("/api/phoenix-mcp/oauth/revoke", data=form, headers=HOST)
        assert response.status == 200 and await response.json() == {}
    assert (await _mcp(app, tokens["access_token"])).status == 200
    assert (await app.post("/api/phoenix-mcp/oauth/revoke", data="{}", headers={
        **HOST, "Content-Type": "application/json"})).status == 400


async def test_revoke_reports_a_failed_save(oauth):
    app, admin, data, _fetch = oauth
    access_key, _raw = await _key(data)
    tokens = await _sign_in(app, admin, access_key.id)
    with patch.object(data.oauth_grants, "async_remove", side_effect=DurableStorageError("disk")):
        response = await app.post("/api/phoenix-mcp/oauth/revoke", data={"token": tokens["access_token"]},
                                  headers=HOST)
    assert response.status == 503
    assert (await _mcp(app, tokens["access_token"])).status == 200


async def _context(app, token, query=""):
    return await app.get("/api/phoenix-mcp/context" + query, headers={**HOST, "Authorization": f"Bearer {token}"})


async def test_removing_the_integration_deletes_the_sign_in_file(hass, hass_storage, enable_custom_integrations):
    from pytest_homeassistant.common import MockConfigEntry

    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = hass.data[DOMAIN]
    access_key, _raw = await data.store.async_create_access_key("phone", "admin", oauth_enabled=True)
    await data.oauth_grants.async_create(access_key=access_key, client_id=CLIENT_ID, client_name="App",
                                         redirect_host="app.example", created_by="admin")
    assert OAUTH_GRANTS_STORAGE_KEY in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert OAUTH_GRANTS_STORAGE_KEY not in hass_storage
