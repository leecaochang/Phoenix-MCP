"""Current authority for an admitted MCP request across asynchronous waits."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .data import PhoenixData
    from .token_store import TokenRecord


class AuthorityChangedError(PermissionError):
    """The request's credential, runtime, or policy is no longer current."""


@dataclass(frozen=True)
class RequestAuthority:
    """Capture credential identity without retaining a raw bearer secret."""

    hass: HomeAssistant
    data: PhoenixData
    token_id: str
    token_hash: str

    def current_token(self) -> TokenRecord:
        data = self.data
        if (self.hass.data.get(DOMAIN) is not data or not data.ready
                or data.shutting_down or data.store.get_settings().kill_switch):
            raise AuthorityChangedError("Service unavailable.")
        token = data.store.get_token_by_hash(self.token_hash)
        if token is None or token.id != self.token_id or not token.is_valid():
            raise AuthorityChangedError("Unauthorized.")
        return token


request_authority: ContextVar[RequestAuthority | None] = ContextVar("phoenix_request_authority", default=None)
policy_version: ContextVar[int | None] = ContextVar("phoenix_request_policy_version", default=None)


def check_request_authority() -> None:
    """Refuse further dispatch/disclosure if authority changed during a wait."""
    authority = request_authority.get()
    if authority is None:
        return
    token = authority.current_token()
    version = policy_version.get()
    if version is not None and token.settings_version != version:
        raise AuthorityChangedError("Permissions changed. Retry with current permissions.")


def finite_json_numbers(value: object) -> bool:
    """Validate nested in-process tool arguments before persistence or dispatch."""
    import math

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            return False
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return True
