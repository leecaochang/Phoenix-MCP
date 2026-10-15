"""Entity hints reach agents on search_entities rows, describe_entity and the context documents.

A per-access-key hint lives on the key's permission node and wins over the global
hint for the same entity. Hint text is administrator prose: a global hint reaches
every key that can see the entity, so entity IDs it names outside the reading
key's scope are replaced and secret-shaped text is scrubbed at delivery, including
for a pass-through key whose Assist-exposure filter hides the named entity. The
context endpoint and the access-context prompt apply the same scoping.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from tests.runtime_fixtures import mesa_off_store

DESK, CEILING, LOCK = "light.desk", "light.ceiling", "lock.front_door"


def _key(**entities: PermissionNode) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}, entities=dict(entities)),
        cap_search="allow",
    )


def _pass_through_key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(), pass_through=True, use_assist_exposure=True, cap_search="allow",
    )


def _data(global_hints: dict[str, str] | None = None) -> PhoenixData:
    store = mesa_off_store()
    store.get_entity_hints.return_value = dict(global_hints or {})
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock())


async def _tool(hass, name: str, args: dict, key: AccessKeyRecord, data: PhoenixData) -> dict:
    result, outcome, _ = await _call_tool(name, args, key, hass, data)
    assert outcome == "allowed", result
    return json.loads(result["content"][0]["text"])


async def _rows(hass, key: AccessKeyRecord, data: PhoenixData) -> dict[str, dict]:
    body = await _tool(hass, "search_entities", {"domain": "light"}, key, data)
    return {row["entity_id"]: row for row in body["entities"]}


@pytest.fixture
async def home(hass):
    for entity_id in (DESK, CEILING, LOCK):
        hass.states.async_set(entity_id, "on", {"friendly_name": entity_id})
    return hass


async def test_global_hint_rides_on_rows_and_describe_and_is_absent_when_unset(home):
    data = _data({DESK: "Rachel's desk lamp, not the ceiling light"})
    rows = await _rows(home, _key(), data)
    assert rows[DESK]["hint"] == "Rachel's desk lamp, not the ceiling light"
    assert "hint" not in rows[CEILING]
    described = await _tool(home, "describe_entity", {"entity_id": DESK}, _key(), data)
    assert described["hint"] == "Rachel's desk lamp, not the ceiling light"
    plain = await _tool(home, "describe_entity", {"entity_id": CEILING}, _key(), data)
    assert "hint" not in plain


async def test_access_key_hint_wins_over_the_global_hint(home):
    key = _key(**{DESK: PermissionNode(state="GREEN", hint="Only this key: the reading lamp")})
    data = _data({DESK: "Global text", CEILING: "Global ceiling text"})
    rows = await _rows(home, key, data)
    assert rows[DESK]["hint"] == "Only this key: the reading lamp"
    assert rows[CEILING]["hint"] == "Global ceiling text"
    described = await _tool(home, "describe_entity", {"entity_id": DESK}, key, data)
    assert described["hint"] == "Only this key: the reading lamp"


async def test_hint_naming_an_entity_outside_scope_is_redacted(home):
    # The key reads lights only, so the lock this global hint names is outside its
    # scope; the light it names stays because the key can read it.
    data = _data({DESK: f"Turn off with {CEILING} once {LOCK} is locked"})
    rows = await _rows(home, _key(), data)
    assert LOCK not in rows[DESK]["hint"]
    assert rows[DESK]["hint"] == f"Turn off with {CEILING} once <redacted-entity> is locked"
    described = await _tool(home, "describe_entity", {"entity_id": DESK}, _key(), data)
    assert described["hint"] == rows[DESK]["hint"]


async def test_hint_secrets_are_scrubbed(home):
    data = _data({DESK: "Snapshot at http://tablet:wall-panel-7@192.168.1.60/snap.jpg"})
    rows = await _rows(home, _key(), data)
    assert "wall-panel-7" not in rows[DESK]["hint"]
    described = await _tool(home, "describe_entity", {"entity_id": DESK}, _key(), data)
    assert "wall-panel-7" not in described["hint"]


async def test_pass_through_key_reads_global_hints_scoped_by_assist_exposure(home):
    assert await async_setup_component(home, "homeassistant", {})
    async_expose_entity(home, "conversation", DESK, True)
    async_expose_entity(home, "conversation", CEILING, False)
    data = _data({DESK: f"Pairs with {CEILING}"})
    rows = await _rows(home, _pass_through_key(), data)
    assert CEILING not in rows
    assert rows[DESK]["hint"] == "Pairs with <redacted-entity>"
    described = await _tool(home, "describe_entity", {"entity_id": DESK}, _pass_through_key(), data)
    assert described["hint"] == "Pairs with <redacted-entity>"


async def test_device_node_hint_is_inherited_by_the_device_entities(hass):
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "lamp")})
    lamp = er.async_get(hass).async_get_or_create(
        "light", "test", "lamp", suggested_object_id="lamp", device_id=device.id, config_entry=entry,
    ).entity_id
    hass.states.async_set(lamp, "on")
    hass.states.async_set(CEILING, "on")
    key = _key()
    key.permissions.devices[device.id] = PermissionNode(state="GREEN", hint="Bedside lamp")
    rows = await _rows(hass, key, _data())
    assert rows[lamp]["hint"] == "Bedside lamp"
    assert "hint" not in rows[CEILING]


async def test_context_documents_redact_hints(home):
    home.data[DOMAIN] = _data({DESK: f"Turn off with {CEILING} once {LOCK} is locked"})
    plain = mcp_view._build_context_plain(_key(), home)
    assert LOCK not in plain
    assert f'- {DESK} (READ/WRITE) - "Turn off with {CEILING} once <redacted-entity> is locked"' in plain
    rows = {row["entity_id"]: row for row in mcp_view._build_context_json(_key(), home)["entities"]}
    assert rows[DESK]["hint"] == f"Turn off with {CEILING} once <redacted-entity> is locked"
    assert "hint" not in rows[CEILING]


async def test_context_json_gives_pass_through_keys_their_global_hints(home):
    assert await async_setup_component(home, "homeassistant", {})
    async_expose_entity(home, "conversation", DESK, True)
    async_expose_entity(home, "conversation", CEILING, False)
    home.data[DOMAIN] = _data({DESK: f"Pairs with {CEILING}"})
    rows = {row["entity_id"]: row for row in mcp_view._build_context_json(_pass_through_key(), home)["entities"]}
    assert CEILING not in rows
    assert rows[DESK]["hint"] == "Pairs with <redacted-entity>"


async def test_access_context_prompt_redacts_a_saved_global_hint(home, access_key_store):
    data = PhoenixData(store=access_key_store, rate_limiter=MagicMock(), audit=MagicMock(), hass=home,
                       enforce_mcp_lifecycle=False)
    home.data[DOMAIN] = data
    try:
        key, _ = await access_key_store.async_create_access_key("hint-key", "admin")
        key.permissions = PermissionTree(domains={"light": PermissionNode("GREEN")})
        await access_key_store.async_set_entity_hint(DESK, f"Leave on until {LOCK} is locked")
        response, *_ = await mcp_view._dispatch_mcp(
            "prompts/get", 1, {"name": "Phoenix MCP access context"}, key, home, data, "127.0.0.1",
            base_url="http://homeassistant.local")
        text = response["result"]["messages"][0]["content"]["text"]
        assert LOCK not in text
        assert f'- {DESK} (READ/WRITE) - "Leave on until <redacted-entity> is locked"' in text
    finally:
        if data.sensor_flush_cancel:
            data.sensor_flush_cancel()
