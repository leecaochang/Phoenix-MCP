"""Every tool Home Assistant's Device control API offers is either forwarded by Phoenix MCP or
explained, including tools an integration's llm.py adds as an llm.Tool class rather than an intent.

The intent triage covers only intents. An integration can also add a tool class to the Device
control API, and such a tool would otherwise arrive unnoticed. Phoenix keeps forwarding its fixed
list; these tests only make each new tool a recorded decision. Two directions are checked: every
installed llm.py that serves the API is named below, and every tool the API returns once those
platforms are set up with an exposed entity of each domain is forwarded or explained.
"""

from __future__ import annotations

from pathlib import Path
import re

import homeassistant
import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm as ha_llm
from homeassistant.helpers.llm import Tool
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.tool_defs import _NATIVE_TOOL_NAMES

_COMPONENTS = Path(homeassistant.__file__).parent / "components"

# Integration LLM platforms arrive with Core's llm component; an older Core has no Device control
# platforms to triage, and its intents are covered by the intent triage alone.
pytestmark = pytest.mark.skipif(
    not (_COMPONENTS / "llm" / "__init__.py").is_file()
    or "def async_get_tools" not in (_COMPONENTS / "llm" / "__init__.py").read_text(encoding="utf-8"),
    reason="this Home Assistant has no integration LLM tool platforms")

# Every installed platform that serves the Device control API, and how this test reaches it. A
# platform that cannot be set up here carries its reason instead.
PLATFORMS: dict[str, str] = {
    "homeassistant": "set up", "llm": "set up", "intent": "set up", "light": "set up",
    "fan": "set up", "climate": "set up", "media_player": "set up", "vacuum": "set up",
    "humidifier": "set up", "lawn_mower": "set up", "todo": "set up", "calendar": "set up",
    "script": "set up", "intent_script": "set up",
    "assist_satellite": "its audio dependencies are not installable here; HassBroadcast is checked against "
                        "Core's source by the intent triage",
}

# Device control tools Phoenix MCP does not forward, each with the reason.
NOT_FORWARDED: dict[str, str] = {
    "calendar__get_events": "get_calendar_events reads calendars within the key's scope",
    "todo__get_items": "get_todo_items reads to-do lists within the key's scope",
    "todo__HassListAddItem": "set_todo_item writes to-do lists under the key's gates",
    "todo__HassListCompleteItem": "set_todo_item writes to-do lists under the key's gates",
    "todo__HassListRemoveItem": "set_todo_item writes to-do lists under the key's gates",
    "media_player__search_media": "media_player__HassMediaSearchAndPlay keeps the scoped combined search and play",
    "media_player__play_media": "media_player__HassMediaSearchAndPlay keeps the scoped combined search and play",
    "humidifier__HassHumidifierSetpoint": "call_service on the humidifier domain, under the key's gates",
    "humidifier__HassHumidifierMode": "call_service on the humidifier domain, under the key's gates",
    "lawn_mower__HassLawnMowerStartMowing": "call_service on the lawn_mower domain, under the key's gates",
    "lawn_mower__HassLawnMowerDock": "call_service on the lawn_mower domain, under the key's gates",
}

# Device control tools of NOT_FORWARDED that a supported release before the listed one lacks.
ADDED_IN: dict[str, tuple[int, int]] = {
    "media_player__search_media": (2026, 10),
    "media_player__play_media": (2026, 10),
}

# Families whose names come from the home's own configuration, one tool per item.
NOT_FORWARDED_FAMILIES: dict[str, str] = {
    "script__": "one tool per exposed script; scripts run through call_service under the key's gates",
    "intent_script__": "one tool per configured intent_script; it runs through call_service under the key's gates",
}

# One exposed state per domain whose platform offers tools only while something is exposed.
_EXPOSED = {
    "light.desk": "on", "fan.ceiling": "on", "climate.hall": "heat", "media_player.tv": "playing",
    "vacuum.robot": "docked", "humidifier.room": "on", "lawn_mower.lawn": "docked", "todo.shopping": "0",
    "calendar.home": "off",
}


def _serves_device_control(path: Path) -> bool:
    """Whether an installed llm.py contributes tools to the Device control API."""
    text = path.read_text(encoding="utf-8")
    return "LLM_API_ASSIST" in text or path.parent.name == "llm"


def _installed_platforms() -> set[str]:
    return {path.parent.name for path in _COMPONENTS.glob("*/llm.py") if _serves_device_control(path)}


def test_every_installed_device_control_platform_is_named() -> None:
    installed = _installed_platforms()
    assert installed, "no llm.py platform found; the component layout may have moved"
    assert not installed - set(PLATFORMS), (
        f"Home Assistant has new Device control platforms; set them up here or say why not: {sorted(installed - set(PLATFORMS))}")
    assert not set(PLATFORMS) - installed, f"PLATFORMS names platforms Home Assistant no longer ships: {sorted(set(PLATFORMS) - installed)}"
    assert all(reason.strip() for reason in PLATFORMS.values())


async def _device_control_tools(hass: HomeAssistant) -> list[Tool]:
    for domain, how in PLATFORMS.items():
        if how == "set up":
            config = {} if domain in ("homeassistant", "llm", "intent", "script") else {domain: {}}
            assert await async_setup_component(hass, domain, config), f"could not set up {domain}"
    await hass.async_block_till_done()
    for entity_id, state in _EXPOSED.items():
        # A player that can search and play media also brings the search and play tools.
        features = (int(MediaPlayerEntityFeature.SEARCH_MEDIA | MediaPlayerEntityFeature.PLAY_MEDIA)
                    if entity_id.startswith("media_player.") else 0)
        hass.states.async_set(entity_id, state, {"friendly_name": entity_id.split(".")[1].title(),
                                                 "supported_features": features})
        async_expose_entity(hass, "conversation", entity_id, True)
    context = ha_llm.LLMContext(platform="phoenix_mcp", context=None, language="en",
                                assistant="conversation", device_id=None)
    api = await ha_llm.async_get_api(hass, ha_llm.LLM_API_ASSIST, context)
    return list(api.tools)


def _untriaged(names: set[str]) -> list[str]:
    forwarded = set(_NATIVE_TOOL_NAMES.values())
    return sorted(name for name in names if name not in forwarded and name not in NOT_FORWARDED
                  and not any(name.startswith(prefix) for prefix in NOT_FORWARDED_FAMILIES))


async def test_every_device_control_tool_is_forwarded_or_explained(hass: HomeAssistant) -> None:
    names = {tool.name for tool in await _device_control_tools(hass)}
    assert {"homeassistant__GetLiveContext", "llm__GetDateTime", "light__HassLightSet"} <= names, (
        "Device control returned too little; the platforms may not have loaded")
    untriaged = _untriaged(names)
    assert not untriaged, (
        "Home Assistant's Device control API offers tools Phoenix MCP neither forwards nor explains; "
        f"forward them or add a reason to NOT_FORWARDED: {untriaged}")
    installed = (MAJOR_VERSION, MINOR_VERSION)
    stale = sorted(name for name in NOT_FORWARDED
                   if name not in names and installed >= ADDED_IN.get(name, (0, 0)))
    assert not stale, f"NOT_FORWARDED names tools Device control no longer offers: {stale}"
    assert all(reason.strip() for reason in [*NOT_FORWARDED.values(), *NOT_FORWARDED_FAMILIES.values()])


class _StandInTool(Tool):
    """A tool class an integration adds to Device control, as a later Home Assistant might."""

    name = "light__run_command_later"
    integration = "light"
    description = "Run a command later."

    async def async_call(self, hass, tool_input, llm_context):  # pragma: no cover - never called
        return {}


async def test_a_tool_class_added_at_runtime_is_reported(hass: HomeAssistant, monkeypatch) -> None:
    from homeassistant.components.light import llm as light_llm

    real = light_llm.async_get_tools

    def with_stand_in(hass, llm_context, api_id):
        result = real(hass, llm_context, api_id)
        if result is not None:
            result.tools.append(_StandInTool())
        return result

    monkeypatch.setattr(light_llm, "async_get_tools", with_stand_in)
    names = {tool.name for tool in await _device_control_tools(hass)}
    assert _untriaged(names) == ["light__run_command_later"]


@pytest.mark.parametrize("name", ["schedule__run_command_later", "light__HassLightFlash"])
def test_a_new_tool_is_reported(name: str) -> None:
    """A stand-in for a tool a later Home Assistant adds, as an llm.Tool class or a new intent."""
    assert _untriaged({"light__HassLightSet", name}) == [name]


def test_a_configured_family_member_is_explained_by_its_prefix() -> None:
    assert _untriaged({"script__good_night", "intent_script__open_garage"}) == []
    assert re.fullmatch(r"\w+__", next(iter(NOT_FORWARDED_FAMILIES)))
