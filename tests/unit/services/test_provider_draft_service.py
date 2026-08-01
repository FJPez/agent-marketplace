from typing import TYPE_CHECKING, cast

import pytest

from app.core.actor import ActorContext
from app.core.enums import AccessMode, ServiceLifecycle
from app.db.models import ProviderUpstream, Service, ServiceEndpoint
from app.schemas.service import EndpointUpstreamRequest
from app.services.provider_endpoint_service import ProviderEndpointService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        pass

    def add(self, _instance: object) -> None:
        pass


class FakeServiceRepo:
    def __init__(
        self,
        service: Service | None = None,
        endpoint: ServiceEndpoint | None = None,
    ) -> None:
        self.service = service
        self.endpoint = endpoint
        if self.service is not None and self.endpoint is not None:
            self.service.endpoints = [self.endpoint]
            self.endpoint.service = self.service

    async def get_owned(
        self,
        *,
        service_id: int,
        provider_account_id: int,
    ) -> Service | None:
        _ = service_id
        _ = provider_account_id
        return self.service

    async def get_owned_for_update(
        self,
        *,
        service_id: int,
        provider_account_id: int,
    ) -> Service | None:
        return await self.get_owned(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )


class FakeEndpointRepo:
    def __init__(self, endpoint: ServiceEndpoint | None = None) -> None:
        self.endpoint = endpoint

    async def get_owned(
        self,
        *,
        endpoint_id: int,
        provider_account_id: int,
    ) -> ServiceEndpoint | None:
        _ = endpoint_id
        _ = provider_account_id
        return self.endpoint


class FakeUpstreamRepo:
    def __init__(self) -> None:
        self.calls = 0

    async def upsert(
        self,
        endpoint: ServiceEndpoint,
        *,
        base_url: str,
        path: str,
        http_method: str,
        config: dict[str, object],
    ) -> object:
        self.calls += 1
        endpoint.upstream = ProviderUpstream(
            endpoint_id=endpoint.id,
            base_url=base_url,
            path=path,
            http_method=http_method,
            config=config,
        )
        return endpoint.upstream


def _draft_service() -> Service:
    return Service(
        id=101,
        provider_account_id=42,
        slug="translation-service",
        name="Translation Service",
        summary="Summary",
        description="Description",
        lifecycle=ServiceLifecycle.DRAFT,
    )


def _draft_endpoint() -> ServiceEndpoint:
    endpoint = ServiceEndpoint(
        id=303,
        service_id=101,
        key="translate",
        name="Translate",
        summary="Summary",
        description="Description",
        access_mode=AccessMode.FREE,
        request_schema={"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=30,
        is_enabled=True,
    )
    endpoint.service = _draft_service()
    return endpoint


@pytest.mark.asyncio
async def test_upsert_upstream_marks_endpoint_as_having_upstream() -> None:
    session = FakeSession()
    endpoint = _draft_endpoint()
    upstream_repo = FakeUpstreamRepo()
    endpoint_service = ProviderEndpointService(cast("AsyncSession", session))
    endpoint_service._service_repo = FakeServiceRepo(endpoint.service, endpoint)
    endpoint_service._endpoint_repo = FakeEndpointRepo(endpoint)
    endpoint_service._upstream_repo = upstream_repo

    await endpoint_service.upsert_upstream(
        ActorContext(account_id=42),
        endpoint_id=303,
        request=EndpointUpstreamRequest.model_validate(
            {
                "base_url": "http://127.0.0.1:9000",
                "path": "/translate",
                "http_method": "POST",
                "config": {"auth": {"type": "bearer"}},
            },
        ),
    )

    assert upstream_repo.calls == 1
    assert endpoint.upstream is not None
    assert session.commits == 1
