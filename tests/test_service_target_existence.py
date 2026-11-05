"""Service targets never reveal whether an out-of-scope entity exists.

A registered entity the access key cannot see and an ID that names nothing must
produce the same resolution, preview counts and call result, alone or mixed with
a permitted target. An ID with no registry entry is never dispatched, so a
service call cannot create an entity.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import MESA_MODE_OFF
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.policy_engine import resolve_service_targets

GHOST = "light.zz_does_not_exist"
YAML_ONLY = "light.yaml_only"


def _key(entities: dict[str, str], *, pass_through: bool = False) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(entities={eid: PermissionNode(state=s) for eid, s in entities.items()}),
        pass_through=pass_through, cap_search="allow",
    )


def _data() -> PhoenixData:
    store = MagicMock()
    store.get_settings.return_value = SimpleNamespace(mesa_mode=MESA_MODE_OFF)
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=None)


def _args(service: str, target: dict) -> dict:
    return {"service": {"domain": "light", "name": service}, "targets": [target]}


def _body(result: tuple) -> dict:
    return json.loads(result[0]["content"][0]["text"])


@pytest.fixture
async def home(hass):
    """Three registered lights in one area plus a stateful light with no registry entry."""
    assert await async_setup_component(hass, "homeassistant", {})
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    ids = {}
    for name in ("allowed", "readonly", "hidden"):
        entry = registry.async_get_or_create("light", "test", name, suggested_object_id=name)
        registry.async_update_entity(entry.entity_id, area_id=area.id)
        hass.states.async_set(entry.entity_id, "off")
        ids[name] = entry.entity_id
    hass.states.async_set(YAML_ONLY, "off")
    calls = AsyncMock()
    hass.services.async_register("light", "turn_on", calls)
    key = _key({ids["allowed"]: "GREEN", ids["readonly"]: "YELLOW", YAML_ONLY: "GREEN"})
    return SimpleNamespace(hass=hass, area=area, calls=calls, key=key, **ids)


async def test_hidden_and_missing_ids_resolve_identically(home):
    def resolved(entity_id):
        return resolve_service_targets(entity_id=entity_id, service_domain="light",
                                       access_key=home.key, hass=home.hass)

    assert resolved([home.allowed, home.hidden]) == resolved([home.allowed, GHOST]) == ([home.allowed], 1)
    assert resolved(home.hidden) == resolved(GHOST) == ([], 0)
    assert resolved([home.allowed, home.readonly, GHOST]) == ([home.allowed], 2)


async def test_unregistered_stateful_id_is_counted_but_never_dispatched(home):
    for key in (home.key, _key({}, pass_through=True)):
        permitted, visible = resolve_service_targets(
            entity_id=[home.allowed, YAML_ONLY], service_domain="light", access_key=key, hass=home.hass)
        assert permitted == [home.allowed]
        assert visible == 2


async def test_dry_run_previews_hidden_and_missing_identically(home):
    async def preview(ids):
        return _body(await _call_tool("dry_run_service", _args("turn_on", {"kind": "entity", "ids": ids}),
                                      home.key, home.hass, _data()))

    mixed = await preview([home.allowed, home.hidden])
    assert mixed == await preview([home.allowed, GHOST])
    assert (mixed["requested_target_count"], mixed["dropped_count"]) == (1, 0)
    alone = await preview([home.hidden])
    assert alone == await preview([GHOST])
    assert (alone["requested_target_count"], alone["dropped_count"], alone["predicted_outcome"]) == (0, 0, "denied")
    readonly = await preview([home.readonly, YAML_ONLY])
    assert (readonly["requested_target_count"], readonly["dropped_count"]) == (2, 2)
    assert readonly["resolved_entities"] == []


async def test_dry_run_area_counts_only_visible_members(home):
    body = _body(await _call_tool("dry_run_service", _args("turn_on", {"kind": "area", "ids": [home.area.id]}),
                                  home.key, home.hass, _data()))
    assert body["resolved_entities"] == [home.allowed]
    assert (body["requested_target_count"], body["dropped_count"]) == (2, 1)


async def test_call_service_mixed_lists_act_the_same(home):
    async def call(ids):
        home.calls.reset_mock()
        result = await _call_tool("call_service", _args("turn_on", {"kind": "entity", "ids": ids}),
                                  home.key, home.hass, _data())
        dispatched = [c.args[0].data["entity_id"] for c in home.calls.await_args_list]
        return result, dispatched

    hidden, hidden_dispatched = await call([home.allowed, home.hidden])
    missing, missing_dispatched = await call([home.allowed, GHOST])
    assert hidden[1] == missing[1] == "allowed"
    assert hidden_dispatched == missing_dispatched == [[home.allowed]]
    assert hidden[0] == missing[0]
    alone_hidden, none_dispatched = await call([home.hidden])
    alone_missing, _ = await call([GHOST])
    assert alone_hidden[1] == alone_missing[1] == "denied"
    assert alone_hidden[0] == alone_missing[0]
    assert none_dispatched == []
    _, yaml_dispatched = await call([home.allowed, YAML_ONLY])
    assert yaml_dispatched == [[home.allowed]]


async def test_an_area_name_or_alias_targets_like_its_id(home):
    from homeassistant.helpers import area_registry as ar

    ar.async_get(home.hass).async_update(home.area.id, aliases={"Cookhouse"})

    async def preview(ids):
        return _body(await _call_tool("dry_run_service", _args("turn_on", {"kind": "area", "ids": ids}),
                                      home.key, home.hass, _data()))

    by_id = await preview([home.area.id])
    for spelling in ("Kitchen", "kitchen", "cookhouse"):
        assert await preview([spelling]) == by_id
    # An unknown name and an area with nothing visible answer alike, with no note naming an area ID.
    ar.async_get(home.hass).async_create("Attic")
    assert await preview(["Nowhere"]) == await preview(["Attic"])
