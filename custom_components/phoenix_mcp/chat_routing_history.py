"""Bounded runtime evidence for conversations handled entirely by local commands."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import secrets
from time import monotonic
from typing import Any

from .const import (
    CHAT_ROUTING_HISTORY_LIMIT, CHAT_ROUTING_HISTORY_TTL_SECONDS,
    CHAT_ROUTING_HISTORY_MAX_MESSAGES, CHAT_ROUTING_HISTORY_MAX_BYTES,
)


@dataclass
class ChatRoutingHistory:
    """Store only keyed digests, never messages, targets, credentials or plans.

    Evidence is consumed before the next evaluation, so a fallback or failed
    turn cannot leave its old prefix eligible. This is routing admission only;
    it neither authorizes actions nor provides request deduplication.
    """

    _key: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    _entries: OrderedDict[str, float] = field(default_factory=OrderedDict, repr=False)

    def _digest(self, messages: list[dict], binding: Any) -> str | None:
        if not messages or len(messages) % 2 or len(messages) > CHAT_ROUTING_HISTORY_MAX_MESSAGES:
            return None
        characters = 0
        for index, message in enumerate(messages):
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or message["role"] != ("assistant" if index % 2 else "user")
                    or not isinstance(message["content"], str)):
                return None
            characters += len(message["content"])
            if characters > CHAT_ROUTING_HISTORY_MAX_BYTES:
                return None
        encoded = json.dumps([binding, messages], ensure_ascii=True, sort_keys=True).encode()
        if len(encoded) > CHAT_ROUTING_HISTORY_MAX_BYTES:
            return None
        return hmac.new(self._key, encoded, hashlib.sha256).hexdigest()

    def consume(self, messages: list[dict], binding: Any) -> bool:
        digest = self._digest(messages, binding)
        expires = self._entries.pop(digest, None) if digest is not None else None
        return expires is not None and expires > monotonic()

    def remember(self, messages: list[dict], binding: Any) -> None:
        digest = self._digest(messages, binding)
        if digest is None:
            return
        now = monotonic()
        self._entries = OrderedDict((key, expires) for key, expires in self._entries.items() if expires > now)
        self._entries[digest] = now + CHAT_ROUTING_HISTORY_TTL_SECONDS
        self._entries.move_to_end(digest)
        while len(self._entries) > CHAT_ROUTING_HISTORY_LIMIT:
            self._entries.popitem(last=False)
