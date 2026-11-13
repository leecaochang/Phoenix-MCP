"""Dockview's React wrapper ships with the layout engine of its own release.

``dockview-react`` declares a range for ``dockview`` and ``dockview-core``, so ``package.json``
pins both through ``overrides``. The pins must name the wrapper's exact version, and the
lockfile must install that version, or the wrapper runs on an older engine with no build error.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PINNED = ("dockview", "dockview-core")


def test_dockview_overrides_follow_the_wrapper_version() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    wrapper = package["dependencies"]["dockview-react"]
    assert {name: package["overrides"][name] for name in PINNED} == {name: wrapper for name in PINNED}
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    installed = {path: entry["version"] for path, entry in lock.items()
                 if path.rsplit("node_modules/", 1)[-1] in PINNED}
    assert installed and set(installed.values()) == {wrapper}, installed
