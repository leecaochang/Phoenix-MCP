"""Record which English each translated Guide page was made from, and report the drift.

The build already holds a translated page to English's sections and place in the
contents, but not to its wording: a page translated before its English was
rewritten keeps describing the old behaviour. This stamps each translated page
with "<english>/<translation>", fingerprints of the English page's title and body
and of the translation, in tests/contract/guide_i18n_source_hashes.json; a drift
test names every page whose English moved since.

Stamping refuses a page whose English moved while its translation did not, the
shortcut that marks a stale page as reviewed. An English-only edit that needs no
new translation passes with --allow-unchanged. Header fields such as keywords and
covers do not count: only what a reader reads.

Run: python scripts/guide_i18n_stamp.py zh-Hans
     python scripts/guide_i18n_stamp.py --all
     python scripts/guide_i18n_stamp.py --check --all
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TOPICS = REPO / "frontend_src" / "guide" / "topics"
BASELINE = REPO / "tests" / "contract" / "guide_i18n_source_hashes.json"
HEADER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
COMMENT = (
    "Fingerprints of the English Guide page each translated page was made from, and of that translation. "
    "Written by scripts/guide_i18n_stamp.py, read by tests/test_guide_i18n_drift.py."
)


def digest(text: str) -> str:
    """Short, stable fingerprint of a page's readable text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def readable(source: str) -> str | None:
    """A topic file's title and body, the part a reader reads; None when it has no header."""
    match = HEADER.match(source.replace("\r\n", "\n"))
    if not match:
        return None
    title = json.loads(match.group(1)).get("title", "")
    return f"{title}\n{match.group(2).strip()}"


def pages(language: str) -> dict[str, str]:
    """A language's pages by topic id: their readable text."""
    found: dict[str, str] = {}
    folder = TOPICS / language
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        text = readable(path.read_text(encoding="utf-8"))
        if text is not None:
            found[path.stem] = text
    return found


def locales() -> list[str]:
    """Every translated Guide language: each topic folder but English."""
    return sorted(p.name for p in TOPICS.iterdir() if p.is_dir() and p.name != "en")


def load_baseline() -> dict[str, dict[str, str]]:
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8")).get("locales", {})


def write_baseline(baseline: dict[str, dict[str, str]]) -> None:
    BASELINE.write_text(
        json.dumps({"_comment": COMMENT, "locales": dict(sorted(baseline.items()))}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def audit(english: dict[str, str], locale: dict[str, str], stamped: dict[str, str]) -> dict[str, list[str]]:
    """Classify a language's pages against their stamps. Pure, so it can be tested directly.

    stale: the English page moved since it was translated.
    unstamped: translated but never fingerprinted.
    orphaned: a stamp whose page is gone from the language or from English.
    """
    return {
        "stale": sorted(p for p in locale if p in english and p in stamped
                        and stamped[p].split("/", 1)[0] != digest(english[p])),
        "unstamped": sorted(p for p in locale if p in english and p not in stamped),
        "orphaned": sorted(p for p in stamped if p not in locale or p not in english),
    }


def unchanged_translations(english: dict[str, str], locale: dict[str, str], stamped: dict[str, str]) -> list[str]:
    """Pages whose English moved since their stamp while their translation stayed the same. Pure."""
    return sorted(
        p for p in locale if p in english and p in stamped and "/" in stamped[p]
        and stamped[p].split("/", 1)[0] != digest(english[p]) and stamped[p].split("/", 1)[1] == digest(locale[p])
    )


def check(language: str) -> int:
    """Report drift for one language. 0 when its stamps and the English agree."""
    problems = audit(pages("en"), pages(language), load_baseline().get(language, {}))
    for kind, label in (("stale", "English changed since these were translated"),
                        ("unstamped", "translated but never stamped"),
                        ("orphaned", "stamped but no longer translated, or gone from English")):
        if problems[kind]:
            print(f"{language}: {label}: {', '.join(problems[kind])}")
    return 1 if any(problems.values()) else 0


def stamp(language: str, allow_unchanged: bool = False) -> int:
    """Fingerprint every page this language translates, refusing unchanged translations of moved English."""
    english, locale = pages("en"), pages(language)
    baseline = load_baseline()
    unchanged = unchanged_translations(english, locale, baseline.get(language, {}))
    if unchanged and not allow_unchanged:
        print(f"REFUSED {language}: English changed but these pages did not: {', '.join(unchanged)}")
        print("Translate them, or use --allow-unchanged for an English-only edit.")
        return 1
    baseline[language] = {p: f"{digest(english[p])}/{digest(text)}" for p, text in sorted(locale.items()) if p in english}
    write_baseline(baseline)
    print(f"stamped {len(baseline[language])} pages for {language}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    targets = locales() if "--all" in args else [a for a in args if not a.startswith("--")]
    if not targets:
        if "--all" in args:
            return 0  # no translated Guide yet: nothing to stamp or check
        print(__doc__)
        return 2
    if "--check" in args:
        return max(check(t) for t in targets)
    return max(stamp(t, "--allow-unchanged" in args) for t in targets)


if __name__ == "__main__":
    sys.exit(main())
