"""YAML parsing bounds file size, nesting depth and alias expansion.

Configuration and device YAML is parsed with the bounded loader, so a hostile
document is refused before a tree walk, a redaction pass or an entry lookup
consumes it.
"""

import pytest
import yaml

from custom_components.phoenix_mcp.const import MAX_FILE_BYTES, YAML_MAX_DEPTH
from custom_components.phoenix_mcp.esphome_yaml import redact_esphome_text
from custom_components.phoenix_mcp.yaml_includes import YamlParseError, load_tagged_lenient, read_entry


def alias_graph(depth=8):
    text = "v0: &v0 [light.kitchen]\n"
    for level in range(1, depth + 1):
        text += f"v{level}: &v{level} [" + ", ".join([f"*v{level - 1}"] * 9) + "]\n"
    return text


@pytest.mark.parametrize("content", [alias_graph(), "x: &x [*x]\n", "[" * 2000 + "]" * 2000])
def test_existing_yaml_and_esphome_reject_unbounded_graphs(content, tmp_path):
    with pytest.raises(YamlParseError):
        load_tagged_lenient(content)
    with pytest.raises(YamlParseError):
        redact_esphome_text(content)
    (tmp_path / "scripts.yaml").write_text(content)
    assert read_entry(str(tmp_path), "script", "v8") is None


def test_depth_refusal_bounds_scanner_work(monkeypatch):
    # Nesting under the limit parses.
    assert load_tagged_lenient("[" * 8 + "]" * 8) == [[[[[[[[]]]]]]]]
    calls = 0
    original = yaml.scanner.Scanner.fetch_flow_sequence_start

    def count(scanner):
        nonlocal calls
        calls += 1
        assert calls <= YAML_MAX_DEPTH + 4, "Unbounded scanning before nesting refusal"
        return original(scanner)

    monkeypatch.setattr(yaml.scanner.Scanner, "fetch_flow_sequence_start", count)
    with pytest.raises(YamlParseError, match="depth"):
        load_tagged_lenient("[" * 2000 + "]" * 2000)


def test_size_refusal_precedes_parser_work(monkeypatch):
    def unexpected(_self):
        pytest.fail("Oversized content reached the scanner")

    monkeypatch.setattr(yaml.scanner.Scanner, "fetch_more_tokens", unexpected)
    with pytest.raises(YamlParseError, match="file size"):
        load_tagged_lenient(" " * (MAX_FILE_BYTES + 1))


def test_bounded_aliases_keep_path_sensitive_esphome_secret_checks():
    text = "label: &shared private-value\napi:\n  password: *shared\n"
    assert load_tagged_lenient(text)["api"]["password"] == "private-value"
    redacted, paths = redact_esphome_text(text)
    assert "api.password" in paths
    assert "private-value" not in redacted
    assert load_tagged_lenient("items: &items [one, two]\ncopy: *items")["copy"] == ["one", "two"]


def test_alias_chains_cannot_exceed_walk_depth():
    text = "v0: &v0 [value]\n"
    for level in range(1, YAML_MAX_DEPTH + 1):
        text += f"v{level}: &v{level} [*v{level - 1}]\n"
    with pytest.raises(YamlParseError, match="depth"):
        load_tagged_lenient(text)
