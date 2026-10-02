"""Real HA entry setup, reload, route ownership, unload and credential deletion."""

import copy
import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.agentcli import _get_secret_store
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.token_store import TokenStore


@pytest.mark.parametrize("stored", [
    {"version": 1, "data": {"records": {"a": 1}}},
    {"version": 1, "data": {"records": ["x"]}},
    {"version": 1, "data": {"records": None}},
    {"version": 2, "data": {"records": []}},
])
async def test_unreadable_calendar_journal_does_not_abort_entry(hass, hass_storage, enable_custom_integrations, stored):
    key = "phoenix_mcp_calendar_writes"
    hass_storage[key] = copy.deepcopy(stored)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    data = hass.data[DOMAIN]
    assert data.ready and data.calendar_writes.unavailable
    with pytest.raises(ValueError, match="Calendar recovery storage is unavailable"):
        await data.calendar_writes.load()
    with pytest.raises(ValueError):
        await data.calendar_writes.save()
    assert hass_storage[key] == stored
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_real_entry_reload_has_stable_routes_and_deletion_retires_credentials(hass, enable_custom_integrations, caplog):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    data = hass.data[DOMAIN]
    assert data.ready and not data.mesa_setup_failed
    token, _raw = await data.store.async_create_token("lifecycle", "admin")
    secrets = await _get_secret_store(hass)
    await secrets.add("openai", {"api_key": "synthetic-example-key", "model": "example"})
    await data.store.async_patch_settings(assist_bound_token_id=token.id,
        voice_agent_enabled=True, voice_agent_token_id=token.id,
        ai_task_enabled=True, ai_task_token_id=token.id)
    resources = len(list(hass.http.app.router.resources()))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(list(hass.http.app.router.resources())) == resources
    assert hass.data[DOMAIN] is not data
    assert hass.data[DOMAIN].store.get_token_by_id(token.id) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data
    assert not any("UrlManager" in record.message or "setup rollback" in record.message for record in caplog.records if record.levelno >= 40)
    assert (await hass.config_entries.async_remove(entry.entry_id))["require_restart"] is False
    restored = await TokenStore.async_create(hass)
    assert restored.get_token_by_id(token.id) is None
    assert restored.list_archived()[0].revoked
    settings = restored.get_settings()
    assert settings.assist_bound_token_id is None
    assert settings.voice_agent_token_id is None and not settings.voice_agent_enabled
    assert settings.ai_task_token_id is None and not settings.ai_task_enabled
    assert (await _get_secret_store(hass)).list_instances() == []
