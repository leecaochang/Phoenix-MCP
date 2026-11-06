"""Admin access key views: lifecycle, settings, presets, statistics and rotation."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from ..access_key_store import (
    NotesError, PermissionTree, PresetLimitError, PresetNameTakenError, access_key_name_slug,
    normalize_notes,
)
from ..approvals import (
    REASON_REVOKED, async_cancel_approvals_for_access_key, dismiss_approval_notification,
    fire_approval_resolved_event,
)
from ..const import (
    ACCESS_KEY_MUTABLE_FIELDS, ACCESS_KEY_NAME_REGEX, ACCESS_KEY_NOTES_MAX_LENGTH, CAPABILITY_NAMES,
    CAP_ALLOW, CAP_CONFIRM, CAP_MODES, CONFIRM_AVAILABLE_CAPS, CONFIRM_ONLY_CAPS,
    DEFAULT_RATE_LIMIT_BURST, DEFAULT_RATE_LIMIT_REQUESTS, DOMAIN, MAX_CONFIRM_INLINE_WAIT_SECONDS,
    MAX_PRESETS_PER_ACCESS_KEY, MIN_CONFIRM_INLINE_WAIT_SECONDS, PERSONA_HOME_COMPANION,
    PERSONA_NAMES, PRESET_NAME_MAX_LENGTH,
)
from ..data import PhoenixData
from ..helpers import cancel_expiry_timer
from ..mesa import release_access_key_leases
from ..sensor import async_rename_access_key_sensors
from ..view_base import PhoenixView, _err
from ..voice_agent import async_remove_assist_pipeline
from .common import (
    _INJECTION_CHARS, _audit_admin, _in_menu, _ok, _read_body, _request_time, require_admin,
)

_LOGGER = logging.getLogger(__name__)


class PhoenixAdminArchivedAccessKeysView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys/archived - list all archived access keys."""

    url = "/api/phoenix-mcp/admin/access-keys/archived"
    name = "api:phoenix-mcp:admin:archived_access_keys"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: PhoenixData = self.hass.data[DOMAIN]
        archived = [t.to_dict() for t in data.store.list_archived()]
        return _ok(archived, request_id=request["phoenix_mcp_rid"])


class PhoenixAdminArchivedAccessKeyView(PhoenixView):
    """DELETE /api/phoenix-mcp/admin/access-keys/archived/{access_key_id} - permanently delete an archived record."""

    url = "/api/phoenix-mcp/admin/access-keys/archived/{access_key_id}"
    name = "api:phoenix-mcp:admin:archived_access_key"
    requires_auth = True

    @require_admin
    async def delete(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        deleted = await data.store.async_delete_archived(access_key_id)
        if not deleted:
            return _err("not_found", "Archived access key not found.", 404, rid, key="archivedAccessKeyNotFound")
        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )
        return web.Response(status=204, headers={"X-Phoenix-Request-ID": rid})


class PhoenixAdminAccessKeysView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys - list active access keys.
    POST /api/phoenix-mcp/admin/access-keys - create a new access key.
    """

    url = "/api/phoenix-mcp/admin/access-keys"
    name = "api:phoenix-mcp:admin:access-keys"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: PhoenixData = self.hass.data[DOMAIN]
        access_keys = [t.to_dict() for t in data.store.list_access_keys()]
        return _ok(access_keys, request_id=request["phoenix_mcp_rid"])

    @require_admin
    async def post(self, request: web.Request) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        name = body.get("name")
        if not name or not isinstance(name, str):
            return _err("invalid_request", "name is required.", 400, rid, key="nameRequired")
        if not ACCESS_KEY_NAME_REGEX.fullmatch(name):
            return _err(
                "invalid_request",
                "name must be 3-32 characters: letters, numbers, hyphens, or underscores.",
                400, rid, key="accessKeyNameLengthPattern", params={"min": 3, "max": 32},
            )
        pass_through = body.get("pass_through", False)
        if not isinstance(pass_through, bool):
            return _err(
                "invalid_request", "pass_through must be a boolean.", 400, rid,
                key="booleanRequired", params={"field": "pass_through"},
            )
        # Require a real JSON true, not just a truthy value: bool("false") is
        # True, so a string would otherwise satisfy this acknowledgment gate.
        if pass_through and body.get("confirm_pass_through") is not True:
            return _err(
                "invalid_request",
                "confirm_pass_through: true is required when enabling pass_through.",
                400, rid, key="passThroughConfirmationRequired",
            )
        use_assist_exposure = body.get("use_assist_exposure", False)
        if not isinstance(use_assist_exposure, bool):
            return _err(
                "invalid_request", "use_assist_exposure must be a boolean.", 400, rid,
                key="booleanRequired", params={"field": "use_assist_exposure"},
            )
        if not pass_through:
            use_assist_exposure = False

        expires_at = None
        if "expires_at" in body:
            raw_expires = body["expires_at"]
            # A timezone-less value would persist naive and later crash every
            # comparison against aware UTC (expiry checks run during setup, so
            # one poisoned record aborts the whole integration). Normalized to
            # UTC; a naive input is treated as HA's configured local time.
            expires_at = _request_time(raw_expires) if isinstance(raw_expires, str) else None
            if expires_at is None:
                return _err("invalid_request", "Invalid expires_at datetime.", 400, rid, key="badExpiresAt")
            # A key that is already expired would be archived on its first use.
            if expires_at <= utcnow():
                return _err("invalid_request", "expires_at must be in the future.", 400, rid, key="expiresInPast")

        # Strict: int() would silently accept True (as 1), 1.75 (as 1), and
        # "12" (as 12), which contradicts the error message and the documented
        # contract. Same check as confirm_inline_wait_seconds; bool is excluded
        # explicitly because it is a subclass of int.
        rate_limit_requests = body.get("rate_limit_requests", DEFAULT_RATE_LIMIT_REQUESTS)
        rate_limit_burst = body.get("rate_limit_burst", DEFAULT_RATE_LIMIT_BURST)
        if not all(
            isinstance(v, int) and not isinstance(v, bool)
            for v in (rate_limit_requests, rate_limit_burst)
        ):
            return _err(
                "invalid_request",
                "rate_limit_requests and rate_limit_burst must be integers.",
                400, rid, key="rateLimitsInteger",
            )

        if rate_limit_requests < 0 or rate_limit_burst < 0:
            return _err(
                "invalid_request",
                "rate_limit_requests and rate_limit_burst must be non-negative.",
                400, rid, key="rateLimitsNonNegative",
            )
        if rate_limit_requests > 100_000 or rate_limit_burst > 100_000:
            return _err(
                "invalid_request",
                "rate_limit_requests and rate_limit_burst must not exceed 100000.",
                400, rid, key="rateLimitsMaximum", params={"max": 100_000},
            )

        async with data.store.async_lock:
            if data.store.name_slug_exists(name):
                return _err("conflict", "An access key with that name (or equivalent slug) already exists.", 409, rid, key="accessKeyNameTaken")
            record, raw_access_key = await data.store.async_create_access_key(
                name=name,
                created_by=user.id,
                expires_at=expires_at,
                pass_through=pass_through,
                use_assist_exposure=use_assist_exposure,
                rate_limit_requests=rate_limit_requests,
                rate_limit_burst=rate_limit_burst,
            )

        if data.async_on_access_key_created:
            await data.async_on_access_key_created(record)

        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        # raw_access_key is included once in the creation response and never again.
        response_body = record.to_dict()
        response_body["access_key"] = raw_access_key
        return _ok(response_body, status=201, request_id=rid)


# The per-access-key settings a PATCH may write. Capabilities are folded in from
# const, so adding one needs no change here.
_PATCHABLE_ACCESS_KEY_FIELDS = ACCESS_KEY_MUTABLE_FIELDS | set(CAPABILITY_NAMES)

_RATE_LIMIT_MAX = 100_000


def _validated_access_key_patch(
    body: dict, access_key: Any, access_key_id: str, data: PhoenixData, rid: str
) -> tuple[dict, web.Response | None]:
    """Filter an access key PATCH body to writable fields and validate them.

    Returns (patchable, None) or ({}, error_response) on the first rejection. Call with
    data.store.async_lock held, as the uniqueness and pass-through checks read what it writes.
    """
    unknown = sorted(set(body) - _PATCHABLE_ACCESS_KEY_FIELDS - {"confirm_pass_through"})
    if unknown:
        fields = ", ".join(unknown)
        return {}, _err(
            "invalid_request",
            f"The request contains fields Phoenix does not recognize: {fields}.",
            400,
            rid,
            key="unknownFields",
            params={"fields": fields},
        )

    if "name" in body:
        new_name = body["name"]
        if not new_name or not isinstance(new_name, str):
            return {}, _err("invalid_request", "name is required.", 400, rid, key="nameRequired")
        if not ACCESS_KEY_NAME_REGEX.fullmatch(new_name):
            return {}, _err(
                "invalid_request",
                "name must be 3-32 characters: letters, numbers, hyphens, or underscores.",
                400, rid, key="accessKeyNameLengthPattern", params={"min": 3, "max": 32},
            )
        if data.store.name_slug_exists(new_name, exclude_access_key_id=access_key_id):
            return {}, _err(
                "invalid_request", "An access key with this name already exists.", 400, rid,
                key="accessKeyNameTaken",
            )

    if "pass_through" in body:
        if not isinstance(body["pass_through"], bool):
            return {}, _err(
                "invalid_request", "pass_through must be a boolean.", 400, rid,
                key="booleanRequired", params={"field": "pass_through"},
            )
        enabling = body["pass_through"]
        # Real JSON true only: bool("false") is True, so a truthy string
        # must not satisfy this acknowledgment gate.
        if enabling and not access_key.pass_through and body.get("confirm_pass_through") is not True:
            return {}, _err(
                "invalid_request",
                "confirm_pass_through: true is required when enabling pass_through.",
                400, rid, key="passThroughConfirmationRequired",
            )

    patchable = {k: v for k, v in body.items() if k in _PATCHABLE_ACCESS_KEY_FIELDS}

    for bool_field in ("announce_all_tools", "use_assist_exposure"):
        if bool_field in patchable and not isinstance(patchable[bool_field], bool):
            return {}, _err(
                "invalid_request", f"{bool_field} must be a boolean.", 400, rid,
                key="booleanRequired", params={"field": bool_field},
            )

    if "confirm_inline_wait_seconds" in patchable:
        # 0 disables the inline wait (the unattended-agent mode, API-only);
        # any other value must fall in the enabled range [MIN, MAX].
        wait = patchable["confirm_inline_wait_seconds"]
        valid_int = isinstance(wait, int) and not isinstance(wait, bool)
        valid = valid_int and (wait == 0 or MIN_CONFIRM_INLINE_WAIT_SECONDS <= wait <= MAX_CONFIRM_INLINE_WAIT_SECONDS)
        if not valid:
            return {}, _err(
                "invalid_request",
                f"confirm_inline_wait_seconds must be 0 (off) or an integer from "
                f"{MIN_CONFIRM_INLINE_WAIT_SECONDS} to {MAX_CONFIRM_INLINE_WAIT_SECONDS}.",
                400,
                rid,
                key="inlineWaitRange",
                params={
                    "min": MIN_CONFIRM_INLINE_WAIT_SECONDS,
                    "max": MAX_CONFIRM_INLINE_WAIT_SECONDS,
                },
            )

    for cap_name in set(CAPABILITY_NAMES) & patchable.keys():
        value = patchable[cap_name]
        if not _in_menu(value, CAP_MODES):
            return {}, _err(
                "invalid_request", f"{cap_name} must be one of: deny, allow, confirm.",
                400, rid, key="capabilityModeInvalid", params={"capability": cap_name},
            )
        if value == CAP_CONFIRM and cap_name not in CONFIRM_AVAILABLE_CAPS:
            return {}, _err(
                "invalid_request", f"{cap_name} does not support 'confirm' mode.",
                400, rid, key="capabilityConfirmUnsupported", params={"capability": cap_name},
            )
        if value == CAP_ALLOW and cap_name in CONFIRM_ONLY_CAPS:
            return {}, _err(
                "invalid_request", f"{cap_name} does not support 'allow' mode.",
                400, rid, key="capabilityAllowUnsupported", params={"capability": cap_name},
            )

    if "persona" in patchable and not _in_menu(patchable["persona"], PERSONA_NAMES):
        return {}, _err("invalid_request", "Unknown persona.", 400, rid, key="unknownPersona")

    # A Home Companion key never uses pass-through or announces every tool: the
    # store turns both off. Asking for either is refused rather than answered
    # with a success that changed nothing.
    if patchable.get("persona", access_key.persona) == PERSONA_HOME_COMPANION and (
        patchable.get("pass_through") is True or patchable.get("announce_all_tools") is True
    ):
        return {}, _err(
            "invalid_request", "A Home Companion key can't use pass-through or announce all tools.",
            400, rid, key="companionSettingRefused",
        )

    if "notes" in patchable:
        try:
            patchable["notes"] = normalize_notes(patchable["notes"])
        except NotesError as exc:
            if exc.reason == "length":
                return {}, _err(
                    "invalid_request",
                    f"notes must be {ACCESS_KEY_NOTES_MAX_LENGTH} characters or fewer.",
                    400, rid, key="notesTooLong", params={"max": ACCESS_KEY_NOTES_MAX_LENGTH},
                )
            if exc.reason == "characters":
                return {}, _err(
                    "invalid_request",
                    "notes must not contain control or hidden formatting characters.",
                    400, rid, key="notesInvalidCharacters",
                )
            return {}, _err("invalid_request", "notes must be a string or null.", 400, rid, key="notesString")

    if "use_assist_exposure" in patchable:
        # Keyed on the RESULTING pass_through, so enabling both in one PATCH works.
        resulting_pass_through = bool(patchable.get("pass_through", access_key.pass_through))
        if not resulting_pass_through:
            return {}, _err(
                "invalid_request", "use_assist_exposure is only valid for pass_through access keys.",
                400, rid, key="assistExposurePassThroughOnly",
            )

    for rl_field in ("rate_limit_requests", "rate_limit_burst"):
        if rl_field not in patchable:
            continue
        # Strict, matching the create path: int() would coerce booleans, floats,
        # and numeric strings into a stored value the caller never asked for.
        value = patchable[rl_field]
        if not (isinstance(value, int) and not isinstance(value, bool)):
            return {}, _err(
                "invalid_request", f"{rl_field} must be an integer.", 400, rid,
                key="integerRequired", params={"field": rl_field},
            )
        if value < 0:
            return {}, _err(
                "invalid_request", f"{rl_field} must be non-negative.", 400, rid,
                key="nonNegativeRequired", params={"field": rl_field},
            )
        if value > _RATE_LIMIT_MAX:
            return {}, _err(
                "invalid_request", f"{rl_field} must not exceed {_RATE_LIMIT_MAX}.",
                400, rid, key="maximumRequired",
                params={"field": rl_field, "max": _RATE_LIMIT_MAX},
            )

    return patchable, None


async def _rename_access_key_sensors(
    hass: HomeAssistant, access_key_id: str, old_name: str, updated: Any
) -> None:
    """Follow an access key rename on its sensors in place, best-effort."""

    try:
        await async_rename_access_key_sensors(hass, old_name, updated)
    except Exception:
        _LOGGER.warning("Sensor rename failed for access key %s", access_key_id, exc_info=True)


class PhoenixAdminAccessKeyView(PhoenixView):
    """GET/PATCH/DELETE /api/phoenix-mcp/admin/access-keys/{access_key_id} - manage a single access key."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}"
    name = "api:phoenix-mcp:admin:access-key"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
        return _ok(access_key.to_dict(), request_id=rid)

    @require_admin
    async def patch(self, request: web.Request, access_key_id: str) -> web.Response:

        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        if "expires_at" in body:
            return _err("invalid_request", "expires_at is immutable after access key creation.", 400, rid, key="expiresImmutable")

        old_name: str | None = None
        async with data.store.async_lock:
            access_key = data.store.get_access_key_by_id(access_key_id)
            if access_key is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
            old_name = access_key.name

            patchable, invalid = _validated_access_key_patch(body, access_key, access_key_id, data, rid)
            if invalid is not None:
                return invalid
            updated = await data.store.async_patch_access_key(access_key_id, **patchable)
            if updated is None:
                # The store returns None only for an unknown id, which the lookup
                # above already excluded under this same lock. Report it rather
                # than dereference None.
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        if updated is not None and old_name is not None and updated.name != old_name:
            await _rename_access_key_sensors(self.hass, access_key_id, old_name, updated)

        return _ok(updated.to_dict(), request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, access_key_id: str) -> web.Response:
        """Revoke an access key, close its queued approvals, and fire the bus event."""

        rid = request["phoenix_mcp_rid"]
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        # Settings reference validation uses this lock before the access-key-store
        # lock. Revoke follows the same order and clears dependent references in
        # the archival transaction, so a settings PATCH cannot bind an access key in
        # the gap between validation and persistence.
        async with data.settings_update_lock:
            async with data.store.async_lock:
                access_key = data.store.get_access_key_by_id(access_key_id)
                if access_key is None:
                    return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

                access_key_name = access_key.name
                now = utcnow()

                await data.store.async_archive_access_key(access_key_id, revoked=True, revoked_at=now)
                cancel_expiry_timer(data, access_key_id)
                # If this access key was the Assist binding, the voice agent's access key or
                # the AI Task access key, clear it in the same transaction as archival.
                _s = data.store.get_settings()
                _patch: dict[str, Any] = {}
                if _s.assist_bound_access_key_id == access_key_id:
                    _patch["assist_bound_access_key_id"] = None
                if _s.voice_agent_access_key_id == access_key_id:
                    _patch["voice_agent_access_key_id"] = None
                if _s.ai_task_access_key_id == access_key_id:
                    _patch["ai_task_access_key_id"] = None
                if _patch:
                    await data.store.async_patch_settings(**_patch)
                # A revoked access key's queued approvals must not linger as approvable
                # entries. Live claims are skipped; a stale marker is closed as
                # interrupted so access key cleanup preserves its uncertainty.
                changed_approvals = await async_cancel_approvals_for_access_key(
                    data.store, access_key_id, REASON_REVOKED,
                    skip_ids=data.approvals_in_progress,
                )
        for approval in changed_approvals:
            dismiss_approval_notification(hass, approval.id)
            fire_approval_resolved_event(hass, approval)

        # Advisory leases the access key still held. Outside the store lock: they are
        # in-memory and unrelated to stored state.
        release_access_key_leases(data, access_key_id)

        if data.async_sync_voice_agent is not None:
            data.async_sync_voice_agent()

        if "voice_agent_access_key_id" in _patch:
            # The voice agent lost its access key and is unregistered; remove any
            # Phoenix-created Assist pipeline so a broken assistant is not left behind.
            await async_remove_assist_pipeline(hass, data)

        if "ai_task_access_key_id" in _patch and data.async_sync_ai_task is not None:
            # The AI Task lost its access key and is not fully configured; remove the AI
            # Task entity from HA (and the picker).
            data.async_sync_ai_task()

        data.rate_limiter.destroy(access_key_id)
        data.rate_limit_notified.pop(access_key_id, None)
        data.access_key_counters.pop(access_key_id, None)
        data.stale_tools_advised.pop(access_key_id, None)

        hass.bus.async_fire("phoenix_mcp_access_key_revoked", {
            "access_key_id": access_key_id,
            "access_key_name": access_key_name,
            "revoked_by": user.id,
            "timestamp": now.isoformat(),
        })

        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        slug = access_key_name_slug(access_key_name)
        if data.async_on_access_key_archived:
            try:
                await data.async_on_access_key_archived(slug)
            except Exception:
                _LOGGER.warning("Sensor removal failed for access key %s; entity registry may have ghost entries", access_key_id, exc_info=True)

        return web.Response(status=204, headers={"X-Phoenix-Request-ID": rid})


def _presets_disabled(data: PhoenixData, rid: str) -> web.Response | None:
    """403 for every preset endpoint while the feature toggle is off."""
    if not data.store.get_settings().access_key_presets_enabled:
        return _err("forbidden", "Access key presets are disabled. Enable them in Settings.", 403, rid, key="presetsDisabled")
    return None


def _validate_preset_name(raw: object, rid: str) -> str | web.Response:
    """Return a validated preset name from a request body, or an error response."""
    if not isinstance(raw, str) or not raw.strip():
        return _err("invalid_request", "name is required.", 400, rid, key="nameRequired")
    name = raw.strip()
    if len(name) > PRESET_NAME_MAX_LENGTH:
        return _err(
            "invalid_request", f"name must be at most {PRESET_NAME_MAX_LENGTH} characters.",
            400, rid, key="presetNameTooLong", params={"max": PRESET_NAME_MAX_LENGTH},
        )
    if any(c in name for c in _INJECTION_CHARS):
        return _err(
            "invalid_request", "name contains invalid characters.", 400, rid,
            key="presetNameInvalidCharacters",
        )
    return name


class PhoenixAdminAccessKeyPresetsView(PhoenixView):
    """POST /api/phoenix-mcp/admin/access-keys/{access_key_id}/presets - save the current settings as a new preset."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/presets"
    name = "api:phoenix-mcp:admin:access-key:presets"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if (gate := _presets_disabled(data, rid)) is not None:
            return gate
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        name = _validate_preset_name(body.get("name"), rid)
        if isinstance(name, web.Response):
            return name

        async with data.store.async_lock:
            try:
                updated = await data.store.async_add_preset(access_key_id, name)
            except PresetLimitError as exc:
                return _err("invalid_request", str(exc), 400, rid,
                            key="presetLimitReached", params={"max": MAX_PRESETS_PER_ACCESS_KEY})
            except PresetNameTakenError as exc:
                return _err("invalid_request", str(exc), 400, rid, key="presetNameTaken")
            except ValueError as exc:
                return _err(
                    "invalid_request", str(exc), 400, rid,
                    key="presetSaveRejected",
                )
            if updated is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        _audit_admin(self.hass, request, rid, request.path)
        return _ok(updated.to_dict(), status=201, request_id=rid)


class PhoenixAdminAccessKeyPresetView(PhoenixView):
    """PATCH/DELETE /api/phoenix-mcp/admin/access-keys/{access_key_id}/presets/{preset_id} - rename or delete a preset."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/presets/{preset_id}"
    name = "api:phoenix-mcp:admin:access-key:preset"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, access_key_id: str, preset_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if (gate := _presets_disabled(data, rid)) is not None:
            return gate
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        name = _validate_preset_name(body.get("name"), rid)
        if isinstance(name, web.Response):
            return name

        async with data.store.async_lock:
            try:
                updated = await data.store.async_rename_preset(access_key_id, preset_id, name)
            except LookupError:
                return _err("not_found", "Preset not found.", 404, rid, key="presetNotFound")
            except ValueError as exc:
                return _err(
                    "invalid_request", str(exc), 400, rid,
                    key="presetRenameRejected",
                )
            if updated is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        _audit_admin(self.hass, request, rid, request.path)
        return _ok(updated.to_dict(), request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, access_key_id: str, preset_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if (gate := _presets_disabled(data, rid)) is not None:
            return gate

        async with data.store.async_lock:
            try:
                updated = await data.store.async_delete_preset(access_key_id, preset_id)
            except LookupError:
                return _err("not_found", "Preset not found.", 404, rid, key="presetNotFound")
            except ValueError as exc:
                return _err(
                    "invalid_request", str(exc), 400, rid,
                    key="presetDeleteRejected",
                )
            if updated is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        _audit_admin(self.hass, request, rid, request.path)
        return _ok(updated.to_dict(), request_id=rid)


class PhoenixAdminAccessKeyPresetApplyView(PhoenixView):
    """POST /api/phoenix-mcp/admin/access-keys/{access_key_id}/presets/{preset_id}/apply.

    Applying a different preset first saves the live state into the outgoing one; applying the
    active one reverts to its saved state. Routes through the same store paths as a manual
    PATCH.
    """

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/presets/{preset_id}/apply"
    name = "api:phoenix-mcp:admin:access-key:preset:apply"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, access_key_id: str, preset_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        if (gate := _presets_disabled(data, rid)) is not None:
            return gate
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        async with data.store.async_lock:
            access_key = data.store.get_access_key_by_id(access_key_id)
            if access_key is None:
                return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")
            preset = next((p for p in access_key.presets if p.id == preset_id), None)
            if preset is None:
                return _err("not_found", "Preset not found.", 404, rid, key="presetNotFound")
            if preset.pass_through and not access_key.pass_through and body.get("confirm_pass_through") is not True:
                return _err(
                    "invalid_request",
                    "confirm_pass_through: true is required when the preset enables pass_through.",
                    400, rid, key="presetPassThroughConfirmationRequired",
                )

            # SWITCH: the outgoing preset absorbs the live state first, so
            # nothing the admin changed since the last switch is ever lost.
            # Applying the active preset itself skips this (that is the revert).
            if preset_id != access_key.active_preset_id and access_key.active_preset_id:
                await data.store.async_sync_preset(access_key_id, access_key.active_preset_id)

            await data.store.async_patch_access_key(
                access_key_id,
                persona=preset.persona,
                pass_through=preset.pass_through,
                use_assist_exposure=preset.use_assist_exposure if preset.pass_through else False,
                announce_all_tools=preset.announce_all_tools,
                confirm_inline_wait_seconds=preset.confirm_inline_wait_seconds,
                rate_limit_requests=preset.rate_limit_requests,
                rate_limit_burst=preset.rate_limit_burst,
                **preset.caps,
            )
            # Deep-copy the stored tree so live edits never mutate the preset.
            await data.store.async_set_permissions(
                access_key_id, PermissionTree.from_dict(preset.permissions.to_dict()),
            )
            access_key.active_preset_id = preset.id
            await data.store.async_save()

        # New limits take effect cleanly rather than inheriting the old window.
        data.rate_limiter.destroy(access_key_id)
        _audit_admin(self.hass, request, rid, request.path)
        return _ok(access_key.to_dict(), request_id=rid)


class PhoenixAdminAccessKeyStatsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys/{access_key_id}/stats - in-memory counters for one access key."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/stats"
    name = "api:phoenix-mcp:admin:access-key_stats"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        counters = data.access_key_counters.get(access_key_id, {
            "request_count": 0,
            "denied_count": 0,
            "rate_limit_hits": 0,
        })

        last_used = access_key.last_used_at.isoformat() if access_key.last_used_at else None

        status = "expired" if access_key.is_expired() else "active"

        return _ok({
            "access_key_id": access_key_id,
            "access_key_name": access_key.name,
            "request_count": counters["request_count"],
            "denied_count": counters["denied_count"],
            "rate_limit_hits": counters["rate_limit_hits"],
            "last_used_at": last_used,
            "status": status,
        }, request_id=rid)


class PhoenixAdminAccessKeyConnectionView(PhoenixView):
    """GET /api/phoenix-mcp/admin/access-keys/{access_key_id}/connection - connection signals.

    For the onboarding wizard. Streamable HTTP registers no session, so ``request_count``
    above zero is the signal.
    """

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/connection"
    name = "api:phoenix-mcp:admin:access-key_connection"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        data: PhoenixData = self.hass.data[DOMAIN]
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        counters = data.access_key_counters.get(access_key_id, {})
        last_used = access_key.last_used_at.isoformat() if access_key.last_used_at else None

        return _ok({
            "last_used_at": last_used,
            "request_count": counters.get("request_count", 0),
        }, request_id=rid)


class PhoenixAdminAccessKeyRotateView(PhoenixView):
    """POST /api/phoenix-mcp/admin/access-keys/{access_key_id}/rotate - replace the raw access key value atomically."""

    url = "/api/phoenix-mcp/admin/access-keys/{access_key_id}/rotate"
    name = "api:phoenix-mcp:admin:access-key_rotate"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, access_key_id: str) -> web.Response:
        rid = request["phoenix_mcp_rid"]
        hass = self.hass
        data: PhoenixData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        async with data.store.async_lock:
            result = await data.store.async_rotate_access_key(access_key_id)

        if result is None:
            return _err("not_found", "Access key not found.", 404, rid, key="accessKeyNotFound")

        access_key, raw_access_key = result

        hass.bus.async_fire("phoenix_mcp_access_key_rotated", {
            "access_key_id": access_key.id,
            "access_key_name": access_key.name,
            "rotated_by": user.id,
            "timestamp": utcnow().isoformat(),
        })

        data.audit.record(
            request_id=rid,
            access_key_id="admin",
            access_key_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        response_body = access_key.to_dict()
        response_body["access_key"] = raw_access_key
        return _ok(response_body, request_id=rid)
