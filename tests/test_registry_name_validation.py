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
from custom_components.phoenix_mcp.device_semantics import entity_name_source
from custom_components.phoenix_mcp.mcp_view import (
    _build_diff_set_entity,
    _call_tool,
    async_execute_approved_tool,
    async_restore_version,
)
from custom_components.phoenix_mcp.access_key_store import (
    PermissionNode,
    PermissionTree,
    AccessKeyRecord,
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
    access_key = AccessKeyRecord(
        id=str(uuid.uuid4()), name="name-validation", access_key_hash="x",
        created_at=utcnow(), created_by="test", cap_registry_write="allow",
        permissions=PermissionTree(devices={device.id: PermissionNode(state="GREEN")}),
    )
    data = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    return kind, registry, target, name_field, other_field, access_key, data


@pytest.mark.parametrize("name", [" ", "\t\n", "\u2003\u00a0", "''", '""', "'", '"', " \t\" ' \n"])
@pytest.mark.parametrize("mode", ["allow", "confirm", "pass_through", "approved"])
async def test_invalid_name_refuses_complete_request(hass, registry_name_env, name, mode):
    kind, registry, target, name_field, other_field, access_key, data = registry_name_env
    access_key.cap_registry_write = "confirm" if mode == "confirm" else "allow"
    access_key.pass_through = mode == "pass_through"
    before = registry.async_get(target)
    changes = {"name": name, other_field: kind == "entity"}
    if kind == "entity":
        changes["new_entity_id"] = "light.renamed_validation"
    gate = AsyncMock()
    with patch("custom_components.phoenix_mcp.mcp_view._gate", gate):
        if mode == "approved":
            content, outcome, _ = await async_execute_approved_tool(
                f"set_{kind}", {f"{kind}_id": target, **changes}, access_key, hass, data
            )
        else:
            content, outcome, _ = await _call_tool(
                f"set_{kind}", {f"{kind}_id": target, "changes": changes}, access_key, hass, data
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
    kind, registry, target, name_field, _, access_key, data = registry_name_env
    args = {f"{kind}_id": target}
    if approved:
        args["name"] = name
        content, outcome, _ = await async_execute_approved_tool(
            f"set_{kind}", args, access_key, hass, data
        )
    else:
        args["changes"] = {"name": name}
        content, outcome, _ = await _call_tool(f"set_{kind}", args, access_key, hass, data)
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
    kind, registry, target, name_field, other_field, access_key, data = registry_name_env
    _, outcome, _ = await _call_tool(
        f"set_{kind}",
        {f"{kind}_id": target, "changes": {other_field: kind == "entity"}},
        access_key, hass, data,
    )
    assert outcome == "allowed"
    assert getattr(registry.async_get(target), name_field) == "Existing override"


async def test_capability_denial_precedes_name_diagnostic(hass, registry_name_env):
    kind, _, target, _, _, access_key, data = registry_name_env
    access_key.cap_registry_write = "deny"
    content, outcome, _ = await _call_tool(
        f"set_{kind}", {f"{kind}_id": target, "changes": {"name": '""'}}, access_key, hass, data
    )
    assert outcome == "denied"
    assert "changes.name" not in content["content"][0]["text"]


# Home Assistant 2026.10 reads an empty entity name override as "Use device name".
DEVICE_NAMING = hasattr(er, "async_get_legacy_friendly_name")


@pytest.fixture
def device_name_env(hass):
    owner = MockConfigEntry(domain="test_integration")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("test_integration", "device-name")},
        name="Porch sensor",
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor", "test_integration", "device-name", config_entry=owner,
        device_id=device.id, original_name="Battery", has_entity_name=True,
    )
    hass.states.async_set(entity.entity_id, "1")
    access_key = AccessKeyRecord(
        id=str(uuid.uuid4()), name="device-name", access_key_hash="x",
        created_at=utcnow(), created_by="test", cap_registry_write="allow",
        permissions=PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}),
    )
    data = PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())
    return entity.entity_id, device.id, access_key, data


def _friendly_name(hass, entity_id):
    return er.async_get_legacy_friendly_name(hass, er.async_get(hass).async_get(entity_id))


@pytest.mark.parametrize("approved", [False, True])
async def test_use_device_name_follows_the_device_and_reverts(hass, device_name_env, approved):
    if not DEVICE_NAMING:
        pytest.skip("Home Assistant before 2026.10 has no device-name mode")
    entity_id, device_id, access_key, data = device_name_env

    async def set_mode(value):
        if approved:
            return await async_execute_approved_tool(
                "set_entity", {"entity_id": entity_id, "use_device_name": value}, access_key, hass, data
            )
        return await _call_tool(
            "set_entity", {"entity_id": entity_id, "changes": {"use_device_name": value}},
            access_key, hass, data,
        )

    content, outcome, _ = await set_mode(True)
    assert outcome == "allowed"
    assert er.async_get(hass).async_get(entity_id).name == ""
    assert json.loads(content["content"][0]["text"])["updated"]["name_source"] == "device"
    assert _friendly_name(hass, entity_id) == "Porch sensor"
    dr.async_get(hass).async_update_device(device_id, name_by_user="Garden sensor")
    assert _friendly_name(hass, entity_id) == "Garden sensor"

    content, outcome, _ = await set_mode(False)
    assert outcome == "allowed"
    assert er.async_get(hass).async_get(entity_id).name is None
    assert json.loads(content["content"][0]["text"])["updated"]["name_source"] == "integration"
    assert _friendly_name(hass, entity_id) == "Garden sensor Battery"


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("not_boolean", "boolean"),
        ("with_name", "either name or use_device_name"),
        ("no_device", "named device"),
        ("unnamed_device", "named device"),
        ("older_home_assistant", "2026.10"),
    ],
)
async def test_use_device_name_refusals_leave_the_entity_unchanged(
    hass, device_name_env, monkeypatch, case, message
):
    entity_id, device_id, access_key, data = device_name_env
    changes: dict = {"use_device_name": True}
    if case == "not_boolean":
        changes["use_device_name"] = "yes"
    elif case == "with_name":
        changes["name"] = "Porch battery"
    elif case == "no_device":
        er.async_get(hass).async_update_entity(entity_id, device_id=None)
    elif case == "unnamed_device":
        dr.async_get(hass).async_update_device(device_id, name=None)
    elif case == "older_home_assistant":
        monkeypatch.delattr(er, "async_get_legacy_friendly_name", raising=False)
    elif not DEVICE_NAMING:
        pytest.skip("Home Assistant before 2026.10 has no device-name mode")
    before = er.async_get(hass).async_get(entity_id)
    gate = AsyncMock()
    with patch("custom_components.phoenix_mcp.mcp_view._gate", gate):
        content, outcome, _ = await _call_tool(
            "set_entity", {"entity_id": entity_id, "changes": changes}, access_key, hass, data
        )
    assert outcome == "invalid_request"
    assert message in content["content"][0]["text"]
    gate.assert_not_awaited()
    assert er.async_get(hass).async_get(entity_id) == before
    assert data.versions.list_for("entity", entity_id) == []


async def test_approved_device_name_revalidates_before_writing(hass, device_name_env):
    entity_id, _, access_key, data = device_name_env
    er.async_get(hass).async_update_entity(entity_id, device_id=None)
    content, outcome, _ = await async_execute_approved_tool(
        "set_entity", {"entity_id": entity_id, "use_device_name": True}, access_key, hass, data
    )
    assert outcome == "invalid_request"
    assert "named device" in content["content"][0]["text"]
    assert er.async_get(hass).async_get(entity_id).name is None


async def test_restore_replays_an_empty_name_override_exactly(hass, device_name_env):
    entity_id, _, access_key, data = device_name_env
    er.async_get(hass).async_update_entity(entity_id, name="")
    _, outcome, _ = await _call_tool(
        "set_entity", {"entity_id": entity_id, "changes": {"name": "Porch battery"}},
        access_key, hass, data,
    )
    assert outcome == "allowed"
    record = data.versions.list_for("entity", entity_id)[0]
    assert record.before["name"] == ""
    _, outcome, _ = await async_restore_version(record, "admin", hass, data, side="before")
    assert outcome == "allowed"
    assert er.async_get(hass).async_get(entity_id).name == ""


async def test_name_source_reports_each_naming_state(hass, device_name_env):
    entity_id, _, access_key, data = device_name_env
    registry = er.async_get(hass)
    expected = {None: "integration", "Porch battery": "custom", "": "device" if DEVICE_NAMING else "none"}
    for name, source in expected.items():
        registry.async_update_entity(entity_id, name=name)
        assert entity_name_source(hass, registry.async_get(entity_id)) == source
    registry.async_update_entity(entity_id, device_id=None)
    assert entity_name_source(hass, registry.async_get(entity_id)) == "none"
    access_key.cap_search = "allow"
    content, outcome, _ = await _call_tool("describe_entity", {"entity_id": entity_id}, access_key, hass, data)
    assert outcome == "allowed"
    assert json.loads(content["content"][0]["text"])["registry"]["name_source"] == "none"


async def test_device_name_approval_preview_shows_the_mode_change(hass, device_name_env):
    entity_id, _, access_key, _ = device_name_env
    diff = await _build_diff_set_entity({"entity_id": entity_id, "use_device_name": True}, access_key, hass)
    assert diff["preview"]["use_device_name"] == {"before": False, "after": True}
    assert "use_device_name" in diff["summary"]
