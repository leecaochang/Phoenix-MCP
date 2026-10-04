"""Bounded tool-selection advice shared by Phoenix-owned agent loops."""
from __future__ import annotations

import logging
from .helpers import log_internal_failure

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
from time import monotonic
from typing import Any, Callable, Literal

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_routing import RoutingAuthority, routing_language
from .const import (
    COMMAND_ROUTING_LANGUAGES, DECISION_CONTEXT_MAX_BYTES, TOOL_ROUTING_MAX_BYTES,
    TOOL_ROUTING_MAX_REVIEWS, TOOL_ROUTING_MAX_REPLANS, TOOL_ROUTING_QUALIFIED_MODELS,
    TOOL_ROUTING_REASONS, TOOL_ROUTING_SETTINGS,
)
from .decision_history import pending_context_history, history_texts
from .decision_provider import DecisionError, DecisionProvider
from .helpers import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolReview:
    """Advice only, never action arguments or execution authority."""

    action: Literal["continue", "replan", "stop"] = "continue"
    reason: str = "off"
    tool: str | None = None


@dataclass
class ToolReviewState:
    """Turn-local bounds and admission snapshot, never reused by another turn."""

    authority: RoutingAuthority | None = None
    reviews: int = 0
    replans: int = 0


def create_review_state(data: Any, access_key: Any, surface: str) -> ToolReviewState:
    """Capture routing authority for observe or advise mode on a non-companion access key.

    Companion access keys and the off mode get an empty state, which leaves the loop unchanged.
    """
    from .home_companion import is_companion
    if is_companion(access_key):
        return ToolReviewState()
    mode = getattr(data.store.get_settings(), TOOL_ROUTING_SETTINGS[surface], "off")
    return ToolReviewState(RoutingAuthority.capture(data, access_key) if mode in ("observe", "advise") else None)


def qualified_languages(cfg: Any) -> list[str]:
    """Expose evaluated languages without exposing account credentials."""
    if cfg is None:
        return []
    return sorted({language for (kind, endpoint, model, language), returned in TOOL_ROUTING_QUALIFIED_MODELS.items()
                   if (kind, endpoint, model) == (cfg.kind, cfg.base_url, cfg.model) and returned})


def withheld_results(calls: list[dict], review: ToolReview) -> list[dict]:
    """Answer every withheld call ID without pretending a tool actually ran."""
    text = (f"Not executed. Reconsider the next step using {review.tool}; generate fresh arguments. "
            "This is routing advice, not authorization. You may keep your original plan if appropriate.") \
        if review.action == "replan" else "Not executed. Routing was cancelled or authority changed."
    return [{"tool_use_id": call["id"], "tool_name": call["name"], "result_text": text, "is_error": True}
            for call in calls]


def _context(hass: Any, access_key: Any, messages: list[dict], calls: list[dict], system: str,
             language: str, catalog: list[dict], secret: str | None) -> dict:
    history = pending_context_history(messages, calls)
    if history is None:
        raise DecisionError("context_unavailable")
    state: dict[str, Any] = {"history": history, "pending": deepcopy(calls), "constraints": system, "language": language}
    try:
        if len(json.dumps(state, ensure_ascii=False, allow_nan=False).encode()) > DECISION_CONTEXT_MAX_BYTES:
            raise DecisionError("context_unavailable")
        for text in history_texts(state, check_sensitive=True):
            if ((secret and secret in text) or
                    redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(text) or "", access_key, hass) != text):
                raise DecisionError("context_unavailable")
        if (not catalog or len({tool["name"] for tool in catalog}) != len(catalog)
                or any(not isinstance(tool.get("name"), str) or not tool["name"] for tool in catalog)
                or calls[0]["name"] not in {tool["name"] for tool in catalog}):
            raise DecisionError("catalog_unavailable")
        state["catalog"] = {f"t{i}": deepcopy(tool) for i, tool in enumerate(catalog)}
        if len(json.dumps(state, ensure_ascii=False, allow_nan=False).encode()) > TOOL_ROUTING_MAX_BYTES:
            raise DecisionError("catalog_unavailable")
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        if isinstance(error, DecisionError):
            raise
        raise DecisionError("context_unavailable") from None
    return state


async def async_review_tools(hass: Any, data: Any, turn: ToolReviewState, *, surface: str,
                             language: object, messages: list[dict], calls: list[dict], system: str,
                             remaining_rounds: int, cancel: asyncio.Event | None = None,
                             announced_tools: list[dict] | None = None) -> ToolReview:
    """Review before dispatch, sharing decision capacity and durable request accounting."""
    from .agentcli import _get_secret_store, build_agent_tool_list, PROVIDER_DEFINITIONS

    started = monotonic()
    authority = turn.authority
    if authority is None:
        return ToolReview()
    mode = getattr(authority.settings, TOOL_ROUTING_SETTINGS[surface])
    runtime = data.decision_shadow

    def result(reason: str, tool: str | None = None) -> ToolReview:
        reason = reason if reason in TOOL_ROUTING_REASONS else "unavailable"
        action: Literal["continue", "replan", "stop"] = "continue"
        if reason in {"authority_changed", "catalog_changed", "context_changed", "cancelled"}:
            action = "stop"
        elif reason == "alternative" and mode == "advise":
            reason, action = "replan", "replan"
            turn.replans += 1
        if mode != "off":
            runtime.observe_tool_review(surface, reason, (monotonic() - started) * 1000)
        return ToolReview(action, reason, tool)

    if mode == "off":
        return ToolReview()
    if cancel is not None and cancel.is_set():
        return result("cancelled")
    if authority.current(hass) is None:
        return result("authority_changed")
    if turn.reviews >= TOOL_ROUTING_MAX_REVIEWS or turn.replans >= TOOL_ROUTING_MAX_REPLANS:
        return result("limit")
    if remaining_rounds <= 0:
        return result("last_round")
    lang = routing_language(language, enabled=tuple(COMMAND_ROUTING_LANGUAGES))
    if lang is None:
        return result("unsupported_language")
    if runtime.task is not None:
        return result("busy")
    turn.reviews += 1

    async def evaluate() -> ToolReview:
        validate_current: Callable[[], None] | None = None

        def failure(reason: str) -> ToolReview:
            if validate_current is not None:
                try:
                    validate_current()
                except DecisionError as error:
                    reason = str(error)
            elif authority.current(hass) is None:
                reason = "authority_changed"
            return result(reason)

        try:
            async with asyncio.timeout(authority.settings.decision_timeout_ms / 1000):
                store = await _get_secret_store(hass)
                access_key = authority.current(hass)
                if access_key is None:
                    return result("authority_changed")
                account = authority.settings.decision_provider_id
                cfg = store.resolve(account, purpose="decision") if account else None
                if cfg is None:
                    return result("no_account")
                admitted = TOOL_ROUTING_QUALIFIED_MODELS.get((cfg.kind, cfg.base_url, cfg.model, lang), ())
                if mode == "advise" and not admitted:
                    return result("unqualified")
                catalog = build_agent_tool_list(access_key, data, surface)
                if announced_tools is not None and catalog != announced_tools:
                    return result("catalog_changed")
                snapshot = _context(hass, access_key, messages, calls, system, lang, catalog, cfg.api_key)

                def guard() -> None:
                    current = authority.current(hass)
                    if (current is None or store.resolve(account or "", purpose="decision") != cfg
                            or (cancel is not None and cancel.is_set())):
                        raise DecisionError("authority_changed")
                    if snapshot["catalog"] != {f"t{i}": tool for i, tool in enumerate(build_agent_tool_list(current, data, surface))}:
                        raise DecisionError("catalog_changed")
                    try:
                        fresh = _context(hass, current, messages, calls, system, lang,
                                         build_agent_tool_list(current, data, surface), cfg.api_key)
                    except DecisionError:
                        raise DecisionError("context_changed") from None
                    if fresh != snapshot:
                        raise DecisionError("context_changed")

                validate_current = guard
                provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter,
                    metrics=runtime.metrics, metric_scope=f"tool_{surface}",
                    request_budget=data.decision_budget, request_limit=authority.settings.decision_request_budget,
                    request_guard=guard)
                guard()
                try:
                    answer = await provider.select_tool(async_get_clientsession(hass), snapshot,
                                                        authority.settings.decision_timeout_ms)
                except (DecisionError, TimeoutError):
                    guard()
                    raise
                guard()
                if mode == "advise" and answer["model"] not in admitted:
                    return result("unqualified")
                selected = answer["tool"]
                if selected is None:
                    return result("declined")
                if selected not in {tool["name"] for tool in snapshot["catalog"].values()}:
                    return result("malformed")
                return result("agreement" if selected == calls[0]["name"] else "alternative", selected)
        except DecisionError as error:
            return failure(str(error))
        except TimeoutError:
            return failure("timeout")
        except Exception as exc:
            log_internal_failure(_LOGGER, "tool_routing", exc)
            return failure("unavailable")

    task = hass.async_create_background_task(evaluate(), "phoenix_tool_review")
    runtime.task = task
    waiter = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if waiter is not None:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if cancel is not None and cancel.is_set():
                return result("cancelled")
        return await task
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise
        return result("cancelled")
    finally:
        if not task.done():
            task.cancel()
        if waiter is not None:
            waiter.cancel()
        await asyncio.gather(task, *([waiter] if waiter is not None else []), return_exceptions=True)
        if runtime.task is task:
            runtime.task = None
