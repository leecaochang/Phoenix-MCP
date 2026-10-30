"""Refuse Home Assistant template syntax in caller service data on every service path.

A service may render a field as a template over every entity's state, outside the
access key's scope, so a templated value is refused before dispatch, at approved
re-execution, and in the dry-run preview.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import async_mock_service

from tests.runtime_fixtures import mesa_off_store

from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, PermissionNode, PermissionTree
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.service_targets import template_fields

_ORACLE = "{% if is_state('lock.front_door', 'unlocked') %}/config/www/a.jpg{% else %}/nope/b.jpg{% endif %}"


def _access_key(**caps: str) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(entities={"camera.front": PermissionNode(state="GREEN")}), **caps,
    )


def _data() -> PhoenixData:
    return PhoenixData(store=mesa_off_store(), rate_limiter=MagicMock(), audit=MagicMock())


@pytest.fixture
def camera(hass):
    er.async_get(hass).async_get_or_create("camera", "test", "front", suggested_object_id="front")
    hass.states.async_set("camera.front", "idle")
    hass.states.async_set("lock.front_door", "unlocked")
    return async_mock_service(hass, "camera", "snapshot")


@pytest.mark.parametrize("value", [
    "{{ states('lock.front_door') }}", "{% if true %}x{% endif %}", "{# note #}",
    ["/config/www/a.jpg", "{{ 1 }}"], {"nested": {"deeper": "{{ 1 }}"}}, {"{{ key }}": "plain"},
])
def test_template_syntax_is_found_at_any_depth_and_in_keys(value):
    assert template_fields({"filename": value, "quality": 80}) == ("filename",)


@pytest.mark.parametrize("data", [
    {"filename": "/config/www/front.jpg"}, {"message": "Braces { alone } are fine"}, {}, None, "{{ 1 }}",
])
def test_plain_service_data_passes(data):
    assert template_fields(data) == ()


async def test_call_service_refuses_a_templated_field_before_dispatch(hass, camera):
    content, outcome, _ = await _call_tool("call_service", {
        "domain": "camera", "service": "snapshot", "target": {"entity_id": "camera.front"},
        "service_data": {"filename": _ORACLE},
    }, _access_key(), hass, _data())
    assert outcome == "invalid_request", content
    text = content["content"][0]["text"]
    assert "Template syntax is not supported in service_data: filename." in text
    assert "lock.front_door" not in text
    assert not camera


async def test_call_service_forwards_a_plain_field(hass, camera):
    _content, outcome, _ = await _call_tool("call_service", {
        "domain": "camera", "service": "snapshot", "target": {"entity_id": "camera.front"},
        "service_data": {"filename": "/config/www/front.jpg"},
    }, _access_key(), hass, _data())
    assert outcome == "allowed"
    assert [call.data["filename"] for call in camera] == ["/config/www/front.jpg"]


async def test_approved_reexecution_refuses_a_templated_field(hass, camera):
    """Approval re-runs saved arguments, so the executor refuses them too."""
    from custom_components.phoenix_mcp.approval_targets import bind_targets

    data = _data()
    hass.data[DOMAIN] = data
    saved = bind_targets({
        "domain": "camera", "service": "snapshot", "entity_id": "camera.front",
        "service_data": {"filename": _ORACLE},
    }, ["camera.front"], hass)
    content, outcome, _ = await async_execute_approved_tool("call_service", saved, _access_key(), hass, data)
    assert outcome == "invalid_request", content
    assert not camera


async def test_dry_run_refuses_a_templated_field(hass, camera):
    content, outcome, _ = await _call_tool("dry_run_service", {
        "service": {"domain": "camera", "name": "snapshot", "data": {"filename": _ORACLE}},
        "targets": [{"kind": "entity", "ids": ["camera.front"]}],
    }, _access_key(cap_search="allow"), hass, _data())
    assert outcome == "invalid_request", content
    assert "Template syntax is not supported" in content["content"][0]["text"]
