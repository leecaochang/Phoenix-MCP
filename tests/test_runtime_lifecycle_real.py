"""Real HA entry setup, reload, route ownership, unload and credential deletion."""

import asyncio
import copy
from datetime import timedelta
import logging

from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.llm.accounts import _get_secret_store
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore


@pytest.mark.parametrize("stored", [
    {"version": 1, "data": {"records": {"a": 1}}},
    {"version": 1, "data": {"records": ["x"]}},
    {"version": 1, "data": {"records": None}},
    {"version": 2, "data": {"records": []}},
])
async def test_unreadable_calendar_journal_does_not_abort_entry(hass, hass_storage, enable_custom_integrations, stored):
    key = "phoenix_mcp_calendar_writes"
    hass_storage[key] = copy.deepcopy(stored)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    data = hass.data[DOMAIN]
    assert data.ready and data.calendar_writes.unavailable
    with pytest.raises(ValueError, match="Calendar recovery storage is unavailable"):
        await data.calendar_writes.load()
    with pytest.raises(ValueError):
        await data.calendar_writes.save()
    assert hass_storage[key] == stored
    assert await hass.config_entries.async_unload(entry.entry_id)


def _mesa_profile(key: str, mode: str):
    from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile

    return SemanticProfile.from_dict(
        key, {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}},
        default_origin=MetadataOrigin.USER,
    )


async def _setup_runtime(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry, hass.data[DOMAIN]


async def test_reload_detaches_mesa_audit_handler(hass, enable_custom_integrations):
    """Entry-owned logging handlers must not accumulate across real reloads."""
    from custom_components.phoenix_mcp.mesa_audit import audit_logger, detach_mesa_audit_bridge

    baseline = set(audit_logger.handlers)
    entry = None
    try:
        entry, _ = await _setup_runtime(hass)
        assert len(set(audit_logger.handlers) - baseline) == 1
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert len(set(audit_logger.handlers) - baseline) == 1
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert set(audit_logger.handlers) == baseline
    finally:
        if entry is not None and entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
        for handler in set(audit_logger.handlers) - baseline:
            detach_mesa_audit_bridge(handler)


async def test_unload_drains_owned_decision_job(hass, enable_custom_integrations):
    """Unload must await cancellation before discarding the runtime owner."""
    entry, data = await _setup_runtime(hass)
    entered, finished = asyncio.Event(), asyncio.Event()

    async def provider_job():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            finished.set()

    job = hass.async_create_background_task(provider_job(), 'qualification_decision')
    data.decision_shadow.task = job
    try:
        await entered.wait()
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert job.cancelled() and finished.is_set()
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_persists_dirty_routing_statistics(hass, hass_storage, enable_custom_integrations):
    """An unsaved completed turn survives a real entry unload and reload."""
    from custom_components.phoenix_mcp.routing.routing_tables import ROUTING_STATISTICS_STORAGE_KEY
    from custom_components.phoenix_mcp.routing.routing_statistics import TurnStatistics

    entry, data = await _setup_runtime(hass)
    statistics = data.routing_statistics
    statistics.finish(TurnStatistics('chat', statistics.surfaces['chat']['generation'],
                                     route='local', completed=True))
    assert statistics.revision != statistics.saved_revision
    expected = statistics.summary()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass_storage[ROUTING_STATISTICS_STORAGE_KEY]['data'] == expected
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN].routing_statistics.summary() == expected
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_startup_archives_expired_access_keys_and_schedules_future_expiry(hass, enable_custom_integrations):
    """Startup enforces expiry without waiting for a request or interval sweep."""
    store = await AccessKeyStore.async_create(hass)
    expired, _ = await store.async_create_access_key('expired', 'admin',
                                               expires_at=utcnow() - timedelta(minutes=1))
    future, _ = await store.async_create_access_key('future', 'admin',
                                              expires_at=utcnow() + timedelta(minutes=5))
    entry, data = await _setup_runtime(hass)
    try:
        assert data.store.get_access_key_by_id(expired.id) is None
        assert any(access_key.id == expired.id for access_key in data.store.list_archived())
        assert data.store.get_access_key_by_id(future.id) is not None
        assert set(data.expiry_timers) == {future.id}
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_real_entry_reload_has_stable_routes_and_deletion_retires_credentials(hass, enable_custom_integrations, caplog):
    from homeassistant.components import conversation
    from homeassistant.helpers import llm

    entry, data = await _setup_runtime(hass)
    assert data.ready and not data.mesa_setup_failed
    access_key, _ = await data.store.async_create_access_key("lifecycle", "admin")
    secrets = await _get_secret_store(hass)
    account = await secrets.add("chatgpt", {"api_key": "synthetic-example-key", "model": "example"})
    assert len(secrets.list_instances()) == 1
    assert secrets.resolve(account).api_key == "synthetic-example-key"
    await data.store.async_patch_settings(assist_bound_access_key_id=access_key.id,
        voice_agent_enabled=True, voice_agent_access_key_id=access_key.id,
        voice_agent_provider_id=account, voice_agent_model="example",
        ai_task_enabled=True, ai_task_access_key_id=access_key.id,
        ai_task_provider_id=account, ai_task_model="example")
    resources = len(list(hass.http.app.router.resources()))
    watched = ("entity_registry_updated", "device_registry_updated", "area_registry_updated", "label_registry_updated", "automation_reloaded", "homeassistant_stop")
    expected_listeners = None
    for _ in range(3):
        previous = hass.data[DOMAIN]
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert len(list(hass.http.app.router.resources())) == resources
        assert hass.data[DOMAIN] is not previous
        assert hass.data[DOMAIN].store.get_access_key_by_id(access_key.id) is not None
        assert any(api.id == DOMAIN for api in llm.async_get_apis(hass))
        assert conversation.async_get_agent(hass, entry.entry_id) is not None
        counts = {event: hass.bus.async_listeners().get(event, 0) for event in watched}
        if expected_listeners is not None:
            assert counts == expected_listeners
        expected_listeners = counts
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data
    assert all(api.id != DOMAIN for api in llm.async_get_apis(hass))
    assert conversation.async_get_agent(hass, entry.entry_id) is None
    assert not any("UrlManager" in record.message or "setup rollback" in record.message or "Setup failed" in record.message for record in caplog.records if record.levelno >= 40)
    assert (await hass.config_entries.async_remove(entry.entry_id))["require_restart"] is False
    restored = await AccessKeyStore.async_create(hass)
    assert restored.get_access_key_by_id(access_key.id) is None
    assert restored.list_archived()[0].revoked
    settings = restored.get_settings()
    assert settings.assist_bound_access_key_id is None
    assert settings.voice_agent_access_key_id is None and not settings.voice_agent_enabled
    assert settings.ai_task_access_key_id is None and not settings.ai_task_enabled
    secrets = await _get_secret_store(hass)
    assert secrets.list_instances() == []
    assert secrets.resolve(account) is None


async def test_registry_changes_drop_the_cached_permission_tree(hass, enable_custom_integrations):
    """Entity, device, area and label changes each make the next tree request rebuild it."""
    from homeassistant.helpers import area_registry as ar, label_registry as lr

    entry, data = await _setup_runtime(hass)
    try:
        def change_area():
            ar.async_get(hass).async_create("Tree cache area")

        def change_label():
            lr.async_get(hass).async_create("Tree cache label")

        def fire(event):
            return lambda: hass.bus.async_fire(event, {"action": "update"})

        for change in (change_area, change_label, fire("entity_registry_updated"), fire("device_registry_updated")):
            data.entity_tree_cache = {}
            data.entity_tree_cache_valid = True
            change()
            await hass.async_block_till_done()
            assert data.entity_tree_cache_valid is False
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_idle_usage_ticks_do_not_write_and_changed_usage_flushes_once(hass, enable_custom_integrations, monkeypatch):
    from pytest_homeassistant.common import async_fire_time_changed
    from custom_components.phoenix_mcp.const import FLUSH_INTERVAL

    entry, data = await _setup_runtime(hass)
    access_key, _ = await data.store.async_create_access_key("flush", "admin")
    saves = []
    original = data.store.async_save

    async def save():
        saves.append(1)
        await original()

    monkeypatch.setattr(data.store, "async_save", save)
    now = utcnow()
    for index in range(1, 3):
        async_fire_time_changed(hass, now + FLUSH_INTERVAL * index + timedelta(seconds=1))
        await hass.async_block_till_done()
    assert saves == []
    data.store.update_last_used(access_key.id, now)
    async_fire_time_changed(hass, now + FLUSH_INTERVAL * 3 + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert saves == [1]
    async_fire_time_changed(hass, now + FLUSH_INTERVAL * 4 + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert saves == [1]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_flushes_usage_and_audit_added_during_platform_unload(hass, hass_storage, enable_custom_integrations, monkeypatch):
    entry, data = await _setup_runtime(hass)
    access_key, _ = await data.store.async_create_access_key("unload", "admin")
    original = hass.config_entries.async_unload_platforms
    stamp = utcnow() + timedelta(seconds=1)

    async def unload(*args, **kwargs):
        result = await original(*args, **kwargs)
        data.store.update_last_used(access_key.id, stamp)
        data.audit.record(request_id="last-unload-row", access_key_id=access_key.id, access_key_name=access_key.name,
            method="get_state", resource="fixture", outcome="allowed", client_ip="", settings=data.store.get_settings())
        return result

    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", unload)
    assert await hass.config_entries.async_unload(entry.entry_id)
    persisted = next(row for row in hass_storage[DOMAIN]["data"]["access_keys"] if row["id"] == access_key.id)
    assert persisted["last_used_at"] == stamp.isoformat()
    assert any(row["request_id"] == "last-unload-row" for row in hass_storage["phoenix_mcp_audit"]["data"]["entries"])


async def test_failed_platform_unload_persists_audit_and_keeps_runtime_available(hass, hass_storage, enable_custom_integrations, monkeypatch):
    """A refused platform unload still persists the audit log and leaves the runtime available."""
    entry, data = await _setup_runtime(hass)
    data.audit.record(request_id='failed-unload-row', access_key_id='fixture', access_key_name='fixture',
                      method='get_state', resource='fixture', outcome='allowed', client_ip='',
                      settings=data.store.get_settings())
    original = hass.config_entries.async_unload_platforms

    async def refuse(*args, **kwargs):
        return False

    monkeypatch.setattr(hass.config_entries, 'async_unload_platforms', refuse)
    try:
        assert not await hass.config_entries.async_unload(entry.entry_id)
        assert hass.data[DOMAIN] is data and data.ready and not data.shutting_down
        assert any(row['request_id'] == 'failed-unload-row'
                   for row in hass_storage['phoenix_mcp_audit']['data']['entries'])
    finally:
        monkeypatch.setattr(hass.config_entries, 'async_unload_platforms', original)
        # Reset the synthetic failure after restoring the real platform owner.
        # HA otherwise rejects further teardown of a FAILED_UNLOAD entry.
        entry._async_set_state(hass, ConfigEntryState.LOADED, None)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_stop_gates_real_http_admission_before_persistence(hass, enable_custom_integrations, hass_client_no_auth):
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    entry, data = await _setup_runtime(hass)
    _, raw = await data.store.async_create_access_key("stop", "admin")
    client = await hass_client_no_auth()
    headers = {"Authorization": "Bearer " + raw}
    assert (await client.get("/api/phoenix-mcp/context", headers=headers)).status == 200
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert not data.ready and data.shutting_down
    assert (await client.get("/api/phoenix-mcp/context", headers=headers)).status == 503
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_routes_left_behind_by_unload_refuse_with_503(hass, enable_custom_integrations, hass_client_no_auth):
    """aiohttp cannot remove routes, so the views outlive the runtime they served."""
    entry, data = await _setup_runtime(hass)
    _, raw = await data.store.async_create_access_key("unloaded", "admin")
    client = await hass_client_no_auth()
    headers = {"Authorization": "Bearer " + raw}
    assert (await client.get("/api/phoenix-mcp/context", headers=headers)).status == 200
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert DOMAIN not in hass.data
    for request_headers in (headers, {}):
        assert (await client.get("/api/phoenix-mcp/context", headers=request_headers)).status == 503


async def test_unload_releases_real_expiry_and_sensor_flush_timers(hass, enable_custom_integrations):
    from custom_components.phoenix_mcp.helpers import update_access_key_counter

    entry, data = await _setup_runtime(hass)
    access_key, _ = await data.store.async_create_access_key("timers", "admin", expires_at=utcnow() + timedelta(hours=1))
    await data.async_on_access_key_created(access_key)
    await hass.async_block_till_done()
    assert access_key.id in data.expiry_timers
    update_access_key_counter(data, access_key.id, "allowed")
    assert data.sensor_flush_cancel is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert data.expiry_timers == {}
    assert data.sensor_flush_cancel is None


async def test_startup_prunes_terminal_history_and_payloads(hass, enable_custom_integrations):
    from custom_components.phoenix_mcp.const import APPROVAL_HISTORY_LIMIT

    store = await AccessKeyStore.async_create(hass)
    old = (utcnow() - timedelta(hours=2)).isoformat()
    store.set_pending_approvals([
        {"id": str(index), "status": "approved", "resolved_at": old,
         "args": {"content": "private"}, "diff": {"before": "private"}, "result": "private"}
        for index in range(APPROVAL_HISTORY_LIMIT + 5)
    ])
    await store.async_save()
    entry, data = await _setup_runtime(hass)
    rows = data.store.get_pending_approvals()
    assert len(rows) == APPROVAL_HISTORY_LIMIT
    assert all(row["args"] == {} and row["diff"] == {} and row["result"] is None for row in rows)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_cancels_inflight_mesa_priming(hass, enable_custom_integrations, monkeypatch):
    from custom_components.phoenix_mcp import mesa

    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    completed = []

    async def import_profiles(*args):
        entered.set()
        try:
            await release.wait()
            completed.append(True)
            return 0
        finally:
            finished.set()

    monkeypatch.setattr(mesa, "async_import_sidecar_profiles", import_profiles)
    entry, _ = await _setup_runtime(hass)
    try:
        async with asyncio.timeout(3):
            await entered.wait()
        assert await hass.config_entries.async_unload(entry.entry_id)
        async with asyncio.timeout(3):
            await finished.wait()
        assert completed == []
    finally:
        release.set()


async def test_sweep_records_an_expired_approval_in_the_audit_log(hass, enable_custom_integrations):
    """Expiry found by the sweep is audited like expiry found at approve time."""
    from pytest_homeassistant.common import async_fire_time_changed
    from custom_components.phoenix_mcp.approvals import async_create_pending_approval
    from custom_components.phoenix_mcp.const import APPROVAL_SWEEP_INTERVAL

    entry, data = await _setup_runtime(hass)
    try:
        access_key, _ = await data.store.async_create_access_key("sweeper", "admin")
        async with data.store.async_lock:
            approval = await async_create_pending_approval(
                data.store, access_key_id=access_key.id, access_key_name=access_key.name,
                tool_name="restart_ha", cap_name="cap_restart", args={}, diff={}, request_id="rid-sweep",
            )
            rows = data.store.get_pending_approvals()
            for row in rows:
                if row["id"] == approval.id:
                    row["expires_at"] = (utcnow() - timedelta(minutes=1)).isoformat()
            data.store.set_pending_approvals(rows)
        async_fire_time_changed(hass, utcnow() + APPROVAL_SWEEP_INTERVAL + timedelta(seconds=1))
        # The interval job runs as a background task.
        await hass.async_block_till_done(wait_background_tasks=True)
        audited = [e for e in data.audit.query(limit=50) if e.method == "approval/expired"]
        assert [e.resource for e in audited] == [f"approval:restart_ha:{approval.id}"]
        assert audited[0].request_id == "rid-sweep" and audited[0].outcome == "denied"
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_entity_rename_carries_its_grant_over(hass, enable_custom_integrations):
    """Renaming an entity in Home Assistant keeps its per-key grant."""
    from homeassistant.helpers import entity_registry as er

    entry, data = await _setup_runtime(hass)
    try:
        registry = er.async_get(hass)
        entity = registry.async_get_or_create("light", "test", "rename-me", suggested_object_id="before")
        access_key, _ = await data.store.async_create_access_key("renamer", "admin")
        await data.store.async_patch_permission_node(access_key.id, "entities", entity.entity_id, "GREEN", hint="desk")
        earlier = await data.versions.record(
            resource_type="entity", resource_id="light.before", action="edit",
            before={"name": None}, after={"name": "Desk"},
        )
        registry.async_update_entity(entity.entity_id, new_entity_id="light.after")
        await hass.async_block_till_done(wait_background_tasks=True)
        nodes = data.store.get_access_key_by_id(access_key.id).permissions.entities
        assert "light.before" not in nodes
        assert (nodes["light.after"].state, nodes["light.after"].hint) == ("GREEN", "desk")
        # Its Changes history moves with it.
        assert [r.id for r in data.versions.list_for("entity", "light.after")] == [earlier.id]
        assert data.versions.list_for("entity", "light.before") == []
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_entity_rename_carries_its_mesa_profile(hass, enable_custom_integrations, hass_storage):
    """Protection follows the entity from the moment the registry event fires."""
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp.const import MESA_STORAGE_KEY

    entry, data = await _setup_runtime(hass)
    try:
        registry = er.async_get(hass)
        entity = registry.async_get_or_create("light", "test", "vault", suggested_object_id="vault")
        data.mesa.store.set(entity.entity_id, _mesa_profile(entity.entity_id, "prohibited"))
        await data.mesa.async_save()

        registry.async_update_entity(entity.entity_id, new_entity_id="light.vault2")
        # No await: nothing may run between the rename and the move.
        assert data.mesa.store.get("light.vault2") is not None
        assert data.mesa.store.get("light.vault") is None

        await hass.async_block_till_done(wait_background_tasks=True)
        stored = hass_storage[MESA_STORAGE_KEY]["data"]["profiles"]
        assert "light.vault2" in stored and "light.vault" not in stored
        assert data.mesa.orphans == []
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(("old_mode", "new_mode", "governs", "orphan"), [
    ("prohibited", "autonomous", "prohibited", "autonomous"),
    ("autonomous", "prohibited", "prohibited", "prohibited"),
    ("confirm", "confirm", "confirm", "confirm"),
])
async def test_a_rename_onto_a_profiled_id_keeps_both_and_never_loosens(
    hass, enable_custom_integrations, caplog, old_mode, new_mode, governs, orphan,
):
    from homeassistant.helpers import entity_registry as er

    entry, data = await _setup_runtime(hass)
    try:
        registry = er.async_get(hass)
        entity = registry.async_get_or_create("light", "test", "taken", suggested_object_id="before")
        data.mesa.store.set("light.before", _mesa_profile("light.before", old_mode))
        data.mesa.store.set("light.after", _mesa_profile("light.after", new_mode))
        await data.mesa.async_save()

        registry.async_update_entity(entity.entity_id, new_entity_id="light.after")
        await hass.async_block_till_done(wait_background_tasks=True)

        mode = lambda eid: data.mesa.store.get(eid).operational_boundaries.control_mode.value
        assert mode("light.after") == governs
        assert mode("light.before") == orphan
        assert data.mesa.orphans == ["light.before"]
        assert "already has a profile" in caplog.text
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_rename_whose_save_fails_keeps_the_entity_protected(
    hass, enable_custom_integrations, caplog,
):
    """A failed save returns memory to the durable set, which the move already updated."""
    from unittest.mock import patch

    from homeassistant.helpers import entity_registry as er
    from homeassistant.util.file import WriteError

    entry, data = await _setup_runtime(hass)
    try:
        registry = er.async_get(hass)
        entity = registry.async_get_or_create("light", "test", "held", suggested_object_id="held")
        data.mesa.store.set(entity.entity_id, _mesa_profile(entity.entity_id, "prohibited"))
        await data.mesa.async_save()
        with patch("homeassistant.helpers.storage.Store._async_write_data", side_effect=WriteError("full")):
            registry.async_update_entity(entity.entity_id, new_entity_id="light.held2")
            await hass.async_block_till_done(wait_background_tasks=True)
        assert data.mesa.store.get("light.held2") is not None
        assert "could not be saved" in caplog.text
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_corrupt_store_starts_the_integration_with_the_gates_closed(hass, enable_custom_integrations):
    """Reads and recovery stay up; state changes refuse, and the operator is told."""
    from pathlib import Path

    from custom_components.phoenix_mcp.const import MESA_STORAGE_KEY

    path = Path(hass.config.path(".storage", f"{MESA_STORAGE_KEY}.corrupt.2026-01-01T00:00:00+00:00"))
    path.parent.mkdir(exist_ok=True)
    path.write_text("not json")
    entry, data = await _setup_runtime(hass)
    try:
        assert data.mesa is None
        assert data.mesa_setup_failed is True
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_ends_leases_so_their_event_still_fires(hass, enable_custom_integrations):
    """A reload replaces the lease manager, so a lease must end before it goes."""
    entry, data = await _setup_runtime(hass)
    ended = []
    hass.bus.async_listen(f"{DOMAIN}_mesa_lease_expired", ended.append)
    entity = "light.leased"
    hass.states.async_set(entity, "on")
    granted = data.mesa.lease_manager.request([entity], 30, session_id="key", caller_id="key")
    assert granted.granted

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert [event.data["lease_id"] for event in ended] == [granted.lease_id]


async def test_reload_leaves_logger_handlers_where_it_found_them(hass, enable_custom_integrations):
    """Debug capture attaches only to a raised integration's loggers, and unload takes it off.

    Setup attaches nothing, and a reload drops the capture with the rest of Phoenix's runtime.
    """
    from custom_components.phoenix_mcp.debug_capture import async_detach_debug_capture, async_update_debug_capture

    names = ("homeassistant.components", "custom_components", "homeassistant.components.hue", "custom_components.hue")
    loggers = [logging.getLogger(name) for name in names]
    baseline = [list(logger.handlers) for logger in loggers]
    entry, _ = await _setup_runtime(hass)
    try:
        assert [list(logger.handlers) for logger in loggers] == baseline
        async_update_debug_capture(hass, "hue", "DEBUG")
        assert [len(logger.handlers) for logger in loggers] == [
            len(handlers) + (index >= 2) for index, handlers in enumerate(baseline)]
        for _cycle in range(2):
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert [list(logger.handlers) for logger in loggers] == baseline
        async_update_debug_capture(hass, "hue", "DEBUG")
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert [list(logger.handlers) for logger in loggers] == baseline
    finally:
        async_detach_debug_capture(hass)


async def test_setup_refuses_a_home_assistant_older_than_the_minimum(hass, enable_custom_integrations, caplog, monkeypatch):
    """A manual install skips HACS's version check; setup says why it stops."""
    from homeassistant import const as ha_const

    monkeypatch.setattr(ha_const, "__version__", "2025.1.0")
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="Phoenix MCP")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "needs Home Assistant 2026.7.0 or newer, and this is 2025.1.0" in caplog.text
    assert DOMAIN not in hass.data
