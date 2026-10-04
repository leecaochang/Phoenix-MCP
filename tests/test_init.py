"""Integration setup and unload orchestration in __init__.py.

Covers async_setup_entry's wiring decisions, not the HA primitives it drives: the
kill-switch gate on client route registration (admin routes and panel always
register), MESA initialization failure keeping protected-write refusal, rollback
of a failed setup, one-shot listener lifecycle, and async_unload_entry cleaning
up hass.data even when a step fails. Real timers and background tasks are
neutralized so nothing is scheduled that would linger past a test.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.phoenix_mcp as phoenix_init
from custom_components.phoenix_mcp.const import DOMAIN, RUNTIME_READY_KEY


@pytest.fixture(autouse=True)
async def cleanup_direct_setup_entries(hass):
    """Run unload callbacks for entries set up outside HA's entry manager."""
    yield
    # Direct setup never marks these mock entries loaded, so HA's fixture cannot
    # unload them. In particular, their process-wide audit bridges must detach.
    for entry in hass.config_entries.async_entries(DOMAIN):
        await entry._async_process_on_unload(hass)


@pytest.mark.parametrize("exit_path", ["stop", "unload", "rollback"])
async def test_lifecycle_drains_policy_callbacks_before_runtime_disposal(hass, exit_path):
    """Shutdown must leave both HA execution bridges available to accepted work."""
    import threading

    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from custom_components.phoenix_mcp.mesa_core import ProfileStore, ashutdown_policy_workers
    from custom_components.phoenix_mcp.mesa_core.backends import MemoryBackend
    from custom_components.phoenix_mcp.mcp_view import PhoenixMcpView

    _, entry, _ = await _run_setup(hass)
    data = hass.data[DOMAIN]
    entered, release, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()

    async def bridge():
        entered.set()
        await release.wait()
        assert hass.data[DOMAIN] is data
        return await hass.async_add_executor_job(lambda: [])

    class BridgeBackend(MemoryBackend):
        def list_keys(self, prefix=None):
            return asyncio.run_coroutine_threadsafe(bridge(), loop).result(timeout=5)

    async def observed_shutdown():
        assert data.shutting_down and not data.ready
        assert hass.data[RUNTIME_READY_KEY] is False
        closing.set()
        await ashutdown_policy_workers()

    async def leave():
        if exit_path == "stop":
            hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
            await hass.async_block_till_done()
        elif exit_path == "unload":
            assert await phoenix_init.async_unload_entry(hass, entry)
        else:
            await phoenix_init._rollback_failed_setup(hass, entry, data)

    work = asyncio.create_task(ProfileStore(BridgeBackend()).aentity_keys())
    await asyncio.wait_for(entered.wait(), 5)
    with (patch("custom_components.phoenix_mcp.mesa_core.ashutdown_policy_workers", observed_shutdown),
          patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)),
          patch.object(phoenix_init, "_remove_frontend")):
        cleanup = asyncio.create_task(leave())
        try:
            await asyncio.wait_for(closing.wait(), 5)
            assert not cleanup.done()
            view = PhoenixMcpView()
            view.hass = hass
            assert (await view.post(MagicMock())).status == 503
        finally:
            release.set()
            assert await asyncio.wait_for(work, 5) == []
            await asyncio.wait_for(cleanup, 5)
    assert not any(t.name.startswith(("mesa-policy", "mesa-shutdown")) for t in threading.enumerate())
    # A later setup can use the same vendored package without a process restart.
    assert await ProfileStore(MemoryBackend()).aentity_keys() == []
    await ashutdown_policy_workers()


async def test_failed_unload_preserves_policy_workers(hass):
    """A failed unload leaves the integration active and must not drain its pool."""
    _, entry, _ = await _run_setup(hass)
    with (patch("custom_components.phoenix_mcp.mesa_core.ashutdown_policy_workers", AsyncMock()) as shutdown,
          patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False))):
        assert not await phoenix_init.async_unload_entry(hass, entry)
    shutdown.assert_not_awaited()
    assert hass.data[DOMAIN].ready and not hass.data[DOMAIN].shutting_down


def _settings(kill_switch: bool = False):
    return SimpleNamespace(
        kill_switch=kill_switch,
        audit_log_maxlen=1000,
        mesa_mode="off",
        audit_flush_interval=0,
        voice_agent_enabled=False,
    )


def _mock_store(kill_switch: bool = False) -> MagicMock:
    store = MagicMock()
    store.get_settings.return_value = _settings(kill_switch)
    store.list_tokens.return_value = []
    store.get_pending_approvals.return_value = []
    store.async_flush_last_used = AsyncMock()
    store.async_lock = asyncio.Lock()
    return store


def _fake_bg(coro, name=None):
    # Don't schedule background loops in the test; close the coroutine so there is
    # no "never awaited" warning, and hand back a cancel-able stand-in.
    coro.close()
    return MagicMock(cancel=MagicMock())


async def _run_setup(hass: HomeAssistant, *, kill_switch: bool = False, mesa_fail: bool = False, forward=None):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    hass.http = MagicMock()
    store = _mock_store(kill_switch)

    mesa = AsyncMock(side_effect=RuntimeError("boom")) if mesa_fail else AsyncMock(return_value=None)

    with patch.object(phoenix_init.TokenStore, "async_create", AsyncMock(return_value=store)), \
         patch("custom_components.phoenix_mcp.mesa.async_setup_mesa", mesa), \
         patch("custom_components.phoenix_mcp.panel.async_register_phoenix_panel", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.async_sync_mesa_inject", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.async_sync_agentchat_inject", AsyncMock()), \
         patch("custom_components.phoenix_mcp.ws_dispatch.check_ws_dispatch_compat", return_value=None), \
         patch.object(phoenix_init, "async_track_time_interval", MagicMock(return_value=MagicMock())), \
         patch.object(hass, "async_create_background_task", _fake_bg), \
         patch.object(hass.config_entries, "async_forward_entry_setups", forward or AsyncMock()):
        result = await phoenix_init.async_setup_entry(hass, entry)

    return result, entry, hass.http.register_view.call_count


@pytest.mark.parametrize("disabled", [False, True])
async def test_setup_publishes_ai_task_availability_after_ready(hass, phoenix_ai_task_platform, disabled):
    """Run real entity registration before setup publishes its final readiness."""
    from custom_components.phoenix_mcp import ai_task
    from custom_components.phoenix_mcp.token_store import GlobalSettings
    from homeassistant.helpers import entity_registry as er

    platform, _ = phoenix_ai_task_platform
    store = _mock_store()
    store.get_settings.return_value = GlobalSettings(
        ai_task_enabled=True, ai_task_token_id="t", ai_task_provider_id="p", ai_task_model="m",
    )

    async def forward(entry, platforms):
        if "ai_task" not in platforms:
            return
        platform.config_entry = entry
        if disabled:
            er.async_get(hass).async_get_or_create(
                "ai_task", DOMAIN, f"{entry.entry_id}_ai_task", config_entry=entry,
                disabled_by=er.RegistryEntryDisabler.USER,
            )
        assert hass.data[DOMAIN].ready is False
        await ai_task.async_setup_entry(hass, entry, platform._async_schedule_add_entities_for_entry)
        await hass.async_block_till_done()
        entity = hass.data[DOMAIN].ai_task_entity
        if not disabled:
            assert hass.states.get(entity.entity_id).state == "unavailable"

    with patch(__name__ + "._mock_store", return_value=store):
        result, _, _ = await _run_setup(hass, forward=AsyncMock(side_effect=forward))
    assert result is True
    entity = hass.data[DOMAIN].ai_task_entity
    if disabled:
        assert entity is None
        hass.data[DOMAIN].async_sync_ai_task()
        assert hass.data[DOMAIN].ai_task_entity is None
    else:
        assert hass.states.get(entity.entity_id).state == "unknown"


async def test_setup_registers_routes_when_kill_switch_off(hass: HomeAssistant):
    result, _entry, view_count = await _run_setup(hass, kill_switch=False)
    assert result is True
    data = hass.data[DOMAIN]
    assert data.routes_registered is True
    # Admin views plus the client views were all registered.
    from custom_components.phoenix_mcp.admin_view import ALL_ADMIN_VIEWS
    assert view_count > len(ALL_ADMIN_VIEWS)


async def test_optional_ai_task_refresh_failure_keeps_integration_ready(hass, caplog):
    refresh = MagicMock(side_effect=RuntimeError("refresh failed"))

    async def forward(entry, platforms):
        if "ai_task" in platforms:
            hass.data[DOMAIN].async_sync_ai_task = refresh

    result, _, _ = await _run_setup(hass, forward=AsyncMock(side_effect=forward))
    assert result is True
    assert hass.data[DOMAIN].ready is True
    assert hass.data[RUNTIME_READY_KEY] is True
    refresh.assert_called_once()
    assert "Could not refresh the optional AI Task entity" in caplog.text


async def test_setup_skips_client_routes_when_kill_switch_on(hass: HomeAssistant):
    result, _entry, view_count = await _run_setup(hass, kill_switch=True)
    assert result is True
    data = hass.data[DOMAIN]
    # Client routes are NOT registered, but the helper is wired for later re-enable.
    assert data.routes_registered is False
    assert callable(data.async_register_routes)
    # Admin views are still registered (kill switch never hides the admin surface).
    # This includes the agentCLI provider-config admin views, which are also
    # kill-switch-immune; only the agentCLI streaming chat view is gated.
    from custom_components.phoenix_mcp.admin_view import ALL_ADMIN_VIEWS
    from custom_components.phoenix_mcp.agentcli import ALL_AGENTCLI_ADMIN_VIEWS
    assert view_count == len(ALL_ADMIN_VIEWS) + len(ALL_AGENTCLI_ADMIN_VIEWS)


async def test_setup_degrades_when_mesa_fails(hass: HomeAssistant):
    result, _entry, _ = await _run_setup(hass, mesa_fail=True)
    assert result is True
    # MESA setup raising must not block reads or admin recovery, but protected
    # writes must be able to distinguish this from an explicit off mode.
    data = hass.data[DOMAIN]
    assert data.mesa is None
    assert data.mesa_setup_failed is True

    from homeassistant.helpers import issue_registry as ir

    assert ir.async_get(hass).async_get_issue(
        DOMAIN, "mesa_runtime_unavailable"
    ) is None


async def test_mandatory_platform_failure_rolls_back_every_published_surface(
    hass: HomeAssistant,
):
    """A late mandatory failure must leave permanent routes inert and retryable."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    hass.http = MagicMock()
    store = _mock_store()
    forward = AsyncMock(side_effect=RuntimeError("sensor setup failed"))

    with patch.object(phoenix_init.TokenStore, "async_create", AsyncMock(return_value=store)), \
         patch("custom_components.phoenix_mcp.mesa.async_setup_mesa", AsyncMock(return_value=None)), \
         patch("custom_components.phoenix_mcp.panel.async_register_phoenix_panel", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.async_sync_mesa_inject", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.async_sync_agentchat_inject", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel") as panel, \
         patch("custom_components.phoenix_mcp.panel.remove_mesa_inject") as mesa_inject, \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject") as chat_inject, \
         patch("custom_components.phoenix_mcp.ws_dispatch.check_ws_dispatch_compat", return_value=None), \
         patch.object(phoenix_init, "async_track_time_interval", MagicMock(return_value=MagicMock())), \
         patch.object(hass, "async_create_background_task", _fake_bg), \
         patch.object(hass.config_entries, "async_forward_entry_setups", forward), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)) as unload:
        with pytest.raises(RuntimeError, match="sensor setup failed"):
            await phoenix_init.async_setup_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert hass.data[RUNTIME_READY_KEY] is False
    panel.assert_called_once()
    mesa_inject.assert_called_once()
    chat_inject.assert_called_once()
    unload.assert_not_awaited()

    # HA cannot unregister aiohttp routes. They survive the rollback by design,
    # but the process-wide readiness marker makes them refuse every request.
    from custom_components.phoenix_mcp.mcp_view import PhoenixMcpView

    view = PhoenixMcpView()
    view.hass = hass
    response = await view.post(MagicMock())
    assert response.status == 503


async def test_setup_can_retry_after_mandatory_platform_failure(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    hass.http = MagicMock()
    store = _mock_store()
    forward = AsyncMock(side_effect=[RuntimeError("first attempt"), None])

    with patch.object(phoenix_init.TokenStore, "async_create", AsyncMock(return_value=store)), \
         patch("custom_components.phoenix_mcp.mesa.async_setup_mesa", AsyncMock(return_value=None)), \
         patch("custom_components.phoenix_mcp.panel.async_register_phoenix_panel", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.async_sync_mesa_inject", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.async_sync_agentchat_inject", AsyncMock()), \
         patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel"), \
         patch("custom_components.phoenix_mcp.panel.remove_mesa_inject"), \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject"), \
         patch("custom_components.phoenix_mcp.ws_dispatch.check_ws_dispatch_compat", return_value=None), \
         patch.object(phoenix_init, "async_track_time_interval", MagicMock(return_value=MagicMock())), \
         patch.object(hass, "async_create_background_task", _fake_bg), \
         patch.object(hass.config_entries, "async_forward_entry_setups", forward), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        with pytest.raises(RuntimeError, match="first attempt"):
            await phoenix_init.async_setup_entry(hass, entry)
        assert await phoenix_init.async_setup_entry(hass, entry) is True

    assert hass.data[DOMAIN].ready is True
    assert hass.data[RUNTIME_READY_KEY] is True


async def test_unload_removes_data(hass: HomeAssistant):
    await _run_setup(hass, kill_switch=False)
    assert DOMAIN in hass.data
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel"), \
         patch("custom_components.phoenix_mcp.panel.remove_mesa_inject"), \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject"), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        ok = await phoenix_init.async_unload_entry(hass, entry)

    assert ok is True
    assert DOMAIN not in hass.data


async def test_setup_reconciles_approvals_interrupted_by_the_last_shutdown(hass: HomeAssistant):
    """Startup must resolve approvals whose executor never reported back.

    An approval marked as executing and still pending may already have applied
    its side effect. Leaving it pending offers the admin a button that could apply
    a service call or config write a second time, which has no recovery. Setup
    reconciles first, then the initial expiry sweep reconciles again while
    excluding the live execution claims.
    """
    with patch(
        "custom_components.phoenix_mcp.approvals.async_reconcile_interrupted_approvals",
        AsyncMock(return_value=[]),
    ) as reconcile:
        await _run_setup(hass, kill_switch=False)

    data = hass.data[DOMAIN]
    assert reconcile.await_args_list == [
        call(data.store, skip_ids=None),
        call(data.store, skip_ids=data.approvals_in_progress),
    ]
    assert reconcile.await_args_list[1].kwargs["skip_ids"] is data.approvals_in_progress


async def test_external_alias_sentinel_loss_is_logged_without_repair(
    hass: HomeAssistant, caplog
):
    """Warn after an external update removes HA's computed-name sentinel."""
    from homeassistant.helpers import entity_registry as er

    await _run_setup(hass, kill_switch=False)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "light", "phoenix_test", "alias_watch", suggested_object_id="alias_watch"
    )
    assert entry.aliases == [er.COMPUTED_NAME]

    caplog.set_level("WARNING", logger="custom_components.phoenix_mcp")
    registry.async_update_entity(entry.entity_id, aliases=["old name"])
    await hass.async_block_till_done()

    assert registry.async_get(entry.entity_id).aliases == ["old name"]
    assert any(
        "removed Home Assistant's computed-name alias" in record.message
        for record in caplog.records
    )


async def test_alias_updates_without_sentinel_transition_are_not_warned(
    hass: HomeAssistant, caplog
):
    """Do not warn for unrelated updates or existing literal aliases."""
    from homeassistant.helpers import entity_registry as er

    await _run_setup(hass, kill_switch=False)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "light", "phoenix_test", "alias_watch_2", suggested_object_id="alias_watch_2"
    )
    registry.async_update_entity(entry.entity_id, aliases=["literal name"])
    caplog.set_level("WARNING", logger="custom_components.phoenix_mcp")
    caplog.clear()

    registry.async_update_entity(entry.entity_id, aliases=["another literal name"])
    registry.async_update_entity(entry.entity_id, name="Renamed")
    await hass.async_block_till_done()

    assert not any(
        "removed Home Assistant's computed-name alias" in record.message
        for record in caplog.records
    )


# --- one-shot event listeners ------------------------------------------------


async def test_a_fired_one_shot_listener_is_not_removed_again_on_unload(hass: HomeAssistant, caplog):
    """A one-shot listener that already fired is not removed again on unload.

    `hass.bus.async_listen_once` removes its own listener when the event fires,
    so passing its remove-callback to `entry.async_on_unload` asks HA to remove
    the same listener twice. The second call raises
    `ValueError: list.remove(x): x not in list`, which HA logs as "Unable to
    remove unknown job listener" with a Phoenix traceback.

    This only shows when the event has FIRED before the unload, as on a
    config-entry reload after a cold start. It applies to both
    `homeassistant_started` (suggestion priming) and `homeassistant_stop` (the
    final flush).
    """
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED

    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    seen: list[str] = []

    @callback
    def _listener(_event=None) -> None:
        seen.append("fired")

    phoenix_init._listen_once_until_unload(
        hass, entry, EVENT_HOMEASSISTANT_STARTED, _listener)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert seen == ["fired"]

    # Runs the on-unload callbacks, which is the mechanism under test.
    #
    # Asserted on the LOG, not on a raised exception: HA catches the ValueError
    # inside _async_remove_listener and logs it, so an exception-based assertion
    # would pass whether or not the double removal happens.
    caplog.clear()
    await entry._async_process_on_unload(hass)
    await hass.async_block_till_done()

    assert "Unable to remove unknown job listener" not in caplog.text, (
        "the already-fired listener was removed a second time on unload; this is "
        "the ERROR + Phoenix traceback seen in the operator's log on every reload"
    )


async def test_an_unfired_one_shot_listener_is_still_removed_on_unload(hass: HomeAssistant):
    # The other half: if the event never arrives, the listener must NOT be left
    # behind, or a reload would stack a second one and prime twice.
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED

    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    seen: list[str] = []

    @callback
    def _listener(_event=None) -> None:
        seen.append("fired")

    phoenix_init._listen_once_until_unload(
        hass, entry, EVENT_HOMEASSISTANT_STARTED, _listener)

    await entry._async_process_on_unload(hass)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert seen == [], "the listener outlived the entry that registered it"


async def test_a_coroutine_one_shot_listener_is_awaited(hass: HomeAssistant):
    # _on_stop is async. Wrapping it in a sync @callback would leave a coroutine
    # unawaited and silently skip the shutdown flush.
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    seen: list[str] = []

    async def _listener(_event=None) -> None:
        seen.append("awaited")

    phoenix_init._listen_once_until_unload(
        hass, entry, EVENT_HOMEASSISTANT_STOP, _listener)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert seen == ["awaited"]


# --- unload is failure-tolerant ---------------------------------------------
#
# Every step before the platform unload is best-effort. The failure these guard
# against is silent and total: shutting_down is set FIRST and helpers gates every
# token request on it, so an exception on the way out would leave the entry loaded
# with every route registered and every request answering 503 until the next
# restart.


async def test_unload_survives_an_unwritable_audit_store(hass: HomeAssistant):
    await _run_setup(hass, kill_switch=False)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    data = hass.data[DOMAIN]
    data.audit.async_save = AsyncMock(side_effect=OSError("disk full"))

    with patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel"), \
         patch("custom_components.phoenix_mcp.panel.remove_mesa_inject"), \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject"), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)) as unload:
        ok = await phoenix_init.async_unload_entry(hass, entry)

    assert ok is True
    # The teardown ran to completion rather than aborting at the save.
    assert unload.await_count == 1
    assert DOMAIN not in hass.data


async def test_unload_survives_a_failing_panel_removal(hass: HomeAssistant):
    await _run_setup(hass, kill_switch=False)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel",
               side_effect=RuntimeError("panel gone")), \
         patch("custom_components.phoenix_mcp.panel.remove_mesa_inject"), \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject"), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)) as unload:
        ok = await phoenix_init.async_unload_entry(hass, entry)

    assert ok is True
    assert unload.await_count == 1
    assert DOMAIN not in hass.data


async def test_one_failing_frontend_removal_does_not_skip_the_others(hass: HomeAssistant):
    # They are three unrelated removals: a failure in one must not skip the rest,
    # which would leave modules injected into every HA page with nothing left to
    # service them.
    await _run_setup(hass, kill_switch=False)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch("custom_components.phoenix_mcp.panel.remove_mesa_inject",
               side_effect=RuntimeError("stuck")) as mesa, \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject") as chat, \
         patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel") as panel, \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        assert await phoenix_init.async_unload_entry(hass, entry) is True

    mesa.assert_called_once()
    chat.assert_called_once()
    panel.assert_called_once()


async def test_a_failed_unload_keeps_the_frontend(hass: HomeAssistant):
    """HA keeps a failed unload LOADED, so its admin UI has to survive it.

    Removing the panel before the outcome is known would leave a running
    integration with no administrative surface, recoverable only by a later
    successful reload or a restart. The token routes stay live either way.
    """
    await _run_setup(hass, kill_switch=False)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel") as panel, \
         patch("custom_components.phoenix_mcp.panel.remove_mesa_inject") as mesa, \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject") as chat, \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert await phoenix_init.async_unload_entry(hass, entry) is False

    panel.assert_not_called()
    mesa.assert_not_called()
    chat.assert_not_called()
    assert hass.data[DOMAIN].shutting_down is False
    assert hass.data[DOMAIN].ready is True
    assert hass.data[RUNTIME_READY_KEY] is True


async def test_a_failed_unload_leaves_the_integration_usable(hass: HomeAssistant):
    # HA keeps a failed unload LOADED. Leaving shutting_down set would 503 every
    # token request against an integration Home Assistant still considers up.
    await _run_setup(hass, kill_switch=False)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch("custom_components.phoenix_mcp.panel.remove_phoenix_panel"), \
         patch("custom_components.phoenix_mcp.panel.remove_mesa_inject"), \
         patch("custom_components.phoenix_mcp.panel.remove_agentchat_inject"), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        ok = await phoenix_init.async_unload_entry(hass, entry)

    assert ok is False
    assert DOMAIN in hass.data
    assert hass.data[DOMAIN].shutting_down is False
    assert hass.data[DOMAIN].ready is True
    assert hass.data[RUNTIME_READY_KEY] is True


# --- startup compat probes (system_log shape, template env construction) -----


def test_system_log_probe_skips_when_absent(hass: HomeAssistant):
    # Absent = not loaded / not enabled / loads after Phoenix MCP. None of those are
    # drift, so the probe must stay silent rather than false-positive on order.
    from custom_components.phoenix_mcp.helpers import check_system_log_compat

    hass.data.pop("system_log", None)
    assert check_system_log_compat(hass) is None


def test_system_log_probe_passes_on_current_shape(hass: HomeAssistant):
    from custom_components.phoenix_mcp.helpers import check_system_log_compat

    hass.data["system_log"] = SimpleNamespace(records={})
    assert check_system_log_compat(hass) is None


def test_system_log_probe_reports_shape_drift(hass: HomeAssistant):
    from custom_components.phoenix_mcp.helpers import check_system_log_compat

    hass.data["system_log"] = SimpleNamespace(handler=object())  # no .records
    reason = check_system_log_compat(hass)
    assert reason is not None and "records" in reason

    hass.data["system_log"] = SimpleNamespace(records=[1, 2])  # not a mapping
    reason = check_system_log_compat(hass)
    assert reason is not None and "mapping" in reason


def test_template_sandbox_audit_warns_when_env_construction_fails(caplog, monkeypatch):
    # If HA's TemplateEnvironment changes shape, every render_template call will
    # fail; the audit must surface that once at startup as a warning, not debug.
    import custom_components.phoenix_mcp.helpers as helpers_mod

    monkeypatch.setattr(helpers_mod, "safe_template_env", MagicMock(side_effect=TypeError("shape")))
    phoenix_init._audit_template_sandbox()
    assert any(
        "template sandbox environment failed to construct" in r.message
        for r in caplog.records if r.levelname == "WARNING"
    )


async def test_setup_migrates_phoenix_repairs_without_discarding_receipts(hass):
    from homeassistant.helpers import issue_registry as ir
    from custom_components.phoenix_mcp.agent_recovery import AgentRecovery, STORAGE_KEY
    from custom_components.phoenix_mcp.durable_storage import DurableStore
    recovery = AgentRecovery(DurableStore(hass, 1, STORAGE_KEY, atomic_writes=True))
    await recovery.begin("token", "call_service", "legacy")
    for domain, issue_id in ((DOMAIN, "agent_recovery_legacy"), (DOMAIN, "calendar_recovery_old"),
                             (DOMAIN, "mesa_runtime_unavailable"), ("other", "agent_recovery_keep")):
        ir.async_create_issue(hass, domain, issue_id, is_fixable=False, severity=ir.IssueSeverity.ERROR,
                              translation_key="fixture")
    result, _, _ = await _run_setup(hass)
    assert result is True
    assert hass.data[DOMAIN].agent_recovery.blocks("token")
    assert hass.data[DOMAIN].agent_recovery.admin_records()[0]["request_id"] == "legacy"
    assert list(ir.async_get(hass).issues) == [("other", "agent_recovery_keep")]
