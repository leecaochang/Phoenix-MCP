"""Protected crash snapshots, complete multithread blocks and authority checks."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from custom_components.phoenix_mcp import fault_log, mcp_view
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import (
    DOMAIN, FAULT_LOG_FILENAME, FAULT_LOG_MAX_BLOCK_BYTES, FAULT_LOG_MAX_BYTES,
    FAULT_LOG_MAX_LINES, FAULT_LOG_MAX_PAGE_BYTES,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore


def _dump(label="Aborted"):
    return (
        f"Fatal Python error: {label}\n\n"
        "Thread 0x000000001234abcd (most recent call first):\n"
        '  File "/usr/local/lib/python3.14/threading.py", line 10 in wait\n\n'
        "Current thread 0x000000009abc1234 (most recent call first):\n"
        '  File "/config/custom_components/demo/worker.py", line 42 in run\n\n'
        "Extension modules: demo._native (total: 1)\n"
    )


@pytest_asyncio.fixture
async def context(hass, tmp_path):
    store = AccessKeyStore(hass, AsyncMock())
    access_key, _ = await store.async_create_access_key("crash-test", "admin", rate_limit_requests=0)
    access_key.cap_log_read = "allow"
    access_key.cap_diagnostics = "allow"
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    return hass, access_key, data, tmp_path / FAULT_LOG_FILENAME


async def _call(context, **arguments):
    hass, access_key, data, _ = context
    response, _, _, outcome = await mcp_view._dispatch_mcp(
        "tools/call", 1, {"name": "get_logs", "arguments": {"source": "fault_log", **arguments}},
        access_key, hass, data, "127.0.0.1", "http://localhost",
    )
    if "error" in response:
        return response["error"], outcome, True
    result = response["result"]
    text = result["content"][0]["text"]
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        body = text
    return body, outcome, result.get("isError", False)


async def test_absent_empty_unavailable_are_distinct(context, monkeypatch):
    _, _, _, path = context
    body, outcome, error = await _call(context)
    assert (body["source"]["status"], body["crash_recorded"], outcome, error) == ("absent", False, "allowed", False)
    path.touch()
    body, outcome, error = await _call(context)
    assert (body["source"]["status"], body["crash_recorded"], outcome, error) == ("empty", False, "allowed", False)
    monkeypatch.setattr(fault_log.os, "read", lambda *a: (_ for _ in ()).throw(PermissionError("sensitive path")))
    path.write_text(_dump())
    body, outcome, error = await _call(context)
    assert (body["source"]["status"], body["crash_recorded"], outcome, error) == ("unavailable", None, "invalid_request", True)
    assert "sensitive path" not in json.dumps(body)


async def test_order_search_and_whole_block_pages(context):
    *_, path = context
    path.write_text(_dump("older") + _dump("newer"))
    body, outcome, error = await _call(context, limit=1)
    assert outcome == "allowed" and not error
    assert body["crash_recorded"] is True
    assert body["count"] == 1 and body["matched_blocks"] == 2 and body["has_more"]
    text = body["entries"][0]["text"]
    assert "newer" in text and "older" not in text
    assert text.index("Thread <thread>") < text.index("Current thread <thread>") < text.index("Extension modules")
    page, _, _ = await _call(context, cursor=body["next_cursor"], limit=1)
    assert "older" in page["entries"][0]["text"] and not page["has_more"]
    oldest, _, _ = await _call(context, order="oldest_first")
    assert "older" in oldest["entries"][0]["text"]
    searched, _, _ = await _call(context, search="NEWER")
    assert searched["matched_blocks"] == 1 and "Thread <thread>" in searched["entries"][0]["text"]
    no_match, _, _ = await _call(context, search="missing")
    assert no_match["crash_recorded"] is True and no_match["count"] == 0


async def test_real_python_multithread_traceback(context):
    # Use CPython's real multithread writer without crashing any process.
    process = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", '''
import faulthandler, sys, threading
event = threading.Event()
threading.Thread(target=event.wait, daemon=True).start()
faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
'''], capture_output=True, timeout=10)
    assert process.returncode == 0
    *_, path = context
    path.write_bytes(b"Fatal Python error: synthetic test header\n\n" + process.stdout)
    body, outcome, error = await _call(context)
    assert outcome == "allowed" and not error
    assert body["source"]["status"] == "available" and body["count"] == 1
    assert body["entries"][0]["text"].count("thread>") == 2


async def test_scrub_before_search_and_paths(context):
    _, _, _, path = context
    raw_access_key = "phx_" + "a" * 64
    path.write_text(_dump() + f"password: secret-value\n{raw_access_key}\nsensor.hidden\nhttp://user:pass@192.168.1.10/?token=abc\n/Users/private/config/secrets.yaml\n")
    body, _, _ = await _call(context)
    text = body["entries"][0]["text"]
    for private in (raw_access_key, "secret-value", "sensor.hidden", "192.168.1.10", "user:pass", "/Users/", "/config/", "0x00000000"):
        assert private not in text
    assert 'File "custom_components/demo/worker.py", line 42 in run' in text
    for private in (raw_access_key, "secret-value", "/Users/", "sensor.hidden"):
        searched, _, _ = await _call(context, search=private)
        assert searched["count"] == 0
    searched, _, _ = await _call(context, search="<phoenix-token>")
    assert searched["count"] == 1


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "bad_utf8"])
async def test_fixed_file_allowlist_refuses_aliases_and_special_files(context, kind):
    *_, path = context
    secret = path.parent / "secrets.yaml"
    secret.write_text(_dump("private-secret"))
    if kind == "symlink":
        path.symlink_to(secret)
    elif kind == "hardlink":
        os.link(secret, path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff")
    body, outcome, error = await _call(context)
    assert body["source"]["status"] == "unavailable" and error and outcome == "invalid_request"
    assert "private-secret" not in json.dumps(body)


@pytest.mark.parametrize("args", [
    {"source": "../secrets.yaml"}, {"source": []}, {"source": None},
    {"path": "secrets.yaml"}, {"level": "ERROR"}, {"integration": "demo"},
    {"logger": "demo"}, {"since": "1h"}, {"until": "now"},
    {"order": []}, {"order": "reverse"}, {"limit": True}, {"limit": 1.5},
    {"limit": 0}, {"limit": 101}, {"search": []}, {"search": "x" * 513},
    {"cursor": []}, {"cursor": "x" * 1025}, {"cursor": "invalid"},
])
async def test_invalid_arguments_do_not_read_files(context, args, monkeypatch):
    reads = []
    monkeypatch.setattr(fault_log, "read_fault_snapshot", lambda *a: reads.append(a))
    _, outcome, error = await _call(context, **args)
    assert outcome == "invalid_request" and error
    assert not reads  # malformed cursors must be rejected before file I/O too


@pytest.mark.parametrize("cap", ["cap_log_read", "cap_diagnostics"])
async def test_deny_precedes_validation_and_io(context, cap, monkeypatch):
    _, access_key, _, _ = context
    setattr(access_key, cap, "deny")
    monkeypatch.setattr(fault_log, "read_fault_snapshot", lambda *a: pytest.fail("denied read"))
    body, outcome, error = await _call(context, limit=[])
    assert body == "Forbidden." and outcome == "denied" and error


async def test_pass_through_still_requires_exempt_log_grant(context):
    _, access_key, _, _ = context
    access_key.pass_through = True
    access_key.cap_log_read = "deny"
    body, outcome, _ = await _call(context)
    assert body == "Forbidden." and outcome == "denied"


@pytest.mark.parametrize("change", ["revoke", "rotate", "kill", "unload", "permissions", "capability"])
async def test_authority_changes_during_io_block_disclosure(context, change, monkeypatch):
    hass, access_key, data, path = context
    path.write_text(_dump("must-not-disclose"))
    original = hass.async_add_executor_job

    async def changed_read(function, *args):
        value = await original(function, *args)
        if change == "revoke":
            access_key.revoked = True
        elif change == "rotate":
            await data.store.async_rotate_access_key(access_key.id)
        elif change == "kill":
            data.store.get_settings().kill_switch = True
        elif change == "unload":
            data.shutting_down = True
        elif change == "permissions":
            access_key.settings_version += 1
        else:
            access_key.cap_diagnostics = "deny"
        return value

    monkeypatch.setattr(hass, "async_add_executor_job", changed_read)
    body, outcome, error = await _call(context)
    assert outcome == "denied" and error and "must-not-disclose" not in json.dumps(body)


async def test_tail_line_and_byte_bounds_discard_leading_fragments(context):
    *_, path = context
    for prefix in ("x\n" * FAULT_LOG_MAX_LINES, "x" * FAULT_LOG_MAX_BYTES + "\n"):
        path.write_text(_dump("outside-window") + prefix + _dump("retained"))
        body, _, _ = await _call(context)
        assert body["source"]["clipped"] and body["source"]["status"] == "degraded"
        assert body["count"] == 1 and "retained" in body["entries"][0]["text"]
        assert "outside-window" not in json.dumps(body)


@pytest.mark.parametrize("text", ["unrecognized\n", _dump()[:-1], "Fatal Python error: Aborted\n"])
async def test_unknown_partial_content_is_not_healthy(context, text):
    *_, path = context
    path.write_text(text)
    body, _, _ = await _call(context)
    assert body["source"]["status"] == "degraded" and body["count"] == 0
    assert body["crash_recorded"] is not False


async def test_oversize_block_is_omitted_not_split(context):
    *_, path = context
    path.write_text(_dump("huge") + "x" * FAULT_LOG_MAX_BLOCK_BYTES + "\n" + _dump("small"))
    body, _, _ = await _call(context)
    assert body["crash_recorded"] is True and body["source"]["status"] == "degraded"
    assert body["count"] == 1 and "small" in body["entries"][0]["text"]


async def test_byte_budget_pages_without_splitting(context):
    *_, path = context
    path.write_text("".join(_dump(str(i)) + "x" * 20000 + "\n" for i in range(6)))
    seen = []
    cursor = None
    while True:
        body, _, error = await _call(context, cursor=cursor, limit=100)
        assert not error
        assert sum(len(json.dumps(entry).encode()) for entry in body["entries"]) <= FAULT_LOG_MAX_PAGE_BYTES
        seen.extend(body["entries"])
        if not body["has_more"]:
            break
        assert body["entries"]
        cursor = body["next_cursor"]
    assert len(seen) == 6


async def test_cursor_bound_to_snapshot_filters_and_token(context):
    _, access_key, _, path = context
    path.write_text(_dump("old") + _dump("new"))
    body, _, _ = await _call(context, limit=1)
    cursor = body["next_cursor"]
    for args in ({"order": "oldest_first"}, {"search": "old"}, {"cursor": cursor[:-1] + "X"}):
        _, outcome, error = await _call(context, **{"cursor": cursor, **args})
        assert outcome == "invalid_request" and error
    old_hash = access_key.access_key_hash
    access_key.access_key_hash = "b" * 64
    _, outcome, error = await _call(context, cursor=cursor)
    assert outcome == "invalid_request" and error
    access_key.access_key_hash = old_hash
    path.write_text(path.read_text() + _dump("newest"))
    _, outcome, error = await _call(context, cursor=cursor)
    assert outcome == "invalid_request" and error


async def test_fully_clipped_window_cannot_claim_no_crash(context):
    *_, path = context
    path.write_text(_dump("lost-header") + "x" * (FAULT_LOG_MAX_BYTES + 1))
    body, _, error = await _call(context)
    assert not error and body["source"]["clipped"]
    assert body["source"]["status"] == "degraded"
    assert body["crash_recorded"] is None and body["count"] == 0


async def test_page_budget_includes_metadata_and_cursor(context):
    *_, path = context
    dumps = []
    for index in range(3):
        block = _dump(str(index))
        padding = FAULT_LOG_MAX_BLOCK_BYTES - len(block.encode()) - 2
        dumps.append(block + "x" * padding + "\n")
    path.write_text("".join(dumps))
    body, _, error = await _call(context, limit=100)
    assert not error and body["matched_blocks"] == 3 and body["has_more"]
    assert len(json.dumps(body).encode()) <= FAULT_LOG_MAX_PAGE_BYTES
