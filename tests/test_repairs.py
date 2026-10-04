"""Explicit operator reconciliation never executes or rolls back an action."""

from types import SimpleNamespace

import pytest

from custom_components.phoenix_mcp.agent_recovery import AgentRecovery
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.repairs import AgentRecoveryFlow


@pytest.mark.asyncio
async def test_repair_requires_checked_outcome_and_never_clears_running_dispatch(hass):
    recovery = AgentRecovery()
    hass.data[DOMAIN] = SimpleNamespace(agent_recovery=recovery)
    await recovery.begin("access_key", "call_service", "request")
    flow = AgentRecoveryFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = "agent_recovery_request"
    flow.data = {"request_id": "request"}
    result = await flow.async_step_confirm({"checked_home_assistant": False})
    assert result["errors"] == {"base": "check_required"}
    result = await flow.async_step_confirm({"checked_home_assistant": True})
    assert result["errors"] == {"base": "operation_active"}
    recovery.interrupted("request")
    result = await flow.async_step_confirm({"checked_home_assistant": True})
    assert result["type"] == "create_entry"
    assert recovery.records("access_key") == []
