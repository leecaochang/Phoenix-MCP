"""A composed name carries a parent device's name only for a key that can read that parent.

From Home Assistant 2026.11 the name of an entity on a child device starts with its main
parent. A key granted the entity alone gets not found for that parent, so neither the name,
the search that matches it, nor a resolved alias may give the parent away.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry
import pytest

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.version_store import VersionStore
from tests.runtime_fixtures import mesa_off_store

PARENT = "Vault Hub"
COMPOSED = "Vault Hub Plug Relay"
PLAIN = "Plug Relay"


def _key(permissions: PermissionTree, **fields) -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=permissions, cap_registry_read="allow", cap_registry_write="allow",
                           cap_search="allow", **fields)


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


async def _call(hass, tool, args, key):
    content, outcome, _ = await _call_tool(tool, args, key, hass, _data())
    text = content["content"][0]["text"]
    return (json.loads(text) if text.startswith("{") else text), outcome


@pytest.fixture
def home(hass):
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    parent = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "p")}, name=PARENT)
    if not hasattr(devices, "async_get_or_create_child"):
        pytest.skip("Child devices arrived in Home Assistant 2026.9.")
    child = devices.async_get_or_create_child(config_entry_id=entry.entry_id, identifiers={("test", "c")},
                                              name="Plug", parent_device_id=parent.id)
    # The entity id does not embed the parent, as after a rename of the parent.
    relay = entities.async_get_or_create("switch", "test", "relay", config_entry=entry, device_id=child.id,
                                         suggested_object_id="plug_relay", original_name="Relay", has_entity_name=True)
    lamp = entities.async_get_or_create("light", "test", "lamp", config_entry=entry, device_id=parent.id,
                                        original_name="Lamp", has_entity_name=True)
    hass.states.async_set(relay.entity_id, "on", {"friendly_name": PLAIN})
    hass.states.async_set(lamp.entity_id, "on", {"friendly_name": f"{PARENT} Lamp"})
    assert er.async_get_full_entity_name(hass, relay) == COMPOSED
    return SimpleNamespace(parent=parent, child=child, relay=relay.entity_id, lamp=lamp.entity_id)


def _entities(home, **more) -> PermissionTree:
    return PermissionTree(entities={home.relay: PermissionNode(state="GREEN")}, **more)


def _hidden(home) -> list[AccessKeyRecord]:
    """A key with no way to the parent, and one with the parent RED but the entity itself granted."""
    return [_key(_entities(home)), _key(_entities(home, devices={home.parent.id: PermissionNode(state="RED")}))]


async def _reads(hass, home, key) -> dict:
    described, _ = await _call(hass, "describe_entity", {"entity_id": home.relay}, key)
    listed, _ = await _call(hass, "search_entities", {"domain": "switch"}, key)
    return {"described": described, "row": next((row for row in listed["entities"] if row["entity_id"] == home.relay), None)}


async def test_an_ungranted_parent_stays_out_of_the_name(hass, home):
    key = _key(_entities(home))
    assert (await _call(hass, "get_device", {"device_id": home.parent.id}, key))[1] == "not_found"
    reads = await _reads(hass, home, key)
    assert reads["row"]["friendly_name"] == PLAIN and "composed_name" not in reads["row"]
    assert "composed_name" not in reads["described"]
    assert PARENT not in json.dumps(reads)


async def test_search_does_not_match_a_hidden_parent(hass, home):
    for key in _hidden(home):
        found, _ = await _call(hass, "search_entities", {"query": "vault", "domain": "switch"}, key)
        assert found["entities"] == []
    found, _ = await _call(hass, "search_entities", {"query": "relay", "domain": "switch"}, _key(_entities(home)))
    assert [row["entity_id"] for row in found["entities"]] == [home.relay]


async def test_a_resolved_alias_does_not_carry_a_hidden_parent(hass, home):
    er.async_get(hass).async_update_entity(home.relay, aliases=[er.COMPUTED_NAME, "Boiler"])
    described, _ = await _call(hass, "describe_entity", {"entity_id": home.relay}, _key(_entities(home)))
    assert described["aliases"] == [PLAIN, "Boiler"]
    granted = _key(_entities(home, devices={home.parent.id: PermissionNode(state="GREEN")}))
    described, _ = await _call(hass, "describe_entity", {"entity_id": home.relay}, granted)
    assert described["aliases"] == [COMPOSED, "Boiler"]


async def test_a_red_parent_hides_it_whichever_way_the_entity_is_reached(hass, home):
    for key in _hidden(home):
        reads = await _reads(hass, home, key)
        assert PARENT not in json.dumps(reads)


async def test_a_granted_parent_keeps_the_composed_name(hass, home):
    key = _key(_entities(home, devices={home.parent.id: PermissionNode(state="YELLOW")}))
    assert (await _call(hass, "get_device", {"device_id": home.parent.id}, key))[1] == "allowed"
    reads = await _reads(hass, home, key)
    assert reads["row"]["composed_name"] == COMPOSED and reads["described"]["composed_name"] == COMPOSED
    found, _ = await _call(hass, "search_entities", {"query": "vault", "domain": "switch"}, key)
    assert [row["entity_id"] for row in found["entities"]] == [home.relay]


async def test_a_parent_reached_through_one_of_its_entities_is_readable(hass, home):
    key = _key(PermissionTree(entities={home.relay: PermissionNode(state="GREEN"),
                                        home.lamp: PermissionNode(state="YELLOW")}))
    assert (await _call(hass, "get_device", {"device_id": home.parent.id}, key))[1] == "allowed"
    assert (await _reads(hass, home, key))["row"]["composed_name"] == COMPOSED


async def test_pass_through_keeps_the_composed_name(hass, home):
    reads = await _reads(hass, home, _key(PermissionTree(), pass_through=True))
    assert reads["row"]["composed_name"] == COMPOSED


async def test_pass_through_follows_the_assist_filter_on_the_parent(hass, home):
    """The parent's entities are not exposed, so get_device hides it and the name must too."""
    key = _key(PermissionTree(), pass_through=True, use_assist_exposure=True)
    with patch("custom_components.phoenix_mcp.policy_engine._assist_exposed",
               side_effect=lambda _hass, entity_id: entity_id == home.relay):
        assert (await _call(hass, "get_device", {"device_id": home.parent.id}, key))[1] == "not_found"
        reads = await _reads(hass, home, key)
    assert reads["row"] is not None and "composed_name" not in reads["row"]


async def test_set_entity_answers_with_the_same_name_a_read_gives(hass, home):
    key = _key(_entities(home))
    body, outcome = await _call(hass, "set_entity", {"entity_id": home.relay, "icon": "mdi:power"}, key)
    assert outcome == "allowed", body
    assert body["updated"]["composed_name"] == PLAIN and PARENT not in json.dumps(body)
    granted = _key(_entities(home, devices={home.parent.id: PermissionNode(state="GREEN")}))
    body, _ = await _call(hass, "set_entity", {"entity_id": home.relay, "icon": "mdi:flash"}, granted)
    assert body["updated"]["composed_name"] == COMPOSED


async def test_an_entity_on_a_plain_device_is_untouched(hass, home):
    described, _ = await _call(hass, "describe_entity", {"entity_id": home.lamp},
                               _key(PermissionTree(entities={home.lamp: PermissionNode(state="GREEN")})))
    assert described["attributes"]["friendly_name"] == f"{PARENT} Lamp"
