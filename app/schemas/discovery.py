from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import AccessMode, PricingModelType
from app.core.json_types import to_json_object
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.service import Description, SchemaObject, ServiceName, Slug, Summary

SERVICE_ID_MAX = 9223372036854775807

ServiceId = Annotated[int, Field(gt=0, le=SERVICE_ID_MAX)]


class PublicServiceRef(BaseModel):
    """A public service identifier: exactly one of a service id or a service slug."""

    model_config = ConfigDict(frozen=True)

    id: ServiceId | None = None
    slug: Slug | None = None

    @model_validator(mode="after")
    def check_exactly_one_identifier(self) -> Self:
        if (self.id is None) == (self.slug is None):
            msg = "service reference must carry either an id or a slug"
            raise ValueError(msg)
        return self


def parse_public_service_ref(value: str) -> PublicServiceRef:
    """Purely numeric identifiers address the service id; anything else must be a slug."""
    if value.isdigit():
        return PublicServiceRef(id=int(value))
    return PublicServiceRef(slug=value)


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
        if endpoint.access_mode is AccessMode.FREE:
            return cls(
                key=endpoint.key,
                access_mode=endpoint.access_mode,
                pricing_type=PricingModelType.FREE.value,
                amount_minor=None,
                currency=None,
            )
        price = endpoint.price
        if price is not None:
            return cls(
                key=endpoint.key,
                access_mode=endpoint.access_mode,
                pricing_type=PricingModelType.FIXED_PER_CALL.value,
                amount_minor=price.amount_minor,
                currency=price.currency,
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
        list_item = PublicServiceListItem.from_model(service)
        return cls(
            **dict(list_item),
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
