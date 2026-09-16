from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AccessMode, InvocationStatus
from app.schemas.common import Id, JsonValue, RequestHash, Timestamp
from app.schemas.service import Slug


class InvokeRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "endpoint_key": "free-ping",
                    "payload": {"message": "hello from the local demo"},
                    "quote_id": None,
                },
                {
                    "endpoint_key": "paid-summary",
                    "payload": {"message": "Please summarize this paid request."},
                    "quote_id": 1,
                },
            ]
        },
    )

    endpoint_key: Slug
    payload: JsonValue
    quote_id: Id | None = None


class InvocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Id
    service_id: Id
    endpoint_key: Slug
    access_mode: AccessMode
    quote_id: Id | None
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: RequestHash
    status: InvocationStatus
    upstream_status_code: int | None
    response_payload: JsonValue | None
    error_message: str | None
    created_at: Timestamp


class InvocationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Id
    service_id: Id
    endpoint_key: Slug
    access_mode: AccessMode
    quote_id: Id | None
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: RequestHash
    status: InvocationStatus
    upstream_status_code: int | None
    created_at: Timestamp
