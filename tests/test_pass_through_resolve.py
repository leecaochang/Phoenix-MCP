"""Pass-through access keys take entity scope from resolve() wherever entities are picked.

Broadcast targeting, the GetLiveContext listing and the admin scope listing each
select entities themselves rather than taking a resolved list. A pass-through
key reaches every entity resolve() grants, and nothing it refuses: its Assist
filter and Phoenix's own entities apply there as on every other path. Each
exposure check runs with the filter off too, so an absence is the filter and not
a broken fixture.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import policy_engine
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, AccessKeyStore, PermissionTree
from custom_components.phoenix_mcp.admin.permissions import PhoenixAdminScopeView
from custom_components.phoenix_mcp.const import ANNOUNCE_BIT, DOMAIN, MESA_MODE_OFF
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.policy_engine import Permission
from custom_components.phoenix_mcp.tools import native

NATIVE = "custom_components.phoenix_mcp.tools.native"
SHOWN, HIDDEN = "assist_satellite.kitchen", "assist_satellite.porch"


def _key(*, filtered: bool = True) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(), pass_through=True, use_assist_exposure=filtered,
    )


def _data(access_key: AccessKeyRecord | None = None) -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    store.get_settings.return_value = SimpleNamespace(mesa_mode=MESA_MODE_OFF)
    store.get_access_key_by_id.return_value = access_key
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock())


def _admin_request() -> MagicMock:
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

    user = MagicMock(is_admin=True, id="admin-user")
    state: dict = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "rid"}
    request = MagicMock()
    request.query = {}
    request.__getitem__ = MagicMock(side_effect=state.get)
    request.__setitem__ = MagicMock(side_effect=state.__setitem__)
    request.get = MagicMock(side_effect=lambda k, default=None: state.get(k, default))
    return request


@pytest.fixture
async def home(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    for entity_id in (SHOWN, HIDDEN):
        hass.states.async_set(entity_id, "idle", {"supported_features": ANNOUNCE_BIT})
    async_expose_entity(hass, "conversation", SHOWN, True)
    async_expose_entity(hass, "conversation", HIDDEN, False)
    return hass


@pytest.mark.parametrize("filtered,expected", [(True, [SHOWN]), (False, sorted([SHOWN, HIDDEN]))])
async def test_broadcast_announces_only_on_satellites_the_key_reaches(home, filtered, expected):
    """A filtered pass-through key announces on exposed satellites only; unfiltered, on all."""
    home.data[DOMAIN] = _data()
    calls: list = []
    home.services.async_register("assist_satellite", "announce", calls.append)

    result, outcome, _ = await native._tool_hass_broadcast({"message": "hello"}, _key(filtered=filtered), home)
    await home.async_block_till_done()

    assert outcome == "allowed", result
    assert len(calls) == 1
    assert sorted(calls[0].data["entity_id"]) == expected


async def test_broadcast_with_no_exposed_satellite_announces_nowhere(home):
    """A filtered pass-through key with no exposed satellite is refused and nothing is announced."""
    async_expose_entity(home, "conversation", SHOWN, False)
    home.data[DOMAIN] = _data()
    calls: list = []
    home.services.async_register("assist_satellite", "announce", calls.append)

    _result, outcome, _ = await native._tool_hass_broadcast({"message": "hello"}, _key(), home)
    await home.async_block_till_done()

    assert outcome == "denied"
    assert calls == []


async def test_live_context_lists_only_what_resolve_grants_a_pass_through_key(hass):
    """GetLiveContext for a pass-through key leaves out what resolve() refuses and Phoenix's own entities."""
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen lamp"})
    hass.states.async_set("light.porch", "on", {"friendly_name": "Porch lamp"})
    own = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "own-counter")
    hass.states.async_set(own.entity_id, "3", {"friendly_name": "Phoenix own counter"})
    real = policy_engine.resolve
    seen: list[str] = []

    def refuse_porch(entity_id, access_key, home):
        seen.append(entity_id)
        return Permission.NO_ACCESS if entity_id == "light.porch" else real(entity_id, access_key, home)

    with patch(f"{NATIVE}.resolve", side_effect=refuse_porch):
        out = native._build_live_context(_key(filtered=False), hass)

    assert "names: Kitchen lamp" in out
    assert "Porch lamp" not in out
    assert "Phoenix own counter" not in out
    assert "light.porch" in seen


@pytest.mark.parametrize("filtered,expected", [(True, [SHOWN]), (False, sorted([SHOWN, HIDDEN]))])
async def test_admin_scope_lists_only_entities_a_pass_through_key_reaches(home, filtered, expected):
    """The admin scope listing of a filtered pass-through key names exposed entities only."""
    own = er.async_get(home).async_get_or_create("sensor", DOMAIN, "own-counter")
    home.states.async_set(own.entity_id, "3")
    access_key = _key(filtered=filtered)
    home.data[DOMAIN] = _data(access_key)
    view = PhoenixAdminScopeView()
    view.hass = home

    resp = await view.get(_admin_request(), access_key_id=access_key.id)

    assert resp.status == 200, resp.text
    body = json.loads(resp.text)
    assert body["readable"] == expected
    assert body["writable"] == expected
