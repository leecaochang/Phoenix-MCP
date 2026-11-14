"""Approved blueprint edits and deletes act only on the source their review showed.

The Confirm gate binds a server-owned hash of the blueprint source the approval diff was built
from. A person's change to the file while the approval waits refuses the approved write, so a
stale diff never authorises a change to every automation built from the blueprint. The cases
queue approvals through the real gate and run them through the approved executor, as the admin
approve route does; only HA's blueprint WS commands and the consumer scan are replaced.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionTree
from custom_components.phoenix_mcp.const import APPROVAL_REVIEWED_HASH_KEY
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_common import reviewed_hash
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.tools import blueprint as blueprint_tools
from custom_components.phoenix_mcp.version_store import VersionStore

SOURCE = """\
blueprint:
  name: Reviewed Blueprint
  domain: automation
  input:
    trigger_entity:
      name: Trigger entity
triggers:
  - trigger: state
    entity_id: !input trigger_entity
actions:
  - action: light.turn_on
    target:
      entity_id: light.kitchen
mode: single
"""
PERSON = SOURCE.replace("Reviewed Blueprint", "Person edit")
AGENT = SOURCE.replace("Reviewed Blueprint", "Agent edit")
REL = "author/existing.yaml"


class ApprovalStore:
    """The slice of the integration store the Confirm gate keeps pending approvals in."""

    def __init__(self) -> None:
        self.pending: list = []
        self.async_lock = asyncio.Lock()
        self.async_save = AsyncMock()

    def get_pending_approvals(self) -> list:
        return self.pending

    def set_pending_approvals(self, approvals: list) -> None:
        self.pending = approvals

    def get_settings(self):
        return SimpleNamespace(mesa_mode="off", notify_on_approval=False)


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(), cap_blueprint_write="confirm", cap_automation_write="allow",
        cap_config_read="allow",
    )


def _args(approval) -> dict:
    return approval["args"] if isinstance(approval, dict) else approval.args


def _text(content: dict) -> str:
    return content["content"][0]["text"]


@pytest.fixture
def blueprint_file(hass: HomeAssistant, tmp_path) -> str:
    hass.config.config_dir = str(tmp_path)
    target = os.path.join(str(tmp_path), "blueprints", "automation", REL)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as stream:
        stream.write(SOURCE)
    return target


def _ws(target: str) -> AsyncMock:
    """HA's blueprint/save and blueprint/delete, writing or removing the file as Core would."""
    async def dispatch(hass, command, payload, **kwargs):
        if command == "blueprint/save":
            with open(target, "w", encoding="utf-8") as stream:
                stream.write(blueprint_tools._canonical_blueprint_source(payload["domain"], payload["yaml"]))
        elif command == "blueprint/delete":
            os.remove(target)
        return {"overrides_existing": True}

    return AsyncMock(side_effect=dispatch)


def _read(target: str) -> str | None:
    if not os.path.exists(target):
        return None
    with open(target, encoding="utf-8") as stream:
        return stream.read()


async def _queue(hass, tool: str) -> tuple[dict, AccessKeyRecord, PhoenixData]:
    store = ApprovalStore()
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore(), mesa=None)
    key = _key()
    args = {"domain": "automation", "path": REL}
    if tool == "edit_blueprint":
        args["content"] = AGENT
    with patch.object(blueprint_tools, "_blueprint_consumers", return_value=[]):
        _, outcome, _ = await _call_tool(tool, args, key, hass, data)
    assert outcome == "pending_approval"
    (approval,) = store.pending
    return _args(approval), key, data


@pytest.mark.parametrize("tool", ["edit_blueprint", "delete_blueprint"])
async def test_the_approval_is_bound_to_the_reviewed_source(hass, blueprint_file, tool):
    stored, _, _ = await _queue(hass, tool)
    assert stored[APPROVAL_REVIEWED_HASH_KEY] == reviewed_hash(SOURCE)


@pytest.mark.parametrize("tool", ["edit_blueprint", "delete_blueprint"])
async def test_a_person_s_change_while_pending_refuses_the_approved_write(hass, blueprint_file, tool):
    stored, key, data = await _queue(hass, tool)
    with open(blueprint_file, "w", encoding="utf-8") as stream:
        stream.write(PERSON)
    ws = _ws(blueprint_file)
    with patch.object(blueprint_tools, "async_ws_command", new=ws), \
            patch.object(blueprint_tools, "_blueprint_consumers", return_value=[]):
        content, outcome, _ = await async_execute_approved_tool(tool, stored, key, hass, data)
    assert outcome == "invalid_request"
    assert yi.MSG_CHANGED_SINCE_REVIEW in _text(content)
    assert _read(blueprint_file) == PERSON
    ws.assert_not_awaited()


@pytest.mark.parametrize("tool", ["edit_blueprint", "delete_blueprint"])
async def test_an_unchanged_blueprint_is_written_as_approved(hass, blueprint_file, tool):
    stored, key, data = await _queue(hass, tool)
    with patch.object(blueprint_tools, "async_ws_command", new=_ws(blueprint_file)), \
            patch.object(blueprint_tools, "_blueprint_consumers", return_value=[]):
        _, outcome, _ = await async_execute_approved_tool(tool, stored, key, hass, data)
    assert outcome == "allowed"
    expected = blueprint_tools._canonical_blueprint_source("automation", AGENT) if tool == "edit_blueprint" else None
    assert _read(blueprint_file) == expected


@pytest.mark.parametrize("tool", ["edit_blueprint", "delete_blueprint"])
async def test_an_approval_without_the_binding_asks_again(hass, blueprint_file, tool):
    stored, key, data = await _queue(hass, tool)
    stored = {k: v for k, v in stored.items() if k != APPROVAL_REVIEWED_HASH_KEY}
    ws = _ws(blueprint_file)
    with patch.object(blueprint_tools, "async_ws_command", new=ws), \
            patch.object(blueprint_tools, "_blueprint_consumers", return_value=[]):
        content, outcome, _ = await async_execute_approved_tool(tool, stored, key, hass, data)
    assert outcome == "invalid_request"
    assert yi.MSG_UNBOUND_REVIEW in _text(content)
    ws.assert_not_awaited()


async def test_a_caller_supplied_binding_is_replaced(hass, blueprint_file):
    store = ApprovalStore()
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore(), mesa=None)
    args = {"domain": "automation", "path": REL, "content": AGENT, APPROVAL_REVIEWED_HASH_KEY: reviewed_hash(PERSON)}
    with patch.object(blueprint_tools, "_blueprint_consumers", return_value=[]):
        _, outcome, _ = await blueprint_tools._tool_edit_blueprint(args, _key(), hass, data)
    assert outcome == "pending_approval"
    assert _args(store.pending[0])[APPROVAL_REVIEWED_HASH_KEY] == reviewed_hash(SOURCE)
