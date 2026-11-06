"""Bounded, value-free diagnostics for HA automation and script authoring."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from difflib import get_close_matches
from traceback import format_tb
from typing import Any

import voluptuous as vol
from homeassistant.components.automation.config import PLATFORM_SCHEMA
from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.loader import IntegrationNotFound, async_get_integration

from .const import AUTHORING_MAX_ERRORS, AUTHORING_MAX_PATH_DEPTH
from .yaml_includes import TAG_STRING_REFUSAL, contains_tag_strings

_LOGGER = logging.getLogger(__name__)

# Only schema vocabulary can be disclosed; dynamic field names and selector
# values may contain credentials or inaccessible resource identifiers.
_FIELDS = frozenset("alias description id trigger triggers condition conditions action actions sequence mode max max_exceeded initial_state enabled variables trigger_variables fields trace icon use_blueprint path input platform entity_id device_id area_id floor_id label_id service target data value_template template delay wait_template timeout continue_on_timeout repeat count while until choose default then else if for from to at above below event event_type event_data name required selector advanced example stored_traces entities metadata".split())
_ROOT_FIELDS = frozenset("alias description trigger triggers condition conditions action actions sequence mode max max_exceeded initial_state enabled variables trigger_variables fields trace icon use_blueprint".split())

# Names Core resolves without registering them as keys: undotted legacy trigger
# platforms it aliases, and condition platforms it handles itself. Both sets follow Core's alias tables.
_LEGACY_TRIGGER_KEYS = frozenset({"device", "event", "numeric_state", "state", "time", "time_pattern"})
_BUILTIN_CONDITION_KEYS = frozenset({"and", "device", "not", "numeric_state", "or", "state", "template", "time", "trigger"})

# Older HA validates with voluptuous, which reports an undeclared key as a plain
# Invalid carrying this fixed text; newer HA validates with probatio, which has
# a class for it. The text is only compared, never shown.
_EXTRA_KEY_CLASSES = frozenset({"ExtraInvalid", "ExtraKeysInvalid"})
_VOLUPTUOUS_EXTRA_KEY_TEXT = "extra keys not allowed"
# Only a key in Core's own key syntax is echoed back to the caller.
_ECHOABLE_KEY = re.compile(r"[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?")
_MATCH_LIMIT = 3
# A key in the caller's own domain is the likely rename; a key from another
# domain is offered only when it is nearly identical, such as a domain typo.
_SAME_DOMAIN_CUTOFF = 0.6
_OTHER_DOMAIN_CUTOFF = 0.8


def expected_shape(domain: str) -> str:
    """Return a small native config example without imposing optional fields."""
    if domain == "automation":
        return 'Expected shape: config={"triggers": [{"trigger": "time", "at": "12:00:00"}], "actions": [{"delay": "00:00:01"}]}.'
    if domain == "scene":
        return 'Expected shape: config={"name": "My scene", "entities": {"light.example": "on"}}, plus optional icon and metadata.'
    return 'Expected shape: script_id="my_script", config={"sequence": [{"delay": "00:00:01"}]}.'


def validation_message(
    domain: str, error: vol.Invalid | None = None, extra: Iterable[str] = (), *, path_offset: int = 0,
) -> str:
    """Project error classes and paths, never validator text or input values.

    ``extra`` carries Phoenix's own findings; ``path_offset`` skips a wrapping schema's path
    parts.
    """
    findings = []
    errors = error.errors if isinstance(error, vol.MultipleInvalid) else [error]
    for item in errors[:AUTHORING_MAX_ERRORS]:
        path = "config"
        if item is not None:
            for part in item.path[path_offset:path_offset + AUTHORING_MAX_PATH_DEPTH]:
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
        elif type(item).__name__ in _EXTRA_KEY_CLASSES or getattr(item, "msg", None) == _VOLUPTUOUS_EXTRA_KEY_TEXT:
            reason = "is not supported at this location"
        elif isinstance(item, vol.TypeInvalid):
            reason = "has the wrong type"
        finding = f"{path} {reason}."
        if finding not in findings:
            findings.append(finding)
    findings.extend(extra)
    if len(errors) > AUTHORING_MAX_ERRORS:
        findings.append("Additional validation errors omitted; correct these fields and retry.")
    return f"{domain.capitalize()} config failed validation. " + " ".join(findings) + " " + expected_shape(domain)


async def async_validation_message(
    hass: HomeAssistant, domain: str, config: Any, error: vol.Invalid | None = None
) -> str:
    """Project a Core validation failure, naming every unregistered key."""
    return validation_message(domain, error, await async_unknown_key_findings(hass, config))


async def async_unknown_key_findings(hass: HomeAssistant, config: Any) -> list[str]:
    """Name each trigger or condition key the running Core has not registered.

    ``closest_matches`` searches the key's own domain first. A key is unknown only when its
    domain is loaded or no such integration exists. Failure to read keys gives no findings.
    """
    sites = _key_sites(config)
    if not sites:
        return []
    try:
        # Local: inside a guard for releases that lack it.
        from homeassistant.helpers.condition import (
            async_get_all_descriptions as async_get_all_condition_descriptions,
        )
        # Local: inside a guard for releases that lack it.
        from homeassistant.helpers.trigger import (
            async_get_all_descriptions as async_get_all_trigger_descriptions,
        )

        registered = {
            "trigger": set(await async_get_all_trigger_descriptions(hass)) | _LEGACY_TRIGGER_KEYS,
            "condition": set(await async_get_all_condition_descriptions(hass)) | _BUILTIN_CONDITION_KEYS,
        }
    except Exception:
        # Degrade to the plain projection; never break the tool.
        _LOGGER.debug("registered trigger and condition keys unavailable", exc_info=True)
        return []
    findings: list[str] = []
    for path, kind, key in sites:
        if len(findings) >= AUTHORING_MAX_ERRORS:
            break
        if not await _is_unknown_key(hass, kind, key, registered[kind]):
            continue
        shown = f' "{key}"' if _ECHOABLE_KEY.fullmatch(key) else ""
        finding = f"{path}{shown} is not a {kind} key this Home Assistant has registered."
        if matches := _closest_keys(key, registered[kind]):
            finding += f" closest_matches: {json.dumps(matches)}."
        findings.append(finding)
    return findings


async def _is_unknown_key(hass: HomeAssistant, kind: str, key: str, registered: set[str]) -> bool:
    """Whether Core certainly has no such key, mirroring how it resolves one."""
    domain = key.split(".", 1)[0]
    if not domain or key in registered or (kind == "condition" and domain in _BUILTIN_CONDITION_KEYS):
        return False
    if domain in hass.config.components:
        return True
    try:
        await async_get_integration(hass, domain)
    except IntegrationNotFound:
        return True
    except Exception:
        # An unreadable integration is not proof of a typo.
        _LOGGER.debug("integration lookup failed while checking a %s key", kind, exc_info=True)
    return False


def _closest_keys(key: str, registered: set[str]) -> list[str]:
    """Registered keys close to ``key``, its own domain ranked first."""
    key = key.lower()
    domain = key.split(".", 1)[0]
    same = sorted(k for k in registered if k == domain or k.startswith(domain + "."))
    other = sorted(registered.difference(same))
    matches = get_close_matches(key, same, _MATCH_LIMIT, _SAME_DOMAIN_CUTOFF)
    matches += get_close_matches(key, other, _MATCH_LIMIT, _OTHER_DOMAIN_CUTOFF)
    return matches[:_MATCH_LIMIT]


def _key_sites(config: Any) -> list[tuple[str, str, str]]:
    """List (path, kind, key) for every trigger and condition key in a raw config.

    Walks every nesting action, with legacy ``platform`` and and/or/not shorthand. Paths use
    only schema vocabulary and list indexes.
    """
    sites: list[tuple[str, str, str]] = []

    def items(value: Any, path: str) -> Iterable[tuple[str, Any]]:
        for index, item in enumerate(value if isinstance(value, list) else [value]):
            if isinstance(item, dict):
                yield f"{path}[{index}]", item

    def triggers(value: Any, path: str, depth: int) -> None:
        for here, item in items(value, path):
            if "triggers" in item and depth < AUTHORING_MAX_PATH_DEPTH:
                triggers(item["triggers"], here + ".triggers", depth + 1)
            for field in ("trigger", "platform"):
                if isinstance(item.get(field), str):
                    sites.append((f"{here}.{field}", "trigger", item[field]))
                    break

    def condition(item: dict, here: str, depth: int) -> None:
        if isinstance(key := item.get("condition"), str):
            sites.append((here + ".condition", "condition", key))
        if depth >= AUTHORING_MAX_PATH_DEPTH:
            return
        for field in ("conditions", "and", "or", "not"):
            if field in item:
                conditions(item[field], f"{here}.{field}", depth + 1)

    def conditions(value: Any, path: str, depth: int) -> None:
        for here, item in items(value, path):
            condition(item, here, depth)

    def actions(value: Any, path: str, depth: int) -> None:
        if depth >= AUTHORING_MAX_PATH_DEPTH:
            return
        for here, item in items(value, path):
            if any(field in item for field in ("condition", "and", "or", "not")):
                condition(item, here, depth)
                continue
            conditions(item.get("if"), here + ".if", depth + 1)
            triggers(item.get("wait_for_trigger"), here + ".wait_for_trigger", depth + 1)
            for field in ("then", "else", "default", "sequence", "parallel"):
                actions(item.get(field), f"{here}.{field}", depth + 1)
            for option_path, option in items(item.get("choose"), here + ".choose"):
                conditions(option.get("conditions"), option_path + ".conditions", depth + 1)
                actions(option.get("sequence"), option_path + ".sequence", depth + 2)
            if isinstance(repeat := item.get("repeat"), dict):
                for field in ("while", "until"):
                    conditions(repeat.get(field), f"{here}.repeat.{field}", depth + 1)
                actions(repeat.get("sequence"), here + ".repeat.sequence", depth + 1)

    if isinstance(config, dict):
        for field in ("triggers", "trigger"):
            triggers(config.get(field), "config." + field, 1)
        for field in ("conditions", "condition"):
            conditions(config.get(field), "config." + field, 1)
        for field in ("actions", "action", "sequence"):
            actions(config.get(field), "config." + field, 1)
    return sites


def authoring_input_error(args: dict, domain: str) -> str | None:
    """Combine independent nesting and installed-HA schema findings safely."""
    findings = [f"{field} belongs inside config as config.{field}."
                for field in sorted(_ROOT_FIELDS.intersection(args))[:AUTHORING_MAX_ERRORS]]
    config = args.get("config")
    if not isinstance(config, dict):
        findings.append("config must be an object containing one " + domain + "; do not wrap it in a domain or resource ID.")
        findings.append(expected_shape(domain))
    else:
        if contains_tag_strings(config):
            findings.append(TAG_STRING_REFUSAL)
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
