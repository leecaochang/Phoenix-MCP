"""get_history summary mode against the real Recorder, plus its paging contract.

Each Recorder test writes states at chosen timestamps through Core's state
machine and reads them back through the real history query, so attribute-only
filtering, the window-start baseline and Recorder's oldest-state rule are Core's.
The randomized tests at the end drive the scanner with a fake that reproduces the
state-change query's contract (rows strictly inside the range, ascending, the
limit applied in that order, and the latest earlier row stamped at the start),
including ties and one-microsecond gaps that real writes rarely produce.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.history_summary import (
    SummaryUnavailableError, cursor_cut, group_cut, scan_state_changes,
)
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.policy_engine import Permission


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


def _key() -> AccessKeyRecord:
    domains = {domain: PermissionNode(state="GREEN") for domain in ("light", "sensor", "zone")}
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=dt_util.utcnow(),
                           created_by="u", permissions=PermissionTree(domains=domains))


def _at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def _set(hass: HomeAssistant, entity_id: str, state: str, seconds: float, attributes: dict | None = None,
         **kwargs) -> None:
    hass.states.async_set(entity_id, state, attributes, timestamp=_at(seconds).timestamp(), **kwargs)


def _anchor(hass: HomeAssistant) -> None:
    """Make Recorder's oldest state older than every window, as on an instance with history."""
    _set(hass, "sensor.anchor", "x", -86400)


async def _call(hass: HomeAssistant, entity_id: str, start: float, end: float, **extra):
    args = {"entity_id": entity_id, "mode": "summary", "start_time": _at(start).isoformat(),
            "end_time": _at(end).isoformat(), **extra}
    return await _call_tool("get_history", args, _key(), hass, MagicMock(), "r", None)


async def _summary(hass: HomeAssistant, entity_id: str, start: float, end: float, **extra) -> dict:
    result = await _call(hass, entity_id, start, end, **extra)
    assert result[1] == "allowed", result
    return json.loads(result[0]["content"][0]["text"])


def _states(body: dict) -> dict[str, tuple[float, int]]:
    return {row["state"]: (row["duration_seconds"], row["entered"]) for row in body["states"]}


async def test_a_window_without_changes_is_the_baseline_throughout(recorder_mock, hass: HomeAssistant) -> None:
    """The state in effect at the start fills the window and is not a transition."""
    _anchor(hass)
    _set(hass, "light.desk", "on", -3600)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "light.desk", 0, 3600)
    assert body["complete"] is True and body["incomplete_reasons"] == []
    assert body["state_at_start"] == body["state_at_end"] == "on"
    assert body["transitions"] == 0 and body["first_transition"] is None
    assert body["states"] == [{"state": "on", "duration_seconds": 3600.0, "duration": "1h", "percent": 100.0,
                               "entered": 0, "first_entered": None, "last_entered": None}]
    assert body["recorded_seconds"] == 3600.0 and body["unrecorded_seconds"] == 0.0
    assert body["has_more"] is False and body["next_cursor"] is None and body["other_states"] is None


async def test_transitions_inside_the_window_are_clipped_to_it(recorder_mock, hass: HomeAssistant) -> None:
    """Changes before the start set the baseline, changes after the end are ignored."""
    _anchor(hass)
    for state, seconds in (("on", -7200), ("off", -3600), ("on", 600), ("off", 1800), ("on", 3500), ("off", 4000)):
        _set(hass, "light.desk", state, seconds)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "light.desk", 0, 3600)
    assert body["state_at_start"] == "off" and body["state_at_end"] == "on"
    assert _states(body) == {"off": (2300.0, 1), "on": (1300.0, 2)}
    assert body["transitions"] == 3
    assert body["first_transition"] == _at(600).isoformat() and body["last_transition"] == _at(3500).isoformat()
    on = next(row for row in body["states"] if row["state"] == "on")
    assert on["first_entered"] == _at(600).isoformat() and on["last_entered"] == _at(3500).isoformat()
    assert on["duration"] == "21m 40s" and on["percent"] == 36.11
    assert body["complete"] is True and body["rows_scanned"] == 3


async def test_attribute_updates_and_repeated_states_are_not_transitions(recorder_mock, hass: HomeAssistant) -> None:
    """Core drops attribute-only rows; a forced same-state row reaches the fold and is coalesced."""
    _anchor(hass)
    _set(hass, "light.desk", "on", -3600, {"brightness": 10})
    _set(hass, "light.desk", "on", 100, {"brightness": 200})
    _set(hass, "light.desk", "on", 200, {"brightness": 200}, force_update=True)
    _set(hass, "light.desk", "off", 300, {"brightness": 200})
    await async_wait_recording_done(hass)
    body = await _summary(hass, "light.desk", 0, 600)
    assert body["rows_scanned"] == 2
    assert body["transitions"] == 1
    assert _states(body) == {"on": (300.0, 0), "off": (300.0, 1)}


async def test_unavailable_and_unknown_are_states_not_missing_evidence(recorder_mock, hass: HomeAssistant) -> None:
    _anchor(hass)
    for state, seconds in (("on", -60), ("unavailable", 100), ("unknown", 250), ("on", 300)):
        _set(hass, "sensor.pump", state, seconds)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "sensor.pump", 0, 400)
    assert _states(body) == {"on": (200.0, 1), "unavailable": (150.0, 1), "unknown": (50.0, 1)}
    assert body["complete"] is True and body["unrecorded_seconds"] == 0.0


async def test_an_entity_first_recorded_inside_the_window_leaves_the_start_unrecorded(
    recorder_mock, hass: HomeAssistant,
) -> None:
    """Without a baseline the first row starts recorded time and is not a transition."""
    _anchor(hass)
    _set(hass, "light.new", "on", 600)
    _set(hass, "light.new", "off", 900)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "light.new", 0, 1200)
    assert body["state_at_start"] is None and body["complete"] is False
    assert body["incomplete_reasons"] == ["no_recorded_state_at_start"]
    assert body["recorded_range"] == {"start": _at(600).isoformat(), "end": _at(1200).isoformat()}
    assert body["recorded_seconds"] == 600.0 and body["unrecorded_seconds"] == 600.0
    assert body["transitions"] == 1 and _states(body) == {"on": (300.0, 0), "off": (300.0, 1)}
    assert body["states"][0]["percent"] == 50.0
    assert any("unrecorded, not a state" in warning for warning in body["warnings"])


async def test_a_window_with_no_record_at_all_claims_no_state(recorder_mock, hass: HomeAssistant) -> None:
    _anchor(hass)
    _set(hass, "light.later", "on", 5000)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "light.later", 0, 600)
    assert body["states"] == [] and body["state_at_start"] is None and body["state_at_end"] is None
    assert body["recorded_range"] is None and body["unrecorded_seconds"] == 600.0
    assert body["complete"] is False and body["incomplete_reasons"] == ["no_recorded_state_at_start"]


async def test_a_start_older_than_the_oldest_record_is_unrecorded(recorder_mock, hass: HomeAssistant) -> None:
    """Core withholds the baseline when Recorder holds no state that old, as after a purge."""
    start = -11 * 86400
    _set(hass, "light.desk", "on", start + 600)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "light.desk", start, start + 1200)
    assert body["state_at_start"] is None and body["unrecorded_seconds"] == 600.0
    assert body["complete"] is False
    assert any("short-term Recorder retention" in warning for warning in body["warnings"])


async def test_simultaneous_rows_count_as_the_last_in_recorder_order(recorder_mock, hass: HomeAssistant) -> None:
    _anchor(hass)
    _set(hass, "light.desk", "off", -60)
    _set(hass, "light.desk", "on", 100)
    _set(hass, "light.desk", "off", 100)
    _set(hass, "light.desk", "on", 200)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "light.desk", 0, 300)
    assert body["transitions"] == 1 and _states(body) == {"off": (200.0, 0), "on": (100.0, 1)}
    assert body["rows_scanned"] == 3
    assert any("1 Recorder rows shared a timestamp" in warning for warning in body["warnings"])


def _alternate(hass: HomeAssistant, entity_id: str, count: int) -> None:
    _set(hass, entity_id, "off", -60)
    for index in range(count):
        _set(hass, entity_id, "on" if index % 2 == 0 else "off", 10 + index * 10 + index % 3)


async def test_state_carries_across_internal_pages(recorder_mock, hass: HomeAssistant) -> None:
    _anchor(hass)
    _alternate(hass, "light.desk", 25)
    await async_wait_recording_done(hass)
    whole = await _summary(hass, "light.desk", 0, 400)
    with (patch.object(mcp_view, "HISTORY_SUMMARY_PAGE_ROWS", 3),
          patch.object(mcp_view, "read_state_change_page", wraps=mcp_view.read_state_change_page) as page):
        paged = await _summary(hass, "light.desk", 0, 400)
    assert page.call_count >= 9
    assert paged == whole and whole["transitions"] == 25 and whole["complete"] is True


async def test_sub_microsecond_timestamps_are_read_once_across_pages(recorder_mock, hass: HomeAssistant) -> None:
    """Core stores time.time() floats finer than a microsecond; a page bound must not re-read its last row."""
    _anchor(hass)
    hass.states.async_set("light.desk", "off", timestamp=_at(-60).timestamp())
    for index in range(30):
        stamp = _at(10 + index * 10).timestamp() + (0.0000002 if index % 2 else 0.0000007)
        hass.states.async_set("light.desk", "on" if index % 2 == 0 else "off", timestamp=stamp)
    await async_wait_recording_done(hass)
    whole = await _summary(hass, "light.desk", 0, 400)
    with patch.object(mcp_view, "HISTORY_SUMMARY_PAGE_ROWS", 3):
        paged = await _summary(hass, "light.desk", 0, 400)
    assert whole["rows_scanned"] == paged["rows_scanned"] == 30
    assert paged == whole and whole["transitions"] == 30
    assert not any("shared a timestamp" in warning for warning in whole["warnings"])


async def test_a_spent_budget_returns_a_cursor_whose_continuations_add_up(recorder_mock, hass: HomeAssistant) -> None:
    """Each page summarizes only its covered range; together they equal one unbudgeted scan."""
    _anchor(hass)
    _alternate(hass, "light.desk", 25)
    _set(hass, "light.desk", "on", 20, {"brightness": 5})
    await async_wait_recording_done(hass)
    whole = await _summary(hass, "light.desk", 0, 400)
    totals: dict[str, list[float]] = {}
    transitions, pages, cursor = 0, [], None
    with (patch.object(mcp_view, "HISTORY_SUMMARY_PAGE_ROWS", 3), patch.object(mcp_view, "HISTORY_SUMMARY_MAX_ROWS", 6)):
        while True:
            body = await _summary(hass, "light.desk", 0, 400, **({"cursor": cursor} if cursor else {}))
            pages.append(body)
            transitions += body["transitions"]
            for row in body["states"]:
                totals.setdefault(row["state"], [0.0, 0])
                totals[row["state"]][0] += row["duration_seconds"]
                totals[row["state"]][1] += row["entered"]
            if not body["has_more"]:
                break
            assert body["complete"] is False and body["incomplete_reasons"] == ["scan_limit"]
            assert body["covered_range"]["end"] == body["next_cursor"]
            cursor = body["next_cursor"]
    assert len(pages) > 2 and pages[-1]["complete"] is True
    for previous, following in zip(pages, pages[1:]):
        assert following["state_at_start"] == previous["state_at_end"]
        assert following["covered_range"]["start"] == previous["next_cursor"]
    assert transitions == whole["transitions"]
    assert {state: (round(d, 3), e) for state, (d, e) in totals.items()} == _states(whole)


async def test_the_time_budget_stops_after_one_page(recorder_mock, hass: HomeAssistant) -> None:
    _anchor(hass)
    _alternate(hass, "light.desk", 10)
    await async_wait_recording_done(hass)
    with patch.object(mcp_view, "HISTORY_SUMMARY_PAGE_ROWS", 3), patch.object(mcp_view, "HISTORY_SUMMARY_MAX_SECONDS", 0):
        body = await _summary(hass, "light.desk", 0, 400)
    assert body["has_more"] is True and body["rows_scanned"] < 10
    assert any("stopped at its budget" in warning for warning in body["warnings"])


async def test_many_distinct_states_list_the_longest_and_combine_the_rest(recorder_mock, hass: HomeAssistant) -> None:
    _anchor(hass)
    for index, seconds in enumerate((0.5, 10, 70, 130, 400, 1000)):
        _set(hass, "sensor.mode", f"mode_{index}", seconds)
    await async_wait_recording_done(hass)
    with patch.object(mcp_view, "HISTORY_SUMMARY_MAX_STATES", 3):
        body = await _summary(hass, "sensor.mode", 0, 1200)
    assert [row["state"] for row in body["states"]] == ["mode_4", "mode_3", "mode_5"]
    assert body["other_states"] == {"count": 3, "duration_seconds": 129.5, "duration": "2m 10s",
                                    "percent": 10.8, "entered": 2}
    listed = sum(row["duration_seconds"] for row in body["states"])
    assert listed + body["other_states"]["duration_seconds"] == body["recorded_seconds"] == 1199.5
    assert any("6 distinct states" in warning for warning in body["warnings"])


async def test_zone_counts_stay_separate_states(recorder_mock, hass: HomeAssistant) -> None:
    """Occupied time is the caller's sum of counts above zero, never an invented state."""
    _anchor(hass)
    for state, seconds in (("0", -60), ("1", 100), ("2", 200), ("1", 250), ("0", 300)):
        _set(hass, "zone.home", state, seconds)
    await async_wait_recording_done(hass)
    body = await _summary(hass, "zone.home", 0, 400)
    assert _states(body) == {"0": (200.0, 1), "1": (150.0, 2), "2": (50.0, 1)}


async def test_a_future_end_stops_at_the_current_time(recorder_mock, hass: HomeAssistant) -> None:
    _anchor(hass)
    _set(hass, "light.desk", "on", -60)
    await async_wait_recording_done(hass)
    now = dt_util.utcnow()
    start = (now - BASE).total_seconds() - 3600
    body = await _summary(hass, "light.desk", start, start + 7200)
    covered_end = dt_util.parse_datetime(body["covered_range"]["end"])
    assert covered_end is not None and now <= covered_end <= dt_util.utcnow()
    assert body["requested_range"]["end"] == _at(start + 7200).isoformat()
    assert body["complete"] is True and 3599 < body["recorded_seconds"] < 3700
    assert "end_time is in the future; the summary stops at the current time." in body["warnings"]


@pytest.mark.parametrize("recorder_config", [{"exclude": {"entities": ["light.secret"]}}])
async def test_an_entity_recorder_excludes_is_refused(recorder_mock, hass: HomeAssistant) -> None:
    """Carrying an unrecorded entity's last row forward would invent its durations."""
    _anchor(hass)
    _set(hass, "light.secret", "on", -60)
    _set(hass, "light.desk", "on", -60)
    await async_wait_recording_done(hass)
    result = await _call(hass, "light.secret", 0, 600)
    assert result[1] == "invalid_request"
    assert "does not record this entity" in result[0]["content"][0]["text"]
    assert (await _summary(hass, "light.desk", 0, 600))["complete"] is True


async def test_access_is_rechecked_after_the_scan(recorder_mock, hass: HomeAssistant) -> None:
    """Access lost while the scan runs withholds the summary as if the entity did not exist."""
    _anchor(hass)
    _set(hass, "light.desk", "on", -60)
    await async_wait_recording_done(hass)
    with patch.object(mcp_view, "resolve_read", side_effect=[Permission.READ, Permission.NO_ACCESS]) as resolve:
        result = await _call(hass, "light.desk", 0, 600)
    assert resolve.call_count == 2
    assert result[1] == "denied" and result[0]["content"][0]["text"] == "Entity not found."


@pytest.mark.parametrize("extra", [{"limit": 10}, {"order": "newest_first"}, {"order": "oldest_first"}])
async def test_row_paging_arguments_are_refused(recorder_mock, hass: HomeAssistant, extra: dict) -> None:
    _set(hass, "light.desk", "on", -60)
    result = await _call(hass, "light.desk", 0, 600, **extra)
    assert result[1] == "invalid_request" and "do not apply to summary mode" in result[0]["content"][0]["text"]


async def test_hidden_entities_are_not_found_before_any_scan(recorder_mock, hass: HomeAssistant) -> None:
    _set(hass, "switch.hidden", "on", -60)
    with patch.object(mcp_view, "read_state_change_page") as page:
        result = await _call(hass, "switch.hidden", 0, 600)
    assert result[1] == "denied" and result[0]["content"][0]["text"] == "Entity not found."
    page.assert_not_called()


def test_cuts_never_split_a_timestamp_group() -> None:
    assert group_cut([(1, "a"), (1, "b"), (5, "c")]) == 1
    assert group_cut([(3, "a"), (3, "b"), (3, "c")]) is None
    assert cursor_cut([(1, "a"), (1, "b"), (5, "c")]) is None
    assert cursor_cut([(1, "a"), (2, "b"), (3, "c")]) is None
    assert cursor_cut([(1, "a"), (3, "b"), (4, "c")]) == 0
    assert cursor_cut([(1, "a"), (3, "b"), (5, "c")]) == 1


class _Recorder:
    """The state-change query's contract over (timestamp, state) rows in insertion order."""

    def __init__(self, rows: list[tuple[int, str]]) -> None:
        self.rows = sorted(rows, key=lambda row: row[0])
        self.queries = 0

    async def fetch(self, after: int, before: int, limit: int, include_start: bool) -> list[tuple[int, str]]:
        self.queries += 1
        page = [row for row in self.rows if after < row[0] < before][:limit]
        earlier = [row for row in self.rows if row[0] < after]
        return ([(after, earlier[-1][1])] if include_start and earlier else []) + page


def _reference(rows: list[tuple[int, str]], start: int, end: int) -> tuple[dict[str, list[int]], int]:
    """Durations and entries by direct interval arithmetic over the last row per timestamp."""
    last_at: dict[int, str] = {}
    for stamp, state in sorted(rows, key=lambda row: row[0]):
        last_at[stamp] = state
    before = [stamp for stamp in last_at if stamp < start]
    points = ([(start, last_at[max(before)])] if before else []) + sorted(
        (stamp, state) for stamp, state in last_at.items() if start < stamp < end)
    totals: dict[str, list[int]] = {}
    transitions = 0
    for index, (stamp, state) in enumerate(points):
        following = points[index + 1][0] if index + 1 < len(points) else end
        totals.setdefault(state, [0, 0])[0] += following - stamp
        if index and state != points[index - 1][1]:
            totals[state][1] += 1
            transitions += 1
    return totals, transitions


@pytest.mark.parametrize("seed", range(200))
async def test_budgeted_continuations_match_direct_arithmetic(seed: int) -> None:
    """Ties, one-microsecond gaps and repeated states never shift time between pages."""
    rng = random.Random(seed)
    stamp, rows = 0, []
    for _ in range(rng.randint(0, 120)):
        stamp += rng.choice((0, 1, 1, 2, 3, 5, 7, 9, 50, 1000))
        rows.append((stamp, rng.choice("abc")))
    start, end = rng.randint(-5, 200), stamp + rng.randint(1, 500)
    recorder = _Recorder(rows)
    page_rows = rng.randint(4, 8)
    totals: dict[str, list[int]] = {}
    transitions, lower, calls = 0, start, 0
    while True:
        calls += 1
        summary = await scan_state_changes(recorder.fetch, lower, end, page_rows=page_rows,
                                           max_rows=page_rows * rng.randint(2, 3), max_seconds=60)
        transitions += summary.transitions
        for state, state_totals in summary.totals.items():
            entry = totals.setdefault(state, [0, 0])
            entry[0] += state_totals.duration_us
            entry[1] += state_totals.entered
        if summary.next_cursor_us is None:
            break
        assert lower < summary.next_cursor_us < end
        assert all(row[0] != summary.next_cursor_us for row in rows)
        lower = summary.next_cursor_us
    expected, expected_transitions = _reference(rows, start, end)
    assert {state: entry for state, entry in totals.items() if entry != [0, 0]} == {
        state: entry for state, entry in expected.items() if entry != [0, 0]}
    assert transitions == expected_transitions
    assert recorder.queries <= calls * 2 * 3


async def test_a_page_filled_by_one_timestamp_is_refused_not_guessed() -> None:
    recorder = _Recorder([(5, "a")] + [(10, "b"), (10, "a")] * 3)
    with pytest.raises(SummaryUnavailableError):
        await scan_state_changes(recorder.fetch, 0, 100, page_rows=4, max_rows=400, max_seconds=60)


async def test_a_spent_budget_can_stop_just_before_a_page_without_a_cursor() -> None:
    """The last folded row is alone and earlier than the page, so the cursor goes between them."""
    recorder = _Recorder([(5, "a"), (10, "b"), (10, "a"), (10, "b"), (10, "a"), (10, "b"), (20, "a")])
    summary = await scan_state_changes(recorder.fetch, 0, 100, page_rows=4, max_rows=8, max_seconds=60)
    assert summary.next_cursor_us == 7 and summary.rows_scanned == 1
    assert summary.current == "a" and summary.totals["a"].duration_us == 2
