from typing import Self

from pydantic import BaseModel, ConfigDict

from app.core.enums import PricingModelType
from app.db.models import Quote
from app.schemas.common import Id, JsonValue, RequestHash, Timestamp
from app.schemas.pricing import CurrencyCode
from app.schemas.service import Slug


class QuoteCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "endpoint_key": "paid-summary",
                    "payload": {
                        "message": "Summarize this request after the payment flow completes."
                    },
                }
            ]
        }
    )

    endpoint_key: Slug
    payload: JsonValue


class QuoteResponse(BaseModel):
    id: Id
    service_id: Id
    endpoint_key: Slug
    pricing_type: PricingModelType
    amount_minor: int | None
    currency: CurrencyCode | None
    request_hash: RequestHash
    service_revision_id: Id | None
    service_change_token: RequestHash | None
    expires_at: Timestamp
    created_at: Timestamp

    @classmethod
    def from_model(cls, quote: Quote) -> Self:
        return cls(
            id=quote.id,
            service_id=quote.service_id,
            endpoint_key=quote.endpoint_key,
            pricing_type=quote.pricing_type,
            amount_minor=quote.amount_minor,
            currency=quote.currency,
            request_hash=quote.request_hash,
            service_revision_id=quote.service_revision_id,
            service_change_token=quote.service_change_token,
            expires_at=quote.expires_at,
            created_at=quote.created_at,
        )
