"""Tests for HassMediaSearchAndPlay searching a player before playing."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.components.media_player import BrowseMedia, MediaClass, MediaPlayerEntityFeature, SearchMedia
from homeassistant.core import SupportsResponse
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.tool_common import RefusedBeforeWrite

TOOL = "media_player__HassMediaSearchAndPlay"
SEARCHABLE = int(MediaPlayerEntityFeature.SEARCH_MEDIA | MediaPlayerEntityFeature.PLAY_MEDIA)
PLAIN = int(MediaPlayerEntityFeature.PLAY_MEDIA)


def _access_key(state: str = "GREEN") -> AccessKeyRecord:
    tree = PermissionTree(domains={"media_player": PermissionNode(state=state)})
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(),
                           created_by="u", permissions=tree)


def _album(uri: str, title: str) -> BrowseMedia:
    return BrowseMedia(media_class=MediaClass.ALBUM, media_content_id=uri, media_content_type="music",
                       title=title, can_play=True, can_expand=True)


class Player:
    """Real states and stand-ins for the two services the tool calls."""

    def __init__(self, hass) -> None:
        self.hass, self.searches, self.plays, self.results = hass, [], [], [_album("library://album/1", "Rumours")]

    def add(self, name: str, features: int) -> str:
        entity_id = f"media_player.{name}"
        er.async_get(self.hass).async_get_or_create("media_player", "test", name, suggested_object_id=name,
                                                    original_name=name.title())
        self.hass.states.async_set(entity_id, "idle", {"friendly_name": name.title(), "supported_features": features})
        return entity_id

    async def search(self, call):
        self.searches.append(call)
        return {call.data["entity_id"]: SearchMedia(result=self.results)}

    async def play(self, call):
        self.plays.append(call)


@pytest.fixture
def player(hass):
    """Stand-in services over real states, with MESA switched off."""
    stub = Player(hass)
    data = MagicMock(mesa=None, mesa_setup_failed=False)
    data.store.get_settings.return_value.mesa_mode = "off"
    hass.data[DOMAIN] = data
    hass.services.async_register("media_player", "search_media", stub.search, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("media_player", "play_media", stub.play)
    return stub


async def test_plays_the_first_search_result(hass, player):
    entity_id = player.add("kitchen", SEARCHABLE)
    content, outcome, _ = await _call_tool(
        TOOL, {"name": "Kitchen", "search_query": "Fleetwood Mac - Rumours", "media_class": "album"},
        _access_key(), hass, MagicMock())
    assert outcome == "allowed", content
    assert player.searches[0].data["search_query"] == "Fleetwood Mac - Rumours"
    assert player.searches[0].data["media_filter_classes"] == ["album"]
    assert player.plays[0].data["media_content_id"] == "library://album/1"
    assert player.plays[0].data["entity_id"] == [entity_id]


async def test_no_results_tells_the_agent_how_to_search_and_plays_nothing(hass, player):
    player.add("kitchen", SEARCHABLE)
    player.results = []
    content, outcome, _ = await _call_tool(
        TOOL, {"name": "Kitchen", "search_query": "Rumours Fleetwood Mac"}, _access_key(), hass, MagicMock())
    assert outcome == "invalid_request"
    assert "Artist - Title" in content["content"][0]["text"]
    # Nothing can have changed, so an agent turn may retry instead of stopping.
    assert isinstance(content, RefusedBeforeWrite)
    assert player.plays == []


async def test_a_failed_search_plays_nothing(hass, player):
    player.add("kitchen", SEARCHABLE)
    from homeassistant.exceptions import HomeAssistantError

    async def _fails(call):
        raise HomeAssistantError("boom")

    hass.services.async_register("media_player", "search_media", _fails, supports_response=SupportsResponse.ONLY)
    content, outcome, _ = await _call_tool(
        TOOL, {"name": "Kitchen", "search_query": "Rumours"}, _access_key(), hass, MagicMock())
    assert outcome == "invalid_request"
    assert isinstance(content, RefusedBeforeWrite)
    assert "boom" not in json.dumps(content)
    assert player.plays == []


@pytest.mark.parametrize(("features", "query"), [(PLAIN, "Rumours"), (SEARCHABLE, "http://example.test/a.mp3")])
async def test_a_player_without_search_or_a_url_plays_directly(hass, player, features, query):
    player.add("kitchen", features)
    content, outcome, _ = await _call_tool(
        TOOL, {"name": "Kitchen", "search_query": query}, _access_key(), hass, MagicMock())
    assert outcome == "allowed", content
    assert player.searches == []
    assert player.plays[0].data["media_content_id"] == query
    assert player.plays[0].data["media_content_type"] == "music"


async def test_a_read_only_player_is_neither_searched_nor_played(hass, player):
    player.add("kitchen", SEARCHABLE)
    _, outcome, _ = await _call_tool(
        TOOL, {"name": "Kitchen", "search_query": "Rumours"}, _access_key("YELLOW"), hass, MagicMock())
    assert outcome == "denied"
    assert player.searches == [] and player.plays == []
