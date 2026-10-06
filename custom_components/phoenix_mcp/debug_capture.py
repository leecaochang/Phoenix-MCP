"""Bounded capture of integration INFO and DEBUG records.

Home Assistant's system log keeps WARNING and above only, so an integration set
to DEBUG through set_integration_log_level would otherwise produce output no tool
can read. One handler on the two integration logger namespaces keeps the most
recent lower-level records in memory. Nothing is captured until a level is
raised, Phoenix MCP's own records are excluded, and reads apply the strict
diagnostic scrub plus the reading access key's entity scope.
"""

from __future__ import annotations

import itertools
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DEBUG_CAPTURE_CAPACITY, DEBUG_CAPTURE_MAX_CHARS, DOMAIN

DEBUG_CAPTURE_RUNTIME_KEY = "phoenix_mcp_debug_capture"
_NAMESPACES = ("homeassistant.components", "custom_components")
_EXCLUDED = tuple(f"{namespace}.{DOMAIN}" for namespace in _NAMESPACES)


def integration_domain(logger_name: str) -> str | None:
    """Return the integration domain a logger belongs to, or None."""
    for namespace in _NAMESPACES:
        prefix = namespace + "."
        if logger_name.startswith(prefix):
            return logger_name[len(prefix):].split(".", 1)[0] or None
    return None


class DebugCapture(logging.Handler):
    """Keep the newest INFO/DEBUG records from integration loggers."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: deque[tuple[int, float, str, str, str, bool]] = deque(maxlen=DEBUG_CAPTURE_CAPACITY)
        self._sequence = itertools.count(1)
        self.attached_at = datetime.now(timezone.utc)

    def emit(self, record: logging.LogRecord) -> None:
        # WARNING and above already reach the system log; keep only what it drops.
        if record.levelno >= logging.WARNING or record.name.startswith(_EXCLUDED):
            return
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not break logging
            message = str(record.msg)
        truncated = len(message) > DEBUG_CAPTURE_MAX_CHARS
        self.records.append((next(self._sequence), record.created, record.levelname, record.name,
                             message[:DEBUG_CAPTURE_MAX_CHARS], truncated))

    def newest(self, domains: set[str], before: int | None) -> list[tuple[int, float, str, str, str, bool]]:
        """Records of visible domains, newest first, strictly older than `before`.

        Callers scrub before searching, so a search can never test raw text.
        """
        # Logging threads append while this runs on the event loop.
        self.acquire()
        try:
            snapshot = list(self.records)
        finally:
            self.release()
        return [row for row in reversed(snapshot)
                if (before is None or row[0] < before) and integration_domain(row[3]) in domains]


def async_attach_debug_capture(hass: HomeAssistant) -> DebugCapture:
    """Attach the process-wide handler once and return it."""
    existing = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    if isinstance(existing, DebugCapture):
        return existing
    handler = DebugCapture()
    for namespace in _NAMESPACES:
        logging.getLogger(namespace).addHandler(handler)
    hass.data[DEBUG_CAPTURE_RUNTIME_KEY] = handler
    return handler


def async_detach_debug_capture(hass: HomeAssistant) -> None:
    """Remove the handler and drop what it kept."""
    handler: Any = hass.data.pop(DEBUG_CAPTURE_RUNTIME_KEY, None)
    if isinstance(handler, DebugCapture):
        for namespace in _NAMESPACES:
            logging.getLogger(namespace).removeHandler(handler)
        handler.records.clear()
