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


@pytest.mark.parametrize("value,secret", [
    ({"service": {"domain": "alarm_control_panel", "name": "alarm_disarm", "data": {"code": "4821"}}}, "4821"),
    ({"service_data": {"code": "entry-secret"}}, "entry-secret"),
    ({"pin": "4821", "psk": "4821", "encryption_key": "4821"}, "4821"),
    ('The code is 4821.', "4821"),
    ('service: alarm_control_panel.alarm_disarm\nservice_data:\n  code: "entry-secret"', "entry-secret"),
    ('{"service":{"domain":"alarm_control_panel","data":{"code":"entry-secret"}}}', "entry-secret"),
    ('https://example.test/?psk=entry-secret&code=4821', "entry-secret"),
])
def test_service_credentials_are_redacted_without_losing_error_codes(value, secret):
    assert secret not in json.dumps(history.sanitized(value, max_bytes=10000))
    assert history.sanitized({"code": "cancelled"}, max_bytes=10000) == {"code": "cancelled"}


def test_address_redaction_preserves_durations_and_ordinary_words():
    source = "Spin: 1200 rpm; delay: 00:05:00; at: 07:30:00; MAC aa:bb:cc:dd:ee:ff"
    assert history.sanitized(source, max_bytes=10000, hide_ip=True) == source
    for address in ("192.0.2.1", "2001:db8::1", "::1"):
        assert address not in history.sanitized(f"Address [{address}]", max_bytes=10000, hide_ip=True)


def test_oversize_result_preserves_later_denial_and_reply(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("agentcli", prompt="Change two things")
    for tool_id, outcome, payload in [("one", "allowed", "x" * 2_000_000), ("two", "denied", "No access")]:
        ctx = history._tool.set(tool_id)
        try:
            log.event(handle, "tool_call", {"name": "call_service", "arguments": {}})
            log.event(handle, "tool_result", payload, outcome=outcome)
        finally:
            history._tool.reset(ctx)
    log.finish(handle, response="One change succeeded and one was denied.")
    entry = only(log)
    assert len(entry["events"]) == 4 and entry["events"][-1]["outcome"] == "denied"
    assert entry["execution_status"] == "partial"
    assert entry["response"] == "One change succeeded and one was denied."
    assert entry["events"][1]["omission"] == "size_limit"
    assert log.query(outcome="denied")["total"] == 1


@pytest.mark.asyncio
async def test_event_limit_preserves_late_approval_after_reload(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("agentcli", prompt="Change something")
    approval = SimpleNamespace(id="retained-approval", tool_name="set_state", status="pending", diff=None, result=None, rejected_reason=None)
    context = history._current.set(handle)
    tool = history._tool.set("pending-call")
    try:
        log.event(handle, "tool_call", {"name": "set_state"})
        log.approval(approval)
        log.event(handle, "tool_result", {}, outcome="pending_approval")
        for _ in range(history.CONVERSATION_LOG_MAX_EVENTS + 1):
            log.event(handle, "routing", {"route": "provider"})
    finally:
        history._current.reset(context)
        history._tool.reset(tool)
    log.finish(handle, response="Awaiting approval")
    persisted = AsyncMock()
    persisted.async_load.return_value = {"entries": [only(log)]}
    restored = history.ConversationLog(persisted)
    restored.configure(data.store.get_settings())
    await restored.async_load()
    approval.status = "approved"
    approval.result = {"outcome": "allowed"}
    restored.approval(approval, resolved=True)
    entry = only(restored)
    assert entry["execution_status"] == "allowed"
    assert entry["omitted_events"] > 0
    assert len(entry["events"]) <= history.CONVERSATION_LOG_MAX_EVENTS
    assert restored.query()["bytes"] < restored.entry_budget


def test_content_search_and_multimodal_excerpt(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    prompt = 'Describe this\npicture with "quotes"'
    handle = log.begin("agentcli", prompt=[{"type": "text", "text": prompt}, {"type": "image", "data": "binary"}])
    log.event(handle, "tool_result", "Service failed", outcome="invalid_request")
    log.finish(handle, response="Unable to complete")
    assert log.query()["entries"][0]["excerpt"] == prompt
    assert log.query(search=prompt)["total"] == 1
    assert log.query(search="tool_count")["total"] == 0
    assert log.query(search="coverage")["total"] == 0
    assert log.query(outcome="failed")["total"] == 1
    cached = log._search[handle[1]]
    assert log.query(search=prompt)["total"] == 1
    assert log._search[handle[1]] is cached


@pytest.mark.asyncio
async def test_narration_keeps_its_place_before_tools(conversation_data):
    data, token = conversation_data

    @history.recorded_chat
    async def chat(**kwargs):
        await kwargs["emit"]("assistant_delta", {"text": "Let me check."})
        history.record_event(data, "tool_call", {"name": "get_state"})
        history.record_event(data, "tool_result", "on", outcome="allowed")
        await kwargs["emit"]("assistant_delta", {"text": "The light is on."})

    await chat(data=data, token=token, messages=[{"role": "user", "content": "Is it on?"}], emit=AsyncMock())
    entry = only(data.conversations)
    assert [e["kind"] for e in entry["events"]] == ["assistant_text", "tool_call", "tool_result", "assistant_text"]
    assert entry["events"][0]["payload"] == "Let me check."
    assert entry["response"] == "The light is on."


@pytest.mark.asyncio
async def test_loaded_history_resanitizes_credentials_and_payload_ips(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("voice", prompt="hello")
    log.finish(handle, response="done")
    row = only(log)
    row["prompt"] = "The code is 4821. IP 192.0.2.1. Wait 00:05:00."
    row["events"] = [{"kind": "tool_call", "timestamp": row["started_at"], "tool_id": "one", "payload": {"service_data": {"code": "4821"}}}]
    settings = data.store.get_settings()
    settings.log_client_ip = False
    persisted = AsyncMock()
    persisted.async_load.return_value = {"entries": [row]}
    restored = history.ConversationLog(persisted)
    restored.configure(settings)
    await restored.async_load()
    entry = only(restored)
    assert "4821" not in json.dumps(entry) and "192.0.2.1" not in json.dumps(entry)
    assert "00:05:00" in entry["prompt"]
    assert entry["started_at"] == row["started_at"]


def test_one_entry_does_not_evict_other_history_and_size_accounting_is_exact(conversation_data):
    data, _ = conversation_data
    settings = data.store.get_settings()
    settings.conversation_log_max_mb = 1
    log = history.recorder(data)
    for _ in range(10):
        log.finish(log.begin("voice", prompt="retained"), response="done")
    handle = log.begin("mcp")
    for _ in range(20):
        log.event(handle, "tool_result", "x" * 100000, outcome="allowed")
    assert log.query()["total"] == 11
    entry = log.detail(handle[1])
    assert history.json_size(entry) <= log.entry_budget
    assert log._sizes[handle[1]] == history.json_size(entry)


@pytest.mark.asyncio
async def test_completion_saves_coalesce_but_clear_and_shutdown_flush(conversation_data, monkeypatch):
    data, _ = conversation_data
    persisted = AsyncMock()
    log = history.ConversationLog(persisted)
    log.configure(data.store.get_settings())
    monkeypatch.setattr(history.time, "monotonic", lambda: 1000)
    for _ in range(10):
        log.finish(log.begin("mcp"))
        await log.async_save(coalesced=True)
    assert persisted.async_save.await_count == 1
    await log.async_save()
    assert persisted.async_save.await_count == 2
    await log.async_clear()
    assert persisted.async_save.call_args.args[0] == {"entries": []}


@pytest.mark.asyncio
async def test_unavailable_local_voice_command_records_failure(hass, conversation_data):
    from custom_components.phoenix_mcp.command_execution import _dispatch_command
    from custom_components.phoenix_mcp.routing_statistics import local_result
    data, token = conversation_data
    authority = SimpleNamespace(current=lambda _hass: None)

    @history.recorded_turn("voice", "prompt")
    async def turn(data, prompt):
        reply = await _dispatch_command(hass, data, token, {}, authority, "", False)
        local_result(reply, "local")
        return reply.text

    await turn(data, "Turn on the light")
    entry = only(data.conversations)
    assert entry["status"] == "failed"
    assert entry["events"][0]["payload"]["outcome"] == "unauthorized"
    assert entry["response"]


@pytest.mark.parametrize("snapshot", [
    {"action": "alarm_control_panel.alarm_disarm", "data": {"code": "canary-alarm"}},
    {"service": "lock.unlock", "data": {"code": "canary-alarm"}},
    {"before": {"actions": [{"action": "lock.unlock", "data": {"code": "canary-alarm"}}]}},
])
def test_service_codes_in_configuration_snapshots(snapshot):
    assert "canary-alarm" not in json.dumps(history.sanitized(snapshot, max_bytes=10000))


def test_many_operations_remain_bounded_and_mixed(conversation_data):
    data, _ = conversation_data
    data.store.get_settings().conversation_log_max_mb = 1
    log = history.recorder(data)
    handle = log.begin("agentcli", prompt="p" * 100000)
    for index in range(history.CONVERSATION_LOG_MAX_OPERATIONS + 50):
        ctx = history._tool.set(f"operation-{index}")
        try:
            log.event(handle, "tool_call", {"name": "get_state"})
            log.event(handle, "tool_result", "done", outcome="allowed" if index % 2 else "denied")
        finally:
            history._tool.reset(ctx)
    log.finish(handle, response="r" * 100000)
    entry = only(log)
    assert entry["execution_status"] == "partial"
    assert len(entry["receipts"]) == history.CONVERSATION_LOG_MAX_OPERATIONS
    assert entry["omitted_events"] > 0
    assert log._sizes[handle[1]] == history.json_size(entry) < log.entry_budget


@pytest.mark.asyncio
async def test_sanitized_stream_overflow_cannot_reuse_earlier_narration(conversation_data, monkeypatch):
    data, token = conversation_data
    monkeypatch.setattr(history, "CONVERSATION_LOG_SCAN_BYTES", 500)

    @history.recorded_chat
    async def chat(**kwargs):
        await kwargs["emit"]("assistant_delta", {"text": "Let me check."})
        history.flush_assistant()
        await kwargs["emit"]("assistant_delta", {"text": "x" * 501})

    await chat(data=data, token=token, messages=[{"role": "user", "content": "Hello"}], emit=AsyncMock())
    entry = only(data.conversations)
    assert entry["response"] is None
    assert entry["response_status"] == "size_limit"
    assert entry["field_omissions"]["response"] == "size_limit"


@pytest.mark.parametrize("raw", [
    '{"entity_id":"sensor.battery",}',
    '{"entity_id":"sensor.battery"',
    '{not json',
])
def test_rejected_argument_text_keeps_broken_syntax(raw):
    assert history._scrub_argument_text(raw, hide_ip=False) == raw


@pytest.mark.parametrize("raw", [
    '{"password":"canary secret with spaces',
    '{"pass\\u0077ord":"canary secret",}',
    '{"code":"canary code",}',
    '{"_private":"canary internals",}',
    '{"blob":"canary media",}',
    '{"message":"password is canary",}',
])
def test_rejected_argument_text_scrubs_complete_and_incomplete_secrets(raw):
    clean = history._scrub_argument_text(raw, hide_ip=False)
    assert "canary" not in clean
    assert "<redacted>" in clean


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["chatgpt", "claude"])
@pytest.mark.parametrize("arguments,reason", [
    ('{"entity_id":"sensor.battery","password":"canary secret",}', "not valid JSON"),
    ('[]', "must be a JSON object"),
])
async def test_provider_rejected_calls_reach_history_not_client_diagnostics(conversation_data, kind, arguments, reason):
    from custom_components.phoenix_mcp import agentcli
    data, token = conversation_data
    if kind == "claude":
        frames = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let me check."}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "call-one", "name": "get_state"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": arguments}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        ]
        provider = agentcli.ClaudeProvider(agentcli.ProviderConfig(kind, "model", "https://provider.invalid", "canary-key"))
    else:
        frames = [
            {"choices": [{"delta": {"content": "Let me check.", "reasoning_content": "canary thought"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-one", "function": {"name": "get_state", "arguments": arguments}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        provider = agentcli.OpenAICompatProvider(agentcli.ProviderConfig(kind, "model", "https://provider.invalid", "canary-key"))
    body = "\n\n".join("data: " + json.dumps(frame) for frame in frames).encode()

    async def chunks(_size):
        for offset in range(0, len(body), 19):
            yield body[offset:offset + 19]

    response = MagicMock(status=200, headers={})
    response.content.iter_chunked = chunks
    response.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = response
    emit = AsyncMock()

    @history.recorded_chat
    async def chat(**kwargs):
        return await agentcli._consume_model_stream(provider, session, system="canary system prompt", messages=[],
            tools=[], options={}, cancel=asyncio.Event(), emit=kwargs["emit"],
            usage_done_input=0, usage_done_output=0, context_tokens=0)

    result = await chat(data=data, token=token, messages=[{"role": "user", "content": "Graph the battery"}], emit=emit)
    assert result.errored and not result.tool_calls
    entry = only(data.conversations)
    assert entry["status"] == "failed" and entry["tool_count"] == 0 and entry["execution_status"] == "no_tools"
    rejection = next(e["payload"] for e in entry["events"] if isinstance(e["payload"], dict) and e["payload"].get("code") == "tool_call_rejected")
    assert reason in rejection["message"]
    assert rejection["name"] == "get_state" and rejection["call_id"] == "call-one"
    assert rejection["executed"] is False
    assert rejection["arguments"] == history.sanitized(arguments, max_bytes=10000)
    if reason == "not valid JSON":
        assert rejection["validation"]["line"] == 1 and rejection["validation"]["column"] > 1
    kinds = [e["kind"] for e in entry["events"]]
    assert kinds.index("assistant_text") < kinds.index("error")
    assert next(e["payload"] for e in entry["events"] if e["kind"] == "assistant_text") == "Let me check."
    assert "canary" not in json.dumps(entry)
    assert "arguments" not in json.dumps(emit.call_args_list, default=str)


@pytest.mark.asyncio
async def test_rejected_call_recording_obeys_privacy_limits_and_failures(conversation_data, monkeypatch):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin("agentcli", prompt="test")
    context = history._current.set(handle)
    try:
        data.store.get_settings().log_entity_names = False
        history.record_rejected_tool("id", "get_state", 'secret content', reason="Invalid JSON.")
        assert log.entry(handle)["events"][-1]["payload"] is None
        data.store.get_settings().log_entity_names = True
        history.record_rejected_tool("id", "get_state", '{"entity_id":"\\q"}', reason="Invalid JSON.")
        payload = log.entry(handle)["events"][-1]["payload"]
        assert payload["arguments"] is None and payload["arguments_omission"]
        monkeypatch.setattr(history, "CONVERSATION_LOG_SCAN_BYTES", 50)
        history.record_rejected_tool("id", "get_state", 'x' * 100, reason="Invalid JSON.")
        assert "x" * 100 not in json.dumps(log.entry(handle))
        monkeypatch.setattr(log, "event", MagicMock(side_effect=RuntimeError("canary")))
        history.record_rejected_tool("id", "get_state", '{}', reason="Invalid JSON.")
        await log.async_clear()
        history.record_rejected_tool("id", "get_state", '{}', reason="Invalid JSON.")
        assert not log.query()["entries"]
    finally:
        history._current.reset(context)


def test_explicit_chat_groups_are_oldest_first_and_sessions_stay_separate(conversation_data):
    from uuid import uuid4
    data, token = conversation_data
    log = history.recorder(data)
    chat, other = str(uuid4()), str(uuid4())
    first = log.begin('agentcli', prompt='First request')
    log.bind_chat(first, chat, token, has_history=False)
    log.finish(first, response='First reply')
    standalone = log.begin('mcp')
    log.finish(standalone)
    second = log.begin('agentcli', prompt='Follow-up')
    log.bind_chat(second, chat, token, has_history=True)
    log.finish(second, response='Second reply')
    different = log.begin('agentcli', prompt='Different chat')
    log.bind_chat(different, other, token, has_history=False)
    log.finish(different, response='Other reply')
    rows = log.query(grouped=True)['entries']
    assert [row['id'] for row in rows] == [different[1], first[1], standalone[1]]
    assert [turn['id'] for turn in rows[1]['turns']] == [first[1], second[1]]
    assert 'events' not in rows[1]['turns'][0]
    assert log.query(grouped=True, search='Follow-up')['entries'][0]['turns'] == rows[1]['turns']
    assert log.query(grouped=True, limit=1, offset=1)['entries'][0]['id'] == first[1]
    reset = log.begin('agentcli', prompt='Reset with same client ID')
    log.bind_chat(reset, chat, token, has_history=False)
    assert log.query(grouped=True)['total'] == 4
    log._remove(first[1])
    old = next(row for row in log.query(grouped=True)['entries'] if row['id'] == first[1])
    assert old['history_missing'] and len(old['turns']) == 1


@pytest.mark.asyncio
async def test_chat_grouping_restart_privacy_and_clear(conversation_data):
    from uuid import uuid4
    data, token = conversation_data
    log = history.recorder(data)
    chat = str(uuid4())
    first = log.begin('agentcli', prompt='one')
    log.bind_chat(first, chat, token, has_history=False)
    log.finish(first, response='done')
    saved = deepcopy(list(log._entries.values()))
    log._store = SimpleNamespace(async_load=AsyncMock(return_value={'entries': saved}), async_save=AsyncMock())
    await log.async_load()
    follow = log.begin('agentcli', prompt='two')
    log.bind_chat(follow, chat, token, has_history=True)
    assert log.query(grouped=True)['total'] == 1
    await log.async_clear()
    log.bind_chat(follow, chat, token, has_history=True)
    assert log.query(grouped=True)['total'] == 0
    fresh = log.begin('agentcli', prompt='after clear')
    log.bind_chat(fresh, chat, token, has_history=True)
    assert log.query(grouped=True)['entries'][0]['history_missing']
    assert chat not in json.dumps(log.detail(fresh[1]))
    token.persona = 'home_companion'
    companion = log.begin('agentcli', prompt='stateless')
    log.bind_chat(companion, chat, token, has_history=True)
    assert 'thread_id' not in log.detail(companion[1])


@pytest.mark.asyncio
async def test_routing_receipt_completes_in_place_and_cannot_recreate_cleared_history(conversation_data):
    data, _ = conversation_data
    log = history.recorder(data)
    handle = log.begin('voice', prompt='check')
    ctx = history._current.set(handle)
    try:
        with history.routing_span('decision_provider', provider='test', model='test-model', observation_only=True) as receipt:
            receipt['reason'] = 'password=canary-secret'
        events = log.detail(handle[1])['events']
        assert len(events) == 1 and events[0]['payload']['result'] == 'completed'
        assert events[0]['finished_at']
        assert 'canary-secret' not in json.dumps(events)
        with pytest.raises(asyncio.CancelledError):
            with history.routing_span('provider'):
                raise asyncio.CancelledError
        assert log.detail(handle[1])['events'][-1]['payload']['result'] == 'cancelled'
        with history.routing_span('provider'):
            await log.async_clear()
        assert log.query()['total'] == 0
    finally:
        history._current.reset(ctx)


@pytest.mark.asyncio
async def test_nested_dispatch_records_parent_operation(conversation_data):
    data, token = conversation_data
    @history.recorded_exchange
    async def dispatch(method, rpc_id, params, token, hass, data, client_ip, base_url, request_id=None):
        if params['name'] == 'home_request':
            await dispatch(method, rpc_id, {'name': 'get_state'}, token, hass, data, client_ip, base_url)
        return {}, '', '', 'allowed'
    await dispatch('tools/call', 1, {'name': 'home_request'}, token, None, data, 'mcp', '')
    events = only(data.conversations)['events']
    assert events[0]['parent_tool_id'] is None
    assert events[1]['parent_tool_id'] == events[0]['tool_id']
    assert events[2]['tool_id'] == events[1]['tool_id']
