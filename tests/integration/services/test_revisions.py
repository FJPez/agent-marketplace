import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from app.core.enums import AccessMode
from app.db.models import Account, Service, ServiceEndpoint, ServiceRevision
from app.services import revisions

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


async def _create_provider_account(
    session: AsyncSession,
    *,
    display_name: str,
) -> int:
    account = Account(display_name=display_name)
    session.add(account)
    await session.flush()
    return account.id


async def test_create_revision_persists_snapshot_and_updates_current_token(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory.begin() as session:
        provider_account_id = await _create_provider_account(
            session,
            display_name="Provider",
        )
        service = Service(
            provider_account_id=provider_account_id,
            slug="translation-service",
            name="Translation Service",
            summary="Summary",
            description="Description",
        )
        session.add(service)
        await session.flush()
        endpoint = ServiceEndpoint(
            service=service,
            key="translate",
            name="Translate",
            summary="Endpoint summary",
            description="Endpoint description",
            access_mode=AccessMode.FREE,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        set_committed_value(endpoint, "price", None)
        set_committed_value(service, "endpoints", [endpoint])

        first_revision = await revisions.create_revision(session=session, service=service)
        second_revision = await revisions.create_revision(session=session, service=service)

        await session.flush()

        assert first_revision.revision_number == 1
        assert second_revision.revision_number == 2
        assert first_revision.change_token != second_revision.change_token
        assert service.current_revision_id == second_revision.id
        assert service.current_change_token == second_revision.change_token

    async with db_session_factory() as session:
        persisted_revisions = list(
            (
                await session.scalars(
                    select(ServiceRevision)
                    .where(ServiceRevision.service_id == service.id)
                    .order_by(
                        ServiceRevision.revision_number.desc(),
                        ServiceRevision.id.desc(),
                    ),
                )
            ).all()
        )
        reloaded_service = await session.get(Service, service.id)

    assert [revision.revision_number for revision in persisted_revisions] == [2, 1]
    assert persisted_revisions[0].snapshot == {
        "service": {"id": service.id, "slug": "translation-service"},
        "endpoints": [
            {
                "id": endpoint.id,
                "key": "translate",
                "access_mode": "free",
                "request_schema": {"type": "object"},
                "response_schema": {"type": "object"},
                "pricing": {
                    "pricing_type": "free",
                    "amount_minor": None,
                    "currency": None,
                },
                "timeout_seconds": 30,
                "is_enabled": True,
            },
        ],
    }
    assert reloaded_service is not None
    assert reloaded_service.current_revision_id == persisted_revisions[0].id
    assert reloaded_service.current_change_token == persisted_revisions[0].change_token
