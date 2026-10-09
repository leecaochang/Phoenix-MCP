"""Bounded time, cursor, retention, and response helpers for Recorder tools."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
import logging
from datetime import datetime, timedelta
from collections.abc import Mapping
from typing import Any, Literal, cast

from homeassistant.util import dt as dt_util

from .helpers import parse_time_param

_LOGGER = logging.getLogger(__name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
MAX_SIGNIFICANT_STATES_RANGE = timedelta(days=7)

STATISTIC_PERIOD_LIMITS = {
    "5minute": 1000,
    "hour": 1000,
    "day": 366,
    "week": 52,
    "month": 12,
    "year": 1,
}


@dataclass(frozen=True, slots=True)
class RecorderWindow:
    """A validated requested range and the page position within it."""

    start: datetime
    end: datetime
    page_start: datetime
    limit: int


def _as_utc(value: datetime) -> datetime:
    """Normalize an aware timestamp to UTC; a naive one raises ValueError."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone.")
    return dt_util.as_utc(value)


def parse_recorder_window(
    args: dict[str, Any], *, default_range: timedelta, now: datetime | None = None
) -> RecorderWindow:
    """Parse the shared Recorder range, cursor and limit inputs.

    Raises ValueError with a client-safe message on a malformed or
    inconsistent value.
    """
    effective_now = _as_utc(now or dt_util.utcnow())
    end = effective_now
    if args.get("end_time"):
        try:
            end = _as_utc(parse_time_param(args["end_time"]))
        except (TypeError, ValueError) as err:
            raise ValueError("Invalid end_time format.") from err

    start = end - default_range
    if args.get("start_time"):
        try:
            start = _as_utc(parse_time_param(args["start_time"]))
        except (TypeError, ValueError) as err:
            raise ValueError("Invalid start_time format.") from err
    if start >= end:
        raise ValueError("start_time must be earlier than end_time.")

    page_start = start
    if args.get("cursor"):
        try:
            raw_cursor = args["cursor"]
            parsed_cursor = dt_util.parse_datetime(raw_cursor) if isinstance(raw_cursor, str) else None
            if parsed_cursor is None:
                raise ValueError
            page_start = _as_utc(parsed_cursor)
        except (TypeError, ValueError) as err:
            raise ValueError("Invalid cursor format.") from err
        if not start <= page_start < end:
            raise ValueError("cursor must fall within the requested time range.")

    raw_limit = args.get("limit", DEFAULT_LIMIT)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise ValueError("limit must be an integer.")
    if not 1 <= raw_limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}.")
    return RecorderWindow(start=start, end=end, page_start=page_start, limit=raw_limit)


NEWEST_PAGE_MAX_PROBES = 40


async def newest_page(fetch: Any, start: datetime, end: datetime, limit: int) -> tuple[list[Any], bool]:
    """Return the newest rows in (start, end), oldest first, without unbounded reads.

    `fetch(lower, end, n)` returns at most n ascending rows strictly after lower.
    Recorder can only bound a query from its oldest end, so this narrows the lower
    bound by bisection until the window holds no more than `limit` rows. Every
    probe is limited to limit + 1 rows. Returns (rows, has_more); has_more means
    older rows remain before the returned ones.
    """
    rows = await fetch(start, end, limit + 1)
    if len(rows) <= limit:
        return rows, False
    too_wide, fits = start, end
    best: list[Any] = []
    for _ in range(NEWEST_PAGE_MAX_PROBES):
        middle = too_wide + (fits - too_wide) / 2
        if middle in (too_wide, fits):
            break
        rows = await fetch(middle, end, limit + 1)
        if len(rows) > limit:
            too_wide = middle
        else:
            fits, best = middle, rows
            if len(rows) == limit:
                break
    return best, True


def iso_utc(value: datetime) -> str:
    return dt_util.as_utc(value).isoformat()


def state_timestamp(row: Mapping[str, Any]) -> datetime | None:
    """Return the row's last_changed (else last_updated) as UTC, or None if absent or unparsable."""
    value = row.get("last_changed") or row.get("last_updated")
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        try:
            return _as_utc(parse_time_param(value))
        except ValueError:
            return None
    return None


def statistic_timestamp(row: Mapping[str, Any]) -> datetime | None:
    """Return the bucket `start` (datetime, epoch seconds or ISO string) as UTC, or None if unparsable."""
    value = row.get("start")
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=dt_util.UTC)
    if isinstance(value, str):
        try:
            return _as_utc(parse_time_param(value))
        except ValueError:
            return None
    return None


def retention_metadata(kind: Literal["short_term", "long_term"], keep_days: int) -> dict[str, Any]:
    """Describe which Recorder store backs a response."""
    if kind == "short_term":
        return {"kind": kind, "configured_days": keep_days}
    return {"kind": kind}


def retention_warnings(
    kind: Literal["short_term", "long_term"], start: datetime, keep_days: int, now: datetime
) -> list[str]:
    """Warn when a short-term request starts before the configured Recorder retention."""
    if kind == "short_term" and start < now - timedelta(days=keep_days):
        return [
            f"The requested start is older than the configured {keep_days}-day short-term Recorder retention; earlier data may be unavailable."
        ]
    return []


def recorder_envelope(
    *,
    entity_id: str,
    window: RecorderWindow,
    covered_start: datetime,
    covered_end: datetime,
    rows: list[dict[str, Any]],
    timestamp_getter: Any,
    effective_limit: int,
    has_more: bool,
    next_cursor: datetime | None,
    retention: dict[str, Any],
    warnings: list[str],
    result_key: Literal["history", "statistics"],
) -> dict[str, Any]:
    """Build the common cursor-based Recorder response contract."""
    timestamps = [stamp for row in rows if (stamp := timestamp_getter(row)) is not None]
    data_range = None
    if timestamps:
        data_range = {"start": iso_utc(timestamps[0]), "end": iso_utc(timestamps[-1])}
    return {
        "entity_id": entity_id,
        "requested_range": {"start": iso_utc(window.start), "end": iso_utc(window.end)},
        "covered_range": {"start": iso_utc(covered_start), "end": iso_utc(covered_end)},
        "data_range": data_range,
        "count": len(rows),
        "limit": window.limit,
        "effective_limit": effective_limit,
        "has_more": has_more,
        "next_cursor": iso_utc(next_cursor) if next_cursor is not None else None,
        "retention": retention,
        "warnings": warnings,
        result_key: rows,
    }


def floor_statistic_period(value: datetime, period: str) -> datetime:
    """Floor a timestamp to a Home Assistant local calendar period."""
    local = dt_util.as_local(value)
    if period == "5minute":
        local = local.replace(minute=local.minute - local.minute % 5, second=0, microsecond=0)
    elif period == "hour":
        local = local.replace(minute=0, second=0, microsecond=0)
    elif period == "day":
        local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        local = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday())
    elif period == "month":
        local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "year":
        local = local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Unsupported statistics period: {period}")
    return dt_util.as_utc(local)


def add_statistic_periods(value: datetime, period: str, count: int) -> datetime:
    """Advance by local calendar periods without losing DST alignment."""
    if period == "5minute":
        return dt_util.as_utc(value) + timedelta(minutes=5 * count)
    elif period == "hour":
        return dt_util.as_utc(value) + timedelta(hours=count)

    local = dt_util.as_local(value)
    if period == "day":
        result = local + timedelta(days=count)
    elif period == "week":
        result = local + timedelta(weeks=count)
    elif period in ("month", "year"):
        months = count if period == "month" else count * 12
        month_index = local.year * 12 + local.month - 1 + months
        year, zero_month = divmod(month_index, 12)
        month = zero_month + 1
        result = local.replace(year=year, month=month, day=min(local.day, monthrange(year, month)[1]))
    else:
        raise ValueError(f"Unsupported statistics period: {period}")
    return dt_util.as_utc(result)


def ceil_statistic_period(value: datetime, period: str) -> datetime:
    """Ceil a timestamp to the next local calendar bucket boundary."""
    floor = floor_statistic_period(value, period)
    if floor == value:
        return floor
    return add_statistic_periods(floor, period, 1)


def statistic_page(window: RecorderWindow, period: str) -> tuple[datetime, datetime, int, bool]:
    """Return (page_start, page_end, effective_limit, has_more) for an aligned statistics page.

    The page is capped at the period's row limit and never extends past the
    requested end rounded up to a bucket boundary.
    """
    effective_limit = min(window.limit, STATISTIC_PERIOD_LIMITS[period])
    page_start = floor_statistic_period(window.page_start, period)
    requested_end = ceil_statistic_period(window.end, period)
    page_end = min(add_statistic_periods(page_start, period, effective_limit), requested_end)
    return page_start, page_end, effective_limit, page_end < requested_end


def read_statistics_page(
    hass: Any, start: datetime, end: datetime, statistic_id: str,
    period: Literal["5minute", "day", "hour", "week", "month", "year"], types: set[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Read one statistics page with Core's unit label. Recorder executor only.

    Returns (rows, units, warnings). Core converts values to the unit its own
    get_display_unit reports, so that is the label; it comes from Recorder
    metadata rather than the rows, so an empty page is labelled too, and None
    means no metadata exists. Core 2026.7 to 2026.10 also passes last_reset
    timestamps through that value converter. When a conversion happened, resets
    are re-read with every unit class pinned to the stored unit, where Core
    converts nothing; a reset that cannot be re-read is omitted, never returned
    converted.
    """
    from homeassistant.components.recorder import statistics as recorder_stats

    result = recorder_stats.statistics_during_period(hass, start, end, {statistic_id}, period, None, types)
    rows = [dict(row) for row in result.get(statistic_id, [])]
    try:
        metadata = recorder_stats.get_metadata(hass, statistic_ids={statistic_id}).get(statistic_id)
    except Exception:  # noqa: BLE001 - the values stay usable without their label
        _LOGGER.warning("Recorder metadata for %s could not be read", statistic_id, exc_info=True)
        dropped = [row.pop("last_reset", None) for row in rows]
        dropped_any = any(value is not None for value in dropped)
        return rows, None, [
            "The unit could not be read from Recorder metadata, so these values are unlabelled."
            + (" last_reset was omitted because it may carry a unit conversion." if dropped_any else "")
        ]
    if metadata is None:
        return rows, None, []
    stored = metadata[1].get("unit_of_measurement")
    unit_class = metadata[1].get("unit_class")
    display = recorder_stats.get_display_unit(hass, statistic_id, unit_class, stored)
    units = {
        "display_unit_of_measurement": display,
        "statistics_unit_of_measurement": stored,
        "unit_class": unit_class,
    }
    if display == stored or not any(row.get("last_reset") is not None for row in rows):
        return rows, units, []
    # A unitless stored unit is None, which Core's converters accept as a unit.
    same_unit = cast(dict[str, str], {
        converter.UNIT_CLASS: stored for converter in recorder_stats.UNIT_CLASS_TO_UNIT_CONVERTER.values()})
    try:
        reset_rows = recorder_stats.statistics_during_period(
            hass, start, end, {statistic_id}, period, same_unit, {"last_reset"}).get(statistic_id, [])
        resets: dict[Any, Any] = {row["start"]: row.get("last_reset") for row in reset_rows}
    except Exception:  # noqa: BLE001 - omit rather than return converted timestamps
        _LOGGER.warning("Unconverted last_reset values for %s could not be read", statistic_id, exc_info=True)
        resets = {}
    omitted = False
    for row in rows:
        if "last_reset" not in row:
            continue
        if row.get("start") in resets:
            row["last_reset"] = resets[row["start"]]
        else:
            omitted |= row.pop("last_reset") is not None
    warnings = ["last_reset was omitted from some rows because its unconverted value could not be read."] if omitted else []
    return rows, units, warnings
