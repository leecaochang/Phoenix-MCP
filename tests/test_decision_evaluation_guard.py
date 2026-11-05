"""One exception mapping for every provider evaluation, per entry point and in isolation."""

import asyncio
import logging
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.runtime_fixtures import patch_readers
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.llm import accounts
from custom_components.phoenix_mcp.routing import tool_routing
from custom_components.phoenix_mcp.routing.command_routing import async_evaluate_command
from custom_components.phoenix_mcp.routing.decision_batch import async_preview_batch
from custom_components.phoenix_mcp.routing.decision_context import async_preview_context
from custom_components.phoenix_mcp.routing.decision_provider import (
    DecisionError, DecisionValidationError, _evaluate_guarded,
)
from custom_components.phoenix_mcp.routing.decision_reference import async_preview_reference
from custom_components.phoenix_mcp.routing.decision_shadow import async_preview_decision
from custom_components.phoenix_mcp.routing.decision_targets import CatalogLimitError

LOGGER = logging.getLogger("phoenix-guard-test")
MISS = {"reason": "no_match", "proposal": None}
SENTENCE = "Switch on the desk lamp"
PERCENT_SENTENCE = "set Desk lamp to thirty seven percent brightness"
CONTEXT_HISTORY = [{"role": "user", "content": "We are discussing how brightness works."},
                   {"role": "assistant", "content": "Brightness is expressed as a percentage."}]
HISTORY = [{"role": "user", "content": "turn on Desk lamp"}, {"role": "assistant", "content": "Command sent."}]
TOOL_CALLS = [{"id": "c1", "name": "get_state", "input": {}}]
TOOL_HISTORY = [{"role": "user", "content": "Investigate the lamp."},
                {"role": "assistant", "content": [{"type": "tool_use", **call} for call in TOOL_CALLS]}]
TOOL_CATALOG = [{"name": "get_state", "description": "Read one entity.", "inputSchema": {"type": "object"}}]


def reason_of(value):
    return value


async def guarded(exc, **kwargs):
    async def work():
        raise exc
    return await _evaluate_guarded(LOGGER, "phase", work(), reason_of, **kwargs)


async def test_the_outcome_of_a_clean_evaluation_is_returned_untouched():
    outcome = object()

    async def work():
        return outcome
    assert await _evaluate_guarded(LOGGER, "phase", work(), reason_of) is outcome


@pytest.mark.parametrize(("exc", "kwargs", "expected", "logged"), [
    (DecisionError("too_many_targets"), {}, "too_many_targets", False),
    (DecisionError("too_many_targets"), {"invalid_reason": "bad"}, "too_many_targets", False),
    (TimeoutError(), {}, "timeout", False),
    (TimeoutError(), {"invalid_reason": "bad"}, "timeout", False),
    (ValueError("x"), {}, "unavailable", True),
    (RecursionError("x"), {}, "unavailable", True),
    (ValueError("x"), {"invalid_reason": "bad"}, "bad", False),
    (RecursionError("x"), {"invalid_reason": "bad"}, "bad", False),
    (RuntimeError("x"), {}, "unavailable", True),
    (RuntimeError("x"), {"invalid_reason": "bad"}, "unavailable", True),
])
async def test_each_failure_maps_to_one_reason(caplog, exc, kwargs, expected, logged):
    with caplog.at_level(logging.WARNING, LOGGER.name):
        assert await guarded(exc, **kwargs) == expected
    assert ("phase=phase" in caplog.text) is logged


async def test_rejected_replaces_only_the_decision_error_outcome():
    seen = []
    def rejected(error):
        seen.append(error)
        return "rejected"
    error = DecisionError("x")
    assert await guarded(error, rejected=rejected) == "rejected"
    assert seen == [error]
    assert await guarded(TimeoutError(), rejected=rejected) == "timeout"
    assert await guarded(ValueError("x"), rejected=rejected, invalid_reason="bad") == "bad"


async def test_an_internal_failure_logs_its_type_and_never_its_text(caplog):
    with caplog.at_level(logging.WARNING, LOGGER.name):
        await guarded(RuntimeError("Bearer private-provider-token"))
    assert "type=RuntimeError" in caplog.text
    assert "private-provider-token" not in caplog.text


async def test_cancellation_is_never_mapped():
    with pytest.raises(asyncio.CancelledError):
        await guarded(asyncio.CancelledError())


@pytest.fixture
async def home(hass, access_key_store):
    access_key, _ = await access_key_store.async_create_access_key("guard", "test")
    access_key.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    data = PhoenixData(store=access_key_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = accounts.AgentCliStore(AsyncMock())
    account = await store.add("typesafe", {"api_key": "private-test-key", "model": "jev-latest"})
    hass.data["_phoenix_agentcli_store"] = store
    await access_key_store.async_patch_settings(decision_provider_id=account, command_routing_timeout_ms=5000, agentcli_tool_routing_mode="observe")
    with patch_readers("custom_components.phoenix_mcp.tool_catalog.build_mcp_tool_list", return_value=deepcopy(TOOL_CATALOG)):
        yield data, access_key, store, account
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


async def _batch(hass, data, access_key):
    return await async_preview_batch(hass, data, access_key, SENTENCE, "en", MISS)


async def _context_review(hass, data, access_key):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    entry = er.async_get(hass).async_get_or_create("light", "test", "desk", original_name="Desk lamp")
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Desk lamp", "supported_color_modes": ["brightness"]})
    local = await async_evaluate_command(hass, data, access_key, PERCENT_SENTENCE, "en", preview=True)
    assert local["proposal"]
    return await async_preview_context(hass, data, access_key, PERCENT_SENTENCE, "en", CONTEXT_HISTORY, local)


async def _context_decision(hass, data, access_key):
    return await async_preview_context(hass, data, access_key, SENTENCE, "en", HISTORY, MISS)


async def _reference(hass, data, access_key):
    return await async_preview_reference(hass, data, access_key, "turn it off", "en", HISTORY, MISS)


async def _shadow(hass, data, access_key):
    return await async_preview_decision(hass, data, access_key, SENTENCE, "en", MISS)


async def _tool_review(hass, data, access_key):
    turn = tool_routing.create_review_state(data, access_key, "chat")
    review = await tool_routing.async_review_tools(hass, data, turn, surface="chat", language="en",
                                                   messages=deepcopy(TOOL_HISTORY), calls=deepcopy(TOOL_CALLS),
                                                   system="Respect the user restrictions.", remaining_rounds=2)
    return {"reason": review.reason}


@pytest.mark.parametrize(("evaluate", "decision", "timeout", "value", "internal"), [
    (_batch, "too_many_targets", "timeout", "unavailable", "unavailable"),
    (_context_review, "too_many_targets", "timeout", "unavailable", "unavailable"),
    (_context_decision, "context_unavailable", "timeout", "context_unavailable", "unavailable"),
    (_reference, "too_many_targets", "timeout", "context_unavailable", "unavailable"),
    (_shadow, "too_many_targets", "timeout", "unavailable", "unavailable"),
    (_tool_review, "too_many_targets", "timeout", "unavailable", "unavailable"),
])
async def test_every_entry_point_keeps_its_own_mapping(hass, home, evaluate, decision, timeout, value, internal):
    data, access_key, *_ = home
    for failure, expected in ((DecisionError("too_many_targets"), decision), (TimeoutError(), timeout),
                              (ValueError("x"), value), (RuntimeError("x"), internal)):
        with patch_readers("custom_components.phoenix_mcp.llm.accounts._get_secret_store", AsyncMock(side_effect=failure)):
            result = await evaluate(hass, data, access_key)
        await hass.async_block_till_done()
        assert result["reason"] == expected, type(failure).__name__


async def test_a_failed_tool_review_reports_the_authority_change_it_raced(hass, home):
    data, access_key, store, account = home

    async def fail_after_account_change(*_args):
        assert await store.set_model(account, "jev-1.13.0")
        raise RuntimeError("x")

    with patch.object(tool_routing.DecisionProvider, "select_tool", fail_after_account_change):
        assert (await _tool_review(hass, data, access_key))["reason"] == "authority_changed"


async def test_the_batch_preview_reports_catalog_limits_and_validation_checks(hass, home):
    data, access_key, *_ = home
    limit = CatalogLimitError("light_on", "target_count", 9, 5)
    with patch_readers("custom_components.phoenix_mcp.llm.accounts._get_secret_store", AsyncMock(side_effect=limit)):
        result = await _batch(hass, data, access_key)
    assert result["reason"] == "too_many_targets" and result["catalog_limit"] == limit.details
    invalid = DecisionValidationError("probabilities", question="route")
    with patch_readers("custom_components.phoenix_mcp.llm.accounts._get_secret_store", AsyncMock(side_effect=invalid)):
        result = await _batch(hass, data, access_key)
    assert result["reason"] == "malformed" and result["validation_check"] == "probabilities"
    assert ("validation_details" in result) is bool(invalid.diagnostics)
