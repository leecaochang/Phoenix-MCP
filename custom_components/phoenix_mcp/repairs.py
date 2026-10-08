"""Operator reconciliation of uncertain provider-driven Home Assistant actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import RepairsFlow

if TYPE_CHECKING:
    from homeassistant.components.repairs.models import RepairsFlowResult
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .schema_compat import schema_engine


class AgentRecoveryFlow(RepairsFlow):
    """Acknowledge a checked outcome without executing or replaying an action."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        data = self.hass.data.get(DOMAIN)
        receipt_id = (self.data or {}).get("calendar_receipt_id")
        rid = str(receipt_id or (self.data or {}).get("request_id") or "")
        if data is None:
            return self.async_abort(reason="not_ready")
        from homeassistant.helpers import issue_registry as ir

        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        placeholders = issue.translation_placeholders if issue else {"tool": "", "request_id": rid}
        errors = {}
        if user_input is not None:
            if user_input.get("checked_home_assistant") is not True:
                errors["base"] = "check_required"
            elif await (data.calendar_writes.reviewed(rid) if receipt_id and data.calendar_writes is not None
                        else data.agent_recovery.reviewed(rid)):
                return self.async_create_entry(data={})
            else:
                errors["base"] = "operation_active"
        engine = schema_engine()
        return self.async_show_form(
            step_id="confirm",
            data_schema=engine.Schema({engine.Required("checked_home_assistant", default=False): bool}),
            errors=errors, description_placeholders=placeholders,
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create the HA-admin-only reconciliation flow."""
    return AgentRecoveryFlow()
