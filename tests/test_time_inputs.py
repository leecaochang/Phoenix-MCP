"""Every tool reads a time argument the same way.

A time or date without an offset is Home Assistant local time, as Home
Assistant's own dt_util.as_utc reads it; a refused relative time explains
itself through each tool's message instead of a bare "Invalid format".
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.helpers import parse_time_param
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools.logs import _logbook_time
from custom_components.phoenix_mcp.recorder_queries import parse_recorder_window

UTC = timezone.utc
BANGKOK = ZoneInfo("Asia/Bangkok")


@pytest.fixture
def bangkok():
    original = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(BANGKOK)
    yield
    dt_util.set_default_time_zone(original)


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(), cap_search="allow", cap_log_read="allow",
    )


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-10-24T08:00:00", datetime(2026, 10, 24, 1, tzinfo=UTC)),
    ("2026-10-24", datetime(2026, 10, 23, 17, tzinfo=UTC)),
    ("2026-10-24T08:00:00Z", datetime(2026, 10, 24, 8, tzinfo=UTC)),
    ("2026-10-24T08:00:00+02:00", datetime(2026, 10, 24, 6, tzinfo=UTC)),
])
def test_naive_times_and_dates_are_local_and_offsets_are_kept(bangkok, value, expected):
    assert parse_time_param(value) == expected
    assert _logbook_time(value, utcnow()) == expected
    window = parse_recorder_window({"start_time": value, "end_time": "2026-10-25T00:00:00Z"},
                                   default_range=timedelta(days=1))
    assert window.start == expected


@pytest.mark.parametrize(("value", "reason"), [
    ("5m", "5min for minutes or 5mo for months"),
    ("400d", "maximum allowed range of 366 days"),
    ("yesterday", "relative amount ago such as 30min"),
])
def test_a_refused_time_names_its_reason_in_every_tool(value, reason):
    with pytest.raises(ValueError, match=reason):
        parse_recorder_window({"start_time": value}, default_range=timedelta(days=1))


@pytest.mark.parametrize(("tool", "args"), [
    ("compare_states", {"entity_ids": ["light.nothing"], "t1": "5m"}),
    ("get_logs", {"since": "5m"}),
    ("get_logbook", {"start_time": "5m"}),
])
async def test_tool_messages_carry_the_parser_reason(hass, tool, args):
    content, outcome, _ = await _call_tool(tool, args, _key(), hass, MagicMock())
    assert outcome == "invalid_request"
    assert "5min for minutes or 5mo for months" in content["content"][0]["text"]


async def test_compare_states_takes_a_wall_time(hass):
    content, outcome, _ = await _call_tool(
        "compare_states", {"entity_ids": ["light.nothing"], "t1": "2026-10-01T08:00:00"}, _key(), hass, MagicMock())
    assert outcome == "allowed", content
    assert json.loads(content["content"][0]["text"])["comparisons"] == []
