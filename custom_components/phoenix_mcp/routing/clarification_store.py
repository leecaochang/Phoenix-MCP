"""Bind ephemeral clarification requests to one conversation and the current configuration."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import secrets
from time import monotonic
from typing import Any

from .routing_tables import COMMAND_CLARIFICATION_LIMIT, COMMAND_CLARIFICATION_TTL_SECONDS


@dataclass
class CommandClarifications:
    """Bound ephemeral utterances to an exact conversation and current configuration."""

    _key: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    _entries: OrderedDict[str, tuple[float, str, dict]] = field(default_factory=OrderedDict, repr=False)

    def _digest(self, value: Any) -> str:
        return hmac.new(self._key, json.dumps(value, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()

    def remember(self, conversation: Any, binding: Any, request: dict) -> None:
        now = monotonic()
        self._entries = OrderedDict((key, value) for key, value in self._entries.items() if value[0] > now)
        key = self._digest(conversation)
        self._entries[key] = (now + COMMAND_CLARIFICATION_TTL_SECONDS, self._digest(binding), deepcopy(request))
        self._entries.move_to_end(key)
        while len(self._entries) > COMMAND_CLARIFICATION_LIMIT:
            self._entries.popitem(last=False)

    def consume(self, conversation: Any, binding: Any) -> dict | None:
        entry = self._entries.pop(self._digest(conversation), None)
        if entry is None:
            return None
        if entry[0] <= monotonic() or not hmac.compare_digest(entry[1], self._digest(binding)):
            return {'_expired': True}
        return entry[2]

    def clear(self) -> None:
        """Discard retained utterances and bindings on an explicit core wipe."""
        self._entries.clear()
        self._key = secrets.token_bytes(32)
