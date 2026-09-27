"""Domain-control proof: a provider shows it controls the hosts its upstreams name.

For every upstream host the provider publishes a TXT record at
`_agent-marketplace.<host>` whose value is `agent-marketplace-verification=<token>`,
with the account's domain token. The token is the same for every host and never
changes, so a record published once keeps proving control. Publishing checks every
host of the service, live; another account's record never matches.
"""

import asyncio
from collections.abc import Collection
from enum import StrEnum
from secrets import token_urlsafe

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus
from app.core.json_types import JsonObject
from app.db.models import ProviderDomainToken
from app.integrations.providers.dns import DnsLookupError, DnsResolver
from app.integrations.providers.targets import (
    UnsafeUpstreamTargetError,
    resolve_public_addresses,
)
from app.services.service_health import ServiceHealthOutcome

RECORD_LABEL = "_agent-marketplace"


class HostProof(StrEnum):
    VERIFIED = "verified"
    # No address, a failed address lookup, or a non-public address.
    NOT_PUBLIC = "not_public"
    RECORD_MISSING = "record_missing"
    LOOKUP_FAILED = "lookup_failed"


def record_value(token: str) -> str:
    return f"agent-marketplace-verification={token}"


async def ensure_domain_token(*, session: AsyncSession, account_id: int) -> str:
    """Return the account's domain token, creating it on the first request."""
    token = (
        await session.execute(
            insert(ProviderDomainToken)
            .values(account_id=account_id, token=token_urlsafe(32))
            # The no-op update makes RETURNING yield the stored token when there is one,
            # so concurrent first requests all return the token that won.
            .on_conflict_do_update(
                index_elements=[ProviderDomainToken.account_id],
                set_={"token": ProviderDomainToken.token},
            )
            .returning(ProviderDomainToken.token),
        )
    ).scalar_one()
    await session.commit()
    return token


async def check_domain_control(
    *,
    resolver: DnsResolver,
    token: str | None,
    hosts: Collection[str],
) -> ServiceHealthOutcome:
    """Check live that every host is public and carries the provider's record."""
    if token is None:
        return ServiceHealthOutcome(
            status=ServiceHealthStatus.FAIL,
            summary=(
                "the provider has no domain verification token; request one with "
                "POST /v1/provider/domain-verification"
            ),
        )
    ordered_hosts = sorted(hosts)
    proofs = dict(
        zip(
            ordered_hosts,
            await asyncio.gather(
                *(
                    _prove_host(host, resolver=resolver, expected=record_value(token))
                    for host in ordered_hosts
                ),
            ),
            strict=True,
        ),
    )
    details: JsonObject = {"hosts": {host: proof.value for host, proof in proofs.items()}}
    failures = [
        f"{host} ({proof})" for host, proof in proofs.items() if proof is not HostProof.VERIFIED
    ]
    if failures:
        return ServiceHealthOutcome(
            status=ServiceHealthStatus.FAIL,
            summary=(
                f"upstream hosts failed the domain-control check: {', '.join(failures)}; "
                f"publish a TXT record at {RECORD_LABEL}.<host> with the value from "
                "POST /v1/provider/domain-verification"
            ),
            details=details,
        )
    return ServiceHealthOutcome(
        status=ServiceHealthStatus.PASS,
        summary="the provider controls every upstream host",
        details=details,
    )


async def _prove_host(host: str, *, resolver: DnsResolver, expected: str) -> HostProof:
    try:
        await resolve_public_addresses(host, resolver=resolver)
    except UnsafeUpstreamTargetError:
        return HostProof.NOT_PUBLIC
    try:
        values = await resolver.resolve_txt(f"{RECORD_LABEL}.{host}")
    except DnsLookupError:
        return HostProof.LOOKUP_FAILED
    if expected in (value.strip() for value in values):
        return HostProof.VERIFIED
    return HostProof.RECORD_MISSING
