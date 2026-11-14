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
    PackageSource, esphome_rel_path_ok, inline_secret_values, mask_yaml_text, redact_esphome_text,
)
from custom_components.phoenix_mcp.redaction import (
    _scrub_log_text, entity_domains, redact_entity_ids_outside, redact_inaccessible_entity_ids_in_text,
    redact_secrets_in_text,
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
    "entity_id = event.data.get('entity_id')",
    "camera.stream.source and notify.mobile_app.send",
    "time.sleep.blocking, select.option.value, image.png.bak, sensor.yaml.bak, button.press.service",
])
async def test_readable_and_non_entity_chains_stay(hass, text):
    _home(hass)
    assert redact_inaccessible_entity_ids_in_text(text, _key(), hass) == text


@pytest.mark.parametrize("chain", ["lock.smart_lock.state", "lock.no_such_lock.state", "light.no_such_light.attributes.x"])
async def test_a_missing_entity_in_a_chain_reads_like_a_hidden_one(hass, chain):
    """A key that writes text and reads it back learns nothing from which chains come back."""
    _home(hass)
    key = _key()
    tail = chain.split(".", 2)[2]
    expected = f"note <redacted-entity>.{tail} end"
    assert redact_inaccessible_entity_ids_in_text(f"note {chain} end", key, hass) == expected
    worker = redact_entity_ids_outside(
        f"note {chain} end", frozenset({"light.hall"}), hass, chain_domains=entity_domains(hass))
    assert worker == expected


@pytest.mark.parametrize("text", [
    "router.local.lan", "backup.tar.gz", "version 6.3b2.1", "entity_id = event.data.get('entity_id')",
    "light.hall.attributes.brightness",
])
async def test_a_chain_outside_every_entity_domain_or_readable_stays(hass, text):
    _home(hass)
    assert redact_inaccessible_entity_ids_in_text(text, _key(), hass) == text
    assert redact_entity_ids_outside(text, frozenset({"light.hall"}), hass, chain_domains=entity_domains(hass)) == text


async def test_a_chain_in_a_domain_with_entities_is_judged_like_an_entity(hass):
    _home(hass)
    hass.states.async_set("event.doorbell", "unknown")
    assert redact_inaccessible_entity_ids_in_text("event.data.get", _key(), hass) == "<redacted-entity>.get"


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


@pytest.mark.parametrize("name", ["user_keyboard_layout", "cookies_enabled", "accept_cookies", "steam_appid"])
async def test_a_name_holding_a_credential_word_is_not_a_credential(hass, name):
    from custom_components.phoenix_mcp.policy_engine import is_sensitive_key, scrub_attributes

    assert not is_sensitive_key(name)
    assert _scrub_log_text(f"{name}=us") == f"{name}=us"
    assert scrub_attributes({name: "us"}, _key(), hass) == {name: "us"}
    for credential in ("cookie", "Set-Cookie", "user_key", "appid"):
        assert is_sensitive_key(credential)


@pytest.mark.parametrize("value", [
    "{{ trigger.webhook_id == 'garage' }}",
    "http://homeassistant.local:8123/api/webhook/garage-door",
    "webhook_id: garage-door",
])
def test_a_recovery_copy_keeps_a_webhook_id_that_readers_mask(value):
    from custom_components.phoenix_mcp.config_recovery import safe_snapshot

    assert redact_secrets_in_text(value) != value
    assert redact_secrets_in_text(value, reader=False) == value
    assert safe_snapshot({"id": "a", "actions": [{"data": {"text": value}}], "webhook_id": "garage-door"})
    hooks = "https://hooks.slack.com/services/T000/B000/XXXXsecret"
    with pytest.raises(Exception, match="credential"):
        safe_snapshot({"id": "a", "actions": [{"data": {"url": hooks}}]})


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
        "  relay_pin: GPIO12\n"
        "  pkg_api_key: hunter33word\n"
        "esphome:\n  name: ${name}\n"
        "api:\n  encryption:\n    key: ${enc}\n"
        "ota:\n  - platform: esphome\n    password: $ota_word\n"
        "wifi:\n  password: ${pw_full}\n"
        "packages:\n  common: !include\n    file: common.yaml\n    vars:\n      friendly: Kitchen Plug\n"
        "      ota_password: hunter22\n"
    )
    # The package is read, and uses each of these as an ordinary value.
    source = PackageSource("dev.yaml", {
        "common.yaml": "sensor:\n  - name: ${friendly} ${pkg_only}\n    pin: ${relay_pin}\n"}.get)
    masked, paths = redact_esphome_text(doc, None, source)
    for secret in (_KEY, "otaotaota9", "partpartpart", "hunter22", "hunter33word"):
        assert secret not in masked
    # A substitution or package variable no credential refers to stays.
    for kept in ("name: rf-blaster1", "pkg_only: used-by-a-package", "relay_pin: GPIO12", "friendly: Kitchen Plug"):
        assert kept in masked
    assert {"substitutions.enc", "substitutions.ota_word", "substitutions.part"} <= set(paths)
    # The values join the scrub of Builder output too; the readable ones do not.
    assert _KEY in inline_secret_values(doc, None, source)
    assert not {"used-by-a-package", "Kitchen Plug", "rf-blaster1"} & inline_secret_values(doc, None, source)


def test_folded_names_and_hidden_characters_are_refused():
    refused = (
        "ſecrets.yaml", "ſECRETS.yaml", "packages/ſecrets.yaml", "secr\u200bets.yaml", "secrets\u00ad.yaml",
        "packages/door\u202eamly.yaml", "door\u2066.yaml", "door\x07.yaml", "door\u0378.yaml", "door\u2028.yaml",
        "\u200b.storage/core.yaml",
    )
    for name in refused:
        assert not esphome_rel_path_ok(name), name
    readable = (
        "door.yaml", "packages/küche.yaml", "厨房/灯.yaml", "ﬁle.yaml", "eſphome/door.yaml",
        "packages/\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645.yaml", "packages/a\u00a0b.yaml",
        "\U0001F468\u200d\U0001F469.yaml", "door\u00ad.yaml",
    )
    for name in readable:
        assert esphome_rel_path_ok(name), name


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


async def test_get_yaml_config_reads_printable_non_ascii_names(hass, config_dir):
    (config_dir / "secrets.yaml").write_text("alarm_code: 1234\n")
    (config_dir / "packages").mkdir()
    (config_dir / "packages" / "küche.yaml").write_text("light_level: 3\n")
    (config_dir / "厨房.yaml").write_text("fan_speed: 2\n")
    persian = "packages/\u0622\u0634\u067e\u0632\u200c\u062e\u0627\u0646\u0647.yaml"
    (config_dir / persian).write_text("oven_mode: 4\n")
    os.link(config_dir / "secrets.yaml", config_dir / "密钥.yaml")
    key = _key(cap_yaml_edit="allow")
    for name, value in (("packages/küche.yaml", "light_level"), ("厨房.yaml", "fan_speed"), (persian, "oven_mode")):
        content, outcome, _ = await _call_tool("get_yaml_config", {"file": name}, key, hass, _yaml_data())
        assert outcome == "allowed", name
        assert value in content["content"][0]["text"]
    hidden = ("ſecrets.yaml", "secr\u200bets.yaml", "secrets\u202e.yaml", "\u202esecrets.yaml", "密钥.yaml",
              "eſphome/door.yaml")
    for name in hidden:
        content, outcome, _ = await _call_tool("get_yaml_config", {"file": name}, key, hass, _yaml_data())
        assert outcome == "invalid_request", name
        assert "1234" not in content["content"][0]["text"]


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


async def test_the_audit_summary_folds_the_case_of_a_probed_id(hass):
    from custom_components.phoenix_mcp.access_key_store import GlobalSettings
    from custom_components.phoenix_mcp.audit import AuditLog

    _home(hass)
    key = _key()
    log = AuditLog()
    data = SimpleNamespace(audit=log)
    probes = ["LOCK.SMART_LOCK", "Lock.Smart_Lock", "LOCK.MADE_UP", "lock.made_up"]
    for index, probe in enumerate(probes):
        for tool in ("get_state", "get_history"):
            content, outcome, resource = await _call_tool(tool, {"entity_id": probe}, key, hass, data)
            assert "not found" in content["content"][0]["text"], (tool, probe)
            log.record(request_id=f"{tool}-{index}", access_key_id=key.id, access_key_name="t", method=tool,
                       resource=resource, outcome=outcome, client_ip="x", settings=GlobalSettings())
    content, _outcome, _ = await _call_tool("get_audit_summary", {"limit": 50}, key, hass, data)
    entries = json.loads(content["content"][0]["text"])["entries"]
    assert len(entries) == 8
    assert {row["outcome"] for row in entries} == {"not_found"}



def test_the_shared_secrets_check_folds_names_and_compares_files(tmp_path):
    from custom_components.phoenix_mcp.yaml_includes import is_secrets_file

    (tmp_path / "secrets.yaml").write_text("alarm_code: 1234\n")
    os.link(tmp_path / "secrets.yaml", tmp_path / "kept.yaml")
    (tmp_path / "other.yaml").write_text("a: 1\n")
    for name in ("secrets.yaml", "ſecrets.yaml", "SECRETS.YAML", "ſECRETS.yaml", "kept.yaml"):
        assert is_secrets_file(str(tmp_path / name)), name
    assert not is_secrets_file(str(tmp_path / "other.yaml"))
