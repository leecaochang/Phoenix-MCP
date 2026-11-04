"""Tests for Music Assistant library reads through call_service and dry_run_service."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.tool_common import RefusedBeforeWrite


def _access_key(domains: dict[str, str] | None = None, **caps) -> AccessKeyRecord:
    tree = PermissionTree(domains={
        domain: PermissionNode(state=state)
        for domain, state in (domains or {"media_player": "YELLOW", "sensor": "GREEN"}).items()
    })
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(),
        created_by="u", permissions=tree, **{"cap_service_response": "allow", "cap_search": "allow", **caps},
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


def _text(content: dict) -> str:
    return content["content"][0]["text"]


async def _call(name: str, service: str, data: dict, access_key: AccessKeyRecord, hass, **targets):
    domain, _, action = service.partition(".")
    args = {"service": {"domain": domain, "name": action, "data": data}, **targets}
    return await _call_tool(name, args, access_key, hass, MagicMock())


@pytest.fixture
def ma_entries(hass):
    """Real config entries and registry rows; only the LOADED state is set by hand.

    The music_assistant integration cannot set up here (its client library is
    not a Phoenix requirement), so each entry is marked loaded and put back to
    not loaded before teardown would try to unload it.
    """
    created: list[MockConfigEntry] = []

    def add(domain: str = "music_assistant", entity_ids: tuple[str, ...] = ("media_player.kitchen_speaker",),
            state: ConfigEntryState = ConfigEntryState.LOADED) -> MockConfigEntry:
        entry = MockConfigEntry(domain=domain, title=domain)
        entry.add_to_hass(hass)
        entry.mock_state(hass, state)
        registry = er.async_get(hass)
        for entity_id in entity_ids:
            entity_domain, object_id = entity_id.split(".", 1)
            registry.async_get_or_create(
                entity_domain, domain, f"unique-{object_id}", config_entry=entry, suggested_object_id=object_id,
            )
        created.append(entry)
        return entry

    yield add
    for entry in created:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


@pytest.fixture
def library_calls(hass):
    """Register stand-ins for the two response-only Music Assistant services."""
    calls: list = []

    async def _handler(call):
        calls.append(call)
        return {"albums": [{"name": "Rumours", "uri": "library://album/1", "player": "light.hidden_lamp"}]}

    for service in ("search", "get_library"):
        hass.services.async_register("music_assistant", service, _handler, supports_response=SupportsResponse.ONLY)
    return calls


class TestLibraryRead:
    @pytest.mark.parametrize(("service", "data"), [
        ("music_assistant.search", {"name": "Rumours", "artist": "Fleetwood Mac", "media_type": ["album"]}),
        ("music_assistant.get_library", {"media_type": "album", "search": "Rumours"}),
    ])
    async def test_readable_entry_is_searched_and_redacted(self, hass, ma_entries, library_calls, service, data):
        entry = ma_entries()
        content, outcome, resource = await _call(
            "call_service", service, {"config_entry_id": entry.entry_id, **data}, _access_key(), hass)
        assert outcome == "allowed", _text(content)
        assert resource == f"service:{service.replace('.', '/')}"
        assert [call.data for call in library_calls] == [{"config_entry_id": entry.entry_id, **data}]
        album = _json(content)["service_response"]["albums"][0]
        assert album["uri"] == "library://album/1"
        # The response passes through the service-response redaction.
        assert album["player"] == "<redacted>"

    async def test_a_lone_readable_entry_fills_an_omitted_entry_id(self, hass, ma_entries, library_calls):
        entry = ma_entries()
        content, outcome, _ = await _call(
            "call_service", "music_assistant.search", {"name": "Rumours"}, _access_key(), hass)
        assert outcome == "allowed", _text(content)
        assert library_calls[0].data["config_entry_id"] == entry.entry_id

    async def test_several_readable_entries_need_an_entry_id(self, hass, ma_entries, library_calls):
        ma_entries()
        ma_entries(entity_ids=("media_player.bedroom_speaker",))
        content, outcome, _ = await _call(
            "call_service", "music_assistant.search", {"name": "Rumours"}, _access_key(), hass)
        assert outcome == "invalid_request"
        assert "config_entry_id" in _text(content)
        assert library_calls == []

    async def test_without_the_response_capability_nothing_is_dispatched(self, hass, ma_entries, library_calls):
        entry = ma_entries()
        content, outcome, _ = await _call(
            "call_service", "music_assistant.search", {"config_entry_id": entry.entry_id, "name": "Rumours"},
            _access_key(cap_service_response="deny"), hass)
        assert outcome == "denied"
        assert library_calls == []

    async def test_unreadable_unknown_foreign_and_unloaded_entries_answer_alike(self, hass, ma_entries, library_calls):
        readable_elsewhere = ma_entries(domain="sensor_maker", entity_ids=("sensor.outdoor_temperature",))
        unloaded = ma_entries(entity_ids=("media_player.garage_speaker",), state=ConfigEntryState.NOT_LOADED)
        hidden = ma_entries(entity_ids=("media_player.office_speaker",))
        bodies = set()
        for entry_id, access_key in (
            (hidden.entry_id, _access_key({"sensor": "GREEN"})),
            ("01NOSUCHENTRY", _access_key()),
            (readable_elsewhere.entry_id, _access_key()),
            (unloaded.entry_id, _access_key()),
            (["not", "a", "string"], _access_key()),
        ):
            content, outcome, _ = await _call(
                "call_service", "music_assistant.search", {"config_entry_id": entry_id, "name": "Rumours"},
                access_key, hass)
            assert outcome == "denied", entry_id
            bodies.add(_text(content))
        assert bodies == {"Forbidden."}
        assert library_calls == []

    async def test_an_entity_target_is_refused(self, hass, ma_entries, library_calls):
        entry = ma_entries()
        content, outcome, _ = await _call(
            "call_service", "music_assistant.search", {"config_entry_id": entry.entry_id, "name": "Rumours"},
            _access_key(), hass, targets=[{"kind": "entity", "ids": ["media_player.kitchen_speaker"]}])
        assert outcome == "invalid_request"
        assert "takes config_entry_id" in _text(content)
        # Nothing can have changed, so an agent turn may correct the call.
        assert isinstance(content, RefusedBeforeWrite)
        assert library_calls == []

    async def test_other_secondary_targets_stay_refused(self, hass, ma_entries, library_calls):
        entry = ma_entries()
        content, outcome, _ = await _call(
            "call_service", "music_assistant.search",
            {"config_entry_id": entry.entry_id, "name": "Rumours", "entity_id": "media_player.kitchen_speaker"},
            _access_key(), hass)
        assert outcome == "invalid_request"
        assert "entity_id" in _text(content)
        assert library_calls == []

    async def test_config_entry_id_stays_refused_on_other_services(self, hass, ma_entries):
        entry = ma_entries()
        calls: list = []

        async def _handler(call):
            calls.append(call)

        hass.services.async_register("music_assistant", "play_media", _handler)
        content, outcome, _ = await _call(
            "call_service", "music_assistant.play_media",
            {"config_entry_id": entry.entry_id, "media_id": "Rumours"}, _access_key({"media_player": "GREEN"}), hass,
            targets=[{"kind": "entity", "ids": ["media_player.kitchen_speaker"]}])
        assert outcome == "invalid_request"
        assert "config_entry_id" in _text(content)
        assert calls == []


class TestLibraryDryRun:
    async def test_predicts_the_call(self, hass, ma_entries, library_calls):
        entry = ma_entries()
        data = {"config_entry_id": entry.entry_id, "name": "Rumours"}
        content, _, _ = await _call("dry_run_service", "music_assistant.search", data, _access_key(), hass)
        assert _json(content)["predicted_outcome"] == "allowed"
        content, _, _ = await _call(
            "dry_run_service", "music_assistant.search", data, _access_key(cap_service_response="deny"), hass)
        assert _json(content)["predicted_outcome"] == "denied"
        content, _, _ = await _call(
            "dry_run_service", "music_assistant.search", data, _access_key({"sensor": "GREEN"}), hass)
        assert _json(content)["predicted_outcome"] == "denied"
        assert library_calls == []
