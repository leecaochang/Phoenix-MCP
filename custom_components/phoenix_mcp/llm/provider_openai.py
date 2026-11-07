"""The OpenAI-compatible Chat Completions provider client."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Iterator

from aiohttp import ClientError, ClientSession, SocketTimeoutError

from ..conversation_log import record_rejected_tool
from ..catalog_text import LocalizedReason
from .accounts import PROVIDER_DEFINITIONS, ProviderConfig
from .base import (
    _bounded_catalog, _CHAT_TIMEOUT, _created_map, _error_body, _FINISH_FAILURES,
    _gemini_tool_slot, _http_failure, _http_failure_code, _IMAGE_PROBE_TIMEOUT,
    _invalid_tool_calls, _invalid_tools_error, _is_expired, _json_error_hint,
    _merged_whole_arguments, _MISTRAL_RATE_LIMIT_HEADER_FIELDS, _model_result_text, _norm,
    _infron_capabilities, _norm_stop, _ollama_capabilities, _openrouter_capabilities, _parse_provider_error,
    _PROBE_TIMEOUT, _provider_object, _provider_tool, _ProviderResponseError,
    _rate_limit_headers, _read_json, _REFUSAL_STATUSES, _refused_option,
    _REPLAY_REASONING_KINDS, _RETRY_AFTER_HEADER_FIELDS, _retry_transient, _RETRYABLE_STATUSES,
    _silent_stream_error, _sse_lines, _ThinkSplitter, _tool_call_problem,
    _total_provider_response, _usage_int, _valid_tool_call, EV_DONE, EV_ERROR, EV_INVALID_TOOLS,
    EV_TEXT, EV_THINKING, EV_TOOL, EV_USAGE,
)
from ..provider_constants import (
    AGENT_CHAT_DEEPSEEK_MAX_TOKENS, AGENT_CHAT_NVIDIA_MAX_TOKENS, AGENT_CHAT_OPENCODE_CHAT_MODELS,
    AGENT_CHAT_OPENCODE_CONSOLE_BASE_URL, AGENT_CHAT_OPENCODE_LEGACY_CHAT_EXCEPTIONS,
    AGENT_CHAT_PROBE_IMAGE_MAX_TOKENS, AGENT_CHAT_PROMPT_CHARS_PER_TOKEN,
    AGENT_CHAT_REASONING_PROBE_MAX_TOKENS,
)


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


def _reasoned(reply: Any) -> bool:
    """Whether a finished Chat Completion shows reasoning, as text or as counted tokens."""
    choices = reply.get("choices") if isinstance(reply, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if isinstance(message, dict) and any(message.get(field) for field in ("reasoning", "reasoning_content", "reasoning_details")):
        return True
    usage = reply.get("usage") if isinstance(reply, dict) else None
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
    return isinstance(tokens, int) and tokens > 0


def _filter_infron_models(data: list[dict]) -> list[str]:
    """Keep Infron's tool-capable text models reachable through Chat Completions.

    The catalog also lists media, embedding and Responses-only models. Sorted for a stable
    dropdown; falls back to every id if none qualify (schema drift).
    """
    rows = [m for m in data if isinstance(m, dict) and m.get("id")]
    return sorted(
        str(m["id"]) for m in rows
        if m.get("category_type") == "LLM" and m.get("supports_function_calling") is True
        and m.get("output_modalities") == ["text"]
        and "openai" in (m.get("supported_endpoint_types") or [])
        and not m.get("deprecated") and not m.get("is_display_only")
    ) or sorted(str(m["id"]) for m in rows)


def _filter_mistral_models(data: list[dict]) -> list[str]:
    """Keep active Mistral models declared fit for tool-driven chat.

    A model must declare completion_chat and function_calling. If that schema disappears entirely,
    fall back to active ids rather than an empty account.
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


# OpenCode Zen serves one catalogue through several wire formats. Only the
# chat-completions family is reachable by this client, so models Zen documents on
# its Responses, Anthropic-messages, Gemini or typed-decision endpoints are left
# out. Legacy accounts retain their family fallback plus exact qualified
# exceptions; Console requires documented per-model Chat Completions support.
_OPENCODE_OTHER_ENDPOINT_RE = re.compile(
    r"^(?:gpt|grok|claude|gemini|jev|muse|qwen)", re.I,
)
# Mistral AI's model families, whose replies OpenCode forwards in Mistral's content shape.
_MISTRAL_FAMILY_RE = re.compile(r"^(?:mistral|magistral|ministral|devstral|codestral)-", re.I)


# OpenCode's free models answer only OpenCode's own app; any other client gets
# HTTP 403 FreeTierError, so they are never offered or chosen automatically.
_OPENCODE_FREE_RE = re.compile(r"^big-pickle$|-free$")


def _filter_opencode_models(models: list[str], *, verified_only: bool = False) -> list[str]:
    """Require Console endpoint evidence and preserve qualified legacy exceptions."""
    known_chat = (AGENT_CHAT_OPENCODE_CHAT_MODELS if verified_only
                  else AGENT_CHAT_OPENCODE_LEGACY_CHAT_EXCEPTIONS)
    return sorted(
        m for m in models
        if not _OPENCODE_FREE_RE.search(m)
        and (m in known_chat or (not verified_only and not _OPENCODE_OTHER_ENDPOINT_RE.match(m)
                                 and not _NON_CHAT_MODEL_RE.search(m)))
    )


def _filter_meta_models(models: list[str]) -> list[str]:
    """Keep Meta's chat models: the Muse Wonder and Spark text lines, without the data-sharing tier.
    """
    # Meta serves image, voice and segmentation models under the same key, and a `-contributor`
    # model trains on the prompts it receives, which must be an explicit operator choice.
    chat = sorted(m for m in models if m.lower().startswith(("muse-spark", "muse-wonder"))
                  and "contributor" not in m.lower())
    return chat or sorted(models)


def _filter_nvidia_models(models: list[str]) -> list[str]:
    """Drop NVIDIA's embedding and reranking models from the dropdown, sorted.

    Falls back to every id if the naming convention changes enough to filter everything out.
    """
    # NVIDIA's catalog advertises no per-model parameters, so tool support cannot be detected and is
    # left to the operator; only ids that provably cannot chat are removed.
    chat = sorted(
        m for m in models
        if not _NON_CHAT_MODEL_RE.search(m) and not re.search(
            r"rerank|reward|safety|parse|clip|vila|neva|kosmos|deplot|detector|"
            r"retriever|calibration|riva", m, re.I)
    )
    return chat or sorted(models)


class OpenAICompatProvider:
    """OpenAI-compatible chat/completions, streaming.

    Backs every conversation provider whose definition does not use the Anthropic adapter. Ollama
    differs slightly: the OpenAI path lives under /v1, models come from /api/tags, and older
    endpoints keep a boolean UI fallback until a probe proves reasoning_effort levels. Local Ollama
    is keyless with a user-supplied base URL and is the fragile tool backend (see _parse); Ollama
    Cloud is the same shape at a fixed URL with a Bearer API key.
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

    def _models_url(self) -> str:
        for choice in self.definition.endpoints:
            if choice.base_url == self.cfg.base_url and choice.models_url:
                return choice.models_url
        return f"{self.cfg.base_url}/models"

    @property
    def _is_opencode_console(self) -> bool:
        return self.kind == "opencode" and self.cfg.base_url == AGENT_CHAT_OPENCODE_CONSOLE_BASE_URL

    @property
    def _unsupported_model_api(self) -> bool:
        return self._is_opencode_console and self.cfg.model not in AGENT_CHAT_OPENCODE_CHAT_MODELS

    @property
    def _mistral_content(self) -> bool:
        """Whether replies use Mistral's typed content parts; OpenCode's model family decides it."""
        return self.definition.model_filter == "mistral" or (
            self.kind == "opencode" and bool(_MISTRAL_FAMILY_RE.match(self.cfg.model)))

    def _model_fallback(self) -> list[str]:
        return [self.cfg.model] if self.cfg.model and not self._unsupported_model_api else []

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
        parts: list[dict] = []
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
                parts.append({
                    "type": "text",
                    "text": f"The tool call {r['tool_use_id']} returned the following image for visual inspection.",
                })
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
        # Images go in one user message after EVERY tool result: the API requires
        # all of a turn's tool messages to follow its assistant message directly.
        if parts:
            messages.append({"role": "user", "content": parts})

    async def validate(self, session: ClientSession) -> tuple[bool, str]:
        async def _probe() -> tuple[bool, str]:
            if self._is_ollama:
                async with session.get(f"{self.cfg.base_url}/api/tags",
                                       headers=self._headers(), timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                    if resp.status == 200:
                        return True, ""
                    if resp.status in (401, 403):
                        return False, LocalizedReason("API key rejected.", "providerKeyRejected")
                    label = "Ollama Cloud" if self._is_ollama_cloud else "Ollama"
                    return False, LocalizedReason(f"{label} returned HTTP {resp.status}.", "providerNamedHttpStatus", provider=label, status=resp.status)
            async with session.get(self.definition.key_check_url or self._models_url(), headers=self._headers(),
                                   timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status == 200:
                    return True, ""
                if resp.status in (401, 403):
                    return False, LocalizedReason("API key rejected.", "providerKeyRejected")
                if self.definition.validation_error == "fireworks" and resp.status == 412:
                    return False, LocalizedReason(
                        "Fireworks reports that this account is not active for API inference. "
                        "Check the account status and confirm that billing or free-trial credits "
                        "are activated.",
                        "providerFireworksInactive",
                    )
                if self.definition.validation_error == "qwen_openai" and resp.status == 404:
                    return False, LocalizedReason(
                        "Qwen returned HTTP 404. Phoenix uses the OpenAI-compatible Chat "
                        "Completions endpoint; enter that Base URL, not the "
                        "Anthropic-compatible /apps/anthropic URL.",
                        "providerQwenWrongEndpoint",
                    )
                return False, LocalizedReason(f"Provider returned HTTP {resp.status}.", "providerHttpStatus", status=resp.status)
        try:
            return await _retry_transient(_probe)
        except ValueError:
            return False, LocalizedReason("Invalid provider connection settings.", "providerSettingsInvalid")
        except (ClientError, asyncio.TimeoutError) as exc:
            if self._is_ollama_local:
                # Newlines are preserved in the panel (the banner uses
                # white-space: pre-line), so keep this as a short lead plus a
                # scannable checklist rather than one dense paragraph.
                return False, LocalizedReason(
                    f"Could not reach Ollama at {self.cfg.base_url}.\n"
                    f"({exc})\n\n"
                    "Phoenix MCP connects from the Home Assistant server, not your browser, so "
                    "Ollama must be reachable from that host:\n"
                    "- Ollama is running and bound to a reachable interface (OLLAMA_HOST=0.0.0.0).\n"
                    "- The firewall allows the Ollama port (default 11434).\n"
                    "- 'localhost' here means the HA server, not your PC.",
                    "providerOllamaUnreachable", url=self.cfg.base_url, detail=str(exc),
                )
            if self._is_ollama_cloud:
                return False, LocalizedReason(f"Could not reach Ollama Cloud at {self.cfg.base_url}. ({exc})", "providerOllamaCloudUnreachable", url=self.cfg.base_url, detail=str(exc))
            return False, LocalizedReason(f"Could not reach the provider: {exc}", "providerUnreachable", detail=str(exc))

    @_bounded_catalog
    async def list_models(self, session: ClientSession) -> list[str]:
        try:
            if self._is_ollama:
                async with session.get(f"{self.cfg.base_url}/api/tags",
                                       headers=self._headers(), timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                    if resp.status != 200:
                        return []
                    body = await _read_json(resp)
                self.model_created = _created_map(body.get("models", []))
                return [m.get("name") or m.get("model") for m in body.get("models", [])
                        if (m.get("name") or m.get("model"))]
            async with session.get(self._models_url(), headers=self._headers(),
                                   timeout=_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    return self._model_fallback()
                body = await _read_json(resp)
            data = (
                body if self.definition.models_shape == "list" and isinstance(body, list)
                else body.get("data", []) if isinstance(body, dict)
                else []
            )
            if self.definition.model_filter == "opencode":
                data = [row for row in (data if isinstance(data, list) else []) if isinstance(row, dict)
                        and isinstance(row.get("id"), str) and row["id"]]
            data = [row for row in data if not _is_expired(row)]
            self.model_created = _created_map(data)
            if self.definition.model_filter == "openrouter":
                return _filter_openrouter_models(data) or ([self.cfg.model] if self.cfg.model else [])
            if self.definition.model_filter == "infron":
                return _filter_infron_models(data) or ([self.cfg.model] if self.cfg.model else [])
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
                return _filter_opencode_models(models, verified_only=self._is_opencode_console)
            elif self.definition.model_filter == "meta":
                models = _filter_meta_models(models)
            elif self.definition.model_filter == "chat":
                models = _filter_generic_chat_models(models)
            return models or ([self.cfg.model] if self.cfg.model else [])
        except (ClientError, asyncio.TimeoutError, ValueError):
            return self._model_fallback()

    async def probe_image(self, session: ClientSession, image: str, question: str) -> tuple[int | None, str]:
        """Status and reply text for one question about an attached PNG (see async_probe_vision)."""
        if self._unsupported_model_api:
            return 400, ""
        token_limit = ("max_completion_tokens" if self.kind in ("mimo", "chatgpt", "meta")
                       else "max_tokens")
        body: dict = {"model": self.cfg.model, token_limit: AGENT_CHAT_PROBE_IMAGE_MAX_TOKENS, "stream": False,
                      "messages": [self.user_message(question, [{"mime_type": "image/png", "data": image}])]}
        if self.definition.reasoning == "deepseek":
            # A DeepSeek reasoning model can spend the whole allowance thinking
            # about an image it cannot see and return no answer; it has an off switch.
            body["thinking"] = {"type": "disabled"}
        try:
            async with session.post(self._chat_url(), headers=self._headers(), json=body,
                                    timeout=_IMAGE_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    return resp.status, _parse_provider_error(await _error_body(resp))[0]
                data = await _read_json(resp)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None, ""
        choices = data.get("choices") if isinstance(data, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict) and isinstance(part.get("text"), str))
        return 200, content if isinstance(content, str) else ""

    async def probe_reasoning(self, session: ClientSession, effort: str) -> tuple[int | None, bool]:
        """Status for a small question at this effort, and whether the model visibly reasoned."""
        body = {
            "model": self.cfg.model, "max_tokens": AGENT_CHAT_REASONING_PROBE_MAX_TOKENS, "stream": False,
            "messages": [{"role": "user", "content": "What is 17 times 23? Answer with the number."}],
            "reasoning": {"effort": effort},
        }
        try:
            async with session.post(self._chat_url(), headers=self._headers(), json=body,
                                    timeout=_IMAGE_PROBE_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    return resp.status, False
                return 200, _reasoned(await _read_json(resp))
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None, False

    async def probe_option(self, session: ClientSession, extra: dict) -> int | None:
        """HTTP status for the smallest possible completion carrying `extra`.

        None when the provider could not be reached at all, which is not an
        answer about the option and must not be recorded as one.
        """
        if self._unsupported_model_api:
            return 400
        # OpenAI's reasoning models reject max_tokens, and Meta deprecates it.
        token_limit = ("max_completion_tokens" if self.kind in ("mimo", "chatgpt", "meta")
                       else "max_tokens")
        # DashScope's stream-only models refuse a non-streamed call outright,
        # which would read as a refused control; only the status matters here.
        body = {"model": self.cfg.model, token_limit: 1, "stream": self.kind == "qwen",
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
        """What each model declares it accepts; empty when the provider says nothing.

        Empty means "declared nothing", never "supports nothing", or a failed lookup would strip a
        working model's controls.
        """
        # OpenRouter, Infron, Mistral and Ollama report this; Z.ai, Qwen and MiMo are credited with thinking
        # from their reasoning style. Other backends list only an id and an owner, so the shipped
        # capability table stays and probing is a separate step.
        try:
            if self._is_ollama:
                return await _ollama_capabilities(
                    session, self.cfg.base_url, self._headers(), models)
            if self.definition.model_filter in ("openrouter", "infron"):
                async with session.get(
                    f"{self.cfg.base_url}/models", headers=self._headers(),
                    timeout=_PROBE_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        return {}
                    body = await _read_json(resp)
                if self.definition.model_filter == "infron":
                    return _infron_capabilities(body.get("data", []))
                return _openrouter_capabilities(body.get("data", []))
            if self.definition.model_filter == "mistral":
                async with session.get(
                    f"{self.cfg.base_url}/models", headers=self._headers(),
                    timeout=_PROBE_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        return {}
                    body = await _read_json(resp)
                return _mistral_capabilities(body.get("data", []), models)
            if self.definition.reasoning in ("zai", "qwen", "mimo"):
                return {model: {"thinking": True} for model in models}
        except (ClientError, asyncio.TimeoutError, ValueError, AttributeError, KeyError, TypeError):
            return {}
        return {}

    async def stream_turn(
        self, session: ClientSession, *, system_prompt: str, messages: list[dict],
        tools: list[dict], options: dict,
    ) -> AsyncIterator[dict]:
        # OpenAI shape carries the system prompt as the first message.
        if self._unsupported_model_api:
            yield _norm(EV_ERROR, status=0, code="model_api_unsupported",
                        message="This model is not qualified for OpenCode Chat Completions.", retryable=False)
            return
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
            # With thinking off, DeepSeek's 8K default can truncate a large tool
            # call mid-JSON; with thinking on (its default) the defaults are
            # larger than any cap Phoenix would set. See provider_constants.py.
            if thinking is False:
                body["max_tokens"] = AGENT_CHAT_DEEPSEEK_MAX_TOKENS
        elif self.definition.reasoning == "effort":
            # xAI (and OpenAI's Chat Completions, which Phoenix now uses only for
            # probes; turns go through OpenAIResponsesProvider) take a top-level
            # reasoning_effort; sending it marks a reasoning turn (skip
            # temperature). A model that does not accept the level returns a
            # clean error.
            if effort:
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
            # never fires. The wrong guess costs nothing. OpenRouter's nested
            # `reasoning` object carries every level its catalog lists, max
            # included, which its top-level reasoning_effort does not. Infron
            # documents only the nested form.
            if effort and self.kind in ("openrouter", "infron"):
                body["reasoning"] = {"effort": effort}
            elif effort:
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
            # GLM-5.2 and later take reasoning_effort (default max) beside the
            # thinking switch; "none" there means thinking off.
            if effort == "none":
                thinking = False
            elif effort:
                body["reasoning_effort"] = effort
            if thinking is not None:
                body["thinking"] = {"type": "enabled" if thinking else "disabled"}
                if thinking and tools:
                    # Preserved thinking: keep earlier rounds' reasoning_content
                    # (replayed below) instead of discarding it. It is off by
                    # default on the standard API and lives inside `thinking`.
                    body["thinking"]["clear_thinking"] = False
            if tools:
                body["tool_stream"] = True
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
            # Mistral treats temperature 0 as greedy sampling and refuses it unless
            # top_p is 1; the refusal names only top_p, so it cannot be learned.
            if self._mistral_content and body.get("temperature") == 0:
                body["top_p"] = 1
        if self.kind == "nvidia":
            body["max_tokens"] = AGENT_CHAT_NVIDIA_MAX_TOKENS
        show_thinking = bool(options.get("show_thinking"))
        # A local Ollama server silently drops the start of a prompt longer than the
        # context it loaded the model with: the system prompt and tool rules first.
        prompt_tokens = (_estimated_prompt_tokens(body) if self._is_ollama_local else 0)
        loaded = await self._loaded_context(session) if prompt_tokens else None
        if (problem := _ollama_context_problem(prompt_tokens, loaded)) is not None:
            yield problem
            return
        try:
            for attempt in range(2):
                async with session.post(self._chat_url(), headers=self._headers(), json=body,
                                        timeout=_CHAT_TIMEOUT, allow_redirects=False) as resp:
                    if resp.status != 200:
                        text = await _error_body(resp)
                        msg = _parse_provider_error(text)[0]
                        if (attempt == 0 and self.kind == "nvidia" and resp.status in _REFUSAL_STATUSES
                                and "max_tokens" in msg.lower()):
                            # This model's range is below Phoenix's cap: use its own default.
                            body.pop("max_tokens", None)
                            continue
                        # Ollama commonly 500s on an unknown/hallucinated tool name.
                        code = _http_failure_code(resp.status, "ollama_backend" if self._is_ollama else "upstream")
                        retryable = resp.status in _RETRYABLE_STATUSES
                        rate_limit = _rate_limit_headers(
                            resp.headers,
                            _MISTRAL_RATE_LIMIT_HEADER_FIELDS if self.definition.model_filter == "mistral"
                            else _RETRY_AFTER_HEADER_FIELDS,
                        ) if resp.status in (429, 503, 529) else {}
                        yield _http_failure(self.kind, resp.status, text, code=code, retryable=retryable,
                                            api_key=self.cfg.api_key, refused=_refused_option(resp.status, msg, body),
                                            **({"rate_limit": rate_limit} if rate_limit else {}))
                        return
                    checked = not prompt_tokens or loaded is not None
                    async for ev in self._parse(resp, show_thinking):
                        if not checked and ev.get("type") in (EV_TOOL, EV_DONE, EV_INVALID_TOOLS):
                            # The model was loaded by this request: now its context is
                            # known, and no call from a cut-off prompt may run.
                            checked = True
                            problem = _ollama_context_problem(prompt_tokens, await self._loaded_context(session))
                            if problem is not None:
                                yield problem
                                return
                        yield ev
                    return
        except SocketTimeoutError:
            yield _silent_stream_error()
        except (ClientError, asyncio.TimeoutError):
            yield _norm(EV_ERROR, status=0, code="network", message="Provider connection failed.", retryable=True)

    async def _loaded_context(self, session: ClientSession) -> int | None:
        """The context length a local Ollama server loaded this model with, or None.

        Read from /api/ps, which lists only loaded models; None when the model is
        not loaded or the server does not say, so nothing is refused on a guess.
        """
        try:
            async with session.get(f"{self.cfg.base_url}/api/ps", timeout=_PROBE_TIMEOUT,
                                   allow_redirects=False) as resp:
                if resp.status != 200:
                    return None
                data = await _read_json(resp)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None
        for row in data.get("models") or [] if isinstance(data, dict) else []:
            if isinstance(row, dict) and self.cfg.model in (row.get("name"), row.get("model")):
                length = row.get("context_length")
                return length if type(length) is int and length > 0 else None
        return None

    @_total_provider_response
    async def _parse(self, resp: Any, show_thinking: bool) -> AsyncIterator[dict]:
        stream = _ChatStream(self.definition.tool_stream == "gemini")
        async for line in _sse_lines(resp):
            payload = _sse_payload(line)
            if not payload:
                continue
            if payload == "[DONE]":
                break
            for event in self._chunk_events(_provider_object(payload), stream, show_thinking):
                yield event
        _settle_finish(stream)
        # Flush any held-back tail from the think splitter.
        for event in _segment_events(stream.think.flush(), stream, show_thinking):
            yield event
        assistant_tool_calls, parsed_tools, checked, bad = _checked_tool_calls(stream)
        assistant_msg = self._assistant_message(stream, assistant_tool_calls)
        if bad:
            yield _bad_tool_call_event(stream, assistant_msg, assistant_tool_calls, checked)
            return
        _refuse_unusable_end(stream, parsed_tools)
        for tool_call in parsed_tools:
            yield _norm(EV_TOOL, id=tool_call["id"], name=tool_call["name"],
                        input=tool_call["input"])
        yield _norm(EV_DONE, stop_reason=_norm_stop(stream.finish), assistant_msg=assistant_msg)

    def _chunk_events(self, chunk: dict, stream: _ChatStream, show_thinking: bool) -> Iterator[dict]:
        """Fold one stream chunk into `stream`, yielding its events in the order they apply."""
        usage = _usage_event(chunk)
        if usage is not None:
            yield usage
        picked = _chunk_choice(chunk)
        if picked is None:
            return
        choice, delta = picked
        thinking = self._kept_reasoning(delta, stream, show_thinking)
        if thinking is not None:
            yield thinking
        yield from self._content_events(delta, stream, show_thinking)
        _merge_tool_fragments(delta, stream)
        if choice.get("finish_reason"):
            stream.finish = choice["finish_reason"]
            stream.saw_finish = True

    def _kept_reasoning(self, delta: dict, stream: _ChatStream, show_thinking: bool) -> dict | None:
        """Retain the reasoning this provider needs back; the thinking event to show, or None."""
        # Retain protocol-required reasoning even when its display is hidden.
        # Ollama's reasoning field remains display-only.
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            raise _ProviderResponseError()
        if self.kind in ("openrouter", "infron") and isinstance(delta.get("reasoning_details"), list):
            stream.reasoning_details.extend(d for d in delta["reasoning_details"] if isinstance(d, dict))
        if reasoning and self.kind in _REPLAY_REASONING_KINDS:
            stream.replay_reasoning_parts.append(str(reasoning))
        if reasoning and show_thinking:
            return _norm(EV_THINKING, text=reasoning)
        return None

    def _content_events(self, delta: dict, stream: _ChatStream, show_thinking: bool) -> Iterator[dict]:
        """Refuse a content of the wrong shape, else fold it in and yield what it shows."""
        content = delta.get("content")
        if content is not None and not isinstance(content, str) and not (
            self._mistral_content and isinstance(content, list)
        ):
            raise _ProviderResponseError()
        if self._mistral_content and isinstance(content, list):
            yield from _mistral_block_events(content, stream, show_thinking)
        elif self._mistral_content and isinstance(content, str) \
                and content and stream.mistral_content_parts:
            # Mistral may switch from structured ThinkChunk content to
            # ordinary string deltas for the answer. Materialize those as
            # TextChunk content after the retained thinking blocks.
            stream.text_parts.append(content)
            if stream.mistral_content_parts[-1].get("type") == "text":
                old_text = str(stream.mistral_content_parts[-1].get("text", ""))
                stream.mistral_content_parts[-1]["text"] = old_text + content
            else:
                stream.mistral_content_parts.append({"type": "text", "text": content})
            yield _norm(EV_TEXT, text=content)
        elif isinstance(content, str) and content:
            yield from _segment_events(stream.think.feed(content), stream, show_thinking)

    def _assistant_message(self, stream: _ChatStream, assistant_tool_calls: list[dict]) -> dict:
        """The assistant message that goes back to the provider on the next turn."""
        # Use "" (not None) for a tool-only reply: a null content serializes to
        # JSON null, which Ollama's OpenAI-compatible endpoint rejects on the next
        # turn ("invalid message content type: <nil>"). An empty string is valid
        # for OpenAI/DeepSeek/Gemini too, so this is safe across all of them.
        answer_text = "".join(stream.text_parts)
        if self._mistral_content and stream.mistral_content_parts:
            assistant_content: str | list[dict] = stream.mistral_content_parts
        else:
            assistant_content = answer_text
        assistant_msg: dict = {"role": "assistant", "content": assistant_content}
        if stream.replay_reasoning_parts:
            assistant_msg["reasoning_content"] = "".join(stream.replay_reasoning_parts)
        if stream.reasoning_details:
            assistant_msg["reasoning_details"] = stream.reasoning_details
        if assistant_tool_calls:
            assistant_msg["tool_calls"] = assistant_tool_calls
        return assistant_msg


# (id, name, parsed arguments or None, model-facing JSON error or None).
_CheckedCall = tuple[Any, Any, Any, str | None]


class _ChatStream:
    """What one Chat Completions stream has accumulated so far."""

    def __init__(self, gemini: bool) -> None:
        self.text_parts: list[str] = []
        # The providers in _REPLAY_REASONING_KINDS retain reasoning_content verbatim
        # with the assistant tool call on the next round. Other compatible
        # providers use this field for display only, so retention is
        # profile-controlled.
        self.replay_reasoning_parts: list[str] = []
        # OpenRouter requires its structured `reasoning_details` array to be sent
        # back unmodified, in order, across tool calls (encrypted and summarised
        # reasoning cannot be rebuilt from text).
        self.reasoning_details: list = []
        # Mistral emits reasoning as structured ThinkChunk content. Unlike the
        # display-only reasoning fields used by other compatible APIs, Mistral
        # requires these chunks to be replayed in the assistant message on the
        # next turn, even when verbose output is hidden in Phoenix.
        self.mistral_content_parts: list[dict] = []
        # tool calls accumulated by index: {index: {"id","name","args"}}
        self.calls: dict[int, dict] = {}
        # Gemini's compatibility layer deviates from OpenAI streaming for tool
        # calls (see _merged_whole_arguments and _gemini_tool_slot).
        self.gemini = gemini
        self.pending_extra: dict | None = None
        self.finish = "stop"
        # Whether the stream ever reported WHY it ended. Every provider sends a
        # finish_reason on its final chunk, so the default above standing
        # untouched means the response stopped arriving rather than finishing:
        # the one signal that tells a truncated tool call apart from a malformed
        # one. Deliberately not keyed on the "[DONE]" sentinel, which is an
        # OpenAI convention some compatible backends never send, and which would
        # therefore relabel their genuinely malformed calls as dropped ones.
        self.saw_finish = False
        # Some local reasoning models (e.g. r1 via Ollama) emit their chain of
        # thought inline as <think>...</think> inside content rather than in a
        # separate reasoning field. Split it out so it is treated as thinking
        # (shown only when show_thinking) and never leaks into the reply or the
        # message history sent back next turn.
        self.think = _ThinkSplitter()


def _sse_payload(line: str) -> str:
    """The payload of one SSE line, empty when it has none; bare JSON outside the framing is refused."""
    if not line.startswith("data:"):
        if line.lstrip().startswith(("{", "[")):
            _provider_object(line)
            raise _ProviderResponseError()
        return ""
    return line[5:].strip()


def _usage_event(chunk: dict) -> dict | None:
    """The usage event of one chunk, or None when it reports no tokens."""
    # Usage must be read BEFORE the empty-choices skip: OpenAI's final
    # usage chunk (stream_options include_usage) carries choices: [].
    # Some backends (Gemini, Grok) volunteer usage on content chunks
    # instead; cumulative-replace semantics cover both. Ollama sends
    # usage only when include_usage is requested.
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        pt = _usage_int(usage.get("prompt_tokens"))
        ct = _usage_int(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        cached = _usage_int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        cached = cached or _usage_int(usage.get("prompt_cache_hit_tokens"))
        if pt or ct:
            return _norm(EV_USAGE, input_tokens=pt, output_tokens=ct, cache_read_tokens=cached)
    return None


def _chunk_choice(chunk: dict) -> tuple[dict, dict] | None:
    """The first choice of a chunk and its delta, None when it has no choices; refuses a bad shape or a block."""
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        raise _ProviderResponseError()
    if not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        raise _ProviderResponseError()
    delta = choice.get("delta") or {}
    if not isinstance(delta, dict):
        raise _ProviderResponseError()
    if delta.get("refusal") or choice.get("finish_reason") == "content_filter":
        raise _ProviderResponseError("provider_blocked", retryable=False)
    return choice, delta


def _segment_events(
    segments: list[tuple[str, str]], stream: _ChatStream, show_thinking: bool,
) -> Iterator[dict]:
    """Yield think-splitter segments: text joins the reply, thinking shows only when asked."""
    for kind, seg in segments:
        if kind == "text":
            stream.text_parts.append(seg)
            yield _norm(EV_TEXT, text=seg)
        elif show_thinking:
            yield _norm(EV_THINKING, text=seg)


def _mistral_block_events(content: list, stream: _ChatStream, show_thinking: bool) -> Iterator[dict]:
    """Retain Mistral's structured content blocks and yield what each one shows."""
    if not stream.mistral_content_parts and any(isinstance(block, dict) for block in content):
        # Replay sends only the retained parts, so text that streamed as plain deltas joins
        # them first. The think splitter is not fed after this, so its held tail ends here.
        yield from _segment_events(stream.think.flush(), stream, show_thinking)
        if stream.text_parts:
            stream.mistral_content_parts.append({"type": "text", "text": "".join(stream.text_parts)})
    for block in content:
        if not isinstance(block, dict):
            continue
        # Retain each structured block in its original order and
        # shape. Mistral explicitly requires the full assistant
        # content, including ThinkChunk, on subsequent turns.
        stream.mistral_content_parts.append(block)
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
            stream.text_parts.append(seg)
            yield _norm(EV_TEXT, text=seg)


def _merge_tool_fragments(delta: dict, stream: _ChatStream) -> None:
    """Fold a delta's tool call fragments into `stream.calls`, refusing a bad shape."""
    # OpenAI-compatible servers serialize an unset field as null.
    fragments = delta.get("tool_calls")
    if fragments is None:
        fragments = []
    if not isinstance(fragments, list):
        raise _ProviderResponseError()
    if stream.gemini and isinstance(delta.get("extra_content"), dict):
        # A thought signature can arrive on the delta rather than the call.
        stream.pending_extra = delta["extra_content"]
    for tc in fragments:
        _merge_tool_fragment(tc, stream)


def _merge_tool_fragment(tc: Any, stream: _ChatStream) -> None:
    """Fold one tool call fragment into its slot; a rejected one is recorded before the refusal."""
    if not isinstance(tc, dict):
        raise _ProviderResponseError()
    idx = _gemini_tool_slot(stream.calls, tc) if stream.gemini else tc.get("index", 0)
    if type(idx) is not int or idx < 0:
        raise _ProviderResponseError()
    slot = stream.calls.setdefault(idx, {"id": None, "name": None, "args": ""})
    if tc.get("id"):
        slot["id"] = tc["id"]
    if stream.gemini:
        extra = tc.get("extra_content")
        if isinstance(extra, dict):
            slot["extra_content"] = extra
        elif stream.pending_extra is not None and "extra_content" not in slot:
            slot["extra_content"] = stream.pending_extra
        stream.pending_extra = None
    elif isinstance(tc.get("thought_signature"), str) and tc["thought_signature"]:
        # Infron carries a thought signature on the call itself.
        slot["thought_signature"] = tc["thought_signature"]
    fn = tc.get("function")
    if fn is None:
        fn = {}
    if not isinstance(fn, dict):
        raise _ProviderResponseError()
    if fn.get("name"):
        slot["name"] = fn["name"]
    if "arguments" in fn:
        if not isinstance(fn["arguments"], str):
            record_rejected_tool(slot["id"], slot["name"], fn["arguments"],
                                 reason="The provider must send tool arguments as JSON text.")
            raise _ProviderResponseError()
        slot["args"] = (_merged_whole_arguments(slot["args"], fn["arguments"]) if stream.gemini
                        else slot["args"] + fn["arguments"])


def _settle_finish(stream: _ChatStream) -> None:
    """Refuse a finish reason that means failure and normalize the rest."""
    if stream.finish in _FINISH_FAILURES:
        code, retryable = _FINISH_FAILURES[stream.finish]
        raise _ProviderResponseError(code, retryable=retryable)
    if stream.finish == "repetition_truncation":
        # MiMo cut a degenerate repetition short: the output stopped early.
        stream.finish = "length"
    if stream.gemini and stream.finish == "stop" and stream.calls:
        # Gemini ends a tool-call response with "stop" rather than "tool_calls".
        stream.finish = "tool_calls"


def _checked_tool_calls(stream: _ChatStream) -> tuple[list[dict], list[dict], list[_CheckedCall], bool]:
    """Judge each accumulated call: (history form, runnable, checked rows, any unparseable JSON)."""
    assistant_tool_calls = []
    parsed_tools = []
    bad = False
    checked: list[_CheckedCall] = []
    for idx in sorted(stream.calls):
        slot = stream.calls[idx]
        args_str = slot["args"] or "{}"
        hint = None
        try:
            parsed = json.loads(args_str)
            if not _valid_tool_call(slot["id"], slot["name"], parsed):
                record_rejected_tool(slot["id"], slot["name"], slot["args"],
                                     reason=_tool_call_problem(slot["id"], slot["name"]),
                                     finish_reason=stream.finish if stream.saw_finish else None)
                raise _ProviderResponseError(retryable=stream.finish != "length")
        except json.JSONDecodeError as exc:
            bad = True
            parsed = None
            hint = _json_error_hint(args_str, exc)
            reason = ("The output length limit interrupted the tool arguments." if stream.finish == "length"
                      else "The stream ended before the tool arguments were complete." if not stream.saw_finish
                      else "Tool arguments are not valid JSON.")
            record_rejected_tool(slot["id"], slot["name"], slot["args"], reason=reason,
                                 finish_reason=stream.finish if stream.saw_finish else None, error=exc)
        checked.append((slot["id"], slot["name"], parsed, hint))
        assistant_tool_calls.append({
            "id": slot["id"], "type": "function",
            "function": {"name": slot["name"], "arguments": args_str},
            # Gemini's thought signature must return with its call, unchanged,
            # or the next request is refused.
            **({"extra_content": slot["extra_content"]} if slot.get("extra_content") else {}),
            **({"thought_signature": slot["thought_signature"]} if slot.get("thought_signature") else {}),
        })
        if parsed is not None:
            parsed_tools.append({"id": slot["id"], "name": slot["name"], "input": parsed})
    return assistant_tool_calls, parsed_tools, checked, bad


def _bad_tool_call_event(
    stream: _ChatStream, assistant_msg: dict, assistant_tool_calls: list[dict], checked: list[_CheckedCall],
) -> dict:
    """The one event that ends a response whose tool arguments never parse as JSON."""
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
    if stream.finish == "length":
        code = "tool_call_truncated"
        retryable = False
        message = (
            "The model hit its output length limit in the middle of a tool "
            "call, so the action was not run. The requested change was too "
            "large to emit in one call; try asking for a smaller change."
        )
    elif not stream.saw_finish:
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
        }) if stream.finish == "tool_calls" else None
        if event is not None:
            return event
        return _invalid_tools_error()
    return _norm(EV_ERROR, status=0, code=code,
                 message=message, retryable=retryable, diagnostic={'category': code})


def _refuse_unusable_end(stream: _ChatStream, parsed_tools: list[dict]) -> None:
    """Refuse a stream that never finished, ended oddly, or whose calls cannot run."""
    if not stream.saw_finish:
        for slot in stream.calls.values():
            record_rejected_tool(slot["id"], slot["name"], slot["args"],
                                 reason="The provider response ended before the tool call was complete.")
        raise _ProviderResponseError("provider_incomplete")
    if stream.finish not in ("stop", "tool_calls", "length"):
        raise _ProviderResponseError()
    if stream.finish == "tool_calls" and not parsed_tools:
        raise _ProviderResponseError()
    if parsed_tools and stream.finish != "tool_calls":
        raise _ProviderResponseError("provider_incomplete", retryable=stream.finish != "length")
    if len({tool["id"] for tool in parsed_tools}) != len(parsed_tools):
        for tool in parsed_tools:
            record_rejected_tool(tool["id"], tool["name"], tool["input"],
                                 reason="The response contains duplicate tool call IDs.",
                                 finish_reason=stream.finish)
        raise _ProviderResponseError()


def _estimated_prompt_tokens(body: dict) -> int:
    """A conservative, low-erring token estimate for the messages and tools of one request."""
    # Calibrated on a local Ollama (see AGENT_CHAT_PROMPT_CHARS_PER_TOKEN): erring low means an
    # estimate above the context really does not fit.
    size = len(json.dumps(body.get("messages") or [], ensure_ascii=False))
    size += len(json.dumps(body.get("tools") or [], ensure_ascii=False))
    return int(size / AGENT_CHAT_PROMPT_CHARS_PER_TOKEN)


def _ollama_context_problem(prompt_tokens: int, context: int | None) -> dict | None:
    """The refusal for a prompt longer than the loaded Ollama context, or None."""
    if context is None or prompt_tokens <= context:
        return None
    suggested = 1 << max(prompt_tokens * 5 // 4, 1).bit_length()
    params = {"needed": prompt_tokens, "context": context, "suggested": suggested}
    return _norm(EV_ERROR, status=0, code="ollama_context_too_small", retryable=False, message_params=params,
                 message=(f"Phoenix's prompt is about {prompt_tokens} tokens, but the Ollama server loaded this "
                          f"model with a context of {context} tokens, so it would silently drop the start of the "
                          f"prompt. Set OLLAMA_CONTEXT_LENGTH (or the model's num_ctx) to at least {suggested}."))
