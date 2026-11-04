"""Tests for the Broadcast capability on every call_service path to an announcement."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool, _execute_call_service

SPEAKER = "media_player.kitchen_speaker"
SATELLITE = "assist_satellite.hall"
ANNOUNCEMENTS = [
    ("music_assistant", "play_announcement", SPEAKER, {"url": "http://example.test/a.mp3"}),
    ("assist_satellite", "announce", SATELLITE, {"message": "Dinner"}),
    ("media_player", "play_media", SPEAKER, {"media_content_id": "http://example.test/a.mp3",
                                             "media_content_type": "music", "announce": True}),
]


def _access_key(**caps) -> AccessKeyRecord:
    tree = PermissionTree(domains={"media_player": PermissionNode(state="GREEN"),
                                   "assist_satellite": PermissionNode(state="GREEN")})
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(),
                           created_by="u", permissions=tree, **{"cap_search": "allow", **caps})


async def _call(name: str, domain: str, service: str, data: dict, entity_id: str, access_key, hass):
    args = {"service": {"domain": domain, "name": service, "data": data},
            "targets": [{"kind": "entity", "ids": [entity_id]}]}
    return await _call_tool(name, args, access_key, hass, MagicMock())


@pytest.fixture
def calls(hass):
    """Real states and registered stand-ins for the announcement services."""
    recorded: list = []

    async def _handler(call):
        recorded.append(call)

    registry = er.async_get(hass)
    for entity_id in (SPEAKER, SATELLITE):
        domain, object_id = entity_id.split(".", 1)
        registry.async_get_or_create(domain, "test", object_id, suggested_object_id=object_id)
        hass.states.async_set(entity_id, "idle")
    for domain, service in {(domain, service) for domain, service, _, _ in ANNOUNCEMENTS}:
        hass.services.async_register(domain, service, _handler)
    return recorded


@pytest.mark.parametrize(("domain", "service", "entity_id", "data"), ANNOUNCEMENTS)
async def test_write_alone_is_refused(hass, calls, domain, service, entity_id, data):
    content, outcome, _ = await _call("call_service", domain, service, data, entity_id,
                                      _access_key(cap_broadcast="deny"), hass)
    assert outcome == "denied", content
    assert calls == []


@pytest.mark.parametrize(("domain", "service", "entity_id", "data"), ANNOUNCEMENTS)
async def test_broadcast_lets_it_run(hass, calls, domain, service, entity_id, data):
    content, outcome, _ = await _call("call_service", domain, service, data, entity_id,
                                      _access_key(cap_broadcast="allow"), hass)
    assert outcome == "allowed", content
    assert [call.data["entity_id"] for call in calls] == [[entity_id]]


@pytest.mark.parametrize(("domain", "service", "entity_id", "data"), ANNOUNCEMENTS)
async def test_the_executor_refuses_on_its_own(hass, calls, domain, service, entity_id, data):
    # Reached directly by an approved call, after the capability may have been revoked.
    args = {"domain": domain, "service": service, "service_data": data, "entity_id": [entity_id]}
    _, outcome, _ = await _execute_call_service(args, _access_key(cap_broadcast="deny"), hass, MagicMock())
    assert outcome == "denied"
    assert calls == []


async def test_ordinary_playback_needs_only_write(hass, calls):
    data = {"media_content_id": "Rumours", "media_content_type": "album"}
    content, outcome, _ = await _call("call_service", "media_player", "play_media", data, SPEAKER,
                                      _access_key(cap_broadcast="deny"), hass)
    assert outcome == "allowed", content
    assert len(calls) == 1


@pytest.mark.parametrize(("cap", "predicted"), [("deny", "denied"), ("allow", "allowed")])
@pytest.mark.parametrize(("domain", "service", "entity_id", "data"), ANNOUNCEMENTS)
async def test_dry_run_predicts_the_gate(hass, calls, domain, service, entity_id, data, cap, predicted):
    content, _, _ = await _call("dry_run_service", domain, service, data, entity_id,
                                _access_key(cap_broadcast=cap), hass)
    assert json.loads(content["content"][0]["text"])["predicted_outcome"] == predicted
    assert calls == []


async def test_find_available_actions_says_what_is_missing(hass, calls):
    content, _, _ = await _call_tool("find_available_actions", {"entity_id": SATELLITE},
                                     _access_key(cap_broadcast="deny"), hass, MagicMock())
    row = next(a for a in json.loads(content["content"][0]["text"])["actions"]
               if a["service"] == "assist_satellite.announce")
    assert row["available"] is False
    assert row["reason"] == "requires broadcast capability"
