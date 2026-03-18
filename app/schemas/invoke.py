from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AccessMode, InvocationStatus
from app.core.json_types import to_json_value
from app.db.models import Invocation
from app.schemas.common import Id, JsonValue, RequestHash, Timestamp
from app.schemas.service import Slug


class InvokeRequest(BaseModel):
    model_config = ConfigDict(
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
        }
    )

    endpoint_key: Slug
    payload: JsonValue
    quote_id: Id | None = None


class InvocationResponse(BaseModel):
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

    @classmethod
    def from_model(cls, invocation: Invocation) -> Self:
        return cls(
            id=invocation.id,
            service_id=invocation.service_id,
            endpoint_key=invocation.endpoint_key,
            access_mode=invocation.access_mode,
            quote_id=invocation.quote_id,
            idempotency_key=invocation.idempotency_key,
            request_hash=invocation.request_hash,
            status=invocation.status,
            upstream_status_code=invocation.upstream_status_code,
            response_payload=None
            if invocation.response_payload is None
            else to_json_value(invocation.response_payload),
            error_message=invocation.error_message,
            created_at=invocation.created_at,
        )


class InvocationListItem(BaseModel):
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

    @classmethod
    def from_model(cls, invocation: Invocation) -> Self:
        return cls(
            id=invocation.id,
            service_id=invocation.service_id,
            endpoint_key=invocation.endpoint_key,
            access_mode=invocation.access_mode,
            quote_id=invocation.quote_id,
            idempotency_key=invocation.idempotency_key,
            request_hash=invocation.request_hash,
            status=invocation.status,
            upstream_status_code=invocation.upstream_status_code,
            created_at=invocation.created_at,
        )
