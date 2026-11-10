"""State, history and statistics reads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import functools
import json
import logging
import time
from typing import Any, cast, Literal

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import utcnow

from ..access_key_store import AccessKeyRecord
from ..const import (
    CAP_DENY, DOMAIN, GET_STATE_MAX_IDS, HISTORY_SUMMARY_BATCH_SECONDS,
    HISTORY_SUMMARY_MANY_MAX_STATES, HISTORY_SUMMARY_MAX_ROWS, HISTORY_SUMMARY_MAX_SECONDS,
    HISTORY_SUMMARY_MAX_STATES, HISTORY_SUMMARY_PAGE_ROWS, SEARCH_ENTITIES_MAX_LIMIT,
    TOOL_RESULT_MAX_BYTES,
)
from ..conversation_log import json_size
from ..data import PhoenixData
from ..tool_input import str_arg, str_list_arg
from ..capability import effective_cap
from ..history_summary import (
    attribute_page, attribute_rows, compact_summary_row, read_attribute_changes, read_state_change_page,
    scan_state_changes, summary_body, SummaryUnavailableError,
)
from ..policy_engine import (
    filter_entities_for_access_key, Permission, resolve_read, scrub_attributes, scrub_sensitive_attributes,
    scrub_state_dict as _scrub_state_dict,
)
from ..recorder_queries import (
    backward_page_start, ceil_us, floor_us, forward_page_end, from_us, iso_utc,
    MAX_SIGNIFICANT_STATES_RANGE, newest_page, parse_recorder_window, read_statistic_window,
    read_statistics_page, recorder_envelope, RecorderWindow, retention_metadata,
    retention_warnings, row_timestamp, state_timestamp, statistic_page, STATISTIC_PERIOD_LIMITS,
    statistic_timestamp, to_us,
)
from ..request_authority import check_request_authority, request_authority
from ..tool_common import _project_state, _tool_error, _tool_success, longest_fitting
from ..tool_contracts import normalize_tool_args
from ..tool_input import note_argument_adjustment
from .discovery import _tool_search_entities, _area_name_for_entity
from ..component_guard import require_component


_LOGGER = logging.getLogger(__name__)


def _readable_state(entity_id: Any, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[Any, Permission]:
    """Return (state, permission) for one requested ID, or (None, ...) when unreadable."""
    perm = resolve_read(entity_id, access_key, hass)
    if perm not in (Permission.READ, Permission.WRITE):
        return None, perm
    return hass.states.get(entity_id), perm


async def _tool_get_state(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: one entity's state, or several in request order."""
    args, error = normalize_tool_args("get_state", args)
    if error:
        return _tool_error(error), "invalid_request", "get_state"
    requested = args.get("entity_id")
    fields, detailed = args.get("fields"), bool(args.get("detailed"))
    if isinstance(requested, list):
        if not 1 <= len(requested) <= GET_STATE_MAX_IDS or not all(isinstance(item, str) for item in requested):
            return _tool_error(f"entity_id must be a string or a list of 1 to {GET_STATE_MAX_IDS} entity ID strings."), "invalid_request", "get_state"
        rows: list[dict] = []
        not_found: list[str] = []
        for entity_id in dict.fromkeys(item.strip() for item in requested):
            state, _ = _readable_state(entity_id, access_key, hass)
            if state is None:
                not_found.append(entity_id)
            else:
                rows.append(_project_state(scrub_sensitive_attributes(state, access_key, hass), fields, detailed))
        body = {"count": len(rows), "states": rows, "not_found": not_found}
        return _tool_success(json.dumps(body, default=str)), "allowed", f"entities:{len(rows)}"
    entity_id = str_arg(requested)
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "get_state"
    state, perm = _readable_state(entity_id, access_key, hass)
    if state is None:
        # Audit-only distinction; the reply is identical for missing and hidden.
        outcome = "denied" if perm in (Permission.NO_ACCESS, Permission.DENY) else "not_found"
        return _tool_error("Entity not found."), outcome, entity_id
    result = _project_state(scrub_sensitive_attributes(state, access_key, hass), fields, detailed)
    return _tool_success(json.dumps(result, default=str)), "allowed", entity_id


async def _tool_get_history(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData | None = None
) -> tuple[dict, str, str]:
    """MCP tool: fetch one bounded page of state history, or summarize several entities."""
    args = _without_empty_placeholder(args)
    several = "select" in args or isinstance(args.get("entity_id"), list)
    mode = str(args.get("mode") or "").strip().lower()
    if several and mode in ("state_changes", "significant_states") and "select" not in args and len(args["entity_id"]) == 1:
        note_argument_adjustment("Read a one-item entity_id list as that entity.")
        args = {**args, "entity_id": args["entity_id"][0]}
    elif several:
        if not mode:
            note_argument_adjustment("Several entities are summarized, so mode summary was used; call one entity for rows.")
            args = {**args, "mode": "summary"}
        ignored = [key for key in ("limit", "order", "cursor") if key in args]
        if ignored and mode in ("", "summary"):
            note_argument_adjustment(f"Ignored {', '.join(ignored)}, which do not apply to a summary of several entities; "
                                     "continue an entity that returned next_cursor in a single-entity call.")
            args = {key: value for key, value in args.items() if key not in ignored}
    if "select" in args:
        return await _history_summary_select(args["select"], args, access_key, hass, data)
    if isinstance(args.get("entity_id"), list) and mode == "state_changes":
        return await _history_rows_many(args["entity_id"], args, access_key, hass)
    if isinstance(args.get("entity_id"), list):
        return await _history_summary_many(args["entity_id"], args, access_key, hass)
    return await _fit_recorder_page(lambda page_args: _history_page(page_args, access_key, hass), args, "history")


async def _fit_recorder_page(
    read: Callable[[dict], Awaitable[tuple[dict, str, str]]], args: dict, rows_key: str,
) -> tuple[dict, str, str]:
    """A Recorder page read again with a smaller limit until it fits TOOL_RESULT_MAX_BYTES.

    Each reader ends a page where its cursor continues exactly, so re-reading keeps continuation
    exact where cutting rows would not. The model is told the page shrank.
    """
    result = await read(args)
    shortened = False
    for _attempt in range(5):
        content, outcome, _resource = result
        text = "" if content.get("isError") else content["content"][0]["text"]
        # A shortened page leaves room for the warning that says so.
        budget = TOOL_RESULT_MAX_BYTES - (256 if shortened else 0)
        if outcome != "allowed" or not text.startswith("{") or json_size(text) <= budget:
            break
        rows = len(json.loads(text).get(rows_key) or [])
        if rows <= 1:
            break
        args = {**args, "limit": max(1, min(rows - 1, int(rows * budget * 0.9 / json_size(text))))}
        result = await read(args)
        shortened = True
    if not shortened or result[1] != "allowed" or result[0].get("isError"):
        return result
    body = json.loads(result[0]["content"][0]["text"])
    body["warnings"] = [*body.get("warnings", []),
                        f"This page was shortened to {body.get('count')} rows to fit one result; continue with next_cursor."]
    return _tool_success(json.dumps(body, default=str)), result[1], result[2]


async def _history_page(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[dict, str, str]:
    """One bounded page of one entity's state history, or its summary."""
    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "get_history"

    perm = resolve_read(entity_id, access_key, hass)
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    if perm in (Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id

    mode = str(args.get("mode") or "state_changes").strip().lower()
    if mode not in ("state_changes", "significant_states", "summary"):
        return _tool_error(
            "Invalid mode. Must be one of: state_changes, significant_states, summary."
        ), "invalid_request", entity_id
    if mode == "summary" and ("limit" in args or "order" in args):
        ignored = [key for key in ("limit", "order") if key in args]
        note_argument_adjustment(f"Ignored {', '.join(ignored)}, which do not apply to summary mode's per-state totals.")
        args = {key: value for key, value in args.items() if key not in ignored}
    if args.get("attribute") and mode != "summary":
        return _tool_error(
            "attribute is accepted only with mode summary, which totals how long the attribute held each value."
        ), "invalid_request", entity_id
    order = str(args.get("order") or "oldest_first").strip().lower()
    if order not in ("oldest_first", "newest_first"):
        return _tool_error(
            "Invalid order. Must be one of: oldest_first, newest_first."
        ), "invalid_request", entity_id
    try:
        window = parse_recorder_window(args, default_range=timedelta(hours=24))
    except ValueError as err:
        return _tool_error(str(err)), "invalid_request", entity_id
    if mode == "summary":
        if args.get("attribute") and window.end - window.start > MAX_SIGNIFICANT_STATES_RANGE:
            return _tool_error(_ATTRIBUTE_SUMMARY_RANGE_ERROR), "invalid_request", entity_id
        return await _history_summary(entity_id, window, access_key, hass, args.get("attribute") or None)
    if mode == "significant_states" and window.end - window.start > MAX_SIGNIFICANT_STATES_RANGE:
        return _tool_error(
            "significant_states supports at most 7 days per request because Home Assistant cannot bound this query in the database. Use state_changes for longer ranges."
        ), "invalid_request", entity_id
    newest = order == "newest_first"
    # A cursor continues where the previous page stopped: forward it is the new
    # lower bound, backward it is the new exclusive upper bound.
    lower = window.start if newest else window.page_start
    upper = (window.page_start if args.get("cursor") else window.end) if newest else window.end

    try:
        require_component(hass, "recorder")
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder import history as rec_history

        instance = get_instance(hass)
        keep_days = instance.keep_days

        async def fetch(after: datetime, before: datetime, count: int | None) -> list[Any]:
            fn: functools.partial[Any]
            if mode == "state_changes":
                fn = functools.partial(
                    rec_history.state_changes_during_period,
                    hass, after, before, entity_id, True, False, count, False,
                )
            else:
                fn = functools.partial(
                    rec_history.get_significant_states,
                    hass, after, before, [entity_id], None, False, True, False, False,
                )
            result = cast(dict[str, list[Any]], await instance.async_add_executor_job(fn))
            return result.get(entity_id, [])

        older = False
        if not newest:
            states_list = await fetch(lower, upper, window.limit + 1)
        elif mode == "state_changes":
            # One extra row lets the page end between two rows a cursor can separate.
            states_list, older = await newest_page(fetch, lower, upper, window.limit + 1)
        else:
            # Significant states are already bounded to 7 days and read whole.
            states_list = await fetch(lower, upper, None)
    except Exception:
        _LOGGER.warning("MCP history call failed for entity %s", entity_id, exc_info=True)
        return _tool_error("History call failed."), "denied", entity_id

    # Core filters on stored float timestamps finer than a microsecond, so the
    # cursor is derived from those floats: a cursor taken from a row's rounded
    # time would read that row again or skip a neighbour in the same microsecond.
    stamps = [row_timestamp(state) for state in states_list]
    has_more = older or len(states_list) > window.limit
    cursor: datetime | None = None
    if has_more:
        if any(stamp is None for stamp in stamps):
            _LOGGER.warning("MCP history row for entity %s has no usable timestamp", entity_id)
            return _tool_error("History call returned an invalid timestamp."), "invalid_request", entity_id
        known = cast(list[float], stamps)
        if not newest:
            count = forward_page_end(known, window.limit)
            if count is not None:
                states_list, cursor = states_list[:count], from_us(ceil_us(known[count - 1]))
        else:
            first = backward_page_start(known, window.limit)
            if first is not None:
                states_list, cursor = states_list[first:], from_us(floor_us(known[first]))
        if cursor is None:
            return _tool_error(
                "More history rows share one microsecond than this limit allows; increase limit."
            ), "invalid_request", entity_id

    page_dicts: list[dict[str, Any]] = [
        dict(s.as_dict()) if hasattr(s, "as_dict") else dict(s) for s in states_list
    ]
    if newest:
        page_dicts.reverse()
    if mode == "significant_states":
        history = [_scrub_state_dict(d, access_key, hass) for d in page_dicts]
        for row in history:
            for key in ("last_changed", "last_updated"):
                if isinstance(row.get(key), datetime):
                    row[key] = iso_utc(row[key])
    else:
        history = [
            {
                "state": d.get("state"),
                "when": iso_utc(stamp) if (stamp := state_timestamp(d)) is not None else None,
            }
            for d in page_dicts
        ]
    if newest:
        covered_start = cursor if cursor is not None else window.start
        covered_end = upper
    else:
        covered_start = window.page_start
        covered_end = cursor if cursor is not None else window.end
    warnings = retention_warnings("short_term", window.start, keep_days, utcnow())
    body = recorder_envelope(
        entity_id=entity_id,
        window=window,
        covered_start=covered_start,
        covered_end=covered_end,
        rows=history,
        timestamp_getter=(
            (lambda row: state_timestamp({"last_changed": row.get("when")}))
            if mode == "state_changes"
            else state_timestamp
        ),
        effective_limit=window.limit,
        has_more=has_more,
        next_cursor=cursor,
        retention=retention_metadata("short_term", keep_days),
        warnings=warnings,
        result_key="history",
        newest_first=newest,
    )
    body["order"] = order
    body["mode"] = mode
    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id


_ATTRIBUTE_SUMMARY_RANGE_ERROR = (
    "attribute summaries cover at most 7 days per request because Home Assistant cannot bound this query "
    "in the database. Summarize a shorter range, or leave out attribute to total the state over a longer one."
)


async def _scan_summary(
    instance: Any, hass: HomeAssistant, entity_id: str, access_key: AccessKeyRecord, start_us: int, end_us: int,
    max_seconds: float, attribute: str | None = None,
) -> Any:
    """Fold one entity's Recorder changes over [start_us, end_us) within the summary budgets.

    With attribute, the fold runs over that attribute's values instead of the state, reading them
    once and paging in memory because Core's query takes no limit. The labels are scrubbed as
    significant_states scrubs a row, before the fold and so before any state filter sees them.
    """
    rows: list[tuple[int, str]] | None = None

    async def fetch(after_us: int, before_us: int, limit: int, include_start: bool) -> list[tuple[int, str]]:
        nonlocal rows
        if attribute is not None:
            if rows is None:
                changes = await instance.async_add_executor_job(
                    read_attribute_changes, hass, entity_id, attribute, after_us, before_us, include_start)
                rows = attribute_rows(changes, lambda found: scrub_attributes(found, access_key, hass))
            return attribute_page(rows, after_us, limit, include_start)
        return cast(list[tuple[int, str]], await instance.async_add_executor_job(
            read_state_change_page, hass, entity_id, after_us, before_us, limit, include_start))

    return await scan_state_changes(
        fetch, start_us, end_us, page_rows=HISTORY_SUMMARY_PAGE_ROWS,
        max_rows=HISTORY_SUMMARY_MAX_ROWS, max_seconds=max_seconds,
    )


# search_entities filters select accepts; it finds the entities, so their scope,
# Assist exposure and child-device rules are exactly that tool's.
_SUMMARY_SELECT_FILTERS = ("query", "domain", "device_class", "integration", "radio", "power", "area")


def _stored_bytes(value: Any) -> int:
    """Bytes a tool result's JSON text takes as the Conversation Log stores it."""
    return json_size(json.dumps(value))


def _without_empty_placeholder(args: dict) -> dict:
    """args without an empty select or entity_id sent beside the other.

    Some models fill every optional parameter: an empty one beside the other names
    nothing, so it is not a second, conflicting request.
    """
    if "select" in args and "entity_id" in args:
        for placeholder in ("select", "entity_id"):
            if args[placeholder] in (None, "", [], {}):
                return {key: value for key, value in args.items() if key != placeholder}
    return args


async def _resolve_select(
    select: Any, tool: str, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData | None,
    *, every: bool = False,
) -> tuple[list[str], dict[str, Any]] | tuple[dict, str, str]:
    """The entities a search_entities filter selects for a summary, and the fields describing them.

    Returns the sorted entity IDs and selection fields, or a tool error. With `every`, a select with
    no filter selects every accessible entity.
    """
    # Diagnostic and configuration entities are left out unless asked for, since a battery or
    # link-quality sensor is not what such a question means. An approximate name match selects
    # nothing, because summarizing a guessed entity would answer the wrong question.
    allowed = (*_SUMMARY_SELECT_FILTERS, "include_diagnostic")
    if not isinstance(select, dict) or any(key not in allowed for key in select):
        return _tool_error(
            "select must be an object of search_entities filters: " + ", ".join(allowed) + "."
        ), "invalid_request", tool
    include_diagnostic = select.get("include_diagnostic", False)
    if not isinstance(include_diagnostic, bool):
        return _tool_error("select.include_diagnostic must be a boolean."), "invalid_request", tool
    filters = {key: select[key] for key in _SUMMARY_SELECT_FILTERS if select.get(key) not in (None, "", [])}
    if not filters and not every:
        return _tool_error(
            'select needs at least one filter, for example {"radio": "zigbee", "domain": "binary_sensor"}, '
            "or in_state, which keeps only the entities that were in a state and lets an empty select scan every entity."
        ), "invalid_request", tool
    denied = "select uses search_entities, which this access key cannot use; pass entity_id instead."
    if not filters:
        if effective_cap(access_key, "cap_search") == CAP_DENY:
            return _tool_error(denied), "denied", tool
        registry = er.async_get(hass)
        listed: dict[str, Any] = {}
        rows = [{"entity_id": state["entity_id"], "entity_category": getattr(
                    registry.async_get(state["entity_id"]), "entity_category", None)}
                for state in filter_entities_for_access_key(hass.states.async_all(), access_key, hass)]
    else:

        found, outcome, _resource = await _tool_search_entities(
            {**filters, "limit": SEARCH_ENTITIES_MAX_LIMIT, "registry_state": "enabled"}, access_key, hass,
            data if data is not None else hass.data[DOMAIN])
        text = "".join(item.get("text", "") for item in found.get("content", []) if isinstance(item, dict))
        if outcome != "allowed":
            if outcome == "denied":
                text = denied
            return _tool_error(text if text.startswith("select") else f"select: {text}"), outcome, tool
        listed = json.loads(text)
        rows = listed.get("entities", [])
    warnings: list[str] = []
    if listed.get("fuzzy_fallback"):
        rows = []
        warnings.append("No entity name contains every query word; search_entities shows the closest matches.")
    diagnostic = [row for row in rows if row.get("entity_category") in ("config", "diagnostic")]
    chosen = sorted(row["entity_id"] for row in rows if include_diagnostic or row not in diagnostic)
    if listed.get("has_more"):
        warnings.append(f"More than {SEARCH_ENTITIES_MAX_LIMIT} entities matched select; narrow it.")
    selection: dict[str, Any] = {"select": {**filters, **({"include_diagnostic": True} if include_diagnostic else {})},
                                 "matched": len(chosen)}
    if diagnostic and not include_diagnostic:
        selection["diagnostic_excluded"] = len(diagnostic)
    if not chosen:
        warnings.append("No entity matched select.")
    selection["warnings"] = warnings
    return chosen, selection


async def _history_summary_select(
    select: Any, args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData | None
) -> tuple[dict, str, str]:
    """get_history summary mode over the entities a search_entities filter selects.

    Matches past what one result holds are named in not_summarized rather than
    dropped; _resolve_select owns which entities a filter selects.
    """
    tool = "get_history"
    if "entity_id" in args:
        return _tool_error("Pass entity_id or select, not both."), "invalid_request", tool
    if str(args.get("mode") or "").strip().lower() != "summary":
        return _tool_error("select is accepted only with mode summary."), "invalid_request", tool
    resolved = await _resolve_select(select, tool, access_key, hass, data,
                                     every=bool(args.get("in_state") or args.get("not_in_state")))
    if not isinstance(resolved[0], list):
        return cast(tuple[dict, str, str], resolved)
    chosen, selection = cast(tuple[list[str], dict[str, Any]], resolved)
    return await _history_summary_many(chosen, args, access_key, hass, selection=selection)


async def _history_summary_many(
    raw_ids: list[Any], args: dict, access_key: AccessKeyRecord, hass: HomeAssistant,
    selection: dict[str, Any] | None = None,
) -> tuple[dict, str, str]:
    """get_history summary mode for several entities: one compact row each.

    Access is checked before any Recorder read and after the scans; inaccessible and missing share
    not_found. Entities past the time or size budget are named, never dropped.
    """
    # One time budget bounds the scans and TOOL_RESULT_MAX_BYTES the result, so the Conversation Log
    # keeps it whole: rows are added in order while they fit, counting every entity still to come as
    # a name. The rest land in unscanned and not_summarized. An entity whose own scan stops at its
    # budget keeps a next_cursor to continue alone. A selection adds its own fields and warnings,
    # and may legitimately match nothing.
    tool = "get_history"
    if str(args.get("mode") or "").strip().lower() != "summary":
        return _tool_error(
            "A list of entity_id is accepted with mode summary, one compact row per entity, or state_changes, "
            "each entity's rows; significant_states reads one entity."
        ), "invalid_request", tool
    if any(key in args for key in ("limit", "order", "cursor")):
        return _tool_error(
            "limit, order and cursor do not apply to a summary of several entities; continue an entity that "
            "returned next_cursor in a single-entity call."
        ), "invalid_request", tool
    # A selection is bounded by the scan and result budgets instead, which name what they leave out.
    if ((not raw_ids and selection is None) or (selection is None and len(raw_ids) > SEARCH_ENTITIES_MAX_LIMIT)
            or not all(isinstance(item, str) and item.strip() for item in raw_ids)):
        return _tool_error(
            f"entity_id must be one entity ID or a list of 1 to {SEARCH_ENTITIES_MAX_LIMIT} entity ID strings."
        ), "invalid_request", tool
    raw_in_state, raw_not_in_state = args.get("in_state"), args.get("not_in_state")
    in_state = {item.strip() for item in str_list_arg(raw_in_state)}
    not_in_state = {item.strip() for item in str_list_arg(raw_not_in_state)}
    if raw_in_state not in (None, "", []) and (not in_state or "" in in_state):
        return _tool_error("in_state must be a state or a list of states."), "invalid_request", tool
    if raw_not_in_state not in (None, "", []) and (not not_in_state or "" in not_in_state):
        return _tool_error("not_in_state must be a state or a list of states."), "invalid_request", tool
    if in_state and not_in_state:
        return _tool_error("Pass in_state or not_in_state, not both."), "invalid_request", tool
    try:
        window = parse_recorder_window(args, default_range=timedelta(hours=24))
    except ValueError as err:
        return _tool_error(str(err)), "invalid_request", tool
    if args.get("attribute") and window.end - window.start > MAX_SIGNIFICANT_STATES_RANGE:
        return _tool_error(_ATTRIBUTE_SUMMARY_RANGE_ERROR), "invalid_request", tool
    now = utcnow()
    end = min(window.end, now)
    if window.start >= end:
        return _tool_error("A summary needs a range that starts before the current time."), "invalid_request", tool

    def readable(entity_id: str) -> bool:
        return resolve_read(entity_id, access_key, hass) not in (
            Permission.NOT_FOUND, Permission.NO_ACCESS, Permission.DENY)

    ids = list(dict.fromkeys(item.strip() for item in raw_ids))
    scan_ids = [entity_id for entity_id in ids if readable(entity_id)]
    attribute = args.get("attribute") or None
    selected = dict(selection or {})
    # "Which room had no motion" is answered by room, so each row names its entity and area.

    registries = (er.async_get(hass), dr.async_get(hass), ar.async_get(hass))
    scanned = 0
    selection_warnings = list(selected.pop("warnings", []))
    rows: dict[str, Any] = {}
    not_recorded: list[str] = []
    unscanned: list[str] = []
    unavailable: list[str] = []
    not_summarized: list[str] = []
    keep_days: Any = None

    def build(still: set[str]) -> dict[str, Any]:
        warnings = selection_warnings + retention_warnings("short_term", window.start, keep_days, now)
        kept = [row for entity_id, row in rows.items() if entity_id in still]
        if window.end > now:
            warnings.append("end_time is in the future; the summary stops at the current time.")
        if any("other_states" in row for row in kept):
            warnings.append(
                f"Rows list their {HISTORY_SUMMARY_MANY_MAX_STATES} longest states and combine the rest in "
                "other_states; for a numeric sensor use get_statistics.")
        if any("next_cursor" in row for row in kept):
            warnings.append(
                "An entity with next_cursor stopped at its scan budget: continue it in a single-entity call with "
                "that cursor and add the totals.")
        if not_summarized:
            warnings.append(
                "The result is full: summarize not_summarized as an entity_id list in another call.")
        if unscanned:
            warnings.append(
                "The call's time budget ended before the unscanned entities; summarize them in another call.")
        if unavailable:
            warnings.append(
                "Recorder holds too many changes within a few microseconds to summarize the entities in unavailable.")
        body: dict[str, Any] = {
            "mode": "summary",
            **({"attribute": attribute} if attribute is not None else {}),
            **({"in_state": sorted(in_state), "scanned": scanned} if in_state else {}),
            **({"not_in_state": sorted(not_in_state), "scanned": scanned} if not_in_state else {}),
            "requested_range": {"start": window.start.isoformat(), "end": window.end.isoformat()},
            **selected,
            "count": len(kept),
            "entities": kept,
        }
        # not_found is always present, as on get_state's list; the rest when non-empty.
        body["not_found"] = [e for e in ids if e not in still]
        for key, values in (("not_summarized", [e for e in not_summarized if e in still]),
                            ("not_recorded", [e for e in not_recorded if e in still]),
                            ("unscanned", [e for e in unscanned if e in still]),
                            ("unavailable", [e for e in unavailable if e in still])):
            if values:
                body[key] = values
        body["retention"] = retention_metadata("short_term", keep_days)
        body["warnings"] = warnings
        return body

    try:
        require_component(hass, "recorder")
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.recorder import get_instance, is_entity_recorded

        instance = get_instance(hass)
        keep_days = instance.keep_days
        deadline = time.monotonic() + HISTORY_SUMMARY_BATCH_SECONDS
        # Whatever does not fit is named, so every entity still to come counts as
        # its name; the envelope estimate leaves room for the warnings. The built
        # body is measured exactly below.
        names = [_stored_bytes(entity_id) for entity_id in scan_ids]
        to_come = sum(names)
        used = _stored_bytes(build(set(scan_ids))) + 1024
        for entity_id, name in zip(scan_ids, names):
            to_come -= name
            if not is_entity_recorded(hass, entity_id):
                not_recorded.append(entity_id)
            elif not_summarized:
                not_summarized.append(entity_id)
            elif (remaining := deadline - time.monotonic()) <= 0:
                unscanned.append(entity_id)
            else:
                try:
                    summary = await _scan_summary(
                        instance, hass, entity_id, access_key, to_us(window.start), to_us(end),
                        min(HISTORY_SUMMARY_MAX_SECONDS, remaining), attribute)
                except SummaryUnavailableError:
                    unavailable.append(entity_id)
                else:
                    scanned += 1
                    # Decided on the full totals, which a row's state list may combine.
                    if in_state and not any(totals.duration_us or totals.entered
                                            for state, totals in summary.totals.items() if state in in_state):
                        continue
                    # "Which sensors never triggered": an entity that held or entered one of
                    # these states, even when the range began in it, is left out.
                    if not_in_state and any(totals.duration_us or totals.entered
                                            for state, totals in summary.totals.items() if state in not_in_state):
                        continue
                    row = compact_summary_row(summary_body(
                        entity_id, (window.start, window.end), summary, max_states=HISTORY_SUMMARY_MANY_MAX_STATES,
                        retention={}, warnings=[],
                    ))
                    state = hass.states.get(entity_id)
                    row = {"entity_id": entity_id, "name": state.name if state is not None else None,
                           "area": _area_name_for_entity(entity_id, *registries),
                           **{key: value for key, value in row.items() if key != "entity_id"}}
                    size = _stored_bytes(row)
                    if used + size + to_come <= TOOL_RESULT_MAX_BYTES:
                        rows[entity_id] = row
                        used += size
                        continue
                    not_summarized.append(entity_id)
            used += name
    except Exception:
        _LOGGER.warning("MCP history summary of several entities failed", exc_info=True)
        return _tool_error("History call failed."), "invalid_request", tool
    check_request_authority()
    authority = request_authority.get()
    if authority is not None:
        access_key = authority.current_access_key()
    still = {entity_id for entity_id in scan_ids if readable(entity_id)}
    body = build(still)
    while rows and _stored_bytes(body) > TOOL_RESULT_MAX_BYTES:
        not_summarized.insert(0, rows.popitem()[0])
        body = build(still)
    return _tool_success(json.dumps(body)), "allowed", tool


async def _history_rows_many(
    raw_ids: list[Any], args: dict, access_key: AccessKeyRecord, hass: HomeAssistant,
) -> tuple[dict, str, str]:
    """get_history state_changes for several entities: each entity's own page.

    Each page equals a single-entity call's, so next_cursor continues that entity alone and a cursor
    is refused here. Access is rechecked after the reads.
    """
    # Inaccessible and missing entities share not_found. Pages are added in order while they fit
    # TOOL_RESULT_MAX_BYTES and one time budget; the entities past either are named (not_listed,
    # unscanned), never dropped.
    tool = "get_history"
    if "cursor" in args:
        return _tool_error(
            "cursor continues one entity: send that entity alone with the next_cursor its row returned."
        ), "invalid_request", tool
    if (not raw_ids or len(raw_ids) > SEARCH_ENTITIES_MAX_LIMIT
            or not all(isinstance(item, str) and item.strip() for item in raw_ids)):
        return _tool_error(
            f"entity_id must be one entity ID or a list of 1 to {SEARCH_ENTITIES_MAX_LIMIT} entity ID strings."
        ), "invalid_request", tool
    order = str(args.get("order") or "oldest_first").strip().lower()
    if order not in ("oldest_first", "newest_first"):
        return _tool_error("Invalid order. Must be one of: oldest_first, newest_first."), "invalid_request", tool
    if args.get("attribute"):
        return _tool_error(
            "attribute is accepted only with mode summary, which totals how long the attribute held each value."
        ), "invalid_request", tool
    try:
        window = parse_recorder_window(args, default_range=timedelta(hours=24))
    except ValueError as err:
        return _tool_error(str(err)), "invalid_request", tool

    def readable(entity_id: str) -> bool:
        return resolve_read(entity_id, access_key, hass) not in (
            Permission.NOT_FOUND, Permission.NO_ACCESS, Permission.DENY)


    registries = (er.async_get(hass), dr.async_get(hass), ar.async_get(hass))
    ids = list(dict.fromkeys(item.strip() for item in raw_ids))
    read_ids = [entity_id for entity_id in ids if readable(entity_id)]
    page_args = {key: value for key, value in args.items() if key != "entity_id"}
    rows: dict[str, Any] = {}
    page_warnings: list[str] = []
    not_recorded: list[str] = []
    not_listed: list[str] = []
    unscanned: list[str] = []
    unavailable: list[str] = []
    keep_days: Any = None

    def build(still: set[str]) -> dict[str, Any]:
        warnings = list(dict.fromkeys(page_warnings))
        kept = [row for entity_id, row in rows.items() if entity_id in still]
        if any(row["has_more"] for row in kept):
            warnings.append(
                "An entity with has_more has more rows in the range: continue it in a single-entity call with "
                "its next_cursor and the same order.")
        if not_listed:
            warnings.append("The result is full: read not_listed as an entity_id list in another call.")
        if unscanned:
            warnings.append("The call's time budget ended before the unscanned entities; read them in another call.")
        if unavailable:
            warnings.append("Recorder could not return a page for the entities in unavailable; read each alone.")
        body: dict[str, Any] = {
            "mode": "state_changes",
            "order": order,
            "requested_range": {"start": iso_utc(window.start), "end": iso_utc(window.end)},
            "limit": window.limit,
            "count": len(kept),
            "entities": kept,
            "not_found": [e for e in ids if e not in still],
        }
        for key, values in (("not_listed", not_listed), ("not_recorded", not_recorded),
                            ("unscanned", unscanned), ("unavailable", unavailable)):
            if listed := [e for e in values if e in still]:
                body[key] = listed
        body["retention"] = retention_metadata("short_term", keep_days)
        body["warnings"] = warnings
        return body

    try:
        require_component(hass, "recorder")
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.recorder import get_instance, is_entity_recorded

        keep_days = get_instance(hass).keep_days
        deadline = time.monotonic() + HISTORY_SUMMARY_BATCH_SECONDS
        names = [_stored_bytes(entity_id) for entity_id in read_ids]
        to_come = sum(names)
        used = _stored_bytes(build(set(read_ids))) + 1024
        for entity_id, name in zip(read_ids, names):
            to_come -= name
            if not is_entity_recorded(hass, entity_id):
                not_recorded.append(entity_id)
            elif not_listed:
                not_listed.append(entity_id)
            elif time.monotonic() >= deadline:
                unscanned.append(entity_id)
            else:
                content, outcome, _resource = await _history_page(
                    {**page_args, "entity_id": entity_id}, access_key, hass)
                if outcome != "allowed" or content.get("isError"):
                    unavailable.append(entity_id)
                else:
                    page = json.loads(content["content"][0]["text"])
                    page_warnings.extend(page.get("warnings") or [])
                    state = hass.states.get(entity_id)
                    row = {"entity_id": entity_id, "name": state.name if state is not None else None,
                           "area": _area_name_for_entity(entity_id, *registries),
                           "count": page["count"], "has_more": page["has_more"],
                           **({"next_cursor": page["next_cursor"]} if page["has_more"] else {}),
                           "history": page["history"]}
                    size = _stored_bytes(row)
                    if used + size + to_come <= TOOL_RESULT_MAX_BYTES:
                        rows[entity_id] = row
                        used += size
                        continue
                    not_listed.append(entity_id)
            used += name
    except Exception:
        _LOGGER.warning("MCP history rows of several entities failed", exc_info=True)
        return _tool_error("History call failed."), "invalid_request", tool
    check_request_authority()
    authority = request_authority.get()
    if authority is not None:
        access_key = authority.current_access_key()
    still = {entity_id for entity_id in read_ids if readable(entity_id)}
    body = build(still)
    while rows and _stored_bytes(body) > TOOL_RESULT_MAX_BYTES:
        not_listed.insert(0, rows.popitem()[0])
        body = build(still)
    return _tool_success(json.dumps(body)), "allowed", tool


async def _history_summary(
    entity_id: str, window: RecorderWindow, access_key: AccessKeyRecord, hass: HomeAssistant,
    attribute: str | None = None,
) -> tuple[dict, str, str]:
    """get_history summary mode: one budgeted scan folded into per-state totals.

    With attribute, totals are per value of that attribute. An entity Recorder excludes is refused;
    access is rechecked after the scan and before disclosure.
    """
    # The scan stops at the current time, because a future end would attribute time that has not
    # happened. Carrying an excluded entity's last row forward would invent durations.
    now = utcnow()
    end = min(window.end, now)
    if window.page_start >= end:
        return _tool_error("A summary needs a range that starts before the current time."), "invalid_request", entity_id
    try:
        require_component(hass, "recorder")
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.recorder import get_instance, is_entity_recorded

        instance = get_instance(hass)
        if not is_entity_recorded(hass, entity_id):
            return _tool_error(
                "Home Assistant's Recorder does not record this entity, so there is no history to summarize."
            ), "invalid_request", entity_id
        keep_days = instance.keep_days
        summary = await _scan_summary(
            instance, hass, entity_id, access_key, to_us(window.page_start), to_us(end),
            HISTORY_SUMMARY_MAX_SECONDS, attribute)
    except SummaryUnavailableError:
        return _tool_error(
            "Recorder holds too many changes for this entity within a few microseconds to summarize this range."
        ), "invalid_request", entity_id
    except Exception:
        _LOGGER.warning("MCP history summary failed for entity %s", entity_id, exc_info=True)
        return _tool_error("History call failed."), "invalid_request", entity_id
    check_request_authority()
    authority = request_authority.get()
    if authority is not None:
        access_key = authority.current_access_key()
    if resolve_read(entity_id, access_key, hass) in (Permission.NOT_FOUND, Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id
    warnings = retention_warnings("short_term", window.page_start, keep_days, now)
    if window.end > now:
        warnings.append("end_time is in the future; the summary stops at the current time.")
    def build(count: int) -> dict:
        # A label is a whole attribute value, so fifty of them can pass the result bound.
        body = summary_body(
            entity_id, (window.start, window.end), summary, max_states=count,
            retention=retention_metadata("short_term", keep_days), warnings=list(warnings),
            continued=window.page_start > window.start,
        )
        return body if attribute is None else {"attribute": attribute, **body}

    fitted = longest_fitting(build, min(HISTORY_SUMMARY_MAX_STATES, len(summary.totals)))
    return _tool_success(json.dumps(fitted)), "allowed", entity_id


_STATISTIC_WINDOW_TYPES = ("mean", "min", "max", "change")


async def _statistics_summary_many(
    raw_ids: list[Any], args: dict, access_key: AccessKeyRecord, hass: HomeAssistant,
    selection: dict[str, Any] | None = None,
) -> tuple[dict, str, str]:
    """get_statistics over several entities: one row each for the whole window.

    A row has a measurement's mean, min and max or a total's change, in its display unit. Access is
    checked before any read and before disclosure.
    """
    # The shared time budget names the entities it did not reach, and rows past what one result
    # holds are named in not_summarized.
    tool = "get_statistics"
    if ((not raw_ids and selection is None) or len(raw_ids) > SEARCH_ENTITIES_MAX_LIMIT
            or not all(isinstance(item, str) and item.strip() for item in raw_ids)):
        return _tool_error(
            f"entity_id must be one entity ID or a list of 1 to {SEARCH_ENTITIES_MAX_LIMIT} entity ID strings."
        ), "invalid_request", tool
    raw_types = args.get("statistic_types")
    if raw_types is not None and (not isinstance(raw_types, list) or not raw_types
                                  or any(not isinstance(item, str) for item in raw_types)):
        return _tool_error("statistic_types must be a non-empty array of strings."), "invalid_request", tool
    # Page options and per-period fields change nothing a whole-window row reads, so
    # a model carrying them over from a page request is told, not sent back.
    ignored = [key for key in ("period", "limit", "cursor") if key in args]
    ignored += [item for item in raw_types or [] if item not in _STATISTIC_WINDOW_TYPES]
    types = {item for item in raw_types or _STATISTIC_WINDOW_TYPES if item in _STATISTIC_WINDOW_TYPES} \
        or set(_STATISTIC_WINDOW_TYPES)
    try:
        window = parse_recorder_window(args, default_range=timedelta(days=30))
    except ValueError as err:
        return _tool_error(str(err)), "invalid_request", tool
    end = min(window.end, utcnow())
    if window.start >= end:
        return _tool_error("Statistics need a range that starts before the current time."), "invalid_request", tool

    def readable(entity_id: str) -> bool:
        return resolve_read(entity_id, access_key, hass) not in (
            Permission.NOT_FOUND, Permission.NO_ACCESS, Permission.DENY)

    ids = list(dict.fromkeys(item.strip() for item in raw_ids))
    scan_ids = [entity_id for entity_id in ids if readable(entity_id)]
    rows: list[dict[str, Any]] = []
    no_statistics: list[str] = []
    unscanned: list[str] = []
    try:
        require_component(hass, "recorder")
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.recorder import get_instance

        instance = get_instance(hass)
        deadline = time.monotonic() + HISTORY_SUMMARY_BATCH_SECONDS
        for entity_id in scan_ids:
            if time.monotonic() >= deadline:
                unscanned.append(entity_id)
                continue
            found = await instance.async_add_executor_job(
                read_statistic_window, hass, window.start, end, entity_id, types)
            if found is None:
                no_statistics.append(entity_id)
            else:
                values, unit = found
                rows.append({"entity_id": entity_id, **{key: round(value, 4) if isinstance(value, float) else value
                                                         for key, value in values.items()}, "unit": unit})
    except Exception:
        _LOGGER.warning("MCP statistics of several entities failed", exc_info=True)
        return _tool_error("Statistics call failed."), "invalid_request", tool
    check_request_authority()
    authority = request_authority.get()
    if authority is not None:
        access_key = authority.current_access_key()
    still = {entity_id for entity_id in scan_ids if readable(entity_id)}
    # A comparison is reported by room and name, so each row carries them.

    registries = (er.async_get(hass), dr.async_get(hass), ar.async_get(hass))
    rows = [{"entity_id": row["entity_id"],
             "name": (state.name if (state := hass.states.get(row["entity_id"])) is not None else None),
             "area": _area_name_for_entity(row["entity_id"], *registries),
             **{key: value for key, value in row.items() if key != "entity_id"}}
            for row in rows if row["entity_id"] in still]
    selected = dict(selection or {})
    selection_warnings = list(selected.pop("warnings", []))

    def build(count: int) -> dict[str, Any]:
        warnings = list(selection_warnings)
        if ignored:
            warnings.append(
                "Ignored " + ", ".join(ignored) + ": each row covers the whole window with "
                + ", ".join(sorted(types)) + " (change is a total's use over the window).")
        body: dict[str, Any] = {
            "requested_range": {"start": window.start.isoformat(), "end": window.end.isoformat()},
            **selected,
            "count": count,
            "entities": rows[:count],
        }
        body["not_found"] = [e for e in ids if e not in still]
        for key, values in (("not_summarized", [row["entity_id"] for row in rows[count:]]),
                            ("no_statistics", [e for e in no_statistics if e in still]),
                            ("unscanned", [e for e in unscanned if e in still])):
            if values:
                body[key] = values
        if count < len(rows):
            warnings.append("The result is full: read not_summarized as an entity_id list in another call.")
        if unscanned:
            warnings.append("The call's time budget ended before the unscanned entities; read them in another call.")
        if no_statistics:
            warnings.append("Entities in no_statistics have no Recorder statistics (no state_class); use get_history.")
        body["warnings"] = warnings
        return body

    return _tool_success(json.dumps(longest_fitting(build, len(rows)), default=str)), "allowed", tool


async def _tool_get_statistics(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData | None = None
) -> tuple[dict, str, str]:
    """MCP tool: fetch one aligned and bounded page of Recorder statistics, or several entities' windows."""
    args = _without_empty_placeholder(args)
    if "select" in args:
        if "entity_id" in args:
            return _tool_error("Pass entity_id or select, not both."), "invalid_request", "get_statistics"
        resolved = await _resolve_select(args["select"], "get_statistics", access_key, hass, data)
        if not isinstance(resolved[0], list):
            return cast(tuple[dict, str, str], resolved)
        chosen, selection = cast(tuple[list[str], dict[str, Any]], resolved)
        return await _statistics_summary_many(chosen, args, access_key, hass, selection=selection)
    if isinstance(args.get("entity_id"), list):
        return await _statistics_summary_many(args["entity_id"], args, access_key, hass)
    return await _fit_recorder_page(lambda page_args: _statistics_page(page_args, access_key, hass), args, "statistics")


async def _statistics_page(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[dict, str, str]:
    """One aligned and bounded page of one entity's Recorder statistics."""
    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "get_statistics"

    perm = resolve_read(entity_id, access_key, hass)
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    if perm in (Permission.NO_ACCESS, Permission.DENY):
        return _tool_error("Entity not found."), "denied", entity_id

    try:
        window = parse_recorder_window(args, default_range=timedelta(days=30))
    except ValueError as err:
        return _tool_error(str(err)), "invalid_request", entity_id

    period = args.get("period", "hour")
    if period not in STATISTIC_PERIOD_LIMITS:
        return _tool_error(
            "Invalid period. Must be one of: 5minute, hour, day, week, month, year."
        ), "invalid_request", entity_id

    valid_types = {"mean", "min", "max", "sum", "state", "change", "last_reset"}
    raw_types = args.get("statistic_types")
    if raw_types is not None and (
        not isinstance(raw_types, list)
        or not raw_types
        or any(not isinstance(item, str) or item not in valid_types for item in raw_types)
    ):
        return _tool_error(
            "statistic_types must be a non-empty array containing only: mean, min, max, sum, state, change, last_reset."
        ), "invalid_request", entity_id
    type_set = cast(
        set[Literal["mean", "min", "max", "sum", "state", "change", "last_reset"]],
        set(raw_types) if raw_types else valid_types,
    )
    page_start, page_end, effective_limit, has_more = statistic_page(window, period)

    try:
        require_component(hass, "recorder")
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.recorder import get_instance

        instance = get_instance(hass)
        keep_days = instance.keep_days
        query_end = page_end
        if period in ("day", "week", "month", "year"):
            query_end -= timedelta.resolution
        fn = functools.partial(
            read_statistics_page, hass, page_start, query_end, entity_id, period, type_set,
        )
        raw_rows, units, unit_warnings = await instance.async_add_executor_job(fn)
    except Exception:
        _LOGGER.warning("MCP statistics call failed for entity %s", entity_id, exc_info=True)
        return _tool_error("Statistics call failed."), "invalid_request", entity_id

    statistics: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        stamp = statistic_timestamp(raw_row)
        if stamp is None or not page_start <= stamp < page_end:
            continue
        row = dict(raw_row)
        row["start"] = iso_utc(stamp)
        # Core returns bucket ends and resets as epoch seconds; render them like start.
        for key in ("end", "last_reset"):
            value = row.get(key)
            if isinstance(value, datetime):
                row[key] = iso_utc(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                row[key] = iso_utc(datetime.fromtimestamp(value, tz=dt_util.UTC))
        statistics.append(row)
        if len(statistics) == effective_limit:
            break
    retention_kind: Literal["short_term", "long_term"] = (
        "short_term" if period == "5minute" else "long_term"
    )
    warnings = retention_warnings(retention_kind, window.start, keep_days, utcnow()) + unit_warnings
    if not statistics and has_more:
        # Pages run oldest first, so an empty early page says nothing about later ones.
        warnings.append(
            "No statistics in this page. Later pages may hold them: continue with next_cursor through unread_range before concluding there are none."
        )
    elif not statistics:
        warnings.append(
            "No statistics were returned. The entity may not expose a state_class, may not produce Recorder statistics, or may have no data in this period."
        )
    body = recorder_envelope(
        entity_id=entity_id,
        window=window,
        covered_start=page_start,
        covered_end=page_end,
        rows=statistics,
        timestamp_getter=statistic_timestamp,
        effective_limit=effective_limit,
        has_more=has_more,
        next_cursor=page_end if has_more else None,
        retention=retention_metadata(retention_kind, keep_days),
        warnings=warnings,
        result_key="statistics",
    )
    body["period"] = period
    # Every value field is in units.display_unit_of_measurement; null means Recorder
    # holds no metadata for this entity, not that the values are unitless.
    body["units"] = units
    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id
