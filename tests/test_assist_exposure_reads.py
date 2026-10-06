"""Single-entity reads honor the pass-through Assist-exposure filter.

A pass-through access key with use_assist_exposure reads only entities exposed to
Assist. Every tool that names one entity must refuse an unexposed one with the
reply it gives for an entity that does not exist, so no read is an existence
oracle. Each tool is checked three ways: an exposed entity is read, the same
unexposed entity is read once the filter is off (so the refusal is the filter and
not a broken fixture), and the filtered refusal is wire-identical to a ghost's.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant.components.calendar import CalendarEntity, CalendarEvent, DATA_COMPONENT
from homeassistant.components.homeassistant.exposed_entities import (
    DATA_EXPOSED_ENTITIES,
    async_expose_entity,
)
from homeassistant.core import State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.const import MESA_MODE_OFF
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.policy_engine import Permission, resolve_read

SHOWN, HIDDEN, GHOST = "light.kitchen", "light.porch", "light.ghost"


def _key(*, filtered: bool = True) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(), pass_through=True, use_assist_exposure=filtered,
        cap_search="allow", cap_config_read="allow", cap_log_read="allow", cap_camera_read="allow", cap_diagnostics="allow",
    )


def _data(mesa: object | None = None) -> PhoenixData:
    store = MagicMock()
    store.get_settings.return_value = SimpleNamespace(mesa_mode=MESA_MODE_OFF)
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=mesa)


def _expose(hass, shown: str, hidden: str) -> None:
    async_expose_entity(hass, "conversation", shown, True)
    async_expose_entity(hass, "conversation", hidden, False)


def _body(result: tuple) -> dict:
    return json.loads(result[0]["content"][0]["text"])


@pytest.fixture
async def home(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    for entity_id in (SHOWN, HIDDEN):
        hass.states.async_set(entity_id, "on", {"brightness": 10, "friendly_name": entity_id})
    _expose(hass, SHOWN, HIDDEN)
    return hass


async def _assert_gated(hass, tool, args_for, data, *, shown=SHOWN, hidden=HIDDEN, ghost=GHOST) -> None:
    """Exposed is read, unfiltered hidden is read, filtered hidden replies exactly as a ghost."""
    allowed = await _call_tool(tool, args_for(shown), _key(), hass, data)
    assert allowed[1] == "allowed", allowed
    unfiltered = await _call_tool(tool, args_for(hidden), _key(filtered=False), hass, data)
    assert unfiltered[1] == "allowed", unfiltered
    refused = await _call_tool(tool, args_for(hidden), _key(), hass, data)
    absent = await _call_tool(tool, args_for(ghost), _key(), hass, data)
    assert refused[0].get("isError") is True, refused
    assert refused[0] == absent[0]


async def test_resolve_read_canonicalizes_and_never_records_a_ghost(home):
    exposed = home.data[DATA_EXPOSED_ENTITIES]
    # With new entities exposed by default, an uncanonical spelling of a hidden
    # light would read as a new, default-exposed light if it reached HA as typed.
    exposed.async_set_expose_new_entities("conversation", True)
    entry = er.async_get(home).async_get_or_create("light", "test", "hall", suggested_object_id="hall")
    home.states.async_set(entry.entity_id, "on")
    async_expose_entity(home, "conversation", entry.entity_id, False)
    key = _key()
    assert resolve_read(entry.entity_id.upper(), key, home) == Permission.NO_ACCESS
    assert resolve_read(HIDDEN.upper(), key, home) == Permission.NO_ACCESS
    assert resolve_read(SHOWN.upper(), key, home) == Permission.WRITE
    assert resolve_read(GHOST, key, home) == Permission.NOT_FOUND
    assert not {GHOST, HIDDEN.upper(), entry.entity_id.upper()} & set(exposed.entities)


async def test_get_history(home):
    instance = SimpleNamespace(keep_days=10)

    async def run(fn):
        return fn()
    instance.async_add_executor_job = run

    def changes(_hass, _after, _before, entity_id, *_args):
        return {entity_id: [State(entity_id, "on")]}

    with (patch("homeassistant.components.recorder.get_instance", return_value=instance),
          patch("homeassistant.components.recorder.history.state_changes_during_period", side_effect=changes)):
        await _assert_gated(home, "get_history", lambda eid: {"entity_id": eid}, _data())


async def test_get_statistics(home):
    instance = SimpleNamespace(keep_days=10)

    async def run(fn):
        return fn()
    instance.async_add_executor_job = run
    with (patch("homeassistant.components.recorder.get_instance", return_value=instance),
          patch("homeassistant.components.recorder.statistics.statistics_during_period", return_value={})):
        await _assert_gated(home, "get_statistics", lambda eid: {"entity_id": eid}, _data())


class _Calendar(CalendarEntity):
    """A read-only calendar holding one event."""

    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        start = dt_util.now()
        self.events = [CalendarEvent(start=start, end=start + timedelta(hours=1), summary="Dentist", uid="u1")]

    @property
    def event(self):
        return self.events[0]

    async def async_get_events(self, hass, start_date, end_date):
        return self.events


@pytest.fixture
def calendars(home):
    shown, hidden = "calendar.family", "calendar.private"
    entities = {eid: _Calendar(eid) for eid in (shown, hidden)}
    for eid in entities:
        home.states.async_set(eid, "off")
    _expose(home, shown, hidden)
    home.data[DATA_COMPONENT] = SimpleNamespace(get_entity=entities.get)
    return shown, hidden


async def test_get_calendar_events(home, calendars):
    shown, hidden = calendars
    await _assert_gated(home, "get_calendar_events", lambda eid: {"entity_id": eid}, _data(),
                        shown=shown, hidden=hidden, ghost="calendar.ghost")


async def test_get_calendar_write(home, calendars):
    shown, hidden = calendars
    await _assert_gated(home, "get_calendar_write", lambda eid: {"entity_id": eid}, _data(),
                        shown=shown, hidden=hidden, ghost="calendar.ghost")


async def test_get_camera_image(home):
    from homeassistant.components import camera

    for eid in ("camera.front", "camera.side"):
        home.states.async_set(eid, "idle")
    _expose(home, "camera.front", "camera.side")
    image = SimpleNamespace(content=b"\xff\xd8jpeg", content_type="image/jpeg")
    with patch.object(camera, "async_get_image", AsyncMock(return_value=image)):
        await _assert_gated(home, "get_camera_image", lambda eid: {"entity_id": eid}, _data(),
                            shown="camera.front", hidden="camera.side", ghost="camera.ghost")


async def test_find_available_actions(home):
    await _assert_gated(home, "find_available_actions", lambda eid: {"entity_id": eid}, _data())


async def test_describe_entity(home):
    await _assert_gated(home, "describe_entity", lambda eid: {"entity_id": eid}, _data())


async def test_describe_entity_registry_only_entity(home):
    registry = er.async_get(home)
    shown = registry.async_get_or_create("light", "test", "attic", suggested_object_id="attic").entity_id
    hidden = registry.async_get_or_create("light", "test", "cellar", suggested_object_id="cellar").entity_id
    _expose(home, shown, hidden)
    await _assert_gated(home, "describe_entity", lambda eid: {"entity_id": eid}, _data(),
                        shown=shown, hidden=hidden)


async def test_whatif(home):
    await _assert_gated(home, "whatif", lambda eid: {"entity_id": eid, "hypothetical_state": "off"}, _data())


async def test_compare_entities_either_side(home):
    home.states.async_set("light.hall", "on", {"brightness": 20})
    async_expose_entity(home, "conversation", "light.hall", True)
    await _assert_gated(home, "compare_entities", lambda eid: {"entity_id": eid, "compare_to": "light.hall"}, _data())
    await _assert_gated(home, "compare_entities", lambda eid: {"entity_id": "light.hall", "compare_to": eid}, _data())


async def test_compare_states_drops_an_unexposed_entity_like_a_missing_one(home):
    seen: list[list[str]] = []

    async def at(_hass, _when, entity_ids):
        seen.append(list(entity_ids))
        return {eid: [State(eid, "on")] for eid in entity_ids}

    args = {"entity_ids": [SHOWN, HIDDEN, GHOST], "t1": (utcnow() - timedelta(hours=1)).isoformat()}
    with patch("custom_components.phoenix_mcp.tools.discovery._history_states_at", side_effect=at):
        filtered = await _call_tool("compare_states", args, _key(), home, _data())
        unfiltered = await _call_tool("compare_states", args, _key(filtered=False), home, _data())
    assert [row["entity_id"] for row in _body(filtered)["comparisons"]] == [SHOWN]
    assert [row["entity_id"] for row in _body(unfiltered)["comparisons"]] == [SHOWN, HIDDEN]
    assert seen[:2] == [[SHOWN], [SHOWN]]


async def test_watch_entity(home):
    await _assert_gated(home, "watch_entity", lambda eid: {"entity_id": eid, "until_state": "on", "timeout": 1}, _data())


async def test_watch_entity_refuses_before_subscribing(home):
    """A ghost refuses at once, so waiting out the timeout on a hidden entity would time an oracle."""
    real_track = mcp_view.async_track_state_change_event
    with patch.object(mcp_view, "async_track_state_change_event", wraps=real_track) as track:
        result, outcome, _ = await asyncio.wait_for(_call_tool(
            "watch_entity", {"entity_id": HIDDEN, "until_state": "off", "timeout": 5}, _key(), home, _data()), timeout=2)
    assert outcome == "not_found"
    assert result["content"][0]["text"] == "Entity not found."
    track.assert_not_called()


async def test_watch_entity_rechecks_exposure_after_the_wait(home):
    ready = asyncio.Event()
    real_track = mcp_view.async_track_state_change_event

    def tracked(hass, entity_ids, action):
        unsub = real_track(hass, entity_ids, action)
        ready.set()
        return unsub

    with patch.object(mcp_view, "async_track_state_change_event", side_effect=tracked):
        task = asyncio.create_task(_call_tool(
            "watch_entity", {"entity_id": SHOWN, "until_state": "off", "timeout": 5}, _key(), home, _data()))
        await asyncio.wait_for(ready.wait(), timeout=2)
        async_expose_entity(home, "conversation", SHOWN, False)
        home.states.async_set(SHOWN, "off")
        result, outcome, _ = await task
    assert outcome == "not_found"
    assert result["content"][0]["text"] == "Entity not found."


@pytest.fixture
def related(home):
    """Two registered lights in one area and an exposed automation that targets both."""
    registry = er.async_get(home)
    area = ar.async_get(home).async_create("Kitchen")
    lights = []
    for object_id in ("counter", "pantry"):
        entry = registry.async_get_or_create("light", "test", object_id, suggested_object_id=object_id)
        registry.async_update_entity(entry.entity_id, area_id=area.id)
        home.states.async_set(entry.entity_id, "on")
        lights.append(entry.entity_id)
    shown, hidden = lights
    automation = registry.async_get_or_create("automation", "test", "auto1", suggested_object_id="evening")
    home.states.async_set(automation.entity_id, "on")
    _expose(home, shown, hidden)
    async_expose_entity(home, "conversation", automation.entity_id, True)
    with open(os.path.join(home.config.config_dir, "automations.yaml"), "w", encoding="utf-8") as handle:
        yaml.safe_dump([{
            "id": "auto1", "alias": "Evening",
            "trigger": [{"platform": "state", "entity_id": shown}],
            "action": [{"service": "light.turn_on", "target": {"entity_id": [shown, hidden]}}],
        }], handle)
    return area, automation.entity_id, shown, hidden


async def test_get_relationships_entity_selector(home, related):
    _, _, shown, hidden = related
    await _assert_gated(home, "get_relationships", lambda eid: {"scope": {"kind": "entity", "id": eid}}, _data(),
                        shown=shown, hidden=hidden)


async def test_get_relationships_area_scope_and_forward_references(home, related):
    area, automation, shown, hidden = related
    by_area = {"scope": {"kind": "area", "id": area.id}}
    assert _body(await _call_tool("get_relationships", by_area, _key(), home, _data()))["scope"]["entity_ids"] == [shown]
    assert _body(await _call_tool("get_relationships", by_area, _key(filtered=False), home, _data()))["scope"]["entity_ids"] == [shown, hidden]
    by_entity = {"scope": {"kind": "entity", "id": automation}}
    assert _body(await _call_tool("get_relationships", by_entity, _key(), home, _data()))["references"] == [shown]
    assert _body(await _call_tool("get_relationships", by_entity, _key(filtered=False), home, _data()))["references"] == [shown, hidden]


async def test_get_logbook_requested_entity_and_home_wide_rows(home):
    rows = [{"entity_id": eid, "when": float(i), "state": "on", "message": "turned on"}
            for i, eid in enumerate((SHOWN, HIDDEN))]
    with patch.object(mcp_view, "async_ws_command", new=AsyncMock(return_value=rows)):
        await _assert_gated(
            home, "get_logbook", lambda eid: {"target": {"kind": "resources", "entity_ids": [eid]}}, _data())
        filtered = await _call_tool("get_logbook", {}, _key(), home, _data())
        unfiltered = await _call_tool("get_logbook", {}, _key(filtered=False), home, _data())
    assert [row["entity_id"] for row in _body(filtered)["entries"]] == [SHOWN]
    assert [row["entity_id"] for row in _body(unfiltered)["entries"]] == [SHOWN, HIDDEN]


_SCRIPT = {"sequence": [{"action": "light.turn_on", "target": {"entity_id": [SHOWN, HIDDEN, GHOST]}}]}


async def test_validate_automation_or_script_reports_unexposed_like_a_typo(home):
    async def refs(key):
        result = await _call_tool("validate_automation_or_script", {"type": "script", "config": _SCRIPT}, key, home, _data())
        return {row["entity_id"]: (row["exists"], row["accessible"]) for row in _body(result)["referenced_entities"]}

    filtered, unfiltered = await refs(_key()), await refs(_key(filtered=False))
    assert filtered == {SHOWN: (True, True), HIDDEN: (False, False), GHOST: (False, False)}
    assert unfiltered[HIDDEN] == (True, True)


async def test_saved_config_warnings_name_an_unexposed_reference(home):
    from custom_components.phoenix_mcp.tools.discovery import reference_warnings

    [filtered] = reference_warnings(home, _key(), "script", _SCRIPT)
    [unfiltered] = reference_warnings(home, _key(filtered=False), "script", _SCRIPT)
    assert filtered.endswith(f": {GHOST}, {HIDDEN}.") and SHOWN not in filtered
    assert unfiltered.endswith(f": {GHOST}.")


@pytest.mark.parametrize("tool", ["mesa_get_profile", "mesa_explain_profile"])
async def test_mesa_entity_tools(home, tool):
    from custom_components.phoenix_mcp.mesa import async_setup_mesa
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
    from custom_components.phoenix_mcp.mesa_tools import _not_found_envelope

    runtime = await async_setup_mesa(home, "advisory")
    for entity_id in (SHOWN, HIDDEN):
        runtime.store.set(entity_id, SemanticProfile.from_dict(
            entity_id, {"semantic_profile": {"semantic_tags": ["lighting.ambient"]}},
            default_origin=MetadataOrigin.USER))
    data = _data(mesa=runtime)
    assert (await _call_tool(tool, {"entity_id": SHOWN}, _key(), home, data))[1] == "allowed"
    assert (await _call_tool(tool, {"entity_id": HIDDEN}, _key(filtered=False), home, data))[1] == "allowed"
    refused = await _call_tool(tool, {"entity_id": HIDDEN}, _key(), home, data)
    assert refused[1] == "not_found"
    assert _body(refused) == _not_found_envelope(HIDDEN)
