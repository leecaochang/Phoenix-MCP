"""get_statistics over several entities: one row each for the whole window, against real Recorder statistics.

"Which room was warmest this week" or "which device used the most energy" is one
call: a measurement's mean, min and max, or a total's change, in its display unit.
"""
from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp import tool_common
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.conversation_log import json_size
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from tests.recorder_rows import page_rows


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


@pytest.mark.parametrize("types", [["mean"], ["change", "mean"], ["min", "max"]])
async def test_a_type_a_total_does_not_keep_is_left_out_not_a_failure(hass: HomeAssistant, stats_env, types) -> None:
    """Core's window query fails outright when asked only for columns a total lacks."""
    result = await _summary(hass, stats_env, entity_id=["sensor.meter", "sensor.temp_a"], statistic_types=types)
    assert result[1] == "allowed", result
    rows = {row["entity_id"]: row for row in json.loads(result[0]["content"][0]["text"])["entities"]}
    assert set(rows["sensor.meter"]) == {"entity_id", "name", "area", "unit", *({"change"} & set(types))}
    assert set(types) - {"change"} <= set(rows["sensor.temp_a"])


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
    assert len(page_rows(body, "statistics")) == 3


async def test_an_entity_outside_scope_is_never_read_from_recorder(hass: HomeAssistant, stats_env) -> None:
    from custom_components.phoenix_mcp import recorder_queries
    from custom_components.phoenix_mcp.tools import history as history_tools

    asked: list[set[str]] = []
    read: list[str] = []
    real_metadata, real_values = recorder_queries.read_statistic_metadata, recorder_queries.read_statistic_values

    def metadata(hass_, statistic_ids):
        asked.append(set(statistic_ids))
        return real_metadata(hass_, statistic_ids)

    def values(hass_, start, end, statistic_id, meta, types):
        read.append(statistic_id)
        return real_values(hass_, start, end, statistic_id, meta, types)

    with (patch.object(history_tools, "read_statistic_metadata", side_effect=metadata),
          patch.object(history_tools, "read_statistic_values", side_effect=values)):
        body = json.loads((await _summary(hass, stats_env, entity_id=["sensor.hidden", "sensor.temp_a"]))[0]["content"][0]["text"])
    assert asked == [{"sensor.temp_a"}] and read == ["sensor.temp_a"] and body["not_found"] == ["sensor.hidden"]


class _SlowReads:
    """Wraps the window read so it takes a while in its thread, and records how many overlap."""

    def __init__(self, delays: dict[str, float] | None = None) -> None:
        from custom_components.phoenix_mcp import recorder_queries

        self._real = recorder_queries.read_statistic_values
        self._delays = delays or {}
        self._lock = threading.Lock()
        self.running = 0
        self.most = 0
        self.order: list[str] = []

    def __call__(self, hass_, start, end, statistic_id, meta, types):
        with self._lock:
            self.running += 1
            self.most = max(self.most, self.running)
        try:
            time.sleep(self._delays.get(statistic_id, 0.05))
            return self._real(hass_, start, end, statistic_id, meta, types)
        finally:
            with self._lock:
                self.running -= 1
                self.order.append(statistic_id)


async def test_one_metadata_query_serves_every_entity(hass: HomeAssistant, stats_env) -> None:
    from custom_components.phoenix_mcp import recorder_queries
    from custom_components.phoenix_mcp.tools import history as history_tools

    ids = ["sensor.temp_a", "sensor.plain", "sensor.temp_b", "sensor.meter"]
    calls: list[set[str]] = []
    real = recorder_queries.read_statistic_metadata

    def metadata(hass_, statistic_ids):
        calls.append(set(statistic_ids))
        return real(hass_, statistic_ids)

    whole = json.loads((await _summary(hass, stats_env, entity_id=ids))[0]["content"][0]["text"])
    with patch.object(history_tools, "read_statistic_metadata", side_effect=metadata):
        body = json.loads((await _summary(hass, stats_env, entity_id=ids))[0]["content"][0]["text"])
    assert calls == [set(ids)] and body == whole
    assert [row["entity_id"] for row in body["entities"]] == ["sensor.temp_a", "sensor.temp_b", "sensor.meter"]
    assert body["no_statistics"] == ["sensor.plain"]


async def test_window_reads_overlap_up_to_the_cap_and_rows_keep_request_order(hass: HomeAssistant, stats_env) -> None:
    from custom_components.phoenix_mcp.tools import history as history_tools

    ids = ["sensor.temp_a", "sensor.temp_b", "sensor.meter"]
    serial = json.loads((await _summary(hass, stats_env, entity_id=ids))[0]["content"][0]["text"])
    # The first entity's read finishes last, so rows in finishing order would put it at the end.
    slow = _SlowReads({"sensor.temp_a": 0.3})
    with patch.object(history_tools, "read_statistic_values", side_effect=slow):
        body = json.loads((await _summary(hass, stats_env, entity_id=ids))[0]["content"][0]["text"])
    assert slow.most == 3 and slow.order[-1] == "sensor.temp_a"
    assert body == serial
    capped = _SlowReads()
    with (patch.object(history_tools, "RECORDER_PARALLEL_READS", 2),
          patch.object(history_tools, "read_statistic_values", side_effect=capped)):
        body = json.loads((await _summary(hass, stats_env, entity_id=ids))[0]["content"][0]["text"])
    assert capped.most == 2 and body == serial


async def test_no_window_read_starts_after_the_time_budget(hass: HomeAssistant, stats_env) -> None:
    from custom_components.phoenix_mcp.tools import history as history_tools

    slow = _SlowReads()
    with (patch.object(history_tools, "HISTORY_SUMMARY_BATCH_SECONDS", 0),
          patch.object(history_tools, "read_statistic_values", side_effect=slow)):
        body = json.loads((await _summary(
            hass, stats_env, entity_id=["sensor.meter", "sensor.plain", "sensor.temp_a"]))[0]["content"][0]["text"])
    assert slow.order == [] and body["entities"] == []
    assert body["unscanned"] == ["sensor.meter", "sensor.temp_a"] and body["no_statistics"] == ["sensor.plain"]


async def test_a_failed_window_read_fails_the_call_without_detail(hass: HomeAssistant, stats_env) -> None:
    from custom_components.phoenix_mcp.tools import history as history_tools

    def broken(hass_, start, end, statistic_id, meta, types):
        raise RuntimeError("database detail")

    with patch.object(history_tools, "read_statistic_values", side_effect=broken):
        content, outcome, _ = await _summary(hass, stats_env, entity_id=["sensor.temp_a", "sensor.meter"])
    assert outcome == "invalid_request" and content["content"][0]["text"] == "Statistics call failed."
