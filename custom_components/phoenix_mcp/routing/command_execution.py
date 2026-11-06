"""Execute command proposals through existing dispatch, approval and recovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from ..agent_dispatch import dispatch_agent_tool
from ..agent_recovery import RecoveryRequired, recovery_turn
from ..agent_turn import dispatch_tool_result, _parse_pending
from ..audit import generate_request_id
from ..const import VOICE_AGENT_CLIENT_IP
from ..helpers import log_internal_failure
from ..catalog_text import voice_text
from ..tool_dispatch import _dispatch_mcp
from .command_display import async_display_state, async_state_translations
from .command_operations import service_arguments
from .command_plan import _capture_plan
from .command_routing import RoutingAuthority
from .command_summary import summary_text
from .plan_currency import _local_plan, plan_is_current
from .routing_statistics import local_result
from .routing_tables import COMMAND_ROUTING_READ_INTENTS

_LOGGER = logging.getLogger(__name__)


@dataclass
class LocalCommandReply:
    """Result of one local command turn: reply text, outcome, and any pending approval."""

    text: str
    outcome: str = "ok"
    pending: dict | None = None
    allows_local_followup: bool = False
    statistics_completed: bool = False


async def _dispatch_command(hass: HomeAssistant, data: Any, access_key: Any, plan: dict,
                            authority: RoutingAuthority, base_url: str,
                            include_review_links: bool, *, client_ip: str = VOICE_AGENT_CLIENT_IP,
                            cancel: asyncio.Event | None = None, language: object = "en") -> LocalCommandReply:
    """Run inside the owning ingress's recovery turn, including response delivery."""

    await data.agent_recovery.reconcile(data.store, access_key.id)
    current = authority.current(hass)
    if cancel is not None and cancel.is_set():
        return LocalCommandReply(voice_text(language, "local_cancelled"), "cancelled")
    if current is None or not plan_is_current(plan, hass, data, current):
        return LocalCommandReply(voice_text(language, "access_key_unavailable"), "unauthorized")
    access_key = replace(current, confirm_inline_wait_seconds=0)
    proposal = plan["proposal"]
    read = proposal["intent"] in COMMAND_ROUTING_READ_INTENTS
    if not read and data.agent_recovery.blocks(access_key.id):
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    arguments: dict[str, Any]
    if proposal['intent'] == 'PhoenixStateSummary':
        name = 'get_state'
        arguments = {'entity_id': list(proposal['entity_ids']), 'projection': {'kind': 'fields', 'fields': ['state']}}
    elif read:
        name = "get_state"
        field = "state" if proposal["intent"] == "HassGetState" else "attr." + proposal["value"]
        arguments = {"entity_id": proposal["entity_ids"][0], "projection": {
            "kind": "fields", "fields": [field, "attr.unit_of_measurement", "attr.temperature_unit", "attr.device_class"]}}
    else:
        name = "call_service"
        service_args = service_arguments(proposal)
        if service_args is None:
            return LocalCommandReply(voice_text(language, "access_key_unavailable"), "unauthorized")
        arguments = {"service": {"domain": service_args["domain"], "name": service_args["service"], "data": service_args["service_data"]},
                     "targets": [{"kind": "entity", "ids": proposal["entity_ids"]}]}
    request_id = generate_request_id()
    context = _local_plan.set(plan)
    try:
        response, _method, _resource, outcome = await dispatch_agent_tool(
            _dispatch_mcp, hass=hass, data=data, access_key=access_key, name=name, arguments=arguments,
            tool_id=request_id, request_id=request_id, client_ip=client_ip, base_url=base_url,
        )
    except RecoveryRequired:
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    finally:
        _local_plan.reset(context)
    if authority.current(hass) is None:
        return LocalCommandReply(voice_text(language, "outcome_unknown" if not read else "access_key_unavailable"), "outcome_unknown")
    result = dispatch_tool_result(response)
    pending = _parse_pending(result, outcome=outcome, data=data, access_key_id=access_key.id,
                             tool_name=name, request_id=request_id)
    if pending:
        text = voice_text(language, "local_pending")
        if include_review_links:
            text += f"\n\n[{voice_text(language, 'local_review')}]({base_url}{pending['review_url']})"
        return LocalCommandReply(text, pending={
            "approval_id": pending["approval_id"], "tool_name": name,
            "review_url": pending.get("review_url"), "expires_at": pending.get("expires_at"),
            "diff": pending["record"].diff,
        })
    if result.get("isError") or outcome != "allowed":
        return LocalCommandReply(voice_text(language, "local_failed"), "failed")
    payload = None
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                payload = json.loads(item["text"])
                break
            except (ValueError, TypeError):
                continue
    if proposal['intent'] == 'PhoenixStateSummary':
        rows = payload.get('states') if isinstance(payload, dict) else None
        translations = await async_state_translations(hass, language)
        summary_reply = summary_text(proposal, rows, language, translations) if plan_is_current(plan, hass, data, access_key) else None
        return LocalCommandReply(summary_reply or voice_text(language, 'local_read_failed'), outcome='ok' if summary_reply is not None else 'failed', allows_local_followup=summary_reply is not None, statistics_completed=summary_reply is not None)
    if not isinstance(payload, dict):
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    if read:
        if not plan_is_current(plan, hass, data, access_key):
            return LocalCommandReply(voice_text(language, "access_key_unavailable"), "unauthorized")
        attributes = payload.get("attributes", {})
        if not isinstance(attributes, dict):
            return LocalCommandReply(voice_text(language, "local_read_failed"), "failed")
        state = payload.get("state") if proposal["intent"] == "HassGetState" else attributes.get(proposal["value"])
        if proposal["intent"] != "HassGetState":
            if state is None:
                return LocalCommandReply(voice_text(language, "local_attribute_unavailable"), "failed")
        domain = proposal["entity_ids"][0].split(".", 1)[0]
        temperature_unit = (hass.config.units.temperature_unit if domain in ("climate", "water_heater")
                            else attributes.get("temperature_unit") if domain == "weather" else None)
        state = await async_display_state(hass, proposal["entity_ids"][0], payload, language,
            field=proposal.get("value") if proposal["intent"] != "HassGetState" else None,
            temperature_unit=temperature_unit if isinstance(temperature_unit, str) else None)
        current = authority.current(hass)
        if current is None or not plan_is_current(plan, hass, data, current):
            return LocalCommandReply(voice_text(language, "access_key_unavailable"), "unauthorized")
        if isinstance(state, str) and len(state) <= 1024:
            return LocalCommandReply(voice_text(language, "local_state", state=state), allows_local_followup=True, statistics_completed=True)
        return LocalCommandReply(voice_text(language, "local_read_failed"), "failed")
    if payload.get("partial") or payload.get("success") is not True:
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")
    warnings = payload.get("mesa_advisory", [])
    advisory = " ".join(warning for warning in warnings if isinstance(warning, str)) if isinstance(warnings, list) else ""
    matched = payload.get("state_verification") == "matched"
    text = voice_text(language, "local_state_matched" if matched else "local_state_unconfirmed")
    return LocalCommandReply(text + (f" {advisory}" if advisory else ""),
                             allows_local_followup=matched and not warnings, statistics_completed=True)


@recovery_turn
async def _run_local_command(hass: HomeAssistant, data: Any, access_key: Any, plan: dict,
                             authority: RoutingAuthority, base_url: str,
                             include_review_links: bool, language: object = "en") -> LocalCommandReply:
    """Voice owns a complete non-streaming command recovery turn."""
    return await _dispatch_command(hass, data, access_key, plan, authority, base_url, include_review_links, language=language)


async def async_execute_chat_command(hass: HomeAssistant, data: Any, access_key: Any, proposal: dict,
                                     authority: RoutingAuthority, base_url: str,
                                     client_ip: str, cancel: asyncio.Event, *, language: object = "en") -> LocalCommandReply | None:
    """Reuse the outer Chat recovery turn; None permits pre-dispatch fallback."""
    try:
        plan = _capture_plan(hass, data, access_key, proposal, ingress="chat")
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        return None
    if plan is None:
        return None
    return await async_execute_chat_plan(hass, data, access_key, plan, authority, base_url, client_ip, cancel, language=language)


async def async_execute_chat_plan(hass: HomeAssistant, data: Any, access_key: Any, plan: dict,
                                  authority: RoutingAuthority, base_url: str,
                                  client_ip: str, cancel: asyncio.Event, *, language: object = "en") -> LocalCommandReply:
    """Consume the frozen Chat plan within its outer recovery turn, without replay."""
    if plan.get("ingress") != "chat":
        return LocalCommandReply(voice_text(language, "access_key_unavailable"), "unauthorized")
    try:
        return await _dispatch_command(hass, data, access_key, plan, authority, base_url, True,
                                       client_ip=client_ip, cancel=cancel, language=language)
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        return LocalCommandReply(voice_text(language, "outcome_unknown"), "outcome_unknown")


async def async_execute_local_command(hass: HomeAssistant, data: Any, access_key: Any, proposal: dict,
                                      authority: RoutingAuthority, base_url: str,
                                      include_review_links: bool = False, *, language: object = "en") -> str | None:
    """None allows provider fallback only before any local dispatch is attempted."""
    try:
        plan = _capture_plan(hass, data, access_key, proposal)
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        return None
    if plan is None:
        return None
    return await async_execute_command_plan(hass, data, access_key, plan, authority, base_url, include_review_links, language=language)


async def async_execute_command_plan(hass: HomeAssistant, data: Any, access_key: Any, plan: dict,
                                      authority: RoutingAuthority, base_url: str,
                                      include_review_links: bool = False, *, language: object = "en") -> str:
    """Consume the exact frozen plan; never replan or fall back after this boundary."""
    try:
        reply = await _run_local_command(hass, data, access_key, plan, authority, base_url, include_review_links, language=language)
        local_result(reply, "decision" if plan.get("decision_category") else "local")
        return reply.text
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_execution", exc)
        # A failed dispatch or receipt save must never cause provider replay.
        return voice_text(language, "outcome_unknown")
