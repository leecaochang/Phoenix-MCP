"""Tests for analysis and introspection MCP tools."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.agent_recovery import AgentRecovery
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord


def _access_key(**caps) -> AccessKeyRecord:
    tree = PermissionTree(domains={
        "light": PermissionNode(state="GREEN"),
        "automation": PermissionNode(state="GREEN"),
    })
    base = {"cap_search": "allow", "cap_diagnostics": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


def _data(mesa=None) -> MagicMock:
    d = MagicMock()
    d.agent_recovery = AgentRecovery()
    d.mesa = mesa
    return d


async def _call(name, args, access_key, hass, data=None):
    if name == "whatif":
        from custom_components.phoenix_mcp.tools.discovery import _domain_entries
        configs, _ = _domain_entries(hass, "automation")
        registry = er.async_get(hass)
        for config in configs:
            identity = config.get("id")
            if identity:
                entry = registry.async_get_or_create("automation", "test_integration", str(identity), suggested_object_id=str(identity))
                hass.states.async_set(entry.entity_id, "on")
    args = dict(args)
    name = {
        "validate_config": "validate_automation_or_script",
        "compare_state": "compare_states",
    }.get(name, name)
    if name == "compare_states" and "entity_id" in args:
        entity_ids = args.pop("entity_id")
        args["entity_ids"] = (
            entity_ids if isinstance(entity_ids, list) else [entity_ids]
        )
    if name == "dry_run_service" and isinstance(args.get("service"), str):
        service = {
            "domain": args.pop("domain"),
            "name": args.pop("service"),
        }
        entity_id = args.pop("entity_id", None)
        if entity_id is not None:
            args["targets"] = [{"kind": "entity", "ids": [entity_id]}]
        args["service"] = service
    return await _call_tool(name, args, access_key, hass, data or _data())


# --- get_capability_summary ---

class TestCapabilitySummary:
    async def test_no_cap_required_and_reports_modes(self, hass):
        access_key = _access_key(cap_search="deny", cap_physical_control="confirm", cap_config_read="allow")
        content, outcome, _ = await _call("get_capability_summary", {}, access_key, hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["persona"] == access_key.persona
        assert "cap_physical_control" in body["confirm_gated"]
        assert "cap_config_read" in body["allowed"]
        assert "cap_search" in body["denied"]
        assert body["write_scope"] is True  # light GREEN

    async def test_tool_gate_map(self, hass):
        access_key = _access_key(cap_search="deny", cap_automation_write="confirm", cap_config_read="allow")
        content, _, _ = await _call("get_capability_summary", {}, access_key, hass)
        tools = _json(content)["tools"]
        assert "search_entities" in tools["unavailable"]      # cap_search deny
        assert "create_automation" in tools["needs_approval"]  # cap_automation_write confirm
        assert "get_config" in tools["usable"]                 # cap_config_read allow
        assert "get_approval_status" in tools["usable"]        # no cap, always usable
        # a tool appears in exactly one bucket
        all_listed = tools["usable"] + tools["needs_approval"] + tools["unavailable"]
        assert len(all_listed) == len(set(all_listed))


# --- get_audit_summary ---

class TestAuditSummary:
    async def test_returns_own_entries(self, hass):
        access_key = _access_key()
        entry = SimpleNamespace(request_id="r1", timestamp="2026-01-01", method="tools/call",
                                resource="get_state", outcome="allowed", mesa_advisory=False)
        data = _data()
        data.audit.query.return_value = [entry]
        content, outcome, _ = await _call("get_audit_summary", {"limit": 10}, access_key, hass, data)
        assert outcome == "allowed"
        body = _json(content)
        assert body["entries"][0]["request_id"] == "r1"
        # mesa_advisory omitted when false (matches AuditEntry.to_dict)
        assert "mesa_advisory" not in body["entries"][0]
        # query must be scoped to this token
        assert data.audit.query.call_args.kwargs["access_key_id"] == access_key.id

    async def test_surfaces_mesa_advisory_flag(self, hass):
        access_key = _access_key()
        entry = SimpleNamespace(request_id="r2", timestamp="2026-01-01", method="tools/call",
                                resource="call_service", outcome="allowed", mesa_advisory=True)
        data = _data()
        data.audit.query.return_value = [entry]
        content, outcome, _ = await _call("get_audit_summary", {"limit": 10}, access_key, hass, data)
        assert outcome == "allowed"
        body = _json(content)
        assert body["entries"][0]["mesa_advisory"] is True

    async def test_invalid_outcome_filter(self, hass):
        data = _data()
        data.audit.query.return_value = None
        _, outcome, _ = await _call("get_audit_summary", {"outcome": "bogus"}, _access_key(), hass, data)
        assert outcome == "invalid_request"


# --- get_approval_status ---

def _approval_dict(approval_id: str, access_key_id: str, status: str = "pending") -> dict:
    now = utcnow()
    return {
        "id": approval_id, "access_key_id": access_key_id, "access_key_name": "t",
        "tool_name": "create_backup", "cap_name": "cap_backup",
        "args": {}, "diff": {}, "status": status,
        "created_at": now.isoformat(), "expires_at": now.isoformat(),
        "request_id": "r",
    }


class TestApprovalStatus:
    async def test_lists_own_pending_when_no_id(self, hass):
        access_key = _access_key()
        other_id = str(uuid.uuid4())
        data = _data()
        data.store.get_pending_approvals.return_value = [
            _approval_dict("appr_mine", access_key.id),
            _approval_dict("appr_other", other_id),            # another token: hidden
            _approval_dict("appr_done", access_key.id, "approved"),  # resolved: excluded
        ]
        content, outcome, _ = await _call("get_approval_status", {}, access_key, hass, data)
        assert outcome == "allowed"
        body = _json(content)
        assert body["count"] == 1
        assert [a["approval_id"] for a in body["pending_approvals"]] == ["appr_mine"]

    async def test_single_id_still_works(self, hass):
        access_key = _access_key()
        data = _data()
        data.store.get_pending_approvals.return_value = [_approval_dict("appr_mine", access_key.id)]
        content, outcome, resource = await _call(
            "get_approval_status", {"approval_id": "appr_mine"}, access_key, hass, data)
        assert outcome == "allowed"
        assert _json(content)["status"] == "pending"

    async def test_cross_token_id_not_found(self, hass):
        access_key = _access_key()
        data = _data()
        data.store.get_pending_approvals.return_value = [_approval_dict("appr_x", str(uuid.uuid4()))]
        _, outcome, _ = await _call(
            "get_approval_status", {"approval_id": "appr_x"}, access_key, hass, data)
        assert outcome == "not_found"


# --- wait_for_approval ---

class TestWaitForApproval:
    async def test_missing_id_invalid(self, hass):
        _, outcome, _ = await _call("wait_for_approval", {}, _access_key(), hass, _data())
        assert outcome == "invalid_request"

    async def test_cross_token_not_found(self, hass):
        access_key = _access_key()
        data = _data()
        data.store.get_pending_approvals.return_value = [_approval_dict("appr_x", str(uuid.uuid4()))]
        _, outcome, _ = await _call(
            "wait_for_approval", {"approval_ids": ["appr_x"]}, access_key, hass, data)
        assert outcome == "not_found"

    async def test_already_resolved_returns_immediately(self, hass):
        access_key = _access_key()
        data = _data()
        data.store.get_pending_approvals.return_value = [_approval_dict("appr_mine", access_key.id, "approved")]
        content, outcome, _ = await _call(
            "wait_for_approval", {"approval_ids": ["appr_mine"]}, access_key, hass, data)
        assert outcome == "allowed"
        body = _json(content)
        assert body["approvals"][0]["status"] == "approved"
        assert body["resolved"] is True

    async def test_wakes_on_resolved_event(self, hass):
        access_key = _access_key()
        data = _data()
        rec = _approval_dict("appr_mine", access_key.id, "pending")
        data.store.get_pending_approvals.return_value = [rec]

        task = asyncio.ensure_future(
            _call("wait_for_approval", {"approval_ids": ["appr_mine"], "timeout": 5}, access_key, hass, data))
        # Deterministic readiness instead of sleep(0): poll the public listener
        # registry until the tool has actually subscribed to the resolved event,
        # then fire it. No fixed delay, no coupling to scheduler ordering.
        for _ in range(200):
            if hass.bus.async_listeners().get("phoenix_mcp_approval_resolved", 0) >= 1:
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("wait_for_approval never subscribed to phoenix_mcp_approval_resolved")
        rec["status"] = "approved"  # the resolved record the re-read will return
        hass.bus.async_fire("phoenix_mcp_approval_resolved", {"approval_id": "appr_mine"})
        content, outcome, _ = await task

        assert outcome == "allowed"
        body = _json(content)
        assert body["resolved"] is True
        assert body["approvals"][0]["status"] == "approved"

    async def test_timeout_returns_pending(self, hass):
        access_key = _access_key()
        data = _data()
        data.store.get_pending_approvals.return_value = [_approval_dict("appr_mine", access_key.id, "pending")]
        content, outcome, _ = await _call(
            "wait_for_approval", {"approval_ids": ["appr_mine"], "timeout": 1}, access_key, hass, data)
        assert outcome == "allowed"
        body = _json(content)
        assert body["resolved"] is False
        assert body["approvals"][0]["status"] == "pending"


# --- whatif ---

@pytest.fixture
def whatif_env(hass: HomeAssistant):
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    e = ent_reg.async_get_or_create("light", "test_integration", "uid_k", config_entry=entry, suggested_object_id="kitchen")
    hass.states.async_set(e.entity_id, "off", {})
    hass.states.async_set("sensor.secret", "1", {})
    with open(os.path.join(hass.config.config_dir, "automations.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump([
            {"id": "a1", "alias": "OnWatcher",
             "trigger": [{"platform": "state", "entity_id": "light.kitchen", "to": "on"}],
             "action": []},
            {"id": "a2", "alias": "OffWatcher",
             "trigger": [{"platform": "state", "entity_id": "light.kitchen", "to": "off"}],
             "action": []},
            {"id": "a3", "alias": "Templated",
             "trigger": [{"platform": "template", "value_template": "{{ is_state('light.kitchen','on') }}"}],
             "action": []},
        ], f)
    return hass


class TestWhatif:
    @pytest.mark.parametrize("layout", ["file", "directory", "inline"])
    @pytest.mark.parametrize("hidden", [False, True])
    async def test_reads_configured_automations_with_scope(self, hass, whatif_env, layout, hidden):
        from pathlib import Path

        config = {"id": "included", "alias": "Included watcher", "triggers": [
            {"trigger": "state", "entity_id": "light.kitchen", "to": "on"}], "actions": []}
        root = Path(hass.config.config_dir)
        if layout == "inline":
            source = yaml.safe_dump({"automation": [config]})
        elif layout == "file":
            (root / "watchers.yaml").write_text(yaml.safe_dump([config]))
            source = "automation: !include watchers.yaml\n"
        else:
            (root / "watchers").mkdir()
            (root / "watchers" / "included.yaml").write_text(yaml.safe_dump(config))
            source = "automation: !include_dir_list watchers\n"
        (root / "configuration.yaml").write_text(source)
        access_key = _access_key()
        if hidden:
            access_key.permissions.entities["automation.included"] = PermissionNode(state="RED")
        content, outcome, _ = await _call(
            "whatif", {"entity_id": "light.kitchen", "hypothetical_state": "on"}, access_key, hass)
        assert outcome == "allowed"
        candidates = _json(content)["candidates"]
        if hidden:
            assert candidates == []
            assert "Included watcher" not in json.dumps(_json(content))
        else:
            assert len(candidates) == 1
            assert candidates[0]["automation_id"] == "included"
            assert candidates[0]["would_fire"] is True

    async def test_reports_unreadable_automation_include(self, hass, whatif_env):
        with open(hass.config.path("configuration.yaml"), "w") as file:
            file.write("automation: !include missing.yaml\n")
        content, outcome, _ = await _call(
            "whatif", {"entity_id": "light.kitchen", "hypothetical_state": "on"}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["candidates"] == []
        assert any(gap["kind"] == "automation" for gap in body["not_searched"])

    async def test_deny(self, hass, whatif_env):
        _, outcome, _ = await _call("whatif", {"entity_id": "light.kitchen", "hypothetical_state": "on"}, _access_key(cap_search="deny"), hass)
        assert outcome == "denied"

    async def test_predicts_matching_trigger(self, hass, whatif_env):
        content, outcome, _ = await _call("whatif", {"entity_id": "light.kitchen", "hypothetical_state": "on"}, _access_key(), hass)
        assert outcome == "allowed"
        by_id = {c["automation_id"]: c for c in _json(content)["candidates"]}
        assert by_id["a1"]["would_fire"] is True   # to: on
        assert by_id["a2"]["would_fire"] is False  # to: off
        # template trigger that references the entity by name is not in entity_id refs,
        # so a3 is not even a candidate.
        assert "a3" not in by_id

    async def test_inaccessible_not_found(self, hass, whatif_env):
        _, outcome, _ = await _call("whatif", {"entity_id": "sensor.secret", "hypothetical_state": "on"}, _access_key(), hass)
        assert outcome == "not_found"

    async def test_missing_hypothetical(self, hass, whatif_env):
        _, outcome, _ = await _call("whatif", {"entity_id": "light.kitchen"}, _access_key(), hass)
        assert outcome == "invalid_request"

    async def test_device_trigger_coverage_reports_unknown(self, hass):
        """A trigger naming this entity's DEVICE (no entity_id) is a candidate.

        It reports would_fire "unknown", because device trigger types are
        integration-specific and cannot be simulated, instead of being invisible.
        """
        from homeassistant.helpers import device_registry as dr

        entry = MockConfigEntry(domain="test_integration", entry_id="e_dev")
        entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("test_integration", "d1")})
        e = er.async_get(hass).async_get_or_create(
            "light", "test_integration", "uid_dev", config_entry=entry,
            suggested_object_id="rack", device_id=device.id)
        hass.states.async_set(e.entity_id, "off", {})
        with open(os.path.join(hass.config.config_dir, "automations.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump([
                {"id": "a_dev", "alias": "DeviceWatcher",
                 "trigger": [{"platform": "device", "device_id": device.id,
                              "domain": "light", "type": "turned_on"}],
                 "action": []},
            ], f)

        content, outcome, _ = await _call(
            "whatif", {"entity_id": e.entity_id, "hypothetical_state": "on"}, _access_key(), hass)
        assert outcome == "allowed"
        by_id = {c["automation_id"]: c for c in _json(content)["candidates"]}
        assert by_id["a_dev"]["would_fire"] == "unknown"
        assert by_id["a_dev"]["triggers"][0]["platform"] == "device"


# --- dry_run_service ---

class TestDryRunService:
    async def test_deny(self, hass):
        _, outcome, _ = await _call("dry_run_service", {"domain": "light", "service": "turn_on"}, _access_key(cap_search="deny"), hass)
        assert outcome == "denied"

    async def test_resolves_without_executing(self, hass):
        # resolve_service_targets requires the entity to exist in the registry
        # (a service call never creates an entity), so register it.
        entry = MockConfigEntry(domain="test_integration", entry_id="e_dry")
        entry.add_to_hass(hass)
        e = er.async_get(hass).async_get_or_create(
            "light", "test_integration", "uid_dry", config_entry=entry, suggested_object_id="kitchen")
        hass.states.async_set(e.entity_id, "off", {})
        called = []
        hass.services.async_register("light", "turn_on", lambda c: called.append(c))
        content, outcome, _ = await _call(
            "dry_run_service", {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["resolved_entities"] == ["light.kitchen"]
        assert body["mesa"] is None  # no mesa runtime in this data mock
        assert body["predicted_outcome"] == "allowed"
        assert called == []  # nothing executed

    async def test_dual_gate_system_service(self, hass):
        content, _, _ = await _call("dry_run_service", {"domain": "homeassistant", "service": "restart"}, _access_key(), hass)
        body = _json(content)
        assert body["system_service"] is True
        assert body["would_execute"] is False  # cap_restart defaults to deny
        assert body["predicted_outcome"] == "denied"

    async def test_physical_gate_confirm_predicts_pending(self, hass):
        entry = MockConfigEntry(domain="test_integration", entry_id="e_lock")
        entry.add_to_hass(hass)
        e = er.async_get(hass).async_get_or_create(
            "lock", "test_integration", "uid_lock", config_entry=entry, suggested_object_id="front")
        hass.states.async_set(e.entity_id, "locked", {})
        access_key = _access_key(cap_physical_control="confirm")
        access_key.permissions.domains["lock"] = PermissionNode(state="GREEN")
        content, _, _ = await _call(
            "dry_run_service",
            {"domain": "lock", "service": "unlock", "entity_id": "lock.front"}, access_key, hass)
        body = _json(content)
        assert body["physical_gate"] is True
        assert body["predicted_outcome"] == "pending_approval"

    async def test_valve_is_physical_gated(self, hass):
        # Valve open/close/stop/set_position are physical actuation (water/gas
        # flow), gated on cap_physical_control like covers.
        entry = MockConfigEntry(domain="test_integration", entry_id="e_valve")
        entry.add_to_hass(hass)
        e = er.async_get(hass).async_get_or_create(
            "valve", "test_integration", "uid_valve", config_entry=entry, suggested_object_id="irrigation")
        hass.states.async_set(e.entity_id, "closed", {})
        access_key = _access_key(cap_physical_control="confirm")
        access_key.permissions.domains["valve"] = PermissionNode(state="GREEN")
        content, _, _ = await _call(
            "dry_run_service",
            {"domain": "valve", "service": "open_valve", "entity_id": "valve.irrigation"}, access_key, hass)
        body = _json(content)
        assert body["physical_gate"] is True
        assert body["predicted_outcome"] == "pending_approval"

    async def test_mesa_confirm_predicts_pending(self, hass):
        entry = MockConfigEntry(domain="test_integration", entry_id="e_dry2")
        entry.add_to_hass(hass)
        e = er.async_get(hass).async_get_or_create(
            "light", "test_integration", "uid_dry2", config_entry=entry, suggested_object_id="den")
        hass.states.async_set(e.entity_id, "off", {})
        data = _data(mesa=MagicMock())
        data.store.get_settings.return_value = SimpleNamespace(mesa_mode="enforced")
        verdict = SimpleNamespace(allowed=[], confirm=["light.den"], blocked=[], warnings=[])
        with patch("custom_components.phoenix_mcp.tools.discovery.evaluate_service_entities", return_value=verdict):
            content, _, _ = await _call(
                "dry_run_service",
                {"domain": "light", "service": "turn_on", "entity_id": "light.den"}, _access_key(), hass, data)
        body = _json(content)
        assert body["predicted_outcome"] == "pending_approval"


# --- validate_config ---

class TestValidateConfig:
    async def test_deny(self, hass):
        _, outcome, _ = await _call("validate_config", {"type": "automation", "config": {}}, _access_key(cap_diagnostics="deny"), hass)
        assert outcome == "denied"

    async def test_valid_automation_reports_referenced(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        config = {
            "alias": "x",
            "trigger": [{"platform": "state", "entity_id": "light.kitchen"}],
            "action": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
        }
        content, outcome, _ = await _call("validate_config", {"type": "automation", "config": config}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["valid"] is True
        ref = {r["entity_id"]: r for r in body["referenced_entities"]}
        assert ref["light.kitchen"]["exists"] is True
        assert ref["light.kitchen"]["accessible"] is True

    async def test_out_of_scope_entity_is_not_an_existence_oracle(self, hass):
        # A real entity outside the token's scope must look identical to a typo:
        # exists is collapsed to the accessible flag so existence never leaks.
        hass.states.async_set("lock.front_door", "locked", {})
        config = {
            "alias": "x",
            "trigger": [{"platform": "state", "entity_id": "light.kitchen"}],
            "action": [{"service": "lock.lock", "target": {"entity_id": "lock.front_door"}}],
        }
        content, outcome, _ = await _call("validate_config", {"type": "automation", "config": config}, _access_key(), hass)
        assert outcome == "allowed"
        ref = {r["entity_id"]: r for r in _json(content)["referenced_entities"]}
        assert ref["lock.front_door"]["accessible"] is False
        assert ref["lock.front_door"]["exists"] is False

    async def test_invalid_automation(self, hass):
        content, _, _ = await _call("validate_config", {"type": "automation", "config": {"alias": "x"}}, _access_key(), hass)
        body = _json(content)
        assert body["valid"] is False
        assert body["errors"]

    async def test_bad_type(self, hass):
        _, outcome, _ = await _call("validate_config", {"type": "scene", "config": {}}, _access_key(), hass)
        assert outcome == "invalid_request"


# --- compare_state / recent_activity (recorder-backed) ---

class _FakeState:
    def __init__(self, eid, state, when):
        self._eid, self._s, self._w = eid, state, when

    def as_dict(self):
        return {"entity_id": self._eid, "state": self._s, "last_changed": self._w, "last_updated": self._w}


def _patch_history(mapping, second_mapping=None):
    inst = MagicMock()
    if second_mapping is None:
        inst.async_add_executor_job = AsyncMock(return_value=mapping)
    else:
        inst.async_add_executor_job = AsyncMock(side_effect=[mapping, second_mapping])
    return patch("homeassistant.components.recorder.get_instance", return_value=inst)


class TestCompareState:
    async def test_deny(self, hass):
        _, outcome, _ = await _call("compare_state", {"entity_id": "light.kitchen", "t1": "24h"}, _access_key(cap_search="deny"), hass)
        assert outcome == "denied"

    async def test_changed_flag(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        now = utcnow()
        at_t1 = {"light.kitchen": [_FakeState("light.kitchen", "off", now - timedelta(hours=20))]}
        at_t2 = {"light.kitchen": [_FakeState("light.kitchen", "on", now)]}
        with _patch_history(at_t1, at_t2):
            content, outcome, _ = await _call("compare_state", {"entity_id": "light.kitchen", "t1": "24h"}, _access_key(), hass)
        assert outcome == "allowed"
        comp = _json(content)["comparisons"][0]
        assert comp["state_at_t1"] == "off"
        assert comp["state_at_t2"] == "on"
        assert comp["changed"] is True

    async def test_invalid_t1(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        _, outcome, _ = await _call("compare_state", {"entity_id": "light.kitchen", "t1": "notatime"}, _access_key(), hass)
        assert outcome == "invalid_request"

    async def test_reversed_times_are_rejected_before_history(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        with patch("homeassistant.components.recorder.get_instance") as get_instance:
            _, outcome, _ = await _call(
                "compare_state",
                {"entity_id": "light.kitchen", "t1": "2026-01-02T00:00:00+00:00", "t2": "2026-01-01T00:00:00+00:00"},
                _access_key(), hass,
            )
        assert outcome == "invalid_request"
        get_instance.assert_not_called()

    async def test_more_than_100_entities_is_rejected(self, hass):
        ids = [f"light.item_{index}" for index in range(101)]
        _, outcome, _ = await _call("compare_state", {"entity_id": ids, "t1": "24h"}, _access_key(), hass)
        assert outcome == "invalid_request"


class TestRecentActivity:
    async def test_deny(self, hass):
        _, outcome, _ = await _call("recent_activity", {}, _access_key(cap_search="deny"), hass)
        assert outcome == "denied"

    async def test_lists_changes(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        now = utcnow()
        mapping = {"light.kitchen": [_FakeState("light.kitchen", "on", now)]}
        with _patch_history(mapping):
            content, outcome, _ = await _call("recent_activity", {"minutes": 60}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["changes"][0]["entity_id"] == "light.kitchen"
        assert body["window_minutes"] == 60
