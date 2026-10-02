"""Exercise HA household identity at dispatch, waits and saved approval execution."""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import make_mocked_request
import pytest
from homeassistant.auth.const import GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.core import Context
from homeassistant.helpers import llm
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import ai_task, assist_api, mcp_view, voice_agent
from custom_components.phoenix_mcp.admin_view import _approve_approval
from custom_components.phoenix_mcp.approvals import async_create_pending_approval, get_approval
from custom_components.phoenix_mcp.approval_targets import bind_targets
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import CAPABILITY_NAMES, DOMAIN, HOUSEHOLD_CAPABILITIES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.helpers import effective_cap
from custom_components.phoenix_mcp.household_authority import household_authority, household_scope
from custom_components.phoenix_mcp.policy_engine import Permission, resolve, resolve_registry_access
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.request_authority import AuthorityChangedError, check_request_authority
from custom_components.phoenix_mcp.token_store import PermissionNode, TokenStore


async def _environment(hass):
    store = TokenStore(hass, AsyncMock())
    token, _ = await store.async_create_token("household", "admin", rate_limit_requests=0)
    token.pass_through = True
    for cap in CAPABILITY_NAMES:
        setattr(token, cap, "allow")
    # Exercise household authority with an explicit supported MESA mode; an
    # unavailable advisory runtime would otherwise refuse every service call.
    await store.async_patch_settings(mesa_mode="off")
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    return data, token


def _context(user_id):
    return llm.LLMContext(platform="conversation", context=Context(user_id=user_id),
                          language="en", assistant="conversation", device_id=None)


@pytest.mark.parametrize("identity", ["admin", "user", "unknown"])
@pytest.mark.parametrize("pass_through", [False, True])
async def test_household_caps_intersect_token_even_in_pass_through(hass, hass_admin_user, identity, pass_through):
    _, token = await _environment(hass)
    token.pass_through = pass_through
    user = hass_admin_user if identity == "admin" else await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    context = Context(user_id=user.id) if identity != "unknown" else None
    async with household_scope(hass, context):
        for cap in CAPABILITY_NAMES:
            assert effective_cap(token, cap) == ("allow" if identity == "admin" or cap in HOUSEHOLD_CAPABILITIES else "deny"), cap
        token.cap_physical_control = "confirm"
        assert effective_cap(token, "cap_physical_control") == "confirm"
    assert household_authority.get() is None


@pytest.mark.parametrize("resolver", [resolve, resolve_registry_access])
@pytest.mark.parametrize("group,expected", [(GROUP_ID_USER, Permission.WRITE), (GROUP_ID_READ_ONLY, Permission.READ), (None, Permission.NO_ACCESS)])
async def test_user_entity_permissions_intersect_phoenix_grants(hass, hass_admin_user, resolver, group, expected):
    _, token = await _environment(hass)
    user = await hass.auth.async_create_user("member", group_ids=[group] if group else [])
    assert not user.is_admin
    hass.states.async_set("light.household", "off")
    async with household_scope(hass, Context(user_id=user.id)):
        assert resolver("LIGHT.Household", token, hass) == expected


@pytest.mark.parametrize("identity", ["admin", "user", "unknown"])
async def test_assist_gate_and_audit_use_requesting_user(hass, hass_admin_user, identity):
    data, token = await _environment(hass)
    user = hass_admin_user if identity == "admin" else await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    tool = assist_api.PhoenixAssistTool("get_logs", "", {}, token.id)
    downstream = AsyncMock(return_value=({"content": [{"type": "text", "text": "private-log-fixture"}]}, "allowed", "get_logs"))
    # Keep the owning capability gate, replace only the admin-only backend read.
    with patch.object(mcp_view, "_tool_log_query", downstream):
        result = await tool.async_call(hass, llm.ToolInput(tool_name="get_logs", tool_args={}), _context(user.id if identity != "unknown" else None))
    if identity == "admin":
        assert "private-log-fixture" in json.dumps(result)
        downstream.assert_awaited_once()
    else:
        assert "private-log-fixture" not in json.dumps(result)
        assert result.get("error")
        downstream.assert_not_awaited()
    row = data.audit.query()[-1]
    assert row.ha_user_id == (user.id if identity != "unknown" else None)
    assert row.client_ip == "assist"


async def test_assist_listing_withholds_admin_tools_for_regular_user(hass, hass_admin_user):
    data, token = await _environment(hass)
    await data.store.async_patch_settings(assist_bound_token_id=token.id)
    user = await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    api = assist_api.PhoenixAssistAPI(hass, data)
    member = await api.async_get_api_instance(_context(user.id))
    admin = await api.async_get_api_instance(_context(hass_admin_user.id))
    member_names = {t.name for t in member.tools}
    assert {"get_state", "call_service"} <= member_names
    assert "get_logs" not in member_names and "get_config" not in member_names
    assert "get_logs" in {t.name for t in admin.tools}


@pytest.mark.parametrize("identity", ["admin", "user", "unknown"])
@pytest.mark.parametrize("announce_all", [False, True])
async def test_shared_token_history_requires_household_admin(hass, hass_admin_user, identity, announce_all):
    data, token = await _environment(hass)
    token.announce_all_tools = announce_all
    await data.store.async_patch_settings(assist_bound_token_id=token.id)
    user = hass_admin_user if identity == "admin" else await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    context = _context(user.id if identity != "unknown" else None)
    data.audit.record(request_id="private", token_id=token.id, token_name=token.name,
                      method="write_file", resource="file:private-path", outcome="allowed", client_ip="external",
                      settings=data.store.get_settings())
    record = await async_create_pending_approval(data.store, token_id=token.id, token_name=token.name,
        tool_name="restart_ha", cap_name="cap_restart", args={}, diff={}, request_id="external", client_ip="external")
    api = await assist_api.PhoenixAssistAPI(hass, data).async_get_api_instance(context)
    restricted = {"get_audit_summary", "get_approval_status", "wait_for_approval"}
    assert restricted.intersection(tool.name for tool in api.tools) == (restricted if identity == "admin" else set())
    for name, args in (("get_audit_summary", {}), ("get_approval_status", {}),
                       ("get_approval_status", {"approval_id": record.id}),
                       ("wait_for_approval", {"approval_ids": ["missing"]})):
        tool = assist_api.PhoenixAssistTool(name, "", {}, token.id)
        result = await tool.async_call(hass, llm.ToolInput(tool_name=name, tool_args=args), context)
        if identity != "admin":
            assert result == {"error": "Forbidden."}
        elif name == "get_audit_summary":
            assert "private-path" in json.dumps(result)
        elif name == "get_approval_status":
            assert record.id in json.dumps(result)


@pytest.mark.parametrize("unavailable", ["removed_user", "inactive_user", "kill_switch"])
async def test_assist_unavailable_turn_has_no_tools_and_an_accurate_prompt(hass, hass_admin_user, unavailable):
    data, token = await _environment(hass)
    await data.store.async_patch_settings(assist_bound_token_id=token.id)
    user = await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    if unavailable == "removed_user":
        await hass.auth.async_remove_user(user)
    elif unavailable == "inactive_user":
        await hass.auth.async_deactivate_user(user)
    else:
        await data.store.async_patch_settings(kill_switch=True)
    instance = await assist_api.PhoenixAssistAPI(hass, data).async_get_api_instance(_context(user.id))
    assert instance.tools == []
    assert "currently bound" not in instance.api_prompt
    assert ("disabled" if unavailable == "kill_switch" else "user is no longer available") in instance.api_prompt


@pytest.mark.parametrize("change", ["remove", "deactivate", "demote"])
async def test_household_identity_change_during_wait_stops_disclosure(hass, hass_admin_user, change):
    user = await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    async with household_scope(hass, Context(user_id=user.id)) as authority:
        if change == "remove":
            await hass.auth.async_remove_user(user)
        elif change == "deactivate":
            await hass.auth.async_deactivate_user(user)
        else:
            await hass.auth.async_update_user(user, group_ids=[GROUP_ID_READ_ONLY])
        await hass.async_block_till_done()
        with pytest.raises(AuthorityChangedError):
            check_request_authority()
    with pytest.raises(AuthorityChangedError):
        authority.check()


async def test_deleted_id_never_degrades_to_unknown_satellite_authority(hass):
    async with household_scope(hass, Context(user_id="missing-user")):
        with pytest.raises(AuthorityChangedError):
            check_request_authority()


async def test_approval_rechecks_original_user_after_admin_accepts(hass, hass_admin_user):
    data, token = await _environment(hass)
    user = await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    entity = er.async_get(hass).async_get_or_create("light", "test", "household")
    hass.states.async_set(entity.entity_id, "off")
    effects = []
    async def service(call):
        effects.append(call)
    hass.services.async_register("light", "turn_on", service)
    async with household_scope(hass, Context(user_id=user.id)):
        record = await async_create_pending_approval(data.store, token_id=token.id, token_name=token.name,
            tool_name="call_service", cap_name="cap_physical_control",
            args=bind_targets({"domain": "light", "service": "turn_on", "entity_id": entity.entity_id}, [entity.entity_id], hass),
            diff={}, request_id="origin", client_ip="assist")
    assert get_approval(data.store, record.id).ha_user_id == user.id
    await hass.auth.async_update_user(user, group_ids=[GROUP_ID_READ_ONLY])
    await hass.async_block_till_done()
    request = make_mocked_request("POST", "/approve")
    request[KEY_HASS_USER] = hass_admin_user
    request["phoenix_mcp_rid"] = "accept"
    response = await _approve_approval(hass, request, record.id)
    assert response.status == 200
    assert effects == []
    assert get_approval(data.store, record.id).status == "failed"


@pytest.mark.parametrize("identity", ["admin", "user", "unknown", "bearer"])
@pytest.mark.parametrize("admin_only", [False, True])
@pytest.mark.parametrize("pass_through", [False, True])
async def test_service_authority_uses_native_admin_registration(
    hass, hass_admin_user, identity, admin_only, pass_through,
):
    data, token = await _environment(hass)
    token.pass_through = pass_through
    entity = er.async_get(hass).async_get_or_create("light", "test", "household")
    token.permissions.entities[entity.entity_id] = PermissionNode(state="GREEN")
    hass.states.async_set(entity.entity_id, "off")
    effects = []
    async def service(call):
        effects.append(call.context.user_id)
    register = async_register_admin_service if admin_only else hass.services.async_register
    if admin_only:
        register(hass, "homeassistant", "reload_config_entry", service, schema=None)
    else:
        register("homeassistant", "reload_config_entry", service)
    user = hass_admin_user if identity == "admin" else await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    args = {"service": {"domain": "homeassistant", "name": "reload_config_entry"},
            "targets": [{"kind": "entity", "ids": [entity.entity_id]}]}
    if identity == "bearer":
        result, outcome, _ = await mcp_view._tool_call_service(args, token, hass, data)
    else:
        tool = assist_api.PhoenixAssistTool("call_service", "", {}, token.id)
        result = await tool.async_call(hass, llm.ToolInput(tool_name="call_service", tool_args=args),
                                      _context(user.id if identity != "unknown" else None))
        outcome = data.audit.query()[-1].outcome
    permitted = not admin_only or identity in {"admin", "bearer"}
    assert outcome == ("allowed" if permitted else "denied"), result
    assert effects == ([user.id if identity in {"admin", "user"} else None] if permitted else [])


@pytest.mark.parametrize("native_tool,domain,service_name", [
    ("turn", "light", "turn_on"),
    ("volume", "media_player", "volume_set"),
    ("timers", "timer", "cancel"),
    ("broadcast", "assist_satellite", "announce"),
])
@pytest.mark.parametrize("identity,admin_only", [("user", False), ("unknown", True)])
async def test_native_actions_preserve_household_service_authority(
    hass, native_tool, domain, service_name, identity, admin_only,
):
    from custom_components.phoenix_mcp.const import ANNOUNCE_BIT
    from custom_components.phoenix_mcp.tools import native

    data, token = await _environment(hass)
    user = await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    entity = er.async_get(hass).async_get_or_create(domain, "test", "household", original_name="Household")
    hass.states.async_set(entity.entity_id, "off", {
        "friendly_name": "Household", "volume_level": 0.2, "supported_features": ANNOUNCE_BIT,
    })
    effects = []
    async def service(call):
        effects.append(call.context)
    if admin_only:
        async_register_admin_service(hass, domain, service_name, service, schema=None)
    else:
        hass.services.async_register(domain, service_name, service)
    context = Context(user_id=user.id if identity == "user" else None)
    async with household_scope(hass, context):
        if native_tool == "turn":
            result, outcome, _ = await native._tool_intent_action(
                "HassTurnOn", domain, service_name, {}, [entity.entity_id], hass, token)
        elif native_tool == "volume":
            result, outcome, _ = await native._tool_hass_set_volume_relative(
                {"name": "Household", "volume_step": 10}, token, hass, data)
        elif native_tool == "timers":
            result, outcome, _ = await native._tool_hass_cancel_all_timers({}, token, hass)
        else:
            result, outcome, _ = await native._tool_hass_broadcast({"message": "hello"}, token, hass)
    assert outcome == ("denied" if admin_only else "allowed"), result
    assert effects == ([] if admin_only else [context])


@pytest.mark.parametrize("native_tool", [False, True])
async def test_service_registration_change_after_gate_cannot_gain_system_authority(hass, native_tool):
    from custom_components.phoenix_mcp.tools import native

    data, token = await _environment(hass)
    entity = er.async_get(hass).async_get_or_create("light", "test", "household")
    hass.states.async_set(entity.entity_id, "off")
    effects = []
    async def service(call):
        effects.append(call)
    hass.services.async_register("light", "turn_on", service)
    module = native if native_tool else mcp_view
    gate_name = "_mesa_gate_native" if native_tool else "async_apply_mesa_to_call"
    original_gate = getattr(module, gate_name)
    async def gate(*args, **kwargs):
        result = await original_gate(*args, **kwargs)
        async_register_admin_service(hass, "light", "turn_on", service, schema=None)
        return result
    async with household_scope(hass, Context()):
        with patch.object(module, gate_name, side_effect=gate):
            if native_tool:
                result, outcome, _ = await native._tool_intent_action(
                    "HassTurnOn", "light", "turn_on", {}, [entity.entity_id], hass, token)
            else:
                result, outcome, _ = await mcp_view._execute_call_service(
                    {"domain": "light", "service": "turn_on", "entity_id": entity.entity_id}, token, hass, data)
    assert outcome == "denied", result
    assert effects == []


@pytest.mark.parametrize("change", ["remove", "deactivate"])
async def test_inactive_household_approval_is_a_known_refusal(hass, hass_admin_user, change, caplog):
    from custom_components.phoenix_mcp.approvals import async_prune_terminal_approvals
    from custom_components.phoenix_mcp.const import APPROVAL_PAYLOAD_RETENTION_SECONDS

    data, token = await _environment(hass)
    user = await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    async with household_scope(hass, Context(user_id=user.id)):
        record = await async_create_pending_approval(data.store, token_id=token.id, token_name=token.name,
            tool_name="call_service", cap_name="cap_physical_control", args={"private": "marker"},
            diff={}, request_id="origin", client_ip="assist")
    if change == "remove":
        await hass.auth.async_remove_user(user)
    else:
        await hass.auth.async_deactivate_user(user)
    request = make_mocked_request("POST", "/approve")
    request[KEY_HASS_USER] = hass_admin_user
    request["phoenix_mcp_rid"] = "accept"
    with patch.object(mcp_view, "async_execute_approved_tool", AsyncMock()) as execute:
        response = await _approve_approval(hass, request, record.id)
    execute.assert_not_awaited()
    assert response.status == 200
    saved = get_approval(data.store, record.id)
    assert saved.status == "failed"
    assert saved.rejected_reason == "execution_failed"
    assert saved.execution_started_at is None
    assert "No action was dispatched" in response.text
    assert "may have" not in response.text
    assert "Approval execution failed" not in caplog.text
    assert record.id not in data.approvals_in_progress
    later = utcnow() + timedelta(seconds=APPROVAL_PAYLOAD_RETENTION_SECONDS + 1)
    with patch("custom_components.phoenix_mcp.approvals.utcnow", return_value=later):
        await async_prune_terminal_approvals(data.store)
    assert get_approval(data.store, record.id).args == {}


@pytest.mark.parametrize("identity", ["admin", "user", "unknown"])
async def test_approval_keeps_original_identity_at_native_admin_service(hass, hass_admin_user, identity):
    data, token = await _environment(hass)
    user = hass_admin_user if identity == "admin" else await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    entity = er.async_get(hass).async_get_or_create("light", "test", "household")
    hass.states.async_set(entity.entity_id, "off")
    effects = []
    async def service(call):
        effects.append(call.context.user_id)
    async_register_admin_service(hass, "light", "turn_on", service, schema=None)
    async with household_scope(hass, Context(user_id=user.id if identity != "unknown" else None)):
        record = await async_create_pending_approval(data.store, token_id=token.id, token_name=token.name,
            tool_name="call_service", cap_name="cap_physical_control",
            args=bind_targets({"domain": "light", "service": "turn_on", "entity_id": entity.entity_id}, [entity.entity_id], hass),
            diff={}, request_id="origin", client_ip="assist")
    request = make_mocked_request("POST", "/approve")
    request[KEY_HASS_USER] = hass_admin_user
    request["phoenix_mcp_rid"] = "accept"
    response = await _approve_approval(hass, request, record.id)
    assert response.status == 200
    assert effects == ([user.id] if identity == "admin" else []), response.text
    assert get_approval(data.store, record.id).status == ("approved" if identity == "admin" else "failed")


@pytest.mark.parametrize("ingress", ["voice", "ai_task"])
async def test_owned_provider_loop_keeps_household_restriction(hass, hass_admin_user, ingress):
    data, token = await _environment(hass)
    user = await hass.auth.async_create_user("member", group_ids=[GROUP_ID_USER])
    await data.store.async_patch_settings(voice_agent_enabled=True, voice_agent_token_id=token.id,
        voice_agent_provider_id="provider", voice_agent_model="model", ai_task_enabled=True,
        ai_task_token_id=token.id, ai_task_provider_id="provider", ai_task_model="model")
    cfg = SimpleNamespace(model="model")
    secret_store = MagicMock()
    secret_store.resolve.return_value = cfg
    async def run(*args, **kwargs):
        response, _, _, outcome = await mcp_view._dispatch_mcp("tools/call", "tool", {"name": "get_logs", "arguments": {}}, token, hass, data, ingress, "")
        assert outcome == "denied"
        assert response["result"]["isError"]
        assert household_authority.get().user_id == (user.id if ingress == "voice" else None)
        return "denied"
    module = voice_agent if ingress == "voice" else ai_task
    loop = "async_run_voice_turn" if ingress == "voice" else "async_run_ai_task"
    with patch("custom_components.phoenix_mcp.agentcli._get_secret_store", AsyncMock(return_value=secret_store)), \
         patch("custom_components.phoenix_mcp.agentcli.build_provider", return_value=MagicMock()), \
         patch("custom_components.phoenix_mcp.agentcli."+loop, side_effect=run), \
         patch.object(module, "async_get_clientsession", return_value=MagicMock()):
        if ingress == "voice":
            assert await module.async_voice_answer(hass, data, "read logs", context=Context(user_id=user.id)) == "denied"
        else:
            assert await module.async_generate_ai_task_data(hass, data, "read logs", None) == "denied"
