"""Shared runtime helpers: explicit safety settings and Home Assistant result shapes."""

from unittest.mock import MagicMock

from custom_components.phoenix_mcp.access_key_store import GlobalSettings, AccessKeyStore


def mesa_off_store() -> MagicMock:
    """AccessKeyStore mock whose settings put MESA in off mode.

    For tests that isolate non-MESA behavior.
    """
    store = MagicMock(spec=AccessKeyStore)
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    return store


def assist_result_parts(result):
    """Split a Phoenix Assist tool result into its JSON object and error flag.

    HA 2026.10 takes llm.ToolResult, whose error flag providers forward to the model.
    Earlier releases take the plain object, which carries no flag, reported as None.
    """
    from homeassistant.helpers import llm

    result_type = getattr(llm, "ToolResult", None)
    if result_type is None:
        assert isinstance(result, dict)
        return result, None
    assert isinstance(result, result_type)
    return result.data, result.error
