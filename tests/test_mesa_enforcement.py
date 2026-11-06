"""MESA per-entity enforcement classification.

Exercises evaluate_service_entities over a real MesaRuntime: advisory vs enforced vs
off behaviour, the interactive=False confirm routing, per-profile enforcement_mode
override, and confirm-approved folding.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import Profile, Profiles
from homeassistant.components.light.const import DATA_PROFILES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.mesa import (
    async_apply_mesa_to_call,
    async_setup_mesa,
    build_mesa_service_diff,
    evaluate_service_entities,
)
import custom_components.phoenix_mcp.mesa as mesa_module
from custom_components.phoenix_mcp.mesa_audit import mesa_advisory_ctx
from custom_components.phoenix_mcp.access_key_store import GlobalSettings
from custom_components.phoenix_mcp.mesa_core import ControlMode, MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord
from custom_components.phoenix_mcp.tools.service import _execute_call_service


def _access_key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id="tok",
        name="t",
        access_key_hash="x",
        created_at=utcnow(),
        created_by="admin",
        persona="power_user",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
    )


def _set_profile(runtime, entity_id, control_mode, *, enforcement_mode=None):
    # Stamp source: user, mirroring operator-authored profiles. An unknown-origin
    # autonomous declaration is clamped to confirm by MESA (untrusted loosening).
    ob = {"control_mode": control_mode}
    if enforcement_mode is not None:
        ob["enforcement_mode"] = enforcement_mode
    runtime.store.set(
        entity_id,
        SemanticProfile.from_dict(
            entity_id,
            {"semantic_profile": {"operational_boundaries": ob}},
            default_origin=MetadataOrigin.USER,
        ),
    )


def _evaluate(runtime, mode, entities, **kw):
    return evaluate_service_entities(
        runtime, mode, _access_key(), entities,
        domain="light", service="turn_on", service_data={}, session_id="s", **kw,
    )


@pytest.mark.asyncio
async def test_autonomous_allowed(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "autonomous")
    verdict = _evaluate(runtime, "enforced", ["light.a"])
    assert verdict.allowed == ["light.a"]
    assert verdict.confirm == [] and verdict.blocked == []


@pytest.mark.asyncio
async def test_prohibited_blocks_under_enforced(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "prohibited")
    verdict = _evaluate(runtime, "enforced", ["light.a"])
    assert verdict.allowed == []
    assert verdict.blocked and verdict.blocked[0][1] == "control_mode:prohibited"


@pytest.mark.asyncio
async def test_prohibited_warns_but_allows_under_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "prohibited")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.allowed == ["light.a"]
    assert verdict.blocked == []


@pytest.mark.asyncio
async def test_read_only_blocks_even_under_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "read_only")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.allowed == []
    assert verdict.blocked and verdict.blocked[0][1] == "control_mode:read_only"


@pytest.mark.asyncio
async def test_confirm_routes_to_confirm_under_enforced(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "confirm")
    verdict = _evaluate(runtime, "enforced", ["light.a"])
    assert verdict.confirm == ["light.a"]
    assert verdict.allowed == [] and verdict.blocked == []


@pytest.mark.asyncio
async def test_confirm_allows_with_warning_under_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "confirm")
    hass.states.async_set("light.a", "off")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.allowed == ["light.a"]
    assert verdict.confirm == []
    # The agent must be told the action was confirm-gated but allowed because
    # MESA is advisory (otherwise advisory is indistinguishable from off).
    assert any("light.a" in w and "advisory" in w for w in verdict.warnings)


@pytest.mark.asyncio
async def test_confirm_approved_emits_no_advisory_warning(hass: HomeAssistant):
    # Re-execution after admin approval must not re-warn (it was approved, not
    # waved through by advisory mode).
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "confirm")
    verdict = _evaluate(runtime, "enforced", ["light.a"], confirm_approved=True)
    assert verdict.allowed == ["light.a"]
    assert verdict.warnings == []


@pytest.mark.asyncio
async def test_per_profile_enforced_overrides_global_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "confirm", enforcement_mode="enforced")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.confirm == ["light.a"]


@pytest.mark.asyncio
async def test_confirm_approved_folds_into_allowed(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "confirm")
    verdict = _evaluate(runtime, "enforced", ["light.a"], confirm_approved=True)
    assert verdict.allowed == ["light.a"]
    assert verdict.confirm == []


@pytest.mark.asyncio
async def test_mixed_entities_split_correctly(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.ok", "autonomous")
    _set_profile(runtime, "light.gate", "confirm")
    _set_profile(runtime, "light.no", "prohibited")
    verdict = _evaluate(runtime, "enforced", ["light.ok", "light.gate", "light.no"])
    assert verdict.allowed == ["light.ok"]
    assert verdict.confirm == ["light.gate"]
    assert [b[0] for b in verdict.blocked] == ["light.no"]


@pytest.mark.asyncio
async def test_diff_includes_mesa_block(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.gate", "confirm")
    _set_profile(runtime, "light.ok", "autonomous")
    verdict = _evaluate(runtime, "enforced", ["light.gate", "light.ok"])
    diff = build_mesa_service_diff("light", "turn_on", {"brightness_pct": 50}, verdict)
    assert diff["kind"] == "service_preview"
    assert diff["preview"]["mesa"]["confirm_entities"] == ["light.gate"]
    assert diff["preview"]["resolved_entity_ids"] == ["light.gate", "light.ok"]


def test_each_entity_is_evaluated_against_its_exact_service_data():
    allowed = SimpleNamespace(allowed=True, warnings=[], rule_applied=None, evaluated_control_mode=ControlMode.AUTONOMOUS)
    enforcer = SimpleNamespace(evaluate=MagicMock(return_value=allowed))
    runtime = SimpleNamespace(enforcer=enforcer)
    verdict = evaluate_service_entities(
        runtime,
        "enforced",
        _access_key(),
        ["media_player.one", "media_player.two"],
        domain="media_player",
        service="volume_set",
        service_data={},
        service_data_by_entity={
            "media_player.one": {"volume_level": 0.45},
            "media_player.two": {"volume_level": 1.0},
        },
        session_id="sid",
    )
    assert verdict.allowed == ["media_player.one", "media_player.two"]
    assert [call.kwargs["service_params"] for call in enforcer.evaluate.call_args_list] == [
        {"volume_level": 0.45, "entity_id": "media_player.one"},
        {"volume_level": 1.0, "entity_id": "media_player.two"},
    ]


@pytest.mark.asyncio
async def test_one_mesa_approval_preserves_every_entity_specific_value(
    hass: HomeAssistant, monkeypatch
):
    from custom_components.phoenix_mcp.const import APPROVAL_TARGETS_KEY
    from homeassistant.helpers import entity_registry as er
    for name in ("one", "two"):
        er.async_get(hass).async_get_or_create("media_player", "test", name, suggested_object_id=name)
        hass.states.async_set("media_player." + name, "on")
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "media_player.one", "confirm")
    _set_profile(runtime, "media_player.two", "autonomous")
    create = AsyncMock(return_value=SimpleNamespace(id="approval"))
    monkeypatch.setattr(mesa_module, "async_create_mesa_approval", create)
    data = SimpleNamespace(
        mesa=runtime,
        mesa_setup_failed=False,
        store=SimpleNamespace(get_settings=lambda: GlobalSettings(mesa_mode="enforced")),
    )
    plan = {
        "target_levels": [
            {"entity_id": "media_player.one", "volume_level": 0.45},
            {"entity_id": "media_player.two", "volume_level": 1.0},
        ],
        "volume_step": 25,
    }
    mapping = {
        "media_player.one": {"volume_level": 0.45},
        "media_player.two": {"volume_level": 1.0},
    }
    outcome = await async_apply_mesa_to_call(
        hass,
        data,
        _access_key(),
        domain="media_player",
        service="volume_set",
        service_data={},
        service_data_by_entity=mapping,
        entities=["media_player.one", "media_player.two"],
        request_id="rid",
        client_ip=None,
        session_id="sid",
        approval_tool_name="HassSetVolumeRelative",
        approval_args=plan,
        require_all=True,
    )
    assert outcome.decision == "pending"
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["tool_name"] == "HassSetVolumeRelative"
    assert {key: value for key, value in kwargs["args"].items() if key != APPROVAL_TARGETS_KEY} == plan
    assert kwargs["args"][APPROVAL_TARGETS_KEY]["entity_ids"] == ["media_player.one", "media_player.two"]
    assert isinstance(kwargs["args"][APPROVAL_TARGETS_KEY]["fingerprint"], str)
    assert kwargs["diff"]["preview"]["service_data_by_entity"] == mapping
    assert kwargs["diff"]["preview"]["mesa"] == {
        "confirm_entities": ["media_player.one"],
        "allowed_entities": ["media_player.two"],
        "blocked": [],
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_custom_mesa_approval_preserves_tool_args_and_diff(
    hass: HomeAssistant, monkeypatch
):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.gate", "confirm")
    create = AsyncMock(return_value=SimpleNamespace(id="approval"))
    monkeypatch.setattr(mesa_module, "async_create_mesa_approval", create)
    data = SimpleNamespace(
        mesa=runtime,
        mesa_setup_failed=False,
        store=SimpleNamespace(get_settings=lambda: GlobalSettings(mesa_mode="enforced")),
    )
    custom_diff = {
        "kind": "config_diff",
        "preview": {"property": "led_mode"},
    }
    outcome = await async_apply_mesa_to_call(
        hass,
        data,
        _access_key(),
        domain="zigbee",
        service="set_property",
        service_data={"property": "led_mode", "value": "on"},
        entities=["light.gate"],
        request_id="rid",
        client_ip="1.2.3.4",
        session_id="sid",
        approval_tool_name="set_zigbee_device_property",
        approval_args={"device_id": "dev", "property": "led_mode"},
        approval_diff=custom_diff,
        require_all=True,
    )
    assert outcome.decision == "pending"
    kwargs = create.await_args.kwargs
    assert kwargs["tool_name"] == "set_zigbee_device_property"
    assert kwargs["args"] == {"device_id": "dev", "property": "led_mode"}
    assert kwargs["diff"]["kind"] == "config_diff"
    assert kwargs["diff"]["preview"]["property"] == "led_mode"
    assert kwargs["diff"]["preview"]["mesa"]["confirm_entities"] == [
        "light.gate"
    ]


@pytest.mark.asyncio
async def test_require_all_mesa_denies_mixed_allowed_and_blocked(
    hass: HomeAssistant, monkeypatch
):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.ok", "autonomous")
    _set_profile(runtime, "light.no", "prohibited")
    create = AsyncMock()
    monkeypatch.setattr(mesa_module, "async_create_mesa_approval", create)
    data = SimpleNamespace(
        mesa=runtime,
        mesa_setup_failed=False,
        store=SimpleNamespace(get_settings=lambda: GlobalSettings(mesa_mode="enforced")),
    )
    outcome = await async_apply_mesa_to_call(
        hass,
        data,
        _access_key(),
        domain="zigbee",
        service="set_property",
        service_data={"property": "led_mode", "value": "on"},
        entities=["light.ok", "light.no"],
        request_id="rid",
        client_ip=None,
        session_id="sid",
        require_all=True,
    )
    assert outcome.decision == "deny"
    assert [item[0] for item in outcome.blocked] == ["light.no"]
    create.assert_not_awaited()


async def _mixed_call(hass: HomeAssistant, keep_mode: str = "autonomous"):
    """Two written lights under a real MESA runtime, with one blocked by policy."""
    from custom_components.phoenix_mcp.data import PhoenixData
    from pytest_homeassistant.common import async_mock_service

    registry = er.async_get(hass)
    entities = []
    for object_id in ("keep", "block"):
        entity_id = registry.async_get_or_create("light", "test", object_id, suggested_object_id=object_id).entity_id
        hass.states.async_set(entity_id, "off")
        entities.append(entity_id)
    calls = async_mock_service(hass, "light", "turn_on")
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.keep", keep_mode)
    _set_profile(runtime, "light.block", "read_only")
    store = MagicMock()
    store.get_settings.return_value = GlobalSettings(mesa_mode="enforced")
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=runtime)
    key = _access_key()
    key.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    return entities, calls, data, key


@pytest.mark.asyncio
async def test_a_mixed_call_only_reaches_the_entities_mesa_kept(hass: HomeAssistant):
    entities, calls, data, key = await _mixed_call(hass)
    result, outcome, _ = await _execute_call_service(
        {"domain": "light", "service": "turn_on", "entity_id": entities}, key, hass, data)
    assert outcome == "allowed", result
    assert [call.data["entity_id"] for call in calls] == [["light.keep"]]


@pytest.mark.asyncio
async def test_an_approved_mixed_call_with_a_newly_blocked_target_dispatches_nothing(hass: HomeAssistant):
    from custom_components.phoenix_mcp.approval_targets import bind_targets
    from custom_components.phoenix_mcp.request_authority import _approved_exec_ctx

    entities, calls, data, key = await _mixed_call(hass)
    args = bind_targets({"domain": "light", "service": "turn_on", "entity_id": entities}, entities, hass)
    token = _approved_exec_ctx.set(True)
    try:
        result, outcome, _ = await _execute_call_service(args, key, hass, data)
    finally:
        _approved_exec_ctx.reset(token)
    assert outcome == "denied", result
    assert calls == []


@pytest.mark.asyncio
async def test_authority_lost_during_the_mesa_gate_stops_dispatch(hass: HomeAssistant):
    from custom_components.phoenix_mcp import mesa
    from custom_components.phoenix_mcp.tools import service as service_tools
    from custom_components.phoenix_mcp.request_authority import AuthorityChangedError

    entities, calls, data, key = await _mixed_call(hass)
    real_gate = mesa.async_apply_mesa_to_call
    lost = []

    async def gate_then_lose_authority(*args, **kwargs):
        outcome = await real_gate(*args, **kwargs)
        lost.append(True)
        return outcome

    def check():
        if lost:
            raise AuthorityChangedError("key changed")

    with (patch.object(service_tools, "async_apply_mesa_to_call", gate_then_lose_authority),
          patch.object(service_tools, "check_request_authority", check),
          pytest.raises(AuthorityChangedError)):
        await _execute_call_service({"domain": "light", "service": "turn_on", "entity_id": entities}, key, hass, data)
    assert calls == []


# --- declared limits meet every form of the same call ---------------------------


_ALWAYS = {"entity": "sun.sun", "operator": "neq", "value": "impossible"}


def _limited(runtime, entity_id, *, service="light.turn_on", parameter="brightness", enforcement="enforced", **limit):
    runtime.hass.states.async_set("sun.sun", "above_horizon")
    runtime.store.set(entity_id, SemanticProfile.from_dict(entity_id, {"semantic_profile": {"operational_boundaries": {
        "control_mode": "autonomous",
        "enforcement_mode": enforcement,
        "declared_limits": [{"id": "cap", "predicate": _ALWAYS,
                             "limit": {"service": service, "parameter": parameter, **limit},
                             "human_reason": "too bright"}],
    }}}, default_origin=MetadataOrigin.USER))


def _call(runtime, entity_id, domain, service, data):
    return evaluate_service_entities(
        runtime, "advisory", _access_key(), [entity_id],
        domain=domain, service=service, service_data=data, session_id="s",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("domain", "service", "data", "blocked"), [
    ("light", "turn_on", {"brightness": 200}, True),
    ("light", "turn_on", {"brightness": 100}, False),
    ("homeassistant", "turn_on", {"brightness": 200}, True),
    ("homeassistant", "turn_on", {"brightness": 100}, False),
    ("homeassistant", "toggle", {"brightness": 200}, True),
    ("light", "toggle", {"brightness": 200}, True),
    ("light", "toggle", {}, False),
    ("homeassistant", "turn_on", {}, False),
    ("light", "turn_on", {"brightness_pct": 100}, True),
    ("light", "turn_on", {"brightness_pct": 30}, False),
    ("light", "turn_on", {"brightness_pct": "100"}, True),
    ("homeassistant", "turn_on", {"brightness_pct": 100}, True),
    ("light", "turn_on", {"profile": "relax"}, True),
    ("light", "turn_on", {"brightness_pct": "not a number"}, True),
    ("light", "turn_on", {"color_name": "red"}, False),
])
async def test_a_brightness_limit_meets_every_form_of_a_brightness(hass, domain, service, data, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "light.dim", max_value=128)
    verdict = _call(runtime, "light.dim", domain, service, data)
    assert bool(verdict.blocked) is blocked, verdict
    if blocked:
        assert verdict.blocked[0][1] == "declared_limit:cap"


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "attributes", "data", "blocked"), [
    ("on", {"brightness": 100}, {"brightness_step": 50}, True),
    ("on", {"brightness": 100}, {"brightness_step": 10}, False),
    ("on", {"brightness": 100}, {"brightness_step_pct": 20}, True),
    ("on", {"brightness": 100}, {"brightness_step": -90}, False),
    ("off", {}, {"brightness_step": 100}, False),
    ("off", {}, {"brightness_step": 200}, True),
    ("on", {}, {"brightness_step": "lots"}, True),
])
async def test_a_step_is_measured_from_the_current_brightness(hass, state, attributes, data, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", state, attributes)
    _limited(runtime, "light.dim", max_value=128)
    assert bool(_call(runtime, "light.dim", "light", "turn_on", data).blocked) is blocked


@pytest.mark.asyncio
async def test_a_limit_on_the_percentage_meets_an_absolute_brightness(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "light.dim", parameter="brightness_pct", max_value=50)
    assert _call(runtime, "light.dim", "light", "turn_on", {"brightness": 200}).blocked
    assert not _call(runtime, "light.dim", "light", "turn_on", {"brightness": 100}).blocked


@pytest.mark.asyncio
async def test_a_generic_call_reaches_a_limit_on_any_domain(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "fan.big", service="fan.turn_on", parameter="percentage", max_value=50)
    assert _call(runtime, "fan.big", "homeassistant", "turn_on", {"percentage": 80}).blocked


@pytest.mark.asyncio
async def test_a_limit_on_the_generic_service_itself_still_matches(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "light.dim", service="homeassistant.turn_on", max_value=128)
    assert _call(runtime, "light.dim", "homeassistant", "turn_on", {"brightness": 200}).blocked


@pytest.mark.asyncio
async def test_a_limit_on_toggle_itself_still_matches(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "light.dim", service="light.toggle", max_value=128)
    assert _call(runtime, "light.dim", "light", "toggle", {"brightness": 200}).blocked


@pytest.mark.asyncio
async def test_an_advisory_limit_warns_once_for_the_equivalent_form(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "light.dim", enforcement="advisory", max_value=128)
    hass.states.async_set("light.dim", "on")
    verdict = _call(runtime, "light.dim", "homeassistant", "turn_on", {"brightness_pct": 100})
    assert verdict.allowed == ["light.dim"]
    assert len([warning for warning in verdict.warnings if "too bright" in warning]) == 1


@pytest.mark.asyncio
async def test_a_call_on_an_entity_without_limits_is_evaluated_once(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    with patch.object(runtime.enforcer, "evaluate", wraps=runtime.enforcer.evaluate) as evaluate:
        _call(runtime, "light.plain", "homeassistant", "turn_on", {"brightness_pct": 100})
    assert evaluate.call_count == 1
    assert evaluate.call_args.kwargs["service"] == "homeassistant.turn_on"
    assert "brightness" not in evaluate.call_args.kwargs["service_params"]


# --- what an agent can read from a verdict --------------------------------------


@pytest.mark.asyncio
async def test_a_warning_never_names_an_entity_the_key_cannot_read(hass):
    runtime = await async_setup_mesa(hass, "enforced")
    hass.states.async_set("light.dim", "on")
    hass.states.async_set("person.alice", "unavailable")
    runtime.store.set("light.dim", SemanticProfile.from_dict("light.dim", {"semantic_profile": {
        "operational_boundaries": {"control_mode": "autonomous", "declared_limits": [{
            "id": "cap", "predicate": {"entity": "person.alice", "operator": "eq", "value": "home"},
            "limit": {"service": "light.turn_on", "parameter": "brightness", "max_value": 50}}]}}},
        default_origin=MetadataOrigin.USER))
    verdict = evaluate_service_entities(
        runtime, "enforced", _access_key(), ["light.dim"],
        domain="light", service="turn_on", service_data={"brightness": 10}, session_id="s",
    )
    text = " ".join(verdict.warnings)
    assert verdict.allowed == ["light.dim"]
    assert "person.alice" not in text
    assert "unavailable" in text


@pytest.mark.asyncio
async def test_a_warning_keeps_an_entity_the_key_can_read(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.a", "on")
    runtime.store.set("light.a", SemanticProfile.from_dict("light.a", {"semantic_profile": {
        "operational_boundaries": {"control_mode": "autonomous", "declared_limits": [{
            "id": "cap", "predicate": {"entity": "light.b", "operator": "eq", "value": "on"},
            "limit": {"service": "light.turn_on", "parameter": "brightness", "max_value": 50}}]}}},
        default_origin=MetadataOrigin.USER))
    hass.states.async_set("light.b", "unavailable")
    verdict = _call(runtime, "light.a", "light", "turn_on", {"brightness": 10})
    assert any("light.b" in warning for warning in verdict.warnings)


# --- a target field is data, a target block is not -------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["advisory", "enforced"])
@pytest.mark.parametrize("target", ["x", ["a", "b"]])
async def test_a_target_data_field_is_not_a_contradiction(hass, caplog, mode, target):
    runtime = await async_setup_mesa(hass, mode)
    _set_profile(runtime, "light.a", "autonomous")
    verdict = evaluate_service_entities(
        runtime, mode, _access_key(), ["light.a"],
        domain="light", service="turn_on", service_data={"target": target}, session_id="s",
    )
    assert verdict.allowed == ["light.a"]
    assert "contradictory" not in caplog.text


@pytest.mark.asyncio
async def test_a_contradictory_target_is_refused_without_an_error_log(hass, caplog):
    """A caller chooses these parameters, so they must not be able to raise an error."""
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "autonomous")
    verdict = evaluate_service_entities(
        runtime, "advisory", _access_key(), ["light.a"],
        domain="light", service="turn_on", service_data={"config_entry_id": "x"}, session_id="s",
    )
    assert verdict.blocked[0][1] == "contradictory_target"
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


@pytest.mark.parametrize("service, data, blocked", [
    ("turn_on", {"percentage": 80}, True),
    ("turn_on", {"percentage": 40}, False),
    ("turn_on", {}, False),
    ("set_percentage", {"percentage": 80}, True),
    ("set_percentage", {"percentage": 40}, False),
    ("toggle", {"percentage": 80}, True),
    ("increase_speed", {"percentage_step": 40}, True),
    ("increase_speed", {"percentage_step": 10}, False),
    ("increase_speed", {"percentage_step": "fast"}, True),
    ("decrease_speed", {"percentage_step": 40}, False),
])
@pytest.mark.parametrize("limited", ["fan.set_percentage", "fan.turn_on"])
@pytest.mark.asyncio
async def test_a_fan_limit_meets_every_percentage_form(hass, limited, service, data, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "fan.attic", service=limited, parameter="percentage", max_value=60)
    hass.states.async_set("fan.attic", "on", {"percentage": 30, "percentage_step": 25})
    assert bool(_call(runtime, "fan.attic", "fan", service, data).blocked) is blocked


@pytest.mark.parametrize("percentage, service, blocked", [
    (30, "increase_speed", True),
    (10, "increase_speed", False),
    (100, "decrease_speed", True),
    (75, "decrease_speed", False),
])
@pytest.mark.asyncio
async def test_a_default_fan_step_lands_on_the_next_speed(hass, percentage, service, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "fan.attic", service="fan.set_percentage", parameter="percentage", max_value=60)
    hass.states.async_set("fan.attic", "on", {"percentage": percentage, "percentage_step": 25})
    assert bool(_call(runtime, "fan.attic", "fan", service, {}).blocked) is blocked


@pytest.mark.asyncio
async def test_a_default_fan_step_that_cannot_be_worked_out_is_refused(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "fan.attic", service="fan.set_percentage", parameter="percentage", max_value=60)
    hass.states.async_set("fan.attic", "on", {"percentage": 30})
    assert _call(runtime, "fan.attic", "fan", "increase_speed", {}).blocked


@pytest.mark.asyncio
async def test_a_generic_call_reaches_a_fan_percentage_limit(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "fan.attic", service="fan.set_percentage", parameter="percentage", max_value=60)
    assert _call(runtime, "fan.attic", "homeassistant", "turn_on", {"percentage": 80}).blocked


@pytest.mark.parametrize("parameter, data, blocked", [
    ("temperature", {"target_temp_high": 30, "target_temp_low": 18}, True),
    ("temperature", {"target_temp_high": 24, "target_temp_low": 18}, False),
    ("temperature", {"target_temp_high": 24, "target_temp_low": 30}, True),
    ("target_temp_high", {"temperature": 30}, True),
    ("target_temp_high", {"temperature": 20}, False),
    ("target_temp_low", {"temperature": 30}, True),
    ("target_temp_high", {"target_temp_high": 24, "target_temp_low": 30}, False),
    ("target_temp_low", {"target_temp_high": 30, "target_temp_low": 24}, False),
])
@pytest.mark.asyncio
async def test_a_climate_limit_meets_a_range_and_a_single_temperature(hass, parameter, data, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "climate.hall", service="climate.set_temperature", parameter=parameter, max_value=25)
    assert bool(_call(runtime, "climate.hall", "climate", "set_temperature", data).blocked) is blocked


@pytest.mark.parametrize("state, service, data, blocked", [
    ("off", "turn_on", {}, True),
    ("off", "turn_on", {"color_name": "red"}, True),
    ("on", "turn_on", {}, True),
    ("on", "turn_on", {"color_name": "red"}, False),
    ("off", "turn_on", {"brightness": 40}, False),
    ("off", "toggle", {}, True),
    ("on", "toggle", {}, False),
])
@pytest.mark.parametrize("domain", ["light", "homeassistant"])
@pytest.mark.asyncio
async def test_a_call_naming_no_brightness_meets_the_default_light_profile(
    hass, domain, state, service, data, blocked,
):
    profile = Profile("light.dim.default", 0.5, 0.5, 200)
    profiles = Profiles(hass)
    profiles.data[profile.name] = profile
    hass.data[DATA_PROFILES] = profiles
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "light.dim", max_value=128)
    hass.states.async_set("light.dim", state)
    assert bool(_call(runtime, "light.dim", domain, service, data).blocked) is blocked


@pytest.mark.asyncio
async def test_a_bare_turn_on_with_no_profile_stays_within_the_limit(hass):
    hass.data[DATA_PROFILES] = Profiles(hass)
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "light.dim", max_value=128)
    hass.states.async_set("light.dim", "off")
    assert not _call(runtime, "light.dim", "light", "turn_on", {}).blocked


@pytest.mark.parametrize(("mode", "profile", "flagged"), [
    ("advisory", "prohibited", True),
    ("advisory", "autonomous", False),
    ("enforced", "autonomous", False),
])
@pytest.mark.asyncio
async def test_the_gate_flags_the_audit_row_of_a_call_it_lets_through_with_warnings(hass, mode, profile, flagged):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", profile)
    data = SimpleNamespace(mesa=runtime, store=SimpleNamespace(get_settings=lambda: GlobalSettings(mesa_mode=mode)))
    mesa_advisory_ctx.set(False)
    outcome = await mesa_module.async_apply_mesa_to_call(
        hass, data, _access_key(), domain="light", service="turn_on", service_data={},
        entities=["light.a"], request_id="r", client_ip=None, session_id="s",
    )
    assert outcome.decision == "allow"
    assert mesa_advisory_ctx.get() is flagged


# --- what an upgraded mesa-core adds at this seam ---------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "domain", "service", "blocked"), [
    ("off", "light", "turn_on", True),
    ("off", "light", "toggle", True),
    ("off", "homeassistant", "toggle", True),
    ("on", "light", "toggle", False),
])
async def test_a_limit_can_refuse_a_call_that_omits_its_parameter(hass, state, domain, service, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", state)
    _limited(runtime, "light.dim", max_value=128, when_absent="refuse")
    verdict = _call(runtime, "light.dim", domain, service, {})
    assert bool(verdict.blocked) is blocked, verdict


@pytest.mark.asyncio
async def test_a_limit_on_absent_parameters_stays_allowed_by_default(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "off")
    _limited(runtime, "light.dim", max_value=128)
    assert not _call(runtime, "light.dim", "light", "toggle", {}).blocked


@pytest.mark.asyncio
async def test_a_limit_measures_a_plain_target_field(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "notify.pager", service="notify.send_message", parameter="target", permitted_values=["ops"])
    allowed = _call(runtime, "notify.pager", "notify", "send_message", {"target": "ops"})
    refused = _call(runtime, "notify.pager", "notify", "send_message", {"target": "everyone"})
    assert not allowed.blocked and refused.blocked


@pytest.mark.asyncio
async def test_an_unresolvable_profile_writes_one_audit_row(hass, caplog):
    from custom_components.phoenix_mcp import mesa_audit

    runtime = await async_setup_mesa(hass, "enforced")
    data = MagicMock()
    bridge = mesa_audit.attach_mesa_audit_bridge(data)
    try:
        with (patch.object(runtime.enforcer.resolver, "explain", side_effect=ValueError("damaged")),
              mesa_audit.request_context(_access_key(), "rid", None)):
            verdict = _call(runtime, "light.dim", "light", "turn_on", {})
    finally:
        mesa_audit.detach_mesa_audit_bridge(bridge)
    assert verdict.blocked[0][1] == "policy_unavailable"
    data.audit.record.assert_called_once()
    assert data.audit.record.call_args.kwargs["method"] == "mesa:enforcement_decision"
    assert data.audit.record.call_args.kwargs["request_id"] == "rid"


def _confirm_limited(runtime, entity_id, **limit):
    _limited(runtime, entity_id, **limit)
    document = runtime.backend.read(entity_id)
    document["semantic_profile"]["operational_boundaries"]["control_mode"] = "confirm"
    runtime.backend.write(entity_id, document)


@pytest.mark.parametrize(("domain", "service", "data", "rule"), [
    ("light", "turn_on", {"brightness": 200}, "declared_limit:cap"),
    ("homeassistant", "turn_on", {"brightness": 200}, "declared_limit:cap"),
    ("homeassistant", "toggle", {"brightness": 200}, "declared_limit:cap"),
    ("light", "toggle", {"brightness": 200}, "declared_limit:cap"),
    ("light", "turn_on", {"brightness_pct": 90}, "declared_limit:cap"),
    ("light", "turn_on", {"brightness_step": 180}, "declared_limit:cap"),
    ("light", "turn_on", {"white": 200}, "declared_limit:cap"),
])
@pytest.mark.asyncio
async def test_a_confirm_entity_refuses_every_form_of_a_breached_limit(hass, domain, service, data, rule):
    """An approver must not be asked about a value the limit refuses outright."""
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "on", {"brightness": 10})
    _confirm_limited(runtime, "light.dim", max_value=128)
    verdict = _call(runtime, "light.dim", domain, service, data)
    assert verdict.confirm == [] and verdict.allowed == []
    assert verdict.blocked[0][1] == rule


@pytest.mark.parametrize(("domain", "data"), [
    ("light", {"brightness": 100}),
    ("homeassistant", {"brightness": 100}),
    ("light", {"brightness_pct": 20}),
    ("light", {"white": 100}),
])
@pytest.mark.asyncio
async def test_a_confirm_entity_within_its_limit_still_asks(hass, domain, data):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "on", {"brightness": 10})
    _confirm_limited(runtime, "light.dim", max_value=128)
    verdict = _call(runtime, "light.dim", domain, "turn_on", data)
    assert verdict.confirm == ["light.dim"] and verdict.blocked == []


@pytest.mark.asyncio
async def test_a_climate_limit_only_profile_refuses_a_range_on_its_confirm_baseline(hass):
    """Leaving the mode unset keeps the baseline, which is Confirm for most domains."""
    runtime = await async_setup_mesa(hass, "advisory")
    _limited(runtime, "climate.hall", service="climate.set_temperature", parameter="temperature", max_value=25)
    document = runtime.backend.read("climate.hall")
    del document["semantic_profile"]["operational_boundaries"]["control_mode"]
    runtime.backend.write("climate.hall", document)
    verdict = _call(runtime, "climate.hall", "climate", "set_temperature", {"target_temp_high": 30, "target_temp_low": 18})
    assert verdict.confirm == [] and verdict.blocked[0][1] == "declared_limit:cap"


@pytest.mark.asyncio
async def test_an_approval_does_not_pass_a_limit_on_an_equivalent_form(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "on", {"brightness": 10})
    _confirm_limited(runtime, "light.dim", max_value=128)
    verdict = evaluate_service_entities(
        runtime, "advisory", _access_key(), ["light.dim"], domain="homeassistant", service="turn_on",
        service_data={"brightness": 200}, session_id="s", confirm_approved=True,
    )
    assert verdict.allowed == [] and verdict.blocked[0][1] == "declared_limit:cap"


@pytest.mark.asyncio
async def test_an_advisory_confirm_entity_warns_of_a_breached_limit_on_any_form(hass):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "on", {"brightness": 10})
    _confirm_limited(runtime, "light.dim", max_value=128, enforcement="advisory")
    verdict = _call(runtime, "light.dim", "homeassistant", "turn_on", {"brightness": 200})
    assert verdict.allowed == ["light.dim"]
    assert any("exceeds" in warning for warning in verdict.warnings)


@pytest.mark.parametrize(("state", "attributes", "white", "blocked"), [
    ("on", {}, 200, True),
    ("on", {}, 100, False),
    ("on", {}, "200", True),
    ("on", {"brightness": 200}, True, True),
    ("on", {"brightness": 100}, True, False),
    ("off", {}, True, True),
    ("on", {}, float("nan"), True),
])
@pytest.mark.asyncio
async def test_white_is_measured_as_the_brightness_it_carries(hass, state, attributes, white, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", state, attributes)
    _limited(runtime, "light.dim", max_value=128)
    assert bool(_call(runtime, "light.dim", "light", "turn_on", {"white": white}).blocked) is blocked


@pytest.mark.parametrize(("data", "blocked"), [
    ({"white": 200, "brightness": 100}, False),
    ({"white": 100, "brightness": 200}, True),
    ({"white": 100, "brightness_pct": 90}, True),
])
@pytest.mark.asyncio
async def test_brightness_overrides_white_as_it_does_in_home_assistant(hass, data, blocked):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "on")
    _limited(runtime, "light.dim", max_value=128)
    assert bool(_call(runtime, "light.dim", "light", "turn_on", data).blocked) is blocked


@pytest.mark.parametrize(("profile_brightness", "white", "blocked"), [(200, 100, True), (100, 200, False)])
@pytest.mark.asyncio
async def test_a_default_profile_overrides_white_as_it_does_in_home_assistant(hass, profile_brightness, white, blocked):
    profile = Profile("light.dim.default", 0.5, 0.5, profile_brightness)
    profiles = Profiles(hass)
    profiles.data[profile.name] = profile
    hass.data[DATA_PROFILES] = profiles
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "off")
    _limited(runtime, "light.dim", max_value=128)
    assert bool(_call(runtime, "light.dim", "light", "turn_on", {"white": white}).blocked) is blocked


@pytest.mark.parametrize("white", [200, 40, True])
@pytest.mark.asyncio
async def test_a_limit_refusing_an_absent_brightness_refuses_white(hass, white):
    """The call names no brightness, which is what such a limit asks for."""
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "on", {"brightness": 10})
    _limited(runtime, "light.dim", max_value=128, when_absent="refuse")
    assert _call(runtime, "light.dim", "light", "turn_on", {"white": white}).blocked


@pytest.mark.parametrize("white", [200, 40])
@pytest.mark.asyncio
async def test_a_limit_refusing_an_absent_brightness_refuses_white_through_a_generic_call(hass, white):
    runtime = await async_setup_mesa(hass, "advisory")
    hass.states.async_set("light.dim", "on", {"brightness": 10})
    _limited(runtime, "light.dim", max_value=128, when_absent="refuse")
    verdict = _call(runtime, "light.dim", "homeassistant", "turn_on", {"white": white})
    assert bool(verdict.blocked) is (white == 200)
