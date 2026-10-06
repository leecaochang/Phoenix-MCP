"""Newest-first history pages are exact and complete, using only lower-bounded Recorder reads.

The fake Recorder below reproduces state_changes_during_period's query contract
from Home Assistant's source: rows strictly after start and strictly before end,
ascending, with the SQL limit applied to that ascending order.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.recorder_queries import newest_page

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _rows(count: int, seed: int) -> list[datetime]:
    rng = random.Random(seed)
    return sorted({BASE + timedelta(seconds=rng.randint(0, 86_000)) for _ in range(count)})


def _fetcher(stamps: list[datetime], probes: list[int]):
    async def fetch(after, before, limit):
        probes.append(limit)
        selected = [stamp for stamp in stamps if after < stamp < before]
        return selected[:limit] if limit else selected
    return fetch


async def test_newest_page_returns_exactly_the_newest_rows():
    stamps = _rows(500, 7)
    for limit in (1, 7, 100, 499, 500, 900):
        probes: list[int] = []
        rows, has_more = await newest_page(_fetcher(stamps, probes), BASE - timedelta(seconds=1), BASE + timedelta(days=2), limit)
        assert rows == stamps[-limit:] if limit < len(stamps) else rows == stamps
        assert has_more == (limit < len(stamps))
        assert all(probe == limit + 1 for probe in probes) and len(probes) <= 42


class _State:
    def __init__(self, stamp):
        self.stamp = stamp

    def as_dict(self):
        return {"entity_id": "light.kitchen", "state": "on", "last_changed": self.stamp.isoformat()}


async def test_tool_pages_backward_through_every_change_once(hass):
    stamps = _rows(230, 11)
    hass.states.async_set("light.kitchen", "on", {})

    def changes(_hass, after, before, entity_id, *_args):
        limit = _args[2]
        selected = [_State(stamp) for stamp in stamps if after < stamp < before]
        return {entity_id: selected[:limit] if limit else selected}

    instance = SimpleNamespace(keep_days=10)

    async def run(fn):
        return fn()
    instance.async_add_executor_job = run
    key = AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                          permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}))
    seen: list[str] = []
    args = {"entity_id": "light.kitchen", "start_time": (BASE - timedelta(seconds=1)).isoformat(),
            "end_time": (BASE + timedelta(days=1)).isoformat(), "order": "newest_first", "limit": 50}
    with (patch("homeassistant.components.recorder.get_instance", return_value=instance),
          patch("homeassistant.components.recorder.history.state_changes_during_period", side_effect=changes)):
        while True:
            result, outcome, _ = await _call_tool("get_history", args, key, hass, MagicMock())
            assert outcome == "allowed", result
            body = json.loads(result["content"][0]["text"])
            page = [row["when"] for row in body["history"]]
            assert page == sorted(page, reverse=True)
            seen += page
            if not body["has_more"]:
                break
            args = {**args, "cursor": body["next_cursor"]}
    assert seen == [stamp.isoformat() for stamp in reversed(stamps)]
