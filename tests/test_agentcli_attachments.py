"""Tests for images an administrator attaches to an Agent Chat message.

Covers the server bounds (count, size, header-declared type and dimensions),
the per-provider message shapes, learning that a model refuses image input, the
chat endpoint's refusals, and what the Conversation Log keeps of an image.
"""

from __future__ import annotations

import base64
import io
import json
import struct
import zlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
import pytest

import custom_components.phoenix_mcp.agentcli as ac
from custom_components.phoenix_mcp.agent_recovery import AgentRecovery
from custom_components.phoenix_mcp.access_key_store import GlobalSettings
from custom_components.phoenix_mcp.agentcli import (
    AgentCliStore,
    ClaudeProvider,
    OpenAICompatProvider,
    PhoenixAgentCliChatView,
    ProviderConfig,
    _chat_attachments,
    _image_kind_and_size,
    _refused_option,
    _with_learned,
)
from custom_components.phoenix_mcp.const import (
    AGENTCLI_ATTACHMENT_MAX_BYTES,
    AGENTCLI_ATTACHMENT_MAX_COUNT,
    AGENTCLI_ATTACHMENT_MAX_EDGE_PX,
    MAX_REQUEST_BODY_BYTES,
)
from custom_components.phoenix_mcp.conversation_log import prompt_attachments, prompt_excerpt, sanitized


def _encoded(fmt: str, size: tuple[int, int] = (40, 20), **save: object) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=fmt, **save)
    return buffer.getvalue()


def _attachment(data: bytes, mime_type: str) -> dict[str, str]:
    return {"mime_type": mime_type, "data": base64.b64encode(data).decode()}


def _png_header(width: int, height: int, padding: int = 0) -> bytes:
    """A PNG signature and IHDR declaring a size, padded to a byte count."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk + bytes(padding)


# --------------------------------------------------------------------------- #
# Header parsing and bounds
# --------------------------------------------------------------------------- #

def test_header_type_and_size_come_from_real_encoders():
    exif = Image.Exif()
    exif[0x010F] = "camera maker"
    assert _image_kind_and_size(_encoded("PNG", (40, 20))) == ("image/png", 40, 20)
    assert _image_kind_and_size(_encoded("GIF", (33, 7))) == ("image/gif", 33, 7)
    # An APP1 EXIF segment comes before the frame header the size is read from.
    assert _image_kind_and_size(_encoded("JPEG", (1568, 882), exif=exif)) == ("image/jpeg", 1568, 882)
    assert _image_kind_and_size(_encoded("JPEG", (64, 48), progressive=True)) == ("image/jpeg", 64, 48)


@pytest.mark.parametrize("data", [
    _encoded("WEBP"),
    _encoded("BMP"),
    b"not an image at all",
    _encoded("JPEG")[:20],
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
    b"",
])
def test_unknown_or_truncated_images_have_no_header(data):
    assert _image_kind_and_size(data) is None


def test_valid_images_are_normalized_from_their_headers():
    png, jpeg = _encoded("PNG"), _encoded("JPEG")

    images = _chat_attachments([_attachment(png, "image/png"), _attachment(jpeg, "IMAGE/JPEG ")], "rid")

    assert images == [
        {"data": base64.b64encode(png).decode(), "mime_type": "image/png"},
        {"data": base64.b64encode(jpeg).decode(), "mime_type": "image/jpeg"},
    ]
    assert _chat_attachments(None, "rid") == []
    assert _chat_attachments([], "rid") == []


_PNG = _encoded("PNG")


@pytest.mark.parametrize("value, key", [
    ({"data": "x"}, "attachmentsInvalid"),
    ([_attachment(_PNG, "image/png")] * (AGENTCLI_ATTACHMENT_MAX_COUNT + 1), "attachmentsTooMany"),
    (["not an object"], "attachmentsInvalid"),
    ([{"data": base64.b64encode(_PNG).decode()}], "attachmentsInvalid"),
    ([{"mime_type": "image/png", "data": "@@not base64@@"}], "attachmentsInvalid"),
    ([_attachment(_PNG, "image/jpeg")], "attachmentUnsupported"),
    ([_attachment(_encoded("WEBP"), "image/webp")], "attachmentUnsupported"),
    ([_attachment(b"%PDF-1.7 not an image", "image/png")], "attachmentUnsupported"),
    ([_attachment(_png_header(10, 10, AGENTCLI_ATTACHMENT_MAX_BYTES), "image/png")], "attachmentTooLarge"),
    ([_attachment(_png_header(AGENTCLI_ATTACHMENT_MAX_EDGE_PX + 1, 10), "image/png")], "attachmentDimensions"),
    ([_attachment(_png_header(10, 0), "image/png")], "attachmentDimensions"),
    ([_attachment(_png_header(10, 10, 3 * 512 * 1024), "image/png")] * 3, "attachmentsTooLarge"),
])
def test_one_bad_image_refuses_the_whole_message(value, key):
    refusal = _chat_attachments(value, "rid")

    assert not isinstance(refusal, list) and refusal.status == 400
    assert json.loads(refusal.text)["message_key"] == f"adminError.{key}"


# --------------------------------------------------------------------------- #
# Provider message shapes
# --------------------------------------------------------------------------- #

_IMAGE = {"mime_type": "image/png", "data": "QUJD"}


def test_claude_puts_images_before_the_text():
    provider = ClaudeProvider(ProviderConfig(kind="claude", model="m", base_url="https://x", api_key="k"))

    assert provider.user_message("what is wrong here?", [_IMAGE]) == {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        {"type": "text", "text": "what is wrong here?"},
    ]}
    assert provider.user_message("", [_IMAGE])["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
    ]


def test_openai_compatible_providers_send_data_uri_parts():
    openai = OpenAICompatProvider(ProviderConfig(kind="chatgpt", model="m", base_url="https://x", api_key="k"))
    mistral = OpenAICompatProvider(ProviderConfig(kind="mistral", model="m", base_url="https://x", api_key="k"))

    assert openai.user_message("look", [_IMAGE]) == {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        {"type": "text", "text": "look"},
    ]}
    assert mistral.user_message("look", [_IMAGE])["content"][0] == {
        "type": "image_url", "image_url": "data:image/png;base64,QUJD"}


# --------------------------------------------------------------------------- #
# Learning that a model refuses image input
# --------------------------------------------------------------------------- #

def _sent(with_image: bool) -> dict:
    content: object = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                       {"type": "text", "text": "hi"}] if with_image else "hi"
    return {"model": "m", "messages": [{"role": "user", "content": content}]}


@pytest.mark.parametrize("message", [
    "Failed to deserialize the JSON body into the target type: messages[0]: unknown variant `image_url`, "
    "expected `text`",
    "Invalid content type. image_url is only supported by certain models.",
    "Image input is not supported for this model.",
    "This model does not support images.",
])
def test_an_image_input_refusal_is_learned(message):
    assert _refused_option(400, message, _sent(True)) == "images"


@pytest.mark.parametrize("status, message, with_image", [
    (400, "Image exceeds 5 MB maximum: 6123456 bytes > 5242880 bytes", True),
    (400, "Could not process image", True),
    (400, "This model does not support images.", False),
    (429, "This model does not support images.", True),
])
def test_other_image_complaints_teach_nothing(status, message, with_image):
    assert _refused_option(status, message, _sent(with_image)) is None


def test_a_learned_refusal_resolves_the_model_without_vision():
    at = datetime.now(timezone.utc).isoformat()
    account = {"kind": "chatgpt", "api_key": "k", "model": "m",
               "capabilities": {"m": {"tools": True}}, "learned": {"m": {"images": at}}}
    store = AgentCliStore(MagicMock())
    store._data = {"i1": account}

    assert _with_learned(account)["m"]["vision"] is False
    resolved = store.resolve("i1")
    assert resolved is not None and resolved.vision is False


# --------------------------------------------------------------------------- #
# The chat endpoint
# --------------------------------------------------------------------------- #

def _request(body: bytes) -> MagicMock:
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
    user = MagicMock(is_admin=True, id="admin")
    request = MagicMock()
    request.method = "POST"
    request.path = "/api/phoenix-mcp/agentcli/chat"
    request.content_length = len(body)
    request.content = MagicMock()
    request.content.read = AsyncMock(return_value=body)
    request.content.at_eof = MagicMock(return_value=True)
    request.__getitem__ = MagicMock(side_effect=lambda k: user if k == KEY_HASS_USER else None)
    request.get = MagicMock(side_effect=lambda k, default=None: (
        user if k == KEY_HASS_USER else True if k == KEY_AUTHENTICATED else default))
    return request


class _Stream:
    def __init__(self, **kw):
        self.headers = dict(kw.get("headers", {}))
        self.status = kw.get("status")

    async def prepare(self, _request):
        pass

    async def write(self, _data):
        pass

    async def write_eof(self):
        pass


async def _post(hass, body: dict, *, cfg: ProviderConfig | None = None, persona: str = "custom"):
    data = MagicMock()
    data.agent_recovery = AgentRecovery()
    data.shutting_down = False
    data.store.get_settings.return_value = GlobalSettings()
    key = MagicMock(id="t1", persona=persona)
    key.is_valid.return_value = True
    data.store.get_access_key_by_id.return_value = key
    store = MagicMock()
    store.resolve.return_value = cfg or ProviderConfig(
        kind="chatgpt", model="m", base_url="https://x", api_key="k")
    view = PhoenixAgentCliChatView()
    view.hass = hass
    hass.data[ac.DOMAIN] = data
    with patch.object(ac.web, "StreamResponse", _Stream), \
         patch.object(ac, "_get_secret_store", AsyncMock(return_value=store)), \
         patch.object(ac, "async_get_clientsession", return_value=MagicMock()), \
         patch.object(ac, "async_run_agent_turn", new=AsyncMock()) as turn:
        response = await view.post(_request(json.dumps(
            {"access_key_id": "t1", "instance_id": "i1", **body}).encode()))
    return response, turn


def _error_key(response) -> str:
    return json.loads(response.text)["message_key"]


@pytest.mark.asyncio
async def test_unknown_vision_sends_the_image_in_the_providers_format(hass):
    png = _encoded("PNG")

    _response, turn = await _post(hass, {"user": "what is this?", "attachments": [_attachment(png, "image/png")]})

    turn.assert_awaited_once()
    assert turn.call_args.kwargs["messages"] == [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        {"type": "text", "text": "what is this?"},
    ]}]


@pytest.mark.asyncio
async def test_an_image_needs_no_text(hass):
    _response, turn = await _post(hass, {"user": "  ", "attachments": [_attachment(_encoded("JPEG"), "image/jpeg")]})

    message = turn.call_args.kwargs["messages"][-1]
    assert [part["type"] for part in message["content"]] == ["image_url"]


@pytest.mark.asyncio
async def test_text_only_messages_are_unchanged(hass):
    _response, turn = await _post(hass, {"user": "hello", "attachments": []})

    assert turn.call_args.kwargs["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("body, persona, vision, key", [
    ({"user": "x"}, "custom", False, "attachmentsVisionUnsupported"),
    ({"continue": True, "messages": [{"role": "user", "content": "x"}]}, "custom", None, "attachmentsContinue"),
    ({"user": "x"}, "home_companion", True, "attachmentsCompanion"),
])
async def test_images_are_refused_before_any_provider_call(hass, body, persona, vision, key):
    cfg = ProviderConfig(kind="chatgpt", model="m", base_url="https://x", api_key="k", vision=vision)

    response, turn = await _post(hass, {**body, "attachments": [_attachment(_encoded("PNG"), "image/png")]},
                                 cfg=cfg, persona=persona)

    assert response.status == 400
    assert _error_key(response) == f"adminError.{key}"
    turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_invalid_image_is_refused_with_its_reason(hass):
    response, turn = await _post(hass, {"user": "x", "attachments": [_attachment(_PNG, "image/gif")]})

    assert response.status == 400
    assert _error_key(response) == "adminError.attachmentUnsupported"
    turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_larger_body_limit_is_only_for_attachments(hass):
    history = [{"role": "user", "content": "x" * MAX_REQUEST_BODY_BYTES}]
    response, turn = await _post(hass, {"user": "x", "messages": history})
    assert response.status == 413
    turn.assert_not_awaited()

    images = [_attachment(_png_header(10, 10, 1024 * 1024), "image/png")] * 3
    _response, turn = await _post(hass, {"user": "x", "attachments": images})
    turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_resent_history_images_use_the_image_budget(hass):
    """The browser resends the newest images, so they must not hit the text cap."""
    data = base64.b64encode(_png_header(10, 10, 3 * 1024 * 1024)).decode()
    claude = {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
        {"type": "text", "text": "what is wrong?"}]}
    openai = {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        {"type": "text", "text": "what is wrong?"}]}
    for message in (claude, openai):
        history = [message, {"role": "assistant", "content": "the card overflows"}]
        _response, turn = await _post(hass, {"user": "and the right one?", "messages": history})
        turn.assert_awaited_once()

    too_many = [claude, {"role": "assistant", "content": "ok"}] * 2
    response, turn = await _post(hass, {"user": "x", "messages": too_many})
    assert response.status == 413
    turn.assert_not_awaited()


def _history_with_images() -> list[dict]:
    """An attachment, a camera result inside a tool result, and an OpenAI image part."""
    return [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
            {"type": "text", "text": "what is wrong?"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_camera_image", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "text", "text": "snapshot"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "REVG"}}]}]},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,R0hJ"}}]},
        {"role": "assistant", "content": "the card overflows"},
    ]


@pytest.mark.asyncio
async def test_a_model_without_vision_gets_notes_instead_of_history_images(hass):
    """A refused image must not be resent with the history and refused again."""
    cfg = ProviderConfig(kind="claude", model="m", base_url="https://x", api_key="k", vision=False)

    _response, turn = await _post(hass, {"user": "and now?", "messages": _history_with_images()}, cfg=cfg)

    sent = json.dumps(turn.call_args.kwargs["messages"])
    for data in ("QUJD", "REVG", "R0hJ"):
        assert data not in sent
    assert sent.count(ac._UNSEEN_IMAGE_NOTE) == 3
    assert "what is wrong?" in sent and "snapshot" in sent


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [None, True])
async def test_history_images_stay_for_a_model_that_may_see_them(hass, vision):
    cfg = ProviderConfig(kind="claude", model="m", base_url="https://x", api_key="k", vision=vision)

    _response, turn = await _post(hass, {"user": "and now?", "messages": _history_with_images()}, cfg=cfg)

    assert turn.call_args.kwargs["messages"][:-1] == _history_with_images()


def test_image_payloads_are_counted_wherever_a_history_holds_them():
    tool_result = {"type": "tool_result", "tool_use_id": "t", "content": [
        {"type": "text", "text": "snapshot"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD" * 10}}]}
    history = [
        {"role": "user", "content": [tool_result]},
        {"role": "user", "content": [{"type": "image_url", "image_url": "data:image/png;base64,QUJD"}]},
        {"role": "user", "content": "plain text, no image"},
    ]
    assert ac._image_payload_chars(history) == 40 + len("data:image/png;base64,QUJD")
    assert ac._image_payload_chars("not a history") == 0


# --------------------------------------------------------------------------- #
# Conversation Log
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("provider", [
    ClaudeProvider(ProviderConfig(kind="claude", model="m", base_url="https://x", api_key="k")),
    OpenAICompatProvider(ProviderConfig(kind="chatgpt", model="m", base_url="https://x", api_key="k")),
])
def test_the_log_keeps_the_text_and_counts_images_without_their_bytes(provider):
    data = base64.b64encode(_encoded("PNG")).decode()
    prompt = provider.user_message("broken card", [{"mime_type": "image/png", "data": data}])["content"]

    kept = sanitized(prompt, max_bytes=100_000)

    assert data not in json.dumps(kept)
    assert prompt_attachments(kept) == 1
    assert prompt_excerpt(kept) == "broken card"
