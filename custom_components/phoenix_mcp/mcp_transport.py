"""MCP Streamable HTTP endpoint for the Phoenix MCP integration."""

from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
import json
import logging
import math
import secrets
import time
from typing import Any, cast

from homeassistant.core import HomeAssistant
from aiohttp import web

from .access_key_store import AccessKeyRecord
from .agent_recovery import tool_is_read_only
from .audit import generate_request_id, Outcome
from .const import (
    DOMAIN, MAX_BATCH_ITEMS, MCP_AUTHORITY_CHANGED_ERROR, MCP_DISCOVER_TTL_MS,
    MCP_LEGACY_PROTOCOL_VERSION_PREFERRED, MCP_LEGACY_PROTOCOL_VERSIONS,
    MCP_LEGACY_SESSION_BUSY_RETRY_SECONDS, MCP_PROTOCOL_VERSIONS, MCP_SSE_KEEPALIVE_SECONDS,
    PHOENIX_VERSION, MAX_REQUEST_BODY_BYTES as _MAX_BODY,
)
from .conversation_log import transport_session
from .data import PhoenixData
from .helpers import (
    build_error_response as _error, fire_rate_limit_events as _fire_rate_limit_events, get_client_ip as _get_client_ip, log_internal_failure, log_request as _log,
)
from .access_key_auth import (
    async_get_authenticated_access_key as _async_get_authenticated_access_key,
)
from .rate_limiter import RateLimitResult
from .request_authority import AuthorityChangedError, request_authority, RequestAuthority
from .tool_common import _progress_ctx, _ProgressBus, _tool_success
from .tool_dispatch import (
    _build_context_json, _build_context_plain, _dispatch_mcp, _jsonrpc_error, _jsonrpc_result,
    _MCP_LEGACY_METHODS, _MCP_METHODS, _MCP_MODERN_METHODS,
)
from .tool_input import _parse_finite_float, _reject_non_finite
from .view_base import PhoenixView


_LOGGER = logging.getLogger(__name__)


def _valid_jsonrpc_id(raw_id: Any) -> bool:
    """Return whether this is an MCP-conforming request id: a string or a non-bool int."""
    if isinstance(raw_id, bool):
        return False
    return isinstance(raw_id, (str, int))


def _sanitize_jsonrpc_id(raw_id: Any) -> str | int | None:
    """Reduce a JSON-RPC id to something safe for an error RESPONSE envelope."""
    return raw_id if _valid_jsonrpc_id(raw_id) else None


_MCP_MODERN_VERSION = "2026-07-28"
_MCP_PROTOCOL_META = "io.modelcontextprotocol/protocolVersion"
_MCP_CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"
_MCP_CLIENT_CAPABILITIES_META = "io.modelcontextprotocol/clientCapabilities"
_MCP_SERVER_INFO_META = "io.modelcontextprotocol/serverInfo"
_MCP_NAMED_METHOD_FIELDS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
}
_MCP_CACHEABLE_METHODS = frozenset({
    "server/discover", "tools/list", "prompts/list", "resources/list",
    "resources/templates/list", "resources/read",
})
_MCP_LEGACY_SESSION_TTL_SECONDS = 3600.0
_MCP_LEGACY_UNINITIALIZED_TTL_SECONDS = 60.0
_MCP_LEGACY_SESSION_LIMIT = 256
_MCP_LEGACY_SESSION_ACCESS_KEY_LIMIT = 8
# The legacy session this request holds open, released when the request ends.
_legacy_session_in_use: ContextVar[dict | None] = ContextVar("phoenix_legacy_session_in_use", default=None)


def _protocol_error_response(
    msg_id: Any,
    code: int,
    message: str,
    request_id: str,
    *,
    data: dict | None = None,
    status: int = 400,
    modern: bool = False,
) -> web.Response:
    """Return a protocol-level JSON-RPC error with its required HTTP status."""
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(_jsonrpc_error(msg_id, code, message, data=data, modern=modern)),
        headers={"X-Phoenix-Request-ID": request_id},
    )


def _audit_mcp_transport(
    data: PhoenixData,
    access_key: AccessKeyRecord,
    request_id: str,
    client_ip: str,
    *,
    body: Any = None,
    outcome: Outcome = "invalid_request",
    resource: str = "/api/phoenix-mcp",
) -> None:
    """Audit an authenticated MCP message that does not reach dispatch."""
    method = body.get("method") if isinstance(body, dict) else None
    _log(
        data,
        access_key,
        request_id=request_id,
        method=method if isinstance(method, str) and method else "POST",
        resource=resource,
        outcome=outcome,
        client_ip=client_ip,
    )


def _request_meta(params: Any) -> dict | None:
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else None


def _modern_headers(request: web.Request) -> bool:
    """Select an MCP era from headers alone, for a body with no usable envelope."""
    header_version = request.headers.get("MCP-Protocol-Version")
    return bool(header_version and header_version not in MCP_LEGACY_PROTOCOL_VERSIONS)


def _is_modern_request(body: dict, request: web.Request) -> bool:
    """Select an MCP era without inferring modern behavior from method names."""
    params = body.get("params")
    meta = _request_meta(params)
    if meta is not None and _MCP_PROTOCOL_META in meta:
        return True
    if body.get("method") == "initialize":
        return request.headers.get("MCP-Protocol-Version") == _MCP_MODERN_VERSION
    return _modern_headers(request)


def _decode_mcp_header(value: str) -> str | None:
    """Decode the modern MCP Base64 sentinel form, or return a plain value."""
    if not (value.startswith("=?base64?") and value.endswith("?=")):
        return value
    encoded = value[len("=?base64?"):-2]
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _validate_modern_request(
    body: dict, request: web.Request, request_id: str
) -> web.Response | None:
    """Validate 2026-07-28 per-request metadata and mirrored HTTP headers."""
    msg_id = _sanitize_jsonrpc_id(body.get("id"))
    params = body.get("params")
    meta = _request_meta(params)
    header_version = request.headers.get("MCP-Protocol-Version")
    body_version = meta.get(_MCP_PROTOCOL_META) if meta is not None else None

    def refuse(code: int, message: str, *, data: dict | None = None) -> web.Response:
        return _protocol_error_response(msg_id, code, message, request_id, data=data, modern=True)

    if body_version is None and isinstance(header_version, str) and header_version not in MCP_PROTOCOL_VERSIONS:
        return refuse(
            -32022, "Unsupported protocol version",
            data={"supported": list(MCP_PROTOCOL_VERSIONS), "requested": header_version},
        )

    # A request missing a required field is malformed; a mismatch needs two values.
    if not isinstance(body_version, str):
        return refuse(-32602, "Invalid params: protocol version metadata is required.")
    if not isinstance(header_version, str):
        return refuse(-32020, "Header mismatch: the MCP-Protocol-Version header is required.")
    if header_version != body_version:
        return refuse(-32020, "Header mismatch: MCP-Protocol-Version does not match the request body.")
    if body_version not in MCP_PROTOCOL_VERSIONS or body_version != _MCP_MODERN_VERSION:
        return refuse(
            -32022, "Unsupported protocol version",
            data={"supported": list(MCP_PROTOCOL_VERSIONS), "requested": body_version},
        )

    if meta is None or not isinstance(meta.get(_MCP_CLIENT_CAPABILITIES_META), dict):
        return refuse(-32602, "Invalid params: client capabilities metadata is required.")
    client_info = meta.get(_MCP_CLIENT_INFO_META)
    if client_info is not None and (
        not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not isinstance(client_info.get("version"), str)
    ):
        return refuse(-32602, "Invalid params: clientInfo metadata is malformed.")

    method = body.get("method")
    method_header = request.headers.get("Mcp-Method")
    if not isinstance(method, str) or method_header != method:
        return refuse(-32020, "Header mismatch: Mcp-Method does not match the request body.")

    name_field = _MCP_NAMED_METHOD_FIELDS.get(method)
    if name_field is not None:
        body_name = params.get(name_field) if isinstance(params, dict) else None
        raw_name = request.headers.get("Mcp-Name")
        header_name = _decode_mcp_header(raw_name) if isinstance(raw_name, str) else None
        if not isinstance(body_name, str) or header_name != body_name:
            return refuse(-32020, "Header mismatch: Mcp-Name does not match the request body.")
    return None


def _modernize_response(response_msg: dict | None, method: str) -> dict | None:
    """Add wire-only 2026-07-28 result fields in place; legacy replies never pass here.

    Also remaps the legacy unknown-resource code (-32002) to -32602 and the
    authority-changed code (-32000) to MCP_AUTHORITY_CHANGED_ERROR.
    """
    if response_msg is None:
        return None
    error = response_msg.get("error")
    if isinstance(error, dict):
        if error.get("code") == -32002:
            error["code"] = -32602
        elif error.get("code") == -32000:
            error["code"] = MCP_AUTHORITY_CHANGED_ERROR
    result = response_msg.get("result")
    if not isinstance(result, dict):
        return response_msg
    result["resultType"] = "complete"
    meta = result.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta.setdefault(
            _MCP_SERVER_INFO_META,
            {"name": "Phoenix MCP", "version": PHOENIX_VERSION},
        )
    if method in _MCP_CACHEABLE_METHODS:
        result.setdefault("ttlMs", MCP_DISCOVER_TTL_MS if method == "server/discover" else 0)
        result.setdefault("cacheScope", "private")
    return response_msg


def _classify_jsonrpc_message(body: dict) -> tuple[str, Any]:
    """Validate one JSON-RPC envelope before dispatch; jsonrpc == "2.0" is already checked.
    Returns one of:
    - ("dispatch", (method, params)): a well-formed request.
    - ("accepted", None): a JSON-RPC response object, accepted by the legacy transport.
    - ("unsupported", (code, message)): valid but undispatchable; a notification stays silent.
    - ("error", (code, message)): malformed; the caller replies with the message's own id.
    A malformed id is never coerced and dispatched, as the reply could not be correlated.
    """
    method = body.get("method")
    # A response object carries result/error and no method. The legacy transport
    # accepts these without a reply; post() rejects them when a modern protocol
    # signal is present, because modern clients may only POST requests or
    # notifications.
    if method is None and ("result" in body or "error" in body):
        return "accepted", None
    if not isinstance(method, str) or not method:
        return "error", (-32600, "Invalid Request.")
    # A present MCP id must be a String or Integer. A malformed one must not be
    # coerced to None and dispatched: `{"id": {}, "method": "tools/call"}` would
    # run its side effect and answer with an id the caller cannot match to its
    # request, the shape that makes a client retry a non-idempotent call.
    # Refusing costs a conforming client nothing.
    if "id" in body and not _valid_jsonrpc_id(body["id"]):
        return "error", (-32600, "Invalid Request.")
    raw_params = body.get("params")
    # JSON-RPC allows params to be an Array (positional) or Object (by-name). Phoenix MCP
    # only dispatches by-name (its handlers index params as a dict). A list is thus
    # STRUCTURALLY VALID but unsupported ("unsupported"), which matters because a
    # valid positional-params NOTIFICATION must still get no response; a scalar is
    # not a structured value at all, so it is a malformed envelope ("error").
    if raw_params is not None and not isinstance(raw_params, dict):
        if isinstance(raw_params, list):
            return "unsupported", (-32602, "Positional parameters are not supported.")
        return "error", (-32602, "Invalid params.")
    return "dispatch", (method, raw_params or {})


async def _handle_streamable_batch(
    items: list,
    access_key: AccessKeyRecord,
    rl_result: RateLimitResult,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str,
    client_ip: str,
    base_url: str,
) -> web.Response:
    """Dispatch a JSON-RPC batch array per MCP 2025-03-26.

    Items fail individually and notifications produce no entry. Returns 202 when all items are
    notifications, otherwise 200 with a results array.
    """
    if not items:
        _audit_mcp_transport(
            data, access_key, request_id, client_ip, body=items, resource="mcp:batch"
        )
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(_jsonrpc_error(None, -32600, "Empty batch.")),
            headers={"X-Phoenix-Request-ID": request_id},
        )

    # Rate limiting for a batch is charged PER ITEM and preflighted against both
    # the minute and burst ceilings by RateLimiter.charge_batch, in post() before
    # anything here runs; charging one unit per batch would let an access key spend
    # MAX_BATCH_ITEMS times its configured ceiling.
    # Items are dispatched sequentially (not via asyncio.gather) so a batch can never
    # run up to MAX_BATCH_ITEMS side-effecting tools concurrently and interleave
    # writes; order is preserved and one item's failure stays isolated.
    if len(items) > MAX_BATCH_ITEMS:
        _audit_mcp_transport(
            data, access_key, request_id, client_ip, body=items, resource="mcp:batch"
        )
        return web.Response(
            status=400,
            content_type="application/json",
            text=json.dumps(_jsonrpc_error(None, -32600, f"Batch too large. Maximum {MAX_BATCH_ITEMS} items.")),
            headers={"X-Phoenix-Request-ID": request_id},
        )

    responses = await _dispatch_streamable_batch(
        items, access_key, hass, data, client_ip, base_url=base_url,
        request_id=request_id,
    )

    if not responses:
        return web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id})

    resp = web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(responses, default=str),
        headers={"X-Phoenix-Request-ID": request_id},
    )
    resp.headers.update(_rate_limit_headers(access_key, rl_result))
    return resp


def _batch_expects_response(items: list) -> bool:
    """Return whether a batch produces any reply entry, without dispatching anything.

    Mirrors the suppression rules in _dispatch_streamable_batch; a change there changes this.
    """
    for item in items:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            return True  # malformed: gets an Invalid Request entry
        kind, _payload = _classify_jsonrpc_message(item)
        if kind == "accepted":
            continue  # a response/notification object never gets a reply
        if kind == "unsupported" and "id" not in item:
            continue  # valid positional-params notification: no entry
        if kind == "error" or "id" in item:
            return True
    return False


async def _dispatch_streamable_batch(
    items: list,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    *,
    base_url: str,
    request_id: str | None = None,
) -> list[dict]:
    """Dispatch each batch item in order, returning only the entries that reply.

    Sequential, not gathered, so side-effecting tools never interleave; failures stay
    isolated.
    """
    bus = _progress_ctx.get()
    batch_policy_version = access_key.settings_version

    write_receipts: dict[int, str] = {}

    async def _dispatch_one(item: Any) -> dict | None:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, resource="mcp:batch",
            )
            msg_id = _sanitize_jsonrpc_id(item.get("id")) if isinstance(item, dict) else None
            return _jsonrpc_error(msg_id, -32600, "Invalid Request.")
        msg_id = _sanitize_jsonrpc_id(item.get("id"))
        kind, payload = _classify_jsonrpc_message(item)
        is_notification = "id" not in item
        if kind == "accepted":
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, outcome="allowed", resource="mcp:response",
            )
            return None  # response/notification object: no reply entry
        if kind in ("error", "unsupported"):
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, resource="mcp:batch",
            )
            # A structurally valid but undispatchable item (positional params) with
            # no id is a valid notification: no entry. A malformed item is an
            # Invalid Request (id null) and still gets an error entry.
            if kind == "unsupported" and is_notification:
                return None
            return _jsonrpc_error(msg_id, *payload)
        method, params = payload
        if is_notification and method in _MCP_METHODS and method not in {"notifications/initialized", "initialized"}:
            _audit_mcp_transport(data, access_key, request_id or generate_request_id(), client_ip,
                                 body=item, resource="mcp:notification")
            return None

        if method not in _MCP_LEGACY_METHODS:
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                body=item, outcome="not_implemented", resource="mcp:batch",
            )
            return None if is_notification else _jsonrpc_error(msg_id, -32601, "Method not found.")
        if bus is not None:
            # Progress is per-request in MCP, and each batch item carries its own
            # token; items run one at a time, so one field is enough.
            bus.progress_token = _progress_token(method, params)
        try:
            response_msg, _, _, outcome = await _dispatch_mcp(
                method, msg_id, params, access_key, hass, data, client_ip,
                base_url=base_url,
                request_id=request_id,
            )
        finally:
            if bus is not None:
                bus.progress_token = None
                bus.status = None
        if (not is_notification and response_msg is not None and method == "tools/call"
                and isinstance(params.get("name"), str) and not tool_is_read_only(params["name"])):
            write_receipts[id(response_msg)] = outcome
        return None if is_notification else response_msg

    responses: list[dict] = []
    for item in items:
        try:
            r = await _dispatch_one(item)
        except Exception:  # isolate one item's failure from the batch
            msg_id = _sanitize_jsonrpc_id(item.get("id")) if isinstance(item, dict) else None
            responses.append(_jsonrpc_error(msg_id, -32603, "Internal error."))
            continue
        if r is not None:
            responses.append(r)
    # Earlier replies remain buffered while later items await. Revalidate their
    # common authority before disclosing any buffered result in the final batch.
    authority = request_authority.get()
    if authority is not None:
        try:
            current = authority.current_access_key()
            if not current.accepts_policy_version(batch_policy_version):
                raise AuthorityChangedError("Permissions changed.")
        except AuthorityChangedError:
            _audit_mcp_transport(
                data, access_key, request_id or generate_request_id(), client_ip,
                outcome="invalid_request", resource="mcp:batch",
            )
            withheld = []
            for row in responses:
                if write_receipts.get(id(row)) == "allowed":
                    withheld.append(_jsonrpc_result(row.get("id"), _tool_success(
                        "Action dispatch completed. Result withheld because authority changed. Do not repeat the action; verify its outcome in Home Assistant."
                    )))
                elif id(row) in write_receipts:
                    withheld.append(_jsonrpc_error(row.get("id"), -32000,
                        "Authority changed. Check earlier actions before resubmitting; do not repeat any dispatched action."))
                else:
                    withheld.append(_jsonrpc_error(row.get("id"), -32000,
                        "Request authority changed. Resubmit reads with current permissions."))
            return withheld
    return responses


async def _dispatch_mcp_result(
    method: str,
    msg_id: Any,
    params: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    *,
    base_url: str,
    request_id: str | None = None,
) -> dict | None:
    """_dispatch_mcp reduced to just the response message, for the SSE writer."""
    response_msg, _method, _resource, _outcome = await _dispatch_mcp(
        method, msg_id, params, access_key, hass, data, client_ip,
        base_url=base_url,
        request_id=request_id,
    )
    return response_msg


async def _dispatch_modern_mcp_result(
    method: str,
    msg_id: Any,
    params: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    client_ip: str,
    *,
    base_url: str,
    request_id: str | None = None,
) -> dict | None:
    """Dispatch and apply the 2026-07-28 wire shape for an SSE response."""
    response_msg = await _dispatch_mcp_result(
        method, msg_id, params, access_key, hass, data, client_ip,
        base_url=base_url, request_id=request_id,
    )
    return _modernize_response(response_msg, method)


def _rate_limit_headers(access_key: AccessKeyRecord, rl_result: Any) -> dict[str, str]:
    """The X-RateLimit-* trio, or nothing when rate limiting is off for this access key."""
    if access_key.rate_limit_requests <= 0:
        return {}
    return {
        "X-RateLimit-Limit": str(access_key.rate_limit_requests),
        "X-RateLimit-Remaining": str(rl_result.remaining),
        "X-RateLimit-Reset": str(rl_result.reset),
    }


def _progress_token(method: str, params: dict) -> str | int | None:
    """Return the client's progressToken for this request, if it asked for progress.

    A token is a string or integer in ``params._meta``. Anything else means no progress, never
    an error.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    tok = meta.get("progressToken")
    if isinstance(tok, str) and tok:
        return tok
    if isinstance(tok, int) and not isinstance(tok, bool):
        return tok
    return None


def _sse_frame(payload: dict) -> bytes:
    """One SSE `message` event carrying a JSON-RPC object."""
    return f"event: message\ndata: {json.dumps(payload, default=str)}\n\n".encode()


def _progress_notification(bus: _ProgressBus, elapsed: float) -> dict:
    """A JSON-RPC notifications/progress message for the current wait.

    `progress` is elapsed seconds, which satisfies the spec's requirement that the
    value increase with every notification for a given token.
    """
    params: dict[str, Any] = {
        "progressToken": bus.progress_token,
        "progress": round(elapsed, 1),
    }
    if bus.total is not None:
        params["total"] = bus.total
    if bus.status:
        params["message"] = bus.status
    return {"jsonrpc": "2.0", "method": "notifications/progress", "params": params}


async def _mcp_sse_response(
    request: web.Request,
    hass: HomeAssistant,
    request_id: str,
    extra_headers: dict[str, str],
    bus: _ProgressBus,
    dispatch: Any,
    *,
    cancel_on_disconnect: bool,
    msg_id: Any = None,
) -> web.StreamResponse:
    """Run ``dispatch`` and deliver its JSON-RPC result as a single SSE message.

    The payload matches the plain JSON path, with a frame every MCP_SSE_KEEPALIVE_SECONDS so
    an intermediary does not drop a held gate. Only this function writes to the stream.
    """
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer the stream
            "X-Phoenix-Request-ID": request_id,
            **extra_headers,
        },
    )
    await resp.prepare(request)

    task = hass.loop.create_task(dispatch)
    task.add_done_callback(_log_dispatch_task_error)
    started = time.monotonic()
    writable = True
    try:
        while True:
            done, _pending = await asyncio.wait(
                {task}, timeout=MCP_SSE_KEEPALIVE_SECONDS
            )
            if done:
                break
            if not writable:
                continue
            elapsed = time.monotonic() - started
            frame = (
                _sse_frame(_progress_notification(bus, elapsed))
                if bus.progress_token is not None
                else b": keepalive\n\n"
            )
            try:
                await resp.write(frame)
            except Exception:  # any write failure closes this response
                writable = False
                if cancel_on_disconnect:
                    # Stream closure is cancellation in the 2026-07-28 transport.
                    # Cancellation is cooperative: an awaited operation that already
                    # committed is not rolled back, but no later awaitable work should
                    # continue after the response channel disappears. The legacy
                    # 2025-03-26 transport explicitly requires the opposite behavior.
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    break

        try:
            response_msg = None if task.cancelled() else task.result()
        except Exception:
            # Defense in depth: _dispatch_mcp already nets tools/call exceptions, and
            # the batch path isolates per item. Never surface the text to the client.
            _LOGGER.exception("Unhandled exception on the MCP SSE path rid=%s", request_id)
            response_msg = _jsonrpc_error(msg_id, -32603, "Internal error.")

        if writable and response_msg is not None:
            try:
                await resp.write(_sse_frame(response_msg))
            except Exception:  # client disconnected mid-flight
                pass
        try:
            await resp.write_eof()
        except Exception:
            pass
        return resp
    except asyncio.CancelledError:
        # Home Assistant enables aiohttp handler cancellation. A connection loss
        # can therefore cancel this request handler while it is waiting for the
        # child dispatch task, before any keepalive write gets a chance to fail.
        # Propagate that transport cancellation into modern dispatch, but leave
        # legacy dispatch detached as required by the 2025-03-26 revision.
        if cancel_on_disconnect and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        raise


def _log_dispatch_task_error(task: asyncio.Task) -> None:
    """Never let a detached dispatch task's exception vanish silently."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log_internal_failure(_LOGGER, "mcp_dispatch_task", exc)


def _parse_mcp_body(body_bytes: bytes) -> Any:
    """Parse one HTTP body under MCP's JSON rules; raise ValueError or RecursionError."""
    parsed = json.loads(body_bytes.decode("utf-8"), parse_constant=_reject_non_finite, parse_float=_parse_finite_float)
    json.dumps(parsed, ensure_ascii=False).encode("utf-8")
    return parsed


def _origin_rejected(hass: HomeAssistant, request: web.Request, request_id: str) -> web.Response | None:
    """Refuse a browser cross-origin (DNS-rebinding) request per the MCP transport spec.
    An absent Origin is allowed, as only browsers send one. A present Origin must match HA's
    configured URLs through is_hass_url, never the forgeable Host header. Defense in depth:
    such a request would still fail bearer authentication.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.network import is_hass_url
    origin = request.headers.get("Origin")
    if not origin:
        return None
    try:
        allowed = is_hass_url(hass, origin)
    except ValueError:
        # A malformed Origin (e.g. "http://[::1") makes HA's URL parser raise;
        # treat any unparseable Origin as not allowed rather than letting it
        # escape as an unauthenticated 500.
        allowed = False
    if allowed:
        return None
    return _error("forbidden", "Origin not allowed.", 403, request_id)


def _prune_legacy_sessions(data: PhoenixData) -> None:
    """Expire idle legacy sessions and bound worst-case in-memory growth.

    A session that never completed initialization expires sooner: a client that
    abandons its handshake would otherwise hold a quota slot for the full hour.
    """
    now = time.monotonic()
    expired = [
        session_id
        for session_id, state in data.mcp_sessions.items()
        if now - float(state.get("last_seen", 0.0)) > (
            _MCP_LEGACY_SESSION_TTL_SECONDS if state.get("initialized")
            else _MCP_LEGACY_UNINITIALIZED_TTL_SECONDS)
    ]
    for session_id in expired:
        data.mcp_sessions.pop(session_id, None)


def _validate_legacy_initialize(params: dict) -> tuple[int, str] | None:
    """Validate the required 2025-03-26 initialize parameters."""
    if not isinstance(params.get("protocolVersion"), str):
        return -32602, "Invalid params: protocolVersion is required."
    if not isinstance(params.get("capabilities"), dict):
        return -32602, "Invalid params: capabilities are required."
    client_info = params.get("clientInfo")
    if (
        not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not isinstance(client_info.get("version"), str)
    ):
        return -32602, "Invalid params: clientInfo is required."
    return None


def _open_legacy_session(data: PhoenixData, access_key: AccessKeyRecord) -> str | None:
    """Allocate within both quotas, closing this key's own stale session first.

    A full quota closes the key's least recently used idle session. Another key's session is
    never closed, and None means every candidate is busy.
    """
    _prune_legacy_sessions(data)
    owned = [(state, session_id) for session_id, state in data.mcp_sessions.items()
             if state.get("access_key_id") == access_key.id]
    if len(owned) >= _MCP_LEGACY_SESSION_ACCESS_KEY_LIMIT or len(data.mcp_sessions) >= _MCP_LEGACY_SESSION_LIMIT:
        idle = [(float(state.get("last_seen", 0.0)), session_id) for state, session_id in owned
                if not state.get("in_use")]
        if not idle:
            return None
        data.mcp_sessions.pop(min(idle)[1], None)
    session_id = secrets.token_urlsafe(32)
    data.mcp_sessions[session_id] = {
        "access_key_id": access_key.id,
        "initialized": False,
        "last_seen": time.monotonic(),
        "protocol_version": MCP_LEGACY_PROTOCOL_VERSION_PREFERRED,
    }
    return session_id


def _legacy_session_retry_after(data: PhoenixData, access_key: AccessKeyRecord) -> int:
    """Return the seconds until a refused initialize can find room.

    Short when the key's own sessions are busy; else until the stalest other session idles
    out.
    """
    if any(state.get("access_key_id") == access_key.id for state in data.mcp_sessions.values()):
        return MCP_LEGACY_SESSION_BUSY_RETRY_SECONDS
    oldest = min((float(state.get("last_seen", 0.0)) for state in data.mcp_sessions.values()), default=None)
    if oldest is None:
        return MCP_LEGACY_SESSION_BUSY_RETRY_SECONDS
    remaining = _MCP_LEGACY_SESSION_TTL_SECONDS - (time.monotonic() - oldest)
    return max(MCP_LEGACY_SESSION_BUSY_RETRY_SECONDS, math.ceil(remaining))


def _legacy_session(
    data: PhoenixData, request: web.Request, access_key: AccessKeyRecord
) -> dict | None:
    _prune_legacy_sessions(data)
    session_id = request.headers.get("Mcp-Session-Id")
    state = data.mcp_sessions.get(session_id) if isinstance(session_id, str) else None
    if state is None or state.get("access_key_id") != access_key.id:
        return None
    state["last_seen"] = time.monotonic()
    if _legacy_session_in_use.get() is None:
        state["in_use"] = state.get("in_use", 0) + 1
        _legacy_session_in_use.set(state)
    return state


def _legacy_session_error_response(
    request: web.Request, msg_id: Any, request_id: str
) -> web.Response:
    """Distinguish a missing required session from a supplied unknown one."""
    supplied = bool(request.headers.get("Mcp-Session-Id"))
    return _protocol_error_response(
        msg_id,
        -32000,
        "Legacy MCP session required.",
        request_id,
        status=404 if supplied else 400,
    )


class PhoenixMcpView(PhoenixView):
    """POST/DELETE /api/phoenix-mcp - MCP Streamable HTTP transport.

    The implemented revisions are const.MCP_PROTOCOL_VERSIONS, a compliance claim. The
    trailing-slash form is registered too, as aiohttp would otherwise 404 it.
    """

    url = "/api/phoenix-mcp"
    extra_urls = ["/api/phoenix-mcp/"]
    name = "api:phoenix-mcp:mcp"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """Handle one request with task-local credential identity."""
        context = request_authority.set(None)
        in_use = _legacy_session_in_use.set(None)
        try:
            with transport_session(request.headers.get("Mcp-Session-Id")):
                return await self._post(request)
        finally:
            if (session := _legacy_session_in_use.get()) is not None:
                session["in_use"] -= 1
                session["last_seen"] = time.monotonic()
            _legacy_session_in_use.reset(in_use)
            request_authority.reset(context)

    async def delete(self, request: web.Request) -> web.Response:
        """Terminate an authenticated legacy Streamable HTTP session."""
        context = request_authority.set(None)
        try:
            return await self._delete(request)
        finally:
            request_authority.reset(context)

    async def _delete(self, request: web.Request) -> web.Response:
        """Release a session created by ``initialize`` for this access key."""
        hass = self.hass
        request_id = generate_request_id()
        data = hass.data.get(DOMAIN)
        if data is None or not data.ready or data.shutting_down:
            return _error(
                "service_unavailable", "Service unavailable.", 503, request_id
            )
        client_ip = _get_client_ip(request)

        rejected = _origin_rejected(hass, request, request_id)
        if rejected is not None:
            return rejected

        result = await _async_get_authenticated_access_key(
            hass, request, data, request_id, "/api/phoenix-mcp"
        )
        if isinstance(result, web.Response):
            return result
        access_key, _rl_result = result
        authority = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash)
        request_authority.set(authority)
        try:
            access_key = authority.current_access_key()
        except AuthorityChangedError:
            unavailable = (
                not data.ready
                or data.shutting_down
                or data.store.get_settings().kill_switch
                or hass.data.get(DOMAIN) is not data
            )
            return _error(
                "service_unavailable" if unavailable else "unauthorized",
                "Service unavailable." if unavailable else "Unauthorized.",
                503 if unavailable else 401,
                request_id,
            )

        session_id = request.headers.get("Mcp-Session-Id")
        if not session_id:
            _log(
                data,
                access_key,
                request_id=request_id,
                method="DELETE",
                resource="mcp:session",
                outcome="invalid_request",
                client_ip=client_ip,
            )
            return _error(
                "invalid_request",
                "Mcp-Session-Id header is required.",
                400,
                request_id,
            )

        _prune_legacy_sessions(data)
        session = data.mcp_sessions.get(session_id)
        if session is None or session.get("access_key_id") != access_key.id:
            _log(
                data,
                access_key,
                request_id=request_id,
                method="DELETE",
                resource="mcp:session",
                outcome="not_found",
                client_ip=client_ip,
            )
            return _error("not_found", "MCP session not found.", 404, request_id)

        data.mcp_sessions.pop(session_id, None)
        _log(
            data,
            access_key,
            request_id=request_id,
            method="DELETE",
            resource="mcp:session",
            outcome="allowed",
            client_ip=client_ip,
        )
        return web.Response(status=204, headers={"X-Phoenix-Request-ID": request_id})

    async def _post(self, request: web.Request) -> web.Response:
        """Authenticate, read, and dispatch one Streamable HTTP request."""
        hass = self.hass
        request_id = generate_request_id()
        data = hass.data.get(DOMAIN)
        if data is None or not data.ready or data.shutting_down:
            return _error(
                "service_unavailable", "Service unavailable.", 503, request_id
            )
        client_ip = _get_client_ip(request)

        rejected = _origin_rejected(hass, request, request_id)
        if rejected is not None:
            return rejected

        result = await _async_get_authenticated_access_key(
            hass, request, data, request_id, "/api/phoenix-mcp"
        )
        if isinstance(result, web.Response):
            return result
        access_key, rl_result = result
        authority = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash)
        request_authority.set(authority)

        def audited_transport_response(
            response: web.Response,
            *,
            body: Any = None,
            outcome: Outcome = "invalid_request",
            resource: str = "/api/phoenix-mcp",
        ) -> web.Response:
            _audit_mcp_transport(
                data, access_key, request_id, client_ip,
                body=body, outcome=outcome, resource=resource,
            )
            return response

        def dead_session_response(body: Any = None) -> web.Response | None:
            """Answer 404 for a legacy message naming a session that is gone, whatever its shape."""
            if not request.headers.get("Mcp-Session-Id"):
                return None
            modern_body = _is_modern_request(body, request) if isinstance(body, dict) else _modern_headers(request)
            if modern_body or _legacy_session(data, request, access_key) is not None:
                return None
            msg_id = _sanitize_jsonrpc_id(body.get("id")) if isinstance(body, dict) else None
            return audited_transport_response(
                _legacy_session_error_response(request, msg_id, request_id),
                body=body, outcome="not_found",
            )

        def parse_error_response() -> web.Response:
            dead_session = dead_session_response()
            if dead_session is not None:
                return dead_session
            return audited_transport_response(web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32700, "Parse error.", modern=_modern_headers(request))),
                headers={"X-Phoenix-Request-ID": request_id},
            ))

        if request.content_length is not None and request.content_length > _MAX_BODY:
            return audited_transport_response(
                _error("request_too_large", "Request body too large.", 413, request_id)
            )
        # aiohttp's StreamReader.read(n) is a SHORT read: it returns as soon as
        # the buffer has ANY data, up to n bytes, not once n bytes have
        # arrived. A single read(_MAX_BODY + 1) call could therefore silently
        # truncate a larger tool-call body arriving across multiple
        # TCP/chunked segments. Loop until EOF instead, bailing as soon as the
        # cap is exceeded.
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = await request.content.read(_MAX_BODY - total + 1)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_BODY:
                    return audited_transport_response(
                        _error("request_too_large", "Request body too large.", 413, request_id)
                    )
                at_eof = getattr(request.content, "at_eof", None)
                if callable(at_eof) and at_eof():
                    break
        except Exception:
            return audited_transport_response(
                _error("invalid_request", "Failed to read request body.", 400, request_id)
            )
        try:
            access_key = authority.current_access_key()
        except AuthorityChangedError:
            unavailable = not data.ready or data.shutting_down or data.store.get_settings().kill_switch or hass.data.get(DOMAIN) is not data
            return _error(
                "service_unavailable" if unavailable else "unauthorized",
                "Service unavailable." if unavailable else "Unauthorized.",
                503 if unavailable else 401, request_id,
            )
        body_bytes = b"".join(chunks)
        if not body_bytes:
            return parse_error_response()
        try:
            parsed = _parse_mcp_body(body_bytes)
        except (ValueError, RecursionError):  # Invalid JSON or text, numeric overflow, or excessive nesting.
            _LOGGER.warning(
                "MCP POST: invalid JSON body (%d bytes read, Content-Length %s) rid=%s",
                len(body_bytes), request.content_length, request_id,
            )
            return parse_error_response()

        # Response framing, not a session: an SSE-capable client gets the SAME
        # single JSON-RPC response, delivered as one `message` event, with
        # keepalive frames while the work runs. Per the Streamable HTTP spec
        # conforming clients for the supported revisions advertise both types,
        # so this is their normal path; a custom JSON-only caller keeps
        # byte-identical behavior.
        wants_sse = "text/event-stream" in request.headers.get("Accept", "")
        try:
            base_url = str(request.url.origin())
        except ValueError:  # A Host header that does not form a URL.
            return audited_transport_response(
                _error("invalid_request", "Invalid Host header.", 400, request_id)
            )
        rl_headers = _rate_limit_headers(access_key, rl_result)

        if isinstance(parsed, list):
            header_version = request.headers.get("MCP-Protocol-Version")
            carries_modern_meta = any(
                isinstance(item, dict)
                and _MCP_PROTOCOL_META in (_request_meta(item.get("params")) or {})
                for item in parsed
            )
            if (
                header_version == _MCP_MODERN_VERSION
                or carries_modern_meta
            ):
                return audited_transport_response(_protocol_error_response(
                    None,
                    -32600,
                    "Invalid Request: JSON-RPC batches are not supported by this protocol version.",
                    request_id,
                    modern=True,
                ), body=parsed, resource="mcp:batch")
            if header_version and header_version not in MCP_PROTOCOL_VERSIONS:
                return audited_transport_response(_protocol_error_response(
                    None, -32022, "Unsupported protocol version", request_id,
                    data={"supported": list(MCP_PROTOCOL_VERSIONS), "requested": header_version},
                    modern=True,
                ), body=parsed, resource="mcp:batch")
            if any(
                isinstance(item, dict) and item.get("method") == "initialize"
                for item in parsed
            ):
                return audited_transport_response(_protocol_error_response(
                    None,
                    -32600,
                    "Invalid Request: initialize must not be sent in a batch.",
                    request_id,
                ), body=parsed, resource="mcp:batch")
            session = _legacy_session(data, request, access_key)
            if session is None:
                session_outcome: Outcome = (
                    "not_found"
                    if request.headers.get("Mcp-Session-Id")
                    else "invalid_request"
                )
                return audited_transport_response(
                    _legacy_session_error_response(request, None, request_id),
                    body=parsed,
                    outcome=session_outcome,
                    resource="mcp:batch",
                )
            if not session.get("initialized") and parsed:
                first = parsed[0]
                if (isinstance(first, dict) and first.get("jsonrpc") == "2.0" and "id" not in first
                        and first.get("method") in {"notifications/initialized", "initialized"}
                        and _classify_jsonrpc_message(first)[0] == "dispatch"):
                    session["initialized"] = True
            if not session.get("initialized"):
                return audited_transport_response(_protocol_error_response(
                    None, -32000, "Legacy MCP initialization is not complete.", request_id
                ), body=parsed, resource="mcp:batch")
            if len(parsed) > MAX_BATCH_ITEMS or (access_key.rate_limit_requests > 0 and (
                    (access_key.rate_limit_burst > 0 and len(parsed) > access_key.rate_limit_burst)
                    or len(parsed) > access_key.rate_limit_requests)):
                return audited_transport_response(_protocol_error_response(None, -32600,
                    "Batch exceeds the configured request capacity; split it into smaller requests.", request_id),
                    body=parsed, resource="mcp:batch")
            # A batch is ONE HTTP request carrying up to MAX_BATCH_ITEMS calls,
            # and the auth check above charged it as one, so batching would
            # multiply an access key's effective rate by MAX_BATCH_ITEMS. The items are
            # the work, so the other N-1 are charged here, once the count is
            # known, and the whole batch is REFUSED if the budget will not cover
            # it. Preflighting is what makes the ceiling real: charging without
            # refusing would let an access key run every item and only then be
            # throttled. Nothing has been dispatched yet, so there is no
            # half-applied batch to worry about.
            batch_rl = data.rate_limiter.charge_batch(
                access_key.id, access_key.rate_limit_requests, access_key.rate_limit_burst,
                min(len(parsed), MAX_BATCH_ITEMS) - 1,
            )
            if not batch_rl.allowed:
                _fire_rate_limit_events(hass, data, access_key)
                _log(
                    data, access_key, request_id=request_id, method="POST",
                    resource="mcp:batch", outcome="rate_limited", client_ip=client_ip,
                )
                resp = _error("rate_limited", "Rate limit exceeded.", 429, request_id)
                resp.headers["Retry-After"] = str(batch_rl.retry_after)
                return resp
            # From here on the batch's own result is what the client is told, on
            # BOTH paths: the single-request result above was computed before the
            # other N-1 items were charged, so it would report a budget the access key
            # no longer has. This covers the SSE framing; the JSON path takes
            # batch_rl directly.
            if batch_rl.rate_limiting_enabled:
                rl_headers = _rate_limit_headers(access_key, batch_rl)
            if wants_sse and _batch_expects_response(parsed) and 0 < len(parsed) <= MAX_BATCH_ITEMS:
                bus = _ProgressBus()
                _progress_ctx.set(bus)
                return cast(web.Response, await _mcp_sse_response(
                    request, hass, request_id, rl_headers, bus,
                    _dispatch_streamable_batch(
                        parsed, access_key, hass, data, client_ip, base_url=base_url,
                        request_id=request_id,
                    ),
                    cancel_on_disconnect=False,
                ))
            batch_dispatch = _handle_streamable_batch(parsed, access_key, batch_rl, hass, data, request_id, client_ip, base_url=base_url)
            batch_task = asyncio.create_task(batch_dispatch)
            batch_task.add_done_callback(_log_dispatch_task_error)
            return await asyncio.shield(batch_task)

        dead_session = dead_session_response(parsed)
        if dead_session is not None:
            return dead_session

        if not isinstance(parsed, dict):
            return audited_transport_response(web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(None, -32600, "Invalid Request.", modern=_modern_headers(request))),
                headers={"X-Phoenix-Request-ID": request_id},
            ), body=parsed)

        body = parsed
        modern = _is_modern_request(body, request)
        is_notification = "id" not in body
        # MCP requires an HTTP error status when a message with no id cannot be
        # accepted. The body stays, which it permits and JSON-RPC asks for.
        refused_status = 400 if is_notification else 200
        if body.get("jsonrpc") != "2.0":
            return audited_transport_response(web.Response(
                status=refused_status,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(_sanitize_jsonrpc_id(body.get("id")), -32600, "Invalid Request.", modern=modern)),
                headers={"X-Phoenix-Request-ID": request_id},
            ), body=body)

        msg_id = _sanitize_jsonrpc_id(body.get("id"))
        # A JSON-RPC notification is a VALID request that omits `id`; it must
        # never receive a response. But `id`-absence alone is not enough: a
        # malformed envelope (bad method, non-object params) is an Invalid
        # Request and MUST get an error, not be silently swallowed. So classify
        # FIRST and only suppress the dispatch path. (`id: null` is a request,
        # not a notification, hence membership, not value.)
        kind, payload = _classify_jsonrpc_message(body)
        if kind == "accepted":
            if modern:
                return audited_transport_response(_protocol_error_response(
                    msg_id,
                    -32600,
                    "Invalid Request: clients must not send JSON-RPC responses for this protocol version.",
                    request_id,
                    modern=True,
                ), body=body)
            return audited_transport_response(
                web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id}),
                body=body,
                outcome="allowed",
                resource="mcp:response",
            )
        if kind in ("error", "unsupported"):
            # A structurally valid but undispatchable request (positional params)
            # is still a valid notification when it has no id, so it gets no reply;
            # a malformed envelope always gets an error.
            if kind == "unsupported" and is_notification:
                return audited_transport_response(
                    web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id}),
                    body=body,
                )
            code, message = payload
            return audited_transport_response(web.Response(
                status=refused_status,
                content_type="application/json",
                text=json.dumps(_jsonrpc_error(msg_id, code, message, modern=modern)),
                headers={"X-Phoenix-Request-ID": request_id},
            ), body=body)
        method, params = payload

        if is_notification and method in _MCP_METHODS and method not in {"notifications/initialized", "initialized"}:
            return audited_transport_response(web.Response(status=400,
                headers={"X-Phoenix-Request-ID": request_id}), body=body)
        legacy_session_id: str | None = None
        if modern:
            validation_error = _validate_modern_request(body, request, request_id)
            if validation_error is not None:
                return audited_transport_response(validation_error, body=body)
            known_methods = _MCP_MODERN_METHODS
        else:
            known_methods = _MCP_LEGACY_METHODS
            if method == "initialize":
                if request.headers.get("Mcp-Session-Id"):
                    return audited_transport_response(_protocol_error_response(
                        msg_id,
                        -32600,
                        "Invalid Request: initialize must start a new session.",
                        request_id,
                    ), body=body)
                invalid_initialize = _validate_legacy_initialize(params)
                if invalid_initialize is not None:
                    return audited_transport_response(_protocol_error_response(
                        msg_id, *invalid_initialize, request_id
                    ), body=body)
                legacy_session_id = _open_legacy_session(data, access_key)
                if legacy_session_id is None:
                    response = _protocol_error_response(
                        msg_id, -32000, "Legacy MCP session capacity reached. Close unused sessions or retry after they expire.", request_id,
                        status=429,
                    )
                    response.headers["Retry-After"] = str(_legacy_session_retry_after(data, access_key))
                    return audited_transport_response(response, body=body)
            else:
                session = _legacy_session(data, request, access_key)
                if session is None:
                    message_session_outcome: Outcome = (
                        "not_found"
                        if request.headers.get("Mcp-Session-Id")
                        else "invalid_request"
                    )
                    return audited_transport_response(
                        _legacy_session_error_response(request, msg_id, request_id),
                        body=body,
                        outcome=message_session_outcome,
                    )
                if method in ("notifications/initialized", "initialized"):
                    session["initialized"] = True
                elif method != "ping" and not session.get("initialized"):
                    return audited_transport_response(_protocol_error_response(
                        msg_id,
                        -32000,
                        "Legacy MCP initialization is not complete.",
                        request_id,
                    ), body=body)

        response_msg: dict | None
        if method not in known_methods:
            # Still routed through the dispatcher rather than answered here, so
            # the error body and the audit row keep one definition; only the
            # HTTP status is decided at this layer. A notification for a method
            # that is not implemented is accepted and dropped (202), not refused:
            # the envelope is valid and the sender is not owed a reply. That
            # includes notifications/cancelled, which a receiver may ignore: legacy
            # dispatch runs to completion, because stopping a write partway cannot
            # undo what ran, and one held for approval stays queued for the operator
            # whether or not anything is waiting on it.
            if method in _MCP_METHODS:
                _log(
                    data,
                    access_key,
                    request_id=request_id,
                    method=method,
                    resource="/api/phoenix-mcp",
                    outcome="not_implemented",
                    client_ip=client_ip,
                )
                response_msg = _jsonrpc_error(msg_id, -32601, "Method not found.")
            else:
                response_msg, _m, _r, _o = await _dispatch_mcp(
                    method, msg_id, params, access_key, hass, data, client_ip,
                    base_url=base_url,
                    request_id=request_id,
                )
            if modern:
                response_msg = _modernize_response(response_msg, method)
            if is_notification or response_msg is None:
                return web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id})
            return web.Response(
                status=404 if modern else 200,
                content_type="application/json",
                text=json.dumps(response_msg),
                headers={"X-Phoenix-Request-ID": request_id},
            )

        if legacy_session_id is not None:
            rl_headers = {**rl_headers, "Mcp-Session-Id": legacy_session_id}

        if wants_sse and not is_notification:
            bus = _ProgressBus(progress_token=_progress_token(method, params))
            _progress_ctx.set(bus)
            dispatch = (
                _dispatch_modern_mcp_result(
                    method, msg_id, params, access_key, hass, data, client_ip,
                    base_url=base_url,
                    request_id=request_id,
                )
                if modern
                else _dispatch_mcp_result(
                    method, msg_id, params, access_key, hass, data, client_ip,
                    base_url=base_url,
                    request_id=request_id,
                )
            )
            return cast(web.Response, await _mcp_sse_response(
                request, hass, request_id, rl_headers, bus,
                dispatch,
                cancel_on_disconnect=modern,
                msg_id=msg_id,
            ))

        inline_dispatch = _dispatch_mcp(method, msg_id, params, access_key, hass, data, client_ip,
                                 base_url=base_url, request_id=request_id)
        if not modern:
            task = asyncio.create_task(inline_dispatch)
            task.add_done_callback(_log_dispatch_task_error)
            response_msg, _log_method, _log_resource, _outcome = await asyncio.shield(task)
        else:
            response_msg, _log_method, _log_resource, _outcome = await inline_dispatch
        if modern:
            response_msg = _modernize_response(response_msg, method)

        if is_notification or response_msg is None:
            return web.Response(status=202, headers={"X-Phoenix-Request-ID": request_id})

        resp = web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(response_msg, default=str),
            headers={"X-Phoenix-Request-ID": request_id},
        )
        resp.headers.update(rl_headers)
        return resp


class PhoenixMcpContextView(PhoenixView):
    """GET /api/phoenix-mcp/context - context document listing accessible entities and capability flags.

    Returns plain text by default; pass ?format=json for a structured JSON response.
    """

    url = "/api/phoenix-mcp/context"
    name = "api:phoenix-mcp:context"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = self.hass
        request_id = generate_request_id()
        data = hass.data.get(DOMAIN)
        if data is None or not data.ready or data.shutting_down:
            return _error(
                "service_unavailable", "Service unavailable.", 503, request_id
            )
        client_ip = _get_client_ip(request)

        result = await _async_get_authenticated_access_key(
            hass, request, data, request_id, "/api/phoenix-mcp/context"
        )
        if isinstance(result, web.Response):
            return result
        access_key, _rl = result

        _log(data, access_key, request_id=request_id, method="GET", resource="/api/phoenix-mcp/context",
             outcome="allowed", client_ip=client_ip)

        fmt = request.query.get("format", "")
        if fmt == "json":
            body = _build_context_json(access_key, hass)
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps(body, default=str),
                headers={"X-Phoenix-Request-ID": request_id},
            )

        text = _build_context_plain(access_key, hass)
        return web.Response(
            status=200,
            content_type="text/plain",
            text=text,
            headers={"X-Phoenix-Request-ID": request_id},
        )


ALL_MCP_VIEWS: list[type[PhoenixView]] = [
    PhoenixMcpView,
    PhoenixMcpContextView,
]
