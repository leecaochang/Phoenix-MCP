"""Every production package that ships in a panel bundle carries its license notice."""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTICES = ROOT / "frontend_src" / "public" / "THIRD_PARTY_NOTICES.txt"
SHIPPED = ROOT / "custom_components" / "phoenix_mcp" / "frontend" / "THIRD_PARTY_NOTICES.txt"
# js-yaml depends on this for its command line only; no bundle contains it.
NOT_BUNDLED = {"argparse"}


def _production_versions() -> dict[str, set[str]]:
    """Each production package of the lockfile, with every version of it that is installed."""
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for path, package in lock["packages"].items():
        if path and not package.get("dev") and not package.get("devOptional"):
            versions.setdefault(path.rsplit("node_modules/", 1)[-1], set()).add(package["version"])
    return versions


def _production_packages() -> set[str]:
    return set(_production_versions())


def _section_headings(text: str) -> list[str]:
    return re.findall(r"^\d+\. (.+)$", text, flags=re.MULTILINE)


def test_every_production_package_has_a_notice_section() -> None:
    packages = _production_packages()
    assert "react" in packages, "the lockfile listing broke"
    headings = " ".join(_section_headings(NOTICES.read_text(encoding="utf-8")))
    missing = sorted(p for p in packages - NOT_BUNDLED if not re.search(rf"(?<![\w-]){re.escape(p)}(?![\w-])", headings))
    assert missing == [], f"add the license notice of each package to {NOTICES.name}: {missing}"


def test_a_version_named_in_a_heading_is_the_one_that_ships() -> None:
    versions = _production_versions()
    named = [
        (match.group(1), match.group(2))
        for heading in _section_headings(NOTICES.read_text(encoding="utf-8"))
        for match in re.finditer(r"(?<![\w-])([a-z][\w.-]*) (\d+\.\d+\.\d+)(?![\w.])", heading)
        if match.group(1) in versions
    ]
    assert any(package == "lucide-react" for package, _version in named), "the heading scan broke"
    stale = [(package, version) for package, version in named if version not in versions[package]]
    assert stale == [], f"update these versions in {NOTICES.name} to the lockfile's: {stale}"


def test_each_notice_section_carries_a_copyright_line() -> None:
    sections = re.split(r"^\d+\. ", NOTICES.read_text(encoding="utf-8"), flags=re.MULTILINE)[2:]
    assert sections, "the section split broke"
    assert all(re.search(r"Copyright \(c\)", section, flags=re.IGNORECASE) for section in sections)


def test_the_shipped_copy_matches_its_source() -> None:
    assert SHIPPED.read_bytes() == NOTICES.read_bytes()
