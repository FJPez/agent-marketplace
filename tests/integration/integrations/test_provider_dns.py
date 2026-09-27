"""DnsPythonResolver against a DNS server on loopback: no test leaves the machine."""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from ipaddress import ip_address

import dns.asyncresolver
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest

from app.integrations.providers.dns import DnsLookupError, DnsPythonResolver


class _StubDnsServer(asyncio.DatagramProtocol):
    """Answers each query from `records` (name to (type, value) pairs).

    A name in `failing` gets SERVFAIL, a name in `silent` no answer at all, and any
    other name missing from `records` NXDOMAIN.
    """

    def __init__(
        self,
        records: Mapping[str, Sequence[tuple[str, str]]],
        *,
        failing: frozenset[str] = frozenset(),
        silent: frozenset[str] = frozenset(),
    ) -> None:
        self.records = records
        self.failing = failing
        self.silent = silent
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str | int, int]) -> None:
        query = dns.message.from_wire(data)
        question = query.question[0]
        name = question.name.to_text()
        if name in self.silent:
            return
        response = dns.message.make_response(query)
        if name in self.failing:
            response.set_rcode(dns.rcode.SERVFAIL)
        elif name not in self.records:
            response.set_rcode(dns.rcode.NXDOMAIN)
        else:
            record_type = dns.rdatatype.to_text(question.rdtype)
            values = [value for kind, value in self.records[name] if kind == record_type]
            if values:
                response.answer.append(
                    dns.rrset.from_text_list(question.name, 60, "IN", record_type, values),
                )
        assert self.transport is not None
        self.transport.sendto(response.to_wire(), addr)


@pytest.fixture
async def resolver() -> AsyncIterator[DnsPythonResolver]:
    server = _StubDnsServer(
        {
            "dual.provider.example.": [
                ("A", "93.184.215.14"),
                ("A", "10.0.0.1"),
                ("AAAA", "2606:4700:4700::1111"),
            ],
            "ipv6-only.provider.example.": [("AAAA", "::1")],
        },
        failing=frozenset({"broken.provider.example."}),
        silent=frozenset({"slow.provider.example."}),
    )
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: server,
        local_addr=("127.0.0.1", 0),
    )
    stub = dns.asyncresolver.Resolver(configure=False)
    stub.nameservers = ["127.0.0.1"]
    stub.port = transport.get_extra_info("sockname")[1]
    stub.lifetime = 0.5
    try:
        yield DnsPythonResolver(stub)
    finally:
        transport.close()


async def test_resolve_addresses_returns_every_a_and_aaaa_record(
    resolver: DnsPythonResolver,
) -> None:
    addresses = await resolver.resolve_addresses("dual.provider.example")

    assert sorted(addresses, key=str) == sorted(
        [ip_address("93.184.215.14"), ip_address("10.0.0.1"), ip_address("2606:4700:4700::1111")],
        key=str,
    )


@pytest.mark.parametrize(
    ("host", "expected"),
    [("ipv6-only.provider.example", ["::1"]), ("missing.provider.example", [])],
    ids=["no_a_records", "nxdomain"],
)
async def test_a_missing_record_type_or_name_is_no_addresses(
    resolver: DnsPythonResolver,
    host: str,
    expected: list[str],
) -> None:
    assert await resolver.resolve_addresses(host) == [ip_address(value) for value in expected]


@pytest.mark.parametrize(
    "host",
    ["broken.provider.example", "slow.provider.example"],
    ids=["servfail", "timeout"],
)
async def test_a_failed_lookup_raises_instead_of_returning_no_addresses(
    resolver: DnsPythonResolver,
    host: str,
) -> None:
    with pytest.raises(DnsLookupError, match=f"DNS lookup for {host} failed"):
        await resolver.resolve_addresses(host)
