"""Guard: the import layers stay one-way, and a local import states why it is local.

The layers, lowest first: shared primitives and tool_catalog, then tools/, then
tool_dispatch, then the agent turn and home_companion_turn, then the ingress views.
tool_registration sits above every tool and is the only module that names them all.
Static analysis cannot see a cycle hidden in a function body, so a first-party
import inside a function is refused outright.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from custom_components.phoenix_mcp.home_companion_turn import run_request
from custom_components.phoenix_mcp.tool_catalog import _COMPANION_TURN, _EXECUTOR_REGISTRY, _TOOL_HANDLERS

PACKAGE = Path(__file__).parents[1] / "custom_components" / "phoenix_mcp"
# The package entry keeps its imports inside setup so that a Home Assistant release
# too old for them still reaches the version refusal; routing has its own pass.
LOCAL_IMPORT_EXEMPT_DIRS = {"mesa_core", "routing"}
LOCAL_REASON = "# Local:"
ABOVE_TOOLS = {"tool_dispatch", "tool_registration", "mcp_transport", "agent_turn", "agent_chat",
               "agentcli_views", "home_companion_turn"}
ABOVE_PROVIDERS = {"tool_dispatch", "tool_registration", "mcp_transport", "agent_turn", "agent_chat",
                   "agentcli_views", "voice_agent", "ai_task", "assist_api"}


def _modules() -> dict[str, Path]:
    return {
        ".".join(path.relative_to(PACKAGE).with_suffix("").parts).removesuffix(".__init__"): path
        for path in sorted(PACKAGE.rglob("*.py")) if "mesa_core" not in path.parts
    }


def _target(module: str, path: Path, node: ast.ImportFrom) -> str:
    parts = module.split(".")
    package = parts if path.name == "__init__.py" else parts[:-1]
    base = package[:len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _all_first_party_imports(path: Path, module: str) -> set[str]:
    return {
        _target(module, path, node) for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level
    }


def _top_level_edges() -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    modules = _modules()
    for module, path in modules.items():
        found: set[str] = set()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ImportFrom) and node.level:
                base = _target(module, path, node)
                found |= {f"{base}.{alias.name}".lstrip(".") if f"{base}.{alias.name}".lstrip(".") in modules else base
                          for alias in node.names}
        edges[module] = found & set(modules)
    return edges


def _local_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(function):
                if isinstance(node, ast.Import | ast.ImportFrom):
                    yield node


def _local_import_rule_applies(module: str, path: Path) -> bool:
    return module != "__init__" and path.parent.name != "custom_components" and not (
        set(path.relative_to(PACKAGE).parts) & LOCAL_IMPORT_EXEMPT_DIRS)


@pytest.mark.parametrize("module", [name for name in _modules() if name.startswith("tools.")])
def test_a_tool_module_never_imports_a_layer_above_it(module: str) -> None:
    assert not _all_first_party_imports(_modules()[module], module) & ABOVE_TOOLS


@pytest.mark.parametrize("module", [name for name in _modules() if name.startswith("llm.")])
def test_a_provider_module_never_imports_the_agent_or_a_transport(module: str) -> None:
    assert not _all_first_party_imports(_modules()[module], module) & ABOVE_PROVIDERS


def test_shared_catalog_and_dispatch_never_import_the_registration() -> None:
    modules = _modules()
    for module in ("tool_catalog", "tool_dispatch"):
        assert "tool_registration" not in _all_first_party_imports(modules[module], module)


def test_the_module_level_import_graph_has_no_cycle() -> None:
    edges = _top_level_edges()
    assert sum(len(found) for found in edges.values()) > 200, "the scan found almost no imports"
    state: dict[str, int] = {}

    def visit(module: str, trail: tuple[str, ...]) -> None:
        state[module] = 1
        for target in edges[module]:
            assert state.get(target) != 1, " > ".join((*trail, module, target))
            if target not in state:
                visit(target, (*trail, module))
        state[module] = 2

    for module in edges:
        if module not in state:
            visit(module, ())


def test_no_first_party_import_hides_in_a_function() -> None:
    found = [
        f"{module}:{node.lineno}" for module, path in _modules().items()
        if _local_import_rule_applies(module, path)
        for node in _local_imports(path)
        if isinstance(node, ast.ImportFrom) and node.level
    ]
    assert found == []


def test_every_local_import_states_its_reason() -> None:
    missing = []
    checked = 0
    for module, path in _modules().items():
        if not _local_import_rule_applies(module, path):
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        for node in _local_imports(path):
            checked += 1
            line = node.lineno - 2
            while lines[line].strip().startswith(("from ", "import ")) or lines[line].strip() == ")":
                line -= 1
            if not lines[line].strip().startswith(LOCAL_REASON):
                missing.append(f"{module}:{node.lineno}")
    assert checked > 50, "the scan found almost no local imports"
    assert missing == []


def test_registration_fills_every_slot_dispatch_reads() -> None:
    assert _TOOL_HANDLERS and _EXECUTOR_REGISTRY
    assert _COMPANION_TURN == [run_request]
