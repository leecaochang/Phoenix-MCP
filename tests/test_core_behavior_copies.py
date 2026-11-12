"""Phoenix MCP's copies of how Home Assistant behaves stay true to the installed Core.

A table that restates Core behavior, rather than limiting what Phoenix allows, fails
silently when Core changes: a storage collection that starts merging updates, a
renamed state attribute, a new backup agent that keeps one backup. Where no public API
exposes the value, each copy is checked here against the installed Core, so an upgrade
fails the suite instead of Phoenix. Triage tables record why each Core item that a
survey finds, and Phoenix does not list, needs no entry; a new one fails until judged.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant
import pytest
import yaml
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_OFF, STATE_ON
from homeassistant.setup import async_setup_component

from custom_components.phoenix_mcp.const import (
    ANNOUNCE_BIT,
    ANNOUNCEMENT_SERVICES,
    BACKUP_SINGLE_SLOT_AGENT_DOMAINS,
    CROSS_DOMAIN_SERVICES,
    DASHBOARD_RESOURCE_TYPES,
    DOMAIN_IMPORTANT_ATTRIBUTES,
    LOCAL_BACKUP_AGENT_IDS,
    MEDIA_SOURCE_ENTITY_DOMAINS,
    MESA_SUGGEST_COVER_DEVICE_CLASSES,
    NO_TARGET_SERVICES,
    REACH_WIDENING_FLAGS,
    SCRIPT_INPUT_FIELD,
    SCRIPT_INPUT_SERVICES,
    STORAGE_HELPER_ICON_TYPES,
    STORAGE_HELPER_MERGING_TYPES,
    STORAGE_HELPER_TYPES,
)
from custom_components.phoenix_mcp.routing.routing_tables import (
    COMMAND_ROUTING_NUMERIC_BOUNDS,
    COMMAND_ROUTING_NUMERIC_OPERATIONS,
    COMMAND_ROUTING_NUMERIC_STATE,
    COMMAND_ROUTING_READ_PERCENT_SCALES,
    COMMAND_SUMMARY_STATES,
)
from custom_components.phoenix_mcp.ws_dispatch import WsDispatchError, async_ws_command

_CORE = Path(homeassistant.__file__).parent
_COMPONENTS = _CORE / "components"


class _Loader(yaml.SafeLoader):
    """Core's service files use a tag the safe loader does not know."""


_Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))


def _service_descriptions() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for path in sorted(_COMPONENTS.glob("*/services.yaml")):
        for service, description in (yaml.load(path.read_text(), Loader=_Loader) or {}).items():
            rows[f"{path.parent.name}/{service}"] = description if isinstance(description, dict) else {}
    return rows


_DESCRIPTIONS = _service_descriptions()


def _fields(service_key: str) -> dict:
    """A service's fields, with the fields of its collapsible sections folded in."""
    fields: dict = {}
    for name, field in (_DESCRIPTIONS[service_key].get("fields") or {}).items():
        if isinstance(field, dict) and "fields" in field and "selector" not in field:
            fields.update(field["fields"])
        else:
            fields[name] = field
    return fields


def _string_literals(domain: str) -> set[str]:
    """Every quoted identifier in a component's modules and in Core's shared constants.

    Script and automation state attributes are named in the shared script helper.
    """
    text = (_CORE / "const.py").read_text() + (_CORE / "helpers" / "script.py").read_text()
    for path in (_COMPONENTS / domain).glob("*.py"):
        text += path.read_text()
    return set(re.findall(r"""["']([a-z0-9_]+)["']""", text))


def test_the_service_survey_read_core() -> None:
    """A survey that found nothing would let every rule below pass for nothing."""
    assert len(_DESCRIPTIONS) > 1000 and "light/turn_on" in _DESCRIPTIONS


# Storage helpers: the payload a create needs, and the stored field an update omits.
# HA rebuilds a non-merging item from the update alone, so the omitted field is lost.
_STORAGE_HELPER_PAYLOADS: dict[str, tuple[dict, str, object]] = {
    "input_boolean": ({}, "icon", "mdi:sofa"),
    "input_button": ({}, "icon", "mdi:sofa"),
    "input_datetime": ({"has_date": True}, "icon", "mdi:sofa"),
    "input_number": ({"min": 0, "max": 10}, "icon", "mdi:sofa"),
    "input_select": ({"options": ["a", "b"]}, "icon", "mdi:sofa"),
    "input_text": ({}, "icon", "mdi:sofa"),
    "counter": ({}, "icon", "mdi:sofa"),
    "timer": ({}, "icon", "mdi:sofa"),
    "schedule": ({}, "icon", "mdi:sofa"),
    "zone": ({"latitude": 1.0, "longitude": 2.0, "radius": 100}, "icon", "mdi:sofa"),
    "tag": ({"tag_id": "copy-probe"}, "description", "kept"),
    # Not device_trackers: HA's update schema defaults an omitted list to [], which the person
    # edit path compensates for by resending the stored trackers.
    "person": ({}, "picture", "/api/image/serve/copy_probe/512x512"),
}


def test_every_storage_helper_has_a_probe() -> None:
    assert set(_STORAGE_HELPER_PAYLOADS) == STORAGE_HELPER_TYPES


async def _setup_storage_helper(hass, helper_type: str) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, helper_type, {})
    await hass.async_block_till_done()


@pytest.mark.parametrize("helper_type", sorted(STORAGE_HELPER_TYPES))
async def test_storage_helper_update_merges_only_where_phoenix_says(hass, hass_admin_user, helper_type) -> None:
    """The update target is named <type>_id, and an omitted field survives only a merge."""
    await _setup_storage_helper(hass, helper_type)
    base, field, value = _STORAGE_HELPER_PAYLOADS[helper_type]
    created = await async_ws_command(hass, f"{helper_type}/create", {"name": "Copy Probe", **base, field: value})
    assert created[field] == value
    update = {key: item for key, item in base.items() if key != "tag_id"}
    updated = await async_ws_command(hass, f"{helper_type}/update", {
        f"{helper_type}_id": created["id"], "name": "Copy Probe Renamed", **update,
    })
    assert updated["name"] == "Copy Probe Renamed"
    assert (updated.get(field) == value) is (helper_type in STORAGE_HELPER_MERGING_TYPES)


@pytest.mark.parametrize("helper_type", sorted(STORAGE_HELPER_TYPES - STORAGE_HELPER_ICON_TYPES))
async def test_storage_helpers_without_an_icon_store_none(hass, hass_admin_user, helper_type) -> None:
    await _setup_storage_helper(hass, helper_type)
    base, _field, _value = _STORAGE_HELPER_PAYLOADS[helper_type]
    try:
        created = await async_ws_command(hass, f"{helper_type}/create", {"name": "Icon Probe", **base, "icon": "mdi:sofa"})
    except WsDispatchError:
        return
    assert "icon" not in created


@pytest.mark.parametrize("service_key", sorted(NO_TARGET_SERVICES))
def test_every_no_target_service_exists_and_takes_no_target(service_key) -> None:
    assert service_key in _DESCRIPTIONS
    assert "target" not in _DESCRIPTIONS[service_key]


@pytest.mark.parametrize("service_key", sorted(CROSS_DOMAIN_SERVICES))
def test_every_cross_domain_service_targets_any_entity(service_key) -> None:
    description = _DESCRIPTIONS[service_key]
    if "target" in description:
        # An empty target block admits every kind of target with no domain filter.
        filters = (description["target"] or {}).get("entity") or {}
    else:
        filters = _fields(service_key)["entity_id"]["selector"]["entity"] or {}
    assert not (filters.get("domain") if isinstance(filters, dict) else any(f.get("domain") for f in filters))


# Core fields named like a reach-widening flag that do not widen a call's reach.
_FLAG_NAMES_NOT_WIDENING = {
    ("foursquare/checkin", "broadcast"): "shares a check-in with linked social accounts; no device is addressed",
}


def test_every_core_broadcast_field_has_been_judged() -> None:
    flags = {flag for flagged in REACH_WIDENING_FLAGS.values() for flag in flagged}
    found = {(key, name) for key in _DESCRIPTIONS for name in _fields(key) if name in flags}
    declared = {(key, flag) for key, flagged in REACH_WIDENING_FLAGS.items() for flag in flagged}
    assert declared <= found
    assert found - declared - set(_FLAG_NAMES_NOT_WIDENING) == set()


@pytest.mark.parametrize("service_key", sorted(SCRIPT_INPUT_SERVICES))
async def test_script_input_services_hand_variables_to_the_run(hass, service_key) -> None:
    domain, service = service_key.split("/")
    assert await async_setup_component(hass, domain, {domain: {}})
    await hass.async_block_till_done()
    schema = hass.services.async_services()[domain][service].schema
    validated = schema({"entity_id": [f"{domain}.copy_probe"], SCRIPT_INPUT_FIELD: {"door": "lock.x"}})
    assert validated[SCRIPT_INPUT_FIELD] == {"door": "lock.x"}


# Core services that speak or announce and are not in ANNOUNCEMENT_SERVICES.
_SPEECH_SERVICES_ELSEWHERE = {
    "elkm1/speak_phrase": "speaks on an Elk panel chosen by prefix; takes no target, so call_service cannot reach it",
    "elkm1/speak_word": "speaks on an Elk panel chosen by prefix; takes no target, so call_service cannot reach it",
    "media_player/play_media": "gated as an announcement when its announce field is set",
    "tts/say": "a legacy tts.<engine>_say service, gated by its suffix",
    "yolink/play_on_speaker_hub": "names its speaker in target_device, a secondary target call_service refuses",
}
_SPEECH_WORDS = ("announce", "ask_question", "conversation", "speak", "say", "broadcast", "tts")


def test_every_core_speech_service_has_been_judged() -> None:
    speech = {
        key for key in _DESCRIPTIONS
        if any(word in key.split("/")[1] for word in _SPEECH_WORDS) or "announce" in _fields(key)
    }
    speech -= {key for key, _ in _FLAG_NAMES_NOT_WIDENING} | set(REACH_WIDENING_FLAGS)
    assert ANNOUNCEMENT_SERVICES <= set(_DESCRIPTIONS)
    assert speech - ANNOUNCEMENT_SERVICES - set(_SPEECH_SERVICES_ELSEWHERE) == set()


# Core media sources whose identifiers are not entity IDs, so a key's scope cannot judge them.
_MEDIA_SOURCES_NOT_ENTITIES = {
    "ai_task": "generated images, admitted only to the key that generated them",
    "dlna_dms": "a DLNA server's library, keyed by server",
    "google_photos": "an account's photo library",
    "icloud": "an account's photo library",
    "image_upload": "uploaded images, keyed by upload ID",
    "immich": "a photo server's library",
    "jellyfin": "a media server's library",
    "motioneye": "camera recordings, keyed by config entry and camera",
    "nest": "camera events, keyed by device",
    "netatmo": "camera events, keyed by home and camera",
    "radio_browser": "internet radio stations",
    "reolink": "camera recordings, keyed by config entry and channel",
    "synology_dsm": "a photo library",
    "system_bridge": "files of a remote host",
    "tts": "speech generated on request",
    "unifiprotect": "camera events, keyed by console and camera",
    "xbox": "game captures of an account",
}


def test_every_core_media_source_has_been_judged() -> None:
    sources = {path.parent.name for path in _COMPONENTS.glob("*/media_source.py")}
    assert MEDIA_SOURCE_ENTITY_DOMAINS <= sources
    assert sources - MEDIA_SOURCE_ENTITY_DOMAINS - set(_MEDIA_SOURCES_NOT_ENTITIES) == set()


# Core backup agents that keep every backup, each under its own name or ID.
_MULTI_SLOT_BACKUP_AGENTS = frozenset({
    "aws_s3", "azure_storage", "backblaze_b2", "backup", "cloudflare_r2", "dropbox",
    "google_drive", "hassio", "idrive_e2", "kitchen_sink", "onedrive", "onedrive_for_business",
    "seafile", "sftp_storage", "synology_dsm", "webdav",
})


def test_every_core_backup_agent_has_been_judged() -> None:
    agents = {
        path.parent.name for path in _COMPONENTS.glob("*/backup.py")
        if "def async_get_backup_agents" in path.read_text()
    }
    assert BACKUP_SINGLE_SLOT_AGENT_DOMAINS <= agents
    assert agents - BACKUP_SINGLE_SLOT_AGENT_DOMAINS - _MULTI_SLOT_BACKUP_AGENTS == set()


def test_cloud_backup_still_keeps_one_backup() -> None:
    """The cloud agent uploads under the instance's one fixed name, replacing the last backup."""
    source = (_COMPONENTS / "cloud" / "backup.py").read_text()
    assert 'filename = f"{self._cloud.client.prefs.instance_id}.tar"' in source


async def test_local_backup_agent_ids_are_cores_own(hass) -> None:
    """Core's local agent and the Supervisor's local storage agent carry exactly these IDs."""
    from homeassistant.components.backup.backup import CoreLocalBackupAgent
    from homeassistant.components.hassio import backup as hassio_backup

    client = MagicMock()
    client.mounts.info = AsyncMock(return_value=MagicMock(mounts=[]))
    with patch.object(hassio_backup, "get_supervisor_client", return_value=client):
        supervisor_agents = await hassio_backup.async_get_backup_agents(hass)
    core_agent_id = f"{CoreLocalBackupAgent.domain}.{CoreLocalBackupAgent.unique_id}"
    assert set(LOCAL_BACKUP_AGENT_IDS) == {supervisor_agents[0].agent_id, core_agent_id}
    assert supervisor_agents[0].location == hassio_backup.LOCATION_LOCAL_STORAGE


@pytest.mark.parametrize("domain", sorted(DOMAIN_IMPORTANT_ATTRIBUTES))
def test_lean_view_attributes_are_names_core_writes(domain) -> None:
    assert set(DOMAIN_IMPORTANT_ATTRIBUTES[domain]) - _string_literals(domain) == set()


@pytest.mark.parametrize("intent", sorted(COMMAND_ROUTING_NUMERIC_OPERATIONS))
def test_numeric_routing_names_cores_service_fields_and_attributes(intent) -> None:
    domain, _slot, _proposal, service, field, *_rest = COMMAND_ROUTING_NUMERIC_OPERATIONS[intent]
    assert field in _fields(f"{domain}/{service}")
    attributes = {COMMAND_ROUTING_NUMERIC_STATE.get(intent), *COMMAND_ROUTING_NUMERIC_BOUNDS.get(intent, ())}
    assert attributes - {None} - _string_literals(domain) == set()


@pytest.mark.parametrize("attribute", sorted(COMMAND_ROUTING_READ_PERCENT_SCALES))
def test_percent_read_attributes_are_names_core_writes(attribute) -> None:
    domains, _scale = COMMAND_ROUTING_READ_PERCENT_SCALES[attribute]
    assert [domain for domain in domains if attribute not in _string_literals(domain)] == []


def _core_states(domain: str) -> set[str]:
    from homeassistant.components.cover import CoverState
    from homeassistant.components.lock import LockState
    from homeassistant.components.media_player import MediaPlayerState
    try:
        from homeassistant.components.timer.const import STATUS_ACTIVE, STATUS_IDLE, STATUS_PAUSED
    except ImportError:  # releases that define them in the package itself
        from homeassistant.components.timer import STATUS_ACTIVE, STATUS_IDLE, STATUS_PAUSED
    from homeassistant.components.vacuum import VacuumActivity
    from homeassistant.components.valve import ValveState

    enums = {
        "cover": CoverState, "valve": ValveState, "lock": LockState,
        "media_player": MediaPlayerState, "vacuum": VacuumActivity,
    }
    if domain in enums:
        return {str(member.value) for member in enums[domain]}
    if domain == "timer":
        return {STATUS_ACTIVE, STATUS_IDLE, STATUS_PAUSED}
    if domain in ("person", "device_tracker"):
        return {STATE_HOME, STATE_NOT_HOME}
    return {STATE_ON, STATE_OFF}


@pytest.mark.parametrize("domain", sorted(COMMAND_SUMMARY_STATES))
def test_summary_states_are_states_core_reports(domain) -> None:
    assert set(COMMAND_SUMMARY_STATES[domain]) - _core_states(domain) == set()


def test_mesa_cover_classes_are_core_device_classes() -> None:
    from homeassistant.components.cover import CoverDeviceClass

    assert MESA_SUGGEST_COVER_DEVICE_CLASSES <= {str(member.value) for member in CoverDeviceClass}


def test_announce_bit_is_cores_satellite_feature() -> None:
    from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature

    assert ANNOUNCE_BIT == AssistSatelliteEntityFeature.ANNOUNCE


def test_dashboard_resource_types_are_cores_less_html() -> None:
    from homeassistant.components.lovelace.const import RESOURCE_TYPES

    assert set(RESOURCE_TYPES) - set(DASHBOARD_RESOURCE_TYPES) == {"html"}
