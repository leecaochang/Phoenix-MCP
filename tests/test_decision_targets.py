"""Scoped target previews cannot disclose hidden metadata or execute model choices."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli
from custom_components.phoenix_mcp.const import DOMAIN, DECISION_MAX_TARGETS, DECISION_ROUTE_CHOICES
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider
from custom_components.phoenix_mcp.decision_shadow import async_preview_decision
from custom_components.phoenix_mcp.decision_targets import capture_targets
from custom_components.phoenix_mcp.token_store import PermissionTree, PermissionNode


def choice(selected, keys):
    return {"choice": selected, "probabilities": {key: float(key == selected) for key in keys},
            "confidence": 1.0, "model": "test-model"}


@pytest.fixture
async def target_home(hass, token_store):
    token, _ = await token_store.async_create_token("target_preview", "test")
    token.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    data = PhoenixData(store=token_store, rate_limiter=MagicMock(), audit=MagicMock())
    hass.data[DOMAIN] = data
    store = agentcli.AgentCliStore(AsyncMock())
    account = await store.add("typesafe", {"api_key": "secret", "model": "jev-latest"})
    await token_store.async_patch_settings(decision_provider_id=account)
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", "desk", original_name="Desk lamp")
    area = ar.async_get(hass).async_create("Office")
    registry.async_update_entity(entry.entity_id, aliases=[er.COMPUTED_NAME, "Task light"], area_id=area.id)
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "Desk lamp",
                          "supported_color_modes": ["brightness"], "access_token": "private-state-secret"})
    with patch.object(agentcli, "_get_secret_store", AsyncMock(return_value=store)):
        yield data, token, store, account, entry.entity_id
    data.decision_shadow.cancel()
    if data.decision_shadow.task:
        await asyncio.gather(data.decision_shadow.task, return_exceptions=True)


async def preview(hass, home, **kwargs):
    data, token, *_ = home
    return await async_preview_decision(hass, data, token, "Please light up my desk lamp", "en",
                                        {"reason": "no_match", "proposal": None}, targets=True, **kwargs)


def mocks(category="light_on", selected="t0"):
    return (patch.object(DecisionProvider, "classify", AsyncMock(return_value=choice(category, DECISION_ROUTE_CHOICES))),
            patch.object(DecisionProvider, "select_target", AsyncMock(return_value=choice(selected, ["t0", "none"]))))


async def test_scoped_metadata_only_and_inert_preview(hass, target_home):
    data, token, _, _, entity_id = target_home
    snapshot = capture_targets(hass, token, "light_on")
    assert snapshot.targets == {"t0": {"entity_id": entity_id, "name": "Desk lamp",
                                      "aliases": ["Desk lamp", "Task light"], "area": "Office"}}
    first, second = mocks()
    with (first, second as select, patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp") as dispatch):
        result = await preview(hass, target_home)
    assert result["executed"] is False
    assert result["target_selection"]["entity_id"] == entity_id
    assert select.await_args.args[3] == snapshot.targets
    dispatch.assert_not_called()
    assert data.store.get_pending_approvals() == []
    assert data.decision_shadow.observations == 0
    assert "Desk lamp" not in repr(data.decision_shadow)


@pytest.mark.parametrize("restriction", ["hidden", "disabled", "unexposed", "denied", "unavailable", "group", "aggregate", "unregistered"])
async def test_ineligible_entity_identity_never_sent(hass, target_home, restriction):
    _, token, _, _, entity_id = target_home
    registry = er.async_get(hass)
    if restriction == "hidden": registry.async_update_entity(entity_id, hidden_by=er.RegistryEntryHider.USER)
    if restriction == "disabled": registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    if restriction == "unexposed": async_expose_entity(hass, "conversation", entity_id, False)
    if restriction == "denied": token.permissions = PermissionTree()
    if restriction == "unavailable": hass.states.async_set(entity_id, "unavailable")
    if restriction == "group":
        registry.async_remove(entity_id)
        registry.async_get_or_create("light", "group", "desk", suggested_object_id=entity_id.split(".")[1])
    if restriction == "aggregate": hass.states.async_set(entity_id, "off", {"entity_id": ["light.hidden"]})
    if restriction == "unregistered": registry.async_remove(entity_id)
    first, second = mocks()
    with first, second as select:
        result = await preview(hass, target_home)
    select.assert_not_called()
    assert result["target_selection"]["reason"] == "no_target"
    assert result["target_selection"]["entity_id"] is None


async def test_read_scope_and_brightness_requirements(hass, target_home):
    _, token, _, _, entity_id = target_home
    token.permissions = PermissionTree(domains={"light": PermissionNode(state="YELLOW")})
    assert capture_targets(hass, token, "state_query").targets
    assert not capture_targets(hass, token, "light_on").targets
    token.permissions = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    hass.states.async_set(entity_id, "off", {"supported_color_modes": ["onoff"]})
    assert not capture_targets(hass, token, "light_brightness").targets


async def test_names_and_aliases_scrub_secrets_and_hidden_ids(hass, target_home):
    _, token, _, _, entity_id = target_home
    er.async_get(hass).async_update_entity(entity_id, aliases={"sensor.hidden", "api_key: private-key"})
    snapshot = capture_targets(hass, token, "light_on")
    text = json.dumps(snapshot.targets)
    assert "sensor.hidden" not in text and "private-key" not in text and "private-state-secret" not in text


@pytest.mark.parametrize("case", ["count", "name", "aliases", "bytes"])
async def test_catalog_limits_refuse_instead_of_truncating(hass, target_home, case):
    _, token, _, _, entity_id = target_home
    registry = er.async_get(hass)
    if case == "name": hass.states.async_set(entity_id, "off", {"friendly_name": "a" * 257})
    if case == "aliases": registry.async_update_entity(entity_id, aliases={f"alias{i}" for i in range(9)})
    if case in ("count", "bytes"):
        for index in range(DECISION_MAX_TARGETS if case == "count" else 20):
            entry = registry.async_get_or_create("light", "test", f"extra{index}")
            registry.async_update_entity(entry.entity_id, aliases={str(i) * 200 for i in range(8)} if case == "bytes" else set())
            hass.states.async_set(entry.entity_id, "off")
    with pytest.raises(DecisionError, match="too_many_targets" if case == "count" else "target_metadata_too_large"):
        capture_targets(hass, token, "light_on")


@pytest.mark.parametrize("change", ["name", "alias", "area", "scope", "availability", "new_candidate", "identity", "account", "settings", "revoked"])
async def test_target_wait_revalidates_full_catalog_and_authority(hass, target_home, change):
    data, token, store, account, entity_id = target_home
    entered = asyncio.Event()
    release = asyncio.Event()
    async def select(*args):
        entered.set()
        await release.wait()
        return choice("t0", ["t0", "none"])
    first, _ = mocks()
    with first, patch.object(DecisionProvider, "select_target", select):
        task = asyncio.create_task(preview(hass, target_home))
        await entered.wait()
        registry = er.async_get(hass)
        if change == "name": hass.states.async_set(entity_id, "off", {"friendly_name": "Changed"})
        if change == "alias": registry.async_update_entity(entity_id, aliases={"Changed"})
        if change == "area": ar.async_get(hass).async_update(registry.async_get(entity_id).area_id, name="Changed")
        if change == "scope": token.permissions = PermissionTree()
        if change == "availability": hass.states.async_set(entity_id, "unavailable")
        if change == "new_candidate":
            entry = registry.async_get_or_create("light", "test", "another")
            hass.states.async_set(entry.entity_id, "off")
        if change == "identity":
            registry.async_remove(entity_id)
            registry.async_get_or_create("light", "other", "replacement", suggested_object_id=entity_id.split(".")[1])
        if change == "account": await store.set_model(account, "different-model")
        if change == "settings": await data.store.async_patch_settings(decision_timeout_ms=1234)
        if change == "revoked": token.revoked = True
        release.set()
        result = await task
    if change in ("account", "settings", "revoked"):
        assert result["reason"] == "authority_changed" and result["decision"] is None
        assert "target_selection" not in result
    else:
        assert result["target_selection"]["reason"] == "targets_changed"
        assert result["target_selection"]["entity_id"] is None


@pytest.mark.parametrize("category,selected,expected", [("provider", "t0", "provider_fallback"),
                                                       ("light_on", "none", "target_declined")])
async def test_explicit_decline(hass, target_home, category, selected, expected):
    first, second = mocks(category, selected)
    with first, second as select:
        result = await preview(hass, target_home)
    assert result["target_selection"]["reason"] == expected
    assert result["target_selection"]["entity_id"] is None
    assert select.await_count == (0 if category == "provider" else 1)


async def test_tied_distribution_does_not_select(hass, target_home):
    first, _ = mocks()
    answer = choice("t0", ["t0", "none"])
    answer["probabilities"] = {"t0": 0.5, "none": 0.5}
    with first, patch.object(DecisionProvider, "select_target", AsyncMock(return_value=answer)):
        result = await preview(hass, target_home)
    assert result["target_selection"]["reason"] == "ambiguous_target"
    assert result["target_selection"]["entity_id"] is None


async def test_one_budget_for_both_requests_and_cancellation(hass, target_home):
    data, *_ = target_home
    await data.store.async_patch_settings(decision_timeout_ms=250)
    closed = asyncio.Event()
    async def classify(*args):
        await asyncio.sleep(0.17)
        return choice("light_on", DECISION_ROUTE_CHOICES)
    async def select(*args):
        try:
            await asyncio.sleep(0.17)
            return choice("t0", ["t0", "none"])
        finally:
            closed.set()
    with patch.object(DecisionProvider, "classify", classify), patch.object(DecisionProvider, "select_target", select):
        result = await preview(hass, target_home)
    assert result["reason"] == "timeout" and result["decision"] is None
    assert closed.is_set()
    assert data.decision_shadow.task is None


@pytest.mark.parametrize("protocol", ["systemone", "classifier"])
@pytest.mark.parametrize("invalid", [False, True])
async def test_target_http_contract_and_generated_id_rejection(protocol, invalid, socket_enabled):
    requests = []
    async def handler(request):
        payload = await request.json()
        requests.append(payload)
        answer = choice("light.outside_scope" if invalid else "t0", ["t0", "none"])
        return web.json_response({"model": "test-model", "answers": {"target": {"type": "choice", **answer}}})
    app = web.Application()
    app.router.add_post("/" + protocol, handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig("systemone", "test-model", str(server.make_url("")).rstrip("/"))
        provider = DecisionProvider(cfg, protocol)
        targets = {"t0": {"name": "Desk lamp"}}
        if invalid:
            with pytest.raises(DecisionError, match="malformed"):
                await provider.select_target(session, "lamp on", "light_on", targets, 1000)
        else:
            assert (await provider.select_target(session, "lamp on", "light_on", targets, 1000))["choice"] == "t0"
    assert len(requests) == 1
    assert requests[0]["state"] == {"utterance": "lamp on", "category": "light_on", "targets": targets}
    assert set(requests[0]["questions"]["target"]["criteria"]) == {"t0", "none"}


async def test_plain_observation_never_enumerates_or_sends_targets(hass, target_home):
    data, token, *_ = target_home
    first, second = mocks()
    with first, second as select, patch("custom_components.phoenix_mcp.decision_targets.capture_targets") as capture:
        result = await data.decision_shadow.start(hass, data, token, "lamp on", "en", observe=True)
    # HA can start this task eagerly; its completion callback runs on the next tick.
    await asyncio.sleep(0)
    capture.assert_not_called()
    select.assert_not_called()
    assert "target_selection" not in result
    assert data.decision_shadow.observations == 1


@pytest.mark.parametrize("reason", ["supported", "sentence_trigger", "timeout", "unavailable"])
async def test_local_match_or_unsafe_recognition_skips_both_calls(hass, target_home, reason):
    data, token, *_ = target_home
    first, second = mocks()
    with first as classify, second as select:
        result = await async_preview_decision(hass, data, token, "lamp on", "en", {
            "reason": reason, "proposal": {"intent": "HassTurnOn"} if reason == "supported" else None,
        }, targets=True)
    classify.assert_not_called()
    select.assert_not_called()
    assert "target_selection" not in result


async def test_cancellation_during_target_wait_releases_shared_slot(hass, target_home):
    data, token, *_ = target_home
    entered, closed = asyncio.Event(), asyncio.Event()
    async def select(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    first, _ = mocks()
    with first, patch.object(DecisionProvider, "select_target", select):
        task = asyncio.create_task(preview(hass, target_home))
        await entered.wait()
        assert data.decision_shadow.start(hass, data, token, "other", "en") is None
        data.decision_shadow.cancel()
        result = await task
    assert result["reason"] == "cancelled"
    assert closed.is_set() and data.decision_shadow.task is None


async def test_default_computed_alias_uses_home_assistant_name_resolution(hass, target_home):
    _, token, *_ = target_home
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", "default_alias", original_name="Reading lamp")
    assert entry.aliases == [er.COMPUTED_NAME]
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "State label"})
    snapshot = capture_targets(hass, token, "light_on")
    target = next(item for item in snapshot.targets.values() if item["entity_id"] == entry.entity_id)
    assert target["name"] == "State label"
    assert target["aliases"] == ["Reading lamp"]
    registry.async_update_entity(entry.entity_id, name="Renamed lamp")
    updated = capture_targets(hass, token, "light_on")
    target = next(item for item in updated.targets.values() if item["entity_id"] == entry.entity_id)
    assert target["aliases"] == ["Renamed lamp"]
    assert updated != snapshot
