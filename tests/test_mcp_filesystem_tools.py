"""Tests for the scoped filesystem MCP tools (list_files, read_file).

Both gate on cap_filesystem, a read capability. Access is confined to www/,
themes/ and custom_templates/ under the config dir; traversal, symlinks that
leave the allowlist, and sibling directories sharing an allowed prefix all read
as not found.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.const import MAX_FILE_BYTES
from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.access_key_store import PermissionTree, AccessKeyRecord


def _access_key(**caps) -> AccessKeyRecord:
    base = {"cap_filesystem": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=PermissionTree(), **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, access_key, hass):
    return await _call_tool(name, args, access_key, hass, _data())


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


class TestReadFile:
    async def test_deny(self, hass, fs_env):
        _, outcome, _ = await _call("read_file", {"path": "www/hello.txt"}, _access_key(cap_filesystem="deny"), hass)
        assert outcome == "denied"

    async def test_read(self, hass, fs_env):
        content, outcome, _ = await _call("read_file", {"path": "www/hello.txt"}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["content"] == "hi there"

    async def test_read_reports_content_hash(self, hass, fs_env):
        content, _, _ = await _call("read_file", {"path": "www/hello.txt"}, _access_key(), hass)
        assert _json(content)["content_hash"] == content_hash("hi there")

    async def test_traversal_refused(self, hass, fs_env):
        content, outcome, _ = await _call("read_file", {"path": "www/../secrets.yaml"}, _access_key(), hass)
        assert outcome == "not_found"  # outside allowlist looks identical to missing

    async def test_missing(self, hass, fs_env):
        _, outcome, _ = await _call("read_file", {"path": "www/nope.txt"}, _access_key(), hass)
        assert outcome == "not_found"

    async def test_oversized_file_is_refused_without_its_content(self, hass, fs_env):
        path = hass.config.path("www/large.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * (MAX_FILE_BYTES + 1))
        content, outcome, _ = await _call("read_file", {"path": "www/large.txt"}, _access_key(), hass)
        assert outcome == "invalid_request"
        text = content["content"][0]["text"]
        assert "maximum readable size" in text
        assert "xxx" not in text
        # The same file at the cap reads in full.
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * MAX_FILE_BYTES)
        content, outcome, _ = await _call("read_file", {"path": "www/large.txt"}, _access_key(), hass)
        assert outcome == "allowed"
        assert len(_json(content)["content"]) == MAX_FILE_BYTES


class TestListFiles:
    async def test_root_lists_allowed_dirs(self, hass, fs_env):
        content, outcome, _ = await _call("list_files", {}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["directories"] == ["www", "themes", "custom_templates"]

    async def test_list_dir(self, hass, fs_env):
        content, outcome, _ = await _call("list_files", {"path": "www"}, _access_key(), hass)
        assert outcome == "allowed"
        names = [e["name"] for e in _json(content)["entries"]]
        assert "hello.txt" in names

    @pytest.mark.parametrize("path", ["www/missing", "www/..", "../", "www/hello.txt"])
    async def test_outside_or_absent_directory_is_not_found(self, hass, fs_env, path):
        content, outcome, _ = await _call("list_files", {"path": path}, _access_key(), hass)
        assert outcome == "not_found"
        assert "secrets.yaml" not in str(content)

    async def test_deny(self, hass, fs_env):
        _, outcome, _ = await _call("list_files", {"path": "www"}, _access_key(cap_filesystem="deny"), hass)
        assert outcome == "denied"


@pytest.mark.parametrize("name,args", [
    ("read_file", {"path": "www/escape/secret.txt"}),
    ("list_files", {"path": "www/escape"}),
])
async def test_symlink_below_allowed_root_cannot_escape_config_jail(hass, fs_env, tmp_path, name, args):
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}"
    outside.mkdir()
    path = outside / "secret.txt"
    path.write_text("outside-secret")
    os.symlink(outside, hass.config.path("www/escape"), target_is_directory=True)
    try:
        result, outcome, _ = await _call(name, args, _access_key(), hass)
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
    result, outcome, _ = await _call("read_file", {"path": path}, _access_key(), hass)
    assert outcome == "not_found"
    assert "private fixture" not in str(result)
    _, allowed, _ = await _call("read_file", {"path": "www/hello.txt"}, _access_key(), hass)
    assert allowed == "allowed"
