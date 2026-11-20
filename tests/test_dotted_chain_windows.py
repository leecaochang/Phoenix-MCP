"""Every window of a dotted chain hides a hidden and a missing entity alike, and module paths stay.

A window in a domain with entities on this home is redacted unless the key reads that entity,
wherever it sits in the chain. A logger or module path keeps its integration module, from a map
of installed modules built in the executor; until the map is ready such a window is redacted.
"""

from __future__ import annotations

import uuid

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import redaction
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.redaction import (
    async_refresh_integration_modules, entity_domains, redact_entity_ids_outside,
    redact_inaccessible_entity_ids_in_text,
)

R = "<redacted-entity>"


def _key(*green_domains: str) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={d: PermissionNode(state="GREEN") for d in green_domains}))


def _home(hass: HomeAssistant, tmp_path) -> None:
    hass.states.async_set("lock.smart_lock", "locked")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("sensor.power", "5")
    hass.states.async_set("acme.thing", "on")
    er.async_get(hass).async_get_or_create("automation", "automation", "morning", suggested_object_id="morning")
    custom = tmp_path / "custom_components" / "acme"
    custom.mkdir(parents=True)
    (custom / "__init__.py").write_text("")
    (custom / "coordinator.py").write_text("")


async def _both(hass: HomeAssistant, text: str, key: AccessKeyRecord, visible: frozenset[str]) -> tuple[str, str]:
    """The loop reader's output and the worker-thread reader's, run in the executor."""
    loop = redact_inaccessible_entity_ids_in_text(text, key, hass)
    domains = entity_domains(hass)
    worker = await hass.async_add_executor_job(
        lambda: redact_entity_ids_outside(text, visible, hass, chain_domains=domains))
    return loop, worker


@pytest.mark.parametrize(("hidden", "missing", "expected"), [
    ("x.lock.smart_lock", "x.lock.no_such", f"x.{R}"),
    ("foo.light.hall.attr", "foo.light.no_such.attr", f"foo.{R}.attr"),
    ("config.entry.lock.smart_lock.state", "config.entry.lock.no_such.state", f"config.entry.{R}.state"),
])
async def test_a_later_window_reads_alike_for_hidden_and_missing(hass, isolated_config_dir, hidden, missing, expected):
    _home(hass, isolated_config_dir)
    await async_refresh_integration_modules(hass)
    key = _key()
    for chain in (hidden, missing):
        assert await _both(hass, f"note {chain} end", key, frozenset()) == (f"note {expected} end",) * 2


@pytest.mark.parametrize("text", [
    "homeassistant.components.sensor.recorder: compiling statistics",
    "homeassistant.components.light.reproduce_state: reproducing",
    "custom_components.acme.coordinator: polling",
    "homeassistant.components.sensor.recorder.helpers is not real but stays",
])
async def test_an_integration_module_path_stays(hass, isolated_config_dir, text):
    _home(hass, isolated_config_dir)
    await async_refresh_integration_modules(hass)
    assert await _both(hass, text, _key(), frozenset()) == (text, text)


async def test_a_custom_integration_name_that_is_no_module_is_judged_as_an_entity(hass, isolated_config_dir):
    _home(hass, isolated_config_dir)
    await async_refresh_integration_modules(hass)
    text = "custom_components.acme.thing: polling"
    assert await _both(hass, text, _key(), frozenset()) == (f"custom_components.{R}: polling",) * 2


async def test_an_automation_logger_is_redacted_unless_readable(hass, isolated_config_dir):
    _home(hass, isolated_config_dir)
    await async_refresh_integration_modules(hass)
    text = "homeassistant.components.automation.morning: Running automation actions"
    redacted = f"homeassistant.components.{R}: Running automation actions"
    assert await _both(hass, text, _key(), frozenset()) == (redacted, redacted)
    missing = "homeassistant.components.automation.evening: Running automation actions"
    assert await _both(hass, missing, _key(), frozenset()) == (redacted, redacted)
    assert await _both(hass, text, _key("automation"), frozenset({"automation.morning"})) == (text, text)


async def test_a_module_path_is_redacted_until_the_map_is_ready(hass, isolated_config_dir):
    _home(hass, isolated_config_dir)
    hass.data.pop(redaction._MODULE_MAP_KEY, None)
    text = "homeassistant.components.sensor.recorder: compiling statistics"
    out = await _both(hass, text, _key(), frozenset())
    assert out == (f"homeassistant.components.{R}: compiling statistics",) * 2
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await _both(hass, text, _key(), frozenset()) == (text, text)


async def test_a_new_entity_domain_waits_for_a_refresh(hass, isolated_config_dir):
    _home(hass, isolated_config_dir)
    await async_refresh_integration_modules(hass)
    hass.states.async_set("climate.den", "heat")
    text = "homeassistant.components.climate.reproduce_state: reproducing"
    assert redact_inaccessible_entity_ids_in_text(text, _key(), hass) == f"homeassistant.components.{R}: reproducing"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert redact_inaccessible_entity_ids_in_text(text, _key(), hass) == text


@pytest.mark.parametrize(("text", "expected"), [
    ("门锁lock.smart_lock状态", f"门锁{R}状态"),
    ("检查lock.smart_lock", f"检查{R}"),
    ("lock.smart_lock已锁", f"{R}已锁"),
    ("lock.smart_locké", f"{R}é"),
    ("LOCK.SMART_LOCK is locked", f"{R} is locked"),
    ("LOCK.NO_SUCH is locked", f"{R} is locked"),
    ("states.LOCK.Smart_Lock.state", f"states.{R}.state"),
    ("门锁light.hall", "门锁light.hall"),
    ("LIGHT.HALL is on", "LIGHT.HALL is on"),
    ("see README.md and Node.js", "see README.md and Node.js"),
])
async def test_an_id_beside_other_letters_or_in_upper_case_is_judged_like_any_other(
        hass, isolated_config_dir, text, expected):
    _home(hass, isolated_config_dir)
    key = _key("light")
    loop, worker = await _both(hass, text, key, frozenset({"light.hall"}))
    assert loop == worker == expected
