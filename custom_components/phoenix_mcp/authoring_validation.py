"""Bounded, value-free diagnostics for HA automation and script authoring."""

from __future__ import annotations

import logging
from traceback import format_tb

import voluptuous as vol
from homeassistant.components.automation.config import PLATFORM_SCHEMA
from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA

from .const import AUTHORING_MAX_ERRORS, AUTHORING_MAX_PATH_DEPTH

_LOGGER = logging.getLogger(__name__)

# Only schema vocabulary can be disclosed; dynamic field names and selector
# values may contain credentials or inaccessible resource identifiers.
_FIELDS = frozenset("alias description id trigger triggers condition conditions action actions sequence mode max max_exceeded initial_state enabled variables trigger_variables fields trace icon use_blueprint path input platform entity_id device_id area_id floor_id label_id service target data value_template template delay wait_template timeout continue_on_timeout repeat count while until choose default then else if for from to at above below event event_type event_data name required selector advanced example stored_traces".split())
_ROOT_FIELDS = frozenset("alias description trigger triggers condition conditions action actions sequence mode max max_exceeded initial_state enabled variables trigger_variables fields trace icon use_blueprint".split())


def expected_shape(domain: str) -> str:
    """Return a small native config example without imposing optional fields."""
    if domain == "automation":
        return 'Expected shape: config={"triggers": [{"trigger": "time", "at": "12:00:00"}], "actions": [{"delay": "00:00:01"}]}.'
    return 'Expected shape: script_id="my_script", config={"sequence": [{"delay": "00:00:01"}]}.'


def validation_message(domain: str, error: vol.Invalid | None = None) -> str:
    """Project error classes and paths, never validator text or input values."""
    findings = []
    errors = error.errors if isinstance(error, vol.MultipleInvalid) else [error]
    for item in errors[:AUTHORING_MAX_ERRORS]:
        path = "config"
        if item is not None:
            for part in item.path[:AUTHORING_MAX_PATH_DEPTH]:
                if isinstance(part, vol.Marker):
                    part = part.schema
                if type(part) is int and 0 <= part <= 100000:
                    path += f"[{part}]"
                elif isinstance(part, str) and part in _FIELDS:
                    path += "." + part
                else:
                    path += ".[field]"
                    break
        reason = "has an invalid value or structure"
        if isinstance(item, vol.RequiredFieldInvalid):
            reason = "is required"
        elif type(item).__name__ in {"ExtraInvalid", "ExtraKeysInvalid"}:
            reason = "is not supported at this location"
        elif isinstance(item, vol.TypeInvalid):
            reason = "has the wrong type"
        finding = f"{path} {reason}."
        if finding not in findings:
            findings.append(finding)
    if len(errors) > AUTHORING_MAX_ERRORS:
        findings.append("Additional validation errors omitted; correct these fields and retry.")
    return f"{domain.capitalize()} config failed validation. " + " ".join(findings) + " " + expected_shape(domain)


def authoring_input_error(args: dict, domain: str) -> str | None:
    """Combine independent nesting and installed-HA schema findings safely."""
    findings = [f"{field} belongs inside config as config.{field}."
                for field in sorted(_ROOT_FIELDS.intersection(args))[:AUTHORING_MAX_ERRORS]]
    config = args.get("config")
    if not isinstance(config, dict):
        findings.append("config must be an object containing one " + domain + "; do not wrap it in a domain or resource ID.")
        findings.append(expected_shape(domain))
    else:
        if "enabled" in config or "enabled" in args:
            advice = "Use config.initial_state for automation startup state; omit it to let HA restore the saved state. Runtime changes use call_service with automation.turn_on or automation.turn_off and normal authorization."
            if domain == "script":
                advice = "Scripts have no enabled config setting; use sequence for their actions."
            findings.append("enabled is not a root config setting. " + advice)
        if domain == "automation" and "sequence" in config:
            findings.append("config.sequence belongs to a script; automations use config.triggers and config.actions. For a script use create_script or edit_script.")
        if domain == "script" and any(field in config for field in ("trigger", "triggers", "action", "actions")):
            findings.append("Scripts use config.sequence; triggers belong to an automation. For an automation use create_automation or edit_automation.")
        if "automation" in config or "script" in config or "config" in config:
            findings.append("Pass one configuration directly inside config; remove the extra domain/config wrapper.")
        # Blueprint expansion belongs to the async HA validator. Do not validate
        # the wrapper as though it were an expanded native configuration.
        if "use_blueprint" not in config:
            try:
                (PLATFORM_SCHEMA if domain == "automation" else SCRIPT_ENTITY_SCHEMA)(config)
            except vol.Invalid as error:
                findings.append(validation_message(domain, error))
            except Exception as error:
                # Traceback frames retain the failing API location, without an
                # exception message that may quote caller secrets or resources.
                _LOGGER.error("Internal authoring schema failure:\n%s", "".join(format_tb(error.__traceback__)))
                return "Internal error validating this config."
    return " ".join(findings) if findings else None
