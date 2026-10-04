"""Explicit safety settings for tests that isolate non-MESA behavior."""

from unittest.mock import MagicMock

from custom_components.phoenix_mcp.access_key_store import GlobalSettings, AccessKeyStore


def mesa_off_store() -> MagicMock:
    """AccessKeyStore mock whose settings put MESA in off mode.

    For tests that isolate non-MESA behavior.
    """
    store = MagicMock(spec=AccessKeyStore)
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    return store
