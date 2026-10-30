"""get_todo_items and set_todo_item against Home Assistant's own todo component.

Reads need only READ on the list; changes run as the todo service through
call_service and need WRITE, and the list is read back so the result says what
it now holds. Unknown and inaccessible lists answer the same.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.components.todo.const import DATA_COMPONENT
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from tests.runtime_fixtures import mesa_off_store


class _List(TodoListEntity):
    """An in-memory list supporting every item change."""

    _attr_supported_features = (TodoListEntityFeature.CREATE_TODO_ITEM | TodoListEntityFeature.UPDATE_TODO_ITEM
                                 | TodoListEntityFeature.DELETE_TODO_ITEM | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
                                 | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM)

    def __init__(self, name: str, items: list[TodoItem]) -> None:
        self._attr_name = name
        self._attr_unique_id = name.lower()
        self._attr_todo_items = items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        self._attr_todo_items = [*self._attr_todo_items, TodoItem(
            uid=uuid.uuid4().hex, summary=item.summary, status=TodoItemStatus.NEEDS_ACTION,
            due=item.due, description=item.description)]
        self.async_write_ha_state()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        # Home Assistant passes an item without a uid on a list that gives none; such a list finds it by text.
        index = next(i for i, existing in enumerate(self._attr_todo_items)
                     if (existing.uid == item.uid if item.uid else existing.summary == item.summary))
        self._attr_todo_items = [*self._attr_todo_items[:index], item, *self._attr_todo_items[index + 1:]]
        self.async_write_ha_state()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        self._attr_todo_items = [item for item in self._attr_todo_items if item.uid not in uids]
        self.async_write_ha_state()


@pytest.fixture
async def lists(hass):
    assert await async_setup_component(hass, "todo", {})
    shopping = _List("Shopping", [
        TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION),
        TodoItem(uid="eggs", summary="Eggs", status=TodoItemStatus.COMPLETED),
        TodoItem(uid="bread1", summary="Bread", status=TodoItemStatus.NEEDS_ACTION),
        TodoItem(uid="bread2", summary="bread", status=TodoItemStatus.COMPLETED),
    ])
    secret = _List("Secret", [TodoItem(uid="gift", summary="Gift", status=TodoItemStatus.NEEDS_ACTION)])
    await hass.data[DATA_COMPONENT].async_add_entities([shopping, secret])
    await hass.async_block_till_done()
    return shopping


def _key(state: str = "GREEN") -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(entities={"todo.shopping": PermissionNode(state=state)}),
    )


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), mesa=None)


async def _call(hass, tool, args, key=None):
    content, outcome, _ = await _call_tool(tool, args, key or _key(), hass, _data())
    text = content["content"][0]["text"]
    return (json.loads(text) if text.startswith("{") else text), outcome


async def test_every_readable_list_is_read_and_hidden_ones_are_not(hass, lists):
    body, outcome = await _call(hass, "get_todo_items", {})
    assert outcome == "allowed"
    assert body["lists"] == [{"entity_id": "todo.shopping", "name": "Shopping"}]
    assert [item["uid"] for item in body["items"]] == ["milk", "eggs", "bread1", "bread2"]
    assert all(item["list"] == "todo.shopping" for item in body["items"]) and body["total"] == 4
    assert "Gift" not in json.dumps(body)


async def test_a_status_narrows_and_named_lists_say_what_was_not_found(hass, lists):
    body, _ = await _call(hass, "get_todo_items", {"entity_id": ["todo.shopping", "todo.secret", "todo.nothing"],
                                                   "status": "needs_action"})
    assert [item["summary"] for item in body["items"]] == ["Milk", "Bread"]
    # Hidden and missing lists read the same.
    assert body["not_found"] == ["todo.secret", "todo.nothing"]


async def test_a_read_only_key_reads_but_cannot_change(hass, lists):
    body, outcome = await _call(hass, "get_todo_items", {"entity_id": "todo.shopping"}, _key("YELLOW"))
    assert outcome == "allowed" and body["total"] == 4
    _, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "add", "summary": "Tea"},
                             _key("YELLOW"))
    assert outcome == "denied"
    assert [item.summary for item in lists.todo_items] == ["Milk", "Eggs", "Bread", "bread"]


async def test_add_complete_and_remove_are_read_back(hass, lists):
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "add",
                                                        "summary": "Tea", "due": "2026-11-02"})
    assert outcome == "allowed" and body["verified"] is True
    assert body["item"]["summary"] == "Tea" and body["item"]["due"] == "2026-11-02"
    tea = body["item"]["uid"]
    body, _ = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "update", "item": "milk",
                                                  "status": "completed"})
    assert body["verified"] is True and body["item"]["status"] == "completed"
    body, _ = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "remove", "item": "Tea"})
    assert body["verified"] is True and body["item"]["uid"] == tea
    assert tea not in [item.uid for item in lists.todo_items]


async def test_an_ambiguous_summary_lists_the_uids_and_changes_nothing(hass, lists):
    text, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "remove", "item": "BREAD"})
    assert outcome == "invalid_request" and "bread1" in text and "bread2" in text
    assert len(lists.todo_items) == 4


@pytest.mark.parametrize("entity_id", ["todo.secret", "todo.nothing"])
async def test_a_hidden_list_and_a_missing_one_refuse_alike(hass, lists, entity_id):
    text, outcome = await _call(hass, "set_todo_item", {"entity_id": entity_id, "action": "add", "summary": "x"})
    assert text == "Todo list not found." and outcome == "not_found"


async def test_a_list_without_uids_is_changed_by_the_items_text(hass, lists):
    # Some integrations give their items no uid; Home Assistant's services then take the item's text.
    plain = _List("Plain", [TodoItem(summary="Soap", status=TodoItemStatus.NEEDS_ACTION),
                            TodoItem(summary="Rice", status=TodoItemStatus.NEEDS_ACTION),
                            TodoItem(summary="Rice", status=TodoItemStatus.COMPLETED)])
    await hass.data[DATA_COMPONENT].async_add_entities([plain])
    await hass.async_block_till_done()
    key = _key()
    key.permissions.entities["todo.plain"] = PermissionNode(state="GREEN")
    body, _ = await _call(hass, "get_todo_items", {"entity_id": "todo.plain"}, key)
    assert all("uid" not in item for item in body["items"])
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.plain", "action": "update", "item": "soap",
                                                        "status": "completed"}, key)
    assert outcome == "allowed" and body["verified"] is True and body["item"]["status"] == "completed"
    text, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.plain", "action": "update", "item": "Rice",
                                                        "status": "completed"}, key)
    assert outcome == "invalid_request" and "no uid" in text
    # Home Assistant removes items only by uid, so removal is refused plainly rather than failing in the service.
    text, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.plain", "action": "remove", "item": "Soap"}, key)
    assert outcome == "invalid_request" and "removes items only by uid" in text and len(plain.todo_items) == 3


async def test_an_update_is_verified_field_by_field(hass, lists):
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "update", "item": "milk",
                                                        "summary": "Oat milk", "due": "2026-11-02",
                                                        "description": "Two cartons"})
    assert outcome == "allowed", body
    assert body["verified"] is True and "not_applied" not in body
    assert body["item"]["summary"] == "Oat milk" and body["item"]["description"] == "Two cartons"


async def test_a_field_the_list_drops_is_reported_not_applied(hass, lists):
    # Some lists keep only part of an item; the result says which fields did not take.
    async def keep_text_only(item):
        index = next(i for i, existing in enumerate(lists._attr_todo_items) if existing.uid == item.uid)
        lists._attr_todo_items[index] = TodoItem(uid=item.uid, summary=item.summary, status=item.status)
        lists.async_write_ha_state()
    lists.async_update_todo_item = keep_text_only
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "update", "item": "milk",
                                                        "status": "completed", "description": "Two cartons"})
    assert outcome == "allowed" and body["applied"] is True and body["verified"] is False
    assert body["not_applied"] == ["description"] and body["item"]["status"] == "completed"


def test_a_due_time_without_an_offset_is_compared_in_local_time(hass):
    from datetime import datetime
    from homeassistant.util import dt as dt_util
    from custom_components.phoenix_mcp.tools.todo import _same_due
    local = datetime(2026, 11, 2, 18, 0, tzinfo=dt_util.get_default_time_zone())
    assert _same_due(local.isoformat(), "2026-11-02T18:00") and _same_due(local.isoformat(), "2026-11-02 18:00")
    assert _same_due(dt_util.as_utc(local).isoformat(), "2026-11-02T18:00")
    assert not _same_due(local.isoformat(), "2026-11-02T19:00") and not _same_due(None, "2026-11-02")


class _Unavailable(_List):
    """A list whose integration has lost it: Core's entity services skip it and still answer success."""

    _attr_available = False


@pytest.fixture
async def gone(hass, lists):
    """todo.gone unavailable through its entity, todo.restored as the placeholder held while an integration loads."""
    await hass.data[DATA_COMPONENT].async_add_entities(
        [_Unavailable("Gone", [TodoItem(uid="soap", summary="Soap", status=TodoItemStatus.NEEDS_ACTION)])])
    await hass.async_block_till_done()
    hass.states.async_set("todo.restored", "unavailable", {"restored": True, "friendly_name": "Restored"})
    key = _key()
    key.permissions.entities["todo.gone"] = PermissionNode(state="GREEN")
    key.permissions.entities["todo.restored"] = PermissionNode(state="GREEN")
    return key


async def test_an_unavailable_list_is_reported_unreadable_not_empty(hass, gone):
    assert hass.states.get("todo.gone").state == "unavailable"
    body, outcome = await _call(hass, "get_todo_items", {"entity_id": ["todo.gone", "todo.restored", "todo.shopping"]},
                                gone)
    assert outcome == "allowed" and body["total"] == 4 and body["not_found"] == []
    assert {row["list"] for row in body["unreadable"]} == {"todo.gone", "todo.restored"}
    assert all("unavailable" in row["reason"] for row in body["unreadable"])
    body, _ = await _call(hass, "get_todo_items", {}, gone)
    assert {row["list"] for row in body["unreadable"]} == {"todo.gone", "todo.restored"}


@pytest.mark.parametrize("entity_id", ["todo.gone", "todo.restored"])
@pytest.mark.parametrize("args", [{"action": "add", "summary": "Tea"},
                                  {"action": "update", "item": "Soap", "status": "completed"},
                                  {"action": "remove", "item": "soap"}])
async def test_a_write_to_an_unavailable_list_is_refused_before_any_service_or_approval(
        hass, gone, entity_id, args):
    # Home Assistant announces every service call on its bus, so none may appear.
    called = []
    hass.bus.async_listen("call_service", lambda event: called.append(event.data))
    data = _data()
    data.approvals = MagicMock(async_create=AsyncMock())
    content, outcome, _ = await _call_tool("set_todo_item", {"entity_id": entity_id, **args}, gone, hass, data)
    await hass.async_block_till_done()
    assert called == []
    text = content["content"][0]["text"]
    assert outcome == "invalid_request" and "unavailable" in text and "No item" not in text
    data.approvals.async_create.assert_not_awaited()


async def test_the_service_call_listener_sees_a_real_write(hass, gone):
    """Positive control for the refusal test above: the same listener records an ordinary add."""
    called = []
    hass.bus.async_listen("call_service", lambda event: called.append(event.data["service"]))
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "add", "summary": "Tea"},
                                gone)
    await hass.async_block_till_done()
    assert outcome == "allowed" and body["verified"] is True and "add_item" in called


async def test_an_unavailable_hidden_list_still_reads_as_not_found(hass, gone):
    text, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.gone", "action": "add", "summary": "x"})
    assert text == "Todo list not found." and outcome == "not_found"


class _Prepending(_List):
    """A list that puts new items at the top, as some integrations do."""

    async def async_create_todo_item(self, item: TodoItem) -> None:
        self._attr_todo_items = [TodoItem(uid=uuid.uuid4().hex, summary=item.summary,
                                          status=TodoItemStatus.NEEDS_ACTION), *self._attr_todo_items]
        self.async_write_ha_state()


async def _added_list(hass, entity: _List) -> AccessKeyRecord:
    await hass.data[DATA_COMPONENT].async_add_entities([entity])
    await hass.async_block_till_done()
    key = _key()
    key.permissions.entities[entity.entity_id] = PermissionNode(state="GREEN")
    return key


async def test_an_add_on_a_prepending_list_reports_the_new_item_not_an_older_one(hass, lists):
    top = _Prepending("Top", [TodoItem(uid="old-eggs", summary="Eggs", status=TodoItemStatus.COMPLETED)])
    key = await _added_list(hass, top)
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.top", "action": "add", "summary": "Eggs"}, key)
    assert outcome == "allowed" and body["verified"] is True
    assert body["item"]["uid"] == top.todo_items[0].uid != "old-eggs"
    assert body["item"]["status"] == "needs_action"


async def test_an_add_that_creates_no_single_new_uid_is_not_verified(hass, lists):
    class _Twice(_List):
        async def async_create_todo_item(self, item: TodoItem) -> None:
            await super().async_create_todo_item(item)
            await super().async_create_todo_item(item)
    key = await _added_list(hass, _Twice("Twice", []))
    body, _ = await _call(hass, "set_todo_item", {"entity_id": "todo.twice", "action": "add", "summary": "Tea"}, key)
    assert body["applied"] is True and body["verified"] is False and "item" not in body


async def test_a_uidless_list_verifies_an_add_only_while_the_text_is_unique(hass, lists):
    class _Plain(_List):
        async def async_create_todo_item(self, item: TodoItem) -> None:
            self._attr_todo_items = [*self._attr_todo_items,
                                     TodoItem(summary=item.summary, status=TodoItemStatus.NEEDS_ACTION)]
            self.async_write_ha_state()
    key = await _added_list(hass, _Plain("Plain", [TodoItem(summary="Soap", status=TodoItemStatus.COMPLETED)]))
    body, _ = await _call(hass, "set_todo_item", {"entity_id": "todo.plain", "action": "add", "summary": "Tea"}, key)
    assert body["verified"] is True and body["item"]["summary"] == "Tea"
    body, _ = await _call(hass, "set_todo_item", {"entity_id": "todo.plain", "action": "add", "summary": "soap"}, key)
    assert body["applied"] is True and body["verified"] is False and "item" not in body


async def test_a_list_that_goes_unavailable_after_the_write_is_not_claimed(hass, lists):
    class _Lost(_List):
        async def async_create_todo_item(self, item: TodoItem) -> None:
            await super().async_create_todo_item(item)
            self._attr_available = False
            self.async_write_ha_state()
    lost = _Lost("Lost", [])
    key = await _added_list(hass, lost)
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.lost", "action": "add", "summary": "Tea"}, key)
    assert len(lost.todo_items) == 1
    assert outcome == "allowed" and body["applied"] is None and body["verified"] is False and "item" not in body
