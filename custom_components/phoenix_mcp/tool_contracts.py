"""Normalize structured Tool Catalog v2 inputs to the executors' flat shape and diagnose retired v1 shapes."""

from __future__ import annotations

import json
from typing import Any

from .const import (
    PATCH_DASHBOARD_MAX_CHANGES,
    SERVICE_TARGET_MAX_GROUPS,
    SERVICE_TARGET_MAX_IDS,
    UNKNOWN_ARGUMENT_ALIASES,
    UNKNOWN_ARGUMENT_MAX_ERRORS,
    UNKNOWN_ARGUMENT_MAX_NAME,
    UNKNOWN_ARGUMENT_PROTECTED_FIELDS,
)
from .tool_input import _PUBLIC_SCHEMAS, nested_argument_error


_RETIRED_REPLACEMENTS: dict[str, dict[str, str]] = {
    "get_state": {
        "detailed": 'projection: {"kind":"full"} (or {"kind":"compact"})',
        "fields": 'projection: {"kind":"fields","fields":[...]}',
    },
    "get_calendar_events": {"calendar_id": "entity_id"},
    "wait_for_approval": {"approval_id": 'approval_ids: ["<approval_id>"]'},
    "call_service": {
        "domain": 'service: {"domain":"...","name":"...","data":{...}}',
        "service_data": "service.data",
        "entity_id": 'targets: [{"kind":"entity","ids":[...]}]',
        "device_id": 'targets: [{"kind":"device","ids":[...]}]',
        "area_id": 'targets: [{"kind":"area","ids":[...]}]',
    },
    "dry_run_service": {
        "domain": 'service: {"domain":"...","name":"...","data":{...}}',
        "service_data": "service.data",
        "entity_id": 'targets: [{"kind":"entity","ids":[...]}]',
        "device_id": 'targets: [{"kind":"device","ids":[...]}]',
        "area_id": 'targets: [{"kind":"area","ids":[...]}]',
    },
    "get_relationships": {
        "entity_id": 'scope: {"kind":"entity","id":"..."}',
        "device_id": 'scope: {"kind":"device","id":"..."}',
        "integration": 'scope: {"kind":"integration","id":"..."}',
        "area": 'scope: {"kind":"area","id":"..."}',
        "label": 'scope: {"kind":"label","id":"..."}',
    },
    "get_logbook": {
        "entity_ids": 'target: {"kind":"resources","entity_ids":[...]}',
        "device_ids": 'target: {"kind":"resources","device_ids":[...]}',
        "context_id": 'target: {"kind":"context","id":"..."}',
    },
    "get_esphome_job": {
        "job_id": 'lookup: {"kind":"job","id":"..."}',
        "file": 'lookup: {"kind":"file","id":"..."}',
    },
    "edit_energy_config": {
        "op": "operation",
        "statistic": 'target: {"kind":"statistic","id":"..."}',
        "device_name": 'target: {"kind":"device_name","name":"..."}',
        "source_type": 'target: {"kind":"source","source_type":"..."}',
        "new_statistic": "changes.new_statistic",
        "name": "changes.name",
        "stat_energy_from": "changes.stat_energy_from",
        "stat_energy_to": "changes.stat_energy_to",
        "number_energy_price": "changes.number_energy_price",
        "number_energy_price_export": "changes.number_energy_price_export",
        "entity_energy_price": "changes.entity_energy_price",
        "entity_energy_price_export": "changes.entity_energy_price_export",
        "stat_cost": "changes.stat_cost",
        "stat_compensation": "changes.stat_compensation",
    },
    "patch_dashboard": {
        "url_path": "target.url_path",
        "path": "changes[].path",
        "op": "changes[].kind",
        "value": "changes[].value",
        "change": 'changes: [{"path":[...],"kind":"set","value":...}]',
    },
    "set_entity": {
        field: f"changes.{field}" for field in (
            "name", "use_device_name", "icon", "area_id", "device_class", "new_entity_id",
            "enabled", "hidden", "add_aliases", "remove_aliases", "add_labels",
            "remove_labels", "categories",
        )
    },
    "set_device": {
        field: f"changes.{field}"
        for field in ("name", "area_id", "enabled", "add_labels", "remove_labels")
    },
    "set_integration": {
        field: f"changes.{field}"
        for field in ("title", "pref_disable_new_entities", "pref_disable_polling")
    },
    "compare_states": {"entity_id": "entity_ids: [...]"},
}


_SERVICE_OBJECT_SYNTAX = 'service: {"domain":"...","name":"...","data":{...}}'


def _migration_error(
    tool: str, retired: set[str], extra: dict[str, str] | None = None
) -> str:
    """Actionable field-by-field replacement syntax for a retired v1 shape."""
    replacements = {**_RETIRED_REPLACEMENTS[tool], **(extra or {})}
    fields = sorted(retired)
    mapping = "; ".join(f"{field} -> {replacements[field]}" for field in fields)
    return (
        f"Catalog v2 no longer accepts {', '.join(fields)} for {tool}. "
        f"Replace with: {mapping}."
    )


def _near_parameter(name: str, candidate: str) -> bool:
    """Recognize a single edit or adjacent transposition, without fuzzy words."""
    name = name.lower().replace("_", "")
    candidate = candidate.replace("_", "")
    if name == candidate:
        return True
    if len(name) < 2 or len(candidate) < 3 or abs(len(name) - len(candidate)) > 1:
        return False
    if len(name) == len(candidate):
        differences = [i for i, (a, b) in enumerate(zip(name, candidate)) if a != b]
        return len(differences) == 1 or (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and name[differences[0]] == candidate[differences[1]]
            and name[differences[1]] == candidate[differences[0]]
        )
    shorter, longer = sorted((name, candidate), key=len)
    return any(longer[:i] + longer[i + 1:] == shorter for i in range(len(longer)))


def unknown_argument_error(tool: str, args: dict[str, Any]) -> str | None:
    """Refuse undeclared top-level arguments, naming the intended one when it is a near miss.

    Every tool root is closed (additionalProperties false), as HA's Assist engine
    already enforces, so an unknown argument is refused rather than silently
    ignored. Tools with a curated field list get alias-aware suggestions for those
    fields; every other tool gets the one-edit near-miss check against all of its
    declared parameters. Retired public forms are left to their migration
    guidance. Callers run it after the capability-deny check (so a denied access
    key learns nothing about its arguments) and before an approval is created.
    It is deliberately not a validator for arbitrary nested configuration.
    """
    schema = _PUBLIC_SCHEMAS.get(tool)
    if schema is None:
        return None
    properties = schema.get("properties", {})
    protected = UNKNOWN_ARGUMENT_PROTECTED_FIELDS.get(tool, tuple(properties))
    findings: list[str] = []

    def examine(values: dict, declared: dict, fields: tuple[str, ...], prefix: str = "") -> None:
        for name in values:
            if len(findings) >= UNKNOWN_ARGUMENT_MAX_ERRORS:
                break
            if isinstance(name, str) and name in declared:
                continue
            if not isinstance(name, str) or len(name) > UNKNOWN_ARGUMENT_MAX_NAME:
                # Refused without echoing a name too long or odd to quote.
                if "An unknown argument was supplied." not in findings:
                    findings.append("An unknown argument was supplied.")
                continue
            # Retired public forms keep their more specific migration guidance.
            if not prefix and name in _RETIRED_REPLACEMENTS.get(tool, {}):
                continue
            candidates = [field for field in fields if (
                name in UNKNOWN_ARGUMENT_ALIASES.get(field, ()) or _near_parameter(name, field)
            )]
            if not prefix and tool == "patch_dashboard" and name in UNKNOWN_ARGUMENT_ALIASES["url_path"]:
                candidates = ["target.url_path"]
            # JSON quoting prevents control characters from becoming error formatting.
            label = json.dumps(prefix + name, ensure_ascii=True)
            if not candidates:
                findings.append(f"Unknown argument {label}.")
            elif len(candidates) == 1:
                destination = prefix + candidates[0]
                suffix = " (already supplied; remove the conflicting argument)" if candidates[0] in values else ""
                findings.append(f"Unknown argument {label}. Use {destination}{suffix}.")
            else:
                findings.append(f"Unknown argument {label}. Possible parameters: {', '.join(prefix + c for c in sorted(candidates))}.")

    examine(args, properties, protected)
    if tool == "patch_dashboard" and isinstance(args.get("target"), dict):
        examine(args["target"], properties["target"]["properties"], ("url_path",), "target.")
    if not findings:
        return None
    return " ".join(findings) + f" Valid parameters: {', '.join(sorted(properties))}. Correct the arguments and retry."


def normalize_tool_args(tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Translate a Catalog v2 request to the flat shape the executors take.

    Returns (args, error). Unrelated unknown keys pass through untouched; retired
    fields get migration guidance and targeted selector mistakes get correction
    errors.
    """
    replacements = _RETIRED_REPLACEMENTS.get(tool, {})
    retired = set(replacements) & set(args)
    service_string = tool in {"call_service", "dry_run_service"} and isinstance(
        args.get("service"), str
    )
    if retired or service_string:
        extra = {"service": _SERVICE_OBJECT_SYNTAX} if service_string else None
        return args, _migration_error(
            tool, retired | ({"service"} if service_string else set()), extra
        )
    error = unknown_argument_error(tool, args)
    if error and tool not in {"call_service", "dry_run_service"}:
        return args, error
    out = dict(args)
    nested_error = nested_argument_error(tool, args)
    if nested_error and tool not in {"call_service", "dry_run_service"}:
        return out, nested_error
    if tool == "get_state":
        projection = out.pop("projection", None)
        if not isinstance(projection, dict):
            return out, "projection is required and must be an object."
        kind = projection.get("kind")
        if kind == "full":
            out["detailed"] = True
        elif kind == "fields" and isinstance(projection.get("fields"), list):
            out["fields"] = projection["fields"]
        elif kind != "compact":
            return out, "projection.kind must be compact, full, or fields."
    elif tool == "get_calendar_events":
        pass
    elif tool in {"call_service", "dry_run_service"}:
        service = out.pop("service", None)
        targets = out.pop("targets", None)
        if not isinstance(service, dict) or not isinstance(service.get("domain"), str) or not isinstance(service.get("name"), str):
            return out, "service must contain string domain and name."
        out["domain"] = service["domain"]
        out["service"] = service["name"]
        if "data" in service:
            out["service_data"] = service["data"]
        if targets is not None:
            if not isinstance(targets, list) or not 1 <= len(targets) <= SERVICE_TARGET_MAX_GROUPS:
                return out, "targets must be a non-empty array when supplied."
            target_ids: dict[str, list[Any]] = {}
            has_all = False
            for target in targets:
                if not isinstance(target, dict):
                    return out, "Each target must be an object."
                kind, ids = target.get("kind"), target.get("ids")
                if isinstance(ids, list) and len(ids) > SERVICE_TARGET_MAX_IDS:
                    return out, "A target group contains too many ids."
                if kind == "all":
                    has_all = True
                elif isinstance(kind, str) and kind in {"entity", "device", "area"} and isinstance(ids, list) and ids:
                    merged = target_ids.setdefault(kind, [])
                    merged.extend(item for item in ids if item not in merged)
                else:
                    return out, "Each target needs kind all, or entity/device/area with non-empty ids."
            if has_all and target_ids:
                return out, "A target with kind all cannot be combined with entity, device, or area targets."
            if has_all:
                out["entity_id"] = "all"
            else:
                for kind, ids in target_ids.items():
                    out[f"{kind}_id"] = ids
    elif tool == "get_relationships":
        scope = out.pop("scope", None)
        if not isinstance(scope, dict) or scope.get("kind") not in {"entity", "device", "integration", "area", "label"} or not isinstance(scope.get("id"), str):
            return out, "scope must contain a supported kind and string id."
        out[f"{scope['kind']}_id" if scope["kind"] in {"entity", "device"} else scope["kind"]] = scope["id"]
    elif tool == "get_logbook":
        target = out.pop("target", None)
        if target is None:
            return out, None
        if not isinstance(target, dict):
            return out, "target must be an object."
        if target.get("kind") == "context" and isinstance(target.get("id"), str):
            out["context_id"] = target["id"]
        elif target.get("kind") == "resources":
            for key in ("entity_ids", "device_ids"):
                if key in target:
                    out[key] = target[key]
        else:
            return out, "target must be a context id or resource selector."
    elif tool == "get_esphome_job":
        lookup = out.pop("lookup", None)
        if not isinstance(lookup, dict) or lookup.get("kind") not in {"job", "file"} or not isinstance(lookup.get("id"), str):
            return out, "lookup must contain kind job or file and a string id."
        out["job_id" if lookup["kind"] == "job" else "file"] = lookup["id"]
    elif tool == "edit_energy_config":
        operation, target, changes = out.pop("operation", None), out.pop("target", None), out.pop("changes", None)
        if not isinstance(operation, str) or not isinstance(target, dict) or not isinstance(changes, dict):
            return out, "operation, target, and changes must be objects in the Catalog v2 shape."
        out["op"] = operation
        out.update(changes)
        if target.get("kind") == "statistic" and isinstance(target.get("id"), str):
            out["statistic"] = target["id"]
        elif target.get("kind") == "device_name" and isinstance(target.get("name"), str):
            out["device_name"] = target["name"]
        elif target.get("kind") == "source" and isinstance(target.get("source_type"), str):
            out["source_type"] = target["source_type"]
        else:
            return out, "target must identify a statistic, device_name, or source."
    elif tool == "patch_dashboard":
        target, changes = out.pop("target", None), out.pop("changes", None)
        if target is not None and not isinstance(target, dict):
            return out, "target must be an object."
        if not isinstance(changes, list) or not 1 <= len(changes) <= PATCH_DASHBOARD_MAX_CHANGES:
            return out, f"changes must be a list of 1 to {PATCH_DASHBOARD_MAX_CHANGES} changes."
        if target and "url_path" in target:
            out["url_path"] = target["url_path"]
        flat = []
        for change in changes:
            if not isinstance(change, dict) or not isinstance(change.get("path"), list):
                return out, "Each change needs a path array and a kind."
            item = {"path": change["path"], "op": change.get("kind")}
            if "value" in change:
                item["value"] = change["value"]
            flat.append(item)
        out["changes"] = flat
    elif tool in {"set_entity", "set_device", "set_integration"}:
        changes = out.pop("changes", None)
        if not isinstance(changes, dict) or not changes:
            return out, "changes must be a non-empty object."
        out.update(changes)
    elif tool == "compare_states":
        ids = out.pop("entity_ids", None)
        if not isinstance(ids, list) or not ids:
            return out, "entity_ids must be a non-empty array."
        out["entity_id"] = ids
    return out, error or nested_error
