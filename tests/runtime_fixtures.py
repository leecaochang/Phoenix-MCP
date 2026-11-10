"""Helpers shared between test modules: harness objects, sample data and result shapes.

Test modules never import from each other. Anything two of them need lives here
or, when it is a pytest fixture, in conftest.py.
"""

import asyncio
from contextlib import contextmanager
import importlib
import sys
import hashlib
import json
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import DEFAULT, AsyncMock, MagicMock, patch

from aiohttp import web
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.phoenix_mcp import mcp_transport, mesa_tools, tool_defs
from custom_components.phoenix_mcp.access_key_store import (
    AccessKeyRecord,
    AccessKeyStore,
    GlobalSettings,
    PermissionNode,
    PermissionTree,
)
from custom_components.phoenix_mcp.audit import AuditLog
from custom_components.phoenix_mcp.const import (
    ACCESS_KEY_PREFIX, DOMAIN, MCP_LEGACY_PROTOCOL_VERSION_PREFERRED, MESA_MODE_OFF,
)
from custom_components.phoenix_mcp.esphome_constants import (
    ESPHOME_BUILDER_VERIFIED_VERSION, ESPHOME_COMPILER_VERIFIED_VERSION,
)
from custom_components.phoenix_mcp.data import PhoenixData
from custom_components.phoenix_mcp.esphome_builder import BuilderResult
from custom_components.phoenix_mcp.mcp_transport import PhoenixMcpView
from custom_components.phoenix_mcp.tool_dispatch import _call_tool
from custom_components.phoenix_mcp.rate_limiter import RateLimiter, RateLimitResult


@contextmanager
def patch_readers(target: str, new: Any = DEFAULT, **kwargs: Any):
    """Patch a shared function in its home module and every module that imported it.

    Provider accounts and clients are read by each ingress, and one turn crosses
    several of them, so a single binding cannot stand in for the service.
    """
    module_name, _, name = target.rpartition(".")
    original = getattr(importlib.import_module(module_name), name)
    home = patch(target, new, **kwargs)
    mock = home.start()
    copies = [patch.object(module, name, mock) for module_name, module in list(sys.modules.items())
              if module_name.startswith("custom_components.phoenix_mcp") and module is not None
              and getattr(module, name, None) is original]
    for copy in copies:
        copy.start()
    try:
        yield mock
    finally:
        for copy in copies:
            copy.stop()
        home.stop()



def mesa_off_store() -> MagicMock:
    """AccessKeyStore mock whose settings put MESA in off mode.

    For tests that isolate non-MESA behavior.
    """
    store = MagicMock(spec=AccessKeyStore)
    store.get_settings.return_value = GlobalSettings(mesa_mode="off")
    return store


def assist_result_parts(result):
    """Split a Phoenix Assist tool result into its JSON object and error flag.

    HA 2026.10 takes llm.ToolResult, whose error flag providers forward to the model.
    Earlier releases take the plain object, which carries no flag, reported as None.
    """
    from homeassistant.helpers import llm

    result_type = getattr(llm, "ToolResult", None)
    if result_type is None:
        assert isinstance(result, dict)
        return result, None
    assert isinstance(result, result_type)
    return result.data, result.error


def raw_access_key() -> str:
    return ACCESS_KEY_PREFIX + secrets.token_hex(32)


def make_mcp_access_key(
    pass_through: bool = False,
    cap_restart: str = "deny",
    cap_config_read: str = "deny",
    cap_template_render: str = "deny",
    cap_automation_write: str = "deny",
    cap_script_write: str = "deny",
    cap_log_read: str = "deny",
    cap_log_control: str = "deny",
    rate_limit_requests: int = 60,
    rate_limit_burst: int = 10,
    revoked: bool = False,
    permissions: PermissionTree | None = None,
) -> tuple[AccessKeyRecord, str]:
    from homeassistant.util.dt import utcnow

    raw = raw_access_key()
    access_key_hash = hashlib.sha256(raw.encode()).hexdigest()
    record = AccessKeyRecord(
        id=str(uuid.uuid4()),
        name="test-access-key",
        access_key_hash=access_key_hash,
        created_at=utcnow(),
        created_by="user1",
        pass_through=pass_through,
        cap_restart=cap_restart,
        cap_config_read=cap_config_read,
        cap_template_render=cap_template_render,
        cap_automation_write=cap_automation_write,
        cap_script_write=cap_script_write,
        cap_log_read=cap_log_read,
        cap_log_control=cap_log_control,
        rate_limit_requests=rate_limit_requests,
        rate_limit_burst=rate_limit_burst,
        revoked=revoked,
        permissions=permissions or PermissionTree(),
    )
    return record, raw


def make_mcp_data(access_key: AccessKeyRecord | None = None) -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    if access_key is not None:
        store.get_access_key_by_hash.return_value = access_key
    else:
        store.get_access_key_by_hash.return_value = None
    store.get_settings.return_value = MagicMock(
        kill_switch=False,
        mesa_mode="off",
        disable_all_logging=False,
        log_allowed=True,
        log_denied=True,
        log_rate_limited=True,
        log_entity_names=True,
        log_client_ip=True,
        notify_on_rate_limit=False,
    )
    store.update_last_used = MagicMock()
    store.get_entity_hints.return_value = {}

    rate_limiter = MagicMock(spec=RateLimiter)
    rate_limiter.check.return_value = RateLimitResult(
        allowed=True,
        rate_limiting_enabled=True,
        limit=60,
        remaining=59,
        reset=9999999999,
        retry_after=0,
    )

    audit = MagicMock(spec=AuditLog)
    audit.record = MagicMock()

    data = PhoenixData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
        rate_limit_notified={},
    )
    if access_key is not None:
        data.mcp_sessions[LEGACY_TEST_SESSION] = {
            "access_key_id": access_key.id, "initialized": True,
            "last_seen": time.monotonic(), "protocol_version": MCP_LEGACY_PROTOCOL_VERSION_PREFERRED,
        }
    return data


LEGACY_TEST_SESSION = "fixture-session"


class SessionMcpView(PhoenixMcpView):
    """Post on the fixture's initialized legacy session, as a client does after its handshake.

    Requests pass through the real session gate. A request that names a session
    of its own, or a fixture with no session seeded, is left as it is.
    """

    async def post(self, request):
        read = request.headers.get
        if LEGACY_TEST_SESSION in self.hass.data[DOMAIN].mcp_sessions and not read("Mcp-Session-Id"):
            request.headers.get = lambda key, default="": (
                LEGACY_TEST_SESSION if key == "Mcp-Session-Id" else read(key, default))
        return await super().post(request)


def make_mcp_hass(data: PhoenixData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.components = MagicMock()
    hass.states = MagicMock()
    hass.states.async_all.return_value = []
    # The tools these mocks drive need their component loaded; any other stays unloaded.
    hass.config.components = {"automation", "calendar", "script"}
    return hass


def make_active_access_key(name: str = "test-access-key", pass_through: bool = False) -> AccessKeyRecord:
    from homeassistant.util.dt import utcnow

    raw = ACCESS_KEY_PREFIX + secrets.token_hex(32)
    access_key_hash = hashlib.sha256(raw.encode()).hexdigest()
    return AccessKeyRecord(
        id=str(uuid.uuid4()),
        name=name,
        access_key_hash=access_key_hash,
        created_at=utcnow(),
        created_by="user1",
        pass_through=pass_through,
    )


def make_admin_data(access_keys: list[AccessKeyRecord] | None = None) -> PhoenixData:
    store = MagicMock(spec=AccessKeyStore)
    store.list_access_keys.return_value = access_keys or []
    store.list_archived.return_value = []
    store.get_settings.return_value = GlobalSettings()
    store.get_entity_hints.return_value = {}
    store.get_pending_approvals.return_value = []
    store.async_lock = asyncio.Lock()

    rate_limiter = MagicMock(spec=RateLimiter)
    audit = MagicMock(spec=AuditLog)
    audit.query.return_value = []
    audit.count.return_value = 0

    return PhoenixData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
    )


def make_admin_request(is_admin: bool = True, body: bytes = b"", authenticated: bool = True) -> MagicMock:
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

    user = MagicMock()
    user.is_admin = is_admin
    user.id = "admin-user-id"

    def _get(k, default=None):
        if k == KEY_HASS_USER:
            return user
        if k == KEY_AUTHENTICATED:
            return authenticated
        return default

    request = MagicMock()
    request.query = {}
    request.read = AsyncMock(return_value=body)
    request.content_length = len(body)
    request.content = MagicMock()
    request.content.read = AsyncMock(return_value=body)
    request.__getitem__ = MagicMock(side_effect=lambda k: user if k == KEY_HASS_USER else None)
    request.get = MagicMock(side_effect=_get)
    return request


def make_admin_hass(data: PhoenixData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    return hass


def mesa_admin_request(body: bytes = b"", query: dict | None = None) -> MagicMock:
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

    user = MagicMock()
    user.is_admin = True
    user.id = "admin-user"
    state: dict = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "phoenix_mcp_rid": "rid"}

    def _get(k, default=None):
        if k == KEY_HASS_USER:
            return user
        if k == KEY_AUTHENTICATED:
            return True
        return default

    request = MagicMock()
    request.method = "GET"
    request.path = "/api/phoenix-mcp/admin/mesa"
    request.remote = "127.0.0.1"
    request.query = query or {}
    request.content_length = len(body)
    request.content = MagicMock()
    request.content.read = AsyncMock(return_value=body)
    request.__getitem__ = MagicMock(side_effect=lambda k: state.get(k))
    request.__setitem__ = MagicMock(side_effect=lambda k, v: state.__setitem__(k, v))
    request.get = MagicMock(side_effect=_get)
    return request


def esphome_access_key(permissions: PermissionTree | None = None, **caps) -> AccessKeyRecord:
    tree = permissions or PermissionTree(domains={"sensor": PermissionNode(state="GREEN")})
    base = {"cap_diagnostics": "allow"}
    base.update(caps)
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def esphome_json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def esphome_call(name, args, access_key, hass):
    if name in {"call_service", "dry_run_service"} and isinstance(args.get("service"), str):
        args = {"service": {"domain": args.pop("domain"), "name": args.pop("service"), "data": args.pop("service_data", {})}, **args}
    return await _call_tool(name, args, access_key, hass, MagicMock())


def esphome_device_info(**overrides):
    """A stand-in for aioesphomeapi DeviceInfo, read by the tool via getattr.

    Duck-typed because aioesphomeapi is an ESPHome integration requirement, not
    a Phoenix one, and is not installed in the test environment.
    """
    base = {
        "name": "rf-blaster1",
        "friendly_name": "RF Blaster 1",
        "esphome_version": "2026.6.0",
        "compilation_time": "Jul 1 2026, 10:00:00",
        "manufacturer": "espressif",
        "model": "esp32dev",
        "project_name": None,
        "project_version": None,
        "suggested_area": None,
        "has_deep_sleep": False,
        "webserver_port": 0,
        "uses_password": False,
        "api_encryption_supported": True,
        "mac_address": "AA:BB:CC:DD:EE:FF",
        "bluetooth_mac_address": "AA:BB:CC:DD:EE:00",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def esphome_runtime(device_info=None, **overrides):
    base = {
        "available": True,
        "expected_disconnect": False,
        "device_info": device_info if device_info is not None else esphome_device_info(),
        "bluetooth_device": None,
        "services": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def esphome_entry(
    hass, entries, *, entity_ids=("sensor.rf_blaster1_rssi",), runtime=None,
    state=ConfigEntryState.LOADED, title="RF Blaster1",
):
    """Register real registry entities and append a fake entry the tool will see."""
    backing = MockConfigEntry(domain="esphome", title=title)
    backing.add_to_hass(hass)
    registry = er.async_get(hass)
    for eid in entity_ids:
        domain, object_id = eid.split(".", 1)
        registry.async_get_or_create(
            domain, "esphome", f"unique-{object_id}",
            config_entry=backing, suggested_object_id=object_id,
        )

    fake = SimpleNamespace(
        entry_id=backing.entry_id, domain="esphome", title=title, state=state, data={})
    if runtime is not None:
        fake.runtime_data = runtime
    entries.append(fake)
    return fake


DEVICE_YAML = '''esphome:
  name: rf-blaster1

# Enable Home Assistant API
api:
  encryption:
    key: "REALAPIKEY123456="

ota:
  - platform: esphome
    password: "realotapassword"

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

uart:
  baud_rate: 9600
'''


SECRETS_YAML = 'wifi_ssid: MyHomeNetwork\nwifi_password: housepassword1\n'


def esphome_yaml_access_key(**caps) -> AccessKeyRecord:
    base = {"cap_esphome_yaml": "allow"}
    base.update(caps)
    return esphome_access_key(**base)


FIRMWARE_FILE = "rf-blaster1.yaml"


FIRMWARE_JOB = "abc123def456"


def firmware_dash():
    return SimpleNamespace(url="http://127.0.0.1:6052", data={}, last_update_success=True)


class FakeBuilder:
    """Scripts async_builder_command per command name and records every call made."""

    def __init__(self, **by_command):
        self.by_command = by_command
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, hass, url, command, args, **kw):
        self.calls.append((command, args))
        reply = self.by_command.get(command, {})
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(args)
        return reply if isinstance(reply, BuilderResult) else BuilderResult(result=reply)

    @property
    def commands(self) -> list[str]:
        return [c for c, _ in self.calls]

    def args_for(self, command: str) -> dict:
        return next(a for c, a in self.calls if c == command)


EXPOSURE_SHOWN = "light.kitchen"


EXPOSURE_HIDDEN = "light.porch"


def exposure_key(*, filtered: bool = True) -> AccessKeyRecord:
    return AccessKeyRecord(
        id=str(uuid.uuid4()), name="t", access_key_hash="x", created_at=utcnow(), created_by="u",
        permissions=PermissionTree(), pass_through=True, use_assist_exposure=filtered,
        cap_search="allow", cap_config_read="allow", cap_log_read="allow", cap_camera_read="allow", cap_diagnostics="allow",
    )


def exposure_data(mesa: object | None = None) -> PhoenixData:
    store = MagicMock()
    store.get_settings.return_value = SimpleNamespace(mesa_mode=MESA_MODE_OFF)
    return PhoenixData(store=store, rate_limiter=MagicMock(), audit=MagicMock(), mesa=mesa)


def expose_to_assist(hass, shown: str, hidden: str) -> None:
    async_expose_entity(hass, "conversation", shown, True)
    async_expose_entity(hass, "conversation", hidden, False)


def exposure_body(result: tuple) -> dict:
    return json.loads(result[0]["content"][0]["text"])


class FakeStream:
    """Captures what the view writes to a prepared StreamResponse."""

    def __init__(self, fail_writes: bool = False) -> None:
        self.frames: list[bytes] = []
        self.prepared = False
        self.eof = False
        self.fail_writes = fail_writes
        self.status = 200
        self.headers: dict[str, str] = {}

    async def prepare(self, _request):
        self.prepared = True

    async def write(self, data: bytes) -> None:
        if self.fail_writes:
            raise ConnectionResetError("client gone")
        self.frames.append(data)

    async def write_eof(self) -> None:
        self.eof = True

    # --- helpers the assertions use -------------------------------------
    @property
    def text(self) -> str:
        return b"".join(self.frames).decode()

    def messages(self) -> list[dict]:
        out = []
        for frame in self.text.split("\n\n"):
            for line in frame.splitlines():
                if line.startswith("data: "):
                    out.append(json.loads(line[len("data: "):]))
        return out

    def keepalives(self) -> int:
        return self.text.count(": keepalive")


def stream_patch(stream: FakeStream):
    def _factory(*_a, status=200, headers=None, **_kw):
        stream.status = status
        stream.headers = dict(headers or {})
        return stream
    return patch.object(mcp_transport.web, "StreamResponse", side_effect=_factory)


def make_sse_request(
    body: dict | list,
    accept: str,
    raw_access_key: str,
    extra_headers: dict[str, str] | None = None,
) -> MagicMock:
    payload = json.dumps(body).encode()
    req = MagicMock()
    req.method = "POST"
    req.remote = "127.0.0.1"
    headers = {"Authorization": f"Bearer {raw_access_key}", "Accept": accept}
    headers.update(extra_headers or {})
    req.headers = MagicMock()
    req.headers.get = MagicMock(side_effect=lambda k, default="": headers.get(k, default))
    req.content_length = len(payload)
    chunks = [payload, b""]
    req.content = MagicMock()
    req.content.read = AsyncMock(side_effect=lambda *_a: chunks.pop(0) if chunks else b"")
    req.content.at_eof = lambda: not chunks
    req.url = MagicMock()
    req.url.origin.return_value = "http://homeassistant.local"
    req.query = {}
    return req


def sse_view(hass) -> SessionMcpView:
    view = SessionMcpView()
    view.hass = hass
    return view


def static_tool_defs() -> list[dict]:
    return [
        *tool_defs._ENTITY_TOOL_DEFS,
        *tool_defs._NATIVE_TOOL_DEFS,
        *tool_defs._SYSTEM_TOOL_DEFS,
    ]


def all_tool_defs() -> list[dict]:
    return [*static_tool_defs(), *mesa_tools.mesa_tool_defs()]


def make_assist_access_key(*, announce_all: bool = False, cap_config_read: str = "allow") -> AccessKeyRecord:
    from homeassistant.util.dt import utcnow

    raw = ACCESS_KEY_PREFIX + secrets.token_hex(32)
    return AccessKeyRecord(
        id=str(uuid.uuid4()),
        name="voice-access-key",
        access_key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_at=utcnow(),
        created_by="admin",
        cap_config_read=cap_config_read,
        announce_all_tools=announce_all,
        permissions=PermissionTree(),
    )


def make_assist_data(access_key: AccessKeyRecord | None, *, kill_switch: bool = False, bound_id=None) -> SimpleNamespace:
    settings = GlobalSettings(kill_switch=kill_switch, assist_bound_access_key_id=bound_id)
    store = MagicMock()
    store.get_settings.return_value = settings
    store.get_access_key_by_id.side_effect = lambda tid: access_key if (access_key and tid == access_key.id) else None
    return SimpleNamespace(
        store=store,
        mesa=None,
        ready=True,
        shutting_down=False,
        assist_api_unregister=None,
    )


def assist_ctx(user_id=None):
    from homeassistant.core import Context
    return SimpleNamespace(platform="conversation", context=Context(user_id=user_id), language="en", assistant="conversation", device_id=None)


def assist_tool_input(args):
    return SimpleNamespace(id="ti-1", tool_name="t", tool_args=args, external=False)


# Announces the VERIFIED version by default, so ordinary tests model a verified
# deployment and no version note is appended to their errors.
SERVER_INFO = {
    "server_version": ESPHOME_BUILDER_VERIFIED_VERSION,
    "esphome_version": ESPHOME_COMPILER_VERIFIED_VERSION,
    "port": 6052,
    "ha_addon": True,
    "ha_ingress": False,
    "requires_auth": False,
}


class FakeDeviceBuilder:
    """A scriptable stand-in for the add-on's /ws endpoint."""

    def __init__(self, *, requires_auth=False, replies=None, hang=False, keep_open=False):
        self.requires_auth = requires_auth
        # Each entry is either a frame dict or a list of frames to send in order.
        self.replies = replies or {}
        self.hang = hang
        # keep_open models an open-ended builder_stream like devices/logs: frames are
        # sent, then the socket stays up and no result frame ever arrives.
        self.keep_open = keep_open
        self.received: list[dict] = []
        # Overridable per test to exercise the unverified-version path.
        self.server_version = SERVER_INFO["server_version"]

    async def handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps({
            **SERVER_INFO,
            "server_version": self.server_version,
            "requires_auth": self.requires_auth,
        }))
        if self.hang:
            await asyncio.sleep(30)
            return ws
        async for msg in ws:
            frame = json.loads(msg.data)
            self.received.append(frame)
            reply = self.replies.get(frame["command"], {"message_id": "1", "result": {}})
            for out in reply if isinstance(reply, list) else [reply]:
                await ws.send_str(json.dumps(out))
            if self.keep_open:
                # Stay readable so the client's own builder_stream-stop frame is recorded
                # rather than lost when the socket is torn down.
                continue
            await ws.close()
            break
        return ws

    def app(self):
        app = web.Application()
        app.router.add_get("/ws", self.handler)
        return app


def builder_stream(*outputs, success=True, code=0):
    frames = [{"message_id": "1", "event": "output", "data": o} for o in outputs]
    frames.append({
        "message_id": "1",
        "event": "result",
        "data": {"success": success, "code": code},
    })
    return frames


class DecisionWire:
    """A System One endpoint at the aiohttp boundary, so `_request` and its guard run for real.

    Replies choose `picks[question]` where it is a valid answer, else `none`, else the
    first criterion. `before_send[n]` runs while the budget reservation of the n-th
    POST awaits, the last yield before that request's guard. `after_reply[n]` runs
    while the n-th POST is in flight, as a change between two questions would. `reply`
    replaces the whole response body and `status` fails the request.
    """

    def __init__(self, mock: Any) -> None:
        self.posts: list[dict] = []
        self.gets: list[str] = []
        self.picks: dict[str, str] = {}
        self.model = "jev-1.13.0"
        self.reply: dict | None = None
        self.status = 200
        self.before_send: dict[int, Callable[[], Awaitable[None]]] = {}
        self.after_reply: dict[int, Callable[[], Awaitable[None]]] = {}
        self._reservations = 0
        mock.post(re.compile("/systemone$"), side_effect=self._post)
        mock.get(re.compile("/models$"), side_effect=self._get)

    def budget_gate(self) -> SimpleNamespace:
        """Stand in for the request budget: every POST reserves through it first."""
        async def reserve(_limit: int) -> None:
            self._reservations += 1
            action = self.before_send.get(self._reservations)
            if action is not None:
                await action()
        return SimpleNamespace(reserve=reserve)

    def _answer(self, question: str, spec: dict) -> dict:
        if spec["type"] == "noul":
            return {"type": "noul", "noul": 0.75}
        if spec["type"] == "score":
            levels = {str(level): float(level == 0) for level in range(len(spec["criteria"]))}
            return {"type": "score", "score": 0, "confidence": 1, "probabilities": levels,
                    "legend": {str(level): text for level, text in enumerate(spec["criteria"])}}
        wanted = self.picks.get(question)
        criteria = list(spec["criteria"])
        selected = next(key for key in (wanted, "none", criteria[0]) if key in criteria)
        return {"type": "choice", "choice": selected, "confidence": 1,
                "probabilities": {key: float(key == selected) for key in criteria}}

    async def _post(self, _method: str, url: Any, payload: dict) -> AiohttpClientMockResponse:
        self.posts.append(payload)
        action = self.after_reply.get(len(self.posts))
        if action is not None:
            await action()
        if self.status != 200:
            return AiohttpClientMockResponse("post", url, status=self.status, json={})
        body = self.reply if self.reply is not None else {"model": self.model, "answers": {
            question: self._answer(question, spec) for question, spec in payload["questions"].items()}}
        return AiohttpClientMockResponse("post", url, json=body)

    async def _get(self, _method: str, url: Any, _payload: Any) -> AiohttpClientMockResponse:
        self.gets.append(str(url))
        return AiohttpClientMockResponse("get", url, json={"models": [{"name": "jev-latest"}]})


SCHEMA_MARKER = "PHXTORTURE"


def schema_placeholder(name: str, schema: dict):
    """Build a recursively valid seed so one hostile field reaches its own boundary."""
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for choice_key in ("oneOf", "anyOf"):
        choices = schema.get(choice_key)
        if choices and not schema.get("type") and not schema.get("properties"):
            return schema_placeholder(name, choices[0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), kind[0])
    if kind == "object":
        properties = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        if schema.get("anyOf"):
            required.update(schema["anyOf"][0].get("required", []) or [])
        for clause in schema.get("allOf", []) or []:
            options = clause.get("anyOf", []) or []
            if options:
                required.update(options[0].get("required", []) or [])
            required.update(clause.get("required", []) or [])
        minimum = int(schema.get("minProperties", 0) or 0)
        if len(required) < minimum:
            required.update(list(properties)[:minimum])
        seed = {
            field: schema_placeholder(field, properties.get(field, {}))
            for field in properties
            if field in required
        }
        if {"domain", "name"} <= set(seed):
            # A service name has one spelling, so the seed must use it to get past the contract.
            seed = {**seed, "domain": seed["domain"].lower(), "name": seed["name"].lower()}
        return seed
    if kind == "array":
        count = max(0, int(schema.get("minItems", 0) or 0))
        return [schema_placeholder(name, schema.get("items", {})) for _ in range(count)]
    if kind == "boolean":
        return False
    if kind in ("integer", "number"):
        value = schema.get("minimum", 1)
        exclusive = schema.get("exclusiveMinimum")
        if isinstance(exclusive, (int, float)) and not isinstance(exclusive, bool):
            value = max(value, exclusive + 1)
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)):
            value = min(value, maximum)
        return int(value) if kind == "integer" else value
    if name == "entity_id":
        return "light.phx_ghost"
    if name.endswith("_id"):
        return "phx_ghost_id"
    value = "light.phx_ghost" if name == "entity_id" else f"{SCHEMA_MARKER}_value"
    minimum = int(schema.get("minLength", 0) or 0)
    if len(value) < minimum:
        value += "x" * (minimum - len(value))
    maximum = schema.get("maxLength")
    return value[:maximum] if isinstance(maximum, int) else value


def schema_baseline(tool_def: dict) -> dict:
    """Derive valid arguments from a schema, with a semantic fixture where it omits a cross-field rule."""
    if tool_def["name"] == "edit_energy_config":
        return {
            "operation": "remove_device",
            "target": {"kind": "statistic", "id": "sensor.phx_ghost"},
            "changes": {},
        }
    return schema_placeholder("arguments", tool_def.get("inputSchema", {}) or {})
