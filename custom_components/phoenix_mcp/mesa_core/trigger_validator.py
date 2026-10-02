"""TriggerValidator: live validation of triggers_automations declarations (Spec 5.5).

Cross-references profiles declaring ``triggers_automations: none`` against the
actual HA automation configurations supplied by the host. An entity declared
``none`` that appears in an automation trigger or condition block is a stale
and unsafe declaration: agents will skip cascade caution for it.

mesa-core never calls HA: the host provides automation configs through the
``get_automation_configs`` callback, from any source (REST API, YAML parse, or
test fixture). Automations can also reference entities indirectly, through
device triggers and the target selectors of purpose-specific triggers
(HA 2026.7+); resolving those requires the host's ``expand_target`` callback,
because only the host can query the HA registries.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from custom_components.phoenix_mcp.mesa_core._async import run_sync
from custom_components.phoenix_mcp.mesa_core.exceptions import MesaValidationError
from custom_components.phoenix_mcp.mesa_core.inheritance import InheritanceResolver
from custom_components.phoenix_mcp.mesa_core.json_io import check_structure
from custom_components.phoenix_mcp.mesa_core.profile import HA_AUTOMATION_SELECTOR_KEYS, TriggersAutomations
from custom_components.phoenix_mcp.mesa_core.store import ProfileStore

# HA configs use singular and plural section keys depending on age and editor.
_SECTION_KEYS = {
    "trigger": ("trigger", "triggers"),
    "condition": ("condition", "conditions"),
    "action": ("action", "actions"),
}

# Selector keys that reference entities indirectly (device triggers/conditions,
# and the target blocks of purpose-specific triggers). Only the host can
# resolve these against the HA registries.
_TARGET_KEYS = HA_AUTOMATION_SELECTOR_KEYS


@dataclass
class ValidationIssue:
    entity_id: str
    declared_value: str
    automation_id: str
    role: str  # "trigger", "condition", or "action"
    severity: str  # "error" or "warning"
    recommendation: str


def _ensure_acyclic(config: dict[str, Any]) -> None:
    """Reject cycles while allowing shared YAML aliases; avoid recursive descent."""
    active: set[int] = set()
    complete: set[int] = set()
    stack: list[tuple[Any, bool]] = [(config, False)]
    while stack:
        node, leaving = stack.pop()
        if not isinstance(node, dict | list):
            continue
        identity = id(node)
        if leaving:
            active.remove(identity)
            complete.add(identity)
            continue
        if identity in active:
            raise MesaValidationError("cyclic automation configuration")
        if identity in complete:
            continue
        active.add(identity)
        stack.append((node, True))
        values = node.values() if isinstance(node, dict) else node
        stack.extend((value, False) for value in values)


def _collect_references(node: Any, entities: set[str], targets: set[tuple[str, str]]) -> None:
    pending = [node]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if not isinstance(current, dict | list) or id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, list):
            pending.extend(current)
            continue
        for key, value in current.items():
            if key in ("entity_id", "zone"):
                if isinstance(value, str):
                    entities.update(
                        item.strip().lower() for item in value.split(",") if item.strip()
                    )
                elif isinstance(value, list):
                    entities.update(
                        item.strip().lower()
                        for v in value
                        if isinstance(v, str)
                        for item in v.split(",")
                        if item.strip()
                    )
            elif key in _TARGET_KEYS:
                if isinstance(value, str):
                    targets.add((key, value))
                elif isinstance(value, list):
                    targets.update((key, v) for v in value if isinstance(v, str))
            else:
                pending.append(value)


def entities_by_role(
    config: dict[str, Any],
    expand_target: Callable[[str, str], list[str]] | None = None,
) -> dict[str, set[str]]:
    """Entities referenced in an automation config, keyed by role.

    Returns a dict with keys ``"trigger"``, ``"condition"``, and ``"action"``,
    each mapping to the set of entity IDs referenced in that block. Handles the
    singular/plural HA section keys transparently. This is the canonical
    automation-config traversal; hosts building reverse-reference indexes
    should call this rather than reimplementing the entity-ID walk.

    ``expand_target`` resolves indirect references: it is called once per
    target selector found (``kind`` is one of ``area_id``, ``device_id``,
    ``floor_id``, ``label_id``; ``ref`` is the selector value) and returns the
    entity IDs that selector covers in the deployment. Without the callback,
    indirectly referenced entities are invisible to the walk.
    """
    if not isinstance(config, dict):
        raise MesaValidationError("automation configuration must be an object")
    _ensure_acyclic(config)
    check_structure(config)
    result: dict[str, set[str]] = {}
    for role, keys in _SECTION_KEYS.items():
        entities: set[str] = set()
        targets: set[tuple[str, str]] = set()
        for key in keys:
            if key in config:
                _collect_references(config[key], entities, targets)
        if expand_target is not None:
            for kind, ref in targets:
                expanded = expand_target(kind, ref)
                if not isinstance(expanded, list) or not all(
                    isinstance(item, str) for item in expanded
                ):
                    if inspect.iscoroutine(expanded):
                        expanded.close()
                    raise MesaValidationError("expand_target must return an array of entity IDs")
                entities.update(item.strip().lower() for item in expanded)
        result[role] = entities
    return result


class TriggerValidator:
    def __init__(
        self,
        store: ProfileStore,
        *,
        expand_target: Callable[[str, str], list[str]] | None = None,
        resolver: InheritanceResolver | None = None,
    ) -> None:
        self.store = store
        self.expand_target = expand_target
        if resolver is not None:
            store.attach_resolver(resolver)
        self.resolver = store.resolver

    def _is_none(self, entity_id: str) -> bool:
        """Whether an entity reads as ``triggers_automations: none`` to an agent.

        Resolved, not stored: agents consume the effective profile, so a `none`
        inherited from a domain, integration, area, or device profile, or from
        deployment defaults, skips cascade caution exactly as an entity-level
        one does and is just as stale if the entity is in fact a trigger.
        """
        try:
            effective = self.resolver.resolve(entity_id)
        except MesaValidationError:
            return False
        value = effective.operational_boundaries.triggers_automations
        helpers = effective.raw.get("semantic_profile", {}).get("helper_traits", {})
        return value == TriggersAutomations.NONE or (
            value == TriggersAutomations.DEPLOYMENT_DEFINED
            and not helpers.get("affected_automations")
        )

    def _declared_none_entities(self, entity_ids: Iterable[str] | None = None) -> list[str]:
        """Entities that resolve to ``none``.

        ``entity_ids`` is the host's entity registry. Without it only entities
        carrying their own stored profile can be enumerated, so an entity that
        inherits `none` from a broader profile and has no profile of its own is
        invisible to the check.
        """
        candidates = list(entity_ids) if entity_ids is not None else self.store.entity_keys()
        return [key for key in candidates if self._is_none(key)]

    def _walked_configs(
        self, configs: list[dict[str, Any]]
    ) -> tuple[list[tuple[str, dict[str, set[str]]]], list[ValidationIssue]]:
        """Walk each config (and expand its target selectors) exactly once.

        The walk and the ``expand_target`` registry calls are per config, not
        per entity-config pair: with hundreds of ``none`` declarations the
        re-expansion dominated ``validate()``.
        """
        if not isinstance(configs, list):
            if inspect.iscoroutine(configs):
                configs.close()
            raise MesaValidationError("get_automation_configs must return a list")
        walked = []
        issues = []
        for config in configs:
            identifier = config.get("id", "<unknown>") if isinstance(config, dict) else "<unknown>"
            if not isinstance(identifier, str | int):
                identifier = "<invalid>"
            automation_id = str(identifier)[:256]
            try:
                by_role = entities_by_role(config, self.expand_target)
            except Exception as err:
                issues.append(
                    ValidationIssue(
                        "",
                        "unknown",
                        automation_id,
                        "configuration",
                        "error",
                        f"automation configuration cannot be inspected ({type(err).__name__})",
                    )
                )
                continue
            walked.append((automation_id, by_role))
            pending: list[Any] = [config]
            uncertain = False
            while pending:
                node = pending.pop()
                if isinstance(node, str):
                    uncertain |= "{{" in node or "{%" in node
                elif isinstance(node, list):
                    pending.extend(node)
                elif isinstance(node, dict):
                    uncertain |= "use_blueprint" in node
                    pending.extend(node.values())
            if uncertain:
                issues.append(
                    ValidationIssue(
                        "",
                        "unknown",
                        automation_id,
                        "configuration",
                        "warning",
                        "templates or blueprint inputs require host expansion; "
                        "reference coverage is incomplete",
                    )
                )
        return walked, issues

    def _issues_for(
        self, entity_id: str, walked: list[tuple[str, dict[str, set[str]]]]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for automation_id, by_role in walked:
            # Only trigger and condition references invalidate a none declaration
            # (Spec 5.5): an entity written by an action does not trigger automations.
            for role, severity in (("trigger", "error"), ("condition", "warning")):
                if entity_id in by_role[role]:
                    issues.append(
                        ValidationIssue(
                            entity_id=entity_id,
                            declared_value="none",
                            automation_id=automation_id,
                            role=role,
                            severity=severity,
                            recommendation=(
                                f"{entity_id} is declared triggers_automations: none but "
                                f"appears in the {role} block of {automation_id}. "
                                "Change the declaration to 'likely', or to "
                                "'deployment_defined' with affected_automations listing "
                                "this automation."
                            ),
                        )
                    )
        return issues

    def validate(
        self,
        get_automation_configs: Callable[[], list[dict[str, Any]]],
        *,
        entity_ids: Iterable[str] | None = None,
    ) -> list[ValidationIssue]:
        """Cross-reference every effective ``none`` against the automation registry.

        ``entity_ids`` is the host's entity registry. Hosts SHOULD pass it: an
        entity that inherits ``none`` from a domain, integration, or area
        profile without carrying one of its own is not in the store's key set,
        so it can only be checked when the host names it.
        """
        try:
            configs = get_automation_configs()
        except Exception as err:
            raise MesaValidationError("get_automation_configs failed") from err
        walked, issues = self._walked_configs(configs)
        for entity_id in self._declared_none_entities(entity_ids):
            issues.extend(self._issues_for(entity_id, walked))
        return issues

    def validate_entity(
        self,
        entity_id: str,
        get_automation_configs: Callable[[], list[dict[str, Any]]],
    ) -> list[ValidationIssue]:
        """Validate a single entity against the automation registry."""
        if not self._is_none(entity_id):
            return []
        try:
            configs = get_automation_configs()
        except Exception as err:
            raise MesaValidationError("get_automation_configs failed") from err
        walked, issues = self._walked_configs(configs)
        return [*issues, *self._issues_for(entity_id, walked)]

    async def avalidate(
        self,
        get_automation_configs: Callable[[], list[dict[str, Any]]],
        *,
        entity_ids: Iterable[str] | None = None,
    ) -> list[ValidationIssue]:
        return await run_sync(lambda: self.validate(get_automation_configs, entity_ids=entity_ids))

    async def avalidate_entity(
        self,
        entity_id: str,
        get_automation_configs: Callable[[], list[dict[str, Any]]],
    ) -> list[ValidationIssue]:
        return await run_sync(self.validate_entity, entity_id, get_automation_configs)
