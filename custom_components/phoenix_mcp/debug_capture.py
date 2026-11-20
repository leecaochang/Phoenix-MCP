"""Bounded capture of INFO and DEBUG records from integrations Phoenix raised.

Home Assistant's system log keeps WARNING and above only, so an integration set
to DEBUG through set_integration_log_level would otherwise produce output no tool
can read. The handler is attached only to the loggers of integrations whose level
set_integration_log_level raised to INFO or DEBUG, and is removed, with what it
kept for that integration, when the level is lowered or a timed override ends.
Records of any other integration are never captured.

A record is readable only by the access keys that had raised its integration when it
was written: debug output can hold other clients' requests and devices outside a
reader's tree, which the scrub and entity scope applied on read cannot remove. A key
revoked or archived stops owning its captures, and an integration nobody owns any
more stops being captured.

A capture lasts only while Home Assistant still holds the setting Phoenix applied. Any
change made outside Phoenix (the Logger settings, the logger actions, YAML) ends it: a
later raise by someone else must never feed an old owner. Home Assistant's stored setting
is checked by identity on every record and every read, and the raised loggers' levels
every DEBUG_CAPTURE_RECHECK_SECONDS, since the logger actions change levels without
touching the stored setting.
"""

from __future__ import annotations

import itertools
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import DEBUG_CAPTURE_CAPACITY, DEBUG_CAPTURE_MAX_CHARS, DEBUG_CAPTURE_RECHECK_SECONDS, DOMAIN

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


# (sequence, created, level, logger, message, truncated, owners)
CapturedRecord = tuple[int, float, str, str, str, bool, frozenset[str]]
# Home Assistant's stored logger settings and the setting object Phoenix applied for one integration.
SettingWitness = tuple[dict[str, Any], Any]
# The witness (None where Home Assistant's settings could not be read) and each raised logger's level.
_Applied = tuple[SettingWitness | None, dict[str, int]]


def _setting_moved(domain: str, witness: SettingWitness | None) -> bool:
    return witness is not None and witness[0].get(domain) is not witness[1]


class DebugCapture(logging.Handler):
    """Keep the newest INFO/DEBUG records of the integrations Phoenix raised, by raising key."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: deque[CapturedRecord] = deque(maxlen=DEBUG_CAPTURE_CAPACITY)
        self._sequence = itertools.count(1)
        self.attached_at = datetime.now(timezone.utc)
        # Replaced, never mutated, so a logging thread reads one consistent set.
        self._owners: dict[str, frozenset[str]] = {}
        self._applied: dict[str, _Applied] = {}
        self.unsubscribe_recheck: Callable[[], None] | None = None

    @property
    def domains(self) -> set[str]:
        """The integrations being captured."""
        return set(self._owners)

    def captures_for(self, domain: str, owner: str) -> bool:
        """Whether `owner` is capturing this integration's records now."""
        return owner in self._owners.get(domain, frozenset())

    def domains_for(self, owner: str) -> set[str]:
        """The integrations `owner` is capturing now."""
        return {domain for domain, owners in self._owners.items() if owner in owners}

    def start(self, domain: str, owner: str, level: str = "DEBUG", witness: SettingWitness | None = None) -> None:
        """Capture this integration's records for `owner` from now on. Event loop only.

        `witness` is the setting Phoenix just applied; it and the loggers now at `level` or
        chattier are what a later outside change is detected against.
        """
        if domain == DOMAIN:
            return
        threshold = logging.getLevelName(level)
        raised = {logger.name: logger.getEffectiveLevel() for logger in _domain_loggers(domain)
                  if logger.getEffectiveLevel() <= threshold}
        self._applied = {**self._applied, domain: (witness, raised)}
        current = self._owners.get(domain)
        self._owners = {**self._owners, domain: (current or frozenset()) | {owner}}
        if current is None:
            for logger in _domain_loggers(domain):
                logger.addHandler(self)

    def stop(self, domain: str) -> None:
        """Stop capturing this integration and drop what was kept for it. Event loop only."""
        if domain not in self._owners:
            return
        self._owners = {name: owners for name, owners in self._owners.items() if name != domain}
        self._applied = {name: applied for name, applied in self._applied.items() if name != domain}
        for logger in _domain_loggers(domain):
            logger.removeHandler(self)
        self._keep(lambda row: integration_domain(row[3]) != domain)

    def forget(self, owner: str) -> None:
        """End every capture `owner` holds and drop records only it could read. Event loop only."""
        for domain, owners in list(self._owners.items()):
            if owner not in owners:
                continue
            if owners == {owner}:
                self.stop(domain)
            else:
                self._owners = {**self._owners, domain: owners - {owner}}
        self._keep(lambda row: bool(row[6] - {owner}))

    def reconcile(self) -> set[str]:
        """End every capture whose level changed outside Phoenix; return those domains. Event loop only."""
        moved = {domain for domain, (witness, raised) in self._applied.items()
                 if _setting_moved(domain, witness)
                 or any(logging.getLogger(name).getEffectiveLevel() != value for name, value in raised.items())}
        for domain in moved:
            self.stop(domain)
        return moved

    def stop_all(self) -> None:
        """Detach from every integration and drop all records. Event loop only."""
        for domain in list(self._owners):
            self.stop(domain)
        self.records.clear()

    def _keep(self, keep: Callable[[CapturedRecord], bool]) -> None:
        self.acquire()
        try:
            kept = [row for row in self.records if keep(row)]
            self.records.clear()
            self.records.extend(kept)
        finally:
            self.release()

    def emit(self, record: logging.LogRecord) -> None:
        # WARNING and above already reach the system log; keep only what it drops.
        # A record propagating from a child logger is checked against the set too,
        # so a domain stopped while a record is in flight is never kept.
        domain = integration_domain(record.name) or ""
        owners = self._owners.get(domain)
        if record.levelno >= logging.WARNING or record.name.startswith(_EXCLUDED) or not owners:
            return
        # A setting Phoenix did not apply is someone else's raise; the next check ends the capture.
        applied = self._applied.get(domain)
        if applied is not None and _setting_moved(domain, applied[0]):
            return
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not break logging
            message = str(record.msg)
        truncated = len(message) > DEBUG_CAPTURE_MAX_CHARS
        self.records.append((next(self._sequence), record.created, record.levelname, record.name,
                             message[:DEBUG_CAPTURE_MAX_CHARS], truncated, owners))

    def _snapshot(self) -> list[CapturedRecord]:
        # Logging threads append while this runs on the event loop.
        self.acquire()
        try:
            return list(self.records)
        finally:
            self.release()

    def retained_for(self, owner: str) -> int:
        """How many kept records `owner` may read."""
        return sum(owner in row[6] for row in self._snapshot())

    def newest(self, domains: set[str], before: int | None, owner: str) -> list[CapturedRecord]:
        """Records of visible domains that `owner` captured, newest first, older than `before`.

        Callers scrub before searching, so a search can never test raw text.
        """
        return [row for row in reversed(self._snapshot())
                if (before is None or row[0] < before) and owner in row[6]
                and integration_domain(row[3]) in domains]


def async_attach_debug_capture(hass: HomeAssistant) -> DebugCapture:
    """Create the process-wide capture once and return it; it captures nothing yet."""
    existing = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    if isinstance(existing, DebugCapture):
        return existing
    handler = DebugCapture()
    hass.data[DEBUG_CAPTURE_RUNTIME_KEY] = handler
    return handler


def _sync_recheck(hass: HomeAssistant, handler: DebugCapture) -> None:
    """Run the periodic level check only while something is captured."""
    if handler.domains and handler.unsubscribe_recheck is None:
        @callback
        def recheck(_now: datetime) -> None:
            async_reconcile_debug_capture(hass)

        handler.unsubscribe_recheck = async_track_time_interval(
            hass, recheck, timedelta(seconds=DEBUG_CAPTURE_RECHECK_SECONDS), cancel_on_shutdown=True)
    elif not handler.domains and handler.unsubscribe_recheck is not None:
        handler.unsubscribe_recheck()
        handler.unsubscribe_recheck = None


def async_update_debug_capture(
    hass: HomeAssistant, domain: str, level: str | None, owner: str | None = None,
    witness: SettingWitness | None = None,
) -> None:
    """Capture `domain` for `owner` while Phoenix holds it at INFO or DEBUG, and stop otherwise."""
    handler = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    if not isinstance(handler, DebugCapture):
        return
    if level in ("DEBUG", "INFO"):
        if owner is not None:
            handler.start(domain, owner, level, witness)
    else:
        handler.stop(domain)
    _sync_recheck(hass, handler)


def async_reconcile_debug_capture(hass: HomeAssistant) -> None:
    """End the captures whose level changed outside Phoenix. Event loop only."""
    handler = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    if isinstance(handler, DebugCapture):
        handler.reconcile()
        _sync_recheck(hass, handler)


def async_forget_debug_capture_owner(hass: HomeAssistant, owner: str) -> None:
    """End what a revoked or archived access key captured. Event loop only."""
    handler = hass.data.get(DEBUG_CAPTURE_RUNTIME_KEY)
    if isinstance(handler, DebugCapture):
        handler.forget(owner)
        _sync_recheck(hass, handler)


def async_detach_debug_capture(hass: HomeAssistant) -> None:
    """Remove the handler from every integration and drop what it kept."""
    handler: Any = hass.data.pop(DEBUG_CAPTURE_RUNTIME_KEY, None)
    if isinstance(handler, DebugCapture):
        handler.stop_all()
        _sync_recheck(hass, handler)
