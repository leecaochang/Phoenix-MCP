"""Timestamps read from text that Phoenix does not own."""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.util import dt as dt_util


def _encodable(value: object) -> bool:
    """Whether text encodes to UTF-8. Anything else is left for the parser to refuse."""
    if not isinstance(value, str):
        return True
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# ciso8601 dereferences a failed UTF-8 conversion, so a lone surrogate, which
# json.loads makes from a \ud800 escape, ends the process in C where no handler runs.
def parse_datetime(value: str) -> datetime | None:
    """Home Assistant's parser, answering None for text that does not encode to UTF-8."""
    return dt_util.parse_datetime(value) if _encodable(value) else None


def parse_date(value: str) -> date | None:
    """Home Assistant's date parser, under the same refusal as `parse_datetime`."""
    return dt_util.parse_date(value) if _encodable(value) else None
