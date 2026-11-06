"""Guard: no test patches a name that the code under test does not read.

Moving a tool between modules breaks tests in three ways, in increasing order of
how quietly they fail. An `import` fails at collection. A `patch("...tool_dispatch.X")`
for a name that module no longer binds raises AttributeError. A patch on a name it
STILL binds, aimed at code that now reads its own module's binding, fails silently:
the patch applies to a name nobody reads, the real function runs, and every "nothing
happened" assertion still passes (for example `assert_not_called()` against a dead
mock).

The same silent failure exists for any first-party module: a patch of the module
a name was imported from misses every module that copied it at import time, even
when some other module still reads the name through its source. So find_dead_patches
looks across all of them and reports a copy the test itself runs.

The checker lives in this file rather than a helper script, so it cannot go
missing: a guard that silently disappears leaves a green suite claiming a property
that no longer holds.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG = REPO / "custom_components" / "phoenix_mcp"


def _bound(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            out.add(node.targets[0].id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _defined(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            out.add(node.targets[0].id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _reads(tree: ast.AST) -> set[str]:
    out = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    out |= {n.value.id for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    return out


# Each module copies what it imports, so a patch on any of these reaches that module's
# binding alone: never a binding of the same name in a tool the test also runs.
PATCHED_MODULES = ("tool_catalog", "tool_dispatch", "mcp_transport", "tools/approvals", "tools/backup",
                   "tools/history", "tools/integrations", "tools/logs", "tools/registry", "tools/restore",
                   "tools/service", "tools/system",
                   *(f"{package}/{p.stem}" for package in ("admin", "llm", "routing")
                     for p in sorted((PKG / package).glob("*.py")) if p.stem != "__init__"))
module_binds = {stem.replace("/", "."): _bound(ast.parse((PKG / f"{stem}.py").read_text()))
                for stem in PATCHED_MODULES}
registration_src = (PKG / "tool_registration.py").read_text()

# Maps tool name to owning module, read off the _register_tool lines plus the import
# block that names where each handler came from.
handler_module: dict[str, str] = {}
imports: dict[str, str] = {}
for node in ast.parse(registration_src).body:
    if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tools."):
        for a in node.names:
            imports[a.asname or a.name] = node.module.split(".", 1)[1]
for m in re.finditer(r'_register_tool\(\s*"([^"]+)"\s*,\s*(\w+)', registration_src):
    if m.group(2) in imports:
        handler_module[m.group(1)] = imports[m.group(2)]

tools: dict[str, tuple[set[str], set[str]]] = {}
for p in sorted((PKG / "tools").glob("*.py")):
    if p.stem == "__init__":
        continue
    t = ast.parse(p.read_text())
    tools[p.stem] = (_reads(t), _defined(t))


def patch_targets(tree: ast.AST):
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        is_obj = isinstance(f, ast.Attribute) and f.attr in ("object", "setattr")
        is_str = (isinstance(f, ast.Name) and f.id == "patch") or (
            isinstance(f, ast.Attribute) and f.attr in ("patch", "setattr"))
        if is_str and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str) and "." in n.args[0].value:
            path, _, attr = n.args[0].value.rpartition(".")
            yield path, attr, n.lineno
        elif is_obj and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) \
                and isinstance(n.args[1].value, str):
            yield ast.unparse(n.args[0]), n.args[1].value, n.lineno


def find_stale_sites(test_root: pathlib.Path | None = None) -> tuple[list[str], int]:
    """Return (findings, sites_examined). A finding is a human-readable line."""
    findings: list[str] = []
    checked = 0
    modules = _first_party_modules()
    for path in sorted((test_root or REPO / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text())
        aliases, _ = _imported_names(tree, modules)
        funcs = sorted(
            (n.lineno, n.end_lineno, n) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for mod, attr, ln in patch_targets(tree):
            module = _patched_module(mod, aliases)
            if module not in module_binds or attr not in module_binds[module]:
                continue
            stem = module.removeprefix("tools.")
            checked += 1
            owner = next((n for s, e, n in funcs if s <= ln <= e), None)
            if owner is None:
                continue
            names = _reads(owner)
            patched_here = {((_patched_module(m, aliases) or "").removeprefix("tools."), a)
                            for m, a, _ in patch_targets(owner)}
            strings = {c.value for c in ast.walk(owner)
                       if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            reachable = {handler_module[s] for s in strings if s in handler_module}
            if stem in tools and (names & tools[stem][1] or stem in reachable):
                continue  # The patch sits on the tool module the test runs.
            for mname, (reads, defines) in tools.items():
                if attr not in reads or mname == stem or (mname, attr) in patched_here:
                    continue
                if names & defines or mname in reachable:
                    why = "calls its symbols" if names & defines else "calls its tools by name"
                    findings.append(
                        f"{path}:{ln}: patch {stem}.{attr} in {owner.name}() "
                        f"-- tools/{mname} reads it and this test {why}"
                    )
    return findings, checked


BASE = "custom_components.phoenix_mcp"


def _module_name(path: pathlib.Path) -> str:
    return ".".join(path.relative_to(PKG).with_suffix("").parts).removesuffix(".__init__").replace("__init__", "")


def _first_party_modules() -> dict[str, ast.Module]:
    return {_module_name(p): ast.parse(p.read_text()) for p in sorted(PKG.rglob("*.py")) if "mesa_core" not in p.parts}


def _importers(modules: dict[str, ast.Module]) -> tuple[dict[tuple[str, str], set[tuple[str, str]]], set[tuple[str, str]]]:
    """Who imports each (source module, name) at module level, and which pairs are imported inside a function.

    A module-level import copies the binding once, so a later patch of the source
    never reaches that importer: the first result maps the pair to each
    (importing module, local name). A function-level import reads the source at
    call time, so the patch does reach it.
    """
    top: dict[tuple[str, str], set[tuple[str, str]]] = {}
    deferred: set[tuple[str, str]] = set()
    packages = {name.rpartition(".")[0] for name in modules}
    for name, tree in modules.items():
        parent = name if name in packages else name.rpartition(".")[0]
        package = parent.split(".") if parent else []
        top_nodes = {id(node) for node in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = package[:len(package) - (node.level - 1)]
                source = ".".join(base + ([node.module] if node.module else []))
                for alias in node.names:
                    if id(node) in top_nodes:
                        top.setdefault((source, alias.name), set()).add((name, alias.asname or alias.name))
                    else:
                        deferred.add((source, alias.name))
    return top, deferred


def _imported_names(tree: ast.AST, modules: dict[str, ast.Module]) -> tuple[dict[str, str], dict[str, str]]:
    """Local names a test file imported: those that are first-party modules, and symbols with their module."""
    aliases: dict[str, str] = {}
    symbols: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                full = alias.name.removeprefix(BASE).removeprefix(".")
                if alias.asname and alias.name.startswith(BASE) and full in modules:
                    aliases[alias.asname] = full
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(BASE):
            package = node.module.removeprefix(BASE).removeprefix(".")
            for alias in node.names:
                full = f"{package}.{alias.name}".strip(".")
                if full in modules:
                    aliases[alias.asname or alias.name] = full
                elif package in modules:
                    symbols[alias.asname or alias.name] = package
    return aliases, symbols


def _patched_module(target: str, aliases: dict[str, str]) -> str | None:
    """The first-party module a patch target names, or None when it is not one."""
    if target == BASE:
        return ""
    if target.startswith(f"{BASE}."):
        return target.removeprefix(f"{BASE}.")
    return aliases.get(target)


def _reached_functions(owner: ast.AST, functions: dict[str, ast.AST]) -> list[ast.AST]:
    """The test plus the helpers of its file that it names, transitively."""
    reached = [owner]
    seen = {id(owner)}
    for fn in reached:
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        for name in sorted(names & functions.keys()):
            if id(functions[name]) not in seen:
                seen.add(id(functions[name]))
                reached.append(functions[name])
    return reached


def _test_patch_sites(path: pathlib.Path, modules: dict[str, ast.Module]):
    """(module, attribute, line, modules the test runs, bindings it patches) per first-party patch in a file.

    A test runs a module when it, or a helper of its file, uses a symbol imported
    from that module or the module itself as more than a patch target. Fixtures
    do not count: they arrange state, and what they touch is not the code on trial.
    """
    tree = ast.parse(path.read_text())
    aliases, symbols = _imported_names(tree, modules)
    functions: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and not any("fixture" in ast.unparse(d) for d in node.decorator_list):
            functions.setdefault(node.name, node)
    spans = sorted((n.lineno, n.end_lineno, id(n), n) for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    context: dict[int, tuple[set[str], set[tuple[str, str]]]] = {}
    for target, attr, line in patch_targets(tree):
        module = _patched_module(target, aliases)
        if module not in modules:
            continue
        owner = next((n for start, end, _, n in spans if start <= line <= end), None)
        if owner is None:
            yield module, attr, line, set(), set()
            continue
        if id(owner) not in context:
            runs: set[str] = set()
            patched: set[tuple[str, str]] = set()
            for fn in _reached_functions(owner, functions):
                targets = {id(n.args[0]) for n in ast.walk(fn) if isinstance(n, ast.Call) and n.args
                           and isinstance(n.func, ast.Attribute) and n.func.attr in ("object", "setattr")}
                for n in ast.walk(fn):
                    if isinstance(n, ast.Name) and id(n) not in targets:
                        if n.id in aliases:
                            runs.add(aliases[n.id])
                        elif n.id in symbols:
                            runs.add(symbols[n.id])
                patched |= {(_patched_module(t, aliases) or "", a) for t, a, _ in patch_targets(fn)}
            context[id(owner)] = (runs, patched)
        yield (module, attr, line, *context[id(owner)])


def find_dead_patches(test_root: pathlib.Path, modules: dict[str, ast.Module] | None = None) -> tuple[list[str], int]:
    """Return (findings, sites_examined) for patches that miss a reader of the name.

    Two shapes qualify. The module binds the name but never reads it, and everything
    importing it from there imported it at module level, so the mock is unreachable
    from any module. Or a module the test runs copied the name at import time and
    reads its copy, while the test patched only the source: a function-level importer
    elsewhere keeps the source alive without bringing the patch to the code under test.
    """
    modules = modules or _first_party_modules()
    top, deferred = _importers(modules)
    reads = {name: {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
             for name, tree in modules.items()}
    findings: list[str] = []
    checked = 0
    for path in sorted(test_root.rglob("*.py")):
        for module, attr, line, runs, patched in _test_patch_sites(path, modules):
            checked += 1
            copies = [(module, attr)]
            for binding in copies:
                copies.extend(sorted(top.get(binding, set()) - set(copies)))
            missed = sorted(importer for importer, local in copies[1:]
                            if importer in runs and local in reads[importer] and (importer, local) not in patched)
            if missed:
                findings.append(f"{path}:{line}: patch {module}.{attr} misses the copy read by {', '.join(missed)}")
            elif (attr not in reads[module] and (module, attr) in top and (module, attr) not in deferred
                    and attr in _bound(modules[module])):
                findings.append(f"{path}:{line}: patch {module}.{attr} reaches nothing; patch the importing module")
    return findings, checked


def test_no_patch_reaches_nothing_in_any_module() -> None:
    findings, checked = find_dead_patches(REPO / "tests")
    assert checked > 100, f"only {checked} patch sites examined; the checker is not finding them"
    assert not findings, "Patches nothing can read through:\n" + "\n".join(findings)


def test_dead_patch_checker_reports_a_planted_patch(tmp_path: pathlib.Path) -> None:
    """The name is bound by the patched module, but its one reader copied it at import."""
    modules = {
        "source": ast.parse("from .origin import used\n"),
        "origin": ast.parse("def used():\n    pass\n"),
        "reader": ast.parse("from .source import used\nused()\n"),
    }
    (tmp_path / "test_fixture.py").write_text(
        "from unittest.mock import patch\n"
        "def test_x():\n"
        '    with patch("custom_components.phoenix_mcp.source.used"):\n'
        "        pass\n"
        '    with patch("custom_components.phoenix_mcp.reader.used"):\n'
        "        pass\n"
    )
    findings, checked = find_dead_patches(tmp_path, modules)
    assert checked == 2
    assert len(findings) == 1 and "source.used" in findings[0]


def _planted_copy_findings(tmp_path: pathlib.Path, test_body: str) -> list[str]:
    """Findings for a test of a module-level copy whose source a function-level importer still reads."""
    modules = {
        "origin": ast.parse("def wipe():\n    pass\n"),
        "view": ast.parse("from .origin import wipe\ndef handler():\n    wipe()\n"),
        "lifecycle": ast.parse("def teardown():\n    from .origin import wipe\n    wipe()\n"),
    }
    (tmp_path / "test_fixture.py").write_text(
        "from unittest.mock import patch\n"
        "import pytest\n"
        "from custom_components.phoenix_mcp.view import handler\n"
        "from custom_components.phoenix_mcp.lifecycle import teardown\n"
        "from custom_components.phoenix_mcp import view as view_module\n"
        "def call_handler():\n"
        "    handler()\n"
        "@pytest.fixture\n"
        "def arranged():\n"
        "    handler()\n"
        f"{test_body}"
    )
    findings, checked = find_dead_patches(tmp_path, modules)
    assert checked >= 1
    return findings


def test_dead_patch_checker_reports_a_copy_the_test_runs(tmp_path: pathlib.Path) -> None:
    """A function-level importer of the source does not excuse the copy under test."""
    findings = _planted_copy_findings(tmp_path, (
        "def test_x():\n"
        '    with patch("custom_components.phoenix_mcp.origin.wipe") as wipe:\n'
        "        handler()\n"
        "    wipe.assert_not_called()\n"
    ))
    assert len(findings) == 1 and "origin.wipe misses the copy read by view" in findings[0]


def test_dead_patch_checker_follows_helpers_and_monkeypatch_strings(tmp_path: pathlib.Path) -> None:
    findings = _planted_copy_findings(tmp_path, (
        "def test_x(monkeypatch):\n"
        '    monkeypatch.setattr("custom_components.phoenix_mcp.origin.wipe", print)\n'
        "    call_handler()\n"
    ))
    assert len(findings) == 1 and "copy read by view" in findings[0]


@pytest.mark.parametrize("test_body", [
    # The function-level importer reads through the source, so the patch reaches it.
    'def test_x():\n    with patch("custom_components.phoenix_mcp.origin.wipe"):\n        teardown()\n',
    # The copy is patched as well.
    'def test_x():\n    with patch("custom_components.phoenix_mcp.origin.wipe"), '
    'patch("custom_components.phoenix_mcp.view.wipe"):\n        handler()\n',
    # The test names the module only to patch it, and a fixture does not put it on trial.
    'def test_x(arranged):\n    _ = arranged\n    with patch("custom_components.phoenix_mcp.origin.wipe"):\n'
    '        teardown()\n',
    'def test_x(monkeypatch):\n    monkeypatch.setattr(view_module, "wipe", print)\n'
    '    with patch("custom_components.phoenix_mcp.origin.wipe"):\n        teardown()\n',
])
def test_dead_patch_checker_leaves_a_reachable_patch_alone(tmp_path: pathlib.Path, test_body: str) -> None:
    assert _planted_copy_findings(tmp_path, test_body) == []


def test_every_patched_area_is_examined() -> None:
    """Modules split into packages stay in scope, under names that cannot collide."""
    for package in ("admin", "llm", "routing"):
        assert any(name.startswith(f"{package}.") for name in module_binds), package
    assert "tools.approvals" in module_binds and "admin.approvals" in module_binds


def test_no_stale_patch_targets() -> None:
    findings, checked = find_stale_sites(REPO / "tests")
    assert checked > 50, (
        f"only {checked} patch sites examined; the checker is not finding the "
        "tests it is supposed to scan"
    )
    assert not findings, "Patches that may no longer bite:\n" + "\n".join(findings)


def test_checker_reports_a_known_stale_patch(tmp_path: pathlib.Path) -> None:
    """The checker catches a planted stale patch, not merely an empty list.

    A checker that scans nothing reports zero findings exactly like a clean tree.
    The fixture has the real shape: a native-tool test that patches _gate on
    tools/service, which both tools/service and tools/native bind, while calling a
    symbol tools/native defines.
    """
    fixture = tmp_path / "test_fixture.py"
    fixture.write_text(
        "from unittest.mock import patch\n"
        "from custom_components.phoenix_mcp.tools.native import _tool_hass_turn_on\n"
        "\n"
        "def test_x():\n"
        '    with patch("custom_components.phoenix_mcp.tools.service._gate") as gate:\n'
        "        _tool_hass_turn_on({}, None, None, None)\n"
        "    gate.assert_not_called()\n"
    )
    ast.parse(fixture.read_text())  # the fixture itself must be valid Python

    findings, checked = find_stale_sites(tmp_path)
    assert checked == 1, f"the fixture's patch site was not even examined ({checked})"
    assert len(findings) == 1, f"checker missed the planted defect: {findings}"
    assert "_gate" in findings[0] and "tools/native" in findings[0]
