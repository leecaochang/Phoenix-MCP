"""Typed base class for Phoenix MCP's aiohttp views.

HomeAssistantView declares no ``hass`` and Phoenix assigns it at registration, so the
annotation here satisfies type checking and has no runtime effect. Kept apart from
``helpers.py`` so every view module can import it.
"""

from __future__ import annotations

from aiohttp import web

import uuid

import json

from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant


class PhoenixView(HomeAssistantView):
    """A HomeAssistantView that declares the `hass` attribute Phoenix assigns."""

    hass: HomeAssistant


def _err(
    code: str,
    message: str,
    status: int,
    request_id: str = "",
    key: str | None = None,
    params: dict | None = None,
    *,
    passthrough: bool = False,
) -> web.Response:
    """Return a JSON error response, generating a request ID when none is supplied.

    Phoenix-authored text must carry ``key`` and optional ``params``; ``passthrough=True`` is
    reserved for upstream diagnostic text.
    """
    if bool(key) == passthrough:
        raise ValueError("_err requires exactly one of key or passthrough=True")
    rid = request_id or str(uuid.uuid4())
    body: dict[str, Any] = {"error": code, "message": message}
    if key:
        body["message_key"] = f"adminError.{key}"
        if params:
            body["message_params"] = params
    else:
        body["message_passthrough"] = True
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body),
        # Every admin response reflects live, frequently-mutating state (access keys,
        # approvals, audit); without this a browser or intermediary can serve a
        # stale GET on a plain page refresh, e.g. an approval resolved elsewhere
        # still reading "pending" after reloading the Approvals tab.
        headers={"X-Phoenix-Request-ID": rid, "Cache-Control": "no-store"},
    )
