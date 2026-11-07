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
    "zai", "groq", "together", "cerebras", "fireworks", "qwen", "opencode", "mimo", "tencent", "infron"})
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
    "MiniMax-M3.1-Flash", "MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed", "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed", "MiniMax-M2.1", "MiniMax-M2.1-highspeed", "MiniMax-M2",
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
AGENT_CHAT_FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
AGENT_CHAT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
AGENT_CHAT_TENCENT_SINGAPORE_BASE_URL = "https://tokenhub-intl.tencentcloudmaas.com/v1"
AGENT_CHAT_TENCENT_GUANGZHOU_BASE_URL = "https://tokenhub.tencentcloudmaas.com/v1"
AGENT_CHAT_TENCENT_US_BASE_URL = "https://tokenhub-us.tencentcloudmaas.com/v1"
AGENT_CHAT_OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"
# Keep Zen as the default for accounts saved before Console routing existed.
AGENT_CHAT_OPENCODE_CONSOLE_BASE_URL = "https://opencode.ai/inference/openai/v1"
AGENT_CHAT_OPENCODE_CONSOLE_MODELS_URL = "https://opencode.ai/inference/v1/models"
# Console eligibility needs a documented Chat Completions endpoint or a qualified
# live tool round. The catalog now also lists each model's API family; this set
# remains the documented source until that metadata is qualified. Free models are
# left out: OpenCode serves them only to its own app.
# Source: https://opencode.ai/v2/docs/console/models/
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
# prefixed host; US, EU and UAE also process in-region, the others store only.
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
# NVIDIA's hosted models default as low as 1024 output tokens (Llama 3.3 70B);
# 4096 is within each documented model range checked. A model that refuses it is
# retried once without the field.
AGENT_CHAT_NVIDIA_MAX_TOKENS = 4096
# Characters per token for estimating a request's size before a local Ollama
# server sees it. Set above what a whole Phoenix request runs at, so the estimate
# errs low and an estimate over the loaded context is a real overflow.
AGENT_CHAT_PROMPT_CHARS_PER_TOKEN = 5.0
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
# The tools-array cap OpenAI, Groq, Mistral and Z.ai document or enforce. A
# provider definition opts in through max_tools; a larger catalog is declared in
# parts (tool_loading.py), and one whose starting set still exceeds it is refused.
AGENT_CHAT_MAX_TOOLS_PER_REQUEST = 128
# Phoenix's own client-local function that declares deferred tools; never an MCP tool.
AGENT_CHAT_LOAD_TOOLS_NAME = "load_tools"
# Groups of tools deferred under a provider's tools-array cap: id -> (capabilities,
# name fragments, directory summary). A tool joins the first group whose
# capabilities hold its first capability or one of whose fragments is in its
# name; every other tool is in the starting set.
AGENT_CHAT_DEFERRED_TOOL_GROUPS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...], str]] = MappingProxyType({
    "esphome": (("cap_esphome_yaml", "cap_esphome_flash"), ("esphome",),
                "ESPHome Device Builder: device YAML, validation, builds, flashing, jobs, device logs and diagnostics."),
    "dashboards": (("cap_lovelace_write", "cap_dashboard_resources"), ("dashboard",),
                   "Lovelace dashboards, their views and cards, and dashboard resources."),
    "zigbee": (("cap_radio_write",), ("zigbee", "radio"),
               "Zigbee and radio networks: network and device diagnostics, pairing, options, bindings, reporting and groups."),
    "integrations": (("cap_integration_write", "cap_integration_reconfigure"), (),
                     "Adding, removing, reloading and reconfiguring integrations."),
    "registry_writes": (("cap_registry_write",), (), "Renaming, moving, enabling, disabling and removing registry entries."),
    "files_and_backups": (("cap_backup", "cap_filesystem", "cap_yaml_edit"), (), "Reading configuration files and YAML, and backups."),
    "helpers": (("cap_helper_write",), (), "Creating, editing and deleting helpers."),
    "mesa": ((), ("mesa_",), "MESA device-safety profiles, their explanations and control leases."),
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
# Max idle gap between stream chunks before the provider request is aborted. The
# total time is deliberately unbounded (a long agentic turn streams for minutes),
# but a provider that stops sending data must not hang the turn forever.
AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS = 180
# A retry after a temporary provider failure waits this long for the first reply
# rather than the full silent-stream bound: a provider that has just said it is
# overloaded and then sends nothing is stuck, not thinking.
AGENT_CHAT_RETRY_FIRST_REPLY_SECONDS = 60
# How often Agent Chat's status line reports a model call that has shown nothing yet.
AGENT_CHAT_STATUS_INTERVAL_SECONDS = 30
# Interactive inline-approval wait, tied to the live SSE connection (far longer
# than the per-access-key confirm_inline_wait cap, which is for unattended agents).
AGENT_CHAT_APPROVAL_WAIT_SECONDS = 900
# How often Agent Chat relays a held tool's progress line to the panel. Faster
# than the MCP keepalive because this is a local connection with no proxy in
# the way, and the panel is where a human is actually watching.
AGENT_CHAT_PROGRESS_INTERVAL_SECONDS = 2.0
# Sentinel client_ip so agentCLI-driven tool calls are attributable in the audit
# log without a core change to _dispatch_mcp.
AGENT_CHAT_CLIENT_IP = "agentcli"
# Transcript scrollback bound (also the conversation-memory bound): lines dropped
# from the window are dropped from what the LLM is sent.
AGENT_CHAT_SCROLLBACK_DEFAULT = 100
AGENT_CHAT_SCROLLBACK_MIN = 0
AGENT_CHAT_SCROLLBACK_MAX = 5000
# Display-only cap on the tool-result text streamed to the verbose panel view
# (the model always receives the full result via the message history). Large
# enough that ordinary results are shown whole; only pathological dumps clip.
AGENT_CHAT_TOOL_RESULT_MAX_CHARS = 20000
