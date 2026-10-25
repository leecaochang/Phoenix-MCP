"""Credential masking for ESPHome device YAML.

Device files routinely carry credentials inline rather than behind `!secret`.
Phoenix MCP reads them without exposing the values to an agent or a stored record.

Masking is span-surgical: `yaml.compose` gives each scalar's exact source span,
so a placeholder is spliced into the RAW text and comments, ordering and
formatting survive byte-for-byte (a parse-and-re-dump would destroy them). Three
layers feed one span set: curated paths (api.encryption.key, ...); any scalar
whose KEY looks like a credential, at any depth; any scalar whose whole value
matches a secrets.yaml value.

Over-redaction is the accepted failure direction. `secrets.yaml` is never
readable through any tool; it is parsed here only to know what NOT to emit.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import yaml

from .yaml_includes import PhoenixLenientLoader, YamlParseError

# Known credential locations. "[]" matches any list index, so both the modern
# list form (ota: - platform: esphome / password:) and the legacy mapping form
# are covered; a curated list built only from the legacy shape would silently
# miss every current config.
ESPHOME_SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("api", "encryption", "key"),
    ("api", "password"),
    ("wifi", "password"),
    ("wifi", "ap", "password"),
    ("wifi", "networks", "[]", "password"),
    ("ota", "password"),
    ("ota", "[]", "password"),
    ("ota", "encryption", "key"),
    ("ota", "[]", "encryption", "key"),
    ("web_server", "auth", "password"),
    ("mqtt", "password"),
)

# Layer 2: key names that mark their value as a credential wherever they appear.
_SECRET_KEY_RE = re.compile(r"(password|passwd|psk|bindkey|api_key|token|secret)", re.IGNORECASE)

# Layer 3 floor. It catches a credential inlined in one file but referenced by
# `!secret` in another. A short secrets.yaml value (a pin number, a small integer)
# would match unrelated scalars all over a device file and mangle it, so only
# values of at least this length are cross-checked, and only as whole scalars.
MIN_CROSS_CHECK_LENGTH = 8

_PLACEHOLDER_PREFIX = "__PHOENIX_REDACTED__"
_PLACEHOLDER_SUFFIX = "__"

_ALLOWED_SUFFIXES = (".yaml", ".yml")
_ARCHIVE_DIR = "archive"

def placeholder_for(path: str) -> str:
    """The placeholder that stands in for the value at a dotted path.

    Path-based, never value-derived: a hash of the real value would be an
    offline-guessing oracle for a weak password, and would change whenever the
    credential rotated.
    """
    return f"{_PLACEHOLDER_PREFIX}{path}{_PLACEHOLDER_SUFFIX}"


def esphome_rel_path_ok(rel: str) -> bool:
    """Whether a config-relative path may be read as device YAML.

    Same hidden-path, extension and secrets.yaml rules as the generic YAML reader
    (_yaml_read_path_ok), except that `archive/` (the Device Builder's trash) is
    refused and the `esphome/` directory is admitted. secrets.yaml is never
    reachable.
    """
    if not isinstance(rel, str) or not rel.strip():
        return False
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(p.startswith(".") for p in parts):
        return False
    if parts[0].lower() == _ARCHIVE_DIR:
        return False
    name = parts[-1]
    if name.lower() == "secrets.yaml":
        return False
    return name.lower().endswith(_ALLOWED_SUFFIXES)


def _path_matches_curated(path: tuple[str, ...]) -> bool:
    """Whether a node path matches a curated secret path, honoring the [] wildcard."""
    for pattern in ESPHOME_SECRET_PATHS:
        if len(pattern) != len(path):
            continue
        if all(p == "[]" or p == seg for p, seg in zip(pattern, path)):
            return True
    return False


def _format_path(path: tuple[str, ...]) -> str:
    """Render a node path as the dotted form used inside a placeholder."""
    out = ""
    for seg in path:
        if seg.startswith("[") and seg.endswith("]"):
            out += seg
        else:
            out = f"{out}.{seg}" if out else seg
    return out


def _walk(node: Any, path: tuple[str, ...]) -> Iterator[tuple[str, tuple[str, ...], str, Any]]:
    """Yield (dotted_path, path_tuple, key_name, scalar_node) for every scalar."""
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = getattr(key_node, "value", None)
            if not isinstance(key, str):
                continue
            yield from _walk(value_node, path + (key,))
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            yield from _walk(item, path + (f"[{index}]",))
    elif isinstance(node, yaml.ScalarNode):
        key = path[-1] if path else ""
        yield _format_path(path), path, key, node


def _compose(text: str) -> Any:
    """Parse to a node tree, keeping unknown tags intact.

    Composition stops at the parser level, so no constructor runs: !lambda,
    !secret, and any third-party tag survive without needing to be understood.
    """
    try:
        return yaml.compose(text, Loader=PhoenixLenientLoader)
    except (yaml.YAMLError, RecursionError) as err:
        raise YamlParseError(str(err)) from err


def _normalized_path(path: tuple[str, ...]) -> tuple[str, ...]:
    """Path with list indices collapsed to [] so curated patterns can match."""
    return tuple("[]" if seg.startswith("[") and seg.endswith("]") else seg for seg in path)


def _is_tagged(node: yaml.ScalarNode) -> bool:
    """Whether a scalar carries a custom tag (!secret, !lambda, ...).

    A tagged scalar is a reference or an expression, not a literal value, so
    there is nothing to hide.
    """
    return isinstance(node.tag, str) and node.tag.startswith("!")


def secret_values_from_text(text: str) -> set[str]:
    """Values defined in a secrets.yaml, for the layer-3 cross-check.

    Returns an empty set for unparseable or empty input: a missing secrets.yaml
    degrades redaction to layers 1 and 2 rather than failing every read.
    """
    try:
        data = yaml.load(text, Loader=PhoenixLenientLoader)
    except yaml.YAMLError:
        return set()
    if not isinstance(data, dict):
        return set()
    return {
        value for value in data.values()
        if isinstance(value, str) and len(value) >= MIN_CROSS_CHECK_LENGTH
    }


def secret_keys_from_text(text: str) -> set[str]:
    """Key NAMES defined in a secrets.yaml, reported as defined_secrets."""
    try:
        data = yaml.load(text, Loader=PhoenixLenientLoader)
    except yaml.YAMLError:
        return set()
    if not isinstance(data, dict):
        return set()
    return {k for k in data if isinstance(k, str)}


def _secret_spans(text: str, secret_values: set[str]) -> list[tuple[int, int, str]]:
    """Every span in `text` holding a credential, as (start, end, dotted_path).

    Unions all three detection layers. Ordered last-first so callers can splice
    without recomputing offsets.
    """
    root = _compose(text)
    if root is None:
        return []
    spans: list[tuple[int, int, str]] = []
    for dotted, path, key, node in _walk(root, ()):
        if _is_tagged(node):
            continue
        value = node.value
        if not isinstance(value, str) or not value:
            continue
        hit = (
            _path_matches_curated(_normalized_path(path))
            or bool(_SECRET_KEY_RE.search(key))
            or (len(value) >= MIN_CROSS_CHECK_LENGTH and value in secret_values)
        )
        if hit:
            spans.append((node.start_mark.index, node.end_mark.index, dotted))
    spans.sort(key=lambda s: s[0], reverse=True)
    return spans


def redact_esphome_text(
    text: str, secret_values: set[str] | None = None
) -> tuple[str, list[str]]:
    """Return (redacted_text, sorted redacted paths).

    Raises YamlParseError when the document does not parse. Reads FAIL CLOSED:
    a file whose credentials cannot be located must not be returned at all,
    because inline credentials are the normal case rather than the exception.
    """
    spans = _secret_spans(text, secret_values or set())
    out = text
    for start, end, dotted in spans:
        out = out[:start] + placeholder_for(dotted) + out[end:]
    return out, sorted(dotted for _, _, dotted in spans)


def inline_secret_values(text: str, secret_values: set[str] | None = None) -> set[str]:
    """The credential VALUES a device file carries inline, for scrubbing free text.

    Same three-layer detection as the masking path, but returning the values
    rather than their spans: the Device Builder's validation output quotes the
    offending lines back, so a file with an inline password can print it into an
    error message that has no YAML structure to splice.

    Short values are dropped (the MIN_CROSS_CHECK_LENGTH floor) because a scrub
    is a blind substring replace over arbitrary output, where a two-character
    "value" would corrupt unrelated text.
    """
    values: set[str] = set()
    for start, end, _dotted in _secret_spans(text, secret_values or set()):
        raw = text[start:end].strip().strip("\"'")
        if len(raw) >= MIN_CROSS_CHECK_LENGTH:
            values.add(raw)
    return values


def scrub_secret_values(text: str, values: set[str]) -> str:
    """Replace every occurrence of every known credential in free text.

    Longest first, so a value that contains another leaves no readable remainder
    behind. The placeholder is fixed rather than path-derived, because this is
    unstructured output rather than a position in a file.
    """
    out = text
    for value in sorted(values, key=len, reverse=True):
        if value:
            out = out.replace(value, "<redacted>")
    return out
