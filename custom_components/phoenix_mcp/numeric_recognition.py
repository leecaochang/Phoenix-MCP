"""Strict built-in numeric sentences with literal, scoped entity-ID targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .command_operations import numeric_operation
from .const import COMMAND_ROUTING_NUMERIC_OPERATIONS, COMMAND_ROUTING_NUMERIC_MATCH_LIMIT


@dataclass(frozen=True)
class NumericIdMatch:
    """A complete grammar match, not execution authority or a model prediction."""

    intent: str
    entity_id: str
    value: int | float


def recognize_numeric_ids(sentence: str, language: str, targets: tuple[str, ...]) -> NumericIdMatch | None:
    """Run in the owning recognition worker with no HA state or service access.

    Use installed built-in templates only. Target values are literal IDs, never
    wildcard names or model-selected aliases. Original text spans are mandatory:
    older parsers without them retain ordinary-provider fallback.
    """
    from hassil import Intents, recognize_all
    from hassil.intents import SlotList, TextSlotList
    from home_assistant_intents import get_intents

    raw = get_intents(language)
    if not raw:
        return None
    intents = Intents.from_dict({**raw, "intents": {
        name: data for name, data in raw["intents"].items() if name in COMMAND_ROUTING_NUMERIC_OPERATIONS
    }})
    lists: dict[str, SlotList] = {
        "name": TextSlotList.from_tuples(
            [(entity_id, entity_id, {"domain": entity_id.split(".", 1)[0]}) for entity_id in targets],
            allow_template=False,
        ),
        "area": TextSlotList.from_tuples([], allow_template=False),
        "floor": TextSlotList.from_tuples([], allow_template=False),
    }
    matched = None
    for index, result in enumerate(recognize_all(
        sentence, intents, slot_lists=lists, language=language, allow_unmatched_entities=False,
    )):
        if index >= COMMAND_ROUTING_NUMERIC_MATCH_LIMIT:
            return None
        operation = numeric_operation(result.intent.name)
        if (operation is None or result.unmatched_entities
                or set(result.entities) - {"name", operation.slot, "domain"}):
            return None
        name = result.entities.get("name")
        number = result.entities.get(operation.slot)
        if name is None or number is None or name.value not in targets:
            return None
        if (name.is_wildcard or number.is_wildcard
                or not _literal_span(sentence, name, name.value)
                or not _literal_span(sentence, number)
                or not operation.valid_value(number.value)
                or not name.value.startswith(operation.domain + ".")):
            return None
        domain = result.entities.get("domain")
        if domain is not None and domain.value != operation.domain:
            return None
        candidate = NumericIdMatch(result.intent.name, name.value, number.value)
        if matched is not None and candidate != matched:
            return None
        matched = candidate
    return matched


def _literal_span(sentence: str, entity: Any, literal: str | None = None) -> bool:
    span = getattr(entity, "text_span", None)
    if (not isinstance(span, tuple) or len(span) != 2
            or any(not isinstance(value, int) or isinstance(value, bool) for value in span)):
        return False
    start, end = span
    return (0 <= start < end <= len(sentence)
            and sentence[start:end] == (literal if literal is not None else entity.text))
