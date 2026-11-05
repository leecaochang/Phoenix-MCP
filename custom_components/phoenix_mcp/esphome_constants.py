"""ESPHome Device Builder protocol targets, limits and timeouts."""

# ESPHome Device Builder commands. Phoenix MCP speaks the add-on's multiplexed
# WebSocket API at <dashboard url>/ws for validation, for the
# board/component/automation reference data an authoring agent needs, and for
# the firmware job model (compile, install, poll, cancel).
# Version-sensitive surface: the frame shapes and command names are the add-on's,
# with no stability promise, so re-verify them on add-on upgrades.
# Source-qualified protocol targets. The compiler and Device Builder use
# independent version families; ESPHome 2026.10.0 and 2026.10.1 both bundle Device
# Builder 1.25.0. Used only to add context to failures, never as an upgrade
# warning on success.
ESPHOME_BUILDER_VERIFIED_VERSION = "1.25.0"
ESPHOME_COMPILER_VERIFIED_VERSION = "2026.10.1"
ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS = 10.0
# Validation runs the config through ESPHome itself (no compile), which is
# seconds on a small file but can pull external_components on a large one.
ESPHOME_BUILDER_VALIDATE_TIMEOUT_SECONDS = 60.0
ESPHOME_BUILDER_OUTPUT_MAX_CHARS = 65_536
ESPHOME_BUILDER_PAGE_LIMIT = 20
ESPHOME_BUILDER_PAGE_LIMIT_MAX = 50
ESPHOME_BUILDER_MAX_COMPONENT_IDS = 10
ESPHOME_YAML_SEARCH_MAX_QUERY_CHARS = 200
ESPHOME_YAML_SEARCH_MAX_BYTES = 2_097_152
ESPHOME_YAML_SEARCH_CONTEXT_LINES_MAX = 3
ESPHOME_YAML_SEARCH_SNIPPET_MAX_CHARS = 1_000
ESPHOME_YAML_SEARCH_MAX_MATCHES = 1_000
ESPHOME_YAML_INVENTORY_MAX_ENTRIES = 1_000
ESPHOME_BUILDER_METADATA_MAX_CHARS = 512
ESPHOME_BUILDER_DEVICE_STATES = frozenset({"online", "offline", "unknown"})

# Firmware jobs. Compile and install ENQUEUE and return a job record, so those
# calls use the normal request timeout above; only the log replay of a finished
# job needs a longer one.
ESPHOME_BUILDER_JOB_LOG_TIMEOUT_SECONDS = 60.0
# A real build log runs 3-10k lines. The add-on already keeps only the last
# 2000, and the full 64KB budget above would still swamp an agent's context, so
# job output gets its own tighter bound.
ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS = 16_384
# A poll of a RUNNING build gets a much tighter, tail-only slice. The full log is
# near-identical between polls, so an agent checking a real build every 30 seconds
# would pull the same banner into its context a dozen times over. Progress is a
# question about the last few lines; the whole log is only worth returning once
# the build has actually finished.
ESPHOME_BUILDER_JOB_PROGRESS_MAX_CHARS = 4_096
# wait_for_esphome_job holds the request open so a build has something in flight
# to report progress on. This is the longest hold in the whole surface (the next
# is the 180s inline confirm), chosen to fit a real build in one call; the SSE
# keepalive is what keeps an intermediary from dropping it.
ESPHOME_BUILDER_JOB_WAIT_SECONDS = 300
# The same hold on a headless conversational surface (Assist's tool bridge, the
# voice agent, AI Task) is a hang: none of them can render a held request, their
# own pipeline timeouts are far shorter than the value above, and the build runs
# on regardless of whether anyone is waiting. Long enough for a cached build to
# land inside the turn, short enough that the reply is "still building, ask me
# again" rather than a timeout.
ESPHOME_BUILDER_JOB_WAIT_HEADLESS_SECONDS = 10
# Faster than the 15s keepalive so the reported percentage is fresh when a frame
# goes out, and so a build that finishes returns promptly rather than on the tick.
ESPHOME_BUILDER_JOB_POLL_SECONDS = 5.0
# A cancel is confirmed by re-reading the job until it reports a finished status.
ESPHOME_BUILDER_CANCEL_SETTLE_SECONDS = 5.0
ESPHOME_BUILDER_CANCEL_POLL_SECONDS = 0.5
# The add-on caps a decode at 200 lines of 500 chars and times out server-side
# at 60s; the client waits a little longer so its own timeout is never the first
# to fire and mask the add-on's own answer.
ESPHOME_BUILDER_DECODE_TIMEOUT_SECONDS = 90.0
ESPHOME_BUILDER_MAX_BACKTRACE_LINES = 200
# devices/logs runs until it is killed, so a capture window bounds the request.
# The maximum is deliberately small: this hold is dead air on the wire for however
# long it runs.
ESPHOME_BUILDER_LOG_CAPTURE_SECONDS = 15
ESPHOME_BUILDER_LOG_CAPTURE_MAX_SECONDS = 60
# Automation building-block docs. The add-on pages a large request through
# `remaining`; the page cap bounds a reply that never makes progress.
ESPHOME_BUILDER_MAX_AUTOMATION_REFS = 20
ESPHOME_BUILDER_AUTOMATION_DOCS_MAX_PAGES = 5
# The add-on's AUTOMATION_TYPES (controllers/automations/catalog.py).
ESPHOME_AUTOMATION_REF_TYPES = frozenset({
    "triggers", "actions", "conditions", "light_effects", "filters",
})
# One existing automation's masked YAML, and all of them together.
ESPHOME_AUTOMATION_YAML_MAX_CHARS = 4_000
ESPHOME_AUTOMATION_LIST_MAX_CHARS = 32_768
# Masked unified diffs (migration preview, version history).
ESPHOME_DIFF_MAX_CHARS = 16_384
# The git author the Device Builder commits as. Rows report only whether a
# version came from the Builder; an adopted repository can carry real names.
ESPHOME_BUILDER_HISTORY_AUTHOR = "ESPHome Device Builder"
ESPHOME_BUILDER_LOADED_COMPONENTS_MAX = 500
# devices/troubleshoot carries no budget of its own (its legs self-bound) and
# the add-on's own dialog waits 35 s. A headless surface cannot hold that long.
ESPHOME_BUILDER_TROUBLESHOOT_TIMEOUT_SECONDS = 40.0
ESPHOME_BUILDER_TROUBLESHOOT_HEADLESS_SECONDS = 8.0
# Memory analysis is followed in the call that starts it: the add-on drops the
# job once it ends, so there is nothing to poll afterwards.
ESPHOME_BUILDER_ANALYZE_TIMEOUT_SECONDS = ESPHOME_BUILDER_JOB_WAIT_SECONDS
ESPHOME_BUILDER_ANALYZE_OUTPUT_MAX_CHARS = 24_576

# The ESPHome integration's domain. Its services are ALL dynamically registered
# per device (services.yaml is deliberately empty upstream): a device's firmware
# declares actions, and HA registers each as
# esphome.<device_name_with_underscores>_<action_name>. They take no entity
# target, so they need their own no-target dispatch path and are authorized by
# the owning device's write scope rather than a capability.
ESPHOME_DOMAIN = "esphome"
