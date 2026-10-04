#!/usr/bin/env python3
"""Vendor a copy of mesa-core into custom_components/phoenix_mcp/mesa_core/.

mesa-core is a separate, read-only project; Phoenix MCP ships a vendored copy so
a HACS install is self-contained. Its absolute ``mesa_core`` imports are rewritten
to ``custom_components.phoenix_mcp.mesa_core`` so the copy is hermetic and never
collides with an installed mesa-core or another integration's copy.

The copy comes from the tracked files at the clean source checkout's HEAD;
mesa-core itself is never modified. Commit the regenerated tree with its manifest.

Usage:
    python scripts/sync_mesa_core.py [SOURCE_DIR]   re-vendor from source
    python scripts/sync_mesa_core.py --check        verify, writing nothing

SOURCE_DIR defaults to the ``mesa_core`` package inside the ``mesa-core`` symlink
at the repository root. ``--check`` needs no source tree: it hashes the vendored
files against the tracked ``tests/contract/mesa_core_vendor.json``, so a clean
checkout can tell a re-sync from a hand-edit, which nothing else would notice.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "mesa-core" / "mesa_core"
DEST = REPO_ROOT / "custom_components" / "phoenix_mcp" / "mesa_core"
# Tracked, unlike _VENDORED.txt (which is gitignored because it records a local
# absolute path and a sync date). This is the copy a clean checkout can check.
MANIFEST = REPO_ROOT / "tests" / "contract" / "mesa_core_vendor.json"

VENDOR_PREFIX = "custom_components.phoenix_mcp.mesa_core"

# Never copied into the vendored tree and never hashed. These belong to the OS,
# editor and tooling, not the library: they exist only in a developer's working
# copy and are gitignored, so copying them ships junk and hashing them makes the
# manifest describe a tree no clean checkout can reproduce.
_EXCLUDED_DIRS = frozenset({"__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep"})
# The provenance marker itself: it records a sync DATE and a local absolute path,
# so hashing it would change the digest on every re-run with no upstream change.
_MANIFEST_EXCLUDED_NAMES = _EXCLUDED_NAMES | {"_VENDORED.txt"}


def _is_excluded(path: Path, root: Path) -> bool:
    """True for OS/editor metadata and caches, which never belong in the copy."""
    rel = path.relative_to(root)
    if _EXCLUDED_DIRS.intersection(rel.parts):
        return True
    return path.name in _EXCLUDED_NAMES or path.name.endswith((".pyc", ".pyo", ".swp"))

# All internal imports in mesa-core take the form ``from mesa_core[.x] import``.
# A bare ``import mesa_core`` would bind the wrong name after rewriting, so it is
# refused rather than silently rewritten into broken code.
_FROM_RE = re.compile(r"\bfrom mesa_core\b")
_BARE_IMPORT_RE = re.compile(r"\bimport mesa_core\b(?!\s*\.)")


def _rewrite_source(text: str, rel: Path) -> str:
    if _BARE_IMPORT_RE.search(text):
        raise SystemExit(
            f"ERROR: {rel} contains a bare 'import mesa_core' statement, which "
            "the vendoring rewrite cannot handle. Report this to the user; do "
            "not edit mesa-core."
        )
    return _FROM_RE.sub(f"from {VENDOR_PREFIX}", text)


def _read_source_version(source_root: Path) -> str:
    init = (source_root / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    return match.group(1) if match else "unknown"


def _read_source_provenance(source_root: Path, version: str) -> dict[str, str]:
    """Return immutable Git provenance, refusing a modified source package."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(source_root), *args],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as err:
            raise SystemExit(
                "ERROR: mesa-core source must be a clean Git checkout so its "
                "immutable revision can be recorded."
            ) from err

    dirty = git("status", "--porcelain", "--", ".")
    if dirty:
        raise SystemExit(
            "ERROR: mesa-core source package has local changes. Refusing to vendor "
            "content that is not identified by the recorded Git revision."
        )
    revision = git("rev-parse", "HEAD")
    tag_result = subprocess.run(
        ["git", "-C", str(source_root), "describe", "--tags", "--exact-match", revision],
        capture_output=True,
        text=True,
    )
    tag = tag_result.stdout.strip() if tag_result.returncode == 0 else None
    if tag is not None and tag != f"v{version}":
        raise SystemExit(
            f"ERROR: mesa-core source version is {version}, but HEAD is tagged {tag!r}."
        )
    provenance = {
        "source_repository": git("remote", "get-url", "origin"),
        "source_revision": revision,
    }
    if tag is not None:
        provenance["source_tag"] = tag
    return provenance



def _read_revision_files(source_root: Path, revision: str) -> dict[Path, bytes]:
    """Read only immutable tracked blobs, never working-tree or ignored bytes."""
    prefix = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--show-prefix"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    repository = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--show-toplevel"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    listing = subprocess.run(
        ["git", "--literal-pathspecs", "-C", repository, "ls-tree", "-rz", "--full-tree", revision, "--", prefix or "."],
        check=True, capture_output=True,
    ).stdout
    files = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        full_name = name.decode("utf-8")
        if not full_name.startswith(prefix):
            raise SystemExit("ERROR: source revision contains an unexpected package path.")
        rel = Path(full_name[len(prefix):])
        if _is_excluded(source_root / rel, source_root):
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SystemExit(f"ERROR: unsupported source entry mode for {rel}: {mode}")
        files[rel] = subprocess.run(
            ["git", "-C", str(source_root), "cat-file", "blob", oid],
            check=True, capture_output=True,
        ).stdout
    if Path("__init__.py") not in files:
        raise SystemExit("ERROR: recorded revision does not contain the source package.")
    return files


def main() -> int:
    source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_SOURCE.resolve()
    if not source.is_dir():
        raise SystemExit(f"ERROR: source mesa_core package not found at {source}")

    version = _read_source_version(source)
    provenance = _read_source_provenance(source, version)

    source_files = _read_revision_files(source, provenance["source_revision"])
    init = source_files[Path("__init__.py")].decode("utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    version = match.group(1) if match else "unknown"
    if provenance.get("source_tag", f"v{version}") != f"v{version}":
        raise SystemExit("ERROR: recorded release tag does not match the committed version.")
    prepared = {
        rel: _rewrite_source(blob.decode("utf-8"), rel).encode("utf-8")
        if rel.suffix == ".py" else blob
        for rel, blob in source_files.items()
    }
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    copied = 0
    for rel, blob in sorted(prepared.items()):
        dest_path = DEST / rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(blob)
        copied += 1

    stamp = datetime.date.today().isoformat()
    (DEST / "_VENDORED.txt").write_text(
        "This directory is a vendored copy of mesa-core. Do not edit by hand.\n"
        "Regenerate with scripts/sync_mesa_core.py.\n\n"
        f"source_version: {version}\n"
        f"synced_on: {stamp}\n"
        f"source_path: {source}\n",
        encoding="utf-8",
    )
    _write_manifest(version, provenance)

    print(f"Vendored mesa-core {version} -> {DEST} ({copied} files, import prefix rewritten).")
    print(f"Wrote {MANIFEST.relative_to(REPO_ROOT)}; commit it with the vendored tree.")
    return 0


def _tree_digest() -> tuple[str, dict[str, str]]:
    """Hash every SHIPPED vendored file, and the whole tree.

    It must hash exactly the files a clean checkout has. Gitignored OS and editor
    metadata is skipped, or the manifest would describe a tree no clean checkout
    can reproduce; so is _VENDORED.txt, whose sync date and local path would
    change the digest on every re-run with no upstream change.
    """
    per_file: dict[str, str] = {}
    for path in sorted(DEST.rglob("*")):
        if not path.is_file() or _is_excluded(path, DEST):
            continue
        rel = path.relative_to(DEST).as_posix()
        if path.name in _MANIFEST_EXCLUDED_NAMES:
            continue
        per_file[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    combined = hashlib.sha256(
        "\n".join(f"{rel} {digest}" for rel, digest in per_file.items()).encode()
    ).hexdigest()
    return combined, per_file


def _write_manifest(version: str, provenance: dict[str, str]) -> None:
    combined, per_file = _tree_digest()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "_comment": (
                    "Content hashes of the vendored mesa-core tree. Generated by "
                    "scripts/sync_mesa_core.py; tests/test_mesa_vendor.py fails if the "
                    "shipped files stop matching. This is TRACKED (unlike _VENDORED.txt, "
                    "which carries a local absolute path and a sync date) so a clean "
                    "checkout can tell a hand-edit from a re-sync."
                ),
                "source_version": version,
                **provenance,
                "vendor_prefix": VENDOR_PREFIX,
                "tree_sha256": combined,
                "files": per_file,
            },
            indent=2,
            sort_keys=False,
        ) + "\n",
        encoding="utf-8",
    )


def check() -> int:
    """Report whether the vendored tree still matches its manifest. Writes nothing.

    It catches a HAND-EDIT of the vendored copy, which the project forbids and
    which nothing else would notice: the copy is ordinary Python that imports and
    passes tests whatever it says. It does not authenticate an unknown upstream.
    """
    if not MANIFEST.exists():
        print(f"ERROR: {MANIFEST.relative_to(REPO_ROOT)} is missing; run the sync to create it.")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    combined, per_file = _tree_digest()
    if combined == manifest.get("tree_sha256"):
        print(f"Vendored mesa-core matches the manifest ({len(per_file)} files).")
        return 0

    recorded = manifest.get("files", {})
    changed = sorted(k for k in per_file.keys() & recorded.keys() if per_file[k] != recorded[k])
    added = sorted(per_file.keys() - recorded.keys())
    removed = sorted(recorded.keys() - per_file.keys())
    print("ERROR: the vendored mesa-core tree does not match its manifest.")
    for label, names in (("changed", changed), ("added", added), ("removed", removed)):
        for name in names:
            print(f"  {label}: {name}")
    print("\nIf this was a re-sync, re-run scripts/sync_mesa_core.py to regenerate the")
    print("manifest. If it was not, the vendored copy has been hand-edited; restore it")
    print("from the sync rather than keeping the edit.")
    return 1


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        raise SystemExit(check())
    raise SystemExit(main())
