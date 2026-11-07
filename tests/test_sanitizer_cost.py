"""The sanitizer's cost grows with its input, whatever shape a client chooses.

Every audited call runs it on the event loop, so one superlinear pattern is a stall any
key can ask for. Growth is compared rather than wall time, which a loaded machine moves.
"""

from __future__ import annotations

import re
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp import (
    approvals, audit, conversation_log as history, esphome_yaml, redaction, voice_agent,
)
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.admin.approvals import PhoenixAdminApprovalsView
from custom_components.phoenix_mcp.approvals import PendingApproval, async_create_pending_approval, list_approvals
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import CONVERSATION_LOG_SCAN_BYTES, CONVERSATION_LOG_TEXT_SCAN_CHARS, DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.llm import probes
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.tool_dispatch import _dispatch_mcp
from tests.runtime_fixtures import make_admin_request

SMALL, FACTOR = 4_000, 8
# Linear growth gives about FACTOR and quadratic about FACTOR squared. The floor keeps
# timer noise on a fast run from failing a pattern that is nowhere near a stall.
RATIO_LIMIT, FLOOR_SECONDS = 3 * FACTOR, 0.03

SHAPES = {
    "initializer trailing space": lambda n: "a.b=0" + " " * n + "x",
    "initializer tab and semicolon": lambda n: "x.y=null" + "\t" * (n // 2) + ";" + "\t" * (n // 2) + "x",
    "initializer open braces": lambda n: "const a = {" + " " * n + "x",
    "initializer every gap": lambda n: " " * (n // 6) + "const" + " " * (n // 6) + "a.b" + " " * (n // 6) + "=" + " " * (n // 6) + "0" + " " * (n // 6) + ";" + " " * (n // 6) + "x",
    "repeated token header": lambda n: "eyJ" * (n // 3),
    "token header inside a word": lambda n: "xeyJ" * (n // 4),
    "token headers split by dashes": lambda n: ("eyJ" * 5 + "-") * (n // 16),
    "dotted token headers": lambda n: "eyJaaaaaa." * (n // 10),
    "two token segments": lambda n: "eyJaaaaaa.bbbbbb" * (n // 16),
    "long word": lambda n: "a" * n,
    "yaml key then space": lambda n: "a:" + " " * n + "x",
    "yaml block marker then space": lambda n: "a:" + " " * n + "|" + " " * n + "x",
    "sensitive key then space": lambda n: "pin:" + " " * n + ",",
    "code key then space": lambda n: "code:" + " " * n + "x",
    "yaml dashes": lambda n: "-" * n + " x",
    "yaml list marker": lambda n: " " * (n // 2) + "-" + " " * (n // 2) + "x",
    "colons": lambda n: ":" * n + "g",
    "hex and colons": lambda n: "abcd:" * (n // 5) + "g",
    "url userinfo": lambda n: "://" + "a" * (n // 2) + ":" + "b" * (n // 2),
    "repeated userinfo": lambda n: "://a:b" * (n // 6),
    "private key header": lambda n: "-----BEGIN " * (n // 11),
    "private key ends": lambda n: "-----BEGIN PRIVATE KEY-----" + "-----END " * (n // 9),
    "open quote secrets": lambda n: 'pin:"' * (n // 5),
    "mixed open quotes": lambda n: "pin:\"a'pin:'b\"" * (n // 13),
    "bearer then space": lambda n: "bearer" + " " * n,
    "alarm code digits": lambda n: "alarm code 1" + "-" * n + "x",
    "repeated data uri": lambda n: "data:" * (n // 5),
    "secret prefix then dashes": lambda n: "sk-" + "-" * n + "!",
    "credential call openings": lambda n: "password(" * (n // 9),
    "credential array openings": lambda n: "key[" * (n // 4),
    "credential then open quote": lambda n: 'password "' * (n // 10),
    "url parameters": lambda n: "?a=" * (n // 3),
    "url parameter names": lambda n: "?" + "a" * n,
    "newlines": lambda n: "\n" * n,
    "unclosed strong": lambda n: "**a " * (n // 4),
    "unclosed underscores": lambda n: "__a " * (n // 4),
    "unclosed emphasis": lambda n: " _a" * (n // 3),
    "unclosed star": lambda n: " *a" * (n // 3),
    "unclosed strike": lambda n: "~~a " * (n // 4),
    "open brackets": lambda n: "[" * n,
    "open image brackets": lambda n: "![" * (n // 2),
    "open link targets": lambda n: "[a](" * (n // 4),
    "spaces before no newline": lambda n: " " * n + "x",
    "rule marks": lambda n: "- " * (n // 2) + "x",
    "open thinking tags": lambda n: "<think>" * (n // 7),
    "short lines": lambda n: "k: v\n" * (n // 5),
    "unicode space": lambda n: " " * n + "x",
}

SCANNERS = {
    "redact_secrets_in_text": redaction.redact_secrets_in_text,
    "_scrub_log_text": redaction._scrub_log_text,
    "sanitized": lambda text: history.sanitized(text, max_bytes=CONVERSATION_LOG_SCAN_BYTES),
    "sanitized hiding addresses": lambda text: history.sanitized(text, max_bytes=CONVERSATION_LOG_SCAN_BYTES, hide_ip=True),
    "_spoken_text": voice_agent._spoken_text,
    "probe thinking": lambda text: probes._PROBE_THINKING.sub("", text),
    "device file scalar": lambda text: esphome_yaml._scrubbed(text, esphome_yaml._device_secret_key),
}


def _cost(run, *args) -> float:
    best = float("inf")
    for _ in range(3):
        started = time.process_time()
        run(*args)
        best = min(best, time.process_time() - started)
    return best


def _assert_linear(small: float, large: float, label: str) -> None:
    assert large < FLOOR_SECONDS or large <= RATIO_LIMIT * max(small, 1e-4), (
        f"{label}: {small:.4f}s then {large:.4f}s for {FACTOR} times the input")


@pytest.mark.parametrize("scanner", SCANNERS)
def test_every_shape_costs_in_proportion_to_its_size(scanner):
    run = SCANNERS[scanner]
    for name, shape in SHAPES.items():
        _assert_linear(_cost(run, shape(SMALL)), _cost(run, shape(SMALL * FACTOR)), name)


def test_finding_known_values_costs_in_proportion_to_the_text():
    values = [f"secret-value-{index:04d}" for index in range(50)]
    chunks = lambda count: ["secret-value-0007 and other text"] * count
    _assert_linear(_cost(esphome_yaml._value_hits, chunks(SMALL), values),
                   _cost(esphome_yaml._value_hits, chunks(SMALL * FACTOR), values), "value hits")


def test_the_guard_measures_a_quadratic_pattern():
    """A positive control: the same assertion must fail on a known backtracking pattern."""
    pattern = re.compile(r"\s*;?\s*")

    def run(text: str) -> None:
        pattern.fullmatch(text)
    text = " " * SMALL + "x"
    with pytest.raises(AssertionError):
        _assert_linear(_cost(run, text), _cost(run, " " * (SMALL * FACTOR) + "x"), "control")


def _unbounded_row(payload):
    return lambda: audit._logged_payload(payload)


@pytest.mark.parametrize("name", ["initializer trailing space", "repeated token header", "yaml key then space"])
def test_an_audit_row_costs_the_same_however_long_the_payload(name):
    small = {"name": "get_state", "arguments": {"entity_id": SHAPES[name](SMALL)}}
    large = {"name": "get_state", "arguments": {"entity_id": SHAPES[name](SMALL * FACTOR)}}
    _assert_linear(_cost(_unbounded_row(small)), _cost(_unbounded_row(large)), name)


def test_a_rejected_call_scans_a_bounded_part_of_provider_text():
    seen = []
    real = history.redact_secrets_in_text

    def measure(text):
        seen.append(len(text))
        return real(text)

    one_string = '{"note": "' + ("x " * 1_000_000) + '"}'
    many_strings = "[" + ", ".join(['"' + "x " * 20_000 + '"'] * 40) + "]"
    for raw in (one_string, many_strings):
        seen.clear()
        with patch.object(history, "redact_secrets_in_text", measure):
            history._scrub_argument_text(raw, hide_ip=False)
        assert seen and max(seen) <= CONVERSATION_LOG_TEXT_SCAN_CHARS + 64
        # Each string once, then the text they sit in.
        assert sum(seen) <= 2 * CONVERSATION_LOG_TEXT_SCAN_CHARS + 64


def test_rejected_arguments_that_are_not_text_are_bounded_too():
    seen = []
    real = history.redact_secrets_in_text

    def measure(text):
        seen.append(len(text))
        return real(text)

    log = SimpleNamespace(entry=lambda _handle: {}, event=lambda *_args, **_kwargs: None,
                          _settings=SimpleNamespace(log_entity_names=True, log_client_ip=True))
    token = history._current.set((log, "id", 0))
    try:
        with patch.object(history, "redact_secrets_in_text", measure):
            history.record_rejected_tool("id", "tool", {"note": "x " * 1_000_000}, reason="invalid")
    finally:
        history._current.reset(token)
    assert seen and max(seen) <= CONVERSATION_LOG_TEXT_SCAN_CHARS


async def _access_key_and_data(hass):
    store = AccessKeyStore(hass, AsyncMock())
    store.get_settings().mesa_mode = "off"
    access_key, _raw = await store.async_create_access_key("cost", "admin", rate_limit_requests=0)
    access_key.pass_through = True
    access_key.cap_physical_control = "confirm"
    access_key.confirm_inline_wait_seconds = 0
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    er.async_get(hass).async_get_or_create("lock", "test", "front", suggested_object_id="front")
    hass.states.async_set("lock.front", "locked")
    hass.services.async_register("lock", "unlock", AsyncMock())
    return access_key, data


@pytest.mark.parametrize("name", ["initializer trailing space", "repeated token header", "yaml key then space"])
async def test_a_queued_approval_costs_in_proportion_to_its_arguments(hass, name):
    access_key, data = await _access_key_and_data(hass)

    async def queue(length: int) -> float:
        params = {"name": "call_service", "arguments": {
            "service": {"domain": "lock", "name": "unlock"}, "targets": [{"kind": "entity", "ids": ["lock.front"]}],
            "service_data": {"note": SHAPES[name](length)}}}
        started = time.process_time()
        _result, _method, _resource, outcome = await _dispatch_mcp(
            "tools/call", 1, params, access_key, hass, data, "10.0.0.5", "http://x")
        spent = time.process_time() - started
        assert outcome == "pending_approval"
        return spent

    small, large = await queue(SMALL), await queue(SMALL * FACTOR)
    _assert_linear(small, large, name)
    listed = list_approvals(data.store)
    _assert_linear(_cost(listed[0].to_dict), _cost(listed[1].to_dict), name)


async def test_saving_an_approval_scans_nothing(hass):
    """The stored row keeps raw arguments, so redacting them on the way to disk buys nothing."""
    access_key, data = await _access_key_and_data(hass)
    with patch.object(approvals, "redact_structure", side_effect=AssertionError("scanned")):
        record = await async_create_pending_approval(
            data.store, access_key_id=access_key.id, access_key_name="n", tool_name="call_service",
            cap_name="cap_physical_control", args={"note": "x"}, diff={}, request_id="r",
        )
        assert record.to_dict(redact_args=False)["args"] == {"note": "x"}
    assert "scanned" not in str(record.args)


async def test_the_approval_list_redacts_off_the_event_loop(hass):
    """A page holds many rows, and the size of each is a key's choice."""
    access_key, data = await _access_key_and_data(hass)
    await async_create_pending_approval(
        data.store, access_key_id=access_key.id, access_key_name="n", tool_name="call_service",
        cap_name="cap_physical_control", args={"note": "x"}, diff={}, request_id="r",
    )
    threads = []
    real = PendingApproval._public_args

    def spy(record):
        threads.append(threading.get_ident())
        return real(record)

    view = PhoenixAdminApprovalsView()
    view.hass = hass
    with patch.object(PendingApproval, "_public_args", spy):
        response = await view.get(make_admin_request())
    assert response.status == 200
    assert threads and threading.get_ident() not in threads
