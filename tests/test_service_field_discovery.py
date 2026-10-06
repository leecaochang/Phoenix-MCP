"""find_available_actions describes each service's fields as this entity can use them."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from tests.runtime_fixtures import mesa_off_store

# The validated description shape: feature filters are integers, attribute
# filters hold enum values, and a section nests its own fields.
DESCRIPTIONS = {"climate": {"set_temperature": {"fields": {
    "temperature": {"required": False, "example": 21, "selector": {"number": {"min": 0, "max": 250, "step": 0.1, "unit_of_measurement": "°"}},
                    "filter": {"supported_features": [1]}},
    "target_temp_high": {"selector": {"number": {"min": 0, "max": 250}}, "filter": {"supported_features": [2]}},
    "hvac_mode": {"selector": {"select": {"options": [{"value": "heat", "label": "Heat"}, "cool"]}},
                  "filter": {"attribute": {"hvac_modes": ["heat", "cool"]}}},
    "advanced": {"collapsed": True, "fields": {"timeout": {"required": True, "selector": {"number": {"min": 1}}}}},
}}}}


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=PermissionTree(domains={"climate": PermissionNode(state="GREEN")}), cap_search="allow")


async def _actions(hass, include_fields=True, descriptions=DESCRIPTIONS):
    hass.services.async_register("climate", "set_temperature", lambda call: None)
    hass.states.async_set("climate.hall", "heat", {"supported_features": 1, "hvac_modes": ["heat", "off"]})
    data = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    loader = AsyncMock(return_value=descriptions) if not isinstance(descriptions, Exception) else AsyncMock(side_effect=descriptions)
    with patch("homeassistant.helpers.service.async_get_all_descriptions", loader):
        result, outcome, _ = await _call_tool("find_available_actions", {"entity_id": "climate.hall", "include_fields": include_fields},
                                              _key(), hass, data)
    assert outcome == "allowed", result
    return json.loads(result["content"][0]["text"])


async def test_fields_are_filtered_to_what_this_entity_supports(hass):
    body = await _actions(hass)
    fields = {row["field"]: row for row in body["actions"][0]["fields"]}
    assert set(fields) == {"temperature", "hvac_mode", "timeout"}
    assert fields["temperature"] == {"field": "temperature", "type": "number", "min": 0, "max": 250, "step": 0.1,
                                     "unit_of_measurement": "°", "example": 21}
    assert fields["hvac_mode"]["options"] == ["heat", "cool"]
    assert fields["timeout"]["required"] is True


async def test_fields_are_opt_in_and_degrade_to_a_warning(hass):
    assert "fields" not in (await _actions(hass, include_fields=False))["actions"][0]
    body = await _actions(hass, descriptions=RuntimeError("missing dependency"))
    assert "fields" not in body["actions"][0] and body["warnings"]
