"""Bounded template execution over a permission-filtered state snapshot."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys
from typing import TYPE_CHECKING

from .const import TEMPLATE_WORKER_DIAGNOSTIC_EXIT, TEMPLATE_WORKER_INPUT_BYTES, TEMPLATE_WORKER_TIMEOUT_SECONDS
from .helpers import build_permitted_states
from .request_authority import check_request_authority
from .template_diagnostics import format_template_failure

_LOGGER = logging.getLogger(__name__)


class TemplateRenderError(ValueError):
    """Safe diagnostic for template input, execution, or worker failure."""


if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .data import PhoenixData
    from .token_store import TokenRecord


async def async_render_template_for_token(
    template: str, token: TokenRecord, hass: HomeAssistant, data: PhoenixData,
) -> str:
    """Render a template in a disposable subprocess over this token's permitted states.

    Concurrency (`template_slots`), input size and wall time are bounded here;
    the worker applies the memory and CPU limits itself, and is always killed and
    reaped. Request authority is rechecked before the render, after the worker
    returns, and on exit unless cancelled. Every failure is a TemplateRenderError
    with a safe message.
    """
    if not isinstance(template, str) or len(template.encode()) > TEMPLATE_WORKER_INPUT_BYTES:
        raise TemplateRenderError("Invalid template size.")
    if data.template_slots.locked():
        raise TemplateRenderError("Template rendering capacity reached.")
    async with data.template_slots:
        check_request_authority()
        snapshot = {
            "template": template,
            "time_zone": hass.config.time_zone,
            "states": [state.as_dict() for state in build_permitted_states(token, hass).values()],
        }
        payload = json.dumps(snapshot, default=str).encode()
        if len(payload) > TEMPLATE_WORKER_INPUT_BYTES:
            raise TemplateRenderError("Template state snapshot is too large.")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name("template_worker.py")),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # HA adds installed integration dependencies to sys.path at runtime.
                # A fresh interpreter must receive those same import locations.
                env={
                    **os.environ,
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
                    stdout, stderr = await process.communicate(payload)
            except TimeoutError:
                raise TemplateRenderError("Template rendering timed out. Reduce the template workload.") from None
            check_request_authority()
            if process.returncode == 70:
                diagnostic = stderr.decode(errors="replace").strip().splitlines()[-1:]
                _LOGGER.warning("Template worker startup failed: %s", str(diagnostic)[:240])
                raise TemplateRenderError("Template worker could not start. Check Phoenix diagnostics.")
            if process.returncode == TEMPLATE_WORKER_DIAGNOSTIC_EXIT:
                raise TemplateRenderError(format_template_failure(stdout, template))
            if process.returncode == -signal.SIGXCPU:
                raise TemplateRenderError("Template rendering exceeded its CPU budget. Reduce the template workload.")
            if process.returncode != 0:
                raise TemplateRenderError("Template worker exited unexpectedly; a resource limit may have been reached.")
            return stdout.decode()
        finally:
            cancelled = isinstance(sys.exc_info()[1], asyncio.CancelledError)
            if process.returncode is None:
                process.kill()
            await process.wait()
            if not cancelled:
                check_request_authority()
