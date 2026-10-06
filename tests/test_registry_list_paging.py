"""Registry lists filter and page; floors list the accessible areas on them."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.tool_common import offset_page


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
                           cap_registry_read="allow", **caps)


async def _list(hass, tool, args, key=None):
    result, outcome, _ = await _call_tool(tool, args, key or _key(), hass, MagicMock())
    assert outcome == "allowed", result
    return json.loads(result["content"][0]["text"])


def _home(hass):
    floor = fr.async_get(hass).async_create("Upstairs")
    study = ar.async_get(hass).async_create("Study", floor_id=floor.floor_id)
    hall = ar.async_get(hass).async_create("Hall")
    hue = MockConfigEntry(domain="hue", title="Bridge")
    zha = MockConfigEntry(domain="zha", title="Stick")
    for entry in (hue, zha):
        entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    for index, (entry, area, model) in enumerate(((hue, study, "Bloom"), (zha, hall, "Bulb"), (hue, hall, "Strip"))):
        device = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("t", str(index))},
                                             name=f"Lamp {index}", manufacturer="Acme", model=model)
        devices.async_update_device(device.id, area_id=area.id)
        light = er.async_get(hass).async_get_or_create("light", "t", str(index), device_id=device.id)
        hass.states.async_set(light.entity_id, "on", {})
    return floor, study, hall


async def test_list_devices_filters_by_area_floor_integration_and_query(hass):
    floor, study, hall = _home(hass)
    names = lambda body: sorted(row["name"] for row in body["devices"])  # noqa: E731
    assert names(await _list(hass, "list_devices", {"area": "Upstairs"})) == ["Lamp 0"]
    assert names(await _list(hass, "list_devices", {"area": hall.id})) == ["Lamp 1", "Lamp 2"]
    assert names(await _list(hass, "list_devices", {"integration": "hue"})) == ["Lamp 0", "Lamp 2"]
    assert names(await _list(hass, "list_devices", {"query": "strip"})) == ["Lamp 2"]
    page = await _list(hass, "list_devices", {"limit": 2})
    assert (page["total"], page["count"], page["has_more"], page["next_offset"]) == (3, 2, True, 2)
    rest = await _list(hass, "list_devices", {"limit": 2, "offset": 2})
    assert rest["count"] == 1 and rest["has_more"] is False and "next_offset" not in rest


async def test_list_floors_names_its_accessible_areas(hass):
    _, study, _ = _home(hass)
    body = await _list(hass, "list_floors", {})
    assert body["floors"][0]["areas"] == [{"area_id": study.id, "name": "Study"}]


async def test_list_integrations_filters_and_pages(hass):
    _home(hass)
    key = _key(cap_integration_write="allow")
    assert [row["domain"] for row in (await _list(hass, "list_integrations", {"domain": "zha"}, key))["integrations"]] == ["zha"]
    assert [row["title"] for row in (await _list(hass, "list_integrations", {"query": "bridge"}, key))["integrations"]] == ["Bridge"]
    page = await _list(hass, "list_integrations", {"limit": 1}, key)
    assert page["total"] == 2 and page["has_more"] is True


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": True}, {"offset": -1}, {"offset": "2"}])
def test_offset_page_refuses_bad_paging(args):
    assert isinstance(offset_page([1, 2, 3], args, default_limit=2, max_limit=10), str)
