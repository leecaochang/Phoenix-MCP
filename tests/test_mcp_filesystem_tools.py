"""Tests for the scoped filesystem MCP tools (list_files, read_file, write_file).

All gate on cap_filesystem; write_file is Confirm-eligible. Access is confined to
www/, themes/, custom_templates/ under the config dir; traversal is refused.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery
from custom_components.phoenix_mcp.mcp_view import (
    _EXECUTOR_REGISTRY,
    _call_tool,
    _execute_write_file,
)
from custom_components.phoenix_mcp.token_store import PermissionTree, TokenRecord
from custom_components.phoenix_mcp.tools import config_files


def _token(**caps) -> TokenRecord:
    base = {"cap_filesystem": "allow"}
    base.update(caps)
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u", permissions=PermissionTree(), **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass):
    return await _call_tool(name, args, token, hass, _data())


def _data():
    value = MagicMock()
    value.config_recovery = ConfigurationRecovery()
    return value


@pytest.fixture
def fs_env(hass: HomeAssistant):
    www = os.path.join(hass.config.config_dir, "www")
    os.makedirs(www, exist_ok=True)
    with open(os.path.join(www, "hello.txt"), "w", encoding="utf-8") as f:
        f.write("hi there")
    # A secret outside the allowlist, to prove traversal is refused.
    with open(os.path.join(hass.config.config_dir, "secrets.yaml"), "w", encoding="utf-8") as f:
        f.write("token: supersecret")
    return hass


class TestExecutorRegistration:
    def test_write_file_registered(self):
        assert "write_file" in _EXECUTOR_REGISTRY


class TestReadFile:
    async def test_deny(self, hass, fs_env):
        _, outcome, _ = await _call("read_file", {"path": "www/hello.txt"}, _token(cap_filesystem="deny"), hass)
        assert outcome == "denied"

    async def test_read(self, hass, fs_env):
        content, outcome, _ = await _call("read_file", {"path": "www/hello.txt"}, _token(), hass)
        assert outcome == "allowed"
        assert _json(content)["content"] == "hi there"

    async def test_read_reports_content_hash(self, hass, fs_env):
        content, _, _ = await _call("read_file", {"path": "www/hello.txt"}, _token(), hass)
        assert _json(content)["content_hash"] == content_hash("hi there")

    async def test_traversal_refused(self, hass, fs_env):
        content, outcome, _ = await _call("read_file", {"path": "www/../secrets.yaml"}, _token(), hass)
        assert outcome == "not_found"  # outside allowlist looks identical to missing

    async def test_missing(self, hass, fs_env):
        _, outcome, _ = await _call("read_file", {"path": "www/nope.txt"}, _token(), hass)
        assert outcome == "not_found"


class TestWriteFile:
    async def test_session_counter_source_is_written_with_recovery(self, hass, fs_env):
        source = "class DeferredCard {\n  constructor() {\n    this.session = 0;\n  }\n}\n"
        data = _data()
        result, outcome, _ = await _call_tool(
            "write_file", {"path": "www/counter.js", "content": source}, _token(), hass, data)
        assert outcome == "allowed"
        with open(hass.config.path("www/counter.js"), encoding="utf-8") as stream:
            assert stream.read() == source
        record = data.config_recovery.get(_json(result)["recovery_id"])
        assert record["source"]["content"] == source
        assert record["phase"] == "verified"

    @pytest.mark.parametrize("existing", [False, True])
    async def test_secret_snapshot_refused_before_approval(self, hass, fs_env, existing):
        path = hass.config.path("www/counter.js")
        secret = 'this.session = "private-session";'
        if existing:
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(secret)
        args = {"path": "www/counter.js", "content": "this.session = 0;" if existing else secret}
        with patch.object(config_files, "_gate", new_callable=AsyncMock) as gate:
            result, outcome, _ = await _call("write_file", args, _token(cap_filesystem="confirm"), hass)
        assert outcome == "invalid_request"
        gate.assert_not_awaited()
        message = result["content"][0]["text"]
        assert "line 1" in message and "Approval cannot override" in message
        assert "private-session" not in message
        assert os.path.exists(path) == existing
        if existing:
            with open(path, encoding="utf-8") as stream:
                assert stream.read() == secret

    async def test_counter_source_reaches_confirm_gate(self, hass, fs_env):
        pending = ({"status": "pending_approval"}, "pending_approval", "write_file")
        with patch.object(config_files, "_gate", new_callable=AsyncMock, return_value=pending) as gate:
            result = await _call("write_file", {"path": "www/counter.js", "content": "this.session = 0;"},
                                 _token(cap_filesystem="confirm"), hass)
        assert result == pending
        gate.assert_awaited_once()
        assert not os.path.exists(hass.config.path("www/counter.js"))

    async def test_approved_executor_rechecks_existing_secret(self, hass, fs_env):
        path = hass.config.path("www/hello.txt")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("password: existing-secret")
        data = _data()
        result, outcome, _ = await _execute_write_file(
            {"path": "www/hello.txt", "content": "this.session = 0;"}, _token(), hass, data)
        assert outcome == "invalid_request" and result["isError"]
        assert data.config_recovery.records() == []
        with open(path, encoding="utf-8") as stream:
            assert stream.read() == "password: existing-secret"

    async def test_unreadable_recovery_source_refused_before_approval(self, hass, fs_env):
        with patch.object(config_files, "_read_text_capped", side_effect=ValueError("file too large")), patch.object(
            config_files, "_gate", new_callable=AsyncMock,
        ) as gate:
            result, outcome, _ = await _call(
                "write_file", {"path": "www/hello.txt", "content": "ok"},
                _token(cap_filesystem="confirm"), hass)
        assert outcome == "invalid_request" and "read completely" in result["content"][0]["text"]
        gate.assert_not_awaited()

    async def test_force_argument_cannot_bypass_snapshot_protection(self, hass, fs_env):
        with patch.object(config_files, "_gate", new_callable=AsyncMock) as gate:
            result, outcome, _ = await _call(
                "write_file", {"path": "www/secret.js", "content": "password: secret", "force": True},
                _token(cap_filesystem="confirm"), hass)
        assert outcome == "invalid_request" and result["isError"]
        gate.assert_not_awaited()

    async def test_deny(self, hass, fs_env):
        _, outcome, _ = await _call("write_file", {"path": "themes/x.yaml", "content": "a"}, _token(cap_filesystem="deny"), hass)
        assert outcome == "denied"

    async def test_write_creates_file(self, hass, fs_env):
        content, outcome, _ = await _call(
            "write_file", {"path": "themes/mytheme.yaml", "content": "a: 1"}, _token(), hass)
        assert outcome == "allowed"
        assert isinstance(_json(content).get("recovery_id"), str)
        written = os.path.join(hass.config.config_dir, "themes", "mytheme.yaml")
        assert os.path.isfile(written)
        with open(written, encoding="utf-8") as f:
            assert f.read() == "a: 1"

    async def test_traversal_refused(self, hass, fs_env):
        content, outcome, _ = await _call("write_file", {"path": "../evil.yaml", "content": "x"}, _token(), hass)
        assert outcome == "denied"
        assert not os.path.isfile(os.path.join(hass.config.config_dir, "evil.yaml"))

    async def test_non_string_content(self, hass, fs_env):
        _, outcome, _ = await _call("write_file", {"path": "themes/x.yaml", "content": 123}, _token(), hass)
        assert outcome == "invalid_request"

    async def test_non_string_content_confirm_mode_rejected_before_pending(self, hass, fs_env):
        """A doomed write fails before any approval is created, even in confirm mode."""
        _, outcome, _ = await _call(
            "write_file", {"path": "themes/x.yaml", "content": 123}, _token(cap_filesystem="confirm"), hass)
        assert outcome == "invalid_request"

    async def test_traversal_refused_confirm_mode_rejected_before_pending(self, hass, fs_env):
        _, outcome, _ = await _call(
            "write_file", {"path": "../evil.yaml", "content": "x"}, _token(cap_filesystem="confirm"), hass)
        assert outcome == "denied"
        assert not os.path.isfile(os.path.join(hass.config.config_dir, "evil.yaml"))

    async def test_matching_hash_writes(self, hass, fs_env):
        h = _json((await _call("read_file", {"path": "www/hello.txt"}, _token(), hass))[0])["content_hash"]
        _, outcome, _ = await _call(
            "write_file", {"path": "www/hello.txt", "content": "updated", "expected_hash": h}, _token(), hass)
        assert outcome == "allowed"
        with open(os.path.join(hass.config.config_dir, "www", "hello.txt"), encoding="utf-8") as f:
            assert f.read() == "updated"

    async def test_stale_hash_conflicts_and_does_not_write(self, hass, fs_env):
        _, outcome, _ = await _call(
            "write_file",
            {"path": "www/hello.txt", "content": "clobber", "expected_hash": content_hash("what I read earlier")},
            _token(), hass)
        assert outcome == "invalid_request"
        with open(os.path.join(hass.config.config_dir, "www", "hello.txt"), encoding="utf-8") as f:
            assert f.read() == "hi there"  # untouched

    async def test_executor_stale_hash_conflict_at_apply_time(self, hass, fs_env):
        _, outcome, _ = await _execute_write_file(
            {"path": "www/hello.txt", "content": "new", "expected_hash": content_hash("stale")},
            _token(), hass, _data())
        assert outcome == "invalid_request"
        with open(os.path.join(hass.config.config_dir, "www", "hello.txt"), encoding="utf-8") as f:
            assert f.read() == "hi there"


class TestListFiles:
    async def test_root_lists_allowed_dirs(self, hass, fs_env):
        content, outcome, _ = await _call("list_files", {}, _token(), hass)
        assert outcome == "allowed"
        assert _json(content)["directories"] == ["www", "themes", "custom_templates"]

    async def test_list_dir(self, hass, fs_env):
        content, outcome, _ = await _call("list_files", {"path": "www"}, _token(), hass)
        assert outcome == "allowed"
        names = [e["name"] for e in _json(content)["entries"]]
        assert "hello.txt" in names


@pytest.mark.parametrize("name,args", [
    ("read_file", {"path": "www/escape/secret.txt"}),
    ("write_file", {"path": "www/escape/secret.txt", "content": "changed"}),
    ("list_files", {"path": "www/escape"}),
])
async def test_symlink_below_allowed_root_cannot_escape_config_jail(hass, fs_env, tmp_path, name, args):
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}"
    outside.mkdir()
    path = outside / "secret.txt"
    path.write_text("outside-secret")
    os.symlink(outside, hass.config.path("www/escape"), target_is_directory=True)
    try:
        result, outcome, _ = await _call(name, args, _token(), hass)
        assert outcome != "allowed"
        assert "outside-secret" not in str(result)
        assert path.read_text() == "outside-secret"
    finally:
        path.unlink()
        outside.rmdir()


@pytest.mark.parametrize("path", ["www2/private.txt", "www/../www2/private.txt"])
async def test_config_sibling_with_allowed_prefix_is_outside_jail(hass, fs_env, path):
    from pathlib import Path

    sibling = Path(hass.config.path("www2"))
    sibling.mkdir()
    (sibling / "private.txt").write_text("private fixture")
    result, outcome, _ = await _call("read_file", {"path": path}, _token(), hass)
    assert outcome == "not_found"
    assert "private fixture" not in str(result)
    _, allowed, _ = await _call("read_file", {"path": "www/hello.txt"}, _token(), hass)
    assert allowed == "allowed"
