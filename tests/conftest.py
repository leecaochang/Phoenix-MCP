"""Shared test fixtures for Phoenix MCP integration tests."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest

importlib.import_module("custom_components.phoenix_mcp")

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
async def cleanup_mesa_policy_workers(hass):
    """Shut down mesa-core policy workers after each test.

    Unit tests call its async API outside integration setup, so nothing else stops them.
    """
    from custom_components.phoenix_mcp.mesa_core import ashutdown_policy_workers

    yield
    await ashutdown_policy_workers()


@pytest.fixture
def custom_sentence_recognition(monkeypatch):
    """Mark a real HA match with the metadata used for custom sentence precedence."""
    from custom_components.phoenix_mcp import command_routing

    recognize = command_routing.async_recognize

    async def custom(*args, **kwargs):
        result = await recognize(*args, **kwargs)
        assert result is not None
        result.intent_metadata = {**(result.intent_metadata or {}), "hass_custom_sentence": True}
        return result

    monkeypatch.setattr(command_routing, "async_recognize", custom)


@pytest.fixture
def immediate_authoring_observation(monkeypatch):
    """Skip the authoring registration wait.

    No-op reload tests do not sleep, and observation still runs for real.
    """
    from custom_components.phoenix_mcp import entity_registration

    monkeypatch.setattr(entity_registration, "AUTHORING_REGISTRATION_TIMEOUT_SECONDS", 0)


@pytest.fixture
def immediate_command_observation(monkeypatch):
    """Observe real state without wall-clock waits in dispatch contract tests."""
    from custom_components.phoenix_mcp import command_verification

    monkeypatch.setattr(command_verification, "COMMAND_VERIFICATION_WAIT_SECONDS", 0)


@pytest.fixture(autouse=True)
def isolated_config_dir(hass, tmp_path):
    """Keep any device-YAML writes out of the shared testing config dir."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture
def esphome_entries(hass):
    """Fake esphome config entries at the point the tool enumerates them.

    The tool duck-types entry.state and entry.runtime_data, and the real esphome
    integration cannot load here (aioesphomeapi is an esphome requirement, not a
    Phoenix one, so a LOADED MockConfigEntry explodes on teardown unload). Only
    the enumeration is faked: the entity registry rows stay real, because
    registry-based scoping is the security-critical half and must not be mocked.
    """
    entries: list = []
    real_async_entries = hass.config_entries.async_entries
    real_async_get_entry = hass.config_entries.async_get_entry

    def _async_entries(domain=None, **kwargs):
        if domain == "esphome":
            return list(entries)
        return real_async_entries(domain, **kwargs)

    def _async_get_entry(entry_id):
        # Must agree with _async_entries: code reaching an entry by id (via an
        # entity's config_entry_id) has to see the same object as code that
        # enumerates the domain, or scoping and dispatch disagree.
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        return real_async_get_entry(entry_id)

    with (
        patch.object(hass.config_entries, "async_entries", _async_entries),
        patch.object(hass.config_entries, "async_get_entry", _async_get_entry),
    ):
        yield entries


@pytest.fixture
def esphome_dir(hass, tmp_path):
    """A realistic config/esphome directory, including the excluded entries."""
    # Imported in the body, not at module scope: the sample YAML is asserted
    # against by the module that owns it, and a conftest-level import would run
    # at collection time for every test session.
    from tests.test_mcp_esphome_tools import DEVICE_YAML, SECRETS_YAML

    root = tmp_path / "esphome"
    root.mkdir()
    (root / "rf-blaster1.yaml").write_text(DEVICE_YAML)
    (root / "secrets.yaml").write_text(SECRETS_YAML)
    (root / ".device-builder.json").write_text("{}")
    archive = root / "archive"
    archive.mkdir()
    (archive / "old-device.yaml").write_text("esphome:\n  name: old\n")
    return root


@pytest.fixture
def mock_store_data():
    return {
        "version": 1,
        "tokens": [],
        "archived_tokens": [],
        "settings": {},
    }


@pytest.fixture
def mock_store(mock_store_data):
    store = AsyncMock()
    store.async_load = AsyncMock(return_value=mock_store_data)
    store.async_save = AsyncMock()
    return store


@pytest.fixture
async def access_key_store(hass, mock_store):
    from custom_components.phoenix_mcp.access_key_store import AccessKeyStore

    with patch(
        "custom_components.phoenix_mcp.access_key_store._PhoenixStore",
        return_value=mock_store,
    ):
        instance = await AccessKeyStore.async_create(hass)
    return instance


@pytest.fixture(params=['openai', 'reasoning', 'anthropic', 'text_blocks'])
def provider_context_history(request):
    """Completed tool-exchange history in each provider's wire format.

    Built through the real adapters for openai, reasoning, anthropic and text_blocks.
    """
    import json
    from custom_components.phoenix_mcp.agentcli import ClaudeProvider, OpenAICompatProvider, ProviderConfig

    style = request.param
    history = [{'role': 'user', 'content': 'List the available lights; do not change anything.'}]
    if style == 'text_blocks':
        history.append({'role': 'assistant', 'content': [{'type': 'text', 'text': 'I can list lights.'}]})
        return history
    provider = (ClaudeProvider(ProviderConfig('claude', 'test', '')) if style == 'anthropic'
                else OpenAICompatProvider(ProviderConfig('chatgpt', 'test', '')))
    calls = [('call_a', 'list_entities', {'domain': 'light'}), ('call_b', 'get_datetime', {})]
    if style == 'anthropic':
        assistant = {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Checking the available information.'},
            *[{'type': 'tool_use', 'id': ident, 'name': name, 'input': args} for ident, name, args in calls]]}
    else:
        assistant = {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}
            for ident, name, args in calls]}
        if style == 'reasoning': assistant['reasoning_content'] = 'Only inspect state; do not change devices.'
    provider.append_assistant(history, assistant)
    provider.append_tool_results(history, [
        {'tool_use_id': ident, 'tool_name': name, 'result_text': '{"status":"ok"}', 'is_error': False}
        for ident, name, _ in calls])
    provider.append_assistant(history, {'role': 'assistant', 'content': (
        [{'type': 'text', 'text': 'The read-only checks are complete.'}] if style == 'anthropic'
        else 'The read-only checks are complete.')})
    return history


@pytest.fixture
def phoenix_ai_task_platform(hass, enable_custom_integrations):
    """Return (platform, entry) wired to HA's real AI Task component."""
    import logging
    from datetime import timedelta

    from homeassistant.components.ai_task.const import DATA_COMPONENT
    from homeassistant.helpers.entity_component import EntityComponent
    from homeassistant.helpers.entity_platform import EntityPlatform
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    domain = "phoenix_mcp"
    entry = MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    hass.data[DATA_COMPONENT] = EntityComponent(logging.getLogger(__name__), "ai_task", hass)
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="ai_task",
        platform_name=domain, platform=None, scan_interval=timedelta(seconds=60),
        entity_namespace=None,
    )
    platform.config_entry = entry
    return platform, entry


@pytest.fixture
def requires_child_devices():
    """Run child-device cases only on HA releases that expose that API."""
    from homeassistant.helpers import device_registry as dr
    if not hasattr(dr.DeviceRegistry, "async_get_or_create_child"):
        pytest.skip("Installed Home Assistant has no child-device registry API")
