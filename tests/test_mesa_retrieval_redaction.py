"""MESA retrieval tools and get_overview name no entity the key cannot read.

A readable entity's profile, or an area or device layer above it, can name another entity in its
text and limit predicates; the gate path already redacts them, and retrieval does the same.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import tool_dispatch
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile

HIDDEN = "alarm_control_panel.hidden_vault"
VISIBLE_REF = "light.hall"


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id="tok", name="scoped", access_key_hash="x", created_at=utcnow(), created_by="admin",
        persona="read_only", cap_config_read="allow", cap_search="allow",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
    )


def _layer(reason: str) -> dict:
    return {"semantic_profile": {"operational_boundaries": {
        "control_mode": "prohibited", "control_reason": reason,
        "declared_limits": [{"id": "vault", "human_reason": reason,
                             "predicate": {"entity": HIDDEN, "operator": "eq", "value": "armed_away"},
                             "limit": {"service": "light.turn_on", "parameter": "brightness", "max_value": 10}}]}}}


async def _env(hass: HomeAssistant, layer_kind: str) -> tuple[str, PhoenixData]:
    entry = MockConfigEntry(domain="demo")
    entry.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Study")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("demo", "lamp")}, name="Lamp")
    lamp = er.async_get(hass).async_get_or_create(
        "light", "demo", "desk", suggested_object_id="desk", device_id=device.id, config_entry=entry)
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    for entity_id, state in ((lamp.entity_id, "on"), (VISIBLE_REF, "on"), (HIDDEN, "disarmed")):
        hass.states.async_set(entity_id, state, {})
    hass.services.async_register("light", "turn_on", AsyncMock())
    runtime = await async_setup_mesa(hass, "enforced")
    layer = _layer(f"Only while {HIDDEN} is disarmed (see also {VISIBLE_REF})")
    if layer_kind == "entity":
        runtime.store.set(lamp.entity_id, SemanticProfile.from_dict(lamp.entity_id, layer, default_origin=MetadataOrigin.USER))
    elif layer_kind == "device":
        runtime.store.set_device_profile(device.id, SemanticProfile.from_dict(device.id, layer, default_origin=MetadataOrigin.USER))
    else:
        runtime.store.set_area_profile(area.id, SemanticProfile.from_dict(area.id, layer, default_origin=MetadataOrigin.USER))
    store = MagicMock()
    store.get_settings.return_value.mesa_mode = "enforced"
    audit = MagicMock()
    audit.async_log = AsyncMock()
    return lamp.entity_id, PhoenixData(store=store, rate_limiter=MagicMock(), audit=audit, mesa=runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("layer_kind", ["entity", "device", "area"])
async def test_retrieval_redacts_hidden_entities_in_profile_text_and_predicates(hass: HomeAssistant, layer_kind: str):
    lamp, data = await _env(hass, layer_kind)
    texts = {}
    for name, args in (("mesa_get_profile", {"entity_id": lamp}),
                       ("mesa_explain_profile", {"entity_id": lamp}),
                       ("mesa_query_profiles", {"include_fields": ["operational_boundaries"]}),
                       ("get_overview", {})):
        result, outcome, _ = await tool_dispatch._call_tool(name, args, _key(), hass, data, request_id="r1")
        assert outcome == "allowed", (name, result)
        texts[name] = result["content"][0]["text"]
    for name, text in texts.items():
        assert HIDDEN not in text, name
        assert "armed_away" not in text, name
    # A readable entity named in the same text stays, and so does the limit's service.
    assert VISIBLE_REF in texts["mesa_get_profile"]
    assert "light.turn_on" in texts["mesa_get_profile"]
    if layer_kind == "entity":
        assert VISIBLE_REF in texts["get_overview"]
