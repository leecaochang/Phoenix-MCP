"""Verify authoring results against real Home Assistant integrations.

Blocking reloads await script and automation entity addition on the pinned HA
version. Scene reload schedules addition, so the fast-path probe alone cannot
prove reload completion implies registration. Delayed-addition tests exercise
that boundary explicitly. Registration observations do not certify freshness
of edited configuration or availability of the entity.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from tests.runtime_fixtures import mesa_off_store

from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.phoenix_mcp.version_store import VersionStore


@pytest.fixture(autouse=True)
def isolated_config_dir(hass: HomeAssistant, tmp_path):
    """A real HA-default include layout in a private config dir.

    The reload handlers re-read configuration.yaml from hass.config.config_dir,
    so this is what the created entry is loaded from.
    """
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\n"
        "script: !include scripts.yaml\n"
        "scene: !include scenes.yaml\n",
        encoding="utf-8",
    )
    for name in ("automations.yaml", "scenes.yaml"):
        (tmp_path / name).write_text("[]\n", encoding="utf-8")
    (tmp_path / "scripts.yaml").write_text("{}\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
async def admin_user(hass: HomeAssistant):
    """A real active admin user, which ws_dispatch runs require_admin commands as."""
    return await hass.auth.async_create_user("Probe Admin", group_ids=["system-admin"])


def _data() -> PhoenixData:
    return PhoenixData(
        store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore(),
    )


def _token(tree: PermissionTree | None = None, **caps) -> TokenRecord:
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x", created_at=utcnow(),
        created_by="u", permissions=tree or PermissionTree(domains={}), **caps,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


def _text(content: dict) -> str:
    """Raw response text for assertion messages (a refusal is not JSON)."""
    return content["content"][0]["text"]


class TestCreateMaterialization:
    """Each test asserts on hass.states with NO await in between: any awaited
    settle (async_block_till_done) would mask the very race being probed."""

    async def test_automation_exists_the_moment_create_returns(self, hass: HomeAssistant):
        assert await async_setup_component(hass, "automation", {})
        await hass.async_block_till_done()

        result, outcome, _res = await _call_tool(
            "create_automation",
            {"config": {
                "alias": "Materialization Probe",
                "trigger": [{"platform": "state", "entity_id": "input_boolean.x"}],
                "action": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
            }},
            _token(cap_automation_write="allow"), hass, _data(),
        )
        assert outcome == "allowed", _text(result)

        state = hass.states.get("automation.materialization_probe")
        assert state is not None, "create_automation returned before the entity existed"
        assert state.state == "on"

    async def test_script_exists_the_moment_create_returns(self, hass: HomeAssistant):
        assert await async_setup_component(hass, "script", {})
        await hass.async_block_till_done()

        result, outcome, _res = await _call_tool(
            "create_script",
            {"script_id": "materialization_probe", "config": {
                "alias": "Materialization Probe",
                "sequence": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
            }},
            _token(PermissionTree(domains={"script": PermissionNode(state="GREEN")}), cap_script_write="allow"), hass, _data(),
        )
        assert outcome == "allowed", _text(result)

        assert hass.states.get("script.materialization_probe") is not None, (
            "create_script returned before the entity existed"
        )

    async def test_scene_exists_the_moment_create_returns(self, hass: HomeAssistant):
        assert await async_setup_component(hass, "scene", {})
        await hass.async_block_till_done()
        hass.states.async_set("input_boolean.x", "off")

        result, outcome, _res = await _call_tool(
            "create_scene",
            {"config": {
                "name": "Materialization Probe",
                "entities": {"input_boolean.x": "on"},
            }},
            # Scene writes additionally require WRITE on every member entity.
            _token(
                PermissionTree(domains={"input_boolean": PermissionNode(state="GREEN"), "scene": PermissionNode(state="GREEN")}),
                cap_scene_write="allow",
            ), hass, _data(),
        )
        assert outcome == "allowed", _text(result)

        assert hass.states.get("scene.materialization_probe") is not None, (
            "create_scene returned before the entity existed"
        )

    async def test_helper_exists_the_moment_create_returns(self, hass: HomeAssistant, admin_user):
        # Helpers involve no reload: create_helper dispatches HA's own
        # input_boolean/create collection command in process, so the question is
        # whether that command has registered the entity by the time it returns.
        assert await async_setup_component(hass, "input_boolean", {})
        await hass.async_block_till_done()

        result, outcome, _res = await _call_tool(
            "create_helper",
            {"helper_type": "input_boolean", "config": {"name": "Materialization Probe"}},
            _token(cap_helper_write="allow"), hass, _data(),
        )
        assert outcome == "allowed", _text(result)

        created = _json(result)["helper"]
        assert hass.states.get(f"input_boolean.{created['id']}") is not None, (
            "create_helper returned before the entity existed"
        )

    async def test_zone_exists_the_moment_create_returns(self, hass: HomeAssistant, admin_user):
        """The sensitive storage-helper path has the same synchronous lifecycle."""
        assert await async_setup_component(hass, "zone", {"zone": []})
        await hass.async_block_till_done()

        result, outcome, _res = await _call_tool(
            "create_helper",
            {
                "helper_type": "zone",
                "config": {
                    "name": "Materialization Zone",
                    "latitude": 10.0,
                    "longitude": 20.0,
                    "radius": 100,
                },
            },
            _token(
                PermissionTree(domains={"zone": PermissionNode(state="GREEN")}),
                cap_helper_write="allow",
            ),
            hass,
            _data(),
        )
        assert outcome == "allowed", _text(result)

        created = _json(result)["helper"]
        state = hass.states.get(f"zone.{created['id']}")
        assert state is not None, "create_helper returned before the zone existed"
        assert state.attributes["latitude"] == 10.0
        assert state.attributes["longitude"] == 20.0

    async def test_tag_exists_the_moment_create_returns(self, hass: HomeAssistant, admin_user):
        """Registry-backed tag naming must also be visible synchronously."""
        from homeassistant.helpers import entity_registry as er

        assert await async_setup_component(hass, "tag", {"tag": {}})
        await hass.async_block_till_done()

        result, outcome, _res = await _call_tool(
            "create_helper",
            {
                "helper_type": "tag",
                "config": {
                    "tag_id": "materialization-tag-id",
                    "name": "Materialization Tag",
                    "description": "Disposable probe",
                },
            },
            _token(
                PermissionTree(domains={"tag": PermissionNode(state="GREEN")}),
                cap_helper_write="allow",
            ),
            hass,
            _data(),
        )
        assert outcome == "allowed", _text(result)

        entity_id = er.async_get(hass).async_get_entity_id(
            "tag", "tag", "materialization-tag-id"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None, "create_helper returned before the tag existed"
        assert state.attributes["friendly_name"] == "Materialization Tag"

    async def test_person_exists_the_moment_create_returns(
        self, hass: HomeAssistant, admin_user,
    ):
        """Person storage lifecycle also materializes synchronously."""
        from homeassistant.helpers import entity_registry as er

        assert await async_setup_component(hass, "person", {"person": []})
        await hass.async_block_till_done()
        result, outcome, _res = await _call_tool(
            "create_helper",
            {"helper_type": "person", "config": {"name": "Materialization Person"}},
            _token(
                PermissionTree(domains={
                    "person": PermissionNode(state="GREEN"),
                    "device_tracker": PermissionNode(state="GREEN"),
                }),
                cap_helper_write="allow",
            ),
            hass,
            _data(),
        )
        assert outcome == "allowed", _text(result)
        helper_id = _json(result)["helper"]["id"]
        entity_id = er.async_get(hass).async_get_entity_id(
            "person", "person", helper_id
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None, "create_helper returned before the person existed"
        assert state.attributes["friendly_name"] == "Materialization Person"

    async def test_edit_is_visible_the_moment_edit_returns(self, hass: HomeAssistant):
        """The same property for a rename, which an agent reads back to confirm an edit."""
        assert await async_setup_component(hass, "automation", {})
        await hass.async_block_till_done()
        token = _token(cap_automation_write="allow")
        data = _data()

        created = _json((await _call_tool(
            "create_automation",
            {"config": {
                "alias": "Before Rename",
                "trigger": [{"platform": "state", "entity_id": "input_boolean.x"}],
                "action": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
            }},
            token, hass, data,
        ))[0])

        current = _json((await _call_tool(
            "get_automation", {"automation_id": created["id"]}, token, hass, data,
        ))[0])
        result, outcome, _res = await _call_tool(
            "edit_automation",
            {"automation_id": created["id"], "expected_hash": current["content_hash"], "config": {
                "alias": "After Rename",
                "trigger": [{"platform": "state", "entity_id": "input_boolean.x"}],
                "action": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
            }},
            token, hass, data,
        )
        assert outcome == "allowed", _text(result)

        state = hass.states.get("automation.before_rename")
        assert state is not None, "the automation vanished across the edit"
        assert state.attributes.get("friendly_name") == "After Rename", (
            "edit_automation returned before the reloaded config was visible"
        )


class TestNonEntityWriteVisibility:
    """Writes that produce no entity are visible to the matching read tool at once.

    A blueprint, configuration.yaml and a dashboard have nothing in hass.states
    to appear, so an agent that writes and reads back to confirm must not get
    the pre-write answer.
    """

    async def test_a_new_blueprint_is_listed_the_moment_create_returns(
        self, hass: HomeAssistant, admin_user,
    ):
        # HA keeps a DomainBlueprints cache, but list_blueprints re-scans the
        # directory, so a blueprint file written with no cache reset is visible
        # to the very next listing. There is nothing to reset after a write.
        assert await async_setup_component(hass, "automation", {})
        await hass.async_block_till_done()
        token = _token(cap_blueprint_write="allow", cap_automation_write="allow", cap_config_read="allow")
        data = _data()

        # List, create, list is the real agent sequence; the first read proves the
        # blueprint was absent beforehand.
        before, outcome, _res = await _call_tool("list_blueprints", {"domain": "automation"}, token, hass, data)
        assert outcome == "allowed", _text(before)
        assert "phx_probe.yaml" not in [b.get("path") for b in _json(before)["blueprints"]]

        source = (
            "blueprint:\n"
            "  name: Materialization Probe\n"
            "  domain: automation\n"
            "  input:\n"
            "    trigger_entity:\n"
            "      name: Trigger entity\n"
            "triggers:\n"
            "  - trigger: state\n"
            "    entity_id: !input trigger_entity\n"
            "actions:\n"
            "  - action: light.turn_on\n"
            "    target:\n"
            "      entity_id: light.kitchen\n"
            "mode: single\n"
        )
        result, outcome, _res = await _call_tool(
            "create_blueprint",
            {"domain": "automation", "path": "phx_probe.yaml", "content": source},
            token, hass, data,
        )
        assert outcome == "allowed", _text(result)

        listed, outcome, _res = await _call_tool("list_blueprints", {"domain": "automation"}, token, hass, data)
        assert outcome == "allowed"
        paths = [b.get("path") for b in _json(listed)["blueprints"]]
        assert "phx_probe.yaml" in paths, (
            f"create_blueprint returned before list_blueprints could see it: {paths}"
        )

    async def test_configuration_yaml_reads_back_what_was_just_written(self, hass: HomeAssistant):
        # set_yaml_config runs no reload (the reload family is a separate, gated
        # apply step), so this pins that the file read is not a stale cache.
        token = _token(cap_yaml_edit="allow")
        data = _data()
        # A superset of the seeded layout: set_yaml_config refuses a write that
        # drops a top-level key without declaring it.
        content = (
            "automation: !include automations.yaml\n"
            "script: !include scripts.yaml\n"
            "scene: !include scenes.yaml\n"
            "# phx probe marker\n"
        )
        result, outcome, _res = await _call_tool(
            "set_yaml_config", {"content": content}, token, hass, data,
        )
        assert outcome == "allowed", _text(result)

        read, outcome, _res = await _call_tool("get_yaml_config", {}, token, hass, data)
        assert outcome == "allowed"
        assert "phx probe marker" in _json(read)["content"]

    async def test_a_dashboard_layout_reads_back_what_was_just_written(
        self, hass: HomeAssistant,
    ):
        # async_get_lovelace_config can hand back the lovelace integration's LIVE
        # config object (which is why the card ops deep-copy before mutating), so
        # both the whole-blob write and a single-card op are checked, the latter
        # also for the previous card surviving the read.
        assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
        await hass.async_block_till_done()
        token = _token(cap_lovelace_write="allow")
        data = _data()

        result, outcome, _res = await _call_tool(
            "set_dashboard_config",
            {"config": {"views": [{"title": "Probe", "cards": [{"type": "markdown", "content": "one"}]}]}},
            token, hass, data,
        )
        assert outcome == "allowed", _text(result)

        read, outcome, _res = await _call_tool("get_dashboard_config", {}, token, hass, data)
        assert outcome == "allowed"
        cards = _json(read)["config"]["views"][0]["cards"]
        assert [c["content"] for c in cards] == ["one"], (
            "set_dashboard_config returned before get_dashboard_config could see it"
        )

        added, outcome, _res = await _call_tool(
            "add_dashboard_card",
            {"view_index": 0, "card": {"type": "markdown", "content": "two"}},
            token, hass, data,
        )
        assert outcome == "allowed", _text(added)

        read, outcome, _res = await _call_tool("get_dashboard_config", {}, token, hass, data)
        assert outcome == "allowed"
        cards = _json(read)["config"]["views"][0]["cards"]
        assert [c["content"] for c in cards] == ["one", "two"], (
            f"a card op was not immediately visible, or clobbered the layout: {cards}"
        )

    async def test_a_scoped_file_reads_back_what_was_just_written(
        self, hass: HomeAssistant, isolated_config_dir,
    ):
        (isolated_config_dir / "www").mkdir()
        token = _token(cap_filesystem="allow")
        data = _data()
        result, outcome, _res = await _call_tool(
            "write_file", {"path": "www/phx_probe.txt", "content": "probe marker"}, token, hass, data,
        )
        assert outcome == "allowed", _text(result)

        read, outcome, _res = await _call_tool(
            "read_file", {"path": "www/phx_probe.txt"}, token, hass, data,
        )
        assert outcome == "allowed"
        assert _json(read)["content"] == "probe marker"


async def test_delayed_real_scene_registration_and_metadata(hass, monkeypatch):
    """A real scene reload returns while its scheduled entity addition is held."""
    from homeassistant.helpers.entity_platform import EntityPlatform
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp import entity_registration

    assert await async_setup_component(hass, "scene", {})
    await hass.async_block_till_done()
    hass.states.async_set("input_boolean.x", "off")
    token = _token(
        PermissionTree(domains={name: PermissionNode(state="GREEN") for name in ("scene", "input_boolean")}),
        cap_scene_write="allow", cap_registry_write="allow",
    )
    data = _data()
    entered, release = asyncio.Event(), asyncio.Event()
    original = EntityPlatform.async_add_entities

    async def delayed(platform, *args, **kwargs):
        if platform.domain == "scene":
            entered.set()
            await release.wait()
        return await original(platform, *args, **kwargs)

    monkeypatch.setattr(EntityPlatform, "async_add_entities", delayed)
    monkeypatch.setattr(entity_registration, "AUTHORING_REGISTRATION_POLL_SECONDS", 0.001)
    task = asyncio.create_task(_call_tool(
        "create_scene", {"config": {"name": "Delayed Probe", "entities": {"input_boolean.x": "on"}}},
        token, hass, data,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0)
        assert not task.done(), "Write returned while real entity addition was still blocked"
        release.set()
        result, outcome, _ = await asyncio.wait_for(task, 2)
        assert outcome == "allowed"
        body = _json(result)
        entity_id = body["registration"]["entity_id"]
        assert body["registration"]["status"] == "observed"
        assert entity_id == "scene.delayed_probe"
        assert er.async_get(hass).async_get(entity_id).unique_id == body["scene_id"]
        state, outcome, _ = await _call_tool("get_state", {"entity_id": entity_id, "projection": {"kind": "compact"}}, token, hass, data)
        assert outcome == "allowed", _text(state)
        metadata, outcome, _ = await _call_tool("set_entity", {"entity_id": entity_id, "changes": {"name": "Metadata Probe"}}, token, hass, data)
        assert outcome == "allowed", _text(metadata)
        assert er.async_get(hass).async_get(entity_id).name == "Metadata Probe"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
