"""Delete a helper, through its storage collection or its config entry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..access_key_store import AccessKeyRecord
from ..const import CAP_DENY, TEMPLATE_RECOVERY_TYPES
from ..data import PhoenixData
from ..catalog_text import diff_summary_fields as _summary
from ..capability import effective_cap
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _tool_error
from .discovery import _registry_relationship_preview
from .helper import (
    _SCOPED_STORAGE_HELPER_TYPES, _bind_scoped_helper_context, _execute_delete_helper,
    _helper_mesa_approval_gate, _helper_mesa_context, _helper_write_precheck,
    _resolve_helper_entity_id, _stored_helper_item, _template_helper_entry,
)
from .integrations import _tool_remove_integration


async def _tool_delete_helper(
    args: dict, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
    request_id: str = "", client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: delete a helper (Confirm-gated)."""
    if effective_cap(access_key, "cap_helper_write") == CAP_DENY:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "delete_helper"
    if args.get("helper_type") in TEMPLATE_RECOVERY_TYPES:
        helper_id = str(args.get("helper_id") or "").strip()
        helper_type = str(args.get("helper_type"))
        entry = _template_helper_entry(hass, helper_type, helper_id)
        if entry is None:
            return _tool_error("Helper not found."), "not_found", f"helper:{helper_type}:{helper_id}"
        return await _tool_remove_integration(
            {"entry_id": entry.entry_id}, access_key, hass, data,
            request_id=request_id, client_ip=client_ip,
        )
    pre = _helper_write_precheck(
        {**args, "config": {}}, "delete_helper", require_id=True
    )
    if pre is not None:
        return pre
    approval_args, context_error = await _bind_scoped_helper_context(
        args, access_key, hass, "delete_helper"
    )
    if context_error is not None:
        return context_error
    helper_type = str(args.get("helper_type"))
    helper_id = str(args.get("helper_id") or "").strip()
    entity_id = _resolve_helper_entity_id(hass, helper_type, helper_id)
    if entity_id is None:
        return _tool_error("Helper not found."), "not_found", f"helper:{helper_type}:{helper_id}"
    mesa_context = _helper_mesa_context(
        data,
        access_key,
        [entity_id],
        action="delete",
        service_data={"helper_type": helper_type},
        session_id=request_id or "delete_helper",
    )
    if isinstance(mesa_context, tuple):
        return mesa_context
    # The executor needs this item as its safety copy, so refuse now rather
    # than after an approval that could only fail. Scoped types read it above.
    if helper_type not in _SCOPED_STORAGE_HELPER_TYPES:
        _, missing = await _stored_helper_item(hass, access_key, helper_type, helper_id, entity_id, "delete_helper")
        if missing is not None:
            return missing
    approval_args, blocked = await _helper_mesa_approval_gate(
        approval_args,
        access_key,
        hass,
        data,
        context=mesa_context,
        tool_name="delete_helper",
        request_id=request_id,
        client_ip=client_ip,
        diff_builder=lambda: _build_diff_delete_helper(approval_args, access_key, hass),
    )
    if blocked is not None:
        return blocked
    return await _execute_delete_helper(approval_args, access_key, hass, data)


async def _build_diff_delete_helper(args: dict, access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:

    helper_type = args.get("helper_type")
    helper_id = str(args.get("helper_id") or "").strip()
    entity_id = _resolve_helper_entity_id(hass, str(helper_type), helper_id)
    state = hass.states.get(entity_id) if entity_id is not None else None
    label = (
        state.attributes.get("friendly_name")
        if state is not None else None
    ) or helper_id
    return {
        "kind": "system_action",
        **_summary("delete_helper", helper_type=helper_type, helper_id=helper_id),
        "target": {"type": "helper", "id": helper_id, "label": label},
        "before": None,
        "preview": {
            "helper_type": helper_type, "warning": "This helper will be removed permanently.",
            "relationships": (await _registry_relationship_preview(hass, entity_id) if entity_id is not None
                              else {"consumers": [], "consumer_count": 0, "searched": []}),
        },
    }
