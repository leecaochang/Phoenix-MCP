"""Which network addresses a request Phoenix makes on someone else's behalf may reach.

Shared by provider setup and by the OAuth client-metadata fetch, whose address an app
chooses: both must not be steered at this machine, the home network or cloud metadata.
"""

from __future__ import annotations

import ipaddress

# Cloud metadata endpoints that the link-local and reserved ranges do not cover.
METADATA_ADDRESSES = frozenset(ipaddress.ip_address(address) for address in (
    "100.100.100.200", "168.63.129.16", "192.0.0.192", "fd00:ec2::254",
))


def ip_is_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool,
) -> bool:
    """Return whether an operator-supplied endpoint may not target this address.

    IPv4-carrying IPv6 is judged by its IPv4, and Teredo by its client as well as itself.
    Remote providers must be globally routable; a local server is refused only metadata
    addresses. Loopback comes first: ::1 counts as reserved. Deprecated site-local fec0::/10
    is private, though Python counts it as global.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if carried := ip.ipv4_mapped or ip.sixtofour:
            return ip_is_blocked(carried, allow_private=allow_private)
        if ip.teredo and ip_is_blocked(ip.teredo[1], allow_private=allow_private):
            return True
    if ip.is_loopback:
        return not allow_private
    return (
        ip in METADATA_ADDRESSES
        or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        or (not allow_private and (not ip.is_global or getattr(ip, "is_site_local", False)))
    )
