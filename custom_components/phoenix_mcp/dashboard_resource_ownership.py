"""Remember which dashboard resources Phoenix MCP created.

Update and delete reach only these IDs, so a resource that HACS or a person
registered is never touched. A missing or unreadable record grants nothing.
"""

from __future__ import annotations

import asyncio
import logging

from .durable_storage import DurableStore

_LOGGER = logging.getLogger(__name__)


class DashboardResourceOwnership:
    """The IDs of the resources Phoenix created. A None store stays in memory."""

    def __init__(self, store: DurableStore | None = None) -> None:
        self._store = store
        self._ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        if self._store is None:
            return
        try:
            raw = await self._store.async_load()
        except Exception:  # an unreadable record must not block setup
            _LOGGER.exception("Could not load the dashboard resource record; no resource counts as Phoenix's")
            return
        ids = raw.get("ids") if isinstance(raw, dict) else None
        if isinstance(ids, list):
            self._ids = {item for item in ids if isinstance(item, str) and item}

    def owns(self, resource_id: object) -> bool:
        return isinstance(resource_id, str) and resource_id in self._ids

    async def _replace(self, ids: set[str]) -> None:
        if self._store is not None:
            await self._store.async_save({"ids": sorted(ids)})
        self._ids = ids

    async def async_add(self, resource_id: str) -> None:
        async with self._lock:
            await self._replace(self._ids | {resource_id})

    async def async_discard(self, resource_id: str) -> None:
        async with self._lock:
            if resource_id in self._ids:
                await self._replace(self._ids - {resource_id})
