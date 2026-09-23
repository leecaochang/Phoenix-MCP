"""Single-flight, English-only decision observation with no dispatch authority."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_routing import RoutingAuthority
from .const import MAX_SEARCH_QUERY_LEN
from .decision_provider import DecisionError, DecisionProvider


@dataclass
class DecisionShadow:
    """Own one bounded network job; retain aggregate counters only."""

    task: asyncio.Task | None = None
    observations: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    choices: dict[str, int] = field(default_factory=dict)
    total_elapsed_ms: float = 0

    def summary(self) -> dict:
        return {"observations": self.observations, "reasons": dict(self.reasons),
                "choices": dict(self.choices), "busy": self.task is not None,
                "mean_elapsed_ms": round(self.total_elapsed_ms / self.observations, 2)
                if self.observations else None}

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()

    def start(self, hass: Any, data: Any, token: Any, sentence: object,
              language: object, *, observe: bool = False, targets: bool = False) -> asyncio.Task | None:
        if self.task is not None:
            return None
        authority = RoutingAuthority.capture(data, token)
        task = hass.async_create_background_task(
            self._evaluate(hass, data, authority, sentence, language, targets), "phoenix_decision_shadow")
        self.task = task

        def done(completed: asyncio.Task) -> None:
            if self.task is completed:
                self.task = None
            if completed.cancelled():
                return
            error = completed.exception()
            if error is None and observe and authority.current(hass) is not None:
                result = completed.result()
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
                        sentence: object, language: object, targets: bool = False) -> dict:
        from .agentcli import _get_secret_store, PROVIDER_DEFINITIONS

        started = monotonic()
        def result(reason: str, decision: dict | None = None) -> dict:
            return {"reason": reason, "decision": decision, "executed": False,
                    "elapsed_ms": round((monotonic() - started) * 1000, 2)}

        if not isinstance(language, str) or language.lower().split("-", 1)[0] != "en":
            return result("unsupported_language")
        if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > MAX_SEARCH_QUERY_LEN:
            return result("invalid_input")
        if authority.current(hass) is None:
            return result("authority_changed")
        try:
            store = await _get_secret_store(hass)
            settings = data.store.get_settings()
            account = settings.decision_provider_id
            cfg = store.resolve(account, purpose="decision") if account else None
            if cfg is None or not cfg.model:
                return result("not_configured")
            if authority.current(hass) is None:
                return result("authority_changed")
            provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter)
            session = async_get_clientsession(hass)
            # Category and optional target selection share one wall-clock budget.
            async with asyncio.timeout(settings.decision_timeout_ms / 1000):
                answer = await provider.classify(session, sentence.strip(), settings.decision_timeout_ms)
                if (authority.current(hass) is None or store.resolve(account, purpose="decision") != cfg):
                    return result("authority_changed")
                if not targets:
                    return result("observed", answer)
                selection = await self._select_target(hass, authority, provider, session, sentence.strip(),
                                                      answer, settings.decision_timeout_ms)
                if (authority.current(hass) is None or store.resolve(account, purpose="decision") != cfg):
                    return result("authority_changed")
                return {**result("observed", answer), "target_selection": selection}
        except DecisionError as error:
            return result(str(error))
        except TimeoutError:
            return result("timeout")
        except Exception:
            # Provider errors must not leak prompts, credentials or response bodies.
            return result("unavailable")

    async def _select_target(self, hass: Any, authority: RoutingAuthority, provider: DecisionProvider,
                             session: Any, sentence: str, category: dict, timeout_ms: int) -> dict:
        from .decision_targets import capture_targets

        started = monotonic()
        def result(reason: str, entity_id: str | None = None, decision: dict | None = None) -> dict:
            return {"reason": reason, "entity_id": entity_id, "decision": decision,
                    "elapsed_ms": round((monotonic() - started) * 1000, 2)}

        if category["choice"] == "provider":
            return result("provider_fallback")
        token = authority.current(hass)
        if token is None:
            return result("authority_changed")
        try:
            snapshot = capture_targets(hass, token, category["choice"])
            if not snapshot.targets:
                return result("no_target")
            answer = await provider.select_target(session, sentence, category["choice"], snapshot.targets, timeout_ms)
            token = authority.current(hass)
            if token is None:
                return result("authority_changed")
            try:
                current = capture_targets(hass, token, category["choice"])
            except DecisionError:
                return result("targets_changed")
            if current != snapshot:
                return result("targets_changed")
            selected = answer["choice"]
            if selected == "none":
                return result("target_declined", decision=answer)
            if sum(value == answer["probabilities"][selected] for value in answer["probabilities"].values()) != 1:
                return result("ambiguous_target", decision=answer)
            return result("target_selected", snapshot.targets[selected]["entity_id"], answer)
        except DecisionError as error:
            return result(str(error))


async def async_preview_decision(hass: Any, data: Any, token: Any, sentence: str,
                                 language: str, local: dict, *, targets: bool = False) -> dict:
    """Manual previews share local-first gating and cannot dispatch an action."""
    if local["reason"] not in ("no_match", "unsupported", "no_target"):
        return {"reason": "local_match" if local["proposal"] else "local_unavailable",
                "decision": None, "executed": False, "elapsed_ms": 0}
    task = data.decision_shadow.start(hass, data, token, sentence, language, targets=targets)
    if task is None:
        return {"reason": "busy", "decision": None, "executed": False, "elapsed_ms": 0}
    try:
        return await task
    except asyncio.CancelledError:
        # Settings changes cancel the owned request as well as unload/caller cancellation.
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        return {"reason": "cancelled", "decision": None, "executed": False, "elapsed_ms": 0}
