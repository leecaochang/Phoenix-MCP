"""Camera image retrieval with Phoenix capability, scope, and MESA checks.

This module deliberately uses Home Assistant's in-process camera helper. It
never calls Home Assistant's own REST camera proxy, and it never returns a
camera URL or stores image bytes in Phoenix-owned state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.util import dt as dt_util

from ..const import CAP_ALLOW, MESA_MODE_OFF
from ..data import PhoenixData
from ..helpers import effective_cap, str_arg
from ..mesa import build_caller_context
from ..mesa_audit import request_context
from ..policy_engine import Permission, canonical_entity_id, resolve_read
from ..access_key_store import AccessKeyRecord
from ..tool_common import _CAP_FORBIDDEN_MESSAGE, _tool_error

_LOGGER = logging.getLogger(__name__)

_MAX_CAMERA_DIMENSION = 2048
_MAX_CAMERA_IMAGE_BYTES = 4 * 1024 * 1024
_CAMERA_FETCH_TIMEOUT_SECONDS = 30
_ALLOWED_CAMERA_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif"})


def _image_size(data: bytes, mime_type: str) -> tuple[int, int] | None:
    """Read pixel dimensions from a PNG, GIF or JPEG header; None when unreadable."""
    if mime_type == "image/png" and len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if mime_type == "image/gif" and len(data) >= 10 and data[:4] == b"GIF8":
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if mime_type == "image/jpeg" and data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 <= len(data):
            if data[index] != 0xFF:
                return None
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            length = int.from_bytes(data[index + 2:index + 4], "big")
            # Start-of-frame markers carry height then width.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[index + 7:index + 9], "big"), int.from_bytes(data[index + 5:index + 7], "big")
            index += 2 + length
    return None


def _camera_denied(resource: str, outcome: str = "denied") -> tuple[dict, str, str]:
    """Return the uniform camera refusal without revealing camera existence."""
    return _tool_error("Entity not found."), outcome, resource


def _dimension(value: Any, name: str) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{name} must be a positive integer no greater than {_MAX_CAMERA_DIMENSION}."
    if value < 1 or value > _MAX_CAMERA_DIMENSION:
        return None, f"{name} must be a positive integer no greater than {_MAX_CAMERA_DIMENSION}."
    return value, None


async def _tool_get_camera_image(
    args: dict,
    access_key: AccessKeyRecord,
    hass: HomeAssistant,
    data: PhoenixData,
    request_id: str = "",
    client_ip: str | None = None,
) -> tuple[dict, str, str]:
    """MCP tool: retrieve one scoped camera image as an MCP image block."""
    if effective_cap(access_key, "cap_camera_read") != CAP_ALLOW:
        return _tool_error(_CAP_FORBIDDEN_MESSAGE), "denied", "get_camera_image"

    raw_entity_id = str_arg(args.get("entity_id"))
    if not raw_entity_id:
        return _camera_denied("get_camera_image", "not_found")

    entity_id = canonical_entity_id(raw_entity_id, hass)
    if not valid_entity_id(entity_id) or not entity_id.startswith("camera."):
        return _camera_denied(entity_id, "not_found")

    permission = resolve_read(entity_id, access_key, hass)
    if permission == Permission.NOT_FOUND:
        return _camera_denied(entity_id, "not_found")
    if permission not in (Permission.READ, Permission.WRITE):
        return _camera_denied(entity_id)

    settings = data.store.get_settings()
    if settings.mesa_mode != MESA_MODE_OFF:
        runtime = data.mesa
        if runtime is None:
            _LOGGER.warning("MESA runtime unavailable while reading camera %s", entity_id)
            return _camera_denied(entity_id)
        else:
            profile = runtime.resolver.resolve(entity_id)
            with request_context(access_key, request_id, client_ip):
                decision = runtime.enforcer.privacy.evaluate(
                    profile.privacy_classification,
                    build_caller_context(access_key, request_id or "get_camera_image"),
                    entity_id=entity_id,
                    is_person=profile.domain == "person",
                    is_minor=profile.person_traits.is_minor is True,
                )
            if not decision.allowed:
                return _camera_denied(entity_id)

    width, error = _dimension(args.get("width"), "width")
    if error:
        return _tool_error(error), "invalid_request", entity_id
    height, error = _dimension(args.get("height"), "height")
    if error:
        return _tool_error(error), "invalid_request", entity_id

    try:
        from homeassistant.components.camera import async_get_image  # noqa: PLC0415

        async with asyncio.timeout(_CAMERA_FETCH_TIMEOUT_SECONDS):
            image = await async_get_image(hass, entity_id, width=width, height=height)
        image_bytes = bytes(image.content)
        content_type = str(image.content_type or "").lower().split(";", 1)[0].strip()
    except Exception:  # noqa: BLE001 - camera integrations expose varied failures
        _LOGGER.debug("Camera image retrieval failed for %s", entity_id, exc_info=True)
        return _tool_error("Unable to retrieve the camera image."), "invalid_request", entity_id

    if not image_bytes:
        return _tool_error("Unable to retrieve the camera image."), "invalid_request", entity_id
    if len(image_bytes) > _MAX_CAMERA_IMAGE_BYTES:
        return _tool_error("The camera image is too large to return."), "invalid_request", entity_id
    if content_type not in _ALLOWED_CAMERA_MIME_TYPES:
        return _tool_error("The camera returned an unsupported image format."), "invalid_request", entity_id

    encoded = base64.b64encode(image_bytes).decode("ascii")
    metadata: dict[str, Any] = {
        "entity_id": entity_id,
        "mime_type": content_type,
        "bytes": len(image_bytes),
        "retrieved_at": dt_util.utcnow().isoformat(),
    }
    if (size := _image_size(image_bytes, content_type)) is not None:
        metadata["width"], metadata["height"] = size
    if width is not None:
        metadata["requested_width"] = width
    if height is not None:
        metadata["requested_height"] = height
    return {
        "content": [
            {"type": "text", "text": json.dumps(metadata, separators=(",", ":"), sort_keys=True)},
            {"type": "image", "data": encoded, "mimeType": content_type},
        ]
    }, "allowed", entity_id
