import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode, PricingModelType, ServiceHealthStatus, ServiceLifecycle
from app.db.models import (
    Account,
    ModerationAction,
    PricingModel,
    ProviderProfile,
    ProviderUpstream,
    Service,
    ServiceEndpoint,
    ServiceHealthCheck,
    ServiceRevision,
)


def _auth_headers(account_id: int) -> dict[str, str]:
    return {"X-Account-Id": str(account_id)}


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    display_name: str = "Provider Account",
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(
            ProviderProfile(account_id=account.id, display_name=display_name),
        )
        return account.id


async def _create_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    is_admin: bool = False,
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(is_admin=is_admin)
        session.add(account)
        await session.flush()
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    name: str = "Seeded Service",
    summary: str = "Seeded summary",
    description: str | None = "Seeded description",
    lifecycle: ServiceLifecycle = ServiceLifecycle.DRAFT,
) -> int:
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug=slug,
            name=name,
            summary=summary,
            description=description,
            lifecycle=lifecycle,
        )
        session.add(service)
        await session.flush()
        return service.id


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    key: str = "translate",
    name: str = "Translate",
    access_mode: AccessMode = AccessMode.FREE,
) -> int:
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key=key,
            name=name,
            summary="Endpoint summary",
            description="Endpoint description",
            access_mode=access_mode,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        return endpoint.id


async def _seed_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            ProviderUpstream(
                endpoint_id=endpoint_id,
                base_url="https://provider.internal",
                path="/invoke",
                http_method="POST",
                config={"auth": {"type": "bearer", "token_env": "TOKEN"}},
            ),
        )


async def _seed_pricing(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    pricing_type: PricingModelType = PricingModelType.FIXED_PER_CALL,
    amount_minor: int | None = 1500,
    currency: str | None = "USD",
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            PricingModel(
                endpoint_id=endpoint_id,
                pricing_type=pricing_type,
                amount_minor=amount_minor,
                currency=currency,
            ),
        )


async def _seed_health_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    status: ServiceHealthStatus,
    summary: str = "unhealthy",
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            ServiceHealthCheck(
                service_id=service_id,
                check_name="publish-readiness",
                status=status,
                summary=summary,
                details={"source": "test"},
            ),
        )


async def _seed_moderation_action(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    action: str,
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            ModerationAction(
                service_id=service_id,
                actor_account_id=None,
                action=action,
                reason="policy",
            ),
        )


@pytest.mark.asyncio
async def test_create_paid_endpoint_returns_fixed_per_call_pricing(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="pricing-service",
    )

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/endpoints",
        headers=_auth_headers(account_id),
        json={
            "key": "translate",
            "name": "Translate",
            "summary": "Translate text",
            "description": "Endpoint description",
            "access_mode": "paid",
            "request_schema": {"type": "object"},
            "response_schema": {"type": "object"},
            "timeout_seconds": 45,
            "is_enabled": True,
            "pricing": {
                "pricing_type": "fixed_per_call",
                "amount_minor": 1500,
                "currency": "USD",
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["pricing"] == {
        "pricing_type": "fixed_per_call",
        "amount_minor": 1500,
        "currency": "USD",
    }


@pytest.mark.asyncio
async def test_provider_service_routes_require_x_account_id_header(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/v1/provider/services")

    assert response.status_code == 401
    assert response.json() == {"detail": "X-Account-Id header is required"}


@pytest.mark.asyncio
async def test_create_provider_service_returns_created_draft_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)

    response = await async_client.post(
        "/v1/provider/services",
        headers=_auth_headers(account_id),
        json={
            "slug": "translation-assistant",
            "name": "Translation Assistant",
            "summary": "Translates short text snippets.",
            "description": "Draft translation service.",
        },
    )

    assert response.status_code == 201
    assert response.json()["provider_account_id"] == account_id
    assert response.json()["slug"] == "translation-assistant"
    assert response.json()["name"] == "Translation Assistant"
    assert response.json()["summary"] == "Translates short text snippets."
    assert response.json()["description"] == "Draft translation service."
    assert response.json()["lifecycle"] == "draft"
    assert response.json()["tags"] == []
    assert response.json()["endpoints"] == []
    assert response.json()["created_at"]
    assert response.json()["updated_at"]


@pytest.mark.asyncio
async def test_create_provider_service_returns_not_found_without_provider_profile(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)

    response = await async_client.post(
        "/v1/provider/services",
        headers=_auth_headers(account_id),
        json={
            "slug": "translation-assistant",
            "name": "Translation Assistant",
            "summary": "Translates short text snippets.",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "provider profile not found"}


@pytest.mark.asyncio
async def test_list_provider_services_returns_owned_services_newest_first(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    other_account_id = await _create_provider_account(
        db_session_factory,
        display_name="Other Provider",
    )
    await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="older-service",
        name="Older Service",
    )
    await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="newer-service",
        name="Newer Service",
    )
    await _seed_service(
        db_session_factory,
        provider_account_id=other_account_id,
        slug="other-service",
        name="Other Service",
    )

    response = await async_client.get(
        "/v1/provider/services",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 200
    assert [item["slug"] for item in response.json()] == [
        "newer-service",
        "older-service",
    ]


@pytest.mark.asyncio
async def test_get_provider_service_hides_upstream_fields_in_endpoint_payload(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )
    await _seed_upstream(
        db_session_factory,
        endpoint_id=endpoint_id,
    )

    response = await async_client.get(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 200
    endpoint = response.json()["endpoints"][0]
    assert endpoint["has_upstream"] is True
    assert "base_url" not in endpoint
    assert "path" not in endpoint
    assert "http_method" not in endpoint
    assert "config" not in endpoint


@pytest.mark.asyncio
async def test_patch_provider_service_updates_draft_metadata(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )

    response = await async_client.patch(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
        json={
            "name": "Updated Service",
            "summary": "Updated summary",
            "description": "Updated description",
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated Service"
    assert response.json()["summary"] == "Updated summary"
    assert response.json()["description"] == "Updated description"


@pytest.mark.asyncio
async def test_patch_provider_service_clears_description_on_explicit_null(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
        description="Seeded description",
    )

    patch_response = await async_client.patch(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
        json={"description": None},
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["description"] is None

    detail_response = await async_client.get(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
    )

    assert detail_response.status_code == 200
    assert detail_response.json()["description"] is None


@pytest.mark.asyncio
async def test_patch_provider_service_rejects_explicit_null_for_name(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )

    response = await async_client.patch(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
        json={"name": None},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "name cannot be null"}


@pytest.mark.asyncio
async def test_replace_service_tags_normalizes_and_sorts_tags(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/tags",
        headers=_auth_headers(account_id),
        json={"tags": [" Translation ", "nlp", "translation", "NLP"]},
    )

    assert response.status_code == 200
    assert response.json()["tags"] == ["nlp", "translation"]


@pytest.mark.asyncio
async def test_replace_service_tags_rejects_non_slug_token_values(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/tags",
        headers=_auth_headers(account_id),
        json={"tags": ["ml ops", "foo!"]},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "tags must be lowercase slug tokens"}


@pytest.mark.asyncio
async def test_create_and_patch_endpoint_manage_owned_draft_endpoint(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )

    create_response = await async_client.post(
        f"/v1/provider/services/{service_id}/endpoints",
        headers=_auth_headers(account_id),
        json={
            "key": "translate",
            "name": "Translate",
            "summary": "Translate text",
            "description": "Endpoint description",
            "access_mode": "free",
            "request_schema": {"type": "object"},
            "response_schema": {"type": "object"},
            "timeout_seconds": 45,
            "is_enabled": True,
        },
    )

    assert create_response.status_code == 201
    endpoint_id = create_response.json()["id"]
    assert create_response.json()["has_upstream"] is False

    patch_response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={
            "name": "Translate Updated",
            "summary": "Translate text quickly",
            "timeout_seconds": 90,
            "is_enabled": False,
            "access_mode": "paid",
        },
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["name"] == "Translate Updated"
    assert patch_response.json()["summary"] == "Translate text quickly"
    assert patch_response.json()["timeout_seconds"] == 90
    assert patch_response.json()["is_enabled"] is False
    assert patch_response.json()["access_mode"] == "paid"


@pytest.mark.asyncio
async def test_patch_provider_endpoint_clears_nullable_fields_on_explicit_null(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    patch_response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={"summary": None, "description": None},
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["summary"] is None
    assert patch_response.json()["description"] is None

    detail_response = await async_client.get(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
    )

    assert detail_response.status_code == 200
    endpoint = detail_response.json()["endpoints"][0]
    assert endpoint["summary"] is None
    assert endpoint["description"] is None


@pytest.mark.asyncio
async def test_patch_provider_endpoint_rejects_explicit_null_for_name(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={"name": None},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "name cannot be null"}


@pytest.mark.asyncio
async def test_put_endpoint_upstream_returns_no_content_and_keeps_it_hidden(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="translation-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    upstream_response = await async_client.put(
        f"/v1/provider/endpoints/{endpoint_id}/upstream",
        headers=_auth_headers(account_id),
        json={
            "base_url": "https://provider.internal",
            "path": "/translate",
            "http_method": "POST",
            "config": {"auth": {"type": "bearer"}},
        },
    )

    assert upstream_response.status_code == 204
    assert upstream_response.content == b""

    detail_response = await async_client.get(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
    )

    assert detail_response.status_code == 200
    endpoint = detail_response.json()["endpoints"][0]
    assert endpoint["has_upstream"] is True
    assert "base_url" not in endpoint
    assert "path" not in endpoint
    assert "http_method" not in endpoint
    assert "config" not in endpoint


@pytest.mark.asyncio
async def test_provider_service_routes_hide_cross_owner_service_access(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_account_id = await _create_provider_account(db_session_factory)
    other_account_id = await _create_provider_account(
        db_session_factory,
        display_name="Other Provider",
    )
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=owner_account_id,
        slug="translation-service",
    )

    response = await async_client.get(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(other_account_id),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "service not found"}


@pytest.mark.asyncio
async def test_suspended_service_mutations_return_conflict(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="suspended-service",
        lifecycle=ServiceLifecycle.SUSPENDED,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    service_response = await async_client.patch(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
        json={"summary": "Updated"},
    )
    tag_response = await async_client.post(
        f"/v1/provider/services/{service_id}/tags",
        headers=_auth_headers(account_id),
        json={"tags": ["updated"]},
    )
    endpoint_response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={"summary": "Updated"},
    )
    upstream_response = await async_client.put(
        f"/v1/provider/endpoints/{endpoint_id}/upstream",
        headers=_auth_headers(account_id),
        json={
            "base_url": "https://provider.internal",
            "path": "/translate",
            "http_method": "POST",
            "config": {"auth": {"type": "bearer"}},
        },
    )

    assert service_response.status_code == 409
    assert tag_response.status_code == 409
    assert endpoint_response.status_code == 409
    assert upstream_response.status_code == 409


@pytest.mark.asyncio
async def test_suspended_service_blocks_contract_affecting_endpoint_updates(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_account_id = await _create_account(db_session_factory, is_admin=True)
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="moderated-active-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await _seed_pricing(db_session_factory, endpoint_id=endpoint_id)

    suspend_response = await async_client.post(
        f"/v1/admin/services/{service_id}/suspend",
        headers=_auth_headers(admin_account_id),
        json={"reason": "policy"},
    )
    timeout_response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={"timeout_seconds": 60},
    )
    pricing_response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={
            "pricing": {
                "pricing_type": "fixed_per_call",
                "amount_minor": 2500,
                "currency": "GBP",
            },
        },
    )

    assert suspend_response.status_code == 201
    assert timeout_response.status_code == 409
    assert timeout_response.json() == {"detail": "service is suspended"}
    assert pricing_response.status_code == 409
    assert pricing_response.json() == {"detail": "service is suspended"}


@pytest.mark.asyncio
async def test_suspended_service_allows_descriptive_endpoint_updates(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_account_id = await _create_account(db_session_factory, is_admin=True)
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="moderated-summary-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    suspend_response = await async_client.post(
        f"/v1/admin/services/{service_id}/suspend",
        headers=_auth_headers(admin_account_id),
        json={"reason": "policy"},
    )
    summary_response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={"summary": "Updated while suspended"},
    )

    assert suspend_response.status_code == 201
    assert summary_response.status_code == 200
    assert summary_response.json()["summary"] == "Updated while suspended"


@pytest.mark.asyncio
async def test_patch_active_provider_service_updates_non_material_metadata_without_revision(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="active-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )

    response = await async_client.patch(
        f"/v1/provider/services/{service_id}",
        headers=_auth_headers(account_id),
        json={"summary": "Updated summary"},
    )

    assert response.status_code == 200
    assert response.json()["summary"] == "Updated summary"

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)

    assert service is not None
    assert service.current_revision_id is None
    assert service.current_change_token is None


@pytest.mark.asyncio
async def test_patch_active_provider_endpoint_material_change_creates_revision_and_token(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="active-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={"timeout_seconds": 60},
    )

    assert response.status_code == 200
    assert response.json()["timeout_seconds"] == 60

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(
                ServiceRevision.service_id == service_id,
            ),
        )

    assert service is not None
    assert service.current_revision_id is not None
    assert service.current_change_token is not None
    assert revision_count == 1


@pytest.mark.asyncio
async def test_patch_active_provider_endpoint_pricing_change_creates_revision_and_token(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="active-paid-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await _seed_pricing(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=1500,
        currency="USD",
    )

    response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={
            "pricing": {
                "pricing_type": "fixed_per_call",
                "amount_minor": 2500,
                "currency": "GBP",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["pricing"] == {
        "pricing_type": "fixed_per_call",
        "amount_minor": 2500,
        "currency": "GBP",
    }

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert service is not None
    assert service.current_revision_id is not None
    assert service.current_change_token is not None
    assert revision_count == 1


@pytest.mark.asyncio
async def test_patch_active_provider_endpoint_rejects_paid_transition_without_pricing(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="active-free-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.FREE,
    )

    response = await async_client.patch(
        f"/v1/provider/endpoints/{endpoint_id}",
        headers=_auth_headers(account_id),
        json={"access_mode": "paid"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "active paid endpoints must define fixed_per_call pricing",
    }


@pytest.mark.asyncio
async def test_publish_service_rejects_service_without_endpoints(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="unpublishable-service",
    )

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "service must define at least one endpoint before publish",
    }


@pytest.mark.asyncio
async def test_publish_service_rejects_paid_endpoint_without_pricing(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="priced-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "paid endpoint 'translate' must define fixed_per_call pricing before publish",
    }


@pytest.mark.asyncio
async def test_publish_service_returns_active_service_when_endpoints_are_ready(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="ready-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 200
    assert response.json()["lifecycle"] == "active"
    assert response.json()["endpoints"][0]["pricing"] == {
        "pricing_type": "free",
        "amount_minor": None,
        "currency": None,
    }

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)

    assert service is not None
    assert service.current_revision_id is not None
    assert service.current_change_token is not None


@pytest.mark.asyncio
async def test_publish_service_rejects_service_with_failed_latest_publish_readiness(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="health-blocked-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)
    await _seed_health_check(
        db_session_factory,
        service_id=service_id,
        status=ServiceHealthStatus.FAIL,
    )

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "service failed latest publish-readiness health check",
    }


@pytest.mark.asyncio
async def test_publish_succeeds_with_passing_health_check(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="healthy-publish",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="healthy-ep",
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)
    await _seed_health_check(
        db_session_factory,
        service_id=service_id,
        status=ServiceHealthStatus.PASS,
    )

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 200
    assert response.json()["lifecycle"] == "active"


@pytest.mark.asyncio
async def test_publish_service_rejects_already_active_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="already-active-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)

    response = await async_client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "service is not publishable outside draft",
    }


@pytest.mark.asyncio
async def test_publish_service_rejects_suspended_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_account_id = await _create_account(db_session_factory, is_admin=True)
    account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="suspended-publish-service",
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)

    moderation_response = await async_client.post(
        f"/v1/admin/services/{service_id}/suspend",
        headers=_auth_headers(admin_account_id),
        json={"reason": "spam"},
    )
    response = await async_client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=_auth_headers(account_id),
    )

    assert moderation_response.status_code == 201
    assert response.status_code == 409
    assert response.json() == {
        "detail": "service is suspended",
    }
