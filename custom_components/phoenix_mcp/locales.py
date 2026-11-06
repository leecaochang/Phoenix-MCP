"""Canonicalize Home Assistant language tags to Phoenix catalog locales."""

from __future__ import annotations

_SIMPLIFIED_CHINESE_REGIONS = frozenset({"CN", "MY", "SG"})
_TRADITIONAL_CHINESE_REGIONS = frozenset({"HK", "MO", "TW"})

# Keep this list in step with catalogs/*.json, without scanning the filesystem on the
# request path.
SHIPPED_LOCALES = frozenset({
    "da", "de", "en", "es", "fi", "fr", "it", "ja", "ko", "nb", "nl", "pl", "ru", "sv",
    "zh-Hans", "zh-Hant",
})


def _normalize(language: object) -> tuple[str, list[str]] | None:
    if not language:
        return None
    raw = str(language).strip().replace("_", "-")
    if not raw:
        return None
    parts = raw.split("-")
    if any(not part for part in parts):
        return None
    return parts[0].casefold(), parts[1:]


def canonical_language(language: object) -> str:
    """Return the closest shipped Phoenix locale, with English as last resort.

    Exact matches win, then Chinese script or regional equivalents, then the base language.
    Uses only in-memory constants, as this runs on the event loop.
    """
    normalized = _normalize(language)
    if normalized is None:
        return "en"

    base, subtags = normalized
    exact = "-".join([base, *subtags]).casefold()
    exact_match = next(
        (locale for locale in SHIPPED_LOCALES if locale.casefold() == exact),
        None,
    )
    if exact_match:
        return exact_match

    if base == "zh":
        script = next((part.title() for part in subtags if len(part) == 4), None)
        region = next(
            (part.upper() for part in subtags if len(part) in (2, 3) and part.isalnum()),
            None,
        )
        if script in ("Hans", "Hant") and f"zh-{script}" in SHIPPED_LOCALES:
            return f"zh-{script}"
        if region in _TRADITIONAL_CHINESE_REGIONS:
            return "zh-Hant"
        if region in _SIMPLIFIED_CHINESE_REGIONS or region is None:
            return "zh-Hans"

    # Plain "no" is the macrolanguage, written as Bokmal by default. Nynorsk ("nn")
    # is a language of its own and is not substituted.
    if base == "no":
        return "nb"

    base_match = next(
        (locale for locale in SHIPPED_LOCALES if locale.casefold() == base),
        None,
    )
    return base_match or "en"
