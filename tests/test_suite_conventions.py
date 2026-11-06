"""Guards on how the suite and the source are written, read off the AST.

Shared test code lives in conftest.py or a helper module, because a test module that
imports another one loads every fixture and side effect of it. A parameter named `hass`
is annotated and does not name Any, because mypy checks Home Assistant API use only
through that annotation; `object` and a parameter under another name are not detected.
"""

import ast
import pathlib
import re
import tomllib

import yaml

TESTS = pathlib.Path(__file__).resolve().parent
PACKAGE = TESTS.parent / "custom_components" / "phoenix_mcp"


def _test_module_imports(tree: ast.Module) -> list[str]:
    """Names of test modules this tree imports, at any depth."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "tests" and len(parts) > 1 and parts[1].startswith("test_"):
                found.append(node.module)
            if parts == ["tests"]:
                found.extend(alias.name for alias in node.names if alias.name.startswith("test_"))
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("tests.test_"))
    return found


def _untyped_hass(tree: ast.Module) -> list[int]:
    """Lines of `hass` parameters that are unannotated or whose annotation names Any."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.arg != "hass":
                continue
            annotation = arg.annotation
            if annotation is None or "Any" in re.findall(r"\w+", ast.unparse(annotation)):
                lines.append(arg.lineno)
    return lines


def test_the_checks_recognise_what_they_refuse():
    assert _test_module_imports(ast.parse("from tests.test_x import y")) == ["tests.test_x"]
    assert _test_module_imports(ast.parse("def f():\n    from tests import test_x")) == ["test_x"]
    assert _test_module_imports(ast.parse("from tests.runtime_fixtures import y")) == []
    assert _untyped_hass(ast.parse("def f(hass: Any): ...\ndef g(hass): ...\ndef h(hass: HomeAssistant): ...")) == [1, 2]
    assert _untyped_hass(ast.parse(
        "def f(hass: typing.Any): ...\ndef g(hass: 'Any'): ...\ndef h(hass: Any | None): ...\n"
        "def i(hass: HomeAssistant | None): ...\ndef j(hass: 'HomeAssistant'): ...")) == [1, 2, 3]


def test_no_test_module_imports_another():
    offenders = {
        path.name: imported for path in TESTS.rglob("*.py")
        if (imported := _test_module_imports(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert not offenders


def test_every_hass_parameter_is_typed():
    offenders = {
        str(path.relative_to(PACKAGE)): lines for path in PACKAGE.rglob("*.py")
        if "mesa_core" not in path.parts and (lines := _untyped_hass(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert not offenders


def test_every_ci_job_and_test_has_a_time_limit():
    """A hang fails on its own clock, at the test first and at the job otherwise."""
    root = TESTS.parent
    jobs = {
        f"{path.name}:{name}": job for path in (root / ".github" / "workflows").glob("*.yml")
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items()
    }
    assert len(jobs) >= 6, "the workflow listing broke"
    assert [name for name, job in jobs.items() if not job.get("timeout-minutes")] == []
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["pytest"]["ini_options"]["timeout"] > 0
