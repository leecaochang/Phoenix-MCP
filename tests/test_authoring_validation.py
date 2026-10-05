"""Correction, refusal and safe-projection contracts for authoring errors."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.authoring_validation import authoring_input_error, validation_message
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord
from custom_components.phoenix_mcp.const import AUTHORING_MAX_ERRORS
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tools import authoring, discovery


def _key(**caps):
    return AccessKeyRecord(id="test", name="test", access_key_hash="x", created_at=utcnow(), created_by="test", **caps)


@pytest.mark.parametrize(("domain", "args", "expected"), [
    ("automation", {"alias": "secret", "triggers": [], "actions": []}, ["config.alias", "config.triggers", "config.actions", "Expected shape"]),
    ("script", {"sequence": []}, ["config.sequence", "Expected shape"]),
    ("automation", {"config": {"enabled": True}}, ["config.triggers is required", "config.actions is required", "initial_state", "automation.turn_off"]),
    ("script", {"config": {"triggers": [], "actions": []}}, ["config.sequence is required", "create_automation"]),
    ("automation", {"config": {"sequence": []}}, ["create_script", "config.actions is required"]),
    ("script", {"config": {"sequence": [{"delay": "00:00:01"}], "mode": "secret"}}, ["config.mode"]),
])
def test_actionable_combined_errors(domain, args, expected):
    text = authoring_input_error(args, domain)
    assert text
    assert "secret" not in text
    for value in expected:
        assert value in text


@pytest.mark.parametrize(("domain", "config"), [
    ("automation", {"triggers": [], "actions": []}),
    ("automation", {"trigger": [], "action": [], "initial_state": False}),
    ("script", {"sequence": []}),
    ("script", {"use_blueprint": {"path": "example.yaml"}}),
])
def test_optional_alias_and_native_forms(domain, config):
    assert authoring_input_error({"config": config}, domain) is None


def test_error_projection_bounds_and_hides_dynamic_names_and_messages():
    errors = [vol.Invalid("token=secret light.hidden", path=["sequence", 0, "data", "token=secret"])]
    errors += [vol.RequiredFieldInvalid("secret", path=["sequence", i, "delay"]) for i in range(30)]
    text = validation_message("script", vol.MultipleInvalid(errors))
    assert "config.sequence[0].data.[field]" in text
    assert "secret" not in text and "light.hidden" not in text
    assert "omitted" in text
    assert text.count("is required") < AUTHORING_MAX_ERRORS


@pytest.mark.parametrize("domain", ["automation", "script"])
@pytest.mark.parametrize("operation", ["create", "edit"])
@pytest.mark.parametrize("cap", ["confirm", "deny"])
async def test_rejection_never_gates_or_mutates(hass, domain, operation, cap):
    key = _key(**{f"cap_{domain}_write": cap})
    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock())
    tool = getattr(authoring, f"_tool_{operation}_{domain}")
    args = {f"{domain}_id": "example", "config": {"enabled": True}}
    with patch.object(authoring, "_gate", AsyncMock()) as gate, patch.object(hass, "async_add_executor_job", AsyncMock()) as worker:
        content, outcome, _ = await tool(args, key, hass, data)
    assert outcome == ("denied" if cap == "deny" else "invalid_request")
    text = content["content"][0]["text"]
    assert ("config." in text) == (cap != "deny")
    gate.assert_not_awaited()
    worker.assert_not_awaited()


@pytest.mark.parametrize("tool_name", ["create_automation", "edit_automation", "create_script", "edit_script"])
async def test_executor_rechecks_without_effects(hass, tool_name):
    tool = getattr(authoring, "_execute_" + tool_name)
    domain = "automation" if "automation" in tool_name else "script"
    with patch.object(hass, "async_add_executor_job", AsyncMock()) as worker:
        _, outcome, _ = await tool({f"{domain}_id": "example", "config": {}}, _key(), hass, MagicMock())
    assert outcome == "invalid_request"
    worker.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("secret light.hidden"), vol.Invalid("secret light.hidden", path=["mode"])])
async def test_diagnostics_internal_error_is_distinct_and_safe(hass, error):
    with patch.object(discovery, "_validate_script_config", AsyncMock(side_effect=error)):
        content, outcome, _ = await discovery._tool_validate_config(
            {"type": "script", "config": {"sequence": []}}, _key(cap_diagnostics="allow"), hass)
    text = content["content"][0]["text"]
    assert "secret" not in text and "light.hidden" not in text
    if isinstance(error, RuntimeError):
        assert outcome == "invalid_request" and content["isError"]
        assert "Internal error" in text
    else:
        assert outcome == "allowed" and json.loads(text)["valid"] is False


@pytest.mark.parametrize("domain", ["automation", "script"])
async def test_valid_input_reaches_real_ha_validator_without_mutating(hass, domain):
    config = {"triggers": [], "actions": []} if domain == "automation" else {"sequence": []}
    result, outcome, _ = await discovery._tool_validate_config(
        {"type": domain, "config": config}, _key(cap_diagnostics="allow"), hass)
    assert outcome == "allowed" and json.loads(result["content"][0]["text"])["valid"] is True


@pytest.mark.parametrize("domain", ["automation", "script"])
async def test_schema_drift_is_internal_and_does_not_log_exception_values(hass, domain, caplog):
    target = "PLATFORM_SCHEMA" if domain == "automation" else "SCRIPT_ENTITY_SCHEMA"
    with patch("custom_components.phoenix_mcp.authoring_validation." + target, side_effect=RuntimeError("token=secret light.hidden")):
        result, outcome, _ = await discovery._tool_validate_config(
            {"type": domain, "config": {}}, _key(cap_diagnostics="allow"), hass)
    assert outcome == "invalid_request" and result["isError"]
    assert "Internal error" in result["content"][0]["text"]
    assert "token=secret" not in caplog.text and "light.hidden" not in caplog.text


@pytest.mark.parametrize(("domain", "broken", "fixed"), [
    ("automation", {"alias": "Noon", "triggers": [], "actions": []}, {"config": {"alias": "Noon", "triggers": [], "actions": []}}),
    ("automation", {"config": {"enabled": False}}, {"config": {"initial_state": False, "triggers": [], "actions": []}}),
    ("script", {"config": {"actions": [{"delay": "00:00:01"}]}}, {"config": {"sequence": [{"delay": "00:00:01"}]}}),
    ("script", {"config": {"sequence": [], "mode": "incorrect"}}, {"config": {"sequence": [], "mode": "single"}}),
])
async def test_representative_correction_in_two_calls(hass, domain, broken, fixed):
    """Deterministic repair benchmark; does not claim hosted-agent qualification."""
    key = _key(cap_diagnostics="allow")
    results = []
    for args in (broken, fixed):
        result, _, _ = await discovery._tool_validate_config({"type": domain, **args}, key, hass)
        results.append(result)
    first = results[0]
    assert first.get("isError") or json.loads(first["content"][0]["text"])["valid"] is False
    assert json.loads(results[1]["content"][0]["text"])["valid"] is True
    assert len(results) == 2
