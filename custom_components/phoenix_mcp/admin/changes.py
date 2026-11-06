"""Admin Changes views: attention items, configuration recovery, versions and restore."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import cast

from aiohttp import web
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.core import HomeAssistant

from ..audit import Outcome
from ..config_recovery import (
    ConfigurationRecoveryError, recovery_error_key, recovery_error_params, runtime_identity,
)
from ..const import DOMAIN
from ..data import PhoenixData
from ..esphome_yaml import redact_esphome_text
from ..template_snapshots import template_snapshot
from ..tools.esphome import _read_esphome_secrets
from ..tools.helper import _public_helper_config, _storage_items
from ..tools.restore import async_restore_configuration, async_restore_version, restore_refusal
from ..view_base import PhoenixView, _err
from ..ws_dispatch import async_ws_command
from .common import _audit_admin, _ok, _parse_pagination, _read_body, require_admin

_LOGGER = logging.getLogger(__name__)


def _tool_text(tool_result: dict) -> str:
    """A tool result's text content, joined."""
    return " ".join(
        item.get("text", "")
        for item in tool_result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    ).strip()


def _unverified_restore(tool_result: dict) -> bool:
    """Whether a restore ran but its result reports a partial or unknown readback."""
    for item in tool_result.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            body = json.loads(item.get("text", ""))
        except (TypeError, ValueError):
            continue
        if isinstance(body, dict) and body.get("status") in ("partial", "unknown"):
            return True
    return False


def _recovery_refusal(detail: str) -> str | None:
    """Return the refusal text inside a recovery executor's JSON error, or None."""
    try:
        body = json.loads(detail)
    except (TypeError, ValueError):
        return None
    if isinstance(body, dict) and isinstance(body.get("error"), str) and body["error"]:
        return body["error"]
    return None


# Restore refusals async_restore_version itself raises, by their exact agent text.
_RESTORE_NO_CONFIG_SIDE = "This version has no configuration to restore on that side."

_RESTORE_YAML_TAGS = (
    "This version contains YAML tags such as !secret or !include and "
    "cannot be restored automatically. Re-create it manually."
)

_RESTORE_HELPER_NOT_VERIFIED = (
    "Helper restoration was not verified. Inspect configuration recovery before retrying."
)


def _version_summary(r) -> dict:
    """Compact list projection that omits the (potentially large) before/after configs."""
    return {
        "id": r.id,
        "resource_type": r.resource_type,
        "resource_id": r.resource_id,
        "alias": r.alias,
        "action": r.action,
        "access_key_id": r.access_key_id,
        "access_key_name": r.access_key_name,
        "approved_by_user_id": r.approved_by_user_id,
        "timestamp": r.timestamp,
        "summary": r.summary,
        "summary_key": r.summary_key,
        "summary_params": r.summary_params,
        "has_before": r.before is not None,
        "has_after": r.after is not None,
    }


class PhoenixAdminAttentionView(PhoenixView):
    """Admin-only unresolved actions and unavailable Phoenix safety services."""

    url = "/api/phoenix-mcp/admin/attention"
    name = "api:phoenix-mcp:admin:attention"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        items = []
        agent_items = []
        for row in data.agent_recovery.admin_records():
            access_key = data.store.get_access_key_by_id(row["access_key_id"])
            agent_items.append({"id": row["request_id"], "kind": "agent", "tool": row["tool"],
                                "access_key_name": access_key.name if access_key else row["access_key_id"],
                                "started_at": row["started_at"], "dispatch_result": row["dispatch_result"]})
        # A calendar write the calendar journal records says more than the same
        # call's agent row (what it changed, with a snapshot), so the agent row
        # gives way to it, whatever its review state.
        calendar_requests: set[str] = set()
        journal = data.calendar_writes
        if journal is not None:
            try:
                async with journal.lock:
                    rows = await journal.load()
                    calendar_requests = {row["request_id"] for row in rows if isinstance(row.get("request_id"), str)}
                    for row in rows:
                        if row["status"] != "outcome_unknown" or row["receipt_id"] in journal.active:
                            continue
                        access_key = data.store.get_access_key_by_id(row["access_key_id"])
                        items.append({"id": row["receipt_id"], "kind": "calendar",
                                      "tool": row["operation"] + "_calendar_event",
                                      "access_key_name": access_key.name if access_key else row["access_key_id"],
                                      "started_at": row["created_at"], "entity_id": row["entity_id"],
                                      "dispatch_result": "unknown"})
            except Exception:
                items.append({"id": "calendar_storage", "kind": "calendar_storage"})
        items[:0] = [item for item in agent_items if item["id"] not in calendar_requests]
        if data.mesa_setup_failed:
            items.append({"id": "mesa", "kind": "mesa"})
        if data.audit.save_failed is True:
            items.append({"id": "audit_storage", "kind": "audit_storage"})
        return _ok({"items": items}, request_id=rid)


class PhoenixAdminAttentionReviewView(PhoenixView):
    """Release one reviewed barrier without replay, rollback, or bulk dismissal."""

    url = "/api/phoenix-mcp/admin/attention/{kind}/{record_id}"
    name = "api:phoenix-mcp:admin:attention:review"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, kind: str, record_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        if body.get("checked_outcome") is not True:
            return _err("invalid_request", "Check the action outcome before continuing.", 400, rid,
                        key="attentionCheckRequired")
        user = await self.hass.auth.async_get_user(request[KEY_HASS_USER].id)
        if user is None or not user.is_active or not user.is_admin:
            return _err("forbidden", "Admin access required.", 403, rid, key="adminRequired")
        data: PhoenixData = self.hass.data[DOMAIN]
        if kind != "agent" and kind != "calendar":
            return _err("not_found", "This item cannot be acknowledged.", 404, rid, key="attentionNotReviewable")
        try:
            if kind == "agent":
                reviewed = await data.agent_recovery.reviewed(record_id)
            else:
                reviewed = data.calendar_writes is not None and await data.calendar_writes.reviewed(record_id)
        except Exception:
            _LOGGER.exception("Could not save action review")
            return _err("service_unavailable", "The review could not be saved. The action still needs review.",
                        503, rid, key="attentionSaveFailed")
        if not reviewed:
            return _err("conflict", "This action is running or has already been reviewed. Refresh the list.",
                        409, rid, key="attentionReviewConflict")
        _audit_admin(self.hass, request, rid, f"attention/{kind}/{record_id}")
        return _ok({"reviewed": True}, request_id=rid)


class PhoenixAdminConfigurationRecoveryView(PhoenixView):
    """Admin-only recovery artifacts, independent of rotating Changes history."""

    url = "/api/phoenix-mcp/admin/config-recovery"
    name = "api:phoenix-mcp:admin:config-recovery"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: PhoenixData = self.hass.data[DOMAIN]
        try:
            records = data.config_recovery.records()
        except ConfigurationRecoveryError:
            return _err("service_unavailable", "Configuration recovery storage could not be loaded.", 503, request["phoenix_mcp_rid"], key="recoveryStorageUnavailable")
        return _ok({"records": records}, request_id=request["phoenix_mcp_rid"])


class PhoenixAdminConfigurationRecoveryRecordView(PhoenixView):
    """Inspect current state, restore a saved side, or explicitly reconcile."""

    url = "/api/phoenix-mcp/admin/config-recovery/{recovery_id}"
    name = "api:phoenix-mcp:admin:config-recovery:record"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, recovery_id: str) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        user = request[KEY_HASS_USER]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        record = data.config_recovery.get(recovery_id)
        if record is None:
            return _err("not_found", "Recovery record not found.", 404, rid, key="recoveryNotFound")
        action = body.get("action")
        try:
            if action == "inspect":
                snapshot = record.get("source") or record.get("before")
                if isinstance(snapshot, dict) and snapshot.get("snapshot_type") == "restart_receipt":
                    return _ok({"runtime_replaced": runtime_identity(self.hass) != snapshot["runtime_identity"],
                                "execution_attribution": "unproven"}, request_id=rid)
                if isinstance(snapshot, dict) and snapshot.get("snapshot_type") == "storage_helper":
                    helper_type = snapshot["helper_type"]
                    rows = _storage_items(helper_type, await async_ws_command(self.hass, f"{helper_type}/list", {}))
                    if rows is None:
                        raise ConfigurationRecoveryError("Helper storage could not be read.")
                    helper_id = record.get("observations", {}).get("replacement_helper_id") or snapshot["helper_id"]
                    current = next((row for row in rows if row.get("id") == helper_id), None)
                    return _ok({"observed": _public_helper_config(snapshot["helper_type"], current) if isinstance(current, dict) else None,
                                "helper_present": current is not None, "execution_attribution": "unproven"}, request_id=rid)
                if not isinstance(snapshot, dict) or snapshot.get("snapshot_type") != "template_helper":
                    return _ok({"status": "manual_verification_required"}, request_id=rid)
                replacement = record.get("observations", {}).get("replacement_entry_id")
                entry = self.hass.config_entries.async_get_entry(replacement or snapshot["entry_id"])
                observed = template_snapshot(self.hass, entry) if entry is not None else None
                return _ok({"observed": observed, "entry_present": entry is not None,
                            "execution_attribution": "unproven"}, request_id=rid)
            if action in ("reviewed", "delete"):
                await data.config_recovery.reviewed(recovery_id, user.id, delete=action == "delete")
                result = {"status": action}
            elif action == "restore":
                if any(item["id"] == recovery_id and item["active"] for item in data.config_recovery.records()):
                    raise ConfigurationRecoveryError("The configuration operation is still active.")
                if record["phase"] not in ("verified", "not_applied", "reviewed"):
                    raise ConfigurationRecoveryError("Review the inactive recovery record before restoring it.")
                side = body.get("side")
                if side not in ("before", "source") or not isinstance(record.get(side), dict):
                    return _err("invalid_request", "Select an available before or source snapshot.", 400, rid, key="recoverySideRequired")
                snapshot = record[side]
                resource_type = None
                if snapshot.get("snapshot_type") == "storage_helper":
                    resource_type = "helper"
                    resource_id = f"{snapshot['helper_type']}:{snapshot['helper_id']}"
                    snapshot = snapshot["config"]
                elif snapshot.get("snapshot_type") == "template_helper":
                    resource_type = "config_entry"
                    resource_id = snapshot["entry_id"]
                elif snapshot.get("snapshot_type") in (
                    "energy_preferences", "authored_file", "yaml_file", "blueprint_source",
                    "dashboard_registry", "dashboard_layout", "dashboard_resource",
                ):
                    restored, outcome, _ = await async_restore_configuration(
                        recovery_id, side, user.id, self.hass, data,
                    )
                    result = {"result": restored, "outcome": outcome}
                else:
                    return _err("invalid_request", "This recovery artifact requires manual restoration.", 400, rid, key="recoveryManualOnly")
                if resource_type is not None:
                    restored, outcome, _ = await async_restore_version(SimpleNamespace(
                        before=snapshot, after=None, resource_type=resource_type,
                        resource_id=resource_id,
                    ), user.id, self.hass, data, "before")
                    result = {"result": restored, "outcome": outcome}
            else:
                return _err("invalid_request", "Unknown recovery action.", 400, rid, key="recoveryUnknownAction")
        except ConfigurationRecoveryError as exc:
            return _err("conflict", str(exc), 409, rid, key=recovery_error_key(str(exc)),
                        params=recovery_error_params(str(exc)))
        data.audit.record(request_id=rid, access_key_id="admin", access_key_name=f"admin:{user.id}",
                          method=f"config_recovery/{action}", resource=recovery_id,
                          outcome=result.get("outcome", "allowed"),
                          client_ip="", settings=data.store.get_settings())
        return _ok(result, request_id=rid)


class PhoenixAdminVersionsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/versions - configuration version history for one resource.

    Requires resource_type and resource_id query params. Returns compact summaries
    (no before/after); fetch a single version for the full diff payload.
    """

    url = "/api/phoenix-mcp/admin/versions"
    name = "api:phoenix-mcp:admin:versions"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        resource_type = request.query.get("resource_type")
        resource_id = request.query.get("resource_id")
        if resource_type and resource_id:
            records = data.versions.list_for(resource_type, resource_id)
            total = len(records)
        elif not resource_type and not resource_id:
            paged = _parse_pagination(request, rid, default_limit=50, max_limit=500)
            if isinstance(paged, web.Response):
                return paged
            limit, offset = paged
            records = data.versions.list_recent(limit, offset)
            total = data.versions.count_recent()
        else:
            return _err(
                "invalid_request",
                "Provide both resource_type and resource_id, or neither for the recent feed.",
                400, rid, key="versionResourcePairRequired",
            )
        return _ok({
            "resource_type": resource_type,
            "resource_id": resource_id,
            "versions": [_version_summary(r) for r in records],
            "total": total,
        }, request_id=rid)


_ESPHOME_VERSION_WITHHELD = (
    "# Phoenix MCP withheld this snapshot: its credentials could not be located, "
    "because the content does not parse as YAML. The stored version is unchanged; "
    "read the file in the ESPHome dashboard."
)


async def _mask_esphome_version(hass: HomeAssistant, body: dict) -> dict:
    """Mask credentials in an esphome_yaml version's before and after, for display only.

    The stored snapshot stays raw. Fails closed: when masking raises, the raw text is not
    shown.
    """

    try:
        secret_values, _ = await hass.async_add_executor_job(_read_esphome_secrets, hass)
    except Exception:
        secret_values = set()

    def _mask(text: str) -> str:
        return redact_esphome_text(text, secret_values)[0]

    out = dict(body)
    for side in ("before", "after"):
        payload = out.get(side)
        if not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if not isinstance(content, str) or not content:
            continue
        try:
            masked = await hass.async_add_executor_job(_mask, content)
        except Exception:
            masked = _ESPHOME_VERSION_WITHHELD
        out[side] = {**payload, "content": masked}
    return out


class PhoenixAdminVersionView(PhoenixView):
    """GET /api/phoenix-mcp/admin/versions/{version_id} - one version with full before/after."""

    url = "/api/phoenix-mcp/admin/versions/{version_id}"
    name = "api:phoenix-mcp:admin:version"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, version_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        record = data.versions.get(version_id)
        if record is None:
            return _err(
                "not_found", "Version not found.", 404, rid,
                key="versionNotFound",
            )

        body = record.to_dict()
        if record.resource_type == "esphome_yaml":
            body = await _mask_esphome_version(self.hass, body)
        # Sides Restore would refuse whatever the item holds now, with the reason,
        # so Changes says why instead of offering a choice that fails.
        body["restore_refusals"] = {
            side: restore_refusal(record, side, self.hass) for side in ("before", "after")
        }
        return _ok(body, request_id=rid)


class PhoenixAdminVersionRestoreView(PhoenixView):
    """POST /api/phoenix-mcp/admin/versions/{version_id}/restore - re-apply a stored version.

    Existing resources are edited; deleted ones are recreated. Runs with admin
    authority and records the change as a 'rollback' attributed to the admin.
    """

    url = "/api/phoenix-mcp/admin/versions/{version_id}/restore"
    name = "api:phoenix-mcp:admin:version:restore"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, version_id: str) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        user = request[KEY_HASS_USER]
        data: PhoenixData = self.hass.data[DOMAIN]
        record = data.versions.get(version_id)
        if record is None:
            return _err(
                "not_found", "Version not found.", 404, rid,
                key="versionNotFound",
            )
        # The panel sends no body for a default restore; _read_body returns {}
        # for an empty body, 400 for malformed/non-object JSON (which must not
        # silently restore the default side), and 413 over the size cap.
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        side = body.get("side")
        if side not in (None, "before", "after"):
            return _err(
                "invalid_request", "side must be 'before' or 'after'.", 400, rid,
                key="restoreSideInvalid",
            )
        expected_latest = body.get("expected_latest_id")
        if expected_latest is not None and not isinstance(expected_latest, str):
            return _err(
                "invalid_request", "expected_latest_id must be a string or null.", 400, rid,
                key="stringOrNull", params={"field": "expected_latest_id"},
            )
        # The caller's view of the item: when another change landed after it chose
        # Restore, it would be restoring over something it has not seen.
        if expected_latest is not None:
            history = data.versions.list_for(record.resource_type, record.resource_id)
            if (history[0].id if history else None) != expected_latest:
                return _err(
                    "stale_version",
                    "This item has changed since you chose to restore it. Review the comparison and try again.",
                    409, rid, key="restoreStale",
                )
        try:
            tool_result, _outcome, _resource = await async_restore_version(record, user.id, self.hass, data, side)
        except Exception:
            _LOGGER.exception("Version restore failed for %s", version_id)
            return _err(
                "internal_error", "Restore failed.", 500, rid,
                key="restoreFailed",
            )
        is_error = bool(tool_result.get("isError"))
        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            ha_user_id=user.id,
            originating_access_key_id=record.access_key_id,
            method="version/restore",
            resource=f"version_restore:{record.resource_type}:{record.resource_id}",
            outcome=cast(Outcome, _outcome),
            client_ip="",
            settings=data.store.get_settings(),
        )
        # A dispatched restore whose readback did not confirm the chosen version
        # reports status partial or unknown, as a success or (a recreated helper)
        # as an error. It is not a restore, and the change may still have landed;
        # the recovery sweep raises an Approvals request if it still differs.
        # The executors' messages are agent text, so the panel gets its own.
        if _unverified_restore(tool_result) or (is_error and _tool_text(tool_result) == _RESTORE_HELPER_NOT_VERIFIED):
            return _err(
                "restore_unverified",
                "The restore was submitted, but the configuration read back afterwards does not match the "
                "chosen version, so it may be only partly applied. Phoenix rechecks it and asks for review "
                "in Approvals if it still differs. Check the current configuration before restoring again.",
                409, rid, key="restoreUnverified",
            )
        if is_error:
            # Relay the executor's OWN reason: it names the exact cause (a version
            # too large to restore, or one holding configuration file text),
            # which a fixed string would hide. This is an
            # admin-only surface authenticated by an HA session, so there is no
            # caller to keep in the dark, unlike the access-key-facing tools where
            # uniform messages are what prevent an oracle. The restore refusals
            # Phoenix knows are translated; any other executor text is agent
            # English, so the panel labels it as the tool's own report.
            detail = _tool_text(tool_result)
            if detail == _RESTORE_NO_CONFIG_SIDE:
                return _err("invalid_request", detail, 400, rid, key="restoreNoConfigSide")
            if detail == _RESTORE_YAML_TAGS:
                return _err("invalid_request", detail, 400, rid, key="restoreYamlTags")
            refusal = _recovery_refusal(detail)
            if refusal is not None:
                return _err("invalid_request", refusal, 400, rid, key=recovery_error_key(refusal),
                            params=recovery_error_params(refusal))
            if detail:
                return _err(
                    "invalid_request", detail, 400, rid,
                    key="restoreToolReported", params={"detail": detail},
                )
            return _err(
                "invalid_request",
                "Restore could not be applied to the current configuration.",
                400, rid, key="restoreNotApplicable",
            )
        return _ok({
            "restored": True,
            "version_id": version_id,
            "resource_type": record.resource_type,
            "resource_id": record.resource_id,
        }, request_id=rid)
