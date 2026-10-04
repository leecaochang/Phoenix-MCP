"""Disposable template worker; receives only scrubbed state JSON on standard input."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import resource
import runpy
import sys


def main() -> None:
    """Apply OS limits, then render the template read from stdin.

    RLIMIT_CPU and RLIMIT_AS are set before importing HA or touching caller input.
    Exit 70 means startup failed (one stderr line, no caller data); the
    diagnostic exit code means the render failed, with a bounded JSON diagnostic
    on stdout.
    """
    phase = "resource_limits"
    try:
        # Run by filename so spawn works independently of HA's process entry point.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        # Load the stdlib-only constants without executing the integration package,
        # whose __init__ imports Home Assistant before the worker limits are active.
        limits = runpy.run_path(str(Path(__file__).with_name("const.py")))
        cpu_seconds = limits["TEMPLATE_WORKER_CPU_SECONDS"]
        memory_bytes = limits["TEMPLATE_WORKER_MEMORY_BYTES"]
        input_bytes = limits["TEMPLATE_WORKER_INPUT_BYTES"]
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        phase = "imports"
        from homeassistant.util import dt as dt_util
        from custom_components.phoenix_mcp.helpers import ScrubbedState, render_template_snapshot
        from custom_components.phoenix_mcp.template_diagnostics import describe_template_failure

    except Exception as exc:
        # No caller input has been read: emit only failure type and missing module.
        detail = f"{phase}: {type(exc).__name__}"
        if isinstance(exc, OSError):
            detail += f" errno={exc.errno} reason={exc.strerror}"
        if isinstance(exc, ImportError):
            detail += f" module={exc.name}"
        sys.stderr.write(detail + "\n")
        raise SystemExit(70) from None

    # HA's undefined-variable logger can contain runtime values. Diagnostics use
    # the private stdout protocol; do not emit render logs or Python tracebacks.
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        raw = sys.stdin.buffer.read(input_bytes + 1)
        if len(raw) > input_bytes:
            raise ValueError("Worker input exceeded its limit.")
        payload = json.loads(raw)
        time_zone = dt_util.get_time_zone(payload["time_zone"])
        if time_zone is None:
            raise ValueError("Invalid Home Assistant time zone.")
        dt_util.set_default_time_zone(time_zone)
        permitted = {}
        for row in payload["states"]:
            state = ScrubbedState.from_dict(row)
            permitted[state.entity_id] = state
        rendered = render_template_snapshot(payload["template"], permitted)
    except Exception as exc:
        diagnostic = json.dumps(describe_template_failure(exc), ensure_ascii=False)
        if len(diagnostic.encode()) > limits["TEMPLATE_DIAGNOSTIC_BYTES"]:
            diagnostic = '{"kind":"worker"}'
        sys.stdout.write(diagnostic)
        raise SystemExit(limits["TEMPLATE_WORKER_DIAGNOSTIC_EXIT"]) from None
    finally:
        logging.disable(previous_logging_disable)
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
