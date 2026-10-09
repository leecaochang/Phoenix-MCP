"""Local Voice execution uses real dispatch and preserves refusal and recovery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli, mcp_view, voice_agent
from custom_components.phoenix_mcp.access_key_schema import normalize_access_key_root
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.command_execution import async_execute_local_command
from custom_components.phoenix_mcp.command_routing import RoutingAuthority, async_evaluate_command
from custom_components.phoenix_mcp.const import DOMAIN, COMMAND_ROUTING_PLAN_KEY
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.access_key_store import PermissionTree, PermissionNode


@pytest.fixture
async def local_home(hass, access_key_store, immediate_command_observation):
    access_key, _ = await access_key_store.async_create_access_key("local", "test", rate_limit_requests=0)
    access_key.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    access_key.cap_physical_control = "allow"
    data = PhoenixData(store=access_key_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    await access_key_store.async_patch_settings(voice_agent_enabled=True, voice_agent_access_key_id=access_key.id,
        voice_agent_provider_id="p", voice_agent_model="model", voice_agent_routing_mode="local", mesa_mode="off",
        command_routing_timeout_ms=5000)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", "desk", original_name="Desk lamp")
    registry.async_update_entity(entry.entity_id, aliases={"Desk lamp"})
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Desk lamp", "supported_color_modes": ["brightness"]})
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    return data, access_key, entry.entity_id


async def run_voice(hass, data, sentence, **kwargs):
    store = MagicMock()
    store.resolve.return_value = SimpleNamespace(model="model")
    with (patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=store)),
          patch.object(agentcli, "build_provider", return_value=MagicMock()) as build,
          patch.object(agentcli, "async_run_voice_turn", AsyncMock(return_value="Provider answer")) as provider):
        answer = await voice_agent.async_voice_answer(hass, data, sentence, **kwargs)
    return answer, build, provider


@pytest.mark.parametrize("sentence,service,extra", [
    ("turn on the desk lamp", "turn_on", {}),
    ("turn off the desk lamp", "turn_off", {}),
    ("set the desk lamp brightness to 50 percent", "turn_on", {"brightness_pct": 50.0}),
])
@pytest.mark.parametrize("routing_mode", ["local", "auto"])
async def test_local_voice_dispatches_exactly_once_without_model(hass, local_home, sentence, service, extra, routing_mode):
    await local_home[0].store.async_patch_settings(voice_agent_routing_mode=routing_mode)
    data, access_key, entity_id = local_home
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, build, provider = await run_voice(hass, data, sentence, language="en")
    assert answer == ("Home Assistant reports the requested state." if service == "turn_off" else
                      "Command sent. Home Assistant has not yet confirmed the requested state.")
    call.assert_awaited_once_with("light", service, {"entity_id": [entity_id], **extra}, blocking=True, return_response=False)
    build.assert_not_called()
    provider.assert_not_called()
    assert data.agent_recovery.records(access_key.id) == []


async def test_local_state_read_and_provider_fallback(hass, local_home):
    data, _, _ = local_home
    answer, build, _ = await run_voice(hass, data, "what is the state of the desk lamp", language="en")
    assert answer == "The state is Off."
    build.assert_not_called()
    answer, _, provider = await run_voice(hass, data, "why is the desk lamp off", language="en")
    assert answer == "Provider answer"
    assert provider.await_args.args[6] == "why is the desk lamp off"


@pytest.mark.parametrize("language", ["fr", "zh", "es"])
async def test_non_english_keeps_provider_path(hass, local_home, language):
    data, _, _ = local_home
    with patch("custom_components.phoenix_mcp.command_routing.async_recognize") as recognize:
        answer, _, provider = await run_voice(hass, data, "turn on the desk lamp", language=language)
    assert answer == "Provider answer"
    recognize.assert_not_called()
    provider.assert_awaited_once()


async def enable_mesa(hass, data, entity_id, mode):
    await data.store.async_patch_settings(mesa_mode="enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    data.mesa.store.set(entity_id, SemanticProfile.from_dict(entity_id,
        {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}}, default_origin=MetadataOrigin.USER))


@pytest.mark.parametrize("mode", ["prohibited", "confirm", "read_only"])
async def test_mesa_checks_apply_to_local_commands(hass, local_home, mode):
    data, access_key, entity_id = local_home
    await enable_mesa(hass, data, entity_id, mode)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, build, _ = await run_voice(hass, data, "turn on the desk lamp", language="en")
    call.assert_not_called()
    build.assert_not_called()
    if mode == "confirm":
        assert "needs approval" in answer
        assert COMMAND_ROUTING_PLAN_KEY in data.store.get_pending_approvals()[0]["args"]
    else:
        assert "could not complete" in answer


@pytest.mark.parametrize("legacy_proof", [False, True])
@pytest.mark.parametrize("change", ["identity", "unavailable", "brightness", "settings", "scope", "version", "settings_digest", "proof_conflict"])
async def test_saved_approval_rejects_changed_targets(hass, local_home, change, legacy_proof):
    data, access_key, entity_id = local_home
    await enable_mesa(hass, data, entity_id, "confirm")
    with patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])):
        answer, _, _ = await run_voice(hass, data, "set the desk lamp brightness to 50 percent", language="en")
    assert "needs approval" in answer
    record = data.store.get_pending_approvals()[0]
    if legacy_proof:
        plan = record["args"][COMMAND_ROUTING_PLAN_KEY]
        plan["token_version"] = plan.pop("access_key_version")
        record = normalize_access_key_root({"pending_approvals": [record]})[0]["pending_approvals"][0]
    if change == "identity":
        registry = er.async_get(hass)
        registry.async_remove(entity_id)
        registry.async_get_or_create("light", "test", "replacement", suggested_object_id=entity_id.split(".")[1])
    elif change == "unavailable":
        hass.states.async_set(entity_id, "unavailable")
    elif change == "brightness":
        hass.states.async_set(entity_id, "off", {"friendly_name": "Desk lamp", "supported_color_modes": ["onoff"]})
    elif change == "settings":
        await data.store.async_patch_settings(voice_agent_routing_mode="off")
    elif change == "version":
        access_key.settings_version += 1
    elif change == "settings_digest":
        await data.store.async_patch_settings(command_routing_timeout_ms=501)
    elif change == "proof_conflict":
        record["args"][COMMAND_ROUTING_PLAN_KEY].update(
            token_version=access_key.settings_version, access_key_version=None)
        record = normalize_access_key_root({"pending_approvals": [record]})[0]["pending_approvals"][0]
    else:
        access_key.permissions = PermissionTree()
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        result, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], record["args"], access_key, hass, data)
    assert outcome == "denied"
    assert result["isError"] is True
    call.assert_not_called()


@pytest.mark.parametrize("legacy_proof", [False, True])
async def test_unchanged_approval_uses_existing_executor(hass, local_home, legacy_proof):
    data, access_key, entity_id = local_home
    await enable_mesa(hass, data, entity_id, "confirm")
    with patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])):
        await run_voice(hass, data, "turn on the desk lamp", language="en")
    record = data.store.get_pending_approvals()[0]
    plan = record["args"][COMMAND_ROUTING_PLAN_KEY]
    assert "access_key_version" in plan and "token_version" not in plan
    if legacy_proof:
        plan["token_version"] = plan.pop("access_key_version")
        record = normalize_access_key_root({"pending_approvals": [record]})[0]["pending_approvals"][0]
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], record["args"], access_key, hass, data)
    assert outcome == "allowed"
    assert call.await_args.args[2] == {"entity_id": [entity_id]}


@pytest.mark.parametrize("change", ["identity", "settings", "exposure"])
async def test_changes_during_service_preparation_refuse_effects(hass, local_home, change):
    data, access_key, entity_id = local_home
    async def preparation(*args):
        if change == "settings":
            await data.store.async_patch_settings(voice_agent_routing_mode="off")
        elif change == "exposure":
            from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
            async_expose_entity(hass, "conversation", entity_id, False)
        else:
            er.async_get(hass).async_update_entity(entity_id, name="Replacement")
        return []
    with (patch.object(mcp_view, "unsupported_secondary_targets", preparation),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, build, provider = await run_voice(hass, data, "turn on the desk lamp", language="en")
    call.assert_not_called()
    build.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
async def test_interrupted_local_write_keeps_recovery_barrier(hass, local_home, failure):
    data, access_key, _ = local_home
    error = asyncio.TimeoutError() if failure == "timeout" else asyncio.CancelledError()
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", AsyncMock(side_effect=error)) as call):
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await run_voice(hass, data, "turn on the desk lamp", language="en")
        else:
            answer, build, _ = await run_voice(hass, data, "turn on the desk lamp", language="en")
            assert "This action may have run" in answer
            build.assert_not_called()
    call.assert_awaited_once()
    assert data.agent_recovery.blocks(access_key.id)


async def test_sentence_trigger_collision_only_matches_without_callbacks(hass, local_home):
    from homeassistant.components.conversation.agent_manager import get_agent_manager
    data, access_key, _ = local_home
    callback = AsyncMock()
    remove = get_agent_manager(hass).register_trigger(["turn on the desk lamp"], callback)
    try:
        observation = await async_evaluate_command(hass, data, access_key, "turn on the desk lamp")
        assert observation["reason"] == "sentence_trigger"
        answer, _, provider = await run_voice(hass, data, "turn on the desk lamp", language="en")
        assert answer == "Provider answer"
        provider.assert_awaited_once()
        callback.assert_not_called()
    finally:
        remove()


async def test_external_tool_cannot_supply_private_local_plan(hass, local_home):
    data, access_key, entity_id = local_home
    args = {"service": {"domain": "light", "name": "turn_on"},
            "targets": [{"kind": "entity", "ids": [entity_id]}], COMMAND_ROUTING_PLAN_KEY: {}}
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view._tool_call_service(args, access_key, hass, data)
    # Refused as an unknown argument before the dedicated private-plan guard.
    assert outcome == "invalid_request"
    call.assert_not_called()


async def test_unregistered_target_returns_to_provider_before_dispatch(hass, local_home):
    data, access_key, _ = local_home
    hass.states.async_set("light.unregistered", "off")
    proposal = {"intent": "HassTurnOn", "entity_ids": ["light.unregistered"]}
    result = await async_execute_local_command(hass, data, access_key, proposal, RoutingAuthority.capture(data, access_key), "")
    assert result is None


async def test_mesa_wait_cannot_change_targets_before_effect(hass, local_home):
    data, _, entity_id = local_home
    apply = mcp_view.async_apply_mesa_to_call
    async def changed(*args, **kwargs):
        result = await apply(*args, **kwargs)
        er.async_get(hass).async_update_entity(entity_id, name="Different target")
        return result
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(mcp_view, "async_apply_mesa_to_call", changed),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, build, _ = await run_voice(hass, data, "turn on the desk lamp", language="en")
    assert "could not complete" in answer
    call.assert_not_called()
    build.assert_not_called()


async def test_pass_through_does_not_bypass_mesa_or_allow_partial_area_actions(hass, local_home):
    from homeassistant.helpers import area_registry as ar
    data, access_key, entity_id = local_home
    access_key.pass_through = True
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    registry.async_update_entity(entity_id, area_id=area.id)
    other = registry.async_get_or_create("light", "test", "other", original_name="Other lamp")
    registry.async_update_entity(other.entity_id, area_id=area.id)
    hass.states.async_set(other.entity_id, "off", {"supported_color_modes": ["brightness"]})
    await enable_mesa(hass, data, other.entity_id, "prohibited")
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, build, _ = await run_voice(hass, data, "turn on the kitchen lights", language="en")
    assert "could not complete" in answer
    call.assert_not_called()
    build.assert_not_called()


async def test_failed_write_blocks_second_local_attempt_without_provider_replay(hass, local_home):
    data, access_key, _ = local_home
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", AsyncMock(side_effect=RuntimeError("uncertain"))) as call):
        first, _, _ = await run_voice(hass, data, "turn on the desk lamp", language="en")
        second, build, provider = await run_voice(hass, data, "turn on the desk lamp", language="en")
    assert "This action may have run" in first and "This action may have run" in second
    assert data.agent_recovery.blocks(access_key.id)
    call.assert_awaited_once()
    build.assert_not_called()
    provider.assert_not_called()


async def test_approval_rechecks_current_mesa_restrictions(hass, local_home):
    data, access_key, entity_id = local_home
    await enable_mesa(hass, data, entity_id, "confirm")
    with patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])):
        await run_voice(hass, data, "turn on the desk lamp", language="en")
    record = data.store.get_pending_approvals()[0]
    data.mesa.store.set(entity_id, SemanticProfile.from_dict(entity_id,
        {"semantic_profile": {"operational_boundaries": {"control_mode": "prohibited"}}}, default_origin=MetadataOrigin.USER))
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], record["args"], access_key, hass, data)
    assert outcome == "denied"
    call.assert_not_called()


@pytest.mark.parametrize("language,selection,alias,sentence,reply", [
    ("zh-Hans", "zh-Hans", "台灯", "打开台灯", "命令已发送。Home Assistant 尚未确认请求的状态。"),
    ("zh-CN", "zh-Hans", "台灯", "打开台灯", "命令已发送。Home Assistant 尚未确认请求的状态。"),
    ("es-ES", "es", "Lámpara", "enciende la lámpara", "Comando enviado. Home Assistant aún no ha confirmado el estado solicitado."),
])
async def test_enabled_language_runs_real_local_voice_once(hass, local_home, language, selection, alias, sentence, reply):
    data, _, entity_id = local_home
    await data.store.async_patch_settings(command_routing_languages=[selection])
    er.async_get(hass).async_update_entity(entity_id, aliases={alias})
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, build, provider = await run_voice(hass, data, sentence, language=language)
    assert answer == reply
    call.assert_awaited_once_with("light", "turn_on", {"entity_id": [entity_id]}, blocking=True, return_response=False)
    build.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize("language,alias,sentence,reply", [
    ("zh-Hans", "台灯", "打开台灯", "此指令需要在 Phoenix MCP 面板中审批。"),
    ("es", "Lámpara", "enciende la lámpara", "Esta orden requiere aprobación en el panel de Phoenix MCP."),
])
@pytest.mark.parametrize("remove_language", [False, True])
async def test_multilingual_voice_approval_rechecks_language_selection(hass, local_home, language, alias, sentence, reply, remove_language):
    data, access_key, entity_id = local_home
    await data.store.async_patch_settings(command_routing_languages=[language])
    er.async_get(hass).async_update_entity(entity_id, aliases={alias})
    await enable_mesa(hass, data, entity_id, "confirm")
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, _, provider = await run_voice(hass, data, sentence, language=language)
        assert answer == reply
        provider.assert_not_called()
        call.assert_not_called()
        record = data.store.get_pending_approvals()[0]
        if remove_language:
            await data.store.async_patch_settings(command_routing_languages=[])
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], record["args"], access_key, hass, data)
    assert outcome == ("denied" if remove_language else "allowed")
    assert call.await_count == (0 if remove_language else 1)


@pytest.mark.parametrize("language,expected", [("zh-Hans", "状态为 关闭。"), ("es", "El estado es Apagado.")])
async def test_command_state_reply_localizes_prose_and_ha_state(hass, local_home, language, expected):
    data, access_key, entity_id = local_home
    await data.store.async_patch_settings(command_routing_languages=[language])
    answer = await async_execute_local_command(hass, data, access_key,
        {"intent": "HassGetState", "entity_ids": [entity_id]}, RoutingAuthority.capture(data, access_key),
        "http://test", language=language)
    assert answer == expected


@pytest.mark.parametrize("language,alias,sentence,reply", [
    ("zh-Hans", "台灯", "打开台灯", "Phoenix MCP 未能完成此指令。"),
    ("es", "Lámpara", "enciende la lámpara", "Phoenix MCP no pudo completar esta orden."),
])
async def test_multilingual_local_command_keeps_mesa_denial(hass, local_home, language, alias, sentence, reply):
    data, access_key, entity_id = local_home
    await data.store.async_patch_settings(command_routing_languages=[language])
    await enable_mesa(hass, data, entity_id, "prohibited")
    er.async_get(hass).async_update_entity(entity_id, aliases={alias})
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        answer, build, provider = await run_voice(hass, data, sentence, language=language)
    assert answer == reply
    call.assert_not_called()
    build.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize('delayed', [False, True])
async def test_success_reply_observes_actual_report_without_resending(hass, local_home, delayed, monkeypatch):
    from custom_components.phoenix_mcp import command_verification
    data, access_key, eid = local_home
    monkeypatch.setattr(command_verification, 'COMMAND_VERIFICATION_WAIT_SECONDS', .06)
    monkeypatch.setattr(command_verification, 'COMMAND_VERIFICATION_POLL_SECONDS', .005)
    handles = []
    async def dispatched(*args, **kwargs):
        def reported():
            hass.states.async_set(eid, 'on', {**hass.states.get(eid).attributes, 'brightness': 94})
        if delayed:
            handles.append(asyncio.get_running_loop().call_later(.01, reported))
        else:
            reported()
    try:
        with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
              patch.object(type(hass.services), 'async_call', AsyncMock(side_effect=dispatched)) as call):
            answer, _, provider = await run_voice(hass, data, 'set the desk lamp brightness to 37 percent', language='en')
        assert answer == 'Home Assistant reports the requested state.'
        call.assert_awaited_once()
        provider.assert_not_called()
        assert data.agent_recovery.records(access_key.id) == []
    finally:
        for handle in handles:
            handle.cancel()


@pytest.mark.parametrize('report_matches', [False, True])
async def test_approved_routed_action_has_the_same_state_observation(hass, local_home, report_matches):
    import json
    data, access_key, eid = local_home
    await enable_mesa(hass, data, eid, 'confirm')
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        answer, _, _ = await run_voice(hass, data, 'turn on the desk lamp', language='en')
    assert 'needs approval' in answer
    record = data.store.get_pending_approvals()[0]
    async def dispatched(*args, **kwargs):
        if report_matches:
            hass.states.async_set(eid, 'on', dict(hass.states.get(eid).attributes))
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', AsyncMock(side_effect=dispatched)) as call):
        result, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], access_key, hass, data)
    payload = json.loads(result['content'][0]['text'])
    assert outcome == 'allowed' and payload['success'] is True
    assert payload['state_verification'] == ('matched' if report_matches else 'unconfirmed')
    call.assert_awaited_once()


async def test_cancel_after_dispatch_during_observation_keeps_recovery_barrier(hass, local_home, monkeypatch):
    from custom_components.phoenix_mcp import command_verification
    data, access_key, _ = local_home
    monkeypatch.setattr(command_verification, 'COMMAND_VERIFICATION_WAIT_SECONDS', 10)
    entered = asyncio.Event()
    original = command_verification.async_verify_command_state
    async def observing(*args):
        entered.set()
        return await original(*args)
    with (patch.object(command_verification, 'async_verify_command_state', observing),
          patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        task = asyncio.create_task(run_voice(hass, data, 'turn on the desk lamp', language='en'))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    call.assert_awaited_once()
    assert data.agent_recovery.blocks(access_key.id)


async def test_attribute_read_uses_existing_scrubbed_read_dispatch_and_no_write_authority(hass, local_home):
    from custom_components.phoenix_mcp.const import DECISION_ENTITY_CATEGORIES
    data, access_key, entity_id = local_home
    access_key.permissions.domains['light'].state = 'YELLOW'
    access_key.cap_physical_control = 'deny'
    hass.states.async_set(entity_id, 'on', {'brightness': 94, 'access_token': 'not-public'})
    proposal = {'intent': DECISION_ENTITY_CATEGORIES['attribute_read'], 'entity_ids': [entity_id], 'value': 'brightness'}
    authority = RoutingAuthority.capture(data, access_key)
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        answer = await async_execute_local_command(hass, data, access_key, proposal, authority, '', language='en')
    assert answer == 'The state is 36.9%.'
    call.assert_not_called()
    assert await async_execute_local_command(hass, data, access_key, {**proposal, 'value': 'access_token'}, authority, '') is None
    access_key.permissions.domains['light'].state = 'RED'
    assert await async_execute_local_command(hass, data, access_key, proposal, authority, '') is None


async def test_attribute_read_plan_rejects_removed_attribute_before_dispatch(hass, local_home):
    from custom_components.phoenix_mcp.command_execution import _capture_plan, plan_is_current
    from custom_components.phoenix_mcp.const import DECISION_ENTITY_CATEGORIES
    data, access_key, entity_id = local_home
    hass.states.async_set(entity_id, 'on', {'brightness': 94})
    proposal = {'intent': DECISION_ENTITY_CATEGORIES['attribute_read'], 'entity_ids': [entity_id], 'value': 'brightness'}
    plan = _capture_plan(hass, data, access_key, proposal)
    assert plan is not None and plan_is_current(plan, hass, data, access_key)
    hass.states.async_set(entity_id, 'on', {})
    assert not plan_is_current(plan, hass, data, access_key)


@pytest.mark.parametrize('value,expected', [
    (None, 'Home Assistant is not reporting a value for the requested property right now.'),
    (0, 'The state is 0%.'),
    ('invalid', 'Phoenix MCP could not read the state.'),
])
async def test_attribute_read_distinguishes_absent_value_from_zero_and_invalid(hass, local_home, value, expected):
    from custom_components.phoenix_mcp.command_execution import _capture_plan, _run_local_command
    from custom_components.phoenix_mcp.const import DECISION_ENTITY_CATEGORIES
    data, access_key, entity_id = local_home
    access_key.cap_physical_control = 'deny'
    hass.states.async_set(entity_id, 'off', {'brightness': value})
    proposal = {'intent': DECISION_ENTITY_CATEGORIES['attribute_read'], 'entity_ids': [entity_id], 'value': 'brightness'}
    authority = RoutingAuthority.capture(data, access_key)
    plan = _capture_plan(hass, data, access_key, proposal)
    assert plan is not None
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        answer = await _run_local_command(hass, data, access_key, plan, authority, '', False, language='en')
    assert answer.text == expected
    call.assert_not_called()
    if value is None:
        assert not answer.allows_local_followup


@pytest.mark.parametrize('change', ['scope', 'rotation', 'settings', 'identity'])
async def test_localized_read_rechecks_authority_after_translation_wait(hass, local_home, change):
    data, access_key, entity_id = local_home
    async def display(*args, **kwargs):
        if change == 'scope':
            access_key.permissions = PermissionTree()
        elif change == 'rotation':
            access_key.access_key_hash = 'rotated'
        elif change == 'settings':
            await data.store.async_patch_settings(kill_switch=True)
        else:
            er.async_get(hass).async_update_entity(entity_id, name='Changed identity')
        return 'private translated state'
    with patch('custom_components.phoenix_mcp.command_display.async_display_state', side_effect=display):
        answer, build, provider = await run_voice(hass, data, 'what is the state of the desk lamp', language='en')
    assert 'private translated state' not in answer
    build.assert_not_called()
    provider.assert_not_called()
