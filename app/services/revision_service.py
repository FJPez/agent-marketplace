from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from app.repositories.service_revision_repo import ServiceRevisionRepository

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models.service import Service
    from app.db.models.service_revision import ServiceRevision

MATERIAL_ENDPOINT_FIELDS = frozenset(
    {
        "access_mode",
        "request_schema",
        "response_schema",
        "timeout_seconds",
        "is_enabled",
    },
)


class UpdateImpact(StrEnum):
    MATERIAL = "material"
    NON_MATERIAL = "non_material"


def build_contract_snapshot(service: Service) -> dict[str, object]:
    ordered_endpoints = sorted(
        service.endpoints,
        key=lambda endpoint: (endpoint.key, endpoint.id),
    )
    return {
        "service": {
            "id": service.id,
            "slug": service.slug,
        },
        "endpoints": [
            {
                "id": endpoint.id,
                "key": endpoint.key,
                "access_mode": endpoint.access_mode.value,
                "request_schema": endpoint.request_schema,
                "response_schema": endpoint.response_schema,
                "timeout_seconds": endpoint.timeout_seconds,
                "is_enabled": endpoint.is_enabled,
            }
            for endpoint in ordered_endpoints
        ],
    }


class RevisionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._revision_repo = ServiceRevisionRepository(session)

    @staticmethod
    def classify_service_update(
        update_fields: Mapping[str, object],
    ) -> UpdateImpact:
        _ = update_fields
        return UpdateImpact.NON_MATERIAL

    @staticmethod
    def classify_endpoint_update(
        update_fields: Mapping[str, object],
    ) -> UpdateImpact:
        if MATERIAL_ENDPOINT_FIELDS.intersection(update_fields):
            return UpdateImpact.MATERIAL
        return UpdateImpact.NON_MATERIAL

    async def create_revision(self, service: Service) -> ServiceRevision:
        revision_number = await self._revision_repo.next_revision_number(
            service_id=service.id,
        )
        revision = self._revision_repo.add(
            service_id=service.id,
            revision_number=revision_number,
            change_token=uuid4().hex,
            snapshot=build_contract_snapshot(service),
        )
        await self._session.flush()
        service.current_revision_id = revision.id
        service.current_change_token = revision.change_token
        await self._session.flush()
        return revision

    async def create_revision_if_material_service_update(
        self,
        service: Service,
        *,
        update_fields: Mapping[str, object],
    ) -> UpdateImpact:
        impact = self.classify_service_update(update_fields)
        if impact is UpdateImpact.MATERIAL:
            await self.create_revision(service)
        return impact

    async def create_revision_if_material_endpoint_update(
        self,
        service: Service,
        *,
        update_fields: Mapping[str, object],
    ) -> UpdateImpact:
        impact = self.classify_endpoint_update(update_fields)
        if impact is UpdateImpact.MATERIAL:
            await self.create_revision(service)
        return impact
