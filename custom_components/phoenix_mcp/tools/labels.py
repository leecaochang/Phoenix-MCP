"""Labels and categories: listing them, and guarded create, rename and delete.

Labels and categories are not nodes in the permission tree; what a key may do to
one follows from what carries it, as for areas and floors (tools/locations.py).
A label is listed when something the access key can read carries it, and changed
only when the key can write everything that carries it, since renaming or
deleting it changes those things too. One that nothing carries, and creating a
new one, need pass-through authority. Categories work the same within their
scope (automation, script, scene, helpers), carried by entities.

Every change is reviewed against a binding of the item and its holders, applied
without an await between the final check and the registry write, read back, and
recorded in version history so an update can be restored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from ..access_key_store import AccessKeyRecord
from ..const import CAP_CONFIRM, CAP_DENY, LOCATION_REGISTRY_TEXT_LIMIT
from ..data import PhoenixData
from ..device_semantics import device_registry_snapshot
from ..helpers import diff_summary_fields, effective_cap
from ..policy_engine import (
    Permission, resolve_device_registry_access, resolve_location_registry_write, resolve_registry_access,
)
from ..request_authority import check_request_authority
from ..tool_common import _gate, _record_version, _restore_ctx, _tool_error, _tool_success
from ..request_authority import _approved_exec_ctx

_LOGGER = logging.getLogger(__name__)
_TOOL = "set_label_or_category"
BINDING = "_label_registry_binding"
CATEGORY_SCOPES = ("automation", "script", "scene", "helpers")
_FIELDS = {"label": ("name", "color", "icon", "description"), "category": ("name", "icon")}
_NOT_FOUND = "Label or category not found or not writable."
_READABLE = (Permission.READ, Permission.WRITE)


class LabelRefusal(ValueError):
    """A scoped, value-free refusal before any registry change."""

    def __init__(self, message: str, outcome: str = "invalid_request") -> None:
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class Holders:
    """What carries one label or category."""

    entities: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.entities or self.devices or self.areas)


def _devices(hass: HomeAssistant) -> list[Any]:
    """Main and child devices alike, from the one snapshot that joins them."""
    return list(device_registry_snapshot(hass).by_id.values())


def label_holders(hass: HomeAssistant, label_id: str) -> Holders:
    return Holders(
        entities=tuple(sorted(e.entity_id for e in er.async_get(hass).entities.values() if label_id in e.labels)),
        devices=tuple(sorted(d.id for d in _devices(hass) if label_id in d.labels)),
        areas=tuple(sorted(a.id for a in ar.async_get(hass).async_list_areas() if label_id in a.labels)),
    )


def category_holders(hass: HomeAssistant, scope: str, category_id: str) -> Holders:
    return Holders(entities=tuple(sorted(
        e.entity_id for e in er.async_get(hass).entities.values() if (e.categories or {}).get(scope) == category_id)))


def _readable(holders: Holders, access_key: AccessKeyRecord, hass: HomeAssistant) -> bool:
    """Whether the key can see something that carries the item; areas count for pass-through only."""
    return (access_key.pass_through
            or any(resolve_registry_access(entity_id, access_key, hass) in _READABLE for entity_id in holders.entities)
            or any(resolve_device_registry_access(device_id, access_key, hass) in _READABLE
                   for device_id in holders.devices))


def _writable(holders: Holders, access_key: AccessKeyRecord, hass: HomeAssistant) -> bool:
    """Whole-membership authority: every holder writable, and an unheld item needs pass-through."""
    if holders.empty:
        return access_key.pass_through
    return (all(resolve_registry_access(entity_id, access_key, hass) is Permission.WRITE for entity_id in holders.entities)
            and all(resolve_device_registry_access(device_id, access_key, hass) is Permission.WRITE
                    for device_id in holders.devices)
            and all(resolve_location_registry_write("area", area_id, access_key, hass) is Permission.WRITE
                    for area_id in holders.areas))


def _snapshot(kind: str, entry: Any) -> dict[str, Any]:
    result = {field: getattr(entry, field, None) for field in _FIELDS[kind]}
    result["created_at"] = str(getattr(entry, "created_at", None))
    return result


def _entry(kind: str, hass: HomeAssistant, item_id: str, scope: str | None) -> Any:
    if kind == "label":
        return lr.async_get(hass).async_get_label(item_id)
    return cr.async_get(hass).async_get_category(scope=scope or "", category_id=item_id)


def _holders(kind: str, hass: HomeAssistant, item_id: str, scope: str | None) -> Holders:
    return label_holders(hass, item_id) if kind == "label" else category_holders(hass, scope or "", item_id)


async def _tool_list_labels(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: the labels and categories carried by something this access key can read."""
    labels = []
    for label in sorted(lr.async_get(hass).async_list_labels(), key=lambda item: item.name.casefold()):
        holders = label_holders(hass, label.label_id)
        if not _readable(holders, access_key, hass) or (holders.empty and not access_key.pass_through):
            continue
        labels.append({"label_id": label.label_id, **{k: v for k, v in _snapshot("label", label).items()
                                                      if k != "created_at" and v is not None},
                       "entities": sum(resolve_registry_access(e, access_key, hass) in _READABLE for e in holders.entities),
                       "devices": sum(resolve_device_registry_access(d, access_key, hass) in _READABLE
                                      for d in holders.devices),
                       "areas": len(holders.areas) if access_key.pass_through else None})
    categories: dict[str, list[dict]] = {}
    for scope in CATEGORY_SCOPES:
        for category in sorted(cr.async_get(hass).async_list_categories(scope=scope), key=lambda item: item.name.casefold()):
            holders = category_holders(hass, scope, category.category_id)
            if not _readable(holders, access_key, hass) or (holders.empty and not access_key.pass_through):
                continue
            categories.setdefault(scope, []).append({
                "category_id": category.category_id, "name": category.name,
                **({"icon": category.icon} if category.icon else {}),
                "entities": sum(resolve_registry_access(e, access_key, hass) in _READABLE for e in holders.entities)})
    for row in labels:
        if row["areas"] is None:
            row.pop("areas")
    return _tool_success(json.dumps({"labels": labels, "categories": categories})), "allowed", "list_labels"


@dataclass
class LabelPlan:
    kind: str
    operation: str
    scope: str | None
    item_id: str | None
    updates: dict[str, Any]
    before: dict[str, Any] | None
    holders: Holders
    binding: str


def _prepare(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, *, internal: bool = False) -> LabelPlan:
    if effective_cap(access_key, "cap_registry_write") == CAP_DENY:
        raise LabelRefusal("Forbidden.", "denied")
    allowed = {"kind", "operation", "id", "scope", "changes"} | ({BINDING} if internal else set())
    if set(args) - allowed:
        raise LabelRefusal("Unsupported label or category arguments.")
    kind, operation = args.get("kind"), args.get("operation")
    if kind not in ("label", "category") or operation not in ("create", "update", "delete"):
        raise LabelRefusal("kind must be label or category; operation must be create, update or delete.")
    scope = args.get("scope")
    if kind == "category" and scope not in CATEGORY_SCOPES:
        raise LabelRefusal("A category needs scope: " + ", ".join(CATEGORY_SCOPES) + ".")
    if kind == "label" and scope is not None:
        raise LabelRefusal("scope applies to categories only.")
    changes = args.get("changes", {})
    if not isinstance(changes, dict) or set(changes) - set(_FIELDS[kind]):
        raise LabelRefusal(f"changes may contain only: {', '.join(_FIELDS[kind])}.")
    for field, value in changes.items():
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > LOCATION_REGISTRY_TEXT_LIMIT):
            raise LabelRefusal(f"{field} must be a nonempty bounded string" + ("." if field == "name" else " or null."))
        if field == "name" and value is None:
            raise LabelRefusal("name cannot be cleared.")
    item_id, before = args.get("id"), None
    if operation == "create":
        if item_id is not None:
            raise LabelRefusal("Creation uses a Home Assistant generated ID; omit id.")
        if not changes.get("name"):
            raise LabelRefusal("Creation needs changes.name.")
        if not access_key.pass_through:
            raise LabelRefusal(f"Creating a {kind} requires pass-through authority.", "denied")
        holders = Holders()
    else:
        if not isinstance(item_id, str) or not item_id:
            raise LabelRefusal(f"{operation} needs id, the {kind}'s ID from list_labels.")
        entry = _entry(kind, hass, item_id, scope)
        holders = _holders(kind, hass, item_id, scope)
        # Missing and not writable answer alike, so a hidden label's existence is not confirmed.
        if entry is None or not _writable(holders, access_key, hass):
            raise LabelRefusal(_NOT_FOUND, "denied")
        before = _snapshot(kind, entry)
        if operation == "delete" and changes:
            raise LabelRefusal("Delete does not accept changes.")
        if operation == "update" and not changes:
            raise LabelRefusal("Provide changes to update.")
    if "name" in changes:
        if kind == "label":
            occupied = lr.async_get(hass).async_get_label_by_name(changes["name"])
            clash = occupied is not None and occupied.label_id != item_id
        else:
            clash = any(c.name.casefold() == changes["name"].casefold() and c.category_id != item_id
                        for c in cr.async_get(hass).async_list_categories(scope=scope or ""))
        if clash:
            raise LabelRefusal(f"A {kind} with that name already exists.")
    binding = hashlib.sha256(json.dumps(
        {"kind": kind, "scope": scope, "before": before, "holders": [holders.entities, holders.devices, holders.areas]},
        sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    return LabelPlan(kind, operation, scope, item_id, dict(changes), before, holders, binding)


def _preview(plan: LabelPlan) -> dict[str, Any]:
    name = (plan.before or plan.updates).get("name") or plan.kind
    return {
        "kind": "system_action",
        **diff_summary_fields(f"{plan.kind}.{plan.operation}", name=name),
        "target": {"type": plan.kind, "id": plan.item_id or "new", "label": name},
        "preview": {
            "scope": plan.scope, "before": plan.before, "changes": plan.updates,
            "affected_entities": list(plan.holders.entities), "affected_devices": list(plan.holders.devices),
            "affected_areas": list(plan.holders.areas),
            **({"warning": f"Deleting removes the {plan.kind} from everything listed here, and automations or scripts "
                           "that target it stop matching. It cannot be restored automatically."}
               if plan.operation == "delete" else {}),
        },
    }


async def _tool_set_label_or_category(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create, rename or delete one label or category, behind cap_registry_write."""
    try:
        plan = _prepare(args, access_key, hass)
    except LabelRefusal as exc:
        return _tool_error(str(exc)), exc.outcome, _TOOL
    blocked = await _gate(
        "cap_registry_write", access_key, hass, data, tool_name=_TOOL, args={**args, BINDING: plan.binding},
        request_id=request_id, client_ip=client_ip, diff=lambda: _preview(plan),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_label_or_category(args, access_key, hass, data)


async def _execute_set_label_or_category(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Final check, registry write and readback with no await between them."""
    check_request_authority()
    try:
        plan = _prepare(args, access_key, hass, internal=True)
        approved = _approved_exec_ctx.get()
        if (effective_cap(access_key, "cap_registry_write") == CAP_CONFIRM
                and not (approved or _restore_ctx.get() is not None)):
            raise LabelRefusal("This change requires approval.", "denied")
        if approved and args.get(BINDING) != plan.binding:
            raise LabelRefusal("The item or what carries it changed; request a new review.", "denied")
    except LabelRefusal as exc:
        return _tool_error(str(exc)), exc.outcome, _TOOL
    item_id, after = plan.item_id, None
    try:
        # _prepare gives an update or delete its id, and a category its scope.
        if plan.kind == "label":
            labels = lr.async_get(hass)
            if plan.operation == "create":
                item_id = labels.async_create(**plan.updates).label_id
            elif plan.operation == "update":
                labels.async_update(cast(str, item_id), **plan.updates)
            else:
                labels.async_delete(cast(str, item_id))
        else:
            categories = cr.async_get(hass)
            scope = cast(str, plan.scope)
            if plan.operation == "create":
                item_id = categories.async_create(scope=scope, **plan.updates).category_id
            elif plan.operation == "update":
                categories.async_update(scope=scope, category_id=cast(str, item_id), **plan.updates)
            else:
                categories.async_delete(scope=scope, category_id=cast(str, item_id))
        assert isinstance(item_id, str)
        readback = _entry(plan.kind, hass, item_id, plan.scope)
        if plan.operation == "delete":
            if readback is not None:
                raise RuntimeError("deletion readback mismatch")
        else:
            if readback is None:
                raise RuntimeError("readback unavailable")
            after = _snapshot(plan.kind, readback)
            if any(after.get(field) != value for field, value in plan.updates.items()):
                raise RuntimeError("readback mismatch")
    except Exception:
        _LOGGER.exception("Label or category write failed after dispatch")
        return _tool_error(json.dumps({
            "status": "unknown", "id": item_id,
            "message": "The write may have applied. Inspect list_labels before any retry; nothing was replayed or rolled back.",
        })), "invalid_request", _TOOL
    receipt = _tool_success(json.dumps({
        "kind": plan.kind, "operation": plan.operation, "id": item_id, "scope": plan.scope,
        "write_success": True, "verified": True, "updated": after, "restorable": plan.operation != "delete",
    }))
    before = dict(plan.before) if plan.before is not None else None
    if plan.operation == "delete" and before is not None:
        before["restorable"] = False
    resource_id = item_id if plan.kind == "label" else f"{plan.scope}/{item_id}"
    await _record_version(
        data, access_key, resource_type="label" if plan.kind == "label" else "category", resource_id=resource_id,
        action={"create": "create", "update": "edit", "delete": "delete"}[plan.operation],
        before=before, after=after, alias=(after or before or {}).get("name"),
    )
    check_request_authority()
    return receipt, "allowed", resource_id


async def restore_label_version(
    kind: str, resource_id: str, target: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Restore metadata only to the same surviving label or category."""
    scope, _, item_id = resource_id.rpartition("/") if kind == "category" else (None, "", resource_id)
    entry = _entry(kind, hass, item_id, scope or None)
    if (_restore_ctx.get() is None or entry is None or target.get("restorable") is False
            or target.get("created_at") != str(getattr(entry, "created_at", None))):
        return _tool_error("Deleted or replaced labels and categories cannot be restored automatically."), "invalid_request", _TOOL
    changes = {field: target[field] for field in _FIELDS[kind] if field in target and target[field] != getattr(entry, field, None)}
    if not changes:
        return _tool_success(json.dumps({"kind": kind, "id": item_id, "restored": "unchanged"})), "allowed", resource_id
    return await _execute_set_label_or_category(
        {"kind": kind, "operation": "update", "id": item_id, **({"scope": scope} if scope else {}), "changes": changes},
        access_key, hass, data,
    )
