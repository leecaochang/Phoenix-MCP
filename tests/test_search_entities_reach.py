"""search_entities pages past its limit and resolves floors, area aliases and entity aliases."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from tests.runtime_fixtures import mesa_off_store


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}), cap_search="allow",
    )


async def _search(hass, **args) -> dict:
    data = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    result, outcome, _ = await _call_tool("search_entities", args, _key(), hass, data)
    assert outcome == "allowed", result
    return json.loads(result["content"][0]["text"])


def _light(hass, object_id: str, name: str, **registry) -> str:
    entry = er.async_get(hass).async_get_or_create("light", "test", object_id, suggested_object_id=object_id)
    if registry:
        er.async_get(hass).async_update_entity(entry.entity_id, **registry)
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": name})
    return entry.entity_id


async def test_offset_pages_through_every_match_once(hass):
    ids = {_light(hass, f"lamp_{index}", f"Lamp {index}") for index in range(5)}
    seen: list[str] = []
    page = await _search(hass, domain="light", limit=2)
    while True:
        seen += [row["entity_id"] for row in page["entities"]]
        assert page["total"] == 5
        if not page["has_more"]:
            assert "next_offset" not in page
            break
        page = await _search(hass, domain="light", limit=2, offset=page["next_offset"])
    assert sorted(seen) == sorted(ids) and len(seen) == 5


async def test_floor_and_area_alias_select_areas(hass):
    floor = fr.async_get(hass).async_create("Upstairs", aliases={"second floor"})
    study = ar.async_get(hass).async_create("Study", floor_id=floor.floor_id, aliases={"den"})
    kitchen = ar.async_get(hass).async_create("Kitchen")
    desk = _light(hass, "desk", "Desk", area_id=study.id)
    _light(hass, "counter", "Counter", area_id=kitchen.id)
    for area in ("second floor", "Upstairs", floor.floor_id, "den", "study"):
        body = await _search(hass, area=area)
        assert [row["entity_id"] for row in body["entities"]] == [desk], area
    assert (await _search(hass, area="attic"))["entities"] == []


async def test_entity_alias_matches_and_hidden_is_flagged(hass):
    reading = _light(hass, "lamp_7", "Lamp 7", aliases={"reading lamp"}, hidden_by=er.RegistryEntryHider.USER)
    body = await _search(hass, query="reading lamp")
    assert [row["entity_id"] for row in body["entities"]] == [reading]
    assert body["entities"][0]["hidden"] is True
    assert "_aliases" not in body["entities"][0]
