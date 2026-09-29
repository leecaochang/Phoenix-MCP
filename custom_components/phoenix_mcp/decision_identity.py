"""Independent literal identity evidence for single-entity decision writes."""
from __future__ import annotations

import re
import unicodedata

from .const import (
    COMMAND_ROUTING_READ_INTENTS, DECISION_TARGET_MENTION_LIMIT,
    DECISION_UNSEGMENTED_TARGET_LANGUAGES,
)


def _normalized(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFC', text).casefold().split())


def explicit_write_target(sentence: str, targets: dict, proposal: dict, language: str) -> bool:
    """Require a unique literal identity; this is not complete intent recognition.

    The model must still reject negation, modifiers and multiple operations. Reads
    and independently reviewed conversation references have separate contracts.
    Do not fuzzy-match, translate names, or turn area metadata into entity names.
    """
    if proposal['intent'] in COMMAND_ROUTING_READ_INTENTS:
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
    for descriptor in targets.values():
        names = [descriptor['entity_id'], descriptor.get('name', ''), *descriptor.get('aliases', [])]
        for name in names:
            literal = _normalized(name)
            if not literal or re.search(r'<redacted(?:-entity)?>', literal):
                continue
            # Entity IDs always need boundaries, including in unsegmented speech.
            boundary = not unsegmented or name == descriptor['entity_id']
            pattern = (r'(?<![\w.])' if boundary else '') + re.escape(literal) + (r'(?![\w]|\.[\w])' if boundary else '')
            for match in re.finditer(pattern, text):
                mentions.add((match.start(), match.end(), descriptor['entity_id']))
                if len(mentions) > DECISION_TARGET_MENTION_LIMIT:
                    return False
    # "Desk lamp" must not independently match the shorter alias "Desk".
    maximal = {entity_id for start, end, entity_id in mentions
               if not any(other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
                          for other_start, other_end, _ in mentions)}
    return maximal == set(ids)
