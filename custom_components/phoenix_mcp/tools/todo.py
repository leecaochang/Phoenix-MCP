"""Todo list reads and item changes, scoped like any other entity.

A list is read with READ access to its todo entity, through Home Assistant's own
todo.get_items, so a shopping list needs no service-response capability. Item
changes run as the matching todo service through call_service, so scope, MESA,
approvals and the audit trail are exactly that tool's, and the item is read back
afterwards so the result says what the list now holds.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..access_key_store import AccessKeyRecord
from ..const import TODO_ITEMS_DEFAULT_LIMIT, TODO_ITEMS_MAX_LIMIT, TODO_TIMEOUT_SECONDS
from ..data import PhoenixData
from ..household_authority import household_service_kwargs
from ..policy_engine import Permission, resolve_read
from ..request_authority import AuthorityChangedError, check_request_authority
from ..tool_common import _tool_error, _tool_success, fitted_offset_page, offset_page
from .calendar import _safe_calendar_data

STATUSES = ("needs_action", "completed")
_NOT_FOUND = "Todo list not found."
_UNAVAILABLE = "The list is unavailable, so Home Assistant skips it and returns no items."


def _unavailable(entity_id: str, hass: HomeAssistant) -> bool:
    """Whether the list reads unavailable: Core's entity services then skip it and still answer success.

    A placeholder restored while its integration loads reads unavailable too.
    """
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
        except Exception as exc:  # noqa: BLE001 - one list's failure must not hide the others
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
    from ..mcp_view import _tool_call_service  # noqa: PLC0415 - mcp_view imports this module

    tool = "set_todo_item"
    entity_id = str(args.get("entity_id") or "").strip()
    action = args.get("action")
    if action not in ("add", "update", "remove"):
        return _tool_error("action must be add, update or remove."), "invalid_request", tool
    if not _readable(entity_id, access_key, hass):
        return _tool_error(_NOT_FOUND), "not_found", tool
    if _unavailable(entity_id, hass):
        # Core would skip the list and answer success, so nothing would change; say why before any approval.
        return _tool_error(
            "This todo list is unavailable, so Home Assistant would skip it and change nothing. Check the "
            "list's integration, then try again once the list is available."
        ), "invalid_request", tool
    due = _due_fields(args.get("due"))
    if isinstance(due, str):
        return _tool_error(due), "invalid_request", tool
    summary = args.get("summary")
    description = args.get("description")
    status = args.get("status")
    if status is not None and status not in STATUSES:
        return _tool_error("status must be needs_action or completed."), "invalid_request", tool
    target: dict | None = None
    before: list[dict] = []
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
        else:
            changes = {**({"rename": summary.strip()} if isinstance(summary, str) and summary.strip() else {}),
                       **({"status": status} if status else {}), **due}
            if description is not None:
                changes["description"] = description
            if not changes:
                return _tool_error("update needs at least one of summary, status, due or description."), "invalid_request", tool
            service, service_data = "update_item", {"item": _ref(matched), **changes}
    if action == "add" and description is not None:
        service_data["description"] = description
    # What the item should now hold, compared field by field once it is read back.
    expected = {key: value for key, value in service_data.items()
                if key in ("rename", "status", "due_date", "due_datetime", "description")}
    result = await _tool_call_service(
        {"service": {"domain": "todo", "name": service, "data": service_data},
         "targets": [{"kind": "entity", "ids": [entity_id]}]},
        access_key, hass, data, request_id, client_ip,
    )
    content, outcome, _resource = result
    if outcome != "allowed" or content.get("isError"):
        # A refusal, a failure or a pending approval answers as call_service did.
        return result
    return await _verified(hass, access_key, entity_id, action, target, summary, expected, before)


def _added(before: list[dict], after: list[dict], summary: Any) -> dict | None:
    """The one item the add created, or None when it cannot be singled out.

    Lists may insert at the top or the bottom, so the new item is the one uid
    that was not there before, never the first or last with the same text. A
    list that gives no uids is matched by text only while that text is unique.
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
    left, right = dt_util.parse_datetime(read), dt_util.parse_datetime(sent.replace(" ", "T"))
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
            held = _same_due(item.get("due"), value)
        else:
            held = (item.get(key) or None) == (value or None)
        if not held:
            wrong.append("summary" if key == "rename" else "due" if key.startswith("due") else key)
    return wrong


async def _verified(
    hass: HomeAssistant, access_key: AccessKeyRecord, entity_id: str, action: str, target: dict | None, summary: Any,
    expected: dict | None = None, before: list[dict] | None = None,
) -> tuple[dict, str, str]:
    """Read the list back, so the result says what it now holds rather than what was asked."""
    body: dict[str, Any] = {"list": entity_id, "action": action}
    if _unavailable(entity_id, hass):
        # Home Assistant answers no items for an unavailable list, which would read as a lost change.
        body.update(applied=None, verified=False,
                    note="The list became unavailable, so whether the change took cannot be confirmed.")
        return _tool_success(_dumps(body)), "allowed", entity_id
    try:
        items = await _items(hass, entity_id, list(STATUSES))
    except Exception:  # noqa: BLE001 - the change was applied; only its confirmation failed
        body.update(applied=True, verified=False,
                    note="The change was applied, but the list could not be read back to confirm it.")
        return _tool_success(_dumps(body)), "allowed", entity_id
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
    return _tool_success(_dumps(_safe_calendar_data(body, access_key, hass))), "allowed", entity_id
