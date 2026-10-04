"""Energy dashboard tools: get_energy_config, get_solar_forecast, edit_energy_config.

Reads need cap_config_read; edit_energy_config needs cap_energy_write. The Energy
preferences (statistics mapped to roles: grid, solar, battery, gas, water, or an
individual device) live in .storage/energy and are reachable ONLY through the
energy/get_prefs WebSocket command, so this module is the single path to them.
That command runs under the resolved admin user and names every statistic, so
results pass through filter_service_response, which replaces an entity the access key
cannot resolve with the redaction sentinel and drops entity-keyed entries.

THE WRITE IS ADDRESSED, NEVER WHOLESALE. energy/save_prefs full-replaces each
top-level key it receives, so a caller composing "device_consumption" from a
redacted read would delete every device the read hid. edit_energy_config takes
ONE addressed operation, re-reads the preferences UNREDACTED inside the executor,
applies it, checks the result differs only as intended (`_verify_structure`), and
sends only the top-level keys that changed. The redacted view is never a write
input, so a placeholder is never written back as configuration; a literal one in
the caller's arguments is refused. A version restore is the one wholesale write:
reproducing the snapshot IS the operation. mcp_view registers these tools.
"""

from __future__ import annotations

from typing import Any
import copy
import json
import logging
import math

from homeassistant.core import HomeAssistant, valid_entity_id

from ..const import CAP_DENY, REDACTION_SENTINEL
from ..data import PhoenixData
from ..tool_contracts import normalize_tool_args
from ..helpers import (
    content_hash,
    diff_summary_fields as _summary,
    effective_cap,
    str_arg,
    version_summary_fields as _version_summary,
)
from ..policy_engine import Permission, filter_service_response, resolve
from ..access_key_store import AccessKeyRecord
from ..tool_common import (
    _CAP_FORBIDDEN_MESSAGE,
    _gate,
    _record_version,
    _tool_error,
    _tool_success,
    _truncate,
    redaction_sentinel_path,
)
from ..config_recovery import ConfigurationRecoveryError, safe_snapshot
from ..request_authority import check_request_authority
from ..ws_dispatch import WsCommandNotDispatchedError, WsDispatchError, async_ws_command

_LOGGER = logging.getLogger(__name__)


# The three top-level keys of HA's EnergyPreferences. device_consumption_water is
# NotRequired upstream, so an instance configured before it existed returns two
# keys rather than three; every one of them is emitted here so a caller never has
# to distinguish "no water devices" from "this HA is too old to have the key".
_PREFS_KEYS = ("energy_sources", "device_consumption", "device_consumption_water")

# The two dispatch failures that both mean "there is no Energy configuration to
# look at", which is an ANSWER (configured: false) rather than a failed read.
# Reporting either as a tool error would tell an agent checking for a dependency
# that the check itself did not run, which is the opposite of what happened.
#
#   not_found      energy/get_prefs answers ERR_NOT_FOUND "No prefs" on an
#                  instance whose Energy dashboard was never set up, rather than
#                  returning empty lists. ws_dispatch formats a handler error as
#                  "<code>: <message>", so the code is what is matched.
#   not available  the energy component is not set up, so the command is not in
#                  hass.data["websocket_api"] and ws_dispatch refuses before any
#                  handler lookup. An HA without the component has no Energy
#                  dashboard for anything to depend on.
#
# Substring matching is deliberate but narrow: both strings are ws_dispatch's own
# formatting, one function away, not HA's. Any OTHER failure still surfaces.
_NO_CONFIG_ERRORS = ("not_found", "not available")


def _default_prefs() -> dict[str, Any]:
    """Return the empty preferences structure for a never-configured instance.

    Mirrors EnergyManager.default_preferences() in HA's energy/data.py. Callers
    pair it with configured=False so "never set up" is distinguishable from "set
    up, then everything removed", which would both read as empty lists.
    """
    return {key: [] for key in _PREFS_KEYS}


def _referenced_entities(prefs: Any, _depth: int = 0) -> list[str]:
    """Collect every entity id the preferences reference, sorted and deduplicated.

    Walks the whole structure instead of a fixed list of key names, because the
    keys differ per source type and HA adds to them; a name list would go stale
    silently and report no dependency where one exists.

    Call this on the REDACTED preferences, never the raw ones: the sentinel is
    skipped, so an entity the access key cannot resolve is absent rather than named.
    External statistics ("domain:name") are not entity ids and are not collected.
    """
    if _depth > 10:
        return []
    if isinstance(prefs, str):
        if prefs != REDACTION_SENTINEL and valid_entity_id(prefs):
            return [prefs]
        return []
    if isinstance(prefs, dict):
        found: list[str] = []
        for value in prefs.values():
            found.extend(_referenced_entities(value, _depth + 1))
        return found
    if isinstance(prefs, list):
        found = []
        for item in prefs:
            found.extend(_referenced_entities(item, _depth + 1))
        return found
    return []


async def _tool_get_energy_config(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read the Energy dashboard preferences (cap_config_read)."""
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_energy_config"

    configured = True
    try:
        prefs = await async_ws_command(hass, "energy/get_prefs", {})
    except WsDispatchError as err:
        message = str(err).lower()
        if any(marker in message for marker in _NO_CONFIG_ERRORS):
            prefs, configured = _default_prefs(), False
        else:
            _LOGGER.exception("get_energy_config: energy/get_prefs failed")
            return (
                _tool_error("Could not read the Energy dashboard configuration."),
                "invalid_request",
                "get_energy_config",
            )

    if not isinstance(prefs, dict):
        # Defensive: HA has always sent a mapping here, but a shape change would
        # otherwise reach filter_service_response and be returned as a bare value.
        _LOGGER.warning("get_energy_config: unexpected prefs type %s", type(prefs).__name__)
        prefs, configured = _default_prefs(), False

    body = _scoped_body(prefs, configured, access_key, hass)
    # Included on every read rather than behind a flag: a caller asking what the
    # dashboard depends on needs to know which dependencies are ALREADY dead.
    body["issues"] = await _energy_issues(prefs, access_key, hass) if configured else []
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_energy_config"


async def _tool_get_solar_forecast(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read solar production forecasts (cap_config_read).

    Empty when no solar source names a forecast config entry, which is the normal
    state without a forecast integration installed; that is an answer, not an
    error. Forecast values are per config entry and name no entities, so there is
    nothing here to scope, but the result is filtered anyway rather than trusted to
    stay that shape.
    """
    if effective_cap(access_key, "cap_config_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_solar_forecast"
    try:
        forecast = await async_ws_command(hass, "energy/solar_forecast", {})
    except WsDispatchError as err:
        if any(marker in str(err).lower() for marker in _NO_CONFIG_ERRORS):
            forecast = {}
        else:
            _LOGGER.exception("get_solar_forecast: energy/solar_forecast failed")
            return (
                _tool_error("Could not read the solar forecast."),
                "invalid_request",
                "get_solar_forecast",
            )
    if not isinstance(forecast, dict):
        forecast = {}
    body = {
        "configured": bool(forecast),
        "forecasts": filter_service_response(_plain(forecast), access_key, hass),
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "get_solar_forecast"


def _scoped_body(prefs: dict, configured: bool, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """The access key-scoped response body. Shared by the read and the write's result."""
    scoped = filter_service_response(prefs, access_key, hass)
    return {
        "configured": configured,
        **{key: scoped.get(key) or [] for key in _PREFS_KEYS},
        "referenced_entities": sorted(set(_referenced_entities(scoped))),
    }


def _plain(value: Any) -> Any:
    """Recursively turn sets and tuples into lists.

    HA's ValidationIssue carries `affected_entities` as a set of TUPLES.
    json.dumps cannot serialize a set, and filter_service_response walks dicts,
    lists and strings only, so a tuple would fall through it and return entity ids
    the access key cannot see. Normalising first puts them where the redactor looks.
    """
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(v) for v in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _entry_label(key: str, index: int, prefs: dict) -> str:
    """Name the configuration entry an issue is about, in the operator's terms.

    HA reports issues as three arrays positionally parallel to the preferences
    lists, which is unreadable on its own; this pairs each back with the thing the
    operator recognises (a device's own label, or the source's type).
    """
    entries = prefs.get(key) or []
    entry = entries[index] if index < len(entries) else None
    if not isinstance(entry, dict):
        return f"{key}[{index}]"
    if key == "energy_sources":
        return str(entry.get("name") or entry.get("type") or f"{key}[{index}]")
    return str(entry.get("name") or entry.get("stat_consumption") or f"{key}[{index}]")


async def _energy_issues(
    prefs: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> list[dict] | None:
    """Flatten energy/validate into one scoped, labelled list of problems.

    Returns None when validation could not run, which is reported as a null field
    rather than an empty list: "nothing is wrong" and "nobody checked" must not
    look the same to a caller deciding whether it is safe to delete something.

    Best-effort by design. This rides on a plain config READ, so a validation
    failure degrades the extra field instead of failing the read the caller
    actually asked for.
    """
    try:
        report = await async_ws_command(hass, "energy/validate", {})
    except WsDispatchError:
        _LOGGER.debug("energy/validate unavailable; reporting issues as unknown", exc_info=True)
        return None
    if not isinstance(report, dict):
        return None
    issues: list[dict] = []
    for key in _PREFS_KEYS:
        for index, per_entry in enumerate(report.get(key) or []):
            for issue in per_entry or []:
                if not isinstance(issue, dict):
                    continue
                issues.append({
                    "where": key,
                    "index": index,
                    "label": _entry_label(key, index, prefs),
                    "type": issue.get("type"),
                    "affected_entities": _plain(issue.get("affected_entities") or []),
                })
    # Scoped last, over plain containers, so an issue naming an entity this access key
    # cannot resolve reports the redaction sentinel rather than the id.
    return filter_service_response(issues, access_key, hass)


# ---------------------------------------------------------------------------
# edit_energy_config (cap_energy_write)
# ---------------------------------------------------------------------------

_OPS = ("replace_statistic", "add_device", "remove_device", "rename_device", "set_source", "remove_source")
_SOURCE_TYPES = ("grid", "solar", "battery", "gas", "water")

# The fields this tool will write per source type, mirrored from HA's own
# *_SOURCE_SCHEMA in components/energy/data.py. Deliberately a SUBSET: stat_rate,
# power_config, stat_soc and config_entry_solar_forecast are omitted because they
# are set up by HA's own flows and a hand-written value is more likely to break a
# working source than to fix one. A field absent here is refused by name, so a
# caller learns the field is not writable rather than having it silently dropped.
# HA-VERSION-SENSITIVE: re-verify the schemas on upgrades.
#
# The price fields come in two shapes and both are offered, because a tariff is
# either fixed or it is not: number_* is a constant, entity_energy_price* names a
# sensor publishing the current rate (time-of-use), and stat_cost / stat_compensation
# name an already-existing cost statistic instead of letting HA derive one. HA
# refuses a price on a source whose meter is an EXTERNAL statistic
# (_reject_price_for_external_stat), so that combination fails at save with its own
# message rather than being second-guessed here.
_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "grid": (
        "stat_energy_from", "stat_energy_to",
        "stat_cost", "entity_energy_price", "number_energy_price",
        "stat_compensation", "entity_energy_price_export", "number_energy_price_export",
    ),
    "solar": ("stat_energy_from",),
    "battery": ("stat_energy_from", "stat_energy_to"),
    "gas": ("stat_energy_from", "stat_cost", "entity_energy_price", "number_energy_price"),
    "water": ("stat_energy_from", "stat_cost", "entity_energy_price", "number_energy_price"),
}

# Every writable field across all types, so an unknown-field refusal can name what
# the caller sent instead of only what the type accepts. Derived, never a copy.
_ALL_SOURCE_FIELDS = frozenset(f for fields in _SOURCE_FIELDS.values() for f in fields)

# What HA's schema marks vol.Required, so a CREATE that omits one would be
# refused by the dispatch schema with a message about a key the caller never saw.
_SOURCE_REQUIRED: dict[str, tuple[str, ...]] = {
    "grid": (),  # only cost_adjustment_day, which _new_source supplies
    "solar": ("stat_energy_from",),
    "battery": ("stat_energy_from", "stat_energy_to"),
    "gas": ("stat_energy_from",),
    "water": ("stat_energy_from",),
}

# Fields naming a statistic or an entity rather than holding a number. Only these
# are checked against the access key's scope, and only when being WRITTEN.
_STATISTIC_FIELDS = frozenset({
    "stat_energy_from", "stat_energy_to", "stat_cost", "stat_compensation",
    "entity_energy_price", "entity_energy_price_export",
})


class _OpError(Exception):
    """An operation that cannot be applied to the current preferences."""


def _is_external_statistic(value: str) -> bool:
    """Whether a statistic id belongs to an external source ("domain:name").

    External statistics are not entities, so they never resolve through the
    permission tree and must not be rejected for failing to.
    """
    return ":" in value


# The prefs keys holding device_consumption entries. Both are searched when
# addressing or replacing, so a water device is reachable; add_device writes to
# the electricity list, which is what "individual device" means on the dashboard.
_DEVICE_KEYS = ("device_consumption", "device_consumption_water")


def _find_device(prefs: dict, statistic: str, device_name: str) -> tuple[str, int]:
    """Locate one device entry by statistic id or by its display name.

    Addressing by NAME is not a convenience: a device whose statistic the access key
    cannot resolve comes back as the redaction sentinel, so its id is genuinely
    unavailable to the caller and the name is the only handle left. That is the
    common case for a dead entry, which is exactly what an operator wants to fix.
    Raises _OpError when nothing or more than one thing matches.
    """
    hits = [
        (key, index)
        for key in _DEVICE_KEYS
        for index, entry in enumerate(prefs.get(key) or [])
        if isinstance(entry, dict)
        and (
            (statistic and entry.get("stat_consumption") == statistic)
            or (device_name and str(entry.get("name") or "").strip().lower() == device_name.lower())
        )
    ]
    if not hits:
        addressed = statistic or device_name
        raise _OpError(f"No Energy device entry matches {addressed!r}. Call get_energy_config to see the current entries.")
    if len(hits) > 1:
        raise _OpError(
            f"{len(hits)} Energy device entries match {(statistic or device_name)!r}. "
            "Address the one you mean by its statistic id."
        )
    return hits[0]


def _replace_everywhere(prefs: dict, old: str, new: str) -> int:
    """Point every occurrence of one statistic id at another. Returns the count.

    Walks sources AND both device lists, because a statistic can legitimately
    appear in several places (a battery's charge meter, a device entry for the
    same appliance) and a migration that fixed only one would leave the rest
    pointing at an integration that is about to be removed.
    """
    changed = 0
    for source in prefs.get("energy_sources") or []:
        if not isinstance(source, dict):
            continue
        for field in _STATISTIC_FIELDS | {"stat_cost", "stat_compensation", "entity_energy_price", "stat_rate"}:
            if source.get(field) == old:
                source[field] = new
                changed += 1
    for key in _DEVICE_KEYS:
        for entry in prefs.get(key) or []:
            if isinstance(entry, dict):
                for field in ("stat_consumption", "stat_rate", "included_in_stat"):
                    if entry.get(field) == old:
                        entry[field] = new
                        changed += 1
    return changed


# HA's per-source CROSS-FIELD rules, mirrored from the validators wired onto
# *_SOURCE_SCHEMA in components/energy/data.py. Each is (import-side, export-side)
# key groups: the meter, its price-entity, its price-number, and its cost stat.
# Only grid has an export side; gas and water carry the import group alone, and
# solar/battery have no cost fields at all.
#
# WHY THESE ARE MIRRORED rather than left to HA: per-field validation cannot see
# them, so a call that sets both a price entity AND a price number would pass every
# other check, become a pending approval, and only fail at save after an admin
# approved it. Validating before the approval is created avoids that.
# HA-VERSION-SENSITIVE: re-verify the validators on upgrades.
_PRICE_GROUPS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "grid": (
        ("stat_energy_from", "entity_energy_price", "number_energy_price", "stat_cost"),
        ("stat_energy_to", "entity_energy_price_export", "number_energy_price_export", "stat_compensation"),
    ),
    "gas": (("stat_energy_from", "entity_energy_price", "number_energy_price", "stat_cost"),),
    "water": (("stat_energy_from", "entity_energy_price", "number_energy_price", "stat_cost"),),
}


def _check_source_cross_fields(source: dict, source_type: str) -> None:
    """Refuse a source HA's own schema would reject for a cross-field reason.

    Called on the MERGED source (existing values plus the caller's), because every
    rule here reads more than one field and an update supplies only some of them.
    Raises _OpError with HA's own wording, so the message a caller sees pre-gate is
    the message they would have seen at save.
    """
    for meter, entity_price, number_price, cost_stat in _PRICE_GROUPS.get(source_type, ()):
        side = "export" if meter == "stat_energy_to" else "import"
        if source.get(entity_price) is not None and source.get(number_price) is not None:
            raise _OpError(
                f"Define either an entity or a fixed number for {side} price, not both "
                f"({entity_price} and {number_price}). Set the one you do not want to null."
            )
        stat_id = source.get(meter)
        # A price cannot be derived for an EXTERNAL statistic; HA wants a ready-made
        # cost statistic instead, and skips this rule when one is already set.
        if (
            stat_id is not None
            and not valid_entity_id(stat_id)
            and source.get(cost_stat) is None
            and (source.get(entity_price) is not None or source.get(number_price) is not None)
        ):
            raise _OpError(
                f"{meter} is an external statistic, so a price cannot be applied to it. "
                f"Set {cost_stat} to an existing cost statistic instead."
            )
    if source_type == "grid" and not any(
        source.get(k) is not None for k in ("stat_energy_from", "stat_energy_to", "stat_rate", "power_config")
    ):
        raise _OpError(
            "A grid source must keep at least one of an import meter, an export meter, "
            "or a power sensor."
        )


def _new_source(source_type: str) -> dict:
    """A minimal valid source of one type, ready for the caller's fields.

    cost_adjustment_day is HA's only vol.Required field on a grid source and has
    no sensible caller-facing meaning, so it is supplied rather than asked for.
    """
    source: dict[str, Any] = {"type": source_type}
    if source_type == "grid":
        source["cost_adjustment_day"] = 0.0
    return source


def _check_writable_statistic(value: Any, field: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> str:
    """Validate one statistic id the caller wants WRITTEN into the preferences.

    Applied only to values being written, never to the ones used to ADDRESS an
    existing entry: a dead entry is addressed by the very id that no longer
    resolves, and refusing that would make the broken entries unfixable, which is
    half the reason this tool exists.
    """
    statistic = str_arg(value).strip()
    if not statistic:
        raise _OpError(f"{field} must be an entity id or an external statistic id.")
    if _is_external_statistic(statistic):
        return statistic
    if not valid_entity_id(statistic):
        raise _OpError(f"{field}: {statistic!r} is not a valid entity id or external statistic id.")
    if resolve(statistic, access_key, hass) in (Permission.NO_ACCESS, Permission.DENY, Permission.NOT_FOUND):
        # Byte-identical for out-of-scope and nonexistent, so the refusal is not an
        # existence oracle. Naming the field is safe: it is the caller's own argument.
        raise _OpError(
            f"{field}: {statistic!r} is not an entity this access key can use. "
            "Check the entity id with search_entities."
        )
    return statistic


def _source_fields_arg(args: dict, source_type: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """The caller's source fields, validated against the type's own schema.

    An explicit null is kept rather than skipped: clearing a price or a cost
    statistic is a real edit, and dropping it would silently turn "unset this" into
    a no-op the caller has no way to notice.
    """
    supplied = {field: args[field] for field in _SOURCE_FIELDS[source_type] if field in args}
    unknown = [f for f in _ALL_SOURCE_FIELDS if f in args and f not in _SOURCE_FIELDS[source_type]]
    if unknown:
        raise _OpError(
            f"A {source_type} source does not take {', '.join(sorted(unknown))}. "
            f"It accepts: {', '.join(_SOURCE_FIELDS[source_type])}."
        )
    if not supplied:
        raise _OpError(
            f"set_source needs at least one field to set. A {source_type} source accepts: "
            f"{', '.join(_SOURCE_FIELDS[source_type])}."
        )
    out: dict[str, Any] = {}
    for field, value in supplied.items():
        if value is None:
            out[field] = None
            continue
        if field in _STATISTIC_FIELDS:
            out[field] = _check_writable_statistic(value, field, access_key, hass)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _OpError(f"{field} must be a number or null.")
        try:
            out[field] = float(value)
            if not math.isfinite(out[field]):
                raise _OpError(f"{field} must be a finite number or null.")
        except (TypeError, ValueError, OverflowError):
            raise _OpError(f"{field} must be a number or null.") from None
    return out


def _apply_op(prefs: dict, args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> tuple[dict, dict]:
    """Apply ONE addressed operation to a copy of prefs.

    Returns (new_prefs, summary), where summary carries the fields the diff and
    version templates render. Raises _OpError with a caller-facing message for
    anything that cannot be applied; the caller maps that to invalid_request.
    """
    op = str_arg(args.get("op")).strip().lower()
    if op not in _OPS:
        raise _OpError(f"op must be one of: {', '.join(_OPS)}.")
    new = copy.deepcopy(prefs)
    statistic = str_arg(args.get("statistic")).strip()
    device_name = str_arg(args.get("device_name")).strip()

    if op == "replace_statistic":
        new_statistic = _check_writable_statistic(args.get("new_statistic"), "new_statistic", access_key, hass)
        old = statistic
        label = device_name or statistic
        if not old:
            if not device_name:
                raise _OpError("replace_statistic needs either statistic or device_name to say what to repoint.")
            key, index = _find_device(new, "", device_name)
            old = str_arg(new[key][index].get("stat_consumption")).strip()
            label = str(new[key][index].get("name") or device_name)
            if not old:
                raise _OpError(f"The Energy device entry {device_name!r} has no statistic to replace.")
        if old == new_statistic:
            raise _OpError(f"{new_statistic} is already what {label!r} points at.")
        if _replace_everywhere(new, old, new_statistic) == 0:
            raise _OpError(f"{old!r} is not referenced anywhere in the Energy configuration.")
        return new, {"op": op, "label": label, "old_statistic": old, "new_statistic": new_statistic}

    if op == "add_device":
        new_statistic = _check_writable_statistic(args.get("statistic"), "statistic", access_key, hass)
        for key in _DEVICE_KEYS:
            for entry in new.get(key) or []:
                if isinstance(entry, dict) and entry.get("stat_consumption") == new_statistic:
                    raise _OpError(f"{new_statistic} is already tracked as an individual device.")
        name = str_arg(args.get("name")).strip()
        added: dict[str, Any] = {"stat_consumption": new_statistic}
        if name:
            added["name"] = name
        new.setdefault("device_consumption", []).append(added)
        return new, {"op": op, "label": name or new_statistic, "new_statistic": new_statistic}

    if op == "remove_device":
        if not statistic and not device_name:
            raise _OpError("remove_device needs either statistic or device_name to say what to remove.")
        key, index = _find_device(new, statistic, device_name)
        removed = new[key].pop(index)
        return new, {
            "op": op,
            "label": str(removed.get("name") or removed.get("stat_consumption") or device_name or statistic),
            "old_statistic": str(removed.get("stat_consumption") or ""),
        }

    if op == "rename_device":
        if not statistic and not device_name:
            raise _OpError("rename_device needs either statistic or device_name to say which entry to rename.")
        name = str_arg(args.get("name")).strip()
        if not name:
            raise _OpError("rename_device needs a name.")
        key, index = _find_device(new, statistic, device_name)
        entry = new[key][index]
        previous = str(entry.get("name") or entry.get("stat_consumption") or "")
        if previous == name:
            raise _OpError(f"That entry is already called {name!r}.")
        entry["name"] = name
        return new, {"op": op, "label": name, "old_label": previous}

    if op == "remove_source":
        source_type = str_arg(args.get("source_type")).strip().lower()
        if source_type not in _SOURCE_TYPES:
            raise _OpError(f"source_type must be one of: {', '.join(_SOURCE_TYPES)}.")
        sources = new.get("energy_sources") or []
        matches = [i for i, s in enumerate(sources) if isinstance(s, dict) and s.get("type") == source_type]
        if not matches:
            raise _OpError(f"There is no {source_type} source to remove.")
        if len(matches) > 1:
            # HA allows several of a type (multiple grid connections, several
            # batteries). Removing "the first one" would be a coin flip on which of
            # the operator's meters stops being counted, so refuse and say so.
            raise _OpError(
                f"There are {len(matches)} {source_type} sources. Removing one by type would be "
                "ambiguous; remove it in the Energy dashboard UI."
            )
        removed = sources.pop(matches[0])
        return new, {
            "op": op,
            "source_type": source_type,
            "label": source_type,
            "old_statistic": str(removed.get("stat_energy_from") or ""),
        }

    source_type = str_arg(args.get("source_type")).strip().lower()
    if source_type not in _SOURCE_TYPES:
        raise _OpError(f"source_type must be one of: {', '.join(_SOURCE_TYPES)}.")
    fields = _source_fields_arg(args, source_type, access_key, hass)
    sources = new.setdefault("energy_sources", [])
    existing = next(
        (s for s in sources if isinstance(s, dict) and s.get("type") == source_type), None
    )
    if existing is None:
        missing = [f for f in _SOURCE_REQUIRED[source_type] if f not in fields]
        if missing:
            raise _OpError(
                f"There is no {source_type} source yet, and creating one requires "
                f"{', '.join(missing)}."
            )
        source = _new_source(source_type)
        source.update(fields)
        _check_source_cross_fields(source, source_type)
        sources.append(source)
        action = "create"
    else:
        # Checked on the MERGED result, not the caller's fields alone: an update
        # supplying only a price has to be judged against the meter already there.
        merged = {**existing, **fields}
        _check_source_cross_fields(merged, source_type)
        existing.update(fields)
        action = "update"
    return new, {
        "op": f"set_source.{action}",
        "source_type": source_type,
        "fields": ", ".join(sorted(fields)),
        "label": source_type,
    }


def _verify_structure(before: dict, after: dict, op: str) -> None:
    """Refuse a result that changed more than the operation is allowed to change.

    The backstop for HA's full-replace write semantics: this is what would catch a
    bug in _apply_op before it reaches the wire, rather than after the operator's
    other entries are already gone. Counts only, because every op here has an
    exact, knowable effect on the list lengths.
    """
    def counts(prefs: dict) -> tuple[int, int, int]:
        return tuple(len(prefs.get(key) or []) for key in _PREFS_KEYS)  # type: ignore[return-value]

    sources_before, elec_before, water_before = counts(before)
    sources_after, elec_after, water_after = counts(after)
    devices_before, devices_after = elec_before + water_before, elec_after + water_after

    if op in ("replace_statistic", "set_source.update", "rename_device"):
        allowed = sources_after == sources_before and devices_after == devices_before
    elif op == "set_source.create":
        allowed = sources_after == sources_before + 1 and devices_after == devices_before
    elif op == "add_device":
        allowed = sources_after == sources_before and devices_after == devices_before + 1
    elif op == "remove_device":
        allowed = sources_after == sources_before and devices_after == devices_before - 1
    elif op == "remove_source":
        allowed = sources_after == sources_before - 1 and devices_after == devices_before
    else:  # pragma: no cover - _apply_op already rejected an unknown op
        allowed = False
    if not allowed:
        raise _OpError(
            "Refusing to write: the change would alter the Energy configuration beyond "
            f"what '{op}' should touch "
            f"({sources_before} sources / {devices_before} devices before, "
            f"{sources_after} / {devices_after} after)."
        )


async def _read_prefs_raw(hass: HomeAssistant) -> dict:
    """The current preferences, UNREDACTED, defaulting when nothing is configured.

    Unredacted is the point: this is the base a write is applied to, so an entry
    the calling access key cannot resolve must survive as its real statistic id rather
    than being rewritten to the redaction sentinel.
    """
    try:
        prefs = await async_ws_command(hass, "energy/get_prefs", {})
    except WsDispatchError as err:
        if any(marker in str(err).lower() for marker in _NO_CONFIG_ERRORS):
            return _default_prefs()
        raise
    return prefs if isinstance(prefs, dict) else _default_prefs()


def _sentinel_refusal(args: dict) -> str | None:
    """Refuse a caller-supplied value carrying the redaction placeholder.

    A redacted placeholder is never written back as configuration. Only the
    caller's own arguments can introduce one, because no part of the stored
    configuration is resent through this tool.
    """
    for field in ("statistic", "new_statistic", "name", "device_name", "stat_energy_from", "stat_energy_to"):
        if field in args and redaction_sentinel_path(args[field]) is not None:
            return (
                f"{field} contains the redaction placeholder {REDACTION_SENTINEL!r}. That placeholder is "
                "what a read substitutes for an entity this access key cannot resolve, so it names nothing. "
                "Address the entry by device_name instead, or send the real entity id."
            )
    return None


def _preview_rows(prefs: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """A compact, scoped view of the preferences for the panel's visual preview.

    Structured rows rather than a JSON string: diff.before/after go through
    _truncate, and a truncated JSON string does not parse. These rows are small
    enough never to be truncated.

    Scoped through filter_service_response, so a statistic the access key cannot
    resolve previews as the sentinel rather than being disclosed to whoever opens
    the approval.
    """
    scoped = filter_service_response(prefs, access_key, hass)
    sources = []
    for source in scoped.get("energy_sources") or []:
        if not isinstance(source, dict):
            continue
        sources.append({
            "type": source.get("type"),
            "name": source.get("name"),
            # Ordered pairs rather than a dict so the panel renders import before
            # export without needing to know the field names itself.
            "meters": [
                [field, source[field]]
                for field in ("stat_energy_from", "stat_energy_to")
                if source.get(field)
            ],
            "price": source.get("number_energy_price") if source.get("number_energy_price") is not None
                     else source.get("entity_energy_price"),
        })
    devices = []
    for key in _DEVICE_KEYS:
        for entry in scoped.get(key) or []:
            if isinstance(entry, dict):
                devices.append({
                    "name": entry.get("name"),
                    "statistic": entry.get("stat_consumption"),
                    "water": key.endswith("_water"),
                })
    return {"sources": sources, "devices": devices}


async def _build_diff_edit_energy_config(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Approval diff for one Energy edit, in the operator's terms.

    Best-effort and never raises: the precheck has already validated, and a diff
    that failed to build must not turn an allowed call into an error.
    """
    diff: dict[str, Any] = {
        "kind": "config_diff",
        "target": {"type": "energy", "id": "preferences", "label": "Energy dashboard"},
    }
    try:
        before = await _read_prefs_raw(hass)
        after, summary = _apply_op(before, args, access_key, hass)
        diff.update(_summary(f"edit_energy_config.{summary['op']}", **{
            k: v for k, v in summary.items() if k != "op"
        }))
        diff["before"] = _truncate(json.dumps(filter_service_response(before, access_key, hass), indent=2, default=str))
        diff["after"] = _truncate(json.dumps(filter_service_response(after, access_key, hass), indent=2, default=str))
        diff["preview"] = {
            **{k: v for k, v in summary.items() if k != "op"},
            "energy": {"before": _preview_rows(before, access_key, hass), "after": _preview_rows(after, access_key, hass)},
        }
    except Exception:  # noqa: BLE001 - diagnostic only
        diff.setdefault("summary", "Change the Energy dashboard configuration")
    return diff


async def _tool_edit_energy_config(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: change ONE addressed part of the Energy dashboard (Confirm-gated).

    Never takes a preferences blob (see the module docstring). Validated against
    the CURRENT preferences before the approval is created, after the cap-deny
    check so a denied access key learns nothing about its payload, so an invalid
    request never becomes a pending approval. Re-validated at apply time so a
    change during the approval window is caught.
    """
    tool = "edit_energy_config"
    if effective_cap(access_key, "cap_energy_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    args, error = normalize_tool_args(tool, args)
    if error:
        return _tool_error(error), "invalid_request", tool
    refusal = _sentinel_refusal(args)
    if refusal is not None:
        return _tool_error(refusal), "invalid_request", tool
    try:
        before = await _read_prefs_raw(hass)
        after, summary = _apply_op(before, args, access_key, hass)
        _verify_structure(before, after, summary["op"])
    except _OpError as err:
        return _tool_error(str(err)), "invalid_request", tool
    except WsDispatchError:
        _LOGGER.exception("edit_energy_config: could not read the current preferences")
        return _tool_error("Could not read the Energy dashboard configuration."), "invalid_request", tool

    blocked = await _gate(
        "cap_energy_write", access_key, hass, data,
        tool_name=tool, args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_edit_energy_config(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_edit_energy_config(args, access_key, hass, data)


async def _execute_edit_energy_config(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Side-effect path for edit_energy_config. Assumes the capability is satisfied."""
    tool = "edit_energy_config"
    if effective_cap(access_key, "cap_energy_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    try:
        before = await _read_prefs_raw(hass)
        after, summary = _apply_op(before, args, access_key, hass)
        _verify_structure(before, after, summary["op"])
    except _OpError as err:
        return _tool_error(str(err)), "invalid_request", tool
    except WsDispatchError:
        _LOGGER.exception("edit_energy_config: could not read the current preferences")
        return _tool_error("Could not read the Energy dashboard configuration."), "invalid_request", tool

    try:
        recovery = data.config_recovery
        resource = "energy:preferences"
        async with recovery.resource(resource):
            # Persist the exact raw preferences before dispatch. This is an
            # Energy-owned snapshot, independent of the redacted agent view.
            before_snapshot = _energy_recovery_snapshot(before)
            rid = await recovery.begin(resource, tool, access_key.id, before_snapshot,
                expected=_energy_recovery_snapshot(after))
            try:
                await recovery.mark(rid, "dispatching")
                check_request_authority()
                current = await _read_prefs_raw(hass)
                if current != before:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("Energy preferences changed while preparing the safety copy; nothing was dispatched."), "invalid_request", tool
                # Send ONLY top-level keys changed by the addressed operation.
                payload = {key: after[key] for key in _PREFS_KEYS if key in after and after.get(key) != before.get(key)}
                if not payload:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("That change would leave the Energy configuration unchanged."), "invalid_request", tool
                try:
                    await async_ws_command(hass, "energy/save_prefs", payload)
                except WsDispatchError as err:
                    _LOGGER.exception("edit_energy_config: energy/save_prefs failed")
                    await recovery.mark(rid, "not_applied" if isinstance(err, WsCommandNotDispatchedError) else "unknown", error="save_rejected")
                    return _tool_error(f"Home Assistant rejected the Energy configuration: {err}"), "invalid_request", tool
                try:
                    observed = await _read_prefs_raw(hass)
                    if observed == after:
                        source_snapshot = _energy_recovery_snapshot(observed)
                        await recovery.mark(rid, "verified", observed_hash=content_hash(observed))
                        record = recovery.get(rid)
                        if record is not None:
                            # The record's source side is persisted by the family
                            # adapter only after readback confirms the committed value.
                            await recovery.set_source(rid, source_snapshot)
                    else:
                        await recovery.mark(rid, "partial", observed_hash=content_hash(observed))
                        return _tool_success(json.dumps({
                            "status": "partial", "saved": False, "recovery_id": rid,
                            "message": "Energy preferences were submitted but readback differs; inspect configuration recovery before retrying.",
                            **_scoped_body(observed, True, access_key, hass),
                        }, default=str)), "allowed", "energy:preferences"
                except Exception:  # noqa: BLE001 - a committed write with failed readback is uncertain
                    await recovery.mark(rid, "unknown", readback="unavailable")
                    return _tool_error("Energy preferences were submitted, but readback is incomplete; inspect configuration recovery before retrying."), "invalid_request", tool
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as err:
        return _tool_error(str(err)), "invalid_request", tool
    except Exception:  # noqa: BLE001 - never claim a saved Energy write without its record
        _LOGGER.exception("edit_energy_config recovery persistence failed")
        return _tool_error("Could not verify the Energy configuration change; inspect configuration recovery before retrying."), "invalid_request", tool

    await _record_version(
        data, access_key, resource_type="energy", resource_id="preferences",
        action="edit", before=before, after=after, alias="Energy dashboard",
        summary=_version_summary(
            "energy.sources",
            count=len(after.get("energy_sources") or []),
            devices=sum(len(after.get(key) or []) for key in _DEVICE_KEYS),
        ),
    )
    body = {
        "saved": True, "recovery_id": rid,
        **{k: v for k, v in summary.items()}, **_scoped_body(after, True, access_key, hass),
    }
    return _tool_success(json.dumps(body, default=str)), "allowed", "energy:preferences"


def _energy_recovery_snapshot(prefs: dict[str, Any]) -> dict[str, Any]:
    """Serialize one raw Energy preferences snapshot without redaction loss."""
    if (not isinstance(prefs, dict)
            or any(not isinstance(prefs.get(key), list) for key in _PREFS_KEYS[:2])
            or any(key in prefs and not isinstance(prefs[key], list) for key in _PREFS_KEYS)):
        raise ConfigurationRecoveryError("A complete Energy recovery snapshot is unavailable; nothing was dispatched.")
    return safe_snapshot({
        "snapshot_type": "energy_preferences",
        "schema": 1,
        "resource_id": "preferences",
        "preferences": {key: copy.deepcopy(prefs[key]) for key in _PREFS_KEYS if key in prefs},
    }) or {}


def validate_energy_recovery_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate the Energy adapter envelope and return its complete preferences."""
    safe_snapshot(snapshot)
    prefs = snapshot.get("preferences") if isinstance(snapshot, dict) else None
    if (snapshot.get("snapshot_type") != "energy_preferences"
            or snapshot.get("schema") != 1
            or snapshot.get("resource_id") != "preferences"
            or not isinstance(prefs, dict)
            or not set(_PREFS_KEYS[:2]).issubset(prefs)
            or set(prefs) - set(_PREFS_KEYS)
            or any(not isinstance(prefs.get(key), list) for key in prefs)):
        raise ConfigurationRecoveryError("Unsupported or incomplete Energy recovery snapshot.")
    return prefs


def validate_energy_recovery_scope(
    snapshot: dict[str, Any], access_key: AccessKeyRecord, hass: HomeAssistant,
) -> None:
    """Refuse restores whose entity references exceed the access key's current scope."""
    prefs = validate_energy_recovery_snapshot(snapshot)
    for entity_id in _referenced_entities(prefs):
        try:
            _check_writable_statistic(entity_id, "recovery", access_key, hass)
        except _OpError:
            raise ConfigurationRecoveryError(
                "The Energy snapshot references an entity outside this access key's current scope."
            ) from None


async def energy_recovery_target(hass: HomeAssistant) -> dict[str, Any]:
    """Capture the live target used by restore receipts and approval binding."""
    prefs = await _read_prefs_raw(hass)
    return _energy_recovery_snapshot(prefs)


def energy_recovery_diff(before: dict[str, Any], after: dict[str, Any], side: str,
                         access_key: AccessKeyRecord, hass: HomeAssistant) -> dict[str, Any]:
    """Build a scoped, family-specific restore diff for Energy preferences."""
    old_prefs = before.get("preferences", {}) if isinstance(before, dict) else {}
    new_prefs = after["preferences"]
    return {
        "kind": "config_diff",
        "summary": "Restore Energy dashboard preferences",
        "target": {"type": "energy", "id": "preferences", "label": "Energy dashboard"},
        "before": json.dumps(filter_service_response(old_prefs, access_key, hass), indent=2, sort_keys=True, default=str),
        "after": json.dumps(filter_service_response(new_prefs, access_key, hass), indent=2, sort_keys=True, default=str),
        "preview": {"side": side, "energy": {
            "before": _scoped_body(old_prefs, True, access_key, hass),
            "after": _scoped_body(new_prefs, True, access_key, hass),
        }},
    }


async def restore_energy_configuration(snapshot: dict[str, Any], access_key: AccessKeyRecord,
                                       hass: HomeAssistant, data: PhoenixData) -> tuple[dict, str, str]:
    """Restore one Energy snapshot, recording and verifying the whole transaction."""
    tool = "restore_configuration"
    if effective_cap(access_key, "cap_energy_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", tool
    try:
        wanted = validate_energy_recovery_snapshot(snapshot)
        validate_energy_recovery_scope(snapshot, access_key, hass)
        recovery = data.config_recovery
        resource = "energy:preferences"
        async with recovery.resource(resource):
            before = await _read_prefs_raw(hass)
            before_snapshot = _energy_recovery_snapshot(before)
            rid = await recovery.begin(resource, tool, access_key.id, before_snapshot, source=snapshot, expected=snapshot)
            try:
                await recovery.mark(rid, "dispatching")
                check_request_authority()
                if await _read_prefs_raw(hass) != before:
                    await recovery.mark(rid, "not_applied")
                    return _tool_error("Energy preferences changed before restore dispatch; nothing was applied."), "invalid_request", tool
                await async_ws_command(hass, "energy/save_prefs", wanted)
                observed = await _read_prefs_raw(hass)
                if observed != wanted:
                    await recovery.mark(rid, "partial", observed_hash=content_hash(observed))
                    return _tool_success(json.dumps({
                        "status": "partial", "recovery_id": rid,
                        "message": "Energy restore was submitted but readback differs; do not retry automatically.",
                    })), "allowed", tool
                await recovery.mark(rid, "verified", observed_hash=content_hash(observed))
                await recovery.set_source(rid, _energy_recovery_snapshot(observed))
                await _record_version(
                    data, access_key, resource_type="energy", resource_id="preferences",
                    action="edit", before=before, after=observed, alias="Energy dashboard",
                    summary=_version_summary(
                        "energy.sources", count=len(observed.get("energy_sources") or []),
                        devices=sum(len(observed.get(key) or []) for key in _DEVICE_KEYS),
                    ),
                )
                return _tool_success(json.dumps({
                    "status": "verified", "recovery_id": rid,
                    **_scoped_body(observed, True, access_key, hass),
                }, default=str)), "allowed", tool
            except WsDispatchError as err:
                await recovery.mark(rid, "unknown", error="save_or_readback_failed")
                return _tool_success(json.dumps({
                    "status": "unknown", "recovery_id": rid,
                    "message": f"Energy restore outcome is unknown ({err}); inspect before any retry.",
                })), "allowed", tool
            except BaseException:
                # Cancellation after dispatch deliberately leaves dispatching as
                # the durable uncertain state. Never infer that it did not apply.
                raise
            finally:
                recovery.leave(rid)
    except ConfigurationRecoveryError as err:
        return _tool_error(str(err)), "invalid_request", tool


async def async_restore_energy_prefs(
    snapshot: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Restore a whole Energy preferences snapshot (admin version rollback only).

    The one wholesale write: reproducing the chosen snapshot IS the operation, so
    the addressed-write rule the rest of this module enforces does not apply (a
    YAML version restore is exempt from its removal check for the same reason).
    Reached only from async_restore_version, never from a tool.
    """
    tool = "edit_energy_config"
    if not isinstance(snapshot, dict):
        return _tool_error("This version is not a restorable Energy configuration."), "invalid_request", tool
    payload = {key: snapshot[key] for key in _PREFS_KEYS if isinstance(snapshot.get(key), list)}
    if not payload:
        return _tool_error("This version holds no Energy configuration to restore."), "invalid_request", tool
    try:
        before = await _read_prefs_raw(hass)
        await async_ws_command(hass, "energy/save_prefs", payload)
    except WsDispatchError as err:
        _LOGGER.exception("restore energy preferences failed")
        return _tool_error(f"Home Assistant rejected the Energy configuration: {err}"), "invalid_request", tool
    await _record_version(
        data, access_key, resource_type="energy", resource_id="preferences",
        action="edit", before=before, after=snapshot, alias="Energy dashboard",
        summary=_version_summary(
            "energy.sources",
            count=len(snapshot.get("energy_sources") or []),
            devices=sum(len(snapshot.get(key) or []) for key in _DEVICE_KEYS),
        ),
    )
    return (
        _tool_success(json.dumps({"saved": True, **_scoped_body(snapshot, True, access_key, hass)}, default=str)),
        "allowed",
        "energy:preferences",
    )
