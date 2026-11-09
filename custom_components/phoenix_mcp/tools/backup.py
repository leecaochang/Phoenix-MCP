"""List and create Home Assistant backups."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import Any, Literal

from homeassistant.core import callback, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from ..access_key_store import AccessKeyRecord
from ..const import (
    BACKUP_COMPLETION_WAIT_SECONDS, BACKUP_SINGLE_SLOT_AGENT_DOMAINS, CAP_DENY,
    LOCAL_BACKUP_AGENT_IDS,
)
from ..data import PhoenixData
from ..helpers import log_internal_failure
from ..catalog_text import diff_summary_fields as _summary
from ..capability import effective_cap
from ..request_authority import check_request_authority, mark_dispatch_started, write_boundary
from ..tool_common import _gate, _tool_error, _tool_success
from ..ws_dispatch import async_ws_command, WsDispatchError


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backup (cap_backup) - create + list only; restore is intentionally unsupported
# ---------------------------------------------------------------------------


async def _backup_agent_ids(hass: HomeAssistant) -> list[str]:
    """Available backup agent ids (e.g. hassio.local on HAOS, backup.local on Core).

    Raises WsDispatchError rather than degrading to [], which means "no agents" and which callers
    act on.
    """
    info = await async_ws_command(hass, "backup/agents/info", {})
    agents = info.get("agents") if isinstance(info, dict) else None
    if not isinstance(agents, list):
        return []
    return [str(a["agent_id"]) for a in agents if isinstance(a, dict) and a.get("agent_id")]


def _backup_to_summary(b: Any) -> dict:
    """Project one backup (a ManagerBackup dataclass or dict) to compact JSON.

    The raw backup/info result holds dataclass instances; serializing them with
    json default=str produces unparseable repr strings, so flatten to fields.
    """
    if isinstance(b, dict):
        d = b
    elif dataclasses.is_dataclass(b) and not isinstance(b, type):
        try:
            d = dataclasses.asdict(b)
        except Exception:  # fall back to attribute access
            d = {}
    else:
        d = {}
    fields = ("backup_id", "name", "date", "database_included", "homeassistant_version")
    out: dict = {f: (d.get(f) if d else getattr(b, f, None)) for f in fields}
    agents = d.get("agents") if d else getattr(b, "agents", None)
    size = None
    agent_ids: list = []
    protected: list = []
    if isinstance(agents, dict):
        agent_ids = list(agents.keys())
        for a in agents.values():
            sz = a.get("size") if isinstance(a, dict) else getattr(a, "size", None)
            if sz is not None and size is None:
                size = sz
            protected.append(a.get("protected") if isinstance(a, dict) else getattr(a, "protected", None))
    out["size"] = size
    out["agents"] = agent_ids
    # True only when every stored copy is encrypted; None when HA did not say.
    out["encrypted"] = all(protected) if protected and None not in protected else None
    return out


async def _tool_list_backups(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list existing backups (compact, newest first) and available agents."""
    if effective_cap(access_key, "cap_backup") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_backups"
    try:
        result = await async_ws_command(hass, "backup/info", {})
    except WsDispatchError as exc:
        return _tool_error(f"Failed to list backups: {exc}"), "invalid_request", "list_backups"
    raw = result.get("backups") if isinstance(result, dict) else None
    backups = raw if isinstance(raw, list) else []
    summaries = [_backup_to_summary(b) for b in backups]
    summaries.sort(key=lambda s: s.get("date") or "", reverse=True)
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 200))
    warnings: list[str] = []
    agent_errors = result.get("agent_errors") if isinstance(result, dict) else None
    if isinstance(agent_errors, dict) and agent_errors:
        # HA lists what the reachable agents returned; an unreachable one would
        # otherwise just be missing, and its backups with it.
        warnings.append(
            "Backups could not be read from " + ", ".join(sorted(map(str, agent_errors)))
            + ", so this list may be incomplete."
        )
    try:
        available_agents = await _backup_agent_ids(hass)
    except WsDispatchError as exc:
        available_agents = []
        warnings.append(f"Failed to list backup agents: {exc}")
    body = {
        "count": min(len(summaries), limit),
        "total": len(summaries),
        "has_more": len(summaries) > limit,
        "returned": min(len(summaries), limit),
        # Explicit, even though total vs returned already implies it: every other
        # paginating read states truncation as a boolean, and a reader that has to
        # compare two numbers to notice will sometimes not compare them.
        "truncated": len(summaries) > limit,
        "backups": summaries[:limit],
        "available_agents": available_agents,
    }
    if isinstance(result, dict) and result.get("state") is not None:
        # Lets a caller told "started" see whether that backup is still running.
        body["manager_state"] = str(result["state"])
    if isinstance(agent_errors, dict) and agent_errors:
        body["agent_errors"] = {str(k): str(v) for k, v in agent_errors.items()}
    if warnings:
        body["warnings"] = warnings
    return _tool_success(json.dumps(body, default=str)), "allowed", "list_backups"


def _backup_manager(hass: HomeAssistant) -> Any | None:
    """HA's backup manager, or None when the backup integration is not loaded."""
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.backup import async_get_manager
        return async_get_manager(hass)
    except (ImportError, HomeAssistantError):
        return None


def _single_slot_backup_agent(agent_id: str) -> bool:
    return agent_id.split(".", 1)[0] in BACKUP_SINGLE_SLOT_AGENT_DOMAINS


@dataclasses.dataclass(frozen=True)
class _BackupPlan:
    """Where a Phoenix MCP backup goes and what it holds.

    Resolved from HA's stored backup settings. It records whether HA holds a
    backup key, never the key itself, so it is safe in a preview or a result.
    """

    agent_ids: list[str]
    skipped_agent_ids: list[str]
    destination: Literal["requested", "backup_settings", "local"]
    has_key: bool
    # Locations whose own setting stores backups unencrypted even with a key.
    unencrypted_agent_ids: list[str]
    include_database: bool
    include_all_addons: bool
    include_addons: list[str] | None
    include_folders: list[Any] | None

    @property
    def encrypted(self) -> bool:
        return self.has_key and not self.unencrypted_agent_ids

    def preview(self) -> dict:
        return {
            "agent_ids": self.agent_ids, "skipped_agent_ids": self.skipped_agent_ids,
            "destination": self.destination, "encrypted": self.encrypted,
            "unencrypted_agent_ids": self.unencrypted_agent_ids if self.has_key else self.agent_ids,
            "contents": {
                "homeassistant": True, "database": self.include_database,
                "all_addons": self.include_all_addons, "addons": self.include_addons or [],
                "folders": [str(getattr(f, "value", f)) for f in self.include_folders or []],
            },
        }


def _backup_plan(manager: Any, args: dict) -> _BackupPlan:
    """Resolve the destination and contents, or raise _BackupRefused.

    Without agent_ids it follows the automatic backup settings minus single-slot agents, else a
    local agent. Contents and encryption always follow those settings.
    """
    stored = manager.config.data.create_backup
    requested = args.get("agent_ids")
    skipped: list[str] = []
    destination: Literal["requested", "backup_settings", "local"]
    if requested is not None:
        if single := [a for a in requested if _single_slot_backup_agent(a)]:
            raise _BackupRefused(
                f"Refused: {', '.join(single)} keeps one backup, so a backup created here would "
                "replace the operator's own. Choose other agent_ids, or omit agent_ids."
            )
        agent_ids, destination = list(requested), "requested"
    else:
        configured = [str(a) for a in stored.agent_ids or []]
        skipped = [a for a in configured if _single_slot_backup_agent(a)]
        agent_ids, destination = [a for a in configured if a not in skipped], "backup_settings"
        if not agent_ids:
            available = list(manager.backup_agents)
            agent_ids = next(([a] for a in LOCAL_BACKUP_AGENT_IDS if a in available), available[:1])
            destination = "local"
        if not agent_ids:
            raise _BackupRefused("No backup agents are available; pass agent_ids explicitly.")
    # HA's own rule, on Core and the Supervisor alike: with a key, a location
    # encrypts unless its own setting says not to.
    agent_settings = manager.config.data.agents
    unencrypted = [a for a in agent_ids if a in agent_settings and not agent_settings[a].protected]
    return _BackupPlan(
        agent_ids=agent_ids, skipped_agent_ids=skipped, destination=destination,
        has_key=bool(stored.password), unencrypted_agent_ids=unencrypted,
        include_database=stored.include_database,
        include_all_addons=stored.include_all_addons, include_addons=stored.include_addons,
        include_folders=stored.include_folders,
    )


class _BackupRefused(Exception):
    """A create_backup refusal found before anything was dispatched."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.result: tuple[dict, str, str] = (_tool_error(message), "invalid_request", "create_backup")


def _backup_precheck(hass: HomeAssistant, args: dict) -> tuple[Any, _BackupPlan]:
    """Side-effect-free refusals, run before an approval and again in the executor."""
    if _backup_agent_ids_malformed(args):
        raise _BackupRefused(_BACKUP_AGENT_IDS_REFUSAL)
    manager = _backup_manager(hass)
    if manager is None:
        raise _BackupRefused("The Home Assistant backup integration is not available.")
    if str(manager.state) != "idle":
        raise _BackupRefused(
            "Home Assistant is already creating, receiving or restoring a backup. "
            "Try again when it finishes; list_backups shows manager_state."
        )
    return manager, _backup_plan(manager, args)


async def _tool_create_backup(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a backup (Confirm-gated)."""
    if effective_cap(access_key, "cap_backup") != CAP_DENY:
        try:
            _backup_precheck(hass, args)
        except _BackupRefused as refused:
            return refused.result
    blocked = await _gate(
        "cap_backup", access_key, hass, data,
        tool_name="create_backup", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_create_backup(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_backup(args, access_key, hass, data)


_BACKUP_AGENT_IDS_REFUSAL = "agent_ids must be a non-empty array of backup agent IDs; omit it to use the backup settings."


def _backup_agent_ids_malformed(args: dict) -> bool:
    """A malformed agent_ids is refused instead of silently choosing other agents."""
    agent_ids = args.get("agent_ids")
    return not (agent_ids is None or (isinstance(agent_ids, list) and agent_ids
                                      and all(isinstance(item, str) and item for item in agent_ids)))


def _backup_result(plan: _BackupPlan, backup: Any | None) -> dict:
    """Describe the created backup from HA's own record of it."""
    body: dict[str, Any] = {"destination": plan.destination}
    if plan.skipped_agent_ids:
        body["skipped_agent_ids"] = plan.skipped_agent_ids
    notes: list[str] = []
    unencrypted = plan.unencrypted_agent_ids
    if backup is None:
        body["encrypted"] = plan.encrypted
    else:
        agents = {aid: {"encrypted": status.protected, "size": status.size} for aid, status in backup.agents.items()}
        body["backup"] = {
            "backup_id": backup.backup_id, "name": backup.name, "date": backup.date,
            "homeassistant_included": backup.homeassistant_included,
            "database_included": backup.database_included,
            "addons": [addon.slug for addon in backup.addons],
            "folders": [str(getattr(f, "value", f)) for f in backup.folders],
            "agents": agents,
        }
        body["encrypted"] = bool(agents) and all(a["encrypted"] for a in agents.values())
        body["failed_agent_ids"] = list(backup.failed_agent_ids)
        body["failed_addons"] = [addon.slug for addon in backup.failed_addons]
        body["failed_folders"] = [str(getattr(f, "value", f)) for f in backup.failed_folders]
        unencrypted = [aid for aid, a in agents.items() if not a["encrypted"]]
    if plan.has_key and unencrypted:
        notes.append(
            f"Stored unencrypted on {', '.join(unencrypted)}: Home Assistant's backup settings turn "
            "encryption off for that location, so the backup holds secrets.yaml in plain text there."
        )
    if not plan.has_key:
        notes.append(
            "Not encrypted: Home Assistant has no backup encryption key, so this backup holds "
            "secrets.yaml in plain text. Setting up automatic backups in Home Assistant creates one."
        )
    if all(aid in LOCAL_BACKUP_AGENT_IDS for aid in plan.agent_ids):
        reason = ", because its backup settings name no backup locations" if plan.destination == "local" else ""
        notes.append(
            f"Stored only on this Home Assistant's own storage{reason}. It does not survive the loss of that disk."
        )
    if plan.skipped_agent_ids:
        notes.append(
            f"Not sent to {', '.join(plan.skipped_agent_ids)}: it keeps one backup, so this one "
            "would have replaced the operator's own."
        )
    if notes:
        body["notes"] = notes
    return body


@write_boundary
async def _execute_create_backup(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Create the backup and wait, within a bound, for HA to report how it ended.

    HA's backup key goes straight from its stored settings into the manager call.
    It is never placed in arguments, previews, results, logs or error text.
    """
    # Local: optional component, imported only when this path runs.
    from homeassistant.components.backup import CreateBackupEvent

    try:
        manager, plan = _backup_precheck(hass, args)
    except _BackupRefused as refused:
        return refused.result
    name = args.get("name")
    try:
        before, _ = await manager.async_get_backups()
    except Exception:  # only narrows which backup is new
        _LOGGER.debug("Could not list backups before creating one", exc_info=True)
        before = {}
    finished = asyncio.Event()
    ended: dict[str, Any] = {}

    @callback
    def _on_event(event: Any) -> None:
        if isinstance(event, CreateBackupEvent) and str(event.state) in ("completed", "failed"):
            ended.update(state=str(event.state), reason=event.reason)
            finished.set()

    unsubscribe = manager.async_subscribe_events(_on_event)
    try:
        check_request_authority()
        mark_dispatch_started()
        try:
            new_backup = await manager.async_initiate_backup(
                agent_ids=plan.agent_ids,
                include_addons=plan.include_addons,
                include_all_addons=plan.include_all_addons,
                include_database=plan.include_database,
                include_folders=plan.include_folders,
                include_homeassistant=True,
                name=name.strip() if isinstance(name, str) and name.strip() else None,
                password=manager.config.data.create_backup.password,
            )
        except HomeAssistantError as exc:
            key = manager.config.data.create_backup.password
            message = str(exc)
            if key and key in message:
                message = "Home Assistant refused the backup."
            return _tool_error(f"Failed to create backup: {message}"), "invalid_request", "create_backup"
        except Exception as exc:  # HA's text may carry arguments; keep it out
            log_internal_failure(_LOGGER, "create_backup", exc)
            return _tool_error("Failed to create backup: internal error."), "invalid_request", "create_backup"
        try:
            async with asyncio.timeout(BACKUP_COMPLETION_WAIT_SECONDS):
                await finished.wait()
        except TimeoutError:
            pass
    finally:
        unsubscribe()
    check_request_authority()
    job_id = new_backup.backup_job_id
    status = ended.get("state", "started")
    backup = None
    if status != "started":
        try:
            after, _ = await manager.async_get_backups()
        except Exception:  # the backup exists; only the readback failed
            _LOGGER.debug("Could not read the new backup back", exc_info=True)
            after = {}
        new = {bid: b for bid, b in after.items() if bid not in before}
        # Core reuses the job ID as the backup ID; the Supervisor does not, and
        # the manager runs one backup at a time, so a single new entry is it.
        backup = new.get(job_id) or (next(iter(new.values())) if len(new) == 1 else None)
    body: dict[str, Any] = {
        "created": status == "completed" or (status == "failed" and backup is not None and bool(backup.agents)),
        "status": status, "backup_job_id": job_id, "verified": backup is not None,
    }
    if status == "failed":
        body["reason"] = ended.get("reason")
    if status == "started":
        body["message"] = (
            f"The backup was still running after {BACKUP_COMPLETION_WAIT_SECONDS} seconds. "
            "Do not start another; check list_backups until manager_state is idle."
        )
    body.update(_backup_result(plan, backup))
    return _tool_success(json.dumps(body, default=str)), "allowed", "create_backup"


def _build_diff_create_backup(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    name = args.get("name") if isinstance(args.get("name"), str) else None
    preview: dict[str, Any] = {"name": name}
    manager = _backup_manager(hass)
    try:
        if manager is None:
            raise _BackupRefused("The Home Assistant backup integration is not available.")
        # Resolved again when the backup runs, so a settings change in between applies.
        preview.update(_backup_plan(manager, args).preview())
    except _BackupRefused as refused:
        preview["refused"] = str(refused)
    preview["note"] = "Creates a backup; Phoenix MCP cannot restore backups."
    return {
        "kind": "system_action",
        **(_summary("create_backup.named", name=name) if name else _summary("create_backup")),
        "target": {"type": "backup", "id": None, "label": name},
        "preview": preview,
    }
