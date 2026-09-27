from typing import Self

from pydantic import BaseModel, ConfigDict

from app.core.enums import AccessMode
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


class PublicListingPrice(BaseModel):
    """The public terms of a price version: what a paid call to the listing costs."""

    model_config = ConfigDict(from_attributes=True)

    version: int
    amount: int
    asset: str
    network: str
    pay_to: str


class PublicEndpointPricing(BaseModel):
    endpoint_id: int
    key: Slug
    access_mode: AccessMode
    # The invoke route arrives in phase 1; the URL is stable (spec D13).
    invoke_url: str
    price: PublicListingPrice | None

    @classmethod
    def from_model(cls, endpoint: ServiceEndpoint, *, service_slug: str) -> Self:
        return cls(
            endpoint_id=endpoint.id,
            key=endpoint.key,
            access_mode=endpoint.access_mode,
            invoke_url=f"/v1/invoke/{service_slug}/{endpoint.key}",
            price=(
                None
                if endpoint.current_price is None
                else PublicListingPrice.model_validate(endpoint.current_price)
            ),
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
        list_item = PublicServiceListItem.from_model(service)
        return cls(
            **list_item.model_dump(),
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
                PublicEndpointPricing.from_model(endpoint, service_slug=service.slug)
                for endpoint in service.endpoints
                if endpoint.is_enabled
            ],
        )
