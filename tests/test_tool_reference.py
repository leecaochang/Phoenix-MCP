"""The Guide's tool reference against the tool registry it describes.

The reference is generated (tests/tool_reference_build.py writes
frontend_src/guide/tool_reference.json; the Guide's build turns it into pages), so
these guards hold its hand-written parts to the registry: every tool has an example
that its own schema accepts and a summary in every catalog of a language that
translates the tools page (and no other catalog has one), no example or summary
outlives its tool, each capability a tool checks only for some calls is checked in its
code, every group has a title, and every capability's section of the Capabilities
page lists its tools.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from custom_components.phoenix_mcp.const import CAPABILITY_NAMES
from custom_components.phoenix_mcp.tool_catalog import _TOOL_HANDLERS
from custom_components.phoenix_mcp.tool_defs import _NATIVE_TOOL_PUBLIC_TO_INTERNAL
from custom_components.phoenix_mcp.tool_reference_data import TOOL_CALL_CAPS, TOOL_EXAMPLES

from .tool_reference_build import render

REPO = Path(__file__).resolve().parents[1]
CATALOGS = REPO / "custom_components" / "phoenix_mcp" / "catalogs"
GUIDE = REPO / "frontend_src" / "guide" / "topics"
REFERENCE = REPO / "frontend_src" / "guide" / "tool_reference.json"


@pytest.fixture(scope="module")
def reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def _catalogs() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(CATALOGS.glob("*.json"))}


def test_reference_data_is_current() -> None:
    """A tool added, removed or changed without regenerating fails here, naming the script."""
    assert REFERENCE.read_text(encoding="utf-8") == render(), (
        "frontend_src/guide/tool_reference.json is stale: rewrite it with "
        'venv/bin/python -c "from tests.tool_reference_build import write; write()"'
    )


def test_every_tool_has_an_example_its_schema_accepts(reference: dict) -> None:
    names = {tool["name"] for tool in reference["tools"]}
    assert len(names) >= 170, f"tool extraction broke: {len(names)}"
    assert set(TOOL_EXAMPLES) == names
    problems = [
        f"{tool['name']}: {error.message}"
        for tool in reference["tools"]
        for error in Draft202012Validator(tool["inputSchema"]).iter_errors(tool["example"])
    ]
    assert problems == []


@pytest.mark.parametrize("language", sorted(path.stem for path in CATALOGS.glob("*.json")))
def test_every_guide_language_summarizes_exactly_the_tools(reference: dict, language: str) -> None:
    """A language that translates the tools page has its own reference; any other reads English's."""
    names = {tool["name"] for tool in reference["tools"]}
    section = _catalogs()[language].get("guideTools")
    if language != "en" and not (GUIDE / language / "topic.tools.md").is_file():
        assert section is None, f"{language} does not translate topic.tools, so its catalog carries no guideTools"
        return
    summaries = (section or {}).get("summary", {})
    assert sorted(names - set(summaries)) == [], f"{language}: tools without a summary"
    assert sorted(set(summaries) - names) == [], f"{language}: summaries of tools that no longer exist"
    assert [name for name, text in summaries.items() if not isinstance(text, str) or not text.strip()] == []


def test_every_group_has_a_title_and_every_tool_a_group(reference: dict) -> None:
    """Groups are the overview's sections; a tool belongs to the one whose search words name it."""
    source = (GUIDE / "en" / "topic.tools.md").read_text(encoding="utf-8")
    header = json.loads(source.split("---\n")[1])
    sections = re.findall(r"^## .*\{#([a-z0-9-]+)\}$", source, re.M)
    words = header["sectionKeywords"]
    groups = {}
    for tool in reference["tools"]:
        names = {tool["name"], tool.get("shortName")}
        found = [anchor for anchor in sections if names & set(words.get(anchor, []))]
        assert len(found) == 1, f"{tool['name']} is in {found or 'no'} sections' sectionKeywords"
        groups.setdefault(found[0], []).append(tool["name"])
    titles = _catalogs()["en"]["guideTools"]["groups"]
    assert set(titles) == set(groups)


def test_every_capability_section_lists_its_tools() -> None:
    """The build expands each line into the tools the capability allows; a capability without one shows none."""
    for language in ("en", "zh-Hans", "zh-Hant"):
        page = (GUIDE / language / "window.capabilities.md").read_text(encoding="utf-8")
        sections = re.split(r"^#{2,3} .*\{#([a-z0-9-]+)\}$", page, flags=re.M)
        lists = {sections[i]: re.findall(r"^\{\{tools:([a-z_]+)\}\}$", sections[i + 1], re.M) for i in range(1, len(sections), 2)}
        for cap in CAPABILITY_NAMES:
            anchor = cap.removeprefix("cap_").replace("_", "-")
            assert lists.get(anchor) == [cap], f"{language}: #{anchor} must hold {{{{tools:{cap}}}}}"
        assert lists.get("no-capability") == ["none"], f"{language}: #no-capability must hold {{{{tools:none}}}}"


def test_exact_text_cannot_break_the_page(reference: dict) -> None:
    """Descriptions ship verbatim inside code blocks, and Guide labels are filled in after rendering."""
    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, list):
            for value in node:
                yield from strings(value)
    bad = sorted({tool["name"] for tool in reference["tools"]
                  for text in strings([tool["description"], tool["inputSchema"], tool["example"]])
                  if "```" in text or "{{label:" in text or "{{toolText:" in text})
    assert bad == []


def _code_reach(module_name: str) -> str:
    """The source of a module and of every Phoenix module it imports from directly.

    A module naming most capabilities (const.py lists them all) is a registry, not a check,
    so it is left out: otherwise every capability would appear checked.
    """
    module = importlib.import_module(module_name)
    source = Path(module.__file__).read_text(encoding="utf-8")
    package = module_name.rsplit(".", 1)[0]
    texts = [source]
    for relative in re.findall(r"^from (\.+[\w.]*) import", source, re.M):
        dots = len(relative) - len(relative.lstrip("."))
        base = package.rsplit(".", dots - 1)[0] if dots > 1 else package
        name = f"{base}.{relative.lstrip('.')}" if relative.lstrip(".") else base
        try:
            text = Path(importlib.import_module(name).__file__).read_text(encoding="utf-8")
        except (ImportError, TypeError):
            continue
        if len(set(re.findall(r'"(cap_[a-z_]+)"', text)) & set(CAPABILITY_NAMES)) < len(CAPABILITY_NAMES) // 3:
            texts.append(text)
    return "\n".join(texts)


def test_capabilities_some_calls_need_are_checked_in_the_code(reference: dict) -> None:
    """Each listed capability is named by the tool's handler or a module it imports, and none is already declared."""
    tools = {tool["name"]: tool for tool in reference["tools"]}
    problems = []
    for name, caps in TOOL_CALL_CAPS.items():
        handler = _TOOL_HANDLERS[_NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(name, name)][0]
        reach = _code_reach(handler.__module__)
        problems += [f"{name}: {cap} appears nowhere its handler reaches" for cap in caps if f'"{cap}"' not in reach]
        declared = set(tools[name]["caps"]) | set(tools[name]["capsAny"])
        problems += [f"{name}: {cap} is already declared" for cap in caps if cap in declared]
        assert len(set(caps)) == len(caps), f"{name}: a capability is listed twice"
    assert problems == []
