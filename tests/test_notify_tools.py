"""send_notification and get_notifications against Home Assistant's own notification services.

Sending needs cap_notify; a notify entity also needs WRITE. Phoenix's own operator
notifications never reach an agent, and notification text is scrubbed.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components import persistent_notification
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool
from tests.runtime_fixtures import mesa_off_store


@pytest.fixture
async def home(hass, hass_admin_user):
    assert await async_setup_component(hass, "persistent_notification", {})
    calls = AsyncMock()
    hass.services.async_register("notify", "mobile_app_alex_phone", calls)
    hass.services.async_register("notify", "send_message", calls)
    hass.states.async_set("notify.office_screen", "unknown")
    hass.states.async_set("notify.secret_screen", "unknown")
    return calls


def _key(**caps) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(entities={"notify.office_screen": PermissionNode(state="GREEN")}),
        **{"cap_notify": "allow", "cap_log_read": "allow", **caps},
    )


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock(), mesa=None)


async def _call(hass, tool, args, key=None):
    content, outcome, _ = await _call_tool(tool, args, key or _key(), hass, _data())
    text = content["content"][0]["text"]
    return (json.loads(text) if text.startswith("{") else text), outcome


async def test_a_persistent_notification_is_shown_and_read_back(hass, home):
    body, outcome = await _call(hass, "send_notification", {"target": "persistent_notification",
                                                            "title": "Laundry", "message": "The washer is done."})
    assert outcome == "allowed" and body["sent"] is True and body["verified"] is True
    listed, _ = await _call(hass, "get_notifications", {})
    assert [(row["title"], row["message"]) for row in listed["notifications"]] == [("Laundry", "The washer is done.")]


@pytest.mark.parametrize(("target", "service", "extra"), [
    ("mobile_app_alex_phone", "mobile_app_alex_phone", {}),
    ("notify.mobile_app_alex_phone", "mobile_app_alex_phone", {}),
    ("notify.office_screen", "send_message", {"entity_id": "notify.office_screen"}),
])
async def test_a_phone_service_or_a_writable_notify_entity_is_sent_to(hass, home, target, service, extra):
    body, outcome = await _call(hass, "send_notification", {"target": target, "message": "Dinner is ready"})
    assert outcome == "allowed" and body["sent"] is True and body["verified"] is None
    call = home.await_args.args[0]
    assert call.service == service and call.data == {"message": "Dinner is ready", **extra}


async def test_an_unknown_or_hidden_target_lists_only_what_the_key_can_use(hass, home):
    for target in ("notify.secret_screen", "nobody"):
        text, outcome = await _call(hass, "send_notification", {"target": target, "message": "hi"})
        assert outcome == "invalid_request" and "secret_screen" not in text
        assert "persistent_notification" in text and "notify.office_screen" in text and "mobile_app_alex_phone" in text
    assert home.await_count == 0


@pytest.mark.parametrize("message", ["request_location_update", "command_dnd", "TTS"])
async def test_a_mobile_app_command_is_not_sent(hass, home, message):
    text, outcome = await _call(hass, "send_notification", {"target": "mobile_app_alex_phone", "message": message})
    assert outcome == "invalid_request" and "mobile app command" in text and home.await_count == 0


async def test_a_denied_capability_sends_nothing(hass, home):
    _, outcome = await _call(hass, "send_notification", {"target": "persistent_notification", "message": "x"},
                             _key(cap_notify="deny"))
    assert outcome == "denied" and persistent_notification._async_get_or_create_notifications(hass) == {}


async def test_phoenix_notifications_are_hidden_and_addresses_scrubbed(hass, home):
    persistent_notification.async_create(hass, "Approve unlock of the front door?", "Approval", "phoenix_mcp_approval_1")
    persistent_notification.async_create(
        hass, "Login attempt or request with invalid authentication from 192.168.1.77", "Login attempt failed", "http-login")
    listed, outcome = await _call(hass, "get_notifications", {})
    assert outcome == "allowed" and [row["notification_id"] for row in listed["notifications"]] == ["http-login"]
    assert "192.168.1.77" not in json.dumps(listed)
    _, outcome = await _call(hass, "get_notifications", {}, _key(cap_log_read="deny"))
    assert outcome == "denied"
