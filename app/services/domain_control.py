"""Domain-control proof: every upstream host carries the provider's TXT record."""

import asyncio
from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account
from app.integrations.providers.dns import DnsLookupError, DnsResolver

RECORD_LABEL = "_agent-marketplace"
# Bounds the DNS queries one publish sends.
MAX_HOSTS = 10


def record_value(token: UUID) -> str:
    return f"agent-marketplace-verification={token}"


async def get_domain_token(*, session: AsyncSession, account_id: int) -> UUID:
    return (
        await session.execute(select(Account.domain_token).where(Account.id == account_id))
    ).scalar_one()


async def check_domain_control(
    *,
    resolver: DnsResolver,
    token: UUID,
    hosts: Collection[str],
) -> list[str]:
    """Why the provider has not proved it controls `hosts`; empty when it has."""
    if len(hosts) > MAX_HOSTS:
        return [f"a service's upstreams can name at most {MAX_HOSTS} hosts"]
    async with asyncio.TaskGroup() as checks:
        tasks = [
            checks.create_task(_host_problem(host, resolver=resolver, expected=record_value(token)))
            for host in sorted(hosts)
        ]
    return [problem for task in tasks if (problem := task.result()) is not None]


async def _host_problem(host: str, *, resolver: DnsResolver, expected: str) -> str | None:
    try:
        addresses = await resolver.resolve_ipv4(host)
        records = await resolver.resolve_txt(f"{RECORD_LABEL}.{host}")
    except DnsLookupError:
        return f"the DNS lookup for {host} failed; retry"
    if not addresses or not all(a.is_global and not a.is_multicast for a in addresses):
        return f"{host} must resolve only to public IPv4 addresses"
    if expected not in (record.strip() for record in records):
        return f"{host} must carry the TXT record {RECORD_LABEL}.{host}"
    return None
