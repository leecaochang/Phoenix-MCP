"""Observe routed command outcomes without retrying or claiming physical completion."""

from __future__ import annotations

import asyncio
import math
from typing import Any, TypeGuard

from homeassistant.core import State

from .command_operations import numeric_operation, service_arguments, finite_number
from .const import (
    COMMAND_VERIFICATION_POLL_SECONDS, COMMAND_VERIFICATION_WAIT_SECONDS,
    COMMAND_VERIFICATION_TOLERANCES, COMMAND_ROUTING_NUMERIC_STATE, COMMAND_ROUTING_TEMPORAL_OPERATIONS,
)
from .request_authority import RequestAuthority


def state_matches(proposal: dict, state: State | None, timezone: str = "UTC") -> bool:
    """Compare only the requested property, in the operation's native HA units."""
    if (state is None or state.state in ("unknown", "unavailable")
            or state.attributes.get("assumed_state")):
        return False
    intent = proposal.get("intent")
    if intent in ("HassTurnOn", "HassTurnOff"):
        return state.state == ("on" if intent == "HassTurnOn" else "off")
    if intent in COMMAND_ROUTING_TEMPORAL_OPERATIONS:
        from .temporal_values import temporal_state_matches
        return temporal_state_matches(proposal, state, timezone)
    operation = numeric_operation(intent) if isinstance(intent, str) else None
    if operation is None or not operation.valid_value(proposal.get(operation.proposal_field)):
        return False
    value = proposal[operation.proposal_field]
    if intent == "HassLightSet":
        # HA converts brightness_pct using round(255 * pct / 100).
        expected = round(255 * value / 100)
        if expected == 0:
            return state.state == "off"
        actual = state.attributes.get("brightness")
        return (state.state == "on" and _number(actual, 255) and actual > 0
                and abs(actual - expected) <= COMMAND_VERIFICATION_TOLERANCES[intent])
    if intent == "HassSetVolume":
        actual = state.attributes.get("volume_level")
        return (state.state != "off" and _number(actual, 1)
                and math.isclose(actual, value / 100, rel_tol=0, abs_tol=COMMAND_VERIFICATION_TOLERANCES[intent]))
    if intent == "HassSetPosition":
        actual = state.attributes.get("current_position")
        # A moving cover can pass the requested position without stopping there.
        return (state.state == ("closed" if value == 0 else "open")
                and _number(actual, 100) and abs(actual - value) <= COMMAND_VERIFICATION_TOLERANCES[intent])
    if intent in COMMAND_ROUTING_NUMERIC_STATE:
        field = COMMAND_ROUTING_NUMERIC_STATE[intent]
        actual = state.attributes.get(field) if field else state.state
        if field is None:
            try:
                actual = float(state.state)
            except (ValueError, TypeError):
                return False
        if intent in ("PhoenixValvePosition", "PhoenixCoverTilt") and state.state not in ("open", "closed"):
            return False
        if intent == "PhoenixLightTemperature" and state.state != "on":
            return False
        if intent == "HassFanSetSpeed" and state.state != ("on" if value else "off"):
            return False
        return finite_number(actual) and math.isclose(actual, value, rel_tol=0, abs_tol=1e-6)
    return False


def _number(value: Any, maximum: float) -> TypeGuard[int | float]:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= maximum)


async def async_verify_command_state(hass: Any, data: Any, token: Any, plan: dict) -> str:
    """Return a scoped observation, never resend, poll a device, or clear recovery."""
    from .command_execution import _target_fingerprint

    proposal = plan.get("proposal", {})
    if service_arguments(proposal) is None:
        return "unconfirmed"
    authority = RequestAuthority(hass, data, token.id, token.token_hash)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + COMMAND_VERIFICATION_WAIT_SECONDS
    while True:
        current = authority.current_token()
        if current.settings_version != plan.get("token_version"):
            return "unconfirmed"
        # Recheck read access, exposure and registry identity before every read.
        # The fingerprint deliberately excludes changing state and setpoints.
        for entity_id in proposal["entity_ids"]:
            fingerprint = _target_fingerprint(hass, current, entity_id, "HassGetState")
            if fingerprint is None or fingerprint != plan.get("fingerprints", {}).get(entity_id):
                return "unconfirmed"
        if all(state_matches(proposal, hass.states.get(eid), hass.config.time_zone) for eid in proposal["entity_ids"]):
            return "matched"
        remaining = deadline - loop.time()
        if remaining <= 0:
            return "unconfirmed"
        await asyncio.sleep(min(remaining, COMMAND_VERIFICATION_POLL_SECONDS))
