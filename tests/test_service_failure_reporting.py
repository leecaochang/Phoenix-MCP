"""A call Home Assistant fails is reported as that failure, never as a permission refusal."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import MESA_MODE_OFF, SERVICE_FAILURE_TEXT_MAX
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_common import ReportedFailure, UncertainOutcome
from custom_components.phoenix_mcp.tool_dispatch import _call_tool


def _key(entity_id: str | None = None, **caps) -> AccessKeyRecord:
    entities = {entity_id: PermissionNode(state="GREEN")} if entity_id else {}
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=PermissionTree(entities=entities), cap_search="allow", **caps)


def _data() -> PhoenixData:
    store = MagicMock()
    store.get_settings.return_value = SimpleNamespace(mesa_mode=MESA_MODE_OFF)
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=None)


def _entity(hass, domain: str) -> str:
    entity_id = er.async_get(hass).async_get_or_create(domain, "test", "x", suggested_object_id="x").entity_id
    hass.states.async_set(entity_id, "off")
    return entity_id


async def _failing_call(hass, domain: str, service: str, error: Exception, key: AccessKeyRecord | None = None, target: bool = True):
    entity_id = _entity(hass, domain)
    hass.services.async_register(domain, service, AsyncMock(side_effect=error))
    arguments: dict = {"service": {"domain": domain, "name": service}}
    if target:
        arguments["targets"] = [{"kind": "entity", "ids": [entity_id]}]
    return await _call_tool("call_service", arguments, key or _key(entity_id), hass, _data())


def _message(result: tuple) -> str:
    body = json.loads(result[0]["content"][0]["text"])
    assert (body["status"], body["retry_safe"]) == ("unknown", False)
    return body["message"]


async def test_a_failed_call_is_reported_with_its_reason(hass):
    result = await _failing_call(hass, "light", "turn_on", HomeAssistantError("The bridge did not answer"))
    assert result[1] == "invalid_request"
    assert isinstance(result[0], ReportedFailure) and result[0]["isError"] is True
    assert "The bridge did not answer. It may have applied in part" in _message(result)
    # Approval closes such a run as interrupted, not failed.
    assert isinstance(result[0], UncertainOutcome)


async def test_the_reason_is_scrubbed_before_its_bound(hass):
    secret = "abcdefghijklmnopqrstuvwxyz0123456789"
    reason = f"Failed at http://192.168.1.50/x with lock.front_door, password={secret} " + "y" * 900
    message = _message(await _failing_call(hass, "light", "turn_on", HomeAssistantError(reason)))
    assert "192.168.1.50" not in message and "lock.front_door" not in message and secret not in message
    assert len(message) < SERVICE_FAILURE_TEXT_MAX + 150


async def test_a_refusal_by_home_assistant_stays_a_refusal(hass):
    content, outcome, _ = await _failing_call(hass, "light", "turn_on", Unauthorized())
    assert outcome == "denied" and content["content"][0]["text"] == "Forbidden."


async def test_a_validation_error_still_explains_the_argument(hass):
    content, outcome, _ = await _failing_call(hass, "light", "turn_on", ServiceValidationError("brightness is too high"))
    assert outcome == "invalid_request" and "brightness is too high" in content["content"][0]["text"]
    assert not isinstance(content, ReportedFailure)


@pytest.mark.parametrize("domain", ["scene", "script", "automation"])
async def test_a_service_running_other_entities_gives_no_reason(hass, domain):
    result = await _failing_call(hass, domain, "turn_on", HomeAssistantError("Unlocking the Hidden Door failed"))
    assert result[1] == "invalid_request"
    assert "Hidden Door" not in _message(result) and f"{domain}.turn_on" in _message(result)


async def test_a_dual_gate_service_reports_failure_without_its_reason(hass):
    key = _key(cap_restart="allow")
    result = await _failing_call(hass, "homeassistant", "stop", HomeAssistantError("Supervisor at 10.0.0.2 refused"), key, target=False)
    assert result[1] == "invalid_request"
    assert "Supervisor" not in _message(result)


async def test_a_reload_gives_its_reason(hass):
    key = _key(cap_yaml_edit="allow")
    result = await _failing_call(hass, "automation", "reload", HomeAssistantError("Config has an error"), key, target=False)
    assert result[1] == "invalid_request"
    assert "Config has an error" in _message(result)
