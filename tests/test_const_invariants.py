"""Invariants between hand-maintained security constants in const.py.

These constants are edited by humans and must stay mutually consistent; a drift
between them is a silent security gap (e.g. a physical service gated in
call_service but not in the native Hass* tool path). Assert the relationships
directly so an edit to one without the other fails loudly.
"""

from __future__ import annotations

import ast
import pathlib

import homeassistant.components.system_log

from custom_components.phoenix_mcp.const import (
    CAPABILITY_TIERS,
    CONFIRM_AVAILABLE_CAPS,
    LOG_LEVELS,
    PHYSICAL_GATE_DOMAINS,
    PHYSICAL_GATE_SERVICES,
)
from custom_components.phoenix_mcp.helpers import _LOG_LEVEL_RANK


def test_physical_gate_domains_cover_every_service_domain():
    # Both the call_service gate (policy_engine.call_needs_physical_gate) and the
    # native Hass* tools gate on PHYSICAL_GATE_DOMAINS. PHYSICAL_GATE_SERVICES is
    # the catalog of actuation services on those domains; every one of its
    # domains MUST be in PHYSICAL_GATE_DOMAINS or that service would slip both
    # gates. Gating the whole domain also covers services an exact name list
    # misses (cover.toggle, *_cover_tilt, valve.toggle, alarm_arm_custom_bypass).
    service_domains = {s.split("/")[0] for s in PHYSICAL_GATE_SERVICES}
    assert service_domains <= PHYSICAL_GATE_DOMAINS, (
        f"domains gated via services but not via PHYSICAL_GATE_DOMAINS: "
        f"{sorted(service_domains - PHYSICAL_GATE_DOMAINS)}"
    )


def test_physical_gate_domains_has_no_orphans():
    # The reverse: every gate domain needs at least one gated service, or the
    # native-tool filter blocks a domain call_service allows.
    service_domains = {s.split("/")[0] for s in PHYSICAL_GATE_SERVICES}
    assert PHYSICAL_GATE_DOMAINS <= service_domains, (
        f"domains in PHYSICAL_GATE_DOMAINS with no gated service: "
        f"{sorted(PHYSICAL_GATE_DOMAINS - service_domains)}"
    )


def _system_log_handler_level() -> str:
    """The level Home Assistant attaches its system_log handler at, read from HA.

    Read from HA's source by AST rather than asserted from memory, because it
    decides which levels Phoenix may offer and a lower HA level would change the
    answer. Inspecting the live handler would need the whole component set up.
    """
    source = pathlib.Path(homeassistant.components.system_log.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setLevel"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Attribute)
        ):
            return node.args[0].attr
    raise AssertionError(
        "no handler.setLevel(logging.X) call found in HA's system_log; the "
        "assumption behind const.LOG_LEVELS can no longer be verified"
    )


def test_log_levels_match_what_home_assistant_actually_collects():
    # get_logs reads hass.data["system_log"].records, and HA attaches that handler
    # at WARNING, so an INFO record never enters the store. Offering a level the
    # store never holds is a promise it cannot keep: a caller asking for INFO
    # would get warnings back and read the quiet result as "nothing was logged".
    # If HA ever lowers this, WIDEN LOG_LEVELS rather than change the assertion.
    assert _system_log_handler_level() == "WARNING"
    assert LOG_LEVELS == ("WARNING", "ERROR")


def test_log_levels_are_a_subset_of_the_record_ranks():
    # The sets differ in size on purpose (_LOG_LEVEL_RANK classifies a RECORD's
    # level, LOG_LEVELS is what a CALLER may ask for), but every offered level
    # must rank or collect_log_entries would fall back to WARNING and silently
    # answer a different question.
    assert set(LOG_LEVELS) <= set(_LOG_LEVEL_RANK)


def test_confirm_is_offered_exactly_outside_the_read_and_everyday_tiers():
    # The Read and Everyday tiers are deny/allow only and every other tier offers
    # Confirm. The two constants are edited separately, so moving a capability
    # between tiers without updating CONFIRM_AVAILABLE_CAPS would leave a write
    # tier without its Confirm choice or offer Confirm where it gates nothing.
    drifted = {
        cap: tier for cap, tier in CAPABILITY_TIERS.items()
        if (tier in ("read", "everyday")) == (cap in CONFIRM_AVAILABLE_CAPS)
    }
    assert not drifted, f"Confirm availability disagrees with the tier: {drifted}"


def test_configuration_file_capabilities_sit_in_their_tiers():
    # cap_filesystem only reads, and cap_esphome_yaml reads device YAML and runs
    # Device Builder work that touches no device, so neither offers Confirm.
    # cap_yaml_edit keeps Confirm because it also gates the reload services that
    # apply hand-edited configuration.
    assert CAPABILITY_TIERS["cap_filesystem"] == "read"
    assert CAPABILITY_TIERS["cap_esphome_yaml"] == "everyday"
    assert CAPABILITY_TIERS["cap_yaml_edit"] == "system"
    assert "cap_filesystem" not in CONFIRM_AVAILABLE_CAPS
    assert "cap_esphome_yaml" not in CONFIRM_AVAILABLE_CAPS
    assert "cap_yaml_edit" in CONFIRM_AVAILABLE_CAPS
