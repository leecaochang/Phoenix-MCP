"""Pin the enumerable parts of the Guide against the code.

Documentation drift is mostly enumerable facts (tool names, provider kinds,
error codes, SSE frame names, routes, capability lists, events) restated by
hand and left behind by code changes. Each expectation is derived from the code
side, so a new tool, provider, code, frame, route, capability or event is
covered without a new test. Extraction-based checks assert a floor on what they
extracted, so a broken extraction fails loudly instead of passing vacuously.
The frontend Guide contract covers what the panel itself points at: a section
for every setting, persona and capability.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from custom_components.phoenix_mcp.const import (
    AGENTCLI_PROVIDERS,
    CAPABILITY_NAMES,
)
from custom_components.phoenix_mcp.mcp_view import (
    _ENTITY_TOOL_DEFS,
    _NATIVE_TOOL_DEFS,
    _SYSTEM_TOOL_DEFS,
)
from custom_components.phoenix_mcp.mesa_tools import mesa_tool_defs

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUIDE = ROOT / "frontend_src" / "guide" / "topics" / "en"
PKG = ROOT / "custom_components" / "phoenix_mcp"


def _page(*names: str) -> str:
    return "\n".join((GUIDE / f"{name}.md").read_text(encoding="utf-8") for name in names)


def _admin_api_pages() -> str:
    """Every Admin API page: the overview and one page per endpoint group."""
    pages = sorted(path.stem for path in GUIDE.glob("topic.adminApi*.md"))
    assert len(pages) >= 6, f"Admin API page lookup broke: {pages}"
    return _page(*pages)


def test_every_tool_is_named_on_the_tools_page() -> None:
    """A tool is findable by name, in a section's text or its search keywords.

    Native tools are announced as `<domain>__<intent>`; the page names the intent.
    """
    names = {d["name"].split("__")[-1] for d in (
        list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + list(mesa_tool_defs()))}
    assert len(names) >= 100, f"tool extraction broke: {len(names)}"
    page = _page("topic.tools")
    missing = sorted(name for name in names if not re.search(rf"\b{name}\b", page))
    assert missing == [], f"topic.tools names no section for: {missing}"


def test_admin_api_documents_every_capability() -> None:
    pages = _admin_api_pages()
    missing = sorted(c for c in CAPABILITY_NAMES if f"`{c}`" not in pages)
    assert missing == [], f"Admin API pages missing caps: {missing}"


def test_admin_api_documents_every_provider_kind() -> None:
    # Code-wrapped, not bare substring: "meta" would otherwise be satisfied by
    # the word "metadata" anywhere on the pages.
    pages = _admin_api_pages()
    missing = sorted(k for k in AGENTCLI_PROVIDERS if f"`{k}`" not in pages)
    assert missing == [], f"Admin API pages missing provider kinds: {missing}"


def test_admin_api_documents_every_emitted_error_code() -> None:
    src = "\n".join((PKG / name).read_text(encoding="utf-8") for name in ("admin_view.py", "agentcli.py"))
    codes = set(re.findall(r'_err\(\s*"([a-z_]+)"', src))
    assert len(codes) >= 6, f"error-code extraction broke: {sorted(codes)}"
    pages = _admin_api_pages()
    missing = sorted(c for c in codes if f"`{c}`" not in pages)
    assert missing == [], f"Admin API pages missing error codes: {missing}"


def test_admin_api_documents_every_sse_frame() -> None:
    src = (PKG / "agentcli.py").read_text(encoding="utf-8")
    frames = set(re.findall(r'emit\(\s*"([a-z_]+)"', src))
    assert len(frames) >= 10, f"SSE frame extraction broke: {sorted(frames)}"
    page = _page("topic.adminApiChat")
    missing = sorted(f for f in frames if f"`{f}`" not in page)
    assert missing == [], f"topic.adminApiChat missing SSE frames: {missing}"


@pytest.mark.parametrize("pages,module_names", [
    (("topic.agentEndpoints",), ("mcp_view", "skill_view")),
    ((), ("admin_view", "agentcli")),
])
def test_route_reference_matches_methods_and_paths(pages, module_names) -> None:
    """A documented URL alone cannot pin an omitted DELETE or wrong verb."""
    import importlib
    from homeassistant.components.http import HomeAssistantView

    expected = set()
    for name in module_names:
        module = importlib.import_module(f"custom_components.phoenix_mcp.{name}")
        for cls in vars(module).values():
            if not isinstance(cls, type) or not issubclass(cls, HomeAssistantView) or cls.__module__ != module.__name__:
                continue
            url = cls.__dict__.get("url")
            if not isinstance(url, str):
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                if method in cls.__dict__:
                    expected.add((method.upper(), re.sub(r"\{[^}]+\}", "{}", url)))
    assert len(expected) >= (4 if pages else 60)
    source = _page(*pages) if pages else _admin_api_pages()
    documented = {
        (method, re.sub(r"\{[^}]+\}", "{}", path))
        for methods, path in re.findall(r"\b((?:GET|POST|PUT|PATCH|DELETE)(?:/(?:GET|POST|PUT|PATCH|DELETE))*)\s+(/api/[\w/{}.-]+)", source)
        for method in methods.split("/")
    }
    # The agent endpoint page names its missing GET handler to say it answers
    # 405; the claim holds only while no GET handler exists.
    refused = {("GET", "/api/phoenix-mcp")} if pages else set()
    assert not refused & expected
    assert expected <= documented, sorted(expected - documented)
    assert documented - refused <= expected, sorted(documented - refused - expected)


def test_readmes_name_every_manifest_requirement() -> None:
    manifest = json.loads((PKG / "manifest.json").read_text())
    for requirement in manifest["requirements"]:
        for readme in ROOT.glob("README*.md"):
            assert f"`{requirement}`" in readme.read_text(), readme.name


def test_documented_mesa_baseline_and_default_match_runtime() -> None:
    from custom_components.phoenix_mcp.mesa_core import DOMAIN_SAFETY_BASELINE
    from custom_components.phoenix_mcp.access_key_store import GlobalSettings

    assert GlobalSettings().mesa_mode == "advisory"
    assert DOMAIN_SAFETY_BASELINE["light"] == "autonomous"
    assert DOMAIN_SAFETY_BASELINE["lock"] == DOMAIN_SAFETY_BASELINE["alarm_control_panel"] == "prohibited"
    assert all(mode == "confirm" for domain, mode in DOMAIN_SAFETY_BASELINE.items()
               if domain not in ("light", "lock", "alarm_control_panel"))
    rules = _page("topic.mesaRules")
    assert "lights are Autonomous, locks and alarm panels are Prohibited, and everything else is Confirm" in rules
    assert "**{{label:settings.mesaModeAdvisory}}** (default)" in rules


def test_event_reference_covers_runtime_events_and_rate_limit_coalescing() -> None:
    source = _page("topic.running")
    events = set()
    for path in PKG.rglob("*.py"):
        if "mesa_core" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or not node.args:
                continue
            if node.func.attr == "async_fire":
                argument = node.args[0]
            elif (node.func.attr == "add_job" and len(node.args) >= 2
                  and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "async_fire"):
                argument = node.args[1]
            else:
                continue
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                event = argument.value
            elif (isinstance(argument, ast.JoinedStr) and len(argument.values) == 2
                  and isinstance(argument.values[0], ast.FormattedValue)
                  and isinstance(argument.values[0].value, ast.Name) and argument.values[0].value.id == "DOMAIN"
                  and isinstance(argument.values[1], ast.Constant)):
                event = "phoenix_mcp" + argument.values[1].value
            else:
                continue
            if event.startswith("phoenix_mcp_"):
                events.add(event)
    assert len(events) >= 8
    documented = set(re.findall(r"^- `(phoenix_mcp_\w+)`:", source, re.M))
    assert events <= documented, events - documented
    row = re.search(r"^- `phoenix_mcp_rate_limited`:.*$", source, re.M).group(0)
    assert "at most once a minute per key" in row
