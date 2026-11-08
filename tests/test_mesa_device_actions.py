"""MESA reaches whole-device radio and firmware actions, and every mutating tool.

The behavioural half drives each device action through its tool and its executor
against a real MESA runtime, with the radio or Device Builder seam recording
whether anything was sent. The radio half takes its tool list from the catalog, so
a new radio tool joins every case or fails here. The structural half walks the
call graph from every tool: a tool that is not read-only must reach a MESA gate or
state why it has no entity for MESA to judge, and a read-only tool must reach no
dispatch site.
"""

from __future__ import annotations

import ast
import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import radio as radio_backend
from custom_components.phoenix_mcp import tool_defs, tool_dispatch
from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_catalog import _EXECUTOR_REGISTRY, _TOOL_HANDLERS
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.tool_common import MESA_DENIED_MESSAGE
from custom_components.phoenix_mcp.tools import radio as radio_tools
from tests.runtime_fixtures import FIRMWARE_FILE as FILE, FIRMWARE_JOB as JOB, FakeBuilder, firmware_dash as _dash
from tests.runtime_fixtures import esphome_device_info, esphome_entry as _esphome_entry, esphome_runtime as _runtime

_RADIO = "custom_components.phoenix_mcp.tools.radio"
_DASHBOARD = "custom_components.phoenix_mcp.tools.esphome._esphome_dashboard"
_COMMAND = "custom_components.phoenix_mcp.tools.esphome.async_builder_command"

_IEEE = "00:11:22:33:44:55:66:77"

# Mutating tools that act on nothing MESA can judge, each with the reason. A
# profile governs an entity through its device, area, integration and domain, so
# a tool that targets configuration text or the instance has no entity to resolve.
_NO_ENTITY_TO_JUDGE = {
    "add_dashboard_card": "dashboard configuration; it displays entities and actuates none",
    "analyze_esphome_memory": "compiles and reports; nothing reaches a device",
    "cancel_esphome_job": "stops a Device Builder job and starts nothing",
    "check_esphome_connectivity": "a network probe of a device the key can read",
    "clean_esphome_build": "deletes build artifacts in the Device Builder",
    "compile_esphome_firmware": "builds an image; flashing is install_esphome_firmware",
    "create_automation": "configuration text; it actuates nothing until it runs",
    "create_backup": "instance-wide, with no entity target",
    "create_blueprint": "configuration text with no entity target",
    "create_dashboard": "dashboard configuration",
    "create_scene": "configuration text; activating it is a gated service call",
    "create_script": "configuration text; running it is a gated service call",
    "delete_automation": "configuration text",
    "delete_blueprint": "configuration text",
    "delete_dashboard": "dashboard configuration",
    "delete_dashboard_card": "dashboard configuration",
    "delete_scene": "configuration text",
    "delete_script": "configuration text",
    "edit_automation": "configuration text",
    "edit_blueprint": "configuration text",
    "edit_dashboard": "dashboard configuration",
    "edit_dashboard_card": "dashboard configuration",
    "edit_energy_config": "energy preferences, which actuate nothing",
    "edit_scene": "configuration text",
    "edit_script": "configuration text",
    "patch_dashboard": "dashboard configuration",
    "permit_zigbee_join": "opens the coordinator's join window; it acts on the network, not a device",
    "restart_ha": "no entity target",
    "scan_zigbee_topology": "reads the mesh and changes no device",
    "set_dashboard_config": "dashboard configuration",
    "set_dashboard_resource": "a dashboard's script or stylesheet URL, with no entity target",
    "set_integration_log_level": "log verbosity, with no entity target",
    "set_label_or_category": "registry metadata; no profile resolves through a label or a category",
}



def _catalog_radio_tools() -> tuple[str, ...]:
    """The mutating tools the radio module serves that act on a device, as the catalog lists them."""
    names = {
        tool_defs._NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(public, public)
        for public, hints in tool_defs._TOOL_ANNOTATIONS.items() if not hints.get("readOnlyHint")
    }
    return tuple(sorted(
        name for name in names
        if name not in _NO_ENTITY_TO_JUDGE and _TOOL_HANDLERS[name][0].__module__ == radio_tools.__name__
    ))


RADIO_TOOLS = _catalog_radio_tools()


def _profile(key: str, mode: str) -> SemanticProfile:
    return SemanticProfile.from_dict(
        key,
        {"semantic_profile": {"operational_boundaries": {"control_mode": mode, "control_reason": "fixture"}}},
        default_origin=MetadataOrigin.USER,
    )


def _access_key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="device-actions", access_key_hash="x", created_at=utcnow(), created_by="t",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
        **{"cap_radio_write": "allow", "cap_physical_control": "allow", "cap_esphome_flash": "allow",
           "cap_esphome_yaml": "allow", **caps},
    )


async def _data(hass, mode: str = "enforced") -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    pending: list = []
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_settings.return_value = GlobalSettings(mesa_mode=mode)
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=await async_setup_mesa(hass, mode))
    hass.data[DOMAIN] = data
    return data


@pytest.fixture
def zigbee_device(hass):
    entry = MockConfigEntry(domain="zha", entry_id="zigbee")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", _IEEE)}, name="Node",
    )
    light = er.async_get(hass).async_get_or_create(
        "light", "zha", "node-light", config_entry=entry, device_id=device.id, suggested_object_id="node",
    )
    hass.states.async_set(light.entity_id, "on")
    return device, light.entity_id, entry


def _resolved(device) -> SimpleNamespace:
    return SimpleNamespace(device=device, backend=radio_backend.BACKEND_ZHA, ieee=_IEEE)


def _group_change(tool: str, operation: str, device, *, group: bool) -> SimpleNamespace:
    return SimpleNamespace(
        tool_name=tool, operation=operation, backend=radio_backend.BACKEND_ZHA, name="Group",
        group=SimpleNamespace(
            entity_id="light.zigbee_group", configuration={"name": "Group", "members": []}, visible_members=(),
            hidden_member_count=0,
        ) if group else None,
        members=(_resolved(device),), endpoints=(1,), before_hash="",
    )


def _radio_cases(device):
    """Per tool: the backend call that reaches the radio, and the resolvers that stand in for its network."""
    change = SimpleNamespace(
        device=device, ieee=_IEEE, changes={"legacy": False},
        desired={"endpoint": 1, "cluster": "genOnOff", "attribute": "onOff"},
        entry={}, before={"endpoints": [{"endpoint": 1, "configured_reportings": []}]}, after={}, before_hash="",
        property_name="mode", schema={},
    )
    binding = SimpleNamespace(
        operation="bind", backend=radio_backend.BACKEND_ZHA, source=_resolved(device), target=_resolved(device),
        source_endpoint=None, target_endpoint=None, clusters=(), source_entry=None, target_entry=None,
        source_before=None, target_before=None, source_hash=None, target_hash=None,
    )
    groups = {
        "create_zigbee_group": ("async_create_zigbee_group", _group_change("create_zigbee_group", "create", device, group=False)),
        "set_zigbee_group_members": ("async_set_zigbee_group_members", _group_change("set_zigbee_group_members", "add", device, group=True)),
        "remove_zigbee_group": ("async_remove_zigbee_group", _group_change("remove_zigbee_group", "remove", device, group=True)),
    }
    return {
        "remove_zigbee_device": ("async_remove_device", {}),
        "reconfigure_zigbee_device": ("async_reconfigure_device", {}),
        "set_zigbee_device_options": ("async_set_z2m_device_options", {
            "_resolve_z2m_option_change": AsyncMock(return_value=change)}),
        "configure_zigbee_reporting": ("async_configure_z2m_reporting", {
            "_resolve_zigbee_reporting_change": AsyncMock(return_value=change)}),
        "set_zigbee_device_property": ("async_set_z2m_exposed_property", {
            "_resolve_z2m_property_change": AsyncMock(return_value=change)}),
        "set_zigbee_binding": ("async_set_zha_binding", {
            "_resolve_zigbee_binding_change": AsyncMock(return_value=binding)}),
        **{tool: (backend, {"_GROUP_CHANGE_RESOLVERS": {tool: AsyncMock(return_value=group_change)}})
           for tool, (backend, group_change) in groups.items()},
    }


class _RadioSeam:
    """Patch the tool's resolver and record the one call that reaches the radio."""

    def __init__(self, tool: str, device) -> None:
        backend, resolvers = _radio_cases(device)[tool]
        self.backend = AsyncMock(side_effect=RuntimeError("stop after dispatch"))
        self._patches = [patch(f"{_RADIO}.radio_backend.{backend}", self.backend)]
        for name, value in resolvers.items():
            self._patches.append(
                patch.dict(f"{_RADIO}.{name}", value) if isinstance(value, dict) else patch(f"{_RADIO}.{name}", value)
            )

    def __enter__(self):
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *_exc):
        for item in self._patches:
            item.stop()


async def _run(tool: str, args: dict, key, hass, data, *, via_executor: bool = False):
    """Enter at the handler, past schema checks that the seams above stand in for."""
    if via_executor:
        return await _EXECUTOR_REGISTRY[tool](args, key, hass, data)
    handler, _context, bound = _TOOL_HANDLERS[tool]
    return await handler(args, key, hass, data, request_id="rid", client_ip=None, **bound)


@pytest.mark.parametrize("via_executor", [False, True], ids=["tool", "executor"])
@pytest.mark.parametrize("tool", RADIO_TOOLS)
async def test_a_prohibited_device_refuses_every_radio_action(hass, zigbee_device, tool, via_executor):
    device, _light, _entry = zigbee_device
    data = await _data(hass)
    data.mesa.store.set_device_profile(device.id, _profile(device.id, "prohibited"))
    with _RadioSeam(tool, device) as seam:
        result = await _run(tool, {"device_id": device.id}, _access_key(), hass, data, via_executor=via_executor)
    assert result[1] == "denied"
    assert result[0]["content"][0]["text"] == MESA_DENIED_MESSAGE
    seam.backend.assert_not_awaited()


@pytest.mark.parametrize("tool", RADIO_TOOLS)
async def test_a_prohibited_device_queues_no_capability_approval(hass, zigbee_device, tool):
    """The operator must not be asked to approve what MESA would refuse anyway."""
    device, _light, _entry = zigbee_device
    data = await _data(hass)
    data.mesa.store.set_device_profile(device.id, _profile(device.id, "prohibited"))
    with _RadioSeam(tool, device):
        result = await _run(tool, {"device_id": device.id}, _access_key(cap_radio_write="confirm"), hass, data)
    assert result[1] == "denied"
    assert data.store.get_pending_approvals() == []


@pytest.mark.parametrize("tool", RADIO_TOOLS)
async def test_an_entity_the_key_cannot_write_still_protects_the_device(hass, zigbee_device, tool):
    """The action reaches the whole device, so scope does not narrow the evaluation."""
    device, _light, entry = zigbee_device
    hidden = er.async_get(hass).async_get_or_create(
        "lock", "zha", "node-lock", config_entry=entry, device_id=device.id, suggested_object_id="node_lock",
    )
    data = await _data(hass)
    data.mesa.store.set(hidden.entity_id, _profile(hidden.entity_id, "prohibited"))
    with _RadioSeam(tool, device) as seam:
        result = await _run(tool, {"device_id": device.id}, _access_key(), hass, data)
    assert result[1] == "denied"
    seam.backend.assert_not_awaited()


@pytest.mark.parametrize("tool", RADIO_TOOLS)
async def test_a_child_device_profile_protects_its_parent_action(hass, zigbee_device, tool):
    device, _light, entry = zigbee_device
    registry = dr.async_get(hass)
    if not hasattr(registry, "async_get_or_create_child"):
        pytest.skip("this Home Assistant has no child devices")
    child = registry.async_get_or_create_child(
        config_entry_id=entry.entry_id, identifiers={("zha", "part")}, name="Part", parent_device_id=device.id,
    )
    part = er.async_get(hass).async_get_or_create(
        "switch", "zha", "part-switch", config_entry=entry, device_id=child.id, suggested_object_id="part",
    )
    data = await _data(hass)
    data.mesa.store.set(part.entity_id, _profile(part.entity_id, "prohibited"))
    with _RadioSeam(tool, device) as seam:
        result = await _run(tool, {"device_id": device.id}, _access_key(), hass, data)
    assert result[1] == "denied"
    seam.backend.assert_not_awaited()


@pytest.mark.parametrize("tool", RADIO_TOOLS)
async def test_confirm_routes_to_an_exact_approval_that_runs_once(hass, zigbee_device, tool):
    device, light, _entry = zigbee_device
    data = await _data(hass)
    data.mesa.store.set(light, _profile(light, "confirm"))
    key = _access_key()
    with _RadioSeam(tool, device) as seam:
        pending = await _run(tool, {"device_id": device.id}, key, hass, data)
        assert pending[1] == "pending_approval"
        seam.backend.assert_not_awaited()
        approval = data.store.get_pending_approvals()[0]
        assert approval["tool_name"] == tool
        with pytest.raises(RuntimeError, match="stop after dispatch"):
            await async_execute_approved_tool(tool, approval["args"], key, hass, data)
    seam.backend.assert_awaited_once()


@pytest.mark.parametrize("tool", RADIO_TOOLS)
async def test_an_approval_does_not_outlive_a_tightened_policy(hass, zigbee_device, tool):
    device, light, _entry = zigbee_device
    data = await _data(hass)
    data.mesa.store.set(light, _profile(light, "confirm"))
    key = _access_key()
    with _RadioSeam(tool, device) as seam:
        await _run(tool, {"device_id": device.id}, key, hass, data)
        approval = data.store.get_pending_approvals()[0]
        data.mesa.store.set(light, _profile(light, "prohibited"))
        result = await async_execute_approved_tool(tool, approval["args"], key, hass, data)
    assert result[1] == "denied"
    seam.backend.assert_not_awaited()


@pytest.mark.parametrize("tool", RADIO_TOOLS)
async def test_a_mesa_approval_does_not_survive_a_denied_capability(hass, zigbee_device, tool):
    """A MESA approval carries a sentinel capability, so the executor reads the real one."""
    device, light, _entry = zigbee_device
    data = await _data(hass)
    data.mesa.store.set(light, _profile(light, "confirm"))
    with _RadioSeam(tool, device) as seam:
        await _run(tool, {"device_id": device.id}, _access_key(), hass, data)
        approval = data.store.get_pending_approvals()[0]
        result = await async_execute_approved_tool(
            tool, approval["args"], _access_key(cap_radio_write="deny"), hass, data
        )
    assert result[1] == "denied"
    seam.backend.assert_not_awaited()


@pytest.mark.parametrize("tool", [name for name in RADIO_TOOLS if name in ("set_zigbee_group_members", "remove_zigbee_group")])
async def test_a_profile_on_the_group_entity_protects_the_group(hass, zigbee_device, tool):
    device, _light, _entry = zigbee_device
    data = await _data(hass)
    data.mesa.store.set("light.zigbee_group", _profile("light.zigbee_group", "prohibited"))
    with _RadioSeam(tool, device) as seam:
        result = await _run(tool, {"device_id": device.id}, _access_key(), hass, data)
    assert result[1] == "denied"
    seam.backend.assert_not_awaited()


async def test_the_whole_binding_is_judged_through_the_real_resolver(hass, zigbee_device):
    """A key scoped to sensors reaches the device, and must meet the light it cannot write."""
    device, light, entry = zigbee_device
    registry, entities = dr.async_get(hass), er.async_get(hass)
    source = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", "00:11:22:33:44:55:66:01")}, name="Remote",
    )
    battery = entities.async_get_or_create(
        "sensor", "zha", "remote-battery", config_entry=entry, device_id=source.id, suggested_object_id="remote",
    )
    power = entities.async_get_or_create(
        "sensor", "zha", "node-power", config_entry=entry, device_id=device.id, suggested_object_id="node_power",
    )
    for entity_id in (battery.entity_id, power.entity_id):
        hass.states.async_set(entity_id, "1")
    data = await _data(hass)
    data.mesa.store.set(light, _profile(light, "prohibited"))
    for entity_id in (battery.entity_id, power.entity_id):
        data.mesa.store.set(entity_id, _profile(entity_id, "autonomous"))
    key = _access_key()
    key.permissions = PermissionTree(domains={"sensor": PermissionNode(state="GREEN")})
    bind = AsyncMock(return_value={})
    args = {"operation": "bind", "source_device_id": source.id, "target_device_id": device.id}
    with patch(f"{_RADIO}.radio_backend.async_set_zha_binding", bind):
        result = await _call_tool("set_zigbee_binding", args, key, hass, data, request_id="rid")
    assert result[1] == "denied"
    assert result[0]["content"][0]["text"] == MESA_DENIED_MESSAGE
    bind.assert_not_awaited()


@pytest.mark.parametrize(("mode", "outcome", "warned"), [("advisory", "allowed", True), ("enforced", "denied", False)])
async def test_a_binding_to_a_device_with_no_entity_follows_the_whole_device_rule(hass, zigbee_device, mode, outcome, warned):
    device, light, entry = zigbee_device
    bare = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", "aa:bb:cc:dd:ee:ff:00:22")}, name="Bare",
    )
    key = _access_key()
    key.permissions = PermissionTree(
        domains={"light": PermissionNode(state="GREEN")}, devices={bare.id: PermissionNode(state="GREEN")},
    )
    data = await _data(hass, mode)
    data.mesa.store.set(light, _profile(light, "autonomous"))
    bind = AsyncMock(return_value={})
    args = {"operation": "bind", "source_device_id": device.id, "target_device_id": bare.id}
    with patch(f"{_RADIO}.radio_backend.async_set_zha_binding", bind):
        result = await _call_tool("set_zigbee_binding", args, key, hass, data, request_id="rid")
    assert result[1] == outcome, result
    assert bind.await_count == (1 if outcome == "allowed" else 0)
    assert ("could not check" in json.dumps(result[0])) is warned


async def test_advisory_warns_in_the_result_and_runs(hass, zigbee_device):
    device, light, _entry = zigbee_device
    data = await _data(hass, "advisory")
    data.mesa.store.set(light, _profile(light, "prohibited"))
    backend = AsyncMock(return_value={"removed": True})
    with (patch(f"{_RADIO}.radio_backend.async_remove_device", backend),
          patch(f"{_RADIO}.radio_backend.async_confirm_device_removed", AsyncMock(return_value=True))):
        result = await _call_tool("remove_zigbee_device", {"device_id": device.id}, _access_key(), hass, data, request_id="rid")
    assert result[1] == "allowed"
    backend.assert_awaited_once()
    body = json.loads(result[0]["content"][0]["text"])
    assert body["removed"] is True
    assert any("prohibited" in warning for warning in body["mesa_advisory"])


async def test_an_advisory_on_a_hidden_entity_of_the_device_does_not_name_it(hass, zigbee_device):
    """A device holds entities the key may not see, so its advisories turn generic, as call_service's do."""
    device, light, entry = zigbee_device
    hidden = er.async_get(hass).async_get_or_create(
        "lock", "zha", "node-lock", config_entry=entry, device_id=device.id, suggested_object_id="node_lock",
    )
    data = await _data(hass, "advisory")
    data.mesa.store.set(hidden.entity_id, _profile(hidden.entity_id, "prohibited"))
    data.mesa.store.set(light, _profile(light, "prohibited"))
    key = _access_key()
    key.permissions = PermissionTree(entities={light: PermissionNode(state="GREEN")})
    with (patch(f"{_RADIO}.radio_backend.async_remove_device", AsyncMock(return_value={"removed": True})),
          patch(f"{_RADIO}.radio_backend.async_confirm_device_removed", AsyncMock(return_value=True))):
        result = await _call_tool("remove_zigbee_device", {"device_id": device.id}, key, hass, data, request_id="rid")
    assert result[1] == "allowed", result
    warnings = json.loads(result[0]["content"][0]["text"])["mesa_advisory"]
    assert hidden.entity_id not in json.dumps(warnings)
    assert warnings == ["An entity of this device has an advisory policy restriction."]


async def test_off_mode_passes_and_an_unavailable_runtime_refuses(hass, zigbee_device):
    device, _light, _entry = zigbee_device
    args = {"device_id": device.id}
    data = await _data(hass, "off")
    backend = AsyncMock(return_value={"completed": True})
    with patch(f"{_RADIO}.radio_backend.async_reconfigure_device", backend):
        allowed = await _call_tool("reconfigure_zigbee_device", args, _access_key(), hass, data, request_id="rid")
        assert allowed[1] == "allowed"
        data = await _data(hass, "advisory")
        data.mesa = None
        data.mesa_setup_failed = True
        refused = await _call_tool("reconfigure_zigbee_device", args, _access_key(), hass, data, request_id="rid")
    assert refused[1] == "denied"
    backend.assert_awaited_once()


@pytest.mark.parametrize(("mode", "stored_profile", "outcome"), [
    ("off", False, "allowed"),
    ("advisory", False, "allowed"),
    ("advisory", True, "denied"),
    ("enforced", False, "denied"),
])
async def test_a_device_with_no_entity_warns_in_advisory_and_refuses_where_policy_must_hold(
    hass, mode, stored_profile, outcome,
):
    entry = MockConfigEntry(domain="zha", entry_id="bare")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", "aa:bb:cc:dd:ee:ff:00:11")}, name="Bare",
    )
    key = _access_key()
    key.permissions = PermissionTree(devices={device.id: PermissionNode(state="GREEN")})
    data = await _data(hass, mode)
    if stored_profile:
        data.mesa.store.set_device_profile(device.id, _profile(device.id, "prohibited"))
    backend = AsyncMock(return_value={"removed": True})
    with (patch(f"{_RADIO}.radio_backend.async_remove_device", backend),
          patch(f"{_RADIO}.radio_backend.async_confirm_device_removed", AsyncMock(return_value=True))):
        result = await _call_tool("remove_zigbee_device", {"device_id": device.id}, key, hass, data, request_id="rid")
    assert result[1] == outcome, result
    assert backend.await_count == (1 if outcome == "allowed" else 0)
    assert ("could not check" in json.dumps(result[0])) is (mode == "advisory" and outcome == "allowed")


def _adopted_entity(hass, esphome_entries):
    entry = _esphome_entry(hass, esphome_entries, runtime=_runtime())
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id


def _adopted_pair(hass, esphome_entries) -> tuple[str, str]:
    """A device whose entities sort as (first, second), so a gate must look past the first."""
    entry = _esphome_entry(
        hass, esphome_entries, entity_ids=("sensor.rf_blaster1_rssi", "sensor.rf_blaster1_zz"), runtime=_runtime(),
    )
    first, second = sorted(
        reg_entry.entity_id for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    )
    return first, second


def _named(device: str):
    return _runtime(device_info=esphome_device_info(name=device))


def _flash_key(**caps):
    key = _access_key(**caps)
    key.permissions = PermissionTree(domains={"sensor": PermissionNode(state="GREEN")})
    return key


@pytest.mark.parametrize("via_executor", [False, True], ids=["tool", "executor"])
async def test_a_prohibited_device_refuses_a_firmware_install(hass, esphome_dir, esphome_entries, via_executor):
    entity_id = _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))
    builder = FakeBuilder()
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _run(
            "install_esphome_firmware", {"file": FILE}, _flash_key(), hass, data, via_executor=via_executor,
        )
    assert result[1] == "denied"
    assert result[0]["content"][0]["text"] == MESA_DENIED_MESSAGE
    assert "firmware/install" not in builder.commands


async def test_a_prohibited_device_queues_no_flash_approval(hass, esphome_dir, esphome_entries):
    entity_id = _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, FakeBuilder()):
        result = await _call_tool(
            "install_esphome_firmware", {"file": FILE}, _flash_key(cap_esphome_flash="confirm"), hass, data,
            request_id="rid",
        )
    assert result[1] == "denied"
    assert data.store.get_pending_approvals() == []


@pytest.mark.parametrize("via_executor", [False, True], ids=["tool", "executor"])
async def test_one_prohibited_entity_refuses_the_install_wherever_it_sorts(
    hass, esphome_dir, esphome_entries, via_executor,
):
    """Flashing replaces what the whole device runs, so every entity must allow it."""
    _first, second = _adopted_pair(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(second, _profile(second, "prohibited"))
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _run(
            "install_esphome_firmware", {"file": FILE}, _flash_key(), hass, data, via_executor=via_executor,
        )
    assert result[1] == "denied"
    assert data.store.get_pending_approvals() == []
    assert "firmware/install" not in builder.commands


async def test_one_confirm_and_one_prohibited_entity_refuse_rather_than_ask(hass, esphome_dir, esphome_entries):
    """The install is all or nothing, so a refusal outranks the approval the other entity wants."""
    first, second = _adopted_pair(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(first, _profile(first, "confirm"))
    data.mesa.store.set(second, _profile(second, "prohibited"))
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool("install_esphome_firmware", {"file": FILE}, _flash_key(), hass, data, request_id="rid")
    assert result[1] == "denied"
    assert data.store.get_pending_approvals() == []
    assert "firmware/install" not in builder.commands


async def test_a_firmware_approval_does_not_outlive_a_tightened_policy(hass, esphome_dir, esphome_entries):
    entity_id = _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(entity_id, _profile(entity_id, "confirm"))
    key = _flash_key()
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        pending = await _call_tool("install_esphome_firmware", {"file": FILE}, key, hass, data, request_id="rid")
        assert pending[1] == "pending_approval"
        approval = data.store.get_pending_approvals()[0]
        data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))
        result = await async_execute_approved_tool("install_esphome_firmware", approval["args"], key, hass, data)
    assert result[1] == "denied"
    assert "firmware/install" not in builder.commands


async def test_a_firmware_install_confirm_is_approved_once(hass, esphome_dir, esphome_entries):
    entity_id = _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(entity_id, _profile(entity_id, "confirm"))
    key = _flash_key()
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        pending = await _call_tool("install_esphome_firmware", {"file": FILE}, key, hass, data, request_id="rid")
        assert pending[1] == "pending_approval"
        assert "firmware/install" not in builder.commands
        approval = data.store.get_pending_approvals()[0]
        assert approval["tool_name"] == "install_esphome_firmware"
        done = await async_execute_approved_tool("install_esphome_firmware", approval["args"], key, hass, data)
    assert done[1] == "allowed"
    assert builder.commands.count("firmware/install") == 1


async def test_a_mesa_approval_does_not_survive_a_denied_flash_capability(hass, esphome_dir, esphome_entries):
    entity_id = _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(entity_id, _profile(entity_id, "confirm"))
    builder = FakeBuilder()
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        await _call_tool("install_esphome_firmware", {"file": FILE}, _flash_key(), hass, data, request_id="rid")
        approval = data.store.get_pending_approvals()[0]
        result = await async_execute_approved_tool(
            "install_esphome_firmware", approval["args"],
            _flash_key(cap_esphome_flash="deny"), hass, data,
        )
    assert result[1] == "denied"
    assert "firmware/install" not in builder.commands


async def test_an_orphan_file_installs_while_mesa_is_active(hass, esphome_dir, esphome_entries):
    """A device not yet in Home Assistant has nothing a profile could cover."""
    data = await _data(hass, "advisory")
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool("install_esphome_firmware", {"file": FILE}, _access_key(), hass, data, request_id="rid")
    assert result[1] == "allowed"
    assert builder.commands.count("firmware/install") == 1


@pytest.mark.parametrize("source", [
    "esphome:\n  name: rf-blaster1\n",
    "substitutions:\n  device: rf-blaster1\nesphome:\n  name: ${device}\n",
    "substitutions:\n  prefix: rf\nesphome:\n  name: $prefix-blaster1\n",
], ids=["literal", "substitution", "partial_substitution"])
@pytest.mark.parametrize("via_executor", [False, True], ids=["tool", "executor"])
async def test_a_file_is_matched_on_the_name_it_declares(hass, esphome_dir, esphome_entries, source, via_executor):
    (esphome_dir / "garage.yaml").write_text(source)
    entity_id = _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _run(
            "install_esphome_firmware", {"file": "garage.yaml"}, _flash_key(), hass, data, via_executor=via_executor,
        )
    assert result[1] == "denied"
    assert result[0]["content"][0]["text"] == MESA_DENIED_MESSAGE
    assert "firmware/install" not in builder.commands


@pytest.mark.parametrize("flag", ["true", "yes", "${suffix}", "${undefined}"])
async def test_a_mac_suffixed_device_is_matched_on_its_declared_name(hass, esphome_dir, esphome_entries, flag):
    (esphome_dir / "garage.yaml").write_text(
        f"substitutions:\n  suffix: 'on'\nesphome:\n  name: blaster\n  name_add_mac_suffix: {flag}\n"
    )
    entry = _esphome_entry(hass, esphome_entries, runtime=_named("blaster-a1b2c3"))
    entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
    data = await _data(hass)
    data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))
    builder = FakeBuilder()
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool(
            "install_esphome_firmware", {"file": "garage.yaml"}, _flash_key(), hass, data, request_id="rid",
        )
    assert result[1] == "denied"
    assert "firmware/install" not in builder.commands


async def test_a_declared_owner_outside_the_scope_is_not_found(hass, esphome_dir, esphome_entries):
    (esphome_dir / "garage.yaml").write_text("esphome:\n  name: rf-blaster1\n")
    _adopted_entity(hass, esphome_entries)
    data = await _data(hass, "off")
    key = _access_key()
    builder = FakeBuilder()
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool("install_esphome_firmware", {"file": "garage.yaml"}, key, hass, data, request_id="rid")
    assert result[1] == "denied"
    assert "firmware/install" not in builder.commands


_UNKNOWN_OWNER = "esphome:\n  name: ${elsewhere}\npackages:\n  base: !include base.yaml\n"


@pytest.mark.parametrize(("mode", "outcome"), [("advisory", "allowed"), ("enforced", "denied"), ("off", "allowed")])
async def test_a_file_whose_owner_cannot_be_established_while_devices_are_adopted(
    hass, esphome_dir, esphome_entries, mode, outcome,
):
    (esphome_dir / "garage.yaml").write_text(_UNKNOWN_OWNER)
    _adopted_entity(hass, esphome_entries)
    data = await _data(hass, mode)
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool(
            "install_esphome_firmware", {"file": "garage.yaml"}, _flash_key(), hass, data, request_id="rid",
        )
    assert result[1] == outcome, result
    assert builder.commands.count("firmware/install") == (1 if outcome == "allowed" else 0)
    assert ("could not check" in json.dumps(result[0])) is (mode == "advisory")


@pytest.mark.parametrize("mode", ["advisory", "enforced"])
async def test_a_new_device_installs_while_nothing_is_adopted(hass, esphome_dir, esphome_entries, mode):
    """With no device in Home Assistant there is no policy to miss, in either mode."""
    (esphome_dir / "garage.yaml").write_text(_UNKNOWN_OWNER)
    data = await _data(hass, mode)
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool(
            "install_esphome_firmware", {"file": "garage.yaml"}, _flash_key(), hass, data, request_id="rid",
        )
    assert result[1] == "allowed", result
    assert "could not check" not in json.dumps(result[0])
    assert builder.commands.count("firmware/install") == 1


async def test_a_failed_runtime_refuses_even_a_true_orphan(hass, esphome_dir, esphome_entries):
    """The refusal comes before the orphan branch, so nothing installs unchecked."""
    data = await _data(hass, "advisory")
    data.mesa = None
    data.mesa_setup_failed = True
    builder = FakeBuilder()
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool("install_esphome_firmware", {"file": FILE}, _access_key(), hass, data, request_id="rid")
    assert result[1] == "denied"
    assert "firmware/install" not in builder.commands


async def test_a_file_the_builder_reports_with_no_adopted_device_is_a_true_orphan(hass, esphome_dir, esphome_entries):
    """A new device can be installed under Enforced once the Builder knows its name."""
    (esphome_dir / "new.yaml").write_text("esphome:\n  name: brand-new\n")
    _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    dash = _dash()
    dash.data = {"brand-new": {"configuration": "new.yaml"}}
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=dash), patch(_COMMAND, builder):
        result = await _call_tool("install_esphome_firmware", {"file": "new.yaml"}, _flash_key(), hass, data, request_id="rid")
    assert result[1] == "allowed", result
    assert builder.commands.count("firmware/install") == 1


async def _install_with_row(hass, esphome_dir, esphome_entries, source, mode="enforced", protect=True, host=None):
    """Install new.yaml, which the Builder reports under a name no adopted device has."""
    (esphome_dir / "new.yaml").write_text(source)
    entity_id = _adopted_entity(hass, esphome_entries)
    if host is not None:
        esphome_entries[-1].data = {"host": host}
    data = await _data(hass, mode)
    if protect:
        data.mesa.store.set(entity_id, _profile(entity_id, "prohibited"))
    dash = _dash()
    dash.data = {"harmless": {"configuration": "new.yaml"}}
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=dash), patch(_COMMAND, builder):
        result = await _call_tool("install_esphome_firmware", {"file": "new.yaml"}, _flash_key(), hass, data, request_id="rid")
    return result, builder


@pytest.mark.parametrize("address", ["rf-blaster1.local", "RF-Blaster1.local.", "rf-blaster1"])
async def test_a_file_aimed_at_an_adopted_name_is_gated_as_that_device(hass, esphome_dir, esphome_entries, address):
    source = f"esphome:\n  name: harmless\nwifi:\n  use_address: {address}\n"
    result, builder = await _install_with_row(hass, esphome_dir, esphome_entries, source)
    assert result[1] == "denied" and result[0]["content"][0]["text"] == MESA_DENIED_MESSAGE
    assert "firmware/install" not in builder.commands


async def test_a_file_aimed_at_an_adopted_host_is_gated_as_that_device(hass, esphome_dir, esphome_entries):
    source = "esphome:\n  name: harmless\nwifi:\n  use_address: 192.0.2.7\n"
    result, builder = await _install_with_row(hass, esphome_dir, esphome_entries, source, host="192.0.2.7")
    assert result[1] == "denied" and result[0]["content"][0]["text"] == MESA_DENIED_MESSAGE
    assert "firmware/install" not in builder.commands


async def test_a_static_address_of_an_adopted_device_is_gated_as_that_device(hass, esphome_dir, esphome_entries):
    source = "esphome:\n  name: harmless\nwifi:\n  manual_ip:\n    static_ip: 192.0.2.7\n"
    result, builder = await _install_with_row(hass, esphome_dir, esphome_entries, source, host="192.0.2.7")
    assert result[1] == "denied"
    assert "firmware/install" not in builder.commands


@pytest.mark.parametrize(("mode", "outcome"), [("advisory", "allowed"), ("enforced", "denied")])
async def test_an_address_matching_nothing_leaves_the_owner_unknown(hass, esphome_dir, esphome_entries, mode, outcome):
    source = "esphome:\n  name: harmless\nwifi:\n  use_address: somewhere.example\n"
    result, builder = await _install_with_row(hass, esphome_dir, esphome_entries, source, mode, protect=False)
    assert result[1] == outcome, result
    assert builder.commands.count("firmware/install") == (1 if outcome == "allowed" else 0)
    assert ("could not check" in json.dumps(result[0])) is (mode == "advisory")


@pytest.mark.parametrize("source", [
    "packages:\n  base: !include hidden.yaml\n",
    "esphome:\n  name: harmless\npackages:\n  base: !include hidden.yaml\n",
    "substitutions:\n  n: harmless\nesphome:\n  name: ${n}\n",
    "esphome:\n  name: harmless\nwifi:\n  use_address: !secret target\n",
    "wifi: !include wifi.yaml\nesphome:\n  name: harmless\n",
], ids=["name_in_package", "package_beside_name", "substitution", "tagged_address", "included_network"])
async def test_a_file_that_can_reach_elsewhere_is_no_orphan_despite_a_builder_row(
    hass, esphome_dir, esphome_entries, source
):
    result, builder = await _install_with_row(hass, esphome_dir, esphome_entries, source)
    assert result[1] == "denied", result
    assert "firmware/install" not in builder.commands



async def test_a_literal_name_the_builder_does_not_list_is_refused_under_enforced(hass, esphome_dir, esphome_entries):
    """A new device installs once the Builder reports the file, not before."""
    (esphome_dir / "new.yaml").write_text("esphome:\n  name: brand-new\n")
    _adopted_entity(hass, esphome_entries)
    data = await _data(hass)
    builder = FakeBuilder(**{"firmware/install": {"job_id": JOB, "status": "queued"}})
    with patch(_DASHBOARD, return_value=_dash()), patch(_COMMAND, builder):
        result = await _call_tool("install_esphome_firmware", {"file": "new.yaml"}, _flash_key(), hass, data, request_id="rid")
    assert result[1] == "denied", result
    assert "firmware/install" not in builder.commands


# --- structural matrix ------------------------------------------------------

_PACKAGE = Path(radio_tools.__file__).resolve().parent.parent
_PACKAGE_NAME = "custom_components.phoenix_mcp"

# What a call must reach to count as gated: the shared actuation gate, or the
# registry gate every entity identity action uses.
_GATES = frozenset({f"{_PACKAGE_NAME}.mesa:async_apply_mesa_to_call", f"{_PACKAGE_NAME}.mesa:evaluate_registry_action"})

# A read-only tool that does call a service, with the reason it changes nothing.
_READS_THROUGH_A_SERVICE = {
    "get_todo_items": "todo.get_items only returns the list",
}


def _package_sources() -> dict[str, tuple[str, bool]]:
    """Every first-party module's source, and whether it is a package."""
    sources: dict[str, tuple[str, bool]] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        if "mesa_core" in path.parts or "fastmcp" in path.parts:
            continue
        relative = path.relative_to(_PACKAGE).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        sources[".".join((_PACKAGE_NAME, *parts))] = (path.read_text(encoding="utf-8"), relative.name == "__init__")
    return sources


def _import_bindings(
    module: str, is_package: bool, tree: ast.AST, modules: set[str],
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """What a module's imports bind, anywhere in it: names to (module, name), and aliases to modules."""
    package = module.split(".") if is_package else module.split(".")[:-1]
    names: dict[str, tuple[str, str]] = {}
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        base = package[:len(package) - node.level + 1] if node.level else []
        target = ".".join([*base, *(node.module or "").split(".")]).rstrip(".")
        for alias in node.names:
            if f"{target}.{alias.name}" in modules:
                aliases[alias.asname or alias.name] = f"{target}.{alias.name}"
            elif target in modules:
                names[alias.asname or alias.name] = (target, alias.name)
    return names, aliases


def _call_graph(sources: dict[str, tuple[str, bool]]) -> tuple[dict[str, set[str]], dict[str, ast.AST]]:
    """Every top-level function and class as ``module:name``, with what each one refers to.

    Names resolve through the module's own definitions and imports, re-exports included, so two
    functions that share a name stay apart. A reference through a module alias counts; an
    attribute of anything else could be any object, and counting those would reach everything.
    """
    trees = {module: ast.parse(source) for module, (source, _is_package) in sources.items()}
    definitions = {
        module: {
            node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for module, tree in trees.items()
    }
    imports = {
        module: _import_bindings(module, sources[module][1], tree, set(trees))
        for module, tree in trees.items()
    }

    def resolve(module: str, name: str, depth: int = 0) -> str | None:
        if name in definitions[module]:
            return f"{module}:{name}"
        target = imports[module][0].get(name)
        return resolve(*target, depth + 1) if target is not None and depth < 8 else None

    edges: dict[str, set[str]] = {}
    nodes: dict[str, ast.AST] = {}
    for module, defined in definitions.items():
        aliases = imports[module][1]
        for name, definition in defined.items():
            references = set()
            for child in ast.walk(definition):
                if isinstance(child, ast.Name):
                    references.add(resolve(module, child.id))
                elif (isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
                      and child.value.id in aliases):
                    references.add(resolve(aliases[child.value.id], child.attr))
            edges[f"{module}:{name}"] = references - {None}
            nodes[f"{module}:{name}"] = definition
    return edges, nodes


def _function_bodies() -> tuple[dict[str, ast.AST], dict[str, set[str]]]:
    edges, nodes = _call_graph(_package_sources())
    return nodes, edges


def _reachable(start: str, edges: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    queue = [start]
    while queue:
        name = queue.pop()
        if name not in seen:
            seen.add(name)
            queue.extend(edges.get(name, ()))
    return seen


def _reaches_gate(start: str, edges: dict[str, set[str]]) -> bool:
    return bool(_reachable(start, edges) & _GATES)


def _qualified(function) -> str:
    return f"{function.__module__}:{function.__qualname__}"


def _catalog_tools(*, read_only: bool) -> dict[str, tuple[str, str | None]]:
    """Each advertised tool of one kind, with its handler and executor names."""
    tools: dict[str, tuple[str, str | None]] = {}
    for public, hints in tool_defs._TOOL_ANNOTATIONS.items():
        if bool(hints.get("readOnlyHint")) is not read_only:
            continue
        name = tool_defs._NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(public, public)
        handler = _TOOL_HANDLERS.get(name)
        assert handler is not None, f"{public} is advertised with no handler"
        executor = _EXECUTOR_REGISTRY.get(name)
        tools[name] = (_qualified(handler[0]), _qualified(executor) if executor is not None else None)
    return tools


def _mutating_tools() -> dict[str, tuple[str, str | None]]:
    return _catalog_tools(read_only=False)


def _dispatch_sites(nodes: dict[str, ast.AST]) -> set[str]:
    """Every function holding a Home Assistant service call."""
    return {
        name for name, node in nodes.items()
        if any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "async_call"
            for child in ast.walk(node)
        )
    }


def _tracked_handlers() -> set[str]:
    """The handlers tool_dispatch trusts for dispatch evidence, read from where it says so."""
    tree = ast.parse((_PACKAGE / "tool_dispatch.py").read_text())
    call = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "DispatchProgress"
        and any(keyword.arg == "tracked" and isinstance(keyword.value, ast.Compare) for keyword in node.keywords)
    )
    compare = next(keyword.value for keyword in call.keywords if keyword.arg == "tracked")
    return {_qualified(getattr(tool_dispatch, item.id)) for item in compare.comparators[0].elts}


def _unmarked_dispatches(node: ast.AST) -> list[int]:
    """Lines of a function's service calls with no mark_dispatch_started before them."""
    calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
    marks = [child.lineno for child in calls if getattr(child.func, "id", "") == "mark_dispatch_started"]
    return [
        child.lineno for child in calls
        if isinstance(child.func, ast.Attribute) and child.func.attr == "async_call"
        and not any(mark <= child.lineno for mark in marks)
    ]


def test_a_tracked_handler_marks_before_every_dispatch():
    """A tracked handler that never marked proves nothing ran, so one unmarked branch lies."""
    nodes, edges = _function_bodies()
    tracked = _tracked_handlers()
    assert len(tracked) >= 2, f"the scan found only {tracked}; it is not working"
    reached = set().union(*(_reachable(handler, edges) for handler in tracked))
    sites = _dispatch_sites(nodes) & reached
    assert len(sites) >= 4, f"the scan found only {sites}; it is not working"
    unmarked = {site: lines for site in sites if (lines := _unmarked_dispatches(nodes[site]))}
    assert unmarked == {}


def test_the_marking_scan_catches_an_unmarked_dispatch():
    tree = ast.parse(
        "async def f(hass):\n"
        "    await hass.services.async_call('a', 'b')\n"
        "    mark_dispatch_started()\n"
        "    await hass.services.async_call('a', 'c')\n"
    )
    assert _unmarked_dispatches(tree) == [2]


def test_every_mutating_tool_reaches_mesa_or_states_why_it_cannot():
    _functions, edges = _function_bodies()
    tools = _mutating_tools()
    assert len(tools) > 50, f"the scan found only {len(tools)} mutating tools; it is not working"
    ungated = sorted(
        name for name, (handler, executor) in tools.items()
        if name not in _NO_ENTITY_TO_JUDGE
        and not (_reaches_gate(handler, edges) or (executor is not None and _reaches_gate(executor, edges)))
    )
    assert not ungated, (
        "These mutating tools reach no MESA gate. Route them through the shared gate, "
        f"or add them to _NO_ENTITY_TO_JUDGE with the reason: {ungated}"
    )


def test_a_gated_tool_gates_in_its_executor_too():
    """A tool with an executor can be replayed by an approval, which skips the tool."""
    _functions, edges = _function_bodies()
    tools = _mutating_tools()
    gated_at_tool_only = sorted(
        name for name, (handler, executor) in tools.items()
        if name not in _NO_ENTITY_TO_JUDGE and executor is not None
        and _reaches_gate(handler, edges) and not _reaches_gate(executor, edges)
    )
    assert not gated_at_tool_only, f"An approval could replay these past their MESA gate: {gated_at_tool_only}"


def test_the_exemption_list_has_no_stale_entries():
    """A tool left here after it gained a gate would lose this guard's protection."""
    _functions, edges = _function_bodies()
    tools = _mutating_tools()
    stale = set(_NO_ENTITY_TO_JUDGE) - set(tools)
    assert not stale, f"_NO_ENTITY_TO_JUDGE names tools that are gone or read-only: {sorted(stale)}"
    gated = sorted(
        name for name in _NO_ENTITY_TO_JUDGE
        if _reaches_gate(tools[name][0], edges) or (tools[name][1] is not None and _reaches_gate(tools[name][1], edges))
    )
    assert not gated, f"These reach a MESA gate, so they are not exempt: {gated}"


@pytest.mark.parametrize("tool", RADIO_TOOLS + ("install_esphome_firmware", "remove_device", "set_device", "set_entity"))
def test_device_actions_are_never_exempt(tool):
    assert tool not in _NO_ENTITY_TO_JUDGE
    _functions, edges = _function_bodies()
    handler, executor = _mutating_tools()[tool]
    assert _reaches_gate(handler, edges) and _reaches_gate(executor, edges)


def test_a_read_only_tool_reaches_no_dispatch_site():
    """The annotation exempts a tool from every scan above, so it has to be earned."""
    nodes, edges = _function_bodies()
    sites = _dispatch_sites(nodes) | _GATES
    tools = _catalog_tools(read_only=True)
    assert len(tools) > 50, f"the scan found only {len(tools)} read-only tools; it is not working"
    dispatching = sorted(
        name for name, (handler, executor) in tools.items()
        if name not in _READS_THROUGH_A_SERVICE
        and (_reachable(handler, edges) | (_reachable(executor, edges) if executor else set())) & sites
    )
    assert not dispatching, (
        "These tools are marked read-only but can reach a service call or a MESA gate. Correct "
        f"the annotation, or add them to _READS_THROUGH_A_SERVICE with the reason: {dispatching}"
    )


def test_the_read_service_exemptions_are_current():
    nodes, edges = _function_bodies()
    sites = _dispatch_sites(nodes)
    tools = _catalog_tools(read_only=True)
    assert {name for name in _READS_THROUGH_A_SERVICE if _reachable(tools[name][0], edges) & sites} == set(_READS_THROUGH_A_SERVICE)


_GATE_MODULE = "async def async_apply_mesa_to_call():\n    pass\n"


def test_the_scan_would_notice_a_tool_with_no_gate():
    edges, _nodes = _call_graph({
        f"{_PACKAGE_NAME}.mesa": (_GATE_MODULE, False),
        f"{_PACKAGE_NAME}.tools.new": ("async def _tool_new(hass):\n    await _helper(hass)\n"
                                       "def _helper(hass):\n    return hass.services.async_call('lock', 'unlock')\n", False),
    })
    assert not _reaches_gate(f"{_PACKAGE_NAME}.tools.new:_tool_new", edges)


def test_the_scan_follows_an_import_and_a_re_export_to_the_gate():
    edges, _nodes = _call_graph({
        f"{_PACKAGE_NAME}.mesa": (_GATE_MODULE, False),
        f"{_PACKAGE_NAME}.tools": ("from ..mesa import async_apply_mesa_to_call as gate\n", True),
        f"{_PACKAGE_NAME}.tools.new": ("from . import gate\nasync def _tool_new():\n    await gate()\n", False),
    })
    assert _reaches_gate(f"{_PACKAGE_NAME}.tools.new:_tool_new", edges)


def test_the_scan_keeps_functions_that_share_a_name_apart():
    edges, _nodes = _call_graph({
        f"{_PACKAGE_NAME}.mesa": (_GATE_MODULE, False),
        f"{_PACKAGE_NAME}.tools.gated": ("from ..mesa import async_apply_mesa_to_call\n"
                                         "async def _prepare():\n    await async_apply_mesa_to_call()\n"
                                         "async def _tool_gated():\n    await _prepare()\n", False),
        f"{_PACKAGE_NAME}.tools.ungated": ("async def _prepare():\n    pass\n"
                                           "async def _tool_ungated():\n    await _prepare()\n", False),
    })
    assert _reaches_gate(f"{_PACKAGE_NAME}.tools.gated:_tool_gated", edges)
    assert not _reaches_gate(f"{_PACKAGE_NAME}.tools.ungated:_tool_ungated", edges)


def test_the_scan_would_notice_a_read_only_tool_that_dispatches():
    edges, nodes = _call_graph({
        f"{_PACKAGE_NAME}.tools.new": ("async def _tool_new(hass):\n    await _helper(hass)\n"
                                       "def _helper(hass):\n    return hass.services.async_call('lock', 'unlock')\n", False),
    })
    assert _reachable(f"{_PACKAGE_NAME}.tools.new:_tool_new", edges) & _dispatch_sites(nodes)


@pytest.mark.parametrize(("tool", "module"), [
    ("set_label_or_category", "labels"), ("set_dashboard_resource", "dashboard_resources"),
])
def test_tools_outside_mesa_stay_listed_with_their_reason(tool, module):
    source = (_PACKAGE / "tools" / f"{module}.py").read_text(encoding="utf-8")
    assert "mesa" not in source.lower()
    assert _NO_ENTITY_TO_JUDGE[tool]
