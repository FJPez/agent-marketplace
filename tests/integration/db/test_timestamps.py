from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)

from app.db.models import ProviderUpstream, Service, ServiceEndpoint


@pytest.mark.parametrize("model", [Service, ServiceEndpoint, ProviderUpstream])
async def test_an_update_that_leaves_updated_at_alone_stamps_it_in_utc(
    db_session_factory: async_sessionmaker[AsyncSession],
    model: type[Service | ServiceEndpoint | ProviderUpstream],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(db_session_factory, provider_account_id=account_id)
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)
    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        row = (await session.scalars(select(model))).one()
        row.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        await session.flush()

        assert row.updated_at.tzinfo is not None
