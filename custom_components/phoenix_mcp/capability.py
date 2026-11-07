"""Resolve which capability a service needs and how an access key holds it."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .const import (
    ANNOUNCEMENT_SERVICES, CAPABILITY_NAMES, CAP_ALLOW, CAP_CONFIRM, CAP_DENY, CONFIRM_ONLY_CAPS,
    DUAL_GATE_SERVICES, HOUSEHOLD_CAPABILITIES, NON_ACTUATING_SERVICES, NO_TARGET_SERVICES,
    PASS_THROUGH_EXEMPT_CAPS, REVIEWED_ACTION_DOMAINS, REVIEWED_ACTION_SERVICES,
)
from .request_authority import household_authority

if TYPE_CHECKING:
    from .access_key_store import AccessKeyRecord


# effective_cap collapses pass_through interaction into one value for self-summary
# endpoints. It does not replace async_evaluate_capability when enforcing, as it skips the
# approval queue.


_ACTION_NAME_KEYS = frozenset({"action", "service", "service_template"})
_ACTION_ARGUMENT_KEYS = frozenset({"data", "data_template", "target", "variables", "metadata"})


def is_announcement(domain: str, service: str, service_data: object) -> bool:
    """Whether a service call interrupts a speaker with an announcement."""
    if f"{domain}/{service}" in ANNOUNCEMENT_SERVICES or (domain == "tts" and service.endswith("_say")):
        return True
    return (domain, service) == ("media_player", "play_media") and isinstance(service_data, dict) and bool(service_data.get("announce"))


def service_capabilities(domain: str, service: str, service_data: object, targets: Iterable[str]) -> tuple[str, ...]:
    """Return every capability a service needs beyond Write, physical control aside.

    One definition for admission, execution and preview; the executor is the authority. What
    a call reaches adds to what its name needs and never replaces it, so a service in any
    domain that acts on a notify entity needs cap_notify as well.
    """

    service_key = f"{domain}/{service}"
    needed: list[str] = []
    if service_key in DUAL_GATE_SERVICES:
        needed.append("cap_restart")
    if domain == "notify" or (
        service_key not in NON_ACTUATING_SERVICES and any(target.split(".", 1)[0] == "notify" for target in targets)
    ):
        needed.append("cap_notify")
    if is_announcement(domain, service, service_data):
        needed.append("cap_broadcast")
    if service_key in NO_TARGET_SERVICES:
        needed.append("cap_yaml_edit")
    if service_key == "scene/delete":
        needed.append("cap_scene_write")
    if service_key == "recorder/purge_entities":
        needed.append("cap_registry_write")
    return tuple(needed)


def reviewed_actions(config: object) -> frozenset[str]:
    """Return what a saved config calls that needs a person's review.

    A listed service, a templated action name, or a blueprint, since Phoenix cannot tell what
    the last two will run. `data` and the like are arguments to a call, not calls.
    """
    found: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if isinstance(blueprint := node.get("use_blueprint"), dict):
                found.add(f"blueprint {blueprint.get('path')}")
            for key, value in node.items():
                if key in _ACTION_NAME_KEYS and isinstance(value, str):
                    name = value.strip().lower()
                    domain, _, service = name.partition(".")
                    if ("{{" in name or "{%" in name or domain in REVIEWED_ACTION_DOMAINS
                            or f"{domain}/{service}" in REVIEWED_ACTION_SERVICES):
                        found.add(value.strip())
                elif key not in _ACTION_ARGUMENT_KEYS:
                    visit(value)

    visit(config)
    return frozenset(found)


def effective_cap(access_key: AccessKeyRecord, cap_name: str) -> str:
    """Return the cap mode after applying pass_through interaction rules.
    Under pass_through a non-exempt "deny" becomes "allow" and "confirm" stays. A confirm-only
    cap never reads "allow". A household non-administrator gets "deny" outside
    HOUSEHOLD_CAPABILITIES.
    """

    household = household_authority.get()
    if household is not None and not household.is_admin() and cap_name not in HOUSEHOLD_CAPABILITIES:
        return CAP_DENY
    raw = getattr(access_key, cap_name, CAP_DENY)
    if raw == CAP_ALLOW and cap_name in CONFIRM_ONLY_CAPS:
        return CAP_CONFIRM
    if cap_name in PASS_THROUGH_EXEMPT_CAPS:
        return raw
    if access_key.pass_through:
        if raw == CAP_CONFIRM:
            return CAP_CONFIRM
        return CAP_ALLOW
    return raw


def strictest_capability(access_key: AccessKeyRecord, *cap_names: str | None) -> str | None:
    """Return the named capability that binds this key hardest, Deny before Confirm before Allow.

    A call needing several passes only if each does, and one gate decides it.
    """
    order = (CAP_DENY, CAP_CONFIRM)
    needed = [name for name in cap_names if name is not None]

    def rank(name: str) -> int:
        mode = effective_cap(access_key, name)
        return order.index(mode) if mode in order else len(order)

    return min(needed, key=rank, default=None)


def access_key_has_write_scope(access_key: AccessKeyRecord) -> bool:
    """Return True if the access key can write to at least one entity.

    Decides tools/list for control tools. An advisory over-approximation: any GREEN grant
    counts, and the per-call check is the real gate.
    """
    if access_key.pass_through:
        return True
    tree = access_key.permissions
    for nodes in (tree.domains, tree.devices, tree.entities):
        for node in nodes.values():
            if node.state == "GREEN":
                return True
    return False


def effective_caps(access_key: AccessKeyRecord) -> dict[str, str]:
    """Return {cap name: effective mode} for every capability of an access key."""
    return {name: effective_cap(access_key, name) for name in CAPABILITY_NAMES}
