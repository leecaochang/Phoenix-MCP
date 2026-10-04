"""Check first-party Python syntax against the supported HA interpreter floor.

Home Assistant 2026.7.0 requires Python 3.14.2, so Phoenix source must parse
with the Python 3.14 grammar. Dependency and vendored source are outside this
scan. An older-grammar negative control proves that feature_version is enforced.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# Tracks the interpreter the oldest supported HA release requires. Raising it is
# a deliberate product decision that starts with raising the HA floor (it drops
# installs on older interpreters), never a fix for a failing test.
PYTHON_FLOOR = (3, 14)

_PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "phoenix_mcp"


def _phoenix_sources() -> list[pathlib.Path]:
    return sorted(p for p in _PACKAGE.rglob("*.py") if "mesa_core" not in p.parts)


def test_there_are_sources_to_check():
    """A path typo would make every parametrized case vanish and the file pass."""
    assert len(_phoenix_sources()) > 25


@pytest.mark.parametrize("path", _phoenix_sources(), ids=lambda p: p.name)
def test_source_parses_on_the_python_floor(path: pathlib.Path):
    floor = ".".join(str(part) for part in PYTHON_FLOOR)
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=PYTHON_FLOOR)
    except SyntaxError as err:
        pytest.fail(
            f"{path.name}:{err.lineno} uses syntax newer than Python {floor}: {err.msg}. "
            f"CI runs a much newer interpreter, so this would only fail on a user's "
            f"Home Assistant install."
        )


def test_parser_honors_an_older_requested_grammar(tmp_path):
    """An older requested grammar rejects newer syntax.

    A parser that ignored feature_version (or a typo in the version tuple) would
    turn the whole module into a no-op that still reports green.
    """
    newer = tmp_path / "newer.py"
    newer.write_text("type Alias[T = int] = list[T]\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        ast.parse(newer.read_text(), feature_version=(3, 12))

    generic = tmp_path / "generic.py"
    generic.write_text("def f[T = int](x: T) -> T: return x\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        ast.parse(generic.read_text(), feature_version=(3, 12))


def test_the_guard_accepts_syntax_the_floor_allows():
    """The guard accepts every syntax form the supported interpreter allows."""
    for src in (
        "def f(x: int | None) -> None: ...\n",              # PEP 604, 3.10
        "def f(x):\n    match x:\n        case 1: return 2\n",  # match, 3.10
        "async def f() -> None:\n    async with open('x') as fh: ...\n",
        "type Alias = int\n",                               # PEP 695, 3.12
        "def f[T](x: T) -> T: return x\n",                  # PEP 695, 3.12
    ):
        ast.parse(src, feature_version=PYTHON_FLOOR)
