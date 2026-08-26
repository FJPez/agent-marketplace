"""Publish transition for provider services."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.core.errors import InvalidInputError, InvalidStateError
from app.db.models.service import Service
from app.services import revisions, service_access
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.publish_readiness import (
    PUBLISH_READINESS_PASS_SUMMARY,
    validate_service_for_publish,
)
from app.services.service_health_service import (
    PUBLISH_READINESS_CHECK_NAME,
    ServiceHealthOutcome,
    ServiceHealthService,
)


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
        await ModerationService(session).ensure_service_publishable(service.id)
    except ServiceUnavailableError as exc:
        raise InvalidStateError(f"service is {exc.state.value}") from exc

    health_service = ServiceHealthService(session)
    try:
        validate_service_for_publish(service)
    except InvalidInputError as exc:
        await health_service.record_check(
            service_id=service.id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            outcome=ServiceHealthOutcome(
                status=ServiceHealthStatus.FAIL,
                summary=str(exc),
            ),
        )
        # Deliberate commit on the failure path: the FAIL row is the attempt's
        # only mutation and must stay visible after the rejection. Nothing may
        # mutate after this point.
        await session.commit()
        raise

    await health_service.record_check(
        service_id=service.id,
        check_name=PUBLISH_READINESS_CHECK_NAME,
        outcome=ServiceHealthOutcome(
            status=ServiceHealthStatus.PASS,
            summary=PUBLISH_READINESS_PASS_SUMMARY,
            details={
                "enabled_endpoint_count": len(
                    [endpoint for endpoint in service.endpoints if endpoint.is_enabled],
                ),
            },
        ),
    )

    # Stamped after the lock wait so the timestamp reflects when the row was
    # actually mutated.
    now = datetime.now(UTC)
    if service.current_revision_id is None or service.current_change_token is None:
        await revisions.create_revision(session=session, service=service)
    service.lifecycle = ServiceLifecycle.ACTIVE
    service.updated_at = now
    await session.commit()
    return service
