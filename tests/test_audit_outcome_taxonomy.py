"""An I/O failure is never audited as a policy decision.

`denied` is the one outcome an operator reads to see what Phoenix MCP's own
permission rules stopped. A disk error filed under it corrupts that signal: the
Audit tab shows a refusal that no rule made, and the real cause (a full disk, a
permission problem on the config dir) stays invisible. The outcome vocabulary is
a closed set, so an internal failure uses `invalid_request` rather than gaining
a new value.

This is pinned by an AST walk over the whole package rather than a test per
site: an error branch that is hard to trigger is exactly the branch a
behavioural test does not cover.

Scope is deliberately narrow. Only `except OSError` (and its `IOError` alias) is
checked, because that handler unambiguously means the filesystem failed, not
that a rule fired. Broader exception types can legitimately carry a denial (a
`HomeAssistantError` from a service call may be a real refusal), and deciding
that needs context this walk does not have.
"""

from __future__ import annotations

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "phoenix_mcp"

# Vendored, regenerated from a read-only source and never edited here.
_SKIP_DIRS = {"mesa_core"}

_IO_ERRORS = {"OSError", "IOError"}


def _catches_io_error(handler: ast.ExceptHandler) -> bool:
    """Does this handler catch OSError, alone or in a tuple of types?"""
    node = handler.type
    if node is None:  # bare except: too broad to judge here
        return False
    names = node.elts if isinstance(node, ast.Tuple) else [node]
    return any(isinstance(n, ast.Name) and n.id in _IO_ERRORS for n in names)


def _denied_returns(handler: ast.ExceptHandler) -> list[int]:
    """Line numbers of `return ..., "denied", ...` inside this handler.

    Every tool handler returns (content, outcome, resource), so the outcome is
    the second element of a returned tuple.
    """
    lines = []
    for node in ast.walk(handler):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
            continue
        elements = node.value.elts
        if len(elements) < 2:
            continue
        outcome = elements[1]
        if isinstance(outcome, ast.Constant) and outcome.value == "denied":
            lines.append(node.lineno)
    return lines


def _source_files() -> list[pathlib.Path]:
    return [
        path for path in sorted(PACKAGE.rglob("*.py"))
        if not any(part in _SKIP_DIRS for part in path.relative_to(PACKAGE).parts)
    ]


def test_no_io_error_is_audited_as_denied():
    offenders: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _catches_io_error(node):
                continue
            rel = path.relative_to(PACKAGE)
            offenders.extend(f"{rel}:{line}" for line in _denied_returns(node))

    assert not offenders, (
        "An OSError is an internal failure, not a policy decision, so it must be "
        'audited as "invalid_request" (and logged with _LOGGER.exception) rather '
        'than "denied", which is reserved for what the permission rules stopped. '
        "Offending returns: " + ", ".join(offenders)
    )


def test_the_walk_actually_finds_denied_returns():
    """The guard above asserts an ABSENCE, so prove it is not vacuous.

    A walk that silently matched nothing (a renamed AST field, an unrecognised
    tuple shape) would pass forever while the defect it exists to catch walked back
    in.
    """
    tree = ast.parse(
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except OSError:\n"
        '        return {}, "denied", "x"\n'
    )
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    assert len(handlers) == 1
    assert _catches_io_error(handlers[0])
    assert _denied_returns(handlers[0]) == [5]


def test_the_walk_covers_the_modules_that_regressed():
    """The config-file and ESPHome tool modules are scanned; mesa_core is not.

    A path-filter change must not quietly drop the modules whose OSError branches
    handle disk failures.
    """
    scanned = {str(p.relative_to(PACKAGE)) for p in _source_files()}
    assert {"tools/config_files.py", "tools/esphome.py"} <= scanned
    assert not any(s.startswith("mesa_core/") for s in scanned)
