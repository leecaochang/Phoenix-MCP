"""Durable dispatch barriers for Phoenix-owned provider loops.

Only dispatch metadata is retained. A missing result never authorizes replay;
Home Assistant approval records or explicit operator reconciliation clear it.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import functools
import uuid
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.helpers.storage import Store
from homeassistant.util.dt import utcnow

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .token_store import TokenStore

STORAGE_KEY = "phoenix_mcp_agent_recovery"
MAX_RECORDS = 200
_turn_id: ContextVar[str | None] = ContextVar("phoenix_recovery_turn", default=None)


class RecoveryRequired(RuntimeError):
    """A dispatch cannot safely start or its outcome cannot be established."""


class AgentRecovery:
    """Serialize durable claims before dispatch, fail closed on storage failure."""

    def __init__(self, store: Store[dict] | None = None) -> None:
        self._store = store
        self._records: dict[str, dict] = {}
        self._active: set[str] = set()
        self._live_turns: set[str] = set()
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        if self._store is None:
            return
        saved = await self._store.async_load()
        if saved is None:
            return
        if not isinstance(saved, dict) or not isinstance(saved.get("records"), dict):
            raise RecoveryRequired("Invalid agent recovery store")
        records = saved["records"]
        for rid, record in records.items():
            if (not isinstance(rid, str) or not isinstance(record, dict)
                    or not all(isinstance(record.get(k), str) for k in ("token_id", "tool", "started_at"))):
                raise RecoveryRequired("Invalid agent recovery record")
        self._records = records

    def records(self, token_id: str) -> list[dict]:
        return [
            {"request_id": rid, "tool": r["tool"], "started_at": r["started_at"],
             "status": ("dispatching" if rid in self._active else
                        "in_progress" if r.get("turn_id") in self._live_turns else "outcome_unknown"),
             "dispatch_result": r.get("dispatch_result", "unknown")}
            for rid, r in self._records.items() if r["token_id"] == token_id
        ]

    def blocks(self, token_id: str, own_request: str | None = None) -> bool:
        turn = _turn_id.get()
        return any(
            r["token_id"] == token_id and rid != own_request
            and not (turn is not None and r.get("turn_id") == turn
                     and r.get("dispatch_result") == "received")
            for rid, r in self._records.items()
        )

    async def received(self, rid: str) -> None:
        if _turn_id.get() is None:
            await self.finish(rid)
            return
        async with self._lock:
            self._records[rid]["dispatch_result"] = "received"
            await self._save()
            self._active.discard(rid)

    async def complete_turn(self, turn: str) -> None:
        async with self._lock:
            removed = {rid: r for rid, r in self._records.items() if r.get("turn_id") == turn}
            if not removed:
                return
            for rid in removed:
                del self._records[rid]
            try:
                await self._save()
            except BaseException:
                self._records.update(removed)
                raise

    async def _save(self) -> None:
        if self._store is not None:
            await self._store.async_save({"records": self._records})

    async def begin(self, token_id: str, tool: str, rid: str) -> None:
        async with self._lock:
            if self.blocks(token_id) or len(self._records) >= MAX_RECORDS:
                raise RecoveryRequired("Reconcile the previous agent operation in Home Assistant Repairs before retrying.")
            from .tool_defs import _TOOL_ANNOTATIONS, _NATIVE_TOOL_NAMES
            from .mesa_tools import MESA_TOOL_NAMES
            if tool not in _TOOL_ANNOTATIONS and tool not in _NATIVE_TOOL_NAMES and tool not in MESA_TOOL_NAMES:
                tool = "unknown_tool"
            self._records[rid] = {"token_id": token_id, "tool": tool, "started_at": utcnow().isoformat(), "turn_id": _turn_id.get()}
            try:
                await self._save()
            except Exception:
                # Retain the barrier in memory too: a failed save may still
                # have committed, and continuing would make that ambiguous.
                raise RecoveryRequired("Could not persist the dispatch barrier; nothing was dispatched.") from None
            self._active.add(rid)

    async def finish(self, rid: str) -> None:
        async with self._lock:
            record = self._records.pop(rid, None)
            if record is None:
                return
            try:
                await self._save()
            except BaseException:
                self._records[rid] = record
                raise
            finally:
                self._active.discard(rid)

    def interrupted(self, rid: str) -> None:
        self._active.discard(rid)

    async def reconcile(self, store: TokenStore, token_id: str) -> None:
        """Use an attributed HA approval receipt; never infer from device state."""
        for rid, record in list(self._records.items()):
            if record["token_id"] != token_id or rid in self._active or record.get("dispatch_result") == "received":
                continue
            for approval in store.get_pending_approvals():
                if approval.get("request_id") != rid or approval.get("token_id") != token_id:
                    continue
                status = approval.get("status")
                if status in ("approved", "rejected", "cancelled", "expired") or (
                    status == "pending" and not approval.get("execution_started_at")
                ):
                    # A pending queue entry proves dispatch reached the gate,
                    # not that the action ran. The existing approval owns it.
                    async with self._lock:
                        record["dispatch_result"] = "approval_" + status
                        await self._save()
                break

    async def reviewed(self, rid: str) -> bool:
        record = self._records.get(rid)
        if record is None or rid in self._active or record.get("turn_id") in self._live_turns:
            return False
        await self.finish(rid)
        return True

    async def async_wipe(self, hass: HomeAssistant | None = None) -> None:
        async with self._lock:
            old = self._records
            self._records = {}
            try:
                await self._save()
            except BaseException:
                self._records = old
                raise
            if hass is not None:
                from homeassistant.helpers import issue_registry as ir
                for rid in old:
                    ir.async_delete_issue(hass, DOMAIN, f"agent_recovery_{rid}")

    def publish_issues(self, hass: HomeAssistant) -> None:
        from homeassistant.helpers import issue_registry as ir

        for rid, record in self._records.items():
            if rid in self._active or record.get("turn_id") in self._live_turns:
                continue
            ir.async_create_issue(
                hass, DOMAIN, f"agent_recovery_{rid}", is_fixable=True,
                severity=ir.IssueSeverity.ERROR, translation_key="agent_recovery",
                translation_placeholders={"tool": record["tool"], "request_id": rid},
                data={"request_id": rid},
            )


def recovery_turn(run: Callable) -> Callable:
    """Retain dispatch receipts until the entire provider turn succeeds."""
    @functools.wraps(run)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        hass = kwargs["hass"] if "hass" in kwargs else args[0]
        data = kwargs["data"] if "data" in kwargs else args[1]
        turn = uuid.uuid4().hex
        ctx = _turn_id.set(turn)
        data.agent_recovery._live_turns.add(turn)
        failed = False
        emit = kwargs.get("emit")
        if emit is not None:
            async def observe(name: str, payload: dict) -> None:
                nonlocal failed
                if name == "error" or (name == "notice" and payload.get("code") == "max_tokens_stop"):
                    failed = True
                await emit(name, payload)
            kwargs["emit"] = observe
        try:
            result = await run(*args, **kwargs)
            cancel = kwargs.get("cancel")
            if not failed and not (cancel is not None and cancel.is_set()) and getattr(result, "outcome", "ok") == "ok":
                await data.agent_recovery.complete_turn(turn)
            return result
        finally:
            data.agent_recovery._live_turns.discard(turn)
            _turn_id.reset(ctx)
            data.agent_recovery.publish_issues(hass)
    return guarded


def tool_is_read_only(name: str) -> bool:
    """Use the server-owned catalog, never model-provided annotations."""
    from .tool_defs import _TOOL_ANNOTATIONS, _NATIVE_TOOL_NAMES
    from .mesa_tools import _MESA_TOOL_ANNOTATIONS

    if name in _MESA_TOOL_ANNOTATIONS:
        return _MESA_TOOL_ANNOTATIONS[name]["readOnlyHint"] is True
    canonical = _NATIVE_TOOL_NAMES.get(name, name)
    return _TOOL_ANNOTATIONS.get(canonical, {}).get("readOnlyHint") is True



async def dispatch_agent_tool(
    dispatch: Any, *, hass: HomeAssistant, data: Any, token: Any,
    name: str, arguments: dict, tool_id: Any, request_id: str,
    client_ip: str, base_url: str,
) -> tuple:
    """One dispatch, with no retry after the durable boundary."""
    recovery = data.agent_recovery
    write = name != "phoenix_request" and not tool_is_read_only(name)
    try:
        if write:
            await recovery.reconcile(data.store, token.id)
            await recovery.begin(token.id, name, request_id)
        response = await dispatch(
            "tools/call", tool_id, {"name": name, "arguments": arguments},
            token, hass, data, client_ip, base_url, request_id=request_id,
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
            elif isinstance(msg, dict) and isinstance(msg.get("error"), dict) and outcome == "denied":
                msg["error"]["message"] = "Authority changed during dispatch. The action outcome is unknown; reconcile Home Assistant before retrying."
            else:
                raise RecoveryRequired("The action outcome is unknown. Check Home Assistant Repairs before retrying.")
        return response
    except Exception:
        if write:
            raise RecoveryRequired("The action outcome is unknown. Check Home Assistant Repairs before retrying.") from None
        raise
    finally:
        if write:
            recovery.interrupted(request_id)
            recovery.publish_issues(hass)
