#!/usr/bin/env python3
"""Fail when a security-critical module falls below its own coverage floor.

coverage.py gates only the total, so a large well-tested module can average away a
drop in a small one. The floors live in pyproject.toml under
[tool.phoenix-coverage-floors], as module path relative to the package, and are
compared with a coverage.py JSON report of statements and branches together.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = "custom_components/phoenix_mcp/"


def floors(pyproject: pathlib.Path) -> dict[str, float]:
    """Read the configured module floors."""
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["phoenix-coverage-floors"]


def failures(report: dict, configured: dict[str, float]) -> list[str]:
    """Name each module under its floor; a floor with no measured module also fails."""
    out = []
    for module, floor in sorted(configured.items()):
        measured = report["files"].get(PACKAGE + module)
        if measured is None:
            out.append(f"{module}: not in the coverage report")
        elif measured["summary"]["percent_covered"] < floor:
            out.append(f"{module}: {measured['summary']['percent_covered']:.2f} is below its floor of {floor}")
    return out


def main(argv: list[str]) -> int:
    report = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    problems = failures(report, floors(ROOT / "pyproject.toml"))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
