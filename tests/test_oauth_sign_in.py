"""OAuth sign-in for access keys, end to end through Home Assistant's real HTTP router.

The app side is played by an unauthenticated client sending the Host header of the
instance's configured HTTPS address; the administrator side by Home Assistant's
authenticated admin client. Only the metadata-document fetch is replaced, at the seam
where the approval view calls it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.admin.access_keys import PhoenixAdminAccessKeyView, PhoenixAdminAccessKeysView
from custom_components.phoenix_mcp.admin.oauth import (
    PhoenixAdminAccessKeyOAuthSignInView, PhoenixAdminAccessKeyOAuthView,
    PhoenixAdminAccessKeyOAuthWindowView, PhoenixAdminOAuthRequestApproveView,
    PhoenixAdminOAuthRequestDenyView, PhoenixAdminOAuthRequestView, PhoenixAdminOAuthSummaryView,
)
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN, OAUTH_REFRESH_GRACE_SECONDS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_transport import ALL_MCP_VIEWS
from custom_components.phoenix_mcp.oauth_client_metadata import check_document
from custom_components.phoenix_mcp.oauth_flow import ClientVerdict, pkce_matches, redirect_matches
from custom_components.phoenix_mcp.oauth_views import ALL_OAUTH_VIEWS
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.request_authority import AuthorityChangedError, RequestAuthority

ORIGIN = "https://phx.example"
HOST = {"Host": "phx.example:443"}
CLIENT_ID = "https://app.example/oauth/client.json"
REDIRECT = "https://app.example/callback"
_ADMIN_VIEWS = (
    PhoenixAdminAccessKeysView, PhoenixAdminAccessKeyView,
    PhoenixAdminOAuthRequestView, PhoenixAdminOAuthRequestApproveView, PhoenixAdminOAuthRequestDenyView,
    PhoenixAdminAccessKeyOAuthView, PhoenixAdminAccessKeyOAuthWindowView, PhoenixAdminAccessKeyOAuthSignInView,
    PhoenixAdminOAuthSummaryView,
)


@pytest.fixture
async def oauth(hass, hass_client, hass_client_no_auth):
    assert await async_setup_component(hass, "http", {})
    await hass.config.async_update(external_url=ORIGIN)
    store = await AccessKeyStore.async_create(hass)
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    for view_cls in (*ALL_MCP_VIEWS, *ALL_OAUTH_VIEWS, *_ADMIN_VIEWS):
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
    assert (await _authorize(app, challenge)).headers["Location"] == "/phoenix-mcp/oauth/closed"
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
