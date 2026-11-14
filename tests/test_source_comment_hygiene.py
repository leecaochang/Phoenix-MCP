"""Source comments state behaviour and reasons, never who asked for them or when.

The repository is public. A comment that cites a person or a date reads as authority
long after the behaviour has moved on, so this holds the frontend, stylesheets and
Python to the same rule. Dates that belong to a protocol or a fixture stay allowed.

The patterns recognise the usual ways of writing these, not every phrasing. The person
and date scan covers comments and docstrings of those sources. A second scan reads every
tracked text file, strings included, for internal context: review finding ids, internal
document paths and home directories. Literals private to the maintainer's network (addresses,
host names) live in a Git-ignored local file, ``PRIVATE_LITERALS``; without it, as in CI,
that part is skipped.
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

ATTRIBUTION = re.compile(r"\(user,|\buser,?\s+20\d\d-\d\d|\buser\s+(?:asked|requested|wanted|decided|insisted)\b"
                         r"|\buser\s+wants\s+(?:this|it)\b|\bowner\s+(?:asked|requested|wanted|wants|decided|insisted|chose)\b",
                         re.IGNORECASE)
DATE = re.compile(r"(?<![\w-])20\d\d-\d\d-\d\d(?![\dT])")
WRITTEN_DATE = re.compile(
    r"(?<![\w/-])20\d\d/\d\d/\d\d(?![\d/])"
    r"|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?"
    r"|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"
)
# Review records and commits stay out of the source: neither says why the code is right.
RECORD_REFERENCE = re.compile(
    r"\badversarial audit\b|\b(?:AUTHZ|ADMIN|MESA|MCP|DOC|TOOL|TEST|HEALTH|LIFE|DEP|FE|A11Y|I18N)-\d+\b"
    r"|\bcommit\s+[0-9a-f]{7,40}\b",
    re.IGNORECASE,
)
# The MCP protocol names its revisions by date.
PROTOCOL_REVISIONS = {"2025-03-26", "2026-07-28"}

# Internal context no tracked file may carry, in comments or strings: review finding ids
# such as X-QZ-1, Q8-FOO-BAR-02 or Q7B-FOO-02, merged finding names such as M-FOO-BAR,
# internal notes, and a home directory.
INTERNAL_CONTEXT = re.compile(
    r"\b[A-Z](?:\d{1,2}[A-Z]?)?-(?=[A-Z0-9]*[A-Z][A-Z0-9]*[A-Z])[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)*-\d+\b"
    r"|\bM-[A-Z][A-Z0-9]{2,}(?:-[A-Z0-9]+)*\b"
    r"|docs-internal/|\bBACKLOG\.md\b|/(?:Users|home)/[a-z][a-z0-9_.-]*/"
)
# Fixture values the scan would otherwise flag, each one a deliberate example.
INTERNAL_CONTEXT_FIXTURES = {"/Users/private/"}
# Generated bundles repeat their sources, which the scan already reads.
GENERATED = ("custom_components/phoenix_mcp/frontend/",)
# One private literal per line; # starts a comment. Git-ignored, so absent from clones and CI.
PRIVATE_LITERALS = ROOT / "docs-internal" / "hygiene-private-literals.txt"


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
        if ATTRIBUTION.search(text) or dates or WRITTEN_DATE.search(text) or RECORD_REFERENCE.search(text):
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


def _tracked_text_files() -> dict[str, str]:
    """Every tracked text file outside the generated bundles, by repository path."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.split("\0")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available to enumerate published files")
    texts = {}
    for name in listed:
        path = ROOT / name
        if not name or name.startswith(GENERATED) or name == f"tests/{pathlib.Path(__file__).name}" or not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            texts[name] = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return texts


def _internal_context(text: str) -> list[str]:
    return [match.group() for match in INTERNAL_CONTEXT.finditer(text)
            if match.group() not in INTERNAL_CONTEXT_FIXTURES]


def _private_literals() -> list[str]:
    if not PRIVATE_LITERALS.is_file():
        return []
    lines = (line.split("#", 1)[0].strip() for line in PRIVATE_LITERALS.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line]


def test_no_tracked_file_carries_internal_context() -> None:
    files = _tracked_text_files()
    assert len(files) > 300, "the tracked listing broke"
    problems = {name: found for name, text in files.items() if (found := _internal_context(text))}
    assert problems == {}


def test_no_tracked_file_carries_a_private_literal() -> None:
    literals = _private_literals()
    if not literals:
        pytest.skip("no local list of private literals here")
    problems = {
        name: [literal for literal in literals if literal in text]
        for name, text in _tracked_text_files().items()
    }
    assert {name: found for name, found in problems.items() if found} == {}


@pytest.mark.parametrize("text", [
    "// fixes X-QZ-1 from round 3\n",
    "// see K4-WIDGET-03 and Z9-ZORP-01\n",
    "msg = 'see Q8-FOO-BAR-07'\n",
    "# see Q7B-WIDGET-PART-04 and K3A-ZORP-11\n",
    "msg = 'M-ZORPLE follow-up'\n",
    "// the M-WIDGET-FLOW repair\n",
    "// rules in docs-internal/agents/guide.md\n",
    "// copied from /Users/someone/Documents/project/x.ts\n",
    "// smoke-tested on the test server, see BACKLOG.md\n",
    '"""Mirror of mesa_core internals, see docs-internal/spec/mesa.md."""\n',
])
def test_the_tracked_scan_finds_internal_context(text: str) -> None:
    assert _internal_context(text)


@pytest.mark.parametrize("text", [
    "pattern = r'[A-Z0-9-]'; ok = 'A-Z0-9'\n",
    "encoding = 'UTF-8'; header = 'X-Frame-Options'\n",
    "path = '/Users/private/secret.txt'  # the scrub fixture\n",
    "token = 'phx_' + 'a-b-1'\n",
    "license = 'CC-BY-4.0'; size = 'M-2'\n",
])
def test_the_tracked_scan_leaves_ordinary_text(text: str) -> None:
    assert _internal_context(text) == []


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
    ("// because the user asked for it\n", 1),
    ("// the owner decided to keep it\n", 1),
    ("// the user wants this kept\n", 1),
    ("// when the user wants a colder room, lower it\n", 0),
    ("// the height the user chose stays\n", 0),
    ("// dated 2026/11/05\n", 1),
    ("// see /logs/2026/11/05/entry\n", 0),
    ("// settled on November 5, 2026\n", 1),
    ("// changed on Nov 5\n", 1),
    ("// may 5 entries wait in November\n", 0),
    ("// fixes AUTHZ-1 from the adversarial audit\n", 1),
    ("// see commit b04defeb\n", 1),
    ("// commit the transaction first\n", 0),
])
def test_the_script_scanner_finds_comments_and_skips_strings(source: str, count: int) -> None:
    assert len(_problems(_script_comments(source))) == count


def test_the_style_and_python_scanners_find_their_comments() -> None:
    assert _problems(_style_comments("a { color: red; } /* wide (user, 2026-10-31) */\n")) == [
        (1, "/* wide (user, 2026-10-31) */"),
    ]
    assert len(_problems(_python_comments('"""Doc (user, 2026-10-31)."""\nx = 1  # dated 2026-10-31\n'))) == 2
    assert _problems(_python_comments('x = "(user, 2026-10-31)"\n')) == []
