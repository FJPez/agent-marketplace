"""Contract revision classification and creation for provider services."""

from collections.abc import Collection
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccessMode, PricingModelType
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.db.models.service_revision import ServiceRevision

MATERIAL_ENDPOINT_FIELDS = frozenset(
    {
        "access_mode",
        "request_schema",
        "response_schema",
        "pricing",
        "timeout_seconds",
        "is_enabled",
    },
)


class UpdateImpact(StrEnum):
    MATERIAL = "material"
    NON_MATERIAL = "non_material"


def classify_endpoint_update(update_fields: Collection[str]) -> UpdateImpact:
    if MATERIAL_ENDPOINT_FIELDS.intersection(update_fields):
        return UpdateImpact.MATERIAL
    return UpdateImpact.NON_MATERIAL


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
                "pricing": _build_pricing_snapshot(endpoint),
                "timeout_seconds": endpoint.timeout_seconds,
                "is_enabled": endpoint.is_enabled,
            }
            for endpoint in ordered_endpoints
        ],
    }


def _build_pricing_snapshot(endpoint: ServiceEndpoint) -> dict[str, object | None]:
    if endpoint.access_mode is AccessMode.FREE:
        return {
            "pricing_type": PricingModelType.FREE.value,
            "amount_minor": None,
            "currency": None,
        }
    price = endpoint.price
    if price is not None:
        return {
            "pricing_type": PricingModelType.FIXED_PER_CALL.value,
            "amount_minor": price.amount_minor,
            "currency": price.currency,
        }
    return {
        "pricing_type": None,
        "amount_minor": None,
        "currency": None,
    }


async def create_revision(*, session: AsyncSession, service: Service) -> ServiceRevision:
    # max + 1 is only correct because callers hold the service row lock; the
    # (service_id, revision_number) unique constraint backstops that contract.
    highest_revision_number = await session.scalar(
        select(func.max(ServiceRevision.revision_number)).where(
            ServiceRevision.service_id == service.id,
        ),
    )
    revision = ServiceRevision(
        service_id=service.id,
        revision_number=(highest_revision_number or 0) + 1,
        change_token=uuid4().hex,
        snapshot=build_contract_snapshot(service),
    )
    session.add(revision)
    # Flushed to obtain the generated revision id before stamping it on the
    # service; the stamped columns ride the caller's commit.
    await session.flush()
    service.current_revision_id = revision.id
    service.current_change_token = revision.change_token
    return revision
