"""get_statistics over several entities: one row each for the whole window, against real Recorder statistics.

"Which room was warmest this week" or "which device used the most energy" is one
call: a measurement's mean, min and max, or a total's change, in its display unit.
"""
from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp import tool_common
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.conversation_log import json_size
from custom_components.phoenix_mcp.mcp_view import _call_tool


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
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN")},
                                   entities={"sensor.hidden": PermissionNode(state="RED")}),
        **{"cap_search": "allow", **caps})


def _hour_ago(hours: int) -> datetime:
    return dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)


def _import(hass: HomeAssistant, statistic_id: str, unit: str, unit_class: str, start: datetime, *, mean: bool) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics

    rows = [{"start": start + timedelta(hours=i), "mean": 20.0 + i, "min": 19.0, "max": 21.0 + i} if mean
            else {"start": start + timedelta(hours=i), "state": 2.0 + i, "sum": 2.0 + i} for i in range(3)]
    async_import_statistics(hass, {
        "mean_type": StatisticMeanType.ARITHMETIC if mean else StatisticMeanType.NONE,
        "has_sum": not mean, "name": None, "source": "recorder", "statistic_id": statistic_id,
        "unit_class": unit_class, "unit_of_measurement": unit,
    }, rows)


@pytest.fixture
async def stats_env(recorder_mock, hass: HomeAssistant) -> datetime:
    start = _hour_ago(6)
    registry = er.async_get(hass)
    for object_id, attributes in (
        ("temp_a", {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}),
        ("temp_b", {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}),
        ("meter", {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total"}),
        ("plain", {}),
        ("hidden", {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}),
    ):
        entry = registry.async_get_or_create("sensor", "test", object_id, suggested_object_id=object_id)
        if object_id == "temp_a":
            from homeassistant.helpers import area_registry as ar
            registry.async_update_entity(entry.entity_id, area_id=ar.async_get(hass).async_create("Kitchen").id)
        hass.states.async_set(f"sensor.{object_id}", "1", attributes)
    _import(hass, "sensor.temp_a", "°C", "temperature", start, mean=True)
    _import(hass, "sensor.temp_b", "°C", "temperature", start - timedelta(hours=1), mean=True)
    _import(hass, "sensor.hidden", "°C", "temperature", start, mean=True)
    # Stored in MWh, displayed in kWh: the window's change is converted like a page's values.
    _import(hass, "sensor.meter", "MWh", "energy", start, mean=False)
    await async_wait_recording_done(hass)
    return start


async def _summary(hass, start, key=None, **args):
    args = {"start_time": start.isoformat(), "end_time": (start + timedelta(hours=3)).isoformat(), **args}
    result = await _call_tool("get_statistics", args, key or _key(), hass, MagicMock(), "r", None)
    return result


async def test_one_call_gives_each_entity_its_window(hass: HomeAssistant, stats_env) -> None:
    result = await _summary(hass, stats_env, entity_id=["sensor.temp_a", "sensor.meter", "sensor.plain", "sensor.hidden"])
    assert result[1] == "allowed", result
    body = json.loads(result[0]["content"][0]["text"])
    rows = {row["entity_id"]: row for row in body["entities"]}
    assert rows["sensor.temp_a"] == {"entity_id": "sensor.temp_a", "name": "temp a", "area": "Kitchen",
                                     "mean": 21.0, "min": 19.0, "max": 23.0, "unit": "°C"}
    meter = rows["sensor.meter"]
    assert set(meter) == {"entity_id", "name", "area", "change", "unit"} and meter["area"] is None
    assert meter["unit"] == "kWh" and meter["change"] >= 1000.0
    assert body["no_statistics"] == ["sensor.plain"] and body["not_found"] == ["sensor.hidden"]
    assert list(rows) == ["sensor.temp_a", "sensor.meter"]


async def test_select_names_the_entities_and_types_narrow_the_row(hass: HomeAssistant, stats_env) -> None:
    # temp_b's first hour falls before the window, so only its later two count.
    result = await _summary(hass, stats_env, select={"device_class": "temperature"}, statistic_types=["mean"])
    body = json.loads(result[0]["content"][0]["text"])
    assert body["matched"] == 2 and body["select"] == {"device_class": "temperature"}
    assert [{key: row[key] for key in ("entity_id", "mean", "unit")} for row in body["entities"]] == [
        {"entity_id": "sensor.temp_a", "mean": 21.0, "unit": "°C"}, {"entity_id": "sensor.temp_b", "mean": 21.5, "unit": "°C"}]
    assert "max" not in body["entities"][0]


async def test_page_options_carried_over_are_ignored_and_named(hass: HomeAssistant, stats_env) -> None:
    # Models used to a page request add period or sum; refusing them cost a round for nothing.
    result = await _summary(hass, stats_env, entity_id=["sensor.meter"], period="day", statistic_types=["change", "sum"])
    assert result[1] == "allowed", result
    body = json.loads(result[0]["content"][0]["text"])
    assert set(body["entities"][0]) == {"entity_id", "name", "area", "change", "unit"}
    assert any(warning.startswith("Ignored period, sum") for warning in body["warnings"])


@pytest.mark.parametrize(("args", "message"), [
    ({"entity_id": ["sensor.temp_a"], "statistic_types": [3]}, "array of strings"),
    ({"entity_id": [f"sensor.s{index}" for index in range(501)]}, "list of 1 to 500"),
    ({"entity_id": "sensor.temp_a", "select": {"domain": "sensor"}}, "not both"),
])
async def test_a_malformed_request_is_refused(hass: HomeAssistant, stats_env, args, message) -> None:
    content, outcome, _ = await _summary(hass, stats_env, **args)
    assert outcome == "invalid_request" and message in content["content"][0]["text"]


async def test_rows_past_one_result_are_named(hass: HomeAssistant, stats_env) -> None:
    ids = ["sensor.temp_a", "sensor.temp_b", "sensor.meter"]
    whole = json_size((await _summary(hass, stats_env, entity_id=ids))[0]["content"][0]["text"])
    with patch.object(tool_common, "TOOL_RESULT_MAX_BYTES", whole - 1):
        body = json.loads((await _summary(hass, stats_env, entity_id=ids))[0]["content"][0]["text"])
    assert [row["entity_id"] for row in body["entities"]] + body["not_summarized"] == ids
    assert body["not_summarized"] and any("not_summarized" in warning for warning in body["warnings"])


async def test_a_single_entity_still_reads_a_page(hass: HomeAssistant, stats_env) -> None:
    body = json.loads((await _summary(hass, stats_env, entity_id="sensor.temp_a", period="hour"))[0]["content"][0]["text"])
    assert len(body["statistics"]) == 3


async def test_an_entity_outside_scope_is_never_read_from_recorder(hass: HomeAssistant, stats_env) -> None:
    from custom_components.phoenix_mcp import mcp_view

    read: list[str] = []
    real = mcp_view.read_statistic_window

    def recording(hass_, start, end, statistic_id, types):
        read.append(statistic_id)
        return real(hass_, start, end, statistic_id, types)

    with patch.object(mcp_view, "read_statistic_window", side_effect=recording):
        body = json.loads((await _summary(hass, stats_env, entity_id=["sensor.hidden", "sensor.temp_a"]))[0]["content"][0]["text"])
    assert read == ["sensor.temp_a"] and body["not_found"] == ["sensor.hidden"]
