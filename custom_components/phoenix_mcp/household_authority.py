"""Intersect household ingress with the requesting Home Assistant authority."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from inspect import unwrap
from typing import TYPE_CHECKING, TypedDict

from homeassistant.auth import EVENT_USER_REMOVED, EVENT_USER_UPDATED
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import Context, callback
from homeassistant.exceptions import Unauthorized

from .request_authority import AuthorityChangedError

if TYPE_CHECKING:
    from homeassistant.auth.models import User
    from homeassistant.core import Event, HomeAssistant


@dataclass
class HouseholdAuthority:
    """A turn-local identity that cannot survive changes or outlive its turn."""

    user_id: str | None
    user: User | None
    invalid: bool = False
    closed: bool = False
    context: Context | None = None

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


def household_admin_access() -> bool:
    """Token administration requires a known administrator on household ingress."""
    authority = household_authority.get()
    return authority is None or authority.is_admin()


def household_tool_access(tool_name: str) -> bool:
    """Shared-token history belongs to administrators, even without a capability."""
    from .const import HOUSEHOLD_ADMIN_TOOLS

    return tool_name not in HOUSEHOLD_ADMIN_TOOLS or household_admin_access()


class _ServiceContextKwargs(TypedDict, total=False):
    """Optional service-call context, without changing non-household calls."""

    context: Context


def household_service_kwargs(hass: HomeAssistant, domain: str, service: str) -> _ServiceContextKwargs:
    """Preserve HA caller identity and refuse native admin services for households.

    HA's admin-service wrapper exempts a context without a user ID. Inspect its
    registered job for unidentified callers too; sending an empty Context would
    silently grant system authority. HA exposes no public admin-service flag.
    """
    authority = household_authority.get()
    if authority is None:
        return {}
    authority.check()
    if not authority.is_admin():
        from homeassistant.helpers import service as ha_service

        admin_handler = getattr(ha_service, "_async_admin_handler", None)
        if admin_handler is None:
            # Refuse if HA changes the registration adapter we have qualified.
            raise Unauthorized()
        registered = hass.services.async_services().get(domain, {}).get(service)
        if registered is not None:
            try:
                target = unwrap(registered.job.target)
                while isinstance(target, partial):
                    target = unwrap(target.func)
            except (AttributeError, ValueError):
                raise Unauthorized() from None
            if target is admin_handler:
                raise Unauthorized()
    if authority.context is not None:
        return {"context": authority.context}
    if authority.user_id is not None:
        return {"context": Context(user_id=authority.user_id)}
    return {}


@asynccontextmanager
async def household_scope(
    hass: HomeAssistant, context: Context | None = None, *, user_id: str | None = None,
) -> AsyncIterator[HouseholdAuthority]:
    """Admit only HA-supplied identity, with no model or token supplied override."""
    if context is not None:
        user_id = context.user_id
    user = await hass.auth.async_get_user(user_id) if user_id else None
    authority = HouseholdAuthority(user_id, user, invalid=bool(user_id and user is None), context=context)

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
