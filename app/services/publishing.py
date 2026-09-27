"""Publish transition for provider services."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError
from app.db.models import ProviderDomainToken, ProviderSigningSecret
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
    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not publishable outside draft")

    try:
        await moderation.ensure_service_publishable(session=session, service_id=service.id)
    except ServiceUnavailableError as exc:
        raise InvalidStateError(f"service is {exc.state.value}") from exc

    has_signing_secret = await session.get(ProviderSigningSecret, account_id) is not None

    # Stamped after the lock wait and the gates so the timestamp reflects when the
    # row was actually mutated. One clock for the whole operation: the check rows and
    # the service all carry this value.
    now = datetime.now(UTC)
    try:
        validate_service_for_publish(service, has_signing_secret=has_signing_secret)
    except InvalidInputError as exc:
        await service_health.record_check(
            session=session,
            service_id=service.id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            outcome=ServiceHealthOutcome(
                status=ServiceHealthStatus.FAIL,
                summary=str(exc),
            ),
            checked_at=now,
        )
        # Deliberate commit on the failure path: the FAIL row is the attempt's
        # only mutation and must stay visible after the rejection. Nothing may
        # mutate after this point.
        await session.commit()
        raise

    if _upstream_hosts(service) != hosts:
        # The check above proved other hosts than the ones now stored.
        raise ConflictError("the service's upstreams changed while publishing; publish again")
    if domain_control_outcome.status is not ServiceHealthStatus.PASS:
        await service_health.record_check(
            session=session,
            service_id=service.id,
            check_name=DOMAIN_CONTROL_CHECK_NAME,
            outcome=domain_control_outcome,
            checked_at=now,
        )
        # Deliberate commit on the failure path, as for readiness above.
        await session.commit()
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


def _upstream_hosts(service: Service) -> frozenset[str]:
    """Every upstream's host, enabled or not: a disabled endpoint can be enabled later."""
    return frozenset(
        host
        for endpoint in service.endpoints
        if endpoint.upstream is not None
        and (host := urlsplit(endpoint.upstream.base_url).hostname) is not None
    )
