"""Validate local numeric operations and project exact HA service arguments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any, TypeGuard

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.valve import ValveEntityFeature
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.components.light import ATTR_SUPPORTED_COLOR_MODES, brightness_supported
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import State

from .const import (
    COMMAND_ROUTING_NUMERIC_OPERATIONS, COMMAND_ROUTING_SWITCH_SERVICES,
    COMMAND_ROUTING_NATIVE_VALUES, COMMAND_ROUTING_NUMERIC_BOUNDS, COMMAND_ROUTING_TEMPORAL_OPERATIONS,
    COMMAND_ROUTING_ENTITY_INTENTS, COMMAND_ROUTING_READ_INTENTS,
)


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

    native: bool = False

    def valid_value(self, value: Any) -> TypeGuard[int | float]:
        """Reject invalid values without coercion, clipping or rounding."""
        return (finite_number(value) and (self.native or 0 <= value <= 100)
                and (not self.whole_percent or value == int(value)))


def numeric_operation(intent: str) -> NumericOperation | None:
    spec = COMMAND_ROUTING_NUMERIC_OPERATIONS.get(intent)
    return NumericOperation(*spec, native=intent in COMMAND_ROUTING_NATIVE_VALUES) if spec else None


def command_domain(intent: str) -> str | None:
    if intent in COMMAND_ROUTING_READ_INTENTS:
        return None
    if intent in COMMAND_ROUTING_ENTITY_INTENTS:
        return COMMAND_ROUTING_ENTITY_INTENTS[intent][0]
    temporal = COMMAND_ROUTING_TEMPORAL_OPERATIONS.get(intent)
    if temporal:
        return temporal[0]
    operation = numeric_operation(intent)
    return operation.domain if operation else "light" if intent in COMMAND_ROUTING_SWITCH_SERVICES else None


def supports_command(state: State, intent: str) -> bool:
    """Check domain and current device support before planning or execution."""
    if intent == "HassGetState":
        return True
    if intent in COMMAND_ROUTING_TEMPORAL_OPERATIONS:
        from .temporal_values import temporal_supports
        return temporal_supports(state, intent)
    if intent in COMMAND_ROUTING_ENTITY_INTENTS:
        from .entity_commands import supports_entity_command
        return supports_entity_command(state, intent)
    if state.domain != command_domain(intent):
        return False
    operation = numeric_operation(intent)
    if operation is None:
        return True
    if operation.domain == "light":
        modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES)
        if not (isinstance(modes, (list, tuple, set, frozenset)) and all(isinstance(mode, str) for mode in modes)):
            return False
        return ("color_temp" in modes and numeric_limits(state, intent) is not None
                if intent == "PhoenixLightTemperature" else brightness_supported(modes))
    feature = {
        "HassSetPosition": CoverEntityFeature.SET_POSITION,
        "PhoenixCoverTilt": CoverEntityFeature.SET_TILT_POSITION,
        "HassSetVolume": MediaPlayerEntityFeature.VOLUME_SET,
        "HassFanSetSpeed": FanEntityFeature.SET_SPEED,
        "PhoenixValvePosition": ValveEntityFeature.SET_POSITION,
        "HassClimateSetTemperature": ClimateEntityFeature.TARGET_TEMPERATURE,
        "PhoenixClimateHumidity": ClimateEntityFeature.TARGET_HUMIDITY,
        "PhoenixWaterTemperature": WaterHeaterEntityFeature.TARGET_TEMPERATURE,
    }.get(intent)
    if feature is not None:
        supported = state.attributes.get("supported_features")
        if not (isinstance(supported, int) and not isinstance(supported, bool)
                and supported >= 0 and bool(supported & feature)):
            return False
    return numeric_limits(state, intent) is not None


def finite_number(value: Any) -> TypeGuard[int | float]:
    """Bound huge integers before float conversion; never coerce strings or booleans."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and -1e100 < value < 1e100 and math.isfinite(value))


def numeric_limits(state: State, intent: str) -> tuple[int | float, int | float, int | float | None] | None:
    """Read native entity limits; percentage controls have a fixed unit range."""
    if intent == "PhoenixCounterValue":
        low, high = state.attributes.get("minimum"), state.attributes.get("maximum")
        if any(value is not None and (type(value) is not int or not finite_number(value)) for value in (low, high)):
            return None
        lower, upper = low if low is not None else -math.inf, high if high is not None else math.inf
        return (lower, upper, None) if lower <= upper else None
    bounds = COMMAND_ROUTING_NUMERIC_BOUNDS.get(intent)
    if bounds is None:
        return (0, 100, None)
    low, high = (state.attributes.get(key) for key in bounds[:2])
    if not finite_number(low) or not finite_number(high) or low > high:
        return None
    step = state.attributes.get(bounds[2]) if bounds[2] else None
    if step is not None and (not finite_number(step) or step <= 0):
        return None
    if intent in ("PhoenixNumberValue", "PhoenixInputNumberValue") and step is None:
        return None
    return low, high, step


def valid_target_value(state: State | None, intent: str, value: Any) -> bool:
    """Check range and grid on each target at capture and again at dispatch/approval."""
    operation = numeric_operation(intent)
    if operation is None:
        return True
    if state is None or not supports_command(state, intent) or not operation.valid_value(value):
        return False
    limits = numeric_limits(state, intent)
    if limits is None or not limits[0] <= value <= limits[1]:
        return False
    step = limits[2]
    if step is None:
        return True
    try:
        steps = (Decimal(str(value)) - Decimal(str(limits[0]))) / Decimal(str(step))
        return steps == steps.to_integral_value()
    except InvalidOperation:
        return False


def numeric_metadata(state: State, intent: str) -> dict:
    """Describe supported units and limits without exposing current state or setpoints."""
    operation = numeric_operation(intent)
    if operation is None:
        return {}
    unit = ("K" if intent == "PhoenixLightTemperature" else
            state.attributes.get("unit_of_measurement", "") if operation.native else "%")
    limits = numeric_limits(state, intent)
    return {"unit": unit, "minimum": limits[0] if math.isfinite(limits[0]) else None,
            "maximum": limits[1] if math.isfinite(limits[1]) else None, "step": limits[2]} if limits else {}


def service_arguments(proposal: dict) -> dict | None:
    """Project a validated proposal, including exact percentage conversion."""
    intent = proposal.get("intent")
    if not isinstance(intent, str):
        return None
    if intent in COMMAND_ROUTING_ENTITY_INTENTS:
        from .entity_commands import entity_arguments
        return entity_arguments(proposal)
    if intent in COMMAND_ROUTING_TEMPORAL_OPERATIONS:
        from .temporal_values import temporal_arguments
        return temporal_arguments(proposal)
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
