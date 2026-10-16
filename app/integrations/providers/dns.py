"""DNS lookups for provider hosts, behind an interface tests replace with a fake."""

from collections.abc import Sequence
from ipaddress import IPv4Address
from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver
from dns.rdata import Rdata
from dns.rdtypes.txtbase import TXTBase


class DnsLookupError(Exception):
    """A lookup failed (timeout, server failure) rather than finding no records."""


class DnsResolver(Protocol):
    async def resolve_ipv4(self, host: str) -> list[IPv4Address]:
        """Every A address of `host`; empty when it has none."""
        ...

    async def resolve_txt(self, name: str) -> list[str]:
        """Every TXT record at `name`, its strings joined; empty when it has none."""
        ...


class DnsPythonResolver:
    """Resolves with dnspython, queried directly (no hosts file or search list)."""

    def __init__(self, resolver: dns.asyncresolver.Resolver) -> None:
        self._resolver = resolver

    async def resolve_ipv4(self, host: str) -> list[IPv4Address]:
        return [IPv4Address(record.to_text()) for record in await self._query(host, "A")]

    async def resolve_txt(self, name: str) -> list[str]:
        return [
            b"".join(record.strings).decode("utf-8", errors="replace")
            for record in await self._query(name, "TXT")
            # Narrows `Rdata` for the type checker.
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
