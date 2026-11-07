"""Capability probes for a configured model."""

from __future__ import annotations

import base64
import logging
import random
import re
import struct
import zlib

from aiohttp import ClientSession

from .accounts import ProviderConfig
from .base import _IMAGE_REFUSAL, _REFUSAL_STATUSES
from .factory import build_provider
from ..provider_constants import (
    AGENT_CHAT_EFFORT_LEVEL_ORDER, AGENT_CHAT_PROBE_MAX_CALLS, AGENT_CHAT_PROBE_SENTINEL,
)


_LOGGER = logging.getLogger(__name__)


def _effort_probe_body(kind: str, level: str) -> dict | None:
    """The request fragment that carries an effort level for this backend, or None if it has none.

    Aggregators are probed, since a probe covers the one selected model. DeepSeek's and Z.ai's
    fragments carry the thinking toggle too, because they read effort only with thinking on.
    """
    # Probing the level alone on those two would test nothing and answer 200 for every value.
    if kind == "claude":
        return {"output_config": {"effort": level}}
    if kind == "minimax":
        # Only MiniMax-M3.1-Flash reads the effort; it documents a 400 for an
        # unknown level, so the two-stage probe is meaningful there.
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": level}}
    if kind in ("deepseek", "zai"):
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}
    if kind in ("openrouter", "infron"):
        # The nested object carries every level, including max.
        return {"reasoning": {"effort": level}}
    if kind in (
        "chatgpt", "grok", "gemini", "kimi", "meta", "mistral",
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
# Stops at the next opening tag, so a reply of unclosed tags costs one pass, not one per tag.
_PROBE_THINKING = re.compile(r"(?is)<think>(?:(?!<think>).)*?</think>")
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

    True when the colours are named in order; False for a wrong answer or a refused image; None for
    no usable answer, which establishes nothing and must not be recorded.
    """
    # Some providers accept image parts but drop them before the model, and no status code reveals
    # that. So the check asks what only sight can answer: three stripe colours in a random order,
    # which a text-only model guesses right about once in 120 tries. No usable answer covers an
    # unreachable host, a rate limit, an outage, or an empty reply from a model that spent its
    # output on reasoning.
    stripes = _probe_stripes()
    status, text = await build_provider(cfg).probe_image(session, _stripe_png(stripes), _PROBE_IMAGE_QUESTION)
    _LOGGER.debug("Image check for %s %s answered %s with %d characters", cfg.kind, cfg.model, status, len(text))
    if status in _REFUSAL_STATUSES and _IMAGE_REFUSAL.search(text.lower()):
        return False
    if status != 200:
        return None
    reply = _PROBE_THINKING.sub("", text).strip().lower()
    if not reply:
        return None
    return _PROBE_COLOUR_WORD.findall(reply)[:3] == stripes


async def async_probe_capabilities(
    session: ClientSession, cfg: ProviderConfig,
) -> tuple[dict, int, bool]:
    """What this model's API actually accepts, established by asking it.

    Returns (capabilities, calls_made, answered). A value not established is omitted, never guessed.
    `answered` is False when the control request did not answer 200.
    """
    # A key with no credit, a revoked key, a refused model or an unreachable host answers every
    # question alike, which must not read as a provider that does not validate its options. The
    # order is the whole method. A control with no option must answer 200 first, or a model refused
    # outright would read as refused options. A deliberately invalid effort must then answer 400,
    # proving the server validates the field. Only then does a 200 on a real level mean anything,
    # since a server that ignores unknown parameters answers 200 to every level.
    provider = build_provider(cfg)
    caps: dict = {}
    calls = 1
    if await provider.probe_option(session, {}) != 200:
        return caps, calls, False

    fragment = _effort_probe_body(cfg.kind, AGENT_CHAT_PROBE_SENTINEL)
    if fragment is not None:
        status = await provider.probe_option(session, fragment)
        calls += 1
        # Only a refusal (400 or 422) is evidence. Unreachable (None) says
        # nothing, and a 200 means the field is ignored or leniently defaulted,
        # which are indistinguishable and equally useless.
        if status in _REFUSAL_STATUSES:
            levels = []
            for level in AGENT_CHAT_EFFORT_LEVEL_ORDER:
                if calls >= AGENT_CHAT_PROBE_MAX_CALLS:
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

    if calls < AGENT_CHAT_PROBE_MAX_CALLS:
        # Temperature is the mirror-image question: not "which values", but
        # "does this model refuse the field", which is what reasoning models do.
        # So here the 400 is the finding and a 200 records nothing, since an
        # accepted temperature may still be ignored.
        status = await provider.probe_option(session, {"temperature": 0.7})
        calls += 1
        if status in _REFUSAL_STATUSES:
            caps["temperature"] = False

    return caps, calls, True
