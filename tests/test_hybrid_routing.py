"""Decision-derived commands exercise the real Voice dispatcher and saved approvals."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
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
async def hybrid_home(hass, token_store):
    token, _ = await token_store.async_create_token("hybrid", "test", rate_limit_requests=0)
    token.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    token.cap_physical_control = "allow"
    data = PhoenixData(store=token_store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    hass.data["_phoenix_agentcli_store"] = store
    decision = await store.add("typesafe", {"api_key": "decision-secret", "model": "jev-latest"})
    chat = await store.add("deepseek", {"api_key": "chat-secret", "model": "chat"})
    await token_store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id=chat, voice_agent_model="chat", voice_agent_routing_mode="hybrid",
        decision_provider_id=decision, mesa_mode="off")
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", "desk", original_name="Desk lamp")
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Desk lamp", "supported_color_modes": ["brightness"]})
    yield data, token, store, decision, entry.entity_id
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


def wire_answer(payload, category="light_on", target="t0", percent=50):
    choices = {"route": category, "target": target,
               "brightness_tens": str(percent // 10), "brightness_units": str(percent % 10)}
    return {"model": "test-model", "answers": {
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
    ("light_brightness", "turn_on", {"brightness_pct": 0}),
    ("light_brightness", "turn_on", {"brightness_pct": 50}),
    ("light_brightness", "turn_on", {"brightness_pct": 100}),
])
@pytest.mark.parametrize("routing_mode", ["hybrid", "auto"])
async def test_two_requests_dispatch_once_and_do_not_duplicate_shadow(hass, hybrid_home, category, service, extra, routing_mode):
    await hybrid_home[0].store.async_patch_settings(voice_agent_routing_mode=routing_mode)
    data, token, _, _, entity_id = hybrid_home
    await data.store.async_patch_settings(decision_shadow_enabled=True)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        answer, remote, provider = await run_voice(hass, hybrid_home, category=category, percent=extra.get("brightness_pct", 50))
    assert answer == "Command sent."
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
    assert answer == "Command sent."
    call.assert_awaited_once()
    remote.assert_not_called()
    provider.assert_not_called()
    assert hybrid_home[0].decision_shadow.summary()["hybrid"] == {"evaluations": 0, "reasons": {}}


@pytest.mark.parametrize("case,expected_calls,reason", [
    ("provider", 1, "provider_fallback"), ("decline", 2, "target_declined"),
    ("low_category", 1, "category_low_probability"), ("low_target", 2, "target_low_probability"),
    ("low_zero_category", 1, "category_low_probability"),
    ("malformed", 1, "malformed"), ("timeout", 1, "timeout"), ("unavailable", 1, "unavailable"),
    ("non_english", 0, "unsupported_language"), ("local_timeout", 0, "local_unavailable"),
    ("trigger", 0, "local_unavailable"), ("no_scope", 1, "no_target"), ("busy", 0, "busy"),
    ("brightness_declined", 2, "brightness_declined"),
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


@pytest.mark.parametrize("change", ["none", "account", "deleted", "settings", "revoked", "scope", "name", "new_candidate", "mesa"])
@pytest.mark.parametrize("percent", [0, 50, 100])
async def test_saved_decision_approval_revalidates_account_and_full_catalog(hass, hybrid_home, change, percent):
    data, token, _, _, entity_id = hybrid_home
    await set_mesa(hass, hybrid_home, "confirm")
    with patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])):
        answer, _, _ = await run_voice(hass, hybrid_home, category="light_brightness", percent=percent)
    assert "needs approval" in answer
    record = data.store.get_pending_approvals()[0]
    assert record["args"]["service_data"] == {"brightness_pct": percent}
    await mutate(hass, hybrid_home, change)
    with (patch.object(mcp_view, "unsupported_secondary_targets", AsyncMock(return_value=[])),
          patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as call):
        _, outcome, _ = await mcp_view.async_execute_approved_tool(record["tool_name"], record["args"], token, hass, data)
    if change == "none":
        assert outcome == "allowed"
        call.assert_awaited_once_with("light", "turn_on", {"entity_id": [entity_id], "brightness_pct": percent},
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


@pytest.mark.parametrize("change", ["account", "settings", "revoked"])
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


async def test_cancelled_decision_never_dispatches_or_falls_back(hass, hybrid_home):
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
    ("zh-Hans", "请将台灯亮度设置为百分之三十七", "指令已发送。"),
    ("es", "pon la lámpara al treinta y siete por ciento", "Orden enviada."),
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
                                                 category="light_brightness", percent=37)
    assert answer == reply
    assert remote.await_count == 2
    assert all(args.args[4]["state"]["language"] == ("zh-CN" if language == "zh-Hans" else "es") for args in remote.await_args_list)
    call.assert_awaited_once_with("light", "turn_on", {"entity_id": [entity_id], "brightness_pct": 37}, blocking=True, return_response=False)
    provider.assert_not_called()
