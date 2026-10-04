"""Phoenix-owned recovery presentation preserves durable and admin boundaries."""
import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.helpers import issue_registry as ir

from custom_components.phoenix_mcp.admin_view import PhoenixAdminAttentionView, PhoenixAdminAttentionReviewView
from custom_components.phoenix_mcp.agent_recovery import AgentRecovery
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.tools.calendar import CalendarWriteJournal


@pytest.fixture
async def attention_env(hass, hass_client, access_key_store):
    assert await async_setup_component(hass, "http", {})
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    access_key, _ = await access_key_store.async_create_access_key("Review token", "admin")
    for cls in (PhoenixAdminAttentionView, PhoenixAdminAttentionReviewView):
        view = cls()
        view.hass = hass
        hass.http.register_view(view)
    return await hass_client(), data, access_key


async def test_review_requires_confirmation_and_inactive_action(attention_env, hass):
    client, data, access_key = attention_env
    await data.agent_recovery.begin(access_key.id, "call_service", "request")
    path = "/api/phoenix-mcp/admin/attention/agent/request"
    assert (await client.get("/api/phoenix-mcp/admin/attention")).status == 200
    for body in ({}, {"checked_outcome": "true"}, {"checked_outcome": 1}):
        assert (await client.post(path, json=body)).status == 400
    assert (await client.post(path, json={"checked_outcome": True})).status == 409
    data.agent_recovery.interrupted("request")
    assert data.agent_recovery.blocks(access_key.id)
    response = await client.get("/api/phoenix-mcp/admin/attention")
    items = (await response.json())["items"]
    assert items[0]["token_name"] == "Review token"
    assert items[0]["tool"] == "call_service" and "arguments" not in json.dumps(items)
    assert not ir.async_get(hass).issues
    data.store.get_settings().kill_switch = True
    assert (await client.post(path, json={"checked_outcome": True})).status == 200
    assert not data.agent_recovery.blocks(access_key.id)
    assert await (await client.get("/api/phoenix-mcp/admin/attention")).json() == {"items": []}
    assert (await client.post(path, json={"checked_outcome": True})).status == 409


async def test_failed_review_preserves_barrier_and_hides_exception(attention_env):
    client, data, access_key = attention_env
    data.agent_recovery._store = AsyncMock()
    await data.agent_recovery.begin(access_key.id, "call_service", "request")
    data.agent_recovery.interrupted("request")
    data.agent_recovery._store.async_save.side_effect = OSError("private credential")
    response = await client.post("/api/phoenix-mcp/admin/attention/agent/request", json={"checked_outcome": True})
    assert response.status == 503
    assert "private credential" not in await response.text()
    assert data.agent_recovery.blocks(access_key.id)


async def test_calendar_and_mesa_items_do_not_expose_payloads(attention_env, hass):
    client, data, access_key = attention_env
    data.calendar_writes = CalendarWriteJournal(hass)
    data.calendar_writes.records = [{"receipt_id": "r", "entity_id": "calendar.test", "token_id": access_key.id,
                                    "operation": "create", "created_at": "2026-10-03T00:00:00Z",
                                    "status": "outcome_unknown", "requested": {"summary": "private event"}}]
    data.calendar_writes.save = AsyncMock()
    data.mesa_setup_failed = True
    result = await (await client.get("/api/phoenix-mcp/admin/attention")).json()
    assert {row["kind"] for row in result["items"]} == {"calendar", "mesa"}
    assert "private event" not in json.dumps(result)
    for kind in ("mesa", "bad"):
        assert (await client.post(f"/api/phoenix-mcp/admin/attention/{kind}/r", json={"checked_outcome": True})).status == 404
    data.calendar_writes.active.add("r")
    assert (await client.post("/api/phoenix-mcp/admin/attention/calendar/r", json={"checked_outcome": True})).status == 409
    data.calendar_writes.active.clear()
    assert (await client.post("/api/phoenix-mcp/admin/attention/calendar/r", json={"checked_outcome": True})).status == 200
    assert data.calendar_writes.records[0]["status"] == "reviewed"
    data.calendar_writes.save.assert_awaited_once()


async def test_unreadable_calendar_history_is_not_an_empty_list(attention_env, hass):
    client, data, _ = attention_env
    data.calendar_writes = CalendarWriteJournal(hass)
    data.calendar_writes.store.async_load = AsyncMock(side_effect=OSError("private storage"))
    result = await (await client.get("/api/phoenix-mcp/admin/attention")).json()
    assert result == {"items": [{"id": "calendar_storage", "kind": "calendar_storage"}]}


@pytest.mark.parametrize("admin", [False, True])
async def test_attention_is_ha_admin_only(hass, hass_client_no_auth, hass_access_token, hass_admin_user, admin):
    assert await async_setup_component(hass, "http", {})
    hass_admin_user.is_admin = admin
    store = await AccessKeyStore.async_create(hass)
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    for cls in (PhoenixAdminAttentionView, PhoenixAdminAttentionReviewView):
        view = cls(); view.hass = hass; hass.http.register_view(view)
    client = await hass_client_no_auth()
    _, raw = await store.async_create_access_key("MCP", "admin")
    for path, method in (("/api/phoenix-mcp/admin/attention", "GET"), ("/api/phoenix-mcp/admin/attention/agent/r", "POST")):
        assert (await client.request(method, path, headers={"Authorization": f"Bearer {raw}"}, json={})).status == 401
        response = await client.request(method, path, headers={"Authorization": f"Bearer {hass_access_token}"}, json={})
        expected = (200 if method == "GET" else 400) if admin else 403
        assert response.status == expected


async def test_concurrent_reviews_only_acknowledge_once():
    import asyncio
    disk = AsyncMock()
    recovery = AgentRecovery(disk)
    await recovery.begin("token", "call_service", "r")
    recovery.interrupted("r")
    results = await asyncio.gather(recovery.reviewed("r"), recovery.reviewed("r"))
    assert sorted(results) == [False, True]


async def test_review_rechecks_admin_after_body_read(attention_env, hass_admin_user, monkeypatch):
    from custom_components.phoenix_mcp import admin_view
    client, data, access_key = attention_env
    await data.agent_recovery.begin(access_key.id, "call_service", "request")
    data.agent_recovery.interrupted("request")
    async def changed_authority(*args):
        hass_admin_user.is_admin = False
        return {"checked_outcome": True}
    monkeypatch.setattr(admin_view, "_read_body", changed_authority)
    response = await client.post("/api/phoenix-mcp/admin/attention/agent/request", json={"checked_outcome": True})
    assert response.status == 403
    assert data.agent_recovery.blocks(access_key.id)
