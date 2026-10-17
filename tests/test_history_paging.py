"""Raw get_history pages return every Recorder row exactly once, against the real Recorder.

Core stores each row's time.time() float, finer than a microsecond, and filters
on it strictly inside the requested bounds, while a cursor is a microsecond ISO
time. These tests write rows whose stored times fall either side of a microsecond
and page through them with small limits in both modes and both orders.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.recorder_queries import (
    backward_page_start, ceil_us, floor_us, forward_page_end, from_us,
)


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Start the recorder database before the hass fixture, as HA's own recorder tests do."""


@pytest.fixture(autouse=True)
def quiet_sql_engine() -> Iterator[None]:
    """Keep SQL statement logging out of the run when an earlier test left INFO enabled."""
    logger = logging.getLogger("sqlalchemy.engine")
    level = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(level)


BASE = dt_util.utcnow().replace(microsecond=0) - timedelta(days=2)
FRACTIONS = (0.0000002, 0.0000007, 0.0, 0.0000004999, 0.0000005001)


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=dt_util.utcnow(),
                           created_by="u", permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}))


def _at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


async def _page(hass: HomeAssistant, **args) -> dict:
    result = await _call_tool("get_history", {"entity_id": "light.desk", "start_time": _at(0).isoformat(),
                                              "end_time": _at(1000).isoformat(), **args},
                              _key(), hass, MagicMock(), "r", None)
    assert result[1] == "allowed", result
    return json.loads(result[0]["content"][0]["text"])


def _when(row: dict) -> str:
    return row.get("when") or row["last_updated"]


async def _write(hass: HomeAssistant, count: int) -> None:
    hass.states.async_set("light.desk", "off", timestamp=_at(-60).timestamp())
    for index in range(count):
        stamp = _at(10 + index * 7).timestamp() + FRACTIONS[index % len(FRACTIONS)]
        hass.states.async_set("light.desk", "on" if index % 2 == 0 else "off", timestamp=stamp)
    await async_wait_recording_done(hass)


@pytest.mark.parametrize("mode", ["state_changes", "significant_states"])
@pytest.mark.parametrize("order", ["oldest_first", "newest_first"])
async def test_small_pages_return_each_row_once(recorder_mock, hass: HomeAssistant, mode: str, order: str) -> None:
    await _write(hass, 40)
    whole = await _page(hass, mode=mode, order=order, limit=1000)
    expected = [_when(row) for row in whole["history"]]
    assert len(expected) == 40 and whole["has_more"] is False
    for limit in (1, 3, 7):
        seen, cursor, pages = [], None, 0
        while True:
            body = await _page(hass, mode=mode, order=order, limit=limit, **({"cursor": cursor} if cursor else {}))
            pages += 1
            assert 1 <= body["count"] <= limit
            seen += [_when(row) for row in body["history"]]
            if not body["has_more"]:
                break
            assert body["next_cursor"] != cursor, "a cursor must move"
            assert pages <= 80
            edge = "end" if order == "oldest_first" else "start"
            assert body["covered_range"][edge] == body["next_cursor"]
            cursor = body["next_cursor"]
        assert seen == expected, (limit, pages)
        assert pages >= 40 // limit


def test_stamps_agree_with_cores_strict_bounds() -> None:
    """Core keeps a row strictly after a lower bound and strictly before an upper one."""
    rng = random.Random(3)
    base = int(BASE.timestamp()) * 1_000_000
    for _ in range(20000):
        bound = base + rng.randint(0, 10**9)
        stored = (bound + rng.choice((-1, 0, 1)) * rng.random() * 2) / 1_000_000
        if rng.random() < 0.3:
            stored = bound / 1_000_000
        edge = from_us(bound).timestamp()
        assert (stored > edge) == (ceil_us(stored) > bound)
        assert (stored < edge) == (floor_us(stored) < bound)
    assert ceil_us(from_us(base).timestamp()) == floor_us(from_us(base).timestamp()) == base


def test_page_edges_never_split_one_microsecond() -> None:
    second = 1_700_000_000.0
    inside = [second + 0.0000002, second + 0.0000007]
    assert forward_page_end([second - 1, *inside, second + 1], 2) == 1
    assert forward_page_end([*inside, second + 1], 1) is None
    assert backward_page_start([second - 1, *inside, second + 1], 2) == 3
    assert backward_page_start([second - 1, *inside], 1) is None
    assert backward_page_start([second - 1, *inside], 2) == 1
