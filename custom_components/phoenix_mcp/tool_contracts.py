"""Normalize structured Tool Catalog v2 inputs to the executors' flat shape, reading retired v1 shapes that name one request."""

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
from .tool_input import _PUBLIC_SCHEMAS, nested_argument_error, note_argument_adjustment, scalar_arguments


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
_RETIRED_REPLACEMENTS["get_logbook"]["entity_id"] = 'target: {"kind":"resources","entity_ids":[...]}'

# The current fields each retired form stands in for. When none is present the
# retired form names one request: it is read in place (the executors take that
# flat shape) and the result says so. With both, the call is ambiguous and keeps
# its migration guidance.
_CURRENT_FIELDS: dict[str, set[str]] = {
    "get_state": {"projection"},
    "get_calendar_events": {"entity_id"},
    "wait_for_approval": {"approval_ids"},
    "get_relationships": {"scope"},
    "get_logbook": {"target"},
    "get_esphome_job": {"lookup"},
    "edit_energy_config": {"operation", "target", "changes"},
    "patch_dashboard": {"changes"},
    "set_entity": {"changes"},
    "set_device": {"changes"},
    "set_integration": {"changes"},
    "compare_states": {"entity_ids"},
}


def _listed(value: Any) -> Any:
    return value if isinstance(value, list) else [value]


def _adopt_retired(tool: str, args: dict[str, Any], retired: set[str]) -> dict[str, Any]:
    """The executors' flat shape for a retired form that names one request."""
    out = dict(args)
    if tool == "get_calendar_events":
        out["entity_id"] = out.pop("calendar_id")
    elif tool == "wait_for_approval":
        out["approval_ids"] = _listed(out.pop("approval_id"))
    elif tool == "get_logbook" and "entity_id" in out:
        out["entity_ids"] = _listed(out.pop("entity_id"))
    elif tool == "patch_dashboard":
        change = out.pop("change", None)
        single = {key: out.pop(key) for key in ("path", "op", "value") if key in out}
        if isinstance(change, dict):
            change = {("op" if key == "kind" else key): value for key, value in change.items()}
            out["changes"] = [{**change, **single}]
        elif single:
            out["changes"] = [single]
    elif tool == "compare_states":
        out["entity_id"] = out.pop("entity_id")
    note_argument_adjustment(
        "; ".join(f"{field} was read as {_RETIRED_REPLACEMENTS[tool][field]}" for field in sorted(retired))
        + ". Send that shape next time."
    )
    return out


_SERVICE_OBJECT_SYNTAX = 'service: {"domain":"...","name":"...","data":{...}}'
_TARGET_SELECTORS = {"entity_id": "entity", "device_id": "device", "area_id": "area"}
_TARGET_SYNONYMS = {"entity_ids": "entity_id", "entities": "entity_id", "device_ids": "device_id", "area_ids": "area_id"}
_EMPTY = (None, "", [], {})


def _target_groups(selectors: dict[str, Any]) -> list[dict[str, Any]]:
    """Target groups for flat selectors; entity_id "all" is a whole-domain target."""
    groups: list[dict[str, Any]] = []
    for key, kind in _TARGET_SELECTORS.items():
        if key in selectors:
            ids = _listed(selectors[key])
            groups.append({"kind": "all"} if kind == "entity" and ids == ["all"] else {"kind": kind, "ids": ids})
    return groups


def _service_call_shape(args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """call_service and dry_run_service in the current shape, from Home Assistant's own or a retired one.

    Home Assistant's call shape (service "light.turn_on", target, data) and the
    retired flat one (domain, service, service_data, entity_id) each name one call
    exactly, so they are read as service and targets, which then go through every
    check the current shape does. Sending both shapes for one part is refused,
    with the arguments as far as they were read, so the handler can still let a
    denied capability answer first.
    """
    out = dict(args)
    read: list[str] = []
    service = out.get("service")
    domain = out.pop("domain", None)
    if isinstance(service, dict):
        if domain is not None:
            return out, "Pass the domain inside service, not beside it: " + _SERVICE_OBJECT_SYNTAX + "."
        if "name" not in service and isinstance(service.get("service"), str):
            service = {**{key: value for key, value in service.items() if key != "service"}, "name": service["service"]}
            read.append("service.service as service.name")
    elif isinstance(service, str) and service.strip():
        name = service.strip()
        if not (isinstance(domain, str) and domain.strip()):
            domain, separator, name = name.partition(".")
            if not separator:
                return out, "service names no domain: " + _SERVICE_OBJECT_SYNTAX + "."
        service = {"domain": domain.strip(), "name": name}
        read.append(f'service "{args["service"]}"' + (f' and domain "{args["domain"]}"' if "domain" in args else "")
                    + f' as service: {{"domain":"{service["domain"]}","name":"{service["name"]}"}}')
    elif domain is not None:
        return out, "service must contain string domain and name: " + _SERVICE_OBJECT_SYNTAX + "."
    for key in ("service_data", "data"):
        if key in out:
            if not isinstance(service, dict) or "data" in service:
                return out, f"Pass {key} as service.data, not beside service: " + _SERVICE_OBJECT_SYNTAX + "."
            service = {**service, "data": out.pop(key)}
            read.append(f"{key} as service.data")
    if service is not None:
        out["service"] = service

    selectors = {key: out.pop(key) for key in _TARGET_SELECTORS if key in out}
    for key, selector in _TARGET_SYNONYMS.items():
        if key in out and selector not in selectors:
            selectors[selector] = out.pop(key)
            read.append(f"{key} as {selector}")
    # Home Assistant's target is an object; a list there is a misspelt targets,
    # which unknown_argument_error names.
    ha_target = out.pop("target") if isinstance(out.get("target"), dict) else None
    if ha_target is not None:
        if not ha_target or set(ha_target) - set(_TARGET_SELECTORS):
            return out, ("target supports entity_id, device_id and area_id only; send them as "
                          'targets: [{"kind":"entity","ids":[...]}].')
        if set(ha_target) & set(selectors):
            return out, "Pass each selector once, in target or beside it."
        selectors.update(ha_target)
        read.append("target as targets")
    elif selectors:
        read.append(", ".join(sorted(selectors)) + " as targets")
    targets = out.get("targets")
    if selectors:
        if targets is not None:
            return out, 'Pass targets or entity_id, device_id and area_id, not both: targets: [{"kind":"entity","ids":[...]}].'
        targets = _target_groups(selectors)
    if isinstance(targets, dict):
        targets = [targets]
        read.append("one targets object as a list")
    if isinstance(targets, list):
        fixed = []
        for group in targets:
            if isinstance(group, dict):
                kind, ids = group.get("kind"), group.get("ids")
                if isinstance(kind, str) and kind in _TARGET_SELECTORS:
                    group = {**group, "kind": _TARGET_SELECTORS[kind]}
                    read.append(f"kind {kind} as {_TARGET_SELECTORS[kind]}")
                if isinstance(ids, str):
                    group = {**group, "ids": [ids]}
                    read.append("ids text as a list")
            fixed.append(group)
        targets = fixed
    if targets is None and isinstance(service, dict) and isinstance(service.get("data"), dict):
        lifted = {key: service["data"][key] for key in _TARGET_SELECTORS if key in service["data"]}
        if lifted:
            out["service"] = {**service, "data": {k: v for k, v in service["data"].items() if k not in lifted}}
            targets = _target_groups(lifted)
            read.append(", ".join(f"service.data.{key}" for key in sorted(lifted)) + " as targets")
    if targets is not None:
        out["targets"] = targets
    if read:
        note_argument_adjustment("Read " + "; ".join(dict.fromkeys(read)) + ". Send the current shape next time.")
    return out, None


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


def adopt_near_misses(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Read an undeclared argument as the one parameter it plainly means.

    On a read-only tool, a synonym or one-edit near miss with exactly one
    candidate that was not also sent is renamed, and an undeclared argument
    holding only an empty placeholder is dropped. A write keeps refusing both,
    since a guess there could act on something unintended or let an undeclared
    key through. Each reading is noted on the result; anything else stays for
    unknown_argument_error to refuse. Retired forms keep their own handling.
    """
    schema = _PUBLIC_SCHEMAS.get(tool)
    if schema is None:
        return args
    from .agent_recovery import tool_is_read_only

    # A preview must refuse what the call it predicts refuses.
    renames = tool_is_read_only(tool) and tool != "dry_run_service"
    properties = schema.get("properties", {})
    fields = UNKNOWN_ARGUMENT_PROTECTED_FIELDS.get(tool, tuple(properties))
    out = dict(args)
    read: list[str] = []
    for name in args:
        if (not isinstance(name, str) or name in properties or len(name) > UNKNOWN_ARGUMENT_MAX_NAME
                or name in _RETIRED_REPLACEMENTS.get(tool, {})):
            continue
        candidates = [field for field in fields if (
            name in UNKNOWN_ARGUMENT_ALIASES.get(field, ()) or _near_parameter(name, field))]
        if renames and len(candidates) == 1 and candidates[0] not in out:
            out[candidates[0]] = out.pop(name)
            read.append(f"{json.dumps(name)} as {candidates[0]}")
        elif renames and not candidates and args[name] in _EMPTY:
            del out[name]
            read.append(f"empty {json.dumps(name)}, which this tool does not take, as absent")
    if read:
        note_argument_adjustment("Read " + "; ".join(read) + ". Send the declared parameter names next time.")
    return out


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


def _implied_kind(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Add a selector's missing kind when its other keys decide it, and say so.

    A logbook target holding entity_ids or device_ids names resources, an
    ESPHome lookup whose id is a YAML file name is a file and any other id a job,
    and a relationships scope whose ids are all entity IDs is an entity scope.
    Anything still ambiguous is left for the shape check to refuse.
    """
    field = {"get_logbook": "target", "get_esphome_job": "lookup", "get_relationships": "scope"}.get(tool)
    kind: str | None = None
    value = args.get(field) if field else None
    if not isinstance(value, dict) or "kind" in value:
        return args
    if tool == "get_logbook" and {"entity_ids", "device_ids"} & set(value) and "id" not in value:
        kind = "resources"
    elif tool == "get_esphome_job" and isinstance(value.get("id"), str):
        kind = "file" if value["id"].lower().endswith((".yaml", ".yml")) else "job"
    elif tool == "get_relationships":
        from homeassistant.core import valid_entity_id

        ids = value.get("id")
        ids = [ids] if isinstance(ids, str) else ids
        if isinstance(ids, list) and ids and all(isinstance(item, str) and valid_entity_id(item) for item in ids):
            kind = "entity"
    if kind is None:
        return args
    note_argument_adjustment(f'{field} had no kind, so its keys were read as kind "{kind}".')
    return {**args, field: {**value, "kind": kind}}


def _state_projection_shape(args: dict[str, Any]) -> dict[str, Any]:
    """get_state's projection object from an omitted one, a bare kind or a field list."""
    projection = args.get("projection", None)
    if projection is None:
        note_argument_adjustment('No projection was sent, so compact was used: projection {"kind":"compact"}.')
        return {**args, "projection": {"kind": "compact"}}
    if isinstance(projection, str) and projection in ("compact", "full"):
        note_argument_adjustment(f'Read projection "{projection}" as {{"kind":"{projection}"}}.')
        return {**args, "projection": {"kind": projection}}
    if isinstance(projection, list) and projection and all(isinstance(item, str) for item in projection):
        note_argument_adjustment('Read a projection list as {"kind":"fields","fields":[...]}.')
        return {**args, "projection": {"kind": "fields", "fields": projection}}
    if isinstance(projection, dict) and "kind" not in projection and isinstance(projection.get("fields"), list):
        note_argument_adjustment('Read projection with fields as {"kind":"fields","fields":[...]}.')
        return {**args, "projection": {**projection, "kind": "fields"}}
    return args


def state_projection(projection: dict) -> tuple[list | None, bool, str | None]:
    """Read a get_state-style projection: (fields, detailed, error).

    One reading for every tool that takes it, so compact, full and fields mean
    the same thing on get_state and search_entities.
    """
    kind = projection.get("kind")
    if kind == "full":
        return None, True, None
    if kind == "fields" and isinstance(projection.get("fields"), list):
        return projection["fields"], False, None
    if kind != "compact":
        return None, False, "projection.kind must be compact, full, or fields."
    return None, False, None


# Tools that check their arguments in the handler, after a capability check
# only they can make, take the central scalar pass there. call_service and
# dry_run_service translate a service shape first and keep their own checks.
_HANDLER_CHECKED = frozenset(UNKNOWN_ARGUMENT_PROTECTED_FIELDS) - {"call_service", "dry_run_service"}


def handler_arguments(tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Unknown names, then the central scalar contract, for a tool that checks its own arguments.

    Such a tool runs this after its capability check, so a denied access key
    still learns nothing about its arguments; its scalars are then held to the
    contract every other tool's are (_call_tool_checked): view_index 0.0 is 0
    and a missing required argument is named the same way. Writes stay strict.
    """
    if error := unknown_argument_error(tool, args):
        return args, error
    return scalar_arguments(tool, args)


def normalize_tool_args(tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Translate a Catalog v2 request to the flat shape the executors take.

    Returns (args, error). Unrelated unknown keys pass through untouched; retired
    fields get migration guidance and targeted selector mistakes get correction
    errors.
    """
    shape_error = None
    if tool in {"call_service", "dry_run_service"}:
        # Like an unknown argument, a shape error is returned after the
        # translation below, so the handler can still let a denied capability
        # answer first from the recognized service and selectors.
        args, shape_error = _service_call_shape(args)
    replacements = _RETIRED_REPLACEMENTS.get(tool, {})
    retired = set(replacements) & set(args)
    service_string = tool in {"call_service", "dry_run_service"} and isinstance(
        args.get("service"), str
    )
    if retired and tool in _CURRENT_FIELDS:
        current = _CURRENT_FIELDS[tool] & set(args)
        if tool == "patch_dashboard":
            # A top-level url_path stands beside current changes; it belongs in target.
            target = args.get("target")
            if "url_path" in retired and not (isinstance(target, dict) and "url_path" in target):
                note_argument_adjustment("url_path was read as target.url_path. Send that shape next time.")
                args = {**{key: value for key, value in args.items() if key not in ("url_path", "target")},
                        "target": {**(target if isinstance(target, dict) else {}), "url_path": args["url_path"]}}
                retired.discard("url_path")
                if not retired:
                    return normalize_tool_args(tool, args)
        if current:
            return args, _migration_error(tool, retired)
        adopted = _adopt_retired(tool, args, retired)
        return adopted, unknown_argument_error(tool, adopted)
    if retired or service_string:
        extra = {"service": _SERVICE_OBJECT_SYNTAX} if service_string else None
        return args, _migration_error(
            tool, retired | ({"service"} if service_string else set()), extra
        )
    args = adopt_near_misses(tool, args)
    if tool == "get_state":
        args = _state_projection_shape(args)
    args = _implied_kind(tool, args)
    error = unknown_argument_error(tool, args)
    if error and tool not in {"call_service", "dry_run_service"}:
        return args, error
    out = dict(args)
    nested_error = nested_argument_error(tool, args)
    if nested_error and tool not in {"call_service", "dry_run_service"}:
        return out, nested_error
    if tool in _HANDLER_CHECKED:
        out, error = scalar_arguments(tool, out)
        if error:
            return out, error
    if tool == "get_state":
        projection = out.pop("projection", None)
        if not isinstance(projection, dict):
            return out, "projection must be an object: {\"kind\":\"compact\"}, {\"kind\":\"full\"} or {\"kind\":\"fields\",\"fields\":[...]}."
        fields, detailed, error = state_projection(projection)
        if error:
            return out, error
        if detailed:
            out["detailed"] = True
        elif fields is not None:
            out["fields"] = fields
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
        scope_id = scope.get("id") if isinstance(scope, dict) else None
        if (not isinstance(scope, dict) or scope.get("kind") not in {"entity", "device", "integration", "area", "label"}
                or not (isinstance(scope_id, str) or (isinstance(scope_id, list) and scope_id
                                                      and all(isinstance(item, str) for item in scope_id)))):
            return out, "scope must contain a supported kind and an id: a string, or a list of strings."
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
    return out, shape_error or error or nested_error
