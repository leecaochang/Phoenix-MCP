"""tts.speak names its speakers in a data field, and each speaker meets every gate a target does.

Runs the real tts component with a real entity, so the call reaches Home Assistant's own schema
and its own media_player.play_media announcement.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.tts import DATA_COMPONENT, TextToSpeechEntity
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_set_service_schema
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import async_mock_service

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.service_targets import merge_speech_players
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool


class _Engine(TextToSpeechEntity):
    """The least a real tts entity needs to be addressed."""

    _attr_name = "Engine"
    _attr_unique_id = "engine"
    _attr_default_language = "en"
    _attr_supported_languages = ["en"]


async def _speech(hass) -> list:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "tts", {})
    await hass.data[DATA_COMPONENT].async_add_entities([_Engine()])
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for object_id in ("kitchen", "hall", "vault"):
        registry.async_get_or_create("media_player", "test", object_id, suggested_object_id=object_id)
        hass.states.async_set(f"media_player.{object_id}", "idle")
    return async_mock_service(hass, "media_player", "play_media")


def _data(hass, mesa_mode: str = "off") -> tuple[PhoenixData, list]:
    pending: list = []
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    store.get_settings.return_value = GlobalSettings(mesa_mode=mesa_mode)
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=None)
    hass.data[DOMAIN] = data
    return data, pending


ENGINE = "tts.engine"


def _key(entities: dict[str, str] | None = None, **fields) -> AccessKeyRecord:
    fields.setdefault("cap_broadcast", "allow")
    fields.setdefault("cap_search", "allow")
    granted = {ENGINE: "GREEN", "media_player.kitchen": "GREEN", "media_player.hall": "GREEN", **(entities or {})}
    return AccessKeyRecord(
        id="k", name="K", access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(entities={e: PermissionNode(state=s) for e, s in granted.items()}),
        **fields,
    )


def _speak(players: object = "media_player.kitchen", engine: str | None = ENGINE) -> dict:
    data = {"message": "dinner", "media_player_entity_id": players}
    return {
        "service": {"domain": "tts", "name": "speak", "data": data},
        **({"targets": [{"kind": "entity", "ids": [engine]}]} if engine else {}),
    }


def _set_profile(data: PhoenixData, entity_id: str, mode: str) -> None:
    data.mesa.store.set(entity_id, SemanticProfile.from_dict(
        entity_id, {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}},
        default_origin=MetadataOrigin.USER,
    ))


async def test_a_key_with_every_grant_speaks_on_the_named_players(hass):
    plays = await _speech(hass)
    data, _ = _data(hass)
    speech: list = []
    hass.bus.async_listen(
        EVENT_CALL_SERVICE,
        lambda event: speech.append(event.data["service_data"]) if event.data["domain"] == "tts" else None,
    )
    result, outcome, _ = await _call_tool("call_service", _speak(["media_player.kitchen", "media_player.hall"]), _key(), hass, data)
    await hass.async_block_till_done()
    assert outcome == "allowed", result
    assert speech[0]["entity_id"] == [ENGINE]
    assert len(plays) == 1
    assert sorted(plays[0].data["entity_id"]) == ["media_player.hall", "media_player.kitchen"]
    assert plays[0].data["announce"] is True


async def test_a_single_player_may_be_a_plain_string(hass):
    plays = await _speech(hass)
    data, _ = _data(hass)
    _, outcome, _ = await _call_tool("call_service", _speak("media_player.kitchen"), _key(), hass, data)
    assert outcome == "allowed"
    assert plays[0].data["entity_id"] == ["media_player.kitchen"]


async def test_it_is_an_announcement(hass):
    plays = await _speech(hass)
    data, pending = _data(hass)
    denied = await _call_tool("call_service", _speak(), _key(cap_broadcast="deny"), hass, data)
    assert denied[1] == "denied"
    queued = await _call_tool("call_service", _speak(), _key(cap_broadcast="confirm"), hass, data)
    assert queued[1] == "pending_approval"
    assert plays == []


@pytest.mark.parametrize("state", ["RED", "GREY", "YELLOW"])
async def test_every_named_player_needs_write(hass, state):
    plays = await _speech(hass)
    data, _ = _data(hass)
    key = _key({"media_player.vault": state})
    result, outcome, _ = await _call_tool("call_service", _speak(["media_player.kitchen", "media_player.vault"]), key, hass, data)
    assert outcome == "denied", result
    assert plays == []


async def test_a_hidden_player_and_a_missing_one_read_alike(hass):
    await _speech(hass)
    data, _ = _data(hass)
    hidden = await _call_tool("call_service", _speak("media_player.vault"), _key(), hass, data)
    missing = await _call_tool("call_service", _speak("media_player.nothing"), _key(), hass, data)
    assert hidden[0] == missing[0]


async def test_the_engine_needs_write_too(hass):
    plays = await _speech(hass)
    data, _ = _data(hass)
    result, outcome, _ = await _call_tool("call_service", _speak(), _key({ENGINE: "YELLOW"}), hass, data)
    assert outcome == "denied", result
    assert plays == []


@pytest.mark.parametrize("engine", [None, "media_player.kitchen"])
async def test_no_engine_refuses(hass, engine):
    plays = await _speech(hass)
    data, _ = _data(hass)
    _, outcome, _ = await _call_tool("call_service", _speak(engine=engine), _key(), hass, data)
    assert outcome in ("denied", "invalid_request")
    assert plays == []


@pytest.mark.parametrize("players", ["all", "none", ["all"], [], [1], {"entity_id": "media_player.kitchen"},
                                     "light.kitchen", "MEDIA_PLAYER.kitchen", ["media_player.kitchen", "all"]])
async def test_a_player_field_that_names_no_player_is_refused(hass, players):
    plays = await _speech(hass)
    data, _ = _data(hass)
    result, outcome, _ = await _call_tool("call_service", _speak(players), _key(), hass, data)
    assert outcome == "invalid_request", result
    assert plays == []


async def test_a_group_with_a_red_member_cannot_be_a_speaker(hass):
    plays = await _speech(hass)
    hass.states.async_set("media_player.zone", "idle", {"group_entities": ["media_player.vault"]})
    er.async_get(hass).async_get_or_create("media_player", "test", "zone", suggested_object_id="zone")
    data, _ = _data(hass)
    key = _key({"media_player.zone": "GREEN", "media_player.vault": "RED"})
    result, outcome, _ = await _call_tool("call_service", _speak("media_player.zone"), key, hass, data)
    assert outcome == "denied", result
    assert plays == []


async def test_a_prohibited_player_refuses_the_whole_call(hass):
    plays = await _speech(hass)
    data, _ = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "media_player.hall", "prohibited")
    result, outcome, _ = await _call_tool("call_service", _speak(["media_player.kitchen", "media_player.hall"]), _key(), hass, data)
    assert outcome == "denied", result
    assert plays == []


async def test_a_player_needing_confirmation_queues_the_exact_call(hass):
    plays = await _speech(hass)
    data, pending = _data(hass, "enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    _set_profile(data, "media_player.hall", "confirm")
    key = _key()
    result, outcome, _ = await _call_tool("call_service", _speak(["media_player.kitchen", "media_player.hall"]), key, hass, data)
    assert outcome == "pending_approval", result
    assert plays == []
    await async_execute_approved_tool(pending[0]["tool_name"], pending[0]["args"], key, hass, data)
    assert sorted(plays[0].data["entity_id"]) == ["media_player.hall", "media_player.kitchen"]


async def test_a_capability_approval_replays_and_binds_the_players(hass):
    plays = await _speech(hass)
    data, pending = _data(hass)
    key = _key(cap_broadcast="confirm")
    await _call_tool("call_service", _speak("media_player.kitchen"), key, hass, data)
    args = pending[0]["args"]
    assert sorted(args["_phoenix_approval_targets"]["entity_ids"]) == ["media_player.kitchen", ENGINE]
    assert "media_player_entity_id" not in args["service_data"]
    await async_execute_approved_tool("call_service", args, key, hass, data)
    assert plays[0].data["entity_id"] == ["media_player.kitchen"]


async def test_an_approval_stops_at_a_player_since_refused(hass):
    plays = await _speech(hass)
    data, pending = _data(hass)
    key = _key(cap_broadcast="confirm")
    await _call_tool("call_service", _speak("media_player.kitchen"), key, hass, data)
    key.permissions.entities["media_player.kitchen"] = PermissionNode(state="RED")
    result = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert result[1] == "denied"
    assert plays == []


async def test_the_preview_predicts_the_call(hass):
    await _speech(hass)
    data, _ = _data(hass)
    allowed, _, _ = await _call_tool("dry_run_service", _speak(), _key(), hass, data)
    body = json.loads(allowed["content"][0]["text"])
    assert body["predicted_outcome"] == "allowed"
    assert sorted(body["resolved_entities"]) == sorted([ENGINE, "media_player.kitchen"])
    refused, _, _ = await _call_tool("dry_run_service", _speak("media_player.vault"), _key(), hass, data)
    assert json.loads(refused["content"][0]["text"])["predicted_outcome"] == "denied"
    gated, _, _ = await _call_tool("dry_run_service", _speak(), _key(cap_broadcast="confirm"), hass, data)
    assert json.loads(gated["content"][0]["text"])["predicted_outcome"] == "pending_approval"


async def test_the_listing_offers_it_as_it_runs(hass):
    await _speech(hass)
    data, _ = _data(hass)

    async def row(key: AccessKeyRecord) -> dict:
        result, _, _ = await _call_tool("find_available_actions", {"entity_id": ENGINE}, key, hass, data)
        return next(item for item in json.loads(result["content"][0]["text"])["actions"]
                    if item["service"] == "tts.speak")

    assert (await row(_key()))["available"] is True
    denied = await row(_key(cap_broadcast="deny"))
    assert denied["available"] is False and denied["reason"] == "requires broadcast capability"


async def test_the_field_stays_refused_on_every_other_service(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "light", {})
    light = er.async_get(hass).async_get_or_create("light", "test", "x", suggested_object_id="x").entity_id
    hass.states.async_set(light, "on")
    data, _ = _data(hass)
    args = {
        "service": {"domain": "light", "name": "turn_on", "data": {"media_player_entity_id": "media_player.x"}},
        "targets": [{"kind": "entity", "ids": [light]}],
    }
    key = AccessKeyRecord(
        id="k", name="K", access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(entities={light: PermissionNode(state="GREEN")}),
    )
    assert (await _call_tool("call_service", args, key, hass, data))[1] == "invalid_request"


def test_the_merge_leaves_every_other_call_alone():
    args = {"entity_id": ["light.x"], "service_data": {"media_player_entity_id": "media_player.x"}}
    assert merge_speech_players(args, "light", "turn_on") == (args, None)
    assert merge_speech_players({"service_data": None}, "tts", "speak") == ({"service_data": None}, None)


@pytest.mark.parametrize(("broadcast", "outcome", "runs"), [("deny", "denied", 0), ("allow", "allowed", 1)])
async def test_a_legacy_say_service_is_an_announcement_too(hass, broadcast, outcome, runs):
    """Home Assistant plays it with announce, and its description targets the player."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "tts", {})
    ran: list = []

    async def handler(call) -> None:
        ran.append(call.data)

    hass.services.async_register("tts", "engine_say", handler)
    async_set_service_schema(hass, "tts", "engine_say", {
        "fields": {"entity_id": {"required": True, "selector": {"entity": {"domain": "media_player"}}}},
    })
    player = er.async_get(hass).async_get_or_create("media_player", "test", "x", suggested_object_id="x").entity_id
    hass.states.async_set(player, "idle")
    data, _ = _data(hass)
    key = _key({player: "GREEN"}, cap_broadcast=broadcast)
    result = await _call_tool("call_service", {
        "service": {"domain": "tts", "name": "engine_say", "data": {"message": "x"}},
        "targets": [{"kind": "entity", "ids": [player]}],
    }, key, hass, data)
    assert result[1] == outcome, result
    assert len(ran) == runs
