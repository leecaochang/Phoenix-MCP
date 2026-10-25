"""Readback and native approval integration for interrupted configuration writes."""

from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.admin_view import _approve_approval, _resolve_approval
from custom_components.phoenix_mcp.approvals import get_approval, async_update_approval_status
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecoveryError, runtime_identity
from custom_components.phoenix_mcp.config_reconciliation import (
    reconcile_configurations, observe_configuration, is_recovery_approval,
)
from custom_components.phoenix_mcp.const import DOMAIN, CONFIG_RECOVERY_UNRESOLVED
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.tools.blueprint import _blueprint_recovery_snapshot


@pytest.fixture
async def runtime(hass, hass_admin_user):
    # The automation integration registers the blueprint commands a restore uses,
    # and the reload that follows a blueprint save reads configuration.yaml.
    Path(hass.config.path("configuration.yaml")).write_text("{}\n", encoding="utf-8")
    assert await async_setup_component(hass, "automation", {})
    store = AccessKeyStore(hass, MagicMock(async_save=AsyncMock()))
    store.get_settings().mesa_mode = "off"
    value = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = value
    return value


_REL = "reconciliation/example.yaml"
_RESOURCE = f"blueprint:automation/{_REL}"


def blueprint(name: str) -> str:
    """Blueprint source in the form Home Assistant's blueprint/save writes back."""
    return f"blueprint:\n  name: {name}\n  domain: automation\n  input: {{}}\ntriggers: []\nactions: []\n"


SAVED, INTENDED, UNEXPECTED = blueprint("Saved"), blueprint("Intended"), blueprint("Unexpected")


def request():
    user = MagicMock(id="admin", is_admin=True)
    req = MagicMock()
    state = {KEY_HASS_USER: user, "phoenix_mcp_rid": "native-recovery-test"}
    req.__getitem__.side_effect = state.__getitem__
    return req


async def interrupted(hass, runtime, current=UNEXPECTED, *, expected=True, phase="unknown", leave=True):
    """An interrupted blueprint edit whose saved side is SAVED and intended side INTENDED."""
    path = Path(hass.config.path("blueprints", "automation", *_REL.split("/")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current, encoding="utf-8")
    before = _blueprint_recovery_snapshot("automation", _REL, SAVED)
    wanted = _blueprint_recovery_snapshot("automation", _REL, INTENDED) if expected else None
    rid = await runtime.config_recovery.begin(_RESOURCE, "edit_blueprint", "former-agent", before, expected=wanted)
    if phase != "prepared":
        await runtime.config_recovery.mark(rid, phase)
    if leave:
        runtime.config_recovery.leave(rid)
    return rid, path


async def test_matching_state_clears_without_command_or_notification(hass, runtime):
    rid, path = await interrupted(hass, runtime, INTENDED)
    with patch("custom_components.phoenix_mcp.approvals.create_approval_notification") as notify:
        await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "verified"
    assert runtime.config_recovery.get(rid)["observations"]["execution_attribution"] == "unproven"
    assert path.read_text() == INTENDED
    assert runtime.store.get_pending_approvals() == []
    notify.assert_not_called()
    next_id = await runtime.config_recovery.begin(_RESOURCE, "edit_blueprint", "t", {})
    runtime.config_recovery.leave(next_id)


async def test_native_approval_restores_saved_blueprint_and_dismisses_notification(hass, runtime):
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
        assert path.read_text() == SAVED
        assert runtime.config_recovery.get(rid)["phase"] == "reviewed"
        assert dismiss.called
        await _approve_approval(hass, request(), approval_id)
        assert path.read_text() == SAVED
        assert len(runtime.config_recovery.records()) == 2


async def test_reject_keeps_current_and_unblocks_using_native_endpoint(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    response = await _resolve_approval(hass, request(), approval_id, terminal_status="rejected", auto_reason=None)
    assert response.status == 200
    assert path.read_text() == UNEXPECTED
    assert runtime.config_recovery.get(rid)["decision"] == "keep_current"
    await reconcile_configurations(hass, runtime)
    assert len(runtime.store.get_pending_approvals()) == 1


async def test_changed_target_refuses_then_replaces_stale_approval(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    old_id = runtime.config_recovery.get(rid)["approval_id"]
    path.write_text(blueprint("Changed elsewhere"))
    response = await _approve_approval(hass, request(), old_id)
    assert response.status == 200
    assert get_approval(runtime.store, old_id).status == "failed"
    assert path.read_text() == blueprint("Changed elsewhere")
    assert runtime.config_recovery.get(rid)["phase"] in CONFIG_RECOVERY_UNRESOLVED
    await reconcile_configurations(hass, runtime)
    new_id = runtime.config_recovery.get(rid)["approval_id"]
    assert new_id != old_id
    path.write_text(blueprint("Changed again"))
    await reconcile_configurations(hass, runtime)
    assert get_approval(runtime.store, new_id).status == "cancelled"
    assert runtime.config_recovery.get(rid)["approval_id"] != new_id


async def test_readback_resolution_cancels_existing_native_request(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    path.write_text(INTENDED)
    with patch("homeassistant.components.persistent_notification.async_dismiss") as dismiss:
        await reconcile_configurations(hass, runtime)
        dismiss.assert_called_once()
    approval = get_approval(runtime.store, approval_id)
    assert approval.status == "cancelled"
    assert approval.result["configuration_reconciled"]


@pytest.mark.parametrize("phase", ["prepared", "dispatching", "partial", "unknown"])
async def test_active_operation_is_never_reconciled(hass, runtime, phase):
    rid, _ = await interrupted(hass, runtime, INTENDED, phase=phase, leave=False)
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == phase
    assert runtime.store.get_pending_approvals() == []


async def test_prepared_record_does_not_offer_a_restore(hass, runtime):
    rid, _ = await interrupted(hass, runtime, phase="prepared")
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "not_applied"
    assert runtime.store.get_pending_approvals() == []


async def test_legacy_record_without_intended_state_is_not_assumed_successful(hass, runtime):
    rid, _ = await interrupted(hass, runtime, SAVED, expected=False)
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(rid)["phase"] == "unknown"
    assert runtime.config_recovery.get(rid)["approval_id"]


def stored_file_snapshot(snapshot_type: str, rel: str, content: str) -> dict:
    """A configuration file snapshot in the stored form earlier releases wrote."""
    return {"snapshot_type": snapshot_type, "schema": 1, "resource_id": rel, "exists": True, "content": content}


@pytest.mark.parametrize("family", ["blueprint_source", "authored_file", "yaml_file"])
async def test_file_record_is_only_acknowledged_while_a_blueprint_is_restored(hass, runtime, family):
    """Configuration file text is never written back, even through administrator review.

    A stored file record has no observation adapter, so the sweep offers only an
    acknowledgement, and approving it leaves the file as it is. An interrupted
    blueprint record is offered and applied as a restore.
    """
    if family == "blueprint_source":
        rid, path = await interrupted(hass, runtime)
    else:
        rel = "packages/legacy.yaml" if family == "yaml_file" else "www/legacy.txt"
        path = Path(hass.config.path(rel))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("current: true\n", encoding="utf-8")
        rid = await runtime.config_recovery.begin(
            f"{family}:{rel}", "file_write", "former-agent",
            stored_file_snapshot(family, rel, "saved: true\n"),
            expected=stored_file_snapshot(family, rel, "intended: true\n"),
        )
        await runtime.config_recovery.mark(rid, "unknown")
        runtime.config_recovery.leave(rid)
        with pytest.raises(ConfigurationRecoveryError, match=r"^This configuration has no observation adapter\.$"):
            await observe_configuration(hass, runtime.config_recovery.get(rid))
    current = path.read_text()
    await reconcile_configurations(hass, runtime)
    approval = get_approval(runtime.store, runtime.config_recovery.get(rid)["approval_id"])
    restores = family == "blueprint_source"
    assert approval.args["_resolution"] == ("restore" if restores else "acknowledge")
    response = await _approve_approval(hass, request(), approval.id)
    assert response.status == 200, response.body
    assert runtime.config_recovery.get(rid)["phase"] == "reviewed"
    if restores:
        assert path.read_text() == SAVED
    else:
        assert path.read_text() == current
        assert runtime.config_recovery.get(rid)["decision"] == "acknowledged"
        assert len(runtime.config_recovery.records()) == 1


async def test_unreadable_source_is_unknown_not_absence(hass, runtime):
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
    assert path.read_text() == UNEXPECTED


async def test_system_approval_requires_durable_back_reference(hass, runtime):
    rid, path = await interrupted(hass, runtime)
    await reconcile_configurations(hass, runtime)
    approval_id = runtime.config_recovery.get(rid)["approval_id"]
    await runtime.config_recovery.update(rid, approval_id="some-other-request")
    assert not is_recovery_approval(get_approval(runtime.store, approval_id), runtime)
    response = await _approve_approval(hass, request(), approval_id)
    assert response.status == 404
    assert path.read_text() == UNEXPECTED


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
    assert path.read_text() == UNEXPECTED
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
    from custom_components.phoenix_mcp.mcp_view import _restore_access_key
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
        "settings": {"state": "{{ 8 }}", "unit_of_measurement": None}}, _restore_access_key("admin"), hass, runtime)
    assert not response[0].get("isError"), response
    record = runtime.config_recovery.records()[0]
    assert record["expected"] == template_snapshot(hass, entry)
    await runtime.config_recovery.mark(record["id"], "unknown")
    await reconcile_configurations(hass, runtime)
    assert runtime.config_recovery.get(record["id"])["phase"] == "verified"
