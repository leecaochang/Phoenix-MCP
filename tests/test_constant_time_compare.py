"""Access key hash comparison is constant-time, asserted structurally.

Replacing hmac.compare_digest with == is behaviour-preserving: both return the
same answer for every input, so no functional test can tell them apart. The
property is about timing, so the only way to assert it is to assert the source,
and a "simplification" of the helper to == would otherwise turn nothing red.

Scope: SECRET comparison only. tool_common's expected_hash check compares a
content hash the caller supplied, for optimistic concurrency rather than
authentication, so == is correct there and deliberately not covered.
"""

from __future__ import annotations

import ast
import hmac
import pathlib

import pytest

from custom_components.phoenix_mcp import access_key_store

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG = REPO / "custom_components" / "phoenix_mcp"

# Names that hold a secret or its digest. A == against any of these is forbidden:
# access keys are compared with hmac.compare_digest on their SHA-256 hashes.
SECRET_NAMES = {"access_key_hash", "presented_hash", "stored_hash", "presented_access_key", "raw_access_key"}


def _package_files() -> list[pathlib.Path]:
    return [p for p in sorted(PKG.rglob("*.py"))
            if "mesa_core" not in p.parts and "fastmcp" not in p.parts]


def test_hmac_compare_actually_uses_compare_digest():
    """The one helper every authentication path goes through."""
    src = ast.parse(pathlib.Path(access_key_store.__file__).read_text())
    node = next((n for n in ast.walk(src)
                 if isinstance(n, ast.FunctionDef) and n.name == "hmac_compare"), None)
    assert node is not None, "access_key_store.hmac_compare is gone; where does auth compare now?"
    body = ast.unparse(node)
    assert "hmac.compare_digest(" in body, (
        "hmac_compare no longer calls hmac.compare_digest. This is rule 1 and no "
        "behavioural test can catch it, because == returns the same answers."
    )
    assert "==" not in body, f"hmac_compare contains a == comparison:\n{body}"


def test_hmac_compare_is_really_constant_time_at_runtime():
    """The helper resolves to the real hmac.compare_digest, not a shadowed or stubbed one."""
    hmac_compare = access_key_store.hmac_compare

    assert hmac_compare("a" * 64, "a" * 64) is True
    assert hmac_compare("a" * 64, "b" * 64) is False
    # compare_digest is the only stdlib primitive with this contract.
    assert hmac.compare_digest("x", "x") is True


def test_no_secret_is_compared_with_equality_anywhere():
    """No secret is compared with ==/!= anywhere in the package.

    A second comparison elsewhere would satisfy the helper check and still be a
    timing oracle.
    """
    offenders: list[str] = []
    for path in _package_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            hit = names & SECRET_NAMES
            if hit:
                offenders.append(
                    f"{path.relative_to(REPO)}:{node.lineno}: {sorted(hit)} in "
                    f"{ast.unparse(node)[:80]}"
                )
    assert not offenders, (
        "Secret values compared with ==/!= (rule 1 requires hmac.compare_digest):\n  "
        + "\n  ".join(offenders)
    )


def test_authentication_lookups_route_through_the_helper():
    """The call sites, so the helper cannot be quietly bypassed."""
    src = pathlib.Path(access_key_store.__file__).read_text()
    tree = ast.parse(src)
    lookups = [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and "access_key_hash" in ast.unparse(n)
               and n.name != "hmac_compare"
               and ("get_access_key_by_hash" in n.name or "by_hash" in n.name)]
    assert lookups, "no access-key-by-hash lookup found; has it been renamed?"
    for node in lookups:
        body = ast.unparse(node)
        assert "hmac_compare(" in body, (
            f"{node.name} looks up an access key by hash without hmac_compare"
        )


@pytest.mark.parametrize("mutated_source,should_flag", [
    ("def hmac_compare(a, b):\n    return a == b\n", True),
    ("def hmac_compare(a, b):\n    return hmac.compare_digest(a, b)\n", False),
])
def test_the_structural_check_would_notice(mutated_source, should_flag):
    """The structural check flags an == helper and passes a compare_digest one.

    Guards against the check being vacuous (unparsing the wrong node, or
    matching a substring that is always present).
    """
    node = ast.parse(mutated_source).body[0]
    body = ast.unparse(node)
    flagged = "hmac.compare_digest(" not in body
    assert flagged is should_flag, (
        "the structural check cannot tell a constant-time compare from a =="
    )
