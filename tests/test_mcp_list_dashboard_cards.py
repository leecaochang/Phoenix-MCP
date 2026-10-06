"""Tests for list_dashboard_cards, the installed-custom-card discovery tool.

The key invariant is cold start: an un-harvested catalog must never read as "this
instance has no custom cards". An agent told that avoids every custom card with
no way to recover, whereas "unknown, prefer built-ins" degrades gracefully.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.card_catalog import CardCatalogStore
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord


def _access_key(**caps) -> AccessKeyRecord:
    base = {"cap_lovelace_write": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(),
        created_by="u", permissions=PermissionTree(domains={}), **base,
    )


def _data(catalog: CardCatalogStore | None = None) -> PhoenixData:
    return PhoenixData(
        store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(),
        card_catalog=catalog or CardCatalogStore(),
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _harvested(**over) -> CardCatalogStore:
    store = CardCatalogStore()
    entries = over.pop("entries", [
        {
            "type": "mushroom-light-card",
            "name": "Mushroom Light Card",
            "description": "A light card",
            "documentation_url": "https://example.invalid/light",
            "stub_config": {"type": "custom:mushroom-light-card", "entity": "light.kitchen"},
            "has_visual_editor": True,
        },
        {"type": "broken-card", "name": "Broken", "available": False},
    ])
    await store.async_replace(entries=entries, resource_count=31, failed_imports=[])
    return store


async def _call(args, access_key, hass, data):
    return await _call_tool("list_dashboard_cards", args, access_key, hass, data=data)


async def test_unharvested_is_reported_as_unknown_not_empty(hass: HomeAssistant) -> None:
    """A cold catalog reads as unknown, never as 'no custom cards'."""
    content, outcome, _ = await _call({}, _access_key(), hass, _data())
    body = _json(content)

    assert outcome == "allowed"
    assert body["harvested"] is False
    assert body["cards"] == []
    # The notice must say UNKNOWN, and must not be phrased as an absence.
    notice = body["notice"].lower()
    assert "unknown" in notice
    assert "not the same as none" in notice
    assert "built-in" in notice


async def test_denied_cap_returns_uniform_forbidden(hass: HomeAssistant) -> None:
    content, outcome, resource = await _call({}, _access_key(cap_lovelace_write="deny"), hass, _data())

    assert outcome == "denied"
    assert resource == "list_dashboard_cards"
    assert content["content"][0]["text"].startswith("Forbidden")


async def test_lean_listing_omits_examples(hass: HomeAssistant) -> None:
    """A discovery call must not spend its budget on examples for unused cards."""
    content, outcome, _ = await _call({}, _access_key(), hass, _data(await _harvested()))
    body = _json(content)

    assert outcome == "allowed"
    assert body["harvested"] is True
    assert body["count"] == 2
    card = next(c for c in body["cards"] if c["type"] == "custom:mushroom-light-card")
    assert card["name"] == "Mushroom Light Card"
    assert card["description"] == "A light card"
    assert card["documentation_url"] == "https://example.invalid/light"
    assert "example_config" not in card


async def test_types_carry_the_custom_prefix(hass: HomeAssistant) -> None:
    """The catalog stores bare types; a card config needs the prefixed form."""
    content, _, _ = await _call({}, _access_key(), hass, _data(await _harvested()))
    for card in _json(content)["cards"]:
        assert card["type"].startswith("custom:")


async def test_unavailable_card_is_flagged_and_available_ones_are_not(hass: HomeAssistant) -> None:
    """A picker entry whose element never loaded must not look usable."""
    content, _, _ = await _call({}, _access_key(), hass, _data(await _harvested()))
    cards = {c["type"]: c for c in _json(content)["cards"]}

    broken = cards["custom:broken-card"]
    assert broken["available"] is False
    assert "do not use" in broken["note"].lower()

    # The common case stays quiet rather than repeating "available: true" per row.
    assert "available" not in cards["custom:mushroom-light-card"]


async def test_single_type_lookup_returns_the_example(hass: HomeAssistant) -> None:
    hass.states.async_set("light.kitchen", "on")
    content, outcome, _ = await _call(
        {"type": "mushroom-light-card"}, _access_key(), hass, _data(await _harvested())
    )
    body = _json(content)

    assert outcome == "allowed"
    assert body["found"] is True
    assert body["card"]["example_config"]["entity"] == "<redacted>"
    assert body["card"]["has_visual_editor"] is True


async def test_single_type_lookup_accepts_custom_prefix(hass: HomeAssistant) -> None:
    content, _, _ = await _call(
        {"type": "custom:mushroom-light-card"}, _access_key(), hass, _data(await _harvested())
    )
    assert _json(content)["found"] is True


async def test_missing_type_reports_not_installed(hass: HomeAssistant) -> None:
    content, outcome, _ = await _call(
        {"type": "custom:nope-card"}, _access_key(), hass, _data(await _harvested())
    )
    body = _json(content)

    assert outcome == "allowed"
    assert body["found"] is False
    assert "not installed" in body["notice"].lower()


async def test_detailed_includes_every_example(hass: HomeAssistant) -> None:
    hass.states.async_set("light.kitchen", "on")
    content, _, _ = await _call({"detailed": True}, _access_key(), hass, _data(await _harvested()))
    cards = {c["type"]: c for c in _json(content)["cards"]}

    assert cards["custom:mushroom-light-card"]["example_config"]["entity"] == "<redacted>"
    # A card with no stub carries no example rather than a null.
    assert "example_config" not in cards["custom:broken-card"]


async def test_wrong_shaped_type_arg_is_refused_by_name(hass: HomeAssistant) -> None:
    """A bad shape is refused with the field named, never treated as absent."""
    content, outcome, _ = await _call(
        {"type": ["mushroom-light-card"]}, _access_key(), hass, _data(await _harvested())
    )
    assert outcome == "invalid_request"
    assert content["content"][0]["text"] == "type must be a string."


async def test_examples_filter_existing_and_embedded_out_of_scope_entities(hass: HomeAssistant) -> None:
    """Old saved stubs obey current entity authority in both read projections."""
    hass.states.async_set("light.kitchen", "on")
    hass.states.async_set("lock.private_door", "locked")
    access_key = _access_key()
    access_key.permissions.domains["light"] = PermissionNode(state="YELLOW")
    catalog = await _harvested(entries=[{
        "type": "scope-card",
        "stub_config": {"entity": "light.kitchen", "nested": ["lock.private_door"],
                        "template": "{{ states('lock.private_door') }}", "password": "private"},
    }])
    for args in ({"type": "scope-card"}, {"detailed": True}):
        content, outcome, _ = await _call(args, access_key, hass, _data(catalog))
        body = _json(content)
        example = (body["card"] if "card" in body else body["cards"][0])["example_config"]
        assert outcome == "allowed"
        assert example["entity"] == "light.kitchen"
        assert "lock.private_door" not in json.dumps(example)
        assert "private" not in json.dumps(example)
        assert catalog.get("scope-card").stub_config["nested"] == ["lock.private_door"]


async def test_examples_preserve_placeholders_urls_and_css_but_redact_attribute_access(hass):
    hass.states.async_set("light.visible", "on")
    hass.states.async_set("lock.private_door", "locked")
    hidden = er.async_get(hass).async_get_or_create("lock", "test", "registered_only", suggested_object_id="registered_only")
    access_key = _access_key()
    access_key.permissions.domains["light"] = PermissionNode(state="YELLOW")
    benign = {"entity": "light.example", "image": "/local/images/bg.png",
              "url_path": "https://www.home-assistant.io/docs/configuration.yaml",
              "style": "width: 10.5px", "template": "{{ states.light.visible.state }}"}
    private = {"template": "{{ states.lock.private_door.state }}",
               "attribute": "lock.private_door.attributes.battery",
               "registry": f"{{{{ states.{hidden.entity_id}.state }}}}",
               "password": "secret", "nested": [{"lock.private_door": "must disappear"}]}
    catalog = await _harvested(entries=[{"type": "examples", "stub_config": {"benign": benign, "private": private}}])
    for args in ({"type": "examples"}, {"detailed": True}):
        content, outcome, _ = await _call(args, access_key, hass, _data(catalog))
        body = _json(content)
        example = (body["card"] if "card" in body else body["cards"][0])["example_config"]
        assert outcome == "allowed"
        assert example["benign"] == benign
        assert "lock.private_door" not in json.dumps(example)
        assert hidden.entity_id not in json.dumps(example)
        assert example["private"]["nested"] == [{}]
        assert example["private"]["password"] == "<redacted>"
