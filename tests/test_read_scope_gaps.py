"""Readers hide hidden entities, credentials and existence in the shapes the scrubs used to miss.

Dotted entity references, keys inside a harmless key's value, header and webhook URL shapes,
ESPHome substitutions behind a credential, folded file names and audit refusals.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.config_recovery import ConfigurationRecovery
from custom_components.phoenix_mcp.esphome_yaml import (
    esphome_rel_path_ok, inline_secret_values, mask_yaml_text, redact_esphome_text,
)
from custom_components.phoenix_mcp.redaction import (
    _scrub_log_text, redact_entity_ids_outside, redact_inaccessible_entity_ids_in_text, redact_secrets_in_text,
)
from custom_components.phoenix_mcp.tool_dispatch import _call_tool

HIDDEN_LOCK = "lock.smart_lock"


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}), **caps)


def _home(hass: HomeAssistant) -> None:
    hass.states.async_set(HIDDEN_LOCK, "locked")
    hass.states.async_set("light.hall", "on")
    er.async_get(hass).async_get_or_create("automation", "automation", "morning", suggested_object_id="morning")


@pytest.mark.parametrize("text", [
    "{{ states.lock.smart_lock.state }}",
    "{{ states.lock.smart_lock.attributes.battery }}",
    "dotted lock.smart_lock.state and lock.smart_lock.attributes.Battery",
    "Template warning: 'states.lock.smart_lock' is unknown",
    "homeassistant.components.automation.morning: Running automation actions",
])
async def test_a_dotted_reference_to_a_hidden_entity_is_redacted(hass, text):
    _home(hass)
    key = _key()
    out = redact_inaccessible_entity_ids_in_text(text, key, hass)
    assert "smart_lock" not in out and "automation.morning" not in out
    assert "<redacted-entity>" in out
    visible = frozenset({"light.hall"})
    assert "smart_lock" not in redact_entity_ids_outside(text, visible, hass)


@pytest.mark.parametrize("text", [
    "{{ states.light.hall.state }} and light.hall.attributes.brightness",
    "homeassistant.components.sensor.recorder: compiling statistics",
    "aiohttp.client_exceptions.ClientConnectorError at 192.168.1.10 version 2026.11.0",
    "trigger.to_state.state is on",
])
async def test_readable_and_non_entity_chains_stay(hass, text):
    _home(hass)
    assert redact_inaccessible_entity_ids_in_text(text, _key(), hass) == text


@pytest.mark.parametrize(("text", "secret"), [
    ("payload={'password': 'hunter2'}", "hunter2"),
    ("config: {'token': 'abcdef123'}", "abcdef123"),
    ("kwargs=dict(api_key='sekrit1')", "sekrit1"),
    ("headers={'Authorization': 'Token abc123'}", "abc123"),
    ('Request body: {"client_secret": "xyz789"}', "xyz789"),
    ("Authorization: Token X123secret", "X123secret"),
    ("Cookie: session=abc999; theme=dark", "abc999"),
    ("msg=password=hunter3 next", "hunter3"),
    ("token={'a': 'nested-value'} after", "nested-value"),
    ("POST https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage", "AAHdqTcv"),
    ("hook https://hooks.slack.com/services/T000/B000/XXXXsecret", "XXXXsecret"),
    ("bot token xoxb-1234567890-abcdefghij in use", "xoxb-1234567890"),
    ("discord https://discord.com/api/webhooks/1234/abcDEF_secret", "abcDEF_secret"),
    ("POST /api/webhook/household-note from 10.0.0.2", "household-note"),
    ("webhook_id=household-note", "household-note"),
    ("pushover user_key=uQiRzpo4DXghDmr9QzzfQu27cmVRsG", "uQiRzpo4"),
])
def test_log_credentials_are_scrubbed_wherever_they_sit(text, secret):
    assert secret not in _scrub_log_text(text)
    assert secret not in (redact_secrets_in_text(text) or "")


def test_the_log_scrub_keeps_what_holds_no_credential():
    for text in ("GET https://api.example.com/v1?access_token=SEKRET&page=2", "name: kitchen, state: on",
                 "password: !secret wifi", "light.turn_on called"):
        out = _scrub_log_text(text)
        assert "SEKRET" not in out
    assert "page=2" in _scrub_log_text("GET https://api.example.com/v1?access_token=SEKRET&page=2")
    assert _scrub_log_text("name: kitchen, state: on") == "name: kitchen, state: on"
    assert _scrub_log_text("password: !secret wifi") == "password: !secret wifi"


def test_configuration_files_mask_the_keys_device_files_do():
    masked = mask_yaml_text(
        "notify:\n  - platform: pushover\n    user_key: uk-1\n    appid: ap-2\n    consumer_key: ck-3\n"
        "zwave_js:\n  s0_legacy_key: s0-4\n  s2_access_control_key: s2-5\n  s2_authenticated_key: s2-6\n"
        "spotify:\n  sp_dc: dc-7\n"
        "api:\n  encryption:\n    key: enc-8\n"
        "automation:\n  - triggers:\n      - trigger: webhook\n        webhook_id: hook-9\n"
        "name: kept-10\n")
    for secret in ("uk-1", "ap-2", "ck-3", "s0-4", "s2-5", "s2-6", "dc-7", "enc-8", "hook-9"):
        assert secret not in masked
    assert "kept-10" in masked


_KEY = "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0MTI="


def test_a_substitution_behind_a_credential_is_masked():
    doc = (
        "substitutions:\n"
        f'  enc: "{_KEY}"\n'
        '  ota_word: "otaotaota9"\n'
        "  name: rf-blaster1\n"
        "  part: partpartpart\n"
        '  pw_full: "x${part}"\n'
        "  pkg_only: used-by-a-package\n"
        "esphome:\n  name: ${name}\n"
        "api:\n  encryption:\n    key: ${enc}\n"
        "ota:\n  - platform: esphome\n    password: $ota_word\n"
        "wifi:\n  password: ${pw_full}\n"
        "packages:\n  common: !include\n    file: common.yaml\n    vars:\n      wifi_word: hunter22\n"
    )
    masked, paths = redact_esphome_text(doc)
    for secret in (_KEY, "otaotaota9", "partpartpart", "used-by-a-package", "hunter22"):
        assert secret not in masked
    assert "name: rf-blaster1" in masked
    assert {"substitutions.enc", "substitutions.ota_word", "substitutions.part"} <= set(paths)
    # The values join the scrub of Builder output too.
    assert _KEY in inline_secret_values(doc)


def test_folded_and_non_ascii_names_are_refused():
    for name in ("ſecrets.yaml", "ſECRETS.yaml", "packages/ſecrets.yaml", "eſphome/door.yaml", "ﬁle.yaml"):
        assert not esphome_rel_path_ok(name)
    assert esphome_rel_path_ok("door.yaml")


@pytest.fixture
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    return tmp_path


def _yaml_data() -> MagicMock:
    data = MagicMock()
    data.config_recovery = ConfigurationRecovery()
    return data


async def test_get_yaml_config_never_opens_a_secrets_file(hass, config_dir):
    (config_dir / "secrets.yaml").write_text("alarm_code: 1234\n")
    (config_dir / "packages").mkdir()
    (config_dir / "packages" / "secrets.yaml").write_text("door_code: 5678\n")
    os.link(config_dir / "secrets.yaml", config_dir / "notes.yaml")
    key = _key(cap_yaml_edit="allow")
    names = ["secrets.yaml", "ſecrets.yaml", "packages/ſecrets.yaml", "ſECRETS.yaml", "notes.yaml"]
    for name in names:
        content, outcome, _ = await _call_tool("get_yaml_config", {"file": name}, key, hass, _yaml_data())
        text = content["content"][0]["text"]
        assert outcome == "invalid_request", name
        assert "1234" not in text and "5678" not in text
    (config_dir / "configuration.yaml").write_text("homeassistant:\n  name: Home\n")
    _content, outcome, _ = await _call_tool("get_yaml_config", {}, key, hass, _yaml_data())
    assert outcome == "allowed"


async def test_the_audit_summary_answers_a_hidden_entity_as_not_found(hass):
    _home(hass)
    key = _key()
    rows = [
        SimpleNamespace(request_id=str(index), timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        method="get_state", resource=resource, outcome=outcome, mesa_advisory=False)
        for index, (resource, outcome) in enumerate([
            (HIDDEN_LOCK, "denied"), ("lock.made_up", "not_found"), ("light.hall", "allowed"),
            ("set_integration_log_level", "denied"),
        ])
    ]
    audit = MagicMock()
    audit.query = MagicMock(side_effect=lambda **kwargs: None if kwargs.get("outcome") == "bogus" else rows)
    data = MagicMock(audit=audit)
    content, outcome, _ = await _call_tool("get_audit_summary", {}, key, hass, data)
    assert outcome == "allowed"
    shown = {row["resource"]: row["outcome"] for row in json.loads(content["content"][0]["text"])["entries"]}
    assert shown == {HIDDEN_LOCK: "not_found", "lock.made_up": "not_found", "light.hall": "allowed",
                     "set_integration_log_level": "denied"}
    content, _outcome, _ = await _call_tool("get_audit_summary", {"outcome": "denied"}, key, hass, data)
    listed = [row["resource"] for row in json.loads(content["content"][0]["text"])["entries"]]
    assert listed == ["set_integration_log_level"]
    _content, outcome, _ = await _call_tool("get_audit_summary", {"outcome": "bogus"}, key, hass, data)
    assert outcome == "invalid_request"



def test_the_shared_secrets_check_folds_names_and_compares_files(tmp_path):
    from custom_components.phoenix_mcp.yaml_includes import is_secrets_file

    (tmp_path / "secrets.yaml").write_text("alarm_code: 1234\n")
    os.link(tmp_path / "secrets.yaml", tmp_path / "kept.yaml")
    (tmp_path / "other.yaml").write_text("a: 1\n")
    for name in ("secrets.yaml", "ſecrets.yaml", "SECRETS.YAML", "ſECRETS.yaml", "kept.yaml"):
        assert is_secrets_file(str(tmp_path / name)), name
    assert not is_secrets_file(str(tmp_path / "other.yaml"))
