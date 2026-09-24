"""Compare model percentages with bounded source text, without interpreting commands."""
from __future__ import annotations

from functools import lru_cache
import re

from .const import (
    DECISION_PERCENTAGE_EVIDENCE_LANGUAGES, DECISION_PERCENTAGE_EVIDENCE_MODIFIERS,
    MAX_SEARCH_QUERY_LEN,
)

_TOKENS = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def has_percentage_unit(sentence: str) -> bool:
    """Detect percentage notation without treating it as an action or valid value."""
    tokens = [match.group().lower() for match in _TOKENS.finditer(sentence)]
    return any(token in ("%", "percent") or tokens[index:index + 2] == ["per", "cent"]
               for index, token in enumerate(tokens))


@lru_cache(maxsize=1)
def _number_words() -> tuple[dict[str, int], frozenset[str]]:
    """Use the number spelling dependency already used by HA's Hassil recognizer."""
    from unicode_rbnf import RbnfEngine

    engine = RbnfEngine.for_language("en")
    values = {str(value): value for value in range(101)}
    for value in range(101):
        for spelling in engine.format_number(value).text_by_ruleset.values():
            normalized = " ".join(spelling.lower().replace("-", " ").split())
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
        values, words = _number_words()
    except (ImportError, LookupError, ValueError):
        return None
    tokens = [match.group().lower() for match in _TOKENS.finditer(sentence)]
    units = [index for index, token in enumerate(tokens)
             if token in ("%", "percent") or (token == "per" and tokens[index:index + 2] == ["per", "cent"])]
    if len(units) != 1:
        return None
    end = units[0]
    start = end
    numeric_words = words | DECISION_PERCENTAGE_EVIDENCE_MODIFIERS
    while start and (tokens[start - 1] in numeric_words | {"and", "-", "+", ".", "/", "\u2212"}
                     or any(char.isnumeric() for char in tokens[start - 1])):
        start -= 1
    number = tokens[start:end]
    if not number:
        return None
    # Hyphens are spelling separators only between number words, never signs or ranges.
    for index, token in enumerate(number):
        if token == "-" and (index == 0 or index == len(number) - 1
                             or number[index - 1] not in words or number[index + 1] not in words):
            return None
    normalized = " ".join(token for token in number if token != "-")
    value = values.get(normalized)
    if value is None:
        return None
    # A second number may be a competing value, a fraction, a conversion or another device.
    # Do not guess which one matters, even if the model confidently picks this percentage.
    if any(token in numeric_words or any(char.isnumeric() for char in token)
           for index, token in enumerate(tokens) if not start <= index < end):
        return None
    return value


def compare_percentage(source_value: int | None, proposed_value: int) -> dict:
    """Expose a fixed diagnostic without retaining source text or granting authority."""
    if source_value is None:
        return {"status": "unverified"}
    return {"status": "match" if source_value == proposed_value else "mismatch", "source_value": source_value}
