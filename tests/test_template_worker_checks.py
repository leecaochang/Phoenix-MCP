"""Caller templates reach Home Assistant only after the disposable worker has run them.

test_condition evaluates its templates in the worker; validation, confirm-mode authoring and
the helper preflights compile and trial-render theirs there first. A template that stalls or
crashes the interpreter costs only the worker. A host that refuses the worker's memory limit,
as macOS does, runs it without that limit.
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
from custom_components.phoenix_mcp.ws_dispatch import async_get_lovelace_config, async_save_lovelace_config

REAL_TEMPLATE_WORKER = True

# Folded while compiling: a constant power that takes far more CPU than the work budget on any
# runner. The smaller 9 ** (9 ** 7) took about 3 s, which a fast runner could finish within the
# budget plus its rounding, so the worker sometimes answered instead of refusing.
POWER = "{{ (9 ** (9 ** 8)) > 0 }}"
# A lone surrogate computed at render time, which ciso8601 cannot survive.
COMPUTED_SURROGATE = "{{ as_timestamp('%c' % 55296, 0) > 0 }}"


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
    ("test_trigger", {"cap_config_read": "allow", "cap_automation_write": "allow"},
     {"trigger": {"trigger": "template", "value_template": POWER}, "timeout": 1}),
    ("test_trigger", {"cap_config_read": "allow", "cap_automation_write": "allow"},
     {"trigger": {"trigger": "template", "value_template": COMPUTED_SURROGATE}, "timeout": 1}),
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


@pytest.mark.parametrize("template", [POWER, COMPUTED_SURROGATE])
async def test_a_dashboard_card_template_costs_only_the_worker(hass, template):
    """A card's Jinja is checked before any approval, whose Preview would hand it to Core."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    layout = {"views": [{"title": "V", "cards": [{"type": "markdown", "content": "plain"}]}]}
    await async_save_lovelace_config(hass, None, layout)
    data = _data()
    stall, (content, outcome, _) = await _longest_stall(_call_tool(
        "add_dashboard_card", {"view_index": 0, "card": {"type": "markdown", "content": template}},
        _key(cap_lovelace_write="confirm"), hass, data))
    assert outcome == "invalid_request", content
    assert stall < 0.5, stall
    assert not any("approval" in str(call) for call in data.store.method_calls)
    assert await async_get_lovelace_config(hass, None) == layout
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


async def test_a_check_that_ran_out_of_memory_is_refused(hass):
    async def exhausted(payload, hass, data):
        return 0, b'{"compile_seconds": 0.001, "exhausted": true}'

    with patch.object(template_render, "_run_worker", exhausted):
        refusal = await async_template_refusal({"value_template": "{{ 1 }}"}, hass, _data())
    assert refusal is not None and "more memory than a check allows" in refusal


def _out_of_memory(*args, **kwargs):
    raise MemoryError


@pytest.mark.parametrize("template,patched", [
    ("{{ '%400000000d' % 1 }}", ("binop", "%")),
    ("{{ 'a' * 400000000 }}", ("binop", "*")),
    ("{{ ([0] * 30000000) | length }}", ("binop", "*")),
    ("{{ 'a' | center(400000000) }}", ("filter", "center")),
    ("{% if 1 %}{{ 'a' * 400000000 }}{% endif %}", ("binop", "*")),
])
def test_a_fold_the_memory_limit_stops_is_reported_not_skipped(template, patched, monkeypatch):
    """Jinja treats a MemoryError while folding as "not foldable"; Core, with no limit, completes the fold.

    The allocation the worker's address-space limit refuses is replaced by a MemoryError, as
    hosts without that limit cannot refuse it for real.
    """
    from jinja2 import defaults, nodes

    kind, name = patched
    monkeypatch.setitem(nodes._binop_to_func if kind == "binop" else defaults.DEFAULT_FILTERS, name, _out_of_memory)
    assert template_worker._check([template]).get("exhausted") is True


@pytest.mark.parametrize("template", ["{{ '%5d' % 1 }}", "{{ 1 / 0 }}", "{{ 'a' | center(4) }}", "{{ x * 3 }}"])
def test_a_fold_that_is_impossible_for_another_reason_is_not_exhausted(template):
    assert "exhausted" not in template_worker._check([template])


def test_the_check_folder_repeats_jinjas_own():
    """The check's folder copies Jinja's Optimizer.generic_visit; a Jinja change must be looked at."""
    import hashlib
    import inspect
    from jinja2.optimizer import Optimizer

    source = inspect.getsource(Optimizer.generic_visit)
    assert hashlib.sha256(source.encode()).hexdigest()[:16] == "fc8593b452d4b6b2", source


async def test_a_large_constant_fold_is_refused_by_the_real_worker(hass):
    """Refused by the memory limit where the host applies it, else by the CPU budget."""
    refusal = await async_template_refusal(_automation("{{ '%1000000000d' % 1 }}"), hass, _data())
    assert refusal is not None and "Templates refused" in refusal


async def test_more_templates_than_the_worker_takes_are_refused(hass):
    with patch.object(template_render, "_run_worker", side_effect=AssertionError("spawned")):
        refusal = await async_template_refusal(["{{ %d }}" % n for n in range(300)], hass, _data())
    assert refusal is not None and "Too many templates" in refusal


async def test_the_compile_budget_holds_for_each_template_not_their_sum(hass):
    """Several templates that each compile well within the budget pass, though together they exceed it."""
    templates = ["{{ (7 ** 400000) > %d }}" % n for n in range(8)]
    assert await async_template_refusal(templates, hass, _data()) is None


async def test_repeated_templates_are_checked_once(hass):
    seen: list[list[str]] = []

    async def worker(payload, hass, data):
        seen.append(payload["templates"])
        return 0, b'{"compile_seconds": 0}'

    with patch.object(template_render, "_run_worker", worker):
        assert await async_template_refusal(["{{ 1 }}"] * 300 + ["{{ 2 }}"], hass, _data()) is None
    assert seen == [["{{ 1 }}", "{{ 2 }}"]]


async def test_templates_the_stored_entry_holds_are_not_checked_again(hass):
    stored = {"sequence": [{"variables": {f"v{n}": "{{ 1 + %d }}" % n}} for n in range(300)]}
    edited = {"alias": "Renamed", **stored}
    with patch.object(template_render, "_run_worker", side_effect=AssertionError("spawned")):
        assert await async_template_refusal(edited, hass, _data(), stored) is None
    added = {**edited, "variables": {"new": "{{ 2 + 2 }}"}}
    seen: list[list[str]] = []

    async def worker(payload, hass, data):
        seen.append(payload["templates"])
        return 0, b'{"compile_seconds": 0}'

    with patch.object(template_render, "_run_worker", worker):
        assert await async_template_refusal(added, hass, _data(), stored) is None
    assert seen == [["{{ 2 + 2 }}"]]


@pytest.mark.usefixtures("immediate_authoring_observation")
async def test_renaming_a_large_stored_script_starts_no_worker(hass, tmp_path):
    """An edit that adds no template checks nothing, however many templates the script holds."""
    from custom_components.phoenix_mcp import yaml_includes

    hass.config.config_dir = str(tmp_path)
    hass.services.async_register("script", "reload", lambda call: None)
    big = {"alias": "Big", "sequence": [{"variables": {f"v{n}": "{{ 1 + %d }}" % n}} for n in range(300)]}
    (tmp_path / "configuration.yaml").write_text("script: !include scripts.yaml\n")
    (tmp_path / "scripts.yaml").write_text(yaml_includes.dump_tagged({"big": big}))
    with patch.object(template_render, "_run_worker", side_effect=AssertionError("spawned")):
        content, outcome, _ = await _call_tool(
            "edit_script", {"script_id": "big", "config": {**big, "alias": "Renamed"}},
            _key(cap_script_write="allow"), hass, _data())
    assert outcome == "allowed", content
    assert "alias: Renamed" in (tmp_path / "scripts.yaml").read_text()


async def _hold_slots(data: PhoenixData) -> None:
    for _ in range(data.template_slots._value):
        await data.template_slots.acquire()


async def test_a_check_waits_for_a_busy_worker_slot(hass):
    data = _data()
    await _hold_slots(data)

    async def in_slot(payload, data):
        return 0, b'{"compile_seconds": 0}'

    with patch.object(template_render, "_run_in_slot", in_slot):
        task = asyncio.create_task(async_template_refusal({"value_template": "{{ 1 }}"}, hass, data))
        await asyncio.sleep(0.05)
        assert not task.done()
        data.template_slots.release()
        assert await task is None


async def test_a_check_still_waiting_answers_busy_not_refused(hass, monkeypatch):
    data = _data()
    await _hold_slots(data)
    monkeypatch.setattr(template_render, "TEMPLATE_CHECK_SLOT_WAIT_SECONDS", 0.05)
    with patch.object(template_render, "_run_in_slot", side_effect=AssertionError("spawned")):
        refusal = await async_template_refusal({"value_template": "{{ 1 }}"}, hass, data)
    assert refusal is not None and "busy" in refusal and "Retry" in refusal
    assert "refused" not in refusal


async def test_a_render_never_waits_for_a_slot(hass):
    data = _data()
    await _hold_slots(data)
    with patch.object(template_render, "_run_in_slot", side_effect=AssertionError("spawned")):
        with pytest.raises(template_render.TemplateRenderError, match="capacity"):
            await template_render.async_render_template_for_access_key("{{ 1 }}", _key(), hass, data)


def test_a_host_without_a_memory_limit_still_runs_the_worker(monkeypatch, capsys):
    """macOS refuses RLIMIT_AS; the worker then runs under its CPU limit and says so on stderr."""
    calls: list[int] = []

    def setrlimit(kind, limits):
        calls.append(kind)
        if kind == resource.RLIMIT_AS:
            raise ValueError("not allowed")

    monkeypatch.setattr(template_worker.resource, "setrlimit", setrlimit)
    payload = json.dumps({"template": "{{ 6 * 7 }}", "states": [], "time_zone": "UTC"}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    zone = dt_util.get_default_time_zone()
    try:
        template_worker.main()
    finally:
        dt_util.set_default_time_zone(zone)
    captured = capsys.readouterr()
    assert captured.out == "42"
    assert captured.err.splitlines() == [template_worker.NO_MEMORY_LIMIT]
    assert calls == [resource.RLIMIT_CPU, resource.RLIMIT_AS, resource.RLIMIT_CPU]


async def test_a_missing_memory_limit_is_logged_once(hass, monkeypatch, caplog):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(template_render, "_memory_limit_noted", False)
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"42", template_worker.NO_MEMORY_LIMIT.encode() + b"\n"))
    process.wait = AsyncMock()
    with patch.object(template_render.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
        for _ in range(2):
            assert await template_render.async_render_template_for_access_key("{{ 6 * 7 }}", _key(), hass, _data()) == "42"
    assert sum("memory limit" in record.getMessage() for record in caplog.records) == 1


@pytest.mark.parametrize("tool,caps,args", [
    ("test_condition", {"cap_config_read": "allow"}, {"condition": "{{ is_state('light.desk', 'on') }}"}),
    ("test_trigger", {"cap_config_read": "allow", "cap_automation_write": "allow"},
     {"trigger": {"trigger": "template", "value_template": "{{ is_state('light.desk', 'on') }}"}, "timeout": 1}),
    ("validate_automation_or_script", {"cap_diagnostics": "allow"},
     {"type": "automation", "config": _automation("{{ is_state('light.desk', 'on') }}")}),
    ("add_dashboard_card", {"cap_lovelace_write": "confirm"},
     {"view_index": 0, "card": {"type": "markdown", "content": "{{ states('light.desk') }}"}}),
])
async def test_every_checking_tool_answers_busy_rather_than_refused(hass, monkeypatch, tool, caps, args):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    await async_save_lovelace_config(hass, None, {"views": [{"title": "V", "cards": []}]})
    hass.states.async_set("light.desk", "on")
    data = _data()
    await _hold_slots(data)
    monkeypatch.setattr(template_render, "TEMPLATE_CHECK_SLOT_WAIT_SECONDS", 0.05)
    with patch.object(template_render, "_run_in_slot", side_effect=AssertionError("spawned")):
        content, outcome, _ = await _call_tool(tool, args, _key(**caps), hass, data)
    text = content["content"][0]["text"]
    assert outcome == "invalid_request", content
    assert "busy" in text and "Retry" in text, text
    assert "refused" not in text and "could not be evaluated" not in text, text


async def test_a_blueprint_edit_checks_only_templates_the_current_source_lacks(hass):
    from custom_components.phoenix_mcp.tools.blueprint import _blueprint_template_refusal

    current = ("blueprint:\n  name: Probe\n  domain: script\nsequence:\n"
               + "".join(f"  - variables:\n      v{n}: \"{{{{ 1 + {n} }}}}\"\n" for n in range(300)))
    edited = current.replace("name: Probe", "name: Renamed")
    with patch.object(template_render, "_run_worker", side_effect=AssertionError("spawned")):
        assert await _blueprint_template_refusal(hass, edited, _data(), "edit_blueprint", current) is None
    refusal = await _blueprint_template_refusal(hass, edited, _data(), "edit_blueprint")
    assert refusal is not None and "Too many templates" in refusal[0]["content"][0]["text"]


@pytest.mark.usefixtures("immediate_authoring_observation")
async def test_editing_a_large_stored_automation_starts_no_worker(hass, tmp_path):
    from custom_components.phoenix_mcp import yaml_includes

    hass.config.config_dir = str(tmp_path)
    hass.services.async_register("automation", "reload", lambda call: None)
    big = {"id": "big", "alias": "Big", "triggers": [{"trigger": "homeassistant", "event": "start"}],
           "actions": [{"variables": {f"v{n}": "{{ 1 + %d }}" % n}} for n in range(300)]}
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text(yaml_includes.dump_tagged([big]))
    with patch.object(template_render, "_run_worker", side_effect=AssertionError("spawned")):
        content, outcome, _ = await _call_tool(
            "edit_automation", {"automation_id": "big", "config": {**big, "description": "Edited"}},
            _key(cap_automation_write="allow"), hass, _data())
    assert outcome == "allowed", content
    assert "description: Edited" in (tmp_path / "automations.yaml").read_text()
