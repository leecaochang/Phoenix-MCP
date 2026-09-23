"""Experimental, local-only command observations; never authorizes or executes."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, TYPE_CHECKING

from homeassistant.components.light import ATTR_SUPPORTED_COLOR_MODES, brightness_supported
from homeassistant.core import HomeAssistant

from .const import (
    COMMAND_ROUTING_INTENTS, COMMAND_ROUTING_TARGET_SLOTS, COMMAND_ROUTING_PREVIEW_LANGUAGES,
    MAX_SEARCH_QUERY_LEN,
)
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
    if result is None or getattr(result, "unmatched_entities", None):
        return "no_match", None
    metadata = getattr(result, "intent_metadata", None) or {}
    if metadata.get("hass_custom_sentence"):
        return "unsupported", None
    intent_name = result.intent.name
    if intent_name not in COMMAND_ROUTING_INTENTS:
        return "unsupported", None
    slots = {name: entity.value for name, entity in result.entities.items()}
    allowed = COMMAND_ROUTING_TARGET_SLOTS | ({"brightness"} if intent_name == "HassLightSet" else set())
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
    if read_only and (not name or getattr(result, "response", None) not in (None, "default", "one")):
        return "unsupported", None
    ids = resolve_intent_entities(
        hass, token, name=name, area=area,
        domains=[domains] if domains else None,
        device_classes=[device_class] if device_class else None, read_only=True,
    )
    if not ids or (name and len(ids) != 1) or (read_only and len(ids) != 1):
        return "no_target", None
    states = [hass.states.get(entity_id) for entity_id in ids]
    if any(state is None or state.state in ("unknown", "unavailable") for state in states):
        return "no_target", None
    if not read_only and any(
        not entity_id.startswith("light.") or resolve(entity_id, token, hass) != Permission.WRITE
        for entity_id in ids
    ):
        return "no_target", None
    if not read_only and not name and domains != "light":
        return "unsupported", None
    proposal: dict[str, Any] = {"intent": intent_name, "entity_ids": sorted(ids)}
    if intent_name == "HassLightSet":
        value = slots.get("brightness")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 100):
            return "unsupported", None
        for state in states:
            modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) if state else None
            if (not isinstance(modes, (list, tuple, set, frozenset))
                    or not all(isinstance(mode, str) for mode in modes)
                    or not brightness_supported(modes)):
                return "unsupported", None
        proposal["brightness_pct"] = value
    return "supported", proposal


def routing_language(language: object, *, preview: bool = False) -> str | None:
    """Keep multilingual admission confined to explicit administrator previews."""
    if not isinstance(language, str):
        return None
    if preview:
        return COMMAND_ROUTING_PREVIEW_LANGUAGES.get(language)
    return "en" if language.lower().split("-", 1)[0] == "en" else None


async def async_evaluate_command(
    hass: HomeAssistant, data: PhoenixData, token: TokenRecord,
    sentence: object, language: object = "en", *, preview: bool = False,
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
    recognition_language = routing_language(language, preview=preview)
    if recognition_language is None:
        return outcome("unsupported_language")
    runtime = data.command_routing
    if runtime.task is not None and not runtime.task.done():
        return outcome("busy")
    # Only recognition runs in the background. Projection and disclosure happen
    # synchronously after the authority check, never in a timed-out task.
    task = asyncio.create_task(async_recognize(hass, sentence.strip(), recognition_language, strict=True))
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
