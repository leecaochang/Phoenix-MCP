"""get_logs source=integration_debug reads scrubbed, scoped INFO/DEBUG records."""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.debug_capture import async_attach_debug_capture, async_detach_debug_capture
from custom_components.phoenix_mcp.mcp_view import _call_tool


@pytest.fixture
def capture(hass):
    handler = async_attach_debug_capture(hass)
    loggers = [logging.getLogger(name) for name in ("homeassistant.components.hue", "homeassistant.components.zha",
                                                     "custom_components.phoenix_mcp")]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.DEBUG)
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
