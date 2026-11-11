"""Automations, scripts and scenes defined under homeassistant.packages are read, never edited.

Each test writes a real configuration tree and reads it through Phoenix's tag-preserving
loader, the way Home Assistant would merge it: every include form for the core config, the
packages key, each package and each domain value, labeled keys, and the jail, depth, cycle and
secrets.yaml guards.
"""

from __future__ import annotations

import json
import os
import textwrap
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.version_store import VersionStore


def write(base: Path, rel: str, content: str = "") -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def ids(entries: list[yi.PackageEntry]) -> list[tuple[str, str, str]]:
    """(package, file, id or script key) for each entry, in order."""
    out = []
    for entry in entries:
        config = entry.config
        key = str(config.get("id")) if "id" in config else next(iter(config))
        out.append((entry.package, entry.file, key))
    return out


# ---- every form of the packages key


def test_inline_packages_in_configuration_yaml(tmp_path):
    write(tmp_path, "configuration.yaml", """\
        homeassistant:
          packages:
            garden:
              automation:
                - id: water
                  alias: Water
              script:
                lights_out:
                  sequence: []
              scene:
                - id: dusk
                  name: Dusk
                  entities: {}
    """)
    assert ids(yi.read_package_entries(str(tmp_path), "automation")[0]) == [("garden", "configuration.yaml", "water")]
    assert ids(yi.read_package_entries(str(tmp_path), "script")[0]) == [("garden", "configuration.yaml", "lights_out")]
    assert ids(yi.read_package_entries(str(tmp_path), "scene")[0]) == [("garden", "configuration.yaml", "dusk")]


def test_packages_from_one_included_file(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include packages.yaml\n")
    write(tmp_path, "packages.yaml", "garden:\n  automation:\n    - id: water\n")
    assert ids(yi.read_package_entries(str(tmp_path), "automation")[0]) == [("garden", "packages.yaml", "water")]


def test_packages_named_by_file(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include_dir_named packages\n")
    write(tmp_path, "packages/garden.yaml", "automation:\n  - id: water\n")
    write(tmp_path, "packages/sub/hall.yaml", "automation:\n  - id: hall_light\n")
    entries, unreadable = yi.read_package_entries(str(tmp_path), "automation")
    assert sorted(ids(entries)) == [
        ("garden", os.path.join("packages", "garden.yaml"), "water"),
        ("hall", os.path.join("packages", "sub", "hall.yaml"), "hall_light"),
    ]
    assert unreadable == ()


def test_packages_merged_from_a_directory(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include_dir_merge_named packages\n")
    write(tmp_path, "packages/outside.yaml", """\
        garden:
          automation:
            - id: water
        porch:
          script:
            porch_light:
              sequence: []
    """)
    assert ids(yi.read_package_entries(str(tmp_path), "automation")[0]) == [
        ("garden", os.path.join("packages", "outside.yaml"), "water")]
    assert ids(yi.read_package_entries(str(tmp_path), "script")[0]) == [
        ("porch", os.path.join("packages", "outside.yaml"), "porch_light")]


def test_an_included_core_config_and_package_bodies(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant: !include core.yaml\n")
    write(tmp_path, "core.yaml", "name: Home\npackages:\n  garden: !include garden.yaml\n")
    write(tmp_path, "garden.yaml", "automation: !include_dir_merge_list garden_autos\n")
    write(tmp_path, "garden_autos/a.yaml", "- id: water\n- !include one.yaml\n")
    write(tmp_path, "garden_autos/one.yaml", "id: mow\n")
    entries, unreadable = yi.read_package_entries(str(tmp_path), "automation")
    assert ids(entries) == [
        ("garden", os.path.join("garden_autos", "a.yaml"), "water"),
        ("garden", os.path.join("garden_autos", "one.yaml"), "mow"),
    ]
    assert unreadable == ()


def test_a_whole_file_include_chain(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include_dir_named packages\n")
    write(tmp_path, "packages/garden.yaml", "!include ../real/garden.yaml\n")
    write(tmp_path, "real/garden.yaml", "!include garden_body.yaml\n")
    write(tmp_path, "real/garden_body.yaml", "script:\n  water:\n    sequence: []\n")
    assert ids(yi.read_package_entries(str(tmp_path), "script")[0]) == [
        ("garden", os.path.join("real", "garden_body.yaml"), "water")]


@pytest.mark.parametrize("key", ["automation split", "automation  second"])
def test_labeled_keys_and_a_single_automation_mapping(tmp_path, key):
    write(tmp_path, "configuration.yaml", f"""\
        homeassistant:
          packages:
            garden:
              {key}:
                id: water
              automationish:
                - id: not_an_automation
              "automation ":
                - id: bad_label
    """)
    assert ids(yi.read_package_entries(str(tmp_path), "automation")[0]) == [("garden", "configuration.yaml", "water")]


def test_each_domain_value_form_inside_a_package(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include_dir_named packages\n")
    write(tmp_path, "packages/garden.yaml", """\
        script: !include_dir_named garden_scripts
        scene: !include_dir_list garden_scenes
        automation: !include garden_autos.yaml
    """)
    write(tmp_path, "packages/garden_scripts/water.yaml", "sequence: []\n")
    write(tmp_path, "packages/garden_scenes/dusk.yaml", "id: dusk\nname: Dusk\nentities: {}\n")
    write(tmp_path, "packages/garden_autos.yaml", "- id: mow\n")
    assert [key for *_, key in ids(yi.read_package_entries(str(tmp_path), "script")[0])] == ["water"]
    assert [key for *_, key in ids(yi.read_package_entries(str(tmp_path), "scene")[0])] == ["dusk"]
    assert [key for *_, key in ids(yi.read_package_entries(str(tmp_path), "automation")[0])] == ["mow"]


# ---- guards


def test_an_include_outside_the_config_directory_is_not_read(tmp_path):
    config = tmp_path / "config"
    write(tmp_path, "outside/garden.yaml", "automation:\n  - id: stolen\n")
    write(config, "configuration.yaml", """\
        homeassistant:
          packages:
            garden: !include ../outside/garden.yaml
            linked: !include linked.yaml
            kept:
              automation:
                - id: kept
    """)
    os.symlink(tmp_path / "outside" / "garden.yaml", config / "linked.yaml")
    entries, unreadable = yi.read_package_entries(str(config), "automation")
    assert ids(entries) == [("kept", "configuration.yaml", "kept")]
    assert len(unreadable) == 2 and all("outside the configuration directory" in item for item in unreadable)


def test_a_symlinked_file_in_a_package_folder_is_not_read(tmp_path):
    config = tmp_path / "config"
    write(tmp_path, "outside/stolen.yaml", "automation:\n  - id: stolen\n")
    write(config, "configuration.yaml", "homeassistant:\n  packages: !include_dir_named packages\n")
    write(config, "packages/garden.yaml", "automation:\n  - id: water\n")
    os.symlink(tmp_path / "outside" / "stolen.yaml", config / "packages" / "stolen.yaml")
    entries, unreadable = yi.read_package_entries(str(config), "automation")
    assert [key for *_, key in ids(entries)] == ["water"]
    assert unreadable == ("a package include points outside the configuration directory",)


def test_a_merge_folder_skips_files_of_the_wrong_shape(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include_dir_merge_named packages\n")
    write(tmp_path, "packages/list.yaml", "- automation:\n    - id: stray\n")
    write(tmp_path, "packages/good.yaml", "garden:\n  automation:\n    - id: water\n")
    entries, unreadable = yi.read_package_entries(str(tmp_path), "automation")
    assert [key for *_, key in ids(entries)] == ["water"]
    # Home Assistant skips such a file without a word, so it is no gap in the answer.
    assert unreadable == ()


def test_a_cycle_is_named_and_the_rest_still_read(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include_dir_named packages\n")
    write(tmp_path, "packages/loop.yaml", "!include loop_b.yaml\n")
    write(tmp_path, "packages/loop_b.yaml", "!include loop.yaml\n")
    write(tmp_path, "packages/garden.yaml", "automation:\n  - id: water\n")
    entries, unreadable = yi.read_package_entries(str(tmp_path), "automation")
    assert [key for *_, key in ids(entries)] == ["water"]
    assert any("cycle" in item for item in unreadable)


def test_includes_deeper_than_the_limit_are_named(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages:\n    deep: !include d0.yaml\n")
    for level in range(yi.MAX_INCLUDE_DEPTH + 2):
        write(tmp_path, f"d{level}.yaml", f"!include d{level + 1}.yaml\n")
    write(tmp_path, f"d{yi.MAX_INCLUDE_DEPTH + 2}.yaml", "automation:\n  - id: too_deep\n")
    entries, unreadable = yi.read_package_entries(str(tmp_path), "automation")
    assert entries == []
    assert any("levels of includes deep" in item for item in unreadable)


@pytest.mark.parametrize("name", ["secrets.yaml", "SECRETS.yaml"])
def test_secrets_yaml_is_never_read_as_a_package(tmp_path, name):
    write(tmp_path, name, "db_password: hunter2-very-secret\n")
    write(tmp_path, "configuration.yaml", f"""\
        homeassistant:
          packages:
            sneaky:
              script: !include {name}
    """)
    entries, unreadable = yi.read_package_entries(str(tmp_path), "script")
    assert entries == []
    assert any("is not read" in item for item in unreadable)
    assert "hunter2" not in json.dumps([e.config for e in entries])


def test_an_unparseable_package_is_named_and_the_rest_still_read(tmp_path):
    write(tmp_path, "configuration.yaml", "homeassistant:\n  packages: !include_dir_named packages\n")
    write(tmp_path, "packages/broken.yaml", "automation: [unclosed\n")
    write(tmp_path, "packages/garden.yaml", "automation:\n  - id: water\n")
    entries, unreadable = yi.read_package_entries(str(tmp_path), "automation")
    assert [key for *_, key in ids(entries)] == ["water"]
    assert unreadable == (os.path.join("packages", "broken.yaml") + " could not be read",)


def test_secret_tags_stay_opaque(tmp_path):
    write(tmp_path, "secrets.yaml", "api_token: hunter2-very-secret\n")
    write(tmp_path, "configuration.yaml", """\
        homeassistant:
          packages:
            garden:
              script:
                notify_me:
                  sequence:
                    - action: rest_command.ping
                      data:
                        token: !secret api_token
    """)
    entry = yi.read_package_entry(str(tmp_path), "script", "notify_me")
    assert entry is not None
    text = json.dumps(entry.config)
    assert "!secret api_token" in text and "hunter2" not in text


def test_an_id_defined_in_two_packages_is_not_returned(tmp_path):
    write(tmp_path, "configuration.yaml", """\
        homeassistant:
          packages:
            one:
              automation:
                - id: water
            two:
              automation:
                - id: water
    """)
    assert yi.read_package_entry(str(tmp_path), "automation", "water") is None


# ---- reverse-reference reads


def test_read_all_entries_includes_packages_and_drops_the_blanket_warning(tmp_path):
    write(tmp_path, "configuration.yaml", """\
        automation: !include automations.yaml
        script: !include scripts.yaml
        homeassistant:
          packages: !include_dir_named packages
    """)
    write(tmp_path, "automations.yaml", "- id: editor_one\n")
    write(tmp_path, "scripts.yaml", "editor_script:\n  sequence: []\n")
    write(tmp_path, "packages/garden.yaml", "automation:\n  - id: water\nscript:\n  water_script:\n    sequence: []\n")
    automations = yi.read_all_entries(str(tmp_path), "automation")
    assert [item["id"] for item in automations.entries] == ["water", "editor_one"]
    assert automations.unreadable == ()
    assert set(yi.read_all_entries(str(tmp_path), "script").entries) == {"editor_script", "water_script"}


# ---- read tools


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=caps.pop("tree", PermissionTree(domains={})), **caps,
    )


def _data() -> PhoenixData:
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


@pytest.fixture
def packaged(hass: HomeAssistant, tmp_path):
    """A config dir whose automation, script and scene live in a package, and the editor files empty."""
    hass.config.config_dir = str(tmp_path)
    write(tmp_path, "configuration.yaml", """\
        automation: !include automations.yaml
        script: !include scripts.yaml
        scene: !include scenes.yaml
        homeassistant:
          packages: !include_dir_named packages
    """)
    write(tmp_path, "automations.yaml", "[]\n")
    write(tmp_path, "scripts.yaml", "{}\n")
    write(tmp_path, "scenes.yaml", "[]\n")
    write(tmp_path, "packages/garden.yaml", """\
        automation:
          - id: water
            alias: Water
            triggers:
              - trigger: time
                at: "06:00:00"
            actions: []
        script:
          water_script:
            sequence: []
        scene:
          - id: dusk
            name: Dusk
            entities:
              light.kitchen: "on"
    """)
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    light = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "uid_k", config_entry=entry, suggested_object_id="kitchen")
    hass.states.async_set(light.entity_id, "on", {})
    hass.states.async_set("automation.water", "on", {"id": "water", "friendly_name": "Water"})
    return tmp_path


@pytest.mark.parametrize(("tool", "id_key", "entry_id", "cap"), [
    ("get_automation", "automation_id", "water", "cap_automation_write"),
    ("get_script", "script_id", "water_script", "cap_script_write"),
    ("get_scene", "scene_id", "dusk", "cap_scene_write"),
])
async def test_a_read_tool_returns_a_package_entry_marked_not_editable(hass, packaged, tool, id_key, entry_id, cap):
    tree = PermissionTree(entities={"light.kitchen": PermissionNode(state="GREEN")})
    result, outcome, _ = await _call_tool(tool, {id_key: entry_id}, _key(tree=tree, **{cap: "allow"}), hass, _data())
    assert outcome == "allowed", result
    body = json.loads(result["content"][0]["text"])
    assert body["editable"] is False and "content_hash" not in body
    assert body["package"] == "garden" and body["file"] == os.path.join("packages", "garden.yaml")
    assert "cannot change this one" in body["note"]


async def test_a_package_scene_beyond_write_scope_reads_as_missing(hass, packaged):
    result, outcome, _ = await _call_tool(
        "get_scene", {"scene_id": "dusk"}, _key(cap_scene_write="allow"), hass, _data())
    missing, missing_outcome, _ = await _call_tool(
        "get_scene", {"scene_id": "nowhere"}, _key(cap_scene_write="allow"), hass, _data())
    assert outcome == missing_outcome == "denied"
    assert result["content"][0]["text"].replace("dusk", "X") == missing["content"][0]["text"].replace("nowhere", "X")


async def test_a_package_automation_is_still_refused_for_edits(hass, packaged):
    before = (packaged / "packages" / "garden.yaml").read_text()
    result, outcome, _ = await _call_tool("edit_automation", {
        "automation_id": "water",
        "config": {"alias": "Changed", "triggers": [{"trigger": "time", "at": "07:00:00"}], "actions": []},
    }, _key(cap_automation_write="allow"), hass, _data())
    assert outcome != "allowed", result
    assert (packaged / "packages" / "garden.yaml").read_text() == before
    assert not (packaged / "automations.yaml").read_text().strip("[]\n")


async def test_list_automations_searches_package_automations(hass, packaged):
    tree = PermissionTree(entities={"automation.water": PermissionNode(state="YELLOW")})
    result, outcome, _ = await _call_tool("list_automations", {"trigger": "time"}, _key(
        tree=tree, cap_registry_read="allow", cap_automation_write="allow"), hass, _data())
    assert outcome == "allowed", result
    body = json.loads(result["content"][0]["text"])
    assert [item["entity_id"] for item in body["automations"]] == ["automation.water"]
    assert "not_searched" not in body


# ---- creating an id a package already defines


def _create_layout(tmp_path, package_body: str = "") -> None:
    write(tmp_path, "configuration.yaml", """\
        automation: !include automations.yaml
        script: !include scripts.yaml
        homeassistant:
          packages: !include_dir_named packages
    """)
    write(tmp_path, "automations.yaml", "[]\n")
    write(tmp_path, "scripts.yaml", "{}\n")
    (tmp_path / "packages").mkdir(exist_ok=True)
    if package_body:
        write(tmp_path, "packages/garden.yaml", package_body)


AUTOMATION = {"triggers": [], "actions": []}


@pytest.mark.parametrize("dry_run", [True, False])
def test_an_automation_id_a_package_defines_is_refused(tmp_path, dry_run):
    _create_layout(tmp_path, "automation:\n  - id: water\n")
    result = yi.automation_write(str(tmp_path), "water", {"id": "water", **AUTOMATION}, True, None, dry_run)
    assert result.error_kind == "already_exists"
    assert "package 'garden'" in result.message and os.path.join("packages", "garden.yaml") in result.message
    assert (tmp_path / "automations.yaml").read_text() == "[]\n"


def test_an_automation_id_no_package_defines_is_created(tmp_path):
    _create_layout(tmp_path, "automation:\n  - id: water\n")
    result = yi.automation_write(str(tmp_path), "porch", {"id": "porch", **AUTOMATION}, True)
    assert result.ok, result.message
    assert "porch" in (tmp_path / "automations.yaml").read_text()


def test_an_unread_package_still_leaves_a_chosen_automation_id_unproven(tmp_path):
    _create_layout(tmp_path, "automation:\n  - id: water\n")
    write(tmp_path, "packages/broken.yaml", "automation: [unclosed\n")
    result = yi.automation_write(str(tmp_path), "porch", {"id": "porch", **AUTOMATION}, True)
    assert result.error_kind == "ambiguous" and "cannot confirm" in result.message
    generated = yi.automation_write(str(tmp_path), "phoenix_mcp_x", {"id": "phoenix_mcp_x", **AUTOMATION},
                                    True, None, False, True)
    assert generated.ok, generated.message


def test_a_script_id_a_package_defines_is_refused_before_and_at_the_write(tmp_path):
    _create_layout(tmp_path, "script:\n  water:\n    sequence: []\n")
    precheck = yi.write_target_refusal(str(tmp_path), "script", "water", create=True)
    assert precheck is not None and precheck.error_kind == "already_exists"
    result = yi.perform_create(str(tmp_path), "script", "water", {"sequence": []})
    assert result.error_kind == "already_exists" and "package 'garden'" in result.message
    assert (tmp_path / "scripts.yaml").read_text() == "{}\n"
    assert yi.write_target_refusal(str(tmp_path), "script", "porch", create=True) is None
    assert yi.perform_create(str(tmp_path), "script", "porch", {"sequence": []}).ok


def test_an_unread_package_does_not_block_a_new_script(tmp_path):
    """As before packages were read: only an id proven taken refuses a script."""
    _create_layout(tmp_path)
    write(tmp_path, "packages/broken.yaml", "script: [unclosed\n")
    assert yi.perform_create(str(tmp_path), "script", "porch", {"sequence": []}).ok


async def test_create_script_refuses_a_package_id_before_any_approval(hass, packaged):
    from custom_components.phoenix_mcp.tools import authoring
    from unittest.mock import AsyncMock, patch

    with patch.object(authoring, "_gate", new_callable=AsyncMock) as gate:
        result, outcome, _ = await _call_tool("create_script", {
            "script_id": "water_script", "config": {"sequence": []}}, _key(cap_script_write="allow"), hass, _data())
    assert outcome == "denied", result
    assert "package 'garden'" in result["content"][0]["text"]
    gate.assert_not_awaited()
    assert (packaged / "scripts.yaml").read_text() == "{}\n"


# ---- secrets.yaml on the editor-file paths


SECRET = "hunter2-very-secret"


@pytest.mark.parametrize("route", ["!include secrets.yaml", "!include linked.yaml", "!include SECRETS.yaml"])
def test_a_domain_routed_to_secrets_yaml_is_never_read_or_written(tmp_path, route):
    name = "SECRETS.yaml" if "SECRETS" in route else "secrets.yaml"
    write(tmp_path, name, f"db_password:\n  sequence: {SECRET}\n")
    if "linked" in route:
        os.symlink(tmp_path / name, tmp_path / "linked.yaml")
    write(tmp_path, "configuration.yaml", f"script: {route}\n")
    before = (tmp_path / name).read_text()
    assert yi.read_entry(str(tmp_path), "script", "db_password") is None
    found = yi.read_all_entries(str(tmp_path), "script")
    assert found.entries == {} and found.unreadable == (yi.MSG_SECRETS.format(domain="script"),)
    for result in (
        yi.perform_create(str(tmp_path), "script", "new_one", {"sequence": []}),
        yi.perform_edit(str(tmp_path), "script", "db_password", {"sequence": []}),
        yi.perform_delete(str(tmp_path), "script", "db_password"),
        yi.write_target_refusal(str(tmp_path), "script", "new_one", create=True),
    ):
        assert result is not None and not result.ok and SECRET not in (result.message or "")
    assert (tmp_path / name).read_text() == before


@pytest.mark.parametrize("flavor", ["!include_dir_merge_named", "!include_dir_named"])
def test_a_symlinked_leaf_in_an_editor_folder_is_not_read(tmp_path, flavor):
    config = tmp_path / "config"
    write(config, "secrets.yaml", f"db_password:\n  sequence: {SECRET}\n")
    write(tmp_path, "outside/stolen.yaml", f"stolen:\n  sequence: {SECRET}\n")
    write(config, "configuration.yaml", f"script: {flavor} scripts\n")
    write(config, "scripts/kept.yaml", "kept:\n  sequence: []\n" if "merge" in flavor else "sequence: []\n")
    os.symlink(config / "secrets.yaml", config / "scripts" / "linked_secrets.yaml")
    os.symlink(tmp_path / "outside" / "stolen.yaml", config / "scripts" / "linked_out.yaml")
    found = yi.read_all_entries(str(config), "script")
    assert SECRET not in json.dumps(found.entries) and set(found.entries) == {"kept"}
    for entry_id in ("db_password", "linked_secrets", "stolen", "linked_out"):
        assert SECRET not in json.dumps(yi.read_entry(str(config), "script", entry_id))


def test_a_nested_include_of_secrets_yaml_is_refused(tmp_path):
    write(tmp_path, "secrets.yaml", f"db_password:\n  sequence: {SECRET}\n")
    write(tmp_path, "configuration.yaml", "script: !include scripts.yaml\n")
    write(tmp_path, "scripts.yaml", "db_password: !include secrets.yaml\n")
    assert yi.read_entry(str(tmp_path), "script", "db_password") is None
    result = yi.perform_edit(str(tmp_path), "script", "db_password", {"sequence": []})
    assert not result.ok and result.message == yi.MSG_SECRETS.format(domain="script")


def test_the_fallback_editor_readers_refuse_secrets_yaml(tmp_path):
    from custom_components.phoenix_mcp.tools import authoring

    write(tmp_path, "secrets.yaml", f"db_password:\n  sequence: {SECRET}\n")
    os.symlink(tmp_path / "secrets.yaml", tmp_path / "scripts.yaml")
    for reader in (authoring._read_scripts_yaml, authoring._read_automations_yaml, authoring._read_scenes_yaml):
        with pytest.raises(yi.SecretsFileError):
            reader(str(tmp_path / "scripts.yaml"))
    assert yi.read_entry(str(tmp_path), "script", "db_password") is None
