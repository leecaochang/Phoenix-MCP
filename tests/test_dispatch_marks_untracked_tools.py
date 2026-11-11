"""Write tools that mark dispatch inline: a refusal before the write wrote nothing.

One representative per module whose handler marks before its own effects: a native
intent, a dashboard card, a Zigbee group, a todo item and a Device Builder job. Each
runs through the real dispatch. A refusal reaches an agent turn as refused-before-write
and holds nothing; an interruption at a step before the effect is audited as cancelled
and leaves the key free; an interruption inside the effect stays an unknown outcome
that blocks the key's next write.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.components.todo.const import DATA_COMPONENT
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import radio as radio_backend
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.agent_dispatch import dispatch_agent_tool
from custom_components.phoenix_mcp.audit import AuditLog, generate_request_id
from custom_components.phoenix_mcp.const import ANNOUNCE_BIT, AUDIT_STORAGE_KEY, AUDIT_STORAGE_VERSION, DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.esphome_builder import BuilderResult
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.tool_common import RefusedBeforeWrite
from custom_components.phoenix_mcp.tool_defs import _NATIVE_TOOL_NAMES
from custom_components.phoenix_mcp.tool_dispatch import _dispatch_mcp
from custom_components.phoenix_mcp.ws_dispatch import async_save_lovelace_config
from tests.runtime_fixtures import FIRMWARE_FILE, firmware_dash

_TOOLS = "custom_components.phoenix_mcp.tools"
_BROADCAST = _NATIVE_TOOL_NAMES["HassBroadcast"]


class _Hold:
    """An awaitable stand-in that says it was reached, then never returns."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()

    async def __call__(self, *_args, **_kwargs):
        self.reached.set()
        await asyncio.sleep(30)


@pytest.fixture
async def phoenix(hass):
    store = await AccessKeyStore.async_create(hass)
    store.get_settings().mesa_mode = "off"
    data = PhoenixData(store=store, rate_limiter=RateLimiter(),
                       audit=AuditLog(store=Store(hass, AUDIT_STORAGE_VERSION, AUDIT_STORAGE_KEY)))
    hass.data[DOMAIN] = data
    key, _raw = await store.async_create_access_key("marks", "admin", rate_limit_requests=0, rate_limit_burst=0)
    return data, key


async def _agent_result(hass, data, key, name: str, arguments: dict) -> dict:
    """One call as an agent turn makes it; an unknown outcome would raise RecoveryRequired."""
    msg, *_rest = await dispatch_agent_tool(
        _dispatch_mcp, hass=hass, data=data, access_key=key, name=name, arguments=arguments,
        tool_id=1, request_id=generate_request_id(), client_ip="10.0.0.5", base_url="http://x")
    return msg["result"]


def _assert_refused_and_free(result: dict, data, key) -> None:
    assert isinstance(result, RefusedBeforeWrite), result
    assert data.agent_recovery.records(key.id) == []
    assert not data.agent_recovery.blocks(key.id)


async def _interrupt(hass, data, key, name: str, arguments: dict, hold: _Hold) -> list[str]:
    """Cancel a dispatch once it reaches the held step; the cancelled rows' outcomes."""
    task = asyncio.create_task(_dispatch_mcp(
        "tools/call", 1, {"name": name, "arguments": arguments}, key, hass, data, "10.0.0.5", "http://x"))
    async with asyncio.timeout(3):
        await hold.reached.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await hass.async_block_till_done()
    return [row.outcome for row in data.audit.query() if row.method == "request/cancelled"]


def _assert_interrupted(rows: list[str], data, key, *, after_mark: bool) -> None:
    if after_mark:
        assert rows == ["unknown"]
        assert data.agent_recovery.blocks(key.id)
    else:
        assert rows == ["cancelled"]
        assert data.agent_recovery.records(key.id) == []


# --- native intent: HassBroadcast --------------------------------------------------


@pytest.fixture
def satellite(hass, phoenix):
    _data, key = phoenix
    key.cap_broadcast = "allow"
    key.permissions = PermissionTree(domains={"assist_satellite": PermissionNode(state="GREEN")})
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": ANNOUNCE_BIT})


async def test_a_broadcast_refused_before_announcing_returns_to_the_agent(hass, phoenix, satellite):
    data, key = phoenix
    calls: list = []
    hass.services.async_register("assist_satellite", "announce", calls.append)
    result = await _agent_result(hass, data, key, _BROADCAST, {"message": ""})
    _assert_refused_and_free(result, data, key)
    assert calls == []


@pytest.mark.parametrize("after_mark", [False, True], ids=["before_mark", "after_mark"])
async def test_an_interrupted_broadcast_is_unknown_only_once_it_announced(hass, phoenix, satellite, after_mark):
    data, key = phoenix
    hold = _Hold()
    if after_mark:
        async def announce(_call) -> None:
            await hold()

        hass.services.async_register("assist_satellite", "announce", announce)
        rows = await _interrupt(hass, data, key, _BROADCAST, {"message": "Dinner"}, hold)
    else:
        with patch(f"{_TOOLS}.native._mesa_gate_native", hold):
            rows = await _interrupt(hass, data, key, _BROADCAST, {"message": "Dinner"}, hold)
    _assert_interrupted(rows, data, key, after_mark=after_mark)


# --- lovelace: add_dashboard_card --------------------------------------------------


_CARD = {"type": "markdown", "content": "a"}


@pytest.fixture
async def dashboard(hass, phoenix):
    _data, key = phoenix
    key.cap_lovelace_write = "allow"
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    await async_save_lovelace_config(hass, None, {"views": [{"title": "V", "cards": [dict(_CARD)]}]})


async def test_a_card_refused_before_saving_returns_to_the_agent(hass, phoenix, dashboard):
    data, key = phoenix
    result = await _agent_result(hass, data, key, "add_dashboard_card", {"view_index": 5, "card": _CARD})
    _assert_refused_and_free(result, data, key)


@pytest.mark.parametrize("after_mark", [False, True], ids=["before_mark", "after_mark"])
async def test_an_interrupted_card_is_unknown_only_once_it_saved(hass, phoenix, dashboard, after_mark):
    data, key = phoenix
    hold = _Hold()
    seam = "async_save_lovelace_config" if after_mark else "async_get_lovelace_config"
    with patch(f"{_TOOLS}.lovelace.{seam}", hold):
        rows = await _interrupt(hass, data, key, "add_dashboard_card", {"view_index": 0, "card": _CARD}, hold)
    _assert_interrupted(rows, data, key, after_mark=after_mark)


# --- radio: create_zigbee_group ----------------------------------------------------


_IEEE = "00:11:22:33:44:55:66:77"


@pytest.fixture
def zigbee(hass, phoenix):
    _data, key = phoenix
    key.cap_radio_write = "allow"
    key.cap_physical_control = "allow"
    key.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    entry = MockConfigEntry(domain="zha", entry_id="zigbee")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", _IEEE)}, name="Node")
    light = er.async_get(hass).async_get_or_create(
        "light", "zha", "node-light", config_entry=entry, device_id=device.id, suggested_object_id="node")
    hass.states.async_set(light.entity_id, "on")
    return device


def _create_change(device) -> SimpleNamespace:
    """A resolved create, standing in for the radio network the real resolver reads."""
    member = SimpleNamespace(device=device, backend=radio_backend.BACKEND_ZHA, ieee=_IEEE)
    return SimpleNamespace(
        tool_name="create_zigbee_group", operation="create", backend=radio_backend.BACKEND_ZHA, name="Group",
        group=None, members=(member,), endpoints=(1,), before_hash="",
    )


async def test_a_group_refused_before_the_radio_returns_to_the_agent(hass, phoenix, zigbee):
    data, key = phoenix
    backend = AsyncMock()
    with patch(f"{_TOOLS}.radio.radio_backend.async_create_zigbee_group", backend):
        result = await _agent_result(hass, data, key, "create_zigbee_group",
                                     {"name": "a/b", "members": [{"device_id": zigbee.id}]})
    _assert_refused_and_free(result, data, key)
    backend.assert_not_awaited()


@pytest.mark.parametrize("after_mark", [False, True], ids=["before_mark", "after_mark"])
async def test_an_interrupted_group_is_unknown_only_once_the_radio_was_asked(hass, phoenix, zigbee, after_mark):
    data, key = phoenix
    hold = _Hold()
    resolver = AsyncMock(return_value=_create_change(zigbee)) if after_mark else hold
    backend = hold if after_mark else AsyncMock()
    with (patch.dict(f"{_TOOLS}.radio._GROUP_CHANGE_RESOLVERS", {"create_zigbee_group": resolver}),
          patch(f"{_TOOLS}.radio.radio_backend.async_create_zigbee_group", backend)):
        rows = await _interrupt(hass, data, key, "create_zigbee_group",
                                {"name": "Group", "members": [{"device_id": zigbee.id}]}, hold)
    _assert_interrupted(rows, data, key, after_mark=after_mark)


# --- todo: set_todo_item -----------------------------------------------------------


class _List(TodoListEntity):
    """An in-memory list that can add items."""

    _attr_supported_features = TodoListEntityFeature.CREATE_TODO_ITEM

    def __init__(self) -> None:
        self._attr_name = "Shopping"
        self._attr_unique_id = "shopping"
        self._attr_todo_items = [TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION)]
        self.adding: _Hold | None = None

    async def async_create_todo_item(self, item: TodoItem) -> None:
        if self.adding is not None:
            await self.adding()
        self._attr_todo_items = [*self._attr_todo_items, item]
        self.async_write_ha_state()


@pytest.fixture
async def shopping(hass, phoenix):
    _data, key = phoenix
    key.permissions = PermissionTree(entities={"todo.shopping": PermissionNode(state="GREEN")})
    assert await async_setup_component(hass, "todo", {})
    entity = _List()
    await hass.data[DATA_COMPONENT].async_add_entities([entity])
    await hass.async_block_till_done()
    return entity


async def test_a_todo_change_refused_before_the_service_returns_to_the_agent(hass, phoenix, shopping):
    data, key = phoenix
    result = await _agent_result(hass, data, key, "set_todo_item", {"entity_id": "todo.shopping", "action": "add"})
    _assert_refused_and_free(result, data, key)
    assert [item.summary for item in shopping.todo_items] == ["Milk"]


@pytest.mark.parametrize("after_mark", [False, True], ids=["before_mark", "after_mark"])
async def test_an_interrupted_todo_change_is_unknown_only_once_the_service_ran(hass, phoenix, shopping, after_mark):
    data, key = phoenix
    hold = _Hold()
    arguments = {"entity_id": "todo.shopping", "action": "add", "summary": "Tea"}
    if after_mark:
        shopping.adding = hold
        rows = await _interrupt(hass, data, key, "set_todo_item", arguments, hold)
    else:
        with patch(f"{_TOOLS}.todo._items", hold):
            rows = await _interrupt(hass, data, key, "set_todo_item", arguments, hold)
    _assert_interrupted(rows, data, key, after_mark=after_mark)


# --- esphome: clean_esphome_build --------------------------------------------------


def _builder(**replies):
    """Device Builder commands by name: a reply, or a _Hold to wait inside that command."""
    async def command(_hass, _url, name, _args, **_kwargs):
        reply = replies.get(name, {})
        if isinstance(reply, _Hold):
            await reply()
        return BuilderResult(result=reply)
    return command


@pytest.fixture
def device_builder(hass, phoenix, esphome_dir):
    _data, key = phoenix
    key.cap_esphome_yaml = "allow"
    key.permissions = PermissionTree(domains={"sensor": PermissionNode(state="GREEN")})
    with patch(f"{_TOOLS}.esphome._esphome_dashboard", return_value=firmware_dash()):
        yield


async def test_a_clean_refused_before_the_builder_returns_to_the_agent(hass, phoenix, device_builder):
    data, key = phoenix
    running = [{"job_id": "running-1", "configuration": FIRMWARE_FILE, "status": "running"}]
    with patch(f"{_TOOLS}.esphome.async_builder_command", _builder(**{"firmware/get_jobs": running})):
        result = await _agent_result(hass, data, key, "clean_esphome_build", {"file": FIRMWARE_FILE})
    _assert_refused_and_free(result, data, key)


@pytest.mark.parametrize("after_mark", [False, True], ids=["before_mark", "after_mark"])
async def test_an_interrupted_clean_is_unknown_only_once_the_builder_was_asked(
    hass, phoenix, device_builder, after_mark,
):
    data, key = phoenix
    hold = _Hold()
    replies = {"firmware/get_jobs": [], "firmware/clean": hold} if after_mark else {"firmware/get_jobs": hold}
    with patch(f"{_TOOLS}.esphome.async_builder_command", _builder(**replies)):
        rows = await _interrupt(hass, data, key, "clean_esphome_build", {"file": FIRMWARE_FILE}, hold)
    _assert_interrupted(rows, data, key, after_mark=after_mark)
