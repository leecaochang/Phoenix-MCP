"""Measure exported MCP catalogs without sending prompts or executing tools."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


def encoded(value: Any) -> bytes:
    """Canonical compact UTF-8 JSON; sizes are bytes, not token estimates."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def catalog_tools(document: Any) -> list[dict]:
    """Accept a tool list, tools object, or successful tools/list RPC envelope."""
    if isinstance(document, dict):
        document = document.get("result", document)
        if isinstance(document, dict):
            document = document.get("tools")
    if not isinstance(document, list) or not all(
        isinstance(tool, dict) and isinstance(tool.get("name"), str)
        and isinstance(tool.get("inputSchema"), dict) for tool in document
    ):
        raise ValueError("Expected a complete tools/list catalog")
    if len({tool["name"] for tool in document}) != len(document):
        raise ValueError("Duplicate tool names")
    return document


def schema_without_descriptions(schema: Any) -> Any:
    """Remove schema descriptions, preserving properties named description."""
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "description":
            continue
        if key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}:
            result[key] = {name: schema_without_descriptions(child) for name, child in value.items()}
        elif key in {"items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"}:
            result[key] = schema_without_descriptions(value)
        elif key in {"oneOf", "anyOf", "allOf", "prefixItems"}:
            result[key] = [schema_without_descriptions(child) for child in value]
        else:
            result[key] = value
    return result


def description_rows(value: Any, path: str = "") -> list[dict]:
    """Inventory every schema description, including array and union branches."""
    rows = []
    if isinstance(value, dict):
        if isinstance(value.get("description"), str):
            rows.append({"path": path, "description": value["description"]})
        for key, child in value.items():
            rows.extend(description_rows(child, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(description_rows(child, f"{path}/{index}"))
    return rows


def missing_parameter_descriptions(schema: Any, path: str = "") -> list[str]:
    """Inspect schema nodes, including properties inside arrays and unions."""
    if not isinstance(schema, dict):
        return []
    missing = []
    for name, child in schema.get("properties", {}).items():
        current = f"{path}/{name}"
        if not isinstance(child, dict) or not str(child.get("description", "")).strip():
            missing.append(current)
        missing.extend(missing_parameter_descriptions(child, current))
    for keyword in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"):
        missing.extend(missing_parameter_descriptions(schema.get(keyword), f"{path}/{keyword}"))
    for keyword in ("oneOf", "anyOf", "allOf", "prefixItems"):
        for index, child in enumerate(schema.get(keyword, [])):
            missing.extend(missing_parameter_descriptions(child, f"{path}/{keyword}/{index}"))
    for keyword in ("$defs", "definitions", "patternProperties", "dependentSchemas"):
        for name, child in schema.get(keyword, {}).items():
            missing.extend(missing_parameter_descriptions(child, f"{path}/{keyword}/{name}"))
    return missing


def measure_catalog(tools: list[dict], instructions: str = "", selected: list[str] | None = None) -> dict:
    """Keep complete-catalog, repeated-primer and deferred projections distinct."""
    names = {tool["name"] for tool in tools}
    selected_names = set(selected or [])
    if selected_names - names:
        raise ValueError("Selected tools absent from catalog: " + ", ".join(sorted(selected_names - names)))
    descriptions = [row for tool in tools for row in description_rows(tool, tool["name"])]
    repeated = Counter(row["description"] for row in descriptions)
    semantics = [{"name": tool["name"], "inputSchema": schema_without_descriptions(tool["inputSchema"]),
                  "annotations": tool.get("annotations", {})} for tool in tools]
    # This projection measures discovery cost only; it never replaces a catalog.
    stubs = [{"name": tool["name"], "description": tool.get("description", "")} for tool in tools]
    expanded = [tool for tool in tools if tool["name"] in selected_names]
    return {
        "tool_count": len(tools),
        "catalog_utf8_bytes": len(encoded(tools)),
        "catalog_sha256": hashlib.sha256(encoded(tools)).hexdigest(),
        "schema_and_annotation_sha256": hashlib.sha256(encoded(semantics)).hexdigest(),
        "missing_parameter_descriptions": [path for tool in tools for path in
                                           missing_parameter_descriptions(tool["inputSchema"], tool["name"])],
        "field_json_bytes": {key: sum(len(encoded(tool.get(key, {} if key != "description" else "")))
                                      for tool in tools) for key in ("description", "inputSchema", "annotations")},
        "instructions_utf8_bytes": len(instructions.encode()),
        "primer_repeated_per_tool_utf8_bytes": len(instructions.encode()) * len(tools),
        "deferred_projection": {"inventory_utf8_bytes": len(encoded(stubs)),
                                "selected_names": sorted(selected_names),
                                "selected_schemas_utf8_bytes": len(encoded(expanded)),
                                "all_schemas_utf8_bytes": len(encoded(tools)),
                                "note": "Projection only; actual client discovery/context retention is unobserved."},
        "repeated_descriptions": sorted(
            [{"description": text, "occurrences": count, "repeated_utf8_bytes": len(text.encode()) * (count - 1)}
             for text, count in repeated.items() if count > 1],
            key=lambda row: row["repeated_utf8_bytes"], reverse=True),
        "largest_tools": sorted(
            [{"name": tool["name"], "utf8_bytes": len(encoded(tool))} for tool in tools],
            key=lambda row: row["utf8_bytes"], reverse=True)[:10],
    }


def main() -> None:
    """Report measurements from caller-provided, credential-free snapshots."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--discovery", type=Path, help="initialize/server-discover JSON containing instructions")
    parser.add_argument("--selected-tool", action="append", default=[], help="Model deferred expansion of this tool")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    tools = catalog_tools(json.loads(args.catalog.read_text()))
    instructions = ""
    if args.discovery:
        discovery = json.loads(args.discovery.read_text())
        instructions = discovery.get("result", discovery).get("instructions", "")
        if not isinstance(instructions, str):
            parser.error("instructions must be a string")
    report = measure_catalog(tools, instructions, args.selected_tool)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(content)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
