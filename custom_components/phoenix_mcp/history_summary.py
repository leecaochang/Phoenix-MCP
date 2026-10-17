"""Per-state durations and transition counts for get_history's summary mode.

The fold reads attribute-free Recorder state changes in bounded pages and returns
no rows. The state in effect at the window start is a baseline, never a
transition. A row repeating the current state (a restart rewrite or a forced
update) is not a transition, and rows sharing one timestamp count as the last of
them in Recorder order. Time before the entity's first record is unrecorded,
never attributed to a state. A scan that reaches its page or wall-clock budget
ends on a cursor strictly between two recorded changes, so the continuation's own
Recorder baseline is the carried state. Times are UTC epoch microseconds so
durations add exactly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
import time
from typing import Any

from homeassistant.util import dt as dt_util

_EPOCH = datetime(1970, 1, 1, tzinfo=dt_util.UTC)
_MICROSECOND = timedelta(microseconds=1)

Row = tuple[int, str]
# fetch(after_us, before_us, limit, include_start_state) -> ascending rows.
Fetch = Callable[[int, int, int, bool], Awaitable[list[Row]]]


class SummaryUnavailableError(Exception):
    """The bounded scan found no safe page end, or no safe cursor once over budget."""


def to_us(value: datetime) -> int:
    return (dt_util.as_utc(value) - _EPOCH) // _MICROSECOND


def from_us(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value)


def stamp_us(timestamp: float) -> int:
    """The smallest microsecond n whose bound from_us(n) is not earlier than a stored timestamp.

    Core stores float timestamps finer than a microsecond and compares them with
    the bound's datetime.timestamp(), which equals n / 1_000_000. With this
    mapping a lower bound of from_us(n) excludes exactly the rows stamped n or
    earlier, so a page boundary never reads its last row again.
    """
    stamp = math.ceil(timestamp * 1_000_000)
    while (stamp - 1) / 1_000_000 >= timestamp:
        stamp -= 1
    while stamp / 1_000_000 < timestamp:
        stamp += 1
    return stamp


def read_state_change_page(
    hass: Any, entity_id: str, after_us: int, before_us: int, limit: int, include_start_state: bool,
) -> list[Row]:
    """One ascending page of attribute-free state changes. Recorder executor only.

    Core's query drops attribute-only updates (last_changed differs from
    last_updated) and reads rows strictly inside (after, before). With
    include_start_state it adds the latest earlier row stamped at `after`, or
    nothing when Recorder holds no state that old.
    """
    from homeassistant.components.recorder import history

    states = history.state_changes_during_period(
        hass, from_us(after_us), from_us(before_us), entity_id, True, False, limit, include_start_state,
    ).get(entity_id, [])
    return [(stamp_us(state.last_updated_timestamp), state.state) for state in states]


def group_cut(rows: list[Row]) -> int | None:
    """The last index of a full page that ends a timestamp group, or None.

    The page's final row may belong to a group that continues past the page, so
    a page never ends on it; None means one timestamp fills the page.
    """
    for index in range(len(rows) - 2, -1, -1):
        if rows[index + 1][0] > rows[index][0]:
            return index
    return None


def cursor_cut(rows: list[Row]) -> int | None:
    """The last index a scan may stop on and hand out a cursor, or None.

    The row must be alone at its timestamp, so Recorder's own baseline query for
    the continuation picks it unambiguously, and at least two microseconds before
    the next row, so a cursor fits strictly between them. A page's first row is
    never tied with folded rows, which all precede the page's lower bound.
    """
    for index in range(len(rows) - 2, -1, -1):
        stamp = rows[index][0]
        if rows[index + 1][0] - stamp >= 2 and (index == 0 or rows[index - 1][0] != stamp):
            return index
    return None


@dataclass(slots=True)
class StateTotals:
    duration_us: int = 0
    entered: int = 0
    first_entered_us: int | None = None
    last_entered_us: int | None = None


@dataclass(slots=True)
class StateSummary:
    """The running fold of one scan over [start_us, end_us)."""

    start_us: int
    end_us: int = 0
    state_at_start: str | None = None
    first_recorded_us: int | None = None
    current: str | None = None
    since_us: int = 0
    transitions: int = 0
    first_transition_us: int | None = None
    last_transition_us: int | None = None
    totals: dict[str, StateTotals] = field(default_factory=dict)
    rows_scanned: int = 0
    simultaneous: int = 0
    last_group_single: bool = False
    next_cursor_us: int | None = None

    def baseline(self, state: str) -> None:
        self.state_at_start = self.current = state
        self.since_us = self.start_us
        self.totals.setdefault(state, StateTotals())

    def feed(self, rows: list[Row]) -> None:
        index = 0
        while index < len(rows):
            last = index
            while last + 1 < len(rows) and rows[last + 1][0] == rows[index][0]:
                last += 1
            self.simultaneous += last - index
            self.rows_scanned += last - index + 1
            self.last_group_single = last == index
            self._apply(*rows[last])
            index = last + 1

    def _apply(self, stamp: int, state: str) -> None:
        if self.current is None:
            self.first_recorded_us = self.since_us = stamp
            self.current = state
            self.totals.setdefault(state, StateTotals())
            return
        if state == self.current:
            return
        self.totals[self.current].duration_us += stamp - self.since_us
        entered = self.totals.setdefault(state, StateTotals())
        entered.entered += 1
        if entered.first_entered_us is None:
            entered.first_entered_us = stamp
        entered.last_entered_us = stamp
        self.transitions += 1
        if self.first_transition_us is None:
            self.first_transition_us = stamp
        self.last_transition_us = stamp
        self.current, self.since_us = state, stamp

    def close(self, end_us: int) -> None:
        self.end_us = end_us
        if self.current is not None:
            self.totals[self.current].duration_us += end_us - self.since_us
            self.since_us = end_us

    def stop_at(self, cursor_us: int) -> StateSummary:
        self.close(cursor_us)
        self.next_cursor_us = cursor_us
        return self

    @property
    def recorded_start_us(self) -> int | None:
        return self.start_us if self.state_at_start is not None else self.first_recorded_us


async def scan_state_changes(
    fetch: Fetch, start_us: int, end_us: int, *, page_rows: int, max_rows: int, max_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> StateSummary:
    """Fold [start_us, end_us) page by page within the row and time budgets.

    Each query asks for page_rows + 1 rows; a page that is not full reaches
    end_us. A full page is folded up to its group_cut and the next page starts
    strictly after that row. Once a budget is spent the scan stops at the page's
    cursor_cut, else just before the page when the last folded row was alone and
    two microseconds earlier; failing both it folds on, at most to twice the page
    budget. Raises SummaryUnavailableError when no cut remains.
    """
    summary = StateSummary(start_us=start_us)
    began = clock()
    max_pages = max(1, max_rows // page_rows)
    lower, pages = start_us, 0
    while True:
        rows = await fetch(lower, end_us, page_rows + 1, pages == 0)
        if pages == 0 and rows and rows[0][0] <= start_us:
            summary.baseline(rows[0][1])
            rows = rows[1:]
        pages += 1
        if len(rows) <= page_rows:
            summary.feed(rows)
            summary.close(end_us)
            return summary
        if pages >= max_pages or clock() - began >= max_seconds:
            cut = cursor_cut(rows)
            if cut is not None:
                summary.feed(rows[:cut + 1])
                return summary.stop_at((rows[cut][0] + rows[cut + 1][0]) // 2)
            if lower > start_us and summary.last_group_single and rows[0][0] - lower >= 2:
                return summary.stop_at((lower + rows[0][0]) // 2)
            if pages >= 2 * max_pages:
                raise SummaryUnavailableError
        cut = group_cut(rows)
        if cut is None:
            raise SummaryUnavailableError
        summary.feed(rows[:cut + 1])
        lower = rows[cut][0]


def _seconds(value: int) -> float:
    return round(value / 1_000_000, 3)


def _duration_text(value: int) -> str:
    """Whole-second duration such as 1d 2h 5s, omitting zero units."""
    seconds = round(value / 1_000_000)
    if seconds == 0:
        return "0s" if value == 0 else "<1s"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, rest = divmod(rest, 60)
    return " ".join(f"{amount}{unit}" for amount, unit in (
        (days, "d"), (hours, "h"), (minutes, "m"), (rest, "s")) if amount)


def _iso(value: int | None) -> str | None:
    return from_us(value).isoformat() if value is not None else None


def _percent(duration: int, recorded: int) -> float | None:
    return round(duration * 100 / recorded, 2) if recorded else None


def summary_body(
    entity_id: str, requested: tuple[datetime, datetime], summary: StateSummary, *,
    max_states: int, retention: dict[str, Any], warnings: list[str],
) -> dict[str, Any]:
    """The summary response; durations and percentages cover recorded time only."""
    recorded_start = summary.recorded_start_us
    recorded = summary.end_us - recorded_start if recorded_start is not None else 0
    ranked = sorted(summary.totals.items(), key=lambda item: (-item[1].duration_us, item[0]))
    listed, rest = ranked[:max_states], ranked[max_states:]
    reasons = []
    if summary.state_at_start is None:
        reasons.append("no_recorded_state_at_start")
        warnings.append(
            "Recorder has no state for this entity before "
            + (f"{_iso(recorded_start)}; the time before it" if recorded_start is not None else "the end of this range; the whole range")
            + " is unrecorded, not a state."
        )
    if summary.next_cursor_us is not None:
        reasons.append("scan_limit")
        warnings.append(
            "The scan stopped at its budget. Continue with next_cursor as cursor and add each page's durations and transitions."
        )
    if summary.simultaneous:
        warnings.append(
            f"{summary.simultaneous} Recorder rows shared a timestamp with a later row; each such group counts as its last row in Recorder order."
        )
    if rest:
        warnings.append(
            f"{len(ranked)} distinct states: the {max_states} longest are listed and the rest are combined in other_states. For a numeric sensor use get_statistics."
        )
    other = None
    if rest:
        duration = sum(totals.duration_us for _, totals in rest)
        other = {
            "count": len(rest),
            "duration_seconds": _seconds(duration),
            "duration": _duration_text(duration),
            "percent": _percent(duration, recorded),
            "entered": sum(totals.entered for _, totals in rest),
        }
    return {
        "entity_id": entity_id,
        "mode": "summary",
        "requested_range": {"start": requested[0].isoformat(), "end": requested[1].isoformat()},
        "covered_range": {"start": _iso(summary.start_us), "end": _iso(summary.end_us)},
        "recorded_range": (
            {"start": _iso(recorded_start), "end": _iso(summary.end_us)} if recorded_start is not None else None
        ),
        "complete": not reasons,
        "incomplete_reasons": reasons,
        "state_at_start": summary.state_at_start,
        "state_at_end": summary.current,
        "recorded_seconds": _seconds(recorded),
        "unrecorded_seconds": _seconds(summary.end_us - summary.start_us - recorded),
        "transitions": summary.transitions,
        "first_transition": _iso(summary.first_transition_us),
        "last_transition": _iso(summary.last_transition_us),
        "states": [
            {
                "state": state,
                "duration_seconds": _seconds(totals.duration_us),
                "duration": _duration_text(totals.duration_us),
                "percent": _percent(totals.duration_us, recorded),
                "entered": totals.entered,
                "first_entered": _iso(totals.first_entered_us),
                "last_entered": _iso(totals.last_entered_us),
            }
            for state, totals in listed
        ],
        "other_states": other,
        "rows_scanned": summary.rows_scanned,
        "has_more": summary.next_cursor_us is not None,
        "next_cursor": _iso(summary.next_cursor_us),
        "retention": retention,
        "warnings": warnings,
    }
