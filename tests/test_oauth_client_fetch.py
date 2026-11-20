"""The network half of the OAuth client-document fetch, against a real local server.

The app chooses the address, so the fetch must connect only to public addresses, follow no
redirect, and read a bounded body that may arrive in pieces. A fake system resolver answers
for a name; the address check, the connector and the reader are Phoenix's own.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from unittest.mock import patch

import pytest
from aiohttp import web

from custom_components.phoenix_mcp import oauth_client_metadata as fetch
from custom_components.phoenix_mcp.const import OAUTH_CLIENT_METADATA_MAX_BYTES
from custom_components.phoenix_mcp.helpers import read_bounded_body
from custom_components.phoenix_mcp.net_guard import ip_is_blocked

REDIRECT = "https://app.example/callback"


class _LocalResolver:
    """Stands in for the system resolver: every name is this machine."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def resolve(self, host, port=0, family=socket.AF_INET):
        self.asked.append(host)
        return [{"hostname": host, "host": "127.0.0.1", "port": port, "family": socket.AF_INET,
                 "proto": 0, "flags": socket.AI_NUMERICHOST}]

    async def close(self) -> None:
        return None


@pytest.fixture
async def server(socket_enabled, unused_tcp_port):
    """A local document server; handlers are registered per test before it starts."""
    app = web.Application()
    runner = web.AppRunner(app)

    async def start(routes):
        for path, handler in routes.items():
            app.router.add_get(path, handler)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
        return f"http://app.test:{unused_tcp_port}"

    yield start
    await runner.cleanup()


def _document(client_id: str, **extra) -> bytes:
    return json.dumps({"client_id": client_id, "redirect_uris": [REDIRECT], "client_name": "App", **extra}).encode()


async def _verdict(client_id: str, *, public: bool = True):
    """Fetch through the fake resolver; ``public`` lets the address check pass this machine."""
    resolver = _LocalResolver()
    with patch.object(fetch, "ThreadedResolver", return_value=resolver):
        if public:
            with patch.object(fetch, "ip_is_blocked", return_value=False):
                return await fetch.async_fetch_client_verdict(client_id, REDIRECT), resolver
        return await fetch.async_fetch_client_verdict(client_id, REDIRECT), resolver


async def test_a_private_answer_is_refused_and_a_public_one_fetched(server):
    async def document(request):
        return web.Response(body=_document(str(request.url)), content_type="application/json")

    base = await server({"/c.json": document})
    refused, resolver = await _verdict(base + "/c.json", public=False)
    assert refused.problem == "oauthClientAddressRefused" and resolver.asked == ["app.test"]
    fetched, _resolver = await _verdict(base + "/c.json")
    assert fetched.ok and fetched.name == "App"


async def test_a_document_sent_in_pieces_is_read_whole(server):
    async def pieces(request):
        body = _document(str(request.url), pad="y" * 4000)
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(body[:len(body) // 2])
        await asyncio.sleep(0.1)
        await response.write(body[len(body) // 2:])
        await response.write_eof()
        return response

    verdict, _resolver = await _verdict(await server({"/c.json": pieces}) + "/c.json")
    assert verdict.ok


async def test_a_document_past_the_cap_is_too_large(server):
    async def large(request):
        return web.Response(body=_document(str(request.url), pad="y" * OAUTH_CLIENT_METADATA_MAX_BYTES),
                            content_type="application/json")

    verdict, _resolver = await _verdict(await server({"/c.json": large}) + "/c.json")
    assert verdict.problem == "oauthClientDocumentTooLarge"


async def test_a_redirect_is_not_followed(server):
    async def moved(_request):
        raise web.HTTPFound("/real.json")

    async def real(request):
        return web.Response(body=_document(str(request.url).replace("/real.json", "/c.json")),
                            content_type="application/json")

    verdict, _resolver = await _verdict(await server({"/c.json": moved, "/real.json": real}) + "/c.json")
    assert verdict.problem == "oauthClientDocumentUnavailable"


async def test_a_failed_answer_is_unavailable(server):
    async def missing(_request):
        return web.Response(status=404)

    verdict, _resolver = await _verdict(await server({"/c.json": missing}) + "/c.json")
    assert verdict.problem == "oauthClientDocumentUnavailable"


async def test_a_document_that_stops_arriving_times_out(server):
    async def stalls(request):
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(b'{"client_id": ')
        await asyncio.sleep(1)
        return response

    base = await server({"/c.json": stalls})
    with patch.object(fetch, "OAUTH_CLIENT_METADATA_TIMEOUT_SECONDS", 0.2):
        verdict, _resolver = await asyncio.wait_for(_verdict(base + "/c.json"), 3)
    assert verdict.problem == "oauthClientDocumentUnavailable"


def test_site_local_ipv6_is_not_public():
    assert ip_is_blocked(ipaddress.ip_address("fec0::1"), allow_private=False)
    assert not ip_is_blocked(ipaddress.ip_address("fec0::1"), allow_private=True)
    assert not ip_is_blocked(ipaddress.ip_address("2606:4700::1"), allow_private=False)


class _ShortReads:
    """A stream that, like aiohttp's, returns whatever is buffered: here one piece per read."""

    def __init__(self, *pieces: bytes) -> None:
        self._pieces = list(pieces)

    async def read(self, n: int) -> bytes:
        return self._pieces.pop(0)[:n] if self._pieces else b""


async def test_the_bounded_reader_reads_to_the_end_and_stops_past_the_cap():
    assert await read_bounded_body(_ShortReads(b"ab", b"cd", b"e"), 10, 1) == b"abcde"
    assert len(await read_bounded_body(_ShortReads(b"abcd", b"efgh", b"ijkl"), 5, 1)) == 6

    class _Stalls:
        async def read(self, _n: int) -> bytes:
            await asyncio.sleep(5)
            return b""

    with pytest.raises(TimeoutError):
        await read_bounded_body(_Stalls(), 10, 0.05)


@pytest.mark.parametrize("host", ["a" * 64 + ".example.com", "ex..ample.com"])
async def test_a_host_the_resolver_cannot_encode_is_unavailable(host):
    """getaddrinfo IDNA-encodes the host first, which raises UnicodeError for such a label."""
    class _EncodingResolver(_LocalResolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            host.encode("idna")
            raise AssertionError("encoded")

    with patch.object(fetch, "ThreadedResolver", return_value=_EncodingResolver()):
        verdict = await fetch.async_fetch_client_verdict(f"https://{host}/c.json", REDIRECT)
    assert verdict.problem == "oauthClientDocumentUnavailable"
