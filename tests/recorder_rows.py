"""Read the CSV rows of a get_history or get_statistics page back as dictionaries for assertions."""

from __future__ import annotations

import csv
import io
from typing import Any

from homeassistant.util import dt as dt_util


def page_rows(body: dict[str, Any], key: str = "history") -> list[dict[str, str]]:
    """The page's <key>_csv rows as dictionaries of cell text; an empty cell stays an empty string."""
    return list(csv.DictReader(io.StringIO(body[f"{key}_csv"])))


def instant(value: str) -> Any:
    """An ISO time as an aware datetime, so local and UTC renderings of one instant compare equal."""
    parsed = dt_util.parse_datetime(value)
    assert parsed is not None, value
    return parsed


def to_second(value: str) -> Any:
    """An ISO time as an aware datetime truncated to the second, as CSV rows carry it."""
    return instant(value).replace(microsecond=0)
