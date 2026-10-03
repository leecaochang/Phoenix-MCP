"""Durable aggregate turn accounting for Phoenix Chat and Voice, without transcripts."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from typing import Any
from uuid import uuid4

from homeassistant.util.dt import utcnow, parse_datetime

from .const import ROUTING_STATISTICS_OUTCOMES, ROUTING_STATISTICS_SURFACES


def _empty() -> dict:
    return {"since": utcnow().isoformat(), "generation": uuid4().hex,
            "outcomes": dict.fromkeys(ROUTING_STATISTICS_OUTCOMES, 0), "provider_turns": 0,
            "decision_usage": _usage(), "provider_usage": _usage()}


def _usage() -> dict:
    return dict.fromkeys(("requests", "reports", "input_tokens", "output_tokens"), 0)


def _valid_count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**53 - 1


class RoutingStatistics:
    """Only fixed counters persist; resets invalidate in-flight turns for that surface."""

    def __init__(self, store=None):
        self.store = store
        self.surfaces = {surface: _empty() for surface in ROUTING_STATISTICS_SURFACES}
        self.lock = asyncio.Lock()
        self.revision = 0
        self.saved_revision = 0

    async def async_load(self) -> None:
        raw = await self.store.async_load() if self.store else None
        if not isinstance(raw, dict):
            return
        for surface in ROUTING_STATISTICS_SURFACES:
            row = raw.get(surface)
            if not isinstance(row, dict) or not isinstance(row.get("since"), str) or parse_datetime(row["since"]) is None:
                continue
            fresh = _empty()
            fresh["since"] = row["since"]
            fresh["provider_turns"] = row.get("provider_turns", 0) if _valid_count(row.get("provider_turns")) else 0
            for group in ("outcomes", "decision_usage", "provider_usage"):
                values = row.get(group, {})
                if isinstance(values, dict):
                    for key in fresh[group]:
                        if _valid_count(values.get(key)):
                            fresh[group][key] = values[key]
            self.surfaces[surface] = fresh

    def summary(self) -> dict:
        return {surface: {k: deepcopy(v) for k, v in row.items() if k != "generation"}
                for surface, row in self.surfaces.items()}

    async def async_save(self) -> None:
        async with self.lock:
            if self.store and self.saved_revision != self.revision:
                revision = self.revision
                await self.store.async_save(self.summary())
                self.saved_revision = revision

    async def async_reset(self, surface: str | None = None) -> None:
        async with self.lock:
            proposed = deepcopy(self.surfaces)
            for name in (ROUTING_STATISTICS_SURFACES if surface is None else (surface,)):
                proposed[name] = _empty()
            if self.store:
                await self.store.async_save({name: {k: v for k, v in row.items() if k != "generation"}
                                             for name, row in proposed.items()})
            for name in (ROUTING_STATISTICS_SURFACES if surface is None else (surface,)):
                self.surfaces[name] = proposed[name]
            self.revision += 1

    async def async_wipe(self) -> None:
        async with self.lock:
            if self.store:
                await self.store.async_remove()
            self.surfaces = {surface: _empty() for surface in ROUTING_STATISTICS_SURFACES}
            self.revision += 1
            self.saved_revision = self.revision

    def finish(self, turn: TurnStatistics) -> None:
        row = self.surfaces[turn.surface]
        if row["generation"] != turn.generation:
            return
        outcome = "cancelled" if turn.cancelled else "failed" if turn.failed else "pending" if turn.pending else turn.route if turn.completed else "failed"
        if turn.provider_used and outcome in ("local", "decision"):
            outcome = "provider"
        row["outcomes"][outcome] += 1
        row["provider_turns"] += int(turn.provider_used)
        for group in ("decision_usage", "provider_usage"):
            for key, value in getattr(turn, group).items():
                row[group][key] += value
        self.revision += 1


@dataclass
class TurnStatistics:
    surface: str
    generation: str
    route: str = "provider"
    completed: bool = False
    failed: bool = False
    cancelled: bool = False
    pending: bool = False
    provider_used: bool = False
    closed: bool = False
    decision_usage: dict = field(default_factory=_usage)
    provider_usage: dict = field(default_factory=_usage)

    def usage(self, group: str, body: Any, *, started: bool = False) -> None:
        if self.closed:
            return
        values = getattr(self, group)
        if not started:
            values["requests"] += 1
        if isinstance(body, dict) and all(_valid_count(body.get(k)) for k in ("input_tokens", "output_tokens")):
            values["reports"] += 1
            for key in ("input_tokens", "output_tokens"):
                values[key] += body[key]


_current: ContextVar[TurnStatistics | None] = ContextVar("phoenix_routing_statistics", default=None)


def local_result(reply: Any, route: str) -> None:
    from .conversation_log import record_current
    record_current("routing", {"route": route, "outcome": reply.outcome})
    turn = _current.get()
    if turn is not None:
        turn.route = route
        turn.pending = bool(reply.pending)
        turn.completed = reply.statistics_completed
        turn.cancelled = reply.outcome == "cancelled"
        turn.failed = not turn.pending and not turn.completed and not turn.cancelled


def voice_result(outcome: str, pending: bool, has_text: bool) -> None:
    from .conversation_log import record_current
    record_current("turn_outcome", {"outcome": outcome, "pending": pending})
    turn = _current.get()
    if turn is not None:
        turn.pending = pending
        turn.completed = outcome == "ok" and has_text
        turn.failed = not turn.completed and not pending


def begin_decision(scope: str) -> TurnStatistics | None:
    turn = _current.get()
    if turn is not None and not turn.closed and scope in (turn.surface, f"tool_{turn.surface}"):
        turn.decision_usage["requests"] += 1
        return turn
    return None


def end_decision(turn: TurnStatistics | None, body: Any) -> None:
    if turn is not None:
        turn.usage("decision_usage", body.get("usage") if isinstance(body, dict) else None, started=True)


def decision_usage(scope: str, body: Any) -> None:
    end_decision(begin_decision(scope), body)


async def provider_events(events):
    """Replace cumulative usage within each actual request, then add requests once."""
    from .conversation_log import record_current
    record_current("routing", {"route": "provider"})
    turn = _current.get()
    usage = None
    if turn is not None:
        turn.provider_used = True
        turn.route = "provider"
        turn.provider_usage["requests"] += 1
    try:
        async for event in events:
            if event.get("type") == "usage_update":
                usage = event
            yield event
    finally:
        await events.aclose()
        if turn is not None:
            turn.usage("provider_usage", usage, started=True)


def tracked_turn(surface: str):
    """Observe the existing boundary without changing dispatch or response behavior."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            data = kwargs.get("data") if "data" in kwargs else args[1]
            stats = getattr(data, "routing_statistics", None)
            if stats is None:
                return await function(*args, **kwargs)
            turn = TurnStatistics(surface, stats.surfaces[surface]["generation"])
            context = _current.set(turn)
            if surface == "chat":
                emit = kwargs["emit"]
                async def observed(event, payload):
                    if event == "error":
                        turn.cancelled = payload.get("code") == "cancelled"
                        turn.failed = not turn.cancelled
                    elif event == "approval_required":
                        turn.pending = True
                    elif event == "approval_resolved":
                        turn.pending = False
                    elif event == "done":
                        turn.completed = turn.completed or (turn.provider_used and payload.get("stop_reason") == "end_turn")
                    elif event == "continue_required" or (event == "notice" and payload.get("code") == "empty_turn"):
                        turn.failed = True
                    await emit(event, payload)
                kwargs["emit"] = observed
            try:
                return await function(*args, **kwargs)
            except asyncio.CancelledError:
                turn.cancelled = True
                raise
            except BaseException:
                turn.failed = True
                raise
            finally:
                turn.closed = True
                _current.reset(context)
                stats.finish(turn)
        return wrapped
    return decorate


def untracked_turn(function):
    """AI Task cannot inherit a surrounding Voice/Chat accounting context."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        context = _current.set(None)
        try:
            return await function(*args, **kwargs)
        finally:
            _current.reset(context)
    return wrapped
