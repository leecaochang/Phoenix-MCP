"""The Decisions entity answers ai_task.evaluate only through its bound access key.

The provider is faked at the aiohttp boundary through `decision_wire`, so the guards,
request allowance and parsing run for real. A refusal is asserted on what reached the
wire, and on the allowance, never only on the raised message.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
import pytest

from custom_components.phoenix_mcp import ai_task
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, GlobalSettings, PermissionTree
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.llm import accounts as llm_accounts
from custom_components.phoenix_mcp.routing.decision_evaluation import (
    async_evaluate, decisions_configured, parse_evaluation,
)
from custom_components.phoenix_mcp.routing.decision_types import DecisionError
from custom_components.phoenix_mcp.routing.routing_tables import (
    DECISION_EVALUATE_MAX_BYTES, DECISION_EVALUATE_MAX_CHOICES, DECISION_EVALUATE_MAX_SCORE_LEVELS,
)
from custom_components.phoenix_mcp.tool_dispatch import _call_tool

NOUL = {"type": "noul", "instructions": "Has the package arrived?"}
CHOICE = {"type": "choice", "instructions": "Which door?", "criteria": {"front": "The front door.", "back": "The back door."}}
SCORE = {"type": "score", "instructions": "How urgent?", "criteria": ["Can wait", "Soon", "Now"]}
QUESTIONS = {"arrived": NOUL, "door": CHOICE, "urgency": SCORE}
requires_evaluate = pytest.mark.skipif(not ai_task.ai_task_decisions_supported(), reason="Added in HA 2026.11")


class Home:
    def __init__(self, hass, data, access_key, store, account, wire) -> None:
        self.hass, self.data, self.access_key = hass, data, access_key
        self.store, self.account, self.wire = store, account, wire

    async def ask(self, questions=None, state="Left at the front door.", **kwargs):
        return await async_evaluate(self.hass, self.data, "Check delivery", questions or {"arrived": NOUL}, state, **kwargs)

    async def refused(self, *args, **kwargs) -> str:
        """Assert one refusal spent nothing and reached nothing, and return its message."""
        with pytest.raises(HomeAssistantError) as raised:
            await self.ask(*args, **kwargs)
        assert self.wire.posts == []
        assert self.wire._reservations == 0
        return str(raised.value)

    async def set_cap(self, mode: str) -> None:
        self.access_key.cap_service_response = mode


@pytest.fixture
async def home(hass, access_key_store, decision_wire):
    access_key, _ = await access_key_store.async_create_access_key("decisions", "test")
    access_key.cap_service_response = "allow"
    data = PhoenixData(store=access_key_store, rate_limiter=MagicMock(), audit=MagicMock())
    data.ready = True
    data.decision_budget = decision_wire.budget_gate()
    hass.data[DOMAIN] = data
    store = llm_accounts.AgentCliStore(AsyncMock())
    hass.data["_phoenix_agentcli_store"] = store
    account = await store.add("typesafe", {"api_key": "wire-secret", "model": "jev-latest"})
    await access_key_store.async_patch_settings(
        decision_provider_id=account, ai_task_decisions_enabled=True, ai_task_decisions_access_key_id=access_key.id)
    yield Home(hass, data, access_key, store, account, decision_wire)


# --------------------------------------------------------------------------- #
# Answers
# --------------------------------------------------------------------------- #

async def test_every_question_type_returns_home_assistants_fields(home):
    answers = await home.ask(QUESTIONS)
    assert answers == {
        "arrived": {"type": "noul", "noul": 0.75},
        "door": {"type": "choice", "choice": "front", "probabilities": {"front": 1.0, "back": 0.0}},
        "urgency": {"type": "score", "score": 0, "probabilities": [1.0, 0.0, 0.0]},
    }
    sent = home.wire.posts[0]
    assert sent["model"] == "jev-latest" and sent["questions"] == QUESTIONS


@requires_evaluate
async def test_an_entity_answers_through_home_assistants_own_action(hass, home, phoenix_ai_task_platform):
    from homeassistant.components import ai_task as core_ai_task

    platform, entry = phoenix_ai_task_platform
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "ai_task", {})
    await ai_task.async_setup_entry(hass, entry, platform._async_schedule_add_entities_for_entry)
    await hass.async_block_till_done()
    entity = home.data.ai_evaluate_entity
    assert entity.unique_id == f"{entry.entry_id}_ai_evaluate"
    assert entity.supported_features == core_ai_task.AITaskEntityFeature.EVALUATE
    assert entity.available is True

    result = await core_ai_task.async_evaluate(
        hass, task_name="Check delivery", questions=QUESTIONS, state="Left at the front door.",
        entity_id=entity.entity_id)
    assert result.as_dict()["answers"]["arrived"] == {"noul": 0.75, "type": "noul"}
    assert result.answers["urgency"].probabilities == [1.0, 0.0, 0.0]
    assert result.answers["door"].choice == "front"


@pytest.mark.parametrize(("state", "sent"), [
    ("text", "text"), ({"a": 1}, {"a": 1}), ([1, 2], [1, 2]), (4, "4"), (2.5, "2.5"), (True, "true"), (False, "false"),
])
async def test_state_reaches_the_provider_as_a_shape_it_reads(home, state, sent):
    await home.ask(state=state)
    assert home.wire.posts[0]["state"] == sent


async def test_probability_keys_out_of_order_are_read_by_level(home):
    home.wire.reply = {"model": "jev-1.14.0", "answers": {"urgency": {
        "type": "score", "score": 1.5, "confidence": 0.9, "legend": {"2": "Now", "0": "Can wait", "1": "Soon"},
        "probabilities": {"2": 0.5, "0": 0, "1": 0.5}}}}
    answers = await home.ask({"urgency": SCORE})
    assert answers["urgency"]["probabilities"] == [0, 0.5, 0.5]


async def test_answers_rounded_by_the_endpoint_are_accepted(home):
    home.wire.reply = {"model": "jev-1.14.0", "answers": {"urgency": {
        "type": "score", "score": 1.0, "confidence": 0.9, "legend": {"0": "a", "1": "b", "2": "c"},
        "probabilities": {"0": 0.33, "1": 0.33, "2": 0.33}}}}
    assert (await home.ask({"urgency": SCORE}))["urgency"]["score"] == 1.0


@pytest.mark.parametrize("name", ["tool_x", "target", "route", "summary"])
async def test_a_question_name_is_not_read_as_routing(home, name):
    await home.ask({name: NOUL})
    summary = home.data.decision_shadow.metrics.summary()
    assert set(summary["typesafe"]) == {"ai_task_evaluate"}
    assert set(summary["typesafe"]["ai_task_evaluate"]) == {"evaluate"}


async def test_a_low_confidence_choice_is_still_returned(home):
    home.wire.reply = {"model": "jev-1.14.0", "answers": {"door": {
        "type": "choice", "choice": "front", "confidence": 0.3, "probabilities": {"front": 0.55, "back": 0.45}}}}
    assert (await home.ask({"door": CHOICE}))["door"]["probabilities"] == {"front": 0.55, "back": 0.45}


async def test_the_largest_accepted_questions_are_sent(home):
    choice = {"type": "choice", "instructions": "Pick.",
              "criteria": {f"c{i}": "x" for i in range(DECISION_EVALUATE_MAX_CHOICES)}}
    score = {"type": "score", "instructions": "Rate.", "criteria": ["x"] * DECISION_EVALUATE_MAX_SCORE_LEVELS}
    await home.ask({"pick": choice, "rate": score})
    assert len(home.wire.posts) == 1


# --------------------------------------------------------------------------- #
# Refused before anything is spent
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("questions", [
    {"pick": {"type": "choice", "instructions": "Pick.",
              "criteria": {f"c{i}": "x" for i in range(DECISION_EVALUATE_MAX_CHOICES + 1)}}},
    {"rate": {"type": "score", "instructions": "Rate.", "criteria": ["x"] * (DECISION_EVALUATE_MAX_SCORE_LEVELS + 1)}},
])
async def test_too_many_criteria_are_refused(home, questions):
    assert "too large" in await home.refused(questions)


async def test_an_oversized_state_is_refused(home):
    assert "too large" in await home.refused(state="x" * DECISION_EVALUATE_MAX_BYTES)


async def test_a_reply_too_large_to_read_is_refused_before_asking(home):
    # Score repeats its criteria in a legend, so a request within limits can still overflow.
    score = {"type": "score", "instructions": "Rate.", "criteria": ["x" * 6000] * DECISION_EVALUATE_MAX_SCORE_LEVELS}
    questions = {f"q{i}": score for i in range(2)}
    assert "too large" in await home.refused(questions, state="s")


@pytest.mark.parametrize("state", [float("nan"), float("inf"), {"at": utcnow()}, "\ud800", {"a": {"b": float("nan")}}])
async def test_a_value_the_provider_cannot_read_is_refused(home, state):
    assert "cannot read" in await home.refused(state=state)


async def test_no_state_is_refused(home):
    assert "cannot read" in await home.refused(state=None)


@pytest.mark.parametrize("state", [
    "the key is wire-secret", {"password": "hunter2"}, "api_key: abc123", "http://user:pass@host/path",
    '{"token": "abc"}',
])
async def test_secret_like_state_is_refused(home, state):
    assert "secret-like" in await home.refused(state=state)


async def test_secret_like_question_text_is_refused(home):
    question = {"type": "choice", "instructions": "Pick.", "criteria": {"a": "wire-secret", "b": "other"}}
    assert "secret-like" in await home.refused({"pick": question})


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #

async def test_the_capability_must_be_allow(home):
    await home.set_cap("deny")
    assert "service response capability" in await home.refused()


async def test_pass_through_reads_deny_as_allow(home):
    home.access_key.pass_through = True
    await home.set_cap("deny")
    await home.ask()
    assert len(home.wire.posts) == 1


async def test_a_revoked_key_is_refused(home):
    assert await home.data.store.async_archive_access_key(home.access_key.id, True)
    await home.refused()


async def test_an_expired_key_is_refused(home):
    home.access_key.expires_at = utcnow() - timedelta(minutes=1)
    await home.refused()


@pytest.mark.parametrize(("patch_settings", "text"), [
    ({"ai_task_decisions_enabled": False}, "not fully configured"),
    ({"ai_task_decisions_access_key_id": None}, "not fully configured"),
    ({"decision_provider_id": None}, "not fully configured"),
    ({"kill_switch": True}, "changed"),
])
async def test_settings_refuse_the_call(home, patch_settings, text):
    await home.data.store.async_patch_settings(**patch_settings)
    assert text in await home.refused()


async def test_a_removed_account_is_refused(home):
    await home.store.delete(home.account)
    assert "not fully configured" in await home.refused()


async def test_a_deactivated_user_is_refused(hass, home):
    user = await hass.auth.async_create_user("Guest", group_ids=["system-users"])
    user.is_active = False
    assert "changed" in await home.refused(user_id=user.id)


async def test_an_unknown_user_is_refused(home):
    assert "changed" in await home.refused(user_id="not-a-user")


async def test_a_non_admin_user_keeps_the_keys_allowance(hass, home):
    user = await hass.auth.async_create_user("Guest", group_ids=["system-users"])
    await home.ask(user_id=user.id)
    assert len(home.wire.posts) == 1


async def test_a_change_while_the_account_loads_spends_no_allowance(home):
    """The provider reserves before its own guard, so this one runs ahead of the request."""
    async def loaded(_hass):
        await home.data.store.async_patch_settings(kill_switch=True)
        return home.store

    with patch("custom_components.phoenix_mcp.routing.decision_evaluation._get_secret_store", loaded):
        assert "changed" in await home.refused()


@pytest.mark.parametrize("change", [
    lambda home: home.data.store.async_archive_access_key(home.access_key.id, True),
    lambda home: home.store.set_model(home.account, "jev-1.13.0"),
    lambda home: home.data.store.async_patch_settings(ai_task_decisions_enabled=False),
    lambda home: home.data.store.async_patch_settings(kill_switch=True),
])
async def test_a_change_during_the_last_await_stops_the_request(home, change):
    async def act():
        await change(home)

    home.wire.before_send[1] = act
    with pytest.raises(HomeAssistantError, match="changed"):
        await home.ask()
    assert home.wire.posts == []


@pytest.mark.parametrize("change", [
    lambda home: home.data.store.async_archive_access_key(home.access_key.id, True),
    lambda home: home.store.set_model(home.account, "jev-1.13.0"),
    lambda home: home.data.store.async_patch_settings(kill_switch=True),
])
async def test_a_change_while_in_flight_withholds_the_answer(home, change):
    async def act():
        await change(home)

    home.wire.after_reply[1] = act
    with pytest.raises(HomeAssistantError, match="changed"):
        await home.ask()


async def test_a_capability_lowered_while_in_flight_withholds_the_answer(home):
    async def act():
        await home.set_cap("deny")

    home.wire.after_reply[1] = act
    with pytest.raises(HomeAssistantError, match="service response capability"):
        await home.ask()


@pytest.mark.parametrize("pass_through", [False, True])
async def test_no_access_key_reaches_the_entity_through_call_service(hass, pass_through):
    """Phoenix's own entities are outside every grant, the bound key's included."""
    calls: list = []

    async def handler(call):
        calls.append(call)

    entity = er.async_get(hass).async_get_or_create("ai_task", DOMAIN, "e_ai_evaluate", suggested_object_id="decisions")
    hass.states.async_set(entity.entity_id, "unknown")
    hass.services.async_register("ai_task", "evaluate", handler)
    key = AccessKeyRecord(id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
                          permissions=PermissionTree(), pass_through=pass_through, cap_service_response="allow")
    data = MagicMock(mesa=None, mesa_setup_failed=False)
    data.store.get_settings.return_value.mesa_mode = "off"
    data.store.get_settings.return_value.ai_task_decisions_access_key_id = key.id
    content, outcome, _ = await _call_tool(
        "call_service",
        {"service": {"domain": "ai_task", "name": "evaluate"}, "service_data": {"task_name": "t", "questions": {}},
         "targets": [{"kind": "entity", "ids": [entity.entity_id]}]},
        key, hass, data)
    assert outcome != "allowed", content
    assert calls == []


# --------------------------------------------------------------------------- #
# Failures
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("status", "text"), [
    (401, "API key rejected"), (429, "limiting requests"), (500, "refused the request"), (403, "API key rejected"),
])
async def test_a_failed_request_keeps_its_text_out_of_the_error(home, status, text):
    home.wire.status = status
    with pytest.raises(HomeAssistantError) as raised:
        await home.ask(state="private household note")
    assert text in str(raised.value)
    assert "private household note" not in str(raised.value)
    assert "wire-secret" not in str(raised.value)


@pytest.mark.parametrize("code", ["request_budget_exhausted", "budget_unavailable"])
async def test_an_exhausted_allowance_is_refused_without_a_request(home, code):
    from custom_components.phoenix_mcp.routing.decision_budget import DecisionBudgetError

    home.data.decision_budget = SimpleNamespace(reserve=AsyncMock(side_effect=DecisionBudgetError(code)))
    with pytest.raises(HomeAssistantError, match="request limit"):
        await home.ask()
    assert home.wire.posts == []


async def test_an_unexpected_failure_reads_as_unavailable(home):
    with patch("custom_components.phoenix_mcp.routing.decision_evaluation._admit", side_effect=RuntimeError("private")):
        with pytest.raises(HomeAssistantError, match="Could not reach") as raised:
            await home.ask()
    assert "private" not in str(raised.value)


GOOD_SCORE = {"type": "score", "score": 0, "confidence": 1, "legend": {"0": "a", "1": "b", "2": "c"},
              "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0}}


@pytest.mark.parametrize(("questions", "answers"), [
    ({"arrived": NOUL}, {}),
    ({"arrived": NOUL}, {"arrived": {"type": "noul", "noul": 0.5}, "extra": {"type": "noul", "noul": 0.5}}),
    ({"arrived": NOUL}, {"arrived": {"type": "noul", "noul": 1.2}}),
    ({"arrived": NOUL}, {"arrived": {"type": "noul", "noul": True}}),
    ({"arrived": NOUL}, {"arrived": {"type": "choice", "noul": 0.5}}),
    ({"arrived": NOUL}, {"arrived": "yes"}),
    ({"door": CHOICE}, {"door": {"type": "choice", "choice": "side", "confidence": 1,
                                 "probabilities": {"front": 0.5, "back": 0.5}}}),
    ({"door": CHOICE}, {"door": {"type": "choice", "choice": "front", "confidence": 1,
                                 "probabilities": {"front": 0.9, "back": 0.9}}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "legend": {"Can wait": 0, "Soon": 1, "Now": 2}}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "probabilities": {"Can wait": 1.0, "Soon": 0.0, "Now": 0.0}}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "probabilities": {"0": 1.0, "1": 0.0}}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "probabilities": {"0": 0.5, "1": 0.0, "2": 0.0}}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "score": 3}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "score": -0.5}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "score": 1.5}}),
    ({"urgency": SCORE}, {"urgency": {**GOOD_SCORE, "score": True}}),
    ({"urgency": SCORE}, {"urgency": {k: v for k, v in GOOD_SCORE.items() if k != "confidence"}}),
])
async def test_an_answer_that_does_not_fit_its_question_is_refused(home, questions, answers):
    home.wire.reply = {"model": "jev-1.14.0", "answers": answers}
    with pytest.raises(HomeAssistantError, match="could not read"):
        await home.ask(questions)


@pytest.mark.parametrize("model", ["", None, 5, "bad model", "x" * 201])
def test_a_bad_model_identifier_is_refused_for_every_type(model):
    body = {"model": model, "answers": {"arrived": {"type": "noul", "noul": 0.5}}}
    with pytest.raises(DecisionError):
        parse_evaluation(body, {"arrived": NOUL})


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

class _Prefs:
    gen_data_entity_id = None
    evaluate_entity_id = None

    def async_set_preferences(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _OldPrefs:
    """An older Home Assistant, whose preferences have no evaluation default."""

    gen_data_entity_id = None

    def async_set_preferences(self, **kwargs):
        raise TypeError(sorted(kwargs))


FULL = {"ai_task_decisions_enabled": True, "ai_task_decisions_access_key_id": "k", "decision_provider_id": "a"}


@pytest.mark.parametrize(("settings", "expected"), [
    (FULL, True),
    ({**FULL, "ai_task_decisions_enabled": False}, False),
    ({**FULL, "ai_task_decisions_access_key_id": None}, False),
    ({**FULL, "decision_provider_id": None}, False),
    ({}, False),
])
def test_configured_needs_all_three(settings, expected):
    assert decisions_configured(GlobalSettings(**settings)) is expected


async def _added(hass, home, platform, entry):
    await ai_task.async_setup_entry(hass, entry, platform._async_schedule_add_entities_for_entry)
    await hass.async_block_till_done()
    return home.data.ai_evaluate_entity


@requires_evaluate
async def test_the_entity_leaves_and_clears_only_its_own_default(hass, home, phoenix_ai_task_platform):
    platform, entry = phoenix_ai_task_platform
    entity = await _added(hass, home, platform, entry)
    entity_id = entity.entity_id
    prefs = _Prefs()
    prefs.gen_data_entity_id = "ai_task.other"
    with patch.object(ai_task, "_ai_task_preferences", return_value=prefs):
        status = ai_task.set_ai_evaluate_preferred(hass, entry)
        assert status["is_preferred"] is True and prefs.evaluate_entity_id == entity_id
        await home.data.store.async_patch_settings(ai_task_decisions_enabled=False)
        home.data.async_sync_ai_task()
    await hass.async_block_till_done()
    assert home.data.ai_evaluate_entity is None
    assert prefs.evaluate_entity_id is None and prefs.gen_data_entity_id == "ai_task.other"
    assert er.async_get(hass).async_get(entity_id) is None


@requires_evaluate
async def test_generate_default_is_not_cleared_with_the_decisions_default(hass, home, phoenix_ai_task_platform):
    platform, entry = phoenix_ai_task_platform
    await _added(hass, home, platform, entry)
    prefs = _Prefs()
    prefs.gen_data_entity_id = "ai_task.other"
    with patch.object(ai_task, "_ai_task_preferences", return_value=prefs):
        ai_task.set_ai_evaluate_preferred(hass, entry)
        status = ai_task.clear_ai_evaluate_preferred(hass, entry)
    assert status["is_preferred"] is False and prefs.evaluate_entity_id is None
    assert prefs.gen_data_entity_id == "ai_task.other"


@requires_evaluate
async def test_another_default_is_named_and_left_on_clear(hass, home, phoenix_ai_task_platform):
    platform, entry = phoenix_ai_task_platform
    await _added(hass, home, platform, entry)
    prefs = _Prefs()
    prefs.evaluate_entity_id = "ai_task.other"
    with patch.object(ai_task, "_ai_task_preferences", return_value=prefs):
        status = ai_task.clear_ai_evaluate_preferred(hass, entry)
    assert prefs.evaluate_entity_id == "ai_task.other"
    assert status["evaluate_name"] == "ai_task.other" and status["is_preferred"] is False


def test_setting_the_default_needs_a_loaded_entity(hass):
    with pytest.raises(ai_task.AiTaskSetupError) as raised:
        ai_task.set_ai_evaluate_preferred(hass, SimpleNamespace(entry_id="nope"))
    assert raised.value.args[0].key == "aiDecisionsNotConfigured"


async def test_a_leftover_after_a_downgrade_is_removed_without_an_unknown_preference(hass, home, phoenix_ai_task_platform):
    platform, entry = phoenix_ai_task_platform
    registered = er.async_get(hass).async_get_or_create(
        "ai_task", DOMAIN, f"{entry.entry_id}_ai_evaluate", suggested_object_id="decisions")
    await home.data.store.async_patch_settings(ai_task_decisions_enabled=False)
    with patch.object(ai_task, "_ai_task_preferences", return_value=_OldPrefs()):
        ai_task.async_sync_ai_task(hass, entry, home.data, MagicMock())
    assert er.async_get(hass).async_get(registered.entity_id) is None


@requires_evaluate
async def test_a_disabled_entity_is_not_added(hass, home, phoenix_ai_task_platform):
    platform, entry = phoenix_ai_task_platform
    er.async_get(hass).async_get_or_create(
        "ai_task", DOMAIN, f"{entry.entry_id}_ai_evaluate", disabled_by=er.RegistryEntryDisabler.USER)
    add = MagicMock()
    ai_task.async_sync_ai_task(hass, entry, home.data, add)
    assert home.data.ai_evaluate_entity is None
    assert all(not isinstance(call.args[0][0], ai_task.PhoenixEvaluationEntity) for call in add.call_args_list)


@requires_evaluate
async def test_availability_follows_each_gate(hass, home, phoenix_ai_task_platform):
    platform, entry = phoenix_ai_task_platform
    entity = await _added(hass, home, platform, entry)
    assert entity.available is True
    await home.data.store.async_patch_settings(kill_switch=True)
    assert entity.available is False
    await home.data.store.async_patch_settings(kill_switch=False)
    home.data.ready = False
    assert entity.available is False
    home.data.ready = True
    hass.data.pop("_phoenix_agentcli_store")
    assert entity.available is False


@pytest.mark.parametrize("enabled", [True, False])
async def test_startup_loads_the_account_only_when_decisions_are_on(hass, home, enabled):
    await home.data.store.async_patch_settings(ai_task_decisions_enabled=enabled)
    home.data.async_sync_ai_task = MagicMock()
    with patch.object(ai_task, "_get_secret_store", AsyncMock()) as load:
        await ai_task.async_refresh_ai_task(hass, home.data)
    assert load.await_count == int(enabled)
    home.data.async_sync_ai_task.assert_called_once_with()


async def test_an_older_home_assistant_never_creates_the_entity(hass, home):
    add = MagicMock()
    with patch.object(ai_task, "_EVALUATE_FEATURE", None):
        assert ai_task.ai_task_decisions_supported() is False
        ai_task.async_sync_ai_task(hass, SimpleNamespace(entry_id="e"), home.data, add)
    assert home.data.ai_evaluate_entity is None
