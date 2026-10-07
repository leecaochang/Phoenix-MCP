"""Tests for the dashboard card catalog store.

The load-bearing invariants here are the ones that mislead an agent when broken:
a never-harvested catalog must not read as an empty one, a removed card must
disappear, and an untrusted browser payload must never reach .storage unbounded.
"""

from __future__ import annotations

import pytest

from custom_components.phoenix_mcp.card_catalog import (
    CardCatalog,
    CardCatalogStore,
    assess_freshness,
    resource_fingerprint,
    sanitize_entries,
    sanitize_failures,
)
from custom_components.phoenix_mcp.const import (
    MAX_CARD_CATALOG_ENTRIES,
    MAX_CARD_CATALOG_FAILURES,
    MAX_CARD_STUB_CONFIG_BYTES,
)


def _entry(card_type: str, **kw) -> dict:
    return {"type": card_type, **kw}


async def test_never_harvested_is_not_empty() -> None:
    """A fresh catalog reports unharvested, which callers must distinguish."""
    store = CardCatalogStore()
    assert store.catalog.harvested is False
    assert store.catalog.harvested_at is None
    assert store.catalog.entries == []

    await store.async_replace(entries=[], resource_count=0, failed_imports=[])

    # A harvest that genuinely found nothing IS harvested. This is the whole
    # point of the flag: "no cards installed" and "nobody has looked" differ.
    assert store.catalog.harvested is True
    assert store.catalog.entries == []


async def test_replace_is_wholesale_so_removed_cards_disappear() -> None:
    """A merge would keep recommending a card the operator uninstalled."""
    store = CardCatalogStore()
    await store.async_replace(
        entries=[_entry("mushroom-entity-card"), _entry("button-card")],
        resource_count=2,
        failed_imports=[],
    )
    assert store.known_types() == {"mushroom-entity-card", "button-card"}

    await store.async_replace(entries=[_entry("button-card")], resource_count=1, failed_imports=[])
    assert store.known_types() == {"button-card"}


async def test_get_accepts_custom_prefix() -> None:
    """Callers hold `custom:x` from a card config; the catalog keys on the bare type."""
    store = CardCatalogStore()
    await store.async_replace(entries=[_entry("bubble-card")], resource_count=1, failed_imports=[])

    assert store.get("bubble-card") is not None
    assert store.get("custom:bubble-card") is not None
    assert store.get("custom:not-installed") is None


def test_sanitize_drops_junk_and_dedupes() -> None:
    """A malformed payload yields fewer rows, never an exception."""
    rows = sanitize_entries(
        [
            _entry("good-card", name="Good", description="A card"),
            _entry("good-card", name="Duplicate"),  # dropped, first wins
            {"type": ""},                            # no usable type
            {"type": 42},                            # wrong shape degrades to absent
            "not-a-dict",
            None,
        ]
    )
    assert [r.type for r in rows] == ["good-card"]
    assert rows[0].name == "Good"

    assert sanitize_entries("not a list") == []
    assert sanitize_entries(None) == []


def test_sanitize_coerces_wrong_shaped_fields_to_absent() -> None:
    """str(True) would invent content the card never published."""
    (row,) = sanitize_entries([_entry("c", name=True, description=["x"], documentation_url=7)])
    assert row.name is None
    assert row.description is None
    assert row.documentation_url is None


def test_available_defaults_true_but_is_honored() -> None:
    """A picker entry whose element never defined is advertised, not usable."""
    rows = sanitize_entries([_entry("a"), _entry("b", available=False)])
    assert rows[0].available is True
    assert rows[1].available is False


def test_unknown_source_falls_back_to_picker() -> None:
    rows = sanitize_entries([_entry("a", source="element"), _entry("b", source="bogus")])
    assert rows[0].source == "element"
    assert rows[1].source == "picker"


def test_entry_cap_is_enforced() -> None:
    rows = sanitize_entries([_entry(f"card-{i}") for i in range(MAX_CARD_CATALOG_ENTRIES + 50)])
    assert len(rows) == MAX_CARD_CATALOG_ENTRIES


def test_oversized_stub_config_is_dropped_not_the_entry() -> None:
    """The card is still worth knowing about without its example."""
    big = {"filler": "x" * (MAX_CARD_STUB_CONFIG_BYTES + 100)}
    (row,) = sanitize_entries([_entry("c", stub_config=big)])
    assert row.type == "c"
    assert row.stub_config is None

    (ok,) = sanitize_entries([_entry("c", stub_config={"entity": "light.kitchen"})])
    assert ok.stub_config == {"entity": "light.kitchen"}


def test_unserializable_stub_config_is_dropped() -> None:
    (row,) = sanitize_entries([_entry("c", stub_config={"k": {1, 2}})])
    assert row.stub_config is None


@pytest.mark.parametrize("bad", ["nope", 5, None, {"url": "x"}])
def test_sanitize_failures_tolerates_junk(bad: object) -> None:
    assert sanitize_failures(bad) == []


def test_failure_cap_is_enforced() -> None:
    out = sanitize_failures(
        [{"url": f"/x/{i}.js", "error": "boom"} for i in range(MAX_CARD_CATALOG_FAILURES + 20)]
    )
    assert len(out) == MAX_CARD_CATALOG_FAILURES


async def test_clear_returns_to_never_harvested() -> None:
    store = CardCatalogStore()
    await store.async_replace(entries=[_entry("a")], resource_count=1, failed_imports=[])
    assert store.catalog.harvested is True

    await store.async_clear()
    assert store.catalog.harvested is False
    assert store.catalog.entries == []


async def test_corrupt_store_reads_as_unharvested() -> None:
    """A bad cache must never block setup, and must not look like an empty instance."""

    class _BadStore:
        async def async_load(self):
            raise ValueError("corrupt")

    store = CardCatalogStore(_BadStore())  # type: ignore[arg-type]
    await store.async_load()
    assert store.catalog.harvested is False


@pytest.mark.parametrize("stored,expected", [
    ("not-a-number", 0), (["1"], 0), ({"n": 1}, 0), (float("inf"), 0),
    (float("nan"), 0), (True, 0), (-5, 0), ("7", 7), (12, 12),
])
async def test_loaded_resource_count_is_sanitized(stored, expected) -> None:
    """A well-formed cache document with a bad count must still load, not abort setup."""

    class _Store:
        async def async_load(self):
            return {"entries": [], "harvested_at": "2026-01-01T00:00:00+00:00", "resource_count": stored}

    store = CardCatalogStore(_Store())  # type: ignore[arg-type]
    await store.async_load()
    assert store.catalog.resource_count == expected
    assert store.catalog.harvested is True


@pytest.mark.parametrize("count", [float("inf"), float("nan"), "1e999", [3]])
async def test_harvest_with_an_unrepresentable_count_records_zero(count) -> None:
    store = CardCatalogStore()
    await store.async_replace(entries=[], resource_count=count, failed_imports=[])
    assert store.catalog.resource_count == 0


async def test_round_trip_through_as_dict() -> None:
    store = CardCatalogStore()
    await store.async_replace(
        entries=[_entry("mushroom-light-card", name="Mushroom Light", stub_config={"entity": "light.x"})],
        resource_count=31,
        failed_imports=[{"url": "/hacsfiles/gone/gone.js", "error": "404"}],
    )
    data = store.as_dict()

    restored = CardCatalogStore()
    restored._catalog.entries = sanitize_entries(data["entries"])
    assert restored.known_types() == {"mushroom-light-card"}
    assert data["resource_count"] == 31
    assert data["failed_imports"][0]["url"] == "/hacsfiles/gone/gone.js"
    assert data["harvested_at"] is not None


async def test_harvest_records_an_order_independent_resource_fingerprint() -> None:
    store = CardCatalogStore()
    await store.async_replace(entries=[], resource_count=2, failed_imports=[], resource_urls=["/b.js", "/a.js"])

    assert store.catalog.resource_fingerprint == resource_fingerprint(["/a.js", "/b.js", "/a.js"])
    assert store.as_dict()["resource_fingerprint"] == store.catalog.resource_fingerprint


async def test_unread_resources_record_no_fingerprint() -> None:
    store = CardCatalogStore()
    await store.async_replace(entries=[], resource_count=2, failed_imports=[])

    assert store.catalog.resource_fingerprint is None


def test_freshness_is_complete_only_against_the_same_resources_without_failures() -> None:
    harvested = CardCatalog(harvested_at="t", resource_fingerprint=resource_fingerprint(["/a.js"]))

    assert assess_freshness(harvested, ["/a.js"]).complete is True
    assert assess_freshness(harvested, ["/a.js", "/b.js"]).reasons == ("resources_changed",)
    assert assess_freshness(harvested, []).reasons == ("resources_changed",)
    # An unreadable current list is unknown, never "no resources".
    assert assess_freshness(harvested, None).reasons == ("provenance_unknown",)

    failed = CardCatalog(
        harvested_at="t", resource_fingerprint=resource_fingerprint(["/a.js"]),
        failed_imports=[{"url": "/a.js", "error": "404"}],
    )
    assert assess_freshness(failed, ["/a.js"]).reasons == ("failed_imports",)
    assert assess_freshness(CardCatalog(harvested_at="t"), ["/a.js"]).reasons == ("provenance_unknown",)


@pytest.mark.parametrize("stored", ["", "abc", "Z" * 64, 7, None, ["x"], "A" * 64])
async def test_a_malformed_stored_fingerprint_loads_as_unknown(stored) -> None:
    class _Store:
        async def async_load(self):
            return {"entries": [], "harvested_at": "2026-01-01T00:00:00", "resource_fingerprint": stored}

    store = CardCatalogStore(_Store())  # type: ignore[arg-type]
    await store.async_load()

    assert store.catalog.harvested is True
    assert store.catalog.resource_fingerprint is None


async def test_a_stored_fingerprint_survives_a_reload() -> None:
    fingerprint = resource_fingerprint(["/a.js"])

    class _Store:
        async def async_load(self):
            return {"entries": [], "harvested_at": "2026-01-01T00:00:00", "resource_fingerprint": fingerprint}

    store = CardCatalogStore(_Store())  # type: ignore[arg-type]
    await store.async_load()

    assert store.catalog.resource_fingerprint == fingerprint
