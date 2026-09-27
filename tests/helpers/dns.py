from __future__ import annotations

from ipaddress import ip_address
from typing import TYPE_CHECKING

from app.integrations.providers.dns import DnsLookupError

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from app.integrations.providers.dns import IpAddress

# The host every test upstream points at, and the public address it resolves to.
TEST_UPSTREAM_HOST = "provider.example.com"
TEST_UPSTREAM_ADDRESS = "93.184.215.14"
TEST_UPSTREAM_BASE_URL = f"https://{TEST_UPSTREAM_HOST}/"


class FakeResolver:
    """A DnsResolver that answers from its attributes, so no test resolves a real name.

    A name missing from `addresses` or `txt_records` has no such records; a name in
    `failing_names` fails the way a timed-out lookup does. Tests change the attributes
    to change the answers.
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

    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        self._fail_if_failing(host)
        return [ip_address(value) for value in self.addresses.get(host, [])]

    async def resolve_txt(self, name: str) -> list[str]:
        self._fail_if_failing(name)
        return list(self.txt_records.get(name, []))

    def _fail_if_failing(self, name: str) -> None:
        if name in self.failing_names:
            raise DnsLookupError(f"DNS lookup for {name} failed")
