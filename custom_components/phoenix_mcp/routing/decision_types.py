"""Decision outcomes shared below the provider client: the safe error and the admission heuristic."""

from __future__ import annotations

from .routing_tables import (
    DECISION_ACT_NOW_CHOICES, DECISION_MIN_CHOICE_PROBABILITY, DECISION_SUMMARY_COMPONENTS,
    DECISION_THRESHOLD_MAX, DECISION_THRESHOLD_MIN,
)


def choice_threshold(answer: dict) -> float:
    """The bar an answer must reach: its account's, within bounds, else the default."""
    bar = answer.get("min_probability")
    if (isinstance(bar, (int, float)) and not isinstance(bar, bool)
            and DECISION_THRESHOLD_MIN <= bar <= DECISION_THRESHOLD_MAX):
        return float(bar)
    return DECISION_MIN_CHOICE_PROBABILITY


class DecisionError(ValueError):
    """A safe classification, never a raw provider body or transport exception."""


def confident_choice(answer: dict) -> bool:
    """A conservative admission heuristic; provider probabilities are not accuracy."""
    if "_components" in answer:
        components = answer["_components"]
        return (isinstance(components, dict) and set(components) == set(DECISION_SUMMARY_COMPONENTS)
                and all("_components" not in component and component.get("model") == answer.get("model")
                        and confident_choice(component) for component in components.values()))
    selected = answer["probabilities"][answer["choice"]]
    return selected >= choice_threshold(answer) and sum(
        value == selected for value in answer["probabilities"].values()) == 1


def act_now_confirmed(answer: object) -> bool:
    """Whether the act-now answer says, at its own bar, that the sentence asks to act now.

    A missing or malformed answer never confirms, so a request that did not ask cannot act.
    """
    return (isinstance(answer, dict) and answer.get("choice") == "act_now"
            and isinstance(answer.get("probabilities"), dict) and set(answer["probabilities"]) == set(DECISION_ACT_NOW_CHOICES)
            and confident_choice(answer))
