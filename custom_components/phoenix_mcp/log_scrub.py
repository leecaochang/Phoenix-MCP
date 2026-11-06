"""Scrub the tracebacks Phoenix logs, keeping them readable for an issue report.

A logger filter runs only for records logged on that logger, so it would have to
be attached to every module's logger. A handler on the package logger sees every
child record before the root handlers do, because `Logger.callHandlers` walks up
from the logger that logged it. Formatters reuse a record's `exc_text`, so setting
it here is what every later handler writes, Home Assistant's log file and its
system log included.
"""

from __future__ import annotations

import logging
import traceback

from .redaction import _scrub_log_text


class TracebackScrubHandler(logging.Handler):
    """Writes nothing itself; replaces a record's traceback text with its scrubbed form."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_text is not None or not record.exc_info or record.exc_info[0] is None:
            return
        # A traceback that cannot be formatted is left to the next handler's own error handling.
        text = "".join(traceback.format_exception(*record.exc_info))
        record.exc_text = _scrub_log_text(text.rstrip("\n"))


def install_traceback_scrub(logger: logging.Logger) -> None:
    """Attach the handler to the package logger once."""
    if not any(isinstance(handler, TracebackScrubHandler) for handler in logger.handlers):
        logger.addHandler(TracebackScrubHandler())
