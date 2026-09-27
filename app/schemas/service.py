from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from app.core.enums import AccessMode, ServiceLifecycle
from app.core.json_types import JsonObject, to_json_object
from app.core.request_schema_validation import check_request_schema, check_request_schema_shape
from app.core.service_fields import (
    DEFAULT_RESPONSE_CONTENT_TYPE,
    ENDPOINT_TIMEOUT_MAX_SECONDS,
    SERVICE_DESCRIPTION_MAX_LENGTH,
    SERVICE_NAME_MAX_LENGTH,
    SERVICE_SUMMARY_MAX_LENGTH,
    SERVICE_TAGS_MAX_COUNT,
    SLUG_MAX_LENGTH,
    TAG_MAX_LENGTH,
    UPSTREAM_PATH_MAX_LENGTH,
    normalize_media_type,
    normalize_slug,
    normalize_tag,
    normalize_upstream_path,
)
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.common import Id, Timestamp
from app.schemas.pricing import ListingPriceRequest, ListingPriceResponse

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


def _check_request_schema_if_sent(schema: JsonObject | None) -> JsonObject | None:
    # An explicit null is refused by the update model's own null check.
    return None if schema is None else check_request_schema(schema)


# The shape (nesting and size) is checked before the value is validated as a JSON object,
# the rest after.
RequestSchema = Annotated[
    JsonObject,
    BeforeValidator(check_request_schema_shape),
    AfterValidator(check_request_schema),
]
# An update's request_schema may be omitted. Its checks wrap the whole optional type, so
# an invalid schema is one error at `request_schema`, not one per member of the union.
OptionalRequestSchema = Annotated[
    JsonObject | SkipJsonSchema[None],
    BeforeValidator(check_request_schema_shape),
    AfterValidator(_check_request_schema_if_sent),
]
ResponseContentType = Annotated[str, AfterValidator(normalize_media_type)]
TimeoutSeconds = Annotated[StrictInt, Field(gt=0, le=ENDPOINT_TIMEOUT_MAX_SECONDS)]


def reject_explicit_null(value: object, info: ValidationInfo) -> object:
    """Reject an explicit null sent for a non-clearable field.

    Field validators never run for defaults, so this fires only when the client
    actually sent a null.
    """
    if value is None:
        msg = f"{info.field_name} cannot be null"
        raise ValueError(msg)
    return value


def require_a_field[ModelT: BaseModel](model: ModelT) -> ModelT:
    """Reject a partial-update payload that carries no fields at all."""
    if not model.model_fields_set:
        msg = "at least one field must be provided"
        raise ValueError(msg)
    return model


class ServiceCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "slug": "demo-agent-service",
                    "name": "Demo Agent Service",
                    "summary": "A provider-owned service for local marketplace demos.",
                    "description": "Exposes free and paid endpoints for guided walkthroughs.",
                }
            ]
        },
    )

    slug: Slug
    name: ServiceName
    summary: Summary
    description: Description | None = None


class ServiceUpdateRequest(BaseModel):
    """Partial service update.

    Omitted fields are left unchanged. ``description`` is clearable and accepts
    an explicit null. ``name`` and ``summary`` are non-clearable: sending an
    explicit null is a client error rather than a request to unset the value.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Demo Agent Service",
                    "summary": "Updated service summary for the oral demonstration.",
                    "description": "Optional long-form provider description.",
                }
            ]
        },
    )

    name: ServiceName | SkipJsonSchema[None] = None
    summary: Summary | SkipJsonSchema[None] = None
    description: Description | None = None

    validate_non_clearable = field_validator("name", "summary")(reject_explicit_null)
    validate_any_field_supplied = model_validator(mode="after")(require_a_field)


class ServiceTagsUpdateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"tags": ["demo", "translation"]}]},
    )

    tags: Annotated[list[Tag], Field(max_length=SERVICE_TAGS_MAX_COUNT)]


class EndpointCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
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
                }
            ]
        },
    )

    key: Slug
    name: ServiceName
    summary: Summary | None = None
    description: Description | None = None
    access_mode: AccessMode
    request_schema: RequestSchema
    response_schema: SchemaObject
    response_content_type: ResponseContentType = DEFAULT_RESPONSE_CONTENT_TYPE
    timeout_seconds: TimeoutSeconds
    supports_idempotency: StrictBool = False
    is_enabled: StrictBool = True
    price: ListingPriceRequest | None = None

    @model_validator(mode="after")
    def reject_price_on_free_endpoint(self) -> Self:
        if self.access_mode is AccessMode.FREE and self.price is not None:
            msg = "free endpoints cannot have a price"
            raise ValueError(msg)
        return self


class EndpointUpdateRequest(BaseModel):
    """Partial endpoint update.

    Omitted fields are left unchanged. ``summary``, ``description``, and
    ``price`` are clearable and accept an explicit null. Every other field is
    non-clearable: sending an explicit null is a client error rather than a
    request to unset the value. A new ``price`` amount creates a new immutable
    price version, and so does resending the current amount after the
    marketplace's payment terms changed; omit ``price`` to leave it untouched.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "summary": "Updated paid endpoint summary.",
                    "timeout_seconds": 20,
                    "is_enabled": True,
                    "price": {"amount": 25_000},
                }
            ]
        },
    )

    name: ServiceName | SkipJsonSchema[None] = None
    summary: Summary | None = None
    description: Description | None = None
    access_mode: AccessMode | SkipJsonSchema[None] = None
    request_schema: OptionalRequestSchema = None
    response_schema: SchemaObject | SkipJsonSchema[None] = None
    response_content_type: ResponseContentType | SkipJsonSchema[None] = None
    timeout_seconds: TimeoutSeconds | SkipJsonSchema[None] = None
    supports_idempotency: StrictBool | SkipJsonSchema[None] = None
    is_enabled: StrictBool | SkipJsonSchema[None] = None
    price: ListingPriceRequest | None = None

    validate_non_clearable = field_validator(
        "name",
        "access_mode",
        "request_schema",
        "response_schema",
        "response_content_type",
        "timeout_seconds",
        "supports_idempotency",
        "is_enabled",
    )(reject_explicit_null)
    validate_any_field_supplied = model_validator(mode="after")(require_a_field)


class EndpointUpstreamRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "base_url": "https://provider.example.com",
                    "path": "/free-ping",
                    "http_method": "POST",
                }
            ]
        },
    )

    base_url: HttpUrl
    path: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=UPSTREAM_PATH_MAX_LENGTH),
        AfterValidator(normalize_upstream_path),
    ]
    http_method: Literal["POST", "PUT", "PATCH"]


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
    response_content_type: str
    timeout_seconds: int
    supports_idempotency: bool
    is_enabled: bool
    price: ListingPriceResponse | None
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
            response_content_type=endpoint.response_content_type,
            timeout_seconds=endpoint.timeout_seconds,
            supports_idempotency=endpoint.supports_idempotency,
            is_enabled=endpoint.is_enabled,
            price=(
                None
                if endpoint.current_price is None
                else ListingPriceResponse.model_validate(endpoint.current_price)
            ),
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
