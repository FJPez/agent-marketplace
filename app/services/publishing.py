"""Publish transition for provider services."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError
from app.db.models.provider_domain_token import ProviderDomainToken
from app.db.models.provider_signing_secret import ProviderSigningSecret
from app.db.models.service import Service
from app.integrations.providers.dns import DnsResolver
from app.services import domain_control, moderation, revisions, service_access, service_health
from app.services.moderation import ServiceUnavailableError
from app.services.publish_readiness import validate_service_for_publish
from app.services.service_health import (
    DOMAIN_CONTROL_CHECK_NAME,
    PUBLISH_READINESS_CHECK_NAME,
    ServiceHealthOutcome,
)


async def publish_service(
    *,
    session: AsyncSession,
    resolver: DnsResolver,
    account_id: int,
    service_id: int,
) -> Service:
    # The domain-control check resolves DNS, so it runs first, between two
    # transactions: read what it needs, end that transaction, check, then lock.
    unlocked = await service_access.load_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    # An early answer for a service that cannot be published, so it sends no DNS query.
    # The same gates run again under the lock, and those decide.
    await _ensure_publishable_state(session=session, service=unlocked)
    hosts = _upstream_hosts(unlocked)
    domain_token = await session.scalar(
        select(ProviderDomainToken.token).where(ProviderDomainToken.account_id == account_id),
    )
    await session.rollback()
    domain_control_outcome = await domain_control.check_domain_control(
        resolver=resolver,
        token=domain_token,
        hosts=hosts,
    )

    service = await service_access.load_owned_service_for_update(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    await _ensure_publishable_state(session=session, service=service)

    has_signing_secret = await session.get(ProviderSigningSecret, account_id) is not None

    # Stamped after the lock wait and the gates so the timestamp reflects when the
    # row was actually mutated. One clock for the whole operation: the check rows and
    # the service all carry this value.
    now = datetime.now(UTC)
    try:
        validate_service_for_publish(service, has_signing_secret=has_signing_secret)
    except InvalidInputError as exc:
        await _record_rejection(
            session=session,
            service_id=service.id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            outcome=ServiceHealthOutcome(status=ServiceHealthStatus.FAIL, summary=str(exc)),
            checked_at=now,
        )
        raise

    if _upstream_hosts(service) != hosts:
        # The check above proved other hosts than the ones now stored.
        raise ConflictError("the service's upstreams changed while publishing; publish again")
    if domain_control_outcome.status is not ServiceHealthStatus.PASS:
        await _record_rejection(
            session=session,
            service_id=service.id,
            check_name=DOMAIN_CONTROL_CHECK_NAME,
            outcome=domain_control_outcome,
            checked_at=now,
        )
        raise InvalidInputError(domain_control_outcome.summary)

    await service_health.record_check(
        session=session,
        service_id=service.id,
        check_name=PUBLISH_READINESS_CHECK_NAME,
        outcome=ServiceHealthOutcome(
            status=ServiceHealthStatus.PASS,
            summary="service is publish-ready",
            details={
                "enabled_endpoint_count": len(
                    [endpoint for endpoint in service.endpoints if endpoint.is_enabled],
                ),
            },
        ),
        checked_at=now,
    )
    await service_health.record_check(
        session=session,
        service_id=service.id,
        check_name=DOMAIN_CONTROL_CHECK_NAME,
        outcome=domain_control_outcome,
        checked_at=now,
    )

    if service.current_revision_id is None or service.current_change_token is None:
        await revisions.create_revision(session=session, service=service)
    service.lifecycle = ServiceLifecycle.ACTIVE
    service.updated_at = now
    await session.commit()
    return service


async def _ensure_publishable_state(*, session: AsyncSession, service: Service) -> None:
    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not publishable outside draft")
    try:
        await moderation.ensure_service_publishable(session=session, service_id=service.id)
    except ServiceUnavailableError as exc:
        raise InvalidStateError(f"service is {exc.state.value}") from exc


async def _record_rejection(
    *,
    session: AsyncSession,
    service_id: int,
    check_name: str,
    outcome: ServiceHealthOutcome,
    checked_at: datetime,
) -> None:
    await service_health.record_check(
        session=session,
        service_id=service_id,
        check_name=check_name,
        outcome=outcome,
        checked_at=checked_at,
    )
    # Deliberate commit on the failure path: the FAIL row is the attempt's only
    # mutation and must stay visible after the rejection. Nothing may mutate after it.
    await session.commit()


def _upstream_hosts(service: Service) -> frozenset[str]:
    """Every upstream's host, enabled or not: a disabled endpoint can be enabled later."""
    hosts: set[str] = set()
    for endpoint in service.endpoints:
        if endpoint.upstream is None:
            continue
        host = urlsplit(endpoint.upstream.base_url).hostname
        if host is None:
            # The API stores only validated URLs with a host, so this one was written
            # around it. Fail closed rather than publish a host that was never proven.
            raise InvalidInputError(
                f"endpoint '{endpoint.key}' has an upstream with no host; set its upstream again",
            )
        hosts.add(host)
    return frozenset(hosts)
