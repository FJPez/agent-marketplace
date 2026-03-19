from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.enums import ServiceLifecycle
from app.db.models.service import Service
from app.repositories.service_repo import ServiceRepository
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.provider_service_errors import (
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)
from app.services.publish_readiness import PublishReadinessChecker
from app.services.revision_service import RevisionService
from app.services.service_health_service import (
    PUBLISH_READINESS_CHECK_NAME,
    ServiceHealthCheckFailedError,
    ServiceHealthService,
)


class PublishService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._service_repo = ServiceRepository(session)
        self._service_health_service = ServiceHealthService(session)
        self._moderation_service = ModerationService(session)
        self._revision_service = RevisionService(session)

    async def publish_service(
        self,
        actor: ActorContext,
        *,
        service_id: int,
    ) -> Service:
        service = await self._service_repo.get_owned_for_update(
            service_id=service_id,
            provider_account_id=actor.account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("service not found")
        if service.lifecycle is not ServiceLifecycle.DRAFT:
            raise ProviderServiceStateError("service is not publishable outside draft")
        try:
            await self._moderation_service.ensure_service_publishable(service.id)
        except ServiceUnavailableError as exc:
            raise ProviderServiceStateError(f"service is {exc.state.value}") from exc

        await self._service_health_service.run_check(
            service_id=service.id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            checker=PublishReadinessChecker(self._session),
        )
        try:
            await self._service_health_service.ensure_publish_ready(service_id=service.id)
        except ServiceHealthCheckFailedError as exc:
            raise ProviderServiceValidationError(
                exc.summary or "service failed latest publish-readiness health check",
            ) from exc
        if service.current_revision_id is None or service.current_change_token is None:
            await self._revision_service.create_revision(service)
        self._service_repo.set_lifecycle(service, lifecycle=ServiceLifecycle.ACTIVE)
        await self._session.commit()

        published = await self._service_repo.get_owned(
            service_id=service_id,
            provider_account_id=actor.account_id,
        )
        if published is None:
            raise ProviderServiceNotFoundError("service not found")
        return published
