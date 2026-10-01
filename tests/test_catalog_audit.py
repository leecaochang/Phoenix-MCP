"""Check catalog measurements and description-only semantic comparisons."""

import pytest

from scripts.audit_mcp_catalog import (
    catalog_tools,
    description_rows,
    encoded,
    measure_catalog,
    missing_parameter_descriptions,
    schema_without_descriptions,
)


def test_schema_comparison_preserves_named_properties_and_literal_values():
    schema = {"description": "Help", "type": "object", "properties": {
        "description": {"type": "string", "description": "User-authored text", "default": "description"},
        "rows": {"type": "array", "items": {"anyOf": [
            {"type": "integer", "minimum": 0, "description": "Index"},
            {"type": "string", "enum": ["description"], "description": "Key"},
        ]}},
    }, "required": ["description"], "additionalProperties": False}
    cleaned = schema_without_descriptions(schema)
    assert cleaned["properties"]["description"] == {"type": "string", "default": "description"}
    assert cleaned["required"] == ["description"]
    assert cleaned["additionalProperties"] is False
    assert cleaned["properties"]["rows"]["items"]["anyOf"] == [
        {"type": "integer", "minimum": 0}, {"type": "string", "enum": ["description"]}]
    assert len(description_rows(schema)) == 4
    assert missing_parameter_descriptions(schema) == ["/rows"]
    assert missing_parameter_descriptions({"type": "array", "items": {"oneOf": [
        {"properties": {"step_id": {"type": "string"}}},
        {"properties": {"description": {"type": "string"}}},
    ]}}) == ["/items/oneOf/0/step_id", "/items/oneOf/1/description"]


def test_bytes_deferred_projection_and_semantic_hash_use_complete_unicode_catalog():
    tools = [{"name": "read", "description": "读取", "inputSchema": {"type": "object"}},
             {"name": "write", "description": "Write", "inputSchema": {"type": "object"},
              "annotations": {"readOnlyHint": False}}]
    report = measure_catalog(tools, "安全", ["read"])
    assert report["catalog_utf8_bytes"] == len(encoded(tools))
    assert report["instructions_utf8_bytes"] == 6
    assert report["primer_repeated_per_tool_utf8_bytes"] == 12
    assert report["tool_count"] == 2
    assert report["deferred_projection"]["selected_schemas_utf8_bytes"] == len(encoded(tools[:1]))
    changed = [{**tool, "description": "Shorter"} for tool in tools]
    assert measure_catalog(changed)["schema_and_annotation_sha256"] == report["schema_and_annotation_sha256"]
    changed[1]["annotations"] = {"readOnlyHint": True}
    assert measure_catalog(changed)["schema_and_annotation_sha256"] != report["schema_and_annotation_sha256"]


def test_envelopes_and_failed_or_incomplete_discovery():
    tools = [{"name": "read", "inputSchema": {"type": "object"}}]
    for envelope in (tools, {"tools": tools}, {"jsonrpc": "2.0", "result": {"tools": tools}}):
        assert catalog_tools(envelope) == tools
    for invalid in ({"error": {"code": -32601}}, {"tools": [{"name": "read"}]}, tools * 2):
        with pytest.raises(ValueError):
            catalog_tools(invalid)
    with pytest.raises(ValueError, match="absent"):
        measure_catalog(tools, selected=["missing"])
