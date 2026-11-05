"""get_automation says plainly why an automation it can see has no configuration to read.

A YAML automation without an id: has no stored id, and one defined outside the
automation files Phoenix edits (a package) has an id but no entry; both used to
answer like a mistyped id. The reason is given only for an entity the access
key can read, so it never names an automation the key cannot see.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.tool_dispatch import _call_tool

ACTIONS = [{"delay": {"seconds": 1}}]


def _key(*, sees_automations: bool = True) -> AccessKeyRecord:
    domain = "automation" if sees_automations else "light"
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={domain: PermissionNode(state="GREEN")}), cap_automation_write="allow",
    )


@pytest.fixture
async def automations(hass, tmp_path):
    """One automation in automations.yaml, one with an id defined elsewhere, one without an id."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    in_file = {"id": "in_file", "alias": "In file", "triggers": [], "actions": ACTIONS}
    (tmp_path / "automations.yaml").write_text(yi.dump_tagged([in_file]))
    assert await async_setup_component(hass, "automation", {"automation": [
        in_file,
        {"id": "elsewhere", "alias": "Elsewhere", "triggers": [], "actions": ACTIONS},
        {"alias": "No id", "triggers": [], "actions": ACTIONS},
    ]})
    await hass.async_block_till_done()
    assert hass.states.get("automation.no_id") is not None
    return hass


async def _text(hass, tool, automation_id, key):
    content, outcome, _ = await _call_tool(tool, {"automation_id": automation_id}, key, hass, MagicMock())
    return content["content"][0]["text"], outcome


async def test_an_entity_id_with_a_stored_id_reads_its_configuration(automations):
    text, outcome = await _text(automations, "get_automation", "automation.in_file", _key())
    assert outcome == "allowed" and json.loads(text)["config"]["alias"] == "In file"


@pytest.mark.parametrize("tool", ["get_automation", "delete_automation"])
async def test_a_yaml_automation_without_an_id_is_named_as_such(automations, tool):
    text, _ = await _text(automations, tool, "automation.no_id", _key())
    assert text.startswith("automation.no_id is a YAML automation without an id:")


async def test_an_id_outside_the_edited_files_is_named_as_such(automations):
    text, outcome = await _text(automations, "get_automation", "automation.elsewhere", _key())
    assert outcome == "not_found"
    assert text.startswith("automation.elsewhere has id 'elsewhere', but its configuration is not in the automation files")


@pytest.mark.parametrize("requested", ["automation.no_id", "automation.elsewhere"])
async def test_no_reason_is_given_for_an_automation_the_key_cannot_see(automations, requested):
    text, _ = await _text(automations, "get_automation", requested, _key(sees_automations=False))
    assert text.startswith("No automation found with id") and "YAML" not in text and "package" not in text
