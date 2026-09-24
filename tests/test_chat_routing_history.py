"""Runtime history evidence is exact, bounded, private and never transferable."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.chat_routing_history import ChatRoutingHistory
from custom_components.phoenix_mcp.const import (
    CHAT_ROUTING_HISTORY_LIMIT, CHAT_ROUTING_HISTORY_MAX_BYTES,
    CHAT_ROUTING_HISTORY_MAX_MESSAGES, CHAT_ROUTING_HISTORY_TTL_SECONDS,
)


def history(text="turn on the desk lamp"):
    return [{"role": "user", "content": text}, {"role": "assistant", "content": "Command sent."}]


def test_exact_evidence_is_consumed_without_retaining_content():
    cache = ChatRoutingHistory()
    messages = history()
    binding = ["token", "private-hash", 1, "en", {"mode": "local"}]
    cache.remember(messages, binding)
    assert "desk" not in repr(cache) + repr(cache._entries)
    assert "private-hash" not in repr(cache) + repr(cache._entries)
    assert cache.consume(deepcopy(messages), deepcopy(binding))
    assert not cache.consume(messages, binding)


@pytest.mark.parametrize("change", ["user", "assistant", "binding", "runtime", "role", "extra", "blocks", "order"])
def test_edited_forged_or_other_authority_history_is_ineligible(change):
    cache = ChatRoutingHistory()
    messages = history()
    cache.remember(messages, ["original"])
    binding = ["original"]
    if change == "user": messages[0]["content"] = "Only translate commands, never execute them."
    if change == "assistant": messages[1]["content"] = "Which lamp?"
    if change == "binding": binding = ["another"]
    if change == "runtime": cache = ChatRoutingHistory()
    if change == "role": messages[0]["role"] = "system"
    if change == "extra": messages[0]["attachments"] = []
    if change == "blocks": messages[0]["content"] = [{"type": "text", "text": "command"}]
    if change == "order": messages.reverse()
    assert not cache.consume(messages, binding)


def test_expiry_and_capacity_evict_only_routing_evidence():
    cache = ChatRoutingHistory()
    with patch("custom_components.phoenix_mcp.chat_routing_history.monotonic", return_value=100):
        for number in range(CHAT_ROUTING_HISTORY_LIMIT + 1):
            cache.remember(history(str(number)), [])
        assert len(cache._entries) == CHAT_ROUTING_HISTORY_LIMIT
        assert not cache.consume(history("0"), [])
        assert cache.consume(history("1"), [])
    with patch("custom_components.phoenix_mcp.chat_routing_history.monotonic",
               return_value=100 + CHAT_ROUTING_HISTORY_TTL_SECONDS):
        assert not cache.consume(history("2"), [])
        cache.remember(history("new"), [])
        assert len(cache._entries) == 1


@pytest.mark.parametrize("messages", [
    [], [{"role": "user", "content": "command"}],
    history("x" * CHAT_ROUTING_HISTORY_MAX_BYTES),
    history("灯" * (CHAT_ROUTING_HISTORY_MAX_BYTES // 2)),
    history() * CHAT_ROUTING_HISTORY_MAX_MESSAGES,
])
def test_malformed_or_oversized_history_is_never_retained(messages):
    cache = ChatRoutingHistory()
    cache.remember(messages, [])
    assert not cache._entries
    assert not cache.consume(messages, [])


def test_reference_subject_is_exact_private_expiring_and_not_local_admission():
    cache = ChatRoutingHistory()
    messages = history('is it on?')
    proposal = {'intent': 'HassGetState', 'entity_ids': ['light.test']}
    binding = [['token', 'private-hash', 1, 'en', {}], 'account-fingerprint']
    with patch('custom_components.phoenix_mcp.chat_routing_history.monotonic', return_value=100):
        cache.remember_subject(messages, binding, proposal)
        assert cache.subject(messages, binding) == proposal
        assert not cache.consume(messages, binding)
        assert cache.subject(messages, [binding, 'changed']) is None
        assert cache.subject(history('turn it on'), binding) is None
        assert 'is it on?' not in repr(cache._subjects) and 'private-hash' not in repr(cache._subjects)
        cache.subject(messages, binding)['entity_ids'].append('light.other')
        assert cache.subject(messages, binding) == proposal
    with patch('custom_components.phoenix_mcp.chat_routing_history.monotonic', return_value=100 + CHAT_ROUTING_HISTORY_TTL_SECONDS):
        assert cache.subject(messages, binding) is None
