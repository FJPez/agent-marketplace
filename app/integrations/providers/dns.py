"""DNS lookups for provider hosts, behind an interface tests replace with a fake."""

import asyncio
from collections.abc import Sequence
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver
from dns.rdata import Rdata
from dns.rdtypes.txtbase import TXTBase

type IpAddress = IPv4Address | IPv6Address


class DnsLookupError(Exception):
    """A lookup failed (timeout, server failure) rather than finding no records."""


class DnsResolver(Protocol):
    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        """Every A and AAAA address of `host`; empty when it has none."""
        ...

    async def resolve_txt(self, name: str) -> list[str]:
        """Every TXT record at `name`, its strings joined; empty when it has none."""
        ...


class DnsPythonResolver:
    """Resolves with dnspython's resolver, queried directly (no hosts file or search list)."""

    def __init__(self, resolver: dns.asyncresolver.Resolver) -> None:
        self._resolver = resolver

    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        try:
            # A failed query cancels the other instead of leaving it to run out its time.
            async with asyncio.TaskGroup() as queries:
                ipv4_query = queries.create_task(self._query(host, "A"))
                ipv6_query = queries.create_task(self._query(host, "AAAA"))
        except* DnsLookupError as failed:
            raise DnsLookupError(f"DNS lookup for {host} failed") from failed
        ipv4, ipv6 = ipv4_query.result(), ipv6_query.result()
        return [ip_address(record.to_text()) for record in (*ipv4, *ipv6)]

    async def resolve_txt(self, name: str) -> list[str]:
        return [
            # A TXT record is one or more strings of at most 255 bytes, read as one value.
            b"".join(record.strings).decode("utf-8", errors="replace")
            for record in await self._query(name, "TXT")
            # A TXT answer holds only TXT rdata; the check narrows `Rdata` for the type
            # checker, which cannot see `.strings` on it.
            if isinstance(record, TXTBase)
        ]

    async def _query(self, name: str, record_type: str) -> Sequence[Rdata]:
        try:
            answer = await self._resolver.resolve(name, record_type, search=False)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return ()
        except dns.exception.DNSException as exc:
            raise DnsLookupError(f"DNS lookup for {name} failed") from exc
        return tuple(answer)
