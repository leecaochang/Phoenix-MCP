"""Every tool with an approval executor: Confirm builds a diff, Approve reaches the executor.

The approval path runs over HA's real router against a real store. A diff builder
that raises breaks every Confirm-mode call of its tool before an approval exists,
and a broken executor only fails after a human approved, so both ends are swept
from the registry rather than from a list of tools somebody remembered.
"""

import json
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.calendar import DATA_COMPONENT as CALENDAR_COMPONENT
from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.components.todo.const import DATA_COMPONENT as TODO_COMPONENT
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
import pytest
import yaml

from custom_components.phoenix_mcp import mesa_tools, tool_catalog, tool_defs, tool_dispatch
from custom_components.phoenix_mcp.approval_targets import bind_targets
from custom_components.phoenix_mcp.approvals import async_create_pending_approval
from custom_components.phoenix_mcp.const import CAPABILITY_NAMES, DOMAIN, MESA_APPROVED_EXECUTOR, MESA_CONFIRM_CAP
from custom_components.phoenix_mcp.tool_common import UncertainOutcome
from custom_components.phoenix_mcp.tools import authoring
from custom_components.phoenix_mcp.tools.todo import _items, _reviewed
from custom_components.phoenix_mcp.ws_dispatch import async_save_lovelace_config, async_ws_command
from tests.runtime_fixtures import schema_baseline


APPROVE = "/api/phoenix-mcp/admin/approvals/{}/approve"
OUTCOMES = frozenset({"allowed", "denied", "not_found", "invalid_request", "not_implemented", "pending_approval"})
PUBLIC = {internal: public for public, internal in tool_defs._NATIVE_TOOL_PUBLIC_TO_INTERNAL.items()}
DEFS = {tool["name"]: tool for tool in (
    *tool_defs._ENTITY_TOOL_DEFS, *tool_defs._NATIVE_TOOL_DEFS, *tool_defs._SYSTEM_TOOL_DEFS, *mesa_tools.mesa_tool_defs())}
LAMP = "light.sweep"
AUTOMATION = {"alias": "Sweep", "triggers": [{"trigger": "state", "entity_id": LAMP}],
              "actions": [{"action": "light.turn_on", "target": {"entity_id": LAMP}}]}
SCRIPT = {"alias": "Sweep", "sequence": [{"action": "light.turn_on", "target": {"entity_id": LAMP}}]}
SCENE = {"name": "Sweep", "entities": {LAMP: "on"}}
BLUEPRINT = "blueprint:\n  name: Sweep\n  domain: automation\ntrigger: []\naction: []\n"
CARD = {"type": "markdown", "content": "sweep"}
BOARD = {"url_path": "sweep-board"}
EVENT = {"summary": "Sweep", "start": "2030-01-01T10:00:00+00:00", "end": "2030-01-01T11:00:00+00:00"}


def _schema_args(name: str) -> dict:
    tool = DEFS.get(name) or DEFS.get(PUBLIC.get(name, ""))
    return schema_baseline(tool) if tool else {}


CALENDAR_HASH = "Needs the expected hash of an event read back from a calendar."
ZIGBEE = "Needs a Zigbee coordinator, which only the radio tool harness fakes."
# The approval half of these still runs from saved arguments below.
GATE_EXEMPT: dict[str, str] = {
    MESA_APPROVED_EXECUTOR: "Only MESA Confirm creates it; it has no tool call of its own.",
    "HassSetVolumeRelative": "Needs a real media player entity behind the intent.",
    "HassTurnOff": "No capability gates it, so only MESA Confirm queues it.",
    "HassTurnOn": "No capability gates it, so only MESA Confirm queues it.",
    "configure_zigbee_reporting": ZIGBEE,
    "create_backup": "Needs the backup integration and its agents.",
    "create_zigbee_group": ZIGBEE,
    "delete_calendar_event": CALENDAR_HASH,
    "edit_energy_config": "Needs a stored Energy preference to change.",
    "install_esphome_firmware": "Needs a running Device Builder add-on.",
    "permit_zigbee_join": ZIGBEE,
    "reconfigure_integration": "Needs an integration with a reconfigure flow.",
    "reconfigure_zigbee_device": ZIGBEE,
    "remove_device": "Needs an integration that supports device removal.",
    "remove_zigbee_device": ZIGBEE,
    "remove_zigbee_group": ZIGBEE,
    "restore_configuration": "Needs a recorded configuration recovery point.",
    "scan_zigbee_topology": ZIGBEE,
    "set_helper_settings": "Needs an entry of an integration classified as a helper.",
    "set_todo_item": "No capability gates it, so only MESA Confirm queues it.",
    "set_zigbee_binding": ZIGBEE,
    "set_zigbee_device_options": ZIGBEE,
    "set_zigbee_device_property": ZIGBEE,
    "set_zigbee_group_members": ZIGBEE,
    "update_calendar_event": CALENDAR_HASH,
}

# Arguments that pass each tool's own refusals against the world `sweep` builds.
SEEDS: dict[str, dict] = {
    "HassSetPosition": {"name": "Sweep blind", "position": 10},
    "HassStopMoving": {"name": "Sweep blind"},
    "add_dashboard_card": {**BOARD, "view_index": 0, "card": CARD},
    "call_service": {"service": {"domain": "lock", "name": "unlock"}, "targets": [{"kind": "entity", "ids": ["lock.sweep"]}]},
    "create_automation": {"config": AUTOMATION},
    "create_blueprint": {"domain": "automation", "path": "sweep/new.yaml", "content": BLUEPRINT},
    "create_calendar_event": {"entity_id": "calendar.sweep", "event": EVENT},
    "create_dashboard": {"config": {"url_path": "fresh-board", "title": "Fresh"}},
    "create_helper": {"helper_type": "input_boolean", "config": {"name": "Fresh flag"}},
    "create_scene": {"config": SCENE},
    "create_script": {"script_id": "fresh", "config": SCRIPT},
    "delete_automation": {"automation_id": "sweep"},
    "delete_blueprint": {"domain": "automation", "path": "sweep/old.yaml"},
    "delete_dashboard": {"dashboard_id": "sweep-board"},
    "delete_dashboard_card": {**BOARD, "view_index": 0, "card_index": 0},
    "delete_entity": {"entity_id": "sensor.sweep_orphan"},
    "delete_helper": {"helper_type": "input_boolean", "helper_id": "sweep_flag"},
    "delete_scene": {"scene_id": "sweep"},
    "delete_script": {"script_id": "sweep"},
    "edit_automation": {"automation_id": "sweep", "config": AUTOMATION},
    "edit_blueprint": {"domain": "automation", "path": "sweep/old.yaml", "content": BLUEPRINT},
    "edit_dashboard": {"dashboard_id": "sweep-board", "config": {"title": "Renamed"}},
    "edit_dashboard_card": {**BOARD, "view_index": 0, "card_index": 0, "card": CARD},
    "edit_helper": {"helper_type": "input_boolean", "helper_id": "sweep_flag", "config": {"name": "Flag"}},
    "edit_scene": {"scene_id": "sweep", "config": SCENE},
    "edit_script": {"script_id": "sweep", "config": SCRIPT},
    "patch_dashboard": {**BOARD, "changes": [{"kind": "set", "path": ["views", 0, "title"], "value": "Renamed"}]},
    "reload_integration": {"entry_id": "sweep_entry"},
    "restart_ha": {},
    "remove_integration": {"entry_id": "sweep_entry"},
    "send_notification": {"target": "persistent_notification", "message": "sweep"},
    "set_area_or_floor": {"kind": "area", "operation": "create", "changes": {"name": "Sweep room"}},
    "set_dashboard_config": {"config": {"views": []}},
    "set_dashboard_resource": {"operation": "create", "url": "/local/sweep.js", "type": "module"},
    "set_device": {"device_id": "@device", "changes": {"name": "Renamed"}},
    "set_entity": {"entity_id": LAMP, "changes": {"name": "Renamed"}},
    "set_integration": {"entry_id": "sweep_entry", "changes": {"title": "Renamed"}},
    "set_integration_enabled": {"entry_id": "sweep_entry", "enabled": False},
    "set_integration_log_level": {"integration": "demo", "level": "DEBUG", "persistence": "none"},
    "set_label_or_category": {"kind": "label", "operation": "create", "changes": {"name": "Sweep"}},
}


class _Calendar(CalendarEntity):
    """A writable calendar; the sweep needs it to exist, not to remember events."""

    _attr_supported_features = (CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.UPDATE_EVENT
                                | CalendarEntityFeature.DELETE_EVENT)
    entity_id = "calendar.sweep"
    event = None

    async def async_get_events(self, hass, start_date, end_date):
        return []

    async def async_create_event(self, **kwargs) -> None:
        return None

    async def async_update_event(self, uid, event, **selectors) -> None:
        return None

    async def async_delete_event(self, uid, **selectors) -> None:
        return None


def _resolve(args: Any, device_id: str) -> Any:
    """Substitute the device row's generated id into a seed."""
    return json.loads(json.dumps(args).replace("@device", device_id))


class Sweep:
    """The real runtime, a key on Confirm for everything, and the world the seeds name."""

    def __init__(self, hass, data, access_key, client, device_id) -> None:
        self.hass, self.data, self.access_key, self.client = hass, data, access_key, client
        self.device_id = device_id

    def args(self, name: str) -> dict:
        return _resolve(SEEDS[name] if name in SEEDS else _schema_args(name), self.device_id)

    async def approve(self, name: str, approval_id: str) -> tuple[int, list]:
        """POST Approve and report the status and the calls that reached the real executor."""
        real = tool_catalog._EXECUTOR_REGISTRY[name]
        calls: list = []

        async def spy(*args: Any):
            result = await real(*args)
            calls.append(result)
            return result

        with patch.dict(tool_catalog._EXECUTOR_REGISTRY, {name: spy}):
            response = await self.client.post(APPROVE.format(approval_id), json={})
            await self.hass.async_block_till_done()
        return response.status, calls


@pytest.fixture
async def sweep(hass, hass_storage, enable_custom_integrations, hass_client, hass_admin_user):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    assert await async_setup_component(hass, "lovelace", {})
    assert await async_setup_component(hass, "logger", {})
    assert await async_setup_component(hass, "persistent_notification", {})
    hass.services.async_register("homeassistant", "restart", AsyncMock())
    hass.services.async_register("light", "turn_on", AsyncMock())
    hass.services.async_register("light", "turn_off", AsyncMock())
    hass.services.async_register("lock", "unlock", AsyncMock())
    hass.services.async_register("cover", "stop_cover", AsyncMock())
    hass.services.async_register("cover", "set_cover_position", AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = hass.data[DOMAIN]
    await data.store.async_patch_settings(mesa_mode="off")

    other = MockConfigEntry(domain="demo", entry_id="sweep_entry", title="Sweep", data={"host": "old.invalid"})
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("demo", "sweep")})
    registry = er.async_get(hass)
    registry.async_get_or_create("light", "demo", "lamp", suggested_object_id="sweep", original_name="Sweep lamp",
                                 config_entry=other, device_id=device.id)
    registry.async_get_or_create("lock", "demo", "lock", suggested_object_id="sweep", original_name="Sweep lock")
    registry.async_get_or_create("sensor", "demo", "orphan", suggested_object_id="sweep_orphan")
    registry.async_get_or_create("automation", "automation", "sweep", suggested_object_id="sweep")
    registry.async_get_or_create("script", "script", "sweep", suggested_object_id="sweep")
    registry.async_get_or_create("scene", "homeassistant", "sweep", suggested_object_id="sweep")
    registry.async_get_or_create("calendar", "local_calendar", "sweep", suggested_object_id="sweep")
    hass.states.async_set(LAMP, "off", {"friendly_name": "Sweep lamp", "supported_features": 0})
    hass.states.async_set("lock.sweep", "locked", {"friendly_name": "Sweep lock"})
    registry.async_get_or_create("cover", "demo", "blind", suggested_object_id="sweep", original_name="Sweep blind")
    hass.states.async_set("cover.sweep", "open", {"friendly_name": "Sweep blind", "supported_features": 15})
    hass.states.async_set("automation.sweep", "on", {"id": "sweep"})
    hass.states.async_set("script.sweep", "off", {"friendly_name": "Sweep"})
    hass.states.async_set("scene.sweep", "off", {"id": "sweep", "entity_id": [LAMP]})
    hass.states.async_set("calendar.sweep", "off")
    calendar = _Calendar()
    hass.data[CALENDAR_COMPONENT] = SimpleNamespace(
        get_entity=lambda entity_id: calendar if entity_id == calendar.entity_id else None)
    config = pathlib.Path(hass.config.config_dir)
    (config / "automations.yaml").write_text(yaml.safe_dump([{"id": "sweep", **AUTOMATION}]))
    (config / "scripts.yaml").write_text(yaml.safe_dump({"sweep": SCRIPT}))
    (config / "scenes.yaml").write_text(yaml.safe_dump([{"id": "sweep", **SCENE}]))
    blueprints = config / "blueprints" / "automation" / "sweep"
    blueprints.mkdir(parents=True)
    (blueprints / "old.yaml").write_text(BLUEPRINT)
    await async_ws_command(hass, "input_boolean/create", {"name": "Sweep flag"})
    await async_ws_command(hass, "lovelace/dashboards/create", {"url_path": "sweep-board", "title": "Sweep"})
    await async_save_lovelace_config(hass, "sweep-board", {"views": [{"title": "Home", "cards": [CARD]}]})

    access_key, _ = await data.store.async_create_access_key("sweep", "admin")
    live = data.store.get_access_key_by_id(access_key.id)
    live.pass_through = True
    for cap in CAPABILITY_NAMES:
        setattr(live, cap, "confirm")
    await data.store.async_save()
    yield Sweep(hass, data, live, await hass_client(), device.id)


def _tool_name(name: str) -> str:
    return (DEFS.get(name) or DEFS[PUBLIC[name]])["name"]


async def _reaches_executor(sweep: Sweep, name: str, approval_id: str) -> None:
    status, calls = await sweep.approve(name, approval_id)
    assert status < 500, name
    assert len(calls) == 1, name
    tool_result, outcome, _resource = calls[0]
    assert tool_result["content"], name
    assert outcome in OUTCOMES, (name, outcome)
    record = next(row for row in sweep.data.store.get_pending_approvals() if row["id"] == approval_id)
    assert record["status"] != "pending", name


def test_every_executor_is_seeded_or_named_with_its_reason():
    """A new executor must join the sweep or say why it cannot, never fall out of it silently."""
    registered = set(tool_catalog._EXECUTOR_REGISTRY)
    assert set(SEEDS) | set(GATE_EXEMPT) == registered
    assert not set(SEEDS) & set(GATE_EXEMPT)


@pytest.mark.parametrize("name", sorted(SEEDS))
async def test_confirm_builds_a_diff_and_approve_reaches_the_executor(sweep, name):
    if name == "create_calendar_event":
        # The sweep fakes the calendar component; marking it for every tool would make
        # a reloaded demo entry forward a calendar platform that was never set up.
        sweep.hass.config.components.add("calendar")
    result, outcome, _resource = await tool_dispatch._call_tool(
        _tool_name(name), sweep.args(name), sweep.access_key, sweep.hass, sweep.data)
    assert outcome == "pending_approval", (name, result)
    approval_id = json.loads(result["content"][0]["text"])["approval_id"]
    record = next(row for row in sweep.data.store.get_pending_approvals() if row["id"] == approval_id)
    assert record["tool_name"] == name
    assert record["diff"]["kind"] and record["diff"]["summary"], name
    await _reaches_executor(sweep, name, approval_id)


@pytest.mark.parametrize("name", sorted(tool_catalog._EXECUTOR_REGISTRY))
async def test_a_saved_approval_reaches_every_registered_executor(sweep, name):
    """Saved arguments may no longer fit the home; the executor still answers, it never raises."""
    async with sweep.data.store.async_lock:
        record = await async_create_pending_approval(
            sweep.data.store, access_key_id=sweep.access_key.id, access_key_name=sweep.access_key.name,
            tool_name=name, cap_name=MESA_CONFIRM_CAP if name == MESA_APPROVED_EXECUTOR else "cap_restart",
            args=_resolve(_schema_args(name), sweep.device_id),
            diff={"kind": "system_action", "summary": name}, request_id=f"r-{name}",
        )
    await _reaches_executor(sweep, name, record.id)


class _StubbornList(TodoListEntity):
    """A list that accepts a removal and keeps its items."""

    _attr_name = "Sweep list"
    _attr_unique_id = "sweep_list"
    _attr_supported_features = TodoListEntityFeature.DELETE_TODO_ITEM
    _attr_todo_items = [TodoItem(uid="eggs", summary="Eggs", status=TodoItemStatus.COMPLETED)]

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Keep everything."""


async def test_an_approved_delete_whose_reload_fails_closes_as_interrupted(sweep):
    """The entry left its file, so an approval closed as a plain failure would say nothing happened."""
    args, _ = await authoring._bind_delete_args({"automation_id": "sweep"}, sweep.hass, "automation", "sweep")
    async with sweep.data.store.async_lock:
        record = await async_create_pending_approval(
            sweep.data.store, access_key_id=sweep.access_key.id, access_key_name=sweep.access_key.name,
            tool_name="delete_automation", cap_name="cap_automation_write", args=args,
            diff={"kind": "config_diff", "summary": "delete"}, request_id="r-delete",
        )
    status, calls = await sweep.approve("delete_automation", record.id)
    assert status == 200 and isinstance(calls[0][0], UncertainOutcome), calls
    stored = next(row for row in sweep.data.store.get_pending_approvals() if row["id"] == record.id)
    assert (stored["status"], stored["closure_reason"]) == ("failed", "execution_interrupted")


async def test_an_approved_clear_that_leaves_items_closes_as_interrupted(sweep):
    assert await async_setup_component(sweep.hass, "todo", {})
    await sweep.hass.data[TODO_COMPONENT].async_add_entities([_StubbornList()])
    await sweep.hass.async_block_till_done()
    entity_id = "todo.sweep_list"
    rows = [_reviewed(item) for item in await _items(sweep.hass, entity_id, ["completed"])]
    args = bind_targets({"entity_id": entity_id, "action": "clear_completed", "reviewed": rows}, [entity_id], sweep.hass)
    async with sweep.data.store.async_lock:
        record = await async_create_pending_approval(
            sweep.data.store, access_key_id=sweep.access_key.id, access_key_name=sweep.access_key.name,
            tool_name="set_todo_item", cap_name=MESA_CONFIRM_CAP, args=args,
            diff={"kind": "service_preview", "summary": "clear"}, request_id="r-clear",
        )
    status, calls = await sweep.approve("set_todo_item", record.id)
    assert status == 200 and isinstance(calls[0][0], UncertainOutcome)
    stored = next(row for row in sweep.data.store.get_pending_approvals() if row["id"] == record.id)
    assert (stored["status"], stored["closure_reason"]) == ("failed", "execution_interrupted")
