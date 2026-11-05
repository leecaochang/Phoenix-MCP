"""MCP HTTP ingress through Home Assistant's real server, with bytes a client library would refuse.

A real socket reaches what a mocked request cannot: undecoded header bytes,
body encodings, a client that goes away mid-call, and what the audit store
actually manages to write afterwards.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
import pytest

from custom_components.phoenix_mcp import tool_dispatch
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore, GlobalSettings
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import (
    ACCESS_KEY_LENGTH,
    ACCESS_KEY_PREFIX,
    AUDIT_STORAGE_KEY,
    AUDIT_STORAGE_VERSION,
    DOMAIN,
    MAX_REQUEST_BODY_BYTES,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.access_key_auth import async_get_authenticated_access_key
from custom_components.phoenix_mcp.mcp_transport import ALL_MCP_VIEWS
from custom_components.phoenix_mcp.tool_dispatch import _dispatch_mcp
from custom_components.phoenix_mcp.rate_limiter import RateLimiter
from custom_components.phoenix_mcp.version_store import VersionStore

ROUTE = "/api/phoenix-mcp"
MODERN = "2026-07-28"
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "client", "version": "1"}}}
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


@pytest.fixture
async def env(hass, socket_enabled):
    assert await async_setup_component(hass, "http", {})
    store = await AccessKeyStore.async_create(hass)
    store.get_settings().mesa_mode = "off"
    audit_store: Store[dict] = Store(hass, AUDIT_STORAGE_VERSION, AUDIT_STORAGE_KEY)
    data = PhoenixData(store=store, rate_limiter=RateLimiter(), audit=AuditLog(store=audit_store))
    data.versions = VersionStore()
    hass.data[DOMAIN] = data
    for view_class in ALL_MCP_VIEWS:
        view = view_class()
        view.hass = hass
        hass.http.register_view(view)
    client = TestClient(TestServer(hass.http.app, handler_cancellation=True))
    await client.start_server()
    yield client, data
    await client.close()


def _headers(raw: str, **extra: str) -> dict:
    return {"Authorization": f"Bearer {raw}", "Accept": "application/json", **extra}


async def _key(data: PhoenixData, *, allow_all: bool = False):
    key, raw = await data.store.async_create_access_key("transport", "admin", rate_limit_requests=0, rate_limit_burst=0)
    if allow_all:
        key.pass_through = True
        for field in fields(key):
            if field.name.startswith("cap_"):
                setattr(key, field.name, "allow")
    return key, raw


async def _session(client: TestClient, data: PhoenixData, **key_options) -> tuple:
    key, raw = await _key(data, **key_options)
    response = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    assert response.status == 200, await response.text()
    headers = _headers(raw, **{"Mcp-Session-Id": response.headers["Mcp-Session-Id"]})
    response = await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status == 202
    return key, raw, headers


def _modern(method: str, msg_id: int | None = 1, **params) -> dict:
    body = {"jsonrpc": "2.0", "method": method, "params": {**params, "_meta": {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "client", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }}}
    return body if msg_id is None else {**body, "id": msg_id}


def _modern_headers(raw: str, method: str, name: str | None = None) -> dict:
    headers = _headers(raw, **{"MCP-Protocol-Version": MODERN, "Mcp-Method": method})
    return headers if name is None else {**headers, "Mcp-Name": name}


async def _raw_request(client: TestClient, request: bytes) -> bytes:
    """Send bytes aiohttp's client would refuse to put in a header, and read the reply."""
    reader, writer = await asyncio.open_connection(client.server.host, client.server.port)
    writer.write(request)
    await writer.drain()
    try:
        async with asyncio.timeout(3):
            return await reader.read(4000)
    finally:
        writer.close()


def _post_bytes(client: TestClient, *header_lines: bytes, host: bytes | None = None) -> bytes:
    host = host or f"{client.server.host}:{client.server.port}".encode()
    body = json.dumps(PING).encode()
    lines = [b"POST " + ROUTE.encode() + b" HTTP/1.1", b"Host: " + host, b"Content-Length: %d" % len(body),
             b"Connection: close", *header_lines]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


def _errors(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


# --- message encoding -----------------------------------------------------------


@pytest.mark.parametrize("body", [
    json.dumps(PING).encode("utf-16"),
    json.dumps(PING).encode("utf-16-le"),
    json.dumps(PING).encode("utf-32"),
    b"\xef\xbb\xbf" + json.dumps(PING).encode(),
    b'{"jsonrpc":"2.0","id":1,"method":"pi\xffng"}',
    b'{"jsonrpc":"2.0","id":1,"method":"\xed\xa0\x80"}',
], ids=["utf16", "utf16le", "utf32", "bom", "invalid", "cesu"])
async def test_a_message_that_is_not_utf8_is_a_parse_error(env, body):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, data=body)
    assert response.status == 200
    assert (await response.json())["error"]["code"] == -32700


@pytest.mark.parametrize("text", [r'"x\ud800"', r'"\udc00"'])
@pytest.mark.parametrize("place", ["value", "key", "method"])
async def test_an_escaped_lone_surrogate_is_a_parse_error(env, text, place):
    client, data = env
    _, _, headers = await _session(client, data)
    body = {
        "value": '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"note":%s}}' % text,
        "key": '{"jsonrpc":"2.0","id":1,"method":"ping","params":{%s:1}}' % text,
        "method": '{"jsonrpc":"2.0","id":1,"method":%s}' % text,
    }[place]
    response = await client.post(ROUTE, headers=headers, data=body.encode())
    assert (await response.json())["error"]["code"] == -32700


async def test_a_valid_surrogate_pair_is_ordinary_text(env):
    client, data = env
    _, _, headers = await _session(client, data)
    body = r'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"note":"\ud83d\ude00"}}'
    response = await client.post(ROUTE, headers=headers, data=body.encode())
    assert (await response.json())["result"] == {}


@pytest.mark.parametrize("body,status,code", [
    (b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}', 200, -32700),
    (b'{"jsonrpc":"2.0","id":1e999,"method":"ping"}', 200, -32700),
    (b'{"jsonrpc":"2.0","id":' + b"9" * 5000 + b',"method":"ping"}', 200, -32700),
    (b'{"jsonrpc":"2.0","id":1.5,"method":"ping"}', 200, -32600),
    (b"[" * 500000 + b"]" * 500000, 200, -32700),
], ids=["nan", "exponent-overflow", "digit-limit", "float-id", "depth"])
async def test_the_number_and_depth_rules_survive_strict_decoding(env, body, status, code):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, data=body)
    assert response.status == status
    assert (await response.json())["error"]["code"] == code


async def test_a_large_integer_id_stays_an_integer(env):
    """orjson would turn this into a float, which is why the standard parser stays."""
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, data=b'{"jsonrpc":"2.0","id":' + b"9" * 30 + b',"method":"ping"}')
    assert (await response.json())["id"] == int("9" * 30)


async def test_a_lone_surrogate_written_through_json_text_reaches_no_registry(env, hass):
    """A string decoded from JSON text passes the same admission as the outer body."""
    client, data = env
    _, _, headers = await _session(client, data, allow_all=True)
    registry = lr.async_get(hass)
    before = len(registry.async_list_labels())
    arguments = {"kind": "label", "operation": "create", "changes": r'{"name": "evil\ud800"}'}
    response = await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
        "name": "set_label_or_category", "arguments": arguments}})
    assert (await response.json())["result"]["isError"] is True
    assert len(registry.async_list_labels()) == before


# --- header bytes ---------------------------------------------------------------


async def test_an_invalid_session_header_is_a_plain_refusal(env, caplog):
    client, _ = env
    reply = await _raw_request(client, _post_bytes(client, b"Mcp-Session-Id: \xff\xfe"))
    assert reply.startswith(b"HTTP/1.1 401")
    assert not _errors(caplog)


async def test_invalid_bearer_bytes_are_refused_without_hashing(env):
    """Through the real server Core's own auth middleware sees this header first."""
    client, data = env
    request = MagicMock()
    request.headers = {"Authorization": "Bearer phx_" + "\udcff" * (ACCESS_KEY_LENGTH - len(ACCESS_KEY_PREFIX))}
    response = await async_get_authenticated_access_key(client.app, request, data, "rid", ROUTE)
    assert response.status == 401


async def test_invalid_header_bytes_get_the_uniform_answers_when_unavailable(env, hass, caplog):
    client, data = env
    data.store.get_settings().kill_switch = True
    reply = await _raw_request(client, _post_bytes(client, b"Mcp-Session-Id: \xff"))
    assert reply.startswith(b"HTTP/1.1 503")
    data.store.get_settings().kill_switch = False
    hass.data.pop(DOMAIN)
    reply = await _raw_request(client, _post_bytes(client, b"Mcp-Session-Id: \xff"))
    assert reply.startswith(b"HTTP/1.1 503")
    hass.data[DOMAIN] = data
    assert not _errors(caplog)


async def test_a_host_that_forms_no_url_is_refused_for_a_valid_key(env, caplog):
    client, data = env
    _, _, headers = await _session(client, data)
    reply = await _raw_request(client, _post_bytes(
        client, b"Authorization: " + headers["Authorization"].encode(),
        b"Mcp-Session-Id: " + headers["Mcp-Session-Id"].encode(), host=b"a b["))
    assert reply.startswith(b"HTTP/1.1 400")
    assert not _errors(caplog)


# --- audit persistence ----------------------------------------------------------


async def test_one_hostile_request_cannot_stop_the_audit_reaching_disk(env, hass_storage, caplog):
    """A row that cannot be serialized fails the whole save, and Store only logs it."""
    client, data = env
    data.store.get_settings().log_denied = True
    _, _, headers = await _session(client, data)
    data.audit.record(request_id="r\ud800", access_key_id="k\ud800", access_key_name="n\ud800", method="\ud800",
                      resource="\ud800", outcome="denied", client_ip="10.0.0.1\ud800", preset="p\ud800",
                      originating_access_key_id="o\ud800", ha_user_id="u\ud800",
                      settings=data.store.get_settings(), payload={"\ud800": "\ud800"})
    for body in (r'{"jsonrpc":"2.0","id":1,"method":"\ud800"}', r'{"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"\ud800"}}'):
        await client.post(ROUTE, headers=headers, data=body.encode())
    caplog.clear()
    await data.audit.async_save()
    assert not _errors(caplog)
    rows = hass_storage[AUDIT_STORAGE_KEY]["data"]["entries"]
    hostile = next(row for row in rows if row["client_ip"].startswith("10.0.0.1"))
    assert hostile["method"] == "\\ud800" and hostile["client_ip"] == "10.0.0.1\\ud800"


# --- legacy sessions and refused messages ---------------------------------------


@pytest.mark.parametrize("body", [
    json.dumps(PING).encode(),
    b'{"jsonrpc":"2.0","id":1,"result":{}}',
    b'{"jsonrpc":"2.0","id":1,"method":"ping","params":3}',
    b'{"jsonrpc":"2.0","method":"x"}',
    b'{"id":1}',
    b"{",
    b"",
    b"1",
], ids=["request", "response", "bad-params", "notification", "not-jsonrpc", "invalid-json", "empty", "scalar"])
async def test_a_terminated_session_is_404_for_every_message_shape(env, body):
    client, data = env
    _, _, headers = await _session(client, data)
    assert (await client.delete(ROUTE, headers=headers)).status == 204
    response = await client.post(ROUTE, headers=headers, data=body)
    assert response.status == 404


async def test_a_modern_request_ignores_a_stale_session_header(env):
    client, data = env
    _, raw = await _key(data)
    headers = {**_modern_headers(raw, "tools/list"), "Mcp-Session-Id": "gone"}
    response = await client.post(ROUTE, headers=headers, json=_modern("tools/list"))
    assert response.status == 200


@pytest.mark.parametrize("body,code", [
    ({"jsonrpc": "2.0", "method": "x", "params": 3}, -32602),
    ({"jsonrpc": "2.0", "method": 5}, -32600),
    ({"method": "x"}, -32600),
])
async def test_a_refused_message_with_no_id_gets_an_http_error(env, body, code):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, json=body)
    assert response.status == 400
    assert (await response.json())["error"]["code"] == code


async def test_a_malformed_request_with_an_id_keeps_its_json_rpc_status(env):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "id": 4, "method": "ping", "params": 3})
    assert response.status == 200
    assert (await response.json())["id"] == 4


# --- modern error shapes --------------------------------------------------------


@pytest.mark.parametrize("name,body,headers", [
    ("parse", b"{", {}),
    ("not-object", b"1", {}),
    ("batch", json.dumps([_modern("tools/list")]).encode(), {"Mcp-Method": "tools/list"}),
    ("empty-batch", b"[]", {}),
    ("null-id", json.dumps(_modern("tools/list", msg_id=None) | {"id": None}).encode(), {"Mcp-Method": "tools/list"}),
    ("no-id-notification", b'{"jsonrpc":"2.0","method":5}', {}),
    ("response-object", b'{"jsonrpc":"2.0","result":{}}', {}),
])
async def test_a_modern_error_with_no_usable_id_leaves_id_out(env, name, body, headers):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw, **{"MCP-Protocol-Version": MODERN, **headers}), data=body)
    payload = await response.json()
    assert "error" in payload, name
    assert "id" not in payload, name


async def test_a_legacy_error_with_no_usable_id_keeps_id_null(env):
    client, data = env
    _, _, headers = await _session(client, data)
    assert (await (await client.post(ROUTE, headers=headers, data=b"{")).json())["id"] is None


async def test_a_modern_request_missing_its_body_version_is_invalid_params(env):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_modern_headers(raw, "tools/list"), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert response.status == 400
    assert (await response.json())["error"]["code"] == -32602


async def test_a_modern_request_whose_versions_differ_is_still_a_mismatch(env):
    client, data = env
    _, raw = await _key(data)
    headers = {**_modern_headers(raw, "tools/list"), "MCP-Protocol-Version": "2025-03-26"}
    response = await client.post(ROUTE, headers=headers, json=_modern("tools/list"))
    assert (await response.json())["error"]["code"] == -32020


# --- disconnects ----------------------------------------------------------------


async def _light(hass, handler) -> str:
    hass.services.async_register("light", "turn_on", handler)
    entity = er.async_get(hass).async_get_or_create("light", "transport", "kitchen", suggested_object_id="kitchen")
    hass.states.async_set(entity.entity_id, "off")
    return entity.entity_id


def _service_call(entity_id: str) -> dict:
    return {"service": {"domain": "light", "name": "turn_on", "data": {}},
            "targets": [{"kind": "entity", "ids": [entity_id]}]}


async def _disconnect_mid_call(client: TestClient, headers: dict, body: dict, started: asyncio.Event) -> None:
    payload = json.dumps(body).encode()
    lines = [b"POST " + ROUTE.encode() + b" HTTP/1.1", b"Host: x", b"Content-Length: %d" % len(payload)]
    lines += [f"{name}: {value}".encode() for name, value in headers.items()]
    reader, writer = await asyncio.open_connection(client.server.host, client.server.port)
    writer.write(b"\r\n".join(lines) + b"\r\n\r\n" + payload)
    await writer.drain()
    async with asyncio.timeout(3):
        await started.wait()
    writer.transport.abort()


@pytest.mark.parametrize("sse", [False, True])
async def test_a_modern_disconnect_during_a_write_leaves_its_record(env, hass, sse):
    """The write may have run, so the audit says so and the key's next write waits for review."""
    client, data = env
    _, raw = await _key(data, allow_all=True)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(_call):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    entity_id = await _light(hass, slow)
    arguments = _service_call(entity_id)
    headers = _modern_headers(raw, "tools/call", "call_service")
    headers["Accept"] = "application/json, text/event-stream" if sse else "application/json"
    await _disconnect_mid_call(client, headers, _modern("tools/call", name="call_service", arguments=arguments), started)
    async with asyncio.timeout(3):
        await cancelled.wait()
    await hass.async_block_till_done()

    row = next(row for row in data.audit.query() if row.method == "request/cancelled")
    assert row.resource == "call_service" and row.outcome == "invalid_request"
    assert json.loads(row.payload)["arguments"]["targets"][0]["ids"] == [entity_id]
    key = data.store.get_access_key_by_hash(row.access_key_id) or data.store.get_access_key_by_id(row.access_key_id)
    assert [record["status"] for record in data.agent_recovery.records(key.id)] == ["outcome_unknown"]

    response = await client.post(ROUTE, headers=_modern_headers(raw, "tools/call", "call_service"),
                                 json=_modern("tools/call", name="call_service", arguments=arguments))
    assert "unresolved outcome" in json.dumps(await response.json())


async def test_a_modern_disconnect_during_a_read_claims_nothing(env, hass):
    client, data = env
    key, raw = await _key(data, allow_all=True)
    started = asyncio.Event()

    async def slow_read(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)

    with patch.object(tool_dispatch, "_dispatch_tools_call", slow_read):
        await _disconnect_mid_call(client, _modern_headers(raw, "tools/call", "get_state"),
                                   _modern("tools/call", name="get_state", arguments={"entity_id": "light.x"}), started)
        await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert data.agent_recovery.records(key.id) == []
    assert not any(row.method == "request/cancelled" for row in data.audit.query())


async def test_a_legacy_disconnect_still_finishes_and_audits_the_write(env, hass):
    client, data = env
    _, _, headers = await _session(client, data, allow_all=True)
    started, finish = asyncio.Event(), asyncio.Event()

    async def held(_call):
        started.set()
        await finish.wait()

    entity_id = await _light(hass, held)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "call_service", "arguments": _service_call(entity_id)}}
    await _disconnect_mid_call(client, headers, body, started)
    await asyncio.sleep(0.05)
    finish.set()
    await hass.async_block_till_done()
    assert next(row for row in data.audit.query() if row.method == "call_service").outcome == "allowed"
    assert not any(row.method == "request/cancelled" for row in data.audit.query())


async def test_a_disconnect_while_an_approval_waits_leaves_the_key_free(env, hass):
    """The queue owns that outcome, so a client timeout must not stop the key's other writes."""
    client, data = env
    key, raw = await _key(data, allow_all=True)
    key.cap_physical_control = "confirm"
    key.confirm_inline_wait_seconds = 60
    hass.services.async_register("lock", "unlock", lambda _call: None)
    entity_id = er.async_get(hass).async_get_or_create("lock", "transport", "door", suggested_object_id="door").entity_id
    hass.states.async_set(entity_id, "locked")
    created = asyncio.Event()
    hass.bus.async_listen(f"{DOMAIN}_approval_requested", callback(lambda _event: created.set()))
    arguments = {"service": {"domain": "lock", "name": "unlock", "data": {}},
                 "targets": [{"kind": "entity", "ids": [entity_id]}]}
    await _disconnect_mid_call(client, _modern_headers(raw, "tools/call", "call_service"),
                               _modern("tools/call", name="call_service", arguments=arguments), created)
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert data.agent_recovery.records(key.id) == []
    assert [row.outcome for row in data.audit.query() if row.method == "call_service"] == ["pending_approval"]
    assert [approval["status"] for approval in data.store.get_pending_approvals()] == ["pending"]


async def test_an_interrupted_write_blocks_before_its_save_finishes(env, hass):
    """The barrier takes effect in the cancelled task, whatever the recovery lock is doing."""
    _, data = env
    key, _ = await _key(data, allow_all=True)
    started = asyncio.Event()

    async def slow(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)

    save = AsyncMock()
    with patch.object(data.agent_recovery, "_save", save):
        async with data.agent_recovery._lock:
            with patch.object(tool_dispatch, "_call_tool", slow):
                task = asyncio.create_task(_dispatch_mcp(
                    "tools/call", 1, {"name": "call_service", "arguments": {}}, key, hass, data, "10.0.0.5", "http://x"))
                await started.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert data.agent_recovery.blocks(key.id)
            save.assert_not_awaited()
        await hass.async_block_till_done()
    assert data.agent_recovery.blocks(key.id)
    save.assert_awaited_once()


async def test_a_failed_barrier_save_keeps_the_barrier(env, hass, caplog):
    _, data = env
    key, _ = await _key(data, allow_all=True)
    started = asyncio.Event()

    async def slow(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)

    async def failing_save() -> None:
        raise OSError("disk")

    with patch.object(tool_dispatch, "_call_tool", slow), patch.object(data.agent_recovery, "_save", failing_save):
        task = asyncio.create_task(_dispatch_mcp(
            "tools/call", 1, {"name": "call_service", "arguments": {}}, key, hass, data, "10.0.0.5", "http://x"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await hass.async_block_till_done()
    assert data.agent_recovery.blocks(key.id)


async def test_a_legacy_cancellation_notification_is_accepted_and_changes_nothing(env, hass):
    """A receiver may ignore it, and the transport contract says this one does."""
    client, data = env
    _, _, headers = await _session(client, data, allow_all=True)
    started, finish = asyncio.Event(), asyncio.Event()

    async def held(_call):
        started.set()
        await finish.wait()

    entity_id = await _light(hass, held)
    call = asyncio.create_task(client.post(ROUTE, headers=headers, json={
        "jsonrpc": "2.0", "id": 77, "method": "tools/call",
        "params": {"name": "call_service", "arguments": _service_call(entity_id)}}))
    async with asyncio.timeout(3):
        await started.wait()
    response = await client.post(ROUTE, headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 77}})
    assert response.status == 202
    finish.set()
    payload = await (await call).json()
    assert "isError" not in payload["result"]


# --- sanitizer cost -------------------------------------------------------------


async def test_a_refused_call_with_a_huge_multiline_argument_does_not_stall_the_loop(env, hass):
    """The audit row is written for a refused call too, with text the client chose.

    The bound is generous for a slow machine: quadratic sanitizing takes close to
    a minute on this input.
    """
    _, data = env
    key, _ = await _key(data)
    data.store.get_settings = lambda: GlobalSettings()
    argument = "k:\n" * ((MAX_REQUEST_BODY_BYTES - 200) // 3)
    assert len(argument) > 1_000_000

    gaps: list[float] = []

    async def ticker() -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    watch = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    response, _, _, outcome = await _dispatch_mcp(
        "tools/call", 1, {"name": "get_state", "arguments": {"entity_id": argument}}, key, hass, data, "10.0.0.5", "http://x")
    await asyncio.sleep(0.02)
    watch.cancel()
    assert outcome != "allowed"
    assert response is not None
    assert max(gaps) < 3.0
    rows = data.audit.query()
    assert rows and len(rows[0].payload) < 2200
