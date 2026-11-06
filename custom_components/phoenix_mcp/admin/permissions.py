"""Admin permission views: the tree, bulk selection, resolution, scope and entity hints."""

from __future__ import annotations

import functools
import json
from typing import Any

from aiohttp import web
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar, device_registry as dr, entity_registry as er, entity_registry as er_mod,
    label_registry as lr,
)

from ..access_key_store import KEEP_HINT, PermissionNode, PermissionTree, VALID_NODE_STATES
from ..const import BLOCKED_DOMAINS, CAPABILITY_NAMES, DOMAIN
from ..data import PhoenixData
from ..device_semantics import (
    device_authority_chain, device_registry_snapshot, effective_entity_area_id,
)
from ..policy_engine import (
    Permission, config_entry_registry_context, device_config_entry_ids, get_effective_hint, resolve,
)
from ..view_base import PhoenixView, _err
from .common import (
    _ENTITY_RE, _audit_admin, _in_menu, _ok, _read_body, _validate_node_id, require_admin,
)


def _validate_permission_tree_body(body: dict, rid: str) -> web.Response | None:
    """Return an error response if any node ID, state, or hint in the permission tree body is invalid."""
    for section, node_type in (("domains", "domains"), ("devices", "devices"), ("entities", "entities")):
        raw_section = body.get(section, {})
        # A non-object section (list, string, number) is structurally invalid
        # but syntactically valid JSON; reject it with a 400 instead of letting
        # .items() raise AttributeError (a 500).
        if not isinstance(raw_section, dict):
            return _err(
                "invalid_request",
                f"{section!r} must be an object keyed by node ID.",
                400,
                rid,
                key="permissionSectionObject",
                params={"section": section},
            )
        for key, value in raw_section.items():
            err = _validate_node_id(node_type, key, rid)
            if err:
                return err
            if not isinstance(value, dict):
                return _err(
                    "invalid_request",
                    f"Node {key!r} value must be an object with a 'state' key.",
                    400,
                    rid,
                    key="permissionNodeObject",
                    params={"node": key},
                )
            state = value.get("state", "GREY")
            if not _in_menu(state, VALID_NODE_STATES):
                message = (f"Invalid state {state!r} for {node_type[:-1]} {key!r}. "
                           f"Valid states: {sorted(VALID_NODE_STATES)}.")
                params = {"state": state, "node": key, "states": ", ".join(sorted(VALID_NODE_STATES))}
                # One message per node type, so a translated message never carries an English noun.
                if node_type == "domains":
                    return _err("invalid_request", message, 400, rid, key="permissionStateInvalidDomain", params=params)
                if node_type == "devices":
                    return _err("invalid_request", message, 400, rid, key="permissionStateInvalidDevice", params=params)
                return _err("invalid_request", message, 400, rid, key="permissionStateInvalidEntity", params=params)
            hint = value.get("hint")
            if hint is not None:
                if not isinstance(hint, str):
                    return _err(
                        "invalid_request", f"hint for {key!r} must be a string.", 400, rid,
                        key="permissionHintString", params={"node": key},
                    )
                if len(hint) > 200:
                    return _err(
                        "invalid_request", f"hint for {key!r} exceeds 200 characters.", 400, rid,
                        key="permissionHintTooLong", params={"node": key, "max": 200},
                    )
    return None


def _build_entity_tree(hass: HomeAssistant) -> dict:
    """Build a domain-keyed tree of enabled entities outside BLOCKED_DOMAINS and Phoenix's own.

    Synchronous, from the in-memory registries; a child's row names its main parent. Cached in
    PhoenixData.entity_tree_cache until a registry change.
    """

    entity_reg = er.async_get(hass)
    device_snapshot = device_registry_snapshot(hass)
    area_reg = ar.async_get(hass)
    label_reg = lr.async_get(hass)

    tree: dict[str, dict] = {}

    def device_row(device_id: str) -> dict[str, Any]:
        device = device_snapshot.get(device_id)
        if device is None:
            return {
                "device_id": device_id,
                "parent_device_id": None,
                "name": device_id,
                "area_id": None,
                "area_name": None,
                "entities": [],
            }
        area_id = device_snapshot.effective_area_id(device_id)
        area = area_reg.async_get_area(area_id) if area_id else None
        return {
            "device_id": device_id,
            "parent_device_id": device_snapshot.child_parent_id(device_id),
            "name": device.name_by_user or device.name or device_id,
            "area_id": area_id,
            "area_name": area.name if area else None,
            "entities": [],
        }

    for entry in entity_reg.entities.values():
        if entry.disabled_by is not None:
            continue

        entity_id = entry.entity_id
        domain = entity_id.split(".")[0]

        if domain in BLOCKED_DOMAINS:
            continue

        # Exclude Phoenix MCP's own telemetry sensors (entities registered to the
        # phoenix_mcp platform). They live in the sensor domain, so BLOCKED_DOMAINS
        # does not catch them. Showing them would let admins grant permissions the
        # runtime policy engine always blocks; Phoenix MCP internals never appear
        # in the permission UI.
        if entry.platform == DOMAIN:
            continue

        state = hass.states.get(entity_id)
        friendly_name = None
        if state:
            friendly_name = state.attributes.get("friendly_name") or state.name

        if domain not in tree:
            tree[domain] = {"devices": {}, "deviceless_entities": [], "entity_details": {}}

        area_id = effective_entity_area_id(entry, device_snapshot)

        area_name = None
        if area_id:
            area = area_reg.async_get_area(area_id)
            area_name = area.name if area else None

        # Effective labels follow HA label-target semantics: the entity's own
        # labels plus those of its device. Used by the "Select by Label" picker.
        label_ids: set[str] = set(entry.labels)
        if entry.device_id:
            dev_for_labels = device_snapshot.get(entry.device_id)
            if dev_for_labels:
                label_ids |= dev_for_labels.labels
        labels = []
        for lid in label_ids:
            lbl = label_reg.async_get_label(lid)
            if lbl is not None:
                labels.append({"id": lid, "name": lbl.name})
        labels.sort(key=lambda x: x["name"].lower())

        entity_info: dict[str, Any] = {
            "entity_id": entity_id,
            "friendly_name": friendly_name,
            "device_id": entry.device_id,
            "area_id": area_id,
            "area_name": area_name,
            "labels": labels,
        }

        if entry.device_id:
            device_id = entry.device_id
            if device_id not in tree[domain]["devices"]:
                tree[domain]["devices"][device_id] = device_row(device_id)
            tree[domain]["devices"][device_id]["entities"].append(entity_id)
        else:
            tree[domain]["deviceless_entities"].append(entity_id)

        tree[domain]["entity_details"][entity_id] = entity_info

    # Entities Home Assistant runs without a registry entry (YAML without a
    # unique_id, for example) resolve like any deviceless entity, so they appear
    # and can be granted one by one, not only through their domain.
    for entity_id in hass.states.async_entity_ids():
        domain = entity_id.split(".")[0]
        if domain in BLOCKED_DOMAINS or entity_reg.async_get(entity_id) is not None:
            continue
        state = hass.states.get(entity_id)
        domain_tree = tree.setdefault(domain, {"devices": {}, "deviceless_entities": [], "entity_details": {}})
        domain_tree["deviceless_entities"].append(entity_id)
        domain_tree["entity_details"][entity_id] = {
            "entity_id": entity_id,
            "friendly_name": (state.attributes.get("friendly_name") or state.name) if state else None,
            "device_id": None,
            "area_id": None,
            "area_name": None,
            "labels": [],
        }

    # A child device's main parent governs it, so it gets a row in every domain
    # where its parts appear, even when it has no entities there or anywhere.
    # Without it, a parent's node could only show as an unmatched device.
    for domain_tree in tree.values():
        devices = domain_tree["devices"]
        for parent_id in {row["parent_device_id"] for row in list(devices.values())}:
            if parent_id is not None and parent_id not in devices:
                devices[parent_id] = device_row(parent_id)

    # Zone, tag, and person creation require inherited domain WRITE scope because
    # their entity IDs do not exist until Home Assistant creates them. Keep all
    # three grantable domains in the permission UI before their first
    # registry-backed user entity exists; otherwise the operator faces a
    # chicken-and-egg requirement. Each gains its ordinary children as soon as an
    # entity materializes and invalidates this cache.
    for creatable_domain in ("person", "tag", "zone"):
        tree.setdefault(
            creatable_domain,
            {"devices": {}, "deviceless_entities": [], "entity_details": {}},
        )

    return tree


def _integration_permission_selection(
    hass: HomeAssistant, entry_id: str
) -> dict[str, Any] | None:
    """Return the exact permission nodes selected by one config entry."""
    context = config_entry_registry_context(entry_id, hass)
    if context is None:
        return None
    entity_reg = er_mod.async_get(hass)
    selected_entities: list[str] = []
    registry_only_deviceless: list[str] = []
    for entity_id in context.entity_ids:
        entity = entity_reg.async_get(entity_id)
        if (
            entity is None
            or entity.device_id is not None
            or entity_id.split(".", 1)[0] in BLOCKED_DOMAINS
            or entity.platform == DOMAIN
        ):
            continue
        if entity.disabled_by is None and hass.states.get(entity_id) is not None:
            selected_entities.append(entity_id)
        else:
            registry_only_deviceless.append(entity_id)

    device_snapshot = device_registry_snapshot(hass)
    shared_devices = [
        device_id
        for device_id in context.device_ids
        if (
            (device := device_snapshot.get(device_id)) is not None
            and len(device_config_entry_ids(device)) > 1
        )
    ]
    return {
        "entry_id": context.entry.entry_id,
        "domain": context.entry.domain,
        "title": context.entry.title,
        "device_ids": list(context.device_ids),
        "entity_ids": sorted(selected_entities),
        "registry_only_deviceless_count": len(registry_only_deviceless),
        "required_domain_ids": sorted({
            entity_id.split(".", 1)[0]
            for entity_id in registry_only_deviceless
        }),
        "shared_device_count": len(shared_devices),
    }


def _bulk_permission_selection(
    hass: HomeAssistant, selector_type: str, selector_id: str
) -> dict[str, Any] | None:
    """Resolve an area, label, or config entry to current permission nodes."""
    if selector_type == "integration":
        return _integration_permission_selection(hass, selector_id)

    if selector_type not in ("area", "label"):
        return None
    entity_tree = _build_entity_tree(hass)
    entity_ids: list[str] = []
    for domain in entity_tree.values():
        for entity_id, detail in domain["entity_details"].items():
            matches = (
                detail["area_id"] == selector_id
                if selector_type == "area"
                else any(label["id"] == selector_id for label in detail["labels"])
            )
            if matches:
                entity_ids.append(entity_id)
    if not entity_ids:
        return None
    return {
        "device_ids": [],
        "entity_ids": sorted(entity_ids),
        "registry_only_deviceless_count": 0,
        "required_domain_ids": [],
        "shared_device_count": 0,
    }


def _build_resolution_path(entity_id: str, access_key: Any, hass: HomeAssistant) -> list[dict]:
    """Return the ancestor chain and each node's state for a given entity/access key pair.

    Used by the resolve admin endpoint to explain why an entity has a particular
    effective permission.
    """

    er_reg = er.async_get(hass)
    dr_reg = dr.async_get(hass)

    entry = er_reg.async_get(entity_id)
    if entry:
        entity_id = entry.entity_id
    domain = entity_id.split(".")[0]

    path: list[dict] = [{"level": "global", "state": "GREY"}]

    domain_node = access_key.permissions.domains.get(domain)
    path.append({"level": f"domain:{domain}", "state": domain_node.state if domain_node else "GREY"})

    # Outermost first: a child device's main parent sits above the child.
    for device_id in reversed(device_authority_chain(hass, entry.device_id if entry else None) or ()):
        device = dr_reg.async_get(device_id)
        if device:
            device_name = device.name_by_user or device.name or device_id
        else:
            device_name = device_id
        device_node = access_key.permissions.devices.get(device_id)
        path.append({"level": f"device:{device_name}", "state": device_node.state if device_node else "GREY"})

    entity_node = access_key.permissions.entities.get(entity_id)
    path.append({"level": f"entity:{entity_id}", "state": entity_node.state if entity_node else "GREY"})

    return path


class PhoenixAdminPermissionsView(PhoenixView):
    """GET/PUT /api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions - read or replace the full permission tree."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions"
    name = "api:phoenix-mcp:admin:permissions"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
        return _ok(access_key.permissions.to_dict(), request_id=rid)

    @require_admin
    async def put(self, request: web.Request, access_key_id: str) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        err = _validate_permission_tree_body(body, rid)
        if err:
            return err

        try:
            new_tree = PermissionTree.from_dict(body)
        except Exception:
            return _err(
                "invalid_request", "Invalid permission tree structure.", 400, rid,
                key="permissionTreeInvalid",
            )

        async with data.store.async_lock:
            access_key = data.store.get_access_key_by_id(access_key_id)
            if access_key is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

            updated = await data.store.async_set_permissions(access_key_id, new_tree)
            if updated is None:
                # The store returns None only for an unknown id, which the lookup
                # above already excluded under this same lock. Report it rather
                # than dereference None.
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )
        return _ok(updated.permissions.to_dict(), request_id=rid)


class PhoenixAdminPermissionIntegrationOptionsView(PhoenixView):
    """List exact config entries available to the permission selector."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/integration-options"
    name = "api:phoenix-mcp:admin:permission_integration_options"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if data.store.get_access_key_by_id(access_key_id) is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
        integrations = []
        for entry in self.hass.config_entries.async_entries():
            selection = _integration_permission_selection(self.hass, entry.entry_id)
            if selection is None:
                continue
            if not selection["device_ids"] and not selection["entity_ids"]:
                continue
            integrations.append({
                "entry_id": selection["entry_id"],
                "domain": selection["domain"],
                "title": selection["title"],
                "device_count": len(selection["device_ids"]),
                "deviceless_entity_count": len(selection["entity_ids"]),
                # The members themselves, so the panel's Select By filter can
                # narrow the permissions tree to this entry without a grant.
                "device_ids": sorted(selection["device_ids"]),
                "entity_ids": selection["entity_ids"],
                "registry_only_deviceless_count": selection[
                    "registry_only_deviceless_count"
                ],
                "required_domain_ids": selection["required_domain_ids"],
                "shared_device_count": selection["shared_device_count"],
            })
        integrations.sort(key=lambda item: (
            str(item["title"]).casefold(), item["domain"], item["entry_id"]
        ))
        return _ok({"integrations": integrations}, request_id=rid)


class PhoenixAdminPermissionBulkSelectView(PhoenixView):
    """Atomically apply one state to a server-resolved selector snapshot."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/bulk-select"
    name = "api:phoenix-mcp:admin:permission_bulk_select"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        selector_type = body.get("selector_type")
        selector_id = body.get("selector_id")
        state = body.get("state")
        if selector_type not in ("area", "label", "integration"):
            return _err(
                "invalid_request",
                "selector_type must be area, label, or integration.",
                400,
                rid,
                key="selectorTypeInvalid",
            )
        if not isinstance(selector_id, str) or not selector_id:
            return _err(
                "invalid_request", "selector_id is required.", 400, rid,
                key="selectorIdRequired",
            )
        if not _in_menu(state, VALID_NODE_STATES):
            return _err(
                "invalid_request",
                f"state must be one of: {', '.join(sorted(VALID_NODE_STATES))}.",
                400,
                rid,
                key="stateAllowed",
                params={"states": ", ".join(sorted(VALID_NODE_STATES))},
            )

        selection = _bulk_permission_selection(
            self.hass, selector_type, selector_id
        )
        if selection is None:
            return _err(
                "not_found", "Permission selection not found.", 404, rid,
                key="permissionSelectionNotFound",
            )

        data: PhoenixData = self.hass.data[DOMAIN]
        async with data.store.async_lock:
            access_key = data.store.get_access_key_by_id(access_key_id)
            if access_key is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
            permissions = PermissionTree.from_dict(access_key.permissions.to_dict())
            for node_type, node_ids in (
                ("devices", selection["device_ids"]),
                ("entities", selection["entity_ids"]),
            ):
                nodes = getattr(permissions, node_type)
                for node_id in node_ids:
                    # Selecting by area, label or integration changes states, never hints.
                    existing = nodes.get(node_id)
                    kept = existing.hint if existing else None
                    if state == "GREY" and not kept:
                        nodes.pop(node_id, None)
                    else:
                        nodes[node_id] = PermissionNode(state=state, hint=kept)
            updated = await data.store.async_set_permissions(access_key_id, permissions)
            if updated is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        _audit_admin(self.hass, request, rid, request.path)
        summary = {
            "selector_type": selector_type,
            "selector_id": selector_id,
            "state": state,
            "device_count": len(selection["device_ids"]),
            "entity_count": len(selection["entity_ids"]),
            "registry_only_deviceless_count": selection[
                "registry_only_deviceless_count"
            ],
            "required_domain_ids": selection["required_domain_ids"],
            "shared_device_count": selection["shared_device_count"],
        }
        return _ok(
            {"permissions": updated.permissions.to_dict(), "summary": summary},
            request_id=rid,
        )


class PhoenixAdminPermissionDomainView(PhoenixView):
    """PATCH /api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/domains/{node_id}."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/domains/{node_id}"
    name = "api:phoenix-mcp:admin:permission_domain"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, access_key_id: str, node_id: str) -> web.Response:
        return await _patch_permission_node(request, self.hass, access_key_id, "domains", node_id)


class PhoenixAdminPermissionDeviceView(PhoenixView):
    """PATCH /api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/devices/{node_id}."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/devices/{node_id}"
    name = "api:phoenix-mcp:admin:permission_device"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, access_key_id: str, node_id: str) -> web.Response:
        return await _patch_permission_node(request, self.hass, access_key_id, "devices", node_id)


class PhoenixAdminPermissionEntityView(PhoenixView):
    """PATCH /api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/entities/{node_id}."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/permissions/entities/{node_id}"
    name = "api:phoenix-mcp:admin:permission_entity"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, access_key_id: str, node_id: str) -> web.Response:
        return await _patch_permission_node(request, self.hass, access_key_id, "entities", node_id)


async def _patch_permission_node(
    request: web.Request,
    hass: HomeAssistant,
    access_key_id: str,
    node_type: str,
    node_id: str,
) -> web.Response:
    """Shared handler for PATCH on domain/device/entity permission nodes."""
    rid = request["phoenix_mcp_rid"]

    err = _validate_node_id(node_type, node_id, rid)
    if err:
        return err

    data: PhoenixData = hass.data[DOMAIN]

    body = await _read_body(request, rid)
    if isinstance(body, web.Response):
        return body

    state = body.get("state")
    if not _in_menu(state, VALID_NODE_STATES):
        return _err(
            "invalid_request",
            f"state must be one of: {', '.join(sorted(VALID_NODE_STATES))}.",
            400, rid, key="stateAllowed",
            params={"states": ", ".join(sorted(VALID_NODE_STATES))},
        )

    # A body without "hint" changes the state and keeps the node's hint; null clears it.
    hint = body.get("hint") if "hint" in body else KEEP_HINT
    if hint is not KEEP_HINT and hint is not None and not isinstance(hint, str):
        return _err(
            "invalid_request", "hint must be a string.", 400, rid,
            key="hintString",
        )
    if isinstance(hint, str) and len(hint) > 200:
        return _err(
            "invalid_request", "hint must be 200 characters or fewer.", 400, rid,
            key="hintTooLong", params={"max": 200},
        )

    async with data.store.async_lock:
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        updated = await data.store.async_patch_permission_node(
            access_key_id, node_type, node_id, state, hint
        )
        if updated is None:
            # Same reasoning as the access key PATCH above: report rather than
            # dereference None if the record vanished inside the lock.
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

    user = request[KEY_HASS_USER]
    data.audit.record(
        request_id=rid,
        access_key_id="admin",
        access_key_name=f"admin:{user.id}",
        method=request.method,
        resource=request.path,
        outcome="allowed",
        client_ip=request.remote or "",
        settings=data.store.get_settings(),
    )
    return _ok(updated.permissions.to_dict(), request_id=rid)


class PhoenixAdminResolveView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys/{access_key_id}/resolve/{entity_id} - explain effective permission."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/resolve/{entity_id}"
    name = "api:phoenix-mcp:admin:resolve"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str, entity_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        if not _ENTITY_RE.match(entity_id):
            return _err("invalid_request", "Invalid entity ID format.", 400, rid, key="badEntityId")
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        perm = resolve(entity_id, access_key, hass)
        resolution_path = _build_resolution_path(entity_id, access_key, hass)

        effective_map = {
            Permission.WRITE: "WRITE",
            Permission.READ: "READ",
            Permission.DENY: "DENY",
            Permission.NO_ACCESS: "NO_ACCESS",
            Permission.NOT_FOUND: "NOT_FOUND",
        }

        effective_hint = get_effective_hint(access_key, entity_id, hass, data.store.get_entity_hints())

        return _ok({
            "entity_id": entity_id,
            "resolution_path": resolution_path,
            "effective": effective_map.get(perm, "NO_ACCESS"),
            "effective_hint": effective_hint,
        }, request_id=rid)


class PhoenixAdminScopeView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys/{access_key_id}/scope - enumerate all readable/writable entities."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/scope"
    name = "api:phoenix-mcp:admin:scope"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        all_states = hass.states.async_all()
        readable: list[str] = []
        writable: list[str] = []

        # resolve() for pass-through keys too, so a key limited to Assist-exposed
        # entities lists only what its agent can actually reach.
        for state in all_states:
            eid = state.entity_id
            perm = resolve(eid, access_key, hass)
            if perm == Permission.WRITE:
                readable.append(eid)
                writable.append(eid)
            elif perm == Permission.READ:
                readable.append(eid)

        # capability_flags reports raw stored values, without the pass_through OR
        # adjustments applied by _build_server_info / _build_context_json: this is a
        # diagnostic view, so the admin sees the stored flags, not the effective
        # values a client would receive.
        return _ok({
            "access_key_id": access_key_id,
            "access_key_name": access_key.name,
            "readable": sorted(readable),
            "writable": sorted(writable),
            "persona": access_key.persona,
            "capability_flags": {name: getattr(access_key, name) for name in CAPABILITY_NAMES},
        }, request_id=rid)


class PhoenixAdminEntityTreeView(PhoenixView):
    """GET /api/phoenix-mcp/admin/entities - return (cached) entity tree for the permission UI."""

    url = "/api/phoenix-mcp/admin/entities"
    name = "api:phoenix-mcp:admin:entities"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]

        if request.query.get("force_reload"):
            data.entity_tree_cache_valid = False

        async with data.entity_tree_lock:
            if not data.entity_tree_cache_valid or data.entity_tree_cache is None:
                data.entity_tree_cache = _build_entity_tree(hass)
                data.entity_tree_cache_valid = True

        json_body = await hass.async_add_executor_job(
            functools.partial(json.dumps, data.entity_tree_cache, default=str)
        )
        return web.Response(
            status=200,
            content_type="application/json",
            text=json_body,
            headers={"X-Phoenix-Request-ID": rid, "Cache-Control": "no-store"},
        )


class PhoenixAdminEntityHintsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/entity-hints - the global entity-hint map (entity_id to hint)."""

    url = "/api/phoenix-mcp/admin/entity-hints"
    name = "api:phoenix-mcp:admin:entity_hints"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        return _ok({"entity_hints": dict(data.store.get_entity_hints())}, request_id=rid)


class PhoenixAdminEntityHintView(PhoenixView):
    """PUT /api/phoenix-mcp/admin/entity-hints/{entity_id} - set or clear one global entity hint.

    A global hint applies to every access key that can see the entity. It is distinct
    from a per-access-key permission-node hint, which always takes precedence.
    """

    url = "/api/phoenix-mcp/admin/entity-hints/{entity_id}"
    name = "api:phoenix-mcp:admin:entity_hint"
    requires_auth = True

    @require_admin
    async def put(self, request: web.Request, entity_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        if not _ENTITY_RE.match(entity_id):
            return _err("invalid_request", "Invalid entity ID format.", 400, rid, key="badEntityId")
        data: PhoenixData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        hint = body.get("hint")
        if hint is not None:
            if not isinstance(hint, str):
                return _err(
                    "invalid_request", "hint must be a string.", 400, rid,
                    key="hintString",
                )
            hint = hint.strip()
            if len(hint) > 200:
                return _err(
                    "invalid_request", "hint must be 200 characters or fewer.", 400, rid,
                    key="hintTooLong", params={"max": 200},
                )

        async with data.store.async_lock:
            await data.store.async_set_entity_hint(entity_id, hint or None)

        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )
        return _ok({"entity_hints": dict(data.store.get_entity_hints())}, request_id=rid)
