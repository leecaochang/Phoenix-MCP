"""Log-text and diagnostics redaction behind the cap_log_read MCP tools.

collect_log_entries routes every message and exception through _scrub_log_text,
which removes Phoenix MCP access keys, JWTs/LLATs, and URL-embedded credentials so an
access key holding cap_log_read never receives another integration's secret verbatim.
redact_secrets_in_text and redact_diagnostics apply the same discipline to source
text and diagnostics payloads.
"""

from __future__ import annotations

import sys
import types

import homeassistant.components
import pytest
from homeassistant.components import network
from homeassistant.components.network.models import Adapter, IPv4ConfiguredAddress, IPv6ConfiguredAddress

from custom_components.phoenix_mcp import helpers
from custom_components.phoenix_mcp.const import DOMAIN, ACCESS_KEY_PREFIX
from custom_components.phoenix_mcp.helpers import (
    _PHOENIX_LOGGER_PREFIXES,
    _scrub_log_text,
    _strict_diagnostic_text,
    redact_diagnostics,
    redact_secrets_in_text,
)


def test_scrubs_phx_access_key():
    raw = "auth failed for phx_" + "a" * 64
    assert "phx_" + "a" * 64 not in _scrub_log_text(raw)
    assert "<phoenix-access-key>" in _scrub_log_text(raw)


def test_access_key_scrub_pattern_tracks_the_real_access_key_prefix():
    """The scrubber must match the prefix access keys are actually minted with.

    A drifted pattern fails open: live bearer tokens would flow through to a
    caller holding cap_log_read and nothing raises.
    """
    minted = ACCESS_KEY_PREFIX + "b" * 64
    assert minted not in _scrub_log_text(f"Bearer {minted} rejected")


def test_logger_prefixes_match_the_integration_module_path():
    """Log filtering keys off this module path; a stale value silently matches nothing."""
    assert any(p.endswith(f".{DOMAIN}") for p in _PHOENIX_LOGGER_PREFIXES)
    assert f"custom_components.{DOMAIN}" in _PHOENIX_LOGGER_PREFIXES


def test_scrubs_jwt_llat():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJoYSJ9.abc123signaturepart"
    out = _scrub_log_text(f"rejected token {jwt} from client")
    assert jwt not in out
    assert "<token>" in out


def test_scrubs_url_query_credentials():
    out = _scrub_log_text("GET https://api.example.com/v1?access_token=SEKRET&page=2")
    assert "SEKRET" not in out
    assert "access_token=<redacted>" in out
    assert "page=2" in out


def test_scrubs_userinfo_credentials():
    out = _scrub_log_text("connecting to https://admin:hunter2@db.local/x")
    assert "hunter2" not in out
    assert "://<redacted>@" in out


def test_preserves_benign_text():
    raw = "Setup of sensor.kitchen took 1.2 seconds"
    assert _scrub_log_text(raw) == raw


def test_redact_secrets_in_text_yaml_keys():
    diff = "name: My HA\npassword: hunter2\napi_key: abcd1234\nlatitude: 51.5"
    out = redact_secrets_in_text(diff)
    assert "hunter2" not in out
    assert "abcd1234" not in out
    assert "password: <redacted>" in out
    assert "api_key: <redacted>" in out
    assert "latitude: 51.5" in out  # benign key preserved


def test_redact_secrets_in_text_none_passthrough():
    assert redact_secrets_in_text(None) is None
    assert redact_secrets_in_text("") == ""


@pytest.mark.parametrize("source", [
    "this.session = 0;", "this._session=0;", "this.sessionCounter = 0;",
    "this.tokenCount=0;", "this.secretRevision = 0;", "this.sessionGeneration=0;",
    "this.accessToken = null;", "this.session = undefined;", "this.secret = {};",
    "this.credentials = [ ];", 'this.password = "";', "this.api_key = '';",
    "this.session = false;", "this.session = true;", "let token = null;",
    "const credentials = {};", "var session = 0;",
    "this.session = 0", "this.accessToken=null", "let session = 0",
])
def test_empty_code_initializers_are_not_credentials(source):
    assert redact_secrets_in_text(source) == source


@pytest.mark.parametrize("source", [
    'this.session = "actual-session-value";', "this.password = 1234;",
    "this.password = 0;", "this.access_token = 1234;", "this.token = 0;",
    'this.credentials = {password: "hunter2"};', 'this.tokens = ["abc"];',
    'this.session = 0; this.password = "hunter2";',
    'this.session = 0; // token=abc', 'this.session = ""; // password=abc',
    "session: 0", "password: false", "api_key: 0", "session = 0",
    "this.session = phx_" + "a" * 64 + ";",
    "this.phx_" + "a" * 64 + " = null;",
])
def test_initializer_exception_cannot_admit_credential_values(source):
    assert redact_secrets_in_text(source) != source


def test_redact_diagnostics_scrubs_topology_conservatively():
    obj = {
        "a": "10.0.0.5",                 # private IPv4
        "b": "192.168.1.1",              # private IPv4
        "c": "172.20.3.4",               # private IPv4 (172.16/12)
        "d": "fe80::1ff:fe23:4567:890a", # link-local IPv6
        "e": "http://nas.local:8080/x",  # bare URL host
        "f": "/config/.storage/db",      # absolute unix path
        "g": r"C:\HA\config\secrets",    # windows path
        "ok": "reachable",               # benign
        "ver": "4.8.0.1",                # public-IP-shaped version: NOT private, keep
        "pub": "8.8.8.8",                # public IP: not a private range, keep
        "frac": "ratio 1/2 done",        # lone slash: not a path, keep
    }
    out = redact_diagnostics(obj)
    assert out["a"] == "<redacted-ip>"
    assert out["b"] == "<redacted-ip>"
    assert out["c"] == "<redacted-ip>"
    assert out["d"] == "<redacted-ip>"
    assert out["e"] == "<redacted-url>"
    assert out["f"] == "<redacted-path>"
    assert out["g"] == "<redacted-path>"
    assert out["ok"] == "reachable"
    assert out["ver"] == "4.8.0.1"
    assert out["pub"] == "8.8.8.8"
    assert out["frac"] == "ratio 1/2 done"


def test_redact_diagnostics_scrubs_topology_in_dict_keys():
    # An integration may key its free-form health data by a URL, LAN IP, or path;
    # keys get the same scrub as values so topology cannot leak through the key.
    out = redact_diagnostics({
        "http://nas.local:8080/status": "ok",
        "/config/.storage/core": "ok",
        "192.168.1.50": "reachable",
        "benign_label": "ok",
    })
    assert out == {
        "<redacted-url>": "ok",
        "<redacted-path>": "ok",
        "<redacted-ip>": "reachable",
        "benign_label": "ok",
    }
    # A sensitive-named key still redacts its value (key name is a harmless label).
    assert redact_diagnostics({"password": "hunter2"}) == {"password": "<redacted>"}


_PRIVATE_SHAPES = {
    "nas.home.lan": "<redacted-host>",
    "NAS.Home.LAN:5000 is up": "<redacted-host>:5000 is up",
    "machine.tail1234.ts.net": "<redacted-host>",
    "192-168-1-5.0123456789abcdef.plex.direct:32400": "<redacted-host>:32400",
    "peer 100.101.102.103 joined": "peer <redacted-ip> joined",
    "100.64.0.1": "<redacted-ip>",
    "100.127.255.254": "<redacted-ip>",
    "host='192.168.1.5'": "host='<redacted-ip>'",
}
# Values that only resemble the new shapes and must stay readable.
_LOOKALIKES = (
    "wlan0", "sports.net", "4.8.0.1", "2026.10.3", "100.63.255.255", "100.128.0.1",
    "lan", "ts.net", "plex.direct", "nas.home.lan.example.com",
)


async def test_private_hostnames_and_the_shared_range_are_scrubbed(hass):
    for text, expected in _PRIVATE_SHAPES.items():
        assert redact_diagnostics(text) == expected, text
        assert _strict_diagnostic_text(text) == expected, text
    for text in _LOOKALIKES:
        assert redact_diagnostics(text) == text, text
        assert _strict_diagnostic_text(text) == text, text
    assert redact_diagnostics({"nas.home.lan": "reachable"}) == {"<redacted-host>": "reachable"}


def _adapter(name: str, ipv4: list[str], ipv6: list[str]) -> Adapter:
    return Adapter(
        name=name, index=1, enabled=True, auto=True, default=name == "eth0",
        ipv4=[IPv4ConfiguredAddress(address=a, network_prefix=24) for a in ipv4],
        ipv6=[IPv6ConfiguredAddress(address=a, flowinfo=0, scope_id=0, network_prefix=64) for a in ipv6],
    )


@pytest.fixture
async def instance(hass, monkeypatch):
    """An instance with a single-label internal URL, a public external URL and two adapters.

    The test harness already replaces the network integration's adapter lookup
    with a fixed list; this replaces it at the same seam with these adapters.
    """
    await hass.config.async_update(
        internal_url="http://homeassistant:8123", external_url="https://myhome.duckdns.org:8123")
    adapters = [_adapter("eth0", ["203.0.113.7"], ["2001:db8::7"]), _adapter("lo", ["127.0.0.1"], ["::1"])]
    monkeypatch.setattr(network, "async_get_loaded_adapters", lambda _hass: adapters)
    return hass


async def test_the_instances_own_identifiers_are_scrubbed_by_exact_value(instance):
    text = "reached myhome.duckdns.org:8123 from 203.0.113.7 and [2001:db8::7]:8123"
    expected = "reached <redacted-host>:8123 from <redacted-ip> and [<redacted-ip>]:8123"
    assert redact_diagnostics(text) == expected
    assert _strict_diagnostic_text(text) == expected
    assert redact_diagnostics({"MyHome.DuckDNS.org": "ok"}) == {"<redacted-host>": "ok"}
    # Loopback and the single-label internal name stay readable, and a known
    # value inside a longer hostname or address is a different identifier.
    for kept in ("homeassistant:8123", "::1 up", "notmyhome.duckdns.org", "myhome.duckdns.org.example.com",
                 "203.0.113.70", "203.0.113.7.sslip.io", "2001:db8::70", "version 2026.10.3"):
        assert redact_diagnostics(kept) == kept, kept


async def test_identifiers_are_read_again_on_every_call(instance):
    assert redact_diagnostics("myhome.duckdns.org") == "<redacted-host>"
    await instance.config.async_update(external_url="https://ha.example.net")
    assert redact_diagnostics("myhome.duckdns.org") == "myhome.duckdns.org"
    assert redact_diagnostics("ha.example.net") == "<redacted-host>"


async def test_every_cloud_host_is_scrubbed_with_a_custom_domain(hass, monkeypatch):
    # From 2026.11 get_url answers with the custom domain, so the instance's own host
    # is only in the full list.
    urls = ["https://home.example.net", "https://abcdefgh.ui.nabu.casa"]
    cloud = types.ModuleType("homeassistant.components.cloud")
    cloud.CloudNotAvailable = type("CloudNotAvailable", (Exception,), {})
    cloud.async_remote_ui_url = lambda hass: urls[-1]
    cloud.async_remote_ui_urls = lambda hass: urls
    monkeypatch.setitem(sys.modules, "homeassistant.components.cloud", cloud)
    monkeypatch.setattr(homeassistant.components, "cloud", cloud, raising=False)
    hass.config.components.add("cloud")
    assert redact_diagnostics("home.example.net abcdefgh.ui.nabu.casa") == "<redacted-host> <redacted-host>"


async def test_the_cloud_remote_host_is_scrubbed(hass, monkeypatch):
    # The installed cloud component cannot import here (its TTS dependency is
    # missing), so get_url's own cloud lookup reaches this stand-in module.
    cloud = types.ModuleType("homeassistant.components.cloud")
    cloud.CloudNotAvailable = type("CloudNotAvailable", (Exception,), {})
    cloud.async_remote_ui_url = lambda hass: "https://abcdefgh.ui.nabu.casa"
    cloud.async_remote_ui_urls = lambda hass: ["https://abcdefgh.ui.nabu.casa"]
    monkeypatch.setitem(sys.modules, "homeassistant.components.cloud", cloud)
    hass.config.components.add("cloud")
    assert redact_diagnostics("remote abcdefgh.ui.nabu.casa") == "remote <redacted-host>"


async def test_a_failed_identifier_read_never_fails_the_scrub(instance, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("read failed")

    monkeypatch.setattr(helpers, "get_url", broken)
    monkeypatch.setattr(network, "async_get_loaded_adapters", broken)
    assert redact_diagnostics("myhome.duckdns.org and nas.home.lan") == "myhome.duckdns.org and <redacted-host>"


async def test_off_the_event_loop_no_identifiers_are_added(instance):
    # Every caller scrubs on the event loop; an executor thread has no Home
    # Assistant context, so only the pattern-based scrub applies there.
    out = await instance.async_add_executor_job(redact_diagnostics, "myhome.duckdns.org via nas.home.lan")
    assert out == "myhome.duckdns.org via <redacted-host>"


def test_a_repeated_failure_warns_once_until_it_recovers(caplog):
    import logging

    from custom_components.phoenix_mcp.helpers import RepeatedFailureLog

    logger = logging.getLogger("custom_components.phoenix_mcp.test_repeat")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    failures = RepeatedFailureLog(logger, "Flush failed")

    def warnings():
        return [r for r in caplog.records if r.levelno == logging.WARNING]

    error = OSError("http://user:secret@host/")
    failures.failed(error)
    failures.failed(error)
    assert len(warnings()) == 1 and "OSError" in caplog.text
    assert "secret" not in warnings()[0].getMessage()
    failures.recovered()
    failures.failed(error)
    assert len(warnings()) == 2


@pytest.mark.parametrize("module", [
    "mcp_transport.py", "tool_dispatch.py", "tools/approvals.py", "tools/backup.py", "tools/history.py",
    "tools/integrations.py", "tools/logs.py", "tools/registry.py", "tools/restore.py", "tools/service.py",
    "tools/system.py", "ai_task.py", "tools/radio.py",
])
def test_no_error_line_logs_an_exception_unscrubbed(module):
    """Third-party errors can carry hosts or credentials, and this log gets pasted into issues."""
    import ast
    from pathlib import Path

    source = (Path(helpers.__file__).parent / module).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "error" and getattr(node.func.value, "id", "") == "_LOGGER"):
            bare = [arg.id for arg in node.args if isinstance(arg, ast.Name) and arg.id in ("exc", "err", "error")]
            assert not bare, f"line {node.lineno}"
            assert not any(keyword.arg == "exc_info" for keyword in node.keywords), f"line {node.lineno}"
