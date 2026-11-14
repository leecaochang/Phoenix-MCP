"""Limits of the disposable template worker.

The worker loads this file by path before applying its OS limits, so it must stay
free of imports: nothing here may run the integration package or a third-party
module ahead of those limits.
"""

# Wall time for one worker run, interpreter startup included. Startup alone takes about
# 1 s on a desktop CPU and more on small boards, and the event loop never waits on it.
TEMPLATE_WORKER_TIMEOUT_SECONDS = 10
# CPU for the template work itself: the worker adds it to what its imports used.
TEMPLATE_WORKER_CPU_SECONDS = 2
# CPU the interpreter and Home Assistant imports may use before the work budget applies.
TEMPLATE_WORKER_STARTUP_CPU_SECONDS = 8
TEMPLATE_WORKER_MEMORY_BYTES = 512 * 1024 * 1024
TEMPLATE_WORKER_INPUT_BYTES = 1_048_576
TEMPLATE_WORKER_CONCURRENCY = 2
# How long a check or test waits for a worker slot before answering busy; render_template
# never waits, so renders cannot hold back writes.
TEMPLATE_CHECK_SLOT_WAIT_SECONDS = 10
TEMPLATE_WORKER_DIAGNOSTIC_EXIT = 65
TEMPLATE_DIAGNOSTIC_BYTES = 4096
TEMPLATE_DIAGNOSTIC_MESSAGE_CHARS = 320
TEMPLATE_DIAGNOSTIC_SOURCE_CHARS = 240
TEMPLATE_DIAGNOSTIC_TRACEBACK_FRAMES = 64
# Each caller template Home Assistant will compile on its event loop must compile in the
# worker within this much CPU: Jinja folds constant expressions while compiling, so compiling
# runs caller arithmetic, regexes and filters. A legitimate one takes about 1 ms on a desktop
# CPU, so slow boards stay far below it. The worker's CPU limit bounds a call's total.
TEMPLATE_CHECK_COMPILE_SECONDS = 0.25
# Distinct new template strings one call may send to the worker.
TEMPLATE_WORKER_MAX_TEMPLATES = 256
TEMPLATE_WARNINGS_PER_RENDER = 10
