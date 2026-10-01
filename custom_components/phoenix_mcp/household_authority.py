"""Intersect household ingress with the requesting Home Assistant authority."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.auth import EVENT_USER_REMOVED, EVENT_USER_UPDATED
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import callback

from .request_authority import AuthorityChangedError

if TYPE_CHECKING:
    from homeassistant.auth.models import User
    from homeassistant.core import Context, Event, HomeAssistant


@dataclass
class HouseholdAuthority:
    """A turn-local identity that cannot survive changes or outlive its turn."""

    user_id: str | None
    user: User | None
    invalid: bool = False
    closed: bool = False

    def check(self) -> None:
        if self.closed or self.invalid or (self.user is not None and not self.user.is_active):
            raise AuthorityChangedError("Home Assistant user authority changed.")

    def is_admin(self) -> bool:
        self.check()
        return self.user is not None and self.user.is_admin

    def entity_access(self, entity_id: str) -> tuple[bool, bool]:
        """Unknown satellite identity retains only the administrator-bound scope."""
        self.check()
        if self.user is None:
            return True, True
        permissions = self.user.permissions
        return (permissions.check_entity(entity_id, POLICY_READ),
                permissions.check_entity(entity_id, POLICY_CONTROL))


household_authority: ContextVar[HouseholdAuthority | None] = ContextVar(
    "phoenix_household_authority", default=None,
)


@asynccontextmanager
async def household_scope(
    hass: HomeAssistant, context: Context | None = None, *, user_id: str | None = None,
) -> AsyncIterator[HouseholdAuthority]:
    """Admit only HA-supplied identity, with no model or token supplied override."""
    if context is not None:
        user_id = context.user_id
    user = await hass.auth.async_get_user(user_id) if user_id else None
    authority = HouseholdAuthority(user_id, user, invalid=bool(user_id and user is None))

    @callback
    def changed(event: Event) -> None:
        if event.data.get("user_id") == user_id:
            authority.invalid = True

    unregister = []
    if user_id:
        unregister = [hass.bus.async_listen(name, changed)
                      for name in (EVENT_USER_UPDATED, EVENT_USER_REMOVED)]
    admitted = household_authority.set(authority)
    try:
        yield authority
    finally:
        authority.closed = True
        household_authority.reset(admitted)
        for remove in unregister:
            remove()
