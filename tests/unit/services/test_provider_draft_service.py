from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.actor import ActorContext
from app.core.enums import AccessMode, ServiceLifecycle
from app.db.models import ProviderProfile, ProviderUpstream, Service, ServiceEndpoint
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointUpstreamRequest,
    ServiceCreateRequest,
    ServiceTagsUpdateRequest,
    ServiceUpdateRequest,
)
from app.services.provider_draft_service import ProviderDraftService
from app.services.provider_endpoint_service import ProviderEndpointService
from app.services.provider_service_errors import (
    ProviderServiceConflictError,
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)

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


class FailingCommitSession(FakeSession):
    async def commit(self) -> None:
        self.commits += 1
        raise IntegrityError("statement", {}, Exception("boom"))


class FakeProviderProfileRepo:
    def __init__(self, profile: ProviderProfile | None) -> None:
        self._profile = profile

    async def get_by_account_id(self, account_id: int) -> ProviderProfile | None:
        _ = account_id
        return self._profile


class FakeServiceRepo:
    def __init__(self, service: Service | None = None) -> None:
        self.service = service
        self.replaced_tags: list[str] | None = None

    def add(
        self,
        *,
        provider_account_id: int,
        slug: str,
        name: str,
        summary: str,
        description: str | None,
    ) -> Service:
        self.service = Service(
            id=101,
            provider_account_id=provider_account_id,
            slug=slug,
            name=name,
            summary=summary,
            description=description,
            lifecycle=ServiceLifecycle.DRAFT,
        )
        return self.service

    async def get_owned(
        self,
        *,
        service_id: int,
        provider_account_id: int,
    ) -> Service | None:
        _ = service_id
        _ = provider_account_id
        return self.service

    async def list_by_provider_account_id(
        self,
        *,
        provider_account_id: int,
    ) -> list[Service]:
        _ = provider_account_id
        if self.service is None:
            return []
        return [self.service]

    def update_service(
        self,
        service: Service,
        *,
        name: str | None = None,
        summary: str | None = None,
        description: str | None = None,
    ) -> Service:
        if name is not None:
            service.name = name
        if summary is not None:
            service.summary = summary
        if description is not None:
            service.description = description
        return service

    async def replace_tags(self, service: Service, *, tags: list[str]) -> Service:
        self.replaced_tags = tags
        service.tags = []
        return service


class FakeEndpointRepo:
    def __init__(self, endpoint: ServiceEndpoint | None = None) -> None:
        self.endpoint = endpoint

    def add(self, **kwargs: object) -> ServiceEndpoint:
        self.endpoint = ServiceEndpoint(
            id=303,
            service_id=cast("int", kwargs["service_id"]),
            key=cast("str", kwargs["key"]),
            name=cast("str", kwargs["name"]),
            summary=cast("str | None", kwargs["summary"]),
            description=cast("str | None", kwargs["description"]),
            access_mode=cast("AccessMode", kwargs["access_mode"]),
            request_schema=cast("dict[str, object]", kwargs["request_schema"]),
            response_schema=cast("dict[str, object]", kwargs["response_schema"]),
            timeout_seconds=cast("int", kwargs["timeout_seconds"]),
            is_enabled=cast("bool", kwargs["is_enabled"]),
        )
        self.endpoint.service = Service(
            id=cast("int", kwargs["service_id"]),
            provider_account_id=42,
            slug="service",
            name="Service",
            summary="Summary",
            description="Description",
            lifecycle=ServiceLifecycle.DRAFT,
        )
        return self.endpoint

    async def get_owned(
        self,
        *,
        endpoint_id: int,
        provider_account_id: int,
    ) -> ServiceEndpoint | None:
        _ = endpoint_id
        _ = provider_account_id
        return self.endpoint

    def update_endpoint(self, endpoint: ServiceEndpoint, **kwargs: object) -> ServiceEndpoint:
        for field, value in kwargs.items():
            if value is not None:
                setattr(endpoint, field, value)
        return endpoint


class FakeUpstreamRepo:
    def __init__(self) -> None:
        self.calls = 0

    async def upsert(self, endpoint: ServiceEndpoint, **kwargs: object) -> object:
        self.calls += 1
        endpoint.upstream = ProviderUpstream(
            endpoint_id=endpoint.id,
            base_url=cast("str", kwargs["base_url"]),
            path=cast("str", kwargs["path"]),
            http_method=cast("str", kwargs["http_method"]),
            config=cast("dict[str, object]", kwargs["config"]),
        )
        return endpoint.upstream


def _provider_profile() -> ProviderProfile:
    return ProviderProfile(account_id=42, display_name="Provider")


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


def _active_service() -> Service:
    return Service(
        id=101,
        provider_account_id=42,
        slug="translation-service",
        name="Translation Service",
        summary="Summary",
        description="Description",
        lifecycle=ServiceLifecycle.ACTIVE,
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
async def test_create_service_requires_provider_profile() -> None:
    service = ProviderDraftService(cast("AsyncSession", FakeSession()))
    service._provider_profile_repo = FakeProviderProfileRepo(None)
    service._service_repo = FakeServiceRepo()

    with pytest.raises(ProviderServiceNotFoundError, match="provider profile not found"):
        await service.create_service(
            ActorContext(account_id=42),
            ServiceCreateRequest(
                slug="translation-service",
                name="Translation Service",
                summary="Summary",
            ),
        )


@pytest.mark.asyncio
async def test_update_service_rejects_empty_patch_payload() -> None:
    service = ProviderDraftService(cast("AsyncSession", FakeSession()))
    service._provider_profile_repo = FakeProviderProfileRepo(_provider_profile())
    service._service_repo = FakeServiceRepo(_draft_service())

    with pytest.raises(
        ProviderServiceValidationError,
        match="at least one field must be provided",
    ):
        await service.update_service(
            ActorContext(account_id=42),
            service_id=101,
            request=ServiceUpdateRequest(),
        )


@pytest.mark.asyncio
async def test_update_service_rejects_non_draft_mutation() -> None:
    service = ProviderDraftService(cast("AsyncSession", FakeSession()))
    service._provider_profile_repo = FakeProviderProfileRepo(_provider_profile())
    service._service_repo = FakeServiceRepo(_active_service())

    with pytest.raises(
        ProviderServiceStateError,
        match="service is not mutable outside draft",
    ):
        await service.update_service(
            ActorContext(account_id=42),
            service_id=101,
            request=ServiceUpdateRequest(summary="Updated"),
        )


@pytest.mark.asyncio
async def test_create_service_translates_duplicate_slug_to_conflict() -> None:
    service = ProviderDraftService(cast("AsyncSession", FailingCommitSession()))
    service._provider_profile_repo = FakeProviderProfileRepo(_provider_profile())
    service._service_repo = FakeServiceRepo()

    with pytest.raises(ProviderServiceConflictError, match="service slug already exists"):
        await service.create_service(
            ActorContext(account_id=42),
            ServiceCreateRequest(
                slug="translation-service",
                name="Translation Service",
                summary="Summary",
            ),
        )


@pytest.mark.asyncio
async def test_replace_tags_normalizes_and_sorts_tags() -> None:
    session = FakeSession()
    repo = FakeServiceRepo(_draft_service())
    service = ProviderDraftService(cast("AsyncSession", session))
    service._provider_profile_repo = FakeProviderProfileRepo(_provider_profile())
    service._service_repo = repo

    await service.replace_tags(
        ActorContext(account_id=42),
        service_id=101,
        request=ServiceTagsUpdateRequest(tags=[" Translation ", "nlp", "translation", "NLP"]),
    )

    assert repo.replaced_tags == ["nlp", "translation"]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_create_endpoint_translates_duplicate_key_to_conflict() -> None:
    endpoint_service = ProviderEndpointService(
        cast("AsyncSession", FailingCommitSession()),
    )
    endpoint_service._provider_profile_repo = FakeProviderProfileRepo(_provider_profile())
    endpoint_service._service_repo = FakeServiceRepo(_draft_service())
    endpoint_service._endpoint_repo = FakeEndpointRepo()
    endpoint_service._upstream_repo = FakeUpstreamRepo()

    with pytest.raises(
        ProviderServiceConflictError,
        match="endpoint key already exists for this service",
    ):
        await endpoint_service.create_endpoint(
            ActorContext(account_id=42),
            service_id=101,
            request=EndpointCreateRequest(
                key="translate",
                name="Translate",
                summary="Summary",
                description="Description",
                access_mode=AccessMode.FREE,
                request_schema={"type": "object"},
                response_schema={"type": "object"},
                timeout_seconds=30,
                is_enabled=True,
            ),
        )


@pytest.mark.asyncio
async def test_upsert_upstream_marks_endpoint_as_having_upstream() -> None:
    session = FakeSession()
    endpoint = _draft_endpoint()
    upstream_repo = FakeUpstreamRepo()
    endpoint_service = ProviderEndpointService(cast("AsyncSession", session))
    endpoint_service._provider_profile_repo = FakeProviderProfileRepo(_provider_profile())
    endpoint_service._service_repo = FakeServiceRepo(_draft_service())
    endpoint_service._endpoint_repo = FakeEndpointRepo(endpoint)
    endpoint_service._upstream_repo = upstream_repo

    await endpoint_service.upsert_upstream(
        ActorContext(account_id=42),
        endpoint_id=303,
        request=EndpointUpstreamRequest.model_validate(
            {
                "base_url": "https://provider.internal",
                "path": "/translate",
                "http_method": "POST",
                "config": {"auth": {"type": "bearer"}},
            },
        ),
    )

    assert upstream_repo.calls == 1
    assert endpoint.upstream is not None
    assert session.commits == 1
