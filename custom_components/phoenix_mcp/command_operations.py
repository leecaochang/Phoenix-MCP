"""Validate local numeric operations and project exact HA service arguments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.light import ATTR_SUPPORTED_COLOR_MODES, brightness_supported
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import State

from .const import COMMAND_ROUTING_NUMERIC_OPERATIONS, COMMAND_ROUTING_SWITCH_SERVICES


@dataclass(frozen=True)
class NumericOperation:
    """Units and bounds belong to the operation, independently of the provider."""

    domain: str
    slot: str
    proposal_field: str
    service: str
    service_field: str
    divisor: int
    whole_percent: bool
    match_domains: tuple[str, ...]

    def valid_value(self, value: Any) -> TypeGuard[int | float]:
        """Reject invalid values without coercion, clipping or rounding."""
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and 0 <= value <= 100 and (not self.whole_percent or value == int(value)))


def numeric_operation(intent: str) -> NumericOperation | None:
    spec = COMMAND_ROUTING_NUMERIC_OPERATIONS.get(intent)
    return NumericOperation(*spec) if spec else None


def command_domain(intent: str) -> str | None:
    operation = numeric_operation(intent)
    return operation.domain if operation else "light" if intent in COMMAND_ROUTING_SWITCH_SERVICES else None


def supports_command(state: State, intent: str) -> bool:
    """Check domain and current device support before planning or execution."""
    if intent == "HassGetState":
        return True
    if state.domain != command_domain(intent):
        return False
    operation = numeric_operation(intent)
    if operation is None:
        return True
    if operation.domain == "light":
        modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES)
        return (isinstance(modes, (list, tuple, set, frozenset))
                and all(isinstance(mode, str) for mode in modes) and brightness_supported(modes))
    feature = (CoverEntityFeature.SET_POSITION if operation.domain == "cover"
               else MediaPlayerEntityFeature.VOLUME_SET)
    supported = state.attributes.get("supported_features")
    return (isinstance(supported, int) and not isinstance(supported, bool)
            and supported >= 0 and bool(supported & feature))


def service_arguments(proposal: dict) -> dict | None:
    """Project a validated proposal, including exact percentage conversion."""
    intent = proposal.get("intent")
    if not isinstance(intent, str):
        return None
    domain = command_domain(intent)
    ids = proposal.get("entity_ids")
    if (domain is None or not isinstance(ids, list) or not ids
            or any(not isinstance(eid, str) or not eid.startswith(domain + ".") for eid in ids)
            or len(set(ids)) != len(ids)):
        return None
    operation = numeric_operation(intent)
    service_data = {}
    if operation:
        value = proposal.get(operation.proposal_field)
        if not operation.valid_value(value):
            return None
        service = operation.service
        service_data[operation.service_field] = value / operation.divisor if operation.divisor != 1 else value
    else:
        service = COMMAND_ROUTING_SWITCH_SERVICES[intent]
    return {"domain": domain, "service": service, "entity_id": ids, "service_data": service_data}
