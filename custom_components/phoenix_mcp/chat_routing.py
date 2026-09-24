"""Route explicit Chat commands while preserving conversational context."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from .command_execution import LocalCommandReply, async_execute_chat_command, async_execute_chat_plan
from .command_routing import (
    RoutingAuthority, async_evaluate_command, effective_routing_mode, routing_language, routing_languages,
)
from .decision_shadow import async_preview_decision
from .decision_context import async_prepare_chat_context, context_execution_eligible, context_history
from .helpers import voice_text


async def async_route_chat_command(hass: Any, data: Any, token: Any,
                                   messages: list[dict], language: object,
                                   cancel: asyncio.Event, *, base_url: str, client_ip: str,
                                   emit: Callable[[str, dict], Awaitable[None]]) -> bool:
    """True continues the provider; False means this turn has already finished."""
    settings = data.store.get_settings()
    mode = effective_routing_mode(settings, "chat")
    execute = mode in ("local", "hybrid")
    if (not (execute or settings.agentcli_routing_shadow_enabled)
            or routing_language(language, enabled=routing_languages(settings, "chat")) is None
            or not messages or not isinstance(messages[-1], dict)
            or set(messages[-1]) != {"role", "content"} or messages[-1]["role"] != "user"
            or not isinstance(messages[-1]["content"], str)):
        return True
    assert isinstance(language, str)
    authority = RoutingAuthority.capture(data, token)
    binding = [token.id, token.token_hash, token.settings_version, language, authority.settings_values]
    followup = len(messages) != 1
    contextual = False
    if followup:
        if not execute:
            return True
        if not data.command_routing.chat_history.consume(messages[:-1], binding):
            if not context_execution_eligible(hass, data, language) or context_history(messages[:-1], structured=True) is None:
                return True
            contextual = True
    snapshot = deepcopy(messages) if followup else None
    context_account = None
    if contextual:
        from .agentcli import decision_account_fingerprint
        context_account = decision_account_fingerprint(hass, settings.decision_provider_id)
    sentence = messages[-1]["content"]

    async def finish(reply: LocalCommandReply, *, local: bool = False) -> bool:
        if reply.outcome != "ok":
            await emit("error", {"code": reply.outcome, "message": reply.text, "retryable": False})
        else:
            if reply.pending:
                await emit("approval_required", reply.pending)
            messages.append({"role": "assistant", "content": reply.text})
            await emit("assistant_delta", {"text": reply.text})
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "end_turn" if reply.outcome == "ok" else "error"})
        if (local and reply.allows_local_followup and reply.outcome == "ok" and not reply.pending
                and not cancel.is_set() and authority.current(hass) is not None):
            data.command_routing.chat_history.remember(messages, binding)
        return False

    async def stop() -> bool:
        return await finish(LocalCommandReply(
            voice_text(language, "local_cancelled" if cancel.is_set() else "local_unauthorized"),
            "cancelled" if cancel.is_set() else "unauthorized"))

    if cancel.is_set() or authority.current(hass) is None:
        return await stop()
    result = await async_evaluate_command(hass, data, token, sentence, language, ingress="chat")
    current = authority.current(hass)
    if cancel.is_set() or current is None or (followup and messages != snapshot):
        return await stop()
    if contextual and (context_account is None or
                       decision_account_fingerprint(hass, settings.decision_provider_id) != context_account):
        return await stop()
    data.command_routing.observe(result, chat=True)
    if contextual:
        decision = await async_prepare_chat_context(hass, data, current, sentence, language,
                                                    messages[:-1], result, cancel)
        current = authority.current(hass)
        data.decision_shadow.observe_hybrid(decision, authority_changed=current is None, chat=True)
        if (current is None or cancel.is_set() or messages != snapshot
                or decision["reason"] in ("authority_changed", "context_changed", "cancelled")):
            return await stop()
        if decision.get("_plan") is not None:
            context_reply = await async_execute_chat_plan(hass, data, current, decision["_plan"], authority,
                                                          base_url, client_ip, cancel, language=language)
            # Mixed history always needs review again; never establish a local-only chain.
            return await finish(context_reply)
        return True
    if execute:
        if result["proposal"]:
            reply = await async_execute_chat_command(hass, data, current, result["proposal"], authority,
                                                     base_url, client_ip, cancel, language=language)
            if reply is not None:
                return await finish(reply, local=True)
            if cancel.is_set() or authority.current(hass) is None:
                return await stop()
        if followup and not result["proposal"] and context_execution_eligible(hass, data, language):
            decision = await async_prepare_chat_context(hass, data, current, sentence, language,
                                                        messages[:-1], result, cancel)
            current = authority.current(hass)
            data.decision_shadow.observe_hybrid(decision, authority_changed=current is None, chat=True)
            if (current is None or cancel.is_set() or messages != snapshot
                    or decision["reason"] in ("authority_changed", "context_changed", "cancelled")):
                return await stop()
            if decision.get("_plan") is not None:
                reply = await async_execute_chat_plan(hass, data, current, decision["_plan"], authority,
                                                      base_url, client_ip, cancel, language=language)
                return await finish(reply)
            return True
        if mode == "hybrid" and not followup and not result["proposal"]:
            decision = await async_preview_decision(hass, data, current, sentence, language,
                                                    result, targets=True, prepare=True, ingress="chat", cancel=cancel)
            current = authority.current(hass)
            data.decision_shadow.observe_hybrid(decision, authority_changed=current is None, chat=True)
            if current is None or cancel.is_set() or decision["reason"] in ("authority_changed", "cancelled"):
                return await stop()
            if decision.get("_plan") is not None:
                reply = await async_execute_chat_plan(hass, data, current, decision["_plan"], authority,
                                                      base_url, client_ip, cancel, language=language)
                return await finish(reply)
    elif (settings.decision_provider_id and result["reason"] in ("no_match", "unsupported", "no_target")):
        # Execution mode pauses observation without changing its stored preference.
        data.decision_shadow.start(hass, data, current, sentence, language,
                                   observe_chat=True, ingress="chat")
    return True
