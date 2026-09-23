"""Side-effect-free access to HA's default sentence recognizer."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.core import HomeAssistant


class RecognitionUnavailable(Exception):
    """The installed HA agent does not expose the required recognition API."""


class SentenceTriggerMatch(Exception):
    """A sentence automation takes precedence over a built-in intent."""


async def async_recognize(
    hass: HomeAssistant, sentence: str, language: str, *, strict: bool = False,
) -> Any:
    """Recognize only; never process a conversation or invoke an intent handler.

    Strict recognition is optional on older HA releases. Never silently use
    fuzzy recognition for a routing caller that requested strict recognition.
    """
    from homeassistant.core import Context
    from homeassistant.components.conversation.agent_manager import async_get_agent
    from homeassistant.components.conversation.const import HOME_ASSISTANT_AGENT
    from homeassistant.components.conversation.models import ConversationInput

    try:
        agent = async_get_agent(hass, HOME_ASSISTANT_AGENT)
    except KeyError:
        agent = None
    recognize = getattr(agent, "async_recognize_intent", None)
    if not callable(recognize):
        raise RecognitionUnavailable
    if strict:
        code = getattr(recognize, "__code__", None)
        if code is None or "strict_intents_only" not in code.co_varnames[
            :code.co_argcount + code.co_kwonlyargcount
        ]:
            raise RecognitionUnavailable

    wanted = {
        "text": sentence, "context": Context(), "conversation_id": None,
        "device_id": None, "satellite_id": None, "language": language,
        "agent_id": HOME_ASSISTANT_AGENT, "extra_system_prompt": None,
    }
    declared = {field.name for field in dataclasses.fields(ConversationInput)}
    input_type: Any = ConversationInput
    user_input = input_type(**{key: val for key, val in wanted.items() if key in declared})
    if strict:
        trigger = getattr(agent, "async_recognize_sentence_trigger", None)
        if not callable(trigger):
            raise RecognitionUnavailable
        # This HA API only matches. Never call async_handle_sentence_triggers.
        if await trigger(user_input) is not None:
            raise SentenceTriggerMatch
        result = await recognize(user_input, strict_intents_only=True)
        if await trigger(user_input) is not None:
            raise SentenceTriggerMatch
        return result
    return await recognize(user_input)
