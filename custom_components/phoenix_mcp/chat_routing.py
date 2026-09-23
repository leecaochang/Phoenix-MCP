"""Observe standalone English Agent Chat commands without changing execution."""

from __future__ import annotations

import asyncio
from typing import Any

from .command_routing import RoutingAuthority, async_evaluate_command


async def async_observe_chat_command(hass: Any, data: Any, token: Any,
                                    messages: list[dict], language: object,
                                    cancel: asyncio.Event) -> bool:
    """False stops a turn whose authority or caller changed during recognition."""
    if (not data.store.get_settings().agentcli_routing_shadow_enabled
            or not isinstance(language, str) or language.lower().split("-", 1)[0] != "en"
            or len(messages) != 1 or not isinstance(messages[0], dict)
            or set(messages[0]) != {"role", "content"} or messages[0]["role"] != "user"
            or not isinstance(messages[0]["content"], str)):
        return True
    authority = RoutingAuthority.capture(data, token)
    if cancel.is_set() or authority.current(hass) is None:
        return False
    result = await async_evaluate_command(hass, data, token, messages[0]["content"], language)
    current = authority.current(hass)
    if cancel.is_set() or current is None:
        return False
    data.command_routing.observe(result, chat=True)
    if (data.store.get_settings().decision_provider_id
            and result["reason"] in ("no_match", "unsupported", "no_target")):
        # The shared single-flight job never delays or controls the chat provider.
        data.decision_shadow.start(hass, data, current, messages[0]["content"], language,
                                   observe_chat=True)
    return True
