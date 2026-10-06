"""An unknown label or category refusal names the IDs that do exist."""

from __future__ import annotations

from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import label_registry as lr

from custom_components.phoenix_mcp.tool_common import unknown_category_message, unknown_label_message


async def test_unknown_label_lists_existing_labels(hass):
    assert "No labels exist yet" in unknown_label_message(hass, "guest")
    lr.async_get(hass).async_create("Holiday lights")
    message = unknown_label_message(hass, "holiday")
    assert message.startswith("Unknown label id: holiday.") and "holiday_lights (Holiday lights)" in message


async def test_unknown_category_lists_that_scopes_categories(hass):
    created = cr.async_get(hass).async_create(scope="automation", name="Security")
    message = unknown_category_message(hass, "automation", "safety")
    assert f"{created.category_id} (Security)" in message
    assert "no categories yet" in unknown_category_message(hass, "script", "safety")
