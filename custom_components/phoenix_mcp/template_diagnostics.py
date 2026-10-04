"""Bounded template failure descriptions without runtime values or Python tracebacks."""

from __future__ import annotations

import json

from jinja2 import TemplateError, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SecurityError

from .const import (
    TEMPLATE_DIAGNOSTIC_BYTES,
    TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS,
    TEMPLATE_DIAGNOSTIC_SOURCE_CHARS,
    TEMPLATE_DIAGNOSTIC_TRACEBACK_FRAMES,
    TEMPLATE_FAILURE_MESSAGES,
)


class TemplateOutputLimitError(ValueError):
    """The snapshot renderer exceeded its output budget."""


def _bounded_text(text: str, limit: int) -> str:
    """Keep diagnostics on one printable, bounded line."""
    clipped = len(text) > limit
    return "".join(c if c.isprintable() else " " for c in text[:limit]) + ("..." if clipped else "")


def describe_template_failure(exc: Exception) -> dict:
    """Classify a worker failure as a safe payload: a kind, a line, and for syntax errors a message.

    Runs in the worker without re-rendering or reading locals. Only compiler
    syntax messages are passed on; runtime exception text can hold state values.
    """
    line = None
    tb = exc.__traceback__
    for _ in range(TEMPLATE_DIAGNOSTIC_TRACEBACK_FRAMES):
        if tb is None:
            break
        if tb.tb_frame.f_code.co_filename == "<template>":
            line = tb.tb_lineno
        tb = tb.tb_next
    else:
        # A truncated walk cannot identify the innermost failure reliably.
        line = None

    if isinstance(exc, TemplateOutputLimitError):
        kind = "output"
    elif isinstance(exc, MemoryError):
        kind = "memory"
    elif isinstance(exc, RecursionError):
        kind = "recursion"
    elif isinstance(exc, TemplateSyntaxError):
        # Compiler messages describe caller source, never evaluated state values.
        return {
            "kind": "syntax",
            "message": _bounded_text(exc.message or "Invalid syntax.", TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS),
            "line": exc.lineno,
        }
    elif isinstance(exc, SecurityError):
        kind = "security"
    elif isinstance(exc, UndefinedError):
        kind = "undefined"
    elif line is not None and isinstance(exc, ZeroDivisionError):
        kind = "division"
    elif line is not None and isinstance(exc, ValueError):
        kind = "value"
    elif line is not None and isinstance(exc, TypeError):
        kind = "type"
    elif isinstance(exc, TemplateError):
        kind = "runtime"
    else:
        kind = "worker"
    # Never stringify a runtime exception: it can contain values or object reprs.
    return {"kind": kind, "line": line if kind not in {"worker", "output", "memory", "recursion"} else None}


def format_template_failure(payload: bytes, template: str) -> str:
    """Turn a worker diagnostic payload into a user-facing message.

    The payload is untrusted: anything oversized, malformed or carrying unknown
    keys collapses to the generic worker message, and the quoted source line is
    taken from the caller's own template, never from the payload.
    """
    fallback = TEMPLATE_FAILURE_MESSAGES["worker"]
    if len(payload) > TEMPLATE_DIAGNOSTIC_BYTES:
        return fallback
    try:
        result = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        return fallback
    if not isinstance(result, dict) or set(result) - {"kind", "message", "line"}:
        return fallback
    kind = result.get("kind")
    if not isinstance(kind, str) or kind not in TEMPLATE_FAILURE_MESSAGES:
        return fallback
    message = TEMPLATE_FAILURE_MESSAGES[kind]
    if kind == "syntax":
        detail = result.get("message")
        if not isinstance(detail, str) or len(detail) > TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS + 3:
            return fallback
        message += ": " + _bounded_text(detail, TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS)
    elif "message" in result:
        return fallback
    line = result.get("line")
    # Jinja normalizes CRLF and CR, but not other Unicode line separators.
    lines = template.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if type(line) is int and 0 < line <= len(lines) and kind not in {"worker", "output", "memory", "recursion"}:
        source = _bounded_text(lines[line - 1], TEMPLATE_DIAGNOSTIC_SOURCE_CHARS)
        message += f"\nLine {line}: {source}"
    return message
