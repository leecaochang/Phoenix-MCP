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

import difflib
import re
from collections.abc import Iterator
from typing import Any

import yaml

from .policy_engine import is_sensitive_key
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

# Layer 2: key names that mark their value as a credential wherever they appear,
# on top of the shared is_sensitive_key markers. pass and pw are whole words of a
# key (admin_pass, wifi-pw) so that bypass or compass stay readable.
_SECRET_KEY_RE = re.compile(
    r"password|passwd|passphrase|passcode|psk|preshared|bindkey|api_key|api-key|apikey|token|secret"
    r"|private_key|authorization|encryption_key|(?:^|[_\-.])(?:pass|pw|pwd)(?:$|[_\-.])",
    re.IGNORECASE,
)
# A pin in a device file is a GPIO, not a credential, so that shared marker does not apply.
_GPIO_PIN_KEY_RE = re.compile(r"(?:^|[^a-z0-9])pin$", re.IGNORECASE)
# Every value below one of these keys is masked: request headers carry bearer
# tokens and API keys under names no list can anticipate.
_SECRET_SUBTREE_KEYS = frozenset({"headers", "request_headers"})

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
            or (is_sensitive_key(key) and not _GPIO_PIN_KEY_RE.search(key))
            or path[-2:] == ("encryption", "key")
            or not _SECRET_SUBTREE_KEYS.isdisjoint(path)
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


def _line_offsets(text: str, first_line: int, last_line: int) -> tuple[int, int]:
    """Character offsets of 1-indexed lines first..last (inclusive) in `text`.

    Lines are counted with splitlines, the way the Device Builder numbers them,
    so a CRLF or a lone CR ends a line here exactly as it does there.
    """
    lines = text.splitlines(keepends=True)
    if first_line < 1 or last_line < first_line or last_line > len(lines):
        raise ValueError(f"lines {first_line}..{last_line} are outside {len(lines)} lines")
    start = sum(len(line) for line in lines[:first_line - 1])
    return start, start + sum(len(line) for line in lines[first_line - 1:last_line])


def redact_esphome_slice(
    text: str, secret_values: set[str] | None, first_line: int, last_line: int
) -> str:
    """Lines first..last of a device file, with every credential in them masked.

    Spans are found over the WHOLE document, so a credential's path and the
    layer-3 cross-check read the same as in a full masked read. Every span that
    touches the slice is replaced by its placeholder, clipped to the slice: a
    block-scalar credential that begins above the first line still leaves none of
    its characters behind. Raises YamlParseError (fail closed) and ValueError for
    a range outside the file.
    """
    start, end = _line_offsets(text, first_line, last_line)
    spans = _secret_spans(text, secret_values or set())
    out = text[start:end]
    for span_start, span_end, dotted in spans:
        if span_end <= start or span_start >= end:
            continue
        cut_start, cut_end = max(span_start, start) - start, min(span_end, end) - start
        out = out[:cut_start] + placeholder_for(dotted) + out[cut_end:]
    return out


# Component and platform names as ESPHome spells them. Anything else in a key
# position is not reported: names only ever describe the file, never its values.
_COMPONENT_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def _uses_include(node: Any, depth: int = 0) -> bool:
    """Whether any node carries an !include-style tag (packages pulled from elsewhere)."""
    if depth > 64:
        return True
    tag = getattr(node, "tag", None)
    if isinstance(tag, str) and tag.startswith("!include"):
        return True
    if isinstance(node, yaml.MappingNode):
        return any(_uses_include(k, depth + 1) or _uses_include(v, depth + 1) for k, v in node.value)
    if isinstance(node, yaml.SequenceNode):
        return any(_uses_include(item, depth + 1) for item in node.value)
    return False


def _platform_names(node: Any) -> list[str]:
    """The `platform:` scalars of a component block: a list of entries or one mapping."""
    entries = node.value if isinstance(node, yaml.SequenceNode) else [node]
    names = []
    for entry in entries:
        if not isinstance(entry, yaml.MappingNode):
            continue
        for key_node, value_node in entry.value:
            if (getattr(key_node, "value", None) == "platform"
                    and isinstance(value_node, yaml.ScalarNode) and not _is_tagged(value_node)
                    and isinstance(value_node.value, str)
                    and _COMPONENT_NAME_RE.fullmatch(value_node.value)):
                names.append(value_node.value)
    return names


def declared_components(text: str) -> dict[str, Any]:
    """The components and platforms a device file declares itself, by name only.

    Read from the file's own top level, so what `packages:` or an `!include`
    brings in is not seen; uses_includes says when that is the case. Composition
    only: no constructor runs and no value is ever returned. Raises
    YamlParseError for a file that does not parse.
    """
    root = _compose(text)
    components: list[str] = []
    platforms: dict[str, list[str]] = {}
    if isinstance(root, yaml.MappingNode):
        for key_node, value_node in root.value:
            key = getattr(key_node, "value", None)
            if not isinstance(key, str) or not _COMPONENT_NAME_RE.fullmatch(key):
                continue
            components.append(key)
            names = _platform_names(value_node)
            if names:
                platforms[key] = sorted(set(names))
    return {
        "components": sorted(set(components)),
        "platforms": platforms,
        "uses_includes": "packages" in components or (root is not None and _uses_include(root)),
    }


def apply_line_splice(text: str, from_line: int, to_line: int, replacement: str) -> str:
    """Apply a Device Builder YamlDiff to `text`.

    Same rules as the add-on's own apply_yaml_diff: lines are 1-indexed and
    inclusive in the OLD text, `to_line == from_line - 1` is a pure insert before
    `from_line`, and the replacement's final terminator is implied, so a splice
    reaching an unterminated last line leaves the text unterminated. Raises
    ValueError for a range outside the text.
    """
    lines = text.splitlines(keepends=True)
    if to_line < from_line - 1:
        raise ValueError(f"splice {from_line}..{to_line} is inverted")
    if from_line < 1 or to_line > len(lines):
        raise ValueError(f"splice {from_line}..{to_line} is outside {len(lines)} lines")
    head, tail = "".join(lines[:from_line - 1]), "".join(lines[to_line:])
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    body = replacement.removesuffix("\n").removesuffix("\r")
    open_ended = not tail and bool(lines) and len(f"{lines[-1][-1]}x".splitlines()) != 2
    if not body:
        if open_ended:
            head = head.removesuffix("\n").removesuffix("\r")
    else:
        if head and len(f"{head[-1]}x".splitlines()) != 2:
            head += newline
        if not open_ended:
            body += replacement[len(body):] or newline
    return head + body + tail


def masked_unified_diff(
    before: str, after: str, before_label: str, after_label: str, max_chars: int
) -> tuple[str, bool]:
    """A unified diff of two already-masked texts, bounded to max_chars.

    Only ever fed masked text: the inputs come from redact_esphome_text, so the
    diff can show where a credential sits but never what it is.
    """
    def lines(text: str) -> list[str]:
        # A last line without a terminator would run into the next diff line.
        out = text.splitlines(keepends=True)
        if out and not out[-1].endswith(("\n", "\r")):
            out[-1] += "\n"
        return out

    diff = "".join(difflib.unified_diff(
        lines(before), lines(after), fromfile=before_label, tofile=after_label, n=2,
    ))
    if len(diff) <= max_chars:
        return diff, False
    return diff[:max_chars], True


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
