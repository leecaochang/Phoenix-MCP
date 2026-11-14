"""Authoring writes stay serialized and keep YAML tags, whatever happens around them.

Script and scene edits compare expected_hash with the entry the writing worker job finds, so two
writers presenting the same hash cannot both land. A request cancelled mid-write keeps the domain
lock until its worker has finished and still records its version, so a queued writer never works
from a file about to be replaced. The fallback path used when configuration.yaml cannot be read
keeps !env_var and !secret tags and refuses an editor file using !include.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry

from custom_components.phoenix_mcp import yaml_includes
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.tools import authoring
from custom_components.phoenix_mcp.version_store import VersionStore

pytestmark = pytest.mark.usefixtures("immediate_authoring_observation")


def _key() -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN"), "scene": PermissionNode(state="GREEN")}),
        cap_script_write="allow", cap_scene_write="allow", cap_automation_write="allow",
    )


def _data() -> PhoenixData:
    return PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path):
    hass.config.config_dir = str(tmp_path)
    for domain in ("automation", "script", "scene"):
        hass.services.async_register(domain, "reload", lambda call: None)
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    light = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "uid_a", config_entry=entry, suggested_object_id="a")
    hass.states.async_set(light.entity_id, "on", {})
    return tmp_path


def _routed(config_dir) -> None:
    (config_dir / "configuration.yaml").write_text(
        "script: !include scripts.yaml\nscene: !include scenes.yaml\nautomation: !include automations.yaml\n")
    (config_dir / "scripts.yaml").write_text(
        "alpha:\n  alias: Alpha\n  sequence:\n  - delay: 1\nbeta:\n  alias: Beta\n  sequence:\n  - delay: 1\n")
    (config_dir / "scenes.yaml").write_text("- id: '1'\n  name: Evening\n  entities:\n    light.a: 'on'\n")


class TestInLockCompare:
    async def test_two_script_edits_with_one_hash_land_once(self, hass, config_dir):
        _routed(config_dir)
        expected = content_hash(yaml_includes.read_entry(str(config_dir), "script", "alpha"))
        edits = [{"script_id": "alpha", "expected_hash": expected,
                  "config": {"alias": f"Alpha {n}", "sequence": [{"delay": n}]}} for n in (2, 3)]
        results = await asyncio.gather(*(authoring._execute_edit_script(e, _key(), hass, _data()) for e in edits))
        assert sorted(r[1] for r in results) == ["allowed", "invalid_request"]

    async def test_two_scene_edits_with_one_hash_land_once(self, hass, config_dir):
        _routed(config_dir)
        expected = content_hash(yaml_includes.read_entry(str(config_dir), "scene", "1"))
        edits = [{"scene_id": "1", "expected_hash": expected,
                  "config": {"name": f"Evening {state}", "entities": {"light.a": state}}} for state in ("on", "off")]
        results = await asyncio.gather(*(authoring._execute_edit_scene(e, _key(), hass, _data()) for e in edits))
        assert sorted(r[1] for r in results) == ["allowed", "invalid_request"]


class TestCancelledWrite:
    async def test_cancelled_edit_keeps_the_lock_until_its_worker_finishes(self, hass, config_dir):
        """The queued edit runs after the cancelled one's file replace, so both survive and both are recorded."""
        _routed(config_dir)
        data = _data()
        real_write = yaml_includes.write_utf8_file_atomic
        first = threading.Event()

        def slow_first_write(path, text):
            if not first.is_set():
                first.set()
                time.sleep(0.4)
            real_write(path, text)

        with patch.object(yaml_includes, "write_utf8_file_atomic", new=slow_first_write):
            one = asyncio.ensure_future(authoring._execute_edit_script(
                {"script_id": "alpha", "config": {"alias": "Alpha edited", "sequence": [{"delay": 2}]}},
                _key(), hass, data))
            while not first.is_set():
                await asyncio.sleep(0.01)
            one.cancel()
            two = asyncio.ensure_future(authoring._execute_edit_script(
                {"script_id": "beta", "config": {"alias": "Beta edited", "sequence": [{"delay": 3}]}},
                _key(), hass, data))
            with pytest.raises(asyncio.CancelledError):
                await one
            assert "Alpha edited" in (config_dir / "scripts.yaml").read_text()
            result = await two
        assert result[1] == "allowed", result
        final = (config_dir / "scripts.yaml").read_text()
        assert "Alpha edited" in final and "Beta edited" in final
        assert len(data.versions.list_for("script", "alpha")) == 1
        assert len(data.versions.list_for("script", "beta")) == 1

    async def test_a_hung_cancelled_write_releases_the_lock_after_its_bound(self, hass, config_dir):
        """Repeated cancels do not shorten the wait; at the bound the lock goes and no version is recorded."""
        _routed(config_dir)
        data = _data()
        real_write = yaml_includes.write_utf8_file_atomic
        started, release = threading.Event(), threading.Event()

        def hung_first_write(path, text):
            if not started.is_set():
                started.set()
                release.wait(5)
            real_write(path, text)

        try:
            with patch.object(yaml_includes, "write_utf8_file_atomic", new=hung_first_write), \
                    patch.object(authoring, "_CANCELLED_WRITE_WAIT_SECONDS", 0.3):
                one = asyncio.ensure_future(authoring._execute_edit_script(
                    {"script_id": "alpha", "config": {"alias": "Alpha edited", "sequence": [{"delay": 2}]}},
                    _key(), hass, data))
                while not started.is_set():
                    await asyncio.sleep(0.01)
                for _ in range(3):
                    one.cancel()
                    await asyncio.sleep(0.02)
                assert not one.done()
                two = asyncio.ensure_future(authoring._execute_edit_script(
                    {"script_id": "beta", "config": {"alias": "Beta edited", "sequence": [{"delay": 3}]}},
                    _key(), hass, data))
                async with asyncio.timeout(3):
                    with pytest.raises(asyncio.CancelledError):
                        await one
                    result = await two
                assert result[1] == "allowed", result
                assert not release.is_set()
        finally:
            release.set()
        # The stalled thread's own file replace still lands; the job that would record it is gone.
        async with asyncio.timeout(3):
            while "Alpha edited" not in (config_dir / "scripts.yaml").read_text():
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert data.versions.list_for("script", "alpha") == []
        assert len(data.versions.list_for("script", "beta")) == 1

    async def test_cancelled_before_the_lock_writes_nothing(self, hass, config_dir):
        """A request cancelled while queued for the lock never starts its write."""
        _routed(config_dir)
        before = (config_dir / "scripts.yaml").read_text()
        lock = authoring._get_script_lock(hass)
        await lock.acquire()
        try:
            task = asyncio.ensure_future(authoring._execute_edit_script(
                {"script_id": "alpha", "config": {"alias": "Never", "sequence": []}}, _key(), hass, _data()))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            lock.release()
        assert (config_dir / "scripts.yaml").read_text() == before


class TestFallbackTags:
    """No configuration.yaml: the editor file is rewritten directly."""

    async def test_edit_keeps_env_var_and_secret_tags(self, hass, config_dir):
        (config_dir / "scripts.yaml").write_text(
            "alpha:\n  alias: Alpha\n  sequence:\n  - delay: 1\n"
            "beta:\n  alias: Beta\n  sequence:\n  - action: notify.x\n    data:\n"
            "      token: !env_var MY_TOKEN\n      password: !secret pw\n")
        with patch.dict("os.environ", {"MY_TOKEN": "plaintext-env-value"}):
            result = await authoring._execute_edit_script(
                {"script_id": "alpha", "config": {"alias": "Alpha edited", "sequence": [{"delay": 2}]}},
                _key(), hass, _data())
        assert result[1] == "allowed", result
        text = (config_dir / "scripts.yaml").read_text()
        assert "Alpha edited" in text
        assert "!env_var MY_TOKEN" in text and "!secret pw" in text
        assert "plaintext-env-value" not in text

    async def test_scene_fallback_keeps_tags(self, hass, config_dir):
        (config_dir / "scenes.yaml").write_text(
            "- id: '1'\n  name: Evening\n  entities:\n    light.a: 'on'\n"
            "- id: '2'\n  name: Other\n  entities:\n    light.a:\n      state: 'on'\n      note: !secret note\n")
        result = await authoring._execute_edit_scene(
            {"scene_id": "1", "config": {"name": "Evening edited", "entities": {"light.a": "off"}}}, _key(), hass, _data())
        assert result[1] == "allowed", result
        assert "!secret note" in (config_dir / "scenes.yaml").read_text()

    async def test_automation_delete_fallback_keeps_tags(self, hass, config_dir):
        (config_dir / "automations.yaml").write_text(
            "- id: a1\n  alias: One\n  triggers: []\n  actions: []\n"
            "- id: a2\n  alias: Two\n  triggers: []\n  actions:\n  - action: notify.x\n"
            "    data:\n      token: !env_var MY_TOKEN\n")
        with patch.dict("os.environ", {"MY_TOKEN": "plaintext-env-value"}):
            result = await authoring._tool_delete_automation({"automation_id": "a1"}, _key(), hass, _data())
        assert result[1] == "allowed", result
        text = (config_dir / "automations.yaml").read_text()
        assert "a1" not in text and "!env_var MY_TOKEN" in text and "plaintext-env-value" not in text

    async def test_edit_script_refuses_an_include_in_the_editor_file(self, hass, config_dir):
        (config_dir / "scripts.yaml").write_text(
            "alpha:\n  alias: Alpha\n  sequence:\n  - delay: 1\nbeta: !include beta.yaml\n")
        (config_dir / "beta.yaml").write_text("alias: Beta\nsequence: []\n")
        before = (config_dir / "scripts.yaml").read_text()
        result = await authoring._execute_edit_script(
            {"script_id": "alpha", "config": {"alias": "Alpha edited", "sequence": []}}, _key(), hass, _data())
        assert result[1] == "denied"
        assert yaml_includes.MSG_INCLUDE_REFUSAL.format(name="scripts.yaml") in result[0]["content"][0]["text"]
        assert (config_dir / "scripts.yaml").read_text() == before

    async def test_fallback_compares_expected_hash_in_the_job(self, hass, config_dir):
        (config_dir / "scripts.yaml").write_text("alpha:\n  alias: Alpha\n  sequence:\n  - delay: 1\n")
        before = (config_dir / "scripts.yaml").read_text()
        result = await authoring._execute_edit_script(
            {"script_id": "alpha", "expected_hash": "0" * 64, "config": {"alias": "X", "sequence": []}},
            _key(), hass, _data())
        assert result[1] == "invalid_request"
        assert (config_dir / "scripts.yaml").read_text() == before
