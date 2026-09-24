"""Compare model percentages with bounded source text, without interpreting commands."""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

from .const import (
    DECISION_PERCENTAGE_EVIDENCE_LANGUAGES, DECISION_PERCENTAGE_EVIDENCE_MODIFIERS,
    DECISION_PERCENTAGE_EVIDENCE_CONNECTORS, DECISION_PERCENTAGE_UNITS, MAX_SEARCH_QUERY_LEN,
)

_TOKENS = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _tokens(sentence: str) -> list[str]:
    """Separate Han characters without normalizing signs or non-ASCII digits."""
    # Han has no word separators. Keep Latin words intact for HA number spellings.
    spaced = "".join(f" {char} " if '\u3400' <= char <= '\u9fff' else char
                     for char in unicodedata.normalize('NFC', sentence))
    return [match.group().lower() for match in _TOKENS.finditer(spaced)]


def _units(tokens: list[str]) -> list[tuple[int, int]]:
    return [(index, index + len(unit)) for index in range(len(tokens))
            for unit in DECISION_PERCENTAGE_UNITS if tuple(tokens[index:index + len(unit)]) == unit]


def has_percentage_unit(sentence: str) -> bool:
    """Detect percentage notation without treating it as an action or valid value."""
    return bool(_units(_tokens(sentence)))


@lru_cache(maxsize=3)
def _number_words(language: str) -> tuple[dict[str, int], frozenset[str]]:
    """Use the number spelling dependency already used by HA's Hassil recognizer."""
    from unicode_rbnf import RbnfEngine

    engine = RbnfEngine.for_language('zh' if language == 'zh-CN' else language)
    values = {str(value): value for value in range(101)}
    for value in range(101):
        spellings = engine.format_number(value).text_by_ruleset
        if language == 'zh-CN':
            # Calendar and financial spellings are outside the spoken-command subset.
            spellings = {key: text for key, text in spellings.items()
                         if key in ('spellout-cardinal', 'spellout-cardinal-alternate2')}
        for spelling in spellings.values():
            normalized = " ".join(_tokens(spelling.replace('-', ' ')))
            if normalized in values and values[normalized] != value:
                raise ValueError("Conflicting number spellings")
            values[normalized] = value
    words = frozenset(word for spelling in values for word in spelling.split() if word.isalpha())
    return values, words


def read_explicit_percentage(sentence: str, language: str) -> int | None:
    """Read one whole percentage; ambiguity or unsupported notation has no evidence.

    This does not decide whether the sentence is a command, whether it is negated,
    or which operation/device owns the value. It must never authorize execution.
    Run in the recognition executor: loading number spelling rules can perform I/O.
    """
    if (language not in DECISION_PERCENTAGE_EVIDENCE_LANGUAGES or not isinstance(sentence, str)
            or not sentence or len(sentence) > MAX_SEARCH_QUERY_LEN):
        return None
    try:
        values, words = _number_words(language)
    except (ImportError, LookupError, ValueError):
        return None
    tokens = _tokens(sentence)
    units = _units(tokens)
    if len(units) != 1:
        return None
    unit_start, unit_end = units[0]
    unit = tuple(tokens[unit_start:unit_end])
    prefix = unit == ('百', '分', '之')
    if prefix and language != 'zh-CN':
        return None
    if unit not in (('%',), ('％',), ('百', '分', '之')) and (
            (language == 'en' and unit not in (('percent',), ('per', 'cent')))
            or (language == 'es' and unit != ('por', 'ciento')) or language == 'zh-CN'):
        return None
    numeric_words = words | DECISION_PERCENTAGE_EVIDENCE_MODIFIERS[language]
    separators = DECISION_PERCENTAGE_EVIDENCE_CONNECTORS[language] | {'-', '+', '.', '/', '\u2212'}
    if language != 'en':
        separators = separators | {','}

    def number_part(token: str) -> bool:
        return token in numeric_words | separators or any(char.isnumeric() for char in token)

    if prefix:
        start = end = unit_end
        while end < len(tokens) and number_part(tokens[end]):
            end += 1
        # A sentence-ending ASCII period is punctuation, unlike a decimal point.
        if end == len(tokens) and tokens[end - 1:end] == ['.']:
            end -= 1
    else:
        start = end = unit_start
        while start and number_part(tokens[start - 1]):
            start -= 1
    number = tokens[start:end]
    if not number:
        return None
    # Hyphens are English spelling separators only, never signs or ranges.
    for index, token in enumerate(number):
        if token == '-' and (language != 'en' or index == 0 or index == len(number) - 1
                             or number[index - 1] not in words or number[index + 1] not in words):
            return None
    value = values.get(' '.join(token for token in number if token != '-'))
    if value is None:
        return None
    # Do not guess whether another number is a competing setting, time or device name.
    if any(token in numeric_words or any(char.isnumeric() for char in token)
           for index, token in enumerate(tokens)
           if not start <= index < end and not unit_start <= index < unit_end):
        return None
    return value


def compare_percentage(source_value: int | None, proposed_value: int) -> dict:
    """Expose a fixed diagnostic without retaining source text or granting authority."""
    if source_value is None:
        return {"status": "unverified"}
    return {"status": "match" if source_value == proposed_value else "mismatch", "source_value": source_value}
