"""Disposable template worker; receives only scrubbed state JSON on standard input."""

from __future__ import annotations

import json
from pathlib import Path
import resource
import runpy
import sys


def main() -> None:
    """Enforce OS resource limits before importing HA or compiling caller input."""
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

    except Exception as exc:
        # No caller input has been read: emit only failure type and missing module.
        detail = f"{phase}: {type(exc).__name__}"
        if isinstance(exc, OSError):
            detail += f" errno={exc.errno} reason={exc.strerror}"
        if isinstance(exc, ImportError):
            detail += f" module={exc.name}"
        sys.stderr.write(detail + "\n")
        raise SystemExit(70) from None

    payload = json.loads(sys.stdin.buffer.read(input_bytes + 1))
    time_zone = dt_util.get_time_zone(payload["time_zone"])
    if time_zone is None:
        raise ValueError("Invalid Home Assistant time zone.")
    dt_util.set_default_time_zone(time_zone)
    permitted = {}
    for row in payload["states"]:
        state = ScrubbedState.from_dict(row)
        permitted[state.entity_id] = state
    sys.stdout.write(render_template_snapshot(payload["template"], permitted))


if __name__ == "__main__":
    main()
