"""No function-local Home Assistant component import reads a component from disk on the event loop.

Importing a component Home Assistant has not loaded reads it from disk. Every import of
homeassistant.components.<domain> inside a Phoenix MCP function must therefore name a domain
that is always loaded before Phoenix, or be preceded in the same function by
require_component(hass, "<domain>") or a hass.config.components check. The few exemptions
below each say why, and the executor-only ones are pinned to run only in an executor.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import homeassistant

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "phoenix_mcp"
COMPONENTS = Path(homeassistant.__file__).parent / "components"

# Home Assistant sets these up before any other integration (bootstrap's core, logging
# and HTTP stages), and Phoenix MCP depends on frontend.
BOOTSTRAP_ROOTS = ("homeassistant", "persistent_notification", "logger", "network", "system_log", "http", "frontend")

# Functions that only ever run in an executor thread, never on the event loop.
EXECUTOR_ONLY = {
    ("history_summary.py", "read_state_change_page"),
    ("history_summary.py", "read_attribute_changes"),
    ("recorder_queries.py", "read_statistic_window"),
    ("recorder_queries.py", "read_statistics_page"),
    ("tools/blueprint.py", "_validate_blueprint_source"),
    ("tools/blueprint.py", "_canonical_blueprint_source"),
}
# Reached only after the same component was confirmed loaded.
CONFIRMED_BY_CALLER = {
    ("entity_commands.py", "supports_entity_command"): "only for the state of a camera entity",
    ("tools/calendar.py", "_calendar_event_payload"): "only after _calendar_entity",
    ("tools/calendar.py", "_calendar_serialized"): "only after _calendar_entity",
    ("tools/calendar.py", "_calendar_write_plan"): "after its own _calendar_entity call",
}
# Executor-only callers of executor-only functions.
EXECUTOR_CALLERS = {"_blueprint_restored_text"}


def _always_loaded() -> set[str]:
    """The bootstrap roots plus everything they depend on, from the installed manifests."""
    seen: set[str] = set()
    pending = list(BOOTSTRAP_ROOTS)
    while pending:
        domain = pending.pop()
        if domain in seen:
            continue
        seen.add(domain)
        manifest = json.loads((COMPONENTS / domain / "manifest.json").read_text())
        pending.extend(manifest.get("dependencies", []))
    return seen


def _sources() -> list[tuple[str, ast.Module, str]]:
    return [(path.relative_to(PACKAGE).as_posix(), ast.parse(text := path.read_text()), text)
            for path in sorted(PACKAGE.rglob("*.py")) if "mesa_core" not in path.parts]


def _component_imports(node: ast.AST) -> list[tuple[int, str]]:
    found = []
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom) and (child.module or "").startswith("homeassistant.components"):
            parts = child.module.split(".")
            found += [(child.lineno, parts[2])] if len(parts) > 2 else [(child.lineno, a.name) for a in child.names]
        elif isinstance(child, ast.Import):
            found += [(child.lineno, a.name.split(".")[2]) for a in child.names
                      if a.name.startswith("homeassistant.components.")]
    return found


def _guarded(text_before: str, domain: str) -> bool:
    return (f'require_component(hass, "{domain}")' in text_before
            or re.search(rf"""(["']{domain}["']|_[A-Z_]+) (not )?in hass\.config\.components""", text_before) is not None)


def test_every_local_component_import_is_guarded():
    """An unguarded import of a component that may not be loaded is a disk read on the event loop."""
    always = _always_loaded()
    exempt = EXECUTOR_ONLY | set(CONFIRMED_BY_CALLER)
    unguarded = []
    for name, tree, text in _sources():
        lines = text.splitlines()
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or (name, function.name) in exempt:
                continue
            for lineno, domain in _component_imports(function):
                if domain in always:
                    continue
                if not _guarded("\n".join(lines[function.lineno - 1:lineno - 1]), domain):
                    unguarded.append(f"{name}:{lineno} {function.name} imports {domain}")
    assert unguarded == []


def test_exemptions_still_name_existing_functions_with_component_imports():
    """A stale exemption would hide a renamed function from the guard."""
    functions = {(name, node.name): node for name, tree, _ in _sources() for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for key in EXECUTOR_ONLY | set(CONFIRMED_BY_CALLER):
        assert key in functions, key
        assert _component_imports(functions[key]), key


def test_executor_only_functions_never_run_on_the_event_loop():
    """Each is handed to an executor, or called directly only by another executor-only function.

    A functools.partial binding counts as handing over; the history tools pass it straight on.
    """
    names = {function for _, function in EXECUTOR_ONLY}
    misuse = []
    for name, tree, _ in _sources():
        # Handed over directly, or bound with functools.partial for an executor call.
        executor_args: set[int] = set()
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in (
                    "async_add_executor_job", "partial"):
                executor_args |= {id(arg) for arg in call.args[:1]}
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not (isinstance(node, ast.Name) and node.id in names) or id(node) in executor_args:
                    continue
                direct_caller_ok = (isinstance(function, ast.FunctionDef)
                                    and function.name in names | EXECUTOR_CALLERS)
                if not direct_caller_ok:
                    misuse.append(f"{name}:{node.lineno} {function.name} uses {node.id}")
    assert misuse == []


def test_executor_callers_are_themselves_handed_to_an_executor():
    """_blueprint_restored_text calls an executor-only function directly, so it must run in one too."""
    handed = set()
    for _, tree, _ in _sources():
        for call in ast.walk(tree):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "async_add_executor_job" and call.args
                    and isinstance(call.args[0], ast.Name)):
                handed.add(call.args[0].id)
    assert EXECUTOR_CALLERS <= handed


async def test_esphome_availability_imports_nothing_without_esphome(hass, monkeypatch):
    """The tool listing and the settings view ask this on every call, on the event loop.

    Import attempts are recorded rather than sys.modules checked, because the import fails
    anyway where ESPHome's own libraries are not installed.
    """
    import builtins

    from custom_components.phoenix_mcp.tools.esphome import esphome_availability

    attempted: list[str] = []
    real_import = builtins.__import__

    def recording_import(name, *args, **kwargs):
        attempted.append(name)
        return real_import(name, *args, **kwargs)

    assert "esphome" not in hass.config.components
    monkeypatch.setattr(builtins, "__import__", recording_import)
    availability = esphome_availability(hass)
    monkeypatch.undo()
    assert availability.builder is False and availability.builder_live is False
    assert not [name for name in attempted if name.startswith("homeassistant.components.esphome")]


async def test_backup_manager_is_none_without_backup(hass):
    from custom_components.phoenix_mcp.tools.backup import _backup_manager

    assert "backup" not in hass.config.components
    assert _backup_manager(hass) is None
