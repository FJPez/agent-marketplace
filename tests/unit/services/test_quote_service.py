from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.request_schema_validation import PayloadSchemaMismatchError
from app.db.models.endpoint_price import EndpointPrice
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.services.moderation_service import (
    ModerationServiceState,
    ServiceUnavailableError,
)
from app.services.quote_service import (
    QuoteExpiredError,
    QuoteMismatchError,
    QuoteNotFoundError,
    QuoteService,
    QuoteStaleError,
    QuoteUnavailableError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    async def commit(self) -> None:
        return None

    async def refresh(self, instance: object) -> None:
        _ = instance


@dataclass(slots=True)
class FakeQuote:
    id: int
    service_id: int
    endpoint_id: int
    endpoint_key: str
    request_hash: str
    pricing_type: PricingModelType
    amount_minor: int | None
    currency: str | None
    service_revision_id: int | None
    service_change_token: str | None
    expires_at: datetime
    created_at: datetime


class FakeQuoteRepository:
    def __init__(self, quote: FakeQuote | None) -> None:
        self.quote = quote

    async def get(self, *, quote_id: int) -> FakeQuote | None:
        if self.quote is None or self.quote.id != quote_id:
            return None
        return self.quote

    def add(self, **_: object) -> FakeQuote:
        if self.quote is None:
            msg = "quote not configured"
            raise RuntimeError(msg)
        return self.quote


class FakeServiceRepository:
    def __init__(self, service: Service | None) -> None:
        self.service = service

    async def get_public(self, *, service_id_or_slug: str) -> Service | None:
        _ = service_id_or_slug
        return self.service


class FakeModerationService:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable

    async def ensure_service_listed(self, service_id: int) -> None:
        if self.unavailable:
            raise ServiceUnavailableError(
                service_id=service_id,
                state=ModerationServiceState.SUSPENDED,
            )


def _service(
    *,
    revision_id: int | None = 11,
    change_token: str | None = "b" * 64,
    endpoint_key: str = "translate",
    endpoint_enabled: bool = True,
    access_mode: AccessMode = AccessMode.PAID,
    with_price: bool = True,
    request_schema: dict[str, object] | None = None,
) -> Service:
    service = Service(
        id=101,
        provider_account_id=1,
        slug="quote-service",
        name="Quote Service",
        summary="Summary",
        description=None,
        lifecycle=ServiceLifecycle.ACTIVE,
        current_revision_id=revision_id,
        current_change_token=change_token,
    )
    endpoint = ServiceEndpoint(
        id=303,
        service_id=service.id,
        key=endpoint_key,
        name="Translate",
        summary=None,
        description=None,
        access_mode=access_mode,
        request_schema=request_schema or {"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=30,
        is_enabled=endpoint_enabled,
    )
    if with_price:
        endpoint.price = EndpointPrice(
            endpoint_id=endpoint.id,
            amount_minor=500,
            currency="USD",
        )
    service.endpoints = [endpoint]
    return service


def _quote(
    *,
    expires_at: datetime | None = None,
    request_hash: str = "9b2d43affbf49a367028df2e1414f84c0e099ac98c3d54a8a80157fd7771af25",
    revision_id: int | None = 11,
    change_token: str | None = "b" * 64,
    endpoint_key: str = "translate",
) -> FakeQuote:
    now = datetime(2026, 3, 14, 12, 0, tzinfo=UTC)
    return FakeQuote(
        id=1,
        service_id=101,
        endpoint_id=303,
        endpoint_key=endpoint_key,
        request_hash=request_hash,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
        service_revision_id=revision_id,
        service_change_token=change_token,
        expires_at=expires_at or now + timedelta(minutes=5),
        created_at=now,
    )


@pytest.mark.asyncio
async def test_validate_quote_rejects_missing_quote() -> None:
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(None)
    service._service_repo = FakeServiceRepository(_service())
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteNotFoundError, match="quote not found"):
        await service.validate_quote(
            quote_id=1,
            payload={"message": "hello"},
        )


@pytest.mark.asyncio
async def test_validate_quote_rejects_expired_quote() -> None:
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(
        _quote(expires_at=datetime(2026, 3, 14, 11, 59, tzinfo=UTC)),
    )
    service._service_repo = FakeServiceRepository(_service())
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteExpiredError, match="quote has expired"):
        await service.validate_quote(
            quote_id=1,
            payload={"message": "hello"},
            now=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_validate_quote_rejects_payload_hash_mismatch() -> None:
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(_quote())
    service._service_repo = FakeServiceRepository(_service())
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteMismatchError, match="request hash does not match quote"):
        await service.validate_quote(
            quote_id=1,
            payload={"message": "different"},
            now=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_validate_quote_rejects_stale_revision_or_change_token() -> None:
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(_quote())
    service._service_repo = FakeServiceRepository(
        _service(revision_id=12, change_token="c" * 64),
    )
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteStaleError, match="quote no longer matches current service state"):
        await service.validate_quote(
            quote_id=1,
            payload={"message": "hello"},
            now=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_validate_quote_rejects_missing_endpoint() -> None:
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(_quote(endpoint_key="translate"))
    service._service_repo = FakeServiceRepository(_service(endpoint_key="summarize"))
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteStaleError, match="quote no longer matches current service state"):
        await service.validate_quote(
            quote_id=1,
            payload={"message": "hello"},
            now=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_validate_quote_rejects_moderated_service() -> None:
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(_quote())
    service._service_repo = FakeServiceRepository(_service())
    service._moderation_service = FakeModerationService(unavailable=True)

    with pytest.raises(QuoteStaleError, match="quote no longer matches current service state"):
        await service.validate_quote(
            quote_id=1,
            payload={"message": "hello"},
            now=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_create_quote_rejects_missing_contract_binding() -> None:
    quote = _quote()
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(quote)
    service._service_repo = FakeServiceRepository(_service(revision_id=None, change_token=None))
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteUnavailableError, match="service contract is not quoteable"):
        await service.create_quote(
            service_id_or_slug="quote-service",
            endpoint_key="translate",
            payload={"message": "hello"},
        )


@pytest.mark.asyncio
async def test_create_quote_rejects_free_endpoint() -> None:
    quote = _quote()
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(quote)
    service._service_repo = FakeServiceRepository(
        _service(access_mode=AccessMode.FREE, with_price=False),
    )
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteUnavailableError, match="endpoint is not quoteable"):
        await service.create_quote(
            service_id_or_slug="quote-service",
            endpoint_key="translate",
            payload={"message": "hello"},
        )


@pytest.mark.asyncio
async def test_create_quote_rejects_paid_endpoint_without_fixed_pricing() -> None:
    quote = _quote()
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(quote)
    service._service_repo = FakeServiceRepository(
        _service(with_price=False),
    )
    service._moderation_service = FakeModerationService()

    with pytest.raises(QuoteUnavailableError, match="endpoint is not quoteable"):
        await service.create_quote(
            service_id_or_slug="quote-service",
            endpoint_key="translate",
            payload={"message": "hello"},
        )


@pytest.mark.asyncio
async def test_create_quote_rejects_payload_that_does_not_match_endpoint_schema() -> None:
    quote = _quote()
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(quote)
    service._service_repo = FakeServiceRepository(
        _service(
            request_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            }
        ),
    )
    service._moderation_service = FakeModerationService()

    with pytest.raises(
        PayloadSchemaMismatchError, match="request payload does not match endpoint schema"
    ):
        await service.create_quote(
            service_id_or_slug="quote-service",
            endpoint_key="translate",
            payload={"message": 123},
        )


@pytest.mark.asyncio
async def test_validate_quote_accepts_matching_quote() -> None:
    quote = _quote()
    service = QuoteService(cast("AsyncSession", FakeSession()))
    service._quote_repo = FakeQuoteRepository(quote)
    service._service_repo = FakeServiceRepository(_service())
    service._moderation_service = FakeModerationService()

    validated = await service.validate_quote(
        quote_id=1,
        payload={"message": "hello"},
        now=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
    )

    assert validated is quote
