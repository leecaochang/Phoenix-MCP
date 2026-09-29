"""Constants for the Phoenix MCP integration."""

import datetime
import pathlib
import re

from home_assistant_intents import get_languages as _assist_languages

# Phoenix's OWN operator-facing string catalogs (panel, notification, voice),
# deliberately NOT under translations/. hassfest validates translations/en.json
# against a closed set of Home Assistant categories and errors on any other
# top-level key, which fails the HACS submission; translations/ therefore holds
# only HA's own config and entity sections. Read by helpers._catalog_section and
# served to the panel by admin_view.PhoenixAdminCatalogView.
CATALOGS_DIR = pathlib.Path(__file__).parent / "catalogs"

PHOENIX_VERSION = "1.4.0"
# The lowest Home Assistant this integration is claimed to run on. It is a floor
# derived from the APIs actually imported, not a guess, and two separate lines
# were checked to arrive at it. Setup imports StaticPathConfig,
# async_register_static_paths and remove_extra_js_url, none of which exist before
# 2024.7.0, so anything older raises ImportError before setup completes. The
# dashboard tools then need lovelace.const.LOVELACE_DATA, which lands in 2025.2.0;
# below it every one of them fails cleanly with "lovelace is not loaded", so
# 2024.7 would start but ship a surface with a hole in it. 2025.2.0 is also
# ASSIST_API_MIN_HA, so the claim is one number rather than two.
# Raising this means re-deriving it, not editing it: check the module-level
# `from homeassistant...` imports across the package against the target release.
MIN_HA_VERSION = "2025.2.0"
GITHUB_URL = "https://github.com/leecaochang/phoenix-mcp"
DOMAIN = "phoenix_mcp"
RUNTIME_READY_KEY = "phoenix_mcp_runtime_ready"
STORAGE_KEY = "phoenix_mcp"
STORAGE_VERSION = 4

PROXY_TIMEOUT_SECONDS = 30
AUTHORING_REGISTRATION_TIMEOUT_SECONDS = 5.0
AUTHORING_REGISTRATION_POLL_SECONDS = 0.1
RELATIVE_VOLUME_MAX_CONCURRENCY = 8
RELATIVE_VOLUME_PLAN_VERSION = 1
APPROVAL_REASON_FORMAT_INCOMPATIBLE = "approval_format_incompatible"
MAX_REQUEST_BODY_BYTES = 1_048_576
MAX_BATCH_ITEMS = 50
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
TEMPLATE_FAILURE_MESSAGES = {
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
}

TOKEN_PREFIX = "phx_"
TOKEN_HEX_LENGTH = 64
TOKEN_LENGTH = len(TOKEN_PREFIX) + TOKEN_HEX_LENGTH

TOKEN_NAME_REGEX = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")

DEFAULT_RATE_LIMIT_REQUESTS = 60
DEFAULT_RATE_LIMIT_BURST = 10

# Token settings presets (named snapshots of a token's full configuration,
# switchable from the panel; gated by settings.token_presets_enabled).
MAX_PRESETS_PER_TOKEN = 8
PRESET_NAME_MAX_LENGTH = 40
DEFAULT_PRESET_NAME = "Default preset"

FLUSH_INTERVAL = datetime.timedelta(minutes=5)
EXPIRY_CHECK_INTERVAL = datetime.timedelta(minutes=1)
SENSOR_PUSH_INTERVAL = datetime.timedelta(hours=1)
# Coalescing window for per-request sensor state writes (update_token_counter):
# the first request after a flush schedules one write this many seconds out and
# further requests ride it, so a busy token produces at most one state_changed
# per window instead of one per request.
SENSOR_WRITE_DEBOUNCE_SECONDS = 5

AUDIT_LOG_MAXLEN = 10000
AUDIT_STORAGE_KEY = "phoenix_mcp_audit"
AUDIT_STORAGE_VERSION = 1
# audit_flush_interval is stored and exposed in minutes (not seconds).
# Valid values: 0 (disable periodic flush), 5, 10, 15, 30, 60.

# Configuration version history. Immutable before/after snapshots of
# agent-driven create/edit/delete of automations, scripts, scenes, and helpers,
# with admin-only rollback. Stored separately from tokens so the schema versions
# evolve independently.
VERSION_STORAGE_KEY = "phoenix_mcp_versions"
VERSION_STORAGE_VERSION = 1
CONFIG_RECOVERY_STORAGE_KEY = "phoenix_mcp_config_recovery"
CONFIG_RECOVERY_MAX_RECORDS = 256
CONFIG_RECOVERY_MAX_BYTES = 1_048_576
# Template's config flow type menu. Keep this in sync with the supported HA
# Template platforms; _flow_step_body intersects it with the installed flow's
# actual menu, so a type missing from an older supported HA release is omitted.
TEMPLATE_RECOVERY_TYPES = frozenset({
    "alarm_control_panel", "binary_sensor", "button", "cover", "device_tracker",
    "event", "fan", "image", "light", "lock", "number", "select", "sensor",
    "switch", "update", "vacuum", "weather",
})
# Per-resource FIFO retention: the newest N versions per (resource_type,
# resource_id) are kept; older ones are evicted on write.
MAX_VERSIONS_PER_RESOURCE = 20
# Resource types eligible for version history. VersionStore.record RAISES on
# anything absent here, and _record_version swallows that (history must never
# break a write), so a type missing from this set loses its whole rollback path
# SILENTLY, with only a log line. Adding a _record_version call site means adding
# the type here AND to tests/contract/version_resource_types.json;
# tests/test_frontend_contract.py pins this set against that fixture in both
# directions, because the two drifted exactly this way on 2026-08-03 (`energy`
# reached the fixture and the TS union but not here, and every energy version
# silently failed to record).
VERSIONED_RESOURCE_TYPES = frozenset(
    {"automation", "script", "scene", "helper", "dashboard", "yaml_config", "file", "entity", "device",
     "blueprint", "esphome_yaml", "energy", "config_entry"}
)

# Dashboard card catalog. Which Lovelace cards this instance can actually render,
# harvested by the panel frontend and cached here so the MCP tool surface can
# answer "what cards do I have" before an agent authors one. It MUST be harvested
# in a browser: a card's type strings frequently do not exist as literals in its
# minified bundle (Mushroom builds all 19 of its types by string concatenation at
# runtime), so parsing the JS on disk finds roughly a quarter of a real catalog
# and silently misses the most-used card sets. window.customCards is the registry
# HA's own card picker reads, and only a browser that imported the resources has it.
CARD_CATALOG_STORAGE_KEY = "phoenix_mcp_card_catalog"
CARD_CATALOG_STORAGE_VERSION = 1
# Bounds on an ingest payload. The catalog is admin-supplied and third-party
# shaped, so every dimension is capped before it reaches .storage.
MAX_CARD_CATALOG_ENTRIES = 500
MAX_CARD_STUB_CONFIG_BYTES = 2000
MAX_CARD_CATALOG_FAILURES = 50

# agentCLI: the in-panel LLM chat that runs an agentic loop server-side on
# behalf of a chosen token. Provider API keys / base URLs persist in a dedicated
# Store, separate from tokens, so the recoverable secret never rides the general
# settings serialization (which the settings GET and audit snapshots echo).
AGENTCLI_SECRETS_STORAGE_KEY = "phoenix_mcp_agentcli_secrets"
AGENTCLI_SECRETS_STORAGE_VERSION = 1
AGENTCLI_PROVIDERS = frozenset({
    "claude", "deepseek", "chatgpt", "gemini", "grok", "kimi", "meta",
    "minimax", "mistral", "openrouter", "nvidia", "ollama", "ollama_cloud",
    "zai", "groq", "together", "cerebras", "fireworks", "qwen", "opencode", "mimo", "tencent"})
# Default endpoints/models; the panel may override the model per conversation.
AGENTCLI_CLAUDE_BASE_URL = "https://api.anthropic.com"
AGENTCLI_CLAUDE_DEFAULT_MODEL = "claude-opus-4-8"
AGENTCLI_ANTHROPIC_VERSION = "2023-06-01"
AGENTCLI_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# `deepseek-chat` and `deepseek-reasoner` were RETIRED on 2026-07-24 and now
# answer an HTTP error. They were never separate models, only routing labels for
# the non-thinking and thinking modes of the current generation, which is why the
# replacement is one id plus the `thinking` field rather than two ids. A shipped
# default is a guess that rots: the provider card refuses to store a model the
# provider does not list, so this constant is a starting point and not a promise.
AGENTCLI_DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
# ChatGPT (OpenAI) is OpenAI-compatible; base already includes /v1.
AGENTCLI_OPENAI_BASE_URL = "https://api.openai.com/v1"
# Gemini via Google's OpenAI-compatible endpoint (Authorization: Bearer <key>);
# base already includes the OpenAI path, so chat/models append cleanly.
AGENTCLI_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
AGENTCLI_GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
# Grok (xAI) is OpenAI-compatible; base includes /v1 so chat/models append cleanly.
# No default model: the live /v1/models list populates the dropdown (like ChatGPT).
AGENTCLI_GROK_BASE_URL = "https://api.x.ai/v1"
AGENTCLI_GROK_DEFAULT_MODEL = ""
# MiniMax via its Anthropic-compatible API (Authorization: Bearer <key>); base has
# no /v1, so /v1/messages and /v1/models append cleanly. It speaks the Anthropic
# Messages wire format, so it runs through ClaudeProvider, not OpenAICompatProvider.
AGENTCLI_MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"
AGENTCLI_MINIMAX_DEFAULT_MODEL = "MiniMax-M2"
# MiniMax does not document a models-list endpoint; this curated set backs the
# model dropdown when the live probe finds none.
AGENTCLI_MINIMAX_MODELS = ("MiniMax-M2", "MiniMax-M1")
# Mistral's hosted API is OpenAI-compatible for chat and publishes live model
# capabilities. Base includes /v1 so chat/models append cleanly. There is no
# shipped default because the account-scoped list is authoritative.
AGENTCLI_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
# Kimi (Moonshot) via its OpenAI-compatible API (Authorization: Bearer); base
# includes /v1 so chat/models append cleanly. It also fronts an
# Anthropic-compatible path at /anthropic, but the OpenAI path is the documented
# primary, is the one whose tool-calling is documented, and is the only one with a
# models-list endpoint, so Phoenix MCP runs Kimi through OpenAICompatProvider.
AGENTCLI_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
# No default model, the ChatGPT/Grok convention: Kimi's /v1/models is scoped to
# what the ACCOUNT is entitled to, not the published catalog (K3 is plan-gated,
# and k2.5 + the moonshot-v1 series are withheld from newly registered users), so
# only the live list knows which models a given key can actually call. Naming a
# flagship default here would offer a model that answers 404 on the lower plans.
AGENTCLI_KIMI_DEFAULT_MODEL = ""
# Meta's Model API (dev.meta.ai) is OpenAI-compatible; base includes /v1 so
# chat/models append cleanly. Its predecessor api.llama.com is being retired.
AGENTCLI_META_BASE_URL = "https://api.meta.ai/v1"
AGENTCLI_META_DEFAULT_MODEL = "muse-spark-1.1"
# OpenRouter aggregates many providers behind one OpenAI-compatible key (a common
# region-friendly way to reach Meta's Llama and hundreds of other models). Base
# includes /api/v1; no default model (the live tool-capable list drives the dropdown).
AGENTCLI_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# NVIDIA (build.nvidia.com) hosts many vendors' models behind one OpenAI-compatible
# key (Authorization: Bearer nvapi-...); base includes /v1 so chat/models append
# cleanly. No default model: the live list drives the dropdown. Model ids are
# vendor-namespaced (e.g. "meta/llama-3.3-70b-instruct"), and tool-calling support
# varies by model, which the catalog does not advertise.
AGENTCLI_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
AGENTCLI_NVIDIA_DEFAULT_MODEL = ""
# Local Ollama has no default: the operator supplies a local base URL and picks a
# model from the installed list. Ollama Cloud is the same wire format but hosted
# at a fixed URL and authenticated with an API key (Authorization: Bearer).
AGENTCLI_OLLAMA_CLOUD_BASE_URL = "https://ollama.com"
# Additional OpenAI-compatible providers. Z.ai has two fixed plans selected by
# endpoint id; Qwen uses the workspace-specific URL supplied by the operator.
AGENTCLI_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
AGENTCLI_ZAI_CODING_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
AGENTCLI_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
AGENTCLI_TOGETHER_BASE_URL = "https://api.together.ai/v1"
AGENTCLI_CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
AGENTCLI_FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
# OpenCode Zen publishes an OpenAI-compatible Chat Completions API and a live
# account-scoped model catalog at this base URL.
AGENTCLI_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
AGENTCLI_TENCENT_SINGAPORE_BASE_URL = "https://tokenhub-intl.tencentcloudmaas.com/v1"
AGENTCLI_TENCENT_GUANGZHOU_BASE_URL = "https://tokenhub.tencentcloudmaas.com/v1"
AGENTCLI_TENCENT_US_BASE_URL = "https://tokenhub-us.tencentcloudmaas.com/v1"
AGENTCLI_OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"
AGENTCLI_DEFAULT_MAX_TOKENS = 8192
# Explicit output cap sent to DeepSeek only. DeepSeek's undocumented default
# (historically 4K non-thinking / 32K thinking) truncated a large tool call
# (a whole-dashboard write) mid-JSON; 32K is at or above both historical
# defaults, so it only ever raises the ceiling, and is far under the V4
# documented 384K max output. Other OpenAI-compatible kinds are deliberately
# not sent max_tokens (OpenAI reasoning models reject the field outright).
AGENTCLI_DEEPSEEK_MAX_TOKENS = 32768
# How many Ollama /api/show lookups run at once during a capability refresh.
# Ollama is the only backend whose capabilities cost one request PER MODEL, and a
# large local library would otherwise arrive as one burst against a machine that
# is also running Home Assistant.
AGENTCLI_CAPABILITY_CONCURRENCY = 6

AGENTCLI_DEFAULT_EFFORT = "high"
AGENTCLI_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
# The same levels in a fixed order, which the capability probe walks. A frozenset
# iterates arbitrarily, and a probe that reports its findings in a different order
# each run reads as a changing answer when nothing changed.
AGENTCLI_EFFORT_LEVEL_ORDER = ("low", "medium", "high", "xhigh", "max")

# The value the probe sends when it is asking "is this field read at all". It has
# to be something no provider could ever accept as a real level, and recognisable
# in a provider's own logs as a deliberate probe rather than a malformed client.
AGENTCLI_PROBE_SENTINEL = "phoenix-probe-invalid"

# Hard cap on completion requests one probe run may make. The matrix is bounded by
# construction (one validation check plus five levels plus temperature), so this
# is a backstop against a future knob turning a button into an unbounded spend.
AGENTCLI_PROBE_MAX_CALLS = 10

# How long a refusal LEARNED from a real turn is honoured before Phoenix tries
# the option again. It expires because the fact came from one moment in one
# provider's history: a model that rejects a parameter today may accept it after
# an upgrade, and a permanent "no" learned from a transient refusal is the same
# silently-stale answer this whole area exists to remove, with our name on it.
AGENTCLI_LEARNED_REFUSAL_TTL_DAYS = 30

# Request keys a refusal can be learned for, mapped to the capability it settles.
# Deliberately short: a key is here only when refusing it has ONE unambiguous
# consequence for what the panel should offer.
AGENTCLI_LEARNABLE_OPTIONS = {
    "temperature": "temperature",
    "reasoning_effort": "thinking",
    "thinking": "thinking",
    "think": "thinking",
    "enable_thinking": "thinking",
}
# Per-turn cap on provider<->tool round trips before the loop pauses. In the
# interactive chat this is a "continue?" checkpoint (the operator can grant
# another N rounds), not a hard stop; the headless voice/AI-task loops stop
# here since there is no one to ask. Admin-configurable via the
# agentcli_max_iterations setting; this is the default and the clamp bounds.
AGENTCLI_MAX_ITERATIONS = 20
AGENTCLI_MAX_ITERATIONS_MIN = 3
AGENTCLI_MAX_ITERATIONS_MAX = 100
# Cap on the between-newline SSE buffer from a provider stream. A real frame is a
# few KB; this bounds a faulty/hostile provider that never sends a newline.
AGENTCLI_MAX_SSE_BUFFER_BYTES = 8 * 1024 * 1024
# Cap on the CUMULATIVE bytes of a single provider response (all frames summed),
# so a flood of small newline-delimited frames cannot exhaust memory even though
# each frame stays under the per-frame cap. A real response is far smaller.
AGENTCLI_MAX_STREAM_BYTES = 32 * 1024 * 1024
# Cap on the TOTAL provider-generated output retained across a whole turn (all
# iterations), measured on the assistant messages held in the conversation. The
# per-response cap above bounds one stream; this bounds the sum so 12 iterations
# cannot accumulate a large multiple. A real multi-step turn stays far below it.
AGENTCLI_MAX_TURN_OUTPUT_BYTES = 48 * 1024 * 1024
# Max idle gap between stream chunks before the provider request is aborted. The
# total time is deliberately unbounded (a long agentic turn streams for minutes),
# but a provider that stops sending data must not hang the turn forever.
AGENTCLI_STREAM_READ_TIMEOUT_SECONDS = 180
# Interactive inline-approval wait, tied to the live SSE connection (far longer
# than the per-token confirm_inline_wait cap, which is for unattended agents).
AGENTCLI_APPROVAL_WAIT_SECONDS = 900
# How often Agent Chat relays a held tool's progress line to the panel. Faster
# than the MCP keepalive because this is a local connection with no proxy in
# the way, and the panel is where a human is actually watching.
AGENTCLI_PROGRESS_INTERVAL_SECONDS = 2.0
# Sentinel client_ip so agentCLI-driven tool calls are attributable in the audit
# log without a core change to _dispatch_mcp.
AGENTCLI_CLIENT_IP = "agentcli"
# Transcript scrollback bound (also the conversation-memory bound): lines dropped
# from the window are dropped from what the LLM is sent.
AGENTCLI_SCROLLBACK_DEFAULT = 100
AGENTCLI_SCROLLBACK_MIN = 0
AGENTCLI_SCROLLBACK_MAX = 5000

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
# Display-only cap on the tool-result text streamed to the verbose panel view
# (the model always receives the full result via the message history). Large
# enough that ordinary results are shown whole; only pathological dumps clip.
AGENTCLI_TOOL_RESULT_MAX_CHARS = 20000

# Assist bridge (assist_api.py): a registered llm.API exposes the bound token's
# scoped tool surface to HA's native Assist/voice conversation agents.
# Sentinel client_ip so Assist-driven tool calls are attributable in the audit
# log without a core change to _dispatch_mcp (mirrors AGENTCLI_CLIENT_IP).
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
# Documented HA floor for the AI Task entity: the ai_task integration shipped in HA
# 2025.7. Real gate is runtime feature detection (the import may be absent); this
# only drives the panel's "requires HA X+" message, like ASSIST_API_MIN_HA.
AI_TASK_MIN_HA = "2025.7.0"
# Documented HA floor for the feature: convert_to_voluptuous (used to translate
# Phoenix MCP's JSON-Schema tool params into the llm.Tool voluptuous schema) shipped with
# the mcp client integration in HA 2025.2. The real gate is runtime feature
# detection (the import may be absent); this constant only drives the panel's
# "requires HA X+" message, decoupled from MIN_HA_VERSION like MESA_INJECT_MIN_HA.
ASSIST_API_MIN_HA = "2025.2.0"

# MESA (semantic safety layer) integration. Profiles persist in a separate
# Store from tokens so the two storage versions evolve independently.
# The inheritance levels a profile can be authored AT, most specific first.
# Deployment defaults are deliberately absent: they are a single fallback record
# rather than a keyed level, and every surface treats them separately.
#
# Hand-mirrored into the panel (the scope union, the six label maps, the editor's
# API dispatch, the list table, and the in-context injector), where a missing
# entry is mostly SILENT rather than a type error: the editor's dispatch used to
# fall through to the area endpoint for any scope it did not name. A contract
# fixture pins this list against the frontend in both directions.
MESA_SCOPES: tuple[str, ...] = ("entity", "device", "area", "integration", "domain")

MESA_STORAGE_KEY = "phoenix_mcp_mesa"
MESA_STORAGE_VERSION = 1
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
# Registered in mcp_view._EXECUTOR_REGISTRY but never dispatchable from the
# tool router, so a token cannot invoke the approved-bypass path directly.
MESA_APPROVED_EXECUTOR = "call_service_mesa_approved"

# Minimum HA frontend version for the optional MESA profile injector.
# Separate from MIN_HA_VERSION because the injector depends on frontend DOM shape.
MESA_INJECT_MIN_HA = "2025.5.0"

# MESA profile suggestions (mesa_suggestions.py). Two v1 signals; suggestions
# are computed admin-side and NEVER auto-applied.
MESA_SUGGEST_SIGNAL_BLAST_RADIUS = "blast_radius"
MESA_SUGGEST_SIGNAL_NAKED_RISKY = "naked_risky"
# Curated risky domains for the naked-risky signal: domain -> (suggested
# control_mode, concern text surfaced verbatim in the suggestion reason).
# lock/alarm suggest "prohibited" to MATCH mesa-core's zero-profile baseline,
# so applying a suggestion is never a relaxation under enforced mode; the
# rest suggest "confirm". Irreversibility rationale folded per signal 9e.
# The suggestion reason an operator reads, and which is written verbatim into a
# profile's control_reason when they Apply. English stays the stored form (like
# a diff summary, it is the record); the panel renders the key in its own
# language. Keys are mirrored into translations/en.json by
# scripts/gen_diff_catalog.py, so never hand-edit that section.
MESA_SUGGESTION_TEMPLATES: dict[str, str] = {
    "blast_radius.risky.one": (
        "This {noun} touches {count} risky entity ({shown}) and has no MESA profile of "
        "its own; an agent allowed to trigger it controls those entities indirectly, "
        "bypassing their own profiles."
    ),
    "blast_radius.risky.other": (
        "This {noun} touches {count} risky entities ({shown}) and has no MESA profile of "
        "its own; an agent allowed to trigger it controls those entities indirectly, "
        "bypassing their own profiles."
    ),
    "blast_radius.fanout": (
        "This {noun} references {count} entities and has no MESA profile of its own; "
        "one trigger has a wide blast radius."
    ),
    "naked_risky.domain": (
        "{count} {domain} entities have no MESA profile at any level. This domain "
        "{concern}. A domain-level profile covers them all at once.{baseline_note}"
    ),
    "naked_risky.entity": (
        "No MESA profile covers this entity at any level, and it {concern}.{baseline_note}"
    ),
}

# The domain nouns and the trailing baseline note the templates interpolate.
MESA_SUGGESTION_PHRASES: dict[str, str] = {
    "noun.automation": "automation",
    "noun.script": "script",
    "noun.scene": "scene",
    "shown.more": "{shown} + {extra} more",
    "baseline_note": (
        " Prohibited matches the built-in baseline for this domain, so the authored "
        "profile makes that intent explicit; choose confirm in Review to allow agent "
        "control behind approvals instead."
    ),
}

MESA_SUGGEST_RISKY_DOMAINS: dict[str, tuple[str, str]] = {
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
}

# Each domain's concern clause is interpolated INTO a reason template, so it
# needs its own catalog entry: translating the sentence but not the clause spliced
# into it produces a half-translated line. Derived from the table above so the two
# cannot drift.
MESA_SUGGESTION_PHRASES.update({
    f"concern.{domain}": concern
    for domain, (_mode, concern) in MESA_SUGGEST_RISKY_DOMAINS.items()
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
})

# The placeholder every redacting reader substitutes for a value the token may not
# see. Named here so a WRITE path can refuse to persist it: a config read is lossy
# (an out-of-scope or ghost entity comes back as this string), so a caller that
# echoes a read back would silently store the placeholder as if it were real
# configuration. `tool_common.redaction_sentinel_path` is that guard.
#
# The four PRODUCERS (policy_engine.filter_service_response, helpers.redact_structure,
# helpers.redact_diagnostics, esphome_yaml's value scrub) still spell the literal
# inline; this constant is deliberately NOT a refactor of them, because rewriting
# working redaction code carries more risk than the duplication does. What keeps the
# two honest is tests/test_redaction_sentinel.py, which asserts this value is what
# filter_service_response actually emits, so a producer that ever changes its
# placeholder fails there rather than silently disarming the write guard.
REDACTION_SENTINEL = "<redacted>"

# Domain-aware "lean" view for get_state / get_states. When a caller passes no
# explicit `fields` and does not set `detailed`, the state is narrowed to the base
# fields (entity_id, state) plus LEAN_ALWAYS_ATTRS plus the domain's important
# attributes below, to cut token cost on the common read path. Field selection
# always runs AFTER sensitive-attribute scrubbing, so it can never reveal a
# scrubbed value. Domains with no entry fall back to base + LEAN_ALWAYS_ATTRS only;
# describe_entity remains the full-detail single-entity tool.
#
# The attribute names are Home Assistant's own canonical primary-state attributes
# per domain (each domain's ATTR_* constants / entity.state_attributes), curated to
# what an agent needs to reason about or act on that domain's state, so a weak model
# rarely has to escalate to detailed=. This is an allowlist by design (never a
# denylist): an unlisted attribute or domain is simply omitted, and the fix for a
# missing attribute is to add it here, not to strip junk. Deliberately excluded:
# large/nested blobs (weather forecast, image/URL-class already scrubbed), long
# free text (release_summary, calendar description), the big capability *_list attrs
# (effect_list/source_list/fan_modes; the current *value* like effect/source stays),
# and location coordinates. HA-coupling point: re-verify these names on HA upgrades
# (e.g. counter uses minimum/maximum, not min/max like number).
LEAN_ALWAYS_ATTRS = ("friendly_name",)

DOMAIN_IMPORTANT_ATTRIBUTES: dict[str, tuple[str, ...]] = {
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
}

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

# Services that require cap_physical_control even when pass_through is True.
# These represent irreversible or safety-relevant physical actions.
PHYSICAL_GATE_SERVICES = frozenset({
    "lock/lock",
    "lock/unlock",
    "lock/open",
    "alarm_control_panel/alarm_disarm",
    "alarm_control_panel/alarm_arm_away",
    "alarm_control_panel/alarm_arm_home",
    "alarm_control_panel/alarm_arm_night",
    "alarm_control_panel/alarm_arm_vacation",
    "alarm_control_panel/alarm_trigger",
    "cover/open_cover",
    "cover/close_cover",
    "cover/stop_cover",
    "cover/set_cover_position",
    "cover/set_cover_tilt_position",
    "valve/open_valve",
    "valve/close_valve",
    "valve/stop_valve",
    "valve/set_valve_position",
})

# Core HA services that take NO entity/device/area target: they re-read YAML
# configuration domain-wide. Phoenix MCP's normal call_service path flattens every call
# to an explicit entity_id list (rule 15), which these services' schemas reject
# ("extra keys not allowed @ data['entity_id']"), so they are handled by a
# dedicated no-target branch that calls them with bare service_data. They are the
# "apply" step for a raw set_yaml_config / write_file edit, so they gate on
# cap_yaml_edit (the same capability that authorized the edit authorizes the
# reload). HA-version-sensitive: this is a curated list of stable core reload
# services, deliberately conservative (integration-specific reloads like
# mqtt/rest/command_line are omitted); re-verify and extend on HA upgrades.
# homeassistant/reload_config_entry is excluded on purpose: it takes a target.
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

# Capability modes. A token's per-capability state is one of these three values.
CAP_DENY = "deny"
CAP_ALLOW = "allow"
CAP_CONFIRM = "confirm"
CAP_MODES = frozenset({CAP_DENY, CAP_ALLOW, CAP_CONFIRM})

# Canonical list of all capability names. Every cap_* field on TokenRecord
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
    "cap_service_response",
    "cap_automation_write",
    "cap_script_write",
    "cap_blueprint_write",
    "cap_scene_write",
    "cap_helper_write",
    "cap_physical_control",
    "cap_restart",
    "cap_integration_write",
    "cap_integration_reconfigure",
    "cap_lovelace_write",
    "cap_registry_write",
    "cap_radio_write",
    "cap_energy_write",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
    "cap_esphome_yaml",
    "cap_esphome_flash",
)

# Tiers drive UI grouping and which capabilities offer Confirm.
# Read and Everyday tiers are deny/allow only; the others support Confirm.
CAPABILITY_TIERS: dict[str, str] = {
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
    "cap_service_response": "everyday",
    "cap_automation_write": "config_write",
    "cap_script_write": "config_write",
    "cap_blueprint_write": "config_write",
    "cap_scene_write": "config_write",
    "cap_helper_write": "config_write",
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
    "cap_backup": "irreversible",
    "cap_filesystem": "irreversible",
    "cap_yaml_edit": "irreversible",
    "cap_esphome_yaml": "config_write",
    # Flashing is not config authoring: it actuates hardware, and a bad image can
    # leave a device unreachable until someone reaches it with a cable. It sits
    # with cap_radio_write, its nearest sibling in consequence.
    "cap_esphome_flash": "system",
}

# Capabilities for which Confirm is a meaningful third state.
# UI hides the Confirm option for caps not in this set.
CONFIRM_AVAILABLE_CAPS = frozenset({
    "cap_automation_write",
    "cap_script_write",
    "cap_blueprint_write",
    "cap_scene_write",
    "cap_helper_write",
    "cap_physical_control",
    "cap_restart",
    "cap_integration_write",
    "cap_integration_reconfigure",
    "cap_log_control",
    "cap_lovelace_write",
    "cap_registry_write",
    "cap_radio_write",
    "cap_energy_write",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
    "cap_esphome_yaml",
    "cap_esphome_flash",
})

# Capabilities ALWAYS evaluated regardless of pass_through state.
# All other capabilities are bypassed (treated as "allow") when pass_through is True,
# except that "confirm" is honored even for non-exempt caps under pass_through
# (admin's intent to gate is preserved). See helpers.effective_cap.
PASS_THROUGH_EXEMPT_CAPS = frozenset({
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
    "cap_integration_write",
    "cap_integration_reconfigure",
    "cap_lovelace_write",
    "cap_registry_write",
    "cap_radio_write",
    "cap_energy_write",
    "cap_backup",
    "cap_filesystem",
    "cap_yaml_edit",
    "cap_esphome_yaml",
    "cap_esphome_flash",
})

# Zigbee2MQTT bridge API. Phoenix MCP talks to Z2M over its MQTT request/response
# topics (<base>/bridge/request/... answered on <base>/bridge/response/...)
# and retained state topics (<base>/bridge/info, <base>/bridge/devices).
# The base topic is Z2M's default; a custom base_topic install would need
# this changed. Version-sensitive surface (shapes verified against Z2M 2.x).
Z2M_BASE_TOPIC = "zigbee2mqtt"
Z2M_REQUEST_TIMEOUT_SECONDS = 30.0
Z2M_RETAINED_READ_TIMEOUT_SECONDS = 5.0
# A topology request actively queries each router and Zigbee2MQTT documents a
# normal completion window of ten seconds to two minutes. MCP's SSE keepalives
# keep the client connection alive while this longer, approval-gated operation
# runs; the ordinary 30-second radio timeout would turn healthy large meshes
# into false failures.
ZIGBEE_TOPOLOGY_SCAN_TIMEOUT_SECONDS = 125.0
# Device options are a local config write, not a sleepy-device operation. Give
# the correlated response a short window, then verify the retained config.
Z2M_DEVICE_OPTIONS_RESPONSE_TIMEOUT_SECONDS = 5.0

# ESPHome Device Builder commands. Phoenix MCP speaks the add-on's multiplexed
# WebSocket API at <dashboard url>/ws for validation, for the
# board/component/automation reference data an authoring agent needs, and for
# the firmware job model (compile, install, poll, cancel).
# Version-sensitive surface: the frame shapes and command names are the add-on's,
# with no stability promise, so re-verify them on add-on upgrades.
# The add-on version every command here was live-verified against. Its OWN
# documentation has been wrong five separate times, so this records what was
# checked on the wire rather than what was read. Used ONLY to explain a failure
# (a refused command, a reply we cannot parse); a mismatch is never warned about
# on its own, because a warning whose consequence never arrives teaches operators
# to click past it, exactly as the install diff refuses to flag a patch bump.
# Bump it after re-verifying against a new add-on, not merely after upgrading.
ESPHOME_BUILDER_VERIFIED_VERSION = "2026.7.3"
ESPHOME_BUILDER_REQUEST_TIMEOUT_SECONDS = 10.0
# Validation runs the config through ESPHome itself (no compile), which is
# seconds on a small file but can pull external_components on a large one.
ESPHOME_BUILDER_VALIDATE_TIMEOUT_SECONDS = 60.0
ESPHOME_BUILDER_OUTPUT_MAX_CHARS = 65_536
ESPHOME_BUILDER_PAGE_LIMIT = 20
ESPHOME_BUILDER_PAGE_LIMIT_MAX = 50
ESPHOME_BUILDER_MAX_COMPONENT_IDS = 10

# Firmware jobs. Compile and install ENQUEUE and return a job record, so those
# calls use the normal request timeout above; only the log replay of a finished
# job needs a longer one.
ESPHOME_BUILDER_JOB_LOG_TIMEOUT_SECONDS = 60.0
# A real build log runs 3-10k lines. The add-on already keeps only the last
# 2000, and the full 64KB budget above would still swamp an agent's context, so
# job output gets its own tighter bound.
ESPHOME_BUILDER_JOB_OUTPUT_MAX_CHARS = 16_384
# A poll of a RUNNING build gets a much tighter, tail-only slice. Live-found: the
# full log is near-identical between polls, so an agent checking a real build
# every 30 seconds pulled the same banner into its context a dozen times over.
# Progress is a question about the last few lines; the whole log is only worth
# returning once the build has actually finished.
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
# The add-on caps a decode at 200 lines of 500 chars and times out server-side
# at 60s; the client waits a little longer so its own timeout is never the first
# to fire and mask the add-on's own answer.
ESPHOME_BUILDER_DECODE_TIMEOUT_SECONDS = 90.0
ESPHOME_BUILDER_MAX_BACKTRACE_LINES = 200
# devices/logs runs until it is killed, so a capture window bounds the request.
# The maximum is deliberately under the SSE keepalive story's comfort zone: this
# hold is real dead air on the wire for however long it runs.
ESPHOME_BUILDER_LOG_CAPTURE_SECONDS = 15
ESPHOME_BUILDER_LOG_CAPTURE_MAX_SECONDS = 60

# Pending-approval queue limits.
MAX_PENDING_APPROVALS_PER_TOKEN = 100
APPROVAL_DEFAULT_TTL_SECONDS = 3600
APPROVAL_SWEEP_INTERVAL = datetime.timedelta(minutes=5)

# How many approvals one batch-approve request may carry. A REQUEST-DURATION
# bound, not a review-policy one: admin_view._approve_approval runs each
# executor INLINE inside the admin's HTTP request (the same constraint that makes
# ESPHome builds enqueue a job instead), so a batch's wall time is the sum of its
# tools' and an unbounded one would hold a connection open for minutes and read
# as a hang. Set well above any realistic hand-selected batch; a migration
# touching every automation on a large instance ran to about 20.
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
# ESPHome device work: diagnostic reads plus device-YAML editing gated to
# confirm. No entity control, no HA config authoring. Seeds caps only, so the
# admin still scopes the entity tree to the ESPHome devices.
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

# Domains whose services require cap_physical_control.
# Derived from the domain portion of PHYSICAL_GATE_SERVICES. Used by the native
# Hass* action tools (HassTurnOn/Off, HassSetPosition) to physical-gate targets
# the same way call_service gates the underlying domain services, so a valve is
# gated whether it is actuated via valve/open_valve or via HassTurnOn.
PHYSICAL_GATE_DOMAINS = frozenset({"lock", "alarm_control_panel", "cover", "valve"})

# The keys Home Assistant reads out of a service call's DATA to decide which
# entities the call reaches. Every one of them must be stripped from
# caller-supplied service data before the call is made, because Phoenix MCP
# resolves and flattens targets itself and then passes an explicit entity list:
# a selector left in the data is unioned with that list by HA, reaching entities
# the permission tree, the capability gates and MESA never saw.
#
# Not a stylistic strip. HA's helpers.target.TargetSelection reads exactly these
# five off ServiceCall.data, and its entity-service schemas accept all five, so a
# leftover floor_id or label_id actuates a whole floor for a token scoped to one
# entity.
#
# Deliberately NOT in this set:
#  - "target", which is a target block only at HA's own WS/REST boundary (which
#    merges it into data before the call). Reaching hass.services.async_call it
#    selects nothing, and it is a REAL data field for the notify domain, whose
#    target names a notification recipient.
#  - "config_entry_id", which TargetSelection does not read and which is a
#    legitimate argument to the config-entry services.
# Both are refused by MESA's own enforcement, which is a stricter rule applied to
# what MESA is shown rather than to what Home Assistant is asked to do.
#
# HA-VERSION-SENSITIVE: a const-invariant test pins this against HA's own
# cv.TARGET_SERVICE_FIELDS, so a release that adds a sixth selector fails loudly
# here rather than silently widening what a call can reach.
TARGET_SELECTOR_KEYS = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id"})

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
    "container_device_id",
    "device_ids",
    "doorbells",
    "entities",
    "entity",
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
    "transfer_id",
    "vehicle",
})

# The ESPHome integration's domain. Its services are ALL dynamically registered
# per device (services.yaml is deliberately empty upstream): a device's firmware
# declares actions, and HA registers each as
# esphome.<device_name_with_underscores>_<action_name>. They take no entity
# target, so they need their own no-target dispatch path and are authorized by
# the owning device's write scope rather than a capability.
ESPHOME_DOMAIN = "esphome"

# Actionable service-name hints for core actuator domains whose real service
# verbs differ from the naive guess (e.g. valve.open_valve, not valve.open;
# cover.open_cover, not cover.open). When a call_service call raises
# ServiceNotFound on one of these domains, Phoenix MCP returns an invalid_request naming
# these valid services instead of the opaque generic "Forbidden.", so an agent
# that guessed the wrong verb can self-correct.
#
# LEAK SAFETY: this map is consulted ONLY inside the ServiceNotFound catch, which
# is strictly post-authorization (the token has already cleared the cap gate,
# entity-scope resolution proving WRITE on an entity in this domain, and MESA).
# The values are a HARDCODED list of public HA-core service names, never a live
# hass.services lookup, so the hint reveals nothing about custom/third-party
# services or about domains/entities the token cannot reach; it only names public
# core verbs for a domain the token already actuates. Domains absent from this
# map keep the generic no-oracle "Forbidden." response.
#
# HA-VERSION-SENSITIVE: these are HA-core service names, verified against HA
# 2026.7. Core rarely renames a service, but re-verify on HA upgrades (like the
# other HA-coupling points). Extend the map when a new verb-mismatch domain
# surfaces; only physical/actuator domains where the naive verb differs are
# included (light/switch/siren use the obvious turn_on/turn_off and are omitted).
DOMAIN_SERVICE_HINTS: dict[str, tuple[str, ...]] = {
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
}

# assist_satellite feature bit for ANNOUNCE support.
ANNOUNCE_BIT = 2

# vacuum feature bits (VacuumEntityFeature), mirrored rather than imported so
# nothing here pulls a component module into the import graph; the ANNOUNCE_BIT
# precedent. HA's own vacuum intents set required_features, so an entity that
# cannot perform the operation is never targeted rather than being asked and
# erroring, and the native tools mirror that.
# HA-VERSION-SENSITIVE: a renumbered bit would silently target the wrong vacuums
# while every test still passed, so tests/test_native_intent_parity.py pins these
# against the installed VacuumEntityFeature enum.
VACUUM_START_BIT = 8192
VACUUM_RETURN_HOME_BIT = 16
VACUUM_CLEAN_AREA_BIT = 16384

# Maximum number of log buckets returned by the MCP diagnostics tools.
MAX_LOG_ENTRIES = 100

# Logbook query bounds. Search is post-retrieval and never widens these windows.
MAX_LOGBOOK_RESOURCE_IDS = 100
MAX_LOGBOOK_HOME_DAYS = 7
MAX_LOGBOOK_NARROWED_DAYS = 31

# Levels get_logs and get_phoenix_diagnostics accept as `level`. INFO is absent
# and must stay absent: Home Assistant attaches its system_log handler with
# setLevel(logging.WARNING) (components/system_log/__init__.py), so an INFO
# record never enters the store this reads and there is no config option to
# lower it. Offering INFO was a promise the store cannot keep, and the failure
# was silent in the direction that matters: an agent asking for INFO to chase
# something got warnings and errors back with nothing saying INFO is never
# collected, so a quiet result read as "nothing was logged" rather than "that
# level is not available here". Distinct from helpers._LOG_LEVEL_RANK, which
# ranks the level of a RECORD; this is the narrower set a CALLER may ask for.
LOG_LEVELS = ("WARNING", "ERROR")
LOG_LEVEL_ERROR_MESSAGE = (
    "level must be WARNING or ERROR. Home Assistant's system log holds WARNING "
    "and above only, so INFO and DEBUG entries never enter it and cannot be read "
    "through this tool. They are written to home-assistant.log on the server."
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

# Hard cap for the search_entities free-text query. Ranking tokenizes on
# whitespace and the fuzzy fallback runs difflib against every accessible
# entity, so an unbounded query could stall the event loop; a real query is far
# shorter than this.
# get_relationships: how many source paths one holder of a dangling reference
# reports before the rest become a count. Live-measured: a single deleted script
# was called from 18 sub-buttons on one dashboard, and repeating that dashboard's
# identity 18 times spent most of the response on nothing an operator needs. The
# true total is always reported alongside, per the truncation contract, so a
# clipped list is never mistaken for a complete one.
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
# measured 80KB live and blew the caller's output limit, so the answer to "did
# my twelve writes land" could not be delivered at all. An ERROR's text is the
# part that has to survive, and it is short: an agent told only "rejected",
# with the executor's reason buried, retries the same doomed call. A success
# only needs to say it succeeded. The single-id form stays unbounded and is
# where a full result is read.
MAX_APPROVAL_RESULT_CHARS = 500

MAX_SEARCH_QUERY_LEN = 512
# Bound opt-in membership normalization and disclosure across one search page.
MAX_SEARCH_MEMBERS = 256
MAX_SEARCH_MEMBER_CHECKS = 4096
MAX_SEARCH_MEMBER_ID_LEN = 255

# Hard cap (and default) for the bounded watch_entity tool.
# It blocks the tool call up to this many seconds waiting for a state change.
MAX_SUBSCRIPTION_SECONDS = 30

# A token's inline wait (seconds) on a confirm gate: a confirm-gated call holds
# the response open up to confirm_inline_wait_seconds for an admin decision
# before falling back to the immediate pending_approval reply. 0 means off,
# return immediately, and it is now BOTH the default and the panel's first
# option. The max is kept well within typical MCP client timeouts so the held
# response never outlives one.
#
# THE DEFAULT IS OFF, and that is a deliberate reversal (it was 60). Blocking is
# automatic behaviour decided by a TOKEN SETTING, but the party that knows
# whether the outcome is needed right now is the AGENT: firing twenty writes it
# needs them queued, not resolved one at a time; turning off one lock it does
# want the answer. Holding the request removes that choice, and because the hold
# blocks a whole request while tool calls arrive one at a time, approval N+1
# cannot even be CREATED until approval N stops waiting. Live-measured at the old
# 60s default, consecutive approvals landed 62.8 SECONDS apart, so a twenty-write
# change took twenty minutes just to reach the operator's queue.
#
# The architecture already answers this properly: return pending_approval at once
# and let the agent call wait_for_approval (now accepting several ids) if and when
# it wants the outcomes. Approvals then STAGE immediately and the operator clears
# them however suits, individually or in one batch. The inline wait stays
# available for an operator who prefers an external client to block on a single
# action, but it is opt-in rather than the thing everyone silently pays for.
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
MCP_PROTOCOL_VERSION_PREFERRED = MCP_PROTOCOL_VERSIONS[0]
MCP_LEGACY_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-03-26",)
MCP_LEGACY_PROTOCOL_VERSION_PREFERRED = MCP_LEGACY_PROTOCOL_VERSIONS[0]

# Freshness hint (milliseconds) on the server/discover result. Nothing in it is
# enforcement: identity and the version list are fixed for a build, and the
# instructions are advisory prose that every cap gate re-derives on the call
# itself, so a client holding a stale copy can be wrong about what to SAY and
# never about what it is allowed to DO.
MCP_DISCOVER_TTL_MS = 300_000

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
# Maximum file size (bytes) the filesystem read/write tools will handle inline.
MAX_FILE_BYTES = 1_048_576

# configuration.yaml subtrees set_yaml_config may never add, remove, or modify,
# as {top-level key: protected child keys}. Not operator-configurable, and not a
# general "powerful keys" list: command_line, shell_command and rest stay writable
# because command execution and outbound HTTP are already accepted surface for a
# token holding cap_yaml_edit. These four are different in kind, because each one
# redefines Home Assistant's OWN trust boundary rather than acting within it:
#   homeassistant: auth_providers / auth_mfa_modules change how the instance
#     authenticates anyone, and packages redirects which folder is loaded as
#     configuration, i.e. the very surface this tool is bounded by (a token that
#     can also write www/ could then point packages at content it controls).
#   http: trusted_proxies + use_x_forwarded_for turn a spoofable X-Forwarded-For
#     header into an authentication bypass; cors_allowed_origins, ip_ban_enabled
#     and login_attempts_threshold disarm the brute-force and origin defenses.
#   frontend: extra_module_url and lovelace: resources load arbitrary JavaScript
#     into the authenticated dashboard, which is a stored-XSS foothold with access
#     to the instance and its tokens.
# An admin edits these by hand. Enforced by mcp_view._yaml_protected_check on both
# the pre-gate and the apply-time path; unchanged values always pass.
YAML_PROTECTED_SUBTREES: dict[str, tuple[str, ...]] = {
    "homeassistant": ("auth_providers", "auth_mfa_modules", "packages"),
    "http": (
        "trusted_proxies", "use_x_forwarded_for", "cors_allowed_origins",
        "ip_ban_enabled", "login_attempts_threshold",
    ),
    "frontend": ("extra_module_url",),
    "lovelace": ("resources",),
}


# Approval diff summaries, as format templates keyed by a stable slug.
#
# The summary an admin reads before approving is the confirm gate's whole safety
# property, so it has to be translatable. It is also PERSISTED on the approval
# record (it doubles as the audit trail), which means translating it server-side
# would freeze the language at creation time and make History mixed after a
# language switch. So the record carries all three: the English `summary` exactly
# as before, plus `summary_key` and `summary_params` for the panel to render in
# the operator's own language, falling back to the stored English for any record
# written before this existed.
#
# The template lives HERE and nowhere else. Python formats it to produce the
# stored English, and scripts/gen_diff_catalog.py generates the panel's
# translations/en.json `diff.*` entries FROM this dict, so the two cannot drift.
# tests/test_diff_summary_contract.py pins that generation and checks every
# builder's emitted key and params against it.
#
# Placeholders are `{name}` only: that is what both str.format and the panel's
# interpolation understand, and what HA's own translation loader validates a
# translated string against. A variant suffix (.mesa, .section, .device) is a
# SEPARATE key rather than a runtime concatenation, because a translator has to
# be able to move the clause.
DIFF_SUMMARY_TEMPLATES: dict[str, str] = {
    # Blueprints. Create never reports consumers (nothing uses it yet).
    "blueprint.create": "Create {domain} blueprint '{rel}'",
    "blueprint.edit": "Replace {domain} blueprint '{rel}'",
    "blueprint.delete": "Delete {domain} blueprint '{rel}'",
    "blueprint.edit.consumers": "Replace {domain} blueprint '{rel}' ({count} will be reloaded)",
    "blueprint.delete.consumers": "Delete {domain} blueprint '{rel}' ({count} still use it)",
    # Entity registry.
    "set_entity": "Update registry metadata ({fields}) for {entity_id}",
    "delete_entity": "Delete the registry entry for {entity_id}",
    # Device registry.
    "set_device": "Update device registry metadata ({fields}) for {device_id}",
    "remove_device": "Remove {device_id} from config entry {config_entry_id}",
    # Zigbee. The bare forms are the error paths, where no device resolved.
    "zigbee_permit": "Open the Zigbee network for joining",
    "zigbee_permit.close": "Close the Zigbee join window",
    "zigbee_permit.open": "Open the Zigbee network for joining for {duration} seconds",
    "zigbee_reconfigure": "Reconfigure a Zigbee device",
    "zigbee_reconfigure.device": "Reconfigure (re-interview) Zigbee device {label}",
    "zigbee_remove": "Remove a Zigbee device",
    "zigbee_remove.device": "Remove Zigbee device {label} from the network",
    "zigbee_options": "Change Zigbee2MQTT device options",
    "zigbee_options.device": "Change Zigbee2MQTT options ({keys}) for {label}",
    "zigbee_property": "Change a Zigbee2MQTT exposed property",
    "zigbee_property.device": "Change Zigbee2MQTT property {property} for {label}",
    "zigbee_binding": "Change a Zigbee device binding",
    "zigbee_binding.devices": "{operation} Zigbee binding from {source} to {target}",
    "zigbee_reporting": "Configure Zigbee attribute reporting",
    "zigbee_reporting.device": "Configure {cluster}.{attribute} reporting for {label}",
    "zigbee_group": "Change a Zigbee group",
    "zigbee_group.named": "{operation} Zigbee group {name}",
    "zigbee_topology": "Scan Zigbee topology",
    "zigbee_topology.backend": "Scan {backend} neighbor tables for the scoped Zigbee map",
    # System.
    "restart_ha": "Restart Home Assistant",
    # {file} rather than a literal configuration.yaml: these tools write any
    # include target now, so a summary naming the wrong file is a false line in
    # the History an admin reads. {path} is already the addressed KEY here.
    "set_yaml_config": "Replace {file}",
    "set_yaml_config.removing": "Replace {file}, removing the top-level {keys}",
    "set_yaml_config.removing_entry": "Replace {file}, removing 1 entry",
    "set_yaml_config.removing_entries": "Replace {file}, removing {count} entries",
    "patch_yaml_config.set": "Set {path} in {file}",
    "patch_yaml_config.append": "Append to {path} in {file}",
    "patch_yaml_config.remove": "Remove {path} from {file}",
    "config_entry.options": "Change {keys} on helper '{label}'",
    "write_file": "Write file '{path}'",
    "integration.enable": "Enable integration {label}",
    "integration.disable": "Disable integration {label}",
    "integration.update": "Update integration {label}",
    "integration.reconfigure": "Reconfigure integration {label}",
    "integration.log_level": "Change integration log level for {label}",
    "integration.reload": "Reload integration {label}",
    "integration.remove": "Remove integration {label}",
    "create_backup": "Create backup",
    "create_backup.named": 'Create backup "{name}"',
    # ESPHome.
    "set_esphome_yaml": "Edit ESPHome device configuration {rel}. This does not flash the device.",
    "set_esphome_yaml.device": (
        "Edit ESPHome device configuration {rel} ({device}, {count} entities). "
        "This does not flash the device."
    ),
    "delete_esphome_yaml": "Delete ESPHome device configuration {rel}. The device itself is not touched.",
    "delete_esphome_yaml.device": (
        "Delete ESPHome device configuration {rel} ({device}). The device itself is not touched."
    ),
    "rename_esphome_device": "Rename ESPHome device {device} to {new_name}. This compiles and flashes the device.",
    "rename_esphome_device.services": (
        "Rename ESPHome device {device} to {new_name}, renaming {count} action service(s). "
        "This compiles and flashes the device."
    ),
    "install_esphome_firmware": "Flash ESPHome firmware to {label}",
    "install_esphome_firmware.jump": "Flash ESPHome firmware to {label} (ESPHome version change: {jump})",
    # Dashboards. The card ops spell out the location rather than passing a
    # pre-joined "view N, section M" fragment, so word order stays translatable.
    "set_dashboard_config": "Set dashboard layout '{label}'",
    "dashboard.create": "Create dashboard '{label}'",
    "dashboard.edit": "Edit dashboard '{label}'",
    "dashboard.delete": "Delete dashboard '{label}'",
    "dashboard_card.add": "Add '{card_type}' to dashboard '{label}' (view {view_index})",
    "dashboard_card.add.section": "Add '{card_type}' to dashboard '{label}' (view {view_index}, section {section_index})",
    "dashboard_card.edit": "Replace card {card_index} on dashboard '{label}' (view {view_index})",
    "dashboard_card.edit.section": "Replace card {card_index} on dashboard '{label}' (view {view_index}, section {section_index})",
    "dashboard_card.delete": "Delete card {card_index} from dashboard '{label}' (view {view_index})",
    "dashboard_card.delete.section": "Delete card {card_index} from dashboard '{label}' (view {view_index}, section {section_index})",
    # Energy dashboard. Each names the human-visible thing, never the JSON path:
    # an operator reviewing these knows their appliances, not HA's prefs schema.
    "edit_energy_config.replace_statistic": "Energy: point '{label}' at {new_statistic} (was {old_statistic})",
    "edit_energy_config.add_device": "Energy: track '{label}' ({new_statistic}) as an individual device",
    "edit_energy_config.remove_device": "Energy: stop tracking '{label}' ({old_statistic})",
    "edit_energy_config.rename_device": "Energy: rename '{old_label}' to '{label}'",
    "edit_energy_config.remove_source": "Energy: remove the {source_type} source ({old_statistic})",
    "edit_energy_config.set_source.update": "Energy: update the {source_type} source ({fields})",
    "edit_energy_config.set_source.create": "Energy: add a {source_type} source ({fields})",
    "patch_dashboard.set": "Set {path} on dashboard '{label}'",
    "patch_dashboard.append": "Append to {path} on dashboard '{label}'",
    "patch_dashboard.remove": "Remove {path} from dashboard '{label}'",
    # Service calls and the native physical-gate tools.
    "call_service": "Call {domain}/{service}",
    "call_service.mesa": "Call {domain}/{service} (includes MESA confirmation)",
    "hass_turn.on": "Turn on physical device(s): {names}",
    "hass_turn.off": "Turn off physical device(s): {names}",
    "hass_turn.on.mesa": "Turn on physical device(s): {names} (includes MESA confirmation)",
    "hass_turn.off.mesa": "Turn off physical device(s): {names} (includes MESA confirmation)",
    "hass_set_position": "Set cover/valve position",
    "hass_set_position.mesa": "Set cover/valve position (includes MESA confirmation)",
    "hass_stop_moving": "Stop moving cover",
    "hass_stop_moving.mesa": "Stop moving cover (includes MESA confirmation)",
    "mesa_service": "MESA confirmation for {domain}.{service}",
    # Authoring.
    "create_automation": "Create automation '{alias}'",
    "edit_automation": "Edit automation '{automation_id}'",
    "delete_automation": "Delete automation '{automation_id}'",
    "create_script": "Create script '{script_id}'",
    "edit_script": "Edit script '{script_id}'",
    "delete_script": "Delete script '{script_id}'",
    "create_scene": "Create scene '{name}'",
    "edit_scene": "Edit scene '{scene_id}'",
    "delete_scene": "Delete scene '{scene_id}'",
    "create_helper": "Create {helper_type} helper '{name}'",
    "edit_helper": "Edit {helper_type} helper '{helper_id}'",
    "delete_helper": "Delete {helper_type} helper '{helper_id}'",
}


# Friendly approval-modal copy. Unlike diff summaries, these sentences are
# presentation only: the persisted English diff remains the audit record. Every
# known diff key gets a title and consequence so Summary never has to expose a
# technical path or rendered YAML. The generated catalog is the frontend API.
_APPROVAL_SUMMARY_TITLE_OVERRIDES = {
    "set_yaml_config": "Update {file}",
    "set_yaml_config.removing": "Update {file}",
    "set_yaml_config.removing_entry": "Update {file}",
    "set_yaml_config.removing_entries": "Update {file}",
    "patch_yaml_config.set": "Set configuration in {file}",
    "patch_yaml_config.append": "Append configuration in {file}",
    "patch_yaml_config.remove": "Remove configuration from {file}",
    "set_esphome_yaml": "Edit ESPHome device configuration {rel}",
    "set_esphome_yaml.device": "Edit ESPHome device configuration {rel} ({device}, {count} entities)",
    "delete_esphome_yaml": "Delete ESPHome device configuration {rel}",
    "delete_esphome_yaml.device": "Delete ESPHome device configuration {rel} ({device})",
    "rename_esphome_device": "Rename ESPHome device {device} to {new_name}",
    "rename_esphome_device.services": (
        "Rename ESPHome device {device} to {new_name}, renaming {count} action service(s)"
    ),
    "dashboard_card.add": "Add a card to dashboard '{label}'",
    "dashboard_card.add.section": "Add a card to dashboard '{label}'",
    "dashboard_card.edit": "Update a card on dashboard '{label}'",
    "dashboard_card.edit.section": "Update a card on dashboard '{label}'",
    "dashboard_card.delete": "Remove a card from dashboard '{label}'",
    "dashboard_card.delete.section": "Remove a card from dashboard '{label}'",
    "patch_dashboard.set": "Update dashboard '{label}'",
    "patch_dashboard.append": "Update dashboard '{label}'",
    "patch_dashboard.remove": "Update dashboard '{label}'",
}

_APPROVAL_SUMMARY_BODIES = {
    "restart_ha": "Home Assistant will be briefly unavailable while it restarts.",
    "zigbee_reconfigure": "This will interview the Zigbee device again. It may be briefly unresponsive. Battery devices must be awake.",
    "zigbee_reconfigure.device": "This will interview {label} again. It may be briefly unresponsive. Battery devices must be awake.",
    "zigbee_remove": "This will remove the Zigbee device from the network. The change cannot be undone from this approval.",
    "zigbee_remove.device": "This will remove {label} from the Zigbee network. The change cannot be undone from this approval.",
    "zigbee_options": "This will change Zigbee2MQTT device options. Open Details to review the proposed values.",
    "zigbee_options.device": "This will change Zigbee2MQTT device options for {label}. Open Details to review the proposed values.",
    "zigbee_property": "This will directly change a Zigbee2MQTT device property. Open Details to review the proposed value.",
    "zigbee_property.device": "This will directly change property {property} for {label}. Open Details to review the proposed value.",
    "zigbee_binding": "This will change direct control between two Zigbee devices. Open Details to review both devices and the selected clusters.",
    "zigbee_binding.devices": "This will {operation} direct Zigbee control from {source} to {target}. Open Details to review the exact relationship.",
    "zigbee_reporting": "This will change how frequently a Zigbee device reports an attribute. Open Details to review the intervals.",
    "zigbee_reporting.device": "This will change {cluster}.{attribute} reporting for {label}. Open Details to review the intervals.",
    "zigbee_group": "This will change which devices are controlled together as a Zigbee group. Open Details to review the membership.",
    "zigbee_group.named": "This will {operation} Zigbee group {name}. Open Details to review the exact membership change.",
    "zigbee_topology": "This actively queries Zigbee routers. The mesh may be less responsive while the scan runs.",
    "zigbee_topology.backend": "This actively queries Zigbee routers on {backend}. The mesh may be less responsive while the scan runs.",
    "integration.reload": "This will temporarily unload the {label} integration and set it up again.",
    "integration.reconfigure": (
        "Phoenix will submit agent-provided values to {label}'s Home Assistant reconfigure flow. "
        "The integration has not validated them yet. Browser or OAuth and asynchronous progress "
        "steps are unsupported, and Phoenix cannot automatically roll this change back."
    ),
    "integration.remove": "This will remove the {label} integration and its configuration from Home Assistant.",
    "delete_entity": "This will delete the registry entry for {entity_id}. The entity may be recreated later by its integration.",
    "remove_device": "This will remove {device_id} from its integration. The device may need to be added again.",
    "set_yaml_config": "This will replace the whole contents of {file}. Open Details to review the exact proposed change.",
    "set_yaml_config.removing": "This will replace the whole contents of {file} and remove configuration. Open Details to review the exact proposed change.",
    "set_yaml_config.removing_entry": "This will replace the whole contents of {file} and remove one entry. Open Details to review the exact proposed change.",
    "set_yaml_config.removing_entries": "This will replace the whole contents of {file} and remove {count} entries. Open Details to review the exact proposed change.",
    "patch_yaml_config.set": "Open Details to review the exact proposed change.",
    "patch_yaml_config.append": "Open Details to review the exact proposed change.",
    "patch_yaml_config.remove": "This will remove configuration from {file}. Open Details to review the exact proposed change.",
    "write_file": "This will write to {path}. Existing contents at that path may be replaced.",
    "delete_esphome_yaml": "This will permanently delete {rel}. The ESPHome device itself will not be changed.",
    "delete_esphome_yaml.device": "This will permanently delete {rel}. The ESPHome device {device} itself will not be changed.",
    "rename_esphome_device": "This will rename {device}, compile its configuration, and flash the device. Do not disconnect it during the update.",
    "rename_esphome_device.services": "This will rename {device}, update its action services, compile its configuration, and flash the device. Do not disconnect it during the update.",
    "install_esphome_firmware": "This will compile and flash firmware to {label}. Do not disconnect the device during the update.",
    "install_esphome_firmware.jump": "This will change the ESPHome version, compile, and flash firmware to {label}. Do not disconnect the device during the update.",
    "blueprint.delete": "This will permanently delete the {domain} blueprint {rel}.",
    "blueprint.delete.consumers": "This will permanently delete {rel}, even though {count} automations or scripts still use it.",
    "dashboard.delete": "This will permanently delete the dashboard {label}.",
    "dashboard_card.delete": "This will permanently delete the selected card from {label}.",
    "dashboard_card.delete.section": "This will permanently delete the selected card from {label}.",
    "delete_automation": "This will permanently delete automation {automation_id}.",
    "delete_script": "This will permanently delete script {script_id}.",
    "delete_scene": "This will permanently delete scene {scene_id}.",
    "delete_helper": "This will permanently delete helper {helper_id}.",
}


def _approval_summary_body(key: str) -> str:
    """Return concise consequence copy for one stable diff-summary key."""
    if key in _APPROVAL_SUMMARY_BODIES:
        return _APPROVAL_SUMMARY_BODIES[key]
    if key.startswith("integration."):
        return "This will change the {label} integration in Home Assistant."
    if key.startswith(("dashboard", "patch_dashboard", "edit_energy_config")):
        return "This will update the Home Assistant dashboard configuration. Open Details to review the exact proposed change."
    if key.startswith(("create_", "edit_", "set_", "patch_", "blueprint.")):
        return "This will apply the proposed configuration change. Open Details to review the exact proposed change."
    if key.startswith(("call_service", "mesa_service")):
        return "This will run the proposed Home Assistant service action."
    if key.startswith(("hass_turn", "hass_set_position", "hass_stop_moving")):
        return "This will control the selected physical device or devices."
    if key.startswith("zigbee_permit"):
        return "This will change whether new devices can join the Zigbee network."
    if key.startswith(("delete_", "remove_")):
        return "This will remove the selected item. The change may not be reversible."
    return "This will apply the proposed action. Open Details to review the exact proposed change."


APPROVAL_SUMMARY_TEMPLATES: dict[str, str] = {
    part_key: template
    for key, diff_title in DIFF_SUMMARY_TEMPLATES.items()
    for part_key, template in (
        (f"{key}.title", _APPROVAL_SUMMARY_TITLE_OVERRIDES.get(key, diff_title)),
        (f"{key}.body", _approval_summary_body(key)),
    )
}

APPROVAL_SUMMARY_UI: dict[str, str] = {
    "view.summary": "Summary",
    "view.details": "Details",
    "view.defaultAria": "Default approval view",
    "action.approve": "Approve and execute",
    "action.approving": "Approving...",
    "action.reject": "Reject",
    "action.rejecting": "Rejecting...",
    "action.cancelReject": "Cancel",
    "action.optionalReason": "Optional reason",
    "action.reasonPlaceholder": "Explain why this request was rejected",
    "fallback.file.title": "Update {file}",
    "fallback.integration.title": "Update integration {label}",
    "fallback.item.title": "Update {label}",
    "fallback.service.title": "Run {label}",
    "fallback.unknown.title": "Review proposed action",
    "fallback.body": "Open Details to review the exact proposed change.",
    "named.editAutomation.title": "Edit automation '{label}'",
    "named.removeDevice.title": "Remove {device} from {integration}",
    "named.removeDevice.body": "This will ask the {integration} integration to remove {device}. You may need to add the device again, and this action cannot be undone from Phoenix MCP.",
    "named.removeDevice.unnamedTitle": "Remove device from {integration}",
    "named.removeDevice.unnamedBody": "This will ask the {integration} integration to remove the selected device. You may need to add the device again, and this action cannot be undone from Phoenix MCP.",
    "named.removeDevice.integrationFallback": "its current integration",
    "named.kind.automation": "automation",
    "named.kind.script": "script",
    "named.kind.scene": "scene",
    "named.kind.helper": "helper",
    "named.kind.input_boolean": "input boolean helper",
    "named.kind.input_number": "number helper",
    "named.kind.input_text": "text helper",
    "named.kind.input_select": "dropdown helper",
    "named.kind.input_datetime": "date and time helper",
    "named.kind.input_button": "button helper",
    "named.kind.counter": "counter helper",
    "named.kind.timer": "timer helper",
    "named.kind.schedule": "schedule helper",
    "named.kind.zone": "zone helper",
    "named.kind.tag": "tag helper",
    "named.kind.person": "person helper",
    "named.resource.createTitle": "Create {kind} '{label}'",
    "named.resource.editTitle": "Edit {kind} '{label}'",
    "named.resource.deleteTitle": "Delete {kind} '{label}'",
    "named.resource.deleteUnnamedTitle": "Delete {kind}",
    "named.resource.createBody": "This will add {kind} '{label}' to Home Assistant. Open Details to review its configuration.",
    "named.resource.editBody": "This will update {kind} '{label}'. Open Details to review the exact proposed change.",
    "named.resource.deleteBody": "This will permanently delete {kind} '{label}'. This action cannot be undone.",
    "named.resource.deleteUnnamedBody": "This will permanently delete the selected {kind}. This action cannot be undone.",
    "history.approved.body": "The request completed successfully.",
    "history.rejected.body": "The request was rejected and was not run.",
    "history.rejectedReason.body": "The request was rejected and was not run. Reason: {reason}",
    "history.expired.body": "The request expired and was not run.",
    "history.cancelled.body": "The request was cancelled and was not run.",
    "history.cancelledReason.body": "The request was cancelled and was not run. Reason: {reason}",
    "history.error.generic": "The action could not be completed. Open Details to review the executor error.",
    "history.error.integrationChanged": "The integration changed after approval. Review the current request details and try again.",
    "history.error.integrationDisabled": "The integration is disabled and cannot be reloaded.",
    "history.error.integrationLoggerChanged": "The integration's logging configuration changed after approval. Review the current request details and try again.",
    "history.error.integrationNotFound": "The integration no longer exists.",
    "history.error.integrationReloadFailed": "Home Assistant could not reload the integration.",
    "history.error.integrationReloadUnsupported": "This integration does not currently support reloading.",
    "history.error.integrationStateNotReloadable": "The integration is currently in state {state} and cannot be reloaded.",
    "history.failed.body": "The request was approved, but execution failed. Error: {error}",
    "history.interrupted.body": "Execution was interrupted and the change may have been applied. Verify the current state before requesting it again.",
    "history.resolvedAt": "Resolved {time}",
}


# Version-record summaries, the Changes tab's one-line "what changed" text.
#
# Same contract as DIFF_SUMMARY_TEMPLATES above and generated into the same
# catalog by the same script, but a separate surface: an approval diff describes
# a change being PROPOSED, a version record describes one already APPLIED, and
# only some resource types get one at all (an automation's alias already tells
# the story, so those stay None).
#
# The card-op location is assembled from the three loc* parts joined with the
# panel's list separator, rather than one key per combination of view/section/
# card: it is a coordinate list, not prose, so the order is fixed and only the
# nouns need translating.
VERSION_SUMMARY_TEMPLATES: dict[str, str] = {
    "cards": "{count} cards",
    "cards.was": "{count} cards (was {before})",
    "size": "{size}",
    "size.was": "{size} (was {before})",
    "entity.removed": "registry entry removed",
    "entity.changed": "changed: {fields}",
    "device.removed": "device registry entry removed",
    "device.changed": "changed: {fields}",
    "device.zigbee_options": "changed Zigbee2MQTT options: {keys}",
    "device.zigbee_property": "changed Zigbee2MQTT property: {property}",
    "device.zigbee_binding": "{operation} Zigbee binding to {target}",
    "device.zigbee_reporting": "configured Zigbee reporting: {cluster}.{attribute}",
    "device.zigbee_group": "{operation} Zigbee group: {name}",
    "config_entry.removed": "integration removed",
    "card.added": "added {subject} ({where})",
    "card.edited": "edited {subject} ({where})",
    "card.deleted": "deleted {subject} ({where})",
    "loc.view": "view {index}",
    "loc.section": "section {index}",
    "loc.card": "card {index}",
    "energy.sources": "{count} sources, {devices} devices",
    "config_entry.options": "reconfigured {subject}",
    "config_entry.reconfigure": "reconfigured {subject}",
    "patch.set": "set {subject}",
    "patch.append": "appended to {subject}",
    "patch.remove": "removed {subject}",
}


# Persistent-notification text, the only Phoenix strings HA itself renders
# outside the panel.
#
# Server-rendered and broadcast to every admin, so there is no per-viewer
# language to key off: these resolve against hass.config.language, the
# instance's own setting. Same {name} placeholder rule as the summary templates,
# and generated into the catalog by the same script.
NOTIFICATION_TEMPLATES: dict[str, str] = {
    "approval.title": "Phoenix MCP",
    # The link label is inside the markdown, so it has to be part of the string;
    # the URL is substituted and must not be translated.
    "approval.message": "Token '{token}' requested approval.\n\n[Review in Phoenix MCP]({url})",
    "rate_limit.title": "Phoenix MCP Alert",
    "rate_limit.message": "Phoenix MCP: token '{token}' has hit its rate limit.",
}

# The sentences the voice agent speaks when it declines a turn. Unlike a
# notification (one stored string shown to every admin, so it follows the
# SERVER language), these are spoken back to whoever is talking, so they
# resolve against the CONVERSATION's own language.
#
# `unavailable` deliberately does not say WHY. It covers the kill switch, an
# admin-disabled agent, and shutdown with one sentence, and the kill switch is
# a security control: naming it would announce the gateway's state to anyone
# within earshot of the satellite.
VOICE_TEMPLATES: dict[str, str] = {
    "local_cancelled": "Cancelled.",
    "local_clarification": "Which target do you mean? {choices}. Reply with its number or exact entity ID, or say cancel. This choice expires in two minutes.",
    "local_clarification_expired": "That choice is no longer current. Please repeat the full command.",
    "local_unauthorized": "This token is no longer authorized.",
    "local_state_matched": "Home Assistant reports the requested state.",
    "local_state_unconfirmed": "Command sent. Home Assistant has not yet confirmed the requested state.",
    "local_pending": "This command needs approval in the Phoenix MCP panel.",
    "local_review": "Review command",
    "local_failed": "Phoenix MCP could not complete this command.",
    "local_state": "The state is {state}.",
    "local_state_unknown": "Unknown",
    "local_state_unavailable": "Unavailable",
    'local_summary_count': 'Among entities exposed to Assist that I can access: {matched} of {total} have state {state}; {unknown} have unknown or unavailable state.',
    'local_summary_total': 'Among entities exposed to Assist that I can access: {total} entities; {unknown} have unknown or unavailable state.',
    'local_summary_breakdown': 'Among entities exposed to Assist that I can access: {total} entities. {summary}.',

    "local_read_failed": "Phoenix MCP could not read the state.",
    "local_attribute_unavailable": "Home Assistant is not reporting a value for the requested property right now.",
    "unavailable": "The Phoenix MCP voice agent is currently unavailable.",
    "not_configured": "The Phoenix MCP voice agent is not fully configured.",
    "token_unavailable": "The Phoenix MCP voice agent's token is no longer available.",
    "error": "Sorry, the Phoenix MCP voice agent hit an error.",
    # How a turn that did not finish cleanly is SPOKEN. These were English
    # literals inside the loop until the turn-outcome split, so a Chinese
    # pipeline heard the four declines above translated and then an English
    # sentence the moment anything went wrong. The loop now reports an outcome
    # code and each surface renders it, which is what lets these live here.
    # {reason} is the provider's own error text and stays in whatever language
    # the provider produced: it is third-party output, not ours to translate.
    "could_not_complete": "Sorry, I could not complete that.",
    "provider_error": "Sorry, {reason}.",
    "outcome_unknown": 'Actions may have run. Check Home Assistant and approval results before retrying. Resolve uncertain actions in Settings > System > Repairs.',
    "prior_action_unresolved": "This new request was not executed because an earlier action has an unresolved outcome. Check the earlier action in Home Assistant and resolve it in Settings > System > Repairs before trying again.",
    "output_limit": "Sorry, the model produced too much output, so I stopped.",
    "out_of_steps": "Sorry, I ran out of steps before finishing that.",
    "focus_declined": (
        "This conversation is in Home-focused mode, so I can only help with requests "
        "about your home and Home Assistant."
    ),
    # Appended after a real answer, so it is a follow-on sentence, not a reply.
    "answer_truncated": "I had to stop before finishing.",
    "focus_answer_anyway": (
        "To continue, repeat your complete request and explicitly say to answer anyway."
    ),
}

# Auto routing follows the ingress language; legacy diagnostic modes retain their allowlist.
COMMAND_ROUTING_SETTINGS_VERSION = 1
COMMAND_ROUTING_MODES = frozenset({"off", "auto", "local_shadow", "local", "hybrid"})
CHAT_COMMAND_ROUTING_MODES = frozenset({"off", "auto", "local", "hybrid"})
CHAT_ROUTING_HISTORY_LIMIT = 128
COMMAND_CLARIFICATION_LIMIT = 64
COMMAND_CLARIFICATION_TTL_SECONDS = 120
COMMAND_CLARIFICATION_MAX_CHOICES = 5
COMMAND_ROUTING_PROOF_FIELDS = frozenset({'voice_origin', 'clarification'})
DECISION_REQUEST_BUDGET_MAX = 100000
DECISION_BUDGET_WINDOW_SECONDS = 3600
DECISION_BUDGET_STORAGE_KEY = 'phoenix_mcp_decision_budget'
COMMAND_CLARIFICATION_CANCEL = frozenset({'cancel', 'never mind', 'nevermind', '取消', '取消吧',
    'abbrechen', 'cancelar', 'annuler', 'annulla', 'キャンセル', '취소', 'annuleren', 'anuluj', 'отмена'})
CHAT_ROUTING_HISTORY_TTL_SECONDS = 1800
CHAT_ROUTING_HISTORY_MAX_MESSAGES = 128
CHAT_ROUTING_HISTORY_MAX_BYTES = 65536
# Canonical routing tags map to the HA sentence variant; previews use the same set.
COMMAND_ROUTING_LANGUAGES = {language: language for language in _assist_languages()}
COMMAND_ROUTING_LANGUAGES.update({"zh-Hans": "zh-CN", "zh-Hant": "zh-TW"})
COMMAND_ROUTING_DEFAULT_LANGUAGES = ("en",)
COMMAND_ROUTING_LANGUAGE_ALIASES = {
    "zh-hans": "zh-Hans", "zh-cn": "zh-Hans", "zh-sg": "zh-Hans", "zh-my": "zh-Hans",
    "zh-hant": "zh-Hant", "zh-tw": "zh-Hant", "zh-mo": "zh-Hant", "zh": "zh-Hans",
}
COMMAND_ROUTING_PLAN_KEY = "_phoenix_command_route"
COMMAND_ROUTING_NUMERIC_TARGET_LIMIT = 16
COMMAND_ROUTING_NUMERIC_MATCH_LIMIT = 64
COMMAND_ROUTING_TIMEOUT_DEFAULT_MS = 500
COMMAND_ROUTING_TIMEOUT_MIN_MS = 50
COMMAND_ROUTING_TIMEOUT_MAX_MS = 5000
# A short observation window, not a device completion deadline or retry budget.
COMMAND_VERIFICATION_WAIT_SECONDS = 1.0
COMMAND_VERIFICATION_POLL_SECONDS = 0.1
COMMAND_VERIFICATION_TOLERANCES = {"HassLightSet": 1, "HassSetVolume": 0.005, "HassSetPosition": 0.5}
# Domain, HA slot, proposal field, service, service field, divisor, whole percent, HA matching domains.
COMMAND_ROUTING_NUMERIC_OPERATIONS = {
    "HassLightSet": ("light", "brightness", "brightness_pct", "turn_on", "brightness_pct", 1, False, ("light",)),
    "HassSetVolume": ("media_player", "volume_level", "volume_pct", "volume_set", "volume_level", 100, True, ("media_player",)),
    "HassSetPosition": ("cover", "position", "position_pct", "set_cover_position", "position", 1, True, ("cover", "valve")),
}
# Additional absolute numeric controls. Synthetic intent names are internal only.
COMMAND_ROUTING_NUMERIC_OPERATIONS.update({
    "HassFanSetSpeed": ("fan", "percentage", "percentage", "set_percentage", "percentage", 1, True, ("fan",)),
    "PhoenixValvePosition": ("valve", "position", "position_pct", "set_valve_position", "position", 1, True, ("cover", "valve")),
    "PhoenixCoverTilt": ("cover", "tilt", "tilt_pct", "set_cover_tilt_position", "tilt_position", 1, True, ("cover",)),
    "HassClimateSetTemperature": ("climate", "temperature", "temperature", "set_temperature", "temperature", 1, False, ("climate",)),
    "HassHumidifierSetpoint": ("humidifier", "humidity", "humidity", "set_humidity", "humidity", 1, True, ("humidifier",)),
    "PhoenixClimateHumidity": ("climate", "humidity", "humidity", "set_humidity", "humidity", 1, True, ("climate",)),
    "PhoenixWaterTemperature": ("water_heater", "temperature", "temperature", "set_temperature", "temperature", 1, False, ("water_heater",)),
    "PhoenixCounterValue": ("counter", "value", "value", "set_value", "value", 1, True, ("counter",)),
    "PhoenixNumberValue": ("number", "value", "value", "set_value", "value", 1, False, ("number", "input_number")),
    "PhoenixInputNumberValue": ("input_number", "value", "value", "set_value", "value", 1, False, ("number", "input_number")),
    "PhoenixLightTemperature": ("light", "temperature", "temperature", "turn_on", "color_temp_kelvin", 1, True, ("light",)),
})
# Native bounds and precision must be present; missing metadata never invents a range.
COMMAND_ROUTING_NUMERIC_BOUNDS = {
    "HassClimateSetTemperature": ("min_temp", "max_temp", "target_temp_step"),
    "PhoenixWaterTemperature": ("min_temp", "max_temp", None),
    "HassHumidifierSetpoint": ("min_humidity", "max_humidity", None),
    "PhoenixClimateHumidity": ("min_humidity", "max_humidity", None),
    "PhoenixNumberValue": ("min", "max", "step"),
    "PhoenixInputNumberValue": ("min", "max", "step"),
    "PhoenixLightTemperature": ("min_color_temp_kelvin", "max_color_temp_kelvin", None),
}
COMMAND_ROUTING_NATIVE_VALUES = frozenset({
    "HassClimateSetTemperature", "PhoenixWaterTemperature", "PhoenixNumberValue",
    "PhoenixInputNumberValue", "PhoenixLightTemperature",
})
COMMAND_ROUTING_NUMERIC_STATE = {
    "HassFanSetSpeed": "percentage", "PhoenixValvePosition": "current_position",
    "PhoenixCoverTilt": "current_tilt_position", "HassClimateSetTemperature": "temperature",
    "PhoenixWaterTemperature": "temperature", "HassHumidifierSetpoint": "humidity",
    "PhoenixClimateHumidity": "humidity", "PhoenixNumberValue": None, "PhoenixInputNumberValue": None,
    "PhoenixLightTemperature": "color_temp_kelvin",
}
COMMAND_ROUTING_NATIVE_VALUES |= {"PhoenixCounterValue"}
COMMAND_ROUTING_NUMERIC_STATE["PhoenixCounterValue"] = None
COMMAND_ROUTING_SWITCH_SERVICES = {"HassTurnOn": "turn_on", "HassTurnOff": "turn_off"}
COMMAND_ROUTING_INTENTS = frozenset({*COMMAND_ROUTING_SWITCH_SERVICES, *COMMAND_ROUTING_NUMERIC_OPERATIONS, "HassGetState"})
COMMAND_ROUTING_TARGET_SLOTS = frozenset({"name", "area", "domain", "device_class"})

# Fixed entity actions and bounded option/text values. No provider-written service names.
# Domain, service, value field, options attribute, required feature, observed state.
COMMAND_ROUTING_ENTITY_COMMANDS = {
    "switch_on": ('switch', 'turn_on', '', '', '', 'on'),
    "switch_off": ('switch', 'turn_off', '', '', '', 'off'),
    "input_boolean_on": ('input_boolean', 'turn_on', '', '', '', 'on'),
    "input_boolean_off": ('input_boolean', 'turn_off', '', '', '', 'off'),
    "fan_on": ('fan', 'turn_on', '', '', 'TURN_ON', 'on'),
    "fan_off": ('fan', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "humidifier_on": ('humidifier', 'turn_on', '', '', '', 'on'),
    "humidifier_off": ('humidifier', 'turn_off', '', '', '', 'off'),
    "climate_on": ('climate', 'turn_on', '', '', 'TURN_ON', ''),
    "climate_off": ('climate', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "water_heater_on": ('water_heater', 'turn_on', '', '', 'ON_OFF', ''),
    "water_heater_off": ('water_heater', 'turn_off', '', '', 'ON_OFF', 'off'),
    "media_player_on": ('media_player', 'turn_on', '', '', 'TURN_ON', ''),
    "media_player_off": ('media_player', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "remote_on": ('remote', 'turn_on', '', '', '', 'on'),
    "remote_off": ('remote', 'turn_off', '', '', '', 'off'),
    "siren_on": ('siren', 'turn_on', '', '', 'TURN_ON', 'on'),
    "siren_off": ('siren', 'turn_off', '', '', 'TURN_OFF', 'off'),
    "switch_toggle": ('switch', 'toggle', '', '', '', ''),
    "input_boolean_toggle": ('input_boolean', 'toggle', '', '', '', ''),
    "counter_increment": ('counter', 'increment', '', '', '', ''),
    "counter_decrement": ('counter', 'decrement', '', '', '', ''),
    "counter_reset": ('counter', 'reset', '', '', '', ''),
    "timer_start": ('timer', 'start', '', '', '', ''),
    "timer_pause": ('timer', 'pause', '', '', '', 'paused'),
    "timer_cancel": ('timer', 'cancel', '', '', '', 'idle'),
    "timer_finish": ('timer', 'finish', '', '', '', ''),
    "button_press": ('button', 'press', '', '', '', ''),
    "input_button_press": ('input_button', 'press', '', '', '', ''),
    "cover_open": ('cover', 'open_cover', '', '', 'OPEN', 'open'),
    "cover_close": ('cover', 'close_cover', '', '', 'CLOSE', 'closed'),
    "cover_stop": ('cover', 'stop_cover', '', '', 'STOP', ''),
    "valve_open": ('valve', 'open_valve', '', '', 'OPEN', 'open'),
    "valve_close": ('valve', 'close_valve', '', '', 'CLOSE', 'closed'),
    "valve_stop": ('valve', 'stop_valve', '', '', 'STOP', ''),
    "media_play": ('media_player', 'media_play', '', '', 'PLAY', 'playing'),
    "media_pause": ('media_player', 'media_pause', '', '', 'PAUSE', 'paused'),
    "media_stop": ('media_player', 'media_stop', '', '', 'STOP', 'idle'),
    "media_next_track": ('media_player', 'media_next_track', '', '', 'NEXT_TRACK', ''),
    "media_previous_track": ('media_player', 'media_previous_track', '', '', 'PREVIOUS_TRACK', ''),
    "vacuum_start": ('vacuum', 'start', '', '', 'START', 'cleaning'),
    "vacuum_pause": ('vacuum', 'pause', '', '', 'PAUSE', 'paused'),
    "vacuum_stop": ('vacuum', 'stop', '', '', 'STOP', ''),
    "vacuum_return_to_base": ('vacuum', 'return_to_base', '', '', 'RETURN_HOME', 'returning'),
    "vacuum_locate": ('vacuum', 'locate', '', '', 'LOCATE', ''),
    "lawn_mower_start_mowing": ('lawn_mower', 'start_mowing', '', '', 'START_MOWING', 'mowing'),
    "lawn_mower_dock": ('lawn_mower', 'dock', '', '', 'DOCK', 'returning'),
    "lawn_mower_pause": ('lawn_mower', 'pause', '', '', 'PAUSE', 'paused'),
    "lock_lock": ('lock', 'lock', '', '', '', 'locked'),
    "lock_unlock": ('lock', 'unlock', '', '', '', 'unlocked'),
    "lock_open": ('lock', 'open', '', '', 'OPEN', ''),
    "scene_activate": ('scene', 'turn_on', '', '', '', ''),
    "script_on": ('script', 'turn_on', '', '', '', ''),
    "script_off": ('script', 'turn_off', '', '', '', 'off'),
    "select_option": ('select', 'select_option', 'option', 'options', '', ''),
    "input_select_option": ('input_select', 'select_option', 'option', 'options', '', ''),
    "text_value": ('text', 'set_value', 'value', '', '', ''),
    "input_text_value": ('input_text', 'set_value', 'value', '', '', ''),
    "climate_hvac": ('climate', 'set_hvac_mode', 'hvac_mode', 'hvac_modes', '', ''),
    "climate_preset": ('climate', 'set_preset_mode', 'preset_mode', 'preset_modes', 'PRESET_MODE', ''),
    "climate_fan": ('climate', 'set_fan_mode', 'fan_mode', 'fan_modes', 'FAN_MODE', ''),
    "climate_swing": ('climate', 'set_swing_mode', 'swing_mode', 'swing_modes', 'SWING_MODE', ''),
    "fan_preset": ('fan', 'set_preset_mode', 'preset_mode', 'preset_modes', 'PRESET_MODE', ''),
    "humidifier_mode": ('humidifier', 'set_mode', 'mode', 'available_modes', 'MODES', ''),
    "water_operation": ('water_heater', 'set_operation_mode', 'operation_mode', 'operation_list', 'OPERATION_MODE', ''),
    "media_source": ('media_player', 'select_source', 'source', 'source_list', 'SELECT_SOURCE', ''),
    "media_sound": ('media_player', 'select_sound_mode', 'sound_mode', 'sound_mode_list', 'SELECT_SOUND_MODE', ''),
    "vacuum_speed": ('vacuum', 'set_fan_speed', 'fan_speed', 'fan_speed_list', 'FAN_SPEED', ''),
}
COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "automation_on": ('automation', 'turn_on', '', '', '', 'on'),
    "automation_off": ('automation', 'turn_off', '', '', '', 'off'),
    "media_mute": ('media_player', 'volume_mute', '', '', 'VOLUME_MUTE', ''),
    "media_unmute": ('media_player', 'volume_mute', '', '', 'VOLUME_MUTE', ''),
    "media_shuffle_on": ('media_player', 'shuffle_set', '', '', 'SHUFFLE_SET', ''),
    "media_shuffle_off": ('media_player', 'shuffle_set', '', '', 'SHUFFLE_SET', ''),
    "media_repeat_all": ('media_player', 'repeat_set', '', '', 'REPEAT_SET', ''),
    "media_repeat_one": ('media_player', 'repeat_set', '', '', 'REPEAT_SET', ''),
    "media_repeat_off": ('media_player', 'repeat_set', '', '', 'REPEAT_SET', ''),
    "media_clear_playlist": ('media_player', 'clear_playlist', '', '', 'CLEAR_PLAYLIST', ''),
    "fan_oscillate_on": ('fan', 'oscillate', '', '', 'OSCILLATE', ''),
    "fan_oscillate_off": ('fan', 'oscillate', '', '', 'OSCILLATE', ''),
    "fan_direction_forward": ('fan', 'set_direction', '', '', 'DIRECTION', ''),
    "fan_direction_reverse": ('fan', 'set_direction', '', '', 'DIRECTION', ''),
    "cover_open_tilt": ('cover', 'open_cover_tilt', '', '', 'OPEN_TILT', ''),
    "cover_close_tilt": ('cover', 'close_cover_tilt', '', '', 'CLOSE_TILT', ''),
    "cover_stop_tilt": ('cover', 'stop_cover_tilt', '', '', 'STOP_TILT', ''),
    "climate_swing_horizontal": ('climate', 'set_swing_horizontal_mode', 'swing_horizontal_mode', 'swing_horizontal_modes', 'SWING_HORIZONTAL_MODE', ''),
})
# Native no-payload actions retain the shared permission and approval executor.
COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "input_number_increment": ('input_number', 'increment', '', '', '', ''),
    "input_number_decrement": ('input_number', 'decrement', '', '', '', ''),
    "water_away_on": ('water_heater', 'set_away_mode', '', '', 'AWAY_MODE', ''),
    "water_away_off": ('water_heater', 'set_away_mode', '', '', 'AWAY_MODE', ''),
    "vacuum_clean_spot": ('vacuum', 'clean_spot', '', '', 'CLEAN_SPOT', ''),
    "media_volume_up": ('media_player', 'volume_up', '', '', 'VOLUME_STEP', ''),
    "media_volume_down": ('media_player', 'volume_down', '', '', 'VOLUME_STEP', ''),
    "media_play_pause": ('media_player', 'media_play_pause', '', '', 'PAUSE', ''),
    "media_unjoin": ('media_player', 'unjoin', '', '', 'GROUPING', ''),
    "camera_on": ('camera', 'turn_on', '', '', 'ON_OFF', 'idle'),
    "camera_off": ('camera', 'turn_off', '', '', 'ON_OFF', 'off'),
    "camera_motion_on": ('camera', 'enable_motion_detection', '', '', '', ''),
    "camera_motion_off": ('camera', 'disable_motion_detection', '', '', '', ''),
    "light_effect": ('light', 'turn_on', 'effect', 'effect_list', 'EFFECT', ''),
    "light_color": ('light', 'turn_on', 'color_name', '__color_names__', '', ''),
})
for _domain in ('select', 'input_select'):
    for _position in ('first', 'last', 'next', 'previous'):
        COMMAND_ROUTING_ENTITY_COMMANDS[f"{_domain}_{_position}"] = (_domain, f"select_{_position}", '', '', '', '')
for _domain in ('light', 'fan', 'humidifier', 'climate', 'media_player', 'remote', 'siren', 'automation', 'script'):
    COMMAND_ROUTING_ENTITY_COMMANDS[f"{_domain}_toggle"] = (_domain, 'toggle', '', '', '', '')
COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "cover_toggle": ('cover', 'toggle', '', '', 'OPEN', ''),
    "cover_tilt_toggle": ('cover', 'toggle_cover_tilt', '', '', 'OPEN_TILT', ''),
    "valve_toggle": ('valve', 'toggle', '', '', 'OPEN', ''),
})
COMMAND_ROUTING_ENTITY_COMMANDS.update({
    "fan_increase": ('fan', 'increase_speed', '', '', 'SET_SPEED', ''),
    "fan_decrease": ('fan', 'decrease_speed', '', '', 'SET_SPEED', ''),
    "light_flash_short": ('light', 'turn_on', '', '', 'FLASH', ''),
    "light_flash_long": ('light', 'turn_on', '', '', 'FLASH', ''),
    "alarm_disarm": ('alarm_control_panel', 'alarm_disarm', '', '', '', 'disarmed'),
    "alarm_home": ('alarm_control_panel', 'alarm_arm_home', '', '', 'ARM_HOME', 'armed_home'),
    "alarm_away": ('alarm_control_panel', 'alarm_arm_away', '', '', 'ARM_AWAY', 'armed_away'),
    "alarm_night": ('alarm_control_panel', 'alarm_arm_night', '', '', 'ARM_NIGHT', 'armed_night'),
    "alarm_vacation": ('alarm_control_panel', 'alarm_arm_vacation', '', '', 'ARM_VACATION', 'armed_vacation'),
    "alarm_custom_bypass": ('alarm_control_panel', 'alarm_arm_custom_bypass', '', '', 'ARM_CUSTOM_BYPASS', 'armed_custom_bypass'),
    "alarm_trigger": ('alarm_control_panel', 'alarm_trigger', '', '', 'TRIGGER', 'triggered'),
    "update_skip": ('update', 'skip', '', '', '', ''),
    "update_clear_skipped": ('update', 'clear_skipped', '', '', '', ''),
})
# Read selection reuses known public state projections, never writable service fields.
COMMAND_ROUTING_ENTITY_COMMANDS["attribute_read"] = ('', '', 'attribute', '__read_attributes__', '', '')
COMMAND_ROUTING_READ_INTENTS = frozenset({"HassGetState", "PhoenixEntityAttributeRead", "PhoenixStateSummary"})
COMMAND_ROUTING_READ_PERCENT_SCALES = {
    "brightness": (("light",), 255), "volume_level": (("media_player",), 1),
    "current_position": (("cover", "valve"), 100), "current_tilt_position": (("cover",), 100),
    "percentage": (("fan",), 100), "humidity": (("climate", "humidifier", "weather"), 100),
    "current_humidity": (("climate", "humidifier"), 100),
}
COMMAND_ROUTING_READ_ATTRIBUTES = frozenset({
    *(key for fields in DOMAIN_IMPORTANT_ATTRIBUTES.values() for key in fields),
    "unit_of_measurement", "options", "remaining", "finishes_at", "min", "max", "step", "minimum", "maximum",
    "current_tilt_position", "swing_horizontal_mode", "oscillating", "direction", "away_mode", "effect",
}) - {"friendly_name", "supported_features", "supported_color_modes", "device_class", "state_class", "unit_of_measurement"}
# Exact service data is owned by Phoenix; a model selects only the category.
COMMAND_ROUTING_ENTITY_FIXED_DATA: dict[str, dict[str, bool | str]] = {
    "automation_off": {"stop_actions": True},
    "light_flash_short": {"flash": "short"}, "light_flash_long": {"flash": "long"},
    "water_away_on": {"away_mode": True}, "water_away_off": {"away_mode": False},
    "media_mute": {"is_volume_muted": True}, "media_unmute": {"is_volume_muted": False},
    "media_shuffle_on": {"shuffle": True}, "media_shuffle_off": {"shuffle": False},
    "media_repeat_all": {"repeat": "all"}, "media_repeat_one": {"repeat": "one"}, "media_repeat_off": {"repeat": "off"},
    "fan_oscillate_on": {"oscillating": True}, "fan_oscillate_off": {"oscillating": False},
    "fan_direction_forward": {"direction": "forward"}, "fan_direction_reverse": {"direction": "reverse"},
}
COMMAND_ROUTING_ENTITY_INTENTS = {"PhoenixEntity" + "".join(part.title() for part in category.split("_")): spec
                                  for category, spec in COMMAND_ROUTING_ENTITY_COMMANDS.items()}
DECISION_ENTITY_CATEGORIES = {category: "PhoenixEntity" + "".join(part.title() for part in category.split("_"))
                              for category in COMMAND_ROUTING_ENTITY_COMMANDS}
COMMAND_ROUTING_FIXED_INTENT_DATA = {DECISION_ENTITY_CATEGORIES[category]: values
                                     for category, values in COMMAND_ROUTING_ENTITY_FIXED_DATA.items()}
COMMAND_ROUTING_FIXED_OBSERVATIONS = {intent: values for intent, values in COMMAND_ROUTING_FIXED_INTENT_DATA.items()
                                     if intent not in {DECISION_ENTITY_CATEGORIES[key] for key in ("automation_off", "light_flash_short", "light_flash_long")}}
COMMAND_ROUTING_FIXED_OBSERVATIONS[DECISION_ENTITY_CATEGORIES["water_away_on"]] = {"away_mode": "on"}
COMMAND_ROUTING_FIXED_OBSERVATIONS[DECISION_ENTITY_CATEGORIES["water_away_off"]] = {"away_mode": "off"}
COMMAND_ROUTING_VALUE_INTENTS = frozenset(intent for intent, spec in COMMAND_ROUTING_ENTITY_INTENTS.items() if spec[2])
# Native named actions share projections and approval checks with typed decisions.
COMMAND_ROUTING_NATIVE_ACTIONS = {
    "HassMediaPause": "media_pause", "HassMediaUnpause": "media_play",
    "HassMediaNext": "media_next_track", "HassMediaPrevious": "media_previous_track",
    "HassMediaPlayerMute": "media_mute", "HassMediaPlayerUnmute": "media_unmute",
    "HassVacuumStart": "vacuum_start", "HassVacuumReturnToBase": "vacuum_return_to_base",
}
COMMAND_ROUTING_REQUIRED_STATES = {
    "HassMediaPause": frozenset({"playing"}), "HassMediaNext": frozenset({"playing"}),
    "HassMediaPrevious": frozenset({"playing"}),
}
for _intent, _category in COMMAND_ROUTING_NATIVE_ACTIONS.items():
    COMMAND_ROUTING_ENTITY_INTENTS[_intent] = COMMAND_ROUTING_ENTITY_COMMANDS[_category]
    if _category in COMMAND_ROUTING_ENTITY_FIXED_DATA:
        COMMAND_ROUTING_FIXED_INTENT_DATA[_intent] = COMMAND_ROUTING_ENTITY_FIXED_DATA[_category]
        COMMAND_ROUTING_FIXED_OBSERVATIONS[_intent] = COMMAND_ROUTING_ENTITY_FIXED_DATA[_category]
COMMAND_ROUTING_INTENTS |= COMMAND_ROUTING_ENTITY_INTENTS.keys()
COMMAND_ROUTING_ENTITY_VALUE_LIMIT = 254
DECISION_ENTITY_VALUE_QUESTIONS = ("value", "attribute", "color")
COMMAND_ROUTING_TIMER_OBSERVATION_TOLERANCE_SECONDS = 2
COMMAND_ROUTING_UNKNOWN_ACTION_DOMAINS = frozenset({"button", "input_button", "scene"})
COMMAND_ROUTING_NUMERIC_READ_DOMAINS = frozenset({"sensor", "number", "input_number", "counter"})
COMMAND_ROUTING_TRANSLATION_TIMEOUT_SECONDS = 2

# Typed helper updates are immediate value writes, never scheduling requests.
COMMAND_ROUTING_TEMPORAL_OPERATIONS = {
    "PhoenixTimerDuration": ("timer", "start", "duration", "duration"),
    "PhoenixDateValue": ("date", "set_value", "date", "date"),
    "PhoenixTimeValue": ("time", "set_value", "time", "time"),
    "PhoenixDateTimeValue": ("datetime", "set_value", "datetime", "datetime"),
    "PhoenixInputDate": ("input_datetime", "set_datetime", "date", "date"),
    "PhoenixInputTime": ("input_datetime", "set_datetime", "time", "time"),
    "PhoenixInputDateTime": ("input_datetime", "set_datetime", "datetime", "datetime"),
}
DECISION_TEMPORAL_CATEGORIES = {
    "timer_duration": "PhoenixTimerDuration",
    "date_value": "PhoenixDateValue", "time_value": "PhoenixTimeValue", "datetime_value": "PhoenixDateTimeValue",
    "helper_date": "PhoenixInputDate", "helper_time": "PhoenixInputTime", "helper_datetime": "PhoenixInputDateTime",
}

# Decision-only shadow routing: configurable network time, independent of HA recognition.
DECISION_TYPESAFE_BASE_URL = "https://api.typesafe.ai/v1"
DECISION_TYPESAFE_MODEL = "jev-latest"
DECISION_TIMEOUT_DEFAULT_MS = 5000
DECISION_TIMEOUT_MIN_MS = 250
DECISION_TIMEOUT_MAX_MS = 30000
DECISION_MAX_RESPONSE_BYTES = 65536
DECISION_MAX_ERROR_BYTES = 4096
DECISION_TYPESAFE_HTTP_ERRORS = {"max_tokens_exceeded": "provider_context_limit"}
DECISION_METRIC_SAMPLES = 256
DECISION_METRIC_SCOPES = frozenset({"preview", "voice", "chat", "voice_shadow", "chat_shadow", "tool_chat", "tool_voice", "tool_ai_task"})
DECISION_METRIC_OPERATIONS = frozenset({"classify", "target", "context", "batch", "reference", "tool"})
DECISION_DIAGNOSTIC_QUESTIONS = frozenset({
    "route", "target", "value", "attribute", "color", "percentage", "summary_scope", "summary",
})
DECISION_CONTEXT_MAX_MESSAGES = 32
DECISION_CONTEXT_MAX_BYTES = 16384
DECISION_CONTEXT_MAX_DEPTH = 16
DECISION_CONTEXT_CHECKS = {
    "request": {
        "immediate": "In this conversation, the latest user message is a real request to carry out the fixed candidate now or read its current state. Its meaning does not depend on unresolved references.",
        "contextual": "The latest message is quoted, hypothetical, a demonstration, text to translate or explain, deferred, conditional, context-dependent or otherwise uncertain.",
    },
    "constraints": {
        "clear": "No earlier user restriction still prevents this fixed candidate now. Either no applicable restriction was given, or a later user message explicitly revoked it.",
        "restricted": "An earlier user restriction still applies, a required condition is unresolved, or it is uncertain whether the fixed candidate is permitted by the user's earlier instructions.",
    },
}
DECISION_CONTEXT_REQUIRED_CHOICES = {"request": "immediate", "constraints": "clear"}
# Singular references have separate qualification from self-contained commands.
DECISION_REFERENCE_LANGUAGES = frozenset(COMMAND_ROUTING_LANGUAGES.values())
DECISION_REFERENCE_INTENTS = frozenset({"HassTurnOn", "HassTurnOff", "HassGetState"})
DECISION_REFERENCE_EXECUTION_MODELS = {
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
}
DECISION_REFERENCE_CHECKS = {
    "route": {
        "light_on": "The latest message requests turning the one referenced light on now.",
        "light_off": "The latest message requests turning the one referenced light off now.",
        "state_query": "The latest message requests only the current on/off state of the one referenced light.",
        "provider": "Anything else, including explanation, translation, negation, delay, conditions, numeric or relative adjustments, multiple requests or uncertainty.",
    },
    "reference": {
        "bound": "The latest singular reference refers to the anchored light, which remains the clear conversational subject. This identifies the device, not whether an earlier action succeeded.",
        "unresolved": "The latest message names a target explicitly, has no singular reference, refers elsewhere, has competing referents, or its reference is uncertain.",
    },
    "constraints": {
        "clear": "No earlier user restriction prevents the operation requested in the latest message. A read-only restriction permits a state query, even when earlier commands requested changes.",
        "restricted": "An earlier user restriction prevents the latest requested operation, an applicable condition remains unresolved, or permission under earlier user instructions is uncertain.",
    },
}
# Initial evaluated revisions only; every adapter remains eligible for inert previews.
DECISION_CONTEXT_EXECUTION_MODELS = {
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
}
DECISION_CONTEXT_EXECUTION_LANGUAGES = frozenset(COMMAND_ROUTING_LANGUAGES.values())
# Combining decision-derived candidates with context review is qualified separately.
DECISION_FOLLOWUP_EXECUTION_MODELS = {
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
}
# Joint routing has its own qualification, independent of context and numeric decisions.
DECISION_BATCH_EXECUTION_MODELS = {
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
}
DECISION_BATCH_EXECUTION_LANGUAGES = frozenset(COMMAND_ROUTING_LANGUAGES.values())
# Numeric execution is qualified independently from categorical routing.
DECISION_PERCENTAGE_EXECUTION_MODELS = {
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-latest"): frozenset({"jev-1.13.0"}),
    ("typesafe", DECISION_TYPESAFE_BASE_URL, "jev-1.13.0"): frozenset({"jev-1.13.0"}),
}
DECISION_ROUTE_CHOICES = {
    "light_on": "One immediate, explicit request to turn on one light, without a brightness setting or adjustment.",
    "light_off": "One immediate, explicit request to turn off one light, without a brightness setting or adjustment.",
    "light_brightness": "One immediate, explicit request to set one light to an absolute whole brightness percentage from 0 through 100, inclusive. Not a relative adjustment.",
    "state_query": "A question about the current primary state of one named entity.",
    "provider": "Anything else, including ambiguity, negation, quoted commands, deferred or conditional actions, multiple actions, comparisons, counts and general conversation. Use the regular agent for history/statistics, forecast/event/list contents, and structured payloads such as calendar/todo authoring, media playback/grouping, remote commands, camera files, temperature ranges, or settings not represented by an exact eligible operation.",
}
DECISION_PERCENTAGE_PREVIEW_CHOICES = {
    "media_volume": "One immediate, explicit request to set one media player's volume to an absolute whole percentage from 0 through 100. Not a relative adjustment.",
    "cover_position": "One immediate, explicit request to set one cover to an absolute whole percentage open from 0 through 100. Zero is fully closed and 100 fully open. Not a relative adjustment.",
}
# TypeSafe Choice supports 255 options; exact percentages need 101 plus a refusal.
DECISION_EXACT_PERCENTAGE_ENDPOINTS = frozenset({("typesafe", DECISION_TYPESAFE_BASE_URL, "systemone")})
# TypeSafe's direct API rounds probabilities to two decimal places.
DECISION_PROBABILITY_ROUNDING = {("typesafe", DECISION_TYPESAFE_BASE_URL, "systemone"): 0.005}
DECISION_PROBABILITY_SUM_TOLERANCE = 0.001
DECISION_PROBABILITY_ROUNDING_EPSILON = 1e-9
DECISION_PERCENTAGE_CHOICES = {
    **{str(value): f"The explicitly requested absolute setting is exactly {value} percent." for value in range(101)},
    "none": "No single explicit absolute whole percentage from 0 through 100 is requested for this operation.",
}

DECISION_PROVIDERS = frozenset({"typesafe", "systemone", "classifier"})
DECISION_TARGET_INTENTS = {
    "light_on": "HassTurnOn", "light_off": "HassTurnOff",
    "light_brightness": "HassLightSet", "state_query": "HassGetState",
    "media_volume": "HassSetVolume", "cover_position": "HassSetPosition",
}
DECISION_ADDITIONAL_NUMERIC = {
    "fan_speed": ("HassFanSetSpeed", "fan speed percentage"),
    "valve_position": ("PhoenixValvePosition", "valve percentage open, never percentage closed"),
    "cover_tilt": ("PhoenixCoverTilt", "cover tilt percentage, distinct from cover position"),
    "climate_temperature": ("HassClimateSetTemperature", "thermostat target temperature in its native unit"),
    "humidifier_humidity": ("HassHumidifierSetpoint", "humidifier target humidity percentage"),
    "climate_humidity": ("PhoenixClimateHumidity", "climate target humidity percentage"),
    "water_temperature": ("PhoenixWaterTemperature", "water heater target temperature in its native unit"),
    "counter_value": ("PhoenixCounterValue", "counter value as an absolute integer"),
    "number_value": ("PhoenixNumberValue", "numeric entity value in its native unit"),
    "input_number_value": ("PhoenixInputNumberValue", "numeric helper value in its native unit"),
    "light_temperature": ("PhoenixLightTemperature", "light colour temperature in kelvin, not room temperature"),
}
DECISION_TARGET_INTENTS.update(DECISION_ENTITY_CATEGORIES)
DECISION_ENTITY_CHOICES = {category: f"One immediate request for {spec[1]} on one named {spec[0]} entity"
                             + (f" using its {spec[2]} value." if spec[2] else ". No additional setting or duration.")
                             for category, spec in COMMAND_ROUTING_ENTITY_COMMANDS.items()}
for _category, _values in COMMAND_ROUTING_ENTITY_FIXED_DATA.items():
    DECISION_ENTITY_CHOICES[_category] += f" Use exactly these fixed service fields: {_values}."
DECISION_ENTITY_CHOICES["attribute_read"] = "Read one current attribute of one named entity. Select its attribute name from the available options. Brightness, volume, position, speed percentage and humidity are reported as percentages; temperatures use Home Assistant units. No other unit conversions. Not a primary-state query, historical query, comparison or aggregate."
DECISION_ENTITY_CHOICES["automation_off"] += " Disable future triggers and stop running automation actions, matching Home Assistant turn_off."
DECISION_TARGET_INTENTS.update(DECISION_TEMPORAL_CATEGORIES)
DECISION_TARGET_INTENTS.update({category: spec[0] for category, spec in DECISION_ADDITIONAL_NUMERIC.items()})
DECISION_PERCENTAGE_PREVIEW_CHOICES.update({
    category: f"One immediate explicit request to set one {spec[1]} to an absolute numeric value."
    for category, spec in DECISION_ADDITIONAL_NUMERIC.items()
})
# Leave one of the common classifier format's 50 choices for declining.
DECISION_MAX_TARGETS = 49
# Generic sequential selection keeps every question within the common 50-choice format.
DECISION_SEQUENTIAL_CHOICE_GROUPS = 8
DECISION_SEQUENTIAL_MAX_TARGETS = DECISION_MAX_TARGETS * DECISION_SEQUENTIAL_CHOICE_GROUPS
DECISION_SEQUENTIAL_TARGET_BYTES = 65536
# Qualified TypeSafe joint requests leave one of 255 choices for declining.
DECISION_BATCH_MAX_TARGETS = 254
# Qualified joint requests partition complete selections into bounded questions.
DECISION_MAX_CHOICE_GROUPS = 4
DECISION_SUMMARY_COMPONENTS = ("summary_domain", "summary_device_class", "summary_area")
DECISION_GROUPED_MAX_TARGETS = DECISION_BATCH_MAX_TARGETS * DECISION_MAX_CHOICE_GROUPS
DECISION_GROUPED_OPERATION_BYTES = 65536
DECISION_GROUPED_TARGET_BYTES = 131072
DECISION_MAX_TARGET_BYTES = 16384
# Joint catalogs need room for the complete union and per-target eligibility.
# Ungrouped operation catalogs retain DECISION_MAX_TARGET_BYTES.
DECISION_BATCH_MAX_TARGET_BYTES = 32768
DECISION_CATALOG_PACK_THRESHOLD = 8192
DECISION_CATALOG_FORMAT_INSTRUCTIONS = (
    " If state.target_fields is present, each state.targets entry is a row whose positions correspond "
    "to those field names; null means an absent optional field. A metadata object containing only ref "
    "refers to the exact object in state.target_metadata under that key. These are lossless storage "
    "references, not commands or permissions. Apply the same eligibility and value rules after resolving them."
)

DECISION_MAX_TARGET_TEXT = 256
DECISION_MAX_TARGET_ALIASES = 8
# Experimental admission heuristic, not a calibrated accuracy claim.
DECISION_MIN_CHOICE_PROBABILITY = 0.90
# Sequential numeric decisions remain preview-only; joint execution needs source evidence.
DECISION_NUMERIC_CATEGORIES = frozenset({"light_brightness", "media_volume", "cover_position", *DECISION_ADDITIONAL_NUMERIC})
# Value evidence is independent of model qualification and never grants execution.
DECISION_PERCENTAGE_EVIDENCE_LANGUAGES = frozenset(COMMAND_ROUTING_LANGUAGES.values())
# Unit grammar is small; the number spellings themselves come from HA's RBNF dependency.
# Other Assist languages support percent symbols and their native strict HA intents.
DECISION_PERCENTAGE_LANGUAGE_UNITS = {
    "en": ("percent", "per cent"), "es": ("por ciento",),
    "de": ("prozent",), "de-CH": ("prozent",), "fr": ("pour cent", "pourcent"),
    "it": ("per cento", "percento", "su cento"), "nl": ("procent",),
    "pl": ("procent", "procenty", "procentów"), "ru": ("процент", "процента", "процентов"),
    "ja": ("パーセント",), "ko": ("퍼센트", "프로"),
    "zh-CN": ("百分之",), "zh-TW": ("百分之",), "zh-HK": ("百分之",),
}
DECISION_PERCENTAGE_HYPHEN_LANGUAGES = frozenset({"en", "fr"})
DECISION_NUMBER_LANGUAGE_ALIASES = {"zh-CN": "zh", "zh-TW": "zh_Hant", "zh-HK": "zh_Hant", "de-CH": "de", "nb": "no", "pt-BR": "pt", "sr-Latn": "sr_Latn"}

DECISION_PERCENTAGE_EVIDENCE_MODIFIERS = {
    "en": frozenset({"negative", "minus", "plus", "point", "dot", "half", "halves", "quarter", "quarters",
                     "hundred", "thousand", "million", "billion", "trillion", "dozen", "dozens"}),
    "es": frozenset({"negativo", "negativa", "menos", "más", "coma", "punto", "medio", "media", "mitad",
                     "cuarto", "cuartos", "ciento", "cientos", "mil", "millón", "millones"}),
    "zh-CN": frozenset("负負正点點半几幾多余餘成折分之万萬亿億兆"),
}
DECISION_PERCENTAGE_EVIDENCE_MODIFIERS.update({
    "de": frozenset({"minus", "plus", "komma", "halb", "halbe", "hälfte", "viertel", "hundert", "tausend", "million", "millionen"}),
    "fr": frozenset({"moins", "plus", "virgule", "demi", "demie", "moitié", "quart"}),
    "it": frozenset({"meno", "più", "virgola", "mezzo", "mezza", "metà", "quarto"}),
    "nl": frozenset({"min", "minus", "plus", "komma", "half", "halve", "kwart"}),
    "pl": frozenset({"minus", "plus", "przecinek", "pół", "połowa", "ćwierć"}),
    "ru": frozenset({"минус", "плюс", "запятая", "половина", "половиной", "четверть"}),
    "ja": frozenset({"マイナス", "プラス", "点", "半", "約"}),
    "ko": frozenset({"마이너스", "플러스", "점", "반"}),
})
for _language, _source in (("zh-TW", "zh-CN"), ("zh-HK", "zh-CN"), ("de-CH", "de")):
    DECISION_PERCENTAGE_EVIDENCE_MODIFIERS[_language] = DECISION_PERCENTAGE_EVIDENCE_MODIFIERS[_source]
DECISION_PERCENTAGE_EVIDENCE_CONNECTORS = {"en": frozenset({"and"}), "es": frozenset({"y"}), "zh-CN": frozenset()}
DECISION_PERCENTAGE_UNITS = (("%",), ("％",), ("percent",), ("per", "cent"), ("por", "ciento"), ("百", "分", "之"))
# Adding a preview category must not automatically authorize an execution path.
DECISION_EXECUTION_CATEGORIES = frozenset({"light_on", "light_off", "state_query", *DECISION_ENTITY_CATEGORIES})
# Bound independent identity checks without assuming whitespace in every language.
DECISION_TARGET_MENTION_LIMIT = 256
DECISION_UNSEGMENTED_TARGET_LANGUAGES = frozenset({'zh', 'ja', 'ko', 'th', 'lo', 'km', 'my'})
DECISION_HYBRID_REASONS = frozenset({
    "plan_ready", "provider_fallback", "category_low_probability", "target_low_probability",
    "target_declined", "target_unverified", "ambiguous_target", "brightness_declined", "no_target", "targets_changed",
    "unsupported_category", "too_many_targets", "target_metadata_too_large", "unsupported_language",
    "invalid_input", "authority_changed", "not_enabled", "not_configured", "local_match", "numeric_unverified",
    "local_unavailable", "busy", "cancelled", "authentication", "rate_limited", "http_error",
    "response_too_large", "provider_context_limit", "malformed", "timeout", "unavailable",
    "request_budget_exhausted", "budget_unavailable",
    "context_unverified", "context_unavailable", "context_changed", "low_probability", "invalid_context",
    "model_unverified", "category_mismatch",
})
DECISION_BRIGHTNESS_CHOICES = {
    # Enumerate whole values so classification does not require digit arithmetic.
    "brightness_tens": {**{str(value): "The requested brightness is exactly one of: " + ", ".join(
        f"{percent}%" for percent in range(value * 10, min(value * 10 + 10, 101))) + "."
        for value in range(11)}, "none": "No single explicit whole brightness percentage from 0% to 100%."},
    "brightness_units": {**{str(value): "The requested brightness is exactly one of: " + ", ".join(
        f"{percent}%" for percent in range(value, 101, 10)) + "."
        for value in range(10)}, "none": "No single explicit whole brightness percentage from 0% to 100%."},
}

# Summary filters are native primary states, never inferred attributes or thresholds.
COMMAND_SUMMARY_STATES = {
    **{domain: ('on', 'off') for domain in ('light', 'switch', 'input_boolean', 'binary_sensor', 'fan', 'humidifier', 'automation')},
    **{domain: ('open', 'closed', 'opening', 'closing') for domain in ('cover', 'valve')},
    'lock': ('locked', 'unlocked', 'locking', 'unlocking', 'jammed'),
    'media_player': ('on', 'off', 'playing', 'paused', 'idle', 'standby'),
    'timer': ('active', 'paused', 'idle'),
    'person': ('home', 'not_home'),
    'device_tracker': ('home', 'not_home'),
    'vacuum': ('cleaning', 'docked', 'idle', 'paused', 'returning', 'error'),
}
COMMAND_SUMMARY_UNKNOWN_STATES = ('unknown', 'unavailable')
COMMAND_SUMMARY_MODES = ('total', 'breakdown', *COMMAND_SUMMARY_UNKNOWN_STATES, *sorted({state for states in COMMAND_SUMMARY_STATES.values() for state in states}))
COMMAND_SUMMARY_MAX_STATES = 20
COMMAND_ROUTING_INTENTS |= {'PhoenixStateSummary'}
DECISION_TARGET_INTENTS['state_summary'] = 'PhoenixStateSummary'

# Aggregate routing telemetry has fixed dimensions and contains no transcripts.
ROUTING_STATISTICS_STORAGE_KEY = "phoenix_mcp_routing_statistics"
ROUTING_STATISTICS_SURFACES = ("voice", "chat")
ROUTING_STATISTICS_OUTCOMES = ("local", "decision", "provider", "pending", "failed", "cancelled")
ROUTING_STATISTICS_FLUSH_INTERVAL = datetime.timedelta(seconds=30)

# Targeted public-input diagnostics; unrelated extra arguments stay tolerated.
UNKNOWN_ARGUMENT_PROTECTED_FIELDS = {
    "get_dashboard_config": ("url_path",),
    "set_dashboard_config": ("url_path", "expected_hash"),
    "add_dashboard_card": ("url_path", "view_index", "section_index", "position", "expected_hash"),
    "edit_dashboard_card": ("url_path", "view_index", "section_index", "card_index", "expected_hash"),
    "delete_dashboard_card": ("url_path", "view_index", "section_index", "card_index", "expected_hash"),
    "patch_dashboard": ("target", "expected_hash"),
    "edit_dashboard": ("dashboard_id",),
    "delete_dashboard": ("dashboard_id",),
    "list_files": ("path",),
    "read_file": ("path",),
    "write_file": ("path", "expected_hash"),
    "get_yaml_config": ("file", "key"),
    "set_yaml_config": ("file", "expected_hash", "remove_keys", "remove_entries"),
    "patch_yaml_config": ("file", "address", "expected_hash"),
    "set_entity": ("entity_id",),
    "delete_entity": ("entity_id",),
    "set_device": ("device_id",),
    "remove_device": ("device_id", "config_entry_id"),
    "set_integration": ("entry_id",),
    "call_service": ("service", "targets"),
    "dry_run_service": ("service", "targets"),
}
UNKNOWN_ARGUMENT_ALIASES = {
    "url_path": ("dashboard_url", "dashboard_url_path", "dashboard_path", "dashboard", "dashboard_id", "url"),
    "dashboard_id": ("dashboard", "dashboard_url", "url_path"),
    "file": ("filename", "file_name", "file_path", "path"),
    "path": ("filename", "file_name", "file_path", "directory"),
    "targets": ("target", "entity_ids", "device_ids", "area_ids", "entities"),
    "entity_id": ("entity", "entity_ids"),
    "device_id": ("device", "device_ids"),
    "entry_id": ("integration_id", "config_entry_id"),
}
UNKNOWN_ARGUMENT_MAX_NAME = 80
UNKNOWN_ARGUMENT_MAX_ERRORS = 8

# Tool advice has separate qualification from direct-command execution.
TOOL_ROUTING_MODES = frozenset({"off", "observe", "advise"})
TOOL_ROUTING_SETTINGS = {
    "chat": "agentcli_tool_routing_mode", "voice": "voice_agent_tool_routing_mode",
    "ai_task": "ai_task_tool_routing_mode",
}
TOOL_ROUTING_MAX_REVIEWS = 2
TOOL_ROUTING_MAX_REPLANS = 1
TOOL_ROUTING_MAX_BYTES = 262144
TOOL_ROUTING_MAX_GROUPS = 8
# (provider, endpoint, configured model, language): admitted returned models.
# Populate only from independent tool-selection and paired task qualification.
TOOL_ROUTING_QUALIFIED_MODELS: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
TOOL_ROUTING_REASONS = frozenset({
    "agreement", "alternative", "replan", "declined", "low_probability", "unqualified",
    "off", "limit", "last_round", "no_account", "unsupported_language", "context_unavailable",
    "catalog_unavailable", "catalog_changed", "context_changed", "authority_changed", "cancelled",
    "busy", "timeout", "unavailable", "malformed", "request_budget_exhausted", "budget_unavailable",
    "provider_context_limit", "authentication", "rate_limited", "http_error", "response_too_large",
    "model_unverified", "too_many_targets", "category_mismatch",
})

# The experimental delegated persona has a fixed, bounded internal catalog.
HOME_COMPANION_TOOLS = frozenset({
    "get_capability_summary", "search_entities", "describe_entity", "describe_area",
    "get_state", "get_states", "get_history", "get_statistics", "compare_states",
    "compare_entities", "intent__HassTurnOn", "intent__HassTurnOff",
    "light__HassLightSet", "fan__HassFanSetSpeed",
})
HOME_COMPANION_CONTROL_TOOLS = frozenset({"intent__HassTurnOn", "intent__HassTurnOff", "light__HassLightSet", "fan__HassFanSetSpeed"})
HOME_COMPANION_SENSOR_CLASSES = frozenset({
    "temperature", "humidity", "illuminance", "power", "energy", "battery",
})
HOME_COMPANION_MAX_ROUNDS = 4
HOME_COMPANION_MAX_CALLS = 8
HOME_COMPANION_MAX_REQUEST_BYTES = 4096
HOME_COMPANION_MAX_CATALOG_BYTES = 24576
HOME_COMPANION_MAX_CONTEXT_BYTES = 16384
HOME_COMPANION_TIMEOUT_SECONDS = 120
