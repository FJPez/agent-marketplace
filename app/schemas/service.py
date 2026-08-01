from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.json_types import JsonObject, to_json_object
from app.core.text import (
    SERVICE_DESCRIPTION_MAX_LENGTH,
    SERVICE_NAME_MAX_LENGTH,
    SERVICE_SUMMARY_MAX_LENGTH,
    SERVICE_TAGS_MAX_COUNT,
    SLUG_MAX_LENGTH,
    TAG_MAX_LENGTH,
    normalize_slug,
    normalize_tag,
)
from app.db.models.pricing_model import PricingModel
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.common import Id, Timestamp


def _validate_upstream_path(value: str) -> str:
    if not value.startswith("/"):
        msg = "path must start with /"
        raise ValueError(msg)
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        msg = "path must be path-only and must not include scheme, host, query, or fragment"
        raise ValueError(msg)
    return value


Slug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=SLUG_MAX_LENGTH,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
    AfterValidator(normalize_slug),
]
ServiceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=SERVICE_NAME_MAX_LENGTH),
]
Summary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=SERVICE_SUMMARY_MAX_LENGTH),
]
Description = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=SERVICE_DESCRIPTION_MAX_LENGTH,
    ),
]
Tag = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TAG_MAX_LENGTH),
    AfterValidator(normalize_tag),
]
SchemaObject = JsonObject
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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "slug": "demo-agent-service",
                    "name": "Demo Agent Service",
                    "summary": "A provider-owned service for local marketplace demos.",
                    "description": "Exposes free and paid endpoints for guided walkthroughs.",
                }
            ]
        }
    )

    slug: Slug
    name: ServiceName
    summary: Summary
    description: Description | None = None


class ServiceUpdateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Demo Agent Service",
                    "summary": "Updated service summary for the oral demonstration.",
                    "description": "Optional long-form provider description.",
                }
            ]
        }
    )

    name: ServiceName | None = None
    summary: Summary | None = None
    description: Description | None = None


class ServiceTagsUpdateRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"tags": ["demo", "translation"]}]})

    tags: Annotated[list[Tag], Field(max_length=SERVICE_TAGS_MAX_COUNT)]


class EndpointCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "key": "free-ping",
                    "name": "Free Ping",
                    "summary": "Simple free invoke endpoint.",
                    "description": "Echo-style endpoint used in the local mock demo.",
                    "access_mode": "free",
                    "request_schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "response_schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "timeout_seconds": 30,
                    "is_enabled": True,
                    "pricing": {"pricing_type": "free"},
                }
            ]
        }
    )

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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Updated free endpoint summary.",
                    "timeout_seconds": 45,
                    "is_enabled": True,
                    "pricing": {"pricing_type": "free"},
                }
            ]
        }
    )

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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "base_url": "http://127.0.0.1:9000",
                    "path": "/free-ping",
                    "http_method": "POST",
                    "config": {
                        "auth": {
                            "type": "hmac_sha256",
                            "key_id": "demo-key",
                            "secret": "demo-secret",
                        }
                    },
                }
            ]
        }
    )

    base_url: HttpUrl
    path: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
        AfterValidator(_validate_upstream_path),
    ]
    http_method: HttpMethod
    config: SchemaObject = Field(default_factory=dict)


class EndpointPricingRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"pricing_type": "free"},
                {"pricing_type": "fixed_per_call", "amount_minor": 250, "currency": "USD"},
            ]
        }
    )

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
            request_schema=to_json_object(endpoint.request_schema),
            response_schema=to_json_object(endpoint.response_schema),
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
