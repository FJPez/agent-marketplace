from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from tests.helpers.auth import auth_headers_for_account_id

from app.core.config import get_settings
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.lifespan import get_app_state
from app.db.models import (
    Account,
    PricingModel,
    ProviderUpstream,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _auth_headers(account_id: int, *, idempotency_key: str) -> dict[str, str]:
    return auth_headers_for_account_id(account_id, idempotency_key=idempotency_key)


def _get_test_app(client: AsyncClient) -> FastAPI:
    app = getattr(client._transport, "app", None)
    if not isinstance(app, FastAPI):
        msg = "ASGI transport app is not available"
        raise AssertionError(msg)
    return app


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Provider")
        session.add(account)
        await session.flush()
        return account.id


async def _create_consumer_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Consumer")
        session.add(account)
        await session.flush()
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
) -> int:
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug="guardrail-service",
            name="Guardrail Service",
            summary="summary",
            description=None,
            lifecycle=ServiceLifecycle.ACTIVE,
        )
        session.add(service)
        await session.flush()
        revision = ServiceRevision(
            service_id=service.id,
            revision_number=1,
            change_token="c" * 64,
            snapshot={"slug": service.slug},
        )
        session.add(revision)
        await session.flush()
        service.current_revision_id = revision.id
        service.current_change_token = revision.change_token
        return service.id


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    access_mode: AccessMode = AccessMode.FREE,
) -> int:
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key="translate",
            name="Translate",
            summary="Translate text",
            description=None,
            access_mode=access_mode,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        session.add(
            ProviderUpstream(
                endpoint_id=endpoint.id,
                base_url="https://provider.internal",
                path="/invoke",
                http_method="POST",
                config={
                    "auth": {
                        "type": "hmac_sha256",
                        "key_id": "gateway-key",
                        "secret": "super-secret",
                    },
                },
            )
        )
        if access_mode is AccessMode.PAID:
            session.add(
                PricingModel(
                    endpoint_id=endpoint.id,
                    pricing_type=PricingModelType.FIXED_PER_CALL,
                    amount_minor=500,
                    currency="USD",
                )
            )
        return endpoint.id


@dataclass
class _FakeHttpClient:
    responses: list[Response]
    calls: list[dict[str, Any]]

    def __init__(self, responses: list[Response] | None = None) -> None:
        self.responses = [] if responses is None else responses
        self.calls = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        **kwargs: int,
    ) -> Response:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "kwargs": kwargs,
            }
        )
        if not self.responses:
            msg = "no fake response configured"
            raise AssertionError(msg)
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


class _SlowHttpClient(_FakeHttpClient):
    def __init__(self) -> None:
        super().__init__([Response(200, json={"result": "ok"})])
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        **kwargs: int,
    ) -> Response:
        self.started.set()
        await self.release.wait()
        return await super().request(method, url, json=json, headers=headers, **kwargs)


@pytest.fixture
async def guarded_client(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    _ = migrated_database
    monkeypatch.setenv("APP_API_RATE_LIMIT", "10/minute")
    monkeypatch.setenv("APP_INVOKE_RATE_LIMIT", "1/minute")
    monkeypatch.setenv("APP_QUOTE_RATE_LIMIT", "10/minute")
    monkeypatch.setenv("APP_INVOKE_PAYLOAD_MAX_BYTES", "4096")
    get_settings.cache_clear()

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_invoke_rejects_oversized_payload(
    guarded_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
    )
    await _seed_endpoint(db_session_factory, service_id=service_id)

    oversized_payload = {"text": "x" * 5000}

    response = await guarded_client.post(
        f"/v1/invoke/{service_id}",
        headers=_auth_headers(consumer_account_id, idempotency_key="payload-limit"),
        json={"endpoint_key": "translate", "payload": oversized_payload, "quote_id": None},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request payload too large"}


@pytest.mark.asyncio
async def test_invoke_rate_limits_repeated_requests(
    guarded_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
    )
    await _seed_endpoint(db_session_factory, service_id=service_id)

    app = _get_test_app(guarded_client)
    state = get_app_state(app)
    fake_http_client = _FakeHttpClient(
        [
            Response(200, json={"result": "first"}),
            Response(200, json={"result": "second"}),
        ]
    )
    state.http_client = fake_http_client

    first = await guarded_client.post(
        f"/v1/invoke/{service_id}",
        headers=_auth_headers(consumer_account_id, idempotency_key="rate-1"),
        json={"endpoint_key": "translate", "payload": {"text": "one"}, "quote_id": None},
    )
    second = await guarded_client.post(
        f"/v1/invoke/{service_id}",
        headers=_auth_headers(consumer_account_id, idempotency_key="rate-2"),
        json={"endpoint_key": "translate", "payload": {"text": "two"}, "quote_id": None},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "rate limit exceeded"}
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_invoke_rejects_duplicate_request_while_first_is_in_flight(
    guarded_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
    )
    await _seed_endpoint(db_session_factory, service_id=service_id)

    app = _get_test_app(guarded_client)
    state = get_app_state(app)
    slow_http_client = _SlowHttpClient()
    state.http_client = slow_http_client

    payload = {"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": None}
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as concurrent_client:
        first = asyncio.create_task(
            guarded_client.post(
                f"/v1/invoke/{service_id}",
                headers=_auth_headers(consumer_account_id, idempotency_key="duplicate-key"),
                json=payload,
            )
        )
        await slow_http_client.started.wait()

        second = await concurrent_client.post(
            f"/v1/invoke/{service_id}",
            headers=_auth_headers(consumer_account_id, idempotency_key="duplicate-key"),
            json=payload,
        )
        slow_http_client.release.set()
        first_response = await first

    assert second.status_code == 409
    assert second.json() == {"detail": "request already in progress"}
    assert first_response.status_code == 200


@pytest.mark.asyncio
async def test_invoke_rejects_reused_idempotency_key_for_different_in_flight_request(
    guarded_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
    )
    await _seed_endpoint(db_session_factory, service_id=service_id)

    app = _get_test_app(guarded_client)
    state = get_app_state(app)
    slow_http_client = _SlowHttpClient()
    state.http_client = slow_http_client

    first_payload = {
        "endpoint_key": "translate",
        "payload": {"text": "hello"},
        "quote_id": None,
    }
    second_payload = {
        "endpoint_key": "translate",
        "payload": {"text": "bonjour"},
        "quote_id": None,
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as concurrent_client:
        first = asyncio.create_task(
            guarded_client.post(
                f"/v1/invoke/{service_id}",
                headers=_auth_headers(consumer_account_id, idempotency_key="shared-key"),
                json=first_payload,
            )
        )
        await slow_http_client.started.wait()

        second = await concurrent_client.post(
            f"/v1/invoke/{service_id}",
            headers=_auth_headers(consumer_account_id, idempotency_key="shared-key"),
            json=second_payload,
        )
        slow_http_client.release.set()
        first_response = await first

    assert second.status_code == 409
    assert second.json() == {"detail": "idempotency key already used for a different request"}
    assert first_response.status_code == 200
