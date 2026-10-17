"""agentCLI: the in-panel LLM chat that runs an agentic loop server-side.

Phoenix MCP itself drives an LLM from a configured provider account on behalf
of a chosen Phoenix MCP access key, calling that access key's scoped Home Assistant tools through
the same in-process dispatch a real MCP client uses, so every capability gate,
MESA check, approval, and audit entry fires identically. The loop streams its
progress to the panel over Server-Sent Events; the browser owns the (ephemeral)
transcript and re-sends it each turn, so this module keeps no conversation state.

The module takes no external Python dependency: both the Anthropic Messages
API and the OpenAI-compatible provider APIs are spoken over raw aiohttp via HA's
shared client session, parsing streaming SSE by hand. The provider API key is
loaded from a dedicated secrets Store and is never logged, persisted into a
transcript, or echoed in an error payload.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import codecs
from contextlib import aclosing
from copy import deepcopy
import functools
import hashlib
from datetime import datetime, timedelta, tzinfo
import ipaddress
import json
import logging
import random
import re
import struct
import uuid
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable, Callable, cast
from urllib.parse import urlparse, urlunparse

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .routing_statistics import tracked_turn, provider_events, voice_result, untracked_turn
from .conversation_log import record_rejected_tool, sanitized

from .view_base import PhoenixView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import parse_datetime, utcnow

from .admin_view import _audit_admin, _err, _ok, _read_body, require_admin
from .audit import generate_request_id
from .agent_recovery import RecoveryRequired, dispatch_agent_tool, recovery_turn, tool_is_read_only
from .approvals import (
    REASON_AGENT_CHAT_ENDED,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REJECTED,
    dismiss_approval_notification,
    fire_approval_resolved_event,
    get_approval,
    async_update_approval_status,
)
from .const import (
    DECISION_TYPESAFE_BASE_URL, DECISION_TYPESAFE_MODEL,
    AGENTCLI_ANTHROPIC_VERSION,
    AGENTCLI_ATTACHMENT_MAX_BYTES, AGENTCLI_ATTACHMENT_MAX_COUNT,
    AGENTCLI_ATTACHMENT_MAX_EDGE_PX, AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES,
    AGENTCLI_REQUEST_BODY_BYTES, MAX_REQUEST_BODY_BYTES,
    PROVIDER_API_KEY_MAX_LENGTH,
    AGENTCLI_APPROVAL_WAIT_SECONDS,
    AGENTCLI_PROGRESS_INTERVAL_SECONDS,
    AGENTCLI_CLAUDE_BASE_URL,
    AGENTCLI_CLIENT_IP,
    AGENTCLI_CAPABILITY_CONCURRENCY,
    AGENTCLI_EFFORT_LEVEL_ORDER,
    AGENTCLI_LEARNABLE_OPTIONS,
    AGENTCLI_LEARNED_REFUSAL_TTL_DAYS,
    AGENTCLI_PROBE_IMAGE_MAX_TOKENS,
    AGENTCLI_PROBE_MAX_CALLS,
    AGENTCLI_PROBE_SENTINEL,
    AGENTCLI_DEEPSEEK_BASE_URL,
    AGENTCLI_GEMINI_BASE_URL,
    AGENTCLI_GROK_BASE_URL,
    AGENTCLI_GROQ_BASE_URL,
    AGENTCLI_KIMI_BASE_URL,
    AGENTCLI_META_BASE_URL,
    AGENTCLI_MINIMAX_BASE_URL,
    AGENTCLI_MINIMAX_MODELS,
    AGENTCLI_MISTRAL_BASE_URL,
    AGENTCLI_CEREBRAS_BASE_URL,
    AGENTCLI_FIREWORKS_BASE_URL,
    AGENTCLI_NVIDIA_BASE_URL,
    AGENTCLI_OLLAMA_CLOUD_BASE_URL,
    AGENTCLI_KIMI_CHINA_BASE_URL,
    AGENTCLI_ZAI_CHINA_BASE_URL,
    AGENTCLI_MIMO_TOKEN_PLAN_BASE_URL,
    AGENTCLI_MISTRAL_EU_BASE_URL,
    AGENTCLI_MISTRAL_US_BASE_URL,
    AGENTCLI_OPENAI_US_BASE_URL,
    AGENTCLI_OPENAI_EU_BASE_URL,
    AGENTCLI_GROK_US_BASE_URL,
    AGENTCLI_OPENAI_BASE_URL,
    AGENTCLI_OPENCODE_BASE_URL,
    AGENTCLI_MIMO_BASE_URL,
    AGENTCLI_TENCENT_SINGAPORE_BASE_URL,
    AGENTCLI_TENCENT_GUANGZHOU_BASE_URL,
    AGENTCLI_TENCENT_US_BASE_URL,
    AGENTCLI_OPENROUTER_BASE_URL,
    AGENTCLI_TOGETHER_BASE_URL,
    AGENTCLI_ZAI_BASE_URL,
    AGENTCLI_ZAI_CODING_BASE_URL,
    AGENTCLI_DEEPSEEK_MAX_TOKENS,
    AGENTCLI_DEFAULT_EFFORT,
    AGENTCLI_DEFAULT_MAX_TOKENS,
    AGENTCLI_EFFORT_LEVELS,
    AGENTCLI_MAX_ITERATIONS,
    AGENTCLI_MAX_SSE_BUFFER_BYTES,
    AGENTCLI_MAX_STREAM_BYTES,
    AGENTCLI_MAX_TOOL_CALL_CORRECTIONS,
    AGENTCLI_MAX_TOOLS_PER_REQUEST,
    AGENTCLI_MAX_TURN_OUTPUT_BYTES,
    AGENTCLI_PROVIDER_DETAIL_CHARS,
    AGENTCLI_STREAM_READ_TIMEOUT_SECONDS,
    AGENTCLI_SECRETS_STORAGE_KEY,
    AGENTCLI_SECRETS_STORAGE_VERSION,
    AGENTCLI_TOOL_RESULT_MAX_CHARS,
    AI_TASK_CLIENT_IP,
    CONVERSATION_STYLE_DIRECT,
    DETAIL_LEVEL_CONCISE,
    DOMAIN,
    MAX_TOOL_NAME_LENGTH,
    MESA_APPROVED_EXECUTOR,
    VOICE_AGENT_CLIENT_IP,
)
from .data import PhoenixData
from .model_selection import effective_model
from .helpers import get_client_ip, access_key_has_write_scope, voice_text
from .access_key_store import AccessKeyRecord

_LOGGER = logging.getLogger(__name__)

# Fast-failing timeout for the setup/validation probes so an unreachable host
# does not hang the panel for the OS default (~20s).
_PROBE_TIMEOUT = ClientTimeout(total=10, connect=5, sock_connect=5)
# The image check waits for a real answer, which a reasoning model can take a while to give.
_IMAGE_PROBE_TIMEOUT = ClientTimeout(total=90, connect=5, sock_connect=5)
# How many distinct model ids an account remembers having seen listed.
_MODEL_SEEN_LIMIT = 1000
# OpenAI models that rejected the forced `reasoning_effort: none` of a tool turn
# (non-reasoning or older models). Process-local: a restart re-learns it with one
# retried request.
_OPENAI_NO_EFFORT_NONE: set[str] = set()
# Providers that require (or reward) the assistant's reasoning_content being sent
# back verbatim with its tool calls on the next round. Kimi's thinking models and
# Qwen's preserve_thinking models take it the same way; others use the field for
# display only.
_REPLAY_REASONING_KINDS = frozenset({"deepseek", "zai", "mimo", "tencent", "kimi", "qwen", "fireworks"})
# Upper bound on catalogue pages followed, so a misbehaving provider cannot loop forever.
_MODEL_LIST_MAX_PAGES = 10


def _epoch(value: Any) -> float | None:
    """A provider timestamp (Unix seconds or an ISO string) as epoch seconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str) and value:
        parsed = parse_datetime(value)
        return parsed.timestamp() if parsed else None
    return None


def _is_expired(row: Any) -> bool:
    """Whether a catalogue row carries a shutdown date that has already passed."""
    if not isinstance(row, dict):
        return False
    for key in ("shutdown_date", "expiration_date", "deprecation"):
        value = row.get(key)
        stamp = parse_datetime(value) if isinstance(value, str) and "T" in value else (
            dt_util.parse_date(value) if isinstance(value, str) and value else None)
        if stamp is None:
            continue
        when = stamp if isinstance(stamp, datetime) else datetime.combine(stamp, datetime.min.time(), tzinfo=dt_util.UTC)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.UTC)
        if when <= utcnow():
            return True
    return False


def _catalog_of(cfg: dict) -> dict:
    """The cached provider catalogue of a stored account, or {} when it has none."""
    catalog = cfg.get("catalog")
    return catalog if isinstance(catalog, dict) else {}


def _created_map(rows: Any) -> dict[str, float]:
    """Creation time per model id from a provider's catalogue rows, where it gives one."""
    out: dict[str, float] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id") or row.get("name") or row.get("model")
        stamp = _epoch(row.get("created") or row.get("created_at") or row.get("modified_at"))
        if isinstance(model_id, str) and model_id and stamp:
            out[model_id] = stamp
    return out
# Chat requests stream and can legitimately run for minutes, so bound only the
# connection, not the total time. The connect bound is kept modest so a transient
# DNS/connect failure fails fast enough to be retried within the retry window.
_CHAT_TIMEOUT = ClientTimeout(
    total=None, connect=8, sock_connect=8,
    sock_read=AGENTCLI_STREAM_READ_TIMEOUT_SECONDS,
)
# A transient connection failure (e.g. a DNS blip) before any content is streamed
# is retried with this backoff until the total window elapses, then surfaced.
_CONNECT_RETRY_WINDOW_SECONDS = 12.0
_CONNECT_RETRY_BACKOFF_SECONDS = 1.5
_CONNECT_RETRY_MAX_ATTEMPTS = 3
# The Settings "Validate"/probe step retries a transient connection failure (e.g. a
# DNS blip that fails within a second) a few times with a short backoff, so a
# one-off hiccup does not reject a good credential. An auth rejection or a real HTTP
# error is never retried.
_VALIDATE_ATTEMPTS = 3
_VALIDATE_RETRY_BACKOFF_SECONDS = 0.6
# Idle keepalive cadence for the SSE stream, so an intermediary does not drop the
# connection during a quiet stretch (model thinking, a pending approval).
AGENTCLI_HEARTBEAT_SECONDS = 15.0

# Normalized streaming-event types yielded by every provider, so the loop is
# provider-agnostic.
EV_TEXT = "text_delta"
EV_THINKING = "thinking_delta"
EV_TOOL = "tool_use_complete"
EV_DONE = "message_done"
EV_ERROR = "provider_error"
# A complete response whose tool arguments did not parse as JSON. It replaces
# EV_TOOL and EV_DONE, so nothing from the response can dispatch. It carries a
# replayable assistant message (placeholder `{}` arguments that are never run)
# and one tool error per call, so a loop can hand the error back to the model.
# A loop that cannot correct treats it as a terminal bad_tool_call error.
EV_INVALID_TOOLS = "invalid_tool_calls"
# Provider-reported token usage for the CURRENT model call, cumulative-replace
# semantics: each event carries the latest known totals for that one call, so a
# consumer keeps the newest event's numbers rather than summing events. Counts
# are exact from the provider; Phoenix MCP never estimates tokens itself.
EV_USAGE = "usage_update"


# --------------------------------------------------------------------------- #
# Secrets / provider configuration store
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProviderConfig:
    """Resolved config for one provider request (secret + non-secret combined)."""

    kind: str
    model: str
    base_url: str
    api_key: str | None = None
    # Tri-state: True is explicit provider/model evidence, False is explicit
    # refusal, and None means unknown. Unknown must remain text-only.
    vision: bool | None = None
    endpoint_id: str | None = None
    decision_routing_revision: str = ""


@dataclass(frozen=True)
class ProbeConfigError:
    """A Phoenix-authored setup error with its panel localization contract."""

    message: str
    key: str
    params: dict[str, str | int] | None = None

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": False,
            "error": self.message,
            "message_key": f"adminError.{self.key}",
            "models": [],
        }
        if self.params:
            out["message_params"] = self.params
        return out


@dataclass(frozen=True)
class ProviderChoice:
    """One safe, non-secret choice rendered by a provider setup field."""

    value: str
    label: str
    label_key: str | None = None
    base_url: str | None = None

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"value": self.value, "label": self.label}
        if self.label_key:
            out["label_key"] = self.label_key
        return out


@dataclass(frozen=True)
class ProviderField:
    """One declarative credential/setup field for the shared panel form."""

    id: str
    type: str
    label_key: str
    required: bool = True
    placeholder: str | None = None
    choices: tuple[ProviderChoice, ...] = ()

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "type": self.type, "label_key": self.label_key,
            "required": self.required,
        }
        if self.placeholder:
            out["placeholder"] = self.placeholder
        if self.choices:
            out["choices"] = [choice.public() for choice in self.choices]
        return out


@dataclass(frozen=True)
class ProviderDefinition:
    """Immutable provider registration, including protocol and setup policy."""

    kind: str
    label: str
    adapter: str
    base_url: str
    model: str = ""
    label_key: str | None = None
    fields: tuple[ProviderField, ...] = ()
    endpoints: tuple[ProviderChoice, ...] = ()
    include_usage: bool = False
    reasoning: str = "effort"
    model_filter: str = "chat"
    models_shape: str = "data"
    validation_error: str | None = None
    ollama: str | None = None
    purpose: str = "conversation"
    # Most tools one request may carry; a larger catalog is refused before the
    # call rather than answered by the provider's HTTP 400. None: no known cap.
    max_tools: int | None = None
    # How tool calls arrive in a stream. "openai" concatenates argument deltas
    # by index; "gemini" is Gemini's compatibility layer (see _merged_whole_arguments).
    tool_stream: str = "openai"

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind, "label": self.label,
            "fields": [field.public() for field in self.fields],
        }
        if self.label_key:
            out["label_key"] = self.label_key
        return out


_API_KEY_FIELD = ProviderField(
    "api_key", "secret", "settings.agentcliApiKey",
)
_OLLAMA_URL_FIELD = ProviderField(
    "base_url", "url", "settings.agentcliServerUrl", placeholder="http://host:11434",
)
_QWEN_URL_FIELD = ProviderField(
    "base_url", "url", "settings.agentcliBaseUrl",
    placeholder="https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
)
_ZAI_ENDPOINTS = (
    ProviderChoice(
        "standard", "Standard API", "settings.agentcliZaiStandard",
        AGENTCLI_ZAI_BASE_URL,
    ),
    ProviderChoice(
        "coding", "Coding Plan", "settings.agentcliZaiCoding",
        AGENTCLI_ZAI_CODING_BASE_URL,
    ),
    ProviderChoice(
        "china", "China (BigModel)", "settings.agentcliZaiChina",
        AGENTCLI_ZAI_CHINA_BASE_URL,
    ),
)
_ZAI_ENDPOINT_FIELD = ProviderField(
    "endpoint_id", "choice", "settings.agentcliZaiPlan",
    choices=_ZAI_ENDPOINTS,
)

_TENCENT_ENDPOINTS = (
    ProviderChoice("singapore", "Singapore", "settings.agentcliSingapore", AGENTCLI_TENCENT_SINGAPORE_BASE_URL),
    ProviderChoice("guangzhou", "Guangzhou", "settings.agentcliGuangzhou", AGENTCLI_TENCENT_GUANGZHOU_BASE_URL),
    ProviderChoice("silicon_valley", "Silicon Valley", "settings.agentcliSiliconValley", AGENTCLI_TENCENT_US_BASE_URL),
)
_TENCENT_ENDPOINT_FIELD = ProviderField(
    "endpoint_id", "choice", "settings.agentcliSite", choices=_TENCENT_ENDPOINTS,
)

def _region_field(choices: tuple[ProviderChoice, ...]) -> ProviderField:
    return ProviderField("endpoint_id", "choice", "settings.agentcliRegion", choices=choices)


_GLOBAL = ("global", "Global", "settings.agentcliRegionGlobal")
_KIMI_ENDPOINTS = (
    ProviderChoice(*_GLOBAL, AGENTCLI_KIMI_BASE_URL),
    ProviderChoice("china", "China", "settings.agentcliRegionChina", AGENTCLI_KIMI_CHINA_BASE_URL),
)
_MISTRAL_ENDPOINTS = (
    ProviderChoice(*_GLOBAL, AGENTCLI_MISTRAL_BASE_URL),
    ProviderChoice("eu", "Europe", "settings.agentcliRegionEu", AGENTCLI_MISTRAL_EU_BASE_URL),
    ProviderChoice("us", "United States", "settings.agentcliRegionUs", AGENTCLI_MISTRAL_US_BASE_URL),
)
_OPENAI_ENDPOINTS = (
    ProviderChoice(*_GLOBAL, AGENTCLI_OPENAI_BASE_URL),
    ProviderChoice("us", "United States", "settings.agentcliRegionUs", AGENTCLI_OPENAI_US_BASE_URL),
    ProviderChoice("eu", "Europe", "settings.agentcliRegionEu", AGENTCLI_OPENAI_EU_BASE_URL),
)
_GROK_ENDPOINTS = (
    ProviderChoice(*_GLOBAL, AGENTCLI_GROK_BASE_URL),
    ProviderChoice("us", "United States", "settings.agentcliRegionUs", AGENTCLI_GROK_US_BASE_URL),
)
_MIMO_ENDPOINTS = (
    ProviderChoice("payg", "Pay as you go", "settings.agentcliMimoPayg", AGENTCLI_MIMO_BASE_URL),
    ProviderChoice("token_plan", "Token Plan", "settings.agentcliMimoTokenPlan", AGENTCLI_MIMO_TOKEN_PLAN_BASE_URL),
)

_PROVIDER_LABEL_KEYS = {
    "claude": "settings.providerAnthropic",
    "deepseek": "settings.providerDeepSeek",
    "chatgpt": "settings.providerOpenAI",
    "gemini": "settings.providerGemini",
    "grok": "settings.providerGrok",
    "groq": "settings.providerGroq",
    "kimi": "settings.providerKimi",
    "meta": "settings.providerMeta",
    "minimax": "settings.providerMiniMax",
    "mistral": "settings.providerMistral",
    "mimo": "settings.providerMiMo",
    "tencent": "settings.providerTencent",
    "nvidia": "settings.providerNvidia",
    "opencode": "settings.providerOpenCodeZen",
    "ollama_cloud": "settings.providerOllamaCloud",
    "ollama": "settings.providerOllamaLocal",
    "openrouter": "settings.providerOpenRouter",
    "together": "settings.providerTogether",
    "cerebras": "settings.providerCerebras",
    "fireworks": "settings.providerFireworks",
    "qwen": "settings.providerQwen",
    "zai": "settings.providerZai",
}


def _definition(
    kind: str, label: str, base_url: str, *, adapter: str = "openai",
    model: str = "", label_key: str | None = None,
    fields: tuple[ProviderField, ...] = (_API_KEY_FIELD,),
    endpoints: tuple[ProviderChoice, ...] = (), include_usage: bool = False,
    reasoning: str = "effort", model_filter: str = "chat",
    models_shape: str = "data", validation_error: str | None = None,
    ollama: str | None = None, max_tools: int | None = None, tool_stream: str = "openai",
) -> ProviderDefinition:
    return ProviderDefinition(
        kind, label, adapter, base_url, model,
        label_key or _PROVIDER_LABEL_KEYS[kind], fields, endpoints,
        include_usage, reasoning, model_filter, models_shape, validation_error,
        ollama, max_tools=max_tools, tool_stream=tool_stream,
    )


PROVIDER_DEFINITIONS: dict[str, ProviderDefinition] = {
    item.kind: item for item in (
        _definition("claude", "Anthropic", AGENTCLI_CLAUDE_BASE_URL, adapter="anthropic"),
        _definition("deepseek", "DeepSeek", AGENTCLI_DEEPSEEK_BASE_URL, include_usage=True, reasoning="deepseek"),
        _definition(
            "chatgpt", "OpenAI", AGENTCLI_OPENAI_BASE_URL, include_usage=True, model_filter="openai",
            fields=(_region_field(_OPENAI_ENDPOINTS), _API_KEY_FIELD), endpoints=_OPENAI_ENDPOINTS,
            max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST,
        ),
        _definition("gemini", "Gemini", AGENTCLI_GEMINI_BASE_URL, reasoning="gemini", model_filter="gemini",
                    tool_stream="gemini"),
        _definition(
            "grok", "Grok", AGENTCLI_GROK_BASE_URL,
            fields=(_region_field(_GROK_ENDPOINTS), _API_KEY_FIELD), endpoints=_GROK_ENDPOINTS,
        ),
        _definition("groq", "Groq", AGENTCLI_GROQ_BASE_URL, include_usage=True,
                    max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST),
        _definition(
            "kimi", "Kimi", AGENTCLI_KIMI_BASE_URL, include_usage=True, reasoning="kimi",
            fields=(_region_field(_KIMI_ENDPOINTS), _API_KEY_FIELD), endpoints=_KIMI_ENDPOINTS,
        ),
        _definition("meta", "Meta", AGENTCLI_META_BASE_URL, reasoning="meta", model_filter="meta"),
        _definition("minimax", "MiniMax", AGENTCLI_MINIMAX_BASE_URL, adapter="anthropic"),
        _definition(
            "mistral", "Mistral AI", AGENTCLI_MISTRAL_BASE_URL, reasoning="mistral", model_filter="mistral",
            fields=(_region_field(_MISTRAL_ENDPOINTS), _API_KEY_FIELD), endpoints=_MISTRAL_ENDPOINTS,
            max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST,
        ),
        _definition("nvidia", "NVIDIA NIM", AGENTCLI_NVIDIA_BASE_URL, reasoning="probed_effort", model_filter="nvidia"),
        _definition(
            "mimo", "Xiaomi MiMo", AGENTCLI_MIMO_BASE_URL, reasoning="mimo", model_filter="mimo",
            fields=(_region_field(_MIMO_ENDPOINTS), _API_KEY_FIELD), endpoints=_MIMO_ENDPOINTS,
        ),
        _definition(
            "tencent", "Tencent Cloud TokenHub", AGENTCLI_TENCENT_SINGAPORE_BASE_URL,
            fields=(_TENCENT_ENDPOINT_FIELD, _API_KEY_FIELD), endpoints=_TENCENT_ENDPOINTS,
            reasoning="probed_effort", model_filter="tencent",
        ),
        _definition("opencode", "OpenCode Zen", AGENTCLI_OPENCODE_BASE_URL, reasoning="probed_effort", model_filter="opencode"),
        _definition("ollama_cloud", "Ollama (cloud)", AGENTCLI_OLLAMA_CLOUD_BASE_URL, include_usage=True, reasoning="ollama", ollama="cloud"),
        _definition("ollama", "Ollama (local)", "", fields=(_OLLAMA_URL_FIELD,), include_usage=True, reasoning="ollama", ollama="local"),
        _definition("openrouter", "OpenRouter", AGENTCLI_OPENROUTER_BASE_URL, include_usage=True, reasoning="probed_effort", model_filter="openrouter"),
        _definition(
            "together", "Together", AGENTCLI_TOGETHER_BASE_URL,
            model_filter="together", models_shape="list",
        ),
        _definition("cerebras", "Cerebras", AGENTCLI_CEREBRAS_BASE_URL),
        _definition(
            "fireworks", "Fireworks", AGENTCLI_FIREWORKS_BASE_URL,
            validation_error="fireworks",
        ),
        _definition(
            "qwen", "Alibaba Cloud Model Studio / Qwen", "",
            fields=(_API_KEY_FIELD, _QWEN_URL_FIELD), include_usage=True,
            reasoning="qwen", validation_error="qwen_openai",
        ),
        _definition("zai", "Z.ai", AGENTCLI_ZAI_BASE_URL, fields=(_ZAI_ENDPOINT_FIELD, _API_KEY_FIELD), endpoints=_ZAI_ENDPOINTS, reasoning="zai",
                    max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST),
    )
}

# Decision accounts share credential storage but cannot enter conversational loops.
PROVIDER_DEFINITIONS["typesafe"] = ProviderDefinition(
    kind="typesafe", label="TypeSafe / JEV", adapter="systemone",
    base_url=DECISION_TYPESAFE_BASE_URL, model=DECISION_TYPESAFE_MODEL,
    fields=(_API_KEY_FIELD,), purpose="decision",
)


def provider_purpose(kind: str) -> str:
    definition = PROVIDER_DEFINITIONS.get(kind)
    return definition.purpose if definition else "unsupported"


# Compatibility projection of the typed registry for tests and internal callers;
# it carries no independent provider facts.
_KINDS: dict[str, dict[str, Any]] = {
    kind: {
        "label": definition.label,
        "keyless": not any(field.id == "api_key" for field in definition.fields),
        "base_url": definition.base_url,
        "model": definition.model,
    }
    for kind, definition in PROVIDER_DEFINITIONS.items()
}


def _provider_catalog(purpose: str = "conversation") -> list[dict[str, Any]]:
    return [
        definition.public()
        for definition in sorted(PROVIDER_DEFINITIONS.values(), key=lambda item: item.label.lower())
        if definition.purpose == purpose
    ]


def _default_base_url(kind: str, endpoint_id: str | None = None) -> str:
    definition = PROVIDER_DEFINITIONS.get(kind)
    if definition is None:
        return ""
    if definition.endpoints:
        selected = endpoint_id or definition.endpoints[0].value
        return next(
            (choice.base_url or "" for choice in definition.endpoints if choice.value == selected),
            "",
        )
    return definition.base_url


def _default_model(kind: str) -> str:
    definition = PROVIDER_DEFINITIONS.get(kind)
    return definition.model if definition else ""


def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _instance_name(inst: dict, has_dupes: bool, instance_id: str) -> str:
    """Disambiguate accounts using server-owned identity or endpoint metadata."""
    kind = inst.get("kind", "")
    label = _KINDS.get(kind, {}).get("label", kind)
    if not has_dupes:
        return label
    if kind == "ollama":
        host = _host_of(inst.get("base_url", ""))
        return f"{label} ({host})" if host else label
    if kind == "qwen":
        host = _host_of(inst.get("base_url", ""))
        return f"{label} ({host})" if host else label
    definition = PROVIDER_DEFINITIONS.get(kind)
    if definition is not None and definition.endpoints:
        endpoints = definition.endpoints
        endpoint_id = str(inst.get("endpoint_id") or endpoints[0].value)
        endpoint = next(
            (choice.label for choice in endpoints if choice.value == endpoint_id),
            endpoint_id,
        )
        return f"{label} ({endpoint})"
    return f"{label} ({instance_id[:8]})"


def _with_learned(inst: dict) -> dict:
    """Declared and probed capabilities, with unexpired learned refusals on top.

    Expiry is applied HERE, on read, rather than by sweeping storage: a refusal
    that has aged out stops being returned, so nothing has to run on a timer and
    a clock change cannot leave a stale record behind. An unparseable timestamp
    is treated as expired, which fails toward offering the control rather than
    toward hiding it.
    """
    caps = {model: dict(vals) for model, vals in (inst.get("capabilities") or {}).items()
            if isinstance(vals, dict)}
    learned = inst.get("learned") or {}
    if not isinstance(learned, dict):
        return caps
    cutoff = utcnow() - timedelta(days=AGENTCLI_LEARNED_REFUSAL_TTL_DAYS)
    for model, options in learned.items():
        if not isinstance(options, dict):
            continue
        for option, at in options.items():
            cap = AGENTCLI_LEARNABLE_OPTIONS.get(option)
            when = parse_datetime(at) if isinstance(at, str) else None
            if cap is None or when is None or when < cutoff:
                continue
            caps.setdefault(model, {})[cap] = False
    return caps


class DuplicateProviderError(ValueError):
    """Raised when an account with the same provider identity already exists."""

    def __str__(self) -> str:
        return "This provider account is already configured."


def _normalized_provider_url(value: object) -> str:
    """Normalize an endpoint URL (Ollama, Qwen) for duplicate detection only."""
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw.rstrip("/")
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return raw.rstrip("/")
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunparse((
        parsed.scheme.lower(), netloc, parsed.path.rstrip("/"),
        parsed.params, parsed.query, parsed.fragment,
    )).rstrip("/")


def _provider_identity(kind: str, cfg: dict) -> tuple[str, str]:
    """Return the identity used to reject exact duplicate accounts.

    The endpoint for Ollama, the credential for most kinds, and endpoint plus
    credential for Qwen, Z.ai and Tencent. Compared in memory only; never stored
    or returned.
    """
    if kind == "ollama":
        return kind, _normalized_provider_url(cfg.get("base_url"))
    if kind == "qwen":
        return kind, "\0".join((
            _normalized_provider_url(cfg.get("base_url")),
            str(cfg.get("api_key") or "").strip(),
        ))
    definition = PROVIDER_DEFINITIONS.get(kind)
    if definition is not None and definition.endpoints:
        return kind, "\0".join((
            str(cfg.get("endpoint_id") or definition.endpoints[0].value),
            str(cfg.get("api_key") or "").strip(),
        ))
    return kind, str(cfg.get("api_key") or "").strip()


class AgentCliStore:
    """Provider accounts ("instances") in a dedicated Store, separate from access keys.

    Shape: {"instances": {"<id>": {"kind": str, "api_key"?: str, "base_url"?: str,
    "model": str}}}. Multiple instances of the same kind are allowed when their
    credentials or Ollama endpoints differ. Keys are write-only; list_instances()
    never returns a secret.
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._data: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    @classmethod
    async def async_create(cls, hass: HomeAssistant) -> AgentCliStore:
        store: Store[dict] = Store(hass, AGENTCLI_SECRETS_STORAGE_VERSION, AGENTCLI_SECRETS_STORAGE_KEY, private=True)
        inst = cls(store)
        raw = await store.async_load() or {}
        if isinstance(raw, dict) and isinstance(raw.get("instances"), dict):
            inst._data = raw["instances"]
        else:
            inst._data = {}
        return inst

    async def _save(self) -> None:
        await self._store.async_save({"instances": self._data})

    def get(self, instance_id: str) -> dict | None:
        cfg = self._data.get(instance_id)
        return dict(cfg) if isinstance(cfg, dict) else None

    def has_duplicate(self, kind: str, cfg: dict) -> bool:
        """Whether the same provider endpoint or credential is already stored."""
        identity = _provider_identity(kind, cfg)
        return bool(identity[1]) and any(
            _provider_identity(inst.get("kind", ""), inst) == identity
            for inst in self._data.values()
            if isinstance(inst, dict)
        )

    async def add(self, kind: str, cfg: dict) -> str:
        async with self._lock:
            if self.has_duplicate(kind, cfg):
                raise DuplicateProviderError
            instance_id = uuid.uuid4().hex
            self._data[instance_id] = {"kind": kind, **cfg}
            await self._save()
            return instance_id

    async def set_model(self, instance_id: str, model: str) -> bool:
        """Change one account's default model. False if the account is unknown.

        An empty model means AUTOMATIC: the account follows the newest model of
        its provider's own catalogue and survives that model being replaced (see
        `model_selection`). That is the default for every new account, because
        a model id goes stale on the PROVIDER's schedule rather than the
        operator's, and re-entering the API key is the wrong cost for that.
        Only the model is patchable. The credential and base_url are what the
        account IS, and re-validating them is what the add flow already does.
        """
        async with self._lock:
            cfg = self._data.get(instance_id)
            if not isinstance(cfg, dict):
                return False
            if cfg.get("model") != model and provider_purpose(cfg.get("kind", "")) == "decision":
                cfg["decision_routing_revision"] = uuid.uuid4().hex
            cfg["model"] = model
            await self._save()
            return True

    async def set_capabilities(
        self, instance_id: str, capabilities: dict, checked_at: str,
    ) -> bool:
        """Record what this account's provider DECLARED, with when it was asked.

        Stored per account rather than per kind because the same kind can differ
        by endpoint: two Ollama servers hold different libraries, and one
        operator's OpenRouter key is entitled to models another's is not.

        The timestamp is half the value. Capabilities have no invalidation signal
        of their own, so "last checked" is what turns a silently ageing answer
        into one the operator can judge.
        """
        async with self._lock:
            cfg = self._data.get(instance_id)
            if not isinstance(cfg, dict):
                return False
            cfg["capabilities"] = capabilities
            cfg["capabilities_checked_at"] = checked_at
            await self._save()
            return True

    async def set_catalog(
        self, instance_id: str, models: list[str], created: dict[str, float], checked_at: str,
        eligible: list[str] | None = None,
    ) -> bool:
        """Cache what the provider currently lists for this account.

        The catalogue is what lets a request pick the newest model, or replace a
        retired one, without a network call. `seen` accumulates every id the
        provider has ever listed for the account; it is what distinguishes a
        retired model (listed before, gone now) from a custom deployment the
        listing never contained, which must be left alone. An empty listing is
        not recorded: it means the provider could not answer, not that every
        model vanished.
        """
        if not models:
            return False
        async with self._lock:
            cfg = self._data.get(instance_id)
            if not isinstance(cfg, dict):
                return False
            seen = [m for m in cfg.get("seen") or [] if isinstance(m, str)]
            seen.extend(m for m in models if m not in seen)
            cfg["seen"] = seen[-_MODEL_SEEN_LIMIT:]
            cfg["catalog"] = {
                "models": list(models),
                "created": {m: created[m] for m in models if m in created},
                "eligible": [m for m in eligible or [] if m in models],
                "checked_at": checked_at,
            }
            await self._save()
            return True

    def _effective(self, cfg: dict) -> tuple[str, str]:
        catalog = _catalog_of(cfg)
        models = [m for m in catalog.get("models") or [] if isinstance(m, str)]
        eligible = [m for m in catalog.get("eligible") or [] if isinstance(m, str)] or list(models)
        # A model a real turn showed cannot call tools is no use to automatic mode.
        caps = _with_learned(cfg)
        eligible = [m for m in eligible if (caps.get(m) or {}).get("tools") is not False]
        return effective_model(
            cfg.get("kind", ""), str(cfg.get("model") or ""), models,
            [m for m in cfg.get("seen") or [] if isinstance(m, str)],
            catalog.get("created") if isinstance(catalog.get("created"), dict) else None,
            eligible or None,
        )

    async def record_refusal(self, instance_id: str, model: str, option: str, at: str) -> bool:
        """Remember that a real turn had this option refused for this model.

        Kept SEPARATE from `capabilities` rather than written into it, and that
        separation is what makes expiry possible at all: declared and probed
        facts are durable statements about the API, while this one is an
        observation from a single moment that has to fade. Merged over the
        durable facts at read time, so nothing here can corrupt them.
        """
        async with self._lock:
            cfg = self._data.get(instance_id)
            if not isinstance(cfg, dict) or not model:
                return False
            learned = dict(cfg.get("learned") or {})
            per_model = dict(learned.get(model) or {})
            per_model[option] = at
            learned[model] = per_model
            cfg["learned"] = learned
            await self._save()
            return True

    async def delete(self, instance_id: str) -> None:
        async with self._lock:
            if instance_id in self._data:
                del self._data[instance_id]
                await self._save()

    async def async_wipe(self) -> None:
        """Delete every provider account, including the recoverable API keys."""
        async with self._lock:
            self._data = {}
            await self._save()

    def list_instances(self) -> list[dict]:
        """Configured accounts for the panel; never includes a key."""
        counts = Counter(inst.get("kind") for inst in self._data.values() if isinstance(inst, dict))
        out = []
        for iid, inst in self._data.items():
            if not isinstance(inst, dict) or inst.get("kind") not in PROVIDER_DEFINITIONS:
                continue
            kind = inst.get("kind", "")
            decision = provider_purpose(kind) == "decision"
            model, replaced = self._effective(inst) if not decision else (inst.get("model", ""), "")
            catalog = _catalog_of(inst)
            out.append({
                "id": iid,
                "kind": kind,
                "name": (f"{PROVIDER_DEFINITIONS[kind].label} ({iid[:8]})"
                         if provider_purpose(kind) == "decision" and counts[kind] > 1
                         else _instance_name(inst, counts[kind] > 1, iid)),
                # The model a request would use now. `model_mode` says whether the
                # operator chose it ("fixed") or the account follows the provider's
                # newest ("auto"); `model_replaced_from` names a chosen model the
                # provider has since retired.
                "model": model,
                "model_mode": "fixed" if decision or inst.get("model") else "auto",
                "model_replaced_from": replaced or None,
                "models_checked_at": catalog.get("checked_at") or None,
                "base_url": inst.get("base_url", ""),
                "endpoint_id": inst.get("endpoint_id") or None,
                "capabilities": _with_learned(inst),
                "capabilities_checked_at": inst.get("capabilities_checked_at") or None,
            })
        # Sort by the DISPLAY name, not the kind key: the panel dropdowns show
        # names, and the two diverge (kind "claude" displays as "Anthropic",
        # "chatgpt" as "OpenAI"), so keying on kind lists them out of order.
        out.sort(key=lambda x: (x["name"].lower(), x["id"]))
        return out

    def resolve(self, instance_id: str, model_override: str | None = None, *,
                purpose: str = "conversation") -> ProviderConfig | None:
        """Build a ProviderConfig for one instance, or None if it is unknown.

        The model may be empty (Ollama is added before a model is chosen); the
        chat endpoint checks for a model separately.
        """
        cfg = self.get(instance_id)
        if cfg is None:
            return None
        kind = cfg.get("kind", "")
        if kind not in PROVIDER_DEFINITIONS or provider_purpose(kind) != purpose:
            return None
        endpoint_id = str(cfg.get("endpoint_id") or "") or None
        base_url = (
            cfg.get("base_url") or _default_base_url(kind, endpoint_id) or ""
        ).rstrip("/")
        if not base_url:
            return None
        if model_override and purpose != "decision":
            # A per-surface or per-request choice (Voice, AI Task, the chat
            # dropdown) follows the same rule as the account's own: kept unless
            # the provider listed it before and has since retired it.
            model = self._effective({**cfg, "model": model_override})[0]
        elif model_override:
            model = model_override
        elif purpose == "decision":
            # A decision account is qualified against exact model ids, so it never
            # follows the provider's newest.
            model = str(cfg.get("model") or "") or _default_model(kind)
        else:
            model = self._effective(cfg)[0]
        model_caps = _with_learned(cfg).get(model) or {}
        vision = model_caps.get("vision") if isinstance(model_caps.get("vision"), bool) else None
        return ProviderConfig(
            kind=kind, model=model, base_url=base_url, api_key=cfg.get("api_key"),
            endpoint_id=endpoint_id, vision=vision,
            decision_routing_revision=str(cfg.get("decision_routing_revision") or "") if purpose == "decision" else "",
        )


async def _get_secret_store(hass: HomeAssistant) -> AgentCliStore:
    """Lazily create and cache the secrets store on hass.data (not on PhoenixData)."""
    key = "_phoenix_agentcli_store"
    inst = hass.data.get(key)
    if isinstance(inst, AgentCliStore):
        return inst
    lock = hass.data.setdefault("_phoenix_agentcli_store_lock", asyncio.Lock())
    async with lock:
        inst = hass.data.get(key)
        if not isinstance(inst, AgentCliStore):
            inst = await AgentCliStore.async_create(hass)
            hass.data[key] = inst
        return inst


def loaded_decision_account(hass: HomeAssistant, instance_id: str) -> ProviderConfig | None:
    """Resolve the current loaded account without loading storage or awaiting."""
    store = hass.data.get("_phoenix_agentcli_store")
    return store.resolve(instance_id, purpose="decision") if isinstance(store, AgentCliStore) else None


def decision_account_fingerprint(hass: HomeAssistant, instance_id: str) -> str | None:
    """Read an already-loaded decision account without exposing credentials in a plan."""
    cfg = loaded_decision_account(hass, instance_id)
    return hashlib.sha256(json.dumps(dataclasses.asdict(cfg), sort_keys=True).encode()).hexdigest() if cfg else None


async def async_wipe_agentcli_secrets(hass: HomeAssistant) -> None:
    """Erase all stored provider accounts and API keys from disk.

    Loads (or reuses the cached) store, so it wipes the on-disk file even when
    Agent Chat was never opened since startup. The cached instance stays live but
    empty, so any open chat window loses its accounts immediately.
    """
    store = await _get_secret_store(hass)
    await store.async_wipe()


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #

def _norm(ev_type: str, **fields: Any) -> dict:
    return {"type": ev_type, **fields}


def _usage_int(value: Any) -> int:
    """A provider-reported token count as a non-negative int; 0 for anything
    absent or malformed. bool is excluded explicitly (it subclasses int)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


# Provider error bodies are JSON {"error": {"message", "type", "code"}} on both
# the Anthropic and OpenAI-compatible surfaces. Variants: Gemini wraps the
# envelope in a one-item list; some backends put a bare string under "error", or
# their code, type and message at the top level (xAI, Cerebras, Mistral); NVIDIA
# has used an RFC 7807 problem body with "detail".
def _error_envelope(text: str) -> dict | None:
    """The error body as a dict, unwrapping Gemini's one-item list; None if not JSON."""
    try:
        obj = json.loads((text or "").strip())
    except (ValueError, TypeError):
        return None
    if isinstance(obj, list) and len(obj) == 1:
        obj = obj[0]
    return obj if isinstance(obj, dict) else None


def _envelope_message(obj: dict | None) -> str:
    """The provider's human sentence from an error envelope, unclipped, or ""."""
    if obj is None:
        return ""
    err = obj.get("error")
    for message in ((err.get("message") if isinstance(err, dict) else err), obj.get("message"), obj.get("detail")):
        if isinstance(message, str) and message.strip():
            return message.strip()
    return ""


def _provider_error_codes(obj: dict | None) -> list[str]:
    """Every machine code an error envelope carries, lowercased, primary first.

    The primary is error.code, else error.type, as before. The rest are where
    some providers put the deciding code: Anthropic's details.error_code,
    OpenRouter's metadata.limit_source and error_type, and top-level fields.
    """
    if obj is None:
        return []
    err: dict = obj["error"] if isinstance(obj.get("error"), dict) else {}
    details: dict = err["details"] if isinstance(err.get("details"), dict) else {}
    metadata: dict = err["metadata"] if isinstance(err.get("metadata"), dict) else {}
    out: list[str] = []
    for value in (err.get("code"), err.get("type"), details.get("error_code"), metadata.get("limit_source"),
                  metadata.get("error_type"), err.get("status"), obj.get("code"), obj.get("type")):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        code = str(value).strip().lower()
        if code and code not in out:
            out.append(code)
    return out


def _parse_provider_error(text: str) -> tuple[str, str | None]:
    """The human sentence (or the raw text) and the primary machine code."""
    obj = _error_envelope(text)
    codes = _provider_error_codes(obj)
    return _envelope_message(obj) or (text or "").strip(), (codes[0] if codes else None)


def _provider_error_detail(text: str) -> str:
    """The provider's own error sentence for an administrator, or "".

    Only the message inside a JSON error envelope qualifies; a proxy's HTML page
    or another unstructured body is not shown. The sentence is clipped and then
    scrubbed like conversation-log text, so a key, bearer token or address a
    provider echoes is redacted. It reaches the admin panel, the conversation
    log and the Home Assistant log, never a model, Voice or AI Task.
    """
    message = _envelope_message(_error_envelope(text))
    if not message:
        return ""
    clipped = " ".join(message.split())[:AGENTCLI_PROVIDER_DETAIL_CHARS]
    try:
        return str(sanitized(clipped, max_bytes=8 * AGENTCLI_PROVIDER_DETAIL_CHARS, hide_ip=True))
    except ValueError:
        return ""


# An ACCOUNT billing refusal: no credit, no balance, a spending limit reached or
# billing not set up. Providers often send it as HTTP 429, but no wait clears it,
# so it is never retried and is shown as a credit problem. Codes mean different
# things at different providers (DashScope uses insufficient_quota for a
# token-rate limit, numeric codes collide), so membership is per provider kind.
# Phrases are a last resort for providers whose code is generic, matched on the
# provider's own sentence; never the bare word "billing", which Gemini's
# ordinary free-tier rate limit also says.
_DEFAULT_BILLING_CODES = frozenset({
    # OpenAI
    "insufficient_quota", "billing_hard_limit_reached", "billing_not_active", "credit_balance_exhausted",
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
    # Anthropic, Meta, Cerebras, MiniMax, Together, Moonshot
    "billing_error", "billing_not_configured", "payment_required", "insufficient_balance_error",
    "credit_limit", "exceeded_current_quota_error",
})
_PROVIDER_BILLING: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "claude": (frozenset({"enforced_spend_limit_reached"}),
               ("credit balance is too low", "reached your specified api usage limits")),
    "minimax": (frozenset(), ("(1008)",)),
    "gemini": (frozenset(), ("prepayment credits are depleted", "exceeded its monthly spending cap")),
    "grok": (frozenset({"personal-team-blocked:spending-limit"}),
             ("used all available credits", "monthly spending limit", "doesn't have any credits")),
    "groq": (frozenset({"blocked_api_access"}), ()),
    "fireworks": (frozenset(), ("is suspended",)),
    "opencode": (frozenset({"creditserror", "monthlylimiterror", "userlimiterror"}), ()),
    "openrouter": (frozenset({"openrouter_credits", "openrouter_key_limit"}), ("key limit exceeded",)),
    "ollama_cloud": (frozenset(), ("requires a subscription",)),
    "zai": (frozenset({"1113", "1309"}), ()),
    "tencent": (frozenset({"403004", "401007", "401008", "failedoperation.servicestoparrears",
                           "failedoperation.resourcepackexhausted", "failedoperation.freeresourcepackexhausted"}), ()),
}
# DashScope replaces the default list: its insufficient_quota is a rate limit.
_QWEN_BILLING = (frozenset({"arrearage", "allocationquota.freetieronly", "prepaidbilloverdue",
                            "postpaidbilloverdue", "budgetlimitexceeded"}), ("free allocated quota exceeded",))
# OpenRouter's one temporary 402: requests already in flight hold the budget.
_OPENROUTER_IN_FLIGHT = "openrouter_in_flight_budget"


def _billing_refusal(kind: str, status: int, codes: list[str], message: str) -> bool:
    """Whether this provider answer is an account billing refusal (see _DEFAULT_BILLING_CODES)."""
    if kind == "openrouter" and _OPENROUTER_IN_FLIGHT in codes:
        return False
    if status == 402:
        return True
    default = frozenset() if kind == "qwen" else _DEFAULT_BILLING_CODES
    extra, phrases = _QWEN_BILLING if kind == "qwen" else _PROVIDER_BILLING.get(kind, (frozenset(), ()))
    if any(code in default or code in extra for code in codes):
        return True
    lowered = message.lower()
    return any(phrase in lowered for phrase in phrases)


def _http_failure_code(status: int, server_error: str = "upstream") -> str:
    """Classify a non-200 provider status for the panel and retry policy."""
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status == 402:
        return "quota"
    if 400 <= status < 500:
        # The provider refused this request as sent. Not an outage, and the
        # same request will be refused again.
        return "provider_rejected"
    return server_error


def _http_failure(kind: str, status: int, text: str, *, code: str, retryable: bool, **extra: Any) -> dict:
    """One EV_ERROR for a non-200 provider answer, with the administrator-only detail.

    Billing refusals override the status classification (_billing_refusal). The
    primary machine code is kept as provider_code when identifier-shaped, so the
    log shows how a refusal was classified without carrying provider prose.
    """
    envelope = _error_envelope(text)
    codes = _provider_error_codes(envelope)
    if _billing_refusal(kind, status, codes, _envelope_message(envelope)):
        code, retryable = "quota", False
    elif kind == "openrouter" and _OPENROUTER_IN_FLIGHT in codes:
        code, retryable = "rate_limit", True
    elif codes and codes[0] in _NON_RETRYABLE_ERROR_CODES:
        retryable = False
    detail = _provider_error_detail(text)
    machine = next((c for c in codes if re.fullmatch(r"[a-z0-9_.:-]{1,64}", c)), None)
    _LOGGER.log(
        logging.DEBUG if retryable else logging.WARNING,
        "Agent provider %s answered HTTP %s (%s): %s", kind, status, machine or "no code", detail or "no error message",
    )
    return _norm(EV_ERROR, status=status, code=code, message=f"Provider request failed (HTTP {status}).",
                 retryable=retryable, **({"provider_detail": detail} if detail else {}),
                 **({"provider_code": machine} if machine else {}), **extra)


# Mistral documents a remaining-quota response header and currently publishes
# more granular numeric headers for token/request windows. Keep this an explicit
# allowlist: response headers are provider-controlled input, and no unrelated
# header should ever reach the operator or logs through an error event.
_MISTRAL_RATE_LIMIT_HEADER_FIELDS: tuple[tuple[str, str], ...] = (
    ("x-ratelimit-limit-tokens-minute", "tokens_minute_limit"),
    ("x-ratelimit-remaining-tokens-minute", "tokens_minute_remaining"),
    ("x-ratelimit-limit-tokens-5-minute", "tokens_five_minute_limit"),
    ("x-ratelimit-remaining-tokens-5-minute", "tokens_five_minute_remaining"),
    ("x-ratelimit-limit-tokens-month", "tokens_month_limit"),
    ("x-ratelimit-remaining-tokens-month", "tokens_month_remaining"),
    ("x-ratelimit-limit-req-minute", "requests_minute_limit"),
    ("x-ratelimit-limit-requests-minute", "requests_minute_limit"),
    ("x-ratelimit-remaining-req-minute", "requests_minute_remaining"),
    ("x-ratelimit-remaining-requests-minute", "requests_minute_remaining"),
    ("x-ratelimit-limit-req-second", "requests_second_limit"),
    ("x-ratelimit-limit-requests-second", "requests_second_limit"),
    ("x-ratelimit-remaining-req-second", "requests_second_remaining"),
    ("x-ratelimit-remaining-requests-second", "requests_second_remaining"),
    ("x-ratelimit-limit", "generic_limit"),
    ("x-ratelimit-remaining", "generic_remaining"),
    ("x-ratelimit-tokens-query-cost", "query_cost"),
    ("retry-after", "retry_after_seconds"),
)
# Retry-After (RFC 9110) means the same thing from every provider. Other
# providers' x-ratelimit-* headers count different windows under similar names,
# so only Mistral's documented set is read. Only the delay-seconds form is
# accepted; an HTTP-date value is dropped rather than converted.
_RETRY_AFTER_HEADER_FIELDS: tuple[tuple[str, str], ...] = (
    ("retry-after", "retry_after_seconds"),
)


def _rate_limit_headers(headers: Any, fields: tuple[tuple[str, str], ...]) -> dict[str, int]:
    """Safe numeric quota diagnostics from one HTTP 429 response."""
    try:
        raw = {str(name).lower(): str(value).strip() for name, value in headers.items()}
    except (AttributeError, TypeError, ValueError):
        return {}
    out: dict[str, int] = {}
    for header, field in fields:
        value = raw.get(header)
        if value is None or not value.isascii() or not value.isdecimal():
            continue
        parsed = int(value)
        # Preserve exact integers in the browser rather than silently rounding
        # an absurd/malformed provider value during JSON-to-JavaScript parsing.
        if parsed <= 9_007_199_254_740_991:
            out.setdefault(field, parsed)
    return out


# Account and request problems retrying cannot change, besides billing (see
# _billing_refusal), so surface them immediately instead of spending the
# connect-retry window (see _stream_turn_resilient) hammering them.
_NON_RETRYABLE_ERROR_CODES = frozenset({
    "account_deactivated", "access_terminated", "invalid_api_key",
    # Anthropic error types that retrying cannot change.
    "request_too_large", "permission_error", "not_found_error",
})
# Anthropic error types that name a transient condition, whether they arrive as an
# HTTP status or as an `error` event after a 200 stream has already started.
_CLAUDE_TRANSIENT_ERRORS = frozenset({
    "overloaded_error", "api_error", "timeout_error", "rate_limit_error",
})
# Claude models that rejected adaptive thinking (older models take an enabled
# thinking budget instead). Process-local; a restart re-learns it with one retry.
_CLAUDE_NO_ADAPTIVE: set[str] = set()


async def _retry_transient(attempt: Callable[[], Awaitable[Any]]) -> Any:
    """Await attempt() with a bounded retry for a transient connection failure.

    A status-based result (whatever attempt() returns) is passed straight back, so
    an auth rejection or a real HTTP error is never retried; only a
    ClientError/TimeoutError is retried, briefly, and the last one is re-raised
    after the final try for the caller to turn into a friendly message. Used by the
    provider validate() probes so a one-off DNS/connect blip does not reject a good
    credential during the Settings Validate step.
    """
    last: Exception | None = None
    for i in range(_VALIDATE_ATTEMPTS):
        try:
            return await attempt()
        except (ClientError, asyncio.TimeoutError) as exc:
            last = exc
            if i + 1 < _VALIDATE_ATTEMPTS:
                await asyncio.sleep(_VALIDATE_RETRY_BACKOFF_SECONDS)
    assert last is not None
    raise last


async def _sse_lines(resp: Any) -> AsyncIterator[str]:
    """Yield decoded lines from a streaming HTTP response, buffering split frames.

    Two byte bounds guard against a faulty or hostile provider (memory
    exhaustion). The between-newline buffer is capped so a stream that never
    sends a newline cannot grow unbounded; the CUMULATIVE bytes for the whole
    response are also capped, so a stream of unlimited small newline-delimited
    frames (which downstream parsers append into text blocks and tool JSON) is
    bounded too. A real response is well under both. Exceeding either raises
    ClientError, which each provider's stream_turn turns into a clean chat error.
    The per-response cap bounds a turn at cap x AGENTCLI_MAX_ITERATIONS.
    """
    buffer = ""
    total = 0
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    async for chunk in resp.content.iter_chunked(2048):
        total += len(chunk)
        if total > AGENTCLI_MAX_STREAM_BYTES:
            raise ClientError("Provider stream exceeded the maximum total size.")
        buffer += decoder.decode(chunk)
        if len(buffer) > AGENTCLI_MAX_SSE_BUFFER_BYTES:
            raise ClientError("Provider stream frame exceeded the maximum size.")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line.rstrip("\r")
    buffer += decoder.decode(b"", final=True)
    if buffer:
        yield buffer.rstrip("\r")


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


class _ProviderResponseError(ValueError):
    """A bounded, content-free provider failure classification."""

    def __init__(self, code: str = "provider_malformed", *, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _provider_failure(code: str, *, retryable: bool = True) -> dict:
    messages = {
        "provider_malformed": "The provider returned a malformed response. No tool from this response was run.",
        "provider_incomplete": "The provider response ended without a complete message. No tool from this response was run.",
        "provider_blocked": "The provider blocked this response. It will not be retried.",
        "provider_transient": "The provider is temporarily unavailable.",
    }
    return _norm(EV_ERROR, status=200, code=code, message=messages[code],
                 retryable=retryable, diagnostic={"category": code})


def _total_provider_response(parse: Callable) -> Callable:
    """Keep malformed provider data out of tracebacks and tool dispatch."""
    @functools.wraps(parse)
    async def guarded(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        try:
            async for event in parse(*args, **kwargs):
                yield event
        except _ProviderResponseError as exc:
            yield _provider_failure(exc.code, retryable=exc.retryable)
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError):
            # No exception text: it can contain arbitrary provider output.
            yield _provider_failure("provider_malformed")
    return guarded


def _provider_object(payload: str) -> dict:
    obj = json.loads(payload)
    if not isinstance(obj, dict):
        raise _ProviderResponseError()
    feedback = obj.get("promptFeedback")
    error = obj.get("error")
    if (isinstance(feedback, dict) and feedback.get("blockReason")) or (
        isinstance(error, dict) and str(error.get("code") or error.get("type")) in
        {*_NON_RETRYABLE_ERROR_CODES, *_DEFAULT_BILLING_CODES, "content_filter", "content_policy_violation"}
    ):
        raise _ProviderResponseError("provider_blocked", retryable=False)
    if isinstance(error, dict) and str(error.get("code") or error.get("type")) in {
        "overloaded_error", "rate_limit_error", "server_error", "internal_server_error",
        "rate_limit_exceeded", "429", "500", "503", "529",
    }:
        raise _ProviderResponseError("provider_transient")
    if error:
        # Unknown HTTP-200 errors are deliberate upstream refusals, not a reason
        # to replay a request. The content itself must never become diagnostics.
        raise _ProviderResponseError("provider_blocked", retryable=False)
    return obj


def _valid_tool_call(call_id: Any, name: Any, arguments: Any) -> bool:
    return (
        isinstance(call_id, str) and 0 < len(call_id) <= 256
        and isinstance(name, str) and 0 < len(name) <= MAX_TOOL_NAME_LENGTH
        and re.fullmatch(r"[A-Za-z0-9_.:-]+", name) is not None
        and isinstance(arguments, dict)
    )


def _tool_call_problem(call_id: Any, name: Any, arguments: Any) -> str:
    """Explain the same structural checks without echoing model content."""
    if not isinstance(call_id, str) or not 0 < len(call_id) <= 256:
        return "The tool call ID is missing or invalid."
    if (not isinstance(name, str) or not 0 < len(name) <= MAX_TOOL_NAME_LENGTH
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", name) is None):
        return "The tool name is missing or invalid."
    return "Tool arguments must be a JSON object."


def _json_error_hint(raw: str, error: json.JSONDecodeError) -> str:
    """Tell the model where its tool arguments stopped parsing and what is likely missing.

    The received text is scanned up to the error position, string-aware, so the
    objects and arrays still open there are known: the innermost one names the
    closer the parser was waiting for, and the key it is the value of. No string
    VALUE is quoted: the text returns to the model and the browser transcript,
    and a value can hold a secret the model wrote.
    """
    pos = max(0, min(error.pos, len(raw)))
    # (opener, index, key it is the value of), outermost first.
    stack: list[tuple[str, int, str | None]] = []
    in_string = escaped = False
    current: list[str] = []
    last_string: str | None = None
    key: str | None = None
    for index, char in enumerate(raw[:pos]):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                last_string = "".join(current)
                continue
            current.append(char)
        elif char == '"':
            in_string, current, key = True, [], None
        elif char == ":":
            key = last_string
        elif char in "{[":
            stack.append((char, index, key))
            key = None
        elif char in "}],":
            if char != "," and stack:
                stack.pop()
            key = None
    closers = "".join("}" if opener == "{" else "]" for opener, _, _ in reversed(stack))
    if error.msg.startswith("Unterminated string") or in_string:
        hint = "A string opened here is never closed, so a closing quote is probably missing."
    elif stack and not raw[pos:].strip():
        noun = "object or array" if len(stack) == 1 else "objects or arrays"
        hint = f"The arguments end with {len(stack)} unclosed {noun}, so '{closers}' is probably missing at the end."
    elif stack:
        opener, start, owner = stack[-1]
        kind = "object" if opener == "{" else "array"
        named = f" (the value of {json.dumps(owner[:64], ensure_ascii=False)})" if owner else ""
        hint = (f"At that point the innermost open {kind}{named} starts at character {start}, "
                f"so '{closers[0]}' is probably missing before this position.")
    else:
        hint = "The JSON value is already complete before that point, so the text after it is extra."
    return (
        f"Not run: the arguments for this call are not valid JSON ({error.msg} at line {error.lineno}, "
        f"column {error.colno}, character {pos} of {len(raw)}). {hint} The arguments are shown as {{}} "
        "in this history only because they could not be parsed; nothing was run with them. Send the "
        "call again with complete, valid JSON arguments."
    )


_INVALID_BATCH_SIBLING = (
    "Not run: another tool call in the same response had arguments that are not valid JSON, so no "
    "call from that response ran. Send this call again if it is still needed."
)


def _invalid_tool_calls(
    calls: list[tuple[Any, Any, Any, str | None]], assistant_msg: dict,
) -> dict | None:
    """Build the EV_INVALID_TOOLS event for one response, or None when it cannot be paired.

    `calls` holds (id, name, parsed arguments or None, model-facing error or
    None) in response order. Every call gets a tool error, the valid ones
    included, because no call from a response with a malformed sibling runs. A
    call whose ID or name is unusable cannot carry a tool result, so the response
    stays a terminal error rather than a correction.
    """
    ids = [call_id for call_id, _name, _args, _error in calls]
    if not calls or len(set(ids)) != len(ids) or not all(
            _valid_tool_call(call_id, name, {}) for call_id, name, _args, _error in calls):
        return None
    results = [
        {"tool_use_id": call_id, "tool_name": name, "result_text": error or _INVALID_BATCH_SIBLING,
         "is_error": True}
        for call_id, name, _args, error in calls
    ]
    return _norm(EV_INVALID_TOOLS, code="bad_tool_call", assistant_msg=assistant_msg, results=results)


def _merged_whole_arguments(current: str, fragment: str) -> str:
    """Merge one Gemini tool-call arguments fragment into what the call has so far.

    Gemini's OpenAI-compatible stream sends a call's arguments as COMPLETE JSON,
    more than once: `{}` first, then the full object, for the same call. The
    OpenAI rule of concatenating fragments turns that into `{}{...}`. A fragment
    that is a complete object replaces a complete object, except that `{}` never
    replaces real arguments; anything else is appended, so genuine deltas still
    concatenate.
    """
    if not current:
        return fragment
    try:
        old, new = json.loads(current), json.loads(fragment)
    except ValueError:
        return current + fragment
    if isinstance(old, dict) and isinstance(new, dict):
        return current if old and not new else fragment
    return current + fragment


def _gemini_tool_slot(calls: dict[int, dict], fragment: dict) -> int:
    """Pick the call a Gemini fragment belongs to: by call ID first, then its index.

    Gemini can omit `index` or repeat it across parallel calls, so the ID is the
    reliable key; an ID-less fragment continues the latest call.
    """
    call_id = fragment.get("id")
    if call_id:
        for idx, slot in calls.items():
            if slot["id"] == call_id:
                return idx
    given = fragment.get("index")
    if type(given) is int and given >= 0 and (given not in calls or not call_id or not calls[given]["id"]):
        return given
    if call_id or not calls:
        return max(calls, default=-1) + 1
    return max(calls)


def _tool_limit_problem(provider: Any, tools: list) -> dict | None:
    """The refusal for a catalog larger than this provider accepts, or None.

    Checked before the first model call: past a documented tools-array cap the
    provider answers HTTP 400 to every request, and trimming the catalog would
    hide capabilities without saying so.
    """
    definition = PROVIDER_DEFINITIONS.get(str(getattr(provider, "kind", "")))
    limit = definition.max_tools if definition is not None else None
    if definition is None or limit is None or len(tools) <= limit:
        return None
    params = {"count": len(tools), "limit": limit, "provider": definition.label}
    return {
        "code": "too_many_tools", "retryable": False, "message_params": params,
        "message": (f"This access key announces {params['count']} tools, but {params['provider']} accepts "
                    f"at most {limit} in one request. Narrow the access key's capabilities or choose "
                    "another provider."),
    }


def _invalid_tools_error(message: str = "The model produced an invalid tool call.") -> dict:
    """The terminal form of EV_INVALID_TOOLS, for a loop that cannot hand it back."""
    return _norm(EV_ERROR, status=0, code="bad_tool_call", message=message, retryable=True,
                 diagnostic={"category": "bad_tool_call"})


class _ThinkSplitter:
    """Split a streamed content string into ("text"|"think", segment) pieces.

    Handles <think>...</think> blocks whose tags may be split across streamed
    chunks by holding back a short tail that could be the start of a tag.
    """

    def __init__(self) -> None:
        self._in_think = False
        self._carry = ""

    def _max_tail(self) -> int:
        return len(_THINK_CLOSE if self._in_think else _THINK_OPEN) - 1

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._carry += chunk
        while self._carry:
            tag = _THINK_CLOSE if self._in_think else _THINK_OPEN
            kind = "think" if self._in_think else "text"
            idx = self._carry.find(tag)
            if idx == -1:
                # Emit everything except a possible partial tag at the tail.
                keep = self._max_tail()
                if len(self._carry) > keep:
                    seg = self._carry[:len(self._carry) - keep]
                    if seg:
                        out.append((kind, seg))
                    self._carry = self._carry[len(self._carry) - keep:]
                break
            if idx > 0:
                out.append((kind, self._carry[:idx]))
            self._carry = self._carry[idx + len(tag):]
            self._in_think = not self._in_think
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self._carry:
            out.append(("think" if self._in_think else "text", self._carry))
            self._carry = ""
        return out


def _apply_claude_options(body: dict, options: dict, kind: str = "claude") -> None:
    """Map the panel's generation options into an Anthropic-format request body.

    Claude takes the full surface (adaptive thinking with a display mode plus an
    output_config effort). MiniMax's Anthropic-compatible API turns thinking on
    via `{type: adaptive}` and takes `output_config.effort` only on its newest
    model; the effort is not sent, and a model that always thinks simply
    ignores the toggle.
    """
    body["max_tokens"] = int(options.get("max_tokens") or AGENTCLI_DEFAULT_MAX_TOKENS)
    thinking = options.get("thinking", True)
    if thinking:
        if kind == "minimax":
            body["thinking"] = {"type": "adaptive"}
        else:
            # display=summarized streams a readable summary the panel can show;
            # omitted still lets the model think but streams no thinking text,
            # which is what to request when the operator has "show thinking" off.
            display = "summarized" if options.get("show_thinking") else "omitted"
            body["thinking"] = {"type": "adaptive", "display": display}
    if kind == "claude":
        effort = options.get("effort", AGENTCLI_DEFAULT_EFFORT)
        if effort in AGENTCLI_EFFORT_LEVELS:
            body["output_config"] = {"effort": effort}


def _thinking_type(body: dict) -> str:
    """The `type` of a request body's thinking object, or "" when there is none."""
    thinking = body.get("thinking")
    return str(thinking.get("type") or "") if isinstance(thinking, dict) else ""


def _use_enabled_thinking(body: dict) -> None:
    """Switch a request from adaptive thinking to a fixed enabled budget.

    For models that do not accept adaptive thinking. The budget must stay below
    max_tokens and at least 1024; a request too small to hold one runs without
    thinking rather than failing. Effort is dropped: those models do not take it.
    """
    thinking = body.get("thinking")
    if not isinstance(thinking, dict) or thinking.get("type") != "adaptive":
        return
    budget = min(int(body.get("max_tokens") or 0) // 2, 16000)
    if budget >= 1024:
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    else:
        body.pop("thinking", None)
    body.pop("output_config", None)


def _cache_marked(system_prompt: str, messages: list[dict]) -> tuple[Any, list[dict]]:
    """Add Anthropic prompt-cache breakpoints (Claude only, never MiniMax).

    Two ephemeral markers: one on the system block, which caches the stable
    prefix (the tools array plus the system prompt, both large and unchanged
    across turns unless the access key's caps change), and one on the last message
    block, so each agentic-loop iteration and each later turn reuses the prior
    message history instead of re-billing it. Markers go on copies only; the
    stored transcript round-trips through the browser and must stay clean.
    """
    marker = {"cache_control": {"type": "ephemeral"}}
    system = [{"type": "text", "text": system_prompt, **marker}]
    if not messages:
        return system, messages
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str) and content:
        marked = {**last, "content": [{"type": "text", "text": content, **marker}]}
    elif isinstance(content, list) and content:
        marked = {**last, "content": [*content[:-1], {**content[-1], **marker}]}
    else:
        return system, messages
    return system, [*messages[:-1], marked]


_SCHEMA_COMBINATORS = ("anyOf", "oneOf", "allOf")


def _requirement_sentence(keyword: str, branches: list) -> str:
    """State one top-level anyOf/oneOf of required-property branches as a sentence."""
    groups = []
    for branch in branches:
        required = branch.get("required") if isinstance(branch, dict) else None
        if not isinstance(required, list) or not required:
            return ("The arguments must match exactly one of the documented alternatives."
                    if keyword == "oneOf" else
                    "The arguments must match at least one of the documented alternatives.")
        names = " and ".join(str(name) for name in required)
        groups.append(f"({names})" if len(required) > 1 else names)
    listed = groups[0] if len(groups) == 1 else ", ".join(groups[:-1]) + f" or {groups[-1]}"
    return f"Provide {'exactly one' if keyword == 'oneOf' else 'at least one'} of: {listed}."


def _lowered_input_schema(schema: Any) -> tuple[Any, list[str]]:
    """Lower the top-level combinators that several provider APIs refuse.

    Anthropic, and OpenAI even without strict mode, answer HTTP 400 to any
    request whose tool schema has anyOf, oneOf or allOf at the top level, so
    one such tool fails every request; gateways forward the schema unchanged.
    allOf members merge into the object (properties and required lists); each
    anyOf or oneOf becomes a sentence for the tool description. Nested
    combinators stay. Each tool's handler enforces these rules at dispatch, so
    no check is relaxed.
    """
    if not isinstance(schema, dict) or not any(key in schema for key in _SCHEMA_COMBINATORS):
        return schema, []
    lowered = {key: value for key, value in schema.items() if key not in _SCHEMA_COMBINATORS}
    properties = dict(lowered.get("properties") or {})
    required = list(lowered.get("required") or [])
    rules: list[str] = []
    members = [schema, *(member for member in schema.get("allOf") or [] if isinstance(member, dict))]
    for member in members:
        if member is not schema:
            for name, spec in (member.get("properties") or {}).items():
                properties.setdefault(name, spec)
            required += [name for name in member.get("required") or [] if name not in required]
        for keyword in ("anyOf", "oneOf"):
            branches = member.get(keyword)
            if not isinstance(branches, list) or not branches:
                continue
            for branch in branches:
                for name, spec in ((branch.get("properties") or {}) if isinstance(branch, dict) else {}).items():
                    properties.setdefault(name, spec)
            rules.append(_requirement_sentence(keyword, branches))
    lowered["type"] = "object"
    lowered["properties"] = properties
    if required:
        lowered["required"] = required
    return lowered, rules


def _explicitly_open(schema: Any, *, nested: bool = False) -> Any:
    """Copy a schema with every nested object that leaves extra keys open marked so.

    JSON Schema leaves an object without additionalProperties open, but xAI
    applies strict rules that close it, and Kimi's default strict dialect may,
    which would leave a model able to send only `{}` for service data or an
    automation config. The explicit `true` keeps the meaning everywhere. Type
    lists are left alone: Kimi refuses any keyword beside one. The root is
    never touched (every tool root is closed). Unchanged schemas are returned
    as the same object.
    """
    if not isinstance(schema, dict):
        return schema
    changed = False
    out = dict(schema)
    for key in ("properties", "patternProperties"):
        if isinstance(schema.get(key), dict):
            mapped = {name: _explicitly_open(sub, nested=True) for name, sub in schema[key].items()}
            if any(mapped[name] is not schema[key][name] for name in mapped):
                out[key], changed = mapped, True
    for key in ("items", "additionalProperties", "not"):
        if isinstance(schema.get(key), dict):
            sub = _explicitly_open(schema[key], nested=True)
            if sub is not schema[key]:
                out[key], changed = sub, True
    for key in _SCHEMA_COMBINATORS:
        if isinstance(schema.get(key), list):
            mapped_list = [_explicitly_open(sub, nested=nested) for sub in schema[key]]
            if any(new is not old for new, old in zip(mapped_list, schema[key])):
                out[key], changed = mapped_list, True
    if nested and schema.get("type") == "object" and "additionalProperties" not in schema:
        out["additionalProperties"], changed = True, True
    return out if changed else schema


def _provider_tool(tool: dict) -> tuple[str, str, Any]:
    """Name, description and input schema as every provider adapter sends them.

    The published MCP schema is unchanged; only this copy is adapted to what
    provider APIs accept (see _lowered_input_schema and _explicitly_open).
    """
    schema, rules = _lowered_input_schema(tool.get("inputSchema", {"type": "object", "properties": {}}))
    schema = _explicitly_open(schema)
    description = tool.get("description", "")
    if rules:
        description = str(description).rstrip()
        if description and description[-1] not in ".!?":
            description += "."
        description = " ".join([description, *rules]).strip()
    return tool["name"], description, schema


class ClaudeProvider:
    """Anthropic Messages API, streaming, over raw HTTP.

    Also backs MiniMax, whose API is Anthropic-compatible; kind-specific bits
    (auth header, thinking options, model listing) branch on self.kind.
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self.kind = cfg.kind
        # Filled by list_models: creation time per listed id, for model selection.
        self.model_created: dict[str, float] = {}
        self._model_rows: dict[str, dict] = {}

    def format_tools(self, mcp_tools: list[dict]) -> list[dict]:
        out = []
        for t in mcp_tools:
            name, description, schema = _provider_tool(t)
            out.append({"name": name, "description": description, "input_schema": schema})
        return out

    def user_message(self, text: str, images: list[dict[str, str]]) -> dict:
        """The administrator's message with attached images first, as Anthropic recommends."""
        content: list[dict] = [{
            "type": "image",
            "source": {"type": "base64", "media_type": image["mime_type"], "data": image["data"]},
        } for image in images]
        if text:
            content.append({"type": "text", "text": text})
        return {"role": "user", "content": content}

    def append_assistant(self, messages: list[dict], assistant_msg: dict) -> None:
        messages.append(assistant_msg)

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:
        # All tool_result blocks for a turn go in ONE user message.
        content = []
        for r in results:
            result_text = _model_result_text(r, self.cfg.vision is True)
            result_content: list[dict] = [{"type": "text", "text": result_text}]
            if self.cfg.vision is True:
                for image in r.get("images", []):
                    result_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image["mime_type"],
                            "data": image["data"],
                        },
                    })
            block: dict = {
                "type": "tool_result",
                "tool_use_id": r["tool_use_id"],
                "content": result_content,
            }
            if r.get("is_error"):
                block["is_error"] = True
            content.append(block)
        messages.append({"role": "user", "content": content})

    async def validate(self, session: ClientSession) -> tuple[bool, str]:
        async def _probe() -> tuple[bool, str]:
            async with session.get(
                f"{self.cfg.base_url}/v1/models",
                headers=self._headers(), timeout=_PROBE_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                if resp.status in (401, 403):
                    return False, "API key rejected."
                if self.kind == "minimax":
                    # MiniMax's Anthropic-compatible base may not expose /v1/models;
                    # a non-auth error there does not mean the key is bad. Soft-accept
                    # (a real problem surfaces on the first message with a clean error).
                    return True, ""
                return False, f"Provider returned HTTP {resp.status}."
        try:
            return await _retry_transient(_probe)
        except ValueError:
            return False, "Invalid provider connection settings."
        except (ClientError, asyncio.TimeoutError) as exc:
            return False, f"Could not reach the provider: {exc}"

    async def list_models(self, session: ClientSession) -> list[str]:
        fallback = list(AGENTCLI_MINIMAX_MODELS) if self.kind == "minimax" \
            else ([self.cfg.model] if self.cfg.model else [])
        rows: list[dict] = []
        after_id = ""
        try:
            # Anthropic pages /v1/models (default 20 per page), so follow
            # `has_more` or an account with many models silently loses the rest.
            for _ in range(_MODEL_LIST_MAX_PAGES):
                params = {"limit": "1000", **({"after_id": after_id} if after_id else {})}
                async with session.get(
                    f"{self.cfg.base_url}/v1/models", headers=self._headers(),
                    params=params, timeout=_PROBE_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        break
                    body = await resp.json(content_type=None)
                if not isinstance(body, dict):
                    break
                rows.extend(r for r in body.get("data", []) if isinstance(r, dict))
                after_id = str(body.get("last_id") or "")
                if not body.get("has_more") or not after_id:
                    break
        except (ClientError, asyncio.TimeoutError):
            pass
        rows = [row for row in rows if not _is_expired(row)]
        self.model_created = _created_map(rows)
        self._model_rows = {m["id"]: m for m in rows if m.get("id")}
        return [m["id"] for m in rows if m.get("id")] or fallback

    def _headers(self) -> dict:
        # MiniMax's Anthropic-compatible API authenticates with a Bearer token
        # rather than Anthropic's x-api-key; both send the anthropic-version.
        if self.kind == "minimax":
            return {
                "Authorization": f"Bearer {self.cfg.api_key or ''}",
                "anthropic-version": AGENTCLI_ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
        return {
            "x-api-key": self.cfg.api_key or "",
            "anthropic-version": AGENTCLI_ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    async def probe_image(self, session: ClientSession, image: str, question: str) -> tuple[int | None, str]:
        """Status and reply text for one question about an attached PNG (see async_probe_vision)."""
        body = {"model": self.cfg.model, "max_tokens": AGENTCLI_PROBE_IMAGE_MAX_TOKENS,
                "messages": [self.user_message(question, [{"mime_type": "image/png", "data": image}])]}
        try:
            async with session.post(f"{self.cfg.base_url}/v1/messages", headers=self._headers(), json=body,
                                    timeout=_IMAGE_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    return resp.status, _parse_provider_error((await resp.text())[:4000])[0]
                data = await resp.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None, ""
        blocks = data.get("content") if isinstance(data, dict) else None
        if not isinstance(blocks, list):
            return 200, ""
        return 200, "".join(block["text"] for block in blocks
                            if isinstance(block, dict) and block.get("type") == "text"
                            and isinstance(block.get("text"), str))

    async def probe_option(self, session: ClientSession, extra: dict) -> int | None:
        """HTTP status for the smallest possible completion carrying `extra`.

        None when the provider could not be reached at all, which is not an
        answer about the option and must not be recorded as one.
        """
        body = {"model": self.cfg.model, "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}], **extra}
        try:
            async with session.post(f"{self.cfg.base_url}/v1/messages", headers=self._headers(),
                                    json=body, timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                return resp.status
        except (ClientError, asyncio.TimeoutError):
            return None

    async def list_model_capabilities(
        self, session: ClientSession, models: list[str],
    ) -> dict[str, dict]:
        """Per-model capabilities from the `capabilities` object on each model row.

        Anthropic reports image input, thinking and the effort levels each model
        accepts, so they are DECLARED rather than probed. MiniMax rows carry no
        capabilities and yield nothing.
        """
        out: dict[str, dict] = {}
        for model in models:
            declared = (self._model_rows.get(model) or {}).get("capabilities")
            if not isinstance(declared, dict):
                continue
            caps: dict[str, Any] = {}
            vision = (declared.get("image_input") or {}).get("supported")
            if isinstance(vision, bool):
                caps["vision"] = vision
            thinking = (declared.get("thinking") or {}).get("supported")
            if isinstance(thinking, bool):
                caps["thinking"] = thinking
            effort = declared.get("effort") or {}
            if isinstance(effort, dict) and effort.get("supported") is True:
                caps["effort_levels"] = [
                    level for level in AGENTCLI_EFFORT_LEVEL_ORDER
                    if (effort.get(level) or {}).get("supported") is True
                ]
            if caps:
                out[model] = caps
        return out

    async def stream_turn(
        self, session: ClientSession, *, system_prompt: str, messages: list[dict],
        tools: list[dict], options: dict,
    ) -> AsyncIterator[dict]:
        # Prompt caching is Anthropic-specific; MiniMax's compatible API is not
        # verified to accept cache_control, so it gets the plain uncached body.
        system, messages = (
            _cache_marked(system_prompt, messages) if self.kind == "claude"
            else (system_prompt, messages)
        )
        body = {
            "model": self.cfg.model,
            "system": system,
            "messages": messages,
            "tools": tools,
            "stream": True,
        }
        _apply_claude_options(body, options, self.kind)
        if self.cfg.model in _CLAUDE_NO_ADAPTIVE:
            _use_enabled_thinking(body)
        try:
            for attempt in range(2):
                async with session.post(
                    f"{self.cfg.base_url}/v1/messages", headers=self._headers(),
                    json=body, timeout=_CHAT_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        text = (await resp.text())[:4000]
                        msg = _parse_provider_error(text)[0]
                        if (attempt == 0 and resp.status == 400
                                and _thinking_type(body) == "adaptive"
                                and "adaptive" in msg.lower()):
                            # An older model (e.g. Haiku 4.5) takes an enabled
                            # thinking budget, not adaptive thinking.
                            _CLAUDE_NO_ADAPTIVE.add(self.cfg.model)
                            _use_enabled_thinking(body)
                            continue
                        rate_limit = (_rate_limit_headers(resp.headers, _RETRY_AFTER_HEADER_FIELDS)
                                      if resp.status == 429 else {})
                        yield _http_failure(self.kind, resp.status, text, code=_http_failure_code(resp.status),
                                            retryable=resp.status in (429, 500, 502, 503, 504, 529),
                                            refused=_refused_option(resp.status, msg, body),
                                            **({"rate_limit": rate_limit} if rate_limit else {}))
                        return
                    async for ev in self._parse(resp):
                        yield ev
                    return
        except (ClientError, asyncio.TimeoutError):
            yield _norm(EV_ERROR, status=0, code="network", message="Provider connection failed.", retryable=True)

    @_total_provider_response
    async def _parse(self, resp: Any) -> AsyncIterator[dict]:
        # Assemble content blocks by index into an assistant message.
        blocks: dict[int, dict] = {}
        tool_json: dict[int, str] = {}
        order: list[int] = []
        stop_reason = "end_turn"
        saw_stop = False
        saw_reason = False
        open_blocks: set[int] = set()
        parsed_tools: list[dict] = []
        # message_start reports the call's input tokens; the closing
        # message_delta reports the final cumulative output tokens.
        input_tokens = 0
        async for line in _sse_lines(resp):
            if not line.startswith("data:"):
                if line.lstrip().startswith(("{", "[")):
                    _provider_object(line)
                    raise _ProviderResponseError()
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            ev = _provider_object(payload)
            etype = ev.get("type")
            if etype == "content_block_start":
                idx = ev.get("index", 0)
                cb = ev.get("content_block", {})
                if type(idx) is not int or idx < 0 or idx in blocks or not isinstance(cb, dict):
                    raise _ProviderResponseError()
                open_blocks.add(idx)
                order.append(idx)
                if cb.get("type") == "tool_use":
                    initial_input = cb.get("input", {})
                    if not isinstance(initial_input, dict):
                        record_rejected_tool(cb.get("id"), cb.get("name"), initial_input,
                                             reason="Tool arguments must be a JSON object.")
                        raise _ProviderResponseError()
                    blocks[idx] = {"type": "tool_use", "id": cb.get("id"), "name": cb.get("name"), "input": {}}
                    tool_json[idx] = json.dumps(initial_input) if initial_input else ""
                elif cb.get("type") == "thinking":
                    blocks[idx] = {"type": "thinking", "thinking": ""}
                else:
                    blocks[idx] = {"type": "text", "text": ""}
            elif etype == "content_block_delta":
                idx = ev.get("index", 0)
                delta = ev.get("delta", {})
                if idx not in open_blocks or not isinstance(delta, dict):
                    raise _ProviderResponseError()
                dtype = delta.get("type")
                if dtype == "text_delta":
                    blocks.setdefault(idx, {"type": "text", "text": ""})
                    blocks[idx]["text"] = blocks[idx].get("text", "") + delta.get("text", "")
                    yield _norm(EV_TEXT, text=delta.get("text", ""))
                elif dtype == "thinking_delta":
                    yield _norm(EV_THINKING, text=delta.get("thinking", ""))
                elif dtype == "input_json_delta":
                    tool_json[idx] = tool_json.get(idx, "") + delta.get("partial_json", "")
            elif etype == "content_block_stop":
                idx = ev.get("index", 0)
                if idx not in open_blocks:
                    raise _ProviderResponseError()
                open_blocks.remove(idx)
                blk = blocks.get(idx)
                if blk and blk.get("type") == "tool_use":
                    parsed_tools.append(_norm(EV_TOOL, id=blk["id"], name=blk["name"],
                                              input=tool_json.get(idx) or "{}", _block_index=idx))
            elif etype == "message_start":
                usage = (ev.get("message") or {}).get("usage") or {}
                if usage:
                    # Context actually sent = fresh input + cache writes + cache
                    # reads (all three are prompt tokens on the wire).
                    input_tokens = (
                        _usage_int(usage.get("input_tokens"))
                        + _usage_int(usage.get("cache_creation_input_tokens"))
                        + _usage_int(usage.get("cache_read_input_tokens"))
                    )
                    yield _norm(EV_USAGE, input_tokens=input_tokens,
                                output_tokens=_usage_int(usage.get("output_tokens")))
            elif etype == "message_delta":
                sr = ev.get("delta", {}).get("stop_reason")
                if sr:
                    stop_reason = sr
                    saw_reason = True
                usage = ev.get("usage") or {}
                if "output_tokens" in usage:
                    yield _norm(EV_USAGE, input_tokens=input_tokens,
                                output_tokens=_usage_int(usage.get("output_tokens")))
            elif etype == "message_stop":
                saw_stop = True
                break
            elif etype == "error":
                # Anthropic can fail AFTER answering 200: an overloaded or
                # internal error is transient and worth a retry, anything else
                # is a refusal of this request.
                err = ev.get("error")
                if isinstance(err, dict) and err.get("type") in _CLAUDE_TRANSIENT_ERRORS:
                    raise _ProviderResponseError("provider_incomplete", retryable=True)
                raise _ProviderResponseError("provider_blocked", retryable=False)
        if not saw_stop or not saw_reason or open_blocks:
            for idx, block in blocks.items():
                if block.get("type") == "tool_use":
                    record_rejected_tool(block.get("id"), block.get("name"), tool_json.get(idx, ""),
                                         reason="The provider response ended before the tool call was complete.",
                                         finish_reason=stop_reason if saw_reason else None)
            raise _ProviderResponseError("provider_incomplete", retryable=stop_reason != "max_tokens")
        if stop_reason in ("refusal", "content_filter"):
            raise _ProviderResponseError("provider_blocked", retryable=False)
        if stop_reason not in ("end_turn", "tool_use", "max_tokens", "stop_sequence"):
            raise _ProviderResponseError()
        if stop_reason == "tool_use" and not parsed_tools:
            raise _ProviderResponseError()
        if parsed_tools and stop_reason != "tool_use":
            raise _ProviderResponseError("provider_incomplete", retryable=stop_reason != "max_tokens")
        # Model-facing error per block index whose arguments did not parse.
        invalid: dict[int, str] = {}
        for tool in parsed_tools:
            raw_arguments = tool["input"]
            try:
                tool["input"] = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                record_rejected_tool(tool["id"], tool["name"], raw_arguments,
                                     reason="Tool arguments are not valid JSON.", finish_reason=stop_reason, error=exc)
                invalid[tool["_block_index"]] = _json_error_hint(raw_arguments, exc)
                # History placeholder only: EV_INVALID_TOOLS never dispatches.
                tool["input"] = {}
                continue
            if not _valid_tool_call(tool["id"], tool["name"], tool["input"]):
                record_rejected_tool(tool["id"], tool["name"], raw_arguments,
                                     reason=_tool_call_problem(tool["id"], tool["name"], tool["input"]),
                                     finish_reason=stop_reason)
                raise _ProviderResponseError()
        if len({tool["id"] for tool in parsed_tools}) != len(parsed_tools):
            for tool in parsed_tools:
                record_rejected_tool(tool["id"], tool["name"], tool["input"],
                                     reason="The response contains duplicate tool call IDs.", finish_reason=stop_reason)
            raise _ProviderResponseError()
        for tool in parsed_tools:
            blocks[tool["_block_index"]]["input"] = tool["input"]
        # Assemble the assistant message in block order; drop thinking blocks from
        # the persisted history (they are display-only and Anthropic re-derives).
        content = []
        for idx in order:
            blk = blocks.get(idx)
            if not blk:
                continue
            if blk["type"] == "text" and blk.get("text"):
                content.append({"type": "text", "text": blk["text"]})
            elif blk["type"] == "tool_use":
                content.append({"type": "tool_use", "id": blk["id"], "name": blk["name"], "input": blk["input"]})
        assistant_msg = {"role": "assistant", "content": content}
        if invalid:
            # The response finished cleanly but a call's arguments do not parse:
            # withhold every call and let the loop hand the error back.
            event = _invalid_tool_calls(
                [(tool["id"], tool["name"], tool["input"], invalid.get(tool["_block_index"])) for tool in parsed_tools],
                assistant_msg,
            )
            if event is None:
                raise _ProviderResponseError()
            yield event
            return
        for tool in parsed_tools:
            tool.pop("_block_index")
            yield tool
        yield _norm(EV_DONE, stop_reason=_norm_stop(stop_reason), assistant_msg=assistant_msg)


# OpenAI's /models lists everything the key can access, which includes many
# non-chat models (embeddings, audio/realtime/transcribe/tts, image, moderation,
# completions-only "-instruct", and web-search variants that reject tool use).
# They are all "public" to the key, but only chat-capable models belong in the
# agentCLI model dropdown, so only the chat families are kept.
_CHATGPT_INCLUDE_RE = re.compile(r"^(gpt-|o\d|chatgpt-)", re.I)
_CHATGPT_EXCLUDE_RE = re.compile(
    r"audio|realtime|transcribe|tts|image|embedding|whisper|moderation|"
    r"dall-e|search|instruct|codex|deep-research|computer-use|cyber|daybreak|"
    r"(?:^|[-_/.:])(?:live|pro)(?:$|[-_/.:])",
    re.I,
)


def _filter_chatgpt_models(models: list[str]) -> list[str]:
    chat = [m for m in models
            if _CHATGPT_INCLUDE_RE.match(m) and not _CHATGPT_EXCLUDE_RE.search(m)]
    return chat or models


# Identifier fragments that prove a catalogue entry is not a chat model: media
# generation (image, video, voice, music), speech, embeddings and rerankers,
# moderation and safety classifiers, and live-session or translation products.
_NON_CHAT_MODEL_RE = re.compile(
    r"embed|rerank|re-rank|image|imagen|imagine|audio|realtime|transcrib|tts|"
    r"whisper|moderation|dall-e|speech|voice|video|veo-|lyria|kinfra|"
    r"guard|safeguard|orpheus|computer-use|"
    r"(?:^|[-_/.:])(?:live|stt|asr|translate)(?:$|[-_/.:])",
    re.I,
)


def _filter_generic_chat_models(models: list[str]) -> list[str]:
    """Drop catalog entries whose identifiers prove they are not chat models."""
    chat = sorted(model for model in models if not _NON_CHAT_MODEL_RE.search(model))
    return chat or sorted(models)


def _filter_together_models(data: list[dict]) -> list[str]:
    """Together publishes a bare array with an explicit model type."""
    chat = sorted(
        str(model["id"]) for model in data
        if model.get("id") and model.get("type") == "chat"
    )
    return chat or sorted(str(model["id"]) for model in data if model.get("id"))


def _strip_models_prefix(model_id: str) -> str:
    # Google's /models lists ids as "models/gemini-...", but the chat endpoint's
    # model field wants the bare name.
    return model_id[len("models/"):] if model_id.startswith("models/") else model_id


def _filter_gemini_models(models: list[str]) -> list[str]:
    """Keep Gemini chat models (drop embedding/imagen/aqa), bare-named."""
    out = []
    for m in models:
        mid = _strip_models_prefix(m)
        low = mid.lower()
        if low.startswith("gemini") and not _NON_CHAT_MODEL_RE.search(low) \
                and not re.search(r"native-audio|robotics|omni|aqa|translate", low):
            out.append(mid)
    return out or [_strip_models_prefix(m) for m in models]


def _filter_openrouter_models(data: list[dict]) -> list[str]:
    """Keep OpenRouter models that advertise "tools" in supported_parameters.

    A model without it cannot drive Phoenix MCP's tools. Sorted for a stable
    dropdown; falls back to every id if none advertise it (schema drift).
    """
    def _text_only(row: dict) -> bool:
        outputs = (row.get("architecture") or {}).get("output_modalities")
        return not isinstance(outputs, list) or outputs == ["text"]

    live = [m for m in data if m.get("id") and not str(m["id"]).endswith(":batch")
            and _text_only(m)]
    tool_ids = sorted(m["id"] for m in live if "tools" in (m.get("supported_parameters") or []))
    return tool_ids or sorted(m["id"] for m in data if m.get("id"))


def _filter_mistral_models(data: list[dict]) -> list[str]:
    """Keep active Mistral models declared fit for tool-driven chat.

    Mistral publishes completion_chat and function_calling booleans per model.
    When that schema is present it is authoritative, so a model must declare
    both. If the schema disappears entirely, fall back to active ids rather than
    turning a provider response change into an empty account.
    """
    # A model with a published deprecation date is on its way out, and the
    # superseded native reasoning line and Labs models are not offered for tool
    # work, so none of them is a candidate for the dropdown or automatic mode.
    active = [
        m for m in data
        if m.get("id") and m.get("archived") is not True and not m.get("deprecation")
        and not re.search(r"^labs-|magistral", str(m["id"]), re.I)
    ]
    if not any(isinstance(m.get("capabilities"), dict) for m in active):
        return [m["id"] for m in active]
    return [
        m["id"] for m in active
        if m.get("capabilities", {}).get("completion_chat") is True
        and m.get("capabilities", {}).get("function_calling") is True
    ]


def _mistral_capabilities(data: list[dict], models: list[str]) -> dict[str, dict]:
    """Translate Mistral's declared model capabilities into Phoenix fields."""
    wanted = set(models)
    out: dict[str, dict] = {}
    for row in data:
        model_id = row.get("id")
        declared = row.get("capabilities")
        if model_id not in wanted or not isinstance(declared, dict):
            continue
        caps: dict[str, bool] = {}
        chat = declared.get("completion_chat")
        tools = declared.get("function_calling")
        vision = declared.get("vision")
        if isinstance(chat, bool) and isinstance(tools, bool):
            caps["tools"] = chat and tools
        if isinstance(vision, bool):
            caps["vision"] = vision
        if caps:
            out[model_id] = caps
    return out


# How providers word "this model cannot take image input": an unsupported
# statement next to image/vision, or a schema that has no image content type.
_IMAGE_REFUSAL = re.compile(
    r"(?:image|vision|multi-?modal)[^.]*(?:not supported|unsupported|only supported by|not enabled|not available)"
    r"|(?:does not|doesn't|do not|cannot|can't) (?:support|accept|process|handle)[^.]*(?:image|vision|multi-?modal)"
    r"|unknown variant `?image_url"
)


def _refused_option(status: int, message: str, sent: dict) -> str | None:
    """The request key a provider just refused, when it named one of ours.

    Bounded three ways, because a wrong answer here disables a control the
    operator was using. It must be a 400 (a refusal, not an outage or a quota);
    the key must be one whose refusal has a single unambiguous consequence; and
    Phoenix must have ACTUALLY SENT it in this request. That last guard is what
    stops an error mentioning "temperature" for some unrelated reason from
    teaching Phoenix that temperature is unsupported.
    """
    if status != 400 or not message:
        return None
    low = message.lower()
    if _IMAGE_REFUSAL.search(low) and any(
        isinstance(block, dict) and block.get("type") in ("image", "image_url")
        for msg in sent.get("messages") or [] if isinstance(msg, dict) and isinstance(msg.get("content"), list)
        for block in msg["content"]
    ):
        # A model that cannot take image input. Size or format complaints about
        # one image do not match, so they never disable attachments.
        return "images"
    if "tools" in sent and re.search(
        r"(?:tool|function)[ -]calling[^.]*(?:not supported|requires)|use the responses api", low,
    ):
        # The model cannot take tools on this endpoint at all (OpenAI's newest
        # models require the Responses API for them).
        return "tools"
    for key in AGENTCLI_LEARNABLE_OPTIONS:
        if key == "tools":
            continue
        if key in sent and re.search(rf"\b{re.escape(key)}\b", low):
            return key
    return None


def _effort_probe_body(kind: str, level: str) -> dict | None:
    """The request fragment that carries an effort level for this backend.

    None for a backend with no effort control at all, which is then not probed
    (MiniMax takes an adaptive object with no level vocabulary). The aggregators
    (OpenRouter, NVIDIA NIM) ARE probed: what they pass through varies per model,
    but a probe runs against the one selected model, so that variation is exactly
    what it handles.

    DeepSeek's fragment carries the thinking toggle too, because it only reads
    the effort when thinking is enabled: probing the level alone would test
    nothing and answer 200 for every value.
    """
    if kind == "claude":
        return {"output_config": {"effort": level}}
    if kind == "deepseek":
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}
    if kind in (
        "chatgpt", "grok", "gemini", "kimi", "meta", "mistral", "openrouter",
        "nvidia", "opencode", "tencent", "ollama", "ollama_cloud", "groq", "together", "cerebras",
        "fireworks",
    ):
        return {"reasoning_effort": level}
    return None


# Six colours every vision model names the same way. The check paints three of
# them, in a random order, as vertical stripes.
_PROBE_COLOURS: dict[str, tuple[int, int, int]] = {
    "red": (220, 30, 30), "green": (30, 160, 60), "blue": (30, 70, 210),
    "yellow": (240, 210, 30), "black": (15, 15, 15), "white": (250, 250, 250),
}
_PROBE_COLOUR_WORD = re.compile(r"\b(" + "|".join(_PROBE_COLOURS) + r")\b")
_PROBE_IMAGE_QUESTION = (
    "This image has three vertical stripes. Name their colors from left to right, using only these words: "
    + ", ".join(_PROBE_COLOURS) + ". Reply with just the three words, separated by commas."
)


def _probe_stripes() -> list[str]:
    return random.SystemRandom().sample(list(_PROBE_COLOURS), 3)


def _stripe_png(stripes: list[str], width: int = 240, height: int = 80) -> str:
    """A base64 PNG of equal vertical stripes, built without an imaging library."""
    row = b"\0" + b"".join(bytes(_PROBE_COLOURS[colour]) * (width // len(stripes)) for colour in stripes)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width // len(stripes) * len(stripes), height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


async def async_probe_vision(session: ClientSession, cfg: ProviderConfig) -> bool | None:
    """Whether the selected model can read an image, established by showing it one.

    Some providers accept image parts but drop them before the model, which then
    answers as if none was sent; no status code reveals that. So the check asks
    a question only sight can answer: three stripe colours in a random order,
    which a text-only model guesses right about once in 120 tries.

    True when all three are named in order. False when the model answers but
    gets them wrong or cannot see them, or the provider refuses the image as
    unsupported input. None when there is no usable answer (unreachable, a rate
    limit, an outage, or an empty reply from a model that spent its output on
    reasoning), which establishes nothing and must not be recorded.
    """
    stripes = _probe_stripes()
    status, text = await build_provider(cfg).probe_image(session, _stripe_png(stripes), _PROBE_IMAGE_QUESTION)
    _LOGGER.debug("Image check for %s %s answered %s with %d characters", cfg.kind, cfg.model, status, len(text))
    if status == 400 and _IMAGE_REFUSAL.search(text.lower()):
        return False
    if status != 200:
        return None
    reply = re.sub(r"(?is)<think>.*?</think>", "", text).strip().lower()
    if not reply:
        return None
    return _PROBE_COLOUR_WORD.findall(reply)[:3] == stripes


async def async_probe_capabilities(
    session: ClientSession, cfg: ProviderConfig,
) -> tuple[dict, int, bool]:
    """What this model's API actually accepts, established by asking it.

    Returns (capabilities, calls_made, answered). A value is omitted rather than
    guessed when it could not be established, so a partial or failed probe
    narrows nothing.

    `answered` is False when the control request below did not answer 200. A key
    with no credit, a revoked key, a refused model or an unreachable host answers
    every question the same way, and reporting that as "this provider does not
    validate its options" would blame the provider for an account problem.

    TWO STAGES, and the order is the whole method. Stage one sends a
    deliberately INVALID effort: a 400 proves the server validates the field.
    Only then does stage two walk the real levels, where a 200 is meaningful.
    Without stage one, a server that ignores an unknown parameter answers 200 to
    every level, which reads as "supports all five". A 400 is informative; a 200
    on its own never is.

    Both stages need a CONTROL first: the same minimal request with no option
    must answer 200. Without it, a model the endpoint refuses outright (wrong
    id, wrong plan, an unrelated request rule) answers 400 to everything, and
    those 400s would be recorded as refused options. A refused control
    establishes nothing and reports the provider as not answering.
    """
    provider = build_provider(cfg)
    caps: dict = {}
    calls = 1
    if await provider.probe_option(session, {}) != 200:
        return caps, calls, False

    fragment = _effort_probe_body(cfg.kind, AGENTCLI_PROBE_SENTINEL)
    if fragment is not None:
        status = await provider.probe_option(session, fragment)
        calls += 1
        # Only a 400 is evidence. Unreachable (None) says nothing, and a 200
        # means the field is ignored or leniently defaulted, which are
        # indistinguishable and equally useless.
        if status == 400:
            levels = []
            for level in AGENTCLI_EFFORT_LEVEL_ORDER:
                if calls >= AGENTCLI_PROBE_MAX_CALLS:
                    break
                body = _effort_probe_body(cfg.kind, level)
                if body is None:
                    continue
                level_status = await provider.probe_option(session, body)
                if level_status == 200:
                    levels.append(level)
                calls += 1
            # An empty result means every level was refused, which cannot be
            # true of a working model and means something else answered the
            # calls. Recording it would strip the control entirely.
            if levels:
                caps["effort_levels"] = levels

    if calls < AGENTCLI_PROBE_MAX_CALLS:
        # Temperature is the mirror-image question: not "which values", but
        # "does this model refuse the field", which is what reasoning models do.
        # So here the 400 is the finding and a 200 records nothing, since an
        # accepted temperature may still be ignored.
        status = await provider.probe_option(session, {"temperature": 0.7})
        calls += 1
        if status == 400:
            caps["temperature"] = False

    return caps, calls, True


def _openrouter_capabilities(data: list[dict]) -> dict[str, dict]:
    """Per-model declared capabilities from OpenRouter's `supported_parameters`.

    The same field also filters the dropdown by tool support, so a model's
    declared tool, reasoning, temperature and vision support come from one
    response.

    A key is emitted ONLY for something the provider actually declares. A missing
    key means "not declared" and must never be read as False.
    """
    out: dict[str, dict] = {}
    for m in data:
        model_id = m.get("id")
        params = m.get("supported_parameters")
        architecture = m.get("architecture")
        modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
        if not isinstance(modalities, list):
            modalities = m.get("input_modalities")
        if not model_id or (not isinstance(params, list) and not isinstance(modalities, list)):
            continue
        row: dict[str, bool] = {}
        if isinstance(params, list):
            row.update({
                "tools": "tools" in params,
                "thinking": "reasoning" in params,
                "temperature": "temperature" in params,
            })
        if isinstance(modalities, list):
            row["vision"] = any(
                isinstance(item, str) and item.lower() in {"image", "image_url"}
                for item in modalities
            )
        out[model_id] = row
    return out


async def _ollama_capabilities(
    session: ClientSession, base_url: str, headers: dict, models: list[str],
) -> dict[str, dict]:
    """Per-model declared capabilities from Ollama's /api/show.

    Ollama is the one backend where a model that cannot call tools is an ordinary
    thing to have installed: a local library is whatever the operator pulled, and
    Agent Chat is useless without tool calling. `/api/tags` does not say, so this
    costs one lookup per model, which is why it runs on an explicit refresh and
    not on every card open.

    `temperature` is deliberately NOT emitted: Ollama takes it for every model
    through its options block and does not list it as a capability, so claiming
    False would be inventing a limit the server does not have.
    """
    sem = asyncio.Semaphore(AGENTCLI_CAPABILITY_CONCURRENCY)

    async def _one(name: str) -> tuple[str, dict] | None:
        async with sem:
            try:
                async with session.post(
                    f"{base_url}/api/show", headers=headers, json={"model": name},
                    timeout=_PROBE_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        return None
                    body = await resp.json(content_type=None)
            except (ClientError, asyncio.TimeoutError, ValueError):
                return None
        caps = body.get("capabilities") if isinstance(body, dict) else None
        if not isinstance(caps, list):
            return None
        return name, {
            "tools": "tools" in caps,
            "thinking": "thinking" in caps,
            "vision": "vision" in caps,
        }

    pairs = await asyncio.gather(*(_one(m) for m in models))
    return dict(p for p in pairs if p is not None)


# OpenCode Zen serves one catalogue through several wire formats. Only the
# chat-completions family is reachable by this client, so models Zen documents on
# its Responses, Anthropic-messages, Gemini or typed-decision endpoints are left
# out. Matching is by vendor family word, never by version, so a new release of a
# family is classified the same way as the last one. A family Phoenix has not seen
# is kept, because chat completions is Zen's default endpoint.
_OPENCODE_OTHER_ENDPOINT_RE = re.compile(
    r"^(?:gpt|grok|claude|gemini|jev|muse|qwen)", re.I,
)


def _filter_opencode_models(models: list[str]) -> list[str]:
    """Keep the Zen models that answer on /chat/completions."""
    chat = sorted(
        m for m in models
        if not _OPENCODE_OTHER_ENDPOINT_RE.match(m) and not _NON_CHAT_MODEL_RE.search(m)
    )
    return chat or sorted(models)


def _filter_meta_models(models: list[str]) -> list[str]:
    """Keep Meta's chat models: the Muse Spark text line, without its data-sharing tier.

    Meta serves image, voice and segmentation models under the same key, and a
    `-contributor` model trains on the prompts it receives, which must be an
    explicit operator choice rather than something a listing offers.
    """
    chat = sorted(m for m in models if m.lower().startswith("muse-spark")
                  and "contributor" not in m.lower())
    return chat or sorted(models)


def _filter_nvidia_models(models: list[str]) -> list[str]:
    """Drop NVIDIA's non-chat models (embedding/reranking) from the dropdown.

    Unlike OpenRouter, NVIDIA's catalog does not advertise per-model parameters,
    so tool-calling support (which varies by model there) cannot be detected and
    is left to the operator. This only removes ids that provably cannot chat at
    all. Sorted for a stable dropdown; falls back to every id if the name
    convention ever changes enough to filter everything out.
    """
    chat = sorted(
        m for m in models
        if not _NON_CHAT_MODEL_RE.search(m) and not re.search(
            r"rerank|reward|safety|parse|clip|vila|neva|kosmos|deplot|detector|"
            r"retriever|calibration|riva", m, re.I)
    )
    return chat or sorted(models)


class OpenAICompatProvider:
    """OpenAI-compatible chat/completions, streaming.

    Backs every conversation provider whose definition does not use the
    Anthropic adapter (ClaudeProvider serves Anthropic and MiniMax).

    Ollama uses a slightly different shape (the OpenAI path lives under /v1 and
    models come from /api/tags). Its current OpenAI-compatible endpoint accepts
    reasoning_effort; older endpoints retain a boolean UI fallback until a probe
    proves level support. Local Ollama is keyless with a user-supplied base URL
    and is the fragile tool backend (see _parse); Ollama Cloud is the same shape
    hosted at a fixed URL with a Bearer API key.
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self.kind = cfg.kind
        self.definition = PROVIDER_DEFINITIONS[cfg.kind]
        # Filled by list_models: creation time per listed id, for model selection.
        self.model_created: dict[str, float] = {}

    @property
    def _is_ollama(self) -> bool:
        # Ollama-shaped (local or cloud): drives /v1 chat plus /api/tags metadata.
        return self.definition.ollama is not None

    @property
    def _is_ollama_local(self) -> bool:
        return self.definition.ollama == "local"

    @property
    def _is_ollama_cloud(self) -> bool:
        return self.definition.ollama == "cloud"

    def _chat_url(self) -> str:
        # Ollama exposes the OpenAI shape under /v1; DeepSeek at the root.
        return f"{self.cfg.base_url}/v1/chat/completions" if self._is_ollama \
            else f"{self.cfg.base_url}/chat/completions"

    def _headers(self) -> dict:
        # Every keyed backend uses a Bearer header, including Ollama Cloud; only
        # local Ollama is keyless (no auth header).
        h = {"content-type": "application/json"}
        if self.cfg.api_key and not self._is_ollama_local:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def format_tools(self, mcp_tools: list[dict]) -> list[dict]:
        out = []
        for t in mcp_tools:
            name, description, schema = _provider_tool(t)
            out.append({"type": "function", "function": {
                "name": name, "description": description, "parameters": schema,
            }})
        return out

    def user_message(self, text: str, images: list[dict[str, str]]) -> dict:
        """The administrator's message with attached images as data-URI content parts."""
        content: list[dict] = []
        for image in images:
            data_uri = f"data:{image['mime_type']};base64,{image['data']}"
            content.append({
                "type": "image_url",
                # Same Mistral difference as tool-result images below.
                "image_url": data_uri if self.definition.model_filter == "mistral" else {"url": data_uri},
            })
        if text:
            content.append({"type": "text", "text": text})
        return {"role": "user", "content": content}

    def append_assistant(self, messages: list[dict], assistant_msg: dict) -> None:
        messages.append(assistant_msg)

    def append_tool_results(self, messages: list[dict], results: list[dict]) -> None:
        for r in results:
            message = {
                "role": "tool",
                "tool_call_id": r["tool_use_id"],
                "name": r.get("tool_name", ""),
                "content": _model_result_text(r, self.cfg.vision is True),
            }
            if self.kind == "groq":
                # Groq answers 400 to a `name` on a message; tool_call_id already
                # carries the link back to the call.
                del message["name"]
            messages.append(message)
            if self.cfg.vision is True and r.get("images"):
                parts: list[dict] = [{
                    "type": "text",
                    "text": "The tool returned the following image for visual inspection.",
                }]
                for image in r["images"]:
                    data_uri = f"data:{image['mime_type']};base64,{image['data']}"
                    parts.append({
                        "type": "image_url",
                        # Mistral takes the URL itself here; OpenAI and the other
                        # compatible APIs take an object containing `url`.
                        "image_url": (
                            data_uri if self.definition.model_filter == "mistral"
                            else {"url": data_uri}
                        ),
                    })
                messages.append({"role": "user", "content": parts})

    async def validate(self, session: ClientSession) -> tuple[bool, str]:
        async def _probe() -> tuple[bool, str]:
            if self._is_ollama:
                async with session.get(f"{self.cfg.base_url}/api/tags",
                                       headers=self._headers(), timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                    if resp.status == 200:
                        return True, ""
                    if resp.status in (401, 403):
                        return False, "API key rejected."
                    label = "Ollama Cloud" if self._is_ollama_cloud else "Ollama"
                    return False, f"{label} returned HTTP {resp.status}."
            async with session.get(f"{self.cfg.base_url}/models", headers=self._headers(),
                                   timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status == 200:
                    return True, ""
                if resp.status in (401, 403):
                    return False, "API key rejected."
                if self.definition.validation_error == "fireworks" and resp.status == 412:
                    return False, (
                        "Fireworks reports that this account is not active for API inference. "
                        "Check the account status and confirm that billing or free-trial credits "
                        "are activated."
                    )
                if self.definition.validation_error == "qwen_openai" and resp.status == 404:
                    return False, (
                        "Qwen returned HTTP 404. Phoenix uses the OpenAI-compatible Chat "
                        "Completions endpoint; enter that Base URL, not the "
                        "Anthropic-compatible /apps/anthropic URL."
                    )
                return False, f"Provider returned HTTP {resp.status}."
        try:
            return await _retry_transient(_probe)
        except ValueError:
            return False, "Invalid provider connection settings."
        except (ClientError, asyncio.TimeoutError) as exc:
            if self._is_ollama_local:
                # Newlines are preserved in the panel (the banner uses
                # white-space: pre-line), so keep this as a short lead plus a
                # scannable checklist rather than one dense paragraph.
                return False, (
                    f"Could not reach Ollama at {self.cfg.base_url}.\n"
                    f"({exc})\n\n"
                    "Phoenix MCP connects from the Home Assistant server, not your browser, so "
                    "Ollama must be reachable from that host:\n"
                    "- Ollama is running and bound to a reachable interface (OLLAMA_HOST=0.0.0.0).\n"
                    "- The firewall allows the Ollama port (default 11434).\n"
                    "- 'localhost' here means the HA server, not your PC."
                )
            if self._is_ollama_cloud:
                return False, f"Could not reach Ollama Cloud at {self.cfg.base_url}. ({exc})"
            return False, f"Could not reach the provider: {exc}"

    async def list_models(self, session: ClientSession) -> list[str]:
        try:
            if self._is_ollama:
                async with session.get(f"{self.cfg.base_url}/api/tags",
                                       headers=self._headers(), timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                    if resp.status != 200:
                        return []
                    # content_type=None: some Ollama builds omit the JSON content
                    # type, which would otherwise make aiohttp .json() raise.
                    body = await resp.json(content_type=None)
                self.model_created = _created_map(body.get("models", []))
                return [m.get("name") or m.get("model") for m in body.get("models", [])
                        if (m.get("name") or m.get("model"))]
            async with session.get(f"{self.cfg.base_url}/models", headers=self._headers(),
                                   timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    return [self.cfg.model] if self.cfg.model else []
                body = await resp.json(content_type=None)
            data = (
                body if self.definition.models_shape == "list" and isinstance(body, list)
                else body.get("data", []) if isinstance(body, dict)
                else []
            )
            data = [row for row in data if not _is_expired(row)]
            self.model_created = _created_map(data)
            if self.definition.model_filter == "openrouter":
                return _filter_openrouter_models(data) or ([self.cfg.model] if self.cfg.model else [])
            if self.definition.model_filter == "mistral":
                return _filter_mistral_models(data)
            if self.definition.model_filter in ("mimo", "tencent"):
                # These catalogs include non-chat products. Never restore a
                # configured model after the catalog explicitly excluded it.
                return sorted(
                    row["id"] for row in data
                    if isinstance(row, dict) and isinstance(row.get("id"), str)
                    and row["id"] and not _NON_CHAT_MODEL_RE.search(row["id"])
                    and not re.search(r"(?:^|[-/])(?:asr|video)(?:[-/]|$)", row["id"], re.I)
                    and (self.kind != "tencent" or row.get("status") == "online")
                )
            models = [m["id"] for m in data if m.get("id")]
            if self.definition.model_filter == "openai":
                models = _filter_chatgpt_models(models)
            elif self.definition.model_filter == "gemini":
                models = _filter_gemini_models(models)
            elif self.definition.model_filter == "nvidia":
                models = _filter_nvidia_models(models)
            elif self.definition.model_filter == "together":
                return _filter_together_models(data)
            elif self.definition.model_filter == "opencode":
                models = _filter_opencode_models(models)
            elif self.definition.model_filter == "meta":
                models = _filter_meta_models(models)
            elif self.definition.model_filter == "chat":
                models = _filter_generic_chat_models(models)
            return models or ([self.cfg.model] if self.cfg.model else [])
        except (ClientError, asyncio.TimeoutError):
            return [self.cfg.model] if self.cfg.model else []

    async def probe_image(self, session: ClientSession, image: str, question: str) -> tuple[int | None, str]:
        """Status and reply text for one question about an attached PNG (see async_probe_vision)."""
        token_limit = ("max_completion_tokens" if self.kind in ("mimo", "chatgpt", "meta")
                       else "max_tokens")
        body: dict = {"model": self.cfg.model, token_limit: AGENTCLI_PROBE_IMAGE_MAX_TOKENS, "stream": False,
                      "messages": [self.user_message(question, [{"mime_type": "image/png", "data": image}])]}
        if self.definition.reasoning == "deepseek":
            # A DeepSeek reasoning model can spend the whole allowance thinking
            # about an image it cannot see and return no answer; it has an off switch.
            body["thinking"] = {"type": "disabled"}
        try:
            async with session.post(self._chat_url(), headers=self._headers(), json=body,
                                    timeout=_IMAGE_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    return resp.status, _parse_provider_error((await resp.text())[:4000])[0]
                data = await resp.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None, ""
        choices = data.get("choices") if isinstance(data, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict) and isinstance(part.get("text"), str))
        return 200, content if isinstance(content, str) else ""

    async def probe_option(self, session: ClientSession, extra: dict) -> int | None:
        """HTTP status for the smallest possible completion carrying `extra`.

        None when the provider could not be reached at all, which is not an
        answer about the option and must not be recorded as one.
        """
        # OpenAI's reasoning models reject max_tokens, and Meta deprecates it.
        token_limit = ("max_completion_tokens" if self.kind in ("mimo", "chatgpt", "meta")
                       else "max_tokens")
        body = {"model": self.cfg.model, token_limit: 1, "stream": False,
                "messages": [{"role": "user", "content": "hi"}], **extra}
        try:
            async with session.post(self._chat_url(), headers=self._headers(), json=body,
                                    timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                return resp.status
        except (ClientError, asyncio.TimeoutError):
            return None

    async def list_model_capabilities(
        self, session: ClientSession, models: list[str],
    ) -> dict[str, dict]:
        """What each model DECLARES it accepts; empty when the provider says nothing.

        OpenRouter, Mistral, and Ollama report this; Z.ai, Qwen and MiMo are
        credited with thinking support from their reasoning style. Every other
        backend's models endpoint returns only an id and an owner, so the shipped
        capability table cannot be replaced by discovery, and probing the knobs is
        a separate step rather than part of this one.

        Empty means "the provider declared nothing", never "the model supports
        nothing": a caller reading it the second way would strip a working
        model's controls the moment a lookup failed.
        """
        try:
            if self._is_ollama:
                return await _ollama_capabilities(
                    session, self.cfg.base_url, self._headers(), models)
            if self.definition.model_filter == "openrouter":
                async with session.get(
                    f"{self.cfg.base_url}/models", headers=self._headers(),
                    timeout=_PROBE_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        return {}
                    body = await resp.json(content_type=None)
                return _openrouter_capabilities(body.get("data", []))
            if self.definition.model_filter == "mistral":
                async with session.get(
                    f"{self.cfg.base_url}/models", headers=self._headers(),
                    timeout=_PROBE_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        return {}
                    body = await resp.json(content_type=None)
                return _mistral_capabilities(body.get("data", []), models)
            if self.definition.reasoning in ("zai", "qwen", "mimo"):
                return {model: {"thinking": True} for model in models}
        except (ClientError, asyncio.TimeoutError, ValueError):
            return {}
        return {}

    async def stream_turn(
        self, session: ClientSession, *, system_prompt: str, messages: list[dict],
        tools: list[dict], options: dict,
    ) -> AsyncIterator[dict]:
        # OpenAI shape carries the system prompt as the first message.
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        body: dict = {
            "model": self.cfg.model,
            "messages": full_messages,
            "tools": tools,
            "stream": True,
        }
        if self.definition.include_usage:
            body["stream_options"] = {"include_usage": True}
        thinking = options.get("thinking")
        effort = options.get("effort")
        # Map the panel's thinking selection to each backend's real API knob:
        #   DeepSeek: a thinking on/off toggle + reasoning_effort (low/high/max,
        #     default high; xhigh maps to max). Thinking mode ignores temperature,
        #     so it is dropped while thinking is on.
        #   OpenAI reasoning models: reasoning_effort only (none/minimal/low/
        #     medium/high; none disables it), and they reject a custom temperature.
        #   Ollama: reasoning_effort on its OpenAI-compatible endpoint. Older
        #     servers that do not validate levels never establish effort_levels,
        #     so the panel keeps its boolean fallback. Off maps to `none`; on with
        #     no established level omits the field and uses the model's default.
        thinking_on = False
        reasoning_model = False
        effort_forced = False
        if self.definition.reasoning == "deepseek":
            if thinking is not None:
                body["thinking"] = {"type": "enabled" if thinking else "disabled"}
                thinking_on = bool(thinking)
            if thinking_on and effort:
                # TOP-LEVEL: a deliberately INVALID value here answers 400, so
                # the field is parsed and validated rather than ignored. It is a
                # standard OpenAI parameter, which is why it sits beside
                # `thinking` rather than inside it; DeepSeek's own samples pass
                # `thinking` through extra_body and leave this one a normal
                # argument.
                #
                # The model's replies cannot show whether it worked: reasoning
                # output appears or does not at any effort level, run to run.
                # Only the invalid-value probe settles it.
                body["reasoning_effort"] = effort
            # DeepSeek's undocumented default output cap can truncate a large tool
            # call (a whole-dashboard write) mid-JSON. 32K only ever raises the
            # ceiling; see const.py. Other kinds deliberately get no max_tokens
            # (OpenAI reasoning models reject the field).
            body["max_tokens"] = AGENTCLI_DEEPSEEK_MAX_TOKENS
        elif self.definition.reasoning == "effort":
            # OpenAI and xAI both take a top-level reasoning_effort on chat
            # completions; sending it marks a reasoning turn (skip temperature).
            # A model that does not accept the level returns a clean error.
            if self.kind == "chatgpt" and tools and self.cfg.model not in _OPENAI_NO_EFFORT_NONE:
                # Chat Completions cannot combine tool calling with a reasoning
                # effort other than "none" on current OpenAI models, and a newer
                # model's default effort is not "none". Send "none" for tool turns;
                # a model that rejects the field (older or non-reasoning models)
                # is retried once without it below and remembered.
                body["reasoning_effort"] = "none"
                effort_forced = True
            elif effort:
                body["reasoning_effort"] = effort
                reasoning_model = True
        elif self.definition.reasoning == "gemini":
            # Gemini's OpenAI-compatible endpoint maps reasoning_effort to Gemini's
            # thinking_level (minimal/low/medium/high). Google strongly recommends
            # NOT setting temperature on Gemini 3 (reasoning is tuned for the
            # default), so Phoenix MCP never sends it for Gemini.
            if effort:
                body["reasoning_effort"] = effort
        elif self.definition.reasoning == "kimi":
            # Kimi splits the knob by model family, so the two fields are emitted
            # independently rather than as an if/else: K3 takes reasoning_effort
            # (low/high/max, default max) and cannot turn reasoning off, while K2.x
            # takes DeepSeek's thinking object and has no effort levels. The panel
            # offers only the one its selected model accepts, and a headless turn
            # (no options at all) sends neither and gets the model's own default.
            if thinking is not None:
                body["thinking"] = {"type": "enabled" if thinking else "disabled"}
            if effort:
                body["reasoning_effort"] = effort
        elif self.definition.reasoning == "probed_effort":
            # Aggregators: one key fronting many vendors' models. One fixed
            # control cannot fit every model behind the key, so capabilities are
            # established PER MODEL; otherwise a reasoning model reached through
            # an aggregator would silently lose its reasoning while the same
            # model reached directly kept it.
            #
            # Which field carries it is not assumed either. Both speak the
            # OpenAI shape, so `reasoning_effort` is the candidate, and the probe
            # is what confirms it: if the aggregator does not validate the field,
            # no levels are established, the panel offers none, and this branch
            # never fires. The wrong guess costs nothing.
            if effort:
                body["reasoning_effort"] = effort
        elif self.definition.reasoning == "meta":
            # Meta's Model API takes a top-level reasoning_effort (minimal/low/
            # medium/high/xhigh; "none" is rejected by Muse Spark, which always
            # reasons, so the panel never offers an off state). Meta tunes the model
            # for its default temperature, so Phoenix MCP does not send one.
            if effort:
                body["reasoning_effort"] = effort
        elif self.definition.reasoning == "mistral":
            # Mistral Small latest and Medium 3.5 accept `none` and `high`.
            # The panel only offers this control for those documented models.
            if effort:
                body["reasoning_effort"] = effort
        elif self.definition.reasoning == "zai":
            if thinking is not None:
                body["thinking"] = {"type": "enabled" if thinking else "disabled"}
            if tools:
                body["tool_stream"] = True
                body["clear_thinking"] = False
        elif self.definition.reasoning == "mimo":
            if thinking is not None:
                body["thinking"] = {"type": "enabled" if thinking else "disabled"}
        elif self.definition.reasoning == "qwen":
            if thinking is not None:
                body["enable_thinking"] = bool(thinking)
        elif self.definition.reasoning == "ollama":
            if effort:
                body["reasoning_effort"] = effort
            elif thinking is False:
                body["reasoning_effort"] = "none"
        temp = options.get("temperature")
        skip_temp = (
            (self.definition.reasoning == "deepseek" and thinking_on)
            or reasoning_model
            or self.definition.reasoning == "gemini"
            # Only Kimi's legacy moonshot-v1 models accept temperature, and those
            # have no reasoning control at all: a thinking or effort selection
            # therefore means a kimi-k* model, which rejects the field.
            or (self.definition.reasoning == "kimi" and (thinking is not None or bool(effort)))
            or self.definition.reasoning == "meta"
            or (self.definition.reasoning in ("zai", "qwen", "mimo") and thinking is True)
        )
        if temp is not None and not skip_temp:
            try:
                body["temperature"] = float(temp)
            except (TypeError, ValueError):
                pass
        show_thinking = bool(options.get("show_thinking"))
        try:
            for attempt in range(2):
                async with session.post(self._chat_url(), headers=self._headers(), json=body,
                                        timeout=_CHAT_TIMEOUT, allow_redirects=False) as resp:
                    if resp.status != 200:
                        text = (await resp.text())[:4000]
                        msg = _parse_provider_error(text)[0]
                        if (attempt == 0 and effort_forced and resp.status == 400
                                and "reasoning_effort" in msg.lower()):
                            _OPENAI_NO_EFFORT_NONE.add(self.cfg.model)
                            body.pop("reasoning_effort", None)
                            continue
                        # Ollama commonly 500s on an unknown/hallucinated tool name.
                        code = _http_failure_code(resp.status, "ollama_backend" if self._is_ollama else "upstream")
                        retryable = resp.status in (429, 500, 502, 503)
                        rate_limit = _rate_limit_headers(
                            resp.headers,
                            _MISTRAL_RATE_LIMIT_HEADER_FIELDS if self.definition.model_filter == "mistral"
                            else _RETRY_AFTER_HEADER_FIELDS,
                        ) if resp.status == 429 else {}
                        yield _http_failure(self.kind, resp.status, text, code=code, retryable=retryable,
                                            refused=_refused_option(resp.status, msg, body),
                                            **({"rate_limit": rate_limit} if rate_limit else {}))
                        return
                    async for ev in self._parse(resp, show_thinking):
                        yield ev
                    return
        except (ClientError, asyncio.TimeoutError):
            yield _norm(EV_ERROR, status=0, code="network", message="Provider connection failed.", retryable=True)

    @_total_provider_response
    async def _parse(self, resp: Any, show_thinking: bool) -> AsyncIterator[dict]:
        text_parts: list[str] = []
        # The providers in _REPLAY_REASONING_KINDS retain reasoning_content verbatim
        # with the assistant tool call on the next round. Other compatible
        # providers use this field for display only, so retention is
        # profile-controlled.
        replay_reasoning_parts: list[str] = []
        # OpenRouter requires its structured `reasoning_details` array to be sent
        # back unmodified, in order, across tool calls (encrypted and summarised
        # reasoning cannot be rebuilt from text).
        reasoning_details: list = []
        # Mistral emits reasoning as structured ThinkChunk content. Unlike the
        # display-only reasoning fields used by other compatible APIs, Mistral
        # requires these chunks to be replayed in the assistant message on the
        # next turn, even when verbose output is hidden in Phoenix.
        mistral_content_parts: list[dict] = []
        # tool calls accumulated by index: {index: {"id","name","args"}}
        calls: dict[int, dict] = {}
        # Gemini's compatibility layer deviates from OpenAI streaming for tool
        # calls (see _merged_whole_arguments and _gemini_tool_slot).
        gemini = self.definition.tool_stream == "gemini"
        pending_extra: dict | None = None
        finish = "stop"
        # Whether the stream ever reported WHY it ended. Every provider sends a
        # finish_reason on its final chunk, so the default above standing
        # untouched means the response stopped arriving rather than finishing:
        # the one signal that tells a truncated tool call apart from a malformed
        # one. Deliberately not keyed on the "[DONE]" sentinel, which is an
        # OpenAI convention some compatible backends never send, and which would
        # therefore relabel their genuinely malformed calls as dropped ones.
        saw_finish = False
        # Some local reasoning models (e.g. r1 via Ollama) emit their chain of
        # thought inline as <think>...</think> inside content rather than in a
        # separate reasoning field. Split it out so it is treated as thinking
        # (shown only when show_thinking) and never leaks into the reply or the
        # message history sent back next turn.
        think = _ThinkSplitter()
        async for line in _sse_lines(resp):
            if not line.startswith("data:"):
                if line.lstrip().startswith(("{", "[")):
                    _provider_object(line)
                    raise _ProviderResponseError()
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                break
            chunk = _provider_object(payload)
            # Usage must be read BEFORE the empty-choices skip: OpenAI's final
            # usage chunk (stream_options include_usage) carries choices: [].
            # Some backends (Gemini, Grok) volunteer usage on content chunks
            # instead; cumulative-replace semantics cover both. Ollama sends
            # usage only when include_usage is requested.
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                pt = _usage_int(usage.get("prompt_tokens"))
                ct = _usage_int(usage.get("completion_tokens"))
                if pt or ct:
                    yield _norm(EV_USAGE, input_tokens=pt, output_tokens=ct)
            choices = chunk.get("choices")
            if not isinstance(choices, list):
                raise _ProviderResponseError()
            if not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                raise _ProviderResponseError()
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise _ProviderResponseError()
            if delta.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise _ProviderResponseError("provider_blocked", retryable=False)
            # Retain protocol-required reasoning even when its display is hidden.
            # Ollama's reasoning field remains display-only.
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning is not None and not isinstance(reasoning, str):
                raise _ProviderResponseError()
            if self.kind == "openrouter" and isinstance(delta.get("reasoning_details"), list):
                reasoning_details.extend(d for d in delta["reasoning_details"] if isinstance(d, dict))
            if reasoning and self.kind in _REPLAY_REASONING_KINDS:
                replay_reasoning_parts.append(str(reasoning))
            if reasoning and show_thinking:
                yield _norm(EV_THINKING, text=reasoning)
            content = delta.get("content")
            if content is not None and not isinstance(content, str) and not (
                self.definition.model_filter == "mistral" and isinstance(content, list)
            ):
                raise _ProviderResponseError()
            if self.definition.model_filter == "mistral" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    # Retain each structured block in its original order and
                    # shape. Mistral explicitly requires the full assistant
                    # content, including ThinkChunk, on subsequent turns.
                    mistral_content_parts.append(block)
                    if block.get("type") == "thinking":
                        thinking_text = "".join(
                            str(item.get("text", ""))
                            for item in block.get("thinking", [])
                            if isinstance(item, dict) and item.get("type") == "text"
                        )
                        if thinking_text:
                            if show_thinking:
                                yield _norm(EV_THINKING, text=thinking_text)
                    elif block.get("type") == "text" and block.get("text"):
                        seg = str(block["text"])
                        text_parts.append(seg)
                        yield _norm(EV_TEXT, text=seg)
            elif self.definition.model_filter == "mistral" and isinstance(content, str) \
                    and content and mistral_content_parts:
                # Mistral may switch from structured ThinkChunk content to
                # ordinary string deltas for the answer. Materialize those as
                # TextChunk content after the retained thinking blocks.
                text_parts.append(content)
                if mistral_content_parts[-1].get("type") == "text":
                    old_text = str(mistral_content_parts[-1].get("text", ""))
                    mistral_content_parts[-1]["text"] = old_text + content
                else:
                    mistral_content_parts.append({"type": "text", "text": content})
                yield _norm(EV_TEXT, text=content)
            elif isinstance(content, str) and content:
                for kind, seg in think.feed(content):
                    if kind == "text":
                        text_parts.append(seg)
                        yield _norm(EV_TEXT, text=seg)
                    elif show_thinking:
                        yield _norm(EV_THINKING, text=seg)
            fragments = delta.get("tool_calls", [])
            if not isinstance(fragments, list):
                raise _ProviderResponseError()
            if gemini and isinstance(delta.get("extra_content"), dict):
                # A thought signature can arrive on the delta rather than the call.
                pending_extra = delta["extra_content"]
            for tc in fragments:
                if not isinstance(tc, dict):
                    raise _ProviderResponseError()
                idx = _gemini_tool_slot(calls, tc) if gemini else tc.get("index", 0)
                if type(idx) is not int or idx < 0:
                    raise _ProviderResponseError()
                slot = calls.setdefault(idx, {"id": None, "name": None, "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                if gemini:
                    extra = tc.get("extra_content")
                    if isinstance(extra, dict):
                        slot["extra_content"] = extra
                    elif pending_extra is not None and "extra_content" not in slot:
                        slot["extra_content"] = pending_extra
                    pending_extra = None
                fn = tc.get("function", {})
                if not isinstance(fn, dict):
                    raise _ProviderResponseError()
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if "arguments" in fn:
                    if not isinstance(fn["arguments"], str):
                        record_rejected_tool(slot["id"], slot["name"], fn["arguments"],
                                             reason="The provider must send tool arguments as JSON text.")
                        raise _ProviderResponseError()
                    slot["args"] = (_merged_whole_arguments(slot["args"], fn["arguments"]) if gemini
                                    else slot["args"] + fn["arguments"])
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
                saw_finish = True
        if gemini and finish == "stop" and calls:
            # Gemini ends a tool-call response with "stop" rather than "tool_calls".
            finish = "tool_calls"
        # Flush any held-back tail from the think splitter.
        for kind, seg in think.flush():
            if kind == "text":
                text_parts.append(seg)
                yield _norm(EV_TEXT, text=seg)
            elif show_thinking:
                yield _norm(EV_THINKING, text=seg)
        # Assemble.
        assistant_tool_calls = []
        parsed_tools = []
        bad = False
        # (id, name, parsed arguments or None, model-facing JSON error or None).
        checked: list[tuple[Any, Any, Any, str | None]] = []
        for idx in sorted(calls):
            slot = calls[idx]
            args_str = slot["args"] or "{}"
            hint = None
            try:
                parsed = json.loads(args_str)
                if not _valid_tool_call(slot["id"], slot["name"], parsed):
                    record_rejected_tool(slot["id"], slot["name"], slot["args"],
                                         reason=_tool_call_problem(slot["id"], slot["name"], parsed),
                                         finish_reason=finish if saw_finish else None)
                    raise _ProviderResponseError(retryable=finish != "length")
            except json.JSONDecodeError as exc:
                bad = True
                parsed = None
                hint = _json_error_hint(args_str, exc)
                reason = ("The output length limit interrupted the tool arguments." if finish == "length"
                          else "The stream ended before the tool arguments were complete." if not saw_finish
                          else "Tool arguments are not valid JSON.")
                record_rejected_tool(slot["id"], slot["name"], slot["args"], reason=reason,
                                     finish_reason=finish if saw_finish else None, error=exc)
            checked.append((slot["id"], slot["name"], parsed, hint))
            assistant_tool_calls.append({
                "id": slot["id"], "type": "function",
                "function": {"name": slot["name"], "arguments": args_str},
                # Gemini's thought signature must return with its call, unchanged,
                # or the next request is refused.
                **({"extra_content": slot["extra_content"]} if slot.get("extra_content") else {}),
            })
            if parsed is not None:
                parsed_tools.append({"id": slot["id"], "name": slot["name"], "input": parsed})
        # Use "" (not None) for a tool-only reply: a null content serializes to
        # JSON null, which Ollama's OpenAI-compatible endpoint rejects on the next
        # turn ("invalid message content type: <nil>"). An empty string is valid
        # for OpenAI/DeepSeek/Gemini too, so this is safe across all of them.
        answer_text = "".join(text_parts)
        if self.kind == "mistral" and mistral_content_parts:
            assistant_content: str | list[dict] = mistral_content_parts
        else:
            assistant_content = answer_text
        assistant_msg: dict = {"role": "assistant", "content": assistant_content}
        if replay_reasoning_parts:
            assistant_msg["reasoning_content"] = "".join(replay_reasoning_parts)
        if reasoning_details:
            assistant_msg["reasoning_details"] = reasoning_details
        if assistant_tool_calls:
            assistant_msg["tool_calls"] = assistant_tool_calls
        if bad:
            # The model emitted a tool call whose arguments never parse as JSON.
            # Do not dispatch garbage; end the turn with a clear error rather
            # than hanging. THREE causes, and each sends the operator somewhere
            # different, so each gets its own code (the panel localizes on the
            # code, not the message, so one code cannot carry three sentences).
            #
            # A stream that stopped on the output-token limit was TRUNCATED (for
            # example a whole-dashboard write exceeding the provider's default
            # cap), so the fix is a smaller change. A stream that never reported
            # a finish_reason did not finish: the connection dropped mid-call,
            # the fix is to send it again, and NOTHING about what the model wrote
            # was wrong. Reporting that as malformed output would send the
            # operator to inspect a config that was fine. Anything else genuinely
            # is malformed output (a known Ollama failure mode).
            if finish == "length":
                code = "tool_call_truncated"
                retryable = False
                message = (
                    "The model hit its output length limit in the middle of a tool "
                    "call, so the action was not run. The requested change was too "
                    "large to emit in one call; try asking for a smaller change."
                )
            elif not saw_finish:
                code = "tool_call_cut"
                retryable = True
                message = (
                    "The connection to the model ended in the middle of a tool "
                    "call, so the action was not run. Nothing was applied and the "
                    "request itself was not at fault; send it again."
                )
            else:
                # A finished tool-call response whose JSON is the model's own
                # syntax error. Hand it back with the position and likely missing
                # closer so the model can resend it. Replayed history carries `{}`
                # for the unparseable arguments: several OpenAI-compatible servers
                # parse historical arguments and would refuse every later request.
                event = _invalid_tool_calls(checked, {
                    **assistant_msg,
                    "tool_calls": [
                        call if hint is None else {**call, "function": {**call["function"], "arguments": "{}"}}
                        for call, (_id, _name, _parsed, hint) in zip(assistant_tool_calls, checked)
                    ],
                }) if finish == "tool_calls" else None
                if event is not None:
                    yield event
                    return
                yield _invalid_tools_error()
                return
            yield _norm(EV_ERROR, status=0, code=code,
                        message=message, retryable=retryable, diagnostic={'category': code})
            return
        if not saw_finish:
            for slot in calls.values():
                record_rejected_tool(slot["id"], slot["name"], slot["args"],
                                     reason="The provider response ended before the tool call was complete.")
            raise _ProviderResponseError("provider_incomplete")
        if finish not in ("stop", "tool_calls", "length"):
            raise _ProviderResponseError()
        if finish == "tool_calls" and not parsed_tools:
            raise _ProviderResponseError()
        if parsed_tools and finish != "tool_calls":
            raise _ProviderResponseError("provider_incomplete", retryable=finish != "length")
        if len({tool["id"] for tool in parsed_tools}) != len(parsed_tools):
            for tool in parsed_tools:
                record_rejected_tool(tool["id"], tool["name"], tool["input"],
                                     reason="The response contains duplicate tool call IDs.", finish_reason=finish)
            raise _ProviderResponseError()
        for tool_call in parsed_tools:
            yield _norm(EV_TOOL, id=tool_call["id"], name=tool_call["name"],
                        input=tool_call["input"])
        yield _norm(EV_DONE, stop_reason=_norm_stop(finish), assistant_msg=assistant_msg)


def _norm_stop(raw: str) -> str:
    """Normalize provider stop reasons to end_turn / tool_use / max_tokens / error."""
    return {
        "end_turn": "end_turn", "tool_use": "tool_use", "max_tokens": "max_tokens",
        "stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens",
    }.get(raw, "end_turn")


def build_provider(cfg: ProviderConfig) -> ClaudeProvider | OpenAICompatProvider:
    definition = PROVIDER_DEFINITIONS.get(cfg.kind)
    if definition is None:
        raise ValueError(f"Unknown provider kind: {cfg.kind}")
    if definition.purpose != "conversation":
        raise ValueError("Decision accounts cannot provide conversation or AI Task responses.")
    if definition.adapter == "anthropic":
        return ClaudeProvider(cfg)
    return OpenAICompatProvider(cfg)


# --------------------------------------------------------------------------- #
# Tool list (mirrors the MCP tools/list gating) and result helpers
# --------------------------------------------------------------------------- #

# Orientation and everyday tools listed first: models weight early tools more
# (weak local ones especially), and the system prompt tells the agent to orient
# via get_capability_summary/get_overview/search_entities before acting, so
# those should not sit buried mid-list. Everything else keeps its source order
# (the sort is stable). Deterministic across turns, so it never busts the
# provider prompt cache.
_TOOL_PRIORITY = (
    "get_capability_summary", "get_overview", "search_entities", "recognize_intent",
    "describe_entity", "describe_area", "get_state",
    "call_service", "dry_run_service", "find_available_actions",
)


def build_mcp_tool_list(access_key: AccessKeyRecord, data: PhoenixData) -> list[dict]:
    """The tool defs this access key would announce, cap key stripped (mirrors tools/list)."""
    from .mcp_view import (  # noqa: PLC0415
        _ENTITY_TOOL_DEFS, _NATIVE_TOOL_DEFS, _SYSTEM_TOOL_DEFS, _tool_is_announced,
    )
    from .mesa_tools import mesa_tool_defs  # noqa: PLC0415
    from .household_authority import household_tool_access

    from .home_companion import is_companion, internal_turn, wrapper_tool
    if is_companion(access_key) and not internal_turn(access_key):
        return [wrapper_tool()]
    announce_all = getattr(access_key, "announce_all_tools", False) and not is_companion(access_key)
    has_write = access_key_has_write_scope(access_key)
    hass = getattr(data, "hass", None)
    mesa_defs = mesa_tool_defs() if getattr(data, "mesa", None) is not None else []
    out = []
    for d in list(_ENTITY_TOOL_DEFS) + list(_NATIVE_TOOL_DEFS) + list(_SYSTEM_TOOL_DEFS) + mesa_defs:
        if household_tool_access(d["name"]) and (announce_all or _tool_is_announced(d, access_key, has_write, cast(Any, hass))):
            out.append({k: v for k, v in d.items() if k not in ("cap", "caps", "caps_any", "requires")})
    if is_companion(access_key):
        from .const import HOME_COMPANION_TOOLS
        out = [tool for tool in out if tool["name"] in HOME_COMPANION_TOOLS]
    rank = {name: i for i, name in enumerate(_TOOL_PRIORITY)}
    out.sort(key=lambda t: rank.get(t["name"], len(rank)))
    return out


def build_agent_tool_list(access_key: AccessKeyRecord, data: PhoenixData, surface: str) -> list[dict]:
    """Share each planner's complete announced catalog with tool review."""
    tools = build_mcp_tool_list(access_key, data)
    # Chat already supplies current local time in every turn's system context.
    return deepcopy([tool for tool in tools if surface != "chat"
                     or tool.get("name") not in ("llm__GetDateTime", "GetDateTime")])


def catalog_payload_metrics(access_key: AccessKeyRecord, data: PhoenixData) -> dict[str, int]:
    """Return deterministic byte metrics for the access key's announced catalog."""
    tools = build_mcp_tool_list(access_key, data)
    canonical = json.dumps(tools, sort_keys=True, separators=(",", ":")).encode()
    claude = ClaudeProvider(ProviderConfig("claude", "metrics", "")).format_tools(tools)
    openai = OpenAICompatProvider(ProviderConfig("chatgpt", "metrics", "")).format_tools(tools)
    return {
        "tool_count": len(tools),
        "canonical_bytes": len(canonical),
        "claude_bytes": len(json.dumps(claude, sort_keys=True, separators=(",", ":")).encode()),
        "openai_bytes": len(json.dumps(openai, sort_keys=True, separators=(",", ":")).encode()),
    }


def _clip_display(text: str) -> str:
    """Cap tool-result text for the verbose panel view; the model still gets the
    full result via the message history."""
    if len(text) <= AGENTCLI_TOOL_RESULT_MAX_CHARS:
        return text
    return text[:AGENTCLI_TOOL_RESULT_MAX_CHARS] + f"\n… (truncated, {len(text)} chars total)"


_MODEL_IMAGE_FALLBACK = (
    "The image is displayed to the operator, but this provider/model is not known "
    "to support visual input. Do not infer or describe image contents."
)


def _model_result_text(result: dict, vision: bool) -> str:
    text = str(result.get("result_text") or "")
    if result.get("images") and not vision:
        return f"{text}\n{_MODEL_IMAGE_FALLBACK}".strip()
    return text


def _extract_image_blocks(content: Any) -> list[dict[str, str]]:
    """Validate and normalize MCP image blocks for internal consumers."""
    images: list[dict[str, str]] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        data = item.get("data")
        mime_type = str(item.get("mimeType") or "").lower().split(";", 1)[0].strip()
        if not isinstance(data, str) or mime_type not in {"image/jpeg", "image/png", "image/gif"}:
            continue
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            continue
        if not decoded or len(decoded) > 4 * 1024 * 1024:
            continue
        images.append({"data": data, "mime_type": mime_type})
    return images


# JPEG start-of-frame markers carry the dimensions; C4, C8 and CC share the
# range but are table and extension markers.
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _image_kind_and_size(data: bytes) -> tuple[str, int, int] | None:
    """The MIME type and pixel size an image's own header declares, or None.

    Read from the bytes rather than trusted from the request, so a renamed or
    truncated file is refused before it reaches a provider.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return "image/gif", int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if not data.startswith(b"\xff\xd8\xff"):
        return None
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:
            index += 2
            continue
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            if index + 9 > len(data):
                return None
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return "image/jpeg", width, height
        index += 2 + length
    return None


_MIB = 1024 * 1024


def _attachments_unreadable(rid: str) -> web.Response:
    return _err("invalid_request", "The attached images could not be read.", 400, rid, key="attachmentsInvalid")


def _attachment_too_large(rid: str) -> web.Response:
    return _err("invalid_request", f"An attached image is larger than {AGENTCLI_ATTACHMENT_MAX_BYTES // _MIB} MB.",
                400, rid, key="attachmentTooLarge", params={"max": AGENTCLI_ATTACHMENT_MAX_BYTES // _MIB})


# Base64 characters every image in one message may use, with room for each
# image part's data-URI prefix.
_ATTACHMENT_BUDGET_CHARS = -(-AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES * 4 // 3) + 1024


_UNSEEN_IMAGE_NOTE = "[An image was attached here, but this model cannot see images.]"


def _without_history_images(messages: list) -> None:
    """Replace every image part in a request's history with a short text note.

    For a model known not to see images: a refused image would otherwise be
    resent with the history and refused again on every later message. Covers
    attachments in user messages and camera images inside tool results. Edits
    the request's own parsed body in place, iteratively, so a deeply nested
    history cannot exhaust the stack.
    """
    stack: list[Any] = [messages]
    while stack:
        item = stack.pop()
        slots = enumerate(item) if isinstance(item, list) else item.items() if isinstance(item, dict) else ()
        for slot, child in list(slots):
            if isinstance(child, dict) and child.get("type") in ("image", "image_url"):
                item[slot] = {"type": "text", "text": _UNSEEN_IMAGE_NOTE}
            elif isinstance(child, (list, dict)):
                stack.append(child)


def _image_payload_chars(value: Any) -> int:
    """Base64 characters of the image parts in a chat history.

    The browser resends the newest attached images (and camera results) with
    the history, so those ride in the attachment budget instead of the ordinary
    body cap. Iterative, so a deeply nested history cannot exhaust the stack.
    """
    total = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            source = item.get("source")
            url = item.get("image_url")
            if item.get("type") == "image" and isinstance(source, dict) and isinstance(source.get("data"), str):
                total += len(source["data"])
            elif item.get("type") == "image_url" and isinstance(url, (dict, str)):
                url = url.get("url") if isinstance(url, dict) else url
                total += len(url) if isinstance(url, str) else 0
            else:
                stack.extend(item.values())
    return total


def _chat_attachments(value: Any, rid: str) -> list[dict[str, str]] | web.Response:
    """Validate the images an administrator attached to one chat message.

    Returns the normalized images, or the refusal to send. Every image must
    decode, match the type its header declares and fit the count, size and
    dimension bounds; one bad image refuses the message rather than sending the
    rest.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return _attachments_unreadable(rid)
    if len(value) > AGENTCLI_ATTACHMENT_MAX_COUNT:
        return _err("invalid_request", f"Attach at most {AGENTCLI_ATTACHMENT_MAX_COUNT} images to one message.",
                    400, rid, key="attachmentsTooMany", params={"max": AGENTCLI_ATTACHMENT_MAX_COUNT})
    images: list[dict[str, str]] = []
    total = 0
    for item in value:
        data = item.get("data") if isinstance(item, dict) else None
        declared = item.get("mime_type") if isinstance(item, dict) else None
        if not isinstance(data, str) or not isinstance(declared, str):
            return _attachments_unreadable(rid)
        if len(data) > -(-AGENTCLI_ATTACHMENT_MAX_BYTES * 4 // 3):
            return _attachment_too_large(rid)
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            return _attachments_unreadable(rid)
        if len(decoded) > AGENTCLI_ATTACHMENT_MAX_BYTES:
            return _attachment_too_large(rid)
        header = _image_kind_and_size(decoded)
        if header is None or header[0] != declared.lower().strip():
            return _err("invalid_request", "Attach a JPEG, PNG or GIF image.", 400, rid, key="attachmentUnsupported")
        kind, width, height = header
        if not (0 < width <= AGENTCLI_ATTACHMENT_MAX_EDGE_PX and 0 < height <= AGENTCLI_ATTACHMENT_MAX_EDGE_PX):
            return _err("invalid_request",
                        f"An attached image is larger than {AGENTCLI_ATTACHMENT_MAX_EDGE_PX} pixels on a side.",
                        400, rid, key="attachmentDimensions", params={"max": AGENTCLI_ATTACHMENT_MAX_EDGE_PX})
        total += len(decoded)
        if total > AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES:
            return _err("invalid_request",
                        f"The attached images are larger than {AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES // _MIB} MB together.",
                        400, rid, key="attachmentsTooLarge", params={"max": AGENTCLI_ATTACHMENT_MAX_TOTAL_BYTES // _MIB})
        images.append({"data": data, "mime_type": kind})
    return images


def _flatten_text(content: list[dict]) -> str:
    return "\n".join(
        c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
    )


_AGENTCLI_LOCAL_TIME_FIELDS: dict[str, frozenset[str]] = {
    "get_state": frozenset({"last_changed", "last_updated", "last_reported"}),
    "get_history": frozenset({
        "start", "end", "when", "last_changed", "last_updated", "last_reported",
        "first_transition", "last_transition", "first_entered", "last_entered",
    }),
    "get_statistics": frozenset({"start", "end", "last_reset"}),
    "get_logbook": frozenset({"start_time", "end_time", "when"}),
    "recent_activity": frozenset({"when"}),
    "get_automation_traces": frozenset({"start", "finish"}),
}


def _agentcli_local_time(value: Any, local_tz: tzinfo) -> str | None:
    """Return a local ISO companion for one recognized wire timestamp."""
    parsed: datetime | None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=dt_util.UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
    else:
        return None
    return parsed.astimezone(local_tz).isoformat()


def _agentcli_add_local_time_fields(
    tool_name: str, text: str, local_tz: tzinfo,
) -> str:
    """Add local companions without changing canonical tool-result timestamps.

    This is an Agent Chat-only presentation layer. Cursor values deliberately get
    no companion because they are opaque protocol inputs for a subsequent call.
    """
    fields = _AGENTCLI_LOCAL_TIME_FIELDS.get(tool_name)
    if fields is None:
        return text
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text

    def add_companions(value: Any) -> Any:
        if isinstance(value, list):
            return [add_companions(item) for item in value]
        if not isinstance(value, dict):
            return value
        augmented: dict[str, Any] = {}
        for key, item in value.items():
            augmented[key] = add_companions(item)
            companion = _agentcli_local_time(item, local_tz) if key in fields else None
            companion_key = f"{key}_local"
            if companion is not None and companion_key not in value:
                augmented[companion_key] = companion
        return augmented

    return json.dumps(add_companions(payload), separators=(",", ":"), ensure_ascii=False)


_AGENTCLI_RESOURCE_INVENTORY_TOOLS = frozenset({
    "list_automations", "list_scripts", "list_scenes", "list_helpers", "list_devices",
})


def _agentcli_add_resource_match_guidance(
    tool_name: str, args: dict[str, Any], text: str,
) -> str:
    """Repeat Agent Chat's premise guard beside discovery evidence for weaker models.

    This provider-only suffix is not emitted in the visible tool summary and never changes the
    external MCP result. Inventory lists always carry the conditional reminder. A filtered entity
    search carries it only on a structurally proven zero match.
    """
    guidance: str | None = None
    if tool_name in _AGENTCLI_RESOURCE_INVENTORY_TOOLS:
        guidance = (
            "If you called this inventory to identify a resource named by the operator and no "
            "plausible match appears, stop now. State the mismatch and ask one focused clarifying "
            "question. Do not substitute a related entry or inspect logs, history, or traces for "
            "a guessed target."
        )
    elif tool_name == "search_entities" and str(args.get("query") or "").strip():
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("count") == 0 \
                and payload.get("entities") == []:
            guidance = (
                "This filtered discovery found no matching entity. If the current request depends "
                "on that named resource, stop now, state the mismatch, and ask one focused "
                "clarifying question. Do not substitute a related entity or search logs, history, "
                "or traces for a guessed target."
            )
    if guidance is None:
        return text
    return f"{text}\n\nPhoenix Agent Chat factual guard: {guidance}"


_AGENTCLI_LOCAL_QUERY_FIELDS: dict[str, frozenset[str]] = {
    "get_history": frozenset({"start_time", "end_time"}),
    "get_statistics": frozenset({"start_time", "end_time"}),
    "get_logbook": frozenset({"start_time", "end_time"}),
}


def _agentcli_prepare_time_args(
    tool_name: str, args: dict[str, Any], local_tz: tzinfo,
) -> tuple[dict[str, Any], str | None]:
    """Apply HA's zone to naive Agent Chat ranges and reject manual shifting."""
    fields = _AGENTCLI_LOCAL_QUERY_FIELDS.get(tool_name)
    if fields is None:
        return args, None
    prepared = dict(args)
    for field in fields:
        value = prepared.get(field)
        if not isinstance(value, str):
            continue
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return args, (
                f"Agent Chat {field} must be a Home Assistant local wall time without "
                "a timezone suffix, or a relative time. Pass the operator's intended "
                "clock time directly; Phoenix applies the configured time zone."
            )
        prepared[field] = parsed.replace(tzinfo=local_tz).isoformat()
    return prepared, None


def dispatch_tool_result(response: dict | None) -> dict:
    """Keep dispatcher refusals visible to internal callers as tool failures."""
    if response and "error" in response:
        return {"isError": True, "content": [{
            "type": "text", "text": "The tool request was refused by Phoenix MCP.",
        }]}
    return (response or {}).get("result", {}) or {}


def _parse_pending(
    result: dict,
    *,
    outcome: str,
    data: PhoenixData,
    access_key_id: str,
    tool_name: str,
    request_id: str,
) -> dict | None:
    """Return trusted control data for this exact pending approval dispatch."""
    if result.get("isError") or outcome != "pending_approval":
        return None
    approval_id = None
    for item in result.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            parsed = json.loads(item.get("text", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(parsed, dict)
            and parsed.get("status") == "pending_approval"
            and isinstance(parsed.get("approval_id"), str)
            and parsed["approval_id"]
        ):
            approval_id = parsed["approval_id"]
            break
    if approval_id is None:
        return None

    record = get_approval(data.store, approval_id)
    if record is None:
        return None
    from .tool_defs import _NATIVE_TOOL_PUBLIC_TO_INTERNAL  # noqa: PLC0415

    internal_name = _NATIVE_TOOL_PUBLIC_TO_INTERNAL.get(tool_name, tool_name)
    if (
        record.status != STATUS_PENDING
        or record.expires_at <= utcnow()
        or record.access_key_id != access_key_id
        or record.tool_name not in (internal_name, MESA_APPROVED_EXECUTOR)
        or record.request_id != request_id
    ):
        return None
    return {
        "approval_id": record.id,
        "review_url": f"/phoenix-mcp/approvals/{record.id}",
        "expires_at": record.expires_at.isoformat(),
        "record": record,
    }


def execution_failure_text(approval: Any) -> str | None:
    """The executor's error text for an approved action whose execution failed, else None.

    The approve path stores the error result and resolves the record as failed
    with the "execution_failed" slug. Reporting that case as a bare "rejected"
    would make an agent treat an approval as a refusal and loop retrying the same
    doomed call instead of fixing the reported cause. None for a real admin
    rejection or any other state.
    """
    if getattr(approval, "rejected_reason", None) != "execution_failed":
        return None
    result = getattr(approval, "result", None)
    if not isinstance(result, dict):
        return None
    tool_result = result.get("tool_result")
    if not isinstance(tool_result, dict):
        return None
    return _flatten_text(tool_result.get("content", [])).strip() or None


def _resolved_result(status: str, applied_result: dict | None, approval: Any = None) -> dict:
    """A CallToolResult to feed back after an interactive approval resolves.

    On a reject/cancel it surfaces the admin's rejection reason (set from the
    Approvals panel) to the agent, mirroring tool_common._tool_inline_resolved, so
    a rejection with a reason is actually reported instead of a generic notice.
    An approved-but-execution-failed record is reported as exactly that, with
    the executor's error, never as a plain rejection. An approved result carries
    the operator-accepted note (tool_common._operator_accepted_result) so the
    agent treats the approved change as settled instead of iterating on it.
    """
    if status == STATUS_APPROVED and applied_result is not None:
        from .tool_common import _operator_accepted_result  # noqa: PLC0415

        return _operator_accepted_result(applied_result)
    if status == STATUS_FAILED:
        failure = execution_failure_text(approval)
        reason = getattr(approval, "rejected_reason", None)
        if failure is not None:
            message = (
                f"The operator APPROVED this action, but executing it failed: {failure} "
                "Do not retry the same call unchanged; fix the reported cause first "
                "(for example, if it reports a stale expected_hash, re-read the "
                "resource for the current content_hash)."
            )
            response_status = "execution_failed"
        elif reason == "execution_interrupted":
            message = (
                "The operator APPROVED this action and execution started, but its "
                "final outcome was interrupted. It may have partly applied. Check "
                "the real-world result before proposing any retry."
            )
            response_status = STATUS_FAILED
        else:
            detail = f" Reason: {reason}." if reason else ""
            message = (
                f"The operator approved this action, but it could not complete.{detail} "
                "Do not treat this as an operator rejection or retry it unchanged."
            )
            response_status = STATUS_FAILED
        return {"content": [{"type": "text", "text": json.dumps({
            "status": response_status,
            "message": message,
        })}], "isError": True}
    if status in (STATUS_REJECTED, STATUS_CANCELLED):
        failure = execution_failure_text(approval)
        if failure is not None:
            return {"content": [{"type": "text", "text": json.dumps({
                "status": "execution_failed",
                "message": (
                    f"The operator APPROVED this action, but executing it failed: {failure} "
                    "Do not retry the same call unchanged; fix the reported cause first "
                    "(for example, if it reports a stale expected_hash, re-read the "
                    "resource for the current content_hash)."
                ),
            })}], "isError": True}
        # A rejection with a reason is the operator STEERING, not stopping:
        # iterating on a proposal through several reject-with-reason rounds is
        # the normal way a card gets built. Do not add a strict alternative
        # (forbid variations, escalate to a hard stop on the second rejection):
        # models turn pedantic, give up, or side-step the tools entirely (dump
        # raw data into chat, build an HTML chart instead). Only a reasonless
        # rejection means "do not just try again".
        reason = getattr(approval, "rejected_reason", None)
        if status == STATUS_REJECTED and reason:
            message = (
                f"This action was not applied (it was rejected). Reason: {reason}. "
                "That reason is the operator's direction for what to do next: "
                "address it in your next proposal instead of resubmitting the "
                "same change."
            )
        elif status == STATUS_REJECTED:
            message = (
                "This action was not applied (it was rejected, no reason given). "
                "Do not resubmit the same change; ask the operator how they "
                "would like to proceed."
            )
        else:
            detail = f" Reason: {reason}." if reason else ""
            message = (
                f"This action was not applied (it was cancelled).{detail} "
                "Do not retry it; report the outcome to the user."
            )
        return {"content": [{"type": "text", "text": json.dumps({
            "status": status,
            "message": message,
        })}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps({
        "status": "pending_approval",
        "message": "Still awaiting approval; it was not applied in this session. Do not retry.",
    })}]}


async def _dispatch_with_progress(emit: Any, tool_id: Any, coro: Any) -> Any:
    """Run one tool call, relaying whatever it reports while it is still running.

    Tools that hold the request open (a firmware build, a confirm gate) describe
    what they are waiting on through the progress bus in tool_common, which
    exists so an SSE-framed MCP request can emit notifications/progress. Agent Chat is the
    other consumer of the same signal: it owns both ends of its own stream, so it
    needs no progressToken negotiation and can always show the line.

    The bus is set BEFORE the task is created so the task's copied context holds
    the same object, matching how mcp_view's SSE writer does it. Only changed
    statuses are emitted, so a slow build does not spam the transcript.
    """
    from .tool_common import _ProgressBus, _progress_ctx  # noqa: PLC0415

    bus = _ProgressBus()
    _progress_ctx.set(bus)
    task = asyncio.ensure_future(coro)
    last: str | None = None
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=AGENTCLI_PROGRESS_INTERVAL_SECONDS)
            if done:
                return await task
            if bus.status and bus.status != last:
                last = bus.status
                await emit("tool_progress", {
                    "id": tool_id,
                    "message": bus.status,
                    **({"message_key": bus.status_key} if bus.status_key else {}),
                    **({"message_params": bus.status_params} if bus.status_params else {}),
                })
    finally:
        _progress_ctx.set(None)
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _cancel_agent_approval(hass: HomeAssistant, data: PhoenixData, approval_id: str) -> Any:
    """Cancel only abandoned pending work whose executor has never been claimed."""
    async with data.store.async_lock:
        record = get_approval(data.store, approval_id)
        if (record is None or record.status != STATUS_PENDING
                or record.execution_started_at is not None
                or approval_id in data.approvals_in_progress):
            return None
        resolved = await async_update_approval_status(
            data.store, approval_id, status=STATUS_CANCELLED,
            rejected_reason=REASON_AGENT_CHAT_ENDED,
        )
    if resolved is not None and resolved.status == STATUS_CANCELLED:
        dismiss_approval_notification(hass, approval_id)
        fire_approval_resolved_event(hass, resolved)
    return resolved


async def _await_agent_approval(
    hass: HomeAssistant, data: PhoenixData, approval_id: str, cancel: asyncio.Event, timeout: float,
) -> dict:
    """Block until an approval resolves (or cancel/timeout), then return a result.

    Modeled on tool_common._await_inline_confirm but tied to the live SSE
    connection (long timeout) and cancel-aware. The bus event carries only IDs, so the
    executed result is re-read from the persisted approval record.
    """
    future: asyncio.Future = hass.loop.create_future()

    @callback
    def _on_resolved(event: Any) -> None:
        # @callback so HA runs this in the event loop (not the executor thread);
        # otherwise future.set_result would never wake the awaiting loop.
        if event.data.get("approval_id") == approval_id and not future.done():
            future.set_result(True)

    unsub = hass.bus.async_listen(f"{DOMAIN}_approval_resolved", _on_resolved)
    cancel_task = hass.loop.create_task(cancel.wait())
    try:
        await asyncio.wait({future, cancel_task}, timeout=timeout,
                           return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        await _cancel_agent_approval(hass, data, approval_id)
        raise
    finally:
        unsub()
        if not cancel_task.done():
            cancel_task.cancel()

    latest = get_approval(data.store, approval_id)
    if latest is None:
        return _resolved_result(STATUS_PENDING, None)
    if latest.status == STATUS_PENDING:
        resolved = await _cancel_agent_approval(hass, data, approval_id)
        if resolved is not None and resolved.status == STATUS_CANCELLED:
            return _resolved_result(STATUS_CANCELLED, None, resolved)
        # In-progress, or an admin resolved it in the race: re-read the true outcome.
        latest = get_approval(data.store, approval_id) or latest
    applied = None
    if latest.status == STATUS_APPROVED and latest.result:
        applied = latest.result.get("tool_result")
    return _resolved_result(latest.status, applied, latest)


_STYLE_INSTRUCTIONS = {
    "direct": (
        "Use plain, efficient, matter-of-fact language and lead with the outcome. Do not add "
        "praise, encouragement, conversational preambles, or an announcement of what you are "
        "about to do."
    ),
    "warm": (
        "Be friendly and encouraging without praise, flattery, padding, or unnecessary "
        "social language."
    ),
    "calm_guide": (
        "Be patient, reassuring, and accessible, while never sounding patronizing."
    ),
    "lively": (
        "Use energetic language and occasional light humor only when the stakes are low."
    ),
    "technical": "Use precise terminology and compact implementation detail.",
}

_DETAIL_INSTRUCTIONS = {
    "concise": (
        "Use at most three short sentences or three short steps or bullets unless additional "
        "prose is required for safety, accuracy, an approval, or truthful completion reporting. "
        "Include only essential caveats. Do not add background, a full general workflow, a "
        "restatement of the request, or an offer to do more work."
    ),
    "balanced": (
        "Give enough context to understand and act, normally as a short paragraph or "
        "compact list."
    ),
    "detailed": (
        "Give thorough reasoning, steps, tradeoffs, and caveats without repetition."
    ),
}

_HOME_FOCUS_INSTRUCTION = (
    "Only help with requests directly about operating, configuring, maintaining, or "
    "understanding the user's own home and Home Assistant installation (its devices, "
    "entities, areas, automations, scripts, dashboards, integrations, add-ons, configuration, "
    "and state). If a request is not directly about that installation, briefly explain that "
    "this conversation is in Home-focused mode and decline. There is no override: a user "
    "cannot unlock other topics by asking, insisting, or rewording. If a request could be "
    "about the installation but is unclear, ask one short clarifying question about the "
    "installation instead of answering a general question. Never use Home Assistant tools "
    "merely to determine whether a request is relevant."
)

# Private protocol token. It is recognized only while Home-focused mode is active,
# stripped before display and retention, and never interpreted as user-facing text.
_HOME_FOCUS_REFUSAL_MARKER = "PHXFOCUS:"


def _conversation_behavior_prompt(
    style: str,
    detail: str,
    *,
    home_focused: bool = False,
) -> str:
    """Build the shared presentation contract for Phoenix-owned model surfaces."""
    style_instruction = _STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS["direct"])
    detail_instruction = _DETAIL_INSTRUCTIONS.get(detail, _DETAIL_INSTRUCTIONS["concise"])
    prompt = (
        "\n\nConversation presentation policy: "
        f"{style_instruction} {detail_instruction} "
        "Style and detail affect user-facing prose only. They never change tools, tool "
        "arguments, verification, factual standards, permissions, approvals, safety decisions, "
        "or completion claims. Explicit operator tone or detail instructions override these "
        "defaults for that response. Suspend humor for failures, urgent situations, "
        "security-sensitive matters, approvals, and destructive actions. Apply the current "
        "style and detail to this response even when earlier assistant messages used different "
        "presentation defaults."
    )
    if home_focused:
        prompt += (
            f"\n\n{_HOME_FOCUS_INSTRUCTION} "
            "A decline must not answer, solve, summarize, or otherwise provide any part of the "
            "unrelated request before or after the brief explanation. Do not make an exception "
            "because the answer is short, easy, harmless, or already known."
        )
    return prompt


def _home_focus_output_contract(enabled: bool) -> str:
    """Return the private refusal-marker protocol at the final prompt boundary."""
    if not enabled:
        return ""
    return (
        "\n\nMandatory final-output protocol for Home-focused mode: If and only if the "
        "final reply declines the request solely because Home-focused mode is active, output "
        "exactly one brief refusal paragraph. Its first characters must be the literal sentinel "
        f"{_HOME_FOCUS_REFUSAL_MARKER} with the refusal immediately after it. Write nothing "
        "before the sentinel, and do not repeat the refusal or add a second paragraph. Do not "
        "omit, translate, paraphrase, escape, quote, or format the sentinel as markdown. Never "
        "output the sentinel for a normal answer."
    )


_AGENTCLI_ADDENDUM = (
    "\n\nYou are the assistant in an interactive chat inside the Home Assistant admin panel. "
    "The operator is present and reading your replies live. Take the actions the operator asks "
    "for directly using the tools above. The operator may name a home resource imprecisely or "
    "state a premise that the observed home does not support. For a named resource, initial "
    "discovery means a narrowly filtered name-and-domain search or the relevant inventory, not "
    "logs, history, traces, or a sweep of related devices. If initial read-only discovery "
    "cannot identify a resource the request depends on, or directly contradicts such a premise, "
    "stop the broad investigation, state the mismatch, and ask one focused clarifying question. "
    "Do not substitute a merely related entity, integration error, or temporal coincidence. "
    "Treat correlation as a lead, never as a cause. If an action needs approval "
    "it will surface an inline Approve/Reject control to the operator; you do not need to poll. "
    "An approved action's result stands as accepted exactly as applied: do not second-guess or "
    "adjust it unprompted. A rejection's reason is the operator steering your next proposal: "
    "address it and try again. Iterating through several proposals and rejections is the normal "
    "way to converge on what the operator wants; stay patient and constructive."
)


def _agentcli_time_context(hass: HomeAssistant) -> str:
    """Tell Agent Chat how to present Home Assistant timestamps locally."""
    now = dt_util.now()
    offset = now.strftime("%z")
    offset_label = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else "UTC"
    return (
        "\n\nHome Assistant's local time zone is "
        f"{hass.config.time_zone} ({offset_label}). The current local date and time at the "
        f"start of this turn is {now.strftime('%Y-%m-%d %H:%M:%S')}. Common temporal tool "
        "results preserve their canonical timestamps and add authoritative companion fields "
        "whose names end in _local. Use the _local value when reasoning or reporting; never "
        "calculate a local value from its canonical counterpart. For get_history, "
        "get_statistics, and get_logbook absolute time arguments, send the intended Home "
        "Assistant wall time without a timezone suffix, for example 2026-08-14T05:00:00. "
        "Phoenix applies the configured zone; do not shift the window yourself. State dates "
        "and times matter-of-factly. Do not mention local time, UTC, offsets, or conversion "
        "unless the operator explicitly asks."
    )


# --------------------------------------------------------------------------- #
# The agent loop
# --------------------------------------------------------------------------- #

async def _cancelable_provider_events(events: Any, cancel: asyncio.Event) -> AsyncGenerator[dict, None]:
    """Close the HTTP generator even when cancellation arrives during a quiet stream."""
    stopped = asyncio.create_task(cancel.wait())
    next_event: asyncio.Task | None = None
    try:
        while not cancel.is_set():
            next_event = asyncio.create_task(anext(events))
            done, _ = await asyncio.wait((next_event, stopped), return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                return
            try:
                event = next_event.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        if next_event is not None:
            next_event.cancel()
        stopped.cancel()
        await asyncio.gather(*([next_event] if next_event is not None else []),
                             stopped, return_exceptions=True)
        await events.aclose()


def _after_dispatch_failure(ev: dict, write_started: bool) -> dict:
    """A provider failure after this turn dispatched a tool: never retried, uncertain after a write.

    Reads still stop automatic retries but cannot create an uncertain device
    action, so only a write is reported as outcome unknown. Classified from
    server annotations, never from the model.
    """
    ev = {**ev, "retryable": False, "dispatch_started": True}
    if write_started:
        ev = {**ev, "code": "provider_after_dispatch", "outcome": "outcome_unknown",
              "message": "The provider failed after tool dispatch. Earlier actions may have run. Reconcile Home Assistant and approval results before retrying."}
    return ev


async def _stream_turn_resilient(
    provider: ClaudeProvider | OpenAICompatProvider, session: ClientSession, *,
    system_prompt: str, messages: list[dict], tools: list[dict], options: dict,
    cancel: asyncio.Event,
    dispatch_started: bool = False,
    write_started: bool = False,
) -> AsyncIterator[dict]:
    """provider.stream_turn wrapped with a bounded retry for a transient connection failure.

    Covers a failure (e.g. a DNS/connect timeout) that occurs BEFORE any content
    is produced: retries with a short backoff until _CONNECT_RETRY_WINDOW_SECONDS
    elapses, then surfaces the error (annotated that it retried). Never retries
    once any content/tool/done event has been seen (that could duplicate
    already-streamed output) or once a tool dispatch has started; a mid-stream or
    non-retryable error passes straight through. Every other event is yielded
    unchanged.
    """
    loop_time = asyncio.get_running_loop().time
    deadline = loop_time() + _CONNECT_RETRY_WINDOW_SECONDS
    attempt = 0
    while not cancel.is_set():
        attempt += 1
        produced = False
        retry = False
        events = _cancelable_provider_events(provider_events(provider.stream_turn(
            session, system_prompt=system_prompt, messages=messages, tools=tools, options=options,
        ), config=getattr(provider, "cfg", None), messages=messages), cancel)
        async with aclosing(events):
            async for ev in events:
                etype = ev.get("type")
                if (etype == EV_ERROR and not produced and not dispatch_started and ev.get("retryable")
                        and attempt < _CONNECT_RETRY_MAX_ATTEMPTS and not cancel.is_set()
                        and loop_time() + _CONNECT_RETRY_BACKOFF_SECONDS < deadline):
                    retry = True
                    break
                if etype == EV_ERROR and dispatch_started:
                    ev = _after_dispatch_failure(ev, write_started)
                if etype == EV_ERROR and attempt > 1:
                    detail = str(ev.get("message") or "").strip()
                    seconds = int(_CONNECT_RETRY_WINDOW_SECONDS)
                    explicit_rate_limit = (
                        ev.get("status") == 429 and ev.get("code") == "rate_limit"
                    )
                    if explicit_rate_limit:
                        code = "provider_rate_limit_exhausted"
                        message = (
                            f"The provider's rate limit remained exceeded after retrying for about "
                            f"{seconds}s. Provider detail: {detail}"
                        )
                    else:
                        code = "provider_retry_exhausted"
                        message = (
                            f"Could not reach the provider after retrying for about "
                            f"{seconds}s. {detail}"
                        )
                    ev = {
                        **ev,
                        "code": code,
                        "message": message.strip(),
                        "message_params": {"seconds": seconds, "detail": detail},
                    }
                if etype in (EV_TEXT, EV_THINKING, EV_TOOL, EV_DONE, EV_INVALID_TOOLS):
                    produced = True
                yield ev
        if not retry or cancel.is_set():
            return
        try:
            await asyncio.wait_for(cancel.wait(), timeout=_CONNECT_RETRY_BACKOFF_SECONDS)
        except asyncio.TimeoutError:
            pass


@dataclasses.dataclass
class _ModelCallResult:
    """Everything one model call produced, drained from its event stream."""

    tool_calls: list[dict]
    assistant_msg: dict | None
    stop_reason: str
    errored: bool
    saw_text: bool
    usage_input: int
    usage_output: int
    context_tokens: int
    focus_declined: bool
    # The EV_INVALID_TOOLS event when the response's tool arguments did not parse.
    invalid_tools: dict | None = None


def _strip_focus_marker_text(text: str) -> tuple[str, bool]:
    """Strip a leading private focus marker while preserving ordinary model text."""
    stripped = text.lstrip()
    if not stripped.startswith(_HOME_FOCUS_REFUSAL_MARKER):
        return text, False
    return stripped[len(_HOME_FOCUS_REFUSAL_MARKER):].lstrip(), True


def _strip_focus_marker_message(message: dict) -> tuple[dict, bool]:
    """Strip the marker from Anthropic, OpenAI, or Mistral assistant content."""
    content = message.get("content")
    if isinstance(content, str):
        cleaned, found = _strip_focus_marker_text(content)
        return ({**message, "content": cleaned} if found else message), found
    if not isinstance(content, list):
        return message, False
    for index, block in enumerate(content):
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        if not block["text"].strip():
            continue
        cleaned, found = _strip_focus_marker_text(block["text"])
        if not found:
            return message, False
        blocks = list(content)
        blocks[index] = {**block, "text": cleaned}
        return {**message, "content": blocks}, True
    return message, False


@dataclasses.dataclass
class _FocusPrefixBuffer:
    """Hold only enough leading streamed text to recognize the private marker."""

    enabled: bool
    pending: str = ""
    decided: bool = False
    declined: bool = False

    def feed(self, text: str) -> str:
        if not self.enabled or self.decided:
            return text
        self.pending += text
        candidate = self.pending.lstrip()
        # The marker check comes first: some providers stream whole sentences,
        # so the marker can arrive inside a chunk longer than the hold limit.
        bounded = len(self.pending) <= len(_HOME_FOCUS_REFUSAL_MARKER) + 64
        if candidate.startswith(_HOME_FOCUS_REFUSAL_MARKER):
            if bounded and not candidate[len(_HOME_FOCUS_REFUSAL_MARKER):].strip():
                # Wait for the refusal itself so its leading space is stripped too.
                return ""
            self.decided = True
            self.declined = True
            visible, _ = _strip_focus_marker_text(self.pending)
            self.pending = ""
            return visible
        # Hold only text that could still become the marker, and only a bounded
        # run of leading whitespace before it.
        if bounded and _HOME_FOCUS_REFUSAL_MARKER.startswith(candidate):
            return ""
        self.decided = True
        visible = self.pending
        self.pending = ""
        return visible

    def finish(self) -> str:
        if not self.pending:
            return ""
        visible, declined = _strip_focus_marker_text(self.pending)
        self.pending = ""
        self.decided = True
        self.declined = self.declined or declined
        return visible


async def _consume_model_stream(
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    *,
    system: str,
    messages: list[dict],
    tools: Any,
    options: dict,
    cancel: asyncio.Event,
    emit: Callable[[str, dict], Awaitable[None]],
    usage_done_input: int,
    usage_done_output: int,
    context_tokens: int,
    detect_focus_refusal: bool = False,
    dispatch_started: bool = False,
    write_started: bool = False,
) -> _ModelCallResult:
    """Drain one model call's normalized event stream into a result.

    Pure translation of provider events to SSE frames plus accumulation; it
    makes no policy decisions and runs no tools. The usage totals are passed in
    because EV_USAGE is cumulative-REPLACE per call, not additive, so the frame
    the browser receives must add this call's latest report to the finished
    calls' running sum.
    """
    tool_calls: list[dict] = []
    assistant_msg: dict | None = None
    invalid_tools: dict | None = None
    stop_reason = "end_turn"
    errored = False
    saw_text = False
    usage_cur_input = usage_cur_output = 0
    focus = _FocusPrefixBuffer(enabled=detect_focus_refusal)
    focus_event_emitted = False

    async for ev in _stream_turn_resilient(
        provider, session, system_prompt=system, messages=messages,
        tools=tools, options=options, cancel=cancel, dispatch_started=dispatch_started,
        write_started=write_started,
    ):
        etype = ev["type"]
        if etype == EV_TEXT:
            visible = focus.feed(ev["text"])
            if focus.declined and not focus_event_emitted:
                await emit("focus_declined", {})
                focus_event_emitted = True
            if visible:
                saw_text = True
                await emit("assistant_delta", {"text": visible})
        elif etype == EV_THINKING:
            await emit("thinking_delta", {"text": ev["text"]})
        elif etype == EV_TOOL:
            tool_calls.append(ev)
        elif etype == EV_INVALID_TOOLS:
            invalid_tools = ev
        elif etype == EV_USAGE:
            usage_cur_input = _usage_int(ev.get("input_tokens"))
            usage_cur_output = _usage_int(ev.get("output_tokens"))
            if usage_cur_input:
                context_tokens = usage_cur_input
            await emit("usage", {
                "input_tokens": usage_done_input + usage_cur_input,
                "output_tokens": usage_done_output + usage_cur_output,
                "context_tokens": context_tokens,
            })
        elif etype == EV_DONE:
            stop_reason = ev["stop_reason"]
            assistant_msg = ev["assistant_msg"]
            if detect_focus_refusal and assistant_msg is not None:
                assistant_msg, message_declined = _strip_focus_marker_message(assistant_msg)
                focus.declined = focus.declined or message_declined
                if message_declined and not focus.decided:
                    focus.pending = ""
                    focus.decided = True
                if focus.declined and not focus_event_emitted:
                    await emit("focus_declined", {})
                    focus_event_emitted = True
        elif etype == EV_ERROR:
            await emit("error", {"code": ev.get("code"), "message": ev.get("message"),
                                 "retryable": ev.get("retryable", False),
                                 **({"outcome": "outcome_unknown"} if ev.get("outcome") == "outcome_unknown" else {}),
                                 **({"diagnostic": ev["diagnostic"]} if ev.get("diagnostic") else {}),
                                 **({"message_params": ev["message_params"]}
                                    if ev.get("message_params") else {}),
                                 # The provider's own error sentence, already
                                 # clipped and scrubbed by the adapter. The
                                 # admin panel and conversation log show it.
                                 **({"provider_detail": ev["provider_detail"]}
                                    if ev.get("provider_detail") else {}),
                                 **({"provider_code": ev["provider_code"]}
                                    if ev.get("provider_code") else {}),
                                 # The adapter has already reduced response headers
                                 # to a strict allowlist of safe numeric quota
                                 # fields. Preserve that structured snapshot
                                 # through the provider-to-SSE translation.
                                 **({"rate_limit": ev["rate_limit"]}
                                    if ev.get("rate_limit") else {}),
                                 # Which of Phoenix's own request keys the provider
                                 # refused, when it named one. The layer holding the
                                 # store records it; nothing down here can.
                                 **({"refused": ev["refused"]} if ev.get("refused") else {})})
            errored = True

    trailing = focus.finish()
    if focus.declined and not focus_event_emitted:
        await emit("focus_declined", {})
        focus_event_emitted = True
    if trailing:
        saw_text = True
        await emit("assistant_delta", {"text": trailing})

    return _ModelCallResult(
        tool_calls=tool_calls, assistant_msg=assistant_msg, stop_reason=stop_reason,
        errored=errored, saw_text=saw_text,
        usage_input=usage_cur_input, usage_output=usage_cur_output,
        context_tokens=context_tokens,
        focus_declined=focus.declined,
        invalid_tools=invalid_tools,
    )


@dataclasses.dataclass
class _ToolBatchResult:
    """Outcome of running one round's tool calls."""

    results: list[dict]
    human_resolved: bool
    stop_turn: bool
    authority_lost: bool
    write_started: bool = False


async def _run_tool_batch(
    tool_calls: list[dict],
    *,
    hass: HomeAssistant,
    data: PhoenixData,
    access_key_id: str,
    client_ip: str,
    base_url: str,
    emit: Callable[[str, dict], Awaitable[None]],
    cancel: asyncio.Event,
) -> _ToolBatchResult:
    """Dispatch one round's tool calls, honoring cancel and mid-turn authority loss.

    EVERY tool_use id gets a result even when nothing was dispatched: Anthropic
    requires each one answered in the next message, so a blocked call still
    appends a synthetic error rather than being skipped. That is why this drains
    the whole batch instead of returning early.

    The access key is re-resolved per call (_current_dispatch_access_key), so a mid-turn
    revoke, expiry, kill switch or cap narrowing is honored BEFORE any side
    effect, not just at the start of the turn.
    """
    from .mcp_view import _dispatch_mcp  # noqa: PLC0415

    results: list[dict] = []
    human_resolved = False
    stop_turn = False
    authority_lost = False
    write_started = False

    for tc in tool_calls:
        name = str(tc.get("name") or "")[:MAX_TOOL_NAME_LENGTH]
        dispatch_access_key = None if (cancel.is_set() or stop_turn) \
            else _current_dispatch_access_key(data, access_key_id)
        if dispatch_access_key is None:
            stop_turn = True
            if cancel.is_set():
                reason: str | None = "Cancelled by the operator; not run."
            else:
                authority_lost = True
                reason = "This access key is no longer authorized; not run."
            results.append({"tool_use_id": tc.get("id"), "tool_name": name,
                            "result_text": reason, "is_error": True})
            continue
        args = tc.get("input") or {}
        await emit("tool_call", {"id": tc.get("id"), "name": name, "arguments": args})
        # emit() can set cancel if the client vanished mid-batch; re-check
        # AFTER the emit and before the side effect closes that gap.
        if cancel.is_set():
            stop_turn = True
            results.append({"tool_use_id": tc.get("id"), "tool_name": name,
                            "result_text": "Cancelled by the operator; not run.",
                            "is_error": True})
            continue
        if isinstance(args, dict):
            args, time_error = _agentcli_prepare_time_args(
                name, args, dt_util.now().tzinfo or dt_util.UTC,
            )
        else:
            time_error = None
        if time_error is not None:
            await emit("tool_result", {
                "id": tc.get("id"), "name": name,
                "is_error": True, "summary": time_error,
            })
            results.append({
                "tool_use_id": tc.get("id"), "tool_name": name,
                "result_text": time_error, "is_error": True,
            })
            continue
        dispatch_request_id = generate_request_id()
        write_started = write_started or not tool_is_read_only(name)
        try:
            resp_msg, _m, _r, outcome = await _dispatch_with_progress(
                emit, tc.get("id"),
                dispatch_agent_tool(
                    _dispatch_mcp, hass=hass, data=data, access_key=dispatch_access_key,
                    name=name, arguments=args, tool_id=tc.get("id"),
                    client_ip=client_ip, base_url=base_url, request_id=dispatch_request_id,
                ),
            )
        except BaseException:
            # Creation may have saved a queue entry before dispatch was interrupted.
            for saved in data.store.get_pending_approvals():
                if saved.get("access_key_id") == access_key_id and saved.get("request_id") == dispatch_request_id:
                    await _cancel_agent_approval(hass, data, saved["id"])
            raise
        result = dispatch_tool_result(resp_msg)
        if outcome in ("denied", "invalid_request") and (resp_msg or {}).get("error", {}).get("code") == -32000:
            authority_lost = stop_turn = True
        pending = _parse_pending(
            result,
            outcome=outcome,
            data=data,
            access_key_id=access_key_id,
            tool_name=name,
            request_id=dispatch_request_id,
        )
        if pending:
            record = pending["record"]
            try:
                await emit("approval_required", {
                    "approval_id": pending["approval_id"],
                    "tool_name": name,
                    "review_url": pending.get("review_url"),
                    "expires_at": pending.get("expires_at"),
                    "diff": record.diff,
                })
                result = await _await_agent_approval(
                    hass, data, pending["approval_id"], cancel, AGENTCLI_APPROVAL_WAIT_SECONDS,
                )
            finally:
                await _cancel_agent_approval(hass, data, pending["approval_id"])
            status = _status_of(result)
            # The transcript shows WHY, not just the status: the admin's
            # typed rejection reason, or the executor's error when an
            # approval executed but failed (status "execution_failed").
            resolved_record = get_approval(data.store, pending["approval_id"])
            reason = None
            if resolved_record is not None:
                reason = execution_failure_text(resolved_record) \
                    or getattr(resolved_record, "rejected_reason", None)
            await emit("approval_resolved", {
                "approval_id": pending["approval_id"],
                "status": status,
                "reason": _clip_display(reason) if reason else None,
            })
            if status in (STATUS_FAILED, "execution_failed"):
                # The executor may have applied part of the approved action.
                # Do not let the next model round turn its error into a replay.
                raise RecoveryRequired("An approved action has an uncertain outcome.")
            if status in (STATUS_APPROVED, STATUS_REJECTED):
                human_resolved = True
        is_error = bool(result.get("isError"))
        content = result.get("content", [])
        text = _agentcli_add_local_time_fields(
            name,
            _flatten_text(content),
            dt_util.now().tzinfo or dt_util.UTC,
        )
        images = _extract_image_blocks(content)
        for index, image in enumerate(images):
            await emit("tool_image", {
                "id": tc.get("id"),
                "name": name,
                "index": index,
                "mime_type": image["mime_type"],
                "data": image["data"],
            })
        await emit("tool_result", {"id": tc.get("id"), "name": name,
                                   "is_error": is_error, "summary": _clip_display(text)})
        model_text = _agentcli_add_resource_match_guidance(name, args, text)
        results.append({"tool_use_id": tc.get("id"), "tool_name": name,
                        "result_text": model_text, "images": images, "is_error": is_error})

    return _ToolBatchResult(
        results=results, human_resolved=human_resolved,
        stop_turn=stop_turn, authority_lost=authority_lost,
        write_started=write_started,
    )


from .conversation_log import recorded_chat, flush_assistant


@recorded_chat
@tracked_turn("chat")
@recovery_turn
async def async_run_agent_turn(
    *, hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession, messages: list[dict], options: dict,
    client_ip: str, base_url: str,
    emit: Callable[[str, dict], Awaitable[None]],
    cancel: asyncio.Event,
    max_iterations: int = AGENTCLI_MAX_ITERATIONS,
    conversation_style: str = CONVERSATION_STYLE_DIRECT,
    detail_level: str = DETAIL_LEVEL_CONCISE,
    home_focused: bool = False,
    routing_language: object = None,
) -> list[dict]:
    """Run one user turn to completion, streaming SSE events. Returns updated messages.

    The loop body is three phases: drain one model call (_consume_model_stream),
    decide whether to keep the assistant message against the turn output budget,
    and run whatever tool calls it asked for (_run_tool_batch). What stays here
    is the turn-level policy the phases must not own: the runaway-iteration cap
    and its human-in-the-loop reset, the output budget, and how the turn ends.
    """
    await data.agent_recovery.reconcile(data.store, access_key.id)
    if data.agent_recovery.blocks(access_key.id):
        await emit("error", {"code": "outcome_unknown", "retryable": False,
                             "message": "An earlier action outcome is unknown. Check Phoenix MCP > Needs attention before continuing."})
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "error"})
        return messages

    from .chat_routing import async_route_chat_command
    if not await async_route_chat_command(hass, data, access_key, messages, routing_language, cancel,
                                           base_url=base_url, client_ip=client_ip, emit=emit):
        return messages

    from .home_companion import is_companion, async_delegate
    from .const import HOME_COMPANION_REPORT_STATUSES
    if is_companion(access_key):
        request = messages[-1].get("content") if messages and messages[-1].get("role") == "user" else None
        if not isinstance(request, str) or cancel.is_set():
            await emit("error", {"code": "cancelled" if cancel.is_set() else "invalid_request",
                                 "message": voice_text(routing_language, "companion_clarify")})
            await emit("messages", {"messages": messages})
            await emit("done", {"stop_reason": "error"})
            return messages
        task = asyncio.create_task(async_delegate(hass, data, access_key, request, routing_language, base_url,
                                                   style=conversation_style, detail=detail_level))
        stopped = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await emit("error", {"code": "cancelled", "message": voice_text(routing_language, "local_cancelled") + " " + voice_text(routing_language, "companion_unverified")})
            else:
                report = await task
                messages.append({"role": "assistant", "content": report["message"]})
                if report["status"] in HOME_COMPANION_REPORT_STATUSES:
                    await emit("assistant_delta", {"text": report["message"]})
                for url in report["review_urls"]:
                    record = get_approval(data.store, url.rsplit("/", 1)[-1])
                    if record is not None and record.access_key_id == access_key.id:
                        await emit("approval_required", {
                            "approval_id": record.id, "review_url": url, "tool_name": record.tool_name,
                            "expires_at": record.expires_at, "diff": record.diff,
                        })
                if report["status"] not in HOME_COMPANION_REPORT_STATUSES:
                    await emit("error", {"code": report["status"], "message": report["message"], "retryable": False})
        finally:
            for waiter in (task, stopped):
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(task, stopped, return_exceptions=True)
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "end_turn"})
        return messages

    from .tool_routing import create_review_state, async_review_tools, withheld_results
    review_state = create_review_state(data, access_key, "chat")

    from .mcp_view import _build_instructions  # noqa: PLC0415

    system = (
        _build_instructions(access_key, data, base_url, hass)
        + _agentcli_time_context(hass)
        + _conversation_behavior_prompt(
            conversation_style, detail_level, home_focused=home_focused,
        )
        + _AGENTCLI_ADDENDUM
        + _home_focus_output_contract(home_focused)
    )
    agent_tools = build_agent_tool_list(access_key, data, "chat")
    tools = provider.format_tools(agent_tools)
    if (too_many := _tool_limit_problem(provider, tools)) is not None:
        await emit("error", too_many)
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "error"})
        return messages
    # The access key is re-resolved per dispatch (see _current_dispatch_access_key), so a
    # mid-turn revoke/expire/cap-change/kill-switch is honored before any side
    # effect. Each dispatch gets the inline-confirm wait zeroed so a confirm gate
    # returns pending immediately (snappy approval card); the loop's own longer,
    # cancel-aware wait runs afterward.
    access_key_id = access_key.id

    stop_reason = "end_turn"
    dispatch_started = False
    write_started = False
    saw_text = False
    turn_errored = False
    # Bound the TOTAL provider-generated output retained across the whole turn.
    # The per-response _sse_lines cap bounds one stream, but a full turn of
    # iterations would still let messages[] accumulate a large multiple; this shared budget
    # (measured on the retained assistant messages, which is what is held and
    # re-sent) caps the turn as a whole against a faulty/hostile provider. The
    # check happens BEFORE append (below), so the offending message is never
    # retained and the returned transcript itself stays within the budget.
    turn_output_bytes = 0
    # The safety cap bounds a RUNAWAY model (tools calling tools with no human in
    # the loop). A human approving or rejecting an action proves they are in the
    # loop, so that round resets the budget to zero rather than counting toward it.
    iterations = 0
    # Set when the loop reaches the per-turn cap: the turn ends cleanly (the
    # conversation is left resumable, ending in tool results) and a
    # continue_required checkpoint is emitted so the operator can grant another
    # N rounds. Distinct from a hard error or a completed answer.
    paused_for_continue = False
    # Provider-reported token usage across the turn's model calls. cur_* holds
    # the in-flight call's latest cumulative report (EV_USAGE is replace, not
    # add); done_* sums finished calls. The browser owns cross-turn session
    # accumulation, so the SSE event carries turn-cumulative totals plus the
    # last-known context size (the newest call's input tokens).
    usage_done_input = usage_done_output = 0
    usage_cur_input = usage_cur_output = 0
    context_tokens = 0
    # Rounds spent handing malformed tool-call JSON back to the model.
    corrections = 0
    while True:
        if iterations >= max_iterations:
            # Pause at the checkpoint rather than stopping dead: the loop has
            # done max_iterations rounds with no human gating an action, so ask
            # the operator whether to continue. The conversation is resumable
            # (the last round appended its tool results), so a continue request
            # picks up exactly here for another N rounds.
            paused_for_continue = True
            break
        iterations += 1
        if cancel.is_set():
            await emit("error", {"code": "cancelled", "message": "Cancelled."})
            break
        call = await _consume_model_stream(
            provider, session, system=system, messages=messages, tools=tools,
            options=options, cancel=cancel, emit=emit,
            usage_done_input=usage_done_input, usage_done_output=usage_done_output,
            context_tokens=context_tokens,
            detect_focus_refusal=home_focused, dispatch_started=dispatch_started,
            write_started=write_started,
        )
        flush_assistant()
        tool_calls = call.tool_calls
        assistant_msg = call.assistant_msg
        stop_reason = call.stop_reason
        errored = call.errored
        saw_text = saw_text or call.saw_text
        usage_cur_input, usage_cur_output = call.usage_input, call.usage_output
        context_tokens = call.context_tokens
        # Fold the finished call's usage into the turn totals (even on error:
        # a partial call still consumed the tokens it reported).
        usage_done_input += usage_cur_input
        usage_done_output += usage_cur_output
        usage_cur_input = usage_cur_output = 0
        if errored:
            # An upstream error is already emitted to the browser. Mark the
            # turn terminal so the generic empty-reply notice cannot mislabel
            # a useful provider diagnostic, such as a billing refusal.
            turn_errored = True
            stop_reason = "error"
            break
        invalid = call.invalid_tools
        if invalid is not None:
            # Nothing from this response ran. Hand the JSON error back as tool
            # results so the model can resend the call, within a small budget.
            if corrections >= AGENTCLI_MAX_TOOL_CALL_CORRECTIONS:
                failure = _invalid_tools_error()
                if dispatch_started:
                    failure = _after_dispatch_failure(failure, write_started)
                await emit("error", {
                    "code": failure["code"], "message": failure["message"], "retryable": failure["retryable"],
                    "diagnostic": failure["diagnostic"],
                    **({"outcome": "outcome_unknown"} if failure.get("outcome") == "outcome_unknown" else {}),
                })
                turn_errored = True
                stop_reason = "error"
                break
            corrections += 1
            assistant_msg = invalid["assistant_msg"]
            stop_reason = "tool_use"
        if assistant_msg is not None:
            # Check the prospective total BEFORE appending, and reject the message
            # if it would blow the turn budget, so the offending message is never
            # retained in messages (which is returned/re-sent). The transcript thus
            # stays within the budget rather than budget + one whole response.
            prospective = turn_output_bytes + len(json.dumps(assistant_msg, default=str))
            if prospective > AGENTCLI_MAX_TURN_OUTPUT_BYTES:
                await emit("error", {"code": "output_limit", "message": (
                    "The provider produced too much output this turn; stopped.")})
                turn_errored = True
                break
            provider.append_assistant(messages, assistant_msg)
            turn_output_bytes = prospective
        if invalid is not None:
            provider.append_tool_results(messages, invalid["results"])
            await emit("notice", {"code": "bad_tool_call_retry", "message": (
                "The model sent a tool call whose arguments were not valid JSON. Nothing from that "
                "response ran; the error was returned so the model can send the call again.")})
            continue
        if stop_reason != "tool_use":
            break

        review = await async_review_tools(hass, data, review_state, surface="chat",
            language=routing_language, messages=messages, calls=tool_calls, system=system,
            remaining_rounds=max_iterations - iterations, cancel=cancel, announced_tools=agent_tools)
        if review.action != "continue":
            provider.append_tool_results(messages, withheld_results(tool_calls, review))
            if review.action == "stop":
                await emit("error", {"code": "cancelled" if review.reason == "cancelled" else "unauthorized",
                                    "message": "Tool selection stopped because cancellation or authority changed."})
                turn_errored = True
                break
            continue
        dispatch_started = True
        try:
            batch = await _run_tool_batch(
                tool_calls, hass=hass, data=data, access_key_id=access_key_id,
                client_ip=client_ip, base_url=base_url, emit=emit, cancel=cancel,
            )
        except RecoveryRequired:
            await emit("error", {"code": "outcome_unknown", "retryable": False,
                                 "message": "An action outcome is unknown. Check Phoenix MCP > Needs attention before retrying."})
            turn_errored = True
            stop_reason = "error"
            break
        results = batch.results
        write_started = write_started or batch.write_started
        human_resolved = batch.human_resolved
        stop_turn = batch.stop_turn
        authority_lost = batch.authority_lost
        if results:
            provider.append_tool_results(messages, results)
        # A human approved or rejected an action this round: reset the runaway
        # guard, since a human is actively gating the loop.
        if human_resolved:
            iterations = 0
        # The access key lost authority mid-batch (revoked/expired/kill-switch): the
        # batch was completed with synthetic results (protocol-safe); end the
        # turn rather than looping into more denied dispatches.
        if authority_lost:
            await emit("error", {"code": "unauthorized",
                                 "message": "This access key is no longer authorized."})
            break
        if stop_turn:
            # Defence in depth with no observable effect today, so no test can
            # meaningfully pin it: every path that sets stop_turn is already
            # handled. Authority loss breaks above; a cancel is caught by the
            # loop-top check BEFORE the next model call, which emits the same
            # "cancelled" error. It stays because it makes the turn's exit
            # explicit at the point the batch decided to stop, rather than
            # depending on an ordering argument two screens away.
            if cancel.is_set():
                await emit("error", {"code": "cancelled", "message": "Cancelled."})
            break

    # Make an ambiguous ending explain itself, so a reply the model truncated at
    # its output limit or an empty response never looks like the agent silently
    # stalled.
    if not turn_errored and not cancel.is_set():
        if stop_reason == "max_tokens":
            await emit("notice", {"code": "max_tokens_stop", "message": (
                "The model stopped because it reached its output length limit. "
                "Ask it to continue, or raise the model's output token limit "
                "(for Ollama, its num_predict/context settings)."
            )})
        elif stop_reason == "end_turn" and not saw_text:
            await emit("notice", {"code": "empty_turn", "message": (
                "The model ended the turn without a text reply."
            )})
    # The loop hit the per-turn round cap with no human in the loop: offer to
    # continue instead of stopping silently. Emitted before the messages frame
    # so the client has the resumable conversation to send back on "Continue".
    if paused_for_continue and not cancel.is_set():
        await emit("continue_required", {"iterations": iterations})
    _LOGGER.debug(
        "agentCLI turn ended: stop_reason=%s saw_text=%s errored=%s paused=%s",
        stop_reason, saw_text, turn_errored, paused_for_continue,
    )
    await emit("messages", {"messages": messages})
    await emit("done", {"stop_reason": stop_reason})
    return messages


_HEADLESS_PROVIDER_ERROR = "the language model could not be reached"

# How a headless turn ended. A CODE, not a sentence, because the two surfaces
# render it very differently: voice speaks it, in the CONVERSATION's language via
# const.VOICE_TEMPLATES, while AI Task raises it as an English operator-facing
# error. Prose from the loop would force an English literal into a spoken reply.
TURN_OK = "ok"
TURN_PROVIDER_ERROR = "provider_error"
TURN_OUTCOME_UNKNOWN = "outcome_unknown"
TURN_OUTPUT_LIMIT = "output_limit"
TURN_EXHAUSTED = "exhausted"
TURN_AUTHORITY_LOST = "authority_lost"
TURN_TRUNCATED = "truncated"


@dataclasses.dataclass
class _HeadlessTurnResult:
    """What one headless turn produced, and how it ended.

    `text` is the model's LAST text (reset at each tool round), so a partial
    answer survives a failure and the caller can still say something useful.

    `outcome` is one of the TURN_* codes, so a non-OK ending is never mistaken
    for a normal one: running out of rounds would otherwise look like an empty
    reply (AI Task would blame the model for JSON it never got to write), and
    hitting the model's own output limit would look like a complete answer.

    `detail` carries the provider's own error text for TURN_PROVIDER_ERROR only.
    """

    text: str
    review_urls: list[str]
    outcome: str = TURN_OK
    detail: str = ""
    focus_declined: bool = False
    blocked_by_prior_action: bool = False
    recovery_turn_id: str = ""


@recovery_turn
async def _run_headless_turn(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    base_url: str,
    *,
    system: str,
    seed: str,
    client_ip: str,
    max_iterations: int,
    detect_focus_refusal: bool = False,
    routing_surface: str = "voice",
    routing_language: object = None,
    reserve_final_round: bool = False,
    response_only: bool = False,
) -> _HeadlessTurnResult:
    """Run one headless model/tool turn to completion.

    The shared engine behind async_run_voice_turn and async_run_ai_task: what a
    surface decides (error policy, audit sentinel, review URLs) lives in its
    wrapper; what both must do identically lives here, so a fix to the
    tool-dispatch path cannot land on one and miss the other.

    HEADLESS means two things, both deliberate. There is no event emission, so
    none of the interactive loop's SSE machinery applies. And a confirm gate is
    never waited on: neither an Assist pipeline nor an ai_task.generate_data call
    can hold a request open for an admin, so a pending approval is fed back as a
    "queued_for_approval" tool result and the turn continues. That rules out
    sharing _run_tool_batch, which blocks up to AGENTCLI_APPROVAL_WAIT_SECONDS on
    a human.

    The access key is re-resolved before every dispatch (_current_dispatch_access_key), so a
    mid-turn revoke, expiry, kill switch or cap narrowing is honored before any
    side effect.
    """
    from .mcp_view import _dispatch_mcp  # noqa: PLC0415

    await data.agent_recovery.reconcile(data.store, access_key.id)
    if data.agent_recovery.blocks(access_key.id):
        return _HeadlessTurnResult("", [], outcome=TURN_OUTCOME_UNKNOWN, blocked_by_prior_action=True)

    from .tool_routing import create_review_state, async_review_tools, withheld_results
    from .home_companion import is_companion
    review_state = None if is_companion(access_key) else create_review_state(data, access_key, routing_surface)
    agent_tools = build_agent_tool_list(access_key, data, routing_surface)
    tools = provider.format_tools(agent_tools)
    if (too_many := _tool_limit_problem(provider, tools)) is not None:
        return _HeadlessTurnResult("", [], outcome=TURN_PROVIDER_ERROR, detail=too_many["message"])
    messages: list[dict] = [{"role": "user", "content": seed}]
    access_key_id = access_key.id
    cancel = asyncio.Event()  # never fired; the stream primitive expects one
    final_text = ""
    review_urls: list[str] = []
    # Bound the TOTAL provider output retained across the turn, matching
    # async_run_agent_turn: assistant and tool messages are retained across every
    # round and the growing transcript is re-sent on each one, so without a
    # ceiling a faulty or hostile provider could inflate it. Checked BEFORE the
    # append, so the offending message is never retained.
    turn_output_bytes = 0
    # Set when a dispatch was refused because the access key lost authority mid-turn.
    # The round that discovers it still finishes and still feeds the refusals
    # back, so the model gets ONE turn to tell the user; asking for tools again
    # after that is stopped rather than repeated (see below).
    authority_lost = False
    # Only a break clears this. Falling out of the for is the runaway case: the
    # round that exhausts the budget is by definition a tool round, which has
    # already reset final_text, so the caller would otherwise receive "".
    exhausted = True
    focus_declined = False
    dispatch_started = False
    write_started = False

    format_corrections = 0
    count_report_only = False
    from .const import HOME_COMPANION_MAX_FORMAT_CORRECTIONS, HOME_COMPANION_FORMAT_ERROR_CODES
    for iteration in range(max_iterations):
        from .home_companion import check_turn_context, internal_turn, ambiguous_control, count_source
        final_round = response_only or count_report_only or (reserve_final_round and iteration == max_iterations - 1)
        round_system = system
        if final_round:
            round_system += (
                "\nThis is the final response round. No further tools are available. "
                "Return the required final answer using only the results already received. "
                "Distinguish an accepted device command from a verified physical state. "
                "If the results are insufficient, explain what is known and what is missing. "
                "Do not claim that no action occurred when a control tool already ran."
            )
        if not check_turn_context(access_key, round_system, messages):
            return _HeadlessTurnResult("", review_urls, outcome=TURN_OUTPUT_LIMIT)
        tool_calls: list[dict] = []
        assistant_msg: dict | None = None
        invalid: dict | None = None
        stop_reason = "end_turn"
        call_text = ""
        correct_format = False
        async for ev in _stream_turn_resilient(
            provider, session, system_prompt=round_system, messages=messages,
            tools=[] if final_round else tools, options={}, cancel=cancel, dispatch_started=dispatch_started,
            write_started=write_started,
        ):
            etype = ev["type"]
            if etype == EV_INVALID_TOOLS:
                # Hand malformed tool-call JSON back as tool errors while a round
                # remains for the corrected call. Home Companion keeps its own
                # stricter budget and never corrects after a write.
                companion = internal_turn(access_key)
                limit = HOME_COMPANION_MAX_FORMAT_CORRECTIONS if companion else AGENTCLI_MAX_TOOL_CALL_CORRECTIONS
                if (not final_round and format_corrections < limit and iteration < max_iterations - 1
                        and not (companion and write_started)):
                    invalid = ev
                    continue
                ev = _invalid_tools_error()
                etype = EV_ERROR
            if etype == EV_TEXT:
                call_text += ev["text"]
            elif etype == EV_TOOL:
                tool_calls.append(ev)
            elif etype == EV_DONE:
                stop_reason = ev["stop_reason"]
                assistant_msg = ev["assistant_msg"]
            elif etype == EV_ERROR:
                if (internal_turn(access_key) and not write_started and not tool_calls
                        and (ev.get('code') in HOME_COMPANION_FORMAT_ERROR_CODES
                             or (ev.get('diagnostic') or {}).get('category') in HOME_COMPANION_FORMAT_ERROR_CODES)
                        and format_corrections < HOME_COMPANION_MAX_FORMAT_CORRECTIONS
                        and iteration < max_iterations - 1):
                    format_corrections += 1
                    messages.append({'role': 'user', 'content': (
                        'Your previous response was malformed and none of the tools in that response ran. '
                        'Use valid JSON objects for tool arguments, including quoted string values. '
                        'Keep the original task and restrictions unchanged.')})
                    correct_format = True
                    break
                return _HeadlessTurnResult(
                    final_text, review_urls,
                    outcome=TURN_OUTCOME_UNKNOWN if write_started else TURN_PROVIDER_ERROR,
                    detail=str(ev.get("message") or _HEADLESS_PROVIDER_ERROR),
                    focus_declined=focus_declined,
                )
        if correct_format:
            continue
        if invalid is not None:
            format_corrections += 1
            prospective = turn_output_bytes + len(json.dumps(invalid["assistant_msg"], default=str))
            if prospective > AGENTCLI_MAX_TURN_OUTPUT_BYTES:
                return _HeadlessTurnResult(
                    final_text, review_urls, outcome=TURN_OUTPUT_LIMIT, focus_declined=focus_declined,
                )
            provider.append_assistant(messages, invalid["assistant_msg"])
            turn_output_bytes = prospective
            provider.append_tool_results(messages, invalid["results"])
            continue
        if detect_focus_refusal:
            call_text, text_declined = _strip_focus_marker_text(call_text)
            focus_declined = focus_declined or text_declined
            if assistant_msg is not None:
                assistant_msg, message_declined = _strip_focus_marker_message(assistant_msg)
                focus_declined = focus_declined or message_declined
        final_text += call_text
        if assistant_msg is not None:
            prospective = turn_output_bytes + len(json.dumps(assistant_msg, default=str))
            if prospective > AGENTCLI_MAX_TURN_OUTPUT_BYTES:
                return _HeadlessTurnResult(
                    final_text, review_urls, outcome=TURN_OUTPUT_LIMIT,
                    focus_declined=focus_declined,
                )
            provider.append_assistant(messages, assistant_msg)
            turn_output_bytes = prospective
        if final_round and tool_calls:
            for proposed in tool_calls:
                record_rejected_tool(proposed.get("id"), proposed.get("name"), proposed.get("input"),
                                     reason="No tools are permitted in the final response round.", stage="round_limit")
            # A provider ignoring the final-round instruction cannot dispatch
            # another batch and leave no round for reporting its outcome.
            return _HeadlessTurnResult("", review_urls, outcome=TURN_EXHAUSTED)
        if stop_reason != "tool_use":
            from .home_companion import invalid_count_report
            if (not write_started and invalid_count_report(access_key, final_text)
                    and format_corrections < HOME_COMPANION_MAX_FORMAT_CORRECTIONS
                    and iteration < max_iterations - 1):
                format_corrections += 1
                count_report_only = True
                messages.append({'role': 'user', 'content': (
                    'Your final report did not pass validation. Return exactly one JSON object '
                    'with status and a nonempty message. For a count, copy the unchanged server-provided read_answer reference '
                    'from the received search result. Do not invent a count or source index. '
                    'Do not repeat discovery or run a control. Keep the original task and restrictions unchanged.')})
                final_text = ''
                continue
            exhausted = False
            break
        if authority_lost:
            # The previous round fed back "no longer authorized" for every call
            # and this round asked for tools again rather than answering. Every
            # one would be refused identically, so stop instead of spending the
            # remaining rounds re-reporting the same refusal to the same dead
            # access key. The interactive loop ends the turn outright here; headless
            # allows the one intervening round so the model can say what happened.
            exhausted = False
            break

        from .home_companion import preflight_control_calls, incomplete_proposal
        if validation_results := preflight_control_calls(access_key, tool_calls):
            for proposed, validation in zip(tool_calls, validation_results):
                record_rejected_tool(proposed.get("id"), proposed.get("name"), proposed.get("input"),
                                     reason=validation["result_text"], stage="companion_preflight")
            if write_started:
                incomplete_proposal(access_key)
                return _HeadlessTurnResult(json.dumps({'status': 'refused', 'message': 'A later control proposal was invalid and was not run.'}), review_urls)
            if (format_corrections >= HOME_COMPANION_MAX_FORMAT_CORRECTIONS
                    or iteration >= max_iterations - 2):
                return _HeadlessTurnResult('', review_urls, outcome=TURN_PROVIDER_ERROR)
            format_corrections += 1
            provider.append_tool_results(messages, validation_results)
            final_text = ''
            continue

        # The answer is the model's LAST text (after tools resolve), not the
        # intermediate "let me check..." text before a tool round; reset it here.
        final_text = ""
        if review_state is not None:
            review = await async_review_tools(hass, data, review_state, surface=routing_surface,
                language=routing_language, messages=messages, calls=tool_calls, system=system,
                remaining_rounds=max_iterations - iteration - 1, announced_tools=agent_tools)
            if review.action != "continue":
                provider.append_tool_results(messages, withheld_results(tool_calls, review))
                if review.action == "stop":
                    return _HeadlessTurnResult("", review_urls, outcome=TURN_AUTHORITY_LOST)
                continue
        results: list[dict] = []
        for tc in tool_calls:
            name = str(tc.get("name") or "")[:MAX_TOOL_NAME_LENGTH]
            dispatch_access_key = _current_dispatch_access_key(data, access_key_id)
            if dispatch_access_key is None:
                record_rejected_tool(tc.get("id"), name, tc.get("input"),
                                     reason="This access key is no longer authorized; the tool was not run.", stage="authority_guard")
                authority_lost = True
                results.append({
                    "tool_use_id": tc.get("id"), "tool_name": name,
                    "result_text": "This access key is no longer authorized; not run.",
                    "is_error": True,
                })
                continue
            dispatch_request_id = generate_request_id()
            if ambiguous_control(dispatch_access_key, hass, name, tc.get("input")):
                record_rejected_tool(tc.get("id"), name, tc.get("input"),
                                     reason="The target is ambiguous; no action was executed.", stage="target_guard")
                return _HeadlessTurnResult(json.dumps({'status': 'clarification_needed', 'message': 'Ambiguous target.'}), review_urls)
            dispatch_started = True
            write_started = write_started or not tool_is_read_only(name)
            try:
                resp_msg, _m, _r, outcome = await dispatch_agent_tool(
                    _dispatch_mcp, hass=hass, data=data, access_key=dispatch_access_key,
                    name=name, arguments=tc.get("input") or {}, tool_id=tc.get("id"),
                    client_ip=client_ip, base_url=base_url, request_id=dispatch_request_id,
                )
            except RecoveryRequired:
                return _HeadlessTurnResult("", review_urls, outcome=TURN_OUTCOME_UNKNOWN)
            result = dispatch_tool_result(resp_msg)
            if outcome in ("denied", "invalid_request") and (resp_msg or {}).get("error", {}).get("code") == -32000:
                authority_lost = True
            pending = _parse_pending(
                result,
                outcome=outcome,
                data=data,
                access_key_id=access_key_id,
                tool_name=name,
                request_id=dispatch_request_id,
            )
            if pending:
                if pending.get("review_url"):
                    review_urls.append(pending["review_url"])
                results.append({
                    "tool_use_id": tc.get("id"), "tool_name": name,
                    "result_text": json.dumps({
                        "status": "queued_for_approval",
                        "message": "Queued for admin approval in the Phoenix MCP panel; not applied yet.",
                    }),
                    "is_error": False,
                })
                from .home_companion import internal_turn
                if internal_turn(dispatch_access_key):
                    return _HeadlessTurnResult("", review_urls)
                continue
            content = result.get("content", [])
            result_text = _flatten_text(content)
            reference = count_source(dispatch_access_key, name)
            if reference is not None:
                try:
                    body = json.loads(result_text)
                except (ValueError, TypeError):
                    body = None
                if isinstance(body, dict):
                    result_text = json.dumps({**body, 'read_answer': reference}, ensure_ascii=False)
                else:
                    result_text += '\nread_answer: ' + json.dumps(reference)
            results.append({
                "tool_use_id": tc.get("id"), "tool_name": name,
                "result_text": result_text,
                "images": _extract_image_blocks(content),
                "is_error": bool(result.get("isError")),
            })
        provider.append_tool_results(messages, results)

    if authority_lost:
        # Reported ahead of exhaustion: a turn can hit both (authority lost in the
        # final allowed round), and the access key dying is the actionable cause. It
        # also cannot be inferred from the outside: the closing round often ends
        # normally, so without this the turn would look like an ordinary success
        # that produced nothing.
        return _HeadlessTurnResult(
            final_text, review_urls, outcome=TURN_AUTHORITY_LOST,
            focus_declined=focus_declined,
        )
    if exhausted:
        return _HeadlessTurnResult(
            final_text, review_urls, outcome=TURN_EXHAUSTED,
            focus_declined=focus_declined,
        )
    if stop_reason == "max_tokens":
        # The model stopped at its OWN output limit, so `text` is a sentence that
        # was cut off mid-thought. Reported rather than returned bare: a spoken
        # half-answer gets acted on, and a half-written JSON document fails to
        # parse and is then blamed on the model's formatting.
        return _HeadlessTurnResult(
            final_text, review_urls, outcome=TURN_TRUNCATED,
            focus_declined=focus_declined,
        )
    return _HeadlessTurnResult(
        final_text, review_urls, focus_declined=focus_declined,
    )


_VOICE_ADDENDUM = (
    "\n\nYou are answering the user through Home Assistant's voice/chat assistant. "
    "Keep replies spoken-friendly, even when the selected detail level is Detailed. "
    "Use the tools above to answer or act. "
    "If a tool result says \"queued_for_approval\", the action has NOT happened yet: it is "
    "waiting for an administrator to approve it in the Phoenix MCP panel. Tell the user it is queued "
    "for approval; do not retry it and do not claim it is done."
)


async def async_run_voice_turn(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    base_url: str,
    user_text: str,
    *,
    max_iterations: int = AGENTCLI_MAX_ITERATIONS,
    include_review_links: bool = False,
    language: object = None,
) -> str:
    """Run one conversation-agent turn to completion; return the spoken answer.

    A headless sibling of async_run_agent_turn for HA's Assist/voice pipeline
    (engine: _run_headless_turn): no SSE, no interactive approval wait. A confirm
    gate is fed back to the model as a "queued_for_approval" tool result, so a
    voice turn never blocks.

    include_review_links appends a clickable approval link for anything queued this
    turn. Pass True only for a TEXT surface (typed Assist chat): a reply is both
    displayed and spoken from one string, so a link would otherwise be read aloud
    by TTS on a voice satellite.

    language is the CONVERSATION's language: a spoken reply goes to whoever is
    talking, not to the server. Every sentence that is not the model's own words
    comes from const.VOICE_TEMPLATES, so a failure is never spoken in English to a
    non-English pipeline. None falls back to English.
    """
    from .home_companion import is_companion, internal_turn, async_delegate
    from .const import HOME_COMPANION_REPORT_STATUSES
    if is_companion(access_key) and not internal_turn(access_key):
        report = await async_delegate(hass, data, access_key, user_text, language, base_url)
        voice_result("ok" if report["status"] in HOME_COMPANION_REPORT_STATUSES
                     else report["status"], bool(report["review_urls"]), bool(report["message"]))
        answer = report["message"]
        if include_review_links and report["review_urls"]:
            answer += "\n\n" + " ".join(f"{base_url}{url}" for url in report["review_urls"])
        return answer

    from .mcp_view import _build_instructions  # noqa: PLC0415

    settings = data.store.get_settings()
    home_focused = settings.voice_agent_home_focused
    behavior = _conversation_behavior_prompt(
        settings.voice_agent_conversation_style,
        settings.voice_agent_detail_level,
        home_focused=home_focused,
    )

    turn = await _run_headless_turn(
        hass, data, access_key, provider, session, base_url,
        system=(
            _build_instructions(access_key, data, base_url, hass)
            + behavior
            + _VOICE_ADDENDUM
            + _home_focus_output_contract(home_focused)
        ),
        seed=user_text, client_ip=VOICE_AGENT_CLIENT_IP, max_iterations=max_iterations,
        detect_focus_refusal=home_focused, routing_surface="voice", routing_language=language,
    )
    voice_result(turn.outcome, bool(turn.review_urls), bool(turn.text.strip()))
    # Never raise at a microphone: every ending becomes something sayable, in the
    # CONVERSATION's language (voice_text), not the server's. A partial answer the
    # model already produced is preferred over an apology that discards it.
    if turn.outcome == TURN_OUTCOME_UNKNOWN:
        return voice_text(language, "prior_action_unresolved" if turn.blocked_by_prior_action else "outcome_unknown")
    if turn.outcome == TURN_PROVIDER_ERROR:
        return turn.text.strip() or voice_text(language, "provider_error", reason=turn.detail)
    if turn.outcome == TURN_OUTPUT_LIMIT:
        return turn.text.strip() or voice_text(language, "output_limit")
    if turn.outcome == TURN_AUTHORITY_LOST:
        # The closing round usually produces a sentence ("I could not do that");
        # say it. The fallback covers a model that asked for tools again instead.
        return turn.text.strip() or voice_text(language, "access_key_unavailable")
    if turn.outcome == TURN_EXHAUSTED:
        # By construction there is no partial answer here: the round that runs out
        # of steps is a tool round, which has already reset the text.
        return voice_text(language, "out_of_steps")

    if turn.focus_declined:
        # A weak model can emit a marked refusal and then answer the unrelated
        # request anyway. Voice has no visible transcript to preserve, so render
        # the refusal deterministically and never speak the model's mixed content.
        answer = voice_text(language, "focus_declined")
    else:
        answer = turn.text.strip() or voice_text(language, "could_not_complete")
    if turn.outcome == TURN_TRUNCATED and turn.text.strip():
        # Said out loud, because a cut-off answer is acted on as a whole one.
        answer = f"{answer} {voice_text(language, 'answer_truncated')}"
    if include_review_links and turn.review_urls:
        # Deterministically append a clickable approval link (the Assist chat renders
        # the reply as markdown). Absolute when a base URL is known so it clicks from
        # anywhere; the review_url is already a same-origin path like /phoenix-mcp/approvals/id.
        links = [f"{base_url}{u}" if base_url else u for u in turn.review_urls]
        joined = " ".join(f"[Approve in the Phoenix MCP panel]({href})" for href in links)
        answer = f"{answer}\n\n{joined}"
    return answer


_AI_TASK_ADDENDUM = (
    "\n\nYou are performing a Home Assistant AI Task: generate the requested data from "
    "the instructions. Use the tools above only when you need live data to answer. "
    "If a tool result says \"queued_for_approval\", that action is waiting for an "
    "administrator and has NOT happened; do not retry it. Return only the requested "
    "result as your final message, with no extra commentary."
)

_AI_TASK_STRUCTURE_DIRECTIVE = (
    "\n\nYour final message MUST be a single valid JSON value conforming to this JSON "
    "schema, with no prose, explanation, or markdown code fences:\n{schema}"
)


def _ai_task_free_text_output_contract(style: str, detail: str) -> str:
    """Repeat free-text presentation defaults at AI Task's final-output boundary."""
    style_instruction = _STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS["direct"])
    detail_instruction = _DETAIL_INSTRUCTIONS.get(detail, _DETAIL_INSTRUCTIONS["balanced"])
    return (
        "\n\nAI Task free-text output contract: "
        f"{style_instruction} {detail_instruction} "
        "Return only the requested data. Do not mention tools, available entities, the task "
        "process, or additional work Phoenix could do unless the task instructions request it."
    )


class AiTaskError(Exception):
    """The AI Task model loop failed (provider/stream error)."""


@untracked_turn
async def async_run_ai_task(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    base_url: str,
    instructions: str,
    *,
    structure_json: dict | None = None,
    max_iterations: int = AGENTCLI_MAX_ITERATIONS,
) -> str:
    """Run one AI Task (ai_task.generate_data) to completion; return the final text.

    A headless sibling of async_run_voice_turn for HA's AI Task platform: same gated tool
    loop (per-access-key scope, MESA, approvals, audit via _dispatch_mcp with sentinel
    client_ip "ai_task"), no SSE, no interactive wait. A confirm gate returns pending,
    fed back as a "queued_for_approval" tool result so a task never blocks. The access key
    is re-resolved before every dispatch, so a mid-task revoke/kill-switch is honored.

    When structure_json is given (the caller's JSON schema derived from the task's
    structure), the model is instructed to emit only matching JSON; the caller parses
    the returned text.
    """
    from .home_companion import is_companion, async_delegate
    companion_authority = None
    if is_companion(access_key):
        from .command_routing import RoutingAuthority
        companion_authority = RoutingAuthority.capture(data, access_key)
        settings = data.store.get_settings()
        report = await async_delegate(hass, data, access_key, instructions, hass.config.language, base_url,
                                      style=settings.ai_task_conversation_style, detail=settings.ai_task_detail_level)
        if companion_authority.current(hass) is None:
            raise AiTaskError(voice_text(hass.config.language, "access_key_unavailable"))
        if report["status"] != "completed":
            raise AiTaskError(report["message"])
        if structure_json is None:
            return report["message"]
        instructions = json.dumps({"task": instructions, "phoenix_report": report}, ensure_ascii=False)

    from .mcp_view import _build_instructions  # noqa: PLC0415

    settings = data.store.get_settings()
    system = _build_instructions(access_key, data, base_url, hass)
    if structure_json is None:
        system += _conversation_behavior_prompt(
            settings.ai_task_conversation_style,
            settings.ai_task_detail_level,
        )
    system += _AI_TASK_ADDENDUM
    if structure_json is None:
        system += _ai_task_free_text_output_contract(
            settings.ai_task_conversation_style,
            settings.ai_task_detail_level,
        )
    else:
        system += _AI_TASK_STRUCTURE_DIRECTIVE.format(schema=json.dumps(structure_json))

    turn = await _run_headless_turn(
        hass, data, access_key, provider, session, base_url,
        system=system, seed=instructions, client_ip=AI_TASK_CLIENT_IP,
        max_iterations=1 if companion_authority is not None else max_iterations, routing_surface="ai_task", routing_language=hass.config.language,
        response_only=companion_authority is not None,
    )
    if companion_authority is not None and companion_authority.current(hass) is None:
        raise AiTaskError(voice_text(hass.config.language, "access_key_unavailable"))
    # Unlike the voice surface there is no one listening to an apology: an
    # automation asked for data, so every non-OK ending is an error the caller
    # wraps as a HomeAssistantError. These messages are operator-facing and
    # deliberately English, like the rest of the agent-facing error text.
    if turn.outcome == TURN_OUTCOME_UNKNOWN:
        if turn.blocked_by_prior_action:
            raise AiTaskError("This task was not executed because an earlier action has an unresolved outcome. Check Phoenix MCP > Needs attention before trying again.")
        raise AiTaskError("An action outcome is unknown. Reconcile Home Assistant and approval results before retrying.")
    if turn.outcome == TURN_PROVIDER_ERROR:
        raise AiTaskError(turn.detail)
    if turn.outcome == TURN_OUTPUT_LIMIT:
        raise AiTaskError("the model produced too much output this turn")
    if turn.outcome == TURN_AUTHORITY_LOST:
        raise AiTaskError(
            "the access key lost authority mid-task (revoked, expired, or the kill "
            "switch); at least one tool call was refused, so the result is unsound"
        )
    if turn.outcome == TURN_EXHAUSTED:
        raise AiTaskError(
            "the model ran out of steps before returning a result; raise "
            "Steps before check-in in Phoenix MCP settings if the task needs more"
        )
    if turn.outcome == TURN_TRUNCATED:
        raise AiTaskError(
            "the model stopped at its own output length limit, so the result is "
            "incomplete; raise the model's output token limit"
        )
    return turn.text.strip()


def _status_of(result: dict) -> str:
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                parsed = json.loads(item.get("text", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("status"):
                return parsed["status"]
    return STATUS_APPROVED if not result.get("isError") else STATUS_FAILED


def _zero_inline_wait(access_key: AccessKeyRecord) -> AccessKeyRecord:
    """Return a copy of the access key with confirm_inline_wait_seconds zeroed.

    Falls back to the original access key if it is not a dataclass.
    """
    try:
        return dataclasses.replace(access_key, confirm_inline_wait_seconds=0)
    except (TypeError, ValueError):
        return access_key


def _current_dispatch_access_key(data: PhoenixData, access_key_id: str) -> AccessKeyRecord | None:
    """Re-resolve the access key for one tool dispatch, or None if it may no longer act.

    A single Agent Chat turn can stay open a long time (a confirm gate blocks on
    an approval wait), so authority must be re-checked before EVERY side effect,
    not just at stream open, mirroring async_get_authenticated_access_key on the MCP path.
    Returns None when Phoenix MCP is shutting down, the runtime kill switch is on, or the
    access key has been revoked/expired or narrowed away since the turn started; the
    fresh record is returned otherwise so cap changes take effect immediately.
    """
    if (
        getattr(data, "ready", True) is False
        or getattr(data, "shutting_down", False) is True
        or data.store.get_settings().kill_switch
    ):
        return None
    access_key = data.store.get_access_key_by_id(access_key_id)
    if access_key is None or not access_key.is_valid():
        return None
    return _zero_inline_wait(access_key)


# --------------------------------------------------------------------------- #
# HTTP views
# --------------------------------------------------------------------------- #

class PhoenixAgentCliChatView(PhoenixView):
    """POST /api/phoenix-mcp/agentcli/chat - the streaming agent turn (admin, SSE)."""

    url = "/api/phoenix-mcp/agentcli/chat"
    name = "api:phoenix-mcp:agentcli:chat"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        # Room for base64 images; everything else keeps the ordinary cap below.
        body = await _read_body(request, rid, limit=AGENTCLI_REQUEST_BODY_BYTES)
        if isinstance(body, web.Response):
            return body
        attachments = body.pop("attachments", None)
        history_images = _image_payload_chars(body.get("messages"))
        if (history_images > _ATTACHMENT_BUDGET_CHARS
                or len(json.dumps(body).encode()) - history_images > MAX_REQUEST_BODY_BYTES):
            return _err("request_too_large", "Request body too large.", 413, rid, key="bodyTooLarge")
        data = self.hass.data.get(DOMAIN)
        if data is None:
            return _err(
                "not_found", "Phoenix MCP is not set up.", 404, rid,
                key="setupMissing",
            )

        # Agent Chat bypasses async_get_authenticated_access_key (it is admin-authed, not
        # bearer-authed), so it must enforce the same invariants that gate every
        # MCP request: shutdown, the runtime kill switch, and access key validity.
        settings = data.store.get_settings()
        if not data.ready or data.shutting_down or settings.kill_switch:
            return _err(
                "service_unavailable", "Phoenix MCP is disabled (kill switch).", 503, rid,
                key="serviceDisabled",
            )

        access_key_id = str(body.get("access_key_id") or "")
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err(
                "not_found", "Access key not found.", 404, rid,
                key="accessKeyNotFound",
            )
        if not access_key.is_valid():
            return _err(
                "invalid_request", "This access key is revoked or expired.", 400, rid,
                key="accessKeyInactive",
            )

        instance_id = str(body.get("instance_id") or "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id, body.get("model") or None)
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        if not cfg.model:
            return _err(
                "invalid_request", "No model selected for this provider.", 400, rid,
                key="providerModelMissing",
            )

        images = _chat_attachments(attachments, rid)
        if isinstance(images, web.Response):
            return images
        if images:
            from .home_companion import is_companion  # noqa: PLC0415
            if body.get("continue") is True:
                # Continue adds no message to carry them.
                return _err("invalid_request", "Images can only be attached to a new message.", 400, rid,
                            key="attachmentsContinue")
            if is_companion(access_key):
                # Home Companion delegates only the text command.
                return _err("invalid_request",
                            "Home Companion takes text commands only, so images cannot be attached.", 400, rid,
                            key="attachmentsCompanion")
            if cfg.vision is False:
                # Declared or learned. Unknown support is sent, and a refusal
                # teaches Phoenix (see _refused_option).
                return _err("invalid_request", "This model does not accept images.", 400, rid,
                            key="attachmentsVisionUnsupported")

        provider = build_provider(cfg)
        messages = body.get("messages")
        if not isinstance(messages, list):
            messages = []
        if cfg.vision is False:
            _without_history_images(messages)
        if body.get("continue") is True:
            # Resume a turn that paused at the round-cap checkpoint: no new user
            # message is appended; the model continues from the tool results the
            # prior turn left behind. Requires an existing conversation.
            if not messages:
                return _err(
                    "invalid_request", "Nothing to continue.", 400, rid,
                    key="nothingToContinue",
                )
        else:
            user_text = str(body.get("user") or "")
            if not user_text.strip() and not images:
                return _err(
                    "invalid_request", "Empty message.", 400, rid,
                    key="emptyMessage",
                )
            message = ({"role": "user", "content": user_text} if not images
                       else provider.user_message(user_text if user_text.strip() else "", images))
            messages = [*messages, message]
        options = body.get("options") if isinstance(body.get("options"), dict) else {}

        session = async_get_clientsession(self.hass)
        base_url = str(request.url.origin())
        client_ip = AGENTCLI_CLIENT_IP or get_client_ip(request)

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Phoenix-Request-ID": rid,
            },
        )
        await resp.prepare(request)
        cancel = asyncio.Event()

        async def emit(name: str, payload: dict) -> None:
            # Learn from a refusal the provider returned during REAL work. This is the
            # backstop under the declared and probed answers: it costs nothing
            # extra, it is ground truth rather than an inference, and it covers
            # the providers that publish nothing and validate nothing, which
            # neither of the other two mechanisms can reach.
            refused = payload.get("refused") if name == "error" else None
            if name == "error" and payload.get("status") == 404:
                # "Model not found" is how a provider says a model was retired.
                # Re-read the catalogue so the next turn resolves to its successor.
                self.hass.async_create_task(async_refresh_catalog(self.hass, instance_id))
            if refused and cfg.model:
                await store.record_refusal(instance_id, cfg.model, refused, utcnow().isoformat())
            frame = f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"
            try:
                await resp.write(frame.encode("utf-8"))
            except (ConnectionResetError, ClientError, RuntimeError):
                cancel.set()

        async def _heartbeat() -> None:
            # Send an SSE comment periodically so an idle intermediary (reverse
            # proxy, Nabu Casa, a backgrounded tab) does not drop the streaming
            # connection during a quiet stretch, e.g. while the model is thinking
            # or an approval is pending. Comment frames carry no data: line, so the
            # panel's parser ignores them. A failed write means the client is gone.
            try:
                while not cancel.is_set():
                    await asyncio.sleep(AGENTCLI_HEARTBEAT_SECONDS)
                    try:
                        await resp.write(b": keepalive\n\n")
                    except (ConnectionResetError, ClientError, RuntimeError):
                        cancel.set()
                        return
            except asyncio.CancelledError:
                pass

        heartbeat = self.hass.loop.create_task(_heartbeat())
        try:
            await emit("ready", {"provider": cfg.kind, "model": cfg.model, "access_key_id": access_key_id})
            await async_run_agent_turn(
                hass=self.hass, data=data, access_key=access_key, provider=provider,
                session=session, messages=messages, options=options or {},
                log_conversation_id=body.get("log_conversation_id"),
                client_ip=client_ip, base_url=base_url, emit=emit, cancel=cancel,
                max_iterations=settings.agentcli_max_iterations,
                conversation_style=settings.agentcli_conversation_style,
                detail_level=settings.agentcli_detail_level,
                home_focused=settings.agentcli_home_focused,
                routing_language=body.get("language") if body.get("continue") is not True else None,
            )
        except Exception:  # noqa: BLE001 - never leak internals to the client
            _LOGGER.exception("agentCLI turn failed")
            await emit("error", {"code": "internal", "message": "Internal error."})
        finally:
            heartbeat.cancel()
            try:
                await resp.write_eof()
            except (ConnectionResetError, ClientError, RuntimeError):
                pass
        return cast(web.Response, resp)


def _ip_is_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool,
) -> bool:
    """Return whether an operator-supplied endpoint may target this address."""
    return (
        ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        or (not allow_private and (ip.is_private or ip.is_loopback))
    )


async def _validate_base_url(
    url: str, *, allow_private: bool = True,
) -> ProbeConfigError | None:
    """Check URL shape and resolved addresses at setup time.

    This administrator-only guard cannot pin future DNS resolution. Ollama may
    use private HTTP endpoints; other providers require HTTPS. Redirect behavior
    depends on the provider adapter, so this check is not a connection-time SSRF
    guarantee.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        return ProbeConfigError("Enter a valid server URL.", "providerUrlInvalid")
    schemes = ("http", "https") if allow_private else ("https",)
    if parts.scheme not in schemes:
        if allow_private:
            return ProbeConfigError(
                "The server URL must start with http:// or https://.",
                "providerUrlScheme",
            )
        return ProbeConfigError(
            "The provider URL must start with https://.",
            "providerRemoteUrlScheme",
        )
    if not parts.hostname:
        return ProbeConfigError(
            "Enter a valid server URL including a host.", "providerUrlHostRequired",
        )
    if parts.username or parts.password:
        return ProbeConfigError(
            "The server URL must not contain embedded credentials.",
            "providerUrlCredentialsForbidden",
        )
    if parts.fragment:
        return ProbeConfigError(
            "The server URL must not contain a '#' fragment.",
            "providerUrlFragmentForbidden",
        )
    try:
        literal = ipaddress.ip_address(parts.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_is_blocked(literal, allow_private=allow_private):
            if allow_private:
                return ProbeConfigError(
                    "The server URL must not point at a link-local or metadata address.",
                    "providerUrlAddressForbidden",
                )
            return ProbeConfigError(
                "The provider URL must not point at a private, loopback, "
                "link-local, multicast, or reserved address.",
                "providerRemoteUrlAddressForbidden",
            )
        return None
    # A hostname: resolve it and reject if ANY address is link-local/metadata.
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, None)
    except (OSError, ValueError):
        return None  # unresolvable at setup time; the actual request fails cleanly
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            continue
        if _ip_is_blocked(ip, allow_private=allow_private):
            if allow_private:
                return ProbeConfigError(
                    "The server URL resolves to a link-local or metadata address.",
                    "providerUrlResolutionForbidden",
                )
            return ProbeConfigError(
                "The provider URL resolves to a private, loopback, link-local, "
                "multicast, or reserved address.",
                "providerRemoteUrlResolutionForbidden",
            )
    return None


async def _probe_config(kind: str, body: dict) -> ProviderConfig | ProbeConfigError:
    """Build a throwaway config or a localized setup error contract."""
    definition = PROVIDER_DEFINITIONS.get(kind)
    if definition is None:
        return ProbeConfigError("Unknown provider.", "providerUnknown")
    api_key: str | None = None
    if any(field.id == "api_key" for field in definition.fields):
        raw_key = body.get("api_key", "")
        if (not isinstance(raw_key, str) or len(raw_key) > PROVIDER_API_KEY_MAX_LENGTH
                or any(ord(char) < 32 or ord(char) == 127 for char in raw_key)):
            return ProbeConfigError("Enter your API key.", "providerApiKeyRequired")
        api_key = raw_key.strip()
        if not api_key and any(field.id == "api_key" and field.required for field in definition.fields):
            return ProbeConfigError("Enter your API key.", "providerApiKeyRequired")

    endpoint_id: str | None = None
    if definition.endpoints:
        endpoint_id = str(body.get("endpoint_id") or definition.endpoints[0].value)
        if endpoint_id not in {choice.value for choice in definition.endpoints}:
            return ProbeConfigError(
                "Choose a valid provider plan.", "providerEndpointInvalid",
            )

    if any(field.id == "base_url" for field in definition.fields):
        base_url = str(body.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            if kind == "ollama":
                return ProbeConfigError(
                    "Enter the Ollama server URL.", "ollamaUrlRequired",
                )
            return ProbeConfigError(
                "Enter the provider base URL.", "providerBaseUrlRequired",
            )
        err = await _validate_base_url(base_url, allow_private=kind == "ollama")
        if err:
            return err
    else:
        base_url = _default_base_url(kind, endpoint_id)
    model = str(body.get("model") or "").strip()
    if definition.purpose == "decision":
        if urlparse(base_url).query:
            return ProbeConfigError("The base URL must not contain a query.", "providerUrlInvalid")
        if len(model) > 200:
            return ProbeConfigError("Invalid provider configuration.", "providerUrlInvalid")
    return ProviderConfig(kind=kind, model=model or _default_model(kind),
                          base_url=base_url.rstrip("/"), api_key=api_key,
                          endpoint_id=endpoint_id)


def _stored_setup_config(cfg: ProviderConfig, model: str = "") -> dict[str, Any]:
    """Return only the setup values this provider persists, never fixed URLs."""
    definition = PROVIDER_DEFINITIONS[cfg.kind]
    out: dict[str, Any] = {}
    if any(field.id == "api_key" for field in definition.fields):
        out["api_key"] = cfg.api_key
    if any(field.id == "base_url" for field in definition.fields):
        out["base_url"] = cfg.base_url
    if cfg.endpoint_id:
        out["endpoint_id"] = cfg.endpoint_id
    if model:
        out["model"] = model
    return out


def _build_setup_provider(cfg: ProviderConfig):
    if provider_purpose(cfg.kind) == "decision":
        from .decision_provider import DecisionProvider
        return DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter)
    return build_provider(cfg)


async def _checked_decision_models(provider, session, model: object, rid: str) -> tuple[list[str], web.Response | None]:
    """Require a real advertised model before persisting a decision account."""
    from .decision_provider import DecisionError

    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        return [], _err("invalid_request", "A model is required.", 400, rid, key="providerModelRequired")
    try:
        models = await provider.list_models(session)
    except DecisionError as error:
        return [], _err("invalid_request", "Could not load decision provider models.", 400, rid,
                       key="providerConnectionFailed", params={"detail": str(error)})
    if model.strip() not in models:
        return [], _err("invalid_request", "Choose a model returned by this provider.", 400, rid,
                       key="providerModelNotListed")
    return models, None


class PhoenixAgentCliProvidersView(PhoenixView):
    """GET (list accounts) / POST (create an account) for agentCLI providers."""

    url = "/api/phoenix-mcp/admin/agentcli/providers"
    name = "api:phoenix-mcp:admin:agentcli:providers"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        purpose = request.query.get("purpose", "conversation")
        return _ok({
            "instances": [i for i in store.list_instances() if provider_purpose(i["kind"]) == purpose],
            "provider_types": _provider_catalog(purpose),
        }, request_id=rid)

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        kind = str(body.get("kind") or "")
        if kind not in PROVIDER_DEFINITIONS:
            return _err(
                "invalid_request", "Unknown provider.", 400, rid,
                key="providerUnknown",
            )

        probe = await _probe_config(kind, body)
        if isinstance(probe, ProbeConfigError):
            return _err(
                "invalid_request", probe.message, 400, rid,
                key=probe.key, params=probe.params,
            )

        model = str(body.get("model") or "").strip()
        cfg = _stored_setup_config(probe, model)

        store = await _get_secret_store(self.hass)
        if store.has_duplicate(kind, cfg):
            return _err(
                "already_exists", str(DuplicateProviderError()), 409, rid,
                key="providerAlreadyConfigured",
            )

        session = async_get_clientsession(self.hass)
        provider = _build_setup_provider(probe)
        decision_models = None
        if provider_purpose(kind) == "decision":
            decision_models, error = await _checked_decision_models(provider, session, body.get("model"), rid)
            if error is not None:
                return error
        else:
            ok, reason = await provider.validate(session)
            if not ok:
                return _err(
                    "invalid_request", f"Connection failed: {reason}", 400, rid,
                    key="providerConnectionFailed", params={"detail": reason},
                )

        try:
            instance_id = await store.add(kind, cfg)
        except DuplicateProviderError as err:
            return _err(
                "already_exists", str(err), 409, rid,
                key="providerAlreadyConfigured",
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider:{instance_id}")
        # Initial setup must establish the same declared model capabilities as
        # the explicit Refresh models action. This is metadata-only (no model
        # completion calls), but it may be one /api/show request per model for
        # Ollama. The account is already valid and stored, so a transient
        # catalogue failure must not turn a successful setup into a failure.
        try:
            models = decision_models if decision_models is not None else await provider.list_models(session)
            capabilities = await provider.list_model_capabilities(session, models)
            checked_at = utcnow().isoformat()
            await store.set_capabilities(instance_id, capabilities, checked_at)
            if provider_purpose(kind) == "conversation":
                await store.set_catalog(
                    instance_id, models, provider.model_created, checked_at,
                    [m for m in models if (capabilities.get(m) or {}).get("tools") is not False]
                    if capabilities else None,
                )
        except Exception:  # noqa: BLE001 - capability discovery is best effort
            _LOGGER.debug("Initial Agent Chat capability discovery failed", exc_info=True)
        created = next((i for i in store.list_instances() if i["id"] == instance_id), None)
        return _ok({"instance": created}, status=201, request_id=rid)


class PhoenixAgentCliProviderView(PhoenixView):
    """PATCH/DELETE /api/phoenix-mcp/admin/agentcli/providers/{instance_id} - change the default model or remove one account."""

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}"
    name = "api:phoenix-mcp:admin:agentcli:provider"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, instance_id: str) -> web.Response:
        """Change this account's default model.

        Conversational accounts permit unlisted model IDs for custom deployments.
        Decision accounts require a current advertised model, including API writes.
        """
        rid = request.get("phoenix_mcp_rid", "")
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        if unknown := set(body) - {"model", "auto"}:
            fields = ", ".join(sorted(unknown))
            return _err("invalid_request", f"Unknown field(s): {fields}.", 400, rid,
                        key="unknownFields", params={"fields": fields})
        auto = body.get("auto")
        if auto is not None and not isinstance(auto, bool):
            return _err("invalid_request", "auto must be a boolean (true or false).", 400, rid,
                        key="booleanRequired", params={"field": "auto"})
        model = str(body.get("model") or "").strip()
        if auto is True and model:
            return _err("invalid_request", "Send either a model or auto, not both.", 400, rid,
                        key="providerModelOrAuto")
        if not model and auto is not True:
            return _err(
                "invalid_request", "A model is required.", 400, rid,
                key="providerModelRequired",
            )
        store = await _get_secret_store(self.hass)
        record = store.get(instance_id)
        if auto is True and record and provider_purpose(record.get("kind", "")) == "decision":
            # Decision accounts are qualified against exact model ids.
            return _err("invalid_request", "Decision accounts need an explicit model.", 400, rid,
                        key="providerModelRequired")
        if record and provider_purpose(record.get("kind", "")) == "decision":
            cfg = store.resolve(instance_id, purpose="decision")
            if cfg is None:
                return _err("not_found", "Provider account not found.", 404, rid,
                            key="providerAccountNotFound")
            _, error = await _checked_decision_models(
                _build_setup_provider(cfg), async_get_clientsession(self.hass), body.get("model"), rid,
            )
            if error is not None:
                return error
            data: PhoenixData = self.hass.data[DOMAIN]
            async with data.settings_update_lock:
                if store.get(instance_id) != record:
                    return _err("invalid_request", "Provider account changed. Reload and try again.", 409, rid,
                                key="providerAccountChanged")
                if not await store.set_model(instance_id, model):
                    return _err("not_found", "Provider account not found.", 404, rid,
                                key="providerAccountNotFound")
        elif not await store.set_model(instance_id, model):
            return _err(
                "not_found", "Provider account not found.", 404, rid,
                key="providerAccountNotFound",
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider:{instance_id}")
        if auto is True:
            # Show the operator the model automatic mode resolves to right now.
            await async_refresh_catalog(self.hass, instance_id, force=True)
        current = next((i for i in store.list_instances() if i["id"] == instance_id), None)
        return _ok({"instance": {
            "id": instance_id, "model": (current or {}).get("model", model),
            "model_mode": "auto" if auto is True else "fixed",
        }}, request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        data = self.hass.data.get(DOMAIN)
        if data is None:
            await store.delete(instance_id)
            return _ok({"deleted": instance_id}, request_id=rid)

        # Settings PATCH validates provider references under this same lock. Delete
        # the provider and clear every dependent reference in one transaction so a
        # validated PATCH cannot persist the ID after it disappears.
        async with data.settings_update_lock:
            await store.delete(instance_id)
            async with data.store.async_lock:
                settings = data.store.get_settings()
                voice_removed = settings.voice_agent_provider_id == instance_id
                ai_task_removed = settings.ai_task_provider_id == instance_id
                patchable: dict[str, Any] = {}
                if settings.decision_provider_default_id == instance_id:
                    # One decision account must stay selected while any exists, so
                    # the default passes to a remaining account. Using it is a
                    # separate switch, which stays off (see below).
                    remaining = [
                        i["id"] for i in store.list_instances()
                        if provider_purpose(i["kind"]) == "decision"
                    ]
                    patchable["decision_provider_default_id"] = remaining[0] if remaining else None
                if settings.decision_provider_id == instance_id:
                    data.decision_shadow.cancel()
                    patchable.update(decision_provider_id=None, decision_shadow_enabled=False,
                                     agentcli_tool_routing_mode="off", voice_agent_tool_routing_mode="off", ai_task_tool_routing_mode="off")
                    for key in ("voice_agent_routing_mode", "agentcli_routing_mode"):
                        if getattr(settings, key) == "hybrid":
                            patchable[key] = "off"
                if voice_removed:
                    patchable.update(
                        voice_agent_provider_id=None,
                        voice_agent_model=None,
                    )
                if ai_task_removed:
                    patchable.update(
                        ai_task_provider_id=None,
                        ai_task_model=None,
                    )
                if patchable:
                    await data.store.async_patch_settings(**patchable)

            if voice_removed:
                if data.async_sync_voice_agent is not None:
                    data.async_sync_voice_agent()
                # The voice agent lost its provider and is unregistered;
                # remove any Phoenix-created Assist pipeline as well.
                from .voice_agent import async_remove_assist_pipeline  # noqa: PLC0415
                await async_remove_assist_pipeline(self.hass, data)
            if ai_task_removed and data.async_sync_ai_task is not None:
                # The AI Task is not fully configured; remove its entity
                # from Home Assistant and the AI Task picker.
                data.async_sync_ai_task()
        _audit_admin(self.hass, request, rid, f"provider:{instance_id}")
        return _ok({"deleted": instance_id}, request_id=rid)


class PhoenixAgentCliModelsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models - available models."""

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models"
    name = "api:phoenix-mcp:admin:agentcli:models"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id, purpose=provider_purpose((store.get(instance_id) or {}).get("kind", "")))
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        session = async_get_clientsession(self.hass)
        from .decision_provider import DecisionError
        try:
            models = await _build_setup_provider(cfg).list_models(session)
        except DecisionError:
            return _err("invalid_request", "Could not connect to the decision provider.", 400, rid,
                        key="providerConnectionFailedNoDetail")
        return _ok({"models": models}, request_id=rid)


_CATALOG_REFRESH_MIN_GAP = timedelta(seconds=60)


async def async_refresh_catalog(hass: HomeAssistant, instance_id: str, *, force: bool = False) -> bool:
    """Re-read one conversation account's model catalogue and declared capabilities.

    This is what keeps automatic mode current: it runs on a timer, when an account
    is added or refreshed, and after a provider answers "model not found". A
    per-account minimum gap stops a failing model from turning every turn into a
    catalogue request. Returns True when a catalogue was recorded.
    """
    store = await _get_secret_store(hass)
    cfg = store.resolve(instance_id)
    if cfg is None:
        return False
    attempts: dict[str, datetime] = hass.data.setdefault("_phoenix_catalog_attempts", {})
    now = utcnow()
    if not force and (last := attempts.get(instance_id)) and now - last < _CATALOG_REFRESH_MIN_GAP:
        return False
    attempts[instance_id] = now
    # Listing with the configured model blanked: a provider that cannot answer
    # falls back to "the configured model", which must not be recorded as the
    # whole catalogue.
    provider = build_provider(dataclasses.replace(cfg, model=""))
    session = async_get_clientsession(hass)
    try:
        models = await provider.list_models(session)
        previous = (store.get(instance_id) or {}).get("catalog") or {}
        changed = sorted(models) != sorted(previous.get("models") or [])
        definition = PROVIDER_DEFINITIONS[cfg.kind]
        # Ollama declares capabilities per model (one request each), so it is
        # re-read only when its library actually changed.
        capabilities = (
            await provider.list_model_capabilities(session, models)
            if models and (definition.ollama is None or changed) else None
        )
    except Exception:  # noqa: BLE001 - catalogue refresh is best effort
        _LOGGER.debug("Provider catalogue refresh failed", exc_info=True)
        return False
    checked_at = utcnow().isoformat()
    eligible = None
    if capabilities:
        # A model that declares it cannot call tools is no use to Agent Chat, so
        # automatic mode never lands on one (an undeclared capability stays eligible).
        eligible = [m for m in models if (capabilities.get(m) or {}).get("tools") is not False]
        await store.set_capabilities(instance_id, capabilities, checked_at)
    return await store.set_catalog(instance_id, models, provider.model_created, checked_at, eligible)


async def async_refresh_all_catalogs(hass: HomeAssistant) -> None:
    """Re-read the catalogues that are due (the scheduled job, run hourly).

    An account that has never had a catalogue is always read: automatic mode
    cannot choose a model without one, so that is not an optional check. Every
    other account is re-read only when the operator has periodic checks enabled
    and its last read is older than the configured period. The last-read time is
    stored with the catalogue, so a restart does not reset the schedule.
    """
    data = hass.data.get(DOMAIN)
    settings = data.store.get_settings() if data is not None else None
    enabled = settings.provider_catalog_refresh_enabled if settings is not None else True
    hours = settings.provider_catalog_refresh_hours if settings is not None else 24
    store = await _get_secret_store(hass)
    now = utcnow()
    for inst in store.list_instances():
        if provider_purpose(inst["kind"]) != "conversation":
            continue
        checked = parse_datetime(inst["models_checked_at"]) if inst.get("models_checked_at") else None
        due = checked is None or (enabled and now - checked >= timedelta(hours=hours))
        if due:
            await async_refresh_catalog(hass, inst["id"], force=True)


class PhoenixAgentCliRefreshView(PhoenixView):
    """POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh.

    Re-read one account's model list AND whatever capabilities its provider
    declares, then store both with a timestamp. Separate from the GET models
    endpoint because the two cost very different things: listing models is one
    cheap request and runs whenever the settings card is opened, while Ollama's
    capabilities cost one request PER MODEL, which is an explicit-button amount
    of work rather than a page-load amount.

    Nothing here spends completion tokens; probing the knobs that no provider
    declares is a separate step the operator consents to (the probe endpoint).
    """

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh"
    name = "api:phoenix-mcp:admin:agentcli:refresh"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id, purpose=provider_purpose((store.get(instance_id) or {}).get("kind", "")))
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        session = async_get_clientsession(self.hass)
        provider = _build_setup_provider(cfg)
        from .decision_provider import DecisionError
        try:
            models = await provider.list_models(session)
            capabilities = await provider.list_model_capabilities(session, models)
        except DecisionError:
            return _err("invalid_request", "Could not connect to the decision provider.", 400, rid,
                        key="providerConnectionFailedNoDetail")
        checked_at = utcnow().isoformat()
        if not await store.set_capabilities(instance_id, capabilities, checked_at):
            return _err(
                "not_found", "Provider account not found.", 404, rid,
                key="providerAccountNotFound",
            )
        if provider_purpose(cfg.kind) == "conversation":
            await store.set_catalog(
                instance_id, models, getattr(provider, "model_created", {}), checked_at,
                [m for m in models if (capabilities.get(m) or {}).get("tools") is not False]
                if capabilities else None,
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider_refresh:{instance_id}")
        # `declared` tells the panel whether this provider reports capabilities at
        # all, so it can say "this provider does not publish them" instead of
        # showing an empty result that reads like a failed refresh.
        return _ok({
            "models": models,
            "capabilities": capabilities,
            "declared": bool(capabilities),
            "checked_at": checked_at,
        }, request_id=rid)


class PhoenixAgentCliProbeCapsView(PhoenixView):
    """POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe.

    Ask the provider what its SELECTED model actually accepts, by sending a
    handful of one-token completions and reading the status codes.

    A SEPARATE endpoint from /refresh, and the separation is the consent
    boundary rather than tidiness: /refresh reads catalogues and costs nothing,
    while this spends the operator's own credit on real completion requests.
    Anything that bills someone gets its own button and its own confirmation.

    Only the selected model is probed. Probing a catalogue would multiply the
    spend by models nobody has chosen, and the only model whose knobs are about
    to be used is this one.
    """

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe"
    name = "api:phoenix-mcp:admin:agentcli:probecaps"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id)
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        if not cfg.model:
            return _err(
                "invalid_request", "Choose a model for this account first.", 400, rid,
                key="providerChooseModel",
            )
        session = async_get_clientsession(self.hass)
        probed, calls, answered = await async_probe_capabilities(session, cfg)
        # Image support is checked only when nothing has said what it is: a
        # provider's own declaration, or a refusal learned in conversation.
        vision_checked = answered and cfg.vision is None
        if vision_checked:
            vision = await async_probe_vision(session, cfg)
            calls += 1
            if vision is not None:
                probed["vision"] = vision

        # Merged over what the provider DECLARED rather than replacing it: the two
        # answer different questions (declared says what exists, probed says what
        # this endpoint accepts) and a probe that established nothing must leave
        # the declared answer standing.
        stored = store.get(instance_id) or {}
        existing = dict(stored.get("capabilities") or {})
        merged = {**(existing.get(cfg.model) or {}), **probed}
        existing[cfg.model] = merged
        checked_at = utcnow().isoformat()
        if not await store.set_capabilities(instance_id, existing, checked_at):
            return _err(
                "not_found", "Provider account not found.", 404, rid,
                key="providerAccountNotFound",
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider_probe:{instance_id}")
        return _ok({
            "model": cfg.model,
            "probed": probed,
            "calls": calls,
            "checked_at": checked_at,
            # Whether this backend has an effort control to ask about AT ALL.
            # Without it the panel cannot tell "asked, and the provider ignores
            # unknown options" from "there was nothing here to ask" (a backend
            # whose thinking control is a boolean flag).
            "effort_checkable": _effort_probe_body(cfg.kind, AGENTCLI_PROBE_SENTINEL) is not None,
            # False when the provider declined every question (no credit, a bad
            # key, unreachable). Reporting that as a finding about the model
            # would blame the provider for an account problem.
            "answered": answered,
            # Whether the image check ran; probed.vision says what it found.
            "vision_checked": vision_checked,
        }, request_id=rid)


class PhoenixAgentCliProbeView(PhoenixView):
    """POST /api/phoenix-mcp/admin/agentcli/probe.

    Validate a submitted credential/base URL and list the provider's models
    WITHOUT storing anything, so the Settings form can populate a model dropdown
    before the operator commits (and can cancel with no side effect).
    """

    url = "/api/phoenix-mcp/admin/agentcli/probe"
    name = "api:phoenix-mcp:admin:agentcli:probe"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        kind = str(body.get("kind") or "")
        if kind not in PROVIDER_DEFINITIONS:
            return _err(
                "invalid_request", "Unknown provider.", 400, rid,
                key="providerUnknown",
            )

        probe = await _probe_config(kind, body)
        if isinstance(probe, ProbeConfigError):
            return _ok(probe.payload(), request_id=rid)
        cfg = _stored_setup_config(probe)
        store = await _get_secret_store(self.hass)
        if store.has_duplicate(kind, cfg):
            return _err(
                "already_exists", str(DuplicateProviderError()), 409, rid,
                key="providerAlreadyConfigured",
            )
        session = async_get_clientsession(self.hass)
        provider = _build_setup_provider(probe)
        ok, reason = await provider.validate(session)
        if not ok:
            if reason:
                return _ok(
                    {
                        "ok": False,
                        "error": reason,
                        "message_passthrough": True,
                        "models": [],
                    },
                    request_id=rid,
                )
            return _ok(
                ProbeConfigError(
                    "Connection failed.", "providerConnectionFailedNoDetail",
                ).payload(),
                request_id=rid,
            )
        models = await provider.list_models(session)
        return _ok({"ok": True, "models": models}, request_id=rid)


# Admin config/models views are kill-switch-immune (like the rest of the admin
# API); the streaming chat view is agent activity and is registered through the
# kill-switch-gated route set in __init__.py.
ALL_AGENTCLI_ADMIN_VIEWS: list[type[PhoenixView]] = [
    PhoenixAgentCliProvidersView,
    PhoenixAgentCliProviderView,
    PhoenixAgentCliModelsView,
    PhoenixAgentCliRefreshView,
    PhoenixAgentCliProbeCapsView,
    PhoenixAgentCliProbeView,
]
ALL_AGENTCLI_CHAT_VIEWS: list[type[PhoenixView]] = [
    PhoenixAgentCliChatView,
]
