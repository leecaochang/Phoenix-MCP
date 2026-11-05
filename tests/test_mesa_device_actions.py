"""MESA reaches whole-device radio and firmware actions, and every mutating tool.

The behavioural half drives each device action through its tool and its executor
against a real MESA runtime, with the radio or Device Builder seam recording
whether anything was sent. The structural half walks the call graph from every
tool that is not read-only and fails on a tool that neither reaches a MESA gate
nor states why it has no entity for MESA to judge.
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

from custom_components.phoenix_mcp import tool_defs
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
from tests.runtime_fixtures import esphome_entry as _esphome_entry, esphome_runtime as _runtime

_RADIO = "custom_components.phoenix_mcp.tools.radio"
_DASHBOARD = "custom_components.phoenix_mcp.tools.esphome._esphome_dashboard"
_COMMAND = "custom_components.phoenix_mcp.tools.esphome.async_builder_command"

_IEEE = "00:11:22:33:44:55:66:77"


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
        **{"cap_radio_write": "allow", "cap_esphome_flash": "allow", "cap_esphome_yaml": "allow", **caps},
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


def _radio_cases(device):
    change = SimpleNamespace(
        device=device, ieee=_IEEE, changes={"legacy": False},
        desired={"endpoint": 1, "cluster": "genOnOff", "attribute": "onOff"},
        entry={}, before={"endpoints": [{"endpoint": 1, "configured_reportings": []}]}, after={}, before_hash="",
    )
    return {
        "remove_zigbee_device": ("async_remove_device", {}),
        "reconfigure_zigbee_device": ("async_reconfigure_device", {}),
        "set_zigbee_device_options": ("async_set_z2m_device_options", {
            "_resolve_z2m_option_change": AsyncMock(return_value=change)}),
        "configure_zigbee_reporting": ("async_configure_z2m_reporting", {
            "_resolve_zigbee_reporting_change": AsyncMock(return_value=change)}),
    }


RADIO_TOOLS = ("remove_zigbee_device", "reconfigure_zigbee_device", "set_zigbee_device_options", "configure_zigbee_reporting")


class _RadioSeam:
    """Patch the tool's resolver and record the one call that reaches the radio."""

    def __init__(self, tool: str, device) -> None:
        backend, resolvers = _radio_cases(device)[tool]
        self.backend = AsyncMock(side_effect=RuntimeError("stop after dispatch"))
        self._patches = [patch(f"{_RADIO}.radio_backend.{backend}", self.backend)]
        self._patches += [patch(f"{_RADIO}.{name}", value) for name, value in resolvers.items()]

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
    return await _TOOL_HANDLERS[tool][0](args, key, hass, data, request_id="rid", client_ip=None)


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


# --- structural matrix ------------------------------------------------------

_PACKAGE = Path(radio_tools.__file__).resolve().parent.parent

# What a call must reach to count as gated: the shared actuation gate, the
# registry gate every entity identity action uses, or the wrappers that hold them.
_GATES = frozenset({"async_apply_mesa_to_call", "evaluate_registry_action"})

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
    "set_integration_log_level": "log verbosity, with no entity target",
}


def _function_bodies() -> tuple[dict[str, ast.AST], dict[str, set[str]]]:
    """Every function in the package and the names each one calls or references."""
    functions: dict[str, ast.AST] = {}
    edges: dict[str, set[str]] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        if "mesa_core" in path.parts or "fastmcp" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node.name] = node
                # Bare names only: an attribute such as .get would join every
                # function that shares a method name and reach everything.
                edges.setdefault(node.name, set()).update(
                    child.id for child in ast.walk(node) if isinstance(child, ast.Name)
                )
    return functions, edges


def _reaches_gate(start: str, edges: dict[str, set[str]]) -> bool:
    seen: set[str] = set()
    queue = [start]
    while queue:
        name = queue.pop()
        if name in _GATES:
            return True
        if name in seen:
            continue
        seen.add(name)
        queue.extend(edges.get(name, ()))
    return False


def _mutating_tools() -> dict[str, tuple[str, str | None]]:
    """Each advertised tool that is not read-only, with its handler and executor names."""
    tools: dict[str, tuple[str, str | None]] = {}
    for public, hints in tool_defs._TOOL_ANNOTATIONS.items():
        if hints.get("readOnlyHint"):
            continue
        name = tool_defs._NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(public, public)
        handler = _TOOL_HANDLERS.get(name)
        assert handler is not None, f"{public} is advertised with no handler"
        executor = _EXECUTOR_REGISTRY.get(name)
        tools[name] = (handler[0].__name__, executor.__name__ if executor is not None else None)
    return tools


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


def test_the_scan_would_notice_a_tool_with_no_gate():
    edges = {"_tool_new": {"_helper"}, "_helper": {"hass", "async_call"}}
    assert not _reaches_gate("_tool_new", edges)
    edges["_helper"].add("async_apply_mesa_to_call")
    assert _reaches_gate("_tool_new", edges)
