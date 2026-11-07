"""get_todo_items and set_todo_item against Home Assistant's own todo component.

Reads need only READ on the list; changes run as the todo service through
call_service and need WRITE, and the list is read back so the result says what
it now holds. Unknown and inaccessible lists answer the same.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.components.todo.const import DATA_COMPONENT
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import APPROVAL_TARGETS_KEY, TOOL_RESULT_MAX_BYTES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import MesaVerdict, build_mesa_service_diff
from custom_components.phoenix_mcp.service_targets import todo_items_in_review, todo_removal_review
from custom_components.phoenix_mcp.tool_common import RefusedBeforeWrite, UncertainOutcome, stored_result_bytes
from custom_components.phoenix_mcp.tool_defs import _ENTITY_TOOL_DEFS
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.tools import todo as todo_tools
from custom_components.phoenix_mcp.tools.service import _build_diff_call_service
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


@pytest.mark.parametrize("fields", [
    {"action": "update", "item": "milk", "description": "password: <redacted>\nnew note"},
    {"action": "update", "item": "milk", "summary": "Milk <redacted>"},
    {"action": "update", "item": "milk", "description": "see <redacted-entity>"},
    {"action": "add", "summary": "password: <redacted>"},
    {"action": "add", "summary": "Tea", "description": "<redacted>"},
])
async def test_a_redacted_value_cannot_be_written_back(hass, lists, fields):
    """A read masks a secret or a hidden entity, so sending that text back would overwrite the real one."""
    lists.todo_items[0].description = "password: hunter2"
    before = [(item.uid, item.summary, item.description) for item in lists.todo_items]
    with patch.object(todo_tools, "_tool_call_service") as service:
        body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", **fields})
    assert outcome == "invalid_request"
    assert "Redacted item text" in body
    service.assert_not_called()
    assert [(item.uid, item.summary, item.description) for item in lists.todo_items] == before


async def test_a_masked_note_a_read_returns_is_refused_end_to_end(hass, lists):
    lists.todo_items[0].description = "password: hunter2"
    read, _ = await _call(hass, "get_todo_items", {"entity_id": "todo.shopping"})
    masked = next(item for item in read["items"] if item["uid"] == "milk")["description"]
    assert "hunter2" not in masked
    _, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "update", "item": "milk",
                                                     "description": f"{masked}\nnew note"})
    assert outcome == "invalid_request"
    assert lists.todo_items[0].description == "password: hunter2"


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
    assert outcome == "invalid_request" and body["applied"] is True and body["verified"] is False
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
    assert outcome == "invalid_request" and body["applied"] is None and body["verified"] is False and "item" not in body


async def _set(hass, **args):
    return await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", **args})


async def test_null_clears_due_and_description(hass, lists):
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1), description="Whole")
    body, outcome = await _set(hass, action="update", item="milk", due=None, description=None)
    assert outcome == "allowed" and body["verified"] is True
    assert lists._attr_todo_items[0].due is None and lists._attr_todo_items[0].description is None
    assert "due" not in body["item"] and "description" not in body["item"]


async def test_an_empty_description_clears_and_omitted_fields_stay(hass, lists):
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1), description="Whole")
    _, outcome = await _set(hass, action="update", item="milk", description="")
    assert outcome == "allowed"
    assert lists._attr_todo_items[0].description is None and lists._attr_todo_items[0].due == date(2026, 12, 1)
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1), description="Whole")
    await _set(hass, action="update", item="milk", summary="Oat milk")
    assert lists._attr_todo_items[0].due == date(2026, 12, 1) and lists._attr_todo_items[0].description == "Whole"


async def test_an_empty_due_string_changes_nothing(hass, lists):
    """A client padding optional fields with "" must not wipe a due date."""
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1))
    await _set(hass, action="update", item="milk", summary="Oat milk", due="")
    assert lists._attr_todo_items[0].due == date(2026, 12, 1)


async def test_a_due_time_only_list_clears_through_its_own_field(hass, lists):
    lists._attr_supported_features = (TodoListEntityFeature.UPDATE_TODO_ITEM
                                      | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM)
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=datetime(2026, 12, 1, 8, tzinfo=UTC))
    lists.async_write_ha_state()
    body, outcome = await _set(hass, action="update", item="milk", due=None)
    assert outcome == "allowed" and body["verified"] is True
    assert lists._attr_todo_items[0].due is None


async def test_a_list_without_due_dates_refuses_clearing_one(hass, lists):
    lists._attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM
    lists.async_write_ha_state()
    body, outcome = await _set(hass, action="update", item="milk", due=None)
    assert outcome == "invalid_request" and "does not support due dates" in body


async def test_a_clear_that_does_not_take_reads_back_as_unapplied(hass, lists):
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1))
    lists.async_update_todo_item = AsyncMock()
    body, _ = await _set(hass, action="update", item="milk", due=None)
    assert body["verified"] is False and body["not_applied"] == ["due"]


async def test_an_add_ignores_null_and_empty_fields(hass, lists):
    body, outcome = await _set(hass, action="add", summary="Tea", due=None, description="")
    assert outcome == "allowed" and body["verified"] is True
    assert lists._attr_todo_items[-1].description is None


async def test_description_must_be_text(hass, lists):
    body, outcome = await _set(hass, action="update", item="milk", description=5)
    assert outcome == "invalid_request" and "description" in body


async def test_clear_completed_removes_exactly_the_completed_items(hass, lists):
    body, outcome = await _set(hass, action="clear_completed")
    assert outcome == "allowed" and body["verified"] is True and body["applied"] is True
    assert [item["uid"] for item in body["removed"]] == ["eggs", "bread2"]
    assert body["remaining_completed"] == 0
    assert [item.uid for item in lists._attr_todo_items] == ["milk", "bread1"]


async def test_clear_completed_never_uses_the_service_that_chooses_at_execution(hass, lists):
    called = []
    hass.bus.async_listen("call_service", lambda event: called.append(event.data["service"]))
    await _set(hass, action="clear_completed")
    await hass.async_block_till_done()
    assert "remove_item" in called and "remove_completed_items" not in called


async def test_clear_completed_with_nothing_to_clear_changes_nothing(hass, lists):
    lists._attr_todo_items = [item for item in lists._attr_todo_items if item.status == TodoItemStatus.NEEDS_ACTION]
    delete = lists.async_delete_todo_items = AsyncMock()
    body, outcome = await _set(hass, action="clear_completed")
    assert outcome == "allowed" and body["applied"] is False and body["removed"] == []
    delete.assert_not_awaited()


async def test_clear_completed_refuses_other_arguments(hass, lists):
    body, outcome = await _set(hass, action="clear_completed", item="eggs")
    assert outcome == "invalid_request" and "only the list" in body
    assert len(lists._attr_todo_items) == 4


async def test_clear_completed_on_a_list_without_uids_refuses(hass, lists):
    lists._attr_todo_items[1] = TodoItem(summary="Eggs", status=TodoItemStatus.COMPLETED)
    body, outcome = await _set(hass, action="clear_completed")
    assert outcome == "invalid_request" and "no uid" in body


async def test_clear_completed_works_through_at_most_one_batch(hass, lists, monkeypatch):
    monkeypatch.setattr("custom_components.phoenix_mcp.tools.todo.TODO_ITEMS_MAX_LIMIT", 1)
    body, outcome = await _set(hass, action="clear_completed")
    assert outcome == "allowed" and [item["uid"] for item in body["removed"]] == ["eggs"]
    assert body["remaining_completed"] == 1 and body["truncated"] is True


async def test_clear_completed_needs_write_and_hides_unreadable_lists(hass, lists):
    _, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.shopping", "action": "clear_completed"},
                             _key("YELLOW"))
    assert outcome == "denied"
    assert len(lists._attr_todo_items) == 4
    body, outcome = await _call(hass, "set_todo_item", {"entity_id": "todo.secret", "action": "clear_completed"})
    assert outcome == "not_found" and body == "Todo list not found."


async def test_clear_completed_says_when_the_list_cannot_be_read_back(hass, lists):
    with patch("custom_components.phoenix_mcp.tools.todo._items", side_effect=[
        [{"uid": "eggs", "summary": "Eggs", "status": "completed"}], RuntimeError("gone"),
    ]):
        content, outcome, _ = await _call_tool(
            "set_todo_item", {"entity_id": "todo.shopping", "action": "clear_completed"}, _key(), hass, _data())
    assert outcome == "invalid_request" and content["isError"] is True
    assert json.loads(content["content"][0]["text"])["verified"] is False


async def test_an_approval_shows_the_items_and_binds_their_uids(hass, lists):
    args = {"domain": "todo", "service": "remove_item", "service_data": {"item": ["eggs", "bread2"]},
            "entity_id": ["todo.shopping"]}
    diff = _build_diff_call_service(args, _key(), hass)
    assert diff["preview"]["todo_items"] == [
        {"list": "todo.shopping", "uid": "eggs", "summary": "Eggs", "status": "completed"},
        {"list": "todo.shopping", "uid": "bread2", "summary": "bread", "status": "completed"},
    ]
    assert diff["preview"]["service_data"] == {"item": ["eggs", "bread2"]}


async def test_a_confirm_profile_queues_the_items_for_review_without_removing_them(hass, lists):
    data = _data()
    data.mesa = MagicMock()
    data.store.get_settings().mesa_mode = "enforced"
    approval = MagicMock(id="a", expires_at=utcnow())
    verdict = MesaVerdict(confirm=["todo.shopping"])
    with (patch("custom_components.phoenix_mcp.mesa.evaluate_service_call", return_value=verdict),
          patch("custom_components.phoenix_mcp.mesa.async_create_mesa_approval",
                AsyncMock(return_value=approval)) as create):
        await _call_tool("set_todo_item", {"entity_id": "todo.shopping", "action": "clear_completed"},
                         _key(), hass, data)
    saved = create.await_args.kwargs
    assert saved["tool_name"] == "set_todo_item"
    assert saved["args"]["action"] == "clear_completed"
    assert [row["uid"] for row in saved["args"]["reviewed"]] == ["eggs", "bread2"]
    assert saved["args"][APPROVAL_TARGETS_KEY]["entity_ids"] == ["todo.shopping"]
    assert saved["diff"]["preview"]["service_data"] == {"item": ["eggs", "bread2"]}
    assert saved["diff"]["preview"]["target_semantics"] == "exact"
    assert saved["diff"]["preview"]["todo_items"] == [
        {"list": "todo.shopping", "uid": "eggs", "summary": "Eggs", "status": "completed"},
        {"list": "todo.shopping", "uid": "bread2", "summary": "bread", "status": "completed"},
    ]
    assert len(lists._attr_todo_items) == 4


def test_a_review_without_items_adds_nothing(hass):
    verdict = MesaVerdict(confirm=["todo.shopping"])
    assert "todo_items" not in build_mesa_service_diff("todo", "remove_item", {"item": []}, verdict)["preview"]
    assert todo_removal_review(hass, "todo", "remove_item", {"item": []}, ["todo.shopping"]).keys() == {"warning"}
    assert todo_removal_review(hass, "todo", "update_item", {"item": "eggs"}, ["todo.shopping"]) == {}


async def test_only_a_named_item_on_a_resolved_list_reaches_the_review(hass, lists):
    data = {"item": "eggs"}
    assert todo_items_in_review(hass, "todo", "remove_item", data, ["todo.shopping"]) == [
        {"list": "todo.shopping", "uid": "eggs", "summary": "Eggs", "status": "completed"}]
    assert todo_items_in_review(hass, "todo", "remove_item", data, []) == []
    assert todo_items_in_review(hass, "todo", "remove_item", {"item": 5}, ["todo.shopping"]) == []
    assert todo_items_in_review(hass, "todo", "update_item", data, ["todo.shopping"]) == []
    assert todo_items_in_review(hass, "light", "remove_item", data, ["todo.shopping"]) == []
    assert todo_items_in_review(hass, "todo", "remove_item", data, ["todo.nothing"]) == []


async def _queued(hass, lists, key, **args) -> dict:
    """What a MESA Confirm saves for a request, with the list left as it was."""
    data = _data()
    data.mesa = MagicMock()
    data.store.get_settings().mesa_mode = "enforced"
    approval = MagicMock(id="a", expires_at=utcnow())
    with (patch("custom_components.phoenix_mcp.mesa.evaluate_service_call",
                return_value=MesaVerdict(confirm=["todo.shopping"])),
          patch("custom_components.phoenix_mcp.mesa.async_create_mesa_approval",
                AsyncMock(return_value=approval)) as create):
        await _call_tool("set_todo_item", {"entity_id": "todo.shopping", **args}, key, hass, data)
    return create.await_args.kwargs


async def _approve(hass, saved, key, data=None):
    """Run the saved action the way an administrator's Approve does."""
    return await async_execute_approved_tool(saved["tool_name"], saved["args"], key, hass, data or _data())


async def test_an_approved_clear_leaves_a_reopened_item_and_says_so(hass, lists):
    key = _key()
    saved = await _queued(hass, lists, key, action="clear_completed")
    lists._attr_todo_items[1] = TodoItem(uid="eggs", summary="Eggs", status=TodoItemStatus.NEEDS_ACTION)
    content, outcome, _ = await _approve(hass, saved, key)
    body = json.loads(content["content"][0]["text"])
    assert outcome == "allowed" and "isError" not in content
    assert [item.uid for item in lists._attr_todo_items] == ["milk", "eggs", "bread1"]
    assert body["removed"] == [{"uid": "bread2", "summary": "bread"}] and body["removed_count"] == 1
    assert body["not_removed"] == [{"uid": "eggs", "summary": "Eggs", "reason": "reopened"}]
    assert body["verified"] is True


async def test_an_approved_clear_leaves_an_item_edited_while_it_waited(hass, lists):
    key = _key()
    saved = await _queued(hass, lists, key, action="clear_completed")
    lists._attr_todo_items[1] = TodoItem(uid="eggs", summary="Eggs", status=TodoItemStatus.COMPLETED,
                                         description="Keep the receipt")
    content, _, _ = await _approve(hass, saved, key)
    body = json.loads(content["content"][0]["text"])
    assert "eggs" in [item.uid for item in lists._attr_todo_items]
    assert body["not_removed"] == [{"uid": "eggs", "summary": "Eggs", "reason": "changed"}]


async def test_an_item_removed_while_it_waited_does_not_stop_the_rest(hass, lists):
    """Core refuses a whole remove_item call for one missing uid, so the gone one is left out of it."""
    key = _key()
    saved = await _queued(hass, lists, key, action="clear_completed")
    del lists._attr_todo_items[1]
    content, outcome, _ = await _approve(hass, saved, key)
    body = json.loads(content["content"][0]["text"])
    assert outcome == "allowed" and [item.uid for item in lists._attr_todo_items] == ["milk", "bread1"]
    assert body["removed"] == [{"uid": "bread2", "summary": "bread"}]
    assert body["not_removed"] == [{"uid": "eggs", "summary": "Eggs", "reason": "already_gone"}]


async def test_an_approved_clear_with_nothing_still_reviewed_writes_nothing(hass, lists):
    key = _key()
    saved = await _queued(hass, lists, key, action="clear_completed")
    for index in (1, 3):
        lists._attr_todo_items[index] = TodoItem(uid=lists._attr_todo_items[index].uid, summary="Back",
                                                 status=TodoItemStatus.NEEDS_ACTION)
    delete = lists.async_delete_todo_items = AsyncMock()
    content, outcome, _ = await _approve(hass, saved, key)
    delete.assert_not_awaited()
    assert isinstance(content, RefusedBeforeWrite) and outcome == "invalid_request"
    body = json.loads(content["content"][0]["text"])
    assert body["applied"] is False and body["removed"] == []
    assert [row["reason"] for row in body["not_removed"]] == ["reopened", "reopened"]


async def test_an_approved_clear_is_gated_by_mesa_again(hass, lists):
    key = _key()
    saved = await _queued(hass, lists, key, action="clear_completed")
    data = _data()
    data.mesa = MagicMock()
    data.store.get_settings().mesa_mode = "enforced"
    with patch("custom_components.phoenix_mcp.mesa.evaluate_service_call",
               return_value=MesaVerdict(blocked=[("todo.shopping", "prohibited", "Now prohibited.")])) as judged:
        _, outcome, _ = await _approve(hass, saved, key, data)
    assert outcome == "denied" and len(lists._attr_todo_items) == 4
    assert judged.call_args.kwargs["service"] == "remove_item"
    assert judged.call_args.kwargs["confirm_approved"] is True


async def test_an_approved_clear_refuses_a_key_that_lost_the_list(hass, lists):
    key = _key()
    saved = await _queued(hass, lists, key, action="clear_completed")
    key.permissions.entities["todo.shopping"].state = "RED"
    _, outcome, _ = await _approve(hass, saved, key)
    assert outcome in ("denied", "not_found") and len(lists._attr_todo_items) == 4


async def test_an_approved_remove_leaves_an_item_that_changed_and_names_what_went(hass, lists):
    key = _key()
    saved = await _queued(hass, lists, key, action="remove", item="milk")
    assert saved["tool_name"] == "set_todo_item"
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Oat milk", status=TodoItemStatus.NEEDS_ACTION)
    content, _, _ = await _approve(hass, saved, key)
    assert "milk" in [item.uid for item in lists._attr_todo_items]
    assert json.loads(content["content"][0]["text"])["not_removed"][0]["reason"] == "changed"
    saved = await _queued(hass, lists, key, action="remove", item="milk")
    content, outcome, _ = await _approve(hass, saved, key)
    body = json.loads(content["content"][0]["text"])
    assert outcome == "allowed" and body["verified"] is True and body["item"]["summary"] == "Oat milk"
    assert "milk" not in [item.uid for item in lists._attr_todo_items]


async def test_a_saved_todo_removal_with_a_wrong_shape_writes_nothing(hass, lists):
    for args in ({}, {"entity_id": "todo.shopping", "action": "add", "reviewed": [{"uid": "eggs"}]},
                 {"entity_id": "todo.shopping", "action": "clear_completed", "reviewed": []},
                 {"entity_id": "todo.shopping", "action": "clear_completed", "reviewed": ["eggs"]},
                 {"entity_id": "todo.shopping", "action": "clear_completed", "reviewed": [{"summary": "Eggs"}]}):
        content, outcome, _ = await async_execute_approved_tool("set_todo_item", args, _key(), hass, _data())
        assert isinstance(content, RefusedBeforeWrite) and outcome == "invalid_request"
    assert len(lists._attr_todo_items) == 4


async def test_the_review_says_removal_is_permanent_on_both_approval_paths(hass, lists):
    args = {"domain": "todo", "service": "remove_item", "service_data": {"item": ["eggs"]},
            "entity_id": ["todo.shopping"]}
    assert "permanent" in _build_diff_call_service(args, _key(), hass)["preview"]["warning"]
    saved = await _queued(hass, lists, _key(), action="clear_completed")
    assert "permanent" in saved["diff"]["preview"]["warning"]
    assert "warning" not in _build_diff_call_service({**args, "service": "update_item"}, _key(), hass)["preview"]


def test_the_tool_description_says_removal_is_permanent():
    definition = next(tool for tool in _ENTITY_TOOL_DEFS if tool["name"] == "set_todo_item")
    assert "permanent" in definition["description"]


async def test_a_padded_update_keeps_the_due_date_and_description(hass, lists):
    """null in a field with no null form shows the client fills every field, so its other nulls say nothing."""
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1), description="Whole")
    body, outcome = await _set(hass, action="update", item="milk", summary="Oat", due=None, description=None, status=None)
    assert outcome == "allowed" and body["verified"] is True
    assert lists._attr_todo_items[0].summary == "Oat"
    assert lists._attr_todo_items[0].due == date(2026, 12, 1) and lists._attr_todo_items[0].description == "Whole"
    assert body["ignored_null"] == ["due", "description"] and "cleared" not in body


async def test_a_padded_update_with_no_real_change_changes_nothing(hass, lists):
    body, outcome = await _set(hass, action="update", item="milk", summary=None, due=None, description=None)
    assert outcome == "invalid_request" and "padding" in body


async def test_a_clear_reports_the_values_it_removed(hass, lists):
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1), description="Whole")
    body, outcome = await _set(hass, action="update", item="milk", summary="Oat", due=None, description=None)
    assert outcome == "allowed" and lists._attr_todo_items[0].due is None
    assert body["cleared"] == {"due": "2026-12-01", "description": "Whole"}


async def test_clearing_an_empty_field_reports_nothing_cleared(hass, lists):
    body, _ = await _set(hass, action="update", item="milk", due=None)
    assert "cleared" not in body


async def test_a_clear_result_holds_only_uid_and_summary_and_fits(hass, lists):
    lists._attr_todo_items = [TodoItem(uid=f"u{i}", summary=f"Item {i}", status=TodoItemStatus.COMPLETED,
                                       description="x" * 4000) for i in range(500)]
    content, outcome, _ = await _call_tool("set_todo_item", {"entity_id": "todo.shopping", "action": "clear_completed"},
                                           _key(), hass, _data())
    assert outcome == "allowed" and stored_result_bytes(content) <= TOOL_RESULT_MAX_BYTES
    body = json.loads(content["content"][0]["text"])
    assert body["removed_count"] == 500 and len(body["removed"]) == 500
    assert set(body["removed"][0]) == {"uid", "summary"}


async def test_a_clear_removes_only_what_its_result_can_name(hass, lists):
    """Nothing goes unnamed: past what one result holds, items wait for the next call."""
    lists._attr_todo_items = [TodoItem(uid=f"u{i}", summary=f"{i} " + "x" * 900, status=TodoItemStatus.COMPLETED)
                              for i in range(500)]
    named: list[str] = []
    for _ in range(20):
        content, outcome, _ = await _call_tool(
            "set_todo_item", {"entity_id": "todo.shopping", "action": "clear_completed"}, _key(), hass, _data())
        assert outcome == "allowed" and stored_result_bytes(content) <= TOOL_RESULT_MAX_BYTES
        body = json.loads(content["content"][0]["text"])
        named += [row["uid"] for row in body["removed"]]
        assert body["remaining_completed"] == 500 - len(named)
        assert body["truncated"] is (len(named) < 500)
        if not body["truncated"]:
            break
    assert sorted(named) == sorted(f"u{i}" for i in range(500)) and lists._attr_todo_items == []


async def test_a_long_summary_is_clipped_in_the_receipt_and_the_item_still_goes(hass, lists):
    lists._attr_todo_items = [TodoItem(uid="long", summary="y" * 70000, status=TodoItemStatus.COMPLETED)]
    content, outcome, _ = await _call_tool("set_todo_item", {"entity_id": "todo.shopping", "action": "clear_completed"},
                                           _key(), hass, _data())
    assert outcome == "allowed" and lists._attr_todo_items == []
    assert stored_result_bytes(content) <= TOOL_RESULT_MAX_BYTES


async def test_a_clear_that_leaves_named_items_behind_is_uncertain(hass, lists):
    lists.async_delete_todo_items = AsyncMock()
    content, outcome, _ = await _call_tool("set_todo_item", {"entity_id": "todo.shopping", "action": "clear_completed"},
                                           _key(), hass, _data())
    assert isinstance(content, UncertainOutcome) and content["isError"] is True and outcome == "invalid_request"
    body = json.loads(content["content"][0]["text"])
    assert body["verified"] is False and body["status"] == "partial" and body["retry_safe"] is False
    assert body["removed"] == []
    assert [(row["uid"], row["reason"]) for row in body["not_removed"]] == [
        ("eggs", "still_listed"), ("bread2", "still_listed")]


async def test_a_change_that_does_not_read_back_is_uncertain(hass, lists):
    lists._attr_todo_items[0] = TodoItem(uid="milk", summary="Milk", status=TodoItemStatus.NEEDS_ACTION,
                                         due=date(2026, 12, 1))
    lists.async_update_todo_item = AsyncMock()
    content, outcome, _ = await _call_tool("set_todo_item", {"entity_id": "todo.shopping", "action": "update",
                                                             "item": "milk", "due": None}, _key(), hass, _data())
    assert isinstance(content, UncertainOutcome) and outcome == "invalid_request"
    assert json.loads(content["content"][0]["text"])["status"] == "partial"
