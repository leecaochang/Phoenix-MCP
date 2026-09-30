"""Readback and native approval integration for interrupted configuration writes."""

from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.admin_view import _approve_approval, _resolve_approval
from custom_components.phoenix_mcp.approvals import get_approval, async_update_approval_status
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError, runtime_identity
from custom_components.phoenix_mcp.config_reconciliation import (
    reconcile_configurations, observe_configuration, is_recovery_approval,
)
from custom_components.phoenix_mcp.const import DOMAIN, CONFIG_RECOVERY_UNRESOLVED
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.token_store import TokenStore
from custom_components.phoenix_mcp.tools.config_files import _authored_file_snapshot


@pytest.fixture
def runtime(hass):
    store = TokenStore(hass, MagicMock(async_save=AsyncMock()))
    value = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = value
    return value


def request():
    user = MagicMock(id="admin", is_admin=True)
    req = MagicMock()
    state = {KEY_HASS_USER: user, "phoenix_mcp_rid": "native-recovery-test"}
    req.__getitem__.side_effect = state.__getitem__
    return req


async def interrupted(hass, runtime, current="unexpected", *, expected=True, phase="unknown", leave=True):
    rel = "www/reconciliation.txt"
    path = Path(hass.config.path(rel))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current, encoding="utf-8")
    before = _authored_file_snapshot(rel, True, "saved")
    wanted = _authored_file_snapshot(rel, True, "intended") if expected else None
    rid = await runtime.config_recovery.begin(f"authored_file:{rel}", "write_file", "former-agent", before, expected=wanted)
    if phase != "prepared":
        await runtime.config_recovery.mark(rid, phase)
    if leave:
        runtime.config_recovery.leave(rid)
    return rid, path


async def test_matching_state_clears_without_command_or_notification(hass, runtime):
    rid, path = await interrupted(hass, runtime, "intended")
    with patch("custom_components.phoenix_mcp.approvals.create_approval_notification") as notify:
        await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "verified"
    assert runtime.config_recovery.get(rid)["observations"]["execution_attribution"] == "unproven"
    assert path.read_text() == "intended"
    assert runtime.store.get_pending_approvals() == []
    notify.assert_not_called()
    next_id = await runtime.config_recovery.begin("authored_file:www/reconciliation.txt", "write_file", "t", {})
    runtime.config_recovery.leave(next_id)


async def test_native_approval_restores_saved_file_and_dismisses_notification(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    with patch("homeassistant.components.persistent_notification.async_create") as notify, patch(
        "homeassistant.components.persistent_notification.async_dismiss"
    ) as dismiss:
        await reconcile_configurations(hass, runtime)
        await reconcile_configurations(hass, runtime)
        notify.assert_called_once()
        approval_id = runtime.config_recovery.get(rid)["approval_id"]
        assert f"/phoenix-mcp/approvals/{approval_id}" in notify.call_args.kwargs["message"]
        approval = get_approval(runtime.store, approval_id)
        assert is_recovery_approval(approval, runtime)
        assert approval.diff["summary_key"] == "diff.configuration_recovery.restore"
        response = await _approve_approval(hass, request(), approval_id)
        assert response.status == 200, response.body
        assert get_approval(runtime.store, approval_id).status == "approved", response.body
        assert path.read_text() == "saved"
        assert runtime.config_recovery.get(rid)["phase"] == "reviewed"
        assert dismiss.called
        await _approve_approval(hass, request(), approval_id)
        assert path.read_text() == "saved"
        assert len(runtime.config_recovery.records()) == 2


async def test_reject_keeps_current_and_unblocks_using_native_endpoint(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    response = await _resolve_approval(hass, request(), approval_id, terminal_status="rejected", auto_reason=None)
    assert response.status == 200
    assert path.read_text() == "unexpected"
    assert runtime.config_recovery.get(rid)["decision"] == "keep_current"
    await reconcile_configurations(hass, runtime)
    assert len(runtime.store.get_pending_approvals()) == 1


async def test_changed_target_refuses_then_replaces_stale_approval(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    old_id = runtime.config_recovery.get(rid)["approval_id"]
    path.write_text("changed by someone else")
    response = await _approve_approval(hass, request(), old_id)
    assert response.status == 200
    assert get_approval(runtime.store, old_id).status == "failed"
    assert path.read_text() == "changed by someone else"
    assert runtime.config_recovery.get(rid)["phase"] in CONFIG_RECOVERY_UNRESOLVED
    await reconcile_configurations(hass, runtime)
    new_id = runtime.config_recovery.get(rid)["approval_id"]
    assert new_id != old_id
    path.write_text("another change")
    await reconcile_configurations(hass, runtime)
    assert get_approval(runtime.store, new_id).status == "cancelled"
    assert runtime.config_recovery.get(rid)["approval_id"] != new_id


async def test_readback_resolution_cancels_existing_native_request(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    path.write_text("intended")
    with patch("homeassistant.components.persistent_notification.async_dismiss") as dismiss:
        await reconcile_configurations(hass, runtime)
        dismiss.assert_called_once()
    approval = get_approval(runtime.store, approval_id)
    assert approval.status == "cancelled"
    assert approval.result["configuration_reconciled"]


@pytest.mark.parametrize("phase", ["prepared", "dispatching", "partial", "unknown"])
async def test_active_operation_is_never_reconciled(hass, runtime, phase):
    rid, _ = await interrupted(hass, runtime, "intended", phase=phase, leave=False)
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == phase
    assert runtime.store.get_pending_approvals() == []


async def test_prepared_record_does_not_offer_a_restore(hass, runtime):
    rid, _ = await interrupted(hass, runtime, phase="prepared")
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "not_applied"
    assert runtime.store.get_pending_approvals() == []


async def test_legacy_record_without_intended_state_is_not_assumed_successful(hass, runtime):
    rid, _ = await interrupted(hass, runtime, "saved", expected=False)
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "unknown"
    assert runtime.config_recovery.get(rid)["approval_id"]


async def test_unreadable_file_is_unknown_not_absence(hass, runtime):
    rid, _ = await interrupted(hass, runtime)
    with patch("custom_components.phoenix_mcp.tool_common._read_text_capped", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            await observe_configuration(hass, runtime.config_recovery.get(rid))
        await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    approval = get_approval(runtime.store, approval_id)
    assert approval.args["_resolution"] == "acknowledge"
    assert runtime.config_recovery.get(rid)["phase"] == "unknown"
    await _resolve_approval(hass, request(), approval_id, terminal_status="rejected", auto_reason=None)
    assert runtime.config_recovery.get(rid)["phase"] == "unknown"


async def test_restart_receipt_resolves_only_after_runtime_change(hass, runtime):
    rid = await runtime.config_recovery.begin("ha:restart", "restart_ha", "t",
        {"snapshot_type": "restart_receipt", "runtime_identity": runtime_identity(hass)})
    await runtime.config_recovery.mark(rid, "dispatching")
    runtime.config_recovery.leave(rid)
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "dispatching"
    with patch("custom_components.phoenix_mcp.config_reconciliation.runtime_identity", return_value="new-runtime"):
        await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "verified"


async def test_expiry_and_cancel_do_not_release_configuration_barrier(hass, runtime):
    rid, _ = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    await async_update_approval_status(runtime.store, approval_id, status="expired")
    assert runtime.config_recovery.get(rid)["phase"] == "unknown"
    await reconcile_configurations(hass, runtime)
    new_id = runtime.config_recovery.get(rid)["approval_id"]
    assert new_id != approval_id
    await _resolve_approval(hass, request(), new_id, terminal_status="cancelled", auto_reason=None)
    assert runtime.config_recovery.get(rid)["phase"] == "unknown"


async def test_notifications_setting_and_crash_link_adoption(hass, runtime):
    rid, _ = await interrupted(hass, runtime)
    runtime.store.get_settings().notify_on_approval = False
    with patch("homeassistant.components.persistent_notification.async_create") as notify:
        await reconcile_configurations(hass, runtime)
        notify.assert_not_called()
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    await runtime.config_recovery.update(rid, approval_id=None)
    runtime.config_recovery.announced_approvals.clear()
    runtime.store.get_settings().notify_on_approval = True
    with patch("homeassistant.components.persistent_notification.async_create") as notify:
        await reconcile_configurations(hass, runtime)
        assert runtime.config_recovery.get(rid)["approval_id"] == approval_id
        notify.assert_called_once()
    assert len(runtime.store.get_pending_approvals()) == 1


async def test_durable_claim_failure_prevents_restore(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    runtime.store._store.async_save.side_effect = OSError
    response = await _approve_approval(hass, request(), runtime.config_recovery.get(rid)["approval_id"])
    assert response.status == 503
    assert path.read_text() == "unexpected"


async def test_system_approval_requires_durable_back_reference(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    await runtime.config_recovery.update(rid, approval_id="some-other-request")
    assert not is_recovery_approval(get_approval(runtime.store, approval_id), runtime)
    response = await _approve_approval(hass, request(), approval_id)
    assert response.status == 409
    assert path.read_text() == "unexpected"


async def test_retention_prunes_completed_but_never_unresolved(runtime):
    recovery = runtime.config_recovery
    old = await recovery.begin("one", "write", "t", {})
    await recovery.mark(old, "verified")
    recovery.leave(old)
    pending = await recovery.begin("two", "write", "t", {})
    await recovery.mark(pending, "unknown")
    recovery.leave(pending)
    for rid in (old, pending):
        await recovery.update(rid, created_at=(utcnow() - timedelta(days=31)).isoformat())
    await recovery.prune()
    assert recovery.get(old) is None
    assert recovery.get(pending) is not None
    with pytest.raises(ConfigurationRecoveryError):
        await recovery.begin("two", "write", "t", {})
    assert json.dumps(recovery.records())


async def test_runtime_authority_loss_during_readback_prevents_restore(hass, runtime):
    from custom_components.phoenix_mcp import config_reconciliation
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    real_observe = config_reconciliation.observe_configuration
    async def stop_during_read(*args):
        observed = await real_observe(*args)
        runtime.shutting_down = True
        return observed
    with patch.object(config_reconciliation, "observe_configuration", side_effect=stop_during_read):
        response = await _approve_approval(hass, request(), runtime.config_recovery.get(rid)["approval_id"])
    assert response.status == 200
    assert path.read_text() == "unexpected"
    assert runtime.config_recovery.get(rid)["phase"] == "unknown"


async def test_completed_same_resource_can_rotate_inside_write_lock(runtime, monkeypatch):
    from custom_components.phoenix_mcp import config_recovery
    monkeypatch.setattr(config_recovery, "CONFIG_RECOVERY_MAX_RECORDS", 1)
    recovery = runtime.config_recovery
    old = await recovery.begin("one", "write", "t", {})
    await recovery.mark(old, "verified")
    recovery.leave(old)
    async with recovery.resource("one"):
        new = await recovery.begin("one", "write", "t", {})
    assert recovery.get(old) is None
    assert recovery.get(new) is not None
    recovery.leave(new)


async def test_template_settings_expected_snapshot_matches_real_optional_clear(hass, runtime):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.setup import async_setup_component
    from custom_components.phoenix_mcp.mcp_view import _restore_token
    from custom_components.phoenix_mcp.tools.helper import _execute_set_config_entry_options
    from custom_components.phoenix_mcp.template_recovery import template_snapshot
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init("template", context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "sensor"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"],
        {"name": "Recovery reading", "state": "{{ 7 }}", "unit_of_measurement": "widgets"})
    entry = result["result"]
    await hass.async_block_till_done()
    response = await _execute_set_config_entry_options({"entry_id": entry.entry_id,
        "settings": {"state": "{{ 8 }}", "unit_of_measurement": None}}, _restore_token("admin"), hass, runtime)
    assert not response[0].get("isError"), response
    record = runtime.config_recovery.records()[0]
    assert record["expected"] == template_snapshot(hass, entry)
    await runtime.config_recovery.mark(record["id"], "unknown")
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(record["id"])["phase"] == "verified"
