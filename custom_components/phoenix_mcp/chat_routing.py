"""Route standalone English Agent Chat commands before the provider loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .command_execution import LocalCommandReply, async_execute_chat_command
from .command_routing import RoutingAuthority, async_evaluate_command


async def async_route_chat_command(hass: Any, data: Any, token: Any,
                                   messages: list[dict], language: object,
                                   cancel: asyncio.Event, *, base_url: str, client_ip: str,
                                   emit: Callable[[str, dict], Awaitable[None]]) -> bool:
    """True continues the provider; False means this turn has already finished."""
    settings = data.store.get_settings()
    execute = settings.agentcli_routing_mode == "local"
    if (not (execute or settings.agentcli_routing_shadow_enabled)
            or not isinstance(language, str) or language.lower().split("-", 1)[0] != "en"
            or len(messages) != 1 or not isinstance(messages[0], dict)
            or set(messages[0]) != {"role", "content"} or messages[0]["role"] != "user"
            or not isinstance(messages[0]["content"], str)):
        return True
    authority = RoutingAuthority.capture(data, token)

    async def finish(reply: LocalCommandReply) -> bool:
        if reply.outcome != "ok":
            await emit("error", {"code": reply.outcome, "message": reply.text, "retryable": False})
        else:
            if reply.pending:
                await emit("approval_required", reply.pending)
            messages.append({"role": "assistant", "content": reply.text})
            await emit("assistant_delta", {"text": reply.text})
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "end_turn" if reply.outcome == "ok" else "error"})
        return False

    async def stop() -> bool:
        return await finish(LocalCommandReply(
            "Cancelled." if cancel.is_set() else "This token is no longer authorized.",
            "cancelled" if cancel.is_set() else "unauthorized"))

    if cancel.is_set() or authority.current(hass) is None:
        return await stop()
    result = await async_evaluate_command(hass, data, token, messages[0]["content"], language)
    current = authority.current(hass)
    if cancel.is_set() or current is None:
        return await stop()
    data.command_routing.observe(result, chat=True)
    if execute:
        if result["proposal"]:
            reply = await async_execute_chat_command(hass, data, current, result["proposal"], authority,
                                                     base_url, client_ip, cancel)
            if reply is not None:
                return await finish(reply)
            if cancel.is_set() or authority.current(hass) is None:
                return await stop()
    elif (settings.decision_provider_id and result["reason"] in ("no_match", "unsupported", "no_target")):
        # Execution mode pauses observation without changing its stored preference.
        data.decision_shadow.start(hass, data, current, messages[0]["content"], language,
                                   observe_chat=True)
    return True
