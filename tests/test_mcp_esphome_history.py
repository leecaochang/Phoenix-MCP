"""get_esphome_history: the Device Builder's version history, masked and scoped.

Old versions are the leak path a current-file scrub cannot close: a credential
rotated out of secrets.yaml and every current file survives in history. These
tests pin that every version is masked whole, that the add-on's raw diff and
restore are never used, and that deleted files keep their device's scope.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.esphome_builder import BuilderCommandError
from tests.test_mcp_esphome_firmware_tools import FakeBuilder
from tests.test_mcp_esphome_tools import (
    _call,
    _device_info,
    _esphome_entry,
    _json,
    _runtime,
    _yaml_access_key,
)

_DASHBOARD_PATCH = "custom_components.phoenix_mcp.tools.esphome._esphome_dashboard"
_COMMAND_PATCH = "custom_components.phoenix_mcp.tools.esphome.async_builder_command"
FILE = "rf-blaster1.yaml"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
ROTATED = "oldpassword99"
OLD_VERSION = f'''esphome:
  name: rf-blaster1
wifi:
  ssid: !secret wifi_ssid
  password: "{ROTATED}"
'''
VERSIONS = [
    {"sha": NEW_SHA, "short_sha": "bbbbbbb", "author": "ESPHome Device Builder",
     "timestamp": 1761700000, "message": "Edit rf-blaster1.yaml"},
    {"sha": OLD_SHA, "short_sha": "aaaaaaa", "author": "Jane Operator",
     "timestamp": 1761600000, "message": "Add rf-blaster1.yaml"},
]


def _dash():
    return SimpleNamespace(url="http://127.0.0.1:6052", data={}, last_update_success=True)


def _builder(**over):
    replies = {
        "version_history/list_versions": VERSIONS,
        "version_history/get_version": lambda args: (
            {"content": OLD_VERSION} if args["sha"] == OLD_SHA else {"content": "esphome:\n  name: rf-blaster1\n"}),
        "version_history/list_deleted": [],
    }
    replies.update(over)
    return FakeBuilder(**replies)


async def _run(args, hass, builder, access_key=None):
    with patch(_DASHBOARD_PATCH, return_value=_dash()), patch(_COMMAND_PATCH, builder):
        return await _call("get_esphome_history", args, access_key or _yaml_access_key(), hass)


class TestVersions:
    async def test_lists_versions_with_origin_but_never_the_author(self, hass, esphome_dir):
        content, outcome, resource = await _run({"file": FILE}, hass, _builder())
        body = _json(content)
        assert outcome == "allowed" and resource == f"esphome:{FILE}"
        assert [v["origin"] for v in body["versions"]] == ["device_builder", "other"]
        assert "Jane Operator" not in content["content"][0]["text"]
        assert body["exists"] is True and body["total"] == 2

    async def test_a_message_naming_the_file_keeps_the_name(self, hass, esphome_dir):
        """porch-light.yaml reads like the entity light.yaml; the file's own name is not redacted."""
        versions = [dict(VERSIONS[0], message="Edit porch-light.yaml and light.secret_room")]
        content, _, _ = await _run({"file": "porch-light.yaml"}, hass,
                                   _builder(**{"version_history/list_versions": versions}))
        message = _json(content)["versions"][0]["message"]
        assert message.startswith("Edit porch-light.yaml and ")
        assert "light.secret_room" not in message

    async def test_a_rotated_credential_in_an_old_version_is_masked(self, hass, esphome_dir):
        """The current secrets.yaml and file no longer hold it, so only whole-text masking catches it."""
        content, outcome, _ = await _run({"file": FILE, "sha": OLD_SHA, "view": "content"}, hass, _builder())
        body = _json(content)
        assert outcome == "allowed"
        assert ROTATED not in content["content"][0]["text"]
        assert "__PHOENIX_REDACTED__wifi.password__" in body["content"]
        assert body["redacted_paths"] == ["wifi.password"]

    async def test_diffs_are_computed_from_masked_text_and_get_diff_is_never_sent(self, hass, esphome_dir):
        builder = _builder()
        content, _, _ = await _run({"file": FILE, "sha": OLD_SHA}, hass, builder)
        body = _json(content)
        assert body["compared_to"] == "current"
        assert "__PHOENIX_REDACTED__wifi.password__" in body["diff"]
        assert ROTATED not in content["content"][0]["text"]
        assert "REALAPIKEY123456=" not in content["content"][0]["text"]
        assert "version_history/get_diff" not in builder.commands
        assert "version_history/restore" not in builder.commands

    async def test_compare_to_diffs_two_versions(self, hass, esphome_dir):
        content, _, _ = await _run({"file": FILE, "sha": OLD_SHA, "compare_to": NEW_SHA}, hass, _builder())
        body = _json(content)
        assert body["compared_to"] == NEW_SHA and "-wifi:" in body["diff"]

    async def test_an_unparseable_version_fails_closed(self, hass, esphome_dir):
        builder = _builder(**{"version_history/get_version": {"content": f'pw: "{ROTATED}"\nbad: [x\n'}})
        content, outcome, _ = await _run({"file": FILE, "sha": OLD_SHA, "view": "content"}, hass, builder)
        assert outcome == "invalid_request" and ROTATED not in content["content"][0]["text"]

    async def test_the_delete_commit_points_at_the_version_before(self, hass, esphome_dir):
        builder = _builder(**{"version_history/get_version": BuilderCommandError("not_found", "absent")})
        content, outcome, _ = await _run({"file": FILE, "sha": OLD_SHA}, hass, builder)
        assert outcome == "not_found" and "version before it" in content["content"][0]["text"]

    @pytest.mark.parametrize("args", [
        {"file": FILE, "sha": "not-hex"},
        {"file": FILE, "sha": "abc"},
        {"file": FILE, "compare_to": OLD_SHA},
        {"file": FILE, "view": "content"},
        {"file": FILE, "sha": OLD_SHA, "compare_to": NEW_SHA, "view": "content"},
        {"sha": OLD_SHA},
    ])
    async def test_malformed_requests_are_refused_before_any_call(self, hass, esphome_dir, args):
        builder = _builder()
        _content, outcome, _ = await _run(args, hass, builder)
        assert outcome == "invalid_request" and builder.calls == []

    async def test_secrets_yaml_is_refused_by_the_jail(self, hass, esphome_dir):
        builder = _builder()
        _content, outcome, _ = await _run({"file": "secrets.yaml"}, hass, builder)
        assert outcome == "invalid_request" and builder.calls == []


class TestDeletedFiles:
    async def test_out_of_scope_and_nonexistent_are_byte_identical(self, hass, esphome_dir, esphome_entries):
        _esphome_entry(hass, esphome_entries, entity_ids=("light.not_mine",),
                       runtime=_runtime(_device_info(name="stair-light")))
        builder = _builder(**{"version_history/list_versions": []})
        hidden, hidden_outcome, _ = await _run({"file": "stair-light.yaml"}, hass, builder)
        missing, missing_outcome, _ = await _run({"file": "never-existed.yaml"}, hass, builder)
        assert hidden["content"][0]["text"] == missing["content"][0]["text"]
        assert (hidden_outcome, missing_outcome) == ("denied", "not_found")
        # The out-of-scope file never reached the add-on.
        assert builder.args_for("version_history/list_versions") == {"configuration": "never-existed.yaml"}
        assert len(builder.calls) == 1

    async def test_a_deleted_file_with_history_is_readable_in_scope(self, hass, esphome_dir):
        content, outcome, _ = await _run({"file": "hallway-light.yaml"}, hass, _builder())
        body = _json(content)
        assert outcome == "allowed" and body["exists"] is False and body["total"] == 2

    async def test_the_deleted_listing_is_jailed_and_scoped(self, hass, esphome_dir, esphome_entries):
        _esphome_entry(hass, esphome_entries, entity_ids=("light.not_mine",),
                       runtime=_runtime(_device_info(name="stair-light")))
        deleted = [{"configuration": n} for n in (
            "hallway-light.yaml", "stair-light.yaml", "secrets.yaml", "archive/old.yaml", ".hidden.yaml")]
        content, outcome, _ = await _run({}, hass, _builder(**{"version_history/list_deleted": deleted}))
        body = _json(content)
        assert outcome == "allowed"
        assert body["deleted"] == [{"file": "hallway-light.yaml"}]
        assert body["total"] == 1
        assert "stair-light" not in content["content"][0]["text"]
