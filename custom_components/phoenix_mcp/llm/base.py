"""Shared model provider primitives: events, errors, retries, streams and tool schemas."""

from __future__ import annotations

import asyncio
import codecs
from dataclasses import dataclass
from datetime import datetime
import functools
import json
import logging
import re
from typing import Any, AsyncIterator, Awaitable, Callable

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import parse_datetime, utcnow

from ..const import MAX_TOOL_NAME_LENGTH
from ..conversation_log import record_event, record_rejected_tool, sanitized
from ..data import PhoenixData
from .accounts import PROVIDER_DEFINITIONS
from ..provider_constants import (
    AGENT_CHAT_CAPABILITY_CONCURRENCY, AGENT_CHAT_DEFAULT_EFFORT, AGENT_CHAT_DEFAULT_MAX_TOKENS,
    AGENT_CHAT_EFFORT_LEVEL_ORDER, AGENT_CHAT_EFFORT_LEVELS, AGENT_CHAT_LEARNABLE_OPTIONS,
    AGENT_CHAT_LOAD_TOOLS_NAME, AGENT_CHAT_MAX_CATALOG_MODELS, AGENT_CHAT_MAX_JSON_BYTES,
    AGENT_CHAT_MAX_MODEL_ID_LENGTH, AGENT_CHAT_MAX_SSE_BUFFER_BYTES, AGENT_CHAT_MAX_STREAM_BYTES,
    AGENT_CHAT_PROVIDER_DETAIL_CHARS, AGENT_CHAT_PROVIDER_ERROR_BYTES,
    AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS,
)
from ..tool_loading import ToolLoader


_LOGGER = logging.getLogger(__name__)

# Fast-failing timeout for the setup/validation probes so an unreachable host
# does not hang the panel for the OS default (~20s).
_PROBE_TIMEOUT = ClientTimeout(total=10, connect=5, sock_connect=5)
# The image check waits for a real answer, which a reasoning model can take a while to give.
_IMAGE_PROBE_TIMEOUT = ClientTimeout(total=90, connect=5, sock_connect=5)


# Providers that require (or reward) the assistant's reasoning_content being sent
# back verbatim with its tool calls on the next round. Kimi's thinking models and
# Qwen's preserve_thinking models take it the same way; others use the field for
# display only.
_REPLAY_REASONING_KINDS = frozenset({"deepseek", "zai", "mimo", "tencent", "kimi", "qwen", "fireworks", "opencode"})
# Upper bound on catalogue pages followed, so a misbehaving provider cannot loop forever.
_MODEL_LIST_MAX_PAGES = 10


async def _read_json(resp: Any) -> Any:
    """Parse one non-streaming provider reply under the byte cap.

    An oversized or malformed body raises ValueError, which callers treat as no answer. The content
    type is not checked because some Ollama builds omit it.
    """
    # A provider is untrusted input, and aiohttp's own reader buffers whatever it is sent.
    raw = bytearray()
    async for chunk in resp.content.iter_chunked(65536):
        raw.extend(chunk)
        if len(raw) > AGENT_CHAT_MAX_JSON_BYTES:
            raise ValueError("Provider reply exceeds the size limit")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except RecursionError:
        raise ValueError("Provider reply is nested too deeply") from None


def _bounded_catalog(list_models: Any) -> Any:
    """Keep a provider's listing within the stored limits, whatever shape it arrives in.

    A listing whose rows are not the documented objects counts as no listing, so
    the provider's own fallback applies rather than an error reaching the admin API.
    """
    @functools.wraps(list_models)
    async def bounded(self: Any, session: ClientSession) -> list[str]:
        try:
            models = await list_models(self, session)
        except (AttributeError, KeyError, TypeError):
            models = self._model_fallback()
        usable = (m for m in models if isinstance(m, str) and len(m) <= AGENT_CHAT_MAX_MODEL_ID_LENGTH)
        return list(dict.fromkeys(usable))[:AGENT_CHAT_MAX_CATALOG_MODELS]
    return bounded


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
    sock_read=AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS,
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
AGENT_CHAT_HEARTBEAT_SECONDS = 15.0

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
# A temporary failure before any output is about to be retried: attempt is the
# next attempt's number and detail the provider's own sentence, when it sent one.
EV_RETRYING = "retrying"


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


def _detail_text(value: Any) -> str:
    """A validation detail as one sentence: a string, or a list of {msg, loc} items.

    Mistral and Fireworks answer HTTP 422 with FastAPI-style `detail` lists, which
    Mistral nests inside `message`.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _detail_text(value.get("detail"))
    if isinstance(value, list):
        parts = []
        for item in value[:5]:
            if isinstance(item, dict) and isinstance(item.get("msg"), str):
                loc = item.get("loc")
                field = ".".join(str(p) for p in loc if p != "body") if isinstance(loc, list) else ""
                parts.append(f"{field}: {item['msg']}" if field else item["msg"])
        return "; ".join(parts)
    return ""


def _envelope_message(obj: dict | None) -> str:
    """The provider's human sentence from an error envelope, unclipped, or ""."""
    if obj is None:
        return ""
    err = obj.get("error")
    for message in ((err.get("message") if isinstance(err, dict) else err), obj.get("message"), obj.get("detail")):
        text = _detail_text(message)
        if text:
            return text
    return ""


def _provider_error_codes(obj: dict | None) -> list[str]:
    """Every machine code an error envelope carries, lowercased, primary first.

    The primary is error.code, else error.type. The rest are Anthropic's details.error_code,
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


def _provider_error_detail(text: str, api_key: str | None = None) -> str:
    """The provider's own error sentence for an administrator, or "".

    Only a JSON error envelope's message qualifies. It reaches the admin panel, the conversation log
    and the Home Assistant log, never a model, Voice or AI Task.
    """
    # Key formats differ by provider, so the configured key is removed verbatim first. Clipping and
    # scrubbing as conversation-log text then redacts a key, bearer token or address a provider
    # echoes.
    message = _envelope_message(_error_envelope(text))
    if not message:
        return ""
    if api_key:
        message = message.replace(api_key, "<redacted>")
    clipped = " ".join(message.split())[:AGENT_CHAT_PROVIDER_DETAIL_CHARS]
    try:
        return str(sanitized(clipped, max_bytes=8 * AGENT_CHAT_PROVIDER_DETAIL_CHARS, hide_ip=True))
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
# Quota windows and plan limits a provider sends as HTTP 429 that no short retry
# clears (Z.ai window, weekly/monthly, fair-use and spend-cap limits; TokenHub's
# daily token cap). The provider's own sentence, which names the reset, is kept.
_PROVIDER_NON_RETRYABLE: dict[str, frozenset[str]] = {
    "zai": frozenset({"1308", "1310", "1313", "1316", "1317", "1318", "1319", "1320", "1321"}),
    "tencent": frozenset({"429004"}),
}
# Refusals of this request that the status alone misnames: Z.ai's model outside
# the plan, and OpenCode's free models, which answer only OpenCode's own app.
_PROVIDER_REJECTED: dict[str, frozenset[str]] = {
    "zai": frozenset({"1311"}),
    "opencode": frozenset({"freetiererror"}),
}
_RETRY_DELAY = re.compile(r"(\d{1,6})(?:\.\d+)?s")


def _google_quota(envelope: dict | None) -> tuple[int | None, bool]:
    """Google's RetryInfo delay and whether a QuotaFailure names a daily quota.

    Gemini reports which quota ran out in `error.details`; a per-day quota is not
    cleared by any short retry.
    """
    err = envelope.get("error") if isinstance(envelope, dict) else None
    details = err.get("details") if isinstance(err, dict) else None
    delay, daily = None, False
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        match = _RETRY_DELAY.fullmatch(str(item.get("retryDelay") or ""))
        if match:
            delay = int(match.group(1))
        for violation in item.get("violations") or [] if isinstance(item.get("violations"), list) else []:
            if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId") or ""):
                daily = True
    return delay, daily


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


def _http_failure(
    kind: str, status: int, text: str, *, code: str, retryable: bool, api_key: str | None = None, **extra: Any,
) -> dict:
    """One EV_ERROR for a non-200 provider answer, with the administrator-only detail.

    Billing refusals override the status classification. An identifier-shaped primary code stays as
    provider_code, so the log shows the classification without provider prose.
    """
    envelope = _error_envelope(text)
    codes = _provider_error_codes(envelope)
    delay, daily = _google_quota(envelope) if kind == "gemini" else (None, False)
    if _billing_refusal(kind, status, codes, _envelope_message(envelope)):
        code, retryable = "quota", False
    elif kind == "openrouter" and _OPENROUTER_IN_FLIGHT in codes:
        code, retryable = "rate_limit", True
    elif _PROVIDER_REJECTED.get(kind, frozenset()) & set(codes):
        code, retryable = "provider_rejected", False
    elif (codes and codes[0] in _NON_RETRYABLE_ERROR_CODES) or daily or (
            _PROVIDER_NON_RETRYABLE.get(kind, frozenset()) & set(codes)):
        retryable = False
    if delay is not None and status in (429, 503):
        given = extra.get("rate_limit")
        limits: dict = given if isinstance(given, dict) else {}
        extra["rate_limit"] = {**limits, "retry_after_seconds": limits.get("retry_after_seconds", delay)}
    detail = _provider_error_detail(text, api_key)
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


# Statuses that name a temporary condition on every provider: a request timeout,
# a rate limit, server and gateway failures, and Anthropic's overload.
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})
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
_CLAUDE_NO_ADAPTIVE: set[tuple[str, str]] = set()


async def _retry_transient(attempt: Callable[[], Awaitable[Any]]) -> Any:
    """Await attempt() with a bounded retry for a transient connection failure.

    Whatever attempt() returns passes straight back, so a status is never retried; the last
    ClientError or TimeoutError is re-raised.
    """
    # The validate() probes use this so a one-off DNS or connect blip does not reject a good
    # credential during the Settings Validate step.
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


async def _error_body(resp: Any) -> str:
    """The start of a provider's error body; a huge or endless body is never buffered."""
    raw = bytearray()
    async for chunk in resp.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) >= AGENT_CHAT_PROVIDER_ERROR_BYTES:
            break
    return bytes(raw[:AGENT_CHAT_PROVIDER_ERROR_BYTES]).decode("utf-8", errors="replace")


async def _sse_lines(resp: Any) -> AsyncIterator[str]:
    """Yield decoded lines from a streaming HTTP response, buffering split frames.

    Exceeding the between-newline or the whole-response byte cap raises ClientError, which each
    provider's stream_turn turns into a clean chat error.
    """
    # Both caps guard memory against a faulty or hostile provider. One stops a stream that never
    # sends a newline; the other stops unlimited small frames that parsers append into text blocks
    # and tool JSON. A real response is well under both, and a turn is bounded at cap x
    # AGENT_CHAT_MAX_ITERATIONS.
    buffer = ""
    total = 0
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    async for chunk in resp.content.iter_chunked(2048):
        total += len(chunk)
        if total > AGENT_CHAT_MAX_STREAM_BYTES:
            raise ClientError("Provider stream exceeded the maximum total size.")
        buffer += decoder.decode(chunk)
        if len(buffer) > AGENT_CHAT_MAX_SSE_BUFFER_BYTES:
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
        "provider_context_limit": "The conversation is too long for this model's context window. No tool from this response was run.",
        "bad_tool_call": "The model produced an invalid tool call. No tool from this response was run.",
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


# HTTP classes that name a temporary condition when a provider reports an error
# inside an already-started stream. TokenHub uses six-digit codes whose first
# three digits are the status (429006, 502001); OpenRouter sends the bare status.
_TRANSIENT_STREAM_STATUSES = frozenset({"408", "429", "500", "502", "503", "504", "529"})
_TRANSIENT_STREAM_CODES = frozenset({
    "overloaded_error", "rate_limit_error", "server_error", "internal_server_error", "rate_limit_exceeded",
    # OpenRouter's typed categories (error.metadata.error_type).
    "provider_unavailable", "provider_overloaded", "timeout", "server", "rate_limit",
})


def _transient_stream_error(error: dict) -> bool:
    """Whether an error object inside a 200 stream names a temporary condition."""
    code = str(error.get("code") or error.get("type") or "").strip().lower()
    given = error.get("metadata")
    metadata: dict = given if isinstance(given, dict) else {}
    return (code in _TRANSIENT_STREAM_CODES
            or (re.fullmatch(r"\d{3}(?:\d{3})?", code) is not None and code[:3] in _TRANSIENT_STREAM_STATUSES)
            or str(metadata.get("error_type") or "").strip().lower() in _TRANSIENT_STREAM_CODES)


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
    if isinstance(error, dict) and _transient_stream_error(error):
        raise _ProviderResponseError("provider_transient")
    if isinstance(error, dict) and error.get("code") == "tool_use_failed":
        # Groq validates a generated call itself and streams this in place of it
        # (an undeclared tool, unparseable arguments). The model erred, not a
        # policy: nothing ran, and a fresh sample usually succeeds.
        raise _ProviderResponseError("bad_tool_call")
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


def _tool_call_problem(call_id: Any, name: Any) -> str:
    """Explain the same structural checks without echoing model content."""
    if not isinstance(call_id, str) or not 0 < len(call_id) <= 256:
        return "The tool call ID is missing or invalid."
    if (not isinstance(name, str) or not 0 < len(name) <= MAX_TOOL_NAME_LENGTH
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", name) is None):
        return "The tool name is missing or invalid."
    return "Tool arguments must be a JSON object."


def _json_error_hint(raw: str, error: json.JSONDecodeError) -> str:
    """Tell the model where its tool arguments stopped parsing and what is likely missing.

    The innermost object or array open at the error names the awaited closer and its key. No string
    value is quoted: this text reaches the transcript, and a value can hold a secret.
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

    `calls` holds (id, name, parsed arguments or None, model-facing error or None) in response
    order. Every call gets a tool error, since none runs beside a malformed sibling.
    """
    # A call whose ID or name is unusable cannot carry a tool result, so that response stays a
    # terminal error rather than a correction.
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

    A complete object replaces a complete object, except that `{}` never replaces real arguments;
    anything else is appended, so genuine deltas still concatenate.
    """
    # Gemini's OpenAI-compatible stream resends a call's arguments as complete JSON, `{}` and then
    # the full object, which plain concatenation would turn into `{}{...}`.
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

    Past a documented tools-array cap the provider answers 400 to every request, and trimming the
    catalog would hide capabilities without saying so.
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


def _tool_loader(provider: Any, catalog: list[dict]) -> ToolLoader | None:
    """Tool-level loading when this provider's tools-array cap is below the catalog."""
    definition = PROVIDER_DEFINITIONS.get(str(getattr(provider, "kind", "")))
    return ToolLoader.plan(catalog, definition.max_tools if definition is not None else None)


def _local_tool_results(
    loader: ToolLoader | None, tool_calls: list[dict], data: PhoenixData,
) -> tuple[list[dict | None], list[dict]]:
    """Answer load_tools and calls to undeclared deferred tools without dispatching them.

    Returns one slot per call, holding its local result or None, and the calls left for review and
    dispatch. Neither kind can start a write.
    """
    if loader is None:
        return [None] * len(tool_calls), tool_calls
    slots: list[dict | None] = []
    dispatch: list[dict] = []
    for tc in tool_calls:
        name = str(tc.get("name") or "")[:MAX_TOOL_NAME_LENGTH]
        args = tc.get("input") or {}
        if name == AGENT_CHAT_LOAD_TOOLS_NAME:
            text, is_error = loader.load(args)
            record_event(data, "tool_call", {"name": name, "arguments": args})
            record_event(data, "tool_result", {"result": {"content": [{"type": "text", "text": text}], "isError": is_error}},
                         outcome="invalid_request" if is_error else "allowed")
        elif (refusal := loader.blocked(name)) is not None:
            record_rejected_tool(tc.get("id"), name, args, reason=refusal, stage="tool_loading")
            text, is_error = refusal, True
        else:
            loader.use(name)
            slots.append(None)
            dispatch.append(tc)
            continue
        slots.append({"tool_use_id": tc.get("id"), "tool_name": name, "result_text": text, "is_error": is_error})
    return slots, dispatch


def _merged_results(slots: list[dict | None], dispatched: list[dict]) -> list[dict]:
    """Every call's result in call order, local ones in their own places."""
    remaining = iter(dispatched)
    return [slot if slot is not None else next(remaining) for slot in slots]


def _silent_stream_error() -> dict:
    """The provider connected but sent nothing within the read bound: stalled, not unreachable."""
    seconds = AGENT_CHAT_STREAM_READ_TIMEOUT_SECONDS
    return _norm(EV_ERROR, status=0, code="provider_silent", retryable=True,
                 message=f"The provider sent nothing for {seconds}s, so the request was stopped.",
                 message_params={"seconds": seconds})


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

    Claude takes adaptive thinking with a display mode plus an output_config effort. MiniMax takes
    `{type: adaptive}`, and `output_config.effort` only on MiniMax-M3.1-Flash.
    """
    body["max_tokens"] = int(options.get("max_tokens") or AGENT_CHAT_DEFAULT_MAX_TOKENS)
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
        effort = options.get("effort", AGENT_CHAT_DEFAULT_EFFORT)
        if effort in AGENT_CHAT_EFFORT_LEVELS:
            body["output_config"] = {"effort": effort}
    elif (kind == "minimax" and thinking and options.get("effort") in AGENT_CHAT_EFFORT_LEVELS
          and re.match(r"minimax-m3\.1", str(body.get("model") or ""), re.I)):
        # Only MiniMax-M3.1-Flash takes an effort (default max); the panel offers
        # the control for that model alone, and Voice sends none.
        body["output_config"] = {"effort": options["effort"]}


# The output cap every Anthropic-format model has accepted; the fallback for a
# model that refuses AGENT_CHAT_DEFAULT_MAX_TOKENS.
_LEGACY_MAX_TOKENS = 8192


def _use_smaller_output(body: dict) -> None:
    """Retry at the legacy output cap, keeping a fixed thinking budget below it."""
    body["max_tokens"] = _LEGACY_MAX_TOKENS
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = min(_LEGACY_MAX_TOKENS // 2, 16000)
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}


def _thinking_type(body: dict) -> str:
    """The `type` of a request body's thinking object, or "" when there is none."""
    thinking = body.get("thinking")
    return str(thinking.get("type") or "") if isinstance(thinking, dict) else ""


def _use_enabled_thinking(body: dict) -> None:
    """Switch a request from adaptive thinking to a fixed enabled budget, dropping effort.

    For models without adaptive thinking or effort. The budget stays below max_tokens and at least
    1024; a request too small for one runs without thinking rather than failing.
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


_CACHE_MARKER = {"cache_control": {"type": "ephemeral"}}


def _cache_marked_message(message: dict) -> dict | None:
    """Copy one message with a cache breakpoint on its last markable block, or None.

    Anthropic refuses a breakpoint on a thinking block or an empty text block.
    """
    content = message.get("content")
    if isinstance(content, str):
        return {**message, "content": [{"type": "text", "text": content, **_CACHE_MARKER}]} if content else None
    if not isinstance(content, list):
        return None
    for i in range(len(content) - 1, -1, -1):
        block = content[i]
        if (isinstance(block, dict) and block.get("type") not in ("thinking", "redacted_thinking")
                and not (block.get("type") == "text" and not block.get("text"))):
            return {**message, "content": [*content[:i], {**block, **_CACHE_MARKER}, *content[i + 1:]]}
    return None


def _cache_marked(system_prompt: str, messages: list[dict]) -> tuple[Any, list[dict]]:
    """Add Anthropic prompt-cache breakpoints on copies (Claude only, never MiniMax).

    Markers close the system block, the message before the operator's latest, and the last block.
    The stored transcript round-trips through the browser and must stay clean.
    """
    # The system marker caches the tools array plus the system prompt, which change only with the
    # access key's caps. The middle marker ends the history the next turn resends: the latest
    # operator message carries the turn's time note (_with_time_context), so no later prefix recurs.
    # The last marker lets each loop iteration reuse prior history instead of re-billing it.
    system = [{"type": "text", "text": system_prompt, **_CACHE_MARKER}]
    if not messages:
        return system, messages
    marked = list(messages)
    operator = _operator_message_index(messages)
    if operator:
        if (history := _cache_marked_message(messages[operator - 1])) is not None:
            marked[operator - 1] = history
    if (last := _cache_marked_message(messages[-1])) is not None:
        marked[-1] = last
    return system, marked


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

    allOf members merge into the object; each anyOf or oneOf becomes a sentence for the tool
    description. Nested combinators stay.
    """
    # Anthropic, and OpenAI even without strict mode, answer 400 to a tool schema with top-level
    # anyOf, oneOf or allOf, so one such tool fails every request; gateways forward the schema
    # unchanged. Each tool's handler enforces these rules at dispatch, so no check is relaxed.
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

    Type lists are left alone, since Kimi refuses any keyword beside one, and so is the closed root.
    Unchanged schemas return as the same object.
    """
    # JSON Schema leaves an object without additionalProperties open, but xAI's strict rules close
    # it and Kimi's default strict dialect may, leaving a model able to send only `{}` for service
    # data or an automation config.
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


# How providers word "this model cannot take image input": an unsupported
# statement next to image/vision, or a schema that has no image content type.
_IMAGE_REFUSAL = re.compile(
    r"(?:image|vision|multi-?modal)[^.]*(?:not supported|unsupported|only supported by|not enabled|not available)"
    r"|(?:does not|doesn't|do not|cannot|can't) (?:support|accept|process|handle)[^.]*(?:image|vision|multi-?modal)"
    r"|unknown variant `?image_url"
)


# A refusal of the request as sent: 400, or 422 where a provider validates the
# body separately (NVIDIA, Mistral, DeepSeek, xAI, Fireworks, Cerebras).
_REFUSAL_STATUSES = frozenset({400, 422})


def _refused_option(status: int, message: str, sent: dict) -> str | None:
    """The request key a provider just refused, when it named one of ours.

    It needs a 400 or 422, a key whose refusal has one unambiguous consequence, and one Phoenix
    actually sent in this request.
    """
    # A wrong answer here disables a control the operator was using. The last guard stops an
    # unrelated error mentioning "temperature" from teaching Phoenix that temperature is
    # unsupported.
    if status not in _REFUSAL_STATUSES or not message:
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
    for key in AGENT_CHAT_LEARNABLE_OPTIONS:
        if key == "tools":
            continue
        if key in sent and re.search(rf"\b{re.escape(key)}\b", low):
            return key
    return None


def _openrouter_capabilities(data: list[dict]) -> dict[str, dict]:
    """Per-model declared capabilities from OpenRouter's `supported_parameters`.

    A key appears only for what the provider declares; a missing key means "not declared" and must
    never be read as False.
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
        row: dict[str, Any] = {}
        if isinstance(params, list):
            row.update({
                "tools": "tools" in params,
                "thinking": "reasoning" in params,
                "temperature": "temperature" in params,
            })
        # The catalog lists each model's reasoning levels; a mandatory reasoner
        # cannot take "none". This settles what the probe could only guess.
        reasoning = m.get("reasoning")
        efforts = reasoning.get("supported_efforts") if isinstance(reasoning, dict) else None
        if isinstance(reasoning, dict) and isinstance(efforts, list):
            levels = [level for level in AGENT_CHAT_EFFORT_LEVEL_ORDER if level in efforts
                      and not (level == "none" and reasoning.get("mandatory") is True)]
            if levels:
                row["effort_levels"] = levels
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
    """Per-model declared capabilities from Ollama's /api/show, one lookup per model.

    `temperature` is not emitted: Ollama takes it for every model and does not list it as a
    capability, so False would invent a limit.
    """
    # A local library is whatever the operator pulled, so a model that cannot call tools is ordinary
    # here, and `/api/tags` does not say. The per-model cost is why this runs on an explicit refresh
    # and not on every card open.
    sem = asyncio.Semaphore(AGENT_CHAT_CAPABILITY_CONCURRENCY)

    async def _one(name: str) -> tuple[str, dict] | None:
        async with sem:
            try:
                async with session.post(
                    f"{base_url}/api/show", headers=headers, json={"model": name},
                    timeout=_PROBE_TIMEOUT, allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        return None
                    body = await _read_json(resp)
            except (ClientError, asyncio.TimeoutError, ValueError):
                return None
        caps = body.get("capabilities") if isinstance(body, dict) else None
        if not isinstance(caps, list):
            return None
        row: dict[str, Any] = {
            "tools": "tools" in caps,
            "thinking": "thinking" in caps,
            "vision": "vision" in caps,
        }
        # A model with reasoning levels lists them (false means it can be turned
        # off, which the panel's own Off covers). Ollama answers an unknown level
        # with the default rather than a refusal, so a probe cannot learn them.
        thinking = body.get("thinking")
        values = thinking.get("values") if isinstance(thinking, dict) else None
        if isinstance(values, list):
            levels = [level for level in AGENT_CHAT_EFFORT_LEVEL_ORDER if level != "none" and level in values]
            if levels:
                row["effort_levels"] = levels
        return name, row

    pairs = await asyncio.gather(*(_one(m) for m in models))
    return dict(p for p in pairs if p is not None)


# Documented finish reasons that end a response without a usable answer. A
# context overflow resent unchanged overflows again; an upstream fault may pass.
_FINISH_FAILURES: dict[str, tuple[str, bool]] = {
    "sensitive": ("provider_blocked", False),
    "model_context_window_exceeded": ("provider_context_limit", False),
    "model_length": ("provider_context_limit", False),
    "insufficient_system_resource": ("provider_transient", True),
    "network_error": ("provider_transient", True),
    "aborted": ("provider_transient", True),
    "error": ("provider_transient", True),
}


def _norm_stop(raw: str) -> str:
    """Normalize provider stop reasons to end_turn / tool_use / max_tokens / error."""
    return {
        "end_turn": "end_turn", "tool_use": "tool_use", "max_tokens": "max_tokens",
        "stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens",
    }.get(raw, "end_turn")


_MODEL_IMAGE_FALLBACK = (
    "The image is displayed to the operator, but this provider/model is not known "
    "to support visual input. Do not infer or describe image contents."
)


def _model_result_text(result: dict, vision: bool) -> str:
    text = str(result.get("result_text") or "")
    if result.get("images") and not vision:
        return f"{text}\n{_MODEL_IMAGE_FALLBACK}".strip()
    return text


def _operator_message_index(messages: list[dict]) -> int | None:
    """Index of the latest message the operator wrote, skipping tool-result messages."""
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return i
        if isinstance(content, list) and not any(
                isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return i
    return None
