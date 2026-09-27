"""DNS lookups for provider hosts, behind an interface tests replace with a fake."""

import asyncio
from collections.abc import Sequence
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver
from dns.rdata import Rdata

type IpAddress = IPv4Address | IPv6Address


class DnsLookupError(Exception):
    """A lookup failed (timeout, server failure) rather than finding no records."""


class DnsResolver(Protocol):
    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        """Every A and AAAA address of `host`; empty when it has none."""
        ...


class DnsPythonResolver:
    """Resolves with dnspython's resolver, queried directly (no hosts file or search list)."""

    def __init__(self, resolver: dns.asyncresolver.Resolver) -> None:
        self._resolver = resolver

    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        ipv4, ipv6 = await asyncio.gather(self._query(host, "A"), self._query(host, "AAAA"))
        return [ip_address(record.to_text()) for record in (*ipv4, *ipv6)]

    async def _query(self, name: str, record_type: str) -> Sequence[Rdata]:
        try:
            answer = await self._resolver.resolve(name, record_type, search=False)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return ()
        except dns.exception.DNSException as exc:
            raise DnsLookupError(f"DNS lookup for {name} failed") from exc
        return tuple(answer)
