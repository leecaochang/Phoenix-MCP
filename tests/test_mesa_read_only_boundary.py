"""Phoenix retains read-only protection using the core's final evaluated mode."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import async_setup_mesa, evaluate_service_entities
from custom_components.phoenix_mcp.mesa_audit import attach_mesa_audit_bridge, detach_mesa_audit_bridge
from custom_components.phoenix_mcp.mesa_core import ControlMode, MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.mcp_view import _execute_call_service, async_execute_approved_tool
from custom_components.phoenix_mcp.access_key_store import GlobalSettings, AccessKeyRecord, AccessKeyStore
from custom_components.phoenix_mcp.tools.native import _tool_intent_action


def _profile(mode="autonomous", condition=None):
    boundaries = {"control_mode": mode}
    if condition is not None:
        boundaries["temporal_constraints"] = [{
            "id": "read_only_window", "condition": condition,
            "effect": {"control_mode": "read_only"},
        }]
    return SemanticProfile.from_dict(
        "light", {"semantic_profile": {"operational_boundaries": boundaries}},
        default_origin=MetadataOrigin.USER,
    )


@pytest.fixture
async def env(hass):
    entity = er.async_get(hass).async_get_or_create(
        "light", "test", "readonly-boundary", suggested_object_id="boundary"
    ).entity_id
    hass.states.async_set(entity, "off")
    calls = async_mock_service(hass, "light", "turn_on")
    store = MagicMock(spec=AccessKeyStore)
    pending = []
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    store.get_settings.return_value = GlobalSettings(mesa_mode="advisory")
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(),
                       mesa=await async_setup_mesa(hass, "advisory"))
    hass.data[DOMAIN] = data
    access_key = AccessKeyRecord(id="boundary", name="Boundary", token_hash="x", created_at=utcnow(),
                        created_by="admin", pass_through=True, cap_registry_write="allow")
    return data, access_key, entity, calls


@pytest.mark.parametrize("ingress", ["service", "native", "approved"])
@pytest.mark.parametrize("condition,blocked", [
    (None, True),
    ({"type": "time_range", "start_time": "11:00", "end_time": "13:00"}, True),
    ({"type": "time_range", "start_time": "23:00", "end_time": "06:00"}, False),
    ({"type": "calendar_entity", "calendar_entity": "calendar.policy"}, True),
])
async def test_inherited_read_only_reaches_every_execution_path(hass, env, ingress, condition, blocked):
    data, access_key, entity, calls = env
    data.mesa.store.set_domain_profile("light", _profile("read_only" if condition is None else "autonomous", condition))
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with patch("custom_components.phoenix_mcp.mesa.dt_util.now", return_value=now):
        core = data.mesa.enforcer.evaluate(entity, "light.turn_on", current_time=now)
        assert core.allowed  # Proves the host veto, not a core refusal, is exercised.
        assert core.evaluated_control_mode is (ControlMode.READ_ONLY if blocked else ControlMode.AUTONOMOUS)
        if condition is not None:
            assert core.effective_profile.operational_boundaries.control_mode is ControlMode.AUTONOMOUS
        args = {"domain": "light", "service": "turn_on", "entity_id": entity}
        if ingress == "approved":
            from custom_components.phoenix_mcp.approval_targets import bind_targets
            args = bind_targets(args, [entity], hass)
            _, outcome, _ = await async_execute_approved_tool("call_service", args, access_key, hass, data)
        elif ingress == "native":
            _, outcome, _ = await _tool_intent_action("HassTurnOn", "light", "turn_on", {}, [entity], hass, access_key)
        else:
            _, outcome, _ = await _execute_call_service(args, access_key, hass, data)
    assert outcome == ("denied" if blocked else "allowed")
    assert len(calls) == (0 if blocked else 1)


@pytest.mark.parametrize("change", ["unchanged", "time", "profile"])
async def test_approval_replay_rechecks_current_read_only_restriction(hass, env, change):
    data, access_key, entity, calls = env
    data.mesa.set_mode("enforced")
    data.store.get_settings.return_value = GlobalSettings(mesa_mode="enforced")
    condition = {"type": "time_range", "start_time": "23:00", "end_time": "06:00"}
    data.mesa.store.set_domain_profile("light", _profile("confirm", condition))
    with (patch("custom_components.phoenix_mcp.mesa.dt_util.now", return_value=datetime(2026, 10, 2, 12, tzinfo=UTC)),
          patch("homeassistant.components.persistent_notification.async_create")):
        _, outcome, _ = await _execute_call_service(
            {"domain": "light", "service": "turn_on", "entity_id": entity}, access_key, hass, data
        )
    assert outcome == "pending_approval" and not calls
    saved = data.store.get_pending_approvals()[0]["args"]
    if change == "profile":
        data.mesa.store.set_domain_profile("light", _profile("read_only"))
    # A mode change cannot turn a previously approved action into a read-only write.
    data.mesa.set_mode("advisory")
    data.store.get_settings.return_value = GlobalSettings(mesa_mode="advisory")
    with patch("custom_components.phoenix_mcp.mesa.dt_util.now", return_value=datetime(2026, 10, 2, 23 if change == "time" else 12, tzinfo=UTC)):
        _, outcome, _ = await async_execute_approved_tool("call_service", saved, access_key, hass, data)
    assert outcome == ("allowed" if change == "unchanged" else "denied")
    assert len(calls) == (1 if change == "unchanged" else 0)


async def test_host_read_only_audit_is_attributed_once_and_excludes_previews(hass, env):
    data, access_key, entity, calls = env
    data.mesa.store.set_domain_profile("light", _profile("read_only"))
    bridge = attach_mesa_audit_bridge(data)
    try:
        evaluate_service_entities(data.mesa, "advisory", access_key, [entity], domain="light", service="turn_on",
                                  service_data={}, session_id="preview")
        data.audit.record.assert_not_called()
        _, outcome, _ = await _execute_call_service(
            {"domain": "light", "service": "turn_on", "entity_id": entity}, access_key, hass, data,
            request_id="read-only-request", client_ip="test-client",
        )
        assert outcome == "denied" and not calls
        data.audit.record.assert_called_once()
        event = data.audit.record.call_args.kwargs
        assert event["token_id"] == access_key.id
        assert event["request_id"] == "read-only-request"
        assert event["payload"]["rule_applied"] == "control_mode:read_only"
    finally:
        detach_mesa_audit_bridge(bridge)


@pytest.mark.parametrize("hour,blocked", [(12, False), (23, True)])
async def test_registry_mutation_honors_inherited_temporal_read_only(hass, env, hour, blocked):
    from custom_components.phoenix_mcp.mcp_view import _call_tool

    data, access_key, entity, _ = env
    condition = {"type": "time_range", "start_time": "23:00", "end_time": "06:00"}
    data.mesa.store.set_domain_profile("light", _profile("autonomous", condition))
    with patch("custom_components.phoenix_mcp.mesa.dt_util.now", return_value=datetime(2026, 10, 2, hour, tzinfo=UTC)):
        _, outcome, _ = await _call_tool(
            "set_entity", {"entity_id": entity, "changes": {"new_entity_id": "light.renamed"}}, access_key, hass, data
        )
    assert outcome == ("denied" if blocked else "allowed")
    assert (er.async_get(hass).async_get(entity) is not None) is blocked


@pytest.mark.parametrize("brightness,blocked", [(25, False), (100, True)])
async def test_approved_confirmation_cannot_bypass_a_declared_limit(hass, env, brightness, blocked):
    data, access_key, entity, calls = env
    data.mesa.set_mode("enforced")
    data.store.get_settings.return_value = GlobalSettings(mesa_mode="enforced")
    data.mesa.store.set(entity, SemanticProfile.from_dict(entity, {"semantic_profile": {
        "operational_boundaries": {"control_mode": "confirm", "declared_limits": [{
            "id": "brightness", "limit": {"service": "light.turn_on", "parameter": "brightness", "max_value": 50},
            "predicate": {"entity": entity, "operator": "eq", "value": "off"},
        }]},
    }}, default_origin=MetadataOrigin.USER))
    from custom_components.phoenix_mcp.approval_targets import bind_targets
    args = bind_targets({"domain": "light", "service": "turn_on", "entity_id": entity,
                         "service_data": {"brightness": brightness}}, [entity], hass)
    _, outcome, _ = await async_execute_approved_tool("call_service", args, access_key, hass, data)
    assert outcome == ("denied" if blocked else "allowed")
    assert len(calls) == (0 if blocked else 1)
