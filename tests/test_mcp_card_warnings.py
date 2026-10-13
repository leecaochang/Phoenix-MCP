"""Tests for the uninstalled-custom-card advisory on dashboard writes.

Advisory, never a refusal. The catalog is a cache of what one browser could see,
so failing a write closed would block a legitimate card whenever the cache is
cold, one plugin install stale, or harvested from a page that saw a partial
registry. A warning next to a card that does render is a far cheaper mistake
than refusing a write the operator wanted with no way to override.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.card_catalog import CardCatalogStore
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tools.lovelace import _card_warnings, _collect_card_types


def _data(catalog: CardCatalogStore | None = None) -> PhoenixData:
    return PhoenixData(
        store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(),
        card_catalog=catalog or CardCatalogStore(),
    )


RESOURCES = ["/hacsfiles/mushroom/mushroom.js", "/local/tts-card.js"]


@pytest.fixture
async def lovelace(hass: HomeAssistant):
    """Real lovelace with the resources a complete harvest reported against."""
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    resources = hass.data[LOVELACE_DATA].resources
    for url in RESOURCES:
        await resources.async_create_item({"res_type": "module", "url": url})
    return hass


async def _catalog(resource_urls: list[str] | None = RESOURCES, failed: list | None = None) -> CardCatalogStore:
    resources = None if resource_urls is None else [{"type": "module", "url": url} for url in resource_urls]
    store = CardCatalogStore()
    await store.async_replace(
        entries=[
            {"type": "mushroom-light-card", "name": "Mushroom Light"},
            {"type": "tiktoktts-card", "name": "TikTok TTS", "available": False},
        ],
        resource_count=31,
        failed_imports=failed or [],
        resources=resources,
    )
    return store


def test_collects_nested_custom_types() -> None:
    """A stack card hides its children's types several levels down."""
    types: set[str] = set()
    _collect_card_types(
        {
            "type": "custom:vertical-stack-in-card",
            "cards": [
                {"type": "tile", "entity": "light.a"},
                {"type": "custom:mushroom-light-card", "entity": "light.b"},
                {"type": "custom:stack-in-card", "cards": [{"type": "custom:mini-graph-card"}]},
            ],
        },
        types,
    )

    assert types == {
        "vertical-stack-in-card", "mushroom-light-card", "stack-in-card", "mini-graph-card",
    }
    # A built-in type is not a custom card and must not be collected.
    assert "tile" not in types


async def test_installed_card_warns_nothing(lovelace) -> None:
    data = _data(await _catalog())
    assert await _card_warnings({"type": "custom:mushroom-light-card"}, data, lovelace) == []


async def test_unseen_card_on_a_complete_harvest_is_most_likely_not_installed(lovelace) -> None:
    data = _data(await _catalog())
    (warning,) = await _card_warnings({"type": "custom:not-installed-card"}, data, lovelace)

    assert "most likely not installed" in warning
    assert "may be incomplete" not in warning
    assert "list_dashboard_cards" in warning


async def test_resource_added_after_the_harvest_qualifies_the_warning(lovelace) -> None:
    """A newly added resource may provide the very card the harvest never saw."""
    data = _data(await _catalog())
    await lovelace.data[LOVELACE_DATA].resources.async_create_item(
        {"res_type": "module", "url": "/hacsfiles/new-card/new-card.js"}
    )

    (warning,) = await _card_warnings({"type": "custom:new-card"}, data, lovelace)

    assert "most likely not installed" not in warning
    assert "may not be installed" in warning
    assert "dashboard resources changed after it ran" in warning


async def test_failed_import_qualifies_the_warning(lovelace) -> None:
    data = _data(await _catalog(failed=[{"url": "/local/tts-card.js", "error": "404"}]))

    (warning,) = await _card_warnings({"type": "custom:not-installed-card"}, data, lovelace)

    assert "most likely not installed" not in warning
    assert "failed to import" in warning


async def test_catalog_without_resource_provenance_qualifies_the_warning(lovelace) -> None:
    """A cache written before provenance was recorded cannot claim completeness."""
    data = _data(await _catalog(resource_urls=None))

    (warning,) = await _card_warnings({"type": "custom:not-installed-card"}, data, lovelace)

    assert "most likely not installed" not in warning
    assert "could not be compared" in warning


async def test_unreadable_current_resources_qualify_the_warning(hass: HomeAssistant) -> None:
    """Lovelace not loaded: the current resources are unknown, not empty."""
    data = _data(await _catalog())

    (warning,) = await _card_warnings({"type": "custom:not-installed-card"}, data, hass)

    assert "most likely not installed" not in warning
    assert "could not be compared" in warning


async def test_registered_but_unavailable_card_warns(lovelace) -> None:
    data = _data(await _catalog())
    (warning,) = await _card_warnings({"type": "custom:tiktoktts-card"}, data, lovelace)

    assert "did not load" in warning
    assert "may be incomplete" not in warning


async def test_builtin_types_never_warn(lovelace) -> None:
    data = _data(await _catalog())
    assert await _card_warnings({"type": "tile", "entity": "light.a"}, data, lovelace) == []
    assert await _card_warnings({"type": "entities", "entities": ["light.a"]}, data, lovelace) == []


async def test_unharvested_catalog_stays_silent(hass: HomeAssistant) -> None:
    """With nothing to compare against, every custom card would look unknown.

    Warning on all of them would train the reader to ignore the field exactly
    when it starts being accurate.
    """
    data = _data()  # never harvested
    assert await _card_warnings({"type": "custom:anything-at-all"}, data, hass) == []


async def test_warns_once_per_type_sorted(lovelace) -> None:
    data = _data(await _catalog())
    config = {
        "views": [{"cards": [
            {"type": "custom:zzz-card"},
            {"type": "custom:aaa-card"},
            {"type": "custom:zzz-card"},
        ]}]
    }

    warnings = await _card_warnings(config, data, lovelace)

    assert len(warnings) == 2
    assert "aaa-card" in warnings[0]
    assert "zzz-card" in warnings[1]


async def test_malformed_payload_does_not_raise(lovelace) -> None:
    data = _data(await _catalog())
    for payload in (None, "string", 42, [], {"type": 7}, {"cards": None}):
        assert await _card_warnings(payload, data, lovelace) == []
