"""Limits of the disposable template worker.

The worker loads this file by path before applying its OS limits, so it must stay
free of imports: nothing here may run the integration package or a third-party
module ahead of those limits.
"""

TEMPLATE_WORKER_TIMEOUT_SECONDS = 5
TEMPLATE_WORKER_CPU_SECONDS = 2
TEMPLATE_WORKER_MEMORY_BYTES = 512 * 1024 * 1024
TEMPLATE_WORKER_INPUT_BYTES = 1_048_576
TEMPLATE_WORKER_CONCURRENCY = 2
TEMPLATE_WORKER_DIAGNOSTIC_EXIT = 65
TEMPLATE_DIAGNOSTIC_BYTES = 4096
TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS = 320
TEMPLATE_DIAGNOSTIC_SOURCE_CHARS = 240
TEMPLATE_DIAGNOSTIC_TRACEBACK_FRAMES = 64
