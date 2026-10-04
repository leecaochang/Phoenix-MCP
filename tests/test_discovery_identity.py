"""Pin loaded ID-less consumer identity, scope, and bounded visibility work."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.tools import discovery
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord


def _access_key():
    return AccessKeyRecord(id="read", name="read", access_key_hash="hash", created_by="admin", created_at=utcnow(),
        cap_search="allow", permissions=PermissionTree(domains={
            kind: PermissionNode(state="GREEN") for kind in ("light", "automation", "scene")}))


def _body(result):
    assert result[1] == "allowed", result
    return json.loads(result[0]["content"][0]["text"])


@pytest.fixture
async def idless_home(hass):
    hass.states.async_set("light.visible", "off")
    automation = {"alias": "Shared name", "triggers": [{"trigger": "state", "entity_id": "light.visible", "to": "on"}], "actions": []}
    other = {**automation, "id": "other", "triggers": [{"trigger": "event", "event_type": "unused"}]}
    scene = {"name": "Shared scene", "entities": {"light.visible": "on"}}
    for filename, configs in (("automations.yaml", [other, automation]), ("scenes.yaml", [scene])):
        with open(hass.config.path(filename), "w") as file:
            yaml.safe_dump(configs, file)
    assert await async_setup_component(hass, "automation", {"automation": [other, automation]})
    assert await async_setup_component(hass, "scene", {"scene": [scene]})
    await hass.async_block_till_done()
    auto = next(entity for entity in hass.data["automation"].entities if entity.raw_config == automation)
    loaded_scene = next(iter(hass.data["scene"].entities))
    assert auto.entity_id != "automation.shared_name"
    assert er.async_get(hass).async_get(auto.entity_id) is None
    return auto.entity_id, loaded_scene.entity_id


@pytest.mark.parametrize("tool", ["get_relationships", "describe_entity", "whatif"])
@pytest.mark.parametrize("hidden", [False, True])
async def test_idless_consumers_use_loaded_identity_without_guessing(hass, idless_home, tool, hidden):
    access_key = _access_key()
    access_key.permissions.entities["automation.shared_name"] = PermissionNode(state="RED")
    if hidden:
        for eid in idless_home:
            access_key.permissions.entities[eid] = PermissionNode(state="RED")
    # Isolate the source scan from the optional graph so it cannot mask omissions.
    with patch.object(discovery, "_relationship_graph_hits", return_value=(set(), [], False)):
        if tool == "get_relationships":
            body = _body(await discovery._tool_get_relationships({"scope": {"kind": "entity", "id": "light.visible"}}, access_key, hass))
            rows = body["consumers"]
        elif tool == "describe_entity":
            body = _body(await discovery._tool_describe_entity({"entity_id": "light.visible"}, access_key, hass,
                SimpleNamespace(mesa=None, store=SimpleNamespace(get_settings=lambda: None))))
            rows = body["referenced_by"]
        else:
            body = _body(await discovery._tool_whatif({"entity_id": "light.visible", "hypothetical_state": "on"}, access_key, hass))
            rows = body["candidates"]
    assert len(rows) == (0 if hidden else (1 if tool == "whatif" else 2)), body
    assert "_identity_config" not in json.dumps(body)
    gaps = body["referenced_by_not_searched" if tool == "describe_entity" else "not_searched"]
    assert any(gap["kind"] == "consumer_identity" for gap in gaps)
    if not hidden:
        assert idless_home[0].partition(".")[2] in json.dumps(rows)


@pytest.mark.parametrize("name", [2024, ["bad"], {"bad": "name"}, True])
@pytest.mark.parametrize("query", ["2024", "alhpa", ""])
async def test_search_handles_malformed_friendly_names(hass, name, query):
    hass.states.async_set("light.alpha", "on", {"friendly_name": "Alpha"})
    hass.states.async_set("light.odd", "off", {"friendly_name": name})
    body = _body(await discovery._tool_search_entities({"query": query}, _access_key(), hass,
        SimpleNamespace(mesa=None, store=SimpleNamespace(get_settings=lambda: None))))
    assert all(row["friendly_name"] is None or isinstance(row["friendly_name"], str) for row in body["entities"])


async def test_unloaded_consumer_reports_coverage_without_disclosing_identity(hass):
    hass.states.async_set("light.visible", "off")
    config = {"alias": "Private unloaded consumer", "triggers": [{"trigger": "state", "entity_id": "light.visible"}], "actions": []}
    with open(hass.config.path("automations.yaml"), "w") as file:
        yaml.safe_dump([config], file)
    with patch.object(discovery, "_relationship_graph_hits", return_value=(set(), [], False)):
        body = _body(await discovery._tool_get_relationships({"scope": {"kind": "entity", "id": "light.visible"}}, _access_key(), hass))
    assert body["consumers"] == []
    assert "Private unloaded consumer" not in json.dumps(body)
    gap = next(gap for gap in body["not_searched"] if gap["kind"] == "consumer_identity")
    assert set(gap) == {"kind", "reason"}


async def test_many_reference_holders_do_not_rescan_the_registry(hass, monkeypatch):
    """Exercise the response with many consumers and repeated dangling references."""
    registry = er.async_get(hass)
    hass.states.async_set("light.visible", "off")
    configs = []
    for number in range(100):
        entry = registry.async_get_or_create("automation", "automation", str(number), suggested_object_id=f"consumer_{number}")
        hass.states.async_set(entry.entity_id, "on")
        configs.append({"id": str(number), "alias": f"Consumer {number}", "triggers": [{"trigger": "state", "entity_id": "light.visible"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": [f"light.missing_{i}" for i in range(10)]}}]})
    with open(hass.config.path("automations.yaml"), "w") as file:
        yaml.safe_dump(configs, file)

    original_values = type(registry.entities).values
    scans = 0

    def counted_values(self):
        nonlocal scans
        scans += 1
        return original_values(self)

    monkeypatch.setattr(type(registry.entities), "values", counted_values)
    with patch.object(discovery, "_relationship_graph_hits", return_value=(set(), [], False)):
        body = _body(await discovery._tool_get_relationships({"scope": {"kind": "entity", "id": "light.visible"}}, _access_key(), hass))
    assert len(body["consumers"]) == 100
    assert len(body["dangling_references"]) == 10
    assert scans <= 2
