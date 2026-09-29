"""Prepare typed helper value decisions through the existing guarded executor."""
from __future__ import annotations

from typing import Any

from .command_execution import _capture_plan, _digest
from .command_routing import RoutingAuthority
from .const import DECISION_BATCH_EXECUTION_MODELS, COMMAND_ROUTING_TEMPORAL_OPERATIONS
from .decision_provider import DecisionError, confident_choice
from .decision_identity import explicit_write_target
from .temporal_values import valid_temporal_value


def temporal_proposal(answer: dict, targets: dict, source: dict, timezone: str, sentence: str = "", language: str = "en") -> tuple[str, dict | None]:
    """Validate independent operation, target and exact source-value choices."""
    from .const import DECISION_TEMPORAL_CATEGORIES
    route, target, value = answer["route"], answer["target"], answer["value"]
    if route["choice"] == "provider" or target["choice"] == "none" or value["choice"] == "none":
        return "provider_fallback", None
    if not all(confident_choice(check) for check in (route, target, value)):
        return "low_probability", None
    if route["model"] != target["model"] or route["model"] != value["model"]:
        return "model_unverified", None
    category = route["choice"]
    descriptor = targets.get(target["choice"])
    intent = DECISION_TEMPORAL_CATEGORIES.get(category)
    if not intent or not descriptor or category not in descriptor["eligible_categories"]:
        return "category_mismatch", None
    kind = COMMAND_ROUTING_TEMPORAL_OPERATIONS[intent][3]
    if kind == "duration" and source["kind"] != "duration":
        from .temporal_values import explicit_timer_duration
        if not explicit_timer_duration(sentence, source):
            return "numeric_unverified", None
    if (source["kind"] != kind and not (kind == "duration" and source["kind"] == "time")) or not valid_temporal_value(kind, source["value"], timezone):
        return "numeric_unverified", None
    proposal = {"intent": intent, "entity_ids": [descriptor["entity_id"]], "value": source["value"]}
    if not explicit_write_target(sentence, targets, proposal, language):
        return "target_unverified", None
    return "target_selected", proposal


async def async_prepare_temporal(hass: Any, authority: RoutingAuthority, provider: Any, session: Any,
                                 sentence: str, language: str, ingress: str, source: dict) -> tuple[dict, dict]:
    """Use the existing decision slot/deadline, with no retries or model-written values."""
    from .agentcli import loaded_decision_account
    from .decision_batch import batch_catalog_digest, capture_batch_targets
    current = authority.current(hass)
    if current is None:
        raise DecisionError("authority_changed")
    timezone = hass.config.time_zone
    if not valid_temporal_value(source["kind"], source["value"], timezone):
        raise DecisionError("numeric_unverified")
    snapshot = capture_batch_targets(hass, current, extended=True, temporal=True)
    targets, catalogs = snapshot
    answer = await provider.route_temporal(session, sentence, language, targets, source, timezone,
                                           authority.settings.decision_timeout_ms)
    current = authority.current(hass)
    account = authority.settings.decision_provider_id
    if (current is None or not account or loaded_decision_account(hass, account) != provider.cfg
            or hass.config.time_zone != timezone):
        raise DecisionError("authority_changed")
    if capture_batch_targets(hass, current, extended=True, temporal=True) != snapshot:
        raise DecisionError("targets_changed")
    category = answer["route"]
    if category["model"] not in DECISION_BATCH_EXECUTION_MODELS.get(
            (provider.cfg.kind, provider.cfg.base_url, provider.cfg.model), ()):
        raise DecisionError("model_unverified")
    reason, proposal = temporal_proposal(answer, targets, source, timezone, sentence, language)
    selection: dict = {"reason": reason, "decision": answer["target"], "executed": False}
    if proposal is None:
        return category, selection
    plan = _capture_plan(hass, authority.data, current, proposal, ingress=ingress)
    if plan is None:
        raise DecisionError("targets_changed")
    plan["decision_target_evidence"] = _digest(proposal)
    plan.update(decision_category=category["choice"], decision_catalog=catalogs[category["choice"]].digest(),
                decision_batch={"model": category["model"], "language": language,
                                "catalog": batch_catalog_digest(snapshot), "value": source["value"],
                                "proposal": _digest(proposal)})
    return category, {**selection, "proposal": proposal, "entity_id": proposal["entity_ids"][0], "_plan": plan}
