"""One Home Assistant user cannot take a shared key's whole approval queue, and a repeat queues nothing new.

Household callers (Voice, Assist, AI Task) reach the key's approvals with no rate limit, so each user
has MAX_PENDING_APPROVALS_PER_HOUSEHOLD_USER of the key's slots, and a user asking again for an action
still pending gets that approval back without a second notification or event. An agent using the
key's secret keeps one approval per call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp import tool_dispatch
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN, MAX_PENDING_APPROVALS_PER_HOUSEHOLD_USER
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.household_authority import household_scope
from custom_components.phoenix_mcp.rate_limiter import RateLimiter


async def _home(hass: HomeAssistant):
    store = AccessKeyStore(hass, AsyncMock())
    await store.async_patch_settings(mesa_mode="off")
    key, _ = await store.async_create_access_key("shared", "admin", rate_limit_requests=0)
    key.pass_through = True
    key.cap_physical_control = "confirm"
    key.confirm_inline_wait_seconds = 0
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    er.async_get(hass).async_get_or_create("lock", "test", "front", suggested_object_id="front")
    hass.states.async_set("lock.front", "locked")
    return store, key, data


async def _unlock(hass, key, data, user_id: str | None, code: str, request_id: str) -> str:
    arguments = {"service": {"domain": "lock", "name": "unlock", "data": {"code": code}},
                 "targets": [{"kind": "entity", "ids": ["lock.front"]}]}

    async def call():
        return await tool_dispatch._dispatch_mcp("tools/call", request_id, {"name": "call_service", "arguments": arguments},
                                                 key, hass, data, "voice", "http://h", request_id=request_id)

    if user_id is None:
        response = await call()
    else:
        async with household_scope(hass, Context(user_id=user_id)):
            response = await call()
    return response[0]["result"]["content"][0]["text"]


def _pending(store) -> list[dict]:
    return [row for row in store.get_pending_approvals() if row["status"] == "pending"]


async def test_one_household_user_takes_only_their_share_of_the_queue(hass: HomeAssistant):
    store, key, data = await _home(hass)
    guest = await hass.auth.async_create_user("guest", group_ids=[GROUP_ID_USER])
    other = await hass.auth.async_create_user("other", group_ids=[GROUP_ID_USER])
    for index in range(MAX_PENDING_APPROVALS_PER_HOUSEHOLD_USER):
        assert "pending_approval" in await _unlock(hass, key, data, guest.id, str(1000 + index), f"g{index}")
    refused = await _unlock(hass, key, data, guest.id, "2000", "g-over")
    assert f"already has {MAX_PENDING_APPROVALS_PER_HOUSEHOLD_USER} actions waiting" in refused
    assert len(_pending(store)) == MAX_PENDING_APPROVALS_PER_HOUSEHOLD_USER
    # Another person, and the key's own secret, still have room.
    assert "pending_approval" in await _unlock(hass, key, data, other.id, "3000", "o1")
    assert "pending_approval" in await _unlock(hass, key, data, None, "4000", "k1")
    assert len(_pending(store)) == MAX_PENDING_APPROVALS_PER_HOUSEHOLD_USER + 2


async def test_a_repeated_request_returns_the_waiting_approval_without_announcing_it_again(hass: HomeAssistant):
    store, key, data = await _home(hass)
    guest = await hass.auth.async_create_user("guest", group_ids=[GROUP_ID_USER])
    other = await hass.auth.async_create_user("other", group_ids=[GROUP_ID_USER])
    events = []
    hass.bus.async_listen(f"{DOMAIN}_approval_requested", events.append)
    first = await _unlock(hass, key, data, guest.id, "1234", "r1")
    again = await _unlock(hass, key, data, guest.id, "1234", "r2")
    await hass.async_block_till_done()
    assert len(_pending(store)) == 1 and len(events) == 1
    approval_id = _pending(store)[0]["id"]
    assert approval_id in first and approval_id in again
    # A different action, or the same action from someone else, is its own request.
    await _unlock(hass, key, data, guest.id, "9999", "r3")
    await _unlock(hass, key, data, other.id, "1234", "r4")
    await hass.async_block_till_done()
    assert len(_pending(store)) == 3 and len(events) == 3


async def test_an_agent_repeating_a_call_with_the_key_queues_each_one(hass: HomeAssistant):
    """An agent that sends the same call twice means it twice, so both wait and both run when approved."""
    store, key, data = await _home(hass)
    for request_id in ("k1", "k2"):
        await _unlock(hass, key, data, None, "1234", request_id)
    assert len(_pending(store)) == 2
