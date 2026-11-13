"""Caller templates reach Home Assistant only after the disposable worker has run them.

test_condition evaluates its templates in the worker; validation, confirm-mode authoring and
the helper preflights compile and trial-render theirs there first. A template that stalls or
crashes the interpreter costs only the worker. Off Linux the worker's memory limit cannot be
set, so these tests run it through a wrapper that skips only that limit.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import resource
import sys
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import template_render, template_worker
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.authoring_validation import async_template_refusal
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.template_limits import (
    TEMPLATE_WORKER_CPU_SECONDS, TEMPLATE_WORKER_MEMORY_BYTES, TEMPLATE_WORKER_STARTUP_CPU_SECONDS,
)
from custom_components.phoenix_mcp.tool_dispatch import _call_tool

REAL_TEMPLATE_WORKER = True

# Folded while compiling: a constant power that takes seconds of CPU.
POWER = "{{ (9 ** (9 ** 7)) > 0 }}"
# A lone surrogate computed at render time, which ciso8601 cannot survive.
COMPUTED_SURROGATE = "{{ as_timestamp('%c' % 55296, 0) > 0 }}"


@pytest.fixture(autouse=True)
def worker_script(tmp_path):
    """The real worker; off Linux, through a wrapper that skips only RLIMIT_AS."""
    if sys.platform == "linux":
        yield
        return
    wrapper = tmp_path / "worker_without_memory_limit.py"
    wrapper.write_text(
        "import resource, runpy\n"
        "_set = resource.setrlimit\n"
        "resource.setrlimit = lambda kind, limits: None if kind == resource.RLIMIT_AS else _set(kind, limits)\n"
        f"runpy.run_path({str(template_render._WORKER_SCRIPT)!r}, run_name='__main__')\n"
    )
    with patch.object(template_render, "_WORKER_SCRIPT", wrapper):
        yield


def _data() -> PhoenixData:
    return PhoenixData(store=MagicMock(), rate_limiter=RateLimiter(), audit=AuditLog())


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(entities={"light.desk": PermissionNode(state="GREEN")}), **caps,
    )


def _automation(template: str) -> dict:
    return {"alias": "probe", "triggers": [{"trigger": "homeassistant", "event": "start"}],
            "conditions": [{"condition": "template", "value_template": template}],
            "actions": [{"delay": 1}]}


async def _longest_stall(call) -> tuple[float, object]:
    """Run a call while a heartbeat measures the longest gap in the event loop."""
    gaps: list[float] = []
    stop = False

    async def heartbeat() -> None:
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    try:
        result = await call
    finally:
        stop = True
        await beat
    return max(gaps), result


@pytest.mark.parametrize("tool,caps,args", [
    ("test_condition", {"cap_config_read": "allow"}, {"condition": POWER}),
    ("test_condition", {"cap_config_read": "allow"}, {"condition": COMPUTED_SURROGATE}),
    ("validate_automation_or_script", {"cap_diagnostics": "allow"}, {"type": "automation", "config": _automation(POWER)}),
    ("validate_automation_or_script", {"cap_diagnostics": "allow"},
     {"type": "automation", "config": _automation("{{ ('%c' % 55296) | as_timestamp(0) > 0 }}")}),
    ("create_automation", {"cap_automation_write": "confirm"}, {"config": _automation(POWER)}),
    ("create_script", {"cap_script_write": "confirm"},
     {"script_id": "probe", "config": {"sequence": [{"condition": "template", "value_template": POWER}]}}),
])
async def test_a_costly_or_crashing_template_costs_only_the_worker(hass, tool, caps, args):
    assert await async_setup_component(hass, "homeassistant", {})
    data = _data()
    stall, (content, outcome, _) = await _longest_stall(_call_tool(tool, args, _key(**caps), hass, data))
    assert outcome == "invalid_request", content
    assert stall < 0.5, stall
    assert not any("approval" in str(call) for call in data.store.method_calls)
    text = content["content"][0]["text"]
    assert "CPU budget" in text or "exited unexpectedly" in text or "too long to compile" in text, text


async def test_a_condition_template_is_evaluated_through_the_real_worker(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("light.desk", "on")
    content, outcome, _ = await _call_tool(
        "test_condition", {"condition": "{{ is_state('light.desk', 'on') and level > 2 }}", "variables": {"level": 3}},
        _key(cap_config_read="allow"), hass, _data())
    assert outcome == "allowed", content
    assert json.loads(content["content"][0]["text"])["result"] is True


async def test_benign_authoring_templates_pass_the_check(hass):
    config = _automation("{{ states('sensor.x') | float(0) > 20 and trigger.to_state.state == 'on' }}")
    assert await async_template_refusal(config, hass, _data()) is None


async def test_helper_settings_templates_are_checked(hass):
    refusal = await async_template_refusal({"state": POWER}, hass, _data())
    assert refusal is not None and "Templates refused" in refusal


def test_the_work_budget_starts_after_the_imports(monkeypatch, capsys):
    """Memory and a startup cap come first; the CPU limit then adds the budget to what imports used."""
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(template_worker.resource, "setrlimit", lambda kind, limits: calls.append((kind, limits)))
    payload = json.dumps({"template": "{{ 6 * 7 }}", "states": [], "time_zone": "UTC"}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    zone = dt_util.get_default_time_zone()
    try:
        template_worker.main()
    finally:
        dt_util.set_default_time_zone(zone)
    assert capsys.readouterr().out == "42"
    used = resource.getrusage(resource.RUSAGE_SELF)
    assert calls[0] == (resource.RLIMIT_CPU, (TEMPLATE_WORKER_STARTUP_CPU_SECONDS,) * 2)
    assert calls[1] == (resource.RLIMIT_AS, (TEMPLATE_WORKER_MEMORY_BYTES,) * 2)
    kind, (soft, hard) = calls[2]
    assert kind == resource.RLIMIT_CPU and soft == hard
    # This process has used more CPU than a fresh worker, so the cap applies here.
    expected = min(TEMPLATE_WORKER_STARTUP_CPU_SECONDS,
                   math.ceil(used.ru_utime + used.ru_stime + TEMPLATE_WORKER_CPU_SECONDS))
    assert soft in (expected, expected - 1)
    assert len(calls) == 3


async def test_a_slow_compile_is_refused_even_within_the_cpu_limit(hass):
    async def slow(payload, hass, data):
        return 0, b'{"compile_seconds": 0.3}'

    with patch.object(template_render, "_run_worker", slow):
        refusal = await async_template_refusal({"value_template": "{{ 1 }}"}, hass, _data())
    assert refusal is not None and "too long to compile" in refusal


async def test_more_templates_than_the_worker_takes_are_refused(hass):
    with patch.object(template_render, "_run_worker", side_effect=AssertionError("spawned")):
        refusal = await async_template_refusal(["{{ %d }}" % n for n in range(300)], hass, _data())
    assert refusal is not None and "Too many templates" in refusal
