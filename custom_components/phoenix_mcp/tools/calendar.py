"""Scoped calendar authoring with exact approvals and durable recovery receipts."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
import json
import logging
from typing import Any
import uuid

import voluptuous as vol
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..approval_targets import bind_targets, target_binding, targets_current
from ..const import (
    DOMAIN, CAP_DENY, CALENDAR_MAX_EVENTS, CALENDAR_MAX_WINDOW_DAYS,
    CALENDAR_RECEIPT_LIMIT, CALENDAR_TIMEOUT_SECONDS, CALENDAR_EVENT_MAX_BYTES,
    CALENDAR_RESULT_MAX_BYTES,
)
from ..data import PhoenixData
from ..durable_storage import DurableStore
from ..helpers import (
    content_hash, effective_cap, diff_summary_fields, redact_inaccessible_entity_ids_in_text,
    redact_structure,
)
from ..mesa import async_apply_mesa_to_call
from ..policy_engine import Permission, resolve, resolve_read
from ..request_authority import AuthorityChangedError, check_request_authority, dispatch_request_id, mark_dispatch_started
from ..access_key_store import AccessKeyRecord
from ..tool_common import (
    _approved_exec_ctx, _gate, _pending_or_inline, _tool_error, _tool_success,
    redaction_sentinel_path, _tool_pending,
)

_LOGGER = logging.getLogger(__name__)
_FIELDS = frozenset({"summary", "start", "end", "description", "location", "rrule"})


def _journal_row_problem(row: Any, seen: set[str]) -> str | None:
    """Name the first field that makes a stored journal record unusable, or None."""
    if not isinstance(row, dict):
        return "is not an object"
    if not isinstance(row.get("receipt_id"), str) or not row["receipt_id"] or row["receipt_id"] in seen:
        return "has a missing or repeated receipt_id"
    if row.get("status") not in {"outcome_unknown", "applied", "reviewed", "not_applied"}:
        return "has an unknown status"
    for key in ("entity_id", "access_key_id", "created_at", "recovery"):
        if not isinstance(row.get(key), str) or not row[key]:
            return f"has no {key}"
    if row.get("operation") not in {"create", "update", "delete"}:
        return "has an unknown operation"
    for key in ("identity", "requested"):
        if not isinstance(row.get(key), dict):
            return f"has no {key}"
    for key in ("before", "after"):
        if row.get(key) is not None and not isinstance(row[key], dict):
            return f"has an invalid {key}"
    return None


class CalendarWriteJournal:
    """Save intent before dispatch; an interrupted record remains outcome_unknown."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.active: set[str] = set()
        self.store: Store[dict] = DurableStore(hass, 1, "phoenix_mcp_calendar_writes", private=True, atomic_writes=True, serialize_in_event_loop=False)
        self.lock = asyncio.Lock()
        self.records: list[dict] | None = None
        self.unavailable = False

    async def load(self) -> list[dict]:
        """Load under the caller-held journal lock, without reinterpreting intent."""
        if self.records is None or self.unavailable:
            try:
                value = await self.store.async_load()
                records = [] if value is None else value["records"]
                if not isinstance(records, list) or len(records) > CALENDAR_RECEIPT_LIMIT:
                    raise ValueError("the record list is missing or over its limit")
                seen: set[str] = set()
                for index, row in enumerate(records):
                    if reason := _journal_row_problem(row, seen):
                        raise ValueError(f"record {index} {reason}")
                    seen.add(row["receipt_id"])
                self.records = records
                self.unavailable = False
            except Exception as err:
                if not self.unavailable:
                    # The attention panel sends the operator here; say what failed,
                    # never the stored content.
                    _LOGGER.warning("Calendar write history in .storage/%s could not be read (%s); "
                                    "calendar writes are paused", self.store.key,
                                    err if isinstance(err, ValueError) and str(err) else type(err).__name__)
                self.unavailable = True
                raise ValueError("Calendar recovery storage is unavailable; no write was dispatched.") from None
        return self.records

    async def save(self) -> None:
        """Persist snapshots before acknowledging a transition."""
        if self.unavailable or self.records is None:
            raise ValueError("Calendar recovery storage is unavailable; no write was dispatched.")
        await self.store.async_save({"records": self.records})


    async def reviewed(self, rid: str) -> bool:
        """Acknowledge manual reconciliation without replay, rollback, or eviction."""
        if rid in self.active:
            return False
        async with self.lock:
            rows = await self.load()
            row = next((r for r in rows if r.get("receipt_id") == rid), None)
            if row is None or row.get("status") != "outcome_unknown" or rid in self.active:
                return False
            original = dict(row)
            row.update(status="reviewed", reviewed_at=dt_util.utcnow().isoformat())
            try:
                await self.save()
            except BaseException:
                row.clear()
                row.update(original)
                raise
            from homeassistant.helpers import issue_registry as ir
            ir.async_delete_issue(self.hass, DOMAIN, f"calendar_recovery_{rid}")
            return True


def _calendar_journal(data: PhoenixData, hass: HomeAssistant) -> CalendarWriteJournal:
    if data.calendar_writes is None:
        data.calendar_writes = CalendarWriteJournal(hass)
    return data.calendar_writes


def _safe_calendar_data(value: Any, access_key: AccessKeyRecord, hass: HomeAssistant) -> Any:
    value = redact_structure(value)
    if isinstance(value, str):
        return redact_inaccessible_entity_ids_in_text(value, access_key, hass)
    if isinstance(value, list):
        return [_safe_calendar_data(item, access_key, hass) for item in value]
    if isinstance(value, dict):
        return {key: _safe_calendar_data(item, access_key, hass) for key, item in value.items()}
    return value


def _calendar_entity(entity_id: Any, access_key: AccessKeyRecord, hass: HomeAssistant, *, write: bool = False) -> Any:
    """Authorize before inspecting the integration or its feature flags."""
    if not isinstance(entity_id, str) or not valid_entity_id(entity_id):
        raise ValueError("entity_id must be a calendar entity ID.")
    # Reads also honor the pass-through Assist-exposure filter; a write addresses
    # an explicit target exactly as call_service does.
    permission = resolve(entity_id, access_key, hass) if write else resolve_read(entity_id, access_key, hass)
    allowed = (Permission.WRITE,) if write else (Permission.READ, Permission.WRITE)
    if permission not in allowed:
        raise PermissionError("Calendar not found.")
    if not entity_id.startswith("calendar."):
        raise ValueError("entity_id must be a calendar entity ID.")
    from homeassistant.components.calendar import CalendarEntity, DATA_COMPONENT

    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component is not None else None
    if not isinstance(entity, CalendarEntity):
        raise PermissionError("Calendar not found.")
    return entity


def _calendar_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Times must be ISO dates or timestamps with a timezone.")
    if len(value) == 10 and (day := dt_util.parse_date(value)) is not None:
        return dt_util.start_of_local_day(datetime.combine(day, datetime.min.time()))
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("Times must be ISO dates or timestamps with a timezone.")
    return dt_util.as_local(parsed)


def _calendar_window(args: dict, *, defaults: bool = False) -> tuple[datetime, datetime]:
    start = _calendar_time(args["start_time"]) if "start_time" in args else dt_util.now() if defaults else None
    end = _calendar_time(args["end_time"]) if "end_time" in args else start + timedelta(days=7) if defaults and start else None
    if start is None or end is None:
        raise ValueError("start_time and end_time are required to locate the event.")
    if not timedelta(0) < end - start <= timedelta(days=CALENDAR_MAX_WINDOW_DAYS):
        raise ValueError(f"Event windows must be positive and at most {CALENDAR_MAX_WINDOW_DAYS} days.")
    return start, end


async def _calendar_events(entity: Any, hass: HomeAssistant, start: datetime, end: datetime) -> list[dict]:
    async with asyncio.timeout(CALENDAR_TIMEOUT_SECONDS):
        events = await entity.async_get_events(hass, start, end)
    check_request_authority()
    if len(events) > CALENDAR_MAX_EVENTS:
        raise ValueError("Too many calendar events. Use a shorter window.")
    rows = [event.as_dict() for event in events]
    if (any(len(json.dumps(row).encode()) > CALENDAR_EVENT_MAX_BYTES for row in rows)
            or len(json.dumps(rows).encode()) > CALENDAR_RESULT_MAX_BYTES):
        raise ValueError("Calendar events exceed the readback budget. Use a shorter window or smaller events.")
    return rows


def _calendar_event_payload(value: Any) -> dict:
    """Check shapes before using HA's native event schema and recurrence validator."""
    if not isinstance(value, dict) or set(value) - _FIELDS:
        raise ValueError("event accepts only summary, start, end, description, location and rrule.")
    if redaction_sentinel_path(value) is not None:
        raise ValueError("Redacted event content cannot be written back.")
    if any(not isinstance(item, str) for item in value.values()):
        raise ValueError("Event fields must be strings.")
    if len(json.dumps(value).encode()) > CALENDAR_EVENT_MAX_BYTES:
        raise ValueError("Calendar event exceeds the write budget.")
    if not all(value.get(key) for key in ("summary", "start", "end")):
        raise ValueError("summary, start and end are required and must be non-empty.")
    _calendar_time(value["start"])
    _calendar_time(value["end"])
    from homeassistant.components.calendar import EVENT_START, EVENT_END, WEBSOCKET_EVENT_SCHEMA

    try:
        native = {EVENT_START if key == "start" else EVENT_END if key == "end" else key: item
                  for key, item in value.items()}
        return WEBSOCKET_EVENT_SCHEMA(native)
    except (vol.Invalid, ValueError, TypeError, OverflowError):
        raise ValueError("Invalid calendar event dates, duration, timezone or recurrence rule.") from None


def _calendar_serialized(event: dict) -> dict:
    from homeassistant.components.calendar import EVENT_START, EVENT_END

    return {"start" if key == EVENT_START else "end" if key == EVENT_END else key:
            value.isoformat() if hasattr(value, "isoformat") else value for key, value in event.items()}


async def _calendar_write_plan(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, operation: str) -> dict:
    entity_id = args.get("entity_id")
    entity = _calendar_entity(entity_id, access_key, hass, write=True)
    from homeassistant.components.calendar import CalendarEntityFeature

    feature = getattr(CalendarEntityFeature, f"{operation.upper()}_EVENT", 0)
    if not feature or not entity.supported_features or not entity.supported_features & feature:
        raise ValueError(f"This calendar does not support event {operation}.")
    plan = {"entity_id": entity_id, "operation": operation, "before": None}
    if operation == "create":
        if any(key in args for key in ("uid", "recurrence_id", "recurrence_range", "mode", "expected_hash")):
            raise ValueError("Creation cannot specify an existing event or recurrence selector.")
        event = _calendar_event_payload(args.get("event"))
        start, end = _calendar_time(_calendar_serialized(event)["start"]), _calendar_time(_calendar_serialized(event)["end"])
        # Creation readback needs only the first occurrence, not an unbounded series.
        end = min(end, start + timedelta(days=CALENDAR_MAX_WINDOW_DAYS))
    else:
        uid = args.get("uid")
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError("uid must be a non-empty string from get_calendar_events.")
        recurrence_id = args.get("recurrence_id")
        if "recurrence_id" in args and (not isinstance(recurrence_id, str) or not recurrence_id.strip()):
            raise ValueError("recurrence_id must be a non-empty occurrence ID.")
        if "recurrence_range" in args and (args["recurrence_range"] != "THISANDFUTURE" or not recurrence_id):
            raise ValueError("recurrence_range requires recurrence_id and must be THISANDFUTURE.")
        start, end = _calendar_window(args)
        rows = await _calendar_events(entity, hass, start, end)
        matches = [row for row in rows if row.get("uid") == uid and (not recurrence_id or row.get("recurrence_id") == recurrence_id)]
        if not matches:
            raise ValueError("Event not found in this window. Read its current UID and occurrence again.")
        before = matches[0]
        if args.get("recurrence_range") and not before.get("rrule"):
            raise ValueError("THISANDFUTURE requires an occurrence with a visible rrule. An edited exception cannot safely identify the series.")
        if not recurrence_id and (before.get("recurrence_id") or before.get("rrule")):
            raise ValueError("Recurring writes require an explicit recurrence_id; use THISANDFUTURE for later occurrences.")
        if len(matches) != 1:
            raise ValueError("Event selection is ambiguous. Specify the exact recurrence_id.")
        if args.get("expected_hash") != content_hash(before):
            raise ValueError("Calendar event changed. Read it again and use its content_hash.")
        plan.update({key: args[key] for key in ("uid", "recurrence_id", "recurrence_range") if key in args})
        plan["before"] = before
        if operation == "update":
            mode = args.get("mode")
            if mode not in ("replace", "patch"):
                raise ValueError("mode must be replace or patch.")
            value = args.get("event")
            if mode == "patch":
                if not isinstance(value, dict) or not value or set(value) - _FIELDS:
                    raise ValueError("A patch requires one or more supported event fields.")
                if redaction_sentinel_path(value) is not None:
                    raise ValueError("Redacted event content cannot be written back.")
                merged = {key: item for key, item in before.items() if key in _FIELDS}
                for key, item in value.items():
                    if item is None and key in ("description", "location"):
                        merged[key] = ""
                    elif item is None and key == "rrule":
                        raise ValueError("Home Assistant has no validated recurrence-rule clear value. Supply an explicit supported rrule instead.")
                    else:
                        merged[key] = item
                value = merged
            if isinstance(value, dict):
                value = dict(value)
                if before.get("rrule") and not value.get("rrule"):
                    raise ValueError("Home Assistant has no validated recurrence-rule clear value. A replacement of a recurring event must include an explicit rrule.")
                # HA integrations can merge omitted strings; explicit empty strings clear them.
                value.setdefault("description", "")
                value.setdefault("location", "")
            event = _calendar_event_payload(value)
        else:
            if "event" in args or "mode" in args:
                raise ValueError("Deletion accepts no event payload or update mode.")
            event = None
    rows = await _calendar_events(entity, hass, start, end) if operation == "create" else rows
    check_request_authority()
    _calendar_entity(entity_id, access_key, hass, write=True)
    plan.update({"event": _calendar_serialized(event) if event is not None else None,
                 "start_time": start.isoformat(), "end_time": end.isoformat(),
                 "existing_ids": [[row.get("uid"), row.get("recurrence_id")] for row in rows] if operation == "create" else None})
    return plan


async def _tool_calendar_write(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
                               request_id: str, client_ip: str | None, *, operation: str, tool_name: str) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_calendar_write") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", tool_name
    try:
        plan = await _calendar_write_plan(args, access_key, hass, operation)
        saved = bind_targets({"_calendar_plan": plan}, [plan["entity_id"]], hass)
        gated = await _gate("cap_calendar_write", access_key, hass, data, tool_name=tool_name,
                            args=saved, request_id=request_id, client_ip=client_ip,
                            diff=lambda: _calendar_diff(plan, access_key, hass))
        if gated is not None:
            return gated
        return await _execute_calendar_write(saved, access_key, hass, data, operation=operation, tool_name=tool_name)
    except AuthorityChangedError:
        raise
    except PermissionError:
        return _tool_error("Calendar not found."), "denied", tool_name
    except ValueError as exc:
        return _tool_error(_safe_calendar_data(str(exc), access_key, hass)), "invalid_request", tool_name
    except Exception:
        _LOGGER.exception("Calendar precheck failed")
        return _tool_error("Calendar event validation or read failed. No write was dispatched."), "invalid_request", tool_name


def _calendar_diff(plan: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    if plan["operation"] == "create":
        summary = diff_summary_fields("create_calendar_event")
    elif plan["operation"] == "update":
        summary = diff_summary_fields("update_calendar_event")
    else:
        summary = diff_summary_fields("delete_calendar_event")
    return {"kind": "calendar_event", **summary,
            "before": _safe_calendar_data(plan["before"], access_key, hass), "after": _safe_calendar_data(plan["event"], access_key, hass),
            "preview": {"target_semantics": "exact", "resolved_entity_ids": [plan["entity_id"]],
                        "uid": plan.get("uid"), "recurrence_id": plan.get("recurrence_id"),
                        "recurrence_range": plan.get("recurrence_range"),
                        "recovery": "Occurrence snapshot only; no automatic rollback or complete series restoration."}}


async def _execute_calendar_write(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
                                  *, operation: str, tool_name: str) -> tuple[dict, str, str]:
    if effective_cap(access_key, "cap_calendar_write") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", tool_name
    plan = args.get("_calendar_plan")
    if not isinstance(plan, dict) or plan.get("operation") != operation:
        return _tool_error("Calendar write binding missing. Submit a new request."), "denied", tool_name
    entity_id = plan["entity_id"]
    journal = _calendar_journal(data, hass)
    check_request_authority()
    try:
        _calendar_entity(entity_id, access_key, hass, write=True)
        if not targets_current(args, [entity_id], hass):
            raise PermissionError
    except (PermissionError, ValueError):
        return _tool_error("Calendar not found or its identity changed."), "denied", entity_id
    mesa = await async_apply_mesa_to_call(
        hass, data, access_key, domain="calendar", service=f"{operation}_event",
        service_data=plan["event"] or {}, entities=[entity_id], request_id=dispatch_request_id.get(), client_ip=None,
        session_id=access_key.id, confirm_approved=_approved_exec_ctx.get(),
        approval_tool_name=tool_name, approval_args=args,
        approval_diff=_calendar_diff(plan, access_key, hass), require_all=True,
    )
    if mesa.decision == "pending":
        return await _pending_or_inline(hass, data, access_key, mesa.approval)
    if mesa.decision != "allow":
        return _tool_error("Forbidden."), "denied", entity_id
    async with journal.lock:
        receipt = None
        ready_for_dispatch = False
        try:
            check_request_authority()
            entity = _calendar_entity(entity_id, access_key, hass, write=True)
            if not targets_current(args, [entity_id], hass):
                raise PermissionError
            public = {key: plan[key] for key in ("entity_id", "uid", "recurrence_id", "recurrence_range", "start_time", "end_time") if key in plan}
            public.update({"expected_hash": content_hash(plan["before"]), "mode": "replace", "event": plan["event"]} if operation == "update" else {"event": plan["event"]} if operation == "create" else {"expected_hash": content_hash(plan["before"])})
            fresh = await _calendar_write_plan(public, access_key, hass, operation)
            if fresh != plan:
                raise ValueError("Calendar event or readback context changed. Submit a new request.")
            records = await journal.load()
            while len(records) >= CALENDAR_RECEIPT_LIMIT:
                removable = next((row for row in records if row.get("status") in {"applied", "reviewed", "not_applied"}), None)
                if removable is None:
                    raise ValueError("Calendar recovery storage is full. No write was dispatched.")
                records.remove(removable)
            receipt = {"receipt_id": uuid.uuid4().hex, "access_key_id": access_key.id,
                       "identity": target_binding([entity_id], hass), "entity_id": entity_id,
                       "status": "outcome_unknown", "created_at": dt_util.utcnow().isoformat(),
                       "operation": operation, "before": copy.deepcopy(plan["before"]),
                       "requested": copy.deepcopy(plan), "after": None,
                       "recovery": "No automatic retry or rollback. Snapshot covers one occurrence; deleted UIDs and whole series cannot be restored exactly."}
            records.append(receipt)
            await journal.save()
            check_request_authority()
            _calendar_entity(entity_id, access_key, hass, write=True)
            if effective_cap(access_key, "cap_calendar_write") == CAP_DENY:
                raise PermissionError
            if not targets_current(args, [entity_id], hass):
                raise PermissionError
            if await _calendar_write_plan(public, access_key, hass, operation) != plan:
                raise ValueError("Calendar event changed before dispatch. Submit a new request.")
            mesa = await async_apply_mesa_to_call(
                hass, data, access_key, domain="calendar", service=f"{operation}_event",
                service_data=plan["event"] or {}, entities=[entity_id], request_id=dispatch_request_id.get(), client_ip=None,
                session_id=access_key.id, confirm_approved=_approved_exec_ctx.get(),
                approval_tool_name=tool_name, approval_args=args,
                approval_diff=_calendar_diff(plan, access_key, hass), require_all=True,
            )
            if mesa.decision == "pending" and mesa.approval is not None:
                # Waiting inline while holding the journal lock would deadlock replay.
                return _tool_pending(mesa.approval), "pending_approval", mesa.approval.id
            if mesa.decision != "allow":
                raise PermissionError
            check_request_authority()
            entity = _calendar_entity(entity_id, access_key, hass, write=True)
            if effective_cap(access_key, "cap_calendar_write") == CAP_DENY or not targets_current(args, [entity_id], hass):
                raise PermissionError
            ready_for_dispatch = True
        except AuthorityChangedError:
            raise
        except PermissionError:
            return _tool_error("Calendar not found or its identity changed."), "denied", entity_id
        except ValueError as exc:
            return _tool_error(_safe_calendar_data(str(exc), access_key, hass)), "invalid_request", entity_id
        except Exception:
            _LOGGER.exception("Calendar write preparation failed")
            return _tool_error("Calendar write preparation failed. No write was dispatched."), "invalid_request", entity_id
        finally:
            if receipt is not None and not ready_for_dispatch:
                receipt["status"] = "not_applied"
                try:
                    await journal.save()
                except asyncio.CancelledError:
                    receipt["status"] = "outcome_unknown"
                    raise
                except Exception:
                    receipt["status"] = "outcome_unknown"
                    _LOGGER.error("Could not persist the undispatched calendar receipt")
        journal.active.add(receipt["receipt_id"])
        try:
            async with asyncio.timeout(CALENDAR_TIMEOUT_SECONDS):
                mark_dispatch_started()
                if operation == "create":
                    await entity.async_create_event(**_calendar_event_payload(plan["event"]))
                else:
                    selectors = {key: plan[key] for key in ("recurrence_id", "recurrence_range") if key in plan}
                    if operation == "update":
                        await entity.async_update_event(plan["uid"], _calendar_event_payload(plan["event"]), **selectors)
                    else:
                        await entity.async_delete_event(plan["uid"], **selectors)
            receipt["status"] = "applied"
            await journal.save()
            check_request_authority()
            read_start, read_end = _calendar_time(plan["start_time"]), _calendar_time(plan["end_time"])
            if operation == "update":
                read_start = _calendar_time(plan["event"]["start"])
                read_end = min(_calendar_time(plan["event"]["end"]), read_start + timedelta(days=CALENDAR_MAX_WINDOW_DAYS))
            rows = await _calendar_events(entity, hass, read_start, read_end)
            _calendar_entity(entity_id, access_key, hass, write=True)
            if operation == "create":
                candidates = [row for row in rows if row.get("uid") and [row.get("uid"), row.get("recurrence_id")] not in plan["existing_ids"]
                              and all(row.get(key) == value for key, value in plan["event"].items())]
            else:
                candidates = [row for row in rows if row.get("uid") == plan["uid"] and row.get("recurrence_id") == plan.get("recurrence_id")]
            receipt["after"] = candidates[0] if len(candidates) == 1 else None
            await journal.save()
        except AuthorityChangedError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Calendar dispatch or readback failed")
            # A saved applied receipt distinguishes a readback failure from uncertain dispatch.
            return _calendar_result(receipt, access_key, hass, error=True)
        finally:
            journal.active.discard(receipt["receipt_id"])
        return _calendar_result(receipt, access_key, hass)


def _calendar_result(receipt: dict, access_key: AccessKeyRecord, hass: HomeAssistant, *, error: bool = False) -> tuple[dict, str, str]:
    check_request_authority()
    _calendar_entity(receipt["entity_id"], access_key, hass)
    body = {key: value for key, value in receipt.items() if key not in ("access_key_id", "identity")}
    if receipt["after"] is not None:
        body["after"] = {**receipt["after"], "content_hash": content_hash(receipt["after"])}
    result = _tool_success(json.dumps(_safe_calendar_data(body, access_key, hass)))
    if error:
        result["isError"] = True
    return result, "invalid_request" if error else "allowed", receipt["entity_id"]


async def _execute_create_calendar_event(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData) -> tuple[dict, str, str]:
    return await _execute_calendar_write(args, access_key, hass, data, operation="create", tool_name="create_calendar_event")


async def _execute_update_calendar_event(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData) -> tuple[dict, str, str]:
    return await _execute_calendar_write(args, access_key, hass, data, operation="update", tool_name="update_calendar_event")


async def _execute_delete_calendar_event(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData) -> tuple[dict, str, str]:
    return await _execute_calendar_write(args, access_key, hass, data, operation="delete", tool_name="delete_calendar_event")


async def _tool_get_calendar_write(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData) -> tuple[dict, str, str]:
    try:
        _calendar_entity(args.get("entity_id"), access_key, hass)
    except (PermissionError, ValueError):
        return _tool_error("Calendar write not found."), "not_found", "get_calendar_write"
    journal = _calendar_journal(data, hass)
    async with journal.lock:
        records = await journal.load()
        check_request_authority()
        visible = [row for row in records if row["entity_id"] == args["entity_id"] and row["access_key_id"] == access_key.id
                   and row["identity"] == target_binding([args["entity_id"]], hass)]
        if "receipt_id" not in args:
            check_request_authority()
            _calendar_entity(args["entity_id"], access_key, hass)
            rows = [{key: row[key] for key in ("receipt_id", "operation", "status", "created_at")} for row in visible]
            return _tool_success(json.dumps({"writes": rows})), "allowed", args["entity_id"]
        receipt = next((row for row in visible if row["receipt_id"] == args.get("receipt_id")), None)
        if receipt is None:
            return _tool_error("Calendar write not found."), "not_found", "get_calendar_write"
        try:
            _calendar_entity(receipt["entity_id"], access_key, hass)
            if receipt["identity"] != target_binding([receipt["entity_id"]], hass):
                raise PermissionError
        except (PermissionError, ValueError):
            return _tool_error("Calendar write not found."), "not_found", "get_calendar_write"
        return _calendar_result(receipt, access_key, hass)
