"""Typed date/time helper values, independent of model decisions and scheduling."""
from __future__ import annotations

from datetime import UTC, date, datetime, time
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import COMMAND_ROUTING_TEMPORAL_OPERATIONS, COMMAND_ROUTING_TIMER_OBSERVATION_TOLERANCE_SECONDS, MAX_SEARCH_QUERY_LEN

# These patterns read literal value notation, not intents or target names.
_VALUE = re.compile(r"(?<![0-9a-z_:/.-])(?:[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?(?:\s*[ap]m)?)?|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?(?:\s*[ap]m)?)(?![0-9a-z_:/-]|\.[0-9])", re.IGNORECASE)
_ENTITY = re.compile(r"(?<![a-z0-9_])[a-z_][a-z0-9_]*\.[a-z0-9_]+(?![a-z0-9_])")


def read_temporal_value(sentence: str) -> dict[str, str] | None:
    """Read one ISO date, clock time, or date and time from a sentence.

    Returns {"kind", "value"} or None; relative dates and locale order are not
    guessed, and any other number in the sentence (entity ids aside) refuses.
    """
    if not isinstance(sentence, str) or len(sentence) > MAX_SEARCH_QUERY_LEN:
        return None
    matches = list(_VALUE.finditer(sentence))
    if len(matches) == 2:
        pieces = [read_temporal_value(match.group()) for match in matches]
        if not all(pieces) or {piece["kind"] for piece in pieces if piece} != {"date", "time"}:
            return None
        remainder = sentence[:matches[0].start()] + sentence[matches[0].end():matches[1].start()] + sentence[matches[1].end():]
        if any(char.isnumeric() for char in _ENTITY.sub("", remainder)):
            return None
        values = {piece["kind"]: piece["value"] for piece in pieces if piece}
        return {"kind": "datetime", "value": values["date"] + " " + values["time"]}
    if len(matches) != 1:
        return None
    match = matches[0]
    rest = _ENTITY.sub("", sentence[:match.start()] + sentence[match.end():])
    if any(char.isnumeric() for char in rest):
        return None
    literal = match.group()
    try:
        if "-" in literal:
            if ":" in literal:
                day = date.fromisoformat(literal[:10]).isoformat()
                clock = read_temporal_value(literal[11:])
                return {"kind": "datetime", "value": day + " " + clock["value"]} if clock else None
            return {"kind": "date", "value": date.fromisoformat(literal).isoformat()}
        compact = literal.lower().replace(" ", "")
        if compact.endswith(("am", "pm")):
            value_time = datetime.strptime(compact, "%I:%M:%S%p" if compact.count(":") == 2 else "%I:%M%p").time()
        else:
            parts = compact.split(":")
            value_time = time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) == 3 else 0)
        return {"kind": "time", "value": value_time.isoformat(timespec="seconds")}
    except ValueError:
        return None


async def async_read_temporal_value(hass: Any, sentence: str, language: str) -> dict[str, str] | None:
    """Read a literal value, else a timer duration from HA's strict HassStartTimer match.

    The duration is evidence only, never authority: it needs a named timer, only
    hours/minutes/seconds slots, and under 24 hours. Raises SentenceTriggerMatch
    when a custom sentence trigger owns the sentence.
    """
    source = read_temporal_value(sentence)
    if source is not None or not hass.states.async_entity_ids("timer"):
        return source
    from .intent_recognition import RecognitionUnavailable, SentenceTriggerMatch, async_recognize
    try:
        recognized = await async_recognize(hass, sentence, language, strict=True)
    except RecognitionUnavailable:
        return None
    result = getattr(recognized, "intent", None)
    if result is None or result.name != "HassStartTimer" or getattr(recognized, "unmatched_entities", None):
        return None
    if (getattr(recognized, "intent_metadata", None) or {}).get("hass_custom_sentence"):
        raise SentenceTriggerMatch
    slots = {key: entity.value for key, entity in recognized.entities.items()}
    # Assist also uses this intent for delayed commands and unnamed new timers.
    # Neither provides evidence for an immediate update of an existing helper.
    if slots.keys() - {"name", "hours", "minutes", "seconds"} or not isinstance(slots.get("name"), str) or not slots["name"].strip():
        return None
    parts = [slots.get(key, 0) for key in ("hours", "minutes", "seconds")]
    if any(type(value) is not int or value < 0 for value in parts):
        return None
    total = parts[0] * 3600 + parts[1] * 60 + parts[2]
    if not 0 < total < 24 * 3600:
        return None
    return {"kind": "duration", "value": f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"}


def explicit_timer_duration(sentence: str, source: dict) -> bool:
    """A clock with only two fields is ambiguous as a duration; require all three."""
    if source.get("kind") != "time":
        return False
    matches = list(_VALUE.finditer(sentence))
    return len(matches) == 1 and re.fullmatch(r"[0-9]{1,2}:[0-9]{2}:[0-9]{2}", matches[0].group()) is not None


def valid_temporal_value(kind: str, value: Any, timezone: str | None = None) -> bool:
    """Require canonical values and refuse nonexistent or ambiguous local datetimes."""
    if not isinstance(value, str):
        return False
    try:
        if kind == "date":
            return date.fromisoformat(value).isoformat() == value
        if kind in ("time", "duration"):
            parsed_time = time.fromisoformat(value)
            return (parsed_time.tzinfo is None and parsed_time.isoformat(timespec="seconds") == value
                    and (kind != "duration" or parsed_time != time.min))
        if kind != "datetime":
            return False
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None or parsed.isoformat(sep=" ", timespec="seconds") != value:
            return False
        if timezone is not None:
            zone = ZoneInfo(timezone)
            first, second = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset():
                return False
            if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != parsed:
                return False
        return True
    except (ValueError, ZoneInfoNotFoundError):
        return False


def temporal_supports(state: Any, intent: str) -> bool:
    spec = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(intent)
    if spec is None or state.domain != spec[0]:
        return False
    if state.domain != "input_datetime":
        return True
    kind = spec[3]
    return ((kind == "time" or state.attributes.get("has_date") is True)
            and (kind == "date" or state.attributes.get("has_time") is True))


def temporal_arguments(proposal: dict) -> dict | None:
    from .const import COMMAND_ROUTING_PROOF_FIELDS
    spec = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(proposal.get("intent", ""))
    if spec is None:
        return None
    domain, service, field, kind = spec
    ids = proposal.get("entity_ids")
    if (set(proposal) - COMMAND_ROUTING_PROOF_FIELDS != {"intent", "entity_ids", "value"} or not isinstance(ids, list) or len(ids) != 1
            or not isinstance(ids[0], str) or not ids[0].startswith(domain + ".")
            or not valid_temporal_value(kind, proposal.get("value"))):
        return None
    return {"domain": domain, "service": service, "entity_id": ids, "service_data": {field: proposal["value"]}}


def temporal_value_current(proposal: dict, state: Any, timezone: str) -> bool:
    """Check the effective local datetime when updating one field of a combined helper."""
    spec = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(proposal.get("intent", ""))
    if spec is None or state is None or not valid_temporal_value(spec[3], proposal.get("value"), timezone):
        return False
    if (state.domain != "input_datetime" or spec[3] == "datetime"
            or not (state.attributes.get("has_date") and state.attributes.get("has_time"))):
        return True
    try:
        existing = datetime.fromisoformat(state.state)
        value = proposal["value"]
        day = value if spec[3] == "date" else existing.date().isoformat()
        clock = value if spec[3] == "time" else existing.time().isoformat(timespec="seconds")
        return valid_temporal_value("datetime", day + " " + clock, timezone)
    except (TypeError, ValueError):
        return False


def temporal_state_matches(proposal: dict, state: Any, timezone: str) -> bool:
    """Compare only the requested helper field, never infer a scheduled action."""
    spec = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(proposal.get("intent", ""))
    if spec is None or not temporal_arguments(proposal):
        return False
    kind = spec[3]
    expected = proposal["value"]
    try:
        if state.domain == "timer":
            def seconds(value: str) -> int:
                parts = [int(part) for part in value.split(":")]
                if len(parts) != 3:
                    raise ValueError("Invalid timer duration")
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            duration = seconds(expected)
            end = datetime.fromisoformat(state.attributes.get("finishes_at", ""))
            remaining = (end - datetime.now(UTC)).total_seconds()
            return (state.state == "active" and seconds(state.attributes.get("duration", "")) == duration
                    and abs(remaining - duration) <= COMMAND_ROUTING_TIMER_OBSERVATION_TOLERANCE_SECONDS)
        if state.domain == "input_datetime":
            if kind == "date":
                actual = date(state.attributes["year"], state.attributes["month"], state.attributes["day"]).isoformat()
            elif kind == "time":
                actual = time(state.attributes["hour"], state.attributes["minute"], state.attributes["second"]).isoformat()
            else:
                actual = state.state
            return actual == expected
        if state.domain == "datetime":
            actual_dt = datetime.fromisoformat(state.state)
            return (actual_dt.tzinfo is not None and actual_dt ==
                    datetime.fromisoformat(expected).replace(tzinfo=ZoneInfo(timezone)))
        return state.state == expected
    except (ValueError, KeyError, TypeError, ZoneInfoNotFoundError):
        return False
