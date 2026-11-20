"""Bounded template execution over a permission-filtered state snapshot.

Every caller template Phoenix lets Home Assistant compile or render goes through the
disposable worker first: render_template renders there, test_condition evaluates there, and
authoring and helper preflights compile and trial-render there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import signal
import sys
from typing import TYPE_CHECKING, Any

from .template_limits import (
    TEMPLATE_BATCH_GATHER_SECONDS, TEMPLATE_CHECK_COMPILE_SECONDS, TEMPLATE_CHECK_SLOT_WAIT_SECONDS, TEMPLATE_WORKER_DIAGNOSTIC_EXIT,
    TEMPLATE_WORKER_INPUT_BYTES, TEMPLATE_WORKER_MAX_TEMPLATES, TEMPLATE_WORKER_TIMEOUT_SECONDS,
)
from .template_sandbox import iter_permitted_states
from .template_scope import LONE_SURROGATE_REFUSAL, has_lone_surrogate
from .request_authority import AuthorityChangedError, check_request_authority
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


class TemplateBatch:
    """The template worker's share of one parallel model response.

    Its worker requests take one turn at a time, as the same calls made in order would, so none
    is refused or kept waiting by a sibling. Its renders for one key go to the worker together:
    one process and one snapshot, with a separate answer each. A run that fails as a whole
    renders each template alone, so a template that exhausts a limit fails only itself.
    """

    def __init__(self, renders: int = 0) -> None:
        self.turn = asyncio.Lock()
        self._remaining = renders
        self._gathers = renders > 1
        self._waiting: list[_PendingRender] = []
        self._timer: asyncio.TimerHandle | None = None

    def gathers(self) -> bool:
        return self._gathers

    async def render(self, template: str, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData) -> str:
        """This call's answer from the batch's next run, checked against the caller's own authority."""
        loop = asyncio.get_running_loop()
        pending = _PendingRender(template, access_key, loop.create_future())
        self._waiting.append(pending)
        self._remaining -= 1
        if self._remaining <= 0:
            self._flush(hass, data)
        elif self._timer is None:
            self._timer = loop.call_later(TEMPLATE_BATCH_GATHER_SECONDS, self._flush, hass, data)
        try:
            outcome = await pending.future
        except asyncio.CancelledError:
            if pending.run is not None and all(other.future.done() for other in pending.group):
                pending.run.cancel()
            raise
        except AuthorityChangedError:
            # The run checked another caller's authority; this caller answers to its own.
            check_request_authority()
            return await _render_alone(template, access_key, hass, data)
        except Exception:
            # A lost authority outranks the render's own failure, as it does for a render alone.
            check_request_authority()
            raise
        check_request_authority()
        return outcome

    def _flush(self, hass: HomeAssistant, data: PhoenixData) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        group, self._waiting = self._waiting, []
        if not group:
            return
        run = asyncio.ensure_future(_settle_renders(group, hass, data))
        for pending in group:
            pending.run, pending.group = run, group


class _PendingRender:
    """One render waiting for its batch's run."""

    def __init__(self, template: str, access_key: AccessKeyRecord, future: asyncio.Future) -> None:
        self.template = template
        self.access_key = access_key
        self.future = future
        self.run: asyncio.Future | None = None
        self.group: list[_PendingRender] = []


# Set by a provider loop that runs one response's reads together.
template_batch: ContextVar[TemplateBatch | None] = ContextVar("phoenix_template_batch", default=None)


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
    batch = template_batch.get()
    if batch is not None:
        async with batch.turn:
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
    encoded = encoded_payload(payload)
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


_SNAPSHOT_TOO_LARGE = "Template state snapshot is too large."


@dataclass(frozen=True, slots=True)
class _PermittedStates:
    """A key's permitted states, read only once a worker slot is held (encoded_payload)."""

    access_key: AccessKeyRecord
    hass: HomeAssistant
    only: Collection[str] | None = None


def _snapshot(access_key: AccessKeyRecord, hass: HomeAssistant, only: Collection[str] | None = None) -> dict:
    return {"time_zone": hass.config.time_zone, "states": _PermittedStates(access_key, hass, only)}


def encoded_payload(payload: dict) -> bytes:
    """Serialize one worker request within TEMPLATE_WORKER_INPUT_BYTES.

    Permitted states are read and serialized one at a time and the build stops at the limit,
    so a refused or oversized call does not first copy every state on the event loop. The
    bytes equal json.dumps of the whole request with the states in place.
    """
    states = payload.get("states")
    if not isinstance(states, _PermittedStates):
        encoded = json.dumps(payload, default=str).encode()
        if len(encoded) > TEMPLATE_WORKER_INPUT_BYTES:
            raise TemplateRenderError(_SNAPSHOT_TOO_LARGE)
        return encoded
    head = json.dumps({key: value for key, value in payload.items() if key != "states"}, default=str)
    opening = head[:-1] + (", " if len(head) > 2 else "") + '"states": ['
    # ASCII output (json.dumps escapes the rest), so characters are bytes.
    size = len(opening) + len("]}")
    parts: list[str] = []
    for state in iter_permitted_states(states.access_key, states.hass, states.only):
        text = json.dumps(state.as_dict(), default=str)
        size += len(text) + (2 if parts else 0)
        if size > TEMPLATE_WORKER_INPUT_BYTES:
            raise TemplateRenderError(_SNAPSHOT_TOO_LARGE)
        parts.append(text)
    return (opening + ", ".join(parts) + "]}").encode()


async def async_render_template_for_access_key(
    template: str, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData,
) -> str:
    """Render a template in a disposable subprocess over this key's permitted states.

    Every failure is a TemplateRenderError with a safe message.
    """
    if not isinstance(template, str) or len(template.encode()) > TEMPLATE_WORKER_INPUT_BYTES:
        raise TemplateRenderError("Invalid template size.")
    batch = template_batch.get()
    if batch is not None and batch.gathers():
        return await batch.render(template, access_key, hass, data)
    return await _render_alone(template, access_key, hass, data)


async def _render_alone(template: str, access_key: AccessKeyRecord, hass: HomeAssistant, data: PhoenixData) -> str:
    code, stdout = await _run_worker({"template": template, **_snapshot(access_key, hass)}, hass, data)
    if code == TEMPLATE_WORKER_DIAGNOSTIC_EXIT:
        raise TemplateRenderError(format_template_failure(stdout, template))
    if code != 0:
        raise TemplateRenderError(_WORKER_FAILED)
    return stdout.decode()


async def _render_together(group: list[_PendingRender], hass: HomeAssistant, data: PhoenixData) -> list[str | TemplateRenderError]:
    """Each template's output or failure from one worker run; a failure of the run itself raises."""
    payload = {"mode": "renders", "templates": [pending.template for pending in group],
               **_snapshot(group[0].access_key, hass)}
    code, stdout = await _run_worker(payload, hass, data)
    try:
        results = json.loads(stdout)["results"] if code == 0 else None
    except (ValueError, KeyError, TypeError):
        results = None
    if not isinstance(results, list) or len(results) != len(group):
        raise TemplateRenderError(_WORKER_FAILED)
    outcomes: list[str | TemplateRenderError] = []
    for pending, result in zip(group, results, strict=True):
        if isinstance(result, dict) and isinstance(result.get("output"), str):
            outcomes.append(result["output"])
        else:
            failure = result.get("failure") if isinstance(result, dict) else None
            outcomes.append(TemplateRenderError(format_template_failure(json.dumps(failure).encode(), pending.template)))
    return outcomes


async def _settle_renders(group: list[_PendingRender], hass: HomeAssistant, data: PhoenixData) -> None:
    """Answer every waiting render: together per key, alone when the shared run fails as a whole."""
    keys: dict[tuple, list[_PendingRender]] = {}
    for pending in group:
        keys.setdefault((pending.access_key.id, pending.access_key.settings_version), []).append(pending)
    for same_key in keys.values():
        live = [pending for pending in same_key if not pending.future.done()]
        if not live:
            continue
        try:
            outcomes: list[str | BaseException] = list(await _render_together(live, hass, data)) if len(live) > 1 else []
        except TemplateRenderError:
            outcomes = []
        except Exception as exc:
            outcomes = [exc] * len(live)
        if not outcomes:
            for pending in live:
                try:
                    outcomes.append(await _render_alone(pending.template, pending.access_key, hass, data))
                except Exception as exc:
                    outcomes.append(exc)
        for pending, outcome in zip(live, outcomes, strict=True):
            if pending.future.done():
                continue
            if isinstance(outcome, BaseException):
                pending.future.set_exception(outcome)
            else:
                pending.future.set_result(outcome)


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
    or exceeds a limit (a fold or trial render out of memory included), or when compiling one
    template takes longer than TEMPLATE_CHECK_COMPILE_SECONDS; TemplateWorkerBusyError when no
    slot freed in time. Compile and render errors are left for Home Assistant's own validation.
    """
    if not templates:
        return
    _bounded_templates(templates)
    code, stdout = await _run_worker({"mode": "check", "templates": templates}, hass, data)
    try:
        report = json.loads(stdout) if code == 0 else None
    except ValueError:
        report = None
    seconds = report.get("compile_seconds") if isinstance(report, dict) else None
    if not isinstance(report, dict) or not isinstance(seconds, (int, float)):
        raise TemplateRenderError(_WORKER_FAILED)
    if report.get("exhausted"):
        raise TemplateRenderError(
            "A template needs more memory than a check allows. Reduce constant repetition, "
            "padding and large powers in it.")
    if seconds > TEMPLATE_CHECK_COMPILE_SECONDS:
        raise TemplateRenderError(
            "A template takes too long to compile. Reduce constant arithmetic, repetition "
            "and regular expressions in it.")
