"""Registry name validation, clearing semantics, and approved execution."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.runtime_fixtures import mesa_off_store

from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import (
    _call_tool,
    async_execute_approved_tool,
)
from custom_components.phoenix_mcp.token_store import (
    PermissionNode,
    PermissionTree,
    TokenRecord,
)


@pytest.fixture(params=["entity", "device"])
def registry_name_env(hass, request):
    owner = MockConfigEntry(domain="test_integration")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("test_integration", "name-validation")},
        name="Integration device",
    )
    entity = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "name-validation",
        config_entry=owner, device_id=device.id, original_name="Integration entity",
    )
    hass.states.async_set(entity.entity_id, "on")
    kind = request.param
    if kind == "entity":
        registry = er.async_get(hass)
        target = entity.entity_id
        registry.async_update_entity(target, name="Existing override")
        name_field = "name"
        other_field = "hidden"
    else:
        registry = dr.async_get(hass)
        target = device.id
        registry.async_update_device(target, name_by_user="Existing override")
        name_field = "name_by_user"
        other_field = "enabled"
    token = TokenRecord(
        id=str(uuid.uuid4()), name="name-validation", token_hash="x",
        created_at=utcnow(), created_by="test", cap_registry_write="allow",
        permissions=PermissionTree(devices={device.id: PermissionNode(state="GREEN")}),
    )
    data = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    return kind, registry, target, name_field, other_field, token, data


@pytest.mark.parametrize("name", [" ", "\t\n", "\u2003\u00a0", "''", '""', "'", '"', " \t\" ' \n"])
@pytest.mark.parametrize("mode", ["allow", "confirm", "pass_through", "approved"])
async def test_invalid_name_refuses_complete_request(hass, registry_name_env, name, mode):
    kind, registry, target, name_field, other_field, token, data = registry_name_env
    token.cap_registry_write = "confirm" if mode == "confirm" else "allow"
    token.pass_through = mode == "pass_through"
    before = registry.async_get(target)
    changes = {"name": name, other_field: kind == "entity"}
    if kind == "entity":
        changes["new_entity_id"] = "light.renamed_validation"
    gate = AsyncMock()
    with patch("custom_components.phoenix_mcp.mcp_view._gate", gate):
        if mode == "approved":
            content, outcome, _ = await async_execute_approved_tool(
                f"set_{kind}", {f"{kind}_id": target, **changes}, token, hass, data
            )
        else:
            content, outcome, _ = await _call_tool(
                f"set_{kind}", {f"{kind}_id": target, "changes": changes}, token, hass, data
            )
    assert outcome == "invalid_request"
    assert content["isError"] is True
    assert "changes.name: null" in content["content"][0]["text"]
    gate.assert_not_awaited()
    assert registry.async_get(target) == before
    assert getattr(registry.async_get(target), name_field) == "Existing override"
    if kind == "entity":
        assert registry.async_get("light.renamed_validation") is None
    assert data.versions.list_for(kind, target) == []


@pytest.mark.parametrize("name", [None, "", 'Kitchen "Main"', "Kid's room", ' \"Lamp\" ', "灯 '一'", "!", 3, False, [], {}])
@pytest.mark.parametrize("approved", [False, True])
async def test_name_values_and_clearing(hass, registry_name_env, name, approved):
    kind, registry, target, name_field, _, token, data = registry_name_env
    args = {f"{kind}_id": target}
    if approved:
        args["name"] = name
        content, outcome, _ = await async_execute_approved_tool(
            f"set_{kind}", args, token, hass, data
        )
    else:
        args["changes"] = {"name": name}
        content, outcome, _ = await _call_tool(f"set_{kind}", args, token, hass, data)
    if name is not None and not isinstance(name, str):
        assert outcome == "invalid_request"
        assert getattr(registry.async_get(target), name_field) == "Existing override"
        return
    assert outcome == "allowed"
    assert getattr(registry.async_get(target), name_field) == (name or None)
    body = json.loads(content["content"][0]["text"])
    assert body["updated"][name_field] == (name or None)
    if kind == "device" and not name:
        assert body["updated"]["name"] == "Integration device"


async def test_omitting_name_preserves_override(hass, registry_name_env):
    kind, registry, target, name_field, other_field, token, data = registry_name_env
    _, outcome, _ = await _call_tool(
        f"set_{kind}",
        {f"{kind}_id": target, "changes": {other_field: kind == "entity"}},
        token, hass, data,
    )
    assert outcome == "allowed"
    assert getattr(registry.async_get(target), name_field) == "Existing override"


async def test_capability_denial_precedes_name_diagnostic(hass, registry_name_env):
    kind, _, target, _, _, token, data = registry_name_env
    token.cap_registry_write = "deny"
    content, outcome, _ = await _call_tool(
        f"set_{kind}", {f"{kind}_id": target, "changes": {"name": '""'}}, token, hass, data
    )
    assert outcome == "denied"
    assert "changes.name" not in content["content"][0]["text"]
