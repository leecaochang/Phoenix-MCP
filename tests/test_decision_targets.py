"""Scoped target previews cannot disclose hidden metadata or execute model choices."""
import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp import agentcli, device_semantics
from custom_components.phoenix_mcp.command_execution import _target_fingerprint
from custom_components.phoenix_mcp.const import (
    DOMAIN, DECISION_BRIGHTNESS_CHOICES, DECISION_MAX_TARGETS, DECISION_ROUTE_CHOICES,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider
from custom_components.phoenix_mcp.decision_shadow import async_preview_decision
from custom_components.phoenix_mcp.decision_targets import capture_targets, CatalogLimitError
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


async def test_catalog_builds_device_graph_once_without_changing_fingerprints(hass, target_home):
    _, token, *_ = target_home
    for index in range(3):
        entry = er.async_get(hass).async_get_or_create("light", "test", f"extra_{index}")
        hass.states.async_set(entry.entity_id, "off")
    with patch.object(device_semantics, "device_registry_snapshot_from_registry",
                      wraps=device_semantics.device_registry_snapshot_from_registry) as build:
        snapshot = capture_targets(hass, token, "light_on")
    build.assert_called_once()
    assert len(snapshot.targets) == 4
    assert snapshot.fingerprints == {
        item["entity_id"]: _target_fingerprint(hass, token, item["entity_id"], "HassTurnOn")
        for item in snapshot.targets.values()
    }


@pytest.mark.parametrize("restriction", ["hidden", "disabled", "unexposed", "denied", "unavailable", "group", "aggregate", "unregistered"])
@pytest.mark.parametrize("compact", [False, True])
async def test_ineligible_entity_identity_never_sent(hass, target_home, restriction, compact):
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
    assert not capture_targets(hass, token, "light_on", compact=compact).targets
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
@pytest.mark.parametrize("compact", [False, True])
async def test_catalog_limits_refuse_instead_of_truncating(hass, target_home, case, compact):
    _, token, _, _, entity_id = target_home
    registry = er.async_get(hass)
    if case == "name": hass.states.async_set(entity_id, "off", {"friendly_name": "a" * 257})
    if case == "aliases": registry.async_update_entity(entity_id, aliases={f"alias{i}" for i in range(9)})
    if case in ("count", "bytes"):
        for index in range(DECISION_MAX_TARGETS if case == "count" else 20):
            entry = registry.async_get_or_create("light", "test", f"extra{index}")
            registry.async_update_entity(entry.entity_id, aliases={str(i) * 200 for i in range(8)} if case == "bytes" else set())
            hass.states.async_set(entry.entity_id, "off")
    with pytest.raises(CatalogLimitError, match="too_many_targets" if case == "count" else "target_metadata_too_large") as caught:
        capture_targets(hass, token, "light_on", compact=compact)
    assert caught.value.details["category"] == "light_on"
    assert caught.value.details["bound"] == {"count": "target_count", "name": "text_length",
                                            "aliases": "alias_count", "bytes": "metadata_bytes"}[case]
    assert caught.value.details["observed"] > caught.value.details["limit"]
    assert entity_id not in str(caught.value.details)


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
    async def classify(*args, **kwargs):
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


@pytest.mark.parametrize("protocol", ['systemone'])
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
    async def models(_request):
        return web.json_response({"data": [{"id": "test-model"}]})
    app.router.add_get("/models", models)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig("typesafe", "test-model", str(server.make_url("")).rstrip("/"))
        provider = DecisionProvider(cfg, protocol)
        targets = {"t0": {"name": "Desk lamp"}}
        if invalid:
            with pytest.raises(DecisionError, match="malformed"):
                await provider.select_target(session, "lamp on", "light_on", targets, 1000)
        else:
            assert (await provider.select_target(session, "lamp on", "light_on", targets, 1000))["choice"] == "t0"
    assert len(requests) == 1
    expected_state = {"utterance": "lamp on", "language": "en", "category": "light_on"}
    expected_state["targets"] = targets
    assert requests[0]["state"] == expected_state
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


@pytest.mark.parametrize("reason", ["supported", "sentence_trigger", "custom_sentence", "timeout", "unavailable"])
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


def test_brightness_option_lists_partition_all_whole_percentages():
    """Every written percentage must identify exactly one lossless pair of choices."""
    partitions = {}
    for question, criteria in DECISION_BRIGHTNESS_CHOICES.items():
        assert "none" in criteria and len(criteria) <= 50
        buckets = {key: [int(value) for value in re.findall(r"(\d+)%", description)]
                   for key, description in criteria.items() if key != "none"}
        values = [value for bucket in buckets.values() for value in bucket]
        assert sorted(values) == list(range(101))
        partitions[question] = buckets
    for percent in range(101):
        selected = {question: next(key for key, values in buckets.items() if percent in values)
                    for question, buckets in partitions.items()}
        assert 10 * int(selected["brightness_tens"]) + int(selected["brightness_units"]) == percent


@pytest.mark.parametrize("protocol", ['systemone'])
@pytest.mark.parametrize("percent", [0, 5, 37, 50, 55, 99, 100])
async def test_brightness_multi_question_http_contract(protocol, percent, socket_enabled):
    requests = []
    async def handler(request):
        payload = await request.json()
        requests.append(payload)
        selected = {"target": "t0", "brightness_tens": str(percent // 10), "brightness_units": str(percent % 10)}
        return web.json_response({"model": "test-model", "answers": {
            question: {"type": "choice", **choice(selected[question], spec["criteria"])}
            for question, spec in payload["questions"].items()}})
    app = web.Application()
    app.router.add_post("/" + protocol, handler)
    async def models(_request):
        return web.json_response({"data": [{"id": "test-model"}]})
    app.router.add_get("/models", models)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig("typesafe", "test-model", str(server.make_url("")).rstrip("/"))
        answer = await DecisionProvider(cfg, protocol).select_target(
            session, f"set the desk lamp to {percent} percent", "light_brightness", {"t0": {"name": "Desk lamp"}}, 1000)
    assert answer["brightness_pct"] == percent
    assert len(requests) == 1
    assert set(requests[0]["questions"]) == {"target", "brightness_tens", "brightness_units"}
    assert all(len(question["criteria"]) <= 50 for question in requests[0]["questions"].values())
    for question in ("brightness_tens", "brightness_units"):
        spec = requests[0]["questions"][question]
        assert spec["criteria"] == DECISION_BRIGHTNESS_CHOICES[question]
        assert "list contains that exact whole percentage" in spec["instructions"]


@pytest.mark.parametrize("change", ["missing", "extra", "fractional", "out_of_range", "none", "low_probability", "bool"])
async def test_brightness_answers_are_closed_and_bounded(change):
    cfg = agentcli.ProviderConfig("typesafe", "test-model", "http://example.invalid")
    async def request(session, method, path, timeout, payload=None):
        if method == "GET":
            return {"data": [{"id": "test-model"}]}
        selected = {"target": "t0", "brightness_tens": "5", "brightness_units": "0"}
        if change == "fractional": selected["brightness_units"] = "0.5"
        if change == "out_of_range": selected.update(brightness_tens="10", brightness_units="1")
        if change == "none": selected["brightness_units"] = "none"
        if change == "bool": selected["brightness_units"] = True
        answers = {question: {"type": "choice", **choice(selected[question], spec["criteria"])}
                   for question, spec in payload["questions"].items()}
        if change == "missing": answers.pop("brightness_units")
        if change == "extra": answers["instructions"] = answers["target"]
        if change == "low_probability": answers["brightness_units"]["probabilities"].update({"0": 0.8, "none": 0.2})
        return {"model": "test-model", "answers": answers}
    with patch.object(DecisionProvider, "_request", AsyncMock(side_effect=request)):
        provider = DecisionProvider(cfg, "systemone")
        if change in ("none", "low_probability"):
            answer = await provider.select_target(None, "brightness", "light_brightness", {"t0": {}}, 1000)
            assert answer["brightness_pct"] is None
        else:
            with pytest.raises(DecisionError, match="malformed"):
                await provider.select_target(None, "brightness", "light_brightness", {"t0": {}}, 1000)


async def test_manual_brightness_preview_exposes_proposal_but_never_execution_plan(hass, target_home):
    first, _ = mocks("light_brightness")
    answer = {**choice("t0", ["t0", "none"]), "brightness_pct": 0}
    with (first, patch.object(DecisionProvider, "select_target", AsyncMock(return_value=answer)),
          patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp") as dispatch):
        result = await preview(hass, target_home)
    assert result["target_selection"]["proposal"] == {
        "intent": "HassLightSet", "entity_ids": [target_home[-1]], "brightness_pct": 0}
    assert "_plan" not in json.dumps(result)
    dispatch.assert_not_called()
    assert not target_home[0].store.get_pending_approvals()


@pytest.mark.parametrize('sentence,model_value,evidence', [
    ('Set the desk lamp to thirty-seven percent', 37, {'status': 'match', 'source_value': 37}),
    ('Set the desk lamp to thirty-seven percent', 5, {'status': 'mismatch', 'source_value': 37}),
    ('Set the desk lamp to 37 percent instead of 73 percent', 37, {'status': 'unverified'}),
    ('Do not set the desk lamp to 37 percent', 37, {'status': 'match', 'source_value': 37}),
])
async def test_sequential_value_evidence_is_diagnostic_only(hass, target_home, sentence, model_value, evidence):
    data, token, *_ = target_home
    first, _ = mocks('light_brightness')
    answer = {**choice('t0', ['t0', 'none']), 'brightness_pct': model_value}
    with (first, patch.object(DecisionProvider, 'select_target', AsyncMock(return_value=answer)),
          patch('custom_components.phoenix_mcp.command_execution._capture_plan') as prepare):
        result = await async_preview_decision(hass, data, token, sentence, 'en',
                                              {'reason': 'no_match', 'proposal': None}, targets=True)
    assert result['target_selection']['value_check'] == evidence
    assert result['executed'] is False and '_plan' not in result
    prepare.assert_not_called()


async def test_target_selector_cannot_prepare_numeric_plan_even_with_matching_value(hass, target_home):
    from custom_components.phoenix_mcp.command_routing import RoutingAuthority
    data, token, store, account, _ = target_home
    provider = DecisionProvider(store.resolve(account, purpose='decision'), 'systemone')
    with (patch.object(provider, 'select_target') as select,
          patch('custom_components.phoenix_mcp.command_execution._capture_plan') as prepare):
        result = await data.decision_shadow._select_target(
            hass, RoutingAuthority.capture(data, token), provider, None, 'Set the lamp to 37 percent',
            choice('light_brightness', DECISION_ROUTE_CHOICES), 1000, prepare=True,
        )
    assert result['reason'] == 'numeric_unverified'
    select.assert_not_called()
    prepare.assert_not_called()


@pytest.mark.parametrize("language,wire_language,sentence", [
    ("zh-Hans", "zh-CN", "把台灯亮度设为百分之五十"),
    ("es", "es", "pon la lámpara al cincuenta por ciento"),
])
async def test_multilingual_target_and_brightness_preview_keeps_language_and_no_plan(hass, target_home, language, wire_language, sentence):
    data, token, _, _, entity_id = target_home
    er.async_get(hass).async_update_entity(entity_id, aliases={'台灯', 'lámpara'})
    payloads = []
    async def request(session, method, path, timeout, payload):
        payloads.append(payload)
        selected = {"route": "light_brightness", "target": "t0", "percentage": "50"}
        return {"model": "test-model", "answers": {
            question: {"type": "choice", **choice(selected[question], spec["criteria"])}
            for question, spec in payload["questions"].items()}}
    with (patch.object(DecisionProvider, "_request", AsyncMock(side_effect=request)),
          patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp") as dispatch):
        result = await async_preview_decision(hass, data, token, sentence, language,
                                              {"reason": "no_match", "proposal": None}, targets=True, ingress="preview")
    assert result["target_selection"]["proposal"] == {
        "intent": "HassLightSet", "entity_ids": [entity_id], "brightness_pct": 50}
    assert len(payloads) == 2
    assert all(payload["state"]["language"] == wire_language for payload in payloads)
    assert all(payload["state"]["utterance"] == sentence for payload in payloads)
    assert "English" not in json.dumps(payloads)
    assert "_plan" not in json.dumps(result)
    dispatch.assert_not_called()
    assert not data.store.get_pending_approvals()


@pytest.mark.parametrize('category,intent,field,domain', [
    ('light_brightness', 'HassLightSet', 'brightness_pct', 'light'),
    ('media_volume', 'HassSetVolume', 'volume_pct', 'media_player'),
    ('cover_position', 'HassSetPosition', 'position_pct', 'cover'),
])
@pytest.mark.parametrize('percent', [0, 1, 10, 37, 55, 99, 100])
async def test_jev_exact_percentage_preview_is_scoped_and_inert(hass, target_home, category, intent, field, domain, percent):
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.components.media_player import MediaPlayerEntityFeature
    data, token, *_ = target_home
    token.permissions = PermissionTree(domains={domain: PermissionNode(state='GREEN')})
    if domain == 'light':
        entity_id = target_home[-1]
    else:
        entry = er.async_get(hass).async_get_or_create(domain, 'test', 'numeric', original_name='Numeric device')
        entity_id = entry.entity_id
        feature = MediaPlayerEntityFeature.VOLUME_SET if domain == 'media_player' else CoverEntityFeature.SET_POSITION
        hass.states.async_set(entity_id, 'idle' if domain == 'media_player' else 'open', {'supported_features': feature})
        async_expose_entity(hass, 'conversation', entity_id, True)
    async def response(session, method, path, timeout, payload):
        selected = {'route': category, 'target': 't0', 'percentage': str(percent)}
        if 'target' in payload['questions']:
            assert set(payload['questions']) == {'target', 'percentage'}
            assert len(payload['questions']['percentage']['criteria']) == 102
            assert payload['state']['targets']['t0']['entity_id'] == entity_id
        return {'model': 'jev-1.13.0', 'answers': {
            question: {'type': 'choice', **choice(selected[question], spec['criteria'])}
            for question, spec in payload['questions'].items()}}
    with (patch.object(DecisionProvider, '_request', AsyncMock(side_effect=response)) as request,
          patch('custom_components.phoenix_mcp.mcp_view._dispatch_mcp') as dispatch):
        result = await async_preview_decision(hass, data, token, f'Set {entity_id} to {percent} percent', 'en',
                                              {'reason': 'no_match', 'proposal': None}, targets=True, ingress='preview')
    assert result['target_selection']['proposal'] == {'intent': intent, 'entity_ids': [entity_id], field: percent}
    assert result['target_selection']['value_check'] == {'status': 'match', 'source_value': percent}
    assert result['executed'] is False and '_plan' not in json.dumps(result)
    assert request.await_count == 2 and not data.store.get_pending_approvals()
    dispatch.assert_not_called()


@pytest.mark.parametrize('damage', ['missing', 'extra', 'fractional', 'outside', 'bool', 'low', 'none'])
async def test_jev_exact_percentage_rejects_unsound_values(damage):
    from custom_components.phoenix_mcp.const import DECISION_TYPESAFE_BASE_URL
    provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'jev-latest', DECISION_TYPESAFE_BASE_URL), 'systemone')
    async def response(session, method, path, timeout, payload):
        selected = {'target': 't0', 'percentage': '37'}
        selected['percentage'] = {'fractional': '37.5', 'outside': '101', 'bool': True, 'none': 'none'}.get(damage, '37')
        answers = {question: {'type': 'choice', **choice(selected[question], spec['criteria'])}
                   for question, spec in payload['questions'].items()}
        if damage == 'missing': del answers['percentage']
        if damage == 'extra': answers['unrequested'] = answers['target']
        if damage == 'low': answers['percentage']['probabilities'].update({'37': 0.8, 'none': 0.2})
        return {'model': 'jev-1.13.0', 'answers': answers}
    with patch.object(provider, '_request', side_effect=response):
        if damage in ('low', 'none'):
            answer = await provider.select_target(None, 'request', 'media_volume', {'t0': {}}, 1000)
            assert answer['volume_pct'] is None
        else:
            with pytest.raises(DecisionError, match='malformed'):
                await provider.select_target(None, 'request', 'media_volume', {'t0': {}}, 1000)


@pytest.mark.parametrize('category,domain', [('media_volume', 'media_player'), ('cover_position', 'cover')])
async def test_numeric_preview_requires_actual_device_support(hass, target_home, category, domain):
    _, token, *_ = target_home
    token.permissions = PermissionTree(domains={domain: PermissionNode(state='GREEN')})
    entry = er.async_get(hass).async_get_or_create(domain, 'test', 'unsupported')
    hass.states.async_set(entry.entity_id, 'on', {'supported_features': 0})
    async_expose_entity(hass, 'conversation', entry.entity_id, True)
    with (patch.object(DecisionProvider, 'classify', AsyncMock(return_value=choice(category, [category]))),
          patch.object(DecisionProvider, 'select_target') as select):
        result = await preview(hass, target_home)
    assert result['target_selection']['reason'] == 'no_target'
    select.assert_not_called()


@pytest.mark.parametrize('area_present', [False, True])
async def test_compact_catalog_keeps_distinct_identity_text_and_full_fingerprints(hass, target_home, area_present):
    _, token, _, _, entity_id = target_home
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, aliases={er.COMPUTED_NAME, entity_id, 'Desk Lamp', '书桌灯', 'Task light'})
    if not area_present:
        registry.async_update_entity(entity_id, area_id=None)
    raw = capture_targets(hass, token, 'light_brightness')
    compact = capture_targets(hass, token, 'light_brightness', compact=True)
    assert raw.fingerprints == compact.fingerprints
    assert set(raw.targets) == set(compact.targets) == {'t0'}
    assert compact.targets['t0'] == {
        'entity_id': entity_id, 'name': 'Desk lamp', 'aliases': ['Desk Lamp', 'Task light', '书桌灯'],
        **({'area': 'Office'} if area_present else {}),
    }
    # Even a removed duplicate is retained in the authority fingerprint.
    registry.async_update_entity(entity_id, aliases={'Desk Lamp', '书桌灯', 'Task light'})
    changed = capture_targets(hass, token, 'light_brightness', compact=True)
    assert changed.targets == compact.targets
    assert changed.digest() != compact.digest()


def unpack_catalog(state):
    """Independently decode the public wire layout to compare every original value."""
    from copy import deepcopy
    targets = deepcopy(state['targets'])
    if 'target_fields' in state:
        targets = {key: {field: value for field, value in zip(state['target_fields'], row, strict=True) if value is not None}
                   for key, row in targets.items()}
    for target in targets.values():
        for field in ('entity_command', 'numeric'):
            value = target.get(field)
            if isinstance(value, dict) and set(value) == {'ref'}:
                target[field] = deepcopy(state['target_metadata'][value['ref']])
        for group in ('entity_operations', 'numeric_operations'):
            for category, value in target.get(group, {}).items():
                if set(value) == {'ref'}:
                    target[group][category] = deepcopy(state['target_metadata'][value['ref']])
    return targets


def test_large_catalog_packing_preserves_all_entities_aliases_options_and_native_bounds():
    from copy import deepcopy
    from custom_components.phoenix_mcp.decision_targets import catalog_state
    targets = {f't{i}': {'entity_id': f'light.room_{i}', 'name': f'Room light {i}',
        'aliases': [f'Lampe {i}', f'ランプ {i}'], 'area': 'Shared room',
        'eligible_categories': ['light_brightness', 'light_color', 'attribute_read'],
        'entity_operations': {'attribute_read': {'kind': 'attribute', 'options': ['brightness', 'rgb_color', 'effect']},
                              'light_color': {'kind': 'color'}},
        'numeric_operations': {'temperature': {'minimum': 2000, 'maximum': 6500, 'step': None, 'unit': 'K'}}}
        for i in range(100)}
    original = deepcopy(targets)
    packed = catalog_state(targets)
    assert unpack_catalog(packed) == original == targets
    assert len(json.dumps(packed).encode()) < len(json.dumps({'targets': targets}).encode()) * 0.75
    assert len(packed['targets']) == 100
    assert catalog_state(targets) == packed


def test_catalog_packing_retains_supplied_null_and_does_not_mutate_source():
    from custom_components.phoenix_mcp.decision_targets import catalog_state
    targets = {f't{i}': {'entity_id': f'sensor.test_{i}', 'name': 'x' * 100, 'optional': None} for i in range(100)}
    packed = catalog_state(targets)
    assert 'target_fields' not in packed
    assert unpack_catalog(packed) == targets


@pytest.mark.parametrize("protocol", ['systemone'])
@pytest.mark.parametrize("case", ["selected", "declined", "conflict", "uncertain", "missing", "foreign"])
async def test_generic_grouped_targets_http_contract(protocol, case, socket_enabled):
    """All groups see competitors; a confident final-group winner is necessary."""
    targets = {f"t{i}": {"entity_id": f"light.item_{i}", "name": f"Lamp {i}",
                          "aliases": ["shared alias"] if i in (0, 98) else []} for i in range(99)}
    requests = []
    async def handler(request):
        payload = await request.json()
        requests.append(payload)
        answers = {}
        for name, spec in payload["questions"].items():
            selected = "t98" if "t98" in spec["criteria"] and case != "declined" else "none"
            if case == "conflict" and name == "target":
                selected = "t0"
            answer = {"type": "choice", **choice(selected, spec["criteria"])}
            if case == "uncertain" and name == "target":
                answer.update(confidence=0.8, probabilities={key: 0.8 if key == "none" else 0.2 if key == "t0" else 0.0
                                                           for key in spec["criteria"]})
            if case == "foreign" and name == "target":
                answer = {"type": "choice", **choice("t98", [*spec["criteria"], "t98"])}
            answers[name] = answer
        if case == "missing":
            del answers["target_2"]
        return web.json_response({"model": "test-model", "answers": answers})
    app = web.Application()
    app.router.add_post("/" + protocol, handler)
    async def models(_request):
        return web.json_response({"data": [{"id": "test-model"}]})
    app.router.add_get("/models", models)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = agentcli.ProviderConfig("typesafe", "test-model", str(server.make_url("")).rstrip("/"))
        provider = DecisionProvider(cfg, protocol)
        call = provider.select_target(session, "switch Lamp 98 off", "light_off", targets, 1000)
        if case in ("selected", "declined"):
            answer = await call
            assert answer["choice"] == ("t98" if case == "selected" else "none")
        else:
            with pytest.raises(DecisionError, match={"conflict": "category_mismatch", "uncertain": "low_probability",
                                                   "missing": "malformed", "foreign": "malformed"}[case]):
                await call
    assert len(requests) == 1
    payload = requests[0]
    assert len(payload["state"]["targets"]) == 99
    assert "shared alias" in json.dumps(payload["state"])
    assert set(payload["questions"]) == {"target", "target_2", "target_3"}
    assert all(len(q["criteria"]) <= 50 and "COMPLETE shared catalog" in q["instructions"]
               for q in payload["questions"].values())
    assert {key for q in payload["questions"].values() for key in q["criteria"]} == {*targets, "none"}


async def test_generic_grouped_catalog_bound_and_stale_competitor(hass, target_home):
    from custom_components.phoenix_mcp.const import DECISION_SEQUENTIAL_MAX_TARGETS
    from custom_components.phoenix_mcp.decision_targets import capture_sequential_targets
    data, token, _, _, _ = target_home
    registry = er.async_get(hass)
    for i in range(DECISION_SEQUENTIAL_MAX_TARGETS - 1):
        entry = registry.async_get_or_create("light", "test", f"extra_{i}")
        hass.states.async_set(entry.entity_id, "off")
    snapshot = capture_sequential_targets(hass, token, "light_on")
    assert len(snapshot.targets) == DECISION_SEQUENTIAL_MAX_TARGETS
    async def select(_self, _session, _sentence, _category, targets, *_args):
        assert len(targets) == DECISION_SEQUENTIAL_MAX_TARGETS
        # A competing alias changes while the model is evaluating another group.
        registry.async_update_entity(entry.entity_id, aliases={"Desk lamp"})
        return choice("t0", ["t0", "none"])
    first, _ = mocks()
    with first, patch.object(DecisionProvider, "select_target", select):
        result = await preview(hass, target_home)
    assert result["target_selection"]["reason"] == "targets_changed"
    extra = registry.async_get_or_create("light", "test", "overflow")
    hass.states.async_set(extra.entity_id, "off")
    with pytest.raises(CatalogLimitError, match="too_many_targets"):
        capture_sequential_targets(hass, token, "light_on")
    assert not data.store.get_pending_approvals()


@pytest.mark.parametrize("advertised,expected", [(255, 255), (50, 50), (64, 64), (True, None), (256, None), (1, None), ("255", None)])
async def test_generic_choice_capacity_is_model_scoped_and_bounded(advertised, expected, socket_enabled):
    async def models(_request):
        return web.json_response({"data": [{"id": "other", "x_max_choice_options": 255},
                                           {"id": "test-model", "x_max_choice_options": advertised}]})
    app = web.Application()
    app.router.add_get('/models', models)
    async with TestServer(app) as server, ClientSession() as session:
        provider = DecisionProvider(agentcli.ProviderConfig("typesafe", "test-model", str(server.make_url('')).rstrip('/')), 'systemone')
        if expected is None:
            with pytest.raises(DecisionError, match='malformed'):
                await provider.target_choice_limit(session, 1000)
        else:
            assert await provider.target_choice_limit(session, 1000) == expected
        assert not provider.supports_exact_percentages()


async def test_advertised_extended_choices_avoid_unnecessary_groups(socket_enabled):
    requests = []
    targets = {f't{i}': {'entity_id': f'light.fixture_{i}', 'name': f'Lamp {i}',
                        'aliases': [f'ランプ {i}', f'Lampe {i}'], 'area': 'Test room',
                        'numeric_operations': {'brightness': {'minimum': 0, 'maximum': 100}}}
               for i in range(95)}
    async def models(_request):
        return web.json_response({"data": [{"id": "test-model", "x_max_choice_options": 255}]})
    async def classify(request):
        payload = await request.json()
        requests.append(payload)
        return web.json_response({"model": "test-model", "answers": {
            "target": {"type": "choice", **choice("t94", payload['questions']['target']['criteria'])}}})
    app = web.Application()
    app.router.add_get('/models', models)
    app.router.add_post('/systemone', classify)
    async with TestServer(app) as server, ClientSession() as session:
        provider = DecisionProvider(agentcli.ProviderConfig('typesafe', 'test-model', str(server.make_url('')).rstrip('/')), 'systemone')
        result = await provider.select_target(session, 'turn off Lamp 94', 'light_off',
                                             targets, 1000)
    assert result['choice'] == 't94'
    assert set(requests[0]['questions']) == {'target'}
    assert len(requests[0]['questions']['target']['criteria']) == 96
    assert unpack_catalog(requests[0]['state']) == targets
    assert 'state.targets' in requests[0]['questions']['target']['instructions']


@pytest.mark.parametrize('protocol', ['systemone'])
@pytest.mark.parametrize('limit,count', [(2, 2), (10, 9), (10, 10), (10, 12), (50, 12), (64, 12), (None, 12)])
async def test_small_target_catalog_obeys_model_capacity(protocol, limit, count, socket_enabled):
    targets = {f't{i}': {'entity_id': f'light.fixture_{i}', 'name': f'Lamp {i}'} for i in range(count)}
    requests = []
    capacity = limit or 50

    async def models(request):
        requests.append(request.method)
        selected = {'id': 'test-model'}
        if limit is not None:
            selected['x_max_choice_options'] = limit
        return web.json_response({'data': [{'id': 'other', 'x_max_choice_options': 255}, selected]})

    async def classify(request):
        requests.append(request.method)
        payload = await request.json()
        questions = payload['questions']
        assert all(len(q['criteria']) <= capacity and 'none' in q['criteria'] for q in questions.values())
        assert {key for q in questions.values() for key in q['criteria']} == {*targets, 'none'}
        assert unpack_catalog(payload['state']) == targets
        selected = f't{count - 1}'
        return web.json_response({'model': 'test-model', 'answers': {
            name: {'type': 'choice', **choice(selected if selected in spec['criteria'] else 'none', spec['criteria'])}
            for name, spec in questions.items()}})

    app = web.Application()
    app.router.add_get('/models', models)
    app.router.add_post('/' + protocol, classify)
    async with TestServer(app) as server, ClientSession() as session:
        provider = DecisionProvider(agentcli.ProviderConfig("typesafe", 'test-model', str(server.make_url('')).rstrip('/')), protocol)
        result = await provider.select_target(session, f'turn off Lamp {count - 1}', 'light_off', targets, 1000)
    assert result['choice'] == f't{count - 1}'
    assert requests == ['GET', 'POST']


@pytest.mark.parametrize('field,metadata', [
    ('entity_command', {'kind': 'attribute', 'options': ['brightness', 'rgb_color', 'color_temp_kelvin', 'effect']}),
    ('entity_command', {'kind': 'option', 'options': ['Mode 1', 'Mode 2', 'Mode 3', '模式 4']}),
    ('numeric', {'minimum': -10, 'maximum': 100, 'step': 0.5, 'unit': '°C'}),
])
def test_operation_metadata_pooling_is_lossless_and_keeps_distinct_values(field, metadata):
    from copy import deepcopy
    from custom_components.phoenix_mcp.decision_targets import catalog_state
    targets = {f't{i}': {'entity_id': f'sensor.item_{i}', 'name': f'Item {i}', field: deepcopy(metadata)}
               for i in range(100)}
    targets['t99'][field] = {**metadata, 'unique': 'Preserve this competitor'}
    original = deepcopy(targets)
    packed = catalog_state(targets)
    assert 'target_metadata' in packed
    assert unpack_catalog(packed) == original == targets
    assert len(json.dumps(packed).encode()) < len(json.dumps({'targets': targets}).encode()) * 0.6
    assert catalog_state(targets) == packed
