"""Conversation privacy, isolation, retention and real dispatch integration."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util.dt import utcnow

from custom_components.phoenix_mcp import conversation_log as history
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import DOMAIN
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.mcp_view import _dispatch_mcp
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.token_store import GlobalSettings, TokenRecord, TokenStore


@pytest.fixture
def conversation_data(hass):
    token = TokenRecord(id="test-id", name="test-client", token_hash="hash", created_at=utcnow(),
                        created_by="test-admin", pass_through=True)
    store = MagicMock(spec=TokenStore)
    store.get_settings.return_value = GlobalSettings(conversation_log_enabled=True, mesa_mode="off")
    store.get_token_by_id.return_value = token
    store.get_token_by_hash.return_value = token
    store.get_pending_approvals.return_value = []
    store.get_entity_hints.return_value = {}
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog(), hass=hass)
    hass.data[DOMAIN] = data
    yield data, token
    if data.sensor_flush_cancel is not None:
        data.sensor_flush_cancel()


def only(log):
    rows = log.query()["entries"]
    assert len(rows) == 1
    return log.detail(rows[0]["id"])


@pytest.mark.parametrize("value,secret", [
    ({"nested": {"password": "canary-one"}}, "canary-one"),
    ('{"api_key":"canary-two"}', "canary-two"),
    ("My password is canary-three", "canary-three"),
    ("Authorization: Bearer canary-four", "canary-four"),
    ({"https://canary-five:password@example.test/?token=canary-six": "ok"}, "canary-five"),
    ("sk-proj-abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvwx"),
    ("private_key: -----BEGIN PRIVATE KEY-----\ncanary-seven\n-----END PRIVATE KEY-----", "canary-seven"),
])
def test_secret_redaction_including_keys_and_free_text(value, secret):
    result = history.sanitized(value, max_bytes=100000)
    assert secret not in json.dumps(result)


def test_media_and_private_fields_are_not_retained():
    result = history.sanitized({"image": {"type": "image", "mimeType": "image/png", "data": "canary-image"},
                                "text": "![a](data:image/png;base64,canary-binary)",
                                "_authority": "canary-authority"}, max_bytes=100000)
    assert "canary" not in json.dumps(result)
    assert result["image"]["mime_type"] == "image/png"


def test_bounds_fail_closed_and_original_is_not_mutated():
    value = {"password": "secret", "items": ["ok"]}
    original = deepcopy(value)
    history.sanitized(value, max_bytes=10000)
    assert value == original
    with pytest.raises(ValueError):
        history.sanitized({"value": "x" * 1000}, max_bytes=100)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError):
        history.sanitized(cycle, max_bytes=10000)


def test_recording_opt_in_and_all_logging_switch():
    log = history.ConversationLog()
    settings = GlobalSettings()
    log.configure(settings)
    log.begin("mcp", prompt="not retained")
    assert not log.query()["entries"]
    settings.conversation_log_enabled = True
    settings.disable_all_logging = True
    log.configure(settings)
    log.begin("mcp", prompt="not retained either")
    assert not log.query()["entries"]


@pytest.mark.asyncio
async def test_real_external_dispatch_keeps_full_result_and_denials(hass, conversation_data):
    data, token = conversation_data
    text = "ordinary " * 2000
    hass.states.async_set("sensor.conversation_test", "ok", {"description": text})
    result = await _dispatch_mcp("tools/call", 1, {"name": "get_state", "arguments": {"entity_id": "sensor.conversation_test", "projection": {"kind": "full"}}},
                                 token, hass, data, "127.0.0.1", "")
    entry = only(data.conversations)
    assert result[3] == "allowed", result
    assert entry["prompt"] is None and entry["response"] is None
    assert entry["coverage"] == "tool_exchange"
    assert entry["events"][1]["payload"] == result[0]
    assert text in json.dumps(entry)
    token.pass_through = False
    result = await _dispatch_mcp("tools/call", 2, {"name": "get_state", "arguments": {"entity_id": "sensor.conversation_test", "projection": {"kind": "full"}}},
                                 token, hass, data, "127.0.0.1", "")
    assert result[3] == "denied"
    denied = data.conversations.detail(data.conversations.query()["entries"][0]["id"])
    assert denied["execution_status"] == "denied"


@pytest.mark.asyncio
async def test_concurrent_owned_turns_never_mix_same_token(hass, conversation_data):
    data, token = conversation_data
    hass.states.async_set("sensor.conversation_test", "on")

    @history.recorded_turn("test", "prompt")
    async def turn(data, prompt):
        await asyncio.sleep(0)
        await _dispatch_mcp("tools/call", 1, {"name": "get_state", "arguments": {"entity_id": "sensor.conversation_test", "projection": {"kind": "full"}}},
                            token, hass, data, "agentcli", "")
        return prompt + " reply"

    await asyncio.gather(turn(data, "first"), turn(data, "second"))
    assert len(data.conversations.query()["entries"]) == 2
    for summary in data.conversations.query()["entries"]:
        entry = data.conversations.detail(summary["id"])
        assert entry["response"] == entry["prompt"] + " reply"
        assert entry["coverage"] == "full_turn"
        assert entry["tool_count"] == 1


@pytest.mark.asyncio
async def test_nested_delegation_no_duplicate_entry(conversation_data):
    data, _ = conversation_data

    @history.recorded_turn("nested", "prompt")
    async def inner(data, prompt):
        return prompt

    @history.recorded_turn("voice", "prompt", transform=lambda s: s.upper())
    async def outer(data, prompt):
        return await inner(data, prompt)

    assert await outer(data, "hello") == "hello"
    entry = only(data.conversations)
    assert entry["source"] == "voice" and entry["response"] == "HELLO"


@pytest.mark.asyncio
async def test_stream_fragments_scrubbed_together_and_reasoning_excluded(conversation_data):
    data, token = conversation_data

    @history.recorded_chat
    async def chat(**kwargs):
        emit = kwargs["emit"]
        for fragment in ["My pass", "word is ", "canary-stream", "\nDone"]:
            await emit("assistant_delta", {"text": fragment})
        await emit("thinking_delta", {"text": "private thought"})
        await emit("error", {"code": "provider_error", "message": "Unavailable"})

    await chat(data=data, token=token, messages=[{"role": "user", "content": "Hello"}], emit=AsyncMock())
    entry = only(data.conversations)
    assert "canary-stream" not in json.dumps(entry)
    assert "private thought" not in json.dumps(entry)
    assert "Done" in entry["response"]
    assert entry["response_status"] == "failed"
    assert entry["coverage"] == "partial_conversation"


@pytest.mark.asyncio
async def test_clear_and_disable_invalidate_running_handles(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("voice", prompt="before")
    await log.async_clear()
    log.finish(handle, response="late")
    assert not log.query()["entries"]
    handle = log.begin("voice", prompt="second")
    settings = data.store.get_settings()
    settings.conversation_log_enabled = False
    log.configure(settings)
    settings.conversation_log_enabled = True
    log.configure(settings)
    log.finish(handle, response="late")
    entry = only(log)
    assert entry["response"] is None and entry["status"] == "interrupted"


@pytest.mark.asyncio
async def test_enable_during_disabled_turn_does_not_backfill(hass, conversation_data):
    data, token = conversation_data
    settings = data.store.get_settings()
    settings.conversation_log_enabled = False

    @history.recorded_turn("voice", "prompt")
    async def turn(data, prompt):
        settings.conversation_log_enabled = True
        await _dispatch_mcp("tools/call", 1, {"name": "get_state", "arguments": {"entity_id": "sensor.unavailable"}},
                            token, hass, data, "voice", "")
        return "end"

    await turn(data, "unrecorded")
    assert not data.conversations.query()["entries"]


def test_privacy_and_sanitizer_failure_never_keep_raw(conversation_data, monkeypatch):
    data, _ = conversation_data
    settings = data.store.get_settings()
    settings.log_entity_names = False
    log = history.recorder(data)
    handle = log.begin("voice", prompt="hidden entity")
    log.finish(handle, response="also hidden")
    entry = only(log)
    assert entry["prompt"] is None and entry["response"] is None
    assert entry["omissions"] == ["privacy"]
    settings.log_entity_names = True
    monkeypatch.setattr(history, "sanitized", MagicMock(side_effect=RuntimeError("secret")))
    log.event(handle, "error", "canary")
    assert "canary" not in json.dumps(only(log))


@pytest.mark.asyncio
async def test_retention_and_restart_interrupts_unfinished(conversation_data):
    data, _ = conversation_data
    settings = data.store.get_settings()
    settings.conversation_log_max_entries = 2
    persisted = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    log = history.ConversationLog(persisted)
    log.configure(settings)
    old = log.begin("voice", prompt="old")
    log.begin("voice", prompt="middle")
    latest = log.begin("voice", prompt="latest")
    assert log.detail(old[1]) is None
    log.event(latest, "tool_call", {"name": "get_state"})
    await log.async_save()
    persisted.async_load.return_value = persisted.async_save.call_args.args[0]
    restored = history.ConversationLog(persisted)
    restored.configure(settings)
    await restored.async_load()
    entry = restored.detail(latest[1])
    assert entry["status"] == "interrupted" and entry["execution_status"] == "unknown"
    settings.conversation_log_max_days = 1
    for raw in persisted.async_load.return_value["entries"]:
        raw["started_at"] = (utcnow() - timedelta(days=2)).isoformat()
    await restored.async_load()
    assert not restored.query()["entries"]


def test_large_payload_limit_preserves_completion(conversation_data):
    data, _ = conversation_data
    data.store.get_settings().conversation_log_max_mb = 1
    log = history.recorder(data)
    handle = log.begin("voice", prompt="hello")
    log.event(handle, "tool_result", "a" * 1_000_001, outcome="allowed")
    log.finish(handle, response="done")
    entry = only(log)
    assert entry["status"] == "completed"
    assert "size_limit" in entry["omissions"]
    assert log.query()["bytes"] <= 1_000_000


@pytest.mark.asyncio
async def test_delayed_approval_survives_restart_but_not_clear(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("voice", prompt="change")
    approval = SimpleNamespace(id="approval-one", tool_name="set_state", status="pending", diff={"password": "canary"}, result=None, rejected_reason=None)
    context = history._current.set(handle)
    try:
        log.approval(approval)
    finally:
        history._current.reset(context)
    log.finish(handle, response="Queued")
    persisted = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    log._store = persisted
    await log.async_save()
    persisted.async_load.return_value = persisted.async_save.call_args.args[0]
    log = history.ConversationLog(persisted)
    log.configure(data.store.get_settings())
    await log.async_load()
    approval.status = "approved"
    approval.result = {"outcome": "allowed", "result": "saved"}
    log.approval(approval, resolved=True)
    entry = only(log)
    assert entry["execution_status"] == "allowed"
    assert [event["kind"] for event in entry["events"]] == ["approval_requested", "approval_resolved"]
    assert "canary" not in json.dumps(entry)
    await log.async_clear()
    log.approval(approval, resolved=True)
    assert not log.query()["entries"]


@pytest.mark.asyncio
async def test_storage_failure_is_reported_without_raw_exception(conversation_data, caplog):
    data, _ = conversation_data
    persisted = SimpleNamespace(async_save=AsyncMock(side_effect=OSError("secret-exception")))
    log = history.ConversationLog(persisted)
    log.configure(data.store.get_settings())
    log.begin("voice", prompt="hello")
    await log.async_save()
    assert not log.query()["available"]
    assert "secret-exception" not in caplog.text
    persisted.async_save.side_effect = None
    await log.async_save()
    assert log.query()["available"]


@pytest.mark.asyncio
async def test_cleared_approval_scope_cannot_create_fresh_exchange(hass, conversation_data):
    data, token = conversation_data
    log = history.recorder(data)
    await log.async_clear()
    with log.approval_scope("deleted-approval"):
        await _dispatch_mcp("tools/call", 1, {"name": "get_state", "arguments": {"entity_id": "sensor.missing"}},
                            token, hass, data, "assist", "")
    assert not log.query()["entries"]


def test_approval_does_not_resume_capture_after_disable(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("mcp")
    approval = SimpleNamespace(id="old", tool_name="set_state", status="pending", diff=None, result=None, rejected_reason=None)
    ctx = history._current.set(handle)
    try:
        log.approval(approval)
    finally:
        history._current.reset(ctx)
    log.finish(handle)
    settings = data.store.get_settings()
    settings.conversation_log_enabled = False
    log.configure(settings)
    settings.conversation_log_enabled = True
    log.configure(settings)
    approval.status = "approved"
    log.approval(approval, resolved=True)
    assert len(only(log)["events"]) == 1


@pytest.mark.asyncio
async def test_restart_skips_bad_rows_preserves_safe_identity(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("voice", prompt="retained", metadata={"token_id": "safe-id", "token_name": "Client"})
    log.finish(handle, response="done")
    entry = only(log)
    persisted = SimpleNamespace(async_load=AsyncMock(return_value={"entries": [{"bad": object()}, entry, {**entry, "id": "bad", "events": [None]}]}))
    restored = history.ConversationLog(persisted)
    restored.configure(data.store.get_settings())
    await restored.async_load()
    assert only(restored)["metadata"]["token_id"] == "safe-id"
    assert restored.query()["bytes"] == log.query()["bytes"]


def test_inline_resolution_is_not_overwritten_by_pending_receipt(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("voice")
    ctx = history._current.set(handle)
    tool = history._tool.set("operation")
    approval = SimpleNamespace(id="inline", tool_name="set_state", status="pending", diff=None, result=None, rejected_reason=None)
    try:
        log.event(handle, "tool_call", {"name": "set_state"})
        log.approval(approval)
        approval.status = "approved"
        approval.result = {"outcome": "allowed"}
        log.approval(approval, resolved=True)
        log.event(handle, "tool_result", {}, outcome="pending_approval")
        log.finish(handle)
    finally:
        history._tool.reset(tool)
        history._current.reset(ctx)
    assert only(log)["execution_status"] == "allowed"


@pytest.mark.asyncio
async def test_changed_configuration_remains_visible_after_response_failure(conversation_data):
    from custom_components.phoenix_mcp.tool_common import _record_version
    data, token = conversation_data
    data.versions.record = AsyncMock()

    @history.recorded_chat
    async def chat(**kwargs):
        handle = history._current.get()
        data.conversations.event(handle, "tool_call", {"name": "update_script"})
        await _record_version(data, token, resource_type="script", resource_id="smoke", action="update",
                              before={"text": "api_key: canary-before\nold"}, after={"text": "api_key: canary-after\nnew"})
        data.conversations.event(handle, "tool_result", {"success": True}, outcome="allowed")
        await kwargs["emit"]("error", {"code": "provider_error", "message": "Unavailable"})

    await chat(data=data, token=token, messages=[{"role": "user", "content": "Change the script"}], emit=AsyncMock())
    entry = only(data.conversations)
    assert entry["change_count"] == 1 and entry["execution_status"] == "allowed"
    assert entry["status"] == "failed" and entry["response_status"] == "failed"
    change = next(e["payload"] for e in entry["events"] if e["kind"] == "change")
    assert change["before"]["text"].endswith("old") and change["after"]["text"].endswith("new")
    assert "canary" not in json.dumps(entry)


@pytest.mark.asyncio
async def test_uncertain_write_failure_preserves_transport_outcome(hass, conversation_data, monkeypatch):
    from custom_components.phoenix_mcp import mcp_view
    data, token = conversation_data
    monkeypatch.setattr(mcp_view, "_dispatch_mcp_authorized", AsyncMock(side_effect=OSError("synthetic write failure")))
    result = await _dispatch_mcp("tools/call", 1, {"name": "call_service", "arguments": {}},
                                 token, hass, data, "127.0.0.1", "")
    assert result[3] == "invalid_request"
    entry = only(data.conversations)
    assert entry["execution_status"] == "unknown"
    assert entry["events"][-1]["outcome"] == "invalid_request"


@pytest.mark.asyncio
async def test_continuation_does_not_mislabel_provider_tool_envelope_as_prompt(conversation_data):
    data, token = conversation_data

    @history.recorded_chat
    async def chat(**kwargs):
        await kwargs["emit"]("assistant_delta", {"text": "Continued response"})

    await chat(data=data, token=token, messages=[{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "provider-id", "content": "provider-only-envelope"},
    ]}], emit=AsyncMock())
    entry = only(data.conversations)
    assert entry["prompt"] is None and entry["coverage"] == "partial_conversation"
    assert "provider-only-envelope" not in json.dumps(entry)
    assert entry["response"] == "Continued response"


@pytest.mark.asyncio
async def test_selected_identity_is_sanitized_without_copying_provider_secrets(conversation_data):
    from custom_components.phoenix_mcp.agentcli import ProviderConfig
    data, token = conversation_data
    cfg = ProviderConfig(kind="openai", model="selected-model", base_url="https://secret-host.invalid",
                         api_key="canary-provider-key")

    @history.recorded_chat
    async def chat(**kwargs):
        history.record_identity(token=SimpleNamespace(id="delegated-id", name="Other"),
                                config=ProviderConfig(kind="other", model="other-model", base_url=""))
        await kwargs["emit"]("assistant_delta", {"text": "Done"})

    await chat(data=data, token=token, provider=SimpleNamespace(cfg=cfg),
               messages=[{"role": "user", "content": "Hello"}], emit=AsyncMock())
    entry = only(data.conversations)
    assert entry["metadata"] == {"token_id": token.id, "token_name": token.name, "provider": "openai", "model": "selected-model"}
    assert "canary-provider-key" not in json.dumps(entry) and "secret-host" not in json.dumps(entry)
    store = AsyncMock()
    store.async_load.return_value = {"entries": [entry]}
    restored = history.ConversationLog(store)
    restored.configure(data.store.get_settings())
    await restored.async_load()
    assert only(restored)["metadata"] == entry["metadata"]
    handle = data.conversations.begin("ai_task")
    ctx = history._current.set(handle)
    try:
        history.record_identity(token=token, config=ProviderConfig(kind="custom", model="password=canary-model", base_url=""))
    finally:
        history._current.reset(ctx)
    assert "canary-model" not in json.dumps(data.conversations.detail(handle[1]))
