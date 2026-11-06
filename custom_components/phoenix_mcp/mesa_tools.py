"""Access-key-scoped wrappers around the vendored mesa-core retrieval tools.

A per-request ScopedProfileStore shows the four handlers only entities the key may read, so
counts, cursors and fingerprints stay scope-relative. Out-of-scope and ghost lookups return
the identical not_found envelope.
"""

from __future__ import annotations

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er_mod

import hashlib
import json
import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .const import CAP_DENY, MAX_PREVIEW_ENTITY_IDS
from .capability import effective_cap
from .redaction import redact_inaccessible_entity_ids_in_text
from .mesa import async_semantic_moments, build_caller_context
from .mesa_core import InheritanceResolver, ProfileQueryResult, ProfileStore
from .mesa_core.mcp.schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS
from .mesa_core.mcp.tools import MesaToolHandlers
from .policy_engine import Permission, resolve, resolve_read
from .tool_defs import _MESA_TOOL_ANNOTATIONS

if TYPE_CHECKING:
    from .data import PhoenixData
    from .mesa import MesaRuntime
    from .mesa_core import SemanticProfile
    from .access_key_store import AccessKeyRecord

# Tools whose entity_id argument must be scope-checked before the handler runs.
_ENTITY_TARGETED = frozenset({"mesa_get_profile", "mesa_explain_profile"})

# The advisory-lease tools. They run with session_id = access_key.id, as the stateless
# transport's per-request id would make release or refresh impossible. A lease therefore
# excludes other keys, not other callers of one key: mesa-core treats same-session overlap
# as a refresh, matching the one-key-per-agent model.
_LEASE_TOOLS = frozenset({"mesa_request_lease", "mesa_release_lease"})

# All six mesa_* tools (four retrieval plus the two lease tools) are gated by
# this capability: profiles are configuration metadata, the same sensitivity
# class as get_config.
MESA_TOOLS_CAP = "cap_config_read"


_MESA_PARAM_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "mesa_query_profiles": {
        "domains": "Optional entity domains to match.",
        "tags": "Optional semantic tags to match.",
        "tags_match": "Whether any or all requested tags must match.",
        "areas": "Optional Home Assistant area IDs or names to match.",
        "intents": "Optional semantic intent names to match.",
        "min_origin_authority": "Minimum accepted profile-origin authority.",
        "include_inferred": "Whether inferred effective profiles may be returned.",
        "include_fields": "Optional profile fields to include in each row.",
        "limit": "Maximum profile rows to return.",
        "cursor": "Opaque cursor from the previous page with the same filters.",
    },
    "mesa_get_profile": {
        "entity_id": "Accessible entity whose effective MESA profile to return.",
        "include_diagnostic": "Whether to include profile-resolution diagnostics.",
    },
    "mesa_explain_profile": {
        "entity_id": "Accessible entity whose MESA inheritance to explain.",
        "show_conflicts": "Whether to include conflicting inherited values.",
    },
    "mesa_request_lease": {
        "entities": "Entity IDs the advisory lease should cover.",
        "intent": "Short description of the operation protected by the lease.",
        "priority_level": "Requested advisory lease priority.",
        "preemption_handling": "How competing higher-priority leases should be handled.",
    },
    "mesa_release_lease": {
        "lease_id": "Lease identifier returned by mesa_request_lease.",
    },
}


def _described_mesa_schema(name: str) -> dict[str, Any]:
    """Copy a vendored schema and add Phoenix's public parameter prose."""
    schema = copy.deepcopy(TOOL_SCHEMAS[name])
    for parameter, description in _MESA_PARAM_DESCRIPTIONS.get(name, {}).items():
        schema["properties"][parameter]["description"] = description
    # Phoenix's copy: a closed root like every other tool.
    schema.setdefault("additionalProperties", False)
    return schema


def mesa_tool_defs() -> list[dict[str, Any]]:
    """tools/list entries for the mesa_* tools, tagged with the gating cap.

    Built from the vendored MCP schemas/descriptions so Phoenix MCP never re-specifies
    them. The tool list strips the 'cap' key before exposing the tool.
    """
    return [
        {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "cap": MESA_TOOLS_CAP,
            "inputSchema": _described_mesa_schema(name),
            "annotations": _MESA_TOOL_ANNOTATIONS[name],
        }
        for name in ("mesa_query_profiles", "mesa_get_profile",
                     "mesa_explain_profile", "mesa_get_caller_context",
                     "mesa_request_lease", "mesa_release_lease")
    ]


def _not_found_envelope(entity_id: str) -> dict[str, Any]:
    """Replicate mesa_core.mcp.tools._error('not_found', ...) byte-for-byte."""
    return {
        "error": "not_found",
        "message": f"entity {entity_id!r} has no MESA profile at any level",
        "details": {},
    }


def _is_visible(entity_id: str, access_key: AccessKeyRecord, hass: HomeAssistant) -> bool:
    """True when the access key may read the entity (READ or WRITE)."""
    return resolve(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)


class ScopedProfileStore:
    """A read-only ProfileStore view filtered to one access key's permission scope.

    Entity reads are hidden; inheritance layers pass through unfiltered, since they only
    tighten and hiding one could loosen a profile. device_keys, area_keys and integration_keys
    are not exposed, as they could enumerate. Visibility is decided once, on the event loop.
    """

    def __init__(self, inner, access_key: AccessKeyRecord, hass: HomeAssistant) -> None:
        self._inner = inner
        self._visible = frozenset(k for k in inner.entity_keys() if _is_visible(k, access_key, hass))

    def entity_keys(self) -> list[str]:
        return [k for k in self._inner.entity_keys() if k in self._visible]

    def get(self, entity_id: str) -> SemanticProfile | None:
        if entity_id not in self._visible:
            return None
        return self._inner.get(entity_id)

    def get_domain_profile(self, domain: str) -> SemanticProfile | None:
        return self._inner.get_domain_profile(domain)

    def get_integration_profile(self, integration: str) -> SemanticProfile | None:
        return self._inner.get_integration_profile(integration)

    def get_area_profile(self, area_id: str) -> SemanticProfile | None:
        return self._inner.get_area_profile(area_id)

    def get_device_profile(self, device_id: str) -> SemanticProfile | None:
        return self._inner.get_device_profile(device_id)

    def get_deployment_defaults(self) -> Any:
        return self._inner.get_deployment_defaults()

    @property
    def get_entity_area(self) -> Any:
        return self._inner.get_entity_area

    @property
    def get_entity_integration(self) -> Any:
        return self._inner.get_entity_integration

    @property
    def get_entity_device(self) -> Any:
        return self._inner.get_entity_device

    @property
    def get_device_parent(self) -> Any:
        return self._inner.get_device_parent

    @property
    def get_entity_domain(self) -> Any:
        return self._inner.get_entity_domain

    def _fingerprint(self) -> str:
        digest = hashlib.sha256("|".join(self.entity_keys()).encode())
        return digest.hexdigest()[:16]

    def query(self, **kwargs: Any) -> ProfileQueryResult:
        """Run mesa-core's profile query over this view, keeping results scope-relative."""
        return ProfileStore.query(self, **kwargs)  # type: ignore[arg-type]  # ScopedProfileStore duck-types ProfileStore's read surface by design; mesa_core is read-only so it cannot declare a Protocol

    def _default_resolver(self) -> InheritanceResolver:
        return InheritanceResolver(
            store=self,  # type: ignore[arg-type]  # ScopedProfileStore duck-types ProfileStore's read surface by design; mesa_core is read-only so it cannot declare a Protocol
        )


# Control modes surfaced as an explicit "do not operate / observe only" list in
# get_overview. Other authored modes (e.g. a confirm override) appear only in the
# counts, since confirm is the common domain default and listing every confirm
# entity would reintroduce the baseline noise a rollup must avoid.
_RESTRICTIVE_MODES = ("prohibited", "read_only")


def authored_restrictions(
    runtime: "MesaRuntime",
    access_key: "AccessKeyRecord",
    hass: HomeAssistant,
    *,
    limit: int = MAX_PREVIEW_ENTITY_IDS,
) -> dict[str, Any]:
    """Return a scope-relative summary of admin-authored entity profiles, for get_overview.

    Reads only stored profiles through the scoped store, so domain defaults do not dominate.
    """
    scoped = ScopedProfileStore(runtime.store, access_key, hass)
    by_mode: dict[str, int] = {}
    restricted: list[dict[str, str]] = []
    truncated = 0
    for entity_id in sorted(scoped.entity_keys()):
        profile = scoped.get(entity_id)
        if profile is None:
            continue
        boundaries = profile.operational_boundaries
        cm = boundaries.control_mode
        mode = getattr(cm, "value", cm)
        if mode is None:
            continue
        by_mode[mode] = by_mode.get(mode, 0) + 1
        if mode in _RESTRICTIVE_MODES:
            if len(restricted) < limit:
                entry = {"entity_id": entity_id, "control_mode": mode}
                reason = getattr(boundaries, "control_reason", None)
                if reason:
                    entry["reason"] = reason
                restricted.append(entry)
            else:
                truncated += 1
    summary: dict[str, Any] = {
        "authored_profile_count": sum(by_mode.values()),
        "by_control_mode": dict(sorted(by_mode.items())),
        "restricted_entities": restricted,
    }
    if truncated:
        summary["restricted_truncated"] = truncated
    return summary


def _build_validity_context(hass: HomeAssistant) -> Callable[[str], dict[str, Any]]:
    """Return the per-entity context that decides whether a profile is still valid.

    Synchronous, once per entity, so the registry union is built here. ``known_entity_ids``
    must be complete, as a missing id reads as removed, and reusable, not a generator.
    """
    registry = er_mod.async_get(hass)
    known = frozenset(registry.entities) | frozenset(hass.states.async_entity_ids())
    # The running Home Assistant version, from the package constant: it is the
    # only always-present source, since Config carries no `version` attribute.
    ha_version = str(HA_VERSION)

    def _validity_context(entity_id: str) -> dict[str, Any]:
        context: dict[str, Any] = {"known_entity_ids": known}
        if ha_version:
            context["ha_version"] = ha_version
        version = _integration_version(hass, entity_id)
        if version:
            context["integration_version"] = version
        return context

    return _validity_context


def _integration_version(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the version of the integration that created this entity, when it has one.

    Core integrations declare none, so the key is omitted, never invented. Fails quiet.
    """
    try:
        entry = er_mod.async_get(hass).async_get(entity_id)
        if entry is None or not entry.platform:
            return None
        # Local: inside a guard for releases that lack it.
        from homeassistant.loader import async_get_loaded_integration

        version = async_get_loaded_integration(hass, entry.platform).version
        return str(version) if version else None
    except Exception:  # an absent version is not an error
        return None


def _build_handlers(
    runtime: MesaRuntime,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    session_id: str,
    get_semantic_moments: Any = None,
) -> MesaToolHandlers:
    scoped = ScopedProfileStore(runtime.store, access_key, hass)
    resolver = InheritanceResolver(
        store=scoped,  # type: ignore[arg-type]  # ScopedProfileStore duck-types ProfileStore's read surface by design; mesa_core is read-only so it cannot declare a Protocol
    )
    ctx = build_caller_context(access_key, session_id)
    return MesaToolHandlers(
        store=scoped,  # type: ignore[arg-type]  # ScopedProfileStore duck-types ProfileStore's read surface by design; mesa_core is read-only so it cannot declare a Protocol
        resolver=resolver,
        caller_context_fn=lambda: ctx,
        lease_manager=runtime.lease_manager,
        get_semantic_moments=get_semantic_moments,
        get_validity_context=_build_validity_context(hass),
    )


# mesa-core answers a refused or failed query with a success-shaped
# {error, message, details} body. Phoenix marks it isError and audits what it
# was, so a refusal neither reads as data to the agent nor as "allowed" to the
# operator. The out-of-scope not_found above is built to the same envelope and
# flag, so hidden and genuinely unprofiled stay indistinguishable.
_ERROR_OUTCOMES: dict[str, str] = {"not_found": "not_found", "lease_not_found": "not_found", "forbidden": "denied"}


def _is_error_envelope(result: Any) -> bool:
    return isinstance(result, dict) and set(result) == {"error", "message", "details"} and isinstance(result["error"], str)


def _scope_lease_result(result: Any, access_key: AccessKeyRecord, hass: HomeAssistant) -> Any:
    """Remove automations the key cannot read from a lease response.

    The shared lease manager checks every protected automation, while mesa-core's scrub sees
    the scoped store, so this also covers the copy in a refusal's error details.
    """
    if not isinstance(result, dict):
        return result
    details = result.get("details")
    for body in (result, details):
        if not isinstance(body, dict):
            continue
        reasons = body.get("denial_reasons")
        if isinstance(reasons, dict):
            body["denial_reasons"] = {
                entity_id: redact_inaccessible_entity_ids_in_text(str(reason), access_key, hass)
                for entity_id, reason in reasons.items()
            }
        warnings = body.get("warnings")
        if isinstance(warnings, list):
            body["warnings"] = [redact_inaccessible_entity_ids_in_text(str(item), access_key, hass) for item in warnings]
        conflicts = body.get("active_conflicts")
        if isinstance(conflicts, list):
            body["active_conflicts"] = [
                item for item in conflicts
                if isinstance(item, dict) and _is_visible(str(item.get("automation_id")), access_key, hass)
            ]
    return result


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _tool_success(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


async def async_call_mesa_tool(
    tool_name: str,
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    session_id: str,
) -> tuple[dict, str, str]:
    """Dispatch a mesa_* retrieval tool with access key scoping.

    Returns the standard Phoenix MCP tool tuple (result, outcome, resource).
    """
    if effective_cap(access_key, MESA_TOOLS_CAP) == CAP_DENY:
        return _tool_error("Forbidden."), "denied", tool_name

    runtime = data.mesa
    if runtime is None:
        return _tool_error("MESA is not available."), "denied", tool_name

    if tool_name in _LEASE_TOOLS:
        # Leases belong to the access key across stateless calls (see _LEASE_TOOLS).
        session_id = access_key.id
        if tool_name == "mesa_request_lease":
            entities = args.get("entities")
            if isinstance(entities, list) and entities:
                # A lease is an intent to control, so it is scoped to entities
                # the access key can WRITE. Out-of-scope, ghost, and blocked-domain
                # entities are silently dropped (indistinguishable from one
                # another, no enumeration oracle); nothing in scope is the
                # standard generic denial.
                in_scope = [
                    e for e in entities
                    if isinstance(e, str) and resolve(e, access_key, hass) is Permission.WRITE
                ]
                if not in_scope:
                    return _tool_error("Forbidden."), "denied", tool_name
                args = {**args, "entities": in_scope}

    get_semantic_moments = None
    if tool_name in _ENTITY_TARGETED:
        entity_id = args.get("entity_id")
        # An out-of-scope, unexposed or ghost entity must look exactly like a
        # nonexistent one. resolve() also enforces the phx-domain blocklist. The
        # scoped store keeps the plain resolver: hiding an addressable entity's
        # layer there would relax the policy it resolves.
        if entity_id and resolve_read(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
            return _tool_error(json.dumps(_not_found_envelope(entity_id))), "not_found", entity_id
        # mesa-core's get_semantic_moments callback is synchronous, but HA's
        # purpose-specific trigger lookup is async, so pre-compute here (the
        # visibility check above already ran, so out-of-scope entities never
        # reach the HA lookup) and hand mesa-core a closure over the result.
        if (
            tool_name == "mesa_get_profile"
            and entity_id
            and bool(args.get("include_semantic_moments", False))
        ):
            moments = await async_semantic_moments(hass, entity_id)
            get_semantic_moments = lambda eid: moments if eid == entity_id else None

    handlers = _build_handlers(
        runtime, access_key, hass, session_id, get_semantic_moments=get_semantic_moments
    )
    handler = getattr(handlers, tool_name)
    result = await handler(args)
    if tool_name == "mesa_request_lease":
        result = _scope_lease_result(result, access_key, hass)
    text = json.dumps(result, default=str)
    if _is_error_envelope(result):
        return _tool_error(text), _ERROR_OUTCOMES.get(result["error"], "invalid_request"), tool_name
    return _tool_success(text), "allowed", tool_name
