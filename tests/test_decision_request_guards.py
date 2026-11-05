"""Every routing ingress refuses to send household context after authority moves.

The provider is faked at the aiohttp boundary, so `DecisionProvider._request` and the
guard each ingress installs run for real. A change lands in the last await before a
request or while the previous question is in flight, and the assertion is on what
reached the wire: the request is never sent, whatever the post-reply checks would say.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.llm import accounts as llm_accounts
from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree
from custom_components.phoenix_mcp.routing.command_routing import RoutingAuthority, async_evaluate_command
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.routing.routing_tables import DECISION_CONTEXT_REQUIRED_CHOICES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.routing.decision_batch import async_preview_batch
from custom_components.phoenix_mcp.routing.decision_context import async_preview_context
from custom_components.phoenix_mcp.routing.decision_provider import DecisionError, DecisionProvider
from custom_components.phoenix_mcp.routing.decision_reference import async_preview_reference
from custom_components.phoenix_mcp.routing.decision_shadow import async_preview_decision
from custom_components.phoenix_mcp.routing.decision_batch import async_prepare_temporal

MISS = {"reason": "no_match", "proposal": None}
LAMP_SENTENCE = "Switch on the desk lamp"
PERCENT_SENTENCE = "set Desk lamp to thirty seven percent brightness"
TIMER_SENTENCE = "Start Tea timer for five minutes"
HISTORY = [{"role": "user", "content": "turn on Desk lamp"}, {"role": "assistant", "content": "Command sent."}]
CONTEXT_HISTORY = [{"role": "user", "content": "We are discussing how brightness works."},
                   {"role": "assistant", "content": "Brightness is expressed as a percentage."}]


class Home:
    """One real key, real entity registry and real account store behind the wire."""

    def __init__(self, hass, data, access_key, store, account, wire) -> None:
        self.hass, self.data, self.access_key = hass, data, access_key
        self.store, self.account, self.wire = store, account, wire

    async def change_account(self) -> None:
        assert await self.store.set_model(self.account, "jev-1.13.0")

    async def change_key(self) -> None:
        assert await self.data.store.async_archive_access_key(self.access_key.id, True)

    async def change_catalogue(self) -> None:
        entry = er.async_get(self.hass).async_get_or_create(
            "light", "test", "shelf", original_name="Shelf lamp", suggested_object_id="shelf")
        self.hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Shelf lamp"})

    async def change_target(self) -> None:
        self.hass.states.async_set("light.test_desk", "off", {
            "friendly_name": "Renamed lamp", "supported_color_modes": ["brightness"]})

    async def change_timezone(self) -> None:
        await self.hass.config.async_set_time_zone("Asia/Tokyo")


@pytest.fixture
async def home(hass, access_key_store, decision_wire):
    access_key, _ = await access_key_store.async_create_access_key("guards", "test")
    access_key.permissions = PermissionTree(domains={
        "light": PermissionNode(state="GREEN"), "timer": PermissionNode(state="GREEN")})
    data = PhoenixData(store=access_key_store, rate_limiter=MagicMock(), audit=MagicMock())
    data.decision_budget = decision_wire.budget_gate()
    hass.data[DOMAIN] = data
    store = llm_accounts.AgentCliStore(AsyncMock())
    hass.data["_phoenix_agentcli_store"] = store
    account = await store.add("typesafe", {"api_key": "wire-secret", "model": "jev-latest"})
    await access_key_store.async_patch_settings(
        decision_provider_id=account, command_routing_timeout_ms=5000, agentcli_routing_mode="hybrid", mesa_mode="off")
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    registry = er.async_get(hass)
    lamp = registry.async_get_or_create("light", "test", "desk", original_name="Desk lamp")
    hass.states.async_set(lamp.entity_id, "off", {"friendly_name": "Desk lamp", "supported_color_modes": ["brightness"]})
    async_expose_entity(hass, "conversation", lamp.entity_id, True)
    timer = registry.async_get_or_create("timer", "test", "tea", original_name="Tea timer")
    hass.states.async_set(timer.entity_id, "idle", {"friendly_name": "Tea timer"})
    yield Home(hass, data, access_key, store, account, decision_wire)
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


async def _classify(home):
    return await async_preview_decision(home.hass, home.data, home.access_key, LAMP_SENTENCE, "en", MISS)


async def _sequential(home):
    home.wire.picks = {"route": "light_on", "target": "t0"}
    return await async_preview_decision(home.hass, home.data, home.access_key, LAMP_SENTENCE, "en", MISS, targets=True)


async def _joint(home):
    home.wire.picks = {"route": "light_on", "target": "t0"}
    return await async_preview_decision(home.hass, home.data, home.access_key, LAMP_SENTENCE, "en", MISS,
                                        targets=True, prepare=True, ingress="chat")


async def _batch_preview(home):
    home.wire.picks = {"route": "light_on", "target": "t0"}
    return await async_preview_batch(home.hass, home.data, home.access_key, LAMP_SENTENCE, "en", MISS)


async def _context(home):
    local = await async_evaluate_command(home.hass, home.data, home.access_key, PERCENT_SENTENCE, "en", preview=True)
    assert local["reason"] == "supported"
    home.wire.picks = dict(DECISION_CONTEXT_REQUIRED_CHOICES)
    return await async_preview_context(home.hass, home.data, home.access_key, PERCENT_SENTENCE, "en",
                                       CONTEXT_HISTORY, local)


async def _reference(home):
    home.wire.picks = {"route": "light_off", "reference": "bound", "constraints": "clear"}
    return await async_preview_reference(home.hass, home.data, home.access_key, "turn it off", "en", HISTORY, MISS)


async def _temporal(home):
    home.wire.picks = {"route": "timer_duration", "target": "t0", "value": "value"}
    settings = home.data.store.get_settings()
    cfg = home.store.resolve(home.account, purpose="decision")
    provider = DecisionProvider(cfg, "systemone", request_budget=home.data.decision_budget,
                                request_limit=settings.decision_request_budget)
    authority = RoutingAuthority.capture(home.data, home.access_key)
    try:
        category, selection = await async_prepare_temporal(
            home.hass, authority, provider, async_get_clientsession(home.hass), TIMER_SENTENCE, "en", "chat",
            {"kind": "duration", "value": "00:05:00"})
    except DecisionError as error:
        return {"reason": str(error)}
    return {"reason": selection["reason"], "category": category}


# Each row: ingress, change, POSTs the ingress sends unhindered, the POST the change
# lands in front of, and the refusal it must report.
BEFORE_SEND = [
    (_classify, "account", 1, 1, "authority_changed"),
    (_classify, "key", 1, 1, "authority_changed"),
    (_sequential, "account", 2, 2, "authority_changed"),
    (_sequential, "key", 2, 2, "authority_changed"),
    (_sequential, "catalogue", 2, 2, "targets_changed"),
    (_joint, "account", 1, 1, "authority_changed"),
    (_joint, "key", 1, 1, "authority_changed"),
    (_joint, "catalogue", 1, 1, "targets_changed"),
    (_batch_preview, "account", 1, 1, "authority_changed"),
    (_batch_preview, "key", 1, 1, "authority_changed"),
    (_batch_preview, "catalogue", 1, 1, "targets_changed"),
    (_context, "account", 1, 1, "authority_changed"),
    (_context, "key", 1, 1, "authority_changed"),
    (_context, "target", 1, 1, "context_changed"),
    (_reference, "account", 1, 1, "authority_changed"),
    (_reference, "key", 1, 1, "authority_changed"),
    (_reference, "catalogue", 1, 1, "context_changed"),
    (_temporal, "account", 1, 1, "authority_changed"),
    (_temporal, "key", 1, 1, "authority_changed"),
    (_temporal, "catalogue", 1, 1, "targets_changed"),
    (_temporal, "timezone", 1, 1, "authority_changed"),
    (_batch_preview, "timezone", 1, 1, "targets_changed"),
]


def _refusal(result: dict) -> str:
    """The reason a stopped request surfaced under, at the top level or from target selection."""
    selection = result.get("target_selection")
    return selection["reason"] if selection else result["reason"]


@pytest.mark.parametrize("ingress", sorted({row[0] for row in BEFORE_SEND}, key=lambda f: f.__name__),
                         ids=lambda f: f.__name__.strip("_"))
async def test_unchanged_authority_reaches_the_provider(home, ingress):
    """Positive control: without a change every request goes out with the account's bearer."""
    await ingress(home)
    expected = max(row[2] for row in BEFORE_SEND if row[0] is ingress)
    assert len(home.wire.posts) == expected


@pytest.mark.parametrize("ingress,change,_total,before,reason", BEFORE_SEND,
                         ids=[f"{row[0].__name__.strip('_')}-{row[1]}" for row in BEFORE_SEND])
async def test_a_change_before_send_keeps_the_request_off_the_wire(home, ingress, change, _total, before, reason):
    home.wire.before_send[before] = getattr(home, f"change_{change}")
    result = await ingress(home)
    assert len(home.wire.posts) == before - 1
    assert _refusal(result) == reason
    assert result.get("executed", False) is False


@pytest.mark.parametrize("change,reason", [("account", "authority_changed"), ("key", "authority_changed")])
async def test_a_change_between_two_questions_stops_the_second_post(home, change, reason):
    """The classifier answered for the old account; the target question must not follow.

    A catalogue change here is not stale: the second question captures its targets afterwards.
    """
    home.wire.after_reply[1] = getattr(home, f"change_{change}")
    result = await _sequential(home)
    assert len(home.wire.posts) == 1
    assert _refusal(result) == reason


@pytest.mark.parametrize("change,reason", [
    ("account", "authority_changed"), ("key", "authority_changed"), ("catalogue", "targets_changed")])
async def test_a_change_during_catalog_admission_stops_the_full_catalog_post(hass, home, change, reason):
    """An oversized catalog asks first which kind of query this is, then sends the catalog."""
    area = ar.async_get(hass).async_create("Room")
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", "room", original_name="Room lamp")
    registry.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "Room lamp"})
    home.wire.picks = {"catalog": "entity", "route": "light_on", "target": "t0"}
    home.wire.after_reply[1] = getattr(home, f"change_{change}")
    with patch("custom_components.phoenix_mcp.routing.decision_provider.DECISION_BATCH_MAX_TARGET_BYTES", 1):
        result = await async_preview_batch(hass, home.data, home.access_key, LAMP_SENTENCE, "en", MISS)
    assert "catalog" in home.wire.posts[0]["questions"]
    assert len(home.wire.posts) == 1
    assert result["reason"] == reason


async def test_catalog_admission_positive_control_sends_both_questions(hass, home):
    area = ar.async_get(hass).async_create("Room")
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", "room", original_name="Room lamp")
    registry.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "Room lamp"})
    home.wire.picks = {"catalog": "entity", "route": "light_on", "target": "t0"}
    with patch("custom_components.phoenix_mcp.routing.decision_provider.DECISION_BATCH_MAX_TARGET_BYTES", 1):
        await async_preview_batch(hass, home.data, home.access_key, LAMP_SENTENCE, "en", MISS)
    assert len(home.wire.posts) == 2
