from __future__ import annotations

from ipaddress import ip_address
from typing import TYPE_CHECKING

from app.integrations.providers.dns import DnsLookupError
from app.services.domain_control import RECORD_LABEL, record_value

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.integrations.providers.dns import IpAddress

# The host every test upstream points at, and the public address it resolves to.
TEST_UPSTREAM_HOST = "provider.example.com"
TEST_UPSTREAM_ADDRESS = "93.184.215.14"
TEST_UPSTREAM_BASE_URL = f"https://{TEST_UPSTREAM_HOST}/"
# The domain token test providers get, and the TXT record that proves it on the host.
TEST_DOMAIN_TOKEN = "test-domain-token"
TEST_DOMAIN_RECORD_NAME = f"{RECORD_LABEL}.{TEST_UPSTREAM_HOST}"
TEST_DOMAIN_RECORD_VALUE = record_value(TEST_DOMAIN_TOKEN)


class FakeResolver:
    """A DnsResolver that answers from its attributes, so no test resolves a real name.

    A name missing from `addresses` or `txt_records` has no such records; a name in
    `failing_names` fails the way a timed-out lookup does. Tests change the attributes
    to change the answers. `lookups` lists every name looked up, in order.
    """

    def __init__(
        self,
        addresses: Mapping[str, Sequence[str]] | None = None,
        *,
        txt_records: Mapping[str, Sequence[str]] | None = None,
        failing_names: Collection[str] = (),
    ) -> None:
        self.addresses = {host: list(values) for host, values in (addresses or {}).items()}
        self.txt_records = {name: list(values) for name, values in (txt_records or {}).items()}
        self.failing_names = set(failing_names)
        self.lookups: list[str] = []

    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        self.lookups.append(host)
        self._fail_if_failing(host)
        return [ip_address(value) for value in self.addresses.get(host, [])]

    async def resolve_txt(self, name: str) -> list[str]:
        self.lookups.append(name)
        self._fail_if_failing(name)
        return list(self.txt_records.get(name, []))

    def _fail_if_failing(self, name: str) -> None:
        if name in self.failing_names:
            raise DnsLookupError(f"DNS lookup for {name} failed")


class TransactionWatchingResolver(FakeResolver):
    """Answers as `answers` does and records `session.in_transaction()` at every lookup.

    Proves a workflow resolves DNS with no transaction open on `session`.
    """

    def __init__(self, answers: FakeResolver, session: AsyncSession) -> None:
        super().__init__(
            answers.addresses,
            txt_records=answers.txt_records,
            failing_names=answers.failing_names,
        )
        self._session = session
        self.in_transaction_during_lookups: list[bool] = []

    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        self.in_transaction_during_lookups.append(self._session.in_transaction())
        return await super().resolve_addresses(host)

    async def resolve_txt(self, name: str) -> list[str]:
        self.in_transaction_during_lookups.append(self._session.in_transaction())
        return await super().resolve_txt(name)
