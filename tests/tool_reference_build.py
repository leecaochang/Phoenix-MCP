"""Build the tool data the Guide's tool reference is made from.

Every published tool, read from the registry: its exact schema and description, its
four MCP hints, the capabilities that offer it, what Confirm does to it, and where it is
offered; from tool_reference_data.py, its example call and the capabilities only some
of its calls need. The Guide's build turns frontend_src/guide/tool_reference.json into
one reference page per tool group (frontend_src/guide/reference.ts). The plain summary
of each tool is catalog text (guideTools.summary), in English and in each language that
translates the Guide's tools page.

tests/test_tool_reference.py fails when the file and the registry disagree. Rewrite it
after adding, removing or changing a tool:

    venv/bin/python -c "from tests.tool_reference_build import write; write()"
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "frontend_src" / "guide" / "tool_reference.json"

COMMENT = (
    "Generated from the tool registry by tests/tool_reference_build.py; do not edit. "
    "tests/test_tool_reference.py fails when it is out of date."
)


def _hold_on_confirm(name: str, read_only: bool, executors: set[str]) -> bool:
    """Whether Confirm holds this tool's call for approval, rather than letting it run.

    A read runs straight away; a write waits when an approval can run it. A write whose
    capability offers Confirm but that no approval can run would have no honest answer,
    so the build stops instead of guessing.
    """
    if read_only:
        return False
    if name in executors:
        return True
    raise SystemExit(f"{name}: a write whose capability offers Confirm, but no approval can run it")


def build() -> dict:
    """The reference data, from the registry as it is now."""
    # Imported for its side effect: registering every handler fills the executor registry.
    importlib.import_module("custom_components.phoenix_mcp.tool_registration")
    from custom_components.phoenix_mcp.const import CAPABILITY_NAMES, CONFIRM_AVAILABLE_CAPS, HOUSEHOLD_ADMIN_TOOLS
    from custom_components.phoenix_mcp.home_companion import wrapper_tool
    from custom_components.phoenix_mcp.mesa_tools import mesa_tool_defs
    from custom_components.phoenix_mcp.tool_catalog import _EXECUTOR_REGISTRY, _tool_any_caps, _tool_required_caps
    from custom_components.phoenix_mcp.tool_defs import (
        _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS, _NATIVE_TOOL_NAMES, _NATIVE_TOOL_PUBLIC_TO_INTERNAL, _SYSTEM_TOOL_DEFS,
    )
    from custom_components.phoenix_mcp.tool_reference_data import TOOL_CALL_CAPS, TOOL_EXAMPLES

    short_names = {public: short for short, public in _NATIVE_TOOL_NAMES.items()}
    executors = set(_EXECUTOR_REGISTRY)
    tools = []
    for definition in (*_ENTITY_TOOL_DEFS, *_SYSTEM_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *mesa_tool_defs(), wrapper_tool()):
        name = definition["name"]
        annotations = definition["annotations"]
        caps = list(_tool_required_caps(definition))
        caps_any = list(_tool_any_caps(definition))
        caps_when = {
            parameter: {value: list(needed) for value, needed in by_value.items()}
            for parameter, by_value in (definition.get("caps_when") or {}).items()
        }
        conditional = [cap for by_value in caps_when.values() for needed in by_value.values() for cap in needed]
        # Declared capabilities only: what Confirm does to a call that needs another one depends on
        # that call (a templated test_trigger needs Allow), which the overview explains.
        confirmable = [cap for cap in dict.fromkeys(caps + caps_any + conditional) if cap in CONFIRM_AVAILABLE_CAPS]
        internal = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(name, name)
        entry: dict = {
            "name": name,
            "description": definition["description"],
            "inputSchema": definition.get("inputSchema", {"type": "object", "properties": {}}),
            "annotations": {key: annotations[key] for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")},
            "caps": caps,
            "capsAny": caps_any,
            "capsWhen": caps_when,
            "capsSometimes": [cap for cap in TOOL_CALL_CAPS.get(name, ()) if cap not in caps + caps_any + conditional],
            "example": TOOL_EXAMPLES[name],
        }
        if name in short_names:
            entry["shortName"] = short_names[name]
        if confirmable:
            entry["onConfirm"] = "approval" if _hold_on_confirm(internal, annotations["readOnlyHint"], executors) else "runs"
        if definition.get("requires"):
            entry["requires"] = definition["requires"]
        if name in HOUSEHOLD_ADMIN_TOOLS:
            entry["householdAdminOnly"] = True
        if name == wrapper_tool()["name"]:
            entry["companionOnly"] = True
        tools.append(entry)
    unknown = sorted(set(TOOL_CALL_CAPS) - {tool["name"] for tool in tools})
    if unknown:
        raise SystemExit(f"tool_reference_data.py: TOOL_CALL_CAPS names no such tool {unknown}")
    missing = sorted({tool["name"] for tool in tools} - set(TOOL_EXAMPLES))
    extra = sorted(set(TOOL_EXAMPLES) - {tool["name"] for tool in tools})
    if missing or extra:
        raise SystemExit(f"tool_reference_data.py: missing {missing}, no such tool {extra}")
    return {"comment": COMMENT, "capabilities": list(CAPABILITY_NAMES), "tools": tools}


def render() -> str:
    """The file's text, so the test compares exactly what write() puts on disk."""
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


def write() -> None:
    """Rewrite the file from the registry as it is now."""
    OUT.write_text(render(), encoding="utf-8")
