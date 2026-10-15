"""Tests for helper CRUD MCP tools (create/edit/delete_helper, list_helpers).

Helper writes are Confirm-gated on cap_helper_write and execute via the
in-process WS command dispatcher (ws_dispatch). list_helpers is a
cap_registry_read read scoped to accessible helper entities.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.const import UnitOfTemperature
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from tests.runtime_fixtures import mesa_off_store

from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _EXECUTOR_REGISTRY, _call_tool
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.tools.helper import _build_diff_delete_helper


def _access_key(**caps) -> AccessKeyRecord:
    tree = caps.pop("permissions", PermissionTree(domains={
        "input_boolean": PermissionNode(state="GREEN"),
        "input_button": PermissionNode(state="GREEN"),
        "schedule": PermissionNode(state="GREEN"),
        "zone": PermissionNode(state="GREEN"),
        "tag": PermissionNode(state="GREEN"),
        "person": PermissionNode(state="GREEN"),
        "device_tracker": PermissionNode(state="GREEN"),
        "sensor": PermissionNode(state="GREEN"),
    }))
    base = {"cap_helper_write": "allow", "cap_registry_read": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, access_key, hass):
    return await _call_tool(name, args, access_key, hass, PhoenixData(
        store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(),
    ))


@pytest.fixture
async def helper_env(hass: HomeAssistant):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    return hass


class TestExecutorRegistration:
    def test_helper_executors_registered(self):
        for name in ("create_helper", "edit_helper", "delete_helper"):
            assert name in _EXECUTOR_REGISTRY


class TestCreateHelper:
    async def test_deny_without_cap(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "X"}},
            _access_key(cap_helper_write="deny"), hass)
        assert outcome == "denied"

    async def test_create(self, hass, helper_env, hass_admin_user):
        content, outcome, _ = await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Guest mode"}}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["helper"]["name"] == "Guest mode"
        helper_id = body["helper"]["id"]
        await hass.async_block_till_done()
        # The returned id must map to a real entity, not just "some input_boolean
        # exists": resolve the registry entry by its unique_id and confirm the live
        # entity_id is what got created.
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        entity_id = reg.async_get_entity_id("input_boolean", "input_boolean", helper_id)
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None
        assert hass.states.get(entity_id).attributes.get("friendly_name") == "Guest mode"

    async def test_bad_type(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "light", "config": {"name": "X"}}, _access_key(), hass)
        assert outcome == "invalid_request"

    async def test_empty_config(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {}}, _access_key(), hass)
        assert outcome == "invalid_request"

    async def test_empty_config_confirm_mode_rejected_before_pending(self, hass, helper_env, hass_admin_user):
        """An invalid helper config fails before any approval is created, even in confirm mode."""
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {}},
            _access_key(cap_helper_write="confirm"), hass)
        assert outcome == "invalid_request"

    async def test_bad_type_confirm_mode_rejected_before_pending(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "light", "config": {"name": "X"}},
            _access_key(cap_helper_write="confirm"), hass)
        assert outcome == "invalid_request"


class TestConfigFlowHelperCreation:
    async def test_forms_carry_ha_help_and_rejections_return_the_form_to_fix(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        """HA's own field help reaches the caller; a rejected step comes back with its errors."""
        for entity_id in ("sensor.a", "sensor.b", "sensor.c"):
            hass.states.async_set(entity_id, "1")
        content, outcome, _ = await _call(
            "create_helper", {"helper_type": "mold_indicator", "flow_steps": []}, _access_key(), hass,
        )
        assert outcome == "allowed"
        fields = {field["name"]: field for field in _json(content)["step"]["schema"]}
        assert fields["calibration_factor"]["help"] == "Needs to be calibrated to the critical point in the room."
        assert fields["indoor_temp_sensor"]["help"] == "The entity ID of the indoor temperature sensor."

        final = {"name": "Probe", "indoor_temp_sensor": "sensor.a", "indoor_humidity_sensor": "sensor.b",
                 "outdoor_temp_sensor": "sensor.c", "calibration_factor": "not a number"}
        content, outcome, _ = await _call(
            "create_helper", {"helper_type": "mold_indicator", "flow_steps": [{"step_id": "user", "data": final}]},
            _access_key(), hass,
        )
        body = _json(content)
        assert outcome == "allowed" and body["status"] == "needs_input"
        assert body["step"]["step_id"] == "user" and "calibration_factor" in body["step"]["errors"]
        assert "flow_steps[0]" in body["note"] and "replace" in body["note"]
        assert {field["name"] for field in body["step"]["schema"]} >= {"calibration_factor"}
        assert not hass.config_entries.async_entries("mold_indicator")

        content, outcome, _ = await _call(
            "create_helper",
            {"helper_type": "statistics", "flow_steps": [{"step_id": "user", "data": {"name": "Stats"}}]},
            _access_key(), hass,
        )
        body = _json(content)
        assert outcome == "allowed" and body["step"]["step_id"] == "user"
        assert "entity_id" in body["step"]["errors"] and "flow_steps[0]" in body["note"]
        assert not hass.config_entries.flow.async_progress_by_handler("statistics")

    async def test_mold_indicator_schema_create_and_remove(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        """A real single-step helper flow, including immediate materialization."""
        hass.states.async_set(
            "sensor.indoor_temp", "22",
            {"device_class": "temperature", "unit_of_measurement": UnitOfTemperature.CELSIUS},
        )
        hass.states.async_set(
            "sensor.outdoor_temp", "12",
            {"device_class": "temperature", "unit_of_measurement": UnitOfTemperature.CELSIUS},
        )
        hass.states.async_set(
            "sensor.indoor_humidity", "55",
            {"device_class": "humidity", "unit_of_measurement": "%"},
        )

        content, outcome, _ = await _call(
            "create_helper",
            {"helper_type": "mold_indicator", "flow_steps": []},
            _access_key(), hass,
        )
        assert outcome == "allowed"
        first = _json(content)
        assert first["status"] == "needs_input"
        assert first["step"]["step_id"] == "user"
        assert first["step"]["last_step"] is True
        assert {field["name"] for field in first["step"]["schema"]} == {
            "name", "indoor_temp_sensor", "indoor_humidity_sensor", "outdoor_temp_sensor",
            "calibration_factor",
        }
        assert not hass.config_entries.flow.async_progress_by_handler("mold_indicator")

        content, outcome, _ = await _call(
            "create_helper",
            {
                "helper_type": "mold_indicator",
                "flow_steps": [{
                    "step_id": "user",
                    "data": {
                        "name": "Phoenix Mold Probe",
                        "indoor_temp_sensor": "sensor.indoor_temp",
                        "indoor_humidity_sensor": "sensor.indoor_humidity",
                        "outdoor_temp_sensor": "sensor.outdoor_temp",
                        "calibration_factor": 2.0,
                    },
                }],
            },
            _access_key(), hass,
        )
        assert outcome == "allowed", content
        body = _json(content)
        entry_id = body["config_entry"]["entry_id"]
        try:
            assert body["config_entry"]["domain"] == "mold_indicator"
            assert body["config_entry"]["title"] == "Phoenix Mold Probe"
            assert body["config_entry"]["state"] == "loaded"
            assert len(body["entity_ids"]) == 1
            assert hass.states.get(body["entity_ids"][0]) is not None
            assert hass.config_entries.async_get_entry(entry_id) is not None
        finally:
            await hass.config_entries.async_remove(entry_id)

    async def test_history_stats_returns_each_form_and_creates(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        """The real three-step helper flow is replayable without leaked flows."""
        assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
        # Loading the real flow normally starts Recorder, whose fixture must run
        # before this suite's session-scoped hass. Keep the real history_stats
        # flow handler and config-entry manager, but replace dependency setup and
        # the entity platform with the smallest materializer.
        import homeassistant.components.history_stats as history_stats
        from homeassistant.helpers import entity_registry as er

        async def _setup_history_entry(hass, entry):
            registry_entry = er.async_get(hass).async_get_or_create(
                "history_stats", "history_stats", entry.entry_id,
                config_entry=entry,
                suggested_object_id=entry.title,
            )
            hass.states.async_set(registry_entry.entity_id, "0")
            entry.async_on_unload(
                lambda: hass.states.async_remove(registry_entry.entity_id)
            )
            return True

        steps = [{
            "step_id": "user",
            "data": {
                "name": "Phoenix History Probe",
                "entity_id": "input_boolean.flow_source",
                "type": "time",
            },
        }]
        hass.states.async_set("input_boolean.flow_source", "off")

        with (
            patch(
                "homeassistant.config_entries.async_process_deps_reqs",
                AsyncMock(),
            ),
            patch(
                "homeassistant.setup.async_process_deps_reqs",
                AsyncMock(),
            ),
            patch.object(history_stats, "async_setup_entry", _setup_history_entry),
        ):
            content, outcome, _ = await _call(
                "create_helper",
                {"helper_type": "history_stats", "flow_steps": steps},
                _access_key(), hass,
            )
            assert outcome == "allowed"
            state_form = _json(content)["step"]
            assert state_form["step_id"] == "state"
            assert state_form["last_step"] is False
            assert not hass.config_entries.flow.async_progress_by_handler("history_stats")

            steps.append({
                "step_id": "state",
                "data": {"entity_id": "input_boolean.flow_source", "state": ["on"]},
            })
            content, outcome, _ = await _call(
                "create_helper",
                {"helper_type": "history_stats", "flow_steps": steps},
                _access_key(), hass,
            )
            assert outcome == "allowed"
            options_form = _json(content)["step"]
            assert options_form["step_id"] == "options"
            assert options_form["last_step"] is True
            assert {field["name"] for field in options_form["schema"]} >= {
                "start", "end", "duration",
            }
            assert not hass.config_entries.flow.async_progress_by_handler("history_stats")

            steps.append({
                "step_id": "options",
                "data": {
                    "entity_id": "input_boolean.flow_source",
                    "state": ["on"],
                    "type": "time",
                    "start": "{{ today_at() }}",
                    "duration": {"hours": 1},
                },
            })
            content, outcome, _ = await _call(
                "create_helper",
                {"helper_type": "history_stats", "flow_steps": steps},
                _access_key(), hass,
            )
            assert outcome == "allowed", content
            body = _json(content)
            entry_id = body["config_entry"]["entry_id"]
            try:
                assert body["config_entry"]["domain"] == "history_stats"
                assert body["entity_ids"]
                assert all(hass.states.get(entity_id) is not None for entity_id in body["entity_ids"])
            finally:
                await hass.config_entries.async_remove(entry_id)

    async def test_out_of_scope_source_is_rejected_before_approval(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        hass.states.async_set("sensor.indoor_temp", "22")
        hass.states.async_set("sensor.outdoor_temp", "12")
        hass.states.async_set("sensor.indoor_humidity", "55")
        content, outcome, _ = await _call(
            "create_helper",
            {
                "helper_type": "mold_indicator",
                "flow_steps": [{
                    "step_id": "user",
                    "data": {
                        "name": "No Scope",
                        "indoor_temp_sensor": "sensor.indoor_temp",
                        "indoor_humidity_sensor": "sensor.indoor_humidity",
                        "outdoor_temp_sensor": "sensor.outdoor_temp",
                        "calibration_factor": 2.0,
                    },
                }],
            },
            _access_key(
                cap_helper_write="confirm",
                permissions=PermissionTree(domains={}),
            ),
            hass,
        )
        assert outcome == "denied"
        assert "outside this access key's write scope" in content["content"][0]["text"]

    async def test_final_form_is_confirmation_gated_without_leaking_a_flow(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        for entity_id, value in (
            ("sensor.indoor_temp", "22"),
            ("sensor.outdoor_temp", "12"),
            ("sensor.indoor_humidity", "55"),
        ):
            hass.states.async_set(entity_id, value)
        data = MagicMock()
        data.store.get_pending_approvals.return_value = []
        data.store.async_save = AsyncMock()
        before = {entry.entry_id for entry in hass.config_entries.async_entries("mold_indicator")}
        content, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "mold_indicator",
                "flow_steps": [{
                    "step_id": "user",
                    "data": {
                        "name": "Awaiting Approval",
                        "indoor_temp_sensor": "sensor.indoor_temp",
                        "indoor_humidity_sensor": "sensor.indoor_humidity",
                        "outdoor_temp_sensor": "sensor.outdoor_temp",
                        "calibration_factor": 2.0,
                    },
                }],
            },
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "pending_approval", content
        assert {entry.entry_id for entry in hass.config_entries.async_entries("mold_indicator")} == before
        assert not hass.config_entries.flow.async_progress_by_handler("mold_indicator")
        data.store.set_pending_approvals.assert_called_once()

    async def test_integration_specific_final_rejection_creates_nothing(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        for entity_id, value in (
            ("sensor.indoor_temp", "22"),
            ("sensor.outdoor_temp", "12"),
            ("sensor.indoor_humidity", "55"),
        ):
            hass.states.async_set(entity_id, value)
        before = {entry.entry_id for entry in hass.config_entries.async_entries("mold_indicator")}
        content, outcome, _ = await _call(
            "create_helper",
            {
                "helper_type": "mold_indicator",
                "flow_steps": [{
                    "step_id": "user",
                    "data": {
                        "name": "Invalid Calibration",
                        "indoor_temp_sensor": "sensor.indoor_temp",
                        "indoor_humidity_sensor": "sensor.indoor_humidity",
                        "outdoor_temp_sensor": "sensor.outdoor_temp",
                        "calibration_factor": 0.0,
                    },
                }],
            },
            _access_key(), hass,
        )
        assert outcome == "invalid_request"
        assert "calibration_is_zero" in content["content"][0]["text"]
        assert {entry.entry_id for entry in hass.config_entries.async_entries("mold_indicator")} == before
        assert not hass.config_entries.flow.async_progress_by_handler("mold_indicator")

    async def test_otp_is_deliberately_excluded(self, hass: HomeAssistant, hass_admin_user):
        content, outcome, _ = await _call(
            "create_helper",
            {"helper_type": "otp", "flow_steps": []},
            _access_key(), hass,
        )
        assert outcome == "invalid_request"
        assert "live confirmation code" in content["content"][0]["text"]


class TestCreateHelperDuplicateName:
    async def test_same_name_is_refused_with_the_existing_id(self, hass, helper_env, hass_admin_user):
        first = _json((await _call("create_helper", {"helper_type": "input_boolean", "config": {"name": "Guest mode"}},
                                   _access_key(), hass))[0])
        content, outcome, _ = await _call("create_helper", {"helper_type": "input_boolean", "config": {"name": "guest MODE"}},
                                          _access_key(), hass)
        assert outcome == "invalid_request"
        assert first["helper"]["id"] in content["content"][0]["text"] and "edit_helper" in content["content"][0]["text"]
        _, outcome, _ = await _call("create_helper", {"helper_type": "input_boolean", "config": {"name": "Holiday mode"}},
                                    _access_key(), hass)
        assert outcome == "allowed"

    async def test_a_hidden_same_name_helper_is_not_revealed(self, hass, helper_env, hass_admin_user):
        first = _json((await _call("create_helper", {"helper_type": "input_boolean", "config": {"name": "Vault"}},
                                   _access_key(), hass))[0])
        hidden = f"input_boolean.{first['helper']['id']}"
        tree = PermissionTree(domains={"input_boolean": PermissionNode(state="GREEN")},
                              entities={hidden: PermissionNode(state="RED")})
        content, outcome, _ = await _call("create_helper", {"helper_type": "input_boolean", "config": {"name": "Vault"}},
                                          _access_key(permissions=tree), hass)
        assert outcome == "allowed"
        assert first["helper"]["id"] != _json(content)["helper"]["id"]


class TestEditHelperKeepsUnnamedFields:
    """HA rebuilds most storage helpers from the update alone; edits must not reset fields."""

    async def _stored(self, hass, helper_type, helper_id):
        from custom_components.phoenix_mcp.tools.helper import _read_helper_config
        return await _read_helper_config(hass, helper_type, helper_id)

    async def test_name_only_edit_keeps_icon_and_initial(self, hass, helper_env, hass_admin_user):
        created = _json((await _call("create_helper", {"helper_type": "input_boolean", "config": {
            "name": "Guest", "icon": "mdi:sofa", "initial": True}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        _, outcome, _ = await _call("edit_helper", {
            "helper_type": "input_boolean", "helper_id": hid, "config": {"name": "Visitor"}}, _access_key(), hass)
        assert outcome == "allowed"
        stored = await self._stored(hass, "input_boolean", hid)
        assert stored == {"id": hid, "name": "Visitor", "icon": "mdi:sofa", "initial": True}

    async def test_null_clears_an_optional_field(self, hass, helper_env, hass_admin_user):
        created = _json((await _call("create_helper", {"helper_type": "input_boolean", "config": {
            "name": "Guest", "icon": "mdi:sofa"}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        _, outcome, _ = await _call("edit_helper", {
            "helper_type": "input_boolean", "helper_id": hid, "config": {"icon": None}}, _access_key(), hass)
        assert outcome == "allowed"
        stored = await self._stored(hass, "input_boolean", hid)
        assert stored == {"id": hid, "name": "Guest"}

    async def test_partial_input_number_edit_keeps_its_required_range(self, hass, hass_admin_user):
        assert await async_setup_component(hass, "input_number", {"input_number": {}})
        tree = PermissionTree(domains={"input_number": PermissionNode(state="GREEN")})
        created = _json((await _call("create_helper", {"helper_type": "input_number", "config": {
            "name": "Target", "min": 5, "max": 30, "step": 0.5}}, _access_key(permissions=tree), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        _, outcome, _ = await _call("edit_helper", {
            "helper_type": "input_number", "helper_id": hid, "config": {"name": "Setpoint"}},
            _access_key(permissions=tree), hass)
        assert outcome == "allowed"
        stored = await self._stored(hass, "input_number", hid)
        assert stored["name"] == "Setpoint"
        assert (stored["min"], stored["max"], stored["step"]) == (5, 30, 0.5)

    async def test_schedule_rename_keeps_its_time_blocks(self, hass, hass_admin_user):
        assert await async_setup_component(hass, "schedule", {"schedule": {}})
        created = _json((await _call("create_helper", {"helper_type": "schedule", "config": {
            "name": "Heating", "monday": [{"from": "07:00:00", "to": "09:00:00"}]}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        _, outcome, _ = await _call("edit_helper", {
            "helper_type": "schedule", "helper_id": hid, "config": {"name": "Heating plan"}}, _access_key(), hass)
        assert outcome == "allowed"
        stored = await self._stored(hass, "schedule", hid)
        assert stored["name"] == "Heating plan"
        assert stored["monday"] == [{"from": "07:00:00", "to": "09:00:00"}]
        # Removing the entity cancels its next-transition timer before teardown.
        await _call("delete_helper", {"helper_type": "schedule", "helper_id": hid}, _access_key(), hass)
        await hass.async_block_till_done()

    async def test_approval_diff_shows_the_resulting_whole_item(self, hass, helper_env, hass_admin_user):
        from custom_components.phoenix_mcp.tools.helper import _build_diff_edit_helper
        created = _json((await _call("create_helper", {"helper_type": "input_boolean", "config": {
            "name": "Guest", "icon": "mdi:sofa"}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        current = await self._stored(hass, "input_boolean", hid)
        diff = _build_diff_edit_helper(
            {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "Visitor"}},
            _access_key(), hass, current)
        assert json.loads(diff["before"]) == {"id": hid, "name": "Guest", "icon": "mdi:sofa"}
        assert json.loads(diff["after"]) == {"id": hid, "name": "Visitor", "icon": "mdi:sofa"}


class TestEditDeleteHelper:
    async def test_delete_diff_uses_live_friendly_name(self, hass, helper_env, hass_admin_user):
        created = _json((await _call(
            "create_helper",
            {"helper_type": "input_boolean", "config": {"name": "Approval helper"}},
            _access_key(), hass,
        ))[0])
        await hass.async_block_till_done()
        diff = await _build_diff_delete_helper(
            {
                "helper_type": "input_boolean",
                "helper_id": created["helper"]["id"],
            },
            _access_key(), hass,
        )
        assert diff["target"]["label"] == "Approval helper"
        assert diff["preview"]["relationships"]["consumer_count"] == 0

    async def test_edit(self, hass, helper_env, hass_admin_user):
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "A"}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()  # let the helper entity register
        content, outcome, _ = await _call(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "B"}}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["helper"]["name"] == "B"
        await hass.async_block_till_done()
        # Registry integrity: the same id still maps to a single live entity, whose
        # state now reflects the edit (not a duplicate or orphaned entry).
        from homeassistant.helpers import entity_registry as er
        entity_id = er.async_get(hass).async_get_entity_id("input_boolean", "input_boolean", hid)
        assert entity_id is not None
        assert hass.states.get(entity_id).attributes.get("friendly_name") == "B"

    async def test_delete(self, hass, helper_env, hass_admin_user):
        from homeassistant.helpers import entity_registry as er
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Temp"}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()  # let the helper entity register
        entity_id = er.async_get(hass).async_get_entity_id("input_boolean", "input_boolean", hid)
        assert entity_id is not None
        _, outcome, _ = await _call(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": hid}, _access_key(), hass)
        assert outcome == "allowed"
        await hass.async_block_till_done()
        # Registry integrity: the entry and its live state are both gone.
        assert er.async_get(hass).async_get_entity_id("input_boolean", "input_boolean", hid) is None
        assert hass.states.get(entity_id) is None

    async def test_edit_invalid_config_confirm_mode_rejected_before_pending(self, hass, helper_env, hass_admin_user):
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "A"}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        _, outcome, _ = await _call(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": hid, "config": "not-a-dict"},
            _access_key(cap_helper_write="confirm"), hass)
        assert outcome == "invalid_request"

    async def test_delete_unknown(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": "does_not_exist"}, _access_key(), hass)
        assert outcome == "not_found"

    async def test_edit_missing_not_found(self, hass, helper_env, hass_admin_user):
        # A non-existent helper is not_found regardless of scope (existence check).
        _, outcome, _ = await _call(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": "does_not_exist", "config": {"name": "X"}},
            _access_key(), hass)
        assert outcome == "not_found"

    async def test_edit_cap_only_not_entity_scoped(self, hass, helper_env, hass_admin_user):
        # Helper authoring is cap-gated, not entity-scoped.
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Owned"}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        unscoped = AccessKeyRecord(
            id=str(uuid.uuid4()), name="a", access_key_hash="x", created_at=utcnow(),
            created_by="u", permissions=PermissionTree(domains={}),
            cap_helper_write="allow", cap_registry_read="allow",
        )
        content, outcome, _ = await _call(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "Renamed"}},
            unscoped, hass)
        assert outcome == "allowed"
        assert _json(content)["helper"]["name"] == "Renamed"

    async def test_delete_cap_only_not_entity_scoped(self, hass, helper_env, hass_admin_user):
        # Helper deletion is cap-gated, not entity-scoped.
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Owned"}}, _access_key(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        unscoped = AccessKeyRecord(
            id=str(uuid.uuid4()), name="a", access_key_hash="x", created_at=utcnow(),
            created_by="u", permissions=PermissionTree(domains={}),
            cap_helper_write="allow", cap_registry_read="allow",
        )
        _, outcome, _ = await _call(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": hid}, unscoped, hass)
        assert outcome == "allowed"


class TestVersionCapture:
    """create/edit/delete_helper record version history.

    A real VersionStore is supplied (capture is a no-op when data is a MagicMock)
    so the read-before path that populates `before` for edit and delete is
    exercised end-to-end.
    """

    @staticmethod
    def _data():
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=versions,
        )
        return data, versions

    async def test_create_edit_delete_record_history(self, hass, helper_env, hass_admin_user):
        data, versions = self._data()
        access_key = _access_key()

        created = _json((await _call_tool(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "A"}},
            access_key, hass, data))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()

        await _call_tool(
            "edit_helper",
            {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "B"}},
            access_key, hass, data)
        await hass.async_block_till_done()

        await _call_tool(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": hid},
            access_key, hass, data)

        history = versions.list_for("helper", f"input_boolean:{hid}")
        assert [v.action for v in history] == ["delete", "edit", "create"]  # newest first
        delete_rec, edit_rec, create_rec = history

        assert create_rec.before is None
        assert create_rec.after == {"name": "A"}
        assert create_rec.access_key_name == access_key.name
        # edit_helper reads the prior config into `before`.
        assert edit_rec.before is not None and edit_rec.before.get("name") == "A"
        assert edit_rec.after == {"name": "B"}
        # delete_helper reads the prior config into `before`; no `after`.
        assert delete_rec.before is not None and delete_rec.before.get("name") == "B"
        assert delete_rec.after is None

    async def test_restore_existing_helper_reapplies_as_rollback(self, hass, helper_env, hass_admin_user):
        from custom_components.phoenix_mcp.mcp_view import async_restore_version

        data, versions = self._data()
        access_key = _access_key()
        created = _json((await _call_tool(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "A"}},
            access_key, hass, data))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        await _call_tool(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "B"}},
            access_key, hass, data)
        await hass.async_block_till_done()

        rkey = f"input_boolean:{hid}"
        create_ver = versions.list_for("helper", rkey)[-1]  # the create (name A)
        _r, outcome, _res = await async_restore_version(create_ver, "admin-9", hass, data)
        assert outcome == "allowed"

        latest = versions.list_for("helper", rkey)[0]
        assert latest.action == "rollback"
        assert latest.approved_by_user_id == "admin-9"
        assert latest.after.get("name") == "A"


class TestListHelpers:
    async def test_lists_accessible(self, hass, helper_env, hass_admin_user):
        await _call("create_helper", {"helper_type": "input_boolean", "config": {"name": "Listed"}}, _access_key(), hass)
        await hass.async_block_till_done()
        content, outcome, _ = await _call("list_helpers", {}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert any(h["helper_type"] == "input_boolean" and h["helper_id"] for h in body["helpers"])

    async def test_deny_without_cap(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call("list_helpers", {}, _access_key(cap_registry_read="deny"), hass)
        assert outcome == "denied"


class TestInputButtonLifecycle:
    """input_button follows the shared storage-helper CRUD contract."""

    async def test_crud_and_deleted_restore(self, hass, hass_admin_user):
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.mcp_view import async_restore_version

        assert await async_setup_component(hass, "input_button", {"input_button": {}})
        await hass.async_block_till_done()
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=versions,
        )
        access_key = _access_key()

        created_result, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "input_button",
                "config": {"name": "Doorbell test", "icon": "mdi:gesture-tap-button"},
            },
            access_key, hass, data,
        )
        assert outcome == "allowed"
        created = _json(created_result)["helper"]
        helper_id = created["id"]
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("input_button", "input_button", helper_id)
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Doorbell test"
        assert hass.states.get(entity_id).attributes["icon"] == "mdi:gesture-tap-button"

        listed_result, outcome, _ = await _call_tool(
            "list_helpers", {"helper_type": "input_button"}, access_key, hass, data,
        )
        assert outcome == "allowed"
        assert _json(listed_result)["helpers"] == [{
            "entity_id": entity_id,
            "helper_type": "input_button",
            "name": "Doorbell test",
            "helper_id": helper_id,
        }]

        edited_result, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "input_button",
                "helper_id": helper_id,
                "config": {"name": "Doorbell renamed", "icon": "mdi:radiobox-marked"},
            },
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert _json(edited_result)["helper"]["name"] == "Doorbell renamed"
        assert registry.async_get_entity_id("input_button", "input_button", helper_id) == entity_id
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Doorbell renamed"
        assert hass.states.get(entity_id).attributes["icon"] == "mdi:radiobox-marked"

        # Registry customizations the operator made outside the helper's own
        # configuration: a renamed entity ID and name/icon overrides.
        registry.async_update_entity(
            entity_id, new_entity_id="input_button.front_bell", name="Front bell", icon="mdi:bell",
        )
        entity_id = "input_button.front_bell"
        await hass.async_block_till_done()

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "input_button", "helper_id": helper_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert registry.async_get_entity_id("input_button", "input_button", helper_id) is None
        assert hass.states.get(entity_id) is None

        history = versions.list_for("helper", f"input_button:{helper_id}")
        assert [record.action for record in history] == ["delete", "edit", "create"]
        assert history[0].before["name"] == "Doorbell renamed"
        assert history[1].before["name"] == "Doorbell test"
        assert history[1].after["name"] == "Doorbell renamed"

        restored_result, outcome, _ = await async_restore_version(
            history[0], "admin-input-button", hass, data,
        )
        assert outcome == "allowed"
        restored = _json(restored_result)["helper"]
        restored_id = restored["id"]
        # The renamed helper returns under its original storage ID, so Home
        # Assistant reapplies the deleted entity's registry customizations.
        assert restored_id == helper_id
        assert restored["name"] == "Doorbell renamed"
        assert registry.async_get_entity_id("input_button", "input_button", restored_id) == entity_id
        entry = registry.async_get(entity_id)
        assert (entry.name, entry.icon) == ("Front bell", "mdi:bell")
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Front bell"
        restored_history = versions.list_for("helper", f"input_button:{restored_id}")
        assert restored_history[0].action == "rollback"
        assert restored_history[0].approved_by_user_id == "admin-input-button"

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "input_button", "helper_id": restored_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"

class TestZoneLifecycle:
    """Storage-zone CRUD stays scoped and approval-bound."""

    @staticmethod
    async def _setup(hass: HomeAssistant) -> None:
        assert await async_setup_component(
            hass,
            "zone",
            {
                "zone": [{
                    "name": "YAML test zone",
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "radius": 100,
                }]
            },
        )
        await hass.async_block_till_done()

    async def test_create_requires_zone_domain_write_before_approval(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from homeassistant.helpers import entity_registry as er

        await self._setup(hass)
        access_key = _access_key(
            cap_helper_write="confirm",
            permissions=PermissionTree(domains={}),
        )
        result, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "zone",
                "config": {
                    "name": "Unscoped zone",
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "radius": 100,
                },
            },
            access_key, hass, MagicMock(),
        )
        assert outcome == "denied"
        assert "entire zone domain" in result["content"][0]["text"]
        assert er.async_get(hass).async_get_entity_id(
            "zone", "zone", "unscoped_zone"
        ) is None

    async def test_create_rolls_back_entity_specific_scope_override(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from homeassistant.helpers import entity_registry as er

        await self._setup(hass)
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(),
            versions=versions,
        )
        access_key = _access_key(permissions=PermissionTree(
            domains={"zone": PermissionNode(state="GREEN")},
            entities={"zone.blocked_new_zone": PermissionNode(state="RED")},
        ))
        result, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "zone",
                "config": {
                    "name": "Blocked new zone",
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "radius": 100,
                },
            },
            access_key, hass, data,
        )
        assert outcome == "denied"
        assert "removed and nothing changed" in result["content"][0]["text"]
        assert er.async_get(hass).async_get_entity_id(
            "zone", "zone", "blocked_new_zone"
        ) is None
        assert versions.list_for("helper", "zone:blocked_new_zone") == []

    async def test_crud_scope_storage_filter_and_deleted_restore(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.mcp_view import async_restore_version

        await self._setup(hass)
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(),
            versions=versions,
        )
        access_key = _access_key()
        initial = {
            "name": "Scoped test zone",
            "latitude": 13.7563,
            "longitude": 100.5018,
            "radius": 125,
            "passive": True,
            "icon": "mdi:map-marker-radius",
        }

        unknown_create, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "zone",
                "config": {**initial, "unexpected": True},
            },
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "invalid_request"
        assert "Unknown zone configuration fields" in unknown_create["content"][0]["text"]

        created_result, outcome, _ = await _call_tool(
            "create_helper",
            {"helper_type": "zone", "config": initial},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        created = _json(created_result)["helper"]
        helper_id = created["id"]
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("zone", "zone", helper_id)
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["friendly_name"] == "Scoped test zone"
        assert state.attributes["latitude"] == initial["latitude"]
        assert state.attributes["longitude"] == initial["longitude"]

        listed_result, outcome, _ = await _call_tool(
            "list_helpers", {"helper_type": "zone"}, access_key, hass, data,
        )
        assert outcome == "allowed"
        listed = _json(listed_result)["helpers"]
        assert listed == [{
            "entity_id": entity_id,
            "helper_type": "zone",
            "name": "Scoped test zone",
            "helper_id": helper_id,
        }]
        assert all(row["name"] != "YAML test zone" for row in listed)
        assert all(row["entity_id"] != "zone.home" for row in listed)

        unknown_result, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "zone",
                "helper_id": helper_id,
                "config": {"name": "Bad", "unexpected": True},
            },
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "invalid_request"
        assert "Unknown zone configuration fields" in unknown_result["content"][0]["text"]

        unscoped = _access_key(permissions=PermissionTree(domains={}))
        _, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "zone",
                "helper_id": helper_id,
                "config": {"name": "Hidden"},
            },
            unscoped, hass, data,
        )
        assert outcome == "not_found"
        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "zone", "helper_id": helper_id},
            unscoped, hass, data,
        )
        assert outcome == "not_found"
        assert hass.states.get(entity_id) is not None

        edited_config = {
            "name": "Scoped zone renamed",
            "latitude": 13.75,
            "longitude": 100.52,
            "radius": 175,
            "passive": False,
            "icon": "mdi:map-marker-check",
        }
        edited_result, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "zone",
                "helper_id": helper_id,
                "config": edited_config,
            },
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert _json(edited_result)["helper"]["radius"] == 175.0
        assert registry.async_get_entity_id("zone", "zone", helper_id) == entity_id
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Scoped zone renamed"

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "zone", "helper_id": helper_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert registry.async_get_entity_id("zone", "zone", helper_id) is None
        assert hass.states.get(entity_id) is None

        history = versions.list_for("helper", f"zone:{helper_id}")
        assert [record.action for record in history] == ["delete", "edit", "create"]
        assert history[0].before["latitude"] == edited_config["latitude"]
        assert history[1].before["longitude"] == initial["longitude"]

        restored_result, outcome, _ = await async_restore_version(
            history[0], "admin-zone", hass, data,
        )
        assert outcome == "allowed"
        restored_id = _json(restored_result)["helper"]["id"]
        restored_entity_id = registry.async_get_entity_id("zone", "zone", restored_id)
        assert restored_entity_id is not None
        assert hass.states.get(restored_entity_id).attributes["latitude"] == edited_config["latitude"]
        assert versions.list_for("helper", f"zone:{restored_id}")[0].action == "rollback"

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "zone", "helper_id": restored_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"

    async def test_explicit_zone_list_reports_storage_failure(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from custom_components.phoenix_mcp.ws_dispatch import WsDispatchError

        await self._setup(hass)
        with patch(
            "custom_components.phoenix_mcp.tools.helper.async_ws_command",
            AsyncMock(side_effect=WsDispatchError("unavailable")),
        ):
            result, outcome, _ = await _call_tool(
                "list_helpers", {"helper_type": "zone"}, _access_key(), hass,
                PhoenixData(
                    store=mesa_off_store(), rate_limiter=MagicMock(),
                    audit=MagicMock(), versions=VersionStore(),
                ),
            )
        assert outcome == "invalid_request"
        assert "storage is unavailable" in result["content"][0]["text"]

    async def test_stale_confirmed_edit_is_refused(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from custom_components.phoenix_mcp.mcp_view import async_execute_approved_tool
        from custom_components.phoenix_mcp.ws_dispatch import async_ws_command

        class ApprovalStore:
            def __init__(self) -> None:
                self.pending: list[dict] = []
                self.async_lock = asyncio.Lock()
                self.async_save = AsyncMock()
                self.get_settings = mesa_off_store().get_settings

            def get_pending_approvals(self) -> list[dict]:
                return self.pending

            def set_pending_approvals(self, value: list[dict]) -> None:
                self.pending = value

        await self._setup(hass)
        store = ApprovalStore()
        data = PhoenixData(
            store=store, rate_limiter=MagicMock(), audit=MagicMock(),
            versions=VersionStore(),
        )
        allow_access_key = _access_key()
        created_result, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "zone",
                "config": {
                    "name": "Approval zone",
                    "latitude": 10.0,
                    "longitude": 20.0,
                    "radius": 100,
                },
            },
            allow_access_key, hass, data,
        )
        assert outcome == "allowed"
        helper_id = _json(created_result)["helper"]["id"]

        confirm_access_key = _access_key(cap_helper_write="confirm")
        pending_result, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "zone",
                "helper_id": helper_id,
                "config": {"name": "Approved name"},
            },
            confirm_access_key, hass, data,
        )
        assert outcome == "pending_approval", pending_result
        approved_args = store.pending[0]["args"]

        await async_ws_command(
            hass,
            "zone/update",
            {"zone_id": helper_id, "radius": 250},
        )
        executed = await async_execute_approved_tool(
            "edit_helper", approved_args, confirm_access_key, hass, data,
        )
        assert executed[1] == "invalid_request"
        assert "changed after the request was reviewed" in executed[0]["content"][0]["text"]
        current = await async_ws_command(hass, "zone/list", {})
        item = next(row for row in current if row["id"] == helper_id)
        assert item["name"] == "Approval zone"
        assert item["radius"] == 250.0

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "zone", "helper_id": helper_id},
            allow_access_key, hass, data,
        )
        assert outcome == "allowed"


class TestTagLifecycle:
    """Tag CRUD protects scope and treats scan data as read-only evidence."""

    @staticmethod
    async def _setup(hass: HomeAssistant) -> None:
        assert await async_setup_component(hass, "tag", {"tag": {}})
        await hass.async_block_till_done()

    async def test_create_requires_tag_id_and_domain_write_before_approval(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        await self._setup(hass)
        data = MagicMock()
        missing_id, outcome, _ = await _call_tool(
            "create_helper",
            {"helper_type": "tag", "config": {"name": "Missing id"}},
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "invalid_request"
        assert "requires a non-empty string tag_id" in missing_id["content"][0]["text"]

        forged_scan, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "tag",
                "config": {"tag_id": "forged", "last_scanned": "2026-08-22"},
            },
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "invalid_request"
        assert "Unknown tag configuration fields" in forged_scan["content"][0]["text"]

        unscoped, outcome, _ = await _call_tool(
            "create_helper",
            {"helper_type": "tag", "config": {"tag_id": "unscoped-tag"}},
            _access_key(
                cap_helper_write="confirm",
                permissions=PermissionTree(domains={}),
            ),
            hass, data,
        )
        assert outcome == "denied"
        assert "entire tag domain" in unscoped["content"][0]["text"]

    async def test_create_rolls_back_entity_specific_scope_override(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from homeassistant.helpers import entity_registry as er

        await self._setup(hass)
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(),
            versions=versions,
        )
        access_key = _access_key(permissions=PermissionTree(
            domains={"tag": PermissionNode(state="GREEN")},
            entities={"tag.blocked_new_tag": PermissionNode(state="RED")},
        ))
        result, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "tag",
                "config": {
                    "tag_id": "blocked-new-tag",
                    "name": "Blocked new tag",
                },
            },
            access_key, hass, data,
        )
        assert outcome == "denied"
        assert "removed and nothing changed" in result["content"][0]["text"]
        assert er.async_get(hass).async_get_entity_id(
            "tag", "tag", "blocked-new-tag"
        ) is None
        assert versions.list_for("helper", "tag:blocked-new-tag") == []

    async def test_confirmed_create_rechecks_tag_id_absence(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from custom_components.phoenix_mcp.mcp_view import async_execute_approved_tool
        from custom_components.phoenix_mcp.ws_dispatch import async_ws_command

        class ApprovalStore:
            def __init__(self) -> None:
                self.pending: list[dict] = []
                self.async_lock = asyncio.Lock()
                self.async_save = AsyncMock()
                self.get_settings = mesa_off_store().get_settings

            def get_pending_approvals(self) -> list[dict]:
                return self.pending

            def set_pending_approvals(self, value: list[dict]) -> None:
                self.pending = value

        await self._setup(hass)
        store = ApprovalStore()
        data = PhoenixData(
            store=store, rate_limiter=MagicMock(), audit=MagicMock(),
            versions=VersionStore(),
        )
        access_key = _access_key(cap_helper_write="confirm")
        args = {
            "helper_type": "tag",
            "config": {"tag_id": "approval-race-tag", "name": "Reviewed name"},
        }
        _, outcome, _ = await _call_tool(
            "create_helper", args, access_key, hass, data,
        )
        assert outcome == "pending_approval"
        approved_args = store.pending[0]["args"]

        await async_ws_command(
            hass, "tag/create",
            {"tag_id": "approval-race-tag", "name": "External name"},
        )
        executed = await async_execute_approved_tool(
            "create_helper", approved_args, access_key, hass, data,
        )
        assert executed[1] == "invalid_request"
        assert "tag_id is unavailable" in executed[0]["content"][0]["text"]
        items = await async_ws_command(hass, "tag/list", {})
        item = next(row for row in items if row["id"] == "approval-race-tag")
        assert item["name"] == "External name"

        await async_ws_command(
            hass, "tag/delete", {"tag_id": "approval-race-tag"}
        )

    async def test_explicit_tag_list_reports_storage_failure(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from custom_components.phoenix_mcp.ws_dispatch import WsDispatchError

        await self._setup(hass)
        with patch(
            "custom_components.phoenix_mcp.tools.helper.async_ws_command",
            AsyncMock(side_effect=WsDispatchError("unavailable")),
        ):
            result, outcome, _ = await _call_tool(
                "list_helpers", {"helper_type": "tag"}, _access_key(), hass,
                PhoenixData(
                    store=mesa_off_store(), rate_limiter=MagicMock(),
                    audit=MagicMock(), versions=VersionStore(),
                ),
            )
        assert outcome == "invalid_request"
        assert "storage is unavailable" in result["content"][0]["text"]

    async def test_crud_duplicate_precheck_and_deleted_restore(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.mcp_view import async_restore_version

        await self._setup(hass)
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(),
            versions=versions,
        )
        access_key = _access_key()
        initial = {
            "tag_id": "phoenix-tag-lifecycle",
            "name": "Phoenix tag lifecycle",
            "description": "Disposable lifecycle tag",
        }
        created_result, outcome, _ = await _call_tool(
            "create_helper", {"helper_type": "tag", "config": initial},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        created = _json(created_result)["helper"]
        assert created["id"] == initial["tag_id"]
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("tag", "tag", initial["tag_id"])
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None
        assert hass.states.get(entity_id).attributes["friendly_name"] == initial["name"]

        unknown_edit, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "tag", "helper_id": initial["tag_id"],
                "config": {"device_id": "forged-scanner"},
            },
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "invalid_request"
        assert "Unknown tag configuration fields" in unknown_edit["content"][0]["text"]

        listed_result, outcome, _ = await _call_tool(
            "list_helpers", {"helper_type": "tag"}, access_key, hass, data,
        )
        assert outcome == "allowed"
        assert _json(listed_result)["helpers"] == [{
            "entity_id": entity_id,
            "helper_type": "tag",
            "name": initial["name"],
            "helper_id": initial["tag_id"],
        }]

        duplicate, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "tag",
                "config": {"tag_id": initial["tag_id"], "name": "Hijacked"},
            },
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "invalid_request"
        assert "tag_id is unavailable" in duplicate["content"][0]["text"]
        assert hass.states.get(entity_id).attributes["friendly_name"] == initial["name"]

        unscoped = _access_key(permissions=PermissionTree(domains={}))
        _, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "tag", "helper_id": initial["tag_id"],
                "config": {"name": "Hidden"},
            },
            unscoped, hass, data,
        )
        assert outcome == "not_found"
        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "tag", "helper_id": initial["tag_id"]},
            unscoped, hass, data,
        )
        assert outcome == "not_found"

        edited_config = {
            "name": "Phoenix tag renamed",
            "description": "Updated description",
        }
        edited_result, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "tag", "helper_id": initial["tag_id"],
                "config": edited_config,
            },
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert _json(edited_result)["helper"]["description"] == "Updated description"
        assert registry.async_get_entity_id("tag", "tag", initial["tag_id"]) == entity_id
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Phoenix tag renamed"

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "tag", "helper_id": initial["tag_id"]},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert registry.async_get_entity_id("tag", "tag", initial["tag_id"]) is None
        assert hass.states.get(entity_id) is None

        history = versions.list_for("helper", f"tag:{initial['tag_id']}")
        assert [record.action for record in history] == ["delete", "edit", "create"]
        assert history[0].before["description"] == "Updated description"
        assert history[1].before["description"] == initial["description"]

        restored_result, outcome, _ = await async_restore_version(
            history[0], "admin-tag", hass, data,
        )
        assert outcome == "allowed"
        restored = _json(restored_result)["helper"]
        assert restored["id"] == initial["tag_id"]
        restored_entity_id = registry.async_get_entity_id(
            "tag", "tag", initial["tag_id"]
        )
        assert restored_entity_id is not None
        assert hass.states.get(restored_entity_id).attributes["friendly_name"] == "Phoenix tag renamed"
        assert versions.list_for(
            "helper", f"tag:{initial['tag_id']}"
        )[0].action == "rollback"

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "tag", "helper_id": initial["tag_id"]},
            access_key, hass, data,
        )
        assert outcome == "allowed"

    async def test_scan_metadata_does_not_stale_approval_but_config_drift_does(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from homeassistant.components.tag import async_scan_tag
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.mcp_view import (
            async_execute_approved_tool, async_restore_version,
        )
        from custom_components.phoenix_mcp.ws_dispatch import async_ws_command

        class ApprovalStore:
            def __init__(self) -> None:
                self.pending: list[dict] = []
                self.async_lock = asyncio.Lock()
                self.async_save = AsyncMock()
                self.get_settings = mesa_off_store().get_settings

            def get_pending_approvals(self) -> list[dict]:
                return self.pending

            def set_pending_approvals(self, value: list[dict]) -> None:
                self.pending = value

        await self._setup(hass)
        store = ApprovalStore()
        data = PhoenixData(
            store=store, rate_limiter=MagicMock(), audit=MagicMock(),
            versions=VersionStore(),
        )
        allow_access_key = _access_key()
        tag_id = "phoenix-tag-approval"
        _, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "tag",
                "config": {"tag_id": tag_id, "name": "Approval tag"},
            },
            allow_access_key, hass, data,
        )
        assert outcome == "allowed"

        confirm_access_key = _access_key(cap_helper_write="confirm")
        _, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "tag", "helper_id": tag_id,
                "config": {"name": "After scan"},
            },
            confirm_access_key, hass, data,
        )
        assert outcome == "pending_approval"
        approved_args = store.pending.pop()["args"]
        await async_scan_tag(hass, tag_id, "scanner-device")
        executed = await async_execute_approved_tool(
            "edit_helper", approved_args, confirm_access_key, hass, data,
        )
        assert executed[1] == "allowed"

        _, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "tag", "helper_id": tag_id,
                "config": {"name": "Should not land"},
            },
            confirm_access_key, hass, data,
        )
        assert outcome == "pending_approval"
        stale_args = store.pending.pop()["args"]
        await async_ws_command(
            hass, "tag/update",
            {"tag_id": tag_id, "description": "External change"},
        )
        executed = await async_execute_approved_tool(
            "edit_helper", stale_args, confirm_access_key, hass, data,
        )
        assert executed[1] == "invalid_request"
        assert "changed after the request was reviewed" in executed[0]["content"][0]["text"]

        _, outcome, _ = await _call_tool(
            "delete_helper", {"helper_type": "tag", "helper_id": tag_id},
            allow_access_key, hass, data,
        )
        assert outcome == "allowed"
        deleted = data.versions.list_for("helper", f"tag:{tag_id}")[0]
        assert deleted.before["device_id"] == "scanner-device"
        restored_result, outcome, _ = await async_restore_version(
            deleted, "admin-tag-scan", hass, data,
        )
        assert outcome == "allowed"
        assert _json(restored_result)["helper"]["id"] == tag_id
        restored_entity_id = er.async_get(hass).async_get_entity_id(
            "tag", "tag", tag_id
        )
        assert restored_entity_id is not None
        restored_state = hass.states.get(restored_entity_id)
        assert restored_state is not None
        assert restored_state.state == "unknown"
        assert restored_state.attributes["last_scanned_by_device_id"] is None

        _, outcome, _ = await _call_tool(
            "delete_helper", {"helper_type": "tag", "helper_id": tag_id},
            allow_access_key, hass, data,
        )
        assert outcome == "allowed"


class TestPersonLifecycle:
    """Person CRUD is storage-only and keeps private relationships private."""

    @staticmethod
    async def _setup(hass: HomeAssistant) -> None:
        assert await async_setup_component(
            hass, "person",
            {"person": [{"id": "yaml_person", "name": "YAML person"}]},
        )
        hass.states.async_set("device_tracker.phone", "home")
        hass.states.async_set("device_tracker.watch", "not_home")
        await hass.async_block_till_done()

    async def test_precheck_and_relationship_scope_before_approval(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        await self._setup(hass)
        data = MagicMock()
        for forbidden in ("user_id", "picture"):
            result, outcome, _ = await _call_tool(
                "create_helper",
                {"helper_type": "person", "config": {
                    "name": "Private", forbidden: "secret",
                }},
                _access_key(cap_helper_write="confirm"), hass, data,
            )
            assert outcome == "invalid_request"
            assert "Unknown person configuration fields" in result["content"][0]["text"]
        malformed, outcome, _ = await _call_tool(
            "create_helper",
            {"helper_type": "person", "config": {
                "name": "Duplicate",
                "device_trackers": ["device_tracker.phone", "device_tracker.phone"],
            }},
            _access_key(cap_helper_write="confirm"), hass, data,
        )
        assert outcome == "invalid_request"
        assert "duplicate-free" in malformed["content"][0]["text"]
        unscoped, outcome, _ = await _call_tool(
            "create_helper",
            {"helper_type": "person", "config": {
                "name": "Hidden tracker",
                "device_trackers": ["device_tracker.phone"],
            }},
            _access_key(cap_helper_write="confirm", permissions=PermissionTree(domains={
                "person": PermissionNode(state="GREEN"),
                "device_tracker": PermissionNode(state="RED"),
            })),
            hass, data,
        )
        assert outcome == "denied"
        assert "out-of-scope device tracker" in unscoped["content"][0]["text"]

    async def test_private_projection_merge_scope_delete_and_restore(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.mcp_view import async_restore_version
        from custom_components.phoenix_mcp.ws_dispatch import async_ws_command

        await self._setup(hass)
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(),
            versions=versions,
        )
        access_key = _access_key()
        linked_user = await hass.auth.async_create_user("Linked person user")
        raw = await async_ws_command(hass, "person/create", {
            "name": "Private person",
            "user_id": linked_user.id,
            "device_trackers": ["device_tracker.phone"],
            "picture": "/local/private-person.png",
        })
        helper_id = raw["id"]
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("person", "person", helper_id)
        assert entity_id is not None
        listed_result, outcome, _ = await _call_tool(
            "list_helpers", {"helper_type": "person"}, access_key, hass, data,
        )
        assert outcome == "allowed"
        listed = _json(listed_result)["helpers"]
        assert listed == [{
            "entity_id": entity_id, "helper_type": "person",
            "name": "Private person", "helper_id": helper_id,
            "device_trackers": ["device_tracker.phone"],
            "tracker_count": 1, "has_user_link": True,
        }]
        assert linked_user.id not in json.dumps(listed_result)
        assert "private-person.png" not in json.dumps(listed_result)
        assert all(row["helper_id"] != "yaml_person" for row in listed)
        edited_result, outcome, _ = await _call_tool(
            "edit_helper",
            {"helper_type": "person", "helper_id": helper_id,
             "config": {"name": "Private person renamed"}},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        edited = _json(edited_result)["helper"]
        assert edited["device_trackers"] == ["device_tracker.phone"]
        assert edited["has_user_link"] is True
        assert "user_id" not in edited and "picture" not in edited
        stored = (await async_ws_command(hass, "person/list", {}))["storage"][0]
        assert stored["user_id"] == linked_user.id
        assert stored["picture"] == "/local/private-person.png"
        assert stored["device_trackers"] == ["device_tracker.phone"]
        hidden_access_key = _access_key(permissions=PermissionTree(domains={
            "person": PermissionNode(state="GREEN"),
            "device_tracker": PermissionNode(state="RED"),
        }))
        hidden_list, outcome, _ = await _call_tool(
            "list_helpers", {"helper_type": "person"}, hidden_access_key, hass, data,
        )
        assert outcome == "allowed"
        assert _json(hidden_list)["helpers"] == []
        denied, outcome, _ = await _call_tool(
            "delete_helper", {"helper_type": "person", "helper_id": helper_id},
            hidden_access_key, hass, data,
        )
        assert outcome == "not_found"
        assert linked_user.id not in json.dumps(denied)
        _, outcome, _ = await _call_tool(
            "delete_helper", {"helper_type": "person", "helper_id": helper_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert registry.async_get_entity_id("person", "person", helper_id) is None
        deleted = versions.list_for("helper", f"person:{helper_id}")[0]
        assert deleted.before["has_user_link"] is True
        assert deleted.before["has_picture"] is True
        assert linked_user.id not in json.dumps(deleted.before)
        assert "private-person.png" not in json.dumps(deleted.before)
        restored_result, outcome, _ = await async_restore_version(
            deleted, "admin-person", hass, data,
        )
        assert outcome == "allowed"
        restored = _json(restored_result)["helper"]
        assert restored["id"] == helper_id
        assert restored["device_trackers"] == ["device_tracker.phone"]
        assert restored["has_user_link"] is False
        assert restored["has_picture"] is False
        assert registry.async_get_entity_id("person", "person", helper_id) is not None
        _, outcome, _ = await _call_tool(
            "delete_helper", {"helper_type": "person", "helper_id": helper_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"

    async def test_private_user_link_drift_invalidates_approval_without_leaking_id(
        self, hass: HomeAssistant, hass_admin_user,
    ):
        from custom_components.phoenix_mcp.mcp_view import async_execute_approved_tool
        from custom_components.phoenix_mcp.ws_dispatch import async_ws_command

        class ApprovalStore:
            def __init__(self) -> None:
                self.pending: list[dict] = []
                self.async_lock = asyncio.Lock()
                self.async_save = AsyncMock()
                self.get_settings = mesa_off_store().get_settings

            def get_pending_approvals(self) -> list[dict]:
                return self.pending

            def set_pending_approvals(self, value: list[dict]) -> None:
                self.pending = value

        await self._setup(hass)
        first = await hass.auth.async_create_user("First linked user")
        second = await hass.auth.async_create_user("Second linked user")
        raw = await async_ws_command(
            hass, "person/create", {"name": "Approval person", "user_id": first.id},
        )
        store = ApprovalStore()
        data = PhoenixData(
            store=store, rate_limiter=MagicMock(), audit=MagicMock(),
            versions=VersionStore(),
        )
        access_key = _access_key(cap_helper_write="confirm")
        _, outcome, _ = await _call_tool(
            "edit_helper",
            {"helper_type": "person", "helper_id": raw["id"],
             "config": {"name": "Reviewed rename"}},
            access_key, hass, data,
        )
        assert outcome == "pending_approval"
        approved_args = store.pending[0]["args"]
        assert first.id not in json.dumps(approved_args)
        await async_ws_command(
            hass, "person/update", {"person_id": raw["id"], "user_id": second.id},
        )
        executed = await async_execute_approved_tool(
            "edit_helper", approved_args, access_key, hass, data,
        )
        assert executed[1] == "invalid_request"
        text = executed[0]["content"][0]["text"]
        assert "changed after the request was reviewed" in text
        assert first.id not in text and second.id not in text
        stored = (await async_ws_command(hass, "person/list", {}))["storage"][0]
        assert stored["name"] == "Approval person"
        assert stored["user_id"] == second.id
        await async_ws_command(hass, "person/delete", {"person_id": raw["id"]})


class TestScheduleLifecycle:
    """Schedule CRUD validates its schema, and a deleted schedule restores."""

    async def test_schema_crud_and_deleted_restore(self, hass, hass_admin_user):
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.mcp_view import async_restore_version

        assert await async_setup_component(hass, "schedule", {"schedule": {}})
        await hass.async_block_till_done()
        versions = VersionStore()
        data = PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=versions,
        )
        access_key = _access_key()
        initial_config = {
            "name": "Weekly test",
            "icon": "mdi:calendar-clock",
            "monday": [{
                "from": "08:00:00",
                "to": "09:30:00",
                "data": {"mode": "office", "enabled": True},
            }],
            "sunday": [{"from": "23:00:00", "to": "24:00:00"}],
        }
        created_result, outcome, _ = await _call_tool(
            "create_helper",
            {"helper_type": "schedule", "config": initial_config},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        created = _json(created_result)["helper"]
        helper_id = created["id"]
        assert created["monday"] == initial_config["monday"]
        assert created["sunday"] == initial_config["sunday"]
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("schedule", "schedule", helper_id)
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Weekly test"
        assert hass.states.get(entity_id).attributes["icon"] == "mdi:calendar-clock"

        listed_result, outcome, _ = await _call_tool(
            "list_helpers", {"helper_type": "schedule"}, access_key, hass, data,
        )
        assert outcome == "allowed"
        assert _json(listed_result)["helpers"] == [{
            "entity_id": entity_id,
            "helper_type": "schedule",
            "name": "Weekly test",
            "helper_id": helper_id,
        }]

        invalid_result, outcome, _ = await _call_tool(
            "create_helper",
            {
                "helper_type": "schedule",
                "config": {
                    "name": "Overlapping test",
                    "monday": [
                        {"from": "08:00:00", "to": "10:00:00"},
                        {"from": "09:00:00", "to": "11:00:00"},
                    ],
                },
            },
            access_key, hass, data,
        )
        assert outcome == "invalid_request"
        assert invalid_result["isError"] is True
        assert registry.async_get_entity_id(
            "schedule", "schedule", "overlapping_test"
        ) is None

        edited_config = {
            "name": "Weekly renamed",
            "icon": "mdi:calendar-refresh",
            "tuesday": [{
                "from": "12:15:00",
                "to": "13:45:00",
                "data": {"mode": "lunch", "priority": 2},
            }],
        }
        edited_result, outcome, _ = await _call_tool(
            "edit_helper",
            {
                "helper_type": "schedule",
                "helper_id": helper_id,
                "config": edited_config,
            },
            access_key, hass, data,
        )
        assert outcome == "allowed"
        edited = _json(edited_result)["helper"]
        assert edited["tuesday"] == edited_config["tuesday"]
        assert registry.async_get_entity_id("schedule", "schedule", helper_id) == entity_id
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Weekly renamed"
        assert hass.states.get(entity_id).attributes["icon"] == "mdi:calendar-refresh"

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "schedule", "helper_id": helper_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"
        assert registry.async_get_entity_id("schedule", "schedule", helper_id) is None
        assert hass.states.get(entity_id) is None

        history = versions.list_for("helper", f"schedule:{helper_id}")
        assert [record.action for record in history] == ["delete", "edit", "create"]
        assert history[0].before["tuesday"] == edited_config["tuesday"]
        assert history[1].before["monday"] == initial_config["monday"]
        # The edit named no monday blocks, so they survive it.
        assert {key: history[1].after[key] for key in edited_config} == edited_config
        assert history[1].after["monday"] == initial_config["monday"]

        restored_result, outcome, _ = await async_restore_version(
            history[0], "admin-schedule", hass, data,
        )
        assert outcome == "allowed"
        restored = _json(restored_result)["helper"]
        restored_id = restored["id"]
        assert restored_id == helper_id
        assert registry.async_get_entity_id("schedule", "schedule", restored_id) == entity_id
        assert restored["name"] == "Weekly renamed"
        assert restored["tuesday"] == edited_config["tuesday"]
        restored_history = versions.list_for("helper", f"schedule:{restored_id}")
        assert restored_history[0].action == "rollback"
        assert restored_history[0].approved_by_user_id == "admin-schedule"

        _, outcome, _ = await _call_tool(
            "delete_helper",
            {"helper_type": "schedule", "helper_id": restored_id},
            access_key, hass, data,
        )
        assert outcome == "allowed"


@pytest.mark.parametrize("kind,state", [("sensor", "{{ 7 }}"), ("binary_sensor", "{{ true }}")])
async def test_template_menu_inspection_and_creation(hass, kind, state):
    """Inspecting the real HA menu/forms, or preflighting a create, creates nothing."""
    assert await async_setup_component(hass, "homeassistant", {})
    access_key = _access_key(permissions=PermissionTree(domains={kind: PermissionNode(state="GREEN")}))
    content, outcome, _ = await _call("create_helper", {"helper_type": "template", "flow_steps": []}, access_key, hass)
    assert outcome == "allowed", content
    from custom_components.phoenix_mcp.const import TEMPLATE_RECOVERY_TYPES
    assert _json(content)["step"]["menu_options"] == sorted(TEMPLATE_RECOVERY_TYPES)
    steps = [{"step_id": "user", "data": {"next_step_id": kind}}]
    content, outcome, _ = await _call("create_helper", {"helper_type": "template", "flow_steps": steps}, access_key, hass)
    assert outcome == "allowed", content
    assert _json(content)["step"]["step_id"] == kind
    assert _json(content)["step"]["last_step"] is True
    assert not hass.config_entries.async_entries("template")
    assert not hass.config_entries.flow.async_progress_by_handler("template")
    steps.append({"step_id": kind, "data": {"name": "Menu Probe", "state": state}})
    from custom_components.phoenix_mcp.tools.helper import _prepare_config_flow_helper
    assert (await _prepare_config_flow_helper(hass, "template", steps, access_key))[0] == "ready"
    assert not hass.config_entries.async_entries("template")
    content, outcome, _ = await _call("create_helper", {"helper_type": "template", "flow_steps": steps}, access_key, hass)
    assert outcome == "allowed", content
    body = _json(content)
    assert body["entity_ids"] == [f"{kind}.menu_probe"]
    await hass.async_block_till_done()
    assert hass.states.get(f"{kind}.menu_probe").state == ("7" if kind == "sensor" else "on")
    assert len(hass.config_entries.async_entries("template")) == 1
    await hass.config_entries.async_remove(body["config_entry"]["entry_id"])


from custom_components.phoenix_mcp.const import TEMPLATE_RECOVERY_TYPES


@pytest.mark.parametrize("kind", sorted(TEMPLATE_RECOVERY_TYPES))
async def test_template_create_helper_covers_available_types(hass, kind):
    """Every supported Template menu type uses HA's actual creation schema."""
    assert await async_setup_component(hass, "homeassistant", {})
    from homeassistant.components.template.config_flow import TEMPLATE_TYPES
    assert kind in {str(item) for item in TEMPLATE_TYPES}
    access_key = _access_key(permissions=PermissionTree(domains={kind: PermissionNode(state="GREEN")}))
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.helpers import entity_registry as er
    flow = hass.config_entries.flow
    initial = await flow.async_init("template", context={"source": SOURCE_USER})
    form = await flow.async_configure(initial["flow_id"], {"next_step_id": kind})
    assert form.get("step_id") == kind
    defaults = {
        "name": f"Phoenix {kind}",
        "state": "{{ 1 }}",
        "event_type": "{{ 'probe' }}",
        "event_types": "{{ ['probe'] }}",
        "url": "{{ 'https://example.invalid/image' }}",
        "condition": "{{ 'sunny' }}",
        "humidity": "{{ 50 }}",
        "temperature": "{{ 20 }}",
        "latitude": "{{ 1 }}",
        "longitude": "{{ 1 }}",
        "installed_version": "{{ '1.0' }}",
        "latest_version": "{{ '1.0' }}",
        "min": 0,
        "max": 100,
        "step": 1,
        "options": "{{ ['one', 'two'] }}",
        "set_value": [],
        "on_action": [], "off_action": [],
        "turn_on": [], "turn_off": [],
        "lock": [], "unlock": [],
        "start": [],
        "open_cover": [], "close_cover": [],
    }
    values = {}
    for marker in form["data_schema"].schema:
        field = marker.schema
        if marker.__class__.__name__ == "Required":
            assert field in defaults, f"Add a real HA fixture value for required Template field {field!r}"
            values[field] = defaults[field]
    if kind == "cover":
        values.update({"open_cover": [], "close_cover": []})
    elif kind == "device_tracker":
        values.update({"latitude": "{{ 1 }}", "longitude": "{{ 1 }}"})
    elif kind == "update":
        values.update({
            "installed_version": "{{ '1.0' }}",
            "latest_version": "{{ '1.1' }}",
        })
    flow.async_abort(initial["flow_id"])
    steps = [
        {"step_id": "user", "data": {"next_step_id": kind}},
        {"step_id": kind, "data": values},
    ]
    content, outcome, _ = await _call(
        "create_helper", {"helper_type": "template", "flow_steps": steps}, access_key, hass,
    )
    assert outcome == "allowed", content
    body = _json(content)
    assert body["entity_ids"] and all(eid.startswith(f"{kind}.") for eid in body["entity_ids"])
    entry_id = body["config_entry"]["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None and entry.options["template_type"] == kind
    registry_entries = er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    assert len(registry_entries) == 1
    from custom_components.phoenix_mcp.template_recovery import template_snapshot, validate_snapshot
    snapshot = template_snapshot(hass, entry)
    validate_snapshot(snapshot)
    await hass.config_entries.async_remove(entry_id)
    runtime = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    from custom_components.phoenix_mcp.template_recovery import restore_template
    restored, restored_outcome, _ = await restore_template(snapshot, access_key, hass, runtime)
    assert restored_outcome == "allowed", (kind, restored)
    restored_body = _json(restored)
    restored_entry = hass.config_entries.async_get_entry(restored_body["entry_id"])
    assert restored_entry is not None
    assert dict(restored_entry.options) == snapshot["options"]
    restored_entities = er.async_entries_for_config_entry(er.async_get(hass), restored_entry.entry_id)
    assert len(restored_entities) == 1 and restored_entities[0].entity_id == snapshot["entities"][0]["entity_id"]
    await hass.config_entries.async_remove(restored_entry.entry_id)


async def test_template_helper_list_edit_delete_and_agent_restore(hass):
    """Template helpers use the public helper tools and owner-scoped recovery path."""
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp.template_recovery import template_snapshot

    assert await async_setup_component(hass, "homeassistant", {})
    access_key = _access_key(
        cap_integration_write="allow",
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}),
    )
    runtime = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    steps = [
        {"step_id": "user", "data": {"next_step_id": "sensor"}},
        {"step_id": "sensor", "data": {"name": "CRUD template", "state": "{{ 7 }}"}},
    ]
    result, outcome, _ = await _call_tool(
        "create_helper", {"helper_type": "template", "flow_steps": steps}, access_key, hass, runtime,
    )
    assert outcome == "allowed", result
    entry_id = _json(result)["config_entry"]["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    template_snapshot(hass, entry)
    helper_id = er.async_entries_for_config_entry(er.async_get(hass), entry_id)[0].unique_id

    result, outcome, _ = await _call_tool("list_helpers", {}, access_key, hass, runtime)
    assert outcome == "allowed"
    assert any(row["helper_type"] == "sensor" and row["helper_id"] == helper_id for row in _json(result)["helpers"])

    result, outcome, _ = await _call_tool(
        "edit_helper", {"helper_type": "sensor", "helper_id": helper_id, "config": {"state": "{{ 8 }}"}},
        access_key, hass, runtime,
    )
    assert outcome == "allowed", result
    assert hass.config_entries.async_get_entry(entry_id).options["state"] == "{{ 8 }}"
    deleted_snapshot = template_snapshot(hass, hass.config_entries.async_get_entry(entry_id))

    result, outcome, _ = await _call_tool(
        "delete_helper", {"helper_type": "sensor", "helper_id": helper_id}, access_key, hass, runtime,
    )
    assert outcome == "allowed", result
    recovery_id = json.loads(result["content"][0]["text"])["recovery_id"]
    assert hass.config_entries.async_get_entry(entry_id) is None

    receipt, outcome, _ = await _call_tool(
        "restore_configuration", {"recovery_id": recovery_id, "side": "before"}, access_key, hass, runtime,
    )
    assert outcome == "allowed", receipt
    bound = json.loads(receipt["content"][0]["text"])
    result, outcome, _ = await _call_tool(
        "restore_configuration", {
            "recovery_id": recovery_id, "side": "before",
            "snapshot_digest": bound["snapshot_digest"],
            "target_fingerprint": bound["target_fingerprint"],
        }, access_key, hass, runtime,
    )
    assert outcome == "allowed", result
    restored_id = json.loads(result["content"][0]["text"])["entry_id"]
    restored = hass.config_entries.async_get_entry(restored_id)
    assert restored is not None and dict(restored.options) == deleted_snapshot["options"]


@pytest.mark.parametrize("values", [{"next_step_id": "climate"}, {"next_step_id": "sensor", "extra": True}, {"next_step_id": []}, {}])
async def test_template_menu_refuses_unqualified_or_malformed_choice(hass, values):
    content, outcome, _ = await _call("create_helper", {"helper_type": "template", "flow_steps": [
        {"step_id": "user", "data": values},
    ]}, _access_key(), hass)
    assert outcome == "invalid_request", content
    assert not hass.config_entries.async_entries("template")
    assert not hass.config_entries.flow.async_progress_by_handler("template")


async def test_template_creation_rechecks_terminal_boundary(hass):
    from custom_components.phoenix_mcp.tools import helper
    prepared = False
    original_prepare = helper._prepare_config_flow_helper
    original_body = helper._flow_step_body

    async def prepare(*args):
        nonlocal prepared
        result = await original_prepare(*args)
        prepared = True
        return result

    def changed_body(*args):
        body = original_body(*args)
        if prepared and body and body["step"]["step_id"] == "sensor":
            body["step"]["last_step"] = False
        return body

    steps = [{"step_id": "user", "data": {"next_step_id": "sensor"}},
             {"step_id": "sensor", "data": {"name": "Boundary Probe", "state": "{{ 7 }}"}}]
    with patch.object(helper, "_prepare_config_flow_helper", side_effect=prepare), patch.object(
        helper, "_flow_step_body", side_effect=changed_body
    ):
        content, outcome, _ = await helper._execute_create_helper(
            {"helper_type": "template", "flow_steps": steps}, _access_key(), hass,
            PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock()),
        )
    assert outcome == "invalid_request", content
    assert "final boundary changed" in content["content"][0]["text"]
    assert not hass.config_entries.async_entries("template")
    assert not hass.config_entries.flow.async_progress_by_handler("template")


async def test_template_creation_authority_loss_before_final_submit(hass):
    from custom_components.phoenix_mcp.tools import helper
    steps = [{"step_id": "user", "data": {"next_step_id": "sensor"}},
             {"step_id": "sensor", "data": {"name": "Authority Probe", "state": "{{ 7 }}"}}]
    with patch("custom_components.phoenix_mcp.request_authority.check_request_authority",
               side_effect=[None, RuntimeError("Authority changed")]) as authority:
        content, outcome, _ = await helper._execute_create_helper(
            {"helper_type": "template", "flow_steps": steps}, _access_key(), hass,
            PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock()),
        )
    assert outcome == "invalid_request", content
    assert authority.call_count == 2
    assert not hass.config_entries.async_entries("template")
    assert not hass.config_entries.flow.async_progress_by_handler("template")


class TestHelperIconsAndRestoreCompleteness:
    """Icon values follow Core's schemas, and a restore reproduces its version or refuses."""

    @staticmethod
    def _data() -> PhoenixData:
        return PhoenixData(
            store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore(),
        )

    @staticmethod
    async def _zone_setup(hass: HomeAssistant) -> None:
        assert await async_setup_component(hass, "zone", {"zone": []})
        await hass.async_block_till_done()

    @pytest.mark.parametrize(("tool", "helper_type", "config", "message"), [
        ("edit_helper", "zone", {"icon": None}, "cannot remove a zone's stored icon"),
        ("edit_helper", "zone", {"icon": ""}, 'icon must be an icon name such as "mdi:sofa".'),
        ("create_helper", "input_boolean", {"name": "Guest", "icon": "sofa"}, 'such as "mdi:sofa".'),
        ("create_helper", "input_boolean", {"name": "Guest", "icon": None}, "omit it to use the default icon"),
        ("edit_helper", "input_boolean", {"icon": 5}, 'such as "mdi:sofa", or null to remove it.'),
        ("create_helper", "tag", {"tag_id": "abc", "icon": "mdi:nfc"}, "Tags have no stored icon; set_entity"),
        ("edit_helper", "person", {"icon": "mdi:account"}, "People have no stored icon; set_entity"),
    ])
    async def test_icon_values_are_refused_before_any_approval(
        self, hass: HomeAssistant, tool, helper_type, config, message,
    ):
        args = {"helper_type": helper_type, "config": config}
        if tool == "edit_helper":
            args["helper_id"] = "anything"
        data = self._data()
        result, outcome, _ = await _call_tool(tool, args, _access_key(cap_helper_write="confirm"), hass, data)
        assert outcome == "invalid_request"
        assert message in result["content"][0]["text"]
        data.store.async_save.assert_not_called()

    async def test_restoring_an_older_version_removes_fields_added_since(self, hass: HomeAssistant, helper_env, hass_admin_user):
        from custom_components.phoenix_mcp.mcp_view import async_restore_version
        from custom_components.phoenix_mcp.tools.helper import _read_helper_config

        data = self._data()
        created, _, _ = await _call_tool("create_helper", {
            "helper_type": "input_boolean", "config": {"name": "Away mode"},
        }, _access_key(), hass, data)
        helper_id = _json(created)["helper"]["id"]
        _, outcome, _ = await _call_tool("edit_helper", {
            "helper_type": "input_boolean", "helper_id": helper_id,
            "config": {"icon": "mdi:airplane", "initial": True},
        }, _access_key(), hass, data)
        assert outcome == "allowed"
        edit_record = data.versions.list_for("helper", f"input_boolean:{helper_id}")[0]
        assert edit_record.action == "edit" and "icon" not in edit_record.before

        result, outcome, _ = await async_restore_version(edit_record, "admin-1", hass, data, "before")
        assert outcome == "allowed", result
        stored = await _read_helper_config(hass, "input_boolean", helper_id)
        assert stored == {"id": helper_id, "name": "Away mode"}
        assert "icon" not in hass.states.get(f"input_boolean.{helper_id}").attributes

    async def test_zone_restore_refuses_an_icon_core_cannot_remove(self, hass: HomeAssistant, hass_admin_user):
        from custom_components.phoenix_mcp.mcp_view import async_restore_version
        from custom_components.phoenix_mcp.tools.helper import _read_helper_config

        await self._zone_setup(hass)
        data = self._data()
        created, _, _ = await _call_tool("create_helper", {"helper_type": "zone", "config": {
            "name": "Allotment", "latitude": 1.0, "longitude": 2.0, "radius": 50,
        }}, _access_key(), hass, data)
        zone_id = _json(created)["helper"]["id"]
        _, outcome, _ = await _call_tool("edit_helper", {
            "helper_type": "zone", "helper_id": zone_id, "config": {"icon": "mdi:sprout", "radius": 80},
        }, _access_key(), hass, data)
        assert outcome == "allowed"
        edit_record = data.versions.list_for("helper", f"zone:{zone_id}")[0]

        recovery_records = len(data.config_recovery.records())
        result, outcome, _ = await async_restore_version(edit_record, "admin-1", hass, data, "before")
        assert outcome == "invalid_request"
        assert "cannot remove the icon this zone now stores" in result["content"][0]["text"]
        stored = await _read_helper_config(hass, "zone", zone_id)
        assert (stored["icon"], stored["radius"]) == ("mdi:sprout", 80.0)
        assert len(data.config_recovery.records()) == recovery_records  # nothing was dispatched

        # An edit's after side holds only the fields it changed, here not the
        # icon, so it re-applies that change rather than replacing the zone.
        await _call_tool("edit_helper", {
            "helper_type": "zone", "helper_id": zone_id, "config": {"radius": 30},
        }, _access_key(), hass, data)
        radius_record = data.versions.list_for("helper", f"zone:{zone_id}")[0]
        assert radius_record.after == {"radius": 30}
        await _call_tool("edit_helper", {
            "helper_type": "zone", "helper_id": zone_id, "config": {"radius": 10},
        }, _access_key(), hass, data)
        result, outcome, _ = await async_restore_version(radius_record, "admin-1", hass, data, "after")
        assert outcome == "allowed", result
        stored = await _read_helper_config(hass, "zone", zone_id)
        assert (stored["name"], stored["icon"], stored["radius"]) == ("Allotment", "mdi:sprout", 30.0)

    async def test_tag_restore_refuses_a_description_core_cannot_remove(self, hass: HomeAssistant, hass_admin_user):
        from custom_components.phoenix_mcp.mcp_view import async_restore_version
        from custom_components.phoenix_mcp.tools.helper import _read_helper_config

        assert await async_setup_component(hass, "tag", {"tag": {}})
        data = self._data()
        _, outcome, _ = await _call_tool("create_helper", {
            "helper_type": "tag", "config": {"tag_id": "garden-gate", "name": "Garden gate"},
        }, _access_key(), hass, data)
        assert outcome == "allowed"
        await _call_tool("edit_helper", {
            "helper_type": "tag", "helper_id": "garden-gate", "config": {"description": "Back fence"},
        }, _access_key(), hass, data)
        edit_record = data.versions.list_for("helper", "tag:garden-gate")[0]
        result, outcome, _ = await async_restore_version(edit_record, "admin-1", hass, data, "before")
        assert outcome == "invalid_request"
        assert "cannot remove the description this tag now stores" in result["content"][0]["text"]
        assert (await _read_helper_config(hass, "tag", "garden-gate"))["description"] == "Back fence"

    async def test_a_renamed_zone_returns_under_its_original_id_and_entity(self, hass: HomeAssistant, hass_admin_user):
        from homeassistant.helpers import entity_registry as er
        from custom_components.phoenix_mcp.mcp_view import async_restore_version
        from custom_components.phoenix_mcp.tools.helper import _restored_helper_entity_id

        await self._zone_setup(hass)
        data = self._data()
        created, _, _ = await _call_tool("create_helper", {"helper_type": "zone", "config": {
            "name": "Office", "latitude": 1.0, "longitude": 2.0, "radius": 50,
        }}, _access_key(), hass, data)
        zone_id = _json(created)["helper"]["id"]
        await _call_tool("edit_helper", {
            "helper_type": "zone", "helper_id": zone_id, "config": {"name": "Studio"},
        }, _access_key(), hass, data)
        registry = er.async_get(hass)
        registry.async_update_entity(f"zone.{zone_id}", new_entity_id="zone.workspace", icon="mdi:desk")
        await hass.async_block_till_done()
        _, outcome, _ = await _call_tool("delete_helper", {"helper_type": "zone", "helper_id": zone_id},
                                         _access_key(), hass, data)
        assert outcome == "allowed"

        # The MESA prediction follows the deleted entry while its entity ID is free.
        from custom_components.phoenix_mcp.tools.helper import _HELPER_RESTORE_ID, _helper_create_mesa_entity
        assert _helper_create_mesa_entity("zone", {"config": {"name": "Studio"}, _HELPER_RESTORE_ID: zone_id}, hass) == "zone.workspace"
        assert _restored_helper_entity_id(hass, "zone", zone_id) == "zone.workspace"
        hass.states.async_set("zone.workspace", "0")
        assert _restored_helper_entity_id(hass, "zone", zone_id) == f"zone.{zone_id}"
        hass.states.async_remove("zone.workspace")

        deleted = data.versions.list_for("helper", f"zone:{zone_id}")[0]
        result, outcome, _ = await async_restore_version(deleted, "admin-1", hass, data)
        assert outcome == "allowed", result
        body = _json(result)
        assert (body["helper"]["id"], body["helper"]["name"], body["status"]) == (zone_id, "Studio", "verified")
        assert registry.async_get_entity_id("zone", "zone", zone_id) == "zone.workspace"
        assert registry.async_get("zone.workspace").icon == "mdi:desk"

    async def test_recreation_reports_partial_when_saved_fields_do_not_read_back(
        self, hass: HomeAssistant, helper_env, hass_admin_user,
    ):
        from custom_components.phoenix_mcp.mcp_view import async_restore_version

        data = self._data()
        created, _, _ = await _call_tool("create_helper", {
            "helper_type": "input_boolean", "config": {"name": "Night mode", "icon": "mdi:weather-night"},
        }, _access_key(), hass, data)
        helper_id = _json(created)["helper"]["id"]
        await _call_tool("delete_helper", {"helper_type": "input_boolean", "helper_id": helper_id},
                         _access_key(), hass, data)
        deleted = data.versions.list_for("helper", f"input_boolean:{helper_id}")[0]
        with patch("custom_components.phoenix_mcp.mcp_view._read_helper_config",
                   AsyncMock(return_value={"id": helper_id, "name": "Night mode"})):
            result, outcome, _ = await async_restore_version(deleted, "admin-1", hass, data)
        assert outcome == "invalid_request"
        assert _json(result)["status"] == "partial"

    @pytest.mark.parametrize(("tool", "marker"), [
        ("edit_helper", "_phoenix_helper_restore_replace"),
        ("create_helper", "_phoenix_helper_restore_id"),
    ])
    async def test_agents_cannot_send_the_internal_restore_markers(self, hass: HomeAssistant, helper_env, tool, marker):
        args = {"helper_type": "input_boolean", "config": {"name": "Guest"}, marker: "guest"}
        if tool == "edit_helper":
            args["helper_id"] = "guest"
        result, outcome, _ = await _call_tool(tool, args, _access_key(), hass, self._data())
        assert outcome == "invalid_request"
        assert marker in result["content"][0]["text"]
