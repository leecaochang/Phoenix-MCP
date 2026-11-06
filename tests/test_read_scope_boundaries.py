"""What reads disclose: attributes, logbook causes, the system log, rename blockers and YAML files."""

from __future__ import annotations

import asyncio
import json
import os
import time
from logging import ERROR, LogRecord
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.system_log import LogEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.conversation_log import sensitive_key
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.template_sandbox import build_permitted_states
from custom_components.phoenix_mcp.helpers import content_hash
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.policy_engine import is_sensitive_key


def _data(hass, others=()) -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.return_value = []
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    store.get_entity_hints.return_value = {}
    store.list_access_keys.return_value = list(others)
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=None)
    hass.data[DOMAIN] = data
    return data


def _key(entities: dict[str, str], key_id: str = "k", name: str = "K", **fields) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=key_id, name=name, access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(entities={e: PermissionNode(state=s) for e, s in entities.items()}),
        **fields,
    )


def _body(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


_ATTRIBUTES = {
    "entity_id": ("light.visible", "lock.hidden"),
    "nested": {"password": "hunter2", "fine": 1, "sensor.hidden": "keyed", "ref": "lock.hidden"},
    "sub": [{"api_key": "k"}, {"ref": ["lock.hidden"]}],
    "token": "top",
    "hostname": "printer.local",
    "stale": "light.long_gone",
}


def _states(hass) -> None:
    hass.states.async_set("light.all", "on", _ATTRIBUTES)
    hass.states.async_set("light.visible", "on")
    hass.states.async_set("lock.hidden", "locked")
    hass.states.async_set("sensor.hidden", "1")


def _assert_scrubbed(attributes: dict) -> None:
    assert "token" not in attributes
    assert attributes["entity_id"] == ["light.visible", "<redacted>"]
    assert attributes["nested"] == {"password": "<redacted>", "fine": 1, "ref": "<redacted>"}
    assert attributes["sub"] == [{"api_key": "<redacted>"}, {"ref": ["<redacted>"]}]


async def test_get_state_scopes_attributes_recursively(hass):
    _states(hass)
    data = _data(hass)
    key = _key({"light.all": "YELLOW", "light.visible": "YELLOW"})
    result, outcome, _ = await _call_tool("get_state", {"entity_id": "light.all", "projection": {"kind": "full"}}, key, hass, data)
    assert outcome == "allowed", result
    _assert_scrubbed(_body(result)["attributes"])
    assert "lock.hidden" not in result["content"][0]["text"]


async def test_strings_that_name_no_entity_stay_readable(hass):
    _states(hass)
    data = _data(hass)
    key = _key({"light.all": "YELLOW"})
    result, _, _ = await _call_tool("get_state", {"entity_id": "light.all", "projection": {"kind": "full"}}, key, hass, data)
    attributes = _body(result)["attributes"]
    assert attributes["hostname"] == "printer.local"
    assert attributes["stale"] == "light.long_gone"


async def test_describe_entity_shares_the_filter(hass):
    _states(hass)
    data = _data(hass)
    key = _key({"light.all": "YELLOW", "light.visible": "YELLOW"}, cap_search="allow")
    result, outcome, _ = await _call_tool("describe_entity", {"entity_id": "light.all"}, key, hass, data)
    assert outcome == "allowed", result
    assert "lock.hidden" not in result["content"][0]["text"]
    assert "hunter2" not in result["content"][0]["text"]


async def test_template_snapshot_shares_the_filter(hass):
    _states(hass)
    key = _key({"light.all": "YELLOW", "light.visible": "YELLOW"})
    _assert_scrubbed(build_permitted_states(key, hass)["light.all"].attributes)


async def test_pass_through_keeps_real_references_and_still_scrubs_secrets(hass):
    _states(hass)
    data = _data(hass)
    key = _key({}, pass_through=True, cap_search="allow")
    result, _, _ = await _call_tool("get_state", {"entity_id": "light.all", "projection": {"kind": "full"}}, key, hass, data)
    attributes = _body(result)["attributes"]
    assert attributes["entity_id"] == ["light.visible", "lock.hidden"]
    assert attributes["nested"]["password"] == "<redacted>"


@pytest.mark.parametrize("name", [
    "wifi_key", "wpa_key", "encryption_key", "network_key", "pre_shared_key", "signing_key", "ssh_key", "license_key",
    "psk", "wpa_psk", "passphrase", "passcode", "pin", "door_pin", "Door-PIN", "pin_code", "pincode", "wifiKey",
    "password", "api_key", "access_key", "private_key", "auth_token", "secret_x", "session_id",
    "x-api-key", "X-Api-Key", "wifi key", "Wifi-Key", "pre-shared-key", "private key", "access-key", "access key",
    "passwd", "db_passwd", "pwd", "wifi_pwd", "pass", "mqtt_pass", "admin-pass", "bearer", "oauth_bearer",
])
def test_credential_names_are_sensitive(name):
    assert is_sensitive_key(name)


@pytest.mark.parametrize("name", [
    "ping", "pinned", "spinning", "shipping", "mapping", "opinion", "pin_count", "upskill", "keyboard", "key_count",
    "translation_key", "unique_key", "sort_key", "monkey", "code", "friendly_name", "access_key_name", "access_key_id",
    "access-key-id", "bypass", "compass", "passage", "pass_through", "pass_count", "passed", "tested_pass_rate",
])
def test_ordinary_names_stay_readable(name):
    assert not is_sensitive_key(name)


@pytest.mark.parametrize("name", ["pin", "psk", "encryption_key"])
def test_the_conversation_log_never_keeps_what_the_shared_check_removed(name):
    assert sensitive_key(name) and sensitive_key(name.upper()) and is_sensitive_key(name)


async def _logbook(hass, key, row) -> dict:
    data = _data(hass)
    with patch("custom_components.phoenix_mcp.tools.logs.async_ws_command", AsyncMock(return_value=[row])):
        result, outcome, _ = await _call_tool("get_logbook", {}, key, hass, data)
    assert outcome == "allowed", result
    return _body(result)["entries"][0]


_ROW = {
    "entity_id": "light.visible", "state": "on", "name": "Visible",
    "context_entity_id": "binary_sensor.presence", "context_entity_id_name": "Someone in the bedroom",
    "context_state": "on", "context_name": "Presence", "context_message": "turned on",
    "context_event_type": "state_changed", "context_domain": "binary_sensor", "context_user_id": "abc123",
}


async def test_a_hidden_cause_leaves_no_trace_in_a_logbook_row(hass):
    hass.states.async_set("light.visible", "on")
    hass.states.async_set("binary_sensor.presence", "on")
    entry = await _logbook(hass, _key({"light.visible": "YELLOW"}, cap_log_read="allow"), {"when": time.time(), **_ROW})
    assert not [field for field in entry if field.startswith("context_")]
    assert entry["name"] == "Visible"


async def test_a_visible_cause_stays_and_the_user_never_does(hass):
    hass.states.async_set("light.visible", "on")
    hass.states.async_set("binary_sensor.presence", "on")
    key = _key({"light.visible": "YELLOW", "binary_sensor.presence": "YELLOW"}, cap_log_read="allow")
    entry = await _logbook(hass, key, {"when": time.time(), **_ROW})
    assert entry["context_entity_id"] == "binary_sensor.presence"
    assert entry["context_state"] == "on"
    assert "context_user_id" not in entry


async def test_a_cause_without_an_entity_stays(hass):
    hass.states.async_set("light.visible", "on")
    key = _key({"light.visible": "YELLOW"}, cap_log_read="allow")
    entry = await _logbook(hass, key, {"when": time.time(), "entity_id": "light.visible", "name": "V", "state": "on",
                                       "context_domain": "script", "context_service": "turn_on", "context_user_id": "abc"})
    assert entry["context_service"] == "turn_on"
    assert "context_user_id" not in entry


def _log_ring(hass, message: str) -> None:
    record = LogRecord("homeassistant.components.light", ERROR, __file__, 1, message, (), None)
    record.created = time.time()
    entry = LogEntry(record, None, ("homeassistant/components/light/__init__.py", 1))
    hass.data["system_log"] = MagicMock(records=MagicMock(
        to_list=MagicMock(return_value=[entry.to_dict()]), maxlen=50), fire_event=False)
    hass.data["system_log"].records = {entry.key: entry}


async def test_system_log_redacts_entities_outside_the_scope_before_search(hass):
    hass.states.async_set("light.visible", "on")
    hass.states.async_set("light.hidden_lamp", "on")
    _log_ring(hass, "light.hidden_lamp failed and light.visible too")
    data = _data(hass)
    key = _key({"light.visible": "YELLOW"}, cap_log_read="allow")
    result, outcome, _ = await _call_tool("get_logs", {"level": "ERROR"}, key, hass, data)
    assert outcome == "allowed", result
    text = result["content"][0]["text"]
    assert "light.hidden_lamp" not in text and "light.visible" in text
    probe, _, _ = await _call_tool("get_logs", {"level": "ERROR", "search": "hidden_lamp"}, key, hass, data)
    assert _body(probe)["entries"] == []


async def test_rename_blockers_reach_the_agent_as_counts(hass):
    registry = er.async_get(hass)
    entity_id = registry.async_get_or_create("light", "t", "a", suggested_object_id="vis").entity_id
    hass.states.async_set(entity_id, "on")
    other = _key({entity_id: "RED"}, key_id="other-id", name="Private tablet")
    data = _data(hass, [other])
    key = _key({entity_id: "GREEN"}, cap_registry_write="allow")
    result, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"new_entity_id": "light.renamed"}}, key, hass, data)
    assert outcome == "invalid_request", result
    text = result["content"][0]["text"]
    assert "other-id" not in text and "Private tablet" not in text
    assert json.loads(text)["blockers"] == [{"kind": "access_key_permission", "count": 1}]


def _config_file(hass, relative: str, text: str) -> None:
    path = hass.config.path(relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


@pytest.mark.parametrize("name", ["secrets.yaml", "secrets.yml", "SECRETS.YAML", "zigbee2mqtt/secret.yaml", "my_secrets.yml"])
async def test_files_named_like_secrets_are_refused(hass, name):
    _config_file(hass, name, "api_key: leaked\n")
    result, outcome, _ = await _call_tool("get_yaml_config", {"file": name}, _key({}, cap_yaml_edit="allow"), hass, _data(hass))
    assert outcome == "invalid_request", result
    assert "leaked" not in result["content"][0]["text"]


async def test_credentials_in_a_readable_file_are_redacted(hass):
    text = (
        "advanced:\n  network_key:\n    - 1\n    - 2\n  pan_id: 6754\n"
        "inline_key: [1, 2]\nmqtt:\n  password: z2m_pw\n  server: mqtt://host\n"
        "wifi:\n  psk: hunter2\n  ssid: home\nsafe: !secret reference\napi_key: !secret api\n"
    )
    _config_file(hass, "zigbee2mqtt/configuration.yaml", text)
    result, outcome, _ = await _call_tool(
        "get_yaml_config", {"file": "zigbee2mqtt/configuration.yaml"}, _key({}, cap_yaml_edit="allow"), hass, _data(hass))
    assert outcome == "allowed", result
    content = _body(result)["content"]
    assert "z2m_pw" not in content and "hunter2" not in content
    assert "- 1" not in content
    assert "pan_id: 6754" in content and "ssid: home" in content
    assert "api_key: !secret api" in content


@pytest.mark.parametrize("text", [
    'rest_command:\n  a:\n    headers: {Authorization: "Bearer hunter2", x-api-key: hunter2}\n',
    "- password: hunter2\n",
    "accounts:\n  - token: hunter2\n    name: a\n",
    '"password": hunter2\n',
    "'api_key': hunter2\n",
    'x: {"password": "hunter2"}\n',
    "auth: {username: u, password: hunter2}\n",
    "password: |\n  hunter2\n  second\nnext: 1\n",
    "token: >-\n  hunter2\nnext: 1\n",
    "private_key: |\n  -----BEGIN PRIVATE KEY-----\n  hunter2\n  -----END PRIVATE KEY-----\n",
    "password:\n  hunter2\nnext: 1\n",
    "password: &p\n  hunter2\nnext: 1\n",
    "shared: &p hunter2\nmqtt:\n  password: *p\n",
    "password: hun\n  ter2 hunter2\nnext: 1\n",
    "x-api-key: hunter2\n",
    "wifi key: hunter2\n",
    "? password\n: hunter2\n",
    "? [a, b]\n: hunter2\n",
    "password: !env_var DB_PW hunter2\n",
    "password: !!str hunter2\n",
    "passwords:\n  - hunter2\n  - {old: hunter2}\n",
    "passwd: hunter2\npwd: hunter2\npass: hunter2\nbearer: hunter2\n",
    "# password: hunter2\nnext: 1\n",
    "next: 1\n# password:\n#   hunter2\n",
    "next: 1 # token hunter2\n",
    "password: x # was hunter2\n",
    "url: mqtt://user:hunter2@host:1883\n",
    "url: https://hunter2@example.com/x\n",
    "resource: https://example.com/a?key=hunter2&b=1\n",
    'payload: \'{"password": "hunter2"}\'\n',
    "template: |\n  const char* password = \"hunter2\";\n",
    "note: Bearer hunter2hunter2\n",
], ids=lambda text: text.split("\n")[0][:30])
async def test_every_yaml_form_of_a_credential_is_masked(hass, text):
    _config_file(hass, "extra.yaml", text)
    result, outcome, _ = await _call_tool(
        "get_yaml_config", {"file": "extra.yaml"}, _key({}, cap_yaml_edit="allow"), hass, _data(hass))
    assert outcome == "allowed", result
    assert "hunter2" not in _body(result)["content"]
    assert "<redacted>" in _body(result)["content"]


async def test_masking_keeps_the_rest_of_the_file_as_written(hass):
    text = (
        "# Living room\nhttp:\n  server_port: 8123  # default\nlight: !include lights.yaml\n"
        "mqtt:\n  password: !secret mqtt_pw\n  use_password: true\n  broker: 'host'\n\n"
        "sensor:\n  - platform: rest\n    name: Bypass valve\n"
    )
    _config_file(hass, "extra.yaml", text)
    result, outcome, _ = await _call_tool(
        "get_yaml_config", {"file": "extra.yaml"}, _key({}, cap_yaml_edit="allow"), hass, _data(hass))
    assert outcome == "allowed", result
    assert _body(result)["content"] == text


@pytest.mark.parametrize("args", [{"file": "extra.yaml"}, {"file": "extra.yaml", "key": "mqtt"}])
async def test_a_file_that_does_not_parse_is_refused_without_quoting_it(hass, args):
    _config_file(hass, "extra.yaml", "mqtt:\n  password: [hunter2\n  other: 1\n")
    result, outcome, _ = await _call_tool("get_yaml_config", args, _key({}, cap_yaml_edit="allow"), hass, _data(hass))
    assert outcome == "invalid_request"
    text = result["content"][0]["text"]
    assert "hunter2" not in text and "line 3" in text


async def test_an_alias_cannot_read_a_masked_value_through_a_harmless_key(hass):
    _config_file(hass, "extra.yaml", "shared: &p hunter2\nmqtt:\n  password: *p\nother:\n  value: *p\n")
    key = _key({}, cap_yaml_edit="allow")
    for name in ("shared", "other"):
        result, outcome, _ = await _call_tool(
            "get_yaml_config", {"file": "extra.yaml", "key": name}, key, hass, _data(hass))
        assert outcome == "allowed", result
        assert "hunter2" not in _body(result)["content"]


async def test_a_fragment_read_scrubs_text_inside_a_value(hass):
    _config_file(hass, "extra.yaml", "mqtt:\n  url: mqtt://user:hunter2@host\n  broker: host\n")
    result, _, _ = await _call_tool(
        "get_yaml_config", {"file": "extra.yaml", "key": "mqtt"}, _key({}, cap_yaml_edit="allow"), hass, _data(hass))
    content = _body(result)["content"]
    assert "hunter2" not in content and "broker: host" in content


async def test_a_fragment_read_cannot_return_a_bare_secret(hass):
    _config_file(hass, "extra.yaml", "mqtt:\n  password: hunter2\n  broker: host\n")
    key = _key({}, cap_yaml_edit="allow")
    data = _data(hass)
    secret, _, _ = await _call_tool("get_yaml_config", {"file": "extra.yaml", "key": "mqtt.password"}, key, hass, data)
    parent, _, _ = await _call_tool("get_yaml_config", {"file": "extra.yaml", "key": "mqtt"}, key, hass, data)
    assert _body(secret)["content"] == "<redacted>"
    assert "hunter2" not in _body(parent)["content"]
    assert "broker: host" in _body(parent)["content"]


async def test_the_content_hash_still_covers_the_whole_file(hass):
    key = _key({}, cap_yaml_edit="allow")
    data = _data(hass)
    _config_file(hass, "extra.yaml", "password: one\n")
    first, _, _ = await _call_tool("get_yaml_config", {"file": "extra.yaml"}, key, hass, data)
    _config_file(hass, "extra.yaml", "password: two\n")
    second, _, _ = await _call_tool("get_yaml_config", {"file": "extra.yaml"}, key, hass, data)
    assert _body(first)["content"] == _body(second)["content"]
    assert _body(first)["content_hash"] != _body(second)["content_hash"]


async def test_the_content_hash_cannot_test_a_guessed_file(hass):
    key = _key({}, cap_yaml_edit="allow")
    text = "password: hunter2\n"
    _config_file(hass, "extra.yaml", text)
    read, _, _ = await _call_tool("get_yaml_config", {"file": "extra.yaml"}, key, hass, _data(hass))
    repeat, _, _ = await _call_tool("get_yaml_config", {"file": "extra.yaml"}, key, hass, _data(hass))
    assert _body(read)["content_hash"] == _body(repeat)["content_hash"]
    assert _body(read)["content_hash"] != content_hash(text)
