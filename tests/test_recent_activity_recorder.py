"""recent_activity counts real state changes against the real Recorder.

Home Assistant records the unchanged state again at every restart, and records
attribute updates for climate-type domains as significant rows. Neither is a
change of state, so neither may make an entity look active.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool


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


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=dt_util.utcnow(), created_by="u",
        cap_search="allow",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN"),
                                            "climate": PermissionNode(state="GREEN")}),
    )


async def test_only_real_state_changes_count(recorder_mock, hass: HomeAssistant) -> None:
    now = dt_util.utcnow()

    def at(minutes: float) -> float:
        return (now - timedelta(minutes=minutes)).timestamp()

    hass.states.async_set("light.porch", "off", timestamp=at(120))
    hass.states.async_set("light.porch", "on", timestamp=at(30))
    hass.states.async_set("light.porch", "off", timestamp=at(20))
    # A restart records the unchanged state again with a new last_changed.
    hass.states.async_set("light.hall", "off", timestamp=at(120))
    hass.states.async_set("light.hall", "off", force_update=True, timestamp=at(10))
    # Climate attribute updates are significant rows with the same state.
    hass.states.async_set("climate.study", "heat", {"temperature": 20}, timestamp=at(120))
    hass.states.async_set("climate.study", "heat", {"temperature": 22}, timestamp=at(15))
    # First recorded inside the window: it appeared, which is a change.
    hass.states.async_set("light.shed", "on", timestamp=at(5))
    await async_wait_recording_done(hass)

    result = await _call_tool("recent_activity", {"minutes": 60}, _key(), hass, MagicMock(), "r", None)
    assert result[1] == "allowed", result
    body = json.loads(result[0]["content"][0]["text"])
    by_id = {row["entity_id"]: row for row in body["changes"]}
    assert set(by_id) == {"light.porch", "light.shed"}
    assert by_id["light.porch"]["changes_in_window"] == 2 and by_id["light.porch"]["state"] == "off"
    assert dt_util.parse_datetime(by_id["light.porch"]["when"]) == dt_util.utc_from_timestamp(at(20))
    assert by_id["light.shed"]["changes_in_window"] == 1
    assert body["total"] == body["count"] == 2 and body["truncated"] is False
