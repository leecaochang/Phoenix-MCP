"""Bounded template execution over a permission-filtered state snapshot.

Every caller template Phoenix lets Home Assistant compile or render goes through the
disposable worker first: render_template renders there, test_condition evaluates there, and
authoring and helper preflights compile and trial-render there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from contextvars import ContextVar
import json
import logging
import os
from pathlib import Path
import signal
import sys
from typing import TYPE_CHECKING, Any

from .template_limits import (
    TEMPLATE_CHECK_COMPILE_SECONDS, TEMPLATE_CHECK_SLOT_WAIT_SECONDS, TEMPLATE_WORKER_DIAGNOSTIC_EXIT,
    TEMPLATE_WORKER_INPUT_BYTES, TEMPLATE_WORKER_MAX_TEMPLATES, TEMPLATE_WORKER_TIMEOUT_SECONDS,
)
from .template_sandbox import build_permitted_states
from .template_scope import LONE_SURROGATE_REFUSAL, has_lone_surrogate
from .request_authority import check_request_authority
from .template_diagnostics import format_template_failure

_LOGGER = logging.getLogger(__name__)


class TemplateRenderError(ValueError):
    """Safe diagnostic for template input, execution, or worker failure."""


class TemplateWorkerBusyError(TemplateRenderError):
    """Every worker slot stayed busy: nothing was checked, and the same call may be retried."""


if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .data import PhoenixData
    from .access_key_store import AccessKeyRecord


_WORKER_ENV_KEYS = frozenset({"PATH", "HOME", "LANG", "TMPDIR", "TZ"})
_WORKER_SCRIPT = Path(__file__).with_name("template_worker.py")
_WORKER_FAILED = "Template worker exited unexpectedly; a resource limit may have been reached."
_WORKER_BUSY = (
    "Template checking is busy with other requests, so nothing was checked or changed. "
    "Retry the same call in a few seconds."
)
# The worker's stderr line when the host refuses its memory limit; it must match the worker's.
_NO_MEMORY_LIMIT = b"memory_limit_unavailable"
_memory_limit_noted = False


def _note_missing_memory_limit(stderr: bytes) -> None:
    """Log once per process that the host runs the worker without its memory limit."""
    global _memory_limit_noted
    if not _memory_limit_noted and _NO_MEMORY_LIMIT in stderr.splitlines():
        _memory_limit_noted = True
        _LOGGER.warning(
            "This host does not allow a memory limit on the template worker; it runs with its "
            "CPU limit and timeout only.")


# Checks and tests wait for a slot; render_template is refused at once when none is free.
_WAITING_MODES = frozenset({"check", "evaluate"})
# Set by a provider loop that runs one response's reads together. Its worker requests then go one
# at a time, taking slots as the same calls made in order would, so none is refused or kept waiting
# by its own siblings.
batch_worker_turn: ContextVar[asyncio.Lock | None] = ContextVar("phoenix_batch_worker_turn", default=None)


async def _acquire_slot(payload: dict, data: PhoenixData) -> None:
    """Take a worker slot: a render only when one is free, a check or test within a bounded wait."""
    if payload.get("mode") not in _WAITING_MODES:
        if data.template_slots.locked():
            raise TemplateRenderError("Template rendering capacity reached.")
        await data.template_slots.acquire()
        return
    try:
        async with asyncio.timeout(TEMPLATE_CHECK_SLOT_WAIT_SECONDS):
            await data.template_slots.acquire()
    except TimeoutError:
        raise TemplateWorkerBusyError(_WORKER_BUSY) from None


async def _run_worker(payload: dict, hass: HomeAssistant, data: PhoenixData) -> tuple[int, bytes]:
    """Run one worker request and return (exit code, stdout).

    Bounds concurrency, input and wall time here; the worker limits memory and CPU. Authority
    is rechecked before the spawn, after the exchange and after cleanup. Startup failure,
    timeout and the CPU limit raise TemplateRenderError, and a slot still busy after the
    bounded wait TemplateWorkerBusyError; the process is always reaped.
    """
    turn = batch_worker_turn.get()
    if turn is not None:
        async with turn:
            return await _run_in_free_slot(payload, data)
    return await _run_in_free_slot(payload, data)


async def _run_in_free_slot(payload: dict, data: PhoenixData) -> tuple[int, bytes]:
    await _acquire_slot(payload, data)
    try:
        return await _run_in_slot(payload, data)
    finally:
        data.template_slots.release()


async def _run_in_slot(payload: dict, data: PhoenixData) -> tuple[int, bytes]:
    """The body of _run_worker, holding one slot."""
    check_request_authority()
    encoded = json.dumps(payload, default=str).encode()
    if len(encoded) > TEMPLATE_WORKER_INPUT_BYTES:
        raise TemplateRenderError("Template state snapshot is too large.")
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(_WORKER_SCRIPT),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Only locale and path basics are inherited, never HA's credentials
            # (SUPERVISOR_TOKEN on HAOS). HA adds installed integration
            # dependencies to sys.path at runtime, so a fresh interpreter must
            # receive those same import locations.
            env={
                **{key: value for key, value in os.environ.items()
                   if key in _WORKER_ENV_KEYS or key.startswith("LC_")},
                "PYTHONPATH": os.pathsep.join(sys.path),
                # HA uses mimalloc, whose large virtual reservations conflict
                # with a small RLIMIT_AS. Keep this worker on the bounded
                # standard allocator without changing HA's own allocator.
                "PYTHONMALLOC": "pymalloc",
            },
        )
    except OSError:
        raise TemplateRenderError("Template worker could not start. Check Phoenix diagnostics.") from None
    try:
        try:
            async with asyncio.timeout(TEMPLATE_WORKER_TIMEOUT_SECONDS):
                stdout, stderr = await process.communicate(encoded)
        except TimeoutError:
            raise TemplateRenderError("Template rendering timed out. Reduce the template workload.") from None
        check_request_authority()
        _note_missing_memory_limit(stderr)
        if process.returncode == 70:
            diagnostic = stderr.decode(errors="replace").strip().splitlines()[-1:]
            _LOGGER.warning("Template worker startup failed: %s", str(diagnostic)[:240])
            raise TemplateRenderError("Template worker could not start. Check Phoenix diagnostics.")
        if process.returncode == -signal.SIGXCPU:
            raise TemplateRenderError("Template rendering exceeded its CPU budget. Reduce the template workload.")
        return process.returncode if process.returncode is not None else -1, stdout
    finally:
        cancelled = isinstance(sys.exc_info()[1], asyncio.CancelledError)
        if process.returncode is None:
            process.kill()
        await process.wait()
        if not cancelled:
            check_request_authority()


def _snapshot(access_key: AccessKeyRecord, hass: HomeAssistant, only: Collection[str] | None = None) -> dict:
    return {
        "time_zone": hass.config.time_zone,
        "states": [state.as_dict() for state in build_permitted_states(access_key, hass, only).values()],
    }


async def async_render_template_for_access_key(
    template: str, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> str:
    """Render a template in a disposable subprocess over this key's permitted states.

    Every failure is a TemplateRenderError with a safe message.
    """
    if not isinstance(template, str) or len(template.encode()) > TEMPLATE_WORKER_INPUT_BYTES:
        raise TemplateRenderError("Invalid template size.")
    code, stdout = await _run_worker({"template": template, **_snapshot(access_key, hass)}, hass, data)
    if code == TEMPLATE_WORKER_DIAGNOSTIC_EXIT:
        raise TemplateRenderError(format_template_failure(stdout, template))
    if code != 0:
        raise TemplateRenderError(_WORKER_FAILED)
    return stdout.decode()


def _bounded_templates(templates: list[str]) -> None:
    """Refuse a batch the worker cannot take, or one holding a lone surrogate."""
    if len(templates) > TEMPLATE_WORKER_MAX_TEMPLATES:
        raise TemplateRenderError(
            f"Too many templates in one request: at most {TEMPLATE_WORKER_MAX_TEMPLATES} can be checked.")
    if any(has_lone_surrogate(template) for template in templates):
        raise TemplateRenderError(LONE_SURROGATE_REFUSAL)


async def async_evaluate_templates_for_access_key(
    renders: list[dict], variables: dict, entity_ids: Collection[str],
    access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> list[dict]:
    """Render each {"template", "state"} in the worker over the key's states of ``entity_ids``.

    ``variables`` are the caller's plain data and ``state`` an entity bound to the state
    variable. Returns one {"output"} or {"failure": safe message} per render, each with
    optional "warnings". Worker failure raises TemplateRenderError.
    """
    _bounded_templates([render["template"] for render in renders])
    payload = {"mode": "evaluate", "renders": renders, "variables": variables,
               **_snapshot(access_key, hass, entity_ids)}
    code, stdout = await _run_worker(payload, hass, data)
    if code == TEMPLATE_WORKER_DIAGNOSTIC_EXIT:
        raise TemplateRenderError(format_template_failure(stdout, ""))
    try:
        results = json.loads(stdout)["results"] if code == 0 else None
    except (ValueError, KeyError, TypeError):
        results = None
    if not isinstance(results, list) or len(results) != len(renders):
        raise TemplateRenderError(_WORKER_FAILED)
    answers: list[dict] = []
    for render, result in zip(renders, results, strict=True):
        answer: dict[str, Any] = {}
        if isinstance(result, dict) and isinstance(result.get("output"), str):
            answer["output"] = result["output"]
        else:
            failure = result.get("failure") if isinstance(result, dict) else None
            answer["failure"] = format_template_failure(json.dumps(failure).encode(), render["template"])
        warnings = result.get("warnings") if isinstance(result, dict) else None
        if isinstance(warnings, list):
            answer["warnings"] = [str(item) for item in warnings]
        answers.append(answer)
    return answers


async def async_check_caller_templates(templates: list[str], hass: HomeAssistant, data: PhoenixData) -> None:
    """Compile and trial-render caller templates in the worker before Home Assistant compiles them.

    Raises TemplateRenderError when one holds a lone surrogate, when the worker dies, times out
    or exceeds a limit, or when compiling one template takes longer than
    TEMPLATE_CHECK_COMPILE_SECONDS; TemplateWorkerBusyError when no slot freed in time. Compile
    and render errors are left for Home Assistant's own validation to report.
    """
    if not templates:
        return
    _bounded_templates(templates)
    code, stdout = await _run_worker({"mode": "check", "templates": templates}, hass, data)
    try:
        seconds = json.loads(stdout)["compile_seconds"] if code == 0 else None
    except (ValueError, KeyError, TypeError):
        seconds = None
    if not isinstance(seconds, (int, float)):
        raise TemplateRenderError(_WORKER_FAILED)
    if seconds > TEMPLATE_CHECK_COMPILE_SECONDS:
        raise TemplateRenderError(
            "A template takes too long to compile. Reduce constant arithmetic, repetition "
            "and regular expressions in it.")
