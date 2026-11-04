"""Tests that a call_service schema failure proves nothing ran."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.tool_common import RefusedBeforeWrite


async def test_a_rejected_call_is_refused_before_write_and_never_reaches_the_handler(hass):
    calls: list = []

    async def _handler(call):
        calls.append(call)

    er.async_get(hass).async_get_or_create("media_player", "test", "den", suggested_object_id="den")
    hass.states.async_set("media_player.den", "idle")
    hass.services.async_register(
        "media_player", "play_media", _handler,
        schema=vol.Schema({vol.Required("entity_id"): list, vol.Required("media_content_type"): str}),
    )
    key = AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                          permissions=PermissionTree(domains={"media_player": PermissionNode(state="GREEN")}))
    data = MagicMock(mesa=None, mesa_setup_failed=False)
    data.store.get_settings.return_value.mesa_mode = "off"
    content, outcome, _ = await _call_tool(
        "call_service",
        {"service": {"domain": "media_player", "name": "play_media"},
         "targets": [{"kind": "entity", "ids": ["media_player.den"]}]},
        key, hass, data)
    assert outcome == "invalid_request", content
    assert "media_content_type" in content["content"][0]["text"]
    assert isinstance(content, RefusedBeforeWrite)
    assert calls == []
