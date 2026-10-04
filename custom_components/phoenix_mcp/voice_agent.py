"""Phoenix MCP as a Home Assistant conversation agent.

Registers Phoenix MCP via conversation.async_set_agent so it appears in Settings >
Voice assistants and drives HA's Assist pipeline with Phoenix MCP's OWN model loop,
the self-contained counterpart to the llm.API bridge (assist_api.py), which hands
Phoenix MCP's tools to a model from another integration. The module is NOT named
conversation.py so HA does not treat it as a conversation ENTITY platform.

Model-side work is reused from agentcli.py: provider accounts and keys, provider
construction, and the gated tool loop (async_run_voice_turn), which dispatches
every tool through mcp_view._dispatch_mcp so per-token scope, MESA, approvals and
audit apply identically. A confirm-gated action degrades to "queued, approve in the
Phoenix MCP panel" (voice has no inline Approve button), so a turn never blocks.
The bound token, provider and model are GlobalSettings.voice_agent_*, set in the
panel rather than a config-entry options flow.

HA-COUPLING POINT alongside ws_dispatch / assist_api: the conversation agent API
(AbstractConversationAgent / ConversationResult / IntentResponse) can shift across
HA versions; re-verify on upgrades.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal

from homeassistant.const import MATCH_ALL
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .helpers import voice_text

_LOGGER = logging.getLogger(__name__)

_MARKDOWN_FENCE_LINE_RE = re.compile(
    r"^[ \t]*(?:```+|~~~+)(?:[A-Za-z0-9_.+-]+)?[ \t]*$", re.MULTILINE
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^\n)]*\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\n)]*\)")
_MARKDOWN_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_MARKDOWN_QUOTE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_MARKDOWN_BULLET_RE = re.compile(r"^[ \t]*[-+*][ \t]+", re.MULTILINE)
_MARKDOWN_RULE_RE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.MULTILINE)
_MARKDOWN_STRONG_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_MARKDOWN_EMPHASIS_RE = re.compile(
    r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", re.DOTALL
)
_MARKDOWN_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|])")


def _spoken_text(text: str) -> str:
    """Remove common visual Markdown syntax before handing text to TTS."""
    spoken = _MARKDOWN_RULE_RE.sub("", text)
    spoken = _MARKDOWN_FENCE_LINE_RE.sub("", spoken)
    spoken = _MARKDOWN_IMAGE_RE.sub(r"\1", spoken)
    spoken = _MARKDOWN_LINK_RE.sub(r"\1", spoken)
    spoken = _MARKDOWN_HEADING_RE.sub("", spoken)
    spoken = _MARKDOWN_QUOTE_RE.sub("", spoken)
    spoken = _MARKDOWN_BULLET_RE.sub("", spoken)
    spoken = _MARKDOWN_ESCAPE_RE.sub(r"\1", spoken)
    spoken = _MARKDOWN_STRIKE_RE.sub(r"\1", spoken)
    spoken = _MARKDOWN_STRONG_RE.sub(r"\2", spoken)
    spoken = _MARKDOWN_EMPHASIS_RE.sub(r"\2", spoken)
    spoken = spoken.replace("`", "")
    spoken = re.sub(r"[*_~]{2,}", "", spoken)
    spoken = re.sub(r"[ \t]+\n", "\n", spoken)
    spoken = re.sub(r"\n{3,}", "\n\n", spoken)
    return spoken.strip()

# conversation is a core integration, but the import is guarded so setup never
# hard-crashes and the module imports in a minimal env (same feature-detect as
# assist_api).
try:
    from homeassistant.components import conversation as _conversation

    _CONVERSATION_AVAILABLE = True
except ImportError:  # pragma: no cover - conversation absent only in broken envs
    _conversation = None  # type: ignore[assignment]
    _CONVERSATION_AVAILABLE = False


def _base_url(hass: HomeAssistant) -> str:
    """Return HA's best available URL for approval/skill links, or "" if none."""
    try:
        from homeassistant.helpers.network import get_url  # noqa: PLC0415

        return get_url(hass)
    except Exception:  # noqa: BLE001 - NoURLAvailableError and anything else
        return ""


from .routing_statistics import tracked_turn


from .conversation_log import recorded_turn


@recorded_turn("voice", "user_text", transform=lambda text: _spoken_text(text))
async def async_voice_answer(
    hass: HomeAssistant, data: Any, user_text: str, *,
    include_review_links: bool = False, language: object = None,
    satellite_id: object = None, device_id: object = None,
    conversation_id: object = None, context: Context | None = None,
) -> str:
    """Run one voice turn inside the requesting HA user's authority scope.

    A change to that user mid-turn ends the turn with the "token unavailable" sentence.
    """
    from .household_authority import household_scope
    from .request_authority import AuthorityChangedError

    try:
        async with household_scope(hass, context):
            return await _async_voice_answer(
                hass, data, user_text, include_review_links=include_review_links,
                language=language, satellite_id=satellite_id, device_id=device_id,
                conversation_id=conversation_id,
            )
    except AuthorityChangedError:
        return voice_text(language, "token_unavailable")


@tracked_turn("voice")
async def _async_voice_answer(
    hass: HomeAssistant,
    data: Any,
    user_text: str,
    *,
    include_review_links: bool = False,
    language: object = None,
    satellite_id: object = None,
    device_id: object = None,
    conversation_id: object = None,
) -> str:
    """Resolve config, token and provider, then route or run the gated voice loop.

    Independent of the conversation base class. A provider or loop failure degrades
    to a spoken sentence instead of raising. include_review_links is passed through
    to the executors (True only when no satellite_id is set; see async_process).
    Decline sentences are rendered in the CONVERSATION's language
    (helpers.voice_text), not the server's; language=None falls back to English.
    """
    from .agentcli import (  # noqa: PLC0415
        _current_dispatch_access_key,
        _get_secret_store,
        build_provider,
        async_run_voice_turn,
    )

    settings = data.store.get_settings()
    if (
        not data.ready
        or data.shutting_down
        or settings.kill_switch
        or not settings.voice_agent_enabled
    ):
        return voice_text(language, "unavailable")
    if not (settings.voice_agent_token_id and settings.voice_agent_provider_id):
        return voice_text(language, "not_configured")

    # Re-resolve the token every turn (never cache it). This also zeroes the
    # inline-confirm wait, so a confirm gate returns pending immediately and the
    # loop reports it queued instead of blocking the turn.
    access_key = _current_dispatch_access_key(data, settings.voice_agent_token_id)
    if access_key is None:
        return voice_text(language, "token_unavailable")

    from .command_routing import RoutingAuthority, async_evaluate_command, effective_routing_mode

    routing_mode = effective_routing_mode(settings)
    authority = RoutingAuthority.capture(data, access_key) if routing_mode in ("local_shadow", "local", "hybrid") else None
    store = await _get_secret_store(hass)
    cfg = store.resolve(settings.voice_agent_provider_id, settings.voice_agent_model or None)
    if cfg is None or not cfg.model:
        return voice_text(language, "not_configured")

    from .conversation_log import record_identity
    record_identity(access_key=access_key, config=cfg)

    from .home_companion import is_companion
    if authority is not None and not is_companion(access_key):
        if authority.current(hass) is None:
            return voice_text(language, "token_unavailable")
        from .voice_context import capture_voice_origin
        from .command_clarification import (async_clarification_reply, clarification_binding, clarification_prompt, is_clarification_cancel)
        origin = capture_voice_origin(hass, satellite_id, device_id)
        context: dict[str, Any] = {'voice_origin': origin} if origin is not None else {}
        can_clarify = routing_mode in ('local', 'hybrid') and isinstance(conversation_id, str) and 0 < len(conversation_id) <= 128
        conversation = ['voice', conversation_id, satellite_id, device_id]
        binding = clarification_binding(hass, authority, language, origin)
        pending = data.command_routing.clarifications.consume(conversation, binding) if can_clarify else None
        if pending is not None:
            selected, reply = await async_clarification_reply(hass, data, access_key, pending, user_text,
                language if isinstance(language, str) else 'en', ingress='voice', voice_origin=origin)
            access_key = authority.current(hass)
            if access_key is None:
                return voice_text(language, 'token_unavailable')
            if reply is not None:
                if not pending.get('_expired') and is_clarification_cancel(user_text):
                    from .command_execution import LocalCommandReply
                    from .routing_statistics import local_result
                    local_result(LocalCommandReply(reply, 'cancelled'), 'local')
                return reply
            if selected is not None:
                from .command_execution import async_execute_local_command
                answer = await async_execute_local_command(hass, data, access_key, selected, authority,
                    _base_url(hass), include_review_links, language=language)
                return answer if answer is not None else voice_text(language, 'local_clarification_expired')
        if can_clarify:
            context['clarify'] = True
        observation = await async_evaluate_command(hass, data, access_key, user_text, language or "en", **context)
        access_key = authority.current(hass)
        if access_key is None or observation['reason'] == 'authority_changed':
            return voice_text(language, "token_unavailable")
        if can_clarify and observation.get('_clarification') is not None:
            pending = observation['_clarification']
            data.command_routing.clarifications.remember(conversation, binding, pending)
            return clarification_prompt(language, pending)
        data.command_routing.observe(observation)
        # Preserve the ordinary voice loop, including its zero inline wait.
        access_key = _current_dispatch_access_key(data, access_key.id)
        if access_key is None:
            return voice_text(language, "token_unavailable")
        if (settings.voice_agent_routing_mode in ("local", "local_shadow") and settings.decision_shadow_enabled and settings.decision_provider_id
                and observation["reason"] in ("no_match", "unsupported", "no_target")):
            # Single-flight background observation: it cannot delay or influence
            # the provider turn.
            data.decision_shadow.start(hass, data, access_key, user_text, language or "en", observe=True)
        if routing_mode in ("local", "hybrid") and observation["proposal"]:
            from .command_execution import async_execute_local_command

            answer = await async_execute_local_command(
                hass, data, access_key, observation["proposal"], authority, _base_url(hass), include_review_links, language=language,
            )
            if answer is not None:
                return answer
            if authority.current(hass) is None:
                return voice_text(language, "token_unavailable")
        if routing_mode == "hybrid" and not observation["proposal"]:
            from .decision_shadow import async_preview_decision
            from .command_execution import async_execute_command_plan

            decision = await async_preview_decision(hass, data, access_key, user_text, language or "en",
                                                    observation, targets=True, prepare=True)
            access_key = authority.current(hass)
            data.decision_shadow.observe_hybrid(decision, authority_changed=access_key is None)
            if access_key is None or decision["reason"] in ("authority_changed", "cancelled"):
                return voice_text(language, "token_unavailable")
            if decision.get("_plan") is not None:
                return await async_execute_command_plan(
                    hass, data, access_key, decision["_plan"], authority, _base_url(hass), include_review_links, language=language,
                )

    provider = build_provider(cfg)
    session = async_get_clientsession(hass)
    try:
        return await async_run_voice_turn(
            hass, data, access_key, provider, session, _base_url(hass), user_text,
            include_review_links=include_review_links,
            max_iterations=data.store.get_settings().agentcli_max_iterations,
            language=language,
        )
    except Exception:  # noqa: BLE001 - a provider/loop failure must not raise
        _LOGGER.exception("Phoenix MCP voice agent turn failed")
        return voice_text(language, "error")


if _CONVERSATION_AVAILABLE:

    class PhoenixConversationAgent(_conversation.AbstractConversationAgent):
        """A conversation agent that runs Phoenix MCP's scoped model loop for one bound token."""

        def __init__(self, hass: HomeAssistant, data: Any) -> None:
            self.hass = hass
            self._data = data

        @property
        def supported_languages(self) -> list[str] | Literal["*"]:
            # No language gate; the underlying model handles it.
            return MATCH_ALL

        async def async_process(
            self, user_input: "_conversation.ConversationInput"
        ) -> "_conversation.ConversationResult":
            """Answer one utterance, spoken as the final assistant text."""
            # A hardware satellite sets satellite_id, so approval links are suppressed
            # there. Companion-app STT does not set it, and HA discards the pipeline's
            # start_stage before calling the agent, so app voice and typed turns are
            # indistinguishable. Every response is sanitized because HA uses this one
            # plain-speech value for both its visible Assist reply and TTS input.
            satellite_id = getattr(user_input, "satellite_id", None)
            from uuid import uuid4
            conversation_id = user_input.conversation_id or uuid4().hex
            text = await async_voice_answer(
                self.hass, self._data, user_input.text,
                include_review_links=satellite_id is None,
                language=user_input.language,
                satellite_id=satellite_id,
                device_id=getattr(user_input, 'device_id', None),
                conversation_id=conversation_id,
                context=getattr(user_input, "context", None),
            )
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech(_spoken_text(text))
            return _conversation.ConversationResult(
                response=response,
                conversation_id=conversation_id,
            )

else:  # pragma: no cover - conversation absent only in broken envs
    PhoenixConversationAgent = None  # type: ignore[assignment,misc]


def async_register_voice_agent(hass: HomeAssistant, entry: Any, data: Any) -> None:
    """Register (or replace) Phoenix MCP's conversation agent for this config entry.

    Idempotent (async_set_agent replaces by entry_id); never raises. A no-op when
    the conversation component is unavailable.
    """
    if not _CONVERSATION_AVAILABLE:
        return
    try:
        _conversation.async_set_agent(hass, entry, PhoenixConversationAgent(hass, data))
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Could not register the Phoenix MCP voice agent", exc_info=True)


def async_unregister_voice_agent(hass: HomeAssistant, entry: Any) -> None:
    """Unregister Phoenix MCP's conversation agent. Idempotent; never raises."""
    if not _CONVERSATION_AVAILABLE:
        return
    try:
        _conversation.async_unset_agent(hass, entry)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Voice agent unregister failed", exc_info=True)


def async_sync_voice_agent(hass: HomeAssistant, entry: Any, data: Any) -> None:
    """Register or unregister the agent to match current settings.

    Registered only when fully configured (enabled + token + provider + model) and
    not shutting down, so Phoenix MCP never appears as an agent that answers "not
    configured"; a deleted provider or revoked token clears its field and
    unregisters it.

    The kill switch is deliberately NOT part of this gate. With it on, the agent
    stays registered but async_voice_answer declines every turn before resolving
    the token or dispatching any tool, so nothing runs. An Assist pipeline pointed
    at Phoenix MCP then degrades to a spoken "unavailable" instead of a hard "agent
    not found" error, and recovers when the kill switch is turned off. The kill
    switch gates the network route surface, not in-process helpers.
    """
    s = data.store.get_settings()
    fully_configured = bool(
        s.voice_agent_enabled and s.voice_agent_token_id
        and s.voice_agent_provider_id and s.voice_agent_model
    )
    # Do not require data.ready: the agent is wired before the final ready flag is
    # published, so requiring it would skip registration on every HA restart. The
    # per-turn async_voice_answer gate keeps it unavailable until startup ends.
    if fully_configured and not data.shutting_down:
        async_register_voice_agent(hass, entry, data)
    else:
        async_unregister_voice_agent(hass, entry)


# --- One-click Assist pipeline setup -------------------------------------------------
#
# Phoenix MCP can create an Assist pipeline that points at its own conversation
# agent, so the operator need not wire it up in Settings > Voice assistants. This
# reaches into assist_pipeline internals (the Pipeline store, the private default
# settings resolver), a HA-COUPLING POINT re-verified on upgrades. Every access is
# guarded and degrades to a clean error, and the private resolver has a hand-built
# text-only fallback, so a resolver rename only loses STT/TTS auto-population.

try:  # assist_pipeline is a default HA integration but guard like the rest.
    from homeassistant.components.assist_pipeline.pipeline import (  # noqa: PLC0415
        KEY_ASSIST_PIPELINE as _KEY_ASSIST_PIPELINE,
    )

    _ASSIST_PIPELINE_AVAILABLE = True
except ImportError:  # pragma: no cover - assist_pipeline absent only in minimal envs
    _KEY_ASSIST_PIPELINE = None  # type: ignore[assignment]
    _ASSIST_PIPELINE_AVAILABLE = False


class VoicePipelineError(Exception):
    """A one-click Assist pipeline operation could not complete."""


def assist_pipeline_supported() -> bool:
    """Whether the running HA exposes the Assist pipeline store seam (one-click)."""
    return _ASSIST_PIPELINE_AVAILABLE


def _pipeline_store(hass: HomeAssistant) -> Any:
    """Return the Assist pipeline storage collection, or None if unavailable."""
    if not _ASSIST_PIPELINE_AVAILABLE:
        return None
    pipeline_data = hass.data.get(_KEY_ASSIST_PIPELINE)
    return getattr(pipeline_data, "pipeline_store", None)


def _resolve_pipeline_settings(hass: HomeAssistant, entry_id: str) -> dict:
    """Build pipeline settings for Phoenix MCP's agent (best-effort STT/TTS defaults)."""
    try:
        from homeassistant.components.assist_pipeline.pipeline import (  # noqa: PLC0415
            _async_resolve_default_pipeline_settings,
        )

        settings = _async_resolve_default_pipeline_settings(
            hass, conversation_engine_id=entry_id, pipeline_name="Phoenix MCP"
        )
    except Exception:  # noqa: BLE001 - private helper drift: fall back to text-only
        # WARNING, not debug: the fallback yields a pipeline with no STT or TTS, and
        # the operator would otherwise get no signal that anything degraded. The
        # catch is broad because the resolver is private and may raise for reasons
        # beyond an import failure.
        _LOGGER.warning(
            "Assist pipeline settings resolver unavailable; creating a text-only "
            "pipeline with no STT/TTS. Set these in Settings > Voice assistants.",
            exc_info=True,
        )
        lang = hass.config.language or "en"
        settings = {
            "conversation_engine": entry_id,
            "conversation_language": lang,
            "language": lang,
            "name": "Phoenix MCP",
            "stt_engine": None,
            "stt_language": None,
            "tts_engine": None,
            "tts_language": None,
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
        }
    # Phoenix MCP must actually receive the utterance, so never prefer local intent handling.
    # The dict mixes str/None/bool values, so it is not a dict[str, str | None].
    settings["prefer_local_intents"] = False  # type: ignore[assignment]
    return settings


async def async_create_assist_pipeline(
    hass: HomeAssistant, entry: Any, data: Any, *, preferred: bool
) -> dict:
    """Create (or return the existing) Phoenix MCP Assist pipeline; optionally set preferred.

    Requires the voice agent to be fully configured so its conversation agent is
    registered and the pipeline resolves to a live agent. The created pipeline id is
    persisted on settings so teardown can remove it. Raises VoicePipelineError on
    any problem the caller should surface.
    """
    store = _pipeline_store(hass)
    if store is None:
        raise VoicePipelineError("Assist pipeline support is not available.")

    s = data.store.get_settings()
    if not (
        s.voice_agent_enabled and s.voice_agent_token_id
        and s.voice_agent_provider_id and s.voice_agent_model
    ):
        raise VoicePipelineError(
            "Configure and enable the Phoenix MCP voice agent (access key, provider, and model) first."
        )

    # Idempotent: if a tracked pipeline still exists, reuse it rather than duplicate.
    existing = s.voice_agent_pipeline_id
    if existing is not None and existing in getattr(store, "data", {}):
        if preferred:
            _set_preferred(store, existing)
        item = store.data[existing]
        return {"pipeline_id": existing, "name": getattr(item, "name", "Phoenix MCP"),
                "preferred": _is_preferred(store, existing)}

    # Ensure the agent is registered so conversation_engine resolves to a live agent.
    async_register_voice_agent(hass, entry, data)
    settings = _resolve_pipeline_settings(hass, entry.entry_id)
    try:
        pipeline = await store.async_create_item(settings)
    except Exception as err:  # noqa: BLE001
        raise VoicePipelineError("Could not create the Phoenix MCP Assist pipeline.") from err

    if preferred:
        _set_preferred(store, pipeline.id)

    async with data.store.async_lock:
        await data.store.async_patch_settings(voice_agent_pipeline_id=pipeline.id)
    return {"pipeline_id": pipeline.id, "name": getattr(pipeline, "name", "Phoenix MCP"),
            "preferred": _is_preferred(store, pipeline.id)}


async def async_remove_assist_pipeline(hass: HomeAssistant, data: Any) -> None:
    """Delete the Phoenix-created Assist pipeline if one is tracked. Best-effort.

    Clears the tracked id even if the pipeline is already gone. A preferred pipeline
    cannot be deleted directly, so preference is first moved to any other pipeline;
    if Phoenix MCP's is the only one it is left in place (HA keeps at least one pipeline).
    """
    s = data.store.get_settings()
    pipeline_id = s.voice_agent_pipeline_id
    if pipeline_id is None:
        return

    store = _pipeline_store(hass)
    if store is not None and pipeline_id in getattr(store, "data", {}):
        try:
            if _is_preferred(store, pipeline_id):
                other = next((pid for pid in store.data if pid != pipeline_id), None)
                if other is None:
                    # Phoenix MCP's is the only/preferred pipeline; HA keeps at least one,
                    # so leave it (clearing the tracked id below is enough).
                    _LOGGER.debug("Phoenix MCP pipeline is the only preferred one; not deleting")
                else:
                    _set_preferred(store, other)
            if not _is_preferred(store, pipeline_id):
                await store.async_delete_item(pipeline_id)
        except Exception:  # noqa: BLE001 - never let cleanup raise
            _LOGGER.warning("Could not remove the Phoenix MCP Assist pipeline", exc_info=True)

    async with data.store.async_lock:
        await data.store.async_patch_settings(voice_agent_pipeline_id=None)


def _is_preferred(store: Any, pipeline_id: str) -> bool:
    try:
        return store.async_get_preferred_item() == pipeline_id
    except Exception:  # noqa: BLE001
        return False


def _set_preferred(store: Any, pipeline_id: str) -> None:
    try:
        store.async_set_preferred_item(pipeline_id)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not set preferred Assist pipeline", exc_info=True)
