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
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tool_common import RefusedBeforeWrite

# These tests stand in for loaded components; see _module_loaded_components in conftest.
LOADED_COMPONENTS = ("media_player",)

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


class TestSource:
    """Choosing between a Music Assistant library and a connected streaming service."""

    def speaker(self, hass, player, *, platform: str = "music_assistant") -> str:
        entry = MockConfigEntry(domain=platform)
        entry.add_to_hass(hass)
        registry = er.async_get(hass)
        row = registry.async_get_or_create("media_player", platform, "den", suggested_object_id="den",
                                           original_name="Den", config_entry=entry)
        hass.states.async_set(row.entity_id, "idle", {"supported_features": SEARCHABLE})
        self.entry_id = entry.entry_id
        return row.entity_id

    def stub(self, hass, albums: list[str]) -> list:
        searches: list = []

        async def _search(call):
            searches.append(call)
            uris = [uri for uri in albums if not call.data["library_only"] or uri.startswith("library://")]
            return {"albums": [{"uri": uri} for uri in uris], "tracks": []}

        hass.services.async_register("music_assistant", "search", _search, supports_response=SupportsResponse.ONLY)
        return searches

    @pytest.mark.parametrize(("source", "library_only", "uri"), [
        ("library", True, "library://album/1"), ("streaming", False, "ytmusic--a://album/2"),
    ])
    async def test_plays_from_the_named_source(self, hass, player, source, library_only, uri):
        entity_id = self.speaker(hass, player)
        searches = self.stub(hass, ["library://album/1", "ytmusic--a://album/2"])
        content, outcome, _ = await _call_tool(
            TOOL, {"name": "Den", "search_query": "Rumours", "media_class": "album", "source": source},
            _access_key(), hass, MagicMock())
        assert outcome == "allowed", content
        assert searches[0].data["config_entry_id"] == self.entry_id
        assert searches[0].data["library_only"] is library_only
        assert searches[0].data["media_type"] == ["album"]
        assert player.searches == []
        assert player.plays[0].data["media_content_id"] == uri
        assert player.plays[0].data["entity_id"] == [entity_id]

    async def test_streaming_does_not_fall_back_to_the_library(self, hass, player):
        self.speaker(hass, player)
        self.stub(hass, ["library://album/1"])
        content, outcome, _ = await _call_tool(
            TOOL, {"name": "Den", "search_query": "Rumours", "source": "streaming"},
            _access_key(), hass, MagicMock())
        assert outcome == "invalid_request"
        assert isinstance(content, RefusedBeforeWrite)
        assert player.plays == []

    async def test_a_speaker_outside_music_assistant_is_refused(self, hass, player):
        self.speaker(hass, player, platform="cast")
        searches = self.stub(hass, ["library://album/1"])
        content, outcome, _ = await _call_tool(
            TOOL, {"name": "Den", "search_query": "Rumours", "source": "library"},
            _access_key(), hass, MagicMock())
        assert outcome == "invalid_request"
        assert "Music Assistant speaker" in content["content"][0]["text"]
        assert searches == [] and player.plays == []

    async def test_an_unknown_source_is_refused(self, hass, player):
        self.speaker(hass, player)
        searches = self.stub(hass, ["library://album/1"])
        content, outcome, _ = await _call_tool(
            TOOL, {"name": "Den", "search_query": "Rumours", "source": "everywhere"},
            _access_key(), hass, MagicMock())
        assert outcome == "invalid_request"
        assert searches == [] and player.plays == []

    async def test_a_read_only_speaker_is_neither_searched_nor_played(self, hass, player):
        self.speaker(hass, player)
        searches = self.stub(hass, ["library://album/1"])
        _, outcome, _ = await _call_tool(
            TOOL, {"name": "Den", "search_query": "Rumours", "source": "library"},
            _access_key("YELLOW"), hass, MagicMock())
        assert outcome == "denied"
        assert searches == [] and player.plays == []


class TestSharedName:
    """One speaker listed twice, once per integration, under the same name."""

    async def test_the_searchable_player_is_used(self, hass, player):
        wanted = player.add("kitchen", SEARCHABLE)
        twin = "media_player.kitchen_two"
        er.async_get(hass).async_get_or_create("media_player", "other", "two", suggested_object_id="kitchen_two",
                                               original_name="KITCHEN")
        hass.states.async_set(twin, "idle", {"supported_features": PLAIN})
        content, outcome, _ = await _call_tool(
            TOOL, {"name": "Kitchen", "search_query": "Rumours"}, _access_key(), hass, MagicMock())
        assert outcome == "allowed", content
        assert player.plays[0].data["entity_id"] == [wanted]

    @pytest.mark.parametrize("features", [PLAIN, SEARCHABLE])
    async def test_a_real_ambiguity_is_named_and_nothing_plays(self, hass, player, features):
        first = player.add("kitchen", features)
        twin = "media_player.kitchen_two"
        er.async_get(hass).async_get_or_create("media_player", "other", "two", suggested_object_id="kitchen_two",
                                               original_name="Kitchen")
        hass.states.async_set(twin, "idle", {"supported_features": features})
        content, outcome, _ = await _call_tool(
            TOOL, {"name": "Kitchen", "search_query": "Rumours"}, _access_key(), hass, MagicMock())
        text = content["content"][0]["text"]
        assert outcome == "invalid_request"
        assert first in text and twin in text
        assert isinstance(content, RefusedBeforeWrite)
        assert player.plays == [] and player.searches == []
