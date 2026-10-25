"""YAML configuration reads (get_yaml_config) and integration enable/disable.

get_yaml_config reads configuration.yaml and the other YAML files in the
configuration directory, one key at a time when asked, and never secrets.yaml,
the esphome directory or hidden directories, whatever a symlink is named.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.const import MAX_FILE_BYTES
from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery
from custom_components.phoenix_mcp.mcp_view import _EXECUTOR_REGISTRY, _call_tool
from custom_components.phoenix_mcp.access_key_store import (
    PermissionNode,
    PermissionTree,
    AccessKeyRecord,
)


@pytest.fixture(autouse=True)
def isolated_config_dir(hass, tmp_path):
    """The tests write configuration.yaml and the files it names; keep them out of
    the shared testing config dir so they cannot leak into other tests.
    """
    hass.config.config_dir = str(tmp_path)
    return tmp_path


def _access_key(**caps) -> AccessKeyRecord:
    base = {"cap_yaml_edit": "allow", "cap_integration_write": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=PermissionTree(), **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, access_key, hass, data=None):
    return await _call_tool(name, args, access_key, hass, data or _data())


def _data():
    value = MagicMock()
    value.config_recovery = ConfigurationRecovery()
    return value


class TestExecutorRegistration:
    def test_registered(self):
        assert "set_integration_enabled" in _EXECUTOR_REGISTRY
        assert "set_integration" in _EXECUTOR_REGISTRY
        assert "reload_integration" in _EXECUTOR_REGISTRY
        assert "remove_integration" in _EXECUTOR_REGISTRY


class TestYamlConfig:
    async def test_get_deny(self, hass):
        _, outcome, _ = await _call("get_yaml_config", {}, _access_key(cap_yaml_edit="deny"), hass)
        assert outcome == "denied"

    async def test_get_reads_configuration_yaml_by_default(self, hass):
        path = hass.config.path("configuration.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("default_config:\n")
        content, outcome, _ = await _call("get_yaml_config", {}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["content"] == "default_config:\n"

    async def test_oversized_file_is_refused_without_its_content(self, hass):
        with open(hass.config.path("configuration.yaml"), "w", encoding="utf-8") as f:
            f.write("#" * (MAX_FILE_BYTES + 1))
        content, outcome, _ = await _call("get_yaml_config", {}, _access_key(), hass)
        assert outcome == "invalid_request"
        text = content["content"][0]["text"]
        assert "maximum readable size" in text
        assert "###" not in text
        # The same file under the cap reads normally.
        with open(hass.config.path("configuration.yaml"), "w", encoding="utf-8") as f:
            f.write("#" * MAX_FILE_BYTES)
        _, outcome, _ = await _call("get_yaml_config", {}, _access_key(), hass)
        assert outcome == "allowed"


class TestGetYamlConfigFileArg:
    """get_yaml_config reads other YAML files in the config dir, inside a jail."""

    async def test_zero_arg_response_shape_is_unchanged(self, hass):
        # The no-argument response is a contract: existing clients depend on
        # exactly these keys, in this order, with no extras.
        path = hass.config.path("configuration.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("default_config:\n")
        content, _, _ = await _call("get_yaml_config", {}, _access_key(), hass)
        body = _json(content)
        assert list(body) == ["path", "exists", "content", "content_hash"]
        assert body["path"] == "configuration.yaml"

    async def test_reads_an_include_target(self, hass):
        with open(hass.config.path("automations.yaml"), "w", encoding="utf-8") as f:
            f.write("- id: '1'\n  alias: A\n")
        content, outcome, _ = await _call(
            "get_yaml_config", {"file": "automations.yaml"}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["path"] == "automations.yaml"
        assert body["content"] == "- id: '1'\n  alias: A\n"

    async def test_reads_a_nested_package_file(self, hass):
        os.makedirs(hass.config.path("packages"), exist_ok=True)
        with open(hass.config.path("packages/kitchen.yaml"), "w", encoding="utf-8") as f:
            f.write("sensor: []\n")
        content, outcome, _ = await _call(
            "get_yaml_config", {"file": "packages/kitchen.yaml"}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["content"] == "sensor: []\n"

    async def test_absent_file_reads_as_empty(self, hass):
        content, outcome, _ = await _call(
            "get_yaml_config", {"file": "nope.yaml"}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["exists"] is False
        assert body["content"] == ""

    @pytest.mark.parametrize(
        "bad",
        [
            "../outside.yaml",
            "packages/../../outside.yaml",
            "/etc/passwd.yaml",
            "secrets.yaml",
            "SECRETS.YAML",
            "sub/secrets.yaml",
            "esphome/device.yaml",
            "ESPHOME/device.yaml",
            ".storage/core.entity_registry.yaml",
            "packages/.hidden/x.yaml",
            "notes.txt",
            "configuration",
        ],
    )
    async def test_refused_paths(self, hass, bad):
        content, outcome, _ = await _call("get_yaml_config", {"file": bad}, _access_key(), hass)
        assert outcome == "invalid_request"
        assert "configuration directory" in content["content"][0]["text"]

    @pytest.mark.parametrize("blank", ["", "   ", None])
    async def test_blank_file_arg_reads_configuration_yaml(self, hass, blank):
        # Models routinely send "" for an omitted optional string; treat a blank
        # value as unset rather than refusing it.
        with open(hass.config.path("configuration.yaml"), "w", encoding="utf-8") as f:
            f.write("default_config:\n")
        content, outcome, _ = await _call("get_yaml_config", {"file": blank}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["path"] == "configuration.yaml"

    async def test_non_string_file_arg_refused(self, hass):
        _, outcome, _ = await _call("get_yaml_config", {"file": ["a.yaml"]}, _access_key(), hass)
        assert outcome == "invalid_request"

    async def test_symlink_out_of_config_dir_refused(self, hass, tmp_path):
        outside = tmp_path.parent / "outside_secret.yaml"
        outside.write_text("secret: 1\n", encoding="utf-8")
        os.symlink(outside, hass.config.path("link.yaml"))
        _, outcome, _ = await _call("get_yaml_config", {"file": "link.yaml"}, _access_key(), hass)
        assert outcome == "invalid_request"

    @pytest.mark.parametrize("hidden, text, value", [
        ("secrets.yaml", "db_pass: hunter2\n", "hunter2"),
        ("esphome/device.yaml", "wifi:\n  password: house-password\n", "house-password"),
        (".storage/notes.yaml", "token: storage-token\n", "storage-token"),
    ])
    async def test_symlink_renaming_an_excluded_file_refused(self, hass, hidden, text, value):
        # The extension/name rules re-run on the RESOLVED path, so a symlink
        # cannot launder an excluded file behind an allowed-looking name.
        target = hass.config.path(hidden)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        os.symlink(target, hass.config.path("innocent.yaml"))
        content, outcome, _ = await _call(
            "get_yaml_config", {"file": "innocent.yaml"}, _access_key(), hass)
        assert outcome == "invalid_request"
        assert value not in content["content"][0]["text"]

    async def test_symlink_to_a_readable_file_reads(self, hass):
        # The refusal above is about where the link points, not about links.
        with open(hass.config.path("automations.yaml"), "w", encoding="utf-8") as f:
            f.write("[]\n")
        os.symlink(hass.config.path("automations.yaml"), hass.config.path("innocent.yaml"))
        content, outcome, _ = await _call(
            "get_yaml_config", {"file": "innocent.yaml"}, _access_key(), hass)
        assert outcome == "allowed"
        assert _json(content)["content"] == "[]\n"

    async def test_deny_applies_to_file_reads(self, hass):
        _, outcome, _ = await _call(
            "get_yaml_config", {"file": "automations.yaml"}, _access_key(cap_yaml_edit="deny"), hass)
        assert outcome == "denied"


class TestGetYamlConfigKeyArg:
    """key returns one fragment, tag-preserving, with the full-file hash."""

    def _write(self, hass, text):
        with open(hass.config.path("configuration.yaml"), "w", encoding="utf-8") as f:
            f.write(text)

    async def test_returns_the_fragment_only(self, hass):
        self._write(hass, "http:\n  server_port: 8123\nfrontend:\n  themes: {}\n")
        content, outcome, _ = await _call("get_yaml_config", {"key": "http"}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["key_found"] is True
        assert body["content"] == "server_port: 8123\n"
        assert "frontend" not in body["content"]

    async def test_nested_dotted_key(self, hass):
        self._write(hass, "homeassistant:\n  packages: !include_dir_named integrations\n")
        content, _, _ = await _call(
            "get_yaml_config", {"key": "homeassistant.packages"}, _access_key(), hass)
        body = _json(content)
        assert body["key_found"] is True
        assert body["defined_via_include"] == "integrations"
        assert body["content"].strip() == "!include_dir_named integrations"

    async def test_missing_key(self, hass):
        self._write(hass, "http:\n  server_port: 8123\n")
        content, outcome, _ = await _call("get_yaml_config", {"key": "lovelace"}, _access_key(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["key_found"] is False
        assert body["content"] == ""

    async def test_key_through_a_non_mapping_is_not_found(self, hass):
        self._write(hass, "http: 5\n")
        body = _json((await _call("get_yaml_config", {"key": "http.port"}, _access_key(), hass))[0])
        assert body["key_found"] is False

    async def test_labeled_top_level_key(self, hass):
        # HA allows "automation manual:" style labeled keys; they carry no dot,
        # so the walker finds them whole.
        self._write(hass, "automation manual:\n  - alias: A\n")
        body = _json((await _call(
            "get_yaml_config", {"key": "automation manual"}, _access_key(), hass))[0])
        assert body["key_found"] is True

    async def test_secret_tag_survives_as_source_text(self, hass):
        self._write(hass, "http:\n  api_password: !secret pw\n")
        body = _json((await _call("get_yaml_config", {"key": "http"}, _access_key(), hass))[0])
        assert "!secret pw" in body["content"]

    async def test_unknown_custom_tag_does_not_break_the_read(self, hass):
        # A third-party tag must not make the file unreadable.
        self._write(hass, "http:\n  server_port: 8123\ncustom: !my_vendor_tag thing\n")
        body = _json((await _call("get_yaml_config", {"key": "http"}, _access_key(), hass))[0])
        assert body["key_found"] is True

    async def test_invalid_yaml_with_key_errors(self, hass):
        self._write(hass, "http:\n  - [unclosed\n")
        content, outcome, _ = await _call("get_yaml_config", {"key": "http"}, _access_key(), hass)
        assert outcome == "invalid_request"
        assert "not valid YAML" in content["content"][0]["text"]

    async def test_invalid_yaml_without_key_still_returns_raw_text(self, hass):
        # No key means no parse, so a broken file is still readable for repair.
        self._write(hass, "http:\n  - [unclosed\n")
        content, outcome, _ = await _call("get_yaml_config", {}, _access_key(), hass)
        assert outcome == "allowed"
        assert "unclosed" in _json(content)["content"]

    async def test_content_hash_is_the_whole_file_even_with_key(self, hass):
        text = "http:\n  server_port: 8123\nfrontend:\n"
        self._write(hass, text)
        body = _json((await _call("get_yaml_config", {"key": "http"}, _access_key(), hass))[0])
        assert body["content_hash"] == content_hash(text)

    async def test_key_from_another_file(self, hass):
        with open(hass.config.path("extra.yaml"), "w", encoding="utf-8") as f:
            f.write("outer:\n  inner: 7\n")
        body = _json((await _call(
            "get_yaml_config", {"file": "extra.yaml", "key": "outer.inner"}, _access_key(), hass))[0])
        assert body["key_found"] is True
        assert body["content"].strip() == "7"

    async def test_empty_key_rejected(self, hass):
        _, outcome, _ = await _call("get_yaml_config", {"key": "  "}, _access_key(), hass)
        assert outcome == "invalid_request"

    async def test_absent_file_with_key_reports_not_found(self, hass):
        body = _json((await _call(
            "get_yaml_config", {"file": "gone.yaml", "key": "http"}, _access_key(), hass))[0])
        assert body["exists"] is False
        assert body["key_found"] is False


class TestYamlConfigContentHash:
    """get_yaml_config reports a hash of the whole file, so two reads can be compared."""

    async def test_get_reports_content_hash(self, hass):
        path = hass.config.path("configuration.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("default_config:\n")
        content, _, _ = await _call("get_yaml_config", {}, _access_key(), hass)
        assert _json(content)["content_hash"] == content_hash("default_config:\n")

    async def test_get_absent_reports_hash_of_empty(self, hass):
        content, _, _ = await _call("get_yaml_config", {}, _access_key(), hass)
        body = _json(content)
        assert body["exists"] is False
        assert body["content_hash"] == content_hash("")



class TestIntegrations:
    async def test_list_deny(self, hass):
        _, outcome, _ = await _call("list_integrations", {}, _access_key(cap_integration_write="deny"), hass)
        assert outcome == "denied"

    async def test_list_excludes_phoenix(self, hass):
        MockConfigEntry(domain="test_integration", title="Test", entry_id="e1").add_to_hass(hass)
        MockConfigEntry(domain="phoenix_mcp", title="Phoenix MCP", entry_id="phoenix_mcp1").add_to_hass(hass)
        content, outcome, _ = await _call(
            "list_integrations", {}, _access_key(pass_through=True), hass
        )
        assert outcome == "allowed"
        domains = {i["domain"] for i in _json(content)["integrations"]}
        assert "test_integration" in domains
        assert "phoenix_mcp" not in domains

    async def test_list_is_scoped_by_owned_entity_and_safe_projection(self, hass):
        from homeassistant.helpers import entity_registry as er

        visible = MockConfigEntry(
            domain="test_integration",
            title="Visible 192.168.1.2 https://example.invalid/setup",
            entry_id="visible-entry",
            data={"password": "secret", "host": "192.168.1.2"},
            options={"token": "secret"},
        )
        hidden = MockConfigEntry(
            domain="other_integration", title="Hidden", entry_id="hidden-entry"
        )
        visible.add_to_hass(hass)
        hidden.add_to_hass(hass)
        entity = er.async_get(hass).async_get_or_create(
            "light", "test_integration", "visible-light",
            config_entry=visible,
        )
        hass.states.async_set(entity.entity_id, "on")
        access_key = _access_key()
        access_key.permissions.entities[entity.entity_id] = PermissionNode(state="YELLOW")

        content, outcome, _ = await _call("list_integrations", {}, access_key, hass)

        assert outcome == "allowed"
        result = _json(content)
        assert result["count"] == 1
        item = result["integrations"][0]
        assert item["entry_id"] == visible.entry_id
        assert item["title"] == "Visible <redacted-ip> <redacted-url>"
        assert item["state"] == "not_loaded"
        assert item["enabled"] is True
        assert item["accessible_entity_count"] == 1
        assert item["accessible_device_count"] == 0
        assert set(item) == {
            "entry_id", "domain", "title", "source", "state", "enabled",
            "disabled_by", "setup_failure_reason", "supports_reload",
            "supports_unload", "supports_options", "supports_reconfigure",
            "pref_disable_new_entities", "pref_disable_polling",
            "accessible_entity_count", "accessible_device_count",
        }
        serialized = json.dumps(item)
        assert "secret" not in serialized
        assert "192.168.1.2" not in serialized
        assert hidden.entry_id not in serialized

    async def test_disabled_direct_entity_grant_does_not_reveal_entry(self, hass):
        from homeassistant.helpers import entity_registry as er

        entry = MockConfigEntry(
            domain="test_integration", title="Disabled", entry_id="disabled-entry"
        )
        entry.add_to_hass(hass)
        entity = er.async_get(hass).async_get_or_create(
            "switch", "test_integration", "disabled-switch",
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        access_key = _access_key()
        access_key.permissions.entities[entity.entity_id] = PermissionNode(state="GREEN")
        content, outcome, _ = await _call("list_integrations", {}, access_key, hass)
        assert outcome == "allowed"
        assert _json(content)["integrations"] == []

    async def test_disable_calls_ha(self, hass):
        entry = MockConfigEntry(domain="test_integration", entry_id="e2")
        entry.add_to_hass(hass)
        access_key = _access_key()
        access_key.pass_through = True
        data = _data()
        data.mesa = None
        data.store.get_settings.return_value.mesa_mode = "off"
        with patch_set_disabled(hass) as mock:
            content, outcome, _ = await _call(
                "set_integration_enabled", {"entry_id": "e2", "enabled": False}, access_key, hass, data)
        assert outcome == "allowed"
        mock.assert_awaited_once()
        assert mock.await_args.args[1] == ConfigEntryDisabler.USER

    async def test_enable_passes_none(self, hass):
        entry = MockConfigEntry(
            domain="test_integration",
            entry_id="e3",
            disabled_by=ConfigEntryDisabler.USER,
        )
        entry.add_to_hass(hass)
        access_key = _access_key()
        access_key.pass_through = True
        data = _data()
        data.mesa = None
        data.store.get_settings.return_value.mesa_mode = "off"
        with patch_set_disabled(hass) as mock:
            _, outcome, _ = await _call(
                "set_integration_enabled", {"entry_id": "e3", "enabled": True}, access_key, hass, data)
        assert outcome == "allowed"
        assert mock.await_args.args[1] is None

    async def test_unknown_entry(self, hass):
        _, outcome, _ = await _call("set_integration_enabled", {"entry_id": "nope", "enabled": True}, _access_key(), hass)
        assert outcome == "not_found"

    async def test_phoenix_entry_refused(self, hass):
        MockConfigEntry(domain="phoenix_mcp", entry_id="phoenix_mcp2").add_to_hass(hass)
        _, outcome, _ = await _call("set_integration_enabled", {"entry_id": "phoenix_mcp2", "enabled": False}, _access_key(), hass)
        assert outcome == "not_found"

    async def test_non_bool(self, hass):
        MockConfigEntry(domain="test_integration", entry_id="e4").add_to_hass(hass)
        _, outcome, _ = await _call("set_integration_enabled", {"entry_id": "e4", "enabled": "yes"}, _access_key(), hass)
        assert outcome == "invalid_request"


def patch_set_disabled(hass):
    from unittest.mock import patch
    return patch.object(hass.config_entries, "async_set_disabled_by", new=AsyncMock(return_value=True))
