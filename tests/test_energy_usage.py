"""get_energy_config with a range reports each meter's usage, so energy questions take one call.

Each figure is the same window change get_statistics reports for that entity;
electricity_used is the Energy dashboard's sum and is given only when every
meter it needs was read. A statistic outside the key's scope is neither read
nor named.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool

_WS = "custom_components.phoenix_mcp.tools.energy.async_ws_command"
METERS = {"grid_in": 3.0, "grid_out": 1.0, "solar": 2.0, "battery_out": 0.5, "battery_in": 0.25, "workshop": 1.5,
          "secret": 9.0}
PREFS = {
    "energy_sources": [
        {"type": "grid", "stat_energy_from": "sensor.grid_in", "stat_energy_to": "sensor.grid_out"},
        {"type": "solar", "stat_energy_from": "sensor.solar"},
        {"type": "battery", "stat_energy_from": "sensor.battery_out", "stat_energy_to": "sensor.battery_in"},
    ],
    "device_consumption": [{"stat_consumption": "sensor.workshop"}, {"stat_consumption": "sensor.secret"}],
    "device_consumption_water": [],
}


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


def _key() -> AccessKeyRecord:
    tree = PermissionTree(domains={"sensor": PermissionNode(state="GREEN")},
                          entities={"sensor.secret": PermissionNode(state="RED")})
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=dt_util.utcnow(),
                           created_by="u", permissions=tree, cap_config_read="allow")


@pytest.fixture
async def meters(recorder_mock, hass: HomeAssistant) -> datetime:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics

    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    for name, step in METERS.items():
        hass.states.async_set(f"sensor.{name}", "1", {"unit_of_measurement": "kWh", "device_class": "energy",
                                                     "state_class": "total_increasing", "friendly_name": name.title()})
        async_import_statistics(hass, {
            "mean_type": StatisticMeanType.NONE, "has_sum": True, "name": None, "source": "recorder",
            "statistic_id": f"sensor.{name}", "unit_class": "energy", "unit_of_measurement": "kWh",
        }, [{"start": start + timedelta(hours=i), "state": step * (i + 1), "sum": step * (i + 1)} for i in range(4)])
    await async_wait_recording_done(hass)
    return start


async def _usage(hass: HomeAssistant, start: datetime, prefs: dict = PREFS) -> dict:
    args = {"start_time": start.isoformat(), "end_time": (start + timedelta(hours=4)).isoformat()}
    with patch(_WS, AsyncMock(return_value=prefs)):
        result = await _call_tool("get_energy_config", args, _key(), hass, MagicMock())
    assert result[1] == "allowed", result
    return json.loads(result[0]["content"][0]["text"])


async def test_each_meter_reports_the_change_get_statistics_reports(hass: HomeAssistant, meters: datetime) -> None:
    body = await _usage(hass, meters)
    usage = body["usage"]
    ids = [f"sensor.{name}" for name in METERS if name != "secret"]
    stats = await _call_tool("get_statistics", {"entity_id": ids, "start_time": meters.isoformat(),
                                                "end_time": (meters + timedelta(hours=4)).isoformat(),
                                                "statistic_types": ["change"]}, _key(), hass, MagicMock())
    expected = {row["entity_id"]: row["change"] for row in json.loads(stats[0]["content"][0]["text"])["entities"]}
    measured = {(row["type"], row["measured"]): row for row in usage["sources"]}
    assert set(measured) == {("grid", "imported"), ("grid", "exported"), ("solar", "produced"),
                             ("battery", "discharged"), ("battery", "charged")}
    for row in [*usage["sources"], *usage["devices"]]:
        assert row["change"] == expected[row["statistic"]] and row["unit"] == "kWh"
    total = sum(sign * measured[key]["change"] for key, sign in (
        (("grid", "imported"), 1), (("grid", "exported"), -1), (("solar", "produced"), 1),
        (("battery", "discharged"), 1), (("battery", "charged"), -1)))
    assert usage["electricity_used"] == {"value": round(total, 4), "unit": "kWh"}
    assert [row["statistic"] for row in usage["devices"]] == ["sensor.workshop"]
    assert usage["devices"][0]["name"] == "Workshop"
    # The hidden device's statistic is redacted in the configuration and never read or named.
    assert "sensor.secret" not in json.dumps(body) and "not_read" not in usage


async def test_no_total_without_every_meter_and_no_usage_without_a_range(hass: HomeAssistant, meters: datetime) -> None:
    prefs = {**PREFS, "energy_sources": [*PREFS["energy_sources"],
                                         {"type": "solar", "stat_energy_from": "sensor.no_statistics"}]}
    hass.states.async_set("sensor.no_statistics", "1")
    usage = (await _usage(hass, meters, prefs))["usage"]
    assert "electricity_used" not in usage and usage["not_read"] == ["sensor.no_statistics"]
    with patch(_WS, AsyncMock(return_value=PREFS)):
        result = await _call_tool("get_energy_config", {}, _key(), hass, MagicMock())
    assert "usage" not in json.loads(result[0]["content"][0]["text"])


async def test_several_sources_of_one_type_all_count(hass: HomeAssistant, meters: datetime) -> None:
    prefs = {**PREFS, "energy_sources": [*PREFS["energy_sources"], {"type": "solar", "stat_energy_from": "sensor.workshop"}]}
    one, two = (await _usage(hass, meters))["usage"], (await _usage(hass, meters, prefs))["usage"]
    extra = next(row["change"] for row in two["sources"] if row["statistic"] == "sensor.workshop")
    assert two["electricity_used"]["value"] == round(one["electricity_used"]["value"] + extra, 4)
