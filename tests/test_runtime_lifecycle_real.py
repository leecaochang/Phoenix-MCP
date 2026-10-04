"""Real HA entry setup, reload, route ownership, unload and credential deletion."""

import asyncio
import copy
from datetime import timedelta

from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.agentcli import _get_secret_store
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.token_store import TokenStore


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
    from custom_components.phoenix_mcp.const import ROUTING_STATISTICS_STORAGE_KEY
    from custom_components.phoenix_mcp.routing_statistics import TurnStatistics

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


async def test_startup_archives_expired_tokens_and_schedules_future_expiry(hass, enable_custom_integrations):
    """Startup enforces expiry without waiting for a request or interval sweep."""
    store = await TokenStore.async_create(hass)
    expired, _ = await store.async_create_token('expired', 'admin',
                                               expires_at=utcnow() - timedelta(minutes=1))
    future, _ = await store.async_create_token('future', 'admin',
                                              expires_at=utcnow() + timedelta(minutes=5))
    entry, data = await _setup_runtime(hass)
    try:
        assert data.store.get_token_by_id(expired.id) is None
        assert any(token.id == expired.id for token in data.store.list_archived())
        assert data.store.get_token_by_id(future.id) is not None
        assert set(data.expiry_timers) == {future.id}
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_real_entry_reload_has_stable_routes_and_deletion_retires_credentials(hass, enable_custom_integrations, caplog):
    from homeassistant.components import conversation
    from homeassistant.helpers import llm

    entry, data = await _setup_runtime(hass)
    assert data.ready and not data.mesa_setup_failed and data.enforce_mcp_lifecycle
    token, _ = await data.store.async_create_token("lifecycle", "admin")
    secrets = await _get_secret_store(hass)
    account = await secrets.add("chatgpt", {"api_key": "synthetic-example-key", "model": "example"})
    assert len(secrets.list_instances()) == 1
    assert secrets.resolve(account).api_key == "synthetic-example-key"
    await data.store.async_patch_settings(assist_bound_token_id=token.id,
        voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id=account, voice_agent_model="example",
        ai_task_enabled=True, ai_task_token_id=token.id,
        ai_task_provider_id=account, ai_task_model="example")
    resources = len(list(hass.http.app.router.resources()))
    watched = ("entity_registry_updated", "device_registry_updated", "area_registry_updated", "automation_reloaded", "homeassistant_stop")
    expected_listeners = None
    for _ in range(3):
        previous = hass.data[DOMAIN]
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert len(list(hass.http.app.router.resources())) == resources
        assert hass.data[DOMAIN] is not previous
        assert hass.data[DOMAIN].store.get_token_by_id(token.id) is not None
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
    restored = await TokenStore.async_create(hass)
    assert restored.get_token_by_id(token.id) is None
    assert restored.list_archived()[0].revoked
    settings = restored.get_settings()
    assert settings.assist_bound_token_id is None
    assert settings.voice_agent_token_id is None and not settings.voice_agent_enabled
    assert settings.ai_task_token_id is None and not settings.ai_task_enabled
    secrets = await _get_secret_store(hass)
    assert secrets.list_instances() == []
    assert secrets.resolve(account) is None


async def test_idle_usage_ticks_do_not_write_and_changed_usage_flushes_once(hass, enable_custom_integrations, monkeypatch):
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from custom_components.phoenix_mcp.const import FLUSH_INTERVAL

    entry, data = await _setup_runtime(hass)
    token, _ = await data.store.async_create_token("flush", "admin")
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
    data.store.update_last_used(token.id, now)
    async_fire_time_changed(hass, now + FLUSH_INTERVAL * 3 + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert saves == [1]
    async_fire_time_changed(hass, now + FLUSH_INTERVAL * 4 + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert saves == [1]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_flushes_usage_and_audit_added_during_platform_unload(hass, hass_storage, enable_custom_integrations, monkeypatch):
    entry, data = await _setup_runtime(hass)
    token, _ = await data.store.async_create_token("unload", "admin")
    original = hass.config_entries.async_unload_platforms
    stamp = utcnow() + timedelta(seconds=1)

    async def unload(*args, **kwargs):
        result = await original(*args, **kwargs)
        data.store.update_last_used(token.id, stamp)
        data.audit.record(request_id="last-unload-row", token_id=token.id, token_name=token.name,
            method="get_state", resource="fixture", outcome="allowed", client_ip="", settings=data.store.get_settings())
        return result

    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", unload)
    assert await hass.config_entries.async_unload(entry.entry_id)
    persisted = next(row for row in hass_storage[DOMAIN]["data"]["tokens"] if row["id"] == token.id)
    assert persisted["last_used_at"] == stamp.isoformat()
    assert any(row["request_id"] == "last-unload-row" for row in hass_storage["phoenix_mcp_audit"]["data"]["entries"])


async def test_failed_platform_unload_persists_audit_and_keeps_runtime_available(hass, hass_storage, enable_custom_integrations, monkeypatch):
    """A refused platform unload does not reach the final successful-unload save."""
    entry, data = await _setup_runtime(hass)
    data.audit.record(request_id='failed-unload-row', token_id='fixture', token_name='fixture',
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
    _, raw = await data.store.async_create_token("stop", "admin")
    client = await hass_client_no_auth()
    headers = {"Authorization": "Bearer " + raw}
    assert (await client.get("/api/phoenix-mcp/context", headers=headers)).status == 200
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert not data.ready and data.shutting_down
    assert (await client.get("/api/phoenix-mcp/context", headers=headers)).status == 503
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_releases_real_expiry_and_sensor_flush_timers(hass, enable_custom_integrations):
    from custom_components.phoenix_mcp.helpers import update_token_counter

    entry, data = await _setup_runtime(hass)
    token, _ = await data.store.async_create_token("timers", "admin", expires_at=utcnow() + timedelta(hours=1))
    await data.async_on_token_created(token)
    await hass.async_block_till_done()
    assert token.id in data.expiry_timers
    update_token_counter(data, token.id, "allowed")
    assert data.sensor_flush_cancel is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert data.expiry_timers == {}
    assert data.sensor_flush_cancel is None


async def test_startup_prunes_terminal_history_and_payloads(hass, enable_custom_integrations):
    from custom_components.phoenix_mcp.const import APPROVAL_HISTORY_LIMIT

    store = await TokenStore.async_create(hass)
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
