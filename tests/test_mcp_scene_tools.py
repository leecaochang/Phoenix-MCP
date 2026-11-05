"""Tests for scene CRUD MCP tools (create/edit/delete_scene, list_scenes).

Scene writes mirror the automation/script YAML pattern: Confirm-gated on
cap_scene_write, with every referenced member entity required to be WRITE-
accessible to the access key. list_scenes is a cap_registry_read read.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp.tool_catalog import _EXECUTOR_REGISTRY
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.tools.authoring import _read_scenes_yaml
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree, AccessKeyRecord

pytestmark = pytest.mark.usefixtures("immediate_authoring_observation")


def _access_key(permissions: PermissionTree | None = None, **caps) -> AccessKeyRecord:
    tree = permissions or PermissionTree(domains={
        "light": PermissionNode(state="GREEN"),
        "scene": PermissionNode(state="GREEN"),
    })
    base = {"cap_scene_write": "allow", "cap_registry_read": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


@pytest.fixture(autouse=True)
def isolated_config_dir(hass: HomeAssistant, tmp_path):
    """Point scene executors at a private config dir with the HA-default include layout.

    The executors resolve configuration.yaml (yaml_includes), and the shared
    testing config dir is polluted by other tests.
    """
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\n"
        "script: !include scripts.yaml\n"
        "scene: !include scenes.yaml\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def scene_env(hass: HomeAssistant):
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    e = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "uid_k", config_entry=entry, suggested_object_id="kitchen")
    hass.states.async_set(e.entity_id, "on", {})
    hass.states.async_set("sensor.secret", "1", {})  # ungranted
    # Dummy scene.reload so _execute's reload call succeeds.
    hass.services.async_register("scene", "reload", lambda call: None)
    return hass


async def _call(name, args, access_key, hass):
    return await _call_tool(name, args, access_key, hass, MagicMock())


class TestExecutorRegistration:
    def test_scene_executors_registered(self):
        for name in ("create_scene", "edit_scene", "delete_scene"):
            assert name in _EXECUTOR_REGISTRY


class TestCreateScene:
    async def test_deny_without_cap(self, hass, scene_env):
        content, outcome, _ = await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}},
            _access_key(cap_scene_write="deny"), hass)
        assert outcome == "denied"

    async def test_create_writes_yaml(self, hass, scene_env):
        content, outcome, _ = await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["config"]["name"] == "Movie"
        assert body["scene_id"].startswith("phoenix_mcp_")
        stored = _read_scenes_yaml(hass.config.path("scenes.yaml"))
        assert any(s["id"] == body["scene_id"] for s in stored)

    async def test_rejects_unwritable_member(self, hass, scene_env):
        content, outcome, _ = await _call(
            "create_scene", {"config": {"name": "Bad", "entities": {"sensor.secret": "1"}}}, _access_key(), hass)
        assert outcome == "denied"
        assert "sensor.secret" in content["content"][0]["text"]

    async def test_invalid_config(self, hass, scene_env):
        content, outcome, _ = await _call("create_scene", {"config": {"name": "NoEntities"}}, _access_key(), hass)
        assert outcome == "invalid_request"

    async def test_invalid_config_confirm_mode_rejected_before_pending(self, hass, scene_env):
        """An invalid scene config fails before any approval is created, even in confirm mode."""
        content, outcome, _ = await _call(
            "create_scene", {"config": {"name": "NoEntities"}}, _access_key(cap_scene_write="confirm"), hass)
        assert outcome == "invalid_request"


class TestEditScene:
    async def test_edit_by_id(self, hass, scene_env):
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass))[0])
        sid = created["scene_id"]
        content, outcome, _ = await _call(
            "edit_scene", {"scene_id": sid, "config": {"name": "Movie2", "entities": {"light.kitchen": "off"}}}, _access_key(), hass)
        assert outcome == "allowed"
        stored = {s["id"]: s for s in _read_scenes_yaml(hass.config.path("scenes.yaml"))}
        assert stored[sid]["name"] == "Movie2"

    async def test_invalid_config_confirm_mode_rejected_before_pending(self, hass, scene_env):
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass))[0])
        sid = created["scene_id"]
        content, outcome, _ = await _call(
            "edit_scene", {"scene_id": sid, "config": {"name": "NoEntities"}}, _access_key(cap_scene_write="confirm"), hass)
        assert outcome == "invalid_request"

    async def test_edit_unknown(self, hass, scene_env):
        content, outcome, _ = await _call(
            "edit_scene", {"scene_id": "nope", "config": {"name": "x", "entities": {"light.kitchen": "on"}}}, _access_key(), hass)
        assert outcome == "denied"

    async def test_missing_and_out_of_scope_same_reason(self, hass, scene_env):
        # No oracle: an out-of-scope scene gives the same generic reason as a
        # missing one (the write-scope hint) and never leaks the existing member
        # name. The caller can write sensor.secret (so the new-member check
        # passes) but not the existing member light.kitchen.
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass))[0])
        sid = created["scene_id"]
        attacker = _access_key(PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}))
        out_of_scope = (await _call(
            "edit_scene", {"scene_id": sid, "config": {"name": "x", "entities": {"sensor.secret": "1"}}}, attacker, hass))[0]["content"][0]["text"]
        missing = (await _call(
            "edit_scene", {"scene_id": "nope", "config": {"name": "x", "entities": {"sensor.secret": "1"}}}, attacker, hass))[0]["content"][0]["text"]
        suffix = ", or it controls entities outside your write scope."
        assert out_of_scope.endswith(suffix)
        assert missing.endswith(suffix)
        assert "light.kitchen" not in out_of_scope  # existing member not leaked

    async def test_edit_out_of_scope_existing_members_denied(self, hass, scene_env):
        # Owner creates a scene over light.kitchen; an attacker who can write a new
        # member (sensor.secret) but NOT the existing member light.kitchen cannot
        # replace it: the existing-member ownership check denies.
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass))[0])
        sid = created["scene_id"]
        attacker = _access_key(PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}))
        content, outcome, _ = await _call(
            "edit_scene", {"scene_id": sid, "config": {"name": "Hijacked", "entities": {"sensor.secret": "1"}}}, attacker, hass)
        assert outcome == "denied"
        # Unchanged on disk.
        stored = {s["id"]: s for s in _read_scenes_yaml(hass.config.path("scenes.yaml"))}
        assert stored[sid]["name"] == "Movie"


class TestDeleteScene:
    async def test_delete(self, hass, scene_env):
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass))[0])
        sid = created["scene_id"]
        content, outcome, _ = await _call("delete_scene", {"scene_id": sid}, _access_key(), hass)
        assert outcome == "allowed"
        assert all(s["id"] != sid for s in _read_scenes_yaml(hass.config.path("scenes.yaml")))

    async def test_delete_unknown(self, hass, scene_env):
        _, outcome, _ = await _call("delete_scene", {"scene_id": "nope"}, _access_key(), hass)
        assert outcome == "denied"

    async def test_delete_out_of_scope_existing_members_denied(self, hass, scene_env):
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass))[0])
        sid = created["scene_id"]
        attacker = _access_key(PermissionTree(domains={"scene": PermissionNode(state="GREEN")}))
        _, outcome, _ = await _call("delete_scene", {"scene_id": sid}, attacker, hass)
        assert outcome == "denied"
        # Still present on disk.
        assert any(s["id"] == sid for s in _read_scenes_yaml(hass.config.path("scenes.yaml")))

    async def test_delete_removes_registry_orphan(self, hass, scene_env):
        # Deleting a scene must remove its entity-registry entry.
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _access_key(), hass))[0])
        sid = created["scene_id"]
        reg = er.async_get(hass)
        scene_entry = reg.async_get_or_create("scene", "homeassistant", sid, suggested_object_id="movie")
        assert reg.async_get(scene_entry.entity_id) is not None
        _, outcome, _ = await _call("delete_scene", {"scene_id": sid}, _access_key(), hass)
        assert outcome == "allowed"
        assert reg.async_get(scene_entry.entity_id) is None  # orphan purged


class TestListScenes:
    async def test_lists_accessible(self, hass, scene_env):
        hass.states.async_set("scene.movie", "scening", {"friendly_name": "Movie", "id": "phx_abc"})
        content, outcome, _ = await _call("list_scenes", {}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        sc = next(s for s in body["scenes"] if s["entity_id"] == "scene.movie")
        assert sc["scene_id"] == "phx_abc"

    async def test_deny_without_cap(self, hass, scene_env):
        _, outcome, _ = await _call("list_scenes", {}, _access_key(cap_registry_read="deny"), hass)
        assert outcome == "denied"

    async def test_entity_id_finds_the_scenes_that_include_it(self, hass, scene_env):
        for light in ("light.studio", "light.porch", "switch.fan"):
            hass.states.async_set(light, "on")
        hass.states.async_set("scene.focus", "scening", {"friendly_name": "Focus", "id": "focus",
                                                         "entity_id": ["light.studio", "switch.fan"]})
        hass.states.async_set("scene.night", "scening", {"friendly_name": "Night", "id": "night",
                                                         "entity_id": ["light.porch"]})

        async def scenes(entity_id):
            content, outcome, _ = await _call("list_scenes", {"entity_id": entity_id}, _access_key(), hass)
            assert outcome == "allowed"
            return {row["entity_id"]: row.get("members") for row in _json(content)["scenes"]}

        # Members the key cannot read (switch.fan) are neither listed nor matched,
        # so a hidden entity answers exactly like one no scene uses.
        assert await scenes("light.studio") == {"scene.focus": ["light.studio"]}
        assert await scenes(["light.studio", "light.porch"]) == {"scene.focus": ["light.studio"], "scene.night": ["light.porch"]}
        assert await scenes("switch.fan") == await scenes("switch.nothing") == {}
        content, _, _ = await _call("list_scenes", {}, _access_key(), hass)
        assert all("members" not in row for row in _json(content)["scenes"])


async def test_listings_fit_one_result_and_name_the_rest(hass, scene_env):
    """Listings without paging grew without bound on a large home."""
    from unittest.mock import patch as _patch
    from custom_components.phoenix_mcp import tool_common

    for index in range(40):
        hass.states.async_set(f"scene.s{index:02d}", "scening", {"friendly_name": "x" * 60, "id": f"id{index}"})
    with _patch.object(tool_common, "TOOL_RESULT_MAX_BYTES", 2000):
        content, outcome, _ = await _call("list_scenes", {}, _access_key(), hass)
    body = _json(content)
    assert outcome == "allowed" and 0 < body["count"] < 40
    listed = [row["entity_id"] for row in body["scenes"]]
    assert listed + body["not_listed"] == [f"scene.s{index:02d}" for index in range(40)]
