"""Decision outcomes shared below the provider client: the safe error and the admission heuristic."""

from __future__ import annotations

from .routing_tables import DECISION_MIN_CHOICE_PROBABILITY, DECISION_SUMMARY_COMPONENTS


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
    return selected >= DECISION_MIN_CHOICE_PROBABILITY and sum(
        value == selected for value in answer["probabilities"].values()) == 1
