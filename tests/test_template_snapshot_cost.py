"""A refused or oversized render reads no more states than it must, on the event loop.

The snapshot of a key's permitted states is read only once a worker slot is held, one state
at a time, and stops at the worker's input limit, so a busy worker or a key that sees too
much costs little. The bytes sent are the same as serializing the whole request at once.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import template_render, template_sandbox
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.template_limits import TEMPLATE_WORKER_CONCURRENCY
from custom_components.phoenix_mcp.data import PhoenixData

# The real _run_worker and slot rules: nothing here reaches a spawn.
REAL_TEMPLATE_WORKER = True


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(id="k", name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=PermissionTree(domains={}), pass_through=True)


def _home(hass: HomeAssistant, count: int) -> None:
    for index in range(count):
        hass.states.async_set(f"sensor.s{index}", str(index),
                              {"friendly_name": f"Capteur n°{index} – salon", "values": [1.5, None, {"a": index}]})


def _counting_states():
    real = template_sandbox.iter_permitted_states
    seen = {"count": 0}

    def counted(*args, **kwargs):
        for state in real(*args, **kwargs):
            seen["count"] += 1
            yield state
    return seen, patch.object(template_render, "iter_permitted_states", counted)


async def test_the_request_bytes_match_serializing_it_whole(hass: HomeAssistant):
    _home(hass, 5)
    key = _key()
    payload = {"template": "{{ states('sensor.s1') }}", **template_render._snapshot(key, hass)}
    whole = {**payload, "states": [state.as_dict() for state in template_sandbox.iter_permitted_states(key, hass)]}
    assert template_render.encoded_payload(payload) == json.dumps(whole, default=str).encode()
    assert json.loads(template_render.encoded_payload(payload))["states"][0]["attributes"]["friendly_name"].startswith("Capteur")


async def test_a_busy_worker_refuses_before_reading_any_state(hass: HomeAssistant):
    _home(hass, 50)
    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())
    for _ in range(data.template_slots._value):
        await data.template_slots.acquire()
    seen, counting = _counting_states()
    with counting, pytest.raises(template_render.TemplateRenderError, match="capacity"):
        await template_render.async_render_template_for_access_key("{{ 1 }}", _key(), hass, data)
    assert seen["count"] == 0


async def test_an_oversized_snapshot_stops_at_the_limit(hass: HomeAssistant):
    _home(hass, 400)
    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())
    seen, counting = _counting_states()
    with counting, patch.object(template_render, "TEMPLATE_WORKER_INPUT_BYTES", 20_000), \
            patch("asyncio.create_subprocess_exec", side_effect=AssertionError("spawned")), \
            pytest.raises(template_render.TemplateRenderError, match="too large"):
        await template_render.async_render_template_for_access_key("{{ 1 }}", _key(), hass, data)
    assert 0 < seen["count"] < 400
    assert data.template_slots._value == TEMPLATE_WORKER_CONCURRENCY
