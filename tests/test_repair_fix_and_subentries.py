"""fix_repair and set_integration_subentry run Core's own flows, only after an administrator approves.

Both are bound at approval time to what was reviewed: the issue or subentry, and the integration's
registry context. Each needs complete write scope over the integration, and an Allow capability
still queues an approval, since a fix runs integration code and a subentry can give an AI agent
Home Assistant's tools.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import (
    MockConfigEntry, MockModule, mock_config_flow, mock_integration, mock_platform,
)

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord, AccessKeyStore, GlobalSettings, PermissionNode, PermissionTree,
)
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.tools.discovery import _repair_ref
from custom_components.phoenix_mcp.tools.integration_subentry import _execute_set_integration_subentry
from custom_components.phoenix_mcp.tools.repair_fix import _execute_fix_repair

FIX, SUB = "fixable_thing", "agent_host"


def _text(content: dict) -> str:
    return content["content"][0]["text"]


async def _data(hass) -> tuple[PhoenixData, list]:
    pending: list = []
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    store.list_access_keys.return_value = []
    store.get_entity_hints.return_value = {}
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), hass=hass,
                       mesa=await async_setup_mesa(hass, "off"))
    hass.data[DOMAIN] = data
    return data, pending


def _owned(hass, domain: str) -> tuple[MockConfigEntry, str]:
    """A config entry owning one device with one entity, the scope a key must write."""
    entry = MockConfigEntry(domain=domain, title=f"{domain} home", entry_id=f"{domain}-entry")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(domain, "box")})
    _DEVICES[:] = [device]
    entity = er.async_get(hass).async_get_or_create(
        "sensor", domain, "probe", config_entry=entry, device_id=device.id, suggested_object_id=f"{domain}_probe")
    hass.states.async_set(entity.entity_id, "1")
    return entry, entity.entity_id


_DEVICES: list = []


def _key(entity_id: str, *, cap: str = "allow", state: str = "GREEN") -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="fixer", access_key_hash="k", created_at=utcnow(), created_by="t",
        cap_integration_reconfigure=cap,
        permissions=PermissionTree(entities={entity_id: PermissionNode(state=state)},
                                   domains={"sensor": PermissionNode(state=state)},
                                   devices={device.id: PermissionNode(state=state)
                                            for device in _DEVICES}),
    )


async def _approve(hass, data, pending, key) -> tuple[dict, str]:
    assert len(pending) == 1, pending
    row = pending[0]
    result, outcome, _ = await async_execute_approved_tool(row["tool_name"], row["args"], key, hass, data)
    return result, outcome


# Repairs: a real RepairsFlow asking one question, created by the integration's repairs platform.
class _AnswerFlow(RepairsFlow):
    answers: list = []

    async def async_step_init(self, user_input=None):
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            _AnswerFlow.answers.append(user_input)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({vol.Required("keep"): bool}))


@pytest.fixture
async def repair_home(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    mock_integration(hass, MockModule(FIX))
    platform = ModuleType(f"{FIX}.repairs")

    async def async_create_fix_flow(hass_, issue_id, data):
        return _AnswerFlow()

    platform.async_create_fix_flow = async_create_fix_flow  # type: ignore[attr-defined]
    mock_platform(hass, f"{FIX}.repairs", platform)
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, FIX, {})
    entry, entity_id = _owned(hass, FIX)
    ir.async_create_issue(hass, FIX, "worn", is_fixable=True, severity=ir.IssueSeverity.WARNING,
                          translation_key="worn", data={"part": "belt"})
    _AnswerFlow.answers = []
    return entry, entity_id


async def test_an_allowed_fix_still_waits_for_approval_then_runs_once(hass, repair_home):
    _entry, entity_id = repair_home
    data, pending = await _data(hass)
    key = _key(entity_id)
    ref = _repair_ref(key, FIX, "worn")
    content, outcome, _ = await _call_tool("fix_repair", {"repair_ref": ref, "config": {"keep": True}}, key, hass, data)
    assert outcome == "pending_approval", content
    assert _AnswerFlow.answers == []
    assert pending[0]["diff"]["summary"].startswith("Fix repair")
    result, outcome = await _approve(hass, data, pending, key)
    assert outcome == "allowed", result
    assert json.loads(_text(result))["status"] == "fixed"
    assert _AnswerFlow.answers == [{"keep": True}]
    assert ir.async_get(hass).async_get_issue(FIX, "worn") is None


async def test_a_fix_without_approval_never_runs(hass, repair_home):
    """The executor refuses its exact saved arguments unless an approval is running it."""
    _entry, entity_id = repair_home
    data, pending = await _data(hass)
    key = _key(entity_id)
    await _call_tool("fix_repair", {"repair_ref": _repair_ref(key, FIX, "worn"), "config": {"keep": True}}, key, hass, data)
    content, outcome, _ = await _execute_fix_repair(pending[0]["args"], key, hass, data)
    assert outcome == "denied", content
    assert _AnswerFlow.answers == []


@pytest.mark.parametrize(("cap", "state", "expected"), [("deny", "GREEN", "denied"), ("allow", "YELLOW", "not_found")])
async def test_capability_and_complete_write_scope_are_required(hass, repair_home, cap, state, expected):
    _entry, entity_id = repair_home
    data, pending = await _data(hass)
    key = _key(entity_id, cap=cap, state=state)
    content, outcome, _ = await _call_tool("fix_repair", {"repair_ref": _repair_ref(key, FIX, "worn")}, key, hass, data)
    assert outcome == expected, content
    assert pending == []


async def test_an_issue_from_an_integration_without_a_config_entry_is_refused(hass, repair_home):
    _entry, entity_id = repair_home
    ir.async_create_issue(hass, "yaml_only", "old", is_fixable=True, severity=ir.IssueSeverity.WARNING, translation_key="old")
    data, pending = await _data(hass)
    key = _key(entity_id)
    content, outcome, _ = await _call_tool("fix_repair", {"repair_ref": _repair_ref(key, "yaml_only", "old")}, key, hass, data)
    assert outcome == "denied", content
    assert "Home Assistant itself" in _text(content) and pending == []


async def test_an_issue_that_changed_after_approval_is_not_fixed(hass, repair_home):
    _entry, entity_id = repair_home
    data, pending = await _data(hass)
    key = _key(entity_id)
    await _call_tool("fix_repair", {"repair_ref": _repair_ref(key, FIX, "worn"), "config": {"keep": True}}, key, hass, data)
    ir.async_create_issue(hass, FIX, "worn", is_fixable=True, severity=ir.IssueSeverity.WARNING,
                          translation_key="worn", data={"part": "chain"})
    result, outcome = await _approve(hass, data, pending, key)
    assert outcome == "denied", result
    assert _AnswerFlow.answers == []


@pytest.mark.parametrize("ref", ["repair_0000", "not-a-ref"])
async def test_an_unknown_reference_is_refused(hass, repair_home, ref):
    _entry, entity_id = repair_home
    data, _ = await _data(hass)
    _content, outcome, _ = await _call_tool("fix_repair", {"repair_ref": ref}, _key(entity_id), hass, data)
    assert outcome in ("not_found", "invalid_request")


# Subentries: a real ConfigSubentryFlow offering an agent with a prompt and tool access.
class _AgentFlow(config_entries.ConfigSubentryFlow):
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Agent", data=user_input)
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("prompt"): str, vol.Optional("llm_hass_api"): [str]}))

    async def async_step_reconfigure(self, user_input=None):
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(self._get_entry(), subentry, data={**subentry.data, **user_input})
        return self.async_show_form(step_id="reconfigure", data_schema=vol.Schema({vol.Required("prompt"): str}))


class _HostFlow(config_entries.ConfigFlow):
    VERSION = 1

    @classmethod
    def async_get_supported_subentry_types(cls, config_entry):
        return {"agent": _AgentFlow}


@pytest.fixture
async def agent_home(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    mock_integration(hass, MockModule(SUB))
    mock_platform(hass, f"{SUB}.config_flow", None)
    entry, entity_id = _owned(hass, SUB)
    with mock_config_flow(SUB, _HostFlow):
        yield entry, entity_id


async def test_adding_an_agent_waits_for_approval_and_shows_its_tools(hass, agent_home):
    entry, entity_id = agent_home
    data, pending = await _data(hass)
    key = _key(entity_id)
    args = {"entry_id": entry.entry_id, "subentry_type": "agent",
            "config": {"prompt": "Be brief", "llm_hass_api": ["assist"]}}
    content, outcome, _ = await _call_tool("set_integration_subentry", args, key, hass, data)
    assert outcome == "pending_approval", content
    assert not entry.subentries
    assert pending[0]["diff"]["preview"]["agent_tools"] == ["assist"]
    result, outcome = await _approve(hass, data, pending, key)
    assert outcome == "allowed", result
    [created] = entry.subentries.values()
    assert dict(created.data) == {"prompt": "Be brief", "llm_hass_api": ["assist"]}
    assert json.loads(_text(result))["subentry_id"] == created.subentry_id


async def test_reconfiguring_an_agent_is_bound_to_its_reviewed_settings(hass, agent_home):
    entry, entity_id = agent_home
    subentry = config_entries.ConfigSubentry(data={"prompt": "Old", "api_key": "s3cret"}, subentry_type="agent",
                                             title="Agent", unique_id=None)
    hass.config_entries.async_add_subentry(entry, subentry)
    data, pending = await _data(hass)
    key = _key(entity_id)
    args = {"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id, "config": {"prompt": "New"}}
    await _call_tool("set_integration_subentry", args, key, hass, data)
    assert "s3cret" not in json.dumps(pending[0]["diff"])
    hass.config_entries.async_update_subentry(entry, subentry, data={"prompt": "Changed meanwhile"})
    result, outcome = await _approve(hass, data, pending, key)
    assert outcome == "denied", result
    assert entry.subentries[subentry.subentry_id].data["prompt"] == "Changed meanwhile"
    pending.clear()
    await _call_tool("set_integration_subentry", args, key, hass, data)
    result, outcome = await _approve(hass, data, pending, key)
    assert outcome == "allowed", result
    assert entry.subentries[subentry.subentry_id].data["prompt"] == "New"


async def test_an_offered_type_is_required_and_scope_is_complete(hass, agent_home):
    entry, entity_id = agent_home
    data, pending = await _data(hass)
    content, outcome, _ = await _call_tool("set_integration_subentry", {
        "entry_id": entry.entry_id, "subentry_type": "camera", "config": {}}, _key(entity_id), hass, data)
    assert outcome == "invalid_request" and "Offered: agent" in _text(content)
    content, outcome, _ = await _call_tool("set_integration_subentry", {
        "entry_id": entry.entry_id, "subentry_type": "agent", "config": {}}, _key(entity_id, state="YELLOW"), hass, data)
    assert outcome == "not_found" and pending == []


async def test_list_integrations_names_subentries(hass, agent_home):
    entry, entity_id = agent_home
    hass.config_entries.async_add_subentry(entry, config_entries.ConfigSubentry(
        data={"prompt": "x"}, subentry_type="agent", title="Kitchen agent", unique_id=None))
    data, _ = await _data(hass)
    key = _key(entity_id)
    key.cap_integration_write = "allow"
    content, outcome, _ = await _call_tool("list_integrations", {}, key, hass, data)
    assert outcome == "allowed", content
    [row] = [row for row in json.loads(_text(content))["integrations"] if row["entry_id"] == entry.entry_id]
    assert [(sub["subentry_type"], sub["title"]) for sub in row["subentries"]] == [("agent", "Kitchen agent")]


async def test_a_subentry_change_without_approval_never_runs(hass, agent_home):
    entry, entity_id = agent_home
    data, pending = await _data(hass)
    key = _key(entity_id)
    await _call_tool("set_integration_subentry", {
        "entry_id": entry.entry_id, "subentry_type": "agent", "config": {"prompt": "x"}}, key, hass, data)
    content, outcome, _ = await _execute_set_integration_subentry(pending[0]["args"], key, hass, data)
    assert outcome == "denied", content
    assert not entry.subentries
