"""Compare model percentages with bounded source text, without interpreting commands."""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

from .const import (
    DECISION_PERCENTAGE_EVIDENCE_LANGUAGES, DECISION_PERCENTAGE_EVIDENCE_MODIFIERS,
    DECISION_PERCENTAGE_EVIDENCE_CONNECTORS, DECISION_PERCENTAGE_UNITS, MAX_SEARCH_QUERY_LEN,
    DECISION_PERCENTAGE_LANGUAGE_UNITS, DECISION_PERCENTAGE_HYPHEN_LANGUAGES, DECISION_NUMBER_LANGUAGE_ALIASES,
)

_TOKENS = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _tokens(sentence: str) -> list[str]:
    """Separate Han characters without normalizing signs or non-ASCII digits."""
    # Han has no word separators. Keep Latin words intact for HA number spellings.
    spaced = "".join(f" {char} " if '\u3400' <= char <= '\u9fff' else char
                     for char in unicodedata.normalize('NFC', sentence))
    return [match.group().lower() for match in _TOKENS.finditer(spaced)]


def _units(tokens: list[str], language: str | None = None) -> list[tuple[int, int]]:
    phrases = (DECISION_PERCENTAGE_LANGUAGE_UNITS.get(language, ()) if language else
               tuple(phrase for phrases in DECISION_PERCENTAGE_LANGUAGE_UNITS.values() for phrase in phrases))
    units = set(DECISION_PERCENTAGE_UNITS if language is None else (("%",), ("％",)))
    units.update(tuple(_tokens(phrase)) for phrase in phrases)
    return [(index, index + len(unit)) for index in range(len(tokens))
            for unit in units if tuple(tokens[index:index + len(unit)]) == unit]


def has_percentage_unit(sentence: str) -> bool:
    """Detect percentage notation without treating it as an action or valid value."""
    return bool(_units(_tokens(sentence)))


@lru_cache(maxsize=64)
def _number_words(language: str) -> tuple[dict[str, int], frozenset[str]]:
    """Use the number spelling dependency already used by HA's Hassil recognizer."""
    from unicode_rbnf import RbnfEngine

    values = {str(value): value for value in range(101)}
    try:
        engine = RbnfEngine.for_language(DECISION_NUMBER_LANGUAGE_ALIASES.get(language, language))
    except ValueError:
        # Some installed Assist languages lack RBNF rules; literal digits remain usable.
        return values, frozenset()

    def spellings(value: int | float) -> list[str]:
        try:
            formatted = engine.format_number(value).text_by_ruleset
        except (ValueError, ArithmeticError, IndexError):
            return []
        return [text for key, text in formatted.items()
                if (key == 'spellout-numbering' or key.startswith('spellout-cardinal'))
                and not any(kind in key for kind in ('financial', 'calendar', 'ordinal', 'year'))]

    for value in range(101):
        for spelling in spellings(value):
            normalized = " ".join(_tokens(spelling.replace('-', ' ')))
            if normalized in values and values[normalized] != value:
                raise ValueError("Conflicting number spellings")
            values[normalized] = value
    words = {word for spelling in values for word in spelling.split() if word.isalpha()}
    # Generated negative, decimal and magnitude words block suffix extraction.
    for probe in (-1, -37, 0.5, 37.5, 101, 137, 200, 1000, 1000000):
        for spelling in spellings(probe):
            words.update(word for word in _tokens(spelling.replace('-', ' ')) if word.isalpha())
    return values, frozenset(words)


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
    # Japanese percent suffixes can adjoin kanji or ASCII digits without spaces.
    if language == 'ja':
        sentence = sentence.replace('パーセント', ' パーセント ')
    if language == 'ko':
        sentence = re.sub(r'(퍼센트|프로)(로)(?=\s|$|[.!?])', r'\1 \2', sentence)
    tokens = _tokens(sentence)
    units = _units(tokens, language)
    if len(units) != 1:
        return None
    unit_start, unit_end = units[0]
    unit = tuple(tokens[unit_start:unit_end])
    prefix = unit == ('百', '分', '之')
    if prefix and language not in ('zh-CN', 'zh-TW', 'zh-HK'):
        return None
    numeric_words = words | DECISION_PERCENTAGE_EVIDENCE_MODIFIERS.get(language, frozenset())
    separators = DECISION_PERCENTAGE_EVIDENCE_CONNECTORS.get(language, frozenset()) | {'-', '+', '.', '/', '\u2212'}
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
        if token == '-' and (language not in DECISION_PERCENTAGE_HYPHEN_LANGUAGES or index == 0 or index == len(number) - 1
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


def read_explicit_scalar(sentence: str, language: str) -> int | float | None:
    """Extract one literal scalar, never interpret a command, date, time or unit.

    Spelled percentages retain their independent reader. Other spelled numbers
    can still use strict Assist matching; uncertain notation falls back.
    """
    from .command_operations import finite_number

    if not isinstance(sentence, str) or not sentence or len(sentence) > MAX_SEARCH_QUERY_LEN:
        return None
    # Preserve signs and non-ASCII digits; reject grouped, scientific and clock/date forms.
    matches = list(re.finditer(r"[+\-\u2212]?[0-9]+(?:[.,][0-9]+)?", sentence))
    if len(matches) != 1:
        return None
    match = matches[0]
    rest = sentence[:match.start()] + " " + sentence[match.end():]
    if any(char.isnumeric() for char in rest):
        return None
    # Scalar fallback must not undo a percentage refusal by dropping a spelled sign,
    # fraction, magnitude or competing number from the surrounding request.
    try:
        _, words = _number_words(language)
    except (ImportError, LookupError, ValueError):
        return None
    tokens = _tokens(rest)
    unit_tokens = {index for start, end in _units(tokens, language) for index in range(start, end)}
    forbidden = words | DECISION_PERCENTAGE_EVIDENCE_MODIFIERS.get(language, frozenset()) | {'+', '-', '\u2212', '/'}
    if any(token in forbidden for index, token in enumerate(tokens) if index not in unit_tokens):
        return None
    before, after = sentence[:match.start()], sentence[match.end():]
    if before[-1:].isascii() and before[-1:].isalpha():
        return None
    if (before[-1:] in ("/", ":", ".", ",", "-", "+", "_")
            or after[:1] in ("/", ":", ",", "_", "-", "+")
            or (after[:1] == "." and len(after) > 1)
            or re.match(r"[eE][+\-]?[0-9]", after)):
        return None
    literal = match.group().replace("\u2212", "-")
    try:
        if "." in literal or "," in literal:
            decimal = "," if "," in literal else "."
            # Three trailing digits may be a thousands group. Do not guess from language.
            if len(literal.split(decimal)[1]) == 3:
                return None
            literal = literal.replace(decimal, ".")
        value = float(literal) if "." in literal else int(literal)
    except (ValueError, LookupError):
        return None
    return value if finite_number(value) else None


def explicit_unit_matches(sentence: str, unit: str | None) -> bool:
    """Reject contradictory standard unit markers independently of classification.

    Absence of a marker is not unit evidence; the decision still has to verify
    native units and all other words in the complete request.
    """
    markers = re.findall(r"°\s*[CF]|℃|℉|%|％|\b(?:celsius|fahrenheit|kelvin)\b|(?<=[0-9])\s*K\b",
                         sentence, re.IGNORECASE)
    known = {"°c": "°C", "℃": "°C", "celsius": "°C", "°f": "°F", "℉": "°F",
             "fahrenheit": "°F", "kelvin": "K", "k": "K", "%": "%", "％": "%"}
    return all(known["".join(marker.split()).lower()] == unit for marker in markers)
