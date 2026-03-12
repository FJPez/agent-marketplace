import re
from typing import TypedDict, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.enums import ServiceLifecycle
from app.db.models.service import Service
from app.repositories.provider_profile_repo import ProviderProfileRepository
from app.repositories.service_repo import ServiceRepository
from app.schemas.service import (
    ServiceCreateRequest,
    ServiceTagsUpdateRequest,
    ServiceUpdateRequest,
)
from app.services.provider_service_errors import (
    ProviderServiceConflictError,
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)

TAG_TOKEN_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class _ServiceUpdateFields(TypedDict, total=False):
    name: str
    summary: str
    description: str | None


class ProviderDraftService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._provider_profile_repo = ProviderProfileRepository(session)
        self._service_repo = ServiceRepository(session)

    async def create_service(
        self,
        actor: ActorContext,
        request: ServiceCreateRequest,
    ) -> Service:
        await self._require_provider_profile(actor.account_id)

        service = self._service_repo.add(
            provider_account_id=actor.account_id,
            slug=request.slug,
            name=request.name,
            summary=request.summary,
            description=request.description,
        )

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ProviderServiceConflictError("service slug already exists") from exc

        return await self._reload_owned_service(
            provider_account_id=actor.account_id,
            service_id=service.id,
        )

    async def list_services(self, actor: ActorContext) -> list[Service]:
        await self._require_provider_profile(actor.account_id)
        return await self._service_repo.list_by_provider_account_id(
            provider_account_id=actor.account_id,
        )

    async def get_service(self, actor: ActorContext, *, service_id: int) -> Service:
        await self._require_provider_profile(actor.account_id)
        service = await self._service_repo.get_owned(
            service_id=service_id,
            provider_account_id=actor.account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("service not found")
        return service

    async def update_service(
        self,
        actor: ActorContext,
        *,
        service_id: int,
        request: ServiceUpdateRequest,
    ) -> Service:
        raw_update_fields = request.model_dump(exclude_unset=True)
        if not raw_update_fields:
            raise ProviderServiceValidationError("at least one field must be provided")

        service = await self.get_service(actor, service_id=service_id)
        self._ensure_draft(service)
        update_fields = cast("_ServiceUpdateFields", raw_update_fields)
        if "name" in update_fields and update_fields["name"] is None:
            raise ProviderServiceValidationError("name cannot be null")
        if "summary" in update_fields and update_fields["summary"] is None:
            raise ProviderServiceValidationError("summary cannot be null")
        self._service_repo.update_service(
            service,
            **update_fields,
        )
        await self._session.commit()
        return await self._reload_owned_service(
            provider_account_id=actor.account_id,
            service_id=service.id,
        )

    async def replace_tags(
        self,
        actor: ActorContext,
        *,
        service_id: int,
        request: ServiceTagsUpdateRequest,
    ) -> Service:
        service = await self.get_service(actor, service_id=service_id)
        self._ensure_draft(service)
        normalized_tags = sorted({self._normalize_tag(tag) for tag in request.tags})
        await self._service_repo.replace_tags(service, tags=normalized_tags)
        await self._session.commit()
        return await self._reload_owned_service(
            provider_account_id=actor.account_id,
            service_id=service.id,
        )

    async def _require_provider_profile(self, account_id: int) -> None:
        profile = await self._provider_profile_repo.get_by_account_id(account_id)
        if profile is None:
            raise ProviderServiceNotFoundError("provider profile not found")

    def _ensure_draft(self, service: Service) -> None:
        if service.lifecycle is not ServiceLifecycle.DRAFT:
            raise ProviderServiceStateError("service is not mutable outside draft")

    def _normalize_tag(self, tag: str) -> str:
        normalized_tag = tag.strip().lower()
        if TAG_TOKEN_PATTERN.fullmatch(normalized_tag) is None:
            raise ProviderServiceValidationError("tags must be lowercase slug tokens")
        return normalized_tag

    async def _reload_owned_service(
        self,
        *,
        provider_account_id: int,
        service_id: int,
    ) -> Service:
        service = await self._service_repo.get_owned(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("service not found")
        return service
