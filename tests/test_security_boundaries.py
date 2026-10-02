"""Regression coverage for identity, redaction, YAML and execution boundaries."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN, REDACTION_SENTINEL, STORAGE_KEY, STORAGE_VERSION
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.helpers import ScrubbedState, redact_secrets_in_text
from custom_components.phoenix_mcp.mcp_view import _execute_call_service, _execute_call_service_mesa_approved
from custom_components.phoenix_mcp.policy_engine import Permission, canonical_entity_id, resolve, resolve_registry_access
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import PermissionNode, TokenRecord, TokenStore
from custom_components.phoenix_mcp.tool_common import redaction_sentinel_path
from custom_components.phoenix_mcp.yaml_patch import PatchError, parse_value


def _token(**kwargs):
    return TokenRecord(id="security", name="security", token_hash="hash", created_at=utcnow(), created_by="admin", **kwargs)


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("resolver", [resolve, resolve_registry_access])
async def test_mixed_case_keeps_entity_red_precedence(hass, registered, resolver):
    if registered:
        entry = MockConfigEntry(domain="test")
        entry.add_to_hass(hass)
        er.async_get(hass).async_get_or_create("light", "test", "kitchen", config_entry=entry, suggested_object_id="kitchen")
    hass.states.async_set("light.kitchen", "on")
    token = _token()
    token.permissions.domains["light"] = PermissionNode("GREEN")
    token.permissions.entities["light.kitchen"] = PermissionNode("RED")
    assert resolver("LIGHT.Kitchen", token, hass) == Permission.DENY
    assert canonical_entity_id("LIGHT.Kitchen", hass) == "light.kitchen"


async def test_mixed_case_cannot_read_internal_sensor_in_pass_through(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entity = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "internal", config_entry=entry)
    hass.states.async_set(entity.entity_id, "1")
    for resolver in (resolve, resolve_registry_access):
        assert resolver(entity.entity_id.upper(), _token(pass_through=True), hass) == Permission.NO_ACCESS


@pytest.mark.parametrize("key", ["api_key", "password", "auth_token", "session_cookie", "entity_picture", "SECRET_VALUE"])
async def test_template_snapshot_uses_read_attribute_redaction(hass, key):
    hass.states.async_set("sensor.router", "on", {key: "private-value", "unit_of_measurement": "C"})
    state = ScrubbedState(hass.states.get("sensor.router"))
    assert key not in state.attributes
    assert "private-value" not in str(state.as_dict())
    assert state.attributes["unit_of_measurement"] == "C"


@pytest.mark.parametrize("content", ["x: &x [1, 2]\ny: *x", "&x [*x]"])
def test_yaml_patch_refuses_alias_graph_before_loading(content):
    with pytest.raises(PatchError, match="anchors and aliases"):
        parse_value(content)


def test_sentinel_walk_terminates_shared_and_cyclic_graphs():
    node = []
    node.append(node)
    for _ in range(12):
        node = [node] * 9
    assert redaction_sentinel_path(node) is None
    node.append({"key": REDACTION_SENTINEL})
    assert redaction_sentinel_path(node) == [9, "key"]


@pytest.mark.parametrize("value", ["!secret smtp_password", "!env_var SMTP_PASSWORD"])
def test_secret_references_can_be_snapshotted(value):
    text = f"password: {value}\n"
    assert redact_secrets_in_text(text) == text


@pytest.mark.parametrize("value", ["inline-password", "!env_var SMTP_PASSWORD inline-password", "!unknown inline-password", "!secret smtp_password trailing"])
def test_secret_reference_exception_cannot_admit_literals(value):
    assert redact_secrets_in_text(f"password: {value}") == "password: <redacted>"


@pytest.mark.parametrize("executor", [_execute_call_service, _execute_call_service_mesa_approved])
@pytest.mark.parametrize("mode", ["deny", "allow"])
async def test_service_executor_rechecks_current_physical_capability(hass, executor, mode):
    store = TokenStore(hass, AsyncMock())
    token, _ = await store.async_create_token("physical", "admin")
    token.cap_physical_control = mode
    token.permissions.domains["lock"] = PermissionNode("GREEN")
    store.get_settings().mesa_mode = "off"
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    entry = er.async_get(hass).async_get_or_create("lock", "test", "front_door", suggested_object_id="front_door")
    hass.states.async_set(entry.entity_id, "locked")
    calls = []

    async def unlock(call):
        calls.append(call.data)

    hass.services.async_register("lock", "unlock", unlock)
    result, outcome, _ = await executor(
        {"domain": "lock", "service": "unlock", "entity_id": entry.entity_id}, token, hass, data)
    assert outcome == ("allowed" if mode == "allow" else "denied"), result
    assert bool(calls) is (mode == "allow")
    if mode == "allow":
        assert calls == [{"entity_id": [entry.entity_id]}]
    else:
        assert result["isError"]


@pytest.mark.parametrize("selector", ["entity_id", "device_id", "area_id", "floor_id", "label_id"])
async def test_service_data_selector_cannot_widen_dispatch(hass, selector):
    token = _token(pass_through=True)
    data = PhoenixData(store=MagicMock(), rate_limiter=RateLimiter(), audit=AuditLog())
    with patch("homeassistant.core.ServiceRegistry.async_call", AsyncMock()) as dispatch:
        result, outcome, _ = await _execute_call_service({"domain": "light", "service": "turn_off", "service_data": {selector: "caller-target"}}, token, hass, data)
    assert outcome == "invalid_request"
    assert "targets" in result["content"][0]["text"]
    dispatch.assert_not_called()


async def test_untargeted_service_does_not_expand_domain(hass):
    hass.states.async_set("light.kitchen", "on")
    token = _token(pass_through=True)
    data = PhoenixData(store=MagicMock(), rate_limiter=RateLimiter(), audit=AuditLog())
    with patch("homeassistant.core.ServiceRegistry.async_call", AsyncMock()) as dispatch:
        _, outcome, _ = await _execute_call_service({"domain": "light", "service": "turn_off"}, token, hass, data)
    assert outcome == "invalid_request"
    dispatch.assert_not_called()


@pytest.mark.parametrize("version", [2, 3])
async def test_released_storage_loads_through_real_ha_store(hass, hass_storage, version):
    token = _token(expires_at=utcnow() + timedelta(days=1))
    hass_storage[STORAGE_KEY] = {"version": version, "minor_version": 1, "key": STORAGE_KEY, "data": {
        "version": version, "tokens": [token.to_storage_dict()], "settings": {"kill_switch": True},
        "pending_approvals": [{"id": "old", "status": "pending", "tool_name": "call_service", "args": {}}],
    }}
    store = await TokenStore.async_create(hass)
    loaded = store.get_token_by_id(token.id)
    assert loaded.token_hash == token.token_hash
    assert loaded.expires_at == token.expires_at
    assert store.get_settings().kill_switch
    assert store.get_pending_approvals()[0]["status"] == "cancelled"
    assert hass_storage[STORAGE_KEY]["version"] == STORAGE_VERSION


def test_invalid_expiry_never_loads_as_an_unlimited_token():
    data = _token().to_storage_dict()
    data["expires_at"] = "corrupt"
    with pytest.raises(ValueError, match="expires_at"):
        TokenRecord.from_dict(data)


async def test_disabled_registry_mixed_case_cannot_use_an_entity_only_grant(hass):
    entry = er.async_get(hass).async_get_or_create("light", "test", "fixture", suggested_object_id="fixture")
    er.async_get(hass).async_update_entity(entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    token = _token()
    token.permissions.entities[entry.entity_id] = PermissionNode("GREEN")
    for entity_id in (entry.entity_id, entry.entity_id.upper()):
        assert resolve_registry_access(entity_id, token, hass) != Permission.WRITE
    token.permissions.domains["light"] = PermissionNode("GREEN")
    assert resolve_registry_access(entry.entity_id.upper(), token, hass) == Permission.WRITE


async def test_missing_service_selector_refuses_before_creating_approval(hass):
    from custom_components.phoenix_mcp.mcp_view import _tool_call_service

    store = TokenStore(hass, AsyncMock())
    token, _ = await store.async_create_token("target", "admin")
    token.cap_physical_control = "confirm"
    token.permissions.domains["lock"] = PermissionNode("GREEN")
    store.get_settings().mesa_mode = "off"
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog())
    hass.data[DOMAIN] = data
    response, outcome, _ = await _tool_call_service(
        {"service": {"domain": "lock", "name": "unlock"}}, token, hass, data)
    assert outcome == "invalid_request", response
    assert store.get_pending_approvals() == []


@pytest.mark.parametrize("version", [0, 1])
async def test_unknown_storage_versions_refuse_without_rewriting(hass, hass_storage, version):
    from copy import deepcopy

    original = {"version": version, "minor_version": 1, "key": STORAGE_KEY, "data": {
        "version": version, "tokens": [_token().to_storage_dict()], "settings": {}, "pending_approvals": [],
    }}
    hass_storage[STORAGE_KEY] = deepcopy(original)
    with pytest.raises(ValueError, match="Unsupported Phoenix storage version"):
        await TokenStore.async_create(hass)
    assert hass_storage[STORAGE_KEY] == original


@pytest.mark.parametrize("version", [2, 3])
async def test_released_storage_does_not_cancel_an_execution_claim(hass, hass_storage, version):
    stamp = utcnow().isoformat()
    approval = {"id": "executing", "status": "pending", "tool_name": "call_service", "args": {}, "execution_started_at": stamp}
    hass_storage[STORAGE_KEY] = {"version": version, "minor_version": 1, "key": STORAGE_KEY, "data": {
        "version": version, "tokens": [], "settings": {}, "pending_approvals": [approval],
    }}
    store = await TokenStore.async_create(hass)
    stored = store.get_pending_approvals()[0]
    assert stored["status"] == "pending"
    assert stored["execution_started_at"] == stamp
