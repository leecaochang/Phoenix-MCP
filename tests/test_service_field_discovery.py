"""find_available_actions describes each service's fields as this entity can use them."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import RUNTIME_SERVICE_GUIDANCE, SERVICE_FIELD_MAX_DESCRIPTION
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
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


_TRANSLATIONS = "custom_components.phoenix_mcp.tools.discovery.async_get_translations"
_FIELD_KEY = "component.climate.services.set_temperature.fields.temperature.description"
_SERVICE_KEY = "component.climate.services.set_temperature.description"


def _key(domain: str = "climate") -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=PermissionTree(domains={domain: PermissionNode(state="GREEN")}), cap_search="allow")


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())


async def _actions(hass, include_fields=True, descriptions=DESCRIPTIONS):
    hass.services.async_register("climate", "set_temperature", lambda call: None)
    hass.states.async_set("climate.hall", "heat", {"supported_features": 1, "hvac_modes": ["heat", "off"]})
    data = _data()
    loader = AsyncMock(return_value=descriptions) if not isinstance(descriptions, Exception) else AsyncMock(side_effect=descriptions)
    with patch("homeassistant.helpers.service.async_get_all_descriptions", loader):
        result, outcome, _ = await _call_tool("find_available_actions", {"entity_id": "climate.hall", "include_fields": include_fields},
                                              _key(), hass, data)
    assert outcome == "allowed", result
    return json.loads(result["content"][0]["text"])


async def test_fields_are_filtered_to_what_this_entity_supports(hass):
    with patch(_TRANSLATIONS, AsyncMock(return_value={})):
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


async def _translated(hass, translations, descriptions=DESCRIPTIONS):
    with patch(_TRANSLATIONS, AsyncMock(return_value=translations)) as reader:
        body = await _actions(hass, descriptions=descriptions)
    reader.assert_awaited_once_with(hass, "en", "services", {"climate"})
    return body["actions"][0], {row["field"]: row for row in body["actions"][0]["fields"]}


async def test_real_core_text_reaches_fields_and_section_children(hass):
    """Core keeps this text in translations, and a section's child uses the flat field key."""
    assert await async_setup_component(hass, "light", {})
    hass.states.async_set("light.hall", "on", {"supported_features": 32, "supported_color_modes": ["brightness"]})
    text = await async_get_translations(hass, "en", "services", {"light"})

    async def read(include_fields):
        result, outcome, _ = await _call_tool("find_available_actions", {"entity_id": "light.hall", "include_fields": include_fields},
                                              _key("light"), hass, _data())
        assert outcome == "allowed", result
        return next(row for row in json.loads(result["content"][0]["text"])["actions"] if row["service"] == "light.turn_on")

    turn_on = await read(True)
    prefix = "component.light.services.turn_on"
    assert turn_on["description"] == text[f"{prefix}.description"][:SERVICE_FIELD_MAX_DESCRIPTION]
    fields = {row["field"]: row for row in turn_on["fields"]}
    assert fields["brightness_pct"]["description"] == text[f"{prefix}.fields.brightness_pct.description"][:SERVICE_FIELD_MAX_DESCRIPTION]
    assert fields["transition"]["description"] == text[f"{prefix}.fields.transition.description"][:SERVICE_FIELD_MAX_DESCRIPTION]
    assert "description" not in await read(False)


async def test_translated_text_wins_and_core_text_is_the_fallback(hass):
    described = {"climate": {"set_temperature": {"description": "Core service text", "fields": {
        "temperature": {"description": "Core field text", "selector": {"number": {}}},
        "dynamic": {"description": "Only Core has this", "selector": {"text": {}}},
        "silent": {"selector": {"text": {}}},
    }}}}
    entry, fields = await _translated(hass, {_FIELD_KEY: "Translated"}, described)
    assert fields["temperature"]["description"] == "Translated"
    assert fields["dynamic"]["description"] == "Only Core has this"
    assert "description" not in fields["silent"]
    # Core's service text is never shown: a named script's needs cap_script_write.
    assert "description" not in entry


async def test_core_fallback_is_scrubbed_before_its_bound_and_shipped_text_is_not(hass):
    hidden = "lock.front_door"
    described = {"climate": {"set_temperature": {"fields": {
        "temperature": {"description": f"Unlocks {hidden} first. " + "x" * 200, "selector": {"number": {}}},
        "hvac_mode": {"selector": {"text": {}}},
    }}}}
    shipped = "component.climate.services.set_temperature.fields.hvac_mode.description"
    _, fields = await _translated(hass, {shipped: "See known_devices.yaml"}, described)
    assert hidden not in fields["temperature"]["description"]
    assert len(fields["temperature"]["description"]) == SERVICE_FIELD_MAX_DESCRIPTION
    assert fields["hvac_mode"]["description"] == "See known_devices.yaml"


async def test_service_text_is_bounded_and_placeholders_fill_known_keys_only(hass):
    described = {"climate": {"set_temperature": {
        "description_placeholders": {"url": "see lock.front_door", "known": "filled", "0": "zero"}, "fields": {
            "temperature": {"selector": {"number": {}}},
        }}}}
    text = "{known} {missing} {0.__class__} {url}"
    entry, fields = await _translated(hass, {_FIELD_KEY: text, _SERVICE_KEY: "y" * 500}, described)
    assert fields["temperature"]["description"].startswith("filled {missing} {0.__class__} ")
    assert "lock.front_door" not in fields["temperature"]["description"]
    assert entry["description"] == "y" * SERVICE_FIELD_MAX_DESCRIPTION


async def test_a_failed_translation_read_drops_the_text_and_keeps_the_fields(hass):
    with patch(_TRANSLATIONS, AsyncMock(side_effect=RuntimeError("no text"))):
        body = await _actions(hass)
    entry = body["actions"][0]
    assert {row["field"] for row in entry["fields"]} == {"temperature", "hvac_mode", "timeout"}
    assert "description" not in entry and "warnings" not in body


async def test_translations_are_not_read_without_fields(hass):
    with patch(_TRANSLATIONS, AsyncMock()) as reader:
        await _actions(hass, include_fields=False)
        await _actions(hass, descriptions=RuntimeError("missing dependency"))
    reader.assert_not_awaited()


async def test_authority_is_checked_after_the_translation_wait(hass):
    order = []

    async def wait(*_args):
        order.append("read")
        return {}

    with patch(_TRANSLATIONS, wait), patch("custom_components.phoenix_mcp.tools.discovery.check_request_authority",
                                           side_effect=lambda: order.append("check")):
        await _actions(hass)
    assert order == ["read", "check"]


@pytest.mark.parametrize("service", ["turn_on", "toggle"])
async def test_runtime_guidance_is_never_replaced(hass, service):
    hass.services.async_register("script", service, lambda call: None)
    hass.states.async_set("script.hall", "off")
    text = {f"component.script.services.{service}.description": "Core's generic text"}
    loader = AsyncMock(return_value={"script": {service: {"fields": {}}}})
    with patch("homeassistant.helpers.service.async_get_all_descriptions", loader), patch(_TRANSLATIONS, AsyncMock(return_value=text)):
        result, outcome, _ = await _call_tool("find_available_actions", {"entity_id": "script.hall", "include_fields": True},
                                              _key("script"), hass, _data())
    assert outcome == "allowed", result
    rows = {row["service"]: row for row in json.loads(result["content"][0]["text"])["actions"]}
    assert rows[f"script.{service}"]["description"] == RUNTIME_SERVICE_GUIDANCE[f"script/{service}"]
