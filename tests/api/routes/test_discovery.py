import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    AdminAccountFactory,
    EndpointFactory,
    ModerationActionFactory,
    ProviderAccountFactory,
    ServiceFactory,
    create_listing_price_record,
)
from tests.fixtures.settings import TEST_TREASURY_ADDRESS
from tests.helpers.auth import auth_headers_for_account_id

from app.core.enums import AccessMode, ServiceLifecycle


@pytest.mark.asyncio
async def test_list_services_returns_only_active_public_services(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    active_service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="translation-service",
        tags=["nlp", "translation"],
    )
    await endpoint_factory(
        service_id=active_service_id,
        key="translate",
    )
    draft_service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="draft-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    await endpoint_factory(
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
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="visibility-service",
    )
    await endpoint_factory(
        service_id=service_id,
        key="translate",
        is_enabled=True,
    )
    await endpoint_factory(
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
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="schema-service",
    )
    await endpoint_factory(
        service_id=service_id,
        key="translate",
        request_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        response_schema={"type": "object", "properties": {"result": {"type": "string"}}},
    )

    response = await async_client.get("/v1/services/schema-service/schema")

    assert response.status_code == 200
    assert response.json()["id"] == service_id
    assert response.json()["endpoints"][0]["key"] == "translate"
    assert response.json()["endpoints"][0]["request_schema"]["type"] == "object"
    assert response.json()["endpoints"][0]["response_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_get_service_pricing_returns_the_public_price_catalogue(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="pricing-service",
    )
    free_endpoint_id = await endpoint_factory(
        service_id=service_id,
        key="ping",
        access_mode=AccessMode.FREE,
    )
    paid_endpoint_id = await endpoint_factory(
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_listing_price_record(db_session_factory, endpoint_id=paid_endpoint_id)
    await create_listing_price_record(
        db_session_factory,
        endpoint_id=paid_endpoint_id,
        amount=25_000,
        version=2,
    )

    response = await async_client.get(f"/v1/services/{service_id}/pricing")

    assert response.status_code == 200
    assert {endpoint["key"]: endpoint for endpoint in response.json()["endpoints"]} == {
        "ping": {
            "endpoint_id": free_endpoint_id,
            "key": "ping",
            "access_mode": "free",
            "invoke_url": "/v1/invoke/pricing-service/ping",
            "price": None,
        },
        "translate": {
            "endpoint_id": paid_endpoint_id,
            "key": "translate",
            "access_mode": "paid",
            "invoke_url": "/v1/invoke/pricing-service/translate",
            "price": {
                "version": 2,
                "amount": 25_000,
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "network": "eip155:84532",
                "pay_to": TEST_TREASURY_ADDRESS,
            },
        },
    }


@pytest.mark.asyncio
async def test_get_service_detail_returns_404_for_nonexistent_service(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/v1/services/does-not-exist")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_discovery_hides_delisted_service(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    admin_account_factory: AdminAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    admin_account_id = await admin_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="delisted-service",
    )
    await endpoint_factory(
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
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    numeric_slug_service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="2",
    )
    await endpoint_factory(
        service_id=numeric_slug_service_id,
        key="numeric-slug-endpoint",
    )
    numeric_id_service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="actual-target-service",
    )
    await endpoint_factory(
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
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="99999",
    )
    await endpoint_factory(
        service_id=service_id,
        key="numeric-only-slug-endpoint",
    )

    response = await async_client.get("/v1/services/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_services_hides_delisted_service(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    moderation_action_factory: ModerationActionFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="hidden-service",
    )
    await endpoint_factory(
        service_id=service_id,
        key="translate",
    )
    await moderation_action_factory(
        service_id=service_id,
        action="delist",
    )

    response = await async_client.get("/v1/services")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_service_detail_returns_not_found_for_suspended_service(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    moderation_action_factory: ModerationActionFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="suspended-service",
    )
    await endpoint_factory(
        service_id=service_id,
        key="translate",
    )
    await moderation_action_factory(
        service_id=service_id,
        action="suspend",
    )

    response = await async_client.get(f"/v1/services/{service_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_services_hides_active_service_with_no_enabled_endpoints(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="all-disabled-service",
    )
    await endpoint_factory(
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
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    moderation_action_factory: ModerationActionFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="restore-visible",
    )
    await endpoint_factory(
        service_id=service_id,
        key="translate",
    )

    await moderation_action_factory(
        service_id=service_id,
        action="suspend",
    )

    hidden_response = await async_client.get("/v1/services")
    assert hidden_response.status_code == 200
    assert not any(item["slug"] == "restore-visible" for item in hidden_response.json())

    await moderation_action_factory(
        service_id=service_id,
        action="restore",
    )

    visible_response = await async_client.get("/v1/services")
    assert visible_response.status_code == 200
    assert any(item["slug"] == "restore-visible" for item in visible_response.json())


@pytest.mark.parametrize(
    "identifier",
    ["Not-A-Slug", "not a slug", "under_score", "9999999999999999999999"],
)
@pytest.mark.asyncio
async def test_get_service_detail_rejects_malformed_identifier(
    async_client: AsyncClient,
    identifier: str,
) -> None:
    response = await async_client.get(f"/v1/services/{identifier}")

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "/problems/invalid_input"
    errors = body["errors"]
    assert isinstance(errors, list)
    assert errors
    assert all("service_id_or_slug" in error["loc"] for error in errors)
