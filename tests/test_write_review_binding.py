"""Approved edits and dashboard writes act only on the content their review showed.

An edit of an automation, script or scene, a card operation addressed by index, a whole-layout
replacement and a path patch each bind a server-owned hash of what the approval diff was built
from. A person's change made while the approval waits, or another approved write that shifts card
indexes, refuses the approved write instead of overwriting or acting on an unreviewed card. The
cases build pending approvals through the real Confirm gate and run them through the approved
executor, as the admin approve route does.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import APPROVAL_REVIEWED_HASH_KEY
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_common import reviewed_hash
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.version_store import VersionStore
from custom_components.phoenix_mcp.ws_dispatch import async_get_lovelace_config, async_save_lovelace_config

pytestmark = pytest.mark.usefixtures("immediate_authoring_observation")


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


def _data() -> tuple[PhoenixData, ApprovalStore]:
    store = ApprovalStore()
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore(), mesa=None), store


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={
            "light": PermissionNode(state="GREEN"), "scene": PermissionNode(state="GREEN"),
        }),
        **{"cap_registry_read": "allow", **caps},
    )


def _text(content: dict) -> str:
    return content["content"][0]["text"]


def _args(approval) -> dict:
    return approval["args"] if isinstance(approval, dict) else approval.args


def _tool(approval) -> str:
    return approval["tool_name"] if isinstance(approval, dict) else approval.tool_name


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path):
    hass.config.config_dir = str(tmp_path)
    for domain in ("automation", "script", "scene"):
        hass.services.async_register(domain, "reload", lambda call: None)
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    light = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "uid_k", config_entry=entry, suggested_object_id="kitchen")
    hass.states.async_set(light.entity_id, "on", {})
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\nscript: !include scripts.yaml\nscene: !include scenes.yaml\n")
    (tmp_path / "automations.yaml").write_text(textwrap.dedent("""\
        - id: a1
          alias: Porch
          triggers:
          - trigger: homeassistant
            event: start
          actions:
          - delay: 1
        """))
    (tmp_path / "scripts.yaml").write_text("porch:\n  alias: Porch\n  sequence:\n  - delay: 1\n")
    (tmp_path / "scenes.yaml").write_text("- id: '1'\n  name: Porch\n  entities:\n    light.kitchen: 'on'\n")
    return tmp_path


EDITS = {
    "automation": ("edit_automation", {"automation_id": "a1", "config": {
        "alias": "Porch", "triggers": [{"trigger": "homeassistant", "event": "start"}], "actions": [{"delay": 5}]}}),
    "script": ("edit_script", {"script_id": "porch", "config": {"alias": "Porch", "sequence": [{"delay": 5}]}}),
    "scene": ("edit_scene", {"scene_id": "1", "config": {"name": "Porch", "entities": {"light.kitchen": "off"}}}),
}
# What a person writes in Home Assistant's editor while the approval waits.
PERSON_EDIT = {
    "automation": ("automations.yaml", textwrap.dedent("""\
        - id: a1
          alias: Porch
          triggers:
          - trigger: homeassistant
            event: start
          conditions:
          - condition: state
            entity_id: light.kitchen
            state: 'on'
          actions:
          - delay: 1
        """)),
    "script": ("scripts.yaml", "porch:\n  alias: Porch\n  sequence:\n  - delay: 9\n"),
    "scene": ("scenes.yaml", "- id: '1'\n  name: Porch\n  entities:\n    light.kitchen: 'on'\n  icon: mdi:door\n"),
}
CONFIRM = {"cap_automation_write": "confirm", "cap_script_write": "confirm", "cap_scene_write": "confirm"}


class TestAuthoringEditBinding:
    @pytest.mark.parametrize("domain", ["automation", "script", "scene"])
    async def test_edit_changed_while_pending_is_refused(self, hass, config_dir, domain):
        """A person's change made while the edit waits survives its approval, which is refused."""
        tool, args = EDITS[domain]
        data, store = _data()
        key = _key(**CONFIRM)
        _, outcome, _ = await _call_tool(tool, json.loads(json.dumps(args)), key, hass, data)
        assert outcome == "pending_approval"
        approved_args = _args(store.pending[0])
        assert isinstance(approved_args.get(APPROVAL_REVIEWED_HASH_KEY), str)
        name, text = PERSON_EDIT[domain]
        (config_dir / name).write_text(text)

        content, outcome, _ = await async_execute_approved_tool(tool, approved_args, key, hass, data)
        assert outcome == "invalid_request"
        assert yi.MSG_CHANGED_SINCE_REVIEW in _text(content)
        assert (config_dir / name).read_text() == text
        assert data.versions.list_for(domain, args[f"{domain}_id"]) == []

    @pytest.mark.parametrize("domain", ["automation", "script", "scene"])
    async def test_unchanged_edit_applies(self, hass, config_dir, domain):
        """Positive control: an approval over an unchanged entry writes."""
        tool, args = EDITS[domain]
        data, store = _data()
        key = _key(**CONFIRM)
        await _call_tool(tool, json.loads(json.dumps(args)), key, hass, data)
        content, outcome, _ = await async_execute_approved_tool(tool, _args(store.pending[0]), key, hass, data)
        assert outcome == "allowed", _text(content)

    @pytest.mark.parametrize("domain", ["automation", "script", "scene"])
    async def test_approved_edit_without_binding_is_refused(self, hass, config_dir, domain):
        """An approval carrying no binding never writes, so a binding cannot be dropped to skip the check."""
        tool, args = EDITS[domain]
        data, _ = _data()
        before = {name: (config_dir / name).read_text() for name in ("automations.yaml", "scripts.yaml", "scenes.yaml")}
        content, outcome, _ = await async_execute_approved_tool(
            tool, json.loads(json.dumps(args)), _key(**CONFIRM), hass, data)
        assert outcome == "invalid_request"
        assert yi.MSG_UNBOUND_REVIEW in _text(content)
        assert {name: (config_dir / name).read_text() for name in before} == before

    async def test_caller_cannot_supply_the_binding(self, hass, config_dir):
        """A caller's own binding is refused at the transport or replaced by the server's."""
        tool, args = EDITS["script"]
        data, store = _data()
        forged = dict(json.loads(json.dumps(args)), **{APPROVAL_REVIEWED_HASH_KEY: "0" * 64})
        content, outcome, _ = await _call_tool(tool, forged, _key(**CONFIRM), hass, data)
        if outcome == "pending_approval":
            current = yi.read_entry(str(config_dir), "script", "porch")
            assert _args(store.pending[0])[APPROVAL_REVIEWED_HASH_KEY] == reviewed_hash(current)
        else:
            assert outcome == "invalid_request" and store.pending == []


async def _seed_dashboard(hass, cards):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    await async_save_lovelace_config(hass, None, {"views": [{"title": "V", "cards": cards}]})


WEATHER = {"type": "markdown", "content": "weather"}
ALARM = {"type": "markdown", "content": "ALARM PANEL (keep)"}
SAFETY = {"type": "markdown", "content": "SAFETY"}


class TestDashboardBinding:
    async def test_batch_add_then_delete_refuses_the_shifted_delete(self, hass):
        """Approving an add at 0 shifts indexes, so the queued delete of index 1 is refused."""
        await _seed_dashboard(hass, [dict(ALARM), dict(WEATHER)])
        data, store = _data()
        key = _key(cap_lovelace_write="confirm")
        await _call_tool("add_dashboard_card", {"view_index": 0, "position": 0, "card": {"type": "markdown", "content": "new"}},
                         key, hass, data)
        await _call_tool("delete_dashboard_card", {"view_index": 0, "card_index": 1}, key, hass, data)
        assert [_tool(a) for a in store.pending] == ["add_dashboard_card", "delete_dashboard_card"]
        first, second = store.pending
        _, outcome, _ = await async_execute_approved_tool(_tool(first), _args(first), key, hass, data)
        assert outcome == "allowed"
        content, outcome, _ = await async_execute_approved_tool(_tool(second), _args(second), key, hass, data)
        assert outcome == "invalid_request" and yi.MSG_CHANGED_SINCE_REVIEW in _text(content)
        cards = (await async_get_lovelace_config(hass, None))["views"][0]["cards"]
        assert ALARM in cards and WEATHER in cards

    async def test_reordered_delete_is_refused_and_unchanged_delete_applies(self, hass):
        await _seed_dashboard(hass, [dict(SAFETY), dict(WEATHER)])
        data, store = _data()
        key = _key(cap_lovelace_write="confirm")
        await _call_tool("delete_dashboard_card", {"view_index": 0, "card_index": 1}, key, hass, data)
        approval = store.pending[0]
        await async_save_lovelace_config(hass, None, {"views": [{"title": "V", "cards": [dict(WEATHER), dict(SAFETY)]}]})
        _, outcome, _ = await async_execute_approved_tool(_tool(approval), _args(approval), key, hass, data)
        assert outcome == "invalid_request"
        assert (await async_get_lovelace_config(hass, None))["views"][0]["cards"] == [WEATHER, SAFETY]
        # Positive control: put the reviewed order back and the same approval deletes the reviewed card.
        await async_save_lovelace_config(hass, None, {"views": [{"title": "V", "cards": [dict(SAFETY), dict(WEATHER)]}]})
        _, outcome, _ = await async_execute_approved_tool(_tool(approval), _args(approval), key, hass, data)
        assert outcome == "allowed"
        assert (await async_get_lovelace_config(hass, None))["views"][0]["cards"] == [SAFETY]

    async def test_edit_after_insert_is_refused(self, hass):
        await _seed_dashboard(hass, [dict(WEATHER)])
        data, store = _data()
        key = _key(cap_lovelace_write="confirm")
        await _call_tool("edit_dashboard_card", {"view_index": 0, "card_index": 0, "card": {"type": "markdown", "content": "w2"}},
                         key, hass, data)
        await async_save_lovelace_config(hass, None, {"views": [{"title": "V", "cards": [dict(SAFETY), dict(WEATHER)]}]})
        approval = store.pending[0]
        _, outcome, _ = await async_execute_approved_tool(_tool(approval), _args(approval), key, hass, data)
        assert outcome == "invalid_request"
        assert (await async_get_lovelace_config(hass, None))["views"][0]["cards"] == [SAFETY, WEATHER]

    @pytest.mark.parametrize("tool,args", [
        ("set_dashboard_config", {"config": {"views": [{"title": "Mine", "cards": []}]}}),
        ("patch_dashboard", {"changes": [{"path": ["views", 0, "title"], "kind": "set", "value": "Renamed"}]}),
    ])
    async def test_layout_write_changed_while_pending_is_refused(self, hass, tool, args):
        await _seed_dashboard(hass, [dict(WEATHER)])
        data, store = _data()
        key = _key(cap_lovelace_write="confirm")
        _, outcome, _ = await _call_tool(tool, args, key, hass, data)
        assert outcome == "pending_approval"
        person = {"views": [{"title": "V", "cards": [dict(WEATHER), dict(SAFETY)]}]}
        await async_save_lovelace_config(hass, None, person)
        approval = store.pending[0]
        content, outcome, _ = await async_execute_approved_tool(_tool(approval), _args(approval), key, hass, data)
        assert outcome == "invalid_request" and yi.MSG_CHANGED_SINCE_REVIEW in _text(content)
        assert await async_get_lovelace_config(hass, None) == person


TEMPLATE_CARD = {"type": "markdown", "content": "![x](https://example.invalid/{{ states('lock.front') }}.png)"}


class TestDashboardActiveContent:
    @pytest.mark.parametrize("card", [
        {"type": "markdown", "content": "{{ states('alarm_control_panel.home') }}"},
        {"type": "markdown", "content": "{% if true %}x{% endif %}"},
        {"type": "custom:button-card", "name": "[[[ return hass.user.name ]]]"},
        {"type": "custom:button-card", "name": "plain"},
        {"type": "picture", "image": "https://example.invalid/a.png"},
        {"type": "markdown", "content": "<img src=//example.invalid/a.png>"},
        {"type": "iframe", "url": "http://example.invalid/"},
    ])
    async def test_active_card_waits_for_approval_under_allow(self, hass, card):
        await _seed_dashboard(hass, [dict(WEATHER)])
        data, store = _data()
        key = _key(cap_lovelace_write="allow")
        _, outcome, _ = await _call_tool("add_dashboard_card", {"view_index": 0, "card": card}, key, hass, data)
        assert outcome == "pending_approval"
        assert (await async_get_lovelace_config(hass, None))["views"][0]["cards"] == [WEATHER]
        assert store.pending and _tool(store.pending[0]) == "add_dashboard_card"

    @pytest.mark.parametrize("card", [
        {"type": "markdown", "content": "Plain text"},
        {"type": "picture", "image": "/local/a.png"},
        {"type": "tile", "entity": "light.kitchen"},
    ])
    async def test_plain_card_stays_allow(self, hass, card):
        await _seed_dashboard(hass, [dict(WEATHER)])
        data, store = _data()
        _, outcome, _ = await _call_tool("add_dashboard_card", {"view_index": 0, "card": card},
                                         _key(cap_lovelace_write="allow"), hass, data)
        assert outcome == "allowed" and store.pending == []

    async def test_layout_keeping_existing_template_stays_allow(self, hass):
        """A whole-layout write that keeps an existing template card adds no new reach."""
        await _seed_dashboard(hass, [dict(TEMPLATE_CARD)])
        data, store = _data()
        key = _key(cap_lovelace_write="allow")
        config = {"views": [{"title": "Renamed", "cards": [dict(TEMPLATE_CARD)]}]}
        _, outcome, _ = await _call_tool("set_dashboard_config", {"config": config}, key, hass, data)
        assert outcome == "allowed" and store.pending == []
        changed = {"views": [{"title": "Renamed", "cards": [dict(TEMPLATE_CARD, content="{{ 1 }}")]}]}
        _, outcome, _ = await _call_tool("set_dashboard_config", {"config": changed}, key, hass, data)
        assert outcome == "pending_approval"

    async def test_patch_adding_a_url_waits_and_review_names_it(self, hass):
        await _seed_dashboard(hass, [{"type": "picture", "image": "/local/a.png"}])
        data, store = _data()
        key = _key(cap_lovelace_write="allow")
        _, outcome, _ = await _call_tool("patch_dashboard", {"changes": [
            {"path": ["views", 0, "cards", 0, "image"], "kind": "set", "value": "https://example.invalid/x.png"}]}, key, hass, data)
        assert outcome == "pending_approval"
        diff = store.pending[0]["diff"] if isinstance(store.pending[0], dict) else store.pending[0].diff
        assert diff["reviewed_actions"] == ["https://example.invalid"]
