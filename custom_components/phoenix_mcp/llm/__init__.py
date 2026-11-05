"""Model provider clients for Agent Chat, voice and AI Task.

The package takes no external Python dependency: the Anthropic Messages API and
the OpenAI-compatible provider APIs are spoken over raw aiohttp via Home
Assistant's shared client session, parsing streaming SSE by hand. A provider API
key is loaded from a dedicated secrets Store and is never logged, persisted into
a transcript, or echoed in an error payload.
"""
