"""Bounded single-tool delegation for an explicitly scoped home persona."""
from __future__ import annotations

import logging

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er

from .agent_recovery import tool_is_read_only
from .const import (
    PERSONA_HOME_COMPANION, HOME_COMPANION_TOOLS, HOME_COMPANION_CONTROL_TOOLS,
    HOME_COMPANION_MAX_CALLS, HOME_COMPANION_MAX_REQUEST_BYTES, HOME_COMPANION_MAX_CONTEXT_BYTES,
    HOME_COMPANION_GROUP_WORDS, HOME_COMPANION_GROUP_PHRASES, HOME_COMPANION_CONTEXT_PATTERNS,
    HOME_COMPANION_GENERIC_TARGET_WORDS, HOME_COMPANION_TYPO_MIN_LENGTH,
    HOME_COMPANION_CONTROL_DOMAINS, HOME_COMPANION_TOOL_DOMAINS,
)
from .conversation_log import record_current
from .device_semantics import device_registry_snapshot, effective_entity_area_id
from .helpers import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text
from .home_companion_response import ResponseEvidence
from .policy_engine import resolve_intent_entities
from .request_authority import check_request_authority
from .tools.native import validate_native_control_args

_LOGGER = logging.getLogger(__name__)


@dataclass
class CompanionTurn:
    """Private turn-local execution admission; never populated from MCP arguments."""

    access_key_id: str
    language: str = "en"
    request: str = ""
    style: str = "direct"
    detail: str = "concise"
    evidence: ResponseEvidence = field(default_factory=ResponseEvidence)
    calls: int = 0
    write_started: bool = False
    writes: set[str] = field(default_factory=set)
    outcomes: list[dict] = field(default_factory=list)


_presentation: ContextVar[tuple[str, str] | None] = ContextVar("phoenix_companion_presentation", default=None)


_turn: ContextVar[CompanionTurn | None] = ContextVar("phoenix_companion_turn", default=None)


# The provider account and model Agent Chat's user chose. Only an internal caller
# sets it, never home_request's arguments, so an external client cannot pick which
# account is spent; without it the Voice settings decide.
_account: ContextVar[tuple[str, str | None] | None] = ContextVar("phoenix_companion_account", default=None)


def is_companion(access_key: Any) -> bool:
    return getattr(access_key, "persona", None) == PERSONA_HOME_COMPANION


def internal_turn(access_key: Any) -> bool:
    turn = _turn.get()
    return bool(turn is not None and turn.access_key_id == access_key.id)


def wrapper_tool() -> dict:
    """Return a fresh schema; the planner's household catalog is separate."""
    return {
        "name": "home_request",
        "description": (
            "Ask Phoenix to read permitted home information or control permitted household devices. "
            "Phoenix uses its configured conversation provider and a bounded internal tool loop. "
            "No previous requests or external chat history are included. Each call needs the complete task, targets and restrictions. "
            "Preserve the user's target wording and actual restrictions when delegating. Phoenix resolves natural device "
            "names from accessible search results. Do not add exact-name, literal-match or no-fallback requirements "
            "unless the user explicitly requested them. Identifying the intended device does not require identical wording. "
            "When clarification is needed, your final user-facing reply must explicitly ask the user to try again "
            "with a complete request containing the action, target and restrictions, not just a device name or "
            "missing detail. Include a complete example using a discovered candidate, clearly as an example rather "
            "than a chosen action. Preserve this guidance when summarizing Phoenix's reply; do not shorten it to "
            "a question such as 'Which light?'. A bare follow-up answer is not enough for this stateless tool. "
            "Inspect status, message, tool_outcomes and review_urls. Pending approval is not execution. "
            "Explain the result naturally and briefly: lead with what happened, then any needed next step. "
            "Do not recite tool traces, internal status codes or policy unless the user asks. "
            "A missing or malformed final reply does not mean a device command failed. If control was attempted "
            "but confirmation is incomplete, acknowledge possible effects and ask the user to check the current "
            "state in Home Assistant. Do not offer to resend the action or claim repeating on/off commands is safe "
            "because they are idempotent. Never retry an outcome_unknown action automatically; follow any recovery guidance."
        ),
        "inputSchema": {"type": "object", "properties": {
            "request": {"type": "string", "minLength": 1, "maxLength": HOME_COMPANION_MAX_REQUEST_BYTES,
                        "description": "The complete task, preserving the user's target wording and explicit restrictions without inventing exact-name requirements."},
            "language": {"type": "string", "maxLength": 32, "default": "en"},
        }, "required": ["request"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True,
                        "idempotentHint": False, "openWorldHint": True},
    }


def authorize_call(access_key: Any, name: str, arguments: Any) -> str | None:
    """Refuse guessed tools and recursive wrappers before dispatch or approvals."""
    if access_key.pass_through:
        return "Home Companion requires scoped permissions; disable pass-through."
    turn = _turn.get()
    if name == "home_request":
        return "Recursive delegation is not available." if turn is not None else None
    if not internal_turn(access_key) or name not in HOME_COMPANION_TOOLS:
        return "This persona exposes only home_request; the requested tool is unavailable."
    if turn is None or turn.calls >= HOME_COMPANION_MAX_CALLS:
        return "The request reached its internal tool limit; not executed."
    if name in HOME_COMPANION_CONTROL_TOOLS:
        signature = json.dumps([name, arguments], sort_keys=True)
        if signature in turn.writes or turn.evidence.refused or turn.evidence.incomplete or any(
                not tool_is_read_only(row["tool"]) and row["outcome"] != "allowed"
                for row in turn.outcomes):
            return "Do not repeat or replace a completed, failed or refused action within this request."
        turn.writes.add(signature)
        turn.write_started = True
    turn.calls += 1
    return None


def record_outcome(access_key: Any, name: str, outcome: str, result: dict | None = None,
                   arguments: dict | None = None, hass: HomeAssistant | None = None) -> None:
    turn = _turn.get()
    if internal_turn(access_key) and turn is not None:
        turn.outcomes.append({"tool": name, "outcome": outcome})
        turn.evidence.observe(name, outcome, result, arguments)
        if hass is not None:
            registry, areas, devices = er.async_get(hass), ar.async_get(hass), device_registry_snapshot(hass)
            turn.evidence.areas.clear()
            for eid in turn.evidence.targets:
                area_id = effective_entity_area_id(registry.async_get(eid), devices)
                if area_id and (area := areas.async_get_area(area_id)):
                    turn.evidence.areas[eid] = area.name
            def scrub(text: str) -> str:
                return redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(text) or "", access_key, hass)
            for labels in (turn.evidence.targets, turn.evidence.candidates, turn.evidence.readings, turn.evidence.areas):
                for eid, label in labels.items():
                    labels[eid] = scrub(label)
            turn.evidence.warnings = [scrub(warning) for warning in turn.evidence.warnings]


def _context_bytes(system: str, messages: list[dict]) -> int:
    return len(json.dumps({"system": system, "messages": messages}, ensure_ascii=False, default=str).encode())


def check_turn_context(access_key: Any, system: str, messages: list[dict]) -> bool:
    if not internal_turn(access_key):
        return True
    check_request_authority()
    return _context_bytes(system, messages) <= HOME_COMPANION_MAX_CONTEXT_BYTES


def append_fitting_results(access_key: Any, system: str, messages: list[dict], results: list[dict],
                           append: Any) -> None:
    """Append a round's results, withholding the largest reads the request has no room for.

    One full-size result can fill this persona's whole context and end the
    request. A withheld read comes back as an error naming its size, so the
    model can ask again for less. A control result is never withheld: it is the
    evidence of what ran. If the rest still does not fit, the next round's context
    check ends the request as before.
    """
    start = len(messages)
    append(messages, results)
    turn = _turn.get()
    if not internal_turn(access_key) or turn is None:
        return
    withheld: set[int] = set()
    while (size := _context_bytes(system, messages)) > HOME_COMPANION_MAX_CONTEXT_BYTES:
        reads = [(len(str(row.get("result_text") or "").encode()), index) for index, row in enumerate(results)
                 if index not in withheld and not row.get("is_error") and tool_is_read_only(str(row.get("tool_name") or ""))]
        if not reads:
            return
        length, index = max(reads)
        withheld.add(index)
        room = max(0, HOME_COMPANION_MAX_CONTEXT_BYTES - (size - length))
        row = results[index]
        results[index] = {**row, "images": [], "is_error": True, "result_text": (
            f"This result was too large for this request ({length} bytes; about {room} bytes remain) and was not shown. "
            "Ask again for less: fewer entities, a narrower selection, a shorter period or a smaller limit.")}
        turn.evidence.model_answer = True
        record_current("error", {"code": "result_withheld", "name": row.get("tool_name"), "call_id": row.get("tool_use_id"),
                                 "executed": True, "message_params": {"length": length},
                                 "message": f"A {length}-byte result did not fit the request's context and was withheld from the model."})
        del messages[start:]
        append(messages, results)


def preflight_control_calls(access_key: Any, calls: list[dict]) -> list[dict] | None:
    """Withhold an invalid native batch before a write claim, without repairing it."""
    if not internal_turn(access_key):
        return None
    check_request_authority()
    turn = _turn.get()
    if turn is not None and any(call.get('name') in HOME_COMPANION_CONTROL_TOOLS for call in calls):
        turn.evidence.control_proposed = True
    errors = {}
    for call in calls:
        if refusal := validate_native_control_args(call.get('name', ''), call.get('input') or {}):
            errors[call.get('id')] = '\n'.join(block.get('text', '') for block in refusal[0]['content'])
    if not errors:
        return None
    return [{'tool_use_id': call.get('id'), 'tool_name': call.get('name'), 'is_error': True,
             'result_text': errors.get(call.get('id'), 'This batch was not run because another call has invalid arguments.')
             + ' None of the tools in this batch ran. Correct the arguments without changing the original task or restrictions.'}
            for call in calls]


def incomplete_proposal(access_key: Any) -> None:
    """Preserve completed receipts while reporting a later withheld control batch."""
    turn = _turn.get()
    if internal_turn(access_key) and turn is not None:
        turn.evidence.incomplete = True


def ambiguous_control(access_key: Any, hass: HomeAssistant, name: str, arguments: Any = None) -> bool:
    """Veto ungrounded context references and tied names before claiming a write."""
    turn = _turn.get()
    if not internal_turn(access_key) or turn is None or name not in HOME_COMPANION_CONTROL_TOOLS:
        return False
    check_request_authority()
    request = turn.request.casefold()
    words = set(re.findall(r"[^\W_]+", request))
    domains = list(HOME_COMPANION_TOOL_DOMAINS.get(name, sorted(HOME_COMPANION_CONTROL_DOMAINS)))
    states = [hass.states.get(eid) for eid in resolve_intent_entities(hass, access_key, domains=domains)]
    # Singular/plural suffix normalization compares evidence; it never selects a target.
    request_words = {word.removesuffix('s') for word in words}
    scores = []
    candidates = []
    grounded = False
    registry, areas, devices = er.async_get(hass), ar.async_get(hass), device_registry_snapshot(hass)
    for state in states:
        if state is None:
            continue
        label = state.attributes.get('friendly_name', state.entity_id)
        entry = registry.async_get(state.entity_id)
        aliases = [] if entry is None else er.async_get_entity_aliases(hass, entry)
        labels = [str(label), state.entity_id, *aliases]
        area_id = effective_entity_area_id(entry, devices)
        if area_id and (area := areas.async_get_area(area_id)):
            labels.extend([area.name, *area.aliases])
        terms = {term for text in labels for term in re.findall(r"[^\W_]+", text.casefold())}
        grounded |= any(text.casefold() in request for text in labels if text)
        grounded |= bool(request_words & {term.removesuffix('s') for term in terms - HOME_COMPANION_GENERIC_TARGET_WORDS})
        normalized = {word.removesuffix('s') for word in terms}
        scores.append(len(request_words & normalized))
        candidates.append((state.entity_id, normalized))
    pattern = HOME_COMPANION_CONTEXT_PATTERNS.get(turn.language.split('-')[0])
    if pattern and re.search(pattern, request) and not grounded:
        return True
    if words & HOME_COMPANION_GROUP_WORDS or any(p in request for p in HOME_COMPANION_GROUP_PHRASES):
        return False
    best = max(scores, default=0)
    if best < 2 or scores.count(best) <= 1:
        return False
    # A unique single-character typo may corroborate the model's explicit ID.
    # It cannot select a target, override exact-name restrictions, or break ties.
    proposed = arguments.get("name") if isinstance(arguments, dict) else None
    if turn.language.split("-")[0] == "en" and proposed and not re.search(r"\b(exact|exactly|literal|literally)\b", request):
        vocabulary = set().union(*(terms for _, terms in candidates))
        misspelled = {word for word in request_words if len(word) >= HOME_COMPANION_TYPO_MIN_LENGTH and word not in vocabulary}
        corroborated = []
        for (entity_id, terms), score in zip(candidates, scores):
            if score == best and any(_one_edit(word, term) for word in misspelled for term in terms
                                     if len(term) >= HOME_COMPANION_TYPO_MIN_LENGTH and term not in HOME_COMPANION_GENERIC_TARGET_WORDS):
                corroborated.append(entity_id)
        if corroborated == [proposed]:
            return False
    return True


def _one_edit(left: str, right: str) -> bool:
    """Recognize one insertion, deletion, or substitution, never general similarity."""
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    short, long = sorted((left, right), key=len)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def count_source(access_key: Any, name: str) -> dict | None:
    """Expose the server-recorded source index inside the private planner transcript."""
    turn = _turn.get()
    if not internal_turn(access_key) or turn is None or name != 'search_entities':
        return None
    source = turn.evidence.calls - 1
    return {'kind': 'count', 'source': source} if source in turn.evidence.search_totals else None


def _parse_final_answer(text: str, evidence: ResponseEvidence) -> dict | None:
    """Admit one complete response object and only received count references."""
    try:
        body = text.strip()
        start = body.find("{")
        if start < 0:
            return None
        answer, end = json.JSONDecoder().raw_decode(body, start)
        suffix = body[end:].strip()
        if suffix and not (suffix == "```" and body[:start].rstrip().endswith(("```json", "```"))):
            return None
        if (not isinstance(answer, dict) or set(answer) - {"read_answer"} != {"status", "message"}
                or answer["status"] not in {"completed", "clarification_needed", "refused"}
                or not isinstance(answer["message"], str) or not answer["message"].strip()
                or ("read_answer" in answer and not evidence.valid_read_answer(answer["read_answer"]))):
            return None
        return answer
    except (ValueError, TypeError):
        return None


def _read_only_answer(text: str) -> dict | None:
    """The answer in a read-only reply that missed the required object: its prose, or a malformed object's message."""
    body = text.strip()
    start = body.find("{")
    if start >= 0:
        try:
            value, _ = json.JSONDecoder().raw_decode(body, start)
        except ValueError:
            value = None
        if isinstance(value, dict):
            message, status = value.get("message"), value.get("status")
            # A count reference that failed validation means the model supplied its own count.
            if not isinstance(message, str) or not message.strip() or "read_answer" in value:
                return None
            return {"status": status if status in {"completed", "clarification_needed", "refused"} else "completed",
                    "message": message.strip()}
    return {"status": "completed", "message": body} if body else None


def invalid_read_report(access_key: Any, text: str) -> bool:
    """Let the bounded loop correct the final formatting of a read-only answer.

    Models sometimes answer a history question in plain prose; one tools-free
    round asks for the required object instead of discarding a good answer.
    """
    turn = _turn.get()
    return bool(internal_turn(access_key) and turn and not turn.evidence.attempts
                and _parse_final_answer(text, turn.evidence) is None)
