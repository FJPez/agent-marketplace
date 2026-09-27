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
from app.integrations.providers.targets import UnsafeUpstreamTargetError, resolve_public_addresses


class _StubDnsServer(asyncio.DatagramProtocol):
    """Answers each query from `records` (name to (type, value) pairs).

    A CNAME is followed as a recursive server does: the answer holds the whole chain and
    the final name's records. A name in `failing` gets SERVFAIL, a name in `silent` no
    answer at all, and any other name missing from `records` NXDOMAIN; a "<name> <type>"
    entry in `failing` or `silent` applies to that record type only.
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
        record_type = dns.rdatatype.to_text(question.rdtype)
        entries = {name, f"{name} {record_type}"}
        if entries & self.silent:
            return
        response = dns.message.make_response(query)
        if entries & self.failing:
            response.set_rcode(dns.rcode.SERVFAIL)
        elif name not in self.records:
            response.set_rcode(dns.rcode.NXDOMAIN)
        else:
            while cname := self._values(name, "CNAME"):
                response.answer.append(dns.rrset.from_text_list(name, 60, "IN", "CNAME", cname))
                name = cname[0]
            if values := self._values(name, record_type):
                response.answer.append(
                    dns.rrset.from_text_list(name, 60, "IN", record_type, values),
                )
        assert self.transport is not None
        self.transport.sendto(response.to_wire(), addr)

    def _values(self, name: str, record_type: str) -> list[str]:
        return [value for kind, value in self.records.get(name, ()) if kind == record_type]


@pytest.fixture
async def resolver() -> AsyncIterator[DnsPythonResolver]:
    server = _StubDnsServer(
        {
            "dual.provider.example.": [
                ("A", "93.184.215.14"),
                ("A", "10.0.0.1"),
                ("AAAA", "2606:4700:4700::1111"),
            ],
            "alias.provider.example.": [("CNAME", "www.provider.example.")],
            "www.provider.example.": [("CNAME", "dual.provider.example.")],
            "ipv6-only.provider.example.": [("AAAA", "::1")],
            "_agent-marketplace.dual.provider.example.": [
                ("TXT", '"agent-marketplace-" "verification=token"'),
                ("TXT", '"v=spf1 -all"'),
            ],
        },
        failing=frozenset({"broken.provider.example.", "half-broken.provider.example. A"}),
        silent=frozenset({"slow.provider.example.", "half-broken.provider.example. AAAA"}),
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


@pytest.mark.parametrize(
    "host",
    ["dual.provider.example", "alias.provider.example"],
    ids=["direct", "cname_chain"],
)
async def test_resolve_addresses_returns_every_a_and_aaaa_record(
    resolver: DnsPythonResolver,
    host: str,
) -> None:
    addresses = await resolver.resolve_addresses(host)

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


async def test_resolve_txt_joins_the_strings_of_each_record(
    resolver: DnsPythonResolver,
) -> None:
    values = await resolver.resolve_txt("_agent-marketplace.dual.provider.example")

    assert sorted(values) == ["agent-marketplace-verification=token", "v=spf1 -all"]


async def test_resolve_txt_of_a_missing_name_is_no_records(resolver: DnsPythonResolver) -> None:
    assert await resolver.resolve_txt("_agent-marketplace.missing.provider.example") == []


@pytest.mark.parametrize("lookup", ["resolve_addresses", "resolve_txt"])
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


async def test_a_failed_query_cancels_the_other_instead_of_leaving_it_running(
    resolver: DnsPythonResolver,
) -> None:
    tasks_before = asyncio.all_tasks()

    with pytest.raises(
        DnsLookupError, match=r"DNS lookup for half-broken\.provider\.example failed"
    ):
        await resolver.resolve_addresses("half-broken.provider.example")

    # The AAAA query, which would have waited out the lifetime, is already over.
    assert asyncio.all_tasks() == tasks_before


async def test_a_cname_to_a_name_with_a_private_address_is_rejected(
    resolver: DnsPythonResolver,
) -> None:
    with pytest.raises(UnsafeUpstreamTargetError, match="must resolve, and only to public"):
        await resolve_public_addresses("alias.provider.example", resolver=resolver)
