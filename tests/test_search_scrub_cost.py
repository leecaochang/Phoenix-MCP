"""search_entities scrubs only the entities a filter can still match, and the sensitive-name check is cached.

A select over one domain used to scrub every readable entity's attributes before the domain
filter dropped most of them, and every attribute name was matched against the credential tables
again. These tests pin which entities are scrubbed, that the filtered rows equal the unfiltered
search's rows for those entities, and that the cached check answers exactly as the uncached one.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import uuid

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import policy_engine
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import (
    SENSITIVE_ATTRIBUTES, SENSITIVE_KEY_FINAL_WORDS, SENSITIVE_KEY_SUBSTRINGS, SENSITIVE_KEY_SUFFIXES,
    SENSITIVE_WHOLE_KEYS,
)
from custom_components.phoenix_mcp.policy_engine import is_sensitive_key
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools import discovery


def _names() -> list[str]:
    """Every table entry, embedded and in other spellings, plus names that only resemble them."""
    base = [*SENSITIVE_ATTRIBUTES, *SENSITIVE_KEY_SUBSTRINGS, *SENSITIVE_WHOLE_KEYS, *SENSITIVE_KEY_FINAL_WORDS,
            *SENSITIVE_KEY_SUFFIXES, "friendly_name", "unit_of_measurement", "ping", "bypass", "pass_through",
            "access_key_id", "monkey", "keyboard", "cookies_enabled", "steam_appid", "", " ", "-"]
    names: list[str] = []
    for name in base:
        names += [name, name.upper(), name.title(), name.replace("_", "-"), name.replace("_", " "),
                  f"x_{name}", f"{name}_x", f"my-{name}", f"{name}count"]
    return names


def test_the_cached_check_answers_as_the_tables_do() -> None:
    names = _names()
    uncached = [policy_engine._sensitive_name.__wrapped__(name) for name in names]
    policy_engine._sensitive_name.cache_clear()
    assert [is_sensitive_key(name) for name in names] == uncached
    # Answered again from the cache, in the opposite order.
    assert [is_sensitive_key(name) for name in reversed(names)] == uncached[::-1]
    assert any(uncached) and not all(uncached)
    assert policy_engine._sensitive_name.cache_info().hits >= len(set(names))


def test_a_str_subclass_is_never_answered_from_or_into_the_cache() -> None:
    # Equal text, different spellings: one cached answer would serve the other.
    class Secret(str):
        def lower(self) -> str:
            return "password"

    class Plain(str):
        def lower(self) -> str:
            return "colour"

    policy_engine._sensitive_name.cache_clear()
    assert is_sensitive_key("colour") is False
    assert is_sensitive_key(Secret("colour")) is True
    assert is_sensitive_key(Plain("colour")) is False
    assert is_sensitive_key("colour") is False


@pytest.mark.parametrize("key", [None, 3, ("password",), b"password"])
def test_a_non_string_key_is_not_sensitive(key) -> None:
    assert is_sensitive_key(key) is False


def test_the_cache_is_bounded() -> None:
    assert policy_engine._sensitive_name.cache_info().maxsize == 4096


def _key(*, pass_through: bool = False) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="scrub-cost", access_key_hash="x", created_at=utcnow(), created_by="user1",
        cap_search="allow", pass_through=pass_through,
        permissions=PermissionTree(domains={domain: PermissionNode(state="GREEN")
                                            for domain in ("light", "sensor", "switch")}),
    )


def _home(hass: HomeAssistant) -> None:
    """Lights, sensors and switches from two integrations, some with secrets in their attributes."""
    registry = er.async_get(hass)
    for platform, domain, count in (("alpha", "light", 3), ("alpha", "sensor", 4), ("beta", "sensor", 2),
                                    ("beta", "switch", 3)):
        for index in range(count):
            row = registry.async_get_or_create(domain, platform, f"{platform}_{index}",
                                               suggested_object_id=f"{platform}_{domain}_{index}")
            hass.states.async_set(row.entity_id, "on" if index % 2 else "off", {
                "friendly_name": f"{platform} {domain} {index}", "device_class": "power" if index else None,
                "api_key": "hunter2", "nested": {"password": "hunter2", "ok": 1}})
    hass.states.async_set("sensor.unregistered", "7", {"friendly_name": "loose", "token": "hunter2"})


async def _search(hass: HomeAssistant, key: AccessKeyRecord, args: dict) -> tuple[dict, list[str]]:
    scrubbed: list[str] = []
    real = discovery.scrub_sensitive_attributes

    def recording(state, access_key, hass_):
        scrubbed.append(state.entity_id)
        return real(state, access_key, hass_)

    data = MagicMock()
    data.mesa = None
    data.store.get_entity_hints.return_value = {}
    with patch.object(discovery, "scrub_sensitive_attributes", side_effect=recording):
        content, outcome, _ = await _call_tool("search_entities", {"limit": 500, **args}, key, hass, data)
    assert outcome == "allowed", content
    assert "hunter2" not in content["content"][0]["text"]
    return json.loads(content["content"][0]["text"]), scrubbed


@pytest.mark.parametrize("pass_through", [False, True])
@pytest.mark.parametrize(("args", "keep"), [
    ({"domain": "sensor"}, lambda row: row["domain"] == "sensor"),
    ({"domain": ["light", "switch"]}, lambda row: row["domain"] in ("light", "switch")),
    ({"integration": "beta"}, lambda row: row["entity_id"].split(".")[1].startswith("beta")),
    ({"integration": "alpha", "domain": "sensor"},
     lambda row: row["domain"] == "sensor" and row["entity_id"].split(".")[1].startswith("alpha")),
    ({"domain": "sensor", "device_class": "power"}, lambda row: row["domain"] == "sensor"
     and row["device_class"] == "power"),
])
async def test_only_entities_a_filter_can_match_are_scrubbed(hass: HomeAssistant, pass_through, args, keep) -> None:
    _home(hass)
    key = _key(pass_through=pass_through)
    everything, scrubbed_all = await _search(hass, key, {})
    found, scrubbed = await _search(hass, key, args)
    expected = [row for row in everything["entities"] if keep(row)]
    assert found["entities"] == expected and found["total"] == len(expected) and expected
    # Only the domain and integration filters run before the scrub; device_class needs attributes.
    pre = {name: value for name, value in args.items() if name != "device_class"}
    pre_rows = [row for row in everything["entities"] if all(
        (row["domain"] in ([value] if isinstance(value, str) else value)) if name == "domain"
        else row["entity_id"].split(".")[1].startswith(value) for name, value in pre.items())]
    assert sorted(scrubbed) == sorted(row["entity_id"] for row in pre_rows)
    assert len(scrubbed) < len(scrubbed_all)


async def test_an_unregistered_entity_never_matches_an_integration_unscrubbed(hass: HomeAssistant) -> None:
    _home(hass)
    found, scrubbed = await _search(hass, _key(), {"integration": ["alpha", "beta"]})
    assert "sensor.unregistered" not in scrubbed
    assert "sensor.unregistered" not in [row["entity_id"] for row in found["entities"]]
    everything, _ = await _search(hass, _key(), {"domain": "sensor"})
    assert "sensor.unregistered" in [row["entity_id"] for row in everything["entities"]]
