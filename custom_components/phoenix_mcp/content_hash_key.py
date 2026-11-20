"""The saved secret that keys every content hash Phoenix hands out.

Read tools return a hash of the stored content so a later write can refuse when it changed.
That content can hold what the read redacted, so the hash is keyed (helpers.keyed_hash): a
caller cannot test a guessed hidden value against it. The key is saved, so a hash an agent
holds and those in recovery receipts still match after a restart. Removal deletes it.
"""

from __future__ import annotations

import logging
import re
import secrets

from homeassistant.core import HomeAssistant

from .const import CONTENT_HASH_KEY_STORAGE_KEY, CONTENT_HASH_KEY_STORAGE_VERSION
from .durable_storage import DurableStore
from .helpers import set_content_hash_key

_LOGGER = logging.getLogger(__name__)
_KEY = re.compile(r"[0-9a-f]{64}\Z")


def _store(hass: HomeAssistant) -> DurableStore[dict]:
    return DurableStore(hass, CONTENT_HASH_KEY_STORAGE_VERSION, CONTENT_HASH_KEY_STORAGE_KEY,
                        private=True, atomic_writes=True)


async def async_load_content_hash_key(hass: HomeAssistant) -> None:
    """Load the saved key, creating it on first use, and use it for every content hash.

    When it cannot be read or saved, hashes stay keyed with this process's own key: they are
    still not guessable, but they change at the next restart.
    """
    store = _store(hass)
    try:
        saved = await store.async_load()
        key = saved.get("key") if isinstance(saved, dict) else None
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            key = secrets.token_hex(32)
            await store.async_save({"key": key})
    except Exception:  # the process key stays in use
        _LOGGER.warning("Phoenix MCP could not load its content hash key; hashes change after a restart",
                        exc_info=True)
        return
    set_content_hash_key(bytes.fromhex(key))


async def async_remove_content_hash_key(hass: HomeAssistant) -> None:
    """Delete the saved key at removal and stop using it."""
    set_content_hash_key(secrets.token_bytes(32))
    await _store(hass).async_remove()
