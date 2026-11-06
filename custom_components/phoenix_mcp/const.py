"""Cross-cutting limits, modes, capability names and security memberships.

Imports only the standard library, and every shared table is read-only. Tables
with a single owning area live beside this module: routing_tables for command,
Decision Provider and tool routing, provider_constants for model providers,
catalog_templates for generated operator text, esphome_constants for ESPHome,
and template_limits for the disposable template worker.
"""

from collections.abc import Mapping
import datetime
import pathlib
import re
from types import MappingProxyType


# Phoenix's OWN operator-facing string catalogs (panel, notification, voice),
# deliberately NOT under translations/. hassfest validates translations/en.json
# against a closed set of Home Assistant categories and errors on any other
# top-level key, which fails the HACS submission; translations/ therefore holds
# only HA's own config and entity sections. Read by catalog_text._catalog_section and
# served to the panel by admin.settings.PhoenixAdminCatalogView.
CATALOGS_DIR = pathlib.Path(__file__).parent / "catalogs"

PHOENIX_VERSION = "2.0.0"
# Minimum supported HA release; runtime imports and boundaries are exercised at this floor.
MIN_HA_VERSION = "2026.7.0"
ASSIST_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
GITHUB_URL = "https://github.com/leecaochang/Phoenix-MCP"
DOMAIN = "phoenix_mcp"
RUNTIME_READY_KEY = "phoenix_mcp_runtime_ready"
STORAGE_KEY = "phoenix_mcp"
STORAGE_VERSION = 6

CALENDAR_MAX_WINDOW_DAYS = 31
CALENDAR_MAX_EVENTS = 500
CALENDAR_RECEIPT_LIMIT = 200
CALENDAR_TIMEOUT_SECONDS = 30
CALENDAR_EVENT_MAX_BYTES = 32768
CALENDAR_RESULT_MAX_BYTES = 1048576

PROXY_TIMEOUT_SECONDS = 30
# create_backup holds the request until Home Assistant reports the backup finished,
# so the result can say where it landed and what failed. A configuration-only
# backup fits; one with add-ons can run longer and is then reported as started.
BACKUP_COMPLETION_WAIT_SECONDS = 45
# Backup agents that keep one backup per account, so a backup Phoenix MCP sent
# there would replace the operator's own. Matched on the agent ID's domain.
BACKUP_SINGLE_SLOT_AGENT_DOMAINS = frozenset({"cloud"})
# The agents that store a backup on Home Assistant's own disk, in the order
# create_backup prefers them when the backup settings name no location.
LOCAL_BACKUP_AGENT_IDS = ("hassio.local", "backup.local")
AUTHORING_REGISTRATION_TIMEOUT_SECONDS = 5.0
AUTHORING_REGISTRATION_POLL_SECONDS = 0.1
RELATIVE_VOLUME_MAX_CONCURRENCY = 8
RELATIVE_VOLUME_PLAN_VERSION = 1
APPROVAL_REASON_FORMAT_INCOMPATIBLE = "approval_format_incompatible"
MAX_REQUEST_BODY_BYTES = 1_048_576
# Bounds the whole read, so a client that stops sending ends its own request.
MCP_BODY_READ_TIMEOUT_SECONDS = 30
MAX_BATCH_ITEMS = 50
# Storage collections use domain/create|update|delete and a domain_id target.
STORAGE_HELPER_TYPES = frozenset({
    "input_boolean", "input_number", "input_text", "input_select",
    "input_datetime", "input_button", "counter", "timer", "schedule",
    "zone", "tag", "person",
})
# HA merges an update into these stored items; every other storage helper is
# rebuilt from the update payload alone, so an omitted field would be reset.
STORAGE_HELPER_MERGING_TYPES = frozenset({"zone", "tag", "person"})
# Tag and person items store no icon; their entities take one only as an entity
# registry override. Every other storage helper stores an optional icon.
STORAGE_HELPER_ICON_TYPES = STORAGE_HELPER_TYPES - {"tag", "person"}
WS_REQUEST_CONTROL_FIELDS = frozenset({"id", "type"})
STORAGE_HELPER_REQUEST_FIELDS = WS_REQUEST_CONTROL_FIELDS | frozenset(
    f"{domain}_id" for domain in STORAGE_HELPER_TYPES
)
TOOL_INPUT_MAX_DEPTH = 64
TOOL_INPUT_MAX_NODES = 32768
SERVICE_TARGET_MAX_GROUPS = 32
SERVICE_TARGET_MAX_IDS = 1024
# One get_state call reads at most this many explicit entity IDs.
GET_STATE_MAX_IDS = 1024
# Rows one search_entities page returns at most: the largest set of entities one
# listing hands over, so also the most one get_history summary takes.
SEARCH_ENTITIES_MAX_LIMIT = 500
# get_history summary mode: Recorder rows per database page, rows one call may
# fold (so at most this many divided by the page size queries), wall-clock
# seconds before no further page starts, and states listed individually.
HISTORY_SUMMARY_PAGE_ROWS = 1000
HISTORY_SUMMARY_MAX_ROWS = 20000
HISTORY_SUMMARY_MAX_SECONDS = 10.0
HISTORY_SUMMARY_MAX_STATES = 50
# A summary of several entities: the wall-clock seconds after which no further
# entity's scan starts (each scan keeps its own row and time budget within that).
# How many rows one result holds is set by TOOL_RESULT_MAX_BYTES, not a count.
HISTORY_SUMMARY_BATCH_SECONDS = 20.0
# States listed per entity in that call; the rest are combined, so a numeric
# sensor's hundreds of distinct values do not crowd out the other entities.
HISTORY_SUMMARY_MANY_MAX_STATES = 5
# Offset-paged registry and calendar reads.
DEVICE_LIST_DEFAULT_LIMIT = 100
DEVICE_LIST_MAX_LIMIT = 500
INTEGRATION_LIST_DEFAULT_LIMIT = 100
INTEGRATION_LIST_MAX_LIMIT = 500
CALENDAR_EVENTS_DEFAULT_LIMIT = 100
# get_todo_items pages items across lists like calendar events.
TODO_ITEMS_DEFAULT_LIMIT = 100
TODO_ITEMS_MAX_LIMIT = 500
TODO_TIMEOUT_SECONDS = 15
# find_available_actions include_fields bounds per service.
SERVICE_FIELDS_MAX = 40
SERVICE_FIELD_MAX_OPTIONS = 30
SERVICE_FIELD_MAX_DESCRIPTION = 160
# An unknown label or category refusal lists at most this many valid IDs.
REGISTRY_ID_HINT_MAX = 50
# Integration INFO/DEBUG records kept in memory for get_logs source=integration_debug.
DEBUG_CAPTURE_CAPACITY = 1000
DEBUG_CAPTURE_MAX_CHARS = 2000
LOG_DETAIL_STALE_MESSAGE = "Invalid or stale log detail reference. Restart the query."
# call_service reports at most this many target states after the call.
SERVICE_RESULT_STATES_MAX = 20
# Authoring results name at most this many unresolvable references.
REFERENCE_WARNING_MAX = 20
# list_files: entries per page.
LIST_FILES_DEFAULT_LIMIT = 500
LIST_FILES_MAX_LIMIT = 1000
# Nested stack cards checked for list shape before a dashboard write.
LAYOUT_SHAPE_MAX_DEPTH = 10
# One patch_dashboard call applies at most this many changes under one approval.
PATCH_DASHBOARD_MAX_CHANGES = 20
# A dashboard layout read is scrubbed this many levels deep; anything deeper is
# returned as the redaction placeholder and reported, never silently emptied.
DASHBOARD_SCRUB_MAX_DEPTH = 100
# A layout's recovery snapshot holds anything that read can show, one level down
# inside the snapshot's own envelope.
CONFIG_RECOVERY_LAYOUT_MAX_DEPTH = DASHBOARD_SCRUB_MAX_DEPTH + 1
# Every other recovery snapshot, matching redaction.redact_structure.
CONFIG_RECOVERY_MAX_DEPTH = 25
# Typed objects under these keys are nested cards for get_dashboard_config search.
DASHBOARD_CARD_CONTAINER_KEYS = frozenset({"card", "cards", "custom_fields", "states"})
DASHBOARD_SEARCH_CRITERION_MAX = 200
DASHBOARD_SEARCH_MAX_MATCHES = 50
DASHBOARD_SEARCH_MATCHED_VALUES = 5
DASHBOARD_SEARCH_VALUE_CHARS = 200
DASHBOARD_SEARCH_MAX_WARNINGS = 20
TEMPLATE_FAILURE_MESSAGES = MappingProxyType({
    "syntax": "Template syntax error",
    "undefined": "Template runtime error: undefined variable or attribute. Check the name and use a default for missing values.",
    "division": "Template runtime error: division by zero. Check the divisor before dividing.",
    "value": "Template runtime error: invalid value. Check filter inputs and supply a valid default for conversions.",
    "type": "Template runtime error: incompatible value types or arguments. Check the operation and its inputs.",
    "security": "Template runtime error: access blocked by the template sandbox.",
    "runtime": "Template runtime error. Check the expression and its inputs.",
    "output": "Template output exceeded its size limit. Reduce the rendered output.",
    "memory": "Template rendering exhausted its memory budget. Reduce the template workload.",
    "recursion": "Template rendering exceeded its recursion limit. Reduce nested or recursive calls.",
    "worker": "Template worker failed. Check Phoenix diagnostics.",
})

ACCESS_KEY_PREFIX = "phx_"
ACCESS_KEY_HEX_LENGTH = 64
ACCESS_KEY_LENGTH = len(ACCESS_KEY_PREFIX) + ACCESS_KEY_HEX_LENGTH

ACCESS_KEY_NAME_REGEX = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")

# The access key fields an update may change, capabilities aside.
ACCESS_KEY_MUTABLE_FIELDS = frozenset({
    "name", "pass_through", "use_assist_exposure", "announce_all_tools", "confirm_inline_wait_seconds",
    "rate_limit_requests", "rate_limit_burst", "persona", "notes",
})
DEFAULT_RATE_LIMIT_REQUESTS = 60
DEFAULT_RATE_LIMIT_BURST = 10

# Administrator notes on an access key: advisory prose added to the agent's
# instructions on every surface, so the bound also caps their prompt cost.
ACCESS_KEY_NOTES_MAX_LENGTH = 2000

# Access key settings presets (named snapshots of an access key's full configuration,
# switchable from the panel; gated by settings.access_key_presets_enabled).
MAX_PRESETS_PER_ACCESS_KEY = 8
PRESET_NAME_MAX_LENGTH = 40
DEFAULT_PRESET_NAME = "Default preset"

FLUSH_INTERVAL = datetime.timedelta(minutes=5)
EXPIRY_CHECK_INTERVAL = datetime.timedelta(minutes=1)
SENSOR_PUSH_INTERVAL = datetime.timedelta(hours=1)
# Coalescing window for per-request sensor state writes (update_access_key_counter):
# the first request after a flush schedules one write this many seconds out and
# further requests ride it, so a busy access key produces at most one state_changed
# per window instead of one per request.
SENSOR_WRITE_DEBOUNCE_SECONDS = 5

AUDIT_LOG_MAXLEN = 10000
# The menus behind two settings: the admin API refuses anything else and a stored
# value outside them is reset on load, so one list has to serve both.
AUDIT_LOG_MAXLENS = frozenset({100, 1000, 5000, 10000})
AUDIT_FLUSH_INTERVALS = frozenset({0, 5, 10, 15, 30, 60})
AUDIT_STORAGE_KEY = "phoenix_mcp_audit"
AUDIT_STORAGE_VERSION = 1
# audit_flush_interval is stored and exposed in minutes (not seconds).
# Valid values: 0 (disable periodic flush), 5, 10, 15, 30, 60.

# Configuration version history. Immutable before/after snapshots of
# agent-driven create/edit/delete of automations, scripts, scenes, and helpers,
# with admin-only rollback. Stored separately from access keys so the schema versions
# evolve independently.
VERSION_STORAGE_KEY = "phoenix_mcp_versions"
VERSION_STORAGE_VERSION = 1
CONFIG_RECOVERY_STORAGE_KEY = "phoenix_mcp_config_recovery"
CONFIG_RECOVERY_SNAPSHOT_PREFIX = CONFIG_RECOVERY_STORAGE_KEY + ".snapshot."
CONFIG_RECOVERY_MAX_RECORDS = 256
CONFIG_RECOVERY_MAX_BYTES = 1_048_576
CONFIG_RECOVERY_RETENTION_DAYS = 30
CONFIG_RECOVERY_PER_RESOURCE = 20
CONFIG_RECOVERY_SWEEP_INTERVAL = datetime.timedelta(seconds=30)
CONFIG_RECOVERY_SWEEP_BATCH = 4
CONFIG_RECOVERY_OBSERVATION_TIMEOUT = 5
CONFIG_RECOVERY_APPROVAL_CAP = "__configuration_recovery__"
CONFIG_RECOVERY_APPROVAL_ACCESS_KEY = "__configuration_recovery__"
CONFIG_RECOVERY_UNRESOLVED = frozenset({"prepared", "dispatching", "partial", "unknown"})
# Template's config flow type menu. Keep this in sync with the supported HA
# Template platforms; _flow_step_body intersects it with the installed flow's
# actual menu, so a type missing from an older supported HA release is omitted.
TEMPLATE_RECOVERY_TYPES = frozenset({
    "alarm_control_panel", "binary_sensor", "button", "cover", "device_tracker",
    "event", "fan", "image", "light", "lock", "number", "select", "sensor",
    "switch", "update", "vacuum", "weather",
})
# Static authoring analysis never renders against HA. Unknown syntax/functions
# refuse rather than guessing their dependencies.
HELPER_TEMPLATE_MAX_CHARS = 8192
HELPER_TEMPLATE_MAX_NODES = 512
HELPER_TEMPLATE_MAX_DEPTH = 32
HELPER_TEMPLATE_STATE_FUNCTIONS = frozenset({
    "states", "state_attr", "is_state", "is_state_attr", "has_value",
})
HELPER_TEMPLATE_STATE_PROPERTIES = frozenset({
    "entity_id", "state", "name", "domain", "object_id", "state_with_unit",
    "last_changed", "last_updated", "last_reported",
})
HELPER_TEMPLATE_PURE_FUNCTIONS = frozenset({
    "float", "int", "bool", "min", "max",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt",
    "log", "average", "median", "clamp", "wrap", "remap",
    "now", "utcnow", "today_at", "as_datetime", "as_local", "as_timestamp",
    "timedelta", "strptime", "iif", "is_number",
})
HELPER_TEMPLATE_TIME_FUNCTIONS = frozenset({
    "now", "utcnow", "today_at", "as_datetime", "as_local", "timedelta", "strptime",
})
HELPER_TEMPLATE_TIME_METHODS = frozenset({
    "replace", "strftime", "isoformat", "timestamp", "date", "time",
    "weekday", "isoweekday", "total_seconds",
})
HELPER_TEMPLATE_TIME_ATTRIBUTES = frozenset({
    "year", "month", "day", "hour", "minute", "second", "microsecond", "days", "seconds",
})
HELPER_TEMPLATE_PURE_FILTERS = frozenset({
    "float", "int", "bool", "string", "abs", "round", "min", "max",
    "sum", "average", "median", "sqrt", "log", "sin", "cos", "tan",
    "lower", "upper", "title", "capitalize", "trim", "replace",
    "default", "d", "length", "count", "first", "last", "list", "join",
    "to_json", "from_json", "iif", "is_number", "format",
    "regex_replace", "regex_match", "regex_search", "regex_findall", "regex_findall_index",
})
HELPER_TEMPLATE_PURE_TESTS = frozenset({
    "defined", "undefined", "none", "boolean", "true", "false", "number",
    "integer", "float", "string", "mapping", "sequence", "iterable",
    "even", "odd", "divisibleby", "equalto", "eq", "ne", "lt", "le", "gt", "ge", "in",
    "is_number", "match", "search",
})
# String methods, allowed on any receiver except a states.* path, whose state
# objects stay out of reach; read a value with states() first.
HELPER_TEMPLATE_STRING_METHODS = frozenset({
    "strip", "lstrip", "rstrip", "removeprefix", "removesuffix", "split",
    "startswith", "endswith", "replace", "lower", "upper",
})
HELPER_TEMPLATE_NODE_TYPES = frozenset({
    "Template", "Output", "TemplateData", "Const", "List", "Tuple", "Dict", "Pair",
    "Add", "Sub", "Mul", "Div", "FloorDiv", "Mod", "Pow", "Neg", "Pos",
    "And", "Or", "Not", "Compare", "Operand", "CondExpr", "Concat", "Keyword",
    "Call", "Filter", "Test", "Name", "Getattr", "Getitem", "If",
    "Assign", "AssignBlock", "For",
})
# Per-resource FIFO retention: the newest N versions per (resource_type,
# resource_id) are kept; older ones are evicted on write.
MAX_VERSIONS_PER_RESOURCE = 20
# Resource types eligible for version history. VersionStore.record raises on anything absent
# and _record_version swallows that, so a missing type silently loses its rollback path.
# Adding a _record_version call site means adding the type here and to the frontend contract
# list.
VERSIONED_RESOURCE_TYPES = frozenset(
    {"automation", "script", "scene", "helper", "dashboard", "entity", "device",
     "blueprint", "energy", "config_entry", "area", "floor", "label", "category"}
)

# Dashboard card catalog. Which Lovelace cards this instance can actually render,
# harvested by the panel frontend and cached here so the MCP tool surface can
# answer which cards exist before an agent authors one. It MUST be harvested in a
# browser: a card's type strings frequently do not exist as literals in its
# minified bundle (some card sets build their type names by string concatenation
# at runtime), so parsing the JS on disk finds only a fraction of a real catalog
# and silently misses the most-used card sets. window.customCards is the registry
# HA's own card picker reads, and only a browser that imported the resources has it.
CARD_CATALOG_STORAGE_KEY = "phoenix_mcp_card_catalog"
CARD_CATALOG_STORAGE_VERSION = 1
# Bounds on an ingest payload. The catalog is admin-supplied and third-party
# shaped, so every dimension is capped before it reaches .storage.
MAX_CARD_CATALOG_ENTRIES = 500
MAX_CARD_STUB_CONFIG_BYTES = 2000
MAX_CARD_CATALOG_FAILURES = 50
# HA's own card types: the card picker's core cards plus the Energy cards. A bare
# type in this set is a built-in card, available without any harvest.
# It follows the current HA card picker; a type an older HA lacks only misses its
# built-in answer.
BUILTIN_CARD_TYPES = frozenset({
    "alarm-panel", "alert", "area", "button", "calendar", "clock", "conditional", "date",
    "distribution", "entities", "entity", "entity-filter", "gauge", "glance", "grid", "heading",
    "history-graph", "horizontal-stack", "humidifier", "iframe", "light", "logbook", "map",
    "markdown", "media-control", "picture", "picture-elements", "picture-entity",
    "picture-glance", "plant-status", "sensor", "shortcut", "statistic", "statistics-graph",
    "thermostat", "tile", "todo-list", "vertical-stack", "weather-forecast",
    "energy-compare", "energy-carbon-consumed-gauge", "energy-date-selection",
    "energy-devices-graph", "energy-distribution", "energy-gas-graph", "energy-grid-balance",
    "energy-grid-neutrality-gauge", "energy-sankey", "energy-self-sufficiency-gauge",
    "energy-solar-consumed-gauge", "energy-solar-graph", "energy-sources-table",
    "energy-usage-graph", "energy-water-graph", "water-flow-sankey", "water-sankey",
})
# A Voice model call that has sent nothing after this long is abandoned and the
# speaker hears that the provider is too slow, instead of waiting out the
# silent-stream bound. A healthy provider starts its stream within seconds.
VOICE_FIRST_REPLY_SECONDS = 30

# Server-owned conversation behavior values. These are persisted settings rather
# than provider options: every provider receives the same prompt contract, and
# invalid or retired values fall back safely when older storage is loaded.
CONVERSATION_STYLE_DIRECT = "direct"
CONVERSATION_STYLE_WARM = "warm"
CONVERSATION_STYLE_CALM_GUIDE = "calm_guide"
CONVERSATION_STYLE_LIVELY = "lively"
CONVERSATION_STYLE_TECHNICAL = "technical"
CONVERSATION_STYLES = frozenset({
    CONVERSATION_STYLE_DIRECT,
    CONVERSATION_STYLE_WARM,
    CONVERSATION_STYLE_CALM_GUIDE,
    CONVERSATION_STYLE_LIVELY,
    CONVERSATION_STYLE_TECHNICAL,
})
DETAIL_LEVEL_CONCISE = "concise"
DETAIL_LEVEL_BALANCED = "balanced"
DETAIL_LEVEL_DETAILED = "detailed"
DETAIL_LEVELS = frozenset({
    DETAIL_LEVEL_CONCISE,
    DETAIL_LEVEL_BALANCED,
    DETAIL_LEVEL_DETAILED,
})

# Assist bridge (assist_api.py): a registered llm.API exposes the bound access key's
# scoped tool surface to HA's native Assist/voice conversation agents.
# Sentinel client_ip so Assist-driven tool calls are attributable in the audit
# log without a core change to _dispatch_mcp (mirrors AGENT_CHAT_CLIENT_IP).
ASSIST_CLIENT_IP = "assist"
# Sentinel client_ip for the Phoenix MCP voice agent (voice_agent.py): Phoenix MCP registered as
# HA's conversation agent, running its own model loop. Distinct from ASSIST_CLIENT_IP
# (the llm.API bridge, which uses HA's model) for audit clarity; both suppress the
# stale-tools advisory since each rebuilds its tool list every turn.
VOICE_AGENT_CLIENT_IP = "voice"
# Sentinel client_ip for the Phoenix MCP AI Task entity (ai_task.py): Phoenix MCP registered as an
# HA AITaskEntity, running its own model loop for ai_task.generate_data. Distinct
# from the voice/assist sentinels for audit clarity; suppresses the stale-tools
# advisory like the others (it rebuilds its tool list every task).
AI_TASK_CLIENT_IP = "ai_task"
# MESA (semantic safety layer) integration. Profiles persist in a separate
# Store from access keys so the two storage versions evolve independently.
# The inheritance levels a profile can be authored AT, most specific first.
# Deployment defaults are deliberately absent: they are a single fallback record
# rather than a keyed level, and every surface treats them separately.
#
# Hand-mirrored into the panel (the scope union, the label maps, the editor's
# API dispatch, the list table, and the in-context injector), where a missing
# entry is mostly SILENT rather than a type error.
MESA_SCOPES: tuple[str, ...] = ("entity", "device", "area", "integration", "domain")

MESA_STORAGE_KEY = "phoenix_mcp_mesa"
MESA_STORAGE_VERSION = 1
# How long a mesa-core worker waits for a registry lookup handed to the event
# loop. One lookup takes milliseconds; the bound only stops a worker from
# waiting forever on a loop that has stopped.
MESA_HOST_CALLBACK_TIMEOUT_SECONDS = 30
# How often Phoenix asks mesa-core to drop advisory leases that have run out,
# which is what fires their natural-expiry event. Leases last at most 30
# seconds, so an expiry is reported at most this late.
MESA_LEASE_SWEEP_INTERVAL = datetime.timedelta(seconds=2)
# Global enforcement mode for the vendored MesaEnforcer. "off" disables MESA
# entirely; "advisory" warns but never blocks (except read_only, which is
# entity-nature, not policy); "enforced" blocks and routes confirm through the
# admin approval gate. Default advisory: the zero-profile domain baseline is
# aggressive (lock/alarm prohibited), so enforced is opt-in.
MESA_MODE_OFF = "off"
MESA_MODE_ADVISORY = "advisory"
MESA_MODE_ENFORCED = "enforced"
MESA_MODES = frozenset({MESA_MODE_OFF, MESA_MODE_ADVISORY, MESA_MODE_ENFORCED})
# Sentinel cap_name on a PendingApproval created by a MESA control_mode:confirm
# block. It is not a real capability; the approval re-validation path special-
# cases it so effective_cap() (which would auto-deny an unknown cap) is skipped.
MESA_CONFIRM_CAP = "mesa_control_mode"
# Executor key for re-running a MESA-gated service call after admin approval.
# Registered in tool_catalog._EXECUTOR_REGISTRY but never dispatchable from the
# tool router, so an access key cannot invoke the approved-bypass path directly.
MESA_APPROVED_EXECUTOR = "call_service_mesa_approved"

# The optional injector is qualified at the supported base release. Its separate
# gate also retains frontend availability checks because it depends on DOM shape.
MESA_INJECT_MIN_HA = MIN_HA_VERSION

# MESA profile suggestions (mesa_suggestions.py). Two signals; suggestions are
# computed admin-side and NEVER auto-applied.
MESA_SUGGEST_SIGNAL_BLAST_RADIUS = "blast_radius"
MESA_SUGGEST_SIGNAL_NAKED_RISKY = "naked_risky"

# Curated risky domains for the naked-risky signal: each domain maps to its
# (suggested control_mode, concern text surfaced verbatim in the suggestion reason).
# lock/alarm suggest "prohibited" to MATCH mesa-core's zero-profile baseline,
# so applying a suggestion is never a relaxation under enforced mode; the
# rest suggest "confirm".
MESA_SUGGEST_RISKY_DOMAINS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "lock": ("prohibited", "controls a physical security boundary; a mistaken unlock cannot be undone remotely"),
    "alarm_control_panel": ("prohibited", "arms and disarms the home security system; a silent disarm defeats it"),
    "cover": ("confirm", "opens a physical entry point"),
    "valve": ("confirm", "controls water or gas flow; a wrong state can flood or vent unattended"),
    "siren": ("confirm", "sounds a loud alarm; false activation disturbs occupants and neighbours"),
    "water_heater": ("confirm", "reaches scald temperatures and burns energy; slow to recover"),
    "update": (
        "confirm",
        "receives firmware updates that are effectively irreversible and can brick the device",
    ),
})
# cover is risky only for these device classes (blinds and shades are noise).
MESA_SUGGEST_COVER_DEVICE_CLASSES = frozenset({"garage", "door", "gate"})
# The blast-radius signal fires on fan-out alone at this many referenced entities.
MESA_SUGGEST_FANOUT_THRESHOLD = 8
# The naked-risky signal consolidates to ONE domain-level suggestion at this many
# uncovered entities in one domain (cover exempt: a domain profile would catch
# blinds too).
MESA_SUGGEST_CONSOLIDATE_THRESHOLD = 5
# Cap on entity ids carried in a suggestion's evidence payload.
MESA_SUGGEST_MAX_EVIDENCE_IDS = 10

SENSITIVE_ATTRIBUTES = frozenset({
    "entity_picture",
    "stream_url",
    "access_token",
    "still_image_url",
})

# Substrings (matched case-insensitively against a key name) that mark a value as
# sensitive regardless of which integration produced it. Such values are dropped
# from state attributes and replaced with "<redacted>" in service-response and
# event data. Defense in depth: third-party integrations can surface secrets
# (tokens, passwords, API keys) under arbitrary attribute/response keys that the
# fixed SENSITIVE_ATTRIBUTES list does not name. Over-redaction is the safe
# failure mode here, so the substrings are matched liberally.
SENSITIVE_KEY_SUBSTRINGS = frozenset({
    "password", "secret", "api_key", "apikey", "access_token",
    "auth_token", "authorization", "credential", "private_key",
    "token", "session", "community",
    "access_key_value", "access_key_hash", "accesskeyvalue", "accesskeyhash",
    "passphrase", "passcode", "passkey", "pincode", "pin_code",
    "wifi_key", "wifikey", "wpa_key", "wpakey", "encryption_key", "encryptionkey",
    "network_key", "networkkey", "pre_shared_key", "preshared_key", "presharedkey",
    "shared_key", "signing_key", "ssh_key", "license_key", "licensekey",
    "passwd", "pwd", "bearer",
})
# Too short to match as substrings ("ping", "shipping", "upskill", "bypass"), so these
# mark a secret only as the final word of a key name, as in wpa_psk, door-pin or mqtt_pass.
SENSITIVE_KEY_FINAL_WORDS = frozenset({"pin", "psk", "pass"})
# Credential suffixes also catch integration-prefixed secrets without treating
# access_key_name and access_key_id metadata as credential values.
SENSITIVE_KEY_SUFFIXES = ("access_key", "accesskey")

# The placeholder every redacting reader substitutes for a value the key may not see. Named
# so a write path can refuse to persist it (`tool_common.redaction_sentinel_path`), as a
# config read is lossy and an echo would store the placeholder as real configuration. The
# redacting producers spell the literal inline, deliberately, and must keep emitting this
# value.
REDACTION_SENTINEL = "<redacted>"

# Domain-aware "lean" view for get_state: without `fields` or `detailed`, a state narrows to
# entity_id, state, LEAN_ALWAYS_ATTRS and the domain's attributes below. Selection runs
# after scrubbing, so it cannot reveal a scrubbed value.
#
# An allowlist of HA's canonical attributes per domain: an unlisted attribute is omitted,
# and the fix is to add it here. Excluded on purpose: large blobs, long free text, *_list
# capability attributes and location coordinates. Names are HA's own (counter uses
# minimum/maximum), so re-verify on upgrades.
LEAN_ALWAYS_ATTRS = ("friendly_name",)

DOMAIN_IMPORTANT_ATTRIBUTES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "light": ("brightness", "color_mode", "color_temp_kelvin", "rgb_color", "effect",
              "supported_color_modes"),
    "climate": ("current_temperature", "temperature", "target_temp_high", "target_temp_low",
                "min_temp", "max_temp", "current_humidity", "hvac_action", "hvac_mode",
                "hvac_modes", "preset_mode", "fan_mode", "swing_mode"),
    "media_player": ("media_title", "media_artist", "source", "volume_level", "is_volume_muted",
                     "media_content_type", "media_position", "media_duration", "app_name"),
    "cover": ("current_position", "current_tilt_position", "device_class"),
    "valve": ("current_position", "device_class"),
    "fan": ("percentage", "preset_mode", "oscillating", "direction"),
    "sensor": ("device_class", "unit_of_measurement", "state_class"),
    "binary_sensor": ("device_class",),
    "lock": ("changed_by",),
    "alarm_control_panel": ("changed_by",),
    "vacuum": ("battery_level", "fan_speed", "status"),
    "humidifier": ("current_humidity", "humidity", "mode"),
    "water_heater": ("current_temperature", "temperature", "operation_mode"),
    "weather": ("temperature", "humidity", "wind_speed"),
    "device_tracker": ("source_type",),
    "number": ("min", "max", "step", "mode", "unit_of_measurement"),
    "select": ("options",),
    "switch": ("device_class",),
    "update": ("installed_version", "latest_version", "in_progress", "update_percentage", "title"),
    "timer": ("duration", "remaining", "finishes_at"),
    "sun": ("elevation", "next_dawn", "next_dusk", "next_rising", "next_setting"),
    "automation": ("last_triggered", "mode", "current", "max"),
    "script": ("last_triggered", "mode", "current", "max", "last_action"),
    "input_number": ("min", "max", "step", "mode"),
    "input_select": ("options",),
    "input_text": ("min", "max", "pattern", "mode"),
    "counter": ("minimum", "maximum", "step"),
    "calendar": ("message", "start_time", "end_time", "all_day", "location"),
    "remote": ("current_activity",),
})

BLOCKED_DOMAINS = frozenset({"phoenix_mcp"})

HIGH_RISK_DOMAINS = frozenset({
    "homeassistant",
    "recorder",
    "system_log",
    "hassio",
    "backup",
    "notify",
    "persistent_notification",
    "mqtt",
})

DUAL_GATE_SERVICES = frozenset({
    "homeassistant/restart",
    "homeassistant/stop",
})
# Music Assistant library reads name a config entry instead of an entity target
# and only return a response. call_service admits them with cap_service_response
# when the access key can read an entity of that Music Assistant entry.
MUSIC_ASSISTANT_DOMAIN = "music_assistant"
MUSIC_ASSISTANT_LIBRARY_SERVICES = frozenset({
    "music_assistant/search",
    "music_assistant/get_library",
})
# Services that interrupt a speaker with an announcement. Write on the speaker is
# not enough; they also need cap_broadcast, as HassBroadcast does. tts.speak and the
# legacy tts.<engine>_say services play through media_player.play_media with announce.
ANNOUNCEMENT_SERVICES = frozenset({
    "assist_satellite/announce",
    "assist_satellite/ask_question",
    "assist_satellite/start_conversation",
    "music_assistant/play_announcement",
    "tts/speak",
})
# tts.speak targets a tts entity and names its speakers in a data field, the one secondary
# target field Phoenix models: each named player joins the resolved target list, so WRITE,
# group, MESA and approval binding all judge it, and dispatch hands it back to this field.
TTS_SPEAK_SERVICE = "tts/speak"
TTS_SPEAK_ENGINE_DOMAIN = "tts"
TTS_SPEAK_PLAYER_DOMAIN = "media_player"
TTS_SPEAK_PLAYER_FIELD = "media_player_entity_id"
# Home Assistant's generic services act on an entity of any domain. Listed so they
# stay available when service descriptions cannot be read; every other service is
# bound to its targets by what its own description declares.
CROSS_DOMAIN_SERVICES = frozenset({
    "homeassistant/turn_on",
    "homeassistant/turn_off",
    "homeassistant/toggle",
    "homeassistant/update_entity",
})
# call_service reaches these no-target services by capability alone, so it is
# announced to an access key holding one of them even without entity write scope.
CALL_SERVICE_NO_TARGET_CAPS = ("cap_restart", "cap_yaml_edit")

SCRIPT_ENTITY_SERVICES = frozenset({"turn_on", "turn_off", "toggle"})
# Governed by the entity itself, never looked through as a group, though a scene publishes
# its members in the attribute a group uses.
PRIMARY_ENTITY_DOMAINS = frozenset({"automation", "scene", "script"})
RUNTIME_SERVICE_GUIDANCE = MappingProxyType({
    "automation/trigger": "Run actions now; skip_condition defaults to true. Does not enable future triggers.",
    "automation/turn_on": "Enable future triggers without running actions now.",
    "automation/turn_off": "Disable future triggers; stop_actions defaults to true and also stops running actions.",
    "automation/toggle": "Toggle whether future triggers are enabled.",
    "script/turn_on": "Start the script without waiting for its sequence to finish; pass inputs in variables.",
    "script/turn_off": "Stop all running executions. The script remains available to run again.",
    "script/toggle": "Start an idle script or stop its running executions.",
    "scene/turn_on": "Activate the scene; later member effects run under Home Assistant.",
    "tts/speak": "Speak on the media players named in media_player_entity_id; every one needs write access, and the call needs the broadcast capability.",
})

# Core HA services that take no target: they re-read YAML configuration domain-wide, and
# their schemas reject the explicit entity_id list Phoenix always sends. A dedicated
# no-target branch calls them with bare service_data, gated on cap_yaml_edit. A conservative
# list of stable core reloads, to re-verify on upgrades. homeassistant/reload_config_entry
# is excluded, as it needs the entry's own scope and only reload_integration runs it.
NO_TARGET_SERVICES = frozenset({
    "homeassistant/reload_all",
    "homeassistant/reload_core_config",
    "automation/reload",
    "script/reload",
    "scene/reload",
    "input_boolean/reload",
    "input_button/reload",
    "input_number/reload",
    "input_text/reload",
    "input_select/reload",
    "input_datetime/reload",
    "template/reload",
    "group/reload",
    "zone/reload",
    "person/reload",
    "schedule/reload",
    "counter/reload",
    "timer/reload",
})

# Capability modes. An access key's per-capability state is one of these three values.
CAP_DENY = "deny"
CAP_ALLOW = "allow"
CAP_CONFIRM = "confirm"
CAP_MODES = frozenset({CAP_DENY, CAP_ALLOW, CAP_CONFIRM})
CAP_GRANT_TRANSITIONS = frozenset({(CAP_DENY, CAP_CONFIRM), (CAP_DENY, CAP_ALLOW), (CAP_CONFIRM, CAP_ALLOW)})

# Canonical list of all capability names. Every cap_* field on AccessKeyRecord
# must appear here. Adding a new cap is a three-step change: add to this list,
# add to CAPABILITY_TIERS, and update the persona table in personas.py.
CAPABILITY_NAMES = (
    "cap_config_read",
    "cap_camera_read",
    "cap_template_render",
    "cap_log_read",
    "cap_log_control",
    "cap_search",
    "cap_registry_read",
    "cap_traces",
    "cap_diagnostics",
    "cap_broadcast",
    "cap_notify",
    "cap_service_response",
    "cap_automation_write",
    "cap_script_write",
    "cap_blueprint_write",
    "cap_scene_write",
    "cap_helper_write",
    "cap_calendar_write",
    "cap_physical_control",
    "cap_restart",
    "cap_integration_write",
    "cap_integration_reconfigure",
    "cap_lovelace_write",
    "cap_registry_write",
    "cap_radio_write",
    "cap_energy_write",
    "cap_dashboard_resources",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
    "cap_esphome_yaml",
    "cap_esphome_flash",
)

# Tiers drive UI grouping and which capabilities offer Confirm.
# Read and Everyday tiers are deny/allow only; the others support Confirm.
CAPABILITY_TIERS: Mapping[str, str] = MappingProxyType({
    "cap_config_read": "read",
    "cap_camera_read": "read",
    "cap_template_render": "read",
    "cap_log_read": "read",
    "cap_log_control": "system",
    "cap_search": "read",
    "cap_registry_read": "read",
    "cap_traces": "read",
    "cap_diagnostics": "read",
    "cap_broadcast": "everyday",
    # Messages reach people's phones and screens: an agent could spam or
    # impersonate, so it sits with the other actions on the household.
    "cap_notify": "system",
    "cap_service_response": "everyday",
    "cap_automation_write": "config_write",
    "cap_script_write": "config_write",
    "cap_blueprint_write": "config_write",
    "cap_scene_write": "config_write",
    "cap_helper_write": "config_write",
    "cap_calendar_write": "config_write",
    "cap_physical_control": "system",
    "cap_restart": "system",
    "cap_integration_write": "system",
    "cap_integration_reconfigure": "system",
    "cap_lovelace_write": "system",
    "cap_registry_write": "system",
    "cap_radio_write": "system",
    # Energy dashboard preferences: instance-wide dashboard configuration, so it
    # sits with cap_lovelace_write rather than in config_write, which holds the
    # things that define BEHAVIOUR (automations, scripts, scenes). Nothing here
    # actuates a device; a wrong write costs you the Energy setup, not the house.
    "cap_energy_write": "system",
    # Dashboard resources are the JavaScript and CSS files every browser loads
    # when it opens a dashboard, so registering a JavaScript file runs code
    # with the viewing person's Home Assistant session. That is why it is separate from
    # cap_lovelace_write (layouts are data) and sits in the irreversible tier.
    "cap_dashboard_resources": "irreversible",
    "cap_backup": "irreversible",
    # Both are reads of configuration the operator owns; cap_yaml_edit also gates
    # the no-target reload services that apply that configuration.
    "cap_filesystem": "read",
    "cap_yaml_edit": "system",
    # Device YAML reads plus Device Builder work (validate, compile, jobs, logs)
    # that touches no device; putting firmware on hardware is cap_esphome_flash.
    "cap_esphome_yaml": "everyday",
    # Flashing is not config authoring: it actuates hardware, and a bad image can
    # leave a device unreachable until someone reaches it with a cable. It sits
    # with cap_radio_write, its nearest sibling in consequence.
    "cap_esphome_flash": "system",
})

# Capabilities for which Confirm is a meaningful third state.
# UI hides the Confirm option for caps not in this set.
CONFIRM_AVAILABLE_CAPS = frozenset({
    "cap_notify",
    "cap_automation_write",
    "cap_script_write",
    "cap_blueprint_write",
    "cap_scene_write",
    "cap_helper_write",
    "cap_calendar_write",
    "cap_physical_control",
    "cap_restart",
    "cap_integration_write",
    "cap_integration_reconfigure",
    "cap_log_control",
    "cap_lovelace_write",
    "cap_registry_write",
    "cap_radio_write",
    "cap_energy_write",
    "cap_dashboard_resources",
    "cap_backup",
    "cap_yaml_edit",
    "cap_esphome_flash",
})

# Capabilities that never run without a person's approval: Allow is not offered,
# and a stored Allow reads as Confirm. A dashboard resource runs in every viewer's
# browser, so an agent, or a prompt injection reaching one, must not add it unreviewed.
CONFIRM_ONLY_CAPS = frozenset({"cap_dashboard_resources"})

# Capabilities ALWAYS evaluated regardless of pass_through state.
# All other capabilities are bypassed (treated as "allow") when pass_through is True,
# except that "confirm" is honored even for non-exempt caps under pass_through
# (admin's intent to gate is preserved). See capability.effective_cap.
PASS_THROUGH_EXEMPT_CAPS = frozenset({
    "cap_notify",
    "cap_camera_read",
    "cap_restart",
    "cap_physical_control",
    "cap_automation_write",
    "cap_script_write",
    "cap_blueprint_write",
    "cap_log_read",
    "cap_log_control",
    "cap_scene_write",
    "cap_helper_write",
    "cap_calendar_write",
    "cap_integration_write",
    "cap_integration_reconfigure",
    "cap_lovelace_write",
    "cap_registry_write",
    "cap_radio_write",
    "cap_energy_write",
    "cap_dashboard_resources",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
    "cap_esphome_yaml",
    "cap_esphome_flash",
})

# Non-administrator and unidentified household ingress cannot use admin tools.
# These capabilities still require the bound access key's ordinary gates.
HOUSEHOLD_CAPABILITIES = frozenset({
    "cap_search", "cap_physical_control", "cap_broadcast", "cap_service_response", "cap_registry_read",
})
HOUSEHOLD_ADMIN_TOOLS = frozenset({"get_audit_summary", "get_approval_status", "wait_for_approval"})

# Zigbee2MQTT bridge API. Phoenix MCP talks to Z2M over its MQTT request/response
# topics (<base>/bridge/request/... answered on <base>/bridge/response/...)
# and retained state topics (<base>/bridge/info, <base>/bridge/devices).
# The base topic is Z2M's default; a custom base_topic install would need
# this changed. Version-sensitive surface (shapes verified against Z2M 2.x).
Z2M_BASE_TOPIC = "zigbee2mqtt"
Z2M_REQUEST_TIMEOUT_SECONDS = 30.0
Z2M_RETAINED_READ_TIMEOUT_SECONDS = 5.0
# How long a Zigbee write's read-back waits for the backend to report the change.
ZIGBEE_CONFIRM_SECONDS = 3.0
# Allowance between the requested join window and the end Zigbee2MQTT reports.
ZIGBEE_PERMIT_END_TOLERANCE_SECONDS = 10
# A topology request actively queries each router and Zigbee2MQTT documents a
# normal completion window of ten seconds to two minutes. MCP's SSE keepalives
# keep the client connection alive while this longer, approval-gated operation
# runs; the ordinary 30-second radio timeout would turn healthy large meshes
# into false failures.
ZIGBEE_TOPOLOGY_SCAN_TIMEOUT_SECONDS = 125.0

# Proxy diagnostics snapshots (proxy_diagnostics.py). Nearby Bluetooth devices
# heard within the age limit, newest first; a busy street can advertise hundreds.
PROXY_NEARBY_MAX_DEVICES = 50
PROXY_NEARBY_MAX_AGE_SECONDS = 300
PROXY_NEARBY_MAX_SERVICE_UUIDS = 8
PROXY_DISCOVERED_MAX_FLOWS = 50
PROXY_SERIAL_MAX_PORTS = 64
PROXY_TEXT_MAX_CHARS = 120
# Device options are a local config write, not a sleepy-device operation. Give
# the correlated response a short window, then verify the retained config.
Z2M_DEVICE_OPTIONS_RESPONSE_TIMEOUT_SECONDS = 5.0

# Pending-approval queue limits.
MAX_PENDING_APPROVALS_PER_ACCESS_KEY = 100
APPROVAL_DEFAULT_TTL_SECONDS = 3600
APPROVAL_SWEEP_INTERVAL = datetime.timedelta(minutes=5)

# How many approvals one batch-approve request may carry. A REQUEST-DURATION
# bound, not a review-policy one: admin.approvals._approve_approval runs each
# executor INLINE inside the admin's HTTP request (the same constraint that makes
# ESPHome builds enqueue a job instead), so a batch's wall time is the sum of its
# tools' and an unbounded one would hold a connection open for minutes and read
# as a hang. Set well above any realistic hand-selected batch.
MAX_BATCH_APPROVALS = 50

# Diff size limits for approval records.
MAX_DIFF_INLINE_BYTES = 100_000
MAX_PREVIEW_ENTITY_IDS = 500

# Persona identifiers. Definitions live in personas.py.
PERSONA_READ_ONLY = "read_only"
PERSONA_VOICE_ASSISTANT = "voice_assistant"
PERSONA_AUTOMATION_BUILDER = "automation_builder"
PERSONA_POWER_USER = "power_user"
PERSONA_HOME_ADMIN = "home_admin"
PERSONA_CUSTOM = "custom"
PERSONA_HOME_COMPANION = "home_companion"
# Gentle starter persona: reads plus service calls, physical control gated to
# confirm. Seeded by the onboarding wizard and also offered in the normal picker.
PERSONA_NEW_USER = "new_user"
# Dashboard/UI work: reads + registry + dashboard write; filesystem confirm for
# theme and custom-card assets. No device control.
PERSONA_DASHBOARD_DESIGNER = "dashboard_designer"
# Routine upkeep: full reads + diagnostics + backups; restart gated to confirm.
# No config authoring or device control.
PERSONA_MAINTENANCE = "maintenance"
# ESPHome device work: diagnostic reads, device-YAML reads and Device Builder
# work, with flashing gated to confirm. No entity control, no configuration
# authoring. Seeds caps only, so the admin still scopes the entity tree to the
# ESPHome devices.
PERSONA_ESPHOME = "esphome"
PERSONA_NAMES = frozenset({
    PERSONA_HOME_COMPANION,
    PERSONA_READ_ONLY,
    PERSONA_VOICE_ASSISTANT,
    PERSONA_AUTOMATION_BUILDER,
    PERSONA_POWER_USER,
    PERSONA_HOME_ADMIN,
    PERSONA_NEW_USER,
    PERSONA_DASHBOARD_DESIGNER,
    PERSONA_MAINTENANCE,
    PERSONA_ESPHOME,
    PERSONA_CUSTOM,
})

# Domains whose services require cap_physical_control even when pass_through is
# True: irreversible or safety-relevant physical actions. The gate covers the whole
# domain rather than a list of service names, so a service added later is gated too.
# Used by call_service and by the native Hass* action tools (HassTurnOn/Off, HassSetPosition) to physical-gate targets
# the same way call_service gates the underlying domain services, so a valve is
# gated whether it is actuated via valve/open_valve or via HassTurnOn.
PHYSICAL_GATE_DOMAINS = frozenset({"lock", "alarm_control_panel", "cover", "valve"})
# A service outside those domains is gated when a resolved target lies in one, so
# nuki.lock_n_go needs the capability on a lock. These raw command services filter on the
# integration, not the entity: any entity of a device reaches its lock, so they are physical
# whatever the target. Read from every core services.yaml; re-verify on HA upgrades.
PHYSICAL_COMMAND_SERVICES = frozenset({
    "isy994/send_node_command",
    "isy994/send_raw_node_command",
    "isy994/set_zwave_parameter",
    "zwave_js/bulk_set_partial_config_parameters",
    "zwave_js/invoke_cc_api",
    "zwave_js/multicast_set_value",
    "zwave_js/set_config_parameter",
    "zwave_js/set_value",
})
# Services that take an entity and cannot actuate it, or do nothing on physical and notify
# domains, so such a target adds no capability. A deliberate list: an unknown service on a
# lock stays gated, and readers that return door codes (schlage.get_codes,
# zwave_js.get_lock_usercode) stay out of it. homeassistant.turn_on and turn_off call the
# target domain's own turn_on or turn_off, which none of these domains has; toggle is not
# here because cover and valve have one.
NON_ACTUATING_SERVICES = frozenset({
    "homeassistant/turn_off",
    "homeassistant/turn_on",
    "homeassistant/update_entity",
    "logbook/log",
    "zwave_js/ping",
    "zwave_js/refresh_notifications",
    "zwave_js/refresh_value",
})

# The keys HA's TargetSelection reads out of a service call's data. Each must be stripped
# from caller data, as HA unions a leftover selector with Phoenix's explicit entity list,
# reaching entities the permission tree, the gates and MESA never saw.
#
# Not in this set:
#  - "target": a target block only at HA's WS/REST boundary, and a real data field for
#    notify. unsupported_secondary_targets refuses the mapping form.
#  - "config_entry_id": TargetSelection does not read it. MESA refuses it on an entity call.
#
# "exclude" only narrows, but after Phoenix resolved targets, so a call would reach fewer
# entities than were reviewed; list the wanted entities instead.
#
# Must match HA's cv.TARGET_SERVICE_FIELDS on every release, or a new selector widens reach.
TARGET_SELECTOR_KEYS = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id", "exclude"})

# Server-owned exact control bindings, persisted with approval executor arguments.
APPROVAL_TARGETS_KEY = "_phoenix_approval_targets"
APPROVAL_TARGETS_VERSION = 1
APPROVAL_TARGET_LIMIT = 1024
APPROVAL_TARGET_DEPTH = 16
APPROVAL_TARGET_ID_CHARS = 255

# Service fields whose selectors name additional HA entities, devices, or areas
# outside the service's ordinary target block. Home Assistant integrations use
# these for destinations, group members, source players, rooms, and similar
# secondary targets. Phoenix cannot authorize them by flattening the primary
# target, so call_service refuses them until a dedicated adapter models the
# field's exact authorization semantics. The runtime also discovers selector
# fields from HA's service descriptions; this set is the fail-closed fallback
# when a description cannot be loaded and an upgrade guard for newly added keys.
SECONDARY_TARGET_SELECTOR_KEYS = frozenset({
    "add_entities",
    "account",
    "bridge",
    "cleaning_area_id",
    "config_entry",
    "config_entry_id",
    "container_device_id",
    "device_ids",
    "doorbells",
    "domains",
    "entities",
    "entity",
    "entity_globs",
    "entry",
    "entry_id",
    "group_members",
    "master",
    "media_player",
    "media_player_entity_id",
    "pot",
    "remove_entities",
    "rooms",
    "slaves",
    "snapshot_entities",
    "source_player",
    "target_device",
    "tts_entity_id",
    "transfer_id",
    "vehicle",
})

# Service-name hints for core actuator domains whose real verbs differ from the naive guess
# (valve.open_valve, cover.open_cover). On ServiceNotFound, Phoenix returns an
# invalid_request naming these rather than the generic "Forbidden.".
#
# Leak safety: read only inside that post-authorization catch, after the cap gate, WRITE
# scope and MESA. The values are hardcoded public core names, never a live lookup, so a hint
# reveals nothing about custom services or unreachable domains. Absent domains keep
# "Forbidden.". Re-verify names on HA upgrades; obvious turn_on/turn_off domains are
# omitted.
DOMAIN_SERVICE_HINTS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "valve": ("open_valve", "close_valve", "stop_valve", "set_valve_position"),
    "cover": (
        "open_cover", "close_cover", "stop_cover", "set_cover_position",
        "open_cover_tilt", "close_cover_tilt", "set_cover_tilt_position",
    ),
    "lock": ("lock", "unlock", "open"),
    "climate": (
        "set_temperature", "set_hvac_mode", "set_fan_mode", "set_preset_mode",
        "set_humidity", "set_swing_mode", "turn_on", "turn_off",
    ),
    "fan": (
        "turn_on", "turn_off", "set_percentage", "set_preset_mode",
        "oscillate", "set_direction", "increase_speed", "decrease_speed",
    ),
    "vacuum": (
        "start", "stop", "pause", "return_to_base", "clean_spot",
        "locate", "set_fan_speed", "send_command",
    ),
    "humidifier": ("turn_on", "turn_off", "set_humidity", "set_mode"),
    "water_heater": (
        "set_temperature", "set_operation_mode", "set_away_mode",
        "turn_on", "turn_off",
    ),
    "media_player": (
        "turn_on", "turn_off", "media_play", "media_pause", "media_stop",
        "media_next_track", "media_previous_track", "volume_set", "volume_mute",
        "select_source", "play_media",
    ),
    "alarm_control_panel": (
        "alarm_arm_away", "alarm_arm_home", "alarm_arm_night",
        "alarm_arm_vacation", "alarm_disarm", "alarm_trigger",
    ),
    "lawn_mower": ("start_mowing", "pause", "dock"),
})

# assist_satellite feature bit for ANNOUNCE support.
ANNOUNCE_BIT = 2

# vacuum feature bits (VacuumEntityFeature), mirrored rather than imported so
# nothing here pulls a component module into the import graph; the ANNOUNCE_BIT
# precedent. HA's own vacuum intents set required_features, so an entity that
# cannot perform the operation is never targeted rather than being asked and
# erroring, and the native tools mirror that.
# HA-VERSION-SENSITIVE: a renumbered bit would silently target the wrong vacuums.
VACUUM_START_BIT = 8192
VACUUM_RETURN_HOME_BIT = 16
VACUUM_CLEAN_AREA_BIT = 16384

# Maximum number of log buckets returned by the MCP diagnostics tools.
MAX_LOG_ENTRIES = 100

# System-log preview/detail budgets use ASCII-escaped JSON byte accounting.
SYSTEM_LOG_MAX_MESSAGE_BYTES = 2048
SYSTEM_LOG_MAX_EXCEPTION_BYTES = 4096
SYSTEM_LOG_MAX_METADATA_BYTES = 512
SYSTEM_LOG_MAX_MESSAGE_VARIANTS = 5
SYSTEM_LOG_MAX_DETAIL_BYTES = 8192
SYSTEM_LOG_MAX_ENTRY_BYTES = 24 * 1024
SYSTEM_LOG_MAX_RESPONSE_BYTES = 32 * 1024

# Fixed crash-diagnostic source and independent input/output budgets.
FAULT_LOG_FILENAME = "home-assistant.log.fault"
FAULT_LOG_MAX_BYTES = 512 * 1024
FAULT_LOG_MAX_LINES = 2000
FAULT_LOG_MAX_BLOCK_BYTES = 32 * 1024
FAULT_LOG_MAX_PAGE_BYTES = 64 * 1024
FAULT_LOG_DEFAULT_LIMIT = 10
FAULT_LOG_MAX_CURSOR_LEN = 1024

# Logbook query bounds. Search is post-retrieval and never widens these windows.
MAX_LOGBOOK_RESOURCE_IDS = 100
MAX_LOGBOOK_HOME_DAYS = 7
MAX_LOGBOOK_NARROWED_DAYS = 31

# Levels get_logs and get_phoenix_diagnostics accept as `level`. INFO must stay absent: HA's
# system_log handler is fixed at WARNING, so offering it would promise records the store
# never keeps, and a quiet result would read as "nothing was logged". Distinct from
# log_read._LOG_LEVEL_RANK, which ranks a record's level.
#
# When to read get_capability_summary, said the same way on every surface that steers it, so
# models do not spend a call on it before every read.
ORIENTATION_GUIDANCE = (
    "Call get_capability_summary before a write or after a permission refusal; "
    "a question that only reads needs no orientation call."
)

LOG_LEVELS = ("WARNING", "ERROR")
LOG_LEVEL_ERROR_MESSAGE = (
    "level must be WARNING or ERROR. Home Assistant's system log holds WARNING "
    "and above only, so INFO and DEBUG entries never enter it and cannot be read "
    "through this tool. They are written to home-assistant.log on the server."
)
# get_logs can read them from its other source, so it says how instead.
GET_LOGS_LEVEL_ERROR_MESSAGE = (
    "level must be WARNING or ERROR. Home Assistant's system log holds WARNING "
    "and above only. To read an integration's INFO or DEBUG records, raise its "
    "level with set_integration_log_level, then call get_logs with source "
    "integration_debug and that integration."
)

# Cap on a tools/call request's "name" field before it is used for dispatch or
# logged as the audit method/resource. Every real tool name is well under this;
# it exists only to bound a malformed client (a local model emitting garbled
# tool-call syntax, e.g. stray XML-like tag debris or special tokens leaking
# into what should be a bare tool name) so the audit log cannot be bloated or
# made unreadable by an unbounded client-supplied string.
MAX_TOOL_NAME_LENGTH = 200

# Bounds on the alias set set_entity may write to an entity registry entry.
# Home Assistant does not bound them itself and the registry is persisted to
# .storage, so an agent sending a large list would bloat a file every restart
# reads. A real entity carries a handful of spoken names; these only ever clip
# something pathological. The length matches what a person would plausibly say
# to a voice assistant rather than any HA limit.
MAX_ENTITY_ALIASES = 32
MAX_ENTITY_ALIAS_LENGTH = 255

# get_relationships: how many source paths one holder of a dangling reference
# reports before the rest become a count. A single deleted script can be called
# from many sub-buttons on one dashboard, and repeating that dashboard's identity
# for each spends most of the response on nothing an operator needs. The true
# total is always reported alongside, per the truncation contract, so a clipped
# list is never mistaken for a complete one.
MAX_DANGLING_PATHS = 3

# compare_entities: how many members of a list-valued attribute are named per
# side before the rest become a count. A light's effect_list and a media
# player's source_list run to hundreds of entries, and echoing both sides in
# full answers nothing the added/removed pair does not. The true totals are
# reported alongside, per the truncation contract.
MAX_COMPARE_LIST_VALUES = 25

# compare_entities: how many characters of one string attribute value are
# shown. This bounds ONE value's length rather than a page of rows, so nothing
# a caller could have asked for separately is withheld.
MAX_COMPARE_VALUE_CHARS = 200

# wait_for_approval, PLURAL form only: how many characters of one approval's
# tool result are echoed. A batch is a list of STATUSES, not a list of results.
# Twelve approved `edit_automation` records each echo their whole config, which
# can exceed a caller's output limit, so the answer to "did my twelve writes
# land" could not be delivered at all. An ERROR's text is the
# part that has to survive, and it is short: an agent told only "rejected",
# with the executor's reason buried, retries the same doomed call. A success
# only needs to say it succeeded. The single-id form stays unbounded and is
# where a full result is read.
MAX_APPROVAL_RESULT_CHARS = 500

# Hard cap for the search_entities free-text query. Ranking tokenizes on
# whitespace and the fuzzy fallback runs difflib against every accessible
# entity, so an unbounded query could stall the event loop; a real query is far
# shorter than this.
MAX_SEARCH_QUERY_LEN = 512
# The radio types search_entities can filter by, each recognized from the device
# registry rather than inferred from names: zigbee (ZHA or Zigbee2MQTT), zwave
# (Z-Wave JS), matter (Matter over Thread or Wi-Fi) and bluetooth (devices
# reached by a Bluetooth address).
SEARCH_RADIO_TYPES = ("zigbee", "zwave", "matter", "bluetooth")
# Home Assistant records a battery reading but no mains or other power source.
SEARCH_POWER_TYPES = ("battery",)
# Bound opt-in membership normalization and disclosure across one search page.
MAX_SEARCH_MEMBERS = 256
MAX_SEARCH_MEMBER_CHECKS = 4096
MAX_SEARCH_MEMBER_ID_LEN = 255

# Hard cap (and default) for the bounded watch_entity tool.
# It blocks the tool call up to this many seconds waiting for a state change.
MAX_SUBSCRIPTION_SECONDS = 30

# How often the MCP endpoint writes to an SSE-framed response while the request
# is still being handled. A confirm gate can hold a response for up to
# MAX_CONFIRM_INLINE_WAIT_SECONDS with nothing on the wire, which is exactly the
# shape a reverse proxy drops; a frame every 15s keeps the connection alive and,
# when the client supplied a progressToken, tells it what the hold is waiting for.
MCP_SSE_KEEPALIVE_SECONDS = 15.0

# The MCP protocol revisions this transport implements, newest first. Phoenix
# is deliberately dual-era: modern requests use 2026-07-28 per-request metadata,
# while initialize selects the older 2025-03-26 session lifecycle. Response
# fields are shaped for the selected era, since modern-only fields can break
# strict legacy clients.
#
# ADDING AN ENTRY IS A COMPLIANCE CLAIM, NOT A LABEL. Do not add an intermediate
# legacy revision unless every MUST introduced by that revision is implemented.
MCP_PROTOCOL_VERSIONS: tuple[str, ...] = ("2026-07-28", "2025-03-26")
MCP_AUTHORITY_CHANGED_ERROR = 1001
MCP_LEGACY_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-03-26",)
MCP_LEGACY_PROTOCOL_VERSION_PREFERRED = MCP_LEGACY_PROTOCOL_VERSIONS[0]

# Freshness hint (milliseconds) on the server/discover result. Nothing in it is
# enforcement: identity and the version list are fixed for a build, and the
# instructions are advisory prose that every cap gate re-derives on the call
# itself, so a client holding a stale copy can be wrong about what to SAY and
# never about what it is allowed to DO.
MCP_DISCOVER_TTL_MS = 300_000

# An access key's inline wait on a confirm gate, in seconds, before the pending_approval
# reply. 0 means off and is the default; the maximum stays within typical MCP client
# timeouts.
#
# Off by default because only the agent knows whether it needs the outcome now. Holding
# blocks a request, so approval N+1 cannot be created until approval N stops waiting: at 60
# seconds, twenty writes take twenty minutes to reach the queue. Agents call
# wait_for_approval, with several ids, when they want outcomes; the wait stays opt-in.
MIN_CONFIRM_INLINE_WAIT_SECONDS = 30
MAX_CONFIRM_INLINE_WAIT_SECONDS = 180
DEFAULT_CONFIRM_INLINE_WAIT_SECONDS = 0

# search_entities similarity cutoff (difflib ratio, 0.0-1.0) for the typo-tolerant
# fallback pass. Only consulted when exact token-AND matching returns nothing, so
# it never changes results for a query that already matches; a candidate must
# score at least this against the entity_id, object_id, or friendly_name to show.
SEARCH_FUZZY_MATCH_CUTOFF = 0.6

# Directories (relative to the HA config dir) the cap_filesystem tools may touch.
# Paths are realpath-resolved and must stay within one of these, blocking traversal.
FILESYSTEM_ALLOWED_DIRS = ("www", "themes", "custom_templates")
# Maximum file size (bytes) the file and YAML read tools will return inline.
MAX_FILE_BYTES = 1_048_576
YAML_MAX_DEPTH = 64
YAML_MAX_EXPANDED_NODES = 100_000

# Targeted public-input diagnostics; unrelated extra arguments stay tolerated.
UNKNOWN_ARGUMENT_PROTECTED_FIELDS = MappingProxyType({
    "get_dashboard_config": ("url_path", "query", "entity_id", "card_type", "heading", "all_dashboards"),
    "set_dashboard_config": ("url_path", "expected_hash"),
    "add_dashboard_card": ("url_path", "view_index", "section_index", "position", "expected_hash"),
    "edit_dashboard_card": ("url_path", "view_index", "section_index", "card_index", "expected_hash"),
    "delete_dashboard_card": ("url_path", "view_index", "section_index", "card_index", "expected_hash"),
    "patch_dashboard": ("target", "changes", "expected_hash"),
    "edit_dashboard": ("dashboard_id",),
    "delete_dashboard": ("dashboard_id",),
    "list_files": ("path",),
    "read_file": ("path",),
    "get_yaml_config": ("file", "key"),
    "set_entity": ("entity_id",),
    "delete_entity": ("entity_id",),
    "set_device": ("device_id",),
    "remove_device": ("device_id", "config_entry_id"),
    "set_integration": ("entry_id",),
    "call_service": ("service", "targets"),
    "dry_run_service": ("service", "targets"),
})
UNKNOWN_ARGUMENT_ALIASES = MappingProxyType({
    "url_path": ("dashboard_url", "dashboard_url_path", "dashboard_path", "dashboard", "dashboard_id", "url"),
    "dashboard_id": ("dashboard", "dashboard_url", "url_path"),
    "file": ("filename", "file_name", "file_path", "path"),
    "path": ("filename", "file_name", "file_path", "directory"),
    "targets": ("target", "entity_ids", "device_ids", "area_ids", "entities"),
    "entity_id": ("entity", "entity_ids", "entities", "statistic_id"),
    "device_id": ("device", "device_ids"),
    "entry_id": ("integration_id", "config_entry_id"),
    "start_time": ("start",),
    "end_time": ("end",),
    "statistic_types": ("types", "statistic_type", "stat_types"),
    "device_class": ("device_classes",),
    "query": ("name",),
})
UNKNOWN_ARGUMENT_MAX_NAME = 80
UNKNOWN_ARGUMENT_MAX_ERRORS = 8

# Value-free authoring error projection budgets.
AUTHORING_MAX_ERRORS = 8
AUTHORING_MAX_PATH_DEPTH = 8
# Trace scoping replaces anything nested deeper than this with a sentinel.
TRACE_SCOPE_MAX_DEPTH = 32
# Longest HA field help (data_description or label) attached to a helper flow field.
HELPER_FIELD_HELP_MAX = 300
# Longest HA step description attached to a helper flow form.
HELPER_STEP_HELP_MAX = 600

# The delegated persona has a bounded household catalog, narrowed by capabilities.
HOME_COMPANION_TOOLS = frozenset({
    "get_capability_summary", "search_entities", "describe_entity", "describe_area",
    "get_state", "get_history", "get_statistics", "compare_states",
    "compare_entities", "get_calendar_events", "get_overview", "get_relationships",
    "recent_activity", "list_areas", "list_floors", "list_devices", "get_device",
    "homeassistant__GetLiveContext", "llm__GetDateTime", "intent__HassTurnOn", "intent__HassTurnOff",
    "light__HassLightSet", "fan__HassFanSetSpeed", "climate__HassClimateSetTemperature",
    "intent__HassSetPosition", "media_player__HassSetVolume", "media_player__HassSetVolumeRelative",
    "media_player__HassMediaPause", "media_player__HassMediaUnpause", "media_player__HassMediaNext",
    "media_player__HassMediaPrevious", "media_player__HassMediaSearchAndPlay",
    "media_player__HassMediaPlayerMute", "media_player__HassMediaPlayerUnmute",
    "intent__HassCancelAllTimers", "vacuum__HassVacuumStart", "vacuum__HassVacuumReturnToBase",
    "vacuum__HassVacuumCleanArea", "intent__HassStopMoving",
})
HOME_COMPANION_CONTROL_TOOLS = frozenset({
    name for name in HOME_COMPANION_TOOLS if "__Hass" in name
})
HOME_COMPANION_CONTROL_DOMAINS = frozenset({
    "light", "fan", "switch", "input_boolean", "climate", "cover", "valve",
    "media_player", "vacuum", "humidifier", "timer",
})
# Reads whose states can answer on their own; any other read leaves the answer to the model.
HOME_COMPANION_STATE_READ_TOOLS = frozenset({"search_entities", "get_state", "get_capability_summary"})
HOME_COMPANION_READ_DOMAINS = frozenset({"sensor", "binary_sensor", "weather", "calendar", "sun"})
HOME_COMPANION_TOOL_DOMAINS = MappingProxyType({
    "light__HassLightSet": ("light",), "fan__HassFanSetSpeed": ("fan",),
    "climate__HassClimateSetTemperature": ("climate",),
    "intent__HassSetPosition": ("cover", "valve"), "intent__HassStopMoving": ("cover",),
    "intent__HassCancelAllTimers": ("timer",),
    **{name: ("media_player",) for name in HOME_COMPANION_CONTROL_TOOLS if name.startswith("media_player__")},
    **{name: ("vacuum",) for name in HOME_COMPANION_CONTROL_TOOLS if name.startswith("vacuum__")},
})
HOME_COMPANION_MAX_FORMAT_CORRECTIONS = 1
HOME_COMPANION_FORMAT_ERROR_CODES = frozenset({'provider_malformed', 'bad_tool_call'})
HOME_COMPANION_GROUP_WORDS = frozenset({
    "switches", "thermostats", "covers", "blinds", "shades", "valves", "speakers", "players", "vacuums",
    "humidifiers", "timers", "heaters",
    "all", "both", "every", "lights", "fans", "alle", "beide", "lichter", "lampen", "ventilatoren",
    "todos", "todas", "ambas", "ambos", "luces", "ventiladores", "tous", "toutes", "lumières", "lampes",
    "ventilateurs", "tutti", "tutte", "luci", "ventilatori", "allemaal", "lichten",
    "wszystkie", "oba", "obie", "światła", "wentylatory", "все", "оба", "обе", "светильники", "вентиляторы",
    "alla", "båda", "samtliga", "lampor", "lamporna", "fläktar", "fläktarna",
    "samtlige", "begge", "lamper", "lamperne", "ventilatorer", "ventilatorerne",
    "lampene", "lysene", "vifter", "viftene",
    "kaikki", "kaikkia", "molemmat", "valot", "valoja", "lamput", "tuulettimet",
})
HOME_COMPANION_GROUP_PHRASES = ("所有", "全部", "すべて", "全て", "모든", "전부")
HOME_COMPANION_CONTEXT_PATTERNS = MappingProxyType({
    'en': r'\b(those|these|them|it|that|this|same|previous)\b',
    'de': r'\b(diese|diesen|jene|jenen|das|es|vorherigen)\b',
    'es': r'\b(esas|esos|estas|estos|aquellas|aquellos|eso|esto|anteriores)\b',
    'fr': r'\b(ces|ceux|celles|ça|cela|précédents)\b',
    'it': r'\b(queste|questi|quelle|quelli|questo|quello|precedenti)\b',
    'nl': r'\b(die|deze|dat|dit|vorige)\b',
    'pl': r'\b(te|tamte|to|poprzednie)\b',
    'sv': r'\b(dessa|detta|denna|dem|den|det|de|samma|föregående|förra)\b',
    'da': r'\b(disse|dette|denne|dem|den|det|de|samme|forrige|tidligere)\b',
    'nb': r'\b(disse|dette|denne|dem|den|det|de|samme|forrige|tidligere)\b',
    'fi': r'\b(nämä|näitä|nuo|noita|ne|niitä|tämä|tätä|tuo|tuota|se|sitä|sama|samat|edellinen|edelliset|aiemmat)\b',
    'ru': r'\b(эти|те|их|это|тот|та|предыдущие)\b',
    'zh': r'那些|这些|它们|那个|这个|之前',
    'ja': r'それら|あれら|それ|あれ|これ|前の',
    'ko': r'그것|이것|저것|이전',
})
HOME_COMPANION_TYPO_MIN_LENGTH = 5
HOME_COMPANION_GENERIC_TARGET_WORDS = HOME_COMPANION_GROUP_WORDS | frozenset({
    "climate", "thermostat", "cover", "blind", "shade", "valve", "media", "player", "speaker", "vacuum",
    "humidifier", "timer", "heater", "input", "boolean", "temperature", "volume", "position", "set",
    "open", "close", "stop", "start", "pause", "play", "mute", "unmute", "cancel",
    "schalter", "thermostate", "thermostat", "rollladen", "rollläden", "ventil", "lautsprecher", "staubsauger",
    "interruptor", "interruptores", "termostato", "termostatos", "persiana", "persianas", "válvula", "altavoz",
    "interrupteur", "interrupteurs", "volet", "volets", "vanne", "aspirateur",
    "interruttore", "interruttori", "tapparella", "tapparelle", "valvola", "altoparlante", "aspirapolvere",
    "schakelaar", "schakelaars", "thermostaat", "jaloezie", "jaloezieën", "klep", "luidspreker", "stofzuiger",
    "przełącznik", "przełączniki", "termostat", "roleta", "rolety", "zawór", "głośnik", "odkurzacz",
    "выключатель", "выключатели", "термостат", "термостаты", "жалюзи", "клапан", "динамик", "пылесос",
    'light', 'lamp', 'lamps', 'fan', 'turn', 'switch', 'on', 'off', 'the', 'in', 'of', 'my',
    'licht', 'lampe', 'ein', 'aus', 'schalte', 'luz', 'enciende', 'apaga', 'las', 'los',
    'lumière', 'allume', 'éteins', 'les', 'la', 'luce', 'accendi', 'spegni', 'le', 'i',
    'zet', 'aan', 'uit', 'włącz', 'wyłącz', 'światło', 'включи', 'выключи', 'свет',
    'lampa', 'lampan', 'tänd', 'släck', 'fläkt', 'brytare', 'termostat', 'persienn', 'ventil', 'högtalare', 'dammsugare',
    'lampe', 'lampen', 'tænd', 'sluk', 'ventilator', 'kontakt', 'persienne', 'højttaler', 'støvsuger',
    'slå', 'skru', 'vifte', 'bryter', 'høyttaler',
    'valo', 'lamppu', 'sytytä', 'sammuta', 'tuuletin', 'kytkin', 'termostaatti', 'verho', 'venttiili', 'kaiutin', 'imuri',
})
HOME_COMPANION_SENSOR_CLASSES = frozenset({
    "temperature", "humidity", "illuminance", "power", "energy", "battery",
})
HOME_COMPANION_REPORT_STATUSES = frozenset({"completed", "partial", "clarification_needed", "pending_approval", "refused"})
HOME_COMPANION_ACCEPTED_TEMPLATES = MappingProxyType({
    "direct": "companion_accepted_direct", "warm": "companion_accepted_warm",
    "calm_guide": "companion_accepted_calm_guide", "lively": "companion_accepted_lively",
    "technical": "companion_accepted_technical",
})
HOME_COMPANION_POWER_TEMPLATES = MappingProxyType({"on": "companion_power_on", "off": "companion_power_off"})
HOME_COMPANION_ACTION_KIND_TEMPLATES = MappingProxyType({'light': 'companion_action_lights', 'fan': 'companion_action_fans', 'other': 'companion_action_devices'})
HOME_COMPANION_EXCEPTION_TEMPLATES = MappingProxyType({'unavailable': 'companion_unavailable_targets', 'unverified': 'companion_unverified_targets'})
HOME_COMPANION_COUNT_TEMPLATES = MappingProxyType({
    "direct": "companion_count_direct", "warm": "companion_count_warm",
    "calm_guide": "companion_count_calm_guide", "lively": "companion_count_lively",
    "technical": "companion_count_technical",
})
HOME_COMPANION_COUNT_KIND_TEMPLATES = MappingProxyType({
    "light": "companion_kind_light", "fan": "companion_kind_fan",
    "sensor": "companion_kind_sensor", "other": "companion_kind_other",
})
HOME_COMPANION_VERIFIED_TEMPLATES = MappingProxyType({
    "concise": "companion_verified_short", "balanced": "companion_verified", "detailed": "companion_verified",
})
HOME_COMPANION_CLIENT_IP = "home_companion"
HOME_COMPANION_MAX_ROUNDS = 4
HOME_COMPANION_MAX_CALLS = 8
HOME_COMPANION_MAX_REQUEST_BYTES = 4096
HOME_COMPANION_MAX_CATALOG_BYTES = 65536
HOME_COMPANION_MAX_REPORT_ENTITIES = 128
HOME_COMPANION_MAX_REPORT_NAMES = 8
HOME_COMPANION_TIMEOUT_SECONDS = 120

# Location registry admission and private approval bindings.
LOCATION_REGISTRY_COLLECTION_LIMIT = 100
LOCATION_REGISTRY_TEXT_LIMIT = 255
LOCATION_REGISTRY_BINDING = "_location_registry_binding"

# Private approval binding for set_dashboard_resource: the hash of the resource
# list as reviewed, so an approval cannot apply to a list that changed since.
DASHBOARD_RESOURCE_BINDING = "_dashboard_resource_binding"
# Resource types offered to agents. Home Assistant also accepts "html", whose
# HTML imports no current browser loads.
DASHBOARD_RESOURCE_TYPES = ("module", "css", "js")
DASHBOARD_RESOURCE_URL_MAX_LENGTH = 2048
# The static roots a resource may load from: www/ and the files HACS serves.
DASHBOARD_RESOURCE_LOCAL_PREFIXES = ("/local/", "/hacsfiles/")
DASHBOARD_RESOURCE_OWNERSHIP_STORAGE_KEY = "phoenix_mcp_dashboard_resources"
LOCATION_REGISTRY_FIELDS = frozenset({
    "name", "icon", "aliases", "floor_id", "level", "labels", "add_labels", "remove_labels",
})

# Bound public configuration-check summaries; raw validation prose stays in HA.
MAX_CONFIG_CHECK_MESSAGES = 50

# Resolved approval history is bounded; unresolved effects retain their evidence.
APPROVAL_HISTORY_LIMIT = 500
APPROVAL_HISTORY_DAYS = 7
APPROVAL_PAYLOAD_RETENTION_SECONDS = 3600

# Optional administrator conversation history, independent of safety journals.
CONVERSATION_LOG_STORAGE_KEY = "phoenix_mcp_conversations"
# A streamed Agent Chat turn whose closing event was written while the turn was
# live has delivered its result; a client leaving afterwards (Home Assistant
# cancels the handler) does not make its effects uncertain. A failed or
# cancelled delivery of this event still does.
TURN_DELIVERED_EVENT = "done"
CONVERSATION_LOG_DEFAULTS = MappingProxyType({
    "conversation_log_max_entries": 100,
    "conversation_log_max_days": 7,
    "conversation_log_max_mb": 25,
})
CONVERSATION_LOG_LIMITS = MappingProxyType({
    "conversation_log_max_entries": (1, 2000),
    "conversation_log_max_days": (1, 365),
    "conversation_log_max_mb": (1, 250),
})
CONVERSATION_LOG_RESERVE_BYTES = 16384
CONVERSATION_LOG_MAX_NODES = 100000
CONVERSATION_LOG_MAX_DEPTH = 25
CONVERSATION_LOG_FLUSH_SECONDS = 30
CONVERSATION_LOG_ENTRY_BYTES = 1_000_000
CONVERSATION_LOG_PAYLOAD_BYTES = 64_000
# Bytes one tool result may take as the Conversation Log stores it: its text as an
# escaped JSON string inside the JSON-RPC envelope, which the rest leaves room for.
# A larger payload is clipped there, so a read that adds rows up to this budget and
# names the rest stays auditable whole.
TOOL_RESULT_MAX_BYTES = CONVERSATION_LOG_PAYLOAD_BYTES - 512
# Home Companion's context is bounded by its call and result limits rather than a
# smaller guess: every permitted call may return a full-size result, plus room for
# the system prompt, operator notes, the request and the model's own tool calls.
# Anything past it (oversized model output) is withheld for the model to narrow.
HOME_COMPANION_PROMPT_BYTES = 32768
HOME_COMPANION_MAX_CONTEXT_BYTES = HOME_COMPANION_MAX_CALLS * TOOL_RESULT_MAX_BYTES + HOME_COMPANION_PROMPT_BYTES
CONVERSATION_LOG_SCAN_BYTES = 8_000_000
# A payload keeps CONVERSATION_LOG_PAYLOAD_BYTES at most, so a string is scanned
# only this far on the event loop.
CONVERSATION_LOG_TEXT_SCAN_CHARS = 128_000
CONVERSATION_LOG_MAX_EVENTS = 256
CONVERSATION_LOG_MAX_OPERATIONS = 256
CONVERSATION_LOG_MAX_APPROVALS = 128
CONVERSATION_LOG_OUTCOMES = frozenset({
    "allowed", "denied", "invalid_request", "not_found", "not_implemented", "rate_limited",
    "pending_approval", "running", "unknown", "approved", "rejected", "expired", "cancelled",
})
CONVERSATION_LOG_STATUS_PRIORITY = (
    "running", "failed", "denied", "invalid_request", "pending_approval", "unknown",
    "interrupted", "cancelled", "partial", "paused",
)
CONVERSATION_LOG_METADATA = frozenset({
    "request_id", "client_ip", "conversation_id", "session_ref", "provider", "model",
    "access_key_id", "access_key_name",
})

# Seconds a refused legacy MCP initialize waits when the access key's own sessions
# are all busy: one frees as soon as its request ends.
MCP_LEGACY_SESSION_BUSY_RETRY_SECONDS = 5

