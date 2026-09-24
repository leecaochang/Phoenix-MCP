"""Experimental, local-only command observations; never authorizes or executes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .chat_routing_history import ChatRoutingHistory
from .command_operations import command_domain, numeric_operation, supports_command, valid_target_value
from .const import (
    COMMAND_ROUTING_INTENTS, COMMAND_ROUTING_TARGET_SLOTS, COMMAND_ROUTING_LANGUAGES,
    COMMAND_ROUTING_DEFAULT_LANGUAGES, COMMAND_ROUTING_LANGUAGE_ALIASES,
    MAX_SEARCH_QUERY_LEN, COMMAND_ROUTING_NUMERIC_OPERATIONS, COMMAND_ROUTING_NUMERIC_TARGET_LIMIT,
)
from .numeric_recognition import NumericIdMatch, recognize_numeric_ids
from .intent_recognition import RecognitionUnavailable, SentenceTriggerMatch, async_recognize
from .policy_engine import Permission, resolve, resolve_intent_entities
from .token_store import GlobalSettings, TokenRecord
from .request_authority import AuthorityChangedError, RequestAuthority

if TYPE_CHECKING:
    from .data import PhoenixData

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
    token_id: str
    token_hash: str
    version: int
    settings: GlobalSettings
    settings_values: dict

    @classmethod
    def capture(cls, data: PhoenixData, token: TokenRecord) -> RoutingAuthority:
        return cls(data, token.id, token.token_hash, token.settings_version,
                   data.store.get_settings(), data.store.get_settings().to_dict())

    def current(self, hass: HomeAssistant) -> TokenRecord | None:
        data = self.data
        if (data.store.get_settings() is not self.settings
                or self.settings.to_dict() != self.settings_values):
            return None
        try:
            token = RequestAuthority(hass, data, self.token_id, self.token_hash).current_token()
        except AuthorityChangedError:
            return None
        return token if token.settings_version == self.version else None



def _proposal(result: Any, token: TokenRecord, hass: HomeAssistant) -> tuple[str, dict | None]:
    """Map only supported, complete HA slots against current Phoenix scope."""
    if isinstance(result, NumericIdMatch):
        from .command_execution import _target_fingerprint

        operation = numeric_operation(result.intent)
        if (operation is None or not operation.valid_value(result.value)
                or not valid_target_value(hass.states.get(result.entity_id), result.intent, result.value)
                or _target_fingerprint(hass, token, result.entity_id, result.intent) is None):
            return "no_target", None
        return "supported", {"intent": result.intent, "entity_ids": [result.entity_id],
                             operation.proposal_field: result.value}
    if result is None or getattr(result, "unmatched_entities", None):
        return "no_match", None
    metadata = getattr(result, "intent_metadata", None) or {}
    if metadata.get("hass_custom_sentence"):
        return "unsupported", None
    intent_name = result.intent.name
    if intent_name not in COMMAND_ROUTING_INTENTS:
        return "unsupported", None
    slots = {name: entity.value for name, entity in result.entities.items()}
    if intent_name == "HassLightSet" and "temperature" in slots and "brightness" not in slots:
        intent_name = "PhoenixLightTemperature"
    operation = numeric_operation(intent_name)
    allowed = COMMAND_ROUTING_TARGET_SLOTS | ({operation.slot} if operation else set())
    # State comparisons, counts, relative brightness, floor/global commands,
    # custom slots and contextual/deferred actions belong to the normal provider.
    if slots.keys() - allowed:
        return "unsupported", None
    name, area = slots.get("name"), slots.get("area")
    if not (name or area) or name == "all":
        return "unsupported", None
    if any(value is not None and (not isinstance(value, str) or not value.strip())
           for value in (name, area)):
        return "unsupported", None
    domains = slots.get("domain")
    device_class = slots.get("device_class")
    if any(value is not None and not isinstance(value, str) for value in (domains, device_class)):
        return "unsupported", None
    read_only = intent_name == "HassGetState"
    expected_domain = command_domain(intent_name)
    if operation and domains and domains != expected_domain:
        return "unsupported", None
    if read_only and (not name or getattr(result, "response", None) not in (None, "default", "one")):
        return "unsupported", None
    # Match the HA intent's domains, not only the domains we can execute.
    # Paired remotes cannot set volume; valves still make position ambiguous.
    target_domains = [domains] if domains else list(operation.match_domains) if operation else None
    ids = resolve_intent_entities(
        hass, token, name=name, area=area,
        domains=target_domains,
        device_classes=[device_class] if device_class else None, read_only=True,
    )
    if not ids or (name and len(ids) != 1) or (read_only and len(ids) != 1):
        return "no_target", None
    if intent_name == "HassSetPosition" and all(eid.startswith("valve.") for eid in ids):
        intent_name = "PhoenixValvePosition"
        operation = numeric_operation(intent_name)
        expected_domain = "valve"
    states = [hass.states.get(entity_id) for entity_id in ids]
    if any(state is None or state.state in ("unknown", "unavailable") for state in states):
        return "no_target", None
    if not read_only and any(
        not entity_id.startswith(f"{expected_domain}.") or resolve(entity_id, token, hass) != Permission.WRITE
        for entity_id in ids
    ):
        return "no_target", None
    if not read_only and not name and not operation and domains != expected_domain:
        return "unsupported", None
    proposal: dict[str, Any] = {"intent": intent_name, "entity_ids": sorted(ids)}
    if operation:
        value = slots.get(operation.slot)
        if not operation.valid_value(value):
            return "unsupported", None
        if any(state is None or not supports_command(state, intent_name) or not valid_target_value(state, intent_name, value) for state in states):
            return "unsupported", None
        proposal[operation.proposal_field] = value
    return "supported", proposal


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
                             sentence: str, language: str) -> Any:
    """Keep both parsers in one bounded job, with HA trigger precedence intact."""
    result = await async_recognize(hass, sentence, language, strict=True)
    if result is not None:
        return result
    token = authority.current(hass)
    if token is None:
        return None
    from .command_execution import _target_fingerprint

    targets = []
    for state in hass.states.async_all():
        if state.entity_id not in sentence:
            continue
        if any(_target_fingerprint(hass, token, state.entity_id, intent) is not None
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
    latest = await async_recognize(hass, sentence, language, strict=True)
    return latest if latest is not None else numeric


async def async_evaluate_command(
    hass: HomeAssistant, data: PhoenixData, token: TokenRecord,
    sentence: object, language: object = "en", *, preview: bool = False, ingress: str = "voice",
) -> dict:
    """Return an inert candidate or fallback with elapsed local wall time.

    A candidate is not permission to act: execution, capability, approval and
    MESA checks remain exclusively with the existing dispatcher.
    No recognized slots, state values, or out-of-scope names are returned.
    """
    start = monotonic()

    def outcome(reason: str, proposal: dict | None = None) -> dict:
        return {"route": "local_candidate" if proposal else "provider",
                "reason": reason, "proposal": proposal, "executed": False,
                "elapsed_ms": round((monotonic() - start) * 1000, 2)}

    authority = RoutingAuthority.capture(data, token)
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
    task = asyncio.create_task(_recognize_command(hass, authority, sentence.strip(), recognition_language))
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
    except Exception:
        # Do not log recognizer exception text: it can contain the sentence.
        _LOGGER.debug("Local command recognition failed")
        return outcome("unavailable")
    current = authority.current(hass)
    if current is None:
        return outcome("authority_changed")
    try:
        reason, proposal = _proposal(recognized, current, hass)
    except Exception:
        _LOGGER.debug("Local command projection failed")
        return outcome("unavailable")
    return outcome(reason, proposal)
