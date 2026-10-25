"""No filesystem call runs on Home Assistant's event loop.

A blocking call on the loop stalls every integration, and HA's own runtime
detector covers only some primitives (open, listdir), not stat-based ones such as
os.path.isfile or os.path.realpath. This static guard covers both: it walks every
coroutine, every @callback function and every lambda they run in place (a lazy
approval diff is one), follows calls into the package's own sync helpers, and
reports any path that reaches a filesystem primitive without an executor hop.
A function handed to async_add_executor_job is a reference, not a call, so the
executor hop is what clears it. The vendored mesa-core package is excluded.
"""

from __future__ import annotations

import ast
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "phoenix_mcp"
EXCLUDED_DIRS = {"mesa_core", "__pycache__"}

# Dotted prefixes of blocking calls. A match on the prefix covers the whole family.
BLOCKING_PREFIXES = (
    ("open",),
    ("os", "path", "isfile"), ("os", "path", "isdir"), ("os", "path", "exists"),
    ("os", "path", "lexists"), ("os", "path", "islink"), ("os", "path", "getsize"),
    ("os", "path", "getmtime"), ("os", "path", "realpath"), ("os", "path", "samefile"),
    ("os", "stat"), ("os", "lstat"), ("os", "listdir"), ("os", "scandir"), ("os", "walk"),
    ("os", "remove"), ("os", "unlink"), ("os", "rename"), ("os", "replace"),
    ("os", "makedirs"), ("os", "mkdir"), ("os", "rmdir"),
    ("shutil",), ("glob",),
)
# pathlib methods that touch the disk; the names are unambiguous across the package.
PATH_METHODS = {"read_text", "read_bytes", "write_text", "write_bytes", "is_file", "is_dir", "iterdir", "rglob"}
EXECUTOR_CALLS = {"async_add_executor_job", "run_in_executor", "to_thread"}


def _dotted(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return None


def _primitive(call: ast.Call) -> str | None:
    name = _dotted(call.func)
    if name is None:
        return None
    for prefix in BLOCKING_PREFIXES:
        if name[: len(prefix)] == prefix:
            return ".".join(name)
    if isinstance(call.func, ast.Attribute) and call.func.attr in PATH_METHODS:
        return ".".join(name)
    return None


def _is_callback(fn: ast.FunctionDef) -> bool:
    return any(_dotted(d) in (("callback",), ("core", "callback")) for d in fn.decorator_list)


class _Module:
    """One module's top-level functions and the names it imports from the package."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.name = ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.imports: dict[str, tuple[str, str]] = {}
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = self.name.split(".")[: -node.level] if node.level else []
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = (target, alias.name)


def _load() -> dict[str, _Module]:
    modules = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if EXCLUDED_DIRS & set(path.relative_to(PACKAGE).parts):
            continue
        module = _Module(path)
        modules[module.name] = module
    return modules


def _runs_in_place(fn: ast.AST):
    """Nodes that execute when fn runs: nested defs excluded, in-place lambdas included."""
    stack = [n for n in getattr(fn, "body", []) if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if isinstance(fn, ast.Lambda):
        stack = [fn.body]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(child, ast.Lambda) and isinstance(node, ast.Call):
                callee = _dotted(node.func)
                if callee and callee[-1] in EXECUTOR_CALLS:
                    continue
            if isinstance(child, ast.Lambda):
                stack.append(child.body)
                continue
            stack.append(child)


def _resolve(modules: dict[str, _Module], module: _Module, call: ast.Call):
    """The package function a call reaches, when it can be named statically."""
    name = _dotted(call.func)
    if not name:
        return None
    if len(name) == 1:
        if name[0] in module.functions:
            return module, module.functions[name[0]]
        if name[0] in module.imports:
            target, attr = module.imports[name[0]]
            other = modules.get(target)
            if other and attr in other.functions:
                return other, other.functions[attr]
    elif len(name) == 2 and name[0] in module.imports:
        target, attr = module.imports[name[0]]
        other = modules.get(f"{target}.{attr}" if target else attr)
        if other and name[1] in other.functions:
            return other, other.functions[name[1]]
    return None


def _blocking_paths(modules: dict[str, _Module]) -> list[str]:
    memo: dict[int, str | None] = {}

    def reaches(module: _Module, fn: ast.FunctionDef, seen: frozenset[int]) -> str | None:
        key = id(fn)
        if key in memo:
            return memo[key]
        if key in seen:
            return None
        memo_value = None
        for node in _runs_in_place(fn):
            if not isinstance(node, ast.Call):
                continue
            hit = _primitive(node)
            if hit:
                memo_value = hit
                break
            target = _resolve(modules, module, node)
            if target and isinstance(target[1], ast.FunctionDef):
                inner = reaches(target[0], target[1], seen | {key})
                if inner:
                    memo_value = f"{target[1].name} -> {inner}"
                    break
        memo[key] = memo_value
        return memo_value

    findings = []
    for module in modules.values():
        roots = [n for n in ast.walk(module.tree) if isinstance(n, ast.AsyncFunctionDef)]
        roots += [n for n in ast.walk(module.tree) if isinstance(n, ast.FunctionDef) and _is_callback(n)]
        for root in roots:
            for node in _runs_in_place(root):
                if not isinstance(node, ast.Call):
                    continue
                hit = _primitive(node)
                if not hit:
                    target = _resolve(modules, module, node)
                    if target and isinstance(target[1], ast.FunctionDef):
                        inner = reaches(target[0], target[1], frozenset())
                        hit = f"{target[1].name} -> {inner}" if inner else None
                if hit:
                    findings.append(f"{module.name}:{node.lineno} {root.name}: {hit}")
    return sorted(set(findings))


def test_no_filesystem_call_reaches_the_event_loop():
    findings = _blocking_paths(_load())
    assert not findings, "Blocking filesystem calls on the event loop:\n" + "\n".join(findings)


def test_the_guard_sees_direct_transitive_and_lambda_calls(tmp_path, monkeypatch):
    """Positive controls: each shape the guard must catch, and the executor hop it must clear."""
    package = tmp_path / "phoenix_mcp"
    package.mkdir()
    (package / "helpers.py").write_text(
        "import os\n"
        "def jail(path):\n    return os.path.realpath(path)\n",
        encoding="utf-8",
    )
    (package / "tools.py").write_text(
        "import os\n"
        "from .helpers import jail\n"
        "def build_diff(path):\n    return jail(path)\n"
        "async def direct(hass, path):\n    return os.path.isfile(path)\n"
        "async def transitive(hass, path):\n    return jail(path)\n"
        "async def lazy(hass, path):\n    return await gate(diff=lambda: build_diff(path))\n"
        "async def cleared(hass, path):\n    return await hass.async_add_executor_job(jail, path)\n"
        "async def cleared_lambda(hass, path):\n"
        "    return await hass.async_add_executor_job(lambda: jail(path))\n"
        "async def nested(hass, path):\n"
        "    def scan():\n        return os.listdir(path)\n"
        "    return await hass.async_add_executor_job(scan)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE", package)
    findings = "\n".join(_blocking_paths(_load()))
    assert "tools:6 direct" in findings
    assert "tools:8 transitive" in findings
    assert "tools:10 lazy" in findings
    for clear in ("cleared", "cleared_lambda", "nested"):
        assert f" {clear}:" not in findings, findings
