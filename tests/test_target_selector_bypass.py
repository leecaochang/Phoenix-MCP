"""Refuse misplaced targets and constrain resolved targets on every service path.

Caller-supplied target selectors are refused before service dispatch. Sanitizing
is a second boundary: only the resolved explicit entity list reaches HA.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import (
    DOMAIN,
    SECONDARY_TARGET_SELECTOR_KEYS,
    TARGET_SELECTOR_KEYS,
    ACCESS_KEY_PREFIX,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.helpers import sanitize_service_data
from custom_components.phoenix_mcp.rate_limiter import RateLimiter, RateLimitResult
from custom_components.phoenix_mcp.service_targets import (
    secondary_target_fields,
    unsupported_secondary_targets,
)
from custom_components.phoenix_mcp.access_key_store import AccessKeyRecord, AccessKeyStore

# A body carrying every selector Home Assistant honours, alongside real service
# data. The real data must survive; every selector must not.
_ALL_SELECTORS = {
    "entity_id": "light.kitchen",
    "device_id": "dev-abc",
    "area_id": "kitchen",
    "floor_id": "ground_floor",
    "label_id": "everything",
}


def _make_token(**caps: str) -> tuple[AccessKeyRecord, str]:
    import uuid

    from homeassistant.util.dt import utcnow

    raw = ACCESS_KEY_PREFIX + secrets.token_hex(32)
    return (
        AccessKeyRecord(
            id=str(uuid.uuid4()),
            name="test-token",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            created_at=utcnow(),
            created_by="user1",
            **caps,
        ),
        raw,
    )


def _make_data(access_key: AccessKeyRecord) -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    store.get_access_key_by_hash.return_value = access_key
    store.get_settings.return_value = MagicMock(
        kill_switch=False,
        disable_all_logging=False,
        log_allowed=True,
        log_denied=True,
        log_rate_limited=True,
        log_entity_names=True,
        log_client_ip=True,
        notify_on_rate_limit=False,
        mesa_mode="off",
    )
    store.update_last_used = MagicMock()
    store.get_pending_approvals.return_value = []
    store.async_lock = asyncio.Lock()

    rate_limiter = MagicMock(spec=RateLimiter)
    rate_limiter.check.return_value = RateLimitResult(
        allowed=True, rate_limiting_enabled=False,
        limit=0, remaining=0, reset=0, retry_after=0,
    )
    audit = MagicMock(spec=AuditLog)
    audit.record = MagicMock()
    return PhoenixData(store=store, rate_limiter=rate_limiter, audit=audit, rate_limit_notified={})


def _hass_with(data: PhoenixData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    hass.bus = MagicMock()
    hass.states = MagicMock()
    hass.states.async_all.return_value = []
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock(return_value=None)
    return hass


def _call_data(hass: MagicMock) -> dict:
    """The service data Phoenix MCP actually handed Home Assistant."""
    assert hass.services.async_call.called, "the service was never called"
    return hass.services.async_call.call_args.args[2]


def _assert_no_selectors_but_entity_list(call_data: dict, expected: list[str]) -> None:
    """entity_id is the resolved list Phoenix MCP chose; nothing else selects."""
    assert call_data["entity_id"] == expected
    for key in TARGET_SELECTOR_KEYS - {"entity_id"}:
        assert key not in call_data, f"{key} reached Home Assistant and would widen the call"


# --- the constant ------------------------------------------------------------


def test_selector_keys_match_home_assistant_own_definition():
    """A Home Assistant release that adds a selector must fail here.

    TARGET_SELECTOR_KEYS is the strip list, so a selector Phoenix MCP does not
    know about would silently widen every call. Pinning it against HA's own
    target-service field set fails the upgrade that introduces one.
    """
    from homeassistant.helpers import config_validation as cv

    assert TARGET_SELECTOR_KEYS == {str(k) for k in cv.TARGET_SERVICE_FIELDS}


def test_target_selection_reads_exactly_those_keys():
    """The strip list is complete with respect to what HA actually honours.

    The field set pinned above is a schema declaration; this reads the class that
    does the selecting at call time, so their agreement makes the strip sufficient
    rather than merely plausible.
    """
    from homeassistant.helpers.target import TargetSelection

    selected = {name.removesuffix("s") for name in TargetSelection.__slots__}
    assert selected == set(TARGET_SELECTOR_KEYS)


def test_static_secondary_selector_fallback_covers_installed_home_assistant():
    """A new HA service selector cannot silently become a new target channel."""
    from pathlib import Path

    import homeassistant
    import yaml

    component_root = Path(homeassistant.__file__).parent / "components"
    descriptions: dict[str, dict] = {}
    for path in component_root.glob("*/services.yaml"):
        loaded = yaml.safe_load(path.read_text()) or {}
        if isinstance(loaded, dict):
            descriptions[path.parent.name] = loaded

    discovered = secondary_target_fields(descriptions) - TARGET_SELECTOR_KEYS
    assert discovered <= SECONDARY_TARGET_SELECTOR_KEYS


def test_nested_target_selector_is_discovered_for_custom_integrations():
    descriptions = {
        "example": {
            "route": {
                "fields": {
                    "destination": {
                        "selector": {"object": {"fields": {"to": {"target": {}}}}}
                    }
                }
            }
        }
    }
    assert "destination" in secondary_target_fields(descriptions)


async def test_config_entry_selector_refused_with_missing_service_metadata(hass):
    with patch("custom_components.phoenix_mcp.service_targets.async_get_all_descriptions", AsyncMock(return_value={})):
        assert await unsupported_secondary_targets(
            hass, {"entry_id": "unrelated-entry"}, domain="homeassistant", service="reload_config_entry",
        ) == ("entry_id",)


def test_custom_config_entry_selector_is_discovered():
    descriptions = {"example": {"reload": {"fields": {"integration": {"selector": {"config_entry": {}}}}}}}
    assert "integration" in secondary_target_fields(descriptions)


# --- the helper --------------------------------------------------------------


def test_sanitizer_strips_every_selector_and_keeps_real_data():
    out = sanitize_service_data({**_ALL_SELECTORS, "brightness_pct": 40})
    assert out == {"brightness_pct": 40}


def test_sanitizer_keeps_target_which_is_a_notify_data_field():
    """`target` is a target block only at HA's own API boundary.

    Reaching hass.services.async_call it selects nothing, and the notify domain
    uses it as a real field naming a notification recipient, so stripping it
    would break those calls while closing nothing.
    """
    out = sanitize_service_data({"message": "hi", "target": ["phone"]})
    assert out == {"message": "hi", "target": ["phone"]}


def test_sanitizer_returns_a_new_dict_and_never_mutates():
    """The sanitizer returns a snapshot and never mutates its input.

    Evaluation suspends at an await, and a caller still holding the original could
    swap the target under it.
    """
    original = {"entity_id": "light.a", "brightness_pct": 1}
    out = sanitize_service_data(original)
    assert original == {"entity_id": "light.a", "brightness_pct": 1}
    assert out is not original


def test_sanitizer_degrades_a_wrong_shaped_value_to_empty():
    for bad in (None, "entity_id=light.a", ["light.a"], 7, True):
        assert sanitize_service_data(bad) == {}


@pytest.mark.asyncio
async def test_mcp_call_service_refuses_misplaced_selectors(hass, access_key_store):
    """A target selector inside service_data is refused, not forwarded to HA."""
    from custom_components.phoenix_mcp.mcp_view import _execute_call_service

    access_key, _ = _make_token()
    data = _make_data(access_key)
    hass = _hass_with(data)

    args = {
        "domain": "light",
        "service": "turn_on",
        "entity_id": "light.kitchen",
        "service_data": {**_ALL_SELECTORS, "brightness_pct": 40},
    }
    with patch(
        "custom_components.phoenix_mcp.mcp_view.resolve_service_targets",
        return_value=(["light.kitchen"], 1),
    ):
        result, outcome, _ = await _execute_call_service(args, access_key, hass, data, request_id="r1")

    assert outcome == "invalid_request"
    assert "targets" in result["content"][0]["text"]
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_call_service_refuses_tts_secondary_target(hass, access_key_store):
    """A permitted TTS entity cannot be used to speak on a denied media player."""
    from custom_components.phoenix_mcp.mcp_view import _execute_call_service

    access_key, _ = _make_token()
    data = _make_data(access_key)
    hass = _hass_with(data)
    args = {
        "domain": "tts",
        "service": "speak",
        "entity_id": "tts.allowed",
        "service_data": {
            "media_player_entity_id": "media_player.denied",
            "message": "hello",
        },
    }

    with patch(
        "custom_components.phoenix_mcp.mcp_view.resolve_service_targets"
    ) as resolve:
        content, outcome, resource = await _execute_call_service(
            args, access_key, hass, data, request_id="r1"
        )

    assert outcome == "invalid_request"
    assert resource == "service:tts/speak"
    assert "media_player_entity_id" in content["content"][0]["text"]
    resolve.assert_not_called()
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_service_description_adds_secondary_target_guard(hass):
    """Third-party selector metadata is enforced without a Phoenix release."""
    descriptions = {
        "example": {
            "route": {
                "fields": {
                    "destination": {"selector": {"entity": {"multiple": True}}}
                }
            }
        }
    }
    with patch(
        "custom_components.phoenix_mcp.service_targets.async_get_all_descriptions",
        AsyncMock(return_value=descriptions),
    ):
        blocked = await unsupported_secondary_targets(
            hass, {"destination": ["light.denied"]}, domain="example", service="route"
        )

    assert blocked == ("destination",)


@pytest.mark.asyncio
async def test_approved_reexecution_refuses_misplaced_selectors(hass, access_key_store):
    """An approval whose saved args carry a selector is refused when it executes.

    A confirm-gated call persists the caller's arguments verbatim and re-runs them
    on approval, so the executor (the single choke point) must refuse selectors
    too; checking only at the request boundary would leave approval as a second
    entrance.
    """
    from custom_components.phoenix_mcp.mcp_view import async_execute_approved_tool

    access_key, _ = _make_token()
    data = _make_data(access_key)
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp.approval_targets import bind_targets
    er.async_get(hass).async_get_or_create("light", "test", "kitchen", suggested_object_id="kitchen")
    hass.states.async_set("light.kitchen", "off")
    hass.data[DOMAIN] = data
    from pytest_homeassistant_custom_component.common import async_mock_service
    calls = async_mock_service(hass, "light", "turn_on")
    access_key.pass_through = True

    saved_args = {
        "domain": "light",
        "service": "turn_on",
        "entity_id": "light.kitchen",
        "service_data": dict(_ALL_SELECTORS),
    }
    saved_args = bind_targets(saved_args, ["light.kitchen"], hass)
    result, outcome, _ = await async_execute_approved_tool("call_service", saved_args, access_key, hass, data)
    assert outcome == "invalid_request", result
    assert not calls


@pytest.mark.asyncio
async def test_dry_run_refuses_misplaced_selectors(hass, access_key_store):
    """dry_run_service refuses misplaced selectors before consulting MESA.

    The preview predicts the verdict of the call that would actually run, so it
    never evaluates the caller's unsanitised service data.
    """
    from custom_components.phoenix_mcp.tools import discovery

    access_key, _ = _make_token(cap_search="allow")
    data = _make_data(access_key)
    data.mesa = MagicMock()
    data.store.get_settings.return_value.mesa_mode = "enforced"
    hass = _hass_with(data)

    seen: dict = {}

    def _capture(*_args, **kwargs):
        seen.update(kwargs["service_data"])
        return MagicMock(allowed=["light.kitchen"], confirm=[], blocked=[], warnings=[])

    args = {
        "service": {
            "domain": "light",
            "name": "turn_on",
            "data": {**_ALL_SELECTORS, "brightness_pct": 40},
        },
        "targets": [{"kind": "entity", "ids": ["light.kitchen"]}],
    }
    with (
        patch.object(discovery, "resolve_service_targets", return_value=(["light.kitchen"], 1)),
        patch.object(discovery, "evaluate_service_entities", side_effect=_capture),
    ):
        result, outcome, _ = await discovery._tool_dry_run_service(args, access_key, hass, data)

    assert outcome == "invalid_request"
    assert "targets" in result["content"][0]["text"]
    assert seen == {}


@pytest.mark.asyncio
async def test_mesa_evaluates_exactly_the_parameters_that_execute(hass, access_key_store):
    """MESA judges the call that runs, not a filtered view of it.

    The property is an equality, not a subset: a decision about a different payload
    than the one sent is the divergence the target check exists to catch, and the
    audit record would describe a call that never happened. The evaluated
    parameters must equal the executed ones, differing only in the entity: MESA
    decides per entity while the call carries the whole resolved list. This holds
    because selectors never survive in service data (refused at ingress, stripped
    for internal callers) and MESA and the executor receive the same service data.

    The capture sits on the ENFORCER, not on evaluate_service_entities: patching
    the latter would replace the code that builds these parameters, so the
    assertion would hold whatever that code did.
    """
    from custom_components.phoenix_mcp.mcp_view import _execute_call_service

    access_key, _ = _make_token()
    data = _make_data(access_key)
    data.store.get_settings.return_value.mesa_mode = "enforced"
    hass = _hass_with(data)

    evaluated: list[dict] = []

    def _capture(*, entity_id, service, service_params, **_kw):
        evaluated.append(dict(service_params))
        return MagicMock(allowed=True, warnings=[], rule_applied=None, reason=None)

    data.mesa = MagicMock()
    data.mesa.enforcer.evaluate = MagicMock(side_effect=_capture)

    args = {
        "domain": "light",
        "service": "turn_on",
        "entity_id": ["light.kitchen", "light.hall"],
        "service_data": {"brightness_pct": 40},
    }
    with patch(
        "custom_components.phoenix_mcp.mcp_view.resolve_service_targets",
        return_value=(["light.kitchen", "light.hall"], 2),
    ):
        await _execute_call_service(args, access_key, hass, data, request_id="r1")

    executed = _call_data(hass)
    assert [e["entity_id"] for e in evaluated] == ["light.kitchen", "light.hall"], (
        "the enforcer was not consulted per resolved entity"
    )
    for params in evaluated:
        assert {k: v for k, v in params.items() if k != "entity_id"} == {
            k: v for k, v in executed.items() if k != "entity_id"
        }


def test_contradictory_target_rule_name_matches_the_library():
    """The defect-signal branch keys on the rule name mesa-core actually emits.

    Phoenix MCP logs this rule as its own bug rather than a policy outcome, so a
    renamed rule would silently disable that alarm while the refusal persisted.
    """
    import inspect

    from custom_components.phoenix_mcp.mesa import _RULE_CONTRADICTORY_TARGET
    from custom_components.phoenix_mcp.mesa_core import enforcer

    assert f'rule_applied="{_RULE_CONTRADICTORY_TARGET}"' in inspect.getsource(enforcer)


def test_native_clean_area_argument_is_not_a_target_selector():
    """HassVacuumCleanArea passes its room in service data as cleaning_area_id.

    Its `area` names the room to clean rather than scoping entity resolution.
    Spelled area_id it would be a target selector, which the sanitizer strips,
    silently dropping the room from every clean request.
    """
    from custom_components.phoenix_mcp.tools import native

    source = native._tool_hass_vacuum_clean_area.__code__.co_consts
    keys = {c for c in source if isinstance(c, str)}
    assert "cleaning_area_id" in keys
    assert not (keys & TARGET_SELECTOR_KEYS)


@pytest.fixture
def remote_service_descriptions():
    """Installed remote service metadata plus a third-party `device` target selector."""
    from pathlib import Path

    import homeassistant
    import yaml

    remote = yaml.safe_load(
        (Path(homeassistant.__file__).parent / 'components/remote/services.yaml').read_text()
    )
    return {
        'remote': remote,
        'example': {'route': {'fields': {'device': {'selector': {'device': {}}}}}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('service', ['send_command', 'learn_command', 'delete_command'])
@pytest.mark.parametrize('surface', ['preview', 'request', 'executor'])
async def test_remote_text_device_reaches_scoped_boundary(
    service, surface, remote_service_descriptions
):
    """A vendor command-group string survives all three service boundaries."""
    import json

    from custom_components.phoenix_mcp import mcp_view
    from custom_components.phoenix_mcp.tools import discovery

    access_key, _ = _make_token(cap_search='allow', cap_physical_control='allow')
    data = _make_data(access_key)
    hass = _hass_with(data)
    service_data = {'device': 'test_command_group', 'command': 'not_sent'}
    public_args = {
        'service': {'domain': 'remote', 'name': service, 'data': service_data},
        'targets': [{'kind': 'entity', 'ids': ['remote.test']}],
    }
    with (
        patch('custom_components.phoenix_mcp.service_targets.async_get_all_descriptions',
              AsyncMock(return_value=remote_service_descriptions)),
        patch.object(mcp_view, 'resolve_service_targets', return_value=(['remote.test'], 1)),
        patch.object(discovery, 'resolve_service_targets', return_value=(['remote.test'], 1)),
    ):
        if surface == 'preview':
            content, outcome, _ = await discovery._tool_dry_run_service(public_args, access_key, hass, data)
            assert outcome == 'allowed'
            body = json.loads(content['content'][0]['text'])
            assert body['resolved_entities'] == ['remote.test']
            assert body['predicted_outcome'] == 'allowed'
            hass.services.async_call.assert_not_called()
        else:
            if surface == 'request':
                content, outcome, _ = await mcp_view._tool_call_service(public_args, access_key, hass, data)
            else:
                content, outcome, _ = await mcp_view._execute_call_service(
                    {'domain': 'remote', 'service': service, 'entity_id': 'remote.test',
                     'service_data': service_data}, access_key, hass, data
                )
            assert outcome == 'allowed', content
            assert _call_data(hass) == {**service_data, 'entity_id': ['remote.test']}


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata', [None, {}, {'text': {}, 'device': {}},
                                     {'device': {}}, {'object': {'fields': {'to': {'entity': {}}}}},
                                     {'text': True}, {'text': {'nested': {'device': {}}}}])
async def test_ambiguous_device_metadata_remains_refused(hass, remote_service_descriptions, metadata):
    remote_service_descriptions['remote']['send_command']['fields']['device']['selector'] = metadata
    with patch('custom_components.phoenix_mcp.service_targets.async_get_all_descriptions',
               AsyncMock(return_value=remote_service_descriptions)):
        assert await unsupported_secondary_targets(
            hass, {'device': 'group'}, domain='remote', service='send_command'
        ) == ('device',)


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, True, 123, ['group'], {'entity_id': 'remote.denied'}])
async def test_remote_text_metadata_does_not_admit_structured_values(hass, remote_service_descriptions, value):
    with patch('custom_components.phoenix_mcp.service_targets.async_get_all_descriptions',
               AsyncMock(return_value=remote_service_descriptions)):
        assert await unsupported_secondary_targets(
            hass, {'device': value}, domain='remote', service='send_command'
        ) == ('device',)


@pytest.mark.asyncio
async def test_text_exception_never_overrides_static_selector_keys(hass):
    descriptions = {'example': {'route': {'fields': {'media_player_entity_id': {'selector': {'text': {}}}}}}}
    with patch('custom_components.phoenix_mcp.service_targets.async_get_all_descriptions',
               AsyncMock(return_value=descriptions)):
        assert await unsupported_secondary_targets(
            hass, {'media_player_entity_id': 'media_player.denied'}, domain='example', service='route'
        ) == ('media_player_entity_id',)


@pytest.mark.asyncio
@pytest.mark.parametrize('descriptions', [{}, {'remote': {}}, {'remote': {'send_command': {}}}])
async def test_missing_requested_description_keeps_dynamic_fallback(hass, remote_service_descriptions, descriptions):
    descriptions['example'] = remote_service_descriptions['example']
    with patch('custom_components.phoenix_mcp.service_targets.async_get_all_descriptions',
               AsyncMock(return_value=descriptions)):
        assert await unsupported_secondary_targets(
            hass, {'device': 'group'}, domain='remote', service='send_command'
        ) == ('device',)


@pytest.mark.asyncio
async def test_description_change_is_rechecked_by_executor(remote_service_descriptions):
    """Approval cannot preserve a text exception after metadata becomes a target."""
    from custom_components.phoenix_mcp import mcp_view

    access_key, _ = _make_token(cap_physical_control='allow')
    data = _make_data(access_key)
    hass = _hass_with(data)
    remote_service_descriptions['remote']['send_command']['fields']['device']['selector'] = {'device': {}}
    with patch('custom_components.phoenix_mcp.service_targets.async_get_all_descriptions',
               AsyncMock(return_value=remote_service_descriptions)):
        content, outcome, _ = await mcp_view._execute_call_service(
            {'domain': 'remote', 'service': 'send_command', 'entity_id': 'remote.test',
             'service_data': {'device': 'group', 'command': 'not_sent'}}, access_key, hass, data,
            mesa_approved=True,
        )
    assert outcome == 'invalid_request'
    assert content['isError'] is True
    hass.services.async_call.assert_not_called()
