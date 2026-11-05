"""Provider definitions, stored accounts and their secrets."""

from __future__ import annotations

import asyncio
from collections import Counter
import dataclasses
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from typing import Any
from urllib.parse import urlparse, urlunparse
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.dt import parse_datetime, utcnow

from ..model_selection import effective_model
from ..provider_constants import (
    AGENTCLI_CEREBRAS_BASE_URL, AGENTCLI_CLAUDE_BASE_URL, AGENTCLI_DEEPSEEK_BASE_URL,
    AGENTCLI_FIREWORKS_BASE_URL, AGENTCLI_GEMINI_BASE_URL, AGENTCLI_GROK_BASE_URL,
    AGENTCLI_GROK_US_BASE_URL, AGENTCLI_GROQ_BASE_URL, AGENTCLI_KIMI_BASE_URL,
    AGENTCLI_KIMI_CHINA_BASE_URL, AGENTCLI_LEARNABLE_OPTIONS, AGENTCLI_LEARNED_REFUSAL_TTL_DAYS,
    AGENTCLI_MAX_TOOLS_PER_REQUEST, AGENTCLI_META_BASE_URL, AGENTCLI_MIMO_BASE_URL,
    AGENTCLI_MIMO_TOKEN_PLAN_AMS_BASE_URL, AGENTCLI_MIMO_TOKEN_PLAN_BASE_URL,
    AGENTCLI_MIMO_TOKEN_PLAN_SGP_BASE_URL, AGENTCLI_MINIMAX_BASE_URL,
    AGENTCLI_MINIMAX_CHINA_BASE_URL, AGENTCLI_MISTRAL_BASE_URL, AGENTCLI_MISTRAL_EU_BASE_URL,
    AGENTCLI_MISTRAL_US_BASE_URL, AGENTCLI_NVIDIA_BASE_URL, AGENTCLI_OLLAMA_CLOUD_BASE_URL,
    AGENTCLI_OPENAI_BASE_URL, AGENTCLI_OPENAI_EU_BASE_URL, AGENTCLI_OPENAI_REGION_PREFIXES,
    AGENTCLI_OPENAI_US_BASE_URL, AGENTCLI_OPENCODE_BASE_URL, AGENTCLI_OPENCODE_CONSOLE_BASE_URL,
    AGENTCLI_OPENCODE_CONSOLE_MODELS_URL, AGENTCLI_OPENROUTER_BASE_URL,
    AGENTCLI_SECRETS_STORAGE_KEY, AGENTCLI_SECRETS_STORAGE_VERSION,
    AGENTCLI_TENCENT_GUANGZHOU_BASE_URL, AGENTCLI_TENCENT_SINGAPORE_BASE_URL,
    AGENTCLI_TENCENT_US_BASE_URL, AGENTCLI_TOGETHER_BASE_URL, AGENTCLI_ZAI_BASE_URL,
    AGENTCLI_ZAI_CHINA_BASE_URL, AGENTCLI_ZAI_CODING_BASE_URL,
)
from ..routing.routing_tables import DECISION_TYPESAFE_BASE_URL, DECISION_TYPESAFE_MODEL


# How many distinct model ids an account remembers having seen listed.
_MODEL_SEEN_LIMIT = 1000


def _catalog_of(cfg: dict) -> dict:
    """The cached provider catalogue of a stored account, or {} when it has none."""
    catalog = cfg.get("catalog")
    return catalog if isinstance(catalog, dict) else {}


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
class ProviderChoice:
    """One safe, non-secret choice rendered by a provider setup field."""

    value: str
    label: str
    label_key: str | None = None
    base_url: str | None = None
    models_url: str | None = None

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
_OPENAI_REGION_NAMES = {
    "ae": ("United Arab Emirates", "settings.agentcliRegionAe"), "au": ("Australia", "settings.agentcliRegionAu"),
    "ca": ("Canada", "settings.agentcliRegionCa"), "jp": ("Japan", "settings.agentcliRegionJp"),
    "in": ("India", "settings.agentcliRegionIn"), "sg": ("Singapore", "settings.agentcliSingapore"),
    "kr": ("South Korea", "settings.agentcliRegionKr"), "gb": ("United Kingdom", "settings.agentcliRegionGb"),
}
_OPENAI_ENDPOINTS = (
    ProviderChoice(*_GLOBAL, AGENTCLI_OPENAI_BASE_URL),
    ProviderChoice("us", "United States", "settings.agentcliRegionUs", AGENTCLI_OPENAI_US_BASE_URL),
    ProviderChoice("eu", "Europe", "settings.agentcliRegionEu", AGENTCLI_OPENAI_EU_BASE_URL),
    *(ProviderChoice(prefix, *_OPENAI_REGION_NAMES[prefix], f"https://{prefix}.api.openai.com/v1")
      for prefix in AGENTCLI_OPENAI_REGION_PREFIXES),
)
_MINIMAX_ENDPOINTS = (
    ProviderChoice(*_GLOBAL, AGENTCLI_MINIMAX_BASE_URL),
    ProviderChoice("china", "China", "settings.agentcliRegionChina", AGENTCLI_MINIMAX_CHINA_BASE_URL),
)
_GROK_ENDPOINTS = (
    ProviderChoice(*_GLOBAL, AGENTCLI_GROK_BASE_URL),
    ProviderChoice("us", "United States", "settings.agentcliRegionUs", AGENTCLI_GROK_US_BASE_URL),
)
_MIMO_ENDPOINTS = (
    ProviderChoice("payg", "Pay as you go", "settings.agentcliMimoPayg", AGENTCLI_MIMO_BASE_URL),
    ProviderChoice("token_plan", "Token Plan (China)", "settings.agentcliMimoTokenPlan", AGENTCLI_MIMO_TOKEN_PLAN_BASE_URL),
    # A Token Plan key works only on the host its subscription page names.
    ProviderChoice("token_plan_sgp", "Token Plan (Singapore)", "settings.agentcliMimoTokenPlanSingapore",
                   AGENTCLI_MIMO_TOKEN_PLAN_SGP_BASE_URL),
    ProviderChoice("token_plan_ams", "Token Plan (Europe)", "settings.agentcliMimoTokenPlanEurope",
                   AGENTCLI_MIMO_TOKEN_PLAN_AMS_BASE_URL),
)
_OPENCODE_ENDPOINTS = (
    ProviderChoice("zen", "Zen (legacy)", "settings.agentcliOpenCodeZen", AGENTCLI_OPENCODE_BASE_URL),
    ProviderChoice(
        "console", "Console", "settings.agentcliOpenCodeConsole",
        AGENTCLI_OPENCODE_CONSOLE_BASE_URL, AGENTCLI_OPENCODE_CONSOLE_MODELS_URL,
    ),
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
            "chatgpt", "OpenAI", AGENTCLI_OPENAI_BASE_URL, adapter="openai_responses", include_usage=True,
            model_filter="openai",
            fields=(_region_field(_OPENAI_ENDPOINTS), _API_KEY_FIELD), endpoints=_OPENAI_ENDPOINTS,
            max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST,
        ),
        _definition("gemini", "Gemini", AGENTCLI_GEMINI_BASE_URL, reasoning="gemini", model_filter="gemini",
                    tool_stream="gemini", max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST),
        _definition(
            "grok", "Grok", AGENTCLI_GROK_BASE_URL,
            fields=(_region_field(_GROK_ENDPOINTS), _API_KEY_FIELD), endpoints=_GROK_ENDPOINTS,
        ),
        _definition("groq", "Groq", AGENTCLI_GROQ_BASE_URL, include_usage=True,
                    max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST),
        _definition(
            "kimi", "Kimi", AGENTCLI_KIMI_BASE_URL, include_usage=True, reasoning="kimi",
            fields=(_region_field(_KIMI_ENDPOINTS), _API_KEY_FIELD), endpoints=_KIMI_ENDPOINTS,
            max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST,
        ),
        _definition("meta", "Meta", AGENTCLI_META_BASE_URL, reasoning="meta", model_filter="meta"),
        _definition("minimax", "MiniMax", AGENTCLI_MINIMAX_BASE_URL, adapter="anthropic",
                    fields=(_region_field(_MINIMAX_ENDPOINTS), _API_KEY_FIELD), endpoints=_MINIMAX_ENDPOINTS),
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
        _definition(
            "opencode", "OpenCode", AGENTCLI_OPENCODE_BASE_URL,
            fields=(ProviderField("endpoint_id", "choice", "settings.agentcliOpenCodeRoute",
                                  choices=_OPENCODE_ENDPOINTS), _API_KEY_FIELD),
            endpoints=_OPENCODE_ENDPOINTS, reasoning="probed_effort", model_filter="opencode",
        ),
        _definition("ollama_cloud", "Ollama (cloud)", AGENTCLI_OLLAMA_CLOUD_BASE_URL, include_usage=True, reasoning="ollama", ollama="cloud"),
        _definition("ollama", "Ollama (local)", "", fields=(_OLLAMA_URL_FIELD,), include_usage=True, reasoning="ollama", ollama="local"),
        # OpenRouter forwards the tools array to the routed provider, and OpenAI's
        # models behind it refuse more than 128.
        _definition("openrouter", "OpenRouter", AGENTCLI_OPENROUTER_BASE_URL, include_usage=True, reasoning="probed_effort",
                    model_filter="openrouter", max_tools=AGENTCLI_MAX_TOOLS_PER_REQUEST),
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
    credential for Qwen and providers with endpoint-bound keys. OpenCode keeps
    its credential identity across Zen and Console. Compared in memory only;
    never stored or returned.
    """
    if kind == "ollama":
        return kind, _normalized_provider_url(cfg.get("base_url"))
    if kind == "qwen":
        return kind, "\0".join((
            _normalized_provider_url(cfg.get("base_url")),
            str(cfg.get("api_key") or "").strip(),
        ))
    definition = PROVIDER_DEFINITIONS.get(kind)
    if definition is not None and definition.endpoints and kind != "opencode":
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
            known = set(seen)
            seen.extend(m for m in models if m not in known)
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
