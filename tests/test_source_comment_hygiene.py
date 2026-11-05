"""Source comments state behaviour and reasons, never who asked for them or when.

The repository is public. A comment that cites a person or a date reads as authority
long after the behaviour has moved on, so this holds the frontend, stylesheets and
Python to the same rule. Dates that belong to a protocol or a fixture stay allowed.
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import subprocess
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend_src"
PYTHON_ROOTS = ("custom_components/phoenix_mcp", "tests", "scripts")

ATTRIBUTION = re.compile(r"\(user,|\buser,?\s+20\d\d-\d\d", re.IGNORECASE)
DATE = re.compile(r"(?<![\w-])20\d\d-\d\d-\d\d(?![\dT])")
# The MCP protocol names its revisions by date.
PROTOCOL_REVISIONS = {"2025-03-26", "2026-07-28"}


def _script_comments(source: str) -> list[tuple[int, str]]:
    """Comments of a TypeScript file, line by line, skipping string contents."""
    comments: list[tuple[int, str]] = []
    line, i, state = 1, 0, "code"
    start = 0
    while i < len(source):
        char, pair = source[i], source[i:i + 2]
        if state == "code":
            if pair == "//":
                end = source.find("\n", i)
                end = len(source) if end < 0 else end
                comments.append((line, source[i:end]))
                i = end
                continue
            if pair == "/*":
                state, start = "block", i
                i += 2
                continue
            if char in "'\"`":
                state = char
        elif state == "block":
            if pair == "*/":
                for offset, text in enumerate(source[start:i].split("\n")):
                    comments.append((line - source[start:i].count("\n") + offset, text))
                state = "code"
                i += 2
                continue
        elif char == "\\":
            i += 2
            continue
        elif char == state or (char == "\n" and state != "`"):
            # A quote left open at the end of a line belongs to JSX text or a regex.
            state = "code"
        if char == "\n":
            line += 1
        i += 1
    return comments


def _style_comments(source: str) -> list[tuple[int, str]]:
    """Comments of a stylesheet, line by line."""
    comments: list[tuple[int, str]] = []
    for match in re.finditer(r"/\*.*?\*/", source, flags=re.DOTALL):
        first = source.count("\n", 0, match.start()) + 1
        comments.extend((first + offset, text) for offset, text in enumerate(match.group().split("\n")))
    return comments


def _python_comments(source: str) -> list[tuple[int, str]]:
    """Comments and docstrings of a Python file, line by line."""
    comments = [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                first = node.body[0].lineno
                comments.extend((first + offset, text) for offset, text in enumerate(docstring.split("\n")))
    return comments


def _problems(comments: list[tuple[int, str]]) -> list[tuple[int, str]]:
    found = []
    for line, text in comments:
        dates = set(DATE.findall(text)) - PROTOCOL_REVISIONS
        if ATTRIBUTION.search(text) or dates:
            found.append((line, text.strip()))
    return found


def _frontend_files() -> list[pathlib.Path]:
    return sorted(
        path for path in FRONTEND.rglob("*")
        if path.suffix in (".ts", ".tsx", ".css") and "topics" not in path.parts
    )


def _python_files() -> list[pathlib.Path]:
    """Python the repository publishes: ignored local tooling is out of scope."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *PYTHON_ROOTS],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available to enumerate published files")
    paths = (ROOT / name for name in listed if name.endswith(".py"))
    return sorted(
        path for path in paths
        if path.is_file() and "mesa_core" not in path.parts and path.name != pathlib.Path(__file__).name
    )


def test_frontend_comments_cite_no_person_or_date() -> None:
    files = _frontend_files()
    assert len(files) > 100, "the source listing broke"
    problems = {}
    for path in files:
        source = path.read_text(encoding="utf-8")
        found = _problems((_style_comments if path.suffix == ".css" else _script_comments)(source))
        if found:
            problems[str(path.relative_to(ROOT))] = found
    assert problems == {}


def test_python_comments_cite_no_person_or_date() -> None:
    files = _python_files()
    assert len(files) > 100, "the source listing broke"
    problems = {}
    for path in files:
        found = _problems(_python_comments(path.read_text(encoding="utf-8")))
        if found:
            problems[str(path.relative_to(ROOT))] = found
    assert problems == {}


def test_no_test_title_carries_an_attribution() -> None:
    titled = re.compile(r"\b(?:it|test|describe)\(\s*[\"'`][^\n]*\(user,")
    hits = [
        str(path.relative_to(ROOT)) for path in _frontend_files()
        if titled.search(path.read_text(encoding="utf-8"))
    ]
    assert hits == []


@pytest.mark.parametrize("source,count", [
    ("// Held open (user, 2026-10-31).\nconst a = 1;\n", 1),
    ("/** Rule one.\n * Decided (user,\n * 2026-10-31).\n */\n", 2),
    ("const a = 1; // dated 2026-10-31\n", 1),
    ("/* inherited (2026-11-03) */\n", 1),
    ('const when = "2026-01-01";\n', 0),
    ('const url = "https://example.test/2026-01-01";\n', 0),
    ("const stamp = `2026-01-01\n// 2026-01-01\n`;\n", 0),
    ("// revision 2026-07-28 of the protocol\n", 0),
    ("<p>don't 2026-01-01</p>\n// (user, 2026-01-01)\n", 1),
])
def test_the_script_scanner_finds_comments_and_skips_strings(source: str, count: int) -> None:
    assert len(_problems(_script_comments(source))) == count


def test_the_style_and_python_scanners_find_their_comments() -> None:
    assert _problems(_style_comments("a { color: red; } /* wide (user, 2026-10-31) */\n")) == [
        (1, "/* wide (user, 2026-10-31) */"),
    ]
    assert len(_problems(_python_comments('"""Doc (user, 2026-10-31)."""\nx = 1  # dated 2026-10-31\n'))) == 2
    assert _problems(_python_comments('x = "(user, 2026-10-31)"\n')) == []
