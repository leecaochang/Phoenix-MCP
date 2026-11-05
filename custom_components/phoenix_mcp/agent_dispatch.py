"""Run one agent tool call inside the unknown-outcome recovery barrier."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .agent_recovery import NotDispatched, RecoveryRequired, _journal_tool_name, tool_is_read_only
from .tool_common import RefusedBeforeWrite, _tool_error


async def dispatch_agent_tool(
    dispatch: Any, *, hass: HomeAssistant, data: Any, access_key: Any,
    name: str, arguments: dict, tool_id: Any, request_id: str,
    client_ip: str, base_url: str,
) -> tuple:
    """One dispatch, with no retry after the durable boundary.

    A barrier refusal is returned as an ordinary tool refusal, because nothing
    was dispatched; only a failure after the boundary is an unknown outcome.
    """

    recovery = data.agent_recovery
    # Unknown names are refused before a handler can run. Do not turn a model
    # typo into a durable claim that an action might have reached HA.
    write = (name != "home_request" and _journal_tool_name(name) != "unknown_tool"
             and not tool_is_read_only(name))
    try:
        if write:
            await recovery.reconcile(data.store, access_key.id)
            try:
                await recovery.begin(access_key.id, name, request_id)
            except NotDispatched as refusal:
                return {"jsonrpc": "2.0", "id": tool_id, "result": _tool_error(str(refusal))}, "tools/call", name, "denied"
        response = await dispatch(
            "tools/call", tool_id, {"name": name, "arguments": arguments},
            access_key, hass, data, client_ip, base_url, request_id=request_id,
        )
        if write:
            msg, _method, _resource, outcome = response
            result = msg.get("result") if isinstance(msg, dict) else None
            # A structured success is a dispatch receipt, not proof of physical
            # completion (queued jobs keep their own lifecycle). An executor
            # error may follow a partial action and remains unknown.
            if isinstance(result, dict) and result.get("isError") is not True:
                await recovery.received(request_id)
            elif isinstance(result, dict) and outcome in ("denied", "not_found"):
                await recovery.received(request_id)
            elif isinstance(result, RefusedBeforeWrite):
                # Server-owned proof that nothing was written: the model gets the
                # error back and there is no outcome to reconcile.
                await recovery.finish(request_id)
            elif (isinstance(msg, dict) and isinstance(msg.get("error"), dict)
                  and msg["error"].get("code") == -32000):
                msg["error"]["message"] = "Authority changed during dispatch. The action outcome is unknown; reconcile Home Assistant before retrying."
            else:
                raise RecoveryRequired("The action outcome is unknown. Check the Needs Attention window of the Phoenix MCP panel before retrying.")
        return response
    except Exception:
        if write:
            raise RecoveryRequired("The action outcome is unknown. Check the Needs Attention window of the Phoenix MCP panel before retrying.") from None
        raise
    finally:
        if write:
            recovery.interrupted(request_id)
