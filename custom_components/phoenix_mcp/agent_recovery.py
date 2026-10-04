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
    from .access_key_store import AccessKeyStore

STORAGE_KEY = "phoenix_mcp_agent_recovery"
MAX_RECORDS = 200
_turn_id: ContextVar[str | None] = ContextVar("phoenix_recovery_turn", default=None)


class RecoveryRequired(RuntimeError):
    """A dispatch cannot safely start or its outcome cannot be established."""


def _journal_tool_name(tool: str) -> str:
    """Persist only server catalog names, never an unbounded caller label."""
    from .tool_defs import _TOOL_ANNOTATIONS, _NATIVE_TOOL_NAMES
    from .mesa_tools import MESA_TOOL_NAMES

    return tool if tool in _TOOL_ANNOTATIONS or tool in _NATIVE_TOOL_NAMES or tool in MESA_TOOL_NAMES else "unknown_tool"


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
        from .access_key_schema import normalize_access_key_fields

        records = {rid: normalize_access_key_fields(row) if isinstance(row, dict) else row
                   for rid, row in saved["records"].items()}
        for rid, record in records.items():
            if (not isinstance(rid, str) or not isinstance(record, dict)
                    or not all(isinstance(record.get(k), str) for k in ("access_key_id", "tool", "started_at"))):
                raise RecoveryRequired("Invalid agent recovery record")
        self._records = records

    def records(self, access_key_id: str) -> list[dict]:
        return [
            {"request_id": rid, "tool": r["tool"], "started_at": r["started_at"],
             "status": ("dispatching" if rid in self._active else
                        "in_progress" if r.get("turn_id") in self._live_turns else "outcome_unknown"),
             "dispatch_result": r.get("dispatch_result", "unknown")}
            for rid, r in self._records.items() if r["access_key_id"] == access_key_id
        ]

    def blocks(self, access_key_id: str, own_request: str | None = None) -> bool:
        turn = _turn_id.get()
        return any(
            r["access_key_id"] == access_key_id and rid != own_request
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

    async def complete_received_turn(self, hass: HomeAssistant, turn: str) -> None:
        """Finalize only this turn's received receipts after a code-owned report."""
        from homeassistant.helpers import issue_registry as ir

        async with self._lock:
            removed = {rid: r for rid, r in self._records.items()
                       if r.get("turn_id") == turn and r.get("dispatch_result") == "received"
                       and rid not in self._active and turn not in self._live_turns}
            if not removed:
                return
            for rid in removed:
                del self._records[rid]
            try:
                await self._save()
            except BaseException:
                self._records.update(removed)
                raise
            for rid in removed:
                ir.async_delete_issue(hass, DOMAIN, f"agent_recovery_{rid}")

    async def _save(self) -> None:
        if self._store is not None:
            await self._store.async_save({"records": self._records})

    async def begin(self, access_key_id: str, tool: str, rid: str) -> None:
        async with self._lock:
            if self.blocks(access_key_id) or len(self._records) >= MAX_RECORDS:
                raise RecoveryRequired("Reconcile the previous agent operation in Phoenix MCP > Needs attention before retrying.")
            tool = _journal_tool_name(tool)
            self._records[rid] = {"access_key_id": access_key_id, "tool": tool, "started_at": utcnow().isoformat(), "turn_id": _turn_id.get()}
            try:
                await self._save()
            except Exception:
                # Retain the barrier in memory too: a failed save may still
                # have committed, and continuing would make that ambiguous.
                raise RecoveryRequired("Could not persist the dispatch barrier; nothing was dispatched.") from None
            self._active.add(rid)

    async def retain_unknown(self, access_key_id: str, tool: str, rid: str) -> None:
        """Retain a write whose authority was lost after entering dispatch."""
        async with self._lock:
            if rid not in self._records:
                self._records[rid] = {
                    "access_key_id": access_key_id, "tool": _journal_tool_name(tool),
                    "started_at": utcnow().isoformat(), "turn_id": _turn_id.get(),
                }
            try:
                await self._save()
            finally:
                self._active.discard(rid)

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

    async def reconcile(self, store: AccessKeyStore, access_key_id: str) -> None:
        """Use an attributed HA approval receipt; never infer from device state."""
        for rid, record in list(self._records.items()):
            if record["access_key_id"] != access_key_id or rid in self._active or record.get("dispatch_result") == "received":
                continue
            for approval in store.get_pending_approvals():
                if approval.get("request_id") != rid or approval.get("access_key_id") != access_key_id:
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
        """Persist one explicit review while preserving active and failed claims."""
        async with self._lock:
            record = self._records.get(rid)
            if record is None or rid in self._active or record.get("turn_id") in self._live_turns:
                return False
            del self._records[rid]
            try:
                await self._save()
            except BaseException:
                self._records[rid] = record
                raise
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

    def admin_records(self) -> list[dict]:
        """Return bounded dispatch metadata for the authenticated admin panel."""
        return [dict(row, access_key_id=access_key_id)
                for access_key_id in dict.fromkeys(r["access_key_id"] for r in self._records.values())
                for row in self.records(access_key_id)
                if row["status"] == "outcome_unknown"]


def recovery_turn(run: Callable) -> Callable:
    """Retain dispatch receipts until the entire provider turn succeeds."""
    @functools.wraps(run)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
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
            if hasattr(result, "recovery_turn_id"):
                result.recovery_turn_id = turn
            cancel = kwargs.get("cancel")
            if not failed and not (cancel is not None and cancel.is_set()) and getattr(result, "outcome", "ok") == "ok":
                await data.agent_recovery.complete_turn(turn)
            return result
        finally:
            data.agent_recovery._live_turns.discard(turn)
            _turn_id.reset(ctx)
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
    dispatch: Any, *, hass: HomeAssistant, data: Any, access_key: Any,
    name: str, arguments: dict, tool_id: Any, request_id: str,
    client_ip: str, base_url: str,
) -> tuple:
    """One dispatch, with no retry after the durable boundary."""
    recovery = data.agent_recovery
    # Unknown names are refused before a handler can run. Do not turn a model
    # typo into a durable claim that an action might have reached HA.
    write = (name != "home_request" and _journal_tool_name(name) != "unknown_tool"
             and not tool_is_read_only(name))
    try:
        if write:
            await recovery.reconcile(data.store, access_key.id)
            await recovery.begin(access_key.id, name, request_id)
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
            elif (isinstance(msg, dict) and isinstance(msg.get("error"), dict)
                  and msg["error"].get("code") == -32000):
                msg["error"]["message"] = "Authority changed during dispatch. The action outcome is unknown; reconcile Home Assistant before retrying."
            else:
                raise RecoveryRequired("The action outcome is unknown. Check Phoenix MCP > Needs attention before retrying.")
        return response
    except Exception:
        if write:
            raise RecoveryRequired("The action outcome is unknown. Check Phoenix MCP > Needs attention before retrying.") from None
        raise
    finally:
        if write:
            recovery.interrupted(request_id)
