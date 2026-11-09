"""Todo list reads and item changes, scoped like any other entity.

A list is read with READ access to its todo entity, through Home Assistant's own
todo.get_items, so a shopping list needs no service-response capability. Item
changes run as the matching todo service through call_service, so scope, MESA,
approvals and the audit trail are exactly that tool's, and the item is read back
afterwards so the result says what the list now holds.

Removal is permanent, so an approval binds the items as they were reviewed and its
own executor re-reads the list: whatever changed meanwhile stays, and the result
says so. The removal still runs through call_service's executor, with every gate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, cast

from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..access_key_store import AccessKeyRecord
from ..const import (
    APPROVAL_TARGETS_KEY, REDACTION_PLACEHOLDER_RE, TODO_ITEMS_DEFAULT_LIMIT, TODO_ITEMS_MAX_LIMIT, TODO_TIMEOUT_SECONDS,
)
from ..data import PhoenixData
from ..household_authority import household_service_kwargs
from ..policy_engine import Permission, resolve_read
from ..request_authority import AuthorityChangedError, check_request_authority, write_boundary
from ..tool_common import (
    _tool_error, _tool_refused_before_write, _tool_success, _tool_uncertain, fitted_offset_page, longest_fitting,
    offset_page,
)
from ..time_text import parse_datetime
from .calendar import _safe_calendar_data
from .service import TODO_PLACEHOLDER_REFUSAL, _execute_call_service, _tool_call_service

STATUSES = ("needs_action", "completed")
_TOOL = "set_todo_item"
_NOT_FOUND = "Todo list not found."
_UNAVAILABLE = "The list is unavailable, so Home Assistant skips it and returns no items."
# The schema's limit on an item's text. A list may hold longer; a receipt shows this much of it.
_SUMMARY_CHARS = 1000
# These fields have no null form, so a null in one shows a client filling every optional field.
_NEVER_NULL = ("summary", "status")
_CLEAR_LEFT_ITEMS = "The list still holds the items in not_removed, though Home Assistant accepted their removal."


def _unavailable(entity_id: str, hass: HomeAssistant) -> bool:
    """Whether the list reads unavailable, which Core's entity services skip yet answer success."""
    # A placeholder restored while its integration loads reads unavailable too.
    state = hass.states.get(entity_id)
    return state is not None and state.state == STATE_UNAVAILABLE


def _readable(entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> bool:
    return (entity_id.startswith("todo.") and hass.states.get(entity_id) is not None
            and resolve_read(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE))


def _statuses(raw: Any) -> list[str] | str:
    """The requested statuses, or a refusal naming the allowed ones."""
    if raw in (None, "", []):
        return list(STATUSES)
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list) or not values or any(value not in STATUSES for value in values):
        return "status must be needs_action, completed, or a list of them."
    return list(dict.fromkeys(values))


async def _items(hass: HomeAssistant, entity_id: str, statuses: list[str]) -> list[dict]:
    """One list's items, in the list's own order, through Home Assistant's todo.get_items."""
    response = await asyncio.wait_for(hass.services.async_call(
        "todo", "get_items", {"entity_id": entity_id, "status": statuses}, blocking=True, return_response=True,
        **household_service_kwargs(hass, "todo", "get_items"),
    ), TODO_TIMEOUT_SECONDS)
    # Home Assistant answers {entity_id: {"items": [...]}}; anything else fails
    # this one list, which the caller reports as unreadable.
    listed = cast("dict[str, Any] | None", (response or {}).get(entity_id))
    items = cast("list[Any]", (listed or {}).get("items") or [])
    return [dict(item) for item in items if isinstance(item, dict)]


async def _tool_get_todo_items(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: items of one todo list, several, or every list the access key can read."""
    tool = "get_todo_items"
    raw = args.get("entity_id")
    if isinstance(raw, str):
        raw = [raw]
    if raw is not None and (not isinstance(raw, list) or not raw or len(raw) > TODO_ITEMS_MAX_LIMIT
                            or not all(isinstance(item, str) and item.strip() for item in raw)):
        return _tool_error("entity_id must be a todo entity ID or a list of them."), "invalid_request", tool
    statuses = _statuses(args.get("status"))
    if isinstance(statuses, str):
        return _tool_error(statuses), "invalid_request", tool
    ids = list(dict.fromkeys(item.strip() for item in raw)) if raw is not None else sorted(
        state.entity_id for state in hass.states.async_all("todo"))
    readable = [entity_id for entity_id in ids if _readable(entity_id, access_key, hass)]
    rows: list[dict] = []
    unreadable: list[dict] = []
    for entity_id in readable:
        if _unavailable(entity_id, hass):
            unreadable.append({"list": entity_id, "reason": _UNAVAILABLE})
            continue
        try:
            rows += [{"list": entity_id, **item} for item in await _items(hass, entity_id, statuses)]
        except AuthorityChangedError:
            raise
        except TimeoutError:
            unreadable.append({"list": entity_id, "reason": "The list did not answer in time."})
        except Exception as exc:  # one list's failure must not hide the others
            unreadable.append({"list": entity_id, "reason": str(exc)[:200] or type(exc).__name__})
    check_request_authority()
    still = [entity_id for entity_id in readable if _readable(entity_id, access_key, hass)]
    rows = [row for row in rows if row["list"] in still]
    paged = offset_page(rows, args, default_limit=TODO_ITEMS_DEFAULT_LIMIT, max_limit=TODO_ITEMS_MAX_LIMIT)
    if isinstance(paged, str):
        return _tool_error(paged), "invalid_request", tool
    page, envelope = paged

    def build(items: list, envelope: dict) -> dict:
        lists = [{"entity_id": entity_id, "name": state.name} for entity_id in still
                 if (state := hass.states.get(entity_id)) is not None]
        body: dict[str, Any] = {"lists": lists, "status": statuses, **envelope, "items": items}
        if failed := [item for item in unreadable if item["list"] in still]:
            body["unreadable"] = failed
        body = _safe_calendar_data(body, access_key, hass)
        # The caller's own IDs: unknown and inaccessible lists read the same.
        if raw is not None:
            body["not_found"] = [entity_id for entity_id in ids if entity_id not in still]
        return body

    return _tool_success(_dumps(fitted_offset_page(page, envelope, build))), "allowed", tool


def _dumps(body: Any) -> str:
    return json.dumps(body, default=str)


def _due_fields(due: Any) -> dict[str, str] | str:
    """A due value as todo's own field: a date, or a date and time."""
    if due in (None, ""):
        return {}
    if not isinstance(due, str):
        return "due must be a date (2026-11-02) or a date and time (2026-11-02T18:00)."
    return {"due_datetime" if "T" in due or " " in due.strip() else "due_date": due.strip()}


def _clear_due_field(entity_id: str, hass: HomeAssistant) -> dict[str, None] | str:
    """The field that clears a due value, or why this list has none.

    Core checks each due field against its own supported feature, and a None in
    either one clears the item's due, so the field is the one this list supports.
    """
    # Local: optional component, imported only when this path runs.
    from homeassistant.components.todo import TodoListEntityFeature

    state = hass.states.get(entity_id)
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if state is not None else 0
    if features & TodoListEntityFeature.SET_DUE_DATE_ON_ITEM:
        return {"due_date": None}
    if features & TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM:
        return {"due_datetime": None}
    return "This list does not support due dates, so there is none to clear."


def _ref(item: dict) -> str:
    """How todo's services name an item: its uid, or its text on a list whose integration gives none."""
    return str(item.get("uid") or item.get("summary") or "")


def _match(items: list[dict], wanted: str) -> dict | str:
    """The one item a uid or exact summary names, or why there is not exactly one."""
    by_uid = [item for item in items if item.get("uid") and item.get("uid") == wanted]
    if by_uid:
        return by_uid[0]
    folded = wanted.strip().casefold()
    named = [item for item in items if str(item.get("summary") or "").strip().casefold() == folded]
    if len(named) == 1:
        return named[0]
    if not named:
        return f"No item on this list has uid or summary {wanted!r}."
    if not all(item.get("uid") for item in named):
        return (f"{len(named)} items are named {wanted!r} and this list gives its items no uid, so one cannot be "
                "chosen here; rename one of them in Home Assistant first.")
    choices = ", ".join(f"{item['uid']} ({item.get('status')})" for item in named[:10])
    return f"{len(named)} items are named {wanted!r}; pass one uid: {choices}."


async def _tool_set_todo_item(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: add, update or remove one item on a todo list the access key can write."""

    tool = _TOOL
    entity_id = str(args.get("entity_id") or "").strip()
    action = args.get("action")
    if action not in ("add", "update", "remove", "clear_completed"):
        return _tool_error("action must be add, update, remove or clear_completed."), "invalid_request", tool
    if not _readable(entity_id, access_key, hass):
        return _tool_error(_NOT_FOUND), "not_found", tool
    if _unavailable(entity_id, hass):
        # Core would skip the list and answer success, so nothing would change; say why before any approval.
        return _tool_error(
            "This todo list is unavailable, so Home Assistant would skip it and change nothing. Check the "
            "list's integration, then try again once the list is available."
        ), "invalid_request", tool
    if action == "clear_completed":
        return await _clear_completed(args, entity_id, access_key, hass, data, request_id, client_ip)
    due = _due_fields(args.get("due"))
    if isinstance(due, str):
        return _tool_error(due), "invalid_request", tool
    summary = args.get("summary")
    description = args.get("description")
    if description is not None and not isinstance(description, str):
        return _tool_error("description must be text, or null to clear it."), "invalid_request", tool
    status = args.get("status")
    if status is not None and status not in STATUSES:
        return _tool_error("status must be needs_action or completed."), "invalid_request", tool
    if any(isinstance(text, str) and REDACTION_PLACEHOLDER_RE.search(text) for text in (summary, description)):
        return _tool_error(TODO_PLACEHOLDER_REFUSAL), "invalid_request", tool
    target: dict | None = None
    before: list[dict] = []
    approve_as: tuple[str, dict] | None = None
    cleared: dict[str, Any] = {}
    ignored_null: list[str] = []
    service_data: dict[str, Any]
    if action == "add":
        if not isinstance(summary, str) or not summary.strip():
            return _tool_error("add needs summary, the item's text."), "invalid_request", tool
        if args.get("item") or status:
            return _tool_error("add takes summary, due and description; set status with update."), "invalid_request", tool
        # The items before the add, so the read-back can tell the new item from an older one with the same text.
        try:
            before = await _items(hass, entity_id, list(STATUSES))
        except TimeoutError:
            return _tool_error("The list did not answer in time."), "invalid_request", tool
        service, service_data = "add_item", {"item": summary.strip(), **due}
    else:
        wanted = args.get("item")
        if not isinstance(wanted, str) or not wanted.strip():
            return _tool_error(f"{action} needs item: the item's uid or its exact summary."), "invalid_request", tool
        try:
            matched = _match(await _items(hass, entity_id, list(STATUSES)), wanted)
        except TimeoutError:
            return _tool_error("The list did not answer in time."), "invalid_request", tool
        if isinstance(matched, str):
            return _tool_error(matched), "invalid_request", tool
        target = matched
        if action == "remove" and not matched.get("uid"):
            return _tool_error(
                "This list gives its items no uid, and Home Assistant removes items only by uid, so this item "
                "cannot be removed here; complete it instead, or remove it in the list's own app."
            ), "invalid_request", tool
        if action == "remove":
            service, service_data = "remove_item", {"item": [_ref(matched)]}
            approve_as = (tool, {"entity_id": entity_id, "action": action, "reviewed": [_reviewed(matched)]})
        else:
            changes = {**({"rename": summary.strip()} if isinstance(summary, str) and summary.strip() else {}),
                       **({"status": status} if status else {}), **due}
            padded = any(field in args and args[field] is None for field in _NEVER_NULL)
            ignored_null = [field for field in ("due", "description")
                            if padded and field in args and args[field] is None]
            if "due" not in ignored_null and "due" in args and args["due"] is None:
                clear_due = _clear_due_field(entity_id, hass)
                if isinstance(clear_due, str):
                    return _tool_error(clear_due), "invalid_request", tool
                changes.update(clear_due)
                cleared["due"] = matched.get("due")
            # Core clears a field on None and would store an empty string as a value.
            if "description" not in ignored_null and "description" in args:
                changes["description"] = description or None
                if not description:
                    cleared["description"] = matched.get("description")
            if not changes:
                padding = (" A null in summary or status reads as a client padding every field, so the null due and "
                           "description here change nothing; omit summary and status to clear them."
                           if ignored_null else "")
                return (_tool_error(f"update needs at least one of summary, status, due or description.{padding}"),
                        "invalid_request", tool)
            service, service_data = "update_item", {"item": _ref(matched), **changes}
    if action == "add" and description:
        service_data["description"] = description
    # What the item should now hold, compared field by field once it is read back.
    expected = {key: value for key, value in service_data.items()
                if key in ("rename", "status", "due_date", "due_datetime", "description")}
    result = await _tool_call_service(
        {"service": {"domain": "todo", "name": service, "data": service_data},
         "targets": [{"kind": "entity", "ids": [entity_id]}]},
        access_key, hass, data, request_id, client_ip, approve_as=approve_as,
    )
    content, outcome, _resource = result
    if outcome != "allowed" or content.get("isError"):
        # A refusal, a failure or a pending approval answers as call_service did.
        return result
    return await _verified(hass, access_key, entity_id, action, target, summary, expected, before,
                           cleared={field: value for field, value in cleared.items() if value},
                           ignored_null=ignored_null)


def _reviewed(item: dict) -> dict:
    """What an approval binds of an item: what its receipt names, and a digest of all the item holds."""
    digest = hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
    return {"uid": item["uid"], "summary": str(item.get("summary") or "")[:_SUMMARY_CHARS],
            "status": item.get("status"), "digest": digest}


def _named(row: dict, reason: str | None = None) -> dict:
    """A reviewed item as a result names it."""
    return {"uid": row["uid"], "summary": row["summary"], **({"reason": reason} if reason else {})}


def _receipt_batch(entity_id: str, rows: list[dict]) -> list[dict]:
    """The leading rows a clear's result can name in full.

    Removal is permanent and the result is the only trail of an unreviewed clear, so an item
    the result could not name waits for the next call. Each row is measured with its reason,
    the longest form it can take.
    """
    def body(count: int) -> dict:
        return {"list": entity_id, "action": "clear_completed", "applied": False, "verified": False,
                "status": "partial", "retry_safe": False, "truncated": True, "removed_count": count,
                "remaining_completed": TODO_ITEMS_MAX_LIMIT, "note": _CLEAR_LEFT_ITEMS,
                "removed": [_named(row, "already_gone") for row in rows[:count]]}

    return rows[:len(longest_fitting(body, len(rows))["removed"])]



async def _clear_completed(
    args: dict, entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str, client_ip: str | None,
) -> tuple[dict, str, str]:
    """Remove the completed items as they read now, by uid, as many as the result can name.

    A remove_item call names each one, so what was reviewed is what goes; an approval runs as
    _execute_set_todo_item.
    """
    # todo.remove_completed_items chooses its items when it runs, which can be long after an
    # approval.
    tool = _TOOL
    if any(args.get(field) not in (None, "") for field in ("item", "summary", "status", "due", "description")):
        return _tool_error("clear_completed takes only the list."), "invalid_request", tool
    try:
        completed = await _items(hass, entity_id, ["completed"])
    except TimeoutError:
        return _tool_error("The list did not answer in time."), "invalid_request", tool
    if not completed:
        return _tool_success(_dumps({"list": entity_id, "action": "clear_completed", "applied": False, "verified": True,
                                     "removed_count": 0, "removed": [], "remaining_completed": 0})), "allowed", entity_id
    chosen = completed[:TODO_ITEMS_MAX_LIMIT]
    if not all(item.get("uid") for item in chosen):
        return _tool_error(
            "This list gives its items no uid, and Home Assistant removes items only by uid, so its completed "
            "items cannot be cleared here; remove them in the list's own app."
        ), "invalid_request", tool
    batch = _receipt_batch(entity_id, [_reviewed(item) for item in chosen])
    result = await _tool_call_service(
        {"service": {"domain": "todo", "name": "remove_item", "data": {"item": [row["uid"] for row in batch]}},
         "targets": [{"kind": "entity", "ids": [entity_id]}]},
        access_key, hass, data, request_id, client_ip,
        approve_as=(tool, {"entity_id": entity_id, "action": "clear_completed", "reviewed": batch}),
    )
    content, outcome, _resource = result
    if outcome != "allowed" or content.get("isError"):
        return result
    return await _cleared(hass, access_key, entity_id, batch)


async def _cleared(
    hass: HomeAssistant, access_key: AccessKeyRecord, entity_id: str, removing: list[dict],
    stayed: list[dict] | None = None,
) -> tuple[dict, str, str]:
    """Read the list back after a clear, naming what went and what stayed."""
    stayed = list(stayed or ())
    body: dict[str, Any] = {"list": entity_id, "action": "clear_completed", "applied": True,
                            "removed_count": len(removing), "removed": [_named(row) for row in removing]}
    try:
        items = await _items(hass, entity_id, list(STATUSES)) if not _unavailable(entity_id, hass) else None
    except Exception:  # the change was applied; only its confirmation failed
        items = None
    if items is None:
        body.update(verified=False, status="unknown", retry_safe=False,
                    note="The list could not be read back to confirm the removal.")
        return _tool_uncertain(_safe_calendar_data(body, access_key, hass)), "invalid_request", entity_id
    held = {item.get("uid") for item in items}
    gone = [row for row in removing if row["uid"] not in held]
    stayed += [_named(row, "still_listed") for row in removing if row["uid"] in held]
    remaining = sum(item.get("status") == "completed" for item in items)
    body.update(removed_count=len(gone), removed=[_named(row) for row in gone], verified=len(gone) == len(removing),
                remaining_completed=remaining, truncated=remaining > 0)
    if stayed:
        body["not_removed"] = stayed
    if len(gone) < len(removing):
        body.update(status="partial", retry_safe=False, note=_CLEAR_LEFT_ITEMS)
        return _tool_uncertain(_safe_calendar_data(body, access_key, hass)), "invalid_request", entity_id
    return _tool_success(_dumps(_safe_calendar_data(body, access_key, hass))), "allowed", entity_id


def _why_kept(row: dict, item: dict | None) -> str | None:
    """Why a reviewed item must not go now, or None while it reads as it was reviewed."""
    if item is None:
        return "already_gone"
    if _reviewed(item)["digest"] == row.get("digest"):
        return None
    return "reopened" if row.get("status") == "completed" and item.get("status") != "completed" else "changed"


@write_boundary
async def _execute_set_todo_item(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Run an approved removal on the reviewed items that still read as they did.

    Core's remove_item takes any uid in any state and refuses the whole call for a missing one,
    so the check is here. Refuses before any write when none is left; scope, target binding and
    MESA are call_service's executor's.
    """
    entity_id, action, reviewed = args.get("entity_id"), args.get("action"), args.get("reviewed")
    if (not isinstance(entity_id, str) or action not in ("remove", "clear_completed")
            or not isinstance(reviewed, list) or not reviewed
            or not all(isinstance(row, dict) and row.get("uid") for row in reviewed)):
        return _tool_refused_before_write("The saved request is not a reviewed todo removal."), "invalid_request", _TOOL
    if not _readable(entity_id, access_key, hass):
        return _tool_refused_before_write(_NOT_FOUND), "not_found", _TOOL
    if _unavailable(entity_id, hass):
        return _tool_refused_before_write("This todo list is unavailable, so nothing was removed."), "invalid_request", _TOOL
    try:
        current = {item.get("uid"): item for item in await _items(hass, entity_id, list(STATUSES))}
    except AuthorityChangedError:
        raise
    except Exception:  # nothing can be checked, so nothing is removed
        return _tool_refused_before_write("The list could not be read, so nothing was removed."), "invalid_request", _TOOL
    reasons = [(row, _why_kept(row, current.get(row["uid"]))) for row in reviewed]
    going = [row for row, reason in reasons if reason is None]
    stayed = [_named(row, reason) for row, reason in reasons if reason is not None]
    if not going:
        body = {"list": entity_id, "action": action, "applied": False, "removed_count": 0, "removed": [],
                "not_removed": stayed, "note": "No item still reads as it was reviewed, so nothing was removed."}
        return (_tool_refused_before_write(_dumps(_safe_calendar_data(body, access_key, hass))),
                "invalid_request", entity_id)
    call: dict[str, Any] = {"domain": "todo", "service": "remove_item", "entity_id": [entity_id],
                            "service_data": {"item": [row["uid"] for row in going]}}
    if APPROVAL_TARGETS_KEY in args:
        call[APPROVAL_TARGETS_KEY] = args[APPROVAL_TARGETS_KEY]
    result = await _execute_call_service(call, access_key, hass, data)
    content, outcome, _resource = result
    if outcome != "allowed" or content.get("isError"):
        return result
    if action == "remove":
        return await _verified(hass, access_key, entity_id, action, current[going[0]["uid"]], None)
    return await _cleared(hass, access_key, entity_id, going, stayed)


def _added(before: list[dict], after: list[dict], summary: Any) -> dict | None:
    """The one item the add created, or None when it cannot be singled out.

    It is the one new uid, since lists may insert at either end. A list without uids matches by text
    only while that text is unique.
    """
    folded = str(summary).strip().casefold()
    if any(item.get("uid") for item in after):
        known = {item.get("uid") for item in before}
        new = [item for item in after if item.get("uid") and item.get("uid") not in known]
        return new[0] if len(new) == 1 else None
    named = [item for item in after if str(item.get("summary") or "").strip().casefold() == folded]
    return named[0] if len(named) == 1 and len(after) == len(before) + 1 else None


def _same_due(read: Any, sent: str) -> bool:
    """A due date or time as the list reports it, against the one sent (local time when it has no offset)."""
    if not isinstance(read, str):
        return False
    if "T" not in sent and " " not in sent.strip():
        return read == sent
    left, right = parse_datetime(read), parse_datetime(sent.replace(" ", "T"))
    if left is None or right is None:
        return False
    return dt_util.as_local(left) == (right if right.tzinfo else right.replace(tzinfo=dt_util.get_default_time_zone()))


def _unapplied(item: dict, expected: dict) -> list[str]:
    """The requested fields the read-back item does not hold."""
    wrong = []
    for key, value in expected.items():
        if key == "rename":
            held = str(item.get("summary") or "").strip() == value
        elif key in ("due_date", "due_datetime"):
            held = not item.get("due") if value is None else _same_due(item.get("due"), value)
        else:
            held = (item.get(key) or None) == (value or None)
        if not held:
            wrong.append("summary" if key == "rename" else "due" if key.startswith("due") else key)
    return wrong


async def _verified(
    hass: HomeAssistant, access_key: AccessKeyRecord, entity_id: str, action: str, target: dict | None, summary: Any,
    expected: dict | None = None, before: list[dict] | None = None,
    *, cleared: dict | None = None, ignored_null: list[str] | None = None,
) -> tuple[dict, str, str]:
    """Read the list back, so the result says what it now holds rather than what was asked.

    A change the read-back cannot confirm is an uncertain write, whatever the service answered.
    """
    body: dict[str, Any] = {"list": entity_id, "action": action}
    if _unavailable(entity_id, hass):
        # Home Assistant answers no items for an unavailable list, which would read as a lost change.
        body.update(applied=None, verified=False, status="unknown", retry_safe=False,
                    note="The list became unavailable, so whether the change took cannot be confirmed.")
        return _tool_uncertain(body), "invalid_request", entity_id
    try:
        items = await _items(hass, entity_id, list(STATUSES))
    except Exception:  # the change was applied; only its confirmation failed
        body.update(applied=True, verified=False, status="unknown", retry_safe=False,
                    note="The change was applied, but the list could not be read back to confirm it.")
        return _tool_uncertain(body), "invalid_request", entity_id
    def named(text: Any) -> list[dict]:
        folded = str(text).strip().casefold()
        return [item for item in items if str(item.get("summary") or "").strip().casefold() == folded]

    if action == "add":
        added = _added(before or [], items, summary)
        body.update(applied=True, verified=added is not None)
        if added is not None:
            body["item"] = added
            if str(added.get("summary") or "").strip().casefold() != str(summary).strip().casefold():
                body.update(verified=False, not_applied=["summary"],
                            note="The list accepted the item, but its text does not read back as requested.")
        else:
            body["note"] = ("The list accepted the item, but the new item could not be told apart from the others, "
                            "so it is not shown; read the list to see it.")
    elif target is not None and target.get("uid"):
        after = next((item for item in items if item.get("uid") == target["uid"]), None)
        verified = after is None if action == "remove" else after is not None
        body.update(applied=True, verified=verified, item=target if action == "remove" else after)
    else:
        renamed = summary if isinstance(summary, str) and summary.strip() else (target or {}).get("summary")
        matches = named(renamed)
        body.update(applied=True, verified=len(matches) == 1, item=matches[0] if len(matches) == 1 else None)
    # Present is not enough: each requested field must read back as sent.
    if action != "remove" and body["verified"] and expected and (wrong := _unapplied(body["item"], expected)):
        body.update(verified=False, not_applied=wrong,
                    note="The list accepted the change, but these fields do not read back as requested.")
    if cleared:
        body["cleared"] = cleared
    if ignored_null:
        body["ignored_null"] = ignored_null
    if not body["verified"]:
        body.update(status="partial", retry_safe=False)
        return _tool_uncertain(_safe_calendar_data(body, access_key, hass)), "invalid_request", entity_id
    return _tool_success(_dumps(_safe_calendar_data(body, access_key, hass))), "allowed", entity_id
