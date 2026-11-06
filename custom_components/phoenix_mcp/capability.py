"""Resolve which capability a service needs and how an access key holds it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    ANNOUNCEMENT_SERVICES, CAPABILITY_NAMES, CAP_ALLOW, CAP_CONFIRM, CAP_DENY, CONFIRM_ONLY_CAPS,
    DUAL_GATE_SERVICES, HOUSEHOLD_CAPABILITIES, NO_TARGET_SERVICES, PASS_THROUGH_EXEMPT_CAPS,
)
from .request_authority import household_authority

if TYPE_CHECKING:
    from .access_key_store import AccessKeyRecord


# effective_cap collapses pass_through interaction into one value for self-summary
# endpoints. It does not replace async_evaluate_capability when enforcing, as it skips the
# approval queue.


def is_announcement(domain: str, service: str, service_data: object) -> bool:
    """Whether a service call interrupts a speaker with an announcement."""
    if f"{domain}/{service}" in ANNOUNCEMENT_SERVICES:
        return True
    return (domain, service) == ("media_player", "play_media") and isinstance(service_data, dict) and bool(service_data.get("announce"))


def service_capability(domain: str, service: str, service_data: object) -> str | None:
    """Return the capability a service needs beyond Write, physical control aside.

    One definition for admission, execution and preview; the executor is the authority.
    """

    service_key = f"{domain}/{service}"
    if service_key in DUAL_GATE_SERVICES:
        return "cap_restart"
    if domain == "notify":
        return "cap_notify"
    if is_announcement(domain, service, service_data):
        return "cap_broadcast"
    if service_key in NO_TARGET_SERVICES:
        return "cap_yaml_edit"
    if service_key == "scene/delete":
        return "cap_scene_write"
    if service_key == "recorder/purge_entities":
        return "cap_registry_write"
    return None


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
