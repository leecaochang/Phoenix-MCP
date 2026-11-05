"""Build the provider client for one account."""

from __future__ import annotations

from .accounts import PROVIDER_DEFINITIONS, ProviderConfig
from .provider_claude import ClaudeProvider
from .provider_openai import OpenAICompatProvider
from .provider_responses import OpenAIResponsesProvider


def build_provider(cfg: ProviderConfig) -> ClaudeProvider | OpenAICompatProvider:
    definition = PROVIDER_DEFINITIONS.get(cfg.kind)
    if definition is None:
        raise ValueError(f"Unknown provider kind: {cfg.kind}")
    if definition.purpose != "conversation":
        raise ValueError("Decision accounts cannot provide conversation or AI Task responses.")
    if definition.adapter == "anthropic":
        return ClaudeProvider(cfg)
    if definition.adapter == "openai_responses":
        return OpenAIResponsesProvider(cfg)
    return OpenAICompatProvider(cfg)
