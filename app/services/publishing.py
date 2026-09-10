"""Publish transition for provider services."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.core.errors import InvalidInputError, InvalidStateError
from app.db.models.service import Service
from app.services import moderation, revisions, service_access, service_health
from app.services.moderation import ServiceUnavailableError
from app.services.publish_readiness import validate_service_for_publish
from app.services.service_health import PUBLISH_READINESS_CHECK_NAME, ServiceHealthOutcome


async def publish_service(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
) -> Service:
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

    # Stamped after the lock wait and the gates so the timestamp reflects when the
    # row was actually mutated. One clock for the whole operation: the readiness
    # row and the service both carry this value.
    now = datetime.now(UTC)
    try:
        validate_service_for_publish(service)
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
