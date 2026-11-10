"""Refuse a function-local Home Assistant component import while the component is not loaded.

Importing a component that Home Assistant has not loaded reads it from disk, which must not
happen on the event loop. A loaded component is already in sys.modules, so its import costs
nothing. Every function-local component import in Phoenix MCP calls require_component first
or checks hass.config.components itself, as tests/test_component_import_guard.py enforces;
imported_module serves code that may run before the component is set up.
"""

from __future__ import annotations

import sys
from types import ModuleType

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError


class ComponentNotLoaded(HomeAssistantError, ImportError):
    """The component is not loaded, so it was not imported.

    Both bases are deliberate: a caller already treating a missing module (ImportError) or an
    unavailable integration (HomeAssistantError) as "not available" keeps working unchanged.
    """


def require_component(hass: HomeAssistant, domain: str) -> None:
    """Raise ComponentNotLoaded unless Home Assistant has loaded `domain`."""
    if domain not in hass.config.components:
        raise ComponentNotLoaded(f"The {domain} integration is not loaded.")


def imported_module(name: str) -> ModuleType:
    """Return a component module only if it is already imported, never importing it.

    For a caller that cannot await, where the component may be in use without being set
    up, as a config flow imports its platform first. An async caller that needs the module
    either way imports it beforehand with Home Assistant's async_import_module, which reads
    from disk in the executor.
    """
    module = sys.modules.get(name)
    if module is None:
        raise ComponentNotLoaded(f"{name} is not imported.")
    return module
