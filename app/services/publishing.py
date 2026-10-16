"""Publish transition for provider services."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError
from app.db.models import ProviderSigningSecret, Service
from app.integrations.providers.dns import DnsResolver
from app.services import domain_control, moderation, revisions, service_access, service_health
from app.services.moderation import ServiceUnavailableError
from app.services.publish_readiness import validate_service_for_publish
from app.services.service_health import PUBLISH_READINESS_CHECK_NAME, ServiceHealthOutcome


async def publish_service(
    *,
    session: AsyncSession,
    resolver: DnsResolver,
    account_id: int,
    service_id: int,
) -> Service:
    # The domain proof queries DNS, so it runs before the lock with no transaction open.
    unlocked = await service_access.load_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    hosts = _upstream_hosts(unlocked)
    token = await domain_control.get_domain_token(session=session, account_id=account_id)
    await session.rollback()
    domain_problems = await domain_control.check_domain_control(
        resolver=resolver,
        token=token,
        hosts=hosts,
    )

    service = await service_access.load_owned_service_for_update(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    if _upstream_hosts(service) != hosts:
        raise ConflictError("the service's upstreams changed while publishing; publish again")
    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not publishable outside draft")

    try:
        await moderation.ensure_service_publishable(session=session, service_id=service.id)
    except ServiceUnavailableError as exc:
        raise InvalidStateError(f"service is {exc.state.value}") from exc

    has_signing_secret = await session.scalar(
        select(exists().where(ProviderSigningSecret.account_id == account_id)),
    )

    # Stamped after the lock wait and the gates so the timestamp reflects when the
    # row was actually mutated. One clock for the whole operation: the readiness
    # row and the service both carry this value.
    now = datetime.now(UTC)
    try:
        validate_service_for_publish(service)
        if not has_signing_secret:
            raise InvalidInputError("provider must issue a signing secret before publish")
        if domain_problems:
            raise InvalidInputError("; ".join(domain_problems))
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

    if service.current_revision_id is None or service.current_change_token is None:
        await revisions.create_revision(session=session, service=service)
    service.lifecycle = ServiceLifecycle.ACTIVE
    service.updated_at = now
    await session.commit()
    return service


def _upstream_hosts(service: Service) -> frozenset[str]:
    # Disabled endpoints count too: one can be enabled after publishing.
    return frozenset(
        urlsplit(endpoint.upstream.base_url).netloc
        for endpoint in service.endpoints
        if endpoint.upstream is not None
    )
