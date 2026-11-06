"""Entity and device registry edits, removals and their MESA decisions."""

from __future__ import annotations

from collections import Counter
import dataclasses
import hashlib
import json
import logging
from typing import Any

import attr
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import (
    area_registry as ar, category_registry as cr, device_registry as dr, entity_registry as er,
    label_registry as lr,
)
from homeassistant import loader

from ..access_key_store import AccessKeyRecord
from ..const import CAP_DENY, DOMAIN, MAX_ENTITY_ALIAS_LENGTH, MAX_ENTITY_ALIASES, MESA_MODE_OFF
from ..data import PhoenixData
from ..device_semantics import (
    device_effective_area_id, device_entry_value, device_registry_snapshot,
    entity_follows_device_name, entity_full_name, entity_name_source, readable_entity_aliases, readable_full_name,
    update_device_registry_entry,
)
from ..catalog_text import diff_summary_fields as _summary
from ..capability import effective_cap
from ..redaction import _scrub_log_text, redact_diagnostics
from ..mesa import (
    area_profile_exits, evaluate_registry_action, fire_mesa_blocked_event, RegistryMesaDecision,
    unresolved_context_refused, UNRESOLVED_CONTEXT_WARNING,
)
from ..policy_engine import (
    device_cascade_entity_ids, device_config_entry_ids, device_read_check, Permission,
    resolve_device_registry_access, resolve_device_registry_write, resolve_registry_access,
)
from ..request_authority import AuthorityChangedError, check_request_authority
from ..tool_common import (
    _add_mesa_advisory, _CAP_FORBIDDEN_MESSAGE, _create_registry_mesa_approval, _gate, _record_version, _restore_ctx, _tool_error, _tool_refused_before_write, _tool_success, unknown_category_message, unknown_label_message,
)
from ..request_authority import _approved_exec_ctx
from ..tool_contracts import handler_arguments, normalize_tool_args
from .discovery import _registry_relationship_preview, _registry_relationships_preview


_LOGGER = logging.getLogger(__name__)


def _entity_meta_snapshot(entry: Any) -> dict:
    """The registry metadata fields set_entity can change (for version capture).

    `aliases` stays in HA's raw wire form (see `_alias_wire`): restore replays it, and resolving the
    COMPUTED_NAME sentinel would freeze the name and break voice matching after a rename.
    """
    return {
        "entity_id": entry.entity_id,
        "name": entry.name,
        "icon": entry.icon,
        "area_id": entry.area_id,
        "device_class": entry.device_class,
        "disabled_by": getattr(entry.disabled_by, "value", entry.disabled_by),
        "hidden_by": getattr(entry.hidden_by, "value", entry.hidden_by),
        "labels": sorted(entry.labels),
        "categories": dict(sorted(entry.categories.items())),
        "aliases": _alias_wire(entry.aliases),
    }


def _alias_wire(aliases: Any) -> list[str | None]:
    """An entity's alias list as JSON, mirroring HA's own `aliases_v2` encoding."""
    # HA serializes the COMPUTED_NAME sentinel as null, so a version snapshot round-trips exactly.
    return [None if alias is er.COMPUTED_NAME else alias for alias in (aliases or ())]


def _alias_unwire(aliases: Any) -> list:
    """The inverse of _alias_wire, for the restore path."""
    return [er.COMPUTED_NAME if alias is None else alias for alias in (aliases or ())]


def _alias_edit(value: Any, field: str) -> list[str] | str:
    """Normalize an add_aliases / remove_aliases argument, or refuse it.

    Wrong shapes and non-string members are refused rather than degraded: a coerced empty list would
    report a voice change nobody made.
    """
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        bad = next((v for v in value if not isinstance(v, str)), None)
        if bad is not None:
            return f"{field} must be a list of strings; {bad!r} is not one."
        candidates = list(value)
    else:
        return f"{field} must be a string or a list of strings."
    cleaned: list[str] = []
    for candidate in candidates:
        alias = candidate.strip()
        if not alias:
            continue
        if len(alias) > MAX_ENTITY_ALIAS_LENGTH:
            return f"alias {alias[:40]!r}... is longer than {MAX_ENTITY_ALIAS_LENGTH} characters."
        if not any(alias.casefold() == seen.casefold() for seen in cleaned):
            cleaned.append(alias)
    return cleaned


def _apply_alias_edits(
    current: list, add: list[str], remove: list[str]
) -> tuple[list, list[str], list[str]] | str:
    """The new alias list, plus what actually changed, or a refusal message.

    Add appends and remove filters strings, preserving order and the COMPUTED_NAME sentinel.
    Matching ignores case, as hassil does, so aliases differing only in case are one.
    """
    # HA seeds every entity with a sentinel meaning "match my own current name" and builds the voice
    # trie from this list alone. An absolute write dropping it would stop the entity answering to
    # its name, and an empty list would remove it from voice control, which is why this tool has no
    # set-the-aliases form.
    folded_remove = {alias.casefold() for alias in remove}
    kept = [
        alias for alias in current
        if not (isinstance(alias, str) and alias.casefold() in folded_remove)
    ]
    removed = [
        alias for alias in current
        if isinstance(alias, str) and alias.casefold() in folded_remove
    ]
    present = {alias.casefold() for alias in kept if isinstance(alias, str)}
    added: list[str] = []
    for alias in add:
        if alias.casefold() in present:
            continue
        present.add(alias.casefold())
        added.append(alias)
    result = [*kept, *added]
    if not result:
        return (
            "That would leave the entity with no names at all, so Home Assistant "
            "would stop responding to it by voice. Keep at least one alias, or "
            "delete the entity if that is what you meant."
        )
    strings = sum(1 for alias in result if isinstance(alias, str))
    if strings > MAX_ENTITY_ALIASES:
        return f"an entity may have at most {MAX_ENTITY_ALIASES} aliases; this would leave {strings}."
    return result, added, removed


def _registry_write_perm_error(perm: Any, entity_id: str) -> tuple[dict, str, str] | None:
    """Shared scope check for registry writes; None means WRITE-accessible.

    READ-only access returns a specific forbidden (the access key can already see the
    entity, so no existence oracle); anything else returns the not_found body.
    """
    if perm == Permission.WRITE:
        return None
    if perm == Permission.READ:
        return _tool_error("Read-only access to this entity; registry edits need write access."), "denied", entity_id
    if perm == Permission.NOT_FOUND:
        return _tool_error("Entity not found."), "not_found", entity_id
    return _tool_error("Entity not found."), "denied", entity_id


def _registry_area_id_error(
    args: dict, hass: HomeAssistant, entity_id: str
) -> tuple[dict, str, str] | None:
    """Validate set_entity's optional area reference; None means valid."""
    if "area_id" not in args:
        return None
    area_id = args["area_id"]
    if area_id is not None and not isinstance(area_id, str):
        return _tool_error("area_id must be a string or null."), "invalid_request", entity_id
    if area_id and ar.async_get(hass).async_get_area(area_id) is None:
        return _tool_error("Unknown area_id."), "invalid_request", entity_id
    return None


_SET_ENTITY_UPDATE_ARGUMENTS = frozenset(
    {
        "name", "use_device_name", "icon", "area_id", "device_class", "enabled",
        "hidden", "add_aliases", "remove_aliases", "add_labels", "remove_labels",
        "categories", "new_entity_id",
    }
)

_SET_ENTITY_RESTORE_ARGUMENTS = frozenset(
    {"aliases", "labels", "disabled_by", "hidden_by"}
)


def _registry_id_edit(value: Any, field: str) -> list[str] | str:
    """Normalize a label-id add/remove argument, or return a refusal message."""
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        return f"{field} must be a string or a list of strings."
    cleaned: list[str] = []
    for value_item in values:
        item = value_item.strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _apply_registry_id_edits(
    current: set[str], add: list[str], remove: list[str]
) -> tuple[set[str], list[str], list[str]]:
    """Apply set-style registry ID edits; additions win an add/remove tie."""
    result = (current - set(remove)) | set(add)
    return result, sorted(result - current), sorted(current - result)


def _registry_name_error(value: Any) -> str | None:
    """Validate a name override without confusing literal quotes with clearing."""
    if value is not None and not isinstance(value, str):
        return "name must be a string or null."
    if value and all(char.isspace() or char in "\"'" for char in value):
        return (
            "name must contain more than quotes and whitespace. "
            "Use changes.name: null to clear the name override."
        )
    return None


def _set_entity_value_error(
    args: dict,
    hass: HomeAssistant,
    entry: er.RegistryEntry,
    *,
    restoring: bool,
) -> str | None:
    """Validate set_entity values and references without changing the registry."""
    name_error = _registry_name_error(args.get("name"))
    if name_error is not None:
        return name_error
    if "use_device_name" in args:
        if not isinstance(args["use_device_name"], bool):
            return "use_device_name must be true or false."
        if "name" in args:
            return "Send either name or use_device_name, not both."
        if args["use_device_name"] and not entity_follows_device_name(hass, entry):
            return (
                "use_device_name needs an entity on a named device and Home Assistant "
                "2026.10 or later, which shows such an entity under its device's name."
            )
    for field in ("icon", "device_class"):
        if field in args and args[field] is not None and not isinstance(args[field], str):
            return f"{field} must be a string or null."
    for field in ("enabled", "hidden"):
        if field in args and not isinstance(args[field], bool):
            return f"{field} must be true or false."

    if "new_entity_id" in args:
        new_entity_id = args["new_entity_id"]
        if not isinstance(new_entity_id, str) or not valid_entity_id(new_entity_id):
            return "new_entity_id must be a valid entity ID."
        if new_entity_id == entry.entity_id:
            return "new_entity_id is already this entity's ID."
        old_domain = entry.entity_id.split(".", 1)[0]
        if new_entity_id.split(".", 1)[0] != old_domain:
            return f"new_entity_id must stay in the {old_domain} domain."
        registry = er.async_get(hass)
        if registry.async_get(new_entity_id) is not None or hass.states.get(
            new_entity_id
        ) is not None:
            return "new_entity_id is already occupied."

    for field in ("add_labels", "remove_labels"):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return parsed
        if field == "add_labels":
            missing = [label_id for label_id in parsed if lr.async_get(hass).async_get_label(label_id) is None]
            if missing:
                return unknown_label_message(hass, missing[0])
    if restoring and "labels" in args:
        labels = args["labels"]
        if not isinstance(labels, list) or not all(
            isinstance(label_id, str) for label_id in labels
        ):
            return "labels must be a list of label ids."
        missing = [
            label_id
            for label_id in labels
            if lr.async_get(hass).async_get_label(label_id) is None
        ]
        if missing:
            return unknown_label_message(hass, missing[0])

    if "categories" in args:
        categories = args["categories"]
        if not isinstance(categories, dict):
            return "categories must be an object mapping scope to category id or null."
        category_registry = cr.async_get(hass)
        for scope, category_id in categories.items():
            if not isinstance(scope, str) or not scope:
                return "category scopes must be non-empty strings."
            if category_id is not None and not isinstance(category_id, str):
                return f"category {scope!r} must be a string id or null."
            if category_id is not None and category_registry.async_get_category(
                scope=scope, category_id=category_id
            ) is None:
                return unknown_category_message(hass, scope, category_id)

    if restoring:
        enum_fields = (
            ("disabled_by", er.RegistryEntryDisabler),
            ("hidden_by", er.RegistryEntryHider),
        )
        for field, enum_type in enum_fields:
            if field not in args or args[field] is None:
                continue
            try:
                enum_type(args[field])
            except (TypeError, ValueError):
                return f"Invalid stored {field} value."

    if not restoring and "enabled" in args and entry.disabled_by not in (
        None, er.RegistryEntryDisabler.USER
    ):
        return f"This entity is disabled by {entry.disabled_by.value}, not by the user."
    if not restoring and "hidden" in args and entry.hidden_by not in (
        None, er.RegistryEntryHider.USER
    ):
        return f"This entity is hidden by {entry.hidden_by.value}, not by the user."
    if args.get("enabled") is True and entry.device_id:
        device = device_registry_snapshot(hass).get(entry.device_id)
        if device is not None and device.disabled:
            return "The entity cannot be enabled while its device is disabled."
    return None


def _set_entity_args_error(
    args: dict,
    hass: HomeAssistant,
    entity_id: str,
    *,
    allow_restore_fields: bool = False,
) -> tuple[dict, str, str] | None:
    """Validate supported fields and reject requests with no registry update."""
    area_error = _registry_area_id_error(args, hass, entity_id)
    if area_error is not None:
        return area_error
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return _tool_error("Entity has no registry entry to edit."), "invalid_request", entity_id
    update_arguments = _SET_ENTITY_UPDATE_ARGUMENTS | (
        _SET_ENTITY_RESTORE_ARGUMENTS if allow_restore_fields else set()
    )
    if not any(field in args for field in update_arguments):
        return (
            _tool_error(
                "Provide at least one of the supported entity registry updates."
            ),
            "invalid_request",
            entity_id,
        )
    value_error = _set_entity_value_error(
        args, hass, entry, restoring=allow_restore_fields
    )
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", entity_id
    return None


_REGISTRY_MESA_FINGERPRINT = "_registry_mesa_fingerprint"


def _contains_entity_identity(value: Any, entity_id: str) -> bool:
    """Whether a Phoenix configuration subtree keys or values the exact ID."""
    if isinstance(value, dict):
        return any(
            key == entity_id or _contains_entity_identity(item, entity_id)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_entity_identity(item, entity_id) for item in value)
    return value == entity_id


def _entity_identity_references(
    data: PhoenixData, entity_id: str
) -> list[dict[str, Any]]:
    """Active Phoenix configuration whose identity would not follow a rename."""
    references: list[dict[str, Any]] = []
    for record in data.store.list_access_keys():
        if not record.is_valid():
            continue
        if entity_id in record.permissions.entities:
            references.append(
                {
                    "kind": "access_key_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                }
            )
        for preset in record.presets:
            if entity_id in preset.permissions.entities:
                references.append(
                    {
                        "kind": "preset_permission",
                        "access_key_id": record.id,
                        "access_key_name": record.name,
                        "preset_id": preset.id,
                        "preset_name": preset.name,
                    }
                )
    if entity_id in data.store.get_entity_hints():
        references.append({"kind": "global_hint", "entity_id": entity_id})
    for pending in data.store.get_pending_approvals():
        if pending.get("status") != "pending":
            continue
        if pending.get("id") in data.approvals_in_progress:
            # The approval currently executing necessarily contains this entity
            # ID in its saved args; it is the operation, not a competing config
            # consumer. Other pending approvals remain blockers.
            continue
        if _contains_entity_identity(pending.get("args", {}), entity_id):
            references.append(
                {
                    "kind": "pending_approval",
                    "approval_id": pending.get("id"),
                    "tool_name": pending.get("tool_name"),
                }
            )
    return references


def _rename_blocker_error(
    data: PhoenixData, entity_id: str
) -> tuple[dict, str, str] | None:
    references = _entity_identity_references(data, entity_id)
    if not references:
        return None
    # This reaches an agent: other keys, presets and approvals stay counts.
    blockers = [{"kind": kind, "count": count}
                for kind, count in Counter(reference["kind"] for reference in references).items()]
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "Entity ID rename is blocked by Phoenix configuration keyed "
                        "to the old ID. Move or remove every blocker first."
                    ),
                    "entity_id": entity_id,
                    "blockers": blockers,
                },
                default=str,
            )
        ),
        "invalid_request",
        entity_id,
    )


def _registry_mesa_error(
    entity_id: str, action: str, decision: RegistryMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": f"MESA blocked entity registry {action}.",
                    "entity_id": entity_id,
                    "mesa": {
                        "rule": decision.rule,
                        "reason": decision.reason,
                        "effective_rule": decision.effective_rule,
                        "warnings": decision.warnings,
                    },
                },
                default=str,
            )
        ),
        "denied",
        entity_id,
    )


def _registry_mesa_decision(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    entity_id: str,
    *,
    action: str,
    service_data: dict[str, Any] | None = None,
    session_id: str,
) -> RegistryMesaDecision | tuple[dict, str, str]:
    """Resolve MESA fail-closed for a registry identity action."""
    try:
        return evaluate_registry_action(
            data,
            access_key,
            entity_id,
            action=action,
            service_data=service_data,
            session_id=session_id,
        )
    except Exception:  # a safety resolver failure must block
        _LOGGER.exception("MESA registry %s evaluation failed for %s", action, entity_id)
        return (
            _tool_error("MESA safety evaluation failed; no registry change was made."),
            "denied",
            entity_id,
        )


def _mesa_preview(decision: RegistryMesaDecision) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "rule": decision.rule,
        "reason": decision.reason,
        "effective_rule": decision.admin_rule,
        "warnings": decision.warnings,
    }


def _entity_mesa_actions(
    data: PhoenixData, hass: HomeAssistant, entry: Any, args: dict, *, restoring: bool
) -> list[tuple[str, dict[str, Any]]]:
    """The MESA actions in one set_entity edit, each with its synthetic parameters.

    Rename, disable and leaving a profiled area are actions; other metadata is not, and an edit
    leaving the state as it is gates nothing.
    """
    if entry is None:
        return []
    actions: list[tuple[str, dict[str, Any]]] = []
    if isinstance(args.get("new_entity_id"), str):
        actions.append(("rename", {"new_entity_id": args["new_entity_id"]}))
    disabled: bool | None = None
    if "enabled" in args:
        disabled = not args["enabled"]
    elif restoring and "disabled_by" in args:
        disabled = args["disabled_by"] is not None
    if disabled is not None and disabled != (entry.disabled_by is not None):
        actions.append(("disable" if disabled else "enable", {"enabled": not disabled}))
    if "area_id" in args:
        after = args["area_id"] or device_effective_area_id(hass, entry.device_id)
        if area_profile_exits(data, {entry.entity_id: after}):
            actions.append(("move_area", {}))
    return actions


def _entity_mesa_decision(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    entity_id: str,
    actions: list[tuple[str, dict[str, Any]]],
    *,
    session_id: str,
) -> RegistryMesaDecision | tuple[dict, str, str]:
    """The strictest decision across one edit's actions, with every warning."""
    strictest: RegistryMesaDecision | None = None
    warnings: list[str] = []
    for action, service_data in actions:
        resolved = _registry_mesa_decision(
            data, access_key, entity_id, action=action,
            service_data=service_data, session_id=session_id,
        )
        if isinstance(resolved, tuple):
            return resolved
        warnings.extend(warning for warning in resolved.warnings if warning not in warnings)
        order = ("allow", "confirm", "deny")
        if strictest is None or order.index(resolved.decision) > order.index(strictest.decision):
            strictest = resolved
    assert strictest is not None
    return dataclasses.replace(strictest, warnings=warnings)


def _registry_write_precheck(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    tool_name: str,
    data: PhoenixData | None = None,
) -> tuple[dict, str, str] | None:
    """Pre-gate validation for registry writes; None means OK to proceed.

    Checks entity_id presence, write scope, and registry references so a doomed
    request is rejected before approval. The executor re-validates at apply time.
    """
    entity_id = str(args.get("entity_id") or "").strip()
    if not entity_id:
        return _tool_error("entity_id is required."), "invalid_request", tool_name
    permission = resolve_registry_access(entity_id, access_key, hass)
    perm_error = _registry_write_perm_error(permission, entity_id)
    if perm_error is not None:
        return perm_error
    registry_entry = er.async_get(hass).async_get(entity_id)
    canonical_id = registry_entry.entity_id if registry_entry is not None else entity_id
    if tool_name == "set_entity":
        args_error = _set_entity_args_error(args, hass, entity_id)
        if args_error is not None:
            return args_error
        if args.get("enabled") is False and resolve_registry_access(
            entity_id, access_key, hass, force_registry_only=True
        ) != Permission.WRITE:
            return (
                _tool_error(
                    "Disabling this entity would remove the access key's only write grant. "
                    "Grant WRITE to its device or domain first."
                ),
                "denied",
                entity_id,
            )
        if "new_entity_id" in args and data is not None:
            blocker_error = _rename_blocker_error(data, canonical_id)
            if blocker_error is not None:
                return blocker_error
    return None


_SET_DEVICE_UPDATE_ARGUMENTS = frozenset(
    {"name", "area_id", "enabled", "add_labels", "remove_labels"}
)
_SET_DEVICE_RESTORE_ARGUMENTS = frozenset({"labels", "disabled_by"})
_DEVICE_MESA_FINGERPRINT = "_device_mesa_fingerprint"


@dataclasses.dataclass
class _DeviceMesaDecision:
    """Aggregate MESA verdict for every entity and action on one device."""

    decision: str
    actions: list[str]
    entities: list[dict[str, Any]]
    warnings: list[str]
    profile_fingerprint: str
    blocked: list[tuple[str, str, str]]


def _device_meta_snapshot(device: Any) -> dict[str, Any]:
    """Restorable user metadata for one device registry entry."""
    return {
        "device_id": device.id,
        "name": device.name_by_user,
        "area_id": device.area_id,
        "disabled_by": getattr(device.disabled_by, "value", device.disabled_by),
        "labels": sorted(device.labels),
    }


def _device_public_meta(device: Any) -> dict[str, Any]:
    """Safe update response extending the restorable device snapshot."""
    return {
        **_device_meta_snapshot(device),
        "name": device.name_by_user or device.name,
        "original_name": device.name,
        "name_by_user": device.name_by_user,
    }


def _device_write_perm_error(
    permission: Permission,
    device_id: str,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> tuple[dict, str, str] | None:
    """Require explicit whole-device WRITE without hiding an already-visible row."""
    if permission == Permission.WRITE:
        return None
    if permission == Permission.NOT_FOUND:
        return _tool_error("Device not found."), "not_found", device_id
    readable = resolve_device_registry_access(device_id, access_key, hass) in (
        Permission.READ,
        Permission.WRITE,
    )
    if readable:
        return (
            _tool_error(
                "Whole-device registry edits require an explicit WRITE grant on "
                "this device; attached entities and domain grants authorize reads only."
            ),
            "denied",
            device_id,
        )
    return _tool_error("Device not found."), "denied", device_id


def _set_device_value_error(
    args: dict,
    hass: HomeAssistant,
    device: Any,
    *,
    restoring: bool,
) -> str | None:
    """Validate set_device values and registry references without mutation."""
    name_error = _registry_name_error(args.get("name"))
    if name_error is not None:
        return name_error
    if "area_id" in args:
        area_id = args["area_id"]
        if area_id is not None and not isinstance(area_id, str):
            return "area_id must be a string or null."
        if area_id and ar.async_get(hass).async_get_area(area_id) is None:
            return "Unknown area_id."
    if "enabled" in args and not isinstance(args["enabled"], bool):
        return "enabled must be true or false."

    for field in ("add_labels", "remove_labels"):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return parsed
        if field == "add_labels":
            missing = [
                label_id
                for label_id in parsed
                if lr.async_get(hass).async_get_label(label_id) is None
            ]
            if missing:
                return unknown_label_message(hass, missing[0])
    if restoring and "labels" in args:
        labels = args["labels"]
        if not isinstance(labels, list) or not all(
            isinstance(label_id, str) for label_id in labels
        ):
            return "labels must be a list of label ids."
        missing = [
            label_id
            for label_id in labels
            if lr.async_get(hass).async_get_label(label_id) is None
        ]
        if missing:
            return unknown_label_message(hass, missing[0])
    if restoring and "disabled_by" in args and args["disabled_by"] is not None:
        try:
            dr.DeviceEntryDisabler(args["disabled_by"])
        except (TypeError, ValueError):
            return "Invalid stored disabled_by value."

    will_enable = args.get("enabled") is True or (
        restoring and "disabled_by" in args and args["disabled_by"] is None
    )
    if will_enable:
        disabled_owners = []
        for entry_id in device_config_entry_ids(device):
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None and entry.disabled_by is not None:
                disabled_owners.append(entry_id)
        if disabled_owners:
            return (
                "The device cannot be enabled while an owning config entry is "
                f"disabled: {disabled_owners[0]}."
            )
    if not restoring and "enabled" in args and device.disabled_by not in (
        None,
        dr.DeviceEntryDisabler.USER,
    ):
        return f"This device is disabled by {device.disabled_by.value}, not by the user."
    return None


def _set_device_args_error(
    args: dict,
    hass: HomeAssistant,
    device_id: str,
    *,
    allow_restore_fields: bool = False,
) -> tuple[dict, str, str] | None:
    """Validate supported device fields and reject no-op-shaped requests."""
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    supported = _SET_DEVICE_UPDATE_ARGUMENTS | (
        _SET_DEVICE_RESTORE_ARGUMENTS if allow_restore_fields else set()
    )
    if not any(field in args for field in supported):
        return (
            _tool_error("Provide at least one supported device registry update."),
            "invalid_request",
            device_id,
        )
    value_error = _set_device_value_error(
        args, hass, device, restoring=allow_restore_fields
    )
    if value_error is not None:
        return _tool_error(value_error), "invalid_request", device_id
    return None


def _device_child_write_error(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    device: Any,
    *,
    restoring: bool,
) -> tuple[dict, str, str] | None:
    """Block authorization-affecting edits when any child is not writable.

    Children are the device's entities and, for a main device, its child
    devices and their entities, which Core disables and places with it.
    """
    affects_children = any(field in args for field in ("name", "area_id", "enabled"))
    if restoring:
        affects_children = affects_children or "disabled_by" in args
    if not affects_children:
        return None
    snapshot = device_registry_snapshot(hass)
    blocked = [
        entity_id
        for entity_id in device_cascade_entity_ids(hass, device.id, snapshot=snapshot)
        if resolve_registry_access(
            entity_id, access_key, hass, force_registry_only=True
        )
        != Permission.WRITE
    ]
    blocked_parts = any(
        resolve_device_registry_write(part_id, access_key, hass, snapshot=snapshot)
        != Permission.WRITE
        for part_id in snapshot.part_ids(device.id)
    )
    if not blocked and not blocked_parts:
        return None
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "The device update would affect registry entities or child "
                        "devices that are not writable by this access key."
                    ),
                    "device_id": device.id,
                    "blocked_entities": blocked,
                }
            )
        ),
        "denied",
        device.id,
    )


def _device_mesa_actions(args: dict, *, restoring: bool) -> list[str]:
    actions: list[str] = []
    if "name" in args:
        actions.append("rename")
    if "enabled" in args:
        actions.append("enable" if args["enabled"] else "disable")
    elif restoring and "disabled_by" in args:
        actions.append("enable" if args["disabled_by"] is None else "disable")
    return actions


def _device_area_exits(
    data: PhoenixData, hass: HomeAssistant, device: Any, args: dict
) -> list[str]:
    """Entities a set_device area change would take out of a profiled area.

    Entities and child devices with their own area stay; a child without one follows this device,
    and clearing a child's area returns it to its parent's.
    """
    if "area_id" not in args:
        return []
    snapshot = device_registry_snapshot(hass)
    new_area = args["area_id"] or None
    own_after = new_area or snapshot.effective_area_id(snapshot.parent_id(device.id))
    registry = er.async_get(hass)
    moves: dict[str, str | None] = {}
    for entity_id in device_cascade_entity_ids(hass, device.id, snapshot=snapshot):
        entry = registry.async_get(entity_id)
        if entry is None or entry.area_id is not None:
            continue
        moves[entity_id] = (
            own_after if entry.device_id == device.id
            else device_entry_value(snapshot.get(entry.device_id), "area_id") or new_area
        )
    return area_profile_exits(data, moves)


def _device_mesa_service_data(args: dict) -> dict[str, Any]:
    """Metadata parameters for MESA after Phoenix expands the exact targets.

    Only rename and enable/disable fields belong here.
    """
    # device_id and area_id are target selectors that could expand to other entities, so mesa-core
    # rejects them beside one resolved entity as contradictory. A move out of a profiled area is
    # judged on where the entity is now.
    return {
        field: args[field]
        for field in ("name", "enabled", "disabled_by")
        if field in args
    }


def _device_mesa_decision(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    device: Any,
    *,
    actions: list[str],
    service_data: dict[str, Any],
    session_id: str,
    entity_ids: list[str] | None = None,
    area_exits: list[str] | None = None,
) -> _DeviceMesaDecision | tuple[dict, str, str]:
    """Resolve all device MESA actions across the exact attached membership.

    ``area_exits`` adds move_area for just the entities leaving a profiled area.
    """
    if area_exits:
        actions = [*actions, "move_area"]
    if entity_ids is None:
        entity_ids = device_cascade_entity_ids(hass, device.id)
    else:
        entity_ids = sorted(set(entity_ids))
    mesa_active = (
        (data.mesa is not None or data.mesa_setup_failed is True)
        and data.store.get_settings().mesa_mode != MESA_MODE_OFF
    )
    if mesa_active and not entity_ids and unresolved_context_refused(data, device.id):
        reason = (
            "MESA is active and this device has no registry entity from which "
            "mesa-core can resolve its inherited device context."
        )
        decision_doc = {
            "decision": "deny",
            "actions": actions,
            "entities": [],
            "warnings": [],
            "blocked": [{
                "entity_id": None,
                "rule": "mesa:unresolved_device_context",
                "reason": reason,
            }],
        }
        fingerprint = hashlib.sha256(
            json.dumps(decision_doc, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return _DeviceMesaDecision(
            decision="deny",
            actions=actions,
            entities=[],
            warnings=[],
            profile_fingerprint=fingerprint,
            blocked=[(
                device.id,
                "mesa:unresolved_device_context",
                reason,
            )],
        )

    resolved_entities: list[dict[str, Any]] = []
    warnings: list[str] = [UNRESOLVED_CONTEXT_WARNING] if mesa_active and not entity_ids else []
    blocked: list[tuple[str, str, str]] = []
    has_confirm = False
    try:
        for action in actions:
            for entity_id in (area_exits or []) if action == "move_area" else entity_ids:
                decision = evaluate_registry_action(
                    data,
                    access_key,
                    entity_id,
                    action=action,
                    registry_domain="device_registry",
                    service_data=service_data,
                    session_id=session_id,
                )
                resolved_entities.append({
                    "entity_id": entity_id,
                    "action": action,
                    "decision": decision.decision,
                    "rule": decision.rule,
                    "reason": decision.reason,
                    "warnings": decision.warnings,
                    "effective_rule": decision.effective_rule,
                    "admin_rule": decision.admin_rule,
                    "profile_fingerprint": decision.profile_fingerprint,
                })
                for warning in decision.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
                if decision.decision == "deny":
                    blocked.append((entity_id, decision.rule, decision.reason))
                elif decision.decision == "confirm":
                    has_confirm = True
    except Exception:  # MESA resolution must fail closed
        _LOGGER.exception("MESA device registry evaluation failed for %s", device.id)
        return (
            _tool_error("MESA safety evaluation failed; no device change was made."),
            "denied",
            device.id,
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "device_id": device.id,
                "actions": actions,
                "entities": resolved_entities,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return _DeviceMesaDecision(
        decision="deny" if blocked else "confirm" if has_confirm else "allow",
        actions=actions,
        entities=resolved_entities,
        warnings=warnings,
        profile_fingerprint=fingerprint,
        blocked=blocked,
    )


def _device_mesa_preview(decision: _DeviceMesaDecision, *, admin: bool = False) -> dict[str, Any]:
    """The decision for an agent's refusal, or with ``admin`` for an approval diff."""
    return {
        "decision": decision.decision,
        "actions": decision.actions,
        "entities": [
            {
                **{key: value for key, value in item.items() if key not in ("profile_fingerprint", "admin_rule")},
                **({"effective_rule": item["admin_rule"]} if admin else {}),
            }
            for item in decision.entities
        ],
        "warnings": decision.warnings,
        "profile_fingerprint": decision.profile_fingerprint[:16],
    }


def _device_mesa_error(
    device: Any, decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": "MESA blocked the device registry update.",
                    "device_id": device.id,
                    "mesa": _device_mesa_preview(decision),
                    "blocked": [
                        {"entity_id": eid, "rule": rule, "reason": reason}
                        for eid, rule, reason in decision.blocked
                    ],
                },
                default=str,
            )
        ),
        "denied",
        device.id,
    )


def _device_mesa_context_changed_error(
    device: Any, decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "The device's entity membership or effective MESA profile "
                        "changed after approval. Review the update again."
                    ),
                    "device_id": device.id,
                    "mesa": _device_mesa_preview(decision),
                },
                default=str,
            )
        ),
        "denied",
        device.id,
    )


def _set_device_precheck(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> tuple[dict, str, str] | None:
    device_id = str(args.get("device_id") or "").strip()
    if not device_id:
        return _tool_error("device_id is required."), "invalid_request", "set_device"
    perm_error = _device_write_perm_error(
        resolve_device_registry_write(device_id, access_key, hass),
        device_id,
        access_key,
        hass,
    )
    if perm_error is not None:
        return perm_error
    args_error = _set_device_args_error(args, hass, device_id)
    if args_error is not None:
        return args_error
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    return _device_child_write_error(
        args, access_key, hass, device, restoring=False
    )


async def _build_diff_set_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: _DeviceMesaDecision | None = None,
) -> dict[str, Any]:
    device_id = str(args.get("device_id") or "")
    device = device_registry_snapshot(hass).get(device_id)
    before = _device_meta_snapshot(device) if device is not None else {}
    fields: list[str] = []
    preview: dict[str, Any] = {}
    for field in ("name", "area_id"):
        if field in args:
            fields.append(field)
            preview[field] = {"before": before.get(field), "after": args[field]}
    if "enabled" in args:
        fields.append("enabled")
        preview["enabled"] = {
            "before": before.get("disabled_by") is None,
            "after": args["enabled"],
        }
    if device is not None and (
        "add_labels" in args or "remove_labels" in args
    ):
        fields.append("labels")
        add = _registry_id_edit(args.get("add_labels", []), "add_labels")
        remove = _registry_id_edit(args.get("remove_labels", []), "remove_labels")
        after_labels = None
        if not isinstance(add, str) and not isinstance(remove, str):
            after_labels = sorted(
                _apply_registry_id_edits(set(device.labels), add, remove)[0]
            )
        preview["labels"] = {
            "before": sorted(device.labels),
            "after": after_labels,
        }
    if device is not None and args.get("enabled") is False:
        affected = device_cascade_entity_ids(hass, device.id)
        preview["affected_entities"] = affected
        preview["relationships"] = await _registry_relationships_preview(
            hass, affected
        )
        preview["warning"] = (
            "Home Assistant will stop publishing this device's entities while "
            "the device is disabled. Consumers are not rewritten."
        )
    if device is not None and args.get("enabled") is True:
        preview["activation"] = {
            "reload_or_restart_may_be_needed": True,
            "note": (
                "The registry entry will be enabled. Its owning integration may "
                "need to reload, or Home Assistant may need to restart, before "
                "live entities return."
            ),
        }
    if mesa_decision is not None:
        preview["mesa"] = _device_mesa_preview(mesa_decision, admin=True)
    label = (
        device.name_by_user or device.name or device_id
        if device is not None
        else device_id
    )
    return {
        "kind": "system_action",
        **_summary(
            "set_device",
            fields=", ".join(fields) or "nothing",
            device_id=device_id,
        ),
        "target": {"type": "device", "id": device_id, "label": label},
        "preview": preview,
    }


async def _tool_set_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: edit user-controlled device registry metadata."""
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_device"
    args, error = normalize_tool_args("set_device", args)
    if error:
        return _tool_error(error), "invalid_request", "set_device"
    pre = _set_device_precheck(args, access_key, hass)
    if pre is not None:
        return pre
    device_id = str(args.get("device_id") or "").strip()
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    actions = _device_mesa_actions(args, restoring=False)
    area_exits = _device_area_exits(data, hass, device, args)
    mesa_decision: _DeviceMesaDecision | None = None
    approval_args = dict(args)
    if actions or area_exits:
        resolved = _device_mesa_decision(
            data,
            access_key,
            hass,
            device,
            actions=actions,
            service_data=_device_mesa_service_data(args),
            session_id=request_id or "set_device",
            area_exits=area_exits,
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        if mesa_decision.decision == "deny":
            if mesa_decision.blocked:
                fire_mesa_blocked_event(hass, access_key, mesa_decision.blocked)
            return _device_mesa_error(device, mesa_decision)
        # The normal capability gate may create the approval even when MESA's
        # own verdict is allow. Pin the complete membership/profile context in
        # either case so a device cannot gain a child or change profile in the
        # approval window and still execute under the old preview.
        approval_args[_DEVICE_MESA_FINGERPRINT] = (
            mesa_decision.profile_fingerprint
        )
    diff_builder = lambda: _build_diff_set_device(
        args, access_key, hass, mesa_decision
    )
    blocked = await _gate(
        "cap_registry_write",
        access_key,
        hass,
        data,
        tool_name="set_device",
        args=approval_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa_decision is not None and mesa_decision.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="set_device",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_set_device(args, access_key, hass, data)


@dataclasses.dataclass(frozen=True)
class _RegistryCommit:
    """A completed synchronous mutation, ready for best-effort history capture."""

    result: tuple[dict, str, str]
    resource_type: str
    resource_id: str
    action: str
    before: dict | None
    after: dict | None
    alias: str | None


async def _finish_registry_commit(
    commit: _RegistryCommit | tuple[dict, str, str],
    access_key: AccessKeyRecord,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Persist already captured history without reopening the mutation boundary."""
    if isinstance(commit, tuple):
        return commit
    await _record_version(
        data, access_key, resource_type=commit.resource_type,
        resource_id=commit.resource_id, action=commit.action,
        before=commit.before, after=commit.after, alias=commit.alias,
    )
    check_request_authority()
    return commit.result


async def _execute_set_device(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Commit registry state before awaiting history persistence."""
    return await _finish_registry_commit(
        _commit_set_device(args, access_key, hass, data), access_key, data
    )


def _commit_set_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> _RegistryCommit | tuple[dict, str, str]:
    """Validate, mutate, and capture one device result without yielding."""
    check_request_authority()
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_device"
    device_id = str(args.get("device_id") or "").strip()
    if not device_id:
        return _tool_error("device_id is required."), "invalid_request", "set_device"
    perm_error = _device_write_perm_error(
        resolve_device_registry_write(device_id, access_key, hass),
        device_id,
        access_key,
        hass,
    )
    if perm_error is not None:
        return perm_error
    registry = dr.async_get(hass)
    device = device_registry_snapshot(hass).get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id
    restoring = _restore_ctx.get() is not None
    args_error = _set_device_args_error(
        args, hass, device_id, allow_restore_fields=restoring
    )
    if args_error is not None:
        return args_error
    child_error = _device_child_write_error(
        args, access_key, hass, device, restoring=restoring
    )
    if child_error is not None:
        return child_error

    actions = _device_mesa_actions(args, restoring=restoring)
    area_exits = _device_area_exits(data, hass, device, args)
    mesa_decision: _DeviceMesaDecision | None = None
    if actions or area_exits:
        resolved = _device_mesa_decision(
            data,
            access_key,
            hass,
            device,
            actions=actions,
            service_data=_device_mesa_service_data(args),
            session_id="set_device_execute",
            area_exits=area_exits,
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        saved_fingerprint = args.get(_DEVICE_MESA_FINGERPRINT)
        if (
            _approved_exec_ctx.get()
            and isinstance(saved_fingerprint, str)
            and saved_fingerprint != mesa_decision.profile_fingerprint
        ):
            return _device_mesa_context_changed_error(device, mesa_decision)
        mesa_confirmed = (
            _approved_exec_ctx.get()
            and isinstance(saved_fingerprint, str)
            and saved_fingerprint == mesa_decision.profile_fingerprint
        )
        if mesa_decision.decision == "deny" or (
            mesa_decision.decision == "confirm" and not mesa_confirmed
        ):
            if mesa_decision.blocked:
                fire_mesa_blocked_event(hass, access_key, mesa_decision.blocked)
            return _device_mesa_error(device, mesa_decision)

    updates: dict[str, Any] = {}
    if "name" in args:
        updates["name_by_user"] = args["name"] or None
    if "area_id" in args:
        updates["area_id"] = args["area_id"] or None
    if "enabled" in args:
        updates["disabled_by"] = (
            None if args["enabled"] else dr.DeviceEntryDisabler.USER
        )
    label_add: list[str] = []
    label_remove: list[str] = []
    for field, sink in (("add_labels", label_add), ("remove_labels", label_remove)):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return _tool_error(parsed), "invalid_request", device_id
        sink.extend(parsed)
    labels_added: list[str] = []
    labels_removed: list[str] = []
    if label_add or label_remove:
        new_labels, labels_added, labels_removed = _apply_registry_id_edits(
            set(device.labels), label_add, label_remove
        )
        updates["labels"] = new_labels
    if restoring:
        if "labels" in args:
            updates["labels"] = set(args["labels"])
        if "disabled_by" in args:
            updates["disabled_by"] = (
                dr.DeviceEntryDisabler(args["disabled_by"])
                if args["disabled_by"] is not None
                else None
            )
    if not updates:
        return (
            _tool_error("Provide at least one supported device registry update."),
            "invalid_request",
            device_id,
        )

    before = _device_meta_snapshot(device)
    try:
        updated = update_device_registry_entry(registry, device_id, updates)
    except Exception as exc:  # surface a clean tool error
        _LOGGER.error("set_device failed for %s: %s", device_id, _scrub_log_text(str(exc)))
        return _tool_error("Failed to update device."), "denied", device_id
    if updated is None:
        return _tool_error("Failed to read updated device."), "denied", device_id
    after = _device_meta_snapshot(updated)

    body: dict[str, Any] = {
        "device_id": device_id,
        "updated": _device_public_meta(updated),
    }
    if label_add or label_remove:
        body["labels_added"] = labels_added
        body["labels_removed"] = labels_removed
    if "enabled" in args and args["enabled"]:
        entity_ids = device_cascade_entity_ids(hass, device_id)
        body["activation"] = {
            "reload_or_restart_may_be_needed": any(
                hass.states.get(entity_id) is None for entity_id in entity_ids
            ),
            "note": (
                "The registry entry is enabled. Its owning integration may need "
                "to reload, or Home Assistant may need to restart, before live "
                "entities return."
            ),
        }
    if mesa_decision is not None and mesa_decision.warnings:
        _add_mesa_advisory(body, mesa_decision.warnings)
    return _RegistryCommit(
        result=(_tool_success(json.dumps(body, default=str)), "allowed", device_id),
        resource_type="device", resource_id=device_id, action="edit",
        before=before, after=after, alias=updated.name_by_user or updated.name or device_id,
    )


_REMOVE_DEVICE_CONTEXT_FINGERPRINT = "_remove_device_context_fingerprint"
_REMOVE_DEVICE_MESA_FINGERPRINT = "_remove_device_mesa_fingerprint"


@dataclasses.dataclass
class _DeviceRemovalContext:
    """One integration owner's exact, revalidatable device-removal context."""

    device: Any
    config_entry: Any
    hook: Any
    owner_ids: list[str]
    affected_entity_ids: list[str]
    child_device_ids: list[str]
    part_device_ids: list[str]
    fingerprint: str


async def _async_device_removal_hook(hass: HomeAssistant, entry: Any) -> Any | None:
    """Load an integration and return its device-removal hook, if supported.

    Loading is the authoritative check, repeated in the executor, since HA's support cache can be
    uninitialized.
    """
    # The async component getter is newer than Phoenix's minimum HA version, so the synchronous
    # getter used by Home Assistant 2025.2 stays as a fallback.
    try:
        integration = await loader.async_get_integration(hass, entry.domain)
        async_get_component = getattr(integration, "async_get_component", None)
        if callable(async_get_component):
            component = await async_get_component()
        else:
            component = integration.get_component()
    except (ImportError, loader.IntegrationNotFound):
        return None
    except Exception:  # a broken integration must fail closed
        _LOGGER.exception(
            "Could not load %s to resolve device removal support", entry.domain
        )
        return None
    hook = getattr(component, "async_remove_config_entry_device", None)
    return hook if callable(hook) else None


def _device_owner_summary(entry: Any, *, supports_removal: bool | None = None) -> dict:
    """Safe config-entry owner projection for removal previews and results."""
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": redact_diagnostics(entry.title),
        "disabled_by": getattr(entry.disabled_by, "value", entry.disabled_by),
        "supports_remove_device": (
            supports_removal
            if supports_removal is not None
            else getattr(entry, "supports_remove_device", None)
        ),
    }


def _device_permission_references(
    data: PhoenixData, device_id: str
) -> list[dict[str, Any]]:
    """Active-access-key and preset permission nodes keyed to one device."""
    references: list[dict[str, Any]] = []
    for record in data.store.list_access_keys():
        if not record.is_valid():
            continue
        if device_id in record.permissions.devices:
            references.append({
                "kind": "access_key_permission",
                "access_key_id": record.id,
                "access_key_name": record.name,
            })
        for preset in record.presets:
            if device_id in preset.permissions.devices:
                references.append({
                    "kind": "preset_permission",
                    "access_key_id": record.id,
                    "access_key_name": record.name,
                    "preset_id": preset.id,
                    "preset_name": preset.name,
                })
    return references


def _device_removal_context_fingerprint(
    device_id: str,
    entry: Any,
    owner_ids: list[str],
    affected_entity_ids: list[str],
    child_device_ids: list[str],
    part_device_ids: list[str],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "device_id": device_id,
                "config_entry_id": entry.entry_id,
                "integration": entry.domain,
                "owner_ids": owner_ids,
                "affected_entity_ids": affected_entity_ids,
                "child_device_ids": child_device_ids,
                "part_device_ids": part_device_ids,
                "supports_removal": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _resolve_device_removal_context(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
) -> _DeviceRemovalContext | tuple[dict, str, str]:
    """Load support, then resolve fresh state for the same selected owner."""
    context = _device_removal_context(args, access_key, hass)
    if isinstance(context, tuple):
        return context
    owner_id: str = context.config_entry.entry_id
    hook = await _async_device_removal_hook(hass, context.config_entry)
    check_request_authority()
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_device"
    if hass.config_entries.async_get_entry(owner_id) is not context.config_entry:
        return _tool_error("The owning config entry changed. Review the removal again."), "denied", context.device.id
    fresh = _device_removal_context(
        {**args, "config_entry_id": owner_id}, access_key, hass, hook
    )
    if isinstance(fresh, tuple):
        return fresh
    if hook is None:
        return (
            _tool_error("This integration does not support device removal."),
            "invalid_request", context.device.id,
        )
    return fresh


def _device_removal_context(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    hook: Any = None,
) -> _DeviceRemovalContext | tuple[dict, str, str]:
    """Read current ownership and membership in one event-loop segment."""
    check_request_authority()
    device_id = str(args.get("device_id") or "").strip()
    if not device_id:
        return _tool_error("device_id is required."), "invalid_request", "remove_device"
    perm_error = _device_write_perm_error(
        resolve_device_registry_write(device_id, access_key, hass),
        device_id,
        access_key,
        hass,
    )
    if perm_error is not None:
        return perm_error
    device_snapshot = device_registry_snapshot(hass)
    device = device_snapshot.get(device_id)
    if device is None:
        return _tool_error("Device not found."), "not_found", device_id

    owner_ids = device_config_entry_ids(device)
    selected = args.get("config_entry_id")
    if selected is not None and (not isinstance(selected, str) or not selected.strip()):
        return (
            _tool_error("config_entry_id must be a non-empty string."),
            "invalid_request",
            device_id,
        )
    if selected is None:
        if len(owner_ids) != 1:
            message = (
                "config_entry_id is required when a device has multiple owners."
                if owner_ids
                else "This device has no owning config entry to remove."
            )
            return _tool_error(message), "invalid_request", device_id
        selected = owner_ids[0]
    else:
        selected = selected.strip()
    if selected not in owner_ids:
        return (
            _tool_error("The selected config entry does not own this device."),
            "invalid_request",
            device_id,
        )
    entry = hass.config_entries.async_get_entry(selected)
    if entry is None:
        return _tool_error("The owning config entry no longer exists."), "invalid_request", device_id
    if entry.domain == DOMAIN:
        return _tool_error("Phoenix MCP devices cannot be removed."), "denied", device_id
    # Core deletes a device's child devices, and their entities, with the
    # device. That happens only once its last owner is removed.
    part_device_ids = (
        list(device_snapshot.part_ids(device_id)) if owner_ids == [selected] else []
    )
    if any(
        resolve_device_registry_write(part_id, access_key, hass, snapshot=device_snapshot)
        != Permission.WRITE
        for part_id in part_device_ids
    ):
        return (
            _tool_error(
                "Removing this device would also remove child devices that are "
                "not writable by this access key."
            ),
            "denied",
            device_id,
        )
    removed_device_ids = {device_id, *part_device_ids}
    affected_entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in er.async_get(hass).entities.values()
        if registry_entry.device_id in removed_device_ids
        and registry_entry.config_entry_id == selected
    )
    child_device_ids = [
        child_id
        for child_id in device_snapshot.child_ids(device_id)
        if child_id not in removed_device_ids
    ]
    fingerprint = _device_removal_context_fingerprint(
        device_id,
        entry,
        owner_ids,
        affected_entity_ids,
        child_device_ids,
        part_device_ids,
    )
    return _DeviceRemovalContext(
        device=device,
        config_entry=entry,
        hook=hook,
        owner_ids=owner_ids,
        affected_entity_ids=affected_entity_ids,
        child_device_ids=child_device_ids,
        part_device_ids=part_device_ids,
        fingerprint=fingerprint,
    )


def _device_removal_mesa_error(
    context: _DeviceRemovalContext, decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": "MESA blocked device removal.",
                    "device_id": context.device.id,
                    "config_entry_id": context.config_entry.entry_id,
                    "mesa": _device_mesa_preview(decision),
                    "blocked": [
                        {"entity_id": eid, "rule": rule, "reason": reason}
                        for eid, rule, reason in decision.blocked
                    ],
                },
                default=str,
            )
        ),
        "denied",
        context.device.id,
    )


def _device_removal_context_changed_error(
    context: _DeviceRemovalContext, mesa_decision: _DeviceMesaDecision
) -> tuple[dict, str, str]:
    return (
        _tool_error(
            json.dumps(
                {
                    "error": (
                        "The device's ownership, affected entities, integration "
                        "support, child relationships, or effective MESA profile "
                        "changed after approval. Review the removal again."
                    ),
                    "device_id": context.device.id,
                    "config_entry_id": context.config_entry.entry_id,
                    "mesa": _device_mesa_preview(mesa_decision),
                },
                default=str,
            )
        ),
        "denied",
        context.device.id,
    )


async def _build_diff_remove_device(
    context: _DeviceRemovalContext,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    mesa_decision: _DeviceMesaDecision,
) -> dict[str, Any]:
    remaining_owner_ids = [
        owner_id
        for owner_id in context.owner_ids
        if owner_id != context.config_entry.entry_id
    ]
    remaining_owners = []
    for owner_id in remaining_owner_ids:
        owner = hass.config_entries.async_get_entry(owner_id)
        if owner is not None:
            remaining_owners.append(_device_owner_summary(owner))
    visible_children = [
        child_id
        for child_id in context.child_device_ids
        if resolve_device_registry_access(child_id, access_key, hass)
        in (Permission.READ, Permission.WRITE)
    ]
    stored_device_profile = (
        data.mesa.store.get_device_profile(context.device.id)
        if data.mesa is not None
        else None
    )
    preview = {
        "selected_owner": _device_owner_summary(
            context.config_entry, supports_removal=True
        ),
        "affected_entities": context.affected_entity_ids,
        "relationships": await _registry_relationships_preview(
            hass, context.affected_entity_ids
        ),
        "child_devices_losing_parent_if_device_disappears": visible_children,
        "child_devices_removed_with_device": context.part_device_ids,
        "remaining_owners": remaining_owners,
        "device_permission_references": _device_permission_references(
            data, context.device.id
        ),
        "device_mesa_profile": (
            stored_device_profile.to_dict()
            if stored_device_profile is not None
            else None
        ),
        "mesa": _device_mesa_preview(mesa_decision, admin=True),
        "warning": (
            "The owning integration will decide whether removal is accepted. "
            "Known consumers are not rewritten, and this operation cannot be restored."
        ),
    }
    label = context.device.name_by_user or context.device.name or context.device.id
    return {
        "kind": "system_action",
        **_summary(
            "remove_device",
            device_id=context.device.id,
            config_entry_id=context.config_entry.entry_id,
        ),
        "target": {"type": "device", "id": context.device.id, "label": label},
        "preview": preview,
    }


async def _tool_remove_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: remove one integration's ownership through its HA hook."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_device"
    args, error = handler_arguments("remove_device", args)
    if error:
        return _tool_refused_before_write(error), "invalid_request", "remove_device"
    resolved = await _resolve_device_removal_context(args, access_key, hass)
    if isinstance(resolved, tuple):
        return resolved
    context = resolved
    mesa = _device_mesa_decision(
        data,
        access_key,
        hass,
        context.device,
        actions=["remove"],
        # config_entry_id is also a Home Assistant target selector. Phoenix has
        # already expanded the selected owner to exact entity IDs, so passing
        # the selector into mesa-core would correctly be rejected as
        # contradictory (it could name entities outside this decision).
        service_data={},
        session_id=request_id or "remove_device",
        entity_ids=context.affected_entity_ids,
    )
    if isinstance(mesa, tuple):
        return mesa
    if mesa.decision == "deny":
        if mesa.blocked:
            fire_mesa_blocked_event(hass, access_key, mesa.blocked)
        return _device_removal_mesa_error(context, mesa)

    approval_args = {
        **args,
        "device_id": context.device.id,
        "config_entry_id": context.config_entry.entry_id,
        _REMOVE_DEVICE_CONTEXT_FINGERPRINT: context.fingerprint,
        _REMOVE_DEVICE_MESA_FINGERPRINT: mesa.profile_fingerprint,
    }
    diff_builder = lambda: _build_diff_remove_device(
        context, access_key, hass, data, mesa
    )
    blocked = await _gate(
        "cap_integration_write",
        access_key,
        hass,
        data,
        tool_name="remove_device",
        args=approval_args,
        request_id=request_id,
        client_ip=client_ip,
        diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="remove_device",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_remove_device(approval_args, access_key, hass, data)


def _device_removal_cleanup_error(device_id: str) -> tuple[dict, str, str]:
    """Report partial or uncertain effects without exposing changed context."""
    return (
        _tool_error(
            "The integration accepted removal, but authority or device "
            "context changed before registry cleanup. Phoenix skipped "
            "cleanup; integration effects may already have occurred. "
            "Inspect the device before retrying."
        ),
        "denied", device_id,
    )


async def _execute_remove_device(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate and invoke an integration-aware device removal."""
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "remove_device"
    resolved = await _resolve_device_removal_context(args, access_key, hass)
    if isinstance(resolved, tuple):
        return resolved
    context = resolved
    mesa = _device_mesa_decision(
        data,
        access_key,
        hass,
        context.device,
        actions=["remove"],
        service_data={},
        session_id="remove_device_execute",
        entity_ids=context.affected_entity_ids,
    )
    if isinstance(mesa, tuple):
        return mesa
    approved = _approved_exec_ctx.get()
    saved_context = args.get(_REMOVE_DEVICE_CONTEXT_FINGERPRINT)
    saved_mesa = args.get(_REMOVE_DEVICE_MESA_FINGERPRINT)
    if approved and (
        saved_context != context.fingerprint
        or saved_mesa != mesa.profile_fingerprint
    ):
        return _device_removal_context_changed_error(context, mesa)
    mesa_confirmed = (
        approved
        and isinstance(saved_mesa, str)
        and saved_mesa == mesa.profile_fingerprint
    )
    if mesa.decision == "deny" or (
        mesa.decision == "confirm" and not mesa_confirmed
    ):
        if mesa.blocked:
            fire_mesa_blocked_event(hass, access_key, mesa.blocked)
        return _device_removal_mesa_error(context, mesa)

    device_id = context.device.id
    selected_owner = context.config_entry.entry_id
    label = context.device.name_by_user or context.device.name or device_id
    try:
        accepted = await context.hook(hass, context.config_entry, context.device)
    except Exception as exc:  # integration hook is third-party code
        _LOGGER.error(
            "remove_device hook failed for %s via %s: %s",
            device_id,
            context.config_entry.domain,
            _scrub_log_text(str(exc)),
        )
        return (
            _tool_error("The integration failed while removing this device; Phoenix made no registry change."),
            "denied",
            device_id,
        )
    if not accepted:
        return (
            _tool_error("The integration rejected device removal; the registry was not changed by Phoenix."),
            "denied",
            device_id,
        )

    # The integration may already have acted. Refusal here cannot undo its work
    # and must never replay the hook or claim an untouched device.
    try:
        check_request_authority()
    except AuthorityChangedError:
        return _device_removal_cleanup_error(device_id)
    registry = dr.async_get(hass)
    current = registry.async_get(device_id)
    if current is not None and selected_owner in device_config_entry_ids(current):
        fresh = _device_removal_context(
            {"device_id": device_id, "config_entry_id": selected_owner},
            access_key, hass, context.hook,
        )
        changed = (
            effective_cap(access_key, "cap_integration_write") == CAP_DENY
            or isinstance(fresh, tuple)
        )
        if isinstance(fresh, _DeviceRemovalContext):
            changed = changed or (
                fresh.config_entry is not context.config_entry
                or fresh.fingerprint != context.fingerprint
            )
            current_mesa = _device_mesa_decision(
                data, access_key, hass, fresh.device, actions=["remove"],
                service_data={}, session_id="remove_device_cleanup",
                entity_ids=fresh.affected_entity_ids,
            )
            changed = changed or (
                isinstance(current_mesa, tuple)
                or current_mesa.profile_fingerprint != mesa.profile_fingerprint
            )
        if changed:
            return _device_removal_cleanup_error(device_id)
        try:
            owners = device_config_entry_ids(current)
            if len(owners) > 1:
                # HA releases with composite/multi-owner devices still need
                # the legacy selective removal path.
                registry.async_update_device(
                    device_id, remove_config_entry_id=selected_owner
                )
            else:
                # HA 2026.9 models a device as belonging to one config entry;
                # removing that owner removes the device itself.
                registry.async_remove_device(device_id)
        except Exception as exc:  # HA registry boundary
            _LOGGER.error(
                "remove_device registry cleanup failed for %s owner %s: %s",
                device_id,
                selected_owner,
                _scrub_log_text(str(exc)),
            )
            return (
                _tool_error(
                    "The integration accepted removal, but Home Assistant could "
                    "not remove its registry ownership. Inspect the device before retrying."
                ),
                "denied",
                device_id,
            )
        current = registry.async_get(device_id)
    if current is not None and selected_owner in device_config_entry_ids(current):
        return (
            _tool_error(
                "The integration accepted removal, but its device ownership remains."
            ),
            "denied",
            device_id,
        )
    disappeared = current is None
    remaining_owner_ids = device_config_entry_ids(current) if current is not None else []
    remaining_owners = []
    for owner_id in remaining_owner_ids:
        owner = hass.config_entries.async_get_entry(owner_id)
        if owner is not None:
            remaining_owners.append(_device_owner_summary(owner))

    version_before = {
        "restorable": False,
        "operation": "remove_config_entry_device",
        "device": _device_meta_snapshot(context.device),
        "selected_owner": _device_owner_summary(
            context.config_entry, supports_removal=True
        ),
        "owners_before": context.owner_ids,
        "affected_entities": context.affected_entity_ids,
        "child_devices_removed_with_device": context.part_device_ids,
        "device_disappeared": disappeared,
        "remaining_owner_ids": remaining_owner_ids,
    }
    await _record_version(
        data,
        access_key,
        resource_type="device",
        resource_id=device_id,
        action="delete",
        before=version_before,
        after=None,
        alias=label,
    )
    check_request_authority()
    body: dict[str, Any] = {
        "device_id": device_id,
        "config_entry_id": selected_owner,
        "removed": True,
        "device_disappeared": disappeared,
        "remaining_owners": remaining_owners,
    }
    if mesa.warnings:
        _add_mesa_advisory(body, mesa.warnings)
    return _tool_success(json.dumps(body, default=str)), "allowed", device_id


def _changes_composed_name(args: dict) -> bool:
    """Whether a set_entity call changes what Home Assistant builds the entity's name from."""
    return any(field in args for field in ("name", "use_device_name", "area_id"))


def _composed_name_after(hass: HomeAssistant, entry: er.RegistryEntry, args: dict) -> str | None:
    """The name Home Assistant would build once this call applies; the executor stores the same values."""
    changes: dict[str, Any] = {}
    if "name" in args:
        changes["name"] = args["name"] or None
    if "use_device_name" in args:
        changes["name"] = "" if args["use_device_name"] else None
    if "area_id" in args:
        changes["area_id"] = args["area_id"] or None
    return entity_full_name(hass, attr.evolve(entry, **changes))


async def _build_diff_set_entity(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: RegistryMesaDecision | None = None,
) -> dict:
    entity_id = str(args.get("entity_id") or "")
    entry = er.async_get(hass).async_get(entity_id)
    before = _entity_meta_snapshot(entry) if entry else {}
    fields = [f for f in ("name", "icon", "area_id", "device_class") if f in args]
    preview: dict[str, Any] = {
        f: {"before": before.get(f), "after": args.get(f)} for f in fields
    }
    if entry is not None and "use_device_name" in args:
        fields.append("use_device_name")
        preview["use_device_name"] = {
            "before": entity_name_source(hass, entry) == "device",
            "after": args["use_device_name"],
        }
    if entry is not None and _changes_composed_name(args):
        preview["composed_name"] = {
            "before": er.async_get_full_entity_name(hass, entry),
            "after": _composed_name_after(hass, entry, args),
        }
    if "enabled" in args:
        fields.append("enabled")
        preview["enabled"] = {
            "before": before.get("disabled_by") is None,
            "after": args["enabled"],
        }
    if "hidden" in args:
        fields.append("hidden")
        preview["hidden"] = {
            "before": before.get("hidden_by") is not None,
            "after": args["hidden"],
        }
    # Aliases preview as the RESOLVED spoken names on both sides, which is what
    # the operator is actually approving a change to; the sentinel is an
    # implementation detail and naming it here would ask them to review a
    # concept the tool does not expose. Best-effort like every diff builder.
    if entry is not None and ("add_aliases" in args or "remove_aliases" in args):
        fields.append("aliases")
        add = _alias_edit(args.get("add_aliases", []), "add_aliases")
        remove = _alias_edit(args.get("remove_aliases", []), "remove_aliases")
        applied = (
            _apply_alias_edits(list(entry.aliases or ()), add, remove)
            if not isinstance(add, str) and not isinstance(remove, str)
            else "unavailable"
        )
        after_names = None
        if not isinstance(applied, str):
            # Resolve the same way async_get_entity_aliases does, rather than
            # building a throwaway registry entry to hand it: one less HA
            # internal to construct, and the .strip() matches its own.
            full = er.async_get_full_entity_name(hass, entry)
            after_names = [
                full if alias is er.COMPUTED_NAME else alias.strip()
                for alias in applied[0]
            ]
        preview["aliases"] = {
            "before": er.async_get_entity_aliases(hass, entry),
            "after": after_names,
        }
    if entry is not None and ("add_labels" in args or "remove_labels" in args):
        fields.append("labels")
        add = _registry_id_edit(args.get("add_labels", []), "add_labels")
        remove = _registry_id_edit(args.get("remove_labels", []), "remove_labels")
        after_labels = None
        if not isinstance(add, str) and not isinstance(remove, str):
            after_labels = sorted(
                _apply_registry_id_edits(set(entry.labels), add, remove)[0]
            )
        preview["labels"] = {"before": sorted(entry.labels), "after": after_labels}
    if entry is not None and "categories" in args:
        fields.append("categories")
        after_categories = dict(entry.categories)
        if isinstance(args["categories"], dict):
            for scope, category_id in args["categories"].items():
                if category_id is None:
                    after_categories.pop(scope, None)
                else:
                    after_categories[scope] = category_id
        preview["categories"] = {
            "before": dict(sorted(entry.categories.items())),
            "after": dict(sorted(after_categories.items())),
        }
    if entry is not None and "new_entity_id" in args:
        fields.append("entity_id")
        preview["entity_id"] = {
            "before": entry.entity_id,
            "after": args["new_entity_id"],
        }
        preview["relationships"] = await _registry_relationship_preview(
            hass, entry.entity_id
        )
        data = hass.data.get(DOMAIN)
        if data is not None:
            preview["phoenix_configuration"] = _entity_identity_references(
                data, entry.entity_id
            )
        preview["warning"] = (
            "Home Assistant does not rewrite consumers of the old entity ID. "
            "Update every relationship listed here separately."
        )
    if mesa_decision is not None:
        preview["mesa"] = _mesa_preview(mesa_decision)
    return {
        "kind": "system_action",
        **_summary("set_entity", fields=", ".join(fields) or "nothing", entity_id=entity_id),
        "target": {"type": "entity", "id": entity_id, "label": entity_full_name(hass, entry) or entity_id},
        "preview": preview,
    }


async def _build_diff_delete_entity(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    mesa_decision: RegistryMesaDecision | None = None,
) -> dict:
    entity_id = str(args.get("entity_id") or "")
    entry = er.async_get(hass).async_get(entity_id)
    snap = _entity_meta_snapshot(entry) if entry else {}
    relationships = (
        await _registry_relationship_preview(hass, entity_id)
        if entry is not None
        else {"consumers": [], "consumer_count": 0, "searched": []}
    )
    data = hass.data.get(DOMAIN)
    preview: dict[str, Any] = {
        "name": snap.get("name"),
        "area_id": snap.get("area_id"),
        "warning": "Removes the entity's registry entry. A live entity's integration may re-create it; an orphan stays gone. Not re-creatable through Phoenix MCP.",
        "relationships": relationships,
        "phoenix_configuration": (
            _entity_identity_references(data, entity_id) if data is not None else []
        ),
    }
    if mesa_decision is not None:
        preview["mesa"] = _mesa_preview(mesa_decision)
    return {
        "kind": "system_action",
        **_summary("delete_entity", entity_id=entity_id),
        "target": {"type": "entity", "id": entity_id, "label": entity_full_name(hass, entry) or entity_id},
        "preview": preview,
    }


async def _tool_set_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: edit an entity's registry metadata (Confirm-eligible)."""
    # Capability gate first: a denied access key gets a uniform Forbidden with no entity
    # work, so the tool can never be a scope/existence oracle when the cap is off.
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_entity"
    args, error = normalize_tool_args("set_entity", args)
    if error:
        return _tool_error(error), "invalid_request", "set_entity"
    # Then validate entity scope and registry references before gating so a doomed
    # request cannot create a pending approval. The executor re-validates at apply time.
    pre = _registry_write_precheck(args, access_key, hass, "set_entity", data)
    if pre is not None:
        return pre
    mesa_decision: RegistryMesaDecision | None = None
    approval_args = dict(args)
    entity_id = str(args.get("entity_id") or "").strip()
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is not None:
        entity_id = registry_entry.entity_id
        approval_args["entity_id"] = entity_id
    mesa_actions = _entity_mesa_actions(data, hass, registry_entry, args, restoring=False)
    if mesa_actions:
        resolved = _entity_mesa_decision(
            data, access_key, entity_id, mesa_actions, session_id=request_id or "set_entity",
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        if mesa_decision.decision == "deny":
            fire_mesa_blocked_event(
                hass,
                access_key,
                [(entity_id, mesa_decision.rule, mesa_decision.reason)],
            )
            return _registry_mesa_error(
                entity_id, "/".join(action for action, _ in mesa_actions), mesa_decision
            )
        if mesa_decision.decision == "confirm":
            approval_args[_REGISTRY_MESA_FINGERPRINT] = (
                mesa_decision.profile_fingerprint
            )
    diff_builder = lambda: _build_diff_set_entity(
        args, access_key, hass, mesa_decision
    )
    blocked = await _gate(
        "cap_registry_write", access_key, hass, data,
        tool_name="set_entity", args=approval_args, request_id=request_id,
        client_ip=client_ip, diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa_decision is not None and mesa_decision.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="set_entity",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_set_entity(args, access_key, hass, data)


async def _execute_set_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Commit registry state before awaiting history persistence."""
    return await _finish_registry_commit(
        _commit_set_entity(args, access_key, hass, data), access_key, data
    )


def _commit_set_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> _RegistryCommit | tuple[dict, str, str]:
    """Validate, mutate, and capture one entity result without yielding."""
    check_request_authority()
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "set_entity"
    entity_id = str(args.get("entity_id") or "").strip()
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is not None:
        entity_id = registry_entry.entity_id
    if not entity_id:
        return _tool_error("entity_id is required."), "invalid_request", "set_entity"
    err = _registry_write_perm_error(
        resolve_registry_access(entity_id, access_key, hass), entity_id
    )
    if err is not None:
        return err
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None:
        return _tool_error("Entity has no registry entry to edit."), "invalid_request", entity_id
    restoring = _restore_ctx.get() is not None
    args_error = _set_entity_args_error(
        args, hass, entity_id, allow_restore_fields=restoring
    )
    if args_error is not None:
        return args_error
    new_entity_id = args.get("new_entity_id")
    mesa_decision: RegistryMesaDecision | None = None
    if isinstance(new_entity_id, str):
        blocker_error = _rename_blocker_error(data, entry.entity_id)
        if blocker_error is not None:
            return blocker_error
    mesa_actions = _entity_mesa_actions(data, hass, entry, args, restoring=restoring)
    if mesa_actions:
        resolved = _entity_mesa_decision(
            data, access_key, entry.entity_id, mesa_actions, session_id="set_entity_execute",
        )
        if isinstance(resolved, tuple):
            return resolved
        mesa_decision = resolved
        # An administrator's own restore is the confirmation; it never passes a
        # refusal.
        mesa_confirmed = restoring or (
            _approved_exec_ctx.get()
            and isinstance(args.get(_REGISTRY_MESA_FINGERPRINT), str)
            and args[_REGISTRY_MESA_FINGERPRINT]
            == mesa_decision.profile_fingerprint
        )
        if mesa_decision.decision == "deny" or (
            mesa_decision.decision == "confirm" and not mesa_confirmed
        ):
            fire_mesa_blocked_event(
                hass,
                access_key,
                [(entry.entity_id, mesa_decision.rule, mesa_decision.reason)],
            )
            return _registry_mesa_error(
                entry.entity_id, "/".join(action for action, _ in mesa_actions), mesa_decision
            )
    will_disable = args.get("enabled") is False or (
        restoring and args.get("disabled_by") is not None
    )
    if will_disable and resolve_registry_access(
        entity_id, access_key, hass, force_registry_only=True
    ) != Permission.WRITE:
        return (
            _tool_error(
                "Disabling this entity would remove the access key's only write grant. "
                "Grant WRITE to its device or domain first."
            ),
            "denied",
            entity_id,
        )

    updates: dict = {}
    if "name" in args:
        # A restore replays the captured override exactly: an empty name is Home
        # Assistant's "Use device name", not a cleared override.
        updates["name"] = args["name"] if restoring else args["name"] or None
    if "use_device_name" in args:
        updates["name"] = "" if args["use_device_name"] else None
    if "icon" in args:
        updates["icon"] = args["icon"] or None
    if "area_id" in args:
        area_id = args["area_id"]
        updates["area_id"] = area_id or None
    if "device_class" in args:
        updates["device_class"] = args["device_class"] or None
    if "enabled" in args:
        updates["disabled_by"] = (
            None if args["enabled"] else er.RegistryEntryDisabler.USER
        )
    if "hidden" in args:
        updates["hidden_by"] = (
            er.RegistryEntryHider.USER if args["hidden"] else None
        )
    if isinstance(new_entity_id, str):
        updates["new_entity_id"] = new_entity_id
    alias_add: list[str] = []
    alias_remove: list[str] = []
    for field, sink in (("add_aliases", alias_add), ("remove_aliases", alias_remove)):
        if field not in args:
            continue
        parsed = _alias_edit(args[field], field)
        if isinstance(parsed, str):
            return _tool_error(parsed), "invalid_request", entity_id
        sink.extend(parsed)
    added: list[str] = []
    removed: list[str] = []
    if alias_add or alias_remove:
        applied = _apply_alias_edits(list(entry.aliases or ()), alias_add, alias_remove)
        if isinstance(applied, str):
            return _tool_error(applied), "invalid_request", entity_id
        new_aliases, added, removed = applied
        updates["aliases"] = new_aliases
    # A restore re-applies the snapshot's alias list ABSOLUTELY, which the tool
    # itself deliberately cannot do. Reproducing the admin's chosen snapshot is
    # the whole operation (a YAML restore is likewise exempt from the dropped-key
    # guard), and the raw wire form carries the sentinel and the order back
    # unchanged. _restore_ctx is read here in the event loop, not passed by the
    # caller, so no agent-reachable path can set it.
    if "aliases" in args and restoring:
        updates["aliases"] = _alias_unwire(args["aliases"])
    label_add: list[str] = []
    label_remove: list[str] = []
    for field, sink in (("add_labels", label_add), ("remove_labels", label_remove)):
        if field not in args:
            continue
        parsed = _registry_id_edit(args[field], field)
        if isinstance(parsed, str):
            return _tool_error(parsed), "invalid_request", entity_id
        sink.extend(parsed)
    labels_added: list[str] = []
    labels_removed: list[str] = []
    if label_add or label_remove:
        new_labels, labels_added, labels_removed = _apply_registry_id_edits(
            set(entry.labels), label_add, label_remove
        )
        updates["labels"] = new_labels
    if "categories" in args and not restoring:
        categories = dict(entry.categories)
        for scope, category_id in args["categories"].items():
            if category_id is None:
                categories.pop(scope, None)
            else:
                categories[scope] = category_id
        updates["categories"] = categories
    if restoring:
        if "labels" in args:
            updates["labels"] = set(args["labels"])
        if "categories" in args:
            updates["categories"] = dict(args["categories"])
        if "disabled_by" in args:
            updates["disabled_by"] = (
                er.RegistryEntryDisabler(args["disabled_by"])
                if args["disabled_by"] is not None else None
            )
        if "hidden_by" in args:
            updates["hidden_by"] = (
                er.RegistryEntryHider(args["hidden_by"])
                if args["hidden_by"] is not None else None
            )
    if not updates:
        return (
            _tool_error("Provide at least one of the supported entity registry updates."),
            "invalid_request", entity_id,
        )

    before = _entity_meta_snapshot(reg.async_get(entity_id))
    try:
        reg.async_update_entity(entity_id, **updates)
    except Exception as exc:  # surface a clean tool error
        _LOGGER.error("set_entity failed for %s: %s", entity_id, _scrub_log_text(str(exc)))
        return _tool_error("Failed to update entity."), "denied", entity_id
    updated_id = new_entity_id if isinstance(new_entity_id, str) else entity_id
    updated_entry = reg.async_get(updated_id)
    if updated_entry is None:
        _LOGGER.error(
            "set_entity updated %s but no registry entry exists at %s",
            entity_id,
            updated_id,
        )
        return _tool_error("Failed to read updated entity."), "denied", entity_id
    after = _entity_meta_snapshot(updated_entry)

    updated_state = hass.states.get(updated_id)
    shown_name = updated_state.attributes.get("friendly_name") if updated_state is not None else None
    parent_readable = device_read_check(access_key, hass)
    public_after = {
        **after,
        "composed_name": readable_full_name(hass, updated_entry, shown_name, parent_readable),
        "name_source": entity_name_source(hass, updated_entry),
        "aliases": readable_entity_aliases(hass, updated_entry, shown_name, parent_readable),
    }
    body: dict = {"entity_id": updated_id, "updated": public_after}
    if updated_id != entity_id:
        body["previous_entity_id"] = entity_id
    if alias_add or alias_remove:
        # What CHANGED, not what was asked for: an alias already present or one
        # that was not there is a no-op, and a caller that cannot see that reads
        # a typo as a successful edit.
        body["aliases_added"] = added
        body["aliases_removed"] = removed
        updated = reg.async_get(updated_id)
        if updated is not None:
            body["aliases"] = readable_entity_aliases(hass, updated, shown_name, parent_readable)
    if label_add or label_remove:
        body["labels_added"] = labels_added
        body["labels_removed"] = labels_removed
    if "enabled" in args and args["enabled"]:
        if updated_entry is not None:
            config_entry = (
                hass.config_entries.async_get_entry(updated_entry.config_entry_id)
                if updated_entry.config_entry_id else None
            )
            body["activation"] = {
                "requires_restart": config_entry is None or not config_entry.supports_unload,
                "note": (
                    "The registry entry is enabled. Its integration may need to be "
                    "reloaded or Home Assistant restarted before a live state appears."
                ),
            }
    if mesa_decision is not None and mesa_decision.warnings:
        _add_mesa_advisory(body, mesa_decision.warnings)
    return _RegistryCommit(
        result=(_tool_success(json.dumps(body, default=str)), "allowed", updated_id),
        resource_type="entity", resource_id=updated_id, action="edit",
        before=before, after=after, alias=entity_full_name(hass, updated_entry) or updated_id,
    )


async def _tool_delete_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: delete an entity's registry entry (Confirm-eligible)."""
    # Capability gate first: a denied access key gets a uniform Forbidden with no entity
    # work, so the tool can never be a scope/existence oracle when the cap is off.
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_entity"
    args, error = handler_arguments("delete_entity", args)
    if error:
        return _tool_refused_before_write(error), "invalid_request", "delete_entity"
    pre = _registry_write_precheck(args, access_key, hass, "delete_entity", data)
    if pre is not None:
        return pre
    entity_id = str(args.get("entity_id") or "").strip()
    resolved = _registry_mesa_decision(
        data,
        access_key,
        entity_id,
        action="delete",
        session_id=request_id or "delete_entity",
    )
    if isinstance(resolved, tuple):
        return resolved
    mesa_decision = resolved
    if mesa_decision.decision == "deny":
        fire_mesa_blocked_event(
            hass,
            access_key,
            [(entity_id, mesa_decision.rule, mesa_decision.reason)],
        )
        return _registry_mesa_error(entity_id, "delete", mesa_decision)
    approval_args = {**args, "entity_id": entity_id}
    if mesa_decision.decision == "confirm":
        approval_args[_REGISTRY_MESA_FINGERPRINT] = (
            mesa_decision.profile_fingerprint
        )
    diff_builder = lambda: _build_diff_delete_entity(
        args, access_key, hass, mesa_decision
    )
    blocked = await _gate(
        "cap_registry_write", access_key, hass, data,
        tool_name="delete_entity", args=approval_args, request_id=request_id,
        client_ip=client_ip, diff=diff_builder,
    )
    if blocked is not None:
        return blocked
    if mesa_decision.decision == "confirm":
        return await _create_registry_mesa_approval(
            hass,
            data,
            access_key,
            tool_name="delete_entity",
            args=approval_args,
            diff=await diff_builder(),
            request_id=request_id,
            client_ip=client_ip,
        )
    return await _execute_delete_entity(args, access_key, hass, data)


async def _execute_delete_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Commit registry state before awaiting history persistence."""
    return await _finish_registry_commit(
        _commit_delete_entity(args, access_key, hass, data), access_key, data
    )


def _commit_delete_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> _RegistryCommit | tuple[dict, str, str]:
    """Validate, mutate, and capture one entity result without yielding."""
    check_request_authority()
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_entity"
    entity_id = str(args.get("entity_id") or "").strip()
    if not entity_id:
        return _tool_error("entity_id is required."), "invalid_request", "delete_entity"
    err = _registry_write_perm_error(
        resolve_registry_access(entity_id, access_key, hass), entity_id
    )
    if err is not None:
        return err
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None:
        return _tool_error("Entity has no registry entry to delete."), "invalid_request", entity_id
    resolved = _registry_mesa_decision(
        data,
        access_key,
        entry.entity_id,
        action="delete",
        session_id="delete_entity_execute",
    )
    if isinstance(resolved, tuple):
        return resolved
    mesa_decision = resolved
    mesa_confirmed = (
        _approved_exec_ctx.get()
        and isinstance(args.get(_REGISTRY_MESA_FINGERPRINT), str)
        and args[_REGISTRY_MESA_FINGERPRINT]
        == mesa_decision.profile_fingerprint
    )
    if mesa_decision.decision == "deny" or (
        mesa_decision.decision == "confirm" and not mesa_confirmed
    ):
        fire_mesa_blocked_event(
            hass,
            access_key,
            [(entry.entity_id, mesa_decision.rule, mesa_decision.reason)],
        )
        return _registry_mesa_error(entry.entity_id, "delete", mesa_decision)
    before = _entity_meta_snapshot(entry)
    label = entity_full_name(hass, entry)
    try:
        reg.async_remove(entity_id)
    except Exception as exc:  # surface a clean tool error
        _LOGGER.error("delete_entity failed for %s: %s", entity_id, _scrub_log_text(str(exc)))
        return _tool_error("Failed to delete entity."), "denied", entity_id

    # Read back: the registry entry is gone. A live entity's integration can still
    # report state or re-create the entry later; the tool description says so.
    body: dict[str, Any] = {"entity_id": entity_id, "deleted": True, "verified": reg.async_get(entity_id) is None}
    if mesa_decision.warnings:
        _add_mesa_advisory(body, mesa_decision.warnings)
    return _RegistryCommit(
        result=(_tool_success(json.dumps(body)), "allowed", entity_id),
        resource_type="entity", resource_id=entity_id, action="delete",
        before=before, after=None, alias=label or entity_id,
    )
