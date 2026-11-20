"""Independent literal identity evidence for single-entity decision writes."""
from __future__ import annotations

from collections.abc import Iterable
import re
import unicodedata

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .routing_tables import (
    COMMAND_ROUTING_UNPROVEN_INTENTS, DECISION_TARGET_MENTION_LIMIT,
    DECISION_UNSEGMENTED_TARGET_LANGUAGES,
)

# Latin letters and digits run together in unsegmented scripts, so a Latin name there
# still needs Latin boundaries: "Lamp" is not mentioned by "lamppost".
_LATIN = r'[0-9a-zÀ-ɏ]'


def _normalized(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFC', text).casefold().split())


def identity_competitors(hass: HomeAssistant, targets: dict) -> list[tuple[str, list[str]]]:
    """Entity ID, names and HA aliases of every Assist-exposed entity outside the catalog.

    Used only locally to refuse a catalog name nested in a longer name; never sent to a provider.
    Event loop only.
    """
    # Local: always loaded with Home Assistant; imported only when this path runs.
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    catalog = {descriptor['entity_id'] for descriptor in targets.values()}
    registry = er.async_get(hass)
    competitors = []
    for state in hass.states.async_all():
        entity_id = state.entity_id
        if entity_id in catalog or not async_should_expose(hass, 'conversation', entity_id):
            continue
        names = [entity_id]
        friendly = state.attributes.get('friendly_name')
        if isinstance(friendly, str):
            names.append(friendly)
        entry = registry.async_get(entity_id)
        if entry is not None:
            names.extend(name for name in (entry.name, entry.original_name) if isinstance(name, str))
            names.extend(alias for alias in er.async_get_entity_aliases(hass, entry) if isinstance(alias, str))
        competitors.append((entity_id, names))
    return competitors


def _mentions(text: str, entity_id: str, names: Iterable[str], unsegmented: bool) -> Iterable[tuple[int, int, str]]:
    for name in names:
        literal = _normalized(name)
        if not literal or literal not in text or re.search(r'<redacted(?:-entity)?>', literal):
            continue
        if not unsegmented or name == entity_id:
            # Entity IDs always need boundaries, including in unsegmented speech.
            pattern = r'(?<![\w.])' + re.escape(literal) + r'(?![\w]|\.[\w])'
        else:
            pattern = ((rf'(?<!{_LATIN})' if re.match(_LATIN, literal) else '') + re.escape(literal)
                       + (rf'(?!{_LATIN})' if re.match(_LATIN, literal[-1]) else ''))
        for match in re.finditer(pattern, text):
            yield match.start(), match.end(), entity_id


def explicit_write_target(sentence: str, targets: dict, proposal: dict, language: str,
                          competitors: Iterable[tuple[str, Iterable[str]]]) -> bool:
    """Require a unique literal identity for a write or a single-entity read; not intent recognition.

    `competitors` are the names of entities outside the catalog (`identity_competitors`); one that
    covers a catalog mention refuses it, but one with exactly the mention's name only when it shares
    the chosen target's domain. Never fuzzy-match, translate names, or use area metadata.
    """
    # Collection summaries and independently reviewed conversation references have separate contracts.
    if proposal['intent'] in COMMAND_ROUTING_UNPROVEN_INTENTS:
        return True
    ids = proposal.get('entity_ids', [])
    if len(ids) != 1:
        return False
    # A copied text value is data, not evidence of which helper was requested.
    if proposal['intent'] in ('PhoenixEntityTextValue', 'PhoenixEntityInputTextValue'):
        value = proposal.get('value')
        if isinstance(value, str) and value:
            sentence = sentence.replace(value, ' ')
    text = _normalized(sentence)
    unsegmented = language.replace('_', '-').split('-')[0] in DECISION_UNSEGMENTED_TARGET_LANGUAGES
    mentions: set[tuple[int, int, str]] = set()
    others: set[tuple[int, int, bool]] = set()
    target_domains = {entity_id.split('.', 1)[0] for entity_id in ids}
    for descriptor in targets.values():
        entity_id = descriptor['entity_id']
        for mention in _mentions(text, entity_id, [entity_id, descriptor.get('name', ''),
                                                   *descriptor.get('aliases', [])], unsegmented):
            mentions.add(mention)
            if len(mentions) > DECISION_TARGET_MENTION_LIMIT:
                return False
    for entity_id, names in competitors:
        same_domain = entity_id.split('.', 1)[0] in target_domains
        for start, end, _ in _mentions(text, entity_id, names, unsegmented):
            others.add((start, end, same_domain))
            if len(others) > DECISION_TARGET_MENTION_LIMIT:
                return False
    # "Desk lamp" must not independently match the shorter alias "Desk".
    maximal = {(start, end, entity_id) for start, end, entity_id in mentions
               if not any(other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
                          for other_start, other_end, _ in mentions)}
    # A name outside the catalog that overlaps a mention without lying inside it ("heater desk lamp"
    # around "desk lamp") leaves the identity unproven. The same name does too, unless the other
    # entity's domain cannot take this write: a garage door's contact sensor beside its cover.
    if any(other_start < end and start < other_end
           and not (start <= other_start and other_end <= end and (other_start, other_end) != (start, end))
           and ((other_start, other_end) != (start, end) or same_domain)
           for start, end, _ in maximal for other_start, other_end, same_domain in others):
        return False
    return {entity_id for _, _, entity_id in maximal} == set(ids)
