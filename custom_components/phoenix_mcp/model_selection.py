"""Provider-neutral model selection: pick the newest model and map a retired id to its successor.

Phoenix ships no model id as a default. A provider retires and replaces models on
its own schedule, so a hardcoded id is a guess that rots. Instead each account keeps
a cached copy of the provider's own live catalogue, and this module decides from
that catalogue alone:

- `best_model` chooses the preferred model of a catalogue (an account in
  "automatic" mode).
- `successor` maps an id the provider has dropped to the newest model of the same
  family, falling back to the best model overall.

Everything here is pure and works on ids plus an optional creation timestamp. It
never calls a provider and never persists anything; callers own the catalogue.
The ranking is a heuristic standing in for "the newest, most capable model", so it
only ever decides automatic mode and replacement of a vanished id. An explicit
operator choice that the provider still lists is never overridden.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# Preference order of model tiers per provider kind, best first. Each entry is a
# regular expression searched in the lower-cased id. A model that matches no entry
# ranks after every model that matches one. These are tier WORDS a vendor uses for
# a product line (never a version number), so a new generation of the same line
# still ranks correctly without a Phoenix update.
TIER_PREFERENCES: dict[str, tuple[str, ...]] = {
    "claude": ("opus", "sonnet", "fable", "haiku"),
    "deepseek": (r"flash", r"pro"),
    "gemini": (r"flash(?![-_]?lite)", r"pro", r"lite"),
    "mistral": ("medium", "large", "small"),
    "qwen": ("max", "plus", "flash"),
    "tencent": (r"^hy", r"^deepseek"),
    "openrouter": (r"anthropic/claude-opus", r"anthropic/claude-sonnet", r"openai/gpt", r"google/gemini"),
}

# Markers of a model the provider has not finished. A stable model always ranks
# ahead of one of these in the same tier.
_PRERELEASE_RE = re.compile(r"(?:^|[-_:/.])(?:preview|exp|experimental|beta|alpha|rc\d*|dev)(?:$|[-_:/.])")
# Speed variants of one model ("highspeed", "ultraspeed"). Same model, same
# generation, so the plain id wins a tie.
_VARIANT_RE = re.compile(r"(?:^|[-_:/.])(?:highspeed|ultraspeed)(?:$|[-_:/.])")
# A dated snapshot ("-2026-06-08", "-20251001", "-0902") of a model that usually
# also exists undated; the undated id is the provider's current pointer.
_SNAPSHOT_RE = re.compile(r"(?:-\d{4}-\d{2}-\d{2}|-\d{8}|-\d{4})$")
# A provider-maintained alias that follows its newest model. Preferring it lets
# the PROVIDER move the target without Phoenix noticing anything.
_LATEST_RE = re.compile(r"(?:^|[-_:/.])latest$")
_VERSION_RUN_RE = re.compile(r"\d+")
_SPLIT_RE = re.compile(r"[-_:/\s]+")
# A parameter count or size ("70b", "8b", "27b") belongs to the family; a version
# ("3.8", "v4", "4o", "20251001") does not.
_SIZE_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?[bkmt]$")


def family_key(model_id: str) -> str:
    """The id with every version and date stripped, so a successor shares its key.

    `claude-opus-4-8`, `claude-opus-5-5` and `claude-opus-5-5-20260101` all reduce
    to `claude opus`; `gemini-2.5-flash` and `gemini-3.8-flash` to `gemini flash`.
    Size tokens stay, so `llama-8b` is never mistaken for the family of `llama-70b`.
    """
    parts: list[str] = []
    for token in _SPLIT_RE.split(model_id.lower()):
        if not token:
            continue
        if _SIZE_TOKEN_RE.match(token):
            parts.append(token)
            continue
        stripped = re.sub(r"[\d.]+", "", token)
        # Glued prefixes such as the "v" of "v4" carry no meaning once the digits
        # are gone.
        if stripped and stripped != "v":
            parts.append(stripped)
    return " ".join(parts)


def _tier(kind: str, model_id: str) -> int:
    lowered = model_id.lower()
    preferences = TIER_PREFERENCES.get(kind, ())
    for index, pattern in enumerate(preferences):
        if re.search(pattern, lowered):
            return index
    return len(preferences)


def _rank_key(
    kind: str, model_id: str, created: Mapping[str, float] | None,
) -> tuple[int, int, int, int, int, float, tuple[int, ...], str]:
    """Sort key where LARGER means more preferred."""
    lowered = model_id.lower()
    return (
        -_tier(kind, model_id),
        0 if _PRERELEASE_RE.search(lowered) else 1,
        0 if _VARIANT_RE.search(lowered) else 1,
        0 if _SNAPSHOT_RE.search(lowered) else 1,
        1 if _LATEST_RE.search(lowered) else 0,
        float((created or {}).get(model_id) or 0),
        tuple(int(run) for run in _VERSION_RUN_RE.findall(lowered)),
        model_id,
    )


def rank_models(
    kind: str, models: Sequence[str], created: Mapping[str, float] | None = None,
) -> list[str]:
    """The catalogue ordered most preferred first. Duplicates collapse."""
    unique = list(dict.fromkeys(m for m in models if isinstance(m, str) and m))
    return sorted(unique, key=lambda m: _rank_key(kind, m, created), reverse=True)


def best_model(
    kind: str, models: Sequence[str], created: Mapping[str, float] | None = None,
) -> str:
    """The preferred model of a catalogue, or "" when it is empty."""
    ranked = rank_models(kind, models, created)
    return ranked[0] if ranked else ""


def successor(
    kind: str, retired: str, models: Sequence[str],
    created: Mapping[str, float] | None = None,
) -> str:
    """The model that replaces a dropped id: its own family first, then the best overall."""
    if not models:
        return ""
    family = family_key(retired)
    same = [m for m in models if family and family_key(m) == family]
    return best_model(kind, same or models, created)


def effective_model(
    kind: str, stored: str, catalogue: Sequence[str], seen: Sequence[str],
    created: Mapping[str, float] | None = None,
    eligible: Sequence[str] | None = None,
) -> tuple[str, str]:
    """The model a request should use, and the id it replaced ("" if none).

    `stored` empty means automatic: the best eligible model of the catalogue. A
    stored id is kept whenever the catalogue lists it, and also whenever the
    account has never seen the provider list it, because that is a custom
    deployment or a model the listing cannot see. Only an id the provider DID list
    and has since dropped is treated as retired and replaced. An empty catalogue
    (provider not reachable yet) changes nothing.

    `eligible` narrows the pool automatic mode and replacement choose from (for
    example Ollama models that declare tool support) without changing which ids
    count as listed.
    """
    pool = eligible if eligible else catalogue
    if not stored:
        return best_model(kind, pool, created), ""
    if catalogue and stored not in catalogue and stored in seen:
        return successor(kind, stored, pool, created), stored
    return stored, ""
