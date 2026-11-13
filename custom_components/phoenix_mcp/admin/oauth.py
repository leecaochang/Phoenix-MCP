"""Admin views for OAuth sign-in: approving a request, the sign-in window, and sign-ins.

An app's sign-in request reaches the panel through a deep link; these views let an
administrator see who is asking, approve it for one access key whose window is open, or deny
it. The kill switch refuses everything here except listing and removing sign-ins.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlencode, urlsplit

from aiohttp import web
from homeassistant.components.http.const import KEY_HASS_USER

from ..approvals import REASON_OAUTH_SIGN_IN_REMOVED, async_cancel_sign_in_approvals
from ..const import DOMAIN, OAUTH_MAX_GRANTS_PER_ACCESS_KEY, OAUTH_RESOURCE_PATH
from ..data import PhoenixData
from ..durable_storage import DurableStorageError
from ..oauth_client_metadata import async_fetch_client_verdict
from ..oauth_flow import (
    ClientVerdict, PendingRequest, configured_public_url, is_loopback_redirect, redirect_host,
)
from ..view_base import PhoenixView, _err
from .common import _audit_admin, _ok, _read_body, require_admin


def _kill_switch_refusal(data: PhoenixData, rid: str) -> web.Response | None:
    if data.store.get_settings().kill_switch:
        return _err(
            "service_unavailable", "Phoenix MCP is stopped by the kill switch.", 503, rid,
            key="oauthKillSwitch",
        )
    return None


async def _verdict(pending: PendingRequest) -> ClientVerdict:
    """Fetch the app's metadata document once per request and remember the answer."""
    if pending.verdict is not None:
        return pending.verdict
    if pending.verdict_task is None:
        pending.verdict_task = asyncio.get_running_loop().create_task(
            async_fetch_client_verdict(pending.client_id, pending.redirect_uri))
    verdict = await asyncio.shield(pending.verdict_task)
    pending.verdict = verdict
    return verdict


def _eligible_keys(data: PhoenixData) -> list[dict]:
    """Access keys an approval may bind to now: valid, OAuth on, window open."""
    keys = []
    for key in data.store.list_access_keys():
        until = data.oauth.window_until(key.id)
        if until is None or not key.oauth_enabled or not key.is_valid():
            continue
        keys.append({
            "id": key.id,
            "name": key.name,
            "persona": key.persona,
            "pass_through": key.pass_through,
            "window_open_until": until.isoformat(),
            "at_cap": data.oauth_grants.count_for_key(key.id, key.oauth_generation) >= OAUTH_MAX_GRANTS_PER_ACCESS_KEY,
        })
    return keys


def _redirect(uri: str, params: dict[str, str]) -> str:
    separator = "&" if urlsplit(uri).query else "?"
    return uri + separator + urlencode(params)


class PhoenixAdminOAuthRequestView(PhoenixView):
    """GET /api/phoenix-mcp/admin/oauth/requests/{request_id} - what an app is asking for."""

    url = "/api/phoenix-mcp/admin/oauth/requests/{request_id}"
    name = "api:phoenix-mcp:admin:oauth:request"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, request_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if (refusal := _kill_switch_refusal(data, rid)) is not None:
            return refusal
        pending = data.oauth.get_pending(request_id)
        if pending is None:
            return _err("not_found", "This sign-in request has ended.", 404, rid, key="oauthRequestExpired")
        pending.opened = True
        verdict = await _verdict(pending)
        return _ok({
            "id": pending.id,
            "client_id": pending.client_id,
            "client_host": urlsplit(pending.client_id).hostname or "",
            "client_name": verdict.name,
            "verified": verdict.ok,
            "problem": verdict.problem or None,
            "redirect_host": redirect_host(pending.redirect_uri),
            "loopback_only": is_loopback_redirect(pending.redirect_uri),
            "created_at": pending.created_at.isoformat(),
            "expires_at": pending.expires_at.isoformat(),
            "keys": _eligible_keys(data),
        }, request_id=rid)


class PhoenixAdminOAuthRequestApproveView(PhoenixView):
    """POST /api/phoenix-mcp/admin/oauth/requests/{request_id}/approve - bind it to a key."""

    url = "/api/phoenix-mcp/admin/oauth/requests/{request_id}/approve"
    name = "api:phoenix-mcp:admin:oauth:request:approve"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, request_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if (refusal := _kill_switch_refusal(data, rid)) is not None:
            return refusal
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        access_key_id = body.get("access_key_id")
        pending = data.oauth.get_pending(request_id)
        if pending is None:
            return _err("not_found", "This sign-in request has ended.", 404, rid, key="oauthRequestExpired")
        if pending.verdict is None or not pending.verdict.ok:
            return _err("invalid_request", "This app could not be verified.", 400, rid, key="oauthClientNotVerified")
        access_key = data.store.get_access_key_by_id(access_key_id) if isinstance(access_key_id, str) else None
        if (access_key is None or not access_key.is_valid() or not access_key.oauth_enabled
                or data.oauth.window_until(access_key.id) is None):
            return _err(
                "invalid_request", "That access key is not open for a new sign-in.", 400, rid,
                key="oauthKeyNotEligible",
            )
        if data.oauth_grants.count_for_key(access_key.id, access_key.oauth_generation) >= OAUTH_MAX_GRANTS_PER_ACCESS_KEY:
            return _err(
                "conflict", "That access key has the most sign-ins it may have.", 409, rid,
                key="oauthSignInLimit", params={"max": OAUTH_MAX_GRANTS_PER_ACCESS_KEY},
            )
        # No await since the checks above: a second approval finds the request gone.
        if data.oauth.pop_pending(request_id) is None:
            return _err("not_found", "This sign-in request has ended.", 404, rid, key="oauthRequestExpired")
        data.oauth.close_window(access_key.id)
        user = request[KEY_HASS_USER]
        code = data.oauth.issue_code(
            access_key_id=access_key.id, generation=access_key.oauth_generation,
            client_id=pending.client_id, client_name=pending.verdict.name,
            redirect_uri=pending.redirect_uri, code_challenge=pending.code_challenge,
            resource=pending.resource, created_by=user.id,
        )
        params = {"code": code}
        if pending.state is not None:
            params["state"] = pending.state
        params["iss"] = pending.issuer
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"redirect_to": _redirect(pending.redirect_uri, params)}, request_id=rid)


class PhoenixAdminOAuthRequestDenyView(PhoenixView):
    """POST /api/phoenix-mcp/admin/oauth/requests/{request_id}/deny - refuse the sign-in.

    The app is told only when its return address was verified; otherwise there is nowhere
    safe to send the browser and the panel just closes the request.
    """

    url = "/api/phoenix-mcp/admin/oauth/requests/{request_id}/deny"
    name = "api:phoenix-mcp:admin:oauth:request:deny"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, request_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        pending = data.oauth.pop_pending(request_id)
        if pending is None:
            return _err("not_found", "This sign-in request has ended.", 404, rid, key="oauthRequestExpired")
        redirect_to = None
        if pending.verdict is not None and pending.verdict.ok:
            params = {"error": "access_denied"}
            if pending.state is not None:
                params["state"] = pending.state
            params["iss"] = pending.issuer
            redirect_to = _redirect(pending.redirect_uri, params)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"redirect_to": redirect_to}, request_id=rid)


def _status(view: PhoenixView, data: PhoenixData, access_key) -> dict:
    until = data.oauth.window_until(access_key.id)
    public = configured_public_url(view.hass)
    return {
        "oauth_enabled": access_key.oauth_enabled,
        "window_open_until": until.isoformat() if until else None,
        "kill_switch": data.store.get_settings().kill_switch,
        "public_url": public + OAUTH_RESOURCE_PATH if public else None,
        "max_sign_ins": OAUTH_MAX_GRANTS_PER_ACCESS_KEY,
        "sign_ins": [
            grant.to_admin_dict() for grant in data.oauth_grants.list_for_key(access_key.id)
            if grant.is_live(access_key)
        ],
    }


class PhoenixAdminOAuthSummaryView(PhoenixView):
    """GET /api/phoenix-mcp/admin/oauth/summary - each key's signed-in apps and open window.

    For the access key list, so the key record and its contract stay unchanged.
    """

    url = "/api/phoenix-mcp/admin/oauth/summary"
    name = "api:phoenix-mcp:admin:oauth:summary"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        keys = {}
        for access_key in data.store.list_access_keys():
            if not access_key.oauth_enabled:
                continue
            until = data.oauth.window_until(access_key.id)
            keys[access_key.id] = {
                "sign_ins": sum(1 for g in data.oauth_grants.list_for_key(access_key.id) if g.is_live(access_key)),
                "window_open_until": until.isoformat() if until else None,
            }
        return _ok({"keys": keys, "kill_switch": data.store.get_settings().kill_switch}, request_id=rid)


class PhoenixAdminAccessKeyOAuthView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys/{access_key_id}/oauth - sign-in status.

    The window's end time is runtime state, never stored; the panel polls this while a
    window is open so an approval in another tab shows up.
    """

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/oauth"
    name = "api:phoenix-mcp:admin:access-key:oauth"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
        return _ok(_status(self, data, access_key), request_id=rid)


class PhoenixAdminAccessKeyOAuthWindowView(PhoenixView):
    """POST or DELETE /api/phoenix-mcp/admin/access-keys/{access_key_id}/oauth/window - open or close the window for a new sign-in."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/oauth/window"
    name = "api:phoenix-mcp:admin:access-key:oauth:window"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if (refusal := _kill_switch_refusal(data, rid)) is not None:
            return refusal
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
        if not access_key.oauth_enabled or not access_key.is_valid():
            return _err(
                "invalid_request", "Turn on OAuth sign-in for this access key first.", 400, rid,
                key="oauthNotEnabled",
            )
        if configured_public_url(self.hass) is None:
            return _err(
                "invalid_request", "Home Assistant has no HTTPS address, so apps can't reach sign-in.",
                400, rid, key="oauthNoHttps",
            )
        data.oauth.open_window(access_key.id)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok(_status(self, data, access_key), request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, access_key_id: str) -> web.Response:
        """Close the key's sign-in window early. Works under the kill switch, since it only takes access away."""
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
        data.oauth.close_window(access_key.id)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok(_status(self, data, access_key), request_id=rid)


class PhoenixAdminAccessKeyOAuthSignInView(PhoenixView):
    """DELETE /api/phoenix-mcp/admin/access-keys/{access_key_id}/oauth/sign-ins/{sign_in_id}.

    Removes one app's sign-in. Works under the kill switch, since it only takes access away.
    """

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/oauth/sign-ins/{sign_in_id}"
    name = "api:phoenix-mcp:admin:access-key:oauth:sign-in"
    requires_auth = True

    @require_admin
    async def delete(self, request: web.Request, access_key_id: str, sign_in_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        grant = data.oauth_grants.get(sign_in_id)
        if grant is None or grant.access_key_id != access_key_id:
            return _err("not_found", "That sign-in no longer exists.", 404, rid, key="oauthSignInNotFound")
        try:
            await data.oauth_grants.async_remove(sign_in_id)
        except DurableStorageError:
            return _err("service_unavailable", "The sign-in could not be removed.", 503, rid, key="oauthSaveFailed")
        # What the sign-in queued goes with it, inline waits included; approve also rechecks it.
        await async_cancel_sign_in_approvals(
            self.hass, data, access_key_id, REASON_OAUTH_SIGN_IN_REMOVED, sign_in_id=sign_in_id,
        )
        _audit_admin(self.hass, request, rid, request.path)
        return web.Response(status=204, headers={"X-Phoenix-Request-ID": rid})
