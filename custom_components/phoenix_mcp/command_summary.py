"""Closed current-state queries over complete scoped Assist entity collections."""
from __future__ import annotations

from collections import Counter
import json
from typing import Any

from homeassistant.helpers import area_registry as ar, entity_registry as er

from .const import COMMAND_SUMMARY_STATES, COMMAND_SUMMARY_UNKNOWN_STATES, COMMAND_SUMMARY_MAX_STATES, DECISION_CATALOG_PACK_THRESHOLD, DECISION_BATCH_MAX_TARGETS, DECISION_MAX_TARGET_BYTES
from .device_semantics import device_registry_snapshot
from .helpers import voice_text, redact_secrets_in_text, redact_inaccessible_entity_ids_in_text


def summary_catalog(hass: Any, token: Any, *, devices: Any = None,
                    max_targets: int = DECISION_BATCH_MAX_TARGETS, max_bytes: int = DECISION_MAX_TARGET_BYTES) -> Any:
    """No state values leave Phoenix; unavailable members remain in the denominator."""
    from .command_execution import _target_fingerprint, _digest
    from .decision_targets import TargetSnapshot, CatalogLimitError
    devices = devices if devices is not None else device_registry_snapshot(hass)
    registry, areas = er.async_get(hass), ar.async_get(hass)
    groups: dict[str, dict] = {}
    fingerprints = {}
    for state in sorted(hass.states.async_all(), key=lambda item: item.entity_id):
        fingerprint = _target_fingerprint(hass, token, state.entity_id, 'PhoenixStateSummary', devices=devices)
        if fingerprint is None:
            continue
        fingerprints[state.entity_id] = fingerprint
        entry = registry.async_get(state.entity_id)
        assert entry is not None
        area_id = entry.area_id or devices.effective_area_id(entry.device_id)
        area = areas.async_get_area(area_id) if area_id else None
        device_class = state.attributes.get('device_class')
        classes = ['', device_class] if isinstance(device_class, str) and device_class else ['']
        for location in ([None, area] if area else [None]):
            for classification in classes:
                selectors = {'domain': state.domain, 'device_class': classification,
                             'area_id': location.id if location else ''}
                key = _digest(selectors)
                descriptor = {'summary_scope': selectors, 'area': location.name if location else '',
                              'area_aliases': sorted(location.aliases) if location else [],
                              'states': [*COMMAND_SUMMARY_STATES.get(state.domain, ()), *COMMAND_SUMMARY_UNKNOWN_STATES] }
                groups[key] = descriptor
    targets = {f's{index}': groups[key] for index, key in enumerate(sorted(groups))}
    encoded = json.dumps(summary_wire(targets), ensure_ascii=False)
    if len(targets) > max_targets:
        raise CatalogLimitError('state_summary', 'target_count', len(targets), max_targets)
    if len(encoded.encode()) > max_bytes:
        raise CatalogLimitError('state_summary', 'metadata_bytes', len(encoded.encode()), max_bytes)
    if redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(encoded) or '', token, hass) != encoded:
        from .decision_provider import DecisionError
        raise DecisionError('context_unavailable')
    return TargetSnapshot(targets, fingerprints)


def summary_members(hass: Any, token: Any, scope: dict) -> list[str]:
    """Resolve an exact server-owned collection, never a generated selector."""
    from .command_execution import _target_fingerprint
    devices = device_registry_snapshot(hass)
    registry = er.async_get(hass)
    ids = []
    for state in hass.states.async_all(scope['domain']):
        if _target_fingerprint(hass, token, state.entity_id, 'PhoenixStateSummary', devices=devices) is None:
            continue
        entry = registry.async_get(state.entity_id)
        assert entry is not None
        if scope['device_class'] and state.attributes.get('device_class') != scope['device_class']:
            continue
        if scope['area_id'] and (entry.area_id or devices.effective_area_id(entry.device_id)) != scope['area_id']:
            continue
        ids.append(state.entity_id)
    return sorted(ids)


def summary_proposal(hass: Any, token: Any, descriptor: dict, mode: str) -> dict | None:
    if mode not in ('total', 'breakdown', *descriptor['states']):
        return None
    ids = summary_members(hass, token, descriptor['summary_scope'])
    if not ids:
        return None
    return {'intent': 'PhoenixStateSummary', 'entity_ids': ids,
            'summary_scope': descriptor['summary_scope'], 'summary_mode': mode}


def summary_current(hass: Any, token: Any, proposal: dict) -> bool:
    """Revalidate collection membership as well as individual entity identities."""
    if set(proposal) - {'voice_origin'} != {'intent', 'entity_ids', 'summary_scope', 'summary_mode'}:
        return False
    scope = proposal['summary_scope']
    if not isinstance(scope, dict) or set(scope) != {'domain', 'device_class', 'area_id'}:
        return False
    if not all(isinstance(value, str) for value in scope.values()) or not scope['domain']:
        return False
    allowed = ('total', 'breakdown', *COMMAND_SUMMARY_STATES.get(scope['domain'], ()), *COMMAND_SUMMARY_UNKNOWN_STATES)
    return (proposal['summary_mode'] in allowed and bool(proposal['entity_ids'])
            and summary_members(hass, token, scope) == proposal['entity_ids'])


def local_summary(hass: Any, token: Any, result: Any, slots: dict) -> dict | None:
    """Use installed HassGetState count grammar, including its native state mapping."""
    if getattr(result, 'response', None) not in ('how_many',):
        return None
    if slots.keys() - {'domain', 'device_class', 'area', 'state'} or not isinstance(slots.get('domain'), str):
        return None
    area_name = slots.get('area', '')
    if not isinstance(area_name, str) or not isinstance(slots.get('device_class', ''), str):
        return None
    areas = [area for area in ar.async_get(hass).areas.values()
             if area_name.casefold() in {str(value).casefold() for value in (area.id, area.name, *area.aliases)}] if area_name else [None]
    candidates = []
    for area in areas:
        scope = {'domain': slots['domain'], 'device_class': slots.get('device_class', ''),
                 'area_id': area.id if area else ''}
        candidate = {'intent': 'PhoenixStateSummary', 'entity_ids': summary_members(hass, token, scope),
                     'summary_scope': scope, 'summary_mode': slots.get('state', 'total')}
        if summary_current(hass, token, candidate):
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def summary_text(proposal: dict, payload: Any, language: object) -> str | None:
    """Count only fully returned authorized members; missing data is never zero."""
    if not isinstance(payload, list):
        return None
    ids = set(proposal['entity_ids'])
    rows = [row for row in payload if isinstance(row, dict) and isinstance(row.get('entity_id'), str) and row['entity_id'] in ids]
    states = {row['entity_id']: row.get('state') for row in rows}
    if len(states) != len(rows):
        return None
    if set(states) != ids or any(not isinstance(value, str) or len(value) > 128 for value in states.values()):
        return None
    counts = Counter(states.values())
    unknown = counts.get('unknown', 0) + counts.get('unavailable', 0)
    mode = proposal['summary_mode']
    if mode == 'total':
        return voice_text(language, 'local_summary_total', total=len(ids), unknown=unknown)
    if mode == 'breakdown':
        if len(counts) > COMMAND_SUMMARY_MAX_STATES:
            return None
        return voice_text(language, 'local_summary_breakdown', total=len(ids), summary=', '.join(f'{state}: {count}' for state, count in sorted(counts.items())))
    return voice_text(language, 'local_summary_count', matched=counts.get(mode, 0), total=len(ids), state=mode, unknown=unknown)


def summary_wire(targets: dict[str, dict]) -> dict:
    """Losslessly encode query scopes separately from single-entity Choice limits."""
    plain = {'summary_scopes': targets}
    if len(json.dumps(plain, ensure_ascii=False).encode()) <= DECISION_CATALOG_PACK_THRESHOLD:
        return plain
    fields = ['domain', 'device_class', 'area_id', 'area', 'area_aliases']
    rows = {}
    states = {}
    for key, item in targets.items():
        scope = item['summary_scope']
        rows[key] = [scope['domain'], scope['device_class'], scope['area_id'], item['area'], item['area_aliases']]
        states[scope['domain']] = item['states']
    packed = {'summary_scopes': rows, 'summary_scope_fields': fields, 'summary_domain_states': states}
    # Large homes repeat the same area identity across many domains and classes.
    # Pool exact tuples, not names alone, so aliases and distinct IDs survive.
    identities: dict[str, str] = {}
    areas = {}
    compact_rows = {}
    for key, row in rows.items():
        identity = json.dumps(row[2:], ensure_ascii=False)
        reference = identities.get(identity)
        if reference is None:
            reference = f"a{len(identities)}"
            identities[identity] = reference
            areas[reference] = dict(zip(fields[2:], row[2:], strict=True))
        compact_rows[key] = [row[0], row[1], reference]
    pooled = {'summary_scopes': compact_rows, 'summary_scope_fields': ['domain', 'device_class', 'area_ref'],
              'summary_areas': areas, 'summary_domain_states': states}
    return min((packed, pooled), key=lambda value: len(json.dumps(value, ensure_ascii=False).encode()))
