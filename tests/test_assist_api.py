"""Tests for the Assist bridge (assist_api.py).

Covers registration lifecycle, per-turn instance building (scoping mirrors
tools/list gating), tool dispatch through _dispatch_mcp with the "assist"
sentinel, confirm-gate degradation to a queued reply, and the unbound/killed
degradations.
"""
from __future__ import annotations

import hashlib
import ast
import secrets
import uuid
from copy import deepcopy
from datetime import timedelta
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jsonschema import Draft202012Validator
from tests.runtime_fixtures import assist_result_parts
from custom_components.phoenix_mcp.schema_compat import schema_engine

vol = schema_engine()

Invalid = vol.Invalid
if vol.Schema.__module__.startswith("probatio."):
    from probatio import to_openapi as _DEFAULT_RENDERING

    # HA 2026.9 providers rendered OpenAPI 3.0; 2026.10 providers pass
    # openapi_version="3.1.0". Phoenix's serializer hook must win in both.
    _PROVIDER_RENDERINGS = {
        "openapi-3.0": _DEFAULT_RENDERING,
        "openapi-3.1": partial(_DEFAULT_RENDERING, openapi_version="3.1.0"),
    }
else:
    from voluptuous_openapi import convert as _DEFAULT_RENDERING

    _PROVIDER_RENDERINGS = {"voluptuous-openapi": _DEFAULT_RENDERING}

from custom_components.phoenix_mcp import assist_api
from custom_components.phoenix_mcp.assist_gemini import gemini_schema
from custom_components.phoenix_mcp.assist_api import (
    PhoenixAssistAPI,
    PhoenixAssistTool,
    async_probe_assist_api,
    assist_api_supported,
    async_register_assist_api,
    async_unregister_assist_api,
)
from custom_components.phoenix_mcp.const import ASSIST_CLIENT_IP, DOMAIN, ACCESS_KEY_PREFIX
from custom_components.phoenix_mcp.access_key_store import GlobalSettings, PermissionTree, AccessKeyRecord


@pytest.fixture(params=list(_PROVIDER_RENDERINGS))
def to_openapi(request):
    """Render a tool schema the way each installed HA provider generation does."""
    return _PROVIDER_RENDERINGS[request.param]


def _make_access_key(*, announce_all: bool = False, cap_config_read: str = "allow") -> AccessKeyRecord:
    from homeassistant.util.dt import utcnow

    raw = ACCESS_KEY_PREFIX + secrets.token_hex(32)
    return AccessKeyRecord(
        id=str(uuid.uuid4()),
        name="voice-access-key",
        access_key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_at=utcnow(),
        created_by="admin",
        cap_config_read=cap_config_read,
        announce_all_tools=announce_all,
        permissions=PermissionTree(),
    )


def _make_data(access_key: AccessKeyRecord | None, *, kill_switch: bool = False, bound_id=None) -> SimpleNamespace:
    settings = GlobalSettings(kill_switch=kill_switch, assist_bound_access_key_id=bound_id)
    store = MagicMock()
    store.get_settings.return_value = settings
    store.get_access_key_by_id.side_effect = lambda tid: access_key if (access_key and tid == access_key.id) else None
    return SimpleNamespace(
        store=store,
        mesa=None,
        ready=True,
        shutting_down=False,
        assist_api_unregister=None,
    )


# --------------------------------------------------------------------------- #
# Support / registration lifecycle
# --------------------------------------------------------------------------- #

def test_assist_api_supported_in_test_env():
    # The installed HA ships helpers.llm and the Probatio converter.
    assert assist_api_supported() is True


@pytest.mark.asyncio
async def test_schema_converter_probe_runs_off_the_event_loop(hass):
    executor = MagicMock()

    async def _run_in_executor(func, *args):
        executor()
        return func(*args)

    with patch.object(hass, "async_add_executor_job", side_effect=_run_in_executor):
        assert await async_probe_assist_api(hass) is True
    executor.assert_called_once_with()


def test_schema_converter_matches_installed_probatio_engine(monkeypatch):
    probatio = pytest.importorskip("probatio")
    assist_api._schema_converter.cache_clear()
    engine = MagicMock(return_value=probatio)
    monkeypatch.setattr(assist_api, "schema_engine", engine)
    assert assist_api._schema_converter() is probatio.from_openapi
    engine.assert_called_once_with()
    assist_api._schema_converter.cache_clear()


def test_schema_converter_uses_lossless_wrapper_on_original_engine(monkeypatch):
    from custom_components.phoenix_mcp.assist_schema import legacy_schema

    assist_api._schema_converter.cache_clear()
    import voluptuous as legacy_vol
    monkeypatch.setattr(assist_api, "schema_engine", lambda: legacy_vol)
    assert assist_api._schema_converter() is legacy_schema
    assist_api._schema_converter.cache_clear()


@pytest.mark.parametrize(
    ("schema", "accepted", "refused"),
    [
        ({"type": "integer", "minimum": 1, "maximum": 3},
         [1, 2, 3, 2.0], [True, False, 0, 4, 1.5, "2", None]),
        ({"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 3},
         [1, 1.5], [True, False, 0, 3, -1, "1", None]),
        ({"anyOf": [{"type": "string", "enum": ["up", "down"]},
                    {"type": "integer", "minimum": -2, "maximum": 2}]},
         ["up", "down", -2, 2], ["sideways", True, 2.5, 3, None, {}]),
        ({"oneOf": [{"type": "integer"}, {"type": "number"}]},
         [1.5], [1, 1.0, True, "1", None]),
        ({"type": "object", "additionalProperties": False,
          "properties": {"rows": {"type": "array", "minItems": 1, "maxItems": 2,
                                  "items": {"type": "object", "additionalProperties": False,
                                            "properties": {"value": {"type": "integer", "minimum": 0}},
                                            "required": ["value"]}}}, "required": ["rows"]},
         [{"rows": [{"value": 0}]}, {"rows": [{"value": 1}, {"value": 2}]}],
         [{}, {"rows": []}, {"rows": [{"value": 1.5}]}, {"rows": [{}]},
          {"rows": [{"value": 0, "extra": 1}]}, {"rows": [{"value": 0}], "extra": 1},
          {"rows": [{"value": 0}] * 3}]),
        ({"type": "object", "properties": {"name": {"type": ["string", "null"]}},
          "additionalProperties": {"type": ["string", "null"]}},
         [{}, {"name": None}, {"name": "x", "other": None}],
         [{"name": 1}, {"other": True}, None]),
        ({"type": "object", "properties": {"name": {"type": "string"},
                                            "area": {"type": "string"},
                                            "value": {"type": "integer"}},
          "allOf": [{"anyOf": [{"required": ["name"]}, {"required": ["area"]}]},
                    {"required": ["value"]}]},
         [{"name": "x", "value": 1}, {"area": "x", "value": 1}],
         [{"name": "x"}, {"value": 1}, {}]),
    ],
    ids=["integer-bounds", "exclusive-bounds", "union-enum", "exact-one-union",
         "nested-array", "null-and-typed-extra", "required-composition"],
)
def test_installed_assist_converter_preserves_json_schema_validation(schema, accepted, refused):
    """Compare real HA validation with an independent Draft 2020-12 oracle."""
    Draft202012Validator.check_schema(schema)
    oracle = Draft202012Validator(schema)
    parameters = assist_api._convert_to_voluptuous(deepcopy(schema))
    for value in accepted:
        assert oracle.is_valid(value), value
        parameters(deepcopy(value))
    for value in refused:
        assert not oracle.is_valid(value), value
        with pytest.raises(Invalid):
            parameters(deepcopy(value))


@pytest.mark.asyncio
async def test_assist_provider_schemas_preserve_complete_catalog(hass, hass_admin_user, to_openapi):
    """Exercise HA's serializer hook for the whole native, Phoenix and MESA catalog."""
    from custom_components.phoenix_mcp.agentcli import build_mcp_tool_list

    access_key = _make_access_key(announce_all=True)
    data = _make_data(access_key, bound_id=access_key.id)
    data.mesa = MagicMock()
    definitions = {d["name"]: deepcopy(d["inputSchema"]) for d in build_mcp_tool_list(access_key, data)}
    context = _ctx()
    context.context.user_id = hass_admin_user.id
    instance = await PhoenixAssistAPI(hass, data).async_get_api_instance(context)
    assert {tool.name for tool in instance.tools} == set(definitions)
    assert instance.custom_serializer is not None
    for tool in instance.tools:
        public = to_openapi(tool.parameters, custom_serializer=instance.custom_serializer)
        Draft202012Validator.check_schema(public)
        assert public == definitions[tool.name], tool.name


@pytest.mark.asyncio
async def test_assist_serializer_preserves_extensions_and_isolates_mutation(hass, to_openapi):
    schema = {"type": "object", "properties": {"value": {"type": ["integer", "null"],
              "exclusiveMinimum": 0, "description": "A positive integer or null."}},
              "x-provider": {"type": "example", "notes": "Retained extension."}, "required": ["value"]}
    original = deepcopy(schema)
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    definition = {"name": "fixture", "description": "Schema fixture.", "inputSchema": schema}
    with patch("custom_components.phoenix_mcp.agentcli.build_mcp_tool_list", return_value=[definition]):
        instance = await PhoenixAssistAPI(hass, data).async_get_api_instance(_ctx())
    schema["properties"].clear()
    tool = instance.tools[0]
    public = to_openapi(tool.parameters, custom_serializer=instance.custom_serializer)
    Draft202012Validator.check_schema(public)
    assert public == original
    public["properties"].clear()
    assert to_openapi(tool.parameters, custom_serializer=instance.custom_serializer) == original


@pytest.mark.asyncio
async def test_assist_serializer_survives_ha_merged_api_and_native_selectors(hass, to_openapi):
    from homeassistant.helpers import llm, selector

    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    api = PhoenixAssistAPI(hass, data)
    instance = await llm.MergedAPI([api]).async_get_api_instance(_ctx())
    for tool in instance.tools:
        public = to_openapi(tool.parameters, custom_serializer=instance.custom_serializer)
        Draft202012Validator.check_schema(public)
    native = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=3))
    assert to_openapi(native, custom_serializer=instance.custom_serializer) == {
        "type": "number", "minimum": 1, "maximum": 3,
    }


@pytest.mark.asyncio
async def test_assist_schema_snapshots_are_isolated_between_turns(hass, to_openapi):
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    first_schema = {"type": "object", "properties": {"value": {"type": "integer", "maximum": 3}}}
    second_schema = {"type": "object", "properties": {"value": {"type": "integer", "maximum": 1}}}
    with patch("custom_components.phoenix_mcp.agentcli.build_mcp_tool_list", return_value=[
        {"name": "fixture", "inputSchema": first_schema},
    ]):
        first = await PhoenixAssistAPI(hass, data).async_get_api_instance(_ctx())
    with patch("custom_components.phoenix_mcp.agentcli.build_mcp_tool_list", return_value=[
        {"name": "fixture", "inputSchema": second_schema},
    ]):
        second = await PhoenixAssistAPI(hass, data).async_get_api_instance(_ctx())
    for instance, expected in [(first, first_schema), (second, second_schema)]:
        assert to_openapi(instance.tools[0].parameters, custom_serializer=instance.custom_serializer) == expected


@pytest.mark.asyncio
async def test_assist_safely_omits_schemas_incompatible_with_installed_gemini_formatter(hass, caplog):
    """Run HA's pure formatter without requiring the optional Google SDK."""
    from homeassistant.helpers import llm
    from custom_components.phoenix_mcp.agentcli import build_mcp_tool_list

    source = Path(llm.__file__).parents[1] / "components/google_generative_ai_conversation/entity.py"
    tree = ast.parse(source.read_text())
    nodes = [node for node in tree.body if (
        isinstance(node, ast.FunctionDef) and node.name in {"_camel_to_snake", "_format_schema"}
    ) or (
        isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SUPPORTED_SCHEMA_KEYS"
                                           for t in node.targets)
    )]
    assert len(nodes) == 3
    namespace = {"Schema": dict, "Any": Any, "cast": cast}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    format_schema = namespace["_format_schema"]
    # Render as the installed formatter does: its own call names any OpenAPI version.
    format_tool = next(node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == "_format_tool")
    (render_call,) = [node for node in ast.walk(format_tool) if isinstance(node, ast.Call) and getattr(
        node.func, "attr", getattr(node.func, "id", None)) in {"to_openapi", "convert"}]
    version = next((ast.literal_eval(keyword.value) for keyword in render_call.keywords
                    if keyword.arg == "openapi_version"), None)
    to_openapi = _DEFAULT_RENDERING if version is None else partial(_DEFAULT_RENDERING, openapi_version=version)
    with pytest.raises(AttributeError, match="upper"):
        format_schema({"type": ["string", "null"]})
    assert format_schema({"anyOf": [{"type": "string"}, {"type": "integer"}]}) == {}

    access_key = _make_access_key(announce_all=True)
    data = _make_data(access_key, bound_id=access_key.id)
    data.mesa = MagicMock()
    definitions = {d["name"]: deepcopy(d["inputSchema"]) for d in build_mcp_tool_list(access_key, data)}
    context = _ctx()
    context.platform = "google_generative_ai_conversation"
    instance = await PhoenixAssistAPI(hass, data).async_get_api_instance(context)
    tools = {tool.name: tool for tool in instance.tools}
    assert {"get_capability_summary", "set_entity", "get_state", "intent__HassTurnOn",
            "media_player__HassSetVolumeRelative", "call_service"} <= set(tools)
    baseline = {tool.name for tool in (await PhoenixAssistAPI(hass, data).async_get_api_instance(_ctx())).tools}
    assert baseline - set(tools) == {"patch_dashboard", "set_zigbee_device_property"}
    assert "skipping tool patch_dashboard" in caplog.text
    for name, tool in tools.items():
        public = to_openapi(tool.parameters, custom_serializer=instance.custom_serializer)
        assert public == gemini_schema(definitions[name])[0]
        Draft202012Validator.check_schema(public)
        formatted = format_schema(public)
        # HA's own {"json"} fallback never fires: every open object arrives pre-shaped.
        pending = list((formatted.get("properties") or {}).values())
        while pending:
            node = pending.pop()
            assert node.get("type") != "OBJECT" or node.get("properties"), name
            pending.extend((node.get("properties") or {}).values())
            if isinstance(node.get("items"), dict):
                pending.append(node["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["unevaluatedProperties", "$dynamicRef"])
async def test_assist_omits_real_incompatible_schema_without_breaking_other_tools(hass, caplog, keyword):
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    unsupported = {"type": "object", "properties": {"nested": {"type": "object",
                   keyword: False if keyword == "unevaluatedProperties" else "#anchor"}}}
    definitions = [{"name": "unsupported", "inputSchema": unsupported},
                   {"name": "supported", "inputSchema": {"type": "object"}}]
    with patch("custom_components.phoenix_mcp.agentcli.build_mcp_tool_list", return_value=definitions):
        instance = await PhoenixAssistAPI(hass, data).async_get_api_instance(_ctx())
    if keyword == "unevaluatedProperties" and not vol.Schema.__module__.startswith("probatio."):
        assert [tool.name for tool in instance.tools] == ["unsupported", "supported"]
        with pytest.raises(vol.Invalid):
            instance.tools[0].parameters({"nested": {"extra": 1}})
    else:
        assert [tool.name for tool in instance.tools] == ["supported"]
        assert "skipping tool unsupported" in caplog.text

@pytest.mark.asyncio
async def test_register_and_unregister_lifecycle(hass):
    data = _make_data(None)
    async_register_assist_api(hass, data)
    assert data.assist_api_unregister is not None
    first = data.assist_api_unregister

    # Idempotent: a second register while up is a no-op (same callback).
    async_register_assist_api(hass, data)
    assert data.assist_api_unregister is first

    async_unregister_assist_api(data)
    assert data.assist_api_unregister is None
    # Idempotent unregister.
    async_unregister_assist_api(data)
    assert data.assist_api_unregister is None


# --------------------------------------------------------------------------- #
# Instance building
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unbound_instance_has_no_tools_and_never_raises(hass):
    data = _make_data(_make_access_key(), bound_id=None)
    api = PhoenixAssistAPI(hass, data)
    instance = await api.async_get_api_instance(_ctx())
    assert instance.tools == []
    assert "bind an access key" in instance.api_prompt.lower()


@pytest.mark.asyncio
async def test_kill_switch_yields_no_tools(hass):
    access_key = _make_access_key()
    data = _make_data(access_key, kill_switch=True, bound_id=access_key.id)
    api = PhoenixAssistAPI(hass, data)
    instance = await api.async_get_api_instance(_ctx())
    assert instance.tools == []


@pytest.mark.asyncio
async def test_bound_instance_tools_mirror_tools_list_gating(hass, hass_admin_user):
    from custom_components.phoenix_mcp.agentcli import build_mcp_tool_list

    access_key = _make_access_key(cap_config_read="allow")
    data = _make_data(access_key, bound_id=access_key.id)
    api = PhoenixAssistAPI(hass, data)
    instance = await api.async_get_api_instance(_ctx(hass_admin_user.id))

    expected = {d["name"] for d in build_mcp_tool_list(access_key, data)}
    assert {t.name for t in instance.tools} == expected
    assert expected  # non-empty for a read-capable access key


@pytest.mark.asyncio
async def test_announce_all_widens_the_tool_set(hass):
    scoped = _make_access_key(announce_all=False)
    wide = _make_access_key(announce_all=True)
    d_scoped = _make_data(scoped, bound_id=scoped.id)
    d_wide = _make_data(wide, bound_id=wide.id)

    scoped_tools = {t.name for t in (await PhoenixAssistAPI(hass, d_scoped).async_get_api_instance(_ctx())).tools}
    wide_tools = {t.name for t in (await PhoenixAssistAPI(hass, d_wide).async_get_api_instance(_ctx())).tools}
    assert scoped_tools < wide_tools


@pytest.mark.asyncio
async def test_one_unconvertible_schema_is_skipped_not_fatal(hass, hass_admin_user):
    from custom_components.phoenix_mcp.agentcli import build_mcp_tool_list

    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    names = [d["name"] for d in build_mcp_tool_list(access_key, data)]
    victim = names[0]
    real = assist_api._convert_to_voluptuous

    def flaky(schema):
        # Fail for the victim tool's schema only; convert the rest normally.
        if isinstance(schema, dict) and schema.get("__victim__"):
            raise ValueError("boom")
        return real(schema)

    def tagged_list(tok, dat):
        out = build_mcp_tool_list(tok, dat)
        for d in out:
            if d["name"] == victim:
                d["inputSchema"] = {"__victim__": True}
        return out

    with patch.object(assist_api, "_convert_to_voluptuous", side_effect=flaky), \
         patch("custom_components.phoenix_mcp.agentcli.build_mcp_tool_list", side_effect=tagged_list):
        instance = await PhoenixAssistAPI(hass, data).async_get_api_instance(_ctx(hass_admin_user.id))

    got = {t.name for t in instance.tools}
    assert victim not in got
    assert got == set(names) - {victim}


# --------------------------------------------------------------------------- #
# Tool dispatch
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tool_call_dispatches_with_assist_sentinel(hass):
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    hass.data[DOMAIN] = data

    captured = {}

    async def fake_dispatch(method, msg_id, params, tok, h, d, client_ip, base_url, *a, **k):
        captured["client_ip"] = client_ip
        captured["name"] = params["name"]
        return ({"result": {"content": [{"type": "text", "text": '{"state": "on"}'}]}}, "get_state", "get_state", "allowed")

    tool = PhoenixAssistTool("get_state", "d", MagicMock(), access_key.id)
    with patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=fake_dispatch):
        result = await tool.async_call(hass, _tool_input({"entity_id": "light.x"}), _ctx())

    assert captured["client_ip"] == ASSIST_CLIENT_IP
    assert captured["name"] == "get_state"
    body, error = assist_result_parts(result)
    assert body == {"state": "on"}
    assert not error


@pytest.mark.asyncio
async def test_confirm_gated_call_returns_queued(hass):
    from homeassistant.util.dt import utcnow

    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    hass.data[DOMAIN] = data

    pending = {
        "result": {
            "content": [{"type": "text", "text": '{"status": "pending_approval", "approval_id": "ap-1"}'}],
        }
    }
    record = SimpleNamespace(
        id="ap-1", status="pending", expires_at=utcnow() + timedelta(minutes=5),
        access_key_id=access_key.id, tool_name="call_service", request_id="",
    )

    async def fake_dispatch(*a, **k):
        record.request_id = k["request_id"]
        return (pending, "call_service", "call_service", "pending_approval")

    tool = PhoenixAssistTool("call_service", "d", MagicMock(), access_key.id)
    with patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=fake_dispatch), \
         patch("custom_components.phoenix_mcp.agentcli.get_approval", return_value=record):
        result = await tool.async_call(hass, _tool_input({}), _ctx())

    body, error = assist_result_parts(result)
    assert body["status"] == "queued_for_approval"
    assert body["approval_id"] == "ap-1"
    # Queued is not a failure: the provider must not tell the model the call failed.
    assert not error


@pytest.mark.asyncio
async def test_tool_call_on_revoked_access_key_returns_error(hass):
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    data.store.get_access_key_by_id.side_effect = lambda tid: None  # gone since instance built
    hass.data[DOMAIN] = data

    tool = PhoenixAssistTool("get_state", "d", MagicMock(), access_key.id)
    body, error = assist_result_parts(await tool.async_call(hass, _tool_input({}), _ctx()))
    assert "error" in body
    assert error is not False


@pytest.mark.asyncio
@pytest.mark.parametrize("rebound_to", ["other-key", None])
async def test_retained_tool_refuses_after_rebind_or_unbind(hass, rebound_to):
    """A tool built under the old binding must not keep dispatching as that key.

    The key itself stays valid, so only the binding comparison refuses it.
    """
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=rebound_to)
    hass.data[DOMAIN] = data

    dispatch = AsyncMock()
    tool = PhoenixAssistTool("get_state", "d", MagicMock(), access_key.id)
    with patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", dispatch):
        result = await tool.async_call(hass, _tool_input({"entity_id": "light.x"}), _ctx())

    body, error = assist_result_parts(result)
    assert "binding changed" in body["error"]
    assert error is not False
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_error_result_is_surfaced_not_raised(hass):
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    hass.data[DOMAIN] = data

    async def fake_dispatch(*a, **k):
        return ({"result": {"content": [{"type": "text", "text": "Forbidden."}], "isError": True}}, "x", "x", "denied")

    tool = PhoenixAssistTool("get_state", "d", MagicMock(), access_key.id)
    with patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=fake_dispatch):
        result = await tool.async_call(hass, _tool_input({}), _ctx())
    body, error = assist_result_parts(result)
    assert body == {"error": "Forbidden."}
    assert error is not False


@pytest.mark.asyncio
async def test_assist_tools_carry_phoenix_hints_as_ha_annotations(hass, hass_admin_user):
    """HA 2026.10 forwards annotations through its own MCP server; an omitted one reads as destructive."""
    from homeassistant.helpers import llm
    from custom_components.phoenix_mcp.agentcli import build_mcp_tool_list

    if not hasattr(llm, "ToolAnnotations"):
        pytest.skip("Home Assistant before 2026.10 has no tool annotations")
    access_key = _make_access_key(announce_all=True)
    data = _make_data(access_key, bound_id=access_key.id)
    data.mesa = MagicMock()
    hints = {d["name"]: d["annotations"] for d in build_mcp_tool_list(access_key, data)}
    context = _ctx()
    context.context.user_id = hass_admin_user.id
    instance = await PhoenixAssistAPI(hass, data).async_get_api_instance(context)
    assert len(instance.tools) == len(hints)
    for tool in instance.tools:
        expected = hints[tool.name]
        assert tool.annotations == llm.ToolAnnotations(
            read_only=expected["readOnlyHint"],
            destructive=expected["destructiveHint"],
            idempotent=expected["idempotentHint"],
            open_world=expected["openWorldHint"],
        ), tool.name
    by_name = {tool.name: tool.annotations for tool in instance.tools}
    assert by_name["get_state"].read_only and not by_name["get_state"].destructive
    assert not by_name["call_service"].read_only and by_name["call_service"].destructive


def test_missing_or_malformed_hints_keep_ha_least_safe_defaults():
    from homeassistant.helpers import llm

    if not hasattr(llm, "ToolAnnotations"):
        pytest.skip("Home Assistant before 2026.10 has no tool annotations")
    assert assist_api._tool_annotations({"readOnlyHint": True, "destructiveHint": "no"}) == llm.ToolAnnotations(
        read_only=True,
    )
    assert assist_api._tool_annotations(None) == llm.ToolAnnotations()


@pytest.mark.asyncio
async def test_older_home_assistant_keeps_plain_results_and_no_annotations(hass, monkeypatch):
    from homeassistant.helpers import llm

    monkeypatch.delattr(llm, "ToolAnnotations", raising=False)
    monkeypatch.delattr(llm, "ToolResult", raising=False)
    access_key = _make_access_key()
    data = _make_data(access_key, bound_id=access_key.id)
    hass.data[DOMAIN] = data
    tool = PhoenixAssistTool("get_state", "d", MagicMock(), access_key.id,
                             annotations={"readOnlyHint": True, "destructiveHint": False,
                                          "idempotentHint": True, "openWorldHint": False})
    assert "annotations" not in vars(tool)

    async def fake_dispatch(*a, **k):
        return ({"result": {"content": [{"type": "text", "text": "Forbidden."}], "isError": True}}, "x", "x", "denied")

    with patch("custom_components.phoenix_mcp.mcp_view._dispatch_mcp", side_effect=fake_dispatch):
        result = await tool.async_call(hass, _tool_input({}), _ctx())
    assert result == {"error": "Forbidden."}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _ctx(user_id=None):
    from homeassistant.core import Context
    return SimpleNamespace(platform="conversation", context=Context(user_id=user_id), language="en", assistant="conversation", device_id=None)


def _tool_input(args):
    return SimpleNamespace(id="ti-1", tool_name="t", tool_args=args, external=False)
