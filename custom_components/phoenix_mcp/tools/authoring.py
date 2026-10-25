"""Authoring tools: automation, script and scene CRUD over the YAML config store.

The three domains share one shape and one module: each stores its configs in a
top-level YAML file (automations.yaml, scripts.yaml, scenes.yaml) that a human may
also edit by hand, and is written under its own asyncio.Lock keyed into hass.data.
An edit may carry an expected_hash (a content_hash from the matching read tool),
checked before gating and again at apply time; a delete captures the reviewed
entry's hash and compares it again under that lock.

`yaml_includes` does the reads and writes. Reads follow every !include, so an
entry is found wherever it is defined. Writes reach only Home Assistant's own
editor file for the domain and only entries its editor would change; an entry in
a split file, an include directory, a package or inline in configuration.yaml is
hand-written YAML and is refused before any approval exists.

Scene writes add one check: a scene captures member entities' states, so
`_unwritable_scene_members` refuses a scene naming an entity the access key cannot
WRITE; otherwise an access key could snapshot and later restore entities outside its
scope. mcp_view owns the transport and the registries and imports from here;
this module does not import it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from functools import partial
from collections.abc import Awaitable, Callable
from traceback import format_exc, format_tb
from typing import Any, Literal

import voluptuous as vol

from homeassistant.components.automation.config import async_validate_config_item as _validate_automation_config
from homeassistant.components.script.config import async_validate_config_item as _validate_script_config
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.file import write_utf8_file_atomic as _write_utf8_file_atomic
from homeassistant.util.yaml import dump as _yaml_dump, load_yaml as _load_yaml
from homeassistant.helpers import entity_registry as er
from homeassistant.core import HomeAssistant

from ..authoring_validation import authoring_input_error, validation_message
from ..const import CAP_DENY, DOMAIN, TRACE_SCOPE_MAX_DEPTH
from ..data import PhoenixData
from ..entity_registration import async_observe_registration
from ..helpers import content_hash, dict_arg, diff_summary_fields as _summary, effective_cap, str_arg, _strict_phoenix_log_text
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _cas_conflict, _gate, _record_version, _tool_error, _tool_success, _truncate
from ..policy_engine import Permission, filter_entities_for_access_key, resolve
from ..request_authority import AuthorityChangedError, check_request_authority
from ..access_key_store import AccessKeyRecord
from .. import yaml_includes

_LOGGER = logging.getLogger(__name__)


_AUTOMATION_YAML = "automations.yaml"
_AUTOMATION_LOCK_KEY = f"{DOMAIN}_automation_lock"
_SCRIPT_CONFIG_PATH = "scripts.yaml"
_SCRIPT_LOCK_KEY = f"{DOMAIN}_script_lock"


async def _finish_entity_write(
    hass: HomeAssistant, access_key: AccessKeyRecord, *, domain: Literal["scene", "script"],
    storage_id: str, config: dict, tool_name: str,
) -> tuple[dict, str, str]:
    """Reload a saved, versioned write and report observation separately.

    Neither a failed reload nor a registration timeout undoes the committed
    configuration. Keep cancellation and authority failures at the dispatch
    boundary; never retry a write or reload here.
    """
    body: dict[str, Any] = {
        "write_success": True, f"{domain}_id": storage_id, "config": config,
        "reload_status": "completed",
    }
    check_request_authority()
    try:
        await hass.services.async_call(domain, "reload", blocking=True)
    except AuthorityChangedError:
        raise
    except Exception as exc:
        check_request_authority()
        _LOGGER.error(
            "%s reload failed after configuration was saved: %s",
            tool_name, _strict_phoenix_log_text(format_exc(), access_key, hass),
        )
        body.update(
            reload_status="failed",
            registration={"status": "not_checked", "entity_id": None, "state_present": None},
            error=(
                _strict_phoenix_log_text(str(exc), access_key, hass)[:500]
                if isinstance(exc, HomeAssistantError)
                else "Internal reload error. Check HA logs for details."
            ),
            message="Configuration saved, but reload failed. Inspect the saved configuration and HA logs before retrying reload; do not repeat the create operation.",
        )
        result = _tool_success(json.dumps(body, default=str))
        result["isError"] = True
        return result, "invalid_request", tool_name

    try:
        body["registration"] = await async_observe_registration(hass, access_key, domain, storage_id)
    except AuthorityChangedError:
        raise
    except Exception:
        check_request_authority()
        _LOGGER.error(
            "%s registration observation failed: %s",
            tool_name, _strict_phoenix_log_text(format_exc(), access_key, hass),
        )
        body["registration"] = {"status": "observation_failed", "entity_id": None, "state_present": None}
    check_request_authority()
    if domain == "script":
        from .discovery import reference_warnings  # noqa: PLC0415
        if warnings := reference_warnings(hass, access_key, "script", config):
            body["warnings"] = warnings
    status = body["registration"]["status"]
    if status == "timed_out":
        body["message"] = "Configuration saved and reload completed. Entity registration or live state was not observed within the wait budget and access key scope. Retry a lookup, not the write."
    elif status == "disabled":
        body["message"] = "Configuration saved and reload completed. The registered entity is disabled."
    elif status == "observation_failed":
        body["message"] = "Configuration saved and reload completed, but entity observation failed. Check HA logs and retry a lookup, not the write."
    return _tool_success(json.dumps(body, indent=2, default=str)), "allowed", tool_name


def _get_automation_lock(hass: HomeAssistant) -> asyncio.Lock:
    if _AUTOMATION_LOCK_KEY not in hass.data:
        hass.data[_AUTOMATION_LOCK_KEY] = asyncio.Lock()
    return hass.data[_AUTOMATION_LOCK_KEY]


def _read_automations_yaml(path: str) -> list:
    if not os.path.isfile(path):
        return []
    data = _load_yaml(path)
    return data if isinstance(data, list) else []


def _write_automations_yaml(path: str, data: list) -> None:
    contents = _yaml_dump(data)
    _write_utf8_file_atomic(path, contents)


def _get_script_lock(hass: HomeAssistant) -> asyncio.Lock:
    if _SCRIPT_LOCK_KEY not in hass.data:
        hass.data[_SCRIPT_LOCK_KEY] = asyncio.Lock()
    return hass.data[_SCRIPT_LOCK_KEY]


def _read_scripts_yaml(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    data = _load_yaml(path)
    return data if isinstance(data, dict) else {}


def _write_scripts_yaml(path: str, data: dict) -> None:
    contents = _yaml_dump(data)
    _write_utf8_file_atomic(path, contents)


def _yaml_file_has_includes(path: str) -> bool:
    """Return True if the file exists and contains YAML !include directives."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "!include" in f.read()
    except OSError:
        return False


_SCENE_CONFIG_PATH = "scenes.yaml"
_SCENE_LOCK_KEY = f"{DOMAIN}_scene_yaml_lock"


def _read_scenes_yaml(path: str) -> list:
    if not os.path.isfile(path):
        return []
    data = _load_yaml(path)
    return data if isinstance(data, list) else []


def _write_scenes_yaml(path: str, data: list) -> None:
    _write_utf8_file_atomic(path, _yaml_dump(data))


def _get_scene_lock(hass: HomeAssistant) -> asyncio.Lock:
    if _SCENE_LOCK_KEY not in hass.data:
        hass.data[_SCENE_LOCK_KEY] = asyncio.Lock()
    return hass.data[_SCENE_LOCK_KEY]


_SCRIPT_ID_RE = re.compile(r"^[a-z0-9_]+$")
_DELETE_EXPECTED_HASH = "_phoenix_delete_expected_hash"
_ENTRY_NOT_CAPTURED = object()


async def _read_config_entry(hass: HomeAssistant, domain: str, entry_id: str) -> Any:
    """Current stored config for one automation/script/scene entry, or None.

    Tag-encoded (a !secret renders as the display string "!secret name"; the
    value is never resolved), so the result is JSON-safe and hashable for the
    compare-and-swap guard.
    """
    return await hass.async_add_executor_job(
        yaml_includes.read_entry, hass.config.config_dir, domain, entry_id,
    )


def _config_read_response(
    id_key: str, entry_id: str, config: Any, resource: str
) -> tuple[dict, str, str]:
    """Shared body for the config-read tools: config plus its content_hash.

    The hash is over the same tag-encoded structure returned here, so an agent
    can echo it straight back as expected_hash on the matching edit tool.
    """
    return _tool_success(json.dumps({
        id_key: entry_id, "config": config, "content_hash": content_hash(config),
    }, indent=2, default=str)), "allowed", resource


async def _entry_cas_conflict(
    args: dict, hass: HomeAssistant, domain: str, entry_id: str, tool_name: str
) -> tuple[dict, str, str] | None:
    """CAS check for an automation/script/scene edit, against its stored config.

    Runs before gating (so a stale edit never becomes a doomed pending approval)
    and again inside the executor (to catch a change made during the approval
    window). No expected_hash skips the check entirely.
    """
    expected = args.get("expected_hash")
    if not expected:
        return None
    current = await _read_config_entry(hass, domain, entry_id)
    return _cas_conflict(expected, current, tool_name)


async def _bind_delete_args(
    args: dict, hass: HomeAssistant, domain: str, entry_id: str
) -> tuple[dict, Any]:
    """Bind a delete request to the exact entry shown in its approval diff."""
    current = await _read_config_entry(hass, domain, entry_id)
    bound = dict(args)
    bound.pop(_DELETE_EXPECTED_HASH, None)
    if current is not None:
        bound[_DELETE_EXPECTED_HASH] = content_hash(current)
    return bound, current


def _delete_cas_conflict(
    args: dict, current: Any, tool_name: str
) -> tuple[dict, str, str] | None:
    """Reject unbound or stale delete approval replay."""
    expected = args.get(_DELETE_EXPECTED_HASH)
    if not isinstance(expected, str) or not expected:
        return (
            _tool_error(
                "This deletion was not bound to reviewed content. Re-read the "
                "configuration and request deletion again."
            ),
            "invalid_request",
            tool_name,
        )
    return _cas_conflict(expected, current, tool_name)


async def _tool_list_automations(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list accessible automation.* entities with their editable id."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "list_automations"
    items: list[dict] = []
    for e in filter_entities_for_access_key(hass.states.async_all(), access_key, hass):
        if not e["entity_id"].startswith("automation."):
            continue
        attrs = e.get("attributes", {})
        items.append({
            "entity_id": e["entity_id"],
            "alias": attrs.get("friendly_name"),
            "automation_id": attrs.get("id"),
            "state": e.get("state"),
        })
    items.sort(key=lambda a: a["entity_id"])
    return _tool_success(json.dumps({"count": len(items), "automations": items}, default=str)), "allowed", "list_automations"


async def _tool_get_automation(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read one automation's current stored config, with a content_hash."""
    args = _with_stored_id(hass, args, "automation")
    if effective_cap(access_key, "cap_automation_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_automation"
    automation_id = str(args.get("automation_id") or "").strip()
    if not automation_id:
        return _tool_error("automation_id is required."), "invalid_request", "get_automation"
    config = await _read_config_entry(hass, "automation", automation_id)
    if config is None:
        return _tool_error(f"No automation found with id '{automation_id}'."), "not_found", f"automation:{automation_id}"
    return _config_read_response("automation_id", automation_id, config, f"automation:{automation_id}")


# The guidance an agent gets when its own config does not validate, one per
# authored domain. Agent-facing error text is deliberately English.
_AUTOMATION_INVALID_MESSAGE = validation_message("automation")
_SCRIPT_INVALID_MESSAGE = validation_message("script")


async def _config_validation_error(
    validator: Callable[[HomeAssistant, str, dict], Awaitable[Any]],
    hass: HomeAssistant,
    config_key: str,
    config: dict,
    *,
    tool_name: str,
    invalid_message: str,
) -> tuple[dict, str, str] | None:
    """Run an HA config validator; return an error triple, or None when the config validates.

    HA's automation and script validators raise vol.Invalid or HomeAssistantError
    for a config the caller got wrong: the routine case, logged at debug and
    answered with bounded field paths and value-free guidance. Any other exception is HA drift or a
    Phoenix bug, so it is logged with a traceback and reported as an internal
    error; calling it a validation failure would send the agent to rewrite a
    config that was never the problem.
    """
    try:
        if await validator(hass, config_key, config) is None:
            return _tool_error(invalid_message), "invalid_request", tool_name
    except vol.Invalid as exc:
        domain = "automation" if "automation" in tool_name else "script"
        return _tool_error(validation_message(domain, exc)), "invalid_request", tool_name
    except HomeAssistantError:
        return _tool_error(invalid_message), "invalid_request", tool_name
    except Exception as exc:
        _LOGGER.error("%s config validation raised an unexpected error:\n%s", tool_name, "".join(format_tb(exc.__traceback__)))
        return _tool_error("Internal error validating this config."), "invalid_request", tool_name
    return None


def _automation_argument_error(args: dict, *, create: bool) -> str | None:
    """Reject malformed or conflicting identity inputs on every ingress."""
    findings = []
    aid = args.get("automation_id")
    if "automation_id" in args and (not isinstance(aid, str) or not aid.strip()):
        findings.append("automation_id must be a non-empty stored automation ID.")
    if not create and aid is None:
        findings.append("automation_id is required.")
    config = args.get("config")
    if error := authoring_input_error(args, "automation"):
        findings.append(error)
    if isinstance(config, dict) and "id" in config and (
        not isinstance(aid, str) or config["id"] != aid.strip()
    ):
        findings.append("config.id must match automation_id; omit config.id when creating a new automation.")
    expected = args.get("expected_hash")
    if "expected_hash" in args and (not isinstance(expected, str) or not expected):
        findings.append("expected_hash must be a non-empty content_hash from get_automation.")
    return " ".join(findings) if findings else None


def _automation_op_error(op: yaml_includes.OpResult, tool_name: str) -> tuple[dict, str, str]:
    """Map identity and storage refusals to the existing tool envelope."""
    outcome = "invalid_request" if op.error_kind in ("conflict", "io_error") else "denied"
    return _tool_error(op.error_text), outcome, tool_name


async def _editor_file_refusal(
    hass: HomeAssistant, domain: str, entry_id: str, tool_name: str, *, create: bool
) -> tuple[dict, str, str] | None:
    """Refuse, before any approval exists, a change Home Assistant's editor could not make.

    Only entries in the domain's editor file are written; anything defined in
    hand-written YAML stays the operator's. The write itself checks again.
    """
    op = await hass.async_add_executor_job(
        partial(yaml_includes.write_target_refusal, hass.config.config_dir, domain, entry_id, create=create)
    )
    if op is None:
        return None
    outcome = "invalid_request" if op.error_kind == "io_error" else "denied"
    return _tool_error(op.error_text), outcome, tool_name


async def _automation_write_precheck(
    args: dict, hass: HomeAssistant, tool_name: str, *, require_id: bool
) -> tuple[dict, str, str] | None:
    """Pre-gate validation for automation writes; None means OK to proceed.

    Checks automation_id presence and config schema so a doomed request is
    rejected before a pending approval is created. The executor re-validates at
    apply time.
    """
    error = _automation_argument_error(args, create=not require_id)
    if error:
        return _tool_error(error), "invalid_request", tool_name
    automation_id = (args.get("automation_id") or "").strip()
    config = args["config"]
    invalid = await _config_validation_error(
        _validate_automation_config, hass, automation_id or "phoenix_mcp_validate", config,
        tool_name=tool_name, invalid_message=_AUTOMATION_INVALID_MESSAGE,
    )
    if invalid is not None:
        return invalid
    if automation_id:
        op = await hass.async_add_executor_job(
            yaml_includes.automation_write, hass.config.config_dir, automation_id,
            config, not require_id, args.get("expected_hash"), True,
        )
        if not op.ok:
            return _automation_op_error(op, tool_name)
        return None
    return await _editor_file_refusal(hass, "automation", "", tool_name, create=True)


async def _tool_create_automation(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a new UI automation by appending to automations.yaml."""
    if effective_cap(access_key, "cap_automation_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "create_automation"
    pre = await _automation_write_precheck(args, hass, "create_automation", require_id=False)
    if pre is not None:
        return pre
    blocked = await _gate(
        "cap_automation_write", access_key, hass, data,
        tool_name="create_automation", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_create_automation(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_automation(args, access_key, hass, data)


def _automation_receipt(
    hass: HomeAssistant, access_key: AccessKeyRecord, automation_id: str, config: dict, tool_name: str,
) -> tuple[dict, str, str]:
    """Name the saved automation's id and entity, and flag unresolvable references.

    The reload above is blocking and in-process, so the entity is registered by now.
    """
    from .discovery import reference_warnings  # noqa: PLC0415

    entity_id = _authoring_entity_id(hass, "automation", automation_id)
    visible = entity_id is not None and resolve(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
    body: dict[str, Any] = {"automation_id": automation_id, "entity_id": entity_id if visible else None, "config": config}
    if warnings := reference_warnings(hass, access_key, "automation", config):
        body["warnings"] = warnings
    return _tool_success(json.dumps(body, indent=2, default=str)), "allowed", tool_name


async def _execute_create_automation(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    error = _automation_argument_error(args, create=True)
    if error:
        return _tool_error(error), "invalid_request", "create_automation"
    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "create_automation"

    # A restore of a deleted automation recreates it under its original id (passed
    # explicitly) so it returns in place and re-restoring is idempotent; a fresh
    # create mints a new id.
    automation_id = str(args.get("automation_id") or "").strip() or "phoenix_mcp_" + uuid.uuid4().hex[:16]
    config = {k: v for k, v in config.items() if k != "id"}
    config["id"] = automation_id

    # Validate config but write the original (not the validated result) to YAML.
    # HA's validator may return internal representations that don't round-trip
    # through YAML cleanly. HA's own automation UI follows the same pattern:
    # write the user's config and let automation.reload normalize it.
    invalid = await _config_validation_error(
        _validate_automation_config, hass, automation_id, config,
        tool_name="create_automation", invalid_message=_AUTOMATION_INVALID_MESSAGE,
    )
    if invalid is not None:
        return invalid

    lock = _get_automation_lock(hass)
    try:
        async with lock:
            check_request_authority()
            op = await hass.async_add_executor_job(
                yaml_includes.automation_write, hass.config.config_dir, automation_id,
                config, True, args.get("expected_hash"), False, not bool(args.get("automation_id")),
            )
            if not op.ok:
                return _automation_op_error(op, "create_automation")
        await hass.services.async_call("automation", "reload", blocking=True)
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("create_automation failed")
        return _tool_error("Failed to create automation. Check HA logs for details."), "invalid_request", "create_automation"

    await _record_version(
        data, access_key, resource_type="automation", resource_id=automation_id,
        action="create", before=None, after=config, alias=config.get("alias"),
    )
    return _automation_receipt(hass, access_key, automation_id, config, "create_automation")


async def _tool_edit_automation(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: replace the config of an existing UI automation."""
    args = _with_stored_id(hass, args, "automation")
    if effective_cap(access_key, "cap_automation_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "edit_automation"
    pre = await _automation_write_precheck(args, hass, "edit_automation", require_id=True)
    if pre is not None:
        return pre
    blocked = await _gate(
        "cap_automation_write", access_key, hass, data,
        tool_name="edit_automation", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_edit_automation(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_edit_automation(args, access_key, hass, data)


async def _execute_edit_automation(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    error = _automation_argument_error(args, create=False)
    if error:
        return _tool_error(error), "invalid_request", "edit_automation"
    # The stored `id` key, not an entity_id.
    automation_id = args.get("automation_id", "").strip()
    if not automation_id:
        return _tool_error("automation_id is required."), "invalid_request", "edit_automation"

    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "edit_automation"

    config = {k: v for k, v in config.items() if k != "id"}
    config["id"] = automation_id

    invalid = await _config_validation_error(
        _validate_automation_config, hass, automation_id, config,
        tool_name="edit_automation", invalid_message=_AUTOMATION_INVALID_MESSAGE,
    )
    if invalid is not None:
        return invalid

    lock = _get_automation_lock(hass)
    try:
        async with lock:
            check_request_authority()
            op = await hass.async_add_executor_job(
                yaml_includes.automation_write, hass.config.config_dir, automation_id,
                config, False, args.get("expected_hash"),
            )
            if not op.ok:
                return _automation_op_error(op, "edit_automation")
            before_cfg = op.before
        await hass.services.async_call("automation", "reload", blocking=True)
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("edit_automation failed")
        return _tool_error("Failed to edit automation. Check HA logs for details."), "invalid_request", "edit_automation"

    await _record_version(
        data, access_key, resource_type="automation", resource_id=automation_id,
        action="edit", before=before_cfg, after=config, alias=config.get("alias"),
    )
    return _automation_receipt(hass, access_key, automation_id, config, "edit_automation")


async def _tool_delete_automation(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: permanently delete a UI automation."""
    args = _with_stored_id(hass, args, "automation")
    if effective_cap(access_key, "cap_automation_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_automation"
    automation_id = str_arg(args.get("automation_id")).strip()
    if not automation_id:
        return _tool_error("automation_id is required."), "invalid_request", "delete_automation"
    args, current = await _bind_delete_args(args, hass, "automation", automation_id)
    if current is None:
        return _tool_error(f"No automation found with id '{automation_id}'."), "denied", "delete_automation"
    refused = await _editor_file_refusal(hass, "automation", automation_id, "delete_automation", create=False)
    if refused is not None:
        return refused
    blocked = await _gate(
        "cap_automation_write", access_key, hass, data,
        tool_name="delete_automation", args=args, request_id=request_id,
        client_ip=client_ip,
        diff=lambda: _build_diff_delete_automation(
            args, access_key, hass, reviewed=current
        ),
    )
    if blocked is not None:
        return blocked
    return await _execute_delete_automation(args, access_key, hass, data)


def _remove_registry_entry(hass: HomeAssistant, domain: str, item_id: str) -> None:
    """Drop a deleted automation's or script's registry entry, as HA's own delete does.

    A reload alone leaves it behind as a restored, unavailable entity.
    """
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id(domain, domain, item_id):
        registry.async_remove(entity_id)


async def _execute_delete_automation(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    automation_id = args.get("automation_id", "").strip()
    if not automation_id:
        return _tool_error("automation_id is required."), "invalid_request", "delete_automation"

    path = os.path.join(hass.config.config_dir, _AUTOMATION_YAML)
    lock = _get_automation_lock(hass)
    try:
        async with lock:
            check_request_authority()
            current = await _read_config_entry(hass, "automation", automation_id)
            check_request_authority()
            stale = _delete_cas_conflict(args, current, "delete_automation")
            if stale is not None:
                return stale
            op = await hass.async_add_executor_job(
                yaml_includes.perform_delete, hass.config.config_dir, "automation",
                automation_id, args[_DELETE_EXPECTED_HASH],
            )
            if op.fallback:
                if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                    return _tool_error("automations.yaml uses !include directives. Phoenix MCP cannot safely edit it without destroying the include structure."), "denied", "delete_automation"
                items = await hass.async_add_executor_job(_read_automations_yaml, path)
                removed = next((a for a in items if a.get("id") == automation_id), None)
                check_request_authority()
                stale = _delete_cas_conflict(args, removed, "delete_automation")
                if stale is not None:
                    return stale
                filtered = [a for a in items if a.get("id") != automation_id]
                if len(filtered) == len(items):
                    return _tool_error(f"No automation found with id '{automation_id}'."), "denied", "delete_automation"
                await hass.async_add_executor_job(_write_automations_yaml, path, filtered)
            elif not op.ok:
                outcome = "invalid_request" if op.error_kind == "conflict" else "denied"
                return _tool_error(op.error_text), outcome, "delete_automation"
            else:
                removed = op.before
        await hass.services.async_call("automation", "reload", blocking=True)
        check_request_authority()
        _remove_registry_entry(hass, "automation", automation_id)
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("delete_automation failed")
        return _tool_error("Failed to delete automation. Check HA logs for details."), "invalid_request", "delete_automation"

    await _record_version(
        data, access_key, resource_type="automation", resource_id=automation_id,
        action="delete", before=removed, after=None,
        alias=removed.get("alias") if isinstance(removed, dict) else None,
    )
    return _tool_success(f"Automation '{automation_id}' deleted successfully."), "allowed", "delete_automation"


async def _tool_list_scripts(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list accessible script.* entities with their editable id."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "list_scripts"
    items: list[dict] = []
    for e in filter_entities_for_access_key(hass.states.async_all(), access_key, hass):
        entity_id = e["entity_id"]
        if not entity_id.startswith("script."):
            continue
        entry = er.async_get(hass).async_get(entity_id)
        items.append({
            "entity_id": entity_id,
            "alias": e.get("attributes", {}).get("friendly_name"),
            "script_id": entry.unique_id if entry is not None and entry.platform == "script" else entity_id.split(".", 1)[1],
            "state": e.get("state"),
        })
    items.sort(key=lambda s: s["entity_id"])
    return _tool_success(json.dumps({"count": len(items), "scripts": items}, default=str)), "allowed", "list_scripts"


async def _tool_get_script(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read one script's current stored config, with a content_hash."""
    args = _with_stored_id(hass, args, "script")
    if effective_cap(access_key, "cap_script_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_script"
    script_id = str(args.get("script_id") or "").strip()
    if not script_id:
        return _tool_error("script_id is required."), "invalid_request", "get_script"
    config = await _read_config_entry(hass, "script", script_id)
    if config is None:
        return _tool_error(f"No script found with id '{script_id}'."), "not_found", f"script:{script_id}"
    return _config_read_response("script_id", script_id, config, f"script:{script_id}")


def _script_argument_error(args: dict) -> str | None:
    """Combine script identity and config errors without inventing IDs."""
    findings = []
    script_id = args.get("script_id")
    if not isinstance(script_id, str) or not script_id.strip():
        findings.append("script_id is required and must be a non-empty string.")
    elif not _SCRIPT_ID_RE.fullmatch(script_id.strip()):
        findings.append("script_id must contain only lowercase letters, digits, and underscores.")
    if error := authoring_input_error(args, "script"):
        findings.append(error)
    return " ".join(findings) if findings else None


async def _script_write_precheck(args: dict, hass: HomeAssistant, tool_name: str) -> tuple[dict, str, str] | None:
    """Pre-gate validation for script writes; None means OK to proceed.

    Checks script_id format and config schema so a doomed request is rejected
    before a pending approval is created. The executor re-validates at apply
    time.
    """
    if error := _script_argument_error(args):
        return _tool_error(error), "invalid_request", tool_name
    script_id = args["script_id"].strip()
    config = args["config"]
    invalid = await _config_validation_error(
        _validate_script_config, hass, script_id, config,
        tool_name=tool_name, invalid_message=_SCRIPT_INVALID_MESSAGE,
    )
    if invalid is not None:
        return invalid
    return await _editor_file_refusal(hass, "script", script_id, tool_name, create=tool_name == "create_script")


async def _tool_create_script(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a new script in scripts.yaml."""
    if effective_cap(access_key, "cap_script_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "create_script"
    pre = await _script_write_precheck(args, hass, "create_script")
    if pre is not None:
        return pre
    blocked = await _gate(
        "cap_script_write", access_key, hass, data,
        tool_name="create_script", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_create_script(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_script(args, access_key, hass, data)


async def _execute_create_script(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    if error := _script_argument_error(args):
        return _tool_error(error), "invalid_request", "create_script"
    script_id = args["script_id"].strip()
    config = args["config"]

    invalid = await _config_validation_error(
        _validate_script_config, hass, script_id, config,
        tool_name="create_script", invalid_message=_SCRIPT_INVALID_MESSAGE,
    )
    if invalid is not None:
        return invalid

    path = hass.config.path(_SCRIPT_CONFIG_PATH)
    lock = _get_script_lock(hass)
    try:
        async with lock:
            check_request_authority()
            op = await hass.async_add_executor_job(
                yaml_includes.perform_create, hass.config.config_dir, "script",
                script_id, config,
            )
            if op.fallback:
                if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                    return _tool_error("scripts.yaml uses !include directives. Phoenix MCP cannot safely edit it without destroying the include structure."), "denied", "create_script"
                scripts = await hass.async_add_executor_job(_read_scripts_yaml, path)
                if script_id in scripts:
                    return _tool_error(f"A script with id '{script_id}' already exists. Use edit_script to update it."), "invalid_request", "create_script"
                scripts[script_id] = config
                await hass.async_add_executor_job(_write_scripts_yaml, path, scripts)
            elif not op.ok:
                outcome = "invalid_request" if op.error_kind == "already_exists" else "denied"
                return _tool_error(op.error_text), outcome, "create_script"
            await _record_version(
                data, access_key, resource_type="script", resource_id=script_id,
                action="create", before=None, after=config, alias=config.get("alias"),
            )
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("create_script failed")
        return _tool_error("Failed to create script. Check HA logs for details."), "invalid_request", "create_script"

    return await _finish_entity_write(
        hass, access_key, domain="script", storage_id=script_id, config=config, tool_name="create_script",
    )


async def _tool_edit_script(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: replace the config of an existing script in scripts.yaml."""
    args = _with_stored_id(hass, args, "script")
    if effective_cap(access_key, "cap_script_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "edit_script"
    pre = await _script_write_precheck(args, hass, "edit_script")
    if pre is not None:
        return pre
    stale = await _entry_cas_conflict(
        args, hass, "script", str(args.get("script_id") or "").strip(), "edit_script",
    )
    if stale is not None:
        return stale
    blocked = await _gate(
        "cap_script_write", access_key, hass, data,
        tool_name="edit_script", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_edit_script(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_edit_script(args, access_key, hass, data)


async def _execute_edit_script(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    if error := _script_argument_error(args):
        return _tool_error(error), "invalid_request", "edit_script"
    script_id = args["script_id"].strip()
    config = args["config"]

    # Re-checked here (not just pre-gate) so a change made during the approval
    # window is caught before the write lands.
    stale = await _entry_cas_conflict(args, hass, "script", script_id, "edit_script")
    if stale is not None:
        return stale

    invalid = await _config_validation_error(
        _validate_script_config, hass, script_id, config,
        tool_name="edit_script", invalid_message=_SCRIPT_INVALID_MESSAGE,
    )
    if invalid is not None:
        return invalid

    path = hass.config.path(_SCRIPT_CONFIG_PATH)
    lock = _get_script_lock(hass)
    try:
        async with lock:
            check_request_authority()
            op = await hass.async_add_executor_job(
                yaml_includes.perform_edit, hass.config.config_dir, "script",
                script_id, config,
            )
            if op.fallback:
                if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                    return _tool_error("scripts.yaml uses !include directives. Phoenix MCP cannot safely edit it without destroying the include structure."), "denied", "edit_script"
                scripts = await hass.async_add_executor_job(_read_scripts_yaml, path)
                if script_id not in scripts:
                    return _tool_error(f"No script found with id '{script_id}'."), "denied", "edit_script"
                before_cfg = scripts[script_id]
                scripts[script_id] = config
                await hass.async_add_executor_job(_write_scripts_yaml, path, scripts)
            elif not op.ok:
                return _tool_error(op.error_text), "denied", "edit_script"
            else:
                before_cfg = op.before
            await _record_version(
                data, access_key, resource_type="script", resource_id=script_id,
                action="edit", before=before_cfg, after=config, alias=config.get("alias"),
            )
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("edit_script failed")
        return _tool_error("Failed to edit script. Check HA logs for details."), "invalid_request", "edit_script"

    return await _finish_entity_write(
        hass, access_key, domain="script", storage_id=script_id, config=config, tool_name="edit_script",
    )


async def _tool_delete_script(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: permanently delete a script from scripts.yaml."""
    args = _with_stored_id(hass, args, "script")
    if effective_cap(access_key, "cap_script_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_script"
    script_id = str_arg(args.get("script_id")).strip()
    if not script_id:
        return _tool_error("script_id is required."), "invalid_request", "delete_script"
    if not _SCRIPT_ID_RE.match(script_id):
        return _tool_error("Invalid script ID format."), "invalid_request", "delete_script"
    args, current = await _bind_delete_args(args, hass, "script", script_id)
    if current is None:
        return _tool_error(f"No script found with id '{script_id}'."), "denied", "delete_script"
    refused = await _editor_file_refusal(hass, "script", script_id, "delete_script", create=False)
    if refused is not None:
        return refused
    blocked = await _gate(
        "cap_script_write", access_key, hass, data,
        tool_name="delete_script", args=args, request_id=request_id,
        client_ip=client_ip,
        diff=lambda: _build_diff_delete_script(args, access_key, hass, reviewed=current),
    )
    if blocked is not None:
        return blocked
    return await _execute_delete_script(args, access_key, hass, data)


async def _execute_delete_script(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    script_id = args.get("script_id", "").strip()
    if not script_id:
        return _tool_error("script_id is required."), "invalid_request", "delete_script"
    if not _SCRIPT_ID_RE.match(script_id):
        return _tool_error("Invalid script ID format."), "invalid_request", "delete_script"

    path = hass.config.path(_SCRIPT_CONFIG_PATH)
    lock = _get_script_lock(hass)
    try:
        async with lock:
            check_request_authority()
            current = await _read_config_entry(hass, "script", script_id)
            check_request_authority()
            stale = _delete_cas_conflict(args, current, "delete_script")
            if stale is not None:
                return stale
            op = await hass.async_add_executor_job(
                yaml_includes.perform_delete, hass.config.config_dir, "script",
                script_id, args[_DELETE_EXPECTED_HASH],
            )
            if op.fallback:
                if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                    return _tool_error("scripts.yaml uses !include directives. Phoenix MCP cannot safely edit it without destroying the include structure."), "denied", "delete_script"
                scripts = await hass.async_add_executor_job(_read_scripts_yaml, path)
                if script_id not in scripts:
                    return _tool_error(f"No script found with id '{script_id}'."), "denied", "delete_script"
                before_cfg = scripts[script_id]
                check_request_authority()
                stale = _delete_cas_conflict(args, before_cfg, "delete_script")
                if stale is not None:
                    return stale
                del scripts[script_id]
                await hass.async_add_executor_job(_write_scripts_yaml, path, scripts)
            elif not op.ok:
                outcome = "invalid_request" if op.error_kind == "conflict" else "denied"
                return _tool_error(op.error_text), outcome, "delete_script"
            else:
                before_cfg = op.before
        await hass.services.async_call("script", "reload", blocking=True)
        check_request_authority()
        _remove_registry_entry(hass, "script", script_id)
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("delete_script failed")
        return _tool_error("Failed to delete script. Check HA logs for details."), "invalid_request", "delete_script"

    await _record_version(
        data, access_key, resource_type="script", resource_id=script_id,
        action="delete", before=before_cfg, after=None,
        alias=before_cfg.get("alias") if isinstance(before_cfg, dict) else None,
    )
    return _tool_success(f"Script '{script_id}' deleted successfully."), "allowed", "delete_script"


# HA-COUPLING POINT. From HA 2026.7 hass.data[DATA_TRACE][key] is a TraceBuckets
# holding two separately size-limited dicts: `runs` (the automation actually
# fired) and `not_triggered` (a trigger evaluated a relevant change and did NOT
# fire), so the latter can never evict the former. Before that it was a plain
# LimitedSizeDict of run_id to trace. Both accessors below read either shape.
# Re-verify on HA upgrades.
#
# Not-triggered traces are deliberately INCLUDED: "why did it not run" is the
# usual reason to read traces at all, and each trace self-reports via a
# not_triggered field in as_short_dict(), so the caller can tell them apart.


_TRACE_ENTITY_ID_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")


def _traces_for_automation(bucket: Any) -> list[Any]:
    """Every trace object stored for one automation, newest-shape or legacy."""
    all_traces = getattr(bucket, "all_traces", None)
    if callable(all_traces):
        return list(all_traces())
    return list(bucket.values())


def _trace_by_run_id(bucket: Any, run_id: str) -> Any | None:
    """One trace by run id, newest-shape or legacy. None when absent."""
    getter = getattr(bucket, "get", None)
    if callable(getter):  # legacy LimitedSizeDict, or the empty-dict default
        return getter(run_id)
    return bucket.runs.get(run_id) or bucket.not_triggered.get(run_id)


def _trace_summary(short: dict) -> dict:
    """Condense a trace short-dict to the fields that explain a run's outcome."""
    return {
        "run_id": short.get("run_id"),
        "state": short.get("state"),
        "script_execution": short.get("script_execution"),
        "last_step": short.get("last_step"),
        "error": short.get("error"),
        "timestamp": short.get("timestamp"),
    }


def _scope_trace(value: Any, access_key: AccessKeyRecord, hass: HomeAssistant, depth: int = 0) -> Any:
    """Remove what a readable run recorded about entities this access key cannot read.

    A trace holds live data, not just configuration: trigger variables carry
    whole State objects, results echo service data, and errors and templates are
    free text. A State, or a dict shaped like one, for an inaccessible entity is
    replaced whole, since its attributes would still identify it; inaccessible
    entity IDs elsewhere are redacted in place, entries keyed by them are
    dropped, and sensitive keys keep no value.
    """
    from homeassistant.core import Context, State  # noqa: PLC0415

    from ..helpers import redact_inaccessible_entity_ids_in_text  # noqa: PLC0415
    from ..policy_engine import is_sensitive_key, scrub_sensitive_attributes  # noqa: PLC0415

    def readable(entity_id: str) -> bool:
        return resolve(entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)

    if depth > TRACE_SCOPE_MAX_DEPTH:
        return "<redacted>"
    if isinstance(value, State):
        value = scrub_sensitive_attributes(value)
    elif isinstance(value, Context):
        value = value.as_dict()
    if isinstance(value, dict):
        entity_id = value.get("entity_id")
        if (isinstance(entity_id, str) and ("state" in value or "attributes" in value)
                and not readable(entity_id)):
            return "<redacted>"
        scoped: dict = {}
        for key, item in value.items():
            if isinstance(key, str) and _TRACE_ENTITY_ID_RE.fullmatch(key) and not readable(key):
                continue
            scoped[key] = "<redacted>" if is_sensitive_key(key) else _scope_trace(item, access_key, hass, depth + 1)
        return scoped
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_scope_trace(item, access_key, hass, depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else str(value)
    if _TRACE_ENTITY_ID_RE.fullmatch(text):
        return text if readable(text) else "<redacted>"
    return redact_inaccessible_entity_ids_in_text(text, access_key, hass)


def _resolve_trace_target(raw: str, hass: HomeAssistant) -> tuple[str | None, str | None]:
    """Map an automation/script entity id, or an automation id, to (entity_id, trace key)."""
    registry = er.async_get(hass)
    if raw.startswith(("automation.", "script.")):
        entry = registry.async_get(raw)
        if entry is None or entry.unique_id is None:
            return None, None
        return entry.entity_id, f"{entry.domain}.{entry.unique_id}"
    for e in registry.entities.values():
        if e.domain == "automation" and e.unique_id == raw:
            return e.entity_id, f"automation.{raw}"
    return None, None


def _empty_trace_diagnosis(hass: HomeAssistant, entity_id: str, trace_key: str) -> str:
    """Explain an empty trace list from facts the access key may already read."""
    state = hass.states.get(entity_id)
    if entity_id.startswith("automation.") and state is not None and state.state == "off":
        return "The automation is disabled, so it has not run since it was turned off."
    if state is not None and state.attributes.get("last_triggered") is None:
        kind = "automation" if entity_id.startswith("automation.") else "script"
        return f"The {kind} has never run. Wait for it to run, or run it once, to record a trace."
    return ("It has run before, but no trace of those runs is retained. Home Assistant keeps only "
            "the most recent runs, as many as the stored_traces trace option allows (default 5).")


async def _tool_get_automation_traces(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: execution traces for an accessible automation or script."""
    if effective_cap(access_key, "cap_traces") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "get_automation_traces"

    raw = str(args.get("automation_id") or args.get("entity_id") or "").strip()
    if not raw:
        return _tool_error("Missing required argument: automation_id"), "invalid_request", "get_automation_traces"

    entity_id, trace_key = _resolve_trace_target(raw, hass)
    # Scope: nonexistent and inaccessible look identical (no oracle).
    if entity_id is None or trace_key is None or resolve(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
        return _tool_error("Automation or script not found."), "not_found", raw

    from homeassistant.components.trace.const import DATA_TRACE  # noqa: PLC0415
    # An absent DATA_TRACE key means the trace component is not loaded, which would
    # otherwise look like an automation that never ran; it is reported as a warning.
    trace_loaded = DATA_TRACE in hass.data
    runs: Any = hass.data.get(DATA_TRACE, {}).get(trace_key, {})
    summary = bool(args.get("summary"))
    run_id = args.get("run_id")

    if run_id:
        trace = _trace_by_run_id(runs, run_id)
        if trace is None:
            return _tool_error("Trace run not found."), "not_found", raw
        run_body = _trace_summary(trace.as_short_dict()) if summary else trace.as_dict()
        return _tool_success(json.dumps(_scope_trace(run_body, access_key, hass), default=str)), "allowed", entity_id

    items = sorted(
        (t.as_short_dict() for t in _traces_for_automation(runs)),
        key=lambda d: d.get("timestamp", {}).get("start") or "",
        reverse=True,
    )
    if summary:
        items = [_trace_summary(d) for d in items]
    domain, item_id = trace_key.split(".", 1)
    body: dict[str, Any] = {
        "domain": domain, f"{domain}_id": item_id, "entity_id": entity_id,
        "count": len(items), "traces": items,
    }
    if not trace_loaded:
        body["warnings"] = ["The trace component is not loaded; trace history is unavailable."]
    elif not items:
        body["diagnosis"] = _empty_trace_diagnosis(hass, entity_id, trace_key)
    return _tool_success(json.dumps(_scope_trace(body, access_key, hass), default=str)), "allowed", entity_id


# ---------------------------------------------------------------------------
# Scene CRUD (cap_scene_write) - mirrors the automation/script YAML pattern
# ---------------------------------------------------------------------------


def _scene_member_entities(config: Any) -> list[str]:
    ents = config.get("entities") if isinstance(config, dict) else None
    return list(ents.keys()) if isinstance(ents, dict) else []


def _unwritable_scene_members(config: Any, access_key: AccessKeyRecord, hass: HomeAssistant) -> list[str]:
    """Scene member entities the access key cannot WRITE (the scene will actuate them)."""
    return sorted(e for e in _scene_member_entities(config) if resolve(e, access_key, hass) != Permission.WRITE)


def _valid_scene_config(config: Any) -> bool:
    return (
        isinstance(config, dict)
        and isinstance(config.get("name"), str) and config["name"].strip() != ""
        and isinstance(config.get("entities"), dict) and len(config["entities"]) > 0
    )


async def _tool_list_scenes(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: list accessible scene.* entities with their editable scene id."""
    if effective_cap(access_key, "cap_registry_read") == CAP_DENY:
        return _tool_error("Forbidden."), "denied", "list_scenes"
    scenes: list[dict] = []
    for e in filter_entities_for_access_key(hass.states.async_all(), access_key, hass):
        if not e["entity_id"].startswith("scene."):
            continue
        attrs = e.get("attributes", {})
        scenes.append({
            "entity_id": e["entity_id"],
            "name": attrs.get("friendly_name"),
            "scene_id": attrs.get("id"),
        })
    scenes.sort(key=lambda s: s["entity_id"])
    return _tool_success(json.dumps({"count": len(scenes), "scenes": scenes}, default=str)), "allowed", "list_scenes"


async def _tool_get_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant
) -> tuple[dict, str, str]:
    """MCP tool: read one scene's current stored config, with a content_hash.

    Applies edit_scene's member rule: a scene controlling entities this access key
    cannot write is refused with the same message as a missing scene, so the
    scene id is not an existence oracle.
    """
    if effective_cap(access_key, "cap_scene_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_scene"
    scene_id = str(args.get("scene_id") or "").strip()
    if not scene_id:
        return _tool_error("scene_id is required."), "invalid_request", "get_scene"
    config = await _read_config_entry(hass, "scene", scene_id)
    if config is None or _unwritable_scene_members(config, access_key, hass):
        return _tool_error(
            f"No scene found with id '{scene_id}', or it controls entities outside your write scope."
        ), "denied", f"scene:{scene_id}"
    return _config_read_response("scene_id", scene_id, config, f"scene:{scene_id}")


async def _scene_write(
    config: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData, *, tool_name: str, scene_id: str, replace: bool
) -> tuple[dict, str, str]:
    """Shared create/edit body: validate, scope-check members, write scenes.yaml, reload."""
    if not _valid_scene_config(config):
        return _tool_error("config must include a non-empty 'name' and a non-empty 'entities' map."), "invalid_request", tool_name
    bad = _unwritable_scene_members(config, access_key, hass)
    if bad:
        return _tool_error("Scene references entities this access key cannot write: " + ", ".join(bad)), "denied", tool_name

    config = {k: v for k, v in config.items() if k != "id"}
    config["id"] = scene_id
    path = hass.config.path(_SCENE_CONFIG_PATH)
    lock = _get_scene_lock(hass)
    try:
        async with lock:
            check_request_authority()
            layout = await hass.async_add_executor_job(
                yaml_includes.resolve_domain_layout, hass.config.config_dir, "scene",
            )
            if layout is None:
                # configuration.yaml missing or unparseable: legacy hardcoded-file path.
                if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                    return _tool_error("scenes.yaml uses !include directives. Phoenix MCP cannot safely edit it without destroying the include structure."), "denied", tool_name
                items = await hass.async_add_executor_job(_read_scenes_yaml, path)
                if replace:
                    idx = next((i for i, s in enumerate(items) if isinstance(s, dict) and str(s.get("id")) == scene_id), None)
                    # The access key must already own the scene it is replacing: it can only
                    # edit a scene whose CURRENT members are all WRITE-accessible. A
                    # missing scene and an out-of-scope one return the same error so the
                    # id is not an existence oracle.
                    if idx is None or _unwritable_scene_members(items[idx], access_key, hass):
                        return _tool_error(f"No scene found with id '{scene_id}', or it controls entities outside your write scope."), "denied", tool_name
                    before_cfg = items[idx]
                    items[idx] = config
                else:
                    before_cfg = None
                    items.append(config)
                await hass.async_add_executor_job(_write_scenes_yaml, path, items)
            elif layout.error:
                return _tool_error(layout.error), "denied", tool_name
            elif replace:
                # Routed edit: same oracle rule as the legacy path, checked on the
                # scene's CURRENT members before anything is written.
                current = await hass.async_add_executor_job(
                    yaml_includes.read_entry, hass.config.config_dir, "scene", scene_id,
                )
                if current is None or _unwritable_scene_members(current, access_key, hass):
                    return _tool_error(f"No scene found with id '{scene_id}', or it controls entities outside your write scope."), "denied", tool_name
                op = await hass.async_add_executor_job(
                    yaml_includes.perform_edit, hass.config.config_dir, "scene",
                    scene_id, config,
                )
                if not op.ok:
                    return _tool_error(op.error_text), "denied", tool_name
                before_cfg = op.before
            else:
                before_cfg = None
                op = await hass.async_add_executor_job(
                    yaml_includes.perform_create, hass.config.config_dir, "scene",
                    scene_id, config,
                )
                if not op.ok:
                    return _tool_error(op.error_text), "denied", tool_name
            await _record_version(
                data, access_key, resource_type="scene", resource_id=scene_id,
                action="edit" if replace else "create",
                before=before_cfg, after=config, alias=config.get("name"),
            )
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("%s failed", tool_name)
        return _tool_error(f"Failed to {tool_name.replace('_', ' ')}. Check HA logs for details."), "invalid_request", tool_name
    return await _finish_entity_write(
        hass, access_key, domain="scene", storage_id=scene_id, config=config, tool_name=tool_name,
    )


def _scene_write_precheck(args: dict, tool_name: str, *, require_id: bool) -> tuple[dict, str, str] | None:
    """Pre-gate validation for scene writes; None means OK to proceed.

    Checks scene_id presence and config shape so a doomed request is rejected
    before a pending approval is created. The executor re-validates at apply
    time.
    """
    if require_id and not str(args.get("scene_id") or "").strip():
        return _tool_error("scene_id is required."), "invalid_request", tool_name
    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", tool_name
    if not _valid_scene_config(config):
        return _tool_error("config must include a non-empty 'name' and a non-empty 'entities' map."), "invalid_request", tool_name
    return None


async def _tool_create_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: create a scene (Confirm-gated)."""
    if effective_cap(access_key, "cap_scene_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "create_scene"
    pre = _scene_write_precheck(args, "create_scene", require_id=False)
    if pre is not None:
        return pre
    refused = await _editor_file_refusal(hass, "scene", "", "create_scene", create=True)
    if refused is not None:
        return refused
    blocked = await _gate(
        "cap_scene_write", access_key, hass, data,
        tool_name="create_scene", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_create_scene(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_create_scene(args, access_key, hass, data)


async def _execute_create_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    config = args.get("config")
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "create_scene"
    # Restore of a deleted scene recreates it under its original id; a fresh
    # create mints a new one.
    scene_id = str(args.get("scene_id") or "").strip() or "phoenix_mcp_" + uuid.uuid4().hex[:16]
    return await _scene_write(
        config, access_key, hass, data, tool_name="create_scene",
        scene_id=scene_id, replace=False,
    )


async def _tool_edit_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: edit a scene (Confirm-gated)."""
    if effective_cap(access_key, "cap_scene_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "edit_scene"
    pre = _scene_write_precheck(args, "edit_scene", require_id=True)
    if pre is not None:
        return pre
    scene_id = str(args.get("scene_id") or "").strip()
    current = await _read_config_entry(hass, "scene", scene_id)
    # Only a scene this key may already edit is located, so the refusal is no
    # existence oracle; anything else meets the executor's not-found answer.
    if current is not None and not _unwritable_scene_members(current, access_key, hass):
        refused = await _editor_file_refusal(hass, "scene", scene_id, "edit_scene", create=False)
        if refused is not None:
            return refused
    stale = await _entry_cas_conflict(
        args, hass, "scene", str(args.get("scene_id") or "").strip(), "edit_scene",
    )
    if stale is not None:
        return stale
    blocked = await _gate(
        "cap_scene_write", access_key, hass, data,
        tool_name="edit_scene", args=args, request_id=request_id,
        client_ip=client_ip, diff=lambda: _build_diff_edit_scene(args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_edit_scene(args, access_key, hass, data)


async def _execute_edit_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    scene_id = str(args.get("scene_id") or "").strip()
    config = args.get("config")
    if not scene_id:
        return _tool_error("scene_id is required."), "invalid_request", "edit_scene"
    if not isinstance(config, dict):
        return _tool_error("config must be an object."), "invalid_request", "edit_scene"
    # Re-checked here (not just pre-gate) so a change made during the approval
    # window is caught before the write lands.
    stale = await _entry_cas_conflict(args, hass, "scene", scene_id, "edit_scene")
    if stale is not None:
        return stale
    return await _scene_write(config, access_key, hass, data, tool_name="edit_scene", scene_id=scene_id, replace=True)


async def _tool_delete_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: delete a scene (Confirm-gated)."""
    if effective_cap(access_key, "cap_scene_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_scene"
    scene_id = str_arg(args.get("scene_id")).strip()
    if not scene_id:
        return _tool_error("scene_id is required."), "invalid_request", "delete_scene"
    args, current = await _bind_delete_args(args, hass, "scene", scene_id)
    if current is None or _unwritable_scene_members(current, access_key, hass):
        return _tool_error(f"No scene found with id '{scene_id}', or it controls entities outside your write scope."), "denied", "delete_scene"
    refused = await _editor_file_refusal(hass, "scene", scene_id, "delete_scene", create=False)
    if refused is not None:
        return refused
    blocked = await _gate(
        "cap_scene_write", access_key, hass, data,
        tool_name="delete_scene", args=args, request_id=request_id,
        client_ip=client_ip,
        diff=lambda: _build_diff_delete_scene(args, access_key, hass, reviewed=current),
    )
    if blocked is not None:
        return blocked
    return await _execute_delete_scene(args, access_key, hass, data)


async def _execute_delete_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    scene_id = str(args.get("scene_id") or "").strip()
    if not scene_id:
        return _tool_error("scene_id is required."), "invalid_request", "delete_scene"
    path = hass.config.path(_SCENE_CONFIG_PATH)
    lock = _get_scene_lock(hass)
    try:
        async with lock:
            check_request_authority()
            current = await _read_config_entry(hass, "scene", scene_id)
            check_request_authority()
            stale = _delete_cas_conflict(args, current, "delete_scene")
            if stale is not None:
                return stale
            layout = await hass.async_add_executor_job(
                yaml_includes.resolve_domain_layout, hass.config.config_dir, "scene",
            )
            if layout is None:
                if await hass.async_add_executor_job(_yaml_file_has_includes, path):
                    return _tool_error("scenes.yaml uses !include directives. Phoenix MCP cannot safely edit it without destroying the include structure."), "denied", "delete_scene"
                items = await hass.async_add_executor_job(_read_scenes_yaml, path)
                existing = next((s for s in items if isinstance(s, dict) and str(s.get("id")) == scene_id), None)
                # The access key may only delete a scene whose current members are all
                # WRITE-accessible. Missing and out-of-scope return the same error.
                if existing is None or _unwritable_scene_members(existing, access_key, hass):
                    return _tool_error(f"No scene found with id '{scene_id}', or it controls entities outside your write scope."), "denied", "delete_scene"
                stale = _delete_cas_conflict(args, existing, "delete_scene")
                if stale is not None:
                    return stale
                check_request_authority()
                filtered = [s for s in items if not (isinstance(s, dict) and str(s.get("id")) == scene_id)]
                await hass.async_add_executor_job(_write_scenes_yaml, path, filtered)
            elif layout.error:
                return _tool_error(layout.error), "denied", "delete_scene"
            else:
                existing = await hass.async_add_executor_job(
                    yaml_includes.read_entry, hass.config.config_dir, "scene", scene_id,
                )
                if existing is None or _unwritable_scene_members(existing, access_key, hass):
                    return _tool_error(f"No scene found with id '{scene_id}', or it controls entities outside your write scope."), "denied", "delete_scene"
                check_request_authority()
                op = await hass.async_add_executor_job(
                    yaml_includes.perform_delete, hass.config.config_dir, "scene",
                    scene_id, args[_DELETE_EXPECTED_HASH],
                )
                if not op.ok:
                    outcome = "invalid_request" if op.error_kind == "conflict" else "denied"
                    return _tool_error(op.error_text), outcome, "delete_scene"
                existing = op.before
        await hass.services.async_call("scene", "reload", blocking=True)
        # Reloading scenes.yaml does not remove the scene's entity-registry entry.
        check_request_authority()
        registry = er.async_get(hass)
        for entry in list(registry.entities.values()):
            if entry.domain == "scene" and entry.unique_id == scene_id:
                registry.async_remove(entry.entity_id)
                break
    except AuthorityChangedError:
        raise
    except Exception:
        _LOGGER.exception("delete_scene failed")
        return _tool_error("Failed to delete scene. Check HA logs for details."), "invalid_request", "delete_scene"
    await _record_version(
        data, access_key, resource_type="scene", resource_id=scene_id,
        action="delete", before=existing, after=None,
        alias=existing.get("name") if isinstance(existing, dict) else None,
    )
    return _tool_success(f"Scene '{scene_id}' deleted successfully."), "allowed", "delete_scene"


def _build_diff_create_automation(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    config = dict_arg(args.get("config"))
    return {
        "kind": "config_diff",
        **_summary("create_automation", alias=config.get("alias", "<no alias>")),
        "target": {"type": "automation", "id": args.get("automation_id"), "label": config.get("alias")},
        "before": None,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode"),
                    "target_semantics": "future_native_execution",
                    "target_membership": "may_change", "runtime_authority": "Home Assistant"},
    }


def _authoring_entity_id(hass: HomeAssistant, domain: str, entry_id: str) -> str | None:
    """The entity a stored automation, script or scene id currently owns."""
    if entity_id := er.async_get(hass).async_get_entity_id(domain, domain, entry_id):
        return entity_id
    for state in hass.states.async_all(domain):
        if str(state.attributes.get("id")) == entry_id or state.entity_id == f"{domain}.{entry_id}":
            return state.entity_id
    return None


def _with_stored_id(hass: HomeAssistant, args: dict, domain: str) -> dict:
    """Accept an automation or script entity_id where its stored id is expected.

    Resolved through the registry before any approval, so approvals and
    executors only ever see the stored id. Anything else passes through.
    """
    key = f"{domain}_id"
    raw = args.get(key)
    if isinstance(raw, str) and raw.strip().startswith(f"{domain}."):
        entry = er.async_get(hass).async_get(raw.strip())
        if entry is not None and entry.domain == domain and entry.platform == domain and entry.unique_id:
            return {**args, key: entry.unique_id}
    return args


def _authoring_entry_label(
    hass: HomeAssistant, domain: str, entry_id: str
) -> str | None:
    """Resolve a live friendly name for a stored authoring resource."""
    direct = hass.states.get(f"{domain}.{entry_id}")
    states = [direct] if direct is not None else hass.states.async_all(domain)
    for state in states:
        if state is None:
            continue
        stored_id = state.attributes.get("id")
        if state.entity_id != f"{domain}.{entry_id}" and str(stored_id) != entry_id:
            continue
        label = state.attributes.get("friendly_name")
        if isinstance(label, str) and label.strip():
            return label.strip()
    return None


async def _build_diff_edit_automation(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    automation_id = (args.get("automation_id") or "").strip()
    config = dict_arg(args.get("config"))
    before = await _entry_before_json(hass, "automation", automation_id)
    return {
        "kind": "yaml_diff",
        **_summary("edit_automation", automation_id=automation_id),
        "target": {"type": "automation", "id": automation_id, "label": config.get("alias")},
        "before": before,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode"),
                    "target_semantics": "future_native_execution",
                    "target_membership": "may_change", "runtime_authority": "Home Assistant"},
    }


async def _authoring_consumers(hass: HomeAssistant, domain: str, item_id: str) -> dict[str, Any]:
    """What still uses the entity a delete removes, for the approval card."""
    from .discovery import _registry_relationship_preview  # noqa: PLC0415

    entity_id = _authoring_entity_id(hass, domain, item_id)
    if entity_id is None:
        return {"consumers": [], "consumer_count": 0, "searched": []}
    return await _registry_relationship_preview(hass, entity_id)


async def _build_diff_delete_automation(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, *, reviewed: Any = _ENTRY_NOT_CAPTURED
) -> dict:
    automation_id = str_arg(args.get("automation_id")).strip()
    before = await _entry_before_json(
        hass, "automation", automation_id, current=reviewed
    )
    label = _authoring_entry_label(hass, "automation", automation_id) or automation_id
    return {
        "kind": "system_action",
        **_summary("delete_automation", automation_id=automation_id),
        "target": {"type": "automation", "id": automation_id, "label": label},
        "before": before,
        "preview": {"warning": "This automation will be removed permanently.",
                    "relationships": await _authoring_consumers(hass, "automation", automation_id)},
    }


def _yaml_entry(hass: HomeAssistant, domain: str, entry_id: str) -> dict | None:
    """Read one automation/script/scene entry's stored config, or None.

    Fail-quiet on purpose: this only feeds a diff's `before` side, and an
    unreadable entry must not stop the approval for the requested write. The
    executor re-reads and re-validates at apply time.
    """
    try:
        return yaml_includes.read_entry(hass.config.config_dir, domain, entry_id)
    except Exception:  # noqa: BLE001 - diagnostic only
        return None


async def _entry_before_json(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    *,
    wrap_key: str | None = None,
    current: Any = _ENTRY_NOT_CAPTURED,
) -> str | None:
    """Truncated JSON of an entry's current config for a diff's `before`, or None.

    wrap_key reproduces the shape each domain's `after` side uses, so both panes
    stay comparable: scripts render as {script_id: config}, automations and scenes
    bare. A passed `current` (the reviewed entry, for deletes) skips the read.

    Fail-quiet end to end, including the executor dispatch and serialization: a
    diff is advisory and nothing here may stop the write it describes from being
    gated.
    """
    try:
        if current is _ENTRY_NOT_CAPTURED:
            current = await hass.async_add_executor_job(
                _yaml_entry, hass, domain, entry_id
            )
        if current is None:
            return None
        payload: Any = {wrap_key: current} if wrap_key is not None else current
        return _truncate(json.dumps(payload, indent=2, default=str))
    except Exception:  # noqa: BLE001 - diagnostic only
        return None


def _build_diff_create_scene(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    config = dict_arg(args.get("config"))
    members = _scene_member_entities(config)
    return {
        "kind": "config_diff",
        **_summary("create_scene", name=config.get("name", "<no name>")),
        "target": {"type": "scene", "id": None, "label": config.get("name")},
        "before": None,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"name": config.get("name"), "entities": members,
                    "unwritable_entities": _unwritable_scene_members(config, access_key, hass)},
    }


async def _build_diff_edit_scene(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    scene_id = str(args.get("scene_id") or "").strip()
    config = dict_arg(args.get("config"))
    before = await _entry_before_json(hass, "scene", scene_id)
    return {
        "kind": "yaml_diff",
        **_summary("edit_scene", scene_id=scene_id),
        "target": {"type": "scene", "id": scene_id, "label": config.get("name")},
        "before": before,
        "after": _truncate(json.dumps(config, indent=2, default=str)),
        "preview": {"name": config.get("name"), "entities": _scene_member_entities(config),
                    "unwritable_entities": _unwritable_scene_members(config, access_key, hass)},
    }


async def _build_diff_delete_scene(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, *, reviewed: Any = _ENTRY_NOT_CAPTURED
) -> dict:
    scene_id = str(args.get("scene_id") or "").strip()
    before = await _entry_before_json(hass, "scene", scene_id, current=reviewed)
    label = _authoring_entry_label(hass, "scene", scene_id) or scene_id
    return {
        "kind": "system_action",
        **_summary("delete_scene", scene_id=scene_id),
        "target": {"type": "scene", "id": scene_id, "label": label},
        "before": before,
        "preview": {"warning": "This scene will be removed permanently.",
                    "relationships": await _authoring_consumers(hass, "scene", scene_id)},
    }


def _build_diff_create_script(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    script_id = (args.get("script_id") or "").strip()
    config = dict_arg(args.get("config"))
    return {
        "kind": "config_diff",
        **_summary("create_script", script_id=script_id),
        "target": {"type": "script", "id": script_id, "label": config.get("alias")},
        "before": None,
        "after": _truncate(json.dumps({script_id: config}, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode"),
                    "target_semantics": "future_native_execution",
                    "target_membership": "may_change", "runtime_authority": "Home Assistant"},
    }


async def _build_diff_edit_script(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    script_id = (args.get("script_id") or "").strip()
    config = dict_arg(args.get("config"))
    before = await _entry_before_json(hass, "script", script_id, wrap_key=script_id)
    return {
        "kind": "yaml_diff",
        **_summary("edit_script", script_id=script_id),
        "target": {"type": "script", "id": script_id, "label": config.get("alias")},
        "before": before,
        "after": _truncate(json.dumps({script_id: config}, indent=2, default=str)),
        "preview": {"alias": config.get("alias"), "mode": config.get("mode"),
                    "target_semantics": "future_native_execution",
                    "target_membership": "may_change", "runtime_authority": "Home Assistant"},
    }


async def _build_diff_delete_script(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, *, reviewed: Any = _ENTRY_NOT_CAPTURED
) -> dict:
    script_id = str_arg(args.get("script_id")).strip()
    before = await _entry_before_json(
        hass, "script", script_id, wrap_key=script_id, current=reviewed
    )
    label = _authoring_entry_label(hass, "script", script_id) or script_id
    return {
        "kind": "system_action",
        **_summary("delete_script", script_id=script_id),
        "target": {"type": "script", "id": script_id, "label": label},
        "before": before,
        "preview": {"warning": "This script will be removed permanently.",
                    "relationships": await _authoring_consumers(hass, "script", script_id)},
    }
