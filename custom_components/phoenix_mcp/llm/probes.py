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
    AGENTCLI_EFFORT_LEVEL_ORDER, AGENTCLI_PROBE_MAX_CALLS, AGENTCLI_PROBE_SENTINEL,
)


_LOGGER = logging.getLogger(__name__)


def _effort_probe_body(kind: str, level: str) -> dict | None:
    """The request fragment that carries an effort level for this backend.

    None for a backend with no effort control at all, which is then not probed.
    The aggregators
    (OpenRouter, NVIDIA NIM) ARE probed: what they pass through varies per model,
    but a probe runs against the one selected model, so that variation is exactly
    what it handles.

    DeepSeek's and Z.ai's fragments carry the thinking toggle too, because they
    only read the effort when thinking is enabled: probing the level alone would
    test nothing and answer 200 for every value.
    """
    if kind == "claude":
        return {"output_config": {"effort": level}}
    if kind == "minimax":
        # Only MiniMax-M3.1-Flash reads the effort; it documents a 400 for an
        # unknown level, so the two-stage probe is meaningful there.
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": level}}
    if kind in ("deepseek", "zai"):
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}
    if kind == "openrouter":
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
    if status in _REFUSAL_STATUSES and _IMAGE_REFUSAL.search(text.lower()):
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

    Returns (capabilities, calls_made, answered). A value that could not be
    established is omitted, never guessed, so a partial or failed probe narrows
    nothing. `answered` is False when the control request did not answer 200: a
    key with no credit, a revoked key, a refused model or an unreachable host
    answers every question alike, and reporting that as "this provider does not
    validate its options" would blame the provider for an account problem.

    TWO STAGES, and the order is the whole method. Stage one sends a deliberately
    INVALID effort: a 400 proves the server validates the field. Only then does
    stage two walk the real levels, where a 200 is meaningful; a server that
    ignores an unknown parameter answers 200 to every level. Both stages need a
    CONTROL first, the same minimal request with no option answering 200: a model
    the endpoint refuses outright (wrong id, wrong plan, an unrelated rule)
    answers 400 to everything, and those 400s would read as refused options. A
    refused control establishes nothing and reports the provider as not answering.
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
        # Only a refusal (400 or 422) is evidence. Unreachable (None) says
        # nothing, and a 200 means the field is ignored or leniently defaulted,
        # which are indistinguishable and equally useless.
        if status in _REFUSAL_STATUSES:
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
        if status in _REFUSAL_STATUSES:
            caps["temperature"] = False

    return caps, calls, True
