"""A saved automation, script or blueprint cannot gain dangerous reach unreviewed.

A saved config runs as Home Assistant, past everything call_service checks, so what it newly
calls is held for a person even on Allow. What it already called gives no new reach.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util.dt import utcnow
import pytest

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord
from custom_components.phoenix_mcp.capability import reviewed_actions
from custom_components.phoenix_mcp.const import REVIEWED_ACTION_DOMAINS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools import authoring, blueprint
from custom_components.phoenix_mcp.version_store import VersionStore


def _action(name: str, key: str = "action") -> dict:
    return {key: name}


@pytest.mark.parametrize("domain", sorted(REVIEWED_ACTION_DOMAINS))
def test_every_listed_domain_is_found(domain):
    assert reviewed_actions({"actions": [_action(f"{domain}.anything")]}) == {f"{domain}.anything"}


@pytest.mark.parametrize("name", ["homeassistant.restart", "homeassistant.stop", "update.install"])
def test_restart_stop_and_update_are_found(name):
    assert reviewed_actions({"sequence": [_action(name)]}) == {name}


@pytest.mark.parametrize("wrap", [
    lambda step: {"action": [step]},
    lambda step: {"sequence": [{"choose": [{"conditions": [], "sequence": [step]}], "default": []}]},
    lambda step: {"actions": [{"if": [], "then": [{"repeat": {"count": 2, "sequence": [step]}}]}]},
    lambda step: {"actions": [{"parallel": [{"sequence": [step]}]}]},
    lambda step: {"actions": [{"if": [], "then": [], "else": [step]}]},
])
def test_nested_calls_are_found(wrap):
    assert reviewed_actions(wrap(_action("shell_command.x"))) == {"shell_command.x"}


@pytest.mark.parametrize("key", ["action", "service", "service_template"])
def test_every_way_of_naming_a_call_is_found(key):
    assert reviewed_actions({"sequence": [_action("Shell_Command.x ", key)]}) == {"Shell_Command.x"}


@pytest.mark.parametrize("name", ["{{ states('input_text.x') }}", "{% if a %}light.on{% endif %}"])
def test_a_templated_name_is_found_because_nothing_shows_what_it_runs(name):
    assert reviewed_actions({"sequence": [_action(name)]}) == {name}


def test_a_blueprint_is_found_because_its_source_can_change():
    config = {"use_blueprint": {"path": "me/x.yaml", "input": {}}}
    assert reviewed_actions(config) == {"blueprint me/x.yaml"}


def test_ordinary_calls_and_arguments_are_not():
    config = {"actions": [
        _action("light.turn_on"), _action("script.morning"),
        {"action": "notify.mobile", "data": {"action": "shell_command.x", "message": "{{ now() }}"}},
        {"action": "light.turn_on", "target": {"action": "hassio.host_reboot"}},
    ], "variables": {"action": "hassio.host_reboot"}}
    assert reviewed_actions(config) == frozenset()


@pytest.mark.parametrize("config", [None, "text", 3, [], {}])
def test_nothing_callable_finds_nothing(config):
    assert reviewed_actions(config) == frozenset()


async def _added(current, config, entry_id="a"):
    with patch.object(authoring, "_read_config_entry", AsyncMock(return_value=current)):
        return await authoring._added_reviewed_actions(None, "automation", entry_id, config)


async def test_a_new_entry_counts_everything():
    assert await _added(None, {"actions": [_action("hassio.host_shutdown")]}, None) == ["hassio.host_shutdown"]


async def test_an_edit_keeping_a_call_adds_nothing():
    current = {"alias": "RF", "actions": [_action("pyscript.rf_transmit_code")]}
    assert await _added(current, {**current, "alias": "Renamed"}) == []


async def test_an_edit_adding_another_call_of_the_same_domain_counts_it():
    current = {"actions": [_action("pyscript.rf_transmit_code")]}
    config = {"actions": [_action("pyscript.rf_transmit_code"), _action("pyscript.other")]}
    assert await _added(current, config) == ["pyscript.other"]


async def test_an_unreadable_entry_counts_as_calling_nothing():
    with patch.object(authoring, "_read_config_entry", AsyncMock(side_effect=OSError)):
        actions = await authoring._added_reviewed_actions(None, "automation", "a", {"actions": [_action("hassio.x")]})
    assert actions == ["hassio.x"]


_BLUEPRINT = """
blueprint:
  name: x
  domain: automation
trigger: []
action:
  - action: {}
"""


def test_a_blueprint_edit_counts_only_what_it_adds():
    plain = _BLUEPRINT.format("light.turn_on")
    risky = _BLUEPRINT.format("shell_command.x")
    assert blueprint._added_blueprint_actions(risky, plain) == ["shell_command.x"]
    assert blueprint._added_blueprint_actions(risky, None) == ["shell_command.x"]
    assert blueprint._added_blueprint_actions(risky, risky) == []
    assert blueprint._added_blueprint_actions(plain, risky) == []


def test_a_blueprint_that_cannot_be_read_counts_as_calling_nothing():
    assert blueprint._added_blueprint_actions(_BLUEPRINT.format("hassio.x"), "[broken") == ["hassio.x"]


@pytest.fixture
async def allowed(hass, tmp_path):
    """A key on Allow for automations, over a real editor file, with approvals captured."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text("[]\n")
    hass.services.async_register("automation", "reload", MagicMock())
    key = AccessKeyRecord(id="k", name="k", access_key_hash="x", created_at=utcnow(), created_by="u",
                          cap_automation_write="allow")
    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())
    data.store.async_lock = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
    held: list[dict] = []

    async def hold(_store, **kwargs):
        held.append(kwargs)
        return MagicMock(id="appr", tool_name=kwargs["tool_name"], args=kwargs["args"], diff=kwargs["diff"])

    with patch("custom_components.phoenix_mcp.approvals.async_create_pending_approval", side_effect=hold), \
            patch("custom_components.phoenix_mcp.approvals.create_approval_notification"), \
            patch("custom_components.phoenix_mcp.approvals.fire_approval_requested_event"), \
            patch("custom_components.phoenix_mcp.tool_common._pending_or_inline",
                  AsyncMock(return_value=({"content": []}, "pending_approval", "approval:appr"))):
        yield hass, key, data, held, tmp_path / "automations.yaml"


def _automation(*actions: str) -> dict:
    return {"alias": "A", "triggers": [{"trigger": "time", "at": "01:00:00"}],
            "actions": [{"action": name} for name in actions]}


async def test_allow_still_holds_a_dangerous_automation_for_a_person(allowed):
    hass, key, data, held, path = allowed
    result = await _call_tool("create_automation", {"config": _automation("shell_command.wipe")}, key, hass, data)
    assert result[1] == "pending_approval", result
    assert held[0]["diff"]["reviewed_actions"] == ["shell_command.wipe"]
    assert "shell_command" not in path.read_text()


async def test_allow_saves_an_ordinary_automation_at_once(allowed):
    hass, key, data, held, path = allowed
    result = await _call_tool("create_automation", {"config": _automation("light.turn_on")}, key, hass, data)
    assert result[1] == "allowed", result
    assert held == [] and "light.turn_on" in path.read_text()


async def test_deny_stays_deny(allowed):
    hass, key, data, held, path = allowed
    key.cap_automation_write = "deny"
    result = await _call_tool("create_automation", {"config": _automation("shell_command.wipe")}, key, hass, data)
    assert result[1] == "denied" and held == []


async def test_a_small_edit_of_a_saved_call_is_not_held(allowed):
    hass, key, data, held, path = allowed
    path.write_text(yi.dump_tagged([dict(_automation("pyscript.rf"), id="rf")]))
    result = await _call_tool("edit_automation", {"automation_id": "rf",
                                                  "config": dict(_automation("pyscript.rf"), mode="restart")}, key, hass, data)
    assert result[1] == "allowed", result
    assert held == []


async def test_an_edit_adding_a_call_is_held(allowed):
    hass, key, data, held, path = allowed
    path.write_text(yi.dump_tagged([dict(_automation("light.turn_on"), id="rf")]))
    result = await _call_tool("edit_automation", {"automation_id": "rf",
                                                  "config": _automation("light.turn_on", "hassio.host_shutdown")}, key, hass, data)
    assert result[1] == "pending_approval", result
    assert held[0]["diff"]["reviewed_actions"] == ["hassio.host_shutdown"]
    assert "hassio" not in path.read_text()
