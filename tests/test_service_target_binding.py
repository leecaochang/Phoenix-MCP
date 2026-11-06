"""A service reaches only the targets it acts on, spelled the way Home Assistant lists it.

Rules run against the real services.yaml descriptions and real registration helpers,
so a change in how Home Assistant declares a target shows up here.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import service as ha_service
from homeassistant.helpers.service import async_get_all_descriptions, async_set_service_schema
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.approval_targets import bind_targets
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.household_authority import household_service_kwargs
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool
from custom_components.phoenix_mcp.service_targets import unbound_service_targets
from custom_components.phoenix_mcp.tool_contracts import service_name_error


@pytest.fixture(autouse=True)
def _real_descriptions_loaded(caplog):
    """A run on the own-domain fallback would prove nothing about the filters read here."""
    yield
    assert "service descriptions could not be read" not in caplog.text


def _environment(hass) -> tuple[PhoenixData, list]:
    pending: list = []
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=None)
    hass.data[DOMAIN] = data
    return data, pending


def _key(entities: dict[str, str], **fields) -> AccessKeyRecord:
    return AccessKeyRecord(
        id="k", name="K", access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(entities={e: PermissionNode(state=s) for e, s in entities.items()}),
        **fields,
    )


def _entity(hass, domain: str, platform: str = "test", object_id: str = "x", state: str = "off") -> str:
    entity_id = er.async_get(hass).async_get_or_create(
        domain, platform, f"{platform}_{object_id}", suggested_object_id=object_id).entity_id
    hass.states.async_set(entity_id, state)
    return entity_id


def _call(domain: str, name: str, entity_id: str, data: dict | None = None) -> dict:
    service: dict = {"domain": domain, "name": name}
    if data is not None:
        service["data"] = data
    return {"service": service, "targets": [{"kind": "entity", "ids": [entity_id]}]}


async def _call_service(hass, data, key, domain, name, entity_id, service_data=None):
    return await _call_tool("call_service", _call(domain, name, entity_id, service_data), key, hass, data)


# ---- canonical spelling

@pytest.mark.parametrize(("domain", "service"), [
    ("LOCK", "unlock"), ("Lock", "unlock"), ("lock", "UNLOCK"), ("locK", "unlock"),
    ("lock ", "unlock"), ("lock\n", "unlock"), ("lock.x", "unlock"), ("lock", "un-lock"), ("löck", "unlock"),
])
def test_only_the_spelling_home_assistant_lists_is_canonical(domain, service):
    assert service_name_error(domain, service)


@pytest.mark.parametrize(("domain", "service"), [
    ("lock", "unlock"), ("media_player", "play_media"), ("script", "x2_y"), ("", "unlock"), ("lock", ""),
])
def test_canonical_spellings_and_missing_parts_pass(domain, service):
    assert service_name_error(domain, service) is None


@pytest.mark.parametrize(("domain", "name", "entity_domain", "caps"), [
    ("LOCK", "unlock", "lock", {"cap_physical_control": "deny"}),
    ("Lock", "unlock", "lock", {"cap_physical_control": "confirm"}),
    ("locK", "unlock", "lock", {"cap_physical_control": "deny"}),
    ("NOTIFY", "send_message", "notify", {"cap_notify": "deny"}),
    ("MEDIA_PLAYER", "play_media", "media_player", {"cap_broadcast": "deny"}),
    ("CALENDAR", "create_event", "calendar", {"cap_calendar_write": "deny"}),
    ("homeassistant", "TOGGLE", "cover", {"cap_physical_control": "deny"}),
    ("SCRIPT", "wipe", "script", {}),
])
async def test_call_service_refuses_a_case_variant_before_any_effect(hass, domain, name, entity_domain, caps):
    calls = async_mock_service(hass, domain.lower(), name.lower())
    entity_id = _entity(hass, entity_domain)
    data, pending = _environment(hass)
    result, outcome, _ = await _call_service(hass, data, _key({entity_id: "GREEN"}, **caps), domain, name, entity_id)
    await hass.async_block_till_done()
    assert outcome == "invalid_request", result
    assert calls == [] and pending == []


async def test_lowercase_control_still_reaches_its_gate(hass):
    calls = async_mock_service(hass, "lock", "unlock")
    entity_id = _entity(hass, "lock")
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"}, cap_physical_control="deny")
    assert (await _call_service(hass, data, key, "lock", "unlock", entity_id))[1] == "denied"
    key.cap_physical_control = "allow"
    assert (await _call_service(hass, data, key, "lock", "unlock", entity_id))[1] == "allowed"
    assert len(calls) == 1


async def test_dry_run_refuses_a_case_variant(hass):
    async_mock_service(hass, "lock", "unlock")
    entity_id = _entity(hass, "lock")
    data, _ = _environment(hass)
    result, outcome, _ = await _call_tool(
        "dry_run_service", _call("LOCK", "unlock", entity_id), _key({entity_id: "GREEN"}, cap_search="allow"), hass, data)
    assert outcome == "invalid_request", result


async def test_saved_approval_arguments_cannot_carry_a_case_variant(hass):
    calls = async_mock_service(hass, "lock", "unlock")
    entity_id = _entity(hass, "lock")
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"}, cap_physical_control="deny")
    result, outcome, _ = await async_execute_approved_tool(
        "call_service", {"domain": "LOCK", "service": "unlock", "entity_id": [entity_id]}, key, hass, data)
    assert outcome == "invalid_request", result
    assert calls == []


async def test_household_probe_sees_the_service_home_assistant_would_run(hass):
    async def handler(call: ServiceCall) -> None:
        return None

    ha_service.async_register_admin_service(hass, "test_admin", "wipe", handler)
    authority = MagicMock(is_admin=MagicMock(return_value=False), context=None)
    with patch("custom_components.phoenix_mcp.household_authority.household_authority") as current:
        current.get.return_value = authority
        with pytest.raises(Exception) as refused:
            household_service_kwargs(hass, "TEST_ADMIN", "WIPE")
    assert type(refused.value).__name__ == "Unauthorized"


# ---- binding, against Home Assistant's own descriptions

@pytest.mark.parametrize(("domain", "service", "entity_domain"), [
    ("light", "turn_on", "light"),
    ("light", "toggle", "light"),
    ("homeassistant", "turn_on", "switch"),
    ("homeassistant", "turn_off", "fan"),
    ("homeassistant", "toggle", "cover"),
    ("homeassistant", "update_entity", "sensor"),
    ("script", "turn_on", "script"),
    ("script", "turn_off", "script"),
    ("automation", "trigger", "automation"),
    ("automation", "turn_off", "automation"),
    ("scene", "turn_on", "scene"),
    ("notify", "send_message", "notify"),
    ("tts", "speak", "tts"),
    ("media_player", "volume_set", "media_player"),
    ("media_player", "play_media", "media_player"),
    ("number", "set_value", "number"),
    ("select", "select_option", "select"),
    ("input_boolean", "toggle", "input_boolean"),
    ("input_number", "set_value", "input_number"),
    ("input_select", "select_option", "input_select"),
    ("input_text", "set_value", "input_text"),
    ("input_datetime", "set_datetime", "input_datetime"),
    ("climate", "set_temperature", "climate"),
    ("cover", "open_cover", "cover"),
    ("lock", "unlock", "lock"),
    ("vacuum", "start", "vacuum"),
    ("todo", "get_items", "todo"),
    ("calendar", "get_events", "calendar"),
    ("weather", "get_forecasts", "weather"),
    ("logbook", "log", "light"),
    ("recorder", "purge_entities", "sensor"),
])
async def test_real_services_keep_their_real_targets(hass, domain, service, entity_domain):
    async_mock_service(hass, domain, service)
    entity_id = _entity(hass, entity_domain)
    assert await unbound_service_targets(hass, [entity_id], domain=domain, service=service) == ()


@pytest.mark.parametrize("service", ["announce", "ask_question"])
async def test_satellite_services_run_through_the_entity_dispatcher(hass, service):
    ha_service.async_register_entity_service(
        hass, "assist_satellite", service, entities={}, func="async_act", job_type=None, schema=None)
    entity_id = _entity(hass, "assist_satellite")
    assert await unbound_service_targets(hass, [entity_id], domain="assist_satellite", service=service) == ()


@pytest.mark.parametrize(("domain", "service", "entity_domain"), [
    ("light", "turn_on", "switch"),
    ("switch", "turn_on", "light"),
    ("automation", "trigger", "script"),
    ("scene", "turn_on", "light"),
    ("notify", "send_message", "light"),
    ("number", "set_value", "input_number"),
    ("homeassistant", "save_persistent_states", "light"),
    ("persistent_notification", "dismiss_all", "light"),
    ("frontend", "set_theme", "light"),
    ("device_tracker", "see", "light"),
])
async def test_real_services_refuse_a_target_they_do_not_declare(hass, domain, service, entity_domain):
    async_mock_service(hass, domain, service)
    entity_id = _entity(hass, entity_domain)
    assert await unbound_service_targets(hass, [entity_id], domain=domain, service=service) == (entity_id,)


async def test_this_environment_reads_the_real_descriptions(hass):
    """Home Assistant validates feature filters against component constants as it loads them."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "light", {})
    descriptions = await async_get_all_descriptions(hass)
    assert descriptions["light"]["turn_on"]["target"]["entity"][0]["domain"] == ["light"]


async def test_real_integration_filter_binds_to_the_owning_integration(hass):
    async_mock_service(hass, "music_assistant", "play_media")
    ours = _entity(hass, "media_player", "music_assistant", "a")
    other = _entity(hass, "media_player", "cast", "b")
    assert await unbound_service_targets(hass, [ours, other], domain="music_assistant", service="play_media") == (other,)


async def test_entity_id_field_is_a_declared_target_with_its_own_filter(hass):
    """The legacy text-to-speech service declares its media player in a field."""
    async_mock_service(hass, "tts", "say")
    player = _entity(hass, "media_player")
    light = _entity(hass, "light")
    assert await unbound_service_targets(hass, [player, light], domain="tts", service="say") == (light,)


async def test_entity_id_field_filter_form(hass):
    """Newer descriptions nest the same filter under the selector's filter key."""
    async_mock_service(hass, "telegram_bot", "send_message")
    ours = _entity(hass, "notify", "telegram_bot", "a")
    other = _entity(hass, "notify", "smtp", "b")
    assert await unbound_service_targets(hass, [ours, other], domain="telegram_bot", service="send_message") == (other,)


@pytest.mark.parametrize(("target", "platform", "entity_domain", "bound"), [
    ({}, "test", "light", True),
    ({"entity": {}}, "test", "light", True),
    ({"entity": [{}]}, "test", "light", True),
    ({"entity": {"domain": "light"}}, "test", "light", True),
    ({"entity": {"domain": "light"}}, "test", "switch", False),
    ({"entity": {"domain": ["light", "switch"]}}, "test", "switch", True),
    ({"entity": [{"domain": "light"}, {"domain": "switch"}]}, "test", "switch", True),
    ({"entity": {"integration": "custom"}}, "custom", "light", True),
    ({"entity": {"integration": "custom"}}, "other", "light", False),
    ({"entity": {"integration": "custom", "domain": "light"}}, "custom", "switch", False),
    ({"device": {"integration": "custom"}}, "custom", "light", True),
    ({"device": {"integration": "custom"}}, "other", "light", False),
])
async def test_custom_descriptions(hass, target, platform, entity_domain, bound):
    async_mock_service(hass, "custom", "act")
    async_set_service_schema(hass, "custom", "act", {"name": "Act", "target": target})
    entity_id = _entity(hass, entity_domain, platform)
    expected = () if bound else (entity_id,)
    assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == expected


async def test_entity_service_without_a_description_keeps_working(hass):
    ha_service.async_register_entity_service(
        hass, "custom", "act", entities={}, func="async_act", job_type=None, schema=None)
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == ()


async def test_batched_entity_service_without_a_description_keeps_working(hass):
    async def act(entities, call):
        return None

    ha_service.async_register_batched_entity_service(
        hass, "custom", "act", entities={}, func=act, schema=None)
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == ()


async def test_schema_less_service_refuses_a_decoy_even_in_its_own_domain(hass):
    async def handler(call: ServiceCall) -> None:
        return None

    hass.services.async_register("custom", "act", handler)
    hass.services.async_register("light", "custom_effect", handler)
    entity_id = _entity(hass, "light")
    with patch("custom_components.phoenix_mcp.service_targets.async_get_all_descriptions", AsyncMock(return_value={})):
        assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == (entity_id,)
        assert await unbound_service_targets(hass, [entity_id], domain="light", service="custom_effect") == (entity_id,)


async def test_an_unregistered_name_in_the_entity_domain_reaches_home_assistants_hint(hass):
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="light", service="turn_onn") == ()


async def test_a_named_script_service_keeps_its_own_domain(hass):
    async def handler(call: ServiceCall) -> None:
        return None

    hass.services.async_register("script", "wake", handler)
    entity_id = _entity(hass, "script")
    assert await unbound_service_targets(hass, [entity_id], domain="script", service="wake") == ()


@pytest.mark.parametrize("schema", [
    pytest.param(vol.Schema({vol.Required("entity_id"): cv.entity_ids}), id="required"),
    pytest.param(vol.Schema({vol.Optional("entity_id"): cv.entity_id, "speed": int}), id="optional"),
    pytest.param(vol.All(vol.Schema({vol.Required("entity_id"): cv.entity_ids}), dict), id="all"),
    pytest.param(vol.Schema(vol.All(vol.Schema({"entity_id": cv.entity_ids}))), id="nested"),
])
async def test_a_registered_entity_id_schema_declares_a_target(hass, schema):
    async def handler(call: ServiceCall) -> None:
        return None

    hass.services.async_register("custom", "act", handler, schema=schema)
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == ()


@pytest.mark.parametrize("schema", [
    pytest.param(vol.Schema({}, extra=vol.ALLOW_EXTRA), id="tolerates-anything"),
    pytest.param(vol.Schema({vol.Required("message"): str}, extra=vol.ALLOW_EXTRA), id="other-field"),
    pytest.param(vol.Schema(dict), id="any-mapping"),
])
async def test_a_schema_that_only_tolerates_entity_id_declares_nothing(hass, schema):
    async def handler(call: ServiceCall) -> None:
        return None

    hass.services.async_register("custom", "act", handler, schema=schema)
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == (entity_id,)


@pytest.mark.parametrize("schema", [
    pytest.param(vol.All({vol.Required("entity_id"): cv.entity_ids}), id="all over a dict"),
    pytest.param(vol.Schema(vol.All({vol.Required("entity_id"): cv.entity_ids})), id="schema of all"),
    pytest.param(vol.All(cv.deprecated("x"), {vol.Required("entity_id"): cv.entity_ids, vol.Optional("x"): str}),
                 id="deprecated"),
])
async def test_a_field_map_inside_a_validator_chain_declares_a_target(hass, schema):
    """The form zwave_js writes: vol.All keeps a plain dict among its validators."""
    async def handler(call: ServiceCall) -> None:
        return None

    hass.services.async_register("custom", "act", handler, schema=schema)
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == ()


async def test_a_field_map_without_entity_id_declares_nothing(hass):
    async def handler(call: ServiceCall) -> None:
        return None

    hass.services.async_register("custom", "act", handler, schema=vol.All({vol.Required("x"): str}))
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="custom", service="act") == (entity_id,)


async def _script_with_input(hass, selector: dict) -> tuple[list, str]:
    """A script run by name, described by its fields as HA's script integration describes it."""
    ran: list = []

    async def handler(call: ServiceCall) -> None:
        ran.append(dict(call.data))

    hass.services.async_register("script", "fields", handler)
    async_set_service_schema(hass, "script", "fields", {
        "name": "x", "fields": {"entity_id": {"selector": selector}},
    })
    entity_id = er.async_get(hass).async_get_or_create(
        "script", "script", "fields", suggested_object_id="fields").entity_id
    hass.states.async_set(entity_id, "off")
    return ran, entity_id


@pytest.mark.parametrize("selector", [
    pytest.param({"entity": {"domain": "light"}}, id="filtered"),
    pytest.param({"entity": {}}, id="unfiltered"),
    pytest.param({"text": None}, id="text"),
])
async def test_a_script_input_named_entity_id_is_explained_not_misread(hass, selector):
    """Its field declares an input, not a target: the refusal says how to run it."""
    assert await async_setup_component(hass, "homeassistant", {})
    ran, script = await _script_with_input(hass, selector)
    assert await unbound_service_targets(hass, [script], domain="script", service="fields") == ()
    data, _ = _environment(hass)
    key = _key({script: "GREEN"}, cap_search="allow")
    result, outcome, _ = await _call_tool("call_service", _call("script", "fields", script), key, hass, data)
    assert outcome == "invalid_request"
    assert "script.turn_on" in result["content"][0]["text"]
    assert "does not act on" not in result["content"][0]["text"]
    assert ran == []
    preview = await _call_tool("dry_run_service", _call("script", "fields", script), key, hass, data)
    assert preview[1] == "invalid_request"


async def test_a_saved_approval_cannot_run_a_script_input_named_entity_id(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    ran, script = await _script_with_input(hass, {"entity": {}})
    data, _ = _environment(hass)
    saved = bind_targets({"domain": "script", "service": "fields", "entity_id": [script]}, [script], hass)
    result = await async_execute_approved_tool("call_service", saved, _key({script: "GREEN"}), hass, data)
    assert result[1] == "invalid_request"
    assert "script.turn_on" in result[0]["content"][0]["text"]
    assert ran == []


async def test_a_script_with_an_entity_id_input_stays_listed_with_the_reason(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    _, script = await _script_with_input(hass, {"entity": {"domain": "light"}})
    data, _ = _environment(hass)
    result, _, _ = await _call_tool("find_available_actions", {"entity_id": script}, _key({script: "GREEN"}, cap_search="allow"), hass, data)
    row = next(item for item in json.loads(result["content"][0]["text"])["actions"]
               if item["service"] == "script.fields")
    assert row["available"] is False and "script.turn_on" in row["reason"]


async def _listing(hass, entity_id: str, **caps) -> dict[str, dict]:
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"}, cap_search="allow", **caps)
    result, _, _ = await _call_tool("find_available_actions", {"entity_id": entity_id}, key, hass, data)
    return {row["service"]: row for row in json.loads(result["content"][0]["text"])["actions"]}


async def test_the_listing_leaves_out_a_service_the_binding_refuses(hass):
    async def handler(call: ServiceCall) -> None:
        return None

    assert await async_setup_component(hass, "light", {})
    hass.services.async_register("light", "custom_effect", handler)
    entity_id = _entity(hass, "light")
    rows = await _listing(hass, entity_id)
    assert "light.custom_effect" not in rows
    assert rows["light.turn_on"]["available"] is True


async def test_the_listing_gives_the_dedicated_tool_for_a_service_only_it_runs(hass):
    assert await async_setup_component(hass, "todo", {})
    entity_id = _entity(hass, "todo")
    rows = await _listing(hass, entity_id)
    row = rows["todo.remove_completed_items"]
    assert row["available"] is False
    assert "set_todo_item" in row["reason"]


async def test_the_listing_says_when_a_required_field_cannot_be_authorized(hass):
    assert await async_setup_component(hass, "media_player", {})
    entity_id = _entity(hass, "media_player")
    rows = await _listing(hass, entity_id)
    row = rows["media_player.join"]
    assert row["available"] is False and "group_members" in row["reason"]
    assert rows["media_player.media_play"]["available"] is True


async def test_unreadable_descriptions_are_logged_once(hass, caplog):
    entity_id = _entity(hass, "light")
    with patch("custom_components.phoenix_mcp.service_targets.async_get_all_descriptions", side_effect=RuntimeError):
        await unbound_service_targets(hass, [entity_id], domain="light", service="turn_on")
        await unbound_service_targets(hass, [entity_id], domain="light", service="turn_off")
    assert caplog.text.count("service descriptions could not be read") == 1
    caplog.clear()


async def test_a_rest_command_keeps_refusing_a_decoy(hass):
    assert await async_setup_component(
        hass, "rest_command", {"rest_command": {"ping": {"url": "http://example.invalid/", "method": "post"}}})
    entity_id = _entity(hass, "light")
    assert await unbound_service_targets(hass, [entity_id], domain="rest_command", service="ping") == (entity_id,)


async def test_purging_recorded_history_needs_the_registry_capability(hass):
    calls = async_mock_service(hass, "recorder", "purge_entities")
    entity_id = _entity(hass, "light")
    data, pending = _environment(hass)
    denied = await _call_service(hass, data, _key({entity_id: "GREEN"}), "recorder", "purge_entities", entity_id)
    assert denied[1] == "denied", denied[0]
    confirm = _key({entity_id: "GREEN"}, cap_registry_write="confirm")
    assert (await _call_service(hass, data, confirm, "recorder", "purge_entities", entity_id))[1] == "pending_approval"
    assert calls == [] and len(pending) == 1
    allowed = _key({entity_id: "GREEN"}, cap_registry_write="allow")
    assert (await _call_service(hass, data, allowed, "recorder", "purge_entities", entity_id))[1] == "allowed"
    assert len(calls) == 1


@pytest.mark.parametrize("selector", [{"domains": ["lock"]}, {"entity_globs": ["lock.*"]}])
async def test_a_purge_cannot_widen_past_its_resolved_targets(hass, selector):
    calls = async_mock_service(hass, "recorder", "purge_entities")
    entity_id = _entity(hass, "light")
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"}, cap_registry_write="allow")
    result, outcome, _ = await _call_service(hass, data, key, "recorder", "purge_entities", entity_id, selector)
    assert outcome == "invalid_request", result
    assert calls == []


async def test_unreadable_descriptions_fall_back_to_the_own_domain_and_the_generic_services(hass, caplog):
    async_mock_service(hass, "music_assistant", "play_media")
    player = _entity(hass, "media_player", "music_assistant")
    with patch("custom_components.phoenix_mcp.service_targets.async_get_all_descriptions", side_effect=RuntimeError):
        assert await unbound_service_targets(hass, [player], domain="music_assistant", service="play_media") == (player,)
        assert await unbound_service_targets(hass, [player], domain="media_player", service="volume_set") == ()
        assert await unbound_service_targets(hass, [player], domain="homeassistant", service="turn_on") == ()


# ---- binding at the dispatch boundary

async def _real_shell_command(hass, tmp_path) -> tuple[str, object]:
    marker = tmp_path / "ran"
    assert await async_setup_component(hass, "shell_command", {"shell_command": {"run": f"touch {marker}"}})
    return "run", marker


@pytest.mark.parametrize("pass_through", [False, True])
async def test_a_decoy_entity_does_not_reach_a_schema_less_service(hass, tmp_path, pass_through):
    name, marker = await _real_shell_command(hass, tmp_path)
    entity_id = _entity(hass, "light")
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"}, pass_through=pass_through)
    result, outcome, _ = await _call_service(hass, data, key, "shell_command", name, entity_id)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert "does not act on" in result["content"][0]["text"]
    assert not marker.exists()


async def test_saved_approval_arguments_cannot_name_a_decoy(hass, tmp_path):
    """The saved arguments carry a current target binding, so only the service binding can refuse."""
    name, marker = await _real_shell_command(hass, tmp_path)
    entity_id = _entity(hass, "light")
    data, _ = _environment(hass)
    args = bind_targets({"domain": "shell_command", "service": name, "entity_id": [entity_id]}, [entity_id], hass)
    result, outcome, _ = await async_execute_approved_tool(
        "call_service", args, _key({entity_id: "GREEN"}), hass, data)
    await hass.async_block_till_done()
    assert outcome == "denied", result
    assert "does not act on" in result["content"][0]["text"]
    assert not marker.exists()


async def test_an_unbound_target_queues_no_approval(hass):
    """Refused at admission: an operator is never asked to approve what cannot run."""
    calls = async_mock_service(hass, "notify", "send_message")
    entity_id = _entity(hass, "light")
    data, pending = _environment(hass)
    key = _key({entity_id: "GREEN"}, cap_notify="confirm")
    result, outcome, _ = await _call_service(hass, data, key, "notify", "send_message", entity_id, {"message": "hi"})
    assert outcome == "denied", result
    assert "does not act on" in result["content"][0]["text"]
    assert pending == [] and calls == []


async def test_refusal_does_not_tell_an_unknown_service_from_a_known_one(hass, tmp_path):
    name, _ = await _real_shell_command(hass, tmp_path)
    entity_id = _entity(hass, "light")
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"})
    known, known_outcome, _ = await _call_service(hass, data, key, "shell_command", name, entity_id)
    unknown, unknown_outcome, _ = await _call_service(hass, data, key, "shell_command", "absent", entity_id)
    assert known_outcome == unknown_outcome == "denied"
    assert known["content"][0]["text"].replace(name, "absent") == unknown["content"][0]["text"]


async def test_dry_run_predicts_the_refusal(hass, tmp_path):
    name, _ = await _real_shell_command(hass, tmp_path)
    entity_id = _entity(hass, "light")
    data, _ = _environment(hass)
    result, outcome, _ = await _call_tool(
        "dry_run_service", _call("shell_command", name, entity_id), _key({entity_id: "GREEN"}, cap_search="allow"), hass, data)
    body = json.loads(result["content"][0]["text"])
    assert outcome == "allowed"
    assert body["predicted_outcome"] == "denied"
    assert body["resolved_entities"] == [] and body["unbound_targets"] == [entity_id]


async def test_mixed_targets_refuse_the_whole_call(hass):
    calls = async_mock_service(hass, "light", "turn_on")
    light, switch = _entity(hass, "light"), _entity(hass, "switch")
    data, _ = _environment(hass)
    key = _key({light: "GREEN", switch: "GREEN"})
    args = {"service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": "entity", "ids": [light, switch]}]}
    result, outcome, _ = await _call_tool("call_service", args, key, hass, data)
    assert outcome == "denied", result
    assert calls == []


async def test_refusal_names_only_what_the_key_may_write(hass):
    calls = async_mock_service(hass, "light", "turn_on")
    switch, hidden = _entity(hass, "switch", object_id="mine"), _entity(hass, "switch", object_id="hidden")
    data, _ = _environment(hass)
    args = {"service": {"domain": "light", "name": "turn_on"}, "targets": [{"kind": "entity", "ids": [switch, hidden]}]}
    result, outcome, _ = await _call_tool("call_service", args, _key({switch: "GREEN"}), hass, data)
    assert outcome == "denied", result
    assert hidden not in result["content"][0]["text"]
    assert calls == []


async def test_reload_config_entry_goes_through_the_dedicated_tool(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain="test_x", title="x")
    entry.add_to_hass(hass)
    entity_id = er.async_get(hass).async_get_or_create(
        "light", "test_x", "x", suggested_object_id="a", config_entry=entry).entity_id
    hass.states.async_set(entity_id, "off")
    data, _ = _environment(hass)
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry:
        denied = await _call_service(
            hass, data, _key({entity_id: "GREEN"}, cap_integration_write="deny"),
            "homeassistant", "reload_config_entry", entity_id)
        allowed = await _call_service(
            hass, data, _key({entity_id: "GREEN"}, cap_integration_write="allow"),
            "homeassistant", "reload_config_entry", entity_id)
    assert denied[1] == "denied"
    assert allowed[1] == "invalid_request" and "reload_integration" in allowed[0]["content"][0]["text"]
    reload_entry.assert_not_awaited()


async def test_scene_delete_needs_the_scene_capability(hass):
    calls = async_mock_service(hass, "scene", "delete")
    entity_id = _entity(hass, "scene", "homeassistant")
    data, pending = _environment(hass)
    denied = await _call_service(hass, data, _key({entity_id: "GREEN"}, cap_scene_write="deny"), "scene", "delete", entity_id)
    assert denied[1] == "denied" and calls == []
    held = await _call_service(hass, data, _key({entity_id: "GREEN"}, cap_scene_write="confirm"), "scene", "delete", entity_id)
    assert held[1] == "pending_approval" and calls == []
    assert (await _call_service(hass, data, _key({entity_id: "GREEN"}, cap_scene_write="allow"), "scene", "delete", entity_id))[1] == "allowed"
    assert len(calls) == 1


# ---- capability gates reached through call_service

@pytest.mark.parametrize(("domain", "service", "cap", "service_data"), [
    ("notify", "send_message", "cap_notify", {"message": "hi"}),
    ("assist_satellite", "announce", "cap_broadcast", {"message": "hi"}),
    ("media_player", "play_media", "cap_broadcast", {
        "media_content_id": "a", "media_content_type": "music", "announce": True}),
])
async def test_confirm_through_call_service_executes_after_approval(hass, domain, service, cap, service_data):
    calls = async_mock_service(hass, domain, service)
    async_set_service_schema(hass, domain, service, {"name": service, "target": {"entity": {"domain": domain}}})
    entity_id = _entity(hass, domain)
    data, pending = _environment(hass)
    key = _key({entity_id: "GREEN"}, **{cap: "confirm"})
    result, outcome, _ = await _call_service(hass, data, key, domain, service, entity_id, service_data)
    assert outcome == "pending_approval", result
    approved, approved_outcome, _ = await async_execute_approved_tool("call_service", pending[0]["args"], key, hass, data)
    assert approved_outcome == "allowed", approved
    assert len(calls) == 1


async def test_notify_queues_nothing_for_a_target_the_key_cannot_write(hass):
    async_mock_service(hass, "notify", "send_message")
    entity_id = _entity(hass, "notify")
    data, pending = _environment(hass)
    key = _key({entity_id: "YELLOW"}, cap_notify="confirm")
    result, outcome, _ = await _call_service(hass, data, key, "notify", "send_message", entity_id, {"message": "hi"})
    assert outcome == "denied", result
    assert pending == []


async def test_denied_notify_stops_a_saved_mesa_approval(hass):
    calls = async_mock_service(hass, "notify", "send_message")
    entity_id = _entity(hass, "notify")
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"}, cap_notify="deny")
    result, outcome, _ = await async_execute_approved_tool(
        "call_service_mesa_approved",
        {"domain": "notify", "service": "send_message", "service_data": {"message": "hi"}, "entity_id": [entity_id]},
        key, hass, data)
    assert outcome == "denied", result
    assert calls == []


@pytest.mark.parametrize("service", ["start_conversation", "ask_question", "announce"])
async def test_satellite_speech_needs_the_broadcast_capability(hass, service):
    calls = async_mock_service(hass, "assist_satellite", service)
    entity_id = _entity(hass, "assist_satellite")
    data, _ = _environment(hass)
    key = _key({entity_id: "GREEN"}, cap_broadcast="deny")
    result, outcome, _ = await _call_service(hass, data, key, "assist_satellite", service, entity_id, {"start_message": "hi"})
    assert outcome == "denied", result
    assert calls == []


async def test_response_services_keep_returning_data(hass):
    async def handler(call: ServiceCall) -> dict:
        return {"ok": True}

    hass.services.async_register("todo", "get_items", handler, supports_response=SupportsResponse.ONLY)
    entity_id = _entity(hass, "todo")
    data, _ = _environment(hass)
    result, outcome, _ = await _call_service(
        hass, data, _key({entity_id: "GREEN"}, cap_service_response="allow"), "todo", "get_items", entity_id)
    assert outcome == "allowed", result
    assert json.loads(result["content"][0]["text"])["service_response"] == {"ok": True}


@pytest.mark.parametrize(("schema", "sent"), [
    pytest.param(vol.Schema({vol.Required("entity_id"): cv.entity_id}), "sensor.x", id="one entity"),
    pytest.param(vol.Schema({vol.Required("entity_id"): cv.string}), "sensor.x", id="one string"),
    pytest.param(vol.Schema({vol.Required("entity_id"): cv.entity_ids}), ["sensor.x"], id="list"),
    pytest.param(cv.make_entity_service_schema({}), ["sensor.x"], id="entity service"),
])
async def test_a_custom_service_receives_its_target_in_the_shape_it_declares(hass, schema, sent):
    seen = []

    async def handler(call: ServiceCall) -> None:
        seen.append(call.data["entity_id"])

    hass.services.async_register("custom", "act", handler, schema=schema)
    entity_id = _entity(hass, "sensor")
    data, _ = _environment(hass)
    result, outcome, _ = await _call_service(hass, data, _key({entity_id: "GREEN"}), "custom", "act", entity_id)
    assert outcome == "allowed", result
    assert seen == [sent]


async def test_several_targets_stay_a_list_for_home_assistant_to_refuse(hass):
    async def handler(call: ServiceCall) -> None:
        raise AssertionError("a single-entity service must not run for several targets")

    hass.services.async_register(
        "custom", "act", handler, schema=vol.Schema({vol.Required("entity_id"): cv.entity_id}))
    first = _entity(hass, "sensor", object_id="a")
    second = _entity(hass, "sensor", object_id="b")
    data, _ = _environment(hass)
    args = {"service": {"domain": "custom", "name": "act"}, "targets": [{"kind": "entity", "ids": [first, second]}]}
    result, outcome, _ = await _call_tool("call_service", args, _key({first: "GREEN", second: "GREEN"}), hass, data)
    assert outcome == "invalid_request", result


async def test_clearing_completed_todo_items_goes_through_the_tool_that_shows_them(hass):
    calls = async_mock_service(hass, "todo", "remove_completed_items")
    entity_id = _entity(hass, "todo")
    data, _ = _environment(hass)
    result, outcome, _ = await _call_service(
        hass, data, _key({entity_id: "GREEN"}), "todo", "remove_completed_items", entity_id)
    assert outcome == "invalid_request"
    assert "clear_completed" in result["content"][0]["text"]
    assert calls == []
