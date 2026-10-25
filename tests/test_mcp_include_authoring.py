"""Integration tests for include-graph-aware YAML authoring (yaml_includes wiring).

Runs the create/edit/delete automation/script/scene tools through the MCP tool
path on split configurations. Writes reach only entries written inline in Home
Assistant's editor files: a hand-written target (a split file, an include
directory, a whole-file entry, a body behind a plain !include) is refused before
any approval exists, and the approved executor refuses again when the entry
moved out of the editor file while the approval waited. Also covers byte
preservation of untouched files, refusal messages, the legacy fallback without
configuration.yaml, tag handling in version capture and restore, and replay
determinism.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp import yaml_includes as yi
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import (
    _call_tool,
    _execute_edit_automation,
    async_execute_approved_tool,
    async_restore_version,
)
from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord
from custom_components.phoenix_mcp.tools import authoring
from custom_components.phoenix_mcp.tools.authoring import (
    _build_diff_create_script,
    _build_diff_delete_automation,
    _execute_delete_automation,
    _execute_delete_scene,
    _execute_delete_script,
    _tool_delete_automation,
    _tool_delete_scene,
    _tool_delete_script,
)
from custom_components.phoenix_mcp.version_store import VersionStore

pytestmark = pytest.mark.usefixtures("immediate_authoring_observation")

DOMAINS = ["automation", "script", "scene"]
EDITOR = {"automation": "automations.yaml", "script": "scripts.yaml", "scene": "scenes.yaml"}
MERGE_DIR = {
    "automation": "!include_dir_merge_list",
    "script": "!include_dir_merge_named",
    "scene": "!include_dir_merge_list",
}
WHOLE_DIR = {"automation": "!include_dir_list", "script": "!include_dir_named", "scene": "!include_dir_list"}
ID_ARG = {"automation": "automation_id", "script": "script_id", "scene": "scene_id"}


def _access_key(tree: PermissionTree | None = None, **caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(),
        created_by="u", permissions=tree or PermissionTree(domains={}), **caps,
    )


def _data() -> tuple[PhoenixData, VersionStore]:
    versions = VersionStore()
    data = PhoenixData(
        store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=versions,
    )
    return data, versions


class ApprovalStore:
    """The slice of the integration store the Confirm gate keeps pending approvals in."""

    def __init__(self) -> None:
        self.pending: list = []
        self.async_lock = asyncio.Lock()
        self.async_save = AsyncMock()

    def get_pending_approvals(self) -> list:
        return self.pending

    def set_pending_approvals(self, approvals: list) -> None:
        self.pending = approvals


def _approval_data() -> tuple[PhoenixData, ApprovalStore]:
    store = ApprovalStore()
    data = PhoenixData(
        store=store, rate_limiter=MagicMock(), audit=MagicMock(),
        versions=VersionStore(), mesa=None,
    )
    return data, store


def _text(content: dict) -> str:
    return content["content"][0]["text"]


AUTOMATION_CFG = {
    "alias": "Test",
    "trigger": [{"platform": "state", "entity_id": "input_boolean.x"}],
    "action": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
}


@pytest.fixture
def env(hass: HomeAssistant, tmp_path):
    """Isolated config dir + no-op reload services for all three domains."""
    hass.config.config_dir = str(tmp_path)
    for domain in ("automation", "script", "scene"):
        hass.services.async_register(domain, "reload", lambda call: None)
    return tmp_path


@pytest.fixture
def kitchen_env(hass: HomeAssistant, env):
    """The config dir plus a real light.kitchen entity for scene members."""
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    light = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "uid_k", config_entry=entry,
        suggested_object_id="kitchen")
    hass.states.async_set(light.entity_id, "on", {})
    return env


def write(base, rel: str, content: str = "") -> None:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def read(base, rel: str) -> str:
    return (base / rel).read_text(encoding="utf-8")


def snapshot(base: Path) -> dict[str, bytes]:
    """Every file under the config tree, by relative path, for unchanged-bytes checks."""
    return {
        str(p.relative_to(base)): p.read_bytes()
        for p in sorted(base.rglob("*")) if p.is_file()
    }


def changed_files(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def hand_written(domain: str, name: str) -> str:
    return yi.MSG_HAND_WRITTEN.format(domain=domain, name=name, editor=EDITOR[domain])


def no_route(domain: str) -> str:
    return yi.MSG_CREATE_NO_ROUTE.format(domain=domain, editor=EDITOR[domain])


def _scene_tree() -> PermissionTree:
    return PermissionTree(domains={
        "light": PermissionNode(state="GREEN"),
        "scene": PermissionNode(state="GREEN"),
    })


def _confirm_key() -> AccessKeyRecord:
    """Every authoring capability on Confirm, so any change that reaches the gate is held."""
    return _access_key(
        tree=_scene_tree(), cap_automation_write="confirm", cap_script_write="confirm",
        cap_scene_write="confirm", cap_registry_read="allow",
    )


def _gate_spy():
    """Wrap the real Confirm gate at its call site, so reaching it is observable."""
    return patch.object(authoring, "_gate", new=AsyncMock(wraps=authoring._gate))


def _body(domain: str, label: str) -> dict:
    """An entry body Home Assistant's validators accept."""
    if domain == "automation":
        return dict(AUTOMATION_CFG, alias=label)
    if domain == "script":
        return {"alias": label, "sequence": []}
    return {"name": label, "entities": {"light.kitchen": "on"}}


def _edited(domain: str, label: str) -> dict:
    """The same entry with a reviewable change and its label kept."""
    if domain == "automation":
        return dict(AUTOMATION_CFG, alias=label, description="Changed")
    if domain == "script":
        return {"alias": label, "description": "Changed", "sequence": []}
    return {"name": label, "entities": {"light.kitchen": "off"}}


def _entries_text(domain: str, entries: dict[str, dict]) -> str:
    """Editor-shaped YAML for {entry_id: body}: a mapping for scripts, a list otherwise."""
    if domain == "script":
        return yi.dump_tagged(entries)
    return yi.dump_tagged([{"id": entry_id, **body} for entry_id, body in entries.items()])


def _whole_text(domain: str, entry_id: str, body: dict) -> str:
    """One entry as a whole document: keyed by filename for scripts, carrying its id otherwise."""
    return yi.dump_tagged(body if domain == "script" else {"id": entry_id, **body})


def _seed_hand_written(base: Path, domain: str, flavor: str) -> str:
    """Seed an editor-file entry "ui" and a hand-written entry "hand"; return the file the refusal names."""
    editor = EDITOR[domain]
    ui = _entries_text(domain, {"ui": _body(domain, "UI")})
    hand = _entries_text(domain, {"hand": _body(domain, "Hand")})
    config = f"{domain}: !include {editor}\n"
    write(base, editor, ui)
    if flavor == "split_file":
        write(base, "configuration.yaml", config + f"{domain} manual: !include manual.yaml\n")
        write(base, "manual.yaml", hand)
        return "manual.yaml"
    if flavor == "include_dir":
        write(base, "configuration.yaml", config + f"{domain} manual: {MERGE_DIR[domain]} manual\n")
        write(base, "manual/pack.yaml", hand)
        return "pack.yaml"
    if flavor == "whole_file":
        write(base, "configuration.yaml", config + f"{domain} manual: {WHOLE_DIR[domain]} manual\n")
        write(base, "manual/hand.yaml", _whole_text(domain, "hand", _body(domain, "Hand")))
        return "hand.yaml"
    assert flavor == "body_file"
    write(base, "configuration.yaml", config)
    reference = "hand: !include body.yaml\n" if domain == "script" else "- !include body.yaml\n"
    write(base, editor, ui + reference)
    write(base, "body.yaml", _whole_text(domain, "hand", _body(domain, "Hand")))
    return "body.yaml"


def _tool_args(base: Path, domain: str, operation: str, entry_id: str = "", label: str = "") -> dict:
    """Arguments an agent would send, bound to the entry's current content where the tool asks."""
    if operation == "create":
        args: dict = {"config": _body(domain, "Fresh")}
        if domain == "script":
            args["script_id"] = "fresh"
        return args
    if operation == "delete":
        return {ID_ARG[domain]: entry_id}
    return {
        ID_ARG[domain]: entry_id,
        "config": _edited(domain, label),
        "expected_hash": content_hash(yi.read_entry(str(base), domain, entry_id)),
    }


class TestAutomationSplitLayout:
    async def test_delete_diff_uses_live_friendly_name(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", "- id: 1734362394008\n  alias: ~test\n")
        hass.states.async_set(
            "automation.test", "off",
            {"id": "1734362394008", "friendly_name": "~test"},
        )
        diff = await _build_diff_delete_automation(
            {"automation_id": "1734362394008"}, _access_key(), hass,
        )
        assert diff["target"] == {
            "type": "automation", "id": "1734362394008", "label": "~test",
        }

    async def test_edit_reaches_only_the_editor_file(self, hass, env):
        """A split-file entry is refused untouched; the editor-file entry beside it is edited."""
        write(env, "configuration.yaml", """\
            automation: !include automations.yaml
            automation manual: !include_dir_merge_list automations
        """)
        write(env, "automations.yaml", "# keep me\n- id: ui\n  alias: UI\n")
        write(env, "automations/one.yaml", "# keep me\n- id: aaa\n  alias: A\n")
        write(env, "automations/two.yaml", "- id: bbb\n  alias: B  # inline note\n")
        before = snapshot(env)

        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "edit_automation",
            {"automation_id": "aaa", "config": dict(AUTOMATION_CFG, alias="A2"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", "aaa"))},
            access_key, hass, MagicMock(),
        )
        assert outcome == "denied"
        assert _text(content) == hand_written("automation", "one.yaml")
        assert snapshot(env) == before

        _, outcome, _ = await _call_tool(
            "edit_automation",
            {"automation_id": "ui", "config": dict(AUTOMATION_CFG, alias="UI2"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", "ui"))},
            access_key, hass, MagicMock(),
        )
        assert outcome == "allowed"
        assert changed_files(before, snapshot(env)) == {"automations.yaml"}
        text = read(env, "automations.yaml")
        assert text.startswith("# keep me\n")
        assert "alias: UI2" in text

    @pytest.mark.parametrize("configuration,directories", [
        pytest.param("default_config:\nfrontend:\n", (), id="unrouted"),
        pytest.param("automation: !include_dir_merge_list automations\n", ("automations",), id="directory"),
        pytest.param(
            "automation a: !include_dir_merge_list one\nautomation b: !include_dir_merge_list two\n",
            ("one", "two"), id="two-directories",
        ),
    ])
    async def test_create_refuses_without_the_editor_file(self, hass, env, configuration, directories):
        """An unroutable create is refused rather than writing a file of Phoenix's choosing."""
        write(env, "configuration.yaml", configuration)
        for directory in directories:
            (env / directory).mkdir()
        before = snapshot(env)
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == no_route("automation")
        assert snapshot(env) == before
        assert not (env / "automations.yaml").exists()
        assert all(list((env / directory).iterdir()) == [] for directory in directories)

    async def test_create_beside_split_files_goes_to_the_editor_file(self, hass, env):
        write(env, "configuration.yaml", """\
            automation ui: !include automations.yaml
            automation a: !include_dir_merge_list one
            automation b: !include_dir_merge_list two
        """)
        write(env, "one/pack.yaml", "- id: aaa\n  alias: A\n")
        (env / "two").mkdir()
        before = snapshot(env)
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "allowed", _text(content)
        aid = json.loads(_text(content))["automation_id"]
        assert changed_files(before, snapshot(env)) == {"automations.yaml"}
        assert [e["id"] for e in yi.load_tagged(str(env / "automations.yaml"))] == [aid]
        assert list((env / "two").iterdir()) == []

    async def test_delete_preserves_other_entries_bytes(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", """\
            - id: aaa
              alias: A
            # bbb heading
            - id: bbb
              alias: B
        """)
        access_key = _access_key(cap_automation_write="allow")
        _, outcome, _ = await _call_tool(
            "delete_automation", {"automation_id": "aaa"}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert (env / "automations.yaml").read_text(encoding="utf-8") == textwrap.dedent("""\
            # bbb heading
            - id: bbb
              alias: B
        """)

    async def test_duplicate_id_refusal_message(self, hass, env):
        write(env, "configuration.yaml", "automation: !include_dir_merge_list automations\n")
        write(env, "automations/a.yaml", "- id: dup\n  alias: A\n")
        write(env, "automations/b.yaml", "- id: dup\n  alias: B\n")
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "edit_automation", {"automation_id": "dup", "config": AUTOMATION_CFG},
            access_key, hass, MagicMock())
        assert outcome == "denied"
        assert "more than one included file" in _text(content)


class TestScriptSplitLayout:
    def test_create_diff_persists_alias(self, hass):
        diff = _build_diff_create_script(
            {
                "script_id": "approval_summary_smoke_test",
                "config": {"alias": "Approval Summary Smoke Test", "sequence": []},
            },
            _access_key(), hass,
        )
        assert diff["target"] == {
            "type": "script",
            "id": "approval_summary_smoke_test",
            "label": "Approval Summary Smoke Test",
        }

    async def test_create_edit_delete_reach_only_the_editor_file(self, hass, env):
        """Beside an include directory, every write lands in scripts.yaml and no file is unlinked."""
        write(env, "configuration.yaml",
              "script: !include scripts.yaml\nscript manual: !include_dir_named scripts\n")
        write(env, "scripts/hand.yaml", "alias: Hand\nsequence: []\n")
        access_key = _access_key(cap_script_write="allow")
        cfg = {"alias": "S", "sequence": []}

        _, outcome, _ = await _call_tool(
            "create_script", {"script_id": "hello", "config": cfg}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert "hello" in yi.load_tagged(str(env / "scripts.yaml"))
        assert not (env / "scripts/hello.yaml").exists()

        _, outcome, _ = await _call_tool(
            "edit_script", {"script_id": "hello", "config": dict(cfg, alias="S2")},
            access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert "alias: S2" in read(env, "scripts.yaml")

        _, outcome, _ = await _call_tool(
            "delete_script", {"script_id": "hello"}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert yi.read_entry(str(env), "script", "hello") is None

        before = snapshot(env)
        for tool, args in (
            ("edit_script", {"script_id": "hand", "config": dict(cfg, alias="Hand2")}),
            ("delete_script", {"script_id": "hand"}),
        ):
            content, outcome, _ = await _call_tool(tool, args, access_key, hass, MagicMock())
            assert outcome == "denied"
            assert _text(content) == hand_written("script", "hand.yaml")
        assert snapshot(env) == before

    async def test_create_refuses_a_directory_only_layout(self, hass, env):
        write(env, "configuration.yaml", "script: !include_dir_named scripts\n")
        (env / "scripts").mkdir()
        access_key = _access_key(cap_script_write="allow")
        content, outcome, _ = await _call_tool(
            "create_script", {"script_id": "hello", "config": {"sequence": []}},
            access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == no_route("script")
        assert list((env / "scripts").iterdir()) == []
        assert not (env / "scripts.yaml").exists()

    async def test_duplicate_create_graph_wide(self, hass, env):
        """An id already defined in hand-written YAML is a duplicate, not a second script."""
        write(env, "configuration.yaml",
              "script: !include scripts.yaml\nscript manual: !include_dir_merge_named scripts\n")
        write(env, "scripts/pack.yaml", "hello:\n  sequence: []\n")
        before = snapshot(env)
        access_key = _access_key(cap_script_write="allow")
        content, outcome, _ = await _call_tool(
            "create_script", {"script_id": "hello", "config": {"sequence": []}},
            access_key, hass, MagicMock())
        assert outcome == "invalid_request"
        assert "already exists" in _text(content)
        assert snapshot(env) == before


class TestSceneSplitLayout:
    def _scene_access_key(self) -> AccessKeyRecord:
        return _access_key(tree=_scene_tree(), cap_scene_write="allow", cap_registry_read="allow")

    async def test_edit_and_delete_reach_only_the_editor_file(self, hass, kitchen_env):
        env = kitchen_env
        write(env, "configuration.yaml",
              "scene: !include scenes.yaml\nscene manual: !include_dir_merge_list scenes\n")
        write(env, "scenes.yaml", """\
            - id: ui
              name: UI
              entities:
                light.kitchen: off
        """)
        write(env, "scenes/pack.yaml", """\
            - id: movie
              name: Movie
              entities:
                light.kitchen: off
            - id: other
              name: Other
              entities:
                light.kitchen: on
        """)
        pack_before = read(env, "scenes/pack.yaml")
        access_key = self._scene_access_key()
        cfg = {"name": "Movie v2", "entities": {"light.kitchen": "on"}}
        for tool, args in (
            ("edit_scene", {"scene_id": "movie", "config": cfg}),
            ("delete_scene", {"scene_id": "movie"}),
        ):
            content, outcome, _ = await _call_tool(tool, args, access_key, hass, MagicMock())
            assert outcome == "denied"
            assert _text(content) == hand_written("scene", "pack.yaml")
        assert read(env, "scenes/pack.yaml") == pack_before

        _, outcome, _ = await _call_tool(
            "edit_scene", {"scene_id": "ui", "config": dict(cfg, name="UI v2")},
            access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert "UI v2" in read(env, "scenes.yaml")

        _, outcome, _ = await _call_tool(
            "delete_scene", {"scene_id": "ui"}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert yi.read_entry(str(env), "scene", "ui") is None
        assert read(env, "scenes/pack.yaml") == pack_before

    async def test_oracle_error_for_missing_scene_on_split_layout(self, hass, kitchen_env):
        env = kitchen_env
        write(env, "configuration.yaml", "scene: !include_dir_merge_list scenes\n")
        (env / "scenes").mkdir()
        access_key = self._scene_access_key()
        content, outcome, _ = await _call_tool(
            "delete_scene", {"scene_id": "ghost"}, access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == (
            "No scene found with id 'ghost', or it controls entities outside your write scope.")

    async def test_delete_scene_registry_cleanup_on_split_layout(self, hass, kitchen_env):
        env = kitchen_env
        write(env, "configuration.yaml",
              "scene: !include scenes.yaml\nscene manual: !include_dir_merge_list scenes\n")
        (env / "scenes").mkdir()
        write(env, "scenes.yaml", """\
            - id: movie
              name: Movie
              entities:
                light.kitchen: off
        """)
        registry = er.async_get(hass)
        scene_entity = registry.async_get_or_create("scene", "homeassistant", "movie")
        access_key = self._scene_access_key()
        _, outcome, _ = await _call_tool(
            "delete_scene", {"scene_id": "movie"}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert registry.async_get(scene_entity.entity_id) is None


class TestHandWrittenNeverPending:
    """A change the editor could not make is refused before the Confirm gate runs.

    Each test drives the real Confirm gate against an in-memory approval store:
    the refused call leaves no pending approval and no changed byte, and the
    editor-file entry in the same tree does reach the gate.
    """

    @pytest.mark.parametrize("domain", DOMAINS)
    @pytest.mark.parametrize("flavor", ["split_file", "include_dir", "whole_file", "body_file"])
    @pytest.mark.parametrize("operation", ["edit", "delete"])
    async def test_refused_change_never_becomes_a_pending_approval(
        self, hass, kitchen_env, domain, flavor, operation,
    ):
        env = kitchen_env
        name = _seed_hand_written(env, domain, flavor)
        tool = f"{operation}_{domain}"
        data, store = _approval_data()
        access_key = _confirm_key()
        before = snapshot(env)

        with _gate_spy() as gate:
            content, outcome, _ = await _call_tool(
                tool, _tool_args(env, domain, operation, "hand", "Hand"), access_key, hass, data)
            assert outcome == "denied"
            assert _text(content) == hand_written(domain, name)
            gate.assert_not_called()
            assert store.pending == []
            assert snapshot(env) == before

            _, outcome, _ = await _call_tool(
                tool, _tool_args(env, domain, operation, "ui", "UI"), access_key, hass, data)
            assert outcome == "pending_approval"
            gate.assert_awaited_once()
        assert [approval["tool_name"] for approval in store.pending] == [tool]
        assert snapshot(env) == before

    @pytest.mark.parametrize("domain", DOMAINS)
    @pytest.mark.parametrize("layout", ["include_dir", "another_file"])
    async def test_refused_create_never_becomes_a_pending_approval(self, hass, kitchen_env, domain, layout):
        env = kitchen_env
        if layout == "include_dir":
            route = f"{domain}: {MERGE_DIR[domain]} manual\n"
            (env / "manual").mkdir()
        else:
            route = f"{domain}: !include manual.yaml\n"
            write(env, "manual.yaml", "")
        write(env, "configuration.yaml", route)
        tool = f"create_{domain}"
        data, store = _approval_data()
        access_key = _confirm_key()
        before = snapshot(env)

        with _gate_spy() as gate:
            content, outcome, _ = await _call_tool(
                tool, _tool_args(env, domain, "create"), access_key, hass, data)
            assert outcome == "denied"
            assert _text(content) == no_route(domain)
            gate.assert_not_called()
            assert store.pending == []
            assert snapshot(env) == before

            write(env, "configuration.yaml", route + f"{domain} ui: !include {EDITOR[domain]}\n")
            _, outcome, _ = await _call_tool(
                tool, _tool_args(env, domain, "create"), access_key, hass, data)
            assert outcome == "pending_approval"
            gate.assert_awaited_once()
        assert [approval["tool_name"] for approval in store.pending] == [tool]
        assert not (env / EDITOR[domain]).exists()

    async def test_refused_create_with_a_chosen_id_never_becomes_a_pending_approval(self, hass, env):
        """An explicit automation_id takes the identity precheck, which refuses the same way."""
        write(env, "configuration.yaml", "automation: !include_dir_merge_list automations\n")
        (env / "automations").mkdir()
        data, store = _approval_data()
        args = {"automation_id": "chosen", "config": AUTOMATION_CFG}
        with _gate_spy() as gate:
            content, outcome, _ = await _call_tool(
                "create_automation", args, _confirm_key(), hass, data)
            assert outcome == "denied"
            assert _text(content) == no_route("automation")
            gate.assert_not_called()
        assert store.pending == []
        assert list((env / "automations").iterdir()) == []


class TestApplyTimeRecheck:
    """The approved executor checks the editor-file boundary again.

    Files change while an approval waits. When the target leaves the editor file
    (or configuration.yaml stops loading it, for a create), the stored approval is
    refused without writing; once the layout is back, the same stored arguments
    apply.
    """

    @pytest.mark.parametrize("domain", DOMAINS)
    @pytest.mark.parametrize("operation", ["edit", "delete"])
    async def test_entry_moved_out_of_the_editor_file_while_pending(self, hass, kitchen_env, domain, operation):
        env = kitchen_env
        editor = EDITOR[domain]
        configuration = f"{domain}: !include {editor}\n"
        editor_text = _entries_text(domain, {"ui": _body(domain, "UI")})
        write(env, "configuration.yaml", configuration)
        write(env, editor, editor_text)
        tool = f"{operation}_{domain}"
        data, store = _approval_data()
        access_key = _confirm_key()
        _, outcome, _ = await _call_tool(
            tool, _tool_args(env, domain, operation, "ui", "UI"), access_key, hass, data)
        assert outcome == "pending_approval"
        approval = store.pending[0]

        # The operator moves the entry into a file of their own, unchanged.
        write(env, "configuration.yaml", configuration + f"{domain} manual: !include manual.yaml\n")
        write(env, "manual.yaml", editor_text)
        write(env, editor, "{}\n" if domain == "script" else "[]\n")
        before = snapshot(env)
        content, outcome, _ = await async_execute_approved_tool(
            tool, approval["args"], access_key, hass, data)
        assert outcome == "denied"
        assert _text(content) == hand_written(domain, "manual.yaml")
        assert snapshot(env) == before
        assert data.versions.list_for(domain, "ui") == []

        # Moved back, the same stored approval applies to the editor file.
        write(env, "configuration.yaml", configuration)
        write(env, editor, editor_text)
        (env / "manual.yaml").unlink()
        before = snapshot(env)
        content, outcome, _ = await async_execute_approved_tool(
            tool, approval["args"], access_key, hass, data)
        assert outcome == "allowed", _text(content)
        assert changed_files(before, snapshot(env)) == {editor}

    @pytest.mark.parametrize("domain", DOMAINS)
    async def test_editor_file_unloaded_while_create_pending(self, hass, kitchen_env, domain):
        env = kitchen_env
        editor = EDITOR[domain]
        configuration = f"{domain}: !include {editor}\n"
        write(env, "configuration.yaml", configuration)
        tool = f"create_{domain}"
        data, store = _approval_data()
        access_key = _confirm_key()
        _, outcome, _ = await _call_tool(
            tool, _tool_args(env, domain, "create"), access_key, hass, data)
        assert outcome == "pending_approval"
        approval = store.pending[0]

        write(env, "configuration.yaml", f"{domain}: {MERGE_DIR[domain]} manual\n")
        (env / "manual").mkdir()
        before = snapshot(env)
        content, outcome, _ = await async_execute_approved_tool(
            tool, approval["args"], access_key, hass, data)
        assert outcome == "denied"
        assert _text(content) == no_route(domain)
        assert snapshot(env) == before
        assert list((env / "manual").iterdir()) == []

        write(env, "configuration.yaml", configuration)
        before = snapshot(env)
        content, outcome, _ = await async_execute_approved_tool(
            tool, approval["args"], access_key, hass, data)
        assert outcome == "allowed", _text(content)
        assert changed_files(before, snapshot(env)) == {editor}


class TestLegacyFallbackParity:
    async def test_no_configuration_yaml_uses_legacy_path(self, hass, env):
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        aid = json.loads(_text(content))["automation_id"]
        assert (env / "automations.yaml").exists()

        _, outcome, _ = await _call_tool(
            "edit_automation", {"automation_id": aid, "config": dict(AUTOMATION_CFG, alias="X"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", aid))},
            access_key, hass, MagicMock())
        assert outcome == "allowed"

    async def test_legacy_include_guard_still_fires(self, hass, env):
        """No configuration.yaml + !include text in automations.yaml = legacy refusal."""
        write(env, "automations.yaml", "- !include extra.yaml\n")
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == (
            "automations.yaml uses !include directives. Phoenix MCP cannot safely edit it "
            "without destroying the include structure.")

    async def test_create_into_bare_empty_list_file(self, hass, env):
        """Creating into HA's default bare '[]' automations.yaml yields a loadable file."""
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", "[]\n")
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "allowed", _text(content)
        text = (env / "automations.yaml").read_text(encoding="utf-8")
        assert "[]" not in text
        import yaml as _yaml
        loaded = _yaml.safe_load(text)
        assert isinstance(loaded, list) and len(loaded) == 1

    async def test_not_found_message_parity(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", "- id: aaa\n  alias: A\n")
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "edit_automation", {"automation_id": "ghost", "config": AUTOMATION_CFG},
            access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == "No automation found with id 'ghost'."


class TestTagHandling:
    async def test_secret_edit_records_encoded_before(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", """\
            - id: aaa
              alias: A
              password: !secret db_pass
        """)
        data, versions = _data()
        access_key = _access_key(cap_automation_write="allow")
        _, outcome, _ = await _call_tool(
            "edit_automation", {"automation_id": "aaa", "config": dict(AUTOMATION_CFG, alias="A2"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", "aaa"))},
            access_key, hass, data)
        assert outcome == "allowed"
        record = versions.list_for("automation", "aaa")[0]
        assert record.before == {"id": "aaa", "alias": "A", "password": "!secret db_pass"}

    async def test_restore_refuses_tag_bearing_version(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", """\
            - id: aaa
              alias: A
              password: !secret db_pass
        """)
        data, versions = _data()
        access_key = _access_key(cap_automation_write="allow")
        await _call_tool("delete_automation", {"automation_id": "aaa"}, access_key, hass, data)
        record = versions.list_for("automation", "aaa")[0]
        assert record.before["password"] == "!secret db_pass"

        content, outcome, _ = await async_restore_version(record, "admin-1", hass, data)
        assert outcome == "invalid_request"
        assert "cannot be restored automatically" in _text(content)
        # Nothing was written back.
        assert "!secret" not in (env / "automations.yaml").read_text(encoding="utf-8")

    async def test_tag_free_restore_round_trips(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        data, versions = _data()
        access_key = _access_key(cap_automation_write="allow")
        content, _, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, data)
        aid = json.loads(_text(content))["automation_id"]
        await _call_tool("delete_automation", {"automation_id": aid}, access_key, hass, data)

        record = versions.list_for("automation", aid)[0]  # the delete, before=config
        _, outcome, _ = await async_restore_version(record, "admin-1", hass, data)
        assert outcome == "allowed"
        restored = yi.read_entry(str(env), "automation", aid)
        assert restored["alias"] == AUTOMATION_CFG["alias"]

    async def test_edit_refuses_include_bearing_entry(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", "- id: aaa\n  alias: A\n  action: !include actions.yaml\n")
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "edit_automation", {"automation_id": "aaa", "config": AUTOMATION_CFG, "expected_hash": content_hash(yi.read_entry(str(env), "automation", "aaa"))},
            access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == yi.MSG_INCLUDE_REFUSAL.format(name="automations.yaml")


class TestApprovalReplay:
    async def test_execute_edit_automation_standalone(self, hass, env):
        """Approval replay runs _execute_X directly; it must behave identically.

        That includes the editor-file boundary: the split-file entry beside the
        editor one is refused untouched.
        """
        write(env, "configuration.yaml", """\
            automation: !include automations.yaml
            automation manual: !include_dir_merge_list automations
        """)
        write(env, "automations.yaml", "- id: aaa\n  alias: A\n")
        write(env, "automations/one.yaml", "- id: bbb\n  alias: B\n")
        access_key = _access_key(cap_automation_write="confirm")
        _, outcome, _ = await _execute_edit_automation(
            {"automation_id": "aaa", "config": dict(AUTOMATION_CFG, alias="Approved"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", "aaa"))},
            access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert "alias: Approved" in read(env, "automations.yaml")

        before = snapshot(env)
        content, outcome, _ = await _execute_edit_automation(
            {"automation_id": "bbb", "config": dict(AUTOMATION_CFG, alias="Approved"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", "bbb"))},
            access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == hand_written("automation", "one.yaml")
        assert snapshot(env) == before

    async def test_stale_legacy_automation_delete_replay_is_refused(self, hass, env):
        write(env, "automations.yaml", "- id: aaa\n  alias: Reviewed\n")
        access_key = _access_key(cap_automation_write="confirm")
        captured: dict = {}
        captured_diff: dict = {}
        reviewed = {"id": "aaa", "alias": "Reviewed"}

        async def capture_gate(*args, **kwargs):
            captured.update(kwargs["args"])
            write(env, "automations.yaml", "- id: aaa\n  alias: Replacement\n")
            captured_diff.update(await kwargs["diff"]())
            return {"content": []}, "pending_approval", "approval:test"

        with patch(
            "custom_components.phoenix_mcp.tools.authoring._gate",
            new=AsyncMock(side_effect=capture_gate),
        ):
            _, outcome, _ = await _tool_delete_automation(
                {
                    "automation_id": "aaa",
                    "_phoenix_delete_expected_hash": "0" * 64,
                },
                access_key, hass, MagicMock(),
        )
        assert outcome == "pending_approval"
        assert captured["_phoenix_delete_expected_hash"] == content_hash(reviewed)
        assert "Reviewed" in captured_diff["before"]
        assert "Replacement" not in captured_diff["before"]

        content, outcome, _ = await _execute_delete_automation(
            captured, access_key, hass, MagicMock()
        )
        assert outcome == "invalid_request"
        assert "expected_hash no longer matches" in _text(content)
        assert "Replacement" in (env / "automations.yaml").read_text(encoding="utf-8")

        content, outcome, _ = await _execute_delete_automation(
            {"automation_id": "aaa"}, access_key, hass, MagicMock()
        )
        assert outcome == "invalid_request"
        assert "not bound to reviewed content" in _text(content)
        assert "Replacement" in (env / "automations.yaml").read_text(encoding="utf-8")

    async def test_unchanged_delete_approval_executes_from_stored_args(self, hass, env):
        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", "- id: aaa\n  alias: Reviewed\n")
        data, store = _approval_data()
        access_key = _access_key(cap_automation_write="confirm")

        _, outcome, _ = await _call_tool(
            "delete_automation", {"automation_id": "aaa"}, access_key, hass, data
        )
        assert outcome == "pending_approval"
        approval = store.pending[0]
        assert approval["args"]["_phoenix_delete_expected_hash"] == content_hash(
            {"id": "aaa", "alias": "Reviewed"}
        )

        result = await async_execute_approved_tool(
            "delete_automation", approval["args"], access_key, hass, data
        )
        assert result[1] == "allowed"
        assert yi.read_entry(str(env), "automation", "aaa") is None

    async def test_stale_script_delete_replay_is_refused(self, hass, env):
        write(env, "configuration.yaml", "script: !include scripts.yaml\n")
        write(env, "scripts.yaml", "hello:\n  alias: Reviewed\n  sequence: []\n")
        access_key = _access_key(cap_script_write="confirm")
        captured: dict = {}

        async def capture_gate(*args, **kwargs):
            captured.update(kwargs["args"])
            return {"content": []}, "pending_approval", "approval:test"

        with patch(
            "custom_components.phoenix_mcp.tools.authoring._gate",
            new=AsyncMock(side_effect=capture_gate),
        ):
            _, outcome, _ = await _tool_delete_script(
                {"script_id": "hello"}, access_key, hass, MagicMock()
            )
        assert outcome == "pending_approval"

        write(env, "scripts.yaml", "hello:\n  alias: Replacement\n  sequence: []\n")
        content, outcome, _ = await _execute_delete_script(
            captured, access_key, hass, MagicMock()
        )
        assert outcome == "invalid_request"
        assert "expected_hash no longer matches" in _text(content)
        assert "Replacement" in read(env, "scripts.yaml")

    async def test_stale_scene_delete_replay_is_refused(self, hass, kitchen_env):
        env = kitchen_env
        write(env, "configuration.yaml", "scene: !include scenes.yaml\n")
        write(env, "scenes.yaml", """\
            - id: movie
              name: Reviewed
              entities:
                light.kitchen: off
        """)
        access_key = _access_key(
            tree=_scene_tree(), cap_scene_write="confirm", cap_registry_read="allow",
        )
        captured: dict = {}

        async def capture_gate(*args, **kwargs):
            captured.update(kwargs["args"])
            return {"content": []}, "pending_approval", "approval:test"

        with patch(
            "custom_components.phoenix_mcp.tools.authoring._gate",
            new=AsyncMock(side_effect=capture_gate),
        ):
            _, outcome, _ = await _tool_delete_scene(
                {"scene_id": "movie"}, access_key, hass, MagicMock()
            )
        assert outcome == "pending_approval"

        write(env, "scenes.yaml", """\
            - id: movie
              name: Replacement
              entities:
                light.kitchen: on
        """)
        content, outcome, _ = await _execute_delete_scene(
            captured, access_key, hass, MagicMock()
        )
        assert outcome == "invalid_request"
        assert "expected_hash no longer matches" in _text(content)
        assert "Replacement" in read(env, "scenes.yaml")
