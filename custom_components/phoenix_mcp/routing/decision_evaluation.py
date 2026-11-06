"""Answer Home Assistant's ``ai_task.evaluate`` through the decision provider.

The caller writes the questions and supplies the state, so nothing here reads Home
Assistant or runs a tool, and no answer carries execution authority. The bound access key
and its ``cap_service_response`` decide whether a call may spend the provider at all;
permission grants and MESA have no entity to judge.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..agent_turn import _current_dispatch_access_key
from ..capability import effective_cap
from ..const import CAP_ALLOW
from ..conversation_log import record_identity, recorded_turn
from ..household_authority import household_scope
from ..llm.accounts import _get_secret_store, PROVIDER_DEFINITIONS
from ..redaction import redact_secrets_in_text
from ..request_authority import AuthorityChangedError, check_request_authority
from .decision_history import history_texts
from .decision_provider import (
    DecisionProvider, DecisionValidationError, _evaluate_guarded, _probability, checked_model,
    decision_error_reason, distribution_fits, parse_choice,
)
from .decision_types import DecisionError
from .routing_tables import (
    DECISION_EVALUATE_MAX_BYTES, DECISION_EVALUATE_MAX_CHOICES, DECISION_EVALUATE_MAX_SCORE_LEVELS,
    DECISION_MAX_RESPONSE_BYTES, DECISION_PROBABILITY_SUM_TOLERANCE,
)

_LOGGER = logging.getLogger(__name__)

# An answer's fixed fields, counted once per question and once per criterion.
_ANSWER_OVERHEAD_BYTES = 96

_REFUSALS = {
    "not_configured": "The Phoenix MCP Decisions entity is not fully configured.",
    "authority_changed": "The Phoenix MCP Decisions setup changed; try again.",
    "capability_denied": "The Phoenix MCP Decisions access key needs its service response capability set to Allow.",
    "request_budget_exhausted": "The decision request limit is used up. Try again later.",
    "budget_unavailable": "The decision request limit could not be read.",
    "provider_context_limit": "The evaluation is too large for the decision provider.",
    "http_error": "The decision provider refused the request.",
    "evaluation_too_large": "The evaluation is too large for the decision provider.",
    "evaluation_unsupported": "The evaluation holds a value the decision provider cannot read.",
    "evaluation_sensitive": "The evaluation holds secret-like text, which is not sent to the decision provider.",
}


def decisions_configured(settings: Any) -> bool:
    """Whether the operator turned the entity on with a key and a provider in use."""
    return bool(settings.ai_task_decisions_enabled and settings.ai_task_decisions_access_key_id
                and settings.decision_provider_id)


def _refused(code: str) -> HomeAssistantError:
    """A fixed sentence for one failure code, never request or response text."""
    return HomeAssistantError(_REFUSALS.get(code) or str(decision_error_reason(code)))


def _wire_state(state: Any) -> Any:
    """JEV reads a string, object or array, so a bare number or boolean goes as JSON text."""
    if state is None or (isinstance(state, float) and not math.isfinite(state)):
        raise DecisionError("evaluation_unsupported")
    return json.dumps(state) if isinstance(state, (bool, int, float)) else state


def _estimated_response(questions: Mapping[str, Mapping[str, Any]]) -> int:
    """Bound the reply, since Score repeats its criteria in a legend and they arrive unasked."""
    size = 0
    for name, question in questions.items():
        criteria = question.get("criteria", ())
        size += len(json.dumps(name)) + _ANSWER_OVERHEAD_BYTES
        names = criteria.keys() if isinstance(criteria, Mapping) else criteria
        size += sum(len(json.dumps(item)) + _ANSWER_OVERHEAD_BYTES for item in names)
    return size


def _admit(state: Any, questions: Mapping[str, Mapping[str, Any]], secret: str | None) -> None:
    """Refuse what cannot be sent, before any request allowance is spent."""
    for question in questions.values():
        criteria = question.get("criteria", ())
        limit = DECISION_EVALUATE_MAX_CHOICES if question["type"] == "choice" else DECISION_EVALUATE_MAX_SCORE_LEVELS
        if len(criteria) > limit:
            raise DecisionError("evaluation_too_large")
    content = {"state": state, "questions": questions}
    try:
        size = len(json.dumps(content, ensure_ascii=False, allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError):
        raise DecisionError("evaluation_unsupported") from None
    if size > DECISION_EVALUATE_MAX_BYTES or _estimated_response(questions) > DECISION_MAX_RESPONSE_BYTES:
        raise DecisionError("evaluation_too_large")
    try:
        texts = history_texts(content, check_sensitive=True)
    except (ValueError, RecursionError):
        raise DecisionError("evaluation_sensitive") from None
    if any((secret and secret in text) or (redact_secrets_in_text(text) or "") != text for text in texts):
        raise DecisionError("evaluation_sensitive")


def _parse_noul(answer: Any) -> dict:
    if not isinstance(answer, dict) or answer.get("type") != "noul" or not _probability(answer.get("noul")):
        raise DecisionValidationError("noul_answer")
    return {"type": "noul", "noul": answer["noul"]}


def _parse_score(answer: Any, levels: int, rounding_error: float) -> dict:
    """Return HA's list by level; the score must agree with the distribution it summarizes."""
    if not isinstance(answer, dict) or answer.get("type") != "score" or not _probability(answer.get("confidence")):
        raise DecisionValidationError("score_answer")
    expected = {str(level) for level in range(levels)}
    probabilities, legend = answer.get("probabilities"), answer.get("legend")
    if (not isinstance(probabilities, dict) or set(probabilities) != expected
            or not isinstance(legend, dict) or set(legend) != expected):
        raise DecisionValidationError("score_levels")
    ordered = [probabilities[str(level)] for level in range(levels)]
    if not all(_probability(value) for value in ordered) or not distribution_fits(ordered, rounding_error):
        raise DecisionValidationError("score_distribution")
    score = answer.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= levels - 1:
        raise DecisionValidationError("score_value")
    margin = rounding_error or DECISION_PROBABILITY_SUM_TOLERANCE
    lower = math.fsum(level * max(0, value - margin) for level, value in enumerate(ordered)) - margin
    upper = math.fsum(level * min(1, value + margin) for level, value in enumerate(ordered)) + margin
    if not lower <= score <= upper:
        raise DecisionValidationError("score_value")
    return {"type": "score", "score": score, "probabilities": ordered}


def parse_evaluation(body: Any, questions: Mapping[str, Mapping[str, Any]], rounding_error: float = 0) -> dict:
    """Validate one answer per question and return HA's fields for each."""
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise DecisionValidationError("response_shape")
    answers = body["answers"]
    if set(answers) != set(questions):
        raise DecisionValidationError("answer_shape")
    checked_model(body)
    result: dict[str, dict] = {}
    for name, question in questions.items():
        answer = answers[name]
        if question["type"] == "noul":
            result[name] = _parse_noul(answer)
        elif question["type"] == "score":
            result[name] = _parse_score(answer, len(question["criteria"]), rounding_error)
        else:
            # No confidence threshold: HA receives the distribution and decides.
            parsed = parse_choice({"answers": {name: answer}, "model": body["model"]}, question["criteria"],
                                  name, rounding_error=rounding_error)
            result[name] = {"type": "choice", "choice": parsed["choice"], "probabilities": parsed["probabilities"]}
    return result


def _current_key(data: Any) -> Any:
    """Return the bound key while every gate holds, refusing with a failure code otherwise."""
    settings = data.store.get_settings()
    if not decisions_configured(settings):
        raise DecisionError("authority_changed")
    access_key = _current_dispatch_access_key(data, settings.ai_task_decisions_access_key_id)
    if access_key is None:
        raise DecisionError("authority_changed")
    try:
        check_request_authority()
        if effective_cap(access_key, "cap_service_response") != CAP_ALLOW:
            raise DecisionError("capability_denied")
    except AuthorityChangedError:
        raise DecisionError("authority_changed") from None
    return access_key


async def _evaluate(hass: HomeAssistant, data: Any, questions: dict[str, Any], state: Any) -> dict:
    settings = data.store.get_settings()
    if not decisions_configured(settings):
        raise DecisionError("not_configured")
    access_key = _current_key(data)
    account = settings.decision_provider_id
    store = await _get_secret_store(hass)
    cfg = store.resolve(account, purpose="decision")
    if cfg is None or not cfg.model:
        raise DecisionError("not_configured")
    record_identity(access_key=access_key, config=cfg)

    def guard() -> None:
        current = _current_key(data)
        if (current.id != access_key.id or data.store.get_settings().decision_provider_id != account
                or store.resolve(account, purpose="decision") != cfg):
            raise DecisionError("authority_changed")

    wire_state = _wire_state(state)
    _admit(wire_state, questions, cfg.api_key)
    guard()  # The provider reserves its allowance before its own guard runs.
    provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter, metrics=data.decision_shadow.metrics,
                                metric_scope="ai_task_evaluate", request_budget=data.decision_budget,
                                request_limit=settings.decision_request_budget, request_guard=guard)
    body = await provider.request_evaluation(async_get_clientsession(hass), wire_state, questions,
                                             settings.decision_timeout_ms)
    guard()
    return parse_evaluation(body, questions, provider.rounding_error)


@recorded_turn("ai_task", "task_name")
async def async_evaluate(hass: HomeAssistant, data: Any, task_name: str, questions: dict[str, Any], state: Any,
                         *, user_id: str | None = None) -> dict:
    """Return HA's answer fields by question, or raise HomeAssistantError with a fixed reason."""
    def failure(code: str) -> dict:
        raise _refused(code) from None

    try:
        async with household_scope(hass, user_id=user_id):
            return await _evaluate_guarded(_LOGGER, "decision_evaluation", _evaluate(hass, data, questions, state),
                                           failure)
    except AuthorityChangedError:
        raise _refused("authority_changed") from None
