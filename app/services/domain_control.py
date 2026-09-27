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
from app.core.logging import get_logger
from app.db.models import ProviderDomainToken
from app.integrations.providers.dns import DnsLookupError, DnsResolver
from app.integrations.providers.targets import (
    UnsafeUpstreamTargetError,
    resolve_public_addresses,
)
from app.services.service_health import ServiceHealthOutcome

logger = get_logger(__name__)

RECORD_LABEL = "_agent-marketplace"
# Hosts one check proves at once. A proof sends an A and an AAAA query together, then a
# TXT query, so a check has at most twice this many queries in flight.
MAX_CONCURRENT_HOST_PROOFS = 4


class HostProof(StrEnum):
    VERIFIED = "verified"
    # No address, a failed address lookup, or a non-public address.
    NOT_PUBLIC = "not_public"
    RECORD_MISSING = "record_missing"
    LOOKUP_FAILED = "lookup_failed"


# What the provider does about each failed proof, in the order the summary lists them.
_REMEDIES = {
    HostProof.NOT_PUBLIC: "point the host only at public addresses.",
    HostProof.RECORD_MISSING: (
        f"publish a TXT record at {RECORD_LABEL}.<host> with the value from "
        "POST /v1/provider/domain-verification. DNS changes can take minutes to be "
        "visible, longer after a failed check because resolvers cache the miss "
        "(negative caching)."
    ),
    HostProof.LOOKUP_FAILED: "the DNS lookup failed; retry.",
}


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
    if not hosts:
        return ServiceHealthOutcome(
            status=ServiceHealthStatus.FAIL,
            summary="the service has no upstream hosts to prove control of",
        )
    if token is None:
        return ServiceHealthOutcome(
            status=ServiceHealthStatus.FAIL,
            summary=(
                "the provider has no domain verification token; request one with "
                "POST /v1/provider/domain-verification"
            ),
        )
    expected = record_value(token)
    slots = asyncio.Semaphore(MAX_CONCURRENT_HOST_PROOFS)
    async with asyncio.TaskGroup() as checks:
        proving = {
            host: checks.create_task(
                _prove_host(host, resolver=resolver, expected=expected, slots=slots),
            )
            for host in sorted(hosts)
        }
    proofs = {host: task.result() for host, task in proving.items()}
    details: JsonObject = {"hosts": {host: proof.value for host, proof in proofs.items()}}
    failed = {host: proof for host, proof in proofs.items() if proof is not HostProof.VERIFIED}
    if failed:
        hosts_named = ", ".join(f"{host} ({proof.value})" for host, proof in failed.items())
        remedies = " ".join(
            f"{proof.value}: {remedy}"
            for proof, remedy in _REMEDIES.items()
            if proof in failed.values()
        )
        return ServiceHealthOutcome(
            status=ServiceHealthStatus.FAIL,
            summary=f"upstream hosts failed the domain-control check: {hosts_named}. {remedies}",
            details=details,
        )
    return ServiceHealthOutcome(
        status=ServiceHealthStatus.PASS,
        summary="the provider controls every upstream host",
        details=details,
    )


async def _prove_host(
    host: str,
    *,
    resolver: DnsResolver,
    expected: str,
    slots: asyncio.Semaphore,
) -> HostProof:
    async with slots:
        try:
            await resolve_public_addresses(host, resolver=resolver)
        except UnsafeUpstreamTargetError:
            return HostProof.NOT_PUBLIC
        record_name = f"{RECORD_LABEL}.{host}"
        try:
            values = await resolver.resolve_txt(record_name)
        except DnsLookupError as exc:
            logger.warning(
                "domain verification record lookup failed",
                extra={"record_name": record_name},
                exc_info=exc,
            )
            return HostProof.LOOKUP_FAILED
    if expected in (value.strip() for value in values):
        return HostProof.VERIFIED
    return HostProof.RECORD_MISSING
