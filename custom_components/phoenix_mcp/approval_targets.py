"""Bind immediate approval actions to registry identities and explicit group membership."""

from __future__ import annotations

import hashlib
import json

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import entity_registry as er

from .const import (
    APPROVAL_TARGET_DEPTH, APPROVAL_TARGET_ID_CHARS, APPROVAL_TARGET_LIMIT, APPROVAL_TARGETS_KEY,
    APPROVAL_TARGETS_VERSION,
)
from .device_semantics import device_registry_snapshot

TARGETS_CHANGED = "Approval targets changed or cannot be verified. Submit a new request."


def target_binding(entities: list[str], hass: HomeAssistant) -> dict:
    """Capture complete observable identities without storing hidden group members."""
    roots = sorted(set(entities))
    registry = er.async_get(hass)
    devices = device_registry_snapshot(hass)
    rows: dict[str, dict] = {}
    visiting: set[str] = set()
    budget = APPROVAL_TARGET_LIMIT

    def visit(entity_id: str, depth: int) -> None:
        nonlocal budget
        if (not isinstance(entity_id, str) or len(entity_id) > APPROVAL_TARGET_ID_CHARS
                or not valid_entity_id(entity_id) or depth > APPROVAL_TARGET_DEPTH
                or entity_id in visiting):
            raise ValueError
        if entity_id in rows:
            return
        budget -= 1
        if budget < 0:
            raise ValueError
        entry = registry.async_get(entity_id)
        if entry is None:
            raise ValueError
        state = hass.states.get(entity_id)
        members: set[str] = set()
        if state is not None:
            for key in ("group_entities", "entity_id"):
                if key not in state.attributes:
                    continue
                value = state.attributes[key]
                if not isinstance(value, (list, tuple, set, frozenset)) or len(value) > APPROVAL_TARGET_LIMIT:
                    raise ValueError
                if any(not isinstance(member, str) for member in value):
                    raise ValueError
                members.update(value)
        budget -= len(members)
        if budget < 0:
            raise ValueError
        if entry.platform == "group" and (state is None or not any(
                key in state.attributes for key in ("group_entities", "entity_id"))):
            raise ValueError
        visiting.add(entity_id)
        for member in sorted(members):
            visit(member, depth + 1)
        visiting.remove(entity_id)
        rows[entity_id] = {
            "registry_id": entry.id, "unique_id": entry.unique_id,
            "platform": entry.platform, "config_entry_id": entry.config_entry_id,
            "device_id": entry.device_id, "area_id": entry.area_id,
            "device_area": devices.effective_area_id(entry.device_id),
            "device_parent": devices.parent_id(entry.device_id),
            "disabled_by": entry.disabled_by, "hidden_by": entry.hidden_by,
            "entity_category": entry.entity_category, "members": sorted(members),
        }

    fingerprint = None
    try:
        if not roots or len(roots) > APPROVAL_TARGET_LIMIT:
            raise ValueError
        for entity_id in roots:
            visit(entity_id, 0)
        fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    except (ValueError, TypeError):
        pass
    return {"version": APPROVAL_TARGETS_VERSION, "entity_ids": roots, "fingerprint": fingerprint}


def bind_targets(args: dict, entities: list[str], hass: HomeAssistant) -> dict:
    """Replace any input binding with server-captured evidence, never caller evidence."""
    return {**args, APPROVAL_TARGETS_KEY: target_binding(entities, hass)}


def targets_current(args: dict, entities: list[str], hass: HomeAssistant) -> bool:
    """Refuse changed or missing exact bindings; ordinary unapproved calls stay dynamic."""
    from .tool_common import _approved_exec_ctx

    if APPROVAL_TARGETS_KEY not in args:
        return not _approved_exec_ctx.get()
    binding = args[APPROVAL_TARGETS_KEY]
    return (isinstance(binding, dict) and type(binding.get("version")) is int
            and isinstance(binding.get("fingerprint"), str)
            and isinstance(entities, list) and all(isinstance(entity, str) for entity in entities)
            and binding == target_binding(entities, hass))


def bound_preview(diff: dict, args: dict) -> dict:
    """Expose the reviewed primary entities, not private fingerprints or group internals."""
    binding = args[APPROVAL_TARGETS_KEY]
    return {**diff, "preview": {**(diff.get("preview") or {}),
                               "target_semantics": "exact",
                               "resolved_entity_ids": list(binding["entity_ids"])}}
