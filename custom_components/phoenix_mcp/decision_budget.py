"""A shared durable hourly request allowance, independent of provider prices."""
from __future__ import annotations

import asyncio
from time import time
from typing import Any

from .const import DECISION_BUDGET_WINDOW_SECONDS


class DecisionBudgetError(Exception):
    """A request was refused before network dispatch."""


class DecisionRequestBudget:
    """Persist reservations before dispatch; uncertain requests are never refunded.

    Only calls made while enabled consume the allowance. A fixed UTC hour makes
    restart recovery deterministic. Clock rollback cannot open an earlier window.
    The store is shared across accounts, previews, Voice, Chat and shadow jobs.
    """

    def __init__(self, store: Any = None) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._window = 0
        self._used = 0
        self._unavailable = False

    async def async_load(self) -> None:
        if self._store is None:
            return
        try:
            raw = await self._store.async_load()
            if raw is None:
                return
            if (not isinstance(raw, dict) or set(raw) != {'window', 'used'}
                    or type(raw['window']) is not int or raw['window'] < 0
                    or type(raw['used']) is not int or raw['used'] < 0):
                raise ValueError('Invalid request budget ledger')
            self._window, self._used = raw['window'], raw['used']
        except Exception:
            self._unavailable = True

    async def reserve(self, limit: int) -> None:
        if limit == 0:
            return
        async with self._lock:
            if self._unavailable:
                raise DecisionBudgetError('budget_unavailable')
            window = int(time()) // DECISION_BUDGET_WINDOW_SECONDS
            if window > self._window:
                self._window, self._used = window, 0
            if self._used >= limit:
                raise DecisionBudgetError('request_budget_exhausted')
            self._used += 1
            if self._store is None:
                return
            # Keep the lock until an uncertain save settles, including cancellation.
            task = asyncio.create_task(self._store.async_save({'window': self._window, 'used': self._used}))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await task
                except Exception:
                    self._unavailable = True
                raise
            except Exception as error:
                self._unavailable = True
                raise DecisionBudgetError('budget_unavailable') from error

    async def async_wipe(self) -> None:
        """Serialize explicit core-data removal against durable reservations."""
        async with self._lock:
            self._unavailable = True
            if self._store is not None:
                await self._store.async_remove()
            self._window = self._used = 0
            self._unavailable = False

    def summary(self, limit: int) -> dict:
        current = int(time()) // DECISION_BUDGET_WINDOW_SECONDS
        used = self._used if current <= self._window else 0
        return {'limit': limit, 'used': used, 'remaining': max(0, limit-used) if limit else None,
                'reset_at': (max(current, self._window)+1)*DECISION_BUDGET_WINDOW_SECONDS,
                'available': not self._unavailable}
