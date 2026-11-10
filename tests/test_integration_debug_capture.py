"""get_logs source=integration_debug reads scrubbed, scoped INFO/DEBUG records.

Only integrations whose level Phoenix raised are captured; any other integration's
records never enter the buffer, and lowering the level drops what was kept.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.debug_capture import (
    async_attach_debug_capture, async_detach_debug_capture, async_update_debug_capture,
)
from custom_components.phoenix_mcp.tool_dispatch import _call_tool


@pytest.fixture
def capture(hass):
    handler = async_attach_debug_capture(hass)
    loggers = [logging.getLogger(name) for name in ("homeassistant.components.hue", "homeassistant.components.zha",
                                                     "custom_components.phoenix_mcp")]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.DEBUG)
    for domain in ("hue", "zha"):
        async_update_debug_capture(hass, domain, "DEBUG")
    yield handler
    for logger, level in zip(loggers, levels):
        logger.setLevel(level)
    async_detach_debug_capture(hass)


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                           permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}),
                           **{"cap_log_read": "allow", "cap_diagnostics": "allow", **caps})


async def _read(hass, key, **args):
    result, outcome, _ = await _call_tool("get_logs", {"source": "integration_debug", **args}, key, hass, MagicMock())
    return outcome, json.loads(result["content"][0]["text"]) if outcome == "allowed" else result["content"][0]["text"]


async def test_only_visible_scrubbed_below_warning_records_are_returned(hass, capture):
    er.async_get(hass).async_get_or_create("light", "hue", "bulb", suggested_object_id="bulb")
    hue, zha = logging.getLogger("homeassistant.components.hue"), logging.getLogger("homeassistant.components.zha")
    hue.debug("polling 192.168.1.40 with token=s3cr3t for light.bulb and lock.vault")
    hue.warning("already in the system log")
    zha.debug("zha is not visible to this key")
    logging.getLogger("custom_components.phoenix_mcp").debug("phoenix's own record")
    outcome, body = await _read(hass, _key())
    assert outcome == "allowed"
    assert [entry["integration"] for entry in body["entries"]] == ["hue"]
    message = body["entries"][0]["message"]
    assert "light.bulb" in message
    for secret in ("192.168.1.40", "s3cr3t", "lock.vault"):
        assert secret not in message
    _, searched = await _read(hass, _key(), search="s3cr3t")
    assert searched["entries"] == []


async def test_newest_first_pages_with_a_sequence_cursor(hass, capture):
    er.async_get(hass).async_get_or_create("light", "hue", "bulb", suggested_object_id="bulb")
    for index in range(5):
        logging.getLogger("homeassistant.components.hue").info("step %s", index)
    _, first = await _read(hass, _key(), limit=2)
    assert [entry["message"] for entry in first["entries"]] == ["step 4", "step 3"] and first["has_more"]
    _, second = await _read(hass, _key(), limit=2, cursor=first["next_cursor"])
    assert [entry["message"] for entry in second["entries"]] == ["step 2", "step 1"]


async def test_diagnostics_is_required_and_other_filters_are_refused(hass, capture):
    outcome, _ = await _read(hass, _key(cap_diagnostics="deny"))
    assert outcome == "denied"
    outcome, text = await _read(hass, _key(), level="DEBUG")
    assert outcome == "invalid_request" and "accepts integration" in text


async def test_an_integration_phoenix_did_not_raise_is_never_captured(hass, capture):
    """A logger left at DEBUG by the user's own configuration stays out of the buffer."""
    er.async_get(hass).async_get_or_create("light", "hue", "bulb", suggested_object_id="bulb")
    other = logging.getLogger("homeassistant.components.light")
    level = other.level
    other.setLevel(logging.DEBUG)
    try:
        other.debug("Sending login {'password': 'hunter2'}")
        logging.getLogger("custom_components.someone_else").debug("not raised either")
    finally:
        other.setLevel(level)
    assert capture.records == deque() or all(
        row[3].startswith(("homeassistant.components.hue", "homeassistant.components.zha"))
        for row in capture.records)
    outcome, body = await _read(hass, _key())
    assert outcome == "allowed" and body["entries"] == []
    assert body["source"]["capturing"] == ["hue"]


async def test_lowering_the_level_stops_the_capture_and_drops_its_records(hass, capture):
    er.async_get(hass).async_get_or_create("light", "hue", "bulb", suggested_object_id="bulb")
    hue = logging.getLogger("homeassistant.components.hue")
    hue.debug("kept while raised")
    async_update_debug_capture(hass, "hue", "WARNING")
    hue.debug("after the level was lowered")
    assert not any(row[3] == "homeassistant.components.hue" for row in capture.records)
    assert capture not in hue.handlers
    _, body = await _read(hass, _key())
    assert body["entries"] == []


async def test_phoenix_never_captures_itself(hass, capture):
    async_update_debug_capture(hass, "phoenix_mcp", "DEBUG")
    assert "phoenix_mcp" not in capture.domains
    assert capture not in logging.getLogger("custom_components.phoenix_mcp").handlers


async def test_quoted_and_dict_secrets_are_scrubbed(hass, capture):
    """The credential shapes an integration logs while being debugged."""
    er.async_get(hass).async_get_or_create("light", "hue", "bulb", suggested_object_id="bulb")
    hue = logging.getLogger("homeassistant.components.hue")
    hue.debug("Sending login %s", {"username": "bob", "password": "hunter2"})
    hue.debug("Headers %s", {"Authorization": "Bearer abc.def-123", "Accept": "json"})
    hue.debug('Reply {"access_token": "tok-999", "api_key": "k-777"}')
    hue.debug("connect(host='h', wifi_password='p w 1', port=1)")
    _, body = await _read(hass, _key())
    text = " ".join(entry["message"] for entry in body["entries"])
    for secret in ("hunter2", "abc.def-123", "tok-999", "k-777", "p w 1"):
        assert secret not in text
    assert "'username': 'bob'" in text and "'Accept': 'json'" in text
