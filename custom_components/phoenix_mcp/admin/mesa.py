"""Admin MESA views: profiles, scopes, vocabulary, issues, portability and suggestions."""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import logging
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr_mod, entity_registry as er_mod

from ..const import DOMAIN
from ..data import PhoenixData
from ..mesa import async_refresh_trigger_issues, read_automation_configs, refresh_orphans
from ..mesa_core import MetadataOrigin, SemanticProfile, vocabulary
from ..mesa_core.exceptions import InvalidCursorError, MesaError, MesaValidationError
from ..mesa_core.migration import migrate_profile
from ..mesa_core.portability import export_profiles, import_profiles
from ..mesa_suggestions import refresh_suggestions
from ..policy_engine import device_registry_entries
from ..view_base import PhoenixView, _err
from .common import (
    _DOMAIN_RE, _ENTITY_RE, _audit_admin, _ok, _read_body, _validate_node_id, require_admin,
)

_LOGGER = logging.getLogger(__name__)


def _mesa_runtime(hass: HomeAssistant, rid: str) -> tuple[Any, web.Response | None]:
    """Return the MESA runtime, or an error response when MESA is unavailable."""
    data: PhoenixData = hass.data[DOMAIN]
    if data.mesa is None:
        return None, _err(
            "service_unavailable", "MESA is not available.", 503, rid,
            key="mesaUnavailable",
        )
    return data.mesa, None


def _mesa_migrated_doc(body: dict) -> dict:
    """Return a profile document stamped with the current MESA schema version.

    The store boundary is the only place that label is written. A malformed body comes back
    untouched, so SemanticProfile.from_dict reports it as a 400.
    """

    try:
        if isinstance(body.get("semantic_profile"), dict):
            return migrate_profile(body)
        # `from_dict` also accepts a BARE semantic profile as the entire body,
        # and that shape has to be stamped too, or half the accepted inputs would
        # still be stored claiming to be 1.0. migrate_profile only understands
        # the wrapped form, so wrap it, migrate, and hand the inner object back.
        return migrate_profile({"semantic_profile": body})["semantic_profile"]
    except MesaError:
        return body


class PhoenixAdminMesaProfilesView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/profiles - list stored entity profiles (paginated)."""

    url = "/api/phoenix-mcp/admin/mesa/profiles"
    name = "api:phoenix-mcp:admin:mesa:profiles"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err


        q = request.query
        tag = q.get("tag")
        area = q.get("area")
        try:
            limit = int(q.get("limit", 50))
        except (TypeError, ValueError):
            return _err(
                "invalid_request", "limit must be an integer.", 400, rid,
                key="integerRequired", params={"field": "limit"},
            )
        try:
            domain = q.get("domain")
            result = runtime.store.query(
                domains=[domain] if domain else None,
                tags=[tag] if tag else None,
                areas=[area] if area else None,
                origin=q.get("origin"),
                include_inferred=True,  # admin sees every origin, including inferred
                limit=limit,
                cursor=q.get("cursor"),
            )
        except InvalidCursorError as exc:
            return _err("invalid_request", str(exc), 400, rid, passthrough=True)
        except ValueError as exc:
            return _err("invalid_request", str(exc), 400, rid, passthrough=True)

        return _ok(
            {
                "profiles": [
                    {"entity_id": row.entity_id, "document": row.stored.to_dict()}
                    for row in result.rows
                ],
                "total_matched": result.total_matched,
                "has_more": result.has_more,
                "next_cursor": result.next_cursor,
            },
            request_id=rid,
        )


async def _new_mesa_profile(identifier: str, request: web.Request, rid: str) -> Any:
    """The profile in a PUT body, or the response refusing it."""

    body = await _read_body(request, rid)
    if isinstance(body, web.Response):
        return body
    try:
        return SemanticProfile.from_dict(identifier, _mesa_migrated_doc(body), default_origin=MetadataOrigin.USER)
    except MesaValidationError as exc:
        return _err("invalid_request", str(exc), 400, rid, passthrough=True)


async def _save_mesa_profile(
    runtime: Any, request: web.Request, rid: str, stored: Callable[[], Any], store: Callable[[], None],
) -> web.Response | None:
    """Store a profile under the runtime lock, refusing to create over an existing one."""
    async with runtime.lock:
        if request.headers.get("If-None-Match") == "*" and stored() is not None:
            return _err("precondition_failed", "A profile already exists. Open it in Edit mode.",
                        412, rid, key="mesaProfileExists")
        store()
        await runtime.async_save()
    return None


class _MesaScopeProfileView(PhoenixView):
    """GET/PUT/DELETE of the one profile a MESA scope holds, keyed by the route's id."""

    requires_auth = True
    id_field: str
    store_suffix: str

    def invalid_id(self, value: str, rid: str) -> web.Response | None:
        raise NotImplementedError

    def _store_call(self, runtime: Any, action: str) -> Callable[..., Any]:
        return getattr(runtime.store, f"{action}_{self.store_suffix}")

    @require_admin
    async def get(self, request: web.Request, **route: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        identifier = route[self.id_field]
        stored = self._store_call(runtime, "get")(identifier)
        return _ok(
            {self.id_field: identifier, "stored": stored.to_dict() if stored is not None else None},
            request_id=rid,
        )

    @require_admin
    async def put(self, request: web.Request, **route: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        identifier = route[self.id_field]
        if (invalid := self.invalid_id(identifier, rid)) is not None:
            return invalid
        profile = await _new_mesa_profile(identifier, request, rid)
        if isinstance(profile, web.Response):
            return profile
        refused = await _save_mesa_profile(
            runtime, request, rid,
            lambda: self._store_call(runtime, "get")(identifier),
            lambda: self._store_call(runtime, "set")(identifier, profile),
        )
        if refused is not None:
            return refused
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({self.id_field: identifier, "stored": profile.to_dict()}, request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, **route: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        identifier = route[self.id_field]
        async with runtime.lock:
            self._store_call(runtime, "delete")(identifier)
            await runtime.async_save()
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({self.id_field: identifier, "deleted": True}, request_id=rid)


class PhoenixAdminMesaProfileView(PhoenixView):
    """GET/PUT/DELETE /api/phoenix-mcp/admin/mesa/profiles/{entity_id} - one entity profile."""

    url = "/api/phoenix-mcp/admin/mesa/profiles/{entity_id}"
    name = "api:phoenix-mcp:admin:mesa:profile"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, entity_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        if not _ENTITY_RE.match(entity_id):
            return _err("invalid_request", "Invalid entity ID format.", 400, rid, key="badEntityId")
        stored = runtime.store.get(entity_id)
        effective = runtime.store.get_effective(entity_id)
        explanation = runtime.resolver.explain(entity_id)
        return _ok(
            {
                "entity_id": entity_id,
                "stored": stored.to_dict() if stored is not None else None,
                "effective": effective.to_dict(),
                "explanation": explanation.to_dict(),
            },
            request_id=rid,
        )

    @require_admin
    async def put(self, request: web.Request, entity_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err

        if not _ENTITY_RE.match(entity_id):
            return _err("invalid_request", "Invalid entity ID format.", 400, rid, key="badEntityId")


        profile = await _new_mesa_profile(entity_id, request, rid)
        if isinstance(profile, web.Response):
            return profile
        refused = await _save_mesa_profile(
            runtime, request, rid,
            lambda: runtime.store.get(entity_id),
            lambda: runtime.store.set(entity_id, profile),
        )
        if refused is not None:
            return refused

        # Cross-check the new profile against the automation registry.
        configs = await self.hass.async_add_executor_job(read_automation_configs, self.hass)
        issues = runtime.validator.validate_entity(entity_id, lambda: configs)

        _audit_admin(self.hass, request, rid, request.path)
        return _ok(
            {
                "entity_id": entity_id,
                "stored": profile.to_dict(),
                "warnings": [_issue_to_dict(i) for i in issues],
            },
            request_id=rid,
        )

    @require_admin
    async def delete(self, request: web.Request, entity_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        if not _ENTITY_RE.match(entity_id):
            return _err("invalid_request", "Invalid entity ID format.", 400, rid, key="badEntityId")
        async with runtime.lock:
            runtime.store.delete(entity_id)
            await runtime.async_save()
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"entity_id": entity_id, "deleted": True}, request_id=rid)


class PhoenixAdminMesaDomainsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/domains - list all stored domain-level profiles."""

    url = "/api/phoenix-mcp/admin/mesa/domains"
    name = "api:phoenix-mcp:admin:mesa:domains"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        domains = []
        for domain in sorted(runtime.store.domain_keys()):
            stored = runtime.store.get_domain_profile(domain)
            if stored is not None:
                domains.append({"domain": domain, "document": stored.to_dict()})
        return _ok({"domains": domains}, request_id=rid)


class PhoenixAdminMesaAreasView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/areas - list all stored area-level profiles."""

    url = "/api/phoenix-mcp/admin/mesa/areas"
    name = "api:phoenix-mcp:admin:mesa:areas"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        areas = []
        for area_id in sorted(runtime.store.area_keys()):
            stored = runtime.store.get_area_profile(area_id)
            if stored is not None:
                areas.append({"area_id": area_id, "document": stored.to_dict()})
        return _ok({"areas": areas}, request_id=rid)


class PhoenixAdminMesaDomainView(_MesaScopeProfileView):
    """GET/PUT/DELETE /api/phoenix-mcp/admin/mesa/domains/{domain} - one domain-level profile."""

    url = "/api/phoenix-mcp/admin/mesa/domains/{domain}"
    name = "api:phoenix-mcp:admin:mesa:domain"
    requires_auth = True
    id_field = "domain"
    store_suffix = "domain_profile"

    def invalid_id(self, value: str, rid: str) -> web.Response | None:
        if _DOMAIN_RE.match(value):
            return None
        return _err("invalid_request", "Invalid domain name.", 400, rid, key="badDomain")


class PhoenixAdminMesaIntegrationsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/integrations - list all stored integration-level profiles.

    Integration profiles (MESA inheritance level between area and domain) are keyed
    by the integration's component name and govern the entities that integration
    created, regardless of entity domain. Vendor sidecars import here.
    """

    url = "/api/phoenix-mcp/admin/mesa/integrations"
    name = "api:phoenix-mcp:admin:mesa:integrations"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        integrations = []
        for integration in sorted(runtime.store.integration_keys()):
            stored = runtime.store.get_integration_profile(integration)
            if stored is not None:
                integrations.append({"integration": integration, "document": stored.to_dict()})
        return _ok({"integrations": integrations}, request_id=rid)


class PhoenixAdminMesaIntegrationView(_MesaScopeProfileView):
    """GET/PUT/DELETE /api/phoenix-mcp/admin/mesa/integrations/{integration} - one profile."""

    url = "/api/phoenix-mcp/admin/mesa/integrations/{integration}"
    name = "api:phoenix-mcp:admin:mesa:integration"
    requires_auth = True
    id_field = "integration"
    store_suffix = "integration_profile"

    def invalid_id(self, value: str, rid: str) -> web.Response | None:
        # Integration/component names share HA's domain character set.
        if _DOMAIN_RE.match(value):
            return None
        return _err("invalid_request", "Invalid integration name.", 400, rid, key="integrationNameInvalid")


class PhoenixAdminMesaIntegrationOptionsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/integration-options - integrations that have entities.

    For the "Add integration profile" picker: ``id`` is the component name and ``name`` its
    title. Phoenix MCP's own domain is excluded.
    """

    url = "/api/phoenix-mcp/admin/mesa/integration-options"
    name = "api:phoenix-mcp:admin:mesa:integration-options"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        registry = er_mod.async_get(self.hass)
        domains = sorted(
            {e.platform for e in registry.entities.values() if e.platform and e.platform != DOMAIN}
        )
        names: dict[str, str] = {}
        try:
            # Local: inside a guard for releases that lack it.
            from homeassistant.loader import async_get_integrations

            for dom, integ in (await async_get_integrations(self.hass, domains)).items():
                if not isinstance(integ, BaseException):
                    names[dom] = getattr(integ, "name", None) or dom
        except Exception:  # friendly names are best-effort
            pass
        options = [{"id": dom, "name": names.get(dom, dom)} for dom in domains]
        return _ok({"integrations": options}, request_id=rid)


class PhoenixAdminMesaAreaView(_MesaScopeProfileView):
    """GET/PUT/DELETE /api/phoenix-mcp/admin/mesa/areas/{area_id} - one area-level profile."""

    url = "/api/phoenix-mcp/admin/mesa/areas/{area_id}"
    name = "api:phoenix-mcp:admin:mesa:area"
    requires_auth = True
    id_field = "area_id"
    store_suffix = "area_profile"

    def invalid_id(self, value: str, rid: str) -> web.Response | None:
        return _validate_node_id("area", value, rid)


class PhoenixAdminMesaDevicesView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/devices - list all stored device-level profiles."""

    url = "/api/phoenix-mcp/admin/mesa/devices"
    name = "api:phoenix-mcp:admin:mesa:devices"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        devices = []
        for device_id in sorted(runtime.store.device_keys()):
            stored = runtime.store.get_device_profile(device_id)
            if stored is not None:
                devices.append({"device_id": device_id, "document": stored.to_dict()})
        return _ok({"devices": devices}, request_id=rid)


class PhoenixAdminMesaDeviceOptionsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/device-options - devices available to profile.

    The picker source: registry id plus HA's display name, preferring the operator's rename.
    """

    url = "/api/phoenix-mcp/admin/mesa/device-options"
    name = "api:phoenix-mcp:admin:mesa:device-options"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        registry = dr_mod.async_get(self.hass)
        options = []
        for device in device_registry_entries(registry):
            name = device.name_by_user or device.name or device.id
            options.append({"id": device.id, "name": name})
        options.sort(key=lambda o: (o["name"].lower(), o["id"]))
        return _ok({"devices": options}, request_id=rid)


class PhoenixAdminMesaDeviceView(_MesaScopeProfileView):
    """GET/PUT/DELETE /api/phoenix-mcp/admin/mesa/devices/{device_id} - one device profile."""

    url = "/api/phoenix-mcp/admin/mesa/devices/{device_id}"
    name = "api:phoenix-mcp:admin:mesa:device"
    requires_auth = True
    id_field = "device_id"
    store_suffix = "device_profile"

    def invalid_id(self, value: str, rid: str) -> web.Response | None:
        return _validate_node_id("device", value, rid)


class PhoenixAdminMesaVocabularyView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/vocabulary - the canonical MESA tag registry.

    Lets the admin UI source tag autocomplete from mesa-core directly, so the
    frontend never duplicates (and never drifts from) the canonical vocabulary.
    """

    url = "/api/phoenix-mcp/admin/mesa/vocabulary"
    name = "api:phoenix-mcp:admin:mesa:vocabulary"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        return _ok(
            {
                "canonical_tags": sorted(vocabulary.CANONICAL_TAGS),
                "canonical_roots": sorted(vocabulary.CANONICAL_ROOTS),
            },
            request_id=rid,
        )


class PhoenixAdminMesaDefaultsView(PhoenixView):
    """GET/PUT /api/phoenix-mcp/admin/mesa/defaults - deployment defaults for unprofiled entities."""

    url = "/api/phoenix-mcp/admin/mesa/defaults"
    name = "api:phoenix-mcp:admin:mesa:defaults"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        defaults = runtime.store.get_deployment_defaults()
        return _ok(
            {"deployment_defaults": defaults.to_dict() if defaults is not None else None},
            request_id=rid,
        )

    @require_admin
    async def put(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        try:
            async with runtime.lock:
                runtime.store.set_deployment_defaults(body)
                await runtime.async_save()
        except (MesaValidationError, ValueError, KeyError, TypeError) as exc:
            return _err(
                "invalid_request", f"Invalid deployment defaults: {exc}", 400, rid,
                key="deploymentDefaultsInvalid", params={"detail": str(exc)},
            )
        _audit_admin(self.hass, request, rid, request.path)
        stored = runtime.store.get_deployment_defaults()
        return _ok(
            {"deployment_defaults": stored.to_dict() if stored is not None else None},
            request_id=rid,
        )


class PhoenixAdminMesaIssuesView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/issues - TriggerValidator issues and orphaned profiles."""

    url = "/api/phoenix-mcp/admin/mesa/issues"
    name = "api:phoenix-mcp:admin:mesa:issues"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        refresh = request.query.get("refresh")
        if refresh == "suggestions":
            # Scoped: the panel's quiet background read. Rescan and the reload
            # after a profile save, delete or import ask for the full recompute
            # below, so orphans and trigger findings follow them.

            refresh_suggestions(self.hass, runtime)
        elif refresh:

            await async_refresh_trigger_issues(self.hass, runtime)
            refresh_orphans(self.hass, runtime)
            refresh_suggestions(self.hass, runtime)
        return _ok(
            {
                "issues": [_issue_to_dict(i) for i in runtime.trigger_issues],
                "orphans": list(runtime.orphans),
                "orphan_devices": list(runtime.orphan_devices),
                "orphan_areas": list(runtime.orphan_areas),
                "orphan_integrations": list(runtime.orphan_integrations),
                "suggestions": [_issue_to_dict(s) for s in runtime.suggestions],
                "dismissed_suggestions": sorted(runtime.dismissed_suggestions),
            },
            request_id=rid,
        )


def _issue_to_dict(issue) -> dict:
    """Serialise a mesa-core ValidationIssue dataclass."""

    return dataclasses.asdict(issue)


class PhoenixAdminMesaOrphansClearView(PhoenixView):
    """POST /api/phoenix-mcp/admin/mesa/orphans/clear - delete all orphaned MESA profiles.

    Recomputes the orphan lists, then deletes entity, area and integration orphans in one
    pass.
    """

    url = "/api/phoenix-mcp/admin/mesa/orphans/clear"
    name = "api:phoenix-mcp:admin:mesa:orphans:clear"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err

        async with runtime.lock:
            # Recompute against the live registries to delete exactly what is
            # orphaned at this moment, never a stale client-supplied list.
            refresh_orphans(self.hass, runtime)
            entities = list(runtime.orphans)
            devices = list(runtime.orphan_devices)
            areas = list(runtime.orphan_areas)
            integrations = list(runtime.orphan_integrations)
            for eid in entities:
                runtime.store.delete(eid)
            for device_id in devices:
                runtime.store.delete_device_profile(device_id)
            for area_id in areas:
                runtime.store.delete_area_profile(area_id)
            for integration in integrations:
                runtime.store.delete_integration_profile(integration)
            if entities or devices or areas or integrations:
                await runtime.async_save()
            refresh_orphans(self.hass, runtime)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok(
            {
                "deleted": {
                    "entities": entities,
                    "devices": devices,
                    "areas": areas,
                    "integrations": integrations,
                },
                "count": len(entities) + len(devices) + len(areas) + len(integrations),
            },
            request_id=rid,
        )


class PhoenixAdminMesaExportView(PhoenixView):
    """GET /api/phoenix-mcp/admin/mesa/export - download every MESA profile as an archive.

    Delegates to mesa-core's export_profiles, synchronously over the dict backend, so no lock.
    """

    url = "/api/phoenix-mcp/admin/mesa/export"
    name = "api:phoenix-mcp:admin:mesa:export"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err

        archive = export_profiles(runtime.store)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok(archive, request_id=rid)


class PhoenixAdminMesaImportView(PhoenixView):
    """POST /api/phoenix-mcp/admin/mesa/import - import a MESA profile archive.

    Body: {"archive": {...}, "on_conflict": "skip" | "overwrite"} (default skip). mesa-core
    quarantines invalid documents in the result's "invalid" map; the "error" mode is not
    exposed.
    """

    url = "/api/phoenix-mcp/admin/mesa/import"
    name = "api:phoenix-mcp:admin:mesa:import"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        archive = body.get("archive")
        if not isinstance(archive, dict):
            return _err(
                "invalid_request", "archive is required.", 400, rid,
                key="mesaArchiveRequired",
            )
        on_conflict = body.get("on_conflict", "skip")
        if on_conflict not in ("skip", "overwrite"):
            return _err(
                "invalid_request", "on_conflict must be 'skip' or 'overwrite'.", 400, rid,
                key="mesaConflictModeInvalid",
            )


        async with runtime.lock:
            try:
                result = import_profiles(runtime.store, archive, on_conflict=on_conflict)
            except MesaValidationError as exc:
                return _err("invalid_request", str(exc), 400, rid, passthrough=True)
            if result.imported or result.overwritten:
                await runtime.async_save()
            # An archive from another deployment can carry profiles for targets
            # that do not exist here; recompute so the orphan banner and the
            # suggestions card reflect the new store immediately. Best-effort:
            # a scan failure must never take down an otherwise-successful
            # import response (the import itself already landed and saved
            # above); the panel's own Rescan recovers the cached lists.
            try:
                refresh_orphans(self.hass, runtime)
                refresh_suggestions(self.hass, runtime)
            except Exception:  # degrade, never fail the import
                _LOGGER.warning(
                    "MESA import: post-import orphan/suggestion rescan failed rid=%s",
                    rid, exc_info=True,
                )
        _audit_admin(self.hass, request, rid, request.path)
        return _ok(
            {
                "imported": result.imported,
                "overwritten": result.overwritten,
                "skipped_existing": list(result.skipped_existing),
                "invalid": dict(result.invalid),
            },
            request_id=rid,
        )


def _suggestions_payload(runtime) -> dict:
    return {
        "suggestions": [_issue_to_dict(s) for s in runtime.suggestions],
        "dismissed_suggestions": sorted(runtime.dismissed_suggestions),
    }


class PhoenixAdminMesaSuggestionDismissView(PhoenixView):
    """POST /api/phoenix-mcp/admin/mesa/suggestions/dismiss - dismiss one profile suggestion.

    The key must match a CURRENTLY computed suggestion (recomputed under the
    lock, never trusted from the client), which bounds the dismissed set by
    construction. Dismissals persist in the phoenix_mcp_mesa store.
    """

    url = "/api/phoenix-mcp/admin/mesa/suggestions/dismiss"
    name = "api:phoenix-mcp:admin:mesa:suggestions:dismiss"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        key = body.get("key")
        if not isinstance(key, str) or not key:
            return _err(
                "invalid_request", "key is required.", 400, rid,
                key="suggestionKeyRequired",
            )


        async with runtime.lock:
            refresh_suggestions(self.hass, runtime)
            if key not in {s.key for s in runtime.suggestions}:
                return _err(
                    "not_found", "No current suggestion with that key.", 404, rid,
                    key="suggestionNotCurrent",
                )
            runtime.dismissed_suggestions.add(key)
            await runtime.async_save()
            refresh_suggestions(self.hass, runtime)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"dismissed": key, **_suggestions_payload(runtime)}, request_id=rid)


class PhoenixAdminMesaSuggestionRestoreView(PhoenixView):
    """POST /api/phoenix-mcp/admin/mesa/suggestions/restore - un-dismiss suggestion(s).

    Body: {"key": "..."} restores one, {"all": true} clears every dismissal.
    """

    url = "/api/phoenix-mcp/admin/mesa/suggestions/restore"
    name = "api:phoenix-mcp:admin:mesa:suggestions:restore"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        key = body.get("key")
        restore_all = body.get("all") is True
        if not restore_all and (not isinstance(key, str) or not key):
            return _err(
                "invalid_request", "Provide key or all: true.", 400, rid,
                key="suggestionRestoreTargetRequired",
            )


        async with runtime.lock:
            if restore_all:
                restored: str | int = len(runtime.dismissed_suggestions)
                runtime.dismissed_suggestions.clear()
            else:
                if key not in runtime.dismissed_suggestions:
                    return _err(
                        "not_found", "That key is not dismissed.", 404, rid,
                        key="suggestionNotDismissed",
                    )
                runtime.dismissed_suggestions.discard(key)
                restored = str(key)
            await runtime.async_save()
            refresh_suggestions(self.hass, runtime)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"restored": restored, **_suggestions_payload(runtime)}, request_id=rid)
