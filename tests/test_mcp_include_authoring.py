"""Integration tests for include-graph-aware YAML authoring (yaml_includes wiring).

Runs the create/edit/delete automation/script/scene executors on split
configurations (!include / !include_dir_* layouts): leaf routing, byte
preservation of untouched files, refusal messages, the legacy fallback without
configuration.yaml, tag handling in version capture and restore, replay determinism.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import uuid
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


def write(base, rel: str, content: str = "") -> None:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


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

    async def test_edit_touches_only_the_leaf(self, hass, env):
        write(env, "configuration.yaml", "automation: !include_dir_merge_list automations\n")
        write(env, "automations/one.yaml", "# keep me\n- id: aaa\n  alias: A\n")
        write(env, "automations/two.yaml", "- id: bbb\n  alias: B  # inline note\n")
        other_before = (env / "automations/two.yaml").read_text(encoding="utf-8")

        access_key = _access_key(cap_automation_write="allow")
        _, outcome, _ = await _call_tool(
            "edit_automation",
            {"automation_id": "aaa", "config": dict(AUTOMATION_CFG, alias="A2"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", "aaa"))},
            access_key, hass, MagicMock(),
        )
        assert outcome == "allowed"
        assert (env / "automations/two.yaml").read_text(encoding="utf-8") == other_before
        text = (env / "automations/one.yaml").read_text(encoding="utf-8")
        assert text.startswith("# keep me\n")
        assert "alias: A2" in text

    async def test_create_routes_to_dir_flavor(self, hass, env):
        write(env, "configuration.yaml", "automation: !include_dir_merge_list automations\n")
        (env / "automations").mkdir()
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        aid = json.loads(_text(content))["automation_id"]
        new_file = env / "automations" / f"{aid}.yaml"
        assert new_file.exists()

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

    async def test_create_refuses_when_unrouted(self, hass, env):
        """An unroutable create is refused rather than silently writing nothing."""
        write(env, "configuration.yaml", "default_config:\nfrontend:\n")
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == yi.MSG_CREATE_NO_ROUTE.format(domain="automation")
        assert not (env / "automations.yaml").exists()

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

    async def test_multi_branch_create_ambiguity(self, hass, env):
        write(env, "configuration.yaml", """\
            automation a: !include_dir_merge_list one
            automation b: !include_dir_merge_list two
        """)
        access_key = _access_key(cap_automation_write="allow")
        content, outcome, _ = await _call_tool(
            "create_automation", {"config": AUTOMATION_CFG}, access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == yi.MSG_CREATE_MULTI.format(domain="automation")


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

    async def test_create_edit_delete_dir_named(self, hass, env):
        write(env, "configuration.yaml", "script: !include_dir_named scripts\n")
        (env / "scripts").mkdir()
        access_key = _access_key(cap_script_write="allow")
        cfg = {"alias": "S", "sequence": []}

        _, outcome, _ = await _call_tool(
            "create_script", {"script_id": "hello", "config": cfg}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert (env / "scripts/hello.yaml").exists()

        _, outcome, _ = await _call_tool(
            "edit_script", {"script_id": "hello", "config": dict(cfg, alias="S2")},
            access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert "alias: S2" in (env / "scripts/hello.yaml").read_text(encoding="utf-8")

        _, outcome, _ = await _call_tool(
            "delete_script", {"script_id": "hello"}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        # dir_named: an empty leftover file would resurrect as {stem: {}}, so
        # delete removes the file itself.
        assert not (env / "scripts/hello.yaml").exists()

    async def test_duplicate_create_graph_wide(self, hass, env):
        write(env, "configuration.yaml", "script: !include_dir_merge_named scripts\n")
        write(env, "scripts/pack.yaml", "hello:\n  sequence: []\n")
        access_key = _access_key(cap_script_write="allow")
        content, outcome, _ = await _call_tool(
            "create_script", {"script_id": "hello", "config": {"sequence": []}},
            access_key, hass, MagicMock())
        assert outcome == "invalid_request"
        assert "already exists" in _text(content)


class TestSceneSplitLayout:
    @pytest.fixture
    def scene_env(self, hass: HomeAssistant, env):
        entry = MockConfigEntry(domain="test_integration", entry_id="e1")
        entry.add_to_hass(hass)
        e = er.async_get(hass).async_get_or_create(
            "light", "test_integration", "uid_k", config_entry=entry,
            suggested_object_id="kitchen")
        hass.states.async_set(e.entity_id, "on", {})
        return env

    def _scene_access_key(self) -> AccessKeyRecord:
        tree = PermissionTree(domains={
            "light": PermissionNode(state="GREEN"),
            "scene": PermissionNode(state="GREEN"),
        })
        return _access_key(tree=tree, cap_scene_write="allow", cap_registry_read="allow")

    async def test_edit_and_delete_in_merge_list_dir(self, hass, scene_env):
        env = scene_env
        write(env, "configuration.yaml", "scene: !include_dir_merge_list scenes\n")
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
        access_key = self._scene_access_key()
        cfg = {"name": "Movie v2", "entities": {"light.kitchen": "on"}}
        _, outcome, _ = await _call_tool(
            "edit_scene", {"scene_id": "movie", "config": cfg}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        text = (env / "scenes/pack.yaml").read_text(encoding="utf-8")
        assert "Movie v2" in text and "name: Other" in text

        _, outcome, _ = await _call_tool(
            "delete_scene", {"scene_id": "movie"}, access_key, hass, MagicMock())
        assert outcome == "allowed"
        remaining = (env / "scenes/pack.yaml").read_text(encoding="utf-8")
        assert "movie" not in remaining and "name: Other" in remaining

    async def test_oracle_error_for_missing_scene_on_split_layout(self, hass, scene_env):
        env = scene_env
        write(env, "configuration.yaml", "scene: !include_dir_merge_list scenes\n")
        (env / "scenes").mkdir()
        access_key = self._scene_access_key()
        content, outcome, _ = await _call_tool(
            "delete_scene", {"scene_id": "ghost"}, access_key, hass, MagicMock())
        assert outcome == "denied"
        assert _text(content) == (
            "No scene found with id 'ghost', or it controls entities outside your write scope.")

    async def test_delete_scene_registry_cleanup_on_split_layout(self, hass, scene_env):
        env = scene_env
        write(env, "configuration.yaml", "scene: !include_dir_merge_list scenes\n")
        write(env, "scenes/pack.yaml", """\
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
        """Approval replay runs _execute_X directly; it must behave identically."""
        write(env, "configuration.yaml", "automation: !include_dir_merge_list automations\n")
        write(env, "automations/one.yaml", "- id: aaa\n  alias: A\n")
        access_key = _access_key(cap_automation_write="confirm")
        _, outcome, _ = await _execute_edit_automation(
            {"automation_id": "aaa", "config": dict(AUTOMATION_CFG, alias="Approved"), "expected_hash": content_hash(yi.read_entry(str(env), "automation", "aaa"))},
            access_key, hass, MagicMock())
        assert outcome == "allowed"
        assert "alias: Approved" in (env / "automations/one.yaml").read_text(encoding="utf-8")

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
        class ApprovalStore:
            def __init__(self) -> None:
                self.pending: list = []
                self.async_lock = asyncio.Lock()
                self.async_save = AsyncMock()

            def get_pending_approvals(self) -> list:
                return self.pending

            def set_pending_approvals(self, approvals: list) -> None:
                self.pending = approvals

        write(env, "configuration.yaml", "automation: !include automations.yaml\n")
        write(env, "automations.yaml", "- id: aaa\n  alias: Reviewed\n")
        store = ApprovalStore()
        data = PhoenixData(
            store=store, rate_limiter=MagicMock(), audit=MagicMock(),
            versions=VersionStore(), mesa=None,
        )
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

    async def test_stale_split_script_delete_replay_is_refused(self, hass, env):
        write(env, "configuration.yaml", "script: !include_dir_named scripts\n")
        write(env, "scripts/hello.yaml", "alias: Reviewed\nsequence: []\n")
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

        write(env, "scripts/hello.yaml", "alias: Replacement\nsequence: []\n")
        content, outcome, _ = await _execute_delete_script(
            captured, access_key, hass, MagicMock()
        )
        assert outcome == "invalid_request"
        assert "expected_hash no longer matches" in _text(content)
        assert "Replacement" in (env / "scripts/hello.yaml").read_text(encoding="utf-8")

    async def test_stale_split_scene_delete_replay_is_refused(self, hass, env):
        entry = MockConfigEntry(domain="test_integration", entry_id="stale-scene")
        entry.add_to_hass(hass)
        light = er.async_get(hass).async_get_or_create(
            "light", "test_integration", "stale-scene-light",
            config_entry=entry, suggested_object_id="kitchen",
        )
        hass.states.async_set(light.entity_id, "on", {})
        write(env, "configuration.yaml", "scene: !include_dir_merge_list scenes\n")
        write(env, "scenes/pack.yaml", """\
            - id: movie
              name: Reviewed
              entities:
                light.kitchen: off
        """)
        access_key = _access_key(
            tree=PermissionTree(domains={
                "light": PermissionNode(state="GREEN"),
                "scene": PermissionNode(state="GREEN"),
            }),
            cap_scene_write="confirm", cap_registry_read="allow",
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

        write(env, "scenes/pack.yaml", """\
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
        assert "Replacement" in (env / "scenes/pack.yaml").read_text(encoding="utf-8")
