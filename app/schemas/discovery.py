from typing import Self

from pydantic import BaseModel

from app.core.enums import AccessMode, PricingModelType
from app.core.json_types import to_json_object
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.service import Description, SchemaObject, ServiceName, Slug, Summary


class PublicEndpointSummary(BaseModel):
    key: Slug
    name: ServiceName
    summary: Summary | None
    description: str | None
    access_mode: AccessMode

    @classmethod
    def from_model(cls, endpoint: ServiceEndpoint) -> Self:
        return cls(
            key=endpoint.key,
            name=endpoint.name,
            summary=endpoint.summary,
            description=endpoint.description,
            access_mode=endpoint.access_mode,
        )


class PublicEndpointSchema(BaseModel):
    key: Slug
    request_schema: SchemaObject
    response_schema: SchemaObject

    @classmethod
    def from_model(cls, endpoint: ServiceEndpoint) -> Self:
        return cls(
            key=endpoint.key,
            request_schema=to_json_object(endpoint.request_schema),
            response_schema=to_json_object(endpoint.response_schema),
        )


class PublicEndpointPricing(BaseModel):
    key: Slug
    access_mode: AccessMode
    pricing_type: str | None
    amount_minor: int | None
    currency: str | None

    @classmethod
    def from_model(cls, endpoint: ServiceEndpoint) -> Self:
        pricing = getattr(endpoint, "pricing", None)
        if pricing is not None:
            return cls(
                key=endpoint.key,
                access_mode=endpoint.access_mode,
                pricing_type=PricingModelType.FIXED_PER_CALL.value,
                amount_minor=getattr(pricing, "amount_minor", None),
                currency=getattr(pricing, "currency", None),
            )
        if endpoint.access_mode is AccessMode.FREE:
            return cls(
                key=endpoint.key,
                access_mode=endpoint.access_mode,
                pricing_type="free",
                amount_minor=None,
                currency=None,
            )
        return cls(
            key=endpoint.key,
            access_mode=endpoint.access_mode,
            pricing_type=None,
            amount_minor=None,
            currency=None,
        )


class PublicServiceListItem(BaseModel):
    id: int
    slug: Slug
    name: ServiceName
    summary: Summary
    description: Description | None
    tags: list[str]

    @classmethod
    def from_model(cls, service: Service) -> Self:
        return cls(
            id=service.id,
            slug=service.slug,
            name=service.name,
            summary=service.summary,
            description=service.description,
            tags=sorted(tag.tag for tag in service.tags),
        )


class PublicServiceDetail(PublicServiceListItem):
    endpoints: list[PublicEndpointSummary]

    @classmethod
    def from_model(cls, service: Service) -> Self:
        return cls(
            id=service.id,
            slug=service.slug,
            name=service.name,
            summary=service.summary,
            description=service.description,
            tags=sorted(tag.tag for tag in service.tags),
            endpoints=[
                PublicEndpointSummary.from_model(endpoint)
                for endpoint in service.endpoints
                if endpoint.is_enabled
            ],
        )


class PublicServiceSchemaResponse(BaseModel):
    id: int
    slug: Slug
    endpoints: list[PublicEndpointSchema]

    @classmethod
    def from_model(cls, service: Service) -> Self:
        return cls(
            id=service.id,
            slug=service.slug,
            endpoints=[
                PublicEndpointSchema.from_model(endpoint)
                for endpoint in service.endpoints
                if endpoint.is_enabled
            ],
        )


class PublicServicePricingResponse(BaseModel):
    id: int
    slug: Slug
    endpoints: list[PublicEndpointPricing]

    @classmethod
    def from_model(cls, service: Service) -> Self:
        return cls(
            id=service.id,
            slug=service.slug,
            endpoints=[
                PublicEndpointPricing.from_model(endpoint)
                for endpoint in service.endpoints
                if endpoint.is_enabled
            ],
        )
