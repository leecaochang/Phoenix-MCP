"""Bounded, scoped observation of authored scene and script identities."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import AUTHORING_REGISTRATION_POLL_SECONDS, AUTHORING_REGISTRATION_TIMEOUT_SECONDS
from .policy_engine import Permission, assist_expose_check, resolve_registry_access
from .request_authority import check_request_authority
from .access_key_store import AccessKeyRecord


async def async_observe_registration(
    hass: HomeAssistant, access_key: AccessKeyRecord, domain: Literal["scene", "script"], storage_id: str,
) -> dict[str, Any]:
    """Observe identity and state, without promising config freshness or availability.

    Registry entries survive reloads. Only a present State completes the live
    observation; disabled entries complete separately. Missing and inaccessible
    entries have the same result. No guessed entity ID is ever returned.
    Cancellation propagates and there are no listeners or background tasks.
    """
    deadline = asyncio.get_running_loop().time() + AUTHORING_REGISTRATION_TIMEOUT_SECONDS
    platform = "homeassistant" if domain == "scene" else "script"
    registry = er.async_get(hass)
    while True:
        check_request_authority()
        observed: dict[str, Any] = {
            "status": "timed_out", "entity_id": None, "state_present": None,
        }
        entity_id = registry.async_get_entity_id(domain, platform, storage_id)
        expose = assist_expose_check(access_key, hass)
        if (
            entity_id is not None
            and resolve_registry_access(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
            and (expose is None or expose(entity_id))
        ):
            entry = registry.async_get(entity_id)
            state_present = hass.states.get(entity_id) is not None
            observed.update(entity_id=entity_id, state_present=state_present)
            if entry is not None and entry.disabled_by is not None:
                observed["status"] = "disabled"
                return observed
            if state_present:
                observed["status"] = "observed"
                return observed
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return observed
        await asyncio.sleep(min(AUTHORING_REGISTRATION_POLL_SECONDS, remaining))
