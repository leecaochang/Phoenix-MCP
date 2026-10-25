"""Phoenix writes configuration files only where Home Assistant's own editor does.

YAML configuration belongs to the operator. The only files Phoenix itself writes
are the editor files for automations, scripts and scenes (automations.yaml,
scripts.yaml, scenes.yaml), through the functions listed in WRITERS; everything
else Phoenix persists goes through Home Assistant's own Store and APIs. This
static guard finds every call to a file-writing primitive in the package,
including one passed by reference to an executor job or imported under another
name, and fails on any site outside that list, so a new writer cannot appear
without this file being edited on purpose. The vendored mesa-core package is
excluded.
"""

from __future__ import annotations

import ast
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "phoenix_mcp"
EXCLUDED_DIRS = {"mesa_core", "__pycache__"}

# (module, function) -> why it may write. Each writes one editor file.
WRITERS = {
    ("yaml_includes", "_write_entry_files"): "splices an edit into the domain's editor file",
    ("yaml_includes", "_automation_write_checked"): "rewrites automations.yaml when configuration.yaml cannot be read",
    ("yaml_includes", "perform_delete"): "splices an entry out of the domain's editor file",
    ("yaml_includes", "_create_append"): "appends a new entry to the domain's editor file",
    ("tools.authoring", "_write_automations_yaml"): "automations.yaml when configuration.yaml cannot be read",
    ("tools.authoring", "_write_scripts_yaml"): "scripts.yaml when configuration.yaml cannot be read",
    ("tools.authoring", "_write_scenes_yaml"): "scenes.yaml when configuration.yaml cannot be read",
}

FILE_WRITERS = {"write_utf8_file_atomic", "write_utf8_file"}
OS_WRITERS = {"remove", "unlink", "rename", "replace", "makedirs", "mkdir", "rmdir", "removedirs", "truncate"}
PATH_WRITERS = {"write_text", "write_bytes", "touch", "unlink", "rmdir", "mkdir"}


def _dotted(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return None


def _aliases(tree: ast.Module) -> tuple[set[str], set[str], set[str]]:
    """Local names bound to HA's file writers, to the os module, and to shutil."""
    writers, os_names, shutil_names = set(), {"os"}, {"shutil"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("util.file"):
            writers |= {a.asname or a.name for a in node.names if a.name in FILE_WRITERS}
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            writers |= {a.asname or a.name for a in node.names if a.name in OS_WRITERS}
        elif isinstance(node, ast.ImportFrom) and node.module == "shutil":
            writers |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
                if alias.name == "shutil":
                    shutil_names.add(alias.asname or "shutil")
    return writers, os_names, shutil_names


def _is_write(node: ast.AST, writers: set[str], os_names: set[str], shutil_names: set[str]) -> bool:
    """A reference to a file-writing primitive, called or passed along."""
    name = _dotted(node)
    if not name:
        return False
    if len(name) == 1:
        return name[0] in writers
    if name[0] in os_names and name[-1] in OS_WRITERS:
        return True
    if name[0] in shutil_names:
        return True
    return isinstance(node, ast.Attribute) and node.attr in PATH_WRITERS and name[0] not in ("self", "hass", "data")


def _write_mode_open(call: ast.Call) -> bool:
    if _dotted(call.func) not in (("open",), ("io", "open")):
        return False
    mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in "wax+")


def _write_sites() -> dict[tuple[str, str], list[int]]:
    sites: dict[tuple[str, str], list[int]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if EXCLUDED_DIRS & set(path.relative_to(PACKAGE).parts):
            continue
        module = ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        writers, os_names, shutil_names = _aliases(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nested = {id(n) for inner in ast.walk(fn) if inner is not fn
                      and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(inner)}
            for node in ast.walk(fn):
                if id(node) in nested or not isinstance(node, ast.Call):
                    continue
                hit = _write_mode_open(node) or _is_write(node.func, writers, os_names, shutil_names) or any(
                    _is_write(arg, writers, os_names, shutil_names) for arg in node.args)
                if hit:
                    sites.setdefault((module, fn.name), []).append(node.lineno)
    return sites


def test_only_the_editor_file_writers_write_files():
    unexpected = {site: lines for site, lines in _write_sites().items() if site not in WRITERS}
    assert not unexpected, "File writes outside Home Assistant's editor files:\n" + "\n".join(
        f"{module}.{fn} lines {lines}" for (module, fn), lines in sorted(unexpected.items()))


def test_every_listed_writer_still_writes():
    """A stale entry would let a renamed writer slip past the guard."""
    assert set(WRITERS) <= set(_write_sites())


def test_the_guard_sees_calls_references_aliases_and_write_modes(tmp_path, monkeypatch):
    """Positive controls for each write shape, and the reads it must leave alone."""
    package = tmp_path / "phoenix_mcp"
    package.mkdir()
    (package / "sample.py").write_text(
        "import os\n"
        "from homeassistant.util.file import write_utf8_file_atomic as _atomic\n"
        "def called(path):\n    _atomic(path, 'x')\n"
        "async def referenced(hass, path):\n    await hass.async_add_executor_job(os.remove, path)\n"
        "def opened(path):\n    with open(path, 'w') as f:\n        f.write('x')\n"
        "def path_method(target):\n    target.write_text('x')\n"
        "def reads(path):\n    with open(path) as f:\n        return f.read(), 'a'.replace('a', 'b')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE", package)
    sites = set(_write_sites())
    assert {("sample", "called"), ("sample", "referenced"), ("sample", "opened"), ("sample", "path_method")} <= sites
    assert ("sample", "reads") not in sites
