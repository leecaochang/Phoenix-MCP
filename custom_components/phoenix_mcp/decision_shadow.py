"""Single-flight decision evaluation with no dispatch authority."""
from __future__ import annotations

import logging
from .helpers import log_internal_failure

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_routing import RoutingAuthority, routing_language, routing_languages, effective_routing_mode
from .command_operations import numeric_operation
from .const import MAX_SEARCH_QUERY_LEN, DECISION_TARGET_INTENTS, DECISION_HYBRID_REASONS, DECISION_NUMERIC_CATEGORIES, DECISION_EXECUTION_CATEGORIES, DECISION_ENTITY_CATEGORIES
from .decision_provider import DecisionError, DecisionProvider, confident_choice
from .const import TOOL_ROUTING_SETTINGS, TOOL_ROUTING_REASONS
from .decision_metrics import DecisionMetrics
from copy import deepcopy
from .decision_identity import explicit_write_target
from .numeric_evidence import read_explicit_percentage, compare_percentage

_LOGGER = logging.getLogger(__name__)


@dataclass
class DecisionShadow:
    """Own one bounded network job; retain aggregate counters only."""

    task: asyncio.Task | None = None
    observations: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    choices: dict[str, int] = field(default_factory=dict)
    total_elapsed_ms: float = 0
    hybrid_reasons: dict[str, int] = field(default_factory=dict)
    chat_reasons: dict[str, int] = field(default_factory=dict)
    chat_choices: dict[str, int] = field(default_factory=dict)
    chat_hybrid_reasons: dict[str, int] = field(default_factory=dict)
    metrics: DecisionMetrics = field(default_factory=DecisionMetrics)
    tool_reviews: dict[str, dict] = field(default_factory=dict)

    def summary(self) -> dict:
        return {"observations": self.observations, "reasons": dict(self.reasons),
                "choices": dict(self.choices), "busy": self.task is not None, "requests": self.metrics.summary(),
                "tool_routing": deepcopy(self.tool_reviews),
                "mean_elapsed_ms": round(self.total_elapsed_ms / self.observations, 2)
                if self.observations else None,
                "hybrid": {"evaluations": sum(self.hybrid_reasons.values()), "reasons": dict(self.hybrid_reasons)},
                "chat_hybrid": {"evaluations": sum(self.chat_hybrid_reasons.values()), "reasons": dict(self.chat_hybrid_reasons)},
                "agent_chat": {"observations": sum(self.chat_reasons.values()),
                               "reasons": dict(self.chat_reasons), "choices": dict(self.chat_choices)}}

    def observe_tool_review(self, surface: str, reason: str, elapsed_ms: float) -> None:
        """Aggregate fixed review labels, never candidate names or context."""
        if surface not in TOOL_ROUTING_SETTINGS:
            return
        reason = reason if reason in TOOL_ROUTING_REASONS else "unavailable"
        stats = self.tool_reviews.setdefault(surface, {"reviews": 0, "reasons": {}, "elapsed_ms": 0.0})
        stats["reviews"] += reason not in {"off", "limit", "last_round", "busy", "unsupported_language"}
        stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
        stats["elapsed_ms"] += elapsed_ms

    def observe_hybrid(self, result: dict, *, authority_changed: bool = False, chat: bool = False) -> None:
        """Count each ingress separately without decision payloads or execution claims."""
        reason = result["reason"]
        if authority_changed:
            reason = "authority_changed"
        elif result.get("_plan") is not None:
            reason = "plan_ready"
        elif reason == "observed":
            selection = result.get("target_selection", {})
            reason = selection.get("reason")
            if reason == "low_probability":
                reason = "category_low_probability" if selection.get("decision") is None else "target_low_probability"
        # Only fixed internal codes may become keys; provider text is never retained.
        if not isinstance(reason, str) or reason not in DECISION_HYBRID_REASONS:
            reason = "unavailable"
        reasons = self.chat_hybrid_reasons if chat else self.hybrid_reasons
        reasons[reason] = reasons.get(reason, 0) + 1

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()

    async def async_cancel(self) -> None:
        """Wait for owned provider work and budget persistence to stop."""
        task = self.task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def start(self, hass: Any, data: Any, access_key: Any, sentence: object,
              language: object, *, observe: bool = False, targets: bool = False,
              prepare: bool = False, observe_chat: bool = False, ingress: str = "voice",
              joint_only: bool = False) -> asyncio.Task | None:
        if self.task is not None:
            return None
        authority = RoutingAuthority.capture(data, access_key)
        task = hass.async_create_background_task(
            self._evaluate(hass, data, authority, sentence, language, targets, prepare, ingress, joint_only), "phoenix_decision_shadow")
        self.task = task

        def done(completed: asyncio.Task) -> None:
            if self.task is completed:
                self.task = None
            if completed.cancelled():
                return
            error = completed.exception()
            if error is None and (observe or observe_chat) and authority.current(hass) is not None:
                result = completed.result()
                if observe_chat:
                    reason = result["reason"]
                    self.chat_reasons[reason] = self.chat_reasons.get(reason, 0) + 1
                    if result["decision"] is not None:
                        choice = result["decision"]["choice"]
                        self.chat_choices[choice] = self.chat_choices.get(choice, 0) + 1
                    return
                self.observations += 1
                reason = result["reason"]
                self.reasons[reason] = self.reasons.get(reason, 0) + 1
                self.total_elapsed_ms += result["elapsed_ms"]
                if result["decision"] is not None:
                    choice = result["decision"]["choice"]
                    self.choices[choice] = self.choices.get(choice, 0) + 1
        task.add_done_callback(done)
        return task

    async def _evaluate(self, hass: Any, data: Any, authority: RoutingAuthority,
                        sentence: object, language: object, targets: bool = False, prepare: bool = False, ingress: str = "voice",
                        joint_only: bool = False) -> dict:
        from .agentcli import _get_secret_store, PROVIDER_DEFINITIONS, decision_account_fingerprint
        from .decision_batch import async_prepare_batch, batch_execution_eligible

        started = monotonic()
        def result(reason: str, decision: dict | None = None, *, selection: dict | None = None) -> dict:
            from .conversation_log import record_current
            record_current("routing", {"route": "decision_result", "reason": reason,
                                       "choice": decision.get("choice") if decision else None,
                                       "selection_reason": selection.get("reason") if selection else None,
                                       "observation_only": not prepare, "executed": False})
            return {"reason": reason, "decision": decision, "executed": False,
                    "elapsed_ms": round((monotonic() - started) * 1000, 2)}

        decision_language = routing_language(language, preview=ingress == "preview",
                                             enabled=routing_languages(authority.settings, ingress))
        if decision_language is None:
            return result("unsupported_language")
        if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > MAX_SEARCH_QUERY_LEN:
            return result("invalid_input")
        if authority.current(hass) is None:
            return result("authority_changed")
        try:
            store = await _get_secret_store(hass)
            settings = data.store.get_settings()
            if prepare:
                if ingress == "chat":
                    enabled = effective_routing_mode(settings, "chat") == "hybrid"
                elif ingress == "voice":
                    enabled = (effective_routing_mode(settings) == "hybrid" and settings.voice_agent_enabled
                               and settings.voice_agent_token_id == authority.token_id)
                else:
                    enabled = False
                if not targets or not enabled:
                    return result("not_enabled")
            account = settings.decision_provider_id
            cfg = store.resolve(account, purpose="decision") if account else None
            if cfg is None or not cfg.model:
                return result("not_configured")
            if authority.current(hass) is None:
                return result("authority_changed")
            scope = ingress if prepare or ingress == "preview" else f"{ingress}_shadow"
            provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter,
                                        request_budget=data.decision_budget, request_limit=authority.settings.decision_request_budget,
                                        metrics=self.metrics, metric_scope=scope)
            session = async_get_clientsession(hass)
            def request_guard() -> None:
                if authority.current(hass) is None or store.resolve(account, purpose='decision') != cfg:
                    raise DecisionError('authority_changed')
            provider.request_guard = request_guard
            # Joint and sequential choices share the existing slot and wall-clock budget.
            async with asyncio.timeout(settings.decision_timeout_ms / 1000):
                joint = None
                if prepare and batch_execution_eligible(cfg, decision_language):
                    joint = await async_prepare_batch(hass, authority, provider, session, sentence.strip(),
                                                      decision_language, ingress)
                if joint is not None:
                    answer, selection = joint
                elif joint_only:
                    return result("context_unverified")
                else:
                    options = {"numeric_preview": True} if targets and not prepare and provider.supports_exact_percentages() else {}
                    answer = await provider.classify(session, sentence.strip(), settings.decision_timeout_ms, decision_language, **options)
                    if (authority.current(hass) is None or store.resolve(account, purpose="decision") != cfg):
                        return result("authority_changed")
                    if not targets:
                        return result("observed", answer)
                    if prepare and ((answer["choice"] not in DECISION_EXECUTION_CATEGORIES and answer["choice"] != "provider")
                                    or answer["choice"] in DECISION_ENTITY_CATEGORIES):
                        return result("numeric_unverified" if answer["choice"] in DECISION_NUMERIC_CATEGORIES
                                      else "unsupported_category", answer)
                    selection = await self._select_target(hass, authority, provider, session, sentence.strip(),
                                                          answer, settings.decision_timeout_ms, prepare=prepare, ingress=ingress, language=decision_language)
                if (authority.current(hass) is None or store.resolve(account, purpose="decision") != cfg):
                    return result("authority_changed")
                plan = selection.pop("_plan", None)
                observation = {**result("observed", answer, selection=selection), "target_selection": selection}
                if plan is not None:
                    fingerprint = decision_account_fingerprint(hass, account)
                    if fingerprint is None:
                        return result("authority_changed")
                    plan["decision_account"] = fingerprint
                    observation["_plan"] = plan
                return observation
        except DecisionError as error:
            return result(str(error))
        except TimeoutError:
            return result("timeout")
        except Exception as exc:
            log_internal_failure(_LOGGER, "decision_shadow", exc)
            # Provider errors must not leak prompts, credentials or response bodies.
            return result("unavailable")

    async def _select_target(self, hass: Any, authority: RoutingAuthority, provider: DecisionProvider,
                             session: Any, sentence: str, category: dict, timeout_ms: int,
                             *, prepare: bool = False, ingress: str = "voice", language: str = "en") -> dict:
        from .decision_targets import capture_sequential_targets

        started = monotonic()
        def result(reason: str, entity_id: str | None = None, decision: dict | None = None) -> dict:
            return {"reason": reason, "entity_id": entity_id, "decision": decision,
                    "elapsed_ms": round((monotonic() - started) * 1000, 2)}

        if category["choice"] == "provider":
            return result("provider_fallback")
        if not confident_choice(category):
            return result("low_probability")
        access_key = authority.current(hass)
        if access_key is None:
            return result("authority_changed")
        try:
            snapshot = capture_sequential_targets(hass, access_key, category["choice"])
            if not snapshot.targets:
                return result("no_target")
            operation = numeric_operation(DECISION_TARGET_INTENTS[category["choice"]])
            if prepare and operation is not None:
                return result("numeric_unverified")
            from .agentcli import loaded_decision_account
            def request_guard() -> None:
                latest = authority.current(hass)
                if latest is None or loaded_decision_account(hass, authority.settings.decision_provider_id or '') != provider.cfg:
                    raise DecisionError('authority_changed')
                if capture_sequential_targets(hass, latest, category['choice']) != snapshot:
                    raise DecisionError('targets_changed')
            provider.request_guard = request_guard
            answer = await provider.select_target(session, sentence, category["choice"], snapshot.targets, timeout_ms, language)
            source_value = (await hass.async_add_executor_job(read_explicit_percentage, sentence, language)
                            if operation is not None else None)
            access_key = authority.current(hass)
            if access_key is None:
                return result("authority_changed")
            try:
                current = capture_sequential_targets(hass, access_key, category["choice"])
            except DecisionError:
                return result("targets_changed")
            if current != snapshot:
                return result("targets_changed")
            selected = answer["choice"]
            if selected == "none":
                return result("target_declined", decision=answer)
            if sum(value == answer["probabilities"][selected] for value in answer["probabilities"].values()) != 1:
                return result("ambiguous_target", decision=answer)
            if not confident_choice(answer):
                return result("low_probability", decision=answer)
            entity_id = snapshot.targets[selected]["entity_id"]
            proposal: dict[str, Any] = {"intent": DECISION_TARGET_INTENTS[category["choice"]], "entity_ids": [entity_id]}
            operation = numeric_operation(proposal["intent"])
            if operation is not None:
                percent = answer.get(operation.proposal_field)
                if type(percent) is not int or not 0 <= percent <= 100:
                    return result("brightness_declined" if category["choice"] == "light_brightness" else "percentage_declined", decision=answer)
                proposal[operation.proposal_field] = percent
            if not explicit_write_target(sentence, snapshot.targets, proposal, language):
                return result("target_unverified", decision=answer)
            selection = {**result("target_selected", entity_id, answer), "proposal": proposal}
            if operation is not None:
                selection["value_check"] = compare_percentage(source_value, proposal[operation.proposal_field])
            if prepare:
                from .command_execution import _capture_plan, _digest
                plan = _capture_plan(hass, authority.data, access_key, proposal, ingress=ingress)
                if plan is None:
                    return result("targets_changed")
                plan["decision_target_evidence"] = _digest(proposal)
                plan.update(decision_category=category["choice"], decision_catalog=snapshot.digest())
                selection["_plan"] = plan
            return selection
        except DecisionError as error:
            return result(str(error))


async def async_preview_decision(hass: Any, data: Any, access_key: Any, sentence: str,
                                 language: object, local: dict, *, targets: bool = False,
                                 prepare: bool = False, ingress: str = "voice",
                                 cancel: asyncio.Event | None = None, joint_only: bool = False) -> dict:
    """Evaluate local misses; only internal command callers can request a frozen plan."""
    if local["reason"] not in ("no_match", "unsupported", "no_target"):
        return {"reason": "local_match" if local["proposal"] else "local_unavailable",
                "decision": None, "executed": False, "elapsed_ms": 0}
    task = data.decision_shadow.start(hass, data, access_key, sentence, language, targets=targets, prepare=prepare, ingress=ingress, joint_only=joint_only)
    if task is None:
        return {"reason": "busy", "decision": None, "executed": False, "elapsed_ms": 0}
    cancelled = {"reason": "cancelled", "decision": None, "executed": False, "elapsed_ms": 0}
    waiter = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if waiter is not None:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if cancel is not None and cancel.is_set():
                return cancelled
        return await task
    except asyncio.CancelledError:
        # Settings changes cancel the owned request as well as unload/caller cancellation.
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        return cancelled
    finally:
        # Cancel only this caller's admitted job, never another ingress's busy slot.
        if not task.done():
            task.cancel()
        if waiter is not None:
            waiter.cancel()
        await asyncio.gather(task, *([waiter] if waiter is not None else []), return_exceptions=True)
