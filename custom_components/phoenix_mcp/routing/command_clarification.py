"""One-use, expiring target clarification with fresh recognition and no saved authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant

from ..catalog_text import voice_text
from ..llm.accounts import decision_account_fingerprint
from .command_plan import clarification_current
from .command_routing import async_evaluate_command, routing_language
from .numeric_evidence import read_choice_number
from .routing_tables import COMMAND_CLARIFICATION_CANCEL


def clarification_binding(hass: HomeAssistant, authority: Any, language: object, origin: dict | None = None) -> list:
    settings = authority.settings
    return [authority.access_key_id, authority.access_key_hash, authority.version, language, authority.settings_values,
            decision_account_fingerprint(hass, settings.decision_provider_id), origin]


def clarification_prompt(language: object, request: dict) -> str:
    choices = '; '.join(f"{index}. {item['entity_id']}" for index, item in enumerate(request['choices']['alternatives'], 1))
    return voice_text(language, 'local_clarification', choices=choices)


def is_clarification_cancel(text: str) -> bool:
    """Recognize only a complete cancellation reply to a pending choice."""
    return text.strip().casefold().rstrip('.!?。！？') in COMMAND_CLARIFICATION_CANCEL


async def async_clarification_reply(hass: HomeAssistant, data: Any, access_key: Any, request: dict,
                                    text: str, language: str, *, ingress: str,
                                    voice_origin: dict | None = None) -> tuple[dict | None, str | None]:
    """Only a bare numbered choice or exact ID can complete one pending request."""

    if request.get('_expired'):
        return None, voice_text(language, 'local_clarification_expired')
    answer = text.strip().casefold().rstrip('.!?。！？')
    if is_clarification_cancel(text):
        return None, voice_text(language, 'local_cancelled')
    alternatives = request['choices']['alternatives']
    selected = next((item for index, item in enumerate(alternatives, 1)
                     if answer in (str(index), item['entity_id'])), None)
    if selected is None:
        number = await hass.async_add_executor_job(read_choice_number, answer,
            routing_language(language, preview=True) or language, len(alternatives))
        if number is not None:
            selected = alternatives[number-1]
    if selected is None:
        # Consume once even when the user starts a different command.
        return None, None
    fresh = await async_evaluate_command(hass, data, access_key, request['sentence'], language,
        ingress=ingress, voice_origin=voice_origin, clarify=True)
    if fresh.get('_clarification') != request:
        return None, voice_text(language, 'local_clarification_expired')
    proposal = deepcopy(selected['proposal'])
    proposal['clarification'] = deepcopy(request['choices'])
    if not clarification_current(hass, data, access_key, proposal):
        return None, voice_text(language, 'local_clarification_expired')
    return proposal, None
