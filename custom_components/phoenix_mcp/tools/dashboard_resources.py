"""Dashboard resources: the JavaScript and CSS files every dashboard loads.

Registering a JavaScript file makes every browser that opens a dashboard run it
with the viewer's Home Assistant session, so both tools sit behind their own
capability, cap_dashboard_resources, rather than cap_lovelace_write.

Only storage-mode resources are changed, through Home Assistant's own collection
commands and validators. YAML-mode resources are loaded with their !secret values
resolved, so they are counted but never listed, and the YAML tools refuse the
lovelace: resources subtree, so an administrator edits them by hand.

Every change is a recoverable write: the target resource is snapshotted before
dispatch, read back afterwards, and the single "dashboard_resources" recovery key
serializes Phoenix writes to the list. A created resource is identified by its URL
until Home Assistant has assigned it an ID, which is why a URL that is already
registered cannot be added again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant

from ..access_key_store import AccessKeyRecord
from ..config_recovery import ConfigurationRecoveryError, safe_snapshot
from ..const import (
    CAP_CONFIRM,
    CAP_DENY,
    DASHBOARD_RESOURCE_BINDING,
    DASHBOARD_RESOURCE_TYPES,
    DASHBOARD_RESOURCE_URL_MAX_LENGTH,
    REDACTION_SENTINEL,
)
from ..data import PhoenixData
from ..helpers import content_hash, diff_summary_fields, effective_cap, redact_structure
from ..request_authority import AuthorityChangedError, check_request_authority
from ..tool_common import (
    _CAP_FORBIDDEN_MESSAGE,
    _approved_exec_ctx,
    _gate,
    _restore_ctx,
    _tool_error,
    _tool_success,
)
from ..ws_dispatch import (
    WsCommandNotDispatchedError,
    WsDispatchError,
    async_lovelace_resources,
    async_ws_command,
    validate_ws_payload,
)

_CAP = "cap_dashboard_resources"
_LIST_TOOL = "list_dashboard_resources"
_SET_TOOL = "set_dashboard_resource"
RECOVERY_RESOURCE = "dashboard_resources"

_UNREADABLE = "Dashboard resources could not be read; Home Assistant may be in safe mode or still starting."
_YAML_MODE = (
    "Dashboard resources on this instance are defined in YAML (lovelace: resources), which Phoenix MCP "
    "cannot change. An administrator edits that list in the YAML file and then reloads resources."
)
_RELOAD_NOTE = (
    "Open dashboards load the change on their next page reload. The installed card list updates when "
    "the Phoenix MCP panel next checks it, which it does on opening, at most every ten minutes per browser."
)


class ResourceRefusal(Exception):
    """A refused plan, carrying the audit outcome to report."""

    def __init__(self, message: str, outcome: str = "invalid_request") -> None:
        super().__init__(message)
        self.outcome = outcome


def _row(item: dict[str, Any]) -> dict[str, Any]:
    """Project one Home Assistant resource item to the fields Phoenix reasons about."""
    return {"id": item.get("id"), "type": item.get("type"), "url": item["url"]}


def resource_snapshot(items: list[dict[str, Any]], lookup: str, key: str) -> dict:
    """Snapshot the one storage resource whose id (or URL) equals key.

    Raises ConfigurationRecoveryError when two resources match, or when the
    resource carries a credential that a recovery snapshot cannot keep.
    """
    matches = [_row(item) for item in items if item.get(lookup) == key]
    if len(matches) > 1:
        raise ConfigurationRecoveryError("Dashboard resource identity is ambiguous; nothing was dispatched.")
    row = matches[0] if matches else None
    return safe_snapshot({
        "snapshot_type": "dashboard_resource", "schema": 1, "lookup": lookup,
        "resource_id": key, "exists": row is not None, "resource": row,
    }) or {}


async def _storage_items(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Read the storage-mode resource list, refusing an unreadable or YAML-mode one."""
    resources = await async_lovelace_resources(hass)
    if resources is None:
        raise ConfigurationRecoveryError(_UNREADABLE)
    if resources.mode != "storage":
        raise ConfigurationRecoveryError(_YAML_MODE)
    return resources.items


async def read_resource_snapshot(hass: HomeAssistant, lookup: str, key: str) -> dict:
    """Read the current snapshot of one resource by id or URL."""
    return resource_snapshot(await _storage_items(hass), lookup, key)


def _same_file(url: str) -> str:
    """The URL without query or fragment: cache-busting tags still load the same file."""
    return url.split("#", 1)[0].split("?", 1)[0]


async def _tool_list_dashboard_resources(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """MCP tool: list the registered dashboard resources."""
    if effective_cap(access_key, _CAP) == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", _LIST_TOOL
    resources = await async_lovelace_resources(hass)
    if resources is None:
        return _tool_error(_UNREADABLE), "invalid_request", _LIST_TOOL
    if resources.mode != "storage":
        return _tool_success(json.dumps({
            "resource_mode": "yaml", "editable": False, "count": len(resources.items),
            "message": (
                "These resources are defined in YAML and are not listed, because Home Assistant resolves "
                "their !secret values when it loads them. Read the lovelace: resources key with "
                "get_yaml_config, which keeps !secret references unresolved. Phoenix MCP cannot change "
                "them; an administrator edits the YAML file and then reloads resources."
            ),
        })), "allowed", _LIST_TOOL
    catalog = data.card_catalog.catalog
    failed = {item["url"] for item in catalog.failed_imports}
    rows = []
    for item in resources.items:
        row = redact_structure(_row(item))
        if item["url"] in failed:
            row["load_failed"] = True
        rows.append(row)
    return _tool_success(json.dumps({
        "resource_mode": "storage", "editable": True, "count": len(rows), "resources": rows,
        "load_checked_at": catalog.harvested_at,
    })), "allowed", _LIST_TOOL


@dataclass
class ResourcePlan:
    """One validated resource change, with everything its review and readback need."""

    operation: str
    command: str
    payload: dict[str, Any]
    lookup: str
    key: str
    before: dict
    current: dict[str, Any] | None
    proposed: dict[str, Any] | None
    binding: str
    warnings: list[str] = field(default_factory=list)

    @property
    def expected(self) -> dict:
        return {**self.before, "exists": self.proposed is not None, "resource": self.proposed}

    @property
    def label(self) -> str:
        return str((self.current or self.proposed or {}).get("url"))


def _url_arg(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResourceRefusal("url must be a non-empty string.")
    if len(value) > DASHBOARD_RESOURCE_URL_MAX_LENGTH:
        raise ResourceRefusal(f"url must be at most {DASHBOARD_RESOURCE_URL_MAX_LENGTH} characters.")
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ResourceRefusal("url must not contain spaces or control characters; percent-encode them.")
    if REDACTION_SENTINEL in value:
        raise ResourceRefusal(f"url contains {REDACTION_SENTINEL}; write the real URL, not a redacted read.")
    if redact_structure(value) != value:
        raise ResourceRefusal(
            "This URL carries a credential, which Phoenix MCP cannot keep in a recovery copy. "
            "Register it in Settings > Dashboards > Resources instead.")
    return value


def _prepare(args: dict, access_key: AccessKeyRecord, items: list[dict[str, Any]],
             hass: HomeAssistant, *, internal: bool = False) -> ResourcePlan:
    """Validate a change against the current list without any side effect."""
    if effective_cap(access_key, _CAP) == CAP_DENY:
        raise ResourceRefusal(_CAP_FORBIDDEN_MESSAGE, "denied")
    allowed = {"operation", "resource_id", "url", "type"} | ({DASHBOARD_RESOURCE_BINDING} if internal else set())
    if unknown := sorted(set(args) - allowed):
        raise ResourceRefusal(f"Unsupported arguments for {_SET_TOOL}: {', '.join(unknown)}.")
    operation = args.get("operation")
    if operation not in ("create", "update", "delete"):
        raise ResourceRefusal("operation must be create, update or delete.")
    res_type = args.get("type")
    if "type" in args and res_type not in DASHBOARD_RESOURCE_TYPES:
        raise ResourceRefusal(f"type must be one of: {', '.join(DASHBOARD_RESOURCE_TYPES)}.")
    url = _url_arg(args["url"]) if "url" in args else None
    others = list(items)
    current = None
    if operation == "create":
        if "resource_id" in args:
            raise ResourceRefusal("Home Assistant assigns the ID of a new resource; omit resource_id.")
        if url is None or res_type is None:
            raise ResourceRefusal("create requires url and type.")
        lookup, key = "url", url
        payload = {"res_type": res_type, "url": url}
        proposed: dict[str, Any] | None = {"type": res_type, "url": url}
    else:
        resource_id = args.get("resource_id")
        if not isinstance(resource_id, str) or not resource_id:
            raise ResourceRefusal(f"{operation} requires the resource_id from {_LIST_TOOL}.")
        found = [item for item in items if item.get("id") == resource_id]
        if not found:
            raise ResourceRefusal(f"No dashboard resource has that resource_id; read {_LIST_TOOL}.", "not_found")
        current = _row(found[0])
        others = [item for item in items if item.get("id") != resource_id]
        lookup, key = "id", resource_id
        if operation == "delete":
            if "url" in args or "type" in args:
                raise ResourceRefusal("delete takes only resource_id.")
            payload = {"resource_id": resource_id}
            proposed = None
        else:
            if url is None and res_type is None:
                raise ResourceRefusal("update requires url, type, or both.")
            proposed = {**current, **({"url": url} if url is not None else {}),
                        **({"type": res_type} if res_type is not None else {})}
            if proposed == current:
                raise ResourceRefusal("The resource already has that URL and type; nothing would change.")
            payload = {"resource_id": resource_id,
                       **({"url": url} if url is not None else {}),
                       **({"res_type": res_type} if res_type is not None else {})}
    warnings: list[str] = []
    if proposed is not None:
        clash = next((item for item in others if item.get("url") == proposed["url"]), None)
        if clash is not None:
            raise ResourceRefusal(
                f"This URL is already registered as resource {clash.get('id')}; change that one instead.")
        duplicate = next((item for item in others if _same_file(item["url"]) == _same_file(proposed["url"])), None)
        if duplicate is not None:
            warnings.append(
                f"Resource {duplicate.get('id')} already loads the same file; a card loaded twice can fail "
                "to register. Remove one of them.")
    try:
        validate_ws_payload(hass, f"lovelace/resources/{operation}", payload)
        before = resource_snapshot(items, lookup, key)
    except WsCommandNotDispatchedError as exc:
        raise ResourceRefusal(f"Home Assistant refused the resource: {exc}") from None
    except ConfigurationRecoveryError as exc:
        raise ResourceRefusal(str(exc)) from None
    if operation == "create" and before.get("exists"):
        raise ResourceRefusal("This URL is already registered; change that resource instead.")
    binding = content_hash([_row(item) for item in items])
    return ResourcePlan(operation, f"lovelace/resources/{operation}", payload, lookup, key,
                        before, current, proposed, binding, warnings)


def _preview(plan: ResourcePlan) -> dict:
    preview: dict[str, Any] = {"operation": plan.operation, "current": plan.current, "proposed": plan.proposed}
    if plan.proposed is not None and plan.proposed.get("type") in ("module", "js"):
        preview["warning"] = ("This JavaScript file runs in every browser that opens a dashboard, "
                              "with that person's Home Assistant access.")
    if plan.warnings:
        preview["warnings"] = plan.warnings
    return {
        "kind": "config_diff",
        **diff_summary_fields(f"dashboard_resource.{plan.operation}", url=plan.label),
        "target": {"type": "dashboard_resource", "id": plan.key if plan.lookup == "id" else None,
                   "label": plan.label},
        "before": json.dumps(plan.current, indent=2) if plan.current else None,
        "after": json.dumps(plan.proposed, indent=2) if plan.proposed else None,
        "preview": preview,
    }


async def _tool_set_dashboard_resource(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: add, change or remove one storage-mode dashboard resource."""
    if effective_cap(access_key, _CAP) == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", _SET_TOOL
    try:
        plan = _prepare(args, access_key, await _storage_items(hass), hass)
    except ResourceRefusal as exc:
        return _tool_error(str(exc)), exc.outcome, _SET_TOOL
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", _SET_TOOL
    blocked = await _gate(
        _CAP, access_key, hass, data, tool_name=_SET_TOOL,
        args={**args, DASHBOARD_RESOURCE_BINDING: plan.binding},
        request_id=request_id, client_ip=client_ip, diff=lambda: _preview(plan),
    )
    if blocked is not None:
        return blocked
    return await _execute_set_dashboard_resource(args, access_key, hass, data)


async def _execute_set_dashboard_resource(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate against the current list, then make one recoverable, verified change."""
    check_request_authority()
    approved = _approved_exec_ctx.get()
    try:
        plan = _prepare(args, access_key, await _storage_items(hass), hass, internal=True)
        if effective_cap(access_key, _CAP) == CAP_CONFIRM and not (approved or _restore_ctx.get() is not None):
            raise ResourceRefusal("This dashboard resource change requires approval.", "denied")
        if approved and args.get(DASHBOARD_RESOURCE_BINDING) != plan.binding:
            raise ResourceRefusal("Dashboard resources changed after this was reviewed; nothing was changed. "
                                  "Read them again and request a new review.")
    except ResourceRefusal as exc:
        return _tool_error(str(exc)), exc.outcome, _SET_TOOL
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", _SET_TOOL
    return await _recoverable_resource_write(plan, access_key, hass, data)


def _matches_expected(observed: dict, expected: dict) -> bool:
    """Whether a readback shows the intended state; a new resource's ID is HA's to assign."""
    row = observed.get("resource") or {}
    return observed.get("exists") == expected.get("exists") and all(
        row.get(key) == value for key, value in (expected.get("resource") or {}).items())


async def _check_authority_before_dispatch(recovery: Any, rid: str) -> None:
    """Refuse a change whose authority lapsed, recording that nothing was sent."""
    try:
        check_request_authority()
    except AuthorityChangedError:
        await recovery.mark(rid, "not_applied")
        raise


async def _recoverable_resource_write(
    plan: ResourcePlan, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Persist, serialize, dispatch and read back one resource change."""
    recovery = data.config_recovery
    resource_name = f"dashboard_resource:{plan.key}"
    try:
        async with recovery.resource(RECOVERY_RESOURCE):
            rid = await recovery.begin(RECOVERY_RESOURCE, f"dashboard_resource_{plan.operation}",
                                       access_key.id, plan.before, expected=plan.expected)
            try:
                await recovery.mark(rid, "dispatching")
                if await read_resource_snapshot(hass, plan.lookup, plan.key) != plan.before:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("The dashboard resource changed while preparing its recovery snapshot; "
                                       "nothing was dispatched."), "invalid_request", _SET_TOOL
                await _check_authority_before_dispatch(recovery, rid)
                try:
                    result = await async_ws_command(hass, plan.command, plan.payload)
                except WsDispatchError as exc:
                    await recovery.mark(rid, "not_applied" if isinstance(exc, WsCommandNotDispatchedError) else "unknown",
                                        error="command_rejected")
                    return _tool_error(f"Dashboard resource change failed: {exc}"), "invalid_request", _SET_TOOL
                try:
                    after = await read_resource_snapshot(hass, plan.lookup, plan.key)
                except Exception:  # noqa: BLE001 - the change may have landed
                    await recovery.mark(rid, "unknown")
                    return _tool_success(json.dumps({
                        "status": "unknown", "recovery_id": rid,
                        "message": "The dashboard resource change could not be read back; inspect before retrying.",
                    })), "allowed", resource_name
                if not _matches_expected(after, plan.expected) or (
                        plan.operation == "create" and isinstance(result, dict)
                        and (after.get("resource") or {}).get("id") != result.get("id")):
                    await recovery.mark(rid, "partial", observed=after)
                    return _tool_success(json.dumps({
                        "status": "partial", "recovery_id": rid,
                        "message": "The dashboard resource reads back differently; do not retry automatically.",
                    })), "allowed", resource_name
                await recovery.mark(rid, "verified", observed_hash=content_hash(after))
                await recovery.set_source(rid, after)
                body: dict[str, Any] = {"status": "verified", "recovery_id": rid,
                                        "resource": after.get("resource"), "note": _RELOAD_NOTE}
                if plan.warnings:
                    body["warnings"] = plan.warnings
                return _tool_success(json.dumps(body)), "allowed", resource_name
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", _SET_TOOL
    except AuthorityChangedError:
        raise
    except Exception:  # noqa: BLE001 - retain unresolved state on persistence errors
        return _tool_error("The dashboard resource outcome is uncertain; inspect configuration recovery "
                           "before retrying."), "invalid_request", _SET_TOOL


# Restoration. A recovery record holds the resource as it was before and after one
# change; restoring a side makes the live resource match that snapshot again.

def _restore_identity(record: dict[str, Any], side: str, snapshot: dict[str, Any]) -> tuple[str, str]:
    """Prefer an ID either side recorded; fall back to the snapshot's own lookup."""
    for candidate in (snapshot, record.get("source" if side == "before" else "before")):
        row = candidate.get("resource") if isinstance(candidate, dict) else None
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]:
            return "id", row["id"]
    return str(snapshot.get("lookup")), str(snapshot.get("resource_id"))


def _valid_snapshot(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict) or snapshot.get("schema") != 1:
        return False
    if snapshot.get("lookup") not in ("id", "url") or not isinstance(snapshot.get("resource_id"), str):
        return False
    row = snapshot.get("resource")
    if not isinstance(snapshot.get("exists"), bool) or snapshot["exists"] != isinstance(row, dict):
        return False
    return not isinstance(row, dict) or (
        row.get("type") in DASHBOARD_RESOURCE_TYPES + ("html",) and isinstance(row.get("url"), str))


async def restore_target(hass: HomeAssistant, record: dict[str, Any], side: str, snapshot: dict) -> dict:
    """The live resource a restore of this snapshot would change.

    Looked up by ID, then by the snapshot's URL: a deleted resource comes back
    with a new ID, and a second copy of the same URL must not be created.
    """
    items = await _storage_items(hass)
    lookup, key = _restore_identity(record, side, snapshot)
    target = resource_snapshot(items, lookup, key)
    row = snapshot.get("resource")
    if not target["exists"] and isinstance(row, dict):
        target = resource_snapshot(items, "url", row["url"])
    return target


def restore_receipt_diff(target: dict, snapshot: dict) -> dict:
    """The review diff for restoring one resource snapshot."""
    label = str((snapshot.get("resource") or target.get("resource") or {}).get("url") or snapshot.get("resource_id"))
    return {
        "kind": "config_diff", "summary": "Restore dashboard resource",
        "target": {"type": "dashboard_resource", "id": (target.get("resource") or {}).get("id"), "label": label},
        "before": json.dumps(target.get("resource"), indent=2) if target.get("exists") else None,
        "after": json.dumps(snapshot.get("resource"), indent=2) if snapshot.get("exists") else None,
    }


async def execute_restore(
    record: dict[str, Any], side: str, snapshot: dict, *, snapshot_digest: Any, target_fingerprint: Any,
    access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> tuple[dict, str, str]:
    """Revalidate the approved digest and fingerprint, then restore one resource snapshot."""
    tool = "restore_configuration"
    if effective_cap(access_key, _CAP) == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    if not _valid_snapshot(snapshot):
        return _tool_error("This dashboard resource snapshot is invalid."), "invalid_request", tool
    if content_hash(snapshot) != snapshot_digest:
        return _tool_error("The selected dashboard resource snapshot changed after approval."), "invalid_request", tool
    try:
        target = await restore_target(hass, record, side, snapshot)
        if content_hash(target) != target_fingerprint:
            return _tool_error("The dashboard resource changed after approval."), "invalid_request", tool
        check_request_authority()
        wanted: dict[str, Any] = snapshot.get("resource") or {}
        current: dict[str, Any] | None = target.get("resource")
        if snapshot["exists"] and current is not None:
            changes = {k: wanted[k] for k in ("url", "type") if current.get(k) != wanted[k]}
            if not changes:
                return _tool_success(json.dumps({"status": "verified", "already_applied": True})), "allowed", tool
            command = "lovelace/resources/update"
            payload: dict[str, Any] = {"resource_id": current["id"],
                                       **({"url": changes["url"]} if "url" in changes else {}),
                                       **({"res_type": changes["type"]} if "type" in changes else {})}
            lookup, key = "id", current["id"]
        elif snapshot["exists"]:
            command = "lovelace/resources/create"
            payload = {"res_type": wanted["type"], "url": wanted["url"]}
            lookup, key = "url", wanted["url"]
        elif current is not None:
            command = "lovelace/resources/delete"
            payload = {"resource_id": current["id"]}
            lookup, key = "id", current["id"]
        else:
            return _tool_success(json.dumps({"status": "verified", "already_applied": True})), "allowed", tool
        before = resource_snapshot(await _storage_items(hass), lookup, key)
        expected = {**before, "exists": snapshot["exists"],
                    "resource": {k: wanted[k] for k in ("url", "type")} if snapshot["exists"] else None}
        recovery = data.config_recovery
        async with recovery.resource(RECOVERY_RESOURCE):
            rid = await recovery.begin(RECOVERY_RESOURCE, "dashboard_resource_restore", access_key.id,
                                       before, expected=expected)
            try:
                await recovery.mark(rid, "dispatching")
                if await read_resource_snapshot(hass, lookup, key) != before:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("The dashboard resource changed before restore dispatch."), "invalid_request", tool
                await _check_authority_before_dispatch(recovery, rid)
                try:
                    await async_ws_command(hass, command, payload)
                except WsDispatchError as exc:
                    await recovery.mark(rid, "not_applied" if isinstance(exc, WsCommandNotDispatchedError) else "unknown",
                                        error="command_rejected")
                    return _tool_error(f"Dashboard resource restore failed: {exc}"), "invalid_request", tool
                observed = await read_resource_snapshot(hass, lookup, key)
                if not _matches_expected(observed, expected):
                    await recovery.mark(rid, "partial", observed=observed)
                    return _tool_success(json.dumps({
                        "status": "partial", "recovery_id": rid,
                        "message": "The dashboard resource reads back differently; do not retry automatically.",
                    })), "allowed", tool
                await recovery.mark(rid, "verified", observed_hash=content_hash(observed))
                await recovery.set_source(rid, observed)
                return _tool_success(json.dumps({
                    "status": "verified", "recovery_id": rid, "resource": observed.get("resource"),
                    "note": _RELOAD_NOTE,
                })), "allowed", tool
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as exc:
        return _tool_error(str(exc)), "invalid_request", tool
    except AuthorityChangedError:
        raise
    except Exception:  # noqa: BLE001 - keep an uncertain outcome in the recovery store
        return _tool_error("Dashboard resource restore outcome is uncertain; inspect configuration recovery "
                           "before retrying."), "invalid_request", tool
