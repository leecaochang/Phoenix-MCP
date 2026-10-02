"""Policy workers isolated from the event loop executor used by host bridges."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from typing import Any

from custom_components.phoenix_mcp.mesa_core.exceptions import MesaError

_WORKERS: ThreadPoolExecutor | None = None
_WORKER_LOCK = threading.Lock()
_SHUTDOWN_LOCK = threading.Lock()
_SHUTTING_DOWN = False
_WORKER_THREAD = threading.local()


def _mark_worker() -> None:
    _WORKER_THREAD.active = True


def _check_shutdown_caller() -> None:
    if getattr(_WORKER_THREAD, "active", False):
        raise MesaError("a policy worker cannot shut down its own pool")


async def run_sync[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    global _WORKERS
    context = contextvars.copy_context()
    with _WORKER_LOCK:
        if _SHUTTING_DOWN:
            raise MesaError("policy workers are shutting down")
        if _WORKERS is None:
            _WORKERS = ThreadPoolExecutor(
                thread_name_prefix="mesa-policy", initializer=_mark_worker
            )
        pending = asyncio.get_running_loop().run_in_executor(
            _WORKERS, partial(context.run, fn, *args, **kwargs)
        )
    return await pending


def shutdown_policy_workers() -> None:
    """Drain and join this package's shared workers; later async use restarts them.

    Stop admitting requests first. Concurrent shutdowns are safe, and work
    submitted during shutdown raises MesaError. Call from host shutdown code,
    outside policy callbacks. Event-loop hosts should use the async variant so
    workers can finish callbacks that bridge to their loop.
    """
    global _WORKERS, _SHUTTING_DOWN
    _check_shutdown_caller()
    with _SHUTDOWN_LOCK:
        with _WORKER_LOCK:
            workers, _WORKERS = _WORKERS, None
            _SHUTTING_DOWN = True
        try:
            if workers is not None:
                workers.shutdown(wait=True)
        finally:
            with _WORKER_LOCK:
                _SHUTTING_DOWN = False


async def ashutdown_policy_workers() -> None:
    """Drain and join workers without occupying the loop or its default executor.

    Cancellation waits for cleanup before propagating. Policy callbacks must
    return for shutdown to finish; this API cannot forcibly stop Python threads.
    """
    _check_shutdown_caller()
    completed: Future[None] = Future()

    def shutdown() -> None:
        try:
            shutdown_policy_workers()
        except BaseException as err:
            completed.set_exception(err)
        else:
            completed.set_result(None)

    thread = threading.Thread(target=shutdown, name="mesa-shutdown")
    thread.start()
    pending = asyncio.wrap_future(completed)
    cancelled = False
    try:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
        pending.result()
    finally:
        thread.join()
    if cancelled:
        raise asyncio.CancelledError
