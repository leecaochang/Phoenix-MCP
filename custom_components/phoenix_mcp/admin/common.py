"""Shared admin plumbing: the admin gate, responses, bounded bodies and input checks."""

from __future__ import annotations

from collections.abc import Collection, Callable
from datetime import datetime
import functools
import json
import logging
import re
from typing import Any, TypeGuard
import uuid

from aiohttp import web
from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import as_utc

from ..const import DOMAIN, MAX_REQUEST_BODY_BYTES, RUNTIME_READY_KEY
from ..data import PhoenixData
from ..durable_storage import MesaSaveError
from ..tool_input import parse_json_body
from ..view_base import _err
from ..time_text import parse_datetime


_LOGGER = logging.getLogger(__name__)


def _ok(body: Any, status: int = 200, request_id: str = "") -> web.Response:
    """Return a JSON success response. Uses request_id if supplied, else generates one."""
    rid = request_id or str(uuid.uuid4())
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body, default=str),
        headers={"X-Phoenix-Request-ID": rid, "Cache-Control": "no-store"},
    )


def require_admin(method: Callable) -> Callable:
    """Decorate a view method to require HA admin and log it under a per-request ID."""
    return _admin_only(method, ready_required=True)


def require_admin_before_ready(method: Callable) -> Callable:
    """Apply require_admin to a view that must answer while setup is still running."""
    return _admin_only(method, ready_required=False)


def _admin_only(method: Callable, *, ready_required: bool) -> Callable:
    @functools.wraps(method)
    async def wrapper(self, request: web.Request, **kwargs: Any) -> web.Response:
        request_id = str(uuid.uuid4())
        request["phoenix_mcp_rid"] = request_id
        user = request.get(KEY_HASS_USER)
        if not request.get(KEY_AUTHENTICATED):
            _LOGGER.info("Admin %s %s unauthenticated rid=%s", request.method, request.path, request_id)
            return _err("unauthorized", "Authentication required.", 401, request_id, key="authRequired")
        if not user or not user.is_admin:
            _LOGGER.info("Admin %s %s forbidden rid=%s", request.method, request.path, request_id)
            return _err("forbidden", "Admin access required.", 403, request_id, key="adminRequired")
        if ready_required and self.hass.data.get(RUNTIME_READY_KEY, True) is False:
            return _err(
                "service_unavailable",
                "Phoenix MCP setup is not complete.",
                503,
                request_id,
                key="setupIncomplete",
            )
        # Logs user.id (UUID) rather than user.name. UUID is stable and non-spoofable;
        # user.name can be changed by the admin. Intentional.
        _LOGGER.info("Admin %s %s rid=%s user=%s", request.method, request.path, request_id, user.id)
        try:
            return await method(self, request, **kwargs)
        except MesaSaveError:
            # One place for every profile write: the runtime has already returned
            # to the stored policy, so the operator is told the change did not land.
            _LOGGER.error("Admin %s %s could not save MESA profiles rid=%s", request.method, request.path, request_id)
            return _err("service_unavailable", "The MESA change could not be saved.", 503, request_id, key="mesaSaveFailed")
    return wrapper


async def _read_body(
    request: web.Request, request_id: str = "", *, limit: int = MAX_REQUEST_BODY_BYTES,
) -> dict | web.Response:
    """Read and parse the request body as a JSON object of at most `limit` bytes.

    Returns an empty dict for requests with no body. Returns an error response
    on read failure, invalid JSON, or a non-object body.
    """
    if request.content_length is not None and request.content_length > limit:
        return _err("request_too_large", "Request body too large.", 413, request_id, key="bodyTooLarge")

    # aiohttp's StreamReader.read(n) is a SHORT read: it returns as soon as the
    # buffer has ANY data, up to n bytes, not once n bytes have actually
    # arrived. A single read(MAX_REQUEST_BODY_BYTES + 1) call would therefore
    # silently truncate a larger body that arrived across multiple TCP/chunked
    # segments (an intermittent "Invalid JSON body." on bigger payloads, e.g.
    # importing many MESA profiles). Loop until EOF, accumulating chunks and
    # bailing as soon as the cap is exceeded.
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await request.content.read(limit - total + 1)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                return _err("request_too_large", "Request body too large.", 413, request_id, key="bodyTooLarge")
            at_eof = getattr(request.content, "at_eof", None)
            if callable(at_eof) and at_eof():
                break
    except Exception:
        return _err("invalid_request", "Failed to read request body.", 400, request_id, key="bodyUnreadable")

    body_bytes = b"".join(chunks)

    if not body_bytes:
        return {}

    try:
        parsed = parse_json_body(body_bytes)
    except (ValueError, RecursionError):  # Invalid JSON or text, numeric overflow, or excessive nesting.
        # Diagnostic only (never logs the body content itself). The loop above
        # reads to EOF, so a byte count short of Content-Length points at
        # something upstream of Phoenix MCP (a proxy or tunnel), not this reader.
        _LOGGER.warning(
            "%s %s: invalid JSON body (%d bytes read, Content-Length %s) rid=%s",
            request.method, request.path, len(body_bytes),
            request.content_length, request_id,
        )
        return _err("invalid_request", "Invalid JSON body.", 400, request_id, key="badJson")

    if not isinstance(parsed, dict):
        return _err("invalid_request", "Request body must be a JSON object.", 400, request_id, key="bodyNotObject")

    return parsed


def _in_menu(value: Any, allowed: Collection[str]) -> TypeGuard[str]:
    """Membership for a value from a request body, which may be unhashable."""
    return isinstance(value, str) and value in allowed


def _request_time(raw: str) -> datetime | None:
    """A request's timestamp as aware UTC, or None when it names no real moment.

    An impossible date raises from the parser, and the far ends of the calendar
    overflow in the conversion, so neither can be left to reach the caller.
    """
    try:
        parsed = parse_datetime(raw)
        return None if parsed is None else as_utc(parsed)
    except (ValueError, OverflowError):
        return None


_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*\Z")

_ENTITY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+\Z")

_MAX_NODE_ID_LEN = 255

_INJECTION_CHARS = frozenset("<>\"'&;")


def _validate_node_id(node_type: str, node_id: str, rid: str) -> web.Response | None:
    """Return an error response if node_id fails length, injection, or format checks."""
    if len(node_id) > _MAX_NODE_ID_LEN:
        return _err("invalid_request", "Node ID is too long.", 400, rid, key="nodeIdTooLong")
    if any(c in node_id for c in _INJECTION_CHARS):
        return _err("invalid_request", "Node ID contains invalid characters.", 400, rid, key="nodeIdInvalidChars")
    if node_type == "domains" and not _DOMAIN_RE.match(node_id):
        return _err("invalid_request", "Invalid domain name.", 400, rid, key="badDomain")
    if node_type == "entities" and not _ENTITY_RE.match(node_id):
        return _err("invalid_request", "Invalid entity ID format.", 400, rid, key="badEntityId")
    return None


def _parse_pagination(
    request: web.Request,
    rid: str,
    *,
    default_limit: int,
    max_limit: int,
) -> tuple[int, int] | web.Response:
    """Parse limit and offset, or return a 400 response.

    A negative limit is a client error, and malformed input is reported, never defaulted.
    """
    try:
        limit = int(request.query.get("limit", default_limit))
        offset = int(request.query.get("offset", 0))
    except (TypeError, ValueError):
        return _err("invalid_request", "Invalid pagination parameters.", 400, rid, key="badPagination")
    if limit < 0 or offset < 0:
        return _err(
            "invalid_request", "limit and offset must be non-negative.", 400, rid,
            key="paginationNonNegative",
        )
    return min(limit, max_limit), offset


def _audit_admin(hass: HomeAssistant, request, rid, resource) -> None:
    data: PhoenixData = hass.data[DOMAIN]
    user = request[KEY_HASS_USER]
    data.audit.record(
        request_id=rid,
        access_key_id="admin",
        access_key_name=f"admin:{user.id}",
        method=request.method,
        resource=resource,
        ha_user_id=user.id,
        outcome="allowed",
        client_ip=request.remote or "",
        settings=data.store.get_settings(),
    )
