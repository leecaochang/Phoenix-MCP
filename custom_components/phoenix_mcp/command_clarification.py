"""One-use, expiring target clarification with fresh recognition and no saved authority."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import secrets
from time import monotonic
from types import SimpleNamespace
from typing import Any

from .const import (COMMAND_CLARIFICATION_LIMIT, COMMAND_CLARIFICATION_TTL_SECONDS,
    COMMAND_CLARIFICATION_MAX_CHOICES, COMMAND_CLARIFICATION_CANCEL)
from .helpers import voice_text


@dataclass
class CommandClarifications:
    """Bound ephemeral utterances to an exact conversation and current configuration."""

    _key: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    _entries: OrderedDict[str, tuple[float, str, dict]] = field(default_factory=OrderedDict, repr=False)

    def _digest(self, value: Any) -> str:
        return hmac.new(self._key, json.dumps(value, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()

    def remember(self, conversation: Any, binding: Any, request: dict) -> None:
        now = monotonic()
        self._entries = OrderedDict((key, value) for key, value in self._entries.items() if value[0] > now)
        key = self._digest(conversation)
        self._entries[key] = (now + COMMAND_CLARIFICATION_TTL_SECONDS, self._digest(binding), deepcopy(request))
        self._entries.move_to_end(key)
        while len(self._entries) > COMMAND_CLARIFICATION_LIMIT:
            self._entries.popitem(last=False)

    def consume(self, conversation: Any, binding: Any) -> dict | None:
        entry = self._entries.pop(self._digest(conversation), None)
        if entry is None:
            return None
        if entry[0] <= monotonic() or not hmac.compare_digest(entry[1], self._digest(binding)):
            return {'_expired': True}
        return entry[2]

    def clear(self) -> None:
        """Discard retained utterances and bindings on an explicit core wipe."""
        self._entries.clear()
        self._key = secrets.token_bytes(32)


def clarification_binding(hass: Any, authority: Any, language: object, origin: dict | None = None) -> list:
    from .agentcli import decision_account_fingerprint
    settings = authority.settings
    return [authority.access_key_id, authority.access_key_hash, authority.version, language, authority.settings_values,
            decision_account_fingerprint(hass, settings.decision_provider_id), origin]


def _recognition(record: dict) -> Any:
    return SimpleNamespace(intent=SimpleNamespace(name=record['intent']), response=record['response'],
        entities={key: SimpleNamespace(value=value) for key, value in record['slots'].items()},
        unmatched_entities={}, intent_metadata={})


def capture_choices(hass: Any, data: Any, access_key: Any, recognized: Any, *,
                    ingress: str = 'voice', voice_origin: dict | None = None) -> dict | None:
    """Offer the whole small ambiguous set only when every alternative is eligible."""
    from .command_routing import _proposal
    from .command_execution import _capture_plan
    from homeassistant.helpers import area_registry as ar

    ids: list[str] = []
    reason, proposal = _proposal(recognized, access_key, hass, candidates=ids)
    if reason != 'no_target' or proposal or not 2 <= len(ids) <= COMMAND_CLARIFICATION_MAX_CHOICES:
        return None
    alternatives = []
    for entity_id in ids:
        _, candidate = _proposal(recognized, access_key, hass, selected=entity_id)
        if candidate is None:
            return None
        if voice_origin is not None:
            candidate['voice_origin'] = deepcopy(voice_origin)
        plan = _capture_plan(hass, data, access_key, candidate, ingress=ingress)
        if plan is None:
            return None
        alternatives.append({'entity_id': entity_id, 'proposal': candidate, 'fingerprints': plan['fingerprints']})
    record = {'intent': recognized.intent.name, 'response': getattr(recognized, 'response', None),
              'slots': {key: entity.value for key, entity in recognized.entities.items()}}
    # Area names/aliases participate in matching but not entity fingerprints.
    areas = [(area.id, area.name, sorted(area.aliases)) for area in ar.async_get(hass).areas.values()]
    area_digest = hashlib.sha256(json.dumps(sorted(areas)).encode()).hexdigest()
    return {'recognition': record, 'alternatives': alternatives, 'areas': area_digest,
            'ingress': ingress, 'voice_origin': voice_origin}


def clarification_current(hass: Any, data: Any, access_key: Any, proposal: dict) -> bool:
    """Bind the selected target and all competitors through dispatch and approval."""
    proof = proposal.get('clarification')
    if not isinstance(proof, dict):
        return False
    current = capture_choices(hass, data, access_key, _recognition(proof['recognition']),
        ingress=proof['ingress'], voice_origin=proof['voice_origin'])
    bare = {key: value for key, value in proposal.items() if key != 'clarification'}
    return current == proof and any(item['proposal'] == bare for item in proof['alternatives'])


def clarification_prompt(language: object, request: dict) -> str:
    choices = '; '.join(f"{index}. {item['entity_id']}" for index, item in enumerate(request['choices']['alternatives'], 1))
    return voice_text(language, 'local_clarification', choices=choices)


def is_clarification_cancel(text: str) -> bool:
    """Recognize only a complete cancellation reply to a pending choice."""
    return text.strip().casefold().rstrip('.!?。！？') in COMMAND_CLARIFICATION_CANCEL


async def async_clarification_reply(hass: Any, data: Any, access_key: Any, request: dict,
                                    text: str, language: str, *, ingress: str,
                                    voice_origin: dict | None = None) -> tuple[dict | None, str | None]:
    """Only a bare numbered choice or exact ID can complete one pending request."""
    from .command_routing import async_evaluate_command, routing_language

    if request.get('_expired'):
        return None, voice_text(language, 'local_clarification_expired')
    answer = text.strip().casefold().rstrip('.!?。！？')
    if is_clarification_cancel(text):
        return None, voice_text(language, 'local_cancelled')
    alternatives = request['choices']['alternatives']
    selected = next((item for index, item in enumerate(alternatives, 1)
                     if answer in (str(index), item['entity_id'])), None)
    if selected is None:
        from .numeric_evidence import read_choice_number
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
