"""The published tool catalog: capability gates, fingerprint and handler registries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from homeassistant.core import HomeAssistant

from .access_key_store import AccessKeyRecord
from .const import CALL_SERVICE_NO_TARGET_CAPS, CAP_DENY, HOME_COMPANION_TOOLS
from .data import PhoenixData
from .helpers import access_key_has_write_scope, content_hash, effective_cap
from .home_companion import is_companion, internal_turn, wrapper_tool
from .household_authority import household_tool_access
from .mesa_tools import mesa_tool_defs
from .tool_defs import (
    _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS, _NATIVE_TOOL_NAMES, _NATIVE_TOOL_PUBLIC_TO_INTERNAL,
    _SYSTEM_TOOL_DEFS, _TOOL_ANNOTATIONS,
)
from .tools.discovery import _requires_satisfied


# Stamped onto the defs at import: a tool added without an annotation raises
# KeyError here rather than shipping with the spec's unsafe defaults.
for _def in (*_ENTITY_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *_SYSTEM_TOOL_DEFS):
    _def["annotations"] = _TOOL_ANNOTATIONS[_def["name"]]
del _def


def _catalog_fingerprint() -> str:
    """Hash of every tool NAME and inputSchema this build defines.

    The second staleness signal, alongside settings_version: that one tracks the
    operator changing an access key, this one tracks the code changing under a client
    that already fetched its tool list. A client enforces its cached schema
    before sending, so an argument added after that fetch is unusable to it and
    no server-side leniency helps; advising a reconnect is the only remedy.

    Hashes the FULL static catalog, never the per-access-key announced subset, so a
    permission change does not masquerade as a deploy. Descriptions are excluded:
    reworded guidance needs no refetch, and counting prose edits as staleness
    would fire the notice constantly and train operators to ignore it.
    """
    catalog = [
        (d["name"], d.get("inputSchema"))
        for d in (*_ENTITY_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *_SYSTEM_TOOL_DEFS, *mesa_tool_defs())
    ]
    return content_hash(sorted(catalog, key=lambda row: row[0]))[:16]


_TOOL_CATALOG_FINGERPRINT = _catalog_fingerprint()


def tool_catalog_counts() -> dict[str, int]:
    """The tool count, from the registry itself. The ONLY place it is defined.

    Every other statement of the count derives from here: the docs contract
    tests and the admin info endpoint. Native means HA's own
    domain-prefixed MCP tool names, which Phoenix implements 1:1; additional is
    everything Phoenix defines beyond them, mesa_* included.
    """
    names = {
        d["name"]
        for d in (*_ENTITY_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *_SYSTEM_TOOL_DEFS, *mesa_tool_defs())
    }
    native = names & set(_NATIVE_TOOL_NAMES.values())
    return {"total": len(names), "native": len(native), "additional": len(names - native)}


# Tools that perform writes/actions. They are announced in tools/list only when
# the access key has write scope (any GREEN grant or pass_through). Cap-tied tools
# (those with a "cap", "caps" or "caps_any" key) gate on their capability, and a
# tool that is both needs both; all remaining tools (reads, GetDateTime, get_approval_status) are
# always announced. The announce_all_tools access key flag overrides this gating
# entirely.
_WRITE_GATED_TOOLS = frozenset({
    "call_service", "set_todo_item",
    "HassTurnOn", "HassTurnOff", "HassLightSet", "HassFanSetSpeed",
    "HassClimateSetTemperature", "HassSetPosition", "HassSetVolume",
    "HassSetVolumeRelative", "HassMediaPause", "HassMediaUnpause",
    "HassMediaNext", "HassMediaPrevious", "HassMediaSearchAndPlay",
    "HassMediaPlayerMute", "HassMediaPlayerUnmute", "HassCancelAllTimers",
    "HassStopMoving",
    "HassVacuumStart", "HassVacuumReturnToBase", "HassVacuumCleanArea",
})


def _tool_required_caps(tool_def: dict) -> tuple[str, ...]:
    """Return capabilities which are all required."""
    caps = tool_def.get("caps")
    if isinstance(caps, (list, tuple)):
        return tuple(cap for cap in caps if isinstance(cap, str))
    cap = tool_def.get("cap")
    return (cap,) if isinstance(cap, str) else ()


def _tool_any_caps(tool_def: dict) -> tuple[str, ...]:
    """Return capabilities for which any non-Deny grant makes a tool visible."""
    caps = tool_def.get("caps_any")
    if isinstance(caps, (list, tuple)):
        return tuple(cap for cap in caps if isinstance(cap, str))
    return ()


def _tool_caps(tool_def: dict) -> tuple[str, ...]:
    return _tool_required_caps(tool_def) + _tool_any_caps(tool_def)


def _write_gate_open(internal_name: str, access_key: AccessKeyRecord, has_write: bool) -> bool:
    """Whether a write-gated tool can do anything for this access key."""
    return has_write or (internal_name == "call_service" and any(
        effective_cap(access_key, cap) != CAP_DENY for cap in CALL_SERVICE_NO_TARGET_CAPS
    ))


def _tool_is_announced(
    tool_def: dict, access_key: AccessKeyRecord, has_write: bool, hass: HomeAssistant
) -> bool:
    """Whether a tool should appear in tools/list for this access key.

    cap-tied tools gate on their capability; write/action tools gate on write
    scope; a tool carrying a "requires" key additionally needs that system
    surface to exist; everything else (reads, GetDateTime, get_approval_status)
    is always announced. The caller applies the announce_all_tools override
    separately.

    TWO MIRRORS: this predicate and _tool_gate_map (get_capability_summary)
    decide announcement independently; build_mcp_tool_list (Agent Chat,
    Assist, voice, AI Task) calls this one. Change both together.
    """
    caps = _tool_required_caps(tool_def)
    any_caps = _tool_any_caps(tool_def)
    if any(effective_cap(access_key, cap) == CAP_DENY for cap in caps):
        return False
    if any_caps and all(effective_cap(access_key, cap) == CAP_DENY for cap in any_caps):
        return False
    internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(
        tool_def["name"], tool_def["name"]
    )
    # A write tool that also names a capability needs both, so a whole-tool
    # physical gate never announces a tool the key has no target for.
    if internal_name in _WRITE_GATED_TOOLS and not _write_gate_open(
        internal_name, access_key, has_write
    ):
        return False
    return _requires_satisfied(tool_def, hass)


# Executor registry for the admin-approval gate. When an admin approves a pending
# request, the approve handler looks up the saved tool_name here and invokes the
# corresponding _execute_X function with the saved args.
_EXECUTOR_REGISTRY: dict[str, Any] = {}


# A Home Companion request runs the agent loop, which dispatches its own tool calls,
# so the dispatcher reaches that turn through this slot instead of importing it.
_COMPANION_TURN: list[Any] = []


def _register_companion_turn(fn: Any) -> None:
    """Record the function that runs a home_request. Called once at module import."""
    _COMPANION_TURN[:] = [fn]


async def run_companion_turn(arguments: dict, access_key: Any, hass: HomeAssistant, data: Any) -> tuple:
    """Run a home_request through the registered companion turn."""
    return await _COMPANION_TURN[0](arguments, access_key, hass, data)


def _register_executor(tool_name: str, fn: Any) -> None:
    """Record an executor function for a tool. Called once at module import."""
    _EXECUTOR_REGISTRY[tool_name] = fn


# Tool dispatch registry: name -> (handler, context params it accepts, bound kwargs).
# Populated by tool_registration, once every handler is defined.
_TOOL_HANDLERS: dict[str, tuple[Any, tuple[str, ...], dict[str, Any]]] = {}

# Request context a handler opts into by naming the parameter. Reads take
# (args, access_key, hass); gated writes add all three of these.
_TOOL_CONTEXT_PARAMS = ("data", "request_id", "client_ip")


def _register_tool(name: str, handler: Any, /, **bound: Any) -> None:
    """Bind a tool name to its handler. Called once per tool at module import.

    The handler's parameter names are read from its code object (not
    inspect.signature, which can evaluate annotations eagerly on Python 3.14) so
    the dispatcher passes just the context each one takes, instead of every
    handler accepting a uniform signature it mostly ignores. bound supplies
    per-registration constants, which is how the dashboard card ops and the
    mesa_* tools share one handler each.

    name and handler are positional-only so that no bound kwarg (the card ops
    bind their own tool_name) can collide with this function's own parameters.
    """
    if name in _TOOL_HANDLERS:
        raise ValueError(f"duplicate tool registration: {name}")
    code = handler.__code__
    accepted = set(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])
    wanted = tuple(p for p in _TOOL_CONTEXT_PARAMS if p in accepted)
    _TOOL_HANDLERS[name] = (handler, wanted, bound)


_TOOL_DEFS_BY_NAME: dict[str, dict] = {
    definition["name"]: definition
    for definition in (*_ENTITY_TOOL_DEFS, *_SYSTEM_TOOL_DEFS, *_NATIVE_TOOL_DEFS, *mesa_tool_defs())
}




# --------------------------------------------------------------------------- #
# Tool list (mirrors the MCP tools/list gating) and result helpers
# --------------------------------------------------------------------------- #

# Orientation and everyday tools listed first: models weight early tools more
# (weak local ones especially), and the system prompt tells the agent to orient
# via get_capability_summary/get_overview/search_entities before acting, so
# those should not sit buried mid-list. Everything else keeps its source order
# (the sort is stable). Deterministic across turns, so it never busts the
# provider prompt cache.
_TOOL_PRIORITY = (
    "get_capability_summary", "get_overview", "search_entities", "recognize_intent",
    "describe_entity", "describe_area", "get_state",
    "call_service", "dry_run_service", "find_available_actions",
)


def build_mcp_tool_list(access_key: AccessKeyRecord, data: PhoenixData) -> list[dict]:
    """The tool defs this access key would announce, cap key stripped (mirrors tools/list)."""

    if is_companion(access_key) and not internal_turn(access_key):
        return [wrapper_tool()]
    announce_all = getattr(access_key, "announce_all_tools", False) and not is_companion(access_key)
    has_write = access_key_has_write_scope(access_key)
    hass = getattr(data, "hass", None)
    mesa_defs = mesa_tool_defs() if getattr(data, "mesa", None) is not None else []
    out = []
    for d in list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + mesa_defs:
        if household_tool_access(d["name"]) and (announce_all or _tool_is_announced(d, access_key, has_write, cast(Any, hass))):
            out.append({k: v for k, v in d.items() if k not in ("cap", "caps", "caps_any", "requires")})
    if is_companion(access_key):
        out = [tool for tool in out if tool["name"] in HOME_COMPANION_TOOLS]
    rank = {name: i for i, name in enumerate(_TOOL_PRIORITY)}
    out.sort(key=lambda t: rank.get(t["name"], len(rank)))
    return out


def build_agent_tool_list(access_key: AccessKeyRecord, data: PhoenixData, surface: str) -> list[dict]:
    """Share each planner's complete announced catalog with tool review."""
    tools = build_mcp_tool_list(access_key, data)
    # Chat already supplies current local time in every turn's system context.
    return deepcopy([tool for tool in tools if surface != "chat"
                     or tool.get("name") not in ("llm__GetDateTime", "GetDateTime")])
