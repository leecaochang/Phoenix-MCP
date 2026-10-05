"""Guard: the declared Home Assistant floor must match what the code imports.

`MIN_HA_VERSION` is a compatibility claim that HACS reads from `hacs.json` and
trusts. If it names a release that lacks an API imported at module level, the
install proceeds and then raises `ImportError` before setup finishes. The suite
runs on one modern HA, so the checks here are the ones that work offline:

  1. The number is stated identically in every file that restates it.
  2. Every UNGUARDED module-level `from homeassistant...` import is listed in
     `KNOWN_IMPORTS` with the release that introduced that name, and none is
     newer than the floor. A new import fails until someone records when it landed.

Imports inside a function, a `try` or a `TYPE_CHECKING` block degrade to a clean
error at call time, so the scan skips them; optional-feature floors are
documented instead. The exception is an API behind a feature in the base
compatibility claim (`LOVELACE_DATA`), which `MANDATORY_FEATURE_IMPORTS` lists by
hand. Introduction versions come from the upstream tag, so recording one is a
human step: check it against
`https://raw.githubusercontent.com/home-assistant/core/<tag>/<path>`.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from custom_components.phoenix_mcp.const import MIN_HA_VERSION

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "phoenix_mcp"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


FLOOR = _version(MIN_HA_VERSION)

# (module, imported name) maps to the HA release that introduced that NAME.
#
# Keyed by SYMBOL, not by module: a table keyed on the module alone would let a
# new API imported from an already-listed module inherit that module's recorded
# version, leaving the guard green on exactly the drift it exists to catch.
#
# "0.0.0" means the name is older than any release this project has claimed, so
# it can never constrain the floor. Anything with a real version was checked
# against that tag's source at
# https://raw.githubusercontent.com/home-assistant/core/<tag>/<path>.
KNOWN_IMPORTS: dict[str, dict[str, str]] = {
    # Entity-command feature exports verified in each component's 2024.5.0
    # __init__.py, older than any claimed floor.
    "homeassistant.components.alarm_control_panel": {"AlarmControlPanelEntityFeature": "0.0.0"},
    "homeassistant.components.climate": {"ClimateEntityFeature": "0.0.0"},
    "homeassistant.components.fan": {"FanEntityFeature": "0.0.0"},
    "homeassistant.components.humidifier": {"HumidifierEntityFeature": "0.0.0"},
    "homeassistant.components.lawn_mower": {"LawnMowerEntityFeature": "0.0.0"},
    "homeassistant.components.lock": {"LockEntityFeature": "0.0.0"},
    "homeassistant.components.siren": {"SirenEntityFeature": "0.0.0"},
    "homeassistant.components.vacuum": {"VacuumEntityFeature": "0.0.0"},
    "homeassistant.components.valve": {"ValveEntityFeature": "0.0.0"},
    "homeassistant.components.water_heater": {"WaterHeaterEntityFeature": "0.0.0"},
    # Verified exported in 2024.5.0, the earliest claimed floor.
    "homeassistant.components.repairs": {"RepairsFlow": "0.0.0"},
    "homeassistant": {
        # Integration loading is older than any claimed floor and is the same
        # public module HA's own device-removal endpoint uses.
        "loader": "0.0.0",
    },
    "homeassistant.components.automation.config": {
        "async_validate_config_item": "0.0.0",
        # Structural schema export verified in the 2024.5.0 source.
        "PLATFORM_SCHEMA": "0.0.0",
    },
    "homeassistant.components.frontend": {
        "add_extra_js_url": "0.0.0",
        "async_register_built_in_panel": "0.0.0",
        "async_remove_panel": "0.0.0",
        # Verified absent in 2024.6.0, present in 2024.7.0.
        "remove_extra_js_url": "2024.7.0",
    },
    "homeassistant.components.http": {
        "HomeAssistantView": "0.0.0",
        # Verified absent in 2024.6.0, present in 2024.7.0.
        "StaticPathConfig": "2024.7.0",
    },
    "homeassistant.components.http.const": {
        "KEY_AUTHENTICATED": "0.0.0",
        "KEY_HASS_USER": "0.0.0",
    },
    "homeassistant.components.light": {
        # Both entity-command exports verified in 2024.5.0 light/__init__.py.
        "LightEntityFeature": "0.0.0",
        "color_supported": "0.0.0",
        # Both exports verified in the 2025.2.0 light/__init__.py source.
        "ATTR_SUPPORTED_COLOR_MODES": "0.0.0",
        "brightness_supported": "0.0.0",
    },
    "homeassistant.components.cover": {
        # Export and SET_POSITION member verified in 2025.2.0 cover/__init__.py.
        "CoverEntityFeature": "0.0.0",
    },
    "homeassistant.components.media_player": {
        # Re-export and VOLUME_SET usage verified in 2025.2.0 media_player/__init__.py.
        "MediaPlayerEntityFeature": "0.0.0",
    },
    "homeassistant.components.script.config": {
        "async_validate_config_item": "0.0.0",
        # Structural schema export verified in the 2024.5.0 source.
        "SCRIPT_ENTITY_SCHEMA": "0.0.0",
    },
    "homeassistant.components.sensor": {
        "SensorDeviceClass": "0.0.0",
        "SensorEntity": "0.0.0",
        "SensorStateClass": "0.0.0",
    },
    "homeassistant.components.websocket_api": {
        "const": "0.0.0",
    },
    "homeassistant.components.websocket_api.connection": {
        "ActiveConnection": "0.0.0",
    },
    "homeassistant.config_entries": {
        "ConfigEntry": "0.0.0",
        "ConfigEntryDisabler": "0.0.0",
        "ConfigEntryState": "0.0.0",
        "ConfigFlow": "0.0.0",
        "ConfigFlowResult": "0.0.0",
        "SOURCE_RECONFIGURE": "0.0.0",
        "SOURCE_USER": "0.0.0",
        # Public helper used by HA's own config-entry and device removal
        # surfaces; older than any claimed floor.
        "support_entry_unload": "0.0.0",
    },
    "homeassistant.const": {
        "EntityCategory": "0.0.0",
        "EVENT_HOMEASSISTANT_STARTED": "0.0.0",
        "EVENT_HOMEASSISTANT_STOP": "0.0.0",
        "MATCH_ALL": "0.0.0",
        "UnitOfTime": "0.0.0",
        "__version__": "0.0.0",
    },
    # Household identity exports verified present in upstream's 2024.5.0 tag.
    "homeassistant.auth": {"EVENT_USER_REMOVED": "2024.5.0", "EVENT_USER_UPDATED": "2024.5.0"},
    "homeassistant.auth.permissions.const": {"POLICY_CONTROL": "2024.5.0", "POLICY_READ": "2024.5.0"},
    "homeassistant.core": {
        "Context": "2024.5.0",
        "CoreState": "0.0.0",
        # Re-exported into core from homeassistant.util.event_type. Verified
        # present in 2024.7.0, so it is older than the floor rather than setting it.
        "EventType": "2024.7.0",
        "Event": "0.0.0",
        "HomeAssistant": "0.0.0",
        "State": "0.0.0",
        "callback": "0.0.0",
        "valid_entity_id": "0.0.0",
    },
    "homeassistant.data_entry_flow": {
        "FlowResultType": "0.0.0",
    },
    "homeassistant.exceptions": {
        "HomeAssistantError": "0.0.0",
        "ServiceNotFound": "0.0.0",
        "ServiceValidationError": "0.0.0",
        "Unauthorized": "0.0.0",
    },
    "homeassistant.helpers": {
        "area_registry": "0.0.0",
        # Verified absent in 2024.3.3, present in 2024.4.0.
        "category_registry": "2024.4.0",
        "config_validation": "0.0.0",
        "device_registry": "0.0.0",
        "entity_registry": "0.0.0",
        "floor_registry": "0.0.0",
        "intent": "0.0.0",
        # Both the issue registry and its helper-module import are older than
        # the earliest HA release claimed.
        "issue_registry": "0.0.0",
        # Verified absent in 2024.2.5, present in 2024.3.0.
        "label_registry": "2024.3.0",
        "sun": "0.0.0",
        # Module and async_get_translations verified in the 2025.2.0 source.
        "translation": "2025.2.0",
    },
    "homeassistant.helpers.aiohttp_client": {
        "async_get_clientsession": "0.0.0",
    },
    "homeassistant.helpers.entity": {
        "DeviceInfo": "0.0.0",
    },
    "homeassistant.helpers.entity_platform": {
        "AddEntitiesCallback": "0.0.0",
    },
    "homeassistant.helpers.event": {
        "async_call_later": "0.0.0",
        "async_track_state_change_event": "0.0.0",
        "async_track_time_interval": "0.0.0",
    },
    "homeassistant.helpers.storage": {
        "Store": "0.0.0",
    },
    "homeassistant.helpers.service": {
        # Present in 2024.1.0, older than any claimed floor.
        "async_get_all_descriptions": "0.0.0",
    },
    "homeassistant.loader": {
        "IntegrationNotFound": "0.0.0",
        "async_get_integration": "0.0.0",
    },
    "homeassistant.util": {
        "dt": "0.0.0",
        # Public utility that core's helper and entity ID generation has long
        # used, far older than any claimed floor.
        "slugify": "0.0.0",
    },
    "homeassistant.util.color": {
        # Named-color catalog verified in 2024.5.0 util/color.py.
        "COLORS": "0.0.0",
        "color_name_to_rgb": "2024.5.0",
    },
    "homeassistant.util.dt": {
        "as_utc": "0.0.0",
        "parse_datetime": "0.0.0",
        "utcnow": "0.0.0",
    },
    "homeassistant.util.file": {"WriteError": "0.0.0",
        "write_utf8_file_atomic": "0.0.0",
    },
    # Export and Store's error-catching seam verified at the supported floor.
    "homeassistant.util.json": {"SerializationError": "2026.7.0"},
    "homeassistant.util.ulid": {
        # Re-exported from ulid_transform. Verified absent in 2024.7.0 and
        # present in 2024.8.0.
        "ulid_to_bytes_or_none": "2024.8.0",
    },
    "homeassistant.util.yaml": {
        "dump": "0.0.0",
        "load_yaml": "0.0.0",
    },
}


# Symbols that are NOT imported at module level but whose absence still breaks a
# documented part of the base product. Nothing discovers these, so they are listed
# by hand.
#
# The automatic scan covers UNGUARDED module-level imports, because those crash
# setup; a guarded import degrades to a clean error and is out of its scope. But
# `LOVELACE_DATA` is guarded AND load-bearing: without it every dashboard tool
# answers "lovelace is not loaded", a documented part of the tool surface, so the
# floor must not be lowered below its release.
#
# Add an entry here when a feature named in the BASE compatibility claim depends
# on an API newer than the previous floor.
MANDATORY_FEATURE_IMPORTS: dict[str, str] = {
    # Reached through a guarded import by the dashboard tools. Verified absent in
    # 2025.1.4 and present in 2025.2.0.
    "homeassistant.components.lovelace.const.LOVELACE_DATA": "2025.2.0",
}

@pytest.mark.parametrize("symbol", sorted(MANDATORY_FEATURE_IMPORTS))
def test_mandatory_feature_imports_are_not_newer_than_the_floor(symbol: str) -> None:
    """A documented feature must not need HA newer than the declared minimum.

    Unlike the scanned imports, these do not crash setup. They fail at CALL time
    with a clean error, so the integration starts, HACS is satisfied, and the
    operator discovers the hole one tool at a time.
    """
    introduced = _version(MANDATORY_FEATURE_IMPORTS[symbol])
    assert introduced <= FLOOR, (
        f"{symbol} needs HA {MANDATORY_FEATURE_IMPORTS[symbol]} but MIN_HA_VERSION "
        f"is {MIN_HA_VERSION}. It is not imported at setup, so nothing crashes; the "
        f"feature that depends on it simply fails on every call, which is exactly "
        f"the 2024.7-versus-2025.2 case. Raise MIN_HA_VERSION."
    )


def test_injector_floor_matches_the_supported_base() -> None:
    """The optional DOM injector cannot advertise an unsupported HA release."""
    from custom_components.phoenix_mcp import const  # noqa: PLC0415

    assert const.MESA_INJECT_MIN_HA == MIN_HA_VERSION


def _unguarded_module_level_ha_imports() -> dict[tuple[str, str], set[str]]:
    """Map each (module, imported name) to the files importing it at module level.

    The NAME matters: a guard keyed on the module alone lets a new API ride in on
    an already-recorded module. Guarded means nested in a function, a `try`, or a
    `TYPE_CHECKING` block: none of those runs at import time, so an absent name
    there cannot fail setup.
    """
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        if "mesa_core" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded: set[int] = set()

        class _Walker(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.AST) -> None:
                guarded.update(id(child) for child in ast.walk(node))

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Try(self, node: ast.AST) -> None:
                guarded.update(id(child) for child in ast.walk(node))

            def visit_If(self, node: ast.If) -> None:
                if "TYPE_CHECKING" in ast.unparse(node.test):
                    guarded.update(id(child) for child in ast.walk(node))
                else:
                    self.generic_visit(node)

        _Walker().visit(tree)
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.ImportFrom) and node.module:
                if not node.module.startswith("homeassistant"):
                    continue
                for alias in node.names:
                    found.setdefault((node.module, alias.name), set()).add(path.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("homeassistant"):
                        found.setdefault((alias.name, "*module*"), set()).add(path.name)
    return found


def _recorded_pairs() -> set[tuple[str, str]]:
    return {(module, name) for module, names in KNOWN_IMPORTS.items() for name in names}


def test_every_module_level_ha_import_is_recorded() -> None:
    """A new setup-time HA import must state which release introduced it.

    Checked per SYMBOL: a new name imported from an already-recorded module still
    needs its own entry.
    """
    imports = _unguarded_module_level_ha_imports()
    recorded = _recorded_pairs()
    unrecorded = {pair: sorted(files) for pair, files in imports.items() if pair not in recorded}
    assert not unrecorded, (
        "These names are imported at module level (so they run during setup) but "
        "are not in KNOWN_IMPORTS. Check which HA release introduced each one, "
        "then add it under its module:\n"
        + "\n".join(
            f"  {module}.{name}  <- {', '.join(files)}"
            for (module, name), files in sorted(unrecorded.items())
        )
    )


def test_no_star_imports_from_home_assistant() -> None:
    """A star import cannot be checked, so it must not exist.

    `from homeassistant.x import *` binds names nobody enumerated, which would
    let an arbitrary post-floor API in through a module the table already lists.
    """
    offenders = sorted(
        f"{module} <- {', '.join(sorted(files))}"
        for (module, name), files in _unguarded_module_level_ha_imports().items()
        if name == "*"
    )
    assert not offenders, f"Star imports from Home Assistant cannot be version-checked: {offenders}"


@pytest.mark.parametrize("pair", sorted(_recorded_pairs()))
def test_no_module_level_import_needs_newer_ha_than_the_floor(pair: tuple[str, str]) -> None:
    """The floor must not be older than an API setup unconditionally imports."""
    module, name = pair
    stated = KNOWN_IMPORTS[module][name]
    assert _version(stated) <= FLOOR, (
        f"{module}.{name} needs HA {stated} but MIN_HA_VERSION is {MIN_HA_VERSION}. "
        f"Setup imports it unconditionally, so every install between the two raises "
        f"ImportError. Raise MIN_HA_VERSION (and the four places that restate it)."
    )


def test_known_imports_has_no_stale_entries() -> None:
    """An entry for a name nothing imports any more is misleading."""
    imports = _unguarded_module_level_ha_imports()
    stale = sorted(f"{module}.{name}" for module, name in _recorded_pairs() - set(imports))
    assert not stale, (
        f"KNOWN_IMPORTS lists names no longer imported at module level: {stale}. "
        "Remove them so the table keeps describing the real setup-time surface."
    )


def test_the_floor_is_stated_identically_everywhere() -> None:
    """Every file that restates the floor must match `MIN_HA_VERSION`."""
    major_minor = ".".join(MIN_HA_VERSION.split(".")[:2])

    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["homeassistant"] == MIN_HA_VERSION, (
        f"hacs.json says {hacs['homeassistant']}, const.py says {MIN_HA_VERSION}. "
        "HACS reads hacs.json to decide whether an install may proceed."
    )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Home Assistant {MIN_HA_VERSION} or later" in readme
    assert f"Home%20Assistant-{major_minor}%2B" in readme, (
        f"The README badge does not read {major_minor}+."
    )

    for page, needle in (
        ("index.html", f"HA {major_minor}+"),
        ("install.html", f"{MIN_HA_VERSION} or later"),
    ):
        html = (ROOT / "docs" / page).read_text(encoding="utf-8")
        assert needle in html, f"docs/{page} does not state the floor as {needle!r}."
