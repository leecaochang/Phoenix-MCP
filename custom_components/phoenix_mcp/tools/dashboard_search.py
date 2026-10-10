"""Card search over scrubbed dashboard layouts, for get_dashboard_config.

The tool scrubs each layout on the event loop first, so a hidden entity, an
embedded hidden entity ID, a sensitive key or a subtree past the depth bound is
already the redaction placeholder here: it can neither match nor appear in a
result. Paths address that scrubbed copy, whose retained keys and list indexes
are the stored layout's, so patch_dashboard accepts them unchanged.

A typed object (a mapping with a string type) under a card container key, as its
value, a list item or a mapping value, is a nested card matched on its own. Any
other typed object, such as a tile feature or an entity row, is part of the card
holding it. Cards inherit the heading of the nearest heading card at or before
their top-level card in the same list, and every match names its view and,
when nested, the types of the cards enclosing it, outermost first.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Collection, Iterator

from ..const import (
    DASHBOARD_CARD_CONTAINER_KEYS,
    DASHBOARD_SEARCH_CRITERION_MAX,
    DASHBOARD_SEARCH_MATCHED_VALUES,
    DASHBOARD_SEARCH_VALUE_CHARS,
    REDACTION_SENTINEL,
)
from ..helpers import _ENTITY_ID_TEXT_RE
from ..policy_engine import _ENTITY_ID_RE

_CRITERIA = ("query", "entity_id", "card_type", "heading")
_ENTITY_PATTERN_RE = re.compile(r"[a-z0-9_*]+\.[a-z0-9_*]+|\*")


@dataclass(frozen=True)
class Criteria:
    """Trimmed search criteria; every given one must match."""

    query: str | None = None
    entity_id: str | None = None
    card_type: str | None = None
    heading: str | None = None

    def entity_pattern(self) -> re.Pattern[str] | None:
        """A whole-ID pattern where `*` matches any run of characters."""
        if self.entity_id is None:
            return None
        return re.compile("".join(".*" if char == "*" else re.escape(char) for char in self.entity_id))


def parse_criteria(args: dict) -> tuple[Criteria | None, str | None]:
    """(criteria, None); (None, None) when none is given; or (None, error).

    A blank criterion counts as not given, so an optional string a model sends
    empty still means an ordinary read.
    """
    values: dict[str, str] = {}
    for name in _CRITERIA:
        raw = args.get(name)
        if raw is None:
            continue
        if not isinstance(raw, str):
            return None, f"{name} must be a string."
        text = raw.strip()
        if not text:
            continue
        if len(text) > DASHBOARD_SEARCH_CRITERION_MAX:
            return None, f"{name} must be at most {DASHBOARD_SEARCH_CRITERION_MAX} characters."
        values[name] = text
    if not values:
        return None, None
    if "entity_id" in values:
        values["entity_id"] = values["entity_id"].lower()
        if not _ENTITY_PATTERN_RE.fullmatch(values["entity_id"]):
            return None, (
                "entity_id must be an entity ID such as light.kitchen; "
                "* matches any run of characters, as in light.* or *.kitchen_*."
            )
    if values.get("card_type") == "badge" and "heading" in values:
        return None, "Badges have no heading, so card_type badge cannot be combined with heading."
    return Criteria(**values), None


@dataclass(frozen=True)
class _Item:
    path: list
    kind: str
    node: Any
    view: str | None
    heading: str | None
    strings: list[tuple[list, str]]
    entity_keys: list[str]
    within: tuple[str, ...] = ()


def _is_card(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("type"), str) and bool(value["type"].strip())


def _split(node: Any) -> tuple[list[tuple[list, str]], list[str], list[tuple[list, dict]]]:
    """A card's own string values, its entity-ID keys and its nested cards, by relative path."""
    strings: list[tuple[list, str]] = []
    keys: list[str] = []
    nested: list[tuple[list, dict]] = []

    def visit(value: Any, rel: list) -> None:
        if isinstance(value, str):
            strings.append((rel, value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, [*rel, index])
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and _ENTITY_ID_RE.match(key):
                    keys.append(key)
                if key not in DASHBOARD_CARD_CONTAINER_KEYS:
                    visit(item, [*rel, key])
                elif _is_card(item):
                    nested.append(([*rel, key], item))
                elif isinstance(item, (list, dict)):
                    entries = enumerate(item) if isinstance(item, list) else item.items()
                    for child_key, child in entries:
                        if _is_card(child):
                            nested.append(([*rel, key, child_key], child))
                        else:
                            if isinstance(child_key, str) and _ENTITY_ID_RE.match(child_key):
                                keys.append(child_key)
                            visit(child, [*rel, key, child_key])
                else:
                    visit(item, [*rel, key])

    # The root itself is never nested; only what it holds can be.
    visit(node, [])
    return strings, keys, nested


def _heading_text(card: dict) -> str | None:
    text = card.get("heading")
    return text if isinstance(text, str) and text.strip() else None


def _cards(card: dict, path: list, view: str | None, heading: str | None) -> Iterator[_Item]:
    """A card and every card nested in it, in document order, with its enclosing card types."""
    pending: list[tuple[dict, list, tuple[str, ...]]] = [(card, path, ())]
    while pending:
        node, at, within = pending.pop()
        strings, keys, nested = _split(node)
        yield _Item(at, "card", node, view, heading, strings, keys, within)
        pending.extend((child, [*at, *rel], (*within, node["type"])) for rel, child in reversed(nested))


def _items(config: Any, warn: Callable[[list, str], None]) -> Iterator[_Item]:
    """Every card and badge a layout stores, reporting containers in the wrong shape."""

    def card_list(cards: Any, path: list, view: str | None) -> Iterator[_Item]:
        if not isinstance(cards, list):
            warn(path, "is not a list, so its cards were not searched")
            return
        heading = None
        for index, card in enumerate(cards):
            if card == REDACTION_SENTINEL:
                continue
            if not _is_card(card):
                warn([*path, index], "is not a card object")
                continue
            if card["type"] == "heading":
                heading = _heading_text(card)
            yield from _cards(card, [*path, index], view, heading)

    if not isinstance(config, dict):
        warn([], "the layout is not an object, so nothing was searched")
        return
    if "strategy" in config:
        warn([], "the dashboard is generated by a strategy and stores no cards")
        return
    views = config.get("views")
    if views is None:
        return
    if not isinstance(views, list):
        warn(["views"], "is not a list, so nothing was searched")
        return
    for view_index, view in enumerate(views):
        at = ["views", view_index]
        if view == REDACTION_SENTINEL:
            continue
        if not isinstance(view, dict):
            warn(at, "is not an object")
            continue
        if "strategy" in view:
            warn(at, "is generated by a strategy and stores no cards")
            continue
        label = next((view[key] for key in ("title", "path") if isinstance(view.get(key), str) and view[key]), None)
        badges = view.get("badges")
        if isinstance(badges, list):
            for index, badge in enumerate(badges):
                if badge == REDACTION_SENTINEL or not isinstance(badge, (str, dict)):
                    continue
                strings, keys, _ = _split(badge)
                yield _Item([*at, "badges", index], "badge", badge, label, None, strings, keys)
        elif badges is not None:
            warn([*at, "badges"], "is not a list, so its badges were not searched")
        header = view.get("header")
        if isinstance(header, dict) and header.get("card") is not None:
            if _is_card(header["card"]):
                yield from _cards(header["card"], [*at, "header", "card"], label, None)
            elif header["card"] != REDACTION_SENTINEL:
                warn([*at, "header", "card"], "is not a card object")
        elif header is not None and not isinstance(header, dict) and header != REDACTION_SENTINEL:
            warn([*at, "header"], "is not an object, so its card was not searched")
        # The frontend renders cards and sections whatever the view type says.
        if view.get("cards") is not None:
            yield from card_list(view["cards"], [*at, "cards"], label)
        sections = view.get("sections")
        if sections is None:
            continue
        if not isinstance(sections, list):
            warn([*at, "sections"], "is not a list, so its cards were not searched")
            continue
        for section_index, section in enumerate(sections):
            where = [*at, "sections", section_index]
            if section == REDACTION_SENTINEL:
                continue
            if not isinstance(section, dict):
                warn(where, "is not an object")
            elif section.get("cards") is not None:
                yield from card_list(section["cards"], [*where, "cards"], label)


def _folded(text: str) -> str:
    """Case-insensitive text where an underscore reads as a space, so "desk lamp" finds light.desk_lamp."""
    return text.casefold().replace("_", " ")


def _visible_parts(text: str) -> list[str]:
    """Text around placeholders, so hidden content never joins a match."""
    return [part for part in text.split(REDACTION_SENTINEL) if part]


def _entity_tokens(item: _Item, domains: Collection[str] | None) -> Iterator[str]:
    """Entity IDs a card names; with domains, only tokens in them are IDs."""
    for token in [token for _, value in item.strings for token in _ENTITY_ID_TEXT_RE.findall(value)] + item.entity_keys:
        if not all(part.isdecimal() for part in token.split(".")) and (
                domains is None or token.split(".", 1)[0] in domains):
            yield token


def _match(
    item: _Item, criteria: Criteria, entity: re.Pattern[str] | None, domains: Collection[str] | None,
) -> list[dict] | None:
    """The query values that matched (empty without a query), or None for no match."""
    if item.kind == "badge":
        if criteria.heading is not None or criteria.card_type not in (None, "badge"):
            return None
    else:
        if criteria.card_type is not None and item.node.get("type") not in (
            criteria.card_type, f"custom:{criteria.card_type}",
        ):
            return None
        if criteria.heading is not None and not any(
            criteria.heading.casefold() in part.casefold() for part in _visible_parts(item.heading or "")
        ):
            return None
    if entity is not None and not any(entity.fullmatch(token) for token in _entity_tokens(item, domains)):
        return None
    if criteria.query is None:
        return []
    wanted = _folded(criteria.query)
    matched = [
        {"path": rel, "value": value[:DASHBOARD_SEARCH_VALUE_CHARS]}
        for rel, value in item.strings
        if any(wanted in _folded(part) for part in _visible_parts(value))
    ]
    return matched[:DASHBOARD_SEARCH_MATCHED_VALUES] or None


def search_layout(
    config: Any, criteria: Criteria, entity_domains: Collection[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Every match in one scrubbed layout, in document order, and the shape problems met.

    entity_domains limits which dotted tokens count as entity IDs, matching the
    scrub that redacted hidden ones.
    """
    problems: list[dict] = []
    entity = criteria.entity_pattern()
    matches: list[dict] = []
    for item in _items(config, lambda path, problem: problems.append({"path": path, "problem": problem})):
        matched = _match(item, criteria, entity, entity_domains)
        if matched is None:
            continue
        match: dict[str, Any] = {"path": item.path, "kind": item.kind}
        if item.view is not None:
            match["view"] = item.view
        if isinstance(item.node, dict) and isinstance(item.node.get("type"), str):
            match["type"] = item.node["type"]
        if item.within:
            match["within"] = list(item.within)
        if item.heading is not None:
            match["heading"] = item.heading
        match["config"] = item.node
        if matched:
            match["matched"] = matched
        matches.append(match)
    return matches, problems
