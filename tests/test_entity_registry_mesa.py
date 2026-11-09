"""Entity-registry rename/delete safety through inherited MESA profiles."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tools.helper import _helper_mesa_preview
from custom_components.phoenix_mcp.tools.integrations import _config_entry_mesa_preview
from custom_components.phoenix_mcp.tools.registry import (
    _REGISTRY_MESA_FINGERPRINT,
    _device_mesa_preview,
    _entity_identity_references,
)
from custom_components.phoenix_mcp.tool_common import RefusedBeforeWrite, _tool_success
from custom_components.phoenix_mcp import tool_dispatch as tool_dispatch_module
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.mesa import (
    RegistryMesaDecision,
    async_setup_mesa,
    evaluate_registry_action,
)
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.access_key_store import (
    GlobalSettings,
    PermissionNode,
    PermissionTree,
    AccessKeyRecord,
    AccessKeyStore,
    snapshot_preset,
)


def _profile(key: str, mode: str, reason: str = "Registry safety test") -> SemanticProfile:
    return SemanticProfile.from_dict(
        key,
        {
            "semantic_profile": {
                "operational_boundaries": {
                    "control_mode": mode,
                    "control_reason": reason,
                }
            }
        },
        default_origin=MetadataOrigin.USER,
    )


def _access_key(*, cap: str = "allow", state: str = "GREEN") -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()),
        name="registry-test",
        access_key_hash="x",
        created_at=utcnow(),
        created_by="test",
        cap_registry_write=cap,
        permissions=PermissionTree(domains={"light": PermissionNode(state=state)}),
    )


def _body(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _data(hass: HomeAssistant, mesa_mode: str) -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_settings.return_value = GlobalSettings(mesa_mode=mesa_mode)
    store.list_access_keys.return_value = []
    store.get_entity_hints.return_value = {}
    store.get_pending_approvals.return_value = []
    runtime = await async_setup_mesa(hass, mesa_mode)
    data = PhoenixData(
        store=store,
        rate_limiter=MagicMock(),
        audit=MagicMock(),
        mesa=runtime,
    )
    hass.data[DOMAIN] = data
    return data


@pytest.fixture
def registry_entity(hass: HomeAssistant) -> tuple[str, str]:
    config_entry = MockConfigEntry(domain="test_integration", entry_id="registry-mesa")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("test_integration", "registry-device")},
    )
    entry = er.async_get(hass).async_get_or_create(
        "light",
        "test_integration",
        "registry-light",
        config_entry=config_entry,
        device_id=device.id,
        suggested_object_id="registry_light",
    )
    hass.states.async_set(entry.entity_id, "off")
    return entry.entity_id, device.id


@pytest.mark.parametrize("profile_scope", ["domain", "device"])
@pytest.mark.parametrize("mode", ["advisory", "enforced"])
async def test_inherited_read_only_blocks_rename_and_delete_before_approval(
    hass: HomeAssistant, registry_entity, profile_scope, mode
):
    entity_id, device_id = registry_entity
    data = await _data(hass, mode)
    if profile_scope == "domain":
        data.mesa.store.set_domain_profile(
            "light", _profile("light", "read_only", "Domain protects registry identity")
        )
    else:
        data.mesa.store.set_device_profile(
            device_id,
            _profile(device_id, "read_only", "Device protects registry identity"),
        )

    gate = AsyncMock()
    with patch("custom_components.phoenix_mcp.tools.registry._gate", gate):
        for tool_name, args in (
            (
                "set_entity",
                {
                    "entity_id": entity_id,
                    "changes": {"new_entity_id": "light.registry_light_renamed"},
                },
            ),
            ("delete_entity", {"entity_id": entity_id}),
        ):
            content, outcome, _resource = await _call_tool(
                tool_name, args, _access_key(cap="confirm"), hass, data
            )
            assert outcome == "denied"
            denial = _body(content)
            assert denial["mesa"]["effective_rule"]["control_mode"] == "read_only"
            assert denial["mesa"]["effective_rule"]["provided_by_level"] == profile_scope

    gate.assert_not_awaited()
    assert er.async_get(hass).async_get(entity_id) is not None
    assert er.async_get(hass).async_get("light.registry_light_renamed") is None


async def test_read_only_always_blocks_but_advisory_prohibited_warns(
    hass: HomeAssistant, registry_entity
):
    entity_id, _device_id = registry_entity
    data = await _data(hass, "advisory")
    data.mesa.store.set_domain_profile("light", _profile("light", "read_only"))
    _content, outcome, _ = await _call_tool(
        "set_entity",
        {"entity_id": entity_id, "changes": {"new_entity_id": "light.read_only_refused"}},
        _access_key(),
        hass,
        data,
    )
    assert outcome == "denied"

    data.mesa.store.set_domain_profile(
        "light", _profile("light", "prohibited", "Advisory prohibition")
    )
    content, outcome, _ = await _call_tool(
        "set_entity",
        {"entity_id": entity_id, "changes": {"new_entity_id": "light.advisory_rename"}},
        _access_key(),
        hass,
        data,
    )
    assert outcome == "allowed"
    assert _body(content)["mesa_advisory"]
    assert er.async_get(hass).async_get("light.advisory_rename") is not None


async def test_enforced_prohibited_blocks_registry_identity_actions(
    hass: HomeAssistant, registry_entity
):
    entity_id, _device_id = registry_entity
    data = await _data(hass, "enforced")
    data.mesa.store.set_domain_profile("light", _profile("light", "prohibited"))
    for tool_name, args in (
        ("set_entity", {"entity_id": entity_id, "changes": {"new_entity_id": "light.blocked"}}),
        ("delete_entity", {"entity_id": entity_id}),
    ):
        _content, outcome, _ = await _call_tool(
            tool_name, args, _access_key(), hass, data
        )
        assert outcome == "denied"
    assert er.async_get(hass).async_get(entity_id) is not None


@pytest.mark.parametrize(
    "tool_name,args_factory",
    [
        (
            "set_entity",
            lambda entity_id: {
                "entity_id": entity_id,
                "changes": {"new_entity_id": "light.confirmed_rename"},
            },
        ),
        ("delete_entity", lambda entity_id: {"entity_id": entity_id}),
    ],
)
async def test_enforced_confirm_uses_one_normal_registry_approval(
    hass: HomeAssistant, registry_entity, tool_name, args_factory
):
    entity_id, _device_id = registry_entity
    data = await _data(hass, "enforced")
    data.mesa.store.set_domain_profile(
        "light", _profile("light", "confirm", "Operator must confirm registry identity")
    )
    gate = AsyncMock(return_value=None)
    create = AsyncMock(return_value=({}, "pending_approval", "approval-1"))
    relationship_preview = AsyncMock(
        return_value={"consumers": [], "consumer_count": 0, "searched": []}
    )
    with (
        patch("custom_components.phoenix_mcp.tools.registry._gate", gate),
        patch(
            "custom_components.phoenix_mcp.tools.registry._create_registry_mesa_approval",
            create,
        ),
        patch(
            "custom_components.phoenix_mcp.tools.registry._registry_relationship_preview",
            relationship_preview,
        ),
    ):
        _content, outcome, _ = await _call_tool(
            tool_name, args_factory(entity_id), _access_key(), hass, data
        )

    assert outcome == "pending_approval"
    gate.assert_awaited_once()
    create.assert_awaited_once()
    saved_args = create.await_args.kwargs["args"]
    assert isinstance(saved_args[_REGISTRY_MESA_FINGERPRINT], str)
    mesa_preview = create.await_args.kwargs["diff"]["preview"]["mesa"]
    assert mesa_preview["decision"] == "confirm"
    assert mesa_preview["effective_rule"]["provided_by_level"] == "domain"
    assert mesa_preview["effective_rule"]["control_reason"].startswith("Operator")
    assert er.async_get(hass).async_get(entity_id) is not None


async def test_a_denial_names_no_entity_the_key_cannot_read(hass: HomeAssistant, registry_entity):
    entity_id, _device_id = registry_entity
    hass.states.async_set("person.alice", "home")
    data = await _data(hass, "enforced")
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited", "guards person.alice at home"))
    content, outcome, _ = await _call_tool(
        "delete_entity", {"entity_id": entity_id}, _access_key(), hass, data, request_id="rid",
    )
    assert outcome == "denied"
    text = content["content"][0]["text"]
    assert "person.alice" not in text
    assert "guards <redacted-entity> at home" in _body(content)["mesa"]["effective_rule"]["control_reason"]


@pytest.mark.parametrize("mode", ["confirm", "autonomous", "prohibited"])
async def test_every_decision_is_redacted_and_only_the_admin_rule_keeps_the_reason(hass: HomeAssistant, registry_entity, mode):
    entity_id, _device_id = registry_entity
    hass.states.async_set("person.alice", "home")
    data = await _data(hass, "enforced")
    data.mesa.store.set(entity_id, _profile(entity_id, mode, "guards person.alice at home"))
    decision = evaluate_registry_action(data, _access_key(), entity_id, action="delete")
    assert "person.alice" not in json.dumps([decision.reason, decision.effective_rule, decision.warnings])
    assert decision.admin_rule["control_reason"] == "guards person.alice at home"


async def test_the_approval_diff_keeps_the_reason_as_the_operator_wrote_it(hass: HomeAssistant, registry_entity):
    entity_id, _device_id = registry_entity
    hass.states.async_set("person.alice", "home")
    data = await _data(hass, "enforced")
    data.mesa.store.set(entity_id, _profile(entity_id, "confirm", "guards person.alice at home"))
    create = AsyncMock(return_value=(_tool_success("pending"), "pending_approval", entity_id))
    with patch("custom_components.phoenix_mcp.tools.registry._create_registry_mesa_approval", create):
        await _call_tool("delete_entity", {"entity_id": entity_id}, _access_key(), hass, data, request_id="rid")
    preview = create.await_args.kwargs["diff"]["preview"]["mesa"]
    assert preview["effective_rule"]["control_reason"] == "guards person.alice at home"


_ENTITY_ROW = {
    "entity_id": "light.a", "effective_rule": {"control_reason": "guards <redacted-entity>"},
    "admin_rule": {"control_reason": "guards person.alice"}, "profile_fingerprint": "f" * 64,
}


@pytest.mark.parametrize("preview", [
    lambda admin: _device_mesa_preview(
        SimpleNamespace(decision="deny", actions=[], entities=[_ENTITY_ROW], warnings=[], profile_fingerprint="f" * 64),
        admin=admin,
    ),
    lambda admin: _config_entry_mesa_preview(
        SimpleNamespace(
            decision="deny", actions=[], warnings=[], fingerprint="f" * 64,
            entities=[{**_ENTITY_ROW, "explanation": {"reason": "guards person.alice"}}],
        ),
        admin=admin,
    ),
    lambda admin: _helper_mesa_preview(
        SimpleNamespace(
            action="delete", decision="deny", warnings=(), fingerprint="f" * 64,
            decisions=(("light.a", RegistryMesaDecision(
                decision="deny", rule="r", reason="redacted", effective_rule={"control_reason": "guards <redacted-entity>"},
                raw_control_reason="guards person.alice",
            )),),
        ),
        admin=admin,
    ),
], ids=["device", "config_entry", "helper"])
def test_only_the_approval_diff_of_a_shared_preview_holds_the_raw_reason(preview):
    assert "person.alice" not in json.dumps(preview(False))
    assert "person.alice" in json.dumps(preview(True))


async def test_capability_confirmation_already_satisfies_mesa_confirmation(
    hass: HomeAssistant, registry_entity
):
    entity_id, _device_id = registry_entity
    data = await _data(hass, "enforced")
    data.mesa.store.set_domain_profile("light", _profile("light", "confirm"))
    gate = AsyncMock(return_value=({}, "pending_approval", "approval-capability"))
    create = AsyncMock()
    with (
        patch("custom_components.phoenix_mcp.tools.registry._gate", gate),
        patch(
            "custom_components.phoenix_mcp.tools.registry._create_registry_mesa_approval",
            create,
        ),
    ):
        _content, outcome, _ = await _call_tool(
            "set_entity",
            {"entity_id": entity_id, "changes": {"new_entity_id": "light.one_approval"}},
            _access_key(cap="confirm"),
            hass,
            data,
        )

    assert outcome == "pending_approval"
    gate.assert_awaited_once()
    assert isinstance(
        gate.await_args.kwargs["args"][_REGISTRY_MESA_FINGERPRINT], str
    )
    create.assert_not_awaited()
    assert er.async_get(hass).async_get(entity_id) is not None


async def test_restrictive_parent_cannot_be_loosened_by_entity_profile(
    hass: HomeAssistant, registry_entity
):
    entity_id, device_id = registry_entity
    data = await _data(hass, "enforced")
    data.mesa.store.set_device_profile(
        device_id, _profile(device_id, "read_only", "Device is immutable")
    )
    data.mesa.store.set(entity_id, _profile(entity_id, "autonomous"))

    decision = evaluate_registry_action(
        data, _access_key(), entity_id, action="delete"
    )
    assert decision.decision == "deny"
    assert decision.effective_rule["control_mode"] == "read_only"
    assert decision.effective_rule["provided_by_level"] == "device"


async def test_permission_and_mesa_are_rechecked_for_approved_execution(
    hass: HomeAssistant, registry_entity
):
    entity_id, _device_id = registry_entity
    data = await _data(hass, "enforced")
    access_key = _access_key()
    data.mesa.store.set_domain_profile("light", _profile("light", "confirm"))
    initial = evaluate_registry_action(data, access_key, entity_id, action="rename")
    approved_args = {
        "entity_id": entity_id,
        "new_entity_id": "light.pending_rename",
        _REGISTRY_MESA_FINGERPRINT: initial.profile_fingerprint,
    }

    data.mesa.store.set_domain_profile(
        "light", _profile("light", "read_only", "Policy tightened while pending")
    )
    _content, outcome, _ = await async_execute_approved_tool(
        "set_entity", approved_args, access_key, hass, data
    )
    assert outcome == "denied"
    assert er.async_get(hass).async_get(entity_id) is not None

    data.mesa.store.set_domain_profile(
        "light", _profile("light", "confirm", "Confirmation reason changed")
    )
    _content, outcome, _ = await async_execute_approved_tool(
        "set_entity", approved_args, access_key, hass, data
    )
    assert outcome == "denied"
    assert er.async_get(hass).async_get(entity_id) is not None

    data.store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    access_key.permissions.domains["light"] = PermissionNode(state="RED")
    _content, outcome, _ = await async_execute_approved_tool(
        "set_entity",
        {"entity_id": entity_id, "new_entity_id": "light.permission_denied"},
        access_key,
        hass,
        data,
    )
    assert outcome == "denied"
    assert er.async_get(hass).async_get(entity_id) is not None


async def test_a_stored_profile_sends_a_rename_to_the_mesa_gate_instead_of_blocking_it(
    hass: HomeAssistant, registry_entity
):
    entity_id, _ = registry_entity
    data = await _data(hass, "enforced")
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))
    content, outcome, _ = await _call_tool(
        "set_entity",
        {"entity_id": entity_id, "changes": {"new_entity_id": "light.renamed"}},
        _access_key(cap="allow"), hass, data,
    )
    assert outcome == "denied", content
    assert "blockers" not in content["content"][0]["text"]
    assert er.async_get(hass).async_get(entity_id) is not None


async def test_rename_blockers_cover_identity_keyed_phoenix_configuration(
    hass: HomeAssistant, registry_entity
):
    entity_id, device_id = registry_entity
    data = await _data(hass, "enforced")
    configured = _access_key()
    configured.permissions.entities[entity_id] = PermissionNode(state="GREEN")
    configured.presets.append(snapshot_preset(configured, "Entity preset"))
    data.store.list_access_keys.return_value = [configured]
    data.store.get_entity_hints.return_value = {entity_id: {"hint": "Keep identity"}}
    data.store.get_pending_approvals.return_value = [
        {
            "id": "approval-other",
            "status": "pending",
            "tool_name": "call_service",
            "args": {"entity_id": entity_id},
        }
    ]
    # A profile moves with the entity, and an inherited one governs the operation.
    data.mesa.store.set(entity_id, _profile(entity_id, "confirm"))
    data.mesa.store.set_domain_profile("light", _profile("light", "confirm"))
    data.mesa.store.set_device_profile(device_id, _profile(device_id, "confirm"))

    references = _entity_identity_references(data, entity_id)
    assert {item["kind"] for item in references} == {
        "access_key_permission",
        "preset_permission",
        "global_hint",
        "pending_approval",
    }

    gate = AsyncMock()
    with patch("custom_components.phoenix_mcp.tools.registry._gate", gate):
        content, outcome, _ = await _call_tool(
            "set_entity",
            {"entity_id": entity_id, "changes": {"new_entity_id": "light.blocked_by_config"}},
            _access_key(cap="confirm"),
            hass,
            data,
        )
    assert outcome == "invalid_request"
    assert {item["kind"] for item in _body(content)["blockers"]} == {
        item["kind"] for item in references
    }
    gate.assert_not_awaited()


@pytest.mark.parametrize(
    "new_entity_id,message",
    [
        ("switch.other_domain", "stay in the light domain"),
        ("not-an-entity-id", "valid entity ID"),
        ("light.occupied", "already occupied"),
    ],
)
async def test_invalid_or_occupied_rename_is_rejected_before_approval(
    hass: HomeAssistant, registry_entity, new_entity_id, message
):
    entity_id, _device_id = registry_entity
    er.async_get(hass).async_get_or_create(
        "light", "test_integration", "occupied", suggested_object_id="occupied"
    )
    data = await _data(hass, "off")
    gate = AsyncMock()
    with patch("custom_components.phoenix_mcp.tools.registry._gate", gate):
        content, outcome, _ = await _call_tool(
            "set_entity",
            {"entity_id": entity_id, "changes": {"new_entity_id": new_entity_id}},
            _access_key(cap="confirm"),
            hass,
            data,
        )
    assert outcome == "invalid_request"
    assert message in content["content"][0]["text"]
    gate.assert_not_awaited()
    # Refused before the registry write, so an agent turn gets it back with no barrier.
    assert isinstance(content, RefusedBeforeWrite)


async def test_a_failure_after_the_registry_write_starts_is_not_typed_as_refused(
    hass: HomeAssistant, registry_entity
):
    entity_id, _device_id = registry_entity
    data = await _data(hass, "off")
    with patch.object(er.EntityRegistry, "async_update_entity", side_effect=RuntimeError("boom")):
        content, outcome, _ = await _call_tool(
            "set_entity", {"entity_id": entity_id, "changes": {"name": "Renamed"}},
            _access_key(cap="allow"), hass, data,
        )
    assert "Failed to update entity" in content["content"][0]["text"]
    assert not isinstance(content, RefusedBeforeWrite)


# --- moves out of a profiled area, and entity enable/disable --------------------


def _device_key(device_id: str, **caps) -> AccessKeyRecord:
    key = _access_key(**caps)
    key.permissions = PermissionTree(
        devices={device_id: PermissionNode(state="GREEN")}, domains={"light": PermissionNode(state="GREEN")},
    )
    return key


def _place(hass: HomeAssistant, entity_id: str, area_id: str) -> None:
    """Give an entity its own area, which Home Assistant allows only with its own name."""
    er.async_get(hass).async_update_entity(entity_id, name="Registry light", area_id=area_id)


@pytest.fixture
def areas(hass: HomeAssistant) -> tuple[str, str]:
    registry = ar.async_get(hass)
    return registry.async_create("Vault").id, registry.async_create("Hall").id


async def test_an_entity_cannot_be_moved_out_of_a_prohibited_area(hass: HomeAssistant, registry_entity, areas):
    entity_id, _device_id = registry_entity
    vault, hall = areas
    _place(hass, entity_id, vault)
    data = await _data(hass, "enforced")
    data.mesa.store.set_area_profile(vault, _profile(vault, "prohibited"))

    content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"area_id": hall}}, _access_key(), hass, data,
    )

    assert outcome == "denied"
    assert "move_area" in _body(content)["error"]
    assert er.async_get(hass).async_get(entity_id).area_id == vault


async def test_clearing_an_entity_area_is_a_move_out_of_it(hass: HomeAssistant, registry_entity, areas):
    entity_id, _device_id = registry_entity
    vault, _hall = areas
    _place(hass, entity_id, vault)
    data = await _data(hass, "enforced")
    data.mesa.store.set_area_profile(vault, _profile(vault, "prohibited"))

    _content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"area_id": None}}, _access_key(), hass, data,
    )

    assert outcome == "denied"
    assert er.async_get(hass).async_get(entity_id).area_id == vault


async def test_moving_into_a_profiled_area_only_tightens(hass: HomeAssistant, registry_entity, areas):
    entity_id, _device_id = registry_entity
    vault, hall = areas
    _place(hass, entity_id, hall)
    data = await _data(hass, "enforced")
    data.mesa.store.set_domain_profile("light", _profile("light", "autonomous"))
    data.mesa.store.set_area_profile(vault, _profile(vault, "prohibited"))

    _content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"area_id": vault}}, _access_key(), hass, data,
    )

    assert outcome == "allowed"
    assert er.async_get(hass).async_get(entity_id).area_id == vault


async def test_a_room_change_without_area_profiles_ignores_mesa(hass: HomeAssistant, registry_entity, areas):
    """A restrictive domain default says nothing about which room an entity is in."""
    entity_id, _device_id = registry_entity
    vault, hall = areas
    _place(hass, entity_id, vault)
    data = await _data(hass, "enforced")
    data.mesa.store.set_domain_profile("light", _profile("light", "prohibited"))

    _content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"area_id": hall}}, _access_key(), hass, data,
    )

    assert outcome == "allowed"


async def test_a_confirm_area_routes_the_move_to_an_approval_that_binds_it(hass: HomeAssistant, registry_entity, areas):
    entity_id, _device_id = registry_entity
    vault, hall = areas
    _place(hass, entity_id, vault)
    data = await _data(hass, "enforced")
    data.mesa.store.set_domain_profile("light", _profile("light", "autonomous"))
    data.mesa.store.set_area_profile(vault, _profile(vault, "confirm"))
    create = AsyncMock(return_value=({}, "pending_approval", "approval"))
    key = _access_key()

    with (patch("custom_components.phoenix_mcp.tools.registry._create_registry_mesa_approval", create)):
        await _call_tool("set_entity", {"entity_id": entity_id, "changes": {"area_id": hall}}, key, hass, data)

    saved = create.await_args.kwargs["args"]
    assert saved[_REGISTRY_MESA_FINGERPRINT]
    assert er.async_get(hass).async_get(entity_id).area_id == vault
    _content, outcome, _ = await async_execute_approved_tool("set_entity", saved, key, hass, data)
    assert outcome == "allowed"
    assert er.async_get(hass).async_get(entity_id).area_id == hall


@pytest.mark.parametrize(("enabled", "initially_disabled"), [(False, False), (True, True)])
async def test_entity_enable_and_disable_are_gated(hass: HomeAssistant, registry_entity, enabled, initially_disabled):
    entity_id, _device_id = registry_entity
    registry = er.async_get(hass)
    if initially_disabled:
        registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    data = await _data(hass, "enforced")
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))

    _content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"enabled": enabled}}, _access_key(), hass, data,
    )

    assert outcome == "denied"
    assert (registry.async_get(entity_id).disabled_by is not None) is initially_disabled


async def test_an_enable_that_changes_nothing_has_no_action_to_gate(hass: HomeAssistant, registry_entity):
    entity_id, _device_id = registry_entity
    data = await _data(hass, "enforced")
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))

    _content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"enabled": True}}, _access_key(), hass, data,
    )

    assert outcome == "allowed"


async def test_the_strictest_action_in_one_edit_decides(hass: HomeAssistant, registry_entity, areas):
    entity_id, _device_id = registry_entity
    vault, hall = areas
    _place(hass, entity_id, vault)
    data = await _data(hass, "advisory")
    data.mesa.store.set_area_profile(vault, _profile(vault, "read_only"))

    content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"area_id": hall, "enabled": False}}, _access_key(), hass, data,
    )

    assert outcome == "denied"
    assert "disable" in _body(content)["error"] and "move_area" in _body(content)["error"]


async def test_a_device_cannot_be_moved_out_of_a_prohibited_area(hass: HomeAssistant, registry_entity, areas):
    entity_id, device_id = registry_entity
    vault, hall = areas
    devices = dr.async_get(hass)
    devices.async_update_device(device_id, area_id=vault)
    data = await _data(hass, "enforced")
    data.mesa.store.set_area_profile(vault, _profile(vault, "prohibited"))

    content, outcome, _ = await _call_tool(
        "set_device", {"device_id": device_id, "area_id": hall}, _device_key(device_id), hass, data,
    )

    assert outcome == "denied", content
    assert devices.async_get(device_id).area_id == vault


async def test_a_confirm_area_routes_a_device_move_to_an_approval(hass: HomeAssistant, registry_entity, areas):
    _entity_id, device_id = registry_entity
    vault, hall = areas
    devices = dr.async_get(hass)
    devices.async_update_device(device_id, area_id=vault)
    data = await _data(hass, "enforced")
    data.mesa.store.set_domain_profile("light", _profile("light", "autonomous"))
    data.mesa.store.set_area_profile(vault, _profile(vault, "confirm"))
    create = AsyncMock(return_value=({}, "pending_approval", "approval"))
    key = _device_key(device_id)

    with patch("custom_components.phoenix_mcp.tools.registry._create_registry_mesa_approval", create):
        _content, outcome, _ = await _call_tool("set_device", {"device_id": device_id, "area_id": hall}, key, hass, data)

    assert outcome == "pending_approval"
    assert devices.async_get(device_id).area_id == vault
    _content, outcome, _ = await async_execute_approved_tool(
        "set_device", create.await_args.kwargs["args"], key, hass, data,
    )
    assert outcome == "allowed"
    assert devices.async_get(device_id).area_id == hall


async def test_a_device_move_gates_only_the_entities_that_leave(hass: HomeAssistant, registry_entity, areas):
    """An entity with an area of its own stays put, so its mode does not refuse the move."""
    entity_id, device_id = registry_entity
    vault, hall = areas
    devices = dr.async_get(hass)
    devices.async_update_device(device_id, area_id=vault)
    _place(hass, entity_id, hall)
    data = await _data(hass, "enforced")
    data.mesa.store.set_area_profile(vault, _profile(vault, "prohibited"))
    data.mesa.store.set_domain_profile("light", _profile("light", "autonomous"))

    _content, outcome, _ = await _call_tool(
        "set_device", {"device_id": device_id, "area_id": hall}, _device_key(device_id), hass, data,
    )

    assert outcome == "allowed"
    assert devices.async_get(device_id).area_id == hall


async def test_the_registry_gate_fails_closed_without_a_runtime(hass: HomeAssistant, registry_entity, areas):
    entity_id, _device_id = registry_entity
    _vault, hall = areas
    data = await _data(hass, "advisory")
    data.mesa = None
    data.mesa_setup_failed = True

    _content, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"area_id": hall}}, _access_key(), hass, data,
    )

    assert outcome == "denied"


@pytest.mark.parametrize("mesa_mode, reason", [
    ("advisory", "Manage entity registry capability is set to Confirm"),
    ("enforced", "MESA requires approval"),
])
async def test_the_delete_approval_says_what_asks_for_it(
    hass: HomeAssistant, registry_entity, mesa_mode, reason
):
    """A waiting approval never shows MESA's 'proceeding' warning, and names its cause."""
    entity_id, _device_id = registry_entity
    data = await _data(hass, mesa_mode)
    data.mesa.store.set(entity_id, _profile(entity_id, "confirm"))
    gate = AsyncMock(return_value=None)
    with patch("custom_components.phoenix_mcp.tools.registry._gate", gate), \
            patch("custom_components.phoenix_mcp.tools.registry._create_registry_mesa_approval",
                  AsyncMock(return_value=({}, "pending_approval", entity_id))) as mesa_approval:
        await _call_tool("delete_entity", {"entity_id": entity_id}, _access_key(cap="confirm"), hass, data)
    builder = gate.call_args.kwargs["diff"]
    preview = (await builder())["preview"]
    assert next(iter(preview)) == "approval_reason"
    assert reason in preview["approval_reason"]
    assert "proceeding" not in json.dumps(preview)
    if mesa_mode == "advisory":
        assert "is not what asks for this approval" in preview["mesa"]["note"]
    else:
        mesa_approval.assert_awaited_once()


@pytest.mark.parametrize("handler_refuses, executor_fails, refused", [
    (True, False, True),    # the handler refused before its executor: nothing written
    (False, True, False),   # the executor failed after entering: outcome stays unknown
    (False, False, False),  # success is not a refusal
])
async def test_an_executor_boundary_decides_whether_a_refusal_wrote_nothing(
    hass: HomeAssistant, handler_refuses, executor_fails, refused
):
    """Any write tool with a @write_boundary executor is tracked, not only hand-instrumented ones."""
    from custom_components.phoenix_mcp.request_authority import write_boundary
    from custom_components.phoenix_mcp.tool_catalog import _EXECUTOR_REGISTRY, _TOOL_HANDLERS
    from custom_components.phoenix_mcp.tool_common import _tool_error

    @write_boundary
    async def executor(args, access_key, hass):
        return (_tool_error("failed midway") if executor_fails else _tool_success({"ok": True})), "allowed", "x"

    async def handler(args, access_key, hass):
        if handler_refuses:
            return _tool_error("bad input"), "invalid_request", "x"
        return await executor(args, access_key, hass)

    data = await _data(hass, "off")
    name = "set_label_or_category"
    from custom_components.phoenix_mcp import tool_catalog
    with patch.dict(_TOOL_HANDLERS, {name: (handler, (), {})}), \
            patch.dict(_EXECUTOR_REGISTRY, {name: executor}), \
            patch.object(tool_catalog, "_EXECUTOR_TRACKED", {(name, handler)}), \
            patch.object(tool_dispatch_module, "_tool_capability_denied", return_value=False), \
            patch.object(tool_dispatch_module, "unknown_argument_error", return_value=None), \
            patch.object(tool_dispatch_module, "scalar_arguments", side_effect=lambda _n, a, **_k: (a, None)):
        content, _outcome, _ = await _call_tool(name, {}, _access_key(cap="allow"), hass, data)
    assert isinstance(content, RefusedBeforeWrite) is refused


async def test_the_agent_hears_what_the_delete_approval_page_shows_without_its_contents(
    hass: HomeAssistant, registry_entity
):
    """The agent could not explain a page it never saw; it now gets an outline, never the names."""
    from custom_components.phoenix_mcp.tool_common import _review_outline
    entity_id, _device_id = registry_entity
    data = await _data(hass, "advisory")
    gate = AsyncMock(return_value=None)
    with patch("custom_components.phoenix_mcp.tools.registry._gate", gate):
        await _call_tool("delete_entity", {"entity_id": entity_id}, _access_key(cap="confirm"), hass, data)
    diff = await gate.call_args.kwargs["diff"]()
    diff["preview"]["relationships"]["consumers"] = [{"kind": "automation", "id": "1", "name": "Secret Vault Routine"}]
    outline = _review_outline(SimpleNamespace(diff=diff, cap_name="cap_registry_write"))
    assert outline["summary"] == diff["summary"]
    assert "Manage entity registry capability is set to Confirm" in outline["why_approval"]
    assert "what uses the target (automations, scripts, dashboards)" in outline["also_shows"]
    assert "Secret Vault Routine" not in json.dumps(outline)
