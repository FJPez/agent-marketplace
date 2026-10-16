"""DnsPythonResolver against a DNS server on loopback: no test leaves the machine."""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from ipaddress import IPv4Address

import dns.asyncresolver
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest

from app.integrations.providers.dns import DnsLookupError, DnsPythonResolver


class StubDnsServer(asyncio.DatagramProtocol):
    """Answers from `records`; a `failing` name gets SERVFAIL, a `silent` one no answer."""

    def __init__(
        self,
        records: Mapping[str, Sequence[tuple[str, str]]],
        *,
        failing: frozenset[str],
        silent: frozenset[str],
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
        record_type = dns.rdatatype.to_text(question.rdtype)
        values = [value for kind, value in self.records.get(name, ()) if kind == record_type]
        if name in self.failing:
            response.set_rcode(dns.rcode.SERVFAIL)
        elif name not in self.records:
            response.set_rcode(dns.rcode.NXDOMAIN)
        elif values:
            response.answer.append(dns.rrset.from_text_list(name, 60, "IN", record_type, values))
        assert self.transport is not None
        self.transport.sendto(response.to_wire(), addr)


@pytest.fixture
async def resolver() -> AsyncIterator[DnsPythonResolver]:
    server = StubDnsServer(
        {
            "api.provider.example.": [
                ("A", "93.184.215.14"),
                ("A", "10.0.0.1"),
                ("AAAA", "2606:4700:4700::1111"),
            ],
            "ipv6-only.provider.example.": [("AAAA", "2606:4700:4700::1111")],
            "_agent-marketplace.api.provider.example.": [
                ("TXT", '"agent-marketplace-" "verification=token"'),
                ("TXT", '"v=spf1 -all"'),
            ],
        },
        failing=frozenset({"broken.provider.example."}),
        silent=frozenset({"slow.provider.example."}),
    )
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: server, local_addr=("127.0.0.1", 0))
    stub = dns.asyncresolver.Resolver(configure=False)
    stub.nameservers = ["127.0.0.1"]
    stub.port = transport.get_extra_info("sockname")[1]
    stub.lifetime = 0.5
    try:
        yield DnsPythonResolver(stub)
    finally:
        transport.close()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("api.provider.example", [IPv4Address("10.0.0.1"), IPv4Address("93.184.215.14")]),
        ("ipv6-only.provider.example", []),
        ("missing.provider.example", []),
    ],
    ids=["a_records", "no_a_records", "nxdomain"],
)
async def test_resolve_ipv4_returns_the_a_records(
    resolver: DnsPythonResolver,
    host: str,
    expected: list[IPv4Address],
) -> None:
    assert sorted(await resolver.resolve_ipv4(host)) == expected


async def test_resolve_txt_joins_the_strings_of_each_record(
    resolver: DnsPythonResolver,
) -> None:
    values = await resolver.resolve_txt("_agent-marketplace.api.provider.example")

    assert sorted(values) == ["agent-marketplace-verification=token", "v=spf1 -all"]


async def test_resolve_txt_of_a_missing_name_is_no_records(resolver: DnsPythonResolver) -> None:
    assert await resolver.resolve_txt("_agent-marketplace.missing.provider.example") == []


@pytest.mark.parametrize("lookup", ["resolve_ipv4", "resolve_txt"])
@pytest.mark.parametrize(
    "host",
    ["broken.provider.example", "slow.provider.example"],
    ids=["servfail", "timeout"],
)
async def test_a_failed_lookup_raises_instead_of_returning_no_records(
    resolver: DnsPythonResolver,
    lookup: str,
    host: str,
) -> None:
    with pytest.raises(DnsLookupError, match=f"DNS lookup for {host} failed"):
        await getattr(resolver, lookup)(host)
