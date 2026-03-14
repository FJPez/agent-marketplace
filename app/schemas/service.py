from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models.pricing_model import PricingModel
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.common import Id, Timestamp

Slug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]
ServiceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
Summary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=5000),
]
Tag = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
SchemaObject = dict[str, Any]
HttpMethod = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=16,
        pattern=r"^[A-Z]+$",
    ),
]
CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
]


class ServiceCreateRequest(BaseModel):
    slug: Slug
    name: ServiceName
    summary: Summary
    description: Description | None = None


class ServiceUpdateRequest(BaseModel):
    name: ServiceName | None = None
    summary: Summary | None = None
    description: Description | None = None


class ServiceTagsUpdateRequest(BaseModel):
    tags: list[Tag]


class EndpointCreateRequest(BaseModel):
    key: Slug
    name: ServiceName
    summary: Summary | None = None
    description: Description | None = None
    access_mode: AccessMode
    request_schema: SchemaObject
    response_schema: SchemaObject
    timeout_seconds: Annotated[int, Field(gt=0, le=3600)]
    is_enabled: bool = True
    pricing: "EndpointPricingRequest | None" = None


class EndpointUpdateRequest(BaseModel):
    name: ServiceName | None = None
    summary: Summary | None = None
    description: Description | None = None
    access_mode: AccessMode | None = None
    request_schema: SchemaObject | None = None
    response_schema: SchemaObject | None = None
    timeout_seconds: Annotated[int, Field(gt=0, le=3600)] | None = None
    is_enabled: bool | None = None
    pricing: "EndpointPricingRequest | None" = None


class EndpointUpstreamRequest(BaseModel):
    base_url: HttpUrl
    path: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]
    http_method: HttpMethod
    config: SchemaObject = Field(default_factory=dict)


class EndpointPricingRequest(BaseModel):
    pricing_type: PricingModelType
    amount_minor: Annotated[int, Field(gt=0)] | None = None
    currency: CurrencyCode | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.pricing_type is PricingModelType.FREE:
            if self.amount_minor is not None or self.currency is not None:
                msg = "free pricing cannot include amount_minor or currency"
                raise ValueError(msg)
            return self

        if self.amount_minor is None or self.currency is None:
            msg = "fixed_per_call pricing requires amount_minor and currency"
            raise ValueError(msg)
        return self


class EndpointPricingResponse(BaseModel):
    pricing_type: PricingModelType
    amount_minor: int | None
    currency: str | None

    @classmethod
    def from_model(cls, pricing: PricingModel) -> Self:
        return cls(
            pricing_type=pricing.pricing_type,
            amount_minor=pricing.amount_minor,
            currency=pricing.currency,
        )

    @classmethod
    def from_endpoint(cls, endpoint: ServiceEndpoint) -> Self | None:
        if endpoint.pricing is not None:
            return cls.from_model(endpoint.pricing)
        if endpoint.access_mode is AccessMode.FREE:
            return cls(
                pricing_type=PricingModelType.FREE,
                amount_minor=None,
                currency=None,
            )
        return None


class EndpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Id
    key: Slug
    name: ServiceName
    summary: Summary | None
    description: str | None
    access_mode: AccessMode
    request_schema: SchemaObject
    response_schema: SchemaObject
    timeout_seconds: int
    is_enabled: bool
    pricing: EndpointPricingResponse | None
    has_upstream: bool
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def from_model(cls, endpoint: ServiceEndpoint) -> Self:
        return cls(
            id=endpoint.id,
            key=endpoint.key,
            name=endpoint.name,
            summary=endpoint.summary,
            description=endpoint.description,
            access_mode=endpoint.access_mode,
            request_schema=endpoint.request_schema,
            response_schema=endpoint.response_schema,
            timeout_seconds=endpoint.timeout_seconds,
            is_enabled=endpoint.is_enabled,
            pricing=EndpointPricingResponse.from_endpoint(endpoint),
            has_upstream=endpoint.upstream is not None,
            created_at=endpoint.created_at,
            updated_at=endpoint.updated_at,
        )


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Id
    provider_account_id: Id
    slug: Slug
    name: ServiceName
    summary: Summary
    description: str | None
    lifecycle: ServiceLifecycle
    tags: list[str]
    endpoints: list[EndpointResponse]
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def from_model(cls, service: Service) -> Self:
        return cls(
            id=service.id,
            provider_account_id=service.provider_account_id,
            slug=service.slug,
            name=service.name,
            summary=service.summary,
            description=service.description,
            lifecycle=service.lifecycle,
            tags=sorted(tag.tag for tag in service.tags),
            endpoints=[EndpointResponse.from_model(endpoint) for endpoint in service.endpoints],
            created_at=service.created_at,
            updated_at=service.updated_at,
        )
