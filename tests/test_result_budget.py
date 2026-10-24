"""List-shaped reads fit one result the Conversation Log keeps whole, and continue exactly.

A page that would pass TOOL_RESULT_MAX_BYTES ends early instead, and its own
continuation (next_offset, next_cursor, or the logbook's older_end_time) picks up
where it stopped, so nothing is skipped. Each test sets the budget just under the
whole result, then reads every page and checks the union is the whole set.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp import tool_common
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.conversation_log import json_size
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.policy_engine import Permission


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Start the recorder database before the hass fixture, as HA's own recorder tests do."""


@pytest.fixture(autouse=True)
def quiet_sql_engine() -> Iterator[None]:
    logger = logging.getLogger("sqlalchemy.engine")
    level = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(level)


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=dt_util.utcnow(), created_by="u",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN"), "sensor": PermissionNode(state="GREEN")}),
        **{"cap_search": "allow", "cap_registry_read": "allow", "cap_log_read": "allow", **caps})


async def _read(hass, tool, args, key=None):
    data = MagicMock()
    data.mesa = None
    content, outcome, _ = await _call_tool(tool, args, key or _key(), hass, data, "r", None)
    assert outcome == "allowed", content
    return content["content"][0]["text"]


async def _pages(hass, tool, args, rows_key, continuation, key=None, extra=0):
    """Every page under a budget about a third of the whole first page."""
    budget = max(json_size(await _read(hass, tool, args, key)) // 3, 1500) + extra
    pages = []
    with patch.object(tool_common, "TOOL_RESULT_MAX_BYTES", budget), \
            patch("custom_components.phoenix_mcp.mcp_view.TOOL_RESULT_MAX_BYTES", budget):
        page_args = dict(args)
        while True:
            text = await _read(hass, tool, page_args, key)
            body = json.loads(text)
            assert json_size(text) <= budget
            pages.append(body)
            follow = continuation(body)
            if follow is None:
                break
            page_args = {**args, **follow}
            assert len(pages) < 50
    return pages, [row for page in pages for row in page[rows_key]]


async def test_search_entities_pages_continue_at_next_offset(hass: HomeAssistant) -> None:
    for index in range(60):
        hass.states.async_set(f"light.lamp_{index:02d}", "on", {"friendly_name": f"Lamp {index} " + "x" * 80})
    pages, rows = await _pages(hass, "search_entities", {"domain": "light", "limit": 500}, "entities",
                               lambda body: {"offset": body["next_offset"]} if body["has_more"] else None)
    assert len(pages) > 1 and sorted(row["entity_id"] for row in rows) == [f"light.lamp_{index:02d}" for index in range(60)]


async def test_list_devices_pages_continue_at_next_offset(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="test", entry_id="e1")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    for index in range(40):
        registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", f"d{index}")},
                                     name=f"Device {index:02d} " + "y" * 60, manufacturer="Maker", model="Model")
    key = _key(pass_through=True)
    pages, rows = await _pages(hass, "list_devices", {"limit": 500}, "devices",
                               lambda body: {"offset": body["next_offset"]} if body["has_more"] else None, key)
    assert len(pages) > 1 and len(rows) == len({row["device_id"] for row in rows}) == 40


async def test_list_integrations_pages_continue_at_next_offset(hass: HomeAssistant) -> None:
    for index in range(40):
        MockConfigEntry(domain=f"test_{index:02d}", title=f"Integration {index} " + "z" * 60, entry_id=f"e{index}").add_to_hass(hass)
    key = _key(pass_through=True, cap_integration_write="allow")
    pages, rows = await _pages(hass, "list_integrations", {"limit": 500}, "integrations",
                               lambda body: {"offset": body["next_offset"]} if body["has_more"] else None, key)
    assert len(pages) > 1 and len({row["entry_id"] for row in rows}) == 40


@pytest.mark.parametrize("extra", [0, 150, 300, 450])
async def test_logbook_keeps_the_newest_and_names_where_older_ones_continue(hass: HomeAssistant, extra) -> None:
    base = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
    # Entries come in threes sharing one instant; across these budgets some page cut
    # falls inside an instant, and the continuation must still read every entry once.
    entries = [{"entity_id": "light.one", "when": base + (index // 3) * 60, "state": "on", "message": f"{index:02d}" + "m" * 120}
               for index in range(42)]

    async def logbook(_hass, _command, payload):
        end = datetime.fromisoformat(payload["end_time"]).timestamp()
        return [entry for entry in entries if entry["when"] < end]

    with patch("custom_components.phoenix_mcp.mcp_view.resolve_read", return_value=Permission.READ), \
            patch("custom_components.phoenix_mcp.mcp_view.filter_service_response", side_effect=lambda value, *_: value), \
            patch("custom_components.phoenix_mcp.mcp_view.async_ws_command", new=AsyncMock(side_effect=logbook)):
        args = {"start_time": "2026-10-23T00:00:00+00:00", "end_time": "2026-10-25T00:00:00+00:00", "limit": 1000,
                "target": {"kind": "resources", "entity_ids": ["light.one"]}}
        pages, rows = await _pages(hass, "get_logbook", args, "entries",
                                   lambda body: {"end_time": body["older_end_time"]} if body["truncated"] else None,
                                   extra=extra)
    assert len(pages) > 1
    assert pages[0]["entries"][-1]["when"] == entries[-1]["when"]
    assert sorted(row["message"] for row in rows) == [entry["message"] for entry in entries]
    assert all(len({row["when"] for row in page["entries"]}) * 3 == len(page["entries"]) for page in pages)


async def test_an_instant_larger_than_one_result_is_kept_partly_and_said_so(hass: HomeAssistant) -> None:
    base = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
    entries = [{"entity_id": "light.one", "when": base, "state": "on", "message": f"{index:02d}" + "m" * 120}
               for index in range(30)]
    with patch("custom_components.phoenix_mcp.mcp_view.resolve_read", return_value=Permission.READ), \
            patch("custom_components.phoenix_mcp.mcp_view.filter_service_response", side_effect=lambda value, *_: value), \
            patch("custom_components.phoenix_mcp.mcp_view.async_ws_command", new=AsyncMock(return_value=entries)), \
            patch.object(tool_common, "TOOL_RESULT_MAX_BYTES", 2500):
        body = json.loads(await _read(hass, "get_logbook", {
            "start_time": "2026-10-23T00:00:00+00:00", "end_time": "2026-10-25T00:00:00+00:00",
            "target": {"kind": "resources", "entity_ids": ["light.one"]}}))
    assert body["truncated"] and 0 < body["count"] < 30
    assert any("share one instant" in warning for warning in body["warnings"])


async def test_a_history_page_shrinks_and_continues_at_next_cursor(recorder_mock, hass: HomeAssistant) -> None:
    start = dt_util.utcnow() - timedelta(hours=2)
    for index in range(60):
        hass.states.async_set("sensor.weather", str(index), {"forecast": "w" * 400},
                              timestamp=(start + timedelta(seconds=index * 30)).timestamp())
    await async_wait_recording_done(hass)
    args = {"entity_id": "sensor.weather", "mode": "significant_states", "limit": 1000,
            "start_time": (start - timedelta(minutes=1)).isoformat(), "end_time": dt_util.utcnow().isoformat()}
    pages, rows = await _pages(hass, "get_history", args, "history",
                               lambda body: {"cursor": body["next_cursor"]} if body["has_more"] else None)
    assert len(pages) > 1 and any("shortened" in warning for warning in pages[0]["warnings"])
    assert [row["state"] for row in rows] == [str(index) for index in range(60)]


async def test_a_statistics_page_shrinks_and_continues_at_next_cursor(recorder_mock, hass: HomeAssistant) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=80)
    hass.states.async_set("sensor.temp", "1", {"unit_of_measurement": "°C", "state_class": "measurement"})
    async_import_statistics(hass, {
        "mean_type": StatisticMeanType.ARITHMETIC, "has_sum": False, "name": None, "source": "recorder",
        "statistic_id": "sensor.temp", "unit_class": "temperature", "unit_of_measurement": "°C",
    }, [{"start": start + timedelta(hours=index), "mean": 20.0 + index / 10, "min": 19.0, "max": 22.0} for index in range(72)])
    await async_wait_recording_done(hass)
    args = {"entity_id": "sensor.temp", "period": "hour", "limit": 1000,
            "start_time": start.isoformat(), "end_time": (start + timedelta(hours=72)).isoformat()}
    pages, rows = await _pages(hass, "get_statistics", args, "statistics",
                               lambda body: {"cursor": body["next_cursor"]} if body["has_more"] else None)
    assert len(pages) > 1 and len(rows) == 72 and len({row["start"] for row in rows}) == 72
