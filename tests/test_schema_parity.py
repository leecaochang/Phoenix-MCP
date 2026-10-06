"""One argument contract across routes: the advertised JSON Schema, Assist's engine and MCP.

The Assist bridge validates with Home Assistant's active engine (Probatio on the
pinned release) before Phoenix's own dispatch runs; every other route reaches
the MCP checks directly. These cases pin where the routes agree, and name the
known differences so an engine upgrade or a handler change cannot move one
silently. Structural acceptance is all this covers: scope, MESA, approval and
authority are decided later on every route.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from jsonschema import Draft202012Validator

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.assist_api import _convert_to_voluptuous
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _TOOL_DEFS_BY_NAME, _call_tool
from custom_components.phoenix_mcp.version_store import VersionStore
from tests.runtime_fixtures import mesa_off_store

LAMP = "light.kitchen"
_SERVICE = {"domain": "light", "name": "turn_on"}
_TARGETS = [{"kind": "entity", "ids": [LAMP]}]

# (tool, arguments, MCP outcome). The advertised schema decides validity; Assist
# must agree with it except where _ASSIST_STRICTER says otherwise.
CASES = [
    ("get_state", {"entity_id": LAMP, "projection": {"kind": "compact"}}, "allowed"),
    ("get_state", {"entity_id": [LAMP], "projection": {"kind": "compact"}}, "allowed"),
    ("get_state", {"entity_id": [], "projection": {"kind": "compact"}}, "invalid_request"),
    ("get_state", {"entity_id": LAMP}, "invalid_request"),
    ("get_state", {"entity_id": LAMP, "projection": {"kind": "compact", "x": 1}}, "invalid_request"),
    ("search_entities", {"query": "kitchen", "limit": 5}, "allowed"),
    ("search_entities", {"query": "kitchen", "limit": 2.0}, "allowed"),
    ("search_entities", {"registry_state": "everything"}, "invalid_request"),
    ("search_entities", {"domain": [1]}, "invalid_request"),
    ("get_history", {"entity_id": LAMP, "order": "random"}, "invalid_request"),
    ("get_history", {"entity_id": LAMP, "limit": 1001}, "invalid_request"),
    ("get_logs", {"level": "INFO"}, "invalid_request"),
    ("get_logs", {"search": "x" * 513}, "invalid_request"),
    ("call_service", {"service": _SERVICE, "targets": _TARGETS}, "allowed"),
    ("call_service", {"service": {**_SERVICE, "data": {"brightness": 10}}, "targets": _TARGETS}, "allowed"),
    ("call_service", {"service": {**_SERVICE, "x": 1}, "targets": _TARGETS}, "invalid_request"),
    ("call_service", {"service": _SERVICE, "targets": []}, "invalid_request"),
    ("call_service", {"service": _SERVICE, "targets": [{"kind": "label", "ids": ["x"]}]}, "invalid_request"),
    ("call_service", {"service": {**_SERVICE, "data": "bright"}, "targets": _TARGETS}, "invalid_request"),
    ("set_entity", {"entity_id": LAMP, "changes": {"name": "Kitchen"}}, "pending_approval"),
    ("set_entity", {"entity_id": LAMP, "changes": {"name": None}}, "pending_approval"),
    ("set_entity", {"entity_id": LAMP, "changes": {"add_aliases": "Cooker light"}}, "pending_approval"),
    ("set_entity", {"entity_id": LAMP, "changes": {}}, "invalid_request"),
    ("set_entity", {"entity_id": LAMP, "changes": {"enabled": None}}, "invalid_request"),
    ("set_entity", {"entity_id": LAMP, "changes": {"nmae": "x"}}, "invalid_request"),
    ("set_entity", {"entity_id": LAMP, "changes": {"categories": {"automation": 5}}}, "invalid_request"),
    # Out-of-range and mistyped scalars are refused by name on every route.
    ("search_entities", {"query": "kitchen", "limit": 501}, "invalid_request"),
    ("search_entities", {"query": "kitchen", "limit": "5"}, "invalid_request"),
    ("search_entities", {"query": "kitchen", "offset": -1}, "invalid_request"),
    ("search_entities", {"unavailable": "true"}, "invalid_request"),
    ("watch_entity", {"entity_id": LAMP, "timeout": 31}, "invalid_request"),
    # Every root is closed, so an unknown argument is refused on every route.
    ("get_state", {"entity_id": LAMP, "projection": {"kind": "compact"}, "verbose": True}, "invalid_request"),
    # Known difference, MCP stricter: projection fields only pair with kind fields,
    # which the schema states in prose rather than structure (a union would hide
    # the tool from Gemini).
    ("get_state", {"entity_id": LAMP, "projection": {"kind": "compact", "fields": ["state"]}}, "invalid_request"),
]


def _schema(tool: str) -> dict:
    return copy.deepcopy(_TOOL_DEFS_BY_NAME[tool]["inputSchema"])


def _assist_accepts(tool: str, args: dict) -> bool:
    try:
        _convert_to_voluptuous(_schema(tool))(copy.deepcopy(args))
    except Exception:  # noqa: BLE001 - any engine refusal
        return False
    return True


@pytest.mark.parametrize(("tool", "args", "_outcome"), CASES)
def test_assist_engine_matches_the_advertised_schema(tool, args, _outcome):
    assert _assist_accepts(tool, args) is Draft202012Validator(_schema(tool)).is_valid(args)


def test_assist_defaults_do_not_refuse_an_exception_detail():
    """Assist fills declared defaults in; none may conflict with a documented call."""
    args = {"detail_ref": "a" * 64, "detail_field": "exception"}
    filled = _convert_to_voluptuous(_schema("get_log_detail"))(dict(args))
    assert "message_index" not in filled


class _Store:
    def __init__(self) -> None:
        self.pending: list = []
        self.async_lock = asyncio.Lock()
        self.async_save = AsyncMock()
        self.get_settings = mesa_off_store().get_settings
        self.get_access_key_by_id = MagicMock(return_value=None)

    def get_pending_approvals(self) -> list:
        return self.pending

    def set_pending_approvals(self, value: list) -> None:
        self.pending = value


async def test_mcp_route_outcomes(hass, hass_admin_user):
    assert await async_setup_component(hass, "light", {})
    er.async_get(hass).async_get_or_create("light", "t", "k", suggested_object_id="kitchen")
    hass.states.async_set(LAMP, "off", {"friendly_name": "Kitchen"})
    hass.services.async_register("light", "turn_on", AsyncMock())
    key = AccessKeyRecord(
        id=str(uuid.uuid4()), name="parity", access_key_hash="x", created_at=utcnow(), created_by="t",
        permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}), confirm_inline_wait_seconds=0,
        cap_registry_write="confirm", cap_search="allow", cap_registry_read="allow", cap_log_read="allow",
        cap_config_read="allow",
    )
    mismatched = []
    for tool, args, expected in CASES:
        data = PhoenixData(store=_Store(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())
        result, outcome, _ = await _call_tool(tool, json.loads(json.dumps(args)), key, hass, data)
        if outcome != expected:
            mismatched.append((tool, args, outcome, result["content"][0]["text"][:120]))
    assert not mismatched
