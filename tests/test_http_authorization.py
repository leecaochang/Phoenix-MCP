"""Exercise bearer admission and session isolation through HA's real HTTP router."""

from datetime import timedelta

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN, TOKEN_PREFIX
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import ALL_MCP_VIEWS
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import TokenStore


@pytest.fixture
async def http_authority(hass, hass_client_no_auth):
    assert await async_setup_component(hass, "http", {})
    store = await TokenStore.async_create(hass)
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog(), enforce_mcp_lifecycle=True)
    hass.data[DOMAIN] = data
    for view_cls in ALL_MCP_VIEWS:
        view = view_cls()
        view.hass = hass
        hass.http.register_view(view)
    return await hass_client_no_auth(), data


def _headers(raw, **extra):
    return {"Authorization": f"Bearer {raw}", "Accept": "application/json", **extra}


def _initialize():
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
    }}


@pytest.mark.parametrize("path", ["/api/phoenix-mcp", "/api/phoenix-mcp/context"])
async def test_unknown_well_formed_bearer_is_unauthorized(http_authority, path):
    client, data = http_authority
    _, known = await data.store.async_create_token("known", "admin")
    raw = TOKEN_PREFIX + "0" * (len(known) - len(TOKEN_PREFIX))
    response = await (client.post(path, headers=_headers(raw), json=_initialize()) if path.endswith("mcp")
                      else client.get(path, headers=_headers(raw)))
    assert response.status == 401
    assert "Bearer" in response.headers["WWW-Authenticate"]


async def test_expired_unarchived_bearer_is_refused_and_archived(http_authority):
    client, data = http_authority
    token, raw = await data.store.async_create_token("expired", "admin", expires_at=utcnow() - timedelta(seconds=1))
    response = await client.get("/api/phoenix-mcp/context", headers=_headers(raw))
    assert response.status == 401
    assert data.store.get_token_by_id(token.id) is None
    assert any(record.id == token.id for record in data.store.list_archived())


async def test_real_single_request_rate_limit_is_enforced(http_authority):
    client, data = http_authority
    _, raw = await data.store.async_create_token("limited", "admin", rate_limit_requests=1, rate_limit_burst=0)
    first = await client.get("/api/phoenix-mcp/context", headers=_headers(raw))
    second = await client.get("/api/phoenix-mcp/context", headers=_headers(raw))
    assert first.status == 200
    assert second.status == 429
    assert int(second.headers["Retry-After"]) > 0


@pytest.mark.parametrize("state", ["kill_switch", "not_ready", "shutting_down"])
@pytest.mark.parametrize("method,path", [("POST", "/api/phoenix-mcp"), ("DELETE", "/api/phoenix-mcp"), ("GET", "/api/phoenix-mcp/context")])
async def test_unavailable_runtime_refuses_every_authenticated_ingress(http_authority, state, method, path):
    client, data = http_authority
    _, raw = await data.store.async_create_token("bound", "admin")
    if state == "kill_switch":
        data.store.get_settings().kill_switch = True
    elif state == "not_ready":
        data.ready = False
    else:
        data.shutting_down = True
    response = await client.request(method, path, headers=_headers(raw), json=_initialize() if method == "POST" else None)
    assert response.status == 503


async def test_session_cannot_be_used_or_deleted_by_another_token(http_authority):
    client, data = http_authority
    _, raw_a = await data.store.async_create_token("a", "admin", rate_limit_requests=0)
    _, raw_b = await data.store.async_create_token("b", "admin", rate_limit_requests=0)
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw_a), json=_initialize())
    assert response.status == 200
    session = response.headers["Mcp-Session-Id"]
    owner_headers = _headers(raw_a, **{"Mcp-Session-Id": session})
    await client.post("/api/phoenix-mcp", headers=owner_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    assert (await client.post("/api/phoenix-mcp", headers=owner_headers, json=ping)).status == 200
    other_headers = _headers(raw_b, **{"Mcp-Session-Id": session})
    assert (await client.post("/api/phoenix-mcp", headers=other_headers, json=ping)).status == 404
    assert (await client.delete("/api/phoenix-mcp", headers=other_headers)).status == 404
    assert (await client.delete("/api/phoenix-mcp", headers=owner_headers)).status == 204


async def test_delete_refuses_foreign_origin(http_authority):
    client, data = http_authority
    _, raw = await data.store.async_create_token("owner", "admin")
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw), json=_initialize())
    assert response.status == 200
    headers = _headers(raw, **{"Mcp-Session-Id": response.headers["Mcp-Session-Id"], "Origin": "https://foreign.example"})
    assert (await client.delete("/api/phoenix-mcp", headers=headers)).status == 403
