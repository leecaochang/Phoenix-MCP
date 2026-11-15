"""Bounded driver for one Home Assistant data-entry flow, for repair fixes and config subentries.

The caller starts the flow on its own manager and says what success looks like; this module
answers each form from the caller's flat values and each menu from its menu choices, refuses
browser, OAuth and progress steps, and aborts anything it does not finish. It never reads or
writes stored configuration itself: Home Assistant's flow validates and applies.

Inspection opens a flow, describes its first step with Home Assistant's own English strings, and
closes it without submitting anything, so an agent can read a form before an approval is asked.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations

from ..const import HELPER_FIELD_HELP_MAX, HELPER_STEP_HELP_MAX, REDACTION_SENTINEL
from ..policy_engine import is_sensitive_key
from .integration_reconfigure import (
    FLOW_STEP_BUDGET, RECONFIGURE_SUCCESS_REASON, _UNSUPPORTED_TYPES, _form_payload, _public_schema,
    _result_type,
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


def inspect_mode(args: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Whether a flow tool was asked only to show its first step, or why the request is malformed."""
    value = args.get("inspect", False)
    if not isinstance(value, bool):
        return False, "inspect must be true or false."
    if value and (args.get("config") or args.get("menu_choices")):
        return True, "inspect shows the first step without submitting anything, so it takes no config or menu_choices."
    return value, None


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
# A stored value shorter than this is not looked for inside a longer placeholder, or every number
# and short word in the entry would hide unrelated text.
_STORED_SUBSTRING_MIN = 4


def fill_placeholders(text: str, placeholders: Mapping[str, Any]) -> str:
    """``text`` with each ``{name}`` the step supplies filled in; unknown names stay as written."""
    return _PLACEHOLDER.sub(
        lambda match: str(placeholders[match.group(1)]) if match.group(1) in placeholders else match.group(0), text)


def translated_errors(strings: Mapping[str, str], base: str, errors: Any, placeholders: Mapping[str, Any]) -> dict[str, str]:
    """Home Assistant's English text for each form error code; a code without text stays a code."""
    out: dict[str, str] = {}
    for name, code in (errors.items() if isinstance(errors, Mapping) else ()):
        text = strings.get(f"{base}error.{code}") if isinstance(code, str) else None
        out[str(name)] = (fill_placeholders(text.strip(), placeholders)[:HELPER_FIELD_HELP_MAX]
                          if isinstance(text, str) and text.strip() else str(code))
    return out


async def async_flow_strings(hass: HomeAssistant, domain: str, category: str) -> dict[str, str]:
    """The integration's English strings for one category; empty when it has none."""
    try:
        return dict(await async_get_translations(hass, "en", category, [domain]))
    except Exception:  # strings are documentation, never required
        _LOGGER.debug("No %s translations for %s", category, domain, exc_info=True)
        return {}


def stored_strings(values: Iterable[Any]) -> set[str]:
    """Every scalar inside stored configuration, as text, to keep it out of what a step shows."""
    found: set[str] = set()

    def walk(value: Any, depth: int) -> None:
        if depth > 8:
            return
        if isinstance(value, Mapping):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                found.add(text)

    for value in values:
        walk(value, 0)
    return found


def safe_placeholders(
    placeholders: Any, stored: set[str], scrub: Callable[[str], Any],
) -> dict[str, str]:
    """A step's placeholders as an agent may read them.

    A flow often fills a placeholder from the entry's own configuration (a host, a URL), which
    Phoenix never shows, so a value matching or containing a stored value is withheld, as is any
    value under a sensitive name. The rest is scrubbed for the key.
    """
    out: dict[str, str] = {}
    for name, value in (placeholders.items() if isinstance(placeholders, Mapping) else ()):
        text = str(value)
        if (is_sensitive_key(str(name)) or text.strip() in stored
                or any(len(item) >= _STORED_SUBSTRING_MIN and item in text for item in stored)):
            out[str(name)] = REDACTION_SENTINEL
        else:
            out[str(name)] = str(scrub(text))
    return out


def _annotate(fields: list, strings: Mapping[str, str], step: str, prefix: str, fill: Callable[[str], str]) -> None:
    """Give each field its label and help from the step's strings; section fields read sections.<name>."""
    for item in fields:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"]
        for key, attr in (("data", "label"), ("data_description", "help")):
            text = strings.get(f"{prefix}{key}.{name}")
            if isinstance(text, str) and text.strip():
                item[attr] = fill(text.strip())[:HELPER_FIELD_HELP_MAX]
        if isinstance(item.get("schema"), list):
            title = strings.get(f"{step}sections.{name}.name")
            if "label" not in item and isinstance(title, str) and title.strip():
                item["label"] = fill(title.strip())[:HELPER_FIELD_HELP_MAX]
            _annotate(item["schema"], strings, step, f"{step}sections.{name}.", fill)


def describe_step(result: Mapping[str, Any], strings: Mapping[str, str], base: str,
                  placeholders: Mapping[str, str]) -> dict[str, Any]:
    """One flow step as an agent reads it: its form or menu with Home Assistant's own labels.

    ``base`` is where the flow's strings start (``component.<domain>.config.``); a form keeps no
    stored suggested or default value.
    """
    kind = _result_type(result)
    step_id = result.get("step_id")
    step = f"{base}step.{step_id}." if isinstance(step_id, str) else None

    def fill(text: str) -> str:
        return fill_placeholders(text, placeholders)

    def text(key: str, max_chars: int) -> str | None:
        value = strings.get(f"{step}{key}") if step else None
        return fill(value.strip())[:max_chars] if isinstance(value, str) and value.strip() else None

    body: dict[str, Any] = {"step_id": step_id, "step_type": getattr(kind, "value", kind)}
    for key in ("title", "description"):
        if (value := text(key, HELPER_STEP_HELP_MAX)) is not None:
            body[key] = value
    if kind is FlowResultType.FORM:
        schema = _public_schema(result)
        if schema is None:
            body["schema"] = None
            body["note"] = "This form could not be described; Home Assistant's frontend shows it."
        else:
            if step:
                _annotate(schema, strings, step, step, fill)
            body["schema"] = schema
        body["last_step"] = result.get("last_step")
        if result.get("errors"):
            body["errors"] = translated_errors(strings, base, result.get("errors"), placeholders)
    elif kind is FlowResultType.MENU:
        raw = result.get("menu_options")
        options = list(raw) if isinstance(raw, (list, tuple)) else list(raw or {})
        labels = raw if isinstance(raw, Mapping) else {}
        body["menu_options"] = [
            {"option": option, **({"label": label} if (label := text(f"menu_options.{option}", HELPER_FIELD_HELP_MAX)
                                                        or labels.get(option)) else {})}
            for option in options if isinstance(option, str)
        ]
    return body


async def async_inspect_flow(
    manager: Any,
    start: Callable[[], Awaitable[Mapping[str, Any]]],
    strings: Mapping[str, str],
    base: str,
    stored: set[str],
    scrub: Callable[[str], Any],
) -> dict[str, Any]:
    """Open a flow, describe its first step, and close it; nothing is ever submitted.

    Opening a flow runs the integration's first step, as Home Assistant's own dialog does. A step
    that finishes the flow by itself has run integration code, which the result says rather than
    hide; any other open flow is aborted before returning.
    """
    try:
        result = await start()
    except Exception as err:  # integration import or init failure
        return {"status": "not_started", "reason": "The flow could not be started.", "error": type(err).__name__}
    flow_id = result.get("flow_id")
    kind = _result_type(result)
    try:
        placeholders = safe_placeholders(result.get("description_placeholders"), stored, scrub)
        if kind in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
            reason = result.get("reason")
            return {
                "status": "flow_ended_at_first_step",
                "step_type": getattr(kind, "value", kind),
                "flow_reason": scrub(str(reason)) if reason is not None else None,
                "may_have_changed": kind is FlowResultType.CREATE_ENTRY or reason == RECONFIGURE_SUCCESS_REASON,
                "reason": "The integration ended its flow without showing a form. Phoenix submitted nothing.",
            }
        body = {"status": "inspected", "step": describe_step(result, strings, base, placeholders)}
        if kind in _UNSUPPORTED_TYPES:
            body["reason"] = "Browser, OAuth and progress steps are unsupported; use Home Assistant's frontend."
        return body
    finally:
        if kind not in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
            _abort(manager, flow_id)
