"""Every tool of Home Assistant's admin LLM API is either covered by a Phoenix MCP tool or absent on purpose.

Phoenix mirrors only the Assist API, whose tool names, parameters and results are a contract held
elsewhere. The admin-only "Home Assistant" API is not mirrored: its results are whole-home and
unscrubbed, its writes run without review, and it changes quickly. Its tools still need a decision
each, so a client moving from Home Assistant's own MCP server finds the same reach under Phoenix's
names or a recorded reason why not. A tool the installed Home Assistant adds fails here until it is
mapped or explained, and an entry for a tool it no longer has fails too.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import homeassistant.components
import pytest
from homeassistant.const import KEY_DATA_LOGGING
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm as ha_llm
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.tool_defs import (
    _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS, _SYSTEM_TOOL_DEFS, MESA_TOOL_NAMES,
)

if not hasattr(ha_llm, "LLM_API_HOME_ASSISTANT"):
    pytest.skip("this Home Assistant has no admin LLM API", allow_module_level=True)

# The Phoenix tools that reach what each admin tool reaches, within an access key's scope.
COVERED: dict[str, tuple[str, ...]] = {
    "config__get_config_entries": ("list_integrations",),
    "config__reload_config_entry": ("reload_integration",),
    "history__get_history": ("get_statistics",),
    "homeassistant__render_template": ("render_template",),
    "homeassistant__search_actions": ("find_available_actions",),
    "homeassistant__call_action": ("call_service",),
    "system_log__get_entries": ("get_logs",),
    "search__search": ("search_entities", "list_devices", "list_areas", "list_labels",
                       "list_automations", "list_scripts", "list_scenes"),
    "system_health__get_system_info": ("get_system_health",),
    "llm__GetDateTime": ("llm__GetDateTime",),
}

# Admin tools with no Phoenix counterpart, and why.
ABSENT: dict[str, str] = {
    "config__search_integrations": (
        "finds integrations to set up through a config flow; Phoenix does not add integrations, "
        "since an arbitrary setup form takes hosts and credentials"
    ),
    "system_log__get_logs": (
        "reads the raw log file at every level; Phoenix reads only the scrubbed WARNING-and-above "
        "system log (get_logs), as raw logs are unscoped whole-home text"
    ),
    "hassio__get_logs": (
        "reads raw Core, Supervisor and app logs at every level; Phoenix reads only the scrubbed "
        "WARNING-and-above system log (get_logs), as raw logs are unscoped whole-home text"
    ),
}


def _phoenix_tools() -> set[str]:
    """Every tool name Phoenix MCP publishes."""
    names = set(MESA_TOOL_NAMES)
    for definitions in (_SYSTEM_TOOL_DEFS, _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS):
        for definition in (definitions.values() if isinstance(definitions, dict) else definitions):
            names.add(definition["name"] if isinstance(definition, dict) else definition)
    return names


def _context(user_id: str | None = None) -> ha_llm.LLMContext:
    return ha_llm.LLMContext(platform="phoenix_mcp", context=Context(user_id=user_id), language="en",
                             assistant="conversation", device_id=None)


@pytest.fixture
def admin_tools(hass: HomeAssistant) -> set[str]:
    """Every tool any installed component's LLM platform offers the admin API.

    Each platform is asked directly, as Home Assistant itself asks only the set-up ones. A log file
    is named, as the system log offers its raw log reader only off Supervisor with one.
    """
    hass.data[KEY_DATA_LOGGING] = str(Path(hass.config.config_dir) / "home-assistant.log")
    root = Path(homeassistant.components.__file__).parent
    names: set[str] = set()
    for platform in sorted(root.glob("*/llm.py")):
        module = importlib.import_module(f"homeassistant.components.{platform.parent.name}.llm")
        get_tools = getattr(module, "async_get_tools", None)
        result = get_tools(hass, _context(), ha_llm.LLM_API_HOME_ASSISTANT) if get_tools else None
        if result is not None:
            names.update(tool.name for tool in result.tools)
    return names


async def test_every_admin_tool_is_covered_or_explained(admin_tools):
    untriaged = sorted(admin_tools - set(COVERED) - set(ABSENT))
    assert not untriaged, (
        "the admin LLM API offers tools Phoenix MCP neither covers nor explains; "
        f"map them in COVERED or give a reason in ABSENT: {untriaged}"
    )


async def test_the_map_has_no_stale_entries(admin_tools):
    assert not set(COVERED) & set(ABSENT), "a tool is both covered and absent"
    assert all(reason.strip() for reason in ABSENT.values()), "every ABSENT entry needs a reason"
    gone = sorted((set(COVERED) | set(ABSENT)) - admin_tools)
    assert not gone, f"the map names tools the admin LLM API no longer offers: {gone}"
    ours = _phoenix_tools()
    missing = sorted({tool for tools in COVERED.values() for tool in tools} - ours)
    assert not missing, f"COVERED names Phoenix tools that do not exist: {missing}"
    published = sorted(set(ABSENT) & ours)
    assert not published, f"ABSENT names tools Phoenix now publishes: {published}"


async def test_the_collection_matches_home_assistants_own_api(hass: HomeAssistant, hass_admin_user, admin_tools):
    # Home Assistant's own route, for the components set up here, offers a subset of what the
    # direct collection found, so the collection cannot miss a tool by asking the wrong way.
    for domain in ("homeassistant", "llm", "search", "system_log"):
        assert await async_setup_component(hass, domain, {})
    await hass.async_block_till_done()
    api = await ha_llm.async_get_api(hass, ha_llm.LLM_API_HOME_ASSISTANT, _context(hass_admin_user.id))
    served = {tool.name for tool in api.tools}
    assert {"homeassistant__call_action", "search__search", "system_log__get_entries"} <= served
    assert served <= admin_tools
