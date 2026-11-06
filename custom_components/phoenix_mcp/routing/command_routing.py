"""Recognize local commands and project them to inert proposals; never authorizes or executes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from time import monotonic
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant

from ..access_key_store import GlobalSettings, AccessKeyRecord
from ..const import MAX_SEARCH_QUERY_LEN
from ..conversation_log import record_current
from ..helpers import log_internal_failure
from ..request_authority import AuthorityChangedError, RequestAuthority
from .chat_routing_history import ChatRoutingHistory
from .clarification_store import CommandClarifications
from .command_plan import _proposal, capture_choices
from .command_target import _target_fingerprint
from .intent_recognition import RecognitionUnavailable, SentenceTriggerMatch, async_recognize
from .numeric_recognition import recognize_numeric_ids
from .routing_tables import (
    COMMAND_ROUTING_LANGUAGES, COMMAND_ROUTING_DEFAULT_LANGUAGES, COMMAND_ROUTING_LANGUAGE_ALIASES,
    COMMAND_ROUTING_NUMERIC_OPERATIONS, COMMAND_ROUTING_NUMERIC_TARGET_LIMIT,
)
from .voice_context import voice_origin_current

if TYPE_CHECKING:
    from ..data import PhoenixData

_LOGGER = logging.getLogger(__name__)


@dataclass
class CommandRouting:
    """Own at most one recognition job for this runtime, including after timeout.

    Recognition may await an HA executor thread. A timeout/cancelled caller must
    not cancel that await and release capacity while the thread is still running.
    The done callback consumes late errors; the job holds no execution callback.
    On unload callers fail the runtime check and the outstanding read finishes
    naturally. No task or observation is reused by a replacement runtime.
    """

    task: asyncio.Task | None = None
    observations: int = 0
    candidates: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    total_elapsed_ms: float = 0
    chat_reasons: dict[str, int] = field(default_factory=dict)
    chat_candidates: int = 0
    chat_history: ChatRoutingHistory = field(default_factory=ChatRoutingHistory, repr=False)
    clarifications: CommandClarifications = field(default_factory=CommandClarifications, repr=False)

    def observe(self, result: dict, *, chat: bool = False) -> None:
        """Retain separate aggregate counts, without utterances or entity IDs."""
        if chat:
            reason = result["reason"]
            self.chat_reasons[reason] = self.chat_reasons.get(reason, 0) + 1
            self.chat_candidates += result["route"] == "local_candidate"
            return
        self.observations += 1
        self.candidates += result["route"] == "local_candidate"
        reason = result["reason"]
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        self.total_elapsed_ms += result["elapsed_ms"]

    def summary(self) -> dict:
        return {
            "observations": self.observations, "candidates": self.candidates,
            "reasons": dict(self.reasons),
            "mean_elapsed_ms": round(self.total_elapsed_ms / self.observations, 2)
            if self.observations else None,
            "agent_chat": {"observations": sum(self.chat_reasons.values()),
                           "candidates": self.chat_candidates, "reasons": dict(self.chat_reasons)},
        }

    def _finished(self, task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()
        if self.task is task:
            self.task = None


@dataclass(frozen=True)
class RoutingAuthority:
    """Snapshot admitted identity and configuration across recognition waits."""

    data: PhoenixData
    access_key_id: str
    access_key_hash: str
    version: int
    settings: GlobalSettings
    settings_values: dict

    @classmethod
    def capture(cls, data: PhoenixData, access_key: AccessKeyRecord) -> RoutingAuthority:
        return cls(data, access_key.id, access_key.access_key_hash, access_key.settings_version,
                   data.store.get_settings(), data.store.get_settings().to_dict())

    def current(self, hass: HomeAssistant) -> AccessKeyRecord | None:
        data = self.data
        if (data.store.get_settings() is not self.settings
                or self.settings.to_dict() != self.settings_values):
            return None
        try:
            access_key = RequestAuthority(hass, data, self.access_key_id, self.access_key_hash).current_access_key()
        except AuthorityChangedError:
            return None
        return access_key if access_key.settings_version == self.version else None


def effective_routing_mode(settings: GlobalSettings, ingress: str = "voice") -> str:
    """Resolve the simple switch without changing stored settings or provider consent."""
    mode = settings.agentcli_routing_mode if ingress == "chat" else settings.voice_agent_routing_mode
    return ("hybrid" if settings.decision_provider_id else "local") if mode == "auto" else mode


def routing_languages(settings: GlobalSettings, ingress: str = "voice") -> list[str]:
    """Auto follows supported ingress languages; diagnostic modes retain their allowlist."""
    mode = settings.agentcli_routing_mode if ingress == "chat" else settings.voice_agent_routing_mode
    return list(COMMAND_ROUTING_LANGUAGES) if mode == "auto" else settings.command_routing_languages


def routing_language(language: object, *, preview: bool = False,
                     enabled: list[str] | tuple[str, ...] = COMMAND_ROUTING_DEFAULT_LANGUAGES) -> str | None:
    """Admit only selected routing languages, without an unknown-to-English fallback."""
    if not isinstance(language, str):
        return None
    lowered = language.strip().replace("_", "-").lower()
    parts = lowered.split("-")
    if any(not part or not part.isascii() or not part.isalnum() for part in parts):
        return None
    known = {key.lower(): key for key in COMMAND_ROUTING_LANGUAGES}
    canonical = COMMAND_ROUTING_LANGUAGE_ALIASES.get(lowered) or known.get(lowered)
    if canonical is None and lowered.startswith("zh-hans-"):
        canonical = "zh-Hans"
    elif canonical is None and lowered.startswith("zh-hant-"):
        canonical = "zh-Hant"
    if canonical is None:
        canonical = known.get(lowered.split("-", 1)[0])
    if canonical is None:
        return None
    value = COMMAND_ROUTING_LANGUAGES[canonical]
    admitted = {COMMAND_ROUTING_LANGUAGES.get(key) for key in enabled}
    return value if preview or value in admitted else None


async def _recognize_command(hass: HomeAssistant, authority: RoutingAuthority,
                             sentence: str, language: str, voice_origin: dict | None = None) -> Any:
    """Keep both parsers in one bounded job, with HA trigger precedence intact."""
    context = {'voice_origin': voice_origin} if voice_origin is not None else {}
    result = await async_recognize(hass, sentence, language, strict=True, **context)
    if result is not None:
        return result
    access_key = authority.current(hass)
    if access_key is None:
        return None

    targets = []
    for state in hass.states.async_all():
        if state.entity_id not in sentence:
            continue
        if any(_target_fingerprint(hass, access_key, state.entity_id, intent) is not None
               for intent in COMMAND_ROUTING_NUMERIC_OPERATIONS):
            targets.append(state.entity_id)
        if len(targets) > COMMAND_ROUTING_NUMERIC_TARGET_LIMIT:
            return None
    if not targets:
        return None
    numeric = await hass.async_add_executor_job(recognize_numeric_ids, sentence, language, tuple(sorted(targets)))
    if numeric is None or authority.current(hass) is None:
        return None
    # Respect triggers and custom sentences added while the independent worker ran.
    latest = await async_recognize(hass, sentence, language, strict=True, **context)
    return latest if latest is not None else numeric


async def async_evaluate_command(
    hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord,
    sentence: object, language: object = "en", *, preview: bool = False, ingress: str = "voice",
    voice_origin: dict | None = None,
    clarify: bool = False,
) -> dict:
    """Return an inert candidate or fallback with elapsed local wall time.

    A candidate is not permission to act: execution, capability, approval and MESA checks stay with
    the dispatcher. No recognized slots, state values or out-of-scope names are returned.
    """
    start = monotonic()
    attempted = False

    def outcome(reason: str, proposal: dict | None = None) -> dict:
        record_current("routing", {"route": "assist", "attempted": attempted, "reason": reason,
                                   "matched": proposal is not None, "executed": False,
                                   "input": {"text": sentence.strip(), "language": recognition_language} if attempted and isinstance(sentence, str) else None,
                                   "observation_only": preview or effective_routing_mode(data.store.get_settings(), ingress) not in {"local", "hybrid"},
                                   "elapsed_ms": round((monotonic() - start) * 1000, 2)})
        return {"route": "local_candidate" if proposal else "provider",
                "reason": reason, "proposal": proposal, "executed": False,
                "elapsed_ms": round((monotonic() - start) * 1000, 2)}

    authority = RoutingAuthority.capture(data, access_key)
    if authority.current(hass) is None:
        return outcome("authority_changed")
    if (not isinstance(sentence, str) or not sentence.strip()
            or len(sentence) > MAX_SEARCH_QUERY_LEN):
        return outcome("invalid_input")
    recognition_language = routing_language(language, preview=preview, enabled=routing_languages(authority.settings, ingress))
    if recognition_language is None:
        return outcome("unsupported_language")
    runtime = data.command_routing
    if runtime.task is not None and not runtime.task.done():
        return outcome("busy")
    # Only recognition runs in the background. Projection and disclosure happen
    # synchronously after the authority check, never in a timed-out task.
    attempted = True
    task = asyncio.create_task(_recognize_command(hass, authority, sentence.strip(), recognition_language, voice_origin))
    runtime.task = task
    task.add_done_callback(runtime._finished)
    try:
        async with asyncio.timeout(data.store.get_settings().command_routing_timeout_ms / 1000):
            recognized = await asyncio.shield(task)
    except TimeoutError:
        return outcome("timeout" if authority.current(hass) else "authority_changed")
    except SentenceTriggerMatch:
        return outcome("sentence_trigger")
    except RecognitionUnavailable:
        return outcome("unavailable")
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_routing", exc)
        # Do not log recognizer exception text: it can contain the sentence.
        _LOGGER.debug("Local command recognition failed")
        return outcome("unavailable")
    current = authority.current(hass)
    if current is None:
        return outcome("authority_changed")
    if voice_origin is not None:
        if ingress != 'voice' or not voice_origin_current(hass, voice_origin):
            return outcome('authority_changed')
    try:
        reason, proposal = _proposal(recognized, current, hass)
    except Exception as exc:
        log_internal_failure(_LOGGER, "command_routing", exc)
        _LOGGER.debug("Local command projection failed")
        return outcome("unavailable")
    if proposal is not None and voice_origin is not None:
        proposal['voice_origin'] = dict(voice_origin)
    result = outcome(reason, proposal)
    if clarify and reason == 'no_target':
        choices = capture_choices(hass, data, current, recognized, ingress=ingress, voice_origin=voice_origin)
        if choices is not None:
            result['_clarification'] = {'sentence': sentence.strip(), 'choices': choices}
    return result
