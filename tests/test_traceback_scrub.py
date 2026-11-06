"""Tracebacks from any Phoenix module reach Home Assistant's log handlers scrubbed, and stay tracebacks."""

from __future__ import annotations

import logging
import queue
from collections.abc import Iterator

import pytest
from homeassistant.components.system_log import LogEntry
from homeassistant.util.logging import HomeAssistantQueueHandler

from custom_components.phoenix_mcp.log_scrub import TracebackScrubHandler, install_traceback_scrub

KEY = "phx_" + "a" * 64
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl"
PACKAGE = "custom_components.phoenix_mcp"


class _Capture(logging.Handler):
    """Stands where a root handler does and keeps what it formats."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self.lines: list[str] = []
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.lines.append(self.format(record))


@pytest.fixture
def capture() -> Iterator[_Capture]:
    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def _fail(logger: logging.Logger) -> None:
    try:
        raise RuntimeError(f"upstream said {KEY} and {JWT} at https://user:secret@example.test/x?token=abc")
    except RuntimeError:
        logger.exception("Step failed")


def test_importing_the_package_attaches_the_handler_once() -> None:
    logger = logging.getLogger(PACKAGE)
    def attached() -> int:
        return sum(isinstance(handler, TracebackScrubHandler) for handler in logger.handlers)

    assert attached() == 1
    install_traceback_scrub(logger)
    assert attached() == 1


@pytest.mark.parametrize("name", [f"{PACKAGE}.tools.calendar", f"{PACKAGE}.a_logger_created_later", PACKAGE])
def test_a_child_traceback_is_scrubbed_and_kept(capture: _Capture, name: str) -> None:
    _fail(logging.getLogger(name))
    line = capture.lines[-1]
    assert KEY not in line and JWT not in line and "user:secret" not in line and "token=abc" not in line
    assert "<phoenix-access-key>" in line and "Traceback (most recent call last)" in line
    assert "RuntimeError" in line and "in _fail" in line


def test_other_integrations_are_not_touched(capture: _Capture) -> None:
    _fail(logging.getLogger("custom_components.something_else"))
    assert KEY in capture.lines[-1]


def test_a_record_without_a_traceback_is_left_alone(capture: _Capture) -> None:
    logging.getLogger(f"{PACKAGE}.tools.calendar").warning("Plain %s", "text")
    assert capture.lines[-1] == "WARNING Plain text"
    assert capture.records[-1].exc_text is None


def test_home_assistants_queue_and_system_log_get_the_scrubbed_text(capture: _Capture) -> None:
    """The file log formats on the queue handler; the system log reads exc_text."""
    logger = logging.getLogger(f"{PACKAGE}.tools.calendar")
    _fail(logger)
    record = capture.records[-1]
    pending: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    handler = HomeAssistantQueueHandler(pending)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.handle(record)
    assert KEY not in pending.get_nowait().getMessage()
    entry = LogEntry(record, [], logging.Formatter("%(message)s"))
    assert entry.exception and KEY not in entry.exception and "Traceback" in entry.exception
