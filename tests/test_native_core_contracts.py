"""Energy saves and Recorder statistics against the installed Core, never a copy of it.

Both tools hand Core's own contract to the caller. An Energy plan is judged by the
registered energy/save_prefs schema before an approval exists, and the readback is
compared with the stored form that schema produces: Core fills a new grid source's
price and meter defaults, so a hand-built expectation reported every successful
first grid as partial. Statistics carry the unit Core converted their values to,
read from Recorder metadata so an empty page is labelled too, and their reset
timestamps are re-read in the stored unit because Core 2026.7 to 2026.10 passes
last_reset through the value converter. Each test runs the real recorder and the
real Energy integration; nothing here mirrors Core's rules.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
import inspect
import json
import logging
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.tool_defs import _ENTITY_TOOL_DEFS
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import WsCommandNotDispatchedError, async_ws_command


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


def _data() -> PhoenixData:
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


def _key(**caps) -> AccessKeyRecord:
    caps.setdefault("cap_config_read", "allow")
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=dt_util.utcnow(), created_by="u",
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}), **caps,
    )


def _body(result) -> dict:
    return json.loads(result[0]["content"][0]["text"])


async def _edit(hass: HomeAssistant, key: AccessKeyRecord, data: PhoenixData, target: dict, changes: dict, op: str = "set_source"):
    return await _call_tool(
        "edit_energy_config", {"operation": op, "target": target, "changes": changes}, key, hass, data, "r", None)


@pytest.fixture
async def energy(recorder_mock, hass: HomeAssistant, hass_admin_user) -> None:
    """The real Energy integration on the real recorder, with an admin to save as."""
    assert await async_setup_component(hass, "energy", {})
    for entity_id in ("sensor.grid_a", "sensor.grid_b", "sensor.solar"):
        hass.states.async_set(entity_id, "1", {"unit_of_measurement": "kWh"})


async def test_the_first_grid_source_verifies_against_cores_stored_form(hass: HomeAssistant, energy) -> None:
    """Core fills a new grid's defaults; readback, version and recovery all hold that form."""
    from custom_components.phoenix_mcp.config_reconciliation import _matches, observe_configuration

    data = _data()
    result = await _edit(hass, _key(cap_energy_write="allow"), data,
                         {"kind": "source", "source_type": "grid"}, {"stat_energy_from": "sensor.grid_a"})
    body = _body(result)
    assert result[1] == "allowed", body
    assert body["saved"] is True and "status" not in body
    stored = await async_ws_command(hass, "energy/get_prefs", {})
    grid = stored["energy_sources"][0]
    assert grid["stat_energy_to"] is None and grid["number_energy_price_export"] is None
    assert body["energy_sources"] == stored["energy_sources"]
    record = data.config_recovery.get(body["recovery_id"])
    assert record["phase"] == "verified"
    # An uncertain outcome is reconciled by comparing this expectation with the live
    # preferences, so it must be Core's stored form or it could never match.
    assert _matches(record, await observe_configuration(hass, record))
    assert data.versions.list_recent()[0].after["energy_sources"] == stored["energy_sources"]


@pytest.mark.parametrize(("seed", "op", "target", "changes", "reason"), [
    # Clearing a meter Core requires: Phoenix's own checks saw nothing wrong.
    ([{"type": "solar", "stat_energy_from": "sensor.solar"}], "set_source",
     {"kind": "source", "source_type": "solar"}, {"stat_energy_from": None}, "stat_energy_from"),
    # Two grid connections sharing one import meter: a rule Phoenix never mirrored.
    ([{"type": "grid", "stat_energy_from": "sensor.grid_a", "cost_adjustment_day": 0},
      {"type": "grid", "stat_energy_from": "sensor.grid_b", "cost_adjustment_day": 0}], "replace_statistic",
     {"kind": "statistic", "id": "sensor.grid_a"}, {"new_statistic": "sensor.grid_b"}, "multiple grid connections"),
    # Both price shapes at once, judged on the merged source.
    ([{"type": "grid", "stat_energy_from": "sensor.grid_a", "number_energy_price": 0.3, "cost_adjustment_day": 0}],
     "set_source", {"kind": "source", "source_type": "grid"}, {"entity_energy_price": "sensor.grid_b"},
     "either an entity or a fixed number"),
])
async def test_a_save_core_would_refuse_never_becomes_an_approval(
    hass: HomeAssistant, energy, seed, op, target, changes, reason,
) -> None:
    """Core's registered schema refuses the plan before the Confirm gate is reached."""
    await async_ws_command(hass, "energy/save_prefs", {"energy_sources": seed})
    before = await async_ws_command(hass, "energy/get_prefs", {})
    gate = AsyncMock()
    with patch("custom_components.phoenix_mcp.tools.energy._gate", gate):
        result = await _edit(hass, _key(cap_energy_write="confirm"), _data(), target, changes, op)
    assert result[1] == "invalid_request"
    text = result[0]["content"][0]["text"]
    assert text.startswith("Home Assistant refuses this Energy configuration:") and reason in text, text
    gate.assert_not_awaited()
    assert await async_ws_command(hass, "energy/get_prefs", {}) == before


async def test_cores_refusal_text_redacts_entities_the_key_cannot_read(hass: HomeAssistant, energy) -> None:
    """Core's message is free text from an admin-run schema, so it is scoped like any other."""
    from custom_components.phoenix_mcp.tools.energy import _core_refusal

    hass.states.async_set("light.private_lamp", "on")
    text = _core_refusal(WsCommandNotDispatchedError(
        "Import meter light.private_lamp clashes with sensor.grid_a"), _key(), hass)
    assert "light.private_lamp" not in text and "sensor.grid_a" in text


async def test_a_version_written_before_normalization_restores_as_verified(hass: HomeAssistant, energy) -> None:
    """An older version record holds a grid without Core's defaults; the restore still verifies."""
    from custom_components.phoenix_mcp.tools.energy import async_restore_energy_prefs

    data = _data()
    snapshot = {"energy_sources": [{"type": "grid", "cost_adjustment_day": 0.0, "stat_energy_from": "sensor.grid_a"}]}
    result = await async_restore_energy_prefs(snapshot, _key(cap_energy_write="allow"), hass, data)
    assert result[1] == "allowed", result
    assert data.config_recovery.records()[0]["phase"] == "verified"


def _import(hass: HomeAssistant, statistic_id: str, unit: str | None, unit_class: str | None,
            start: datetime, *, reset: datetime | None = None, mean: bool = False) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics

    rows = [
        {"start": start + timedelta(hours=i), "mean": 20.0 + i, "min": 19.0, "max": 21.0 + i} if mean
        else {"start": start + timedelta(hours=i), "state": 1.0 + i, "sum": 2.0 + i, "last_reset": reset}
        for i in range(3)
    ]
    async_import_statistics(hass, {
        "mean_type": StatisticMeanType.ARITHMETIC if mean else StatisticMeanType.NONE,
        "has_sum": not mean, "name": None, "source": "recorder", "statistic_id": statistic_id,
        "unit_class": unit_class, "unit_of_measurement": unit,
    }, rows)


async def _statistics(hass: HomeAssistant, entity_id: str, start: datetime, **args) -> dict:
    result = await _call_tool("get_statistics", {
        "entity_id": entity_id, "period": "hour", "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=4)).isoformat(), **args,
    }, _key(), hass, _data(), "r", None)
    assert result[1] == "allowed", result
    return _body(result)


def _hour_ago(hours: int) -> datetime:
    return dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)


async def test_converted_statistics_carry_their_unit_and_an_unconverted_reset(recorder_mock, hass: HomeAssistant) -> None:
    """Stored MWh displayed as kWh: values converted, labelled, and the reset left a time."""
    hass.states.async_set("sensor.meter", "5", {"unit_of_measurement": "kWh", "state_class": "total"})
    start, reset = _hour_ago(6), _hour_ago(30)
    _import(hass, "sensor.meter", "MWh", "energy", start, reset=reset)
    await async_wait_recording_done(hass)
    body = await _statistics(hass, "sensor.meter", start)
    assert body["units"] == {
        "display_unit_of_measurement": "kWh", "statistics_unit_of_measurement": "MWh", "unit_class": "energy"}
    first = body["statistics"][0]
    assert first["state"] == 1000.0 and first["sum"] == 2000.0
    # Core multiplied this timestamp by 1000 along with the values.
    assert first["last_reset"] == reset.isoformat()
    assert first["end"] == (start + timedelta(hours=1)).isoformat()
    assert body["warnings"] == []


async def test_an_entity_without_a_state_reads_in_its_stored_unit(recorder_mock, hass: HomeAssistant) -> None:
    """With no current state Core converts nothing, so the label is the stored unit."""
    er.async_get(hass).async_get_or_create("sensor", "test", "meter_old", suggested_object_id="meter_old")
    start = _hour_ago(6)
    _import(hass, "sensor.meter_old", "MWh", "energy", start, reset=_hour_ago(30))
    await async_wait_recording_done(hass)
    body = await _statistics(hass, "sensor.meter_old", start)
    assert body["units"]["display_unit_of_measurement"] == "MWh"
    assert body["statistics"][0]["state"] == 1.0
    assert body["statistics"][0]["last_reset"] == _hour_ago(30).isoformat()


async def test_a_mean_statistic_is_labelled_in_its_display_unit(recorder_mock, hass: HomeAssistant) -> None:
    """Temperature stored in Celsius and shown in Fahrenheit converts means, not just sums."""
    hass.states.async_set("sensor.room", "70", {"unit_of_measurement": "°F", "state_class": "measurement"})
    start = _hour_ago(6)
    _import(hass, "sensor.room", "°C", "temperature", start, mean=True)
    await async_wait_recording_done(hass)
    body = await _statistics(hass, "sensor.room", start)
    assert body["units"]["display_unit_of_measurement"] == "°F"
    assert body["statistics"][0]["mean"] == pytest.approx(68.0)


async def test_the_unit_is_known_on_an_empty_page(recorder_mock, hass: HomeAssistant) -> None:
    """The label comes from metadata, not the rows, so a page with no data keeps it."""
    hass.states.async_set("sensor.meter", "5", {"unit_of_measurement": "kWh", "state_class": "total"})
    _import(hass, "sensor.meter", "MWh", "energy", _hour_ago(6), reset=_hour_ago(30))
    await async_wait_recording_done(hass)
    body = await _statistics(hass, "sensor.meter", _hour_ago(48))
    assert body["statistics"] == []
    assert body["units"]["display_unit_of_measurement"] == "kWh"


async def test_unitless_is_known_and_no_metadata_is_unknown(recorder_mock, hass: HomeAssistant) -> None:
    """A unitless statistic labels null inside units; an entity without statistics has no units."""
    hass.states.async_set("sensor.count", "3", {"state_class": "total"})
    hass.states.async_set("sensor.untracked", "3")
    start = _hour_ago(6)
    _import(hass, "sensor.count", None, None, start, reset=_hour_ago(30))
    await async_wait_recording_done(hass)
    counted = await _statistics(hass, "sensor.count", start)
    assert counted["units"] == {
        "display_unit_of_measurement": None, "statistics_unit_of_measurement": None, "unit_class": None}
    assert counted["statistics"][0]["state"] == 1.0
    assert (await _statistics(hass, "sensor.untracked", start))["units"] is None


async def test_a_metadata_failure_keeps_values_and_omits_resets(recorder_mock, hass: HomeAssistant) -> None:
    """Without metadata Phoenix cannot know whether a reset was converted, so it says so."""
    hass.states.async_set("sensor.meter", "5", {"unit_of_measurement": "kWh", "state_class": "total"})
    start = _hour_ago(6)
    _import(hass, "sensor.meter", "MWh", "energy", start, reset=_hour_ago(30))
    await async_wait_recording_done(hass)
    with patch("homeassistant.components.recorder.statistics.get_metadata", side_effect=RuntimeError("db")):
        body = await _statistics(hass, "sensor.meter", start)
    assert body["units"] is None
    assert body["statistics"][0]["state"] == 1000.0 and "last_reset" not in body["statistics"][0]
    assert any("unlabelled" in warning and "last_reset was omitted" in warning for warning in body["warnings"])


def test_the_advertised_statistic_types_are_cores() -> None:
    """The statistic_types enum is the Literal Core's own query accepts, read from its source."""
    from homeassistant.components.recorder import statistics as recorder_stats

    tree = ast.parse(inspect.getsource(recorder_stats))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "statistics_during_period")
    annotation = next(arg.annotation for arg in function.args.args if arg.arg == "types")
    core = {node.value for node in ast.walk(annotation) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    definition = next(tool for tool in _ENTITY_TOOL_DEFS if tool["name"] == "get_statistics")
    advertised = set(definition["inputSchema"]["properties"]["statistic_types"]["items"]["enum"])
    assert core and advertised == core


async def test_an_unverified_energy_version_restore_is_reported_as_unverified(hass: HomeAssistant, energy) -> None:
    """Through the admin endpoint: a differing readback answers 409, records no version, leaves recovery partial."""
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    from custom_components.phoenix_mcp.admin_view import PhoenixAdminVersionRestoreView
    from custom_components.phoenix_mcp.const import DOMAIN

    data = _data()
    hass.data[DOMAIN] = data
    await async_ws_command(hass, "energy/save_prefs", {"energy_sources": [{"type": "solar", "stat_energy_from": "sensor.solar"}]})
    current = await async_ws_command(hass, "energy/get_prefs", {})
    record = await data.versions.record(
        resource_type="energy", resource_id="preferences", action="edit",
        before={"energy_sources": [], "device_consumption": [], "device_consumption_water": []},
        after=current, alias="Energy dashboard", access_key_name="agent")
    user = MagicMock(is_admin=True, id="admin-user")
    state = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "rid"}
    request = MagicMock(content_length=0)
    request.__getitem__.side_effect = state.__getitem__
    request.get.side_effect = lambda key, default=None: state.get(key, default)
    request.content.read = AsyncMock(side_effect=[b"", b""])
    view = PhoenixAdminVersionRestoreView()
    view.hass = hass
    drifted = {**current, "energy_sources": [{"type": "solar", "stat_energy_from": "sensor.grid_a"}]}
    with patch("custom_components.phoenix_mcp.tools.energy._read_prefs_raw", AsyncMock(side_effect=[current, drifted])):
        response = await view.post(request, version_id=record.id)
    assert response.status == 409
    assert json.loads(response.text)["message_key"] == "adminError.restoreUnverified"
    assert [r.id for r in data.versions.list_recent()] == [record.id]
    assert data.config_recovery.records()[0]["phase"] == "partial"
