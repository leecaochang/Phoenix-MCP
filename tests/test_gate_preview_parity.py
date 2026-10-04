"""The preview tools must predict the verdict the enforcing path will reach.

dry_run_service lets an agent ask "what would happen" before it actuates, which
is only worth anything if the answer matches. A preview that encodes a gate rule
separately from the enforcing path agrees until one of them changes, then fails
silently in the worst direction: the preview says allowed, the operator approves,
the real call denies or pends.

The physical gate rule therefore lives once, in
policy_engine.physical_gate_applies, and both call_needs_physical_gate and
dry_run_service call it. These tests pin the predicate's behaviour and the
structural fact that the preview does not re-derive the rule.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from custom_components.phoenix_mcp.const import PHYSICAL_GATE_DOMAINS
from custom_components.phoenix_mcp.policy_engine import physical_gate_applies

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG = REPO / "custom_components" / "phoenix_mcp"


class TestPredicate:
    """physical_gate_applies is the single definition of the physical gate."""

    @pytest.mark.parametrize("domain", sorted(PHYSICAL_GATE_DOMAINS))
    def test_every_service_on_an_actuator_domain_gates(self, domain):
        """Domain-keyed, not service-keyed.

        An exact service-name allowlist would miss cover.toggle, *_cover_tilt and
        alarm_arm_custom_bypass.
        """
        for service in ("toggle", "open_cover_tilt", "arm_custom_bypass", "a_service_invented_tomorrow"):
            assert physical_gate_applies(domain, service, []) is True

    def test_generic_toggle_gates_when_a_target_is_physical(self):
        assert physical_gate_applies("homeassistant", "toggle", ["lock.front"]) is True
        assert physical_gate_applies("homeassistant", "toggle", ["light.k", "cover.blind"]) is True

    def test_generic_toggle_does_not_gate_a_non_physical_target(self):
        """Generic toggle on a non-physical target does not gate, or the gate would mean nothing."""
        assert physical_gate_applies("homeassistant", "toggle", ["light.kitchen"]) is False
        assert physical_gate_applies("homeassistant", "toggle", []) is False

    @pytest.mark.parametrize("service", ["turn_on", "turn_off"])
    def test_generic_turn_on_off_are_excluded(self, service):
        """Generic turn_on/turn_off no-op on physical domains, so gating them would refuse a harmless call.

        HA-version-sensitive: re-verify on HA upgrades.
        """
        assert physical_gate_applies("homeassistant", service, ["lock.front"]) is False

    def test_a_plain_domain_never_gates(self):
        assert physical_gate_applies("light", "turn_on", ["light.kitchen"]) is False


class TestNoSecondDefinition:
    """Structural: the preview must CALL the rule, never restate it.

    Two copies that agree behaviourally still drift when one changes, so the
    durable assertion is that only one copy exists.
    """

    def _func(self, rel: str, name: str) -> ast.AST:
        tree = ast.parse((PKG / rel).read_text())
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
        assert node is not None, f"{name} not found in {rel} - did it move or get renamed?"
        return node

    def test_dry_run_calls_the_shared_predicate(self):
        body = ast.unparse(self._func("tools/discovery.py", "_tool_dry_run_service"))
        assert "physical_gate_applies(" in body, (
            "dry_run_service must call physical_gate_applies, not decide for itself"
        )

    def test_dry_run_does_not_re_derive_the_toggle_case(self):
        """dry_run_service contains no PHYSICAL_GATE_DOMAINS membership test of its own.

        An inline copy of the toggle-redispatch rule is how the preview would
        start to drift from the enforcing path.
        """
        body = ast.unparse(self._func("tools/discovery.py", "_tool_dry_run_service"))
        assert "PHYSICAL_GATE_DOMAINS" not in body, (
            "dry_run_service re-derives the physical gate again; call "
            "physical_gate_applies instead so the preview cannot drift from the call"
        )

    def test_the_enforcing_path_also_routes_through_it(self):
        body = ast.unparse(self._func("policy_engine.py", "call_needs_physical_gate"))
        assert "physical_gate_applies(" in body, (
            "call_needs_physical_gate must delegate to the shared predicate"
        )

    def test_this_check_would_notice_a_second_definition(self):
        """The scan flags a re-derived gate, so its clean result is not vacuous.

        An assertion that reads the wrong function, or unparses to an empty
        string, would pass exactly like a clean codebase.
        """
        fake = ast.parse(
            "def _tool_dry_run_service(args):\n"
            "    return domain in PHYSICAL_GATE_DOMAINS\n"
        ).body[0]
        body = ast.unparse(fake)
        assert "PHYSICAL_GATE_DOMAINS" in body and "physical_gate_applies(" not in body, (
            "the scan cannot see a re-derived gate, so its clean result means nothing"
        )


class TestFindAvailableActionsScope:
    """find_available_actions is deliberately NOT routed through the predicate.

    It enumerates services in the ENTITY's own domain, so `homeassistant.toggle`
    never appears in its list and the toggle-redispatch case cannot arise; its
    plain domain membership test is complete for what it enumerates.
    """

    def test_it_enumerates_only_the_entitys_own_domain(self):
        tree = ast.parse((PKG / "tools" / "discovery.py").read_text())
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == "_tool_find_available_actions")
        body = ast.unparse(node)
        assert "async_services().get(domain" in body, (
            "find_available_actions no longer enumerates a single domain; if it can "
            "now surface homeassistant.toggle it must use physical_gate_applies"
        )
