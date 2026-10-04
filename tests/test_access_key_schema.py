"""Released credential schemas preserve authority and history during normalization."""

from copy import deepcopy
from datetime import timedelta
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_schema import normalize_access_key_fields
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.agent_recovery import AgentRecovery, RecoveryRequired
from custom_components.phoenix_mcp.approvals import async_cancel_approvals_for_access_key, REASON_REVOKED
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery, ConfigurationRecoveryError
from custom_components.phoenix_mcp.const import STORAGE_KEY, STORAGE_VERSION, AUDIT_STORAGE_VERSION, VERSION_STORAGE_VERSION
from custom_components.phoenix_mcp.version_store import VersionStore


async def test_v4_upgrade_preserves_secret_bindings_scope_and_pending_authority(hass, hass_storage):
    stamp = utcnow().isoformat()
    secret = 'phx_' + 'a' * 64
    digest = hashlib.sha256(secret.encode()).hexdigest()
    permission = {"entities": {"light.fixture": {"state": "GREEN", "hint": "Existing scope"}}}
    old_key = {"id": "existing", "name": "Existing", "token_hash": digest,
               "created_at": stamp, "created_by": "owner", "cap_physical_control": "confirm",
               "permissions": permission, "settings_version": 17}
    approval = {"id": "pending", "token_id": "existing", "token_name": "Existing",
                "tool_name": "call_service", "cap_name": "cap_physical_control", "status": "pending",
                "created_at": stamp, "expires_at": (utcnow() + timedelta(hours=1)).isoformat(),
                "household_request": True, "ha_user_id": "household-owner",
                "args": {"service_data": {"token_name": "authored-content"}}}
    archived = {**old_key, "id": "retired", "revoked": True, "revoked_at": stamp}
    hass_storage[STORAGE_KEY] = {"version": 4, "minor_version": 1, "key": STORAGE_KEY, "data": {
        "version": 4, "tokens": [old_key], "archived_tokens": [archived],
        "settings": {"assist_bound_token_id": "existing", "voice_agent_token_id": "existing",
                     "ai_task_token_id": "existing", "token_presets_enabled": True},
        "pending_approvals": [approval, {**approval, "id": "executing", "execution_started_at": stamp}],
    }}
    store = await AccessKeyStore.async_create(hass)
    key = store.get_access_key_by_hash(digest)
    assert key.id == "existing" and key.settings_version == 17
    assert key.cap_physical_control == "confirm"
    assert key.permissions.to_dict()["entities"] == permission["entities"]
    assert store.get_archived_by_hash(digest).id == "retired"
    settings = store.get_settings()
    assert settings.assist_bound_access_key_id == settings.voice_agent_access_key_id == settings.ai_task_access_key_id == key.id
    assert settings.access_key_presets_enabled
    pending, executing = store.get_pending_approvals()
    assert pending["status"] == "pending" and pending["ha_user_id"] == "household-owner"
    assert pending["args"] == approval["args"]
    assert executing["execution_started_at"] == stamp
    saved = hass_storage[STORAGE_KEY]
    assert saved["version"] == STORAGE_VERSION
    assert "tokens" not in saved["data"] and "archived_tokens" not in saved["data"]
    assert saved["data"]["access_keys"][0]["access_key_hash"] == digest
    assert "token_hash" not in saved["data"]["access_keys"][0]
    # Retiring the same key still closes its review; an execution claim retains uncertainty.
    async with store.async_lock:
        closed = await async_cancel_approvals_for_access_key(store, key.id, REASON_REVOKED)
    assert {r.id: r.rejected_reason for r in closed} == {
        "pending": "access_key_revoked", "executing": "execution_interrupted"}
    reloaded = await AccessKeyStore.async_create(hass)
    assert reloaded.get_access_key_by_hash(digest).id == key.id
    assert reloaded.get_pending_approvals()[0]["args"] == approval["args"]


async def test_released_audit_and_version_attribution_survives_load_and_save():
    stamp = utcnow().isoformat()
    old_audit = {"request_id": "request", "timestamp": stamp, "token_id": "admin",
                 "token_name": "admin:owner", "originating_token_id": "existing", "ha_user_id": "owner",
                 "method": "version/restore", "resource": "version:fixture", "outcome": "allowed", "client_ip": ""}
    disk = MagicMock(async_load=AsyncMock(return_value={"version": AUDIT_STORAGE_VERSION, "entries": [old_audit]}), async_save=AsyncMock())
    audit = AuditLog(store=disk)
    await audit.async_load()
    row = audit.query(access_key_id="admin")[0].to_dict()
    assert row["access_key_name"] == "admin:owner" and row["originating_access_key_id"] == "existing"
    assert "token_id" not in row
    await audit.async_save()
    assert disk.async_save.call_args.args[0]["entries"][0] == row
    authored = {"token_name": "authored-content"}
    old_version = {"id": "version", "resource_type": "automation", "resource_id": "fixture", "action": "edit",
                   "token_id": "existing", "token_name": "Historical name", "before": authored, "after": {}, "timestamp": stamp}
    disk.async_load.return_value = {"version": VERSION_STORAGE_VERSION, "versions": [old_version]}
    versions = VersionStore(disk)
    await versions.async_load()
    version = versions.get("version")
    assert version.access_key_id == "existing" and version.access_key_name == "Historical name"
    assert version.before == authored
    await versions.async_save()
    assert "token_name" not in disk.async_save.call_args.args[0]["versions"][0]


async def test_released_recovery_records_keep_both_write_barriers():
    stamp = utcnow().isoformat()
    disk = MagicMock(async_load=AsyncMock(return_value={"records": {
        "request": {"token_id": "existing", "tool": "call_service", "started_at": stamp}
    }}), async_save=AsyncMock())
    recovery = AgentRecovery(disk)
    await recovery.async_load()
    assert recovery.blocks("existing") and not recovery.blocks("different")
    with pytest.raises(RecoveryRequired):
        await recovery.begin("existing", "call_service", "duplicate")
    await recovery.begin("different", "call_service", "other")
    assert disk.async_save.call_args.args[0]["records"]["request"]["access_key_id"] == "existing"
    before = {"alias": "Existing config"}
    old = {"id": "config", "token_id": "existing", "resource": "automation:fixture", "operation": "edit",
           "phase": "unknown", "created_at": stamp, "before": before, "source": None, "expected": None}
    disk.async_load.return_value = {"records": {"config": deepcopy(old)}}
    configuration = ConfigurationRecovery(disk)
    await configuration.async_load()
    assert configuration.get("config")["access_key_id"] == "existing"
    assert configuration.get("config")["before"] == before
    with pytest.raises(ConfigurationRecoveryError):
        await configuration.begin("automation:fixture", "edit", "existing", {})
    await configuration.update("config", approval_id="review")
    saved = disk.async_save.call_args.args[0]["records"]["config"]
    assert saved["access_key_id"] == "existing" and "token_id" not in saved


def test_canonical_fields_win_and_normalization_does_not_traverse_authored_payloads():
    args = {"token_id": "authored", "token_name": "authored-name"}
    old = {"token_id": "old", "access_key_id": "current", "rejected_reason": "token_expired", "args": args}
    row = normalize_access_key_fields(old)
    assert row == {"access_key_id": "current", "rejected_reason": "access_key_expired", "args": args}
    assert old["token_id"] == "old" and old["rejected_reason"] == "token_expired"
