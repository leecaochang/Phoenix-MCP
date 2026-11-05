"""Tests for the diagnostics/traces MCP tools and paginated Recorder history."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord


def _access_key(permissions: PermissionTree | None = None, **caps) -> AccessKeyRecord:
    tree = permissions or PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **caps,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, access_key, hass):
    return await _call_tool(name, args, access_key, hass, MagicMock())


# --- get_history ---

class _FakeState:
    def __init__(self, state, when):
        self._s, self._w = state, when

    def as_dict(self):
        return {
            "entity_id": "light.kitchen", "state": self._s,
            "last_changed": self._w, "last_updated": self._w, "attributes": {"x": 1},
        }


class TestGetHistoryTransitions:
    def _patched(self, states):
        inst = MagicMock()
        inst.keep_days = 10
        inst.async_add_executor_job = AsyncMock(return_value={"light.kitchen": states})
        self.executor = inst.async_add_executor_job
        return patch("homeassistant.components.recorder.get_instance", return_value=inst)

    async def test_state_changes_are_bounded_and_cursor_paginated(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        now = utcnow()
        states = [
            _FakeState("on", now - timedelta(hours=3)),
            _FakeState("off", now - timedelta(hours=1)),
            _FakeState("on", now),
        ]
        with self._patched(states):
            content, outcome, _ = await _call(
                "get_history",
                {"entity_id": "light.kitchen", "start_time": "24h", "limit": 2},
                _access_key(), hass,
            )
        assert outcome == "allowed"
        body = _json(content)
        assert body["mode"] == "state_changes"
        assert [h["state"] for h in body["history"]] == ["on", "off"]
        assert body["count"] == 2
        assert body["has_more"] is True
        assert body["next_cursor"] == (now - timedelta(hours=1)).isoformat()
        assert body["effective_limit"] == 2
        assert body["retention"] == {"kind": "short_term", "configured_days": 10}
        assert body["data_range"] == {
            "start": (now - timedelta(hours=3)).isoformat(),
            "end": (now - timedelta(hours=1)).isoformat(),
        }
        job = self.executor.await_args.args[0]
        assert job.func.__name__ == "state_changes_during_period"
        assert job.args[4] is True
        assert job.args[6] == 3
        assert job.args[7] is False

    async def test_significant_states_returns_scrubbed_full_dicts(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        now = utcnow()
        states = [_FakeState("on", now), _FakeState("off", now)]
        with self._patched(states):
            content, _, _ = await _call(
                "get_history",
                {"entity_id": "light.kitchen", "start_time": "24h", "mode": "significant_states"},
                _access_key(), hass,
            )
        body = _json(content)
        assert body["mode"] == "significant_states"
        assert len(body["history"]) == 2
        assert "attributes" in body["history"][0]

    async def test_significant_states_refuses_more_than_seven_days_before_query(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        with patch("homeassistant.components.recorder.get_instance") as get_instance:
            content, outcome, _ = await _call(
                "get_history",
                {"entity_id": "light.kitchen", "start_time": "8d", "mode": "significant_states"},
                _access_key(), hass,
            )
        assert outcome == "invalid_request"
        assert "at most 7 days" in content["content"][0]["text"]
        get_instance.assert_not_called()

    async def test_legacy_modes_are_breaking_errors(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        content, outcome, _ = await _call(
            "get_history", {"entity_id": "light.kitchen", "mode": "raw"}, _access_key(), hass,
        )
        assert outcome == "invalid_request"
        assert "Invalid mode" in content["content"][0]["text"]

    async def test_inaccessible_entity_not_found(self, hass):
        content, outcome, _ = await _call(
            "get_history", {"entity_id": "sensor.secret", "start_time": "24h"}, _access_key(), hass)
        assert outcome in ("not_found", "denied")


# --- get_automation_traces ---

class _FakeTrace:
    def __init__(self, run_id="run1", start="2026-01-01T00:00:00+00:00", not_triggered=False, error=None):
        self.run_id = run_id
        self._start = start
        self.not_triggered = not_triggered
        self._error = error

    def as_short_dict(self):
        short = {
            "run_id": self.run_id, "state": "stopped", "script_execution": "error" if self._error else "finished",
            "last_step": "action/0", "timestamp": {"start": self._start},
        }
        if self.not_triggered:
            short["not_triggered"] = True
        if self._error:
            short["error"] = self._error
        return short

    def as_dict(self):
        return {**self.as_short_dict(), "trace": {"trigger/0": [{"path": "trigger/0"}]}}


def _trace_buckets(runs=None, not_triggered=None):
    """A real TraceBuckets, the shape HA 2026.7+ stores per automation.

    Built from the installed HA rather than hand-rolled, so the fixture cannot
    drift from the shape HA really stores.
    """
    from homeassistant.components.trace.models import TraceBuckets
    from homeassistant.util.limited_size_dict import LimitedSizeDict

    run_bucket = LimitedSizeDict(size_limit=10)
    run_bucket.update({t.run_id: t for t in (runs or [])})
    nt_bucket = LimitedSizeDict(size_limit=10)
    nt_bucket.update({t.run_id: t for t in (not_triggered or [])})
    return TraceBuckets(runs=run_bucket, not_triggered=nt_bucket)


@pytest.fixture
def auto_env(hass: HomeAssistant):
    from homeassistant.components.trace.const import DATA_TRACE
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    auto = ent_reg.async_get_or_create(
        "automation", "test_integration", "auto_uid_1",
        config_entry=entry, suggested_object_id="morning",
    )
    hass.states.async_set(auto.entity_id, "on", {})
    hass.data[DATA_TRACE] = {"automation.auto_uid_1": _trace_buckets(runs=[_FakeTrace()])}
    return {"entity_id": auto.entity_id}


def _auto_access_key(cap_traces="allow"):
    tree = PermissionTree(domains={"automation": PermissionNode(state="GREEN")})
    return _access_key(permissions=tree, cap_traces=cap_traces)


class TestGetAutomationTraces:
    async def test_deny_without_cap(self, hass, auto_env):
        _, outcome, _ = await _call("get_automation_traces", {"automation_id": "automation.morning"}, _auto_access_key("deny"), hass)
        assert outcome == "denied"

    async def test_list_traces(self, hass, auto_env):
        content, outcome, _ = await _call("get_automation_traces", {"automation_id": "automation.morning"}, _auto_access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["count"] == 1
        assert body["traces"][0]["run_id"] == "run1"

    async def test_specific_run_summary(self, hass, auto_env):
        content, _, _ = await _call(
            "get_automation_traces", {"automation_id": "automation.morning", "run_id": "run1", "summary": True}, _auto_access_key(), hass)
        body = _json(content)
        assert body["script_execution"] == "finished"
        assert "trace" not in body  # summary drops the heavy step tree

    async def test_unknown_automation_not_found(self, hass, auto_env):
        _, outcome, _ = await _call("get_automation_traces", {"automation_id": "automation.ghost"}, _auto_access_key(), hass)
        assert outcome == "not_found"

    async def test_list_traces_carries_no_warnings_when_trace_loaded(self, hass, auto_env):
        content, _, _ = await _call(
            "get_automation_traces", {"automation_id": "automation.morning"}, _auto_access_key(), hass)
        assert "warnings" not in _json(content)

    async def test_unknown_run_id_is_not_found_not_an_error(self, hass, auto_env):
        # A TraceBuckets lacks the legacy dict API; an unknown run id must still
        # answer not_found rather than surface as an internal error.
        _, outcome, _ = await _call(
            "get_automation_traces",
            {"automation_id": "automation.morning", "run_id": "no-such-run"},
            _auto_access_key(), hass)
        assert outcome == "not_found"


class TestTraceOverview:
    """Without automation_id, one call says which automations and scripts ran and which failed."""

    def _setup(self, hass, *, many=0):
        from homeassistant.components.trace.const import DATA_TRACE
        entry = MockConfigEntry(domain="test_integration", entry_id="e1")
        entry.add_to_hass(hass)
        registry = er.async_get(hass)
        for domain, uid, name in (("automation", "uid_ok", "fine"), ("automation", "uid_bad", "broken"),
                                  ("automation", "uid_hidden", "hidden"), ("script", "uid_script", "porch"),
                                  *(("automation", f"uid_{i}", f"bulk_{i:02d}") for i in range(many))):
            registry.async_get_or_create(domain, "test_integration", uid, config_entry=entry, suggested_object_id=name)
        hass.data[DATA_TRACE] = {
            "automation.uid_ok": _trace_buckets(
                runs=[_FakeTrace("a1", "2026-01-03T00:00:00+00:00"), _FakeTrace("a2", "2026-01-01T00:00:00+00:00")],
                not_triggered=[_FakeTrace("n1", "2026-01-04T00:00:00+00:00", not_triggered=True)]),
            "automation.uid_bad": _trace_buckets(runs=[
                _FakeTrace("b1", "2026-01-02T00:00:00+00:00", error="Action light.flash not found"),
                _FakeTrace("b2", "2026-01-01T00:00:00+00:00")]),
            "automation.uid_hidden": _trace_buckets(runs=[_FakeTrace("h1", error="secret failure")]),
            "script.uid_script": _trace_buckets(runs=[_FakeTrace("s1", "2026-01-05T00:00:00+00:00")]),
            **{f"automation.uid_{i}": _trace_buckets(runs=[_FakeTrace(f"r{i}", error="x" * 200)]) for i in range(many)},
        }

    def _key(self):
        return _access_key(permissions=PermissionTree(
            domains={"automation": PermissionNode(state="GREEN"), "script": PermissionNode(state="GREEN")},
            entities={"automation.hidden": PermissionNode(state="RED")}), cap_traces="allow")

    async def test_every_readable_automation_and_script_in_one_call_errors_first(self, hass):
        self._setup(hass)
        content, outcome, _ = await _call("get_automation_traces", {}, self._key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert [row["entity_id"] for row in body["items"]] == ["automation.broken", "script.porch", "automation.fine"]
        assert body["count"] == 3 and body["with_errors"] == 1
        broken, _, fine = body["items"]
        assert broken["outcomes"] == {"error": 1, "finished": 1}
        # Error text is scoped like the single-trace view: an entity this key cannot read is redacted.
        assert broken["errors"] == [{"run_id": "b1", "start": "2026-01-02T00:00:00+00:00", "execution": "error",
                                     "last_step": "action/0", "error": "Action <redacted-entity> not found"}]
        assert fine["runs"] == 2 and fine["not_triggered"] == 1 and fine["last"]["run_id"] == "a1"
        assert "errors" not in fine and "secret failure" not in content["content"][0]["text"]

    async def test_the_overview_fits_one_result_and_names_the_rest(self, hass):
        from custom_components.phoenix_mcp import tool_common
        from custom_components.phoenix_mcp.conversation_log import json_size
        self._setup(hass, many=30)
        with patch.object(tool_common, "TOOL_RESULT_MAX_BYTES", 4000):
            body = _json((await _call("get_automation_traces", {}, self._key(), hass))[0])
        assert body["count"] < 33 and len(body["not_listed"]) == 33 - body["count"]
        assert json_size(json.dumps(body)) <= 4000
        assert any("not_listed" in warning for warning in body["warnings"])

    async def test_runs_saved_before_a_restart_are_read(self, hass):
        # Home Assistant restores saved traces only when they are first listed; reading
        # its trace data directly would report a restarted instance as having no runs.
        from homeassistant.components.trace.const import DATA_TRACE, DATA_TRACE_STORE
        self._setup(hass)
        hass.data[DATA_TRACE] = {}
        saved = {"extended_dict": {"domain": "automation", "item_id": "uid_bad", "run_id": "old1", "last_step": "action/0",
                                   "context": {"id": "c1", "parent_id": None, "user_id": None},
                                   "trace": {"action/0": [{"path": "action/0", "error": "Pump jammed"}]}},
                 "short_dict": {"run_id": "old1", "state": "stopped", "script_execution": "aborted",
                                "last_step": "action/0", "timestamp": {"start": "2026-01-02T00:00:00+00:00"}}}
        hass.data[DATA_TRACE_STORE] = MagicMock(async_load=AsyncMock(return_value={"automation.uid_bad": [saved]}))
        overview = _json((await _call("get_automation_traces", {}, self._key(), hass))[0])
        assert overview["items"][0]["errors"][0] == {"run_id": "old1", "start": "2026-01-02T00:00:00+00:00",
                                                     "execution": "aborted", "last_step": "action/0", "error": "Pump jammed"}
        single = _json((await _call("get_automation_traces", {"automation_id": "automation.broken"}, self._key(), hass))[0])
        assert [trace["run_id"] for trace in single["traces"]] == ["old1"]

    def test_an_aborted_run_without_recorded_steps_has_no_error(self):
        from custom_components.phoenix_mcp.tools.authoring import _run_error

        class _Aborted:
            def as_extended_dict(self):
                return {"last_step": None, "trace": {}}

        assert _run_error({"script_execution": "aborted"}, _Aborted()) is None
        assert _run_error({"script_execution": "aborted", "error": "boom"}, _Aborted()) == "boom"

    async def test_a_run_id_needs_its_automation(self, hass):
        self._setup(hass)
        content, outcome, _ = await _call("get_automation_traces", {"run_id": "b1"}, self._key(), hass)
        assert outcome == "invalid_request" and "needs automation_id" in content["content"][0]["text"]

    async def test_a_real_failed_run_is_reported_with_its_error(self, hass):
        from homeassistant.setup import async_setup_component
        # A stop with an error and a false condition both end a run as aborted; only the
        # first is a failure, and Home Assistant records its error on the last step.
        assert await async_setup_component(hass, "automation", {"automation": [{
            "id": "flash", "alias": "Flash",
            "triggers": [{"trigger": "state", "entity_id": "binary_sensor.porch"}],
            "actions": [{"stop": "Pump jammed", "error": True}],
        }, {
            "id": "quiet", "alias": "Quiet",
            "triggers": [{"trigger": "state", "entity_id": "binary_sensor.porch"}],
            "actions": [{"condition": "state", "entity_id": "binary_sensor.porch", "state": "off"}, {"delay": 0}],
        }]})
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.porch", "on")
        await hass.async_block_till_done()
        key = _access_key(permissions=PermissionTree(domains={
            "automation": PermissionNode(state="GREEN"), "binary_sensor": PermissionNode(state="GREEN")}),
            cap_traces="allow")
        body = _json((await _call("get_automation_traces", {}, key, hass))[0])
        rows = {row["entity_id"]: row for row in body["items"]}
        assert body["with_errors"] == 1 and list(rows) == ["automation.flash", "automation.quiet"]
        assert rows["automation.flash"]["outcomes"] == {"aborted": 1}
        assert rows["automation.flash"]["errors"][0]["error"] == "Pump jammed"
        assert rows["automation.quiet"]["outcomes"] == {"aborted": 1} and "errors" not in rows["automation.quiet"]


class TestTraceScopeAndScripts:
    """Real HA traces: a readable run must not reveal what it saw of hidden entities."""

    async def _setup(self, hass):
        from homeassistant.setup import async_setup_component
        assert await async_setup_component(hass, "automation", {"automation": [{
            "id": "vault_watch", "alias": "Vault watch",
            "triggers": [{"trigger": "state", "entity_id": "binary_sensor.vault"}],
            "actions": [{"variables": {"seen": "{{ trigger.to_state.state }} on binary_sensor.vault"}}],
        }, {
            "id": "porch_watch", "alias": "Porch watch",
            "triggers": [{"trigger": "state", "entity_id": "binary_sensor.porch"}],
            "actions": [{"variables": {"seen": "{{ trigger.to_state.state }}"}}],
        }]})
        assert await async_setup_component(hass, "script", {"script": {
            "porch_lights": {"sequence": [{"variables": {"done": True}}]}}})
        await hass.async_block_till_done()

    def _key(self):
        return _access_key(permissions=PermissionTree(domains={
            "automation": PermissionNode(state="GREEN"), "script": PermissionNode(state="GREEN"),
            "binary_sensor": PermissionNode(state="GREEN"),
        }, entities={"binary_sensor.vault": PermissionNode(state="RED")}), cap_traces="allow")

    async def _run(self, hass, entity_id, name):
        hass.states.async_set(entity_id, "on", {"friendly_name": name, "access_token": "secret-value"})
        await hass.async_block_till_done()

    async def test_hidden_trigger_state_is_redacted_from_a_readable_run(self, hass):
        await self._setup(hass)
        await self._run(hass, "binary_sensor.vault", "Vault door")
        listed = _json((await _call("get_automation_traces", {"automation_id": "automation.vault_watch"}, self._key(), hass))[0])
        assert listed["count"] == 1
        content, outcome, _ = await _call("get_automation_traces", {
            "automation_id": "automation.vault_watch", "run_id": listed["traces"][0]["run_id"]}, self._key(), hass)
        assert outcome == "allowed"
        text = content["content"][0]["text"]
        assert "Vault door" not in text and "binary_sensor.vault" not in text
        assert "secret-value" not in text
        assert "<redacted" in text

    async def test_readable_trigger_state_survives_but_secrets_do_not(self, hass):
        await self._setup(hass)
        await self._run(hass, "binary_sensor.porch", "Porch motion")
        listed = _json((await _call("get_automation_traces", {"automation_id": "automation.porch_watch"}, self._key(), hass))[0])
        content, _, _ = await _call("get_automation_traces", {
            "automation_id": "automation.porch_watch", "run_id": listed["traces"][0]["run_id"]}, self._key(), hass)
        text = content["content"][0]["text"]
        assert "Porch motion" in text and "binary_sensor.porch" in text
        assert "secret-value" not in text

    async def test_script_traces_are_readable_by_script_entity_id(self, hass):
        await self._setup(hass)
        await hass.services.async_call("script", "porch_lights", blocking=True)
        content, outcome, _ = await _call("get_automation_traces", {"automation_id": "script.porch_lights"}, self._key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert (body["domain"], body["script_id"], body["count"]) == ("script", "porch_lights", 1)

    async def test_never_run_and_disabled_automations_explain_an_empty_list(self, hass):
        await self._setup(hass)
        body = _json((await _call("get_automation_traces", {"automation_id": "automation.porch_watch"}, self._key(), hass))[0])
        assert body["count"] == 0 and "never run" in body["diagnosis"]
        await hass.services.async_call("automation", "turn_off", {"entity_id": "automation.porch_watch"}, blocking=True)
        body = _json((await _call("get_automation_traces", {"automation_id": "automation.porch_watch"}, self._key(), hass))[0])
        assert "disabled" in body["diagnosis"]

    async def test_hidden_script_is_not_found(self, hass):
        await self._setup(hass)
        key = _access_key(permissions=PermissionTree(domains={"automation": PermissionNode(state="GREEN")}), cap_traces="allow")
        _, outcome, _ = await _call("get_automation_traces", {"automation_id": "script.porch_lights"}, key, hass)
        assert outcome == "not_found"


class TestTraceStorageShapes:
    """HA 2026.7 replaced the per-automation dict with TraceBuckets.

    Both shapes must read correctly, and the not-triggered bucket (new in the same
    change) must be visible: "why did it not fire" is the usual reason to read
    traces at all.
    """

    def _set_bucket(self, hass, bucket):
        from homeassistant.components.trace.const import DATA_TRACE
        hass.data[DATA_TRACE] = {"automation.auto_uid_1": bucket}

    async def test_not_triggered_traces_are_listed_and_labeled(self, hass, auto_env):
        self._set_bucket(hass, _trace_buckets(
            runs=[_FakeTrace(run_id="ran", start="2026-01-01T00:00:00+00:00")],
            not_triggered=[_FakeTrace(run_id="skipped", start="2026-01-02T00:00:00+00:00",
                                      not_triggered=True)],
        ))
        content, outcome, _ = await _call(
            "get_automation_traces", {"automation_id": "automation.morning"}, _auto_access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["count"] == 2
        by_id = {t["run_id"]: t for t in body["traces"]}
        assert by_id["skipped"]["not_triggered"] is True
        assert "not_triggered" not in by_id["ran"]

    async def test_run_id_lookup_finds_a_not_triggered_trace(self, hass, auto_env):
        self._set_bucket(hass, _trace_buckets(
            not_triggered=[_FakeTrace(run_id="skipped", not_triggered=True)]))
        content, outcome, _ = await _call(
            "get_automation_traces",
            {"automation_id": "automation.morning", "run_id": "skipped"},
            _auto_access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["run_id"] == "skipped"

    async def test_legacy_dict_shape_still_reads(self, hass, auto_env):
        # Older HA stored a plain LimitedSizeDict mapping run_id to trace.
        self._set_bucket(hass, {"run1": _FakeTrace(run_id="run1")})
        content, outcome, _ = await _call(
            "get_automation_traces", {"automation_id": "automation.morning"}, _auto_access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["traces"][0]["run_id"] == "run1"

    async def test_legacy_dict_shape_run_id_lookup(self, hass, auto_env):
        self._set_bucket(hass, {"run1": _FakeTrace(run_id="run1")})
        content, outcome, _ = await _call(
            "get_automation_traces",
            {"automation_id": "automation.morning", "run_id": "run1"},
            _auto_access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["run_id"] == "run1"

    async def test_warns_when_trace_component_not_loaded(self, hass, auto_env):
        """No traces because the component is absent reads identically to never-ran."""
        from homeassistant.components.trace.const import DATA_TRACE
        hass.data.pop(DATA_TRACE)
        content, outcome, _ = await _call(
            "get_automation_traces", {"automation_id": "automation.morning"}, _auto_access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["count"] == 0
        assert body["warnings"] == ["The trace component is not loaded; trace history is unavailable."]

    async def test_no_warning_when_loaded_but_automation_never_ran(self, hass, auto_env):
        from homeassistant.components.trace.const import DATA_TRACE
        hass.data[DATA_TRACE] = {}
        content, outcome, _ = await _call(
            "get_automation_traces", {"automation_id": "automation.morning"}, _auto_access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["count"] == 0
        assert "warnings" not in body


# --- get_system_health / check_ha_config ---

class TestDiagnostics:
    async def test_system_health_deny(self, hass):
        _, outcome, _ = await _call("get_system_health", {}, _access_key(cap_diagnostics="deny"), hass)
        assert outcome == "denied"

    async def test_system_health_returns_version(self, hass):
        content, outcome, _ = await _call("get_system_health", {}, _access_key(cap_diagnostics="allow"), hass)
        assert outcome == "allowed"
        assert _json(content)["home_assistant_version"]

    async def test_system_health_redacts_integration_secrets(self, hass):
        # Per-integration health values are arbitrary; secret-keyed values and
        # URL-embedded credentials must be scrubbed before reaching the model.
        info = {
            "cloud": {"api_key": "supersecret", "can_reach_server": "ok"},
            "broker": {"url": "https://admin:hunter2@mqtt.local/x"},
        }
        with patch("homeassistant.components.system_health.get_info", AsyncMock(return_value=info)):
            content, outcome, _ = await _call("get_system_health", {}, _access_key(cap_diagnostics="allow"), hass)
        assert outcome == "allowed"
        body = _json(content)
        text = json.dumps(body)
        assert "supersecret" not in text          # sensitive-keyed value redacted
        assert "hunter2" not in text              # URL credentials scrubbed
        # Benign diagnostic values are preserved.
        assert body["integrations"]["cloud"]["can_reach_server"] == "ok"

    async def test_system_health_scrubs_network_topology(self, hass):
        # Integration diagnostics can disclose LAN IPs, hostnames-in-URLs, and
        # filesystem paths that Phoenix MCP withholds from agents elsewhere (get_config).
        info = {
            "router": {"host": "192.168.1.50", "gateway": "10.0.0.1"},
            "service": {"endpoint": "http://homeassistant.local:8123/api"},
            "store": {"path": "/config/.storage/secret_store", "status": "ok"},
            "version": {"installed": "4.8.0.1"},  # public-IP-shaped; must NOT scrub
        }
        with patch("homeassistant.components.system_health.get_info", AsyncMock(return_value=info)):
            content, outcome, _ = await _call("get_system_health", {}, _access_key(cap_diagnostics="allow"), hass)
        assert outcome == "allowed"
        text = json.dumps(_json(content))
        assert "192.168.1.50" not in text         # private IP scrubbed
        assert "10.0.0.1" not in text              # private IP scrubbed
        assert "homeassistant.local" not in text   # URL host scrubbed
        assert "/config/.storage" not in text      # filesystem path scrubbed
        assert "ok" in text                         # benign value preserved
        assert "4.8.0.1" in text                    # version string NOT a private IP, preserved

    async def test_repairs_deny(self, hass):
        _, outcome, _ = await _call(
            "get_repairs", {}, _access_key(cap_diagnostics="deny"), hass,
        )
        assert outcome == "denied"

    async def test_repairs_safe_projection_filters_and_ignored_default(self, hass):
        hass.states.async_set("light.allowed", "on")
        hass.states.async_set("light.secret", "off")
        ir.async_create_issue(
            hass,
            "demo",
            "private_issue_light.secret_0123456789abcdef0123456789abcdef",
            breaks_in_ha_version="2027.1.0",
            data={"api_key": "raw-repair-data-secret"},
            learn_more_url="http://192.168.1.20/private/help",
            is_fixable=True,
            is_persistent=True,
            issue_domain="http://192.168.1.20/private/domain",
            severity=ir.IssueSeverity.ERROR,
            translation_key="http://192.168.1.20/private/translation",
            translation_placeholders={
                "visible": "Entity `light.allowed` remains visible",
                "hidden": "Entity `light.secret` is hidden",
                "path": "/config/.storage/core.config_entries",
                "api_key": "placeholder-secret",
            },
        )
        ir.async_create_issue(
            hass,
            "demo",
            "ignored_issue",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="ignored_problem",
        )
        ir.async_ignore_issue(hass, "demo", "ignored_issue", True)
        access_key = _access_key(
            permissions=PermissionTree(
                domains={"light": PermissionNode(state="GREEN")},
                entities={"light.secret": PermissionNode(state="RED")},
            ),
            cap_diagnostics="allow",
        )
        content, outcome, _ = await _call(
            "get_repairs", {"domain": "DEMO", "severity": "error"},
            access_key, hass,
        )
        assert outcome == "allowed"
        body = _json(content)
        assert body["count"] == body["matching_count"] == 1
        assert body["truncated"] is False
        issue = body["issues"][0]
        assert issue["repair_ref"].startswith("repair_")
        assert issue["domain"] == "demo"
        assert issue["severity"] == "error"
        assert issue["issue_domain"] == "<redacted-url>"
        assert issue["translation_key"] == "<redacted-url>"
        assert issue["breaks_in_ha_version"] == "2027.1.0"
        assert issue["is_fixable"] is True
        assert issue["has_learn_more"] is True
        assert issue["translation_placeholders"]["visible"] == (
            "Entity `light.allowed` remains visible"
        )
        assert issue["translation_placeholders"]["hidden"] == (
            "Entity `<redacted>` is hidden"
        )
        text = json.dumps(body)
        for forbidden in (
            "private_issue", "0123456789abcdef", "raw-repair-data-secret",
            "placeholder-secret", "192.168.1.20", "/config/.storage",
            "light.secret", "ignored_problem",
        ):
            assert forbidden not in text

        with_ignored, outcome, _ = await _call(
            "get_repairs",
            {"domain": "demo", "include_ignored": True, "limit": 2},
            access_key, hass,
        )
        assert outcome == "allowed"
        included = _json(with_ignored)
        assert included["matching_count"] == 2
        assert any(issue["ignored"] for issue in included["issues"])

    async def test_repairs_refs_are_stable_per_access_key_and_results_are_bounded(self, hass):
        for index in range(3):
            ir.async_create_issue(
                hass,
                "bounded",
                f"issue_{index}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="bounded_issue",
            )
        first_access_key = _access_key(cap_diagnostics="allow")
        second_access_key = _access_key(cap_diagnostics="allow")
        second_access_key.access_key_hash = "different-access-key-hash"
        first = _json((await _call(
            "get_repairs", {"domain": "bounded", "limit": 2}, first_access_key, hass,
        ))[0])
        repeated = _json((await _call(
            "get_repairs", {"domain": "bounded", "limit": 2}, first_access_key, hass,
        ))[0])
        second = _json((await _call(
            "get_repairs", {"domain": "bounded", "limit": 2}, second_access_key, hass,
        ))[0])
        assert first["count"] == 2
        assert first["matching_count"] == 3
        assert first["truncated"] is True
        assert [row["repair_ref"] for row in first["issues"]] == [
            row["repair_ref"] for row in repeated["issues"]
        ]
        assert [row["repair_ref"] for row in first["issues"]] != [
            row["repair_ref"] for row in second["issues"]
        ]

    @pytest.mark.parametrize("args", [
        {"limit": True}, {"limit": 0},
        {"include_ignored": "yes"}, {"severity": "fatal"},
    ])
    async def test_repairs_rejects_invalid_filters(self, hass, args):
        _, outcome, _ = await _call(
            "get_repairs", args, _access_key(cap_diagnostics="allow"), hass,
        )
        assert outcome == "invalid_request"

    async def test_check_ha_config_deny(self, hass):
        _, outcome, _ = await _call("check_ha_config", {}, _access_key(cap_diagnostics="deny"), hass)
        assert outcome == "denied"

    async def test_check_ha_config_valid(self, hass):
        fake = MagicMock(errors=[], warnings=[])
        with patch("homeassistant.helpers.check_config.async_check_ha_config_file", AsyncMock(return_value=fake)):
            content, outcome, _ = await _call("check_ha_config", {}, _access_key(cap_diagnostics="allow"), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["valid"] is True
        assert body["errors"] == []

    async def test_check_ha_config_reports_errors(self, hass):
        err = MagicMock(message="bad yaml", domain="light")
        fake = MagicMock(errors=[err], warnings=[])
        with patch("homeassistant.helpers.check_config.async_check_ha_config_file", AsyncMock(return_value=fake)):
            content, _, _ = await _call("check_ha_config", {}, _access_key(cap_diagnostics="allow"), hass)
        body = _json(content)
        assert body["valid"] is False
        assert body["errors"][0]["message"].startswith("Configuration validation failed")
        assert "bad yaml" not in json.dumps(body)
