"""Content hashes are keyed with a saved secret, so a redacted read cannot confirm a guess.

A read returns a hash of the stored content for the write's compare-and-swap check. That
content can hold what the read redacted; a plain SHA-256 would let a caller rebuild the
content with a guessed hidden value and compare. The key survives a restart, so held
hashes still match, and removal deletes it.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.phoenix_mcp import content_hash_key
from custom_components.phoenix_mcp.const import CONTENT_HASH_KEY_STORAGE_KEY
from custom_components.phoenix_mcp.helpers import content_hash


async def test_the_saved_key_keeps_hashes_across_a_restart_and_removal_deletes_it(hass: HomeAssistant, hass_storage):
    await content_hash_key.async_load_content_hash_key(hass)
    saved = hass_storage[CONTENT_HASH_KEY_STORAGE_KEY]["data"]["key"]
    first = content_hash({"a": 1})
    # A restart: a fresh process key, then the saved one is loaded again.
    content_hash_key.set_content_hash_key(b"\x00" * 32)
    assert content_hash({"a": 1}) != first
    await content_hash_key.async_load_content_hash_key(hass)
    assert content_hash({"a": 1}) == first
    assert hass_storage[CONTENT_HASH_KEY_STORAGE_KEY]["data"]["key"] == saved

    await content_hash_key.async_remove_content_hash_key(hass)
    await hass.async_block_till_done()
    assert CONTENT_HASH_KEY_STORAGE_KEY not in hass_storage
    assert content_hash({"a": 1}) != first


async def test_a_damaged_saved_key_is_replaced(hass: HomeAssistant, hass_storage):
    hass_storage[CONTENT_HASH_KEY_STORAGE_KEY] = {"version": 1, "key": CONTENT_HASH_KEY_STORAGE_KEY,
                                                  "data": {"key": "not-hex"}}
    await content_hash_key.async_load_content_hash_key(hass)
    replaced = hass_storage[CONTENT_HASH_KEY_STORAGE_KEY]["data"]["key"]
    assert replaced != "not-hex" and len(replaced) == 64


async def test_unreadable_storage_keeps_hashes_keyed_with_the_process_key(hass: HomeAssistant, caplog):
    content_hash_key.set_content_hash_key(b"\x01" * 32)
    before = content_hash("x")
    with patch.object(content_hash_key.DurableStore, "async_load", AsyncMock(side_effect=OSError("disk"))):
        await content_hash_key.async_load_content_hash_key(hass)
    assert content_hash("x") == before != hashlib.sha256(b"x").hexdigest()
    assert "could not load its content hash key" in caplog.text
