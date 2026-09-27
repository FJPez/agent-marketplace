"""SSRF-safe validation of provider upstream URLs.

An upstream is reachable only over HTTPS on port 443, at a DNS name (never an IP
literal) whose every A and AAAA address is public. The validated addresses are
returned so the proxy connects to one of them instead of resolving the host again,
and a DNS answer that changes after validation cannot redirect the request.
"""

import re
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, IPv6Network
from urllib.parse import urlsplit

from app.integrations.providers.dns import DnsLookupError, DnsResolver, IpAddress

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# Two or more labels, the last starting with a letter as every top-level domain does,
# so no IPv4 form (dotted, decimal or hex) and no single-label name such as localhost.
_DNS_NAME = re.compile(rf"(?:{_LABEL}\.)+(?=[a-z]){_LABEL}")
_DNS_NAME_MAX_LENGTH = 253
# IPv6 prefixes that carry an IPv4 address in their low 32 bits: the deprecated
# IPv4-compatible form, which Python counts as global, and NAT64's well-known prefix.
_IPV4_COMPATIBLE = IPv6Network("::/96")
_NAT64 = IPv6Network("64:ff9b::/96")


class UnsafeUpstreamTargetError(ValueError):
    """The upstream URL or the addresses its host resolves to are not allowed."""


@dataclass(frozen=True, slots=True)
class UpstreamTarget:
    """A validated upstream: connect to one of `addresses`, send `host` for TLS."""

    base_url: str
    host: str
    addresses: tuple[IpAddress, ...]


async def resolve_upstream_target(base_url: str, *, resolver: DnsResolver) -> UpstreamTarget:
    parts = urlsplit(base_url)
    if parts.scheme != "https":
        raise UnsafeUpstreamTargetError("upstream base_url must use https")
    if "@" in parts.netloc:
        raise UnsafeUpstreamTargetError("upstream base_url must not carry credentials")
    if "?" in base_url or "#" in base_url:
        raise UnsafeUpstreamTargetError(
            "upstream base_url must not carry a query string or fragment",
        )
    try:
        on_https_port = parts.port in {None, 443}
    except ValueError:  # a port that is not a number or is out of range
        on_https_port = False
    if not on_https_port:
        raise UnsafeUpstreamTargetError("upstream base_url must use the https port 443")
    host = parts.hostname or ""
    if len(host) > _DNS_NAME_MAX_LENGTH or not _DNS_NAME.fullmatch(host):
        raise UnsafeUpstreamTargetError(
            "upstream host must be a DNS name such as api.example.com, not an IP address",
        )
    return UpstreamTarget(
        base_url=base_url,
        host=host,
        addresses=await resolve_public_addresses(host, resolver=resolver),
    )


async def resolve_public_addresses(host: str, *, resolver: DnsResolver) -> tuple[IpAddress, ...]:
    """Every address of `host`, provided there is one and all of them are public."""
    try:
        addresses = await resolver.resolve_addresses(host)
    except DnsLookupError as exc:
        raise _not_public(host) from exc
    if not addresses or not all(_is_public(address) for address in addresses):
        raise _not_public(host)
    return tuple(addresses)


def _not_public(host: str) -> UnsafeUpstreamTargetError:
    # One message for no answer, a failed lookup and a non-public answer, so the check
    # never tells a provider which internal names exist.
    return UnsafeUpstreamTargetError(
        f"upstream host {host} must resolve, and only to public addresses",
    )


def _is_public(address: IpAddress) -> bool:
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            return _is_public(address.ipv4_mapped)
        if address in _NAT64:
            return _is_public(IPv4Address(int(address) & 0xFFFF_FFFF))
        if address in _IPV4_COMPATIBLE:
            return False
    # Multicast addresses count as global, but no upstream is one.
    return address.is_global and not address.is_multicast
