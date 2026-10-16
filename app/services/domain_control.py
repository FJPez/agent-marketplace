"""Domain-control proof: every upstream host carries the provider's TXT record."""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account
from app.integrations.providers.dns import DnsLookupError, DnsResolver

RECORD_LABEL = "_agent-marketplace"
# Bounds the DNS queries one publish sends.
MAX_HOSTS = 10


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    """The TXT record a provider publishes at `<label>.<host>` for every upstream host."""

    label: str
    value: str


async def get_verification_record(*, session: AsyncSession, account_id: int) -> VerificationRecord:
    token = (
        await session.execute(select(Account.domain_token).where(Account.id == account_id))
    ).scalar_one()
    return VerificationRecord(label=RECORD_LABEL, value=f"agent-marketplace-verification={token}")


async def check_domain_control(
    *,
    resolver: DnsResolver,
    record: VerificationRecord,
    hosts: Collection[str],
) -> list[str]:
    """Why the provider has not proved it controls `hosts`; empty when it has."""
    if len(hosts) > MAX_HOSTS:
        return [f"a service's upstreams can name at most {MAX_HOSTS} hosts"]
    async with asyncio.TaskGroup() as checks:
        tasks = [
            checks.create_task(_host_problem(host, resolver=resolver, record=record))
            for host in sorted(hosts)
        ]
    return [problem for task in tasks if (problem := task.result()) is not None]


async def _host_problem(
    host: str,
    *,
    resolver: DnsResolver,
    record: VerificationRecord,
) -> str | None:
    record_name = f"{record.label}.{host}"
    try:
        addresses = await resolver.resolve_ipv4(host)
        published = await resolver.resolve_txt(record_name)
    except DnsLookupError:
        return f"the DNS lookup for {host} failed; retry"
    if not addresses or not all(a.is_global and not a.is_multicast for a in addresses):
        return f"{host} must resolve only to public IPv4 addresses"
    if record.value not in (value.strip() for value in published):
        return f"{host} must carry the TXT record {record_name}"
    return None
