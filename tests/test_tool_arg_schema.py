"""Every tool argument the code READS must be one the tool PUBLISHES.

An argument a handler acts on but the `inputSchema` never declares is invisible
to the caller, and a client that validates against the schema cannot send it at
all, which no server leniency reaches. A refusal that tells the agent to pass an
argument is useless if the schema omits it.

The walk is per MODULE, not per tool, deliberately. Arguments are often read far
from the handler that received them, in a helper several calls down, so
attributing a read to one tool would need dataflow analysis. Comparing the
union a module reads with the union its tools declare is coarser but answers the
question that matters: is this argument published ANYWHERE.

Two kinds of read are exempted by name, with a reason. An ALIAS is a spelling a
model might guess, accepted quietly so a near miss still works. An INTERNAL
argument is passed only by Phoenix to an executor (a restore replaying a
snapshot) and withheld on purpose: publishing it would hand callers a capability
the tool refuses.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from custom_components.phoenix_mcp.tool_defs import (
    _ENTITY_TOOL_DEFS,
    _NATIVE_TOOL_DEFS,
    _NATIVE_TOOL_NAMES,
    _SYSTEM_TOOL_DEFS,
)
from custom_components.phoenix_mcp.tool_contracts import (
    _RETIRED_REPLACEMENTS,
    _SERVICE_OBJECT_SYNTAX,
    normalize_tool_args,
)

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "phoenix_mcp"

# (module filename, argument) maps to why it is read but not published.
UNPUBLISHED_ARGS = {
    ("calendar.py", "_calendar_plan"): "INTERNAL server-created calendar identity, exact event fields and snapshot binding; public inputs are rebuilt before gating.",
    ("mcp_view.py", "aliases"): (
        "INTERNAL, not an alias. set_entity edits aliases by add/remove and has "
        "no absolute set-the-list form on purpose: Home Assistant builds its "
        "voice matching entirely from that list, so a whole-set write could drop "
        "the sentinel that means 'my own name' or empty the list and remove the "
        "entity from voice control. async_restore_version needs the absolute "
        "form to replay a snapshot, so the executor accepts it ONLY when "
        "_restore_ctx is set. test_mcp_registry_tools.py pins that a caller "
        "sending it inside public changes outside a restore is refused."
    ),
    ("mcp_view.py", "labels"): (
        "INTERNAL absolute form used only while restoring an entity or device "
        "version. Public set_entity/set_device edit labels with add/remove so a "
        "stale caller cannot replace labels it did not read."
    ),
    ("mcp_view.py", "disabled_by"): (
        "INTERNAL restore form preserving Home Assistant's exact disabler. Public "
        "set_entity and set_device expose only the user-controlled enabled boolean."
    ),
    ("mcp_view.py", "hidden_by"): (
        "INTERNAL restore form preserving Home Assistant's exact hider. Public "
        "set_entity exposes only the user-controlled hidden boolean."
    ),
    ("discovery.py", "name"): (
        "search_entities accepts `name` as an alias for its published `query`, "
        "so a model that guesses the other spelling still searches instead of "
        "silently searching for nothing. Publishing both would invite a caller "
        "to send two conflicting queries."
    ),
    ("authoring.py", "entity_id"): (
        "get_automation_traces accepts `entity_id` as an alias for its published "
        "`automation_id`: an agent holding the automation's entity id has the "
        "harder value to convert, and both resolve to the same automation."
    ),
    ("native.py", "target_levels"): (
        "INTERNAL exact-action payload stored only in a MESA approval for numeric "
        "HassSetVolumeRelative. The public tool accepts selectors plus volume_step; "
        "its registered executor replays this frozen entity-to-level map so approval "
        "cannot actuate a later, recomputed target state."
    ),
    ("native.py", "_relative_volume_plan_version"): (
        "INTERNAL compatibility marker stored beside target_levels. The approval "
        "executor rejects an old or unknown frozen-plan representation instead of "
        "guessing how to replay it."
    ),
}

# Catalog v2 handlers normalize structured public inputs to the internal executor
# shape after the public-schema boundary. These keys are deliberately internal.
NORMALIZED_ARGS = {
    "mcp_view.py": {"add_aliases", "add_labels", "area_id", "categories", "context_id", "detailed", "device_class", "domain", "fields", "hidden", "icon", "new_entity_id", "remove_aliases", "remove_labels", "service_data", "title", "use_device_name"},
    "discovery.py": {"area_id", "service_data"},
    "energy.py": {"device_name", "name", "new_statistic", "op", "source_type", "statistic"},
    "lovelace.py": {"op", "path", "value"},
}


def _tool_defs() -> dict[str, dict]:
    return {d["name"]: d for d in (*_ENTITY_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *_SYSTEM_TOOL_DEFS)}


def test_set_entity_exposes_only_validated_registry_reference_fields():
    """set_entity publishes only registry references validated before the gate and at apply time.

    A new reference field must add that validation first.
    """
    schema = _tool_defs()["set_entity"]["inputSchema"]
    assert set(schema["properties"]) == {"entity_id", "changes"}
    properties = set(schema["properties"]["changes"]["properties"])
    registry_references = {
        "area_id",
        "category_id",
        "categories",
        "device_id",
        "floor_id",
        "label_id",
        "labels",
        "add_labels",
        "remove_labels",
    }
    assert properties & registry_references == {
        "add_labels",
        "area_id",
        "categories",
        "remove_labels",
    }


def test_set_entity_publishes_reversible_registry_fields_and_null_clears():
    properties = _tool_defs()["set_entity"]["inputSchema"]["properties"]["changes"]["properties"]
    assert {
        "device_class",
        "enabled",
        "hidden",
        "add_labels",
        "remove_labels",
        "categories",
    } <= set(properties)
    for field in ("name", "icon", "area_id", "device_class"):
        assert properties[field]["type"] == ["string", "null"]
    assert properties["categories"]["additionalProperties"]["type"] == [
        "string",
        "null",
    ]


def test_set_device_publishes_only_reversible_user_metadata():
    schema = _tool_defs()["set_device"]["inputSchema"]
    assert set(schema["properties"]) == {"device_id", "changes"}
    properties = schema["properties"]["changes"]["properties"]
    assert set(properties) == {
        "name",
        "area_id",
        "enabled",
        "add_labels",
        "remove_labels",
    }
    for field in ("name", "area_id"):
        assert properties[field]["type"] == ["string", "null"]


def test_catalog_v2_removes_retired_flat_public_fields():
    defs = _tool_defs()
    retired = {
        "get_state": {"detailed", "fields"},
        "get_calendar_events": {"calendar_id"},
        "wait_for_approval": {"approval_id"},
        "get_relationships": {"entity_id", "device_id", "integration", "area", "label"},
        "get_logbook": {"entity_ids", "device_ids", "context_id"},
        "get_esphome_job": {"job_id", "file"},
        "edit_energy_config": {"op", "statistic", "device_name", "new_statistic", "source_type"},
        "patch_dashboard": {"url_path", "path", "value", "op"},
        "set_entity": {"name", "icon", "area_id", "new_entity_id"},
        "set_device": {"name", "area_id", "enabled"},
        "set_integration": {"title", "pref_disable_new_entities", "pref_disable_polling"},
    }
    for tool, fields in retired.items():
        assert not fields & set(defs[tool]["inputSchema"]["properties"])


def test_every_retired_field_error_contains_exact_replacement_syntax():
    for tool, replacements in _RETIRED_REPLACEMENTS.items():
        for field, replacement in replacements.items():
            _, error = normalize_tool_args(tool, {field: "retired-value"})
            assert error is not None
            assert f"{field} -> {replacement}" in error


@pytest.mark.parametrize("tool", ["call_service", "dry_run_service"])
def test_retired_flat_service_string_has_exact_object_migration(tool):
    _, error = normalize_tool_args(tool, {"service": "turn_on"})
    assert error is not None
    assert f"service -> {_SERVICE_OBJECT_SYNTAX}" in error


@pytest.mark.parametrize("tool", ["call_service", "dry_run_service"])
def test_mixed_retired_service_fields_are_reported_together(tool):
    _, error = normalize_tool_args(
        tool, {"domain": "light", "service": "turn_on", "entity_id": "light.a"}
    )
    assert error is not None
    assert "domain ->" in error
    assert "entity_id ->" in error
    assert f"service -> {_SERVICE_OBJECT_SYNTAX}" in error


@pytest.mark.parametrize("tool", ["call_service", "dry_run_service"])
def test_repeated_service_target_groups_merge_and_deduplicate(tool):
    normalized, error = normalize_tool_args(
        tool,
        {
            "service": {"domain": "light", "name": "turn_on"},
            "targets": [
                {"kind": "entity", "ids": ["light.a", "light.shared"]},
                {"kind": "device", "ids": ["device-a"]},
                {"kind": "entity", "ids": ["light.shared", "light.b"]},
                {"kind": "device", "ids": ["device-a", "device-b"]},
            ],
        },
    )

    assert error is None
    assert normalized["entity_id"] == ["light.a", "light.shared", "light.b"]
    assert normalized["device_id"] == ["device-a", "device-b"]


@pytest.mark.parametrize("tool", ["call_service", "dry_run_service"])
def test_all_service_target_cannot_be_mixed_with_narrower_groups(tool):
    _, error = normalize_tool_args(
        tool,
        {
            "service": {"domain": "light", "name": "turn_on"},
            "targets": [
                {"kind": "all"},
                {"kind": "entity", "ids": ["light.a"]},
            ],
        },
    )

    assert error == (
        "A target with kind all cannot be combined with entity, device, or area targets."
    )


def test_unrelated_unknown_fields_are_refused():
    _normalized, error = normalize_tool_args(
        "get_logbook", {"unrelated_future_field": True}
    )
    assert error and 'Unknown argument "unrelated_future_field".' in error


def test_remove_device_publishes_only_owner_selection():
    schema = _tool_defs()["remove_device"]["inputSchema"]
    assert set(schema["properties"]) == {"device_id", "config_entry_id"}
    assert schema["required"] == ["device_id"]


def _modules() -> list[pathlib.Path]:
    return [PACKAGE / "mcp_view.py", *sorted((PACKAGE / "tools").glob("*.py"))]


def _registered_handlers() -> dict[str, str]:
    """Map tool name to its handler function name, read off mcp_view's own registry.

    The registry is the source of truth, so a new tool is covered without editing
    this file.
    """
    tree = ast.parse((PACKAGE / "mcp_view.py").read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_register_tool"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[1], ast.Name)
        ):
            found[node.args[0].value] = node.args[1].id
    return found


def _module_of_function() -> dict[str, pathlib.Path]:
    """Map top-level function name to the module defining it."""
    out: dict[str, pathlib.Path] = {}
    for path in _modules():
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, path)
    return out


def _tools_by_module() -> dict[pathlib.Path, set[str]]:
    homes = _module_of_function()
    out: dict[pathlib.Path, set[str]] = {}
    for tool, handler in _registered_handlers().items():
        home = homes.get(handler)
        if home is not None:
            out.setdefault(home, set()).add(_NATIVE_TOOL_NAMES.get(tool, tool))
    return out


def _args_read(path: pathlib.Path) -> set[str]:
    """Every literal key the module reads off a dict named `args`.

    Covers both `args.get("x")` and `args["x"]`. A non-literal key cannot be
    checked and is skipped.
    """
    keys: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "args"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "args"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


MODULES_WITH_TOOLS = sorted(_tools_by_module(), key=lambda p: p.name)


@pytest.mark.parametrize("module", MODULES_WITH_TOOLS, ids=lambda p: p.name)
def test_every_argument_read_is_published_by_some_tool(module):
    defs = _tool_defs()
    declared: set[str] = set()
    for tool in _tools_by_module()[module]:
        declared |= set(defs.get(tool, {}).get("inputSchema", {}).get("properties", {}))
    exempt = {arg for (name, arg) in UNPUBLISHED_ARGS if name == module.name}
    undeclared = sorted(_args_read(module) - declared - exempt - NORMALIZED_ARGS.get(module.name, set()))
    assert not undeclared, (
        f"{module.name} reads tool arguments that no tool in it publishes: "
        f"{undeclared}. A caller cannot send an argument its schema does not "
        f"declare, so either add it to the tool's inputSchema in tool_defs.py, "
        f"or add it to UNPUBLISHED_ARGS with the reason it stays unpublished."
    )


def test_the_walk_actually_finds_arguments():
    """The walk still finds arguments, so the guard cannot pass while checking nothing.

    If the AST shapes stop matching (a helper that takes the dict under another
    name), every module comes back with an empty set and the suite stays green.
    """
    found = {m.name: _args_read(m) for m in MODULES_WITH_TOOLS}
    assert sum(len(v) for v in found.values()) > 100, found
    assert {"path", "file", "key"} <= found["config_files.py"]
    assert "expected_hash" in found["lovelace.py"], (
        "an argument read through helpers is no longer found; if it was removed "
        "on purpose, pick another live argument here"
    )


def test_exemptions_are_all_still_real():
    """Every exemption names an argument its module still reads.

    A stale exemption would quietly excuse a future argument of the same name.
    """
    for (name, arg), reason in UNPUBLISHED_ARGS.items():
        module = next((m for m in MODULES_WITH_TOOLS if m.name == name), None)
        assert module is not None, f"UNPUBLISHED_ARGS names a module that has no tools: {name}"
        assert arg in _args_read(module), f"{name} no longer reads {arg!r}; drop the exemption"
        assert len(reason) > 40, f"{name}/{arg} needs a written reason, not a placeholder"
