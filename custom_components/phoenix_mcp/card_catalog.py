"""Dashboard card catalog: which Lovelace cards this instance can actually render.

Phoenix MCP's dashboard write tools accept any card dict with a non-empty string
`type`, so an agent authoring a dashboard cannot learn which custom cards are
installed. This module holds a cached catalog of every card type the instance can
render, with a one-line description, a documentation URL, and a worked stub config.

The catalog is HARVESTED IN A BROWSER, not read off disk. A card announces itself by
pushing onto `window.customCards` (the registry HA's own card picker reads) at runtime.
A static parse of the plugin JS yields nothing for cards that build their type strings
by concatenation or never register in the picker, and cannot recover descriptions or
stub configs, so it finds far fewer cards and would give a misleading catalog.

The frontend harvester posts here through the admin API. The payload is assembled
from third-party card code, so every field is coerced, bounded, and dropped on doubt.

A NEVER-HARVESTED CATALOG IS NOT AN EMPTY ONE. `harvested_at is None` means no browser
has reported yet, and callers must say so rather than report zero cards: an agent told
"you have no custom cards" avoids every custom card on the system.

A HARVESTED CATALOG IS NOT NECESSARILY A COMPLETE ONE. Each harvest records a
fingerprint of the dashboard resources registered when it reported, so a reader can
tell whether resources changed since; `assess_freshness` combines that with failed
imports into the evidence a "not seen" answer may rely on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from homeassistant.helpers.storage import Store
from homeassistant.util.dt import utcnow

from .const import (
    MAX_CARD_CATALOG_ENTRIES,
    MAX_CARD_CATALOG_FAILURES,
    MAX_CARD_STUB_CONFIG_BYTES,
)

_LOGGER = logging.getLogger(__name__)

# Where an entry came from. "picker" is a window.customCards registration (the
# card advertises itself, so name/description are the author's own). "element"
# is a custom element that resolved by name without a picker registration: some
# widely used cards (stack-in-card, config-template-card) deliberately skip the
# picker, so a catalog listing only picker cards would tell an agent they do not
# exist.
CARD_SOURCES = frozenset({"picker", "element"})


@dataclass
class CardEntry:
    """One renderable card type.

    `available` is the load-bearing field. A card can register a picker entry
    while its custom element never defines, so "advertised" and "usable" differ,
    and an agent that conflates them authors a card that renders as an error.
    """

    type: str
    name: str | None = None
    description: str | None = None
    documentation_url: str | None = None
    preview: bool = False
    available: bool = True
    has_visual_editor: bool = False
    stub_config: dict | None = None
    source: str = "picker"


@dataclass
class CardCatalog:
    """The harvested catalog plus the provenance needed to judge its freshness."""

    entries: list[CardEntry] = field(default_factory=list)
    harvested_at: str | None = None
    resource_count: int = 0
    # Resources that failed to import during the harvest. Surfaced to the admin
    # as a cleanup hint: a registered resource whose file is gone is common,
    # typically a stale duplicate of a card served from a different path.
    failed_imports: list[dict] = field(default_factory=list)
    # Fingerprint of the dashboard resource URLs registered when the harvest
    # reported, or None when they could not be read (or the cache predates it).
    resource_fingerprint: str | None = None

    @property
    def harvested(self) -> bool:
        """True once a browser has reported, even if it found nothing."""
        return self.harvested_at is not None


# Why a harvested catalog may not have seen every installed card. The order is
# the order reasons are reported in.
FRESHNESS_REASONS = ("provenance_unknown", "resources_changed", "failed_imports")

_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


def resource_fingerprint(urls: Iterable[str] | None) -> str | None:
    """Order-independent fingerprint of a resource URL list, or None if unknown."""
    if urls is None:
        return None
    canonical = json.dumps(sorted({u for u in urls if isinstance(u, str)}))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class CatalogFreshness:
    """How far a harvested catalog can be trusted for a card it did not see.

    Complete means the last harvest reported against the dashboard resources
    registered now and every one of them imported. Even then "not seen" is
    strong evidence rather than proof: a card can register later than every
    follow-up harvest waited, or come from an integration rather than a
    dashboard resource.
    """

    reasons: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.reasons


def assess_freshness(catalog: CardCatalog, current_urls: list[str] | None) -> CatalogFreshness:
    """Compare a harvested catalog with the dashboard resources registered now."""
    reasons: list[str] = []
    current = resource_fingerprint(current_urls)
    if catalog.resource_fingerprint is None or current is None:
        reasons.append("provenance_unknown")
    elif current != catalog.resource_fingerprint:
        reasons.append("resources_changed")
    if catalog.failed_imports:
        reasons.append("failed_imports")
    return CatalogFreshness(tuple(reasons))


def _clean_str(value: object, limit: int = 300) -> str | None:
    """Coerce a harvested string, or None for anything else.

    Degrades a wrong-shaped value to absent rather than stringifying it, the
    helpers.str_arg rule: str(True) invents content nobody authored.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] or None


def _clean_stub(value: object) -> dict | None:
    """Keep a stub config only if it is a dict that serializes within bounds.

    Stub configs come from third-party card code via getStubConfig, so the shape
    is whatever that card returned. Anything non-dict, unserializable, or over
    the cap is dropped: the catalog degrades to "no example" rather than
    refusing the whole entry, since the card itself is still worth knowing about.
    """
    if not isinstance(value, dict):
        return None
    try:
        # No `default=` fallback on purpose: this doubles as the check that the
        # value can round-trip to .storage. Coercing an unserializable value here
        # would measure a stringified form while storing the original, which then
        # fails at Store.async_save instead of at the boundary that vetted it.
        encoded = json.dumps(value)
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_CARD_STUB_CONFIG_BYTES:
        return None
    return value


def sanitize_entries(raw: object) -> list[CardEntry]:
    """Build CardEntry rows from an untrusted harvest payload.

    Drops anything without a usable `type`, de-duplicates by type keeping the
    first occurrence, and caps the total. Never raises: a malformed payload
    yields fewer rows, never an error, because a partial catalog is still useful
    and a harvest failure must not break the admin request that carried it.
    """
    if not isinstance(raw, list):
        return []
    entries: list[CardEntry] = []
    seen: set[str] = set()
    for item in raw:
        if len(entries) >= MAX_CARD_CATALOG_ENTRIES:
            break
        if not isinstance(item, dict):
            continue
        card_type = _clean_str(item.get("type"), limit=120)
        if not card_type or card_type in seen:
            continue
        seen.add(card_type)
        raw_source = item.get("source")
        source = raw_source if isinstance(raw_source, str) and raw_source in CARD_SOURCES else "picker"
        entries.append(
            CardEntry(
                type=card_type,
                name=_clean_str(item.get("name")),
                description=_clean_str(item.get("description"), limit=500),
                documentation_url=_clean_str(item.get("documentation_url"), limit=500),
                preview=bool(item.get("preview")),
                available=bool(item.get("available", True)),
                has_visual_editor=bool(item.get("has_visual_editor")),
                stub_config=_clean_stub(item.get("stub_config")),
                source=source,
            )
        )
    return entries


def sanitize_failures(raw: object) -> list[dict]:
    """Build the failed-import list from an untrusted harvest payload."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:MAX_CARD_CATALOG_FAILURES]:
        if not isinstance(item, dict):
            continue
        url = _clean_str(item.get("url"), limit=500)
        if not url:
            continue
        out.append({"url": url, "error": _clean_str(item.get("error"), limit=200) or ""})
    return out


def _resource_count(value: object) -> int:
    """A non-negative count from a harvest payload or the stored cache; anything else is 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, int(value))
    except (ValueError, OverflowError):
        return 0


def _clean_fingerprint(value: object) -> str | None:
    """A stored resource fingerprint, or None for an absent or malformed one."""
    return value if isinstance(value, str) and _FINGERPRINT.fullmatch(value) else None


class CardCatalogStore:
    """Persisted card catalog, replaced wholesale by each harvest.

    Wholesale replacement rather than a merge is deliberate: a card removed from
    the instance must disappear from the catalog, and a merge would keep
    recommending it forever. The harvest is a full snapshot of what one browser
    could see, so it is the complete answer or it is not used.

    A None store keeps the catalog in memory only, matching VersionStore.
    """

    def __init__(self, store: Store | None = None) -> None:
        self._catalog = CardCatalog()
        self._store = store

    @property
    def catalog(self) -> CardCatalog:
        """The current catalog. Never None; check `.harvested` before reporting."""
        return self._catalog

    async def async_load(self) -> None:
        """Load the cached catalog. A corrupt or absent doc reads as never-harvested."""
        if self._store is None:
            return
        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a bad cache must never block setup
            _LOGGER.exception("Could not load the card catalog; treating it as unharvested")
            return
        if not isinstance(data, dict):
            return
        self._catalog = CardCatalog(
            entries=sanitize_entries(data.get("entries")),
            harvested_at=_clean_str(data.get("harvested_at"), limit=64),
            resource_count=_resource_count(data.get("resource_count")),
            failed_imports=sanitize_failures(data.get("failed_imports")),
            resource_fingerprint=_clean_fingerprint(data.get("resource_fingerprint")),
        )

    async def async_save(self) -> None:
        if self._store is None:
            return
        await self._store.async_save(self.as_dict())

    async def async_replace(
        self, *, entries: object, resource_count: object, failed_imports: object,
        resource_urls: list[str] | None = None,
    ) -> CardCatalog:
        """Replace the catalog from a harvest payload and persist it.

        `resource_urls` is the server's own read of the registered dashboard
        resources at report time, never the browser's claim, or None if unread.
        """
        self._catalog = CardCatalog(
            entries=sanitize_entries(entries),
            harvested_at=utcnow().isoformat(),
            resource_count=_resource_count(resource_count),
            failed_imports=sanitize_failures(failed_imports),
            resource_fingerprint=resource_fingerprint(resource_urls),
        )
        await self.async_save()
        return self._catalog

    async def async_clear(self) -> None:
        """Reset to never-harvested. Used by the admin data wipe."""
        self._catalog = CardCatalog()
        await self.async_save()

    def get(self, card_type: str) -> CardEntry | None:
        """Look up one card by type, or None. Accepts a bare or `custom:`-prefixed type."""
        wanted = card_type[len("custom:"):] if card_type.startswith("custom:") else card_type
        for entry in self._catalog.entries:
            if entry.type == wanted:
                return entry
        return None

    def known_types(self) -> frozenset[str]:
        """Every card type the catalog knows, whether or not it is available."""
        return frozenset(e.type for e in self._catalog.entries)

    def as_dict(self) -> dict:
        """Serializable form, for the Store and the admin API."""
        return {
            "entries": [asdict(e) for e in self._catalog.entries],
            "harvested_at": self._catalog.harvested_at,
            "resource_count": self._catalog.resource_count,
            "failed_imports": list(self._catalog.failed_imports),
            "resource_fingerprint": self._catalog.resource_fingerprint,
        }
