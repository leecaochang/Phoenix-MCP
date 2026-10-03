"""Adversarial contracts for bounded system-log and Phoenix diagnostics reads."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.components.system_log import DedupStore
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import (
    DOMAIN,
    SYSTEM_LOG_MAX_DETAIL_BYTES,
    SYSTEM_LOG_MAX_EXCEPTION_BYTES,
    SYSTEM_LOG_MAX_MESSAGE_BYTES,
    SYSTEM_LOG_MAX_RESPONSE_BYTES,
    TOKEN_PREFIX,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.policy_engine import Permission
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from tests.log_fixtures import attach_log_store, make_log_entry, make_log_store
from tests.test_mcp_view import _make_data, _make_hass, _make_token


async def _call(tool: str, args: dict, *, token=None, hass=None, data=None):
    if token is None:
        token, _ = _make_token(cap_log_read="allow")
    if data is None:
        data = _make_data(token)
    if hass is None:
        hass = _make_hass(data)
        attach_log_store(hass, DedupStore(maxlen=50))
    response, _method, _resource, outcome = await mcp_view._dispatch_mcp(
        "tools/call",
        3,
        {"name": tool, "arguments": args},
        token,
        hass,
        data,
        "127.0.0.1",
        base_url="http://homeassistant.local",
    )
    if "error" in response:
        return response["error"], {"isError": True}, outcome
    text = response["result"]["content"][0]["text"]
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        body = text
    return body, response["result"], outcome


def _hass_with_entries(token, entries, *, capacity=50):
    data = _make_data(token)
    hass = _make_hass(data)
    attach_log_store(hass, make_log_store(entries, capacity=capacity))
    return hass, data


@pytest.mark.asyncio
async def test_empty_real_store_is_available_and_truthfully_empty():
    body, result, outcome = await _call("get_logs", {})
    assert outcome == "allowed"
    assert "isError" not in result
    assert body["source"] == {
        "status": "available",
        "kind": "home_assistant_system_log",
        "semantics": "deduplicated_buckets",
        "pagination": "best_effort_live_ring",
        "capacity": 50,
        "retained_buckets": 0,
        "skipped_buckets": 0,
        "earliest_first_occurred": None,
        "latest_occurred": None,
        "read_at": body["source"]["read_at"],
        "recorded_levels": ["WARNING", "ERROR", "CRITICAL"],
    }
    assert body["count"] == body["matched_buckets"] == 0
    assert body["entries"] == []


@pytest.mark.asyncio
async def test_absent_and_unreadable_sources_are_structured_tool_errors():
    token, _ = _make_token(cap_log_read="allow")
    data = _make_data(token)
    hass = _make_hass(data)
    body, result, outcome = await _call(
        "get_logs", {}, token=token, hass=hass, data=data
    )
    assert outcome == "invalid_request"
    assert result["isError"] is True
    assert body["source"]["status"] == "unavailable"
    assert body["entries"] == []
    assert body["filters"]["time_basis"] == "latest_occurrence"

    class UnreadableRecords:
        maxlen = 50

        def values(self):
            raise RuntimeError("changed upstream")

    hass.data["system_log"] = SimpleNamespace(records=UnreadableRecords())
    body, result, outcome = await _call(
        "get_logs", {}, token=token, hass=hass, data=data
    )
    assert outcome == "invalid_request"
    assert result["isError"] is True
    assert body["source"]["status"] == "degraded"
    assert "changed upstream" not in body["source"]["reason"]


@pytest.mark.asyncio
async def test_partial_shape_drift_skips_only_bad_buckets_and_reports_coverage():
    token, _ = _make_token(cap_log_read="allow")
    good = make_log_entry(created=1_700_000_010)
    good.first_occurred = 1_700_000_000
    store = make_log_store([good], capacity=77)
    store["bad"] = SimpleNamespace(level="ERROR")
    hass, data = _hass_with_entries(token, [])
    attach_log_store(hass, store)
    body, _result, outcome = await _call(
        "get_logs", {}, token=token, hass=hass, data=data
    )
    assert outcome == "allowed"
    assert body["source"]["status"] == "degraded"
    assert body["source"]["capacity"] == 77
    assert body["source"]["retained_buckets"] == 2
    assert body["source"]["skipped_buckets"] == 1
    assert body["source"]["earliest_first_occurred"] == "2023-11-14T22:13:20+00:00"
    assert body["source"]["latest_occurred"] == "2023-11-14T22:13:30+00:00"
    assert body["count"] == 1
    assert "Skipped 1 malformed" in body["warnings"][0]


@pytest.mark.asyncio
async def test_bucket_with_unreadable_messages_is_skipped_not_raised():
    class BrokenMessages:
        def __iter__(self):
            raise RuntimeError("bad bucket")

    token, _ = _make_token(cap_log_read="allow")
    good = make_log_entry()
    broken = SimpleNamespace(
        level="ERROR",
        name="homeassistant.components.light",
        timestamp=1_700_000_001,
        first_occurred=1_700_000_001,
        message=BrokenMessages(),
        count=1,
    )
    store = make_log_store([good])
    store["broken"] = broken
    hass, data = _hass_with_entries(token, [])
    attach_log_store(hass, store)
    body, _result, outcome = await _call(
        "get_logs", {}, token=token, hass=hass, data=data
    )
    assert outcome == "allowed"
    assert body["source"]["status"] == "degraded"
    assert body["source"]["skipped_buckets"] == 1
    assert body["count"] == 1


@pytest.mark.asyncio
async def test_real_dedup_store_preserves_all_five_retained_message_variants():
    token, _ = _make_token(cap_log_read="allow")
    entries = [
        make_log_entry(message=f"variant {index}", created=1_700_000_000 + index)
        for index in range(7)
    ]
    hass, data = _hass_with_entries(token, entries)
    body, _result, outcome = await _call(
        "get_logs", {}, token=token, hass=hass, data=data
    )
    assert outcome == "allowed"
    assert body["count"] == 1
    assert body["entries"][0]["messages"] == [
        "variant 2",
        "variant 3",
        "variant 4",
        "variant 5",
        "variant 6",
    ]
    assert body["entries"][0]["occurrences"] == 7
    assert body["entries"][0]["first_occurred"] == "2023-11-14T22:13:20+00:00"
    assert body["entries"][0]["latest_occurred"] == "2023-11-14T22:13:26+00:00"


@pytest.mark.asyncio
async def test_source_and_root_cause_paths_are_normalized_without_host_disclosure():
    token, _ = _make_token(cap_log_read="allow")
    safe = make_log_entry(
        pathname="/usr/lib/homeassistant/components/light/__init__.py",
        lineno=44,
    )
    safe.root_cause = (
        "/config/custom_components/example/client.py",
        91,
        "async_update",
    )
    unsafe = make_log_entry(
        pathname="/srv/private/vendor/secret.py",
        lineno=12,
        created=1_700_000_001,
    )
    hass, data = _hass_with_entries(token, [safe, unsafe])
    body, _result, _outcome = await _call(
        "get_logs", {}, token=token, hass=hass, data=data
    )
    by_line = {entry["source"]["line"]: entry for entry in body["entries"]}
    assert by_line[44]["source"] == {
        "file": "homeassistant/components/light/__init__.py",
        "line": 44,
    }
    assert by_line[44]["root_cause"] == {
        "file": "custom_components/example/client.py",
        "line": 91,
        "function": "async_update",
    }
    assert by_line[12]["source"] == {"file": "<redacted-path>", "line": 12}


@pytest.mark.asyncio
async def test_filters_use_latest_occurrence_and_complete_logger_namespaces():
    token, _ = _make_token(cap_log_read="allow")
    entries = [
        make_log_entry(
            name="homeassistant.components.light",
            message="light exact",
            created=1_700_000_010,
            pathname="/config/custom_components/light/a.py",
        ),
        make_log_entry(
            name="homeassistant.components.light.child",
            message="light child",
            created=1_700_000_020,
            pathname="/config/custom_components/light/b.py",
        ),
        make_log_entry(
            name="homeassistant.components.lighthouse",
            message="lookalike",
            created=1_700_000_030,
            pathname="/config/custom_components/lighthouse/a.py",
        ),
    ]
    hass, data = _hass_with_entries(token, entries)
    args = {
        "integration": "light",
        "logger": "homeassistant.components.light",
        "search": "LIGHT",
        "since": "2023-11-14T22:13:35+00:00",
        "until": "2023-11-14T22:13:45+00:00",
    }
    body, _result, outcome = await _call(
        "get_logs", args, token=token, hass=hass, data=data
    )
    assert outcome == "allowed"
    assert [entry["messages"][0] for entry in body["entries"]] == ["light child"]
    assert body["filters"] == {
        "level": "WARNING",
        "integration": "light",
        "logger": "homeassistant.components.light",
        "search": "LIGHT",
        "since": "2023-11-14T22:13:35+00:00",
        "until": "2023-11-14T22:13:45+00:00",
        "time_basis": "latest_occurrence",
    }
    integration_only, _result, _outcome = await _call(
        "get_logs", {"integration": "light"}, token=token, hass=hass, data=data
    )
    assert {entry["messages"][0] for entry in integration_only["entries"]} == {
        "light exact",
        "light child",
    }


@pytest.mark.asyncio
async def test_search_runs_after_scrubbing_and_cannot_probe_raw_credentials():
    raw_token = TOKEN_PREFIX + "a" * 64
    token, _ = _make_token(cap_log_read="allow")
    entry = make_log_entry(message="request rejected")
    entry.exception = f"authentication failed for {raw_token}"
    hass, data = _hass_with_entries(token, [entry])
    raw_body, _result, _outcome = await _call(
        "get_logs", {"search": raw_token}, token=token, hass=hass, data=data
    )
    safe_body, _result, _outcome = await _call(
        "get_logs", {"search": "<phoenix-token>"}, token=token, hass=hass, data=data
    )
    assert raw_body["matched_buckets"] == 0
    assert safe_body["matched_buckets"] == 1
    assert raw_token not in json.dumps(safe_body)


@pytest.mark.asyncio
async def test_naive_and_timezone_aware_iso_bounds_share_utc_semantics():
    token, _ = _make_token(cap_log_read="allow")
    entry = make_log_entry(created=1_700_000_000)
    hass, data = _hass_with_entries(token, [entry])
    body, _result, outcome = await _call(
        "get_logs",
        {
            "since": "2023-11-14T22:00:00",
            "until": "2023-11-14T23:00:00Z",
        },
        token=token,
        hass=hass,
        data=data,
    )
    assert outcome == "allowed"
    assert body["count"] == 1
    assert body["filters"]["since"] == "2023-11-14T22:00:00+00:00"


@pytest.mark.asyncio
async def test_pagination_is_filter_bound_deterministic_and_reports_live_semantics():
    token, _ = _make_token(cap_log_read="allow")
    entries = [
        make_log_entry(
            message=f"row {index}",
            created=1_700_000_100,
            pathname=f"/config/custom_components/example/file_{index}.py",
        )
        for index in range(5)
    ]
    hass, data = _hass_with_entries(token, entries)
    first, _result, _outcome = await _call(
        "get_logs", {"limit": 2}, token=token, hass=hass, data=data
    )
    second, _result, _outcome = await _call(
        "get_logs",
        {"limit": 2, "cursor": first["next_cursor"]},
        token=token,
        hass=hass,
        data=data,
    )
    assert first["matched_buckets"] == second["matched_buckets"] == 5
    assert first["source"]["pagination"] == "best_effort_live_ring"
    first_messages = {entry["messages"][0] for entry in first["entries"]}
    second_messages = {entry["messages"][0] for entry in second["entries"]}
    assert first_messages.isdisjoint(second_messages)
    assert second["warnings"] == [
        "Pagination is best-effort over a live deduplicated ring; buckets updated after the first page may move."
    ]

    error, result, outcome = await _call(
        "get_logs",
        {"limit": 2, "level": "ERROR", "cursor": first["next_cursor"]},
        token=token,
        hass=hass,
        data=data,
    )
    assert outcome == "invalid_request"
    assert result["isError"] is True
    assert error == "Invalid cursor for these log filters."


@pytest.mark.asyncio
async def test_relative_time_cursor_reuses_the_original_resolved_window():
    token, _ = _make_token(cap_log_read="allow")
    now = utcnow().timestamp()
    entries = [
        make_log_entry(
            message=f"recent {index}",
            created=now - index,
            pathname=f"/config/custom_components/example/recent_{index}.py",
        )
        for index in range(3)
    ]
    hass, data = _hass_with_entries(token, entries)
    first, _result, _outcome = await _call(
        "get_logs", {"since": "1h", "limit": 1}, token=token, hass=hass, data=data
    )
    second, _result, outcome = await _call(
        "get_logs",
        {"since": "1h", "limit": 1, "cursor": first["next_cursor"]},
        token=token,
        hass=hass,
        data=data,
    )
    assert outcome == "allowed"
    assert second["filters"]["since"] == first["filters"]["since"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"since": "2024-01-02T00:00:00+00:00", "until": "2024-01-01T00:00:00+00:00"},
        {"integration": "Light!"},
        {"logger": "homeassistant..light"},
        {"search": "x" * 513},
        {"cursor": "x" * 4097},
    ],
)
async def test_invalid_log_filters_fail_before_reading_the_source(args):
    token, _ = _make_token(cap_log_read="allow")
    data = _make_data(token)
    hass = _make_hass(data)
    body, result, outcome = await _call(
        "get_logs", args, token=token, hass=hass, data=data
    )
    assert outcome == "invalid_request"
    assert result["isError"] is True
    assert isinstance(body, str)


@pytest.mark.asyncio
async def test_phoenix_diagnostics_requires_both_capabilities_before_validation():
    for log_mode, diagnostics_mode in (("deny", "allow"), ("allow", "deny")):
        token, _ = _make_token(cap_log_read=log_mode)
        token.cap_diagnostics = diagnostics_mode
        body, _result, outcome = await _call(
            "get_phoenix_diagnostics", {"level": "INFO"}, token=token
        )
        assert outcome == "denied"
        assert body == "Forbidden."


@pytest.mark.asyncio
async def test_phoenix_diagnostics_is_separate_and_strongly_redacted():
    token, _ = _make_token(cap_log_read="allow")
    token.cap_diagnostics = "allow"
    phoenix = make_log_entry(
        name="custom_components.phoenix_mcp.mcp_view",
        message=(
            "entity sensor.allowed denied sensor.hidden at http://nas.local:8123/a "
            "/config/.storage/core 10.0.0.4 "
            "123e4567-e89b-12d3-a456-426614174000 "
            "01ARZ3NDEKTSV4RRFFQ69G5FAV "
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        ),
    )
    lookalike = make_log_entry(
        name="custom_components.phoenix_mcp_extra",
        message="not Phoenix",
        created=1_700_000_001,
        pathname="/config/custom_components/phoenix_mcp_extra/a.py",
    )
    ordinary = make_log_entry(
        name="homeassistant.components.light",
        message="ordinary",
        created=1_700_000_002,
    )
    hass, data = _hass_with_entries(token, [phoenix, lookalike, ordinary])

    def permission(entity_id, _token, _hass):
        return Permission.READ if entity_id == "sensor.allowed" else Permission.NO_ACCESS

    with patch("custom_components.phoenix_mcp.helpers.resolve", side_effect=permission):
        general, _result, _outcome = await _call(
            "get_logs", {}, token=token, hass=hass, data=data
        )
        diagnostics, _result, outcome = await _call(
            "get_phoenix_diagnostics",
            {"logger": "custom_components.phoenix_mcp"},
            token=token,
            hass=hass,
            data=data,
        )

    assert outcome == "allowed"
    assert {entry["messages"][0] for entry in general["entries"]} == {
        "not Phoenix",
        "ordinary",
    }
    assert diagnostics["count"] == 1
    text = diagnostics["entries"][0]["messages"][0]
    assert "sensor.allowed" in text
    assert "sensor.hidden" not in text
    assert "<redacted-entity>" in text
    assert "nas.local" not in text
    assert "/config" not in text
    assert "10.0.0.4" not in text
    assert "123e4567" not in text
    assert "01ARZ3" not in text
    assert "aaaaaaaaaaaaaaaa" not in text
    assert diagnostics["source"]["recorded_levels"] == [
        "WARNING",
        "ERROR",
        "CRITICAL",
    ]

    with patch(
        "custom_components.phoenix_mcp.helpers.resolve",
        side_effect=RuntimeError("registry drift"),
    ):
        fail_closed, _result, fail_closed_outcome = await _call(
            "get_phoenix_diagnostics", {}, token=token, hass=hass, data=data
        )
    assert fail_closed_outcome == "allowed"
    assert "sensor.allowed" not in fail_closed["entries"][0]["messages"][0]


@pytest.mark.asyncio
async def test_phoenix_search_cannot_probe_pre_redaction_topology():
    token, _ = _make_token(cap_log_read="allow")
    token.cap_diagnostics = "allow"
    entry = make_log_entry(
        name="custom_components.phoenix_mcp",
        message="connection from 192.168.1.55 failed",
    )
    hass, data = _hass_with_entries(token, [entry])
    with patch(
        "custom_components.phoenix_mcp.helpers.resolve",
        return_value=Permission.NO_ACCESS,
    ):
        raw, _result, _outcome = await _call(
            "get_phoenix_diagnostics",
            {"search": "192.168.1.55"},
            token=token,
            hass=hass,
            data=data,
        )
        safe, _result, _outcome = await _call(
            "get_phoenix_diagnostics",
            {"search": "<redacted-ip>"},
            token=token,
            hass=hass,
            data=data,
        )
    assert raw["matched_buckets"] == 0
    assert safe["matched_buckets"] == 1


@pytest.mark.asyncio
async def test_dual_cap_tool_announcement_and_gate_map_require_both_caps():
    for log_mode, diagnostics_mode, expected in (
        ("allow", "deny", False),
        ("deny", "allow", False),
        ("allow", "allow", True),
    ):
        token, _ = _make_token(cap_log_read=log_mode)
        token.cap_diagnostics = diagnostics_mode
        data = _make_data(token)
        hass = _make_hass(data)
        response, _method, _resource, _outcome = await mcp_view._dispatch_mcp(
            "tools/list",
            1,
            {},
            token,
            hass,
            data,
            "127.0.0.1",
            base_url="http://homeassistant.local",
        )
        names = {tool["name"] for tool in response["result"]["tools"]}
        gate_map = mcp_view._tool_gate_map(token, data, hass)
        assert ("get_phoenix_diagnostics" in names) is expected
        bucket = "usable" if expected else "unavailable"
        assert "get_phoenix_diagnostics" in gate_map[bucket]
        published = next(
            tool
            for tool in mcp_view._SYSTEM_TOOL_DEFS
            if tool["name"] == "get_phoenix_diagnostics"
        )
        assert published["caps"] == ["cap_diagnostics", "cap_log_read"]


def _binary_warning():
    return (
        "Detected blocking call to write_bytes with args (b'"
        + "\\xff\\xd8\\x00" * 20000
        + "') inside the event loop\n"
        + '  File "homeassistant/components/example/camera.py", line 27, in write_image\n'
        + "    path.write_bytes(image)\n"
    )


def _long_traceback():
    return (
        "Traceback (most recent call last):\n"
        + ''.join(f'  File "components/example/client.py", line {index + 1}, in frame_{index}\n'
                  for index in range(500))
        + '  File "components/example/client.py", line 501, in innermost_failure\n'
        + "ValueError: final diagnosis\n"
    )


@pytest.mark.asyncio
async def test_binary_warning_and_traceback_have_bounded_useful_ends_and_metadata():
    token, _ = _make_token(cap_log_read="allow")
    entry = make_log_entry(message=_binary_warning())
    entry.exception = _long_traceback()
    hass, data = _hass_with_entries(token, [entry])
    body, result, outcome = await _call("get_logs", {"limit": 1}, token=token, hass=hass, data=data)
    assert outcome == "allowed"
    preview = body["entries"][0]
    assert preview["messages"][0].startswith("Detected blocking call to write_bytes")
    assert preview["messages"][0].endswith("    path.write_bytes(image)\n")
    assert preview["exception"].startswith("Traceback (most recent call last):")
    assert "innermost_failure" in preview["exception"]
    assert preview["exception"].endswith("ValueError: final diagnosis\n")
    for text, budget, metadata in (
        (preview["messages"][0], SYSTEM_LOG_MAX_MESSAGE_BYTES, preview["truncation"]["messages"][0]),
        (preview["exception"], SYSTEM_LOG_MAX_EXCEPTION_BYTES, preview["truncation"]["exception"]),
    ):
        assert len(json.dumps(text).encode()) <= budget
        assert metadata["omitted_chars"] > 0
        assert metadata["original_chars"] == metadata["omitted_chars"] + metadata["head_chars"] + metadata["tail_chars"]
    assert body["payload"]["byte_limited"] is False
    assert body["matched_buckets"] == 1 and body["has_more"] is False
    assert len(json.dumps(result).encode()) <= SYSTEM_LOG_MAX_RESPONSE_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["message", "exception"])
async def test_search_matches_text_omitted_from_previews(field):
    token, _ = _make_token(cap_log_read="allow")
    text = "begin " + "x" * 10000 + "needle-in-cut" + "y" * 10000 + " end"
    entry = make_log_entry(message=text if field == "message" else "failure")
    entry.exception = text if field == "exception" else ""
    hass, data = _hass_with_entries(token, [entry])
    body, _result, outcome = await _call("get_logs", {"search": "NEEDLE-IN-CUT"}, token=token, hass=hass, data=data)
    assert outcome == "allowed" and body["matched_buckets"] == 1
    value = body["entries"][0]["messages"][0] if field == "message" else body["entries"][0]["exception"]
    assert "needle-in-cut" not in value


@pytest.mark.asyncio
async def test_phoenix_compaction_and_search_use_the_full_scrubbed_text():
    token, _ = _make_token(cap_log_read="allow")
    token.cap_diagnostics = "allow"
    entry = make_log_entry(name="custom_components.phoenix_mcp", message=_binary_warning() + "retained end")
    entry.exception = "traceback start\n" + "x " * 10000 + "needle-in-cut 192.168.1.55 " + "y " * 10000 + "innermost end"
    hass, data = _hass_with_entries(token, [entry])
    body, result, outcome = await _call("get_phoenix_diagnostics", {"search": "needle-in-cut"},
                                       token=token, hass=hass, data=data)
    assert outcome == "allowed" and body["count"] == 1
    preview = body["entries"][0]
    assert preview["truncation"]["messages"][0]["omitted_chars"] > 0
    assert preview["truncation"]["exception"]["omitted_chars"] > 0
    assert "needle-in-cut" not in preview["exception"] and preview["exception"].endswith("innermost end")
    assert len(json.dumps(result).encode()) <= SYSTEM_LOG_MAX_RESPONSE_BYTES
    raw, _result, _outcome = await _call("get_phoenix_diagnostics", {"search": "192.168.1.55"},
                                        token=token, hass=hass, data=data)
    assert raw["matched_buckets"] == 0


@pytest.mark.asyncio
async def test_unexpected_variant_count_is_explicit_and_omitted_variants_remain_retrievable():
    token, _ = _make_token(cap_log_read="allow")
    entry = make_log_entry()
    entry.message = [f"variant {index}" for index in range(8)]
    hass, data = _hass_with_entries(token, [entry])
    body, _result, _outcome = await _call("get_logs", {"search": "variant 7"}, token=token, hass=hass, data=data)
    preview = body["entries"][0]
    assert len(preview["messages"]) == 5 and preview["truncation"]["omitted_message_variants"] == 3
    detail, _result, outcome = await _call("get_logs", {"detail_ref": preview["detail_ref"], "message_index": 7},
                                          token=token, hass=hass, data=data)
    assert outcome == "allowed" and detail["detail"]["text"] == "variant 7"


@pytest.mark.asyncio
async def test_detail_boundaries_reject_missing_variants_and_out_of_range_offsets():
    token, _ = _make_token(cap_log_read="allow")
    entry = make_log_entry(message="short")
    hass, data = _hass_with_entries(token, [entry])
    body, _result, _outcome = await _call("get_logs", {}, token=token, hass=hass, data=data)
    reference = body["entries"][0]["detail_ref"]
    for arguments in ({"message_index": 1}, {"detail_offset": 6}):
        error, result, outcome = await _call("get_logs", {"detail_ref": reference, **arguments},
                                            token=token, hass=hass, data=data)
        assert outcome == "invalid_request" and result["isError"] and "short" not in error
    empty, _result, outcome = await _call("get_logs", {"detail_ref": reference, "detail_field": "exception"},
                                          token=token, hass=hass, data=data)
    assert outcome == "allowed" and empty["detail"]["text"] == "" and not empty["detail"]["has_more"]


@pytest.mark.asyncio
async def test_large_escaped_metadata_and_payload_still_allow_pagination_progress():
    token, _ = _make_token(cap_log_read="allow")
    entry = make_log_entry(name="homeassistant.components." + "x" * 20000,
                           message='"' * 50000,
                           pathname="/config/custom_components/" + "x" * 20000 + "/file.py")
    entry.message = ['"' * 50000] * 5
    entry.exception = '"' * 50000
    entry.root_cause = ("/config/custom_components/" + "y" * 20000 + "/file.py", 1, "z" * 20000)
    hass, data = _hass_with_entries(token, [entry])
    body, result, outcome = await _call("get_logs", {}, token=token, hass=hass, data=data)
    assert outcome == "allowed" and body["count"] == 1
    assert body["entries"][0]["truncation"]["metadata"]["logger"]["omitted_chars"] > 0
    assert len(json.dumps(result).encode()) <= SYSTEM_LOG_MAX_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_all_retained_variants_and_exception_can_be_reassembled_in_bounded_chunks():
    token, _ = _make_token(cap_log_read="allow")
    entries = [make_log_entry(message=f"variant {index}: " + '\u4e2d"\\\n' * 3000,
                              created=1_700_000_000 + index) for index in range(7)]
    hass, data = _hass_with_entries(token, entries)
    record = next(iter(hass.data["system_log"].records.values()))
    record.exception = _long_traceback()
    body, _result, _outcome = await _call("get_logs", {}, token=token, hass=hass, data=data)
    preview = body["entries"][0]
    assert len(preview["messages"]) == 5
    assert preview["truncation"]["omitted_message_variants"] == 0
    assert all(item["omitted_chars"] for item in preview["truncation"]["messages"])
    for field, index, expected in [*(('message', index, value) for index, value in enumerate(record.message)),
                                   ("exception", None, record.exception)]:
        arguments = {"detail_ref": preview["detail_ref"], "detail_field": field}
        if index is not None:
            arguments["message_index"] = index
        parts = []
        offset = 0
        while True:
            chunk, result, outcome = await _call("get_logs", {**arguments, "detail_offset": offset},
                                                 token=token, hass=hass, data=data)
            assert outcome == "allowed"
            detail = chunk["detail"]
            assert detail["offset"] == offset and detail["total_chars"] == len(expected)
            assert len(json.dumps(detail["text"]).encode()) <= SYSTEM_LOG_MAX_DETAIL_BYTES
            assert len(json.dumps(result).encode()) <= SYSTEM_LOG_MAX_RESPONSE_BYTES
            parts.append(detail["text"])
            if not detail["has_more"]:
                assert detail["next_offset"] is None
                break
            assert detail["next_offset"] == detail["end_offset"] > offset
            offset = detail["next_offset"]
        assert ''.join(parts) == expected
        end, _result, outcome = await _call("get_logs", {**arguments, "detail_offset": len(expected)},
                                            token=token, hass=hass, data=data)
        assert outcome == "allowed" and end["detail"]["text"] == ""
        assert end["detail"]["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["x", "\u4e2d", chr(0x10000), '"\\\n', "\ud800"])
async def test_byte_limited_pagination_visits_every_bucket_without_losing_coverage(unit):
    token, _ = _make_token(cap_log_read="allow")
    entries = []
    for bucket in range(12):
        for variant in range(5):
            item = make_log_entry(message=f"bucket {bucket} variant {variant}: " + unit * 10000,
                                  created=1_700_000_000 + bucket,
                                  pathname=f"/config/custom_components/example/bucket_{bucket}.py")
            item.exception = _long_traceback()
            entries.append(item)
    hass, data = _hass_with_entries(token, entries)
    arguments = {"limit": 100}
    seen = []
    pages = 0
    while True:
        body, result, outcome = await _call("get_logs", arguments, token=token, hass=hass, data=data)
        assert outcome == "allowed"
        assert len(json.dumps(result).encode()) <= SYSTEM_LOG_MAX_RESPONSE_BYTES
        assert body["matched_buckets"] == body["source"]["retained_buckets"] == 12
        assert body["source"]["skipped_buckets"] == 0
        assert body["source"]["status"] == "available"
        assert body["count"] > 0
        seen.extend(entry["detail_ref"] for entry in body["entries"])
        pages += 1
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        assert body["payload"]["byte_limited"] and body["next_cursor"]
        arguments["cursor"] = body["next_cursor"]
    assert len(seen) == len(set(seen)) == 12
    assert pages > 1


@pytest.mark.asyncio
async def test_detail_references_are_bound_to_token_namespace_and_current_bucket():
    token, _ = _make_token(cap_log_read="allow")
    token.cap_diagnostics = "allow"
    entry = make_log_entry(message="must-not-disclose " + "x" * 10000)
    hass, data = _hass_with_entries(token, [entry])
    body, _result, _outcome = await _call("get_logs", {}, token=token, hass=hass, data=data)
    args = {"detail_ref": body["entries"][0]["detail_ref"]}
    other, _ = _make_token(cap_log_read="allow")
    data.store.get_token_by_hash.side_effect = lambda value: token if value == token.token_hash else other if value == other.token_hash else None
    for tool, caller in (("get_logs", other), ("get_phoenix_diagnostics", token)):
        error, result, outcome = await _call(tool, args, token=caller, hass=hass, data=data)
        assert outcome == "invalid_request" and result["isError"]
        assert "must-not-disclose" not in json.dumps(error)
    entry.message.append("new variant")
    error, result, outcome = await _call("get_logs", args, token=token, hass=hass, data=data)
    assert outcome == "invalid_request" and "stale" in error and result["isError"]


@pytest.mark.asyncio
async def test_phoenix_detail_uses_current_strong_scrubbing_and_scope():
    token, _ = _make_token(cap_log_read="allow")
    token.cap_diagnostics = "allow"
    message = "diagnosis sensor.allowed sensor.hidden http://nas.local/test 192.168.1.55 /config/secrets " + TOKEN_PREFIX + "a" * 64
    entry = make_log_entry(name="custom_components.phoenix_mcp", message=message)
    hass, data = _hass_with_entries(token, [entry])
    def permission(entity_id, _token, _hass):
        return Permission.READ if entity_id == "sensor.allowed" else Permission.NO_ACCESS
    with patch("custom_components.phoenix_mcp.helpers.resolve", side_effect=permission):
        body, _result, _outcome = await _call("get_phoenix_diagnostics", {}, token=token, hass=hass, data=data)
        args = {"detail_ref": body["entries"][0]["detail_ref"]}
        chunk, _result, outcome = await _call("get_phoenix_diagnostics", args, token=token, hass=hass, data=data)
    assert outcome == "allowed" and "sensor.allowed" in chunk["detail"]["text"]
    for hidden in ("sensor.hidden", "nas.local", "192.168.1.55", "/config", TOKEN_PREFIX + "a" * 64):
        assert hidden not in chunk["detail"]["text"]
    with patch("custom_components.phoenix_mcp.helpers.resolve", return_value=Permission.NO_ACCESS):
        error, result, outcome = await _call("get_phoenix_diagnostics", args, token=token, hass=hass, data=data)
    assert outcome == "invalid_request" and result["isError"] and "stale" in error


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_logs", "get_phoenix_diagnostics"])
async def test_detail_capability_denial_precedes_argument_validation_even_in_pass_through(tool):
    token, _ = _make_token(cap_log_read="deny")
    token.pass_through = True
    token.cap_diagnostics = "allow"
    body, result, outcome = await _call(tool, {"detail_ref": []}, token=token)
    assert outcome == "denied" and result["isError"] and body == "Forbidden."


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"detail_ref": None}, {"detail_ref": []}, {"detail_ref": "x" * 64},
    {"detail_field": "exception"}, {"detail_offset": 0}, {"message_index": 0},
    {"detail_ref": "a" * 64, "limit": 1}, {"detail_ref": "a" * 64, "search": "x"},
    {"detail_ref": "a" * 64, "cursor": "x"}, {"detail_ref": "a" * 64, "detail_field": []},
    {"detail_ref": "a" * 64, "message_index": True}, {"detail_ref": "a" * 64, "message_index": -1},
    {"detail_ref": "a" * 64, "detail_offset": 1.5}, {"detail_ref": "a" * 64, "detail_offset": -1},
    {"detail_ref": "a" * 64, "detail_field": "exception", "message_index": 0},
    {"source": "fault_log", "detail_ref": "a" * 64},
])
async def test_invalid_detail_arguments_fail_before_source_access(args):
    token, _ = _make_token(cap_log_read="allow")
    token.cap_diagnostics = "allow"
    data = _make_data(token)
    hass = _make_hass(data)
    body, result, outcome = await _call("get_logs", args, token=token, hass=hass, data=data)
    assert outcome == "invalid_request" and result["isError"] and isinstance(body, str)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "rotate", "kill", "unload", "permissions", "capability"])
async def test_detail_authority_changes_during_collection_block_disclosure(change, hass, token_store):
    token, _ = await token_store.async_create_token("system-log-test", "admin", rate_limit_requests=0)
    token.cap_log_read = "allow"
    data = PhoenixData(store=token_store, rate_limiter=RateLimiter(), audit=AuditLog(), enforce_mcp_lifecycle=False)
    hass.data[DOMAIN] = data
    entry = make_log_entry(message="must-not-disclose")
    attach_log_store(hass, make_log_store([entry]))
    body, _result, _outcome = await _call("get_logs", {}, token=token, hass=hass, data=data)
    original = mcp_view._collect_log_entries
    def changed_collection(*args, **kwargs):
        page = original(*args, **kwargs)
        if change == "revoke":
            token.revoked = True
        elif change == "rotate":
            token.token_hash = "different"
        elif change == "kill":
            data.store.get_settings().kill_switch = True
        elif change == "unload":
            data.shutting_down = True
        elif change == "permissions":
            token.settings_version += 1
        else:
            token.cap_log_read = "deny"
        return page
    with patch("custom_components.phoenix_mcp.mcp_view._collect_log_entries", side_effect=changed_collection):
        error, result, outcome = await _call("get_logs", {"detail_ref": body["entries"][0]["detail_ref"]},
                                             token=token, hass=hass, data=data)
    assert outcome == "denied" and result["isError"]
    assert "must-not-disclose" not in json.dumps(error)
