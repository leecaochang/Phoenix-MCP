"""Bounded Home Companion reports derived from scoped dispatch evidence.

Reports grant no execution authority. A receipt proves acceptance, while only
later state observations can verify the requested device settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from homeassistant.util.color import color_name_to_rgb

from .const import HOME_COMPANION_CONTROL_TOOLS, HOME_COMPANION_CONTROL_DOMAINS, HOME_COMPANION_MAX_REPORT_ENTITIES, HOME_COMPANION_MAX_REPORT_NAMES, HOME_COMPANION_MAX_CALLS, CONVERSATION_STYLES, DETAIL_LEVELS, HOME_COMPANION_ACCEPTED_TEMPLATES, HOME_COMPANION_COUNT_TEMPLATES, HOME_COMPANION_VERIFIED_TEMPLATES, HOME_COMPANION_COUNT_KIND_TEMPLATES, HOME_COMPANION_POWER_TEMPLATES, HOME_COMPANION_ACTION_KIND_TEMPLATES, HOME_COMPANION_EXCEPTION_TEMPLATES
from .helpers import voice_text


def _body(result: dict | None) -> Any:
    try:
        return json.loads(''.join(block['text'] for block in (result or {}).get('content', [])
                                 if block.get('type') == 'text'))
    except (ValueError, KeyError, TypeError):
        return None


def _expected(name: str, arguments: dict, entity_id: str = 'light.unknown') -> dict:
    """Use only fixed native projections of the admitted command arguments."""
    if name in {'intent__HassTurnOn', 'intent__HassTurnOff'}:
        domain = entity_id.split('.')[0]
        if domain in {'light', 'fan', 'switch', 'input_boolean', 'humidifier'}:
            return {'state': 'on' if name == 'intent__HassTurnOn' else 'off'}
        if domain in {'cover', 'valve'}:
            return {'state': 'open' if name == 'intent__HassTurnOn' else 'closed'}
        if domain == 'climate' and name == 'intent__HassTurnOff':
            return {'state': 'off'}
        return {}
    if name == 'climate__HassClimateSetTemperature':
        return {'temperature': arguments.get('temperature')}
    if name == 'intent__HassSetPosition':
        return {'current_position': arguments.get('position')}
    if name == 'media_player__HassSetVolume':
        volume = arguments.get('volume_level')
        return {'volume_level': volume / 100} if isinstance(volume, (int, float)) and not isinstance(volume, bool) else {}
    if name in {'media_player__HassMediaPlayerMute', 'media_player__HassMediaPlayerUnmute'}:
        return {'is_volume_muted': name == 'media_player__HassMediaPlayerMute'}
    if name in {'media_player__HassMediaPause', 'media_player__HassMediaUnpause'}:
        return {'state': 'paused' if name == 'media_player__HassMediaPause' else 'playing'}
    expected: dict[str, Any] = {}
    if name == 'fan__HassFanSetSpeed':
        expected['percentage'] = arguments.get('percentage')
    elif name == 'light__HassLightSet':
        if arguments.get('brightness') is not None:
            expected['brightness'] = round(arguments['brightness'] * 255 / 100)
        if arguments.get('temperature') is not None:
            expected['color_temp_kelvin'] = arguments['temperature']
        if arguments.get('color') is not None:
            try:
                expected['rgb_color'] = list(color_name_to_rgb(arguments['color']))
            except (ValueError, TypeError):
                return {}
    return expected


@dataclass
class ResponseEvidence:
    """Request-local facts, invalidating readback on each new control attempt."""

    attempts: int = 0
    accepted: int = 0
    refused: int = 0
    pending: int = 0
    successful_reads: int = 0
    last_read_failed: bool = False
    targets: dict[str, str] = field(default_factory=dict)
    areas: dict[str, str] = field(default_factory=dict)
    candidates: dict[str, str] = field(default_factory=dict)
    expected: dict[str, dict] = field(default_factory=dict)
    observed: dict[str, dict] = field(default_factory=dict)
    readings: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    incomplete: bool = False
    receipt_only: bool = False
    calls: int = 0
    search_totals: dict[int, int] = field(default_factory=dict)
    search_kinds: dict[int, str] = field(default_factory=dict)
    selected_count: int | None = None
    selected_kind: str = "other"

    def observe(self, name: str, outcome: str, result: dict | None, arguments: dict | None = None) -> None:
        index = self.calls
        self.calls += 1
        control = name in HOME_COMPANION_CONTROL_TOOLS
        body = _body(result)
        if control:
            self.attempts += 1
            self.observed.clear()
            if outcome == 'pending_approval':
                self.pending += 1
                return
            if outcome != 'allowed' or not result or result.get('isError'):
                self.refused += 1
                return
            if not isinstance(body, dict) or body.get('response_type') != 'action_done':
                self.incomplete = True
                return
            data = body.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('success'), list) or not isinstance(data.get('failed'), list):
                self.incomplete = True
                return
            if data['failed'] or body.get('not_permitted'):
                self.refused += 1
            rows = [row for row in data['success'] if isinstance(row, dict) and row.get('type') == 'entity']
            if name == 'intent__HassCancelAllTimers' and not data['failed'] and not body.get('not_permitted'):
                slots = body.get('speech_slots')
                canceled = slots.get('canceled') if isinstance(slots, dict) else None
                if type(canceled) is int and canceled >= 0:
                    self.accepted += 1
                    self.receipt_only = True
                    return
            if not rows:
                self.incomplete = True
            accepted = False
            for row in rows:
                eid = row.get('id')
                if not isinstance(eid, str) or eid.split('.')[0] not in HOME_COMPANION_CONTROL_DOMAINS:
                    self.incomplete = True
                    continue
                if eid not in self.targets and len(self.targets) >= HOME_COMPANION_MAX_REPORT_ENTITIES:
                    self.incomplete = True
                    continue
                label = row.get('name')
                self.targets[eid] = label if isinstance(label, str) else eid
                self.expected[eid] = _expected(name, arguments or {}, eid)
                accepted = True
            if accepted:
                self.accepted += 1
            warning = body.get('speech', {}).get('plain', {}).get('speech')
            if isinstance(warning, str) and warning and len(self.warnings) < HOME_COMPANION_MAX_CALLS:
                self.warnings.append(warning)
            return
        if outcome != 'allowed' or not result or result.get('isError'):
            self.last_read_failed = True
            return
        if name == 'search_entities' and isinstance(body, dict) and isinstance(body.get('entities'), list):
            total = body.get('total')
            if type(total) is int and total >= 0:
                self.search_totals[index] = total
                domain = (arguments or {}).get('domain')
                if isinstance(domain, list) and len(domain) == 1:
                    domain = domain[0]
                self.search_kinds[index] = domain if isinstance(domain, str) and domain in HOME_COMPANION_COUNT_KIND_TEMPLATES else 'other'
        if name not in {'get_capability_summary'}:
            self.successful_reads += 1
        self.last_read_failed = False
        rows = (body['states'] if name == 'get_state' and isinstance(body, dict) and isinstance(body.get('states'), list) else
                [body] if name == 'get_state' and isinstance(body, dict) else
                body.get('entities', []) if name == 'search_entities' and isinstance(body, dict) else [])
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('entity_id'), str):
                continue
            eid = row['entity_id']
            attrs: dict = row['attributes'] if isinstance(row.get('attributes'), dict) else {}
            label = row.get('friendly_name') or attrs.get('friendly_name') or eid
            if not isinstance(label, str):
                label = eid
            if len(self.candidates) < HOME_COMPANION_MAX_REPORT_ENTITIES:
                self.candidates[eid] = label
            if name == 'get_state' and eid in self.targets:
                self.observed[eid] = {**attrs, 'state': row.get('state')}
            if name in {'get_state', 'search_entities'} and isinstance(row.get('state'), str) and len(self.readings) < HOME_COMPANION_MAX_REPORT_ENTITIES:
                unit = attrs.get('unit_of_measurement', '')
                self.readings[eid] = f"{label}: {row['state']}" + (f" {unit}" if isinstance(unit, str) and unit else '')

    def valid_read_answer(self, proposal: Any) -> bool:
        """Validate a count reference without selecting or altering evidence."""
        return not (not isinstance(proposal, dict) or set(proposal) != {'kind', 'source'}
                or proposal['kind'] != 'count' or type(proposal['source']) is not int
                or proposal['source'] not in self.search_totals or self.attempts)

    def select_read_answer(self, proposal: Any) -> bool:
        """Allow a model to select a real scoped count source, never supply the count."""
        if not self.valid_read_answer(proposal):
            return False
        self.selected_count = self.search_totals[proposal['source']]
        self.selected_kind = self.search_kinds[proposal['source']]
        return True

    @property
    def matched_targets(self) -> set[str]:
        """Match each admitted target against its own fresh, complete settings evidence."""
        matched = set()
        for eid in self.targets:
            expected = self.expected.get(eid, {})
            observed = self.observed.get(eid, {})
            if not expected:
                continue
            matches = True
            for key, value in expected.items():
                actual = observed.get(key)
                if key == 'brightness':
                    if type(actual) not in (int, float) or abs(actual - value) > 1:
                        matches = False
                elif key == 'rgb_color':
                    if not isinstance(actual, (list, tuple)) or list(actual) != value:
                        matches = False
                elif key in {'temperature', 'current_position', 'volume_level', 'percentage'}:
                    if type(actual) not in (int, float) or value is None or actual != value:
                        matches = False
                elif key == 'is_volume_muted':
                    if type(actual) is not bool or actual != value:
                        matches = False
                elif actual != value:
                    matches = False
            if matches:
                matched.add(eid)
        return matched

    @property
    def verified(self) -> bool:
        return bool(self.targets) and not self.incomplete and not self.receipt_only and len(self.matched_targets) == len(self.targets)


def _target_phrase(evidence: ResponseEvidence, targets: set[str], language: object) -> str:
    """Summarize multiple targets only with their admitted canonical room evidence."""
    areas = {evidence.areas.get(eid) for eid in targets}
    if len(targets) > 1 and len(areas) == 1 and (area := next(iter(areas))):
        domains = {eid.split('.')[0] for eid in targets}
        kind = next(iter(domains)) if len(domains) == 1 else 'other'
        kind_template = HOME_COMPANION_ACTION_KIND_TEMPLATES.get(kind, HOME_COMPANION_ACTION_KIND_TEMPLATES['other'])
        return voice_text(language, 'companion_group_targets', count=len(targets),
                          kind=voice_text(language, kind_template), area=area)
    return ', '.join(
        [evidence.targets[eid] for eid in evidence.targets if eid in targets][:HOME_COMPANION_MAX_REPORT_NAMES])


def _verification_exceptions(evidence: ResponseEvidence, unmatched: set[str], language: object) -> list[str]:
    unavailable = {eid for eid in unmatched if evidence.observed.get(eid, {}).get('state') == 'unavailable'}
    parts = []
    for ids, key in [(unavailable, HOME_COMPANION_EXCEPTION_TEMPLATES['unavailable']),
                     (unmatched - unavailable, HOME_COMPANION_EXCEPTION_TEMPLATES['unverified'])]:
        if ids:
            names = ', '.join([evidence.targets[eid] for eid in evidence.targets if eid in ids][:HOME_COMPANION_MAX_REPORT_NAMES])
            parts.append(voice_text(language, key, count=len(ids), names=names))
    return parts


def build_report(status: str, model_message: str, evidence: ResponseEvidence, language: object,
                 *, recovery_required: bool = False, style: str = "direct", detail: str = "balanced") -> dict:
    """Choose execution claims and required next steps independently of model prose."""
    style = style if style in CONVERSATION_STYLES else 'direct'
    detail = detail if detail in DETAIL_LEVELS else 'concise'
    labels = list(dict.fromkeys((evidence.targets if evidence.attempts else evidence.candidates).values()))[:HOME_COMPANION_MAX_REPORT_NAMES]
    execution, verification, next_step = 'not_attempted', 'not_verified', 'none'
    retry = 'no_replay' if evidence.attempts else 'new_request'
    parts: list[str] = []
    if status == 'authority_lost':
        execution = 'uncertain' if evidence.attempts else 'not_attempted'
        next_step = 'check_state' if evidence.attempts else 'check_access'
        parts.append(voice_text(language, 'access_key_unavailable'))
        if evidence.attempts:
            parts.append(voice_text(language, 'companion_unverified'))
    elif recovery_required or status == 'outcome_unknown':
        status, next_step, retry = 'outcome_unknown', 'resolve_recovery', 'no_replay'
        execution = 'uncertain' if evidence.attempts else 'not_attempted'
        parts.append(voice_text(language, 'outcome_unknown' if evidence.attempts else 'prior_action_unresolved'))
    elif evidence.pending or status == 'pending_approval':
        status, next_step = 'pending_approval', 'review_approval'
        execution = 'partial' if evidence.accepted else 'pending_approval'
        retry = 'no_replay'
        if evidence.accepted:
            parts.append(voice_text(language, 'companion_accepted'))
        parts.append(voice_text(language, 'local_pending'))
    elif evidence.accepted:
        partial = bool(evidence.refused or evidence.incomplete or status in {'clarification_needed', 'refused'})
        status = 'partial' if partial else 'completed'
        execution = 'partial' if partial else 'accepted'
        all_targets = set(evidence.targets)
        matched = evidence.matched_targets if not evidence.incomplete else set()
        power = {expected.get('state') for expected in evidence.expected.values()}
        parts.append(voice_text(language, 'companion_partial') if partial else
                     voice_text(language, 'companion_accepted') if not labels else
                     voice_text(language, HOME_COMPANION_ACCEPTED_TEMPLATES[style],
                                names=_target_phrase(evidence, all_targets, language) if detail == 'concise' else ', '.join(labels)))
        if evidence.verified:
            verification = 'confirmed'
            if detail == 'concise' and power in ({'on'}, {'off'}):
                parts = [voice_text(language, HOME_COMPANION_POWER_TEMPLATES[next(iter(power))],
                                    names=_target_phrase(evidence, matched, language))] if not partial else parts
            else:
                parts.append(voice_text(language, HOME_COMPANION_VERIFIED_TEMPLATES[detail]))
        else:
            next_step = 'check_state'
            if detail == 'concise' and matched and not partial and power in ({'on'}, {'off'}):
                parts = [voice_text(language, HOME_COMPANION_POWER_TEMPLATES[next(iter(power))],
                                    names=_target_phrase(evidence, matched, language))]
            elif matched:
                parts.append(voice_text(language, 'companion_verification_progress', count=len(matched), total=len(all_targets)))
            parts.extend(_verification_exceptions(evidence, all_targets - matched, language))
            if evidence.incomplete or evidence.receipt_only or not all_targets:
                parts.append(voice_text(language, 'companion_unverified'))
        if partial and next_step == 'none':
            next_step = 'check_state'
    elif evidence.attempts:
        status = 'outcome_unknown' if evidence.incomplete or status == 'outcome_unknown' else 'refused'
        execution = 'uncertain' if status == 'outcome_unknown' else 'refused'
        next_step = 'check_state'
        parts.append(voice_text(language, 'companion_unverified' if status == 'outcome_unknown' else 'companion_refused'))
    elif status == 'clarification_needed' or (status == 'completed' and not evidence.successful_reads and not evidence.last_read_failed):
        status, next_step = 'clarification_needed', 'full_request'
        parts.append(voice_text(language, 'companion_clarify'))
    elif status == 'output_limit':
        next_step = 'review_limits'
        parts.append(voice_text(language, 'companion_limit'))
    elif status == 'exhausted':
        next_step = 'full_request'
        parts.append(voice_text(language, 'companion_steps'))
    elif status == 'busy':
        next_step = 'wait'
        parts.append(voice_text(language, 'companion_busy'))
    elif status == 'not_configured':
        next_step = 'configure_provider'
        parts.append(voice_text(language, 'not_configured'))
    elif status == 'refused' or evidence.last_read_failed:
        status, next_step = 'refused', 'check_access'
        parts.append(voice_text(language, 'companion_no_match'))
    elif status != 'completed':
        next_step = 'full_request'
        parts.append(voice_text(language, 'companion_read_failed'))
    else:
        if evidence.selected_count is not None:
            kind = voice_text(language, HOME_COMPANION_COUNT_KIND_TEMPLATES[evidence.selected_kind])
            parts.append(voice_text(language, HOME_COMPANION_COUNT_TEMPLATES[style], count=evidence.selected_count, kind=kind))
            if detail != 'concise':
                parts.append(voice_text(language, 'companion_count_groups'))
            if detail == 'detailed':
                parts.append(voice_text(language, 'companion_count_scope'))
        elif evidence.readings:
            parts.append(voice_text(language, 'companion_readings', readings='; '.join(
                list(evidence.readings.values())[:HOME_COMPANION_MAX_REPORT_NAMES])))
        else:
            # Interpretation of successful history/statistics reads remains model-authored.
            parts.append(model_message)
    if status not in {'authority_lost', 'outcome_unknown'}:
        if detail == 'detailed' and evidence.accepted:
            readings = [evidence.readings[eid] for eid in evidence.targets if eid in evidence.readings]
            if readings:
                parts.append(voice_text(language, 'companion_readings', readings='; '.join(readings[:HOME_COMPANION_MAX_REPORT_NAMES])))
            parts.append(voice_text(language, 'companion_receipt_limit'))
        if labels and ((evidence.accepted and execution != 'accepted') or status == 'clarification_needed'):
            parts.append(voice_text(language, 'companion_targets' if evidence.attempts else 'companion_candidates', names=', '.join(labels)))
        if detail != 'concise' or status != 'completed':
            parts.extend(evidence.warnings)
    return {'status': status, 'message': ' '.join(parts), 'execution': execution,
            'verification': verification, 'next_step': next_step, 'retry_policy': retry}
