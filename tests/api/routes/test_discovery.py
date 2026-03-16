import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import auth_headers_for_account_id

from app.core.enums import AccessMode, ServiceLifecycle
from app.db.models import (
    Account,
    ModerationAction,
    Service,
    ServiceEndpoint,
    ServiceTag,
)


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Provider")
        session.add(account)
        await session.flush()
        return account.id


async def _create_admin_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(is_admin=True)
        session.add(account)
        await session.flush()
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    lifecycle: ServiceLifecycle = ServiceLifecycle.ACTIVE,
    tags: list[str] | None = None,
) -> int:
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug=slug,
            name=f"{slug} name",
            summary=f"{slug} summary",
            description=f"{slug} description",
            lifecycle=lifecycle,
        )
        session.add(service)
        await session.flush()
        session.add_all(
            [ServiceTag(service_id=service.id, tag=tag) for tag in (tags or [])],
        )
        return service.id


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    key: str,
    access_mode: AccessMode = AccessMode.FREE,
    is_enabled: bool = True,
) -> int:
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key=key,
            name=f"{key} name",
            summary=f"{key} summary",
            description=f"{key} description",
            access_mode=access_mode,
            request_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            response_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            timeout_seconds=30,
            is_enabled=is_enabled,
        )
        session.add(endpoint)
        await session.flush()
        return endpoint.id


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
async def test_list_services_returns_only_active_public_services(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    active_service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="translation-service",
        tags=["nlp", "translation"],
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=active_service_id,
        key="translate",
    )
    draft_service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="draft-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=draft_service_id,
        key="draft-endpoint",
    )

    response = await async_client.get("/v1/services")

    assert response.status_code == 200
    assert [item["slug"] for item in response.json()] == ["translation-service"]
    assert response.json()[0]["tags"] == ["nlp", "translation"]
    assert "provider_account_id" not in response.json()[0]
    assert "lifecycle" not in response.json()[0]


@pytest.mark.asyncio
async def test_get_service_detail_returns_only_enabled_public_endpoints(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="visibility-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
        is_enabled=True,
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="disabled",
        is_enabled=False,
    )

    response = await async_client.get(f"/v1/services/{service_id}")

    assert response.status_code == 200
    assert [endpoint["key"] for endpoint in response.json()["endpoints"]] == ["translate"]
    assert "provider_account_id" not in response.json()
    assert "lifecycle" not in response.json()
    assert "has_upstream" not in response.json()["endpoints"][0]


@pytest.mark.asyncio
async def test_get_service_schema_returns_public_endpoint_schemas(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="schema-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    response = await async_client.get("/v1/services/schema-service/schema")

    assert response.status_code == 200
    assert response.json()["id"] == service_id
    assert response.json()["endpoints"][0]["key"] == "translate"
    assert response.json()["endpoints"][0]["request_schema"]["type"] == "object"
    assert response.json()["endpoints"][0]["response_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_get_service_pricing_returns_free_pricing_without_internal_fields(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="pricing-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.FREE,
    )

    response = await async_client.get(f"/v1/services/{service_id}/pricing")

    assert response.status_code == 200
    assert response.json()["endpoints"][0] == {
        "key": "translate",
        "access_mode": "free",
        "pricing_type": "free",
        "amount_minor": None,
        "currency": None,
    }


@pytest.mark.asyncio
async def test_get_service_detail_returns_404_for_nonexistent_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await async_client.get("/v1/services/does-not-exist")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_discovery_hides_delisted_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    admin_account_id = await _create_admin_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="delisted-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    delist_response = await async_client.post(
        f"/v1/admin/services/{service_id}/delist",
        headers=auth_headers_for_account_id(admin_account_id),
        json={"reason": "policy violation"},
    )
    list_response = await async_client.get("/v1/services")
    detail_response = await async_client.get(f"/v1/services/{service_id}")

    assert delist_response.status_code == 201
    assert list_response.status_code == 200
    assert list_response.json() == []
    assert detail_response.status_code == 404


@pytest.mark.asyncio
async def test_get_service_detail_treats_numeric_path_as_service_id(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    numeric_slug_service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="2",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=numeric_slug_service_id,
        key="numeric-slug-endpoint",
    )
    numeric_id_service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="actual-target-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=numeric_id_service_id,
        key="id-target-endpoint",
    )

    response = await async_client.get("/v1/services/2")

    assert response.status_code == 200
    assert response.json()["id"] == numeric_id_service_id
    assert response.json()["slug"] == "actual-target-service"


@pytest.mark.asyncio
async def test_get_service_detail_does_not_fallback_to_numeric_slug_lookup(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="99999",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="numeric-only-slug-endpoint",
    )

    response = await async_client.get("/v1/services/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_services_hides_delisted_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="hidden-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )
    await _seed_moderation_action(
        db_session_factory,
        service_id=service_id,
        action="delist",
    )

    response = await async_client.get("/v1/services")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_service_detail_returns_not_found_for_suspended_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="suspended-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )
    await _seed_moderation_action(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    response = await async_client.get(f"/v1/services/{service_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_services_hides_active_service_with_no_enabled_endpoints(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="all-disabled-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="disabled-endpoint",
        is_enabled=False,
    )

    list_response = await async_client.get("/v1/services")
    detail_response = await async_client.get(f"/v1/services/{service_id}")

    assert list_response.status_code == 200
    assert not any(item["slug"] == "all-disabled-service" for item in list_response.json())
    assert detail_response.status_code == 404


@pytest.mark.asyncio
async def test_suspended_service_reappears_after_restore(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="restore-visible",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    await _seed_moderation_action(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    hidden_response = await async_client.get("/v1/services")
    assert hidden_response.status_code == 200
    assert not any(item["slug"] == "restore-visible" for item in hidden_response.json())

    await _seed_moderation_action(
        db_session_factory,
        service_id=service_id,
        action="restore",
    )

    visible_response = await async_client.get("/v1/services")
    assert visible_response.status_code == 200
    assert any(item["slug"] == "restore-visible" for item in visible_response.json())
