"""Disposable template worker; receives only scrubbed state JSON on standard input.

The worker is a resource and state boundary, not an OS sandbox: it runs as Home Assistant's
user with its file and network access, and Home Assistant's Jinja sandbox is the only barrier
between a template and those.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
from pathlib import Path
import resource
import runpy
import sys
import time
from typing import Any


def _check_environment() -> Any:
    """A hass-less environment that knows every filter and test name and never folds an unknown one.

    Home Assistant's own environment has filters the hass-less one lacks; here each is a
    context stub that raises when called, so compiling goes as far as Home Assistant would.
    """
    # Local: the worker imports Home Assistant only after its limits apply.
    from jinja2 import pass_context
    from homeassistant.exceptions import TemplateError
    from homeassistant.helpers.template import TemplateEnvironment

    class _AnyName(dict):
        def __missing__(self, name: str) -> Any:
            @pass_context
            def unavailable(*args: Any, **kwargs: Any) -> Any:
                raise TemplateError("not available while checking")
            return unavailable

        def get(self, name: Any, default: Any = None) -> Any:
            return self[name]

    env = TemplateEnvironment(None)
    env.filters = _AnyName(env.filters)
    env.tests = _AnyName(env.tests)
    env.code_generator_class = _exhaustion_aware_code_generator(env.code_generator_class)
    return env


class FoldOutOfMemory(Exception):
    """A constant fold ran out of memory, which Jinja would otherwise treat as not foldable."""


def _out_of_memory(err: BaseException) -> bool:
    """Whether a MemoryError is anywhere in the cause or context chain of ``err``."""
    seen: set[int] = set()
    current: BaseException | None = err
    while current is not None and id(current) not in seen:
        if isinstance(current, MemoryError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _exhaustion_aware_code_generator(base: Any) -> Any:
    """``base`` with Jinja's constant folder replaced by one that reports a fold out of memory.

    Jinja's ``as_const`` turns every exception, MemoryError included, into ``Impossible``, so
    under the worker's memory limit a fold Home Assistant would complete with gigabytes costs
    nothing here. ``generic_visit`` repeats Jinja's ``Optimizer.generic_visit`` otherwise.
    """
    # Local: the worker imports Home Assistant and Jinja only after its limits apply.
    from jinja2 import nodes
    from jinja2.optimizer import Optimizer
    from jinja2.visitor import NodeTransformer

    class _Folder(Optimizer):
        def generic_visit(self, node: Any, *args: Any, **kwargs: Any) -> Any:
            node = NodeTransformer.generic_visit(self, node, *args, **kwargs)
            if isinstance(node, nodes.Expr):
                try:
                    return nodes.Const.from_untrusted(
                        node.as_const(args[0] if args else None),
                        lineno=node.lineno, environment=self.environment)
                except nodes.Impossible as err:
                    if _out_of_memory(err):
                        raise FoldOutOfMemory from None
            return node

    class _Generator(base):
        def __init__(self, environment: Any, *args: Any, **kwargs: Any) -> None:
            super().__init__(environment, *args, **kwargs)
            if getattr(self, "optimizer", None) is not None:
                self.optimizer: Any = _Folder(environment)

    return _Generator


def _check(templates: list[str]) -> dict:
    """Compile each template as Home Assistant would, then trial-render it.

    Reports the compile CPU of the slowest single template, and ``exhausted`` when compiling or
    trial-rendering one ran out of memory, which Home Assistant's unlimited process would not.
    Any other compile or trial-render failure is Home Assistant's to report.
    """
    env = _check_environment()
    compiled = []
    slowest = 0.0
    for source in templates:
        started = time.process_time()
        try:
            compiled.append(env.from_string(source))
        except (FoldOutOfMemory, MemoryError):
            return {"compile_seconds": time.process_time() - started, "exhausted": True}
        except Exception as err:  # Home Assistant reports the same failure from its own compile
            if _out_of_memory(err):
                return {"compile_seconds": time.process_time() - started, "exhausted": True}
        slowest = max(slowest, time.process_time() - started)
    for template in compiled:
        try:
            for _ in template.generate({}):
                pass
        except MemoryError:
            return {"compile_seconds": slowest, "exhausted": True}
        except Exception as err:  # a trial render without its runtime variables may fail
            if _out_of_memory(err):
                return {"compile_seconds": slowest, "exhausted": True}
    return {"compile_seconds": slowest}


def _evaluate(payload: dict, permitted: dict, limits: dict) -> dict:
    """Render each requested template over the snapshot with the caller's plain-data variables.

    A per-render ``state`` names a snapshot entity bound to the ``state`` variable, as a
    numeric_state value_template sees it. Undefined-name warnings are collected per render.
    """
    # Local: the worker imports Home Assistant only after its limits apply.
    from homeassistant.helpers.template import make_logging_undefined
    from custom_components.phoenix_mcp.template_sandbox import render_template_snapshot, safe_template_env
    from custom_components.phoenix_mcp.template_diagnostics import describe_template_failure

    env = safe_template_env()
    previous_undefined = env.undefined
    results: list[dict] = []
    warnings: list[str] = []

    def capture(level: int, message: str) -> None:
        if len(warnings) < limits["TEMPLATE_WARNINGS_PER_RENDER"]:
            warnings.append(str(message)[:limits["TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS"]])

    env.undefined = make_logging_undefined(False, capture)
    try:
        variables = payload.get("variables") or {}
        for render in payload["renders"]:
            warnings = []
            extra = dict(variables)
            if render.get("state") is not None:
                extra["state"] = permitted.get(render["state"])
            try:
                result: dict = {"output": render_template_snapshot(render["template"], permitted, extra)}
            except Exception as exc:
                result = {"failure": describe_template_failure(exc)}
            if warnings:
                result["warnings"] = warnings
            results.append(result)
    finally:
        env.undefined = previous_undefined
    return {"results": results}


def _renders(templates: list[str], permitted: dict) -> dict:
    """Render each template as the render mode does, with its own output or failure."""
    # Local: the worker imports Home Assistant only after its limits apply.
    from custom_components.phoenix_mcp.template_sandbox import render_template_snapshot
    from custom_components.phoenix_mcp.template_diagnostics import describe_template_failure

    results: list[dict] = []
    for template in templates:
        try:
            results.append({"output": render_template_snapshot(template, permitted)})
        except Exception as exc:
            results.append({"failure": describe_template_failure(exc)})
    return {"results": results}


def run_payload(raw: bytes, limits: dict) -> tuple[int, str]:
    """Run one worker request and return (exit code, stdout text).

    ``mode`` is render (the default: one template, rendered text out), renders (several of
    those, JSON out), evaluate (several renders with variables, JSON out) or check (compile and
    trial-render, JSON out).
    """
    # Local: the worker imports Home Assistant only after its limits apply.
    from homeassistant.util import dt as dt_util
    from custom_components.phoenix_mcp.template_sandbox import ScrubbedState, render_template_snapshot
    from custom_components.phoenix_mcp.template_diagnostics import describe_template_failure

    # HA's undefined-variable logger can contain runtime values. Diagnostics use
    # the private stdout protocol; do not emit render logs or Python tracebacks.
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        if len(raw) > limits["TEMPLATE_WORKER_INPUT_BYTES"]:
            raise ValueError("Worker input exceeded its limit.")
        payload = json.loads(raw)
        mode = payload.get("mode", "render")
        if mode == "check":
            return 0, json.dumps(_check(payload["templates"]))
        time_zone = dt_util.get_time_zone(payload["time_zone"])
        if time_zone is None:
            raise ValueError("Invalid Home Assistant time zone.")
        dt_util.set_default_time_zone(time_zone)
        permitted = {}
        for row in payload["states"]:
            state = ScrubbedState.from_dict(row)
            permitted[state.entity_id] = state
        if mode == "evaluate":
            return 0, json.dumps(_evaluate(payload, permitted, limits), ensure_ascii=False)
        if mode == "renders":
            return 0, json.dumps(_renders(payload["templates"], permitted), ensure_ascii=False)
        return 0, render_template_snapshot(payload["template"], permitted)
    except Exception as exc:
        diagnostic = json.dumps(describe_template_failure(exc), ensure_ascii=False)
        if len(diagnostic.encode()) > limits["TEMPLATE_DIAGNOSTIC_BYTES"]:
            diagnostic = '{"kind":"worker"}'
        return limits["TEMPLATE_WORKER_DIAGNOSTIC_EXIT"], diagnostic
    finally:
        logging.disable(previous_logging_disable)


# Written to stderr when the host refuses the memory limit, so Home Assistant can log it once.
NO_MEMORY_LIMIT = "memory_limit_unavailable"


def _limit_memory(memory_bytes: int) -> bool:
    """Cap the address space; False when the host refuses it, as macOS does for RLIMIT_AS."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except (ValueError, OSError):
        return False
    return True


def main() -> None:
    """Apply OS limits, then run the request read from stdin.

    Memory and a startup CPU cap precede HA imports and caller input; once the imports are
    done the CPU limit becomes what they used plus the work budget. A host that refuses the
    memory limit runs without it, under the CPU limit and the caller's wall timeout. Exit 70
    means startup failed; the diagnostic exit code means the request failed, with a bounded
    JSON diagnostic on stdout.
    """
    phase = "resource_limits"
    try:
        # Run by filename so spawn works independently of HA's process entry point.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        # Load the import-free limits without executing the integration package,
        # whose __init__ imports Home Assistant before the worker limits are active.
        limits = runpy.run_path(str(Path(__file__).with_name("template_limits.py")))
        startup_seconds = limits["TEMPLATE_WORKER_STARTUP_CPU_SECONDS"]
        memory_bytes = limits["TEMPLATE_WORKER_MEMORY_BYTES"]
        resource.setrlimit(resource.RLIMIT_CPU, (startup_seconds, startup_seconds))
        if not _limit_memory(memory_bytes):
            sys.stderr.write(NO_MEMORY_LIMIT + "\n")
        phase = "imports"
        # These run only after the resource limits above are in force, so their CPU is
        # measured before the work budget is added.
        for module in ("homeassistant.util.dt", "custom_components.phoenix_mcp.template_sandbox",
                       "custom_components.phoenix_mcp.template_diagnostics"):
            importlib.import_module(module)
        phase = "work_limit"
        usage = resource.getrusage(resource.RUSAGE_SELF)
        work_limit = min(startup_seconds, math.ceil(usage.ru_utime + usage.ru_stime + limits["TEMPLATE_WORKER_CPU_SECONDS"]))
        resource.setrlimit(resource.RLIMIT_CPU, (work_limit, work_limit))
    except Exception as exc:
        # No caller input has been read: emit only failure type and missing module.
        detail = f"{phase}: {type(exc).__name__}"
        if isinstance(exc, OSError):
            detail += f" errno={exc.errno} reason={exc.strerror}"
        if isinstance(exc, ImportError):
            detail += f" module={exc.name}"
        sys.stderr.write(detail + "\n")
        raise SystemExit(70) from None

    code, output = run_payload(sys.stdin.buffer.read(limits["TEMPLATE_WORKER_INPUT_BYTES"] + 1), limits)
    sys.stdout.write(output)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
