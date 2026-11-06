"""The physical and notification gates follow what a call reaches, not the service's domain.

Rules run against every real services.yaml and, through call_service, against services
registered with stub handlers so Home Assistant reads their real descriptions.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import homeassistant
import pytest
import yaml
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.capability import service_capability
from custom_components.phoenix_mcp.const import (
    DOMAIN,
    NON_ACTUATING_SERVICES,
    PHYSICAL_COMMAND_SERVICES,
    PHYSICAL_GATE_DOMAINS,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.policy_engine import physical_gate_applies
from custom_components.phoenix_mcp.service_targets import _declared_target_filters
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from tests.runtime_fixtures import esphome_device_info, esphome_entry, esphome_runtime

# Integration-filtered services that move data, not the device. Anything new here must be
# judged: raw command services go to PHYSICAL_COMMAND_SERVICES.
_INTEGRATION_FILTERED_AND_HARMLESS = frozenset({
    "collection_image/select_first", "collection_image/select_image", "collection_image/select_last",
    "collection_image/select_next", "collection_image/select_previous", "collection_image/shuffle",
    "google_mail/set_vacation", "insteon/print_all_link_database", "isy994/get_zwave_parameter",
    "isy994/rename_node", "openevse/set_override", "workday/check_date",
})


def _real_descriptions() -> list[tuple[str, str, dict]]:
    class _Loader(yaml.SafeLoader):
        """HA's service files use a tag the safe loader does not know."""

    _Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))
    rows = []
    root = Path(homeassistant.__file__).parent / "components"
    for path in sorted(root.glob("*/services.yaml")):
        for service, description in (yaml.load(path.read_text(), Loader=_Loader) or {}).items():
            rows.append((path.parent.name, service, description if isinstance(description, dict) else {}))
    return rows


def _filter_domains(filters: list[dict]) -> set[str]:
    return {
        name for item in filters
        for name in ([item["domain"]] if isinstance(item.get("domain"), str) else item.get("domain") or [])
    }


def _declaring(*domains: str) -> list[tuple[str, str, set[str]]]:
    found = []
    for domain, service, description in _real_descriptions():
        declared = _filter_domains(_declared_target_filters(description) or [])
        if declared & set(domains) and domain not in domains:
            found.append((domain, service, declared & set(domains)))
    return found


def test_the_survey_found_the_real_shapes():
    """A vacuous survey would make the two rules below pass for nothing."""
    assert {"nuki/lock_n_go", "matter/set_lock_credential", "zwave_js/set_credential"} <= {
        f"{domain}/{service}" for domain, service, _ in _declaring(*PHYSICAL_GATE_DOMAINS)
    }
    assert "telegram_bot/send_message" in {f"{domain}/{service}" for domain, service, _ in _declaring("notify")}


@pytest.mark.parametrize(("domain", "service", "declared"), _declaring(*PHYSICAL_GATE_DOMAINS))
def test_every_core_service_on_a_physical_target_is_gated(domain, service, declared):
    assert physical_gate_applies(domain, service, [f"{name}.x" for name in declared]) is True


@pytest.mark.parametrize(("domain", "service", "declared"), _declaring("notify"))
def test_every_core_service_on_a_notify_target_needs_the_capability(domain, service, declared):
    assert service_capability(domain, service, {}, ["notify.x"]) == "cap_notify"


def test_every_integration_filtered_service_has_been_judged():
    """A service reaching a device through any of its entities cannot appear unnoticed."""
    filtered = {
        f"{domain}/{service}"
        for domain, service, description in _real_descriptions()
        if (filters := _declared_target_filters(description))
        and any(item.get("integration") and not item.get("domain") for item in filters)
    }
    assert filtered - PHYSICAL_COMMAND_SERVICES - NON_ACTUATING_SERVICES - _INTEGRATION_FILTERED_AND_HARMLESS == set()


@pytest.mark.parametrize("service_key", sorted(PHYSICAL_COMMAND_SERVICES))
def test_a_raw_command_is_physical_on_any_target(service_key):
    domain, service = service_key.split("/")
    assert physical_gate_applies(domain, service, ["sensor.door_lock_battery"]) is True
    assert physical_gate_applies(domain, service, []) is True


@pytest.mark.parametrize("service_key", sorted(NON_ACTUATING_SERVICES))
def test_a_service_that_cannot_actuate_adds_no_requirement(service_key):
    domain, service = service_key.split("/")
    assert physical_gate_applies(domain, service, ["lock.front"]) is False
    assert service_capability(domain, service, {}, ["notify.chat"]) is None


def _data(hass) -> tuple[PhoenixData, list]:
    pending: list = []
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=None)
    hass.data[DOMAIN] = data
    return data, pending


def _key(entities: dict[str, str], **fields) -> AccessKeyRecord:
    return AccessKeyRecord(
        id="k", name="K", access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(entities={e: PermissionNode(state=s) for e, s in entities.items()}),
        **fields,
    )


def _entity(hass, domain: str, platform: str, object_id: str, state: str = "off", **attributes) -> str:
    entity_id = er.async_get(hass).async_get_or_create(
        domain, platform, f"{platform}_{object_id}", suggested_object_id=object_id).entity_id
    hass.states.async_set(entity_id, state, attributes)
    return entity_id


def _register(hass, *names: tuple[str, str]) -> list:
    ran: list = []

    async def handler(call) -> None:
        ran.append((call.domain, call.service, dict(call.data)))

    for domain, service in names:
        hass.services.async_register(domain, service, handler)
    return ran


def _call(domain: str, service: str, entity_id: str, data: dict | None = None) -> dict:
    return {
        "service": {"domain": domain, "name": service, **({"data": data} if data else {})},
        "targets": [{"kind": "entity", "ids": [entity_id]}],
    }


_GATED_CALLS = [
    ("lock", "nuki", "nuki", "lock_n_go", {"unlatch": True}, "cap_physical_control"),
    ("lock", "subaru", "subaru", "unlock_specific_door", {"door": "all"}, "cap_physical_control"),
    ("alarm_control_panel", "elkm1", "elkm1", "alarm_arm_home_instant", {"code": "1234"}, "cap_physical_control"),
    ("sensor", "zwave_js", "zwave_js", "set_value",
     {"command_class": 98, "property": "targetMode", "value": 0}, "cap_physical_control"),
    ("sensor", "zwave_js", "zwave_js", "invoke_cc_api",
     {"command_class": 98, "method_name": "set", "parameters": [0]}, "cap_physical_control"),
    ("notify", "telegram_bot", "telegram_bot", "send_message", {"message": "hi"}, "cap_notify"),
    ("notify", "ntfy", "ntfy", "publish", {"message": "hi"}, "cap_notify"),
]


@pytest.mark.parametrize(("entity_domain", "platform", "domain", "service", "service_data", "cap"), _GATED_CALLS)
async def test_deny_refuses_on_call_preview_and_listing_alike(
    hass, entity_domain, platform, domain, service, service_data, cap,
):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, (domain, service))
    data, _ = _data(hass)
    target = _entity(hass, entity_domain, platform, "front")
    key = _key({target: "GREEN"}, cap_search="allow", **{cap: "deny"})
    result = await _call_tool("call_service", _call(domain, service, target, service_data), key, hass, data)
    assert result[1] == "denied"
    assert ran == []
    preview = await _call_tool("dry_run_service", _call(domain, service, target, service_data), key, hass, data)
    assert json.loads(preview[0]["content"][0]["text"])["predicted_outcome"] == "denied"


@pytest.mark.parametrize(("entity_domain", "platform", "domain", "service", "service_data", "cap"), _GATED_CALLS)
async def test_confirm_queues_an_approval_and_replay_runs_it(
    hass, entity_domain, platform, domain, service, service_data, cap,
):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, (domain, service))
    data, pending = _data(hass)
    target = _entity(hass, entity_domain, platform, "front")
    key = _key({target: "GREEN"}, cap_search="allow", **{cap: "confirm"})
    preview = await _call_tool("dry_run_service", _call(domain, service, target, service_data), key, hass, data)
    assert json.loads(preview[0]["content"][0]["text"])["predicted_outcome"] == "pending_approval"
    result = await _call_tool("call_service", _call(domain, service, target, service_data), key, hass, data)
    assert result[1] == "pending_approval"
    assert ran == []
    await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert [row[:2] for row in ran] == [(domain, service)]


@pytest.mark.parametrize(("entity_domain", "platform", "domain", "service", "service_data", "cap"), _GATED_CALLS)
async def test_allow_runs_the_call(hass, entity_domain, platform, domain, service, service_data, cap):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, (domain, service))
    data, _ = _data(hass)
    target = _entity(hass, entity_domain, platform, "front")
    key = _key({target: "GREEN"}, **{cap: "allow"})
    result = await _call_tool("call_service", _call(domain, service, target, service_data), key, hass, data)
    assert result[1] == "allowed", result
    assert [row[:2] for row in ran] == [(domain, service)]


async def test_a_replay_is_refused_once_the_capability_is_denied(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, ("nuki", "lock_n_go"))
    data, pending = _data(hass)
    lock = _entity(hass, "lock", "nuki", "front")
    key = _key({lock: "GREEN"}, cap_physical_control="confirm")
    await _call_tool("call_service", _call("nuki", "lock_n_go", lock), key, hass, data)
    key.cap_physical_control = "deny"
    result = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert result[1] == "denied"
    assert ran == []


@pytest.mark.parametrize("cap", ["deny", "confirm"])
async def test_a_refresh_of_a_lock_needs_no_capability(hass, cap):
    assert await async_setup_component(hass, "homeassistant", {})
    data, _ = _data(hass)
    lock = _entity(hass, "lock", "nuki", "front")
    key = _key({lock: "GREEN"}, cap_physical_control=cap)
    result = await _call_tool("call_service", _call("homeassistant", "update_entity", lock), key, hass, data)
    assert result[1] == "allowed", result


async def test_reading_a_door_code_stays_gated(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, ("schlage", "get_codes"))
    data, _ = _data(hass)
    lock = _entity(hass, "lock", "schlage", "front")
    result = await _call_tool(
        "call_service", _call("schlage", "get_codes", lock), _key({lock: "GREEN"}, cap_physical_control="deny"),
        hass, data,
    )
    assert result[1] == "denied"
    assert ran == []


@pytest.mark.parametrize("selector", ["device", "area"])
@pytest.mark.parametrize("cap", ["deny", "allow"])
async def test_a_device_or_area_target_reaches_no_entity_of_another_domain(hass, selector, cap):
    """Indirect targets expand to the service's own domain, so nuki.lock_n_go finds nothing to act on."""
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, ("nuki", "lock_n_go"))
    data, _ = _data(hass)
    entry = MockConfigEntry(domain="nuki")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("nuki", "1")})
    lock = er.async_get(hass).async_get_or_create(
        "lock", "nuki", "front", suggested_object_id="front", device_id=device.id).entity_id
    hass.states.async_set(lock, "locked")
    key = _key({lock: "GREEN"}, cap_physical_control=cap)
    if selector == "area":
        from homeassistant.helpers import area_registry as ar

        area = ar.async_get(hass).async_create("Hall")
        dr.async_get(hass).async_update_device(device.id, area_id=area.id)
        target = {"kind": "area", "ids": [area.id]}
    else:
        target = {"kind": "device", "ids": [device.id]}
    result = await _call_tool(
        "call_service", {"service": {"domain": "nuki", "name": "lock_n_go"}, "targets": [target]}, key, hass, data,
    )
    assert result[1] == "denied"
    assert ran == []


async def test_a_generic_toggle_of_a_group_of_covers_is_physical(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    data, _ = _data(hass)
    cover = _entity(hass, "cover", "test", "blind")
    group = _entity(hass, "group", "group", "doors", entity_id=[cover])
    result = await _call_tool(
        "call_service", _call("homeassistant", "toggle", group),
        _key({group: "GREEN", cover: "GREEN"}, cap_physical_control="deny"), hass, data,
    )
    assert result[1] == "denied"


@pytest.mark.parametrize(("physical", "notify", "outcome"), [
    ("allow", "deny", "denied"),
    ("deny", "allow", "denied"),
    ("confirm", "allow", "pending_approval"),
    ("allow", "confirm", "pending_approval"),
    ("allow", "allow", "allowed"),
])
async def test_a_call_needing_two_capabilities_passes_only_with_both(hass, physical, notify, outcome):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, ("zzc", "anything"))
    hass.services.async_services()["zzc"]["anything"].schema = None
    from homeassistant.helpers.service import async_set_service_schema

    async_set_service_schema(hass, "zzc", "anything", {"name": "x", "target": {"entity": {}}})
    data, _ = _data(hass)
    lock = _entity(hass, "lock", "test", "front")
    chat = _entity(hass, "notify", "test", "chat")
    args = {"service": {"domain": "zzc", "name": "anything"}, "targets": [{"kind": "entity", "ids": [lock, chat]}]}
    key = _key({lock: "GREEN", chat: "GREEN"}, cap_physical_control=physical, cap_notify=notify, cap_search="allow")
    result = await _call_tool("call_service", args, key, hass, data)
    assert result[1] == outcome
    assert len(ran) == (1 if outcome == "allowed" else 0)
    preview = await _call_tool("dry_run_service", args, key, hass, data)
    assert json.loads(preview[0]["content"][0]["text"])["predicted_outcome"] == outcome


def _device_with_action(hass, entries, *entity_ids: str) -> None:
    esphome_entry(hass, entries, entity_ids=entity_ids, runtime=esphome_runtime(
        esphome_device_info(name="gate"), services={1: SimpleNamespace(name="open", args=[])},
    ))
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "off")


async def test_a_device_action_is_physical_where_the_device_is(hass, esphome_entries):
    """Firmware can drive anything on its device, so a lock there gates its actions."""
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, ("esphome", "gate_open"))
    data, _ = _data(hass)
    sensor = "sensor.gate_state"
    _device_with_action(hass, esphome_entries, sensor, "lock.gate_bolt")
    key = _key({sensor: "GREEN"}, cap_physical_control="deny", cap_search="allow")
    args = {"service": {"domain": "esphome", "name": "gate_open"}}
    result = await _call_tool("call_service", args, key, hass, data)
    assert result[1] == "denied"
    assert ran == []
    preview = await _call_tool("dry_run_service", args, key, hass, data)
    assert json.loads(preview[0]["content"][0]["text"])["predicted_outcome"] == "denied"
    listing = await _call_tool("find_available_actions", {"entity_id": sensor}, key, hass, data)
    row = next(item for item in json.loads(listing[0]["content"][0]["text"])["actions"]
               if item["service"] == "esphome.gate_open")
    assert row["available"] is False and row["reason"] == "requires physical control capability"


async def test_a_device_action_with_nothing_physical_needs_nothing_more(hass, esphome_entries):
    assert await async_setup_component(hass, "homeassistant", {})
    ran = _register(hass, ("esphome", "gate_open"))
    data, _ = _data(hass)
    sensor = "sensor.gate_state"
    _device_with_action(hass, esphome_entries, sensor)
    result = await _call_tool(
        "call_service", {"service": {"domain": "esphome", "name": "gate_open"}},
        _key({sensor: "GREEN"}, cap_physical_control="deny"), hass, data,
    )
    assert result[1] == "allowed", result
    assert len(ran) == 1
