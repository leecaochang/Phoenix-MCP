"""Service calls, with their capability, entity, MESA and target checks."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError, ServiceNotFound, ServiceValidationError, Unauthorized,
)

from ..access_key_store import AccessKeyRecord
from ..approval_targets import target_binding, TARGETS_CHANGED, targets_current
from ..audit import Outcome
from ..const import (
    CAP_CONFIRM, CAP_DENY, DUAL_GATE_SERVICES, HIGH_RISK_DOMAINS, MUSIC_ASSISTANT_LIBRARY_SERVICES,
    NO_TARGET_SERVICES, PRIMARY_ENTITY_DOMAINS, PROXY_TIMEOUT_SECONDS, SERVICE_FAILURE_TEXT_MAX,
    SERVICE_RESULT_STATES_MAX, APPROVAL_TARGETS_KEY,
    TTS_SPEAK_PLAYER_FIELD, TTS_SPEAK_SERVICE, UPDATE_INSTALL_SERVICE,
)
from ..data import PhoenixData
from ..helpers import (
    service_not_found_hint as _service_not_found_hint, validation_error_message as _validation_error_message,
)
from ..tool_input import sanitize_service_data as _sanitize_service_data, str_arg
from ..catalog_text import diff_summary_fields as _summary
from ..capability import effective_cap
from ..household_authority import household_service_kwargs
from ..mesa import async_apply_mesa_to_call, fire_mesa_blocked_event
from ..policy_engine import (
    call_needs_physical_gate, esphome_entry_writable, filter_service_response, group_member_ids,
    music_assistant_readable_entries, Permission, required_service_cap, resolve,
    resolve_esphome_user_service, resolve_service_targets, service_target_binding_valid,
    unreadable_group_targets, unregistered_targets,
)
from ..request_authority import check_request_authority, mark_dispatch_started, RequestAuthority
from ..routing.plan_currency import service_plan_is_current, bind_service_plan
from ..routing.command_verification import async_verify_command_state
from ..routing.routing_tables import COMMAND_ROUTING_PLAN_KEY
from ..service_targets import (
    call_reach, CallReach, dedicated_tool_refusal, entity_id_argument, gate_entity_ids, merge_speech_players,
    named_script_refusal, script_input_selector_error, script_input_selectors, secondary_target_error,
    speech_engines, speech_players, speech_target_refusal, template_fields, template_fields_error,
    todo_removal_review, unbound_service_targets, unbound_target_error, unreadable_group_error,
    unregistered_target_error,
    unsupported_secondary_targets, visible_reach_warnings,
)
from ..tool_common import (
    _add_mesa_advisory, _CAP_FORBIDDEN_MESSAGE, _gate, _mesa_confirm_annotation, _pending_or_inline, _tool_error, _tool_refused_before_write, _tool_reported_failure, _tool_success, MESA_DENIED_MESSAGE,
    redaction_sentinel_path,
)
from ..redaction import redact_diagnostics, redact_inaccessible_entity_ids_in_text
from ..request_authority import _approved_exec_ctx
from ..tool_contracts import normalize_tool_args, service_name_error
from .esphome import _ESPHOME_DOMAIN
from .system import _execute_restart_ha


_LOGGER = logging.getLogger(__name__)


TODO_PLACEHOLDER_REFUSAL = "Redacted item text cannot be written back. Send the whole new text, or leave the field out to keep it."


async def _tool_call_service(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
    *,
    approve_as: tuple[str, dict] | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: call a HA service with entity targets filtered to WRITE-permitted entities.

    ``approve_as`` names the (tool, saved arguments) a bounded tool's MESA approval runs as.
    """
    args, error = normalize_tool_args("call_service", args)
    if error:
        # Diagnostics must not override a capability denial. Inspect only the
        # recognized service/selector shape; never repair an unknown selector.
        service_arg = args.get("service")
        domain = str_arg(service_arg.get("domain")) if isinstance(service_arg, dict) else str_arg(args.get("domain"))
        service = str_arg(service_arg.get("name")) if isinstance(service_arg, dict) else str_arg(service_arg)
        dedicated = dedicated_tool_refusal(domain, service)
        cap = dedicated[0] if dedicated is not None else _service_required_cap(args, domain, service, access_key, hass)
        if cap is not None and effective_cap(access_key, cap) == CAP_DENY:
            return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "call_service"
        return _tool_error(error), "invalid_request", "call_service"
    # Coerced before anything gates on them: service_key and the physical-gate
    # domain check are set-membership tests, so a list-valued domain raises
    # instead of being refused.
    domain = str_arg(args.get("domain"))
    service = str_arg(args.get("service"))
    if not domain or not service:
        return _tool_error("Missing required arguments: domain and service"), "invalid_request", "call_service"

    try:
        household_service_kwargs(hass, domain, service)
    except Unauthorized:
        return _tool_error("Forbidden."), "denied", "call_service"

    if (dedicated := dedicated_tool_refusal(domain, service)) is not None:
        if dedicated[0] and effective_cap(access_key, dedicated[0]) == CAP_DENY:
            return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "call_service"
        return _tool_error(dedicated[1]), "invalid_request", "call_service"

    service_key = f"{domain}/{service}"
    args, speech_error = merge_speech_players(args, domain, service)
    if speech_error:
        return _tool_error(speech_error), "invalid_request", "call_service"
    secondary_targets = await unsupported_secondary_targets(
        hass, args.get("service_data"), domain=domain, service=service
    )
    if secondary_targets:
        return (
            _tool_error(secondary_target_error(secondary_targets)),
            "invalid_request",
            "call_service",
        )
    if templated := template_fields(args.get("service_data")):
        return _tool_error(template_fields_error(templated)), "invalid_request", "call_service"
    # Item text read through get_todo_items may hold placeholders; set_todo_item refuses them too.
    if domain == "todo" and service in ("add_item", "update_item") and redaction_sentinel_path(args.get("service_data")) is not None:
        return _tool_refused_before_write(TODO_PLACEHOLDER_REFUSAL), "invalid_request", "call_service"
    if refusal := await named_script_refusal(hass, domain, service):
        return _tool_refused_before_write(refusal), "invalid_request", "call_service"
    if selecting := await script_input_selectors(hass, domain, service, args.get("service_data"), args.get("entity_id")):
        return _tool_refused_before_write(script_input_selector_error(selecting)), "invalid_request", "call_service"


    targetless = (service_key in DUAL_GATE_SERVICES | NO_TARGET_SERVICES | MUSIC_ASSISTANT_LIBRARY_SERVICES
                  or domain == _ESPHOME_DOMAIN)
    if not targetless and not any((args.get("entity_id"), args.get("device_id"), args.get("area_id"))):
        if (call_needs_physical_gate(domain=domain, service=service, access_key=access_key, hass=hass)
                and effective_cap(access_key, "cap_physical_control") == CAP_DENY):
            return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "call_service"
        return (
            _tool_error("An explicit entity_id, device_id, or area_id target is required for an entity service. Targetless services outside the supported allowlist are unavailable through call_service."),
            "invalid_request", "call_service",
        )

    targets: list[str] = []
    reach = CallReach([])
    if not targetless:
        targets, _ = resolve_service_targets(
            entity_id=args.get("entity_id"), device_id=args.get("device_id"),
            area_id=args.get("area_id"), service_domain=domain, access_key=access_key, hass=hass,
        )
        if unbound := await unbound_service_targets(hass, targets, domain=domain, service=service):
            return _tool_refused_before_write(unbound_target_error(domain, service, unbound)), "denied", "call_service"
        if refusal := speech_target_refusal(domain, service, args.get("entity_id"), targets):
            return _tool_refused_before_write(refusal), "denied", "call_service"
        reach = call_reach(domain, service, args.get("service_data"), targets, access_key, hass)

    bound_args = bind_service_plan(args, hass, data, access_key)
    if bound_args is None:
        return _tool_error("Command targets or configuration changed."), "denied", "call_service"
    args = bound_args

    required_cap = _service_required_cap(args, domain, service, access_key, hass)
    denied = required_cap is not None and effective_cap(access_key, required_cap) == CAP_DENY
    if reach.refusal and not denied:
        return _tool_refused_before_write(reach.refusal), "denied", "call_service"
    if not targetless and not targets and not denied and (
            unregistered := unregistered_targets(args.get("entity_id"), access_key, hass)):
        return _tool_refused_before_write(unregistered_target_error(unregistered)), "denied", "call_service"
    if required_cap is not None:
        # An approval must bind everything it will reach, and a key holding no target
        # queues nothing. A denied capability refuses without resolving.
        bind_targets = not targetless and not denied
        if bind_targets and not targets:
            return _tool_error("Forbidden."), "denied", "call_service"
        # An update replaces running code, so a person reviews it whatever the mode.
        review = service_key == UPDATE_INSTALL_SERVICE
        # An approval that cannot verify its targets at execution could never run.
        if (bind_targets and (review or effective_cap(access_key, required_cap) == CAP_CONFIRM)
                and target_binding(reach.entities, hass)["fingerprint"] is None):
            groups = [target for target in targets if group_member_ids(target, hass)]
            refusal = unreadable_group_error(groups) if groups else "Forbidden."
            return _tool_refused_before_write(refusal), "denied", "call_service"
        blocked = await _gate(
            required_cap, access_key, hass, data,
            tool_name="call_service", args=args, request_id=request_id,
            client_ip=client_ip, diff=lambda: _build_diff_call_service(args, access_key, hass),
            target_entities=reach.entities if bind_targets else None, review=review,
        )
        if blocked is not None:
            return blocked
    return await _execute_call_service(
        args, access_key, hass, data, request_id=request_id, client_ip=client_ip, approve_as=approve_as,
    )


def _failure_text(err: HomeAssistantError, domain: str, service: str, access_key: AccessKeyRecord,
                  hass: HomeAssistant, *, detail: bool = True) -> str:
    """Name a failed call, with Core's reason scrubbed then bounded when `detail` is set."""
    reason = str(err).strip() if detail else ""
    if not reason:
        return f"Home Assistant could not complete {domain}.{service}."
    scrubbed = redact_diagnostics(redact_inaccessible_entity_ids_in_text(reason, access_key, hass))
    return f"Home Assistant could not complete {domain}.{service}: {scrubbed[:SERVICE_FAILURE_TEXT_MAX]}"


def _service_failure(err: HomeAssistantError, domain: str, service: str, access_key: AccessKeyRecord,
                     hass: HomeAssistant, *, detail: bool = True) -> dict:
    """The result of a dispatched call that failed, which may have applied in part."""
    text = _failure_text(err, domain, service, access_key, hass, detail=detail)
    return _tool_reported_failure(f"{text.rstrip('.')}. It may have applied in part, so check the result before repeating it.")


def _service_required_cap(
    args: dict, domain: str, service: str, access_key: AccessKeyRecord, hass: HomeAssistant,
) -> str | None:
    """The capability this call needs at admission and again at execution."""
    service_data = args.get("service_data")
    return required_service_cap(
        domain=domain, service=service, service_data=service_data,
        entity_id=gate_entity_ids(domain, service, service_data, args.get("entity_id"), hass),
        device_id=args.get("device_id"),
        area_id=args.get("area_id"), access_key=access_key, hass=hass,
    )


async def _dispatch_no_target_tool_call(
    hass: HomeAssistant,
    access_key: AccessKeyRecord,
    *,
    domain: str,
    service: str,
    service_data: dict,
    resource: str,
    timeout_noun: str,
    surface_validation_errors: bool,
) -> tuple[dict, Outcome, str]:
    """Call a service that takes no entity target, and map the outcome; it authorizes nothing.

    Covers dual-gate services, the config-reload family and ESPHome user-defined actions. The caller
    chooses whether validation detail is safe to surface.
    """
    # Those have no entities in hass.states, reject an entity_id, or take only device-declared
    # arguments respectively. For NO_TARGET_SERVICES, detail is safe: it describes the caller's own
    # reloadable config after the cap_yaml_edit gate.
    if domain in HIGH_RISK_DOMAINS:
        _LOGGER.info(
            "High-risk service call %s/%s by access key %s",
            domain, service, access_key.name,
        )
    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            service_context = household_service_kwargs(hass, domain, service)
            mark_dispatch_started()
            await hass.services.async_call(
                domain, service, service_data, blocking=True, return_response=False,
                **service_context,
            )
    except asyncio.TimeoutError:
        subject = "the device" if timeout_noun == "Action" else "HA"
        return (
            _tool_success(json.dumps({
                "success": True,
                "partial": True,
                "verified": None,
                "message": f"{timeout_noun} dispatched but {subject} did not respond within the timeout window.",
            })),
            "allowed",
            resource,
        )
    except ServiceNotFound:
        # Generic: never confirm or deny service existence.
        return _tool_error("Forbidden."), "denied", resource
    except (ServiceValidationError, vol.Invalid) as err:
        if not surface_validation_errors:
            return _tool_error("Forbidden."), "denied", resource
        return _tool_error(_validation_error_message(err)), "invalid_request", resource
    except Unauthorized:
        return _tool_error("Forbidden."), "denied", resource
    except HomeAssistantError as err:
        failure = _service_failure(err, domain, service, access_key, hass, detail=surface_validation_errors)
        return failure, "invalid_request", resource
    # Nothing to read back: such an action changes no entity's state.
    return _tool_success(json.dumps({
        "success": True, "verified": None,
        "note": "Home Assistant accepted the action; an action without a target has no state to read back.",
    })), "allowed", resource


async def _execute_music_assistant_library_read(
    args: dict,
    service_data: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    *,
    domain: str,
    service: str,
    resource: str,
) -> tuple[dict, Outcome, str]:
    """Run music_assistant.search or get_library, scoped by its config entry.

    Needs cap_service_response and Read on an entity of that entry; a bad entry is refused alike,
    revealing none. One readable entry may be left out. Output is service-response redacted.
    """
    # These name an entry instead of an entity target and only return a response. MESA does not
    # apply because nothing is actuated, and every refusal here is refused-before-write, so an agent
    # turn may correct the call instead of stopping at an unknown outcome.
    if effective_cap(access_key, "cap_service_response") == CAP_DENY:
        return _tool_refused_before_write(_CAP_FORBIDDEN_MESSAGE), "denied", resource
    if any((args.get("entity_id"), args.get("device_id"), args.get("area_id"))):
        return (
            _tool_refused_before_write(f"{domain}.{service} takes config_entry_id in service_data, not an entity_id, device_id or area_id target."),
            "invalid_request", resource,
        )
    readable = music_assistant_readable_entries(hass, access_key)
    entry_id = service_data.get("config_entry_id")
    if entry_id is None and len(readable) == 1:
        service_data = {**service_data, "config_entry_id": readable[0]}
    elif entry_id is None and readable:
        return _tool_refused_before_write("Several Music Assistant servers are readable; pass config_entry_id in service_data."), "invalid_request", resource
    elif entry_id not in readable:
        return _tool_refused_before_write("Forbidden."), "denied", resource
    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            service_context = household_service_kwargs(hass, domain, service)
            mark_dispatch_started()
            response = await hass.services.async_call(
                domain, service, service_data, blocking=True, return_response=True,
                **service_context,
            )
    except asyncio.TimeoutError:
        return _tool_refused_before_write("Music Assistant did not answer within the timeout window."), "invalid_request", resource
    except ServiceNotFound:
        return _tool_refused_before_write("Forbidden."), "denied", resource
    except (ServiceValidationError, vol.Invalid) as err:
        # Authorized by now; the message is about the caller's own arguments.
        return _tool_refused_before_write(_validation_error_message(err)), "invalid_request", resource
    except Unauthorized:
        return _tool_refused_before_write("Forbidden."), "denied", resource
    except HomeAssistantError as err:
        return _tool_refused_before_write(_failure_text(err, domain, service, access_key, hass)), "invalid_request", resource
    body = {"success": True, "service_response": filter_service_response(response, access_key, hass)}
    return _tool_success(json.dumps(body, default=str)), "allowed", resource


async def _execute_call_service(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    *,
    request_id: str = "",
    client_ip: str | None = None,
    mesa_approved: bool = False,
    approve_as: tuple[str, dict] | None = None,
) -> tuple[dict, str, str]:
    # Coerced before anything gates on them: service_key and the physical-gate
    # domain check are set-membership tests, so a list-valued domain raises
    # instead of being refused.
    domain = str_arg(args.get("domain"))
    service = str_arg(args.get("service"))
    if not domain or not service:
        return _tool_error("Missing required arguments: domain and service"), "invalid_request", "call_service"
    # Saved approval arguments reach this executor without the request contract.
    if name_error := service_name_error(domain, service):
        return _tool_error(name_error), "invalid_request", "call_service"

    if (dedicated := dedicated_tool_refusal(domain, service)) is not None:
        if dedicated[0] and effective_cap(access_key, dedicated[0]) == CAP_DENY:
            return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "call_service"
        return _tool_error(dedicated[1]), "invalid_request", "call_service"


    resource = f"service:{domain}/{service}"
    service_key = f"{domain}/{service}"

    required_cap = _service_required_cap(args, domain, service, access_key, hass)
    if required_cap is not None and effective_cap(access_key, required_cap) == CAP_DENY:
        return _tool_error("Forbidden."), "denied", resource

    # Re-run at the executor choke point. Confirm stores caller arguments and a
    # service can register or change its description while approval is pending,
    # so the request-time precheck alone is not an authorization boundary.
    secondary_targets = await unsupported_secondary_targets(
        hass, args.get("service_data"), domain=domain, service=service
    )
    if secondary_targets:
        return (
            _tool_error(secondary_target_error(secondary_targets)),
            "invalid_request",
            resource,
        )
    if templated := template_fields(args.get("service_data")):
        return _tool_error(template_fields_error(templated)), "invalid_request", resource
    if refusal := await named_script_refusal(hass, domain, service):
        return _tool_refused_before_write(refusal), "invalid_request", resource
    if selecting := await script_input_selectors(hass, domain, service, args.get("service_data"), args.get("entity_id")):
        return _tool_refused_before_write(script_input_selector_error(selecting)), "invalid_request", resource

    check_request_authority()
    entity_id = args.get("entity_id")
    device_id = args.get("device_id")
    area_id = args.get("area_id")
    # Selector-bearing service data has already refused at the shared boundary.
    service_data = _sanitize_service_data(args.get("service_data"))

    local_plan = COMMAND_ROUTING_PLAN_KEY in args
    if not service_plan_is_current(args, hass, data, access_key):
        return _tool_error("Command targets or configuration changed."), "denied", resource

    # DUAL_GATE_SERVICES have no entities in hass.states; routing them through
    # resolve_service_targets always produces an empty list and a spurious 403.
    # The cap_restart gate above is the only permission check required.
    if service_key == "homeassistant/restart" and not service_data:
        # One restart path, so every restart leaves the durable restart receipt.
        return await _execute_restart_ha({}, access_key, hass, data)
    if service_key in DUAL_GATE_SERVICES:
        return await _dispatch_no_target_tool_call(
            hass, access_key, domain=domain, service=service, service_data=service_data,
            resource=resource, timeout_noun="Service",
            surface_validation_errors=False,
        )

    # NO_TARGET_SERVICES are domain-wide config reloads that take no entity target.
    # The normal path below always attaches an explicit entity_id list, which
    # these services' schemas reject, so call them with bare service_data.
    # The cap_yaml_edit gate above is the only permission check required; any
    # entity/device/area the caller passed is ignored (these services have no
    # target semantics). MESA does not apply (no entities to govern).
    if service_key in NO_TARGET_SERVICES:
        # surface_validation_errors=True: post-authorization (cap_yaml_edit is
        # already granted) and the message describes the caller's own reloadable
        # config, not hidden state.
        return await _dispatch_no_target_tool_call(
            hass, access_key, domain=domain, service=service, service_data=service_data,
            resource=resource, timeout_noun="Service",
            surface_validation_errors=True,
        )

    if service_key in MUSIC_ASSISTANT_LIBRARY_SERVICES:
        return await _execute_music_assistant_library_read(
            args, service_data, access_key, hass, domain=domain, service=service, resource=resource,
        )

    # ESPHome user-defined actions (esphome.<device>_<action>) take NO entity
    # target: HA builds their schema from only the arguments the device declared,
    # so attaching the flattened entity_id list makes voluptuous reject the call
    # outright, and routing them through resolve_service_targets yields
    # an empty list and a spurious 403. Authorization is the owning DEVICE's write
    # scope: the action is defined by that device's own firmware and can only act on
    # it, so an access key that may already actuate the device's entities is exactly
    # the access key that may invoke its actions, with the capability checked above
    # when the device has a physical entity. MESA does not apply (no entity target to
    # govern). A service no LOADED entry
    # claims falls through to the normal path and refuses uniformly, so a stale
    # registration left behind by an unload reveals nothing.
    if domain == _ESPHOME_DOMAIN:
        esphome_entry = resolve_esphome_user_service(hass, service)
        if esphome_entry is not None:
            if not esphome_entry_writable(hass, esphome_entry, access_key):
                return _tool_error("Forbidden."), "denied", resource
            # A device-defined schema makes a wrong argument NAME the likeliest
            # failure, and the caller can fix it, so validation errors surface.
            return await _dispatch_no_target_tool_call(
                hass, access_key, domain=domain, service=service, service_data=service_data,
                resource=resource, timeout_noun="Action",
                surface_validation_errors=True,
            )

    if not any((entity_id, device_id, area_id)):
        if domain == "esphome":
            return _tool_error("Forbidden."), "denied", resource
        return (
            _tool_error("An explicit entity_id, device_id, or area_id target is required for an entity service. Targetless services outside the supported allowlist are unavailable through call_service."),
            "invalid_request", resource,
        )
    permitted_entities, _visible_count = resolve_service_targets(
        entity_id=entity_id,
        device_id=device_id,
        area_id=area_id,
        service_domain=domain,
        access_key=access_key,
        hass=hass,
    )

    if not permitted_entities and (unreadable := unreadable_group_targets(entity_id, access_key, hass)):
        return _tool_refused_before_write(unreadable_group_error(unreadable)), "denied", resource
    if not permitted_entities or not service_target_binding_valid(domain, service, permitted_entities, hass):
        return _tool_error("Forbidden."), "denied", resource

    # Re-run here for the same reason as the selector check: a service's
    # description can change while an approval is pending.
    if unbound := await unbound_service_targets(hass, permitted_entities, domain=domain, service=service):
        return _tool_refused_before_write(unbound_target_error(domain, service, unbound)), "denied", resource
    if refusal := speech_target_refusal(domain, service, entity_id, permitted_entities):
        return _tool_refused_before_write(refusal), "denied", resource
    reach = call_reach(domain, service, service_data, permitted_entities, access_key, hass)
    if reach.refusal:
        return _tool_refused_before_write(reach.refusal), "denied", resource

    if not service_plan_is_current(args, hass, data, access_key, permitted_entities):
        return _tool_error("Command targets or configuration changed."), "denied", resource

    if not targets_current(args, reach.entities, hass):
        return _tool_error(TARGETS_CHANGED), "denied", resource

    # MESA enforcement runs last, on the flattened entity list Phoenix MCP already
    # permitted (targets are always flattened to explicit entity_ids; device_id,
    # area_id and "all" never reach HA). MESA never sees entities Phoenix MCP
    # denied; it can drop entities, gate the whole call for confirmation, or
    # block outright.
    exact_approval = APPROVAL_TARGETS_KEY in args or _approved_exec_ctx.get()
    every_target = local_plan or exact_approval or service_key == TTS_SPEAK_SERVICE or reach.every_target
    local_constraints: dict[str, Any] = {"require_all": True} if every_target else {}
    if local_plan or reach.every_target:
        # The approval runs this call; what it reaches beyond its targets is not dispatched to.
        local_constraints["approval_args"] = args
    if reach.every_target:
        local_constraints["approval_diff"] = lambda: _build_diff_call_service(args, access_key, hass)
    if approve_as is not None:
        local_constraints.update(approval_tool_name=approve_as[0], approval_args=approve_as[1])
    mesa_outcome = await async_apply_mesa_to_call(
        hass, data, access_key,
        domain=domain, service=service, service_data=service_data,
        entities=reach.entities,
        request_id=request_id, client_ip=client_ip, session_id=request_id,
        confirm_approved=mesa_approved or _approved_exec_ctx.get(),
        **local_constraints,
    )
    if mesa_outcome.blocked:
        fire_mesa_blocked_event(hass, access_key, mesa_outcome.blocked)
    if mesa_outcome.decision == "pending":
        # Same holding behaviour as a capability confirm (tool_common._gate):
        # one definition, so the two kinds of gate cannot answer differently.
        return await _pending_or_inline(hass, data, access_key, mesa_outcome.approval)
    if mesa_outcome.decision == "deny":
        return _tool_error(MESA_DENIED_MESSAGE), "denied", resource
    check_request_authority()
    if not reach.every_target:
        permitted_entities = mesa_outcome.entities
    if not service_target_binding_valid(domain, service, permitted_entities, hass):
        return _tool_error("Forbidden."), "denied", resource
    if not service_plan_is_current(args, hass, data, access_key, permitted_entities):
        return _tool_error("Command targets or configuration changed."), "denied", resource

    if not targets_current(args, reach.entities if reach.every_target else permitted_entities, hass):
        return _tool_error(TARGETS_CHANGED), "denied", resource

    if domain in HIGH_RISK_DOMAINS:
        _LOGGER.info(
            "High-risk service call %s/%s by access key %s",
            domain, service, access_key.name,
        )

    call_data = dict(service_data)
    dispatched = permitted_entities
    if service_key == TTS_SPEAK_SERVICE:
        call_data[TTS_SPEAK_PLAYER_FIELD] = speech_players(permitted_entities)
        dispatched = speech_engines(permitted_entities)
    call_data["entity_id"] = entity_id_argument(hass, domain, service, dispatched, service_data)

    use_return_response = False
    if effective_cap(access_key, "cap_service_response") != CAP_DENY:
        try:
            # Local: inside a guard for releases that lack it.
            from homeassistant.core import SupportsResponse as _SR
            handler = hass.services.async_services().get(domain, {}).get(service)
            use_return_response = (
                handler is not None and
                getattr(handler, "supports_response", None) not in (None, _SR.NONE)
            )
        except Exception:
            _LOGGER.debug(
                "supports_response probe failed for %s/%s; calling without return_response",
                domain, service, exc_info=True,
            )

    # HA runs this same check before the handler, so a failure here proves nothing
    # ran: an agent turn can correct the call instead of stopping at an unknown outcome.
    registered = hass.services.async_services().get(domain, {}).get(service)
    if registered is not None and registered.schema is not None:
        try:
            registered.schema(dict(call_data))
        except vol.Invalid as err:
            return _tool_refused_before_write(_validation_error_message(err)), "invalid_request", resource

    try:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            service_context = household_service_kwargs(hass, domain, service)
            mark_dispatch_started()
            svc_response = await hass.services.async_call(
                domain,
                service,
                call_data,
                blocking=True,
                return_response=use_return_response,
                **service_context,
            )
    except asyncio.TimeoutError:
        return (
            _tool_success(json.dumps({
                "success": True,
                "partial": True,
                "message": "Service dispatched but HA did not respond within the timeout window.",
            })),
            "allowed",
            resource,
        )
    except ServiceNotFound:
        # For most domains, return a generic error: never confirm or deny
        # service existence (no enumeration oracle). But for a curated set of
        # core actuator domains (DOMAIN_SERVICE_HINTS), name the valid core service
        # verbs so an agent that guessed the wrong one (e.g. valve.open instead of
        # valve.open_valve) can self-correct. This is leak-safe: the catch is
        # post-authorization (the access key already proved WRITE on an entity in this
        # domain) and the verbs come from a hardcoded public HA-core list, not a
        # live hass.services lookup, so nothing hidden is revealed.
        hint = _service_not_found_hint(domain, service)
        if hint is not None:
            return _tool_error(hint[0]), "invalid_request", resource
        return _tool_error("Forbidden."), "denied", resource
    except ServiceValidationError as err:
        # The access key already holds WRITE on this entity and invoked a real service;
        # a validation error is about the caller's own argument (e.g. an
        # out-of-range setpoint), not about hidden entities or services, so it is
        # safe to surface and lets the agent self-correct instead of reading it as
        # a permission denial. (ServiceNotFound subclasses ServiceValidationError
        # but is caught above and stays generic, preserving the no-oracle rule.)
        return _tool_error(_validation_error_message(err)), "invalid_request", resource
    except vol.Invalid as err:
        # hass.services.async_call validates service_data against the target
        # service's own schema with plain voluptuous, and re-raises vol.Invalid/
        # MultipleInvalid unwrapped rather than as a HomeAssistantError (e.g. a
        # service whose schema does not accept entity_id at all, called with a
        # resolved entity target still attached). The ServiceValidationError/
        # HomeAssistantError handlers above do not catch it, since vol.Invalid
        # subclasses neither; unhandled it would reach the dispatch-level safety
        # net as a bare "Internal error" and lose the actionable detail
        # voluptuous provides. Safe to surface for the same reason as
        # ServiceValidationError: it describes the caller's own argument shape,
        # not hidden state.
        return _tool_error(_validation_error_message(err)), "invalid_request", resource
    except Unauthorized:
        return _tool_error("Forbidden."), "denied", resource
    except HomeAssistantError as err:
        # A scene, script or automation runs other entities' actions, so its failure can name
        # what this key cannot read.
        failure = _service_failure(err, domain, service, access_key, hass, detail=domain not in PRIMARY_ENTITY_DOMAINS)
        return failure, "invalid_request", resource

    body: dict[str, Any] = {"success": True}
    if local_plan:

        body["state_verification"] = await async_verify_command_state(
            hass, data, access_key, args[COMMAND_ROUTING_PLAN_KEY],
        )
        check_request_authority()
        access_key = RequestAuthority(hass, data, access_key.id, access_key.access_key_hash).current_access_key()
    filtered_response = filter_service_response(svc_response, access_key, hass) if svc_response is not None else None
    if filtered_response is not None:
        body["service_response"] = filtered_response
    try:
        body["targets"] = _service_call_targets(permitted_entities, access_key, hass)
    except Exception:  # the effect already happened; the report is optional
        _LOGGER.debug("Could not describe call_service targets", exc_info=True)
    if mesa_outcome.warnings:
        _add_mesa_advisory(body, visible_reach_warnings(mesa_outcome.warnings, reach, access_key, hass))

    return _tool_success(json.dumps(body, default=str)), "allowed", resource


def _service_call_targets(entity_ids: list[str], access_key: AccessKeyRecord, hass: HomeAssistant) -> dict[str, Any]:
    """Name what the call acted on and how each target reads right after it.

    All were already writable by this key, so nothing hidden is revealed. HA skips an unavailable
    target without failing, so those are listed separately.
    """
    states: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for entity_id in entity_ids:
        if resolve(entity_id, access_key, hass) not in (Permission.READ, Permission.WRITE):
            continue
        state = hass.states.get(entity_id)
        value = state.state if state is not None else None
        if value in (None, "unavailable"):
            unavailable.append(entity_id)
        if len(states) < SERVICE_RESULT_STATES_MAX:
            states.append({"entity_id": entity_id, "state": value})
    result: dict[str, Any] = {"count": len(entity_ids), "states_after_call": states}
    if len(entity_ids) > len(states):
        result["states_omitted"] = len(entity_ids) - len(states)
    if unavailable:
        result["unavailable"] = unavailable[:SERVICE_RESULT_STATES_MAX]
    return result


async def _execute_call_service_mesa_approved(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData
) -> tuple[dict, str, str]:
    """Re-run a MESA-gated service call after admin approval."""
    # Registered under MESA_APPROVED_EXECUTOR but never dispatchable from the tool router, so a key
    # cannot reach this path itself. Scope and MESA re-run, and only control_mode:confirm blocks
    # count as satisfied, so an entity since made prohibited or read_only is still rejected.
    return await _execute_call_service(args, access_key, hass, data, mesa_approved=True)


# --- Diff builders for Confirm-eligible tools ---------------------------------
# Each builder produces the structured payload shown in the admin Approvals UI.
# Diffs are best-effort: anything missing or non-fatal renders an empty preview
# rather than blocking creation of the pending approval.


def _build_diff_call_service(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Diff for call_service. Resolves entity targets read-only and lists service params."""
    domain = args.get("domain", "")
    service = args.get("service", "")
    # Sanitised like the executor's, so the approver reviews the call that will
    # run. Showing a selector the executor strips would describe a call reaching
    # a whole floor and invite a rejection of something that never had that reach.
    service_data = _sanitize_service_data(args.get("service_data"))
    entity_arg = args.get("entity_id")
    if isinstance(entity_arg, str):
        entity_ids: list[str] = [entity_arg]
    elif isinstance(entity_arg, list):
        entity_ids = [e for e in entity_arg if isinstance(e, str)]
    else:
        entity_ids = []
    preview = {
        "domain": domain,
        "service": service,
        "service_data": service_data,
        "requested_entity_ids": entity_ids,
        "device_id": args.get("device_id"),
        "area_id": args.get("area_id"),
    }
    # Read-only target resolution for the MESA note; the executor re-resolves
    # at approve time. Dual-gate and no-target reloads have no targets and
    # resolve to nothing, so they never carry the note.
    resolved, _count = resolve_service_targets(
        entity_id=args.get("entity_id"), device_id=args.get("device_id"),
        area_id=args.get("area_id"), service_domain=domain,
        access_key=access_key, hass=hass,
    )
    preview.update(todo_removal_review(hass, domain, service, service_data, resolved))
    reach = call_reach(domain, service, service_data, resolved, access_key, hass)
    if also_reaches := [entity_id for entity_id in reach.entities if entity_id not in resolved]:
        preview["also_reaches"] = also_reaches
    mesa_note = _mesa_confirm_annotation(access_key, hass, [(domain, service, reach.entities)])
    if mesa_note:
        preview["mesa"] = mesa_note
    return {
        "kind": "service_preview",
        **_summary("call_service.mesa" if mesa_note else "call_service",
                   domain=domain, service=service),
        "target": {"type": "service", "id": f"{domain}/{service}", "label": f"{domain}/{service}"},
        "preview": preview,
    }
