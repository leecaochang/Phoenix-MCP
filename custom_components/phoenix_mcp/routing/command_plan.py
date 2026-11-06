"""Project recognition to an inert proposal and freeze it into a dispatchable plan."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from ..access_key_store import AccessKeyRecord
from ..entity_commands import valid_entity_value
from ..policy_engine import Permission, resolve, resolve_intent_entities
from .command_operations import (
    command_domain, numeric_operation, supports_command, valid_target_value, service_arguments,
)
from .command_summary import summary_current, local_summary
from .command_target import _digest, _target_fingerprint
from .numeric_recognition import NumericIdMatch
from .routing_tables import (
    COMMAND_ROUTING_INTENTS, COMMAND_ROUTING_NATIVE_ACTIONS, COMMAND_ROUTING_TARGET_SLOTS,
    COMMAND_CLARIFICATION_MAX_CHOICES, COMMAND_ROUTING_READ_INTENTS,
    COMMAND_ROUTING_TEMPORAL_OPERATIONS, COMMAND_ROUTING_VALUE_INTENTS, DECISION_ENTITY_CATEGORIES,
)
from .temporal_values import temporal_value_current
from .voice_context import voice_origin_current


def _proposal(result: Any, access_key: AccessKeyRecord, hass: HomeAssistant, *,
              candidates: list[str] | None = None, selected: str | None = None) -> tuple[str, dict | None]:
    """Map only supported, complete HA slots against current Phoenix scope."""
    if isinstance(result, NumericIdMatch):

        operation = numeric_operation(result.intent)
        if (operation is None or not operation.valid_value(result.value)
                or not valid_target_value(hass.states.get(result.entity_id), result.intent, result.value)
                or _target_fingerprint(hass, access_key, result.entity_id, result.intent) is None):
            return "no_target", None
        return "supported", {"intent": result.intent, "entity_ids": [result.entity_id],
                             operation.proposal_field: result.value}
    if result is None or getattr(result, "unmatched_entities", None):
        return "no_match", None
    metadata = getattr(result, "intent_metadata", None) or {}
    if metadata.get("hass_custom_sentence"):
        return "custom_sentence", None
    intent_name = result.intent.name
    if intent_name not in COMMAND_ROUTING_INTENTS:
        return "unsupported", None
    slots = {name: entity.value for name, entity in result.entities.items()}
    if intent_name == "HassGetState":
        summary = local_summary(hass, access_key, result, slots)
        if summary is not None:
            return "supported", summary
    if intent_name == "HassLightSet" and "temperature" in slots and "brightness" not in slots:
        intent_name = "PhoenixLightTemperature"
    operation = numeric_operation(intent_name)
    allowed = COMMAND_ROUTING_TARGET_SLOTS | ({operation.slot} if operation else set())
    # State comparisons, counts, relative brightness, floor/global commands,
    # custom slots and contextual/deferred actions belong to the normal provider.
    if slots.keys() - allowed:
        return "unsupported", None
    name, area = slots.get("name"), slots.get("area")
    native_action = intent_name in COMMAND_ROUTING_NATIVE_ACTIONS
    if native_action and not name:
        return "unsupported", None
    if not (name or area) or name == "all":
        return "unsupported", None
    if any(value is not None and (not isinstance(value, str) or not value.strip())
           for value in (name, area)):
        return "unsupported", None
    domains = slots.get("domain")
    device_class = slots.get("device_class")
    if any(value is not None and not isinstance(value, str) for value in (domains, device_class)):
        return "unsupported", None
    read_only = intent_name == "HassGetState"
    expected_domain = command_domain(intent_name)
    if (operation or native_action) and domains and domains != expected_domain:
        return "unsupported", None
    if read_only and (not name or getattr(result, "response", None) not in (None, "default", "one")):
        return "unsupported", None
    # Match the HA intent's domains, not only the domains Phoenix can execute.
    # Paired remotes cannot set volume; valves still make position ambiguous.
    target_domains = ([domains] if domains else [expected_domain] if native_action and expected_domain is not None else
                      list(operation.match_domains) if operation else None)
    ids = resolve_intent_entities(
        hass, access_key, name=name, area=area,
        domains=target_domains,
        device_classes=[device_class] if device_class else None, read_only=True,
        allow_duplicate_names=candidates is not None or selected is not None,
    )
    if name and len(ids) > 1:
        if selected in ids:
            ids = [selected]
        else:
            if candidates is not None:
                candidates.extend(sorted(ids))
            return 'no_target', None
    if not ids or (name and len(ids) != 1) or (read_only and len(ids) != 1):
        return "no_target", None
    if intent_name == "HassSetPosition" and all(eid.startswith("valve.") for eid in ids):
        intent_name = "PhoenixValvePosition"
        operation = numeric_operation(intent_name)
        expected_domain = "valve"
    if intent_name in ("HassTurnOn", "HassTurnOff") and len(ids) == 1:
        domain = next(iter(ids)).split(".", 1)[0]
        category = domain + ("_on" if intent_name == "HassTurnOn" else "_off")
        if category in DECISION_ENTITY_CATEGORIES:
            intent_name = DECISION_ENTITY_CATEGORIES[category]
            expected_domain = domain
    states = [hass.states.get(entity_id) for entity_id in ids]
    if any(state is None or state.state in ("unknown", "unavailable") for state in states):
        return "no_target", None
    if not read_only and any(state is None or not supports_command(state, intent_name) for state in states):
        return "unsupported", None
    if not read_only and any(
        not entity_id.startswith(f"{expected_domain}.") or resolve(entity_id, access_key, hass) != Permission.WRITE
        for entity_id in ids
    ):
        return "no_target", None
    if not read_only and not name and not operation and domains != expected_domain:
        return "unsupported", None
    proposal: dict[str, Any] = {"intent": intent_name, "entity_ids": sorted(ids)}
    if operation:
        value = slots.get(operation.slot)
        if not operation.valid_value(value):
            return "unsupported", None
        if any(state is None or not supports_command(state, intent_name) or not valid_target_value(state, intent_name, value) for state in states):
            return "unsupported", None
        proposal[operation.proposal_field] = value
    return "supported", proposal


def _recognition(record: dict) -> Any:
    return SimpleNamespace(intent=SimpleNamespace(name=record['intent']), response=record['response'],
        entities={key: SimpleNamespace(value=value) for key, value in record['slots'].items()},
        unmatched_entities={}, intent_metadata={})


def capture_choices(hass: HomeAssistant, data: Any, access_key: Any, recognized: Any, *,
                    ingress: str = 'voice', voice_origin: dict | None = None) -> dict | None:
    """Offer the whole small ambiguous set only when every alternative is eligible."""
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


def clarification_current(hass: HomeAssistant, data: Any, access_key: Any, proposal: dict) -> bool:
    """Bind the selected target and all competitors through dispatch and approval."""
    proof = proposal.get('clarification')
    if not isinstance(proof, dict):
        return False
    current = capture_choices(hass, data, access_key, _recognition(proof['recognition']),
        ingress=proof['ingress'], voice_origin=proof['voice_origin'])
    bare = {key: value for key, value in proposal.items() if key != 'clarification'}
    return current == proof and any(item['proposal'] == bare for item in proof['alternatives'])


def _temporal_current(hass: HomeAssistant, proposal: dict) -> bool:
    if 'voice_origin' in proposal:
        if not voice_origin_current(hass, proposal['voice_origin']):
            return False
    if proposal.get("intent") in COMMAND_ROUTING_VALUE_INTENTS:
        return all(valid_entity_value(hass.states.get(eid), proposal["intent"], proposal.get("value"))
                   for eid in proposal.get("entity_ids", []))
    spec = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(proposal.get("intent", ""))
    if spec is None:
        return True
    return all(temporal_value_current(proposal, hass.states.get(eid), dt_util.get_default_time_zone())
               for eid in proposal.get("entity_ids", []))


def _capture_plan(hass: HomeAssistant, data: Any, access_key: Any, proposal: dict, *, ingress: str = "voice") -> dict | None:
    if 'clarification' in proposal:
        if not clarification_current(hass, data, access_key, proposal):
            return None
    if proposal["intent"] not in COMMAND_ROUTING_READ_INTENTS and service_arguments(proposal) is None:
        return None
    operation = numeric_operation(proposal["intent"])
    if operation and any(not valid_target_value(hass.states.get(eid), proposal["intent"],
                                               proposal.get(operation.proposal_field)) for eid in proposal["entity_ids"]):
        return None
    if not _temporal_current(hass, proposal):
        return None
    if proposal['intent'] == 'PhoenixStateSummary':
        if not summary_current(hass, access_key, proposal):
            return None
    fingerprints = {entity_id: _target_fingerprint(hass, access_key, entity_id, proposal["intent"])
                    for entity_id in proposal["entity_ids"]}
    if not fingerprints or any(value is None for value in fingerprints.values()):
        return None
    return {"proposal": proposal, "fingerprints": fingerprints, "access_key_id": access_key.id, "ingress": ingress,
            "access_key_version": access_key.settings_version, "settings": _digest(data.store.get_settings().to_dict())}
