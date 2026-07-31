"""Outbound URL policy for the scraper.

The scraper fetches user-supplied URLs from inside the deployment network. On
the Raspberry Pi that network also hosts Jenkins and other services on
localhost, and Tailscale peers on the carrier-grade NAT range - so an unguarded
fetch is a server-side request forgery primitive. Every URL is checked here
before a request is made, and again for each redirect hop.

Known limitation: the hostname is resolved for validation and resolved again by
the HTTP client when it connects. A DNS entry that changes between the two
(rebinding) is not caught. Closing that gap requires pinning the connection to
the validated address, which breaks TLS SNI for virtual-hosted sites; given this
service is single-tenant and self-hosted, per-hop validation is the accepted
tradeoff.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Names that resolve inside a private network or to a cloud metadata service.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa")

# Ranges that ipaddress does not already flag via is_private / is_loopback /
# is_link_local / is_multicast / is_reserved / is_unspecified.
EXTRA_BLOCKED_NETWORKS = (
    # Carrier-grade NAT. Not flagged as private by the stdlib, and this is the
    # range Tailscale hands out - so without it, one Tailscale peer could be
    # made to fetch another.
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64
)


class BlockedURLError(ValueError):
    """Raised when a URL is refused by policy."""


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a human-readable reason if this address is off limits."""
    # ::ffff:127.0.0.1 and friends must be judged on the embedded IPv4 address.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        return "link-local address"  # includes 169.254.169.254 metadata
    if ip.is_private:
        return "private address"  # includes 10/8, 172.16/12, 192.168/16, 100.64/10
    if ip.is_multicast:
        return "multicast address"
    if ip.is_reserved:
        return "reserved address"
    if ip.is_unspecified:
        return "unspecified address"
    if any(ip in net for net in EXTRA_BLOCKED_NETWORKS if ip.version == net.version):
        return "reserved range"
    return None


def _resolve(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address the hostname resolves to. Raises BlockedURLError on failure.

    A hostname that is already a literal address is returned as-is rather than
    handed to the resolver, which would be pointless and, with a hostile or
    misconfigured resolver, could return something different from what was asked.
    """
    try:
        return [ipaddress.ip_address(hostname.strip("[]"))]
    except ValueError:
        pass  # not a literal address - fall through to DNS

    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise BlockedURLError(f"Could not resolve host: {hostname}") from e
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def validate_url(url: str) -> str:
    """Check a URL against the outbound policy.

    Returns the URL unchanged when allowed; raises BlockedURLError otherwise.
    Every address the hostname resolves to must pass - a name resolving to both
    a public and a private address is refused.
    """
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise BlockedURLError("URL has no host")

    if not settings.scraper_block_private_networks:
        return url

    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(BLOCKED_HOST_SUFFIXES):
        raise BlockedURLError(f"Refusing to fetch internal host: {hostname}")

    # A literal IP in the URL still goes through _resolve, which passes it
    # straight back - so the same checks apply either way.
    for ip in _resolve(hostname):
        reason = _is_blocked_address(ip)
        if reason is not None:
            raise BlockedURLError(f"Refusing to fetch {hostname} ({ip}): {reason}")

    return url
