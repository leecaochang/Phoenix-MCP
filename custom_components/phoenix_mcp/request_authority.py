"""Current authority for an admitted MCP request across asynchronous waits."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .data import PhoenixData
    from .audit import Outcome
    from .token_store import TokenRecord


class AuthorityChangedError(PermissionError):
    """The request's credential, runtime, or policy is no longer current."""

    receipt: tuple[Outcome, str] | None = None
    not_dispatched: bool = False


class PolicyChangedError(AuthorityChangedError):
    """An admitted operation needs current permissions, not a new credential."""


@dataclass
class DispatchProgress:
    """Evidence only for handlers with every execution boundary instrumented."""

    tracked: bool
    started: bool = False


dispatch_progress: ContextVar[DispatchProgress | None] = ContextVar("phoenix_dispatch_progress", default=None)


def mark_dispatch_started() -> None:
    """Latch before an external effect; later refusals cannot erase prior effects."""
    if (progress := dispatch_progress.get()) is not None:
        progress.started = True


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


dispatch_request_id: ContextVar[str] = ContextVar("phoenix_dispatch_request_id", default="")

execution_guard: ContextVar[Callable[[], None] | None] = ContextVar("phoenix_execution_guard", default=None)


def check_request_authority() -> None:
    """Refuse further dispatch/disclosure if authority changed during a wait."""
    from .household_authority import household_authority

    household = household_authority.get()
    if household is not None:
        household.check()
    guard = execution_guard.get()
    if guard is not None:
        guard()
    authority = request_authority.get()
    if authority is None:
        return
    token = authority.current_token()
    version = policy_version.get()
    if version is not None and not token.accepts_policy_version(version):
        raise PolicyChangedError("Permissions changed. Retry with current permissions.")
