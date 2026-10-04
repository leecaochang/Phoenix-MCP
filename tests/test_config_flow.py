"""Tests for the Phoenix MCP config flow.

The flow is single-step and single-instance with no remote connection, so there
are no cannot_connect or invalid_auth cases. Covered: the initial form, entry
creation, and the already_configured abort.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.const import DOMAIN


@pytest.fixture(autouse=True)
def _enable(hass, enable_custom_integrations):
    """Make the integration discoverable to the config-entries machinery.

    The manifest depends on `frontend`, whose `hass_frontend` package is absent in
    tests, so mark it loaded to stop flow init from trying to bootstrap it.
    """
    hass.config.components.add("frontend")
    yield


async def test_user_step_shows_form(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_creates_entry(hass: HomeAssistant):
    # Patch setup so the flow test does not trigger full integration setup.
    with patch("custom_components.phoenix_mcp.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={})
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Phoenix MCP"
    assert result2["data"] == {}


async def test_single_instance_only(hass: HomeAssistant):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
