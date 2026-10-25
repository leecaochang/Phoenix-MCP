"""yaml_includes: include-graph resolution, locate, and the editor-file write boundary.

Pure-module tests (no hass fixture): everything runs against tmp_path config
trees. Reads follow every include flavor; writes splice only entries written
inline in Home Assistant's editor files and refuse hand-written YAML without
touching it. The oracle tests cross-check Phoenix MCP's include semantics
against HA's own annotatedyaml loader on the same tree.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
import yaml

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.helpers import content_hash


def write(base: Path, rel: str, content: str = "") -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def read(base: Path, rel: str) -> str:
    return (base / rel).read_text(encoding="utf-8")


DEFAULT_CONFIGURATION = """\
    homeassistant:
      name: Test Home
    automation: !include automations.yaml
    script: !include scripts.yaml
    scene: !include scenes.yaml
"""


# ---------------------------------------------------------------------------
# Tag layer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("snippet", [
    "value: !secret db_pass\n",
    "value: !env_var PORT 8080\n",
    "value: !input target_light\n",
    "value: !include other.yaml\n",
    "value: !include_dir_list dir\n",
    "value: !include_dir_merge_list dir\n",
    "value: !include_dir_named dir\n",
    "value: !include_dir_merge_named dir\n",
])
def test_tag_round_trip(snippet):
    data = yaml.load(snippet, Loader=yi.PhoenixSafeLoader)
    assert isinstance(data["value"], yi.TaggedValue)
    assert yi.dump_tagged(data) == snippet


def test_tagged_value_str():
    assert str(yi.TaggedValue("!secret", "db_pass")) == "!secret db_pass"
    assert str(yi.TaggedValue("!secret", "")) == "!secret"


def test_loader_does_not_touch_ha_shared_classes():
    """Phoenix MCP's constructors live only on PhoenixSafeLoader, never on yaml.SafeLoader."""
    assert "!secret" not in yaml.SafeLoader.yaml_constructors
    assert "!include" not in yaml.SafeLoader.yaml_constructors
    assert yi.TaggedValue not in yaml.SafeDumper.yaml_representers


def test_dump_parity_with_ha_dump():
    from homeassistant.util.yaml import dump as ha_dump

    data = {
        "alias": "Test",
        "empty": None,
        "flag": True,
        "count": 3,
        "ratio": 0.5,
        "tricky": ["on", "07:30:00", "yes", "unicode é"],
        "nested": {"list": [{"a": 1}, {"b": None}]},
    }
    assert yi.dump_tagged(data) == ha_dump(data)


def test_encode_tags():
    obj = {
        "password": yi.TaggedValue("!secret", "db_pass"),
        "nested": [yi.TaggedValue("!env_var", "PORT 8080"), "plain"],
    }
    assert yi.encode_tags(obj) == {
        "password": "!secret db_pass",
        "nested": ["!env_var PORT 8080", "plain"],
    }


def test_contains_include_tags():
    assert yi.contains_include_tags({"a": [yi.TaggedValue("!include", "x.yaml")]})
    assert not yi.contains_include_tags({"a": [yi.TaggedValue("!secret", "x")]})
    assert not yi.contains_include_tags({"a": ["!include x.yaml"]})  # plain string


@pytest.mark.parametrize("value,expected", [
    ("!secret db_pass", True),
    ("!secret", True),
    ("!include a.yaml", True),
    ("!include_dir_merge_list automations", True),
    ("!input name", True),
    ("!env_var PORT", True),
    ("!secretx", False),
    ("not !secret", False),
    ("!includes/other", False),
    ("plain", False),
])
def test_contains_tag_strings(value, expected):
    assert yi.contains_tag_strings({"nested": [{"k": value}]}) is expected


# ---------------------------------------------------------------------------
# Layout resolution
# ---------------------------------------------------------------------------


def test_layout_default(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert layout.error is None
    assert len(layout.targets) == 1
    assert layout.targets[0].flavor == "!include"
    assert layout.targets[0].path == os.path.realpath(str(tmp_path / "automations.yaml"))
    assert not layout.has_inline and not layout.has_packages


def test_layout_labeled_keys(tmp_path):
    write(tmp_path, "configuration.yaml", """\
        automation ui: !include automations.yaml
        automation manual: !include_dir_merge_list automations
    """)
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert [t.flavor for t in layout.targets] == ["!include", "!include_dir_merge_list"]
    assert [t.config_key for t in layout.targets] == ["automation ui", "automation manual"]


def test_layout_inline_and_packages(tmp_path):
    write(tmp_path, "configuration.yaml", """\
        homeassistant:
          packages: {}
        automation:
          - id: inline_one
            alias: Inline
    """)
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert layout.has_inline and layout.has_packages
    assert layout.targets == ()


def test_layout_included_core_config_counts_as_packages(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant: !include core.yaml\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert layout.has_packages


def test_layout_missing_or_unparseable_returns_none(tmp_path):
    assert yi.resolve_domain_layout(str(tmp_path), "automation") is None
    write(tmp_path, "configuration.yaml", "{{{ not yaml")
    assert yi.resolve_domain_layout(str(tmp_path), "automation") is None
    write(tmp_path, "configuration.yaml", "- just\n- a list\n")
    assert yi.resolve_domain_layout(str(tmp_path), "automation") is None


def test_layout_containment_violation(tmp_path):
    write(tmp_path, "configuration.yaml", "automation: !include ../outside.yaml\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert layout.error == yi.MSG_OUTSIDE_CONFIG.format(domain="automation")


def test_layout_shape_mismatch(tmp_path):
    write(tmp_path, "configuration.yaml", "script: !include_dir_merge_list scripts\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "script")
    assert layout.error == yi.MSG_SHAPE_MISMATCH.format(
        domain="script", tag="!include_dir_merge_list")
    write(tmp_path, "configuration.yaml", "automation: !include_dir_named automations\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert layout.error == yi.MSG_SHAPE_MISMATCH.format(
        domain="automation", tag="!include_dir_named")


def test_layout_non_include_tag_routes_nothing(tmp_path):
    write(tmp_path, "configuration.yaml", "automation: !secret weird\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert layout.targets == () and layout.error is None and not layout.has_inline


# ---------------------------------------------------------------------------
# Directory enumeration
# ---------------------------------------------------------------------------


def test_iter_dir_files_matches_ha_semantics(tmp_path):
    write(tmp_path, "inc/b.yaml", "- id: b\n")
    write(tmp_path, "inc/a.yaml", "- id: a\n")
    write(tmp_path, "inc/sub/c.yaml", "- id: c\n")
    write(tmp_path, "inc/.hidden.yaml", "- id: hidden\n")
    write(tmp_path, "inc/.hiddendir/d.yaml", "- id: d\n")
    write(tmp_path, "inc/secrets.yaml", "password: hunter2\n")
    write(tmp_path, "inc/notes.txt", "not yaml")
    files = yi._iter_dir_files(str(tmp_path / "inc"))
    names = [os.path.relpath(f, str(tmp_path / "inc")) for f in files]
    assert names[:2] == ["a.yaml", "b.yaml"]
    assert "sub/c.yaml" in names
    assert all(".hidden" not in n and "secrets.yaml" not in n and ".txt" not in n
               for n in names)


# ---------------------------------------------------------------------------
# Locate
# ---------------------------------------------------------------------------


def _automation_layout(tmp_path, config_body):
    write(tmp_path, "configuration.yaml", config_body)
    return yi.resolve_domain_layout(str(tmp_path), "automation")


def test_locate_plain_include_spans(tmp_path):
    write(tmp_path, "automations.yaml", """\
        # header
        - id: aaa
          alias: First
        # heading for second
        - id: bbb
          alias: Second
    """)
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    loc = yi.locate_entry(layout, "aaa")
    assert (loc.ref_kind, loc.start_line, loc.end_line) == ("list_item", 1, 3)
    loc = yi.locate_entry(layout, "bbb")
    assert (loc.start_line, loc.end_line) == (4, 6)
    assert loc.config == {"id": "bbb", "alias": "Second"}


def test_locate_lone_dash_extends_span(tmp_path):
    write(tmp_path, "automations.yaml", "-\n  id: aaa\n  alias: A\n- id: bbb\n")
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    loc = yi.locate_entry(layout, "aaa")
    assert (loc.start_line, loc.end_line) == (0, 3)


def test_locate_dir_list_whole_file(tmp_path):
    write(tmp_path, "automations/one.yaml", "id: aaa\nalias: A\n")
    layout = _automation_layout(tmp_path, "automation: !include_dir_list automations\n")
    loc = yi.locate_entry(layout, "aaa")
    assert loc.ref_kind == "whole_file"
    assert loc.ref_file.endswith("one.yaml")
    assert loc.config == {"id": "aaa", "alias": "A"}


def test_locate_dir_merge_list_items(tmp_path):
    write(tmp_path, "automations/pack.yaml", "- id: aaa\n  alias: A\n- id: bbb\n  alias: B\n")
    layout = _automation_layout(tmp_path, "automation: !include_dir_merge_list automations\n")
    loc = yi.locate_entry(layout, "bbb")
    assert loc.ref_kind == "list_item" and loc.ref_file.endswith("pack.yaml")
    assert (loc.start_line, loc.end_line) == (2, 4)


def test_locate_dir_named_stem_and_empty_file(tmp_path):
    write(tmp_path, "configuration.yaml", "script: !include_dir_named scripts\n")
    write(tmp_path, "scripts/hello.yaml", "alias: Hello\nsequence: []\n")
    write(tmp_path, "scripts/empty.yaml", "")
    layout = yi.resolve_domain_layout(str(tmp_path), "script")
    loc = yi.locate_entry(layout, "hello")
    assert loc.ref_kind == "whole_file" and loc.key == "hello"
    # annotatedyaml treats an empty !include_dir_named file as an empty dict.
    loc = yi.locate_entry(layout, "empty")
    assert loc.config == {}


def test_locate_dir_merge_named_pairs(tmp_path):
    write(tmp_path, "configuration.yaml", "script: !include_dir_merge_named scripts\n")
    write(tmp_path, "scripts/pack.yaml", "one:\n  sequence: []\ntwo:\n  sequence: []\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "script")
    loc = yi.locate_entry(layout, "two")
    assert loc.ref_kind == "named_value" and loc.key == "two"
    assert (loc.start_line, loc.end_line) == (2, 4)


def test_locate_list_item_include_reference(tmp_path):
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n- !include extra.yaml\n")
    write(tmp_path, "extra.yaml", "id: bbb\nalias: B\n")
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    loc = yi.locate_entry(layout, "bbb")
    assert loc.ref_kind == "list_item"
    assert loc.ref_file.endswith("automations.yaml")
    assert loc.content_file.endswith("extra.yaml")
    assert (loc.start_line, loc.end_line) == (2, 3)


def test_locate_named_value_include_reference(tmp_path):
    write(tmp_path, "configuration.yaml", "script: !include scripts.yaml\n")
    write(tmp_path, "scripts.yaml", "hello: !include hello.yaml\n")
    write(tmp_path, "hello.yaml", "alias: Hello\nsequence: []\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "script")
    loc = yi.locate_entry(layout, "hello")
    assert loc.ref_kind == "named_value"
    assert loc.content_file.endswith("hello.yaml")
    assert loc.config == {"alias": "Hello", "sequence": []}


def test_locate_whole_document_include_chain(tmp_path):
    write(tmp_path, "automations.yaml", "!include real.yaml\n")
    write(tmp_path, "real.yaml", "- id: aaa\n  alias: A\n")
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    loc = yi.locate_entry(layout, "aaa")
    assert loc.ref_file.endswith("real.yaml") and loc.ref_kind == "list_item"


def test_locate_duplicate_across_files(tmp_path):
    write(tmp_path, "automations/a.yaml", "- id: dup\n  alias: A\n")
    write(tmp_path, "automations/b.yaml", "- id: dup\n  alias: B\n")
    layout = _automation_layout(tmp_path, "automation: !include_dir_merge_list automations\n")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "dup")
    assert exc.value.reason == "duplicate"
    assert "a.yaml" in exc.value.message and "b.yaml" in exc.value.message


def test_locate_not_found_vs_ambiguous(tmp_path):
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n")
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "ghost")
    assert exc.value.reason == "not_found"
    assert exc.value.message == "No automation found with id 'ghost'."

    layout = _automation_layout(tmp_path, """\
        homeassistant:
          packages: {}
        automation: !include automations.yaml
    """)
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "ghost")
    assert exc.value.reason == "ambiguous"


def test_locate_include_cycle(tmp_path):
    write(tmp_path, "automations.yaml", "!include loop.yaml\n")
    write(tmp_path, "loop.yaml", "!include automations.yaml\n")
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "aaa")
    assert exc.value.reason == "unresolvable" and "cycle" in exc.value.message


def test_locate_depth_cap(tmp_path):
    write(tmp_path, "automations.yaml", "!include chain0.yaml\n")
    for i in range(10):
        write(tmp_path, f"chain{i}.yaml", f"!include chain{i + 1}.yaml\n")
    write(tmp_path, "chain10.yaml", "- id: aaa\n")
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "aaa")
    assert exc.value.reason == "unresolvable" and "levels of includes" in exc.value.message


def test_locate_nested_containment_escape(tmp_path):
    write(tmp_path, "automations.yaml", "- !include ../../outside.yaml\n")
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "aaa")
    assert exc.value.reason == "unresolvable"
    assert exc.value.message == yi.MSG_OUTSIDE_CONFIG.format(domain="automation")


@pytest.mark.parametrize("flavor", ["!include_dir_merge_list", "!include_dir_list"])
def test_symlinked_dir_leaf_outside_config_refused(tmp_path, flavor):
    # A symlinked leaf inside an include dir resolves outside the config dir;
    # containment must hold for the resolved file, not the enumerated path.
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside, "leak.yaml", "- id: aaa\n  alias: Leaked\n" if flavor.endswith("merge_list") else "id: aaa\nalias: Leaked\n")
    config = tmp_path / "config"
    write(config, "automations/.keep.yaml", "")
    os.symlink(outside / "leak.yaml", config / "automations" / "leak.yaml")
    write(config, "configuration.yaml", f"automation: {flavor} automations\n")
    layout = yi.resolve_domain_layout(str(config), "automation")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "aaa")
    assert exc.value.reason == "unresolvable"
    assert exc.value.message == yi.MSG_OUTSIDE_CONFIG.format(domain="automation")
    assert yi.read_entry(str(config), "automation", "aaa") is None
    res = yi.perform_edit(str(config), "automation", "aaa", {"id": "aaa", "alias": "X"})
    assert not res.ok and not res.fallback and res.error_kind == "refused"
    assert "Leaked" in read(outside, "leak.yaml")


@pytest.mark.parametrize("flavor", ["!include_dir_named", "!include_dir_merge_named"])
def test_symlinked_named_dir_leaf_outside_config_refused(tmp_path, flavor):
    # Named directory flavors share the same containment guard as list flavors.
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_text = (
        "hello:\n  alias: Leaked\n  sequence: []\n"
        if flavor.endswith("merge_named")
        else "alias: Leaked\nsequence: []\n"
    )
    write(outside, "hello.yaml", outside_text)
    config = tmp_path / "config"
    write(config, "scripts/.keep.yaml", "")
    os.symlink(outside / "hello.yaml", config / "scripts" / "hello.yaml")
    write(config, "configuration.yaml", f"script: {flavor} scripts\n")

    layout = yi.resolve_domain_layout(str(config), "script")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "hello")
    assert exc.value.reason == "unresolvable"
    assert exc.value.message == yi.MSG_OUTSIDE_CONFIG.format(domain="script")
    assert yi.read_entry(str(config), "script", "hello") is None
    res = yi.perform_edit(str(config), "script", "hello", {"alias": "X", "sequence": []})
    assert not res.ok and not res.fallback and res.error_kind == "refused"
    assert read(outside, "hello.yaml") == outside_text


def test_symlinked_leaf_inside_config_still_locates(tmp_path):
    # A symlink whose target resolves INSIDE the config dir stays readable.
    write(tmp_path, "shared/common.yaml", "- id: aaa\n  alias: Shared\n")
    (tmp_path / "automations").mkdir()
    os.symlink(tmp_path / "shared" / "common.yaml", tmp_path / "automations" / "common.yaml")
    layout = _automation_layout(tmp_path, "automation: !include_dir_merge_list automations\n")
    loc = yi.locate_entry(layout, "aaa")
    assert loc.config == {"id": "aaa", "alias": "Shared"}


def test_locate_scene_id_str_coercion(tmp_path):
    write(tmp_path, "configuration.yaml", "scene: !include scenes.yaml\n")
    write(tmp_path, "scenes.yaml", "- id: 5\n  name: Numeric\n  entities: {}\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "scene")
    loc = yi.locate_entry(layout, "5")
    assert loc.config["name"] == "Numeric"


def test_locate_missing_plain_target_is_not_found(tmp_path):
    layout = _automation_layout(tmp_path, "automation: !include automations.yaml\n")
    with pytest.raises(yi.LocateError) as exc:
        yi.locate_entry(layout, "aaa")
    assert exc.value.reason == "not_found"


# ---------------------------------------------------------------------------
# Edit / delete via splice
# ---------------------------------------------------------------------------


SPLICE_FILE = """\
    # top of file
    - id: aaa
      alias: First
    # bbb section
    - id: bbb
      alias: Second
      password: !secret db_pass

    # trailing notes
"""


def test_edit_preserves_untouched_bytes(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", SPLICE_FILE)
    res = yi.perform_edit(str(tmp_path), "automation", "aaa",
                          {"id": "aaa", "alias": "First v2"})
    assert res.ok
    assert read(tmp_path, "automations.yaml") == textwrap.dedent("""\
        # top of file
        - id: aaa
          alias: First v2
        # bbb section
        - id: bbb
          alias: Second
          password: !secret db_pass

        # trailing notes
    """)
    assert res.before == {"id": "aaa", "alias": "First"}


def test_edit_secret_bearing_entry_allowed_and_encoded(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", SPLICE_FILE)
    res = yi.perform_edit(str(tmp_path), "automation", "bbb",
                          {"id": "bbb", "alias": "Second v2"})
    assert res.ok
    assert res.before == {"id": "bbb", "alias": "Second", "password": "!secret db_pass"}
    text = read(tmp_path, "automations.yaml")
    assert "!secret" not in text  # the replaced entry dropped the reference
    assert "# top of file" in text and "# trailing notes" in text


def test_delete_preserves_heading_and_trailing_comments(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", SPLICE_FILE)
    res = yi.perform_delete(str(tmp_path), "automation", "aaa")
    assert res.ok
    assert read(tmp_path, "automations.yaml") == textwrap.dedent("""\
        # top of file
        # bbb section
        - id: bbb
          alias: Second
          password: !secret db_pass

        # trailing notes
    """)


def test_delete_expected_hash_is_checked_against_located_entry(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", SPLICE_FILE)
    original = read(tmp_path, "automations.yaml")

    res = yi.perform_delete(
        str(tmp_path), "automation", "aaa",
        content_hash({"id": "aaa", "alias": "Previously reviewed"}),
    )

    assert not res.ok
    assert res.error_kind == "conflict"
    assert "expected_hash no longer matches" in res.error_text
    assert read(tmp_path, "automations.yaml") == original


def test_delete_last_entry_keeps_trailing_comments(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", SPLICE_FILE)
    res = yi.perform_delete(str(tmp_path), "automation", "bbb")
    assert res.ok
    text = read(tmp_path, "automations.yaml")
    # The "# bbb section" heading comment survives by design (comments are
    # never deleted); the entry itself is gone.
    assert "# trailing notes" in text and "- id: bbb" not in text
    assert yaml.load(text, Loader=yi.PhoenixSafeLoader) == [{"id": "aaa", "alias": "First"}]


def test_delete_block_scalar_with_comment_lines_never_corrupts(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", """\
        - id: aaa
          alias: A
          message: |
            hello
            # not a comment
        - id: bbb
          alias: B
    """)
    res = yi.perform_delete(str(tmp_path), "automation", "aaa")
    assert res.ok
    remaining = yaml.load(read(tmp_path, "automations.yaml"), Loader=yi.PhoenixSafeLoader)
    assert remaining == [{"id": "bbb", "alias": "B"}]


def test_edit_file_without_trailing_newline(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    (tmp_path / "automations.yaml").write_text(
        "- id: aaa\n  alias: A\n- id: bbb\n  alias: B", encoding="utf-8")
    res = yi.perform_edit(str(tmp_path), "automation", "bbb", {"id": "bbb", "alias": "B2"})
    assert res.ok
    entries = yaml.load(read(tmp_path, "automations.yaml"), Loader=yi.PhoenixSafeLoader)
    assert entries == [{"id": "aaa", "alias": "A"}, {"id": "bbb", "alias": "B2"}]


def test_edit_refuses_entry_with_internal_include(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n  action: !include actions.yaml\n")
    res = yi.perform_edit(str(tmp_path), "automation", "aaa", {"id": "aaa", "alias": "A2"})
    assert not res.ok and res.error_kind == "refused"
    assert res.message == yi.MSG_INCLUDE_REFUSAL.format(name="automations.yaml")
    # Delete of the same entry is allowed (line removal cannot corrupt).
    res = yi.perform_delete(str(tmp_path), "automation", "aaa")
    assert res.ok


def snapshot(base: Path) -> dict[str, bytes]:
    """Every file under the config tree, by relative path, for unchanged-bytes checks."""
    return {
        str(p.relative_to(base)): p.read_bytes()
        for p in sorted(base.rglob("*")) if p.is_file()
    }


def hand_written(domain: str, editor: str, name: str) -> str:
    return yi.MSG_HAND_WRITTEN.format(domain=domain, name=name, editor=editor)


def test_body_file_entry_is_refused_beside_an_inline_one(tmp_path):
    """A body behind a plain !include is hand-written even when the editor file references it.

    The inline entry next to the reference stays the editor's to change, and the
    reference line survives both the refusal and the neighboring write.
    """
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n- !include extra.yaml\n")
    write(tmp_path, "extra.yaml", "id: bbb\nalias: B\n")
    before = snapshot(tmp_path)
    for res in (
        yi.perform_edit(str(tmp_path), "automation", "bbb", {"id": "bbb", "alias": "B2"}),
        yi.perform_delete(str(tmp_path), "automation", "bbb"),
    ):
        assert not res.ok and res.error_kind == "refused"
        assert res.message == hand_written("automation", "automations.yaml", "extra.yaml")
    assert snapshot(tmp_path) == before

    res = yi.perform_edit(str(tmp_path), "automation", "aaa", {"id": "aaa", "alias": "A2"})
    assert res.ok and res.file.endswith("automations.yaml")
    assert read(tmp_path, "automations.yaml") == "- id: aaa\n  alias: A2\n- !include extra.yaml\n"
    res = yi.perform_delete(str(tmp_path), "automation", "aaa")
    assert res.ok
    assert read(tmp_path, "automations.yaml") == "- !include extra.yaml\n"
    assert read(tmp_path, "extra.yaml") == "id: bbb\nalias: B\n"


def test_whole_file_entry_is_refused_and_never_unlinked(tmp_path):
    """A !include_dir_named file holding one script whole is hand-written YAML."""
    write(tmp_path, "configuration.yaml",
          "script: !include scripts.yaml\nscript manual: !include_dir_named scripts\n")
    write(tmp_path, "scripts.yaml", "ui:\n  sequence: []\n")
    write(tmp_path, "scripts/hello.yaml", "alias: Hello\nsequence: []\n")
    before = snapshot(tmp_path)
    for res in (
        yi.perform_edit(str(tmp_path), "script", "hello", {"alias": "H2", "sequence": []}),
        yi.perform_delete(str(tmp_path), "script", "hello"),
    ):
        assert not res.ok and res.error_kind == "refused"
        assert res.message == hand_written("script", "scripts.yaml", "hello.yaml")
    assert snapshot(tmp_path) == before

    res = yi.perform_delete(str(tmp_path), "script", "ui")
    assert res.ok and res.file.endswith("scripts.yaml")
    assert yi.read_entry(str(tmp_path), "script", "ui") is None
    assert read(tmp_path, "scripts/hello.yaml") == "alias: Hello\nsequence: []\n"


_SHAPE_TEXT = {
    # (inline list/mapping entry text, whole-file body text) per domain.
    "automation": ("- id: {id}\n  alias: {label}\n", "id: {id}\nalias: {label}\n"),
    "scene": ("- id: {id}\n  name: {label}\n  entities: {{}}\n", "id: {id}\nname: {label}\nentities: {{}}\n"),
    "script": ("{id}:\n  alias: {label}\n  sequence: []\n", "alias: {label}\nsequence: []\n"),
}
_EDITOR = {"automation": "automations.yaml", "scene": "scenes.yaml", "script": "scripts.yaml"}
_MERGE_DIR = {"automation": "!include_dir_merge_list", "scene": "!include_dir_merge_list", "script": "!include_dir_merge_named"}
_WHOLE_DIR = {"automation": "!include_dir_list", "scene": "!include_dir_list", "script": "!include_dir_named"}


def _seed_hand_written(base: Path, domain: str, flavor: str) -> str:
    """Seed an editor-file entry "ui" and a hand-written entry "hand"; return the file named."""
    entry, whole = _SHAPE_TEXT[domain]
    editor = _EDITOR[domain]
    ui = entry.format(id="ui", label="UI")
    hand = entry.format(id="hand", label="Hand")
    body = whole.format(id="hand", label="Hand")
    config = f"{domain}: !include {editor}\n"
    write(base, editor, ui)
    if flavor == "split_file":
        write(base, "configuration.yaml", config + f"{domain} manual: !include manual.yaml\n")
        write(base, "manual.yaml", hand)
        return "manual.yaml"
    if flavor == "merge_dir":
        write(base, "configuration.yaml", config + f"{domain} manual: {_MERGE_DIR[domain]} manual\n")
        write(base, "manual/pack.yaml", hand)
        return "pack.yaml"
    if flavor == "whole_file":
        write(base, "configuration.yaml", config + f"{domain} manual: {_WHOLE_DIR[domain]} manual\n")
        write(base, "manual/hand.yaml", body)
        return "hand.yaml"
    assert flavor == "body_file"
    write(base, "configuration.yaml", config)
    reference = "hand: !include body.yaml\n" if domain == "script" else "- !include body.yaml\n"
    write(base, editor, ui + reference)
    write(base, "body.yaml", body)
    return "body.yaml"


def _changed(domain: str, entry_id: str) -> dict:
    if domain == "automation":
        return {"id": entry_id, "alias": "Changed"}
    if domain == "scene":
        return {"id": entry_id, "name": "Changed", "entities": {}}
    return {"alias": "Changed", "sequence": []}


@pytest.mark.parametrize("domain", ["automation", "script", "scene"])
@pytest.mark.parametrize("flavor", ["split_file", "merge_dir", "whole_file", "body_file"])
def test_write_paths_refuse_hand_written_entries(tmp_path, domain, flavor):
    """Every write path and the pre-approval check refuse the same entry with the same message.

    The positive control in the same tree shows the editor-file entry is still
    writable and that writing it leaves every hand-written file byte-identical.
    """
    name = _seed_hand_written(tmp_path, domain, flavor)
    editor = _EDITOR[domain]
    expected = hand_written(domain, editor, name)
    hand_config = yi.read_entry(str(tmp_path), domain, "hand")
    assert hand_config is not None  # reads still follow every include
    before = snapshot(tmp_path)

    precheck = yi.write_target_refusal(str(tmp_path), domain, "hand", create=False)
    assert precheck is not None and precheck.error_kind == "refused"
    assert precheck.message == expected
    results = [
        yi.perform_edit(str(tmp_path), domain, "hand", _changed(domain, "hand")),
        yi.perform_delete(str(tmp_path), domain, "hand"),
    ]
    if domain == "automation":
        current = content_hash(yi.read_entry(str(tmp_path), "automation", "hand"))
        for dry_run in (True, False):
            results.append(yi.automation_write(
                str(tmp_path), "hand", _changed(domain, "hand"), False, current, dry_run))
    for res in results:
        assert not res.ok and not res.fallback and res.error_kind == "refused"
        assert res.message == expected
    assert snapshot(tmp_path) == before

    assert yi.write_target_refusal(str(tmp_path), domain, "ui", create=False) is None
    res = yi.perform_edit(str(tmp_path), domain, "ui", _changed(domain, "ui"))
    assert res.ok, res.message
    after = snapshot(tmp_path)
    assert {path for path in after if after[path] != before[path]} == {editor}
    assert yi.read_entry(str(tmp_path), domain, "hand") == hand_config


@pytest.mark.parametrize("configuration", [
    "automation: !include automations.yaml\nautomation manual:\n  - id: hand\n    alias: Hand\n",
    "homeassistant:\n  packages: {}\nautomation: !include automations.yaml\n",
])
def test_inline_and_package_entries_are_never_written(tmp_path, configuration):
    """Entries the locator does not read are not found for writing, so nothing is spliced.

    configuration.yaml itself is never a write target, whatever it defines inline.
    """
    write(tmp_path, "configuration.yaml", configuration)
    write(tmp_path, "automations.yaml", "- id: ui\n  alias: UI\n")
    before = snapshot(tmp_path)
    for res in (
        yi.perform_edit(str(tmp_path), "automation", "hand", {"id": "hand", "alias": "Changed"}),
        yi.perform_delete(str(tmp_path), "automation", "hand"),
        yi.automation_write(str(tmp_path), "hand", {"id": "hand", "alias": "Changed"}, False),
    ):
        assert not res.ok and res.error_kind == "ambiguous"
        assert res.message == yi.MSG_AMBIGUOUS.format(domain="automation", entry_id="hand")
    assert snapshot(tmp_path) == before


def test_write_target_refusal_leaves_missing_entries_to_existence_checks(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n")
    assert yi.write_target_refusal(str(tmp_path), "automation", "ghost", create=False) is None
    assert yi.write_target_refusal(str(tmp_path), "automation", "aaa", create=False) is None


def test_write_target_refusal_falls_back_without_configuration_yaml(tmp_path):
    """Without configuration.yaml the legacy path writes the editor file directly."""
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n")
    for create in (True, False):
        assert yi.write_target_refusal(str(tmp_path), "automation", "aaa", create=create) is None


def test_write_target_refusal_reports_a_layout_error(tmp_path):
    write(tmp_path, "configuration.yaml", "automation: !include ../outside.yaml\n")
    for create in (True, False):
        res = yi.write_target_refusal(str(tmp_path), "automation", "aaa", create=create)
        assert res is not None and res.error_kind == "refused"
        assert res.message == yi.MSG_OUTSIDE_CONFIG.format(domain="automation")


def test_write_target_refusal_reports_an_unreadable_leaf(tmp_path):
    """A leaf that cannot be decoded is a refusal to read, never an exception or a pass."""
    write(tmp_path, "configuration.yaml",
          "automation: !include automations.yaml\nautomation manual: !include_dir_merge_list manual\n")
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n")
    (tmp_path / "manual").mkdir()
    (tmp_path / "manual" / "broken.yaml").write_bytes(b"- id: \xff\xfe\n")
    res = yi.write_target_refusal(str(tmp_path), "automation", "aaa", create=False)
    assert res is not None and res.error_kind == "io_error"
    assert "Cannot safely read the automation configuration" in res.error_text


def test_self_check_aborts_on_bad_span(tmp_path):
    """A corrupted span must refuse with io_error and write nothing."""
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n- id: bbb\n  alias: B\n")
    original = read(tmp_path, "automations.yaml")
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    loc = yi.locate_entry(layout, "aaa")
    # Simulate span-math gone wrong: the span eats half of bbb too.
    broken = yi.LocatedEntry(
        ref_file=loc.ref_file, ref_kind=loc.ref_kind, ref_text=loc.ref_text,
        start_line=loc.start_line, end_line=loc.end_line + 1,
        key=None, config=loc.config,
    )
    res = yi._write_entry_files(broken, "automation", {"id": "aaa", "alias": "A2"}, str(tmp_path))
    assert not res.ok and res.error_kind == "io_error"
    assert read(tmp_path, "automations.yaml") == original


# ---------------------------------------------------------------------------
# Create routing
# ---------------------------------------------------------------------------


def test_create_append_plain_include(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", "- id: aaa\n  alias: A\n")
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_x", {"id": "phoenix_mcp_x", "alias": "X"})
    assert res.ok
    assert read(tmp_path, "automations.yaml") == (
        "- id: aaa\n  alias: A\n- id: phoenix_mcp_x\n  alias: X\n")


def test_create_append_into_bare_empty_list(tmp_path):
    """HA's default empty automations.yaml is a bare inline '[]'.

    A text append after it would produce invalid YAML, so the entry replaces the access key.
    """
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", "[]\n")
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_x", {"id": "phoenix_mcp_x", "alias": "X"})
    assert res.ok, res.message
    text = read(tmp_path, "automations.yaml")
    assert "[]" not in text
    assert yaml.safe_load(text) == [{"id": "phoenix_mcp_x", "alias": "X"}]


def test_create_append_into_bare_empty_dict_scripts(tmp_path):
    """scripts.yaml default is a bare '{}'."""
    write(tmp_path, "configuration.yaml", "script: !include scripts.yaml\n")
    write(tmp_path, "scripts.yaml", "{}\n")
    res = yi.perform_create(str(tmp_path), "script", "hello", {"sequence": []})
    assert res.ok, res.message
    text = read(tmp_path, "scripts.yaml")
    assert "{}" not in text
    assert yaml.safe_load(text) == {"hello": {"sequence": []}}


def test_create_append_bare_list_preserves_comments(tmp_path):
    """A comments-only-plus-'[]' file keeps its comments; only the access key is dropped."""
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    write(tmp_path, "automations.yaml", "# my automations\n[]\n")
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_x", {"id": "phoenix_mcp_x", "alias": "X"})
    assert res.ok, res.message
    text = read(tmp_path, "automations.yaml")
    assert text.startswith("# my automations\n")
    assert yaml.safe_load(text) == [{"id": "phoenix_mcp_x", "alias": "X"}]


def test_create_plain_include_missing_file(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_x", {"id": "phoenix_mcp_x", "alias": "X"})
    assert res.ok
    assert read(tmp_path, "automations.yaml") == "- id: phoenix_mcp_x\n  alias: X\n"


def test_create_append_no_trailing_newline(tmp_path):
    write(tmp_path, "configuration.yaml", DEFAULT_CONFIGURATION)
    (tmp_path / "automations.yaml").write_text("- id: aaa\n  alias: A", encoding="utf-8")
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_x", {"id": "phoenix_mcp_x", "alias": "X"})
    assert res.ok
    assert read(tmp_path, "automations.yaml") == (
        "- id: aaa\n  alias: A\n- id: phoenix_mcp_x\n  alias: X\n")


def test_create_beside_a_directory_goes_to_the_editor_file(tmp_path):
    write(tmp_path, "configuration.yaml", """\
        automation: !include automations.yaml
        automation manual: !include_dir_merge_list automations
    """)
    write(tmp_path, "automations.yaml", "")
    (tmp_path / "automations").mkdir()
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_x", {"id": "phoenix_mcp_x", "alias": "X"})
    assert res.ok and res.file.endswith("automations.yaml")
    assert list((tmp_path / "automations").iterdir()) == []


def test_create_through_a_labeled_editor_include(tmp_path):
    write(tmp_path, "configuration.yaml", "script ui: !include scripts.yaml\n")
    res = yi.perform_create(str(tmp_path), "script", "hello", {"sequence": []})
    assert res.ok and res.file.endswith("scripts.yaml")
    assert yaml.safe_load(read(tmp_path, "scripts.yaml")) == {"hello": {"sequence": []}}


@pytest.mark.parametrize("domain,configuration,directory", [
    pytest.param("automation", "automation: !include_dir_list automations\n", "automations", id="dir-list"),
    pytest.param("automation", "automation: !include_dir_merge_list automations\n", "automations", id="dir-merge-list"),
    pytest.param("script", "script: !include_dir_named scripts\n", "scripts", id="dir-named"),
    pytest.param("script", "script: !include_dir_merge_named scripts\n", "scripts", id="dir-merge-named"),
    pytest.param("automation", "automation a: !include_dir_merge_list one\nautomation b: !include_dir_merge_list two\n",
                 "one", id="two-directories"),
    pytest.param("automation", "automation: !include_dir_merge_list automations\n", None, id="missing-directory"),
    pytest.param("scene", "scene: !include my_scenes.yaml\n", None, id="another-file"),
    pytest.param("scene", "scene:\n  - id: s\n    name: S\n    entities: {}\n", None, id="inline"),
    pytest.param("script", "homeassistant:\n  packages: {}\n", None, id="packages-only"),
    pytest.param("automation", "sensor: !include sensors.yaml\n", None, id="unrouted"),
])
def test_create_refuses_without_the_editor_file(tmp_path, domain, configuration, directory):
    """A create goes only to the editor file a plain !include in configuration.yaml loads.

    No file is created anywhere else, the editor file is not created behind
    configuration.yaml's back, and the pre-approval check gives the same answer.
    """
    write(tmp_path, "configuration.yaml", configuration)
    if directory:
        (tmp_path / directory).mkdir()
    before = snapshot(tmp_path)
    expected = yi.MSG_CREATE_NO_ROUTE.format(domain=domain, editor=_EDITOR[domain])
    config = {
        "automation": {"id": "phoenix_mcp_x", "alias": "X"},
        "scene": {"id": "phoenix_mcp_x", "name": "X", "entities": {}},
        "script": {"sequence": []},
    }[domain]
    results = [
        yi.write_target_refusal(str(tmp_path), domain, "phoenix_mcp_x", create=True),
        yi.perform_create(str(tmp_path), domain, "phoenix_mcp_x", config),
    ]
    if domain == "automation":
        results.append(yi.automation_write(
            str(tmp_path), "phoenix_mcp_x", {"id": "phoenix_mcp_x", "alias": "X"}, True, None, False, True))
    for res in results:
        assert res is not None and not res.ok and res.error_kind == "refused"
        assert res.message == expected
    assert snapshot(tmp_path) == before
    if directory:
        assert list((tmp_path / directory).iterdir()) == []


def test_create_script_proceeds_when_packages_present(tmp_path):
    """A config with homeassistant.packages does not block creating a brand-new script.

    locate returns 'ambiguous', which create treats as proceed.
    """
    write(tmp_path, "configuration.yaml", """\
        script: !include scripts.yaml
        homeassistant:
          packages: !include_dir_named packages
    """)
    write(tmp_path, "scripts.yaml", "{}\n")
    (tmp_path / "packages").mkdir()
    res = yi.perform_create(str(tmp_path), "script", "phoenix_mcp_new", {"sequence": []})
    assert res.ok, res.message
    assert yaml.safe_load(read(tmp_path, "scripts.yaml")) == {"phoenix_mcp_new": {"sequence": []}}


def test_create_script_duplicate_across_leaves_refuses(tmp_path):
    """If the id exists in two leaves, create refuses as already_exists."""
    write(tmp_path, "configuration.yaml", "script: !include_dir_merge_named scripts\n")
    write(tmp_path, "scripts/a.yaml", "hello:\n  sequence: []\n")
    write(tmp_path, "scripts/b.yaml", "hello:\n  sequence: []\n")
    res = yi.perform_create(str(tmp_path), "script", "hello", {"sequence": []})
    assert not res.ok and res.error_kind == "already_exists"


def test_create_script_duplicate_graph_wide(tmp_path):
    write(tmp_path, "configuration.yaml", "script: !include_dir_merge_named scripts\n")
    write(tmp_path, "scripts/pack.yaml", "hello:\n  sequence: []\n")
    res = yi.perform_create(str(tmp_path), "script", "hello", {"sequence": []})
    assert not res.ok and res.error_kind == "already_exists"
    assert "already exists" in res.message


def test_create_append_refuses_wrong_shape(tmp_path):
    write(tmp_path, "configuration.yaml", "automation: !include automations.yaml\n")
    write(tmp_path, "automations.yaml", "some: mapping\n")
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_x", {"id": "phoenix_mcp_x"})
    assert not res.ok and res.error_kind == "refused"


# ---------------------------------------------------------------------------
# Fallback and layout-error propagation
# ---------------------------------------------------------------------------


def test_perform_fallback_without_configuration_yaml(tmp_path):
    for fn in (
        lambda: yi.perform_create(str(tmp_path), "automation", "x", {"id": "x"}),
        lambda: yi.perform_edit(str(tmp_path), "automation", "x", {"id": "x"}),
        lambda: yi.perform_delete(str(tmp_path), "automation", "x"),
    ):
        res = fn()
        assert not res.ok and res.fallback


def test_perform_layout_error_refuses(tmp_path):
    write(tmp_path, "configuration.yaml", "automation: !include ../outside.yaml\n")
    res = yi.perform_edit(str(tmp_path), "automation", "x", {"id": "x"})
    assert not res.ok and not res.fallback and res.error_kind == "refused"
    assert res.message == yi.MSG_OUTSIDE_CONFIG.format(domain="automation")


# ---------------------------------------------------------------------------
# read_entry
# ---------------------------------------------------------------------------


def test_read_entry_routed_and_absent(tmp_path):
    write(tmp_path, "configuration.yaml", "automation: !include_dir_merge_list automations\n")
    write(tmp_path, "automations/pack.yaml", "- id: aaa\n  alias: A\n  password: !secret p\n")
    assert yi.read_entry(str(tmp_path), "automation", "aaa") == {
        "id": "aaa", "alias": "A", "password": "!secret p"}
    assert yi.read_entry(str(tmp_path), "automation", "ghost") is None


def test_read_entry_fallback_tolerates_secrets(tmp_path):
    # No configuration.yaml: fall back to the hardcoded default file, read with the
    # tag-tolerant loader, which must not raise on !secret.
    write(tmp_path, "automations.yaml", "- id: aaa\n  password: !secret p\n")
    assert yi.read_entry(str(tmp_path), "automation", "aaa") == {
        "id": "aaa", "password": "!secret p"}
    write(tmp_path, "scripts.yaml", "hello:\n  sequence: []\n")
    assert yi.read_entry(str(tmp_path), "script", "hello") == {"sequence": []}
    assert yi.read_entry(str(tmp_path), "script", "ghost") is None


def test_read_entry_layout_error_is_authoritative_no_fallback(tmp_path):
    # configuration.yaml parsed but the layout was refused (routed outside the
    # config dir): read_entry must NOT fall back to a stale default file, or
    # diffs and existence checks would desync from what the write paths edit.
    write(tmp_path, "configuration.yaml", "automation: !include ../outside.yaml\n")
    write(tmp_path, "automations.yaml", "- id: stale\n  alias: Stale\n")
    layout = yi.resolve_domain_layout(str(tmp_path), "automation")
    assert layout is not None and layout.error
    assert yi.read_entry(str(tmp_path), "automation", "stale") is None


# ---------------------------------------------------------------------------
# Oracle: Phoenix MCP's include semantics against HA's own loader
# ---------------------------------------------------------------------------


def test_oracle_against_ha_loader(tmp_path):
    from homeassistant.util.yaml import Secrets, load_yaml

    write(tmp_path, "configuration.yaml", """\
        automation: !include automations.yaml
        automation manual: !include_dir_merge_list automations
        script: !include_dir_merge_named scripts
    """)
    write(tmp_path, "secrets.yaml", "p: hunter2\n")
    write(tmp_path, "automations.yaml", "- id: ui\n  alias: UI\n")
    write(tmp_path, "automations/a.yaml", "- id: one\n  alias: One\n")
    write(tmp_path, "automations/b.yaml", "- id: two\n  alias: Two\n  password: !secret p\n")
    write(tmp_path, "automations/.hidden.yaml", "- id: hidden\n")
    write(tmp_path, "scripts/pack.yaml", "hello:\n  sequence: []\n")

    def ha_automations() -> dict:
        resolved = load_yaml(str(tmp_path / "configuration.yaml"), Secrets(tmp_path))
        merged = list(resolved["automation"]) + list(resolved["automation manual"])
        return {a["id"]: a for a in merged}

    resolved = load_yaml(str(tmp_path / "configuration.yaml"), Secrets(tmp_path))
    ha_script_ids = list(resolved["script"])

    # Every entry HA resolves is locatable by Phoenix MCP, and nothing more.
    for entry_id in ha_automations():
        assert yi.read_entry(str(tmp_path), "automation", entry_id) is not None
    assert yi.read_entry(str(tmp_path), "automation", "hidden") is None
    for entry_id in ha_script_ids:
        assert yi.read_entry(str(tmp_path), "script", entry_id) is not None

    # A routed create lands in the editor file and is visible to HA's loader.
    res = yi.perform_create(str(tmp_path), "automation", "phoenix_mcp_new", {"id": "phoenix_mcp_new", "alias": "New"})
    assert res.ok and res.file.endswith("automations.yaml")
    assert "phoenix_mcp_new" in ha_automations()

    # A splice edit is invisible to HA's loader except for the intended change.
    res = yi.perform_edit(str(tmp_path), "automation", "ui", {"id": "ui", "alias": "UI v2"})
    assert res.ok
    by_id = ha_automations()
    assert by_id["ui"]["alias"] == "UI v2"
    assert by_id["two"] == {"id": "two", "alias": "Two", "password": "hunter2"}

    # A hand-written entry HA loads is refused, and HA still loads it unchanged.
    res = yi.perform_edit(str(tmp_path), "automation", "one", {"id": "one", "alias": "One v2"})
    assert not res.ok and res.message == hand_written("automation", "automations.yaml", "a.yaml")
    assert ha_automations()["one"] == {"id": "one", "alias": "One"}


# ---------------------------------------------------------------------------
# read_all_entries: the READ counterpart to locate_entry
# ---------------------------------------------------------------------------
#
# The reverse-reference walks behind get_relationships and describe_entity read
# through read_all_entries. A layout other than HA's default must not read as
# EMPTY: the caller would report that nothing references the entity, which is
# indistinguishable from a genuinely unused one and the worst answer to "is
# anything still using this". These cover every flavor HA accepts, plus the
# branches that stay unreadable and must therefore be NAMED.


def _seed(tmp_path: Path, config: str) -> Path:
    write(tmp_path, "configuration.yaml", config)
    return tmp_path


def test_read_all_default_include(tmp_path):
    _seed(tmp_path, "automation: !include automations.yaml\n")
    write(tmp_path, "automations.yaml", """\
        - id: a1
          alias: One
        - id: a2
          alias: Two
        """)
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert [e["id"] for e in found.entries] == ["a1", "a2"]
    assert found.unreadable == ()


def test_read_all_include_dir_list_is_one_entry_per_file(tmp_path):
    """With !include_dir_list each file IS one automation."""
    _seed(tmp_path, "automation: !include_dir_list automations/\n")
    write(tmp_path, "automations/first.yaml", "id: a1\nalias: One\n")
    write(tmp_path, "automations/second.yaml", "id: a2\nalias: Two\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert sorted(e["id"] for e in found.entries) == ["a1", "a2"]
    assert found.unreadable == ()


def test_read_all_include_dir_merge_list_is_a_list_per_file(tmp_path):
    _seed(tmp_path, "automation: !include_dir_merge_list automations/\n")
    write(tmp_path, "automations/pack.yaml", "- id: a1\n- id: a2\n")
    write(tmp_path, "automations/more.yaml", "- id: a3\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert sorted(e["id"] for e in found.entries) == ["a1", "a2", "a3"]


def test_read_all_include_dir_named_keys_by_filename(tmp_path):
    _seed(tmp_path, "script: !include_dir_named scripts/\n")
    write(tmp_path, "scripts/greet.yaml", "alias: Greet\nsequence: []\n")
    write(tmp_path, "scripts/leave.yaml", "alias: Leave\nsequence: []\n")
    found = yi.read_all_entries(str(tmp_path), "script")
    assert set(found.entries) == {"greet", "leave"}
    assert found.entries["greet"]["alias"] == "Greet"


def test_read_all_include_dir_merge_named_is_a_mapping_per_file(tmp_path):
    _seed(tmp_path, "script: !include_dir_merge_named scripts/\n")
    write(tmp_path, "scripts/pack.yaml", "greet:\n  alias: Greet\nleave:\n  alias: Leave\n")
    found = yi.read_all_entries(str(tmp_path), "script")
    assert set(found.entries) == {"greet", "leave"}


def test_read_all_reads_an_inline_domain(tmp_path):
    """An inline domain is readable here, unlike on the WRITE path.

    Writes refuse inline as ambiguous: reading it is unambiguous, splicing into it is not.
    """
    _seed(tmp_path, "automation:\n  - id: a1\n    alias: Inline\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert [e["alias"] for e in found.entries] == ["Inline"]


def test_read_all_merges_labeled_keys_with_the_bare_one(tmp_path):
    """HA allows `automation manual:` alongside `automation:`."""
    _seed(tmp_path, "automation: !include automations.yaml\n"
                    "automation manual:\n  - id: m1\n")
    write(tmp_path, "automations.yaml", "- id: a1\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert sorted(e["id"] for e in found.entries) == ["a1", "m1"]


def test_read_all_names_packages_rather_than_guessing(tmp_path):
    """HA merges packages into the domain; resolving that tree is not attempted.

    The caller is told its answer is incomplete instead.
    """
    _seed(tmp_path, "homeassistant:\n  packages: !include_dir_named packages/\n"
                    "automation: !include automations.yaml\n")
    write(tmp_path, "automations.yaml", "- id: a1\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert [e["id"] for e in found.entries] == ["a1"]
    assert any("packages" in r for r in found.unreadable)


def test_read_all_names_a_leaf_it_could_not_parse(tmp_path):
    _seed(tmp_path, "automation: !include_dir_merge_list automations/\n")
    write(tmp_path, "automations/good.yaml", "- id: a1\n")
    write(tmp_path, "automations/broken.yaml", "- id: [unclosed\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert [e["id"] for e in found.entries] == ["a1"]
    assert any("broken.yaml" in r for r in found.unreadable)


def test_read_all_treats_an_empty_include_as_empty_not_unreadable(tmp_path):
    """HA ships an empty scenes.yaml; it holds no entries rather than hiding some."""
    _seed(tmp_path, "scene: !include scenes.yaml\nautomation: !include_dir_list automations/\n")
    write(tmp_path, "scenes.yaml", "")
    write(tmp_path, "automations/blank.yaml", "# nothing yet\n")
    write(tmp_path, "automations/real.yaml", "id: a1\n")
    assert yi.read_all_entries(str(tmp_path), "scene") == yi.DomainEntries([], ())
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert [e["id"] for e in found.entries] == ["a1"] and found.unreadable == ()


def test_read_all_names_a_missing_include_file(tmp_path):
    _seed(tmp_path, "scene: !include scenes.yaml\n")
    found = yi.read_all_entries(str(tmp_path), "scene")
    assert found.entries == [] and any("scenes.yaml" in r for r in found.unreadable)


def test_read_all_names_a_missing_include_directory(tmp_path):
    _seed(tmp_path, "automation: !include_dir_list automations/\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert found.entries == []
    assert any("does not exist" in r for r in found.unreadable)


def test_read_all_falls_back_to_the_legacy_leaf(tmp_path):
    """An unreadable configuration.yaml must not make the DEFAULT layout unreadable too.

    This mirrors OpResult.fallback on the write side.
    """
    write(tmp_path, "configuration.yaml", "this: [is not valid\n")
    write(tmp_path, "automations.yaml", "- id: a1\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert [e["id"] for e in found.entries] == ["a1"]


def test_read_all_on_an_absent_config_dir_is_empty_not_an_error(tmp_path):
    assert yi.read_all_entries(str(tmp_path), "automation").entries == []
    assert yi.read_all_entries(str(tmp_path), "script").entries == {}


def test_read_all_reports_a_shape_mismatch(tmp_path):
    """`automation: !include_dir_named` is the wrong flavor for a list domain."""
    _seed(tmp_path, "automation: !include_dir_named automations/\n")
    write(tmp_path, "automations/one.yaml", "id: a1\n")
    found = yi.read_all_entries(str(tmp_path), "automation")
    assert found.unreadable


def test_explicit_automation_id_under_packages_explains_the_create_refusal(tmp_path):
    write(tmp_path, "configuration.yaml", """\
        homeassistant:
          packages: {}
        automation: !include automations.yaml
    """)
    write(tmp_path, "automations.yaml", "[]\n")
    res = yi.automation_write(str(tmp_path), "porch", {"id": "porch", "triggers": [], "actions": []}, True, None, True)
    assert not res.ok and "cannot confirm" in res.message and "Omit the id" in res.message
    generated = yi.automation_write(str(tmp_path), "phoenix_mcp_x", {"id": "phoenix_mcp_x", "triggers": [], "actions": []},
                                    True, None, True, True)
    assert generated.ok, generated.message
