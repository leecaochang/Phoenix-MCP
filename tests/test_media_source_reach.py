"""A media-source ID naming a camera or image needs camera image access to it, in any service.

Core reads the picture behind ``media-source://camera/<id>`` for whatever service carries it,
and checks the caller only when its context has a user, which a Phoenix key does not. The
AI Task tests run on Core's real component, schema and service descriptions with a
third-party entity, so the refusal is pinned where Core would otherwise read the camera.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.components import ai_task as core_ai_task
from homeassistant.components.ai_task import task as core_task
from homeassistant.components.ai_task.const import DATA_COMPONENT, AITaskEntityFeature
from homeassistant.components.ai_task.entity import AITaskEntity
from homeassistant.components.ai_task.task import GenDataTaskResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import async_mock_service

from custom_components.phoenix_mcp import mesa_audit
from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.const import DOMAIN, MESA_MODE_ADVISORY, MESA_MODE_OFF
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.tool_dispatch import _call_tool, async_execute_approved_tool

TASK = "ai_task.other_vendor"
CAMERA = "camera.porch"
HIDDEN = "camera.bedroom"
IMAGE = "image.doorbell"


class _OtherVendorTask(AITaskEntity):
    """A third-party AI Task entity that records what Core hands it."""

    _attr_supported_features = AITaskEntityFeature.GENERATE_DATA | AITaskEntityFeature.SUPPORT_ATTACHMENTS
    _attr_unique_id = "other_vendor"
    _attr_name = None
    entity_id = TASK

    def __init__(self) -> None:
        self.tasks: list = []
        self.contexts: list = []

    async def internal_async_generate_data(self, session, task, context=None):
        self.tasks.append(task)
        self.contexts.append(context)
        return GenDataTaskResult(conversation_id="c", data="described")


def _environment(hass, *, mesa_mode: str = MESA_MODE_OFF, mesa=None) -> tuple[PhoenixData, list]:
    pending: list = []
    store = MagicMock(spec=AccessKeyStore)
    store.async_lock = asyncio.Lock()
    store.async_save = AsyncMock()
    store.get_pending_approvals.side_effect = lambda: pending
    store.set_pending_approvals.side_effect = lambda rows: pending.__setitem__(slice(None), rows)
    store.get_settings.return_value = GlobalSettings(mesa_mode=mesa_mode)
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, audit=MagicMock(), rate_limiter=MagicMock(), mesa=mesa)
    hass.data[DOMAIN] = data
    return data, pending


def _key(entities: dict[str, str], **fields) -> AccessKeyRecord:
    fields.setdefault("cap_camera_read", "allow")
    # Core runs generate_data only with a response requested.
    fields.setdefault("cap_service_response", "allow")
    return AccessKeyRecord(
        id="k", name="K", access_key_hash="x", created_at=utcnow(), created_by="a",
        permissions=PermissionTree(entities={e: PermissionNode(state=s) for e, s in entities.items()}),
        **fields,
    )


def _picture(hass, entity_id: str) -> None:
    domain, object_id = entity_id.split(".", 1)
    er.async_get(hass).async_get_or_create(domain, "test", object_id, suggested_object_id=object_id)
    hass.states.async_set(entity_id, "idle")


@pytest.fixture
async def other_vendor(hass):
    """Core's AI Task component with one third-party entity, and the media it would resolve."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "ai_task", {})
    entity = _OtherVendorTask()
    await hass.data[DATA_COMPONENT].async_add_entities([entity])
    for picture in (CAMERA, HIDDEN, IMAGE):
        _picture(hass, picture)
    resolved: list = []

    async def resolve(hass_, session, attachments=None):
        resolved.append(attachments)
        return []

    with patch.object(core_task, "_resolve_attachments", resolve):
        yield entity, resolved


def _generate(*attachments: str) -> dict:
    data = {
        "task_name": "look", "instructions": "Describe it",
        "attachments": [{"media_content_id": item, "media_content_type": "image/jpeg"} for item in attachments],
    }
    return {"service": {"domain": "ai_task", "name": "generate_data", "data": data},
            "targets": [{"kind": "entity", "ids": [TASK]}]}


def _text(result: dict) -> str:
    return result["content"][0]["text"]


async def test_core_reads_an_attachment_without_checking_a_userless_caller(hass, other_vendor):
    """The premise: Core forwards a hidden camera for a context with no user."""
    entity, resolved = other_vendor
    attachment = {"media_content_id": f"media-source://camera/{HIDDEN}", "media_content_type": "image/jpeg"}
    await hass.services.async_call("ai_task", "generate_data", {
        "task_name": "t", "instructions": "i", "entity_id": TASK, "attachments": [attachment]},
        blocking=True, return_response=True)
    assert resolved == [[attachment]] and len(entity.tasks) == 1
    assert entity.contexts[0].user_id is None


async def test_a_readable_camera_attachment_reaches_the_task(hass, other_vendor):
    entity, resolved = other_vendor
    data, _ = _environment(hass)
    key = _key({TASK: "GREEN", CAMERA: "YELLOW"})
    result, outcome, _ = await _call_tool("call_service", _generate(f"media-source://camera/{CAMERA}"), key, hass, data)
    assert outcome == "allowed", result
    assert resolved == [[{"media_content_id": f"media-source://camera/{CAMERA}", "media_content_type": "image/jpeg"}]]
    assert len(entity.tasks) == 1


async def test_a_readable_image_attachment_reaches_the_task(hass, other_vendor):
    entity, resolved = other_vendor
    data, _ = _environment(hass)
    key = _key({TASK: "GREEN", IMAGE: "YELLOW"})
    result, outcome, _ = await _call_tool("call_service", _generate(f"media-source://image/{IMAGE}"), key, hass, data)
    assert outcome == "allowed", result
    assert len(resolved) == 1 and len(entity.tasks) == 1


@pytest.mark.parametrize("pass_through", [False, True])
@pytest.mark.parametrize("media", [
    f"media-source://camera/{HIDDEN}",
    "media-source://camera/camera.nowhere",
    f"MEDIA-SOURCE://Camera/{HIDDEN.upper()}",
    f"  media-source://camera/{HIDDEN}  ",
    f"media-source://image/{HIDDEN}",
])
async def test_an_unreadable_or_missing_picture_is_refused_alike(hass, other_vendor, media, pass_through):
    entity, resolved = other_vendor
    data, _ = _environment(hass)
    grants = {TASK: "GREEN", CAMERA: "YELLOW", HIDDEN: "RED"}
    if pass_through:
        # Pass-through reads every camera; only the capability and Core's own reading are left.
        grants = {}
    key = _key(grants, pass_through=pass_through, cap_camera_read="deny" if pass_through else "allow")
    result, outcome, _ = await _call_tool("call_service", _generate(f"media-source://camera/{CAMERA}", media),
                                          key, hass, data)
    assert outcome == "denied", result
    assert "camera image access" in _text(result) and HIDDEN not in _text(result)
    assert resolved == [] and entity.tasks == []


async def test_an_unreadable_camera_queues_no_approval(hass, other_vendor):
    """Refused at admission: an approval for it could never run."""
    entity, resolved = other_vendor
    data, pending = _environment(hass)
    key = _key({TASK: "GREEN", HIDDEN: "RED"}, cap_service_response="confirm")
    result, outcome, _ = await _call_tool("call_service", _generate(f"media-source://camera/{HIDDEN}"), key, hass, data)
    assert outcome == "denied", result
    assert pending == [] and resolved == [] and entity.tasks == []


async def test_a_hidden_and_a_missing_camera_answer_the_same_bytes(hass, other_vendor):
    data, _ = _environment(hass)
    key = _key({TASK: "GREEN", HIDDEN: "RED"})
    hidden = await _call_tool("call_service", _generate(f"media-source://camera/{HIDDEN}"), key, hass, data)
    missing = await _call_tool("call_service", _generate("media-source://camera/camera.nowhere"), key, hass, data)
    assert hidden[0] == missing[0] and hidden[1] == missing[1] == "denied"


@pytest.mark.parametrize("capability", ["deny", "confirm"])
async def test_a_readable_camera_still_needs_the_camera_capability(hass, other_vendor, capability):
    entity, resolved = other_vendor
    data, pending = _environment(hass)
    key = _key({TASK: "GREEN", CAMERA: "YELLOW"}, cap_camera_read=capability)
    result, outcome, _ = await _call_tool("call_service", _generate(f"media-source://camera/{CAMERA}"), key, hass, data)
    assert outcome == "denied", result
    assert resolved == [] and entity.tasks == [] and pending == []


async def test_pass_through_with_the_camera_capability_attaches_any_camera(hass, other_vendor):
    entity, resolved = other_vendor
    data, _ = _environment(hass)
    key = _key({}, pass_through=True)
    result, outcome, _ = await _call_tool("call_service", _generate(f"media-source://camera/{HIDDEN}"), key, hass, data)
    assert outcome == "allowed", result
    assert len(resolved) == 1 and len(entity.tasks) == 1


@pytest.mark.parametrize("attachment", [
    {"media_content_id": "media-source://media_source/local/snapshots/bedroom.jpg", "media_content_type": "image/jpeg"},
    {"media_content_id": "media-source://image_upload/abc", "media_content_type": "image/png"},
    {"media_content_id": "/config/www/bedroom.jpg", "media_content_type": "image/jpeg"},
    {"media_content_type": "image/jpeg"},
    "media-source://camera/camera.porch",
])
async def test_an_attachment_from_another_source_is_refused(hass, other_vendor, attachment):
    entity, resolved = other_vendor
    data, _ = _environment(hass)
    call = _generate(f"media-source://camera/{CAMERA}")
    call["service"]["data"]["attachments"].append(attachment)
    result, outcome, _ = await _call_tool(
        "call_service", call, _key({TASK: "GREEN", CAMERA: "YELLOW"}), hass, data)
    assert outcome == "invalid_request", result
    assert "can only be cameras or images" in _text(result)
    assert resolved == [] and entity.tasks == []


async def test_a_mesa_privacy_denial_refuses_the_attachment(hass, other_vendor):
    entity, resolved = other_vendor
    runtime = MagicMock()
    runtime.resolver.resolve.return_value = SimpleNamespace(
        domain="camera", privacy_classification=MagicMock(), person_traits=SimpleNamespace(is_minor=False))
    runtime.enforcer.privacy.evaluate.return_value = SimpleNamespace(allowed=False)
    data, _ = _environment(hass, mesa_mode=MESA_MODE_ADVISORY, mesa=runtime)
    result, outcome, _ = await _call_tool(
        "call_service", _generate(f"media-source://camera/{CAMERA}"), _key({TASK: "GREEN", CAMERA: "YELLOW"}), hass, data)
    assert outcome == "denied", result
    assert resolved == [] and entity.tasks == []
    runtime.enforcer.privacy.evaluate.assert_called_once()


async def test_the_preview_predicts_the_refusal_without_recording_a_privacy_denial(hass, other_vendor):
    runtime = MagicMock()
    runtime.resolver.resolve.return_value = SimpleNamespace(
        domain="camera", privacy_classification=MagicMock(), person_traits=SimpleNamespace(is_minor=False))
    attributed: list = []

    def evaluate(*_args, **_kwargs):
        attributed.append(mesa_audit._context.get())
        return SimpleNamespace(allowed=False)

    runtime.enforcer.privacy.evaluate.side_effect = evaluate
    data, _ = _environment(hass, mesa_mode=MESA_MODE_ADVISORY, mesa=runtime)
    key = _key({TASK: "GREEN", CAMERA: "YELLOW", HIDDEN: "RED"}, cap_search="allow")
    hidden = await _call_tool("dry_run_service", _generate(f"media-source://camera/{HIDDEN}"), key, hass, data)
    assert hidden[1] == "denied" and "camera image access" in _text(hidden[0])
    private = await _call_tool("dry_run_service", _generate(f"media-source://camera/{CAMERA}"), key, hass, data)
    assert private[1] == "denied"
    assert attributed == [None]


async def test_a_saved_approval_is_refused_when_the_camera_left_scope(hass, other_vendor):
    entity, resolved = other_vendor
    data, pending = _environment(hass)
    queued = await _call_tool("call_service", _generate(f"media-source://camera/{CAMERA}"),
                              _key({TASK: "GREEN", CAMERA: "YELLOW"}, cap_service_response="confirm"), hass, data)
    assert len(pending) == 1, queued
    saved = pending[0]["args"]
    narrowed = _key({TASK: "GREEN", CAMERA: "RED"})
    result, outcome, _ = await async_execute_approved_tool("call_service", saved, narrowed, hass, data)
    assert outcome == "denied", result
    assert "camera image access" in _text(result)
    assert resolved == [] and entity.tasks == []
    allowed = await async_execute_approved_tool("call_service", saved, _key({TASK: "GREEN", CAMERA: "YELLOW"}), hass, data)
    assert allowed[1] == "allowed", allowed
    assert len(entity.tasks) == 1


async def test_any_service_naming_a_camera_by_media_source_is_judged(hass):
    """A photo sent to a chat or played on a screen reads the camera as much as an attachment does."""
    calls = async_mock_service(hass, "chat_bot", "send_photo", schema=vol.Schema(
        {vol.Required("entity_id"): cv.entity_ids, vol.Required("media"): dict}, extra=vol.ALLOW_EXTRA))
    chat = "chat_bot.family"
    _picture(hass, chat)
    for picture in (HIDDEN, CAMERA, IMAGE):
        _picture(hass, picture)
    data, _ = _environment(hass)
    key = _key({chat: "GREEN", CAMERA: "YELLOW", IMAGE: "YELLOW", HIDDEN: "RED"})

    def send(media_id: str) -> dict:
        return {"service": {"domain": "chat_bot", "name": "send_photo", "data": {
            "media": {"media_content_id": media_id, "media_content_type": "image/jpeg"},
            "extra": [{"nested": [media_id]}]}},
            "targets": [{"kind": "entity", "ids": [chat]}]}

    refused = await _call_tool("call_service", send(f"media-source://camera/{HIDDEN}"), key, hass, data)
    assert refused[1] == "denied", refused
    mismatched = await _call_tool("call_service", send(f"media-source://camera/{IMAGE}"), key, hass, data)
    assert mismatched[1] == "denied", mismatched
    assert calls == []
    sent = await _call_tool("call_service", send(f"media-source://camera/{CAMERA}"), key, hass, data)
    assert sent[1] == "allowed", sent
    assert len(calls) == 1
    # Text that only mentions an ID is not read by Home Assistant, and passes.
    told = {"service": {"domain": "chat_bot", "name": "send_photo", "data": {
        "media": {}, "caption": f"see media-source://camera/{HIDDEN} later"}},
        "targets": [{"kind": "entity", "ids": [chat]}]}
    assert (await _call_tool("call_service", told, key, hass, data))[1] == "allowed"


async def test_a_call_without_media_ids_needs_no_camera_capability(hass, other_vendor):
    entity, _ = other_vendor
    data, _ = _environment(hass)
    call = _generate()
    del call["service"]["data"]["attachments"]
    result, outcome, _ = await _call_tool(
        "call_service", call, _key({TASK: "GREEN"}, cap_camera_read="deny"), hass, data)
    assert outcome == "allowed", result
    assert len(entity.tasks) == 1


def test_core_still_names_the_attachment_field_and_its_scheme():
    """Phoenix reads Core's field and scheme by name; a rename must fail here, not pass silently."""
    from homeassistant.components.ai_task.const import ATTR_ATTACHMENTS
    from homeassistant.components.media_source import URI_SCHEME

    assert ATTR_ATTACHMENTS == "attachments" and URI_SCHEME == "media-source://"
    assert core_ai_task.DOMAIN == "ai_task"
    assert json.dumps(sorted(AITaskEntityFeature.__members__))
