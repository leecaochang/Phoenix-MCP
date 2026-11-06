"""MCP HTTP ingress through Home Assistant's real server, with bytes a client library would refuse.

A real socket reaches what a mocked request cannot: undecoded header bytes,
body encodings, a client that goes away mid-call, and what the audit store
actually manages to write afterwards.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields, replace
from functools import partial
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

from custom_components.phoenix_mcp import mcp_transport, tool_dispatch
from custom_components.phoenix_mcp.access_key_store import AccessKeyStore, GlobalSettings
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import (
    ACCESS_KEY_LENGTH,
    ACCESS_KEY_PREFIX,
    AUDIT_STORAGE_KEY,
    AUDIT_STORAGE_VERSION,
    DOMAIN,
    MAX_BATCH_ITEMS,
    MAX_REQUEST_BODY_BYTES,
    MCP_AUTHORITY_CHANGED_ERROR,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.access_key_auth import async_get_authenticated_access_key
from custom_components.phoenix_mcp.mcp_transport import ALL_MCP_VIEWS
from custom_components.phoenix_mcp.request_authority import AuthorityChangedError
from custom_components.phoenix_mcp.tool_common import _progress_ctx
from custom_components.phoenix_mcp.tool_defs import _NO_HOME_EFFECT_TOOLS, _TOOL_ANNOTATIONS
from custom_components.phoenix_mcp.tool_dispatch import _TOOL_DEFS_BY_NAME, _dispatch_mcp
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
    assert response.status == 400
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
    (b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}', 400, -32700),
    (b'{"jsonrpc":"2.0","id":1e999,"method":"ping"}', 400, -32700),
    (b'{"jsonrpc":"2.0","id":' + b"9" * 5000 + b',"method":"ping"}', 400, -32700),
    (b'{"jsonrpc":"2.0","id":1.5,"method":"ping"}', 200, -32600),
    (b"[" * 500000 + b"]" * 500000, 400, -32700),
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
    b"[]",
    json.dumps([INITIALIZE]).encode(),
    json.dumps([PING]).encode(),
    b'[{"jsonrpc":"2.0","method":5}]',
], ids=["request", "response", "bad-params", "notification", "not-jsonrpc", "invalid-json", "empty", "scalar",
        "empty-batch", "initialize-batch", "request-batch", "refused-notification-batch"])
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


@pytest.mark.parametrize("accept", ["application/json", "application/json, text/event-stream"])
@pytest.mark.parametrize("body,code", [
    ([], -32600),
    ([{"jsonrpc": "2.0", "method": 5}], -32600),
    ([{"jsonrpc": "2.0", "method": "x", "params": 3}], -32602),
    ([{"method": "x"}, 7], -32600),
    ([{"jsonrpc": "2.0", "id": 1, "result": {}}, {"jsonrpc": "2.0", "method": 5}], -32600),
    ([{"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "method": 5}], -32600),
], ids=["empty", "bad-method", "bad-params", "not-messages", "with-a-response", "with-an-accepted-notification"])
async def test_a_batch_with_no_request_that_was_refused_gets_an_http_error(env, body, code, accept):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers={**headers, "Accept": accept}, json=body)
    assert response.status == 400
    assert response.content_type == "application/json"
    answer = await response.json()
    assert (answer[0] if isinstance(answer, list) else answer)["error"]["code"] == code


async def test_a_batch_with_a_request_keeps_its_results_status(env):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, json=[PING, {"jsonrpc": "2.0", "method": 5}])
    assert response.status == 200
    assert [row.get("id") for row in await response.json()] == [1, None]


async def test_a_batch_of_accepted_notifications_is_still_202(env):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, json=[
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 1, "result": {}}])
    assert response.status == 202


async def test_initialize_in_a_batch_on_a_live_session_is_still_refused_as_such(env):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, json=[INITIALIZE])
    assert response.status == 400
    assert "must not be sent in a batch" in (await response.json())["error"]["message"]


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "modern"])
@pytest.mark.parametrize("body,code", [
    (b"", -32700),
    (b"\xef\xbb\xbf{}", -32700),
    (b"{", -32700),
    (b'{"x":NaN}', -32700),
    (b"1", -32600),
    (b'"text"', -32600),
    (b"null", -32600),
], ids=["empty", "bom", "invalid-json", "nan", "scalar", "string", "null"])
async def test_a_body_that_is_no_message_gets_an_http_error_and_its_json_rpc_body(env, body, code, modern):
    client, data = env
    if modern:
        _, raw = await _key(data)
        headers = _modern_headers(raw, "tools/list")
    else:
        _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, data=body)
    assert response.status == 400
    answer = await response.json()
    assert answer["error"]["code"] == code
    assert ("id" in answer) is not modern


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
    assert row.resource == "call_service" and row.outcome == "unknown"
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


def _cancelled_rows(data: PhoenixData) -> list[str]:
    return [row.outcome for row in data.audit.query() if row.method == "request/cancelled"]


def _required_arguments(name: str) -> dict:
    schema = _TOOL_DEFS_BY_NAME[name]["inputSchema"]
    return {field: "x.yaml" for field in schema.get("required", [])}


async def _held_handler(started: asyncio.Event, *_args, **_kwargs) -> None:
    started.set()
    await asyncio.sleep(30)


async def _cancel_stub_tool(hass, data, key, name: str) -> None:
    """Enter a real dispatch of ``name`` on a held handler, then cancel the task."""
    started = asyncio.Event()
    entry = (partial(_held_handler, started), (), {})
    with patch.dict(tool_dispatch._TOOL_HANDLERS, {name: entry}):
        task = asyncio.create_task(_dispatch_mcp(
            "tools/call", 1, {"name": name, "arguments": _required_arguments(name)}, key, hass, data, "10.0.0.5", "http://x"))
        async with asyncio.timeout(3):
            await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await hass.async_block_till_done()


@pytest.mark.parametrize("log_denied", [True, False])
async def test_a_disconnect_before_the_service_ran_claims_nothing(env, hass, log_denied):
    """The tracked handler proves nothing ran, so the key stays free and the row says so."""
    client, data = env
    data.store.get_settings().log_denied = log_denied
    key, raw = await _key(data, allow_all=True)
    started = asyncio.Event()
    calls: list = []

    async def service(call):
        calls.append(call)

    entity_id = await _light(hass, service)
    arguments = _service_call(entity_id)
    with (patch("custom_components.phoenix_mcp.tools.service.async_apply_mesa_to_call", partial(_held_handler, started)),
          patch.object(tool_dispatch, "record_event") as record):
        await _disconnect_mid_call(client, _modern_headers(raw, "tools/call", "call_service"),
                                   _modern("tools/call", name="call_service", arguments=arguments), started)
        async with asyncio.timeout(3):
            while not _cancelled_rows(data):
                await asyncio.sleep(0.01)
    await hass.async_block_till_done()
    assert calls == []
    assert _cancelled_rows(data) == ["cancelled"]
    assert data.agent_recovery.records(key.id) == []
    assert not any(call.args[1] == "notice" for call in record.call_args_list)

    response = await client.post(ROUTE, headers=_modern_headers(raw, "tools/call", "call_service"),
                                 json=_modern("tools/call", name="call_service", arguments=arguments))
    assert "unresolved outcome" not in json.dumps(await response.json())
    assert len(calls) == 1


@pytest.mark.parametrize("name", sorted(_NO_HOME_EFFECT_TOOLS))
async def test_a_tool_that_cannot_change_the_home_leaves_no_claim(env, hass, name):
    _, data = env
    key, _ = await _key(data, allow_all=True)
    await _cancel_stub_tool(hass, data, key, name)
    assert _cancelled_rows(data) == ["cancelled"]
    assert data.agent_recovery.records(key.id) == []


@pytest.mark.parametrize("name", ["create_script", "clean_esphome_build", "send_notification"])
@pytest.mark.parametrize("log_denied", [True, False])
async def test_a_write_with_no_evidence_keeps_its_claim(env, hass, name, log_denied):
    """An uninstrumented handler counts as started once entered, whatever it did."""
    _, data = env
    data.store.get_settings().log_denied = log_denied
    key, _ = await _key(data, allow_all=True)
    with patch.object(tool_dispatch, "record_event") as record:
        await _cancel_stub_tool(hass, data, key, name)
    assert _cancelled_rows(data) == ["unknown"]
    assert [row["status"] for row in data.agent_recovery.records(key.id)] == ["outcome_unknown"]
    assert data.agent_recovery.blocks(key.id)
    notices = [call for call in record.call_args_list if call.args[1] == "notice"]
    assert [(call.args[2], call.kwargs) for call in notices] == [({"reason": "execution_unknown"}, {"outcome": "unknown"})]


def test_a_tool_listed_as_having_no_home_effect_is_a_real_non_destructive_write():
    for name in _NO_HOME_EFFECT_TOOLS:
        assert _TOOL_ANNOTATIONS[name]["readOnlyHint"] is False
        assert _TOOL_ANNOTATIONS[name]["destructiveHint"] is False


@pytest.mark.parametrize("name,claims,outcome", [
    ("create_script", True, "unknown"),
    ("analyze_esphome_memory", False, "denied"),
])
async def test_authority_lost_during_a_tool_answers_by_what_the_tool_can_do(env, hass, name, claims, outcome):
    _, data = env
    data.store.get_settings().log_denied = False
    data.store.get_settings().log_allowed = False
    key, _ = await _key(data, allow_all=True)

    async def lost(*_args, **_kwargs):
        raise AuthorityChangedError("gone")

    with patch.dict(tool_dispatch._TOOL_HANDLERS, {name: (lost, (), {})}):
        response, _method, _resource, _outcome = await _dispatch_mcp(
            "tools/call", 1, {"name": name, "arguments": _required_arguments(name)}, key, hass, data, "10.0.0.5", "http://x")
    assert data.agent_recovery.blocks(key.id) is claims
    rows = [row.outcome for row in data.audit.query() if row.method == "authority/changed"]
    assert rows == (["unknown"] if claims else [])
    assert ("may already have executed" in json.dumps(response)) is claims


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


# --- refusals before dispatch ---------------------------------------------------


def _audit_rows(data: PhoenixData) -> list[tuple]:
    return [(row.method, row.resource, row.outcome) for row in data.audit.query()]


async def test_a_body_that_cannot_be_read_is_refused_for_the_authenticated_key(env):
    client, data = env
    _, raw = await _key(data)
    head = b"\r\n".join([
        b"POST " + ROUTE.encode() + b" HTTP/1.1",
        f"Host: {client.server.host}:{client.server.port}".encode(),
        b"Authorization: Bearer " + raw.encode(), b"Connection: close",
        b"Content-Encoding: deflate", b"Content-Length: 12"])
    reply = await _raw_request(client, head + b"\r\n\r\nnot deflate!")
    assert reply.startswith(b"HTTP/1.1 400 "), reply
    assert b"Failed to read request body." in reply
    await asyncio.sleep(0)
    assert _audit_rows(data) == [("POST", ROUTE, "invalid_request")]


@pytest.mark.parametrize("item", [
    PING,
    {"jsonrpc": "2.0", "id": 1, "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": [1]},
    {"jsonrpc": "1.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "method": "notifications/cancelled"},
    "not a message",
], ids=["request", "with-id", "positional-params", "wrong-version", "other-notification", "scalar"])
async def test_a_batch_cannot_complete_initialization_with_anything_but_the_notification(env, item):
    client, data = env
    key, raw = await _key(data)
    opened = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    session_id = opened.headers["Mcp-Session-Id"]
    response = await client.post(ROUTE, headers=_headers(raw, **{"Mcp-Session-Id": session_id}), json=[item, PING])
    assert response.status == 400
    assert (await response.json())["error"]["message"] == "Legacy MCP initialization is not complete."
    assert data.mcp_sessions[session_id]["initialized"] is False
    assert _audit_rows(data)[0] == ("POST", "mcp:batch", "invalid_request")
    assert key.id == data.audit.query()[0].access_key_id


@pytest.mark.parametrize("params,detail", [
    ({"capabilities": {}, "clientInfo": {"name": "c", "version": "1"}}, "protocolVersion is required"),
    ({"protocolVersion": "2025-03-26", "clientInfo": {"name": "c", "version": "1"}}, "capabilities are required"),
    ({"protocolVersion": "2025-03-26", "capabilities": {}}, "clientInfo is required"),
    ({"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": 1, "version": "1"}}, "clientInfo is required"),
], ids=["version", "capabilities", "client-info", "client-name"])
async def test_a_malformed_legacy_initialize_opens_no_session(env, params, detail):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw), json={"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": params})
    assert response.status == 400
    assert "Mcp-Session-Id" not in response.headers
    reply = await response.json()
    assert reply["id"] == 7
    assert (reply["error"]["code"], detail in reply["error"]["message"]) == (-32602, True)
    assert not data.mcp_sessions
    assert _audit_rows(data) == [("initialize", ROUTE, "invalid_request")]


def _refusal_chain(data, key) -> list:
    """The door's refusals in contract order: a name, how to provoke it, and its answer."""

    def origin(request):
        request["lines"].append(b"Origin: http://evil.example")

    def bearer(request):
        request["auth"] = False

    def limit(_request):
        key.rate_limit_requests = 1
        data.rate_limiter.check(key.id, 1, 0)

    def oversize(request):
        request["length"] = MAX_REQUEST_BODY_BYTES + 1

    def lose_authority(request):
        request["lose"] = True

    def unparseable(request):
        request["body"] = b"{"

    def host(request):
        request["host"] = b"a b["

    def dead_session(request):
        request["lines"].append(b"Mcp-Session-Id: gone")

    return [
        ("shutting-down", lambda _r: setattr(data, "shutting_down", True), 503, b"Service unavailable."),
        ("origin", origin, 403, b"Origin not allowed."),
        ("kill-switch", lambda _r: setattr(data.store.get_settings(), "kill_switch", True), 503, b"Service unavailable."),
        ("bearer", bearer, 401, b"Unauthorized."),
        ("rate-limit", limit, 429, b"Rate limit exceeded."),
        ("body-size", oversize, 413, b"Request body too large."),
        ("authority", lose_authority, 401, b"Unauthorized."),
        # A dead session answers an unreadable body itself, so this step is held to that.
        ("parse", unparseable, 404, b"Legacy MCP session required."),
        ("host", host, 400, b"Invalid Host header."),
        ("dead-session", dead_session, 404, b"Legacy MCP session required."),
    ]


@pytest.mark.parametrize("start", range(10), ids=[
    "shutting-down", "origin", "kill-switch", "bearer", "rate-limit", "body-size", "authority", "parse", "host", "dead-session"])
async def test_the_first_refusal_in_the_contract_order_wins(env, hass, start):
    """Every later refusal holds at once, so a dropped or swapped step answers differently."""
    client, data = env
    key, raw = await _key(data)
    chain = _refusal_chain(data, key)
    request = {"lines": [], "auth": True, "body": json.dumps(PING).encode(), "length": None, "lose": False,
               "host": f"{client.server.host}:{client.server.port}".encode()}
    for _name, apply, _status, _marker in chain[start:]:
        apply(request)
    _name, _apply, status, marker = chain[start]
    head = [b"POST " + ROUTE.encode() + b" HTTP/1.1", b"Host: " + request["host"], b"Connection: close",
            b"Content-Length: %d" % (request["length"] or len(request["body"])), *request["lines"]]
    if request["auth"]:
        head.append(b"Authorization: Bearer " + raw.encode())

    async def authenticate(*args):
        result = await async_get_authenticated_access_key(*args)
        if request["lose"]:
            key.revoked = True
        return result

    with patch.object(mcp_transport, "_async_get_authenticated_access_key", authenticate):
        reply = await _raw_request(client, b"\r\n".join(head) + b"\r\n\r\n" + request["body"])
    assert reply.startswith(b"HTTP/1.1 %d " % status), reply
    assert marker in reply, reply


async def test_a_parse_error_answers_before_a_host_that_forms_no_url(env):
    client, data = env
    _, raw = await _key(data)
    reply = await _raw_request(client, b"\r\n".join([
        b"POST " + ROUTE.encode() + b" HTTP/1.1", b"Host: a b[", b"Connection: close", b"Content-Length: 1",
        b"Authorization: Bearer " + raw.encode()]) + b"\r\n\r\n{")
    assert reply.startswith(b"HTTP/1.1 400 ") and b"Parse error." in reply


@pytest.mark.parametrize("dead", [False, True], ids=["no-session", "dead-session"])
async def test_a_dead_session_answers_an_unreadable_message_before_its_parse_error(env, dead):
    client, data = env
    _, raw = await _key(data)
    headers = _headers(raw, **({"Mcp-Session-Id": "gone"} if dead else {}))
    response = await client.post(ROUTE, headers=headers, data=b"{")
    assert response.status == (404 if dead else 400)
    code = (await response.json())["error"]["code"]
    assert code == (-32000 if dead else -32700)


@pytest.mark.parametrize("body,status,name", [
    (b"1", 400, "renamed"),
    (b"{", 400, "renamed"),
    (json.dumps([]).encode(), 400, "renamed"),
    (json.dumps({"jsonrpc": "1.0", "id": 1, "method": "ping"}).encode(), 200, "renamed"),
    (b" " * (MAX_REQUEST_BODY_BYTES + 1), 413, "transport"),
], ids=["scalar", "parse-error", "batch", "envelope", "oversized"])
async def test_a_refusal_audits_the_record_the_request_holds_at_that_moment(env, body, status, name):
    """Authority is read again after the body, so later refusals use that record and earlier ones do not."""
    client, data = env
    key, raw = await _key(data)
    replaced = replace(key, name="renamed")

    async def authenticate(*args):
        result = await async_get_authenticated_access_key(*args)
        data.store._access_keys[key.id] = replaced
        return result

    with patch.object(mcp_transport, "_async_get_authenticated_access_key", authenticate):
        response = await client.post(ROUTE, headers=_headers(raw), data=body)
    assert response.status == status
    assert data.audit.query()[0].access_key_name == name


async def test_a_host_that_forms_no_url_is_audited_for_the_key(env):
    client, data = env
    _, raw = await _key(data)
    reply = await _raw_request(client, _post_bytes(client, b"Authorization: Bearer " + raw.encode(), host=b"a b["))
    assert reply.startswith(b"HTTP/1.1 400 ") and b"Invalid Host header." in reply
    assert _audit_rows(data) == [("POST", ROUTE, "invalid_request")]


async def test_an_empty_body_is_a_parse_error_without_the_invalid_json_warning(env, caplog):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw), data=b"")
    assert response.status == 400
    assert (await response.json())["error"]["code"] == -32700
    assert not any("invalid JSON body" in record.getMessage() for record in caplog.records)


async def test_rate_limit_headers_report_the_record_read_after_the_body(env):
    client, data = env
    key, raw = await _key(data)
    key.rate_limit_requests = 50
    replaced = replace(key, rate_limit_requests=70)

    async def authenticate(*args):
        result = await async_get_authenticated_access_key(*args)
        data.store._access_keys[key.id] = replaced
        return result

    opened = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    assert opened.headers["X-RateLimit-Limit"] == "50"
    session = {"Mcp-Session-Id": opened.headers["Mcp-Session-Id"]}
    await client.post(ROUTE, headers=_headers(raw, **session), json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    with patch.object(mcp_transport, "_async_get_authenticated_access_key", authenticate):
        response = await client.post(ROUTE, headers=_headers(raw, **session), json=PING)
    assert response.headers["X-RateLimit-Limit"] == "70"


@pytest.mark.parametrize("lose,status,error", [
    (lambda hass, data, key: setattr(key, "revoked", True), 401, b"Unauthorized."),
    (lambda hass, data, key: setattr(data, "ready", False), 503, b"Service unavailable."),
    (lambda hass, data, key: setattr(data, "shutting_down", True), 503, b"Service unavailable."),
    (lambda hass, data, key: setattr(data.store.get_settings(), "kill_switch", True), 503, b"Service unavailable."),
    (lambda hass, data, key: hass.data.pop(DOMAIN), 503, b"Service unavailable."),
], ids=["revoked", "not-ready", "shutting-down", "kill-switch", "runtime-removed"])
async def test_authority_lost_while_reading_answers_by_what_was_lost(env, hass, lose, status, error):
    client, data = env
    key, raw = await _key(data)

    async def authenticate(*args):
        result = await async_get_authenticated_access_key(*args)
        lose(hass, data, key)
        return result

    with patch.object(mcp_transport, "_async_get_authenticated_access_key", authenticate):
        response = await client.post(ROUTE, headers=_headers(raw), json=PING)
    assert response.status == status
    assert error in await response.read()


@pytest.mark.parametrize("body,method", [
    (b"{", "POST"),
    (b"1", "POST"),
    (json.dumps(PING).encode(), "ping"),
], ids=["unparseable", "scalar", "message"])
async def test_a_dead_session_is_audited_as_not_found(env, body, method):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw, **{"Mcp-Session-Id": "gone"}), data=body)
    assert response.status == 404
    assert _audit_rows(data) == [(method, ROUTE, "not_found")]


@pytest.mark.parametrize("raw_id,expected", [({}, None), (True, None), (1.5, None), ("a", "a"), (7, 7)],
                         ids=["object", "bool", "float", "string", "integer"])
async def test_a_dead_session_replies_only_with_a_usable_id(env, raw_id, expected):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw, **{"Mcp-Session-Id": "gone"}), json={**PING, "id": raw_id})
    assert response.status == 404
    assert (await response.json())["id"] == expected


async def test_a_modern_body_with_no_version_header_ignores_a_stale_session(env):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw, **{"Mcp-Session-Id": "gone"}), json=_modern("tools/list"))
    assert response.status == 400
    assert (await response.json())["error"]["code"] == -32020


@pytest.mark.parametrize("padding,status", [(0, 200), (1, 413)], ids=["at-the-cap", "over-the-cap"])
async def test_a_chunked_body_meets_the_same_cap_as_a_declared_one(env, padding, status):
    client, data = env
    _, _, headers = await _session(client, data)
    message = json.dumps(PING).encode()
    half = (MAX_REQUEST_BODY_BYTES - len(message)) // 2
    pieces = [b" " * half, b" " * (MAX_REQUEST_BODY_BYTES - len(message) - half + padding), message]

    async def body():
        for piece in pieces:
            yield piece

    response = await client.post(ROUTE, headers=headers, data=body())
    assert response.status == status


async def test_a_client_gone_mid_body_cancels_the_read_without_an_audit_row(env):
    client, data = env
    _, raw = await _key(data)
    head = b"\r\n".join([
        b"POST " + ROUTE.encode() + b" HTTP/1.1", b"Host: x", b"Content-Length: 100",
        b"Authorization: Bearer " + raw.encode()]) + b"\r\n\r\n{"
    _, writer = await asyncio.open_connection(client.server.host, client.server.port)
    writer.write(head)
    await writer.drain()
    await asyncio.sleep(0.05)
    writer.transport.abort()
    await asyncio.sleep(0.05)
    assert _audit_rows(data) == []


async def test_a_batch_carrying_modern_metadata_is_refused_with_no_version_header(env):
    client, data = env
    _, _, headers = await _session(client, data)
    response = await client.post(ROUTE, headers=headers, json=[_modern("ping")])
    assert response.status == 400
    assert "batches are not supported" in (await response.json())["error"]["message"]
    assert _audit_rows(data)[0] == ("POST", "mcp:batch", "invalid_request")


@pytest.mark.parametrize("session,status,outcome", [(None, 400, "invalid_request"), ("gone", 404, "not_found")],
                         ids=["no-session", "dead-session"])
async def test_a_batch_with_no_live_session_is_audited_by_what_was_missing(env, session, status, outcome):
    client, data = env
    _, raw = await _key(data)
    headers = _headers(raw, **({"Mcp-Session-Id": session} if session else {}))
    response = await client.post(ROUTE, headers=headers, json=[PING])
    assert response.status == status
    assert _audit_rows(data) == [("POST", "mcp:batch", outcome)]


async def test_an_oversized_batch_is_refused_before_it_is_charged(env):
    client, data = env
    key, raw = await _key(data)
    key.rate_limit_requests = 1000
    opened = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    headers = _headers(raw, **{"Mcp-Session-Id": opened.headers["Mcp-Session-Id"]})
    await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    charged = len(data.rate_limiter._windows[key.id])
    response = await client.post(ROUTE, headers=headers, json=[PING] * (MAX_BATCH_ITEMS + 1))
    assert response.status == 400
    assert "request capacity" in (await response.json())["error"]["message"]
    assert len(data.rate_limiter._windows[key.id]) == charged + 1


async def test_a_batch_over_the_window_is_refused_whole_and_recorded(env):
    client, data = env
    key, raw = await _key(data)
    key.rate_limit_requests = 4
    opened = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    headers = _headers(raw, **{"Mcp-Session-Id": opened.headers["Mcp-Session-Id"]})
    await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    with patch.object(mcp_transport, "_fire_rate_limit_events") as fired, patch.object(mcp_transport, "_dispatch_mcp") as dispatch:
        response = await client.post(ROUTE, headers=headers, json=[PING, {**PING, "id": 2}, {**PING, "id": 3}])
    assert response.status == 429
    assert int(response.headers["Retry-After"]) > 0
    assert "X-RateLimit-Limit" not in response.headers
    fired.assert_called_once()
    dispatch.assert_not_called()
    assert _audit_rows(data)[0] == ("POST", "mcp:batch", "rate_limited")


SSE_ACCEPT = {"Accept": "application/json, text/event-stream"}


async def test_a_streamed_batch_reports_the_budget_after_every_item_is_charged(env):
    client, data = env
    key, raw = await _key(data)
    key.rate_limit_requests = 100
    opened = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    headers = _headers(raw, **{"Mcp-Session-Id": opened.headers["Mcp-Session-Id"]})
    await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    batch = [{**PING, "id": index} for index in range(3)]
    response = await client.post(ROUTE, headers={**headers, **SSE_ACCEPT}, json=batch)
    assert response.headers["Content-Type"].startswith("text/event-stream")
    assert response.headers["X-RateLimit-Remaining"] == "95"


@pytest.mark.parametrize("batched", [False, True], ids=["single", "batch"])
async def test_a_streamed_call_publishes_its_progress_bus_to_dispatch(env, batched):
    client, data = env
    _, _, headers = await _session(client, data)
    seen = []

    async def dispatch(_method, msg_id, *_args, **_kwargs):
        seen.append(_progress_ctx.get().progress_token)
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}, "tools/call", "get_state", "allowed"

    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_state", "_meta": {"progressToken": "token"}}}
    with patch.object(mcp_transport, "_dispatch_mcp", dispatch):
        response = await client.post(ROUTE, headers={**headers, **SSE_ACCEPT}, json=[call] if batched else call)
    assert response.status == 200
    await response.read()
    assert seen == ["token"]


async def test_a_scalar_body_is_audited(env):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw), data=b"1")
    assert response.status == 400
    assert _audit_rows(data) == [("POST", ROUTE, "invalid_request")]


@pytest.mark.parametrize("accept,batched", [({}, False), ({}, True), (SSE_ACCEPT, False), (SSE_ACCEPT, True)],
                         ids=["json", "json-batch", "sse", "sse-batch"])
async def test_legacy_work_finishes_after_the_client_leaves(env, accept, batched):
    client, data = env
    _, _, headers = await _session(client, data)
    started, release, finished = asyncio.Event(), asyncio.Event(), []

    async def dispatch(_method, msg_id, *_args, **_kwargs):
        started.set()
        await release.wait()
        finished.append(msg_id)
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}, "ping", "ping", "allowed"

    with patch.object(mcp_transport, "_dispatch_mcp", dispatch):
        await _disconnect_mid_call(client, {**headers, **accept}, [PING] if batched else PING, started)
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.sleep(0.05)
    assert finished == [1]


@pytest.mark.parametrize("batched", [False, True], ids=["single", "batch"])
async def test_a_detached_legacy_dispatch_reports_its_own_failure(env, batched):
    client, data = env
    _, _, headers = await _session(client, data)
    with patch.object(mcp_transport, "_log_dispatch_task_error") as logged:
        response = await client.post(ROUTE, headers=headers, json=[PING] if batched else PING)
    assert response.status == 200
    logged.assert_called_once()
    assert logged.call_args.args[0].done()


async def test_a_legacy_response_object_is_accepted_and_audited_as_such(env):
    client, data = env
    _, raw = await _key(data)
    response = await client.post(ROUTE, headers=_headers(raw), json={"jsonrpc": "2.0", "id": 1, "result": {}})
    assert response.status == 202
    assert _audit_rows(data) == [("POST", "mcp:response", "allowed")]


async def test_initialize_on_a_live_session_is_refused_and_opens_nothing(env):
    client, data = env
    _, _, headers = await _session(client, data)
    before = set(data.mcp_sessions)
    response = await client.post(ROUTE, headers=headers, json=INITIALIZE)
    assert response.status == 400
    assert "must start a new session" in (await response.json())["error"]["message"]
    assert set(data.mcp_sessions) == before


async def test_only_ping_is_served_before_initialization_completes(env):
    client, data = env
    _, raw = await _key(data)
    opened = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    headers = _headers(raw, **{"Mcp-Session-Id": opened.headers["Mcp-Session-Id"]})
    assert (await client.post(ROUTE, headers=headers, json=PING)).status == 200
    refused = await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert (await refused.json())["error"]["message"] == "Legacy MCP initialization is not complete."


async def test_a_session_quota_refusal_is_audited_with_its_retry(env):
    client, data = env
    key, raw = await _key(data)
    for index in range(8):
        data.mcp_sessions[f"busy-{index}"] = {"access_key_id": key.id, "initialized": True, "in_use": 1, "last_seen": time.monotonic()}
    response = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    assert response.status == 429
    assert int(response.headers["Retry-After"]) > 0
    assert _audit_rows(data) == [("initialize", ROUTE, "invalid_request")]


async def test_a_modern_notification_this_era_does_not_serve_is_accepted_silently(env):
    client, data = env
    _, raw = await _key(data)
    method = "notifications/initialized"
    response = await client.post(ROUTE, headers=_modern_headers(raw, method), json=_modern(method, None))
    assert response.status == 202
    assert await response.read() == b""


async def test_a_modern_unknown_method_keeps_the_modern_error_codes(env):
    client, data = env
    _, raw = await _key(data)

    async def dispatch(_method, msg_id, *_args, **_kwargs):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": "Changed."}}, "no/such", "no/such", "denied"

    with patch.object(mcp_transport, "_dispatch_mcp", dispatch):
        response = await client.post(ROUTE, headers=_modern_headers(raw, "no/such"), json=_modern("no/such"))
    assert response.status == 404
    assert (await response.json())["error"]["code"] == MCP_AUTHORITY_CHANGED_ERROR


async def test_a_streamed_failure_answers_with_the_request_id(env):
    client, data = env
    _, _, headers = await _session(client, data)

    async def dispatch(*_args, **_kwargs):
        raise RuntimeError("held")

    with patch.object(mcp_transport, "_dispatch_mcp", dispatch):
        response = await client.post(ROUTE, headers={**headers, **SSE_ACCEPT}, json={**PING, "id": 41})
        text = await response.text()
    frame = json.loads(text.split("data: ", 1)[1])
    assert (frame["id"], frame["error"]["code"]) == (41, -32603)


async def test_a_notification_gets_no_body_whatever_dispatch_returns(env):
    client, data = env
    _, raw = await _key(data)
    opened = await client.post(ROUTE, headers=_headers(raw), json=INITIALIZE)
    headers = _headers(raw, **{"Mcp-Session-Id": opened.headers["Mcp-Session-Id"]})

    async def dispatch(*_args, **_kwargs):
        return {"jsonrpc": "2.0", "id": None, "result": {}}, "initialized", "initialized", "allowed"

    with patch.object(mcp_transport, "_dispatch_mcp", dispatch):
        response = await client.post(ROUTE, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status == 202
    assert await response.read() == b""


async def test_a_reply_holding_a_value_json_cannot_write_is_stringified(env):
    client, data = env
    _, _, headers = await _session(client, data)

    async def dispatch(_method, msg_id, *_args, **_kwargs):
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"value": set}}, "ping", "ping", "allowed"

    with patch.object(mcp_transport, "_dispatch_mcp", dispatch):
        response = await client.post(ROUTE, headers=headers, json=PING)
    assert response.status == 200
    assert (await response.json())["result"]["value"] == "<class 'set'>"
