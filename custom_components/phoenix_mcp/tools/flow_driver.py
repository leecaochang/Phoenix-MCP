"""Bounded driver for one Home Assistant data-entry flow, for repair fixes and config subentries.

The caller starts the flow on its own manager and says what success looks like; this module
answers each form from the caller's flat values and each menu from its menu choices, refuses
browser, OAuth and progress steps, and aborts anything it does not finish. It never reads or
writes stored configuration itself: Home Assistant's flow validates and applies.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType

from .integration_reconfigure import (
    FLOW_STEP_BUDGET, _UNSUPPORTED_TYPES, _form_payload, _public_schema, _result_type,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlowOutcome:
    """What one bounded flow run did, without guessing past what Home Assistant reported."""

    finished: bool
    submitted: bool
    reason: str
    result: Mapping[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _abort(manager: Any, flow_id: str | None) -> None:
    if not flow_id:
        return
    try:
        manager.async_abort(flow_id)
    except Exception:  # already finished or gone is harmless
        _LOGGER.debug("Could not abort flow %s", flow_id, exc_info=True)


async def async_drive_flow(
    manager: Any,
    start: Callable[[], Awaitable[Mapping[str, Any]]],
    config: Mapping[str, Any],
    menu_choices: list[str],
    success: Callable[[Mapping[str, Any]], bool],
) -> FlowOutcome:
    """Run a flow from ``start`` to a result ``success`` accepts, answering from the caller's values.

    ``submitted`` says whether any answer reached the integration, so a failure after it may
    have changed something. Unused values are reported, not ignored silently.
    """
    try:
        result = await start()
    except Exception as err:  # integration import or init failure
        return FlowOutcome(False, False, "The flow could not be started.", details={"error": type(err).__name__})
    flow_id = result.get("flow_id")
    encountered: set[str] = set()
    menu_index = 0
    submitted = False
    try:
        for _step in range(FLOW_STEP_BUDGET):
            kind = _result_type(result)
            if kind is FlowResultType.FORM:
                if submitted and result.get("errors"):
                    _abort(manager, flow_id)
                    return FlowOutcome(False, True, "The integration rejected the submitted values.", details={
                        "validation_errors": result.get("errors") or {}, "schema": _public_schema(result),
                        "step_id": result.get("step_id")})
                payload, missing = _form_payload(result, config, encountered)
                if payload is None or missing:
                    _abort(manager, flow_id)
                    return FlowOutcome(False, submitted, missing[0] if payload is None else "Required values are missing from config.",
                                       details={"missing_fields": missing, "schema": _public_schema(result),
                                                "step_id": result.get("step_id")})
                submitted = True
                try:
                    result = await manager.async_configure(flow_id, payload)
                except vol.Invalid as err:
                    _abort(manager, flow_id)
                    return FlowOutcome(False, True, "The integration rejected the submitted values.",
                                       details={"validation_error": str(err), "step_id": result.get("step_id")})
                continue
            if kind is FlowResultType.MENU:
                raw = result.get("menu_options")
                options = list(raw) if isinstance(raw, (list, tuple)) else list(raw or {})
                if menu_index >= len(menu_choices) or (options and menu_choices[menu_index] not in options):
                    _abort(manager, flow_id)
                    return FlowOutcome(False, submitted, "The flow needs a menu choice it offers.", details={
                        "step_id": result.get("step_id"), "menu_options": options, "menu_choice_index": menu_index})
                choice = menu_choices[menu_index]
                menu_index += 1
                submitted = True
                result = await manager.async_configure(flow_id, {"next_step_id": choice})
                continue
            if kind in _UNSUPPORTED_TYPES:
                _abort(manager, flow_id)
                return FlowOutcome(False, submitted, "Browser, OAuth and progress steps are unsupported; use Home Assistant's frontend.",
                                   details={"step_type": getattr(kind, "value", kind)})
            if success(result):
                return FlowOutcome(True, submitted, "Home Assistant accepted the flow.", result, {
                    "unused_config_fields": sorted(set(config) - encountered),
                    "unused_menu_choices": menu_choices[menu_index:]})
            _abort(manager, flow_id)
            return FlowOutcome(False, submitted, "The integration ended the flow without the expected result.", result, {
                "flow_result_type": getattr(kind, "value", kind), "flow_reason": result.get("reason")})
        _abort(manager, flow_id)
        return FlowOutcome(False, submitted, "The flow exceeded Phoenix's step budget.", details={"step_budget": FLOW_STEP_BUDGET})
    except Exception as err:  # third-party flow boundary; keep the ambiguity visible
        _LOGGER.exception("Flow %s failed", flow_id)
        _abort(manager, flow_id)
        return FlowOutcome(False, submitted, "The flow failed inside the integration.", details={"error": type(err).__name__})
