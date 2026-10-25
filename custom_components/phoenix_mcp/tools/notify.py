"""Send notifications and read Home Assistant's persistent ones.

Sending needs cap_notify, which Confirm can gate, because a message reaches a
person's phone or screen. A notify entity also needs WRITE on it and passes MESA;
a legacy notify service (one per phone or channel) has no entity, so the
capability alone decides, as for other targetless actions. Mobile app command
messages (request_location_update, command_*, TTS and the like) act on the phone
rather than inform anyone and are refused.

Reading needs cap_log_read. Phoenix's own operator notifications (approval
requests and rate-limit notices, which can describe other access keys' activity)
are never shown, and text is scrubbed of addresses and of entity IDs the access
key cannot read.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from homeassistant.core import HomeAssistant

from ..access_key_store import AccessKeyRecord
from ..const import DOMAIN
from ..data import PhoenixData
from ..helpers import diff_summary_fields, redact_diagnostics, redact_inaccessible_entity_ids_in_text
from ..household_authority import household_service_kwargs
from ..mesa import async_apply_mesa_to_call, fire_mesa_blocked_event
from ..policy_engine import Permission, resolve
from ..request_authority import check_request_authority, mark_dispatch_started
from ..tool_common import (
    MESA_DENIED_MESSAGE, _approved_exec_ctx, _gate, _pending_or_inline, _tool_error, _tool_success,
    longest_fitting,
)
from ..ws_dispatch import async_ws_command

_PERSISTENT = "persistent_notification"
_NOTIFY_TIMEOUT_SECONDS = 15
# Messages the mobile app treats as commands to the phone, not text for a person.
_MOBILE_APP_COMMANDS = frozenset({"request_location_update", "clear_notification", "remove_channel", "TTS"})
_OPERATOR_PREFIX = f"{DOMAIN}_"


def _writable_notify_entity(entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> bool:
    return (entity_id.startswith("notify.") and hass.states.get(entity_id) is not None
            and resolve(entity_id, access_key, hass) is Permission.WRITE)


def _legacy_services(hass: HomeAssistant) -> list[str]:
    """Home Assistant's per-target notify services, without the entity service and the UI channel."""
    return sorted(name for name in hass.services.async_services_for_domain("notify")
                  if name not in ("send_message", _PERSISTENT))


def _targets(access_key: AccessKeyRecord, hass: HomeAssistant) -> list[str]:
    entities = sorted(state.entity_id for state in hass.states.async_all("notify")
                      if _writable_notify_entity(state.entity_id, access_key, hass))
    return [_PERSISTENT, *entities, *_legacy_services(hass)]


def _resolve_target(raw: Any, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[str, str] | str:
    """(kind, name) for a target, or a refusal that lists the targets this key can use."""
    target = raw.strip() if isinstance(raw, str) else ""
    if target.startswith("notify.") and hass.states.get(target) is not None:
        if _writable_notify_entity(target, access_key, hass):
            return "entity", target
    else:
        name = target.removeprefix("notify.")
        if name == _PERSISTENT:
            return "persistent", _PERSISTENT
        if name and name in _legacy_services(hass):
            return "service", name
    return ("target must be one of: " + ", ".join(_targets(access_key, hass))
            + ". persistent_notification shows it in Home Assistant's notification list.")


def _message_error(kind: str, message: Any, title: Any) -> str | None:
    if not isinstance(message, str) or not message.strip():
        return "message is required: the text to send."
    if title is not None and not isinstance(title, str):
        return "title must be text."
    if kind == "service" and (message.strip() in _MOBILE_APP_COMMANDS or message.strip().startswith("command_")):
        return ("That message is a mobile app command that acts on the phone rather than a notification for a "
                "person, so it is not sent here.")
    return None


def _build_diff_send_notification(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    target = str(args.get("target") or "")
    return {
        "kind": "system_action",
        **diff_summary_fields("send_notification", target=target),
        "target": {"type": "notification", "id": target, "label": target},
        "preview": {"title": args.get("title"), "message": args.get("message")},
    }


async def _tool_send_notification(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: send one notification, behind cap_notify's Confirm when set."""
    tool = "send_notification"
    resolved = _resolve_target(args.get("target"), access_key, hass)
    if isinstance(resolved, str):
        return _tool_error(resolved), "invalid_request", tool
    if error := _message_error(resolved[0], args.get("message"), args.get("title")):
        return _tool_error(error), "invalid_request", tool
    blocked = await _gate(
        "cap_notify", access_key, hass, data, tool_name=tool, args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_send_notification(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_send_notification(args, access_key, hass, data, request_id=request_id, client_ip=client_ip)


async def _execute_send_notification(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """Send it, re-resolving the target and MESA now, since an approval may have waited."""
    tool = "send_notification"
    resolved = _resolve_target(args.get("target"), access_key, hass)
    if isinstance(resolved, str):
        return _tool_error(resolved), "invalid_request", tool
    kind, name = resolved
    message, title = args.get("message"), args.get("title")
    if error := _message_error(kind, message, title):
        return _tool_error(error), "invalid_request", tool
    fields: dict[str, Any] = {"message": message, **({"title": title} if title else {})}
    if kind == "entity":
        outcome = await async_apply_mesa_to_call(
            hass, data, access_key, domain="notify", service="send_message", service_data=fields,
            entities=[name], request_id=request_id or tool, client_ip=client_ip, session_id=request_id or tool,
            confirm_approved=_approved_exec_ctx.get(), approval_tool_name=tool, approval_args=args,
            approval_diff=_build_diff_send_notification(args, access_key, hass), require_all=True,
        )
        if outcome.blocked:
            fire_mesa_blocked_event(hass, access_key, outcome.blocked)
        if outcome.decision == "pending":
            return await _pending_or_inline(hass, data, access_key, outcome.approval)
        if outcome.decision == "deny":
            return _tool_error(MESA_DENIED_MESSAGE), "denied", name
        domain, service, service_data = "notify", "send_message", {"entity_id": name, **fields}
    elif kind == "persistent":
        notification_id = uuid.uuid4().hex
        domain, service, service_data = _PERSISTENT, "create", {**fields, "notification_id": notification_id}
    else:
        domain, service, service_data = "notify", name, fields
    check_request_authority()
    mark_dispatch_started()
    await asyncio.wait_for(hass.services.async_call(
        domain, service, service_data, blocking=True, **household_service_kwargs(hass, domain, service),
    ), _NOTIFY_TIMEOUT_SECONDS)
    body: dict[str, Any] = {"sent": True, "target": name}
    if kind == "persistent":
        shown = await async_ws_command(hass, "persistent_notification/get", {})
        body["notification_id"] = notification_id
        body["verified"] = any(isinstance(row, dict) and row.get("notification_id") == notification_id
                               for row in shown or ())
    else:
        body["verified"] = None
        body["note"] = "Handed to the notify service; Home Assistant does not report delivery to the device."
    return _tool_success(_dumps(body)), "allowed", name


def _dumps(body: Any) -> str:
    return json.dumps(body, default=str)


async def _tool_get_notifications(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: Home Assistant's persistent notifications, newest first, without Phoenix's own."""
    if _PERSISTENT not in hass.config.components:
        # Without the component there is no notification list, so none to show.
        rows: Any = []
    else:
        rows = await async_ws_command(hass, "persistent_notification/get", {})
    shown = []
    for row in rows or ():
        if not isinstance(row, dict) or str(row.get("notification_id") or "").startswith(_OPERATOR_PREFIX):
            continue
        created = row.get("created_at")
        item = {
            "notification_id": row.get("notification_id"),
            "title": row.get("title"),
            "message": row.get("message"),
            "created_at": created.isoformat() if created is not None and hasattr(created, "isoformat") else created,
        }
        item = redact_diagnostics(item)
        for key in ("title", "message"):
            if isinstance(item.get(key), str):
                item[key] = redact_inaccessible_entity_ids_in_text(item[key], access_key, hass)
        shown.append(item)
    shown.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def build(count: int) -> dict:
        body: dict[str, Any] = {"count": count, "total": len(shown), "has_more": count < len(shown),
                                "notifications": shown[:count]}
        if count < len(shown):
            body["warnings"] = [f"The result is full: the {len(shown) - count} oldest notifications were not listed."]
        return body

    return _tool_success(_dumps(longest_fitting(build, len(shown)))), "allowed", "get_notifications"
