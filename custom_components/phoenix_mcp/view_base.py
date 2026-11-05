"""Typed base class for Phoenix MCP's aiohttp views.

`HomeAssistantView` does not declare `hass`, and HA's `register_view` never sets
it (it only checks `url`/`name`, then calls `view.register(hass, ...)`). Phoenix
assigns it at registration (`view = view_cls(); view.hass = hass;
hass.http.register_view(view)`), which a type checker cannot see, so every
`self.hass` in the view modules would read as an undeclared attribute.

`hass: HomeAssistant` is an ANNOTATION only: no class attribute is created,
`hasattr` is unchanged, and there is no runtime behaviour. Views inherit
everything else from `HomeAssistantView`. Kept apart from `helpers.py` so every
view module (admin_view, agentcli_views, mcp_transport, skill_view) can import it.
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
    """Return a JSON error response. Uses request_id if supplied, else generates one.

    Phoenix-authored operator text must carry `key` and optional `params`.
    `passthrough=True` is reserved for diagnostic text authored by an upstream
    provider, tool, or mesa-core. Requiring one classification at runtime keeps
    a newly added bare English message from silently bypassing localization.
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
