"""Helper authoring and settings over Home Assistant's own helper machinery.

The storage-based domains in HELPER_TYPES each publish a uniform {type}/create|update|delete WS
command, which lets one set of handlers cover them. Config-flow helpers use HA's flow manager:
create_helper replays cumulative form steps, and only a supplied last step is approval-gated and
submitted. The precheck hoists only cheap structural checks, because the WS command that validates a
helper is also the write. This module never imports the dispatcher.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntry
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.loader import IntegrationNotFound, async_get_integration
from homeassistant.util import slugify

from ..const import (
    CAP_DENY,
    HELPER_FIELD_HELP_MAX,
    HELPER_STEP_HELP_MAX,
    REDACTION_SENTINEL,
    STORAGE_HELPER_ICON_TYPES,
    STORAGE_HELPER_MERGING_TYPES,
    STORAGE_HELPER_REQUEST_FIELDS,
    STORAGE_HELPER_TYPES as HELPER_TYPES,
    TEMPLATE_RECOVERY_TYPES,
)
from ..config_recovery import ConfigurationRecoveryError
from ..data import PhoenixData
from ..mesa import (
    RegistryMesaDecision,
    async_create_mesa_approval,
    evaluate_registry_action,
    fire_mesa_blocked_event,
)
from ..request_authority import AuthorityChangedError, check_request_authority, write_boundary, mark_dispatch_started
from ..template_snapshots import template_snapshot, protect_template
from ..ws_dispatch import WsCommandNotDispatchedError, WsDispatchError, async_ws_command, ws_commands_unsettled
from ..mesa_audit import note_mesa_advisory
from ..helpers import content_hash
from ..tool_input import dict_arg
from ..catalog_text import (
    diff_summary_fields as _summary, version_summary_fields as _version_summary,
)
from ..capability import effective_cap
from ..redaction import (
    _redact_entity_ids, redact_diagnostics, redact_inaccessible_entity_ids_in_text, redact_secrets_in_text,
    redact_structure,
)
from ..tool_common import (
    _CAP_FORBIDDEN_MESSAGE, _cas_conflict, _gate, _pending_or_inline, _record_version, _restore_ctx, fitted_list, redaction_sentinel_path, _tool_error, _tool_success, _tool_uncertain, _truncate, serialize_ha_schema,
)
from ..request_authority import _approved_exec_ctx
from ..policy_engine import (
    _ENTITY_ID_RE,
    Permission,
    filter_entities_for_access_key,
    is_sensitive_key,
    resolve,
    resolve_read,
    resolve_registry_access,
)
from ..access_key_store import AccessKeyRecord
from ..template_scope import HelperScopeError, template_entities, validate_template_selectors

_LOGGER = logging.getLogger(__name__)

# One message for "no such entry" and "not a helper", so the refusal is not an
# existence oracle for integration entries.
_NOT_A_HELPER_ENTRY = (
    "No helper configuration entry with that id. This tool edits HELPER entries only "
    "(the ones Home Assistant classifies as helpers, such as threshold, derivative, "
    "switch_as_x or min_max); integration entries cannot be reconfigured here."
)


_ZONE_CONFIG_FIELDS = frozenset({
    "name", "latitude", "longitude", "radius", "passive", "icon",
})
_TAG_CREATE_CONFIG_FIELDS = frozenset({"tag_id", "name", "description"})
_TAG_UPDATE_CONFIG_FIELDS = frozenset({"name", "description"})
_PERSON_CONFIG_FIELDS = frozenset({"name", "device_trackers"})
_SCOPED_STORAGE_HELPER_TYPES = frozenset({"person", "tag", "zone"})
# Optional stored fields with no default on the helpers HA merges updates into.
# A complete stored version can lack them, and an update cannot remove them.
_MERGING_OPTIONAL_FIELDS = {"zone": frozenset({"icon"}), "tag": frozenset({"name", "description"})}
_NO_STORED_ICON = (
    " {kind} have no stored icon; set_entity sets the icon shown for the entity."
)
_HELPER_CONTEXT_FINGERPRINT = "_phoenix_helper_context_fingerprint"
_HELPER_MESA_FINGERPRINT = "_phoenix_helper_mesa_fingerprint"
_HELPER_RESTORE_ID = "_phoenix_helper_restore_id"
# Set only by version and recovery restore of a complete stored version: the
# config replaces the item, rather than listing fields to change.
_HELPER_RESTORE_REPLACE = "_phoenix_helper_restore_replace"
# Readback that could not run, so it never equals a safety copy.
_UNREAD = object()

# A generated OTP cannot be created noninteractively: HA shows the secret and
# requires a current TOTP code in a later confirmation step. Keeping it out is a
# deliberate safety boundary, not a missing schema mapping.
_EXCLUDED_CONFIG_FLOW_HELPERS = frozenset({"otp"})
_MAX_HELPER_FLOW_STEPS = 8


# ---------------------------------------------------------------------------
# Helper CRUD (cap_helper_write) via in-process WS command dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _HelperMesaContext:
    """One exact inherited-MESA snapshot for a helper mutation."""

    action: str
    decisions: tuple[tuple[str, RegistryMesaDecision], ...]
    decision: str
    fingerprint: str
    warnings: tuple[str, ...]

    @property
    def profile_fingerprints(self) -> frozenset[str]:
        return frozenset(
            decision.profile_fingerprint
            for _entity_id, decision in self.decisions
            if isinstance(decision.profile_fingerprint, str)
        )


def _helper_mesa_context(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    entity_ids: list[str] | tuple[str, ...],
    *,
    action: str,
    service_data: dict[str, Any] | None = None,
    session_id: str,
) -> _HelperMesaContext | tuple[dict, str, str]:
    """Resolve helper.<action> for every exact entity, failing closed."""
    entities = tuple(sorted(set(entity_ids)))
    if not entities:
        return (
            _tool_error("MESA could not resolve an entity for this helper; nothing changed."),
            "denied",
            f"helper:{action}",
        )
    decisions: list[tuple[str, RegistryMesaDecision]] = []
    try:
        for entity_id in entities:
            decisions.append((
                entity_id,
                evaluate_registry_action(
                    data,
                    access_key,
                    entity_id,
                    action=action,
                    registry_domain="helper",
                    service_data=dict(service_data or {}),
                    session_id=session_id,
                ),
            ))
    except Exception:  # a safety resolver failure must block
        _LOGGER.exception("MESA helper %s evaluation failed for %s", action, entities)
        return (
            _tool_error("MESA safety evaluation failed; no helper change was made."),
            "denied",
            f"helper:{action}",
        )
    aggregate = (
        "deny" if any(item.decision == "deny" for _eid, item in decisions)
        else "confirm" if any(item.decision == "confirm" for _eid, item in decisions)
        else "allow"
    )
    warnings = tuple(
        warning
        for _entity_id, decision in decisions
        for warning in decision.warnings
    )
    fingerprint_doc = {
        "action": action,
        "entities": [
            {
                "entity_id": entity_id,
                "decision": decision.decision,
                "rule": decision.rule,
                "reason": decision.reason,
                "effective_rule": decision.effective_rule,
                "profile_fingerprint": decision.profile_fingerprint,
            }
            for entity_id, decision in decisions
        ],
    }
    fingerprint = hashlib.sha256(json.dumps(
        fingerprint_doc, sort_keys=True, separators=(",", ":"), default=str
    ).encode()).hexdigest()
    return _HelperMesaContext(
        action=action,
        decisions=tuple(decisions),
        decision=aggregate,
        fingerprint=fingerprint,
        warnings=warnings,
    )


def _helper_mesa_preview(context: _HelperMesaContext, *, admin: bool = False) -> dict[str, Any]:
    """The decision for an agent's refusal, or with ``admin`` for an approval diff."""
    return {
        "decision": context.decision,
        "action": f"helper.{context.action}",
        "confirm_entities": [
            entity_id
            for entity_id, decision in context.decisions
            if decision.decision == "confirm"
        ],
        "allowed_entities": [
            entity_id
            for entity_id, decision in context.decisions
            if decision.decision == "allow"
        ],
        "blocked": [
            {"entity_id": entity_id, "rule": decision.rule}
            for entity_id, decision in context.decisions
            if decision.decision == "deny"
        ],
        "entities": [
            {
                "entity_id": entity_id,
                "decision": decision.decision,
                "rule": decision.rule,
                "reason": decision.reason,
                "effective_rule": decision.admin_rule if admin else decision.effective_rule,
            }
            for entity_id, decision in context.decisions
        ],
        "warnings": list(context.warnings),
        "fingerprint": context.fingerprint[:16],
    }


def _helper_mesa_error(
    context: _HelperMesaContext,
    *,
    changed: bool = False,
) -> tuple[dict, str, str]:
    message = (
        "MESA protection changed after this helper request was reviewed; nothing changed."
        if changed
        else "MESA blocked this helper change."
    )
    return (
        _tool_error(json.dumps({
            "error": message,
            "mesa": _helper_mesa_preview(context),
        }, default=str)),
        "denied",
        f"helper:{context.action}",
    )


def _fire_helper_mesa_blocks(
    hass: HomeAssistant,
    access_key: AccessKeyRecord,
    context: _HelperMesaContext,
) -> None:
    blocked = [
        (entity_id, decision.rule, decision.reason)
        for entity_id, decision in context.decisions
        if decision.decision == "deny"
    ]
    if blocked:
        fire_mesa_blocked_event(hass, access_key, blocked)


def _helper_mesa_diff(diff: dict, context: _HelperMesaContext) -> dict:
    preview = diff.setdefault("preview", {})
    if isinstance(preview, dict):
        preview["mesa"] = _helper_mesa_preview(context, admin=True)
    return diff


async def _helper_mesa_approval_gate(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    *,
    context: _HelperMesaContext,
    tool_name: str,
    request_id: str,
    client_ip: str | None,
    diff_builder: Any,
) -> tuple[dict, tuple[dict, str, str] | None]:
    """Merge inherited MESA with the ordinary helper capability approval."""
    if context.decision == "deny":
        _fire_helper_mesa_blocks(hass, access_key, context)
        return args, _helper_mesa_error(context)
    approval_args = {**args, _HELPER_MESA_FINGERPRINT: context.fingerprint}

    async def _diff() -> dict:
        value = diff_builder()
        if hasattr(value, "__await__"):
            value = await value
        return _helper_mesa_diff(value, context)

    blocked = await _gate(
        "cap_helper_write",
        access_key,
        hass,
        data,
        tool_name=tool_name,
        args=approval_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=_diff,
    )
    if blocked is not None:
        return approval_args, blocked
    if context.decision == "confirm":
        approval = await async_create_mesa_approval(
            hass,
            data,
            access_key,
            args=approval_args,
            diff=await _diff(),
            request_id=request_id,
            client_ip=client_ip,
            tool_name=tool_name,
        )
        return approval_args, await _pending_or_inline(
            hass, data, access_key, approval
        )
    return approval_args, None


def _enforce_helper_mesa_context(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    entity_ids: list[str] | tuple[str, ...],
    *,
    action: str,
    service_data: dict[str, Any] | None = None,
    session_id: str,
) -> _HelperMesaContext | tuple[dict, str, str]:
    context = _helper_mesa_context(
        data,
        access_key,
        entity_ids,
        action=action,
        service_data=service_data,
        session_id=session_id,
    )
    if isinstance(context, tuple):
        return context
    saved = args.get(_HELPER_MESA_FINGERPRINT)
    if isinstance(saved, str) and saved != context.fingerprint:
        return _helper_mesa_error(context, changed=True)
    restoring = _restore_ctx.get() is not None
    confirmed = (
        _approved_exec_ctx.get()
        and isinstance(saved, str)
        and saved == context.fingerprint
    ) or restoring
    if context.decision == "deny" or (
        context.decision == "confirm" and not confirmed
    ):
        _fire_helper_mesa_blocks(hass, access_key, context)
        return _helper_mesa_error(context)
    note_mesa_advisory(context.warnings)
    return context


# HA-COUPLING POINT. Mirrors tag._create_entry and EntityRegistry.async_get_or_create
# (HA 2026.9): the tag's unique_id is its tag_id, but its entity ID comes from the name.
def _predict_tag_entity_id(hass: HomeAssistant, tag_id: str, name: str | None) -> str:
    """The entity ID Home Assistant will give a tag created with this id and name.

    An existing or freely restorable ID is kept; otherwise the slugified name or tag_id takes the
    next free ID.
    """
    # A deleted entry whose ID is now taken renames from its old name and area, which is not
    # predicted; the post-create MESA check then removes the tag and refuses, as for any wrong
    # prediction.
    registry = er.async_get(hass)
    if existing := registry.async_get_entity_id("tag", "tag", tag_id):
        return existing
    deleted = registry.deleted_entities.get(("tag", "tag", tag_id))
    if (deleted is not None and registry.async_get(deleted.entity_id) is None
            and hass.states.async_available(deleted.entity_id)):
        return str(deleted.entity_id)
    return str(registry.async_get_available_entity_id("tag", slugify(name) if name else tag_id))


def _restored_helper_entity_id(hass: HomeAssistant, helper_type: str, storage_id: str) -> str:
    """The entity ID a storage helper recreated under storage_id receives.

    HA reuses the deleted registry entry's ID and customizations while that ID is free, else derives
    one from the storage ID.
    """
    registry = er.async_get(hass)
    deleted = registry.deleted_entities.get((helper_type, helper_type, storage_id))
    if (deleted is not None and deleted.entity_id not in registry.entities
            and hass.states.async_available(deleted.entity_id)):
        return deleted.entity_id
    return f"{helper_type}.{slugify(storage_id)}"


def _helper_create_mesa_entity(
    helper_type: str, args: dict, hass: HomeAssistant
) -> str:
    if helper_type == "template":
        steps = args.get("flow_steps")
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            step_id = step.get("step_id")
            step_data = step.get("data")
            selected = (
                step_data.get("next_step_id")
                if step_id == "user" and isinstance(step_data, dict)
                else step_id
            )
            if isinstance(selected, str) and selected in TEMPLATE_RECOVERY_TYPES:
                helper_type = selected
                break
    config = args.get("config")
    if helper_type == "tag" and isinstance(config, dict) and isinstance(config.get("tag_id"), str) and config["tag_id"]:
        name = config.get("name")
        return _predict_tag_entity_id(hass, config["tag_id"], name if isinstance(name, str) and name else None)
    restore_id = args.get(_HELPER_RESTORE_ID)
    if isinstance(restore_id, str) and restore_id.strip():
        return _restored_helper_entity_id(hass, helper_type, restore_id)
    suggestion: str | None = None
    if isinstance(config, dict) and isinstance(config.get("name"), str):
        suggestion = config["name"]
    if suggestion is None:
        steps = args.get("flow_steps")
        if isinstance(steps, list):
            for step in reversed(steps):
                step_data = step.get("data") if isinstance(step, dict) else None
                if isinstance(step_data, dict) and isinstance(step_data.get("name"), str):
                    suggestion = step_data["name"]
                    break
    if suggestion is not None:
        base = slugify(suggestion) or "__phoenix_helper_create__"
        if helper_type in HELPER_TYPES and helper_type != "tag":
            used = {
                entry.unique_id
                for entry in er.async_get(hass).entities.values()
                if entry.domain == helper_type and entry.platform == helper_type
            }
            proposal = base
            attempt = 1
            while proposal in used:
                attempt += 1
                proposal = f"{base}_{attempt}"
            base = proposal
        return f"{helper_type}.{base}"
    return f"{helper_type}.__phoenix_helper_create__"


def _valid_helper_type(helper_type: Any) -> bool:
    return isinstance(helper_type, str) and helper_type in HELPER_TYPES


def _storage_items(helper_type: str, result: Any) -> list[dict[str, Any]] | None:
    """Normalize a storage collection list without admitting YAML people."""
    if helper_type == "person":
        if not isinstance(result, dict) or not isinstance(result.get("storage"), list):
            return None
        result = result["storage"]
    if not isinstance(result, list):
        return None
    return [item for item in result if isinstance(item, dict)]


def _person_trackers(item: dict[str, Any]) -> tuple[str, ...] | None:
    """Return a validated, duplicate-free person tracker relationship."""
    value = item.get("device_trackers", [])
    if not isinstance(value, list):
        return None
    trackers = tuple(value)
    if any(
        not isinstance(entity_id, str)
        or not _ENTITY_ID_RE.fullmatch(entity_id)
        or not entity_id.startswith("device_tracker.")
        for entity_id in trackers
    ) or len(set(trackers)) != len(trackers):
        return None
    return trackers


def _person_relationships_accessible(
    item: dict[str, Any], access_key: AccessKeyRecord, hass: HomeAssistant, *, write: bool,
) -> bool:
    trackers = _person_trackers(item)
    if trackers is None:
        return False
    allowed = {Permission.WRITE} if write else {Permission.READ, Permission.WRITE}
    return all(
        resolve_registry_access(entity_id, access_key, hass) in allowed
        for entity_id in trackers
    )


def _public_helper_config(helper_type: str, item: dict[str, Any]) -> dict[str, Any]:
    """Project stored helper data into the MCP/version-history privacy boundary."""
    if helper_type != "person":
        return dict(item)
    trackers = _person_trackers(item)
    return {
        **({"id": item["id"]} if isinstance(item.get("id"), str) else {}),
        **({"name": item["name"]} if isinstance(item.get("name"), str) else {}),
        "device_trackers": list(trackers or ()),
        "has_user_link": bool(item.get("user_id")),
        "has_picture": bool(item.get("picture")),
    }


async def _is_config_flow_helper(hass: HomeAssistant, helper_type: Any) -> bool:
    """Whether HA classifies this domain as a creatable helper config flow."""
    if (
        not isinstance(helper_type, str)
        or not helper_type.strip()
        or helper_type in HELPER_TYPES
        or helper_type in _EXCLUDED_CONFIG_FLOW_HELPERS
    ):
        return False
    try:
        integration = await async_get_integration(hass, helper_type)
    except (IntegrationNotFound, ValueError):
        return False
    return (
        integration.integration_type == "helper"
        and integration.manifest.get("config_flow") is True
    )


def _helper_flow_steps_error(steps: Any) -> str | None:
    """Cheap structural validation before a helper creation flow is opened."""
    if not isinstance(steps, list):
        return "flow_steps must be an array. Use an empty array to read the first form."
    if len(steps) > _MAX_HELPER_FLOW_STEPS:
        return f"flow_steps may contain at most {_MAX_HELPER_FLOW_STEPS} steps."
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"flow_steps[{index}] must be an object."
        if set(step) != {"step_id", "data"}:
            return f"flow_steps[{index}] must contain only step_id and data."
        if not isinstance(step.get("step_id"), str) or not step["step_id"].strip():
            return f"flow_steps[{index}].step_id must be a non-empty string."
        if not isinstance(step.get("data"), dict):
            return f"flow_steps[{index}].data must be an object."
    return None


def _flow_step_body(helper_type: str, result: Any) -> dict[str, Any] | None:
    """Project a form or the qualified Template type-selection menu."""
    if result.get("type") == FlowResultType.MENU:
        if helper_type != "template" or result.get("step_id") != "user":
            return None
        offered = result.get("menu_options")
        if not isinstance(offered, (list, dict)):
            return None
        choices = sorted(
            choice for choice in TEMPLATE_RECOVERY_TYPES
            if choice in offered and isinstance(choice, str)
        )
        if not choices:
            return None
        return {
            "status": "needs_input", "helper_type": helper_type,
            "step": {"step_id": "user", "last_step": False, "menu_options": choices},
            "note": "Append {step_id: 'user', data: {next_step_id: '<choice>'}} to flow_steps, then call create_helper again. The listed Template types are available in this Home Assistant release.",
        }
    if result.get("type") != FlowResultType.FORM:
        return None
    schema = _options_schema_json(result)
    if schema is None:
        return None
    return {
        "status": "needs_input",
        "helper_type": helper_type,
        "step": {
            "step_id": result.get("step_id"),
            "last_step": result.get("last_step"),
            "schema": schema,
            "errors": result.get("errors") or {},
            "description_placeholders": result.get("description_placeholders") or {},
        },
        "note": (
            "Append this step_id and its data to flow_steps, then call create_helper "
            "again. Phoenix replays and closes unfinished flows; approval is requested "
            "only when the final form is supplied."
        ),
    }


def _rejected_step_body(body: dict[str, Any], index: int, errors: dict[str, Any]) -> dict[str, Any]:
    """The form HA rejected, with its errors, telling the caller to replace that step."""
    rejected = copy.deepcopy(body)
    rejected["step"]["errors"] = errors
    rejected["note"] = (
        f"Home Assistant rejected flow_steps[{index}]. Keep the earlier steps, replace "
        "this step's data to fix the errors, and call create_helper again."
    )
    return rejected


def _invalid_form_errors(err: vol.Invalid) -> dict[str, Any]:
    """Per-field messages for a schema rejection, keyed as HA's form errors are."""
    mapped = getattr(err, "schema_errors", None)
    if isinstance(mapped, dict) and mapped:
        return {key: "; ".join(map(str, value)) if isinstance(value, list) else value
                for key, value in mapped.items()}
    errors: dict[str, Any] = {}
    for item in err.errors if isinstance(err, vol.MultipleInvalid) else [err]:
        key = str(item.path[0]) if item.path else "base"
        errors.setdefault(key, item.error_message or str(item))
    return errors


async def _with_field_help(
    hass: HomeAssistant, domain: str, category: str, step_id: Any, schema: Any,
) -> str | None:
    """Add HA's own English help to each field and return the step's description.

    A field gets its data_description, else its label; section fields read sections.<name>. Static
    integration strings, so no scope or privacy boundary moves.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.translation import async_get_translations
    if not isinstance(step_id, str):
        return None
    try:
        strings = await async_get_translations(hass, "en", category, [domain])
    except Exception:  # help text is optional
        _LOGGER.debug("No %s translations for %s", category, domain, exc_info=True)
        return None
    step = f"component.{domain}.{category}.step.{step_id}."

    def annotate(fields: list, prefix: str) -> None:
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                continue
            name = field["name"]
            text = strings.get(f"{prefix}data_description.{name}") or strings.get(f"{prefix}data.{name}")
            if isinstance(text, str) and text.strip():
                field["help"] = text.strip()[:HELPER_FIELD_HELP_MAX]
            if isinstance(field.get("schema"), list):
                annotate(field["schema"], f"{step}sections.{name}.")

    if isinstance(schema, list):
        annotate(schema, step)
    description = strings.get(f"{step}description")
    return description.strip()[:HELPER_STEP_HELP_MAX] if isinstance(description, str) and description.strip() else None


def _creation_menu_error(body: dict, supplied: dict) -> str | None:
    """Reject unlisted menu targets and extra fields before HA dispatch."""
    if "menu_options" not in body["step"]:
        return None
    values = supplied["data"]
    if (set(values) != {"next_step_id"} or not isinstance(values.get("next_step_id"), str)
            or values["next_step_id"] not in body["step"]["menu_options"]):
        return "Choose one listed menu option using only data.next_step_id."
    return None


async def _abort_create_flow(hass: HomeAssistant, flow_id: str | None) -> None:
    """Close an unfinished helper creation flow without leaking a UI dialog."""
    if not flow_id:
        return
    try:
        hass.config_entries.flow.async_abort(flow_id)
    except Exception:  # already-finished and already-gone are fine
        _LOGGER.debug("Could not abort helper creation flow %s", flow_id, exc_info=True)


async def _prepare_config_flow_helper(
    hass: HomeAssistant,
    helper_type: str,
    steps: list[dict[str, Any]],
    access_key: AccessKeyRecord,
) -> tuple[str, Any]:
    """Replay a helper flow without ever submitting its final form.

    Returns ("form", body), ("ready", None) for a schema-valid final form, or ("error", message).
    Every flow opened here is aborted in the finally block.
    """
    flow_id = None

    async def _described(body: dict[str, Any]) -> dict[str, Any]:
        if described := await _with_field_help(
                hass, helper_type, "config", body["step"].get("step_id"), body["step"].get("schema")):
            body["step"]["help"] = described
        return body

    try:
        result = await hass.config_entries.flow.async_init(
            helper_type, context={"source": SOURCE_USER}
        )
        flow_id = result.get("flow_id")
        if result.get("type") == FlowResultType.ABORT:
            return "error", f"Home Assistant refused this helper flow: {result.get('reason')}."

        for index, supplied in enumerate(steps):
            body = _flow_step_body(helper_type, result)
            if body is None:
                return "error", "This helper's creation flow is not a supported form flow."
            current = body["step"]
            if supplied["step_id"] != current["step_id"]:
                return "error", (
                    f"flow_steps[{index}].step_id must be '{current['step_id']}', "
                    f"not '{supplied['step_id']}'."
                )
            if menu_error := _creation_menu_error(body, supplied):
                return "error", menu_error
            denied = _unwritable_option_entities(
                supplied["data"], access_key, hass,
                template_type=supplied["step_id"] if helper_type == "template" and supplied["step_id"] != "user" else None,
            )
            if denied:
                return "denied", denied

            if current["last_step"] is True:
                if index != len(steps) - 1:
                    return "error", "The final helper form must be the last item in flow_steps."
                schema = result.get("data_schema")
                try:
                    if schema is not None:
                        schema(dict(supplied["data"]))
                except vol.Invalid as err:
                    return "form", await _described(_rejected_step_body(body, index, _invalid_form_errors(err)))
                return "ready", None

            # Submitting a form whose last_step is unknown could create an entry
            # during this side-effect-free preflight. Fail closed. HA's schema
            # config flows, including history_stats and mold_indicator, mark it.
            if current["last_step"] is not False:
                return "error", (
                    "This helper flow does not identify its final form, so Phoenix "
                    "cannot preflight it without risking an unapproved creation."
                )
            try:
                result = await hass.config_entries.flow.async_configure(
                    flow_id, dict(supplied["data"])
                )
            except vol.Invalid as err:
                return "form", await _described(_rejected_step_body(body, index, _invalid_form_errors(err)))
            if result.get("type") == FlowResultType.ABORT:
                return "error", f"Home Assistant refused this helper flow: {result.get('reason')}."
            if result.get("type") == FlowResultType.CREATE_ENTRY:
                # last_step=False promises this cannot happen. If an integration
                # breaks that contract the entry already exists: it is removed and
                # reported rather than letting the preflight pass as clean.
                entry = result.get("result")
                mark_dispatch_started()  # the entry was created: this refusal follows a write
                if entry is not None:
                    await hass.config_entries.async_remove(entry.entry_id)
                return "error", "This helper flow finished before its declared final form."
            if result.get("errors"):
                error_body = _flow_step_body(helper_type, result)
                if error_body is None:
                    return "error", "This helper's rejected form could not be described."
                return "form", await _described(_rejected_step_body(error_body, index, error_body["step"]["errors"]))

        body = _flow_step_body(helper_type, result)
        if body is None:
            return "error", "This helper's creation flow is not a supported form flow."
        return "form", await _described(body)
    except HelperScopeError as exc:
        return "scope_error", str(exc)
    except Exception:  # integration flow failures become clean tool errors
        _LOGGER.exception("Could not preflight helper creation flow for %s", helper_type)
        return "error", "Could not open or validate this helper's creation flow."
    finally:
        await _abort_create_flow(hass, flow_id)


def _resolve_helper_entity_id(
    hass: HomeAssistant, helper_type: str, helper_id: str, *, platform: str | None = None,
) -> str | None:
    """Map a helper id back to its entity_id via the registry, or None; the existence check."""
    # list_helpers exposes entry.unique_id as the helper_id, which is unique only per (domain,
    # platform). The lookup names the owner (the helper type for storage helpers, whose collections
    # register under their own domain, or "template"), so another integration's entity with the same
    # unique_id never resolves.
    return er.async_get(hass).async_get_entity_id(helper_type, platform or helper_type, helper_id)


def _template_helper_entry(hass: HomeAssistant, helper_type: str, helper_id: str) -> Any | None:
    """The Template config entry behind a Template helper id, or None.

    Both the entity's platform and its owning entry must be Template, so another integration's
    entity can never select its entry.
    """
    entity_id = _resolve_helper_entity_id(hass, helper_type, helper_id, platform="template")
    registry_entry = er.async_get(hass).async_get(entity_id) if entity_id else None
    config_entry_id = registry_entry.config_entry_id if registry_entry else None
    entry = hass.config_entries.async_get_entry(config_entry_id) if config_entry_id else None
    if (registry_entry is not None and registry_entry.platform == "template"
            and entry is not None and entry.domain == "template"):
        return entry
    return None


async def _tool_list_helpers(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list accessible helper entities with their editable helper id."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_helpers"
    type_filter = args.get("helper_type")
    registry = er.async_get(hass)
    scoped_storage_items: dict[str, dict[str, dict[str, Any]]] = {}
    for helper_type in _SCOPED_STORAGE_HELPER_TYPES:
        if type_filter not in (None, helper_type):
            continue
        if helper_type not in hass.config.components:
            continue
        try:
            items = await async_ws_command(hass, f"{helper_type}/list", {})
        except WsDispatchError:
            if type_filter == helper_type:
                return (
                    _tool_error(
                        f"{helper_type.capitalize()} helper storage is unavailable."
                    ),
                    "invalid_request",
                    "list_helpers",
                )
            continue
        storage_items = _storage_items(helper_type, items)
        if storage_items is None:
            if type_filter == helper_type:
                return (
                    _tool_error(
                        f"{helper_type.capitalize()} helper storage is unavailable."
                    ),
                    "invalid_request",
                    "list_helpers",
                )
            continue
        scoped_storage_items[helper_type] = {
            row["id"]: row for row in storage_items
            if isinstance(row.get("id"), str)
        }
    helpers: list[dict] = []
    for e in filter_entities_for_access_key(hass.states.async_all(), access_key, hass):
        domain = e["entity_id"].split(".")[0]
        registry_entry = registry.async_get(e["entity_id"])
        is_template_helper = (
            domain in TEMPLATE_RECOVERY_TYPES
            and registry_entry is not None
            and registry_entry.platform == "template"
        )
        if domain not in HELPER_TYPES and not is_template_helper:
            continue
        if type_filter and domain != type_filter:
            continue
        entry = registry_entry
        stored_item = (
            scoped_storage_items.get(domain, {}).get(entry.unique_id)
            if entry is not None else None
        )
        if domain in _SCOPED_STORAGE_HELPER_TYPES and stored_item is None:
            continue
        if domain == "person" and (
            stored_item is None
            or not _person_relationships_accessible(
                stored_item, access_key, hass, write=False
            )
        ):
            continue
        helper = {
            "entity_id": e["entity_id"],
            "helper_type": domain,
            "name": e.get("attributes", {}).get("friendly_name"),
            "helper_id": entry.unique_id if entry is not None else None,
        }
        if domain == "person" and stored_item is not None:
            public = _public_helper_config(domain, stored_item)
            helper.update({
                "device_trackers": public["device_trackers"],
                "tracker_count": len(public["device_trackers"]),
                "has_user_link": public["has_user_link"],
            })
        helpers.append(helper)
    helpers.sort(key=lambda h: h["entity_id"])
    return _tool_success(json.dumps(fitted_list(helpers, "helpers"), default=str)), "allowed", "list_helpers"


def _helper_icon_error(helper_type: str, icon: Any, *, editing: bool) -> str | None:
    """Refuse an icon value Home Assistant would reject, before any approval.

    Null removes a stored icon from helpers HA rebuilds on update. A zone merges updates, so its
    icon can only be replaced; creation has nothing to remove.
    """
    if icon is None:
        if not editing:
            return "icon cannot be null when creating a helper; omit it to use the default icon."
        if helper_type in STORAGE_HELPER_MERGING_TYPES:
            return (
                "Home Assistant cannot remove a zone's stored icon, only replace it. Set another "
                "icon, or use set_entity to override the icon shown for the zone's entity."
            )
        return None
    try:
        if not isinstance(icon, str):
            raise vol.Invalid("not a string")
        cv.icon(icon)
    except vol.Invalid:
        clear = "" if helper_type in STORAGE_HELPER_MERGING_TYPES or not editing else ", or null to remove it"
        return f'icon must be an icon name such as "mdi:sofa"{clear}.'
    return None


def _helper_write_precheck(args: dict, tool_name: str, *, require_id: bool) -> tuple[dict, str, str] | None:
    """Pre-gate validation for helper writes; None means OK to proceed.

    Checks helper_type, helper_id and config shape. HA's per-type validation is the write itself, so
    the executor re-validates at apply time.
    """
    if not _valid_helper_type(args.get("helper_type")):
        return _tool_error(f"helper_type must be one of: {', '.join(sorted(HELPER_TYPES))}."), "invalid_request", tool_name
    if require_id and not str(args.get("helper_id") or "").strip():
        return _tool_error("helper_id is required."), "invalid_request", tool_name
    config = args.get("config")
    if require_id:
        if not isinstance(config, dict):
            return _tool_error("config must be an object."), "invalid_request", tool_name
    elif not isinstance(config, dict) or not config:
        return _tool_error("config must be a non-empty object (at least 'name')."), "invalid_request", tool_name
    reserved = STORAGE_HELPER_REQUEST_FIELDS
    if args["helper_type"] == "tag" and not require_id:
        reserved = reserved - {"tag_id"}
    if set(config) & reserved:
        return (
            _tool_error(
                "config must not contain WebSocket request fields or helper update target fields. "
                "Use helper_id to select an existing helper."
            ),
            "invalid_request",
            tool_name,
        )
    if "icon" in config and args["helper_type"] in STORAGE_HELPER_ICON_TYPES and (
        icon_error := _helper_icon_error(args["helper_type"], config["icon"], editing=require_id)
    ):
        return _tool_error(icon_error), "invalid_request", tool_name
    if args.get("helper_type") == "zone" and isinstance(config, dict):
        unknown = sorted(set(config) - _ZONE_CONFIG_FIELDS)
        if unknown:
            return (
                _tool_error(
                    "Unknown zone configuration fields: "
                    f"{', '.join(unknown)}. Allowed fields are: "
                    f"{', '.join(sorted(_ZONE_CONFIG_FIELDS))}."
                ),
                "invalid_request",
                tool_name,
            )
    if args.get("helper_type") == "tag" and isinstance(config, dict):
        allowed = _TAG_UPDATE_CONFIG_FIELDS if require_id else _TAG_CREATE_CONFIG_FIELDS
        unknown = sorted(set(config) - allowed)
        if unknown:
            return (
                _tool_error(
                    "Unknown tag configuration fields: "
                    f"{', '.join(unknown)}. Allowed fields are: "
                    f"{', '.join(sorted(allowed))}."
                    + (_NO_STORED_ICON.format(kind="Tags") if "icon" in unknown else "")
                ),
                "invalid_request",
                tool_name,
            )
        if tool_name == "edit_helper" and not config:
            return (
                _tool_error("Tag edit config must include name or description."),
                "invalid_request",
                tool_name,
            )
        if not require_id and (
            not isinstance(config.get("tag_id"), str)
            or not config["tag_id"].strip()
        ):
            return (
                _tool_error("Tag creation requires a non-empty string tag_id."),
                "invalid_request",
                tool_name,
            )
        if "name" in config and (
            not isinstance(config["name"], str) or not config["name"].strip()
        ):
            return (
                _tool_error("Tag name must be a non-empty string."),
                "invalid_request",
                tool_name,
            )
        if "description" in config and not isinstance(config["description"], str):
            return (
                _tool_error("Tag description must be a string."),
                "invalid_request",
                tool_name,
            )
    if args.get("helper_type") == "person" and isinstance(config, dict):
        unknown = sorted(set(config) - _PERSON_CONFIG_FIELDS)
        if unknown:
            return (
                _tool_error(
                    "Unknown person configuration fields: "
                    f"{', '.join(unknown)}. Phoenix accepts only name and "
                    "device_trackers; user links and pictures remain private "
                    "and must be managed in Home Assistant."
                    + (_NO_STORED_ICON.format(kind="People") if "icon" in unknown else "")
                ),
                "invalid_request",
                tool_name,
            )
        if not require_id and (
            not isinstance(config.get("name"), str) or not config["name"].strip()
        ):
            return (
                _tool_error("Person creation requires a non-empty string name."),
                "invalid_request",
                tool_name,
            )
        if "name" in config and (
            not isinstance(config["name"], str) or not config["name"].strip()
        ):
            return (
                _tool_error("Person name must be a non-empty string."),
                "invalid_request",
                tool_name,
            )
        if tool_name == "edit_helper" and not config:
            return (
                _tool_error("Person edit config must include name or device_trackers."),
                "invalid_request",
                tool_name,
            )
        if "device_trackers" in config and _person_trackers(config) is None:
            return (
                _tool_error(
                    "device_trackers must be a duplicate-free array of "
                    "device_tracker entity IDs."
                ),
                "invalid_request",
                tool_name,
            )
    return None


def _scoped_helper_create_scope_error(
    helper_type: str, access_key: AccessKeyRecord
) -> str | None:
    """Require inherited write scope before creating a scoped helper entity.

    A new entity has no entity-specific grant, so a green domain grant or pass-through scope keeps
    it manageable once it exists.
    """
    # The executor still verifies the concrete entity, because an entity-specific red rule may
    # override the inherited grant once the final entity_id is known.
    if access_key.pass_through:
        return None
    domain = access_key.permissions.domains.get(helper_type)
    if domain is not None and domain.state == "GREEN":
        return None
    return (
        f"Creating a {helper_type} requires write access to the entire "
        f"{helper_type} domain, because the new {helper_type} does not have an "
        "entity ID that can be scoped yet."
    )


async def _tag_create_absence_error(
    args: dict, hass: HomeAssistant, tool_name: str
) -> tuple[dict, str, str] | None:
    """Require an authoritative absent tag id before create and approval."""
    if args.get("helper_type") != "tag":
        return None
    tag_id = str(dict_arg(args.get("config")).get("tag_id") or "").strip()
    try:
        items = await async_ws_command(hass, "tag/list", {})
    except WsDispatchError:
        return (
            _tool_error("Tag helper storage is unavailable; nothing changed."),
            "invalid_request",
            tool_name,
        )
    if not isinstance(items, list):
        return (
            _tool_error("Tag helper storage is unavailable; nothing changed."),
            "invalid_request",
            tool_name,
        )
    if any(
        isinstance(row, dict) and row.get("id") == tag_id for row in items
    ):
        return (
            _tool_error("This tag_id is unavailable."),
            "invalid_request",
            tool_name,
        )
    return None


def _person_relationship_scope_error(
    config: dict[str, Any], access_key: AccessKeyRecord, hass: HomeAssistant,
) -> str | None:
    """Require WRITE for every person tracker without identifying hidden links."""
    if not _person_relationships_accessible(config, access_key, hass, write=True):
        return (
            "This person references an unavailable or out-of-scope device "
            "tracker. Write access to every linked tracker is required; nothing changed."
        )
    return None


def _scoped_helper_approval_context(
    helper_type: str, entity_id: str, item: dict[str, Any]
) -> dict[str, Any]:
    """Return the stable configuration that an approval actually reviews."""
    if helper_type == "tag":
        item = {
            key: item[key] for key in ("id", "name", "description") if key in item
        }
    elif helper_type == "person":
        # The returned value is immediately hashed into the approval arguments.
        # Keeping the raw private binding inside the hash detects user-link and
        # picture drift while never exposing either value in a diff or response.
        item = {
            key: item[key]
            for key in ("id", "name", "user_id", "device_trackers", "picture")
            if key in item
        }
    return {"entity_id": entity_id, "config": item}


async def _storage_helper_rows(hass: HomeAssistant, helper_type: str) -> list[dict[str, Any]] | None:
    """One helper type's stored items, or None when storage cannot be read."""
    try:
        return _storage_items(helper_type, await async_ws_command(hass, f"{helper_type}/list", {}))
    except WsDispatchError:
        return None


async def _stored_helper_item(
    hass: HomeAssistant, access_key: AccessKeyRecord, helper_type: str, helper_id: str,
    entity_id: str, tool_name: str,
) -> tuple[dict[str, Any] | None, tuple[dict, str, str] | None]:
    """Return a registered helper's stored item, or the refusal that explains its absence.

    The storage list proves membership in the editable collection; a list failure is unavailable,
    never absence. Only a key that can read the entity is told it is YAML.
    """
    # YAML and storage items share one id space, so a registered entity missing from storage that HA
    # marks not editable is a YAML helper.
    rows = await _storage_helper_rows(hass, helper_type)
    if rows is None:
        return None, (
            _tool_error(f"The {helper_type} helper storage is unavailable; nothing changed."),
            "invalid_request", tool_name,
        )
    item = next((row for row in rows if row.get("id") == helper_id), None)
    if item is not None:
        return item, None
    state = hass.states.get(entity_id)
    if (state is not None and state.attributes.get("editable") is False
            and resolve(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)):
        return None, (
            _tool_error(
                "This helper is defined in YAML, which helper storage cannot change. "
                "Edit or remove it in the YAML configuration instead; nothing changed."
            ),
            "invalid_request", tool_name,
        )
    return None, (_tool_error("Helper not found."), "not_found", f"helper:{helper_type}:{helper_id}")


async def _scoped_helper_context(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, tool_name: str,
) -> tuple[dict[str, Any] | None, tuple[dict, str, str] | None]:
    """Resolve one writable scoped storage helper and its stored config."""
    helper_type = str(args.get("helper_type") or "")
    helper_id = str(args.get("helper_id") or "").strip()
    entity_id = _resolve_helper_entity_id(hass, helper_type, helper_id)
    if entity_id is None or resolve(entity_id, access_key, hass) != Permission.WRITE:
        return None, (
            _tool_error("Helper not found."), "not_found",
            f"helper:{helper_type}:{helper_id}",
        )
    item, missing = await _stored_helper_item(hass, access_key, helper_type, helper_id, entity_id, tool_name)
    if missing is not None:
        return None, missing
    assert item is not None
    if helper_type == "person" and not _person_relationships_accessible(
        item, access_key, hass, write=True
    ):
        return None, (
            _tool_error(
                "Helper not found or one of its relationships is outside this "
                "access key's write scope."
            ),
            "not_found",
            f"helper:{helper_type}:{helper_id}",
        )
    return {"entity_id": entity_id, "config": item}, None


async def _bind_scoped_helper_context(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, tool_name: str,
) -> tuple[dict, tuple[dict, str, str] | None]:
    """Attach an exact stable snapshot to edit/delete approval arguments."""
    helper_type = str(args.get("helper_type") or "")
    if helper_type not in _SCOPED_STORAGE_HELPER_TYPES:
        return args, None
    context, error = await _scoped_helper_context(args, access_key, hass, tool_name)
    if error is not None:
        return args, error
    assert context is not None
    bound = dict(args)
    bound[_HELPER_CONTEXT_FINGERPRINT] = content_hash(
        _scoped_helper_approval_context(
            helper_type, context["entity_id"], context["config"]
        )
    )
    return bound, None


async def _revalidate_scoped_helper_context(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, tool_name: str,
) -> tuple[dict[str, Any] | None, tuple[dict, str, str] | None]:
    """Re-check helper scope, storage ownership, and any approval snapshot."""
    helper_type = str(args.get("helper_type") or "")
    if helper_type not in _SCOPED_STORAGE_HELPER_TYPES:
        return None, None
    context, error = await _scoped_helper_context(args, access_key, hass, tool_name)
    if error is not None:
        return None, error
    assert context is not None
    saved = args.get(_HELPER_CONTEXT_FINGERPRINT)
    current = _scoped_helper_approval_context(
        helper_type, context["entity_id"], context["config"]
    )
    if saved is not None and content_hash(current) != saved:
        return None, (
            _tool_error(
                f"This {helper_type} changed after the request was reviewed; "
                "nothing changed. "
                "Read it again and submit a new request."
            ),
            "invalid_request",
            tool_name,
        )
    return context, None


async def _duplicate_helper_name_error(
    hass: HomeAssistant, access_key: AccessKeyRecord, helper_type: str, config: dict,
) -> str | None:
    """Refuse a second helper with the same name, which HA would quietly suffix.

    Only a match this key can read refuses, so a name cannot probe outside its scope. Scoped types
    (zone, tag, person) skip the check.
    """
    name = config.get("name")
    if helper_type in _SCOPED_STORAGE_HELPER_TYPES or not isinstance(name, str) or not name.strip():
        return None
    try:
        items = _storage_items(helper_type, await async_ws_command(hass, f"{helper_type}/list", {}))
    except WsDispatchError:
        return None
    for item in items or []:
        if not isinstance(item, dict) or str(item.get("name") or "").strip().casefold() != name.strip().casefold():
            continue
        entity_id = _resolve_helper_entity_id(hass, helper_type, str(item.get("id") or ""))
        if entity_id is not None and resolve(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE):
            return (f"The {helper_type} helper {name.strip()!r} already exists (helper_id {item.get('id')!r}). "
                    "Use edit_helper to change it, or choose another name.")
    return None


async def _tool_create_helper(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a helper (Confirm-gated)."""
    if effective_cap(access_key, "cap_helper_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "create_helper"
    helper_type = args.get("helper_type")
    if _valid_helper_type(helper_type):
        if "flow_steps" in args:
            return (
                _tool_error("Storage helpers use config, not flow_steps."),
                "invalid_request", "create_helper",
            )
        pre = _helper_write_precheck(args, "create_helper", require_id=False)
        if pre is not None:
            return pre
        if duplicate := await _duplicate_helper_name_error(
                hass, access_key, str(helper_type), dict_arg(args.get("config"))):
            return _tool_error(duplicate), "invalid_request", "create_helper"
        if helper_type in _SCOPED_STORAGE_HELPER_TYPES:
            if scope_error := _scoped_helper_create_scope_error(
                str(helper_type), access_key
            ):
                return _tool_error(scope_error), "denied", "create_helper"
            if absence_error := await _tag_create_absence_error(
                args, hass, "create_helper"
            ):
                return absence_error
            if helper_type == "person" and (
                relationship_error := _person_relationship_scope_error(
                    dict_arg(args.get("config")), access_key, hass
                )
            ):
                return _tool_error(relationship_error), "denied", "create_helper"
    else:
        if not await _is_config_flow_helper(hass, helper_type):
            extra = (
                " OTP is deliberately excluded because its generated-token flow "
                "requires a live confirmation code."
                if helper_type == "otp" else ""
            )
            return (
                _tool_error(
                    "helper_type must be a supported storage helper or a Home Assistant "
                    f"integration classified as a helper with a config flow.{extra}"
                ),
                "invalid_request", "create_helper",
            )
        if "config" in args:
            return (
                _tool_error("Config-flow helpers use flow_steps, not config."),
                "invalid_request", "create_helper",
            )
        steps = args.get("flow_steps")
        if error := _helper_flow_steps_error(steps):
            return _tool_error(error), "invalid_request", "create_helper"
        flow_steps = cast(list[dict[str, Any]], steps)
        status, detail = await _prepare_config_flow_helper(
            hass, str(helper_type), flow_steps, access_key
        )
        if status == "form":
            return (
                _tool_success(json.dumps(detail, default=str)),
                "allowed", f"helper_flow:{helper_type}",
            )
        if status == "scope_error":
            return _tool_error(str(detail)), "denied", "create_helper"
        if status == "denied":
            return (
                _tool_error(
                    "These entities are outside this access key's write scope: "
                    f"{', '.join(detail)}. A helper may expose or actuate every entity "
                    "it references, so all of them must already be writable."
                ),
                "denied", "create_helper",
            )
        if status != "ready":
            return _tool_error(str(detail)), "invalid_request", "create_helper"
    create_entity = _helper_create_mesa_entity(str(helper_type), args, hass)
    mesa_context = _helper_mesa_context(
        data,
        access_key,
        [create_entity],
        action="create",
        service_data={
            "helper_type": helper_type,
            "config": args.get("config"),
            "flow_steps": args.get("flow_steps"),
        },
        session_id=request_id or "create_helper",
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    approval_args, blocked = await _helper_mesa_approval_gate(
        args,
        access_key,
        hass,
        data,
        context=mesa_context,
        tool_name="create_helper",
        request_id=request_id,
        client_ip=client_ip,
        diff_builder=lambda: _build_diff_create_helper(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_helper(approval_args, access_key, hass, data)


def _edit_helper_update(helper_type: str, before: Any, config: dict, *, replace: bool = False) -> dict:
    """Return the update HA needs so that only the fields the caller named change.

    Stored fields are resent beneath the changes; null clears an optional field. Zone, tag and
    person merge in HA and get the caller's fields. `replace` sends its version alone.
    """
    # Most storage collections rebuild the item from the update payload alone. A restore's version
    # alone removes a field added since that version rather than carrying it over.
    if helper_type in STORAGE_HELPER_MERGING_TYPES or not isinstance(before, dict):
        return dict(config)
    if replace:
        return {key: value for key, value in config.items() if key != "id" and value is not None}
    merged = {key: value for key, value in before.items() if key != "id"}
    merged.update(config)
    return {key: value for key, value in merged.items() if value is not None}


async def _read_helper_config(hass: HomeAssistant, helper_type: str, helper_id: str) -> dict | None:
    """Return a helper's stored config, or None when absent or unreadable; for restore's readback.
    """
    # Either None leaves the restore unverified. Edit and delete need the distinction and use
    # _stored_helper_item.
    rows = await _storage_helper_rows(hass, helper_type)
    return next((row for row in rows or [] if row.get("id") == helper_id), None)


@write_boundary
async def _execute_create_helper(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    helper_type = args.get("helper_type")
    config = args.get("config")
    if helper_type == "tag":
        # A tag_id taken since review changes the predicted entity, which would
        # otherwise surface as a MESA change. Scope first, so absence is no oracle.
        if scope_error := _scoped_helper_create_scope_error("tag", access_key):
            return _tool_error(scope_error), "denied", "create_helper"
        if absence_error := await _tag_create_absence_error(args, hass, "create_helper"):
            return absence_error
    create_entity = _helper_create_mesa_entity(str(helper_type), args, hass)
    mesa_context = _enforce_helper_mesa_context(
        args,
        access_key,
        hass,
        data,
        [create_entity],
        action="create",
        service_data={
            "helper_type": helper_type,
            "config": config,
            "flow_steps": args.get("flow_steps"),
        },
        session_id="create_helper_execute",
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    if not _valid_helper_type(helper_type):
        if not await _is_config_flow_helper(hass, helper_type):
            return _tool_error("This is not a creatable helper config flow."), "invalid_request", "create_helper"
        steps = args.get("flow_steps")
        if error := _helper_flow_steps_error(steps):
            return _tool_error(error), "invalid_request", "create_helper"
        flow_steps = cast(list[dict[str, Any]], steps)
        status, detail = await _prepare_config_flow_helper(
            hass, str(helper_type), flow_steps, access_key
        )
        if status == "scope_error":
            return _tool_error(str(detail)), "denied", "create_helper"
        if status == "denied":
            return (
                _tool_error(
                    "These entities are outside this access key's write scope: "
                    f"{', '.join(detail)}."
                ),
                "denied", "create_helper",
            )
        if status != "ready":
            message = (
                "The helper flow no longer reaches its final form with these steps. "
                "Call create_helper again to inspect the current form."
                if status == "form" else str(detail)
            )
            return _tool_error(message), "invalid_request", "create_helper"
        return await _execute_config_flow_helper(
            str(helper_type), flow_steps, access_key, hass, data,
            create_mesa_context=mesa_context,
        )
    if not isinstance(config, dict) or not config:
        return _tool_error("config must be a non-empty object (at least 'name')."), "invalid_request", "create_helper"
    pre = _helper_write_precheck(args, "create_helper", require_id=False)
    if pre is not None:
        return pre
    if helper_type in _SCOPED_STORAGE_HELPER_TYPES:
        if scope_error := _scoped_helper_create_scope_error(str(helper_type), access_key):
            return _tool_error(scope_error), "denied", "create_helper"
        if absence_error := await _tag_create_absence_error(
            args, hass, "create_helper"
        ):
            return absence_error
        if helper_type == "person" and (
            relationship_error := _person_relationship_scope_error(
                config, access_key, hass
            )
        ):
            return _tool_error(relationship_error), "denied", "create_helper"
    create_payload = dict(config)
    restore_id = args.get(_HELPER_RESTORE_ID)
    # A tag keeps its explicit tag_id. Every other storage collection derives
    # the ID from the name, which may have changed since the helper was created,
    # so a restore seeds the name with the original storage ID: Home Assistant
    # then allocates that exact ID and reapplies the registry customizations it
    # still holds for the deleted entity. The restored name follows.
    seeded = helper_type != "tag" and isinstance(restore_id, str)
    if seeded:
        create_payload["name"] = restore_id
    try:
        item = await async_ws_command(hass, f"{helper_type}/create", create_payload)
    except WsDispatchError as exc:
        return _tool_error(f"Failed to create helper: {exc}"), "invalid_request", "create_helper"
    new_id = item.get("id") if isinstance(item, dict) else None
    if seeded and new_id != restore_id:
        if new_id is not None:
            try:
                await async_ws_command(
                    hass, f"{helper_type}/delete", {f"{helper_type}_id": str(new_id)}
                )
            except WsDispatchError:
                return (
                    _tool_error(
                        f"Home Assistant assigned a different {helper_type} ID and automatic "
                        "cleanup failed. Remove it in Home Assistant."
                    ),
                    "invalid_request",
                    "create_helper",
                )
        return (
            _tool_error(
                f"The original {helper_type} ID is unavailable, so the restore was rolled back."
            ),
            "invalid_request",
            "create_helper",
        )
    if seeded and config.get("name") != restore_id:
        try:
            item = await async_ws_command(
                hass,
                f"{helper_type}/update",
                {**config, f"{helper_type}_id": restore_id},
            )
        except WsDispatchError as exc:
            try:
                await async_ws_command(
                    hass, f"{helper_type}/delete", {f"{helper_type}_id": restore_id}
                )
            except WsDispatchError:
                return (
                    _tool_error(
                        f"Failed to finish restoring the {helper_type} and automatic cleanup "
                        "failed. Remove it in Home Assistant."
                    ),
                    "invalid_request",
                    "create_helper",
                )
            return (
                _tool_error(f"Failed to finish restoring the {helper_type}: {exc}"),
                "invalid_request",
                "create_helper",
            )
    entity_id = (
        _resolve_helper_entity_id(hass, str(helper_type), str(new_id))
        if new_id is not None else None
    )
    if helper_type in _SCOPED_STORAGE_HELPER_TYPES:
        if entity_id is None or resolve(entity_id, access_key, hass) != Permission.WRITE:
            if new_id is not None:
                try:
                    await async_ws_command(
                        hass,
                        f"{helper_type}/delete",
                        {f"{helper_type}_id": str(new_id)},
                    )
                except WsDispatchError:
                    return (
                        _tool_error(
                            f"The new {helper_type} did not enter this access key's write "
                            "scope, and "
                            "automatic cleanup failed. Remove it in Home Assistant."
                        ),
                        "invalid_request",
                        "create_helper",
                    )
            return (
                _tool_error(
                    f"The new {helper_type} did not enter this access key's write scope, "
                    "so it was removed and nothing changed."
                ),
                "denied",
                "create_helper",
            )
        if helper_type == "person" and (
            not isinstance(item, dict)
            or not _person_relationships_accessible(item, access_key, hass, write=True)
        ):
            if new_id is not None:
                try:
                    await async_ws_command(
                        hass, "person/delete", {"person_id": str(new_id)}
                    )
                except WsDispatchError:
                    return (
                        _tool_error(
                            "The new person has an inaccessible relationship and "
                            "automatic cleanup failed. Remove it in Home Assistant."
                        ),
                        "invalid_request",
                        "create_helper",
                    )
            return (
                _tool_error(
                    "The new person has an inaccessible relationship, so it was "
                    "removed and nothing changed."
                ),
                "denied",
                "create_helper",
            )
    post_mesa = (
        _helper_mesa_context(
            data,
            access_key,
            [entity_id] if entity_id is not None else [],
            action="create",
            service_data={"helper_type": helper_type, "config": config},
            session_id="create_helper_materialized",
        )
    )
    post_error: tuple[dict, str, str] | None = None
    if isinstance(post_mesa, tuple):
        post_error = post_mesa
    elif post_mesa.decision == "deny":
        _fire_helper_mesa_blocks(hass, access_key, post_mesa)
        post_error = _helper_mesa_error(post_mesa)
    elif post_mesa.decision == "confirm" and not (
        _restore_ctx.get() is not None
        or (
            _approved_exec_ctx.get()
            and post_mesa.profile_fingerprints.issubset(
                mesa_context.profile_fingerprints
            )
        )
    ):
        post_error = _helper_mesa_error(post_mesa, changed=True)
    if post_error is not None:
        if new_id is not None:
            try:
                await async_ws_command(
                    hass,
                    f"{helper_type}/delete",
                    {f"{helper_type}_id": str(new_id)},
                )
            except WsDispatchError:
                return (
                    _tool_error(
                        "MESA blocked the new helper, but automatic cleanup failed. "
                        "Remove it in Home Assistant."
                    ),
                    "invalid_request",
                    "create_helper",
                )
        return post_error
    assert isinstance(post_mesa, _HelperMesaContext)
    note_mesa_advisory(post_mesa.warnings)
    # Read the stored row back: it must hold what Home Assistant reported creating,
    # which already carries HA's own normalization of the request. Unreadable
    # storage leaves the create unconfirmed rather than failed.
    rows = await _storage_helper_rows(hass, str(helper_type))
    stored = next((row for row in rows if row.get("id") == new_id), None) if rows and new_id is not None else None
    # Every field Home Assistant reported must read back; a list may add derived ones, as tag/list
    # adds the name it keeps in the entity registry.
    verified = None if rows is None else stored is not None and (
        not isinstance(item, dict) or all(stored.get(key) == value for key, value in item.items()))
    if stored is not None:
        item = stored
    public_item = (
        _public_helper_config(str(helper_type), item)
        if helper_type == "person" and isinstance(item, dict)
        else item if isinstance(item, dict) else dict(config)
    )
    version_after = public_item if helper_type == "person" else config
    await _record_version(
        data, access_key, resource_type="helper", resource_id=f"{helper_type}:{new_id}",
        action="create", before=None, after=version_after, alias=config.get("name"),
    )
    body: dict[str, Any] = {"helper_type": helper_type, "helper": public_item, "verified": verified}
    if verified is not True:
        # As edit and delete do: a create that does not read back is a write of unknown effect.
        body.update(status="partial" if verified is False else "unknown", retry_safe=False, message=(
            "Home Assistant reported the helper created, but its stored configuration does not read back as reported."
            if verified is False else "Home Assistant reported the helper created, but its storage could not be read back."
        ) + " Check the helper list before retrying; do not repeat the create.")
        return _tool_uncertain(body), "invalid_request", f"helper:{helper_type}"
    return _tool_success(json.dumps(body, default=str)), "allowed", f"helper:{helper_type}"


async def _execute_config_flow_helper(
    helper_type: str,
    steps: list[dict[str, Any]],
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    *,
    create_mesa_context: _HelperMesaContext,
) -> tuple[dict, str, str]:
    """Run the approved helper flow through its final form exactly once."""
    flow_id = None
    entry = None
    try:
        result = await hass.config_entries.flow.async_init(
            helper_type, context={"source": SOURCE_USER}
        )
        flow_id = result.get("flow_id")
        for index, supplied in enumerate(steps):
            body = _flow_step_body(helper_type, result)
            if body is None or body["step"]["step_id"] != supplied["step_id"]:
                return (
                    _tool_error("The helper flow changed after approval; nothing was created."),
                    "invalid_request", "create_helper",
                )
            if menu_error := _creation_menu_error(body, supplied):
                return _tool_error(menu_error), "invalid_request", "create_helper"
            terminal = body["step"]["last_step"]
            if terminal is not (index == len(steps) - 1):
                return _tool_error("The helper flow's final boundary changed; nothing was submitted."), "invalid_request", "create_helper"
            denied = _unwritable_option_entities(
                supplied["data"], access_key, hass,
                template_type=supplied["step_id"] if helper_type == "template" and supplied["step_id"] != "user" else None,
            )
            if denied:
                return (
                    _tool_error(
                        "These entities are outside this access key's write scope: "
                        f"{', '.join(denied)}."
                    ),
                    "denied", "create_helper",
                )
            check_request_authority()
            fresh_mesa = _helper_mesa_context(data, access_key,
                [_helper_create_mesa_entity(helper_type, {"flow_steps": steps}, hass)],
                action="create", service_data={"helper_type": helper_type, "config": None, "flow_steps": steps},
                session_id="create_helper_execute")
            if isinstance(fresh_mesa, tuple) or fresh_mesa.fingerprint != create_mesa_context.fingerprint:
                return _tool_error("Helper safety policy changed before creation; nothing was submitted."), "denied", "create_helper"
            result = await hass.config_entries.flow.async_configure(
                flow_id, dict(supplied["data"])
            )
            if result.get("type") == FlowResultType.CREATE_ENTRY:
                if index != len(steps) - 1:
                    entry = result.get("result")
                    if entry is not None:
                        await hass.config_entries.async_remove(entry.entry_id)
                        entry = None
                    return (
                        _tool_error("The helper flow finished before all approved steps were used."),
                        "invalid_request", "create_helper",
                    )
                entry = result.get("result")
                break
            if result.get("type") == FlowResultType.ABORT:
                return (
                    _tool_error(
                        f"Home Assistant refused this helper: {result.get('reason')}. "
                        "Nothing was created."
                    ),
                    "invalid_request", "create_helper",
                )
            if result.get("errors"):
                detail = ", ".join(
                    f"{field}: {message}"
                    for field, message in (result.get("errors") or {}).items()
                )
                return (
                    _tool_error(
                        f"The helper form was rejected ({detail}). Nothing was created."
                    ),
                    "invalid_request", "create_helper",
                )
        if entry is None:
            return (
                _tool_error("The approved steps did not create a helper."),
                "invalid_request", "create_helper",
            )
    except HelperScopeError as exc:
        return _tool_error(str(exc)), "denied", "create_helper"
    except vol.Invalid as err:
        return (
            _tool_error(f"The helper form was rejected: {err}. Nothing was created."),
            "invalid_request", "create_helper",
        )
    except Exception:  # integration failures become clean tool errors
        _LOGGER.exception("Helper creation flow failed for %s", helper_type)
        return (
            _tool_error("Could not create this helper."),
            "invalid_request", "create_helper",
        )
    finally:
        if entry is None:
            await _abort_create_flow(hass, flow_id)

    registry = er.async_get(hass)
    entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in registry.entities.values()
        if registry_entry.config_entry_id == entry.entry_id
    )
    if helper_type == "template":
        selected_type = next((
            step.get("data", {}).get("next_step_id")
            for step in steps
            if isinstance(step, dict) and step.get("step_id") == "user"
            and isinstance(step.get("data"), dict)
        ), None)
        created_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        if (selected_type not in TEMPLATE_RECOVERY_TYPES or len(created_entries) != 1
                or created_entries[0].platform != "template"
                or created_entries[0].domain != selected_type
                or created_entries[0].entity_id not in entity_ids):
            try:
                await hass.config_entries.async_remove(entry.entry_id)
            except Exception:  # unsafe materialization must be surfaced
                _LOGGER.exception("Could not clean up malformed Template helper %s", entry.entry_id)
                return (
                    _tool_error("Template creation produced an unexpected entity mapping and cleanup failed. Inspect Home Assistant before retrying."),
                    "invalid_request", "create_helper",
                )
            return (
                _tool_error("Template creation did not produce exactly one entity of the selected type; it was removed."),
                "invalid_request", "create_helper",
            )
    post_mesa = _helper_mesa_context(
        data,
        access_key,
        entity_ids,
        action="create",
        service_data={"helper_type": helper_type},
        session_id="create_helper_flow_materialized",
    )
    post_error: tuple[dict, str, str] | None = None
    if isinstance(post_mesa, tuple):
        post_error = post_mesa
    elif post_mesa.decision == "deny":
        _fire_helper_mesa_blocks(hass, access_key, post_mesa)
        post_error = _helper_mesa_error(post_mesa)
    elif post_mesa.decision == "confirm" and not (
        _restore_ctx.get() is not None
        or (
            _approved_exec_ctx.get()
            and post_mesa.profile_fingerprints.issubset(
                create_mesa_context.profile_fingerprints
            )
        )
    ):
        post_error = _helper_mesa_error(post_mesa, changed=True)
    if post_error is not None:
        try:
            await hass.config_entries.async_remove(entry.entry_id)
        except Exception:  # report an unsafe partial create loudly
            _LOGGER.exception(
                "MESA blocked new helper entry %s but cleanup failed", entry.entry_id
            )
            return (
                _tool_error(
                    "MESA blocked the new helper, but automatic cleanup failed. "
                    "Remove its configuration entry in Home Assistant."
                ),
                "invalid_request",
                "create_helper",
            )
        return post_error
    assert isinstance(post_mesa, _HelperMesaContext)
    note_mesa_advisory(post_mesa.warnings)
    await _record_version(
        data,
        access_key,
        resource_type="config_entry",
        resource_id=entry.entry_id,
        action="create",
        before=None,
        after={
            "restorable": False,
            "reason": "Config-flow helper creation cannot be replayed safely.",
        },
        alias=entry.title,
    )
    return (
        _tool_success(json.dumps({
            "helper_type": helper_type,
            "config_entry": {
                "entry_id": entry.entry_id,
                "domain": entry.domain,
                "title": entry.title,
                "state": entry.state.value,
            },
            "entity_ids": entity_ids,
            "note": "The helper is materialized; no restart is needed.",
        }, default=str)),
        "allowed",
        f"config_entry:{entry.entry_id}",
    )


async def _tool_edit_helper(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: edit a helper (Confirm-gated)."""
    if effective_cap(access_key, "cap_helper_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "edit_helper"
    if args.get("helper_type") in TEMPLATE_RECOVERY_TYPES:
        helper_id = str(args.get("helper_id") or "").strip()
        helper_type = str(args.get("helper_type"))
        entry = _template_helper_entry(hass, helper_type, helper_id)
        if entry is None:
            return _tool_error("Helper not found."), "not_found", f"helper:{helper_type}:{helper_id}"
        settings = args.get("config")
        if not isinstance(settings, dict) or not settings:
            return _tool_error("config must be a non-empty object of Template settings."), "invalid_request", "edit_helper"
        return await _tool_set_helper_settings(
            {"entry_id": entry.entry_id, "settings": settings},
            access_key, hass, data, request_id=request_id, client_ip=client_ip,
        )
    pre = _helper_write_precheck(args, "edit_helper", require_id=True)
    if pre is not None:
        return pre
    approval_args, context_error = await _bind_scoped_helper_context(
        args, access_key, hass, "edit_helper"
    )
    if context_error is not None:
        return context_error
    if args.get("helper_type") == "person" and "device_trackers" in dict_arg(
        args.get("config")
    ) and (
        relationship_error := _person_relationship_scope_error(
            dict_arg(args.get("config")), access_key, hass
        )
    ):
        return _tool_error(relationship_error), "denied", "edit_helper"
    helper_type = str(args.get("helper_type"))
    helper_id = str(args.get("helper_id") or "").strip()
    entity_id = _resolve_helper_entity_id(hass, helper_type, helper_id)
    if entity_id is None:
        return _tool_error("Helper not found."), "not_found", f"helper:{helper_type}:{helper_id}"
    mesa_context = _helper_mesa_context(
        data,
        access_key,
        [entity_id],
        action="edit",
        service_data={"helper_type": helper_type, "config": args.get("config")},
        session_id=request_id or "edit_helper",
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    # The executor needs this item as its safety copy, so refuse now rather
    # than after an approval that could only fail.
    current, missing = await _stored_helper_item(hass, access_key, helper_type, helper_id, entity_id, "edit_helper")
    if missing is not None:
        return missing
    approval_args, blocked = await _helper_mesa_approval_gate(
        approval_args,
        access_key,
        hass,
        data,
        context=mesa_context,
        tool_name="edit_helper",
        request_id=request_id,
        client_ip=client_ip,
        diff_builder=lambda: _build_diff_edit_helper(approval_args, access_key, hass, current),
    )
    if blocked is not None:
        return blocked
    return await _execute_edit_helper(approval_args, access_key, hass, data)


class _UncertainHelperWrite(Exception):
    """A storage helper write reached Home Assistant without a verified result."""

    def __init__(self, recovery_id: str, status: str) -> None:
        super().__init__(status)
        self.recovery_id = recovery_id
        self.status = status


def _uncertain_helper_result(exc: _UncertainHelperWrite, tool_name: str) -> tuple[dict, str, str]:
    """Report a dispatched but unverified helper write without inviting a retry."""
    return _tool_uncertain({
        "status": exc.status, "recovery_id": exc.recovery_id, "retry_safe": False,
        "error": "Home Assistant received the helper change, but its result was not verified. "
                 "Inspect configuration recovery and the helper's current state; do not retry automatically.",
    }), "invalid_request", tool_name


def _helper_edit_landed(payload: dict, target_field: str, observed: Any, result: Any) -> bool:
    """Whether the stored row carries every field of an edit.

    HA normalizes what it accepts, so a field also matches the value the update returned. A row
    holding neither means the edit did not land as reported.
    """
    if not isinstance(observed, dict):
        return False
    returned = result if isinstance(result, dict) else {}
    return all(
        observed.get(k) == v or (k in returned and observed.get(k) == returned[k])
        for k, v in payload.items() if k != target_field
    )


async def _protected_storage_helper_write(args, access_key, hass: HomeAssistant, data, before, payload, *, deleting=False):
    """Retain a durable safety copy and verify storage readback after one dispatch.

    Raises ConfigurationRecoveryError when nothing changed and _UncertainHelperWrite once a dispatch
    may have taken effect. An authority change propagates after recovery is saved.
    """

    # Nothing changed means a refusal before dispatch, or a Home Assistant refusal whose settled
    # readback still equals the safety copy.
    helper_type = args["helper_type"]
    helper_id = str(args["helper_id"]).strip()
    target_field = f"{helper_type}_id"
    if (payload.get(target_field) != helper_id
            or set(payload) & (STORAGE_HELPER_REQUEST_FIELDS - {target_field})
            or (deleting and set(payload) != {target_field})):
        raise ConfigurationRecoveryError("Helper request does not match its exact target; nothing was dispatched.")
    payload = {**payload, target_field: helper_id}
    operation = "delete_helper" if deleting else "edit_helper"
    key = f"helper:{helper_type}:{helper_id}"
    recovery = data.config_recovery

    async def read():
        rows = await _storage_helper_rows(hass, helper_type)
        if rows is None:
            raise ConfigurationRecoveryError("Helper storage could not be read")
        return next((row for row in rows if row.get("id") == helper_id), None)

    if not isinstance(before, dict) or before.get("id") != helper_id:
        raise ConfigurationRecoveryError("A complete helper safety copy is unavailable; nothing was dispatched.")
    # Private person bindings intentionally remain outside Phoenix storage.
    public = _public_helper_config(helper_type, before) if helper_type == "person" else before
    update = {k: v for k, v in payload.items() if k != target_field}
    # Mirror HA's own update rule: merging collections keep unnamed fields, the
    # rest store exactly the (already merged) update beneath the item id.
    intended = {**before, **update} if helper_type in STORAGE_HELPER_MERGING_TYPES else {"id": helper_id, **update}
    rid = None
    async with recovery.resource(key):
        rid = await recovery.begin(key, operation, access_key.id, {
            "snapshot_type": "storage_helper", "helper_type": helper_type,
            "helper_id": helper_id, "config": public,
        }, expected={
            "snapshot_type": "storage_helper", "helper_type": helper_type, "helper_id": helper_id,
            "config": None if deleting else _public_helper_config(helper_type, intended),
        })
        try:
            await recovery.mark(rid, "dispatching")
            refusal = None
            try:
                if await read() != before:
                    refusal = "Helper configuration changed while saving its safety copy"
                elif (await _revalidate_scoped_helper_context(args, access_key, hass, operation))[1] is not None:
                    refusal = "Helper authority changed before dispatch"
                else:
                    check_request_authority()
                    entity_id = _resolve_helper_entity_id(hass, helper_type, helper_id)
                    if entity_id is None:
                        refusal = "Helper identity changed before dispatch"
                    elif isinstance(_enforce_helper_mesa_context(args, access_key, hass, data, [entity_id],
                            action="delete" if deleting else "edit",
                            service_data={"helper_type": helper_type} if deleting else {"helper_type": helper_type, "config": args["config"]},
                            session_id=operation), tuple):
                        refusal = "Helper safety policy changed before dispatch"
            except ConfigurationRecoveryError as exc:
                refusal = str(exc)
            except AuthorityChangedError:
                await recovery.mark(rid, "not_applied")
                raise
            if refusal is not None:
                await recovery.mark(rid, "not_applied")
                raise ConfigurationRecoveryError(f"{refusal}; nothing was dispatched.")
            try:
                result = await async_ws_command(hass, f"{helper_type}/{'delete' if deleting else 'update'}", payload)
            except WsCommandNotDispatchedError:
                await recovery.mark(rid, "not_applied")
                raise ConfigurationRecoveryError("Home Assistant refused the helper request before running it; nothing was dispatched.") from None
            except WsDispatchError as exc:
                # A settled refusal whose readback is still the safety copy
                # changed nothing; anything else may have landed.
                observed: Any = _UNREAD
                if not ws_commands_unsettled():
                    try:
                        observed = await read()
                    except ConfigurationRecoveryError:
                        pass
                    except AuthorityChangedError:
                        await recovery.mark(rid, "unknown")
                        raise
                if observed != before:
                    await recovery.mark(rid, "unknown")
                    raise _UncertainHelperWrite(rid, "unknown") from exc
                await recovery.mark(rid, "not_applied")
                detail = redact_inaccessible_entity_ids_in_text(str(exc), access_key, hass)[:500]
                raise ConfigurationRecoveryError(f"Home Assistant refused the helper change ({detail}); nothing changed.") from None
            try:
                observed = await read()
            except ConfigurationRecoveryError:
                await recovery.mark(rid, "unknown")
                raise _UncertainHelperWrite(rid, "unknown") from None
            except AuthorityChangedError:
                await recovery.mark(rid, "unknown")
                raise
            verified = observed is None if deleting else _helper_edit_landed(payload, target_field, observed, result)
            await recovery.mark(rid, "verified" if verified else "partial")
            if not verified:
                raise _UncertainHelperWrite(rid, "partial")
            return result
        finally:
            recovery.leave(rid)


@write_boundary
async def _execute_edit_helper(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    helper_type = args.get("helper_type")
    helper_id = str(args.get("helper_id") or "").strip()
    config = args.get("config")
    if not _valid_helper_type(helper_type):
        return _tool_error(f"helper_type must be one of: {', '.join(sorted(HELPER_TYPES))}."), "invalid_request", "edit_helper"
    if not helper_id:
        return _tool_error("helper_id is required."), "invalid_request", "edit_helper"
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "edit_helper"
    pre = _helper_write_precheck(args, "edit_helper", require_id=True)
    if pre is not None:
        return pre
    scoped_context, context_error = await _revalidate_scoped_helper_context(
        args, access_key, hass, "edit_helper"
    )
    if context_error is not None:
        return context_error
    # Ordinary helper authoring is cap-gated rather than entity-scoped. Zone,
    # tag, and person are the narrow exceptions because their records carry
    # sensitive location, scan, user, or tracker relationships.
    # Every path still requires a real storage-backed helper entity.
    entity_id = _resolve_helper_entity_id(hass, str(helper_type), str(helper_id))
    if entity_id is None:
        return _tool_error("Helper not found."), "not_found", f"helper:{helper_type}:{helper_id}"
    mesa_context = _enforce_helper_mesa_context(
        args,
        access_key,
        hass,
        data,
        [entity_id],
        action="edit",
        service_data={"helper_type": helper_type, "config": config},
        session_id="edit_helper_execute",
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    if scoped_context is not None:
        before_cfg = scoped_context["config"]
    else:
        before_cfg, missing = await _stored_helper_item(
            hass, access_key, str(helper_type), helper_id, entity_id, "edit_helper",
        )
        if missing is not None:
            return missing
    if helper_type == "person":
        if "device_trackers" in config and (
            relationship_error := _person_relationship_scope_error(
                config, access_key, hass
            )
        ):
            return _tool_error(relationship_error), "denied", "edit_helper"
        # HA's person/update schema defaults an omitted tracker list to [], so a
        # name-only edit must explicitly merge the stored relationship. user_id
        # and picture are omitted intentionally: HA preserves both on update.
        current = scoped_context["config"] if scoped_context is not None else {}
        config = {
            **config,
            "device_trackers": list(
                config.get("device_trackers", current.get("device_trackers", []))
            ),
        }
    replace = args.get(_HELPER_RESTORE_REPLACE) is True
    if replace and helper_type in STORAGE_HELPER_MERGING_TYPES and isinstance(before_cfg, dict):
        kept = sorted(
            field for field in _MERGING_OPTIONAL_FIELDS.get(str(helper_type), ())
            if before_cfg.get(field) is not None and config.get(field) is None
        )
        if kept:
            return _tool_error(
                f"Home Assistant cannot remove the {', '.join(kept)} this {helper_type} now stores, "
                "which the restored version does not have, so it cannot be restored exactly. "
                "Nothing was changed."
            ), "invalid_request", "edit_helper"
    config = _edit_helper_update(str(helper_type), before_cfg, config, replace=replace)
    payload = {**config, f"{helper_type}_id": helper_id}
    try:
        item = await _protected_storage_helper_write(args, access_key, hass, data, before_cfg, payload)
    except AuthorityChangedError:
        raise
    except _UncertainHelperWrite as exc:
        return _uncertain_helper_result(exc, "edit_helper")
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", "edit_helper"
    except Exception:
        return _tool_uncertain({"status": "unknown", "retry_safe": False, "error": "Helper edit was not verified. Inspect configuration recovery and current Home Assistant state before retrying."}), "invalid_request", "edit_helper"
    public_before = (
        _public_helper_config(str(helper_type), before_cfg)
        if helper_type == "person" and isinstance(before_cfg, dict)
        else before_cfg
    )
    public_item = (
        _public_helper_config(str(helper_type), item)
        if helper_type == "person" and isinstance(item, dict)
        else item if isinstance(item, dict) else dict(config)
    )
    version_after = public_item if helper_type == "person" else config
    await _record_version(
        data, access_key, resource_type="helper", resource_id=f"{helper_type}:{helper_id}",
        action="edit", before=public_before, after=version_after, alias=config.get("name"),
    )
    # The stored row is compared before this point; a mismatch returns status partial instead.
    return _tool_success(json.dumps({"helper_type": helper_type, "helper": public_item, "verified": True},
                                    default=str)), "allowed", f"helper:{helper_type}:{helper_id}"


@write_boundary
async def _execute_delete_helper(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    helper_type = args.get("helper_type")
    helper_id = str(args.get("helper_id") or "").strip()
    if not _valid_helper_type(helper_type):
        return _tool_error(f"helper_type must be one of: {', '.join(sorted(HELPER_TYPES))}."), "invalid_request", "delete_helper"
    if not helper_id:
        return _tool_error("helper_id is required."), "invalid_request", "delete_helper"
    scoped_context, context_error = await _revalidate_scoped_helper_context(
        args, access_key, hass, "delete_helper"
    )
    if context_error is not None:
        return context_error
    # Ordinary helpers are cap-gated rather than entity-scoped. Zone, tag, and
    # person have passed stricter storage ownership and WRITE-scope checks above.
    entity_id = _resolve_helper_entity_id(hass, str(helper_type), str(helper_id))
    if entity_id is None:
        return _tool_error("Helper not found."), "not_found", f"helper:{helper_type}:{helper_id}"
    mesa_context = _enforce_helper_mesa_context(
        args,
        access_key,
        hass,
        data,
        [entity_id],
        action="delete",
        service_data={"helper_type": helper_type},
        session_id="delete_helper_execute",
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    if scoped_context is not None:
        before_cfg = scoped_context["config"]
    else:
        before_cfg, missing = await _stored_helper_item(
            hass, access_key, str(helper_type), helper_id, entity_id, "delete_helper",
        )
        if missing is not None:
            return missing
    try:
        await _protected_storage_helper_write(args, access_key, hass, data, before_cfg,
            {f"{helper_type}_id": helper_id}, deleting=True)
    except AuthorityChangedError:
        raise
    except _UncertainHelperWrite as exc:
        return _uncertain_helper_result(exc, "delete_helper")
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", "delete_helper"
    except Exception:
        return _tool_uncertain({"status": "unknown", "retry_safe": False, "error": "Helper deletion was not verified. Inspect configuration recovery and current Home Assistant state before retrying."}), "invalid_request", "delete_helper"
    public_before = (
        _public_helper_config(str(helper_type), before_cfg)
        if helper_type == "person" and isinstance(before_cfg, dict)
        else before_cfg
    )
    await _record_version(
        data, access_key, resource_type="helper", resource_id=f"{helper_type}:{helper_id}",
        action="delete", before=public_before, after=None,
        alias=public_before.get("name") if isinstance(public_before, dict) else None,
    )
    # The row's absence is read back before this point; otherwise the deletion is reported unverified.
    return _tool_success(json.dumps({
        "deleted": True, "helper_type": helper_type, "helper_id": helper_id, "verified": True,
        "message": f"Helper '{helper_id}' deleted successfully.",
    })), "allowed", f"helper:{helper_type}:{helper_id}"


def _build_diff_create_helper(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    helper_type = args.get("helper_type")
    config = dict_arg(args.get("config"))
    flow_steps = args.get("flow_steps") if isinstance(args.get("flow_steps"), list) else []
    if flow_steps:
        config = {"flow_steps": flow_steps}
        for step in flow_steps:
            step_data = step.get("data") if isinstance(step, dict) else None
            if isinstance(step_data, dict) and isinstance(step_data.get("name"), str):
                config["name"] = step_data["name"]
                break
    return {
        "kind": "config_diff",
        **_summary(
            "create_helper", helper_type=helper_type,
            name=config.get("name") or config.get("tag_id") or "<no name>",
        ),
        "target": {"type": "helper", "id": None, "label": config.get("name")},
        "before": None,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"helper_type": helper_type},
    }


def _build_diff_edit_helper(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, current: dict | None = None,
) -> dict:
    helper_type = str(args.get("helper_type"))
    helper_id = str(args.get("helper_id") or "").strip()
    config = dict_arg(args.get("config"))
    before = None
    if isinstance(current, dict):
        # Show the whole resulting item, so unnamed fields visibly survive.
        before = _public_helper_config(helper_type, current)
        config = _public_helper_config(helper_type, {
            **({"id": current["id"]} if "id" in current else {}),
            **_edit_helper_update(helper_type, current, config),
        }) if helper_type not in STORAGE_HELPER_MERGING_TYPES else {**before, **config}
    return {
        "kind": "yaml_diff",
        **_summary("edit_helper", helper_type=helper_type, helper_id=helper_id),
        "target": {"type": "helper", "id": helper_id, "label": config.get("name")},
        "before": _truncate(json.dumps(before, indent=2, default=str)) if before is not None else None,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"helper_type": helper_type},
    }


# ---------------------------------------------------------------------------
# Config-entry helper reconfigure (cap_helper_write)
# ---------------------------------------------------------------------------


async def _helper_config_entry(hass: HomeAssistant, entry_id: Any) -> Any | None:
    """A config entry that is a helper, or None. The gate for both settings tools.

    Classification is HA's own (`integration_type: helper`), so a third-party helper is covered.
    Widening past helpers needs its own capability, not a relaxed predicate.
    """
    # A helper's options are entity references and numbers, so each entity can be checked against
    # the key's tree. An arbitrary integration's options flow can carry a hostname and credentials,
    # and repointing one at a foreign server is an exfiltration primitive no scope check catches.
    if not isinstance(entry_id, str) or not entry_id.strip():
        return None
    entry = hass.config_entries.async_get_entry(entry_id.strip())
    if entry is None:
        return None
    try:
        integration = await async_get_integration(hass, entry.domain)
    except IntegrationNotFound:
        return None
    return entry if integration.integration_type == "helper" else None


def _config_entry_helper_entity_ids(hass: HomeAssistant, entry_id: str) -> list[str]:
    """Every exact entity owned by one helper config entry."""
    return sorted(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.config_entry_id == entry_id
    )


async def _scoped_helper_entry(hass: HomeAssistant, entry_id: Any, access_key: AccessKeyRecord) -> Any | None:
    """_helper_config_entry for one key: None unless it may read every entity the entry owns.

    A scoped key cannot be checked against an entry that owns no entity, so such an entry reads
    as absent, the rule resolve_config_entry_registry_write applies.
    """
    entry = await _helper_config_entry(hass, entry_id)
    if entry is None:
        return None
    owned = _config_entry_helper_entity_ids(hass, entry.entry_id)
    if not owned and not access_key.pass_through:
        return None
    readable = (Permission.READ, Permission.WRITE)
    return entry if all(resolve_read(eid, access_key, hass, registry=True) in readable for eid in owned) else None


def _config_entry_helper_mesa_entities(
    hass: HomeAssistant, entry: Any, data: PhoenixData
) -> list[str] | tuple[dict, str, str]:
    """Exact owners, or a harmless synthetic anchor only while MESA is off."""
    entity_ids = _config_entry_helper_entity_ids(hass, entry.entry_id)
    if entity_ids:
        return entity_ids
    try:
        if data.store.get_settings().mesa_mode != "off":
            return (
                _tool_error("MESA cannot protect this helper because it has no registry entities; nothing changed."),
                "denied",
                "set_helper_settings",
            )
    except Exception:  # unreadable safety mode fails closed
        return (
            _tool_error("MESA safety state could not be read; nothing changed."),
            "denied",
            "set_helper_settings",
        )
    return [f"{entry.domain}.__phoenix_helper_settings__"]


# The two ways HA lets an entry be reconfigured, and they are NOT interchangeable.
# An options flow writes entry.options and finishes with CREATE_ENTRY; a
# reconfigure flow writes entry.data and finishes with ABORT carrying
# reason "reconfigure_successful", because it updates an entry rather than making
# one. Reading a success as a failure (or the reverse) is the whole hazard here,
# so every mechanism-specific difference is funnelled through the _settings_*
# and flow helpers below rather than spread across the tools.
_OPTIONS = "options"
_RECONFIGURE = "reconfigure"
_SETTINGS_MAX_DEPTH = 25
_REDACTED_SETTINGS_NOTE = (
    "A value shown as <redacted> is hidden from this access key and cannot be sent back; "
    "leave that field out to keep it."
)
_RECONFIGURE_OK = "reconfigure_successful"


def _settings_mechanism(entry: Any) -> str | None:
    """Which flow, if any, can change this entry's settings.

    Options first: a helper that offers both is offering the options flow for
    ordinary settings, which is what this tool is for.
    """
    if entry.supports_options:
        return _OPTIONS
    if entry.supports_reconfigure:
        return _RECONFIGURE
    return None


def _settings_store(entry: Any, mechanism: str | None) -> dict:
    """The entry's current settings, from whichever store its flow writes."""
    return dict(entry.data if mechanism == _RECONFIGURE else entry.options)


def _settings_marker_name(marker: Any) -> str | None:
    """Return a string field name from a voluptuous marker."""
    raw = marker.schema if isinstance(marker, vol.Marker) else marker
    return raw if isinstance(raw, str) and raw else None


def _settings_marker_description(marker: Any) -> dict[str, Any]:
    """Return a marker description without trusting third-party shapes."""
    description = getattr(marker, "description", None)
    return description if isinstance(description, dict) else {}


def _settings_is_section(value: Any) -> bool:
    """Recognize Home Assistant form sections without importing private APIs."""
    value_type = type(value)
    return (
        value_type.__module__ == "homeassistant.data_entry_flow"
        and value_type.__name__ == "section"
        and isinstance(getattr(value, "schema", None), vol.Schema)
    )


def _settings_suggested_value(
    marker: Any, inherited: dict[str, Any] | None
) -> tuple[bool, Any]:
    """Return a current value supplied by the edit step, if any."""
    description = _settings_marker_description(marker)
    if "suggested_value" in description and description["suggested_value"] is not None:
        return True, copy.deepcopy(description["suggested_value"])
    name = _settings_marker_name(marker)
    if inherited is not None and name in inherited:
        return True, copy.deepcopy(inherited[name])
    return False, None


def _settings_clear_by_omission(marker: Any) -> bool:
    """Return whether an optional field has no default to replace omission."""
    return not isinstance(marker, vol.Required) and getattr(
        marker, "default", vol.UNDEFINED
    ) is vol.UNDEFINED


def _settings_form_payload(result: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Build an edit-flow payload without resetting omitted stored values.

    HA's edit forms carry current values as suggested values, and unnamed fields are resubmitted
    from those, never from a field's static schema default.
    """
    schema = result.get("data_schema")
    if not isinstance(schema, vol.Schema) or not isinstance(schema.schema, dict):
        return dict(settings)

    def _walk(
        nested_schema: vol.Schema,
        inherited: dict[str, Any] | None = None,
        explicit: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], set[str]]:
        payload: dict[str, Any] = {}
        consumed: set[str] = set()
        for marker, validator in nested_schema.schema.items():
            name = _settings_marker_name(marker)
            if name is None:
                continue
            if _settings_is_section(validator):
                section_value = settings.get(name)
                section_named = isinstance(section_value, dict)
                found, suggested = _settings_suggested_value(marker, inherited)
                nested_inherited = suggested if found and isinstance(suggested, dict) else None
                child, child_consumed = _walk(
                    validator.schema,
                    nested_inherited,
                    section_value if section_named else None,
                )
                section_has_flat_input = any(
                    child_name in settings for child_name in child_consumed
                )
                if (
                    section_named
                    or section_has_flat_input
                    or isinstance(marker, vol.Required)
                    or found
                ):
                    payload[name] = child
                consumed.add(name)
                consumed.update(child_consumed)
                continue

            consumed.add(name)
            if name in settings:
                value = settings[name]
            elif explicit is not None and name in explicit:
                value = explicit[name]
            else:
                found, value = _settings_suggested_value(marker, inherited)
                if not found or redaction_sentinel_path(value) is not None:
                    if isinstance(marker, vol.Required) and getattr(
                        marker, "default", vol.UNDEFINED
                    ) is vol.UNDEFINED:
                        # Let the flow report a genuinely missing required value.
                        continue
                    continue
            if value is None and _settings_clear_by_omission(marker):
                continue
            payload[name] = copy.deepcopy(value)

        if explicit is not None:
            for name, value in explicit.items():
                if name not in consumed:
                    payload[name] = copy.deepcopy(value)
        return payload, consumed

    payload, consumed = _walk(schema)
    for name, value in settings.items():
        if name not in consumed:
            # Preserve unknown keys so Home Assistant can reject them instead
            # of Phoenix silently turning an invalid request into a no-op.
            payload[name] = copy.deepcopy(value)
    return payload


async def _init_settings_flow(hass: HomeAssistant, entry: Any, mechanism: str) -> Any:
    """Open the entry's settings flow and return its first step."""
    if mechanism == _RECONFIGURE:
        return await hass.config_entries.flow.async_init(
            entry.domain,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
    return await hass.config_entries.options.async_init(entry.entry_id)


async def _configure_settings_flow(
    hass: HomeAssistant, mechanism: str, flow_id: str, user_input: dict
) -> Any:
    manager = hass.config_entries.flow if mechanism == _RECONFIGURE else hass.config_entries.options
    return await manager.async_configure(flow_id, user_input)


def _settings_applied(mechanism: str, result: Any) -> bool:
    """Whether the flow actually wrote the settings."""
    # An options flow signals success with CREATE_ENTRY. A reconfigure flow signals it with ABORT
    # and reason "reconfigure_successful", because async_update_reload_and_abort updates the entry
    # and then aborts. An abort with any other reason is a real refusal and must keep reading as
    # one.
    if mechanism == _RECONFIGURE:
        return (result.get("type") == FlowResultType.ABORT
                and result.get("reason") == _RECONFIGURE_OK)
    return result.get("type") == FlowResultType.CREATE_ENTRY


def _options_schema_json(result: Any) -> list | None:
    """Serialize a flow step's data_schema the way HA's own frontend receives it.

    Uses serialize_ha_schema with HA's custom_serializer, as _prepare_result_json does, so an agent
    reads what the UI does. Reimplementing it would drift.
    """
    schema = result.get("data_schema")
    if schema is None:
        # A step with no schema at all is a confirm-only step: it genuinely has
        # no fields, which is NOT the same as a schema this cannot describe, so
        # it returns the empty list rather than the "unknown" None below.
        return []
    try:
        converted = serialize_ha_schema(schema, cv.custom_serializer)
    except Exception:  # a schema shape this cannot describe
        # Returning [] here would be a lie the caller acts on: an empty field
        # list reads as "this helper accepts nothing", and the caller would then
        # send nothing and clear every optional setting. None means "could not be
        # described", which the caller is told rather than left to infer.
        _LOGGER.debug("Could not serialize a settings schema", exc_info=True)
        return None
    # convert() types as dict-or-list; a vol.Schema over a mapping always yields
    # the per-field list, and anything else is not a form an agent can fill.
    return converted if isinstance(converted, list) else None


async def _abort_flow(hass: HomeAssistant, mechanism: str | None, flow_id: str | None) -> None:
    """Abort a settings flow that was opened but will not be finished.

    Every early return after async_init comes through here, or the flow lingers as a half-finished
    dialog. Aborting on the wrong manager silently leaves it open.
    """
    if not flow_id:
        return
    manager = hass.config_entries.flow if mechanism == _RECONFIGURE else hass.config_entries.options
    try:
        manager.async_abort(flow_id)
    except Exception:  # best effort; an already-gone flow is fine
        _LOGGER.debug("Could not abort options flow %s", flow_id, exc_info=True)


def _readable_setting_text(text: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """One settings string as this key may read it; a body naming a hidden entity goes whole.

    A template can reach an entity in forms no text pattern sees (states.lock.front.state), so one
    is parsed, and one that cannot be proved in scope is withheld. A missing entity stays readable,
    since repointing a helper whose source went away needs the old ID.
    """
    def hidden(entity_id: str) -> bool:
        return resolve(entity_id, access_key, hass) in (Permission.NO_ACCESS, Permission.DENY)

    try:
        if any(hidden(eid) for eid in _entities_in_options(text, er.async_get(hass))):
            return REDACTION_SENTINEL
    except HelperScopeError:
        return REDACTION_SENTINEL
    return redact_secrets_in_text(
        _redact_entity_ids(text, hass, lambda eid: not hidden(eid), REDACTION_SENTINEL)) or ""


def _readable_settings(value: Any, access_key: AccessKeyRecord, hass: HomeAssistant, depth: int = 0) -> Any:
    """Stored or form settings as this key may read them.

    A sensitive key's value is masked, an entry keyed by a hidden entity drops, and a string goes
    through _readable_setting_text. Past the depth limit a container is masked.
    """
    if isinstance(value, str):
        return _readable_setting_text(value, access_key, hass)
    if isinstance(value, (dict, list, tuple, set, frozenset)) and depth > _SETTINGS_MAX_DEPTH:
        return REDACTION_SENTINEL
    if isinstance(value, dict):
        return {
            key: REDACTION_SENTINEL if is_sensitive_key(key) else _readable_settings(item, access_key, hass, depth + 1)
            for key, item in value.items()
            if not isinstance(key, str) or _readable_setting_text(key, access_key, hass) == key
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_readable_settings(item, access_key, hass, depth + 1) for item in value]
    return value


def _readable_schema(fields: list, access_key: AccessKeyRecord, hass: HomeAssistant) -> list:
    """A serialized form as this key may read it: a field's name stays, a sensitive one's values go."""
    shown: list = []
    for field in fields:
        if not isinstance(field, dict):
            shown.append(_readable_settings(field, access_key, hass))
            continue
        item = _readable_settings(field, access_key, hass)
        if "name" in field:
            item["name"] = field["name"]
        if isinstance(field.get("schema"), list):
            item["schema"] = _readable_schema(field["schema"], access_key, hass)
        if is_sensitive_key(field.get("name")):
            if "default" in item:
                item["default"] = REDACTION_SENTINEL
            if isinstance(item.get("description"), dict) and "suggested_value" in item["description"]:
                item["description"]["suggested_value"] = REDACTION_SENTINEL
        shown.append(item)
    return shown


def _helper_settings_result(body: dict, tool: str) -> tuple[dict, str, str]:
    """A settings read's success, saying what a redacted value means when it holds one."""
    if redaction_sentinel_path([body.get("settings"), body.get("editable_settings")]) is not None:
        body["note"] = f"{body.get('note', '')} {_REDACTED_SETTINGS_NOTE}".strip()
    return _tool_success(json.dumps(body, default=str)), "allowed", tool


async def _tool_get_helper_settings(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read one helper config entry's options and the schema for them.

    A read, never approval-gated. It opens the settings flow for the schema, which the flow declares
    rather than stores, and aborts it at once. An entry outside the key's scope reads as absent,
    what the entry refers to is redacted, and content_hash covers what this key reads.
    """
    tool = "get_helper_settings"
    if effective_cap(access_key, "cap_helper_write") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", tool
    entry = await _scoped_helper_entry(hass, args.get("entry_id"), access_key)
    if entry is None:
        return _tool_error(_NOT_A_HELPER_ENTRY), "not_found", tool
    mechanism = _settings_mechanism(entry)
    settings = _readable_settings(_settings_store(entry, mechanism), access_key, hass)
    body: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "mechanism": mechanism,
        "settings": settings,
        "content_hash": content_hash(settings),
    }
    if mechanism is None:
        body["schema"] = []
        body["note"] = "This helper exposes no way to change its settings, so they cannot be changed here."
        return _helper_settings_result(body, tool)
    flow_id = None
    try:
        result = await _init_settings_flow(hass, entry, mechanism)
        flow_id = result.get("flow_id")
        schema = _options_schema_json(result)
        if schema is not None:
            schema = _readable_schema(schema, access_key, hass)
        body["step_id"] = result.get("step_id")
        if schema is None:
            # Settings still readable and writable; only the field description is
            # missing, so say that instead of implying there are no fields.
            body["note"] = (
                "This helper's settings form could not be described, so no field list is "
                "available. You can still change settings by sending the values you want; "
                "send the whole of settings, since the fields it accepts are unknown here."
            )
            return _helper_settings_result(body, tool)
        if described := await _with_field_help(
                hass, entry.domain, "options" if mechanism == _OPTIONS else "config", result.get("step_id"), schema):
            body["help"] = described
        body["schema"] = schema
        # THE FIELDS A CALLER ACTUALLY SENDS BACK, taken from the SCHEMA's own
        # suggested values rather than from the stored settings. Intersecting
        # stored keys with field names fails whenever a flow TRANSFORMS its input
        # (a helper may store {"entities": [id]} while its form field is "entity",
        # one id), which would report nothing editable on a helper that
        # reconfigures fine. The stored shape is the integration's business; the
        # form's shape is the caller's.
        #
        # suggested_value is authoritative because the FLOW puts it there: HA's
        # schema flows seed it from the stored options, and a hand-written flow
        # sets it deliberately. A field carrying none has no current value to
        # report and appears in `schema` alone, so the caller decides.
        editable = {}
        for field in schema:
            if not isinstance(field, dict):
                continue
            described = field.get("description")
            if isinstance(described, dict) and "suggested_value" in described:
                editable[field.get("name")] = described["suggested_value"]
        body["editable_settings"] = editable
        body["note"] = (
            "Send editable_settings back with your change applied; those are the form's "
            "own fields and current values, which may be named or shaped differently from "
            "settings above (that is what this helper stores, and it is left alone). A "
            "field in schema but not here has no current value. Omitted fields retain the "
            "form's current suggested values. To clear an optional field with no default, "
            "explicitly send null; other fields follow the form's validation."
        )
    except Exception:  # a flow that will not start is not a Phoenix bug
        _LOGGER.exception("Could not open the settings flow for %s", entry.entry_id)
        return _tool_error("Could not read this helper's settings."), "invalid_request", tool
    finally:
        await _abort_flow(hass, mechanism, flow_id)
    return _helper_settings_result(body, tool)


def _entities_in_options(options: Any, registry: er.EntityRegistry) -> set[str]:
    """Entity references and statically established template dependencies.

    Matched by value, never key name, and read as HA's validators read strings: any letter case,
    comma-separated lists, or an entity registry ID that resolve() maps.
    """
    # Each helper names its source differently, so a key list would go stale and let an unchecked
    # entity through; over-matching a coincidental string only costs a scope check.
    found: set[str] = set()

    def _string(value: str) -> None:
        found.update(template_entities(value))
        for part in value.split(","):
            part = part.strip()
            lowered = part.lower()
            if (_ENTITY_ID_RE.fullmatch(lowered)
                    and not all(piece.isdecimal() for piece in lowered.split("."))):
                found.add(lowered)
            elif registry.async_get(part) is not None:
                found.add(part)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)
        elif isinstance(node, str):
            _string(node)

    _walk(options)
    return found


def _template_options_after_form(options: dict, payload: dict, schema: vol.Schema) -> dict:
    """Match HA's options merge and explicit optional clears for scope checks."""
    merged = {**options, **payload}
    for marker, validator in schema.schema.items():
        if (isinstance(marker, vol.Optional) and marker.schema not in payload
                and not getattr(validator, "config", {}).get("read_only")):
            merged.pop(marker.schema, None)
    return merged


def _unwritable_option_entities(
    options: Any, access_key: AccessKeyRecord, hass: HomeAssistant, *, template_type: str | None = None,
) -> list[str]:
    """Entities in an options payload the access key may not write.

    Write rather than read, matching _unwritable_scene_members: a helper both exposes and can
    actuate its source.
    """
    # An access key that cannot read sensor.secret could otherwise point a helper at it and read the
    # helper's entity, and switch_as_x lets the new entity actuate the old one. Nothing in the
    # serialized schema says which a field does; the failure direction is refusing a legitimate
    # edit, not a scope escape.
    if template_type is not None:
        validate_template_selectors(options, template_type)
    return sorted(
        eid for eid in _entities_in_options(options, er.async_get(hass))
        if resolve(eid, access_key, hass) != Permission.WRITE
    )


def _config_entry_settings_precheck(
    settings: Any, entry: Any, mechanism: str | None, access_key: AccessKeyRecord,
    hass: HomeAssistant, tool: str,
) -> tuple[dict, str, str] | None:
    """Refuse a doomed reconfigure before an approval is created."""
    if not isinstance(settings, dict) or not settings:
        return _tool_error("settings must be a non-empty object of the values to apply."), "invalid_request", tool
    if mechanism is None:
        return (
            _tool_error("This helper exposes no way to change its settings, so they cannot be changed here."),
            "invalid_request", tool,
        )
    if redaction_sentinel_path(settings) is not None:
        return (
            _tool_error("Redacted settings cannot be written back. Omit a redacted field to keep its value."),
            "invalid_request", tool,
        )
    try:
        denied = _unwritable_option_entities(
            settings, access_key, hass,
            template_type=entry.options.get("template_type") if entry.domain == "template" else None,
        )
    except HelperScopeError as exc:
        return _tool_error(str(exc)), "denied", tool
    if denied:
        return (
            _tool_error(
                f"These entities are outside this access key's write scope: {', '.join(denied)}. "
                "A helper exposes and can actuate the entity it points at, so it may only be "
                "pointed at entities this access key could already control."
            ),
            "denied", tool,
        )
    return None


async def _build_diff_set_config_entry_options(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> dict:
    entry = await _helper_config_entry(hass, args.get("entry_id"))
    mechanism = _settings_mechanism(entry) if entry is not None else None
    before = _settings_store(entry, mechanism) if entry is not None else {}
    after = dict_arg(args.get("settings"))
    label = (entry.title if entry is not None else None) or str(args.get("entry_id"))
    # The keys the write actually changes, computed from the payload rather than
    # taken on trust, so the History line an admin reads cannot misreport itself.
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "kind": "config_diff",
        **_summary("config_entry.options", label=label, keys=", ".join(changed) or "(nothing)"),
        "target": {"type": "config_entry", "id": args.get("entry_id"), "label": label},
        "before": _truncate(json.dumps(redact_structure(before), indent=2, default=str)),
        "after": _truncate(json.dumps(redact_structure(after), indent=2, default=str)),
        "preview": {
            "domain": entry.domain if entry is not None else None,
            "mechanism": mechanism,
            "changed_keys": changed,
            "warning": "Replaces this helper's settings; its flow validates them and reloads it.",
        },
    }


async def _preflight_template_settings(hass: HomeAssistant, entry: ConfigEntry, settings, access_key, *, intended=None):
    """Validate an options form without submitting or creating a recovery barrier."""

    before = template_snapshot(hass, entry)
    flow_id = None
    try:
        result = await _init_settings_flow(hass, entry, _OPTIONS)
        flow_id = result.get("flow_id")
        if (hass.config_entries.async_get_entry(entry.entry_id) is not entry
                or template_snapshot(hass, entry) != before):
            return _tool_error("Template configuration changed during settings validation; nothing was submitted."), "invalid_request", "set_helper_settings"
        schema = result.get("data_schema")
        if result.get("type") != FlowResultType.FORM or not isinstance(schema, vol.Schema):
            return _tool_error("This Template helper has no supported settings form."), "invalid_request", "set_helper_settings"
        payload = schema(_settings_form_payload(result, settings))
        # Check retained values too, after HA has built the complete form.
        options = _template_options_after_form(before["options"], payload, schema)
        denied = _unwritable_option_entities(options, access_key, hass, template_type=entry.options["template_type"])
        if denied:
            return _tool_error("Template references are outside this access key's write scope."), "denied", "set_helper_settings"
        if intended is not None:
            intended.update(before, options=options)
    except HelperScopeError as exc:
        return _tool_error(str(exc)), "denied", "set_helper_settings"
    except vol.Invalid as err:
        return _tool_error(f"The settings were rejected: {err}. Nothing was changed."), "invalid_request", "set_helper_settings"
    finally:
        await _abort_flow(hass, _OPTIONS, flow_id)
    return None


async def _tool_set_helper_settings(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: change one helper config entry's settings (Confirm-gated).

    `settings` goes through the entry's own flow, whose schema rejects undeclared keys. Omitted
    fields keep current values; null clears an optional field with no default.
    """
    # This lets an agent repoint a helper whose source entity went away, which nothing else here
    # can. The set to send is `editable_settings` from get_helper_settings with the change applied.
    tool = "set_helper_settings"
    if effective_cap(access_key, "cap_helper_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    entry = await _scoped_helper_entry(hass, args.get("entry_id"), access_key)
    if entry is None:
        return _tool_error(_NOT_A_HELPER_ENTRY), "not_found", tool
    mechanism = _settings_mechanism(entry)
    pre = _config_entry_settings_precheck(
        args.get("settings"), entry, mechanism, access_key, hass, tool)
    if pre is not None:
        return pre
    conflict = _cas_conflict(
        args.get("expected_hash"), _readable_settings(_settings_store(entry, mechanism), access_key, hass), tool)
    if conflict is not None:
        return conflict
    if entry.domain == "template":
        refusal = await _preflight_template_settings(hass, entry, args["settings"], access_key)
        if refusal is not None:
            return refusal
    entity_ids = _config_entry_helper_mesa_entities(hass, entry, data)
    if isinstance(entity_ids, tuple):
        return entity_ids
    mesa_context = _helper_mesa_context(
        data,
        access_key,
        entity_ids,
        action="settings",
        service_data={"helper_type": entry.domain, "settings": args.get("settings")},
        session_id=request_id or tool,
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    approval_args, blocked = await _helper_mesa_approval_gate(
        args,
        access_key,
        hass,
        data,
        context=mesa_context,
        tool_name=tool,
        request_id=request_id,
        client_ip=client_ip,
        diff_builder=lambda: _build_diff_set_config_entry_options(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_config_entry_options(approval_args, access_key, hass, data)


@write_boundary
async def _execute_set_config_entry_options(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Run a helper settings write, wrapping Template entries in configuration recovery."""

    if effective_cap(access_key, "cap_helper_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_helper_settings"
    entry = hass.config_entries.async_get_entry(str(args.get("entry_id") or ""))
    if entry is None or entry.domain != "template":
        return await _apply_config_entry_options(args, access_key, hass, data)
    tool = "set_helper_settings"
    if await _scoped_helper_entry(hass, entry.entry_id, access_key) is None:
        return _tool_error(_NOT_A_HELPER_ENTRY), "not_found", tool
    pre = _config_entry_settings_precheck(args.get("settings"), entry, _settings_mechanism(entry), access_key, hass, tool)
    if pre is not None:
        return pre
    entities = _config_entry_helper_mesa_entities(hass, entry, data)
    if isinstance(entities, tuple):
        return entities
    mesa = _enforce_helper_mesa_context(args, access_key, hass, data, entities, action="settings",
        service_data={"helper_type": entry.domain, "settings": args.get("settings")}, session_id=tool)
    if isinstance(mesa, tuple):
        return mesa
    wanted: dict = {}
    refusal = await _preflight_template_settings(hass, entry, args["settings"], access_key, intended=wanted)
    if refusal is not None:
        return refusal
    rid = None
    try:
        async with protect_template(hass, data, access_key, entry, tool, expected=wanted) as rid:
            saved = data.config_recovery.get(rid)
            assert saved is not None
            result = await _apply_config_entry_options(args, access_key, hass, data, recovery_before=saved["before"])
            current = hass.config_entries.async_get_entry(entry.entry_id)
            if result[1] == "denied":
                # The apply driver only returns policy refusals before submission.
                await data.config_recovery.mark(rid, "not_applied")
                return result
            if result[0].get("isError") or current is not entry:
                await data.config_recovery.mark(rid, "unknown")
                return _tool_uncertain({"status": "unknown", "recovery_id": rid,
                    "retry_safe": False, "error": "Helper settings were not verified; inspect Home Assistant before retrying."}), "invalid_request", tool
            template_snapshot(hass, current)
            await data.config_recovery.mark(rid, "verified", entry_id=current.entry_id)
            # Include the exact operation receipt so restore can retain a
            # partial result without guessing which concurrent record is ours.
            body = json.loads(result[0]["content"][0]["text"])
            body["recovery_id"] = rid
            return _tool_success(json.dumps(body, default=str)), result[1], result[2]
    except Exception as exc:
        message = str(exc) if isinstance(exc, ConfigurationRecoveryError) else "Configuration recovery storage failed. Inspect the recovery record before retrying."
        body = {"error": message, "recovery_id": rid, "status": "unknown" if rid else "not_applied", "retry_safe": False}
        return _tool_uncertain(body) if rid else _tool_error(json.dumps(body)), "invalid_request", tool


async def _apply_config_entry_options(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData, *, record_history: bool = True, recovery_before: dict | None = None
) -> tuple[dict, str, str]:
    tool = "set_helper_settings"
    entry = await _scoped_helper_entry(hass, args.get("entry_id"), access_key)
    if entry is None:
        return _tool_error(_NOT_A_HELPER_ENTRY), "not_found", tool
    mechanism = _settings_mechanism(entry)
    settings = args.get("settings")
    # Re-validated at apply time, not only pre-gate: the entry, the access key's tree
    # and the entities named can all move while an approval waits.
    pre = _config_entry_settings_precheck(settings, entry, mechanism, access_key, hass, tool)
    if pre is not None:
        return pre
    before = _settings_store(entry, mechanism)
    version_before = template_snapshot(hass, entry) if entry.domain == "template" else before
    conflict = _cas_conflict(args.get("expected_hash"), _readable_settings(before, access_key, hass), tool)
    if conflict is not None:
        return conflict
    entity_ids = _config_entry_helper_mesa_entities(hass, entry, data)
    if isinstance(entity_ids, tuple):
        return entity_ids
    mesa_context = _enforce_helper_mesa_context(
        args,
        access_key,
        hass,
        data,
        entity_ids,
        action="settings",
        service_data={"helper_type": entry.domain, "settings": settings},
        session_id="set_helper_settings_execute",
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    if mechanism is None:  # refused by the precheck above; narrows for the type checker
        return _tool_error("This helper exposes no way to change its settings."), "invalid_request", tool

    flow_id = None
    try:
        result = await _init_settings_flow(hass, entry, mechanism)
        flow_id = result.get("flow_id")
        if entry.domain == "template":
            check_request_authority()
            current_entry = hass.config_entries.async_get_entry(entry.entry_id)
            if (current_entry is None or id(current_entry) != id(entry)
                    or template_snapshot(hass, entry) != (recovery_before or version_before)):
                raise ConfigurationRecoveryError("Template configuration changed before settings submission.")
            fresh_entities = _config_entry_helper_mesa_entities(hass, entry, data)
            if isinstance(fresh_entities, tuple):
                await _abort_flow(hass, mechanism, flow_id)
                return fresh_entities
            fresh_mesa = _enforce_helper_mesa_context(args, access_key, hass, data, fresh_entities,
                action="settings", service_data={"helper_type": entry.domain, "settings": settings},
                session_id="set_helper_settings_execute")
            if isinstance(fresh_mesa, tuple):
                await _abort_flow(hass, mechanism, flow_id)
                return fresh_mesa
        payload = _settings_form_payload(result, settings) if isinstance(settings, dict) else {}
        if entry.domain == "template":
            payload = result["data_schema"](payload)
            options = _template_options_after_form(dict(entry.options), payload, result["data_schema"])
            denied = _unwritable_option_entities(options, access_key, hass, template_type=entry.options["template_type"])
            if denied:
                await _abort_flow(hass, mechanism, flow_id)
                return _tool_error("Template references are outside this access key's write scope."), "denied", tool
        result = await _configure_settings_flow(
            hass,
            mechanism,
            flow_id,
            payload,
        )
    except HelperScopeError as exc:
        await _abort_flow(hass, mechanism, flow_id)
        return _tool_error(str(exc)), "denied", tool
    except vol.Invalid as err:
        await _abort_flow(hass, mechanism, flow_id)
        return _tool_error(f"The settings were rejected: {err}"), "invalid_request", tool
    except Exception:  # a flow failure is not a Phoenix bug, but must not leak
        _LOGGER.exception("Settings flow failed for %s", entry.entry_id)
        await _abort_flow(hass, mechanism, flow_id)
        return _tool_error("Could not apply these settings."), "invalid_request", tool

    if not _settings_applied(mechanism, result):
        # Either the flow wants another step, or it refused. Neither applied
        # anything, and a multi-step flow is not something one call can drive.
        await _abort_flow(hass, mechanism, flow_id)
        errors = result.get("errors") or {}
        if errors:
            detail = ", ".join(f"{field}: {msg}" for field, msg in errors.items())
            message = f"The settings were rejected ({detail}). Nothing was changed."
        elif result.get("type") == FlowResultType.ABORT:
            message = f"Nothing was changed; the helper refused: {result.get('reason')}."
        else:
            message = (
                "Nothing was changed. This helper's settings flow asks for more than one "
                "step, which this tool cannot drive; change it in the Home Assistant UI."
            )
        return _tool_error(message), "invalid_request", tool

    # Re-read rather than trusting the payload: the flow's own validators can
    # normalise what they were given (threshold sets absent bounds to None).
    applied_entry = hass.config_entries.async_get_entry(entry.entry_id)
    after = (_settings_store(applied_entry, mechanism)
             if applied_entry is not None else dict_arg(settings))
    if entry.domain == "template" and (
        applied_entry is not entry or result.get("data") != after
    ):
        return _tool_error("Template settings readback did not match the completed flow; inspect recovery before retrying."), "invalid_request", tool
    if record_history:
        await _record_version(
            data, access_key, resource_type="config_entry", resource_id=entry.entry_id,
            action="edit", before=version_before,
            after=template_snapshot(hass, applied_entry) if entry.domain == "template" else after, alias=entry.title,
            summary=_version_summary("config_entry.options", subject=entry.title),
        )
    shown = _readable_settings(after, access_key, hass)
    return (
        _tool_success(json.dumps({
            "entry_id": entry.entry_id, "domain": entry.domain, "title": redact_diagnostics(entry.title),
            "mechanism": mechanism, "settings": shown, "content_hash": content_hash(shown),
            "note": "The helper reloaded with these settings; no restart is needed.",
        }, default=str)),
        "allowed", f"config_entry:{entry.entry_id}",
    )
