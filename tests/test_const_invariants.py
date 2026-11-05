"""Invariants of the constants modules.

The security constants in const.py are edited by humans and must stay mutually
consistent; a drift between them is a silent security gap (e.g. a physical service
gated in call_service but not in the native Hass* tool path). Assert the
relationships directly so an edit to one without the other fails loudly.

The modules themselves carry structural promises too: const stays standard-library
only, the template worker never loads it, and no shared table can be mutated.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import subprocess
import sys
import types

import homeassistant.components.system_log
import pytest

from custom_components.phoenix_mcp import const
from custom_components.phoenix_mcp.const import (
    CAPABILITY_TIERS,
    CONFIRM_AVAILABLE_CAPS,
    CONFIRM_ONLY_CAPS,
    LOG_LEVELS,
    PASS_THROUGH_EXEMPT_CAPS,
    PHYSICAL_GATE_DOMAINS,
)
from custom_components.phoenix_mcp.helpers import _LOG_LEVEL_RANK


def test_physical_gate_domains_are_exactly_the_safety_relevant_ones():
    # Both the call_service gate (policy_engine.call_needs_physical_gate) and the
    # native Hass* tools gate whole domains on this set, so dropping a domain here
    # silently opens every service of that domain to pass-through keys.
    assert PHYSICAL_GATE_DOMAINS == {"lock", "alarm_control_panel", "cover", "valve"}


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


def test_a_confirm_only_capability_can_confirm_and_ignores_pass_through():
    # Without Confirm it would have no usable mode, and without the exemption a
    # pass-through key would read it as Allow.
    assert CONFIRM_ONLY_CAPS <= CONFIRM_AVAILABLE_CAPS
    assert CONFIRM_ONLY_CAPS <= PASS_THROUGH_EXEMPT_CAPS


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


PACKAGE = pathlib.Path(const.__file__).parent
TABLE_MODULES = (
    "const", "catalog_templates", "esphome_constants", "provider_constants", "routing.routing_tables", "template_limits",
)


def _imports(path: pathlib.Path) -> list[ast.Import | ast.ImportFrom]:
    """Every import statement anywhere in one module, by AST rather than by text."""
    return [node for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, (ast.Import, ast.ImportFrom))]


def test_const_imports_only_the_standard_library():
    """A package import here would also pull the integration in behind it."""
    roots = set()
    for node in _imports(PACKAGE / "const.py"):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        else:
            assert node.level == 0, f"const.py line {node.lineno} imports from the package"
            roots.add((node.module or "").partition(".")[0])
    assert roots <= sys.stdlib_module_names, roots - sys.stdlib_module_names


def test_template_limits_import_nothing():
    """The worker runs this file before its OS limits apply."""
    assert _imports(PACKAGE / "template_limits.py") == []


def test_template_worker_limits_load_without_third_party_modules():
    """The worker's first load, with only this interpreter's standard library."""
    probe = (
        "import runpy, sys\n"
        f"limits = runpy.run_path({str(PACKAGE / 'template_limits.py')!r})\n"
        "assert limits['TEMPLATE_WORKER_CPU_SECONDS'] > 0\n"
        "assert 'home_assistant_intents' not in sys.modules\n"
        "assert not any(name.startswith(('homeassistant', 'custom_components')) for name in sys.modules)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_template_worker_does_not_load_const():
    source = (PACKAGE / "template_worker.py").read_text()
    loaded = [
        node.args[0].value for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "with_name"
    ]
    assert loaded == ["template_limits.py"]


def _mutable_parts(value: object, path: str) -> list[str]:
    """Paths of every writable container reachable through one constant."""
    if isinstance(value, (dict, list, set, bytearray)):
        return [path]
    if isinstance(value, types.MappingProxyType):
        return [
            found for key, item in value.items()
            for found in (*_mutable_parts(key, f"{path} key"), *_mutable_parts(item, f"{path}[{key!r}]"))
        ]
    if isinstance(value, (tuple, frozenset)):
        return [found for item in value for found in _mutable_parts(item, f"{path} member")]
    return []


@pytest.mark.parametrize("name", TABLE_MODULES)
def test_shared_tables_are_deeply_read_only(name):
    module = importlib.import_module(f"custom_components.phoenix_mcp.{name}")
    found = [
        part for key, value in vars(module).items()
        if not key.startswith("__") and not isinstance(value, types.ModuleType)
        for part in _mutable_parts(value, f"{name}.{key}")
    ]
    assert found == []


def test_a_routing_table_refuses_mutation():
    from custom_components.phoenix_mcp.routing.routing_tables import COMMAND_ROUTING_ENTITY_COMMANDS, COMMAND_ROUTING_FIXED_INTENT_DATA

    with pytest.raises(TypeError):
        COMMAND_ROUTING_ENTITY_COMMANDS["injected"] = ("lock", "unlock", "", "", "", "")  # type: ignore[index]
    nested = next(iter(COMMAND_ROUTING_FIXED_INTENT_DATA.values()))
    with pytest.raises(TypeError):
        nested["injected"] = True  # type: ignore[index]
