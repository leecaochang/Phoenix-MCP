"""Model provider endpoints, account storage and Agent Chat budgets."""

from collections.abc import Mapping
import datetime
from types import MappingProxyType

from .const import MAX_REQUEST_BODY_BYTES

# How often the provider model catalogues are CONSIDERED for a re-read. Whether an
# account is actually re-read depends on the operator's settings below, so a
# change of period takes effect without re-registering the timer.
AGENT_CHAT_CATALOG_TICK_INTERVAL = datetime.timedelta(hours=1)
# Operator setting: how many hours between automatic provider model checks.
PROVIDER_CATALOG_REFRESH_HOURS_MIN = 6
PROVIDER_CATALOG_REFRESH_HOURS_MAX = 720
PROVIDER_CATALOG_REFRESH_HOURS_DEFAULT = 24

# agentCLI: the in-panel LLM chat that runs an agentic loop server-side on
# behalf of a chosen access key. Provider API keys / base URLs persist in a dedicated
# Store, separate from access keys, so the recoverable secret never rides the general
# settings serialization (which the settings GET and audit snapshots echo).
AGENT_CHAT_SECRETS_STORAGE_KEY = "phoenix_mcp_agentcli_secrets"
AGENT_CHAT_SECRETS_STORAGE_VERSION = 1
PROVIDER_API_KEY_MAX_LENGTH = 4096
AGENT_CHAT_PROVIDERS = frozenset({
    "claude", "deepseek", "chatgpt", "gemini", "grok", "kimi", "meta",
    "minimax", "mistral", "openrouter", "nvidia", "ollama", "ollama_cloud",
    "zai", "groq", "together", "cerebras", "cloudflare", "fireworks", "qwen", "opencode", "mimo", "tencent", "infron",
    "local_openai", "azure"})
# Kinds whose models run on a server the operator supplies (often on the home network), so its
# address may be private and its first reply includes loading the model on the operator's hardware.
# `local_decision` is a decision account; only the address rules apply to it.
AGENT_CHAT_LOCAL_SERVER_KINDS = frozenset({"ollama", "local_openai", "local_decision"})
# Bionic's own model list, beside its OpenAI one: it gives each model's type, and leaves out the voice
# model its OpenAI list still names. Read from the server root, the address without /v1.
AGENT_CHAT_LOCAL_NATIVE_MODELS_PATH = "/api/v1/models"
# The software behind a local server, shown in its account name so two accounts can be told apart.
# Bionic answers the typed list above; llama.cpp and vLLM name themselves in every model's owned_by.
AGENT_CHAT_LOCAL_NATIVE_SERVER = "Bionic"
# Bionic's declared reasoning options on the reasoning_effort wire: its "off" is "none", and its "on"
# (the model's default) has no value, so it is left out. The other options are already wire levels.
AGENT_CHAT_LOCAL_REASONING_WIRE = MappingProxyType({"off": "none"})
# Ways to turn a local server's thinking off, tried in order by Check Options on a model whose server
# declared nothing; the first that stops it is stored as the model's off method. llama.cpp and Bionic
# read reasoning_effort "none"; a template such as Qwen's on another server reads enable_thinking.
AGENT_CHAT_LOCAL_THINKING_OFF_METHODS = ("reasoning_effort", "enable_thinking")
# llama.cpp's report on its loaded model, read from the server root: image support, and its chat
# template's tool support and effort levels. It describes one model, so a multi-model list skips it.
AGENT_CHAT_LOCAL_PROPS_PATH = "/props"
AGENT_CHAT_LOCAL_SERVER_OWNERS = MappingProxyType({"llamacpp": "llama.cpp", "vllm": "vLLM"})
AGENT_CHAT_LOCAL_SERVER_NAMES = frozenset({AGENT_CHAT_LOCAL_NATIVE_SERVER, *AGENT_CHAT_LOCAL_SERVER_OWNERS.values()})
# Default endpoints/models; the panel may override the model per conversation.
AGENT_CHAT_CLAUDE_BASE_URL = "https://api.anthropic.com"
AGENT_CHAT_ANTHROPIC_VERSION = "2023-06-01"
AGENT_CHAT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# No provider ships a default model id. An account without a chosen model is in
# AUTOMATIC mode and follows the newest model of the provider's own catalogue
# (see model_selection.py), so a retired or replaced model never needs a Phoenix
# release. The retired `deepseek-chat` and `deepseek-reasoner` ids were routing
# labels for thinking modes, which is why the mode is the `thinking` field.
# ChatGPT (OpenAI) is OpenAI-compatible; base already includes /v1.
AGENT_CHAT_OPENAI_BASE_URL = "https://api.openai.com/v1"
# Gemini via Google's OpenAI-compatible endpoint (Authorization: Bearer <key>);
# base already includes the OpenAI path, so chat/models append cleanly.
AGENT_CHAT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# Grok (xAI) is OpenAI-compatible; base includes /v1 so chat/models append cleanly.
AGENT_CHAT_GROK_BASE_URL = "https://api.x.ai/v1"
# MiniMax via its Anthropic-compatible API (Authorization: Bearer <key>); base has
# no /v1, so /v1/messages and /v1/models append cleanly. It speaks the Anthropic
# Messages wire format, so it runs through ClaudeProvider, not OpenAICompatProvider.
AGENT_CHAT_MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"
# MiniMax's /v1/models is documented but not guaranteed on every key; this curated
# set is ONLY the fallback when the live listing returns nothing. Ids are mixed
# case. Automatic mode ranks it like any catalogue, so a new MiniMax model reaches
# an account through the live listing without touching this tuple.
AGENT_CHAT_MINIMAX_MODELS = (
    "MiniMax-M3.1-Flash", "MiniMax-M3",
)
# Mistral's hosted API is OpenAI-compatible for chat and publishes live model
# capabilities. Base includes /v1 so chat/models append cleanly. There is no
# shipped default because the account-scoped list is authoritative.
AGENT_CHAT_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
# Kimi (Moonshot) via its OpenAI-compatible API (Authorization: Bearer); base
# includes /v1 so chat/models append cleanly. It also fronts an
# Anthropic-compatible path at /anthropic, but the OpenAI path is the documented
# primary, is the one whose tool-calling is documented, and is the only one with a
# models-list endpoint, so Phoenix MCP runs Kimi through OpenAICompatProvider.
AGENT_CHAT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
# No default model, the ChatGPT/Grok convention: Kimi's /v1/models is scoped to
# what the ACCOUNT is entitled to, not the published catalog (K3 is plan-gated,
# and k2.5 + the moonshot-v1 series are withheld from newly registered users), so
# only the live list knows which models a given key can actually call. Naming a
# flagship default here would offer a model that answers 404 on the lower plans.
# Meta's Model API (dev.meta.ai) is OpenAI-compatible; base includes /v1 so
# chat/models append cleanly. Its predecessor api.llama.com is being retired.
AGENT_CHAT_META_BASE_URL = "https://api.meta.ai/v1"
# OpenRouter aggregates many providers behind one OpenAI-compatible key (a common
# region-friendly way to reach Meta's Llama and hundreds of other models). Base
# includes /api/v1; no default model (the live tool-capable list drives the dropdown).
AGENT_CHAT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Infron (infron.ai) serves its API from onerouter.pro. Its model list answers without
# a key, so only the balance endpoint, on a second host, can prove one.
AGENT_CHAT_INFRON_BASE_URL = "https://llm.onerouter.pro/v1"
AGENT_CHAT_INFRON_BALANCE_URL = "https://api.onerouter.pro/v1/balance"
# NVIDIA (build.nvidia.com) hosts many vendors' models behind one OpenAI-compatible
# key (Authorization: Bearer nvapi-...); base includes /v1 so chat/models append
# cleanly. No default model: the live list drives the dropdown. Model ids are
# vendor-namespaced (e.g. "meta/llama-3.3-70b-instruct"), and tool-calling support
# varies by model, which the catalog does not advertise.
AGENT_CHAT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Local Ollama has no default: the operator supplies a local base URL and picks a
# model from the installed list. Ollama Cloud is the same wire format but hosted
# at a fixed URL and authenticated with an API key (Authorization: Bearer).
AGENT_CHAT_OLLAMA_CLOUD_BASE_URL = "https://ollama.com"
# Additional OpenAI-compatible providers. Z.ai has two fixed plans selected by
# endpoint id; Qwen uses the workspace-specific URL supplied by the operator.
AGENT_CHAT_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
AGENT_CHAT_ZAI_CODING_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
AGENT_CHAT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
AGENT_CHAT_TOGETHER_BASE_URL = "https://api.together.ai/v1"
AGENT_CHAT_CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
# Workers AI's OpenAI-compatible address carries the operator's account id. Its model list is the
# account API's model search, one level up; text generation is the only task a chat account uses.
AGENT_CHAT_CLOUDFLARE_BASE_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
AGENT_CHAT_CLOUDFLARE_MODELS_QUERY = "models/search?task=Text%20Generation&per_page=100"
# Microsoft Azure (Foundry) serves each resource at its own host; the operator gives that address, and
# Phoenix keeps only the host, since the project address Foundry shows does not serve this API. The
# key is bound to the resource, so it never goes to a host outside these suffixes.
AGENT_CHAT_AZURE_HOST_SUFFIXES = (".services.ai.azure.com", ".openai.azure.com", ".cognitiveservices.azure.com")
AGENT_CHAT_AZURE_API_PATH = "/openai/v1"
# The v1 model list is Azure's whole catalog, not what the resource can run; a request names a deployment.
# Only this older data-plane version still lists deployments with an API key; newer versions answer 404.
AGENT_CHAT_AZURE_DEPLOYMENTS_PATH = "/openai/deployments?api-version=2023-03-15-preview"
AGENT_CHAT_FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
AGENT_CHAT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
AGENT_CHAT_TENCENT_SINGAPORE_BASE_URL = "https://tokenhub-intl.tencentcloudmaas.com/v1"
AGENT_CHAT_TENCENT_GUANGZHOU_BASE_URL = "https://tokenhub.tencentcloudmaas.com/v1"
AGENT_CHAT_TENCENT_US_BASE_URL = "https://tokenhub-us.tencentcloudmaas.com/v1"
AGENT_CHAT_OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"
# Zen stays the route of accounts saved before Console routing existed; new accounts start on Console.
AGENT_CHAT_OPENCODE_CONSOLE_BASE_URL = "https://opencode.ai/inference/openai/v1"
AGENT_CHAT_OPENCODE_CONSOLE_MODELS_URL = "https://opencode.ai/inference/v1/models"
# Console eligibility needs a documented Chat Completions endpoint or a qualified
# live tool round. The catalog now also lists each model's API family; this set
# remains the documented source until that metadata is qualified. Free models are
# left out: OpenCode serves them only to its own app.
# Source: https://opencode.ai/docs/console/models/
AGENT_CHAT_OPENCODE_CHAT_MODELS = frozenset({
    "qwen3.8-max", "deepseek-v4.1-flash", "deepseek-v4-pro",
    "minimax-m3", "minimax-m2.7", "minimax-m2.5", "glm-5.3-flash",
    "glm-5.3", "glm-5.2", "glm-5.1", "glm-5", "kimi-k2.5", "kimi-k2.6",
    "kimi-k2.7-code", "kimi-k3", "mistral-large-4",
    # The table lists Messages, but live Chat Completions tool rounds also pass.
    "qwen3.5-plus",
})
# Exact exceptions to the legacy family filter, qualified on the Zen route.
AGENT_CHAT_OPENCODE_LEGACY_CHAT_EXCEPTIONS = frozenset({"qwen3.8-max", "qwen3.5-plus"})
# Regional and plan-specific endpoints a provider documents next to its default.
# An API key is bound to the region or plan it was issued for, so each is a
# separate choice rather than a rewrite of the default URL.
AGENT_CHAT_KIMI_CHINA_BASE_URL = "https://api.moonshot.cn/v1"
AGENT_CHAT_ZAI_CHINA_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
AGENT_CHAT_MIMO_TOKEN_PLAN_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
AGENT_CHAT_MIMO_TOKEN_PLAN_SGP_BASE_URL = "https://token-plan-sgp.xiaomimimo.com/v1"
AGENT_CHAT_MIMO_TOKEN_PLAN_AMS_BASE_URL = "https://token-plan-ams.xiaomimimo.com/v1"
AGENT_CHAT_MISTRAL_EU_BASE_URL = "https://api.eu.mistral.ai/v1"
AGENT_CHAT_MISTRAL_US_BASE_URL = "https://api.us.mistral.ai/v1"
AGENT_CHAT_OPENAI_US_BASE_URL = "https://us.api.openai.com/v1"
AGENT_CHAT_OPENAI_EU_BASE_URL = "https://eu.api.openai.com/v1"
# OpenAI's other data-residency prefixes. A residency project must use its own
# prefixed host. US, EU and UAE process in-region; Australia, Japan and India do so for some
# models only, and Canada, Singapore, Korea and the UK store in-region.
AGENT_CHAT_OPENAI_REGION_PREFIXES = ("ae", "au", "ca", "jp", "in", "sg", "kr", "gb")
AGENT_CHAT_MINIMAX_CHINA_BASE_URL = "https://api.minimaxi.com/anthropic"
AGENT_CHAT_GROK_US_BASE_URL = "https://us.api.x.ai/v1"
# Output cap for Anthropic-format requests. Thinking counts toward it, and a
# large tool call (a whole-dashboard write) cut off mid-JSON cannot run, so it
# sits well above a normal answer. Every current Claude model accepts it; a model
# that refuses it is retried once at the provider's default.
AGENT_CHAT_DEFAULT_MAX_TOKENS = 32000
# Explicit output cap sent to DeepSeek only while thinking is OFF, where its
# default (8K) can truncate a large tool call mid-JSON. With thinking on, its
# defaults (64K, 128K at maximum effort) are larger, so nothing is sent. Other
# OpenAI-compatible kinds are deliberately not sent max_tokens (OpenAI reasoning
# models reject the field outright), except NVIDIA below.
AGENT_CHAT_DEEPSEEK_MAX_TOKENS = 32768
# NVIDIA's hosted models default as low as 1024 output tokens (Llama 3.3 70B),
# while its reasoning models default to 16384 and share it with their reasoning.
# A larger cap keeps those whole; a model whose range is smaller (gpt-oss-20b
# allows 4096) refuses it and is retried once without the field.
AGENT_CHAT_NVIDIA_MAX_TOKENS = 32768
# Characters per token for estimating a request's size before a local Ollama
# server sees it, as measured on a whole Phoenix request.
AGENT_CHAT_PROMPT_CHARS_PER_TOKEN = 4.6
# Share of a local Ollama model's loaded context kept for the reply (thinking and
# a tool call); a prompt above the rest is refused rather than silently cut.
AGENT_CHAT_OLLAMA_REPLY_SHARE = 0.1
# How many Ollama /api/show lookups run at once during a capability refresh.
# Ollama is the only backend whose capabilities cost one request PER MODEL, and a
# large local library would otherwise arrive as one burst against a machine that
# is also running Home Assistant.
AGENT_CHAT_CAPABILITY_CONCURRENCY = 6

AGENT_CHAT_DEFAULT_EFFORT = "high"
AGENT_CHAT_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
# The same levels in a fixed order, which the capability probe walks. A frozenset
# iterates arbitrarily, and a probe that reports its findings in a different order
# each run reads as a changing answer when nothing changed.
# "none" comes first: a provider that accepts it has a real way to turn
# reasoning off, which the panel then offers as Off.
AGENT_CHAT_EFFORT_LEVEL_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# The value the probe sends when it is asking "is this field read at all". It has
# to be something no provider could ever accept as a real level, and recognisable
# in a provider's own logs as a deliberate probe rather than a malformed client.
AGENT_CHAT_PROBE_SENTINEL = "phoenix-probe-invalid"

# Hard cap on completion requests one probe run may make. The matrix is bounded by
# construction (one control request, one validation check, seven levels and
# temperature), so this is a backstop that keeps a new knob from turning a button
# into unbounded spend.
AGENT_CHAT_PROBE_MAX_CALLS = 10
# Infron maps one documented set of efforts onto every vendor and refuses no value, so a refusal
# proves nothing there: a small reasoning question shows whether the effort does anything.
AGENT_CHAT_INFRON_EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")
AGENT_CHAT_REASONING_PROBE_MAX_TOKENS = 256
# Output room for the image check's short answer, with headroom for models
# that reason before replying.
AGENT_CHAT_PROBE_IMAGE_MAX_TOKENS = 2048

# How long a refusal LEARNED from a real turn is honoured before Phoenix tries
# the option again. It expires because the fact came from one moment in one
# provider's history: a model that rejects a parameter today may accept it after
# an upgrade, and a permanent "no" learned from a transient refusal would be the
# same silently-stale answer this whole area exists to remove.
AGENT_CHAT_LEARNED_REFUSAL_TTL_DAYS = 30

# Request keys a refusal can be learned for, mapped to the capability it settles.
# Deliberately short: a key is here only when refusing it has ONE unambiguous
# consequence for what the panel should offer.
AGENT_CHAT_LEARNABLE_OPTIONS = MappingProxyType({
    "temperature": "temperature",
    "reasoning_effort": "thinking",
    "thinking": "thinking",
    "think": "thinking",
    "enable_thinking": "thinking",
    "tools": "tools",
    # Not a request key: _refused_option reports it when an attached image was
    # refused as unsupported input, which teaches Phoenix the model lacks vision.
    "images": "vision",
})
# Per-turn cap on provider and tool round trips before the loop pauses. In the
# interactive chat this is a "continue?" checkpoint (the operator can grant
# another N rounds), not a hard stop; the headless voice/AI-task loops stop
# here since there is no one to ask. Admin-configurable via the
# agentcli_max_iterations setting; this is the default and the clamp bounds.
AGENT_CHAT_MAX_ITERATIONS = 20
AGENT_CHAT_MAX_ITERATIONS_MIN = 3
AGENT_CHAT_MAX_ITERATIONS_MAX = 100
# Per-turn cap on rounds that hand a malformed tool call back to the model as a
# tool error so it can resend the call. Each correction also spends one round of
# the iteration cap above; past this cap the turn ends with bad_tool_call.
AGENT_CHAT_MAX_TOOL_CALL_CORRECTIONS = 2
# The tools-array cap Groq, Mistral and Z.ai document or enforce, and Phoenix applies to Gemini, Kimi,
# OpenRouter and Infron, which document none. A
# provider definition opts in through max_tools; a larger catalog is declared in
# parts (tool_loading.py), and one whose starting set still exceeds it is refused.
AGENT_CHAT_MAX_TOOLS_PER_REQUEST = 128
# Phoenix's own client-local function that declares deferred tools; never an MCP tool.
AGENT_CHAT_LOAD_TOOLS_NAME = "load_tools"
# Tool-level loading: Phoenix's own loops declare a small core plus load_tools and list the
# rest by group in the system prompt. Measured across personas, providers and Voice, it gave
# the same task outcomes as the complete catalog with 55 to 70% fewer input tokens on large
# catalogs.
AGENT_CHAT_TOOL_LOADING_MODES = ("auto", "always", "never")
AGENT_CHAT_TOOL_LOADING_DEFAULT = "auto"
# Automatic loads in parts from this catalog size (compact JSON bytes, about 24,000 tokens).
# Below it loading saved under 10% per conversation and added a load round.
AGENT_CHAT_TOOL_LOADING_MIN_BYTES = 96_000
# Always declared: orientation, discovery, state, the generic service call and approvals.
AGENT_CHAT_CORE_TOOLS = (
    "get_capability_summary", "get_overview", "search_entities", "describe_entity", "get_state",
    "call_service", "find_available_actions", "get_approval_status", "wait_for_approval", "llm__GetDateTime",
)
# Anthropic's server-side tool search: on Claude, deferred tools are declared with defer_loading
# and found by this tool, so the tools array (and the prompt cache) never changes in a
# conversation. Measured: same outcomes as the complete catalog at about a quarter of its cost.
AGENT_CHAT_TOOL_SEARCH_TOOL: Mapping[str, str] = MappingProxyType(
    {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"})
# Claude models that use it; any other model keeps Phoenix's own loading.
AGENT_CHAT_TOOL_SEARCH_MODELS = r"^claude-(?:(?:opus|sonnet)-(?:4-[5-9]|[5-9])|haiku-[5-9]|fable-|mythos-)"
# Content blocks Anthropic's tool search adds to an assistant message.
AGENT_CHAT_TOOL_SEARCH_BLOCKS = ("server_tool_use", "tool_search_tool_result")
# Groups declared from the start on Voice, where an extra load round is an audible pause.
AGENT_CHAT_VOICE_PRELOADED_GROUPS = ("device_control",)
# Deferred groups: id -> (members, directory summary). A member is an exact tool name or a
# name fragment; exact names are matched before fragments, and fragments in this order (device
# families first, so get_esphome_automations joins esphome), and a tool matching nothing
# stays in the starting set, so no tool is ever hidden.
AGENT_CHAT_TOOL_GROUPS: Mapping[str, tuple[tuple[str, ...], str]] = MappingProxyType({
    "esphome": (("esphome",), "ESPHome Device Builder: device YAML, validation, builds, flashing, jobs, device logs "
                              "and diagnostics."),
    "zigbee": (("zigbee", "radio"), "Zigbee and radio networks: network and device diagnostics, pairing, options, "
                                    "bindings, reporting and groups."),
    "dashboards": (("dashboard",), "Lovelace dashboards, their views and cards, and dashboard resources."),
    "mesa": (("mesa_",), "MESA device-safety profiles, their explanations and control leases."),
    "device_control": (("intent__", "light__", "fan__", "climate__", "media_player__", "vacuum__",
                        "assist_satellite__", "homeassistant__GetLiveContext", "recognize_intent", "dry_run_service",
                        "get_camera_image"),
                       "Direct device commands: on/off, lights, fans, climate, covers, media players, vacuums, "
                       "timers, broadcasts, live context, camera snapshots, intent recognition and service dry runs."),
    "history": (("get_history", "get_statistics", "get_logbook", "recent_activity", "compare_states",
                 "compare_entities", "watch_entity"),
                "Past states, long-term statistics, the logbook, recent activity, comparisons and watching an entity."),
    "calendar_todo": (("_calendar_", "get_calendar", "todo"), "Calendar events and to-do lists: read, create, change and delete."),
    "automations": (("automation", "script", "scene", "blueprint", "test_trigger", "test_condition", "whatif",
                     "render_template"),
                    "Automations, scripts, scenes and blueprints: list, read, create, edit, delete, validate, traces, "
                    "testing triggers and conditions, what-if checks and template rendering."),
    "home_structure": (("describe_area", "list_areas", "list_floors", "list_zones", "list_devices", "get_device",
                        "list_labels", "get_relationships", "list_helpers"),
                       "Areas, floors, zones, devices, labels, the helper list and relationships between them."),
    "system": (("get_logs", "get_log_detail", "get_phoenix_diagnostics", "list_integration_log_levels",
                "set_integration_log_level", "get_system_health", "get_repairs", "check_ha_config", "get_config",
                "get_notifications", "send_notification", "get_audit_summary", "restart_ha"),
               "Home Assistant logs, diagnostics, system health, repairs, config check, configuration, "
               "notifications, the Phoenix audit summary and restart."),
    "energy": (("energy", "solar"), "Energy dashboard configuration and solar forecast."),
    "integrations": (("integration", "fix_repair", "remove_device"),
                     "Integrations: list, add, remove, reload, enable, reconfigure, subentries, repair fixes, and "
                     "removing devices."),
    "registry_writes": (("set_entity", "set_area_or_floor", "set_device", "delete_entity", "set_label_or_category"),
                        "Renaming, moving, enabling, disabling and removing entities, devices, areas, floors, "
                        "labels and categories."),
    "files_and_backups": (("list_files", "read_file", "get_yaml_config", "backup"),
                          "Reading configuration files and YAML, and backups."),
    "helpers": (("create_helper", "edit_helper", "delete_helper", "helper_settings", "restore_configuration"),
                "Creating, editing and deleting helpers, helper settings, and restoring configuration versions."),
})
# Longest provider error-envelope message shown to an administrator.
AGENT_CHAT_PROVIDER_DETAIL_CHARS = 300
# Bytes of a provider's error body read for classification; the rest is never read.
AGENT_CHAT_PROVIDER_ERROR_BYTES = 4000
# Images an administrator attaches to one Agent Chat message. The browser
# re-encodes each to a smaller JPEG first; the server bounds stay independent of it.
# The edge bound is the largest image any supported provider documents accepting.
AGENT_CHAT_ATTACHMENT_MAX_COUNT = 4
AGENT_CHAT_ATTACHMENT_MAX_BYTES = 2 * 1024 * 1024
AGENT_CHAT_ATTACHMENT_MAX_TOTAL_BYTES = 4 * 1024 * 1024
AGENT_CHAT_ATTACHMENT_MAX_EDGE_PX = 8000
# The chat request carries its history plus base64 attachments, so it gets the
# ordinary body cap plus room for the encoded attachment total.
AGENT_CHAT_REQUEST_BODY_BYTES = MAX_REQUEST_BODY_BYTES + -(-AGENT_CHAT_ATTACHMENT_MAX_TOTAL_BYTES * 4 // 3) + 65_536
# Cap on the between-newline SSE buffer from a provider stream. A real frame is a
# few KB; this bounds a faulty/hostile provider that never sends a newline.
AGENT_CHAT_MAX_SSE_BUFFER_BYTES = 8 * 1024 * 1024
# Cap on the CUMULATIVE bytes of a single provider response (all frames summed),
# so a flood of small newline-delimited frames cannot exhaust memory even though
# each frame stays under the per-frame cap. A real response is far smaller.
AGENT_CHAT_MAX_STREAM_BYTES = 32 * 1024 * 1024
# Cap on one streamed tool call's argument text. The dispatcher refuses arguments
# over MAX_REQUEST_BODY_BYTES as serialized UTF-8 JSON; a model's JSON text of the
# same arguments can be up to about three times longer (\u escapes, whitespace),
# so past this the call could never run and the response is refused at once.
AGENT_CHAT_MAX_TOOL_ARGUMENT_CHARS = 4 * MAX_REQUEST_BODY_BYTES
# Cap on one non-streaming provider reply (catalogue, capability and probe reads).
# The largest public catalogue is a few MB; this bounds a faulty or hostile server.
AGENT_CHAT_MAX_JSON_BYTES = 8 * 1024 * 1024
# Caps on a stored model catalogue: every id is rewritten with each provider-store
# save and each Ollama id costs one capability request.
AGENT_CHAT_MAX_CATALOG_MODELS = 2000
AGENT_CHAT_MAX_MODEL_ID_LENGTH = 200
# Cap on the TOTAL provider-generated output retained across a whole turn (all
# iterations), measured on the assistant messages held in the conversation. The
# per-response cap above bounds one stream; this bounds the sum so many iterations
# cannot accumulate a large multiple. A real multi-step turn stays far below it.
AGENT_CHAT_MAX_TURN_OUTPUT_BYTES = 48 * 1024 * 1024
# Max idle gap between stream data before the provider request is aborted. SSE
# comment lines (a gateway's keep-alive) count only until the first data line: a
# reasoning model behind a gateway may think for minutes before it, but a reply that
# stalls afterwards is not held open by them. Agent Chat's total time is deliberately
# unbounded (a long agentic turn streams for minutes), but a provider that stops
# sending must not hang the turn forever.
AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS = 180
# The longest one Voice or AI Task model call may take, however steadily the
# provider keeps sending. Nobody can cancel those turns, so a provider that
# trickles data forever must not hold an automation or a speaker indefinitely.
AGENT_HEADLESS_CALL_SECONDS = 600
# The same bound for a local Ollama server, which runs on the operator's own
# hardware: a CPU-only machine reads a long prompt for minutes and then produces a
# few tokens a second, so a thinking model's trace and answer can take far longer
# than 600 s. An hour still ends a model that generates without end.
AGENT_HEADLESS_LOCAL_CALL_SECONDS = 3600
# A retry after a temporary provider failure waits this long for the first reply
# rather than the full silent-stream bound: a provider that has just said it is
# overloaded and then sends nothing is stuck, not thinking.
AGENT_CHAT_RETRY_FIRST_REPLY_SECONDS = 60
# Agent Chat waits out a provider's named rate-limit wait (HTTP 429 with Retry-After) up to this long
# from the start of the call, even after the turn ran tools: a refused request did no work, and the
# retry resends the same messages, so no tool runs again. A per-minute token limit needs up to a minute.
AGENT_CHAT_RATE_LIMIT_WAIT_SECONDS = 60
# How often Agent Chat's status line reports a model call that has shown nothing yet.
AGENT_CHAT_STATUS_INTERVAL_SECONDS = 30
# Interactive inline-approval wait, tied to the live SSE connection (far longer
# than the per-access-key confirm_inline_wait cap, which is for unattended agents).
AGENT_CHAT_APPROVAL_WAIT_SECONDS = 900
# How often Agent Chat relays a held tool's progress line to the panel. Faster
# than the MCP keepalive because this is a local connection with no proxy in
# the way, and the panel is where a human is actually watching.
AGENT_CHAT_PROGRESS_INTERVAL_SECONDS = 2.0
# How many read calls of one model response run at once. A response has no call
# cap, and each read may hold a template worker or a Recorder query, so a model
# that sends dozens of reads gets them in waves rather than all together.
AGENT_PARALLEL_READ_LIMIT = 6
# Sentinel client_ip so agentCLI-driven tool calls are attributable in the audit
# log without a core change to _dispatch_mcp.
AGENT_CHAT_CLIENT_IP = "agentcli"
# Agent Chat memory: how much earlier conversation each request carries, counted as
# the server's request size check counts it (ASCII JSON, so a non-ASCII unit is its
# six-byte escape). The maximum stays under the 1 MiB request limit the window
# already leaves room for.
AGENT_CHAT_MEMORY_CHARS_DEFAULT = 200_000
AGENT_CHAT_MEMORY_CHARS_MIN = 0
AGENT_CHAT_MEMORY_CHARS_MAX = 960_000
# Display-only cap on the tool-result text streamed to the verbose panel view
# (the model always receives the full result via the message history). Large
# enough that ordinary results are shown whole; only pathological dumps clip.
AGENT_CHAT_TOOL_RESULT_MAX_CHARS = 20000
