"""Recheck a frozen plan against current identity, scope, configuration and evidence."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from homeassistant.core import HomeAssistant

from ..entity_commands import finite_number
from ..llm.accounts import loaded_decision_account, decision_account_fingerprint
from .command_operations import numeric_operation, service_arguments, valid_target_value
from .command_plan import _temporal_current, clarification_current
from .command_routing import effective_routing_mode
from .command_summary import summary_current
from .command_target import _digest, _target_fingerprint
from .decision_batch import (
    batch_catalog_digest, batch_execution_eligible, capture_batch_targets,
    percentage_execution_eligible,
)
from .decision_provider import revision_admitted
from .decision_targets import capture_sequential_targets
from .routing_tables import (
    COMMAND_ROUTING_ENTITY_INTENTS, COMMAND_ROUTING_PLAN_KEY, COMMAND_ROUTING_READ_INTENTS,
    COMMAND_ROUTING_VALUE_INTENTS, DECISION_BATCH_EXECUTION_MODELS,
    DECISION_CONTEXT_EXECUTION_LANGUAGES, DECISION_CONTEXT_EXECUTION_MODELS,
    DECISION_EXECUTION_CATEGORIES, DECISION_FOLLOWUP_EXECUTION_MODELS, DECISION_NUMERIC_CATEGORIES,
    DECISION_PERCENTAGE_EXECUTION_MODELS, DECISION_REFERENCE_EXECUTION_MODELS,
    DECISION_REFERENCE_INTENTS, DECISION_REFERENCE_LANGUAGES, DECISION_TARGET_INTENTS,
    DECISION_TEMPORAL_CATEGORIES,
)


# Only a server-owned command turn can attach a plan to initial service dispatch.
_local_plan: ContextVar[dict | None] = ContextVar("phoenix_local_command_plan", default=None)


def _decision_plan_current(plan: dict, hass: HomeAssistant, data: Any, access_key: Any) -> bool:
    if "decision_category" not in plan:
        return "decision_batch" not in plan

    settings = data.store.get_settings()
    category = plan["decision_category"]
    if (plan["proposal"]["intent"] not in COMMAND_ROUTING_READ_INTENTS and "reference_review" not in plan
            and plan.get("decision_target_evidence") != _digest(plan["proposal"])):
        return False
    if category == 'state_summary' and 'decision_batch' not in plan:
        return False
    numeric = category in DECISION_NUMERIC_CATEGORIES
    temporal = category in DECISION_TEMPORAL_CATEGORIES
    typed = plan["proposal"].get("intent") in COMMAND_ROUTING_VALUE_INTENTS
    if (numeric or temporal or plan["proposal"].get("intent") in COMMAND_ROUTING_ENTITY_INTENTS) and "decision_batch" not in plan:
        return False
    catalog_digest = None
    mode = effective_routing_mode(settings, plan.get("ingress", "voice"))
    if "decision_batch" in plan:
        review = plan["decision_batch"]
        if not isinstance(review, dict):
            return False
        account = settings.decision_provider_id
        cfg = loaded_decision_account(hass, account) if account else None
        keys = {"model", "language", "catalog"} | ({"percentage", "proposal"} if numeric else {"value", "proposal"} if temporal or typed else set())
        if category == 'state_summary':
            keys.add('proposal')
            if review.get('proposal') != _digest(plan['proposal']):
                return False
        if "catalog_numeric" in review:
            if numeric or temporal or review["catalog_numeric"] is not True:
                return False
            keys.add("catalog_numeric")
        if "context_review" in plan:
            keys.add("context")
        if "reference_review" in plan:
            keys.add("reference")
        qualified = DECISION_PERCENTAGE_EXECUTION_MODELS if numeric else DECISION_BATCH_EXECUTION_MODELS
        if (not isinstance(review, dict) or set(review) != keys
                or not all(isinstance(review[key], str) for key in keys - {"percentage", "catalog_numeric"})
                or cfg is None or not batch_execution_eligible(cfg, review["language"])
                or not revision_admitted(qualified, cfg, review["model"])):
            return False
        if "context_review" in plan and review["context"] != _digest(plan["context_review"]):
            return False
        if "reference_review" in plan and (numeric or temporal or typed or "context_review" in plan
                or review["reference"] != _digest(plan["reference_review"])):
            return False
        if numeric:
            operation = numeric_operation(plan["proposal"]["intent"])
            if (not percentage_execution_eligible(cfg, review["language"]) or operation is None
                    or not finite_number(review["percentage"])
                    or not operation.valid_value(plan["proposal"].get(operation.proposal_field))
                    or plan["proposal"][operation.proposal_field] != review["percentage"]
                    or _digest(plan["proposal"]) != review["proposal"]):
                return False
        if (temporal or typed) and (review["value"] != plan["proposal"].get("value")
                         or review["proposal"] != _digest(plan["proposal"]) or not _temporal_current(hass, plan["proposal"])):
            return False
        snapshot = capture_batch_targets(hass, access_key, numeric_preview=numeric or review.get("catalog_numeric", False), extended=True, temporal=temporal)
        if batch_catalog_digest(snapshot) != review["catalog"] or category not in snapshot[1]:
            return False
        catalog_digest = snapshot[1][category].digest()
    return bool((category in DECISION_EXECUTION_CATEGORIES or category == "state_summary" or numeric or temporal)
                and mode == "hybrid" and settings.decision_provider_id
                and DECISION_TARGET_INTENTS.get(category) == plan["proposal"]["intent"]
                and plan.get("decision_account") is not None
                and decision_account_fingerprint(hass, settings.decision_provider_id) == plan["decision_account"]
                and (catalog_digest or capture_sequential_targets(hass, access_key, category).digest()) == plan.get("decision_catalog"))


def _context_plan_current(plan: dict, hass: HomeAssistant, data: Any) -> bool:
    if "context_review" not in plan:
        return True
    review = plan["context_review"]
    account = data.store.get_settings().decision_provider_id
    cfg = loaded_decision_account(hass, account) if account else None
    decision = "decision_category" in plan
    keys = {"account", "kind", "model", "language"} | ({"proposal"} if decision else set())
    qualified = DECISION_FOLLOWUP_EXECUTION_MODELS if decision else DECISION_CONTEXT_EXECUTION_MODELS
    return bool(
        plan.get("ingress") == "chat"
        and effective_routing_mode(data.store.get_settings(), "chat") == "hybrid"
        and isinstance(review, dict) and set(review) == keys
        and all(isinstance(value, str) for value in review.values())
        and review["language"] in DECISION_CONTEXT_EXECUTION_LANGUAGES
        and cfg and account and cfg.kind == review["kind"]
        and revision_admitted(qualified, cfg, review["model"])
        and (not decision or (isinstance(plan.get("decision_batch"), dict) and plan["decision_batch"].get("model") == review["model"]
                              and review["proposal"] == _digest(plan["proposal"])))
        and review["account"] == decision_account_fingerprint(hass, account)
    )


def _reference_plan_current(plan: dict, hass: HomeAssistant, data: Any) -> bool:
    if "reference_review" not in plan:
        return True
    review = plan["reference_review"]
    account = data.store.get_settings().decision_provider_id
    cfg = loaded_decision_account(hass, account) if account else None
    proposal = plan["proposal"]
    ids = proposal.get("entity_ids")
    batch = plan.get("decision_batch")
    return bool(
        plan.get("ingress") == "chat" and "context_review" not in plan
        and effective_routing_mode(data.store.get_settings(), "chat") == "hybrid"
        and isinstance(review, dict) and set(review) == {"account", "model", "language", "proposal"}
        and all(isinstance(value, str) for value in review.values())
        and review["language"] in DECISION_REFERENCE_LANGUAGES
        and cfg and account and revision_admitted(DECISION_REFERENCE_EXECUTION_MODELS, cfg, review["model"])
        and isinstance(batch, dict) and batch.get("model") == review["model"]
        and batch.get("language") == review["language"]
        and review["account"] == decision_account_fingerprint(hass, account)
        and set(proposal) == {"intent", "entity_ids"}
        and proposal["intent"] in DECISION_REFERENCE_INTENTS
        and isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str) and ids[0].startswith("light.")
        and review["proposal"] == _digest(proposal)
    )


def plan_is_current(plan: dict, hass: HomeAssistant, data: Any, access_key: Any) -> bool:
    """Recheck identity, scope, availability, capabilities and configuration."""
    try:
        settings = data.store.get_settings()
        proposal = plan["proposal"]
        if 'clarification' in proposal:
            if not clarification_current(hass, data, access_key, proposal):
                return False
        if proposal["intent"] not in COMMAND_ROUTING_READ_INTENTS and service_arguments(proposal) is None:
            return False
        operation = numeric_operation(proposal["intent"])
        if operation and any(not valid_target_value(hass.states.get(eid), proposal["intent"],
                                                   proposal.get(operation.proposal_field)) for eid in proposal["entity_ids"]):
            return False
        if proposal['intent'] == 'PhoenixStateSummary':
            if not summary_current(hass, access_key, proposal):
                return False
        if not _temporal_current(hass, proposal):
            return False
        ingress = plan.get("ingress", "voice")
        if ingress == "chat":
            enabled = effective_routing_mode(settings, "chat") in ("local", "hybrid")
        elif ingress == "voice":
            enabled = (settings.voice_agent_enabled and effective_routing_mode(settings) in ("local", "hybrid")
                       and settings.voice_agent_access_key_id == access_key.id)
        else:
            return False
        return bool(
            enabled and access_key.is_valid() and access_key.id == plan["access_key_id"]
            and access_key.settings_version == plan["access_key_version"]
            and _digest(settings.to_dict()) == plan["settings"]
            and _decision_plan_current(plan, hass, data, access_key)
            and _context_plan_current(plan, hass, data)
            and _reference_plan_current(plan, hass, data)
            and proposal["entity_ids"] and set(proposal["entity_ids"]) == set(plan["fingerprints"])
            and all(plan["fingerprints"][eid] is not None and
                    _target_fingerprint(hass, access_key, eid, proposal["intent"]) == plan["fingerprints"][eid]
                    for eid in proposal["entity_ids"])
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def bind_service_plan(args: dict, hass: HomeAssistant, data: Any, access_key: Any) -> dict | None:
    """Attach constraints from trusted turn context; refuse caller-supplied plans."""
    if COMMAND_ROUTING_PLAN_KEY in args:
        return None
    plan = _local_plan.get()
    if plan is None:
        return args
    bound = {**args, COMMAND_ROUTING_PLAN_KEY: plan}
    return bound if service_plan_is_current(bound, hass, data, access_key) else None


def service_plan_is_current(args: dict, hass: HomeAssistant, data: Any, access_key: Any,
                            entities: list[str] | None = None) -> bool:
    """Saved approval constraints only restrict the ordinary service executor."""
    if COMMAND_ROUTING_PLAN_KEY not in args:
        return True
    plan = args[COMMAND_ROUTING_PLAN_KEY]
    if not isinstance(plan, dict) or not plan_is_current(plan, hass, data, access_key):
        return False
    proposal = plan["proposal"]
    expected = service_arguments(proposal)
    if expected is None:
        return False
    return (all(args.get(key, {} if key == "service_data" else None) == value for key, value in expected.items())
            and not args.get("area_id") and not args.get("device_id")
            and (entities is None or sorted(entities) == sorted(proposal["entity_ids"])))
