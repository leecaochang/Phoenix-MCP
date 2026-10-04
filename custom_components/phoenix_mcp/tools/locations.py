"""Guarded area/floor registry writes with synchronous commits and exact reviews."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from ..const import (
    CAP_CONFIRM, CAP_DENY, LOCATION_REGISTRY_BINDING, LOCATION_REGISTRY_COLLECTION_LIMIT,
    LOCATION_REGISTRY_FIELDS, LOCATION_REGISTRY_TEXT_LIMIT, MESA_MODE_OFF,
)
from ..data import PhoenixData
from ..device_semantics import device_registry_snapshot
from ..helpers import effective_cap, diff_summary_fields
from ..mesa import evaluate_registry_action, fire_mesa_blocked_event
from ..policy_engine import (
    LocationRegistryContext, Permission, device_config_entry_ids,
    location_registry_context, resolve_location_registry_write,
)
from ..request_authority import check_request_authority
from ..access_key_store import AccessKeyRecord
from ..tool_common import (
    _approved_exec_ctx, _create_registry_mesa_approval, _gate, _record_version,
    _restore_ctx, _tool_error, _tool_success,
)

_LOGGER = logging.getLogger(__name__)
_TOOL = "set_area_or_floor"


class LocationRefusal(ValueError):
    """A scoped, value-free refusal before any registry mutation."""

    def __init__(self, message: str, outcome: str = "invalid_request") -> None:
        super().__init__(message)
        self.outcome = outcome


def _registry(kind: str, hass: HomeAssistant) -> Any:
    return ar.async_get(hass) if kind == "area" else fr.async_get(hass)


def _snapshot(kind: str, entry: Any) -> dict[str, Any]:
    fields = ("name", "icon", "aliases", "floor_id", "labels") if kind == "area" else (
        "name", "icon", "aliases", "level",
    )
    result = {field: getattr(entry, field) for field in fields if hasattr(entry, field)}
    for field in ("aliases", "labels"):
        if field in result:
            result[field] = sorted(result[field])
    result["created_at"] = str(entry.created_at)
    return result


def _text(value: Any, field: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if (not isinstance(value, str) or not value.strip()
            or len(value) > LOCATION_REGISTRY_TEXT_LIMIT):
        raise LocationRefusal(f"{field} must be a nonempty bounded string" + (" or null." if nullable else "."))


def _collection(value: Any, field: str) -> set[str]:
    if not isinstance(value, list) or len(value) > LOCATION_REGISTRY_COLLECTION_LIMIT:
        raise LocationRefusal(f"{field} must be a bounded array of strings.")
    for item in value:
        _text(item, field)
    return set(value)


@dataclass
class LocationPlan:
    """All preparation for a synchronous commit or exact approval preview."""

    kind: str
    operation: str
    location_id: str | None
    context: LocationRegistryContext | None
    updates: dict[str, Any]
    before: dict[str, Any] | None
    binding: str
    mesa: list[dict[str, Any]]


def _prepare(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    *, internal: bool = False,
) -> LocationPlan:
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        raise LocationRefusal("Forbidden.", "denied")
    allowed = {"kind", "operation", "location_id", "changes"}
    if internal:
        allowed.add(LOCATION_REGISTRY_BINDING)
    if set(args) - allowed:
        raise LocationRefusal("Unsupported location registry arguments.")
    kind, operation = args.get("kind"), args.get("operation")
    if kind not in ("area", "floor") or operation not in ("create", "update", "delete"):
        raise LocationRefusal("kind must be area or floor; operation must be create, update or delete.")
    location_id = args.get("location_id")
    context = None
    before = None
    if operation == "create":
        if "location_id" in args:
            raise LocationRefusal("Creation uses a Home Assistant generated ID; omit location_id.")
        if not access_key.pass_through:
            raise LocationRefusal("Creating a location requires pass-through authority.", "denied")
    else:
        _text(location_id, "location_id")
        assert isinstance(location_id, str)
        context = location_registry_context(kind, location_id, hass)
        if resolve_location_registry_write(kind, location_id, access_key, hass, context=context) != Permission.WRITE:
            raise LocationRefusal("Location not found or not writable.", "denied")
        assert context is not None
        before = _snapshot(kind, context.entry)
    changes = args.get("changes", {})
    if not isinstance(changes, dict) or set(changes) - LOCATION_REGISTRY_FIELDS:
        raise LocationRefusal("changes must contain only supported location metadata fields.")
    if operation == "delete":
        if "changes" in args:
            raise LocationRefusal("Delete does not accept changes.")
    elif not changes or (operation == "create" and "name" not in changes):
        raise LocationRefusal("Provide changes; creation requires a name.")
    if kind == "floor" and set(changes) & {"floor_id", "labels", "add_labels", "remove_labels"}:
        raise LocationRefusal("Floors do not support area fields or labels.")
    if kind == "area" and "level" in changes:
        raise LocationRefusal("level is a floor field.")
    if "labels" in changes and set(changes) & {"add_labels", "remove_labels"}:
        raise LocationRefusal("Use labels to replace, or add_labels/remove_labels to patch, not both.")
    updates = {}
    for field in ("name", "icon", "floor_id"):
        if field in changes:
            _text(changes[field], field, nullable=field != "name")
            updates[field] = changes[field]
    if "name" in changes:
        registry = _registry(kind, hass)
        lookup = registry.async_get_area_by_name if kind == "area" else registry.async_get_floor_by_name
        occupied = lookup(changes["name"])
        if occupied is not None and (context is None or occupied != context.entry):
            raise LocationRefusal("Location name is unavailable.")
    if changes.get("floor_id") is not None:
        if resolve_location_registry_write("floor", changes["floor_id"], access_key, hass) != Permission.WRITE:
            raise LocationRefusal("Destination floor not found or not writable.", "denied")
    if "level" in changes:
        value = changes["level"]
        if value is not None and (type(value) is not int or not -(2**31) <= value < 2**31):
            raise LocationRefusal("level must be a bounded integer or null.")
        updates["level"] = value
    if "aliases" in changes:
        updates["aliases"] = _collection(changes["aliases"], "aliases")
    collections = {field: _collection(changes[field], field)
                   for field in ("labels", "add_labels", "remove_labels") if field in changes}
    if collections:
        labels = lr.async_get(hass)
        if any(labels.async_get_label(label_id) is None
               for values in collections.values() for label_id in values):
            raise LocationRefusal("A referenced label ID is unavailable.")
        added, removed = collections.get("add_labels", set()), collections.get("remove_labels", set())
        if added & removed:
            raise LocationRefusal("A label cannot be both added and removed.")
        current = set(context.entry.labels) if context is not None else set()
        updates["labels"] = collections.get("labels", (current | added) - removed)
    method = getattr(type(_registry(kind, hass)), f"async_{operation}")
    # Optional fields vary across HA releases. Refuse unsupported API fields.
    if any(field not in method.__code__.co_varnames[:method.__code__.co_argcount + method.__code__.co_kwonlyargcount]
           for field in updates):
        raise LocationRefusal("This Home Assistant version does not support a requested location field.")
    membership: dict[str, Any] = {}
    mesa = []
    if context is not None:
        registry = er.async_get(hass)
        devices = device_registry_snapshot(hass)
        membership = {
            "areas": {area_id: _snapshot("area", ar.async_get(hass).async_get_area(area_id))
                      for area_id in context.area_ids},
            "entities": [{field: getattr(registry.async_get(entity_id), field, None)
                          for field in ("entity_id", "unique_id", "platform", "config_entry_id",
                                        "device_id", "area_id", "disabled_by", "created_at")}
                         for entity_id in context.entity_ids],
            "devices": [{"id": device_id, "owners": device_config_entry_ids(devices.get(device_id)),
                         "identity": sorted(getattr(devices.get(device_id), "identifiers", ())),
                         "created_at": str(getattr(devices.get(device_id), "created_at", None)),
                         "area_id": devices.effective_area_id(device_id)} for device_id in context.device_ids],
        }
        action = "delete" if operation == "delete" else ("rename" if "name" in changes else None)
        if action:
            mesa_active = data.store.get_settings().mesa_mode != MESA_MODE_OFF and (
                data.mesa is not None or data.mesa_setup_failed is True)
            # Empty locations have no inherited entity/device effects. Floors
            # have no MESA layer; an empty area with a profile cannot be evaluated
            # through mesa-core's entity API and therefore refuses identity writes.
            unresolved_empty_area = not context.entity_ids and any(
                data.mesa_setup_failed is True or (data.mesa is not None
                    and data.mesa.store.get_area_profile(area_id) is not None)
                for area_id in context.area_ids
            )
            if mesa_active and (unresolved_empty_area or any(
                not any(getattr(registry.async_get(entity_id), "device_id", None) == device_id
                        for entity_id in context.entity_ids) for device_id in context.device_ids
            )):
                raise LocationRefusal("MESA cannot resolve the complete location context for this identity action.", "denied")
            for entity_id in context.entity_ids:
                try:
                    decision = evaluate_registry_action(
                        data, access_key, entity_id, action=action, registry_domain=f"{kind}_registry",
                        service_data={"name": changes["name"]} if "name" in changes else {},
                        session_id=_TOOL,
                    )
                except Exception:
                    _LOGGER.exception("MESA location evaluation failed")
                    raise LocationRefusal("MESA safety evaluation failed; no location change was made.", "denied") from None
                if decision.decision == "deny":
                    fire_mesa_blocked_event(hass, access_key, [(entity_id, decision.rule, decision.reason)])
                    raise LocationRefusal("MESA blocked the location identity action.", "denied")
                mesa.append({"entity_id": entity_id, "decision": decision.decision,
                             "profile_fingerprint": decision.profile_fingerprint,
                             "rule": decision.rule, "reason": decision.reason})
    destination = None
    if changes.get("floor_id") is not None:
        destination = _snapshot("floor", fr.async_get(hass).async_get_floor(changes["floor_id"]))
    binding = hashlib.sha256(json.dumps(
        {"before": before, "membership": membership, "destination": destination, "mesa": mesa},
        sort_keys=True, default=str, separators=(",", ":"),
    ).encode()).hexdigest()
    return LocationPlan(kind, operation, location_id, context, updates, before, binding, mesa)


def _preview(plan: LocationPlan) -> dict[str, Any]:
    updates = {key: sorted(value) if isinstance(value, set) else value for key, value in plan.updates.items()}
    return {
        "kind": "system_action",
        **diff_summary_fields(f"location.{plan.operation}", name=(plan.before or updates).get("name", plan.kind)),
        "target": {"type": plan.kind, "id": plan.location_id or "new", "label": (plan.before or updates).get("name", plan.kind)},
        "preview": {
            "before": plan.before, "changes": updates, "mesa": plan.mesa,
            "affected_entities": list(plan.context.entity_ids) if plan.context else [],
            "affected_devices": list(plan.context.device_ids) if plan.context else [],
            "affected_areas": list(plan.context.area_ids) if plan.context else [],
            "warning": "Deletion clears placement references and cannot be restored automatically. Inspect current state after an interrupted write; do not replay it.",
        },
    }


async def _tool_set_area_or_floor(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Admit exact location writes through the existing registry capability gate."""
    try:
        plan = _prepare(args, access_key, hass, data)
    except LocationRefusal as exc:
        return _tool_error(str(exc)), exc.outcome, _TOOL
    saved = {**args, LOCATION_REGISTRY_BINDING: plan.binding}
    blocked = await _gate(
        "cap_registry_write", access_key, hass, data, tool_name=_TOOL, args=saved,
        request_id=request_id, client_ip=client_ip, diff=lambda: _preview(plan),
    )
    if blocked is not None:
        return blocked
    if any(item["decision"] == "confirm" for item in plan.mesa):
        return await _create_registry_mesa_approval(
            hass, data, access_key, tool_name=_TOOL, args=saved, diff=_preview(plan),
            request_id=request_id, client_ip=client_ip,
        )
    return await _execute_set_area_or_floor(args, access_key, hass, data)


async def _execute_set_area_or_floor(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Complete validation, fresh merge, mutation and readback before the first await."""
    check_request_authority()
    try:
        plan = _prepare(args, access_key, hass, data, internal=True)
        approved = _approved_exec_ctx.get()
        if (effective_cap(access_key, "cap_registry_write") == CAP_CONFIRM or any(
                item["decision"] == "confirm" for item in plan.mesa)) and not (approved or _restore_ctx.get() is not None):
            raise LocationRefusal("This location write requires approval.", "denied")
        if approved and args.get(LOCATION_REGISTRY_BINDING) != plan.binding:
            raise LocationRefusal("Location identity, membership, metadata or MESA context changed; request a new review.", "denied")
    except LocationRefusal as exc:
        return _tool_error(str(exc)), exc.outcome, _TOOL
    registry = _registry(plan.kind, hass)
    after = None
    location_id = plan.location_id
    try:
        if plan.operation == "create":
            entry = registry.async_create(**plan.updates)
            location_id = entry.id if plan.kind == "area" else entry.floor_id
            after = _snapshot(plan.kind, entry)
        elif plan.operation == "update":
            after = _snapshot(plan.kind, registry.async_update(location_id, **plan.updates))
        else:
            # Floor removal's HA event listener runs later. Clear its reviewed
            # area references now, within the same uninterrupted commit.
            if plan.kind == "floor":
                assert plan.context is not None
                for area_id in plan.context.area_ids:
                    ar.async_get(hass).async_update(area_id, floor_id=None)
            registry.async_delete(location_id)
        assert isinstance(location_id, str)
        readback = registry.async_get_area(location_id) if plan.kind == "area" else registry.async_get_floor(location_id)
        if plan.operation == "delete":
            if readback is not None:
                raise RuntimeError("Location deletion readback mismatch")
        else:
            if readback is None:
                raise RuntimeError("Location readback unavailable")
            after = _snapshot(plan.kind, readback)
        if after is not None and any(after.get(field) != (sorted(value) if isinstance(value, set) else value)
                                     for field, value in plan.updates.items()):
            raise RuntimeError("Location readback mismatch")
    except Exception:
        _LOGGER.exception("Location registry write failed after dispatch")
        return _tool_error(json.dumps({
            "status": "unknown", "location_id": location_id,
            "message": "The write may have applied partially. Inspect current registry state before any retry; no automatic replay or rollback was attempted.",
        })), "invalid_request", _TOOL
    # Capture the receipt before history persistence lets other writers run.
    receipt = _tool_success(json.dumps({
        "kind": plan.kind, "operation": plan.operation, "location_id": location_id,
        "write_success": True, "updated": after, "restorable": plan.operation != "delete",
    }))
    assert isinstance(location_id, str)
    before = dict(plan.before) if plan.before is not None else None
    if plan.operation == "delete" and before is not None:
        before["restorable"] = False
    await _record_version(
        data, access_key, resource_type="area" if plan.kind == "area" else "floor", resource_id=location_id,
        action={"create": "create", "update": "edit", "delete": "delete"}[plan.operation],
        before=before, after=after, alias=(after or before or {}).get("name"),
    )
    check_request_authority()
    return receipt, "allowed", location_id


async def restore_location_version(
    kind: str, location_id: str, target: dict, access_key: AccessKeyRecord,
    hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Restore supported metadata only to the same surviving location identity."""
    context = location_registry_context(kind, location_id, hass)
    if (_restore_ctx.get() is None or context is None or target.get("restorable") is False
            or target.get("created_at") != str(context.entry.created_at)):
        return _tool_error("Deleted or replaced locations cannot be restored automatically."), "invalid_request", _TOOL
    changes = {key: value for key, value in target.items() if key in LOCATION_REGISTRY_FIELDS}
    return await _execute_set_area_or_floor(
        {"kind": kind, "operation": "update", "location_id": location_id, "changes": changes},
        access_key, hass, data,
    )
