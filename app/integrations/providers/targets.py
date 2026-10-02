"""SSRF-safe validation of provider upstream URLs.

An upstream is reachable only over HTTPS on port 443, at a DNS name (never an IP
literal) whose every A and AAAA address is public. The validated addresses are
returned so the proxy connects to one of them instead of resolving the host again,
and a DNS answer that changes after validation cannot redirect the request.
"""

import re
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.integrations.providers.dns import DnsLookupError, DnsResolver, IpAddress

logger = get_logger(__name__)

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# Two or more labels, the last starting with a letter as every top-level domain does,
# so no IPv4 form (dotted, decimal or hex) and no single-label name such as localhost.
_DNS_NAME = re.compile(rf"(?:{_LABEL}\.)+(?=[a-z]){_LABEL}")
_DNS_NAME_MAX_LENGTH = 253
_NOT_A_DNS_NAME = "upstream host must be a DNS name such as api.example.com, not an IP address"
# The deprecated 6to4 relay anycast block (RFC 7526): no upstream is served from it,
# but Python's `is_global` still counts it as global.
_SIX_TO_FOUR_RELAY_ANYCAST = IPv4Network("192.88.99.0/24")
# NAT64's well-known prefix carries an IPv4 address in its low 32 bits.
_NAT64 = IPv6Network("64:ff9b::/96")
# Any other IPv6 address must be global unicast outside the special-purpose blocks
# allocated inside it: an allowlist, because Python's `is_global` is True for every
# reserved or unallocated address missing from the special-purpose registry. 3ffe::/16
# is the 6bone test network, returned in 2006 and unrouted since.
_GLOBAL_UNICAST = IPv6Network("2000::/3")
_SPECIAL_IN_GLOBAL_UNICAST = tuple(
    IPv6Network(block)
    for block in ("2001::/23", "2001:db8::/32", "2002::/16", "3ffe::/16", "3fff::/20")
)


class UnsafeUpstreamTargetError(ValueError):
    """The upstream URL or the addresses its host resolves to are not allowed."""


@dataclass(frozen=True, slots=True)
class UpstreamTarget:
    """A validated upstream: connect to one of `addresses`, send `host` for TLS."""

    base_url: str
    host: str
    addresses: tuple[IpAddress, ...]


async def resolve_upstream_target(base_url: str, *, resolver: DnsResolver) -> UpstreamTarget:
    """Validate `base_url` and resolve its host to the addresses to connect to.

    The returned `base_url` is rebuilt from the validated scheme, host and path, so it
    cannot name another host than the one checked.
    """
    # urlsplit drops tabs and newlines anywhere and spaces or controls in front, and
    # reads some non-ASCII characters as ASCII ones, so it could split another URL than
    # the one given.
    if not (base_url.isascii() and base_url.isprintable()) or " " in base_url:
        raise UnsafeUpstreamTargetError(
            "upstream base_url must be printable ASCII without whitespace",
        )
    try:
        parts = urlsplit(base_url)
    except ValueError as exc:  # brackets that are unbalanced or enclose no IP address
        raise UnsafeUpstreamTargetError(_NOT_A_DNS_NAME) from exc
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
    # hostname drops the brackets of an IP literal, which would let "[v1.x]" pass as the
    # name v1.x; urlsplit accepts brackets only in pairs, so "[" finds every literal.
    if "[" in parts.netloc or len(host) > _DNS_NAME_MAX_LENGTH or not _DNS_NAME.fullmatch(host):
        raise UnsafeUpstreamTargetError(_NOT_A_DNS_NAME)
    return UpstreamTarget(
        base_url=f"https://{host}{parts.path}",
        host=host,
        addresses=await resolve_public_addresses(host, resolver=resolver),
    )


async def resolve_public_addresses(host: str, *, resolver: DnsResolver) -> tuple[IpAddress, ...]:
    """The addresses to connect to for `host`, provided it has one and all are public.

    Each address appears once, in the resolver's order, and an IPv4-mapped address as
    the IPv4 address it maps.
    """
    try:
        addresses = await resolver.resolve_addresses(host)
    except DnsLookupError as exc:
        # The provider only learns the host did not resolve; operators see why.
        logger.warning("upstream host lookup failed", extra={"host": host}, exc_info=exc)
        raise _not_public(host) from exc
    pinned = tuple(dict.fromkeys(_unmapped(address) for address in addresses))
    if not pinned or not all(_is_public(address) for address in pinned):
        raise _not_public(host)
    return pinned


def _not_public(host: str) -> UnsafeUpstreamTargetError:
    # One message for no answer, a failed lookup and a non-public answer, so the check
    # never tells a provider which internal names exist.
    return UnsafeUpstreamTargetError(
        f"upstream host {host} must resolve, and only to public addresses",
    )


def _unmapped(address: IpAddress) -> IpAddress:
    """An IPv4-mapped IPv6 address as the IPv4 address it maps; any other unchanged."""
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_public(address: IpAddress) -> bool:
    if isinstance(address, IPv4Address):
        # Multicast addresses count as global, but no upstream is one.
        return (
            address.is_global
            and not address.is_multicast
            and address not in _SIX_TO_FOUR_RELAY_ANYCAST
        )
    if address in _NAT64:
        return _is_public(IPv4Address(address.packed[-4:]))
    if address not in _GLOBAL_UNICAST or any(
        address in block for block in _SPECIAL_IN_GLOBAL_UNICAST
    ):
        return False
    # An ISATAP interface id (RFC 5214) carries an IPv4 address in its low 32 bits.
    if (int(address) >> 32) & 0xFCFF_FFFF == 0x0000_5EFE:
        return _is_public(IPv4Address(address.packed[-4:]))
    return True
