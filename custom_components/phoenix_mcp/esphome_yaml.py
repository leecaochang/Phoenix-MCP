"""Credential masking for YAML files, walked as composed nodes rather than matched by line.

ESPHome device files often hold credentials inline, and get_yaml_config reads other software's
files beside Home Assistant's, so both reads share this walk. Masking is span-surgical:
``yaml.compose`` gives each scalar's source span, so a placeholder is spliced into the raw text
and formatting survives. Device files feed three layers into one span set: curated paths,
credential-looking keys at any depth, and values matching secrets.yaml. Every scalar below a
credential-named key is masked, and every other scalar is scanned for URLs, tokens, quoted
assignments and the text of the credentials found, which a file can repeat under a neutral key.
A comment naming or quoting a credential goes with the comments touching it, since a commented-out
block spans lines. The output is for reading: no tool writes it to a file.

Over-redaction is the accepted failure direction. ``secrets.yaml`` is parsed here only to know
what not to emit; no tool can read it.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import difflib
import re
from collections.abc import Callable, Collection, Iterable, Iterator
from typing import Any, NamedTuple

import yaml

from .const import REDACTION_SENTINEL
from .policy_engine import is_sensitive_key
from .redaction import redact_secrets_in_text
from .yaml_includes import (
    OPAQUE_TAGS,
    YAML_READ_ERRORS,
    PhoenixLenientLoader,
    YamlParseError,
    yaml_error_detail,
)

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

# Layer 2: key names that mark what they hold as a credential wherever they appear, on top
# of the shared is_sensitive_key markers. Device files name keys freely (aes_key, mqttpass,
# pw2, a bare key), so anything ending in key or pass counts apart from bypass, compass and
# the public keys below.
_SECRET_KEY_RE = re.compile(
    r"password|passwd|passphrase|passcode|psk|pre[_\-]?shared|bindkey|api_key|api-key|apikey|token"
    r"|secret|private_key|authorization|encryption|(?:^|[_\-.\s])key$|(?-i:[a-z0-9]Key$)"
    r"|(?:^|[_\-.])(?:pass|pw|pwd|wep|sas|llat)\d*(?:$|[_\-.])|pw\d*$|(?<!by)(?<!com)pass\d*$",
    re.IGNORECASE,
)
_PUBLIC_KEY_NAMES = frozenset({"public_key", "peer_public_key", "on_key"})
# A pin in a device file is a GPIO, not a credential, so that shared marker does not apply.
_GPIO_PIN_KEY_RE = re.compile(r"(?:^|[^a-z0-9])pin$", re.IGNORECASE)
# Every value below such a key is masked, in either kind of file: request headers carry
# bearer tokens and API keys under names no list can anticipate.
_HEADERS_KEY_RE = re.compile(r"headers$", re.IGNORECASE)

# Path segments for what a mapping key is rather than a name. The value of a mapping used as
# a key cannot be judged by name, so everything below one counts as a credential.
_KEY_SEGMENT = "<key>"
_COMPLEX_KEY = "<complex key>"
_HIDDEN_KEY = "<key name>"
# A mapping key below a credential key stays readable only when it reads as an option, as in
# `encryption: {key: ...}`. Anything else there can be the credential: `tokens:` maps a token to
# a client name in several add-ons. Digits are left out because generated tokens hold them.
_OPTION_NAME_RE = re.compile(r"[a-z][a-z_]{0,31}")

# Credentials in a scalar's text that redact_secrets_in_text leaves: a token alone as URL
# userinfo, the query parameter map and search APIs use, a bearer value, and a credential-named
# string assigned in a lambda or a JSON body. Bounded and anchored, as these scan whole files.
_URL_USERINFO_RE = re.compile(r"://[^/\s@]{1,512}@")
_URL_PARAM_RE = re.compile(r"([?&#;][\w.\-]{1,64}=)[^&#\s\"']+")
_URL_CREDENTIAL_PARAM_RE = re.compile(r"(?i)(?:^|[_\-])key$|^(?:sig|signature|pass|passwd|pwd)$")
_BEARER_RE = re.compile(r"(?i)\b(bearer\s{1,16})[A-Za-z0-9._~+/=\-]{8,}")
_QUOTED_ASSIGNMENT_RE = re.compile(
    r"""(?<![\w\-])([A-Za-z_][\w\-]{0,127})"""
    r"""((?:["'\]]{0,3}\s{0,16}[:=]|\[\s{0,16}\w{0,16}\s{0,16}\]\s{0,16}=|\s{0,16}[({])\s{0,16}|\s{1,16})"""
    r"""(["'])(?:[^\\\n]|\\.){0,4096}?\3"""
)

_SCRUB_MARK_RE = re.compile(r"<(?:redacted|token|phoenix-access-key)>")
_SCALAR_PROPERTIES_RE = re.compile(r"(?:[&!]\S*\s+)+")
_LINE_BREAK_RE = re.compile("[\r\n\x85\u2028\u2029]")
_COMMENT_RE = re.compile(r"#[^\r\n]*")
_BLOCK_HEADER_COMMENT_RE = re.compile(r"[|>][+\-0-9]*[ \t]+(#[^\r\n]*)")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_MASKED_COMMENT = f"# {REDACTION_SENTINEL}"
_YAML_STR_TAG = "tag:yaml.org,2002:str"
_YAML_BOOL_TAG = "tag:yaml.org,2002:bool"

# (start, end, dotted path, replacement). A None replacement takes the caller's placeholder;
# a comment has an empty path, so it reports nothing.
Span = tuple[int, int, str, str | None]

# Layer 3 floor. It catches a credential inlined in one file but referenced by
# `!secret` in another. A short secrets.yaml value (a pin number, a small integer)
# would match unrelated scalars all over a device file and mangle it, so only
# values of at least this length are cross-checked.
MIN_CROSS_CHECK_LENGTH = 8

_PLACEHOLDER_PREFIX = "__PHOENIX_REDACTED__"
_PLACEHOLDER_SUFFIX = "__"

_ALLOWED_SUFFIXES = (".yaml", ".yml")
_ARCHIVE_DIR = "archive"

def placeholder_for(path: str) -> str:
    """Return the path-based placeholder for a dotted path, never value-derived."""
    return f"{_PLACEHOLDER_PREFIX}{path}{_PLACEHOLDER_SUFFIX}"


def esphome_rel_path_ok(rel: str) -> bool:
    """Return whether a config-relative path may be read as device YAML.

    Follows _yaml_read_path_ok, except that ``archive/`` is refused and ``esphome/`` admitted.
    No file named like secrets is reachable, as a backup copy holds the same values.
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
    name = parts[-1].lower()
    return "secret" not in name and name.endswith(_ALLOWED_SUFFIXES)


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


def _hides_key(key_node: Any, value_node: Any, path: tuple[str, ...], secret_key: Callable[[str], bool]) -> bool:
    """Return whether a mapping key below a credential key is itself treated as a credential.

    A key with no value is the value, as in a !!set or `? x`. A header's name stays readable.
    """
    if not isinstance(key_node, yaml.ScalarNode) or _COMPLEX_KEY in path:
        return False
    named = any(secret_key(segment) for segment in path)
    if not named and not any(_HEADERS_KEY_RE.search(segment) for segment in path):
        return False
    if isinstance(value_node, yaml.ScalarNode) and not value_node.value:
        return True
    return named and not _OPTION_NAME_RE.fullmatch(key_node.value)


def _no_key_is_secret(_name: str) -> bool:
    """Key test for a walk that treats every key as a name."""
    return False


def _walk(
    node: Any, path: tuple[str, ...], secret_key: Callable[[str], bool] = _no_key_is_secret
) -> Iterator[tuple[str, tuple[str, ...], Any]]:
    """Yield (dotted_path, path_tuple, scalar_node) for every scalar, mapping keys included.

    A key that may be a credential enters paths as its index, never its text.
    """
    if isinstance(node, yaml.MappingNode):
        for index, (key_node, value_node) in enumerate(node.value):
            if _hides_key(key_node, value_node, path, secret_key):
                slot = f"[{index}]"
                yield from _walk(key_node, path + (slot, _HIDDEN_KEY), secret_key)
                yield from _walk(value_node, path + (slot,), secret_key)
                continue
            key = getattr(key_node, "value", None)
            name = key if isinstance(key, str) else _COMPLEX_KEY
            yield from _walk(key_node, path + (_KEY_SEGMENT if isinstance(key, str) else _COMPLEX_KEY,), secret_key)
            yield from _walk(value_node, path + (name,), secret_key)
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            yield from _walk(item, path + (f"[{index}]",), secret_key)
    elif isinstance(node, yaml.ScalarNode):
        yield _format_path(path), path, node


def _compose(text: str) -> Any:
    """Parse to a node tree, keeping unknown tags; no constructor runs."""
    try:
        return yaml.compose(text, Loader=PhoenixLenientLoader)
    except (yaml.YAMLError, RecursionError) as err:
        raise YamlParseError(yaml_error_detail(err)) from err


def _normalized_path(path: tuple[str, ...]) -> tuple[str, ...]:
    """Path with list indices collapsed to [] so curated patterns can match."""
    return tuple("[]" if seg.startswith("[") and seg.endswith("]") else seg for seg in path)


def _is_tagged(node: yaml.ScalarNode) -> bool:
    """Return whether a scalar carries a custom tag."""
    return isinstance(node.tag, str) and node.tag.startswith("!")


def _holds_no_value(node: yaml.ScalarNode) -> bool:
    """Return whether a scalar has nothing to hide: empty, a flag, or a reference elsewhere."""
    # An option below `encryption:` such as allow_plaintext_upload is a flag. `!env_var NAME
    # default` carries a value of its own, and !lambda holds code.
    if node.tag == "!env_var":
        return len(node.value.split()) == 1
    if not node.value:
        # An unquoted credential starting with & or ! parses as an anchor or a tag on nothing.
        return node.style is not None or node.start_mark.index == node.end_mark.index
    if node.tag == _YAML_BOOL_TAG:
        return node.value.lower() in yaml.constructor.SafeConstructor.bool_values
    return node.tag in OPAQUE_TAGS


def _under_secret_key(path: tuple[str, ...], secret_key: Callable[[str], bool]) -> bool:
    """Return whether a path runs through a key marking everything below it as a credential.

    A mapping's own key stays readable, unless it is part of a complex key.
    """
    if path[-1:] == (_KEY_SEGMENT,) and _COMPLEX_KEY not in path:
        return False
    return any(
        segment == _COMPLEX_KEY or secret_key(segment) or bool(_HEADERS_KEY_RE.search(segment))
        for segment in path
    )


def _device_secret_key(name: str) -> bool:
    """Return whether a key name in a device file marks a credential."""
    if name.lower() in _PUBLIC_KEY_NAMES:
        return False
    return bool(_SECRET_KEY_RE.search(name)) or (
        is_sensitive_key(name) and not _GPIO_PIN_KEY_RE.search(name))


def _device_secret_path(path: tuple[str, ...]) -> bool:
    """Return whether a scalar at this path of a device file is a credential."""
    return _path_matches_curated(_normalized_path(path)) or _under_secret_key(path, _device_secret_key)


def _config_secret_path(path: tuple[str, ...]) -> bool:
    """Return whether a scalar at this path of a configuration file is a credential."""
    return _under_secret_key(path, is_sensitive_key)


def _scrubbed(text: str, secret_key: Callable[[str], bool]) -> str:
    """Return a scalar's or comment's text without the credentials visible in the text itself."""
    def assignment(match: re.Match[str]) -> str:
        if not secret_key(match[1]):
            return match[0]
        return f"{match[1]}{match[2]}{match[3]}{REDACTION_SENTINEL}{match[3]}"

    def parameter(match: re.Match[str]) -> str:
        name = match[1][1:-1]
        if not secret_key(name) and not _URL_CREDENTIAL_PARAM_RE.search(name):
            return match[0]
        return match[1] + REDACTION_SENTINEL

    out = redact_secrets_in_text(text) or ""
    out = _URL_USERINFO_RE.sub(f"://{REDACTION_SENTINEL}@", out)
    out = _URL_PARAM_RE.sub(parameter, out)
    out = _BEARER_RE.sub(lambda match: match[1] + REDACTION_SENTINEL, out)
    return _QUOTED_ASSIGNMENT_RE.sub(assignment, out)


def _credential_forms(value: str, source: str) -> set[str]:
    """The text a credential scalar can appear as elsewhere: decoded, as written, and by line."""
    properties = _SCALAR_PROPERTIES_RE.match(source)
    written = source[properties.end():] if properties else source
    lines = {value, written, *value.splitlines(), *written.splitlines()}
    forms = {line.strip().strip("\"'") for line in lines}
    return {form for form in forms if len(form) >= MIN_CROSS_CHECK_LENGTH}


def _embedded(source: str, clean: str) -> set[str]:
    """The credentials a scrub took out of `source`: the text between what `clean` kept."""
    pieces = _SCRUB_MARK_RE.split(clean)
    found: set[str] = set()
    cursor = 0
    for index, piece in enumerate(pieces):
        at = len(source) if not piece and index == len(pieces) - 1 else source.find(piece, cursor)
        if at < 0:
            break
        removed = source[cursor:at].strip().strip("\"'")
        if index:
            found.update((removed, removed.partition(":")[2]))
        cursor = at + len(piece)
    return {value for value in found if len(value) >= MIN_CROSS_CHECK_LENGTH}


def _value_hits(chunks: list[str], values: Iterable[str]) -> dict[int, set[str]]:
    """Which chunks hold which values, in one pass over all of them per value."""
    joined = "\0".join(chunks)
    starts = [0]
    for chunk in chunks:
        starts.append(starts[-1] + len(chunk) + 1)
    hits: dict[int, set[str]] = {}
    for value in filter(None, values):
        at = joined.find(value)
        while at >= 0:
            hits.setdefault(bisect_right(starts, at) - 1, set()).add(value)
            at = joined.find(value, at + len(value))
    return hits


def _node_span(text: str, node: yaml.ScalarNode) -> tuple[int, int]:
    """A scalar's source span; a block scalar's end mark runs into the next key's indentation."""
    start, end = node.start_mark.index, node.end_mark.index
    while end > start and text[end - 1] in " \t\r\n":
        end -= 1
    return start, end


def _node_text(text: str, node: yaml.ScalarNode) -> str:
    """A scalar's source text, its anchor, tag and block header included."""
    start, end = _node_span(text, node)
    return text[start:end]


def _load_secrets(text: str) -> Any:
    """The node tree of a secrets.yaml, or None when it is empty or does not parse."""
    try:
        return _compose(text)
    except YamlParseError:
        return None


def secret_values_from_text(text: str) -> set[str]:
    """Values defined in a secrets.yaml at any depth, for the layer-3 cross-check.

    Returns an empty set for unparseable or empty input: a missing secrets.yaml
    degrades redaction to layers 1 and 2 rather than failing every read.
    """
    root = _load_secrets(text)
    if not isinstance(root, yaml.MappingNode):
        return set()
    values: set[str] = set()
    for _dotted, path, node in _walk(root, ()):
        if path[-1:] != (_KEY_SEGMENT,) and not _holds_no_value(node):
            values |= _credential_forms(node.value, _node_text(text, node))
    return values


def secret_keys_from_text(text: str) -> set[str]:
    """Key NAMES defined in a secrets.yaml, reported as defined_secrets."""
    root = _load_secrets(text)
    if not isinstance(root, yaml.MappingNode):
        return set()
    return {key_node.value for key_node, _value in root.value if isinstance(key_node, yaml.ScalarNode)}


class _Partition(NamedTuple):
    """A document's scalars, split by whether they are a credential whole."""

    credentials: list[tuple[str, tuple[str, ...], Any]]
    others: list[tuple[str, tuple[str, ...], Any]]
    # Credential text, all of it, and the part safe to look for inside other text: a value
    # that is also a key of this file would rewrite the file's structure.
    values: set[str]
    searchable: set[str]


def _partition(
    text: str,
    root: Any,
    secret_path: Callable[[tuple[str, ...]], bool],
    secret_key: Callable[[str], bool],
    secret_values: Collection[str],
) -> _Partition:
    """Split a document's scalars, collecting the text of every whole credential."""
    credentials, others = [], []
    values = set(secret_values)
    names: set[str] = set()
    for item in _walk(root, (), secret_key):
        _dotted, path, node = item
        if path[-1:] == (_KEY_SEGMENT,):
            names.add(node.value)
        if _holds_no_value(node):
            continue
        if secret_path(path) or (len(node.value) >= MIN_CROSS_CHECK_LENGTH and node.value in secret_values):
            credentials.append(item)
            values |= _credential_forms(node.value, _node_text(text, node))
        else:
            others.append(item)
    return _Partition(credentials, others, values, {
        value for value in values if len(value) >= MIN_CROSS_CHECK_LENGTH and value not in names})


class _Scan(NamedTuple):
    """What a document holds: the spans to change and the credential text behind them."""

    spans: list[Span]
    values: set[str]
    searchable: set[str]


def _scan_scalars(
    text: str,
    secret_path: Callable[[tuple[str, ...]], bool],
    secret_key: Callable[[str], bool],
    secret_values: Collection[str],
) -> _Scan:
    """Every scalar to change: whole for a credential, or with its embedded credential replaced."""
    root = _compose(text)
    if root is None:
        return _Scan([], set(secret_values), set(secret_values))
    found = _partition(text, root, secret_path, secret_key, secret_values)
    spans: list[Span] = [(*_node_span(text, node), dotted, None) for dotted, _path, node in found.credentials]
    values = set(found.values)
    hits = _value_hits([node.value for _dotted, _path, node in found.others], found.searchable)
    for index, (dotted, _path, node) in enumerate(found.others):
        start, end = _node_span(text, node)
        repeated = hits.get(index, set())
        # A block header's line is left to the comment pass, which can blank a comment on it.
        header_end = _LINE_BREAK_RE.search(text, start, end) if node.style in ("|", ">") else None
        body = header_end.start() if header_end else start
        source = text[body:end]
        clean = scrub_secret_values(_scrubbed(source, secret_key), repeated)
        decoded = _scrubbed(node.value, secret_key)
        if any(value not in source for value in repeated) or (clean == source and decoded != node.value):
            # Quoting or folding hid it from the source text.
            spans.append((start, end, dotted, None))
            values |= _embedded(node.value, decoded)
        elif clean != source:
            spans.append((body, end, dotted, clean))
            values |= _embedded(source, clean)
    paths = _value_hits([span[2] for span in spans], set(secret_values) | found.searchable)
    for index, repeated in paths.items():
        start, end, dotted, replacement = spans[index]
        spans[index] = (start, end, scrub_secret_values(dotted, repeated), replacement)
    return _Scan(spans, values, found.searchable)


def _comments(text: str) -> list[tuple[int, int]]:
    """Every comment's span: the scanner keeps none, so they are what lies between its tokens."""
    found: list[tuple[int, int]] = []
    cursor = 0
    try:
        for token in yaml.scan(text, Loader=PhoenixLenientLoader):
            start = token.start_mark.index
            if start > cursor:
                found.extend(match.span() for match in _COMMENT_RE.finditer(text, cursor, start))
            cursor = max(cursor, token.end_mark.index)
            if isinstance(token, yaml.ScalarToken) and token.style in ("|", ">"):
                header = _BLOCK_HEADER_COMMENT_RE.match(text, start)
                if header is not None:
                    found.append(header.span(1))
    except yaml.YAMLError as err:
        raise YamlParseError(yaml_error_detail(err)) from err
    return found


def _comment_names_secret(comment: str, secret_key: Callable[[str], bool]) -> bool:
    """Return whether a comment's words or text mark it as holding a credential."""
    words = _WORD_RE.findall(comment)
    names = words + [f"{first}_{second}" for first, second in zip(words, words[1:])]
    return any(secret_key(name) for name in names) or _scrubbed(comment, secret_key) != comment


def _comment_spans(
    text: str, scalars: list[Span], secret_key: Callable[[str], bool], values: Iterable[str]
) -> list[Span]:
    """Comments to blank: those naming or quoting a credential or beside a changed scalar.

    Adjacent comment lines go together, since a commented-out key's value or PEM body is on
    the lines after its name.
    """
    comments = _comments(text)
    if not comments:
        return []
    quoting = {comments[index] for index in _value_hits([text[start:end] for start, end in comments], values)}
    breaks = [match.start() for match in re.finditer("\n", text)]
    changed = {bisect_left(breaks, index) for start, end, *_ in scalars for index in (start, end)}
    spans: list[Span] = []
    run: list[tuple[int, int, int]] = []
    previous_line = -2

    def close_run() -> None:
        if any(line in changed or (start, end) in quoting or _comment_names_secret(text[start:end], secret_key)
               for line, start, end in run):
            spans.extend((start, end, "", _MASKED_COMMENT) for _line, start, end in run)
        run.clear()

    for start, end in comments:
        line = bisect_left(breaks, start)
        first = breaks[line - 1] + 1 if line else 0
        alone = not text[first:start].strip()
        if not alone or line != previous_line + 1:
            close_run()
        run.append((line, start, end))
        previous_line = line if alone else -2
    close_run()
    return spans


def _splice(text: str, spans: list[Span], placeholder: Callable[[str], str]) -> str:
    """Apply spans once each, as an aliased scalar is reached by every path to it."""
    parts: list[str] = []
    cursor = 0
    # A whole-scalar mask sorts first, so a header comment inside it is skipped with it.
    for start, end, dotted, replacement in sorted(spans, key=lambda span: (span[0], span[3] is not None)):
        if start < cursor:
            continue
        parts += [text[cursor:start], placeholder(dotted) if replacement is None else replacement]
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _secret_spans(text: str, secret_values: Collection[str]) -> list[Span]:
    """Every span of a device file to change, unioning all three detection layers."""
    scan = _scan_scalars(text, _device_secret_path, _device_secret_key, secret_values)
    return scan.spans + _comment_spans(text, scan.spans, _device_secret_key, scan.searchable)


def redact_esphome_text(
    text: str, secret_values: set[str] | None = None
) -> tuple[str, list[str]]:
    """Return (redacted_text, sorted redacted paths).

    Raises YamlParseError, failing closed: an unparsed file's credentials cannot be located.
    """
    spans = _secret_spans(text, secret_values or set())
    return _splice(text, spans, placeholder_for), sorted(span[2] for span in spans if span[2])


def mask_yaml_text(text: str) -> str:
    """Return a configuration file with every credential shown as the redaction sentinel.

    `!secret` and include references stay, as they hold no value. Raises YamlParseError,
    failing closed: an unparsed file's credentials cannot be located.
    """
    scan = _scan_scalars(text, _config_secret_path, is_sensitive_key, ())
    spans = scan.spans + _comment_spans(text, scan.spans, is_sensitive_key, scan.searchable)
    return _splice(text, spans, lambda _dotted: REDACTION_SENTINEL)


def masked_yaml_data(text: str) -> Any:
    """Load a configuration file with its credentials already replaced in the node tree.

    Masking the shared node means an alias or merge of a credential cannot bring it back under
    a harmless name. Raises YamlParseError, as mask_yaml_text does.
    """
    try:
        loader = PhoenixLenientLoader(text)
    except yaml.YAMLError as err:
        raise YamlParseError(yaml_error_detail(err)) from err
    try:
        root = loader.get_single_node()
        if root is None:
            return None
        found = _partition(text, root, _config_secret_path, is_sensitive_key, ())
        hits = _value_hits([node.value for _dotted, _path, node in found.others], found.searchable)
        for _dotted, path, node in found.credentials:
            # Masked keys stay distinct, or a set or mapping would lose all but one of them.
            suffix = path[-2] if path[-1] == _HIDDEN_KEY else ""
            node.tag, node.value = _YAML_STR_TAG, f"{REDACTION_SENTINEL}{suffix}"
        for index, (_dotted, _path, node) in enumerate(found.others):
            clean = scrub_secret_values(_scrubbed(node.value, is_sensitive_key), hits.get(index, ()))
            if clean != node.value:
                node.tag, node.value = _YAML_STR_TAG, clean
        return loader.construct_document(root)
    except YAML_READ_ERRORS as err:
        raise YamlParseError(yaml_error_detail(err)) from err
    finally:
        loader.dispose()


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
    """Return lines first..last of a device file with every credential masked.

    Spans come from the whole document, and any touching the slice are clipped to it. Raises
    YamlParseError (fail closed) and ValueError for a range outside the file.
    """
    start, end = _line_offsets(text, first_line, last_line)
    clipped: list[Span] = []
    for span_start, span_end, dotted, replacement in _secret_spans(text, secret_values or set()):
        if span_end <= start or span_start >= end:
            continue
        cut_start, cut_end = max(span_start, start), min(span_end, end)
        whole = (cut_start, cut_end) == (span_start, span_end)
        clipped.append((cut_start - start, cut_end - start, dotted, replacement if whole else None))
    return _splice(text[start:end], clipped, placeholder_for)


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
    """Return the components and platforms a device file declares itself, by name only.

    Top level only, so ``uses_includes`` flags what packages or includes hide. No constructor
    runs. Raises YamlParseError for a file that does not parse.
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


_SUBSTITUTION_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
# The spellings ESPHome's boolean validator accepts as true.
_YAML_TRUE = frozenset({"true", "yes", "on", "enable"})


def _scalar_entries(node: Any) -> dict[str, str]:
    """The plain scalar values of a mapping node, by key."""
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {
        key_node.value: value_node.value for key_node, value_node in node.value
        if isinstance(key_node, yaml.ScalarNode) and isinstance(value_node, yaml.ScalarNode)
        and not _is_tagged(value_node)
    }


def _plain(node: Any) -> str:
    """An untagged scalar node's text, else nothing."""
    return node.value if isinstance(node, yaml.ScalarNode) and not _is_tagged(node) else ""


def _keys(node: Any) -> dict[str, Any]:
    """A mapping node's value nodes by scalar key; empty for anything else."""
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {key_node.value: value_node for key_node, value_node in node.value
            if isinstance(key_node, yaml.ScalarNode)}


class DeclaredDevice(NamedTuple):
    """What a device file says about the device it builds, as far as the file alone can tell."""

    name: str | None
    mac_suffix: bool
    # Where a flash of this file goes when that is stated, which can be another device's address.
    addresses: tuple[str, ...]
    # A literal name, no use_address, and nothing merged in that could supply either.
    self_contained: bool


# Top-level keys that bring configuration in from elsewhere.
_IMPORTING_KEYS = frozenset({"packages", "dashboard_import", "<<"})
# The components ESPHome takes an OTA address from.
_NETWORK_KEYS = ("wifi", "ethernet", "openthread")


def declared_device(text: str) -> DeclaredDevice | None:
    """Return what a file declares about its device, or None for a file that is not a mapping.

    ``name`` is None when the file does not state one: a name from a package or an include, or a
    substitution it does not define. No constructor runs. Raises YamlParseError for a file that
    does not parse.
    """
    root = _compose(text)
    if not isinstance(root, yaml.MappingNode):
        return None
    top = _keys(root)
    settings = _scalar_entries(top.get("esphome"))
    substitutions = _scalar_entries(top.get("substitutions"))

    def _expanded(value: str) -> str | None:
        parts = _SUBSTITUTION_RE.split(value)
        names = [part for index, part in enumerate(parts) if index % 3 and part]
        if any(name not in substitutions for name in names):
            return None
        return _SUBSTITUTION_RE.sub(lambda match: substitutions[match[1] or match[2]], value)

    written = settings.get("name", "")
    name = _expanded(written) or None
    suffix = _expanded(settings.get("name_add_mac_suffix", ""))
    addresses: list[str] = []
    contained = written == name and not _IMPORTING_KEYS & top.keys() and not _uses_include(root)
    for block in (_keys(top.get(key)) for key in _NETWORK_KEYS):
        contained = contained and "use_address" not in block and "<<" not in block
        stated = _scalar_entries(block.get("manual_ip")).get("static_ip", "")
        for value in (_plain(block.get("use_address")), stated):
            address = _expanded(value)
            if address:
                addresses.append(address)
    # A flag this file cannot settle widens the match, the safe side for an owner lookup.
    return DeclaredDevice(name, suffix is None or suffix.lower() in _YAML_TRUE, tuple(addresses), contained)


def apply_line_splice(text: str, from_line: int, to_line: int, replacement: str) -> str:
    """Apply a Device Builder YamlDiff to ``text``, by the add-on's apply_yaml_diff rules.

    Lines are 1-indexed and inclusive in the old text; ``to_line == from_line - 1`` is an
    insert. Raises ValueError for a range outside the text.
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
    """Return the credential text a device file carries, for scrubbing free text.

    Same three layers as masking; validation output can quote them. A credential is collected
    decoded, as written and line by line, and one inside a larger value as the matched text alone.
    Values under MIN_CROSS_CHECK_LENGTH are dropped, as the scrub is a blind substring replace.
    """
    scan = _scan_scalars(text, _device_secret_path, _device_secret_key, secret_values or set())
    return {value for value in scan.values if len(value) >= MIN_CROSS_CHECK_LENGTH}


def scrub_secret_values(text: str, values: Iterable[str]) -> str:
    """Replace every occurrence of every known credential in free text, longest first."""
    out = text
    for value in sorted(values, key=len, reverse=True):
        if value:
            out = out.replace(value, REDACTION_SENTINEL)
    return out


def _strings_in(data: Any) -> Iterator[str]:
    """Every string in JSON-like data, dictionary keys included."""
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for key, value in data.items():
            yield from _strings_in(key)
            yield from _strings_in(value)
    elif isinstance(data, (list, tuple)):
        for item in data:
            yield from _strings_in(item)


def _scrub_strings(data: Any, values: set[str]) -> Any:
    """Copy JSON-like data with `values` replaced in every string."""
    if isinstance(data, str):
        return scrub_secret_values(data, values)
    if isinstance(data, dict):
        return {_scrub_strings(key, values): _scrub_strings(value, values) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [_scrub_strings(item, values) for item in data]
    return data


def scrub_secret_data(data: Any, values: Iterable[str]) -> Any:
    """Return JSON-like data with every known credential replaced in its strings.

    For use before encoding, where a value holding a quote, a backslash or a non-ASCII character
    still reads as itself. Data holding none of the values comes back as it is.
    """
    hits = _value_hits(list(_strings_in(data)), values)
    return _scrub_strings(data, set().union(*hits.values())) if hits else data
