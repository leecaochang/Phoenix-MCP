"""Decision-derived commands exercise the real Voice dispatcher and saved approvals."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli, mcp_view, voice_agent
from custom_components.phoenix_mcp.admin_view import _validated_settings_patch
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import COMMAND_ROUTING_PLAN_KEY, DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider
from custom_components.phoenix_mcp.mesa import async_setup_mesa
from custom_components.phoenix_mcp.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree


@pytest.fixture
async def hybrid_home(hass, token_store, immediate_command_observation):
    token, _ = await token_store.async_create_token("hybrid", "test", rate_limit_requests=0)
    token.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    token.cap_physical_control = "allow"
    data = PhoenixData(store=token_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    hass.data["_phoenix_agentcli_store"] = store
    # Generic sequential protocol coverage; qualified JEV is exercised separately below.
    decision = await store.add("typesafe", {"api_key": "decision-secret", "model": "test-model"})
    chat = await store.add("deepseek", {"api_key": "chat-secret", "model": "chat"})
    await token_store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id=chat, voice_agent_model="chat", voice_agent_routing_mode="hybrid",
        decision_provider_id=decision, mesa_mode="off", command_routing_timeout_ms=5000)
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", "desk", original_name="Desk lamp")
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Desk lamp", "supported_color_modes": ["brightness"]})
    yield data, token, store, decision, entry.entity_id
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


def wire_answer(payload, category="light_on", target="t0", percent=50, model="test-model"):
    choices = {"route": category, "target": target, "value": "none", "attribute": "none", "color": "none",
               "percentage": str(percent), "summary": "none", "summary_scope": "none",
               "brightness_tens": str(percent // 10), "brightness_units": str(percent % 10)}
    return {"model": model, "answers": {
        question: {"type": "choice", "choice": choices[question], "confidence": 0.1,
                   "probabilities": {key: float(key == choices[question]) for key in spec["criteria"]}}
        for question, spec in payload["questions"].items()}}


async def run_voice(hass, home, *, category="light_on", sentence="Please illuminate my desk lamp",
                    language="en", request=None, percent=50):
    data, *_ = home
    async def respond(session, method, path, timeout, payload):
        return wire_answer(payload, category, percent=percent)
    with (patch.object(DecisionProvider, "_request", AsyncMock(side_effect=request or respond)) as remote,
          patch.object(agentcli, "async_run_voice_turn", AsyncMock(return_value="Provider answer")) as provider):
        answer = await voice_agent.async_voice_answer(hass, data, sentence, language=language)
    return answer, remote, provider


@pytest.mark.parametrize("category,service,extra", [
    ("light_on", "turn_on", {}), ("light_off", "turn_off", {}),
])
@pytest.mark.parametrize("routing_mode", ["hybrid", "auto"])
async def test_two_requests_dispatch_once_and_do_not_duplicate_shadow(hass, hybrid_home, category, service, extra, routing_mode):
    await hybrid_home[0].store.async_patch_settings(voice_agent_routing_mode=routing_mode)
    data, token, _, _, entity_id = hybrid_home
    await data.store.async_patch_settings(decision_shadow_enabled=True)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home, category=category, percent=extra.get("brightness_pct", 50))
    assert answer == ("Home Assistant reports the requested state." if service == "turn_off" else
                      "Command sent. Home Assistant has not yet confirmed the requested state.")
    assert remote.await_count == 2
    assert len(remote.await_args.args[4]["questions"]) == (3 if category == "light_brightness" else 1)
    call.assert_awaited_once_with("light", service, {"entity_id": [entity_id], **extra}, blocking=True, return_response=False)
    provider.assert_not_called()
    assert data.decision_shadow.observations == 0
    assert data.decision_shadow.summary()["hybrid"] == {"evaluations": 1, "reasons": {"plan_ready": 1}}
    assert data.agent_recovery.records(token.id) == []


async def test_state_query_uses_existing_scoped_read(hass, hybrid_home):
    _, token, *_ = hybrid_home
    token.permissions = PermissionTree(domains={"light": PermissionNode(state="YELLOW")})
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        answer, remote, provider = await run_voice(hass, hybrid_home, category="state_query")
    assert answer == "The state is off."
    assert remote.await_count == 2
    call.assert_not_called()
    provider.assert_not_called()


async def test_exact_local_match_spends_no_decision_request(hass, hybrid_home):
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home, sentence="turn on the desk lamp")
    assert answer == "Command sent. Home Assistant has not yet confirmed the requested state."
    call.assert_awaited_once()
    remote.assert_not_called()
    provider.assert_not_called()
    assert hybrid_home[0].decision_shadow.summary()["hybrid"] == {"evaluations": 0, "reasons": {}}


@pytest.mark.parametrize("case,expected_calls,reason", [
    ("provider", 1, "provider_fallback"), ("decline", 2, "target_declined"),
    ("low_category", 1, "category_low_probability"), ("low_target", 2, "target_low_probability"),
    ("low_zero_category", 1, "numeric_unverified"),
    ("malformed", 1, "malformed"), ("timeout", 1, "timeout"), ("unavailable", 1, "unavailable"),
    ("non_english", 0, "unsupported_language"), ("local_timeout", 0, "local_unavailable"),
    ("trigger", 0, "local_unavailable"), ("no_scope", 1, "no_target"), ("busy", 0, "busy"),
    ("brightness_declined", 1, "numeric_unverified"),
])
async def test_pre_dispatch_declines_keep_regular_provider(hass, hybrid_home, case, expected_calls, reason):
    data, token, *_ = hybrid_home
    async def respond(session, method, path, timeout, payload):
        if case in ("timeout", "unavailable"):
            raise DecisionError(case)
        if case == "malformed":
            return {"model": "test-model", "answers": {}}
        answer = wire_answer(payload, "provider" if case == "provider" else
                             "light_brightness" if case in ("brightness_declined", "low_zero_category") else "light_on",
                             "none" if case == "decline" else "t0", percent=0 if case == "low_zero_category" else 50)
        question = "route" if case in ("low_category", "low_zero_category") else "target" if case == "low_target" else "brightness_tens"
        if case in ("low_category", "low_zero_category", "low_target", "brightness_declined") and question in answer["answers"]:
            value = answer["answers"][question]
            value["probabilities"] = {key: 0.0 for key in value["probabilities"]}
            value["probabilities"][value["choice"]] = 0.8
            value["probabilities"]["provider" if question == "route" else "none"] = 0.2
        return answer
    local = {"reason": "timeout" if case == "local_timeout" else "sentence_trigger" if case == "trigger" else "no_match",
             "proposal": None, "elapsed_ms": 1, "route": "provider"}
    if case == "no_scope": token.permissions = PermissionTree()
    if case == "busy": data.decision_shadow.task = asyncio.create_task(asyncio.Event().wait())
    with (patch("custom_components.phoenix_mcp.command_routing.async_evaluate_command", AsyncMock(return_value=local)),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home, request=respond,
                                                 sentence="Set the desk lamp to 0 percent brightness" if case == "low_zero_category" else "Please illuminate my desk lamp",
                                                 language="fr" if case == "non_english" else "en")
    assert answer == "Provider answer"
    assert remote.await_count == expected_calls
    provider.assert_awaited_once()
    call.assert_not_called()
    assert not data.store.get_pending_approvals()
    assert data.decision_shadow.summary()["hybrid"] == {"evaluations": 1, "reasons": {reason: 1}}
    assert data.decision_shadow.observations == 0


async def set_mesa(hass, home, mode):
    data, _, _, _, entity_id = home
    await data.store.async_patch_settings(mesa_mode="enforced")
    data.mesa = await async_setup_mesa(hass, "enforced")
    data.mesa.store.set(entity_id, SemanticProfile.from_dict(entity_id,
        {"semantic_profile": {"operational_boundaries": {"control_mode": mode}}}, default_origin=MetadataOrigin.USER))


@pytest.mark.parametrize("restriction", ["mesa_prohibited", "mesa_read_only", "mesa_confirm", "pass_through"])
@pytest.mark.parametrize("routing_mode", ["hybrid", "auto"])
async def test_decision_execution_preserves_mesa_and_approval(hass, hybrid_home, restriction, routing_mode):
    await hybrid_home[0].store.async_patch_settings(voice_agent_routing_mode=routing_mode)
    data, token, *_ = hybrid_home
    if restriction.startswith("mesa_") or restriction == "pass_through":
        await set_mesa(hass, hybrid_home, restriction[5:] if restriction.startswith("mesa_") else "prohibited")
    else:
        token.cap_physical_control = restriction
    if restriction == "pass_through": token.pass_through = True
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home)
    assert remote.await_count == 2
    call.assert_not_called()
    provider.assert_not_called()
    if restriction.endswith("confirm"):
        assert "needs approval" in answer
        plan = data.store.get_pending_approvals()[0]["args"][COMMAND_ROUTING_PLAN_KEY]
        assert plan["decision_category"] == "light_on"
        assert "decision-secret" not in json.dumps(plan)
    else:
        assert "could not complete" in answer
        assert not data.store.get_pending_approvals()


async def mutate(hass, home, change):
    data, token, store, account, entity_id = home
    registry = er.async_get(hass)
    if change == "account": await store.set_model(account, "new-model")
    elif change == "model_restored":
        original = store.resolve(account, purpose="decision").model
        await store.set_model(account, "other-model")
        await store.set_model(account, original)
    elif change == "deleted": await store.delete(account)
    elif change == "settings": await data.store.async_patch_settings(voice_agent_routing_mode="local")
    elif change == "revoked": token.revoked = True
    elif change == "scope": token.permissions = PermissionTree()
    elif change == "name": registry.async_update_entity(entity_id, name="Different lamp")
    elif change == "new_candidate":
        entry = registry.async_get_or_create("light", "test", "other", original_name="Desk lamp")
        hass.states.async_set(entry.entity_id, "off", {"supported_color_modes": ["brightness"]})
    elif change == "mesa":
        data.mesa.store.set(entity_id, SemanticProfile.from_dict(entity_id,
            {"semantic_profile": {"operational_boundaries": {"control_mode": "prohibited"}}}, default_origin=MetadataOrigin.USER))


@pytest.mark.parametrize("change", ["none", "account", "model_restored", "deleted", "settings", "revoked", "scope", "name", "new_candidate", "mesa"])
@pytest.mark.parametrize("category,service", [("light_on", "turn_on"), ("light_off", "turn_off")])
async def test_saved_decision_approval_revalidates_account_and_full_catalog(hass, hybrid_home, change, category, service):
    data, token, _, _, entity_id = hybrid_home
    await set_mesa(hass, hybrid_home, "confirm")
    with patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])):
        answer, _, _ = await run_voice(hass, hybrid_home, category=category)
    assert "needs approval" in answer
    record = data.store.get_pending_approvals()[0]
    assert record["args"]["service_data"] == {}
    await mutate(hass, hybrid_home, change)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], record["args"], token, hass, data)
    if change == "none":
        assert outcome == "allowed"
        call.assert_awaited_once_with("light", service, {"entity_id": [entity_id]},
                                    blocking=True, return_response=False)
    else:
        assert outcome == "denied"
        call.assert_not_called()


@pytest.mark.parametrize("change", ["account", "new_candidate", "name"])
async def test_mesa_wait_rechecks_decision_plan_without_provider_replay(hass, hybrid_home, change):
    apply = mcp_view.async_apply_mesa_to_call
    async def changed(*args, **kwargs):
        result = await apply(*args, **kwargs)
        await mutate(hass, hybrid_home, change)
        return result
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(mcp_view, "async_apply_mesa_to_call", changed),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, _, provider = await run_voice(hass, hybrid_home)
    assert "could not complete" in answer
    call.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize("change", ["account", "model_restored", "settings", "revoked"])
async def test_changed_authority_during_decision_stops_turn(hass, hybrid_home, change):
    async def respond(session, method, path, timeout, payload):
        await mutate(hass, hybrid_home, change)
        return wire_answer(payload)
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        _, remote, provider = await run_voice(hass, hybrid_home, request=respond)
    assert remote.await_count == 1
    provider.assert_not_called()
    call.assert_not_called()
    assert hybrid_home[0].decision_shadow.summary()["hybrid"] == {
        "evaluations": 1, "reasons": {"authority_changed": 1},
    }


async def test_uncertain_effect_blocks_replay_and_next_write(hass, hybrid_home):
    data, token, *_ = hybrid_home
    # Exercise service recovery even when HA sentence loading is cold.
    await data.store.async_patch_settings(command_routing_timeout_ms=5000)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", AsyncMock(side_effect=TimeoutError)) as call):
        first, _, provider = await run_voice(hass, hybrid_home)
        second, _, fallback = await run_voice(hass, hybrid_home)
    assert "Actions may have run" in first and "Actions may have run" in second
    assert data.agent_recovery.blocks(token.id)
    call.assert_awaited_once()
    provider.assert_not_called()
    fallback.assert_not_called()


async def assert_cancelled_decision_never_dispatches_or_falls_back(hass, hybrid_home):
    entered, closed = asyncio.Event(), asyncio.Event()
    async def request(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    with (patch.object(DecisionProvider, "_request", request),
          patch.object(agentcli, "async_run_voice_turn", new_callable=AsyncMock) as provider,
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        task = asyncio.create_task(voice_agent.async_voice_answer(hass, hybrid_home[0], "Please illuminate my desk lamp", language="en"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert closed.is_set()
    assert hybrid_home[0].decision_shadow.task is None
    assert hybrid_home[0].decision_shadow.summary()["hybrid"]["evaluations"] == 0
    provider.assert_not_called()
    call.assert_not_called()


async def test_cancelled_decision_never_dispatches_or_falls_back(hass, hybrid_home):
    await assert_cancelled_decision_never_dispatches_or_falls_back(hass, hybrid_home)


async def test_owned_decision_cancellation_is_counted_without_fallback(hass, hybrid_home):
    data = hybrid_home[0]
    entered = asyncio.Event()
    async def request(*args):
        entered.set()
        await asyncio.Event().wait()
    with (patch.object(DecisionProvider, "_request", request),
          patch.object(agentcli, "async_run_voice_turn", new_callable=AsyncMock) as provider,
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        task = asyncio.create_task(voice_agent.async_voice_answer(hass, data, "Please illuminate my desk lamp", language="en"))
        await entered.wait()
        data.decision_shadow.cancel()
        await task
    assert data.decision_shadow.summary()["hybrid"] == {"evaluations": 1, "reasons": {"cancelled": 1}}
    provider.assert_not_called()
    call.assert_not_called()


async def test_hybrid_settings_require_account_and_clearing_disables_mode(hass, hybrid_home):
    data, _, _, account, _ = hybrid_home
    changes, error = await _validated_settings_patch({"voice_agent_routing_mode": "hybrid"}, data, hass, "rid")
    assert error is None and changes["voice_agent_routing_mode"] == "hybrid"
    _, error = await _validated_settings_patch({"voice_agent_routing_mode": "hybrid", "decision_provider_id": None}, data, hass, "rid")
    assert error is not None and error.status == 400
    changes, error = await _validated_settings_patch({"decision_provider_id": None}, data, hass, "rid")
    assert error is None and changes["voice_agent_routing_mode"] == "off"
    assert data.store.get_settings().decision_provider_id == account


@pytest.mark.parametrize("language,sentence,reply", [
    ("zh-Hans", "请打开台灯", "命令已发送。Home Assistant 尚未确认请求的状态。"),
    ("es", "enciende la lámpara", "Comando enviado. Home Assistant aún no ha confirmado el estado solicitado."),
])
@pytest.mark.parametrize("routing_mode", ["hybrid", "auto"])
async def test_enabled_language_hybrid_voice_carries_language_through_dispatch(hass, hybrid_home, language, sentence, reply, routing_mode):
    await hybrid_home[0].store.async_patch_settings(voice_agent_routing_mode=routing_mode)
    data, _, _, _, entity_id = hybrid_home
    await data.store.async_patch_settings(command_routing_languages=[language])
    local = {"reason": "no_match", "proposal": None, "elapsed_ms": 1, "route": "provider"}
    with (patch("custom_components.phoenix_mcp.command_routing.async_evaluate_command", AsyncMock(return_value=local)),
          patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home, sentence=sentence, language=language,
                                                 category="light_on")
    assert answer == reply
    assert remote.await_count == 2
    assert all(args.args[4]["state"]["language"] == ("zh-CN" if language == "zh-Hans" else "es") for args in remote.await_args_list)
    call.assert_awaited_once_with("light", "turn_on", {"entity_id": [entity_id]}, blocking=True, return_response=False)
    provider.assert_not_called()


@pytest.mark.parametrize("kind", ["typesafe", "classifier", "systemone"])
@pytest.mark.parametrize("language", ["en", "zh-Hans", "es"])
@pytest.mark.parametrize("routing_mode", ["auto", "hybrid"])
async def test_numeric_voice_automatically_falls_back_even_with_legacy_opt_in(hass, hybrid_home, kind, language, routing_mode):
    data, _, store, _, _ = hybrid_home
    account = await store.add(kind, {"api_key": "legacy-key", "model": "legacy-model",
        "base_url": "http://server/v1", "decision_numeric_enabled": True,
        "decision_numeric_categories": ["light_brightness"]})
    await data.store.async_patch_settings(decision_provider_id=account, voice_agent_routing_mode=routing_mode,
                                         command_routing_languages=[language])
    local = {"reason": "no_match", "proposal": None, "elapsed_ms": 1, "route": "provider"}
    with (patch("custom_components.phoenix_mcp.command_routing.async_evaluate_command", AsyncMock(return_value=local)),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home, category="light_brightness", percent=55,
            sentence="set the desk lamp to 50 percent", language=language)
    assert answer == "Provider answer"
    provider.assert_awaited_once()
    assert remote.await_count == 1
    call.assert_not_called()
    assert not data.store.get_pending_approvals()
    assert data.decision_shadow.summary()["hybrid"]["reasons"] == {"numeric_unverified": 1}


@pytest.mark.parametrize("percent", [0, 37, 50, 100])
async def test_recognized_numeric_voice_uses_ha_value_without_either_provider(hass, hybrid_home, percent):
    entity_id = hybrid_home[-1]
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home,
            sentence=f"set the desk lamp brightness to {percent} percent")
    assert answer == ("Home Assistant reports the requested state." if percent == 0 else
                      "Command sent. Home Assistant has not yet confirmed the requested state.")
    call.assert_awaited_once_with("light", "turn_on", {"entity_id": [entity_id], "brightness_pct": percent},
                                blocking=True, return_response=False)
    remote.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize("ingress", ["voice", "chat"])
@pytest.mark.parametrize("percent", [0, 50, 100])
@pytest.mark.parametrize("value_check", [None, {"status": "match", "source_value": 50}])
async def test_preexisting_numeric_decision_approval_cannot_execute(hass, hybrid_home, ingress, percent, value_check):
    from custom_components.phoenix_mcp.command_execution import _capture_plan, plan_is_current
    from custom_components.phoenix_mcp.decision_targets import capture_targets
    data, token, _, account, entity_id = hybrid_home
    await data.store.async_patch_settings(agentcli_routing_mode="hybrid")
    proposal = {"intent": "HassLightSet", "entity_ids": [entity_id], "brightness_pct": percent}
    plan = _capture_plan(hass, data, token, proposal, ingress=ingress)
    assert plan_is_current(plan, hass, data, token)  # A recognized local value still qualifies.
    plan.update(decision_category="light_brightness",
                decision_catalog=capture_targets(hass, token, "light_brightness").digest(),
                decision_account=agentcli.decision_account_fingerprint(hass, account))
    if value_check is not None:
        plan["value_check"] = {**value_check, "source_value": percent}
    args = {"domain": "light", "service": "turn_on", "entity_id": [entity_id],
            "service_data": {"brightness_pct": percent}, COMMAND_ROUTING_PLAN_KEY: plan}
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool("call_service", args, token, hass, data)
    assert outcome == "denied"
    call.assert_not_called()
    # The otherwise identical recognized local plan can execute; legacy decision
    # plans lack the independently qualified joint review and cannot impersonate it.
    for key in ("decision_category", "decision_catalog", "decision_account"):
        del plan[key]
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as control):
        _, outcome, _ = await mcp_view.async_execute_approved_tool("call_service", args, token, hass, data)
    assert outcome == "allowed"
    control.assert_awaited_once()


@pytest.fixture
async def jev_hybrid_home(hybrid_home):
    """Enable only the independently qualified joint configuration."""
    _, _, store, account, _ = hybrid_home
    await store.set_model(account, "jev-latest")
    return hybrid_home


async def jev_response(session, method, path, timeout, payload):
    return wire_answer(payload, model="jev-1.13.0")


@pytest.fixture(params=[
    ('light_brightness', 'light', 'brightness_pct', 'turn_on', 'brightness_pct', 1),
    ('media_volume', 'media_player', 'volume_pct', 'volume_set', 'volume_level', 100),
    ('cover_position', 'cover', 'position_pct', 'set_cover_position', 'position', 1),
])
async def jev_numeric_home(hass, jev_hybrid_home, request):
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.components.media_player import MediaPlayerEntityFeature
    category, domain, field, service, service_field, divisor = request.param
    data, token, store, account, entity_id = jev_hybrid_home
    token.permissions.domains[domain] = PermissionNode(state='GREEN')
    if domain != 'light':
        entry = er.async_get(hass).async_get_or_create(domain, 'test', 'device', original_name='Test device')
        entity_id = entry.entity_id
        feature = CoverEntityFeature.SET_POSITION if domain == 'cover' else MediaPlayerEntityFeature.VOLUME_SET
        hass.states.async_set(entity_id, 'open' if domain == 'cover' else 'idle', {'supported_features': feature})
        async_expose_entity(hass, 'conversation', entity_id, True)
    return (data, token, store, account, entity_id), request.param


def numeric_reply(payload, home, spec, *, percent=37, category=None, model='jev-1.13.0'):
    selected = next(key for key, value in payload['state']['targets'].items() if value['entity_id'] == home[-1])
    return wire_answer(payload, category or spec[0], selected, percent=percent, model=model)


@pytest.mark.parametrize('percent', [0, 37, 100])
@pytest.mark.parametrize('language', ['en', 'es-MX', 'zh-Hans'])
async def test_qualified_numeric_voice_uses_one_decision_and_shared_execution(hass, jev_numeric_home, percent, language):
    home, spec = jev_numeric_home
    _, domain, _, service, service_field, divisor = spec
    await home[0].store.async_patch_settings(voice_agent_routing_mode='auto')
    async def respond(session, method, path, timeout, payload):
        assert set(payload['questions']) == {'route', 'target', 'percentage', 'summary', 'summary_scope'}
        return numeric_reply(payload, home, spec, percent=percent)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, request=respond,
            sentence=(f'Quiero el dispositivo al {percent} por ciento ahora.' if language == 'es-MX'
                      else f'我希望设备现在的亮度是百分之{percent}。' if language == 'zh-Hans'
                      else f'I would like the device at {percent} percent now.'), language=language)
    remote.assert_awaited_once()
    provider.assert_not_called()
    call.assert_awaited_once_with(domain, service, {'entity_id': [home[-1]], service_field: percent / divisor
                                 if divisor != 1 else percent}, blocking=True, return_response=False)


@pytest.mark.parametrize('case', ['wrong_value', 'low_value', 'revision', 'categorical', 'decline', 'multiple'])
async def test_numeric_disagreement_or_uncertainty_falls_back_without_execution(hass, jev_numeric_home, case):
    home, spec = jev_numeric_home
    async def respond(session, method, path, timeout, payload):
        body = numeric_reply(payload, home, spec, percent=5 if case == 'wrong_value' else 37,
                             category='provider' if case == 'decline' else 'state_query' if case == 'categorical' else None,
                             model='new-revision' if case == 'revision' else 'jev-1.13.0')
        if case == 'low_value': body['answers']['percentage']['probabilities'].update({'37': 0.8, 'none': 0.2})
        return body
    sentence = 'I would like the device at 37 percent now.'
    if case == 'multiple': sentence += ' Not 73 percent.'
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        _, remote, provider = await run_voice(hass, home, request=respond, sentence=sentence)
    assert remote.await_count == (0 if case == 'multiple' else 1)
    provider.assert_awaited_once()
    call.assert_not_called()
    assert not home[0].store.get_pending_approvals()


@pytest.mark.parametrize('change', ['none', 'source_value', 'proposal', 'missing_review', 'account', 'model_restored',
                                    'scope', 'new_candidate', 'mesa'])
async def test_numeric_approval_binds_evidence_catalog_and_current_authority(hass, jev_numeric_home, change):
    home, spec = jev_numeric_home
    data, token, _, _, _ = home
    await set_mesa(hass, home, 'confirm')
    async def respond(session, method, path, timeout, payload): return numeric_reply(payload, home, spec)
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        reply, remote, provider = await run_voice(hass, home, request=respond,
                                                sentence='I would like the device at thirty-seven percent now.')
    assert 'needs approval' in reply
    remote.assert_awaited_once()
    provider.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    plan = record['args'][COMMAND_ROUTING_PLAN_KEY]
    assert plan['decision_batch']['percentage'] == 37
    assert 'thirty-seven' not in json.dumps(plan) and 'decision-secret' not in json.dumps(plan)
    if change == 'source_value': plan['decision_batch']['percentage'] = 5
    elif change == 'proposal':
        plan['proposal'][spec[2]] = 5
        record['args']['service_data'][spec[4]] = 5 / spec[5]
    elif change == 'missing_review': del plan['decision_batch']['percentage']
    else: await mutate(hass, home, change)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


@pytest.mark.parametrize('restriction', ['capability', 'prohibited', 'pass_through'])
async def test_numeric_fast_path_preserves_capability_and_mesa(hass, jev_numeric_home, restriction):
    home, spec = jev_numeric_home
    if restriction == 'capability': home[1].cap_physical_control = 'deny'
    else: await set_mesa(hass, home, 'prohibited')
    if restriction == 'pass_through': home[1].pass_through = True
    async def respond(session, method, path, timeout, payload): return numeric_reply(payload, home, spec)
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        _, remote, provider = await run_voice(hass, home, request=respond,
                                            sentence='I would like the device at 37 percent now.')
    remote.assert_awaited_once()
    provider.assert_not_called()
    # Existing physical-control policy gates covers, not lighting or media volume.
    assert call.await_count == (1 if restriction == 'capability' and spec[1] != 'cover' else 0)


@pytest.mark.parametrize('change', ['account', 'scope', 'revoked'])
async def test_numeric_source_worker_rechecks_before_sharing_request(hass, jev_hybrid_home, change):
    from custom_components.phoenix_mcp.numeric_evidence import read_explicit_percentage
    original = hass.async_add_executor_job
    reached = False
    async def worker(function, *args):
        nonlocal reached
        if function is read_explicit_percentage:
            reached = True
            await mutate(hass, jev_hybrid_home, change)
            return 37
        return await original(function, *args)
    with patch.object(hass, 'async_add_executor_job', side_effect=worker):
        _, remote, _ = await run_voice(hass, jev_hybrid_home, request=jev_response,
            sentence=f'I would like {jev_hybrid_home[-1]} at 37 percent now.')
    assert reached
    remote.assert_not_called()


@pytest.mark.parametrize("category,service", [("light_on", "turn_on"), ("light_off", "turn_off"), ("state_query", None)])
async def test_jev_joint_routes_one_request_through_existing_executor(hass, jev_hybrid_home, category, service):
    async def respond(session, method, path, timeout, payload):
        assert set(payload["questions"]) == {"route", "target", "summary", "summary_scope"}
        return wire_answer(payload, category, model="jev-1.13.0")
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, jev_hybrid_home, request=respond)
    remote.assert_awaited_once()
    provider.assert_not_called()
    if service:
        call.assert_awaited_once_with("light", service, {"entity_id": [jev_hybrid_home[-1]]},
                                     blocking=True, return_response=False)
        assert "requested state" in answer
    else:
        call.assert_not_called()
        assert answer == "The state is off."
    assert jev_hybrid_home[0].decision_shadow.summary()["hybrid"]["reasons"] == {"plan_ready": 1}


@pytest.mark.parametrize("case,reason", [
    ("provider", "provider_fallback"), ("none", "target_declined"),
    ("low_route", "category_low_probability"), ("low_target", "target_low_probability"),
    ("revision", "model_unverified"), ("malformed", "malformed"),
    ("timeout", "timeout"), ("rate_limited", "rate_limited"), ("category_scope", "category_mismatch"),
    ("unadvertised_route", "malformed"),
])
async def test_jev_joint_declines_without_sequential_retry(hass, jev_hybrid_home, case, reason):
    data, token, *_ = jev_hybrid_home
    if case in ("category_scope", "unadvertised_route"):
        token.permissions = PermissionTree(domains={"light": PermissionNode(state="YELLOW")})
    if case == "category_scope":
        entry = er.async_get(hass).async_get_or_create("light", "test", "zz_scope_control")
        token.permissions.entities[entry.entity_id] = PermissionNode(state="GREEN")
        hass.states.async_set(entry.entity_id, "off")
        async_expose_entity(hass, "conversation", entry.entity_id, True)
    async def respond(session, method, path, timeout, payload):
        assert set(payload["questions"]) == {"route", "target", "summary", "summary_scope"}
        if case in ("category_scope", "unadvertised_route"):
            assert ("light_on" in payload["questions"]["route"]["criteria"]) == (case == "category_scope")
        if case in ("timeout", "rate_limited"):
            raise DecisionError(case)
        answer = wire_answer(payload, "provider" if case == "provider" else "light_on",
                             "none" if case == "none" else "t0",
                             model="unqualified-revision" if case == "revision" else "jev-1.13.0")
        if case == "malformed":
            del answer["answers"]["target"]
        if case in ("low_route", "low_target"):
            key = "route" if case == "low_route" else "target"
            value = answer["answers"][key]
            value["probabilities"][value["choice"]] = 0.8
            value["probabilities"]["provider" if key == "route" else "none"] = 0.2
        return answer
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        answer, remote, provider = await run_voice(hass, jev_hybrid_home, request=respond)
    assert answer == "Provider answer"
    remote.assert_awaited_once()
    provider.assert_awaited_once()
    call.assert_not_called()
    assert not data.store.get_pending_approvals()
    assert data.decision_shadow.summary()["hybrid"]["reasons"] == {reason: 1}


async def test_jev_large_union_keeps_all_candidates_in_one_joint_request(hass, jev_hybrid_home):
    from custom_components.phoenix_mcp.const import DECISION_MAX_TARGETS
    _, token, *_ = jev_hybrid_home
    token.permissions.domains["sensor"] = PermissionNode(state="YELLOW")
    for index in range(DECISION_MAX_TARGETS):
        entry = er.async_get(hass).async_get_or_create("sensor", "test", f"reading_{index}")
        hass.states.async_set(entry.entity_id, "20")
        async_expose_entity(hass, "conversation", entry.entity_id, True)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, jev_hybrid_home, request=jev_response)
    assert [set(item.args[4]["questions"]) for item in remote.await_args_list] == [{"route", "target", "summary", "summary_scope"}]
    assert len(remote.await_args.args[4]["state"]["targets"]) == DECISION_MAX_TARGETS + 1
    call.assert_awaited_once()
    provider.assert_not_called()


@pytest.mark.parametrize("restriction", ["prohibited", "read_only", "confirm", "pass_through"])
async def test_jev_joint_preserves_mesa(hass, jev_hybrid_home, restriction):
    data, token, *_ = jev_hybrid_home
    await set_mesa(hass, jev_hybrid_home, "prohibited" if restriction == "pass_through" else restriction)
    if restriction == "pass_through":
        token.pass_through = True
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, jev_hybrid_home, request=jev_response)
    remote.assert_awaited_once()
    call.assert_not_called()
    provider.assert_not_called()
    assert ("needs approval" in answer) if restriction == "confirm" else ("could not complete" in answer)
    if restriction == "confirm":
        plan = data.store.get_pending_approvals()[0]["args"][COMMAND_ROUTING_PLAN_KEY]
        assert set(plan["decision_batch"]) == {"model", "language", "catalog"}
        assert plan["decision_batch"]["model"] == "jev-1.13.0"
        assert "decision-secret" not in json.dumps(plan) and "Desk lamp" not in json.dumps(plan)


@pytest.mark.parametrize("change", ["none", "account", "model_restored", "scope", "name", "new_candidate", "read_competitor", "mesa"])
async def test_jev_joint_approval_binds_full_union_and_model(hass, jev_hybrid_home, change):
    data, token, _, _, entity_id = jev_hybrid_home
    token.permissions.domains["sensor"] = PermissionNode(state="YELLOW")
    for index in range(60):
        entry = er.async_get(hass).async_get_or_create("sensor", "test", f"catalog_{index}")
        hass.states.async_set(entry.entity_id, "20")
        async_expose_entity(hass, "conversation", entry.entity_id, True)
    await set_mesa(hass, jev_hybrid_home, "confirm")
    with patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])):
        answer, _, _ = await run_voice(hass, jev_hybrid_home, request=jev_response)
    assert "needs approval" in answer
    record = data.store.get_pending_approvals()[0]
    if change == "read_competitor":
        # This candidate is absent from the light-on catalog but was in scope for the joint choice.
        entry = er.async_get(hass).async_get_or_create("sensor", "test", "competing", original_name="Desk lamp")
        hass.states.async_set(entry.entity_id, "20")
        async_expose_entity(hass, "conversation", entry.entity_id, True)
    else:
        await mutate(hass, jev_hybrid_home, change)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], record["args"], token, hass, data)
    if change == "none":
        assert outcome == "allowed"
        call.assert_awaited_once_with("light", "turn_on", {"entity_id": [entity_id]}, blocking=True, return_response=False)
    else:
        assert outcome == "denied"
        call.assert_not_called()


@pytest.mark.parametrize("change", ["account", "settings", "revoked", "new_candidate"])
async def test_jev_joint_rechecks_changes_during_model_wait(hass, jev_hybrid_home, change):
    async def respond(session, method, path, timeout, payload):
        await mutate(hass, jev_hybrid_home, change)
        return wire_answer(payload, model="jev-1.13.0")
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call:
        _, remote, provider = await run_voice(hass, jev_hybrid_home, request=respond)
    remote.assert_awaited_once()
    call.assert_not_called()
    if change != "new_candidate":
        provider.assert_not_called()
    await asyncio.sleep(0)
    assert jev_hybrid_home[0].decision_shadow.task is None


async def test_jev_joint_caller_cancellation_releases_owned_slot(hass, jev_hybrid_home):
    await assert_cancelled_decision_never_dispatches_or_falls_back(hass, jev_hybrid_home)


@pytest.mark.parametrize("sentence", ["turn on the desk lamp", "set the desk lamp to 37 percent brightness"])
async def test_jev_local_matches_keep_zero_decision_calls(hass, jev_hybrid_home, sentence):
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, jev_hybrid_home, sentence=sentence, request=jev_response)
    call.assert_awaited_once()
    remote.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize('kind,model,base_url,language', [
    ('classifier', 'Qwen/Qwen3.5-4B', 'http://local.test/v1', 'en'),
    ('systemone', 'jev-latest', 'http://local.test/v1', 'en'),
    ('typesafe', 'jev-latest', 'https://custom.test/v1', 'en'),
])
async def test_other_models_and_endpoints_keep_sequential_routing(
    hass, hybrid_home, kind, model, base_url, language,
):
    data, _, store, *_ = hybrid_home
    account = await store.add(kind, {'api_key': 'test-secret', 'model': model, 'base_url': base_url})
    await data.store.async_patch_settings(decision_provider_id=account, command_routing_languages=[language])
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, hybrid_home, language=language, request=jev_response)
    assert [set(item.args[4]['questions']) for item in remote.await_args_list] == [{'route'}, {'target'}]
    call.assert_awaited_once()
    provider.assert_not_called()


@pytest.mark.parametrize('sentence', ['decision-secret', 'password: private-value', 'turn on light.hidden'])
async def test_jev_joint_keeps_sensitive_inputs_out_of_decision_request(hass, jev_hybrid_home, sentence):
    local = {'reason': 'no_match', 'proposal': None, 'elapsed_ms': 1, 'route': 'provider'}
    with (patch('custom_components.phoenix_mcp.command_routing.async_evaluate_command', AsyncMock(return_value=local)),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, jev_hybrid_home, sentence=sentence, request=jev_response)
    remote.assert_not_called()
    call.assert_not_called()
    provider.assert_awaited_once()
    assert jev_hybrid_home[0].decision_shadow.summary()['hybrid']['reasons'] == {'context_unavailable': 1}


@pytest.mark.parametrize('change', ['account', 'new_candidate'])
async def test_jev_joint_revalidates_after_mesa_wait_without_replay(hass, jev_hybrid_home, change):
    apply = mcp_view.async_apply_mesa_to_call
    async def changed(*args, **kwargs):
        result = await apply(*args, **kwargs)
        await mutate(hass, jev_hybrid_home, change)
        return result
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(mcp_view, 'async_apply_mesa_to_call', changed),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, jev_hybrid_home, request=jev_response)
    assert 'could not complete' in answer
    remote.assert_awaited_once()
    call.assert_not_called()
    provider.assert_not_called()


@pytest.fixture(params=[
    ('fan_speed', 37), ('valve_position', 37), ('cover_tilt', 37),
    ('climate_temperature', 21.5), ('humidifier_humidity', 45), ('climate_humidity', 45),
    ('water_temperature', 55.5), ('number_value', -2.5), ('input_number_value', -2.5),
    ('light_temperature', 3500),
])
async def expanded_numeric_home(hass, jev_hybrid_home, request):
    from homeassistant.components.climate import ClimateEntityFeature
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.components.fan import FanEntityFeature
    from homeassistant.components.valve import ValveEntityFeature
    from homeassistant.components.water_heater import WaterHeaterEntityFeature
    from custom_components.phoenix_mcp.command_operations import numeric_operation
    from custom_components.phoenix_mcp.const import DECISION_TARGET_INTENTS
    category, value = request.param
    intent = DECISION_TARGET_INTENTS[category]
    operation = numeric_operation(intent)
    data, token, store, account, _ = jev_hybrid_home
    attrs = {
        'fan': {'supported_features': FanEntityFeature.SET_SPEED},
        'valve': {'supported_features': ValveEntityFeature.SET_POSITION},
        'cover': {'supported_features': CoverEntityFeature.SET_TILT_POSITION},
        'climate': {'supported_features': ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TARGET_HUMIDITY,
                    'min_temp': 5, 'max_temp': 35, 'target_temp_step': 0.5, 'min_humidity': 20, 'max_humidity': 80},
        'humidifier': {'min_humidity': 20, 'max_humidity': 80},
        'water_heater': {'supported_features': WaterHeaterEntityFeature.TARGET_TEMPERATURE, 'min_temp': 30, 'max_temp': 65},
        'number': {'min': -10, 'max': 10, 'step': 0.5, 'unit_of_measurement': 'dB'},
        'input_number': {'min': -10, 'max': 10, 'step': 0.5},
        'light': {'supported_color_modes': ['color_temp'], 'min_color_temp_kelvin': 2000, 'max_color_temp_kelvin': 6500},
    }[operation.domain]
    token.permissions.domains[operation.domain] = PermissionNode(state='GREEN')
    entry = er.async_get(hass).async_get_or_create(operation.domain, 'test', 'numeric_extra', original_name='Test control')
    hass.states.async_set(entry.entity_id, 'on', {'friendly_name': 'Test control', **attrs})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    home = data, token, store, account, entry.entity_id
    return home, category, intent, operation, value


def scalar_reply(payload, entity_id, category):
    target = next(key for key, descriptor in payload['state']['targets'].items() if descriptor['entity_id'] == entity_id)
    choices = {'route': category, 'target': target, 'percentage': 'value', 'value': 'none', 'attribute': 'none', 'color': 'none', 'summary': 'none', 'summary_scope': 'none'}
    return {'model': 'jev-1.13.0', 'answers': {
        question: {'type': 'choice', 'choice': choices[question], 'confidence': 1,
                   'probabilities': {key: float(key == choices[question]) for key in spec['criteria']}}
        for question, spec in payload['questions'].items()}}


async def test_expanded_native_values_use_guarded_service_once(hass, expanded_numeric_home):
    home, category, intent, operation, value = expanded_numeric_home
    async def respond(session, method, path, timeout, payload):
        assert payload['state']['source_value'] == value
        return scalar_reply(payload, home[-1], category)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, request=respond,
                                             sentence=f'I want Test control at {value} now')
    remote.assert_awaited_once()
    provider.assert_not_called()
    call.assert_awaited_once_with(operation.domain, operation.service,
        {'entity_id': [home[-1]], operation.service_field: value}, blocking=True, return_response=False)


@pytest.mark.parametrize('change', ['range', 'unit', 'step', 'value'])
async def test_native_numeric_approval_rechecks_bounds_units_and_value(hass, jev_hybrid_home, change):
    home = jev_hybrid_home
    data, token, store, account, _ = home
    token.permissions.domains['number'] = PermissionNode(state='GREEN')
    entry = er.async_get(hass).async_get_or_create('number', 'test', 'level', original_name='Level')
    attributes = {'min': -10, 'max': 10, 'step': 0.5, 'unit_of_measurement': 'dB'}
    hass.states.async_set(entry.entity_id, '0', attributes)
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    home = data, token, store, account, entry.entity_id
    await set_mesa(hass, home, 'confirm')
    async def respond(session, method, path, timeout, payload):
        return scalar_reply(payload, entry.entity_id, 'number_value')
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        reply, _, _ = await run_voice(hass, home, request=respond, sentence='I want Level at -2.5 dB now')
    assert 'needs approval' in reply
    record = data.store.get_pending_approvals()[0]
    if change == 'value':
        record['args'][COMMAND_ROUTING_PLAN_KEY]['proposal']['value'] = 1
        record['args']['service_data']['value'] = 1
    else:
        attributes.update({'range': {'min': 0}, 'unit': {'unit_of_measurement': 'W'}, 'step': {'step': 1}}[change])
        hass.states.async_set(entry.entity_id, '0', attributes)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == 'denied'
    call.assert_not_called()


async def test_expanded_builtin_intents_keep_local_route(hass, expanded_numeric_home):
    from custom_components.phoenix_mcp.command_routing import async_evaluate_command
    home, category, intent, operation, value = expanded_numeric_home
    sentences = {
        'fan_speed': 'set Test control speed to 37 percent',
        'valve_position': 'set Test control to 37 percent',
        'climate_temperature': 'set Test control temperature to 21.5 degrees',
        'light_temperature': 'set Test control color temperature to 3500 kelvin',
    }
    if category not in sentences:
        pytest.skip('No built-in sentence contract for this operation')
    result = await async_evaluate_command(hass, home[0], home[1], sentences[category], 'en')
    assert result['proposal'] == {'intent': intent, 'entity_ids': [home[-1]], operation.proposal_field: value}, result


async def test_expanded_numeric_out_of_range_cannot_be_authorized_by_model(hass, expanded_numeric_home):
    home, category, *_ = expanded_numeric_home
    async def respond(session, method, path, timeout, payload):
        return scalar_reply(payload, home[-1], category)
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        _, remote, provider = await run_voice(hass, home, request=respond, sentence='I want Test control at 999999 now')
    remote.assert_awaited_once()
    provider.assert_awaited_once()
    call.assert_not_called()
    assert not home[0].store.get_pending_approvals()


async def test_explicit_foreign_unit_cannot_be_authorized_by_model(hass, expanded_numeric_home):
    home, category, _, _, value = expanded_numeric_home
    async def respond(session, method, path, timeout, payload):
        return scalar_reply(payload, home[-1], category)
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        _, remote, provider = await run_voice(hass, home, request=respond,
                                             sentence=f'I want Test control at {value} °F now')
    remote.assert_awaited_once()
    provider.assert_awaited_once()
    call.assert_not_called()


@pytest.fixture
async def temporal_home(hass, jev_hybrid_home):
    data, token, store, account, _ = jev_hybrid_home
    token.permissions.domains['input_datetime'] = PermissionNode(state='GREEN')
    entry = er.async_get(hass).async_get_or_create('input_datetime', 'test', 'appointment', original_name='Appointment')
    hass.states.async_set(entry.entity_id, '2026-12-24 10:00:00', {
        'friendly_name': 'Appointment', 'has_date': True, 'has_time': True,
        'year': 2026, 'month': 12, 'day': 24, 'hour': 10, 'minute': 0, 'second': 0})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    return data, token, store, account, entry.entity_id


def temporal_wire(payload, entity_id, category='helper_datetime', value='value'):
    target = next(key for key, item in payload['state']['targets'].items() if item['entity_id'] == entity_id)
    choices = {'route': category, 'target': target, 'value': value, 'summary': 'none', 'summary_scope': 'none'}
    return {'model': 'jev-1.13.0', 'answers': {
        question: {'type': 'choice', 'choice': choices[question], 'confidence': 1,
                   'probabilities': {key: float(key == choices[question]) for key in spec['criteria']}}
        for question, spec in payload['questions'].items()}}


@pytest.mark.parametrize('category,value,field', [
    ('helper_date', '2026-12-25', 'date'), ('helper_time', '18:30:00', 'time'),
    ('helper_datetime', '2026-12-25 18:30:00', 'datetime'),
])
async def test_temporal_helpers_dispatch_only_the_exact_requested_field(hass, temporal_home, category, value, field):
    async def respond(*args):
        assert args[4]['state']['source'] == {'kind': field, 'value': value}
        return temporal_wire(args[4], temporal_home[-1], category)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, temporal_home, request=respond,
                                              sentence=f'Set Appointment to {value}')
    remote.assert_awaited_once()
    provider.assert_not_called()
    call.assert_awaited_once_with('input_datetime', 'set_datetime',
        {'entity_id': [temporal_home[-1]], field: value}, blocking=True, return_response=False)


@pytest.mark.parametrize('change', ['none', 'value', 'flags', 'timezone', 'competitor', 'permission'])
async def test_temporal_approval_revalidates_value_catalog_and_authority(hass, temporal_home, change):
    data, token, _, _, entity_id = temporal_home
    await set_mesa(hass, temporal_home, 'confirm')
    async def respond(*args): return temporal_wire(args[4], entity_id)
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        reply, _, provider = await run_voice(hass, temporal_home, request=respond,
                                            sentence='Set Appointment to 2026-12-25 18:30')
    assert 'needs approval' in reply
    provider.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    if change == 'value':
        record['args'][COMMAND_ROUTING_PLAN_KEY]['proposal']['value'] = '2026-12-26 18:30:00'
        record['args']['service_data']['datetime'] = '2026-12-26 18:30:00'
    elif change == 'flags':
        state = hass.states.get(entity_id)
        hass.states.async_set(entity_id, state.state, {**state.attributes, 'has_date': False})
    elif change == 'timezone': await hass.config.async_set_time_zone('America/New_York')
    elif change == 'competitor':
        entry = er.async_get(hass).async_get_or_create('input_datetime', 'test', 'other', original_name='Appointment')
        hass.states.async_set(entry.entity_id, '2026-12-24', {'has_date': True, 'has_time': False})
        async_expose_entity(hass, 'conversation', entry.entity_id, True)
    elif change == 'permission': token.permissions.domains['input_datetime'] = PermissionNode(state='RED')
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


@pytest.mark.parametrize('change', ['timezone', 'account', 'permission', 'flags', 'value_declined', 'wrong_kind', 'malformed'])
async def test_temporal_model_wait_cannot_authorize_changed_or_invalid_request(hass, temporal_home, change):
    data, token, store, account, entity_id = temporal_home
    async def respond(*args):
        body = temporal_wire(args[4], entity_id, 'helper_time' if change == 'wrong_kind' else 'helper_datetime',
                             'none' if change == 'value_declined' else 'value')
        if change == 'timezone': await hass.config.async_set_time_zone('America/New_York')
        elif change == 'account': await store.set_model(account, 'changed')
        elif change == 'permission': token.permissions.domains['input_datetime'] = PermissionNode(state='RED')
        elif change == 'flags':
            state = hass.states.get(entity_id)
            hass.states.async_set(entity_id, state.state, {**state.attributes, 'has_time': False})
        elif change == 'malformed': body['answers']['value']['choice'] = '2026-12-26'
        return body
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        _, remote, _ = await run_voice(hass, temporal_home, request=respond,
                                      sentence='Set Appointment to 2026-12-25 18:30')
    remote.assert_awaited_once()
    call.assert_not_called()
    assert not data.store.get_pending_approvals()


async def test_temporal_preview_is_inert(hass, temporal_home):
    from custom_components.phoenix_mcp.decision_batch import async_preview_batch
    data, token, _, _, entity_id = temporal_home
    async def respond(*args): return temporal_wire(args[4], entity_id)
    with (patch.object(DecisionProvider, '_request', side_effect=respond),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        result = await async_preview_batch(hass, data, token, 'Set Appointment to 2026-12-25 18:30', 'en',
                                           {'reason': 'no_match', 'proposal': None})
    assert result['reason'] == 'target_selected', result
    assert result['proposal']['value'] == '2026-12-25 18:30:00'
    assert not result['executed'] and '_plan' not in result
    assert not data.store.get_pending_approvals()
    call.assert_not_called()


@pytest.fixture
async def entity_command_home(hass, jev_hybrid_home, request):
    from custom_components.phoenix_mcp.const import COMMAND_ROUTING_ENTITY_COMMANDS, DECISION_ENTITY_CATEGORIES
    from custom_components.phoenix_mcp.entity_commands import _FEATURES
    category, value = request.param
    domain, service, field, options, feature, _ = COMMAND_ROUTING_ENTITY_COMMANDS[category]
    data, token, store, account, _ = jev_hybrid_home
    token.permissions.domains[domain] = PermissionNode(state='GREEN')
    hass.services.async_register(domain, service, AsyncMock())
    entry = er.async_get(hass).async_get_or_create(domain, 'test', 'helper', original_name='Test helper')
    attrs = {'friendly_name': 'Test helper'}
    if feature: attrs['supported_features'] = int(getattr(_FEATURES[domain], feature))
    if options: attrs[options] = ['Away', 'Home']
    hass.states.async_set(entry.entity_id, 'unknown' if domain in ('button', 'input_button', 'scene') else 'off', attrs)
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    return (data, token, store, account, entry.entity_id), category, value, DECISION_ENTITY_CATEGORIES[category], (domain, service, field)


def entity_command_wire(payload, entity_id, category, value=None):
    target = next(key for key, item in payload['state']['targets'].items() if item['entity_id'] == entity_id)
    selected_value = next((key for key, item in payload['state'].get('values', {}).items() if item == value), 'none')
    selected = {'route': category, 'target': target, 'value': selected_value, 'percentage': 'none', 'attribute': 'none', 'color': 'none', 'summary': 'none', 'summary_scope': 'none'}
    for question in ('attribute', 'color'):
        selected[question] = next((key for key, item in payload['state'].get(question + '_values', {}).items() if item == value), 'none')
    return {'model': 'jev-1.13.0', 'answers': {question: {
        'type': 'choice', 'choice': selected[question], 'confidence': 1,
        'probabilities': {key: float(key == selected[question]) for key in spec['criteria']}}
        for question, spec in payload['questions'].items()}}


@pytest.mark.parametrize('entity_command_home', [
    ('input_boolean_on', None), ('counter_increment', None), ('timer_cancel', None),
    ('input_button_press', None), ('cover_open', None), ('vacuum_return_to_base', None),
    ('lock_unlock', None), ('scene_activate', None), ('script_on', None),
    ('select_option', 'Away'), ('input_select_option', 'Away'), ('input_text_value', 'Visitors welcome'),
], indirect=True)
async def test_entity_commands_reach_guarded_dispatch(hass, entity_command_home):
    home, category, value, intent, (domain, service, field) = entity_command_home
    async def respond(*args): return entity_command_wire(args[4], home[-1], category, value)
    sentence = f'Please perform {category} for Test helper' + (f' using "{value}"' if value else '')
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, request=respond, sentence=sentence)
    remote.assert_awaited_once()
    provider.assert_not_called()
    call.assert_awaited_once_with(domain, service, {'entity_id': [home[-1]], **({field: value} if field else {})},
                                 blocking=True, return_response=False)


@pytest.mark.parametrize('entity_command_home', [('select_option', 'Away')], indirect=True)
@pytest.mark.parametrize('change', ['none', 'option', 'value', 'service', 'permission'])
async def test_entity_value_approval_preserves_current_options_and_exact_value(hass, entity_command_home, change):
    home, category, value, _, _ = entity_command_home
    data, token, _, _, entity_id = home
    await set_mesa(hass, home, 'confirm')
    async def respond(*args): return entity_command_wire(args[4], entity_id, category, value)
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        reply, _, provider = await run_voice(hass, home, request=respond, sentence='Set Test helper to Away')
    assert 'needs approval' in reply
    provider.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    if change == 'option': hass.states.async_set(entity_id, 'Home', {'friendly_name': 'Test helper', 'options': ['Home']})
    elif change == 'value':
        record['args'][COMMAND_ROUTING_PLAN_KEY]['proposal']['value'] = 'Home'
        record['args']['service_data']['option'] = 'Home'
    elif change == 'service': hass.services.async_remove('select', 'select_option')
    elif change == 'permission': token.permissions.domains['select'] = PermissionNode(state='RED')
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


@pytest.mark.parametrize('entity_command_home', [('select_option', 'Away')], indirect=True)
async def test_entity_value_wait_rechecks_options(hass, entity_command_home):
    home, category, value, *_ = entity_command_home
    async def respond(*args):
        body = entity_command_wire(args[4], home[-1], category, value)
        hass.states.async_set(home[-1], 'Home', {'friendly_name': 'Test helper', 'options': ['Home']})
        return body
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        _, remote, _ = await run_voice(hass, home, request=respond, sentence='Set Test helper to Away')
    remote.assert_awaited_once()
    call.assert_not_called()
    assert not home[0].store.get_pending_approvals()


@pytest.mark.parametrize('duration,allowed', [('00:05:00', True), ('05:00', False), ('five minutes', True), ('one hour and thirty minutes', True)])
async def test_explicit_timer_duration_uses_existing_named_timer_only(hass, jev_hybrid_home, duration, allowed):
    data, token, store, account, _ = jev_hybrid_home
    token.permissions.domains['timer'] = PermissionNode(state='GREEN')
    entry = er.async_get(hass).async_get_or_create('timer', 'test', 'test_timer', original_name='Test timer')
    hass.states.async_set(entry.entity_id, 'idle', {'friendly_name': 'Test timer', 'duration': '0:01:00'})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    home = data, token, store, account, entry.entity_id
    async def respond(*args): return temporal_wire(args[4], entry.entity_id, 'timer_duration')
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, request=respond, sentence=f'Start timer named Test timer for {duration}')
    remote.assert_awaited_once()
    assert provider.called is not allowed
    if allowed:
        call.assert_awaited_once_with('timer', 'start', {'entity_id': [entry.entity_id], 'duration': '01:30:00' if duration == 'one hour and thirty minutes' else '00:05:00'},
                                     blocking=True, return_response=False)
    else: call.assert_not_called()


async def test_counter_absolute_value_is_exact_and_guarded(hass, jev_hybrid_home):
    data, token, store, account, _ = jev_hybrid_home
    token.permissions.domains['counter'] = PermissionNode(state='GREEN')
    entry = er.async_get(hass).async_get_or_create('counter', 'test', 'test_counter', original_name='Test counter')
    hass.states.async_set(entry.entity_id, '0', {'friendly_name': 'Test counter'})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    home = data, token, store, account, entry.entity_id
    async def respond(*args): return scalar_reply(args[4], entry.entity_id, 'counter_value')
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, request=respond, sentence='Set Test counter to 135')
    remote.assert_awaited_once()
    provider.assert_not_called()
    call.assert_awaited_once_with('counter', 'set_value', {'entity_id': [entry.entity_id], 'value': 135},
                                 blocking=True, return_response=False)


@pytest.mark.parametrize('entity_command_home', [('input_boolean_on', None), ('switch_on', None), ('fan_on', None)], indirect=True)
async def test_entity_on_off_can_use_local_intent_without_decision_provider(hass, entity_command_home):
    home, _, _, _, (domain, service, _) = entity_command_home
    await home[0].store.async_patch_settings(voice_agent_routing_mode='local', decision_provider_id=None)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, sentence='turn on Test helper')
    provider.assert_not_called()
    remote.assert_not_called()
    call.assert_awaited_once_with(domain, service, {'entity_id': [home[-1]]}, blocking=True, return_response=False)


@pytest.mark.parametrize('entity_command_home', [('input_select_option', '2'), ('select_option', 'HDMI 2')], indirect=True)
@pytest.mark.parametrize('change', ['none', 'numeric_competitor', 'option', 'conflicting_value'])
async def test_numbered_options_share_one_decision_and_bind_the_complete_catalog(hass, entity_command_home, change):
    home, category, value, _, (domain, service, field) = entity_command_home
    data, token, _, _, entity_id = home
    hass.states.async_set(entity_id, 'Home', {'friendly_name': 'Test helper', 'options': ['Home', value]})
    await set_mesa(hass, home, 'confirm')
    async def respond(*args):
        payload = args[4]
        assert 'percentage' in payload['questions'] and 'value' in payload['questions']
        reply = entity_command_wire(payload, entity_id, category, value)
        if change == 'conflicting_value':
            reply['answers']['percentage'].update(choice='value', probabilities={'value': 1, 'none': 0})
        return reply
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        reply, remote, provider = await run_voice(hass, home, request=respond, sentence=f'Set Test helper to {value}')
    remote.assert_awaited_once()
    if change == 'conflicting_value':
        provider.assert_awaited_once()
        assert not data.store.get_pending_approvals()
        return
    assert 'needs approval' in reply
    provider.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    if change == 'numeric_competitor':
        token.permissions.domains['number'] = PermissionNode(state='GREEN')
        entry = er.async_get(hass).async_get_or_create('number', 'test', 'competing', original_name='Test helper')
        hass.states.async_set(entry.entity_id, '2', {'min': 0, 'max': 10, 'step': 1})
        async_expose_entity(hass, 'conversation', entry.entity_id, True)
    if change == 'option':
        hass.states.async_set(entity_id, 'Home', {'friendly_name': 'Test helper', 'options': ['Home']})
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)
    if change == 'none':
        call.assert_awaited_once_with(domain, service, {'entity_id': [entity_id], field: value}, blocking=True, return_response=False)


@pytest.mark.parametrize('entity_command_home', [('select_option', '2')], indirect=True)
async def test_numeric_setting_remains_numeric_with_numbered_options_present(hass, entity_command_home):
    home, *_ = entity_command_home
    data, token, store, account, option_id = home
    hass.states.async_set(option_id, '2', {'friendly_name': 'Test helper', 'options': ['2', '37']})
    light_id = next(state.entity_id for state in hass.states.async_all('light'))
    async def respond(*args):
        payload = args[4]
        reply = entity_command_wire(payload, light_id, 'light_brightness')
        reply['answers']['percentage'].update(choice='37', probabilities={
            key: float(key == '37') for key in payload['questions']['percentage']['criteria']})
        return reply
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, request=respond, sentence='I want Desk lamp at 37 percent now')
    remote.assert_awaited_once()
    provider.assert_not_called()
    call.assert_awaited_once_with('light', 'turn_on', {'entity_id': [light_id], 'brightness_pct': 37},
                                 blocking=True, return_response=False)


async def test_timer_recognition_wait_rechecks_account_before_disclosing_to_provider(hass, jev_hybrid_home):
    from custom_components.phoenix_mcp.temporal_values import async_read_temporal_value
    home = jev_hybrid_home
    data, _, store, account, _ = home
    hass.states.async_set('timer.test', 'idle')
    async def recognize(*args):
        value = await async_read_temporal_value(*args)
        assert value == {'kind': 'duration', 'value': '00:05:00'}
        await store.update(account, {'api_key': 'replacement-secret'})
        return value
    with (patch('custom_components.phoenix_mcp.temporal_values.async_read_temporal_value', side_effect=recognize),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, _ = await run_voice(hass, home, sentence='Start timer named Test for five minutes')
    remote.assert_not_called()
    call.assert_not_called()
    assert not data.store.get_pending_approvals()


@pytest.mark.parametrize('entity_command_home,expected', [
    (('media_mute', None), {'is_volume_muted': True}),
    (('media_unmute', None), {'is_volume_muted': False}),
    (('media_shuffle_on', None), {'shuffle': True}),
    (('media_repeat_one', None), {'repeat': 'one'}),
    (('fan_oscillate_off', None), {'oscillating': False}),
    (('fan_direction_reverse', None), {'direction': 'reverse'}),
    (('automation_off', None), {'stop_actions': True}),
    (('cover_open_tilt', None), {}),
    (('climate_swing_horizontal', 'Away'), {'swing_horizontal_mode': 'Away'}),
], indirect=['entity_command_home'])
async def test_fixed_controls_dispatch_exact_native_fields(hass, entity_command_home, expected):
    home, category, value, _, (domain, service, _) = entity_command_home
    async def respond(*args): return entity_command_wire(args[4], home[-1], category, value)
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, remote, provider = await run_voice(hass, home, request=respond,
                                             sentence=f'Please perform {category} for Test helper')
    remote.assert_awaited_once()
    provider.assert_not_called()
    call.assert_awaited_once_with(domain, service, {'entity_id': [home[-1]], **expected},
                                 blocking=True, return_response=False)


@pytest.mark.parametrize('entity_command_home', [('media_mute', None), ('fan_direction_reverse', None)], indirect=True)
@pytest.mark.parametrize('change', ['none', 'feature', 'arguments', 'permission'])
async def test_fixed_control_approval_rechecks_features_and_arguments(hass, entity_command_home, change):
    home, category, value, _, _ = entity_command_home
    data, token, _, _, entity_id = home
    await set_mesa(hass, home, 'confirm')
    async def respond(*args): return entity_command_wire(args[4], entity_id, category, value)
    with patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])):
        reply, _, provider = await run_voice(hass, home, request=respond, sentence=f'Please perform {category} for Test helper')
    assert 'needs approval' in reply
    provider.assert_not_called()
    record = data.store.get_pending_approvals()[0]
    if change == 'feature':
        hass.states.async_set(entity_id, 'off', {'friendly_name': 'Test helper', 'supported_features': 0})
    elif change == 'arguments':
        record['args']['service_data'] = {'is_volume_muted': False} if category == 'media_mute' else {'direction': 'forward'}
    elif change == 'permission':
        token.permissions.domains[entity_id.split('.')[0]] = PermissionNode(state='RED')
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record['tool_name'], record['args'], token, hass, data)
    assert outcome == ('allowed' if change == 'none' else 'denied')
    assert call.await_count == (1 if change == 'none' else 0)


@pytest.mark.parametrize('change', ['none', 'scope', 'exposure', 'attribute'])
async def test_joint_attribute_read_reaches_scoped_read_tool_and_rechecks_changes(hass, jev_hybrid_home, change):
    home = jev_hybrid_home
    _, token, _, _, entity_id = home
    token.permissions.domains['light'].state = 'YELLOW'
    token.cap_physical_control = 'deny'
    hass.states.async_set(entity_id, 'on', {'friendly_name': 'Desk lamp', 'brightness': 94})
    async def respond(*args):
        payload = args[4]
        assert '94' not in json.dumps(payload['state']['targets'])
        if change == 'scope': token.permissions.domains['light'].state = 'RED'
        if change == 'exposure': async_expose_entity(hass, 'conversation', entity_id, False)
        if change == 'attribute': hass.states.async_set(entity_id, 'on', {'friendly_name': 'Desk lamp'})
        return entity_command_wire(payload, entity_id, 'attribute_read', 'brightness')
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        answer, remote, provider = await run_voice(hass, home, request=respond, sentence='Tell me the brightness of Desk lamp')
    remote.assert_awaited_once()
    call.assert_not_called()
    if change == 'none':
        assert answer == 'The state is 36.9%.'
        provider.assert_not_called()
    else:
        assert '36.9' not in answer


@pytest.mark.parametrize('sentence', [
    'Show the temperature history for yesterday.',
    'What is the weather forecast for the weekend?',
    'Add a dentist appointment to my calendar next Tuesday at 10.',
    'Add milk and bread to my shopping list.',
    'Play the news podcast on the kitchen speaker and group it with the lounge.',
    'Send the custom remote sequence with three repeats.',
    'Record the camera for thirty seconds to the requested file.',
    'Set the thermostat range to 18 through 23 degrees.',
])
async def test_structured_requests_keep_original_text_and_normal_provider(hass, jev_hybrid_home, sentence):
    async def respond(*args):
        return wire_answer(args[4], 'provider', 'none', model='jev-1.13.0')
    with patch.object(mcp_view, '_dispatch_mcp', new_callable=AsyncMock) as dispatch:
        answer, _, provider = await run_voice(hass, jev_hybrid_home, sentence=sentence, request=respond)
    assert answer == 'Provider answer'
    provider.assert_awaited_once()
    assert provider.await_args.args[6] == sentence
    dispatch.assert_not_called()


@pytest.mark.parametrize('confirm', [False, True])
async def test_generic_large_catalog_dispatch_and_approval_revalidation(hass, hybrid_home, confirm):
    """A grouped plan reaches shared execution; later competitor changes invalidate approval."""
    data, token, store, _, entity_id = hybrid_home
    account = await store.add('classifier', {'base_url': 'http://example.test/v1', 'model': 'local-model'})
    await data.store.async_patch_settings(decision_provider_id=account)
    registry = er.async_get(hass)
    for i in range(50):
        other = registry.async_get_or_create('light', 'test', f'extra_{i}')
        hass.states.async_set(other.entity_id, 'off')
    if confirm:
        await set_mesa(hass, hybrid_home, 'confirm')
    async def respond(session, method, path, timeout, payload=None):
        if method == 'GET':
            return {'data': [{'id': 'local-model'}]}
        selected = {'route': 'light_on', 'target': 't0', 'target_2': 'none'}
        return {'model': 'local-model', 'answers': {
            name: {'type': 'choice', 'choice': selected[name], 'confidence': 1.0,
                   'probabilities': {key: float(key == selected[name]) for key in spec['criteria']}}
            for name, spec in payload['questions'].items()}}
    with (patch.object(mcp_view, 'unsupported_secondary_targets', AsyncMock(return_value=[])),
          patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home, request=respond)
    provider.assert_not_called()
    assert remote.await_count == 3
    assert len(remote.await_args.args[4]['state']['targets']) == 51
    if not confirm:
        call.assert_awaited_once_with('light', 'turn_on', {'entity_id': [entity_id]}, blocking=True, return_response=False)
    else:
        assert 'approval' in answer
        call.assert_not_called()
        approval = data.store.get_pending_approvals()[0]
        plan = approval['args'][COMMAND_ROUTING_PLAN_KEY]
        from custom_components.phoenix_mcp.command_execution import _decision_plan_current
        assert _decision_plan_current(plan, hass, data, token)
        registry.async_update_entity(other.entity_id, aliases={'Desk lamp'})
        assert not _decision_plan_current(plan, hass, data, token)
