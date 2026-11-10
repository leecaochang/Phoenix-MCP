"""Bounded capture of INFO and DEBUG records from integrations Phoenix raised.

Home Assistant's system log keeps WARNING and above only, so an integration set
to DEBUG through set_integration_log_level would otherwise produce output no tool
can read. The handler is attached only to the loggers of integrations whose level
set_integration_log_level raised to INFO or DEBUG, and is removed, with what it
kept for that integration, when the level is lowered or a timed override ends.
Records of any other integration are never captured, and reads apply the strict
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


def _domain_loggers(domain: str) -> list[logging.Logger]:
    return [logging.getLogger(f"{namespace}.{domain}") for namespace in _NAMESPACES]


class DebugCapture(logging.Handler):
    """Keep the newest INFO/DEBUG records of the integrations Phoenix raised."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: deque[tuple[int, float, str, str, str, bool]] = deque(maxlen=DEBUG_CAPTURE_CAPACITY)
        self._sequence = itertools.count(1)
        self.attached_at = datetime.now(timezone.utc)
        self.domains: set[str] = set()

    def start(self, domain: str) -> None:
        """Capture this integration's records from now on. Event loop only."""
        if domain == DOMAIN or domain in self.domains:
            return
        self.domains.add(domain)
        for logger in _domain_loggers(domain):
            logger.addHandler(self)

    def stop(self, domain: str) -> None:
        """Stop capturing this integration and drop what was kept for it. Event loop only."""
        if domain not in self.domains:
            return
        self.domains.discard(domain)
        for logger in _domain_loggers(domain):
            logger.removeHandler(self)
        self.acquire()
        try:
            kept = [row for row in self.records if integration_domain(row[3]) != domain]
            self.records.clear()
            self.records.extend(kept)
        finally:
            self.release()

    def stop_all(self) -> None:
        """Detach from every integration and drop all records. Event loop only."""
        for domain in list(self.domains):
            self.stop(domain)
        self.records.clear()

    def emit(self, record: logging.LogRecord) -> None:
        # WARNING and above already reach the system log; keep only what it drops.
        # A record propagating from a child logger is checked against the set too,
        # so a domain stopped while a record is in flight is never kept.
        if (record.levelno >= logging.WARNING or record.name.startswith(_EXCLUDED)
                or integration_domain(record.name) not in self.domains):
            return
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not break logging
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
    """Create the process-wide capture once and return it; it captures nothing yet."""
    existing = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    if isinstance(existing, DebugCapture):
        return existing
    handler = DebugCapture()
    hass.data[DEBUG_CAPTURE_RUNTIME_KEY] = handler
    return handler


def async_update_debug_capture(hass: HomeAssistant, domain: str, level: str | None) -> None:
    """Capture `domain` while Phoenix holds it at INFO or DEBUG, and stop otherwise."""
    handler = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    if not isinstance(handler, DebugCapture):
        return
    if level in ("DEBUG", "INFO"):
        handler.start(domain)
    else:
        handler.stop(domain)


def async_detach_debug_capture(hass: HomeAssistant) -> None:
    """Remove the handler from every integration and drop what it kept."""
    handler: Any = hass.data.pop(DEBUG_CAPTURE_RUNTIME_KEY, None)
    if isinstance(handler, DebugCapture):
        handler.stop_all()
