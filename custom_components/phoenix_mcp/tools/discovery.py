"""Discovery and analysis tools: the read-only half of the tool surface.

Everything here answers "what is there and what would happen", never "change it". Every enumeration
is scoped to the access key's permission tree first, so a listing never becomes an oracle. Covers
registry reads, relationship analysis walked out of stored YAML configs, and previews (whatif,
dry_run_service, validate_automation_or_script, find_available_actions), which run a real call's
gates and report the verdict without actuating. `_tool_get_capability_summary` lives in
tools/system.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
import dataclasses
import difflib
import functools
import hashlib
import hmac
import json
import logging
import math
import re

from homeassistant.components.automation.config import async_validate_config_item as _validate_automation_config
from homeassistant.components.script.config import async_validate_config_item as _validate_script_config
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.util.dt import as_utc, parse_datetime, utcnow

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import __version__ as ha_version

from ..authoring_validation import async_validation_message, authoring_input_error
from ..const import ANNOUNCEMENT_SERVICES, CAP_ALLOW, CAP_CONFIRM, CAP_DENY, DEVICE_LIST_DEFAULT_LIMIT, DEVICE_LIST_MAX_LIMIT, DUAL_GATE_SERVICES, MAX_COMPARE_LIST_VALUES, MAX_COMPARE_VALUE_CHARS, MAX_DANGLING_PATHS, MAX_CONFIG_CHECK_MESSAGES, MAX_SEARCH_QUERY_LEN, MAX_SEARCH_MEMBERS, MAX_SEARCH_MEMBER_CHECKS, MAX_SEARCH_MEMBER_ID_LEN, MESA_MODE_OFF, MUSIC_ASSISTANT_LIBRARY_SERVICES, STORAGE_HELPER_TYPES, NO_TARGET_SERVICES, PHYSICAL_GATE_DOMAINS, RUNTIME_SERVICE_GUIDANCE, SCRIPT_ENTITY_SERVICES, SEARCH_ENTITIES_MAX_LIMIT, SEARCH_FUZZY_MATCH_CUTOFF, SEARCH_POWER_TYPES, SEARCH_RADIO_TYPES, SERVICE_FIELDS_MAX, SERVICE_FIELD_MAX_DESCRIPTION, SERVICE_FIELD_MAX_OPTIONS, REFERENCE_WARNING_MAX
from ..consumer_identity import ConsumerIdentityIndex, IDENTITY_COVERAGE
from ..household_authority import household_admin_access
from ..data import PhoenixData
from ..device_semantics import (
    DeviceRegistrySnapshot,
    device_authority_chain,
    device_entry_value,
    device_registry_snapshot,
    effective_entity_area_id,
    composed_name_fields,
    entity_name_source,
)
from ..routing.intent_recognition import RecognitionUnavailable, async_recognize
from ..service_targets import dedicated_tool_refusal, secondary_target_error, template_fields, template_fields_error, unbound_service_targets, unbound_target_error, unsupported_secondary_targets
from ..tool_contracts import normalize_tool_args, state_projection
from ..mesa import async_semantic_moments, build_expand_target, entity_control_mode, evaluate_service_call
from ..mesa_core.trigger_validator import entities_by_role
from ..mesa_tools import MESA_TOOLS_CAP, authored_restrictions
from ..helpers import parse_time_param as _parse_time_param
from ..tool_input import sanitize_service_data as _sanitize_service_data, str_arg, str_list_arg
from ..capability import effective_cap, service_capability
from ..redaction import (
    entity_hint_for_agent, redact_diagnostics as _redact_diagnostics, redact_inaccessible_entity_ids_in_text,
)
from .esphome import _ESPHOME_DOMAIN, _esphome_action_signature, _esphome_actions_for_entity, esphome_availability
from ..tool_common import _project_state, _resolve_area_id, _tool_error, _tool_success, fitted_offset_page, longest_fitting, offset_page
from ..ws_dispatch import WsDispatchError, async_get_lovelace_config, async_ws_command
from ..policy_engine import _ENTITY_ID_RE, Permission, assist_expose_check, device_config_entry_ids, device_registry_entity_ids, esphome_entry_writable, filter_entities_for_access_key, filter_service_response, music_assistant_readable_entries, physical_gate_applies, resolve_config_entry_registry_access, resolve_device_registry_access, resolve_esphome_user_service, resolve_read, resolve_registry_access, resolve_service_targets, service_target_binding_valid, scrub_sensitive_attributes
from ..proxy_diagnostics import device_bluetooth_scanners, rf_capabilities
from ..radio import device_radio_types
from ..recorder_queries import iso_utc, state_timestamp
from ..request_authority import check_request_authority
from ..access_key_store import AccessKeyRecord
from .. import yaml_includes
from .helper import _helper_config_entry

_LOGGER = logging.getLogger(__name__)


def _collect_entity_id_values(node: Any, found: set[str]) -> None:
    """Collect entity_id values from a config subtree; for scripts, which mesa-core does not model.

    Automations go through mesa-core's public entities_by_role, keeping HA-format knowledge
    single-sourced.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "entity_id":
                if isinstance(value, str):
                    found.add(value)
                elif isinstance(value, list):
                    found.update(v for v in value if isinstance(v, str))
            else:
                _collect_entity_id_values(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_entity_id_values(item, found)


def _domain_entries(hass: HomeAssistant, domain: str) -> tuple[Any, list[dict]]:
    """Every entry a domain defines through the include layout, plus the branches not read."""
    # Reading automations.yaml and its siblings directly would show a domain defined inline or
    # through !include_dir_list as empty and its entities as unused, the worst answer here. A
    # package tree or broken leaf is returned so a caller can say so.
    found = yaml_includes.read_all_entries(hass.config.config_dir, domain)
    return found.entries, [{"kind": domain, "reason": r} for r in found.unreadable]


def _references_for_entity_details(
    hass: HomeAssistant, entity_id: str,
) -> tuple[list[dict], list[dict]]:
    """Scope-agnostic reverse index: automations/scripts/scenes referencing entity_id.

    Automations use entities_by_role with expand_target, finding indirect device, area, floor or
    label targets. Scripts and scenes are extracted here. Callers apply key scoping.
    """
    refs: list[dict] = []
    not_searched: list[dict] = []
    expand = build_expand_target(hass)

    automations, gaps = _domain_entries(hass, "automation")
    not_searched.extend(gaps)
    for cfg in automations:
        if not isinstance(cfg, dict):
            continue
        by_role = entities_by_role(cfg, expand)
        roles = sorted(role for role, ents in by_role.items() if entity_id in ents)
        if roles:
            refs.append({"kind": "automation", "id": str(cfg.get("id", "")), "name": cfg.get("alias"), "roles": roles,
                         **({"_identity_config": cfg} if not cfg.get("id") else {})})

    scripts, gaps = _domain_entries(hass, "script")
    not_searched.extend(gaps)
    for script_id, cfg in scripts.items():
        if not isinstance(cfg, dict):
            continue
        found: set[str] = set()
        _collect_entity_id_values(cfg, found)
        if entity_id in found:
            refs.append({"kind": "script", "id": script_id, "name": cfg.get("alias"), "roles": ["sequence"]})

    scenes, gaps = _domain_entries(hass, "scene")
    not_searched.extend(gaps)
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        members = scene.get("entities")
        if isinstance(members, dict) and entity_id in members:
            refs.append({"kind": "scene", "id": str(scene.get("id", "")), "name": scene.get("name"), "roles": ["member"],
                         **({"_identity_config": scene} if not scene.get("id") else {})})

    return refs, not_searched


# ---------------------------------------------------------------------------
# Relationship scanning (get_relationships)
# ---------------------------------------------------------------------------


def _resolve_area(hass: HomeAssistant, query: str) -> Any | None:
    """An area by id, then by name, then by alias.

    Shared by describe_area and get_relationships so both accept the same string.
    """
    area_reg = ar.async_get(hass)
    target = area_reg.async_get_area(query)
    if target is not None:
        return target
    wanted = query.lower()
    for area in area_reg.async_list_areas():
        aliases = {alias.lower() for alias in (area.aliases or [])}
        if (area.name or "").lower() == wanted or wanted in aliases:
            return area
    return None


def _entity_id_strings(node: Any, path: list, out: list[tuple[list, str]]) -> None:
    """Collect every entity-id-shaped string value in a structure, with its path.

    Matches the value, never the key name. The path uses patch_dashboard's addressing form, so a
    dashboard hit is directly actionable.
    """
    # Dashboard cards and config-entry payloads use whatever key the author chose, so a key-name
    # list would go stale and report no dependency where one exists. Over-reporting a coincidental
    # string is the safe direction.
    if isinstance(node, dict):
        for key, value in node.items():
            _entity_id_strings(value, [*path, key], out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _entity_id_strings(value, [*path, index], out)
    elif isinstance(node, str) and _ENTITY_ID_RE.match(node):
        out.append((path, node))


_TEMPLATE_MARKERS = ("{{", "}}", "{%", "|")


_SERVICE_FIELDS = frozenset({"service", "perform_action", "action"})


def _in_service_field(path: list) -> bool:
    """Whether a walked value sits where a SERVICE name goes rather than an id."""
    return bool(path) and path[-1] in _SERVICE_FIELDS


# The script domain's own services, which name a verb rather than a script.
_SCRIPT_SERVICES = frozenset({"script.turn_on", "script.turn_off", "script.toggle", "script.reload"})


def _script_calls(config: Any) -> set[str]:
    """Scripts a configuration runs by name (`action: script.greet`).

    Running a script is its entity id, so a call uses it though no `entity_id:` names it.
    """
    hits: list[tuple[list, str]] = []
    _entity_id_strings(config, [], hits)
    return {eid for path, eid in hits
            if _in_service_field(path) and eid.startswith("script.") and eid not in _SCRIPT_SERVICES}


def _dangling_candidates(
    refs: dict[str, list[dict]], hass: HomeAssistant
) -> dict[str, list[dict]]:
    """Narrow referenced ids to those that were meant to be an entity id.

    A typed holder vouches outright; an untyped holder must name a known domain, and in a
    service-call position only `script` counts. Template syntax and the `all` wildcard are excluded.
    """
    # `dangling_references` is a list an operator acts on, so junk such as a CSS length, version
    # string or filename teaches them to ignore it. A value is kept if any holder vouches. Typed
    # holders are mesa-core's role walk, a script's `entity_id:` key and a scene's entity keys, with
    # no domain check because a reference into a missing domain is more dangling. An untyped holder
    # (dashboard card, config-entry payload) found the value by shape alone. `perform_action:
    # button.press` names a verb, but running a script is its entity id; position is the signal
    # rather than service registration, because HA can keep a script's service after the script is
    # gone.
    domains = {state.entity_id.split(".", 1)[0] for state in hass.states.async_all()}
    domains |= {entry.entity_id.split(".", 1)[0] for entry in er.async_get(hass).entities.values()}
    domains |= set(hass.services.async_services())

    def _vouches(value: str, holder: dict) -> bool:
        if holder["typed"]:
            return True
        if value.split(".", 1)[0] not in domains:
            return False
        return value.startswith("script.") if holder["service"] else True

    out: dict[str, list[dict]] = {}
    for value, holders in refs.items():
        if "." not in value or any(m in value for m in _TEMPLATE_MARKERS):
            continue
        if any(_vouches(value, holder) for holder in holders):
            out[value] = holders
    return out


def _dangling_report(value: str, holders: list[dict]) -> dict:
    """One dangling entry: the dead id and what still points at it.

    Holders are grouped by consumer, since the question is which thing to open. Paths are capped at
    const.MAX_DANGLING_PATHS with the true total alongside.
    """
    grouped: dict[tuple[str, str], dict] = {}
    for holder in holders:
        key = (holder["kind"], holder["id"])
        entry = grouped.setdefault(key, {
            "kind": holder["kind"], "id": holder["id"], "name": holder["name"], "paths": [],
        })
        if "path" in holder:
            entry["paths"].append(holder["path"])
    out = []
    for entry in grouped.values():
        paths = entry.pop("paths")
        if paths:
            entry["path_count"] = len(paths)
            entry["paths"] = paths[:MAX_DANGLING_PATHS]
            entry["truncated"] = len(paths) > MAX_DANGLING_PATHS
        out.append(entry)
    return {"entity_id": value, "referenced_by": out}


def _consumer_visible(consumer: dict, access_key: AccessKeyRecord, hass: HomeAssistant, index: ConsumerIdentityIndex | None = None) -> bool:
    """A reference never grants visibility of the consumer's identity."""
    kind, identity = consumer.get("kind"), str(consumer.get("id", ""))
    if kind == "dashboard":
        return effective_cap(access_key, "cap_lovelace_write") != CAP_DENY
    if kind == "config_entry":
        return resolve_config_entry_registry_access(identity, access_key, hass) in (Permission.READ, Permission.WRITE)
    if kind not in _GRAPH_CONSUMER_KINDS:
        return False
    index = index or ConsumerIdentityIndex(hass)
    return index.visible(kind, identity, access_key, hass, consumer.get("entity_id"))


def _consumer(kind: str, cid: str, name: Any) -> dict:
    return {"kind": kind, "id": cid, "name": name if isinstance(name, str) else None,
            "entities": {}, "roles": set()}


def _record(consumer: dict, entity_id: str, role: str) -> None:
    consumer["entities"].setdefault(entity_id, set()).add(role)
    consumer["roles"].add(role)


def _finish(consumer: dict) -> dict:
    """Render one consumer's accumulated sets as the sorted lists a caller reads."""
    return {
        "kind": consumer["kind"],
        "id": consumer["id"],
        "name": consumer["name"],
        "roles": sorted(consumer["roles"]),
        "entities": [
            {"entity_id": eid, "roles": sorted(roles)}
            for eid, roles in sorted(consumer["entities"].items())
        ],
        **({"paths": consumer["paths"]} if consumer.get("paths") else {}),
        **({"_identity_config": consumer["_identity_config"]} if "_identity_config" in consumer else {}),
    }


_GRAPH_CONSUMER_KINDS = frozenset({
    "automation", "script", "scene", "group", "person",
})

# These describe what the queried entity belongs to, not a configuration that
# would break if the entity changed. HA returns them beside actual consumers.
_GRAPH_NON_CONSUMER_KINDS = frozenset({
    "area", "device", "floor", "label", "config_entry", "integration", "entity",
})


async def _relationship_graph_hits(
    hass: HomeAssistant, scope: set[str],
) -> tuple[set[tuple[str, str, str]], list[dict], bool]:
    """Ask HA's loaded reference graph what consumes each entity in scope.

    Hits are ``(consumer kind, consumer entity id, referenced entity id)``; the boolean says a
    definitive response was read. A missing command or changed shape is an explicit coverage gap.
    """
    # HA's graph sees loaded package, blueprint and integration-managed consumers the YAML walk
    # cannot read, but its extractor skips templated entity ids, so it complements that walk.
    hits: set[tuple[str, str, str]] = set()
    gaps: list[dict] = []
    graph_read = False
    gap_kinds: set[str] = set()

    def _gap(kind: str, reason: str) -> None:
        if kind not in gap_kinds:
            gap_kinds.add(kind)
            gaps.append({"kind": kind, "reason": reason})

    for entity_id in sorted(scope):
        try:
            raw = await async_ws_command(
                hass,
                "search/related",
                {"item_type": "entity", "item_id": entity_id},
            )
        except WsDispatchError:
            _LOGGER.debug("get_relationships could not consult search/related", exc_info=True)
            _gap(
                "reference_graph",
                "Home Assistant's reference graph is unavailable, so UI-loaded "
                "and integration-managed consumers may be missing.",
            )
            break

        if not isinstance(raw, dict):
            _gap(
                "reference_graph",
                "Home Assistant's reference graph returned an unreadable result, so "
                "UI-loaded and integration-managed consumers may be missing.",
            )
            continue

        graph_read = True
        for raw_kind, raw_ids in raw.items():
            kind = str(raw_kind)
            if not isinstance(raw_ids, (set, frozenset, list, tuple)) or any(
                not isinstance(item, str) for item in raw_ids
            ):
                _gap(
                    "reference_graph",
                    "Home Assistant's reference graph returned an unreadable result, so "
                    "UI-loaded and integration-managed consumers may be missing.",
                )
                continue
            ids = set(raw_ids)
            if kind in _GRAPH_CONSUMER_KINDS:
                hits.update((kind, consumer_id, entity_id) for consumer_id in ids)
            elif kind not in _GRAPH_NON_CONSUMER_KINDS and ids:
                _gap(
                    kind,
                    "Home Assistant's reference graph returned a related type Phoenix "
                    "does not model, so this consumer type is not included.",
                )

    return hits, gaps, graph_read


def _graph_consumer_identity(
    hass: HomeAssistant, kind: str, entity_id: str,
) -> tuple[str, str | None]:
    """Translate a graph entity id into Phoenix's existing consumer identity."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is not None and registry_entry.unique_id:
        consumer_id = registry_entry.unique_id
    else:
        domain, separator, object_id = entity_id.partition(".")
        consumer_id = object_id if separator and domain == kind else entity_id

    state = hass.states.get(entity_id)
    name: str | None
    if state is not None:
        name = state.name
    elif registry_entry is not None:
        name = registry_entry.name or registry_entry.original_name
    else:
        name = None
    return str(consumer_id), name


def _merge_graph_hits(
    hass: HomeAssistant,
    consumers: list[dict],
    hits: set[tuple[str, str, str]],
) -> None:
    """Merge graph-only consumers without weakening detailed YAML roles.

    A UI helper's entity joins the config entry the body scan already found, which then names it,
    rather than listing the helper twice.
    """
    by_key = {
        (consumer["kind"], str(consumer["id"])): consumer
        for consumer in consumers if "entity_id" not in consumer
    }
    by_entity = {consumer["entity_id"]: consumer for consumer in consumers if "entity_id" in consumer}
    by_entry = {str(consumer["id"]): consumer for consumer in consumers if consumer["kind"] == "config_entry"}
    registry = er.async_get(hass)
    for kind, graph_entity_id, target_entity_id in sorted(hits):
        consumer_id, name = _graph_consumer_identity(hass, kind, graph_entity_id)
        key = (kind, consumer_id)
        consumer = by_entity.get(graph_entity_id) or by_key.get(key)
        registry_entry = registry.async_get(graph_entity_id)
        if consumer is None and registry_entry is not None and registry_entry.config_entry_id in by_entry:
            consumer = by_entry[registry_entry.config_entry_id]
            consumer.setdefault("entity_id", graph_entity_id)
            by_entity[graph_entity_id] = consumer
        if consumer is None:
            consumer = {
                "kind": kind,
                "id": consumer_id,
                "entity_id": graph_entity_id,
                "name": name,
                "roles": ["reference"],
                "entities": [],
            }
            consumers.append(consumer)
            by_entity[graph_entity_id] = consumer

        existing = next(
            (item for item in consumer["entities"] if item["entity_id"] == target_entity_id),
            None,
        )
        if existing is not None:
            # The body scan knows whether this is a trigger, condition, action,
            # sequence, or member. A graph hit only knows "references", so it
            # must never dilute the stronger role already present.
            continue
        consumer["entities"].append(
            {"entity_id": target_entity_id, "roles": ["reference"]}
        )
        consumer["entities"].sort(key=lambda item: item["entity_id"])
        if "reference" not in consumer["roles"]:
            consumer["roles"].append("reference")
            consumer["roles"].sort()


def _scan_relationships(
    hass: HomeAssistant,
    scope: set[str],
    dashboards: list[tuple[str, str | None, Any]],
    entries: list[dict],
) -> tuple[list[dict], dict[str, list[dict]], list[dict]]:
    """Every consumer touching an entity in `scope`, and every entity id seen.

    The second value maps each referenced id to its holders, each marked `typed` or `service` for
    _dangling_candidates; the third is the unreadable YAML branches.
    """
    # One pass per source covers the whole set of entities. `typed` means HA defines the shape;
    # `service` means the value sat where a service name goes. Naming the holder is what makes a
    # dangling reference actionable. Runs in an executor because it reads YAML files; the caller
    # snapshots dashboards and config entries on the event loop, which obtaining them requires.
    consumers: list[dict] = []
    refs: dict[str, list[dict]] = {}
    unreadable: list[dict] = []
    expand = build_expand_target(hass)

    def _entries(domain: str) -> Any:
        found, gaps = _domain_entries(hass, domain)
        unreadable.extend(gaps)
        return found

    def _note(entity_id: str, holder: dict) -> None:
        refs.setdefault(entity_id, []).append(holder)

    for cfg in _entries("automation"):
        if not isinstance(cfg, dict):
            continue
        by_role = entities_by_role(cfg, expand)
        entry = _consumer("automation", str(cfg.get("id", "")), cfg.get("alias"))
        evidence = {"_identity_config": cfg} if not cfg.get("id") else {}
        entry.update(evidence)
        for role, ents in by_role.items():
            for eid in ents:
                _note(eid, {"kind": "automation", "id": entry["id"],
                            "name": entry["name"], "typed": True, "service": False, **evidence})
                if eid in scope:
                    _record(entry, eid, role)
        for eid in _script_calls(cfg):
            _note(eid, {"kind": "automation", "id": entry["id"],
                        "name": entry["name"], "typed": True, "service": True, **evidence})
            if eid in scope:
                _record(entry, eid, "action")
        if entry["entities"]:
            consumers.append(entry)

    for script_id, cfg in _entries("script").items():
        if not isinstance(cfg, dict):
            continue
        found: set[str] = set()
        _collect_entity_id_values(cfg, found)
        found |= _script_calls(cfg)
        entry = _consumer("script", script_id, cfg.get("alias"))
        for eid in found:
            _note(eid, {"kind": "script", "id": script_id, "name": entry["name"],
                        "typed": True, "service": False})
        for eid in found & scope:
            _record(entry, eid, "sequence")
        if entry["entities"]:
            consumers.append(entry)

    for scene in _entries("scene"):
        if not isinstance(scene, dict):
            continue
        members = scene.get("entities")
        if not isinstance(members, dict):
            continue
        entry = _consumer("scene", str(scene.get("id", "")), scene.get("name"))
        evidence = {"_identity_config": scene} if not scene.get("id") else {}
        entry.update(evidence)
        for member in members:
            if isinstance(member, str):
                _note(member, {"kind": "scene", "id": entry["id"], "name": entry["name"],
                               "typed": True, "service": False, **evidence})
        for eid in set(members) & scope:
            _record(entry, eid, "member")
        if entry["entities"]:
            consumers.append(entry)

    groups = _entries("group")
    if isinstance(groups, dict):
        for group_id, cfg in groups.items():
            if not isinstance(cfg, dict):
                continue
            raw_members = cfg.get("entities", [])
            if isinstance(raw_members, str):
                members = {raw_members}
            elif isinstance(raw_members, list):
                members = {item for item in raw_members if isinstance(item, str)}
            else:
                members = set()
            entry = _consumer("group", str(group_id), cfg.get("name"))
            for member in members:
                _note(
                    member,
                    {
                        "kind": "group",
                        "id": entry["id"],
                        "name": entry["name"],
                        "typed": True,
                        "service": False,
                    },
                )
            for entity_id in members & scope:
                _record(entry, entity_id, "member")
            if entry["entities"]:
                consumers.append(entry)

    for url_path, title, config in dashboards:
        hits: list[tuple[list, str]] = []
        _entity_id_strings(config, [], hits)
        entry = _consumer("dashboard", url_path, title)
        for path, eid in hits:
            _note(eid, {"kind": "dashboard", "id": url_path, "name": title, "path": path,
                        "typed": False, "service": _in_service_field(path)})
        entry["paths"] = []
        for path, eid in hits:
            if eid in scope:
                _record(entry, eid, "card")
                entry["paths"].append(path)
        if entry["entities"]:
            consumers.append(entry)

    for record in entries:
        hits = []
        _entity_id_strings(record["payload"], [], hits)
        entry = _consumer("config_entry", record["entry_id"], record["title"])
        for path, eid in hits:
            _note(eid, {"kind": "config_entry", "id": record["entry_id"],
                        "name": record["title"], "typed": False,
                        "service": _in_service_field(path)})
        for _path, eid in hits:
            if eid in scope:
                _record(entry, eid, record["domain"])
        if entry["entities"]:
            consumers.append(entry)

    return [_finish(c) for c in consumers], refs, unreadable


def _forward_references_details(
    hass: HomeAssistant, entity_id: str,
) -> tuple[list[str], list[dict]]:
    """Entities referenced by entity_id when it is an automation or script, unscoped.

    The caller filters on the event loop, where the Assist-exposure lookup may run. The second value
    names unreadable branches, so an incomplete [] is not proof of no dependencies.
    """
    domain = entity_id.split(".")[0]
    found: set[str] = set()
    not_searched: list[dict] = []
    if domain == "automation":
        entry = er.async_get(hass).async_get(entity_id)
        unique_id = entry.unique_id if entry is not None else None
        if unique_id is not None:
            automations, gaps = _domain_entries(hass, "automation")
            not_searched.extend(gaps)
            for cfg in automations:
                if isinstance(cfg, dict) and str(cfg.get("id", "")) == unique_id:
                    # expand_target resolves device/area/floor/label references
                    # too; the scope filter below still gates what is revealed.
                    for ents in entities_by_role(cfg, build_expand_target(hass)).values():
                        found.update(ents)
                    break
    elif domain == "script":
        script_id = entity_id.split(".", 1)[1] if "." in entity_id else ""
        scripts, gaps = _domain_entries(hass, "script")
        not_searched.extend(gaps)
        cfg = scripts.get(script_id)
        if isinstance(cfg, dict):
            _collect_entity_id_values(cfg, found)
    return sorted(found), not_searched


def _accessible_entity_ids(access_key: AccessKeyRecord, hass: HomeAssistant) -> set[str]:
    """Return the entity IDs the access key can read, through the listing tools' scoping path."""
    # Sharing that path means registry views never reveal entities, areas or devices outside the
    # key's tree.
    accessible = filter_entities_for_access_key(hass.states.async_all(), access_key, hass)
    return {e["entity_id"] for e in accessible}


def _intent_slot_value(entity: Any) -> Any:
    """Return HA's public debug value without disclosing a canonical selector."""
    # Target selectors prefer the caller's text: the canonical value can name an area or entity
    # outside the key's scope, while the submitted text discloses nothing new.
    text = getattr(entity, "text", None)
    return text if text not in (None, "") else getattr(entity, "value", None)


def _intent_slots(result: Any) -> dict[str, Any]:
    """Project recognized slots without HA's private debug metadata."""
    return {
        name: _intent_slot_value(entity)
        for name, entity in getattr(result, "entities", {}).items()
    }


def _intent_source(result: Any) -> str:
    """Classify a sentence without exposing its custom-sentence file path."""
    metadata = getattr(result, "intent_metadata", None)
    if isinstance(metadata, dict) and metadata.get("hass_custom_sentence"):
        return "custom"
    return "builtin"


def _string_set(value: Any) -> set[str] | None:
    """Normalize one HA intent slot to a string set."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set, frozenset)):
        values = {str(item) for item in value if isinstance(item, (str, int, float))}
        return values or None
    if isinstance(value, (int, float)):
        return {str(value)}
    return None


def _recognized_entity_value(result: Any, name: str) -> Any:
    entity = getattr(result, "entities", {}).get(name)
    return getattr(entity, "value", None) if entity is not None else None


def _recognized_targets(
    result: Any, access_key: AccessKeyRecord, hass: HomeAssistant
) -> list[dict[str, Any]]:
    """Resolve one recognition against the intersection of Assist and access key scope.

    HA's matcher treats an empty ``states`` list as "use every state", so the
    explicit empty guard is a security boundary, not an optimization.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.intent import MatchTargetsConstraints
    from homeassistant.helpers.intent import async_match_targets

    name_value = _recognized_entity_value(result, "name")
    area_value = _recognized_entity_value(result, "area")
    floor_value = _recognized_entity_value(result, "floor")
    domains = _string_set(_recognized_entity_value(result, "domain"))
    device_classes = _string_set(_recognized_entity_value(result, "device_class"))
    state_names = _string_set(_recognized_entity_value(result, "state"))
    name = str(name_value) if name_value is not None else None
    area = str(area_value) if area_value is not None else None
    floor = str(floor_value) if floor_value is not None else None

    if not (name or area or floor or domains or device_classes or state_names):
        return []

    accessible_ids = _accessible_entity_ids(access_key, hass)
    accessible_states = [
        state for state in hass.states.async_all()
        if state.entity_id in accessible_ids
    ]
    if not accessible_states:
        return []

    constraints = MatchTargetsConstraints(
        name=name,
        area_name=area,
        floor_name=floor,
        domains=domains,
        device_classes=device_classes,
        assistant="conversation",
    )
    match = async_match_targets(hass, constraints, states=accessible_states)
    if not match.is_match:
        return []

    return [
        {
            "entity_id": state.entity_id,
            "state_match": state_names is None or state.state in state_names,
        }
        for state in sorted(match.states, key=lambda item: item.entity_id)
    ]


async def _tool_recognize_intent(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """Recognize one sentence without invoking an intent handler."""
    if all(
        effective_cap(access_key, cap) == CAP_DENY
        for cap in ("cap_search", "cap_config_read")
    ):
        return _tool_error("Forbidden."), "denied", "recognize_intent"

    sentence = args.get("sentence")
    if not isinstance(sentence, str) or not sentence.strip():
        return (
            _tool_error("sentence must be a non-empty string."),
            "invalid_request", "recognize_intent",
        )
    sentence = sentence.strip()
    if len(sentence) > MAX_SEARCH_QUERY_LEN:
        return (
            _tool_error(
                f"sentence must be at most {MAX_SEARCH_QUERY_LEN} characters."
            ),
            "invalid_request", "recognize_intent",
        )

    language_arg = args.get("language")
    if language_arg is not None and (
        not isinstance(language_arg, str)
        or not language_arg.strip()
        or len(language_arg.strip()) > 64
    ):
        return (
            _tool_error("language must be a non-empty language tag of at most 64 characters."),
            "invalid_request", "recognize_intent",
        )
    language = language_arg.strip() if isinstance(language_arg, str) else hass.config.language


    try:
        result = await async_recognize(hass, sentence, language)
    except RecognitionUnavailable:
        return (
            _tool_error("Home Assistant's default conversation agent is unavailable."),
            "not_implemented", "recognize_intent",
        )

    targets = _recognized_targets(result, access_key, hass) if result is not None else []
    has_selector = result is not None and any(
        _recognized_entity_value(result, key) is not None
        for key in ("name", "area", "floor", "domain", "device_class", "state")
    )
    if (result is None or getattr(result, "unmatched_entities", {})
            or (has_selector and not targets)):
        body = {"match": False, "near_misses": []}
    else:
        body = {
            "match": True,
            "intent": {"name": getattr(result.intent, "name", "")},
            "slots": _intent_slots(result),
            "resolved_entities": targets,
            "source": _intent_source(result),
        }
    return (
        _tool_success(json.dumps(body, default=str)),
        "allowed", "recognize_intent",
    )


async def _tool_list_areas(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list areas containing at least one accessible entity."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_areas"

    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    counts: dict[str, int] = {}
    for eid in _accessible_entity_ids(access_key, hass):
        area_id = _resolve_area_id(registry.async_get(eid), dev_reg)
        if area_id:
            counts[area_id] = counts.get(area_id, 0) + 1

    if access_key.pass_through and household_admin_access() and assist_expose_check(access_key, hass) is None:
        for listed_area in area_reg.areas.values():
            counts.setdefault(listed_area.id, 0)
    areas: list[dict] = []
    for area_id, count in counts.items():
        area = area_reg.async_get_area(area_id)
        if area is None:
            continue
        areas.append({
            "area_id": area.id,
            "name": area.name,
            "floor_id": area.floor_id,
            "aliases": sorted(area.aliases) if area.aliases else [],
            "icon": area.icon,
            "labels": sorted(area.labels),
            "accessible_entity_count": count,
        })
    areas.sort(key=lambda a: (a["name"] or a["area_id"]).lower())
    return _tool_success(json.dumps({"count": len(areas), "areas": areas}, default=str)), "allowed", "list_areas"


async def _tool_list_floors(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list floors containing at least one accessible entity."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_floors"

    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    floor_reg = fr.async_get(hass)
    floor_entity_counts: dict[str, int] = {}
    floor_area_ids: dict[str, set[str]] = {}
    for eid in _accessible_entity_ids(access_key, hass):
        area_id = _resolve_area_id(registry.async_get(eid), dev_reg)
        if not area_id:
            continue
        area = area_reg.async_get_area(area_id)
        if area is None or not area.floor_id:
            continue
        floor_entity_counts[area.floor_id] = floor_entity_counts.get(area.floor_id, 0) + 1
        floor_area_ids.setdefault(area.floor_id, set()).add(area_id)

    if access_key.pass_through and household_admin_access() and assist_expose_check(access_key, hass) is None:
        for listed_floor in floor_reg.floors.values():
            floor_entity_counts.setdefault(listed_floor.floor_id, 0)
            floor_area_ids.setdefault(listed_floor.floor_id, set()).update(
                area.id for area in area_reg.areas.values() if area.floor_id == listed_floor.floor_id
            )
    floors: list[dict] = []
    for floor_id, count in floor_entity_counts.items():
        floor = floor_reg.async_get_floor(floor_id)
        if floor is None:
            continue
        areas = sorted(
            ({"area_id": area.id, "name": area.name}
             for area_id in floor_area_ids[floor_id] if (area := area_reg.async_get_area(area_id)) is not None),
            key=lambda row: (row["name"] or row["area_id"]).lower(),
        )
        floors.append({
            "floor_id": floor.floor_id,
            "name": floor.name,
            "level": floor.level,
            "icon": floor.icon,
            "aliases": sorted(getattr(floor, "aliases", ())),
            "accessible_area_count": len(floor_area_ids[floor_id]),
            "accessible_entity_count": count,
            "areas": areas,
        })
    floors.sort(key=lambda f: (f["level"] if f["level"] is not None else 0, (f["name"] or f["floor_id"]).lower()))
    return _tool_success(json.dumps({"count": len(floors), "floors": floors}, default=str)), "allowed", "list_floors"


async def _tool_list_zones(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list accessible zone.* entities."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_zones"

    accessible = filter_entities_for_access_key(hass.states.async_all(), access_key, hass)
    zones: list[dict] = []
    for e in accessible:
        if not e["entity_id"].startswith("zone."):
            continue
        attrs = e.get("attributes", {})
        zones.append({
            "entity_id": e["entity_id"],
            "name": attrs.get("friendly_name"),
            "latitude": attrs.get("latitude"),
            "longitude": attrs.get("longitude"),
            "radius": attrs.get("radius"),
        })
    zones.sort(key=lambda z: z["entity_id"])
    return _tool_success(json.dumps({"count": len(zones), "zones": zones}, default=str)), "allowed", "list_zones"


async def _tool_list_devices(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list accessible device-registry entries."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_devices"

    registry_state = str(args.get("registry_state") or "enabled").strip().lower()
    if registry_state not in {"enabled", "disabled", "all"}:
        return (
            _tool_error("registry_state must be one of: enabled, disabled, all."),
            "invalid_request",
            "list_devices",
        )

    filters = {key: args.get(key) for key in ("area", "integration", "query")}
    if any(value is not None and not isinstance(value, str) for value in filters.values()):
        return _tool_error("area, integration and query must be strings."), "invalid_request", "list_devices"
    has_area = args.get("has_area")
    area = (filters["area"] or "").strip().lower()
    integration = (filters["integration"] or "").strip().lower()
    query = (filters["query"] or "").strip().lower()
    area_ids = _search_area_ids(hass, area) if area else None

    device_snapshot = device_registry_snapshot(hass)
    devices: list[dict] = []
    for device in device_snapshot.entries:
        if registry_state == "enabled" and device.disabled_by is not None:
            continue
        if registry_state == "disabled" and device.disabled_by is None:
            continue
        if resolve_device_registry_access(
            device.id, access_key, hass, snapshot=device_snapshot
        ) not in (Permission.READ, Permission.WRITE):
            continue
        if area_ids is not None and device_snapshot.effective_area_id(device.id) not in area_ids:
            continue
        if has_area is not None and (device_snapshot.effective_area_id(device.id) is not None) != has_area:
            continue
        if integration and not any(
            (entry := hass.config_entries.async_get_entry(entry_id)) is not None and entry.domain == integration
            for entry_id in device_config_entry_ids(device)
        ):
            continue
        row = _device_list_projection(device, access_key, hass, device_snapshot)
        if query and query not in " ".join(
            str(row.get(key) or "") for key in ("name", "original_name", "manufacturer", "model")
        ).lower():
            continue
        devices.append(row)
    devices.sort(key=lambda d: ((d["name"] or d["device_id"]).lower()))
    paged = offset_page(devices, args, default_limit=DEVICE_LIST_DEFAULT_LIMIT, max_limit=DEVICE_LIST_MAX_LIMIT)
    if isinstance(paged, str):
        return _tool_error(paged), "invalid_request", "list_devices"
    page, envelope = paged
    body = fitted_offset_page(page, envelope, lambda rows, env: {**env, "devices": rows})
    return _tool_success(json.dumps(body, default=str)), "allowed", "list_devices"


async def _tool_get_device(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: return one device plus its accessible entities.

    Returns not_found for both a nonexistent device and a device with no
    accessible entities, so there is no existence oracle across the device set.
    """
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_device"

    device_id = str_arg(args.get("device_id"))
    if not device_id:
        return _tool_error("Missing required argument: device_id"), "invalid_request", "get_device"

    device_snapshot = device_registry_snapshot(hass)
    device = device_snapshot.get(device_id)
    permission = resolve_device_registry_access(
        device_id, access_key, hass, snapshot=device_snapshot
    )
    if device is None or permission not in (Permission.READ, Permission.WRITE):
        return _tool_error("Device not found."), "not_found", device_id

    body = _device_list_projection(device, access_key, hass, device_snapshot)
    body.update({
        "model_id": device_entry_value(device, "model_id"),
        "sw_version": device_entry_value(device, "sw_version"),
        "hw_version": device_entry_value(device, "hw_version"),
        "entities": _accessible_device_entity_ids(device.id, access_key, hass),
        "child_device_ids": [
            child_id
            for child_id in device_snapshot.child_ids(device.id)
            if resolve_device_registry_access(
                child_id, access_key, hass, snapshot=device_snapshot
            )
            in (Permission.READ, Permission.WRITE)
        ],
        "config_entries": _device_config_entry_summaries(device, hass),
    })
    if (scanners := device_bluetooth_scanners(hass, device, access_key)) is not None:
        body["bluetooth_scanners"] = scanners
    return _tool_success(json.dumps(body, default=str)), "allowed", device_id


def _accessible_device_entity_ids(
    device_id: str, access_key: AccessKeyRecord, hass: HomeAssistant
) -> list[str]:
    """Registry entities on a device that this access key may read."""
    expose = assist_expose_check(access_key, hass)
    device = dr.async_get(hass).async_get(device_id)
    force_registry_only = device is not None and device.disabled_by is not None
    return [
        entity_id
        for entity_id in device_registry_entity_ids(hass, device_id)
        if resolve_registry_access(
            entity_id, access_key, hass, force_registry_only=force_registry_only
        )
        in (Permission.READ, Permission.WRITE)
        and (expose is None or expose(entity_id))
    ]


def _device_list_projection(
    device: Any,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    snapshot: DeviceRegistrySnapshot | None = None,
) -> dict[str, Any]:
    """Safe compact projection shared by list_devices and get_device."""
    snapshot = snapshot or device_registry_snapshot(hass)
    parent_device_id = snapshot.child_parent_id(device.id)
    via_device_id = (
        None if parent_device_id is not None else snapshot.parent_id(device.id)
    )
    relation_parent_id = parent_device_id or via_device_id
    if relation_parent_id is not None and resolve_device_registry_access(
        relation_parent_id, access_key, hass, snapshot=snapshot
    ) not in (Permission.READ, Permission.WRITE):
        via_device_id = None
        parent_device_id = None
    # The row already names its area by ID; the name is that same area's label,
    # which "which devices are in the kitchen" is asked in.
    area_id = snapshot.effective_area_id(device.id)
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return {
        "device_id": device.id,
        "name": device.name_by_user or device.name,
        "original_name": device.name,
        "name_by_user": device.name_by_user,
        "manufacturer": device_entry_value(device, "manufacturer"),
        "model": device_entry_value(device, "model"),
        "area_id": area_id,
        "area": area.name if area is not None else None,
        "disabled_by": _registry_value(device.disabled_by),
        "labels": sorted(device.labels),
        "accessible_entity_count": len(
            _accessible_device_entity_ids(device.id, access_key, hass)
        ),
        "owner_count": len(device_config_entry_ids(device)),
        "via_device_id": via_device_id,
        "parent_device_id": parent_device_id,
    }


def _device_config_entry_summaries(device: Any, hass: HomeAssistant) -> list[dict]:
    """Allowlisted, non-secret summaries of a device's owners.

    Titles get the same scrub as list_integrations: integrations often title an
    entry by its host address or URL.
    """
    summaries: list[dict] = []
    for entry_id in device_config_entry_ids(device):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            continue
        summaries.append({
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            "title": _redact_diagnostics(entry.title),
            "disabled_by": _registry_value(entry.disabled_by),
            "supports_remove_device": entry.supports_remove_device,
        })
    return summaries


def _search_area_ids(hass: HomeAssistant, wanted: str) -> set[str]:
    """Area ids named by an area id/name/alias, or every area on a matching floor."""
    def named(item_id: str, name: str | None, aliases: Any) -> bool:
        return wanted in {item_id.lower(), (name or "").lower(), *(str(a).lower() for a in (aliases or ()))}

    areas = ar.async_get(hass).async_list_areas()
    ids = {area.id for area in areas if named(area.id, area.name, area.aliases)}
    floors = {floor.floor_id for floor in fr.async_get(hass).async_list_floors()
              if named(floor.floor_id, floor.name, floor.aliases)}
    ids.update(area.id for area in areas if area.floor_id in floors)
    return ids


def _area_name_for_entity(eid: str, registry: Any, dev_reg: Any, area_reg: Any) -> str | None:
    """Return the area NAME for an entity, or None if it has no area."""
    area_id = _resolve_area_id(registry.async_get(eid), dev_reg)
    if not area_id:
        return None
    area = area_reg.async_get_area(area_id)
    return area.name if area is not None else area_id


# Where else a query word may be found beside the entity's own names, so
# "kitchen", "upstairs", "ikea" or a label find what the home calls by that word.
_SEARCH_CONTEXT_FIELDS = ("area", "floor", "label", "device", "manufacturer", "model")
# A word found only in that context weighs less than one in the entity's name,
# so "kitchen" still lists the Kitchen light before everything in the Kitchen.
_SEARCH_CONTEXT_WEIGHT = 0.3


def _search_context(
    entry: Any, area_name: str | None, snapshot: DeviceRegistrySnapshot, area_reg: Any, floor_reg: Any, label_reg: Any,
) -> dict[str, str]:
    """The lowercased words an entity is known by beyond its own names: its area, floor, labels and device."""
    context: dict[str, str] = {}
    if area_name:
        context["area"] = area_name.lower()
    area_id = effective_entity_area_id(entry, snapshot) if entry is not None else None
    area = area_reg.async_get_area(area_id) if area_id else None
    floor = floor_reg.async_get_floor(area.floor_id) if area is not None and area.floor_id else None
    if floor is not None:
        context["floor"] = floor.name.lower()
    device = snapshot.get(entry.device_id) if entry is not None and entry.device_id else None
    label_ids = set(getattr(entry, "labels", None) or ()) | set(getattr(device, "labels", None) or ())
    names = [label.name for label_id in sorted(label_ids) if (label := label_reg.async_get_label(label_id)) is not None]
    if names:
        context["label"] = " ".join(names).lower()
    if device is not None:
        for key, value in (("device", device.name_by_user or device.name),
                           ("manufacturer", device_entry_value(device, "manufacturer")),
                           ("model", device_entry_value(device, "model"))):
            if isinstance(value, str) and value:
                context[key] = value.lower()
    return context


def _rank_search_matches(matches: list[dict], query: str) -> list[dict]:
    """Token-AND filter and relevance rank for a search_entities query.

    Keeps rows where every token appears in the identity fields or the row's area, floor, label or
    device, ordered by IDF-weighted score. A row matched through context says where in matched.
    """
    # Rarer terms weigh more, exact and prefix or word-start matches get a bonus, and a context-only
    # token weighs less, so the most relevant rows lead and survive truncation.
    access_keys = [t for t in query.split() if t]
    if not access_keys:
        return matches
    docs = [f"{m['entity_id']} {(m.get('friendly_name') or '')} {' '.join(m.get('_aliases', ()))}".lower() for m in matches]
    contexts = [m.get("_context") or {} for m in matches]
    n = len(docs) or 1
    df = {t: sum(1 for d, c in zip(docs, contexts) if t in d or any(t in v for v in c.values())) for t in set(access_keys)}
    full = " ".join(access_keys)
    scored: list[tuple[float, str, dict]] = []
    for m, doc, context in zip(matches, docs, contexts):
        found_in: set[str] = set()
        for t in access_keys:
            if t not in doc:
                fields = {field for field in _SEARCH_CONTEXT_FIELDS if t in context.get(field, "")}
                if not fields:
                    break
                found_in |= fields
        else:
            if found_in:
                m["matched"] = [field for field in _SEARCH_CONTEXT_FIELDS if field in found_in]
            scored.append((_search_score(m, doc, access_keys, df, n, full), m["entity_id"], m))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [m for _, _, m in scored]


def _search_score(m: dict, doc: str, access_keys: list[str], df: dict[str, int], n: int, full: str) -> float:
    """IDF-weighted relevance of one row whose tokens all matched; context-only tokens weigh less."""
    eid = m["entity_id"].lower()
    obj = eid.split(".")[-1]
    fname = (m.get("friendly_name") or "").lower()
    aliases = [alias.lower() for alias in m.get("_aliases", ())]
    score = 0.0
    for t in access_keys:
        weight = math.log(1 + n / (1 + df.get(t, 0)))
        if t not in doc:
            score += weight * _SEARCH_CONTEXT_WEIGHT
            continue
        score += weight
        if t == eid or t == fname or t in aliases:
            score += 5.0
        elif obj.startswith(t) or any(w.startswith(t) for name in (fname, *aliases) for w in name.split()):
            score += 2.0
    if full in (eid, fname, obj, *aliases):
        score += 10.0
    return score


def _fuzzy_search_matches(matches: list[dict], query: str) -> list[dict]:
    """Typo-tolerant fallback rank, run only when exact matching found nothing.

    Keeps candidates whose best difflib ratio against entity_id, object_id or friendly_name reaches
    SEARCH_FUZZY_MATCH_CUTOFF, best first.
    """
    scored: list[tuple[float, str, dict]] = []
    for m in matches:
        eid = m["entity_id"].lower()
        obj = eid.split(".")[-1]
        fname = (m.get("friendly_name") or "").lower()
        ratio = max(
            difflib.SequenceMatcher(None, query, eid).ratio(),
            difflib.SequenceMatcher(None, query, obj).ratio(),
            difflib.SequenceMatcher(None, query, fname).ratio() if fname else 0.0,
            *(difflib.SequenceMatcher(None, query, alias.lower()).ratio() for alias in m.get("_aliases", ())),
        )
        if ratio >= SEARCH_FUZZY_MATCH_CUTOFF:
            scored.append((ratio, m["entity_id"], m))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [m for _, _, m in scored]


@dataclasses.dataclass(frozen=True)
class _SearchFilters:
    """Normalized search_entities arguments.

    Parsing is separate from filtering because every field is defensive against a
    caller that ignores the schema (a comma-joined domain string, a numeric string,
    an empty string meaning "unset"), which keeps the filter loop reading as policy.
    """

    query: str
    domains: set[str] | None
    device_classes: set[str] | None
    integrations: set[str] | None
    radios: set[str] | None
    power: str | None
    state: Any
    area: str
    has_area: bool | None
    want_unavailable: bool
    stale_threshold: float | None
    limit: int
    offset: int
    registry_state: str


def _entity_radio_types(entry: Any, cache: dict[str, set[str]], hass: HomeAssistant) -> set[str]:
    """The radio types of an entity's device, or of its main parent for a child device."""
    device_id = getattr(entry, "device_id", None)
    if not device_id:
        return set()
    if device_id not in cache:
        registry = dr.async_get(hass)
        found: set[str] = set()
        for chain_id in device_authority_chain(hass, device_id) or (device_id,):
            device = registry.async_get(chain_id)
            if device is not None:
                found |= device_radio_types(device)
        cache[device_id] = found
    return cache[device_id]


def _device_reports_battery(entry: Any, cache: dict[str, bool], access_key: AccessKeyRecord,
                            hass: HomeAssistant, expose: Any) -> bool:
    """Whether an entity's device, or its main parent, runs on a battery this key can read about.

    Only a diagnostic battery entity counts. One the key cannot read never counts, so the filter
    cannot reveal it exists.
    """
    # Battery entities are the only power evidence HA records. Integrations mark a battery that
    # keeps the device running as diagnostic, while a car's or home storage system's battery is the
    # product's main reading.
    device_id = getattr(entry, "device_id", None)
    if not device_id:
        return False
    if device_id not in cache:
        registry = er.async_get(hass)
        found = False
        for chain_id in device_authority_chain(hass, device_id) or (device_id,):
            for sibling in er.async_entries_for_device(registry, chain_id, include_disabled_entities=True):
                state = hass.states.get(sibling.entity_id)
                device_class = (sibling.device_class or sibling.original_device_class
                                or (state.attributes.get("device_class") if state is not None else None))
                if (device_class == "battery" and sibling.entity_category == er.EntityCategory.DIAGNOSTIC
                        and resolve_registry_access(sibling.entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
                        and (expose is None or expose(sibling.entity_id))):
                    found = True
                    break
            if found:
                break
        cache[device_id] = found
    return cache[device_id]


def _search_names(value: Any) -> set[str] | None:
    """One name or several for a multi-valued filter, None when unset.

    Several values are sometimes sent as one comma-separated string, or as a
    JSON array inside a string, instead of an array.
    """
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except ValueError:
                decoded = None
            if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
                return {item.strip() for item in decoded if item.strip()} or None
        return {part.strip() for part in text.split(",") if part.strip()} or None
    return set(str_list_arg(value)) or None


def _parse_search_args(args: dict) -> _SearchFilters:
    """Coerce raw search_entities arguments into normalized filters."""
    # Cap the query before ranking: _rank_search_matches tokenizes on whitespace
    # and the fuzzy fallback runs difflib (O(n*m)) against every accessible
    # entity, so an unbounded query (up to the request body limit) could stall the
    # event loop. A real query is short; the cap only clips a pathological one.
    query = str(args.get("query") or args.get("name") or "").strip().lower()[:MAX_SEARCH_QUERY_LEN]

    domains = _search_names(args.get("domain"))

    stale_threshold: float | None = None
    stale_hours = args.get("stale_hours")
    if stale_hours is not None:
        try:
            stale_threshold = float(stale_hours)
        except (TypeError, ValueError):
            stale_threshold = None

    try:
        limit = int(args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = int(args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0

    return _SearchFilters(
        query=query,
        domains=domains,
        device_classes=_search_names(args.get("device_class")),
        integrations=_search_names(args.get("integration")),
        radios=_search_names(args.get("radio")),
        power=str(args["power"]).strip().lower() if args.get("power") not in (None, "") else None,
        state=args.get("state") or None,
        area=str(args.get("area") or "").strip().lower(),
        has_area=args.get("has_area"),
        want_unavailable=bool(args.get("unavailable")),
        stale_threshold=stale_threshold,
        limit=max(1, min(limit, SEARCH_ENTITIES_MAX_LIMIT)),
        offset=max(0, offset),
        registry_state=str(args.get("registry_state") or "enabled").strip().lower(),
    )


def _registry_value(value: Any) -> Any:
    """Return an enum's JSON value while preserving ordinary scalars and None."""
    return getattr(value, "value", value)


def _registry_projection(entry: er.RegistryEntry) -> dict[str, Any]:
    """Agent-safe, explicit projection of one entity-registry entry.

    Identity and integration-private fields are deliberately absent. The allowlist stays local, so a
    new RegistryEntry field can never expand the MCP response.
    """
    return {
        "platform": entry.platform,
        "name": entry.name,
        "original_name": entry.original_name,
        "icon": entry.icon,
        "original_icon": entry.original_icon,
        "area_id": entry.area_id,
        "device_id": entry.device_id,
        "device_class": entry.device_class,
        "original_device_class": entry.original_device_class,
        "entity_category": _registry_value(entry.entity_category),
        "disabled_by": _registry_value(entry.disabled_by),
        "hidden_by": _registry_value(entry.hidden_by),
        "labels": sorted(entry.labels),
        "categories": dict(sorted(entry.categories.items())),
        "has_entity_name": entry.has_entity_name,
    }


def _search_candidate_ids(
    registry_state: str, hass: HomeAssistant, registry: er.EntityRegistry
) -> list[str]:
    """Return the candidate pool for one search registry-state selector.

    enabled starts from live states; all adds every registry row; disabled follows the registry flag
    even if a stale State lingers during teardown.
    """
    live_ids = [state.entity_id for state in hass.states.async_all()]
    if registry_state == "enabled":
        return [
            eid for eid in live_ids
            if (entry := registry.async_get(eid)) is None or entry.disabled_by is None
        ]
    if registry_state == "disabled":
        return [
            entry.entity_id for entry in registry.entities.values()
            if entry.disabled_by is not None
        ]

    seen = set(live_ids)
    return [
        *live_ids,
        *(entry.entity_id for entry in registry.entities.values() if entry.entity_id not in seen),
    ]


def _annotate_search_rows(
    rows: list[dict], registry: Any, data: PhoenixData, access_key: AccessKeyRecord, hass: HomeAssistant
) -> None:
    """Add MESA control_mode, entity_category and the operator hint to the returned rows, in place.

    Runs on the capped page to bound cost. control_mode is omitted when autonomous, the others when
    unset.
    """
    settings = data.store.get_settings()
    if data.mesa is not None and settings.mesa_mode != MESA_MODE_OFF:
        for row in rows:
            mode = entity_control_mode(data.mesa, access_key, row["entity_id"])
            if mode is not None and mode != "autonomous":
                row["control_mode"] = mode

    # Discovery deliberately keeps config/diagnostic entities VISIBLE (a battery
    # or connectivity question is legitimate), unlike bulk service targeting
    # which excludes them; the tag lets an agent skip noise without losing access.
    for row in rows:
        entry = registry.async_get(row["entity_id"])
        if entry is not None and entry.entity_category is not None:
            row["entity_category"] = entry.entity_category.value

    entity_hints = data.store.get_entity_hints()
    for row in rows:
        if hint := entity_hint_for_agent(access_key, row["entity_id"], hass, entity_hints):
            row["hint"] = hint


def _search_membership(
    entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant,
    expose: Any, budget: list[int],
) -> dict[str, Any]:
    """Project bounded direct membership as advisory, potentially incomplete IDs."""
    state = hass.states.get(entity_id)
    if state is None:
        return {"is_group": None}
    for key in ("group_entities", "entity_id"):
        raw = state.attributes.get(key)
        if not isinstance(raw, (list, tuple, set, frozenset)):
            continue
        if len(raw) > MAX_SEARCH_MEMBERS or len(raw) > budget[0]:
            return {"is_group": None}
        budget[0] -= len(raw)
        if any(isinstance(value, str) and len(value) > MAX_SEARCH_MEMBER_ID_LEN for value in raw):
            return {"is_group": None}
        if any(not isinstance(value, str) or not valid_entity_id(value) for value in raw):
            continue
        members = sorted(set(raw))
        return {
            "is_group": True,
            "member_entity_ids": [
                member for member in members
                if resolve_registry_access(member, access_key, hass) in (Permission.READ, Permission.WRITE)
                and (expose is None or expose(member))
            ],
        }
    return {"is_group": False}


async def _tool_search_entities(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: filter the access key's accessible entities by name/domain/area/etc."""
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "search_entities"

    include_membership = args.get("include_membership", False)
    if not isinstance(include_membership, bool):
        return _tool_error("include_membership must be a boolean."), "invalid_request", "search_entities"
    projection = args.get("projection")
    if projection is not None:
        if not isinstance(projection, dict):
            return _tool_error("projection must be an object."), "invalid_request", "search_entities"
        fields, detailed, error = state_projection(projection)
        if error:
            return _tool_error(error), "invalid_request", "search_entities"

    if args.get("power") not in (None, "") and not isinstance(args.get("power"), str):
        return _tool_error("power must be a string."), "invalid_request", "search_entities"
    for name in ("domain", "device_class", "integration", "radio"):
        raw = args.get(name)
        if raw not in (None, "") and not isinstance(raw, str) and not (
            isinstance(raw, list) and all(isinstance(item, str) for item in raw)
        ):
            # Dropping a malformed filter would silently search everything.
            return _tool_error(f"{name} must be a string or an array of strings."), "invalid_request", "search_entities"
    f = _parse_search_args(args)
    if f.radios is not None and not f.radios <= set(SEARCH_RADIO_TYPES):
        return _tool_error(
            "radio must be one or more of: " + ", ".join(SEARCH_RADIO_TYPES) + "."
        ), "invalid_request", "search_entities"
    if f.power is not None and f.power not in SEARCH_POWER_TYPES:
        return _tool_error(
            "power must be battery. Home Assistant records a battery reading but no mains or other power source."
        ), "invalid_request", "search_entities"
    if f.registry_state not in {"enabled", "disabled", "all"}:
        return _tool_error(
            "registry_state must be one of: enabled, disabled, all."
        ), "invalid_request", "search_entities"

    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    now = utcnow()
    # Only a query reads an entity's area, floor, label and device words.
    context_registries = ((device_registry_snapshot(hass), area_reg, fr.async_get(hass), lr.async_get(hass))
                          if f.query else None)

    expose = assist_expose_check(access_key, hass)
    area_ids = _search_area_ids(hass, f.area) if f.area else None
    matches: list[dict] = []
    # Oldest last_changed among readable candidates: a restart resets them all,
    # which is the horizon a stale_hours search cannot see past.
    oldest_change: datetime | None = None
    radio_cache: dict[str, set[str]] = {}
    battery_cache: dict[str, bool] = {}
    for eid in _search_candidate_ids(f.registry_state, hass, registry):
        perm = resolve_registry_access(eid, access_key, hass)
        if perm not in (Permission.READ, Permission.WRITE):
            continue
        if expose is not None and not expose(eid):
            continue

        state = hass.states.get(eid)
        entry = registry.async_get(eid)
        e = scrub_sensitive_attributes(state, access_key, hass) if state is not None else {
            "entity_id": eid,
            "state": None,
            "attributes": {},
        }
        domain = eid.split(".")[0]
        if f.domains is not None and domain not in f.domains:
            continue
        attrs = e.get("attributes", {})
        fname = str_arg(attrs.get("friendly_name")) or (
            str_arg(entry.name or entry.original_name) if entry is not None else ""
        )
        device_class = attrs.get("device_class") or (
            (entry.device_class or entry.original_device_class) if entry is not None else None
        )
        if f.device_classes is not None and device_class not in f.device_classes:
            continue
        if f.integrations is not None and (entry is None or entry.platform not in f.integrations):
            continue
        if f.radios is not None and not f.radios & _entity_radio_types(entry, radio_cache, hass):
            continue
        if f.power is not None and not _device_reports_battery(entry, battery_cache, access_key, hass, expose):
            continue
        state_val = e.get("state")
        if f.state is not None and state_val != f.state:
            continue
        if f.want_unavailable and state_val not in ("unavailable", "unknown"):
            continue
        area_name = _area_name_for_entity(eid, registry, dev_reg, area_reg)
        if area_ids is not None and _resolve_area_id(entry, dev_reg) not in area_ids:
            continue
        if f.has_area is not None and (_resolve_area_id(entry, dev_reg) is not None) != f.has_area:
            continue
        last_changed = None
        if f.stale_threshold is not None:
            # State.as_dict() (scrub_sensitive_attributes' input) serializes
            # last_changed as an ISO string, not a datetime, so parse it before
            # subtracting.
            last_changed = parse_datetime(e.get("last_changed") or "")
            if last_changed is not None and (oldest_change is None or last_changed < oldest_change):
                oldest_change = last_changed
            if last_changed is None or (now - last_changed).total_seconds() < f.stale_threshold * 3600:
                continue
        row = {
            "entity_id": eid,
            "state": state_val,
            "friendly_name": fname or None,
            "domain": domain,
            "area": area_name,
            "device_class": device_class,
        }
        if last_changed is not None:
            row["last_changed"] = iso_utc(last_changed)
        if entry is not None and (entry.disabled_by is not None or state is None):
            row["registry_state"] = (
                "disabled" if entry.disabled_by is not None else "enabled"
            )
            row["state_available"] = state is not None
        if entry is not None and entry.hidden_by is not None:
            row["hidden"] = True
        composed = composed_name_fields(hass, entry, fname or None)
        row.update(composed)
        aliases = [alias for alias in (getattr(entry, "aliases", None) or ()) if isinstance(alias, str)]
        # The composed name is matched and ranked like another name for the entity.
        aliases.extend(composed.values())
        if aliases:
            row["_aliases"] = aliases
        if context_registries is not None:
            row["_context"] = _search_context(entry, area_name, *context_registries)
        if projection is not None and state is not None:
            row["_scrubbed"] = e
        matches.append(row)

    fuzzy_fallback = False
    if f.query:
        ranked = _rank_search_matches(matches, f.query)
        if not ranked:
            # Exact token-AND matching found nothing; try a typo-tolerant pass so
            # a misspelled query returns the closest entities instead of empty.
            # difflib is O(query*name) per candidate, so run it off the event loop.
            ranked = await hass.async_add_executor_job(_fuzzy_search_matches, matches, f.query)
            fuzzy_fallback = bool(ranked)
        matches = ranked

    # Fuzzy ranking yields; recheck authority and parent visibility before disclosure.
    check_request_authority()
    expose = assist_expose_check(access_key, hass)
    matches = [row for row in matches
               if resolve_registry_access(row["entity_id"], access_key, hass) in (Permission.READ, Permission.WRITE)
               and (expose is None or expose(row["entity_id"]))]
    end = f.offset + f.limit
    has_more = len(matches) > end
    results = matches[f.offset:end]
    # Counted over every match the key can read, like total, so "how many of each
    # kind" needs no paging and reveals nothing total does not.
    domains: dict[str, int] = {}
    for row in matches:
        domain = row["entity_id"].split(".", 1)[0]
        domains[domain] = domains.get(domain, 0) + 1
    by_domain = dict(sorted(domains.items(), key=lambda item: (-item[1], item[0])))
    for row in results:
        row.pop("_aliases", None)
        row.pop("_context", None)
        if "_scrubbed" in row:
            # The returned page carries get_state's projection, so the current
            # values need no follow-up read.
            row.update(_project_state(row.pop("_scrubbed"), fields, detailed))

    _annotate_search_rows(results, registry, data, access_key, hass)
    if include_membership:
        budget = [MAX_SEARCH_MEMBER_CHECKS]
        for row in results:
            row.update(_search_membership(row["entity_id"], access_key, hass, expose, budget))

    def build(count: int) -> dict[str, Any]:
        # A page that would not fit one result ends early and continues where it stopped.
        more = has_more or count < len(results)
        page: dict[str, Any] = {"count": count, "total": len(matches),
                                **({"by_domain": by_domain} if len(by_domain) > 1 else {}),
                                "offset": f.offset, "truncated": more, "has_more": more, "entities": results[:count]}
        if more:
            page["next_offset"] = f.offset + count
        if fuzzy_fallback:
            # Signal that these are approximate (typo-tolerant) matches, not exact,
            # so the agent knows the query did not match any entity literally.
            page["fuzzy_fallback"] = True
        if f.stale_threshold is not None:
            page["oldest_last_changed"] = iso_utc(oldest_change) if oldest_change is not None else None
            if oldest_change is not None and (now - oldest_change).total_seconds() < f.stale_threshold * 3600:
                page["warnings"] = [
                    f"No entity searched has a last_changed before {iso_utc(oldest_change)}. Home Assistant resets "
                    "last_changed when it restarts, so entities unchanged since before then cannot be found this way. "
                    "Use get_history with mode summary to check a longer period."
                ]
        return page

    body = longest_fitting(build, len(results))
    return _tool_success(json.dumps(body, default=str)), "allowed", "search_entities"


async def _tool_get_overview(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: compact home summary scoped to the access key."""
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_overview"

    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    accessible = filter_entities_for_access_key(hass.states.async_all(), access_key, hass)
    by_domain: dict[str, int] = {}
    by_area: dict[str, int] = {}
    unavailable = unknown = 0
    for e in accessible:
        eid = e["entity_id"]
        by_domain[eid.split(".")[0]] = by_domain.get(eid.split(".")[0], 0) + 1
        # unknown is normal for a never-pressed button, a scene or an event, so
        # it is counted apart from entities that are actually unavailable.
        if e.get("state") == "unavailable":
            unavailable += 1
        elif e.get("state") == "unknown":
            unknown += 1
        area_name = _area_name_for_entity(eid, registry, dev_reg, area_reg) or "(no area)"
        by_area[area_name] = by_area.get(area_name, 0) + 1

    body = {
        "total_accessible_entities": len(accessible),
        "unavailable_count": unavailable,
        "unknown_count": unknown,
        "by_domain": dict(sorted(by_domain.items())),
        "by_area": dict(sorted(by_area.items())),
    }
    # Deployment-wide MESA posture (off, advisory or enforced) tells the agent
    # whether to expect confirm/read-only gates, plus the operator-authored
    # prohibited/read-only entities to avoid. The rollup lists ONLY admin-authored
    # profiles (never baseline-derived modes), so it reflects operator intent. Gated
    # on cap_config_read (the profile-data cap) and only when MESA governs.
    if data.mesa is not None:
        mesa_mode = data.store.get_settings().mesa_mode
        body["mesa_mode"] = mesa_mode
        if mesa_mode != MESA_MODE_OFF and effective_cap(access_key, MESA_TOOLS_CAP) != CAP_DENY:
            body["mesa_restrictions"] = authored_restrictions(data.mesa, access_key, hass)
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_overview"


async def _tool_describe_area(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: describe one area and its accessible entities.

    Returns not_found for both a nonexistent area and an area with no accessible
    entities, so there is no existence oracle across the area set.
    """
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "describe_area"

    area_query = str(args.get("area") or args.get("area_id") or "").strip()
    if not area_query:
        return _tool_error("Missing required argument: area"), "invalid_request", "describe_area"

    target = _resolve_area(hass, area_query)

    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    entities_by_domain: dict[str, list[dict]] = {}
    count = 0
    if target is not None:
        for e in filter_entities_for_access_key(hass.states.async_all(), access_key, hass):
            eid = e["entity_id"]
            if _resolve_area_id(registry.async_get(eid), dev_reg) != target.id:
                continue
            entities_by_domain.setdefault(eid.split(".")[0], []).append({
                "entity_id": eid,
                "state": e.get("state"),
                "friendly_name": e.get("attributes", {}).get("friendly_name"),
            })
            count += 1

    if target is None or count == 0:
        return _tool_error("Area not found."), "not_found", area_query

    floor_name = None
    if target.floor_id:
        floor = fr.async_get(hass).async_get_floor(target.floor_id)
        floor_name = floor.name if floor is not None else None

    body = {
        "area_id": target.id,
        "name": target.name,
        "floor_id": target.floor_id,
        "floor_name": floor_name,
        "accessible_entity_count": count,
        "entities_by_domain": entities_by_domain,
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", target.id


def _field_applies(field: dict, state: Any) -> bool:
    """Mirror the Home Assistant frontend's per-entity field filter."""
    rule = field.get("filter")
    if not isinstance(rule, dict) or state is None:
        return True
    features = rule.get("supported_features")
    if isinstance(features, list) and features:
        supported = state.attributes.get("supported_features") or 0
        if not isinstance(supported, int) or not any(isinstance(bit, int) and supported & bit for bit in features):
            return False
    attributes = rule.get("attribute")
    if isinstance(attributes, dict):
        for name, wanted in attributes.items():
            value = state.attributes.get(name)
            values = value if isinstance(value, (list, tuple, set)) else [value]
            if not isinstance(wanted, list) or not any(item in wanted for item in values):
                return False
    return True


def _service_fields(description: Any, state: Any) -> list[dict]:
    """Compact, bounded field schemas for one service, as this entity can use them."""
    raw = description.get("fields") if isinstance(description, dict) else None
    flat: list[tuple[str, dict]] = []
    for key, field in (raw or {}).items():
        if isinstance(field, dict) and isinstance(field.get("fields"), dict):
            flat.extend((child_key, child) for child_key, child in field["fields"].items() if isinstance(child, dict))
        elif isinstance(field, dict):
            flat.append((key, field))
    fields: list[dict] = []
    for key, field in flat:
        if len(fields) >= SERVICE_FIELDS_MAX or not _field_applies(field, state):
            continue
        row: dict[str, Any] = {"field": key}
        if field.get("required"):
            row["required"] = True
        selector = field.get("selector")
        if isinstance(selector, dict) and selector:
            kind, config = next(iter(selector.items()))
            row["type"] = kind
            if isinstance(config, dict):
                row.update({name: config[name] for name in ("min", "max", "step", "unit_of_measurement", "multiple")
                            if name in config})
                options = config.get("options")
                if isinstance(options, list):
                    values = [item.get("value") if isinstance(item, dict) else item for item in options]
                    row["options"] = values[:SERVICE_FIELD_MAX_OPTIONS]
                    if len(values) > SERVICE_FIELD_MAX_OPTIONS:
                        row["options_truncated"] = True
        for name in ("example", "default"):
            if name in field:
                row[name] = field[name]
        description_text = field.get("description")
        if isinstance(description_text, str) and description_text:
            row["description"] = description_text[:SERVICE_FIELD_MAX_DESCRIPTION]
        fields.append(row)
    return fields


async def _tool_find_available_actions(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: which services in an entity's domain this access key can currently invoke.

    Reflects Phoenix scope (write access plus physical and dual gate caps). MESA still enforces at
    call time; control_mode is an advisory hint when MESA is active.
    """
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "find_available_actions"

    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "find_available_actions"
    include_fields = args.get("include_fields", False)
    if not isinstance(include_fields, bool):
        return _tool_error("include_fields must be a boolean."), "invalid_request", "find_available_actions"

    perm = resolve_read(entity_id, access_key, hass)
    if perm not in (Permission.READ, Permission.WRITE):
        # Nonexistent and inaccessible give the same response, so no existence oracle.
        return _tool_error("Entity not found."), "not_found", entity_id
    descriptions: dict = {}
    fields_unavailable = False
    if include_fields:
        try:
            # Local: inside a guard for releases that lack it.
            from homeassistant.helpers.service import async_get_all_descriptions
            descriptions = (await async_get_all_descriptions(hass)).get(entity_id.split(".")[0], {}) or {}
        except Exception:  # descriptions are documentation, never required
            _LOGGER.debug("Service descriptions unavailable for %s", entity_id, exc_info=True)
            fields_unavailable = True
        check_request_authority()
    state = hass.states.get(entity_id)

    domain = entity_id.split(".")[0]
    writable = access_key.pass_through or perm == Permission.WRITE
    physical_ok = effective_cap(access_key, "cap_physical_control") != CAP_DENY
    restart_ok = effective_cap(access_key, "cap_restart") != CAP_DENY
    yaml_edit_ok = effective_cap(access_key, "cap_yaml_edit") != CAP_DENY
    notify_ok = effective_cap(access_key, "cap_notify") != CAP_DENY
    broadcast_ok = effective_cap(access_key, "cap_broadcast") != CAP_DENY

    actions: list[dict] = []
    for svc in sorted(hass.services.async_services().get(domain, {}).keys()):
        key = f"{domain}/{svc}"
        if key not in NO_TARGET_SERVICES and (
                not service_target_binding_valid(domain, svc, [entity_id], hass)
                or await unbound_service_targets(hass, [entity_id], domain=domain, service=svc)):
            continue
        available = writable
        reason: str | None = None
        # No-target reload services gate on cap_yaml_edit, not the entity's WRITE
        # permission, so they are annotated before the writable-based branches.
        if key in NO_TARGET_SERVICES:
            available = yaml_edit_ok
            reason = None if yaml_edit_ok else "requires yaml edit capability"
        elif domain == "calendar" and svc != "get_events":
            available, reason = False, "use dedicated calendar event authoring tools"
        elif not writable:
            reason = "read-only access to this entity"
        elif domain in PHYSICAL_GATE_DOMAINS and not physical_ok:
            available, reason = False, "requires physical control capability"
        elif key in DUAL_GATE_SERVICES and not restart_ok:
            available, reason = False, "requires restart capability"
        elif domain == "notify" and not notify_ok:
            available, reason = False, "requires notifications capability"
        elif key in ANNOUNCEMENT_SERVICES and not broadcast_ok:
            available, reason = False, "requires broadcast capability"
        elif key == "scene/delete" and effective_cap(access_key, "cap_scene_write") == CAP_DENY:
            available, reason = False, "requires scene write capability"
        entry = {"service": f"{domain}.{svc}", "available": available}
        if guidance := RUNTIME_SERVICE_GUIDANCE.get(key):
            entry["description"] = guidance
        elif domain == "script" and svc not in SCRIPT_ENTITY_SERVICES and key not in NO_TARGET_SERVICES:
            entry["description"] = "Run this script and wait for completion; pass inputs directly in service data. Target this exact entity."
        if reason:
            entry["reason"] = reason
        if include_fields and not fields_unavailable:
            entry["fields"] = _service_fields(descriptions.get(svc), state)
        actions.append(entry)

    # An ESPHome device's user-defined actions live in the esphome domain, not the
    # entity's own, so the loop above cannot surface them. They are authorized by
    # the device's write scope, matching _execute_call_service.
    actions.extend(_esphome_actions_for_entity(hass, access_key, entity_id, writable))

    body: dict = {
        "entity_id": entity_id,
        "domain": domain,
        "writable": writable,
        "actions": actions,
    }
    if fields_unavailable:
        body["warnings"] = ["Home Assistant's service descriptions could not be read; fields are omitted."]

    # Purpose-built automation triggers/conditions for this entity, with worked
    # config examples. HA-level authoring data (independent of MESA), so it is
    # exposed for ANY READ-accessible entity, not only profiled ones; this is the
    # authoring-discovery path, distinct from mesa_get_profile's profile-gated
    # MESA context. Best-effort: any HA failure omits the key.
    moments = await async_semantic_moments(hass, entity_id)
    if moments:
        body["semantic_moments"] = moments

    settings = data.store.get_settings()
    if data.mesa is not None and settings.mesa_mode != MESA_MODE_OFF:
        control_mode = entity_control_mode(data.mesa, access_key, entity_id)
        if control_mode is not None:
            body["mesa_control_mode"] = control_mode
            body["mesa_note"] = (
                "MESA enforces this entity's nature at call time; "
                "read_only and prohibited block writes, confirm may require admin approval."
            )

    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id


async def _tool_get_system_health(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: HA version + per-integration system health info."""
    if effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_system_health"

    integrations: dict = {}
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components import system_health
        integrations = await system_health.get_info(hass)
    except Exception:  # system_health may be unavailable
        integrations = {}

    # Per-integration health values are arbitrary and can carry embedded tokens,
    # credentials, URL-embedded secrets, AND network topology (LAN IPs, hostnames in
    # URLs, filesystem paths). The keys are integration-defined, so an allowlist does
    # not apply; redact_diagnostics scrubs secret-keyed and credential values and
    # the topology that get_config also withholds, preserving the diagnostic shape.
    body = {
        "home_assistant_version": ha_version,
        "integrations": _redact_diagnostics(integrations),
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_system_health"


_REPAIR_SEVERITIES = frozenset({"critical", "error", "warning"})
_MAX_REPAIR_ISSUES = 50


def _repair_ref(access_key: AccessKeyRecord, domain: str, issue_id: str) -> str:
    """Return a stable per-access-key reference without disclosing HA's raw issue id."""
    digest = hmac.new(
        access_key.access_key_hash.encode(),
        f"{domain}\0{issue_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"repair_{digest}"


def _safe_repair_text(value: Any, access_key: AccessKeyRecord, hass: HomeAssistant) -> Any:
    """Scrub diagnostic text and any entity IDs embedded within it."""
    scrubbed = _redact_diagnostics(value)
    if not isinstance(scrubbed, str):
        return scrubbed
    return redact_inaccessible_entity_ids_in_text(
        scrubbed, access_key, hass, sentinel="<redacted>"
    )


async def _tool_get_repairs(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: bounded, privacy-scrubbed Home Assistant repair issues."""
    if effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_repairs"

    domain = args.get("domain")
    if domain is not None and (
        not isinstance(domain, str) or not domain.strip() or len(domain) > 64
    ):
        return (
            _tool_error("domain must be a non-empty string of at most 64 characters."),
            "invalid_request",
            "get_repairs",
        )
    domain = domain.strip().lower() if isinstance(domain, str) else None
    severity = args.get("severity")
    if severity is not None and (
        not isinstance(severity, str) or severity not in _REPAIR_SEVERITIES
    ):
        return (
            _tool_error("severity must be critical, error, or warning."),
            "invalid_request",
            "get_repairs",
        )
    include_ignored = args.get("include_ignored", False)
    if not isinstance(include_ignored, bool):
        return (
            _tool_error("include_ignored must be a boolean."),
            "invalid_request",
            "get_repairs",
        )
    limit = args.get("limit", _MAX_REPAIR_ISSUES)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_REPAIR_ISSUES:
        return (
            _tool_error(f"limit must be an integer from 1 to {_MAX_REPAIR_ISSUES}."),
            "invalid_request",
            "get_repairs",
        )

    registry = ir.async_get(hass)
    # HA 2026.11 keeps a disabled config entry's issues and hides them from its Repairs list.
    is_hidden = getattr(registry, "async_is_hidden", lambda _issue: False)
    matches = [
        issue for issue in registry.issues.values()
        if issue.active
        and not is_hidden(issue)
        and (include_ignored or issue.dismissed_version is None)
        and (severity is None or str(issue.severity) == severity)
        and (
            domain is None
            or issue.domain.lower() == domain
            or (isinstance(issue.issue_domain, str) and issue.issue_domain.lower() == domain)
        )
    ]
    severity_rank = {"critical": 0, "error": 1, "warning": 2}
    matches.sort(key=lambda issue: (
        severity_rank.get(str(issue.severity), 3),
        -issue.created.timestamp(),
        issue.domain,
        issue.issue_id,
    ))
    projected: list[dict[str, Any]] = []
    for issue in matches[:limit]:
        placeholders = filter_service_response(
            _redact_diagnostics(issue.translation_placeholders or {}), access_key, hass
        )
        placeholders = {
            key: _safe_repair_text(value, access_key, hass)
            for key, value in placeholders.items()
        }
        projected.append({
            "repair_ref": _repair_ref(access_key, issue.domain, issue.issue_id),
            "domain": _safe_repair_text(issue.domain, access_key, hass),
            "issue_domain": _safe_repair_text(issue.issue_domain, access_key, hass),
            "severity": str(issue.severity) if issue.severity is not None else None,
            "created": issue.created.isoformat(),
            "ignored": issue.dismissed_version is not None,
            "is_fixable": issue.is_fixable,
            "breaks_in_ha_version": _safe_repair_text(
                issue.breaks_in_ha_version, access_key, hass
            ),
            "translation_key": _safe_repair_text(issue.translation_key, access_key, hass),
            "translation_placeholders": placeholders,
            "has_learn_more": bool(issue.learn_more_url),
        })
    body = {
        "count": len(projected),
        "total": len(matches),
        "has_more": len(matches) > limit,
        # Earlier names for total and has_more, kept for existing callers.
        "matching_count": len(matches),
        "truncated": len(matches) > limit,
        "issues": projected,
        "note": (
            "Repair references are access-key-specific. Open Home Assistant Repairs "
            "to view localized descriptions, links, and repair flows."
        ),
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_repairs"


# ---------------------------------------------------------------------------
# Tool "requires" preconditions (ESPHome availability)
# ---------------------------------------------------------------------------


def _requires_satisfied(tool_def: dict, hass: HomeAssistant) -> bool:
    """Whether a tool's "requires" precondition holds on this system.

    Only computed for the handful of defs that carry the key, so tools/list does
    not pay a dashboard lookup per tool.
    """
    requires = tool_def.get("requires")
    if requires is None:
        return True
    avail = esphome_availability(hass)
    if requires == "esphome_builder":
        return avail.builder
    return avail.integration or avail.builder


def _requires_unavailable_reason(tool_def: dict) -> str:
    """Explain, for get_capability_summary, why a required surface is missing."""
    if tool_def.get("requires") == "esphome_builder":
        return "The ESPHome Device Builder add-on is not available."
    return "The ESPHome integration is not set up."


_CHECK_LOCATION_RE = re.compile(r" at (?P<file>[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}\.ya?ml), line (?P<line>\d{1,7})")
_CHECK_REQUIRED_KEY_RE = re.compile(r"required key '([a-z_][a-z0-9_]{0,63})' not provided")


def _safe_config_relpath(path: str) -> bool:
    """A config-relative YAML path that names nothing beyond its own location."""
    parts = path.split("/")
    return ".." not in parts and "" not in parts and parts[-1] != "secrets.yaml" and not any(
        part.startswith(".") for part in parts)


async def _tool_check_ha_config(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: validate HA config files and return errors/warnings."""
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers import check_config
    if effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "check_ha_config"

    try:
        result = await check_config.async_check_ha_config_file(hass)
    except Exception:  # surface as a tool error, never 500
        _LOGGER.warning("MCP check_config failed", exc_info=True)
        return _tool_error("Config check failed."), "invalid_request", "check_ha_config"

    # File and line only for a key that could open the file itself.
    show_location = effective_cap(access_key, "cap_yaml_edit") != CAP_DENY

    def project(messages: list, label: str) -> list[dict]:
        rows = []
        for e in messages[:MAX_CONFIG_CHECK_MESSAGES]:
            row: dict[str, Any] = {"message": label, "domain": e.domain if isinstance(e.domain, str)
                                   and e.domain in hass.config.components else None}
            text = e.message if isinstance(getattr(e, "message", None), str) else ""
            located = _CHECK_LOCATION_RE.search(text)
            if show_location and located and _safe_config_relpath(located["file"]):
                row["file"], row["line"] = located["file"], int(located["line"])
            # A reason class only: messages can echo the offending value.
            if required := _CHECK_REQUIRED_KEY_RE.search(text):
                row["reason"] = f"required key '{required[1]}' is missing"
            elif "is an invalid option" in text or "extra keys not allowed" in text:
                row["reason"] = "contains an option this integration does not support"
            elif text:
                row["reason"] = "has an invalid value or structure"
            rows.append(row)
        return rows

    errors = project(result.errors, "Configuration validation failed; review the Home Assistant configuration check.")
    warnings = project(result.warnings, "Configuration warning; review the Home Assistant configuration check.")
    body = {"valid": not result.errors, "errors": errors, "warnings": warnings,
            "truncated": len(result.errors) > MAX_CONFIG_CHECK_MESSAGES or len(result.warnings) > MAX_CONFIG_CHECK_MESSAGES}
    return _tool_success(json.dumps(body, default=str)), "allowed", "check_ha_config"


_RELATIONSHIP_SELECTORS = ("entity_id", "device_id", "integration", "area", "label")

_SCOPE_NOT_FOUND = (
    "Nothing accessible matched that scope. Check the selector value, or the entities "
    "it names may be outside this access key's permission tree."
)


@dataclasses.dataclass(frozen=True)
class _Scope:
    """One resolved selector, its value or values, and the accessible entity ids they name."""

    selector: str
    value: str | list[str]
    entity_ids: set[str]
    not_found: list[str]


def _relationship_scope(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> _Scope | tuple[dict, str, str]:
    """Resolve exactly one selector to the accessible entity ids it names.

    A selector naming nothing accessible is byte-identical to one naming nothing, so the refusal is
    not an existence oracle.
    """
    # device/area/label go through mesa.build_expand_target, the one definition of what an indirect
    # reference names. `integration` is not an HA target selector, so it reads the registry's
    # platform field.
    def given_value(raw: Any) -> bool:
        return bool(str_arg(raw).strip()) if not isinstance(raw, list) else bool(raw)

    given = [s for s in _RELATIONSHIP_SELECTORS if given_value(args.get(s))]
    if len(given) != 1:
        return (
            _tool_error(
                "Pass exactly one of: " + ", ".join(_RELATIONSHIP_SELECTORS)
                + ". entity_id asks what references one entity; the others ask the same "
                "question about everything a device, integration, area, or label covers."
            ),
            "invalid_request", "get_relationships",
        )
    selector = given[0]
    raw = args.get(selector)
    # Several values are one scope: every helper type, or every script, in one call.
    values = [str_arg(item).strip() for item in raw] if isinstance(raw, list) else [str_arg(raw).strip()]
    if not all(values) or len(values) > SEARCH_ENTITIES_MAX_LIMIT:
        return _tool_error(
            f"A scope id list takes 1 to {SEARCH_ENTITIES_MAX_LIMIT} non-empty strings."
        ), "invalid_request", "get_relationships"
    value: str | list[str] = values if isinstance(raw, list) else values[0]

    # Each value's candidates, so a list scope can name the values that cover
    # nothing readable, hidden and missing alike, instead of narrowing silently.
    per_value: dict[str, list[str]] = {}
    if selector == "entity_id":
        per_value = {name: [name] for name in values}
    elif selector == "integration":
        for entry in er.async_get(hass).entities.values():
            if entry.platform in values and entry.disabled_by is None:
                per_value.setdefault(entry.platform, []).append(entry.entity_id)
    elif selector == "area":
        for name in values:
            area = _resolve_area(hass, name)
            per_value[name] = build_expand_target(hass)("area_id", area.id) if area is not None else []
    else:
        kind = "device_id" if selector == "device_id" else "label_id"
        per_value = {name: build_expand_target(hass)(kind, name) for name in values}

    scope = {
        eid for candidates in per_value.values() for eid in candidates
        if resolve_read(eid, access_key, hass) in (Permission.READ, Permission.WRITE)
    }
    if not scope:
        # Byte-identical for "does not exist" and "not in your tree"; the
        # single-entity form uses the standard "Entity not found.".
        message = "Entity not found." if selector == "entity_id" else _SCOPE_NOT_FOUND
        return _tool_error(message), "not_found", f"{selector}:{','.join(values)}"
    not_found = [name for name in values if not set(per_value.get(name, ())) & scope]
    return _Scope(selector, value, scope, not_found)


async def _relationship_dashboards(
    hass: HomeAssistant,
) -> tuple[list[tuple[str, str | None, Any]], list[dict]]:
    """(url_path, title, config) per readable dashboard, plus what was not read.

    Every skip is returned for `not_searched`, and a failed list is reported separately: no named
    dashboard was examined.
    """
    # A YAML-mode dashboard is rejected by async_get_lovelace_config and an auto-generated one has
    # no stored config, so skipping either while reporting "searched: dashboard" would be a
    # confident wrong answer.
    out: list[tuple[str, str | None, Any]] = []
    skipped: list[dict] = []
    listed: list[dict] = []
    try:
        result = await async_ws_command(hass, "lovelace/dashboards/list", {})
        listed = [d for d in result if isinstance(d, dict)] if isinstance(result, list) else []
    except WsDispatchError as exc:
        _LOGGER.debug("get_relationships could not list dashboards", exc_info=True)
        skipped.append({"kind": "dashboard",
                        "reason": f"could not list dashboards, so none were searched: {exc}"})
    # None is the default dashboard, which the list command does not report. It
    # is skipped quietly when absent (an auto-generated default has no stored
    # config, and reporting that on every call is noise), whereas a NAMED
    # dashboard that cannot be read is a real hole in the answer.
    for url_path, title in [(None, None)] + [
        (d.get("url_path"), d.get("title")) for d in listed if d.get("url_path")
    ]:
        try:
            config = await async_get_lovelace_config(hass, url_path)
        except WsDispatchError as exc:
            if url_path is not None:
                skipped.append({"kind": "dashboard", "id": url_path,
                                "reason": f"not readable (YAML-mode dashboards cannot be searched): {exc}"})
            continue
        if isinstance(config, dict):
            out.append((url_path or "lovelace", title, config))
        elif url_path is not None:
            skipped.append({"kind": "dashboard", "id": url_path,
                            "reason": "no stored configuration to search (auto-generated)"})
    return out, skipped


def _relationship_config_entries(hass: HomeAssistant) -> list[dict]:
    """Snapshot config-entry payloads on the event loop for the executor to walk.

    Entries hold credentials: only in-scope entity ids leave `payload`. The title becomes a consumer
    name, so it gets list_integrations' scrub.
    """
    return [
        {
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            "title": _redact_diagnostics(entry.title),
            "payload": {"data": dict(entry.data), "options": dict(entry.options)},
        }
        for entry in hass.config_entries.async_entries()
    ]


async def _registry_relationship_preview(
    hass: HomeAssistant, entity_id: str
) -> dict[str, Any]:
    """Full known-consumer preview for a registry rename or deletion, for an admin approval card."""
    # An approval card is not capability-narrowed: it always attempts dashboards and config entries
    # as well as YAML consumers. Only matching entity IDs reach it; config-entry credentials stay in
    # the snapshot.
    return await _registry_relationships_preview(hass, [entity_id])


async def _registry_relationships_preview(
    hass: HomeAssistant, entity_ids: list[str]
) -> dict[str, Any]:
    """Full known-consumer preview for one or more registry entities."""
    graph_hits, graph_gaps, graph_read = await _relationship_graph_hits(
        hass, set(entity_ids),
    )
    dashboards, skipped = await _relationship_dashboards(hass)
    entries = _relationship_config_entries(hass)
    consumers, _refs, unreadable = await hass.async_add_executor_job(
        _scan_relationships,
        hass,
        set(entity_ids),
        dashboards,
        entries,
    )
    identity_index = ConsumerIdentityIndex(hass)
    identity_index.bind(consumers)
    _merge_graph_hits(hass, consumers, graph_hits)
    incomplete = [*graph_gaps, *skipped, *unreadable, IDENTITY_COVERAGE]
    preview: dict[str, Any] = {
        "consumers": consumers,
        "consumer_count": len(consumers),
        "searched": [
            "automation",
            "script",
            "scene",
            "group",
            *(["person"] if graph_read else []),
            "dashboard",
            "config_entry",
        ],
    }
    if incomplete:
        preview["not_searched"] = incomplete
    return preview


async def _tool_get_relationships(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: what consumes the entities a selector names, grouped by consumer.

    A consumer with its in-scope entities and roles is the edit work list. `not_searched` names what
    this key's capabilities left out, so a narrow answer is never a confident no.
    """
    args, error = normalize_tool_args("get_relationships", args)
    if error:
        return _tool_error(error), "invalid_request", "get_relationships"
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_relationships"

    resolved = _relationship_scope(args, access_key, hass)
    if isinstance(resolved, tuple):
        return resolved
    selector, value, scope = resolved.selector, resolved.value, resolved.entity_ids
    scope_not_found = resolved.not_found if isinstance(value, list) else None

    searched = ["automation", "script", "scene", "group"]
    not_searched: list[dict] = []
    graph_hits, graph_gaps, graph_read = await _relationship_graph_hits(hass, scope)
    not_searched.extend(graph_gaps)
    if graph_read:
        searched.append("person")
    dashboards: list[tuple[str, str | None, Any]] = []
    entries: list[dict] = []
    if effective_cap(access_key, "cap_lovelace_write") == CAP_DENY:
        not_searched.append({"kind": "dashboard", "reason": "requires cap_lovelace_write"})
    else:
        dashboards, skipped = await _relationship_dashboards(hass)
        not_searched.extend(skipped)
        searched.append("dashboard")
    if effective_cap(access_key, "cap_integration_write") == CAP_DENY:
        not_searched.append({"kind": "config_entry", "reason": "requires cap_integration_write"})
    else:
        entries = _relationship_config_entries(hass)
        searched.append("config_entry")

    consumers, refs, unreadable = await hass.async_add_executor_job(
        _scan_relationships, hass, scope, dashboards, entries,
    )
    identity_index = ConsumerIdentityIndex(hass)
    identity_index.bind(consumers)
    _merge_graph_hits(hass, consumers, graph_hits)
    # A YAML branch this cannot read is the same class of gap as a capability it
    # does not hold, so it lands in the same field.
    not_searched.extend([*unreadable, IDENTITY_COVERAGE])
    consumers = [c for c in consumers if _consumer_visible(c, access_key, hass, identity_index)]
    refs = {eid: visible for eid, holders in refs.items()
            if (visible := [h for h in identity_index.bind(holders) if _consumer_visible(h, access_key, hass, identity_index)])}
    # Missing targets are evidence from readable consumer configuration only.
    # Hidden holders cannot reveal their identities or unrelated broken references.
    registry = er.async_get(hass)
    dangling = [
        _dangling_report(eid, holders)
        for eid, holders in sorted(_dangling_candidates(refs, hass).items())
        if hass.states.get(eid) is None and registry.async_get(eid) is None
    ]

    # What no searched consumer touches: removal candidates, which not_searched qualifies.
    touched = {item["entity_id"] for consumer in consumers for item in consumer["entities"]}

    def build(count: int) -> dict[str, Any]:
        # Consumers are the work list, so they fill the result before dangling references.
        listed, dead = consumers[:count], dangling[:max(0, count - len(consumers))]
        body: dict[str, Any] = {
            "scope": {"selector": selector, "value": value,
                      "entity_ids": sorted(scope), "count": len(scope),
                      **({"not_found": scope_not_found} if scope_not_found is not None else {})},
            "consumers": listed,
            "consumer_count": len(consumers),
            "unreferenced": sorted(scope - touched),
            "dangling_references": dead,
            "searched": searched,
        }
        if len(listed) < len(consumers):
            body["consumers_not_listed"] = len(consumers) - len(listed)
        if len(dead) < len(dangling):
            body["dangling_not_listed"] = len(dangling) - len(dead)
        if len(listed) < len(consumers) or len(dead) < len(dangling):
            body["warnings"] = ["The result is full: narrow the scope to list the rest."]
        if not_searched:
            body["not_searched"] = not_searched
        return body

    body = longest_fitting(build, len(consumers) + len(dangling))
    if selector == "entity_id" and isinstance(value, str):
        # Only meaningful for a single automation or script: what IT references.
        references, references_not_searched = await hass.async_add_executor_job(
            _forward_references_details, hass, value,
        )
        # The worker returns every reference; scope, including Assist exposure,
        # is decided here on the loop, where HA's exposure lookup may run.
        body["references"] = [
            eid for eid in references
            if resolve_read(eid, access_key, hass) in (Permission.READ, Permission.WRITE)
        ]
        if references_not_searched:
            body["references_not_searched"] = references_not_searched
    label = ",".join(value) if isinstance(value, list) else value
    return _tool_success(json.dumps(body, default=str)), "allowed", f"{selector}:{label}"


async def _tool_describe_entity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: comprehensive summary of one accessible entity."""
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "describe_entity"

    entity_id = str_arg(args.get("entity_id"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "describe_entity"

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is not None:
        entity_id = entry.entity_id

    perm = resolve_read(entity_id, access_key, hass, registry=True)
    if perm not in (Permission.READ, Permission.WRITE):
        return _tool_error("Entity not found."), "not_found", entity_id
    state = hass.states.get(entity_id)

    domain = entity_id.split(".")[0]
    scrubbed = scrub_sensitive_attributes(state, access_key, hass) if state is not None else {
        "state": None,
        "attributes": {},
    }
    referenced_by, referenced_by_not_searched = await hass.async_add_executor_job(
        _references_for_entity_details, hass, entity_id,
    )
    identity_index = ConsumerIdentityIndex(hass)
    referenced_by = [c for c in identity_index.bind(referenced_by) if _consumer_visible(c, access_key, hass, identity_index)]
    referenced_by_not_searched.append(IDENTITY_COVERAGE)
    body: dict = {
        "entity_id": entity_id,
        "domain": domain,
        "state": scrubbed.get("state"),
        "attributes": scrubbed.get("attributes"),
        "area": _area_name_for_entity(entity_id, registry, dr.async_get(hass), ar.async_get(hass)),
        "writable": access_key.pass_through or perm == Permission.WRITE,
        "domain_services": sorted(hass.services.async_services().get(domain, {}).keys()),
        "referenced_by": referenced_by,
        # This field covers automations, scripts and scenes only; get_relationships
        # also reaches dashboards and config entries, at a WS round trip per
        # dashboard that a one-entity summary should not pay. The note keeps an
        # empty or short list from being read as PROOF nothing uses the entity.
        "referenced_by_note": (
            "Readable automations, scripts and scenes only. Hidden consumers are omitted. Call get_relationships for the "
            "full picture, including dashboards and config entries such as helpers "
            "built on this entity, and for entity IDs that no longer resolve."
        ),
    }
    if referenced_by_not_searched:
        body["referenced_by_not_searched"] = referenced_by_not_searched
    if domain == "radio_frequency" and (rf := rf_capabilities(hass, entity_id)) is not None:
        body["radio_frequency"] = rf

    # entity_category (config/diagnostic) when set: tells the agent this is a
    # setup/health entity, not a primary control. Bulk device/area service calls
    # exclude these; describe_entity keeps them visible and labels them.
    entry = registry.async_get(entity_id)
    if entry is not None and entry.entity_category is not None:
        body["entity_category"] = entry.entity_category.value

    if entry is not None:
        body.update(composed_name_fields(hass, entry, str_arg((scrubbed.get("attributes") or {}).get("friendly_name")) or None))
        body["registry"] = {
            **_registry_projection(entry),
            "name_source": entity_name_source(hass, entry),
        }

    if (defined_by := await _entity_definition(entry, state, access_key, hass)) is not None:
        body["defined_by"] = defined_by

    # The spoken names HA matches this entity by, RESOLVED: the entity's own name
    # appears as the name itself, not as the registry sentinel that stands for it,
    # because a caller compares this list against a failing voice command.
    # set_entity's add_aliases / remove_aliases edit the same list. An empty list
    # is meaningful: an entity with no names is not addressable by voice.
    if entry is not None:
        body["aliases"] = er.async_get_entity_aliases(hass, entry)

    if hint := entity_hint_for_agent(access_key, entity_id, hass, data.store.get_entity_hints()):
        body["hint"] = hint

    settings = data.store.get_settings()
    if data.mesa is not None and settings.mesa_mode != MESA_MODE_OFF:
        control_mode = entity_control_mode(data.mesa, access_key, entity_id)
        if control_mode is not None:
            body["mesa_control_mode"] = control_mode
            body["mesa_note"] = (
                "Call mesa_get_profile (requires cap_config_read) for the full semantic profile."
            )

    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id


async def _entity_definition(
    entry: er.RegistryEntry | None, state: Any, access_key: AccessKeyRecord, hass: HomeAssistant,
) -> dict[str, Any] | None:
    """Where an entity is defined, and the tool that reads that definition.

    A helper's entry id goes only to a key that can list helpers, and a tool is named only when this
    key may call it. An entity with no registry entry is left undescribed.
    """
    # A Template helper and a YAML template look alike from the registry, so without this a caller
    # searches YAML for a helper created in the UI.
    if entry is None:
        return None

    def tool(name: str, cap: str) -> dict[str, str]:
        return {"read_with": name} if effective_cap(access_key, cap) != CAP_DENY else {}

    can_list = effective_cap(access_key, "cap_registry_read") != CAP_DENY
    if entry.config_entry_id:
        helper = await _helper_config_entry(hass, entry.config_entry_id)
        if helper is None:
            config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
            return {"kind": "integration", "integration": config_entry.domain if config_entry else entry.platform}
        return {"kind": "ui_helper", "helper_type": helper.domain, "title": helper.title,
                **({"entry_id": helper.entry_id} if can_list else {}), **tool("get_helper_settings", "cap_helper_write")}
    attributes = getattr(state, "attributes", {}) or {}
    # HA marks a storage helper editable and a YAML one not; tags exist only in
    # storage and carry no such flag.
    stored = attributes.get("editable") is True or entry.domain == "tag"
    if entry.domain in STORAGE_HELPER_TYPES and entry.platform == entry.domain and stored:
        return {"kind": "storage_helper", "helper_type": entry.domain,
                **({"helper_id": entry.unique_id} if can_list else {}), **tool("list_helpers", "cap_registry_read")}
    # Automations, scripts and scenes live in YAML too, but each has its own reader.
    reader = _YAML_READERS.get(entry.domain, ("get_yaml_config", "cap_yaml_edit"))
    return {"kind": "yaml", **tool(*reader)}


_YAML_READERS = {
    "automation": ("get_automation", "cap_automation_write"),
    "script": ("get_script", "cap_script_write"),
    "scene": ("get_scene", "cap_scene_write"),
}


async def _tool_get_audit_summary(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: the access key's own recent audit entries. No cap required; own data only."""
    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    outcome = args.get("outcome")
    entries = data.audit.query(access_key_id=access_key.id, outcome=outcome, limit=limit)
    if entries is None:
        return _tool_error("Invalid outcome filter."), "invalid_request", "get_audit_summary"

    items = []
    for e in entries:
        item: dict[str, Any] = {
            "request_id": e.request_id,
            "timestamp": e.timestamp.isoformat(),
            "method": e.method,
            "resource": e.resource,
            "outcome": e.outcome,
        }
        if e.mesa_advisory:
            item["mesa_advisory"] = True
        items.append(item)
    body = {"access_key_name": access_key.name, "count": len(items), "entries": items}
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_audit_summary"


# ---------------------------------------------------------------------------
# Analysis tools (whatif / compare_states / recent_activity / dry_run / validate)
# ---------------------------------------------------------------------------


def _state_matches(constraint: Any, value: Any) -> bool | None:
    """True/False if value matches a state-trigger constraint, None if no constraint."""
    if constraint is None:
        return None
    if isinstance(constraint, list):
        return value in [str(c) for c in constraint]
    return str(constraint) == value


def _whatif_trigger(trig: dict, current_state: str | None, hypothetical: str) -> bool | str:
    """Best-effort: would this trigger fire if its entity became `hypothetical`?

    Evaluates state and numeric_state platforms; returns "unknown" for triggers
    that cannot be judged from a single state change (template, time, event, etc.).
    """
    platform = trig.get("platform") or trig.get("trigger")
    if platform == "state":
        if _state_matches(trig.get("from"), current_state) is False:
            return False
        if _state_matches(trig.get("not_from"), current_state) is True:
            return False
        to_match = _state_matches(trig.get("to"), hypothetical)
        if to_match is False:
            return False
        if _state_matches(trig.get("not_to"), hypothetical) is True:
            return False
        if to_match is True:
            return True
        if trig.get("to") is None and trig.get("not_to") is None:
            return hypothetical != current_state  # "any change" trigger
        return True
    if platform == "numeric_state":
        try:
            val = float(hypothetical)
        except (TypeError, ValueError):
            return "unknown"
        try:
            if trig.get("above") is not None and not val > float(trig["above"]):
                return False
            if trig.get("below") is not None and not val < float(trig["below"]):
                return False
        except (TypeError, ValueError):
            return "unknown"
        return True
    return "unknown"


async def _tool_whatif(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: which automations would fire if an entity changed to a hypothetical state."""
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "whatif"

    entity_id = str_arg(args.get("entity_id"))
    hypothetical = args.get("hypothetical_state")
    if not entity_id or hypothetical is None:
        return _tool_error("Missing required arguments: entity_id and hypothetical_state"), "invalid_request", "whatif"
    hypothetical = str(hypothetical)
    if resolve_read(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
        return _tool_error("Entity not found."), "not_found", entity_id

    current = hass.states.get(entity_id)
    current_state = current.state if current is not None else None

    automations, unreadable = await hass.async_add_executor_job(
        _domain_entries, hass, "automation"
    )
    check_request_authority()

    candidates: list[dict] = []
    expand = build_expand_target(hass)
    identity_index = ConsumerIdentityIndex(hass)
    for cfg in automations:
        if not isinstance(cfg, dict):
            continue
        consumer = {"kind": "automation", "id": str(cfg.get("id", "")),
                    **({"_identity_config": cfg} if not cfg.get("id") else {})}
        identity_index.bind([consumer])
        if not _consumer_visible(consumer, access_key, hass, identity_index):
            continue
        triggers = cfg.get("trigger") or cfg.get("triggers") or []
        if isinstance(triggers, dict):
            triggers = [triggers]
        matched: list[dict] = []
        for trig in triggers:
            if not isinstance(trig, dict):
                continue
            tents: set[str] = set()
            _collect_entity_id_values(trig, tents)
            if entity_id not in tents:
                # A device/area/floor/label reference (device triggers, target
                # blocks) can still cover this entity: reuse the canonical walk
                # on a one-trigger config with the expand_target callback. Such
                # coverage cannot be simulated (device trigger types are
                # integration-specific), so it reports would_fire "unknown".
                covered = entities_by_role({"trigger": [trig]}, expand)["trigger"]
                if entity_id not in covered:
                    continue
                matched.append({
                    "platform": trig.get("platform") or trig.get("trigger"),
                    "would_fire": "unknown",
                })
                continue
            matched.append({
                "platform": trig.get("platform") or trig.get("trigger"),
                "would_fire": _whatif_trigger(trig, current_state, hypothetical),
            })
        if not matched:
            continue
        if any(m["would_fire"] is True for m in matched):
            verdict: bool | str = True
        elif any(m["would_fire"] == "unknown" for m in matched):
            verdict = "unknown"
        else:
            verdict = False
        candidates.append({
            "automation_id": consumer["id"],
            **({"entity_id": consumer["entity_id"]} if "entity_id" in consumer else {}),
            "name": cfg.get("alias"),
            "would_fire": verdict,
            "triggers": matched,
        })

    body = {
        "entity_id": entity_id,
        "current_state": current_state,
        "hypothetical_state": hypothetical,
        "candidates": candidates,
        "not_searched": [*unreadable, IDENTITY_COVERAGE],
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", entity_id


async def _history_states(hass: HomeAssistant, start: Any, end: Any, entity_ids: list[str], *, include_start: bool = True) -> dict:
    """Recorder significant-states for a set of entities, no attributes."""
    # Local: optional component, imported only when this path runs.
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder import history as rec_history
    fn = functools.partial(
        rec_history.get_significant_states,
        hass, start, end, entity_ids, None, include_start, True, False, True,
    )
    return await get_instance(hass).async_add_executor_job(fn)


async def _history_states_at(hass: HomeAssistant, at: Any, entity_ids: list[str]) -> dict:
    """Return each entity's latest significant state at or before one time."""
    query_start = at + timedelta.resolution
    return await _history_states(
        hass, query_start, query_start + timedelta.resolution, entity_ids
    )


async def _tool_compare_states(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: compare accessible entity states between two times."""
    args, error = normalize_tool_args("compare_states", args)
    if error:
        return _tool_error(error), "invalid_request", "compare_states"
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "compare_states"

    ids = str_list_arg(args.get("entity_id"))
    if not ids:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "compare_states"
    if len(ids) > 100:
        return _tool_error("entity_id accepts at most 100 entities."), "invalid_request", "compare_states"
    accessible = [e for e in ids if resolve_read(e, access_key, hass) in (Permission.READ, Permission.WRITE)]
    if not args.get("t1"):
        return _tool_error("Missing required argument: t1"), "invalid_request", "compare_states"
    try:
        t1 = as_utc(_parse_time_param(args["t1"]))
    except ValueError as err:
        return _tool_error(f"Invalid t1 format. {err}"), "invalid_request", "compare_states"
    t2 = utcnow()
    if args.get("t2"):
        try:
            t2 = as_utc(_parse_time_param(args["t2"]))
        except ValueError as err:
            return _tool_error(f"Invalid t2 format. {err}"), "invalid_request", "compare_states"
    if t1 >= t2:
        return _tool_error(
            "t1 must be earlier than t2. A relative time such as 7d means 7 days ago."
        ), "invalid_request", "compare_states"

    comparisons: list[dict] = []
    if accessible:
        try:
            at_t1, at_t2 = await asyncio.gather(
                _history_states_at(hass, t1, accessible),
                _history_states_at(hass, t2, accessible),
            )
        except Exception:
            _LOGGER.warning("compare_state history failed", exc_info=True)
            return _tool_error("History call failed."), "invalid_request", "compare_states"
        for eid in accessible:
            dicts1 = [s.as_dict() if hasattr(s, "as_dict") else s for s in at_t1.get(eid, [])]
            dicts2 = [s.as_dict() if hasattr(s, "as_dict") else s for s in at_t2.get(eid, [])]
            s1 = dicts1[-1].get("state") if dicts1 else None
            s2 = dicts2[-1].get("state") if dicts2 else None
            # Two snapshots only; with either side unrecorded the answer is unknown, not a change.
            changed = None if s1 is None or s2 is None else s1 != s2
            comparisons.append({"entity_id": eid, "state_at_t1": s1, "state_at_t2": s2, "changed": changed})

    # Named like get_state's list: hidden and missing alike, never silently dropped.
    body = {"t1": t1.isoformat(), "t2": t2.isoformat(), "comparisons": comparisons,
            "not_found": [eid for eid in ids if eid not in accessible]}
    return _tool_success(json.dumps(body, default=str)), "allowed", "compare_states"


_SCALAR_TYPES = (str, int, float, bool, type(None))


def _scalar_list(value: Any) -> list | None:
    """The value as a list of scalars, or None when it is not one.

    A list of mappings is reported by type, since differencing yields two near-identical blobs.
    """
    if not isinstance(value, (list, tuple)):
        return None
    items = list(value)
    return items if all(isinstance(v, _SCALAR_TYPES) for v in items) else None


def _bounded_value(value: Any) -> Any:
    """One attribute value, bounded and JSON-safe.

    Scalars and short scalar lists pass through. A long string is clipped and a larger structure
    becomes a type marker: the question is interchangeability, not content.
    """
    if isinstance(value, str) and len(value) > MAX_COMPARE_VALUE_CHARS:
        return value[:MAX_COMPARE_VALUE_CHARS] + f"... ({len(value) - MAX_COMPARE_VALUE_CHARS} more characters)"
    if isinstance(value, _SCALAR_TYPES):
        return value
    scalars = _scalar_list(value)
    if scalars is not None and len(scalars) <= MAX_COMPARE_LIST_VALUES:
        return scalars
    size = len(value) if isinstance(value, (list, tuple, dict)) else None
    marker = type(value).__name__ if size is None else f"{type(value).__name__} with {size} entries"
    return {"omitted": marker}


def _changed_attribute(name: str, value: Any, other: Any) -> dict:
    """One entry in the `changed` array, differenced by value shape.

    Two scalar lists difference member-wise, so a renamed option reads as a removal beside an
    addition. Everything else reports both values, so a narrowed range shows as a pair.
    """
    entry: dict[str, Any] = {"attribute": name}
    values = _scalar_list(value)
    others = _scalar_list(other)
    if values is None or others is None:
        entry["value"] = _bounded_value(value)
        entry["compare_value"] = _bounded_value(other)
        return entry
    removed = [v for v in values if v not in others]
    added = [v for v in others if v not in values]
    entry["removed"] = removed[:MAX_COMPARE_LIST_VALUES]
    entry["removed_total"] = len(removed)
    entry["added"] = added[:MAX_COMPARE_LIST_VALUES]
    entry["added_total"] = len(added)
    entry["truncated"] = (
        len(removed) > MAX_COMPARE_LIST_VALUES or len(added) > MAX_COMPARE_LIST_VALUES
    )
    if not removed and not added:
        # Same members in a different order. The flag keeps an entry with two
        # empty arrays from reading as a difference the caller failed to
        # understand, when it is one they can almost always ignore.
        entry["reordered"] = True
    return entry


_UNPUBLISHED_WARNING = (
    "{entity_id} is {state}, so it is publishing few or none of its attributes. "
    "The differences below describe what it reports right now, not what it "
    "supports; do not read an attribute as absent until it is available again."
)


def _publishes_nothing(state: dict) -> bool:
    """Whether this entity's attribute set is too degraded to compare against."""
    # An offline entity answers every read, so the comparison would report the other side's
    # attributes as "missing", which reads like a description of the replacement. `restored` is HA's
    # own marker for a state rebuilt from the registry because the integration supplied none.
    # `unknown` is excluded: an entity with no value yet still publishes its attributes.
    return state.get("state") == "unavailable" or (state.get("attributes") or {}).get("restored") is True


async def _tool_compare_entities(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: what differs between two accessible entities' current shapes."""
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "compare_entities"

    entity_id = str_arg(args.get("entity_id"))
    compare_to = str_arg(args.get("compare_to"))
    if not entity_id:
        return _tool_error("Missing required argument: entity_id"), "invalid_request", "compare_entities"
    if not compare_to:
        return _tool_error("Missing required argument: compare_to"), "invalid_request", "compare_entities"

    states = []
    for eid in (entity_id, compare_to):
        # Both sides answer with the same body whether the entity is absent or
        # merely out of scope, and the resource names the tool rather than the
        # id, so a refusal is not an existence oracle.
        not_found = _tool_error(f"Entity not found: {json.dumps(eid[:80])}."), "not_found", "compare_entities"
        if resolve_read(eid, access_key, hass) not in (Permission.READ, Permission.WRITE):
            return not_found
        state = hass.states.get(eid)
        if state is None:
            return not_found
        states.append(scrub_sensitive_attributes(state, access_key, hass))

    # Scrubbing runs before the diff, so an attribute Phoenix strips from a
    # state read can never be reintroduced here as a difference between two.
    attrs, compare_attrs = (s.get("attributes") or {} for s in states)
    warnings = [
        _UNPUBLISHED_WARNING.format(entity_id=eid, state=state.get("state"))
        for eid, state in ((entity_id, states[0]), (compare_to, states[1]))
        if _publishes_nothing(state)
    ]
    missing = sorted(set(attrs) - set(compare_attrs))
    added = sorted(set(compare_attrs) - set(attrs))
    shared = sorted(set(attrs) & set(compare_attrs))
    changed = [
        _changed_attribute(name, attrs[name], compare_attrs[name])
        for name in shared
        if attrs[name] != compare_attrs[name]
    ]

    body = {
        "entity_id": entity_id,
        "compare_to": compare_to,
        "domain": entity_id.split(".")[0],
        "compare_domain": compare_to.split(".")[0],
        "state": states[0].get("state"),
        "compare_state": states[1].get("state"),
        # Named for the direction that breaks a substitution: something reading
        # entity_id's attribute finds nothing on compare_to. The other two
        # arrays are informational by comparison.
        "missing_in_compare_to": [
            {"attribute": name, "value": _bounded_value(attrs[name])} for name in missing
        ],
        "added_in_compare_to": [
            {"attribute": name, "value": _bounded_value(compare_attrs[name])} for name in added
        ],
        "changed": changed,
        "identical_count": len(shared) - len(changed),
        # Conditional, per the package's convention for a read that degraded but
        # still succeeded: a usually-absent field gets read when it appears, where
        # a permanent "degraded": false trains the reader to skip it.
        **({"warnings": warnings} if warnings else {}),
        "note": (
            "Current attributes only. A value that varies over time (an enum "
            "sensor's state) can differ without appearing here; use get_history "
            "on both entities across the same window to compare those."
        ),
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "compare_entities"


async def _tool_recent_activity(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: which accessible entities changed in the last N minutes."""
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "recent_activity"

    try:
        minutes = int(args.get("minutes", 30))
    except (TypeError, ValueError):
        minutes = 30
    minutes = max(1, min(minutes, 1440))
    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    end = utcnow()
    start = end - timedelta(minutes=minutes)
    accessible_ids = list(_accessible_entity_ids(access_key, hass))
    changes: list[dict] = []
    if accessible_ids:
        try:
            # The state at the window start lets a row that repeats the state, as
            # Home Assistant writes at every restart, be told from a change.
            result = await _history_states(hass, start, end, accessible_ids, include_start=True)
        except Exception:
            _LOGGER.warning("recent_activity history failed", exc_info=True)
            return _tool_error("History call failed."), "invalid_request", "recent_activity"
        for eid, states in result.items():
            dicts = [s.as_dict() if hasattr(s, "as_dict") else s for s in states]
            previous: Any = None
            moments = []
            for index, row in enumerate(dicts):
                stamp = state_timestamp(row)
                # The start row is the state already in effect, not a change; an
                # entity first recorded inside the window does count.
                if (row.get("state") != previous if index else stamp is not None and stamp > start):
                    moments.append(stamp)
                previous = row.get("state")
            if not moments:
                continue
            changes.append({
                "entity_id": eid,
                "state": previous,
                "when": iso_utc(moments[-1]) if moments[-1] is not None else None,
                "changes_in_window": len(moments),
            })
    changes.sort(key=lambda c: str(c["when"] or ""), reverse=True)
    body = {
        "window_minutes": minutes,
        "count": min(len(changes), limit),
        "total": len(changes),
        "has_more": len(changes) > limit,
        "truncated": len(changes) > limit,
        "changes": changes[:limit],
    }
    if len(changes) > limit:
        # This read has no offset: the newest changes lead, so a shorter window reads the rest.
        body["warnings"] = [f"More entities changed than limit {limit}; raise limit or shorten minutes."]
    return _tool_success(json.dumps(body, default=str)), "allowed", "recent_activity"


def _cap_outcome(mode: str) -> str:
    """Map an effective cap mode to a predicted call outcome string."""
    if mode == CAP_DENY:
        return "denied"
    if mode == CAP_CONFIRM:
        return "pending_approval"
    return "allowed"


async def _tool_dry_run_service(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """MCP tool: preview a service call (resolved targets + MESA verdict) without executing."""
    if effective_cap(access_key, "cap_search") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "dry_run_service"
    args, error = normalize_tool_args("dry_run_service", args)
    if error:
        return _tool_error(error), "invalid_request", "dry_run_service"

    domain = str_arg(args.get("domain"))
    service = str_arg(args.get("service"))
    if not domain or not service:
        return _tool_error("Missing required arguments: domain and service"), "invalid_request", "dry_run_service"
    if (dedicated := dedicated_tool_refusal(domain, service)) is not None:
        return _tool_error(dedicated[1]), "invalid_request", "dry_run_service"
    secondary_targets = await unsupported_secondary_targets(
        hass, args.get("service_data"), domain=domain, service=service
    )
    if secondary_targets:
        return (
            _tool_error(secondary_target_error(secondary_targets)),
            "invalid_request",
            "dry_run_service",
        )
    if templated := template_fields(args.get("service_data")):
        return _tool_error(template_fields_error(templated)), "invalid_request", "dry_run_service"
    # Sanitised exactly as the real call sanitises it, so the preview predicts
    # the call that would actually run rather than the one the caller wrote.
    service_data = _sanitize_service_data(args.get("service_data"))
    service_key = f"{domain}/{service}"

    if service_key in DUAL_GATE_SERVICES:
        system_predicted = _cap_outcome(effective_cap(access_key, "cap_restart"))
        body = {
            "domain": domain, "service": service, "system_service": True,
            "resolved_entities": [],
            "predicted_outcome": system_predicted,
            "would_execute": system_predicted == "allowed",
        }
        return _tool_success(json.dumps(body, default=str)), "allowed", "dry_run_service"

    if service_key in NO_TARGET_SERVICES:
        no_target_predicted = _cap_outcome(effective_cap(access_key, "cap_yaml_edit"))
        body = {
            "domain": domain, "service": service, "system_service": True,
            "resolved_entities": [],
            "predicted_outcome": no_target_predicted,
            "would_execute": no_target_predicted == "allowed",
        }
        return _tool_success(json.dumps(body, default=str)), "allowed", "dry_run_service"

    # Music Assistant library reads: no entity target, authorized by the
    # response capability and Read on an entity of the named entry, as in
    # call_service, where a lone readable entry may be left unnamed.
    if service_key in MUSIC_ASSISTANT_LIBRARY_SERVICES:
        readable = music_assistant_readable_entries(hass, access_key)
        entry_id = service_data.get("config_entry_id")
        allowed = (
            effective_cap(access_key, "cap_service_response") != CAP_DENY
            and not any((args.get("entity_id"), args.get("device_id"), args.get("area_id")))
            and (entry_id in readable if entry_id is not None else len(readable) == 1)
        )
        body = {
            "domain": domain, "service": service, "library_read": True,
            "resolved_entities": [],
            "predicted_outcome": "allowed" if allowed else "denied",
            "would_execute": allowed,
        }
        return _tool_success(json.dumps(body, default=str)), "allowed", "dry_run_service"

    # ESPHome user-defined actions: no entity target, authorized by the owning
    # device's write scope. Reporting the declared argument signature is the
    # point of a dry run here, since the schema is device-defined and a wrong
    # argument name is the likeliest failure.
    if domain == _ESPHOME_DOMAIN:
        esphome_entry = resolve_esphome_user_service(hass, service)
        if esphome_entry is not None:
            allowed = esphome_entry_writable(hass, esphome_entry, access_key)
            signature = _esphome_action_signature(esphome_entry, service)
            declared = {a["name"] for a in signature}
            body = {
                "domain": domain, "service": service, "device_action": True,
                "resolved_entities": [],
                "declared_args": signature,
                "unknown_args": sorted(k for k in service_data if k not in declared),
                "missing_args": sorted(declared - set(service_data)),
                "predicted_outcome": "allowed" if allowed else "denied",
                "would_execute": allowed,
            }
            return _tool_success(json.dumps(body, default=str)), "allowed", "dry_run_service"

    if not any((args.get("entity_id"), args.get("device_id"), args.get("area_id"))):
        return (
            _tool_error("An explicit entity_id, device_id, or area_id target is required."),
            "invalid_request", "dry_run_service",
        )
    # Counts cover only entities the key can see, so a hidden target and a
    # missing one preview identically.
    permitted, requested = resolve_service_targets(
        entity_id=args.get("entity_id"), device_id=args.get("device_id"),
        area_id=args.get("area_id"), service_domain=domain, access_key=access_key, hass=hass,
    )

    if not service_target_binding_valid(domain, service, permitted, hass):
        permitted = []
    unbound = await unbound_service_targets(hass, permitted, domain=domain, service=service)
    if unbound:
        permitted = []

    # Predict the outcome in the same order call_service applies its gates: the
    # physical-control cap gate first (before target resolution and MESA), then an
    # empty resolution denies, then MESA (a confirm becomes pending, nothing
    # allowed becomes a deny, else allow). A confirm at any layer surfaces as
    # pending. The predicate is the enforcing path's own, not a copy, so the
    # preview cannot drift from the call it predicts.
    physical_gate = physical_gate_applies(domain, service, permitted)
    required_cap = service_capability(domain, service, service_data)
    predicted: str
    if unbound:
        predicted = "denied"
    elif physical_gate and effective_cap(access_key, "cap_physical_control") != CAP_ALLOW:
        predicted = _cap_outcome(effective_cap(access_key, "cap_physical_control"))
    elif required_cap is not None and effective_cap(access_key, required_cap) != CAP_ALLOW:
        predicted = _cap_outcome(effective_cap(access_key, required_cap))
    elif not permitted:
        predicted = "denied"
    else:
        predicted = "allowed"

    mesa: dict | None = None
    settings = data.store.get_settings()
    if data.mesa is not None and settings.mesa_mode != MESA_MODE_OFF and permitted:
        verdict = evaluate_service_call(
            data.mesa, settings.mesa_mode, access_key, permitted,
            domain=domain, service=service, service_data=service_data, session_id="dry_run",
        )
        mesa = {
            "allowed": verdict.allowed,
            "confirm": verdict.confirm,
            "blocked": [{"entity_id": e, "rule": r, "reason": reason} for e, r, reason in verdict.blocked],
            "warnings": verdict.warnings,
        }
        # MESA only narrows the outcome, and only when the cap gate did not
        # already deny/pend (mirrors call_service: the cap gate returns first).
        if predicted == "allowed":
            if verdict.confirm:
                predicted = "pending_approval"
            elif not verdict.allowed:
                predicted = "denied"

    body = {
        "domain": domain,
        "service": service,
        "requested_target_count": requested,
        "resolved_entities": permitted,
        "dropped_count": max(requested - len(permitted), 0),
        "mesa": mesa,
        "physical_gate": physical_gate,
        "predicted_outcome": predicted,
    }
    if unbound:
        body["unbound_targets"] = list(unbound)
        body["unbound_reason"] = unbound_target_error(domain, service, unbound)
    return _tool_success(json.dumps(body, default=str)), "allowed", "dry_run_service"


def config_entity_references(hass: HomeAssistant, cfg_type: str, config: dict) -> set[str]:
    """Entity IDs an automation or script config refers to.

    Automation indirect targets (device/area/floor/label selectors) expand to
    their entities; callers collapse missing and out-of-scope IDs together.
    """
    referenced: set[str] = set()
    if cfg_type == "automation":
        for ents in entities_by_role(config, build_expand_target(hass)).values():
            referenced.update(ents)
    else:
        _collect_entity_id_values(config, referenced)
    return referenced


def reference_warnings(hass: HomeAssistant, access_key: AccessKeyRecord, cfg_type: str, config: dict) -> list[str]:
    """A saved config's references that are missing or outside this access key's scope."""
    try:
        refs = config_entity_references(hass, cfg_type, config)
    except Exception:  # a warning must never fail a saved write
        return []
    missing = sorted(eid for eid in refs if resolve_read(eid, access_key, hass) not in (Permission.READ, Permission.WRITE))
    if not missing:
        return []
    listed = ", ".join(missing[:REFERENCE_WARNING_MAX])
    more = f" and {len(missing) - REFERENCE_WARNING_MAX} more" if len(missing) > REFERENCE_WARNING_MAX else ""
    return [f"Saved, but these referenced entities are missing or outside this access key's scope, so the "
            f"{cfg_type} may not work as intended: {listed}{more}."]


async def _tool_validate_automation_or_script(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: validate an automation or script config without saving it."""
    if effective_cap(access_key, "cap_diagnostics") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "validate_automation_or_script"

    cfg_type = args.get("type")
    config = args.get("config")
    if cfg_type not in ("automation", "script"):
        return _tool_error("Provide type ('automation' or 'script') and a config object."), "invalid_request", "validate_automation_or_script"

    input_error = authoring_input_error(args, cfg_type)
    if input_error:
        if not isinstance(config, dict) or input_error.startswith("Internal error"):
            return _tool_error(input_error), "invalid_request", "validate_automation_or_script"
        body = {"type": cfg_type, "valid": False, "errors": [input_error], "referenced_entities": []}
        return _tool_success(json.dumps(body)), "allowed", "validate_automation_or_script"

    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "validate_automation_or_script"
    valid = True
    errors: list[str] = []
    referenced: set[str] = set()
    try:
        if cfg_type == "automation":
            result = await _validate_automation_config(hass, "phoenix_mcp_validate", config)
        else:
            script_result = await _validate_script_config(hass, "phoenix_mcp_validate", config)
            result = script_result  # type: ignore[assignment]  # one local, two validator result types
        referenced = config_entity_references(hass, cfg_type, config)
        if result is None:
            valid = False
            errors.append("Config failed schema validation.")
    except vol.Invalid as exc:
        valid = False
        errors.append(await async_validation_message(hass, cfg_type, config, exc))
    except HomeAssistantError:
        valid = False
        errors.append(await async_validation_message(hass, cfg_type, config))
    except Exception:
        _LOGGER.error("Config validation raised an unexpected internal error")
        return _tool_error("Internal error validating this config."), "invalid_request", "validate_automation_or_script"

    # Never reveal existence for entities the access key cannot access: a hidden or
    # unexposed real entity must be indistinguishable from a typo (no existence
    # oracle). Since resolve() ghost-checks, READ/WRITE already implies the entity
    # exists, so exists is reported as the accessible flag and collapses for
    # everything else.
    refs = []
    for eid in sorted(referenced):
        accessible = resolve_read(eid, access_key, hass) in (Permission.READ, Permission.WRITE)
        refs.append({"entity_id": eid, "exists": accessible, "accessible": accessible})
    body = {"type": cfg_type, "valid": valid, "errors": errors, "referenced_entities": refs}
    return _tool_success(json.dumps(body, default=str)), "allowed", "validate_automation_or_script"
