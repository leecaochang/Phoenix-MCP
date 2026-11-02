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


def test_error_projection_unwraps_known_markers_and_hides_dynamic_markers():
    errors = [
        vol.RequiredFieldInvalid("secret", path=[vol.Required("triggers")]),
        vol.RequiredFieldInvalid("secret", path=[vol.Required("token=secret")]),
    ]
    text = validation_message("automation", vol.MultipleInvalid(errors))
    assert "config.triggers is required" in text
    assert "config.[field] is required" in text
    assert "secret" not in text


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


async def _core_rejection(hass, domain, config):
    """Run Core's own validator on a config it must reject and project the failure."""
    from homeassistant.components.automation.config import (
        async_validate_config_item as automation_validator,
    )
    from homeassistant.components.script.config import (
        async_validate_config_item as script_validator,
    )
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.phoenix_mcp.authoring_validation import (
        async_validation_message,
    )

    validator = automation_validator if domain == "automation" else script_validator
    try:
        await validator(hass, "phoenix_mcp_validate", config)
    except vol.Invalid as err:
        return await async_validation_message(hass, domain, config, err), err
    except HomeAssistantError as err:
        return await async_validation_message(hass, domain, config), err
    pytest.fail("Core accepted a config the test expects it to reject")


@pytest.fixture
async def core_keys(hass):
    """Load integrations whose real trigger and condition platforms register keys."""
    from homeassistant.setup import async_setup_component

    for integration in ("homeassistant", "schedule", "battery", "light"):
        assert await async_setup_component(hass, integration, {})
    return hass


@pytest.mark.parametrize(("domain", "config", "site", "expected"), [
    ("automation", {"triggers": [{"trigger": "battery.low"}], "actions": []},
     'config.triggers[0].trigger "battery.low"', ["battery.became_low", "battery.no_longer_low"]),
    ("automation", {"triggers": [{"trigger": "schedule.turned_on"}], "actions": []},
     'config.triggers[0].trigger "schedule.turned_on"', ["schedule.block_started", "schedule.block_ended"]),
    # Core renames the legacy root key in the caller's dict before it fails.
    ("automation", {"trigger": {"platform": "schedule.turned_on"}, "action": []},
     'config.triggers[0].platform "schedule.turned_on"', ["schedule.block_started", "schedule.block_ended"]),
    ("automation", {"triggers": [], "conditions": [{"condition": "or", "conditions": [{"condition": "battery.is_lo"}]}], "actions": []},
     'config.conditions[0].conditions[0].condition "battery.is_lo"', ["battery.is_low"]),
    ("script", {"sequence": [{"if": [{"condition": "battery.low"}], "then": []}]},
     'config.sequence[0].if[0].condition "battery.low"', ["battery.is_low"]),
    ("script", {"sequence": [{"wait_for_trigger": [{"trigger": "schedule.turned_on"}]}]},
     'config.sequence[0].wait_for_trigger[0].trigger "schedule.turned_on"', ["schedule.block_started"]),
])
async def test_unknown_key_ranks_same_domain_matches_first(core_keys, domain, config, site, expected):
    text, _ = await _core_rejection(core_keys, domain, config)
    kind = "condition" if site.endswith('condition "battery.is_lo"') or ".if[" in site else "trigger"
    assert f"{site} is not a {kind} key this Home Assistant has registered." in text
    matches = json.loads(text.split("closest_matches: ", 1)[1].split("].", 1)[0] + "]")
    assert matches[:len(expected)] == expected
    # Lookalikes from other domains, such as light.turned_on, never outrank the domain's own keys.
    domain_prefix = expected[0].split(".")[0] + "."
    assert all(m.startswith(domain_prefix) for m in matches[:len(expected)])


@pytest.mark.parametrize(("config", "site", "expected"), [
    ({"triggers": [{"trigger": "lights.turned_on"}], "actions": []}, 'config.triggers[0].trigger "lights.turned_on"', "light.turned_on"),
    # Core raises HomeAssistantError, not vol.Invalid, for a condition whose domain does not exist.
    ({"triggers": [], "actions": [{"condition": "batery.is_low"}]}, 'config.actions[0].condition "batery.is_low"', "battery.is_low"),
])
async def test_unknown_key_with_only_a_cross_domain_match(core_keys, config, site, expected):
    text, _ = await _core_rejection(core_keys, "automation", config)
    assert site + " is not a" in text
    matches = json.loads(text.split("closest_matches: ", 1)[1].split("].", 1)[0] + "]")
    assert matches[0] == expected
    assert all(m.split(".")[0] == expected.split(".")[0] for m in matches)


async def test_unknown_key_without_a_close_match_omits_closest_matches(core_keys):
    text, _ = await _core_rejection(core_keys, "automation", {"triggers": [{"trigger": "zzqx.qqqq"}], "actions": []})
    assert 'config.triggers[0].trigger "zzqx.qqqq" is not a trigger key this Home Assistant has registered.' in text
    assert "closest_matches" not in text


async def test_registered_and_unprovable_keys_are_left_untouched(core_keys):
    """Valid keys, legacy names, and a key from an installed but unloaded integration add nothing."""
    config = {
        "triggers": [{"trigger": "state", "entity_id": "light.kitchen"}, {"platform": "time", "at": "12:00:00"},
                     {"trigger": "schedule.block_started"}, {"trigger": "zwave_js.value_updated"}],
        "conditions": [{"condition": "and", "conditions": [{"condition": "state", "entity_id": "light.kitchen", "state": "on"}]},
                       {"condition": "battery.is_low"}, "{{ true }}"],
        "actions": [{"delay": "not a duration"}],
    }
    text, err = await _core_rejection(core_keys, "automation", config)
    assert text == validation_message("automation", err)
    assert "registered" not in text and "closest_matches" not in text


async def test_unknown_key_message_carries_no_validator_text_or_config_values(core_keys):
    config = {
        "alias": "token=secret",
        "triggers": [{"trigger": "battery.low", "entity_id": "sensor.hidden_vault", "for": "token=secret"},
                     {"trigger": "Token=Secret.low"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.hidden_vault"}}],
    }
    text, err = await _core_rejection(core_keys, "automation", config)
    assert str(err) and str(err) not in text
    assert "Invalid trigger" not in text and "specified" not in text
    assert "secret" not in text.lower() and "hidden_vault" not in text
    # The only echoed caller value is a key in Core's own key syntax.
    assert '"battery.low"' in text
    assert "config.triggers[1].trigger is not a trigger key" in text


async def test_unreadable_key_registry_degrades_to_plain_projection(core_keys):
    config = {"triggers": [{"trigger": "battery.low"}], "actions": []}
    with patch("homeassistant.helpers.trigger.async_get_all_descriptions", AsyncMock(side_effect=RuntimeError("boom"))):
        text, err = await _core_rejection(core_keys, "automation", config)
    assert text == validation_message("automation", err)


def test_legacy_key_sets_match_core_alias_tables():
    from homeassistant.helpers import condition, trigger

    from custom_components.phoenix_mcp import authoring_validation

    assert authoring_validation._LEGACY_TRIGGER_KEYS == set(trigger._PLATFORM_ALIASES)
    assert authoring_validation._BUILTIN_CONDITION_KEYS == set(condition._PLATFORM_ALIASES)
