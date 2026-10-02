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
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    assert data.enforce_mcp_lifecycle is True
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


async def test_request_notification_refused_before_dispatch_and_session_allocation(http_authority, monkeypatch):
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp import mcp_view
    client, data = http_authority
    _, raw = await data.store.async_create_token("notifications", "admin", rate_limit_requests=0)
    dispatch = AsyncMock()
    monkeypatch.setattr(mcp_view, "_dispatch_mcp", dispatch)
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw),
                                 json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "GetDateTime"}})
    assert response.status == 400 and await response.read() == b""
    dispatch.assert_not_awaited()
    assert data.mcp_sessions == {}


async def test_legacy_initialized_first_batch_unknown_method_and_resource_preserve_session(http_authority):
    client, data = http_authority
    _, raw = await data.store.async_create_token("legacy", "admin", rate_limit_requests=0)
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw), json=_initialize())
    session = response.headers["Mcp-Session-Id"]
    headers = _headers(raw, **{"Mcp-Session-Id": session})
    response = await client.post("/api/phoenix-mcp", headers=headers, json=[
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
    ])
    assert response.status == 200 and (await response.json())[0]["id"] == "ping"
    response = await client.post("/api/phoenix-mcp", headers=headers,
                                 json={"jsonrpc": "2.0", "id": "unknown", "method": "unknown/method"})
    assert response.status == 200 and (await response.json())["error"]["code"] == -32601
    response = await client.post("/api/phoenix-mcp", headers=headers,
                                 json={"jsonrpc": "2.0", "id": "resource", "method": "resources/read", "params": {"uri": "missing://resource"}})
    assert response.status == 200 and (await response.json())["error"]["code"] == -32002
    assert session in data.mcp_sessions


async def test_non_tool_exception_is_scrubbed_and_retains_rpc_id(http_authority, monkeypatch, caplog):
    from custom_components.phoenix_mcp import mcp_view
    client, data = http_authority
    _, raw = await data.store.async_create_token("exception", "admin", rate_limit_requests=0)
    async def broken(*args, **kwargs):
        raise RuntimeError("private-value-and-path")
    monkeypatch.setattr(mcp_view, "_dispatch_mcp_authorized", broken)
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw), json=_initialize())
    value = await response.json()
    assert response.status == 200 and value["id"] == 1 and value["error"]["code"] == -32603
    assert "private-value-and-path" not in str(value) and "private-value-and-path" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_all_admin_routes_reject_real_read_only_ha_identity(hass, hass_client, hass_read_only_access_token):
    import inspect
    import re
    from custom_components.phoenix_mcp.admin_view import ALL_ADMIN_VIEWS
    from custom_components.phoenix_mcp.agentcli import ALL_AGENTCLI_ADMIN_VIEWS, ALL_AGENTCLI_CHAT_VIEWS
    assert await async_setup_component(hass, "http", {})
    store = await TokenStore.async_create(hass)
    hass.data[DOMAIN] = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    views = ALL_ADMIN_VIEWS + ALL_AGENTCLI_ADMIN_VIEWS + ALL_AGENTCLI_CHAT_VIEWS
    for view_cls in views:
        view = view_cls()
        view.hass = hass
        hass.http.register_view(view)
    client = await hass_client(hass_read_only_access_token)
    for view_cls in views:
        path = re.sub(r"\{[^}]+\}", "sample", view_cls.url)
        for method in ("get", "post", "put", "patch", "delete"):
            if not inspect.iscoroutinefunction(getattr(view_cls, method, None)):
                continue
            response = await client.request(method, path, json={})
            assert response.status == 403, (method, path, await response.text())
            assert (await response.json())["error"] == "forbidden"
    assert store.list_tokens() == []


async def _initialized_session(client, data, *, rate=0):
    token, raw = await data.store.async_create_token("session", "admin", rate_limit_requests=rate, rate_limit_burst=0)
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw), json=_initialize())
    assert response.status == 200
    headers = _headers(raw, **{"Mcp-Session-Id": response.headers["Mcp-Session-Id"]})
    response = await client.post("/api/phoenix-mcp", headers=headers,
                                 json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status == 202
    return token, raw, headers


@pytest.mark.parametrize("version", ["2025-06-18", "2025-11-25", "2099-01-01"])
@pytest.mark.parametrize("batch", [False, True])
async def test_legacy_foreign_version_reports_unsupported_and_preserves_session(http_authority, version, batch):
    from custom_components.phoenix_mcp.const import MCP_PROTOCOL_VERSIONS

    client, data = http_authority
    _, _, headers = await _initialized_session(client, data)
    body = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    response = await client.post("/api/phoenix-mcp", headers={**headers, "MCP-Protocol-Version": version},
                                 json=[body] if batch else body)
    error = (await response.json())["error"]
    assert response.status == 400
    assert error == {"code": -32022, "message": "Unsupported protocol version",
                     "data": {"supported": list(MCP_PROTOCOL_VERSIONS), "requested": version}}
    response = await client.post("/api/phoenix-mcp", headers=headers, json=body)
    assert response.status == 200 and "result" in await response.json()


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("sse", [False, True])
async def test_authority_loss_wire_code_matches_era_after_real_dispatch(http_authority, monkeypatch, modern, sse):
    import json
    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.const import MCP_AUTHORITY_CHANGED_ERROR

    client, data = http_authority
    token, raw, headers = await _initialized_session(client, data)
    body = {"jsonrpc": "2.0", "id": "authority", "method": "tools/list", "params": {}}
    if modern:
        headers = _headers(raw, **{"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"})
        body["params"] = {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }}
    if sse:
        headers["Accept"] = "application/json, text/event-stream"

    async def read_reply(response):
        if sse:
            messages = [json.loads(line[5:].strip()) for line in (await response.text()).splitlines()
                        if line.startswith("data:")]
            return next(item for item in reversed(messages) if item.get("id") == "authority")
        return await response.json()

    response = await client.post("/api/phoenix-mcp", headers=headers, json=body)
    assert response.status == 200 and "result" in await read_reply(response)
    original = mcp_view._dispatch_mcp_authorized

    async def revoke_after_dispatch(*args, **kwargs):
        result = await original(*args, **kwargs)
        await data.store.async_archive_token(token.id, revoked=True)
        return result

    monkeypatch.setattr(mcp_view, "_dispatch_mcp_authorized", revoke_after_dispatch)
    response = await client.post("/api/phoenix-mcp", headers=headers, json=body)
    reply = await read_reply(response)
    assert response.status == 200
    assert reply["error"]["code"] == (MCP_AUTHORITY_CHANGED_ERROR if modern else -32000)
    assert "result" not in reply
    rows = data.audit.query(token_id=token.id, limit=20)
    assert any(row.request_id == response.headers["X-Phoenix-Request-ID"] and row.outcome == "denied" for row in rows)


async def test_legacy_session_capacity_retries_without_eviction_and_delete_releases(http_authority, monkeypatch):
    from custom_components.phoenix_mcp import mcp_view

    client, data = http_authority
    monkeypatch.setattr(mcp_view, "_MCP_LEGACY_SESSION_TOKEN_LIMIT", 2)
    _, raw, headers = await _initialized_session(client, data)
    second = await client.post("/api/phoenix-mcp", headers=_headers(raw), json=_initialize())
    assert second.status == 200
    sessions = set(data.mcp_sessions)
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw), json=_initialize())
    assert response.status == 429
    assert 0 < int(response.headers["Retry-After"]) <= mcp_view._MCP_LEGACY_SESSION_TTL_SECONDS
    assert set(data.mcp_sessions) == sessions
    assert (await client.delete("/api/phoenix-mcp", headers=headers)).status == 204
    assert (await client.post("/api/phoenix-mcp", headers=_headers(raw), json=_initialize())).status == 200


async def test_legacy_session_idle_expiry_and_activity_refresh(http_authority, monkeypatch):
    from custom_components.phoenix_mcp import mcp_view

    client, data = http_authority
    _, _, headers = await _initialized_session(client, data)
    session = headers["Mcp-Session-Id"]
    clock = data.mcp_sessions[session]["last_seen"]
    # Replace this module's clock object, not asyncio's shared time module.
    from types import SimpleNamespace
    monkeypatch.setattr(mcp_view, "time", SimpleNamespace(monotonic=lambda: clock))
    body = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    for _ in range(2):
        clock += mcp_view._MCP_LEGACY_SESSION_TTL_SECONDS - 1
        assert (await client.post("/api/phoenix-mcp", headers=headers, json=body)).status == 200
        assert data.mcp_sessions[session]["last_seen"] == clock
    clock += mcp_view._MCP_LEGACY_SESSION_TTL_SECONDS + 1
    response = await client.post("/api/phoenix-mcp", headers=headers, json=body)
    assert response.status == 404
    assert session not in data.mcp_sessions


@pytest.mark.parametrize("sse", [False, True])
async def test_batch_within_capacity_refuses_exhausted_remaining_budget_before_dispatch(http_authority, monkeypatch, sse):
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp import mcp_view

    client, data = http_authority
    _, _, headers = await _initialized_session(client, data, rate=5)
    if sse:
        headers["Accept"] = "application/json, text/event-stream"
    dispatch = AsyncMock(wraps=mcp_view._dispatch_mcp)
    monkeypatch.setattr(mcp_view, "_dispatch_mcp", dispatch)
    body = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(4)]
    response = await client.post("/api/phoenix-mcp", headers=headers, json=body)
    assert response.status == 429
    assert int(response.headers["Retry-After"]) > 0
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("version", ["2025-06-18", "2025-11-25", "2099-01-01"])
async def test_initialize_negotiates_legacy_despite_foreign_header(http_authority, version):
    client, data = http_authority
    _, raw = await data.store.async_create_token("negotiate", "admin", rate_limit_requests=0)
    response = await client.post("/api/phoenix-mcp", headers=_headers(raw, **{"MCP-Protocol-Version": version}), json=_initialize())
    assert response.status == 200
    assert (await response.json())["result"]["protocolVersion"] == "2025-03-26"
    assert response.headers["Mcp-Session-Id"] in data.mcp_sessions


async def test_batch_drops_request_notifications_before_dispatch(http_authority, monkeypatch):
    from unittest.mock import AsyncMock
    from custom_components.phoenix_mcp import mcp_view

    client, data = http_authority
    _, _, headers = await _initialized_session(client, data)
    dispatch = AsyncMock(wraps=mcp_view._dispatch_mcp)
    monkeypatch.setattr(mcp_view, "_dispatch_mcp", dispatch)
    response = await client.post("/api/phoenix-mcp", headers=headers, json=[
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "restart_ha", "arguments": {}}},
        {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
    ])
    assert response.status == 200
    assert await response.json() == [{"jsonrpc": "2.0", "id": "ping", "result": {}}]
    assert [call.args[0] for call in dispatch.await_args_list] == ["ping"]


@pytest.mark.parametrize("sse", [False, True])
async def test_modern_resource_error_uses_modern_code_and_retains_id(http_authority, sse):
    import json

    client, data = http_authority
    _, raw = await data.store.async_create_token("resource", "admin", rate_limit_requests=0)
    headers = _headers(raw, **{"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "resources/read", "Mcp-Name": "missing://resource"})
    if sse:
        headers["Accept"] = "application/json, text/event-stream"
    response = await client.post("/api/phoenix-mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": "resource-id", "method": "resources/read", "params": {
            "uri": "missing://resource", "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    })
    if sse:
        reply = [json.loads(line[5:].strip()) for line in (await response.text()).splitlines() if line.startswith("data:")][-1]
    else:
        reply = await response.json()
    assert response.status == 200
    assert reply["id"] == "resource-id"
    assert reply["error"]["code"] == -32602
