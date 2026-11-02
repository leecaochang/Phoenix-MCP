"""Record which English each locale was translated from, and report the drift.

Other i18n guards judge a locale against the current en.json, so a translation
made from English that has since been reworded passes all of them and keeps
stating the old behaviour. This stamps a fingerprint of the English behind each
translated key into tests/contract/i18n_source_hashes.json; when the English
moves, the stamp no longer matches and a drift test names the keys.

Each stamp is "<english>/<translation>": the fingerprint of the English and of
the translation made from it. Stamping refuses a key whose English moved while
its translation did not, the shortcut that marks a stale translation as
reviewed. An English-only edit that genuinely needs no new translation (a typo
fix) passes with --allow-unchanged, and still shows up as a line in the diff.
Only fingerprints are stored; the texts are in the catalogs' git history.
scripts/i18n_merge_locale.py stamps the keys it merges, so a locale written the
normal way needs nothing here.

Run: python scripts/i18n_source_stamp.py zh-Hans
     python scripts/i18n_source_stamp.py --all
     python scripts/i18n_source_stamp.py --all --allow-unchanged
     python scripts/i18n_source_stamp.py --check zh-Hans
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BASELINE = REPO / "tests" / "contract" / "i18n_source_hashes.json"

COMMENT = (
    "Fingerprints of the en.json value each locale key was translated from and of that translation. "
    "Written by scripts/i18n_source_stamp.py, read by tests/test_i18n_source_drift.py. "
    "A mismatch means the English was reworded and that locale now describes the old behaviour."
)


def _load_checker():
    """The sibling checker, by path: scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location(
        "i18n_check_locale", pathlib.Path(__file__).with_name("i18n_check_locale.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def digest(value: str) -> str:
    """Short, stable fingerprint of one English string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def locales() -> list[str]:
    """Every shipped locale. en.json is the source, strings.json is not a locale."""
    return checker.languages()


def catalog(name: str) -> dict[str, str]:
    """One language's whole catalog, flattened.

    Read through the checker so BOTH halves of the split catalog are included (see
    i18n_check_locale.SECTION_DIRS); reading only one would leave the other's keys
    stamped-but-absent, which this script reports as drift.
    """
    return checker.flatten(checker.load(name))


def source_digest(stamp: str) -> str:
    """The English half of a stamp; stamps written before translations were fingerprinted have only it."""
    return stamp.split("/", 1)[0]


def translation_digest(stamp: str) -> str | None:
    """The translation half of a stamp, or None when it was never recorded."""
    return stamp.split("/", 1)[1] if "/" in stamp else None


def unchanged_translations(
    english: dict[str, str], locale: dict[str, str], stamped: dict[str, str], keys: list[str]
) -> list[str]:
    """Keys whose English moved since their stamp while their translation stayed the same. Pure."""
    return sorted(
        k for k in keys
        if k in stamped and source_digest(stamped[k]) != digest(english[k])
        and translation_digest(stamped[k]) == digest(locale[k])
    )


def load_baseline() -> dict[str, dict[str, str]]:
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8")).get("locales", {})


def audit(
    english: dict[str, str], locale: dict[str, str], stamped: dict[str, str]
) -> dict[str, list[str]]:
    """Classify a locale against its stamps. Pure, so it can be tested directly.

    stale: the English moved since this key was translated.
    unstamped: translated but never fingerprinted (nothing would notice drift).
    orphaned: a stamp whose key is gone from the locale or from en.json.
    """
    return {
        "stale": sorted(
            k for k, v in locale.items()
            if k in english and k in stamped and source_digest(stamped[k]) != digest(english[k])
        ),
        "unstamped": sorted(k for k in locale if k in english and k not in stamped),
        "orphaned": sorted(k for k in stamped if k not in locale or k not in english),
    }


def check(language: str) -> int:
    """Report drift for one locale. 0 when the stamps and the English agree."""
    english = catalog("en")
    problems = audit(english, catalog(language), load_baseline().get(language, {}))
    if not any(problems.values()):
        return 0

    if problems["stale"]:
        print("English changed since these were translated; retranslate or re-stamp:")
        for key in problems["stale"]:
            print(f"  {key}\n    english now: {english[key]!r}")
    if problems["unstamped"]:
        print("translated but never stamped; nothing would report drift on them:")
        for key in problems["unstamped"]:
            print(f"  {key}")
    if problems["orphaned"]:
        print("stamped but no longer translated (or gone from en.json):")
        for key in problems["orphaned"]:
            print(f"  {key}")
    print(f"\nRun: python scripts/i18n_source_stamp.py {language}")
    return 1


def stamp(language: str, allow_unchanged: bool = False, keys: set[str] | None = None) -> int:
    """Fingerprint the keys this locale translates: all of them, or only `keys`.

    Refuses, writing nothing, when the English of a chosen key moved but its
    translation did not, unless `allow_unchanged`.
    """
    english, locale = catalog("en"), catalog(language)
    baseline = load_baseline()
    old = baseline.get(language, {})
    chosen = sorted(k for k in locale if k in english and (keys is None or k in keys))
    unchanged = unchanged_translations(english, locale, old, chosen)
    if unchanged and not allow_unchanged:
        print(f"REFUSED {language}: English changed but these translations did not:")
        for key in unchanged:
            print(f"  {key}\n    english now: {english[key]!r}")
        print("Translate them, or use --allow-unchanged for an English-only edit.")
        return 1
    stamps = {} if keys is None else {k: v for k, v in old.items() if k in locale and k in english}
    stamps.update({k: f"{digest(english[k])}/{digest(locale[k])}" for k in chosen})
    baseline[language] = dict(sorted(stamps.items()))
    BASELINE.write_text(
        json.dumps({"_comment": COMMENT, "locales": dict(sorted(baseline.items()))}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"stamped {len(chosen)} keys for {language}")
    return 0


def record_translations() -> int:
    """Add the translation half to stamps that lack it, leaving the English half alone.

    For stamps written before translations were fingerprinted: a stale key keeps
    its old English fingerprint, so it stays reported as drift.
    """
    baseline = load_baseline()
    for language, stamped in baseline.items():
        locale = catalog(language)
        baseline[language] = {
            k: v if "/" in v or k not in locale else f"{v}/{digest(locale[k])}" for k, v in stamped.items()
        }
    BASELINE.write_text(
        json.dumps({"_comment": COMMENT, "locales": dict(sorted(baseline.items()))}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    checking = "--check" in args
    allow_unchanged = "--allow-unchanged" in args
    if "--record-translations" in args:
        return record_translations()
    names = [a for a in args if not a.startswith("--")]
    targets = locales() if "--all" in args else names
    if not targets:
        print(__doc__)
        return 2
    return max(check(name) if checking else stamp(name, allow_unchanged) for name in targets)


if __name__ == "__main__":
    sys.exit(main())
