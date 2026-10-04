"""Pin the counts the documentation states against the code.

Counts embedded in prose rot silently: the sentence still reads fine and only
the number is wrong. The checks are mechanical, the written-out number list is
generated so it has no gaps, and the counts come from
`mcp_view.tool_catalog_counts()` (also served by the admin info endpoint),
never re-derived here.

The localized READMEs restate the headline counts and have their own patterns,
because the English one cannot match a CJK noun (NUMBER_ZH, NUMBER_JA).
Written-out numbers below nine are unchecked, since prose legitimately counts
small subsets; "hundred"/"thousand" near a counted noun fails, so a large
total must be written in digits.
"""

from __future__ import annotations

import html
import pathlib
import re

import pytest

from custom_components.phoenix_mcp.const import AGENTCLI_PROVIDERS, CAPABILITY_NAMES
from custom_components.phoenix_mcp.mcp_view import tool_catalog_counts
from custom_components.phoenix_mcp.personas import PERSONA_DEFINITIONS

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"

# Every written-out number nine through ninety-nine, generated so there are no
# gaps. One through eight stay unlisted on purpose (subset counts); anything
# larger than ninety-nine must be written in digits, enforced by the
# hundred/thousand tripwire below.
_UNITS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
          "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
WORDS: dict[str, int] = {"nine": 9}
for _i, _teen in enumerate(_TEENS):
    WORDS[_teen] = 10 + _i
for _ti, _ten in enumerate(_TENS):
    WORDS[_ten] = 20 + 10 * _ti
    for _ui, _unit in enumerate(_UNITS, start=1):
        WORDS[f"{_ten}-{_unit}"] = 20 + 10 * _ti + _ui

_NOUNS = r"(tools?|capabilit\w*|personas?|switches|providers?)"
# Longest first: a regex alternation is leftmost-first, so "twenty" would
# otherwise shadow "twenty-five".
_WORD_ALT = "|".join(sorted(WORDS, key=len, reverse=True))
# The lookbehind keeps digits embedded in identifiers (a model name like
# claude-opus-4-8, a version like 1.0.23) from reading as prose counts. The
# window excludes quotes as well as sentence/tag boundaries: a real count and
# its noun never straddle an HTML attribute boundary.
NUMBER = re.compile(
    r'(?<![\w.-])\b(' + _WORD_ALT + r'|\d{1,3})\b[^.<",;:)]{0,45}?\b' + _NOUNS + r"\b",
    re.IGNORECASE,
)
# The same count with the noun FIRST ("tools: 139", "capabilities (26)"), which
# NUMBER cannot see. The window is DELIBERATELY far tighter than NUMBER's 45
# characters: a wide one sweeps up every number that merely follows the noun
# ("capability returns 400" is not a count of 400). Only light punctuation may
# sit between the two.
NUMBER_AFTER_NOUN = re.compile(
    r'(?<![\w.-])\b' + _NOUNS + r"\b[\s:=(\[]{0,3}(" + _WORD_ALT + r"|\d{1,3})\b",
    re.IGNORECASE,
)
# A count with its noun ELIDED: "plus 116 more", "and 23 others". Neither
# pattern above has a noun to anchor to, so this matches on the quantifier and
# checks the VALUE against the counts the page may state. Deliberately narrow:
# only phrasings that always mean a count, so an ordinary sentence containing a
# number cannot trip it.
ELIDED_COUNT = re.compile(
    r"(?<![\w.-])\b(?:plus|and|another|a further)\s+(" + _WORD_ALT + r"|\d{1,3})\b\s+"
    r"(?:more|others|further|additional)\b",
    re.IGNORECASE,
)
# A count too large for the word list must be digits; "one hundred and
# twenty-seven tools" is unparseable to this test and therefore forbidden.
BIG_WORD = re.compile(
    r"\b(hundred|thousand)\b[^.<]{0,45}?\b" + _NOUNS + r"\b", re.IGNORECASE
)

# The same counts in the README's Simplified Chinese section. It needs its own
# pattern: `\b` sits between a word and a non-word character and every CJK
# character is a word character, so "131 个工具" has no boundary before the noun
# and the pattern above matches nothing. Translated counts are always digits, so
# the window between the number and its noun is deliberately tight.
_NOUNS_ZH = r"(工具|能力|角色|服务商)"
NUMBER_ZH = re.compile(r"(?<![\w.-])(\d{1,3})\s*[个种项]?\s*" + _NOUNS_ZH)
# Maps each Chinese noun onto the English key its allowed values are filed under.
_ZH_KIND = {"工具": "tool", "能力": "capabilit", "角色": "persona", "服务商": "provider"}

_NOUNS_JA = r"(ツール|機能|ペルソナ|プロバイダー)"
NUMBER_JA = re.compile(r"(?<![\w.-])(\d{1,3})\s*個?の?\s*" + _NOUNS_JA)
_JA_KIND = {"ツール": "tool", "機能": "capabilit", "ペルソナ": "persona", "プロバイダー": "provider"}


def _pages() -> list[pathlib.Path]:
    # The READMEs carry the same headline counts as the docs site.
    return sorted(DOCS.glob("*.html")) + sorted(DOCS.parent.glob("README*.md"))


def test_docs_directory_is_present() -> None:
    """So the sweep below cannot pass by finding nothing to read."""
    assert len(_pages()) > 5


def test_agent_chat_provider_cards_are_alphabetical() -> None:
    """The provider cards on the Agent Chat page stay alphabetical and complete."""
    html = (DOCS / "agentcli.html").read_text(encoding="utf-8")
    directory = re.search(
        r'<div class="grid-2 provider-directory">(.*?)<h2>Using the chat</h2>',
        html,
        re.DOTALL,
    )
    assert directory is not None
    names = re.findall(r"<h3>([^<]+)</h3>", directory.group(1))
    assert len(names) == len(AGENTCLI_PROVIDERS)
    assert names == sorted(names, key=str.casefold)


def test_word_list_has_no_gaps() -> None:
    """Every value 9..99 must be representable, so no written-out count is invisible."""
    assert sorted(WORDS.values()) == list(range(9, 100))


def _stale_counts(text: str, name: str = "fixture") -> list[str]:
    """Check visible prose; native and additional totals are not interchangeable."""
    counts = tool_catalog_counts()
    allowed = {
        "tool": {counts["total"]},
        "capabilit": {len(CAPABILITY_NAMES)},
        "persona": {len(PERSONA_DEFINITIONS)},
        "switches": {len(CAPABILITY_NAMES)},
        "provider": {len(AGENTCLI_PROVIDERS)},
    }

    # Remove non-prose first, preserving block boundaries while joining inline
    # emphasis. Numbers in attributes or scripts are not visible count claims.
    text = re.sub(r"<!--.*?-->|<(script|style)\b[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    text = re.sub(r"</?(?:p|div|tr|td|th|li|h[1-6]|section|article|br)\b[^>]*>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    stale: list[str] = []
    for lineno, line in enumerate(text.split("\n"), 1):
        for big in BIG_WORD.finditer(line):
            stale.append(
                f"{name}:{lineno} says {big.group(0)!r};"
                " write the count in digits so this test can check it"
            )
        for pattern, num_group, noun_group in (
            (NUMBER, 1, 2),
            (NUMBER_AFTER_NOUN, 2, 1),
        ):
            for match in pattern.finditer(line):
                raw = match.group(num_group).lower()
                noun = match.group(noun_group).lower()
                value = WORDS.get(raw)
                if value is None:
                    value = int(raw)
                kind = next(k for k in allowed if noun.startswith(k))
                expected = allowed[kind]
                if kind == "tool":
                    claim = match.group(0).lower()
                    prefix = line[max(0, match.start() - 20):match.start()].lower()
                    if "native" in claim or re.search(r"native\s+$", prefix):
                        expected = {counts["native"]}
                    elif re.search(r"\b(additional|more|phoenix)\b", claim) or re.search(r"(?:additional|phoenix)\s+$", prefix):
                        expected = {counts["additional"]}
                if value not in expected:
                    stale.append(
                        f"{name}:{lineno} says {match.group(0)!r};"
                        f" {kind} should be one of {sorted(expected)}"
                    )
        for match in ELIDED_COUNT.finditer(line):
            raw = match.group(1).lower()
            value = WORDS.get(raw, None)
            if value is None:
                value = int(raw)
            # No noun to key on; the only count prose elides is the additional
            # tools total, so any other value is stale.
            every = {counts["additional"]}
            if value not in every:
                stale.append(
                    f"{name}:{lineno} says {match.group(0)!r} with the noun left"
                    f" out; that count matches none of {sorted(every)}"
                )
        for match in NUMBER_ZH.finditer(line):
            kind = _ZH_KIND[match.group(2)]
            if int(match.group(1)) not in allowed[kind]:
                stale.append(
                    f"{name}:{lineno} says {match.group(0)!r};"
                    f" {kind} should be one of {sorted(allowed[kind])}"
                )
        for match in NUMBER_JA.finditer(line):
            kind = _JA_KIND[match.group(2)]
            if int(match.group(1)) not in allowed[kind]:
                stale.append(
                    f"{name}:{lineno} says {match.group(0)!r};"
                    f" {kind} should be one of {sorted(allowed[kind])}"
                )
    return stale


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_no_stale_counts_in_prose(page: pathlib.Path) -> None:
    assert _stale_counts(page.read_text(encoding="utf-8"), page.name) == []


@pytest.mark.parametrize("claim", [
    "a catalog of {native} tools",
    "<strong>{wrong}</strong> tools",
    "<strong>{native}</strong> additional tools",
    "<strong>{additional}</strong> native tools",
    "<strong>{wrong_caps}</strong> opt-in switches",
])
def test_count_guard_rejects_role_confusion_and_markup(claim: str) -> None:
    counts = tool_catalog_counts()
    assert _stale_counts(claim.format(**counts, wrong=counts["total"] + 1,
                                     wrong_caps=len(CAPABILITY_NAMES) + 1))


def test_count_guard_accepts_current_roles_and_ignores_attributes() -> None:
    counts = tool_catalog_counts()
    assert _stale_counts(
        f'<p>Catalog of <strong>{counts["total"]}</strong> tools.</p>'
        f'<p>{counts["native"]} native tools plus {counts["additional"]} more.</p>'
        '<div title="900 tools"><script>const caption = "800 tools";</script></div>'
    ) == []
