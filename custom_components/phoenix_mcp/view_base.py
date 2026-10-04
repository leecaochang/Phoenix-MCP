"""Typed base class for Phoenix MCP's aiohttp views.

`HomeAssistantView` does not declare `hass`, and HA's `register_view` never sets
it (it only checks `url`/`name`, then calls `view.register(hass, ...)`). Phoenix
assigns it at registration (`view = view_cls(); view.hass = hass;
hass.http.register_view(view)`), which a type checker cannot see, so every
`self.hass` in the view modules would read as an undeclared attribute.

`hass: HomeAssistant` is an ANNOTATION only: no class attribute is created,
`hasattr` is unchanged, and there is no runtime behaviour. Views inherit
everything else from `HomeAssistantView`. Kept apart from `helpers.py` so every
view module (admin_view, agentcli, mcp_view, skill_view) can import it without
an import cycle.
"""

from __future__ import annotations

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant


class PhoenixView(HomeAssistantView):
    """A HomeAssistantView that declares the `hass` attribute Phoenix assigns."""

    hass: HomeAssistant
