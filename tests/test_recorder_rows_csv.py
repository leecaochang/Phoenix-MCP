"""Row pages of get_history and get_statistics as one CSV table inside the JSON envelope."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime, timezone
import io
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.phoenix_mcp.recorder_queries import compact_row_page, rows_csv


@pytest.fixture
def tokyo() -> Iterator[None]:
    """Home Assistant's timezone set to one without daylight saving, restored afterwards."""
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(ZoneInfo("Asia/Tokyo"))
    try:
        yield
    finally:
        dt_util.set_default_time_zone(previous)


def _read(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_history_rows_are_local_whole_seconds_with_the_time_first(tokyo) -> None:
    text = rows_csv([{"state": "27.5612345", "when": "2026-11-10T09:05:25.308240+00:00"},
                     {"state": "off", "when": "2026-11-10T09:05:25.999999+00:00"}])
    # A state is text, so a numeric-looking state keeps every digit.
    assert text == ("when,state\n2026-11-10T18:05:25+09:00,27.5612345\n2026-11-10T18:05:25+09:00,off\n")


def test_statistics_numbers_keep_four_significant_digits_and_two_decimals(tokyo) -> None:
    start = datetime(2026, 11, 4, 9, tzinfo=timezone.utc)
    rows = [{"start": start.isoformat(), "end": "2026-11-04T10:00:00+00:00", "mean": mean, "min": 19.0,
             "max": 1234.5678, "last_reset": None} for mean in (31.43030787105364, 0.0042137, 1.23456, 0.0, -7.3333)]
    table = _read(rows_csv(rows))
    assert list(table[0]) == ["start", "mean", "min", "max"], "end and an all-null last_reset are left out"
    assert [row["mean"] for row in table] == ["31.43", "0.004214", "1.235", "0.0", "-7.333"]
    assert table[0]["min"] == "19.0" and table[0]["max"] == "1234.57"
    assert table[0]["start"] == "2026-11-04T18:00:00+09:00"


def test_a_column_with_any_value_is_kept_and_its_nulls_are_empty_cells(tokyo) -> None:
    rows = [{"start": "2026-11-04T00:00:00+00:00", "sum": 2.0, "last_reset": None},
            {"start": "2026-11-04T01:00:00+00:00", "sum": 3.0, "last_reset": "2026-11-03T15:00:00+00:00"}]
    table = _read(rows_csv(rows))
    assert list(table[0]) == ["start", "sum", "last_reset"]
    assert table[0]["last_reset"] == "" and table[1]["last_reset"] == "2026-11-04T00:00:00+09:00"


def test_states_with_separators_quotes_and_line_breaks_read_back_unchanged() -> None:
    states = ['a, b', 'say "hi"', "two\nlines", ""]
    rows = [{"state": state, "when": None} for state in states]
    assert [row["state"] for row in _read(rows_csv(rows))] == states


def test_no_rows_is_an_empty_table() -> None:
    assert rows_csv([]) == ""


def test_a_page_keeps_its_exact_utc_cursor_and_reads_its_ranges_locally(tokyo) -> None:
    cursor = "2026-11-10T09:05:25.308241+00:00"
    body = {"entity_id": "sensor.t", "requested_range": {"start": "2026-11-10T00:00:00+00:00", "end": cursor},
            "data_range": None, "unread_range": {"start": cursor, "end": "2026-11-11T00:00:00+00:00"},
            "next_cursor": cursor, "count": 1, "history": [{"state": "1", "when": cursor}]}
    page = compact_row_page(body, "history")
    assert "history" not in page and page["history_csv"] == "when,state\n2026-11-10T18:05:25+09:00,1\n"
    assert page["next_cursor"] == cursor, "the cursor continues the page, so it is never rounded or moved"
    assert page["unread_range"]["start"] == "2026-11-10T18:05:25.308241+09:00"
    assert page["requested_range"]["start"] == "2026-11-10T09:00:00+09:00" and page["data_range"] is None
    assert body["history"], "the source body is left as it was"
