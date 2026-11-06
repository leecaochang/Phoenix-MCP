"""Shared test fixtures for Phoenix MCP integration tests."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from unittest.mock import AsyncMock, patch

import pytest

importlib.import_module("custom_components.phoenix_mcp")
# Entry setup registers the tool handlers; a test that calls dispatch directly has no setup.
importlib.import_module("custom_components.phoenix_mcp.tool_registration")

# Home Assistant's own test package loads itself and starts at 2026.11. The 2026.7
# floor job keeps the third-party package it replaced, under the same import names.
if importlib.util.find_spec("pytest_homeassistant") is None:
    pytest_plugins = "pytest_homeassistant_custom_component"
    for module in ("", ".common", ".components.recorder.common", ".test_util", ".test_util.aiohttp"):
        sys.modules[f"pytest_homeassistant{module}"] = importlib.import_module(f"pytest_homeassistant_custom_component{module}")


@pytest.fixture(autouse=True, scope="session")
def loaded_catalogs():
    """Entry setup loads the string catalogs; tests that render text without setup need them too."""
    from custom_components.phoenix_mcp.catalog_text import load_catalogs

    load_catalogs()


@pytest.fixture(autouse=True)
async def cleanup_mesa_policy_workers(hass):
    """Shut down mesa-core policy workers after each test.

    Unit tests call its async API outside integration setup, so nothing else stops them.
    """
    from custom_components.phoenix_mcp.mesa_core import ashutdown_policy_workers

    yield
    await ashutdown_policy_workers()


@pytest.fixture(autouse=True)
def forget_unsettled_ws_commands():
    """A test's still-running HA command must not block review in the next test.

    The tracking set is process-wide by design; each test has its own loop.
    """
    from custom_components.phoenix_mcp import ws_dispatch

    yield
    ws_dispatch._UNSETTLED.clear()


@pytest.fixture
def custom_sentence_recognition(monkeypatch):
    """Mark a real HA match with the metadata used for custom sentence precedence."""
    from custom_components.phoenix_mcp.routing import command_routing

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
    from custom_components.phoenix_mcp.routing import command_verification

    monkeypatch.setattr(command_verification, "COMMAND_VERIFICATION_WAIT_SECONDS", 0)


@pytest.fixture(autouse=True)
def isolated_config_dir(hass, tmp_path):
    """Give each test its own config dir, so files a test lays out never leak between tests."""
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
    from tests.runtime_fixtures import DEVICE_YAML, SECRETS_YAML

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
async def home(hass):
    """A started Core with one entity exposed to Assist and one kept from it."""
    from homeassistant.setup import async_setup_component

    from tests.runtime_fixtures import EXPOSURE_HIDDEN, EXPOSURE_SHOWN, expose_to_assist

    assert await async_setup_component(hass, "homeassistant", {})
    for entity_id in (EXPOSURE_SHOWN, EXPOSURE_HIDDEN):
        hass.states.async_set(entity_id, "on", {"brightness": 10, "friendly_name": entity_id})
    expose_to_assist(hass, EXPOSURE_SHOWN, EXPOSURE_HIDDEN)
    return hass


@pytest.fixture
def related(home):
    """Two registered lights in one area and an exposed automation that targets both."""
    import os

    import yaml
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    from tests.runtime_fixtures import expose_to_assist

    registry = er.async_get(home)
    area = ar.async_get(home).async_create("Kitchen")
    lights = []
    for object_id in ("counter", "pantry"):
        entry = registry.async_get_or_create("light", "test", object_id, suggested_object_id=object_id)
        registry.async_update_entity(entry.entity_id, area_id=area.id)
        home.states.async_set(entry.entity_id, "on")
        lights.append(entry.entity_id)
    shown, hidden = lights
    automation = registry.async_get_or_create("automation", "test", "auto1", suggested_object_id="evening")
    home.states.async_set(automation.entity_id, "on")
    expose_to_assist(home, shown, hidden)
    async_expose_entity(home, "conversation", automation.entity_id, True)
    with open(os.path.join(home.config.config_dir, "automations.yaml"), "w", encoding="utf-8") as handle:
        yaml.safe_dump([{
            "id": "auto1", "alias": "Evening",
            "trigger": [{"platform": "state", "entity_id": shown}],
            "action": [{"service": "light.turn_on", "target": {"entity_id": [shown, hidden]}}],
        }], handle)
    return area, automation.entity_id, shown, hidden


@pytest.fixture
def decision_wire(aioclient_mock):
    """Answer the decision provider below `_request`, so its guard and parsing are real."""
    from tests.runtime_fixtures import DecisionWire

    return DecisionWire(aioclient_mock)


@pytest.fixture
def mock_store_data():
    return {
        "version": 1,
        "access_keys": [],
        "archived_access_keys": [],
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
    from custom_components.phoenix_mcp.llm.provider_claude import ClaudeProvider
    from custom_components.phoenix_mcp.llm.provider_openai import OpenAICompatProvider
    from custom_components.phoenix_mcp.llm.accounts import ProviderConfig

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
    from pytest_homeassistant.common import MockConfigEntry

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
