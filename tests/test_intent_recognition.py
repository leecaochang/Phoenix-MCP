"""Optional HA recognition features fail closed without changing diagnostics."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.intent_recognition import RecognitionUnavailable, async_recognize


async def test_strict_mode_is_never_downgraded(hass):
    calls = []
    async def old_recognizer(user_input):
        calls.append(user_input)
        return None
    with patch("homeassistant.components.conversation.agent_manager.async_get_agent",
               return_value=SimpleNamespace(async_recognize_intent=old_recognizer)):
        with pytest.raises(RecognitionUnavailable):
            await async_recognize(hass, "hello", "en", strict=True)
        assert calls == []
        assert await async_recognize(hass, "hello", "en") is None
        assert len(calls) == 1


async def test_missing_agent_is_unavailable(hass):
    with patch("homeassistant.components.conversation.agent_manager.async_get_agent", side_effect=KeyError):
        with pytest.raises(RecognitionUnavailable):
            await async_recognize(hass, "hello", "en", strict=True)
