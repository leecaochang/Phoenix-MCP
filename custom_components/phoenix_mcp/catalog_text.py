"""Load the operator catalogs and localize summaries, notifications and spoken text."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
import json
import string

from homeassistant.core import HomeAssistant

from .catalog_templates import (
    DIFF_SUMMARY_TEMPLATES, NOTIFICATION_TEMPLATES, VERSION_SUMMARY_TEMPLATES, VOICE_TEMPLATES,
)
from .const import CATALOGS_DIR
from .locales import canonical_language


def diff_summary_fields(key: str, **params: object) -> dict[str, object]:
    """Return a diff's summary, summary_key and summary_params; KeyError if unknown."""
    return _summary_fields("diff", DIFF_SUMMARY_TEMPLATES, key, params)


def version_summary_fields(key: str, **params: object) -> dict[str, object]:
    """Return the same three fields for a version record's change summary."""
    return _summary_fields("version", VERSION_SUMMARY_TEMPLATES, key, params)


def _summary_fields(
    namespace: str, templates: Mapping[str, str], key: str, params: dict[str, object]
) -> dict[str, object]:
    return {
        "summary": templates[key].format(**params),
        "summary_key": f"{namespace}.{key}",
        "summary_params": params,
    }


def notification_text(hass: object, key: str, **params: object) -> str:
    """Return a notification string in the instance's language, else the English template."""
    language = getattr(getattr(hass, "config", None), "language", None)
    return _localized(NOTIFICATION_TEMPLATES, "notification", key, language, params)


def voice_text(language: object, key: str, **params: object) -> str:
    """Return a spoken sentence in the conversation's language, else English."""
    return _localized(VOICE_TEMPLATES, "voice", key, language, params)


# The list separator spoken replies use between names. CJK text joins with the
# ideographic comma; every other shipped language uses a comma and a space.
_SPOKEN_LIST_SEPARATORS = {"ja": "、", "zh-Hans": "、", "zh-Hant": "、"}


def spoken_list(items: Iterable[str], language: object) -> str:
    """Join names for a spoken reply with the language's own separator."""
    return _SPOKEN_LIST_SEPARATORS.get(canonical_language(language), ", ").join(items)


def spoken_sentences(parts: Iterable[str], language: object) -> str:
    """Join whole sentences: CJK sentences end in an ideographic full stop and take no space."""
    return ("" if canonical_language(language) in _SPOKEN_LIST_SEPARATORS else " ").join(parts)


class LocalizedReason(str):
    """English operator text that also names its adminError catalog key.

    A str, so every caller that compares, logs or returns the English keeps
    working; an admin response sends `key` and `params` so the panel shows the
    reader's language instead.
    """

    key: str
    params: dict[str, str | int]

    def __new__(cls, text: str, key: str, **params: str | int) -> LocalizedReason:
        reason = super().__new__(cls, text)
        reason.key = key
        reason.params = params
        return reason


def _localized(
    templates: Mapping[str, str], section: str, key: str, language: object, params: dict
) -> str:
    """Resolve one backend-rendered string, falling back to its English template.

    A translation counts only with the template's exact placeholders and a clean format; a
    hand-edited catalog must cost the translation, never raise.
    """
    template = templates[key]
    resolved = canonical_language(language)
    if resolved != "en":
        translated = _catalog_section(resolved, section).get(key)
        expected = _placeholders(template)
        if translated and expected is not None and _placeholders(translated) == expected:
            try:
                return translated.format(**params)
            except (KeyError, IndexError, ValueError, TypeError):
                pass
    return template.format(**params)


# Every catalogs/ section, flattened, keyed by (language, section). Rebound whole
# by load_catalogs, never mutated, so a reader on the event loop sees either the
# old mapping or the new one.
_catalog_sections: dict[tuple[str, str], dict[str, str]] = {}


def load_catalogs() -> None:
    """Read every catalogs/ file into memory. Blocking: run it in an executor job.

    Reads catalogs/, not translations/, which hassfest closes to HA's own categories. An
    unreadable file leaves that language on English.
    """
    global _catalog_sections
    sections: dict[tuple[str, str], dict[str, str]] = {}
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        for section, section_data in document.items():
            out: dict[str, str] = {}

            def walk(node: object, prefix: str) -> None:
                if isinstance(node, dict):
                    for key, value in node.items():
                        walk(value, f"{prefix}.{key}" if prefix else key)
                elif isinstance(node, str):
                    out[prefix] = node

            walk(section_data, "")
            sections[(path.stem, section)] = out
    _catalog_sections = sections
    panel_catalog.cache_clear()


async def async_load_catalogs(hass: HomeAssistant) -> None:
    """Load the catalogs off the event loop, before anything renders a string."""
    await hass.async_add_executor_job(load_catalogs)


def _catalog_section(language: str, section: str) -> dict[str, str]:
    """Return one language file's section, flattened; empty before load or with no file."""
    return _catalog_sections.get((language, section), {})


def _placeholders(value: str) -> frozenset[str] | None:
    """The {placeholder} names in one catalog string, ignoring literal text.

    None when the string does not parse as a format template (a stray brace), so
    no comparison against a parseable English string treats it as a match.
    """
    try:
        return frozenset(
            field for _, field, _, _ in string.Formatter().parse(value) if field
        )
    except ValueError:
        return None


@lru_cache(maxsize=8)
def panel_catalog(language: str) -> dict[str, str]:
    """Return the panel's strings for one language, English-backed and flattened.

    A translation whose {placeholder} set differs from English, or that does not parse, is
    dropped for the English one, as a backstop for a hand-edited install.
    """
    resolved = canonical_language(language)
    english = _catalog_section("en", "panel")
    if resolved == "en":
        return english
    merged = dict(english)
    for key, value in _catalog_section(resolved, "panel").items():
        base = english.get(key)
        if base is not None and (
            (names := _placeholders(value)) is None or names != _placeholders(base)
        ):
            continue
        merged[key] = value
    return merged
