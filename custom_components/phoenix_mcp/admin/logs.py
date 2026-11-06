"""Admin views over the audit and conversation logs."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..const import DOMAIN
from ..conversation_log import recorder
from ..data import PhoenixData
from ..view_base import PhoenixView, _err
from .common import _audit_admin, _ok, _parse_pagination, _read_body, _request_time, require_admin


class PhoenixAdminAccessKeyAuditView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys/{access_key_id}/audit - paginated audit log for one access key."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/audit"
    name = "api:phoenix-mcp:admin:access-key_audit"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        paged = _parse_pagination(request, rid, default_limit=100, max_limit=500)
        if isinstance(paged, web.Response):
            return paged
        limit, offset = paged

        outcome_filter = request.query.get("outcome")
        ip_filter = request.query.get("ip")

        entries = data.audit.query(
            access_key_id=access_key_id,
            outcome=outcome_filter,
            client_ip=ip_filter,
            limit=limit,
            offset=offset,
        )
        if entries is None:
            return _err(
                "invalid_request", f"Unknown outcome filter: {outcome_filter!r}.",
                400, rid, key="outcomeFilterUnknown",
                params={"value": outcome_filter or ""},
            )
        return _ok([e.to_dict() for e in entries], request_id=rid)


class PhoenixAdminConversationsView(PhoenixView):
    """Administrator-only bounded conversation summaries and explicit erasure."""

    url = "/api/phoenix-mcp/admin/conversations"
    name = "api:phoenix-mcp:admin:conversations"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        paged = _parse_pagination(request, rid, default_limit=25, max_limit=100)
        if isinstance(paged, web.Response):
            return paged
        since_raw = request.query.get("since", "")
        since = _request_time(since_raw) if since_raw else None
        if since_raw and since is None:
            return _err("invalid_request", "Invalid since timestamp.", 400, rid, key="sinceTimestampInvalid")
        log = recorder(self.hass.data[DOMAIN])
        return _ok(log.query(limit=paged[0], offset=paged[1], grouped=request.query.get("grouped") == "true",
                             search=request.query.get("search", "")[:512],
                             source=request.query.get("source", "")[:64],
                             outcome=request.query.get("outcome", "")[:64],
                             since=since.isoformat() if since else ""), request_id=rid)

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        if set(body) != {"confirm"} or body.get("confirm") is not True:
            return _err("invalid_request", "Confirm clearing conversation history.", 400, rid,
                        key="conversationClearConfirm")
        log = recorder(self.hass.data[DOMAIN])
        await log.async_clear()
        if not log.available:
            return _err("service_unavailable", "Conversation history could not be saved.", 503, rid,
                        key="conversationStorageUnavailable")
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"cleared": True}, request_id=rid)


class PhoenixAdminConversationView(PhoenixView):
    """Fetch full sanitized evidence only when an administrator opens an entry."""

    url = "/api/phoenix-mcp/admin/conversations/{entry_id}"
    name = "api:phoenix-mcp:admin:conversation"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        entry = recorder(self.hass.data[DOMAIN]).detail(entry_id)
        if entry is None:
            return _err("not_found", "Conversation entry is no longer available.", 404, rid,
                        key="conversationNotFound")
        return _ok(entry, request_id=rid)


class PhoenixAdminAuditView(PhoenixView):
    """GET /api/phoenix-mcp/admin/audit - paginated global audit log with optional filters."""

    url = "/api/phoenix-mcp/admin/audit"
    name = "api:phoenix-mcp:admin:audit"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]

        paged = _parse_pagination(request, rid, default_limit=100, max_limit=500)
        if isinstance(paged, web.Response):
            return paged
        limit, offset = paged

        access_key_id_filter = request.query.get("access_key_id")
        outcome_filter = request.query.get("outcome")
        ip_filter = request.query.get("ip")
        method_filter = request.query.get("method")
        resource_filter = request.query.get("resource")
        request_id_filter = request.query.get("request_id")
        since_raw = request.query.get("since")
        # Audit timestamps are aware UTC; a timezone-less since would raise
        # TypeError inside the filter. Naive input is treated as local time.
        since_filter = _request_time(since_raw) if since_raw else None
        if since_raw and since_filter is None:
            return _err(
                "invalid_request", "Invalid since timestamp.", 400, rid,
                key="sinceTimestampInvalid",
            )

        filter_kwargs: dict[str, Any] = {
            "access_key_id": access_key_id_filter,
            "outcome": outcome_filter,
            "client_ip": ip_filter,
            "method": method_filter,
            "resource": resource_filter,
            "request_id": request_id_filter,
            "since": since_filter,
        }
        total = data.audit.count(**filter_kwargs)
        if total is None:
            return _err(
                "invalid_request", f"Unknown outcome filter: {outcome_filter!r}.",
                400, rid, key="outcomeFilterUnknown",
                params={"value": outcome_filter or ""},
            )
        entries = data.audit.query(**filter_kwargs, limit=limit, offset=offset) or []
        return _ok({
            "entries": [e.to_dict() for e in entries],
            "total": total,
        }, request_id=rid)
