"""Targeted argument diagnostics prevent accidental fallback without strict schemas."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp.const import UNKNOWN_ARGUMENT_PROTECTED_FIELDS
from custom_components.phoenix_mcp.mcp_view import _call_tool
from custom_components.phoenix_mcp.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.phoenix_mcp.tool_contracts import normalize_tool_args, unknown_argument_error
from custom_components.phoenix_mcp.tool_defs import _ENTITY_TOOL_DEFS, _SYSTEM_TOOL_DEFS


DEFINITIONS = {d['name']: d for d in (*_ENTITY_TOOL_DEFS, *_SYSTEM_TOOL_DEFS)}
CASES = [
    ('get_dashboard_config', {'dashboard_url': 'private'}, 'url_path'),
    ('set_dashboard_config', {'dashboard_url': 'private', 'config': {'views': []}}, 'url_path'),
    ('add_dashboard_card', {'dashboard_url': 'private', 'view_index': 0, 'card': {'type': 'markdown'}}, 'url_path'),
    ('edit_dashboard_card', {'url_path': 'valid', 'view_indxe': 0}, 'view_index'),
    ('delete_dashboard_card', {'section_indxe': 0}, 'section_index'),
    ('patch_dashboard', {'target': {'path': ['views'], 'dashboard_url': 'private'}, 'change': {'kind': 'set', 'value': []}}, 'target.url_path'),
    ('edit_dashboard', {'dashboard': 'private', 'config': {}}, 'dashboard_id'),
    ('delete_dashboard', {'dashboard': 'private'}, 'dashboard_id'),
    ('list_files', {'directory': 'www'}, 'path'),
    ('read_file', {'filename': 'www/private.txt'}, 'path'),
    ('write_file', {'filename': 'www/private.txt', 'content': 'changed'}, 'path'),
    ('get_yaml_config', {'filename': 'private.yaml'}, 'file'),
    ('set_yaml_config', {'file_path': 'private.yaml', 'content': 'changed: true'}, 'file'),
    ('patch_yaml_config', {'file_path': 'private.yaml', 'address': {'kind': 'key', 'value': 'default_config'}, 'change': {'kind': 'set', 'content': '{}'}}, 'file'),
    ('set_entity', {'entity_ids': ['light.private'], 'changes': {'name': 'new'}}, 'entity_id'),
    ('delete_entity', {'entity': 'light.private'}, 'entity_id'),
    ('set_device', {'device': 'private', 'changes': {'name': 'new'}}, 'device_id'),
    ('remove_device', {'device': 'private'}, 'device_id'),
    ('set_integration', {'integration_id': 'private', 'changes': {'title': 'new'}}, 'entry_id'),
    ('dry_run_service', {'service': {'domain': 'light', 'name': 'turn_on'}, 'target': [{'kind': 'entity', 'ids': ['light.private']}]}, 'targets'),
    ('call_service', {'service': {'domain': 'light', 'name': 'turn_on'}, 'tragets': [{'kind': 'entity', 'ids': ['light.private']}]}, 'targets'),
]


def token(**caps):
    return TokenRecord(
        id='test', name='test', token_hash='x', created_at=utcnow(), created_by='test',
        permissions=PermissionTree(domains={'light': PermissionNode(state='GREEN')}), **caps,
    )


@pytest.mark.parametrize('name,args,expected', CASES)
def test_diagnostic_is_actionable_and_does_not_rewrite(name, args, expected):
    original = deepcopy(args)
    error = unknown_argument_error(name, args)
    assert error and expected in error and 'Valid parameters:' in error
    assert 'private' not in error
    assert args == original


@pytest.mark.parametrize('name', UNKNOWN_ARGUMENT_PROTECTED_FIELDS)
def test_policy_matches_schema_and_tolerates_unrelated_fields(name):
    properties = DEFINITIONS[name]['inputSchema']['properties']
    assert set(UNKNOWN_ARGUMENT_PROTECTED_FIELDS[name]) <= properties.keys()
    assert unknown_argument_error(name, {'unrelated_future_field': True}) is None
    assert unknown_argument_error(name, {key: None for key in properties}) is None


@pytest.mark.parametrize('mode', ['allow', 'confirm', 'deny'])
@pytest.mark.parametrize('name,args,expected', CASES[:-1])
async def test_fixed_capability_guard_precedes_io_and_approval(hass, name, args, expected, mode):
    cap = DEFINITIONS[name]['cap']
    data = MagicMock()
    with (
        patch('custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config', new_callable=AsyncMock) as read,
        patch('custom_components.phoenix_mcp.tools.config_files._resolve_yaml_read_path') as yaml_path,
        patch('custom_components.phoenix_mcp.tools.config_files._resolve_fs_path') as file_path,
        patch('custom_components.phoenix_mcp.tools.lovelace._gate', new_callable=AsyncMock) as dashboard_gate,
        patch('custom_components.phoenix_mcp.tools.config_files._gate', new_callable=AsyncMock) as file_gate,
        patch('custom_components.phoenix_mcp.mcp_view._gate', new_callable=AsyncMock) as registry_gate,
    ):
        result, outcome, _ = await _call_tool(name, args, token(**{cap: mode}), hass, data)
    text = result['content'][0]['text']
    assert outcome == ('denied' if mode == 'deny' else 'invalid_request')
    if mode == 'deny':
        assert 'Forbidden' in text and 'Valid parameters' not in text and expected not in text
    else:
        assert expected in text and 'Valid parameters' in text
    for boundary in (read, yaml_path, file_path, dashboard_gate, file_gate, registry_gate):
        boundary.assert_not_called()
    assert not data.mock_calls


@pytest.mark.parametrize('name', ['patch_dashboard', 'patch_yaml_config', 'set_entity', 'set_device', 'set_integration', 'dry_run_service'])
async def test_denied_capability_precedes_existing_normalization_errors(hass, name):
    cap = DEFINITIONS[name]['cap']
    result, outcome, _ = await _call_tool(name, {}, token(**{cap: 'deny'}), hass, MagicMock())
    assert outcome == 'denied'
    assert 'Forbidden' in result['content'][0]['text']


@pytest.mark.parametrize('name', ['set_dashboard_config', 'write_file', 'set_yaml_config', 'patch_dashboard', 'patch_yaml_config'])
def test_hash_typos_and_conflicting_selectors_are_not_ignored(name):
    assert 'expected_hash' in unknown_argument_error(name, {'expected_hahs': 'secret'})
    assert 'expected_hash' in unknown_argument_error(name, {'expected_hash': 'valid', 'expected_hahs': 'secret'})
    assert unknown_argument_error(name, {'expected_hash': 'valid', 'unrelated': 'extra'}) is None


def test_nested_dashboard_and_retired_forms():
    assert 'target.url_path' in unknown_argument_error('patch_dashboard', {'dashboard_url': 'x'})
    _, error = normalize_tool_args('patch_dashboard', {'url_path': 'x'})
    assert 'Catalog v2' in error and 'target.url_path' in error
    assert unknown_argument_error('set_dashboard_config', {'config': {'dashboard_url': 'x'}}) is None
    assert unknown_argument_error('set_device', {'changes': {'unrelated': 'x'}}) is None


def test_error_bounds_and_nested_parameter_names():
    error = unknown_argument_error('patch_dashboard', {'target': {'urlpath': 'x', 'file_path': 'x'}})
    assert 'target.url_path' in error and 'target.path' in error
    assert len(error) < 2000
    assert unknown_argument_error('get_dashboard_config', {'x' * 10000: 'secret'}) is None
    assert unknown_argument_error('get_dashboard_config', {3: 'secret'}) is None


@pytest.mark.parametrize('domain,service,cap', [
    ('homeassistant', 'restart', 'cap_restart'),
    ('lock', 'unlock', 'cap_physical_control'),
    ('automation', 'reload', 'cap_yaml_edit'),
])
@pytest.mark.parametrize('mode', ['allow', 'confirm', 'deny'])
async def test_service_argument_errors_preserve_capability_priority(hass, domain, service, cap, mode):
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        result, outcome, _ = await _call_tool('call_service', {
            'service': {'domain': domain, 'name': service}, 'tragets': [],
        }, token(**{cap: mode}), hass, MagicMock())
    assert outcome == ('denied' if mode == 'deny' else 'invalid_request')
    assert ('Valid parameters' in result['content'][0]['text']) == (mode != 'deny')
    call.assert_not_called()


async def test_service_typo_cannot_actuate_domain_fallback(hass):
    hass.states.async_set('light.one', 'off')
    hass.states.async_set('light.two', 'off')
    data = MagicMock()
    with patch.object(type(hass.services), 'async_call', new_callable=AsyncMock) as call:
        result, outcome, _ = await _call_tool('call_service', {
            'service': {'domain': 'light', 'name': 'turn_on'}, 'target': [{'kind': 'entity', 'ids': ['light.one']}],
        }, token(), hass, data)
    assert outcome == 'invalid_request' and 'targets' in result['content'][0]['text']
    call.assert_not_called()
    assert not data.mock_calls


async def test_yaml_typo_leaves_default_file_unchanged(hass):
    path = Path(hass.config.path('configuration.yaml'))
    path.write_text('default_config: {}\n')
    _, outcome, _ = await _call_tool('set_yaml_config', {
        'filename': 'other.yaml', 'content': 'changed: true',
    }, token(cap_yaml_edit='allow'), hass, MagicMock())
    assert outcome == 'invalid_request'
    assert path.read_text() == 'default_config: {}\n'


async def test_intentional_default_and_unrelated_extra_still_reach_read(hass):
    with patch('custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config', new_callable=AsyncMock, return_value={'views': []}) as read:
        _, outcome, _ = await _call_tool('get_dashboard_config', {'unrelated': True}, token(cap_lovelace_write='allow'), hass, MagicMock())
    assert outcome == 'allowed'
    read.assert_awaited_once_with(hass, None)


@pytest.mark.parametrize('extra', [{}, {'unrelated': True}])
async def test_service_omission_refuses_implicit_domain_fanout(hass, extra):
    from homeassistant.helpers import entity_registry as er
    from custom_components.phoenix_mcp.data import PhoenixData

    entry = er.async_get(hass).async_get_or_create('light', 'demo', 'one')
    hass.states.async_set(entry.entity_id, 'off')
    calls = []

    async def record(call):
        calls.append(call.data)

    hass.services.async_register('light', 'turn_on', record)
    store = MagicMock()
    store.get_settings.return_value = MagicMock(mesa_mode='off')
    data = PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=None)
    _, outcome, _ = await _call_tool('call_service', {
        'service': {'domain': 'light', 'name': 'turn_on'}, **extra,
    }, token(pass_through=True), hass, data)
    assert outcome == 'invalid_request'
    assert calls == []


async def test_toggle_target_typo_still_honors_physical_denial(hass):
    from homeassistant.helpers import entity_registry as er

    entry = er.async_get(hass).async_get_or_create('cover', 'demo', 'one')
    hass.states.async_set(entry.entity_id, 'closed')
    caller = token(cap_physical_control='deny')
    caller.permissions.domains['cover'] = PermissionNode(state='GREEN')
    result, outcome, _ = await _call_tool('call_service', {
        'service': {'domain': 'homeassistant', 'name': 'toggle'},
        'targets': [{'kind': 'entity', 'ids': [entry.entity_id]}],
        'target': [{'kind': 'entity', 'ids': [entry.entity_id]}],
    }, caller, hass, MagicMock())
    assert outcome == 'denied'
    assert 'Valid parameters' not in result['content'][0]['text']


async def test_approved_executor_accepts_normalized_patch_shape(hass):
    from custom_components.phoenix_mcp.data import PhoenixData
    from custom_components.phoenix_mcp.mcp_view import _EXECUTOR_REGISTRY
    from custom_components.phoenix_mcp.version_store import VersionStore

    data = PhoenixData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=VersionStore())
    with (
        patch('custom_components.phoenix_mcp.tools.lovelace.async_get_lovelace_config', new_callable=AsyncMock, return_value={'views': []}),
        patch('custom_components.phoenix_mcp.tools.lovelace.async_save_lovelace_config', new_callable=AsyncMock) as save,
        patch('custom_components.phoenix_mcp.tool_contracts.unknown_argument_error', side_effect=AssertionError('public-only check')),
    ):
        _, outcome, _ = await _EXECUTOR_REGISTRY['patch_dashboard']({
            'url_path': 'test', 'path': ['views'], 'op': 'set', 'value': [{'title': 'Changed'}],
        }, token(cap_lovelace_write='allow'), hass, data)
    assert outcome == 'allowed'
    save.assert_awaited_once_with(hass, 'test', {'views': [{'title': 'Changed'}]})


@pytest.mark.parametrize("name", ["keys", "kye", "ke", "Key"])
def test_short_yaml_scope_selector_typos_are_caught(name):
    assert "key" in unknown_argument_error("get_yaml_config", {name: "private"})


def test_diagnostics_escape_names_and_cap_findings():
    error = unknown_argument_error("get_dashboard_config", {"url_\npath": "private"})
    assert "\n" not in error and "private" not in error
    mistakes = {"url_" + "_" * n + "path": "private" for n in range(1, 40)}
    error = unknown_argument_error("get_dashboard_config", mistakes)
    assert error.count("Unknown argument") == 8
    assert len(error) < 2000
