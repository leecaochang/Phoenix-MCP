"""create_backup and list_backups against Home Assistant's real backup manager.

The backup integration runs for real here: Core's local agent writes tar files
to the test config directory, and in-memory agents registered through HA's own
backup platform stand in for a remote location, a failing upload, an unreadable
location and Home Assistant Cloud. Covers the destination and encryption taken
from the operator's backup settings, completion reporting, refusals, and that
HA's backup key never reaches anything Phoenix MCP stores, logs or returns.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.backup import (
    AgentBackup,
    BackupAgent,
    BackupAgentError,
    BackupNotFound,
    CreateBackupEvent,
    CreateBackupState,
    IdleEvent,
    NewBackup,
    async_get_manager,
)
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockModule, mock_integration, mock_platform

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.rate_limiter import RateLimiter

BACKUP_KEY = "Phoenix-test-backup-key-7Qx2"


class MemoryAgent(BackupAgent):
    """A backup location that keeps uploads in memory, from HA's agent interface."""

    def __init__(self, domain: str, unique_id: str) -> None:
        self.domain = domain
        self.name = unique_id
        self.unique_id = unique_id
        self.backups: dict[str, AgentBackup] = {}
        self.fail_upload = False
        self.fail_list = False
        self.hold_upload: asyncio.Event | None = None

    async def async_download_backup(self, backup_id: str, **kwargs: Any):
        raise BackupNotFound

    async def async_upload_backup(self, *, open_stream, backup: AgentBackup, on_progress, **kwargs: Any) -> None:
        if self.hold_upload is not None:
            await self.hold_upload.wait()
        if self.fail_upload:
            raise BackupAgentError("upload refused")
        async for _ in await open_stream():
            pass
        self.backups[backup.backup_id] = backup

    async def async_delete_backup(self, backup_id: str, **kwargs: Any) -> None:
        self.backups.pop(backup_id, None)

    async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
        if self.fail_list:
            raise BackupAgentError("location unreachable")
        return list(self.backups.values())

    async def async_get_backup(self, backup_id: str, **kwargs: Any) -> AgentBackup:
        if self.fail_list:
            raise BackupAgentError("location unreachable")
        if backup_id not in self.backups:
            raise BackupNotFound
        return self.backups[backup_id]


def _register_agents(hass, domain: str, agents: list[MemoryAgent]) -> None:
    async def async_get_backup_agents(_hass):
        return agents

    mock_integration(hass, MockModule(domain))
    mock_platform(hass, f"{domain}.backup", type("Platform", (), {"async_get_backup_agents": staticmethod(async_get_backup_agents)})())


@pytest.fixture
async def backup_env(hass, hass_admin_user):
    remote, failing = MemoryAgent("test", "remote"), MemoryAgent("test", "failing")
    cloud = MemoryAgent("cloud", "cloud")
    _register_agents(hass, "test", [remote, failing])
    _register_agents(hass, "cloud", [cloud])
    assert await async_setup_component(hass, "test", {})
    assert await async_setup_component(hass, "cloud", {})
    assert await async_setup_component(hass, "backup", {})
    await hass.async_block_till_done()
    manager = async_get_manager(hass)
    assert {"backup.local", "test.remote", "test.failing", "cloud.cloud"} <= set(manager.backup_agents)
    store = AccessKeyStore(hass, AsyncMock())
    store.get_settings().mesa_mode = "off"
    store.get_settings().conversation_log_enabled = True
    access_key, _ = await store.async_create_access_key("backup-tests", "admin", rate_limit_requests=0)
    access_key.cap_backup = "allow"
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog(), hass=hass)
    hass.data[DOMAIN] = data
    yield {"hass": hass, "manager": manager, "data": data, "key": access_key,
            "remote": remote, "failing": failing, "cloud": cloud}
    if data.sensor_flush_cancel is not None:
        data.sensor_flush_cancel()


def _configure(manager, agent_ids: list[str], password: str | None = BACKUP_KEY) -> None:
    manager.config.update(create_backup={
        "agent_ids": agent_ids, "include_addons": None, "include_all_addons": False,
        "include_database": True, "include_folders": None, "name": None, "password": password,
    })


async def _call(env, name: str, args: dict):
    return await mcp_view._call_tool(name, args, env["key"], env["hass"], env["data"])


def _body(result) -> dict:
    return json.loads(result[0]["content"][0]["text"])


async def _backup_ids(manager) -> set[str]:
    backups, _ = await manager.async_get_backups()
    return set(backups)


class TestDestinationAndEncryption:
    async def test_backup_settings_decide_destination_and_encryption(self, backup_env):
        _configure(backup_env["manager"], ["backup.local", "test.remote"])
        result = await _call(backup_env, "create_backup", {"name": "Before edits"})
        assert result[1] == "allowed"
        body = _body(result)
        assert body["status"] == "completed" and body["created"] is True and body["verified"] is True
        assert body["destination"] == "backup_settings"
        assert set(body["backup"]["agents"]) == {"backup.local", "test.remote"}
        assert body["encrypted"] is True
        assert all(a["encrypted"] for a in body["backup"]["agents"].values())
        assert body["backup"]["name"] == "Before edits"
        assert body["failed_agent_ids"] == [] and "notes" not in body
        # HA's record agrees: the remote location received a protected copy.
        stored = next(iter(backup_env["remote"].backups.values()))
        assert stored.protected is True and stored.backup_id == body["backup"]["backup_id"]
        # A Phoenix backup is a custom one: retention and the automatic-backup
        # markers and repair issue are the operator's alone.
        assert stored.extra_metadata["with_automatic_settings"] is False
        assert backup_env["manager"].config.data.last_completed_automatic_backup is None

    async def test_cloud_in_backup_settings_is_skipped_and_said(self, backup_env):
        _configure(backup_env["manager"], ["cloud.cloud", "test.remote"])
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["status"] == "completed"
        assert set(body["backup"]["agents"]) == {"test.remote"}
        assert body["skipped_agent_ids"] == ["cloud.cloud"]
        assert any("cloud.cloud" in note for note in body["notes"])
        assert backup_env["cloud"].backups == {}

    async def test_only_cloud_configured_falls_back_to_local(self, backup_env):
        _configure(backup_env["manager"], ["cloud.cloud"])
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["destination"] == "local" and set(body["backup"]["agents"]) == {"backup.local"}
        assert backup_env["cloud"].backups == {}

    async def test_no_settings_is_local_unencrypted_and_says_so(self, backup_env):
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["status"] == "completed" and body["destination"] == "local"
        assert set(body["backup"]["agents"]) == {"backup.local"}
        assert body["encrypted"] is False
        assert any(note.startswith("Not encrypted") for note in body["notes"])
        assert any("own storage" in note for note in body["notes"])

    async def test_local_only_settings_are_said_without_blaming_settings(self, backup_env):
        _configure(backup_env["manager"], ["backup.local"])
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["destination"] == "backup_settings"
        assert body["notes"] == ["Stored only on this Home Assistant's own storage. It does not survive the loss of that disk."]

    async def test_stored_key_encrypts_even_without_configured_agents(self, backup_env):
        _configure(backup_env["manager"], [])
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["destination"] == "local" and body["encrypted"] is True
        assert not any(note.startswith("Not encrypted") for note in body["notes"])

    async def test_local_fallback_prefers_the_supervisor_agent(self, backup_env):
        # On HAOS both hassio.local and Core's backup.local can exist; the
        # Supervisor's is the one the operator sees as local storage.
        manager = backup_env["manager"]
        supervisor = MemoryAgent("hassio", "local")
        manager.backup_agents[supervisor.agent_id] = supervisor
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["destination"] == "local" and set(body["backup"]["agents"]) == {"hassio.local"}

    async def test_explicit_agents_are_used_and_still_encrypted(self, backup_env):
        _configure(backup_env["manager"], ["backup.local"])
        body = _body(await _call(backup_env, "create_backup", {"agent_ids": ["test.remote"]}))
        assert body["destination"] == "requested"
        assert set(body["backup"]["agents"]) == {"test.remote"} and body["encrypted"] is True

    async def test_location_set_to_unencrypted_is_reported(self, backup_env):
        manager = backup_env["manager"]
        _configure(manager, ["backup.local", "test.remote"])
        manager.config.update(agents={"test.remote": {"protected": False}})
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["encrypted"] is False
        assert body["backup"]["agents"]["test.remote"]["encrypted"] is False
        assert body["backup"]["agents"]["backup.local"]["encrypted"] is True
        assert any("unencrypted on test.remote" in note for note in body["notes"])

    async def test_location_set_to_unencrypted_is_reported_before_readback(self, backup_env):
        # A backup still running has no record yet, so the plan alone must not
        # call it encrypted when a location's setting turns encryption off.
        manager = backup_env["manager"]
        _configure(manager, ["test.remote"])
        manager.config.update(agents={"test.remote": {"protected": False}})
        hold = asyncio.Event()
        backup_env["remote"].hold_upload = hold
        with patch.object(mcp_view, "BACKUP_COMPLETION_WAIT_SECONDS", 0.05):
            body = _body(await _call(backup_env, "create_backup", {}))
        hold.set()
        await backup_env["hass"].async_block_till_done()
        assert body["status"] == "started" and body["encrypted"] is False
        assert any("unencrypted on test.remote" in note for note in body["notes"])
        stored = next(iter(backup_env["remote"].backups.values()))
        assert stored.protected is False


class TestRefusals:
    async def test_explicit_cloud_is_refused_before_anything_runs(self, backup_env):
        before = await _backup_ids(backup_env["manager"])
        result = await _call(backup_env, "create_backup", {"agent_ids": ["test.remote", "cloud.cloud"]})
        assert result[1] == "invalid_request"
        assert "keeps one backup" in result[0]["content"][0]["text"]
        assert await _backup_ids(backup_env["manager"]) == before

    async def test_busy_manager_is_refused(self, backup_env):
        manager = backup_env["manager"]
        manager.async_on_backup_event(CreateBackupEvent(reason=None, stage=None, state=CreateBackupState.IN_PROGRESS))
        try:
            result = await _call(backup_env, "create_backup", {})
        finally:
            manager.async_on_backup_event(IdleEvent())
        assert result[1] == "invalid_request"
        assert "already creating" in result[0]["content"][0]["text"]
        assert await _backup_ids(manager) == set()

    async def test_busy_manager_refuses_before_an_approval_exists(self, backup_env):
        backup_env["key"].cap_backup = "confirm"
        manager = backup_env["manager"]
        manager.async_on_backup_event(CreateBackupEvent(reason=None, stage=None, state=CreateBackupState.IN_PROGRESS))
        try:
            result = await _call(backup_env, "create_backup", {})
        finally:
            manager.async_on_backup_event(IdleEvent())
        assert result[1] == "invalid_request"
        assert backup_env["data"].store.get_pending_approvals() == []

    async def test_missing_backup_integration_is_refused(self, hass):
        store = AccessKeyStore(hass, AsyncMock())
        key, _ = await store.async_create_access_key("t", "admin", rate_limit_requests=0)
        key.cap_backup = "allow"
        data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
        hass.data[DOMAIN] = data
        result = await mcp_view._call_tool("create_backup", {}, key, hass, data)
        assert result[1] == "invalid_request"
        assert "not available" in result[0]["content"][0]["text"]


class TestOutcome:
    async def test_failed_upload_reports_where_it_landed(self, backup_env):
        backup_env["failing"].fail_upload = True
        _configure(backup_env["manager"], ["backup.local", "test.failing"])
        body = _body(await _call(backup_env, "create_backup", {}))
        assert body["status"] == "failed" and body["reason"] == "upload_failed"
        assert body["created"] is True and body["verified"] is True
        assert body["failed_agent_ids"] == ["test.failing"]
        assert set(body["backup"]["agents"]) == {"backup.local"}

    async def test_timeout_reports_started_not_created(self, backup_env):
        hold = asyncio.Event()
        backup_env["remote"].hold_upload = hold
        _configure(backup_env["manager"], ["test.remote"])
        with patch.object(mcp_view, "BACKUP_COMPLETION_WAIT_SECONDS", 0.05):
            body = _body(await _call(backup_env, "create_backup", {}))
        assert body["status"] == "started" and body["created"] is False and body["verified"] is False
        assert "manager_state" in body["message"]
        listed = _body(await _call(backup_env, "list_backups", {}))
        assert listed["manager_state"] == "create_backup"
        hold.set()
        await backup_env["hass"].async_block_till_done()
        assert str(backup_env["manager"].state) == "idle"

    async def test_job_id_differing_from_backup_id_still_finds_the_backup(self, backup_env):
        manager = backup_env["manager"]
        real = manager.async_initiate_backup

        async def supervisor_like(**kwargs):
            await real(**kwargs)
            return NewBackup(backup_job_id="supervisor-job")

        with patch.object(manager, "async_initiate_backup", side_effect=supervisor_like):
            body = _body(await _call(backup_env, "create_backup", {"name": "Job"}))
        assert body["backup_job_id"] == "supervisor-job"
        assert body["verified"] is True and body["backup"]["name"] == "Job"
        assert body["backup"]["backup_id"] != "supervisor-job"


class TestListBackups:
    async def test_unreadable_location_is_reported_not_hidden(self, backup_env):
        _configure(backup_env["manager"], ["backup.local", "test.remote"])
        assert _body(await _call(backup_env, "create_backup", {}))["status"] == "completed"
        backup_env["remote"].fail_list = True
        body = _body(await _call(backup_env, "list_backups", {}))
        assert set(body["agent_errors"]) == {"test.remote"}
        assert any("test.remote" in w and "incomplete" in w for w in body["warnings"])
        assert body["total"] == 1 and body["backups"][0]["encrypted"] is True
        assert body["manager_state"] == "idle"

    async def test_clean_list_has_no_agent_errors(self, backup_env):
        body = _body(await _call(backup_env, "list_backups", {}))
        assert "agent_errors" not in body and "warnings" not in body


class TestBackupKeyNeverLeaves:
    """HA's backup key goes from its settings into the manager call and nowhere else."""

    async def test_key_absent_from_approval_result_audit_and_logs(self, backup_env, caplog):
        caplog.set_level(logging.DEBUG)
        _configure(backup_env["manager"], ["backup.local", "test.remote"])
        data = backup_env["data"]
        backup_env["key"].cap_backup = "confirm"
        # Through the MCP dispatcher, so the audit log and conversation log record it.
        pending = await mcp_view._dispatch_mcp(
            "tools/call", 1, {"name": "create_backup", "arguments": {"name": "Reviewed"}},
            backup_env["key"], backup_env["hass"], data, "127.0.0.1", "")
        assert pending[3] == "pending_approval", pending
        approval = data.store.get_pending_approvals()[0]
        approval_text = json.dumps(approval, default=str)
        # Positive control: the preview really is the resolved plan.
        assert '"encrypted": true' in approval_text and "test.remote" in approval_text
        assert approval["diff"]["preview"]["unencrypted_agent_ids"] == []
        result = await mcp_view.async_execute_approved_tool(
            "create_backup", approval["args"], backup_env["key"], backup_env["hass"], data)
        assert result[1] == "allowed" and _body(result)["encrypted"] is True
        assert data.audit.query() and data.conversations.query()["entries"]
        listed = await _call(backup_env, "list_backups", {})
        surfaces = {
            "approval": approval_text,
            "approval_args": json.dumps(approval["args"], default=str),
            "result": json.dumps(result, default=str),
            "pending": json.dumps(pending, default=str),
            "list": json.dumps(listed, default=str),
            "audit": json.dumps([entry.to_dict() for entry in data.audit.query() or []], default=str),
            "conversations": json.dumps([data.conversations.detail(row["id"]) for row in data.conversations.query()["entries"]], default=str),
            "logs": caplog.text,
        }
        for surface, text in surfaces.items():
            assert BACKUP_KEY not in text, surface

    async def test_key_absent_from_refusal_text(self, backup_env):
        _configure(backup_env["manager"], ["backup.local"])
        manager = backup_env["manager"]

        async def refuse(**kwargs):
            from homeassistant.components.backup import BackupManagerError
            raise BackupManagerError(f"bad password {kwargs['password']}")

        with patch.object(manager, "async_initiate_backup", side_effect=refuse):
            result = await _call(backup_env, "create_backup", {})
        text = json.dumps(result)
        assert result[1] == "invalid_request" and "Failed to create backup" in text
        assert BACKUP_KEY not in text

    async def test_key_absent_from_unexpected_failure(self, backup_env, caplog):
        caplog.set_level(logging.DEBUG)
        _configure(backup_env["manager"], ["backup.local"])
        manager = backup_env["manager"]

        async def crash(**kwargs):
            raise RuntimeError(f"crash {kwargs['password']}")

        with patch.object(manager, "async_initiate_backup", side_effect=crash):
            result = await _call(backup_env, "create_backup", {})
        assert result[1] == "invalid_request"
        assert BACKUP_KEY not in json.dumps(result) and BACKUP_KEY not in caplog.text
