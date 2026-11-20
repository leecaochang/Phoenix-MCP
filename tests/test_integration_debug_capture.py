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
        async_update_debug_capture(hass, domain, "DEBUG", OWNER)
    yield handler
    for logger, level in zip(loggers, levels):
        logger.setLevel(level)
    async_detach_debug_capture(hass)


OWNER = str(uuid.uuid4())


def _key(key_id: str = OWNER, **caps) -> AccessKeyRecord:
    return AccessKeyRecord(id=key_id, name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
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
    async_update_debug_capture(hass, "phoenix_mcp", "DEBUG", OWNER)
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


async def test_only_the_raising_key_reads_the_capture(hass, capture):
    """Debug output can carry other clients' traffic, so a second key with log read sees none of it."""
    er.async_get(hass).async_get_or_create("light", "hue", "bulb", suggested_object_id="bulb")
    hue = logging.getLogger("homeassistant.components.hue")
    hue.debug("Received client message from another client")
    outcome, other = await _read(hass, _key(str(uuid.uuid4())))
    assert outcome == "allowed"
    assert other["entries"] == [] and other["source"]["capturing"] == [] and other["source"]["retained"] == 0
    _, own = await _read(hass, _key())
    assert [entry["message"] for entry in own["entries"]] == ["Received client message from another client"]


async def test_a_key_joining_a_capture_reads_only_what_was_written_after(hass, capture):
    er.async_get(hass).async_get_or_create("light", "hue", "bulb", suggested_object_id="bulb")
    hue = logging.getLogger("homeassistant.components.hue")
    later = str(uuid.uuid4())
    hue.debug("before the second raise")
    async_update_debug_capture(hass, "hue", "DEBUG", later)
    hue.debug("after the second raise")
    _, body = await _read(hass, _key(later))
    assert [entry["message"] for entry in body["entries"]] == ["after the second raise"]
    _, first = await _read(hass, _key())
    assert len(first["entries"]) == 2


async def test_archiving_the_raising_key_ends_its_capture(hass, capture):
    """A revoke or archive goes through the store, which ends what the key raised."""
    from unittest.mock import AsyncMock

    from custom_components.phoenix_mcp.access_key_store import AccessKeyStore

    store = AccessKeyStore(hass, AsyncMock())
    record, _raw = await store.async_create_access_key("raiser", "admin", rate_limit_requests=0)
    shared = str(uuid.uuid4())
    async_update_debug_capture(hass, "hue", "DEBUG", record.id)
    async_update_debug_capture(hass, "zha", "DEBUG", record.id)
    async_update_debug_capture(hass, "zha", "DEBUG", shared)
    logging.getLogger("homeassistant.components.hue").debug("hue record")
    logging.getLogger("homeassistant.components.zha").debug("zha record")
    await store.async_archive_access_key(record.id, revoked=True)
    # hue is still held by the fixture's key; the archived key owns nothing any more.
    assert not capture.domains_for(record.id)
    assert capture.captures_for("zha", shared)
    assert all(record.id not in row[6] or row[6] - {record.id} for row in capture.records)
    capture.forget(OWNER)
    capture.forget(shared)
    assert capture.domains == set() and not capture.records
    assert capture not in logging.getLogger("homeassistant.components.hue").handlers


async def _raised_by_phoenix(hass, domain: str = "demo"):
    """Raise `domain` to DEBUG through Phoenix's manager on the real logger and capture it for OWNER."""
    from homeassistant.setup import async_setup_component

    from custom_components.phoenix_mcp.logger_control import IntegrationOverride, async_get_logger_override_manager

    assert await async_setup_component(hass, "logger", {})
    manager = await async_get_logger_override_manager(hass)
    await manager.async_set(domain=domain, desired=IntegrationOverride("DEBUG", "none"),
                            prior=manager.adapter.get_override(domain),
                            loggers=await manager.adapter.declared_loggers(domain), owner_access_key_id=OWNER,
                            duration_minutes=None)
    handler = async_attach_debug_capture(hass)
    async_update_debug_capture(hass, domain, "DEBUG", OWNER, manager.adapter.setting_witness(domain))
    return handler, manager


async def _set_outside_phoenix(hass, domain: str, level: str) -> None:
    """Change the level the way the Logger settings page does."""
    from homeassistant.components.logger.helpers import DATA_LOGGER, LoggerSetting

    await hass.data[DATA_LOGGER].settings.async_update(hass, domain, LoggerSetting(level, "none", "integration"))


@pytest.mark.parametrize("outside", [["WARNING", "DEBUG"], ["DEBUG"]])
async def test_someone_elses_raise_never_feeds_an_old_capture(hass, hass_admin_user, outside):
    """Lowered and raised again (or raised again at the same level) in Home Assistant: the old owner reads none of it."""
    handler, _ = await _raised_by_phoenix(hass)
    demo = logging.getLogger("homeassistant.components.demo")
    try:
        demo.debug("written while Phoenix held the raise")
        assert handler.newest({"demo"}, None, OWNER)
        for level in outside:
            await _set_outside_phoenix(hass, "demo", level)
        demo.debug("an administrator troubleshooting: lock.front_door unlocked by keypad user 3")
        assert not any("keypad" in row[4] for row in handler.records)
        _, body = await _read(hass, _key(), integration="demo")
        assert body["entries"] == [] and body["source"]["capturing"] == []
        assert handler.domains == set() and capture_not_attached(handler, demo)
    finally:
        async_detach_debug_capture(hass)


def capture_not_attached(handler, logger) -> bool:
    return handler not in logger.handlers


async def test_a_level_changed_by_the_logger_action_ends_the_capture_at_the_next_check(hass, hass_admin_user):
    """logger.set_level changes levels without touching the stored setting, so the periodic check catches it."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant.common import async_fire_time_changed

    from custom_components.phoenix_mcp.const import DEBUG_CAPTURE_RECHECK_SECONDS

    handler, _ = await _raised_by_phoenix(hass)
    demo = logging.getLogger("homeassistant.components.demo")
    try:
        assert handler.unsubscribe_recheck is not None
        await hass.services.async_call("logger", "set_level", {"homeassistant.components.demo": "warning"},
                                       blocking=True)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DEBUG_CAPTURE_RECHECK_SECONDS + 1))
        await hass.async_block_till_done()
        assert handler.domains == set()
        assert handler.unsubscribe_recheck is None
        await hass.services.async_call("logger", "set_level", {"homeassistant.components.demo": "debug"},
                                       blocking=True)
        demo.debug("someone else's session")
        assert handler.records == deque()
    finally:
        async_detach_debug_capture(hass)


async def test_another_key_raising_through_phoenix_keeps_the_first_capture(hass, hass_admin_user):
    """Phoenix's own raise is not an outside change: both keys keep capturing."""
    handler, manager = await _raised_by_phoenix(hass)
    other = str(uuid.uuid4())
    try:
        from custom_components.phoenix_mcp.logger_control import IntegrationOverride

        await manager.async_set(domain="demo", desired=IntegrationOverride("DEBUG", "none"),
                                prior=manager.adapter.get_override("demo"),
                                loggers=await manager.adapter.declared_loggers("demo"), owner_access_key_id=other,
                                duration_minutes=None)
        async_update_debug_capture(hass, "demo", "DEBUG", other, manager.adapter.setting_witness("demo"))
        logging.getLogger("homeassistant.components.demo").debug("both keys raised this")
        assert handler.reconcile() == set()
        assert handler.newest({"demo"}, None, OWNER) and handler.newest({"demo"}, None, other)
    finally:
        async_detach_debug_capture(hass)
