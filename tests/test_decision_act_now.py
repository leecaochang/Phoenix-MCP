"""The act-now question asked beside every first-message route, and the veto it gives.

Models read "don't turn on the bed light" as an off command even though the route question names
negation; asked on its own, a second question separates negated, quoted, conditional and deferred
sentences from real commands. It only vetoes: a plain command still needs the route and target bars.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.phoenix_mcp.llm.accounts import ProviderConfig
from custom_components.phoenix_mcp.routing.decision_batch import temporal_proposal
from custom_components.phoenix_mcp.routing.decision_provider import DecisionProvider
from custom_components.phoenix_mcp.routing.decision_shadow import DecisionShadow
from custom_components.phoenix_mcp.routing.decision_types import DecisionError, act_now_confirmed
from custom_components.phoenix_mcp.routing.routing_tables import (
    DECISION_ACT_NOW_CHOICES, DECISION_ACT_NOW_INSTRUCTIONS, DECISION_ACT_NOW_MIN_PROBABILITY, DECISION_ROUTE_CHOICES,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


def _choice(selected: str, keys, top: float = 1.0, model: str = "test-model") -> dict:
    rest = (1 - top) / max(len(keys) - 1, 1)
    return {"type": "choice", "choice": selected, "confidence": top,
            "probabilities": {key: top if key == selected else rest for key in keys}}


def _act(selected: str = "act_now", top: float = 1.0) -> dict:
    return _choice(selected, list(DECISION_ACT_NOW_CHOICES), top)


async def _classify(answers: dict, model: str = "test-model", **body) -> tuple[dict, list[dict]]:
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({"model": model, "answers": answers, **body})
    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    async with TestServer(app) as server, ClientSession() as session:
        cfg = ProviderConfig("local_decision", model, str(server.make_url("/v1")))
        return await DecisionProvider(cfg, "systemone").classify(session, "Turn on the bed light", 5000), seen


# --------------------------------------------------------------------------- #
# The question and its answer
# --------------------------------------------------------------------------- #

async def test_classify_asks_the_act_question_beside_the_route():
    answer, seen = await _classify({"route": _choice("light_on", list(DECISION_ROUTE_CHOICES)), "act": _act()})
    questions = seen[0]["questions"]
    assert set(questions) == {"route", "act"}
    assert questions["act"] == {"type": "choice", "instructions": DECISION_ACT_NOW_INSTRUCTIONS,
                                "criteria": dict(DECISION_ACT_NOW_CHOICES)}
    assert answer["choice"] == "light_on"
    assert answer["act"]["choice"] == "act_now"
    # The act answer carries its own bar, not the account's.
    assert answer["act"]["min_probability"] == DECISION_ACT_NOW_MIN_PROBABILITY
    assert answer["min_probability"] == 0.9


@pytest.mark.parametrize("answers", [
    {"route": _choice("light_on", list(DECISION_ROUTE_CHOICES))},
    {"route": _choice("light_on", list(DECISION_ROUTE_CHOICES)), "act": _act(), "extra": _act()},
    {"route": _choice("light_on", list(DECISION_ROUTE_CHOICES)),
     "act": {"type": "choice", "choice": "act_now", "confidence": 1, "probabilities": {"act_now": 1.0}}},
    {"route": _choice("light_on", list(DECISION_ROUTE_CHOICES)), "act": _choice("yes", ["yes", "no"])},
])
async def test_a_missing_extra_or_malformed_act_answer_is_refused(answers):
    with pytest.raises(DecisionError, match="malformed"):
        await _classify(answers)


@pytest.mark.parametrize("act,confirmed", [
    (_act("act_now", 1.0), True),
    (_act("act_now", 0.80), True),
    (_act("act_now", DECISION_ACT_NOW_MIN_PROBABILITY), True),
    (_act("act_now", 0.74), False),
    (_act("not_now", 0.99), False),
    (_act("not_now", 0.55), False),
    ({**_act("act_now", 1.0), "probabilities": {"act_now": 0.5, "not_now": 0.5}}, False),
    (None, False),
    ({}, False),
    ({"choice": "act_now", "probabilities": {"act_now": 1.0, "maybe": 0.0}}, False),
])
def test_act_now_is_confirmed_only_by_a_confident_act_now_answer(act, confirmed):
    if isinstance(act, dict) and "probabilities" in act and set(act["probabilities"]) == set(DECISION_ACT_NOW_CHOICES):
        act = {**act, "min_probability": DECISION_ACT_NOW_MIN_PROBABILITY}
    assert act_now_confirmed(act) is confirmed


# --------------------------------------------------------------------------- #
# The veto
# --------------------------------------------------------------------------- #

def _route(act: dict | None, choice: str = "light_off") -> dict:
    route = {**_choice(choice, list(DECISION_ROUTE_CHOICES), 0.97), "model": "test-model", "min_probability": 0.9}
    if act is not None:
        route["act"] = {**act, "model": "test-model", "min_probability": DECISION_ACT_NOW_MIN_PROBABILITY}
    return route


@pytest.mark.parametrize("act", [_act("not_now", 0.97), _act("act_now", 0.6), None])
async def test_a_sequential_target_is_never_requested_without_act_now(act):
    """"Don't turn on the bed light", read as light_off at 0.97, stops before any target request."""
    provider = MagicMock()
    provider.select_target = AsyncMock()
    authority = MagicMock()
    result = await DecisionShadow()._select_target(MagicMock(), authority, provider, MagicMock(),
                                                   "Don't turn on the bed light", _route(act), 5000, prepare=True)
    assert result["reason"] == "not_immediate"
    provider.select_target.assert_not_awaited()
    authority.current.assert_not_called()


async def test_a_confirmed_act_now_reaches_the_target_step():
    provider = MagicMock()
    authority = MagicMock()
    authority.current.return_value = None
    result = await DecisionShadow()._select_target(MagicMock(), authority, provider, MagicMock(),
                                                   "Turn off the bed light", _route(_act("act_now", 0.95)), 5000, prepare=True)
    # Past the veto; this one then stops at the current authority, which the test leaves empty.
    assert result["reason"] == "authority_changed"


def _temporal(act: dict | None) -> dict:
    answer = {"route": {**_choice("time_value", ["time_value", "provider"]), "model": "m"},
              "target": {**_choice("t0", ["t0", "none"]), "model": "m"},
              "value": {**_choice("value", ["value", "none"]), "model": "m"}}
    if act is not None:
        answer["act"] = {**act, "model": "m", "min_probability": DECISION_ACT_NOW_MIN_PROBABILITY}
    return answer


@pytest.mark.parametrize("act", [_act("not_now", 0.95), None])
def test_a_temporal_update_needs_act_now(act):
    reason, proposal = temporal_proposal(_temporal(act), {"t0": {"entity_id": "input_datetime.wake",
                                                                 "eligible_categories": ["time_value"]}},
                                         {"kind": "time", "value": "07:00:00"}, "Don't set the wake time to 7", "en", [])
    assert (reason, proposal) == ("not_immediate", None)
