"""Tests for the bounded subscription MCP tool (watch_entity).

It blocks the tool call up to `timeout` (max 30s) and returns when the accessible
entity changes state, or reports no change on timeout.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import patch

from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord


def _access_key(**caps) -> AccessKeyRecord:
    tree = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    base = {"cap_config_read": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, access_key, hass):
    from unittest.mock import MagicMock
    return await _call_tool(name, args, access_key, hass, MagicMock())


class TestWatchEntity:
    async def test_deny(self, hass):
        _, outcome, _ = await _call("watch_entity", {"entity_id": "light.kitchen"}, _access_key(cap_config_read="deny"), hass)
        assert outcome == "denied"

    async def test_inaccessible_not_found(self, hass):
        _, outcome, _ = await _call("watch_entity", {"entity_id": "sensor.secret"}, _access_key(), hass)
        assert outcome == "not_found"

    async def test_returns_change(self, hass):
        import custom_components.phoenix_mcp.mcp_view as mcp_view

        hass.states.async_set("light.kitchen", "on", {})
        access_key = _access_key()

        # Deterministic readiness instead of a fixed sleep: wrap the real
        # async_track_state_change_event so the change fires only once the tool
        # has registered its listener (registration is synchronous, so the
        # listener is live the moment the wrapper sets the event).
        ready = asyncio.Event()
        real_track = mcp_view.async_track_state_change_event

        def _tracked(h, eids, cb):
            unsub = real_track(h, eids, cb)
            ready.set()
            return unsub

        with patch.object(mcp_view, "async_track_state_change_event", side_effect=_tracked):
            task = asyncio.create_task(
                _call("watch_entity", {"entity_id": "light.kitchen", "timeout": 5}, access_key, hass))
            await asyncio.wait_for(ready.wait(), timeout=2)
            hass.states.async_set("light.kitchen", "off", {})
            content, outcome, _ = await task

        assert outcome == "allowed"
        body = _json(content)
        assert body["changed"] is True
        assert body["state"] == "off"

    async def test_timeout_no_change(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        content, outcome, _ = await _call("watch_entity", {"entity_id": "light.kitchen", "timeout": 1}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["changed"] is False


class TestWatchUntilState:
    async def test_a_state_already_reached_returns_at_once(self, hass):
        hass.states.async_set("light.kitchen", "off", {})
        loop = asyncio.get_running_loop()
        started = loop.time()
        content, outcome, _ = await _call(
            "watch_entity", {"entity_id": "light.kitchen", "until_state": "off", "timeout": 5}, _access_key(), hass)
        body = _json(content)
        assert outcome == "allowed" and body["reached"] is True and body["changed"] is False
        assert loop.time() - started < 1

    async def test_other_changes_do_not_end_the_wait(self, hass):
        from custom_components.phoenix_mcp import mcp_view
        hass.states.async_set("light.kitchen", "on", {})
        ready = asyncio.Event()
        real_track = mcp_view.async_track_state_change_event

        def _tracked(h, eids, cb):
            unsub = real_track(h, eids, cb)
            ready.set()
            return unsub

        with patch.object(mcp_view, "async_track_state_change_event", side_effect=_tracked):
            task = asyncio.create_task(_call(
                "watch_entity", {"entity_id": "light.kitchen", "until_state": "off", "timeout": 5}, _access_key(), hass))
            await asyncio.wait_for(ready.wait(), timeout=2)
            hass.states.async_set("light.kitchen", "on", {"brightness": 10})
            await asyncio.sleep(0)
            assert not task.done()
            hass.states.async_set("light.kitchen", "off", {})
            content, _, _ = await task
        body = _json(content)
        assert body["reached"] is True and body["changed"] is True and body["state"] == "off"

    async def test_unreached_state_reports_the_current_one(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        content, _, _ = await _call(
            "watch_entity", {"entity_id": "light.kitchen", "until_state": "off", "timeout": 1}, _access_key(), hass)
        body = _json(content)
        assert body["reached"] is False and body["state"] == "on"

    async def test_scope_lost_during_the_wait_is_not_disclosed(self, hass):
        from custom_components.phoenix_mcp import mcp_view
        from custom_components.phoenix_mcp.policy_engine import Permission
        hass.states.async_set("light.kitchen", "on", {})
        calls = iter([Permission.READ, Permission.NO_ACCESS])
        with patch.object(mcp_view, "resolve", side_effect=lambda *a: next(calls)):
            content, outcome, _ = await _call(
                "watch_entity", {"entity_id": "light.kitchen", "timeout": 1}, _access_key(), hass)
        assert outcome == "not_found"
        assert content["content"][0]["text"] == "Entity not found."
