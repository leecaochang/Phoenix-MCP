"""Exercise HA household identity at dispatch, waits and saved approval execution."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import make_mocked_request
import pytest
from homeassistant.auth.const import GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.core import Context
from homeassistant.helpers import llm

from custom_components.phoenix_mcp import ai_task, assist_api, mcp_view, voice_agent
from custom_components.phoenix_mcp.admin_view import _approve_approval
from custom_components.phoenix_mcp.approvals import async_create_pending_approval, get_approval
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import CAPABILITY_NAMES, DOMAIN, HOUSEHOLD_CAPABILITIES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.helpers import effective_cap
from custom_components.phoenix_mcp.household_authority import household_authority, household_scope
from custom_components.phoenix_mcp.policy_engine import Permission, resolve, resolve_registry_access
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.request_authority import AuthorityChangedError, check_request_authority
from custom_components.phoenix_mcp.token_store import TokenStore


async def _environment(hass):
    store = TokenStore(hass, AsyncMock())
    token, _ = await store.async_create_token("household", "admin", rate_limit_requests=0)
    token.pass_through = True
    for cap in CAPABILITY_NAMES:
        setattr(token, cap, "allow")
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
    hass.states.async_set("light.household", "off")
    async with household_scope(hass, Context(user_id=user.id)):
        record = await async_create_pending_approval(data.store, token_id=token.id, token_name=token.name,
            tool_name="call_service", cap_name="cap_physical_control", args={}, diff={}, request_id="origin", client_ip="assist")
    assert get_approval(data.store, record.id).ha_user_id == user.id
    await hass.auth.async_update_user(user, group_ids=[GROUP_ID_READ_ONLY])
    await hass.async_block_till_done()
    request = make_mocked_request("POST", "/approve")
    request[KEY_HASS_USER] = hass_admin_user
    request["phoenix_mcp_rid"] = "accept"
    async def executor(*args):
        assert household_authority.get().user_id == user.id
        assert resolve("light.household", token, hass) == Permission.READ
        return {"isError": True, "content": [{"type": "text", "text": "Forbidden."}]}, "denied", "call_service"
    with patch.object(mcp_view, "async_execute_approved_tool", side_effect=executor):
        await _approve_approval(hass, request, record.id)
    assert get_approval(data.store, record.id).status == "failed"


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
