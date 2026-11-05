"""Tests for audit.py."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from homeassistant.util.dt import parse_datetime, utcnow

from custom_components.phoenix_mcp.audit import (
    AUDIT_PAYLOAD_SCAN_CHARS,
    MAX_AUDIT_FIELD_LEN,
    MAX_QUERY_LIMIT,
    AuditEntry,
    AuditLog,
    generate_request_id,
)
from custom_components.phoenix_mcp.const import AUDIT_LOG_MAXLEN
from custom_components.phoenix_mcp.access_key_store import GlobalSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides) -> GlobalSettings:
    defaults = dict(
        disable_all_logging=False,
        log_allowed=True,
        log_denied=True,
        log_rate_limited=True,
        log_entity_names=True,
        log_client_ip=True,
    )
    defaults.update(overrides)
    return GlobalSettings(**defaults)


def _record(
    log: AuditLog,
    *,
    outcome: str = "allowed",
    access_key_id: str = "tok-1",
    access_key_name: str = "myaccesskey",
    method: str = "GET",
    resource: str = "light.kitchen",
    client_ip: str = "192.168.1.1",
    pass_through: bool = False,
    settings: GlobalSettings | None = None,
    timestamp: datetime | None = None,
) -> None:
    log.record(
        request_id=generate_request_id(),
        access_key_id=access_key_id,
        access_key_name=access_key_name,
        method=method,
        resource=resource,
        outcome=outcome,
        client_ip=client_ip,
        pass_through=pass_through,
        settings=settings or _settings(),
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# generate_request_id()
# ---------------------------------------------------------------------------


class TestGenerateRequestId:
    def test_each_id_is_distinct(self):
        assert generate_request_id() != generate_request_id()

    def test_is_valid_uuid(self):
        rid = generate_request_id()
        parsed = uuid.UUID(rid)
        assert str(parsed) == rid

    def test_each_call_returns_unique_value(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


class TestAuditEntry:
    def _entry(self, **kwargs) -> AuditEntry:
        defaults = dict(
            request_id="req-1",
            timestamp=utcnow(),
            access_key_id="tok-1",
            access_key_name="myaccesskey",
            method="GET",
            resource="light.kitchen",
            outcome="allowed",
            client_ip="10.0.0.1",
            pass_through=False,
        )
        defaults.update(kwargs)
        return AuditEntry(**defaults)

    def test_to_dict_contains_all_fields(self):
        entry = self._entry()
        d = entry.to_dict()
        assert "request_id" in d
        assert "timestamp" in d
        assert "access_key_id" in d
        assert "access_key_name" in d
        assert "method" in d
        assert "resource" in d
        assert "outcome" in d
        assert "client_ip" in d
        assert "pass_through" in d

    def test_to_dict_timestamp_is_string(self):
        entry = self._entry()
        assert parse_datetime(entry.to_dict()["timestamp"]) == entry.timestamp

    def test_to_dict_values_match(self):
        ts = utcnow()
        entry = self._entry(
            request_id="req-x",
            access_key_id="tok-x",
            access_key_name="access_key_x",
            method="POST",
            resource="light.living",
            outcome="denied",
            client_ip="1.2.3.4",
            pass_through=True,
            timestamp=ts,
        )
        d = entry.to_dict()
        assert d["request_id"] == "req-x"
        assert d["access_key_id"] == "tok-x"
        assert d["outcome"] == "denied"
        assert d["pass_through"] is True
        assert d["timestamp"] == ts.isoformat()

    def test_pass_through_defaults_to_false(self):
        entry = AuditEntry(
            request_id="r", timestamp=utcnow(), access_key_id="t", access_key_name="n",
            method="GET", resource="x", outcome="allowed", client_ip="1.1.1.1"
        )
        assert entry.pass_through is False

    def test_mesa_advisory_defaults_false_and_omitted(self):
        entry = AuditEntry(
            request_id="r", timestamp=utcnow(), access_key_id="t", access_key_name="n",
            method="GET", resource="x", outcome="allowed", client_ip="1.1.1.1"
        )
        assert entry.mesa_advisory is False
        assert "mesa_advisory" not in entry.to_dict()  # omitted when false

    def test_mesa_advisory_recorded_and_serialized(self):
        log = AuditLog()
        log.record(
            request_id="r", access_key_id="t", access_key_name="n", method="call_service",
            resource="light.kitchen", outcome="allowed", client_ip="1.1.1.1",
            settings=_settings(), mesa_advisory=True,
        )
        entry = log.query(limit=1)[0]
        assert entry.mesa_advisory is True
        assert entry.to_dict()["mesa_advisory"] is True

    def test_preset_and_stale_advisory_default_omitted(self):
        entry = AuditEntry(
            request_id="r", timestamp=utcnow(), access_key_id="t", access_key_name="n",
            method="GET", resource="x", outcome="allowed", client_ip="1.1.1.1"
        )
        assert entry.preset is None and entry.stale_tools_advisory is False
        d = entry.to_dict()
        assert "preset" not in d and "stale_tools_advisory" not in d

    def test_preset_and_stale_advisory_recorded_and_serialized(self):
        log = AuditLog()
        log.record(
            request_id="r", access_key_id="t", access_key_name="n", method="get_state",
            resource="light.kitchen", outcome="allowed", client_ip="1.1.1.1",
            settings=_settings(), preset="Power", stale_tools_advisory=True,
        )
        entry = log.query(limit=1)[0]
        assert entry.preset == "Power"
        d = entry.to_dict()
        assert d["preset"] == "Power" and d["stale_tools_advisory"] is True

    async def test_preset_fields_survive_snapshot_load(self):
        from unittest.mock import AsyncMock, MagicMock

        store = MagicMock()
        store.async_save = AsyncMock()
        log = AuditLog(store=store)
        log.record(
            request_id="r", access_key_id="t", access_key_name="n", method="get_state",
            resource="light.kitchen", outcome="allowed", client_ip="1.1.1.1",
            settings=_settings(), preset="Power", stale_tools_advisory=True,
        )
        await log.async_save()
        snapshot = store.async_save.call_args.args[0]

        store2 = MagicMock()
        store2.async_load = AsyncMock(return_value=snapshot)
        log2 = AuditLog(store=store2)
        await log2.async_load()
        entry = log2.query(limit=1)[0]
        assert entry.preset == "Power" and entry.stale_tools_advisory is True


# ---------------------------------------------------------------------------
# AuditLog - basic recording
# ---------------------------------------------------------------------------


class TestAuditLogRecord:
    def test_record_adds_entry(self):
        log = AuditLog()
        _record(log)
        assert len(log) == 1

    def test_record_coerces_and_caps_scalar_fields(self):
        # method/resource derive from client JSON-RPC data; a non-string (a
        # list-valued params.uri) or oversized value must be coerced to a bounded
        # string so it cannot crash a consumer or bloat the persisted buffer.
        from custom_components.phoenix_mcp.audit import MAX_AUDIT_FIELD_LEN
        log = AuditLog()
        log.record(
            request_id="req-1",
            access_key_id="tok-1",
            access_key_name="myaccesskey",
            method=["resources/read"],          # non-string (poisoned)
            resource=["poison"] * 1000,          # non-string AND oversized
            outcome="denied",
            client_ip="10.0.0.1",
            settings=_settings(),
        )
        entry = list(log._log)[0]
        assert isinstance(entry.method, str)
        assert isinstance(entry.resource, str)
        assert len(entry.resource) <= MAX_AUDIT_FIELD_LEN + len("...[truncated]")
        # And a normal query over the poisoned entry does not raise.
        assert log.query(limit=10)

    def test_record_stores_correct_fields(self):
        log = AuditLog()
        ts = utcnow()
        log.record(
            request_id="req-1",
            access_key_id="tok-1",
            access_key_name="myaccesskey",
            method="GET",
            resource="light.kitchen",
            outcome="allowed",
            client_ip="10.0.0.1",
            pass_through=True,
            settings=_settings(),
            timestamp=ts,
        )
        entry = list(log._log)[0]
        assert entry.request_id == "req-1"
        assert entry.access_key_id == "tok-1"
        assert entry.access_key_name == "myaccesskey"
        assert entry.method == "GET"
        assert entry.resource == "light.kitchen"
        assert entry.outcome == "allowed"
        assert entry.client_ip == "10.0.0.1"
        assert entry.pass_through is True
        assert entry.timestamp == ts

    def test_record_uses_utcnow_when_no_timestamp(self):
        log = AuditLog()
        before = utcnow()
        _record(log)
        after = utcnow()
        entry = list(log._log)[0]
        assert before <= entry.timestamp <= after

    def test_multiple_records_accumulate(self):
        log = AuditLog()
        for _ in range(10):
            _record(log)
        assert len(log) == 10

    def test_all_four_outcomes_recordable(self):
        log = AuditLog()
        for outcome in ("allowed", "denied", "not_found", "rate_limited"):
            _record(log, outcome=outcome)
        assert len(log) == 4


# ---------------------------------------------------------------------------
# AuditLog - logging settings
# ---------------------------------------------------------------------------


class TestLoggingSettings:
    def test_disable_all_logging_blocks_all_outcomes(self):
        log = AuditLog()
        s = _settings(disable_all_logging=True)
        for outcome in ("allowed", "denied", "not_found", "rate_limited"):
            _record(log, outcome=outcome, settings=s)
        assert len(log) == 0

    def test_disable_all_logging_overrides_individual_toggles(self):
        """Even if individual toggles are on, master switch wins."""
        log = AuditLog()
        s = _settings(
            disable_all_logging=True,
            log_allowed=True,
            log_denied=True,
            log_rate_limited=True,
        )
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 0

    def test_log_allowed_false_skips_allowed(self):
        log = AuditLog()
        s = _settings(log_allowed=False)
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 0

    def test_log_allowed_false_still_records_denied(self):
        log = AuditLog()
        s = _settings(log_allowed=False)
        _record(log, outcome="denied", settings=s)
        assert len(log) == 1

    def test_log_denied_false_skips_denied(self):
        log = AuditLog()
        s = _settings(log_denied=False)
        _record(log, outcome="denied", settings=s)
        assert len(log) == 0

    def test_log_denied_false_skips_not_found(self):
        """not_found is controlled by the same log_denied toggle."""
        log = AuditLog()
        s = _settings(log_denied=False)
        _record(log, outcome="not_found", settings=s)
        assert len(log) == 0

    def test_log_denied_false_still_records_allowed(self):
        log = AuditLog()
        s = _settings(log_denied=False)
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 1

    def test_approval_lifecycle_bypasses_allowed_and_denied_filters(self):
        log = AuditLog()
        s = _settings(log_allowed=False, log_denied=False)
        _record(log, method="approval/approved", outcome="allowed", settings=s)
        _record(log, method="approval/failed", outcome="denied", settings=s)
        assert [entry.method for entry in log.query(limit=10)] == [
            "approval/failed",
            "approval/approved",
        ]

    def test_master_logging_switch_still_suppresses_approval_lifecycle(self):
        log = AuditLog()
        s = _settings(disable_all_logging=True)
        _record(log, method="approval/approved", outcome="allowed", settings=s)
        assert len(log) == 0

    def test_log_rate_limited_false_skips_rate_limited(self):
        log = AuditLog()
        s = _settings(log_rate_limited=False)
        _record(log, outcome="rate_limited", settings=s)
        assert len(log) == 0

    def test_log_rate_limited_false_still_records_allowed(self):
        log = AuditLog()
        s = _settings(log_rate_limited=False)
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 1

    def test_individual_toggles_independent(self):
        """Disabling one toggle does not affect the others."""
        log = AuditLog()
        s = _settings(log_allowed=False, log_denied=True, log_rate_limited=True)
        _record(log, outcome="allowed", settings=s)
        _record(log, outcome="denied", settings=s)
        _record(log, outcome="rate_limited", settings=s)
        assert len(log) == 2


# ---------------------------------------------------------------------------
# AuditLog - redaction settings
# ---------------------------------------------------------------------------


class TestRedactionSettings:
    def test_log_entity_names_false_redacts_resource(self):
        log = AuditLog()
        s = _settings(log_entity_names=False)
        _record(log, resource="light.kitchen", settings=s)
        entry = list(log._log)[0]
        assert entry.resource == "[redacted]"

    def test_log_entity_names_true_preserves_resource(self):
        log = AuditLog()
        s = _settings(log_entity_names=True)
        _record(log, resource="light.kitchen", settings=s)
        entry = list(log._log)[0]
        assert entry.resource == "light.kitchen"

    def test_log_client_ip_false_redacts_ip(self):
        log = AuditLog()
        s = _settings(log_client_ip=False)
        _record(log, client_ip="192.168.1.100", settings=s)
        entry = list(log._log)[0]
        assert entry.client_ip == "[redacted]"

    def test_log_client_ip_true_preserves_ip(self):
        log = AuditLog()
        s = _settings(log_client_ip=True)
        _record(log, client_ip="192.168.1.100", settings=s)
        entry = list(log._log)[0]
        assert entry.client_ip == "192.168.1.100"

    def test_both_redacted_simultaneously(self):
        log = AuditLog()
        s = _settings(log_entity_names=False, log_client_ip=False)
        _record(log, resource="sensor.temp", client_ip="10.0.0.1", settings=s)
        entry = list(log._log)[0]
        assert entry.resource == "[redacted]"
        assert entry.client_ip == "[redacted]"

    def test_log_client_ip_false_keeps_phoenixs_own_surfaces(self):
        """They name no one, and without them every internal row looks like a network one."""
        log = AuditLog()
        s = _settings(log_client_ip=False)
        for source in ("agentcli", "voice", "assist", "ai_task", "home_companion"):
            _record(log, client_ip=source, settings=s)
        assert [e.client_ip for e in log._log] == ["agentcli", "voice", "assist", "ai_task", "home_companion"]

    def test_log_entity_names_false_hides_entity_ids_in_the_payload(self):
        """Tool arguments keep only their names, and MESA rows carry entity IDs too."""
        log = AuditLog()
        payload = {"arguments": {"entity_id": "lock.front_door", "target": {"entity_id": ["light.a"]},
                                 "query": "person.alice", "template": "{{ states('sensor.door') }}",
                                 "area_id": "bedroom", "name": "Front door", "lock.back": 1},
                   "entities": [{"id": "x"}], "service": "light.turn_on"}
        log.record(request_id="r", access_key_id="k", access_key_name="n", method="tools/call",
                   resource="lock.front_door", outcome="allowed", client_ip="", settings=_settings(log_entity_names=False),
                   payload=payload)
        stored = json.loads(list(log._log)[0].payload)
        for private in ("lock.front_door", "light.a", "person.alice", "sensor.door", "bedroom", "Front door", "lock.back"):
            assert private not in json.dumps(stored)
        assert stored["arguments"] == ["area_id", "entity_id", "name", "query", "target", "template"]
        assert stored["entities"] == "[redacted]"
        # A service name looks like an entity ID but is not one, and stays.
        assert stored["service"] == "light.turn_on"
        log.record(request_id="r", access_key_id="k", access_key_name="n", method="tools/call",
                   resource="x", outcome="allowed", client_ip="", settings=_settings(), payload=payload)
        assert "lock.front_door" in list(log._log)[1].payload

    def test_redaction_applied_before_storage(self):
        """Original values must not be recoverable from the log after redaction."""
        log = AuditLog()
        s = _settings(log_entity_names=False)
        _record(log, resource="lock.front_door", settings=s)
        raw_entries = list(log._log)
        assert all(e.resource != "lock.front_door" for e in raw_entries)


# ---------------------------------------------------------------------------
# AuditLog - circular buffer behaviour
# ---------------------------------------------------------------------------


class TestCircularBuffer:
    def test_maxlen_is_audit_log_maxlen(self):
        log = AuditLog()
        assert log._log.maxlen == AUDIT_LOG_MAXLEN

    def test_custom_maxlen_respected(self):
        log = AuditLog(maxlen=5)
        assert log._log.maxlen == 5

    def test_overflow_evicts_oldest_entry(self):
        log = AuditLog(maxlen=3)
        for i in range(5):
            log.record(
                request_id=f"req-{i}",
                access_key_id="tok",
                access_key_name="t",
                method="GET",
                resource=f"entity.{i}",
                outcome="allowed",
                client_ip="1.1.1.1",
                settings=_settings(),
            )
        assert len(log) == 3
        entries = list(log._log)
        resources = [e.resource for e in entries]
        assert "entity.0" not in resources
        assert "entity.1" not in resources
        assert "entity.2" in resources
        assert "entity.3" in resources
        assert "entity.4" in resources

    def test_len_does_not_exceed_maxlen(self):
        log = AuditLog(maxlen=10)
        for _ in range(25):
            _record(log)
        assert len(log) == 10

    def test_clear_empties_log(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        log.clear()
        assert len(log) == 0

    def test_clear_allows_new_records_after(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        log.clear()
        _record(log)
        assert len(log) == 1


# ---------------------------------------------------------------------------
# AuditLog - query()
# ---------------------------------------------------------------------------


class TestQuery:
    def _populate(self, log: AuditLog) -> None:
        entries = [
            {"access_key_id": "tok-a", "outcome": "allowed",      "client_ip": "10.0.0.1"},
            {"access_key_id": "tok-a", "outcome": "denied",       "client_ip": "10.0.0.1"},
            {"access_key_id": "tok-b", "outcome": "allowed",      "client_ip": "10.0.0.2"},
            {"access_key_id": "tok-b", "outcome": "rate_limited", "client_ip": "10.0.0.2"},
            {"access_key_id": "tok-a", "outcome": "not_found",    "client_ip": "10.0.0.3"},
        ]
        for kwargs in entries:
            _record(log, **kwargs)

    def test_no_filters_returns_all_newest_first(self):
        log = AuditLog()
        self._populate(log)
        results = log.query()
        assert len(results) == 5
        # Newest first: last recorded entry is first in results
        assert results[0].outcome == "not_found"
        assert results[-1].outcome == "allowed"

    def test_filter_by_token_id(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(access_key_id="tok-a")
        assert len(results) == 3
        assert all(e.access_key_id == "tok-a" for e in results)

    def test_filter_by_outcome(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(outcome="allowed")
        assert len(results) == 2
        assert all(e.outcome == "allowed" for e in results)

    def test_filter_by_client_ip(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(client_ip="10.0.0.2")
        assert len(results) == 2
        assert all(e.client_ip == "10.0.0.2" for e in results)

    def test_filter_by_method_is_substring_case_insensitive(self):
        log = AuditLog()
        _record(log, method="tools/call")
        _record(log, method="resources/list")
        results = log.query(method="TOOLS")
        assert len(results) == 1
        assert results[0].method == "tools/call"

    def test_filter_by_resource_is_substring_case_insensitive(self):
        log = AuditLog()
        _record(log, resource="approval:create_automation:appr_1")
        _record(log, resource="light.kitchen")
        results = log.query(resource="APPR_1")
        assert len(results) == 1
        assert results[0].resource == "approval:create_automation:appr_1"

    def test_filter_by_since_excludes_older_entries(self):
        log = AuditLog()
        _record(log, resource="old", timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc))
        _record(log, resource="new", timestamp=datetime(2030, 1, 1, tzinfo=timezone.utc))
        results = log.query(since=datetime(2025, 1, 1, tzinfo=timezone.utc))
        assert len(results) == 1
        assert results[0].resource == "new"

    def test_method_resource_filter_survives_poisoned_non_string_entry(self):
        """A recorded non-string method/resource does not crash a filtered query.

        A malformed JSON-RPC request can supply one; filtering must not fail on .lower().
        """
        log = AuditLog()
        _record(log, method=["bad"], resource=["also-bad"])
        _record(log, method="tools/call", resource="light.kitchen")
        # No crash, and the good entry still matches its filters.
        assert len(log.query(method="tools")) == 1
        assert len(log.query(resource="kitchen")) == 1
        assert log.count(method="tools") == 1

    def test_pending_approval_is_recordable_and_queryable(self):
        """pending_approval is a real emitted outcome (a tool gated for confirm),
        so it must be queryable rather than rejected as an unknown outcome."""
        log = AuditLog()
        _record(log, outcome="pending_approval")
        results = log.query(outcome="pending_approval")
        assert results is not None
        assert len(results) == 1
        assert results[0].outcome == "pending_approval"

    def test_combined_filters(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(access_key_id="tok-a", outcome="allowed")
        assert len(results) == 1
        assert results[0].access_key_id == "tok-a"
        assert results[0].outcome == "allowed"

    def test_filter_with_no_matches_returns_empty(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(access_key_id="tok-z")
        assert results == []

    def test_pagination_limit(self):
        log = AuditLog()
        for _ in range(10):
            _record(log)
        results = log.query(limit=3)
        assert len(results) == 3

    def test_pagination_offset(self):
        log = AuditLog()
        for i in range(5):
            _record(log, resource=f"light.{i}")
        all_results = log.query()
        offset_results = log.query(offset=2)
        assert offset_results == all_results[2:]

    def test_limit_capped_at_max_query_limit(self):
        log = AuditLog(maxlen=10000)
        for _ in range(600):
            _record(log)
        results = log.query(limit=600)
        assert len(results) == MAX_QUERY_LIMIT

    def test_empty_log_returns_empty_list(self):
        log = AuditLog()
        assert log.query() == []

    def test_query_does_not_modify_log(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        log.query(access_key_id="tok-1", outcome="allowed")
        assert len(log) == 5

    def test_results_are_newest_first(self):
        log = AuditLog()
        t1 = datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 10, 11, 0, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        _record(log, timestamp=t1)
        _record(log, timestamp=t2)
        _record(log, timestamp=t3)
        results = log.query()
        assert results[0].timestamp == t3
        assert results[1].timestamp == t2
        assert results[2].timestamp == t1

    def test_pagination_offset_zero_same_as_no_offset(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        assert log.query(offset=0) == log.query()


class TestCount:
    """count() agrees with query()'s filters, ignoring limit/offset.

    The admin panel uses it to decide whether a "Load more" page exists.
    """

    def test_matches_filtered_length_ignoring_pagination(self):
        log = AuditLog()
        for _ in range(7):
            _record(log, access_key_id="tok-a")
        for _ in range(3):
            _record(log, access_key_id="tok-b")
        assert log.count(access_key_id="tok-a") == 7
        # A tiny limit/offset on query() must not affect count().
        assert len(log.query(access_key_id="tok-a", limit=2, offset=5)) == 2
        assert log.count(access_key_id="tok-a") == 7

    def test_unknown_outcome_returns_none_like_query(self):
        log = AuditLog()
        assert log.count(outcome="not-a-real-outcome") is None

    def test_empty_log_returns_zero(self):
        log = AuditLog()
        assert log.count() == 0


class TestPayloadRedaction:
    """Recorded payloads must not persist secrets verbatim (redact_structure)."""

    def test_sensitive_key_value_redacted(self):
        log = AuditLog()
        log.record(
            request_id=generate_request_id(), access_key_id="t", access_key_name="n",
            method="create_helper", resource="helper", outcome="allowed",
            client_ip="1.2.3.4", settings=_settings(),
            payload={"name": "create_helper", "arguments": {"api_key": "supersecret123"}},
        )
        stored = log.query()[0].payload
        assert "supersecret123" not in stored
        assert "<redacted>" in stored

    def test_embedded_secret_line_scrubbed(self):
        log = AuditLog()
        log.record(
            request_id=generate_request_id(), access_key_id="t", access_key_name="n",
            method="edit_blueprint", resource="blueprint", outcome="allowed",
            client_ip="1.2.3.4", settings=_settings(),
            payload={"name": "edit_blueprint", "arguments": {"content": "password: hunter2"}},
        )
        stored = log.query()[0].payload
        assert "hunter2" not in stored

    def test_innocuous_payload_preserved(self):
        log = AuditLog()
        log.record(
            request_id=generate_request_id(), access_key_id="t", access_key_name="n",
            method="HassTurnOn", resource="light", outcome="allowed",
            client_ip="1.2.3.4", settings=_settings(),
            payload={"name": "HassTurnOn", "arguments": {"entity_id": "light.kitchen", "brightness": 50}},
        )
        stored = log.query()[0].payload
        assert "light.kitchen" in stored
        assert "50" in stored


class TestDeviceCodeRedaction:
    """Audit payloads hide device codes as the Conversation Log does.

    The general secret redaction kept a disarm `code` and a PIN, so they were
    saved in the audit log and returned by the admin audit route.
    """

    @staticmethod
    def _stored(name: str, arguments: dict) -> str:
        log = AuditLog()
        log.record(
            request_id=generate_request_id(), access_key_id="t", access_key_name="n",
            method=name, resource="alarm_control_panel.home", outcome="allowed",
            client_ip="1.2.3.4", settings=_settings(),
            payload={"name": name, "arguments": arguments},
        )
        return log.query()[0].payload

    def test_service_call_code_is_hidden_and_the_rest_kept(self):
        stored = self._stored("call_service", {
            "domain": "alarm_control_panel", "service": "alarm_disarm",
            "target": {"entity_id": "alarm_control_panel.home"}, "service_data": {"code": "4321"},
        })
        assert "4321" not in stored
        assert "alarm_disarm" in stored and "alarm_control_panel.home" in stored

    def test_pin_psk_and_encryption_key_are_hidden_anywhere(self):
        stored = self._stored("set_esphome_config", {"pin": "2468", "wifi": {"psk": "wifi-secret"}, "encryption_key": "abcdef"})
        assert "2468" not in stored and "wifi-secret" not in stored and "abcdef" not in stored

    def test_a_spoken_code_in_free_text_is_hidden(self):
        stored = self._stored("home_request", {"request": "disarm the alarm, the code is 4321"})
        assert "4321" not in stored
        assert "disarm the alarm" in stored

    def test_an_error_code_outside_a_device_call_stays_readable(self):
        stored = self._stored("report_issue", {"code": "bad_tool_call_retry"})
        assert "bad_tool_call_retry" in stored

    def test_a_value_that_is_not_json_keeps_its_text(self):
        when = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        stored = self._stored("mesa:enforcement_decision", {"at": when})
        assert "2026-11-01" in stored


async def test_saved_payloads_with_device_codes_are_scrubbed_at_load_and_saved(hass):
    from homeassistant.helpers.storage import Store

    from custom_components.phoenix_mcp.const import AUDIT_STORAGE_VERSION

    backing = Store(hass, 1, "phoenix_test_scrub_audit")
    legacy = '{"name": "call_service", "arguments": {"service_data": {"code": "4321"}}}'
    clipped = '{"name": "call_service", "arguments": {"service_data": {"code": "8765", "x": "' + "y" * 2100
    clipped = clipped[:2048] + "...[truncated]"
    plain = '{"name": "HassTurnOn", "arguments": {"entity_id": "light.kitchen"}}'
    rows = [
        {"request_id": rid, "timestamp": "2026-11-01T00:00:00+00:00", "access_key_id": "t", "access_key_name": "n",
         "method": "call_service", "resource": "r", "outcome": "allowed", "client_ip": "", "payload": payload}
        for rid, payload in (("legacy", legacy), ("clipped", clipped), ("plain", plain))
    ]
    await backing.async_save({"version": AUDIT_STORAGE_VERSION, "entries": rows})

    loaded = AuditLog(store=backing)
    await loaded.async_load()
    by_id = {e.request_id: e.payload for e in loaded.query(limit=10)}
    assert "4321" not in by_id["legacy"]
    assert "8765" not in by_id["clipped"]
    assert by_id["plain"] == plain

    on_disk = await Store(hass, 1, "phoenix_test_scrub_audit").async_load()
    assert "4321" not in str(on_disk) and "8765" not in str(on_disk)


async def test_restore_origin_survives_persistence_without_relaxing_payload_scrubbing(hass):
    from homeassistant.helpers.storage import Store

    backing = Store(hass, 1, "phoenix_test_restore_audit")
    audit = AuditLog(store=backing)
    audit.record(
        request_id="restore", access_key_id="admin", access_key_name="admin:owner",
        method="version/restore", resource="version:fixture", outcome="allowed",
        client_ip="", settings=_settings(), originating_access_key_id="source-identity",
        payload={"originating_token_id": "private-payload", "api_key": "private-key"},
    )
    await audit.async_save()
    loaded = AuditLog(store=backing)
    await loaded.async_load()
    entry = loaded.query(limit=1)[0].to_dict()
    assert entry["access_key_id"] == "admin"
    assert entry["ha_user_id"] == "owner"
    assert entry["originating_access_key_id"] == "source-identity"
    assert "private-payload" not in entry["payload"]
    assert "private-key" not in entry["payload"]


class TestRequestIdFilter:
    """Needs Attention links to one request, so the filter is exact."""

    def test_returns_only_that_request_and_counts_it(self):
        log = AuditLog()
        for rid in ("req-a", "req-b", "req-a"):
            log.record(request_id=rid, access_key_id="t1", access_key_name="a", method="POST",
                       resource="tool:add_dashboard_card", outcome="allowed", client_ip="",
                       settings=_settings())
        entries = log.query(request_id="req-a")
        assert [e.request_id for e in entries] == ["req-a", "req-a"]
        assert log.count(request_id="req-a") == 2
        assert log.query(request_id="req-") == []
        assert log.count(request_id="nope") == 0


class TestPayloadScanLimit:
    """The sanitizer runs on the event loop for every audited call, so its input is bounded."""

    def _stored(self, payload) -> str:
        log = AuditLog()
        log.record(request_id="r", access_key_id="k", access_key_name="n", method="m", resource="x",
                   outcome="allowed", client_ip="", settings=_settings(), payload=payload)
        return list(log._log)[0].payload

    def test_only_a_bounded_part_of_a_payload_is_sanitized(self):
        seen = []
        import custom_components.phoenix_mcp.conversation_log as conversation_log
        real = conversation_log.sanitized

        def measure(value, **kwargs):
            seen.append(len(json.dumps(value)))
            return real(value, **kwargs)

        with patch("custom_components.phoenix_mcp.audit.sanitized", measure):
            self._stored({"arguments": {"a": "x" * 500_000, "b": ["y"] * 50_000}})
        assert seen and max(seen) < 2 * AUDIT_PAYLOAD_SCAN_CHARS

    def test_a_short_payload_is_not_clipped_and_keeps_every_field(self):
        payload = {"name": "t", "arguments": {"a": 1, "b": [True, None, 1.5], "c": {"d": "e"}}}
        assert json.loads(self._stored(payload)) == payload

    def test_a_secret_stays_hidden_in_what_the_row_keeps(self):
        stored = self._stored({"arguments": {"text": "password: hunter2\n" + "x" * 500_000}})
        assert "hunter2" not in stored and "truncated" in stored

    def test_a_code_stays_hidden_when_what_marked_it_as_a_service_call_is_cut(self):
        """The keys and words that make `code` a credential can sit past the limit."""
        stored = self._stored({"code": "ab12", "pad": "x" * 500_000, "service": "lock.unlock"})
        assert "ab12" not in stored
        stored = self._stored({"text": "code: ab12\n" + "x" * 500_000 + " alarm_control_panel"})
        assert "ab12" not in stored

    def test_a_payload_nested_past_the_sanitizer_limit_is_not_kept_raw(self):
        value: object = "person.alice"
        for _ in range(5_000):
            value = [value]
        log = AuditLog()
        for private in (True, False):
            log.record(request_id="r", access_key_id="k", access_key_name="n", method="m", resource="x",
                       outcome="allowed", client_ip="", settings=_settings(log_entity_names=private),
                       payload={"arguments": {"deep": value}, "other": value})
        assert all("person.alice" not in (row.payload or "") for row in log._log)

    def test_every_client_derived_field_is_storable_and_bounded(self):
        log = AuditLog()
        log.record(request_id="\ud800", access_key_id="\ud800", access_key_name="\ud800", method="\ud800" * 1000,
                   resource="\udfff", outcome="denied", client_ip="\ud800" * 1000, settings=_settings(),
                   preset="\ud800", ha_user_id="\ud800", originating_access_key_id="\ud800",
                   payload={"\ud800": "\ud800"})
        serialized = json.dumps([row.to_dict() for row in log._log], ensure_ascii=False)
        serialized.encode("utf-8")
        row = list(log._log)[0]
        assert row.method.startswith("\\ud800") and len(row.client_ip) < 2 * MAX_AUDIT_FIELD_LEN * len("\\ud800")


async def test_loading_scrubs_payloads_off_the_event_loop():
    import threading
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.phoenix_mcp.audit import AuditLog
    from custom_components.phoenix_mcp.const import AUDIT_STORAGE_VERSION

    store = MagicMock(async_load=AsyncMock(return_value={"version": AUDIT_STORAGE_VERSION, "entries": [{
        "request_id": "r", "timestamp": "2026-01-01T00:00:00+00:00", "access_key_id": "k",
        "access_key_name": "n", "method": "m", "resource": "x", "outcome": "allowed", "client_ip": "",
    }]}), async_save=AsyncMock())
    log = AuditLog(store=store, maxlen=10)
    threads = []
    original = log._parse_rows

    def tracked(rows):
        threads.append(threading.current_thread())
        return original(rows)

    log._parse_rows = tracked
    await log.async_load()
    assert threads and threads[0] is not threading.current_thread()
    assert len(log) == 1
