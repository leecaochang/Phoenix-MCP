"""Fetch and check an app's Client ID Metadata Document.

An app names itself by an https address that serves a JSON document listing its name and
the addresses it accepts sign-ins at. The address is the app's choice, so the fetch runs
only after an administrator opened the request, follows no redirect, reads a bounded body,
and connects only to public addresses: the resolver drops the request when any answer is
private, so the address checked is the address connected to.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import unicodedata
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import ThreadedResolver

from .const import OAUTH_CLIENT_METADATA_MAX_BYTES, OAUTH_CLIENT_METADATA_TIMEOUT_SECONDS, OAUTH_CLIENT_NAME_MAX_LENGTH
from .helpers import read_bounded_body
from .net_guard import ip_is_blocked
from .oauth_flow import ClientVerdict, redirect_matches

_LOGGER = logging.getLogger(__name__)


class _BlockedAddressError(OSError):
    """The app's address resolved to somewhere Phoenix will not connect."""


class _PublicOnlyResolver(AbstractResolver):
    """Resolve through the system resolver and refuse unless every answer is public."""

    def __init__(self) -> None:
        self._inner = ThreadedResolver()

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET) -> list[ResolveResult]:
        results = await self._inner.resolve(host, port, family)
        for result in results:
            try:
                ip = ipaddress.ip_address(str(result["host"]).split("%", 1)[0])
            except ValueError:
                raise _BlockedAddressError(host) from None
            if ip_is_blocked(ip, allow_private=False):
                raise _BlockedAddressError(host)
        return results

    async def close(self) -> None:
        await self._inner.close()


def _clean_name(raw: Any) -> str:
    """A display name with no control, format or private-use characters, bounded."""
    if not isinstance(raw, str):
        return ""
    kept = "".join(c for c in raw if unicodedata.category(c)[0] not in ("C",))
    return " ".join(kept.split())[:OAUTH_CLIENT_NAME_MAX_LENGTH]


def check_document(document: Any, client_id: str, redirect_uri: str) -> ClientVerdict:
    """Judge a parsed document against the request that named it."""
    if not isinstance(document, dict):
        return ClientVerdict(False, problem="oauthClientDocumentInvalid")
    if document.get("client_id") != client_id:
        return ClientVerdict(False, problem="oauthClientIdMismatch")
    redirect_uris = document.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not any(
        isinstance(uri, str) and redirect_matches(redirect_uri, uri) for uri in redirect_uris
    ):
        return ClientVerdict(False, problem="oauthRedirectNotListed")
    return ClientVerdict(True, name=_clean_name(document.get("client_name")))


async def async_fetch_client_verdict(client_id: str, redirect_uri: str) -> ClientVerdict:
    """Fetch the document at ``client_id`` and judge it; never raises."""
    host = urlsplit(client_id).hostname or ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and ip_is_blocked(literal, allow_private=False):
        return ClientVerdict(False, problem="oauthClientAddressRefused")
    # Local: aiohttp's TLS context through Home Assistant's own helper.
    from homeassistant.util.ssl import client_context
    connector = aiohttp.TCPConnector(resolver=_PublicOnlyResolver(), ssl=client_context(), limit=2)
    timeout = aiohttp.ClientTimeout(total=OAUTH_CLIENT_METADATA_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(
                client_id, allow_redirects=False, headers={"Accept": "application/json"},
            ) as response:
                if response.status != 200:
                    return ClientVerdict(False, problem="oauthClientDocumentUnavailable")
                body = await read_bounded_body(
                    response.content, OAUTH_CLIENT_METADATA_MAX_BYTES, OAUTH_CLIENT_METADATA_TIMEOUT_SECONDS)
                if len(body) > OAUTH_CLIENT_METADATA_MAX_BYTES:
                    return ClientVerdict(False, problem="oauthClientDocumentTooLarge")
    except _BlockedAddressError:
        return ClientVerdict(False, problem="oauthClientAddressRefused")
    except aiohttp.ClientConnectorError as exc:
        if isinstance(exc.os_error, _BlockedAddressError):
            return ClientVerdict(False, problem="oauthClientAddressRefused")
        return ClientVerdict(False, problem="oauthClientDocumentUnavailable")
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):  # UnicodeError: a host IDNA refuses
        return ClientVerdict(False, problem="oauthClientDocumentUnavailable")
    try:
        document = json.loads(body)
    except (ValueError, RecursionError):
        return ClientVerdict(False, problem="oauthClientDocumentInvalid")
    return check_document(document, client_id, redirect_uri)
